"""Exercise migration boundaries, without real messages or external services."""
import asyncio
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from plugin_pkg import web_routes
from plugin_pkg.store import JsonKvStore
from plugin_pkg.settings import PluginConfig, load_runtime_config
from plugin_pkg.suwayomi.msg import Image, Text, deliver, send_to_session
from plugin_pkg.suwayomi.updater import check_updates
from tests.test_nb2_msg import _bot
from tests.test_updater import CountingClient, FakePlugin, _chapters, _make_sub_mgr, _config


async def test_local_image_transferred_as_bytes_and_cq_is_literal(tmp_path):
    image = tmp_path / "page.png"
    image.write_bytes(b"image-content")
    bot = _bot()
    await deliver(bot, "OneBot V11|123|group:42", [Text("[CQ:at,qq=all]"), Image(path=str(image))])
    message = bot.call_api.call_args.kwargs["message"]
    assert message[0].type == "text"
    assert message[0].data["text"] == "[CQ:at,qq=all]"
    assert message[1].data["file"].startswith("base64://")
    assert "file://" not in str(message)


async def test_offline_bot_is_delivery_failure(monkeypatch):
    import nonebot
    monkeypatch.setattr(nonebot, "get_bot", MagicMock(side_effect=KeyError("offline")))
    with pytest.raises(RuntimeError, match="离线"):
        await send_to_session("OneBot V11|123|group:42", [Text("update")])


@pytest.mark.parametrize("target", ["broken", "QQ|123|group:42", "OneBot V11|999|group:42", "OneBot V11|123|session:42"])
async def test_invalid_delivery_target_rejected(target):
    bot = _bot()
    with pytest.raises(ValueError):
        await deliver(bot, target, [Text("update")])
    bot.call_api.assert_not_awaited()


async def test_state_isolation_and_failed_write_preserves_cache(tmp_path, monkeypatch):
    store = JsonKvStore(tmp_path / "kv.json")
    original = {"subscribers": ["one"]}
    await store.put_kv_data("subscriptions", original)
    original["subscribers"].clear()
    retrieved = await store.get_kv_data("subscriptions")
    retrieved["subscribers"].clear()
    assert (await store.get_kv_data("subscriptions"))["subscribers"] == ["one"]
    monkeypatch.setattr(store, "_write_file", MagicMock(side_effect=OSError("full")))
    with pytest.raises(OSError):
        await store.put_kv_data("subscriptions", {})
    assert (await store.get_kv_data("subscriptions"))["subscribers"] == ["one"]
    assert store._path.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("image_type", ["png", "jpeg"])
async def test_system_renderer_uses_compatible_api_and_managed_cache(tmp_path, monkeypatch, image_type):
    from plugin_pkg.renderers import make_system_renderer
    compatible = AsyncMock(return_value=b"rendered-image")
    incompatible = AsyncMock(side_effect=AssertionError("incompatible new API"))
    monkeypatch.setitem(sys.modules, "nonebot_plugin_htmlrender", SimpleNamespace(
        html_to_pic=compatible, render_html=incompatible,
    ))
    output = await make_system_renderer(str(tmp_path))(
        "<div>{{ title }}</div>", {"title": "test"}, options={"type": image_type},
    )
    assert Path(output).parent == tmp_path
    assert Path(output).read_bytes() == b"rendered-image"
    assert compatible.call_args.kwargs["quality"] == (95 if image_type == "jpeg" else None)
    incompatible.assert_not_awaited()


def test_private_config_and_default_temp_directory(tmp_path):
    cfg = load_runtime_config(PluginConfig(), tmp_path)
    cfg.save_config()
    assert cfg["advanced"]["temp_dir"] == str((tmp_path / "cache").resolve())
    assert (tmp_path / "config.json").stat().st_mode & 0o777 == 0o600


def dashboard(monkeypatch, token="test-secret"):
    app = FastAPI()
    monkeypatch.setattr(web_routes, "get_driver", lambda: SimpleNamespace(server_app=app))
    core = SimpleNamespace(config={}, sub_mgr=MagicMock(), client=MagicMock(), get_kv_data=AsyncMock())
    registered = web_routes.register_web_routes(core, token)
    return registered, TestClient(app)


def test_empty_token_disables_dashboard(monkeypatch):
    registered, client = dashboard(monkeypatch, "")
    assert not registered
    assert client.get("/suwayomi/config").status_code == 404


def test_assets_load_but_api_requires_bearer(monkeypatch):
    _, client = dashboard(monkeypatch)
    assert client.get("/suwayomi/").status_code == 200
    assert client.get("/suwayomi/app.js").status_code == 200
    assert client.get("/suwayomi/config?token=test-secret").status_code == 401
    assert client.get("/suwayomi/config", headers={"Authorization": "Bearer test-secret"}).status_code == 200
    assert client.post("/suwayomi/config", json=[1], headers={"Authorization": "Bearer test-secret"}).status_code == 400


async def test_failed_recipient_retried_without_resending_successful_recipient():
    plugin = FakePlugin()
    manager = _make_sub_mgr(plugin)
    await manager.subscribe(1, "Test", 1, "online")
    await manager.subscribe(1, "Test", 1, "offline")
    client = CountingClient({1: _chapters(1, [1, 2])}, delay=0)
    seen = []
    async def send(target, message):
        seen.append(target)
        if target == "offline":
            raise RuntimeError("offline")
    async def run(sender):
        return await check_updates(client, manager, sender, _config(), plugin.get_kv_data,
                                   plugin.put_kv_data, asyncio.Lock(), AsyncMock(), AsyncMock())
    summary = await run(send)
    assert "投递未完成" in summary
    assert (await manager.get_all_subscriptions())["1"]["latest_chapter_id"] == 0
    sender = AsyncMock()
    await run(sender)
    assert [c.args[0] for c in sender.call_args_list] == ["offline"]
    assert (await manager.get_all_subscriptions())["1"]["latest_chapter_id"] == 2


def test_real_nonebot_plugin_load_in_separate_process(tmp_path):
    root = Path(__file__).resolve().parent.parent
    env = dict(os.environ)
    env["PYTHONPATH"] = str(root) + os.pathsep + env.get("PYTHONPATH", "")
    code = '''
import asyncio, nonebot
nonebot.init(driver="~fastapi", _env_file=None)
plugin = nonebot.load_plugin("nonebot_plugin_suwayomi_server")
assert plugin is not None
assert plugin.metadata.supported_adapters == {"~onebot.v11"}
async def run():
    core = plugin.module.core
    await core.startup()
    first = core._bg_task
    core.start_bg_loop()
    assert first is core._bg_task
    await core.shutdown()
asyncio.run(run())
'''
    result = subprocess.run([sys.executable, "-c", code], cwd=tmp_path, env=env,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
