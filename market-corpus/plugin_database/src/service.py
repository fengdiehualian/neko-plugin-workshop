"""适配器生命周期与跨插件入口载荷编解码。

入口层（``__init__.py`` 里由脚手架生成的插件类）只做薄封装：
参数进来 -> ``decode_params`` -> 适配器执行 -> ``encode_rows`` 出去。
这样跨插件 IPC 的 JSON 序列化不会丢字节（embedding BLOB 用 base64 包装）。
"""

from __future__ import annotations

import base64
import sys
from pathlib import Path
from typing import Any

from . import hot_schema
from .adapters import ADAPTERS, AdapterError, DatabaseAdapter

_BYTES_KEY = "__bytes_b64__"


def ensure_vendor_path(plugin_dir: str | Path) -> bool:
    """把 ``<plugin_dir>/vendor`` 加进 sys.path（幂等）。返回是否存在 vendor 目录。"""
    vendor = Path(plugin_dir) / "vendor"
    if not vendor.is_dir():
        return False
    s = str(vendor)
    if s not in sys.path:
        sys.path.insert(0, s)
    return True


def encode_value(value: Any) -> Any:
    """bytes -> {"__bytes_b64__": ...}，其余透传；递归处理容器。"""
    if isinstance(value, (bytes, bytearray)):
        return {_BYTES_KEY: base64.b64encode(bytes(value)).decode("ascii")}
    if isinstance(value, tuple):
        return [encode_value(v) for v in value]
    if isinstance(value, list):
        return [encode_value(v) for v in value]
    if isinstance(value, dict):
        return {k: encode_value(v) for k, v in value.items()}
    return value


def decode_value(value: Any) -> Any:
    """{"__bytes_b64__": ...} -> bytes；其余透传；递归处理容器。"""
    if isinstance(value, dict):
        if set(value.keys()) == {_BYTES_KEY} and isinstance(value[_BYTES_KEY], str):
            return base64.b64decode(value[_BYTES_KEY])
        return {k: decode_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [decode_value(v) for v in value]
    return value


def decode_params(params: Any) -> tuple:
    """入口层收到 JSON 数组形态的参数 -> 适配器用的 tuple。"""
    if params is None:
        return ()
    if isinstance(params, tuple):
        return tuple(decode_value(p) for p in params)
    if isinstance(params, list):
        return tuple(decode_value(p) for p in params)
    return (decode_value(params),)


class DatabaseService:
    """持有当前适配器，负责 driver 选择、连接与关闭。"""

    def __init__(self, data_dir=None) -> None:
        self._adapter: DatabaseAdapter | None = None
        self._driver: str = ""
        self._data_dir = data_dir

    @property
    def adapter(self) -> DatabaseAdapter:
        if self._adapter is None:
            raise AdapterError("database adapter not started")
        return self._adapter

    @property
    def driver(self) -> str:
        return self._driver

    # ------------------------------------------------------------------

    def _sqlite_default_path(self, config: dict) -> str:
        if self._data_dir is not None:
            return str(self._data_dir / "plugin_database.db")
        return str(config.get("path") or "plugin_database.db")

    async def start(self, database_cfg: dict) -> dict:
        """按 [database] 配置段选择并连接驱动。返回连接描述。

        先连接新适配器，成功后才关掉旧适配器：切换失败（DSN 不可达、依赖缺失）
        时旧连接原样保留，``db_status`` 仍与可用状态一致。
        """
        cfg = dict(database_cfg or {})
        driver = str(cfg.get("driver", "sqlite")).strip().lower()
        factory = ADAPTERS.get(driver)
        if factory is None:
            raise AdapterError(
                f"unknown database driver {driver!r}; expected one of {sorted(ADAPTERS)}"
            )
        sub = dict(cfg.get(driver) or {})
        if driver == "sqlite" and not sub.get("path") and self._data_dir is not None:
            sub["path"] = self._sqlite_default_path(sub)
        adapter = factory()
        try:
            await adapter.connect(sub)
        except BaseException:
            # 连接失败：新适配器没建起来，旧适配器必须原样可用。
            await self._quiet_close(adapter)
            raise
        previous = self._adapter
        self._adapter = adapter
        self._driver = driver
        if previous is not None:
            await self._quiet_close(previous)
        return {"driver": driver}

    @staticmethod
    async def _quiet_close(adapter: DatabaseAdapter) -> None:
        """关闭适配器并吞掉错误（切换路径不能在旧连接上二次失败）。"""
        try:
            await adapter.close()
        except Exception:
            pass

    async def stop(self) -> None:
        adapter, self._adapter, self._driver = self._adapter, None, ""
        if adapter is not None:
            await adapter.close()

    # ------------------------------------------------------------------

    async def execute(self, sql: str, params: Any) -> int:
        return await self.adapter.execute(sql, decode_params(params))

    async def fetch_all(self, sql: str, params: Any) -> list[dict]:
        return [encode_value(r) for r in await self.adapter.fetch_all(sql, decode_params(params))]

    async def fetch_one(self, sql: str, params: Any) -> dict | None:
        row = await self.adapter.fetch_one(sql, decode_params(params))
        return encode_value(row) if row is not None else None

    # 事务说明：transaction() 的异步上下文管理器只能在持有适配器对象的进程内
    # 使用（跨插件 IPC 无法传递上下文管理器）。跨插件消费方使用 execute/fetch
    # 入口；需要事务的知识库写入走 INSERT OR REPLACE 单语句幂等写。


__all__ = [
    "DatabaseService",
    "decode_params",
    "decode_value",
    "encode_value",
    "hot_schema",
]
