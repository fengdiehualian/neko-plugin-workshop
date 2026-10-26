"""新一轮检测（v0.16.0）：从 `bus.memory` 的轮询里读出"用户刚开了新一轮"。

**为什么是轮次而不是挂钟**：存在感注入原先挂在 60s timer 上按 `interval_sec`（默认
3600s）打点。实机 44 轮对话只覆盖到 12 次注入、3 次发表情——**提醒的覆盖率 27%**，
而不是文案不够狠。宿主 SDK 不向插件派发"助手回复完成"（`@message` 全仓无 emitter，
`turn_end` 走 main↔agent 的私有 ZeroMQ 总线），插件侧唯一可靠的回合边界代理是
`bus.memory` 里的 `user_message`：它标记的是"她即将开始回"，不是"她已经回完"——
早半拍到一秒，正好落在她拼下一条回复之前，这是插件能拿到的最好时机。

形态照抄同门 `n.e.k.o_plugin_forever_companion/mixins/whisper.py`（同一个问题在
这套 SDK 上已经被踩过一遍）：`ctx.bus.memory.get` **不可订阅只能轮询**；timer
handler scope 里同步调会触发宿主 `Sync call invoked inside handler` 告警且 ZMQ IPC
最多阻塞事件循环 1s，所以过 `asyncio.to_thread` 并对 awaitable 兜底 await。

纪律：
- 读失败**必须 warning 不能 debug**——同门原话："debug 级别不进日志文件会造成注入失效
  但零日志的盲区"；且 5 分钟节流，连珠炮对话不许刷满日志。
- 空桶与"被其他类型记录遮蔽"分开记心跳：这两种情况的处置完全不同（前者是没人说话，
  后者是桶形状变了）。
- 水位（`_seen`）按角色卡各自推进：多角色并行会话不许互相吞掉轮次。
- 计数（`_counts`）与水位是两把尺：计数只在注入**真正成功**后清零（`services/awareness.py`
  负责），被闸拦下时不消耗计数——否则实际频率会低于配置值。
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import math
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .lanlan import unwrap_record

logger = logging.getLogger("sticker_manager.turns")

# Host query cap; count all retained new turns, including interleaved roles.
_SCAN_RECORDS = 500
# 单次总线读的上限：它在 timer 里跑，卡住就是拖垮整拍。
_BUS_TIMEOUT_SEC = 1.0
# 同一条总线故障最多 5 分钟吼一次。
_ERROR_LOG_THROTTLE_SEC = 300.0
# 心跳留痕的节流：它是"注入为什么没发生"的唯一线索，不需要每拍都记。
_HEARTBEAT_LOG_THROTTLE_SEC = 300.0

_BUCKET_ID = "default"
_USER_MESSAGE_TYPE = "user_message"


@dataclass(frozen=True)
class UserTurn:
    """一条"用户开了新一轮"的事实。

    `lanlan` 是宿主写入的归属角色（缺失时调用方回落当前角色）；`text` 只在按内容
    决定注什么时才用得上，节奏判定不看它。
    """

    ts: float
    text: str
    lanlan: str
    is_voice: bool = False


def _float_of(value: Any) -> float:
    # 与 core._lenient_float 同纪律：isinstance 先拦字符串（时间戳是数字，不是"123"），
    # 但拦不住巨整数——float(10**400) 抬 OverflowError，坏值当 0 垫底，不许炸掉整拍。
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            number = float(value)
            return number if math.isfinite(number) else 0.0
        except OverflowError:
            return 0.0
    return 0.0


def user_turn_of(record: Any) -> UserTurn | None:
    """一条 bus 记录 → UserTurn；不是用户消息就回 None。

    时间戳先取 payload 的 `_ts`（宿主写入的原始字段），再退到 SDK 记录的
    `timestamp`——两处都坏时回 0.0，让这条永远垫底而不是插队成"最新一轮"。
    """
    raw = unwrap_record(record)
    if not isinstance(raw, Mapping) or str(raw.get("type") or "") != _USER_MESSAGE_TYPE:
        return None
    ts = _float_of(raw.get("_ts"))
    if ts <= 0.0:
        ts = _float_of(getattr(record, "timestamp", None))
    metadata = raw.get("metadata")
    owner = ""
    for source in (raw, metadata if isinstance(metadata, Mapping) else None):
        if isinstance(source, Mapping):
            owner = str(source.get("lanlan") or source.get("lanlan_name") or "").strip()
            if owner:
                break
    return UserTurn(
        ts=ts,
        text=str(raw.get("content") or ""),
        lanlan=owner,
        is_voice=bool(raw.get("is_voice")),
    )


def latest_user_turn(records: Any) -> UserTurn | None:
    """倒序找最新一条用户消息。

    必须倒序扫而不是盲取末条：桶里混着其他类型的记录，盲取会让用户消息被永久遮蔽。
    """
    seq = list(records) if isinstance(records, (list, tuple)) else []
    for record in reversed(seq):
        turn = user_turn_of(record)
        if turn is not None:
            return turn
    return None


class TurnWatcher:
    """Count every unseen retained turn; poll_all returns the full new batch.

    `poll()` 只在新轮次（水位前进）时返回对象；重复读到同一轮返回 None。
    `available` 是给注入侧的降级判据：总线一次都没读通过时，退回挂钟节奏，
    而不是让整条存在感链路静默死掉——本轮要修的正是"静默不发生"。
    """

    def __init__(self, plugin: Any, *, logger: Any = None, now: Any = None):
        self._plugin = plugin
        self._logger = logger
        self._now = now if callable(now) else time.monotonic
        self._seen: dict[str, float] = {}
        self._seen_at_watermark: dict[str, set[UserTurn]] = {}
        self._counts: dict[str, int] = {}
        # 本次运行累计见过的用户轮（分母，不归零）。
        self._turns_seen = 0
        self._available = False
        self._last_error_logged = 0.0
        self._last_heartbeat_logged = 0.0

    # --- 可观测面 -----------------------------------------------------------

    @property
    def available(self) -> bool:
        return self._available

    @property
    def turns_seen(self) -> int:
        """本次运行见过的用户轮总数（面板与日志的分母）。"""
        return self._turns_seen

    def turns_since(self, lanlan: str) -> int:
        return self._counts.get(lanlan, 0)

    def snapshot(self) -> dict[str, Any]:
        """面板展示用：驱动源是否活着 + 各卡距上次注入过了几轮。"""
        return {
            "source": "bus" if self._available else "unavailable",
            "turns_since_inject": dict(self._counts),
            "turns_seen": self._turns_seen,
            "latest_turn_ts": max(self._seen.values(), default=None),
        }

    def reset_count(self, lanlan: str) -> None:
        self._counts[lanlan] = 0

    # --- 节拍 ---------------------------------------------------------------

    async def poll(self) -> UserTurn | None:
        """Compatibility view: count the whole batch, return its latest turn."""
        turns = await self.poll_all()
        return turns[-1] if turns else None

    async def poll_all(self) -> list[UserTurn]:
        """Count every unseen retained message once, with independent role watermarks."""
        records = await self._read()
        if records is None:  # 读失败：不改动任何状态，也不标记 available
            return []
        self._available = True
        turns = [turn for record in records if (turn := user_turn_of(record)) is not None]
        if not turns:
            self._log_heartbeat(records)
            return []
        fresh: list[UserTurn] = []
        for turn in sorted(turns, key=lambda item: item.ts):
            target = turn.lanlan or self._current_lanlan()
            watermark = self._seen.get(target, 0.0)
            if not target or turn.ts <= 0.0 or turn.ts < watermark:
                continue
            resolved = UserTurn(ts=turn.ts, text=turn.text, lanlan=target, is_voice=turn.is_voice)
            if turn.ts == watermark and resolved in self._seen_at_watermark.get(target, set()):
                continue
            if turn.ts > watermark:
                self._seen_at_watermark[target] = set()
            self._seen_at_watermark[target].add(resolved)
            self._seen[target] = turn.ts
            self._counts[target] = self._counts.get(target, 0) + 1
            self._turns_seen += 1
            fresh.append(resolved)
        return fresh

    # --- 总线读（全 getattr 化：形状缺失降级不抛）---------------------------

    async def _read(self) -> list[Any] | None:
        bus = getattr(self._plugin, "bus", None)
        memory = getattr(bus, "memory", None) if bus is not None else None
        getter = getattr(memory, "get", None) if memory is not None else None
        if not callable(getter):
            self._log_error("bus.memory.get unavailable; turn polling disabled")
            return None
        try:
            # kwargs 必须逐字对齐 `SdkMemoryBus.get(*, bucket_id, limit, timeout)`：
            # 多一个参数（实机踩过：顺手抄了 conversations 的 `max_count`）就是每次读
            # 都 TypeError，被下面的 except 吞成"总线没信号"，症状是注入退回挂钟。
            # `bucket_id` 还是必填的，不能省。
            result = await asyncio.to_thread(
                getter,
                bucket_id=_BUCKET_ID,
                limit=_SCAN_RECORDS,
                timeout=_BUS_TIMEOUT_SEC,
            )
            if inspect.isawaitable(result):
                result = await result
        except Exception as exc:  # noqa: BLE001 - 总线不可用不许拖垮 timer 拍
            self._log_error(f"bus memory read failed: {exc}")
            return None
        error = getattr(result, "error", None)
        if result is None or error is not None:
            self._log_error(f"bus memory read returned error: {error}")
            return None
        try:
            return list(result)
        except TypeError:
            return []

    def _current_lanlan(self) -> str:
        ctx = getattr(self._plugin, "ctx", None)
        return str(getattr(ctx, "_current_lanlan", "") or "").strip()

    # --- 日志 ---------------------------------------------------------------

    def _log_error(self, message: str) -> None:
        now = self._now()
        if now - self._last_error_logged < _ERROR_LOG_THROTTLE_SEC:
            return
        self._last_error_logged = now
        self._emit("warning", f"turns: {message} (throttled 5min)")

    def _log_heartbeat(self, records: list[Any]) -> None:
        now = self._now()
        if now - self._last_heartbeat_logged < _HEARTBEAT_LOG_THROTTLE_SEC:
            return
        self._last_heartbeat_logged = now
        if not records:
            self._emit("info", "turns: bucket empty (no user message within TTL)")
            return
        raw = unwrap_record(records[-1])
        latest_type = str(raw.get("type") or "?") if isinstance(raw, Mapping) else "?"
        self._emit(
            "info",
            f"turns: no user_message in last {len(records)} records; latest_type={latest_type}",
        )

    def _emit(self, level: str, message: str) -> None:
        target = self._logger if self._logger is not None else logger
        try:
            log = getattr(target, level, None)
            if callable(log):
                log(message)
            else:
                target.info(message)
        except Exception:  # noqa: BLE001 - 日志面坏掉不许影响轮次检测
            pass
