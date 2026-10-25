"""卡片渲染器（HTML 模板 → 图片）。

与原 AstrBot 插件 ``html_render(tmpl, data, return_url, options)`` 保持同签名：

- ``system``：本地 Jinja2 渲染模板 + ``nonebot-plugin-htmlrender``
  （Playwright 截图）输出图片。需要额外安装
  ``nonebot-plugin-htmlrender`` 与 Playwright 浏览器。
- ``custom``：调用自建 T2I 服务（astrbot-t2i-service 兼容协议），
  即 :mod:`suwayomi.t2i` 的 ``make_endpoint_renderer``。

渲染失败统一抛异常，由调用方回退纯文本。
"""
from __future__ import annotations

import tempfile
from collections.abc import Awaitable, Callable
from .suwayomi.t2i import make_endpoint_renderer, normalize_endpoint

PLUGIN_TAG = "suwayomi_t2i"

# cards.render_card 的 options.device_scale_factor_level → 实际倍率
_SCALE_LEVELS = {"ultra": 1.8, "high": 1.5, "normal": 1.0}

_renderer_cache: dict[str, Callable[..., Awaitable[str]]] = {}


def _render_options(options: dict | None) -> dict:
    opts = dict(options or {})
    scale = _SCALE_LEVELS.get(str(opts.get("device_scale_factor_level", "normal")), 1.0)
    return {
        "type": opts.get("type", "jpeg"),
        "quality": int(opts.get("quality", 95)),
        "viewport": {
            "width": int(opts.get("viewport_width", 880)),
            "height": int(opts.get("viewport_height", 100)),
        },
        "device_scale_factor": scale,
    }


def make_system_renderer(temp_dir: str | None = None) -> Callable[..., Awaitable[str]]:
    """返回基于 nonebot-plugin-htmlrender 的本地渲染器。

    使用 html_to_pic 兼容 API，支持 0.7.x 与保留该接口的后续版本。
    """

    async def _rasterize(html: str, opts: dict) -> tuple[bytes, str]:
        fmt = "jpeg" if opts["type"] in ("jpeg", "jpg") else "png"
        # The compatibility API accepts the same arguments on 0.7.x, v2 and
        # later releases. render_html itself has incompatible signatures and
        # may return either raw bytes or a result object across forks.
        try:
            from nonebot_plugin_htmlrender import html_to_pic
        except ImportError as exc:
            raise RuntimeError("htmlrender 未提供 html_to_pic 兼容 API") from exc

        image = await html_to_pic(
            html=html,
            type=opts["type"],  # type: ignore[arg-type]
            quality=opts["quality"] if fmt == "jpeg" else None,
            viewport=opts["viewport"],
            device_scale_factor=opts["device_scale_factor"],
        )
        return image, ".png" if fmt == "png" else ".jpg"

    async def _render(
        tmpl: str,
        data: dict,
        return_url: bool = False,
        options: dict | None = None,
    ) -> str:
        if return_url:
            raise NotImplementedError("本地 T2I 渲染仅支持返回本地文件")

        try:
            from jinja2 import Template
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("缺少 jinja2 依赖，无法本地渲染卡片") from exc

        try:
            import nonebot_plugin_htmlrender  # noqa: F401
        except ImportError as exc:
            raise RuntimeError(
                "未安装 nonebot-plugin-htmlrender，无法使用系统卡片渲染；"
                "请安装或改用「单独配置」T2I 服务"
            ) from exc

        html = Template(tmpl).render(**(data or {}))
        opts = _render_options(options)
        image, suffix = await _rasterize(html, opts)
        with tempfile.NamedTemporaryFile(
            prefix="suwayomi_card_", suffix=suffix, delete=False, dir=temp_dir
        ) as fh:
            fh.write(image)
            return fh.name

    return _render


def get_renderer(
    t2i_source: str, t2i_endpoint: str, temp_dir: str | None = None
) -> Callable[..., Awaitable[str]] | None:
    """按配置挑选渲染器；返回 None 表示不可用（调用方回退纯文本）。"""
    source = str(t2i_source or "system").strip().lower()
    endpoint = normalize_endpoint(t2i_endpoint)

    if source == "custom" and endpoint:
        key = f"custom:{endpoint}:{temp_dir}"
        if key not in _renderer_cache:
            _renderer_cache[key] = make_endpoint_renderer(endpoint, temp_dir=temp_dir)
        return _renderer_cache[key]

    # 自定义端点为空时回退系统渲染（警告由 core 一次性输出）

    key = f"system:{temp_dir}"
    if key not in _renderer_cache:
        _renderer_cache[key] = make_system_renderer(temp_dir)
    return _renderer_cache[key]


def reset_renderer_cache() -> None:
    """配置变更后清空渲染器缓存（切换端点/来源时生效）。"""
    _renderer_cache.clear()
