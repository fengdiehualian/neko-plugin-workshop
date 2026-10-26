"""mod TCP 遥测接收端：渔力全开 BepInEx mod 事件流 + 状态快照。

协议（JSON 行，照 neko_terraria bridge）：
  事件:  {"type":"event","event":"caught","fish":"Cod","weight":3.2,...}
  状态:  {"type":"state","money":1234,"island":2,"bait":"worm",...}
  图鉴:  {"type":"registry","creatures":[...],"baits":[...]}
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Callable, Dict, Optional

log = logging.getLogger("neko_fishpower.telemetry")


class TelemetryClient:
    """管理 mod TCP 连接（9877）：独立读循环，事件/状态/图鉴分发。"""

    def __init__(self, host: str, port: int,
                 on_event: Callable[[str, Dict[str, Any]], None],
                 on_state: Callable[[Dict[str, Any]], None],
                 on_registry: Callable[[Dict[str, Any]], None],
                 logger: Any = None) -> None:
        self.host = host
        self.port = port
        self._on_event = on_event
        self._on_state = on_state
        self._on_registry = on_registry
        self._logger = logger or log
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._task: Optional[asyncio.Task] = None
        self.connected = False
        self.last_error = ""

    async def connect(self) -> bool:
        """连接 mod TCP 服务（超时 2s）。"""
        try:
            r, w = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port, limit=8 * 1024 * 1024),
                timeout=2.0)
            self._reader, self._writer = r, w
            self.connected = True
            self.last_error = ""
            self._logger.info("telemetry 已连接 %s:%d", self.host, self.port)
            return True
        except (OSError, asyncio.TimeoutError) as exc:
            self.connected = False
            self.last_error = str(exc)
            self._logger.warning("telemetry 连接失败 %s:%d: %s", self.host, self.port, exc)
            return False
        except Exception as exc:
            self.connected = False
            self.last_error = f"{type(exc).__name__}: {exc}"
            self._logger.warning("telemetry 连接异常: %s", exc)
            return False

    def start(self) -> None:
        """启动读循环（重连交给上层 tick）。"""
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._read_loop())

    def close(self) -> None:
        if self._task:
            self._task.cancel()
            self._task = None
        if self._writer:
            try:
                self._writer.close()
            except Exception:
                pass
        self._reader = None
        self._writer = None
        self.connected = False

    async def _read_loop(self) -> None:
        """持续读行，按 type 分发。"""
        while True:
            if not self._reader:
                await asyncio.sleep(0.5)
                continue
            try:
                line = await self._reader.readline()
            except asyncio.CancelledError:
                raise
            except (ConnectionResetError, BrokenPipeError, OSError) as exc:
                self.connected = False
                self.last_error = f"连接断开: {exc}"
                self._logger.warning("telemetry 连接断开: %s", exc)
                break
            if not line:
                self.connected = False
                self.last_error = "mod 关闭连接（游戏退出？）"
                self._logger.warning("telemetry 读空行：mod 关闭连接")
                break
            try:
                msg = json.loads(line.decode("utf-8"))
                if not isinstance(msg, dict):
                    continue
                mtype = msg.get("type", "")
                if mtype == "event":
                    self._on_event(str(msg.get("event", "")), msg)
                elif mtype == "state":
                    self._on_state(msg)
                elif mtype == "registry":
                    self._on_registry(msg)
            except json.JSONDecodeError:
                continue
            except Exception as exc:
                log.warning("分发异常: %s", exc)
        # 退出：清理
        if self._writer:
            try:
                self._writer.close()
            except Exception:
                pass
        self._writer = None
        self._reader = None
