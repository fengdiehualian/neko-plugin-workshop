"""事件规格表（EventSpec）—— 副司机告警的唯一定义源。

设计取自 `neko_pawpilot/core/event_catalog.py`（同域成熟参照：欧卡2 副驾），
按用户给的铁路口径重写：类别 / 优先级 / 是否抢占 / 静默间隔。

**本表只收录"在 Open Rails REST API 里确实有数据源"的事件。**
逐条核对过程与 41 条无数据源事件的处置见同目录 `UNDETECTABLE_EVENTS.md`。
规则：detector 只为本表事件产生候选。宁可少报，不可假报 —— 假报会训练用户
忽略告警，比不报更危险。

数据源记号（详见 core/snapshot.py 的解码依据）
--------------------------------------------
``tm``  = /API/TRACKMONITORDISPLAY（F4 Track Monitor）
``cab`` = /API/CABCONTROLS（机控器数值）
``hud`` = /API/HUD/{0,1}（F5 基础 HUD / Shift+F5 电气扩展）

优先级语义（用户口径）
--------------------
``P0`` 紧急：冒进/超速脱轨/追尾/弓网/司机失能 → 必须立即打断播报
``P1`` 重要：信号未确认/道岔未锁/限速逼近/制动未缓解 → 应尽快播报
``P2`` 常规：常规确认/仪表读数/调车距离提示
``P3`` 提示：预告/时刻/下一站等非安全类
"""

from __future__ import annotations

from dataclasses import dataclass

# ── 类别（用户口径）─────────────────────────────────────────────────────
CAT_SIGNAL = "信号"
CAT_SPEED = "限速"
CAT_SHUNT = "调车"
CAT_GAUGE = "仪表"
CAT_EMERGENCY = "应急"
CAT_PARKING = "防溜"
CAT_STATE = "状态"  # 本插件补充：数据里直接可读、用户清单未列的状态类

CATEGORIES = (CAT_SIGNAL, CAT_SPEED, CAT_SHUNT, CAT_GAUGE, CAT_EMERGENCY, CAT_PARKING, CAT_STATE)

#: 默认开启的类别。仪表类噪音大（压力/电压持续变动），默认关，由用户按需打开。
CATEGORY_DEFAULTS: dict[str, bool] = {
    CAT_SIGNAL: True,
    CAT_SPEED: True,
    CAT_SHUNT: True,
    CAT_GAUGE: False,
    CAT_EMERGENCY: True,
    CAT_PARKING: True,
    CAT_STATE: False,
}

# ── 优先级 ──────────────────────────────────────────────────────────────
P0, P1, P2, P3 = "P0", "P1", "P2", "P3"

#: 严重度 → 宿主 priority。宿主侧约定"数字越大越重要"，既有标尺为
#: bilibili gift/SC=9、memo=8、minecraft alert=9、study=5。
SEVERITY_TO_PRIORITY: dict[str, int] = {P0: 9, P1: 6, P2: 4, P3: 2}

#: 播报密度档位：统一缩放所有 cooldown（同 pawpilot 的 frequency preset）
FREQUENCIES = ("quiet", "standard", "active")
FREQUENCY_MULTIPLIERS: dict[str, float] = {"quiet": 1.6, "standard": 1.0, "active": 0.65}

#: 推送路由。**必须与 `preempt` 解耦** —— 两者是不同的问题：
#:   ``preempt`` = 插件内节流语义：能否绕过冷却/限流、独占槽位（影响 arbiter）
#:   ``route``   = 占用哪个宿主通道（影响额度与成本）
#:
#: 为什么不能简单地 "preempt ⇒ respond"：宿主对 ``respond`` 有硬上限
#: ``AGENT_PROACTIVE_ANALYZE_MAX_PER_SESSION = 2``（config/agent_settings.py:247）。
#: ``respond`` 会让角色自发起口，那一轮 ``had_user_input=False``，走的是**被限流**的
#: 分析路径；同一会话里第 3 条起被宿主静默丢弃（2026-09-20 实测：
#: ``skip proactive: per-session cap reached (2/2)``）。
#: 本表有 14 条 P0，若都走 respond 就是 14 条抢 2 个名额 —— 后 12 条等于没报。
#:
#: 所以只给"不作为即事故"的极小集合分配 ``respond``；其余一律 ``read``
#: （静默进上下文，等用户开口时带出；那一轮 ``had_user_input=True``，**不受额度约束**）。
ROUTE_RESPOND = "respond"
ROUTE_READ = "read"

#: 允许占用稀缺 ``respond`` 通道的事件（"不作为即事故"级）。刻意保持极小。
IMMEDIATE_IDS: frozenset[str] = frozenset({
    "EMERG_OUT_OF_CONTROL_SPAD",         # 已冒进信号
    "EMERG_OUT_OF_CONTROL_OFF_TRACK",    # 已脱线
    "EMERG_RED_STOP",                    # 红灯近距离且速度未降
    "SPD_TRACK_RED",                     # 严重超速（OR 原生深红）
    "EMERG_PANTO_FAULT",                 # 弓网故障
})

#: 宿主允许的每会话 ``respond`` 上限。仅用于告警与文档。
#: ⚠ 若要让全部 P0 都能真正打断，必须调大 config/agent_settings.py 的
#: AGENT_PROACTIVE_ANALYZE_MAX_PER_SESSION，否则超额部分会被静默丢弃。
HOST_RESPOND_BUDGET_PER_SESSION = 2


@dataclass(frozen=True)
class EventSpec:
    """单个事件的静态策略。"""

    event_id: str
    category: str
    severity: str
    preempt: bool              # 可抢占当前 AI 播报
    cooldown_seconds: float    # 同类事件最小静默间隔；<0 = 触发一次后长期静默
    src: str                   # 数据源（本表不为空）
    note: str = ""             # 判定口径的注意事项

    @property
    def priority(self) -> int:
        return SEVERITY_TO_PRIORITY.get(self.severity, 2)

    @property
    def route(self) -> str:
        """占用哪个宿主通道。只有 IMMEDIATE_IDS 能用稀缺的 respond。"""
        return ROUTE_RESPOND if self.event_id in IMMEDIATE_IDS else ROUTE_READ


# (event_id, category, severity, preempt, cooldown_s, src, note)
_RAW: tuple[tuple[str, str, str, bool, float, str, str], ...] = (
    # ── 二、信号类 ──────────────────────────────────────────────────────
    # SignalColItem 的 16x16 矩形 1:1 映射 TrackMonitorSignalAspect，可精确解码
    ("SIG_RED_APPROACH", CAT_SIGNAL, P0, True, 10, "tm.signal=Stop + tm.dist", ""),
    ("SIG_RED_STOP", CAT_SIGNAL, P0, True, 5,
     "tm.signal=Stop + 距离窗口内速度未降",
     "口径按用户指定：不用减速度。红灯距离从 RED_APPROACH_ENTER_M 降到 "
     "RED_APPROACH_EXIT_M 的窗口内，若速度下降不足 SPEED_DROP_TOLERANCE_KMH 即触发。见 constants.py"),
    ("SIG_YELLOW_APPROACH", CAT_SIGNAL, P1, True, 15, "tm.signal=Approach_1/2/3 + tm.dist", ""),
    ("SIG_DOUBLE_YELLOW", CAT_SIGNAL, P1, True, 15, "tm.signal=Approach_2",
     "OR 的 aspect 是信号系统语义，无'双黄'字面；Approach_2 是最接近的对应，属推断"),
    ("SIG_UPGRADE_CHANGE", CAT_SIGNAL, P1, False, 15, "tm.signal 相邻采样变化",
     "信号等级跳变（升级或降级）"),
    ("SIG_INCONSISTENT", CAT_SIGNAL, P0, True, 10, "cab.ASPECT_DISPLAY vs tm.signal",
     "两源都存在，但 ASPECT_DISPLAY 量程仅 0..1、粒度粗；一致性判据需现场校准后再启用"),

    # ── 三、限速与超速类 ────────────────────────────────────────────────
    # 全部直接采用 OR 原生轨道颜色分级，不自算百分比（用户决定 2026-09-20）。
    # 阈值逐字来自 OR 源码 TrackMonitorDisplay.TrackColor()。
    ("SPD_TRACK_ORANGE", CAT_SPEED, P1, True, 10, "tm.track_color=Orange",
     "OR 原生：limit <= v < limit+5 m/s（约 +18 km/h）"),
    ("SPD_TRACK_RED", CAT_SPEED, P0, True, 5, "tm.track_color=OrangeRed",
     "OR 原生：v >= limit+5 m/s"),
    ("SPD_NEAR_LIMIT", CAT_SPEED, P1, False, 15, "tm.track_color=PaleGreen",
     "原为自算 95%，用户决定改为直接用 OR 原生 PaleGreen（速度进入限速值附近但未超限）"),
    ("SPD_LIMIT_DROP", CAT_SPEED, P1, False, 20, "tm.limit 相邻采样下降", ""),
    ("SPD_TEMP_LIMIT_2KM", CAT_SPEED, P1, False, 30, "tm.limit_color=OrangeRed + tm.limit_marker_distance_m",
     "LimitCol 的 OrangeRed 标出临时限速（SpeedItemType.TempRestrictedStart）"),
    ("SPD_TEMP_LIMIT_1500", CAT_SPEED, P2, False, 30, "tm.limit_color=OrangeRed + tm.limit_marker_distance_m", "同上，距离阈值不同"),
    ("SPD_TEMP_LIMIT_1000", CAT_SPEED, P2, False, 30, "tm.limit_color=OrangeRed + tm.limit_marker_distance_m", "同上"),
    ("SPD_SIDE_LIMIT", CAT_SPEED, P1, True, 15, "tm.limit_marker_value + tm.switch 方向",
     "侧向限速由信号 AllowedSpeed 给出，'是否侧向'需结合 ►/◄ 道岔符号，属推断"),

    # ── 四、调车、道岔、连挂类 ──────────────────────────────────────────
    ("SWITCH_SIDE", CAT_SHUNT, P1, False, 15, "tm.track=◄/►", "符号给出道岔开通方向"),
    ("SHUNT_END_OF_TRACK", CAT_SHUNT, P1, True, 10, "tm.authority=End Trck/End Path + tm.dist",
     "AuthorityLabels 行文本可读"),
    ("SHUNT_OVERSPEED", CAT_SHUNT, P0, True, 5, "tm.speed > SHUNT_OVERSPEED_KMH 且处于调车状态",
     "调车限速 3~5 km/h（用户给出）；'是否调车'由控制模式/道岔语境推断，需现场校准"),

    # ── 五、仪表、设备、列车状态类 ──────────────────────────────────────
    ("GAUGE_BRAKE_PIPE_DROP", CAT_GAUGE, P1, True, 10, "hud.BP / cab.BRAKE_PIPE 相邻采样下降", ""),
    ("GAUGE_BRAKE_NOT_RELEASED", CAT_GAUGE, P1, True, 10, "hud.BC>0 且 cab.CP_HANDLE 在牵引位",
     "需推断'应缓解'，口径要实测校准"),
    ("GAUGE_MAIN_RESERVOIR", CAT_GAUGE, P2, False, 20, "cab.MAIN_RES",
     "部分机车该控制项量程为 0（空占位）→ 不可用；本项按 available 判定后再报"),
    ("GAUGE_TAIL_PRESSURE", CAT_GAUGE, P1, False, 15, "hud.EOT BC/BP", "仅部分机车给出列尾压力"),
    ("ELEC_PANTO_DOWN", CAT_GAUGE, P0, True, 5, "cab.PANTOGRAPH/PANTO_DISPLAY + hud.受电弓", ""),
    ("ELEC_NET_VOLTAGE", CAT_GAUGE, P1, True, 10, "cab.LINE_VOLTAGE", "网压控制项只在部分机车 CVF 中出现"),

    # ── 八、非正常与应急类 ──────────────────────────────────────────────
    # ⚠ 高价值：OR 自己就报告脱控原因，无需推断。见 snapshot.OUT_OF_CONTROL_REASONS
    ("EMERG_OUT_OF_CONTROL_SPAD", CAT_EMERGENCY, P0, True, 5, "tm.control_mode=脱控 + reason=SPAD",
     "冒进信号（Signal Passed At Danger）——OR 权威判定"),
    ("EMERG_OUT_OF_CONTROL_MISALIGNED_SWITCH", CAT_EMERGENCY, P0, True, 5,
     "tm.control_mode=脱控 + reason=Misalg Sw", "道岔错位——OR 权威判定"),
    ("EMERG_OUT_OF_CONTROL_OFF_AUTH", CAT_EMERGENCY, P0, True, 10, "tm.control_mode=脱控 + reason=Off Auth/Off Path",
     "越权或偏离径路——OR 权威判定"),
    ("EMERG_OUT_OF_CONTROL_OFF_TRACK", CAT_EMERGENCY, P0, True, 5, "tm.control_mode=脱控 + reason=Off Track",
     "脱线——OR 权威判定"),
    ("EMERG_RED_STOP", CAT_EMERGENCY, P0, True, 5, "tm.signal=Stop + tm.dist<安全距离 + 速度未降",
     "与 SIG_RED_STOP 同源，但在更近距离仍未制动时升级为应急"),
    ("EMERG_TAIL_FAIL", CAT_EMERGENCY, P1, True, 15, "hud.EOT BC/BP", "仅当机车给出 EOT 时可用；'失效'判据待校准"),
    ("EMERG_STOPPED", CAT_EMERGENCY, P1, False, 30, "tm.speed==0 且非站内",
     "'被迫'需语境；速度归零可测，被迫性属推断"),
    ("EMERG_PANTO_FAULT", CAT_EMERGENCY, P0, True, 5, "cab.PANTOGRAPH + cab.LINE_VOLTAGE 组合",
     "弓降+网压异常可组合判定，但'故障'与'正常降弓'难区分"),

    # ── 九、防溜与终到入库类 ────────────────────────────────────────────
    ("PARK_BRAKE_NOT_SET", CAT_PARKING, P0, True, 10,
     "tm.speed==0 且 cab.TRAIN_BRAKE==0 且 cab.ENGINE_BRAKE==0",
     "手制动/铁鞋不在数据里，只能覆盖空气制动"),

    # ── 状态类（插件补充：数据里明确存在且有用）─────────────────────────
    ("CAB_ORIENTATION_CHANGE", CAT_STATE, P1, False, 30, "tm.'Cab ORIEN' 变化",
     "换端作业——用户明确要求覆盖的场景"),
    ("TRAIN_OFF_ROUTE", CAT_STATE, P0, True, 15, "tm.track 含 '⧯'[OrangeRed] (ManualOffRoute)",
     "手动模式偏离径路，OR 用橙色红标出"),
    ("OPPOSITE_TRAIN_AHEAD", CAT_STATE, P1, False, 30, "tm.track 含 '█'[Orange] + tm.dist", "前方反向列车"),
    ("END_OF_AUTHORITY", CAT_STATE, P1, True, 15, "tm.track 含 '▬'[OrangeRed] + tm.dist", "行车许可终点"),
    ("REVERSAL_POINT", CAT_STATE, P2, False, 30, "tm.track 含 '↶'", "折返点；'▬'[OrangeRed] 表示无效折返"),
    ("WAITING_POINT", CAT_STATE, P2, False, 30, "tm.track 含 '✋'", "Yellow=已启用 / OrangeRed=禁用"),
    ("STATION_AHEAD", CAT_STATE, P3, False, 120, "tm.track 含 '▐'/'▌'[Blue] + tm.dist", "站台位置"),
    ("GRADIENT_NOTICE", CAT_STATE, P3, False, 120, "tm.gradient_pct + 箭头(↗/↘)", "坡度提示"),
)


CATALOG: dict[str, EventSpec] = {
    r[0]: EventSpec(event_id=r[0], category=r[1], severity=r[2], preempt=r[3],
                    cooldown_seconds=r[4], src=r[5], note=r[6])
    for r in _RAW
}

#: 保守默认：未知事件按最不重要的常规项处理，且**不抢占**
_FALLBACK = EventSpec("", CAT_STATE, P3, False, 30.0, "", "")


def spec(event_id: str) -> EventSpec:
    return CATALOG.get(event_id, _FALLBACK)


def preempt_ids() -> frozenset[str]:
    return frozenset(eid for eid, s in CATALOG.items() if s.preempt)


def by_category() -> dict[str, list[EventSpec]]:
    out: dict[str, list[EventSpec]] = {}
    for s in CATALOG.values():
        out.setdefault(s.category, []).append(s)
    return out


def scaled_cooldown(s: EventSpec, frequency: str = "standard") -> float:
    """按播报密度档位缩放冷却。``<0`` 表示一次性事件，不缩放。"""
    if s.cooldown_seconds < 0:
        return s.cooldown_seconds
    return s.cooldown_seconds * FREQUENCY_MULTIPLIERS.get(frequency, 1.0)


def audit() -> str:
    """人类可读的事件表（给用户过目 / 写进日志）。"""
    lines = [
        f"{'event_id':<42} {'类别':<5} {'级别':<4} {'抢占':<5} {'路由':<8} {'静默':>5}  数据源",
        "-" * 150,
    ]
    for s in CATALOG.values():
        lines.append(
            f"{s.event_id:<42} {s.category:<5} {s.severity:<4} "
            f"{'是' if s.preempt else '否':<5} {s.route:<8} {s.cooldown_seconds:>5.0f}  {s.src}"
        )
    by_cat: dict[str, int] = {}
    for s in CATALOG.values():
        by_cat[s.category] = by_cat.get(s.category, 0) + 1
    p0 = [s for s in CATALOG.values() if s.severity == P0]
    resp = [s for s in CATALOG.values() if s.route == ROUTE_RESPOND]
    lines += [
        "-" * 150,
        f"共 {len(CATALOG)} 条可检测事件：" + "，".join(f"{k} {v}" for k, v in by_cat.items()),
        f"P0 级 {len(p0)} 条；可抢占 {len(preempt_ids())} 条。",
        f"路由：respond {len(resp)} 条（{', '.join(s.event_id for s in resp)}）；"
        f"其余 {len(CATALOG) - len(resp)} 条走 read。",
        "",
        "⚠ 额度警告：宿主每个会话只放行 "
        f"{HOST_RESPOND_BUDGET_PER_SESSION} 次 respond（主动分析上限），"
        f"第 {HOST_RESPOND_BUDGET_PER_SESSION + 1} 次起被静默丢弃。",
        f"   本表有 {len(p0)} 条 P0，其中只有 {len(resp)} 条获准用 respond，"
        "且同一个会话里最多也只有 2 条能真正打断。",
        "   要让更多 P0 都能打断，必须调大 config/agent_settings.py 的"
        " AGENT_PROACTIVE_ANALYZE_MAX_PER_SESSION（官方注释：调大 = 主动能力更明显，但成本/打扰风险上升）。",
        "   其余事件走 read：静默进上下文，等用户开口那一轮带出 —— 那条路径**不占额度**。",
        "无数据源的事件已移出本表，见 UNDETECTABLE_EVENTS.md。",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    print(audit())
