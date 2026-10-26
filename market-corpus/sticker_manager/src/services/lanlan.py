"""她在跟谁说话——注入目标的解析链与总线记录扫描（v0.14.1 从这里搬出来独立成尺）。

链（依次，第一个非空即胜）：
1. 入口 `_ctx` 里的 `lanlan_name`——LLM 工具/角色侧调用才有；
2. 总线记录：`bus.conversations`（`lanlan_name`）与 `bus.memory`（`lanlan`）里时间戳最新的一条
   ——**每拍现读不缓存**：目标随对话漂，缓存它等于把她钉在上一张卡上；
3. 宿主 `GET /api/characters/current_catgirl`——"当前载入的角色卡"，稳定值，15s TTL
   （**失败也缓存**：宿主不可达时不该每次面板刷新都白等一个超时）；
4. `ctx._current_lanlan`——宿主侧"最近触发角色"的粘滞缓存，最后兜底。

为什么非要问宿主 HTTP（v0.14.0 实机踩出来的）：面板动作派发给插件的 `_ctx` 里只有
`run_id`（`plugin/server/application/plugins/ui_query_service.py:1757-1762`），而**普通聊天话轮
不写 conversations 存储**（只有主动离线轮次才 publish，`main_logic/omni_offline_client/_lifecycle.py:94`）
——只看总线的结果是面板「现在注一条」永远 `no_target`，玩家第一次测就撞这上面。

纪律：整条链 best-effort，任何一步炸了都只丢这一步（降级不抛）；找不到目标回空串，
调用方拿空串当"这拍不该做"。
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from collections.abc import Iterable, Mapping
from functools import partial
from typing import Any

from .local_http import read_local_json

logger = logging.getLogger("sticker_manager.lanlan")

# 快照读多少条：只为找"最近在跟谁说话"，不必翻整本历史。
_SCAN_RECORDS = 12
# 当前角色卡的缓存窗口：面板每次刷新都要这个值，但角色切换不是高频事件。
_CACHE_TTL_SEC = 15.0
# HTTP 超时：面板 context 的总预算是 5s，而这条只是第 3 级兜底——必须短。
_HTTP_TIMEOUT_SEC = 0.6
_DEFAULT_PORT = 48911
_CURRENT_ENDPOINT = "/api/characters/current_catgirl"
# memory 桶的必填入参：宿主只有一个默认桶，但参数不给就是 TypeError。
_BUCKET_ID = "default"
# 总线读的超时：这条链在面板 context（总预算 5s）里也会走，SDK 默认的 5s 会吃满预算。
_BUS_READ_TIMEOUT_SEC = 0.8

# 单测隔离开关（v0.15.0）：解析链的第 3 级会打宿主 loopback HTTP。宿主与测试可以同机
# 并存——不关掉时，"没有目标"的用例会真的问到运行中的宿主并拿到角色名，测试变成看天吃饭。
# 生产默认开；tests/conftest.py 在导入期把它关掉。
HTTP_ENABLED = True


def unwrap_record(record: Any) -> Any:
    """取回 bus 记录的原始 payload dict（一把尺，`services/turns.py` 共用）。

    SDK 包装层契约不一致（同门实机症状）：宿主 `dump_records()` 给的是**对象序列**而非
    dict，`SdkBusMemoryRecord.from_raw` 对非 Mapping 会包成 `{"value": <record>}`，
    把 `type` 字段吞掉。三层形态（宿主记录的 `.raw` / SDK 的 `.payload` / 裸 dict）都要兼容。
    """
    raw = getattr(record, "payload", None) or getattr(record, "raw", record)
    if isinstance(raw, Mapping) and set(raw) == {"value"}:
        inner = raw.get("value")
        raw = getattr(inner, "raw", None) or getattr(inner, "payload", None) or inner
    return raw


def records_of(raw: Any) -> list[Mapping[str, Any]]:
    """把 SDK 的 bus 列表对象规范成 dict 列表（兼容 dump_records / items / 可迭代）。

    每条都过 `unwrap_record`：不 unwrap 的话，SDK 给对象序列时这里会全数丢掉，
    于是"总线里有目标"在真机上读成"没有"——症状与传错 kwargs 一模一样，同样是静默。
    """
    if raw is None:
        return []
    dumper = getattr(raw, "dump_records", None)
    if callable(dumper):
        try:
            return [item for item in (unwrap_record(r) for r in dumper()) if isinstance(item, Mapping)]
        except Exception:  # noqa: BLE001 - 形状不认识就当没有
            pass
    items = getattr(raw, "items", None)
    if isinstance(items, (list, tuple)):
        return [item for item in (unwrap_record(r) for r in items) if isinstance(item, Mapping)]
    if isinstance(raw, (list, tuple)):
        return [item for item in (unwrap_record(r) for r in raw) if isinstance(item, Mapping)]
    return []


def lanlan_of(record: Mapping[str, Any]) -> str:
    """一条轮次记录里的角色卡名。

    两个存储的键名不一样：conversations 记 `lanlan_name`，memory 桶记 `lanlan`
    （forever_companion 走的就是后者）；再退一层到 metadata。取到即止。
    """
    for source in (record, record.get("metadata") if isinstance(record.get("metadata"), Mapping) else None):
        if not isinstance(source, Mapping):
            continue
        for key in ("lanlan_name", "lanlan"):
            name = source.get(key)
            if isinstance(name, str) and name.strip():
                return name.strip()
    return ""


def _timestamp_of(record: Mapping[str, Any]) -> float:
    """两个桶的时间戳键不一样：conversations 记 `timestamp`，memory 桶记 `_ts`。"""
    raw_ts = record.get("_ts")
    if raw_ts is None:
        raw_ts = record.get("timestamp")
    # 与 core._lenient_float 同纪律：isinstance 拦住字符串，但拦不住巨整数——
    # float(10**400) 抬 OverflowError；坏时间戳当 0 垫底，不能炸掉整拍扫描。
    if isinstance(raw_ts, (int, float)) and not isinstance(raw_ts, bool):
        try:
            return float(raw_ts)
        except OverflowError:
            return 0.0
    return 0.0


def latest_lanlan(records: Iterable[Mapping[str, Any]]) -> str:
    """纯函数：从轮次记录里挑"时间戳最新的角色卡"；没有就回空串。

    时间戳缺失/坏值当 0（垫底）——有比没有好，但不能让坏数据插队到"最新"。
    """
    best_name = ""
    best_ts = -math.inf  # 常量哨兵；不用 float("-inf") 写法——字面即意，也不招扫描器误报
    for record in records:
        if not isinstance(record, Mapping):
            continue
        name = lanlan_of(record)
        if not name:
            continue
        ts = _timestamp_of(record)
        if ts > best_ts:
            best_ts, best_name = ts, name
    return best_name


class LanlanResolver:
    """按上面的链解析目标；带 15s 缓存，面板刷新与心跳共用一个实例。"""

    def __init__(self, plugin: Any, *, logger: Any = None):
        self._plugin = plugin
        self._logger = logger
        self._cached: tuple[str, float] = ("", 0.0)

    def _log(self, message: str, *, level: str = "info") -> None:
        target = self._logger if self._logger is not None else logger
        try:
            emitter = getattr(target, level, None) if level != "info" else target.info
            if callable(emitter):
                emitter(message)
            else:
                target.info(message)
        except Exception:  # noqa: BLE001 - 日志不该带走解析
            pass

    async def resolve(self, hint: str = "") -> str:
        """入口 ctx 有就用；否则**先读总线**（目标会随对话漂，不缓存），
        再退到宿主 HTTP 的"当前角色卡"（稳定值，带 TTL 缓存，失败也缓存）。
        """
        name = (hint or "").strip()
        if name:
            return name
        from_bus = await self._from_bus()
        if from_bus:
            return from_bus
        return await self._from_host() or self._from_ctx()

    # --- 宿主 HTTP（权威，但只在没记录时问一次）------------------------------

    def _api_base(self) -> str:
        port = _DEFAULT_PORT
        try:
            from config import MAIN_SERVER_PORT  # 宿主在进程内可解析；独立/测试环境拿不到就用缺省端口

            port = int(MAIN_SERVER_PORT)
        except Exception:  # noqa: BLE001 - 缺省端口即可，不是事故
            pass
        return f"http://127.0.0.1:{port}"

    def _fetch_blocking(self) -> str:
        payload = read_local_json(self._api_base() + _CURRENT_ENDPOINT, timeout=_HTTP_TIMEOUT_SEC)
        if not isinstance(payload, Mapping):
            return ""
        return str(payload.get("current_catgirl") or "").strip()

    async def _from_host(self) -> str:
        if not HTTP_ENABLED:  # 测试态：不碰 loopback，交给下一级（见模块常量注释）
            return ""
        # 失败也缓存：宿主不可达时，每次面板刷新都白等一个超时是纯粹的折磨。
        name, at = self._cached
        if time.monotonic() - at < _CACHE_TTL_SEC:
            return name
        try:
            resolved = await asyncio.to_thread(self._fetch_blocking)
        except Exception:  # noqa: BLE001 - 端口不对/宿主没起/结构变了：降级到粘滞缓存
            self._log("lanlan: current_catgirl unreachable")
            resolved = ""
        self._cached = (resolved, time.monotonic())
        return resolved

    # --- 总线与粘滞缓存 ------------------------------------------------------

    def _from_ctx(self) -> str:
        ctx = getattr(self._plugin, "ctx", None)
        return str(getattr(ctx, "_current_lanlan", "") or "").strip()

    async def _from_bus(self) -> str:
        """两个桶**各按各的签名**读：kwargs 传错就是每次抛 TypeError、被 except 吞成
        "总线里没有目标"（实机踩过：v0.14.1 起这一级其实一直是死的，全靠下一级 HTTP 兜住）。
        `bus.conversations` = `get(*, conversation_id, max_count, since_ts, timeout)`，
        `bus.memory` = `get(*, bucket_id, limit, timeout)`——名字没有交集。
        """
        bus = getattr(self._plugin, "bus", None)
        reads: list[tuple[Any, dict[str, Any]]] = [
            (
                getattr(bus, "conversations", None),
                {"max_count": _SCAN_RECORDS, "timeout": _BUS_READ_TIMEOUT_SEC},
            ),
            (
                getattr(bus, "memory", None),
                {
                    "bucket_id": _BUCKET_ID,
                    "limit": _SCAN_RECORDS,
                    "timeout": _BUS_READ_TIMEOUT_SEC,
                },
            ),
        ]
        for namespace, kwargs in reads:
            getter = getattr(namespace, "get", None)
            if not callable(getter):
                continue
            try:
                # to_thread 包一层：面板/注入的 handler scope 里同步调总线会触发宿主
                # "Sync call invoked inside handler"，ZMQ IPC 下还会阻塞事件循环。
                raw: Any = await asyncio.to_thread(partial(getter, **kwargs))
                if hasattr(raw, "__await__"):
                    raw = await raw
            except Exception as exc:  # noqa: BLE001 - 一次读失败只丢这个命名空间
                self._log(f"lanlan: bus read failed ({type(exc).__name__}: {exc})", level="warning")
                continue
            name = latest_lanlan(records_of(raw))
            if name:
                return name
        return ""
