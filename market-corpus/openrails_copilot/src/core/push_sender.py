"""推送路由：把告警按 EventSpec.route 送到正确通道。

三条通道（由 host 侧 ``delivery_mode`` 决定，见
``plugin/server/messaging/proactive_bridge.py:48-53``）：

===============  ==================  ==========================================
route/behavior   delivery_mode       宿主行为
===============  ==================  ==========================================
``read``         ``passive``         **不打断**，进被动队列，等下一次用户轮次带走。
                                     不占每会话 2 次的主动分析额度。
``respond``      ``proactive``       交节流管理器（优先级/合并/播放门），会让角色
                                     自发起口 → 那一轮 ``had_user_input=False``
                                     → **占**每会话 2 次额度。
``blind``        ``silent``          只渲染给用户看（chat/HUD），**不进 LLM**。
===============  ==================  ==========================================

为什么 ``coalesce_key`` 必须给
----------------------------
合并是 **opt-in** 的：key 为空 ⇒ 宿主用 ``__uniq:N`` 占位 ⇒ **永不合并**，
只能一路堆到队列上限再丢最旧的。OR 源码（``main_logic/core/proactive.py:3353-3361``）
专门为"高频 read 流"写过这段去重，没有 key 就等于放弃它。

本插件按 **event_id** 分组（不是按类别）——比类别更细，保证"不同告警不会互相顶掉"，
同时"同一条告警的重复快照"会折叠成最新的一份。
"""

from __future__ import annotations

from typing import Any

from .event_catalog import ROUTE_RESPOND, EventSpec

#: 心跳/状态快照的合并键。这是"快照"语义：新的顶掉队列里旧的。
COALESCE_STATUS = "openrails:status"


def _coalesce_for(spec: EventSpec) -> str:
    return f"openrails:{spec.event_id}"


class PushSender:
    """封装宿主的 push_message 通道。"""

    def __init__(self, plugin: Any, *, dry_run: bool = False) -> None:
        self.plugin = plugin
        self.dry_run = dry_run

    @property
    def logger(self) -> Any:
        return self.plugin.logger

    # ── 告警 ───────────────────────────────────────────────────────────
    async def send_alert(self, spec: EventSpec, text: str, *, target_lanlan: str = "") -> bool:
        """按 spec 决定的路由投一条告警。返回是否被宿主接受（仅表示本地提交成功）。

        ⚠ ``push_message`` 的返回值只代表**本地提交**是否被接受，不代表用户/LLM
        真的消费了。它能识别的是 ``submitted=False`` 携带的 backpressure /
        transport_unavailable / payload_too_large —— 这三类是"换时机重发有意义"的。
        """
        behavior = ROUTE_RESPOND if spec.route == ROUTE_RESPOND else "read"
        kwargs: dict[str, Any] = {
            "source": "openrails_copilot",
            "visibility": [],               # 告警原文不直接铺给用户；措辞交给角色人设
            "ai_behavior": behavior,
            "parts": [{"type": "text", "text": text}],
            "priority": spec.priority,
            "coalesce_key": _coalesce_for(spec),
        }
        if target_lanlan:
            kwargs["target_lanlan"] = target_lanlan

        if self.dry_run:
            self.logger.info(
                "[dry_run] %s route=%s priority=%s preempt=%s :: %s",
                spec.event_id, spec.route, spec.priority, spec.preempt, text,
            )
            return True

        try:
            result = self.plugin.push_message(**kwargs)
        except Exception as exc:                    # 通道异常不能让监控循环挂掉
            self.logger.warning("push failed for %s: %s: %s", spec.event_id, type(exc).__name__, exc)
            return False
        if not isinstance(result, dict) or not result.get("submitted"):
            self.logger.warning(
                "push not submitted for %s: %s", spec.event_id,
                (result or {}).get("reason") if isinstance(result, dict) else result,
            )
            return False
        return True

    # ── 周期状态快照 ───────────────────────────────────────────────────
    async def send_status(self, text: str, *, priority: int = 1, target_lanlan: str = "") -> bool:
        """一条"一切正常"的周期快照。

        用 ``read`` + 固定 coalesce_key：队列里永远只留最新的一张，不会堆积。
        """
        kwargs: dict[str, Any] = {
            "source": "openrails_copilot",
            "visibility": [],
            "ai_behavior": "read",
            "parts": [{"type": "text", "text": text}],
            "priority": priority,
            "coalesce_key": COALESCE_STATUS,
        }
        if target_lanlan:
            kwargs["target_lanlan"] = target_lanlan
        if self.dry_run:
            self.logger.info("[dry_run] status :: %s", text)
            return True
        try:
            result = self.plugin.push_message(**kwargs)
        except Exception as exc:
            self.logger.warning("status push failed: %s: %s", type(exc).__name__, exc)
            return False
        return bool(isinstance(result, dict) and result.get("submitted"))

    # ── 直出气泡（不进 LLM）────────────────────────────────────────────
    async def push_direct(self, text: str, *, target_lanlan: str = "") -> bool:
        """``blind`` + ``visibility=["chat"]``：只在聊天框显示，LLM 完全看不到。

        高频播报（一声"十辆""五辆"之类）走这条，既不烧额度也不进上下文。
        """
        kwargs: dict[str, Any] = {
            "source": "openrails_copilot",
            "visibility": ["chat"],
            "ai_behavior": "blind",
            "parts": [{"type": "text", "text": text}],
            "priority": 5,
        }
        if target_lanlan:
            kwargs["target_lanlan"] = target_lanlan
        if self.dry_run:
            self.logger.info("[dry_run] direct :: %s", text)
            return True
        try:
            result = self.plugin.push_message(**kwargs)
        except Exception as exc:
            self.logger.warning("direct push failed: %s: %s", type(exc).__name__, exc)
            return False
        return bool(isinstance(result, dict) and result.get("submitted"))
