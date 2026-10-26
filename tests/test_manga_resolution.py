"""Regressions for source title variants and volume/chapter ambiguity."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from plugin_pkg.suwayomi.client import SuwayomiClient, SuwayomiError
from plugin_pkg.suwayomi.models import Chapter, Manga, SearchResult, Source
from plugin_pkg.suwayomi.service import resolve_chapter, resolve_manga


def manga(mid=108, title="樱庄的宠物女友", source="1"):
    return Manga(id=mid, title=title, source_id=source, url="/fixture")


def client(**overrides):
    methods = dict(get_manga=AsyncMock(), search_manga_by_title=AsyncMock(return_value=[]),
                   get_sources=AsyncMock(return_value=[]), search_manga=AsyncMock())
    methods.update(overrides)
    return SimpleNamespace(**methods)


def subscriptions(rows=()):
    return SimpleNamespace(get_subscriptions=AsyncMock(return_value=list(rows)))


@pytest.mark.asyncio
async def test_simplified_title_finds_traditional_cached_title():
    item = manga(129, "櫻莊的寵物女友")
    c = client(search_manga_by_title=AsyncMock(side_effect=lambda q: [item] if q == item.title else []))
    found, error = await resolve_manga(c, subscriptions(), "group", "樱庄的宠物女友", "章节")
    assert found is item and error is None
    c.search_manga.assert_not_awaited()


@pytest.mark.asyncio
async def test_source_title_typo_suggests_subscribed_id_without_selecting_it():
    item = manga()
    c = client(get_manga=AsyncMock(return_value=item))
    found, error = await resolve_manga(c, subscriptions([{"manga_id":108,"title":item.title}]),
                                     "group", "樱花庄的宠物女友", "下载")
    assert found is None and "相近作品" in error
    assert "樱庄的宠物女友" in error and "/漫画 下载 108 1" in error
    c.search_manga.assert_not_awaited()


@pytest.mark.asyncio
async def test_multiple_subscribed_matches_require_id_instead_of_first_subscription():
    items = [manga(1,"作品A"), manga(2,"作品B")]
    c = client(get_manga=AsyncMock(side_effect=lambda mid: items[mid-1]))
    rows = [{"manga_id":m.id,"title":m.title} for m in items]
    found, error = await resolve_manga(c, subscriptions(rows), "group", "作品", "章节")
    assert found is None and "ID 1" in error and "ID 2" in error


@pytest.mark.asyncio
async def test_uncached_exact_title_can_be_found_at_source():
    item = manga(501,"未缓存作品")
    c = client(get_sources=AsyncMock(return_value=[Source(id="1",name="测试源",lang="zh",display_name="测试源")]),
               search_manga=AsyncMock(return_value=SearchResult(mangas=[item],has_next_page=False)))
    found, error = await resolve_manga(c, subscriptions(), "group", item.title, "章节")
    assert found is item and error is None


@pytest.mark.asyncio
async def test_failed_source_search_does_not_claim_work_is_absent():
    c = client(get_sources=AsyncMock(return_value=[Source(id="1",name="测试源",lang="zh",display_name="测试源")]),
               search_manga=AsyncMock(side_effect=SuwayomiError("Connection reset")))
    found, error = await resolve_manga(c, subscriptions(), "group", "未缓存作品", "章节")
    assert found is None and "部分源可能不可用" in error
    assert "/漫画 搜索 未缓存作品" in error


@pytest.mark.asyncio
async def test_numeric_manga_id_is_not_a_search_result_index():
    item = manga()
    c = client(get_manga=AsyncMock(return_value=item))
    found, error = await resolve_manga(c, subscriptions(), "group", "108", "章节")
    assert found is item and error is None
    c.get_manga.assert_awaited_once_with(108)
    c.search_manga_by_title.assert_not_awaited()


def entries():
    return [Chapter(id=359,url="",name="第1卷",chapter_number=1,source_order=1,upload_date=0),
            Chapter(id=367,url="",name="第1话",chapter_number=1,source_order=9,upload_date=0)]


@pytest.mark.parametrize(("query","expected"), [("第1卷",359),("第1话",367),("第1話",367),("ID:359",359)])
def test_explicit_volume_and_chapter_are_distinct(query, expected):
    found, error = resolve_chapter(entries(), query, "108", "下载")
    assert found.id == expected and error is None


def test_ambiguous_plain_number_shows_names_and_individual_copyable_commands():
    found, error = resolve_chapter(entries(), "1", "108", "下载", format_hint="pdf")
    assert found is None
    assert "第1卷（ID:359）" in error and "第1话（ID:367）" in error
    assert "/漫画 下载 108 ID:359 pdf\n" in error
    assert "/漫画 下载 108 ID:367 pdf\n" in error
    assert "ID:359 或" not in error


def test_permission_error_is_actionable_without_java_stack():
    raw = "Exception while fetching data (/fetchChapterPages) : 用户权限不足，请提升用户等级\r\njava.lang.Exception: detail\n\tat coroutine.run(File.kt:1)"
    with pytest.raises(SuwayomiError) as caught:
        SuwayomiClient._response_data(200,{"errors":[{"message":raw}]})
    message = str(caught.value)
    assert "源站" in message and "七七" in message and "登录" in message
    assert "java.lang" not in message and "\n" not in message
    assert caught.value.detail == raw


def test_unknown_backend_error_keeps_bounded_reason_without_stack():
    error = SuwayomiError("Exception while fetching data (/fetchSourceManga) : Connection reset\r\njava.net.SocketException\n\tat worker.run()")
    assert str(error) == "Connection reset"
