"""Small, account-safe answers for N.E.K.O conversation tools."""


def balance_answer(state):
    config = state.get("config") or {}
    source = config.get("source")
    model = config.get("model") or "当前模型"
    a = state.get('allowance')
    if isinstance(a, dict) and a.get('schema_version') == 1:
        if a.get('value') is None:
            return f"暂时读不到 {model} 的猫粮额度（{a['status_label']}），不会把未知额度当作零喵～"
        prefix = '上次记录' if a['status'] in ('stale', 'conflict') else '手动记录' if a['status'] == 'manual' else '当前读数'
        label = '已上报用量' if a['metric'] == 'usage_total' else '预算剩余' if a['metric'] == 'budget_remaining' else '猫粮余额'
        return f"{model} 的{label}：{a['value']} {a['unit']}（{prefix}；{a['basis']}）喵～"

    if source == "desktop_quota" and config.get("quota_mode", "auto") == "auto":
        row = state.get("account_quota") or {}
        if row.get("remaining") is None:
            return f"暂时读不到 {model} 的猫粮积分喵～请打开应用的积分用量页，并检查猫粮额度读取组件的辅助功能权限；也可在账户设置中切换手动校正。"
        prefix = "上次记录的" if state.get("stale") else "本机面板显示"
        suffix = "；本次读取失败，不能当作当前额度" if state.get("stale") else "；以应用最新账单为准"
        return f"{prefix} {model} 猫粮余额：{row['remaining']:g} 积分{suffix}喵～"

    if source == "manual_quota" or source == "desktop_quota":
        remaining = config.get("quota_remaining")
        if remaining is None:
            return "猫粮的手动额度还没填写喵～打开猫粮监测功能，在设置里填写后就能查看。"
        return f"{model} 的猫粮余额是 {remaining:g} {config.get('quota_unit') or '次'}（手动记录，不会自动扣减或重置）喵～"

    if source == "jev":
        usage = state.get("reported_usage") or {}
        count = (usage.get("input_tokens") or 0) + (usage.get("output_tokens") or 0)
        return f"JEV 已记录使用 {count:,} token；它只上报用量，没有提供剩余额度喵～"

    if source == "codex":
        windows = (state.get("codex_quota") or {}).get("windows") or []
        if not windows:
            return "暂时读不到 Codex 猫粮额度喵～请检查本机 Codex 登录状态后再试。"
        details = "，".join(f"{window['label']}剩余 {window['remaining']:g}%" for window in windows)
        prefix = "上次记录的" if state.get("stale") else "当前"
        suffix = "；本次查询未成功，不能当作实时额度" if state.get("stale") else ""
        return f"{prefix} Codex 猫粮余额：{details}{suffix}喵～"

    row = state.get("selected")
    if row:
        prefix = "上次记录的" if state.get("stale") else "当前"
        suffix = "；本次查询未成功，不能当作实时余额" if state.get("stale") else ""
        basis = state.get("basis") or "账户余额"
        return f"{prefix}{model} 猫粮余额是 {row['balance']} {row['currency']}（{basis}）{suffix}喵～"
    if not state.get("configured"):
        return "猫粮还没有配置余额来源喵～说“查看猫粮监测功能”打开挂件后，在设置里选择模型。"
    return f"暂时读不到 {model} 的猫粮余额喵～请打开猫粮监测功能检查账户设置。"
