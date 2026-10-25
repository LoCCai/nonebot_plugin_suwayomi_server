"""nonebot_plugin_suwayomi_server — 📖 Suwayomi 漫画助手

基于 `Suwayomi-Server <https://github.com/Suwayomi/Suwayomi-Server>`_
后端的 NoneBot2 漫画插件：搜索 · 阅读 · 下载 · 订阅更新推送 · Web 管理面板。

自 AstrBot 插件 ``astrbot_plugin_suwayomi_server`` 移植。
"""
from __future__ import annotations

from pathlib import Path

from nonebot import get_driver, get_plugin_config, logger
from nonebot.plugin import PluginMetadata

from .core import SuwayomiCore
from .handlers import set_core
from .settings import PluginConfig, load_runtime_config
from .store import JsonKvStore
from .web_routes import register_web_routes

__plugin_meta__ = PluginMetadata(
    name="Suwayomi 漫画助手",
    description=(
        "基于 Suwayomi-Server 的漫画搜索、阅读、下载与订阅更新推送插件，"
        "支持卡片渲染与 Web 管理面板"
    ),
    usage=(
        "发送「漫画 帮助」查看用法\n"
        "配置 SUWAYOMI_SERVER_URL 指向你的 Suwayomi-Server\n"
        "可选安装 nonebot-plugin-htmlrender 启用卡片渲染"
    ),
    type="application",
    homepage="https://github.com/LoCCai/nonebot_plugin_suwayomi_server",
    config=PluginConfig,
    supported_adapters={"~onebot.v11"},
)

plugin_config = get_plugin_config(PluginConfig)
DATA_DIR = Path(plugin_config.suwayomi_data_dir).expanduser()
if not DATA_DIR.is_absolute():
    DATA_DIR = Path.cwd() / DATA_DIR
DATA_DIR.mkdir(parents=True, exist_ok=True)

runtime_config = load_runtime_config(plugin_config, DATA_DIR)
kv_store = JsonKvStore(DATA_DIR / "kv.json")
core = SuwayomiCore(runtime_config, kv_store)
set_core(core)

# 卡片渲染（系统模式）依赖 nonebot-plugin-htmlrender，未安装时仅回退纯文本
try:
    from nonebot import require
    from .suwayomi.config import get_config_value

    if get_config_value(runtime_config, "result_cards_enabled", False):
        require("nonebot_plugin_htmlrender")
except Exception:  # noqa: BLE001
    logger.info(
        "[nonebot_plugin_suwayomi_server] 未加载 nonebot-plugin-htmlrender，"
        "卡片渲染将不可用（指令结果回退纯文本）"
    )

register_web_routes(core, plugin_config.suwayomi_dashboard_token)

driver = get_driver()


@driver.on_startup
async def _suwayomi_startup() -> None:
    await core.startup()


@driver.on_shutdown
async def _suwayomi_shutdown() -> None:
    await core.shutdown()
