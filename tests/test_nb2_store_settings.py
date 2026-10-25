"""store.JsonKvStore 与 settings 运行时配置。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from plugin_pkg.settings import (
    CONFIG_DEFAULTS,
    PluginConfig,
    RuntimeConfig,
    load_runtime_config,
)
from plugin_pkg.store import JsonKvStore


class TestJsonKvStore:
    @pytest.mark.asyncio
    async def test_roundtrip_and_default(self, tmp_path):
        kv = JsonKvStore(tmp_path / "kv.json")
        assert await kv.get_kv_data("missing", "fallback") == "fallback"
        await kv.put_kv_data("a", {"x": 1})
        assert await kv.get_kv_data("a") == {"x": 1}

    @pytest.mark.asyncio
    async def test_persists_to_file(self, tmp_path):
        path = tmp_path / "kv.json"
        kv = JsonKvStore(path)
        await kv.put_kv_data("k", [1, 2, 3])
        raw = json.loads(path.read_text(encoding="utf-8"))
        assert raw["k"] == [1, 2, 3]

    @pytest.mark.asyncio
    async def test_delete(self, tmp_path):
        kv = JsonKvStore(tmp_path / "kv.json")
        await kv.put_kv_data("k", 1)
        await kv.delete_kv_data("k")
        assert await kv.get_kv_data("k") is None

    @pytest.mark.asyncio
    async def test_corrupt_file_preserved_and_rejected(self, tmp_path):
        path = tmp_path / "kv.json"
        path.write_text("{not json", encoding="utf-8")
        kv = JsonKvStore(path)
        with pytest.raises(json.JSONDecodeError):
            await kv.put_kv_data("k", "d")
        assert path.read_text() == "{not json"


class TestRuntimeConfig:
    def test_defaults_from_env_settings(self, tmp_path):
        cfg = load_runtime_config(PluginConfig(), tmp_path)
        assert get_value(cfg, "server_url") == CONFIG_DEFAULTS["server_url"]
        assert get_value(cfg, "max_pages") == CONFIG_DEFAULTS["max_pages"]

    def test_file_overrides_grouped(self, tmp_path):
        (tmp_path / "config.json").write_text(
            json.dumps({"server": {"server_url": "http://x:1"}}), encoding="utf-8"
        )
        cfg = load_runtime_config(PluginConfig(), tmp_path)
        assert get_value(cfg, "server_url") == "http://x:1"

    def test_file_overrides_flat_legacy(self, tmp_path):
        (tmp_path / "config.json").write_text(
            json.dumps({"max_pages": 5}), encoding="utf-8"
        )
        cfg = load_runtime_config(PluginConfig(), tmp_path)
        assert get_value(cfg, "max_pages") == 5

    def test_save_config_roundtrip(self, tmp_path):
        cfg = load_runtime_config(PluginConfig(), tmp_path)
        cfg["server"] = dict(cfg.get("server", {}), server_url="http://saved:1")
        cfg.save_config()
        again = load_runtime_config(PluginConfig(), tmp_path)
        assert get_value(again, "server_url") == "http://saved:1"

    def test_env_settings_applied(self, tmp_path):
        cfg = load_runtime_config(
            PluginConfig(suwayomi_server_url="http://env:4567"), tmp_path
        )
        assert get_value(cfg, "server_url") == "http://env:4567"


def get_value(cfg, key, default=None):
    from plugin_pkg.suwayomi.config import get_config_value

    return get_config_value(cfg, key, default)
