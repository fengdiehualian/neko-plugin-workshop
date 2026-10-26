"""配置模型：从 data/config/main.json 读取的纯参数对象。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

CONFIG_PATH = Path(__file__).resolve().parent.parent / "data" / "config" / "main.json"

DEFAULTS = {
    "enabled": True,
    "dry_run": True,
    "poll_interval_s": 1.0,
    "speeding_reset_s": 30.0,
    "speeding_escalate_kmh": 15.0,
    "event_cooldown_s": 10.0,
    "time_warn_min": 60,
    "low_fuel_percent": 15,
    "crash_damage_delta": 0.05,
    # 碰撞判定时间窗（秒）：窗口内 max_damage 累计上升超过 crash_damage_delta
    # 即判碰撞——解决"刮蹭式撞击的损伤分散在多次采样、单帧不足阈值"的漏报
    # （实测：一次事故总 +3~7 点，单帧常 <5 点，旧逻辑全程无播报）。
    "crash_damage_window_s": 5.0,
    # 碰撞加速度阈值（g）：实测撞击峰值 1.80g、正常驾驶 <0.4g，取 1.5 安全。
    # 设 0 关闭该信号（只靠损伤时间窗判定）。
    "crash_accel_g": 1.5,
    "hard_brake_force": 0.9,
    "hard_brake_speed_kmh": 60,
    "push_visibility": ["chat"],
    "push_enabled": True,
    # 全局限流 / 抢占冷却
    "global_rate_limit_s": 12.0,
    "critical_cooldown_s": 5.0,
    # 安全门
    "safety_window_s": 60.0,
    "safety_failure_limit": 5,
    "safety_auto_stop": True,
    # 播报偏好（类别开关 + 频率模式）
    "broadcast_frequency": "standard",
    # ── 内容层（2026-10 玩法扩充）──
    "content_passing_places": True,   # 途经已知地点时介绍该地风土
    "content_radio_dj": True,         # 猫娘电台 DJ 节目
    "content_opening_story": True,    # 每单开场故事 + 起点地域文化
    "content_arrival_culture": True,  # 到达地文化介绍
    "radio_interval_s": 1800.0,       # 电台节目间隔（秒）——去话痨化：30 分钟
    "passing_radius_km": 6.0,         # 途经判定半径
    "passing_cooldown_s": 3600.0,     # 同一地点播报冷却（1 小时）
    "mountain_pass_delta_m": 120.0,   # 山口判定：180s 内海拔变化阈值（米）
    # ── 接力驾驶（她真的开一段；v2 前视点跟踪）──
    "pilot_relay_enabled": True,      # 开阔路段主动提议接管
    "pilot_lookahead_m": 45.0,        # 前视点距离
    "pilot_steer_gain": 1.6,          # 横向偏角 → 转向指令增益
    "pilot_steer_step": 6.0,          # 每拍鼠标相对位移上限（像素）
    "pilot_max_lateral_m": 8.0,       # 横向偏差超过即交还（安全带）
    # ── 陪玩维度引擎（2026-10：主人定调"陪玩是方方面面的"）──
    "companion_enabled": True,        # 陪玩维度总开关
    "companion_gap_s": 90.0,          # 两条主动陪聊的最小间隔（秒）；再乘播报频率倍率
                                      # 安静 144s / 标准 90s / 活跃 58s
    "broadcast_categories": {
        "safety": True,
        "task": True,
        "trip": True,
        "lifecycle": True,
        "chatter": True,
    },
    # 遥测插件路径（相对游戏根目录）
    "telemetry_plugin_rel": "bin/win_x64/plugins/scs-telemetry.dll",
    # 遥测捆绑文件路径（相对插件根目录）
    "telemetry_bundle_rel": "data/telemetry/scs-telemetry.dll",
}


def load_main_config() -> Dict[str, Any]:
    """读取 data/config/main.json；缺失回退默认值。"""
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except (OSError, json.JSONDecodeError):
        pass
    return dict(DEFAULTS)


class PawpilotConfig:
    """插件运行参数。"""

    def __init__(self, raw: Dict[str, Any] = None) -> None:
        data = raw or load_main_config()
        self.enabled = bool(data.get("enabled", DEFAULTS["enabled"]))
        self.dry_run = bool(data.get("dry_run", DEFAULTS["dry_run"]))
        self.poll_interval_s = float(data.get("poll_interval_s", DEFAULTS["poll_interval_s"]))
        self.speeding_reset_s = float(data.get("speeding_reset_s", DEFAULTS["speeding_reset_s"]))
        self.speeding_escalate_kmh = float(data.get("speeding_escalate_kmh", DEFAULTS["speeding_escalate_kmh"]))
        self.event_cooldown_s = float(data.get("event_cooldown_s", DEFAULTS["event_cooldown_s"]))
        self.time_warn_min = int(data.get("time_warn_min", DEFAULTS["time_warn_min"]))
        self.low_fuel_percent = float(data.get("low_fuel_percent", DEFAULTS["low_fuel_percent"]))
        self.crash_damage_delta = float(data.get("crash_damage_delta", DEFAULTS["crash_damage_delta"]))
        self.crash_damage_window_s = float(data.get("crash_damage_window_s", DEFAULTS["crash_damage_window_s"]))
        self.crash_accel_g = float(data.get("crash_accel_g", DEFAULTS["crash_accel_g"]))
        self.hard_brake_force = float(data.get("hard_brake_force", DEFAULTS["hard_brake_force"]))
        self.hard_brake_speed_kmh = float(data.get("hard_brake_speed_kmh", DEFAULTS["hard_brake_speed_kmh"]))
        self.push_visibility = list(data.get("push_visibility", DEFAULTS["push_visibility"]))
        self.push_enabled = bool(data.get("push_enabled", DEFAULTS["push_enabled"]))
        self.global_rate_limit_s = float(data.get("global_rate_limit_s", DEFAULTS["global_rate_limit_s"]))
        self.critical_cooldown_s = float(data.get("critical_cooldown_s", DEFAULTS["critical_cooldown_s"]))
        self.safety_window_s = float(data.get("safety_window_s", DEFAULTS["safety_window_s"]))
        self.safety_failure_limit = int(data.get("safety_failure_limit", DEFAULTS["safety_failure_limit"]))
        self.safety_auto_stop = bool(data.get("safety_auto_stop", DEFAULTS["safety_auto_stop"]))
        self.broadcast_frequency = str(data.get("broadcast_frequency", DEFAULTS["broadcast_frequency"]))
        self.content_passing_places = bool(data.get("content_passing_places", DEFAULTS["content_passing_places"]))
        self.content_radio_dj = bool(data.get("content_radio_dj", DEFAULTS["content_radio_dj"]))
        self.content_opening_story = bool(data.get("content_opening_story", DEFAULTS["content_opening_story"]))
        self.content_arrival_culture = bool(data.get("content_arrival_culture", DEFAULTS["content_arrival_culture"]))
        self.radio_interval_s = float(data.get("radio_interval_s", DEFAULTS["radio_interval_s"]))
        self.passing_radius_km = float(data.get("passing_radius_km", DEFAULTS["passing_radius_km"]))
        self.passing_cooldown_s = float(data.get("passing_cooldown_s", DEFAULTS["passing_cooldown_s"]))
        self.mountain_pass_delta_m = float(data.get("mountain_pass_delta_m", DEFAULTS["mountain_pass_delta_m"]))
        self.pilot_relay_enabled = bool(data.get("pilot_relay_enabled", DEFAULTS["pilot_relay_enabled"]))
        self.pilot_lookahead_m = float(data.get("pilot_lookahead_m", DEFAULTS["pilot_lookahead_m"]))
        self.pilot_steer_gain = float(data.get("pilot_steer_gain", DEFAULTS["pilot_steer_gain"]))
        self.pilot_steer_step = float(data.get("pilot_steer_step", DEFAULTS["pilot_steer_step"]))
        self.pilot_max_lateral_m = float(data.get("pilot_max_lateral_m", DEFAULTS["pilot_max_lateral_m"]))
        self.companion_enabled = bool(data.get("companion_enabled", DEFAULTS["companion_enabled"]))
        self.companion_gap_s = float(data.get("companion_gap_s", DEFAULTS["companion_gap_s"]))
        self.broadcast_categories = dict(data.get("broadcast_categories", DEFAULTS["broadcast_categories"]))
        self.telemetry_plugin_rel = str(data.get("telemetry_plugin_rel", DEFAULTS["telemetry_plugin_rel"]))
        self.telemetry_bundle_rel = str(data.get("telemetry_bundle_rel", DEFAULTS["telemetry_bundle_rel"]))

    def to_dict(self) -> Dict[str, Any]:
        return {k: v for k, v in self.__dict__.items()}
