"""Failure evidence only: real prepare entry, isolated IO, no live OCR/chat."""
import asyncio
import copy
import importlib
import json
from types import SimpleNamespace

import pytest
from plugin.plugins.hsr_companion.runtime import prepare_trace as trace
from plugin.plugins.hsr_companion.runtime.diagnostics import build_diagnostic_report
from plugin.plugins.hsr_companion.runtime.observation_store import ObservationSnapshot
from plugin.plugins.hsr_companion.runtime.query_coordinator import QueryObservation
from plugin.plugins.hsr_companion.tests.test_current_observation import plugin_fixture
from plugin.plugins.hsr_companion.tests.test_diagnostics import inputs
from plugin.plugins.hsr_companion.tests.test_stage2_assessment import relic
from plugin.sdk.plugin import unwrap

QUERY = "这件遗器给真珠用怎么样？"
DECLARATION = {"scope": "current_relic", "subject_state": "specified", "character": "真珠"}


def last(owner):
    return trace.public_records(owner._prepare_call_records)[-1]


@pytest.mark.parametrize("kwargs,code,stage", [
    ({"screen_fields": ["invalid"]}, "invalid_screen_fields", "query_plan"),
    ({"response_detail": "invalid"}, "invalid_response_detail", "query_plan"),
    ({"consultation": {**DECLARATION, "topics": []}}, "invalid_consultation_topics", "guidance_plan"),
    ({"consultation": {**DECLARATION, "answer_parts": []}}, "invalid_consultation_answer_parts", "guidance_plan"),
    ({"consultation": {**DECLARATION, "scope": "current_cone"}}, "scope_intent_conflict", "guidance_plan"),
])
def test_formal_parameter_errors_have_real_arguments_stage_and_persistent_receipt(tmp_path, kwargs, code, stage):
    owner, values, sent = plugin_fixture(tmp_path)

    async def run():
        result = await owner.hsr_companion_prepare(query=QUERY, intent="assess_relic", **kwargs)
        record = last(owner)
        assert result["is_error"] and record["outcome"] == "failed"
        assert record["error_code"] == code and record["exception_type"] == "ValueError"
        assert record["stage"] == stage and record["last_completed_stage"] != stage
        assert record["request_id"] == owner._service_request["request_id"]
        assert result["output"]["failure_receipt"]["request_id"] == record["request_id"]
        assert record["persistence"] == "saved" and values[trace.STORE_KEY]
        assert record["arguments"]["query"]["type"] == "str"
        assert QUERY not in json.dumps(values[trace.STORE_KEY], ensure_ascii=False)
        assert "equipment_answer" not in result and not sent

    asyncio.run(run())


@pytest.mark.parametrize("supplied,expected", [({}, "omitted"), ({"consultation": None}, "provided"),
    ({"consultation": {}}, "provided"), ({"consultation": {**DECLARATION, "topics": []}}, "provided")])
def test_raw_capture_precedes_defaults_and_validation(supplied, expected):
    owner = SimpleNamespace()
    captured = trace.capture_arguments(owner, supplied)
    assert captured["consultation"]["presence"] == expected
    if "consultation" in supplied:
        value = supplied["consultation"]
        assert captured["consultation"]["type"] == ("null" if value is None else "dict")
        if isinstance(value, dict) and "topics" in value:
            node = captured["consultation"]["fields"]["topics"]
            assert node["type"] == "list" and node["items"] == []


def resolver(monkeypatch, owner, value):
    async def resolve(plugin, plan, **kwargs):
        return QueryObservation(ObservationSnapshot.select(plugin, value, fields=plan.fields),
                                {"outcome": "timeout" if not value.get("fresh") else "reused", "stages": []},
                                0, kwargs["generation"])
    monkeypatch.setattr(importlib.import_module(type(owner).__module__), "resolve_query", resolve)


def test_missing_read_is_not_an_exception_and_does_not_write_failure_store(tmp_path, monkeypatch):
    owner, values, sent = plugin_fixture(tmp_path)
    resolver(monkeypatch, owner, {"fresh": False, "screen_facts": {"facts": []}, "characters": [],
        "controlled_character": None, "scene": {"label": "未知", "verified": False}, "page_type": "unknown",
        "observed_at": None, "instruction": ""})
    module = importlib.import_module(type(owner).__module__)
    monkeypatch.setattr(module, "arm_followup", lambda *a, **kw: {"pending": True, "reason": "waiting"})

    async def run():
        result = await owner.hsr_companion_prepare(query=QUERY, intent="assess_relic", consultation=DECLARATION)
        record = last(owner)
        assert not result.get("is_error") and record["outcome"] == "incomplete"
        assert record["missing_fields"] == ["equipment"] and record["followup_pending"] is True
        assert record["error_code"] is None and record["exception_type"] is None
        assert trace.STORE_KEY not in values and not sent

    asyncio.run(run())


@pytest.mark.parametrize("error", [RuntimeError("SECRET internal state"), KeyError("SECRET key")])
def test_internal_exception_after_read_keeps_selection_without_leaking_content(tmp_path, monkeypatch, error):
    owner, values, sent = plugin_fixture(tmp_path)
    value = relic(serial=7)
    resolver(monkeypatch, owner, value)
    module = importlib.import_module(type(owner).__module__)

    def broken(*args, **kwargs):
        raise error

    monkeypatch.setattr(module, "build_guidance", broken)

    async def run():
        if isinstance(error, RuntimeError):
            result = await owner.hsr_companion_prepare(query=QUERY, intent="assess_relic", consultation=DECLARATION)
            assert result["is_error"]
        else:
            with pytest.raises(KeyError):
                await owner.hsr_companion_prepare(query=QUERY, intent="assess_relic", consultation=DECLARATION)
        record = last(owner)
        assert record["stage"] == "evaluation" and record["outcome"] == "failed"
        assert record["exception_type"] == type(error).__name__
        assert record["selection"]["serial"] == 7 and record["selection"]["evidence_sha256"]
        assert record["origin"] and all(".py" in row["file"] for row in record["origin"])
        assert "SECRET" not in json.dumps(values[trace.STORE_KEY]) and not sent

    asyncio.run(run())


def test_trace_survives_failed_storage_and_cancellation_without_changing_original_error():
    @trace.trace_prepare
    async def failing(owner, **kwargs):
        trace.prepare_stage("observation")
        raise KeyError("not exported")

    @trace.trace_prepare
    async def cancelled(owner, **kwargs):
        trace.prepare_stage("observation")
        raise asyncio.CancelledError()

    async def bad_store(*args):
        raise OSError("SECRET disk path")

    owner = SimpleNamespace(_store_value=bad_store, _get_store=bad_store)

    async def run():
        with pytest.raises(KeyError):
            await failing(owner)
        assert last(owner)["error_code"] == "internal_exception"
        assert last(owner)["persistence"] == "unavailable"
        with pytest.raises(asyncio.CancelledError):
            await cancelled(owner)
        assert last(owner)["outcome"] == "cancelled" and last(owner)["error_code"] == "request_cancelled"

    asyncio.run(run())


def test_diagnosis_recovers_failure_after_restart_without_ocr_or_messages(tmp_path):
    owner, values, sent = plugin_fixture(tmp_path)

    async def run():
        await owner.hsr_companion_prepare(query=QUERY, intent="assess_relic", response_detail="wrong")
        restored, restored_values, _ = plugin_fixture(tmp_path)
        restored_values.update(copy.deepcopy(values))
        restored._avatars = SimpleNamespace(status=lambda: {"installed": True})
        restored._message_path_ready = lambda: {"available": True}

        async def health():
            return {"connected": True}
        restored._assistant_status = health
        before = copy.deepcopy(restored_values)
        report = unwrap(await restored.diagnose_companion())["diagnostic"]
        diagnostic = json.loads(report["copy_text"])
        records = diagnostic["prepare_calls"]["records"]
        assert records[-1]["error_code"] == "invalid_response_detail"
        assert restored_values == before and not sent

    asyncio.run(run())


def test_public_diagnostic_cannot_export_arbitrary_parameter_or_exception_text():
    args = trace.capture_arguments(SimpleNamespace(), {"query": "SECRET query", "intent": "SECRET intent",
        "consultation": {"scope": "current_relic", "topics": ["relic", "SECRET"], "extra_token": "SECRET"}})
    assert "SECRET" not in json.dumps(args)
    report = json.loads(build_diagnostic_report(**inputs(), prepare_calls={"records": [{
        "stage": "SECRET", "exception_type": "SECRET", "error_code": "SECRET",
        "arguments": {"intent": {"presence": "provided", "type": "str", "value": "SECRET"}},
        "origin": [{"file": "C:/SECRET.py", "function": "SECRET", "line": 1}],
    }]} )["copy_text"])
    assert "SECRET" not in json.dumps(report)


def test_preinitialization_exception_is_also_located(tmp_path):
    owner, values, _ = plugin_fixture(tmp_path)

    def fail():
        raise TypeError("SECRET initialization")
    owner._note_speech_query = fail

    async def run():
        with pytest.raises(TypeError):
            await owner.hsr_companion_prepare(query=QUERY)
        assert last(owner)["stage"] == "initialization"
        assert values[trace.STORE_KEY][-1]["exception_type"] == "TypeError"

    asyncio.run(run())


def test_storage_timeout_does_not_hide_original_failure(monkeypatch):
    monkeypatch.setattr(trace, "PERSIST_TIMEOUT", .01)
    blocked = asyncio.Event()

    async def get(*args):
        await blocked.wait()

    @trace.trace_prepare
    async def fail(owner, **kwargs):
        trace.prepare_stage("observation")
        raise KeyError("original")

    async def run():
        owner = SimpleNamespace(_get_store=get)
        with pytest.raises(KeyError, match="original"):
            await asyncio.wait_for(fail(owner), timeout=1)
        assert last(owner)["persistence"] == "unavailable"
        assert last(owner)["error_code"] == "internal_exception"

    asyncio.run(run())


def test_concurrent_calls_have_independent_stages_and_request_ids():
    @trace.trace_prepare
    async def call(owner, *, intent, barrier, **kwargs):
        trace.prepare_stage("observation" if intent == "assess_relic" else "guidance_plan")
        await barrier.wait()
        raise RuntimeError("injected")

    async def run():
        owner = SimpleNamespace()
        barrier = asyncio.Event()
        first = asyncio.create_task(call(owner, intent="assess_relic", barrier=barrier))
        second = asyncio.create_task(call(owner, intent="assess_cone", barrier=barrier))
        await asyncio.sleep(0)
        barrier.set()
        results = await asyncio.gather(first, second, return_exceptions=True)
        assert all(isinstance(r, RuntimeError) for r in results)
        records = trace.public_records(owner._prepare_call_records)
        assert len({r["request_id"] for r in records}) == 2
        assert {r["stage"] for r in records} == {"observation", "guidance_plan"}
        assert trace.request_id(owner) is None

    asyncio.run(run())


def test_bounded_journal_and_successful_entry_do_not_write_or_add_answer_fields():
    payload = {"query_answer": {"state": "ready", "missing_fields": []},
               "request_completion": {"state": "complete", "service_ready": True}}

    @trace.trace_prepare
    async def call(owner, **kwargs):
        return copy.deepcopy(payload)

    async def run():
        owner = SimpleNamespace()
        for _ in range(8):
            assert await call(owner, query=QUERY) == payload
        assert len(owner._prepare_call_records) == trace.LIMIT
        assert all(r["outcome"] == "complete" and r["persistence"] == "memory_only" for r in trace.public_records(owner._prepare_call_records))

    asyncio.run(run())


def test_trace_capture_is_bounded_even_for_malformed_nested_parameters():
    value = {k: ["SECRET"] * 100 for k in trace.NESTED}
    captured = trace.capture_arguments(SimpleNamespace(), {"consultation": value})
    assert len(json.dumps(captured)) < 30000
    record = trace.public_records([{"arguments": captured}])[0]
    assert "SECRET" not in json.dumps(record)
    assert len(json.dumps(build_diagnostic_report(**inputs(), prepare_calls={"records": [record] * 4}))) < 131072


def test_json_invalid_types_are_diagnostic_data_not_new_exceptions():
    records = trace.public_records([{"stage": [], "error_code": {}, "exception_type": ["SECRET"],
        "origin": {"SECRET": "not a stack"}, "arguments": {"intent": {"type": [], "value": {}}}}])
    assert records[0]["error_code"] is None and "SECRET" not in json.dumps(records)


def test_original_async_signature_and_tool_metadata_are_preserved():
    import inspect

    from plugin.plugins.hsr_companion import HsrCompanionPlugin
    handler = HsrCompanionPlugin.hsr_companion_prepare
    assert inspect.iscoroutinefunction(handler)
    assert "consultation" in inspect.signature(handler).parameters
    assert inspect.signature(handler).parameters["screen_fields"].default is None
    assert handler.__neko_llm_tool_meta__.name == "hsr_companion_prepare"
    assert handler.__neko_llm_tool_meta__.parameters["properties"]["consultation"]["properties"]["topics"]["minItems"] == 1


def test_optional_trace_hashing_cannot_break_valid_answer(monkeypatch, tmp_path):
    owner, _, _ = plugin_fixture(tmp_path)
    payload = {"query_answer": {"state": "ready"}}

    def broken(*args):
        raise ValueError("hash unavailable")

    @trace.trace_prepare
    async def call(owner, **kwargs):
        trace.prepare_stage("fact_projection", snapshot=SimpleNamespace(epoch=2, serial=3), visual=relic())
        return payload

    monkeypatch.setattr(trace, "_digest", broken)
    result = asyncio.run(call(owner))
    assert result is payload and last(owner)["outcome"] == "complete"
    assert not last(owner)["selection_hash_available"]


def test_missing_query_argument_is_located_without_starting_observation(tmp_path):
    owner, values, sent = plugin_fixture(tmp_path)

    async def run():
        with pytest.raises(TypeError):
            await owner.hsr_companion_prepare(intent="assess_relic")
        record = last(owner)
        assert record["stage"] == "argument_binding" and record["exception_type"] == "TypeError"
        assert record["arguments"]["query"]["presence"] == "omitted"
        assert values[trace.STORE_KEY] and not sent

    asyncio.run(run())


def test_discussion_dependencies_are_recorded_without_copying_old_facts():
    import time
    owner = SimpleNamespace(_discussion_generation=3, _guidance_context={
        "at": time.monotonic() - 601, "kind": "build", "execution_intent": "assess_relic",
        "request_focus": "equipment", "character_ids": ["character.pearl"], "old_fact": "SECRET"})

    @trace.trace_prepare
    async def call(owner, **kwargs):
        return {}

    asyncio.run(call(owner, intent="continue_consultation"))
    record = last(owner)
    assert record["context"]["subject_ids"] == ["character.pearl"]
    assert record["context"]["age_seconds"] >= 601
    assert record["context"]["execution_intent"] == "assess_relic"
    assert "SECRET" not in json.dumps(record)


def test_wrong_but_structural_routing_code_is_kept_as_received():
    args = trace.capture_arguments(SimpleNamespace(), {"intent": "wrong_intent", "screen_fields": ["invalid_field"]})
    assert args["intent"]["value"] == "wrong_intent"
    assert args["screen_fields"]["items"][0]["value"] == "invalid_field"
    exported = trace.public_records([{"arguments": args}])[0]["arguments"]
    assert exported["intent"]["value"] == "wrong_intent"
    assert exported["screen_fields"]["items"][0]["value"] == "invalid_field"


@pytest.mark.parametrize("exception,code", [(TimeoutError("not retained"), "request_timeout"),
    (type("BackendReadError", (Exception,), {})("not retained"), "internal_exception")])
def test_unexpected_backend_types_and_timeouts_keep_their_stage(exception, code):
    @trace.trace_prepare
    async def call(owner, **kwargs):
        trace.prepare_stage("observation")
        raise exception

    async def run():
        owner = SimpleNamespace()
        with pytest.raises(type(exception)):
            await call(owner)
        record = last(owner)
        assert record["stage"] == "observation" and record["error_code"] == code
        assert record["exception_type"] == type(exception).__name__

    asyncio.run(run())


@pytest.mark.parametrize("value", [False, True, 0, 1.5])
def test_malformed_scalar_values_are_not_collapsed(value):
    captured = trace.capture_arguments(SimpleNamespace(), {"screen_fields": value})
    record = trace.public_records([{"arguments": captured}])[0]
    assert record["arguments"]["screen_fields"]["value"] == value
    assert type(record["arguments"]["screen_fields"]["value"]) is type(value)


def test_pending_reply_keeps_the_original_request_reference_but_not_message():
    captured = trace.capture_arguments(SimpleNamespace(), {"pending_reply": {
        "request_id": "a" * 32, "relation": "confirm", "player_message": "SECRET player acknowledgment"}})
    record = trace.public_records([{"arguments": captured}])[0]
    fields = record["arguments"]["pending_reply"]["fields"]
    assert fields["request_id"]["value"] == "a" * 32 and fields["relation"]["value"] == "confirm"
    assert "SECRET" not in json.dumps(record)


@pytest.mark.parametrize("value", [10**400, float("nan")])
def test_out_of_bound_numbers_are_hashed_without_breaking_capture(value):
    captured = trace.capture_arguments(SimpleNamespace(), {"screen_fields": value})
    exported = trace.public_records([{"arguments": captured}])[0]["arguments"]["screen_fields"]
    assert exported["sha256"] and "value" not in exported
