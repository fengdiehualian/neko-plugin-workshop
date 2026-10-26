"""Exercise the SDK registry and active heartbeat, without a host or model."""

import asyncio
import importlib
import json
from datetime import datetime, timezone
from pathlib import Path
from queue import SimpleQueue
from unittest.mock import AsyncMock

import httpx
import pytest
from agent_mc_autonomy.host_context import HostContextPort
from agent_mc_autonomy.review import ReviewPolicy
from plugin.sdk.plugin import Err, Ok
from plugin.sdk.plugin.llm_tool import entry_id_for_tool
from test_death_reflection import setup_death
from test_heartbeat import setup
from test_speech_output import prepare


def registered_review(plugin):
    return plugin.collect_entries()[entry_id_for_tool("minecraft_autonomy_review")]


def test_active_registered_review_delivers_exact_bundled_skill(plugin_instance, monkeypatch, run_async):
    port, snapshot, push = setup(plugin_instance, monkeypatch)
    port.observe(snapshot)
    entry = registered_review(plugin_instance)
    skill = (Path(__file__).parents[1] / "skills/gameplay-speaking.md").read_text(encoding="utf-8")
    tool = next(row for row in plugin_instance.list_llm_tools() if row["name"] == "minecraft_autonomy_review")
    assert skill in tool["description"] == entry.meta.description
    queue = SimpleQueue()
    monkeypatch.setattr(plugin_instance._host_ctx, "message_queue", queue, raising=False)
    plugin_instance.__class__(plugin_instance._host_ctx)
    messages = []
    while not queue.empty():
        messages.append(queue.get_nowait())
    registration = next(row for row in messages if row["type"] == "LLM_TOOL_REGISTER" and row["name"] == tool["name"])
    assert registration["plugin_id"] == "minecraft_autonomous" and registration["description"] == tool["description"]
    offered = run_async(plugin_instance.collect_entries()["game_decision_heartbeat"].handler())
    assert isinstance(offered, Ok) and offered.value["submitted"]
    wire = push.call_args.kwargs
    assert wire["source"] == "minecraft_autonomous" and wire["ai_behavior"] == "respond"
    assert "inspect" in wire["parts"][0]["text"] and skill not in json.dumps(wire, ensure_ascii=False)
    rid = offered.value["heartbeat_id"]
    inspected = run_async(entry.handler(heartbeat_id=rid, decision="inspect"))
    assert inspected["commentary"]["skill_source"] == "registered_tool_description"
    assert inspected["completion_contract"]["required"]["heartbeat_id"] == rid
    assert skill not in json.dumps(inspected, ensure_ascii=False)
    assert not inspected["acknowledged"] and not port.mailbox.ready()
    final = run_async(entry.handler(heartbeat_id=rid, decision="continue", reason="Keep useful work"))
    assert final["acknowledged"] and "本轮重点证据" not in final["commentary"]
    assert skill not in json.dumps(final, ensure_ascii=False)
    assert len(json.dumps(final, ensure_ascii=False).encode()) < 1600


def test_registered_stop_and_resume_envelopes_preserve_guards(plugin_instance, monkeypatch, run_async):
    port, snapshot, push = setup(plugin_instance, monkeypatch)
    port.observe(snapshot)
    plugin_instance._operator_state(True)
    entries = plugin_instance.collect_entries()
    stopped = run_async(entries["game_decision_heartbeat"].handler())
    assert isinstance(stopped, Ok) and stopped.value["reason"] == "explicit_stop"
    refused = run_async(registered_review(plugin_instance).handler(heartbeat_id=port.state()["review"]["id"], decision="continue"))
    assert refused["is_error"] and refused["error"] == "disabled_or_stopped"
    resume = run_async(entries["resume_observation"].handler())
    assert isinstance(resume, Err) and resume.error["code"] == "external_runtime_required"
    assert not push.called and not port.mailbox.ready()


@pytest.mark.parametrize("phase", ["completed", "blocked"])
def test_registered_native_evidence_is_bounded_and_original_is_queryable(plugin_instance, monkeypatch, run_async, phase):
    port, snapshot, push = setup(plugin_instance, monkeypatch)
    original = "Actual acquired oak" if phase == "completed" else "Actual missing ingredient"
    snapshot["native_outcomes"] = [{"tool": "mc_do", "execution_id": "synthetic-task", "phase": phase,
        "receipt": original + " detail" * 1000, "observation_only": False, "goal_verified": False,
        "original_event": {"run": "synthetic-run", "cursor": 7}}]
    (port.directory / "status.json").write_text(json.dumps(snapshot), encoding="utf-8")
    port.observe(snapshot)
    offered = run_async(plugin_instance.collect_entries()["game_decision_heartbeat"].handler())
    assert isinstance(offered, Ok)
    text = push.call_args.kwargs["parts"][0]["text"]
    assert original in text and "synthetic-run:7" in text and len(text.encode()) < 750
    entry = registered_review(plugin_instance)
    inspected = run_async(entry.handler(heartbeat_id=offered.value["heartbeat_id"], decision="inspect"))
    selected = inspected["commentary"]["本轮重点证据"]
    assert original in json.dumps(selected) and len(json.dumps(selected, ensure_ascii=False, separators=(",", ":")).encode()) <= 600
    assert not inspected["native_outcomes"][0]["goal_verified"]
    page = run_async(entry.handler(heartbeat_id=offered.value["heartbeat_id"], decision="inspect",
        evidence_ref=inspected["evidence_ref"], detail_path=["evidence", "native_outcomes", 0, "receipt"]))
    assert page["historical"] and original in page["json_chunk"]
    assert "commentary" not in page and "speech_contract" not in page


def test_registered_idle_unchanged_retry_and_optional_context(plugin_instance, monkeypatch, run_async):
    port, snapshot, push = setup(plugin_instance, monkeypatch)
    raw = plugin_instance.config.dump.return_value
    raw["heartbeat"]["idle_presence_seconds"] = 45
    raw["host_context"] = {"enabled": False}
    port.configure(ReviewPolicy(enabled=True))
    port.observe(snapshot)
    entries, entry = plugin_instance.collect_entries(), registered_review(plugin_instance)
    rid = run_async(entries["review_game"].handler()).value["heartbeat_id"]
    inspected = run_async(entry.handler(heartbeat_id=rid, decision="inspect"))
    assert inspected["context_request"]["reason"] == "disabled"
    run_async(entry.handler(heartbeat_id=rid, decision="continue", reason="Continue"))
    unchanged = run_async(entries["game_decision_heartbeat"].handler())
    assert isinstance(unchanged, Ok) and not unchanged.value["submitted"] and push.call_count == 1
    port.now = lambda: 10045
    snapshot["updated_at"] = datetime.fromtimestamp(port.now(), timezone.utc).isoformat()
    (port.directory / "status.json").write_text(json.dumps(snapshot), encoding="utf-8")
    port.observe(snapshot)
    idle = run_async(entries["game_decision_heartbeat"].handler()).value
    assert idle["trigger"] == "idle_presence" and push.call_args.kwargs["ai_behavior"] == "respond"
    idle_detail = run_async(entry.handler(heartbeat_id=idle["heartbeat_id"], decision="inspect"))
    assert "本轮重点证据" not in idle_detail["commentary"]
    # Retry the same unacknowledged review using the real framework offer state.
    port.now = lambda: 10136
    snapshot["updated_at"] = datetime.fromtimestamp(port.now(), timezone.utc).isoformat()
    (port.directory / "status.json").write_text(json.dumps(snapshot), encoding="utf-8")
    port.observe(snapshot)
    retry = run_async(entries["game_decision_heartbeat"].handler()).value
    assert retry["heartbeat_id"] == idle["heartbeat_id"] and retry["submitted"]
    assert "延续刚才的话题" in push.call_args.kwargs["parts"][0]["text"]


def test_registered_death_keeps_evidence_and_requires_honest_completion(plugin_instance, monkeypatch, run_async):
    _, port, review, episode = setup_death(plugin_instance, monkeypatch)
    plugin_instance.config.dump.return_value['heartbeat']['structured_reviews'] = False
    entry = registered_review(plugin_instance)
    port.now = lambda: 10090
    snapshot = json.loads((port.directory / "status.json").read_text(encoding="utf-8"))
    snapshot["updated_at"] = datetime.fromtimestamp(port.now(), timezone.utc).isoformat()
    (port.directory / "status.json").write_text(json.dumps(snapshot), encoding="utf-8")
    port.observe(snapshot)
    offered = run_async(plugin_instance.collect_entries()["game_decision_heartbeat"].handler())
    assert isinstance(offered, Ok) and offered.value["submitted"]
    inspected = run_async(entry.handler(heartbeat_id=review["id"], decision="inspect"))
    assert inspected["commentary"]["本轮重点证据"]["death_episode"]["id"] == episode["id"]
    rejected = run_async(entry.handler(heartbeat_id=review["id"], decision="continue", reason="Recover"))
    assert rejected["error"] == "death_reflection_required" and not port.mailbox.ready()
    final = run_async(entry.handler(heartbeat_id=review["id"], decision="continue", reason="Recover",
        death_reflection={"evidence_ref": inspected["evidence_ref"], "outcome": "deferred", "reason": "Cause is unknown"}))
    assert final["acknowledged"] and not final["reflection_pending"]
    assert "本轮重点证据" not in final["commentary"]


@pytest.mark.parametrize("outcome", ["accepted", "rejected", "unknown", "busy", "disabled", "absent"])
def test_registered_current_speech_receipt_and_retry_never_duplicate(plugin_instance, monkeypatch, run_async, outcome):
    speech, port, _, review, raw = prepare(plugin_instance, monkeypatch)
    entry = registered_review(plugin_instance)
    client = httpx.AsyncClient
    calls = []

    async def handler(request):
        calls.append(request.method)
        if request.method == "GET":
            return httpx.Response(200, json={"paths": {"/api/game/{game_type}/speak": {"post": {}}}})
        if outcome == "unknown":
            raise httpx.ReadTimeout("synthetic unknown")
        return httpx.Response(200, json={"ok": outcome == "accepted", "reason": "busy"})

    monkeypatch.setattr(speech.httpx, "AsyncClient", lambda **kw: client(transport=httpx.MockTransport(handler), **kw))
    if outcome == "disabled":
        raw["speech_output"]["enabled"] = False

    async def scenario():
        if outcome == "busy":
            plugin_instance._speech_output_tasks = {asyncio.get_running_loop().create_future()}
        payload = {"heartbeat_id": review["id"], "decision": "insert", "reason": "Need shelter", "goal": "Build shelter"}
        if outcome != "absent":
            payload["spoken_text"] = "先搭个小屋吧。"
        inspected = await entry.handler(heartbeat_id=review["id"], decision="inspect")
        assert inspected["speech_contract"]["status"] == "absent"
        first = await entry.handler(**payload)
        assert first["acknowledged"] and first["delivery"] == "queued" and not calls
        if outcome in {"accepted", "rejected", "unknown"}:
            assert first["speech_contract"]["status"] == "enqueued"
            await asyncio.gather(*plugin_instance._speech_output_tasks)
        repeat = await entry.handler(**payload)
        assert repeat["speech_contract"]["status"] == outcome
        assert not repeat["speech_contract"]["playback_verified"]
        guidance = repeat["speech_contract"]["instruction"]
        if outcome == "accepted":
            assert "不复述" in guidance and "受理不等于已播放" in guidance
        elif outcome == "unknown":
            assert "不重投或复述" in guidance
        else:
            assert "未排入则用正常最终回复" in guidance
        assert repeat["request_id"] == first["request_id"]
        assert calls == (["GET", "POST"] if outcome in {"accepted", "rejected", "unknown"} else [])
        if outcome == "busy":
            for task in plugin_instance._speech_output_tasks:
                task.cancel()
            plugin_instance._speech_output_tasks.clear()

    run_async(scenario())


def test_registered_context_uses_same_brain_selected_memories(plugin_instance, monkeypatch, run_async):
    port, snapshot, _ = setup(plugin_instance, monkeypatch)
    port.observe(snapshot)
    rid = port.offer()["review"]["id"]
    host = importlib.import_module(plugin_instance.__class__.__module__ + ".neko_host_context")
    monkeypatch.setattr(host, "character_profile", AsyncMock(return_value={"agent_name": "TestRole", "persona": "Synthetic public curiosity",
        "persona_source": "character_profile"}))
    entry = registered_review(plugin_instance)
    inspected = run_async(entry.handler(heartbeat_id=rid, decision="inspect"))
    assert not inspected["context_request"]["persona_needed"]
    selected = {"current_intent": "Explore nearby", "memories": [{"id": "synthetic-memory", "source": "selected", "text": "A quiet clearing"}]}
    final = run_async(entry.handler(heartbeat_id=rid, decision="continue", reason="Continue", context=selected))
    assert final["acknowledged"]
    stored = HostContextPort(port.directory, now=port.now).read(port.mailbox.intent_revision())
    assert stored["stable"]["persona"] == "Synthetic public curiosity"
    assert stored["dynamic"]["memories"] == selected["memories"]
    assert "Synthetic public curiosity" not in json.dumps(final)
