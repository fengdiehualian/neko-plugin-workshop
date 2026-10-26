"""把 Open Rails 的三个 REST 端点解码成一份归一化快照（Snapshot）。

数据源与解码依据
---------------
``/API/TRACKMONITORDISPLAY``  = F4 Track Monitor 的结构化版本。它的取色方式不是
像素，而是**在文本尾部追加 3 字符颜色码**（如 ``"0.0 km/h!??"`` = 文本 + White）。
颜色表与信号 aspect 映射均**逐字抄自 OR 自己的源码**
``Source/RunActivity/Viewer3D/WebServices/TrackMonitorDisplay.cs``
（``ColorCode`` / ``Symbols`` / ``Sprites.SignalMarkers``）。该文件的类注释即
"Table of Colors to client-side color codes"，是权威解码器。

``/API/CABCONTROLS`` = 机控器数组，元素为
``{TypeName, MinValue, MaxValue, RangeFraction}``，实际值 =
``MinValue + (MaxValue - MinValue) * RangeFraction``（与 OR 的
``Content/Web/CabControls/index.js`` 算式一致）。注意 ``ControlIndex`` 未序列化，
同一 ``TypeName`` 会合法重复；零宽量程表示"无值"（OR 渲染为 ``-``）。

``/API/HUD/{0,1}`` = F5 基础 HUD / Shift+F5 电气扩展，行式 ``[label, null, value]``。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# ── 逐字抄自 TrackMonitorDisplay.cs: ColorCode ──────────────────────────
COLOR_CODE: dict[str, str] = {
    "???": "Yellow",
    "??!": "Green",
    "?!?": "Black",
    "?!!": "PaleGreen",
    "!??": "White",
    "!!?": "Orange",
    "!!!": "OrangeRed",
    "%%%": "Cyan",
    "%$$": "Brown",
    "%%$": "LightGreen",
    "$%$": "Blue",
    "$$$": "LightSkyBlue",
}

# ── 逐字抄自 TrackMonitorDisplay.Sprites.SignalMarkers: 矩形 → aspect ────
SIGNAL_BY_RECT: dict[tuple[int, int], str] = {
    (0, 0): "Clear_2",
    (16, 0): "Clear_1",
    (0, 16): "Approach_3",
    (16, 16): "Approach_2",
    (0, 32): "Approach_1",
    (16, 32): "Restricted",
    (0, 48): "StopAndProceed",
    (16, 48): "Stop",
    (0, 64): "Permission",
    (16, 64): "None",
}

#: 信号等级（越大越宽松）。None/Permission 语义特殊，按 OR 颜色归为"最严"。
ASPECT_RANK: dict[str, int] = {
    "Stop": 0,
    "StopAndProceed": 1,
    "Restricted": 1,
    "Permission": 1,
    "Approach_1": 2,
    "Approach_2": 3,
    "Approach_3": 4,
    "Clear_1": 5,
    "Clear_2": 6,
    "None": 0,
}

#: ControlModeLabels 的本地化标签（TrackMonitorDisplay.cs），用于识别无分隔符的模式行
_MODE_LABELS = frozenset({
    "自动信号", "Auto Signal", "节点", "Node", "手动", "Manual",
    "转盘", "Turntable", "不活动", "Inactive", "未知", "Unknown",
    "脱控", "OutOfControl",
})

#: OutOfControlLabels：OR 自己给出的脱控原因，全是 P0 级安全事件
OUT_OF_CONTROL_REASONS = {
    "SPAD": "冒进信号（Signal Passed At Danger）",
    "SPAD-Rear": "尾部冒进",
    "Misalg Sw": "道岔错位（Misaligned Switch）",
    "Off Auth": "越权运行",
    "Off Path": "偏离径路",
    "Splipped": "滑入径路",
    "Slipped": "滑向尽头线",
    "Off Track": "脱线",
    "Slip Turn": "滑入转盘",
    "Undefined": "未定义的脱控",
}

# ── 逐字抄自 TrackMonitorDisplay.Symbols ────────────────────────────────
SYM_TRACK = "\u2502\u2502"          # ││
SYM_EYE = "\u26ef"                  # ⛯  本车位置(眼)
SYM_END_AUTH = "\u25ac"             # ▬  行车许可终点 / 无效折返
SYM_OPPOSITE = "\u2588"             # █  反向列车
SYM_STATION_L = "\u2590"            # ▐  站台(右)
SYM_STATION_R = "\u258c"            # ▌  站台(左)
SYM_SWITCH_L = "\u25c4"             # ◄  道岔开通左(侧向)
SYM_SWITCH_R = "\u25ba"             # ►  道岔开通右
SYM_ARROW_F = "\u25b2"              # ▲
SYM_ARROW_B = "\u25bc"              # ▼
SYM_REVERSAL = "\u21b6"             # ↶  折返点
SYM_WAITING = "\u270b"              # ✋  等候点
SYM_TRAIN_POS = "\u29ef"            # ⧯  本车位置标记(TrainPositionWS)
# ⚠ 别把这个码点写成 U+25EF（◯）。U+29EF 与 U+25EF 视觉近似但是两个字，
#   写错不会报错，只会让"本车标记"静默消失，进而让前/后分类失去锚点——
#   实测踩过一次（锚点丢失 → 全部元素被判成"前方"）。
SYM_SIGNAL = "\u25d5"               # ◕  信号标记
SYM_GRAD_DOWN = "\u2198"            # ↘
SYM_GRAD_UP = "\u2197"              # ↗

_DIST_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(k?m)\s*$", re.IGNORECASE)
_NUM_HEAD_RE = re.compile(r"(\d+(?:\.\d+)?)")


def split_color(cell: Any) -> tuple[str, str | None]:
    """把单元格拆成 (文本, 颜色名)。颜色码恒为末 3 字符。"""
    text = "" if cell is None else str(cell)
    if len(text) >= 3 and text[-3:] in COLOR_CODE:
        return text[:-3].strip(), COLOR_CODE[text[-3:]]
    return text.strip(), None


def parse_distance_m(text: Any) -> float | None:
    """``"4.5 km"`` / ``"73m"`` / ``"0.1km"`` → 米。"""
    if text is None:
        return None
    m = _DIST_RE.match(str(text))
    if not m:
        return None
    value = float(m.group(1))
    return value * 1000.0 if m.group(2).lower() == "km" else value


def aspect_from_row(signal_item: Any, color: str | None) -> str | None:
    """优先用 16x16 矩形精确定位 aspect；矩形缺失才退回颜色粗判。"""
    if isinstance(signal_item, dict):
        try:
            key = (int(signal_item.get("X", -1)), int(signal_item.get("Y", -1)))
        except (TypeError, ValueError):
            key = (-1, -1)
        if key in SIGNAL_BY_RECT:
            return SIGNAL_BY_RECT[key]
    if color in ("PaleGreen",):
        return "Clear_1"
    if color == "Yellow":
        return "Approach_2"
    if color == "OrangeRed":
        return "Stop"
    if color == "Black":
        return "None"
    return None


def _row(rows: list[dict[str, Any]], first_col: str) -> dict[str, Any] | None:
    for r in rows:
        if str(r.get("FirstCol") or "").strip() == first_col:
            return r
    return None


def _float_head(text: Any) -> float | None:
    m = _NUM_HEAD_RE.search("" if text is None else str(text))
    return float(m.group(1)) if m else None


def _percent(text: Any) -> float | None:
    """``"-0.5%"`` → -0.5。HUD 的坡度是纯百分数。"""
    if text is None:
        return None
    s = str(text).strip().replace("%", "").replace("％", "").strip()
    try:
        return float(s)
    except ValueError:
        return None


@dataclass
class TrackItem:
    """Track Monitor 上的一格（道岔/站台/反向车/终点/折返点/等候点…）。

    ``forward`` 很关键：OR 在手动模式下会**同时**画前方与后方的元素
    （``DrawManualModeInfo`` 分别调 ``DrawTrackItems(direction: Forward/Backward)``），
    所以同一格内容可能在列表里出现两次。行号相对本车标记的位置可以区分：
    前方元素的行号更小（列表上方），后方元素更大。把"车后的信号"当成
    "车前的信号"会直接造成冒进误判，所以必须区分。
    """

    kind: str
    color: str | None
    text: str
    distance_m: float | None = None
    symbol: str = ""
    row_index: int = -1
    forward: bool = True

    def __str__(self) -> str:
        d = f" @{self.distance_m:.0f}m" if self.distance_m is not None else ""
        side = "前" if self.forward else "后"
        return f"{self.kind}({self.color}{d},{side})"


@dataclass
class Snapshot:
    """一次采样解码出的归一化状态。字段缺失一律为 None，绝不用 0 冒充"未知"。"""

    # Track Monitor 头部行
    speed_kmh: float | None = None
    speed_color: str | None = None
    projected_kmh: float | None = None
    limit_kmh: float | None = None
    track_color: str | None = None
    gradient_pct: float | None = None
    direction: str | None = None
    cab_orientation: str | None = None
    control_mode: str | None = None
    authority: str | None = None
    #: OR 是否处于自动模式（AUTO_SIGNAL/AUTO_NODE）。自动模式只画前方元素。
    is_auto_mode: bool = False
    #: 前/后分类的来源：auto_all_forward / manual_row_split / unknown_assumed_forward
    forward_source: str = ""
    #: OR 自己报告的脱控状态（OUT_OF_CONTROL）。原因是 SPAD / Misalg Sw / Off Track …
    out_of_control: bool = False
    out_of_control_reason: str = ""

    # 信号
    signal_aspect: str | None = None
    signal_color: str | None = None
    signal_distance_m: float | None = None

    # 限速牌（临时限速由颜色标出）
    limit_marker_value: float | None = None
    limit_marker_color: str | None = None
    limit_marker_distance_m: float | None = None

    # 前方轨道元素
    track_items: list[TrackItem] = field(default_factory=list)

    # 机控器 / HUD
    cab: dict[str, float] = field(default_factory=dict)
    cab_raw: dict[str, dict[str, float]] = field(default_factory=dict)
    hud: dict[str, str] = field(default_factory=dict)
    game_time_s: float | None = None

    # 数据可用性（用于 detector 判断"这项到底有没有"）
    available: dict[str, bool] = field(default_factory=dict)

    # ── 派生判断 ───────────────────────────────────────────────────────
    def overspeed_state(self) -> str | None:
        """由轨道颜色给出 OR 自己的超速分级（非自造阈值）。

        Green=正常 / PaleGreen=接近限速 / Orange=轻度超速 / OrangeRed=严重超速。
        """
        if self.track_color in ("Green", "PaleGreen", "Orange", "OrangeRed"):
            return self.track_color
        return None

    def limit_ratio(self) -> float | None:
        if self.speed_kmh is None or not self.limit_kmh:
            return None
        return self.speed_kmh / self.limit_kmh

    def items(self, kind: str) -> list[TrackItem]:
        return [i for i in self.track_items if i.kind == kind]

    def nearest(self, kind: str) -> TrackItem | None:
        found = [i for i in self.items(kind) if i.distance_m is not None]
        return min(found, key=lambda i: i.distance_m) if found else None

    def stationary(self) -> bool:
        return self.speed_kmh is not None and abs(self.speed_kmh) < 0.5


def decode_track_monitor(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """解码 ``/API/TRACKMONITORDISPLAY`` 的 30 行。"""
    out: dict[str, Any] = {"track_items": []}
    if not isinstance(rows, list):
        return out

    speed_row = _row(rows, "速度") or _row(rows, "Speed")
    if speed_row:
        text, color = split_color(speed_row.get("TrackCol"))
        out["speed_kmh"], out["speed_color"] = _float_head(text), color
        # 别把 speed_color 当 track_color：OR 源码里 TrackColor() 与 SpeedColor()
        # 是两张表，阈值相同但"正常"色不同（Track=Green，Speed=White）。
        # 轨道颜色只能从 ││ 轨道格上取，见下方 track_items 之后的推导。

    proj = _row(rows, "Projected")
    if proj:
        out["projected_kmh"] = _float_head(split_color(proj.get("TrackCol"))[0])

    limit = _row(rows, "Limit")
    if limit:
        out["limit_kmh"] = _float_head(split_color(limit.get("TrackCol"))[0])

    grad = _row(rows, "坡度") or _row(rows, "Gradient")
    if grad:
        text, _ = split_color(grad.get("TrackCol"))
        if text and text != "-":
            out["gradient_pct"] = _float_head(text)
            if SYM_GRAD_DOWN in text:
                out["gradient_pct"] = -(out["gradient_pct"] or 0.0)

    direction = _row(rows, "方向") or _row(rows, "Direction")
    if direction:
        out["direction"] = split_color(direction.get("TrackCol"))[0] or None

    orient = _row(rows, "Cab ORIEN")
    if orient:
        out["cab_orientation"] = split_color(orient.get("TrackCol"))[0] or None

    for r in rows:
        first = str(r.get("FirstCol") or "").strip()
        # 控制模式行：FirstCol 形如 "节点 : TrainAhd" 或 "脱控：SPAD"。
        # 分隔符有两种来源，必须都认：
        #   FindAuthorityInfo     拼 ASCII " : "（"节点 : TrainAhd"）
        #   OUT_OF_CONTROL 分支   直接拼接本地化标签 → 全角 "："（"脱控：SPAD"）
        # 另外不能用 Truthy 判 TrackColRight：OR 把空串填成 " "（空格），空格是真值。
        right_text, _rc = split_color(r.get("TrackColRight"))
        if first and first not in ("Sprtr", "Milepost") and not right_text:
            for sep in (" : ", "：", ":"):
                if sep in first:
                    mode, _, extra = first.partition(sep)
                    out["control_mode"], out["authority"] = mode.strip(), extra.strip()
                    break
            else:
                # 无分隔符的模式行（"自动信号" / "手动" / "转盘" …）
                if first in _MODE_LABELS:
                    out["control_mode"] = first
            mode_now = str(out.get("control_mode") or "")
            if mode_now in ("脱控", "OutOfControl", "OUT_OF_CONTROL"):
                out["out_of_control"] = True
                # OutOfControlLabels：SPAD 冒进 / SPAD-Rear / Misalg Sw 道岔错位 /
                # Off Auth 越权 / Off Path 离径路 / Off Track 脱线 / Slipped …
                out["out_of_control_reason"] = out.get("authority") or ""
        # 信号
        if r.get("SignalCol") and str(r["SignalCol"]).strip():
            _, scolor = split_color(r.get("SignalCol"))
            out["signal_aspect"] = aspect_from_row(r.get("SignalColItem"), scolor)
            out["signal_color"] = scolor
            out["signal_distance_m"] = parse_distance_m(r.get("DistCol"))
        # 限速牌
        if r.get("LimitCol") and str(r["LimitCol"]).strip():
            ltext, lcolor = split_color(r.get("LimitCol"))
            if ltext != "Limit":  # 跳过表头
                out["limit_marker_value"] = _float_head(ltext)
                out["limit_marker_color"] = lcolor
                out["limit_marker_distance_m"] = parse_distance_m(r.get("DistCol"))

    # 逐格轨道元素。row_index 保留下来用于前/后分类（见下方推导）
    for row_index, r in enumerate(rows):
        text, color = split_color(r.get("TrackCol"))
        left, lcolor = split_color(r.get("TrackColLeft"))
        right, rcolor = split_color(r.get("TrackColRight"))
        dist = parse_distance_m(r.get("DistCol"))
        if not text and not left and not right:
            continue

        def add(kind: str, sym: str, col: str | None) -> None:
            out["track_items"].append(
                TrackItem(kind=kind, color=col or color, text=text or left or right,
                          distance_m=dist, symbol=sym, row_index=row_index)
            )

        if text.startswith(SYM_EYE):
            add("own_train_eye", SYM_EYE, color)
        elif text.startswith(SYM_TRAIN_POS):
            add("own_train", SYM_TRAIN_POS, color)
        elif text.startswith(SYM_OPPOSITE):
            add("opposite_train", SYM_OPPOSITE, color)
        elif text.startswith(SYM_END_AUTH):
            add("end_of_authority", SYM_END_AUTH, color)
        elif text.startswith(SYM_REVERSAL):
            add("reversal_point", SYM_REVERSAL, color)
        elif text.startswith(SYM_WAITING):
            add("waiting_point", SYM_WAITING, color)
        elif text.startswith(SYM_SWITCH_L):
            add("switch_left", SYM_SWITCH_L, color)
        elif text.startswith(SYM_SWITCH_R):
            add("switch_right", SYM_SWITCH_R, color)
        elif text.startswith(SYM_ARROW_F):
            add("arrow_forward", SYM_ARROW_F, color)
        elif text.startswith(SYM_ARROW_B):
            add("arrow_backward", SYM_ARROW_B, color)
        elif text.startswith(SYM_TRACK):
            add("track", SYM_TRACK, color)
        if left.startswith(SYM_STATION_L):
            add("station", SYM_STATION_L, lcolor)
        elif right.startswith(SYM_STATION_R):
            add("station", SYM_STATION_R, rcolor)
        if text.startswith(SYM_END_AUTH):
            # ▬ 在有效折返下是"无效折返"，两种含义都归入 items，由 detector 细分
            pass

    items: list[TrackItem] = out["track_items"]

    # 轨道颜色 = 超速分级，取自第一格真实轨道（││）。OR 的轨道色由 TrackColor()
    # 给出，与速度文本的颜色不同色（Green vs White）。
    for item in items:
        if item.kind == "track":
            out["track_color"] = item.color
            break

    # 前/后分类。依据 OR 源码两条不同绘制路径：
    #   DrawAutoModeInfo   只调 DrawTrackItems(..., Forward) ⇒ **所有元素都是前方**，
    #                      本车"眼"标画在顶部，所以不能用行号比较。
    #   DrawManualModeInfo 上方画 Forward、下方画 Backward ⇒ 前方 = 行号更小
    #                      （本车标记在中间，RowOffset 之后落在分界处）。
    # ⚠ 手动模式这一支是从源码推导、尚未在真机逐帧确认；row_index/anchor_index
    #   都保留在返回值里，便于用 --dump 复核。把"车后信号"当成"车前信号"会直接
    #   造成冒进误判，所以这里宁可保守：无法判定前方时按**前方**处理（安全侧），
    #   但把 forward_source 标出来。
    anchor = next((i.row_index for i in items if i.kind == "own_train"), None)
    mode = str(out.get("control_mode") or "")
    is_auto = ("节点" in mode) or ("自动信号" in mode) or ("Auto" in mode) or ("Node" in mode)
    out["anchor_index"] = anchor
    out["is_auto_mode"] = is_auto
    if is_auto:
        out["forward_source"] = "auto_all_forward"
    elif anchor is None:
        out["forward_source"] = "unknown_assumed_forward"
    else:
        out["forward_source"] = "manual_row_split"
        for i in items:
            if i.kind not in ("own_train", "own_train_eye"):
                i.forward = i.row_index < anchor

    return out


def decode_cab_controls(controls: list[dict[str, Any]]) -> tuple[dict[str, float], dict[str, dict[str, float]]]:
    """解码 ``/API/CABCONTROLS``。零宽量程跳过；重复 TypeName 保留第一个真实表。"""
    values: dict[str, float] = {}
    raw: dict[str, dict[str, float]] = {}
    if not isinstance(controls, list):
        return values, raw
    for c in controls:
        name = str(c.get("TypeName") or "")
        if not name:
            continue
        try:
            lo = float(c.get("MinValue", 0.0))
            hi = float(c.get("MaxValue", 0.0))
            frac = float(c.get("RangeFraction", 0.0))
        except (TypeError, ValueError):
            continue
        if hi == lo:          # OR 自己的约定：零宽量程 = 无值（渲染为 "-"）
            continue
        if name in values:    # 同名列控器合法重复（多司机室/多机），取首个真实表
            continue
        values[name] = lo + (hi - lo) * frac
        raw[name] = {"min": lo, "max": hi, "fraction": frac}
    return values, raw


def decode_hud(tables: dict[str, Any]) -> dict[str, str]:
    """解码 ``/API/HUD/{n}`` 的行式表格为 ``{label: value}``。"""
    out: dict[str, str] = {}
    if not isinstance(tables, dict):
        return out
    for key in ("commonTable", "extraTable"):
        t = tables.get(key)
        if not isinstance(t, dict):
            continue
        cols = int(t.get("nCols") or 0)
        rows = int(t.get("nRows") or 0)
        vals = t.get("values") or []
        if cols <= 0:
            continue
        for r in range(rows):
            cells = [vals[r * cols + c] if r * cols + c < len(vals) else None for c in range(cols)]
            label = cells[0] if cells else None
            value = next((c for c in cells[1:] if c not in (None, "")), None)
            if label and str(label).strip():
                out[str(label).strip()] = "" if value is None else str(value).strip()
    return out


def build_snapshot(
    tm_rows: list[dict[str, Any]] | None,
    cab_controls: list[dict[str, Any]] | None,
    hud0: dict[str, Any] | None = None,
    hud1: dict[str, Any] | None = None,
    game_time_s: float | None = None,
) -> Snapshot:
    """把三个端点的原始响应合成一份 Snapshot。"""
    tm = decode_track_monitor(tm_rows or [])
    cab, cab_raw = decode_cab_controls(cab_controls or [])
    hud = decode_hud(hud0 or {})
    hud.update(decode_hud(hud1 or {}))

    snap = Snapshot(
        speed_kmh=tm.get("speed_kmh"),
        speed_color=tm.get("speed_color"),
        projected_kmh=tm.get("projected_kmh"),
        limit_kmh=tm.get("limit_kmh"),
        track_color=tm.get("track_color"),
        gradient_pct=tm.get("gradient_pct"),
        direction=tm.get("direction"),
        cab_orientation=tm.get("cab_orientation"),
        control_mode=tm.get("control_mode"),
        authority=tm.get("authority"),
        signal_aspect=tm.get("signal_aspect"),
        signal_color=tm.get("signal_color"),
        signal_distance_m=tm.get("signal_distance_m"),
        limit_marker_value=tm.get("limit_marker_value"),
        limit_marker_color=tm.get("limit_marker_color"),
        limit_marker_distance_m=tm.get("limit_marker_distance_m"),
        track_items=tm.get("track_items") or [],
        cab=cab,
        cab_raw=cab_raw,
        hud=hud,
        game_time_s=game_time_s,
    )

    # 机控器的 SPEEDLIM_DISPLAY 是限速的第二来源；TM 缺失时兜底
    if snap.limit_kmh is None and "SPEEDLIM_DISPLAY" in cab:
        snap.limit_kmh = cab["SPEEDLIM_DISPLAY"]
    if snap.speed_kmh is None and "SPEEDOMETER" in cab:
        snap.speed_kmh = cab["SPEEDOMETER"]
    # HUD 的"坡度"是纯百分数；TM 在低于 OR 阈值时给 "-"。TM 优先，HUD 兜底。
    if snap.gradient_pct is None:
        snap.gradient_pct = _percent(hud.get("坡度"))

    snap.is_auto_mode = bool(tm.get("is_auto_mode"))
    snap.forward_source = str(tm.get("forward_source") or "")
    snap.out_of_control = bool(tm.get("out_of_control"))
    snap.out_of_control_reason = str(tm.get("out_of_control_reason") or "")

    snap.available = {
        "track_monitor": bool(tm_rows),
        "cab_controls": bool(cab),
        "hud": bool(hud),
        "signal": snap.signal_aspect is not None,
        "speed": snap.speed_kmh is not None,
        "limit": snap.limit_kmh is not None,
        "line_voltage": "LINE_VOLTAGE" in cab,
        "pantograph": "PANTOGRAPH" in cab or "PANTO_DISPLAY" in cab or "受电弓" in hud,
        "brake_pipe": "BRAKE_PIPE" in cab or "列车制动" in hud,
        "eot": "EOT" in " ".join(hud.values()),
        "train_brake": "TRAIN_BRAKE" in cab,
        "engine_brake": "ENGINE_BRAKE" in cab,
        "cab_orientation": snap.cab_orientation is not None,
    }
    return snap
