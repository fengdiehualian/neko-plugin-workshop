# 和猫娘一起开飞机（简单飞机2副驾驶）插件 —— 连接 SimplePlanes 2
# Copyright (C) 2026 星河拓航工作室 (Galaxy Exploration Studio)
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""SP2 Copilot Plugin（和猫娘一起开飞机）

连接《简单飞机2 SimplePlanes 2》，让猫娘坐在右座当副驾驶：

- **读仪表**：空速、地速、升降率、高度（离地/海拔）、航向、俯仰、滚转、
  迎角、侧滑、G 值、马赫数、油门、配平、襟翼、起落架
- **判阶段**：停机 / 滑行 / 起飞滑跑 / 着陆滑跑 / 初始爬升 / 爬升 / 巡航 /
  下降 / 进近着陆 / 已坠毁
- **扫告警**：失速、超速、过载、近地、下降率过大、坡度过陡、起落架未放下、
  襟翼与起落架超速、发动机停车、起火、低油量、机体损伤
- **跑检查单**：按当前阶段给出该做的项目，逐项标出 已完成 / 待办 / 未知
- **做导航**：到目标点的距离、方位、该往哪边转多少度、预计多久到

**这个版本是只读的** —— 插件不发送任何控制指令，飞机始终由玩家驾驶。
副驾驶负责看、算、提醒，不负责动手。

依赖：游戏内需安装配套的 SP2-Bridge 模组，在 127.0.0.1:21580 提供 HTTP 接口。
接口协议见 ``docs/bridge-protocol.md``，本地调试可用 ``tools/mock_bridge.py``。
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional, Tuple

import httpx
from plugin.sdk.plugin import (
    NekoPluginBase,
    Ok,
    SdkError,
    lifecycle,
    llm_tool,
    neko_plugin,
    plugin_entry,
    ui,
)

from .ui_api import Sp2UiMixin

DEFAULT_BRIDGE_URL = "http://127.0.0.1:21580"
_USER_AGENT = "NekoSp2Copilot/0.1 (+https://xhth.top/)"

# ---------------------------------------------------------------------------
# 默认告警阈值（可被 [sp2_copilot] 配置段覆盖）
# ---------------------------------------------------------------------------

DEFAULT_TERRAIN_AGL_M = 300.0
DEFAULT_SINK_RATE_MPS = 6.0
DEFAULT_BANK_DEG = 45.0
DEFAULT_LOW_FUEL_PCT = 20.0
DEFAULT_AOA_DEG = 12.0
DEFAULT_OVERSPEED_RATIO = 0.9

# 判定「起飞滑跑」的门槛：要同时满足「在地 + 有速度 + 油门不小」。
# 低于这个速度就是普通滑行，高于它并按着油门，那就是在冲跑道。
_TAKEOFF_ROLL_SPEED_MPS = 30.0
_TAKEOFF_ROLL_THROTTLE = 0.5

# 判定「在地面静止」的速度门槛（m/s）。
_PARKED_SPEED_MPS = 3.0

# 判定「初始爬升 / 进近」的离地高度门槛（m）。
_LOW_ALTITUDE_AGL_M = 300.0

# 判定「爬升 / 下降 / 巡航」的升降率门槛（m/s）。
_LEVEL_VS_MPS = 2.0

# 告警等级排序：数字越小越紧急。
_LEVEL_ORDER = {"danger": 0, "warning": 1, "caution": 2, "info": 3}

# ---------------------------------------------------------------------------
# 给大模型的说明书
#
# 模型可能完全没听说过《简单飞机2》，也可能不知道本插件提供了哪些工具。
# 这两份文档就是给它补课的，通过 read_sp2_docs 工具读取。
# ---------------------------------------------------------------------------

_SP2_DOC_GAME = """【简单飞机2 SimplePlanes 2 是什么】
Jundroo 出品的载具建造 + 驾驶沙盒，《简单飞机》的正统续作。
2026 年 4 月 23 日以抢先体验（Early Access）形式登陆 Steam，目前只有英文界面。
核心玩法：造载具 → 开出去 → 在群岛地图上乱窜 / 比赛 / 空战 / 联机捣乱。

【重要：它不保证物理真实】
- 这是**游戏物理**，不是真实飞行力学。数值量级、升力阻力模型都可能是简化的
- 不同机体（自制机、社区下载机、喷气/螺旋桨/活塞/电动）差别极大
- 抢先体验期间物理还会调整
→ 任何阈值（失速速度、Vne、最大 G、襟翼限速）**都要从机体数据里读**，
  读不到就如实说「机体没给这个数据」，不要凭现实航空常识猜一个数字出来

【单位约定（本插件统一用这套）】
- 速度：米/秒 m/s（摘要里会同时给 km/h）
- 高度：米 m（altitude_agl = 离地高度，altitude_msl = 海拔）
- 升降率：米/秒 m/s，正数爬升、负数下降
- 角度：度（pitch 正=抬头，roll 正=右倾，heading 为磁/真航向 0-360）
- 迎角 AoA：度，越大越接近失速
- G 值：重力加速度倍数

【飞行阶段（插件会自动判，你直接用结论就行）】
停机 → 滑行 → 起飞滑跑 → 初始爬升 → 爬升 → 巡航 → 下降 → 进近着陆 → 着陆滑跑
另外还有：主菜单/未进入世界、已坠毁

【默认操作（PC 键鼠）】
- 俯仰/滚转：鼠标或手柄摇杆
- 偏航：A / D
- 油门：Shift 加大 / Ctrl 减小
- 起落架：G
- 襟翼：F（多档循环）
- 刹车：B
- 配平：方向键
- 视角切换：C
- 地图：M
⚠️ 键位可以在游戏设置里改，且 HOTAS 玩家各不相同。
   **不要替玩家按任何键** —— 本插件是只读副驾驶，只能看和说。

【给玩家的提醒要点】
- 起飞：油门推满 → 到抬轮速度轻拉 → 建立正上升率再收起落架
- 进近：先减速 → 放襟翼 → 放起落架 → 稳住下降率 → 拉平接地
- 失速：推杆减小迎角 + 加油门，别一味拉杆
"""

_SP2_DOC_TOOLS = """【工具总览：什么时候用哪个】
读说明书        read_sp2_docs          ← 不确定就用它先补课
看整体状况      get_sp2_briefing       ← 一句话状况 + 告警 + 该做什么，首选
看飞行数字      get_sp2_status         ← 高度/速度/升降率/航向/阶段
看姿态细节      get_sp2_instruments    ← 俯仰/滚转/迎角/侧滑/G/马赫
看机体与系统    get_sp2_aircraft       ← 发动机/油量/襟翼/起落架/损伤/限速
看导航          get_sp2_navigation     ← 位置/航向/目标方位距离/预计到达
扫告警          get_sp2_warnings       ← 失速超速过载近地低油量……
跑检查单        get_sp2_checklist      ← 当前阶段该做的事，逐项标状态

【常见问法 → 该调哪个】
「我现在飞得怎么样」「多高多快」      → get_sp2_status
「姿态正常吗」「是不是要失速了」      → get_sp2_instruments + get_sp2_warnings
「有什么要提醒我的吗」                → get_sp2_warnings
「下一步该干什么」「检查单」          → get_sp2_checklist
「怎么飞到 XX」「还有多远」           → get_sp2_navigation
「油还够吗」「发动机有没有事」        → get_sp2_aircraft
「帮我总结一下」                      → get_sp2_briefing

【必须遵守的几条】
❗ **上下文里已经有「【副驾驶自动播报】」时，先直接用那条里的读数回答。**
   那一行是插件自己在后台读来的真实仪表（阶段/高度/速度/告警/检查单），
   和 get_sp2_briefing 是同一份数据 —— 为了同一批数字再去调工具，
   机长只会觉得「她听到了却不理我，查到才回」。
   自动播报里确实没有的细节（姿态细节、机体限速、导航距离）才调工具；
   要调、又可能要等一会儿时，**先说一句「我看一眼仪表」**再调，
   不要一声不吭地卡在那里。
❗ **你是副驾驶，不是机长**：本插件只读，没有任何控制工具。
   遇到该操作的事，**说出来让机长（玩家）去做**，绝对不要假装你按了什么键、
   收了起落架、推了油门 —— 那等于骗人。
❗ **不要编数据**：连不上游戏、字段读不到、机体没给限速，
   就如实说「连接不上 / 读不到 / 机体没提供」，工具返回值里都写清楚了。
❗ **阈值一律以机体数据为准**：aircraft 里的 limits 给了 Vne、失速速度、最大 G
   就按它判；没给就说不确定，不要拿现实航空的数字套。
❗ **告警要分级说**：danger 立刻说，warning 提醒一下，caution/info 可以顺带提。
   别把十条告警一口气全念出来，挑最紧急的两三条。
❗ **说人话**：报高度说「离地 1,010 米」，别说「altitude_agl=1010.3」。
"""


# ---------------------------------------------------------------------------
# 取值辅助
# ---------------------------------------------------------------------------


def _as_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def _as_text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return default


def _num(value: Any) -> Optional[float]:
    """把游戏给的数字转成 float；None / NaN / Inf / 布尔 一律返回 None。"""
    if value is None or isinstance(value, bool):
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if f != f or f in (float("inf"), float("-inf")):
        return None
    return f


def _num_or(value: Any, default: float) -> float:
    f = _num(value)
    return default if f is None else f


def _int_or(value: Any, default: int) -> int:
    f = _num(value)
    return default if f is None else int(f)


def _clamp(value: float, low: float, high: float) -> float:
    if value != value:  # NaN
        return low
    return max(low, min(value, high))


def _default_thresholds() -> Dict[str, float]:
    """默认告警阈值。配置没读到时用它兜底，面板也能拿它显示「恢复默认」的依据。"""
    return {
        "terrain_agl_m": DEFAULT_TERRAIN_AGL_M,
        "sink_rate_mps": DEFAULT_SINK_RATE_MPS,
        "bank_deg": DEFAULT_BANK_DEG,
        "low_fuel_percent": DEFAULT_LOW_FUEL_PCT,
        "aoa_deg": DEFAULT_AOA_DEG,
        "overspeed_ratio": DEFAULT_OVERSPEED_RATIO,
    }


def _cfg_number(section: Dict[str, Any], key: str, default: float, low: float, high: float) -> float:
    raw = section.get(key, default)
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return default
    return _clamp(value, low, high)


# ---------------------------------------------------------------------------
# 格式化（纯函数，便于测试）
# ---------------------------------------------------------------------------


def _fmt(value: Any, digits: int = 1, unit: str = "") -> str:
    """通用数字格式化，取不到返回「未知」。"""
    f = _num(value)
    if f is None:
        return "未知"
    text = f"{f:,.{digits}f}"
    return f"{text}{unit}" if unit else text


def _fmt_speed(value: Any) -> str:
    """空速/地速：m/s 为主，括号里给 km/h —— 玩家两个单位都常用。"""
    f = _num(value)
    if f is None:
        return "未知"
    return f"{f:,.1f} m/s（{f * 3.6:,.0f} km/h）"


def _fmt_alt(value: Any) -> str:
    """高度：一律米；超过 10 km 换成公里写法，避免一长串数字。"""
    f = _num(value)
    if f is None:
        return "未知"
    if abs(f) >= 10_000:
        return f"{f / 1000:,.2f} km（{f:,.0f} m）"
    return f"{f:,.0f} m"


def _fmt_vs(value: Any) -> str:
    """升降率：带方向词，别让模型自己去猜正负号含义。"""
    f = _num(value)
    if f is None:
        return "未知"
    if f > 0.5:
        return f"+{f:,.1f} m/s（爬升）"
    if f < -0.5:
        return f"{f:,.1f} m/s（下降）"
    return f"{f:,.1f} m/s（基本平飞）"


def _fmt_heading(value: Any) -> str:
    f = _num(value)
    if f is None:
        return "未知"
    return f"{f % 360:,.0f}°"


def _fmt_deg(value: Any, digits: int = 1) -> str:
    f = _num(value)
    if f is None:
        return "未知"
    return f"{f:,.{digits}f}°"


def _fmt_pct(value: Any, digits: int = 0) -> str:
    """百分比：输入既可能是 0-1 的小数，也可能是 0-100 的数。

    模组约定统一给 0-100（``percent`` 字段），但保险起见对 0-1 也做兼容。
    """
    f = _num(value)
    if f is None:
        return "未知"
    if -1.0 <= f <= 1.0:
        f = f * 100.0
    return f"{f:,.{digits}f}%"


def _fmt_throttle(value: Any) -> str:
    """油门：0-1 的小数，按百分比念。"""
    f = _num(value)
    if f is None:
        return "未知"
    return f"{_clamp(f, 0.0, 1.0) * 100:,.0f}%"


def _fmt_dist(value: Any) -> str:
    f = _num(value)
    if f is None:
        return "未知"
    if abs(f) >= 10_000:
        return f"{f / 1000:,.2f} km"
    if abs(f) >= 1_000:
        return f"{f / 1000:,.2f} km（{f:,.0f} m）"
    return f"{f:,.0f} m"


def _fmt_duration(value: Any) -> str:
    """秒 -> 「3分20秒」这种。导航预计到达时间用。"""
    f = _num(value)
    if f is None:
        return "未知"
    total = int(max(0.0, f))
    minutes, seconds = divmod(total, 60)
    if minutes >= 60:
        return f"{minutes // 60}小时{minutes % 60}分"
    if minutes:
        return f"{minutes}分{seconds}秒"
    return f"{seconds}秒"


def _fmt_onoff(value: Any, yes: str = "是", no: str = "否") -> str:
    if value is None:
        return "未知"
    return yes if _as_bool(value) else no


def _fmt_gear(value: Any) -> str:
    """起落架：gear 可能给布尔、0/1，也可能给字符串状态。"""
    text = _as_text(value).lower()
    if text in {"up", "retracted"}:
        return "收起"
    if text in {"down", "deployed", "extended"}:
        return "放下"
    if text in {"moving", "transition"}:
        return "收放中"
    if value is None:
        return "未知"
    return "放下" if _as_bool(value) else "收起"


# ---------------------------------------------------------------------------
# 飞行阶段判定（纯函数）
# ---------------------------------------------------------------------------

PHASE_LABELS: Dict[str, str] = {
    "menu": "未进入飞行（主菜单 / 设计器 / 加载中）",
    "parked": "地面停机",
    "taxi": "地面滑行",
    "takeoff_roll": "起飞滑跑",
    "rollout": "着陆滑跑",
    "initial_climb": "初始爬升",
    "climb": "爬升",
    "cruise": "巡航",
    "descent": "下降",
    "approach": "进近着陆",
    "crashed": "已坠毁",
    "unknown": "阶段未知",
}


def _judge_phase(state: Dict[str, Any]) -> str:
    """根据遥测判断当前飞行阶段，返回 PHASE_LABELS 的键。

    判据全部来自 /state：scene、in_world、flight.on_ground、crashed、
    ground_speed、vertical_speed、altitude_agl，以及 controls.throttle。
    任何一项缺失都往保守方向退（判成更宽泛的阶段），绝不硬编一个数字。
    """
    if _as_bool(state.get("crashed")):
        return "crashed"

    flight = _as_dict(state.get("flight"))
    controls = _as_dict(state.get("controls"))
    scene = _as_text(state.get("scene")).lower()

    if not _as_bool(state.get("in_world"), True):
        return "menu"
    if scene and scene not in {"flight", "world", "game", ""}:
        # designer / menu / loading 都算没在飞
        return "menu"

    crashed = _as_bool(flight.get("crashed"))
    if crashed:
        return "crashed"

    on_ground = flight.get("on_ground")
    agl = _num(flight.get("altitude_agl"))
    vs = _num(flight.get("vertical_speed"))
    gs = _num(flight.get("ground_speed"))
    airspeed = _num(flight.get("airspeed"))
    throttle = _num(controls.get("throttle"))

    if on_ground is None:
        # 模组没给 on_ground：用离地高度兜底（<5 m 且有地速 = 在地面）
        if agl is None:
            return "unknown"
        if agl <= 5.0:
            on_ground = True
        elif agl > 5.0:
            on_ground = False
        else:
            return "unknown"

    if _as_bool(on_ground):
        speed = gs if gs is not None else airspeed
        if speed is None or speed < _PARKED_SPEED_MPS:
            return "parked"
        if speed >= _TAKEOFF_ROLL_SPEED_MPS:
            if throttle is not None and throttle >= _TAKEOFF_ROLL_THROTTLE:
                return "takeoff_roll"
            return "rollout"
        return "taxi"

    # 空中
    if agl is None:
        return "cruise" if vs is None or abs(vs) <= _LEVEL_VS_MPS else "climb"
    if agl <= _LOW_ALTITUDE_AGL_M:
        if vs is None:
            return "unknown"
        if vs > _LEVEL_VS_MPS:
            return "initial_climb"
        if vs < -_LEVEL_VS_MPS:
            return "approach"
        return "cruise"
    if vs is None:
        return "unknown"
    if vs > _LEVEL_VS_MPS:
        return "climb"
    if vs < -_LEVEL_VS_MPS:
        return "descent"
    return "cruise"


def _phase_label(phase: str) -> str:
    return PHASE_LABELS.get(phase, PHASE_LABELS["unknown"])


# ---------------------------------------------------------------------------
# 告警扫描（纯函数）
# ---------------------------------------------------------------------------

_WARN_TEMPLATE = {"code": "", "level": "info", "text": "", "action": ""}


def _warn(code: str, level: str, text: str, action: str) -> Dict[str, Any]:
    return {"code": code, "level": level, "text": text, "action": action}


def _scan_warnings(
    state: Dict[str, Any],
    aircraft: Dict[str, Any],
    thresholds: Optional[Dict[str, float]] = None,
) -> List[Dict[str, Any]]:
    """扫一遍仪表，给出分级告警。

    返回按紧急程度排序的列表，每项含 code / level / text / action。
    **只读得到什么就判什么**：机体没给限速就不判超速，绝不拿默认值糊弄。
    """
    th = thresholds or {}
    terrain_agl = th.get("terrain_agl_m", DEFAULT_TERRAIN_AGL_M)
    sink_rate = th.get("sink_rate_mps", DEFAULT_SINK_RATE_MPS)
    bank = th.get("bank_deg", DEFAULT_BANK_DEG)
    low_fuel = th.get("low_fuel_percent", DEFAULT_LOW_FUEL_PCT)
    aoa_limit = th.get("aoa_deg", DEFAULT_AOA_DEG)
    overspeed_ratio = th.get("overspeed_ratio", DEFAULT_OVERSPEED_RATIO)

    flight = _as_dict(state.get("flight"))
    controls = _as_dict(state.get("controls"))
    limits = _as_dict(aircraft.get("limits"))
    damage = _as_dict(aircraft.get("damage"))
    fuel = _as_dict(aircraft.get("fuel"))
    engines = _as_list(aircraft.get("engines"))

    agl = _num(flight.get("altitude_agl"))
    vs = _num(flight.get("vertical_speed"))
    airspeed = _num(flight.get("airspeed"))
    roll = _num(flight.get("roll"))
    aoa = _num(flight.get("aoa"))
    g = _num(flight.get("g_force"))
    mach = _num(flight.get("mach"))
    on_ground = flight.get("on_ground")
    in_air = not _as_bool(on_ground) if on_ground is not None else None

    vne = _num(limits.get("vne_mps"))
    stall_speed = _num(limits.get("stall_speed_mps"))
    max_g = _num(limits.get("max_g"))
    min_g = _num(limits.get("min_g"))
    max_mach = _num(limits.get("max_mach"))
    flap_limit = _num(limits.get("flap_limit_mps"))
    gear_limit = _num(limits.get("gear_limit_mps"))

    out: List[Dict[str, Any]] = []

    # ── 坠毁 ──────────────────────────────────────────────────────────
    if _as_bool(state.get("crashed")) or _as_bool(flight.get("crashed")):
        out.append(_warn("crashed", "danger", "机体已经坠毁。", "重开一局或读档，别的都先别管。"))

    # ── 失速 ──────────────────────────────────────────────────────────
    if _as_bool(flight.get("stalled")):
        out.append(_warn("stall", "danger", "已经失速。", "推杆减小迎角，同时加油门，等速度回来再拉杆。"))
    elif _as_bool(flight.get("stall_warning")):
        out.append(_warn("stall_warning", "danger", "失速警告响了。", "立刻减小迎角并加大油门，别再拉杆。"))
    elif stall_speed is not None and airspeed is not None and in_air:
        if airspeed < stall_speed:
            out.append(_warn(
                "below_stall_speed", "danger",
                f"空速 {_fmt_speed(airspeed)} 已经低于失速速度 {_fmt_speed(stall_speed)}。",
                "推杆加油门，先把速度拉回来。",
            ))
        elif airspeed < stall_speed * 1.15:
            out.append(_warn(
                "near_stall_speed", "warning",
                f"空速 {_fmt_speed(airspeed)} 只比失速速度 {_fmt_speed(stall_speed)} 高一点，裕度很小。",
                "别做剧烈机动，保持速度。",
            ))

    # ── 迎角 ──────────────────────────────────────────────────────────
    if aoa is not None and in_air and aoa > aoa_limit:
        level = "danger" if aoa > aoa_limit * 1.3 else "warning"
        out.append(_warn(
            "high_aoa", level,
            f"迎角 {_fmt_deg(aoa)}，超过告警线 {_fmt_deg(aoa_limit, 0)}。",
            "减小迎角（松杆或推一点杆），迎角再大就要失速了。",
        ))

    # ── 超速 ──────────────────────────────────────────────────────────
    if vne is not None and airspeed is not None:
        if airspeed > vne:
            out.append(_warn(
                "overspeed", "danger",
                f"空速 {_fmt_speed(airspeed)} 已超过最大允许速度 Vne {_fmt_speed(vne)}。",
                "收油门减速，机体可能随时解体。",
            ))
        elif airspeed >= vne * overspeed_ratio:
            out.append(_warn(
                "near_overspeed", "warning",
                f"空速 {_fmt_speed(airspeed)} 已到 Vne {_fmt_speed(vne)} 的 {overspeed_ratio * 100:.0f}%。",
                "该收油门了，再快就超 Vne。",
            ))
    if max_mach is not None and mach is not None and mach > max_mach:
        out.append(_warn(
            "over_mach", "danger",
            f"马赫数 {mach:.2f} 超过上限 {max_mach:.2f}。",
            "收油门减速。",
        ))

    # ── 过载 ──────────────────────────────────────────────────────────
    if g is not None:
        if max_g is not None and g > max_g:
            out.append(_warn("over_g", "danger", f"过载 {g:.2f} G 超过上限 {max_g:.2f} G。", "减小拉杆量。"))
        elif max_g is not None and g > max_g * 0.85:
            out.append(_warn("near_over_g", "warning", f"过载 {g:.2f} G，接近上限 {max_g:.2f} G。", "收着点拉杆。"))
        if min_g is not None and g < min_g:
            out.append(_warn("under_g", "danger", f"负过载 {g:.2f} G 低于下限 {min_g:.2f} G。", "别猛推杆。"))

    # ── 近地与下降率 ──────────────────────────────────────────────────
    if agl is not None and in_air:
        if agl < terrain_agl and vs is not None and vs < -sink_rate:
            out.append(_warn(
                "terrain", "danger",
                f"离地只有 {_fmt_alt(agl)}，还在以 {_fmt_vs(vs)} 往下掉。",
                "拉杆、加油门，先把下降率压住。",
            ))
        elif agl < terrain_agl and vs is not None and vs < -1.0:
            out.append(_warn(
                "low_altitude", "caution",
                f"离地 {_fmt_alt(agl)}，正在下降。",
                "注意高度，确认是不是在进近。",
            ))
        elif agl < 100.0:
            out.append(_warn("very_low", "caution", f"离地仅 {_fmt_alt(agl)}。", "注意高度。"))

    # ── 坡度 ──────────────────────────────────────────────────────────
    if roll is not None and in_air and abs(roll) > bank:
        side = "右" if roll > 0 else "左"
        level = "danger" if abs(roll) > bank * 1.3 else "warning"
        out.append(_warn(
            "bank", level,
            f"{side}倾 {abs(roll):.0f}°，坡度超过告警线 {bank:.0f}°。",
            f"向{'左' if roll > 0 else '右'}压杆改平，坡度太大会掉高度。",
        ))

    # ── 起落架 / 襟翼 ─────────────────────────────────────────────────
    gear_down = controls.get("gear_down")
    if in_air and agl is not None and gear_down is not None and vs is not None:
        if agl < _LOW_ALTITUDE_AGL_M and vs < 0 and not _as_bool(gear_down):
            out.append(_warn(
                "gear_up", "danger",
                f"离地 {_fmt_alt(agl)} 且在下降，但起落架还是收起的。",
                "放起落架（默认 G 键）。",
            ))
    if gear_limit is not None and airspeed is not None and _as_bool(gear_down) and airspeed > gear_limit:
        out.append(_warn(
            "gear_overspeed", "warning",
            f"起落架放下状态，空速 {_fmt_speed(airspeed)} 超过起落架限速 {_fmt_speed(gear_limit)}。",
            "减速或收起落架。",
        ))
    flaps = _num(controls.get("flaps"))
    if flap_limit is not None and airspeed is not None and flaps is not None and flaps > 0:
        if airspeed > flap_limit:
            out.append(_warn(
                "flap_overspeed", "warning",
                f"襟翼已放出，空速 {_fmt_speed(airspeed)} 超过襟翼限速 {_fmt_speed(flap_limit)}。",
                "减速或收襟翼，否则襟翼会损坏。",
            ))

    # ── 发动机 ────────────────────────────────────────────────────────
    # 注意：`running` 缺失 ≠ 停车。
    # 模组用 SetIfNotNull 写这个字段 —— 反射读不到就直接省略它。
    # 旧代码把缺失当 False，于是在满油门爬升时误报"发动机全部停车"。
    # 现在只有**明确读到 bool** 的引擎才计入统计。
    running = 0
    total = 0
    broken_engines: List[str] = []
    for idx, item in enumerate(engines):
        if not isinstance(item, dict):
            continue
        name = _as_text(item.get("name")) or f"#{idx}"
        if _as_bool(item.get("on_fire")):
            out.append(_warn("engine_fire", "danger", f"发动机「{name}」着火了。", "关车灭火（看机体有没有灭火开关），准备迫降。"))
        if _as_bool(item.get("damaged")):
            broken_engines.append(name)
        flag = item.get("running")
        if isinstance(flag, bool):
            total += 1
            if flag:
                running += 1

    # 物理交叉验证：油门推着而且还在爬升，发动机不可能全停。
    # 这条兜底能挡住任何"字段读不到 → 误判停机"的情况。
    throttle = _num(controls.get("throttle"))
    vs = _num(flight.get("vertical_speed"))
    plugged_in = throttle is not None and throttle > 0.05
    climbing = vs is not None and vs > 0.5
    obviously_powered = plugged_in and climbing

    if total > 0 and running == 0 and in_air and not obviously_powered:
        out.append(_warn(
            "all_engines_out", "danger",
            f"{total} 台发动机全部停车，空中没有动力。",
            "立刻找迫降场地，建立最佳滑翔速度；重启试试看能不能点着。",
        ))
    elif total > 1 and 0 < running < total:
        out.append(_warn(
            "engine_out", "warning",
            f"{total} 台发动机里只有 {running} 台在转。",
            "检查停车的那台，注意推力不对称带来的偏航。",
        ))
    if broken_engines:
        out.append(_warn(
            "engine_damaged", "warning",
            "发动机受损：" + "、".join(broken_engines[:4]),
            "推力可能下降，注意油量消耗与航程。",
        ))

    # ── 机体损伤 / 起火 ───────────────────────────────────────────────
    if _as_bool(damage.get("on_fire")):
        out.append(_warn("on_fire", "danger", "机体着火。", "尽快迫降，火势会持续损伤机体。"))
    integrity = _num(damage.get("integrity_percent"))
    if integrity is not None and integrity < 60.0:
        out.append(_warn(
            "damaged", "warning",
            f"机体完整度只剩 {integrity:.0f}%。",
            "结构已经很脆弱，别做大过载机动。",
        ))
    broken_parts = _num(damage.get("broken_parts"))
    if broken_parts is not None and broken_parts > 0:
        out.append(_warn(
            "broken_parts", "warning",
            f"有 {broken_parts:.0f} 个零件损坏/脱落。",
            "检查气动面是否还在，操纵可能已经不对称。",
        ))

    # ── 油量 ──────────────────────────────────────────────────────────
    fuel_pct = _num(fuel.get("percent"))
    if fuel_pct is None:
        current = _num(fuel.get("current_kg"))
        capacity = _num(fuel.get("capacity_kg"))
        if current is not None and capacity not in (None, 0.0) and capacity > 0:
            fuel_pct = current / capacity * 100.0
    if fuel_pct is not None:
        if fuel_pct < low_fuel / 4.0:
            out.append(_warn(
                "fuel_critical", "danger",
                f"油量只剩 {fuel_pct:.0f}%，快见底了。",
                "马上找地方落地，别再爬高。",
            ))
        elif fuel_pct < low_fuel:
            out.append(_warn(
                "low_fuel", "warning",
                f"油量 {fuel_pct:.0f}%，低于告警线 {low_fuel:.0f}%。",
                "规划一下回场或就近降落。",
            ))

    out.sort(key=lambda item: _LEVEL_ORDER.get(_as_text(item.get("level")), 9))
    return out


# ---------------------------------------------------------------------------
# 检查单（纯函数）
# ---------------------------------------------------------------------------

# 每一项的判定结果：done=已做到、todo=该做还没做、unknown=读不到数据不敢下结论。
_ITEM_TODO = "todo"
_ITEM_DONE = "done"
_ITEM_UNKNOWN = "unknown"


def _item(item_id: str, label: str, status: str, detail: str = "") -> Dict[str, Any]:
    return {"id": item_id, "label": label, "status": status, "detail": detail}


def _tri(value: Optional[bool], yes: str, no: str) -> Tuple[str, str]:
    """把「某个条件是否成立」变成 (status, detail)。None 就是读不到。"""
    if value is None:
        return _ITEM_UNKNOWN, "读不到这项数据"
    if value:
        return _ITEM_DONE, yes
    return _ITEM_TODO, no


def _build_checklist(
    phase: str,
    state: Dict[str, Any],
    aircraft: Dict[str, Any],
) -> Dict[str, Any]:
    """按当前阶段生成检查单。

    返回 ``{"phase","phase_label","items","done_count","pending_count","unknown_count"}``。
    **读不到的项一律标 unknown**，绝不猜成已完成 —— 那比不做更危险。
    """
    flight = _as_dict(state.get("flight"))
    controls = _as_dict(state.get("controls"))
    damage = _as_dict(aircraft.get("damage"))
    fuel = _as_dict(aircraft.get("fuel"))
    limits = _as_dict(aircraft.get("limits"))
    engines = _as_list(aircraft.get("engines"))

    throttle = _num(controls.get("throttle"))
    flaps = _num(controls.get("flaps"))
    gear_down = controls.get("gear_down")
    brake = _num(controls.get("brake"))
    trim = _num(controls.get("trim"))
    agl = _num(flight.get("altitude_agl"))
    vs = _num(flight.get("vertical_speed"))
    airspeed = _num(flight.get("airspeed"))

    items: List[Dict[str, Any]] = []

    if phase in {"parked", "taxi"}:
        check: Optional[bool]
        check = None if flaps is None else flaps > 0
        status, detail = _tri(check, f"襟翼已放到 {_fmt_pct(flaps)}", "襟翼还在收上位，起飞前要放起飞襟翼")
        items.append(_item("flaps", "襟翼放到起飞位", status, detail))

        check = None if gear_down is None else _as_bool(gear_down)
        status, detail = _tri(check, "起落架已放下", "起落架没收/未放下，起飞前必须放下")
        items.append(_item("gear", "起落架放下并锁定", status, detail))

        check = None if trim is None else abs(trim) < 0.5
        status, detail = _tri(check, f"配平 {_fmt_deg(trim)}", f"配平 {_fmt_deg(trim)} 偏得有点多，起飞前归零或按机型设置")
        items.append(_item("trim", "配平设置到起飞位", status, detail))

        check = None if brake is None else brake < 0.1
        status, detail = _tri(check, "刹车已松开", "刹车还踩着，松开才滑得动")
        items.append(_item("brake", "松开刹车", status, detail))

        # 只有明确读到开关状态的引擎才参与判断；读不到就当未知，
        # 不要因为字段缺失就把发动机判成"没转"。
        known_engines = [e for e in engines if isinstance(e, dict) and isinstance(e.get("running"), bool)]
        running = None
        if known_engines:
            running = sum(1 for e in known_engines if e["running"])
        check = None if running is None else running > 0
        status, detail = _tri(
            check,
            f"{running}/{len(known_engines)} 台发动机在转" if running is not None else "",
            "发动机都没转，先启动",
        )
        items.append(_item("engines", "发动机启动并稳定", status, detail))

        fuel_pct = _num(fuel.get("percent"))
        check = None if fuel_pct is None else fuel_pct >= 20.0
        status, detail = _tri(
            check,
            f"油量 {_fmt_pct(fuel_pct)}" if fuel_pct is not None else "",
            f"油量只有 {_fmt_pct(fuel_pct)}，起飞前想清楚够不够" if fuel_pct is not None else "",
        )
        items.append(_item("fuel", "油量充足", status, detail))

        integrity = _num(damage.get("integrity_percent"))
        check = None if integrity is None else integrity >= 95.0
        status, detail = _tri(
            check,
            f"机体完整度 {_fmt_pct(integrity)}" if integrity is not None else "",
            f"机体完整度只有 {_fmt_pct(integrity)}，先修一下" if integrity is not None else "",
        )
        items.append(_item("airframe", "机体状态良好", status, detail))

    elif phase in {"takeoff_roll", "initial_climb"}:
        check = None if throttle is None else throttle >= 0.9
        status, detail = _tri(check, f"油门 {_fmt_throttle(throttle)}", f"油门才 {_fmt_throttle(throttle)}，起飞推满")
        items.append(_item("throttle", "油门推到起飞推力", status, detail))

        if phase == "takeoff_roll":
            rotation = _num(limits.get("rotation_speed_mps"))
            check = None
            detail = "机体没给抬轮速度，看着速度自己掌握"
            if rotation is not None and airspeed is not None:
                check = airspeed >= rotation
                detail = f"当前 {_fmt_speed(airspeed)}，抬轮速度 {_fmt_speed(rotation)}"
            elif airspeed is not None:
                detail = f"当前 {_fmt_speed(airspeed)}，机体未提供抬轮速度"
            status, detail2 = _tri(check, "速度够了，可以抬轮", detail)
            items.append(_item("rotate", "到达抬轮速度后拉杆", status, detail2))
        else:
            check = None if vs is None else vs > 1.0
            status, detail = _tri(check, f"升降率 {_fmt_vs(vs)}", f"升降率 {_fmt_vs(vs)}，先建立正上升率")
            items.append(_item("positive_rate", "建立正上升率", status, detail))

            check = None if (gear_down is None or agl is None) else (not _as_bool(gear_down) or agl < 50.0)
            status, detail = _tri(check, "起落架已收起", f"离地 {_fmt_alt(agl)} 了，起落架可以收了")
            items.append(_item("gear_up", "正上升率后收起落架", status, detail))

            check = None if (flaps is None or agl is None) else (flaps <= 0 or agl < 100.0)
            status, detail = _tri(check, "襟翼已收上", f"离地 {_fmt_alt(agl)}，速度上来后收襟翼")
            items.append(_item("flaps_up", "爬升到安全高度收襟翼", status, detail))

    elif phase in {"climb", "cruise", "descent"}:
        pitch_input = _num(controls.get("pitch_input"))
        check = None if pitch_input is None else abs(pitch_input) < 0.2
        status, detail = _tri(
            check,
            "升降舵输入接近零，配平到位",
            f"升降舵输入 {_fmt(pitch_input, 2)}，配平一下能省力",
        )
        items.append(_item("trim", "配平到位（不用一直带杆）", status, detail))

        check = None if throttle is None else 0.1 <= throttle <= 1.0
        status, detail = _tri(check, f"油门 {_fmt_throttle(throttle)}", "油门状态异常")
        items.append(_item("throttle", "油门稳定", status, detail))

        fuel_pct = _num(fuel.get("percent"))
        check = None if fuel_pct is None else fuel_pct >= 25.0
        status, detail = _tri(
            check,
            f"油量 {_fmt_pct(fuel_pct)}" if fuel_pct is not None else "",
            f"油量 {_fmt_pct(fuel_pct)}，注意航程" if fuel_pct is not None else "",
        )
        items.append(_item("fuel", "油量监控", status, detail))

        integrity = _num(damage.get("integrity_percent"))
        check = None if integrity is None else integrity >= 80.0
        status, detail = _tri(
            check,
            f"机体完整度 {_fmt_pct(integrity)}" if integrity is not None else "",
            f"机体完整度 {_fmt_pct(integrity)}，注意损伤" if integrity is not None else "",
        )
        items.append(_item("airframe", "机体状态", status, detail))

    elif phase == "approach":
        check = None if gear_down is None else _as_bool(gear_down)
        status, detail = _tri(check, "起落架已放下", "进近了但起落架还没放")
        items.append(_item("gear", "放下起落架", status, detail))

        check = None if flaps is None else flaps >= 0.5
        status, detail = _tri(check, f"襟翼 {_fmt_pct(flaps)}", f"襟翼只放了 {_fmt_pct(flaps)}，着陆襟翼要放到位")
        items.append(_item("flaps", "襟翼放到着陆位", status, detail))

        approach_speed = _num(limits.get("approach_speed_mps"))
        if approach_speed is not None and airspeed is not None:
            check = abs(airspeed - approach_speed) <= approach_speed * 0.15
            status, detail = _tri(
                check,
                f"进近速度正常（{_fmt_speed(airspeed)}）",
                f"当前 {_fmt_speed(airspeed)}，建议进近速度 {_fmt_speed(approach_speed)}",
            )
        elif airspeed is not None:
            status, detail = _ITEM_UNKNOWN, f"当前 {_fmt_speed(airspeed)}，机体没给建议进近速度"
        else:
            status, detail = _ITEM_UNKNOWN, "读不到空速"
        items.append(_item("speed", "速度在进近范围内", status, detail))

        check = None if vs is None else -5.0 <= vs <= 0.5
        status, detail = _tri(check, f"下降率 {_fmt_vs(vs)}", f"下降率 {_fmt_vs(vs)}，稳住别掉太快")
        items.append(_item("sink", "下降率稳定", status, detail))

        check = None if agl is None else agl >= 30.0
        status, detail = _tri(check, f"离地 {_fmt_alt(agl)}", f"离地只剩 {_fmt_alt(agl)}，准备拉平")
        items.append(_item("flare", "高度够，准备拉平", status, detail))

    elif phase == "rollout":
        check = None if throttle is None else throttle <= 0.2
        status, detail = _tri(check, f"油门 {_fmt_throttle(throttle)}", "油门收到慢车")
        items.append(_item("throttle_idle", "油门收到慢车", status, detail))

        check = None if brake is None else brake > 0.1
        status, detail = _tri(check, "刹车已踩下", "用刹车减速（默认 B 键）")
        items.append(_item("brake", "使用刹车减速", status, detail))

        check = None if flaps is None else flaps <= 0
        status, detail = _tri(check, "襟翼已收上", "襟翼收上，减小阻力")
        items.append(_item("flaps_up", "收襟翼", status, detail))

    elif phase == "crashed":
        items.append(_item("reset", "重新开始一局", _ITEM_TODO, "机体已毁，检查单没有意义了"))
    else:
        items.append(_item("enter_world", "进入飞行场景", _ITEM_TODO, "当前不在飞行世界里，先开一架飞机出来"))

    done = sum(1 for i in items if i["status"] == _ITEM_DONE)
    pending = sum(1 for i in items if i["status"] == _ITEM_TODO)
    unknown = sum(1 for i in items if i["status"] == _ITEM_UNKNOWN)

    return {
        "phase": phase,
        "phase_label": _phase_label(phase),
        "items": items,
        "done_count": done,
        "pending_count": pending,
        "unknown_count": unknown,
        "total_count": len(items),
    }


# ---------------------------------------------------------------------------
# 导航（纯函数）
# ---------------------------------------------------------------------------


def _turn_advice(heading: Optional[float], bearing: Optional[float]) -> Dict[str, Any]:
    """给出「该往哪边转、转多少度」的结论。"""
    if heading is None or bearing is None:
        return {"turn_direction": "未知", "turn_degrees": None, "text": "航向或方位读不到，没法给出转向建议。"}
    diff = (bearing - heading + 540.0) % 360.0 - 180.0
    degrees = abs(diff)
    if degrees < 3.0:
        return {
            "turn_direction": "保持",
            "turn_degrees": round(degrees, 1),
            "text": f"航向基本对准了（只差 {degrees:.0f}°），保持当前航向。",
        }
    direction = "右转" if diff > 0 else "左转"
    return {
        "turn_direction": direction,
        "turn_degrees": round(degrees, 1),
        "text": f"{direction} {degrees:.0f}° 对准目标。",
    }


def _eta_seconds(distance: Optional[float], speed: Optional[float]) -> Optional[float]:
    if distance is None or speed is None or speed <= 1.0:
        return None
    return distance / speed


# ---------------------------------------------------------------------------
# 摘要（纯函数）
# ---------------------------------------------------------------------------


def _summarize_status(state: Dict[str, Any], environment: Dict[str, Any]) -> str:
    flight = _as_dict(state.get("flight"))
    controls = _as_dict(state.get("controls"))
    aircraft = _as_dict(state.get("aircraft"))
    phase = _judge_phase(state)

    name = _as_text(aircraft.get("name")) or _as_text(state.get("aircraft_name"))
    parts: List[str] = []
    if name:
        parts.append(f"机体「{name}」")
    parts.append(f"阶段：{_phase_label(phase)}")

    agl = _num(flight.get("altitude_agl"))
    msl = _num(flight.get("altitude_msl"))
    if agl is not None or msl is not None:
        text = f"高度 离地 {_fmt_alt(agl)}"
        if msl is not None:
            text += f"／海拔 {_fmt_alt(msl)}"
        parts.append(text)

    airspeed = _num(flight.get("airspeed"))
    if airspeed is not None:
        parts.append(f"空速 {_fmt_speed(airspeed)}")

    vs = _num(flight.get("vertical_speed"))
    if vs is not None:
        parts.append(f"升降率 {_fmt_vs(vs)}")

    heading = _num(flight.get("heading"))
    if heading is not None:
        parts.append(f"航向 {_fmt_heading(heading)}")

    throttle = _num(controls.get("throttle"))
    if throttle is not None:
        parts.append(f"油门 {_fmt_throttle(throttle)}")

    weather = _as_text(environment.get("weather"))
    if weather:
        parts.append(f"天气 {weather}")
    wind = _as_dict(environment.get("wind"))
    wind_speed = _num(wind.get("speed_mps"))
    if wind_speed is not None and wind_speed > 0.5:
        parts.append(f"风 {_fmt_speed(wind_speed)} 来自 {_fmt_heading(wind.get('direction_deg'))}")

    return "；".join(parts) + "。"


def _summarize_instruments(state: Dict[str, Any]) -> str:
    flight = _as_dict(state.get("flight"))
    parts: List[str] = []

    pitch = _num(flight.get("pitch"))
    roll = _num(flight.get("roll"))
    if pitch is not None or roll is not None:
        attitude = []
        if pitch is not None:
            attitude.append(f"{'抬头' if pitch >= 0 else '低头'} {abs(pitch):.1f}°")
        if roll is not None:
            attitude.append(f"{'右倾' if roll >= 0 else '左倾'} {abs(roll):.1f}°")
        parts.append("姿态 " + "、".join(attitude))

    aoa = _num(flight.get("aoa"))
    if aoa is not None:
        parts.append(f"迎角 {_fmt_deg(aoa)}")
    sideslip = _num(flight.get("sideslip"))
    if sideslip is not None:
        parts.append(f"侧滑 {_fmt_deg(sideslip)}")
    g = _num(flight.get("g_force"))
    if g is not None:
        parts.append(f"过载 {g:.2f} G")
    mach = _num(flight.get("mach"))
    if mach is not None:
        parts.append(f"马赫 {mach:.2f}")

    airspeed = _num(flight.get("airspeed"))
    gs = _num(flight.get("ground_speed"))
    if airspeed is not None:
        text = f"空速 {_fmt_speed(airspeed)}"
        if gs is not None:
            text += f"，地速 {_fmt_speed(gs)}"
        parts.append(text)

    return "；".join(parts) + "。" if parts else "读不到姿态与气动数据。"


def _summarize_aircraft(aircraft: Dict[str, Any]) -> str:
    if not aircraft:
        return "读不到机体数据。"
    parts: List[str] = []
    name = _as_text(aircraft.get("name"))
    if name:
        parts.append(f"机体「{name}」")
    kind = _as_text(aircraft.get("type"))
    if kind:
        parts.append(f"类型 {kind}")
    count = _num(aircraft.get("part_count"))
    if count is not None:
        parts.append(f"{count:.0f} 个零件")

    fuel = _as_dict(aircraft.get("fuel"))
    pct = _num(fuel.get("percent"))
    if pct is None:
        current = _num(fuel.get("current_kg"))
        capacity = _num(fuel.get("capacity_kg"))
        if current is not None and capacity:
            pct = current / capacity * 100.0
    if pct is not None:
        current = _num(fuel.get("current_kg"))
        text = f"油量 {_fmt_pct(pct)}"
        if current is not None:
            text += f"（{current:,.0f} kg）"
        parts.append(text)

    engines = _as_list(aircraft.get("engines"))
    if engines:
        # 只统计明确读到状态的引擎；读不到就报"状态未知"，不要报 0
        known = [e for e in engines if isinstance(e, dict) and isinstance(e.get("running"), bool)]
        if known:
            running = sum(1 for e in known if e["running"])
            parts.append(f"发动机 {running}/{len(known)} 台在转")
        else:
            parts.append(f"发动机 {len(engines)} 台（开关状态读不到）")

    gear = _as_dict(aircraft.get("gear"))
    if gear:
        parts.append(f"起落架 {_fmt_gear(gear.get('state', gear.get('down')))}")

    damage = _as_dict(aircraft.get("damage"))
    integrity = _num(damage.get("integrity_percent"))
    if integrity is not None:
        parts.append(f"机体完整度 {_fmt_pct(integrity)}")

    return "；".join(parts) + "。" if parts else "读不到机体数据。"


def _summarize_navigation(nav: Dict[str, Any], state: Dict[str, Any]) -> str:
    if not nav:
        return "读不到导航数据。"
    parts: List[str] = []
    heading = _num(nav.get("heading"))
    if heading is not None:
        parts.append(f"当前航向 {_fmt_heading(heading)}")

    target = _as_dict(nav.get("target"))
    if target:
        name = _as_text(target.get("name")) or "目标点"
        distance = _num(target.get("distance"))
        bearing = _num(target.get("bearing"))
        text = f"目标「{name}」"
        if distance is not None:
            text += f" 距离 {_fmt_dist(distance)}"
        if bearing is not None:
            text += f"，方位 {_fmt_heading(bearing)}"
        parts.append(text)
        advice = _turn_advice(heading, bearing)
        parts.append(advice["text"])
        flight = _as_dict(state.get("flight"))
        gs = _num(flight.get("ground_speed"))
        eta = _eta_seconds(distance, gs)
        if eta is not None:
            parts.append(f"按当前地速约 {_fmt_duration(eta)} 到达")
    else:
        airport = _as_dict(nav.get("nearest_airport"))
        if airport:
            name = _as_text(airport.get("name")) or "最近机场"
            distance = _num(airport.get("distance"))
            bearing = _num(airport.get("bearing"))
            text = f"最近机场「{name}」"
            if distance is not None:
                text += f" {_fmt_dist(distance)}"
            if bearing is not None:
                text += f"，方位 {_fmt_heading(bearing)}"
                advice = _turn_advice(heading, bearing)
                text += "，" + advice["text"]
            parts.append(text)
        else:
            parts.append("没有设定目标点，也没有读到附近机场")

    return "；".join(parts) + "。"


def _summarize_warnings(warnings: List[Dict[str, Any]]) -> str:
    if not warnings:
        return "没有告警，各项参数都在正常范围。"
    danger = [w for w in warnings if w.get("level") == "danger"]
    warning = [w for w in warnings if w.get("level") == "warning"]
    caution = [w for w in warnings if w.get("level") in {"caution", "info"}]

    parts: List[str] = []
    if danger:
        parts.append("【紧急】" + "；".join(_as_text(w.get("text")) for w in danger[:3]))
    if warning:
        parts.append("【注意】" + "；".join(_as_text(w.get("text")) for w in warning[:3]))
    if caution:
        parts.append("【提示】" + "；".join(_as_text(w.get("text")) for w in caution[:2]))
    tail = f"共 {len(warnings)} 条告警。" if len(warnings) > 6 else ""
    return " ".join(parts) + tail if parts else "没有可识别的告警。"


def _summarize_checklist(checklist: Dict[str, Any]) -> str:
    items = _as_list(checklist.get("items"))
    if not items:
        return "当前阶段没有检查项目。"
    pending = [i for i in items if i.get("status") == _ITEM_TODO]
    unknown = [i for i in items if i.get("status") == _ITEM_UNKNOWN]
    parts: List[str] = [f"阶段：{_as_text(checklist.get('phase_label'))}"]
    if pending:
        parts.append("还没做：" + "、".join(_as_text(i.get("label")) for i in pending))
    else:
        parts.append("该做的都做到位了")
    if unknown:
        parts.append(f"（{len(unknown)} 项读不到数据，无法判断）")
    return "；".join(parts) + "。"


# 自动播报的合并键：同键的排队消息只留最新一条，轮询再频繁也不会把上下文撑爆。
AUTO_BRIEF_COALESCE_KEY = "sp2_auto_brief"

# 自动播报的前缀，角色一眼能看出这是仪器读数不是主人在说话。
AUTO_BRIEF_PREFIX = "【副驾驶自动播报】"


def _auto_brief_line(brief: Dict[str, Any]) -> str:
    """把一份副驾驶简报压成一段给模型看的状态文本。

    刻意压短：这段会一直待在上下文里跟着每一轮走，写长了就是每轮白烧
    token。只留「要不要开口提醒」真正用得上的东西 —— 当前状态、危险告警、
    还没打勾的检查单项。
    """
    chunks: List[str] = []

    status = _as_text(brief.get("status_summary"))
    if status:
        chunks.append(status)

    # 机体那一段单独给：油量和发动机状态不在 status_summary 里，
    # 而这恰恰是最容易出人命的两项。
    aircraft = _summarize_aircraft(_as_dict(brief.get("aircraft")))
    if aircraft:
        chunks.append(aircraft)

    warnings = [w for w in (brief.get("warnings") or []) if isinstance(w, dict)]
    danger = [w for w in warnings if _as_text(w.get("level")) in ("danger", "critical")]
    if danger:
        chunks.append("【要立刻提醒】" + "；".join(_as_text(w.get("text")) for w in danger if _as_text(w.get("text"))))
    elif warnings:
        chunks.append("注意：" + "；".join(_as_text(w.get("text")) for w in warnings if _as_text(w.get("text"))))

    checklist = _as_dict(brief.get("checklist"))
    pending = [
        _as_text(item.get("label"))
        for item in (checklist.get("items") or [])
        if isinstance(item, dict) and _as_text(item.get("status")) == "pending" and _as_text(item.get("label"))
    ]
    if pending:
        chunks.append("该做没做：" + "；".join(pending))

    body = " ".join(chunk for chunk in chunks if chunk)
    return f"{AUTO_BRIEF_PREFIX}{body}" if body else AUTO_BRIEF_PREFIX + "暂时没有可用的仪表读数。"


# ---------------------------------------------------------------------------
# 插件主体
# ---------------------------------------------------------------------------


@neko_plugin
class Sp2CopilotPlugin(Sp2UiMixin, NekoPluginBase):
    """《简单飞机2》副驾驶插件（只读）。

    ``Sp2UiMixin`` 提供副驾驶仪表盘（Hosted UI）的 ``@ui.context`` / ``@ui.action``；
    它只用本类已有的属性与方法，不改变桥接与 LLM 工具行为。
    """

    def __init__(self, ctx: Any):
        super().__init__(ctx)
        self.file_logger = self.enable_file_logging(log_level="INFO")
        self.logger = self.file_logger

        self._cfg: Dict[str, Any] = {}
        self._bridge_url: str = DEFAULT_BRIDGE_URL
        self._timeout: float = 12.0
        self._poll_interval: float = 3.0
        self._thresholds: Dict[str, float] = {}

        self._client: Optional[httpx.AsyncClient] = None
        self._client_loop: Any = None

        # 自动播报（见 _auto_brief_loop）
        self._auto_brief_enabled: bool = True
        self._auto_brief_interval: float = 20.0
        self._auto_brief_task: Any = None
        self._auto_brief_stop: Any = None
        self._auto_brief_last_text: str = ""
        self._auto_brief_last_ok: Optional[bool] = None

        # 面板用的最近一次连通性（见 ui_api.py）
        self._ui_last_connected: Optional[bool] = None
        self._ui_last_error: str = ""

    # -- 基础设施 ---------------------------------------------------------

    def _get_client(self) -> httpx.AsyncClient:
        """按事件循环缓存客户端（宿主会在不同 asyncio.run 中调用）。"""
        import asyncio

        try:
            loop: Any = asyncio.get_running_loop()
        except RuntimeError:  # pragma: no cover
            loop = None

        if self._client is None or self._client.is_closed or self._client_loop is not loop:
            self._client = httpx.AsyncClient(
                follow_redirects=True,
                timeout=self._timeout,
                headers={"User-Agent": _USER_AGENT},
            )
            self._client_loop = loop
        return self._client

    async def _get_json(self, path: str) -> Dict[str, Any]:
        """GET 一个 JSON 接口。"""
        url = f"{self._bridge_url.rstrip('/')}{path}"
        client = self._get_client()
        try:
            response = await client.get(url, timeout=self._timeout)
        except httpx.TimeoutException as exc:
            raise SdkError(
                "读取游戏数据超时。SP2 在加载存档、切换场景或联机卡顿时可能会卡一下，可以稍后重试。"
            ) from exc
        except httpx.HTTPError as exc:
            raise SdkError(
                f"连不上 SP2-Bridge 桥接服务（{self._bridge_url}）。请确认："
                "1) 游戏正在运行；2) 已安装 SP2-Bridge 模组；3) 装好后重启过游戏。"
            ) from exc

        if response.status_code >= 400:
            raise SdkError(f"SP2-Bridge 返回状态 {response.status_code}。")
        try:
            return response.json()
        except ValueError as exc:
            raise SdkError("SP2-Bridge 返回的内容不是合法 JSON。") from exc

    async def _fetch(self, path: str) -> Tuple[Dict[str, Any], str]:
        """读一个接口，失败不抛异常，返回 ``(数据 或 {}, 错误说明)``。"""
        try:
            data = await self._get_json(path)
        except SdkError as exc:
            return {}, str(exc)
        except Exception as exc:  # pragma: no cover - 兜底
            self.logger.warning("[sp2_copilot] 读取 {} 失败：{}", path, exc)
            return {}, f"读取 {path} 失败：{exc}"
        if not isinstance(data, dict):
            return {}, f"{path} 返回的不是对象。"
        return data, ""

    async def _snapshot(self) -> Dict[str, Any]:
        """一次性拿齐四个接口的数据（面板与综合简报用）。"""
        import asyncio

        (state, state_err), (aircraft, ac_err), (nav, nav_err), (env, env_err) = await asyncio.gather(
            self._fetch("/state"),
            self._fetch("/aircraft"),
            self._fetch("/navigation"),
            self._fetch("/environment"),
        )
        errors = {k: v for k, v in {
            "state": state_err, "aircraft": ac_err, "navigation": nav_err, "environment": env_err,
        }.items() if v}
        return {
            "state": state,
            "aircraft": aircraft,
            "navigation": nav,
            "environment": env,
            "errors": errors,
            "connected": bool(state),
        }

    def _thresholds_dict(self) -> Dict[str, float]:
        return {
            "terrain_agl_m": self._thresholds.get("terrain_agl_m", DEFAULT_TERRAIN_AGL_M),
            "sink_rate_mps": self._thresholds.get("sink_rate_mps", DEFAULT_SINK_RATE_MPS),
            "bank_deg": self._thresholds.get("bank_deg", DEFAULT_BANK_DEG),
            "low_fuel_percent": self._thresholds.get("low_fuel_percent", DEFAULT_LOW_FUEL_PCT),
            "aoa_deg": self._thresholds.get("aoa_deg", DEFAULT_AOA_DEG),
            "overspeed_ratio": self._thresholds.get("overspeed_ratio", DEFAULT_OVERSPEED_RATIO),
        }

    def _offline(self, message: str) -> Dict[str, Any]:
        """连不上游戏时的统一输出。

        ``is_error=False`` + ``ok=False``：让模型知道「调到了但拿不到数据」，
        并明确要求它如实告诉用户，而不是编一串仪表数字出来。
        """
        return {
            "output": {
                "ok": False,
                "connected": False,
                "bridge_url": self._bridge_url,
                "message": (
                    f"{message}\n"
                    "请如实告诉用户现在连不上游戏、读不到仪表数据，不要凭空编造任何数字。"
                ),
            },
            "is_error": False,
        }

    # -- 生命周期 ---------------------------------------------------------

    @lifecycle(id="startup")
    async def on_startup(self, **_):
        try:
            cfg = await self.config.dump(timeout=5.0)
        except Exception as exc:  # pragma: no cover
            self.logger.warning("读取配置失败，使用默认值: {}", exc)
            cfg = {}
        cfg = cfg if isinstance(cfg, dict) else {}
        self._cfg = cfg

        section = _as_dict(cfg.get("sp2_copilot"))
        self._bridge_url = _as_text(section.get("bridge_url")) or DEFAULT_BRIDGE_URL

        try:
            self._timeout = float(section.get("timeout_seconds", 12))
        except (TypeError, ValueError):
            self._timeout = 12.0
        self._timeout = _clamp(self._timeout, 3.0, 60.0)

        try:
            self._poll_interval = float(section.get("poll_interval_seconds", 3))
        except (TypeError, ValueError):
            self._poll_interval = 3.0
        self._poll_interval = _clamp(self._poll_interval, 1.0, 30.0)

        # 自动播报：默认开。关掉就退回问答式（只在你问了才读仪表）。
        self._auto_brief_enabled = _as_bool(section.get("auto_brief_enabled"), True)
        try:
            brief_interval = float(section.get("auto_brief_interval_seconds", 20))
        except (TypeError, ValueError):
            brief_interval = 20.0
        self._auto_brief_interval = _clamp(brief_interval, 5.0, 120.0)

        self._thresholds = {
            "terrain_agl_m": _cfg_number(section, "terrain_warning_agl_m", DEFAULT_TERRAIN_AGL_M, 30.0, 1000.0),
            "sink_rate_mps": _cfg_number(section, "sink_rate_warning_mps", DEFAULT_SINK_RATE_MPS, 1.0, 30.0),
            "bank_deg": _cfg_number(section, "bank_warning_deg", DEFAULT_BANK_DEG, 15.0, 90.0),
            "low_fuel_percent": _cfg_number(section, "low_fuel_warning_percent", DEFAULT_LOW_FUEL_PCT, 1.0, 50.0),
            "aoa_deg": _cfg_number(section, "aoa_warning_deg", DEFAULT_AOA_DEG, 5.0, 30.0),
            "overspeed_ratio": _cfg_number(section, "overspeed_warning_ratio", DEFAULT_OVERSPEED_RATIO, 0.5, 1.0),
        }

        self.logger.info(
            "Sp2Copilot 已就绪（只读副驾驶），桥接地址={} 超时={} 轮询={} 自动播报={}",
            self._bridge_url,
            self._timeout,
            self._poll_interval,
            f"开/{self._auto_brief_interval:g}s" if self._auto_brief_enabled else "关",
        )
        if self._auto_brief_enabled:
            self._start_auto_brief()
        return Ok({
            "status": "ready",
            "mode": "read-only",
            "bridge_url": self._bridge_url,
            "poll_interval_seconds": self._poll_interval,
            "auto_brief": self._auto_brief_enabled,
            "auto_brief_interval_seconds": self._auto_brief_interval,
        })

    @lifecycle(id="shutdown")
    async def on_shutdown(self, **_):
        await self._stop_auto_brief()
        client = self._client
        self._client = None
        self._client_loop = None
        if client is not None and not client.is_closed:
            try:
                await client.aclose()
            except Exception as exc:  # pragma: no cover
                self.logger.debug("关闭客户端出错: {}", exc)
        return Ok({"status": "stopped"})

    # -- 插件入口（面板 / 宿主调用）---------------------------------------

    @ui.action(id="sp2_status", label="读取飞行状态", tone="primary", group="读取", order=10)
    @plugin_entry(
        id="sp2_status",
        name="简单飞机2 飞行状态",
        description="读取《简单飞机2》当前飞行状态：高度、速度、升降率、航向、油门与飞行阶段。",
        timeout=30,
        llm_result_fields=["summary", "connected", "state"],
    )
    async def sp2_status(self, **_):
        state, err = await self._fetch("/state")
        if not state:
            return Ok({"connected": False, "summary": err or "读不到飞行状态。", "state": {}})
        env, _ = await self._fetch("/environment")
        return Ok({
            "connected": True,
            "summary": _summarize_status(state, env),
            "state": state,
        })

    @ui.action(id="sp2_briefing", label="生成副驾驶简报", tone="primary", group="读取", order=20)
    @plugin_entry(
        id="sp2_briefing",
        name="简单飞机2 副驾驶简报",
        description="一次读齐状态、机体、导航与环境，给出综合简报：当前阶段、告警、检查单待办与下一步建议。",
        timeout=45,
        llm_result_fields=["summary", "connected", "phase", "warnings"],
    )
    async def sp2_briefing(self, **_):
        snap = await self._snapshot()
        if not snap.get("state"):
            return Ok({
                "connected": False,
                "summary": "连不上游戏，读不到任何仪表数据。",
                "errors": snap.get("errors", {}),
            })
        return Ok(self._build_briefing(snap))

    def _build_briefing(self, snap: Dict[str, Any]) -> Dict[str, Any]:
        """把四份原始数据压成一份简报（纯计算，面板与工具共用）。"""
        state = _as_dict(snap.get("state"))
        aircraft = _as_dict(snap.get("aircraft"))
        nav = _as_dict(snap.get("navigation"))
        env = _as_dict(snap.get("environment"))

        phase = _judge_phase(state)
        warnings = _scan_warnings(state, aircraft, self._thresholds_dict())
        checklist = _build_checklist(phase, state, aircraft)

        status_text = _summarize_status(state, env)
        warn_text = _summarize_warnings(warnings)
        checklist_text = _summarize_checklist(checklist)
        nav_text = _summarize_navigation(nav, state)

        return {
            "connected": True,
            "phase": phase,
            "phase_label": _phase_label(phase),
            "summary": f"{status_text} {warn_text} {checklist_text}",
            "status_summary": status_text,
            "warning_summary": warn_text,
            "checklist_summary": checklist_text,
            "navigation_summary": nav_text,
            "warnings": warnings,
            "checklist": checklist,
            "state": state,
            "aircraft": aircraft,
            "navigation": nav,
            "environment": env,
            "errors": _as_dict(snap.get("errors")),
            "read_only": True,
        }

    # -- 自动播报（游戏在跑时，让角色每轮对话都提前拿到数据）----------------

    async def _auto_brief_once(self) -> None:
        """读一次仪表，把压缩后的状态塞进上下文。

        用 ``visibility=[]`` + ``ai_behavior="read"``：只进模型上下文，不弹给
        用户看、也不打断当前这轮对话；等主人下次开口时，角色已经知道飞机
        现在什么状况，于是不用临场调工具也能直接接话。
        ``coalesce_key`` 让排队区里只保留最新一条，轮询再快也不会越攒越多。
        """
        try:
            snap = await self._snapshot()
        except Exception as exc:  # 桥接进程没起来时 httpx 会抛各种连接错误
            self.logger.debug("自动播报取数失败: {}", exc)
            return

        connected = bool(snap.get("connected"))
        if connected:
            text = _auto_brief_line(self._build_briefing(snap))
        else:
            if not self._auto_brief_last_ok:
                return  # 一直没连上过 —— 游戏没开，就别往上下文里灌噪音
            text = f"{AUTO_BRIEF_PREFIX}游戏已经退出或断开了，读不到新的仪表数据。"

        if connected == self._auto_brief_last_ok and text == self._auto_brief_last_text:
            return  # 和上次一模一样，没必要重复塞
        self._auto_brief_last_ok = connected
        self._auto_brief_last_text = text

        try:
            result = self.push_message(
                visibility=[],
                ai_behavior="read",
                coalesce_key=AUTO_BRIEF_COALESCE_KEY,
                source="sp2_auto_brief",
                parts=[{"type": "text", "text": text}],
            )
        except Exception as exc:
            self.logger.debug("自动播报推送出错: {}", exc)
            return
        if not _as_dict(result).get("submitted"):
            self.logger.debug("自动播报未进队: {}", _as_dict(result).get("reason"))

    def _start_auto_brief(self) -> None:
        """起后台轮询任务（在 on_startup 的事件循环里调用）。"""
        if self._auto_brief_task is not None and not self._auto_brief_task.done():
            return
        try:
            self._auto_brief_stop = asyncio.Event()
            self._auto_brief_task = asyncio.create_task(self._auto_brief_loop())
        except RuntimeError:
            # 没有运行中的事件循环（宿主同步启动 / 单测环境）
            self._auto_brief_task = None
            self._auto_brief_stop = None

    async def _stop_auto_brief(self) -> None:
        """停掉后台轮询任务（on_shutdown 调用）。"""
        task = self._auto_brief_task
        stop = self._auto_brief_stop
        self._auto_brief_task = None
        self._auto_brief_stop = None
        if stop is not None:
            stop.set()
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception as exc:  # pragma: no cover
                self.logger.debug("停止自动播报时出错: {}", exc)

    async def _auto_brief_loop(self) -> None:
        """先报一次，然后按间隔循环；``stop`` 被置位就退出。"""
        stop = self._auto_brief_stop
        if stop is None:
            return
        while not stop.is_set():
            try:
                await self._auto_brief_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # 单轮失败绝不拖垮后台任务
                self.logger.debug("自动播报这一轮出错: {}", exc)
            try:
                await asyncio.wait_for(stop.wait(), timeout=self._auto_brief_interval)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                raise
            return

    # -- LLM 工具 ---------------------------------------------------------

    @llm_tool(
        name="read_sp2_docs",
        description=(
            "【必看】《简单飞机2 SimplePlanes 2》与本插件的完整说明书。"
            "如果你不了解这款游戏、不清楚飞行阶段怎么判、不确定该用哪个工具、"
            "或者不知道哪些数字该怎么解读 —— 先调这个。"
            "topic 填 game（游戏知识、单位约定、默认键位）或 tools（工具用法、常见问法、铁律），"
            "不填就两份都给。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "topic": {
                    "type": "string",
                    "description": "要读哪一份：game=游戏说明书，tools=工具说明书，留空=两份都读",
                },
            },
        },
        timeout=15,
    )
    async def read_sp2_docs(self, topic: str = "", **_kwargs: Any) -> Dict[str, Any]:
        want = _as_text(topic).lower()
        if want == "game":
            body = _SP2_DOC_GAME
        elif want == "tools":
            body = _SP2_DOC_TOOLS
        else:
            body = _SP2_DOC_GAME + "\n" + _SP2_DOC_TOOLS
        return {
            "output": {
                "ok": True,
                "topic": want or "all",
                "doc": body,
            },
            "is_error": False,
        }

    @llm_tool(
        name="get_sp2_status",
        description=(
            "读取《简单飞机2》当前飞行状态：离地高度、海拔、空速、升降率、航向、油门，"
            "以及自动判定的飞行阶段（滑行/起飞/爬升/巡航/下降/进近）。"
            "用户说「我现在飞得怎么样」「多高」「多快」「在爬升吗」「到哪一步了」时调用。"
            "这是最常用的一个接口。"
        ),
        parameters={"type": "object", "properties": {}},
        timeout=30,
    )
    async def get_sp2_status(self, **_kwargs: Any) -> Dict[str, Any]:
        state, err = await self._fetch("/state")
        if not state:
            return self._offline(err or "读不到飞行状态。")
        env, _ = await self._fetch("/environment")
        flight = _as_dict(state.get("flight"))
        return {
            "output": {
                "ok": True,
                "connected": True,
                "phase": _judge_phase(state),
                "phase_label": _phase_label(_judge_phase(state)),
                "summary": _summarize_status(state, env),
                "altitude_agl_m": _num(flight.get("altitude_agl")),
                "altitude_msl_m": _num(flight.get("altitude_msl")),
                "airspeed_mps": _num(flight.get("airspeed")),
                "ground_speed_mps": _num(flight.get("ground_speed")),
                "vertical_speed_mps": _num(flight.get("vertical_speed")),
                "heading_deg": _num(flight.get("heading")),
                "throttle": _num(_as_dict(state.get("controls")).get("throttle")),
                "on_ground": _as_bool(flight.get("on_ground"), False),
                "state": state,
            },
            "is_error": False,
        }

    @llm_tool(
        name="get_sp2_instruments",
        description=(
            "读取《简单飞机2》的仪表细节：俯仰、滚转、迎角 AoA、侧滑、过载 G、马赫数、"
            "真空速与地速。用户问「姿态正常吗」「迎角多少」「是不是要失速了」"
            "「几个 G」「超音速了吗」时调用。"
        ),
        parameters={"type": "object", "properties": {}},
        timeout=30,
    )
    async def get_sp2_instruments(self, **_kwargs: Any) -> Dict[str, Any]:
        state, err = await self._fetch("/state")
        if not state:
            return self._offline(err or "读不到仪表数据。")
        flight = _as_dict(state.get("flight"))
        return {
            "output": {
                "ok": True,
                "connected": True,
                "summary": _summarize_instruments(state),
                "pitch_deg": _num(flight.get("pitch")),
                "roll_deg": _num(flight.get("roll")),
                "aoa_deg": _num(flight.get("aoa")),
                "sideslip_deg": _num(flight.get("sideslip")),
                "g_force": _num(flight.get("g_force")),
                "mach": _num(flight.get("mach")),
                "airspeed_mps": _num(flight.get("airspeed")),
                "true_airspeed_mps": _num(flight.get("true_airspeed")),
                "ground_speed_mps": _num(flight.get("ground_speed")),
                "stalled": _as_bool(flight.get("stalled"), False),
                "stall_warning": _as_bool(flight.get("stall_warning"), False),
            },
            "is_error": False,
        }

    @llm_tool(
        name="get_sp2_aircraft",
        description=(
            "读取《简单飞机2》当前机体与系统状态：发动机数量与运转情况、油量、"
            "襟翼/减速板/起落架位置、机体损伤与起火、以及机体自带的限速数据"
            "（Vne、失速速度、最大 G、襟翼与起落架限速）。"
            "用户问「油还够吗」「发动机有没有事」「起落架放了吗」「这架飞机能飞多快」时调用。"
            "注意：限速数据以机体给的为准，机体没给就是没有，不要自己编。"
        ),
        parameters={"type": "object", "properties": {}},
        timeout=30,
    )
    async def get_sp2_aircraft(self, **_kwargs: Any) -> Dict[str, Any]:
        aircraft, err = await self._fetch("/aircraft")
        if not aircraft:
            return self._offline(err or "读不到机体数据。")
        return {
            "output": {
                "ok": True,
                "connected": True,
                "summary": _summarize_aircraft(aircraft),
                "aircraft": aircraft,
            },
            "is_error": False,
        }

    @llm_tool(
        name="get_sp2_navigation",
        description=(
            "读取《简单飞机2》的导航信息：当前位置、航向，到目标点的距离与方位，"
            "最近的机场，以及风向风速。"
            "用户问「怎么飞到 XX」「还有多远」「该往哪边转」「附近有没有机场」"
            "「风从哪来」时调用。返回值里直接给了「该往哪边转多少度」和预计到达时间。"
        ),
        parameters={"type": "object", "properties": {}},
        timeout=30,
    )
    async def get_sp2_navigation(self, **_kwargs: Any) -> Dict[str, Any]:
        nav, err = await self._fetch("/navigation")
        if not nav:
            return self._offline(err or "读不到导航数据。")
        state, _ = await self._fetch("/state")
        heading = _num(nav.get("heading"))
        target = _as_dict(nav.get("target"))
        bearing = _num(target.get("bearing"))
        distance = _num(target.get("distance"))
        gs = _num(_as_dict(state.get("flight")).get("ground_speed"))
        return {
            "output": {
                "ok": True,
                "connected": True,
                "summary": _summarize_navigation(nav, state),
                "heading_deg": heading,
                "target": target or None,
                "nearest_airport": _as_dict(nav.get("nearest_airport")) or None,
                "turn": _turn_advice(heading, bearing),
                "eta_seconds": _eta_seconds(distance, gs),
                "eta_text": _fmt_duration(_eta_seconds(distance, gs)),
                "navigation": nav,
            },
            "is_error": False,
        }

    @llm_tool(
        name="get_sp2_warnings",
        description=(
            "扫描《简单飞机2》当前的所有告警：失速、超速、接近 Vne、过载超限、"
            "迎角过大、近地与下降率过大、坡度过陡、进近时起落架未放下、"
            "襟翼/起落架超速、发动机停车或着火、机体损伤、低油量。"
            "用户问「有什么要提醒我的吗」「安全吗」「是不是飞太快了」时调用，"
            "也可以主动调用来做安全监控。返回值按紧急程度排好序，"
            "danger 必须立刻告诉用户，warning 提醒一下，caution 可以顺带提。"
        ),
        parameters={"type": "object", "properties": {}},
        timeout=30,
    )
    async def get_sp2_warnings(self, **_kwargs: Any) -> Dict[str, Any]:
        state, err = await self._fetch("/state")
        if not state:
            return self._offline(err or "读不到飞行数据，无法扫描告警。")
        aircraft, _ = await self._fetch("/aircraft")
        warnings = _scan_warnings(state, aircraft, self._thresholds_dict())
        counts = {
            "danger": sum(1 for w in warnings if w.get("level") == "danger"),
            "warning": sum(1 for w in warnings if w.get("level") == "warning"),
            "caution": sum(1 for w in warnings if w.get("level") in {"caution", "info"}),
        }
        return {
            "output": {
                "ok": True,
                "connected": True,
                "summary": _summarize_warnings(warnings),
                "count": len(warnings),
                "counts": counts,
                "has_danger": counts["danger"] > 0,
                "warnings": warnings,
            },
            "is_error": False,
        }

    @llm_tool(
        name="get_sp2_checklist",
        description=(
            "按《简单飞机2》当前飞行阶段给出该做的检查单，并逐项标出状态："
            "done=已做到、todo=该做还没做、unknown=读不到数据不敢下结论。"
            "用户说「检查单」「下一步该干什么」「起飞前要做什么」「降落要做什么」"
            "「有没有漏掉什么」时调用。"
            "只提醒，不代替操作 —— 你是副驾驶，动手的是玩家。"
        ),
        parameters={"type": "object", "properties": {}},
        timeout=30,
    )
    async def get_sp2_checklist(self, **_kwargs: Any) -> Dict[str, Any]:
        state, err = await self._fetch("/state")
        if not state:
            return self._offline(err or "读不到飞行数据，没法判断该做哪些检查。")
        aircraft, _ = await self._fetch("/aircraft")
        checklist = _build_checklist(_judge_phase(state), state, aircraft)
        checklist["summary"] = _summarize_checklist(checklist)
        return {
            "output": {
                "ok": True,
                "connected": True,
                "read_only": True,
                **checklist,
            },
            "is_error": False,
        }

    @llm_tool(
        name="get_sp2_briefing",
        description=(
            "一次读齐《简单飞机2》的状态、机体、导航与环境，给出综合简报："
            "当前飞行阶段、告警摘要、检查单待办、导航建议，以及下一步该做什么。"
            "用户问「帮我总结一下」「现在什么情况」「给我个简报」时调用；"
            "不确定该用哪个工具时也可以先调它。返回内容比较多，"
            "只要一句结论的话看 summary 字段就够了。"
        ),
        parameters={"type": "object", "properties": {}},
        timeout=45,
    )
    async def get_sp2_briefing(self, **_kwargs: Any) -> Dict[str, Any]:
        snap = await self._snapshot()
        if not snap.get("state"):
            errors = _as_dict(snap.get("errors"))
            return self._offline(
                errors.get("state") or "读不到飞行数据，无法给出简报。"
            )
        briefing = self._build_briefing(snap)
        return {
            "output": {
                "ok": True,
                "read_only": True,
                **briefing,
            },
            "is_error": False,
        }


__all__ = ["Sp2CopilotPlugin"]
