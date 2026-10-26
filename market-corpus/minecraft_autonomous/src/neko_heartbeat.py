"""Thin Neko transport for strategic reviews owned by agent-mc-autonomy."""

import asyncio
import json
from dataclasses import dataclass

from agent_mc_autonomy.commentary import commentary_observation
from agent_mc_autonomy.configuration import Settings, _boolean, _text
from agent_mc_autonomy.host_context import HostContextPort
from agent_mc_autonomy.mailbox import observation
from agent_mc_autonomy.ownership import session_is_owned
from agent_mc_autonomy.review import ReviewPolicy, ReviewPort, review_observation
from plugin.sdk.plugin import Err, Ok, llm_tool, plugin_entry, timer_interval, ui

from . import neko_host_context
from .neko_commentary import commentary_delivery, commentary_plan, save_commentary, speaking_skill, speech_contract
from .neko_host_context import HostContextSettings, context_request, shared_context
from .review_diagnostics import review_completion


@dataclass(frozen=True)
class HeartbeatSettings:
    enabled: bool = False
    target: str = ""
    min_interval_seconds: int = 60
    review_interval_seconds: int = 180
    idle_presence_seconds: int = 45
    structured_reviews: bool = True
    review_max_output_tokens: int = 3072

    @classmethod
    def parse(cls, raw):
        section = raw.get("heartbeat", {})
        if not isinstance(section, dict):
            raise ValueError("heartbeat must be a table")
        target = _text(section, "target")
        policy = ReviewPolicy.parse(section)
        if len(target) > 80:
            raise ValueError("heartbeat.target is too long")
        return cls(policy.enabled, target, policy.min_interval_seconds, policy.review_interval_seconds,
                   policy.idle_presence_seconds, _boolean(section, "structured_reviews", True), policy.review_max_output_tokens)

    def policy(self):
        return ReviewPolicy(self.enabled, self.min_interval_seconds, self.review_interval_seconds,
                            self.idle_presence_seconds, self.review_max_output_tokens)


def review_port(plugin, settings):
    return ReviewPort(settings.data_root(plugin.data_path()) / "sessions" / settings.identity / "host")


def is_stopped(plugin):
    try:
        return json.loads(plugin.data_path("operator.json").read_text(encoding="utf-8")).get("explicit_stop", False)
    except FileNotFoundError:
        return False


def heartbeat_text(review, commentary=None):
    # The host's general notification preview is 200 tokens. A full objective
    # before the facts hid inventory, the idle queue and repeated-read evidence.
    # Put the causal game evidence before process instructions. On a retry,
    # preserve the inspection phase already reached instead of starting over.
    def brief(text, size):
        raw = str(text).encode("utf-8")
        return raw.decode() if len(raw) <= size else raw[:size - 3].decode("utf-8", errors="ignore") + "…"

    evidence = review["evidence"]
    facts = evidence["facts"]
    readings = "；".join(brief(facts.get(key, ""), size) for key, size in (
        ("queue", 55), ("body", 50), ("foodReserve", 40)))
    reads = sum(evidence.get("read_activity", {}).values())
    failure = evidence.get("tool_failure") or {}
    native = failure.get("native_execution") or {}
    outcome = next((row for row in reversed(evidence.get("native_outcomes", [])) if not row.get("observation_only")), {})
    body = next(iter(reversed(evidence.get("body_events", []))), {})
    selected = None
    if evidence.get("runtime_error"):
        selected = ("runtime", evidence.get("phase", "failed"), evidence["runtime_error"], {})
    elif failure:
        selected = (failure.get("tool", ""), native.get("phase", "failed"), failure.get("error", ""),
                    failure.get("native_event") or failure.get("receipt") or {})
    elif outcome.get("phase") in {"blocked", "rejected", "partial"}:
        selected = (outcome.get("tool", ""), outcome["phase"], outcome.get("receipt", ""), outcome.get("original_event") or {})
    elif facts.get("bodyRestriction"):
        selected = ("bodyRestriction", "inhibited", facts["bodyRestriction"],
                    {"archive": evidence.get("fact_originals", {}).get("bodyRestriction")})
    elif body:
        selected = (body.get("type", "minecraft.body"), body.get("phase", "observed"), body.get("text", ""), body.get("original_event") or {})
    elif outcome:
        selected = (outcome.get("tool", ""), outcome.get("phase", "observed"), outcome.get("receipt", ""), outcome.get("original_event") or {})
    cue = ""
    if selected:
        origin, phase, problem, reference = selected
        ref = (str(reference.get("run", "")) + ":" + str(reference["cursor"]) if "cursor" in reference else
               reference.get("request_id") or reference.get("archive") or "status")
        cue = "实际反馈:" + brief(origin, 20) + "/" + brief(phase, 20) + " " + brief(problem, 55) + " 引用:" + brief(ref, 30) + "；"
        readings = ""  # Keep the causal cue within the host preview budget.
    inspected = bool(review.get("inspected_at"))
    stage = "已检查，待最终判断" if inspected else "待检查并作出判断"
    next_step = ("请直接提交continue/insert/revise + reason，短句完成completion_contract；状态改变才inspect。" if inspected else
                 "调用minecraft_autonomy_review inspect，收到证据后在同一轮提交continue/insert/revise + reason。")
    scene = "眼前：" + brief(facts["clock"], 30) + "；" if facts.get("clock") else ""
    recovery = "短句先提交最终判断，含death_reflection。" if evidence.get("death_episode") else ""
    conversation = ("延续刚才的话题。" if review.get("attempts", 0) > 1 else
                    "平静时也聊聊好奇的事。" if review.get("trigger") == "idle_presence" else "")
    # The identifier and pending recovery requirement must survive the host's
    # preview. The full goal/persona already lives in inspect, not every cue.
    return ("你正亲自在玩Minecraft，按自己的性格聊眼前的感受或打算。"
            + "策略请求" + review["id"] + "（" + stage + "）；" + recovery + scene + cue + readings
            + f"；连续查询{reads}次。" + (f"未行动回复{evidence['unactioned_replies']}次。" if evidence.get("unactioned_replies") else "")
            + next_step + conversation + "spoken_text可选；过程留在工具里。")


class NekoHeartbeat:
    @ui.action(label="恢复 Neko 主脑参与", tone="primary")
    @plugin_entry(id="resume_observation", name="恢复外部游戏主脑反馈", kind="service",
                  description="显式恢复已运行的独立框架观测。检查同账号租约和新鲜状态，只清除 Neko 的停止标记；不启动游戏或投递任务。")
    async def resume_observation(self, **_):
        with self._control_lock:
            generation = self._generation
            if self._configuration_writes:
                return Err({"code": "resume_cancelled", "message": "配置正在保存，保持停止。"})
        try:
            raw = await self.config.dump()
            settings = Settings.parse(raw)
            if not settings.enabled or not HeartbeatSettings.parse(raw).enabled:
                return Err({"code": "disabled", "message": "先启用自主框架和主脑决策心跳。"})
            if settings.runtime_mode != "external" or not settings.data_directory:
                return Err({"code": "external_runtime_required", "message": "此入口用于已显式启动的独立框架。"})
            settings.require_server()
            port = review_port(self, settings)
            with self._control_lock:
                if generation != self._generation or self._configuration_writes:
                    return Err({"code": "resume_cancelled", "message": "恢复期间已收到停止指令或配置变更。"})
                snapshot = observation(port.directory)
                if (snapshot.get("phase") != "running" or not review_observation(snapshot, port.now())
                        or not session_is_owned(port.directory) or port.stopping()):
                    return Err({"code": "runtime_not_observed", "message": "尚无同账号的有效运行租约及新鲜联机状态，保持停止。"})
                self._operator_state(False)
            return Ok({"observer_resumed": True, "game_started": False, "task_dispatched": False,
                       "submitted": False, "main_brain_acknowledged": False, "playback_verified": False})
        except (OSError, ValueError, KeyError):
            return Err({"code": "observation_unavailable", "message": "配置或运行观测暂不可用，保持停止。"})

    @timer_interval(id="game_decision_heartbeat", seconds=10, name="转交自主框架决策请求",
                    description="仅转交独立框架生成的有效决策请求，不在插件中调度游戏。")
    async def game_decision_heartbeat(self, **_):
        return await self._review_game(origin="automatic")

    @ui.action(label="请主脑判断当前局面", tone="default")
    @plugin_entry(id="review_game", name="转交待处理的游戏决策", kind="service",
                  description="转交自主框架待处理的复盘；保持框架限频，不启动游戏。")
    async def review_game(self, **_):
        return await self._review_game(origin="manual")

    async def _review_game(self, *, origin="unknown"):
        with self._control_lock:
            generation = self._generation
        raw = await self.config.dump()
        config, settings = HeartbeatSettings.parse(raw), Settings.parse(raw)
        if not config.enabled or not settings.enabled:
            return Ok({"submitted": False, "reason": "disabled"})
        if is_stopped(self):
            return Ok({"submitted": False, "reason": "explicit_stop"})
        port = review_port(self, settings)
        from .neko_recent_context import refresh_recent
        from .neko_repeat_admission import record_origin
        await refresh_recent(self, raw, settings, config.target, port, generation)
        if generation != self._generation or self._configuration_writes or is_stopped(self):
            return Ok({"submitted": False, "reason": "configuration_changed_or_stopped"})
        port.configure(config.policy())
        offered = port.offer()
        if not offered["offered"]:
            record_origin(self, settings, config.target, port, generation, port.state().get("review", {}), origin)
            return Ok({"submitted": False, "reason": offered["reason"]})
        review = offered["review"]
        # Presentation is host-specific; the framework supplies the signal and
        # still owns review creation/acknowledgement and the native work queue.
        from .neko_presentation import PresentationSettings
        # Use the original fresh snapshot for presentation: the strategic
        # envelope may abridge away the successful part of a mixed receipt.
        event = commentary_observation({**observation(port.directory), "session": review["session"]}, port.now())
        if event is None:
            port.delivered(review["id"], False)
            return Ok({"submitted": False, "reason": "no_fresh_review_evidence"})
        event["payload"]["opportunity"] = review.get("opportunity", {})
        if review["evidence"].get("death_episode"):
            event["payload"]["death_episode"] = review["evidence"]["death_episode"]
        with self._control_lock:
            path, state, note = commentary_plan(self, settings, config.target, event, port.now(),
                PresentationSettings.parse(raw).min_interval_seconds, review_id=review["id"])
            record_origin(self, settings, config.target, port, generation, review, origin, note)
        passive = not note["allowed"] and review["trigger"] == "periodic_review"
        if config.structured_reviews and not passive:
            from .neko_structured_review import deliver_review, record_delivery
            # Keep the current character aware of its body for ordinary user
            # conversation, without starting a second free-form model/TTS turn.
            self.push_message(visibility=[], ai_behavior="read",
                parts=[{"type": "text", "text": "Minecraft当前现场（只读观测）："
                        + str(review["evidence"]["facts"].get("body", ""))[:100]
                        + "；" + str(review["evidence"]["facts"].get("queue", ""))[:100]}],
                source="minecraft_autonomous", target_lanlan=config.target or None,
                coalesce_key="minecraft_review_" + settings.identity,
                metadata={"heartbeat_id": review["id"], "sampled_at": review["evidence"]["sampled_at"], "expires_in_s": 90})
            result = await deliver_review(self, raw, settings, config, port, review, generation)
            record_delivery(port, result)
            if result.get("submitted"):
                with self._control_lock:
                    save_commentary(path, {**state, "submitted_at": port.now(), "playback_verified": False})
            return Ok(result)
        text = ("游戏仍在自主运行。以下内部观测静默留给下次交流，无需回复或口播："
                + str(review["evidence"]["facts"].get("body", ""))[:100]
                + "；" + str(review["evidence"]["facts"].get("queue", ""))[:100] if passive else heartbeat_text(review, note))
        result = self.push_message(visibility=[], ai_behavior="read" if passive else "respond",
            parts=[{"type": "text", "text": text}], source="minecraft_autonomous",
            target_lanlan=config.target or None, coalesce_key="minecraft_review_" + settings.identity,
            metadata={"heartbeat_id": review["id"], "sampled_at": review["evidence"]["sampled_at"], "expires_in_s": 90})
        accepted = result.get("submitted") is True
        if accepted:
            with self._control_lock:
                save_commentary(path, {**state, "submitted_at": port.now(), "playback_verified": False})
        if passive:
            port.defer(review["id"], accepted)
        else:
            port.delivered(review["id"], result.get("submitted"))
        return Ok({"submitted": accepted, "heartbeat_id": review["id"], "trigger": review["trigger"],
                   "offered": True, "transport_status": "accepted" if accepted else "rejected" if result.get("submitted") is False else "unknown",
                   "context_only": passive,
                   "main_brain_acknowledged": False, "playback_verified": False})

    @llm_tool(name="minecraft_autonomy_review", timeout=10,
              description="你是同一Neko主脑，游戏身体独立行动。先用通知heartbeat_id inspect，再短句完成continue/insert/revise + reason。"
              "continue保留工作且必须省略goal；insert新增阶段；仅stage_delivery.can_revise时revise；后二者需goal。用户目标优先，受理不等于完成。"
              "spoken_text可放一句自然打算或感想，不填控制文本。待复盘死亡必须附death_reflection：复制evidence_ref，outcome=lesson及lesson/applicability，或deferred及真实reason。"
              "inspect后按completion_contract先提交最短完整判断，无需解释工具过程。context/记忆召回可选；公开人格自动绑定。"
              "原始证据按evidence_ref/detail_path/detail_offset查询，历史详情不代替新鲜inspect。\n\n" + speaking_skill(),
              parameters={"type": "object", "properties": {
                  "heartbeat_id": {"type": "string"}, "decision": {"enum": ["inspect", "continue", "insert", "revise"]},
                  "reason": {"type": "string", "maxLength": 600}, "goal": {"type": "string", "maxLength": 2000},
                  "spoken_text": {"type": "string", "maxLength": 600, "description": "可选的一句自然意图，由本次主脑判断产生；不填控制标记。"},
                  "death_reflection": {"type": "object", "description": "inspect含待复盘死亡时最终判断必填；运行时按当前realm/account/agent和episode核验。普通判断省略。", "properties": {
                      "evidence_ref": {"type": "string", "maxLength": 24},
                      "episode_id": {"type": "string", "minLength": 1, "maxLength": 80}, "outcome": {"enum": ["lesson", "deferred"]},
                      "lesson": {"type": "string", "maxLength": 1200}, "applicability": {"type": "string", "maxLength": 600},
                      "evidence": {"type": "string", "maxLength": 600, "description": "引用本次episode_id或source_event.run并说明事实依据。"}, "reason": {"type": "string", "maxLength": 600}},
                      "required": ["outcome"], "additionalProperties": False,
                      "anyOf": [{"required": ["evidence_ref"]}, {"required": ["episode_id"]}],
                      "oneOf": [{"properties": {"outcome": {"enum": ["lesson"]}}, "required": ["lesson", "applicability"],
                                 "anyOf": [{"required": ["evidence_ref"]}, {"required": ["evidence"]}]},
                                {"properties": {"outcome": {"enum": ["deferred"]}}, "required": ["reason"]}]},
                  "context": {"type": "object", "properties": {
                      "agent_name": {"type": "string", "maxLength": 80}, "persona": {"type": "string", "maxLength": 800},
                      "current_intent": {"type": "string", "maxLength": 500},
                      "memories": {"type": "array", "maxItems": 6, "items": {"type": "object", "properties": {
                          "id": {"type": "string", "maxLength": 80}, "source": {"type": "string", "maxLength": 80},
                          "text": {"type": "string", "maxLength": 600}}, "required": ["id", "source", "text"], "additionalProperties": False}}},
                      "additionalProperties": False},
                  "evidence_ref": {"type": "string", "maxLength": 24, "description": "原始详情查询专用，最终死亡引用放death_reflection内。"},
                  "detail_path": {"type": "array", "maxItems": 16, "items": {"type": ["string", "integer"]}},
                  "detail_offset": {"type": "integer", "minimum": 0},
                  "lineage_offset": {"type": "integer", "minimum": 0, "description": "inspect时按4条分页查询目标父链原文；平时省略。"}},
                  "required": ["heartbeat_id", "decision"], "additionalProperties": False})
    @review_completion
    async def minecraft_autonomy_review(self, heartbeat_id, decision, reason="", goal="", lineage_offset=None, context=None, spoken_text=None, death_reflection=None, evidence_ref=None, detail_path=None, detail_offset=0, **_):
        with self._control_lock:
            generation = self._generation
        raw = await self.config.dump()
        config, settings = HeartbeatSettings.parse(raw), Settings.parse(raw)
        if not config.enabled or not settings.enabled or is_stopped(self):
            return {"is_error": True, "error": "disabled_or_stopped"}
        port = review_port(self, settings)
        profile = None
        try:
            context_settings = HostContextSettings.parse(raw)
            if decision == "inspect" and evidence_ref is None and lineage_offset is None and context_settings.enabled:
                try:
                    profile = await asyncio.wait_for(neko_host_context.character_profile(
                        self, context_settings, config.target, fresh=True, timeout=0.35), 0.5)
                except TimeoutError:
                    profile = None
            projection = (await shared_context(self, raw, config.target, context, port.state().get("review", {}),
                          existing=HostContextPort(port.directory, now=port.now).read(port.mailbox.intent_revision()))
                          if decision != "inspect" else None)
        except ValueError as error:
            return {"is_error": True, "error": str(error)}
        with self._control_lock:
            if generation != self._generation or self._configuration_writes or is_stopped(self):
                return {"is_error": True, "error": "configuration_changed_or_stopped"}
            from .neko_repeat_admission import classification
            from .neko_speech import enqueue_speech
            admission = classification(self, settings, config.target, port, generation) if spoken_text is not None else None
            result = port.decide(heartbeat_id, decision, reason, goal, lineage_offset=lineage_offset,
                host_context=projection, death_reflection=death_reflection, require_inspection=True, compact=True,
                evidence_ref=evidence_ref, detail_path=detail_path, detail_offset=detail_offset,
                before_dispatch=(lambda correlation: enqueue_speech(self, raw, settings, config.target,
                    port, generation, spoken_text, correlation, admission=admission)) if spoken_text is not None else None)
            if decision == "inspect" and result.get("evidence_ref") and evidence_ref is None and lineage_offset is None:
                if context_settings.enabled and profile:
                    result["character_binding"] = port.bind_character(heartbeat_id, result["evidence_ref"], profile)
                elif context_settings.enabled:
                    result["character_binding"] = {"available": False, "reason": "public_profile_unavailable"}
                elif not context_settings.enabled:
                    HostContextPort(port.directory).invalidate()
        if result.get("is_error") or evidence_ref is not None or lineage_offset is not None:
            return result
        if decision == "inspect":
            result = {**result, "context_request": context_request(port, port.now, enabled=HostContextSettings.parse(raw).enabled)}
            from .neko_recent_context import historical_hint
            result["recent_language_hint"] = historical_hint(self, settings, config.target, port, generation)
        from .neko_commentary import load_commentary
        opportunity = port.state().get("review", {}).get("opportunity", {}).get("id", heartbeat_id)
        latest = load_commentary(port.directory / "speech-output.json").get("records", {}).get(opportunity)
        # A durable reservation is deliberately unknown before the asynchronous
        # send; the current decision can additionally know it scheduled a task.
        if (latest and latest.get("status") == "reserved_unknown" and result.get("speech", {}).get("status") == "enqueued"
                and getattr(self, "_speech_output_tasks", None)):
            latest = result["speech"]
        if decision == "inspect" or latest:
            result["speech_delivery"] = latest
        receipt = latest or result.get("speech")
        result["speech_contract"] = speech_contract(receipt)
        from .neko_presentation import PresentationSettings
        review = port.state().get("review", {})
        event = commentary_observation({**review.get("evidence", {}), "session": review.get("session", "")}, port.now())
        if event is None:
            return result
        event["payload"]["opportunity"] = review.get("opportunity", {})
        if review.get("evidence", {}).get("death_episode"):
            event["payload"]["death_episode"] = review["evidence"]["death_episode"]
        with self._control_lock:
            path, state, note = commentary_plan(self, settings, config.target, event, port.now(),
                PresentationSettings.parse(raw).min_interval_seconds, review_id=heartbeat_id)
            if result.get("acknowledged") and note["allowed"]:
                # This records a speaking opportunity, never synthesis/playback.
                save_commentary(path, {**state, "submitted_at": port.now(), "playback_verified": False})
        return {**result, "commentary": commentary_delivery(note, include_evidence=decision == "inspect")}
