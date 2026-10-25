"""JSON 文件键值存储。

AstrBot 插件自带 `plugin.get_kv_data / put_kv_data` 持久化接口，
NoneBot2 没有等价内置设施，这里用 JSON 文件实现同签名的 KV 存储，
供订阅数据、章节缓存时间戳、上次检查时间等使用。
"""
from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from .persistence import write_json


class JsonKvStore:
    """进程内 JSON 文件 KV 存储（异步接口，写入加锁）。"""

    def __init__(self, path: Path):
        self._path = Path(path)
        self._lock = asyncio.Lock()
        self._cache: dict[str, Any] | None = None

    # ── storage io ────────────────────────────────────────────────

    def _read_file(self) -> dict[str, Any]:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        if not isinstance(raw, dict):
            raise ValueError("Suwayomi KV 文件必须为 JSON 对象；原文件保留")
        return raw

    def _write_file(self, data: dict[str, Any]) -> None:
        write_json(self._path, data)

    async def _load(self) -> dict[str, Any]:
        if self._cache is None:
            self._cache = await asyncio.get_running_loop().run_in_executor(
                None, self._read_file
            )
        return self._cache

    async def _save(self, data: dict[str, Any]) -> None:
        await asyncio.get_running_loop().run_in_executor(
            None, self._write_file, data
        )
        self._cache = data

    # ── public api (与 AstrBot plugin KV 同签名) ──────────────────

    async def get_kv_data(self, key: str, default: Any = None) -> Any:
        async with self._lock:
            data = await self._load()
            return deepcopy(data.get(key, default))

    async def put_kv_data(self, key: str, value: Any) -> None:
        async with self._lock:
            data = deepcopy(await self._load())
            data[key] = deepcopy(value)
            await self._save(data)

    async def delete_kv_data(self, key: str) -> None:
        async with self._lock:
            data = deepcopy(await self._load())
            if key in data:
                data.pop(key, None)
                await self._save(data)
