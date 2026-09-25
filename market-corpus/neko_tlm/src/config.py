"""配置管理 — 通过 SDK self.config 读写插件配置，支持预设模式派生与 MC 端配置同步"""

from . import plan as _plan

COMPANION_MODE_PRESETS = {
    "quiet": {
        "playmate_quiet_stable_seconds": 240,
        "playmate_quiet_cooldown": 900,
        "playmate_suggestion_cooldown": 1500,
    },
    "standard": {
        "playmate_quiet_stable_seconds": 90,
        "playmate_quiet_cooldown": 300,
        "playmate_suggestion_cooldown": 600,
    },
    "active": {
        "playmate_quiet_stable_seconds": 45,
        "playmate_quiet_cooldown": 150,
        "playmate_suggestion_cooldown": 240,
    },
}

COMPANION_CUSTOM_FIELDS = [
    "playmate_quiet_stable_seconds",
    "playmate_quiet_cooldown",
    "playmate_suggestion_cooldown",
]


async def load_config(plugin):
    """从 SDK self.config 读取 [minecraft_bridge] 段并填充插件实例变量"""
    try:
        cfg = await plugin.config.dump()
    except Exception as e:
        plugin.logger.warning(f"读取插件配置失败: {e}")
        return

    bridge = cfg.get("minecraft_bridge", {}) or {}
    plugin._ws_url = bridge.get("ws_url", plugin._ws_url)
    plugin._heartbeat_interval = bridge.get("heartbeat_interval", plugin._heartbeat_interval)
    plugin._reconnect_interval = bridge.get("reconnect_interval", plugin._reconnect_interval)
    plugin._max_reconnect_interval = bridge.get("max_reconnect_interval", plugin._max_reconnect_interval)
    plugin._assigned_maid_id = bridge.get("assigned_maid_id", "")
    plugin._assigned_maid_name = bridge.get("assigned_maid_name", "")
    plugin._awareness_interval = bridge.get("awareness_interval", plugin._awareness_interval)
    plugin._companion_mode = _normalize_companion_mode(
        bridge.get("companion_mode", plugin._companion_mode),
        plugin,
    )
    _load_plan_config(plugin, bridge)
    _load_playmate_config(plugin, bridge)
    _apply_companion_mode(plugin)


def _load_plan_config(plugin, config):
    """从配置段加载目标板状态到插件实例变量"""
    state = _plan.normalize_plan_state(config.get("structured_plan", {}))
    text = str(config.get("current_plan", "") or "")
    if text:
        plugin._current_plan_text = text
        plugin._plan_state = state if state.get("title") or state.get("steps") else _plan.plan_from_text(text)
    elif state.get("title") or state.get("steps"):
        plugin._plan_state = state
        plugin._current_plan_text = _plan.plan_to_text(state)
    else:
        plugin._current_plan_text = ""
        plugin._plan_state = _plan.empty_plan()


def _load_playmate_config(plugin, config):
    """从配置段加载陪玩相关参数到插件实例变量"""
    plugin._playmate_memory_items = config.get("playmate_memory_items", plugin._playmate_memory_items)
    plugin._playmate_memory_summary_length = config.get("playmate_memory_summary_length", plugin._playmate_memory_summary_length)
    plugin._playmate_memory_inject_items = config.get("playmate_memory_inject_items", plugin._playmate_memory_inject_items)
    plugin._playmate_memory_inject_chars = config.get("playmate_memory_inject_chars", plugin._playmate_memory_inject_chars)
    plugin._playmate_activity_debounce_checks = config.get("playmate_activity_debounce_checks", plugin._playmate_activity_debounce_checks)
    plugin._playmate_activity_cooldown = config.get("playmate_activity_cooldown", plugin._playmate_activity_cooldown)
    plugin._playmate_quiet_stable_seconds = config.get("playmate_quiet_stable_seconds", plugin._playmate_quiet_stable_seconds)
    plugin._playmate_quiet_cooldown = config.get("playmate_quiet_cooldown", plugin._playmate_quiet_cooldown)
    plugin._playmate_aggregate_window = config.get("playmate_aggregate_window", plugin._playmate_aggregate_window)
    plugin._playmate_throttle_window = config.get("playmate_throttle_window", plugin._playmate_throttle_window)
    plugin._playmate_throttle_limit = config.get("playmate_throttle_limit", plugin._playmate_throttle_limit)
    plugin._playmate_minigame_feedback_cooldown = config.get("playmate_minigame_feedback_cooldown", plugin._playmate_minigame_feedback_cooldown)
    plugin._playmate_minigame_context_chars = config.get("playmate_minigame_context_chars", plugin._playmate_minigame_context_chars)
    plugin._playmate_suggestion_cooldown = config.get("playmate_suggestion_cooldown", plugin._playmate_suggestion_cooldown)
    plugin._playmate_debug_log_enabled = config.get("playmate_debug_log_enabled", plugin._playmate_debug_log_enabled)
    plugin._playmate_debug_log_max_bytes = config.get("playmate_debug_log_max_bytes", plugin._playmate_debug_log_max_bytes)


def _normalize_companion_mode(mode, plugin=None):
    """规范化陪玩模式名称，未知值回退为 standard"""
    mode = str(mode or "standard").strip().lower()
    if mode in ("quiet", "standard", "active", "custom"):
        return mode
    if plugin:
        plugin.logger.warning(f"未知的 companion_mode '{mode}'，回退为 standard")
    return "standard"


def _apply_companion_mode(plugin):
    """根据当前陪玩模式应用预设参数（custom 模式不覆盖自定义值）"""
    mode = _normalize_companion_mode(getattr(plugin, "_companion_mode", "standard"), plugin)
    plugin._companion_mode = mode
    preset = COMPANION_MODE_PRESETS.get(mode)
    if not preset:
        return
    for key, value in preset.items():
        setattr(plugin, f"_{key}", value)


def _coerce_custom_int(value, fallback, minimum=1):
    """将输入值转换为不小于 minimum 的整数，失败时返回 fallback"""
    try:
        number = int(str(value).strip())
    except Exception:
        return fallback
    return max(minimum, number)


def apply_custom_companion_settings(plugin, values):
    """将自定义陪玩参数应用到插件实例变量"""
    for key in COMPANION_CUSTOM_FIELDS:
        if key not in values:
            continue
        current = getattr(plugin, f"_{key}", 1)
        setattr(plugin, f"_{key}", _coerce_custom_int(values.get(key), current))


def companion_settings(plugin):
    """返回当前自定义陪玩参数的快照"""
    return {
        key: getattr(plugin, f"_{key}", COMPANION_MODE_PRESETS["standard"].get(key, 1))
        for key in COMPANION_CUSTOM_FIELDS
    }


def _runtime_config_payload(plugin):
    """构造待持久化的运行时配置字典"""
    payload = {
        "ws_url": plugin._ws_url,
        "heartbeat_interval": getattr(plugin, "_heartbeat_interval", 30),
        "reconnect_interval": getattr(plugin, "_reconnect_interval", 5),
        "max_reconnect_interval": getattr(plugin, "_max_reconnect_interval", 60),
        "assigned_maid_id": plugin._assigned_maid_id,
        "assigned_maid_name": plugin._assigned_maid_name,
        "awareness_interval": getattr(plugin, "_awareness_interval", 5),
        "companion_mode": getattr(plugin, "_companion_mode", "standard"),
    }
    for key in COMPANION_CUSTOM_FIELDS:
        payload[key] = getattr(plugin, f"_{key}", COMPANION_MODE_PRESETS["standard"].get(key, 1))
    payload["current_plan"] = getattr(plugin, "_current_plan_text", "")
    payload["structured_plan"] = _plan.normalize_plan_state(getattr(plugin, "_plan_state", {}))
    return payload


async def save_config(plugin):
    """通过 SDK 的 update_own_config 持久化配置变更并刷新 self.config"""
    payload = _runtime_config_payload(plugin)
    try:
        await plugin.ctx.update_own_config({"minecraft_bridge": payload})
        return True
    except Exception as e:
        plugin.logger.warning(f"保存插件配置失败: {e}")
        return False


def sync_config(plugin, config_data):
    """从 MC 端同步运行时配置到插件实例变量（不涉及 toml 持久化）"""
    plugin._maid_agent_enabled = config_data.get("maid_agent_enabled", True)
    plugin._command_execution_enabled = config_data.get("command_execution_enabled", False)
    plugin._chat_bubble_enabled = config_data.get("chat_bubble_enabled", True)
    plugin._chat_box_enabled = config_data.get("chat_box_enabled", True)
