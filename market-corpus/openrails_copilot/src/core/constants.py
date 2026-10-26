"""可调常量：所有阈值集中在这里，附来源说明。

为什么单列一个文件
-----------------
这个插件原先把全部阈值硬编码在 `__init__.py` 里，`config.example.toml` 里的值
根本没人读（改了不生效）——这是已知的坑。新架构把"可调量"集中到这一处，
后续接插件配置时只改这里。

⚠ Open Rails 本身**不暴露**其中大部分阈值（它只给观测值）。这些数字是
本插件的判定口径，不是游戏参数，所以必须由插件定义并可被用户覆盖。
"""

from __future__ import annotations

# ── 信号 / 红灯 ─────────────────────────────────────────────────────────

RED_SIGNAL_SAFE_DISTANCE_M: float = 200.0
"""红灯"停车安全距离"（米）。**用户指定默认值 200 m（2026-09-20）。**

依据（用户给出）：铁路安全规范要求 ATP 在红灯接近区段内能从 50 km/h 常用制动
到零；需覆盖紧急制动距离（约 124~135 m）+ 天线至车头最大距离（11 m）+ 余量。
200 m 覆盖该范围并留有余量。

⚠ Open Rails 不暴露"红灯安全距离"参数 —— 这是插件侧的判定口径，不是游戏参数。

调车场景另用 ``SHUNT_SAFE_DISTANCE_M``：调车限速仅 3~5 km/h，制动距离极短，
沿用 200 m 会让"连挂/入线"这类本来就慢的作业被误判成冒进风险。
"""

SHUNT_SAFE_DISTANCE_M: float = 50.0
"""调车场景的安全距离（米）。**用户指定（2026-09-20）**：低速下制动距离极短。"""

RED_APPROACH_ENTER_M: float = 200.0
""""速度未降"观察窗口的**起点**距离（米）。等于 ``RED_SIGNAL_SAFE_DISTANCE_M``。"""

RED_APPROACH_EXIT_M: float = 100.0
""""速度未降"观察窗口的**终点**距离（米）。窗口 = [EXIT, ENTER]。"""

SPEED_DROP_TOLERANCE_KMH: float = 2.0
"""速度变化容差（km/h）。**用户指定（2026-09-20）**，用于过滤抖动。

判据（用户口径，**刻意不用减速度**）：
    在红灯距离从 ENTER 降到 EXIT 的窗口内，
    若 ``speed(EXIT) >= speed(ENTER) - TOLERANCE`` → 视为"速度未降"，触发停车告警。

原因（用户给出）：Open Rails 的 HUD 数据刷新率有限，实时计算减速度（m/s²）
会产生大量噪声，导致误报或漏报。所以只用两个距离点的**速度快照**比较，
不引入微分。
"""

# ── 超速分级（直接采用 OR 原生轨道颜色，不自算百分比）──────────────────

TRACK_COLOR_TIERS: tuple[tuple[str, str], ...] = (
    ("Green", "正常：速度低于限速 1 m/s（约 3.6 km/h）以上"),
    ("PaleGreen", "接近限速：已进入限速值附近但未超限"),
    ("Orange", "轻度超速：超限但不超过 5 m/s（约 18 km/h）"),
    ("OrangeRed", "严重超速：超限 5 m/s 以上"),
)
"""(轨道颜色, 含义)。**用户决定（2026-09-20）：放弃自算 95%，直接用 OR 原生分级。**

阈值逐字来自 OR 源码 ``TrackMonitorDisplay.TrackColor()``：

    absoluteSpeed < allowedSpeed - 1.0 m/s   → Green
    absoluteSpeed < allowedSpeed            → PaleGreen
    absoluteSpeed < allowedSpeed + 5.0 m/s   → Orange
    否则                                     → OrangeRed

``TrackColor()`` 与 ``SpeedColor()`` 是**两张表**：阈值相同，但"正常"色不同
（Track=Green，Speed=White）。分级只能取轨道色，不能取速度文本色。
"""

# ── 调车 ────────────────────────────────────────────────────────────────

SHUNT_OVERSPEED_KMH: float = 5.0
"""调车作业限速上限（km/h）。**用户给出**：调车限速 3~5 km/h。"""

# ── 采样 ────────────────────────────────────────────────────────────────

SAMPLE_INTERVAL_S: float = 2.0
"""监控采样周期（秒）。沿用原 @timer_interval(seconds=2)。"""

# ── 播报节流（对齐 neko_pawpilot 的安全阀口径）─────────────────────────

GLOBAL_RATE_LIMIT_S: float = 12.0
"""任意两条**非抢占**播报之间的最小间隔（秒）。

取自同域成熟参照 ``neko_pawpilot/core/safety_guard.py`` 的
``global_rate_limit_s = 12.0``：该插件在欧卡2 里长期运行、验证过的防刷屏口径。
"""

CRITICAL_COOLDOWN_S: float = 5.0
"""两条**抢占**播报之间的最小间隔（秒）。同源：pawpilot 的 ``critical_cooldown_s``。"""

PLAYER_QUIET_WINDOW_S: float = 60.0
"""用户刚说完话后的静默窗（秒）。同源：pawpilot ``on_player_speak(silence_s=60.0)``。

不抢刚开口的人的话——在 N.E.K.O 里这条还额外划算：用户说话的那一轮是
``had_user_input=True``，走的是**不受 2 次/会话上限约束**的分析路径。
"""

SAFETY_FAILURE_WINDOW_S: float = 60.0
"""输出失败计数窗口（秒）。同源：pawpilot ``safety_window_s``。"""

SAFETY_FAILURE_LIMIT: int = 5
"""窗口内失败达到此数即自动急停（断路器）。同源：pawpilot ``safety_failure_limit``。"""
