"""插件核心控制器：命令逻辑与后台更新。

自原 AstrBot 插件 ``main.py`` 移植而来：

- AstrBot ``Star`` 插件类 → 普通类 :class:`SuwayomiCore`
- ``AstrMessageEvent`` → :class:`~suwayomi.msg.MsgContext`
- ``yield event.plain_result(...)`` / ``event.chain_result(...)`` →
  ``await ctx.send(text_msg(...))`` / ``await ctx.send(chain_msg(...))``
  （消息模型见 ``suwayomi/msg.py``，发送异常与原插件一样在调用点捕获）
- AstrBot WebAPI 注册 → ``web_routes.py``（FastAPI 路由）
- AstrBot Agent 工具（LLM FunctionTool）在 NoneBot2 中无对应设施，未移植

命令处理方法由 ``handlers.py`` 调用，向会话发送消息统一走
``await ctx.send(...)``（命令回复）或 :func:`suwayomi.msg.send_to_session`
（订阅推送）。
"""
from __future__ import annotations

import asyncio
import re
import time
from pathlib import Path
from typing import Any

from nonebot import logger

from .renderers import get_renderer, reset_renderer_cache
from .store import JsonKvStore
from .suwayomi import PLUGIN_NAME
from .suwayomi.cards import (
    CardCache,
    build_batch_card,
    build_chapter_cards,
    build_search_card,
    build_subscribe_confirm_card,
    build_subscriptions_card,
    build_update_card,
    embed_covers,
    render_card_cached,
)
from .suwayomi.client import SuwayomiClient, SuwayomiError
from .suwayomi.config import config_bool, get_config_value
from .suwayomi.bangumi import (
    alias_boost,
    api_bases,
    build_probes,
    confident_aliases,
    resolve_aliases,
)
from .suwayomi.models import Manga
from .suwayomi.msg import (
    Image,
    Msg,
    MsgContext,
    Text,
    chain_msg,
    file_msg,
    image_file_msg,
    send_to_session,
    text_msg,
)
from .suwayomi.ranking import (
    STRONG_MATCH_THRESHOLD,
    normalize_for_rank,
    score_title,
)
from .suwayomi.service import (
    STATUS_EMOJI,
    _bounded_int,
    fmt_chapter_display,
    fmt_chapter_label,
    fmt_chapter_num,
    fmt_delivery_failure_message,
    get_or_fetch_chapters,
    match_source_hint,
    normalize_zh,
    refresh_truncated_titles,
    resolve_chapter,
    resolve_manga,
    sanitize_for_message,
    search_best_match,
    select_search_sources,
    split_search_query,
    ttl_cache_lookup,
    ttl_cache_store,
)
from .suwayomi.t2i import normalize_endpoint
from .suwayomi.updater import check_updates as _check_updates
from .suwayomi.updater import run_update_loop
from .utils.downloader import (
    download_cover,
    fetch_pages_local,
    get_file_delivery_max_pages,
)
from .utils.pack import (
    build_chapter_output_path,
    normalize_pack_format,
    pack_images,
    parse_download_args,
)
from .utils.pusher import (
    build_image_msg,
    cancel_pending_cleanups,
    push_chapter_file,
    push_chapter_images,
    schedule_cleanup,
    schedule_cleanup_file,
)
from .utils.subscription import SubscriptionManager

_CACHE_TTL = 600
_SEARCH_CACHE_MAX_ENTRIES = 64
CARD_CACHE_TTL = 600
CARD_FAIL_COOLDOWN = 300


class SuwayomiCore:
    def __init__(self, config: dict, kv: JsonKvStore, client: SuwayomiClient | None = None):
        self.config = config
        self.kv = kv
        self.client = client or SuwayomiClient(
            server_url=get_config_value(config, "server_url", "http://localhost:4567"),
            auth_mode=get_config_value(config, "auth_mode", "none"),
            username=get_config_value(config, "username", ""),
            password=get_config_value(config, "password", ""),
        )
        self.sub_mgr = SubscriptionManager(self)
        self._search_cache: dict[str, tuple[float, dict[str, Manga]]] = {}
        self._card_cache = CardCache(ttl=CARD_CACHE_TTL)
        self._card_cooldown_until = 0.0
        self._t2i_endpoint_warned = False
        self._update_lock = asyncio.Lock()
        self._bg_task: asyncio.Task | None = None
        self._check_updates_fn = None
        self._build_check_updates_fn()
        logger.info(
            f"[{PLUGIN_NAME}] 核心已就绪 | 服务器: {get_config_value(config, 'server_url', 'http://localhost:4567')} | "
            f"缓存: {get_config_value(config, 'chapter_cache_hours', 6)}h | "
            f"检查间隔: {get_config_value(config, 'check_interval', 60)}min"
        )

    # ── KV 存储（SubscriptionManager / 章节缓存使用） ─────────────

    async def get_kv_data(self, key: str, default: Any = None) -> Any:
        return await self.kv.get_kv_data(key, default)

    async def put_kv_data(self, key: str, value: Any) -> None:
        await self.kv.put_kv_data(key, value)

    # ── Lifecycle ──────────────────────────────────────────────────

    async def startup(self):
        """driver.on_startup：订阅数据迁移 + 启动后台更新循环。"""
        try:
            await self.sub_mgr.run_migration()
        except Exception as e:
            logger.error(f"[{PLUGIN_NAME}] 订阅数据迁移失败: {e}")
        self.start_bg_loop()

    def start_bg_loop(self):
        if self._bg_task is not None and not self._bg_task.done():
            return
        interval = float(get_config_value(self.config, "check_interval", 60)) * 60
        logger.info(
            f"[{PLUGIN_NAME}] 启动后台更新循环 | "
            f"间隔: {interval / 60:.0f}min ({interval}s)"
        )

        async def _check_wrapper(force=False):
            fn = self._check_updates_fn
            if fn is None:
                logger.warning(
                    f"[{PLUGIN_NAME}] 检查更新: _check_updates_fn 未就绪，跳过"
                )
                return
            return await fn(force=force)

        self._bg_task = asyncio.create_task(
            run_update_loop(interval, _check_wrapper)
        )

        def _on_task_done(task: asyncio.Task):
            if not task.cancelled():
                exc = task.exception()
                if exc:
                    logger.error(f"[{PLUGIN_NAME}] 后台更新循环异常退出: {exc}")

        self._bg_task.add_done_callback(_on_task_done)

    async def shutdown(self):
        if self._bg_task and not self._bg_task.done():
            self._bg_task.cancel()
            # 等待取消传播完成（睡眠中的循环立即退出），避免悬挂任务噪音
            await asyncio.gather(self._bg_task, return_exceptions=True)
        self._bg_task = None
        cancel_pending_cleanups()
        await self.client.close()
        logger.info(f"[{PLUGIN_NAME}] 插件已卸载")

    # ── Push callbacks builder ─────────────────────────────────────

    def _build_check_updates_fn(self):
        client = self.client
        config = self.config

        async def _push_images(umo, title, chapter):
            return await push_chapter_images(
                client, config,
                umo, title, chapter,
                lambda cid, mp=0: fetch_pages_local(
                    client, cid, max_pages=mp,
                    concurrency=get_config_value(config, "download_concurrency", 6),
                    custom_tmp=get_config_value(config, "temp_dir", "").strip(),
                    retries=get_config_value(config, "download_retries", 3),
                    headers=client.auth_headers,
                ),
                send_to_session,
            )

        async def _push_file(umo, title, chapter):
            return await push_chapter_file(
                config,
                umo, title, chapter,
                lambda cid, mp=0: fetch_pages_local(
                    client, cid, max_pages=mp,
                    concurrency=get_config_value(config, "download_concurrency", 6),
                    custom_tmp=get_config_value(config, "temp_dir", "").strip(),
                    retries=get_config_value(config, "download_retries", 3),
                    headers=client.auth_headers,
                ),
                send_to_session,
            )

        async def _render_update_card(umo, items, heading):
            try:
                tmpldata = build_update_card(items, heading)
                tmpldata["items"] = await embed_covers(
                    client, tmpldata["items"],
                    custom_tmp=get_config_value(config, "temp_dir", "").strip(),
                    retries=get_config_value(config, "download_retries", 3),
                    concurrency=get_config_value(config, "download_concurrency", 6),
                )
                return await self._render_card_result(tmpldata)
            except Exception as e:
                logger.warning(f"[{PLUGIN_NAME}] 更新通知卡片渲染失败: {e}")
                return None

        async def _check(force=False):
            render_fn = _render_update_card if self._result_cards_enabled() else None
            return await _check_updates(
                client, self.sub_mgr, send_to_session, config,
                self.get_kv_data, self.put_kv_data,
                self._update_lock,
                _push_images, _push_file,
                force=force,
                render_update_card_fn=render_fn,
            )
        self._check_updates_fn = _check

    # ── Search cache ───────────────────────────────────────────────

    def _get_cached_manga(self, umo: str, key: str) -> Manga | None:
        cache = ttl_cache_lookup(self._search_cache, umo, _CACHE_TTL)
        if cache is None:
            return None
        return cache.get(key)

    def _set_search_cache(self, umo: str, cache: dict[str, Manga]):
        ttl_cache_store(
            self._search_cache, umo, cache, _SEARCH_CACHE_MAX_ENTRIES
        )

    def _result_cards_enabled(self) -> bool:
        if not config_bool(get_config_value(self.config, "result_cards_enabled", False), False):
            return False
        if time.time() < getattr(self, "_card_cooldown_until", 0.0):
            return False
        return True

    def _card_render_fn(self):
        """Pick the card renderer: 本地渲染（htmlrender）或自建 T2I 服务。"""
        source = str(get_config_value(self.config, "t2i_source", "system") or "system").strip().lower()
        endpoint = normalize_endpoint(get_config_value(self.config, "t2i_endpoint", ""))
        if source == "custom" and not endpoint and not getattr(self, "_t2i_endpoint_warned", False):
            self._t2i_endpoint_warned = True
            logger.warning(
                f"[{PLUGIN_NAME}] T2I 服务来源为「单独配置」但端点为空，"
                "已回退系统渲染；请在设置中填写 T2I 端点"
            )
        return get_renderer(source, endpoint or "", get_config_value(self.config, "temp_dir", "") or None)

    def _reset_after_config_change(self) -> None:
        """WebUI 保存配置后丢弃与旧配置相关的状态。

        卡片图片按 tmpldata 缓存，渲染失败又有冷却窗口；两者都会在用户
        修复/切换 T2I 设置后继续提供或阻挡旧渲染器的结果，这里统一清空。
        """
        self._search_cache.clear()
        self._card_cache.clear()
        self._card_cooldown_until = 0.0
        self._t2i_endpoint_warned = False
        reset_renderer_cache()

    async def _render_card_result(self, tmpldata: dict) -> str | None:
        """Render one card to a local file; return path or None on failure.

        A failure starts a cooldown window (``CARD_FAIL_COOLDOWN``) during
        which ``_result_cards_enabled`` returns False, so commands fall back
        to text immediately instead of waiting for the render timeout again.
        """
        try:
            timeout = float(get_config_value(self.config, "card_render_timeout_sec", 30))
        except (TypeError, ValueError):
            timeout = 30.0
        timeout = max(5.0, min(timeout, 120.0))
        try:
            path = await render_card_cached(
                self._card_cache, self._card_render_fn(), tmpldata, timeout=timeout
            )
        except Exception as exc:
            logger.warning(f"[{PLUGIN_NAME}] 卡片渲染异常: {exc}")
            path = None
        if path:
            self._card_cooldown_until = 0.0
        else:
            self._card_cooldown_until = time.time() + CARD_FAIL_COOLDOWN
        if path:
            schedule_cleanup_file(path, delay=CARD_CACHE_TTL + 60)
        return path

    def _cover_args(self) -> dict:
        return {
            "custom_tmp": get_config_value(self.config, "temp_dir", "").strip(),
            "retries": get_config_value(self.config, "download_retries", 3),
            "concurrency": get_config_value(self.config, "download_concurrency", 6),
        }

    async def _prepare_chapter_delivery(self, ctx: MsgContext, target):
        """Build one chapter result for command yield or direct sending."""
        max_pages = get_config_value(self.config, "max_pages", 30)
        send_mode = get_config_value(self.config, "send_mode", "image")
        fetch_mode = get_config_value(self.config, "image_fetch_mode", "download")
        concurrency = get_config_value(self.config, "download_concurrency", 6)
        custom_tmp = get_config_value(self.config, "temp_dir", "").strip()
        retries = get_config_value(self.config, "download_retries", 3)

        local_paths: list[str] = []
        tmp_dir: Path | None = None
        if fetch_mode == "download":
            total_pages, page_urls, local_paths, tmp_dir = await fetch_pages_local(
                self.client, target.id, max_pages, concurrency, custom_tmp, retries,
                headers=self.client.auth_headers,
            )
            if page_urls and not any(local_paths):
                logger.error(
                    f"[{PLUGIN_NAME}] 所有 {len(page_urls)} 张图片下载均失败，"
                    "请检查 Suwayomi 是否开启了认证，以及插件的认证配置是否正确"
                )
                return None, total_pages, 0, tmp_dir
        else:
            pages = await self.client.fetch_chapter_pages(target.id)
            if not pages:
                return None, 0, 0, None
            total_pages = len(pages)
            page_urls = [self.client.build_image_url(page) for page in pages[:max_pages]]
            if self.client.auth_mode != "none":
                logger.warning(
                    f"[{PLUGIN_NAME}] 图片获取方式为 URL 模式，但 Suwayomi 开启了 "
                    f"{self.client.auth_mode} 认证，图片可能无法加载，请在配置中改用下载模式"
                )

        if not page_urls:
            return None, total_pages, 0, tmp_dir

        msg = build_image_msg(
            page_urls, local_paths, fetch_mode,
            send_mode=send_mode,
            forward_ok=ctx.supports_forward,
            page_label=fmt_chapter_display(target),
            header=None,
            total_pages=total_pages,
            max_pages=max_pages,
            tail_text=f"... 还有 {total_pages - max_pages} 页，请到 WebUI 查看",
        )
        return msg, total_pages, len(page_urls), tmp_dir

    async def _prepare_chapter_file_delivery(
        self,
        ctx: MsgContext,
        manga: Manga,
        target,
        fmt: str,
    ):
        """Download all chapter pages and build a PDF/ZIP/CBZ file result."""
        concurrency = get_config_value(self.config, "download_concurrency", 6)
        custom_tmp = get_config_value(self.config, "temp_dir", "").strip()
        retries = get_config_value(self.config, "download_retries", 3)
        total_pages, page_urls, local_paths, tmp_dir = await fetch_pages_local(
            self.client,
            target.id,
            max_pages=get_file_delivery_max_pages(self.config),
            concurrency=concurrency,
            custom_tmp=custom_tmp,
            retries=retries,
            headers=self.client.auth_headers,
        )
        if not page_urls:
            return None, total_pages, 0, tmp_dir, None

        valid_paths = [path for path in local_paths if path]
        if not valid_paths:
            return None, total_pages, 0, tmp_dir, None
        if len(valid_paths) < len(page_urls):
            logger.warning(
                f"[{PLUGIN_NAME}] {len(page_urls) - len(valid_paths)} 页下载失败，"
                f"将用已有页面打包 {fmt.upper()}"
            )

        label = fmt_chapter_display(target)
        file_ext = normalize_pack_format(fmt)
        output_path = build_chapter_output_path(
            Path(valid_paths[0]).parent, manga.title, str(label), file_ext
        )

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, pack_images, valid_paths, output_path, fmt)

        filename = output_path.name
        return file_msg(output_path, filename), total_pages, len(valid_paths), tmp_dir, filename

    # ── Commands ───────────────────────────────────────────────────

    async def help_cmd(self, ctx: MsgContext):
        """显示漫画助手使用帮助"""
        from .menu import format_help

        await ctx.send(text_msg(format_help()))

    async def list_sources(self, ctx: MsgContext):
        """列出所有已安装的漫画源"""
        try:
            sources = await self.client.get_sources()
            if not sources:
                await ctx.send(text_msg("未找到已安装的漫画源，请在 Suwayomi WebUI 中安装扩展。"))
                return
            lines = ["📚 已安装的漫画源:"]
            for i, src in enumerate(sources, 1):
                lines.append(f"  [{i}] {sanitize_for_message(src.display_name, limit=40)} ({src.lang})")
            await ctx.send(text_msg("\n".join(lines)))
        except SuwayomiError as e:
            await ctx.send(text_msg(f"获取源列表失败: {e}"))
        except Exception as e:
            logger.error(f"[{PLUGIN_NAME}] list_sources error: {e}")
            await ctx.send(text_msg("漫画服务暂时不可用，请稍后重试。"))

    async def search_manga(self, ctx: MsgContext, keyword: str):
        """搜索漫画。用法: /漫画 搜索 <关键词> [源名]"""
        try:
            # 参数按空格切分会丢掉尾部的源名 —— 从完整消息重新解析。
            keyword, source_hint = split_search_query(ctx.message_str, keyword)
            if not keyword:
                await ctx.send(text_msg(
                    "用法: /漫画 搜索 <关键词> [源名]（多词标题用 + 连接）"
                ))
                return

            sources = await self.client.get_sources()
            if not sources:
                await ctx.send(text_msg("未找到已安装的漫画源。"))
                return

            search_query = keyword
            target_sources: list = []
            if source_hint:
                hinted = match_source_hint(sources, source_hint)
                if hinted:
                    target_sources = [hinted]
                else:
                    # 不是源名 —— 视为漫画关键词的一部分。
                    search_query = f"{keyword} {source_hint}".strip()
                    await ctx.send(text_msg(
                        f"未找到名为「{source_hint}」的漫画源，已将其作为关键词继续搜索。"
                        "可用「漫画 源」查看全部源。"
                    ))
            if not target_sources:
                # 与 AI 工具路径一致的源选择：跳过本地源，优先不同扩展而非语言变体
                target_sources = select_search_sources(
                    sources,
                    "",
                    get_config_value(self.config, "default_source_id", 0),
                    max_sources=5,
                )

            ranking_on = config_bool(
                get_config_value(self.config, "search_result_ranking", True), True
            )
            display_limit = _bounded_int(
                get_config_value(self.config, "search_display_limit", 20), 20, 1, 50
            )
            # 截断刷新依赖排序开启：关闭 search_result_ranking 时须完整
            # 恢复旧版行为（不刷新、按源分组原样输出）
            refresh_on = ranking_on and config_bool(
                get_config_value(self.config, "search_refresh_truncated_titles", True),
                True,
            )
            expand_on = ranking_on and config_bool(
                get_config_value(self.config, "search_alias_expansion", True), True
            )
            mirror_on = config_bool(
                get_config_value(self.config, "bangumi_mirror", False), False
            )
            mirror_url = str(
                get_config_value(self.config, "bangumi_mirror_url", "") or ""
            ).strip()

            async def _search_source(src):
                try:
                    result = await asyncio.wait_for(
                        self.client.search_manga(src.id, search_query), timeout=15
                    )
                    # 来源显示名由源扩展控制，进入消息前过清洗
                    return sanitize_for_message(src.display_name, limit=40), result
                except Exception as e:
                    logger.warning(f"[{PLUGIN_NAME}] 搜索源 {src.name} 失败: {e}")
                    return sanitize_for_message(src.display_name, limit=40), None

            # Bangumi 别名解析与源搜索并行执行，互不等待
            resolve_task = (
                asyncio.create_task(
                    resolve_aliases(
                        search_query,
                        bases=api_bases(mirror_on, mirror_url),
                        deadline=20,
                    )
                ) if expand_on else None
            )
            # 并发请求全部源（与 AI 工具路径一致），单源 15s 超时；
            # 失败源记为 None，不阻塞其它源的结果
            try:
                responses = await asyncio.gather(
                    *(_search_source(src) for src in target_sources)
                )
            except BaseException:
                # 命令协程被取消（插件热重载/卸载）时，并行的 Bangumi
                # 解析任务不能变成失去外层兜底的孤儿
                if resolve_task is not None:
                    resolve_task.cancel()
                raise
            resolution = None
            if resolve_task is not None:
                try:
                    # 整体预算兜底：Bangumi 请求最坏等待不允许拖住用户命令，
                    # 超时按「解析失败」静默跳过扩展
                    resolution = await asyncio.wait_for(resolve_task, timeout=20)
                except Exception:
                    resolution = None

            if responses and all(result is None for _, result in responses):
                logger.warning(
                    f"[{PLUGIN_NAME}] 全部 {len(responses)} 个源搜索失败，"
                    "「未找到」可能由网络/限流导致"
                )

            # pool: (manga, 源显示名, Bangumi 溯源条目id|None)；排序开启时
            # 统一按标题相关度混排，编号在排序后分配（同分保持源顺序）
            pool: list[tuple[Manga, str, int | None]] = [
                (m, source_name, None)
                for source_name, result in responses
                if result
                for m in result.mangas
            ]
            logger.debug(
                f"[{PLUGIN_NAME}] 搜索 {search_query!r}: "
                f"{len(target_sources)} 个源, {len(pool)} 条结果"
            )
            if refresh_on and pool:
                # 源站列表页截断的长标题（我的首推是恶役...）从详情页补全，
                # 失败保留原标题（排序有反向包含兜底）
                try:
                    await refresh_truncated_titles(
                        self.client, [m for m, _, _ in pool]
                    )
                except Exception as e:
                    logger.warning(f"[{PLUGIN_NAME}] 截断标题刷新失败: {e}")

            def _rank_pool(items):
                decorated = []
                for i, (m, source_name, prov) in enumerate(items):
                    base = score_title(search_query, m.title)
                    if resolution is not None:
                        base = max(
                            base,
                            alias_boost(
                                search_query, m.title, prov, resolution,
                                base_score=base,
                            ),
                        )
                    decorated.append((base, i, (m, source_name, prov)))
                decorated.sort(key=lambda x: (-x[0], x[1]))
                return [it for _, _, it in decorated], [sc for sc, _, _ in decorated]

            if ranking_on:
                pool, scores = _rank_pool(pool)
                best = scores[0] if scores else 0.0
            else:
                best = 0.0

            # ── 第二轮：无强命中时用 Bangumi 别名有界重搜 ──
            expansion_note = ""
            suggestion = ""
            if (
                ranking_on
                and expand_on
                and resolution is not None
                and len(normalize_for_rank(search_query)) >= 2
                and best < STRONG_MATCH_THRESHOLD
            ):
                probes = build_probes(search_query, resolution)
                if probes:

                    async def _probe(src, query, sid):
                        try:
                            result = await asyncio.wait_for(
                                self.client.search_manga(src.id, query), timeout=15
                            )
                            return result, sanitize_for_message(src.display_name, limit=40), sid
                        except Exception as e:
                            logger.debug(
                                f"[{PLUGIN_NAME}] Bangumi 探针 {query!r} "
                                f"在 {src.name} 搜索失败: {e}"
                            )
                            return None, sanitize_for_message(src.display_name, limit=40), sid

                    probe_responses = await asyncio.gather(
                        *(
                            _probe(src, query, sid)
                            for query, sid in probes
                            for src in target_sources
                        )
                    )
                    seen_ids = {m.id for m, _, _ in pool}
                    added: list[tuple[Manga, str, int | None]] = []
                    for result, source_name, sid in probe_responses:
                        if not result:
                            continue
                        for m in result.mangas:
                            if m.id in seen_ids:
                                continue
                            seen_ids.add(m.id)
                            added.append((m, source_name, sid))
                    if added:
                        if refresh_on:
                            try:
                                await refresh_truncated_titles(
                                    self.client, [m for m, _, _ in added]
                                )
                            except Exception as e:
                                logger.warning(f"[{PLUGIN_NAME}] 截断标题刷新失败: {e}")
                        merged, merged_scores = _rank_pool(pool + added)
                        if merged_scores and merged_scores[0] >= STRONG_MATCH_THRESHOLD:
                            # 扩展带来强命中才并入展示；否则保持第一轮结果
                            pool, scores = merged, merged_scores
                            aliases = confident_aliases(search_query, resolution)
                            used = "、".join(
                                sanitize_for_message(a) for a in aliases[:3]
                            ) or "、".join(
                                sanitize_for_message(qr) for qr, _ in probes
                            )
                            expansion_note = (
                                f"\n💡 关键词无强命中，已通过 Bangumi 别名"
                                f"「{used[:80]}」扩展搜索"
                            )
                            logger.debug(
                                f"[{PLUGIN_NAME}] Bangumi 扩展采纳: "
                                f"{len(probes)} 个探针, 新增 {len(added)} 条, "
                                f"最强分 {merged_scores[0]:.0f}"
                            )
                        else:
                            logger.debug(
                                f"[{PLUGIN_NAME}] Bangumi 扩展未达强命中线，保持第一轮结果"
                            )
                    else:
                        logger.debug(
                            f"[{PLUGIN_NAME}] Bangumi 探针未带回新结果，保持第一轮结果"
                        )
                else:
                    logger.debug(
                        f"[{PLUGIN_NAME}] 未生成可用 Bangumi 探针，跳过扩展搜索"
                    )
                if best < STRONG_MATCH_THRESHOLD and not expansion_note:
                    aliases = (
                        confident_aliases(search_query, resolution)
                        if resolution.confident
                        else []
                    )
                    if aliases:
                        cleaned = "》/《".join(
                            sanitize_for_message(a) for a in aliases
                        )
                        suggestion = (
                            f"\n💡 你说的可能是《{cleaned}》？"
                            "试试完整标题或用别名重新搜索"
                        )

            if not pool:
                await ctx.send(text_msg("未找到相关漫画，请确认关键词。" + suggestion))
                return

            lines: list[str] = []
            idx = 1
            cache: dict[str, Manga] = {}
            card_rows: list[dict] = []

            def _append_row(m: Manga, source_name: str) -> None:
                nonlocal idx, lines
                cache[str(idx)] = m
                card_rows.append({
                    "index": idx,
                    "title": m.title,
                    "status": m.status,
                    "source": source_name,
                    "thumbnail_url": m.thumbnail_url,
                })
                idx += 1

            if ranking_on:
                total = len(pool)
                if expansion_note:
                    lines.append(expansion_note)
                lines.append(f"\n🔍 搜索结果（{total} 条，按相关度排序）:")
                for m, source_name, _prov in pool[:display_limit]:
                    status = STATUS_EMOJI.get(m.status, "未知")
                    lines.append(f"  [{idx}] {sanitize_for_message(m.title, limit=80)} - {status}（{source_name}）")
                    _append_row(m, source_name)
                tail = "回复「漫画 订阅 <编号>」订阅，如「漫画 订阅 1」"
                if idx - 1 < total:
                    tail = f"已按相关度显示前 {idx - 1} 条（共 {total} 条）\n{tail}"
                lines.append("\n" + tail)
                subtitle = f"按相关度排序 · {total} 条"
                if suggestion:
                    lines.append(suggestion)
            else:
                # 关闭排序：保持旧版按源分组的输出格式
                for source_name, result in responses:
                    if result and result.mangas:
                        lines.append(f"\n🔍 搜索结果（源: {source_name}）:")
                        for m in result.mangas:
                            status = STATUS_EMOJI.get(m.status, "未知")
                            lines.append(f"  [{idx}] {sanitize_for_message(m.title, limit=80)} - {status}")
                            _append_row(m, source_name)
                # 旧版末尾的订阅提示行（关闭排序 = 完整恢复旧版输出）
                lines.append("\n回复「漫画 订阅 <编号>」订阅，如「漫画 订阅 1」")
                subtitle = (
                    f"{' · '.join(dict.fromkeys(n for n, r in responses if r))}"
                    f" · {len(card_rows)} 条"
                )

            text = "\n".join(lines)
            self._set_search_cache(ctx.session_key, cache)

            if self._result_cards_enabled():
                try:
                    tmpldata = build_search_card(
                        card_rows,
                        subtitle,
                        "回复「漫画 订阅 编号」订阅，如「漫画 订阅 1」",
                    )
                    tmpldata["rows"] = await embed_covers(
                        self.client, tmpldata["rows"], **self._cover_args()
                    )
                    path = await self._render_card_result(tmpldata)
                    if path:
                        await ctx.send(image_file_msg(path))
                        return
                except Exception as e:
                    logger.warning(f"[{PLUGIN_NAME}] 搜索卡片渲染失败，回退文本: {e}")

            await ctx.send(text_msg(text))

        except SuwayomiError as e:
            await ctx.send(text_msg(f"搜索失败: {e}"))
        except Exception as e:
            logger.error(f"[{PLUGIN_NAME}] search error: {e}")
            await ctx.send(text_msg("搜索失败，漫画服务暂时不可用。"))

    async def subscribe_manga(self, ctx: MsgContext, index: str):
        """订阅漫画。用法: /漫画 订阅 <搜索结果编号>"""
        try:
            manga = self._get_cached_manga(ctx.session_key, index)
            if manga is None:
                await ctx.send(text_msg("未找到该编号的漫画，请先使用「漫画 搜索」。"))
                return

            await self.sub_mgr.subscribe(manga.id, manga.title, manga.source_id, ctx.session_key)
            logger.info(f"[{PLUGIN_NAME}] 用户订阅「{manga.title}」(ID:{manga.id})")
            try:
                chapters = await get_or_fetch_chapters(
                    self.client, self.get_kv_data, self.put_kv_data, self.config, manga.id
                )
                if chapters:
                    max_id = max(ch.id for ch in chapters)
                    await self.sub_mgr.update_latest_chapter(manga.id, max_id)
            except Exception as e:
                logger.warning(f"[{PLUGIN_NAME}] 拉取「{manga.title}」章节失败: {e}")

            if self._result_cards_enabled():
                try:
                    tmpldata = build_subscribe_confirm_card(
                        {"id": manga.id, "title": manga.title,
                         "status": manga.status, "thumbnail_url": manga.thumbnail_url,
                         "description": manga.description},
                        source_name="",
                    )
                    card = (await embed_covers(
                        self.client, [tmpldata], **self._cover_args()
                    ))[0]
                    path = await self._render_card_result(card)
                    if path:
                        await ctx.send(image_file_msg(path))
                        return
                except Exception as e:
                    logger.warning(f"[{PLUGIN_NAME}] 订阅确认卡片渲染失败，回退文本: {e}")
            await ctx.send(text_msg(
                f"✅ 已订阅「{sanitize_for_message(manga.title, limit=80)}」，有新章节时会推送。"
            ))
        except Exception as e:
            logger.error(f"[{PLUGIN_NAME}] subscribe error: {e}")
            await ctx.send(text_msg("订阅失败，请稍后重试。"))

    async def batch_subscribe(self, ctx: MsgContext):
        """批量订阅漫画。用法: /漫画 批量订阅 <名称1>, <名称2>, ... [源名]"""
        try:
            raw = ctx.message_str.strip()
            # 按空格定位子命令，兼容带/不带命令前缀（/、! 等）的消息
            tokens = raw.split()
            args_str = ""
            try:
                cmd_idx = tokens.index("批量订阅")
                args_str = " ".join(tokens[cmd_idx + 1:]).strip()
            except ValueError:
                pass
            if not args_str:
                await ctx.send(text_msg("用法: 漫画 批量订阅 <名称1>, <名称2>, ... [源名]\n名称用逗号分隔，如: 漫画 批量订阅 咒术回战, 鬼灭之刃"))
                return

            sources = await self.client.get_sources()
            src_map = {
                str(s.id): sanitize_for_message(s.display_name, limit=40)
                for s in sources
            }
            source_filter = None
            search_str = args_str

            last_space = args_str.rfind(" ")
            if last_space > 0:
                potential_source = args_str[last_space + 1:].lower()
                for src in sources:
                    if potential_source in (src.name.lower(), src.display_name.lower(), src.lang.lower()):
                        source_filter = src
                        search_str = args_str[:last_space]
                        break

            raw_names = [n.strip() for n in re.split(r"[,，;；]", search_str) if n.strip()]
            if not raw_names:
                await ctx.send(text_msg("请提供漫画名称，用逗号分隔。"))
                return
            if len(raw_names) > 20:
                await ctx.send(text_msg("一次最多批量订阅 20 部漫画。"))
                return

            await ctx.send(text_msg(f"📚 开始批量订阅 {len(raw_names)} 部漫画..."))

            umo = ctx.session_key
            existing_subs = await self.sub_mgr.get_subscriptions(umo)
            existing_ids = {s["manga_id"] for s in existing_subs}

            results: list[tuple[str, str, str]] = []
            card_rows: list[dict] = []
            for i, name in enumerate(raw_names, 1):
                await ctx.send(text_msg(f"正在处理 [{i}/{len(raw_names)}] {name}..."))
                manga, error = await search_best_match(self.client, self.config, name, source_filter)
                if error or manga is None:
                    results.append((name, "fail", error or "未找到匹配结果"))
                    card_rows.append({"status": "fail", "title": name,
                                      "detail": error or "未找到匹配结果", "thumbnail_url": None})
                    continue

                if manga.id in existing_ids:
                    status_text = STATUS_EMOJI.get(manga.status, "未知")
                    source_name = src_map.get(str(manga.source_id), "")
                    results.append((name, "exists", f"{sanitize_for_message(manga.title, limit=80)} - {status_text} - {source_name}"))
                    card_rows.append({"status": "exists", "title": manga.title,
                                      "detail": f"{status_text} - {source_name}（已订阅）",
                                      "thumbnail_url": manga.thumbnail_url})
                    continue

                await self.sub_mgr.subscribe(manga.id, manga.title, manga.source_id, umo)
                existing_ids.add(manga.id)
                try:
                    chapters = await get_or_fetch_chapters(
                        self.client, self.get_kv_data, self.put_kv_data, self.config, manga.id
                    )
                    if chapters:
                        max_id = max(ch.id for ch in chapters)
                        await self.sub_mgr.update_latest_chapter(manga.id, max_id)
                except Exception as e:
                    logger.warning(f"[{PLUGIN_NAME}] 批量订阅拉取「{manga.title}」章节失败: {e}")

                status_text = STATUS_EMOJI.get(manga.status, "未知")
                source_name = src_map.get(str(manga.source_id), "")
                results.append((name, "ok", f"{sanitize_for_message(manga.title, limit=80)} - {status_text} - {source_name}"))
                card_rows.append({"status": "ok", "title": manga.title,
                                  "detail": f"{status_text} - {source_name}",
                                  "thumbnail_url": manga.thumbnail_url})

            ok_count = sum(1 for _, s, _ in results if s == "ok")
            exist_count = sum(1 for _, s, _ in results if s == "exists")
            fail_count = sum(1 for _, s, _ in results if s == "fail")

            lines = [f"📚 批量订阅完成 ({ok_count} 新增, {exist_count} 已存在, {fail_count} 失败):"]
            for name, status, info in results:
                if status == "ok":
                    lines.append(f"  ✅ {info}")
                elif status == "exists":
                    lines.append(f"  ⏭ {info} (已订阅)")
                else:
                    lines.append(f"  ❌ {name} - {info}")
            summary_text = "\n".join(lines)

            if self._result_cards_enabled():
                try:
                    tmpldata = build_batch_card(
                        card_rows,
                        f"{ok_count} 新增, {exist_count} 已存在, {fail_count} 失败",
                    )
                    tmpldata["rows"] = await embed_covers(
                        self.client, tmpldata["rows"], **self._cover_args()
                    )
                    path = await self._render_card_result(tmpldata)
                    if path:
                        await ctx.send(image_file_msg(path))
                        return
                except Exception as e:
                    logger.warning(f"[{PLUGIN_NAME}] 批量订阅卡片渲染失败，回退文本: {e}")

            await ctx.send(text_msg(summary_text))

        except Exception as e:
            logger.error(f"[{PLUGIN_NAME}] batch_subscribe error: {e}")
            await ctx.send(text_msg("批量订阅失败，请稍后重试。"))

    async def unsubscribe_manga(self, ctx: MsgContext, manga_id_or_name: str):
        """取消订阅。用法: /漫画 取消订阅 <漫画ID或名称>"""
        try:
            umo = ctx.session_key
            manga_id = None
            manga_title = manga_id_or_name
            try:
                manga_id = int(manga_id_or_name)
            except ValueError:
                norm_input = normalize_zh(manga_id_or_name)
                subs = await self.sub_mgr.get_subscriptions(umo)
                for s in subs:
                    if norm_input in normalize_zh(s["title"]):
                        manga_id = s["manga_id"]
                        manga_title = s["title"]
                        break

            if manga_id is None:
                await ctx.send(text_msg("未找到匹配的订阅，请使用漫画 ID 或名称。"))
                return
            await self.sub_mgr.unsubscribe(manga_id, umo)
            logger.info(f"[{PLUGIN_NAME}] 用户取消订阅「{manga_title}」(ID:{manga_id})")
            await ctx.send(text_msg(f"✅ 已取消订阅（漫画 ID: {manga_id}）。"))
        except Exception as e:
            logger.error(f"[{PLUGIN_NAME}] unsubscribe error: {e}")
            await ctx.send(text_msg("取消订阅失败，请稍后重试。"))

    async def my_subscriptions(self, ctx: MsgContext):
        """查看当前会话的订阅列表"""
        try:
            subs = await self.sub_mgr.get_subscriptions(ctx.session_key)
            if not subs:
                await ctx.send(text_msg("📭 你还没有订阅任何漫画。使用「漫画 搜索」来查找并订阅。"))
                return
            sources = await self.client.get_sources()
            src_map = {
                str(s.id): sanitize_for_message(s.display_name, limit=40)
                for s in sources
            }

            if self._result_cards_enabled():
                try:
                    sem = asyncio.Semaphore(8)

                    async def _fetch_manga(s):
                        async with sem:
                            return await self.client.get_manga(s["manga_id"])

                    metadatas = await asyncio.gather(
                        *(_fetch_manga(s) for s in subs),
                        return_exceptions=True,
                    )
                    card_rows = []
                    for s, meta in zip(subs, metadatas):
                        thumbnail = meta.thumbnail_url if not isinstance(meta, Exception) else None
                        status = meta.status if not isinstance(meta, Exception) else "UNKNOWN"
                        src_name = src_map.get(str(s["source_id"]), "")
                        push = "🔔 推送开" if s.get("push_enabled") else "🔕 推送关"
                        tag = f" - {src_name}" if src_name else ""
                        card_rows.append({
                            "title": s["title"],
                            "detail": f"{STATUS_EMOJI.get(status, '未知')}{tag} · {push} · ID: {s['manga_id']}",
                            "thumbnail_url": thumbnail,
                        })
                    tmpldata = build_subscriptions_card(card_rows)
                    tmpldata["rows"] = await embed_covers(
                        self.client, tmpldata["rows"], **self._cover_args()
                    )
                    path = await self._render_card_result(tmpldata)
                    if path:
                        await ctx.send(image_file_msg(path))
                        return
                except Exception as e:
                    logger.warning(f"[{PLUGIN_NAME}] 订阅列表卡片渲染失败，回退文本: {e}")

            lines = ["📋 你的订阅列表:"]
            for s in subs:
                source_name = src_map.get(str(s["source_id"]), "")
                tag = f" - {source_name}" if source_name else ""
                lines.append(
                    f"  • {sanitize_for_message(s['title'], limit=80)}{tag} - ID: {s['manga_id']}"
                )
            await ctx.send(text_msg("\n".join(lines)))
        except Exception as e:
            logger.error(f"[{PLUGIN_NAME}] my_subscriptions error: {e}")
            await ctx.send(text_msg("获取订阅列表失败。"))

    # ── Push sub-commands ──────────────────────────────────────────

    async def push_enable(self, ctx: MsgContext):
        """开启当前会话的漫画自动推送"""
        try:
            umo = ctx.session_key
            subs = await self.sub_mgr.get_subscriptions(umo)
            if not subs:
                await ctx.send(text_msg("📭 你还没有订阅任何漫画，请先使用「漫画 搜索」订阅。"))
                return
            await self.sub_mgr.set_auto_push_all(umo, True)
            await self.sub_mgr.set_push_default(umo, True)
            await ctx.send(text_msg(f"✅ 已开启自动推送，共 {len(subs)} 部漫画。有更新时将自动推送内容。"))
        except Exception as e:
            logger.error(f"[{PLUGIN_NAME}] push_enable error: {e}")
            await ctx.send(text_msg("开启自动推送失败。"))

    async def push_disable(self, ctx: MsgContext):
        """关闭当前会话的漫画自动推送"""
        try:
            umo = ctx.session_key
            subs = await self.sub_mgr.get_subscriptions(umo)
            if not subs:
                await ctx.send(text_msg("📭 你还没有订阅任何漫画。"))
                return
            await self.sub_mgr.set_auto_push_all(umo, False)
            await self.sub_mgr.clear_push_default(umo)
            await ctx.send(text_msg("✅ 已关闭自动推送。有更新时将只发送文本通知。"))
        except Exception as e:
            logger.error(f"[{PLUGIN_NAME}] push_disable error: {e}")
            await ctx.send(text_msg("关闭自动推送失败。"))

    async def push_status(self, ctx: MsgContext):
        """查看当前会话的自动推送状态"""
        try:
            umo = ctx.session_key
            subs = await self.sub_mgr.get_subscriptions(umo)
            if not subs:
                await ctx.send(text_msg("📭 你还没有订阅任何漫画。"))
                return
            lines = ["📡 自动推送状态:"]
            all_subs = await self.sub_mgr.get_all_subscriptions()
            for s in subs:
                enabled = self.sub_mgr.is_auto_push_enabled(all_subs, s["manga_id"], umo)
                status = "✅ 开启" if enabled else "❌ 关闭"
                lines.append(f"  • {sanitize_for_message(s['title'], limit=80)} — {status}")
            await ctx.send(text_msg("\n".join(lines)))
        except Exception as e:
            logger.error(f"[{PLUGIN_NAME}] push_status error: {e}")
            await ctx.send(text_msg("获取推送状态失败。"))

    # ── Chapter list ───────────────────────────────────────────────

    async def list_chapters(self, ctx: MsgContext, manga_name_or_id: str):
        """查看漫画章节列表。用法: /漫画 章节 <漫画名或ID> [--刷新]"""
        try:
            tokens = ctx.message_str.strip().split()
            try:
                cmd_idx = tokens.index("章节")
                args = tokens[cmd_idx + 1:]
            except ValueError:
                args = []

            force = "--刷新" in args
            manga_name_or_id = " ".join(a for a in args if a != "--刷新").strip()
            if not manga_name_or_id:
                await ctx.send(text_msg("用法: /漫画 章节 <漫画名或ID> [--刷新]"))
                return

            manga, err = await resolve_manga(self.client, self.sub_mgr, ctx.session_key, manga_name_or_id, "章节")
            if err or manga is None:
                await ctx.send(text_msg(err or "未找到该漫画。"))
                return

            show_cover = get_config_value(self.config, "chapter_list_show_cover", True)
            cards_active = self._result_cards_enabled() and show_cover
            cover_path: str | None = None
            cover_tmp: Path | None = None

            if show_cover and manga.thumbnail_url and not cards_active:
                # 卡片模式下封面由 embed_covers 统一下载，避免与 download_cover 双重下载；
                # 卡片渲染失败回退时直接走纯文本（无封面）。
                chapters_result, cover_result = await asyncio.gather(
                    get_or_fetch_chapters(
                        self.client, self.get_kv_data, self.put_kv_data, self.config, manga.id, force=force
                    ),
                    download_cover(
                        self.client,
                        manga.thumbnail_url,
                        custom_tmp=get_config_value(self.config, "temp_dir", "").strip(),
                        retries=get_config_value(self.config, "download_retries", 3),
                        headers=self.client.auth_headers,
                    ),
                    return_exceptions=True,
                )
                if isinstance(chapters_result, Exception):
                    # If the cover was already downloaded, do not leak its temp dir.
                    if isinstance(cover_result, tuple) and cover_result[0]:
                        schedule_cleanup(cover_result[1], delay=60)
                    raise chapters_result
                chapters = chapters_result
                if not isinstance(cover_result, Exception):
                    cover_path, cover_tmp = cover_result
            else:
                chapters = await get_or_fetch_chapters(
                    self.client, self.get_kv_data, self.put_kv_data, self.config, manga.id, force=force
                )

            if not chapters:
                no_chapter_msg = f"「{sanitize_for_message(manga.title, limit=80)}」暂无章节。"
                if cover_path:
                    msg = no_chapter_msg
                    schedule_cleanup(cover_tmp, delay=60)
                    await ctx.send(chain_msg([Image(path=cover_path), Text(msg)]))
                else:
                    await ctx.send(text_msg(no_chapter_msg))
                return

            chapters.sort(key=lambda ch: ch.source_order)
            num_count: dict[float, int] = {}
            for ch in chapters:
                num_count[ch.chapter_number] = num_count.get(ch.chapter_number, 0) + 1

            try:
                sources = await self.client.get_sources()
                src_name = next(
                    (
                        sanitize_for_message(s.display_name, limit=40)
                        for s in sources
                        if str(s.id) == str(manga.source_id)
                    ),
                    None,
                )
            except Exception as exc:
                logger.debug(f"[{PLUGIN_NAME}] 章节列表获取源名失败: {exc}")
                src_name = None
            src_tag = f" - {src_name}" if src_name else ""

            if cards_active:
                try:
                    lines_card: list[str] = []
                    dl_count = 0
                    for ch in chapters:
                        dl_mark = " 📥" if ch.is_downloaded else ""
                        if ch.is_downloaded:
                            dl_count += 1
                        lines_card.append(f"{fmt_chapter_label(ch, num_count)}{dl_mark}")

                    latest = fmt_chapter_num(chapters[-1].chapter_number) if chapters else "?"
                    meta = f"{src_name or '未知源'} · 共 {len(chapters)} 话"
                    if dl_count:
                        meta += f" · 本地 {dl_count}"
                    card_base = {
                        "title": manga.title,
                        "thumbnail_url": manga.thumbnail_url,
                        "meta": meta,
                        "tags": [{"text": f"最近更新 #{latest}"}],
                        "description": manga.description,
                    }
                    (card_base,) = await embed_covers(
                        self.client, [card_base], **self._cover_args()
                    )
                    cards_tmpldata, tail_lines = build_chapter_cards(card_base, lines_card)
                    rendered: list[str] = []
                    for card_data in cards_tmpldata:
                        path = await self._render_card_result(card_data)
                        if path is None:
                            rendered = []
                            break
                        rendered.append(path)
                    if rendered:
                        await ctx.send(image_file_msg(rendered[0]))
                        for path in rendered[1:]:
                            await ctx.send(image_file_msg(path))
                        if tail_lines:
                            tail_text = "\n".join(tail_lines)
                            for i in range(0, len(tail_text), 1500):
                                chunk = tail_text[i:i + 1500]
                                await ctx.send(text_msg(chunk))
                        if cover_tmp is not None:
                            schedule_cleanup(cover_tmp, delay=60)
                        return
                except Exception as e:
                    logger.warning(f"[{PLUGIN_NAME}] 章节卡片渲染失败，回退旧路径: {e}")

            safe_title = sanitize_for_message(manga.title, limit=80)
            header = f"📖「{safe_title}」{src_tag} 章节列表（共 {len(chapters)} 话）:"
            chunks: list[list[str]] = [[]]
            for ch in chapters:
                dl_mark = " 📥" if ch.is_downloaded else ""
                line = f"  {fmt_chapter_label(ch, num_count)}{dl_mark}"
                current_len = sum(len(item) for item in chunks[-1]) + len(header)
                if current_len + len(line) > 1500 and chunks[-1]:
                    chunks.append([])
                chunks[-1].append(line)

            for i, chunk in enumerate(chunks):
                prefix = header if i == 0 else f"📖「{safe_title}」{src_tag} 章节续 ({i + 1}/{len(chunks)}):"
                msg = prefix + "\n" + "\n".join(chunk)
                if i == 0 and cover_path:
                    schedule_cleanup(cover_tmp, delay=60)
                    await ctx.send(chain_msg([Image(path=cover_path), Text(msg)]))
                elif i == 0:
                    await ctx.send(text_msg(msg))
                else:
                    await ctx.send(text_msg(msg))

        except SuwayomiError as e:
            await ctx.send(text_msg(f"获取章节失败: {e}"))
        except Exception as e:
            logger.error(f"[{PLUGIN_NAME}] list_chapters error: {e}")
            await ctx.send(text_msg("获取章节列表失败。"))

    # ── Read chapter ───────────────────────────────────────────────

    async def read_chapter(self, ctx: MsgContext, manga_name_or_id: str, chapter_num: str = ""):
        """阅读漫画章节。用法: /漫画 阅读 <漫画名或ID> <章节号或ID:数字>"""
        if not chapter_num:
            await ctx.send(text_msg("用法: /漫画 阅读 <漫画名或ID> <章节号>\n示例: /漫画 阅读 一拳超人 1\n指定章节 ID: /漫画 阅读 一拳超人 ID:123"))
            return

        try:
            manga, err = await resolve_manga(self.client, self.sub_mgr, ctx.session_key, manga_name_or_id, "阅读")
            if err or manga is None:
                await ctx.send(text_msg(err or "未找到该漫画。"))
                return

            chapters = await get_or_fetch_chapters(
                self.client, self.get_kv_data, self.put_kv_data, self.config, manga.id
            )

            target, err_msg = resolve_chapter(chapters, chapter_num, manga_name_or_id, "阅读")
            if err_msg:
                await ctx.send(text_msg(err_msg))
                return
            if target is None:
                await ctx.send(text_msg(
                    f"未找到「{sanitize_for_message(manga.title, limit=80)}」指定的章节。"
                ))
                return

            try:
                await ctx.send(
                    text_msg(
                        f"📖 正在加载「{sanitize_for_message(manga.title, limit=80)}」"
                        f"{fmt_chapter_display(target)}，请稍后..."
                    )
                )
            except Exception:
                pass

            tmp_dir: Path | None = None
            try:
                result, total_pages, _, tmp_dir = await self._prepare_chapter_delivery(ctx, target)
                if result is None:
                    fetch_mode = get_config_value(self.config, "image_fetch_mode", "download")
                    await ctx.send(text_msg(
                        fmt_delivery_failure_message(
                            total_pages, fetch_mode, self.client.auth_mode
                        )
                    ))
                    return
                await ctx.send(result)
            finally:
                schedule_cleanup(tmp_dir, delay=60)

        except SuwayomiError as e:
            await ctx.send(text_msg(f"阅读失败: {e}"))
        except Exception as e:
            logger.error(f"[{PLUGIN_NAME}] read_chapter error: {e}")
            await ctx.send(text_msg("阅读章节失败。"))

    # ── Download chapter ───────────────────────────────────────────

    async def download_chapter(self, ctx: MsgContext, manga_name_or_id: str, chapter_num: str = ""):
        """下载漫画章节并打包发送。用法: /漫画 下载 <漫画名或ID> <章节号或ID:数字> [zip/pdf/cbz]"""
        default_fmt = get_config_value(self.config, "download_format", "pdf")
        manga_name_or_id, chapter_num, fmt = parse_download_args(ctx.message_str, default_fmt)

        if not manga_name_or_id or not chapter_num:
            await ctx.send(text_msg(
                "用法: /漫画 下载 <漫画名或ID> <章节号> [格式]\n"
                "示例: /漫画 下载 一拳超人 1\n"
                "指定格式: /漫画 下载 一拳超人 1 pdf\n"
                "指定章节 ID: /漫画 下载 一拳超人 ID:123"
            ))
            return

        tmp_dir: Path | None = None
        try:
            manga, err = await resolve_manga(self.client, self.sub_mgr, ctx.session_key, manga_name_or_id, "下载")
            if err or manga is None:
                await ctx.send(text_msg(err or "未找到该漫画。"))
                return

            chapters = await get_or_fetch_chapters(
                self.client, self.get_kv_data, self.put_kv_data, self.config, manga.id
            )

            target, err_msg = resolve_chapter(chapters, chapter_num, manga_name_or_id, "下载")
            if err_msg:
                await ctx.send(text_msg(err_msg))
                return
            if target is None:
                await ctx.send(text_msg(
                    f"未找到「{sanitize_for_message(manga.title, limit=80)}」指定的章节。"
                ))
                return

            num_label = fmt_chapter_display(target)
            await ctx.send(
                text_msg(
                    f"⏳ 正在下载「{sanitize_for_message(manga.title, limit=80)}」{num_label}，请稍候..."
                )
            )

            concurrency = get_config_value(self.config, "download_concurrency", 6)
            custom_tmp = get_config_value(self.config, "temp_dir", "").strip()
            retries = get_config_value(self.config, "download_retries", 3)
            _, page_urls, local_paths, tmp_dir = await fetch_pages_local(
                self.client, target.id, max_pages=get_file_delivery_max_pages(self.config),
                concurrency=concurrency, custom_tmp=custom_tmp, retries=retries,
                headers=self.client.auth_headers,
            )

            if not page_urls:
                await ctx.send(text_msg(f"{num_label}暂无可用页面。"))
                return

            valid_paths = [p for p in local_paths if p]
            if not valid_paths:
                await ctx.send(text_msg("所有页面下载失败，无法打包。"))
                return

            if len(valid_paths) < len(page_urls):
                logger.warning(f"[{PLUGIN_NAME}] {len(page_urls) - len(valid_paths)} 页下载失败，将用已有页面打包")

            file_ext = normalize_pack_format(fmt)
            output_path = build_chapter_output_path(
                Path(valid_paths[0]).parent, manga.title, str(num_label), file_ext
            )

            try:
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, pack_images, valid_paths, output_path, fmt)
            except Exception as e:
                logger.error(f"[{PLUGIN_NAME}] 打包失败: {e}")
                await ctx.send(text_msg(f"打包失败: {e}"))
                return

            filename = output_path.name
            try:
                await ctx.send(file_msg(output_path, filename))
            except Exception as e:
                logger.warning(f"[{PLUGIN_NAME}] 发送文件失败，回退为图片预览: {e}")
                preview_count = min(3, len(valid_paths))
                parts: Msg = [Text(f"📄 {filename}（{len(valid_paths)} 页，文件发送不支持，以下为预览）")]
                for i in range(preview_count):
                    parts.append(Image(path=valid_paths[i]))
                await ctx.send(parts)

        except SuwayomiError as e:
            await ctx.send(text_msg(f"下载失败: {e}"))
        except Exception as e:
            logger.error(f"[{PLUGIN_NAME}] download error: {e}")
            await ctx.send(text_msg("下载失败。"))
        finally:
            schedule_cleanup(tmp_dir, delay=120)

    # ── Manual update ──────────────────────────────────────────────

    async def manual_update(self, ctx: MsgContext):
        """手动检查漫画更新"""
        if not self._check_updates_fn:
            await ctx.send(text_msg("⏳ 更新引擎尚未就绪，请稍后重试。"))
            return
        try:
            summary = await self._check_updates_fn(force=True)
            await ctx.send(text_msg(summary))
        except Exception as e:
            logger.error(f"[{PLUGIN_NAME}] manual_update error: {e}")
            await ctx.send(text_msg("更新检查失败。"))

    # ── WebUI 后端支持 ─────────────────────────────────────────────

    async def rebuild_client(self, cfg: dict | None = None) -> None:
        """配置保存后重建 Suwayomi 连接与后台任务。"""
        cfg = cfg if cfg is not None else self.config
        if self._bg_task and not self._bg_task.done():
            self._bg_task.cancel()
            await asyncio.gather(self._bg_task, return_exceptions=True)
        self._bg_task = None
        async with self._update_lock:
            await self._replace_client(cfg)
        self.start_bg_loop()

    async def _replace_client(self, cfg: dict) -> None:
        try:
            await self.client.close()
        except Exception as exc:
            logger.debug(f"[{PLUGIN_NAME}] 关闭旧客户端失败: {exc}")
        self.client = SuwayomiClient(
            server_url=get_config_value(cfg, "server_url", "http://localhost:4567"),
            auth_mode=get_config_value(cfg, "auth_mode", "none"),
            username=get_config_value(cfg, "username", ""),
            password=get_config_value(cfg, "password", ""),
        )
        self._build_check_updates_fn()
        self._reset_after_config_change()
