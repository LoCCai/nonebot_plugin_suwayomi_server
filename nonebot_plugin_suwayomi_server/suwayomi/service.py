from __future__ import annotations

import asyncio
import math
import re
import time
from difflib import SequenceMatcher
from typing import TYPE_CHECKING, Any, Callable

import opencc

from nonebot import logger

from . import PLUGIN_NAME
from .config import get_config_value
from .client import SuwayomiError
from .models import Chapter, Manga, Source
from .ranking import looks_truncated, normalize_for_rank, rank_items

if TYPE_CHECKING:
    from ..utils.subscription import SubscriptionManager
    from .client import SuwayomiClient

_PLUGIN_NAME = PLUGIN_NAME

_t2s = opencc.OpenCC("t2s")
_s2t = opencc.OpenCC("s2t")

STATUS_EMOJI: dict[str, str] = {
    "ONGOING": "连载中",
    "COMPLETED": "已完结",
    "LICENSED": "已授权",
    "PUBLISHING_FINISHED": "已完结",
    "CANCELLED": "已停刊",
    "ON_HIATUS": "休刊中",
    "UNKNOWN": "未知",
}

KV_CHAPTER_TS = "suwayomi_chapter_timestamps"

_ts_lock = asyncio.Lock()

_CHAPTER_NUM_SUFFIX_RE = re.compile(r"(?:话|話|章|卷|册|冊)$")
_CHAPTER_UNIT_RE = re.compile(r"第?\s*\d+(?:\.\d+)?\s*(话|話|章|卷|册|冊)")


def _chapter_unit(text: str) -> str:
    match = _CHAPTER_UNIT_RE.search(str(text or ""))
    if not match:
        return ""
    return "volume" if match[1] in {"卷", "册", "冊"} else "chapter"


def parse_chapter_number_text(text: str) -> float | None:
    """Parse user input like '5', '第5话', '第38.5話' into a chapter number."""
    cleaned = str(text or "").strip()
    if cleaned.startswith("第"):
        cleaned = cleaned[1:]
    cleaned = _CHAPTER_NUM_SUFFIX_RE.sub("", cleaned).strip()
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def normalize_zh(text: str) -> str:
    return _t2s.convert(text)


def fmt_chapter_num(num: float) -> int | float | str:
    try:
        if math.isnan(num) or math.isinf(num):
            return "?"
        return int(num) if num == int(num) else num
    except (ValueError, OverflowError):
        return "?"


# 控制字符/行分隔符/RTL 方向控制符 → 替换为空格；再加零宽字符（ZWSP/BOM）删除
_CONTROL_CHARS_RE = re.compile(
    "[\r\n\t\x0b\x0c\x1b\x85\u2028\u2029"
    + chr(0x202A) + "-" + chr(0x202E)
    + chr(0x2066) + "-" + chr(0x2069) + "]+"
)
_INVISIBLE_CHARS_RE = re.compile("[\u200b\ufeff]+")


def sanitize_for_message(text: str, limit: int = 50) -> str:
    """第三方文本（源站标题/章节名/来源名、Bangumi 别名）进入消息前的最小清洗。

    剥离换行/制表/垂直制表/换页/ESC/NEL/行分隔/RTL 控制符（防止伪造
    系统提示行与终端转义序列），删除零宽空格与 BOM，限长防爆屏。
    """
    cleaned = _CONTROL_CHARS_RE.sub(" ", str(text or ""))
    cleaned = _INVISIBLE_CHARS_RE.sub("", cleaned)
    return cleaned.strip()[:limit]


def fmt_chapter_display(ch: Chapter) -> str:
    """Return the human-readable chapter name for display in messages.
    Uses chapter.name if non-empty, otherwise falls back to 第X话.
    """
    name = (ch.name or "").strip()
    if name:
        return sanitize_for_message(name, limit=80)
    return f"第{fmt_chapter_num(ch.chapter_number)}话"


def fmt_chapter_label(ch: Chapter, num_counts: dict[float, int]) -> str:
    num = fmt_chapter_num(ch.chapter_number)
    dup_tag = f" (ID:{ch.id})" if num_counts.get(ch.chapter_number, 0) > 1 else ""
    if ch.name:
        return f"#{num} {sanitize_for_message(ch.name, limit=80)}{dup_tag}"
    return f"#{num}{dup_tag}"


def fmt_delivery_failure_message(total_pages: int, fetch_mode: str, auth_mode: str) -> str:
    """Explain why chapter delivery failed, distinguishing real causes."""
    if total_pages <= 0:
        return "该章节暂无可用页面。"
    if fetch_mode == "download":
        if auth_mode and auth_mode != "none":
            return (
                f"所有 {total_pages} 页图片下载失败。当前 Suwayomi 开启了 "
                f"{auth_mode} 认证，请检查认证用户名/密码是否正确。"
            )
        return (
            f"所有 {total_pages} 页图片下载失败，"
            "请检查 Suwayomi 服务是否正常运行，或尝试更换图片获取方式。"
        )
    if auth_mode and auth_mode != "none":
        return "图片 URL 模式不兼容带认证的 Suwayomi 服务器，请改用下载模式。"
    return "图片加载失败，请稍后重试。"


def find_chapters_by_num(chapters: list[Chapter], chapter_num_f: float) -> list[Chapter]:
    return [ch for ch in chapters if abs(ch.chapter_number - chapter_num_f) < 0.01]


def find_chapter_by_id(chapters: list[Chapter], chapter_id: int) -> Chapter | None:
    for ch in chapters:
        if ch.id == chapter_id:
            return ch
    return None


def resolve_chapter(
    chapters: list[Chapter], chapter_num: str, manga_name_or_id: str, cmd: str,
    format_hint: str = "",
) -> tuple[Chapter | None, str | None]:
    norm = chapter_num.replace("：", ":").lower()
    if norm.startswith("id:"):
        try:
            cid = int(norm[3:])
        except ValueError:
            return None, "章节 ID 格式无效。示例: ID:123"
        target = find_chapter_by_id(chapters, cid)
        if target:
            return target, None
        return None, f"未找到 ID 为 {cid} 的章节。"
    chapter_num_f = parse_chapter_number_text(chapter_num)
    if chapter_num_f is None:
        return None, "章节号无效。示例: 1, 38.5, 第5话 或 ID:123"
    matches = find_chapters_by_num(chapters, chapter_num_f)
    unit = _chapter_unit(chapter_num)
    if unit and any(_chapter_unit(ch.name) for ch in matches):
        matches = [ch for ch in matches if _chapter_unit(ch.name) == unit]
    if len(matches) == 0:
        return None, f"未找到第 {fmt_chapter_num(chapter_num_f)} 话。"
    if len(matches) == 1:
        return matches[0], None
    suffix = f" {format_hint}" if format_hint in {"pdf", "zip", "cbz"} else ""
    safe_name = sanitize_for_message(manga_name_or_id, limit=100)
    lines = [f"编号 {fmt_chapter_num(chapter_num_f)} 对应多个条目，请选择卷或话："]
    for chapter in matches[:10]:
        lines.append(f"{fmt_chapter_display(chapter)}（ID:{chapter.id}）\n/漫画 {cmd} {safe_name} ID:{chapter.id}{suffix}")
    if len(matches) > 10:
        lines.append(f"还有 {len(matches) - 10} 项，请用 /漫画 章节 {safe_name} 查看完整目录。")
    lines.append("也可直接指定第1话、第1卷等；ID:数字是章节 ID，纯数字是章节号。")
    return None, "\n".join(lines)


def split_search_query(message_str: str, keyword: str) -> tuple[str, str]:
    """Split a search command message into (keyword, source_hint).

    AstrBot's command handler splits arguments by spaces, so a trailing source
    name like "/漫画 搜索 安达与岛村 再漫画" is lost from the keyword param.
    Parse the full message instead, falling back to the keyword param.
    """
    raw = (message_str or "").strip()
    tokens = raw.split()
    args = ""
    try:
        idx = tokens.index("搜索")
        args = " ".join(tokens[idx + 1:]).strip()
    except ValueError:
        pass
    if not args:
        args = (keyword or "").strip()

    words = args.rsplit(" ", 1)
    if len(words) == 2:
        return words[0].strip(), words[1].strip()
    return args, ""


def match_source_hint(sources: list[Source], hint: str) -> Source | None:
    """Match a user-provided source hint to a source.

    Matches when the hint is a prefix of the source's name or display name,
    or equals its language code. The local source (id "0") is excluded — it
    crashes searches. A hint that does not match any source is treated as part
    of the manga keyword by the caller.
    """
    hint = (hint or "").strip().casefold()
    if not hint:
        return None
    for src in sources:
        if str(src.id) == "0":
            continue
        display_core = src.display_name.split(" (")[0].casefold()
        if (
            hint == src.name.casefold()
            or src.name.casefold().startswith(hint)
            or hint == display_core
            or display_core.startswith(hint)
            or hint == src.lang.casefold()
        ):
            return src
    return None


def _bounded_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(parsed, maximum))


def select_search_sources(
    sources: list[Source],
    source_hint: str,
    default_source_id: Any,
    max_sources: int,
) -> list[Source]:
    usable = [source for source in sources if str(source.id) != "0"]
    limit = _bounded_int(max_sources, 5, 1, 10)

    hint = source_hint.strip().casefold()
    if hint:
        matches = [
            source
            for source in usable
            if hint in source.name.casefold()
            or hint in source.display_name.casefold()
            or hint == source.lang.casefold()
        ]
        return matches[:limit]

    default_sid = str(default_source_id or "0")
    if default_sid != "0":
        matches = [source for source in usable if str(source.id) == default_sid]
        if matches:
            return matches

    # Prefer different extensions before additional language variants of the
    # same extension, so a small limit is not monopolized by MangaDex variants.
    primary: list[Source] = []
    variants: list[Source] = []
    seen_names: set[str] = set()
    for source in usable:
        key = source.name.strip().casefold() or source.display_name.strip().casefold()
        if key in seen_names:
            variants.append(source)
            continue
        seen_names.add(key)
        primary.append(source)
    return (primary + variants)[:limit]


def ttl_cache_store(
    cache: dict[str, tuple[float, Any]],
    key: str,
    value: Any,
    max_entries: int,
    now: float | None = None,
) -> None:
    """Store an entry; evict the oldest entry when over the cap.

    TTL is enforced on lookup via `ttl_cache_lookup`, not at write time.
    """
    cache[key] = (now if now is not None else time.time(), value)
    if len(cache) > max_entries:
        oldest = min(cache, key=lambda k: cache[k][0])
        del cache[oldest]


def ttl_cache_lookup(
    cache: dict[str, tuple[float, Any]],
    key: str,
    ttl: float,
    now: float | None = None,
) -> Any:
    """Return a non-expired entry or None (expired entries are removed)."""
    entry = cache.get(key)
    if entry is None:
        return None
    ts, value = entry
    if (now if now is not None else time.time()) - ts > ttl:
        cache.pop(key, None)
        return None
    return value


async def get_chapter_timestamp(
    get_kv_data: Callable, manga_id: int
) -> float:
    async with _ts_lock:
        data = await get_kv_data(KV_CHAPTER_TS, {})
        return data.get(str(manga_id), 0)


async def set_chapter_timestamp(
    get_kv_data: Callable,
    put_kv_data: Callable,
    manga_id: int,
):
    async with _ts_lock:
        data = await get_kv_data(KV_CHAPTER_TS, {})
        data[str(manga_id)] = time.time()
        await put_kv_data(KV_CHAPTER_TS, data)


async def get_or_fetch_chapters(
    client: SuwayomiClient,
    get_kv_data: Callable,
    put_kv_data: Callable,
    config: dict,
    manga_id: int,
    force: bool = False,
) -> list[Chapter] | None:
    cache_hours = get_config_value(config, "chapter_cache_hours", 6)
    if cache_hours < -1:
        cache_hours = 0

    logger.debug(
        f"[{_PLUGIN_NAME}] get_or_fetch_chapters(manga_id={manga_id}, "
        f"force={force}, cache_hours={cache_hours})"
    )

    if cache_hours == -1:
        try:
            chapters = await client.fetch_chapters(manga_id)
            if chapters:
                await set_chapter_timestamp(get_kv_data, put_kv_data, manga_id)
            logger.debug(
                f"[{_PLUGIN_NAME}] 从源拉取章节(always): manga_id={manga_id}, "
                f"{len(chapters) if chapters else 0} 章节"
            )
            return chapters
        except SuwayomiError:
            logger.debug(
                f"[{_PLUGIN_NAME}] 源拉取失败，回退DB: manga_id={manga_id}"
            )
            return await client.get_chapters(manga_id)

    should_fetch = force

    if not should_fetch and cache_hours > 0:
        last_ts = await get_chapter_timestamp(get_kv_data, manga_id)
        if last_ts == 0 or (time.time() - last_ts) > cache_hours * 3600:
            should_fetch = True
            logger.debug(
                f"[{_PLUGIN_NAME}] 缓存过期或无记录: manga_id={manga_id}, "
                f"last_ts={last_ts}"
            )

    if not should_fetch:
        chapters = await client.get_chapters(manga_id)
        if chapters:
            logger.debug(
                f"[{_PLUGIN_NAME}] 缓存命中: manga_id={manga_id}, "
                f"{len(chapters)} 章节"
            )
            return chapters
        should_fetch = True
        logger.debug(
            f"[{_PLUGIN_NAME}] DB为空，触发源拉取: manga_id={manga_id}"
        )

    chapters = await client.fetch_chapters(manga_id)
    if chapters:
        await set_chapter_timestamp(get_kv_data, put_kv_data, manga_id)
    logger.debug(
        f"[{_PLUGIN_NAME}] 从源拉取章节: manga_id={manga_id}, "
        f"{len(chapters) if chapters else 0} 章节"
    )
    return chapters


async def resolve_manga(
    client: SuwayomiClient,
    sub_mgr: SubscriptionManager,
    umo: str,
    name_or_id: str,
    cmd: str,
) -> tuple[Manga | None, str | None]:
    try:
        manga_id = int(name_or_id)
        manga = await client.get_manga(manga_id)
        return manga, None
    except (ValueError, SuwayomiError):
        pass

    norm_input = normalize_for_rank(name_or_id)
    if not norm_input:
        return None, "请提供漫画名称或漫画 ID。"
    subs = await sub_mgr.get_subscriptions(umo)
    try:
        candidates: dict[int, Manga] = {}
        for sub in subs:
            title = normalize_for_rank(sub["title"])
            similarity = SequenceMatcher(None, norm_input, title).ratio()
            if norm_input in title or (min(len(norm_input), len(title)) >= 4 and similarity >= 0.8):
                try:
                    manga = await client.get_manga(sub["manga_id"])
                    candidates[manga.id] = manga
                except SuwayomiError:
                    continue

        # Query normalized spellings too: backend title includes is literal and
        # otherwise misses simplified queries against traditional source titles.
        for title in dict.fromkeys([name_or_id, normalize_zh(name_or_id), _s2t.convert(name_or_id)]):
            for manga in await client.search_manga_by_title(title):
                candidates[manga.id] = manga
        mangas = list(candidates.values())
        exact = [m for m in mangas if normalize_for_rank(m.title) == norm_input]
        contained = [m for m in mangas if norm_input in normalize_for_rank(m.title)]
        matches = exact or contained
        if len(matches) == 1:
            return matches[0], None
        if matches:
            mangas = matches
        elif not mangas:
            # The backend cache is not the source catalog. Search a bounded
            # set of installed sources before declaring that no title matches.
            sources = select_search_sources(await client.get_sources(), "", 0, max_sources=5)

            async def search_source(source: Source) -> list[Manga]:
                try:
                    result = await asyncio.wait_for(client.search_manga(source.id, name_or_id), timeout=12)
                    return result.mangas
                except (SuwayomiError, asyncio.TimeoutError):
                    return []

            for batch in await asyncio.gather(*(search_source(source) for source in sources)):
                for manga in batch:
                    title = normalize_for_rank(manga.title)
                    if norm_input in title or SequenceMatcher(None, norm_input, title).ratio() >= 0.8:
                        candidates[manga.id] = manga
            mangas = list(candidates.values())
            exact = [m for m in mangas if normalize_for_rank(m.title) == norm_input]
            if len(exact) == 1:
                return exact[0], None
            if exact:
                mangas = exact
        if not mangas:
            safe_name = sanitize_for_message(name_or_id, limit=100)
            return None, (f"暂未找到匹配的作品，部分源可能不可用。请先搜索并使用结果中的漫画 ID：\n"
                          f"/漫画 搜索 {safe_name}\n例如 /漫画 {cmd} <漫画ID>" + (" 1" if cmd in {"下载", "阅读"} else ""))

        src_map: dict[str, str] = {}
        try:
            sources = await client.get_sources()
            src_map = {
                str(s.id): sanitize_for_message(s.display_name, limit=40)
                for s in sources
            }
        except Exception as exc:
            logger.debug(f"[{_PLUGIN_NAME}] 多结果引导获取源名失败: {exc}")

        heading = "找到多个结果" if matches or exact else "名称可能与源站不同，找到以下相近作品"
        lines = [heading + "，请核对漫画 ID："]
        for m in mangas[:10]:
            status = STATUS_EMOJI.get(m.status, "未知")
            src_name = src_map.get(str(m.source_id), f"源{m.source_id}")
            lines.append(
                f"  ID {m.id}: {sanitize_for_message(m.title, limit=80)} [{status}] ({src_name})"
            )
            suffix = " 1" if cmd in {"下载", "阅读"} else ""
            lines.append(f"/漫画 {cmd} {m.id}{suffix}")
        return None, "\n".join(lines)
    except Exception as e:
        logger.error(f"[{_PLUGIN_NAME}] resolve_manga error: {e}")
        return None, "查找漫画失败。"


async def refresh_truncated_titles(
    client: SuwayomiClient,
    mangas: list[Manga],
    limit: int = 5,
    timeout: float = 10.0,
) -> int:
    """并发刷新疑似被源站截断的标题（原地替换 manga.title），返回刷新成功数。

    只处理以 .. / 。。 / … 结尾的标题；刷新失败或结果仍疑似截断则保留原标题，
    后续排序走反向包含兜底。
    """
    targets = [m for m in mangas if looks_truncated(m.title)][: max(0, limit)]
    if not targets:
        return 0

    async def _refresh(manga: Manga) -> bool:
        try:
            fresh = await asyncio.wait_for(
                client.fetch_manga_details(manga.id), timeout
            )
        except Exception as exc:
            logger.debug(
                f"[{_PLUGIN_NAME}] 截断标题刷新失败(manga={manga.id}): {exc}"
            )
            return False
        if fresh and fresh.title and not looks_truncated(fresh.title):
            manga.title = fresh.title
            return True
        return False

    results = await asyncio.gather(*(_refresh(m) for m in targets))
    refreshed = sum(1 for ok in results if ok)
    logger.debug(
        f"[{_PLUGIN_NAME}] 截断标题刷新: 尝试 {len(targets)} 条, 成功 {refreshed} 条"
    )
    return refreshed


async def search_best_match(
    client: SuwayomiClient,
    config: dict,
    name: str,
    source_filter: Source | None = None,
) -> tuple[Manga | None, str | None]:
    sources = await client.get_sources()
    if not sources:
        return None, "未找到已安装的漫画源"

    if source_filter:
        target_sources = [source_filter]
    else:
        # Same selection as the AI tool path: skip the local source and prefer
        # distinct extensions before language variants (MangaDex has 60+).
        target_sources = select_search_sources(
            sources,
            "",
            get_config_value(config, "default_source_id", 0),
            max_sources=3,
        )

    for src in target_sources:
        try:
            result = await client.search_manga(src.id, name)
        except Exception as e:
            logger.warning(
                f"[{_PLUGIN_NAME}] 批量订阅搜索源 {src.name} 失败: {e}"
            )
            continue
        if result.mangas:
            # 源内按标题相关度选优，不再盲取第一条
            ranked, _ = rank_items(name, result.mangas, title_of=lambda m: m.title)
            return ranked[0], None

    return None, "未找到匹配结果"
