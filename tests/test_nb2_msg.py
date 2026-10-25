"""plugin_pkg.suwayomi.msg — 会话标识、消息构建与 OneBot v11 发送。"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from plugin_pkg.suwayomi.msg import (
    FileSeg,
    Forward,
    ForwardNode,
    Image,
    Text,
    chain_msg,
    deliver,
    file_msg,
    image_file_msg,
    image_url_msg,
    make_session_key,
    parse_session_key,
    plain_text,
    text_msg,
)


def _bot(adapter_name="OneBot V11", self_id="123"):
    bot = MagicMock()
    bot.adapter.get_name = MagicMock(return_value=adapter_name)
    bot.self_id = self_id
    bot.call_api = AsyncMock()
    bot.send = AsyncMock()
    return bot


class _FakeEvent:
    def __init__(self, group_id=None, user_id=999):
        if group_id is not None:
            self.group_id = group_id
        self.user_id = user_id


def _event(group_id=None, user_id=999):
    return _FakeEvent(group_id=group_id, user_id=user_id)


class TestSessionKey:
    def test_group_event(self):
        key = make_session_key(_bot(), _event(group_id=42))
        assert key == "OneBot V11|123|group:42"
        assert parse_session_key(key) == ("123", "group", "42")

    def test_private_event(self):
        key = make_session_key(_bot(), _event(user_id=7))
        assert key == "OneBot V11|123|private:7"
        assert parse_session_key(key) == ("123", "private", "7")

    def test_roundtrip_and_invalid(self):
        assert parse_session_key("garbage") is None
        assert parse_session_key("a|b|weird:1") is None


class TestMsgBuilders:
    def test_text_msg(self):
        assert plain_text(text_msg("hello")) == "hello"

    def test_chain_msg_flattens(self):
        msg = chain_msg(text_msg("a"), None, Image(url="u"), "b", Text("c"))
        assert [type(s).__name__ for s in msg] == ["Text", "Image", "Text", "Text"]
        assert plain_text(msg) == "abc"

    def test_file_msg(self):
        msg = file_msg(Path("/tmp/x.pdf"), "x.pdf")
        assert isinstance(msg[0], FileSeg)
        assert msg[0].name == "x.pdf"

    def test_image_helpers(self):
        assert image_file_msg("/tmp/a.jpg")[0].path == "/tmp/a.jpg"
        assert image_url_msg("http://u")[0].url == "http://u"


class TestDeliver:
    @pytest.mark.asyncio
    async def test_text_and_image_batched_into_one_message(self):
        bot = _bot()
        msg = [Text("a"), Image(url="http://u/1"), Text("b")]
        await deliver(bot, "OneBot V11|123|group:42", msg)
        bot.call_api.assert_awaited_once()
        args = bot.call_api.call_args
        assert args.args[0] == "send_group_msg"
        assert args.kwargs["group_id"] == 42
        message = args.kwargs["message"]
        # 三段合并为一条消息
        assert len(message) == 3

    @pytest.mark.asyncio
    async def test_file_uses_upload_api(self):
        bot = _bot()
        await deliver(bot, "OneBot V11|123|group:42", file_msg("/tmp/x.pdf", "x.pdf"))
        bot.call_api.assert_awaited_once()
        args = bot.call_api.call_args
        assert args.args[0] == "upload_group_file"
        assert args.kwargs["file"] == "/tmp/x.pdf"
        assert args.kwargs["name"] == "x.pdf"

    @pytest.mark.asyncio
    async def test_forward_uses_forward_api(self):
        bot = _bot()
        msg = [
            Forward(
                nodes=[
                    ForwardNode(name="n1", uin="0", content=[Text("hi")]),
                    ForwardNode(name="n2", uin="0", content=[Image(url="http://u")]),
                ]
            )
        ]
        await deliver(bot, "OneBot V11|123|group:42", msg)
        bot.call_api.assert_awaited_once()
        args = bot.call_api.call_args
        assert args.args[0] == "send_group_forward_msg"
        assert len(args.kwargs["messages"]) == 2

    @pytest.mark.asyncio
    async def test_event_target_uses_bot_send(self):
        bot = _bot()
        await deliver(bot, _event(group_id=42), text_msg("hello"))
        bot.send.assert_awaited_once()
        bot.call_api.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_private_target_uses_private_api(self):
        bot = _bot()
        await deliver(bot, "OneBot V11|123|private:7", text_msg("hi"))
        args = bot.call_api.call_args
        assert args.args[0] == "send_private_msg"
        assert args.kwargs["user_id"] == 7

    @pytest.mark.asyncio
    async def test_other_adapter_text_only_fallback(self):
        bot = _bot(adapter_name="QQ")
        event = _event(group_id=1)
        await deliver(bot, event, text_msg("hi"))
        bot.send.assert_awaited_once()
        with pytest.raises(RuntimeError):
            await deliver(bot, event, image_url_msg("http://u"))

    @pytest.mark.asyncio
    async def test_mixed_msg_sends_file_between_messages(self):
        bot = _bot()
        msg = [Text("t"), FileSeg(path="/tmp/a", name="a"), Image(url="http://u")]
        await deliver(bot, "OneBot V11|123|group:42", msg)
        apis = [c.args[0] for c in bot.call_api.call_args_list]
        assert apis == ["send_group_msg", "upload_group_file", "send_group_msg"]
