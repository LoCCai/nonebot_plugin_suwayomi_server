"""NoneBot2 命令入口：把事件翻译给 :class:`core.SuwayomiCore`。

命令结构与原 AstrBot 插件一致（``/漫画 <子命令> ...``）。消息文本
（``event.get_plaintext()``）原样传给核心层，核心内既有解析函数
（``split_search_query`` / ``parse_download_args`` / token 定位等）据此
工作，与原插件行为保持一致。
"""
from __future__ import annotations

from nonebot import on_command, logger, get_driver
from nonebot.adapters import Bot, Event, Message
from nonebot.params import CommandArg

from .core import SuwayomiCore
from .suwayomi import PLUGIN_NAME
from .suwayomi.msg import MsgContext, make_session_key

_CORE: SuwayomiCore | None = None


def set_core(core: SuwayomiCore) -> None:
    global _CORE
    _CORE = core


def get_core() -> SuwayomiCore:
    if _CORE is None:
        raise RuntimeError("SuwayomiCore 尚未初始化")
    return _CORE


def build_ctx(bot: Bot, event: Event) -> MsgContext:
    plaintext = event.get_plaintext()
    return MsgContext(
        bot=bot,
        event=event,
        session_key=make_session_key(bot, event),
        message_str=plaintext,
        sender_id=str(event.get_user_id() or ""),
        supports_forward=(
            bot.adapter.get_name() == "OneBot V11"
            and getattr(event, "group_id", None) is not None
        ),
    )


async def dispatch(core: SuwayomiCore, ctx: MsgContext, args_text: str) -> None:
    """按第一个 token 分派子命令。"""
    args_text = args_text.strip()
    # Core parsers expect the canonical command, including for aliases.
    ctx.message_str = f"漫画 {args_text}".strip()
    tokens = args_text.split()
    if not tokens:
        await core.help_cmd(ctx)
        return

    sub = tokens[0]
    rest = args_text[len(sub):].strip()
    rest_tokens = tokens[1:]

    if sub in {"帮助", "help", "h"}:
        await core.help_cmd(ctx)
    elif sub == "源":
        await core.list_sources(ctx)
    elif sub == "搜索":
        await core.search_manga(ctx, rest)
    elif sub == "订阅":
        await core.subscribe_manga(ctx, rest)
    elif sub == "批量订阅":
        await core.batch_subscribe(ctx)
    elif sub == "取消订阅":
        await core.unsubscribe_manga(ctx, rest)
    elif sub in {"我的订阅", "订阅列表"}:
        await core.my_subscriptions(ctx)
    elif sub == "更新":
        if ctx.sender_id not in get_driver().config.superusers:
            await ctx_send(ctx, "全局更新仅限机器人管理员；订阅会按计划自动检查。")
            return
        await core.manual_update(ctx)
    elif sub == "推送":
        action = rest_tokens[0] if rest_tokens else ""
        if action == "开":
            await core.push_enable(ctx)
        elif action == "关":
            await core.push_disable(ctx)
        elif action == "状态":
            await core.push_status(ctx)
        else:
            await ctx_send(ctx, "用法: /漫画 推送 开/关/状态")
    elif sub == "章节":
        await core.list_chapters(ctx, rest)
    elif sub == "阅读":
        # 最后一词是章节号（ID:123 / 第5话 / 38.5），前面是漫画名。
        if not rest_tokens:
            await core.read_chapter(ctx, "", "")
            return
        name = " ".join(rest_tokens[:-1]) if len(rest_tokens) > 1 else rest
        chapter_num = rest_tokens[-1] if len(rest_tokens) > 1 else ""
        await core.read_chapter(ctx, name, chapter_num)
    elif sub == "下载":
        # 参数解析在核心层完成（parse_download_args）
        await core.download_chapter(ctx, "", "")
    else:
        await ctx_send(ctx, f"未知子命令「{sub}」。发送「漫画 帮助」查看用法。")


async def ctx_send(ctx: MsgContext, text: str) -> None:
    from .suwayomi.msg import text_msg

    await ctx.send(text_msg(text))


manga_cmd = on_command(
    "漫画",
    aliases={"漫画助手", "manga"},
    priority=10,
    block=True,
)


@manga_cmd.handle()
async def handle_manga(bot: Bot, event: Event, args: Message = CommandArg()):
    core = get_core()
    ctx = build_ctx(bot, event)
    args_text = args.extract_plain_text().strip()
    try:
        await dispatch(core, ctx, args_text)
    except Exception as exc:
        logger.error(f"[{PLUGIN_NAME}] 命令处理失败: {type(exc).__name__}: {exc}")
        try:
            await ctx_send(ctx, "命令执行失败，请稍后重试。")
        except Exception:
            pass
