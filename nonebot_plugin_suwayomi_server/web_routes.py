"""Web 管理面板 + REST API（FastAPI 路由）。

路由与原 AstrBot 插件 ``context.register_web_api`` 注册的端点一致：

- ``GET  /suwayomi/status``
- ``GET  /suwayomi/subscriptions``
- ``POST /suwayomi/subscription/delete``
- ``POST /suwayomi/subscription/push``
- ``GET|POST /suwayomi/config``
- ``GET  /suwayomi/sources``
- ``POST /suwayomi/update``

外加仪表盘静态页面（``/suwayomi/``）。处理逻辑复用 :mod:`web.api`
（与原插件相同的独立函数，返回 dict / (dict, status)）。
"""
from __future__ import annotations

import secrets
from pathlib import Path
from typing import Any, Awaitable, Callable

from nonebot import get_driver, logger

try:
    from fastapi import APIRouter, Request
    from fastapi.responses import FileResponse, JSONResponse

    FASTAPI_AVAILABLE = True
except ImportError:  # pragma: no cover
    FASTAPI_AVAILABLE = False

from .core import SuwayomiCore
from .suwayomi import PLUGIN_NAME
from .web.api import (
    api_config_get,
    api_config_post,
    api_sources,
    api_status,
    api_subscription_delete,
    api_subscription_push,
    api_subscriptions,
    api_update,
)

PAGES_DIR = Path(__file__).resolve().parent / "pages" / "dashboard"
API_PREFIX = "/suwayomi"


def register_web_routes(core: SuwayomiCore, dashboard_token: str = "") -> bool:
    """把路由挂到 nonebot FastAPI 应用；返回是否成功。"""
    if not str(dashboard_token or "").strip():
        logger.info("[suwayomi] 未设置 dashboard token，管理面板已禁用")
        return False
    if not FASTAPI_AVAILABLE:  # pragma: no cover
        logger.warning(f"[{PLUGIN_NAME}] 未安装 FastAPI 驱动，Web 面板不可用")
        return False

    driver = get_driver()
    app = getattr(driver, "server_app", None)
    if app is None:
        logger.warning(
            f"[{PLUGIN_NAME}] 当前驱动不提供 server_app，Web 面板不可用"
        )
        return False

    token = str(dashboard_token or "").strip()

    def _authorized(request: Request) -> bool:
        auth = request.headers.get("Authorization", "")
        return secrets.compare_digest(auth.encode(), f"Bearer {token}".encode())

    def _denied() -> JSONResponse:
        return JSONResponse(
            {"success": False, "message": "未授权：访问令牌无效"}, status_code=401
        )

    def _wrap(
        fn: Callable[..., Awaitable[Any]],
    ) -> Callable[[Request], Awaitable[JSONResponse]]:
        async def _route(request: Request) -> JSONResponse:
            if not _authorized(request):
                return _denied()
            try:
                data = await request.json()
            except Exception:
                data = None
            if data is not None and not isinstance(data, dict):
                return JSONResponse({"success": False, "message": "请求体必须是 JSON 对象"}, status_code=400)
            try:
                result = await fn(data)
            except Exception as exc:  # pragma: no cover
                logger.error(f"[{PLUGIN_NAME}] Web API 异常: {exc}")
                return JSONResponse(
                    {"success": False, "message": "操作失败，请查看服务日志"}, status_code=500
                )
            if isinstance(result, tuple):
                return JSONResponse(result[0], status_code=result[1])
            return JSONResponse(result)

        return _route

    router = APIRouter(prefix=API_PREFIX)

    # ── REST API ──────────────────────────────────────────────────

    @router.get("/status")
    async def _status(request: Request):
        if not _authorized(request):
            return _denied()
        return JSONResponse(await api_status(core.client, core.sub_mgr, core.get_kv_data))

    @router.get("/subscriptions")
    async def _subscriptions(request: Request):
        if not _authorized(request):
            return _denied()
        return JSONResponse(await api_subscriptions(core.client, core.sub_mgr))

    @router.post("/subscription/delete")
    async def _subscription_delete(request: Request):
        return await _wrap(
            lambda data: api_subscription_delete(core.sub_mgr, data or {})
        )(request)

    @router.post("/subscription/push")
    async def _subscription_push(request: Request):
        return await _wrap(
            lambda data: api_subscription_push(core.sub_mgr, data or {})
        )(request)

    @router.get("/config")
    async def _config_get(request: Request):
        if not _authorized(request):
            return _denied()
        return JSONResponse(api_config_get(core.config))

    @router.post("/config")
    async def _config_post(request: Request):
        async def _apply(data):
            result = await api_config_post(core.config, data or {}, core.rebuild_client)
            return result

        return await _wrap(_apply)(request)

    @router.get("/sources")
    async def _sources(request: Request):
        if not _authorized(request):
            return _denied()
        return JSONResponse(await api_sources(core.client))

    @router.post("/update")
    async def _update(request: Request):
        if not _authorized(request):
            return _denied()
        if not core._check_updates_fn:
            return JSONResponse(
                {"success": False, "summary": "更新引擎尚未就绪。"}, status_code=503
            )
        return JSONResponse(
            await api_update(core._check_updates_fn, core.put_kv_data)
        )

    # ── 仪表盘静态页面 ────────────────────────────────────────────

    def _page(name: str) -> Callable[[Request], Any]:
        async def _serve(request: Request):
            path = PAGES_DIR / name
            if not path.exists():
                return JSONResponse({"message": "not found"}, status_code=404)
            return FileResponse(path)

        return _serve

    router.add_api_route("/", _page("index.html"), methods=["GET"])
    router.add_api_route("/index.html", _page("index.html"), methods=["GET"])
    router.add_api_route("/app.js", _page("app.js"), methods=["GET"])
    router.add_api_route("/style.css", _page("style.css"), methods=["GET"])

    app.include_router(router)
    logger.info(
        f"[{PLUGIN_NAME}] Web 面板已挂载: {API_PREFIX}/ "
        f"{'（已启用访问令牌）' if token else '（未设置访问令牌）'}"
    )
    return True
