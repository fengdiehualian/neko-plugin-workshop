from __future__ import annotations

from typing import Any

from .pipeline_models import QQReplyDecision, QQReplyRequest


class QQReplyDecisionNode:
    def __init__(self, plugin: Any):
        self.plugin = plugin

    def decide(self, request: QQReplyRequest) -> QQReplyDecision:
        if request.force_reply:
            permission_level = str(request.permission_level_override or "trusted")
            return QQReplyDecision(action="reply", permission_level=permission_level)
        if request.permission_level_override:
            return QQReplyDecision(action="reply", permission_level=str(request.permission_level_override))
        if request.is_group:
            return self._decide_group(request)
        return self._decide_private(request)

    def _attention_state(self, group_id: str) -> dict[str, Any]:
        service = getattr(self.plugin, "attention_service", None)
        if not service:
            return {"enabled": False, "group_score": 0.0, "focus_group_id": "", "focus_score": 0.0, "focus_reason": "", "multiplier": 1.0}
        snapshot = service.get_snapshot()
        group_state = service.get_state(group_id)
        multiplier = service.get_group_multiplier(group_id)
        return {
            "enabled": bool(snapshot.get("enabled", False)),
            "group_score": float(getattr(group_state, "attention_score", 0.0) or 0.0),
            "focus_group_id": str(snapshot.get("focus_group_id") or ""),
            "focus_score": float(snapshot.get("focus_score") or 0.0),
            "focus_reason": str(snapshot.get("focus_reason") or ""),
            "multiplier": float(multiplier or 1.0),
        }

    def _decision_kwargs(self, request: QQReplyRequest, group_level: str, attention: dict[str, Any]) -> dict[str, Any]:
        return {
            "permission_level": group_level,
            "attention_enabled": attention["enabled"],
            "attention_score": attention["group_score"],
            "attention_focus_group_id": attention["focus_group_id"],
            "attention_focus_score": attention["focus_score"],
            "attention_multiplier": attention["multiplier"],
        }

    def _decide_private(self, request: QQReplyRequest) -> QQReplyDecision:
        """私聊一律回复。

        私聊里不存在「@ 才算叫我」这回事——对方就是在跟你单聊。历史上这里按
        ``normal`` 级走 relay（转发给主人）、``none`` 级直接忽略，结果是普通用户
        私聊石沉大海；已按用户口径收敛为「一律回复」，只把**真实权限级别**带下去
        给下游用（工具权限、记忆作用域仍按级别区分）。
        """
        permission_level = self.plugin.permission_mgr.get_permission_level(request.sender_id) if self.plugin.permission_mgr else "none"
        return QQReplyDecision(action="reply", permission_level=permission_level)

    def _decide_group(self, request: QQReplyRequest) -> QQReplyDecision:
        group_id = str(request.group_id or "").strip()
        attention = self._attention_state(group_id)

        # 群聊判定只有这一条路（策略模式已合并并删除，见 SESSION-HANDOFF §13/§24）：
        # 注意力门控已在 dispatcher 层处理，此处补充群权限门控。
        #
        # 即使注意力门控已放行，仍需校验群是否在权限列表中（阻止未配置/已移除的群绕过）
        group_level = self.plugin.group_permission_mgr.get_group_level(group_id) if self.plugin.group_permission_mgr else "none"
        if group_level == "none":
            return QQReplyDecision(action="ignore", **self._decision_kwargs(request, group_level, attention), attention_gate_reason="permission_none")
        if group_level == "normal":
            # normal 群：**只在被 @ 或引用她时回**；其余消息按概率转发给主人。
            if not (request.is_at_bot or request.is_reply_to_bot):
                relay_probability = self.plugin.group_permission_mgr.get_normal_relay_probability(group_id) if self.plugin.group_permission_mgr else None
                return QQReplyDecision(action="relay", relay_probability=relay_probability, **self._decision_kwargs(request, group_level, attention), attention_gate_reason="relay")
            kwargs = self._decision_kwargs(request, group_level, attention)
            kwargs["attention_gate_reason"] = "normal_at_bot"
            return QQReplyDecision(action="reply", **kwargs)
        kwargs = self._decision_kwargs(request, group_level, attention)
        kwargs["attention_gate_reason"] = "attention_gate"
        return QQReplyDecision(action="reply", **kwargs)
