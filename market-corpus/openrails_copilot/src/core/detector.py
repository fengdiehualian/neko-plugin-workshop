"""事件判定：Snapshot → 候选事件（Candidate）。

本模块只做"从数据里看出发生了什么"，**不做节流、不做路由**——那是 arbiter 与
push_sender 的职责。这样分层的好处：判定规则可以单独用历史快照回放验证，
不受限流状态干扰。

只为本目录（`event_catalog.CATALOG`）里的事件产生候选。**没有数据源的事件
一条都不发**——宁可少报，不可假报。

判定口径里几处刻意的选择
----------------------
* 超速分级**只用 OR 原生轨道颜色**，不自算百分比（用户决定 2026-09-20）。
* 红灯"速度未降"**用距离窗口内两次速度快照比较，不算减速度**（用户指定）：
  HUD 刷新率不足以支撑无噪的 m/s² 微分。
* 需要现场校准的规则（`SIG_INCONSISTENT` / `GAUGE_BRAKE_NOT_RELEASED` /
  `SHUNT_OVERSPEED`）挂在 `*_ENABLED` 开关下，默认关闭并注明原因——
  先能跑通，再逐条标定。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from .constants import (
    RED_APPROACH_EXIT_M,
    RED_SIGNAL_SAFE_DISTANCE_M,
    SHUNT_OVERSPEED_KMH,
    SHUNT_SAFE_DISTANCE_M,
    SPEED_DROP_TOLERANCE_KMH,
)
from .event_catalog import (
    EventSpec,
)
from .event_catalog import (
    spec as _spec,
)
from .snapshot import (
    ASPECT_RANK,
    OUT_OF_CONTROL_REASONS,
    Snapshot,
)

_NULL_LOGGER = logging.getLogger("openrails_copilot.detector")
_NULL_LOGGER.addHandler(logging.NullHandler())

# ── 校准开关：默认关，现场标定后再开 ────────────────────────────────────
ENABLE_SIG_INCONSISTENT = False
"""机车信号 vs 地面信号一致性。`ASPECT_DISPLAY` 量程仅 0..1、粒度粗，
与 TrackMonitor 的 10 档 aspect 无法直接对齐，需要先摸清它的编码。"""

ENABLE_GAUGE_BRAKE_NOT_RELEASED = False
"""'制动未缓解'需要推断'本应缓解'。判据依赖 CP_HANDLE 在牵引位 + BC 仍高，
但不同机车 CP_HANDLE 的零点/量程不一，需实测标定。"""

ENABLE_SHUNT_OVERSPEED = False
"""'处于调车状态'没有直接信号，需由控制模式 + 道岔语境推断，需实测标定。"""

ENABLE_SIG_INCONSISTENT_ASPECT_MAP: dict[int, str] = {}
"""ASPECT_DISPLAY 数值 → aspect 名的映射（标定后填）。"""

# ── HUD 制动压力解析 ────────────────────────────────────────────────────
# 实测原文形如：
#   "持续制动 EQ 441 千帕 BC 430 千帕 BP 441 千帕 EOT  BC 430 千帕 BP 441 千帕"
#   "缓解 EQ 600 千帕 BC 0 千帕 BP 600 千帕 EOT  BC 0 千帕 BP 600 千帕"
# 注意 BC/BP 在主段与 EOT 段各出现一次，必须先切掉 EOT 段再解析主段。
_BRAKE_EOT_RE = re.compile(r"EOT\s*BC\s*(\d+(?:\.\d+)?)\s*(?:千帕|kPa)\s*BP\s*(\d+(?:\.\d+)?)")
_BRAKE_MAIN_RE = {
    "eq": re.compile(r"EQ\s*(\d+(?:\.\d+)?)"),
    "bc": re.compile(r"BC\s*(\d+(?:\.\d+)?)"),
    "bp": re.compile(r"BP\s*(\d+(?:\.\d+)?)"),
}

# 阈值（压力类，单位 kPa）。这些是工程经验值，不是 OR 参数。
_BP_DROP_THRESHOLD_KPA = 30.0      # 列车管压力单次下降超过此值视为异常
_BC_STUCK_THRESHOLD_KPA = 50.0     # 制动缸仍有此压力以上视为未缓解
_MAIN_RES_LOW_KPA = 700.0          # 总风缸低压门限
_MAIN_RES_HIGH_KPA = 1000.0        # 总风缸高压门限
_LINE_VOLTAGE_LOW_KV = 19.0        # 网压下限（25kV 制式）
_LINE_VOLTAGE_HIGH_KV = 29.0       # 网压上限
_BP_DEVIATION_KPA = 20.0           # 列尾/主段 BP 偏差门限

# 临时限速的三个提醒档（米）：靠上界判定，避免同一限速点连发三次
_TEMP_LIMIT_TIERS: tuple[tuple[str, float, float], ...] = (
    ("SPD_TEMP_LIMIT_2KM", 2000.0, 2500.0),
    ("SPD_TEMP_LIMIT_1500", 1500.0, 1800.0),
    ("SPD_TEMP_LIMIT_1000", 1000.0, 1300.0),
)

_YELLOW_ASPECTS = frozenset({"Approach_1", "Approach_2", "Approach_3"})

#: 脱控原因 → (event_id, 提示语)
_OUT_OF_CONTROL_EVENTS: dict[str, tuple[str, str]] = {
    "SPAD": ("EMERG_OUT_OF_CONTROL_SPAD", "冒进信号了！立即停车！立即制动！"),
    "SPAD-Rear": ("EMERG_OUT_OF_CONTROL_SPAD", "尾部冒进信号！注意尾部！"),
    "Misalg Sw": ("EMERG_OUT_OF_CONTROL_MISALIGNED_SWITCH", "道岔错位！立即停车！"),
    "Off Auth": ("EMERG_OUT_OF_CONTROL_OFF_AUTH", "越权运行！立即停车确认！"),
    "Off Path": ("EMERG_OUT_OF_CONTROL_OFF_AUTH", "偏离径路！立即停车！"),
    "Splipped": ("EMERG_OUT_OF_CONTROL_OFF_AUTH", "滑入径路！注意！"),
    "Slipped": ("EMERG_OUT_OF_CONTROL_OFF_AUTH", "滑向尽头线！立即制动！"),
    "Off Track": ("EMERG_OUT_OF_CONTROL_OFF_TRACK", "脱线了！立即停车！"),
    "Slip Turn": ("EMERG_OUT_OF_CONTROL_OFF_AUTH", "滑入转盘！注意！"),
    "Undefined": ("EMERG_OUT_OF_CONTROL_OFF_AUTH", "脱控！注意确认行车状态！"),
}


def parse_brake_hud(text: str) -> dict[str, float]:
    """从 HUD "列车制动" 单元格解析压力值（kPa）。缺失的键不出现。"""
    out: dict[str, float] = {}
    if not text:
        return out
    m = _BRAKE_EOT_RE.search(text)
    head = text
    if m:
        out["eot_bc"] = float(m.group(1))
        out["eot_bp"] = float(m.group(2))
        head = text[: m.start()]
    for key, pat in _BRAKE_MAIN_RE.items():
        mm = pat.search(head)
        if mm:
            out[key] = float(mm.group(1))
    return out


@dataclass(frozen=True)
class Candidate:
    """一条"检测到了"的候选事件。是否真的报出去由 arbiter 决定。"""

    event_id: str
    message: str
    spec: EventSpec

    @property
    def severity(self) -> str:
        return self.spec.severity

    @property
    def priority(self) -> int:
        return self.spec.priority

    def __str__(self) -> str:
        return f"[{self.spec.severity}] {self.event_id}: {self.message}"


class Detector:
    """有状态的判定器。

    之所以要有状态：`SIG_RED_STOP` 的判据是"距离窗口内速度没降"，需要记住
    进入窗口那一刻的速度；`*_CHANGE` / `*_DROP` 类需要上一帧。
    """

    def __init__(self, logger: Any = None) -> None:
        self.logger = logger if logger is not None else _NULL_LOGGER
        # 红灯接近窗口：{"aspect": str, "enter_dist": float, "enter_speed": float|None}
        self._red_window: dict[str, Any] | None = None

    def reset(self) -> None:
        self._red_window = None

    # ── 场景分类（供 arbiter 门控）─────────────────────────────────────
    def classify_scenario(self, snap: Snapshot) -> str:
        """由快照推断作业场景。

        这是用户明确要求的那部分——"停站会保持制动"、"单机/连挂/换端作业"。
        场景决定哪些类别整体静默，比逐条调阈值更可靠。
        """
        from .arbiter import SC_NORMAL, SC_SHUNTING, SC_STATION_STOP, SC_STOPPED

        speed = snap.speed_kmh
        if speed is None:
            return SC_NORMAL
        stationary = abs(speed) < 0.5
        if not stationary:
            # 低速 + 前方有面向道岔 ⇒ 视为调车语境
            if abs(speed) <= SHUNT_OVERSPEED_KMH and (snap.items("switch_left") or snap.items("switch_right")):
                return SC_SHUNTING
            return SC_NORMAL

        # 已停：站台就在跟前 ⇒ 停站保压；否则泛化静止
        station = snap.nearest("station")
        if station is not None and (station.distance_m or 1e9) <= 200.0:
            return SC_STATION_STOP
        return SC_STOPPED

    # ── 主入口 ─────────────────────────────────────────────────────────
    def detect(self, snap: Snapshot, prev: Snapshot | None = None) -> list[Candidate]:
        found: list[Candidate] = []
        for fn in (
            self._detect_out_of_control,
            self._detect_signal,
            self._detect_speed,
            self._detect_shunt,
            self._detect_gauge,
            self._detect_emergency,
            self._detect_parking,
            self._detect_state,
        ):
            try:
                found.extend(fn(snap, prev))
            except Exception as exc:  # 单条规则炸掉不能拖垮整个采样
                self.logger.warning("detector rule %s failed: %s: %s", fn.__name__, type(exc).__name__, exc)
        return found

    # ── 应急：OR 权威脱控判定 ──────────────────────────────────────────
    def _detect_out_of_control(self, snap: Snapshot, prev: Snapshot | None) -> list[Candidate]:
        if not snap.out_of_control:
            return []
        reason = snap.out_of_control_reason or "Undefined"
        event_id, text = _OUT_OF_CONTROL_EVENTS.get(reason, _OUT_OF_CONTROL_EVENTS["Undefined"])
        zh = OUT_OF_CONTROL_REASONS.get(reason, reason)
        return [self._cand(event_id, f"{text}（OR 判定：{zh}）")]

    # ── 信号类 ─────────────────────────────────────────────────────────
    def _detect_signal(self, snap: Snapshot, prev: Snapshot | None) -> list[Candidate]:
        out: list[Candidate] = []
        aspect = snap.signal_aspect
        if aspect is None:
            self._red_window = None
            return out
        dist = snap.signal_distance_m
        speed = snap.speed_kmh

        if aspect == "Stop":
            out.extend(self._signal_red(snap, dist, speed))
        else:
            self._red_window = None
            if aspect in _YELLOW_ASPECTS and dist is not None and dist <= 800.0:
                out.append(self._cand(
                    "SIG_YELLOW_APPROACH",
                    f"前方信号——黄灯，注意控速（{dist:.0f} 米）",
                ))
                if aspect == "Approach_2":
                    out.append(self._cand(
                        "SIG_DOUBLE_YELLOW",
                        f"双黄灯——侧向限速，注意道岔（{dist:.0f} 米）",
                    ))

        if prev is not None and prev.signal_aspect and prev.signal_aspect != aspect:
            before = ASPECT_RANK.get(prev.signal_aspect, -1)
            after = ASPECT_RANK.get(aspect, -1)
            if after < before:   # 只报降级；升级不必吵
                out.append(self._cand(
                    "SIG_UPGRADE_CHANGE",
                    f"信号变化——{prev.signal_aspect} → {aspect}，注意确认",
                ))

        if ENABLE_SIG_INCONSISTENT:
            out.extend(self._signal_consistency(snap))
        return out

    def _signal_red(self, snap: Snapshot, dist: float | None, speed: float | None) -> list[Candidate]:
        """红灯判据。速度"未降"用距离窗口内两次快照比较（用户指定口径）。"""
        out: list[Candidate] = []
        if dist is None:
            return out

        if dist > RED_SIGNAL_SAFE_DISTANCE_M:
            # 还没进窗口，先记录/清空
            self._red_window = None
            out.append(self._cand("SIG_RED_APPROACH", f"红灯停车！立即制动（距 {dist:.0f} 米）"))
            return out

        # 进入窗口：记下这一刻的速度
        if self._red_window is None:
            self._red_window = {"enter_dist": dist, "enter_speed": speed}
            out.append(self._cand("SIG_RED_APPROACH", f"红灯停车！立即制动（距 {dist:.0f} 米）"))

        enter_speed = self._red_window.get("enter_speed")
        if dist <= RED_APPROACH_EXIT_M:
            not_dropping = (
                enter_speed is None or speed is None
                or speed >= enter_speed - SPEED_DROP_TOLERANCE_KMH
            )
            if not_dropping:
                out.append(self._cand(
                    "SIG_RED_STOP",
                    f"红灯！停车！停车！（距 {dist:.0f} 米，速度未降）",
                ))
            # 更近距离仍未降 ⇒ 升级为应急
            if dist <= SHUNT_SAFE_DISTANCE_M and not_dropping:
                out.append(self._cand(
                    "EMERG_RED_STOP",
                    f"红灯停车！立即干预！（距 {dist:.0f} 米）",
                ))
        return out

    def _signal_consistency(self, snap: Snapshot) -> list[Candidate]:
        raw = snap.cab.get("ASPECT_DISPLAY")
        if raw is None or snap.signal_aspect is None:
            return []
        mapped = ENABLE_SIG_INCONSISTENT_ASPECT_MAP.get(int(raw))
        if mapped is None:
            return []
        if ASPECT_RANK.get(mapped, -1) != ASPECT_RANK.get(snap.signal_aspect, -1):
            return [self._cand(
                "SIG_INCONSISTENT",
                f"信号不一致（机车 {mapped} / 地面 {snap.signal_aspect}），立即确认，看不准就是停",
            )]
        return []

    # ── 限速与超速类 ───────────────────────────────────────────────────
    def _detect_speed(self, snap: Snapshot, prev: Snapshot | None) -> list[Candidate]:
        out: list[Candidate] = []
        color = snap.track_color
        if color == "Orange":
            out.append(self._cand("SPD_TRACK_ORANGE", f"接近限速，注意控速（{snap.speed_kmh or 0:.0f} km/h）"))
        elif color == "OrangeRed":
            out.append(self._cand("SPD_TRACK_RED", f"超速！立即减速！（{snap.speed_kmh or 0:.0f} km/h）"))
        elif color == "PaleGreen":
            out.append(self._cand("SPD_NEAR_LIMIT", f"速度接近限速（{snap.speed_kmh or 0:.0f} / {snap.limit_kmh or 0:.0f} km/h）"))

        if prev is not None and prev.limit_kmh and snap.limit_kmh and snap.limit_kmh < prev.limit_kmh:
            out.append(self._cand(
                "SPD_LIMIT_DROP",
                f"前方限速 {snap.limit_kmh:.0f}，注意提前控速（原 {prev.limit_kmh:.0f}）",
            ))

        # 临时限速：靠 LimitCol 的 OrangeRed 标记 + 距离分档
        if snap.limit_marker_color == "OrangeRed" and snap.limit_marker_distance_m is not None:
            d = snap.limit_marker_distance_m
            val = snap.limit_marker_value
            for event_id, lo, hi in _TEMP_LIMIT_TIERS:
                if lo <= d < hi:
                    out.append(self._cand(event_id, f"临时限速 {val:.0f} km/h，{d / 1000:.1f} km 前核对" if val else f"限速点 {d:.0f} 米接近"))
                    break

        return out

    # ── 调车类 ─────────────────────────────────────────────────────────
    def _detect_shunt(self, snap: Snapshot, prev: Snapshot | None) -> list[Candidate]:
        out: list[Candidate] = []
        for kind, label in (("switch_left", "左"), ("switch_right", "右")):
            for item in snap.items(kind):
                if not item.forward:
                    continue
                d = f"（{item.distance_m:.0f} 米）" if item.distance_m else ""
                out.append(self._cand("SWITCH_SIDE", f"道岔开通{label}侧，确认径路{d}"))
                break

        auth = (snap.authority or "").strip()
        if auth in ("End Trck", "End Path"):
            out.append(self._cand("SHUNT_END_OF_TRACK", f"行车许可终点（{auth}），注意控速"))

        if ENABLE_SHUNT_OVERSPEED and snap.speed_kmh is not None and snap.speed_kmh > SHUNT_OVERSPEED_KMH:
            out.append(self._cand("SHUNT_OVERSPEED", f"调车超速！{snap.speed_kmh:.0f} km/h，立即控速"))
        return out

    # ── 仪表与设备 ─────────────────────────────────────────────────────
    def _detect_gauge(self, snap: Snapshot, prev: Snapshot | None) -> list[Candidate]:
        out: list[Candidate] = []
        brake = parse_brake_hud(snap.hud.get("列车制动", ""))

        # 列车管压力下降：优先用 HUD 的 BP，缺失时用 cab.BRAKE_PIPE
        bp_now = brake.get("bp", snap.cab.get("BRAKE_PIPE"))
        bp_prev = None
        if prev is not None:
            bp_prev = parse_brake_hud(prev.hud.get("列车制动", "")).get("bp", prev.cab.get("BRAKE_PIPE"))
        if bp_now is not None and bp_prev is not None and (bp_prev - bp_now) > _BP_DROP_THRESHOLD_KPA:
            out.append(self._cand(
                "GAUGE_BRAKE_PIPE_DROP",
                f"列车管压力下降，注意检查（{bp_prev:.0f} → {bp_now:.0f} kPa）",
            ))

        if ENABLE_GAUGE_BRAKE_NOT_RELEASED:
            bc = brake.get("bc", snap.cab.get("BRAKE_CYL"))
            cp = snap.cab.get("CP_HANDLE")
            if bc is not None and bc > _BC_STUCK_THRESHOLD_KPA and cp is not None and cp > 0.3:
                out.append(self._cand("GAUGE_BRAKE_NOT_RELEASED", f"制动未缓解，注意确认（制动缸 {bc:.0f} kPa）"))

        mr = snap.cab.get("MAIN_RES")
        if mr is not None and not (_MAIN_RES_LOW_KPA <= mr <= _MAIN_RES_HIGH_KPA):
            out.append(self._cand("GAUGE_MAIN_RESERVOIR", f"总风 {mr:.0f} kPa，注意"))

        # 列尾：EOT 与主段 BP 偏差过大
        eot_bp = brake.get("eot_bp")
        if eot_bp is not None and bp_now is not None and abs(bp_now - eot_bp) > _BP_DEVIATION_KPA:
            out.append(self._cand("GAUGE_TAIL_PRESSURE", f"尾部风压 {eot_bp:.0f} kPa（前部 {bp_now:.0f}），注意"))

        # 受电弓 / 网压
        panto = snap.cab.get("PANTOGRAPH", snap.cab.get("PANTO_DISPLAY"))
        panto_txt = str(snap.hud.get("受电弓", ""))
        if (panto is not None and panto < 0.5) or ("收" in panto_txt and "升" not in panto_txt):
            out.append(self._cand("ELEC_PANTO_DOWN", "受电弓异常/主断跳，注意确认"))

        lv = snap.cab.get("LINE_VOLTAGE")
        if lv is not None and not (_LINE_VOLTAGE_LOW_KV <= lv <= _LINE_VOLTAGE_HIGH_KV):
            out.append(self._cand("ELEC_NET_VOLTAGE", f"网压异常（{lv:.1f} kV），注意"))

        return out

    # ── 应急补充 ───────────────────────────────────────────────────────
    def _detect_emergency(self, snap: Snapshot, prev: Snapshot | None) -> list[Candidate]:
        out: list[Candidate] = []
        brake = parse_brake_hud(snap.hud.get("列车制动", ""))

        # 列尾失效：有 EOT 数据但与前部偏差巨大（借用 BP 偏差门限的 3 倍）
        eot_bp = brake.get("eot_bp")
        bp = brake.get("bp", snap.cab.get("BRAKE_PIPE"))
        if eot_bp is not None and bp is not None and abs(bp - eot_bp) > _BP_DEVIATION_KPA * 3:
            out.append(self._cand("EMERG_TAIL_FAIL", f"列尾风压异常（尾 {eot_bp:.0f} / 前 {bp:.0f} kPa），查询列尾，报告车站"))

        # 弓网故障：弓已降 + 网压异常同时成立
        panto = snap.cab.get("PANTOGRAPH", snap.cab.get("PANTO_DISPLAY"))
        lv = snap.cab.get("LINE_VOLTAGE")
        if panto is not None and panto < 0.5 and lv is not None and lv < _LINE_VOLTAGE_LOW_KV:
            out.append(self._cand("EMERG_PANTO_FAULT", "弓网故障！禁止盲目升弓，确认接地/绝缘"))

        # 被迫停车：速度归零但不在站内（站台不在 200m 内）
        if snap.stationary():
            station = snap.nearest("station")
            if station is None or (station.distance_m or 1e9) > 200.0:
                out.append(self._cand("EMERG_STOPPED", "被迫停车：报位置、护列车、设防护、防溜、联系救援"))
        return out

    # ── 防溜 ───────────────────────────────────────────────────────────
    def _detect_parking(self, snap: Snapshot, prev: Snapshot | None) -> list[Candidate]:
        if not snap.stationary():
            return []
        tb = snap.cab.get("TRAIN_BRAKE")
        eb = snap.cab.get("ENGINE_BRAKE")
        if tb is None and eb is None:
            return []                     # 数据缺失，不敢断言
        tb = tb or 0.0
        eb = eb or 0.0
        if tb <= 0.02 and eb <= 0.02:
            return [self._cand("PARK_BRAKE_NOT_SET", "注意防溜！车已停但未施加制动")]
        return []

    # ── 状态类 ─────────────────────────────────────────────────────────
    def _detect_state(self, snap: Snapshot, prev: Snapshot | None) -> list[Candidate]:
        out: list[Candidate] = []

        if prev is not None and prev.cab_orientation and snap.cab_orientation and prev.cab_orientation != snap.cab_orientation:
            out.append(self._cand(
                "CAB_ORIENTATION_CHANGE",
                f"换端作业：司机室 {prev.cab_orientation} → {snap.cab_orientation}，确认换向器与制动",
            ))

        for item in snap.items("own_train"):
            if item.color == "OrangeRed":
                out.append(self._cand("TRAIN_OFF_ROUTE", "偏离径路！立即停车确认"))
                break

        op = snap.nearest("opposite_train")
        if op is not None:
            d = f"（{op.distance_m:.0f} 米）" if op.distance_m else ""
            out.append(self._cand("OPPOSITE_TRAIN_AHEAD", f"前方反向列车{d}，注意"))

        eoa = snap.nearest("end_of_authority")
        if eoa is not None:
            d = f"（{eoa.distance_m:.0f} 米）" if eoa.distance_m else ""
            out.append(self._cand("END_OF_AUTHORITY", f"行车许可终点{d}，注意停车"))

        rev = snap.nearest("reversal_point")
        if rev is not None:
            d = f"（{rev.distance_m:.0f} 米）" if rev.distance_m else ""
            out.append(self._cand("REVERSAL_POINT", f"前方折返点{d}"))

        wait = snap.nearest("waiting_point")
        if wait is not None:
            state = "已启用" if wait.color == "Yellow" else "禁用"
            d = f"（{wait.distance_m:.0f} 米）" if wait.distance_m else ""
            out.append(self._cand("WAITING_POINT", f"前方等候点{d}，{state}"))

        st = snap.nearest("station")
        if st is not None:
            d = f"（{st.distance_m:.0f} 米）" if st.distance_m else ""
            out.append(self._cand("STATION_AHEAD", f"前方站台{d}"))

        g = snap.gradient_pct
        if g is not None and abs(g) >= 1.0:
            direction = "上坡" if g > 0 else "下坡"
            out.append(self._cand("GRADIENT_NOTICE", f"前方{direction} {abs(g):.1f}%"))

        return out

    # ── 工具 ───────────────────────────────────────────────────────────
    @staticmethod
    def _cand(event_id: str, message: str) -> Candidate:
        return Candidate(event_id=event_id, message=message, spec=_spec(event_id))


__all__ = ["Candidate", "Detector", "parse_brake_hud"]
