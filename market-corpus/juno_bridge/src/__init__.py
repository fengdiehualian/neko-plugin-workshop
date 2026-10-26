# 和猫娘一起开火箭（朱诺新起源）插件 —— 连接 Juno: New Origins
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

"""Juno Bridge Plugin（和猫娘一起开火箭）

连接《朱诺新起源 Juno: New Origins》（原名 SimpleRockets 2），让猫娘读懂当前飞行：
- 飞船状态：高度、速度、姿态、质量、阶段、乘员
- 轨道根数：远点/近点/离心率/倾角/周期，以及「算不算入轨」
- 资源余量：燃料、电量、单组元、Delta-V、推重比、可燃烧时间
- 大气层内数据：马赫数、空气密度、气压、重力、阻力、攻角
- 星系与目标：天体数据、锁定的目标与相对速度

**这个版本是只读的** —— 插件不发送任何控制指令，火箭始终由玩家驾驶。

依赖：游戏内需安装配套的 N.E.K.O-Juno-bridge 模组（BepInEx 插件，见 ../juno-bridge）。
该模组在 127.0.0.1:21577 提供 HTTP 接口。
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

import httpx
from plugin.sdk.plugin import (
    NekoPluginBase,
    Ok,
    SdkError,
    lifecycle,
    llm_tool,
    neko_plugin,
    plugin_entry,
)

DEFAULT_BRIDGE_URL = "http://127.0.0.1:21577"
_USER_AGENT = "NekoJunoBridge/0.1 (+https://xhth.top/)"

# 自动播报：push_message 的合并键。同一个 key 的排队消息只会保留最新一条，
# 所以后台按固定间隔反复推也不会把角色的上下文越堆越长。
AUTO_BRIEF_COALESCE_KEY = "juno_auto_brief"
AUTO_BRIEF_PREFIX = "【朱诺自动播报】"


# ---------------------------------------------------------------------------
# 取值辅助
# ---------------------------------------------------------------------------


def _as_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


def _as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return default


def _num(value: Any) -> Optional[float]:
    """把游戏给的数字转成 float；null / NaN / Inf / 布尔 一律返回 None。"""
    if value is None or isinstance(value, bool):
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if f != f or f in (float("inf"), float("-inf")):  # NaN / Inf
        return None
    return f


def _extract_list(data: Any, key: str) -> List[Dict[str, Any]]:
    """把 /bodies、/vessels 这类接口的结果统一成 dict 列表。

    模组可能把数组直接放在顶层（/bodies、/vessels 实测就是这样），
    也可能包在 {"ok":true,"bodies":[...]} 里，两种都接受，
    避免以后模组改了包装就整个读不出来。

    元素里 parent 是 **null**（恒星）这类合法空值不会被丢掉 ——
    这里只负责把元素收进列表，怎么解读交给调用方。
    """
    raw: Any = None
    if isinstance(data, list):
        raw = data
    elif isinstance(data, dict):
        raw = data.get(key)
        if raw is None and isinstance(data.get("items"), list):
            raw = data.get("items")
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict)]


# ---------------------------------------------------------------------------
# 格式化（纯函数，便于测试）
# ---------------------------------------------------------------------------


def _fmt(value: Any, unit: str = "", digits: int = 1) -> str:
    """把数字格式化成人类可读的形式，取不到就返回「未知」。"""
    f = _num(value)
    if f is None:
        return "未知"
    text = f"{f:,.{digits}f}"
    return f"{text}{unit}" if unit else text


def _fmt_dist(value: Any) -> str:
    """距离/高度/半径：自动换算成 m / km / Mm。"""
    f = _num(value)
    if f is None:
        return "未知"
    af = abs(f)
    if af >= 1_000_000:
        return f"{f / 1_000_000:,.2f} Mm"
    if af >= 1_000:
        return f"{f / 1_000:,.2f} km"
    return f"{f:,.1f} m"


def _fmt_time(value: Any) -> str:
    """秒 -> 「1天2小时3分」这种。"""
    f = _num(value)
    if f is None:
        return "未知"
    neg = f < 0
    f = abs(f)
    total = int(f)
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, seconds = divmod(rem, 60)
    parts: List[str] = []
    if days:
        parts.append(f"{days}天")
    if hours:
        parts.append(f"{hours}小时")
    if minutes:
        parts.append(f"{minutes}分")
    if not parts:
        parts.append(f"{seconds}秒")
    return ("-" if neg else "") + "".join(parts)


def _trim_num(x: float, digits: int = 2) -> str:
    """去掉小数末尾多余的 0：99.97 保留，62.00 变 62。"""
    text = f"{x:,.{digits}f}".rstrip("0").rstrip(".")
    return text or "0"


def _fmt_raw(value: Any) -> str:
    """原样输出一个数字，**不做任何单位换算**（给单位未标定的字段用）。"""
    f = _num(value)
    if f is None:
        return "未知"
    if f == 0:
        return "0"
    if abs(f) >= 1_000_000 or abs(f) < 0.0001:
        return f"{f:.6g}"
    return f"{f:,.6g}"


# 质量字段的诚实标注：模组给的是游戏内部质量单位，未经标定，绝不换算成 kg / t。
MASS_UNIT_NOTE = "游戏内部单位，未标定"


def _fmt_mass(value: Any) -> str:
    """质量：**游戏内部单位，未标定**。

    实机实测：一艘只有 1 个零件的飞船 mass = 0.0418，和游戏界面上同量级的显示对不上
    （Juno 内部很可能按吨计）。所以这里**不做任何 kg→t 换算，也不声称它是 kg 或 t**，
    只把原始数值原样给出并明确标注，让模型知道不能当 kg 或 t 用。
    """
    f = _num(value)
    if f is None:
        return "未知"
    return f"{_fmt_raw(f)}（{MASS_UNIT_NOTE}）"


def _fmt_kelvin(value: Any) -> str:
    """气温：模组给的是**开尔文 K**。

    摘要必须「℃（K）」双标注 —— 279.65 K 是 6.5 ℃，
    直接念成 279.6 摄氏度是严重误读。
    """
    f = _num(value)
    if f is None:
        return "未知"
    return f"{f - 273.15:.1f} ℃（{f:.1f} K）"


def _fmt_pressure(value: Any) -> str:
    """气压：模组给的是**帕斯卡 Pa**（不是 kPa）。摘要同时给 kPa 和 Pa 两种写法。"""
    f = _num(value)
    if f is None:
        return "未知"
    return f"{f / 1000:,.1f} kPa（{f:.0f} Pa）"


def _fmt_density(value: Any) -> str:
    """空气密度：单位 **kg/m³**。"""
    f = _num(value)
    if f is None:
        return "未知"
    return f"{f:,.5f} kg/m³"


def _fmt_percent(value: Any) -> str:
    """百分比字段：模组给的是 **0~1 归一化**（battery、fuel_all_stages_percent 实机实测如此）。

    统一 ×100 显示，并把原始值附在后面，方便模型核对而不会说错。
    万一某个百分数字段给的是 0~100，也原样显示并附原始值，绝不再乘一次 100。
    """
    f = _num(value)
    if f is None:
        return "未知"
    if 0.0 <= f <= 1.0:
        return f"{_trim_num(f * 100.0, 2)}%（原始值 {f:g}）"
    if 0.0 < f <= 100.0:
        return f"{_trim_num(f, 2)}%（原始值 {f:g}）"
    return f"{_fmt_raw(f)}（原始值 {f:g}）"


def _percent_of_100(value: Any) -> Optional[float]:
    """把可能是 0-1 也可能是 0-100 的百分数字段归一到 0-100，用于判断阈值。"""
    f = _num(value)
    if f is None:
        return None
    return f * 100.0 if 0.0 <= f <= 1.0 else f


_SITUATION_ZH = {
    "LANDED": "已着陆",
    "SPLASHED": "已溅落在水面",
    "FLYING": "在大气层内飞行",
    "SUB_ORBITAL": "亚轨道（还会掉回来）",
    "ORBITING": "环绕轨道",
    "ESCAPING": "正在逃逸",
}


def _situation_text(value: Any) -> str:
    """把 situation 字段翻成人话。取不到就返回「未知」。"""
    raw = _as_text(value).strip()
    if not raw:
        return "未知"
    zh = _SITUATION_ZH.get(raw.upper())
    return f"{raw}（{zh}）" if zh else raw


def _vertical_hint(value: Any) -> str:
    """垂直速度的结论：上升 / 下降 / 平飞。"""
    f = _num(value)
    if f is None:
        return "未知"
    if f > 1.0:
        return "正在上升"
    if f < -1.0:
        return "正在下降"
    return "基本平飞"


def _apo_altitude(orbit: Dict[str, Any]) -> Optional[float]:
    """远点高度：优先用 apoapsis_altitude，缺了就用「远点距离 - 天体半径」补算。"""
    alt = _num(_as_dict(orbit).get("apoapsis_altitude"))
    if alt is not None:
        return alt
    dist = _num(_as_dict(orbit).get("apoapsis_distance"))
    radius = _num(_as_dict(orbit).get("body_radius"))
    if dist is not None and radius is not None:
        return dist - radius
    return None


def _peri_altitude(orbit: Dict[str, Any]) -> Optional[float]:
    """近点高度：优先用 periapsis_altitude，缺了就用「近点距离 - 天体半径」补算。"""
    alt = _num(_as_dict(orbit).get("periapsis_altitude"))
    if alt is not None:
        return alt
    dist = _num(_as_dict(orbit).get("periapsis_distance"))
    radius = _num(_as_dict(orbit).get("body_radius"))
    if dist is not None and radius is not None:
        return dist - radius
    return None


def _orbit_verdict(orbit: Dict[str, Any]) -> str:
    """入轨判断 —— 本插件最核心的一条结论。

    判定标准只有一个：**近点是否高过所在天体的大气层顶**。
    大气层高度每颗天体都不一样，必须用数据里的真实值，绝不凭常识猜。
    """
    orbit = _as_dict(orbit)
    peri = _peri_altitude(orbit)
    atm = _num(orbit.get("atmosphere_height"))

    if peri is None and atm is None:
        return "近点高度和大气层高度都没读到，无法判断算不算入轨 —— 请如实告诉用户这一项判断做不了。"
    if peri is not None and peri < 0:
        return f"近点在地表以下（{_fmt_dist(peri)}），飞船会直接撞上去。"
    if peri is not None and atm is not None:
        if peri < atm:
            return (
                f"近点（{_fmt_dist(peri)}）低于大气层顶（{_fmt_dist(atm)}），"
                "还在大气层里，会被拖下来，这样入不了轨。"
            )
        return f"近点（{_fmt_dist(peri)}）高过大气层顶（{_fmt_dist(atm)}），是稳定轨道。"
    if peri is not None:
        if _as_bool(orbit.get("is_stable_orbit")):
            return f"近点 {_fmt_dist(peri)}，模组判定已经是稳定轨道。"
        return f"近点 {_fmt_dist(peri)}，但没读到大气层高度，无法判断算不算稳定轨道。"
    return f"大气层顶 {_fmt_dist(atm)}，但近点高度没读到，无法判断算不算入轨。"


# ---------------------------------------------------------------------------
# 给模型的统一返回（连不上 / 不在飞行场景，都不算「工具出错」）
# ---------------------------------------------------------------------------


def _error_result(message: str) -> Dict[str, Any]:
    """连不上模组时的返回：is_error 保持 False，让模型照常说话，但必须如实说读不到。"""
    return {
        "output": {
            "ok": False,
            "connected": False,
            "in_flight": False,
            "message": (
                f"{message}\n"
                "请如实告诉用户「我现在读不到游戏数据」，不要凭空编造高度、速度、"
                "轨道或燃料。可以建议他确认游戏是否在运行、模组是否装好。"
            ),
        },
        "is_error": False,
    }


def _not_in_flight_result(data: Any) -> Dict[str, Any]:
    """模组通了、但游戏不在飞行场景时的返回。"""
    err = _as_text(_as_dict(data).get("error")).strip()
    if not err:
        err = "当前不在飞行场景（主菜单、设计器或加载中）。"
    return {
        "output": {
            "ok": False,
            "connected": True,
            "in_flight": False,
            "message": (
                f"{err}\n"
                "模组连接是通的，但游戏此刻不在飞行场景，所以没有任何遥测可读。"
                "请如实告诉用户现在读不到飞行数据，不要编造高度、速度或轨道；"
                "可以提醒他先进入飞行场景（发射或者在跑道上）再问你。"
            ),
        },
        "is_error": False,
    }


# ---------------------------------------------------------------------------
# 游戏知识（给从没玩过《朱诺新起源》的模型看）—— read_juno_docs 的内容
# ---------------------------------------------------------------------------

_DOC_GAME = """【《朱诺新起源 Juno: New Origins》是什么】
它是一款航天模拟沙盒游戏，由 Jundroo 开发，原名 SimpleRockets 2。
和《坎巴拉太空计划 KSP》《航天模拟器 SFS》属于同一类玩法：
在机库里用零件拼出火箭 / 飞机 / 飞船，再按接近真实的轨道力学把它送上天。

在游戏里能做的事：
- 造火箭、航天飞机、飞机、地面载具，以及多级可分离的探测器
- 发射、入轨、变轨、交会对接、登陆其他天体、返回
- 有大气阻力与气动加热，有天体引力球（SOI）切换
- 有 Vizzy 可视化编程，玩家可以给火箭写自动程序
- 有生涯模式和沙盒模式

【和 KSP / SFS 的关系（模型最容易搞混的地方）】
- 都叫航天模拟，但**数值完全不同**。游戏里的星系（Juno 星系）每颗天体
  都有自己的半径、重力、大气高度。
- ⚠️ **不要把 KSP 的数值套过来**：KSP 里 Kerbin 半径 600 km、大气 70 km、
  环绕速度约 2.3 km/s —— 这些数字在本游戏里完全不成立。
- ⚠️ **也不要把真实地球的数值套过来**（真实入轨约 7.9 km/s、卡门线 100 km）。
- 本游戏**不是真实尺度**，具体到某个天体是多少，必须**运行时从工具里读**。

【数值陷阱（这几条最容易让 AI 说错话）】
1. 入轨的唯一判定标准：**近点（periapsis）是否高过所在天体的大气层顶**。
   - 近点低于大气层顶 → 还在大气层里，会被拖下来，没入轨
   - 近点在地表以下（负数）→ 会撞上去
   - 近点高于大气层顶 → 稳定轨道
2. 每颗天体的大气高度**都不一样**，卫星和小行星可能压根没有大气。
   要判断某颗天体的大气高度，就调 list_juno_bodies 读 atmosphere_height，
   **绝对不要凭常识猜**（别默认 70 km，也别默认 100 km）。
3. 单位：高度 / 距离 / 半径是**米**，速度是 **m/s**，角度是**度**，
   时间和周期是**秒**，大气高度也是**米**。
   游戏界面上显示的是 km 或 Mm，1 Mm = 1000 km = 1,000,000 m。
   跟用户说话时可以换成他看得懂的单位，但换算别搞错。
   ⚠️ **气压 / 空气密度 / 气温已经有实机实测结论，但质量单位仍然未标定** ——
   细节见 read_juno_docs(topic="units") 那一节，务必照着念。
4. 字段可能是 null：刚起飞、正在分级、刚切场景时，轨道根数经常还没建立。
   取不到就说「这个数现在读不到」，**不要用 0 顶替**。
5. `/bodies` 里恒星（Juno）的 parent 是 **null**，那是「没有母天体」的意思，
   不要读成「未知」，也不要因此报错。"""

_DOC_TERMS = """【术语表】
- 远点 apoapsis（Ap）—— 轨道上离天体最远的那一点的高度。
- 近点 periapsis（Pe）—— 轨道上离天体最近的那一点的高度。
- 离心率 eccentricity —— 0 是正圆，0~1 是椭圆，≥1 是双曲线（逃逸）。
- 倾角 inclination —— 轨道面和赤道面的夹角，单位度。
- 周期 period —— 绕一圈要多久，单位秒。
- 亚轨道 sub-orbital —— 飞上去了，但近点还在地表或大气层里，一定会掉回来。
- 引力球 sphere of influence（SOI）—— 一个天体的引力主导范围，出界就换主天体。
- Delta-V（Δv）—— 速度增量，可以理解成「这次机动要花掉多少提速能力」，
  单位 m/s。航天里衡量「还能不能到某处」的硬通货就是它。
- Isp 比冲 —— 引擎效率，单位秒，越大越省燃料。
- 推重比 TWR —— 推力和重力之比；小于 1 就离不了地。
- 大气层顶 atmosphere height —— 超过它就没有明显的大气阻力。
- 马赫数 Mach —— 当前速度除以当地音速，≥1 就是超音速。
- 动压 —— 空气密度乘以速度平方的一半，越大气动载荷越狠。"""

_DOC_UNITS = """【数值单位】——实机实测结论，务必照此解读

以下结论来自模组在游戏里跑通后的真实 `/state` 数据（飞船停在 Droo 的 Juno Village Pad），
不是猜的，**不要按常识改判**：

1. 气压 air_pressure = **帕斯卡 Pa**，不是 kPa。
   实测海拔 2779 m 处 71905 Pa，与海平面约 101325 Pa 的指数衰减吻合。
   摘要里会同时给两种写法，例如「71.9 kPa（71905 Pa）」。
2. 空气密度 air_density = **kg/m³**。实测海拔 2779 m 处 0.865 kg/m³，量级合理。
3. 气温 temperature = **开尔文 K**，不是摄氏度。
   实测 279.65 K = 6.5 ℃。⚠️ **绝对不能把 279.6 念成 279.6 摄氏度**。
   摘要写成「6.5 ℃（279.6 K）」这种双标注形式。
4. 音速 speed_of_sound = **m/s**；马赫数 mach **无量纲**（当前速度除以当地音速）。
5. 电量 battery = **0~1 归一化**（实测 0.9997 表示约 100%），显示时 ×100。
6. 燃料余量 fuel_all_stages_percent = **0~1 归一化**（实测 0 表示 0%），同样 ×100 显示；
   摘要会保留原始值，方便核对。
7. 高度 / 离地高度 / 距离 / 半径 / 大气层高度 = **米**；
   各种速度（surface_speed / orbital_speed / vertical_speed / delta_v）= **m/s**；
   角度（pitch / heading / inclination / 攻角 / 侧滑）= **度**；周期与时间 = **秒**。
8. 质量 mass / current_mass / fuel_mass 是**游戏内部质量单位，未标定**。
   实测一艘只有 1 个零件的飞船 mass = 0.0418，和游戏界面上同量级的显示对不上
   （Juno 内部很可能按吨计），所以：
   - **不要做 kg→t 换算，也不要声称它是 kg 或 t**；
   - 摘要只输出原始数值并明确标注「游戏内部单位，未标定」，
     例如「质量 0.0418（游戏内部单位，未标定）」；
   - 模组新增的 current_mass 与 mass 都要按同样方式处理；
   - ⚠️ **质量单位未标定，以游戏界面为准** —— 不要拿它去算 Delta-V、推重比或载重。
"""


_DOC_SITUATION = """【situation 字段各值的含义】
- LANDED —— 已着陆，停在地面上
- SPLASHED —— 溅落在水面
- FLYING —— 在大气层内飞行（起飞、爬升、再入阶段常见）
- SUB_ORBITAL —— 亚轨道：已经飞出大气层，但近点还在地表或大气层里，会掉回来
- ORBITING —— 环绕轨道：近点已经高过大气层顶，稳定绕着天体转
- ESCAPING —— 逃逸：速度已经超过该天体的逃逸速度，正在飞出去

注意：SUB_ORBITAL 不等于入轨。看到它要明确告诉用户「还会掉回来」。"""

_DOC_TASKS = """【常见任务的正确顺序】

◆ 任务 A：用户问「我入轨了吗 / 能不能入轨」
1. 调 get_juno_orbit 读轨道根数，看近点高度（periapsis_altitude）
   和大气层高度（atmosphere_height）。
2. 比较两者：
   - 近点 < 大气层顶 → 还没入轨，会被大气拖下来
   - 近点 < 0 → 会撞上地表
   - 近点 ≥ 大气层顶 → 已经入轨
3. ⚠️ 大气层高度要从数据里读，不要猜。
4. 还没入轨时给建议：继续在近点附近点火，把近点抬到大气层顶以上（俗称「圆化」）。

◆ 任务 B：用户问「现在该不该点火 / 什么时候点火」
1. 调 get_juno_status 看高度、速度和 situation。
2. 调 get_juno_orbit 看还有多久到远点 / 近点（time_to_apoapsis / time_to_periapsis）。
3. 调 get_juno_flight_data 看是不是还在大气层里（空气密度和阻力还不小就说明在）。
4. ⚠️ 你**不能替他点火**。把「在远点点火抬升近点」这类建议说清楚，让他自己按。

◆ 任务 C：用户问「燃料够不够去某处」
1. 调 get_juno_resources 读 delta_v_stage、fuel_mass、推重比、可燃烧时间。
2. 调 get_juno_orbit 看当前轨道；必要时调 list_juno_bodies 看目标天体的数据。
3. 用工具返回的真实数字说话，别用记忆里的 KSP 数字。
   ⚠️ delta_v_stage 只是**当前这一级**还剩多少，不是全船总量，引用时要讲清楚。
4. 结论要说清楚：够 / 不够 / 数据不足无法判断。

◆ 任务 D：用户问「我锁定的是什么 / 离目标多远」（交会对接）
1. 调 get_juno_target 看 locked、name、distance、relative_speed。
2. 没锁定就告诉他先去游戏里选中一个目标。
3. 相对速度很大时提醒他先减速再靠近，不然会撞上。

◆ 任务 E：用户说「这是啥游戏 / 我该怎么玩」
1. 先用 read_juno_docs(topic="all") 读说明，再用 list_juno_bodies 看这个存档的星系。
2. 讲给用户听的时候用游戏里的真实数字，不要背通用参数。"""

_DOC_RULES = """【必须遵守的铁律】
0. **上下文里已经有「【朱诺自动播报】」时，先直接用那条里的数字回答。**
   那一行是插件在后台自己读来的真实遥测（高度/速度/轨道/燃料），
   和 get_juno_status 返回的是同一份数据 —— 为了同一批数字再去调工具，
   玩家只会觉得「她听到了却不理我，查到才回」。
   自动播报里确实没有的细节（逐项资源、大气细节、星系表）才调工具；
   要调、又可能要等一会儿时，**先说一句「我看一眼仪表」**再调，
   不要一声不吭地卡在那里。
1. 这个插件是**只读**的。你不能替他点火、分级、改变姿态、开关引擎、调油门。
   所有操作只能由玩家自己在游戏里做，你负责看数据和给建议。
   ⚠️ 不要说「我帮你点了」「我已经把引擎打开」这类话 —— 你做不到。
2. **只说你确实读到的数字**。没读到的高度、速度、燃料一律说「读不到 / 未知」，
   绝对不要凭印象编一个数，也不要用 0 顶替。
3. 连不上模组（游戏没开、模组没装）或不在飞行场景时，工具会返回 ok=false 并附说明。
   这时请**如实告诉用户现在读不到游戏数据**，不要继续分析，也不要假装读过。
4. 判断入轨、判断大气高度，一律用工具返回的真实数字，不要用 KSP 或地球的常识。
5. 单位和量级要交代清楚（说 km 还是 m），别把米说成千米。
6. 念数值时按 read_juno_docs(topic="units") 的实机实测结论：
   气压说 Pa / kPa，气温说「℃（K）」双标注（**绝不说成摄氏度**），
   电量与燃料是 0~1 归一化（×100 显示），
   质量是**未标定的游戏内部单位** —— 不要换算成 kg 或 t，也不要声称它是 kg / t。
7. 不确定的地方就说「我不确定」。宁可少说，不要说错。"""

_DOC_TOOLS = """【工具索引（什么时候用哪个）】
- get_juno_status —— 最常用。用户说「我现在飞到哪了」「看看我的火箭」「高度多少」
  「什么状态」时用。一次给出飞船、轨道概况、资源概况。
- get_juno_orbit —— 用户问「我入轨了吗」「轨道什么样」「远点近点多少」
  「什么时候到远点」时用。里面有入轨判断和到远点/近点的剩余时间。
- get_juno_resources —— 用户问「油还够吗」「还剩多少电」「Delta-V 还有多少」
  「推重比多少」「还能烧多久」时用。
- get_juno_flight_data —— 用户问「马赫数多少」「空气密度」「有没有超音速」
  「阻力多大」「重力多少」「攻角多少」时用，大气层内最有价值。
- get_juno_target —— 用户问「我锁定的是什么」「离目标多远」「相对速度多少」时用，
  对接和交会最有用。
- list_juno_bodies —— 用户问「星系里有哪些星球」「哪颗星球有大气」「大气多高」
  「引力球多大」时用。**判断入轨前也可以先用它确认大气高度。**
- list_juno_vessels —— 用户问「场景里有哪些飞船」「那个目标飞行器在哪」时用。
- read_juno_docs —— 就是这份说明书。不了解游戏或不确定用哪个工具时先读它。
  可以用 topic 分段读：all / game / terms / units / situation / tasks / rules / tools。
  ⚠️ 拿不准某个数是什么单位时，先读 topic="units" 那一节（实机实测结论）。"""

_DOC_SECTIONS: Dict[str, str] = {
    "game": _DOC_GAME,
    "terms": _DOC_TERMS,
    "units": _DOC_UNITS,
    "situation": _DOC_SITUATION,
    "tasks": _DOC_TASKS,
    "rules": _DOC_RULES,
    "tools": _DOC_TOOLS,
}
_DOC_ORDER = ["game", "terms", "units", "situation", "tasks", "rules", "tools"]
_DOC_ALIASES = {
    "overview": "game",
    "traps": "game",
    "intro": "game",
    "term": "terms",
    "glossary": "terms",
    "unit": "units",
    "measure": "units",
    "task": "tasks",
    "order": "tasks",
    "rule": "rules",
    "tool": "tools",
}


def _juno_docs(topic: Any = "all") -> str:
    """按 topic 取说明书。topic 不认识时返回全部并提示可选值。"""
    key = _as_text(topic).strip().lower()
    if key in ("", "all", "*"):
        return "\n\n".join(_DOC_SECTIONS[name] for name in _DOC_ORDER)
    if key in _DOC_SECTIONS:
        return _DOC_SECTIONS[key]
    if key in _DOC_ALIASES:
        return _DOC_SECTIONS[_DOC_ALIASES[key]]
    head = f"没有「{key}」这一段。可选：all / " + " / ".join(_DOC_ORDER) + "。下面是全部内容。\n\n"
    return head + "\n\n".join(_DOC_SECTIONS[name] for name in _DOC_ORDER)


# ---------------------------------------------------------------------------
# 插件主体
# ---------------------------------------------------------------------------


@neko_plugin
class JunoBridgePlugin(NekoPluginBase):
    """朱诺新起源桥接插件（只读）。"""

    def __init__(self, ctx: Any):
        super().__init__(ctx)
        self.file_logger = self.enable_file_logging(log_level="INFO")
        self.logger = self.file_logger

        self._cfg: Dict[str, Any] = {}
        self._bridge_url: str = DEFAULT_BRIDGE_URL
        self._timeout: float = 12.0
        self._client: Optional[httpx.AsyncClient] = None
        self._client_loop: Any = None

        # 自动播报（游戏在跑就自己在后台读遥测，推进角色上下文）
        self._auto_brief_enabled: bool = True
        self._auto_brief_interval: float = 20.0
        self._auto_brief_task: Any = None
        self._auto_brief_stop: Any = None
        self._auto_brief_last_text: str = ""
        self._auto_brief_last_ok: Optional[bool] = None

    # -- 基础设施 ---------------------------------------------------------

    def _get_client(self) -> httpx.AsyncClient:
        """按事件循环缓存客户端（宿主会在不同 asyncio.run 中调用）。"""
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
                "读取游戏数据超时。游戏在加载存档、切场景或高倍时间加速时可能会卡一下，"
                "可以稍后重试。"
            ) from exc
        except httpx.HTTPError as exc:
            raise SdkError(
                f"连不上朱诺桥接服务（{self._bridge_url}）。请确认："
                "1) 游戏正在运行并已进入存档；"
                "2) 已把 N.E.K.O-Juno-bridge 装进 BepInEx/plugins；"
                "3) 装好后重启过游戏。"
            ) from exc

        if response.status_code >= 400:
            raise SdkError(f"朱诺桥接服务返回状态 {response.status_code}。")
        try:
            return response.json()
        except ValueError as exc:
            raise SdkError("朱诺桥接服务返回的内容不是合法 JSON。") from exc

    # -- 自动播报 ---------------------------------------------------------

    async def _auto_brief_once(self) -> None:
        """读一次遥测，把最新情况推进角色上下文（不打断对话）。

        用 `ai_behavior="read"`：消息进 LLM 上下文但不会立刻触发一轮对话，
        主人下次开口时角色已经知道火箭什么状况，不用再主动去调工具。
        """
        try:
            state = await self._get_json("/state")
            connected = True
        except SdkError:
            state = {}
            connected = False
        except Exception as exc:  # pragma: no cover
            self.logger.debug("自动播报读取失败: {}", exc)
            return

        if connected:
            text = _auto_brief_line(state)
            if not text:
                # 不在飞行场景（主菜单/设计器/加载中），这条不用发。
                self._auto_brief_last_ok = True
                return
        else:
            if not self._auto_brief_last_ok:
                # 从来没连上过就别灌噪音了，主人自己会问。
                return
            text = f"{AUTO_BRIEF_PREFIX}游戏已经退出或断开了，读不到新的遥测数据。"

        if connected == self._auto_brief_last_ok and text == self._auto_brief_last_text:
            return

        try:
            result = self.push_message(
                visibility=[],
                ai_behavior="read",
                coalesce_key=AUTO_BRIEF_COALESCE_KEY,
                source="juno_auto_brief",
                parts=[{"type": "text", "text": text}],
            )
            if not _as_dict(result).get("submitted"):
                self.logger.debug("自动播报未进队: {}", result)
        except Exception as exc:  # pragma: no cover
            self.logger.debug("自动播报推送失败: {}", exc)
            return

        self._auto_brief_last_ok = connected
        self._auto_brief_last_text = text

    def _start_auto_brief(self) -> None:
        """起后台轮询任务（幂等）。"""
        # 用 getattr：测试里常用 __new__ 造实例，没走 __init__。
        task = getattr(self, "_auto_brief_task", None)
        if task is not None and not task.done():
            return
        try:
            self._auto_brief_stop = asyncio.Event()
            self._auto_brief_task = asyncio.create_task(self._auto_brief_loop())
        except RuntimeError:  # pragma: no cover - 没有事件循环（单测/同步启动）
            self._auto_brief_stop = None
            self._auto_brief_task = None

    async def _stop_auto_brief(self) -> None:
        """停掉后台轮询任务（幂等）。"""
        # 用 getattr：测试里常用 __new__ 造实例，没走 __init__。
        task = getattr(self, "_auto_brief_task", None)
        stop = getattr(self, "_auto_brief_stop", None)
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
                self.logger.debug("自动播报任务收尾出错: {}", exc)

    async def _auto_brief_loop(self) -> None:
        """按间隔循环播报，直到被叫停。"""
        stop = self._auto_brief_stop
        while stop is not None and not stop.is_set():
            try:
                await self._auto_brief_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # pragma: no cover
                self.logger.debug("自动播报循环出错: {}", exc)
            try:
                await asyncio.wait_for(stop.wait(), timeout=self._auto_brief_interval)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                raise
            return

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

        section = _as_dict(cfg.get("juno_bridge"))
        self._bridge_url = _as_text(section.get("bridge_url")) or DEFAULT_BRIDGE_URL

        try:
            self._timeout = float(section.get("timeout_seconds", 12))
        except (TypeError, ValueError):
            self._timeout = 12.0
        self._timeout = min(max(self._timeout, 3.0), 60.0)

        self._auto_brief_enabled = _as_bool(section.get("auto_brief_enabled"), True)
        brief_interval = _num(section.get("auto_brief_interval_seconds"))
        self._auto_brief_interval = (
            min(max(brief_interval, 5.0), 300.0) if brief_interval is not None else 20.0
        )

        self.logger.info(
            "JunoBridge 已就绪（只读模式），桥接地址={}，自动播报={}",
            self._bridge_url,
            f"开/{self._auto_brief_interval:g}s" if self._auto_brief_enabled else "关",
        )
        if self._auto_brief_enabled:
            self._start_auto_brief()
        return Ok({
            "status": "ready",
            "mode": "read-only",
            "bridge_url": self._bridge_url,
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

    @plugin_entry(
        id="juno_status",
        name="朱诺状态",
        description="读取《朱诺新起源》当前飞行状态：飞船、轨道概况、资源与目标。",
        timeout=30,
        llm_result_fields=["summary", "connected", "state"],
    )
    async def juno_status(self, **_):
        """读取游戏状态。"""
        try:
            state = await self._get_json("/state")
        except SdkError as exc:
            return Ok({"connected": False, "in_flight": False, "summary": str(exc)})
        except Exception as exc:  # pragma: no cover
            self.logger.warning("读取状态失败: {}", exc)
            return Ok({"connected": False, "in_flight": False, "summary": f"读取失败：{exc}"})

        if not _as_bool(_as_dict(state).get("ok")):
            return Ok({
                "connected": True,
                "in_flight": False,
                "summary": _as_text(_as_dict(state).get("error"))
                or "当前不在飞行场景（主菜单、设计器或加载中）。",
            })

        return Ok({
            "connected": True,
            "in_flight": True,
            "summary": _summarize_state(state),
            "state": state,
        })

    @plugin_entry(
        id="juno_orbit",
        name="朱诺轨道",
        description="读取《朱诺新起源》当前轨道的根数：远点、近点、离心率、倾角、周期。",
        timeout=30,
        llm_result_fields=["summary", "connected", "orbit"],
    )
    async def juno_orbit(self, **_):
        """读取轨道。"""
        try:
            orbit = await self._get_json("/orbit")
        except SdkError as exc:
            return Ok({"connected": False, "in_flight": False, "summary": str(exc)})
        except Exception as exc:  # pragma: no cover
            return Ok({"connected": False, "in_flight": False, "summary": f"读取失败：{exc}"})

        if not _as_bool(_as_dict(orbit).get("ok")):
            return Ok({
                "connected": True,
                "in_flight": False,
                "summary": _as_text(_as_dict(orbit).get("error"))
                or "当前不在飞行场景（主菜单、设计器或加载中）。",
            })

        return Ok({
            "connected": True,
            "in_flight": True,
            "summary": _summarize_orbit(orbit),
            "orbit": orbit,
        })

    @plugin_entry(
        id="juno_resources",
        name="朱诺资源",
        description="读取《朱诺新起源》飞船上的燃料、电量、Delta-V、推重比与可燃烧时间。",
        timeout=30,
        llm_result_fields=["summary", "connected", "resources"],
    )
    async def juno_resources(self, **_):
        """读取资源。"""
        try:
            data = await self._get_json("/resources")
        except SdkError as exc:
            return Ok({"connected": False, "in_flight": False, "summary": str(exc)})
        except Exception as exc:  # pragma: no cover
            return Ok({"connected": False, "in_flight": False, "summary": f"读取失败：{exc}"})

        if not _as_bool(_as_dict(data).get("ok")):
            return Ok({
                "connected": True,
                "in_flight": False,
                "summary": _as_text(_as_dict(data).get("error"))
                or "当前不在飞行场景（主菜单、设计器或加载中）。",
            })

        return Ok({
            "connected": True,
            "in_flight": True,
            "summary": _summarize_resources(data),
            "resources": data,
        })

    # -- LLM 工具 ---------------------------------------------------------

    @llm_tool(
        name="get_juno_status",
        description=(
            "读取《朱诺新起源 Juno: New Origins》当前飞行状态。"
            "用户说「我现在飞到哪了」「看看我的火箭」「高度多少」「速度多少」「什么情况」时调用。"
            "返回飞船名称、飞行阶段、所在地天体、高度、速度、姿态、质量、级数和乘员，"
            "并附带轨道概况与燃料概况。这是最常用的一个接口。"
            "只读，不会改变游戏里的任何东西。"
        ),
        parameters={"type": "object", "properties": {}},
        timeout=30,
    )
    async def get_juno_status(self, **kwargs: Any) -> Dict[str, Any]:
        """LLM 工具：读取飞行状态。"""
        try:
            state = await self._get_json("/state")
        except SdkError as exc:
            return _error_result(str(exc))
        except Exception as exc:  # pragma: no cover
            self.logger.warning("读取状态失败: {}", exc)
            return _error_result(f"读取游戏状态失败：{exc}")

        state = _as_dict(state)
        if not _as_bool(state.get("ok")):
            return _not_in_flight_result(state)

        scene = _as_dict(state.get("scene"))
        return {
            "output": {
                "ok": True,
                "connected": True,
                "in_flight": True,
                "summary": _summarize_state(state),
                "scene": scene,
                "vessel": _as_dict(state.get("vessel")),
                "orbit": _as_dict(state.get("orbit")),
                "flight": _as_dict(state.get("flight")),
                "resources": _as_dict(state.get("resources")),
                "target": _as_dict(state.get("target")),
            },
            "is_error": False,
        }

    @llm_tool(
        name="get_juno_orbit",
        description=(
            "读取《朱诺新起源》当前轨道的详细根数，并给出入轨判断。"
            "用户问「我入轨了吗」「轨道什么样」「远点近点多少」「什么时候到远点」时调用。"
            "返回值里的 verdict 直接说明近点够不够高、算不算稳定轨道；"
            "大气层高度 atmosphere_height 也一并给出，不要凭常识猜这个数。"
            "只读。"
        ),
        parameters={"type": "object", "properties": {}},
        timeout=30,
    )
    async def get_juno_orbit(self, **kwargs: Any) -> Dict[str, Any]:
        """LLM 工具：读取轨道根数。"""
        try:
            orbit = await self._get_json("/orbit")
        except SdkError as exc:
            return _error_result(str(exc))
        except Exception as exc:  # pragma: no cover
            self.logger.warning("读取轨道失败: {}", exc)
            return _error_result(f"读取轨道失败：{exc}")

        orbit = _as_dict(orbit)
        if not _as_bool(orbit.get("ok")):
            return _not_in_flight_result(orbit)

        return {
            "output": {
                "ok": True,
                "connected": True,
                "in_flight": True,
                "summary": _summarize_orbit(orbit),
                "verdict": _orbit_verdict(orbit),
                "orbit": orbit,
            },
            "is_error": False,
        }

    @llm_tool(
        name="get_juno_resources",
        description=(
            "读取《朱诺新起源》飞船上的资源余量：燃料质量、本级燃料、单组元推进剂、"
            "电量、本级 Delta-V、比冲、推重比和按当前油耗还能烧多久。"
            "用户问「油还够吗」「还剩多少电」「Delta-V 还有多少」「能不能飞到月球」时调用。"
            "只读。"
        ),
        parameters={"type": "object", "properties": {}},
        timeout=30,
    )
    async def get_juno_resources(self, **kwargs: Any) -> Dict[str, Any]:
        """LLM 工具：读取资源余量。"""
        try:
            data = await self._get_json("/resources")
        except SdkError as exc:
            return _error_result(str(exc))
        except Exception as exc:  # pragma: no cover
            self.logger.warning("读取资源失败: {}", exc)
            return _error_result(f"读取资源失败：{exc}")

        data = _as_dict(data)
        if not _as_bool(data.get("ok")):
            return _not_in_flight_result(data)

        return {
            "output": {
                "ok": True,
                "connected": True,
                "in_flight": True,
                "summary": _summarize_resources(data),
                "resources": data,
            },
            "is_error": False,
        }

    @llm_tool(
        name="get_juno_flight_data",
        description=(
            "读取《朱诺新起源》飞船在大气层内的飞行数据：马赫数、空气密度、气压、气温、"
            "当地音速、重力、阻力加速度、合加速度、攻角、侧滑角、推力和燃料质量。"
            "用户问「马赫数多少」「超音速了吗」「空气多稀薄」「阻力大不大」「重力多少」时调用。"
            "飞船在大气层内时最有用；在真空里这些气动数据会接近 0。只读。"
        ),
        parameters={"type": "object", "properties": {}},
        timeout=30,
    )
    async def get_juno_flight_data(self, **kwargs: Any) -> Dict[str, Any]:
        """LLM 工具：读取大气与受力数据。"""
        try:
            flight = await self._get_json("/flight")
        except SdkError as exc:
            return _error_result(str(exc))
        except Exception as exc:  # pragma: no cover
            self.logger.warning("读取飞行数据失败: {}", exc)
            return _error_result(f"读取飞行数据失败：{exc}")

        flight = _as_dict(flight)
        if not _as_bool(flight.get("ok")):
            return _not_in_flight_result(flight)

        return {
            "output": {
                "ok": True,
                "connected": True,
                "in_flight": True,
                "summary": _summarize_flight(flight),
                "note": (
                    "单位（实机实测）：air_pressure 是帕斯卡 Pa（不是 kPa）；"
                    "air_density 是 kg/m³；temperature 是开尔文 K（279.65 K = 6.5 ℃，"
                    "**不要说成摄氏度**）；speed_of_sound 是 m/s；mach 无量纲。"
                ),
                "flight": flight,
            },
            "is_error": False,
        }

    @llm_tool(
        name="get_juno_target",
        description=(
            "读取《朱诺新起源》当前锁定的目标信息：目标名称、类型（飞船/天体）、距离、"
            "相对速度，以及目标自己的轨道远点/近点。"
            "用户问「我锁定的是什么」「离目标多远」「相对速度多少」「能不能对接」时调用，"
            "交会和对接时最有用。没锁定目标也会如实说明。只读。"
        ),
        parameters={"type": "object", "properties": {}},
        timeout=30,
    )
    async def get_juno_target(self, **kwargs: Any) -> Dict[str, Any]:
        """LLM 工具：读取锁定目标。"""
        try:
            data = await self._get_json("/target")
        except SdkError as exc:
            return _error_result(str(exc))
        except Exception as exc:  # pragma: no cover
            self.logger.warning("读取目标失败: {}", exc)
            return _error_result(f"读取目标失败：{exc}")

        data = _as_dict(data)
        if not _as_bool(data.get("ok")):
            return _not_in_flight_result(data)

        return {
            "output": {
                "ok": True,
                "connected": True,
                "in_flight": True,
                "locked": _as_bool(data.get("locked")),
                "summary": _summarize_target(data),
                "target": data,
            },
            "is_error": False,
        }

    @llm_tool(
        name="list_juno_bodies",
        description=(
            "列出《朱诺新起源》当前星系里的所有天体及其参数：半径、质量、表面重力、"
            "逃逸速度、是否有大气、大气高度、是否有水、母天体、引力球范围。"
            "用户问「星系里有哪些星球」「哪颗星球有大气」「气层多高」时调用。"
            "**判断入轨、判断某颗天体的大气高度时也用这个，不要凭常识猜。** 只读。"
        ),
        parameters={"type": "object", "properties": {}},
        timeout=30,
    )
    async def list_juno_bodies(self, **kwargs: Any) -> Dict[str, Any]:
        """LLM 工具：列出天体。"""
        try:
            data = await self._get_json("/bodies")
        except SdkError as exc:
            return _error_result(str(exc))
        except Exception as exc:  # pragma: no cover
            self.logger.warning("读取天体失败: {}", exc)
            return _error_result(f"读取天体失败：{exc}")

        # 模组实测把数组直接放在顶层；也可能包成 {"ok":true,"bodies":[...]}。
        # 先留一份原始载荷，否则顶层数组被 _as_dict 吃成 {} 就再也读不出来了。
        payload = data
        data = _as_dict(data)
        if not _as_bool(data.get("ok", True)):
            return _not_in_flight_result(data)

        bodies = _extract_list(payload, "bodies")
        if not bodies:
            return {
                "output": {
                    "ok": True,
                    "connected": True,
                    "count": 0,
                    "message": _as_text(data.get("error"))
                    or _as_text(data.get("note"))
                    or "没有读到天体数据。",
                },
                "is_error": False,
            }

        return {
            "output": {
                "ok": True,
                "connected": True,
                "count": len(bodies),
                "summary": _summarize_bodies(bodies),
                "bodies": bodies,
            },
            "is_error": False,
        }

    @llm_tool(
        name="list_juno_vessels",
        description=(
            "列出《朱诺新起源》当前场景里的所有飞船及其状态：名称、节点 id、是不是玩家自己的船、"
            "高度、质量、零件数、乘员数。"
            "用户问「场景里有哪些飞船」「那个空间站在哪」「有几艘船」时调用。只读。"
        ),
        parameters={"type": "object", "properties": {}},
        timeout=30,
    )
    async def list_juno_vessels(self, **kwargs: Any) -> Dict[str, Any]:
        """LLM 工具：列出飞船。"""
        try:
            data = await self._get_json("/vessels")
        except SdkError as exc:
            return _error_result(str(exc))
        except Exception as exc:  # pragma: no cover
            self.logger.warning("读取飞船列表失败: {}", exc)
            return _error_result(f"读取飞船列表失败：{exc}")

        # 同上：/vessels 实测是顶层数组，也要接受 {"ok":true,"vessels":[...]} 的包装。
        payload = data
        data = _as_dict(data)
        if not _as_bool(data.get("ok", True)):
            return _not_in_flight_result(data)

        vessels = _extract_list(payload, "vessels")
        if not vessels:
            return {
                "output": {
                    "ok": True,
                    "connected": True,
                    "count": 0,
                    "message": _as_text(data.get("error"))
                    or _as_text(data.get("note"))
                    or "当前场景里没有读到飞船。",
                },
                "is_error": False,
            }

        return {
            "output": {
                "ok": True,
                "connected": True,
                "count": len(vessels),
                "summary": _summarize_vessels(vessels),
                "vessels": vessels,
            },
            "is_error": False,
        }

    @llm_tool(
        name="read_juno_docs",
        description=(
            "【必看】《朱诺新起源》这款游戏与本插件的完整中文说明书。"
            "**如果你不了解这款游戏、不清楚它的术语（远点/近点/离心率/入轨判定/Delta-V），"
            "不知道这个游戏和《坎巴拉太空计划》的数值差别，或者不确定该用哪个工具、"
            "该按什么顺序做 —— 先调这个。**"
            "用户第一次找你玩这个游戏时，建议先读一遍。"
            "内容较多，可以用 topic 分段读。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "topic": {
                    "type": "string",
                    "description": (
                        "要看哪一段：all=全部（默认）、game=游戏介绍与数值陷阱、"
                        "terms=术语表、units=数值单位（实机实测：气压 Pa、气温 K、"
                        "电量与燃料 0~1、质量未标定）、"
                        "situation=飞行阶段含义、tasks=常见任务的正确顺序、"
                        "rules=必须遵守的铁律、tools=工具索引。"
                    ),
                },
            },
        },
        timeout=20,
    )
    async def read_juno_docs(self, **kwargs: Any) -> Dict[str, Any]:
        """LLM 工具：读说明书。"""
        topic = _as_text(kwargs.get("topic")).strip() or "all"
        return {
            "output": {
                "ok": True,
                "topic": topic,
                "docs": _juno_docs(topic),
                "message": (
                    "以上是游戏与本插件的说明。"
                    "想了解玩家现在什么情况，可以调 get_juno_status 或 get_juno_orbit。"
                    "再次强调：本插件是只读的，你不能替他操作火箭。"
                ),
            },
            "is_error": False,
        }


# ---------------------------------------------------------------------------
# 摘要生成（纯函数，便于测试）
# ---------------------------------------------------------------------------


def _summarize_state(state: Dict[str, Any]) -> str:
    """把 /state 的输出压成一段能直接念出来的中文。"""
    state = _as_dict(state)
    scene = _as_dict(state.get("scene"))
    vessel = _as_dict(state.get("vessel"))
    flight = _as_dict(state.get("flight"))
    orbit = _as_dict(state.get("orbit"))
    resources = _as_dict(state.get("resources"))

    name = _as_text(vessel.get("name")) or _as_text(scene.get("craft_name")) or "当前飞船"
    body = _as_text(vessel.get("body")) or _as_text(scene.get("body")) or "未知天体"

    parts = [
        f"「{name}」在 {body}",
        f"状态 {_situation_text(vessel.get('situation'))}",
        f"海拔 {_fmt_dist(vessel.get('altitude'))}",
    ]

    agl = _num(vessel.get("altitude_agl"))
    if agl is not None:
        parts.append(f"离地高度 {_fmt_dist(agl)}")

    parts.append(f"地表速度 {_fmt(flight.get('surface_speed'), ' m/s')}")
    parts.append(f"轨道速度 {_fmt(flight.get('orbital_speed'), ' m/s')}")

    vertical = _num(flight.get("vertical_speed"))
    if vertical is not None:
        parts.append(f"垂直速度 {_fmt(vertical, ' m/s')}（{_vertical_hint(vertical)}）")

    pitch = _num(vessel.get("pitch"))
    if pitch is not None:
        parts.append(f"俯仰 {pitch:.0f}°")

    heading = _num(vessel.get("heading"))
    if heading is not None:
        parts.append(f"航向 {heading:.0f}°")

    mass = _num(vessel.get("mass"))
    if mass is not None:
        parts.append(f"质量 {_fmt_mass(mass)}")

    current_mass = _num(vessel.get("current_mass"))
    if current_mass is not None:
        parts.append(f"当前质量 {_fmt_mass(current_mass)}")

    stage = _num(vessel.get("current_stage"))
    stage_count = _num(vessel.get("stage_count"))
    if stage is not None and stage_count is not None:
        parts.append(f"第 {int(stage)}/{int(stage_count)} 级")
    elif stage is not None:
        parts.append(f"第 {int(stage)} 级")

    crew = _num(vessel.get("crew_count"))
    if crew is not None:
        parts.append(f"船上 {int(crew)} 人")

    if _as_bool(vessel.get("grounded")):
        parts.append("已接地")
    elif _as_bool(vessel.get("in_water")):
        parts.append("在水里")

    text = "，".join(parts) + "。"

    if orbit:
        text += (
            f" 轨道：远点 {_fmt_dist(_apo_altitude(orbit))}，"
            f"近点 {_fmt_dist(_peri_altitude(orbit))}。"
        )
        text += " " + _orbit_verdict(orbit)

    if resources:
        pct = _percent_of_100(resources.get("fuel_all_stages_percent"))
        if pct is not None and pct <= 10.0:
            text += f" 注意：全船燃料只剩 {_fmt_percent(resources.get('fuel_all_stages_percent'))}，快见底了。"

    game_time = _num(scene.get("game_time"))
    if game_time is not None:
        text += f" 当前游戏时间 {_fmt_time(game_time)}。"

    warp = _num(scene.get("warp_multiplier"))
    if warp is not None and warp > 1.0:
        text += f" 时间加速 {warp:g} 倍中。"
    if _as_bool(scene.get("paused")):
        text += " 游戏现在是暂停状态。"

    return text


def _auto_brief_line(state: Dict[str, Any]) -> str:
    """把 /state 压成一行自动播报。

    模组通了但游戏不在飞行场景时返回空串，表示「这条不用发」—— 主菜单、
    设计器、加载中反复播报「读不到飞行数据」只是浪费上下文。
    刻意压短：这一段会一直待在上下文里，跟着每一轮对话走。
    但玩家最常追问的几项（燃料余量、Delta-V、推重比、电池）能塞就塞，
    这样「油还够吗」「还剩多少 Delta-V」不必再走一次工具往返。
    """
    if not _as_bool(_as_dict(state).get("ok")):
        return ""
    text = AUTO_BRIEF_PREFIX + _summarize_state(state)

    resources = _as_dict(_as_dict(state).get("resources"))
    extra: List[str] = []
    if resources:
        dv = _num(resources.get("delta_v_stage"))
        if dv is not None and dv > 0:
            extra.append(f"本级 Delta-V {dv:,.0f}")
        twr = _num(resources.get("thrust_to_weight"))
        if twr is not None and twr > 0:
            extra.append(f"推重比 {twr:.2f}")
        # 燃料 ≤10% 时 _summarize_state 已经专门警告过，这里不重复
        fuel = _percent_of_100(resources.get("fuel_all_stages_percent"))
        if fuel is not None and fuel > 10.0:
            extra.append(f"全船燃料 {fuel:.0f}%")
        battery = _percent_of_100(resources.get("battery"))
        if battery is not None:
            extra.append(f"电量 {battery:.0f}%")

    if extra:
        text += "（" + "，".join(extra) + "）"
    return text


def _summarize_orbit(orbit: Dict[str, Any]) -> str:
    """把 /orbit 的输出压成一段能直接念出来的中文。"""
    orbit = _as_dict(orbit)
    if not orbit:
        return "没有读到轨道数据（飞船可能刚起飞、还没入轨，或者不在飞行场景）。"

    body = _as_text(orbit.get("body")) or "未知天体"
    text = (
        f"绕 {body} 的轨道：远点 {_fmt_dist(_apo_altitude(orbit))}，"
        f"近点 {_fmt_dist(_peri_altitude(orbit))}"
    )

    ecc = _num(orbit.get("eccentricity"))
    if ecc is not None:
        text += f"，离心率 {ecc:.4f}"
    inc = _num(orbit.get("inclination"))
    if inc is not None:
        text += f"，倾角 {inc:.2f}°"
    text += f"，周期 {_fmt_time(orbit.get('period'))}。"

    text += " " + _orbit_verdict(orbit)

    text += (
        f" 距离远点还有 {_fmt_time(orbit.get('time_to_apoapsis'))}，"
        f"距离近点还有 {_fmt_time(orbit.get('time_to_periapsis'))}。"
    )

    orbit_type = _as_text(orbit.get("orbit_type"))
    if orbit_type:
        text += f" 轨道类型 {orbit_type}。"
    if not _as_bool(orbit.get("is_valid", True)):
        text += " （模组标记 is_valid=false：这段轨道数据可能还没建立或不完整，谨慎解释。）"
    return text


def _summarize_flight(flight: Dict[str, Any]) -> str:
    """把 /flight 的输出压成一段能直接念出来的中文。"""
    flight = _as_dict(flight)
    if not flight:
        return "没有读到大气与受力数据（飞船可能刚起飞、还没入轨，或者不在飞行场景）。"

    vertical = _num(flight.get("vertical_speed"))
    parts = [
        f"对地高度 {_fmt_dist(flight.get('altitude_agl'))}",
        f"地表速度 {_fmt(flight.get('surface_speed'), ' m/s')}",
        f"轨道速度 {_fmt(flight.get('orbital_speed'), ' m/s')}",
        f"垂直速度 {_fmt(vertical, ' m/s')}（{_vertical_hint(vertical)}）",
        f"横向速度 {_fmt(flight.get('lateral_speed'), ' m/s')}",
        f"马赫数 {_fmt(flight.get('mach'), '', 2)}",
        f"空气密度 {_fmt_density(flight.get('air_density'))}",
        f"气压 {_fmt_pressure(flight.get('air_pressure'))}",
        f"气温 {_fmt_kelvin(flight.get('temperature'))}",
        f"当地音速 {_fmt(flight.get('speed_of_sound'), ' m/s')}",
        f"当地重力 {_fmt(flight.get('gravity'), ' m/s²', 2)}",
        f"阻力加速度 {_fmt(flight.get('drag_acceleration'), ' m/s²', 2)}",
        f"合加速度 {_fmt(flight.get('acceleration'), ' m/s²', 2)}",
        f"推力 {_fmt(flight.get('thrust'), ' N', 0)}（最大 {_fmt(flight.get('max_thrust'), ' N', 0)}）",
        f"燃料质量 {_fmt_mass(flight.get('fuel_mass'))}",
    ]

    text = "大气与受力：" + "，".join(parts) + "。"

    mach = _num(flight.get("mach"))
    density = _num(flight.get("air_density"))
    drag = _num(flight.get("drag_acceleration"))
    if mach is not None and mach >= 1.0:
        text += f" 马赫数已经到 {mach:.2f}，是超音速。"
    if density is not None and density <= 0.0:
        text += " 空气密度基本为 0，是在真空里飞，几乎没有气动阻力。"
    elif drag is not None and abs(drag) >= 1.0:
        text += f" 阻力加速度 {abs(drag):.2f} m/s²，大气在明显拖慢飞船。"
    return text


def _summarize_resources(resources: Dict[str, Any]) -> str:
    """把 /resources 的输出压成一段能直接念出来的中文，并给出够不够用的结论。"""
    resources = _as_dict(resources)
    if not resources:
        return "没有读到燃料与电量数据（飞船可能不在飞行场景）。"

    twr = _num(resources.get("thrust_to_weight"))
    burn = _num(resources.get("burn_time_remaining"))
    parts = [
        f"全船燃料 {_fmt_mass(resources.get('fuel_mass'))}",
        f"本级燃料 {_fmt_mass(resources.get('fuel_in_stage'))}",
        f"全船燃料余量 {_fmt_percent(resources.get('fuel_all_stages_percent'))}",
        f"单组元推进剂 {_fmt(resources.get('monopropellant'), '', 2)}",
        f"电量 {_fmt_percent(resources.get('battery'))}",
        f"本级 Delta-V {_fmt(resources.get('delta_v_stage'), ' m/s', 0)}",
        f"比冲 {_fmt(resources.get('isp'), ' s', 0)}",
        f"推重比 {_fmt(twr, '', 2)}",
        f"按当前油耗还能烧 {_fmt_time(burn)}",
    ]

    text = "资源：" + "，".join(parts) + "。"

    if twr is not None:
        if twr < 1.0:
            text += f" 推重比只有 {twr:.2f}，小于 1，现在这台引擎推不动这艘飞船，起不来也落不下去。"
        elif twr < 1.2:
            text += f" 推重比 {twr:.2f}，刚过 1，起飞会很吃力。"
        else:
            text += f" 推重比 {twr:.2f}，离地够用。"

    pct = _percent_of_100(resources.get("fuel_all_stages_percent"))
    if pct is not None:
        if pct <= 10.0:
            text += f" 燃料只剩 {pct:.0f}%，快见底了，远距离变轨要慎重。"
        elif pct <= 30.0:
            text += f" 燃料剩 {pct:.0f}%，还够做几次机动，但别乱花。"

    if burn is not None and burn <= 30.0:
        text += f" 按当前油耗只能再烧 {_fmt_time(burn)}，很快就没了。"
    return text


def _summarize_target(target: Dict[str, Any]) -> str:
    """把 /target 的输出压成一段能直接念出来的中文。"""
    target = _as_dict(target)
    if not _as_bool(target.get("locked")):
        return "当前没有锁定任何目标。想交会或对接的话，先在游戏里选中一个目标再问我。"

    name = _as_text(target.get("name")) or "未命名目标"
    kind = _as_text(target.get("kind")).strip().lower()
    dist = _fmt_dist(target.get("distance"))
    rel = _num(target.get("relative_speed"))

    if kind == "vessel":
        text = f"已锁定飞船「{name}」，距离 {dist}，相对速度 {_fmt(rel, ' m/s')}。"
    elif kind == "body":
        text = f"已锁定天体「{name}」，距离 {dist}，相对速度 {_fmt(rel, ' m/s')}。"
    else:
        text = f"已锁定目标「{name}」，距离 {dist}，相对速度 {_fmt(rel, ' m/s')}。"

    parent = _as_text(target.get("parent"))
    if parent:
        text += f" 它的母天体是 {parent}。"

    if _as_bool(target.get("has_orbit")):
        text += (
            f" 目标轨道：远点 {_fmt_dist(target.get('apoapsis_distance'))}，"
            f"近点 {_fmt_dist(target.get('periapsis_distance'))}。"
        )

    if rel is not None:
        a = abs(rel)
        if a >= 50.0:
            text += f" 相对速度还有 {a:.1f} m/s，很大 —— 先减速（逆推）再靠近，不然会撞上。"
        elif a >= 5.0:
            text += f" 相对速度 {a:.1f} m/s，还在接近中，慢慢收，别推太猛。"
        else:
            text += " 相对速度已经很小，可以尝试对接了。"
    return text


def _body_parent_text(body: Dict[str, Any]) -> str:
    """天体的母天体。

    模组实测：恒星（Juno）的 parent 是 **null** —— 那是「没有母天体」，
    不是「读不到」，要如实说成恒星而不是「未知」。
    """
    if "parent" not in body:
        return ""
    parent = _as_text(body.get("parent")).strip()
    if parent:
        return f"，母天体 {parent}"
    return "，母天体 无（恒星）"


def _summarize_bodies(bodies: List[Dict[str, Any]]) -> str:
    """把 /bodies 压成一段能直接念出来的中文。"""
    items = [b for b in bodies if isinstance(b, dict)]
    if not items:
        return "没有读到天体数据。"

    lines: List[str] = []
    atmosphere_lines: List[tuple] = []
    for b in items:
        name = _as_text(b.get("name")) or "未命名天体"
        if _as_bool(b.get("has_atmosphere")):
            atm = _num(b.get("atmosphere_height"))
            atm_text = f"有大气 {_fmt_dist(atm)}"
            if atm is not None:
                atmosphere_lines.append((atm, name))
        else:
            atm_text = "无大气"
        lines.append(
            f"{name}（半径 {_fmt_dist(b.get('radius'))}，"
            f"表面重力 {_fmt(b.get('surface_gravity'), ' m/s²', 2)}，{atm_text}"
            f"{_body_parent_text(b)}）"
        )

    text = f"星系里有 {len(items)} 颗天体：" + "；".join(lines) + "。"
    if atmosphere_lines:
        atmosphere_lines.sort(reverse=True)
        top_atm, top_name = atmosphere_lines[0]
        text += (
            f" 其中 {len(atmosphere_lines)} 颗有大气；大气最高的是 {top_name}"
            f"（{_fmt_dist(top_atm)}）—— 判断入轨高度时用对应天体自己的这个数。"
        )
    else:
        text += " 没有一颗天体有大气，所以这些天体的入轨只看近点是否高于地表。"
    return text


def _summarize_vessels(vessels: List[Dict[str, Any]]) -> str:
    """把 /vessels 压成一段能直接念出来的中文。"""
    items = [v for v in vessels if isinstance(v, dict)]
    if not items:
        return "当前场景里没有读到飞船。"

    lines: List[str] = []
    for v in items:
        name = _as_text(v.get("name")) or "未命名飞船"
        who = "你的飞船" if _as_bool(v.get("is_player")) else "其他飞船"
        # 质量是游戏内部单位、未标定：原样给出，模组新增的 current_mass 一并列出。
        mass_parts: List[str] = []
        if _num(v.get("mass")) is not None:
            mass_parts.append(f"质量 {_fmt_raw(v.get('mass'))}（{MASS_UNIT_NOTE}）")
        if _num(v.get("current_mass")) is not None:
            mass_parts.append(f"当前质量 {_fmt_raw(v.get('current_mass'))}（{MASS_UNIT_NOTE}）")
        mass_text = "，".join(mass_parts) if mass_parts else "质量 未知"
        lines.append(
            f"{name}（{who}，海拔 {_fmt_dist(v.get('altitude'))}，"
            f"{mass_text}，"
            f"{_fmt(v.get('part_count'), '', 0)} 个零件，"
            f"乘员 {_fmt(v.get('crew_count'), ' 人', 0)}）"
        )
    return f"场景里共 {len(items)} 艘飞船：" + "；".join(lines) + "。"
