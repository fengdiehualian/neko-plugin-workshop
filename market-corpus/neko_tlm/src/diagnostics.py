"""Minecraft 桥接诊断。

本模块只诊断插件与 mod 的边界问题。宿主侧的 N.E.K.O 模型路由、TTS、
全局工具调用策略不在本插件的诊断范围内。
"""


def _check(status, title, detail, suggestion=""):
    item = {"status": status, "title": title, "detail": detail}
    if suggestion:
        item["suggestion"] = suggestion
    return item


async def diagnose_bridge(plugin):
    checks = []

    bridge_started = bool(plugin._bridge)
    if not bridge_started:
        checks.append(_check(
            "error",
            "桥接线程",
            "Python WebSocket 客户端尚未启动。",
            "确认插件已启用；如果刚启用，请刷新插件面板。",
        ))
    else:
        checks.append(_check("ok", "桥接线程", "Python WebSocket 客户端已启动。"))

    if plugin.connected:
        checks.append(_check("ok", "WebSocket", f"已连接到 {plugin._ws_url}。"))
    else:
        checks.append(_check(
            "warning",
            "WebSocket",
            f"尚未连接到 {plugin._ws_url}。",
            "确认 Minecraft 已启动并进入世界且未暂停；检查 mod 是否安装、端口是否一致、是否被占用。",
        ))
        _append_bridge_error_check(plugin, checks)

    if len(plugin._request_futures) > 3:
        checks.append(_check(
            "warning",
            "待处理请求",
            f"当前有 {len(plugin._request_futures)} 个待处理请求。",
            "如果持续增加，可能是连接卡住或 Minecraft 主线程无响应。",
        ))
    else:
        checks.append(_check("ok", "待处理请求", f"当前待处理请求 {len(plugin._request_futures)} 个。"))

    if plugin.connected:
        await _diagnose_connected(plugin, checks)
    else:
        checks.append(_check(
            "info",
            "诊断范围",
            "当前未连接，无法查询 Minecraft mod 配置和女仆状态。",
        ))

    checks.append(_check(
        "info",
        "宿主边界",
        "本诊断只覆盖 Minecraft 桥接插件和游戏侧 mod；N.E.K.O 宿主模型、TTS、全局工具调用策略需在宿主侧检查。",
    ))

    overall = _overall_status(checks)
    return {
        "status": overall,
        "summary": _summary(overall),
        "ws_url": plugin._ws_url,
        "companion_mode": plugin._companion_mode,
        "connected": bool(plugin.connected),
        "assigned_maid_id": plugin._assigned_maid_id,
        "assigned_maid_name": plugin._assigned_maid_name,
        "checks": checks,
    }


async def _diagnose_connected(plugin, checks):
    status_result = await plugin._send_request({"type": "get_maid_status"}, timeout=5)
    status_ok, maids = _append_maid_status_checks(plugin, checks, status_result)

    config_result = await plugin._send_request({"type": "get_config"}, timeout=3)
    _append_config_checks(checks, config_result, status_ok=status_ok)

    if not status_ok:
        return

    if not plugin._assigned_maid_id:
        checks.append(_check(
            "warning",
            "指定女仆",
            "尚未指定 AI 控制的女仆。",
            "在插件面板中选择一个女仆，或通过 assign_maid entry 指定。",
        ))
        return

    assigned = next((m for m in maids if m.get("id") == plugin._assigned_maid_id), None)
    if assigned:
        checks.append(_check("ok", "指定女仆", f"已指定女仆：{plugin._assigned_maid_name or assigned.get('name', '')}。"))
    else:
        checks.append(_check(
            "error",
            "指定女仆",
            f"配置中的女仆 ID 未在当前世界找到：{plugin._assigned_maid_id}",
            "可能换了存档或女仆已死亡/不存在；请在面板中重新指定。",
        ))


def _append_bridge_error_check(plugin, checks):
    bridge = getattr(plugin, "_bridge", None)
    if not bridge:
        return
    error_type = getattr(bridge, "last_error_type", "")
    if not error_type:
        return
    error_message = getattr(bridge, "last_error_message", "")
    retry_delay = getattr(bridge, "next_reconnect_delay", 0)
    retry_text = f"下一次自动重连约 {retry_delay} 秒后。" if retry_delay else ""
    if error_type == "InvalidMessage":
        checks.append(_check(
            "warning",
            "最近连接错误",
            f"端口有响应，但不是有效的 WebSocket 握手：{error_message}。{retry_text}",
            "通常是 Minecraft mod WebSocket 尚未完全启动、端口配置不一致，或 48920 被其它程序占用。可在游戏内确认 N.E.K.O 模式已启用并进入存档；必要时换一个端口并让插件 UI 与 mod 配置保持一致。",
        ))
        return
    checks.append(_check(
        "info",
        "最近连接错误",
        f"{error_type}: {error_message}。{retry_text}",
    ))


def _append_config_checks(checks, config_result, status_ok):
    if config_result.get("type") == "config":
        data = config_result.get("data", {})
        checks.append(_check("ok", "mod 配置", "已成功读取 Minecraft mod 配置。"))
        if data.get("maid_agent_enabled", True):
            checks.append(_check("ok", "女仆 Agent", "导航与采集动作已启用。"))
        else:
            checks.append(_check("warning", "女仆 Agent", "导航与采集动作已关闭。", "在 Mod 配置中开启 maidAgentEnabled。"))
        if data.get("chat_bubble_enabled", True) or data.get("chat_box_enabled", True):
            checks.append(_check("ok", "聊天显示", "聊天气泡或聊天框至少启用了一项。"))
        else:
            checks.append(_check(
                "warning",
                "聊天显示",
                "聊天气泡和聊天框都已关闭，mc_send_chat 不会在游戏画面显示文本。",
                "需要游戏内可见文字时，开启 chatBubbleEnabled 或 chatBoxEnabled。",
            ))
        if data.get("command_execution_enabled", False):
            checks.append(_check("ok", "指令执行", "Minecraft 指令执行已启用，仍需玩家确认。"))
        else:
            checks.append(_check("info", "指令执行", "Minecraft 指令执行未启用；这是默认安全设置。"))
    else:
        detail = f"读取配置失败：{config_result.get('data', {})}"
        if status_ok:
            checks.append(_check(
                "info",
                "mod 配置",
                f"{detail}；但女仆状态查询正常，核心控制链路可用。",
                "这通常只影响诊断展示指令执行/聊天显示配置。若需要该项正常，确认游戏内 mod 是最新构建并重启 Minecraft。",
            ))
            return
        checks.append(_check(
            "warning",
            "mod 配置",
            detail,
            "如果其它功能也失败，可能是 mod 端 handler 未正常响应。",
        ))


def _append_maid_status_checks(plugin, checks, status_result):
    if status_result.get("type") == "error":
        checks.append(_check(
            "warning",
            "女仆状态",
            f"读取女仆状态失败：{status_result.get('data', {})}",
            "确认车万女仆模组已安装，且当前世界中存在女仆。",
        ))
        return False, []

    maids = status_result.get("data", {}).get("maids", [])
    if maids:
        checks.append(_check("ok", "女仆状态", f"检测到 {len(maids)} 个女仆。"))
        plugin._maid_status_cache = {m.get("id", ""): m for m in maids if m.get("id")}
    else:
        checks.append(_check(
            "warning",
            "女仆状态",
            "已连接但未检测到女仆。",
            "确认世界中存在车万女仆实体；如果无此模组，本插件的控制能力无实际对象。",
        ))
    return True, maids


def _overall_status(checks):
    statuses = {c.get("status") for c in checks}
    if "error" in statuses:
        return "error"
    if "warning" in statuses:
        return "warning"
    return "ok"


def _summary(status):
    return {
        "ok": "桥接诊断通过。",
        "warning": "桥接可用但存在需要注意的配置或状态。",
        "error": "桥接存在阻断问题，需要先处理错误项。",
    }.get(status, "桥接诊断完成。")
