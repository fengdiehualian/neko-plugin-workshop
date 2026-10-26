"""UI 上下文数据辅助方法。"""

from typing import Any, Dict

from plugin.sdk.plugin import ui


class UiContextMixin:
    _agent: Any
    _config: Dict[str, Any]

    @ui.context(id="dashboard", title="泰拉瑞亚控制面板")
    def ctx_dashboard(self) -> Dict[str, Any]:
        # 精简版：与 get_dashboard_state 对齐，不再暴露血量/魔力/背包/箱子等
        # 前端无用数据（后端 _state/_inv_full 缓存仍由事件推送维护，AI 行为不受影响）
        ctx = {
            "connected": self._agent.running,
            "character_name": self._config.get("character_name", "Neko"),
            "current_goal": self._agent.current_goal,
            "mods": self._agent.registry.mod_list(),
            "log": self._agent.get_log_sync(),
            "task": self._agent.executor.current(),
            "task_last": self._agent.executor.last_result(),
            "longterm": self._agent.longterm.active(),
            "doing": self._agent.coordinator.say(),
            "thinking": self._thinking(),
        }
        # v3.0: 第一人称身份+能力引导（参照 vr_neko_cat ai_guidance 模式）
        try:
            from ..core.context import build_ai_guidance
            ctx["ai_guidance"] = build_ai_guidance()
        except Exception:
            pass
        return ctx

    def _thinking(self) -> Dict[str, Any]:
        # 最近一次思考过程，让主人看得见猫娘在想什么
        a = self._agent.brain.last_assessment()
        if a is None:
            return {}
        return {"doable": a.doable, "say": a.say(),
                "thoughts": a.thoughts, "fixes": a.fixes,
                "need": a.need_from_owner}

    @ui.context(id="guide", title="游玩指南")
    def ctx_guide(self) -> Dict[str, Any]:
        return {
            "guide_text": "用语音或聊天告诉猫娘：去挖铁矿、给我、穿上装备。",
            "connected": self._agent.running,
            "mods": self._agent.registry.mod_list(),
        }
