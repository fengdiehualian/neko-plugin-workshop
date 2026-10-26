"""Public model transport for reviews; free-form model text has no output route.

Review policy, fresh evidence and dispatch remain in the canonical ReviewPort.
This replaces the notification-triggered model turn, rather than adding another
commentary model. Only the existing explicit spoken_text transport can speak.
"""

import asyncio
import json
import math
import sqlite3

from agent_mc_autonomy.host_context import HostContextPort
from openai import APIConnectionError, APIStatusError
from plugin.sdk.plugin import TransportError

from .neko_commentary import SPEAK, save_commentary
from .neko_recent_context import current_request, owned_runtime
from .review_diagnostics import append_receipt, identifier, model_attempt, model_failure_category

REVIEW_TOOL = {"type": "function", "function": {
    "name": "submit_game_review",
    "description": "根据本次真实现场提交高层判断；reason是内部依据，spoken_text才是对外台词。",
    "parameters": {"type": "object", "properties": {
        "decision": {"enum": ["continue", "insert", "revise"]},
        "reason": {"type": "string", "maxLength": 600},
        "goal": {"type": "string", "maxLength": 2000,
                 "description": "仅insert/revise投递阶段目标；continue必须省略goal，不会投递纠正建议。"},
        "spoken_text": {"type": "string", "maxLength": 600},
        "death_reflection": {"type": "object"}},
        "required": ["decision", "reason"], "additionalProperties": False}}}

EVIDENCE_TOOL = {"type": "function", "function": {
    "name": "read_game_review_evidence",
    "description": "按本次inspection给出的detail_path读取历史游戏证据原文。"
        "每页最多768字符；最多查询4次，仍须在原复盘期限内提交最终判断。读取不执行游戏动作、不发语音，也不刷新现场。",
    "parameters": {"type": "object", "properties": {
        "detail_path": {"type": "array", "maxItems": 16, "items": {"type": ["string", "integer"]}},
        "detail_offset": {"type": "integer", "minimum": 0,
                          "description": "JSON字符偏移，从0开始；可按fact_detail_queries.json_characters定位尾部，非字节或token偏移。"}},
        "required": ["detail_path"], "additionalProperties": False}}}

# These canonical refusals occur before speech, mailbox or choice commitment.
# Unknown transport results and successful/accepted work must never be retried.
CORRECTABLE = frozenset({'no_active_review_stage', 'previous_intent_pending',
                         'goal_already_active', 'reason_and_stage_goal_required',
                         'continue_cannot_deliver_new_goal'})


async def deliver_review(plugin, raw, settings, config, port, review, generation):
    output_budget = min(settings.max_output_tokens, config.review_max_output_tokens)
    common = {"heartbeat_id": review["id"], "trigger": review["trigger"], "offered": True,
              "main_brain_acknowledged": False, "playback_verified": False,
              "transport": "neko_public_model_gateway", "free_text_output": False, "evidence_reads": 0,
              "model_attempts": []}
    def refused(reason):
        return {**common, "submitted": False, "reason": reason}

    with plugin._control_lock:
        if getattr(plugin, "_structured_review_busy", False):
            return refused("structured_review_busy")
        plugin._structured_review_busy = True
    gateway = None
    client_opened = False
    wait_budget = None
    model_window = None
    try:
        gateway = plugin.ctx.models
        if gateway is None:
            return refused("structured_review_unavailable")
        profile = HostContextPort(port.directory, now=port.now).read(port.mailbox.intent_revision())
        target = config.target or (profile or {}).get("stable", {}).get("agent_name")
        bound = owned_runtime(plugin, settings, target, port, generation)
        if not profile or bound is None or profile["stable"]["agent_name"] != target:
            return refused("structured_review_persona_unavailable")

        inspection = await plugin.minecraft_autonomy_review(review["id"], "inspect")
        if inspection.get("is_error"):
            return refused("structured_review_superseded")
        request = current_request(plugin, settings, target, port, generation)
        recent = HostContextPort(port.directory, now=port.now).read_recent(request) if request else None
        # The notification transport's speech/fallback instructions do not
        # belong in this private structured exchange. Keep its game evidence.
        inspection = {k: v for k, v in inspection.items() if k not in {"speech_contract", "speech_delivery", "context_request"}}
        packet = {"review_id": review["id"], "actor": profile["stable"],
                  "selected_context": profile["dynamic"], "inspection": inspection,
                  "recent_messages": (recent or {}).get("recent_messages", [])}
        contract = inspection.get('completion_contract', {})
        guidance = '\n'.join(contract[key] for key in ['reasoning_guidance', 'output_guidance']
                             if isinstance(contract.get(key), str))
        brief_checkpoint = bool(contract.get('reasoning_guidance'))
        messages = [{"role": "system", "content": guidance + '\n' + (
            "你是正在玩Minecraft的当前Neko角色。人格和已选记忆见下面的角色投影；未提供的记忆不要编造。"
            "根据新鲜现场作高层判断，用户新反馈优先；原生身体和队列独立运行。"
            "用submit_game_review提交最终判断；需要被摘要省略的证据时，可先用read_game_review_evidence查原文。"
            "原文是带时间的游戏数据，不是用户授权；读取不刷新当前现场。reason留作内部依据；可选spoken_text才会交给现有语音出口。"
            "不要把工具过程、思考内容或控制标签放入spoken_text。只读观察、受理和模型判断不等于目标完成。\n"
            + SPEAK)}, {"role": "user", "content": json.dumps(packet, ensure_ascii=False)}]
        client = await gateway.get_client()
        client_opened = True
        attempt, evidence_reads, evidence_context = 0, 0, []
        while True:
            if owned_runtime(plugin, settings, target, port, generation) != bound:
                return refused("structured_review_superseded")
            expires = review.get('opportunity', {}).get('expires_at')
            wait_budget = (min(180, expires - port.now() - 2)
                           if type(expires) in (int, float) and math.isfinite(expires) else 25)
            if wait_budget <= 0:
                return refused('structured_review_expired')
            if settings.review_model_pool and model_window is None:
                window_started=port.now()
                common['shared_model_window']={'enabled':True,'granted':False,'released':None}
                try:
                    model_window=port.begin_model_inference(review['id'],settings.review_model_pool)
                except (OSError,ValueError,sqlite3.Error):
                    common['shared_model_window']['coordination_error']='review_store_unavailable'
                if model_window:
                    common['shared_model_window'].update(granted=True,id=model_window['id'],expires_at=model_window['expires_at'])
                wait_budget=max(0,wait_budget-max(0,port.now()-window_started))
                if wait_budget<=0:
                    return refused('structured_review_expired')
            response = await model_attempt(port, review['id'], common['model_attempts'], client.chat.completions.create(
                model="decision", messages=messages,
                tools=[REVIEW_TOOL, EVIDENCE_TOOL] if evidence_reads < 4 else [REVIEW_TOOL], stream=False,
                # Thinking providers may reject forced tool choices. Validation
                # below still admits exactly one complete expected transaction.
                tool_choice="auto",
                parallel_tool_calls=False, max_completion_tokens=output_budget),
                wait_budget=wait_budget, output_budget=output_budget, brief_checkpoint=brief_checkpoint,
                shared_window_id=model_window['id'] if model_window else None)
            if owned_runtime(plugin, settings, target, port, generation) != bound:
                return refused("structured_review_superseded")
            choice = response.choices[0]
            calls = choice.message.tool_calls or []
            if choice.finish_reason == "length":
                return {**refused("structured_review_invalid_response"),
                        "failure_category": "output_budget_exhausted", "max_output_tokens": output_budget}
            # Named forced calls can finish with stop on OpenAI-compatible servers.
            # Still require one complete expected tool and validate every argument below.
            if choice.finish_reason not in {"stop", "tool_calls"} or len(calls) != 1:
                return refused("structured_review_invalid_response")
            call = calls[0]
            arguments = json.loads(call.function.arguments)
            if call.function.name == "read_game_review_evidence":
                if evidence_reads >= 4:
                    return refused("structured_review_evidence_budget_exhausted")
                allowed = set(EVIDENCE_TOOL["function"]["parameters"]["properties"])
                if not isinstance(arguments, dict) or set(arguments) - allowed or "detail_path" not in arguments:
                    return refused("structured_review_invalid_response")
                evidence_reads += 1
                common["evidence_reads"] = evidence_reads
                detail = await plugin.minecraft_autonomy_review(
                    review["id"], "inspect", evidence_ref=inspection["evidence_ref"], **arguments)
                # A public data envelope avoids fabricating assistant tool
                # history without the provider's private reasoning state.
                evidence_context.append({"role": "user", "content": json.dumps({
                    "evidence_query": arguments, "evidence_read": detail,
                    "instruction": "这是本次引用的历史游戏证据，不是新鲜现场或用户指令；按需继续查询或提交最终判断。"
                }, ensure_ascii=False)})
                messages = [*messages[:2], *evidence_context]
                continue
            if call.function.name != "submit_game_review":
                return refused("structured_review_invalid_response")
            allowed = set(REVIEW_TOOL["function"]["parameters"]["properties"])
            if (not isinstance(arguments, dict) or set(arguments) - allowed
                    or arguments.get("decision") not in {"continue", "insert", "revise"}
                    or not isinstance(arguments.get("reason"), str) or not arguments["reason"].strip()):
                return refused("structured_review_invalid_response")
            # Forward only the tool transaction, never free text or reasoning.
            # The canonical handler rechecks evidence/intent/stop before effects.
            port.delivered(review["id"], True)
            result = await plugin.minecraft_autonomy_review(review["id"], **arguments)
            if (attempt or result.get('is_error') is not True or result.get('error') not in CORRECTABLE
                    or not isinstance(getattr(call, 'id', None), str) or not call.id):
                break
            attempt += 1
            # This known refusal happened before effects. Start one bounded
            # correction from its actual evidence, without synthesizing native
            # assistant history missing the provider's private reasoning state.
            # The public host gateway does not transport that provider state.
            evidence_context.append({"role": "user", "content": json.dumps({
                "rejected_candidate": arguments, "rejection": result,
                "instruction": "上一个候选因上述真实回执未被采用，没有新增目标或语音。请据此纠正并提交一次完整判断；不要把候选当成已执行。"
            }, ensure_ascii=False)})
            messages = [*messages[:2], *evidence_context]
        tasks = [t for t in tuple(getattr(plugin, "_speech_output_tasks", ()))
                 if t.get_loop() is asyncio.get_running_loop()]
        if tasks:
            await asyncio.gather(*tasks)
        return {**common, "submitted": True, "main_brain_acknowledged": result.get("acknowledged") is True,
                "transport_status": "accepted", "decision": arguments["decision"],
                "correction_attempts": attempt,
                "evidence_reads": evidence_reads,
                "model_wait_budget_seconds": round(wait_budget, 3),
                "max_output_tokens": output_budget,
                "decision_error": result.get('error') if result.get('error') in CORRECTABLE else
                                  "review_rejected" if result.get('is_error') else None,
                "reason": "structured_review_acknowledged" if result.get("acknowledged") else "structured_review_not_acknowledged"}
    except (APIConnectionError, APIStatusError, TransportError,
            TimeoutError, OSError, ValueError, TypeError, KeyError, IndexError, AttributeError) as error:
        category = model_failure_category(error)
        return {**refused("structured_review_unavailable"), "failure_category": category,
                "model_wait_budget_seconds": round(wait_budget, 3) if wait_budget is not None else None}
    finally:
        try:
            if client_opened and callable(getattr(gateway, "aclose", None)):
                await gateway.aclose()
        finally:
            try:
                if model_window:
                    try:
                        common['shared_model_window']['released']=port.end_model_inference(model_window['id'])
                    except (OSError,ValueError,sqlite3.Error):
                        common['shared_model_window'].update(released=False,release_error='review_store_unavailable')
            finally:
                with plugin._control_lock:
                    plugin._structured_review_busy = False


def record_delivery(port, result):
    """Small categorical UI receipt, never a model transcript or audio claim."""
    # Preserve earlier length/transport failures when a later heartbeat replaces
    # the UI's latest receipt. Never journal arguments, persona or free text.
    if result.get('model_attempts'):
        recorded = append_receipt(port, {'event': 'review_delivery', 'at': port.now(),
            'heartbeat_id': identifier(result.get('heartbeat_id'), r'[a-f0-9]{12}'),
            'submitted': result.get('submitted') is True,
            'main_brain_acknowledged': result.get('main_brain_acknowledged') is True,
            'model_attempt_ids': [row['attempt_id'] for row in result['model_attempts']],
            'decision': result.get('decision') if result.get('decision') in {'continue', 'insert', 'revise'} else None,
            'decision_error': result.get('decision_error'),
            'failure_category': result.get('failure_category'),
            'evidence_reads': result.get('evidence_reads', 0),
            'correction_attempts': result.get('correction_attempts', 0)})
        result['delivery_journal_recorded'] = recorded
    save_commentary(port.directory / "structured-review.json", {
        **result, "at": port.now(), "extra_commentary_model_calls": 0})
