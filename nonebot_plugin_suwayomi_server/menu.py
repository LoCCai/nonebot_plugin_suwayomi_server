"""Single command catalog for text help, PicMenu, QWeb and LLM discovery.

The metadata describes the native commands; execution and permissions remain
in the command handlers. No host-specific menu or LLM package is required.
"""
from __future__ import annotations

VERSION = "0.8.1"
MENU_GROUP = {
    "key": "entertainment", "name": "内容娱乐",
    "description": "漫画搜索、阅读、下载与订阅更新。", "sort_order": 50,
}

# key, title, canonical command body, summary, details, natural-language intents
COMMANDS = (
    ("help", "漫画帮助", "帮助", "查看漫画助手指令与使用条件",
     "命令别名为漫画助手、manga；执行时保留完整的漫画子命令。搜索前需安装外部漫画源。",
     ("漫画怎么用", "查看漫画帮助")),
    ("sources", "漫画源", "源", "查看后端已安装的漫画源",
     "没有漫画源时提示管理员在 Suwayomi 后端安装扩展；不要把空列表说成已经能搜索作品。",
     ("有哪些漫画源", "查看漫画来源")),
    ("search", "漫画搜索", "搜索 <关键词> [源名]", "搜索漫画并返回可订阅编号",
     "多词标题可用 + 连接；源名可选。编号仅用于当前群或私聊最近一次搜索结果；没有结果时不能编造编号。",
     ("帮我找漫画", "搜索一部漫画")),
    ("chapters", "漫画章节", "章节 <漫画名或ID> [--刷新]", "查看章节列表与精确章节 ID",
     "默认使用缓存；用户明确要求重新获取时可加 --刷新。阅读遇到重复章节号时使用列表给出的 ID:数字。",
     ("查看漫画章节", "这部漫画更新到哪")),
    ("read", "漫画阅读", "阅读 <漫画名或ID> <章节号或ID:数字>", "把章节页面发送到当前会话",
     "群聊按已配置的方式发送图片或合并转发，私聊发送普通图片。直接投递插件结果，不要把已发送页面再次发送一遍。",
     ("帮我看这话漫画", "发送漫画章节")),
    ("download", "漫画下载", "下载 <漫画名或ID> <章节号> [pdf/zip/cbz]", "打包章节并发送文件",
     "默认 PDF，支持 ZIP/CBZ；QQ 网关需能读取共享文件路径。仅确认上传回执后才算文件送达。",
     ("下载这话漫画", "打包漫画文件")),
    ("subscriptions", "漫画订阅列表", "我的订阅", "查看当前会话的订阅",
     "别名：漫画 订阅列表。群聊订阅以群为单位，私聊订阅以用户为单位。",
     ("看看订阅了哪些漫画", "查看漫画订阅")),
    ("subscribe", "漫画订阅", "订阅 <搜索结果编号>", "订阅当前搜索结果中的漫画",
     "会修改当前会话的订阅，须有用户明确意图；先搜索并使用真实编号。编号不是漫画 ID。",
     ("订阅这部漫画", "追更这部漫画")),
    ("batch-subscribe", "漫画批量订阅", "批量订阅 <名称1>, <名称2>, ... [源名]", "按名称批量订阅漫画",
     "会修改当前会话的订阅，须有用户明确意图；名称之间用逗号分隔。",
     ("批量订阅漫画", "同时追更几部漫画")),
    ("unsubscribe", "漫画取消订阅", "取消订阅 <ID或名称>", "取消当前会话的漫画订阅",
     "会停止这部漫画在当前会话的订阅；不影响其他会话。按订阅列表中的真实 ID 或名称操作。",
     ("取消漫画订阅", "不再追更这部漫画")),
    ("push-status", "漫画推送状态", "推送 状态", "查看当前会话自动发送章节的状态",
     "只读查询，不改变订阅和自动推送偏好。",
     ("漫画推送开了吗", "查看漫画推送状态")),
    ("push-on", "漫画开启推送", "推送 开", "新章节自动发送内容到当前会话",
     "须有用户明确意图；为当前会话已订阅漫画开启内容推送，后续新订阅继承偏好。",
     ("开启漫画推送", "自动发送新漫画章节")),
    ("push-off", "漫画关闭推送", "推送 关", "关闭当前会话的章节内容自动推送",
     "须有用户明确意图；仍保留订阅更新通知，不再自动发送整章内容。",
     ("关闭漫画推送", "停止自动发送漫画章节")),
    ("update", "漫画全局更新", "更新", "管理员检查并向所有订阅会话推送更新",
     "权限：仅 NoneBot 超级管理员。会影响所有订阅会话；用户只问一部漫画时应使用章节查询，不要触发全局更新。",
     ("检查所有漫画更新", "全局漫画更新")),
)


def format_help() -> str:
    lines = ["📖 Suwayomi 漫画助手"]
    lines.extend(f"/漫画 {body} — {summary}" for _, _, body, summary, _, _ in COMMANDS)
    lines.append("\n群聊订阅以群为单位，私聊以用户为单位。全局更新仅超级管理员。")
    lines.append("多词搜索可用 + 连接；重复编号章节使用 ID:数字。后端须先安装漫画源。")
    return "\n".join(lines)


def build_menu_data() -> list[dict]:
    return [{
        "func": title,
        "trigger_method": "命令触发",
        "trigger_condition": "仅 NoneBot 超级管理员" if key == "update" else "群聊与私聊用户；沿用原生权限校验",
        "brief_des": summary,
        "detail_des": detail,
        "pmn_triggers": [
            {"type": "command", "label": "命令" if head == "漫画" else "命令别名", "value": f"/{head} {body}"}
            for head in ("漫画", "漫画助手", "manga")
        ],
        "pmn_llm_intents": list(intents),
        "qweb": {"key": key, "slug": f"suwayomi-{key}", "sort_order": index * 10},
    } for index, (key, title, body, summary, detail, intents) in enumerate(COMMANDS, 1)]


def metadata_extra() -> dict:
    return {
        "author": ["LoCCai", "FFFold (AstrBot upstream)"], "version": VERSION,
        "menu_data": build_menu_data(),
        "pmn": {"markdown": True, "sort_order": 151, "menu_group": dict(MENU_GROUP)},
        "qweb": {
            "catalog_provider": True, "key": "suwayomi", "slug": "suwayomi",
            "framework": "nonebot", "audience": "群聊与私聊用户；管理操作按原生权限校验",
            "icon": "book", "is_public": True,
        },
    }
