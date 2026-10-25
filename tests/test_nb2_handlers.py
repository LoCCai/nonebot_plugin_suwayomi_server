"""handlers.dispatch — 子命令路由与 MsgContext 构建。"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from plugin_pkg.handlers import build_ctx, dispatch
from tests.fake_ctx import FakeCtx


def _core():
    core = MagicMock()
    for name in (
        "help_cmd", "list_sources", "search_manga", "subscribe_manga",
        "batch_subscribe", "unsubscribe_manga", "my_subscriptions",
        "manual_update", "push_enable", "push_disable", "push_status",
        "list_chapters", "read_chapter", "download_chapter",
    ):
        setattr(core, name, AsyncMock())
    return core


async def _run(args: str):
    core = _core()
    ctx = FakeCtx(message_str=f"漫画 {args}")
    await dispatch(core, ctx, args)
    return core, ctx


class TestDispatch:
    @pytest.mark.asyncio
    async def test_empty_shows_help(self):
        core, _ = await _run("")
        core.help_cmd.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_help_alias(self):
        core, _ = await _run("help")
        core.help_cmd.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_search_routes_keyword(self):
        core, _ = await _run("搜索 咒术回战 拷贝漫画")
        core.search_manga.assert_awaited_once()
        assert core.search_manga.call_args.args[1] == "咒术回战 拷贝漫画"

    @pytest.mark.asyncio
    async def test_subscribe_routes_index(self):
        core, _ = await _run("订阅 3")
        assert core.subscribe_manga.call_args.args[1] == "3"

    @pytest.mark.asyncio
    async def test_batch_subscribe(self):
        core, _ = await _run("批量订阅 a, b")
        core.batch_subscribe.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_unsubscribe(self):
        core, _ = await _run("取消订阅 12")
        assert core.unsubscribe_manga.call_args.args[1] == "12"

    @pytest.mark.asyncio
    async def test_my_subscriptions(self):
        core, _ = await _run("我的订阅")
        core.my_subscriptions.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_manual_update(self):
        core, _ = await _run("更新")
        core.manual_update.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "action,method",
        [("开", "push_enable"), ("关", "push_disable"), ("状态", "push_status")],
    )
    async def test_push_actions(self, action, method):
        core, _ = await _run(f"推送 {action}")
        getattr(core, method).assert_awaited_once()

    @pytest.mark.asyncio
    async def test_push_unknown_action_answers_usage(self):
        core, ctx = await _run("推送 ???")
        core.push_enable.assert_not_awaited()
        assert "用法" in ctx.sent[0]

    @pytest.mark.asyncio
    async def test_chapters(self):
        core, _ = await _run("章节 一拳超人 --刷新")
        assert core.list_chapters.call_args.args[1] == "一拳超人 --刷新"

    @pytest.mark.asyncio
    async def test_read_splits_name_and_chapter(self):
        core, _ = await _run("阅读 一拳超人 第5话")
        assert core.read_chapter.call_args.args[1] == "一拳超人"
        assert core.read_chapter.call_args.args[2] == "第5话"

    @pytest.mark.asyncio
    async def test_read_no_args_delegates_usage(self):
        core, _ = await _run("阅读")
        core.read_chapter.assert_awaited_once()
        assert core.read_chapter.call_args.args[1:] == ("", "")

    @pytest.mark.asyncio
    async def test_download_delegates_raw_message(self):
        core, _ = await _run("下载 一拳超人 1 pdf")
        core.download_chapter.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_unknown_subcommand_answers(self):
        core, ctx = await _run("不存在的命令")
        assert "未知子命令" in ctx.sent[0]
        assert not any(
            getattr(core, name).await_count for name in (
                "help_cmd", "search_manga", "list_chapters"
            )
        )


class TestBuildCtx:
    def _bot(self):
        bot = MagicMock()
        bot.adapter.get_name = MagicMock(return_value="OneBot V11")
        bot.self_id = "123"
        return bot

    def test_group_event(self):
        from types import SimpleNamespace

        event = SimpleNamespace(
            get_plaintext=lambda: "漫画 帮助",
            get_user_id=lambda: "999",
            group_id=42,
        )

        ctx = build_ctx(self._bot(), event)

        assert ctx.session_key == "OneBot V11|123|group:42"
        assert ctx.message_str == "漫画 帮助"
        assert ctx.sender_id == "999"
        assert ctx.supports_forward is True

    def test_private_event_no_forward(self):
        from types import SimpleNamespace

        event = SimpleNamespace(
            get_plaintext=lambda: "漫画 帮助",
            get_user_id=lambda: "7",
            user_id="7",
        )

        ctx = build_ctx(self._bot(), event)
        assert ctx.supports_forward is False
        assert ctx.session_key == "OneBot V11|123|private:7"
