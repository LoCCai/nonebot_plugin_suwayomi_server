"""Command-level card regression tests (no network)."""
from unittest.mock import AsyncMock, MagicMock

import pytest
from plugin_pkg.core import SuwayomiCore
from tests.fake_ctx import FakeCtx
from plugin_pkg.suwayomi.cards import CardCache
from plugin_pkg.suwayomi.models import Manga, SearchResult, Source


def _plugin(cards_enabled=True):
    plugin = SuwayomiCore.__new__(SuwayomiCore)
    plugin.client = MagicMock()
    plugin.client.auth_headers = {}
    plugin.sub_mgr = MagicMock()
    plugin.config = {
        "chapter_list_show_cover": True,
        "temp_dir": "",
        "download_retries": 3,
        "result_cards_enabled": cards_enabled,
        "card_render_timeout_sec": 30,
        # 单测禁用 Bangumi 扩展（避免真实网络请求）
        "search_alias_expansion": False,
    }
    plugin.get_kv_data = AsyncMock(return_value={})
    plugin.put_kv_data = AsyncMock()
    plugin._search_cache = {}
    plugin._card_cache = CardCache(ttl=600)
    plugin._card_cooldown_until = 0.0
    plugin.html_render = AsyncMock(return_value="/tmp/card.jpg")
    return plugin

def _event():
    return FakeCtx(
        message_str="/漫画 搜索 咒术回战",
        session_key="aiocqhttp:group:g1",
    )


def _manga(title, mid):
    return Manga(id=mid, source_id=2, url="", title=title,
                 status="ONGOING", thumbnail_url=f"/api/v1/manga/{mid}/thumbnail",
                 description="测试漫画简介")


def _source(name="拷贝漫画"):
    return Source(id="2", name="manga", lang="zh", display_name=name)


def _plugin_with_search(plugin, mangas):
    src = _source()
    plugin.client.get_sources = AsyncMock(return_value=[src])
    plugin.client.search_manga = AsyncMock(
        return_value=SearchResult(mangas=mangas, has_next_page=False)
    )


@pytest.mark.asyncio
async def test_search_cards_disabled_keeps_plain_text(monkeypatch):
    plugin = _plugin(cards_enabled=False)
    _plugin_with_search(plugin, [_manga("咒术回战", 1)])
    event = _event()
    monkeypatch.setattr("plugin_pkg.core.SuwayomiClient", MagicMock())

    await plugin.search_manga(event, "咒术回战")
    results = event.sent
    assert len(results) == 1
    assert "搜索结果" in results[0]
    event.plain_result.assert_called_once()
    event.chain_result.assert_not_called()


@pytest.mark.asyncio
async def test_search_cards_render_failure_falls_back_to_text(monkeypatch):
    plugin = _plugin(cards_enabled=True)
    _plugin_with_search(plugin, [_manga("咒术回战", 1)])
    event = _event()
    plugin._render_card_result = AsyncMock(return_value=None)

    await plugin.search_manga(event, "咒术回战")
    results = event.sent
    assert len(results) == 1
    assert "搜索结果" in results[0]
    event.plain_result.assert_called_once()
    event.chain_result.assert_not_called()


@pytest.mark.asyncio
async def test_search_cards_success_sends_image(monkeypatch):
    plugin = _plugin(cards_enabled=True)
    _plugin_with_search(plugin, [_manga("咒术回战", 1)])
    event = _event()
    plugin._render_card_result = AsyncMock(return_value="/tmp/card.jpg")
    monkeypatch.setattr(
        "plugin_pkg.core.embed_covers",
        AsyncMock(side_effect=lambda c, items, **kw: items),
    )

    await plugin.search_manga(event, "咒术回战")
    results = event.sent
    assert len(results) == 1
    event.chain_result.assert_called_once()
    event.plain_result.assert_not_called()
    chain = event.chain_result.call_args[0][0]
    assert len(chain) == 1


@pytest.mark.asyncio
async def test_subscribe_confirm_card_success(monkeypatch):
    plugin = _plugin(cards_enabled=True)
    manga = _manga("咒术回战", 1)
    plugin._get_cached_manga = MagicMock(return_value=manga)
    plugin.sub_mgr.subscribe = AsyncMock()
    plugin.sub_mgr.update_latest_chapter = AsyncMock()
    monkeypatch.setattr(
        "plugin_pkg.core.get_or_fetch_chapters", AsyncMock(return_value=[])
    )
    plugin._render_card_result = AsyncMock(return_value="/tmp/confirm.jpg")
    monkeypatch.setattr(
        "plugin_pkg.core.embed_covers",
        AsyncMock(
            side_effect=lambda c, items, **kw: [
                dict(items[0], cover_data_url="data:image/jpeg;base64,AA==")
            ]
        ),
    )
    event = _event()

    await plugin.subscribe_manga(event, "1")
    results = event.sent
    assert len(results) == 1
    event.chain_result.assert_called_once()
    event.plain_result.assert_not_called()
    card = plugin._render_card_result.call_args[0][0]
    assert card["synopsis"] == "测试漫画简介"


@pytest.mark.asyncio
async def test_subscribe_confirm_render_failure_text(monkeypatch):
    plugin = _plugin(cards_enabled=True)
    manga = _manga("咒术回战", 1)
    plugin._get_cached_manga = MagicMock(return_value=manga)
    plugin.sub_mgr.subscribe = AsyncMock()
    plugin.sub_mgr.update_latest_chapter = AsyncMock()
    monkeypatch.setattr(
        "plugin_pkg.core.get_or_fetch_chapters", AsyncMock(return_value=[])
    )
    plugin._render_card_result = AsyncMock(return_value=None)
    event = _event()

    await plugin.subscribe_manga(event, "1")
    results = event.sent
    assert len(results) == 1
    assert "已订阅" in results[0]
    event.plain_result.assert_called_once()
    event.chain_result.assert_not_called()


@pytest.mark.asyncio
async def test_batch_subscribe_card_success(monkeypatch):
    plugin = _plugin(cards_enabled=True)
    plugin.client.get_sources = AsyncMock(return_value=[_source()])
    plugin.sub_mgr.get_subscriptions = AsyncMock(return_value=[])
    plugin.sub_mgr.subscribe = AsyncMock()
    plugin.sub_mgr.update_latest_chapter = AsyncMock()
    plugin._render_card_result = AsyncMock(return_value="/tmp/batch.jpg")
    monkeypatch.setattr(
        "plugin_pkg.core.search_best_match",
        AsyncMock(return_value=(_manga("咒术回战", 1), None)),
    )
    monkeypatch.setattr(
        "plugin_pkg.core.get_or_fetch_chapters", AsyncMock(return_value=[])
    )
    monkeypatch.setattr(
        "plugin_pkg.core.embed_covers",
        AsyncMock(side_effect=lambda c, items, **kw: [dict(i, cover_data_url="x") for i in items]),
    )
    event = _event()
    event.message_str = "漫画 批量订阅 咒术回战"

    await plugin.batch_subscribe(event)
    assert event.chain_result.call_count >= 1


@pytest.mark.asyncio
async def test_my_subscriptions_card_success(monkeypatch):
    plugin = _plugin(cards_enabled=True)
    plugin.sub_mgr.get_subscriptions = AsyncMock(return_value=[
        {"manga_id": 1, "title": "咒术回战", "source_id": 2, "latest_chapter_id": 5,
         "push_enabled": True},
    ])
    plugin.client.get_sources = AsyncMock(return_value=[_source()])
    plugin.client.get_manga = AsyncMock(return_value=_manga("咒术回战", 1))
    plugin._render_card_result = AsyncMock(return_value="/tmp/subs.jpg")
    monkeypatch.setattr(
        "plugin_pkg.core.embed_covers",
        AsyncMock(side_effect=lambda c, items, **kw: [dict(i, cover_data_url="x") for i in items]),
    )
    event = _event()

    await plugin.my_subscriptions(event)
    results = event.sent
    assert len(results) == 1
    event.chain_result.assert_called_once()
    event.plain_result.assert_not_called()


@pytest.mark.asyncio
async def test_my_subscriptions_card_render_failure_text(monkeypatch):
    plugin = _plugin(cards_enabled=True)
    plugin.sub_mgr.get_subscriptions = AsyncMock(return_value=[
        {"manga_id": 1, "title": "咒术回战", "source_id": 2, "latest_chapter_id": 5,
         "push_enabled": True},
    ])
    plugin.client.get_sources = AsyncMock(return_value=[_source()])
    plugin.client.get_manga = AsyncMock(return_value=_manga("咒术回战", 1))
    plugin._render_card_result = AsyncMock(return_value=None)
    event = _event()

    await plugin.my_subscriptions(event)
    results = event.sent
    assert len(results) == 1
    assert "订阅列表" in results[0]
    event.plain_result.assert_called_once()
    event.chain_result.assert_not_called()


@pytest.mark.asyncio
async def test_card_render_failure_enters_cooldown(monkeypatch):
    plugin = _plugin(cards_enabled=True)
    _plugin_with_search(plugin, [_manga("咒术回战", 1)])
    event = _event()
    monkeypatch.setattr(
        "plugin_pkg.core.render_card_cached", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        "plugin_pkg.core.embed_covers",
        AsyncMock(side_effect=lambda c, items, **kw: items),
    )

    await plugin.search_manga(event, "咒术回战")
    # 渲染失败进入冷却：后续命令直接回退文本，不再尝试渲染
    assert plugin._result_cards_enabled() is False
    event.plain_result.reset_mock()
    await plugin.search_manga(event, "咒术回战")
    event.plain_result.assert_called_once()

    # 冷却恢复后重新尝试
    plugin._card_cooldown_until = 0.0
    assert plugin._result_cards_enabled() is True


def test_card_render_fn_defaults_to_builtin_render(monkeypatch):
    plugin = _plugin(cards_enabled=True)
    fake = MagicMock(return_value="system-render")
    monkeypatch.setattr("plugin_pkg.core.get_renderer", fake)

    assert plugin._card_render_fn() == "system-render"
    fake.assert_called_once_with("system", "", None)


def test_card_render_fn_ignores_endpoint_under_system_source(monkeypatch):
    """A leftover endpoint must not leak into the system path."""
    import plugin_pkg.renderers as renderers_mod

    plugin = _plugin(cards_enabled=True)
    plugin.config["t2i_source"] = "system"
    plugin.config["t2i_endpoint"] = "http://leftover:8999"

    def unexpected(*args, **kwargs):
        raise AssertionError("system 来源下不应创建独立渲染器")

    monkeypatch.setattr(renderers_mod, "make_endpoint_renderer", unexpected)
    monkeypatch.setattr(
        renderers_mod, "make_system_renderer", MagicMock(return_value="system-render")
    )
    renderers_mod.reset_renderer_cache()

    assert plugin._card_render_fn() == "system-render"


@pytest.mark.asyncio
async def test_dead_custom_endpoint_falls_back_to_text_not_system_renderer(monkeypatch):
    """Privacy contract: a failing custom endpoint must degrade to plain text.

    Quietly switching to AstrBot's system T2I would send the user's card HTML
    to a service they explicitly chose not to use, so the system renderer has
    to stay untouched and the cooldown must engage.
    """
    plugin = _plugin(cards_enabled=True)
    plugin.config["t2i_source"] = "custom"
    plugin.config["t2i_endpoint"] = "http://127.0.0.1:1"  # nothing listening
    plugin.config["card_render_timeout_sec"] = 5
    plugin.html_render = AsyncMock(return_value="/tmp/SYSTEM.jpg")

    path = await plugin._render_card_result(
        {"card_type": "search", "rows": [], "subtitle": "s", "footer": "f"}
    )

    assert path is None
    plugin.html_render.assert_not_called()
    assert plugin._result_cards_enabled() is False  # cooldown engaged


def test_card_render_fn_custom_uses_configured_endpoint(monkeypatch):
    import plugin_pkg.renderers as renderers_mod

    plugin = _plugin(cards_enabled=True)
    plugin.config["t2i_source"] = "custom"
    plugin.config["t2i_endpoint"] = "  http://t2i.local:9105  "
    fake_factory = MagicMock(return_value="custom-render-fn")
    monkeypatch.setattr(renderers_mod, "make_endpoint_renderer", fake_factory)
    renderers_mod.reset_renderer_cache()

    render_fn = plugin._card_render_fn()

    assert render_fn == "custom-render-fn"
    # 传入工厂前归一化：去空白并补 /text2img（与服务端路径规则一致）
    fake_factory.assert_called_once_with("http://t2i.local:9105/text2img", temp_dir=None)


def test_card_render_fn_custom_empty_endpoint_falls_back_to_system(monkeypatch):
    import plugin_pkg.renderers as renderers_mod

    plugin = _plugin(cards_enabled=True)
    plugin.config["t2i_source"] = "custom"
    plugin.config["t2i_endpoint"] = ""

    def unexpected(*args, **kwargs):
        raise AssertionError("端点为空时不应创建独立渲染器")

    monkeypatch.setattr(renderers_mod, "make_endpoint_renderer", unexpected)
    monkeypatch.setattr(
        renderers_mod, "make_system_renderer", MagicMock(return_value="system-render")
    )
    renderers_mod.reset_renderer_cache()

    assert plugin._card_render_fn() == "system-render"


def test_card_render_fn_custom_empty_endpoint_warns_once(monkeypatch):
    """Empty endpoint warns once per config load, not on every command."""
    import plugin_pkg.core as core_mod

    plugin = _plugin(cards_enabled=True)
    plugin.config["t2i_source"] = "custom"
    plugin.config["t2i_endpoint"] = ""
    warning = MagicMock()
    monkeypatch.setattr(core_mod.logger, "warning", warning)

    assert plugin._card_render_fn() is plugin._card_render_fn()
    assert warning.call_count == 1
    assert "端点为空" in warning.call_args[0][0]

    # WebUI 保存配置后（rebuild_client）允许再次告警
    plugin._t2i_endpoint_warned = False
    plugin._card_render_fn()
    assert warning.call_count == 2


@pytest.mark.asyncio
async def test_render_card_result_uses_configured_render_fn(monkeypatch):
    plugin = _plugin(cards_enabled=True)
    sentinel = MagicMock()
    plugin._card_render_fn = MagicMock(return_value=sentinel)
    captured = {}

    async def fake_render_cached(cache, html_render, tmpldata, timeout):
        captured["fn"] = html_render
        return "/tmp/card.jpg"

    monkeypatch.setattr("plugin_pkg.core.render_card_cached", fake_render_cached)
    monkeypatch.setattr("plugin_pkg.core.schedule_cleanup_file", MagicMock())

    path = await plugin._render_card_result({"card_type": "search"})

    assert path == "/tmp/card.jpg"
    assert captured["fn"] is sentinel


@pytest.mark.asyncio
async def test_card_render_success_clears_cooldown(monkeypatch):
    plugin = _plugin(cards_enabled=True)
    _plugin_with_search(plugin, [_manga("咒术回战", 1)])
    event = _event()
    plugin._card_cooldown_until = 12345.0
    monkeypatch.setattr(
        "plugin_pkg.core.render_card_cached", AsyncMock(return_value="/tmp/card.jpg")
    )
    monkeypatch.setattr(
        "plugin_pkg.core.embed_covers",
        AsyncMock(side_effect=lambda c, items, **kw: items),
    )
    monkeypatch.setattr("plugin_pkg.core.schedule_cleanup_file", MagicMock())

    await plugin.search_manga(event, "咒术回战")
    assert plugin._result_cards_enabled() is True
