from __future__ import annotations

import asyncio
import re
import time
from contextvars import ContextVar
from typing import Any

from . import addressing
from .pipeline_models import (
    BUFFER_INTERNAL_SOURCE_KINDS,
    QQDeliveryResult,
    QQModelResult,
    QQPipelineStageTrace,
    QQRelayResult,
    QQReplyContext,
    QQReplyDecision,
    QQReplyOutcome,
    QQReplyRequest,
    backlog_sender_label,
    delivered_blocks_text,
)
from .reply_buffer_service import QQReplyBufferService

#: 这一轮"结局是什么、为什么没投递"——`run()` 最后那行 `[Reply] … 结局: …` 从这里取。
#:
#: 为什么用 ContextVar 而不是 `self._note`：同一个 pipeline 实例**并发**处理多个会话
#: （群与私聊同时在跑、缓冲汇总还会再进一次），挂在实例上会串台。
#:
#: 为什么要有它：这一层以前只在**成功**那条路上有日志（`[Buffer] 排定投递`），
#: "生成了却没发出去"的几条出口（空回复、缓冲忽略、没有可投递的块）**一行都不打** ——
#: 真机 2026-09-29 15:28 就是这样：她对一句提问生成了 24 字回复，然后既没投递、也没日志，
#: 事后无法定性（见 docs/SESSION-HANDOFF.md §39）。
_OUTCOME_NOTE: ContextVar[str] = ContextVar("qq_reply_outcome_note", default="")


def _note_outcome(note: str) -> None:
    """记下这一轮的结局说明（唯一出口日志会打出来）。"""
    _OUTCOME_NOTE.set(note)


class QQReplyPipelineRunner:
    def __init__(self, plugin: Any):
        self.plugin = plugin

    def _abandon_placeholder(self, request: QQReplyRequest, action: str) -> None:
        """非回复结局（ignore/relay）后，摘掉 ``pre_buffer`` 留下的未填充占位。

        这类结局不会调 ``schedule_reply``，占位就永远填不上；留着它会挡住后续
        消息——那些消息被追加进占位、跳过自己的 pipeline，而总结轮的判定判据
        与本次一模一样，于是同样不回复。摘掉后下一条消息能重新独立判定。
        详见 ``QQReplyBufferService.abandon_unfilled_placeholder``。"""
        buffer_service = getattr(self.plugin, "reply_buffer_service", None)
        if buffer_service is None:
            return
        session_key = self.plugin._build_session_key(
            sender_id=request.sender_id,
            is_group=bool(request.is_group),
            group_id=request.group_id if request.is_group else None,
        )
        if buffer_service.abandon_unfilled_placeholder(session_key):
            self.plugin._emit_log(
                "DEBUG",
                f"[Buffer] {action} 结局，摘除未填充的预缓冲占位: {session_key}",
            )

    async def run(self, request: QQReplyRequest) -> QQReplyOutcome:
        """**唯一出口**：这一轮不管什么结局，最后都打一行 `[Reply] … 结局: …`。

        以前只有成功那条路有日志（`[Buffer] 排定投递` / `[Send]`），而"生成了却没发出去"
        的出口一行都不打 —— 事后看到的是"她没有后续"，查不出是没接、答了没发、还是发了
        没收到。现在每一轮都有一行，`_note_outcome()` 记的原因跟着它出来。
        """
        token = _OUTCOME_NOTE.set("")
        try:
            outcome = await self._run_pipeline(request)
        except Exception as exc:
            # 异常也是结局：先记一行再往上抛（栈照旧由调用方/上层记录）。
            self._log_outcome(request, delivered=False, note=f"中断：{type(exc).__name__}: {exc}")
            raise
        else:
            delivered = bool(
                getattr(getattr(outcome, "delivery_result", None), "delivered", False)
            )
            note = _OUTCOME_NOTE.get()
            if not note:
                note = "已投递" if delivered else "未投递（这一条出口没标注原因，见上面的日志）"
            self._log_outcome(request, delivered=delivered, note=note)
            return outcome
        finally:
            _OUTCOME_NOTE.reset(token)

    def _log_outcome(self, request: QQReplyRequest, *, delivered: bool, note: str) -> None:
        """这一轮的结局，一行说完（群与私聊都打，目标 id 一眼可辨）。"""
        try:
            label = self.plugin._build_session_key(
                sender_id=request.sender_id,
                is_group=request.is_group,
                group_id=request.group_id if request.is_group else None,
            )
        except Exception:
            label = f"group:{getattr(request, 'group_id', '')}" if request.is_group else f"private:{request.sender_id}"
        self.plugin.logger.info(
            f"[Reply] {label} 结局: {'已投递' if delivered else '未投递'}（{note}）"
        )

    async def _run_pipeline(self, request: QQReplyRequest) -> QQReplyOutcome:
        decision = self._run_decision(request)
        decision_trace = QQPipelineStageTrace(
            stage="decision",
            status=decision.action,
            metadata={
                "permission_level": decision.permission_level,
                "is_group": request.is_group,
                "group_id": str(request.group_id or ""),
                "sender_id": request.sender_id,
                "group_scene_mode": request.group_scene_mode,
                "quoted_message_id": request.quoted_message_id,
                "mentioned_user_ids": list(request.mentioned_user_ids or []),
                "attention_enabled": decision.attention_enabled,
                "attention_score": decision.attention_score,
                "attention_focus_group_id": decision.attention_focus_group_id,
                "attention_focus_score": decision.attention_focus_score,
                "attention_multiplier": decision.attention_multiplier,
                "attention_gate_reason": decision.attention_gate_reason,
            },
        )
        if decision.action == "ignore":
            _note_outcome(f"决策=ignore（不接这条：{decision.attention_gate_reason or '未给原因'}）")
            self._abandon_placeholder(request, decision.action)
            return QQReplyOutcome(action="ignore", traces=[decision_trace])
        if decision.action == "relay":
            outcome = await self._run_relay(request, decision, decision_trace)
            _note_outcome("决策=relay（转交给转发链，不走本会话回复）")
            self._abandon_placeholder(request, decision.action)
            return outcome

        context = await self._run_context(request, decision)
        model_result = await self._run_model(context)
        outcome = await self._run_postprocess(context, model_result)
        outcome.history_ai_row = model_result.history_ai_row
        outcome.traces.extend([
            decision_trace,
            *context.traces,
            QQPipelineStageTrace(
                stage="context",
                status="built",
                metadata={
                    "permission_level": context.permission_level,
                    "is_group": context.is_group,
                    "group_id": str(context.group_id or ""),
                    "memory_context_used": context.memory_context_used,
                    "persist_memory": context.persist_memory,
                    "scene_mode": context.scene_mode,
                    "group_scene_mode": context.group_scene_mode,
                    "core_memory_length": len(context.core_memory_text),
                    "recalled_memory_length": len(context.recalled_memory_text),
                },
            ),
            *model_result.traces,
            QQPipelineStageTrace(
                stage="model",
                status=model_result.source,
                metadata={
                    "used_fallback": model_result.used_fallback,
                    "timed_out": model_result.timed_out,
                    "allow_fallback": model_result.allow_fallback,
                    "fallback_reason": model_result.fallback_reason,
                    "reply_length": len(model_result.reply_text or ""),
                },
            ),
            QQPipelineStageTrace(
                stage="postprocess",
                status="default" if outcome.used_default_message else ("reply" if outcome.reply_text else "empty"),
                metadata={
                    "reply_length": len(outcome.reply_text or ""),
                    "used_default_message": outcome.used_default_message,
                },
            ),
        ])

        # poke/sticker/record/ark 已统一为 <msg> 块，由 reply_delivery_node 处理
        outcome.delivery_plan = self._build_delivery_plan(request, outcome)
        outcome.delivery_result = await self._run_delivery(outcome.delivery_plan, request, outcome, context=context)
        outcome.traces.append(
            QQPipelineStageTrace(
                stage="delivery",
                status="delivered" if outcome.delivery_result and outcome.delivery_result.delivered else "skipped",
                metadata={
                    "target_type": getattr(outcome.delivery_plan, "target_type", ""),
                    "target_id": getattr(outcome.delivery_plan, "target_id", ""),
                    "reply_message_id": getattr(outcome.delivery_plan, "reply_message_id", ""),
                    "at_user_id": getattr(outcome.delivery_plan, "at_user_id", ""),
                },
            )
        )
        return outcome

    def _run_decision(self, request: QQReplyRequest) -> QQReplyDecision:
        return self.plugin.reply_decision_node.decide(request)

    async def _run_relay(self, request: QQReplyRequest, decision: QQReplyDecision, decision_trace: QQPipelineStageTrace) -> QQReplyOutcome:
        outcome = QQReplyOutcome(action="relay", traces=[decision_trace])
        outcome.relay_plan = self.plugin.reply_relay_node.build_plan(
            message_text=request.message_text,
            sender_id=request.sender_id,
            source_type="group" if request.is_group else "private",
            source_id=request.group_id or request.sender_id,
            relay_probability=decision.relay_probability,
        )
        outcome.traces.append(
            QQPipelineStageTrace(
                stage="relay_plan",
                status="built" if outcome.relay_plan else "skipped",
                metadata={
                    "source_type": "group" if request.is_group else "private",
                    "source_id": str(request.group_id or request.sender_id),
                    "relay_probability": decision.relay_probability,
                },
            )
        )
        outcome.relay_result = await self._run_relay_delivery(outcome.relay_plan)
        outcome.traces.append(
            QQPipelineStageTrace(
                stage="relay_delivery",
                status="relayed" if outcome.relay_result and outcome.relay_result.relayed else "skipped",
                metadata={
                    "source_type": getattr(outcome.relay_plan, "source_type", ""),
                    "source_id": getattr(outcome.relay_plan, "source_id", ""),
                },
            )
        )
        return outcome

    async def _run_context(self, request: QQReplyRequest, decision: QQReplyDecision) -> QQReplyContext:
        return await self.plugin.reply_context_node.build(
            message=request.message_text,
            permission_level=(
                getattr(request, "private_permission_level_at_receipt", None)
                if (
                    not request.is_group
                    and getattr(
                        request, "private_permission_level_at_receipt", None,
                    ) is not None
                )
                else decision.permission_level
            ),
            sender_id=request.sender_id,
            attachments=request.attachments,
            is_group=request.is_group,
            group_id=request.group_id,
            user_nickname=request.user_nickname,
            use_memory_context=request.use_memory_context,
            persist_memory=request.persist_memory,
            ephemeral_session=request.ephemeral_session,
            group_facing=request.group_facing,
            group_scene_mode=request.group_scene_mode,
            current_message_id=request.current_message_id,
            is_reply_to_bot=getattr(request, "is_reply_to_bot", False),
            quoted_message_id=getattr(request, "quoted_message_id", "") or "",
            mentions_other_user=getattr(request, "mentions_other_user", False),
            mentions_all=getattr(request, "mentions_all", False),
            # 「谁在跟谁说话」的结论（门控算好的那份）。请求里没有结论时它是 None，
            # 提示词层据此退回改动前的 6 个标签 —— 见 `addressing.verdict_from_request`。
            addressee=addressing.verdict_from_request(request),
            reply_context=getattr(request, "reply_context", "") or "",
            force_reply=request.force_reply,
            source_kind=getattr(request, "source_kind", ""),
            member_memory_at_receipt=getattr(
                request, "member_memory_at_receipt", None,
            ),
            group_speaker_permission_level_at_receipt=getattr(
                request, "group_speaker_permission_level_at_receipt", None,
            ),
            speaker_channel_at_receipt=getattr(
                request, "speaker_channel_at_receipt", None,
            ),
            participant_memory_at_receipt=getattr(
                request, "participant_memory_at_receipt", None,
            ),
            private_permission_level_at_receipt=getattr(
                request, "private_permission_level_at_receipt", None,
            ),
            inherited_consent_snapshot=getattr(
                request, "inherited_consent_snapshot", None,
            ),
        )

    async def _run_model(self, context: QQReplyContext) -> QQModelResult:
        return await self.plugin.reply_model_node.generate(context)

    async def _run_postprocess(self, context: QQReplyContext, model_result: QQModelResult) -> QQReplyOutcome:
        return await self.plugin.reply_postprocess_node.finalize(context, model_result)

    def _build_delivery_plan(self, request: QQReplyRequest, outcome: QQReplyOutcome):
        return self.plugin.reply_postprocess_node.build_delivery_plan(request, outcome)

    async def _send_ark(self, request: QQReplyRequest, outcome: QQReplyOutcome) -> bool:
        """发送 Ark 卡片消息"""
        ark = outcome.parsed_ark
        title = ark.get("title", "")
        desc = ark.get("desc", "")
        pic = ark.get("pic", "")
        btn = ark.get("btn", "")
        url = ark.get("url", "")
        body_text = ark.get("_body", "")

        # 构建 ark payload
        ark_obj: dict[str, Any] = {"msg_type": 10}
        if title:
            ark_obj["ark"] = {
                "template_id": 37,
                "kv": [
                    {"key": "#PROMPT#", "value": body_text or title},
                    {"key": "#TITLE#", "value": title},
                    {"key": "#DESC#", "value": desc or body_text},
                ]
            }
            if pic:
                ark_obj["ark"]["kv"].append({"key": "#IMGPATH#", "value": pic})
        else:
            ark_obj["ark"] = {
                "template_id": 23,
                "kv": [
                    {"key": "#TITLE#", "value": body_text or "卡片"},
                    {"key": "#DESC#", "value": desc},
                ]
            }
            if pic:
                ark_obj["ark"]["kv"].append({"key": "#IMG#", "value": pic})

        if btn:
            ark_obj["ark"]["kv"].append({"key": "#SUBTITLE#", "value": btn})
        if url:
            ark_obj["ark"]["kv"].append({"key": "#URL#", "value": url})

        if not getattr(self.plugin.qq_client, "supports_ark_cards", False):
            # OneBot 后端不支持 Ark 卡片，降级为文本发送
            fallback = body_text or title or desc or ""
            if fallback:
                await self.plugin._deliver_group_reply(
                    str(request.group_id or ""),
                    fallback,
                    reply_message_id="",
                    at_user_id="",
                    fallback_to_text_on_voice_failure=True,
                )
                return True
            return False
        try:
            return await self.plugin.qq_client.send_group_ark_card(
                str(request.group_id or ""),
                ark_obj,
            )
        except Exception as e:
            self.plugin.logger.warning(f"[Ark] 发送失败: {e}")
            return False

    #: 合并转发单次最多带多少条原文。**这是防炸的安全阀，不是产品上限** ——
    #: 一个群在 backlog 里能留 200 条，把几小时的闲聊整段抛出去既刷屏也没人看。
    FORWARD_MAX_NODES = 50

    async def _handle_forward_marks(self, request, outcome) -> None:
        """处理 `<mark/>` 与 `<forward>`（合并转发）。

        - `<mark/>`：把「起点」落盘。提示词说「感觉话题会有意思/可能吵起来时打个标记，
          后续 `<forward>` 只转发标记之后的对话」——标记与转发往往隔几十条消息，
          所以起点必须持久化，不能只留在内存里。
        - `<forward to="X">一句总结</forward>`：取标记之后的**多人多句**原文，
          连同一句总结组装成合并转发发出去，然后**清掉标记**（同一个标记不能复用，
          否则下次转发会把早就发过的对话再抛一遍）。

        `to` 的解析：空 = 当前群；命中已知群号 = 发到那个群；否则当作 QQ 号私聊转发
        （提示词里的例子正是「赢了用 `<forward to="管理员QQ">` 炫耀」）。
        """
        if request is None or not getattr(request, "is_group", False):
            return
        group_id = str(getattr(request, "group_id", "") or "").strip()
        if not group_id:
            return
        store = getattr(self.plugin, "backlog_store", None)
        if store is None:
            return

        if getattr(outcome, "forward_mark", False):
            now = int(
                self.plugin.attention_service._current_time()
                if getattr(self.plugin, "attention_service", None)
                else time.time()
            )
            await store.set_forward_mark(
                group_id,
                message_id=str(getattr(request, "current_message_id", "") or ""),
                timestamp=now,
            )
            self.plugin._emit_log("INFO", f"[Forward] 群{group_id} 已标记转发起点")
            return

        summary = str(getattr(outcome, "forward_content", "") or "").strip()
        if not summary:
            return

        mark = await store.get_forward_mark(group_id)
        if not isinstance(mark, dict):
            # 没有起点就没有"标记之后的对话"这个集合。宁可什么都不发，也不要把
            # 整个 backlog 抛出去 —— 提示词要求先打标记，这里把原因说清楚。
            self.plugin._emit_log(
                "WARNING",
                f"[Forward] 群{group_id} 收到转发但没有 <mark/> 起点，跳过",
            )
            return

        since = int(mark.get("timestamp") or 0)
        anchor_id = str(mark.get("message_id") or "")
        timeline = await store.get_recent_group_messages(group_id, limit=0)
        picked = [
            item for item in timeline
            if int(item.get("timestamp") or 0) > since
            and str(item.get("message_id") or "") != anchor_id
        ]
        if len(picked) > self.FORWARD_MAX_NODES:
            picked = picked[-self.FORWARD_MAX_NODES:]
        if not picked:
            self.plugin._emit_log(
                "INFO", f"[Forward] 群{group_id} 标记之后没有新消息，跳过",
            )
            await store.clear_forward_mark(group_id)
            return

        target = str(getattr(outcome, "forward_target", "") or "").strip()
        if target:
            known_groups = set()
            mgr = getattr(self.plugin, "group_permission_mgr", None)
            if mgr is not None:
                known_groups = {
                    str(g.get("group_id") or "").strip()
                    for g in (mgr.list_groups() or [])
                }
            target_type = "group" if target in known_groups else "private"
            target_id = target
        else:
            target_type, target_id = "group", group_id

        nodes = self.plugin.reply_delivery_node.build_forward_nodes(
            picked,
            summary=summary,
            bot_name=str(getattr(self.plugin, "_bot_nickname", "") or ""),
            bot_uin=str(getattr(self.plugin.qq_client, "self_id", "") or ""),
        )
        sent = await self.plugin.reply_delivery_node.send_forward(
            target_type=target_type, target_id=target_id, nodes=nodes,
        )
        if sent:
            self.plugin._emit_log(
                "INFO",
                f"[Forward] 已合并转发 {len(nodes)} 条到 {target_type}:{target_id}",
            )
            await store.clear_forward_mark(group_id)
            await self._record_forward_in_memory(
                source_group_id=group_id,
                target_type=target_type,
                target_id=target_id,
                summary=summary,
                forwarded=picked,
            )
        else:
            # 失败**不清标记**：起点还在，下一轮还能重试（清掉就永远补不上了）。
            self.plugin._emit_log("WARNING", f"[Forward] 群{group_id} 合并转发未确认")

    #: 转发记录里最多带多少条原文 / 多少字符。转发是把**别人的话**搬进接收方的记忆域，
    #: 必须有界：一次转发可以有 50 个节点，整段塞进去会把那个域淹掉。
    FORWARD_MEMORY_MAX_LINES = 20
    FORWARD_MEMORY_MAX_CHARS = 1200

    def _forward_memory_channel(self, target_type: str, target_id: str):
        """转发记录该写进哪个回忆域（跟着既有的 opt-in 门控走，不另开一套判据）。

        判据与读路径同源（`reply_context_node` 的 `private_memory_mode`）：

        * 群 → `group_chat` 域（需 `group_memory_enabled`）
        * 私聊**管理员** → 主人的 legacy 私有语料（走 `/cache`，与他和猫娘的私聊记忆
          同一个域，所以他一问就能召回）
        * 私聊好友 → 对方 `participant` 域（需 `private_participant_memory_enabled`）
        * 其余（开关关着 / 非信任对象）→ 不写

        返回 `("legacy", None)` / `("scoped", subject)` / `None`。
        """
        settings = getattr(self.plugin, "_qq_settings", {}) or {}
        target_id = str(target_id or "").strip()
        if not target_id:
            return None
        if target_type == "group":
            if not bool(settings.get("group_memory_enabled", False)):
                return None
            return ("scoped", self.plugin.memory_bridge.group_subject(target_id))
        mgr = getattr(self.plugin, "permission_mgr", None)
        level = mgr.get_permission_level(target_id) if mgr is not None else "none"
        if level == "admin":
            return ("legacy", None)
        if bool(settings.get("private_participant_memory_enabled", False)):
            return ("scoped", self.plugin.memory_bridge.participant_subject(target_id))
        return None

    @classmethod
    def _forward_memory_text(
        cls, *, source_group_id: str, target_label: str, summary: str,
        forwarded: list[dict], for_source_group: bool = False,
    ) -> str:
        """组装转发记录。

        **必须明确标注这是转发来的**（使用者的要求）：接收方的记忆域里会出现群友说的话，
        不标注的话提取器会把它当成猫娘自己说的、或当成这个域的成员说的。所以这里写死
        一段「以下原文来自群 X，是群友说的话，不是我说的」，并且逐行带发言人。
        """
        head = f"【转发记录】我把群 {source_group_id} 的一段群聊合并转发给了 {target_label}。"
        if summary:
            head += f"转发时我写了一句总结：{summary}"
        if for_source_group:
            # 源群那侧不需要附原文 —— 那些话本来就是这里的 human 行。
            return head

        lines: list[str] = []
        used = 0
        for item in forwarded:
            name = backlog_sender_label(item)
            sid = str(item.get("sender_id") or "")
            body = str(item.get("text") or item.get("message_text") or "").strip()
            if not body:
                continue
            line = f"{name}({sid}): {body}" if sid else f"{name}: {body}"
            if (
                len(lines) >= cls.FORWARD_MEMORY_MAX_LINES
                or used + len(line) > cls.FORWARD_MEMORY_MAX_CHARS
            ):
                lines.append(f"…（其余 {max(0, len(forwarded) - len(lines))} 条未记入）")
                break
            lines.append(line)
            used += len(line)
        if not lines:
            return head
        return (
            head
            + f"\n——以下原文来自群 {source_group_id}，是**群友说的话，不是我说的**，"
            f"我只是把它转发了出去——\n"
            + "\n".join(lines)
        )

    def _resolve_her_name(self, source_group_id: str) -> str:
        """角色名（记忆按它分库）。

        取法与既有写路径一致，**三级回退**：本群会话的 `her_name` → 宿主角色配置 →
        `"neko"`。注意**不能**写成 `getattr(plugin, "_her_name", "")` —— 插件上没有这个
        属性，那样拿到空串就会在下面 `if not her_name: return` 静默早退，整个转发记录
        功能变成空操作（静默失效正是本会话一直在修的那类问题）。
        """
        sessions = getattr(self.plugin, "_user_sessions", {}) or {}
        group_key = self.plugin._build_session_key(
            sender_id="", is_group=True, group_id=source_group_id,
        )
        user_data = sessions.get(group_key)
        if isinstance(user_data, dict):
            name = str(user_data.get("her_name") or "").strip()
            if name:
                return name
        try:
            from utils.config_manager import get_config_manager

            _, her_name, _, _, _, _, _, _, _ = get_config_manager().get_character_data()
            name = str(her_name or "").strip()
            if name:
                return name
        except Exception:
            pass
        return "neko"

    async def _record_forward_in_memory(
        self, *, source_group_id: str, target_type: str, target_id: str,
        summary: str, forwarded: list[dict],
    ) -> None:
        """把「我转发了什么」记进接收方的回忆域（+ 源群留一句动作记录）。

        为什么需要：转发以前是**一次性外发** —— `send_forward` 只调 API，不写任何会话
        历史/记忆；源群那条 ai 行又因为只含 `<forward>` 标记而被当「未投递」排除。
        结果接收方事后问「你转的那段里说的 X 是什么意思」，猫娘手里什么都没有，只能
        反问「是什么东西呀」。

        记的是 **assistant 行**（猫娘自己发出去的话）——**不伪造 human 行**：合成的用户
        发言会被提取器抽成「用户说过」，那是 `SYNTHETIC_SOURCE_KINDS` 那套护栏一直在防的。
        """
        bridge = getattr(self.plugin, "memory_bridge", None)
        if bridge is None:
            return
        her_name = self._resolve_her_name(source_group_id)
        if not her_name:
            return

        async def _post(channel, subject, text: str, label: str) -> None:
            messages = [{"role": "assistant", "content": [{"type": "text", "text": text}]}]
            try:
                if channel == "legacy":
                    await bridge.post_memory_history("cache", her_name, messages, timeout=5.0)
                else:
                    await bridge.post_scoped_memory_history(
                        her_name, messages, subject=subject, timeout=10.0,
                    )
            except Exception as exc:
                # 转发已经发出去了，这里只是补记 —— 失败只降级成日志。
                self.plugin._emit_log("WARNING", f"[Forward] {label}转发记录写入失败: {exc}")

        channel = self._forward_memory_channel(target_type, target_id)
        if channel is not None:
            kind, subject = channel
            # 接收方那份写「你」/「群 X」：这是**他自己的域**，写"转发给了你"最自然。
            recipient_label = f"群 {target_id}" if target_type == "group" else "你"
            await _post(kind, subject, self._forward_memory_text(
                source_group_id=source_group_id, target_label=recipient_label,
                summary=summary, forwarded=forwarded,
            ), "接收方")

        # 源群也留一句动作记录（不附原文：那些话本来就是这里的 human 行）。
        # 这样她在群里也能主动说「我刚把那段转给了 X」。
        #
        # ⚠️ 目标标签**不写私聊对象的 QQ 号**：源群记忆会被群聊回复召回，把管理员的
        # QQ 存进群域是一种披露。群目标写群号（本来就在群语境里），私聊目标只写
        # 「私聊里的某人」。
        if bool((getattr(self.plugin, "_qq_settings", {}) or {}).get(
            "group_memory_enabled", False,
        )):
            source_label = f"群 {target_id}" if target_type == "group" else "私聊里的某人"
            await _post(
                "scoped", bridge.group_subject(source_group_id),
                self._forward_memory_text(
                    source_group_id=source_group_id, target_label=source_label,
                    summary=summary, forwarded=[], for_source_group=True,
                ),
                "源群",
            )

    async def _run_delivery(self, delivery_plan, request: QQReplyRequest = None, outcome: QQReplyOutcome = None, context=None) -> QQDeliveryResult | None:
        if (
            request is not None
            and outcome is not None
            and self._primary_row_superseded(outcome, delivery_plan)
        ):
            # 主会话产出了非空文本、清洗后为空（例如整条都是思考标签），
            # 于是改发默认回复：那条 raw ai 行已经躺在共享历史里且永远不会
            # 被发出去。used_default_message 让它绕过了所有未投递打标——
            # 下一次 digest 会把用户从没看到的内容（含隐藏推理）入库。
            self.plugin.session_memory_service.record_tail_undelivered_ai_row(
                self.plugin._build_session_key(
                    sender_id=request.sender_id,
                    is_group=request.is_group,
                    group_id=request.group_id if request.is_group else None,
                ),
                outcome.history_ai_row,
            )

        # 情绪/标记：内部状态，先于缓冲/冷却/交付更新
        if outcome and outcome.feeling:
            if delivery_plan and delivery_plan.target_type == "group":
                group = delivery_plan.target_id
            elif request and getattr(request, "is_group", False):
                group = getattr(request, "group_id", "") or ""
            else:
                group = ""
            if group and self.plugin.attention_service:
                await self.plugin.attention_service.set_emotion(group, outcome.feeling)

        # 表情反应（贴表情到对方消息上）：同样是"内部状态"级的一次性副作用，
        # 不参与块投递、也不该受缓冲/冷却影响 —— 反应是针对某条已存在的消息，
        # 延迟或合并都没有意义。以前这个解析结果没有任何消费方。
        if outcome is not None and getattr(outcome, "emoji_reaction_id", ""):
            target_message_id = (
                str(getattr(request, "current_message_id", "") or "")
                or str(getattr(request, "quoted_message_id", "") or "")
            )
            await self.plugin.reply_delivery_node.send_emoji_reaction(
                target_message_id, outcome.emoji_reaction_id,
            )

        # 合并转发：`<mark/>` 记起点，`<forward>` 把标记之后的多人多句抛出去。
        if outcome is not None:
            await self._handle_forward_marks(request, outcome)

        # 缓冲内部调用的请求不再次走缓冲（否则自我延迟/自我合并）。
        # 判据收口到 `BUFFER_INTERNAL_SOURCE_KINDS`：这里曾经内联第三个元组，
        # 于是与 SYNTHETIC_SOURCE_KINDS 一起漂移、双双漏掉 proactive_group。
        skip_buffer = bool(
            request
            and getattr(request, "source_kind", "") in BUFFER_INTERNAL_SOURCE_KINDS
        )
        # 缓冲可按群聊/私聊分别关闭；关掉的那一类走正常投递，不再排队等待。
        buffer_on = bool(
            self.plugin.reply_buffer_service
            and self.plugin.reply_buffer_service.is_enabled(
                getattr(self.plugin, "_qq_settings", {}) or {},
                is_group=bool(getattr(request, "is_group", False)),
            )
        )
        if not skip_buffer and buffer_on and request and delivery_plan and delivery_plan.blocks:
            # 取真实 tool 边界之后的最终段：pre-tool 里的内容不该参与"是否空回复"判定。
            raw = getattr(outcome, "post_tool_text", None)
            if raw is None:
                raw = (outcome.raw_reply_text if outcome else "") or ""
                structural_pre_tool = str(
                    getattr(outcome, "pre_tool_text", "") or ""
                )
                if structural_pre_tool and raw.startswith(structural_pre_tool):
                    raw = raw[len(structural_pre_tool):]
            clean = str(raw or "").strip()
            # 发送延迟由脚本按正态分布取样 —— 与提示词无关，模型不参与
            wait_sec = QQReplyBufferService.send_pause_seconds(
                private=not bool(request.is_group),
                settings=getattr(self.plugin, "_qq_settings", None))
            first_text = delivery_plan.blocks[0].text if delivery_plan.blocks else ""
            visible_text = delivered_blocks_text(delivery_plan.blocks)
            # 检查是否有实际内容（text/record/sticker/poke/emoji 任一非空即有效）
            has_content = any(
                b.text or b.record or b.sticker or b.poke or b.emoji
                for b in (delivery_plan.blocks or [])
            )
            # clean 可能含 <msg></msg>，去标签后再判空
            clean_stripped = re.sub(r"<[^>]+>", "", clean).strip() if clean else ""
            if not has_content and not clean_stripped:
                # LLM 决定不回复（<msg></msg>），跳过缓冲
                _note_outcome("模型没有给出可发的内容（这一轮是空回复）")
                from .pipeline_models import QQDeliveryResult
                return QQDeliveryResult(delivered=False, target_type=delivery_plan.target_type, target_id=delivery_plan.target_id, reply_text=None)
            session_key = self.plugin._build_session_key(
                sender_id=request.sender_id,
                is_group=request.is_group,
                group_id=request.group_id,
            )
            self.plugin._emit_log("DEBUG", f"[Buffer] 调度延迟回复: key={session_key} wait={wait_sec:.1f}s text={first_text[:30]}")
            # 转发消息的子条数计入缓冲
            fwd_count = int(getattr(request, 'forward_sub_count', 0) or 0)
            await self.plugin.reply_buffer_service.schedule_reply(
                session_key=session_key,
                # reply_text 是缓冲汇总的语义输入；首块可能只是 pre-tool，
                # 必须把最终 XML 块也带上，不能让汇总只看到“我查一下”。
                reply_text=visible_text or clean or "",
                raw_text=clean or first_text or "",
                blocks=delivery_plan.blocks,
                wait_seconds=wait_sec,
                sender_id=request.sender_id,
                is_group=request.is_group,
                group_id=request.group_id or "",
                extra_count=fwd_count,
                history_backed=not bool(
                    (
                        getattr(outcome, "used_fallback", False)
                        or getattr(outcome, "used_default_message", False)
                    ) if outcome else False
                ),
                mention_context=context,
                used_fallback_reply=bool(
                    (
                        getattr(outcome, "used_fallback", False)
                        or self._primary_row_superseded(outcome, delivery_plan)
                    ) if outcome else False
                ),
                private_permission_level_at_receipt=getattr(
                    context, "private_permission_level_at_receipt", None,
                ),
                first_user_materialized=bool(
                    (
                        getattr(self.plugin, "_user_sessions", {}) or {}
                    ).get(session_key, {}).get("human_row_materialized", False)
                ),
                consent_snapshot=(
                    # 私聊也可能有依赖（跨群开关打开时的会话清单段），
                    # 按 is_group 分流会让那条路径没有可撤的授权。空 dict
                    # 是"本轮无依赖"这个结论，不能折成 None（那是"还没有
                    # 结论"，会去采样当前开关）。
                    self._generation_consent_snapshot(context)
                    if context is not None else None
                ),
                consented=bool(
                    # Use the resolved policy for private participant turns as
                    # well as groups. Otherwise every private input looks
                    # consented and OFF-era text can re-enter memory through a
                    # later synthetic buffer summary.
                    getattr(context, "persist_memory", None)
                    if context is not None
                    and getattr(context, "persist_memory", None) is not None
                    else getattr(request, "persist_memory", None)
                ),
            )
            from .pipeline_models import QQDeliveryResult
            _note_outcome(
                f"已交给缓冲：等 {wait_sec:.1f}s，{len(delivery_plan.blocks)} 块"
                "（真正的投递在缓冲那一侧，成败看后面的 [Send]）"
            )
            return QQDeliveryResult(delivered=True, target_type=delivery_plan.target_type, target_id=delivery_plan.target_id, reply_text=first_text)

        direct_session_key = None
        direct_ai_row = None
        if (
            request is not None
            and outcome is not None
            and outcome.history_ai_row is not None
            and not getattr(outcome, "used_fallback", False)
            and not getattr(outcome, "used_default_message", False)
            and not self._primary_row_superseded(outcome, delivery_plan)
        ):
            direct_session_key = self.plugin._build_session_key(
                sender_id=request.sender_id,
                is_group=request.is_group,
                group_id=request.group_id if request.is_group else None,
            )
            direct_ai_row = outcome.history_ai_row
            # The history row exists before network delivery. Fence it before
            # the await so an opt-out settlement cannot persist a reply whose
            # send later fails.
            self.plugin.session_memory_service.record_provisional_ai_row(
                direct_session_key, direct_ai_row,
            )

        if context is not None and self._consent_revoked_before_send(context):
            # 直投没有 buffer 的撤销闸：生成后复检到真正发出去之间还有
            # 后处理（XML 修复等再等一次 LLM）与计划构建，这段窗口里关掉
            # 开关的话，带着已撤销记忆的回复照样会发出去。
            self.plugin.logger.warning("发送前记忆授权已撤销，取消本轮投递")
            if (
                request is not None
                and outcome is not None
                and not getattr(outcome, "used_fallback", False)
                and not getattr(outcome, "used_default_message", False)
            ):
                self.plugin.session_memory_service.record_tail_undelivered_ai_row(
                    self.plugin._build_session_key(
                        sender_id=request.sender_id,
                        is_group=request.is_group,
                        group_id=request.group_id if request.is_group else None,
                    ),
                    outcome.history_ai_row,
                )
            from .pipeline_models import QQDeliveryResult
            _note_outcome("发送前记忆授权已撤销，取消本轮投递")
            return QQDeliveryResult(
                delivered=False,
                target_type=getattr(delivery_plan, "target_type", "group"),
                target_id=getattr(delivery_plan, "target_id", "") or "",
                reply_text=None,
            )

        def _mark_tail_undelivered() -> None:
            if (
                request is not None
                and outcome is not None
                and not getattr(outcome, "used_fallback", False)
                and not getattr(outcome, "used_default_message", False)
            ):
                self.plugin.session_memory_service.record_tail_undelivered_ai_row(
                    self.plugin._build_session_key(
                        sender_id=request.sender_id,
                        is_group=request.is_group,
                        group_id=request.group_id if request.is_group else None,
                    ),
                    outcome.history_ai_row,
                )

        try:
            result = await self.plugin.reply_delivery_node.deliver(
                delivery_plan,
                consent_gate=(
                    (lambda: self._consent_revoked_before_send(context))
                    if context is not None else None
                ),
            )
        except asyncio.CancelledError:
            # 取消（stop_runtime 会显式取消所有 handler task）走的是
            # BaseException，不会被下面的 except Exception 接住：用户可能
            # 一个字没收到、也可能只收到前半条，而 ai 行已经躺在共享历史
            # 里——不打标的话关机结算会把它当已投递入库。
            _mark_tail_undelivered()
            raise
        except Exception:
            # NapCat 传输失败以异常上浮：history-backed 回复的 ai 行已在
            # 共享历史里，先按投递失败记入排除名单再传播异常，否则下一次
            # digest 会把没发出去的回复入库。
            _mark_tail_undelivered()
            raise
        if direct_ai_row is not None:
            self.plugin.session_memory_service.settle_provisional_ai_row(
                direct_session_key, direct_ai_row,
                delivered=bool(result is not None and result.delivered),
            )
        if (
            result is not None
            and not getattr(result, "delivered", False)
            and request is not None
            and outcome is not None
            and not getattr(outcome, "used_fallback", False)
            and not getattr(outcome, "used_default_message", False)
        ):
            # 直投失败（合成轮/无 buffer 的 history-backed 回复）：ai 行已
            # 躺在共享历史里，不记名单的话下一次 digest/finalize 会把没
            # 发出去的回复提取成持久记忆。失败即定局，直接进排除名单。
            session_key = self.plugin._build_session_key(
                sender_id=request.sender_id,
                is_group=request.is_group,
                group_id=request.group_id if request.is_group else None,
            )
            self.plugin.session_memory_service.record_tail_undelivered_ai_row(
                session_key, outcome.history_ai_row,
            )
        if (
            result is not None
            and getattr(result, "delivered", False)
            and context is not None
            and outcome is not None
            and outcome.reply_text
        ):
            # 整条计划的正文：outcome.reply_text 只有首块，后续块里披露
            # 的事实既进不了历史（digest 少半条），也记不到 mention（永远
            # 到不了 suppression）。
            delivered_text = (
                delivered_blocks_text(delivery_plan.blocks) or outcome.reply_text
            )
            if getattr(outcome, "used_fallback", False) or (
                self._primary_row_superseded(outcome, delivery_plan)
            ):
                # fallback / 默认回复都没有对应的历史 ai 行（默认回复那条
                # raw 行刚被标成未投递）：确认投递后补上真正说出去的话，
                # 否则群 digest 只会存下半边对话。
                self.plugin.reply_generation_service.append_fallback_ai_row(
                    context, delivered_text,
                )
            # mention 计数绑定实际投递（非 buffer 直投与合成轮都走这里；
            # buffer 路径由 _deliver_after_wait 在真投递后补记）。
            await self.plugin.reply_generation_service.record_scoped_mentions_on_delivery(
                context, delivered_text,
            )
        if result is None:
            # `deliver()` 的 None **只有一个来源**：计划里什么都没有（它开头那句
            # `if not plan or not plan.blocks: return None`）。也就是说这一轮**没有可发
            # 的内容**，跟连接器一点关系都没有。
            #
            # 真机 2026-09-29 17:0x 就是这里（群 1048307485，连续 6 轮）：模型每轮都
            # 回 `<feeling>…</feeling><msg></msg>`（`AI 生成回复完成 length: 36`，每轮
            # 一模一样），后处理之后既没有 blocks 也没有正文 → `build_delivery_plan`
            # 返回 None → 落到直投分支 → `deliver(None)` 立刻返回 None。而这条出口当时
            # 写的是「投递未确认：连接器没有回执」—— 使用者看到的是"她发不出去"，
            # 真相是"她这一轮选择不说话"。同一类谎以前在 §32 查过一次（日志必须说得出
            # "为什么没发"，而不是把责任推给传输层）。
            #
            # 原始输出一起打出来：这一层能看到的只有"计划是空的"，而"为什么空"
            # （整条都是标签？`<msg></msg>`？被过滤光？）只有模型原文能回答。
            try:
                raw = str(getattr(outcome, "raw_reply_text", "") or "")
                self.plugin.logger.warning(
                    f"[Send] 计划为空（模型没给出可发的内容），原始输出={raw[:120]!r}"
                )
            except Exception:  # noqa: BLE001 —— 日志失败不该影响结论
                pass
            _note_outcome("模型没有给出可发的内容（这一轮是空回复）")
        elif getattr(result, "delivered", False):
            _note_outcome(f"直接投递（无缓冲，{len(delivery_plan.blocks or [])} 块）")
        else:
            # 走到这里说明连接器没给回执（NapCat echo 超时 / 开放平台吞异常返回 None）
            _note_outcome("投递未确认：连接器没有回执（消息可能没发出去）")
        return result

    @staticmethod
    def _primary_row_superseded(outcome, delivery_plan) -> bool:
        """True when the ai row this turn wrote is not what went out.

        The only remaining shape is a default reply that replaced a nonempty
        primary answer. A block carrying both `<text>` and `<record>` used to
        count too, because delivery sent the record and continued, so the text
        reached nobody while it sat in the history row. Delivery now sends
        **both** (see `reply_delivery_node`), so that shape is gone — keeping it
        would mark a fully delivered turn as undelivered and drop genuinely
        spoken content from the digest."""
        if getattr(outcome, "used_fallback", False):
            return False  # fallback turns have no history row of their own
        if getattr(outcome, "used_default_message", False):
            return bool(str(getattr(outcome, "raw_reply_text", "") or "").strip())
        return False

    def _consent_revoked_before_send(self, context) -> bool:
        """True when a switch this reply's prompt relied on went off since
        generation — same judgement the buffer applies before a delayed
        send, so the unbuffered direct path is not the weak link."""
        permission_snapshot = getattr(
            context, "private_permission_level_at_receipt", None,
        )
        permission_mgr = getattr(self.plugin, "permission_mgr", None)
        if (
            not getattr(context, "is_group", False)
            and permission_snapshot is not None
            and permission_mgr is not None
            and permission_mgr.get_permission_level(context.sender_id)
            != permission_snapshot
        ):
            return True
        snapshot = getattr(context, "consent_snapshot", None)
        if not snapshot:
            return False
        settings = getattr(self.plugin, "_qq_settings", {}) or {}
        return any(
            was_enabled and not settings.get(key, False)
            for key, was_enabled in snapshot.items()
        )

    def _generation_consent_snapshot(self, context) -> dict:
        """本轮回复实际消费掉的授权，取自生成时刻而非此刻。

        后处理（XML 修复等）会再等一次 LLM，这中间关掉的开关如果在这里
        重新采样，缓冲的撤销检查就是 false 比 false——被撤销的记忆内容
        照样随延迟投递发出去。生成路径拿不到快照时（合成/轻量 context）
        才退回读当前设置。"""
        snapshot = getattr(context, "consent_snapshot", None)
        if snapshot is not None:
            # 空 dict 也是结论（本轮没有任何记忆依赖），不是"没结论"。
            return dict(snapshot)
        settings = getattr(self.plugin, "_qq_settings", {}) or {}
        return {
            key: bool(settings.get(key, False))
            for key in (
                "group_memory_enabled",
                "group_member_memory_enabled",
                "private_participant_memory_enabled",
                "allow_cross_group_context",
            )
        }

    def _resolve_sticker_path(self, sticker_id: str) -> str:
        """解析表情包 ID 到文件路径。"""
        import json
        import os
        # 走 SDK 状态根（data_path）；__file__ 相对路径指向代码根，迁移后读不到存档
        sticker_path = self.plugin.data_path("sticker.json")
        try:
            with open(sticker_path, "r", encoding="utf-8") as f:
                sticker_data = json.loads(f.read())
        except Exception:
            return ""
        info = sticker_data.get(sticker_id)
        if not isinstance(info, dict):
            return ""
        img_path = info.get("path", "")
        if not img_path:
            return ""
        full_path = sticker_path.parent / "sticker" / img_path
        if os.path.exists(full_path):
            return f"file://{full_path}"
        return img_path

    async def _run_relay_delivery(self, relay_plan) -> QQRelayResult | None:
        return await self.plugin.reply_relay_node.execute(relay_plan)
