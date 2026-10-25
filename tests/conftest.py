"""Pure module tests use one package alias; no generic top-level modules."""
from __future__ import annotations

import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PKG_DIR = ROOT / "nonebot_plugin_suwayomi_server"

for path in (str(ROOT),):
    if path not in sys.path:
        sys.path.insert(0, path)

if "plugin_pkg" not in sys.modules:
    stub = types.ModuleType("plugin_pkg")
    stub.__path__ = [str(PKG_DIR)]  # type: ignore[attr-defined]
    sys.modules["plugin_pkg"] = stub

# handlers.py 在导入时注册 matcher，需要已初始化的 nonebot 驱动
import nonebot  # noqa: E402

try:
    nonebot.get_driver()
except ValueError:
    nonebot.init(driver="~fastapi")
