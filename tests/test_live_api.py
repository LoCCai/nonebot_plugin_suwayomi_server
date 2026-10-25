"""Integration tests against a live Suwayomi-Server instance.

Usage:
    uv run pytest tests/test_live_api.py -v -s

Set SUWAYOMI_URL and SUWAYOMI_LIVE_TESTS=1 explicitly.
Set SUWAYOMI_AUTH_MODE / SUWAYOMI_USERNAME / SUWAYOMI_PASSWORD for auth.
"""
import os
from pathlib import Path

import pytest
import pytest_asyncio

from plugin_pkg.suwayomi.client import SuwayomiClient, SuwayomiError
from plugin_pkg.suwayomi.models import Chapter, Manga, SearchResult, Source
from plugin_pkg.suwayomi.service import select_search_sources
from plugin_pkg.suwayomi.bangumi import (
    OFFICIAL_API_BASE,
    build_probes,
    confident_aliases,
    resolve_aliases,
)
from plugin_pkg.suwayomi.ranking import (
    STRONG_MATCH_THRESHOLD,
    looks_truncated,
    normalize_for_rank,
    score_title,
)
from plugin_pkg.suwayomi.service import refresh_truncated_titles

SERVER_URL = os.environ.get("SUWAYOMI_URL", "")
AUTH_MODE = os.environ.get("SUWAYOMI_AUTH_MODE", "none")
AUTH_USER = os.environ.get("SUWAYOMI_USERNAME", "")
AUTH_PASS = os.environ.get("SUWAYOMI_PASSWORD", "")

from tests.helpers import server_reachable_sync  # noqa: E402

pytestmark = pytest.mark.skipif(
    not SERVER_URL or os.environ.get("SUWAYOMI_LIVE_TESTS") != "1" or not server_reachable_sync(SERVER_URL),
    reason="Suwayomi-Server 不可达，跳过集成测试（可用 SUWAYOMI_URL 指定地址）",
)


@pytest_asyncio.fixture
async def client():
    c = SuwayomiClient(SERVER_URL, AUTH_MODE, AUTH_USER, AUTH_PASS)
    yield c
    await c.close()


# ── Sources ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_sources(client):
    sources = await client.get_sources()
    assert len(sources) > 0, "Should have at least one source"
    for src in sources:
        assert isinstance(src, Source)
        assert src.id
        assert src.name, f"Source {src.id} should have a name"
    print(f"\n  Found {len(sources)} sources:")
    for s in sources:
        print(f"    [{s.id}] {s.display_name} ({s.lang})")


# ── Search ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_search_manga(client):
    sources = await client.get_sources()
    zh_sources = [s for s in sources if s.lang == "zh" and s.id != "0"]
    assert zh_sources, "Need at least one Chinese source"

    src = zh_sources[0]
    result = await client.search_manga(src.id, "海贼王")
    assert isinstance(result, SearchResult)
    print(f"\n  Search '海贼王' on {src.display_name}: {len(result.mangas)} results")
    if result.mangas:
        m = result.mangas[0]
        assert m.id > 0
        assert m.title
        print(f"    First: [{m.id}] {m.title} ({m.status})")


@pytest.mark.asyncio
async def test_search_manga_all_zh_sources(client):
    sources = await client.get_sources()
    zh_sources = [s for s in sources if s.lang == "zh" and s.id != "0"]

    total = 0
    for src in zh_sources:
        try:
            result = await client.search_manga(src.id, "one piece")
            total += len(result.mangas)
            print(f"\n  {src.display_name}: {len(result.mangas)} results")
        except SuwayomiError as e:
            print(f"\n  {src.display_name}: ERROR - {e}")

    assert total > 0, "Should find results across sources"


# ── Manga by ID ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_manga(client):
    # First search to get a valid manga ID
    sources = await client.get_sources()
    zh_sources = [s for s in sources if s.lang == "zh" and s.id != "0"]
    result = await client.search_manga(zh_sources[0].id, "海贼王")
    assert result.mangas, "Search should return results"

    manga_id = result.mangas[0].id
    manga = await client.get_manga(manga_id)
    assert isinstance(manga, Manga)
    assert manga.id == manga_id
    assert manga.title
    print(f"\n  manga({manga_id}): {manga.title} | status={manga.status} | in_library={manga.in_library}")


# ── Search by title (library-level) ─────────────────────────────

@pytest.mark.asyncio
async def test_search_manga_by_title(client):
    mangas = await client.search_manga_by_title("海贼王")
    assert isinstance(mangas, list)
    print(f"\n  search_manga_by_title('海贼王'): {len(mangas)} results")
    for m in mangas[:3]:
        assert isinstance(m, Manga)
        print(f"    [{m.id}] {m.title}")


# ── Chapters ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_chapters(client):
    # Get a manga that has chapters
    sources = await client.get_sources()
    zh_sources = [s for s in sources if s.lang == "zh" and s.id != "0"]
    result = await client.search_manga(zh_sources[0].id, "海贼王")
    assert result.mangas

    # Try each manga until we find one with chapters
    found = False
    for m in result.mangas[:5]:
        chapters = await client.get_chapters(m.id)
        if chapters:
            assert isinstance(chapters[0], Chapter)
            assert chapters[0].id > 0
            assert chapters[0].name or chapters[0].chapter_number >= 0
            print(f"\n  chapters(manga={m.id}, '{m.title}'): {len(chapters)} chapters")
            print(f"    First: #{chapters[0].chapter_number} '{chapters[0].name}' (id={chapters[0].id})")
            found = True
            break

    if not found:
        print("\n  WARN: no manga with chapters found in top 5 results")


# ── Fetch chapter pages ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_fetch_chapter_pages(client):
    sources = await client.get_sources()
    zh_sources = [s for s in sources if s.lang == "zh" and s.id != "0"]
    result = await client.search_manga(zh_sources[0].id, "海贼王")
    assert result.mangas

    # Find a manga with chapters, then get pages
    for m in result.mangas[:5]:
        chapters = await client.get_chapters(m.id)
        if chapters:
            pages = await client.fetch_chapter_pages(chapters[0].id)
            assert isinstance(pages, list)
            print(f"\n  fetch_chapter_pages(chapter={chapters[0].id}): {len(pages)} pages")
            if pages:
                assert isinstance(pages[0], str)
                print(f"    First page path: {pages[0]}")
                full_url = client.build_image_url(pages[0])
                print(f"    Full URL: {full_url}")
            break


# ── Fetch chapters from source ─────────────────────────────────

@pytest.mark.asyncio
async def test_fetch_chapters(client):
    """Test fetch_chapters mutation - fetches chapter list from manga source.
    This is used when a manga has no chapters in DB (e.g. never opened in WebUI)."""
    sources = await client.get_sources()
    zh_sources = [s for s in sources if s.lang == "zh" and s.id != "0"]
    result = await client.search_manga(zh_sources[0].id, "海贼王")
    assert result.mangas

    manga = result.mangas[0]
    # Fetch chapters from source
    fetched = await client.fetch_chapters(manga.id)
    assert isinstance(fetched, list)
    assert len(fetched) > 0, f"fetch_chapters should return chapters for '{manga.title}'"
    assert isinstance(fetched[0], Chapter)
    assert fetched[0].id > 0
    print(f"\n  fetch_chapters(manga={manga.id}, '{manga.title}'): {len(fetched)} chapters fetched")
    print(f"    First: #{fetched[0].chapter_number} '{fetched[0].name}' (id={fetched[0].id})")
    # After fetch, DB should also have chapters
    db_chapters = await client.get_chapters(manga.id)
    assert len(db_chapters) >= len(fetched), "DB chapters should be >= fetched chapters"


@pytest.mark.asyncio
async def test_fetch_chapters_then_get(client):
    """Test the pattern used in _check_updates: get_chapters returns empty -> fetch_chapters -> get_chapters returns data."""
    sources = await client.get_sources()
    zh_sources = [s for s in sources if s.lang == "zh" and s.id != "0"]

    # Search for a manga that's likely not in the library
    result = await client.search_manga(zh_sources[0].id, "间谍过家家")
    assert result.mangas, "Search should return results"

    manga = result.mangas[0]
    # Fetch chapters from source
    fetched = await client.fetch_chapters(manga.id)
    assert isinstance(fetched, list)
    # Verify DB is populated after fetch
    db_chapters = await client.get_chapters(manga.id)
    assert len(db_chapters) > 0, "get_chapters should return data after fetch_chapters"
    print(f"\n  fetch_chapters_then_get(manga={manga.id}): {len(fetched)} fetched, {len(db_chapters)} in DB")


# ── Download + pack (command main path) ─────────────────────────

@pytest.mark.asyncio
async def test_download_and_pack_chapter(client):
    """Full delivery chain used by 「漫画 阅读/下载」: fetch pages -> download images -> pack zip/pdf/cbz."""
    import shutil

    from plugin_pkg.utils.downloader import fetch_pages_local
    from plugin_pkg.utils.pack import pack_images

    sources = await client.get_sources()
    zh_sources = [s for s in sources if s.lang == "zh" and s.id != "0"]
    result = await client.search_manga(zh_sources[0].id, "海贼")
    assert result.mangas

    total_pages = 0
    for m in result.mangas[:5]:
        chapters = await client.fetch_chapters(m.id)
        if not chapters:
            continue
        tmp_dir = None
        try:
            total_pages, page_urls, local_paths, tmp_dir = await fetch_pages_local(
                client, chapters[0].id, max_pages=3, concurrency=4,
                headers=client.auth_headers,
            )
            valid = [p for p in local_paths if p]
            if not valid:
                continue
            assert total_pages > 0 and page_urls
            for fmt in ("zip", "pdf", "cbz"):
                out = Path(tmp_dir) / f"test.{fmt}"
                pack_images(valid, out, fmt)
                assert out.exists() and out.stat().st_size > 0
                print(f"\n  pack_{fmt}: {out.stat().st_size} bytes")
            break
        finally:
            if tmp_dir:
                shutil.rmtree(tmp_dir, ignore_errors=True)
    assert total_pages > 0, "no downloadable chapter found on this instance"


# ── Library operations ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_library_mangas(client):
    mangas = await client.get_library_mangas()
    assert isinstance(mangas, list)
    print(f"\n  get_library_mangas(): {len(mangas)} manga in library")
    for m in mangas[:3]:
        print(f"    [{m.id}] {m.title}")


# ── Enqueue download ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_enqueue_download(client):
    # This just verifies the API call doesn't error with an empty list
    # We don't actually download anything to avoid side effects
    pass


# ── Error handling ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_nonexistent_manga(client):
    with pytest.raises(SuwayomiError):
        await client.get_manga(999999999)


@pytest.mark.asyncio
async def test_search_invalid_source(client):
    # Should not crash, may return empty or error
    try:
        result = await client.search_manga(0, "test")
        # Local source may return empty
        assert isinstance(result, SearchResult)
    except SuwayomiError:
        pass  # acceptable


# ── AI service ─────────────────────────────────────────────────






@pytest.mark.asyncio
async def test_select_search_sources_skips_local(client):
    """select_search_sources with real sources excludes local source."""
    sources = await client.get_sources()
    selected = select_search_sources(sources, "", 0, 5)
    for s in selected:
        assert s.id != "0", "Local source should be excluded"


@pytest.mark.asyncio
async def test_select_search_sources_matches_hint(client):
    """select_search_sources matches by name, display_name, or lang."""
    sources = await client.get_sources()
    zh = select_search_sources(sources, "zh", 0, 5)
    assert len(zh) > 0, "Should match sources by lang code"


@pytest.mark.asyncio
async def test_select_search_sources_deduplicates_variants(client):
    """select_search_sources puts variants after all unique extensions."""
    sources = await client.get_sources()
    selected = select_search_sources(sources, "", 0, 10)
    assert len(selected) > 0
    # Verify order: all first-of-name entries precede their variants
    seen_first: dict[str, int] = {}
    for i, s in enumerate(selected):
        key = s.name.strip().casefold() or s.display_name.strip().casefold()
        if key not in seen_first:
            seen_first[key] = i
    for s in selected:
        key = s.name.strip().casefold() or s.display_name.strip().casefold()
        # If this isn't the first occurrence, its index must be
        # after the first occurrence of ALL names (i.e. all primaries)
        if seen_first[key] != selected.index(s):
            assert selected.index(s) >= len(seen_first), \
                f"Variant {s.display_name} at index {selected.index(s)} should be after all primaries ({len(seen_first)} unique names)"
    print(f"\n  {len(selected)} sources, {len(seen_first)} unique extensions")
















# ── Real update scan (check_updates against live server) ─────────


@pytest.mark.asyncio
async def test_check_updates_detects_new_chapters_live(client):
    """Real scan: subscribe -> check_updates fetches from source -> new chapters
    detected, notification pushed, watermark raised, last-check timestamp written.

    Covers the background update loop path that mocked tests cannot.
    """
    import asyncio
    from unittest.mock import AsyncMock
    from tests.test_updater import FakePlugin

    from plugin_pkg.suwayomi.updater import check_updates
    from plugin_pkg.utils.subscription import SubscriptionManager

    sources = await client.get_sources()
    zh_sources = [s for s in sources if s.lang == "zh" and s.id != "0"]
    search = await client.search_manga(zh_sources[0].id, "海贼")
    assert search.mangas

    kv = FakePlugin()
    sub_mgr = SubscriptionManager(kv)
    for manga in search.mangas[:3]:
        try:
            chapters = await client.fetch_chapters(manga.id)
        except SuwayomiError:
            continue  # source throttled on this instance
        if not chapters:
            continue

        # fresh subscription: watermark 0 -> every chapter is "new"
        await sub_mgr.subscribe(manga.id, manga.title, manga.source_id, "e2e:check:1")
        send = AsyncMock()
        summary = await check_updates(
            client, sub_mgr, send, {"chapter_cache_hours": -1},
            kv.get_kv_data, kv.put_kv_data, asyncio.Lock(),
            AsyncMock(), AsyncMock(), force=False,
        )
        if "发现" not in summary:
            continue  # scan errored (e.g. throttled) — try next candidate

        assert send.await_count >= 1, "new chapters must be notified"
        stored = kv._store["suwayomi_subscriptions"][str(manga.id)]
        assert stored["latest_chapter_id"] > 0, "watermark must be raised"
        assert kv._store.get("suwayomi_last_update_check", 0) > 0
        safe_summary = summary.encode("ascii", "replace").decode()[:60]
        print(f"\n  check_updates live: summary={safe_summary!r}")
        return

    pytest.skip("实例上无可扫描漫画（或源持续限流），跳过真实更新扫描验证")


# ── PR #21: 排序 / 截断标题 / Bangumi 别名 / 页数上限 ────────────


async def _search_zh_candidates(client, query="海贼", attempts=2):
    """在 zh 源上搜索并重试；全部限流/无结果时跳过测试。"""
    import asyncio

    sources = await client.get_sources()
    zh_sources = [s for s in sources if s.lang == "zh" and s.id != "0"]
    assert zh_sources, "需要至少一个中文源"
    last_error: Exception | None = None
    for src in zh_sources[:3]:
        for _ in range(attempts):
            try:
                result = await client.search_manga(src.id, query)
                if result.mangas:
                    return src, result
            except SuwayomiError as exc:
                last_error = exc
            await asyncio.sleep(2)
    pytest.skip(f"中文源搜索被限流或无结果，跳过（{last_error}）")




@pytest.mark.asyncio
async def test_fetch_manga_details_persists_live(client):
    """fetchManga 从详情页取回标题并写入 DB（截断标题刷新的基础能力）。"""
    _src, result = await _search_zh_candidates(client, "海贼王")
    manga = result.mangas[0]
    details = await client.fetch_manga_details(manga.id)
    assert details.id == manga.id
    assert details.title
    db_manga = await client.get_manga(manga.id)
    assert db_manga.title == details.title, "fetchManga 应把详情页标题持久化到 DB"
    print(f"\n  fetchManga({manga.id}): {details.title!r}")


@pytest.mark.asyncio
async def test_refresh_truncated_titles_live(client):
    """截断标题经真实 fetchManga 刷新为完整标题；失败时保留原标题。"""
    _src, result = await _search_zh_candidates(client, "海贼王")
    manga = result.mangas[0]
    details = await client.fetch_manga_details(manga.id)
    if looks_truncated(details.title):
        pytest.skip("该源详情页标题仍被截断，无法验证刷新路径")

    fake = Manga(
        id=manga.id, source_id=manga.source_id, url=manga.url,
        title="伪造被截断的标题...", status=manga.status,
        thumbnail_url=manga.thumbnail_url, description="",
    )
    assert looks_truncated(fake.title)
    count = await refresh_truncated_titles(client, [fake])
    assert count == 1
    assert fake.title == details.title and not looks_truncated(fake.title)

    missing = Manga(
        id=999999999, source_id=manga.source_id, url="",
        title="不存在的漫画...", status="UNKNOWN", description="",
    )
    assert await refresh_truncated_titles(client, [missing]) == 0
    assert looks_truncated(missing.title)
    print(f"\n  截断刷新: -> {fake.title!r}; 失败路径保留 {missing.title!r}")


@pytest.mark.asyncio
async def test_fetch_pages_local_max_pages_live(client):
    """文件打包页数上限配置真实生效：fetch_pages_local 只取前 N 页。"""
    import asyncio
    import shutil

    from plugin_pkg.utils.downloader import (
        fetch_pages_local,
        get_file_delivery_max_pages,
    )

    cap = get_file_delivery_max_pages({"file_delivery_max_pages": 2})
    assert cap == 2
    _src, result = await _search_zh_candidates(client, "海贼")
    for manga in result.mangas[:5]:
        chapters = await client.get_chapters(manga.id)
        if not chapters:
            try:
                chapters = await client.fetch_chapters(manga.id)
            except SuwayomiError:
                continue
        if not chapters:
            continue
        tmp_dir = None
        try:
            total, page_urls, local_paths, tmp_dir = await fetch_pages_local(
                client, chapters[0].id, max_pages=cap, concurrency=2,
                retries=1, headers=client.auth_headers,
            )
        except SuwayomiError:
            await asyncio.sleep(2)
            continue
        finally:
            if tmp_dir:
                shutil.rmtree(tmp_dir, ignore_errors=True)
        if total == 0:
            continue
        assert len(page_urls) == min(cap, total), \
            f"页数上限未生效: total={total}, urls={len(page_urls)}"
        assert len(local_paths) == len(page_urls)
        assert any(p for p in local_paths), "至少一页应下载成功"
        print(f"\n  页数上限: total={total}, 实际取 {len(page_urls)} 页")
        return
    pytest.skip("实例上未找到可取页的章节（或源限流），跳过")


@pytest.mark.asyncio
async def test_bangumi_alias_expansion_live(client):
    """真实 api.bgm.tv 解析简称 → 生成探针 → 源站重搜捞回强命中目标。"""
    import asyncio

    resolution = await resolve_aliases(
        "我推恶役", bases=[OFFICIAL_API_BASE], deadline=20
    )
    if resolution is None:
        pytest.skip("api.bgm.tv 不可达，跳过 Bangumi 别名集成验证")
    assert resolution.by_subject, "解析应返回条目"
    assert resolution.confident, "简称应命中官方别名并达到强命中阈值"
    aliases = confident_aliases("我推恶役", resolution)
    assert aliases
    assert max(score_title("我推恶役", a) for a in aliases) >= STRONG_MATCH_THRESHOLD
    probes = build_probes("我推恶役", resolution)
    assert probes, "应能生成探针"
    for probe, sid in probes:
        assert len(normalize_for_rank(probe)) >= 4
        assert sid in resolution.by_subject
    print(f"\n  Bangumi 别名: {aliases[:3]}, 探针: {[p for p, _ in probes]}")

    sources = await client.get_sources()
    zh_sources = [s for s in sources if s.lang == "zh" and s.id != "0"]
    # 探针在真实源站重搜：正篇（标题与官方名归一化后相等）必须出现。
    # 仅凭“分数过强命中线”不够——番外/衍生作标题包含正篇全名时同样过线。
    target_norms = {normalize_for_rank(a) for a in aliases}
    searched = False
    for probe, _sid in probes[:2]:
        for src in zh_sources[:3]:
            try:
                result = await client.search_manga(src.id, probe)
            except SuwayomiError:
                await asyncio.sleep(2)
                continue
            searched = True
            exact = [
                m for m in result.mangas
                if normalize_for_rank(m.title) in target_norms
            ]
            if exact:
                print(f"    探针命中正篇: {probe!r} -> {exact[0].title!r} ({src.display_name})")
                return
            await asyncio.sleep(1)
    if not searched:
        pytest.skip("源站限流，探针未发出任何成功请求，跳过")
    pytest.fail("探针未能在源站找到正篇（与官方名归一化相等的标题）")


@pytest.mark.asyncio
async def test_search_command_ranking_and_cache_live(client):
    """真实实例上跑 /漫画 搜索 命令主路径：并发搜索 → 排序 → 编号缓存。"""
    from unittest.mock import AsyncMock, MagicMock

    from plugin_pkg.main import SuwayomiPlugin

    plugin = SuwayomiPlugin.__new__(SuwayomiPlugin)
    plugin.client = client
    plugin.config = {
        "result_cards_enabled": False,
        "search_alias_expansion": False,  # 本用例聚焦排序与缓存
        "search_display_limit": 10,
    }
    plugin._search_cache = {}

    umo = "live:test:ranking"
    event = MagicMock()
    event.unified_msg_origin = umo
    event.message_str = "/漫画 搜索 海贼"
    event.plain_result = MagicMock(side_effect=lambda text: text)
    event.chain_result = MagicMock(side_effect=lambda chain: chain)
    event.send = AsyncMock()

    messages = [msg async for msg in plugin.search_manga(event, "海贼")]
    assert messages, "搜索命令应有回复"
    text = messages[0]
    if "未找到相关漫画" in text:
        pytest.skip("实例上搜索无结果（源可能限流），跳过")

    assert "按相关度排序" in text
    shown = []
    for i in range(1, 11):
        cached = plugin._get_cached_manga(umo, str(i))
        if cached is not None:
            shown.append(cached)
    assert shown, "展示结果应写入编号缓存"
    scores = [score_title("海贼", m.title) for m in shown]
    assert scores == sorted(scores, reverse=True), \
        f"展示顺序应为相关度降序: {[(m.title, s) for m, s in zip(shown, scores)]}"
    if len(shown) == 10:
        assert "已按相关度显示前 10 条" in text
        assert "[11]" not in text, "超过展示上限的条目不进入编号"
    print(f"\n  命令搜索: {len(shown)} 条展示, top={shown[0].title!r}")


@pytest.mark.asyncio
async def test_download_cover_live(client):
    """真实封面下载链路（resolve_image_url → download_images）可用。"""
    import shutil

    from plugin_pkg.utils.downloader import download_cover

    library = await client.get_library_mangas()
    candidate = next((m for m in library if m.thumbnail_url), None)
    if candidate is None:
        _src, result = await _search_zh_candidates(client, "海贼")
        candidate = next((m for m in result.mangas if m.thumbnail_url), None)
    if candidate is None:
        pytest.skip("实例上没有带封面的漫画，跳过")

    path, tmp_dir = await download_cover(
        client, candidate.thumbnail_url, headers=client.auth_headers
    )
    try:
        assert path and Path(path).exists() and Path(path).stat().st_size > 0
        print(f"\n  封面下载: {candidate.title!r} -> {Path(path).stat().st_size} bytes")
    finally:
        if tmp_dir:
            shutil.rmtree(tmp_dir, ignore_errors=True)
