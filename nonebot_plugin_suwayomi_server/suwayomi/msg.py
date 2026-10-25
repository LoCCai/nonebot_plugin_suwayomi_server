"""平台无关的消息模型 + OneBot v11 发送实现。

原 AstrBot 插件用 ``MessageChain`` / ``message_components`` 构建消息，
这里定义等价的轻量 ``Msg``（若干 Segment 的列表），由 :func:`deliver` 统一
翻译成 OneBot v11 消息并发送。其他适配器走文本兜底，便于命令在更多平台
可用；图片/文件/合并转发为 OneBot v11 专属能力。

会话标识（session_key）格式::

    "<adapter名>|<bot self_id>|<kind>:<id>"

例如 ``OneBot V11|123456|group:789012``。订阅推送按 session_key 寻址，
与 AstrBot 的 unified_msg_origin 角色一致。
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Union

from nonebot import Bot, logger

PLUGIN_TAG = "suwayomi_msg"


# ── 消息模型 ─────────────────────────────────────────────────────


@dataclass
class Text:
    text: str


@dataclass
class Image:
    path: str | None = None
    url: str | None = None


@dataclass
class FileSeg:
    path: str
    name: str | None = None


@dataclass
class ForwardNode:
    name: str
    uin: str
    content: list["Segment"] = field(default_factory=list)


@dataclass
class Forward:
    nodes: list[ForwardNode] = field(default_factory=list)


Segment = Union[Text, Image, FileSeg, Forward]
Msg = list[Segment]


def text_msg(text: str) -> Msg:
    return [Text(text)]


def image_file_msg(path: str | Path) -> Msg:
    return [Image(path=str(path))]


def image_url_msg(url: str) -> Msg:
    return [Image(url=url)]


def file_msg(path: str | Path, name: str | None = None) -> Msg:
    return [FileSeg(path=str(path), name=name)]


def forward_msg(nodes: list[ForwardNode]) -> Msg:
    return [Forward(nodes=nodes)]


def chain_msg(*parts: Union[Msg, Segment, str, None]) -> Msg:
    """拼接消息片段（None 自动忽略，str 视为文本）。"""
    out: Msg = []
    for part in parts:
        if part is None:
            continue
        if isinstance(part, str):
            out.append(Text(part))
        elif isinstance(part, list):
            out.extend(part)
        else:
            out.append(part)
    return out


def plain_text(msg: Msg) -> str:
    return "".join(seg.text for seg in msg if isinstance(seg, Text))


# ── 会话标识 ─────────────────────────────────────────────────────


def make_session_key(bot: Bot, event: Any) -> str:
    """从 bot + event 派生稳定的会话标识。"""
    adapter = bot.adapter.get_name()
    kind, target = _event_target(event)
    return f"{adapter}|{bot.self_id}|{kind}:{target}"


def _event_target(event: Any) -> tuple[str, str]:
    group_id = getattr(event, "group_id", None)
    if group_id is not None:
        return "group", str(group_id)
    user_id = getattr(event, "user_id", None)
    if user_id is not None:
        return "private", str(user_id)
    return "session", str(event.get_session_id())


def parse_session_key(key: str) -> tuple[str, str, str] | None:
    """解析 session_key → (self_id, kind, target)；格式非法返回 None。"""
    parts = str(key).split("|", 2)
    if len(parts) != 3:
        return None
    _adapter, self_id, rest = parts
    kind, _, target = rest.partition(":")
    if (_adapter != "OneBot V11" or not self_id.isdecimal()
            or not target.isdecimal() or int(target) <= 0
            or kind not in {"group", "private"}):
        return None
    return self_id, kind, target


# ── OneBot v11 发送 ──────────────────────────────────────────────


def _is_ob11(bot: Any) -> bool:
    return bot.adapter.get_name() == "OneBot V11"


def _image_segment(seg: Image):
    from nonebot.adapters.onebot.v11 import MessageSegment

    if seg.path:
        # The QQ gateway need not share the Bot's filesystem.
        return MessageSegment.image(Path(seg.path).read_bytes())
    return MessageSegment.image(seg.url or "")


def _build_ob11_message(msg: Msg):
    """把 Msg 里可直接进消息链的片段转成 OneBot v11 Message。

    文件与合并转发片段不入链（需走独立 API），返回时顺带带出。
    """
    from nonebot.adapters.onebot.v11 import Message, MessageSegment

    out = Message()
    for seg in msg:
        if isinstance(seg, Text):
            out += MessageSegment.text(seg.text)
        elif isinstance(seg, Image):
            out += _image_segment(seg)
        elif isinstance(seg, FileSeg):
            logger.warning(
                f"[{PLUGIN_TAG}] 文件片段不支持进入消息链，将单独上传"
            )
        elif isinstance(seg, Forward):
            logger.warning(
                f"[{PLUGIN_TAG}] 合并转发片段不支持进入消息链，将单独发送"
            )
    return out


async def _send_forward(bot: Bot, kind: str, target: str, seg: Forward) -> None:
    from nonebot.adapters.onebot.v11 import Message, MessageSegment

    nodes = []
    for node in seg.nodes:
        content = Message()
        for item in node.content:
            if isinstance(item, Text):
                content += MessageSegment.text(item.text)
            elif isinstance(item, Image):
                content += await asyncio.to_thread(_image_segment, item)
        nodes.append(
            MessageSegment.node_custom(
                user_id=int(node.uin) if str(node.uin).isdigit() else 0,
                nickname=node.name,
                content=content,
            )
        )
    if kind == "group":
        await bot.call_api(
            "send_group_forward_msg", group_id=int(target), messages=nodes
        )
    else:
        await bot.call_api(
            "send_private_forward_msg", user_id=int(target), messages=nodes
        )


async def _send_file(bot: Bot, kind: str, target: str, seg: FileSeg) -> None:
    name = seg.name or Path(seg.path).name
    if kind == "group":
        await bot.call_api(
            "upload_group_file",
            group_id=int(target),
            file=str(seg.path),
            name=name,
        )
    else:
        await bot.call_api(
            "upload_private_file",
            user_id=int(target),
            file=str(seg.path),
            name=name,
        )


async def deliver(bot: Bot, target: Any, msg: Msg) -> None:
    """发送一条消息。

    ``target`` 可为事件对象（命令回复）或 session_key 字符串（推送）。
    OneBot v11 走完整能力（文本/图片/文件/合并转发）；其他适配器仅发送
    文本片段，保证命令可用。
    """
    if not msg:
        return

    is_event = not isinstance(target, str)

    if not _is_ob11(bot):
        text = plain_text(msg)
        has_media = any(not isinstance(seg, Text) for seg in msg)
        if text:
            if has_media:
                logger.warning(
                    f"[{PLUGIN_TAG}] 当前适配器 {bot.adapter.get_name()} "
                    f"不支持媒体推送，已忽略图片/文件内容"
                )
            if is_event:
                await bot.send(target, text)
                return
        raise RuntimeError(
            f"适配器 {bot.adapter.get_name()} 不支持定向媒体推送"
        )

    if is_event:
        kind, tid = _event_target(target)
    else:
        parsed = parse_session_key(target)
        if parsed is None:
            raise ValueError("非法 Suwayomi session_key")
        _self_id, kind, tid = parsed
        if _self_id != str(bot.self_id):
            raise ValueError("Suwayomi session_key 与 Bot 不匹配")

    async def _send_message(message) -> None:
        if is_event:
            await bot.send(target, message)
        elif kind == "group":
            await bot.call_api("send_group_msg", group_id=int(tid), message=message)
        else:
            await bot.call_api("send_private_msg", user_id=int(tid), message=message)

    # 连续的 文本/图片 片段合并成一条消息，与原插件的链式发送行为一致
    pending: list[Segment] = []
    try:
        for seg in msg:
            if isinstance(seg, (Text, Image)):
                pending.append(seg)
                continue
            if pending:
                await _send_message(await asyncio.to_thread(_build_ob11_message, pending))
                pending = []
            if isinstance(seg, Forward):
                await _send_forward(bot, kind, tid, seg)
            elif isinstance(seg, FileSeg):
                await _send_file(bot, kind, tid, seg)
        if pending:
            await _send_message(await asyncio.to_thread(_build_ob11_message, pending))
    except Exception as exc:
        logger.error(f"[{PLUGIN_TAG}] 消息发送到 {kind}:{tid} 失败: {exc}")
        raise


async def deliver_text(bot: Bot, target: Any, text: str) -> None:
    await deliver(bot, target, text_msg(text))


async def send_to_session(session_key: str, msg: Msg) -> None:
    """按 session_key 定向推送（订阅更新等无事件场景）。"""
    import nonebot

    parsed = parse_session_key(session_key)
    if parsed is None:
        raise ValueError("非法 Suwayomi session_key")
    self_id, _kind, _target = parsed
    try:
        bot = nonebot.get_bot(self_id)
    except Exception as exc:
        raise RuntimeError(f"Suwayomi 推送 Bot {self_id} 当前离线") from exc
    await deliver(bot, session_key, msg)


@dataclass
class MsgContext:
    """命令处理上下文：封装 bot/event 与会话信息。"""

    bot: Any
    event: Any
    session_key: str
    message_str: str = ""
    sender_id: str = ""
    supports_forward: bool = False

    async def send(self, msg: Msg) -> None:
        await deliver(self.bot, self.event, msg)
