"""Categorical plugin call diagnostics; no review payload or host internals."""

import asyncio
import json
import re
import threading
import time
import uuid
from functools import wraps

ERRORS = frozenset({
    "disabled_or_stopped", "configuration_changed_or_stopped", "review_superseded",
    "review_expired_or_changed", "review_expired_or_game_unavailable", "invalid_review",
    "invalid_lineage_offset", "reason_and_stage_goal_required", "review_choice_conflict",
    "fresh_inspection_required", "fresh_recovery_inspection_required", "critical_baseline_changed",
    "death_episode_superseded", "death_episode_mismatch", "death_reflection_required",
    "death_reflection_already_completed", "death_evidence_reference_mismatch",
    "intent_or_scope_changed", "intent_revision_changed", "game_not_running",
    "no_active_review_stage", "previous_intent_pending", "goal_already_active",
    "detail_is_read_only", "inspect_is_read_only", "evidence_reference_required",
    "meaningful_review_requires_decision",
})

_APPEND_LOCK = threading.Lock()


def model_failure_category(error):
    # Imports stay at the transport edge; no provider messages enter receipts.
    from openai import APIConnectionError, APIStatusError
    from plugin.sdk.plugin import TransportError

    return ('deadline_exceeded' if isinstance(error, TimeoutError) else
            'provider_http_error' if isinstance(error, APIStatusError) else
            'provider_connection_error' if isinstance(error, APIConnectionError) else
            'gateway_transport_error' if isinstance(error, TransportError) else 'invalid_or_unavailable_response')


def append_receipt(port, row):
    """Append a small pre-redacted receipt; diagnostics cannot change dispatch."""
    try:
        with _APPEND_LOCK:
            with (port.directory / 'structured-review-attempts.jsonl').open('a', encoding='utf-8') as stream:
                stream.write(json.dumps(row, ensure_ascii=True, separators=(',', ':'), allow_nan=False) + '\n')
        return True
    except OSError:
        return False


def _token_counts(usage):
    fields = {'prompt_tokens': getattr(usage, 'prompt_tokens', None),
              'completion_tokens': getattr(usage, 'completion_tokens', None),
              'total_tokens': getattr(usage, 'total_tokens', None),
              'reasoning_tokens': getattr(getattr(usage, 'completion_tokens_details', None), 'reasoning_tokens', None),
              'cached_tokens': getattr(getattr(usage, 'prompt_tokens_details', None), 'cached_tokens', None)}
    return {key: value for key, value in fields.items() if type(value) is int and 0 <= value <= 1_000_000_000}


async def model_attempt(port, review_id, records, request, *, wait_budget, output_budget, brief_checkpoint=False,shared_window_id=None):
    """Measure the actual gateway await, including calls cancelled before return.

    Only categorical/length/usage metadata survives. A provider-success receipt
    is not a tool transaction, canonical acknowledgement or native goal success.
    """
    started = time.monotonic()
    row = {'attempt_id': str(uuid.uuid4()), 'attempt': len(records) + 1, 'started_at': port.now(),
           'wait_budget_seconds': round(wait_budget, 3), 'max_output_tokens': output_budget,
           'brief_checkpoint_guidance': brief_checkpoint is True,
           'shared_model_window_id':identifier(shared_window_id,r'[a-f0-9]{32}'),
           'finish_reason': None, 'tool_count': 0, 'tool_names': [], 'usage': {}}
    row['start_journal_recorded'] = append_receipt(port, {'event': 'model_request_started',
        'heartbeat_id': identifier(review_id, r'[a-f0-9]{12}'), **row})
    try:
        response = await asyncio.wait_for(request, timeout=wait_budget)
        row['outcome'] = 'response_received'
        choices = getattr(response, 'choices', None)
        choice = choices[0] if isinstance(choices, (list, tuple)) and choices else None
        finish = getattr(choice, 'finish_reason', None)
        row['finish_reason'] = (finish if finish in {'stop', 'tool_calls', 'length', 'content_filter', 'function_call', None}
                                else 'other') if isinstance(finish, (str, type(None))) else 'other'
        message = getattr(choice, 'message', None)
        calls = getattr(message, 'tool_calls', None)
        if isinstance(calls, (list, tuple)):
            row['tool_count'] = len(calls)
            row['tool_names'] = [name if isinstance(name, str) and name in {'submit_game_review', 'read_game_review_evidence'} else 'other'
                                for name in (getattr(getattr(call, 'function', None), 'name', None) for call in calls[:8])]
        row['usage'] = _token_counts(getattr(response, 'usage', None))
        for key, value in [('content_characters', getattr(message, 'content', None)),
                           ('reasoning_characters', getattr(message, 'reasoning_content', None))]:
            row[key] = len(value) if isinstance(value, str) else None
        return response
    except BaseException as error:
        row['outcome'] = 'caller_cancelled' if isinstance(error, asyncio.CancelledError) else model_failure_category(error)
        raise
    finally:
        row['finished_at'] = port.now()
        row['elapsed_seconds'] = round(max(0, time.monotonic() - started), 3)
        recorded = append_receipt(port, {'event': 'model_attempt',
            'heartbeat_id': identifier(review_id, r'[a-f0-9]{12}'), **row})
        records.append({**row, 'journal_recorded': recorded})


def identifier(value, pattern):
    return value if isinstance(value, str) and re.fullmatch(pattern, value) else None


def emit(plugin, row):
    try:
        plugin.logger.info("minecraft_review_rpc %s", json.dumps(row, separators=(",", ":")))
    except Exception:
        pass  # Optional diagnostics must not alter return, exception or dispatch.


def review_completion(method):
    @wraps(method)
    async def wrapped(plugin, heartbeat_id, decision, *args, **kwargs):
        reflection = kwargs.get("death_reflection", args[5] if len(args) > 5 else None)
        reference = kwargs.get("evidence_ref", args[6] if len(args) > 6 else None)
        reference = reference or (reflection.get("evidence_ref") if isinstance(reflection, dict) else None)
        row = {"call_id": str(uuid.uuid4()), "review_id": identifier(heartbeat_id, r"[a-f0-9]{12}"),
               "phase": "inspect" if decision == "inspect" else "final" if decision in ("continue", "insert", "revise") else "invalid",
               "evidence_ref_present": identifier(reference, r"[a-f0-9]{24}") is not None}
        emit(plugin, {**row, "event": "entered"})
        try:
            result = await method(plugin, heartbeat_id, decision, *args, **kwargs)
        except BaseException as error:
            emit(plugin, {**row, "event": "raised", "error_category": "cancelled" if isinstance(error, asyncio.CancelledError) else "exception"})
            raise
        data = result if isinstance(result, dict) else {}
        error = data.get("error")
        emit(plugin, {**row, "event": "completed", "acknowledged": data.get("acknowledged") if type(data.get("acknowledged")) is bool else None,
                     "error_category": error if isinstance(error, str) and error in ERRORS else "other_error" if error or data.get("is_error") else None,
                     "request_id": identifier(data.get("request_id"), r"[a-fA-F0-9]{8}(?:-[a-fA-F0-9]{4}){3}-[a-fA-F0-9]{12}"),
                     "evidence_ref_present": row["evidence_ref_present"] or identifier(data.get("evidence_ref"), r"[a-f0-9]{24}") is not None})
        return result
    return wrapped
