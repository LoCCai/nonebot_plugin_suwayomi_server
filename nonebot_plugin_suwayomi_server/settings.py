"""NoneBot2 插件配置与运行时配置。

两层配置来源（后者覆盖前者）：

1. **NoneBot 全局配置**（`.env` / 环境变量）：`suwayomi_` 前缀，如
   ``SUWAYOMI_SERVER_URL``，由 ``get_plugin_config`` 读取。
2. **运行时配置文件** ``<data_dir>/config.json``：WebUI 设置页保存的
   分组配置，加载时覆盖全局配置。WebUI 改动只写入该文件，不影响 `.env`。

对插件内部，配置以**分组 dict**（与 ``suwayomi.config`` 的
``CONFIG_GROUPS`` 结构一致）呈现，因此 ``get_config_value`` /
``set_config_value`` / ``flatten_config`` 等既有工具函数可以原样使用。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from nonebot import logger
from pydantic import BaseModel, Field
from .persistence import write_json

from .suwayomi.config import (
    CONFIG_GROUPS,
    KEY_TO_GROUP,
    get_config_value,
    set_config_value,
)

# 全部配置键的默认值（与原 AstrBot 插件 _conf_schema.json 保持一致）
CONFIG_DEFAULTS: dict[str, Any] = {
    # server
    "server_url": "http://localhost:4567",
    "auth_mode": "none",
    "username": "",
    "password": "",
    # cards
    "result_cards_enabled": False,
    "card_render_timeout_sec": 30,
    "t2i_source": "system",
    "t2i_endpoint": "",
    # reading
    "max_pages": 30,
    "send_mode": "image",
    "image_fetch_mode": "download",
    # pack
    "download_format": "pdf",
    "download_concurrency": 6,
    "download_retries": 3,
    "file_delivery_max_pages": 300,
    # push
    "auto_push_mode": "image",
    # advanced
    "check_interval": 60,
    "chapter_cache_hours": 6,
    "chapter_list_show_cover": True,
    "default_source_id": 0,
    "search_result_ranking": True,
    "search_display_limit": 20,
    "search_refresh_truncated_titles": True,
    "search_alias_expansion": True,
    "bangumi_mirror": False,
    "bangumi_mirror_url": "",
    "temp_dir": "",
}

# 不分组的 NoneBot 侧专属配置
class PluginConfig(BaseModel):
    """NoneBot 配置项（``.env`` 中 ``SUWAYOMI_`` 前缀）。"""

    suwayomi_data_dir: str = Field(
        default="data/suwayomi",
        description="插件数据目录（KV 存储、运行时配置、渲染产物）",
    )
    suwayomi_dashboard_token: str = Field(
        default="",
        description="管理 API 的 Bearer 令牌；留空禁用管理面板",
    )

    # 业务配置：允许通过 .env 给初值，WebUI 保存后以 config.json 为准
    suwayomi_server_url: str = CONFIG_DEFAULTS["server_url"]
    suwayomi_auth_mode: str = CONFIG_DEFAULTS["auth_mode"]
    suwayomi_username: str = CONFIG_DEFAULTS["username"]
    suwayomi_password: str = CONFIG_DEFAULTS["password"]
    suwayomi_result_cards_enabled: bool = CONFIG_DEFAULTS["result_cards_enabled"]
    suwayomi_card_render_timeout_sec: int = CONFIG_DEFAULTS["card_render_timeout_sec"]
    suwayomi_t2i_source: str = CONFIG_DEFAULTS["t2i_source"]
    suwayomi_t2i_endpoint: str = CONFIG_DEFAULTS["t2i_endpoint"]
    suwayomi_max_pages: int = CONFIG_DEFAULTS["max_pages"]
    suwayomi_send_mode: str = CONFIG_DEFAULTS["send_mode"]
    suwayomi_image_fetch_mode: str = CONFIG_DEFAULTS["image_fetch_mode"]
    suwayomi_download_format: str = CONFIG_DEFAULTS["download_format"]
    suwayomi_download_concurrency: int = CONFIG_DEFAULTS["download_concurrency"]
    suwayomi_download_retries: int = CONFIG_DEFAULTS["download_retries"]
    suwayomi_file_delivery_max_pages: int = CONFIG_DEFAULTS["file_delivery_max_pages"]
    suwayomi_auto_push_mode: str = CONFIG_DEFAULTS["auto_push_mode"]
    suwayomi_check_interval: int = CONFIG_DEFAULTS["check_interval"]
    suwayomi_chapter_cache_hours: int = CONFIG_DEFAULTS["chapter_cache_hours"]
    suwayomi_chapter_list_show_cover: bool = CONFIG_DEFAULTS["chapter_list_show_cover"]
    suwayomi_default_source_id: int = CONFIG_DEFAULTS["default_source_id"]
    suwayomi_search_result_ranking: bool = CONFIG_DEFAULTS["search_result_ranking"]
    suwayomi_search_display_limit: int = CONFIG_DEFAULTS["search_display_limit"]
    suwayomi_search_refresh_truncated_titles: bool = CONFIG_DEFAULTS[
        "search_refresh_truncated_titles"
    ]
    suwayomi_search_alias_expansion: bool = CONFIG_DEFAULTS["search_alias_expansion"]
    suwayomi_bangumi_mirror: bool = CONFIG_DEFAULTS["bangumi_mirror"]
    suwayomi_bangumi_mirror_url: str = CONFIG_DEFAULTS["bangumi_mirror_url"]
    suwayomi_temp_dir: str = CONFIG_DEFAULTS["temp_dir"]


class RuntimeConfig(dict):
    """分组运行时配置：对 ``suwayomi.config`` 就是普通 dict，另带持久化。"""

    def __init__(self, data: dict[str, Any], path: Path | None = None):
        super().__init__(data)
        self._path = path

    def resolve_temp_dir(self) -> None:
        if self._path is None:
            return
        raw = str(get_config_value(self, "temp_dir", "") or "cache")
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = self._path.parent / path
        path = path.resolve()
        path.mkdir(parents=True, exist_ok=True)
        set_config_value(self, "temp_dir", str(path))

    def save_config(self) -> None:
        """保存到 ``<data_dir>/config.json``（web/api.py 会同步调用）。"""
        if self._path is None:
            return
        try:
            self.resolve_temp_dir()
            write_json(self._path, dict(self))
        except Exception as exc:
            logger.error(f"[suwayomi_config] 配置保存失败: {exc}")
            raise


def _grouped_from_flat(flat: dict[str, Any]) -> dict[str, Any]:
    grouped: dict[str, Any] = {}
    for group, keys in CONFIG_GROUPS.items():
        if group == "ai":
            # NoneBot 版不移植 AstrBot Agent 工具，AI 分组配置不生效
            continue
        section = {key: flat[key] for key in keys if key in flat}
        if section:
            grouped[group] = section
    return grouped


def _env_flat(settings: PluginConfig) -> dict[str, Any]:
    """把 pydantic 配置（suwayomi_ 前缀）平铺成业务配置键。"""
    flat: dict[str, Any] = {}
    for key in CONFIG_DEFAULTS:
        value = getattr(settings, f"suwayomi_{key}", None)
        if value is not None:
            flat[key] = value
    return flat


def load_runtime_config(settings: PluginConfig, data_dir: Path) -> RuntimeConfig:
    """构建运行时配置：默认值 → .env → ``config.json`` 覆盖。"""
    flat = _env_flat(settings)
    config_path = data_dir / "config.json"
    grouped = _grouped_from_flat(flat)
    config = RuntimeConfig(grouped, config_path)

    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raw = None
    except Exception as exc:
        logger.warning(f"[suwayomi_config] 运行时配置读取失败，使用 .env 配置: {exc}")
        raw = None

    if isinstance(raw, dict):
        for key in list(raw):
            if key in KEY_TO_GROUP and not isinstance(raw[key], dict):
                set_config_value(config, key, raw[key])
        for group, section in raw.items():
            if isinstance(section, dict) and group in CONFIG_GROUPS:
                for key, value in section.items():
                    if key in KEY_TO_GROUP:
                        set_config_value(config, key, value)
        logger.info(f"[suwayomi_config] 已加载运行时配置: {config_path}")

    config.resolve_temp_dir()
    return config
