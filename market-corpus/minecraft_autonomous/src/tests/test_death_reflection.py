import json
from copy import deepcopy
from datetime import datetime, timezone

import jsonschema
import pytest
from agent_mc_autonomy.death_memory import DeathMemory
from plugin.sdk.plugin.llm_tool import collect_llm_tool_methods
from test_speech_output import prepare

SCOPE = {"realm": "synthetic-realm", "account": "synthetic-account", "agent": "synthetic-player"}


def setup_death(plugin, monkeypatch):
    speech, port, snapshot, _, _ = prepare(plugin, monkeypatch)
    snapshot["game_identity"] = SCOPE
    snapshot["facts"]["parts"].extend([
        {"key": "identity", "text": json.dumps(SCOPE)},
        {"key": "deathLifecycle", "text": json.dumps({"phase": "alive", "episodeId": "death-actual"})},
        {"key": "recoveryState", "text": json.dumps({"inventorySynced": True, "inventory": [],
            "position": {"x": 100, "y": 64, "z": 2}, "dimension": "overworld"})}])
    event = {"run": "synthetic-run", "cursor": 1, "ts": "2026-10-15T01:02:03Z", "meta": {
        "death": {"id": "death-actual", "scope": SCOPE, "position": {"x": 1, "y": 64, "z": 2}}}}
    episode = DeathMemory(port.directory).record(event, snapshot["facts"], [], [], [])
    snapshot["death_episode"] = episode
    (port.directory / "status.json").write_text(json.dumps(snapshot), encoding="utf-8")
    port.observe(snapshot)
    review = port.offer()["review"]
    return speech, port, review, episode


def metadata(plugin):
    return next(meta for meta, _ in collect_llm_tool_methods(plugin) if meta.name == "minecraft_autonomy_review")


def test_public_sdk_legacy_lesson_requires_evidence_but_short_reference_and_deferral_remain_valid(plugin_instance):
    schema = metadata(plugin_instance).parameters
    base = {"heartbeat_id": "a" * 12, "decision": "continue", "reason": "Recovery"}
    lesson = {"episode_id": "original-episode", "outcome": "lesson", "lesson": "Inspect resources", "applicability": "After respawn"}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({**base, "death_reflection": lesson}, schema)
    jsonschema.validate({**base, "death_reflection": {**lesson, "evidence": "original-episode; actual synchronized inventory"}}, schema)
    jsonschema.validate({**base, "death_reflection": {key: value for key, value in {**lesson, "evidence_ref": "b" * 24}.items() if key != "episode_id"}}, schema)
    jsonschema.validate({**base, "death_reflection": {"episode_id": "original-episode", "outcome": "deferred", "reason": "Cause unavailable"}}, schema)


@pytest.mark.parametrize("outcome", ["lesson", "deferred"])
def test_sdk_schema_forwarding_reflection_and_same_brain_speech_order(plugin_instance, run_async, monkeypatch, outcome):
    speech, port, review, episode = setup_death(plugin_instance, monkeypatch)
    meta = metadata(plugin_instance)
    jsonschema.Draft202012Validator.check_schema(meta.parameters)
    payload = {"heartbeat_id": review["id"], "decision": "insert", "goal": "Gather wood near the new spawn",
               "reason": "Fresh inventory is empty; old route safety is unknown, so start nearby",
               "spoken_text": "哎呀，东西掉光了，先在附近找木头，再看看旧路线安不安全。"}
    jsonschema.validate(payload, meta.parameters)  # Pending state is known only at runtime.
    assert "必须附death_reflection" in meta.description
    run_async(plugin_instance.minecraft_autonomy_review(review["id"], "inspect"))
    before = deepcopy(port.state())
    rejected = run_async(plugin_instance.minecraft_autonomy_review(**payload))
    assert rejected["error"] == "death_reflection_required"
    assert port.state() == before
    assert not getattr(plugin_instance, "_speech_output_tasks", None)
    assert not (port.directory / "speech-output.json").exists()
    payload["death_reflection"] = {"episode_id": episode["id"], "outcome": outcome}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(payload, meta.parameters)
    if outcome == "lesson":
        payload["death_reflection"].update(lesson="Recheck recovery resources before choosing the old route",
            applicability="After this respawn; distance does not prove route safety",
            evidence=episode["id"] + "; current synchronized inventory is empty")
    else:
        payload["death_reflection"]["reason"] = "Cause and route hazards are unknown; inspect them before inferring a lesson"
    jsonschema.validate(payload, meta.parameters)
    order = []
    original_enqueue, original_submit = speech.enqueue_speech, port.mailbox.submit
    def enqueue(*args, **kwargs):
        assert port.mailbox.get(port.request_id(review["id"])) is None
        order.append("enqueue")
        return {"status": "enqueued", "playback_verified": False}
    def submit(*args, **kwargs):
        order.append("goal")
        return original_submit(*args, **kwargs)
    monkeypatch.setattr(speech, "enqueue_speech", enqueue)
    monkeypatch.setattr(port.mailbox, "submit", submit)
    result = run_async(plugin_instance.minecraft_autonomy_review(**payload))
    assert result["acknowledged"] and result["delivery"] == "queued" and not result["reflection_pending"]
    assert order == ["enqueue", "goal"]
    assert run_async(plugin_instance.minecraft_autonomy_review(**payload))["request_id"] == result["request_id"]
    assert order == ["enqueue", "goal"]
    assert DeathMemory(port.directory).entries(SCOPE, pending=True) == []
    assert callable(original_enqueue)  # Existing output scheduling/independence has its own real transport tests.


def test_adapter_legacy_inspect_completion_preserves_goal_and_speech(plugin_instance, run_async, monkeypatch):
    speech, port, review, episode = setup_death(plugin_instance, monkeypatch)
    choice = {"decision": "continue", "reason": "Earlier accepted recovery", "goal": ""}
    original = {"acknowledged": True, "decision": "continue", "dispatched": False, "verified": False,
                "reflection_pending": True, "speech": {"status": "accepted", "playback_verified": False}}
    with port.transaction() as state:
        state["review"].update(choice=choice, result=deepcopy(original))
    inspected = run_async(plugin_instance.minecraft_autonomy_review(review["id"], "inspect"))
    assert inspected["legacy_reflection_completion"]["choice"] == choice
    def forbidden(*a, **kw):
        raise AssertionError("No legacy speech or goal replay")
    monkeypatch.setattr(speech, "enqueue_speech", forbidden)
    monkeypatch.setattr(port.mailbox, "submit", forbidden)
    payload = {"heartbeat_id": review["id"], **choice, "spoken_text": "This must not replay",
        "death_reflection": {"episode_id": episode["id"], "outcome": "deferred", "reason": "Await actual recovery evidence"}}
    result = run_async(plugin_instance.minecraft_autonomy_review(**payload))
    assert result["speech"] == original["speech"] and not result["reflection_pending"]
    assert run_async(plugin_instance.minecraft_autonomy_review(**payload))["reflection_completion"] == result["reflection_completion"]
    assert not getattr(plugin_instance, "_speech_output_tasks", None)


@pytest.mark.parametrize("outcome", ["lesson", "deferred"])
def test_completed_death_does_not_reenter_ordinary_adapter_inspection(plugin_instance, run_async, monkeypatch, outcome):
    _, port, review, episode = setup_death(plugin_instance, monkeypatch)
    run_async(plugin_instance.minecraft_autonomy_review(review["id"], "inspect"))
    reflected = {"episode_id": episode["id"], "outcome": outcome, "reason": "Cause is unknown; inspect before inferring",
        "lesson": "Check fresh recovery resources", "applicability": "After respawn before returning",
        "evidence": episode["id"] + "; current inventory synchronized"}
    assert run_async(plugin_instance.minecraft_autonomy_review(review["id"], "continue", "Recover locally",
                     death_reflection=reflected))["acknowledged"]
    original = DeathMemory(port.directory).entries(SCOPE)[0]
    port.now = lambda: 10901  # Adapter fixture configures a 900-second idle interval.
    snapshot = json.loads((port.directory / "status.json").read_text(encoding="utf-8"))
    snapshot["updated_at"] = datetime.fromtimestamp(port.now(), timezone.utc).isoformat()
    (port.directory / "status.json").write_text(json.dumps(snapshot), encoding="utf-8")
    port.observe(snapshot)
    ordinary = port.offer()["review"]
    inspected = run_async(plugin_instance.minecraft_autonomy_review(ordinary["id"], "inspect"))
    assert "death_episode" not in inspected
    rejected = run_async(plugin_instance.minecraft_autonomy_review(ordinary["id"], "continue", "Ordinary strategy",
        death_reflection=reflected, spoken_text="This old death must not replay"))
    assert rejected["is_error"] and not getattr(plugin_instance, "_speech_output_tasks", None)
    assert run_async(plugin_instance.minecraft_autonomy_review(ordinary["id"], "continue", "Ordinary strategy"))["acknowledged"]
    saved = DeathMemory(port.directory).entries(SCOPE)[0]
    assert saved["reflection"] == original["reflection"] and saved["lesson"] == original["lesson"]
