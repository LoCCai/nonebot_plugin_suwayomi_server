"""Core 测试用的假 MsgContext。

记录 ``ctx.send`` 发出的消息，同时提供与原 AstrBot 测试断言兼容的 mock 面：
``plain_result``（纯文本消息）/ ``chain_result``（图片、混合消息）/ ``send``。
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from plugin_pkg.suwayomi.msg import Text


class FakeCtx:
    def __init__(
        self,
        message_str: str = "",
        session_key: str = "aiocqhttp:group:g1",
        supports_forward: bool = False,
    ):
        self.session_key = session_key
        self.message_str = message_str
        self.sender_id = "u1"
        self.supports_forward = supports_forward
        self.bot = None
        self.event = None
        self.sent: list = []
        self.plain_result = MagicMock(side_effect=lambda text: text)
        self.chain_result = MagicMock(side_effect=lambda chain: chain)
        self.send = AsyncMock(side_effect=self._record)

    @property
    def unified_msg_origin(self) -> str:
        return self.session_key

    @unified_msg_origin.setter
    def unified_msg_origin(self, value: str) -> None:
        self.session_key = value

    async def _record(self, msg) -> None:
        if msg and all(isinstance(seg, Text) for seg in msg):
            text = "".join(seg.text for seg in msg)
            self.plain_result(text)
            self.sent.append(text)
        else:
            self.chain_result(msg)
            self.sent.append(msg)
