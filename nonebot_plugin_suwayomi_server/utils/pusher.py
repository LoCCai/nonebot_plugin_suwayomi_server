"""章节推送：把章节内容/打包文件推送到订阅会话。

消息构建与发送统一走 :mod:`msg`；``send_fn(umo, msg)`` 由调用方注入
（插件内为 :func:`msg.send_to_session`，测试可注入假实现）。
"""
from __future__ import annotations

import asyncio
import shutil
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING

from nonebot import logger

from ..suwayomi.msg import (
    ForwardNode,
    Image,
    Msg,
    Text,
    file_msg,
    forward_msg,
    parse_session_key,
    text_msg,
)
from ..suwayomi.config import get_config_value
from ..suwayomi.models import Chapter
from ..suwayomi.service import fmt_chapter_display, sanitize_for_message
from .downloader import get_file_delivery_max_pages
from .pack import build_chapter_output_path, normalize_pack_format, pack_images

if TYPE_CHECKING:
    from ..suwayomi.client import SuwayomiClient

from ..suwayomi import PLUGIN_NAME

_PLUGIN_NAME = PLUGIN_NAME

SendFn = Callable[[str, Msg], Awaitable[None]]

_cleanup_tasks: set[asyncio.Task] = set()


def supports_forward(umo: str) -> bool:
    """合并转发仅 OneBot v11 群聊可用，按 session_key 判定。"""
    parsed = parse_session_key(umo)
    return parsed is not None and parsed[1] == "group"


def schedule_cleanup(tmp_dir: Path | None, delay: int = 60) -> asyncio.Task | None:
    if tmp_dir is None:
        return None

    async def _cleanup():
        await asyncio.sleep(delay)
        try:
            await asyncio.get_running_loop().run_in_executor(
                None, lambda: shutil.rmtree(tmp_dir, ignore_errors=True)
            )
        except Exception:
            pass

    task = asyncio.create_task(_cleanup())
    _cleanup_tasks.add(task)
    task.add_done_callback(_cleanup_tasks.discard)
    return task


def cancel_pending_cleanups() -> int:
    """Cancel pending temp-dir cleanup tasks (called on plugin terminate)."""
    tasks = list(_cleanup_tasks)
    for task in tasks:
        task.cancel()
    _cleanup_tasks.clear()
    return len(tasks)


def schedule_cleanup_file(path: str | None, delay: int = 60) -> asyncio.Task | None:
    """Schedule deletion of a single rendered-card image file."""
    if not path:
        return None

    async def _cleanup():
        await asyncio.sleep(delay)
        try:
            Path(path).unlink(missing_ok=True)
        except Exception:
            pass

    task = asyncio.create_task(_cleanup())
    _cleanup_tasks.add(task)
    task.add_done_callback(_cleanup_tasks.discard)
    return task


def build_image_msg(
    page_urls: list[str],
    local_paths: list[str],
    fetch_mode: str,
    *,
    send_mode: str,
    forward_ok: bool,
    page_label: str,
    header: str | None = None,
    header_node_name: str | None = None,
    total_pages: int,
    max_pages: int,
    tail_text: str,
) -> Msg:
    """Build the image/forward message shared by read, push and file preview."""
    missing = 0
    if fetch_mode == "download":
        missing = sum(
            1
            for i in range(len(page_urls))
            if not (i < len(local_paths) and local_paths[i])
        )
        if missing:
            # 整链只提示一次，避免逐图 warning 刷屏
            logger.warning(
                f"[{_PLUGIN_NAME}] {missing}/{len(page_urls)} 张图片本地下载缺失，"
                "已回退 URL 直连（带认证的服务器可能无法加载，请检查认证配置）"
            )

    def _img(idx: int) -> Image:
        if fetch_mode == "download" and idx < len(local_paths) and local_paths[idx]:
            return Image(path=local_paths[idx])
        if fetch_mode == "download":
            logger.debug(
                f"[{_PLUGIN_NAME}] 图片 {idx + 1} 本地缺失，回退 URL 直连"
            )
        return Image(url=page_urls[idx])

    if send_mode == "forward" and forward_ok:
        nodes: list[ForwardNode] = []
        if header is not None:
            nodes.append(
                ForwardNode(
                    name=header_node_name or page_label,
                    uin="0",
                    content=[Text(header)],
                )
            )
        for i in range(len(page_urls)):
            nodes.append(
                ForwardNode(
                    name=f"{page_label} - 第 {i + 1} 页",
                    uin="0",
                    content=[_img(i)],
                )
            )
        if total_pages > max_pages:
            nodes.append(
                ForwardNode(name="提示", uin="0", content=[Text(tail_text)])
            )
        return forward_msg(nodes)

    chain: Msg = []
    if header is not None:
        chain.append(Text(header))
    chain.extend(_img(i) for i in range(len(page_urls)))
    if total_pages > max_pages:
        chain.append(Text(tail_text))
    return chain


async def push_chapter_images(
    client: SuwayomiClient,
    config: dict,
    umo: str,
    title: str,
    chapter: Chapter,
    fetch_pages_local_fn: Callable,
    send_fn: SendFn,
):
    ch_label = fmt_chapter_display(chapter)
    # title 来自源站（订阅 KV 同步），进入消息前统一清洗
    title = sanitize_for_message(title, limit=80)
    max_pages = get_config_value(config, "max_pages", 30)
    fetch_mode = get_config_value(config, "image_fetch_mode", "download")
    send_mode = get_config_value(config, "send_mode", "image")

    local_paths: list[str] = []
    tmp_dir: Path | None = None
    try:
        if fetch_mode == "download":
            total_pages, page_urls, local_paths, tmp_dir = await fetch_pages_local_fn(
                chapter.id, max_pages
            )
            if page_urls and not any(local_paths):
                logger.error(
                    f"[{_PLUGIN_NAME}] 自动推送 {ch_label} 时所有图片下载均失败，"
                    "请检查 Suwayomi 认证配置"
                )
                schedule_cleanup(tmp_dir, delay=60)
                return False
        else:
            pages = await client.fetch_chapter_pages(chapter.id)
            if not pages:
                logger.debug(
                    f"[{_PLUGIN_NAME}] 自动推送 {ch_label}：源未返回任何页面"
                )
                return False
            total_pages = len(pages)
            page_urls = [client.build_image_url(p) for p in pages[:max_pages]]
            if client.auth_mode != "none":
                logger.warning(
                    f"[{_PLUGIN_NAME}] 自动推送图片获取方式为 URL 模式，但 Suwayomi "
                    f"开启了 {client.auth_mode} 认证，图片可能无法加载，请改用下载模式"
                )

        if not page_urls:
            logger.debug(
                f"[{_PLUGIN_NAME}] 自动推送 {ch_label}：无可用页面，跳过"
            )
            return False

        msg = build_image_msg(
            page_urls, local_paths, fetch_mode,
            send_mode=send_mode,
            forward_ok=supports_forward(umo),
            page_label=ch_label,
            header=f"📖「{title}」{ch_label}",
            header_node_name=f"「{title}」{ch_label}",
            total_pages=total_pages,
            max_pages=max_pages,
            tail_text=f"... 还有 {total_pages - max_pages} 页，请使用「漫画 阅读」查看",
        )
        try:
            await send_fn(umo, msg)
        except Exception as e:
            logger.warning(
                f"[{_PLUGIN_NAME}] 图片推送到{umo}失败: {e}"
            )
            await send_fn(
                umo,
                text_msg(
                    f"📖「{title}」{ch_label}已更新，"
                    f"但图片发送失败，请使用「漫画 阅读」查看"
                ),
            )
            return False
        return True
    finally:
        schedule_cleanup(tmp_dir, delay=60)


async def push_chapter_file(
    config: dict,
    umo: str,
    title: str,
    chapter: Chapter,
    fetch_pages_local_fn: Callable,
    send_fn: SendFn,
):
    ch_label = fmt_chapter_display(chapter)
    title = sanitize_for_message(title, limit=80)
    fmt = get_config_value(config, "download_format", "pdf")

    # 文件推送意图是整章打包，但仍设页数上限：恶意源可宣告超大页列表
    _, page_urls, local_paths, tmp_dir = await fetch_pages_local_fn(
        chapter.id, get_file_delivery_max_pages(config)
    )
    if not page_urls:
        logger.debug(f"[{_PLUGIN_NAME}] 文件推送 {ch_label}：源未返回任何页面，跳过")
        schedule_cleanup(tmp_dir, delay=120)
        return False

    valid_paths = [p for p in local_paths if p]
    if not valid_paths:
        logger.debug(f"[{_PLUGIN_NAME}] 文件推送 {ch_label}：所有页面下载失败，跳过")
        schedule_cleanup(tmp_dir, delay=120)
        return False

    try:
        file_ext = normalize_pack_format(fmt)
        output_path = build_chapter_output_path(
            Path(valid_paths[0]).parent, title, str(ch_label), file_ext
        )

        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, pack_images, valid_paths, output_path, fmt)
        except Exception as e:
            logger.error(f"[{_PLUGIN_NAME}] 自动推送打包失败: {e}")
            return False

        filename = output_path.name
        try:
            await send_fn(umo, file_msg(output_path, filename))
        except Exception as e:
            logger.warning(f"[{_PLUGIN_NAME}] 文件推送到{umo}失败: {e}")
            await send_fn(
                umo,
                text_msg(
                    f"📖「{title}」{ch_label}已更新，"
                    f"但文件发送失败，请使用「漫画 下载」获取"
                ),
            )
            return False
        return True
    finally:
        schedule_cleanup(tmp_dir, delay=120)
