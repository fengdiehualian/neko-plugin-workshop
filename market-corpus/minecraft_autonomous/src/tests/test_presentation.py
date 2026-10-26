import base64
import dataclasses
import importlib
import json
import struct
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from plugin.sdk.plugin import Ok


def snapshot(now, health="生命 20/20"):
    return {"phase": "running", "game_connected": True,
            "updated_at": datetime.fromtimestamp(now, timezone.utc).isoformat(),
            "facts": {"parts": [{"key": "body", "text": health}, {"key": "gear", "text": "木板×4"}]}}


def configure(plugin_instance, monkeypatch, now, *, enabled=True):
    module = importlib.import_module(plugin_instance.__class__.__module__ + ".neko_presentation")
    monkeypatch.setattr(module, "time", SimpleNamespace(time=lambda: now))
    monkeypatch.setattr(plugin_instance.config, "dump", AsyncMock(return_value={
        "presentation": {"enabled": enabled, "target": "TestRole"}}))
    monkeypatch.setattr(plugin_instance, "status", AsyncMock(return_value=Ok(snapshot(now))))
    push = Mock(return_value={"submitted": True})
    monkeypatch.setattr(plugin_instance, "push_message", push)
    return module, push


def test_disabled_timer_has_no_model_or_game_io(plugin_instance, run_async):
    assert run_async(plugin_instance.observe_game_changes()).value["reason"] == "disabled"


def test_failure_inhibition_recovery_offer_one_opportunity_with_original_evidence(plugin_instance):
    mod = importlib.import_module(plugin_instance.__class__.__module__ + ".neko_commentary")
    settings = importlib.import_module("agent_mc_autonomy.configuration").Settings.parse({})
    observe = importlib.import_module("agent_mc_autonomy.commentary").commentary_observation
    path, state, _ = mod.commentary_plan(plugin_instance, settings, "TestRole", observe(snapshot(10000), 10000), 10000, 90)
    mod.save_commentary(path, {**state, "submitted_at": 10000})
    changes = [("task_failure", {"native_outcomes": [{"tool": "mc_do", "execution_id": "task-7", "phase": "blocked",
                "receipt": "path_stalled", "original_event": {"run": "run-1", "cursor": 8}}]}),
               ("body_inhibited", {"body_events": [{"type": "minecraft.body", "run": "run-1", "cursor": 9,
                    "text": "[身体受限] server restraint"}]}),
               ("body_recovered", {"body_events": [{"type": "minecraft.body", "run": "run-1", "cursor": 10,
                    "text": "[身体恢复] actual receipt"}]})]
    for index, (topic, extra) in enumerate(changes, 1):
        now = 10000 + index * 100
        event = observe({**snapshot(now), **extra}, now)
        _, state, note = mod.commentary_plan(plugin_instance, settings, "TestRole", event, now, 90, review_id=f"review-{index}")
        assert note["allowed"] and topic in note["topics"]
        assert not note["evidence"]["progress"] and note["evidence"]["task_outcome"] is None
        assert note["evidence"]["body_feedback"] and not note["playback_verified"]
        mod.save_commentary(path, {**state, "submitted_at": now})
        _, _, repeat = mod.commentary_plan(plugin_instance, settings, "TestRole", event, now + 100, 90, review_id=f"repeat-{index}")
        assert not repeat["allowed"]


@pytest.mark.parametrize("fallback", [False, True])
def test_fake_controller_visual_help_invoke_uses_vision_or_honest_text_fallback(plugin_module, tmp_path, run_async, fallback):
    from agent_mc_autonomy.configuration import Settings
    from agent_mc_autonomy.context import RULES
    from agent_mc_autonomy.controller import Controller, model_tools
    from agent_mc_autonomy.perception import has_images
    planner = importlib.import_module(plugin_module.__name__ + ".neko_planner")
    calls, captures, published = [], [], []
    async def create(**request):
        calls.append(request)
        if len(calls) <= 2:
            name, args = (("mc_tool_help", {"name": "mc_visual"}) if len(calls) == 1 else
                          ("mc_invoke", {"name": "mc_visual", "arguments": {"mode": "first", "raw": True}}))
            message = {"tool_calls": [{"id": str(len(calls)), "type": "function", "function": {
                "name": name, "arguments": json.dumps(args)}}]}
        else:
            if request["model"] == "vision":
                assert has_images(request["messages"])
                if fallback:
                    raise ValueError("fake unavailable image response")
            else:
                assert not has_images(request["messages"])
            message = {"content": "Continue using actual text facts.", "tool_calls": []}
        return SimpleNamespace(choices=[SimpleNamespace(finish_reason="tool_calls" if message.get("tool_calls") else "stop",
            message=SimpleNamespace(model_dump=lambda **_: message))], usage=None)
    gateway = SimpleNamespace(get_client=AsyncMock(return_value=SimpleNamespace(chat=SimpleNamespace(
        completions=SimpleNamespace(create=create)))))
    brain = planner.NekoPlanner(gateway, 512)
    class Body:
        tools = [{"name": "mc_visual", "description": "Actual scene PNG, synchronous raw image.",
            "inputSchema": {"type": "object", "properties": {"mode": {"enum": ["first"]}, "raw": {"type": "boolean"}}},
            "_meta": {"agent-mc/v1": {"tags": ["read"]}}}]
        async def call(self, name, args, **_):
            if name == "mc_visual":
                captures.append(args)
                # A fake transport header only; no rendered game image is claimed.
                png = b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + struct.pack(">II", 128, 128)
                return {"structuredContent": {"protocol": "agent-mc/v1", "text": "fake capture receipt"},
                    "content": [{"type": "image", "mimeType": "image/png", "data": base64.b64encode(png).decode()}]}
            value = ({"parts": [{"key": "body", "text": "生命20/20"}, {"key": "gear", "text": "empty"}]}
                     if name == "mc_protocol" and args["action"] == "facts" else
                     {"run": "fake-run", "nextCursor": 0, "events": []} if name == "mc_events" else
                     {"handle": "fake:receipt"} if name == "mc_artifact" else {})
            return {"content": [{"type": "text", "text": json.dumps({"protocol": "agent-mc/v1", **value})}]}
    body = Body()
    settings = dataclasses.replace(Settings(), vision_enabled=True, max_tool_rounds=3)
    controller = Controller(body, brain.decide, settings, tmp_path, lambda **value: published.append(value), lambda: False)
    run_async(controller.turn_once("survive", RULES, model_tools(body.tools)))
    assert captures == [{"mode": "first", "raw": True}] and controller.frame is None
    assert [row["model"] for row in calls] == ["decision", "decision", "vision"] + (["decision"] if fallback else [])
    status = next(row["model_status"] for row in reversed(published) if "model_status" in row)
    assert status["image_used"] is (not fallback)
    if fallback:
        assert status["fallback"] == "invalid_vision_response"
        assert "only attached fresh images prove sight" in calls[-1]["messages"][0]["content"]
        assert "private" not in json.dumps(status)


def test_fresh_progress_uses_public_role_aware_push_and_deduplicates(plugin_instance, run_async, monkeypatch):
    module, push = configure(plugin_instance, monkeypatch, 10000)
    first = run_async(plugin_instance.observe_game_changes()).value
    assert first["submitted"] and first["playback_verified"] is False
    kwargs = push.call_args.kwargs
    assert kwargs["target_lanlan"] == "TestRole" and kwargs["ai_behavior"] == "respond"
    assert kwargs["visibility"] == [] and "{LANLAN_NAME}" in kwargs["parts"][0]["text"]
    assert "生命 20/20" in kwargs["parts"][0]["text"]
    monkeypatch.setattr(module.time, "time", lambda: 10010)
    plugin_instance.status.return_value = Ok({**snapshot(10010), "decisions": 200})
    assert run_async(plugin_instance.observe_game_changes()).value["reason"] == "unchanged"
    plugin_instance.status.return_value = Ok(snapshot(10010, "生命 12/20"))
    assert run_async(plugin_instance.observe_game_changes()).value["reason"] == "rate_limited"
    monkeypatch.setattr(module.time, "time", lambda: 10100)
    plugin_instance.status.return_value = Ok(snapshot(10100, "生命 12/20"))
    assert run_async(plugin_instance.observe_game_changes()).value["submitted"]
    assert push.call_count == 2
    assert json.loads(next(plugin_instance.data_path("presentation").glob("*.json")).read_text(encoding="utf-8"))["playback_verified"] is False


def test_stale_snapshot_and_not_observed_never_narrate(plugin_instance, run_async, monkeypatch):
    _, push = configure(plugin_instance, monkeypatch, 10000)
    connected_without_facts = {**snapshot(10000), "facts": {"parts": []}}
    for status in (snapshot(9900), {"phase": "not_observed"}, snapshot(10040), connected_without_facts):
        plugin_instance.status.return_value = Ok(status)
        assert run_async(plugin_instance.present_status()).value["reason"] == "no_fresh_observation"
    assert not push.called


def test_notification_is_bounded_before_the_host_summary_and_explicit_refresh_is_throttled(plugin_instance, run_async, monkeypatch):
    module, push = configure(plugin_instance, monkeypatch, 10000)
    status = snapshot(10000)
    status["facts"]["parts"] += [{"key": "gear", "text": "木板×18；" * 1000}, {"key": "queue", "text": "正在移动" * 1000}]
    plugin_instance.status.return_value = Ok(status)
    assert run_async(plugin_instance.present_status()).value["submitted"]
    text = push.call_args.kwargs["parts"][0]["text"]
    assert len(text.encode("utf-8")) < 650
    assert "生命 20/20" in text and "木板×18" not in text and "正在移动" in text
    assert "1—2句" in text and "背包清单" in text
    assert run_async(plugin_instance.present_status()).value["reason"] == "rate_limited"
    monkeypatch.setattr(module.time, "time", lambda: 10031)
    plugin_instance.status.return_value = Ok(snapshot(10031))
    assert run_async(plugin_instance.present_status()).value["submitted"]


def test_submission_rejection_keeps_event_retryable_but_bounded(plugin_instance, run_async, monkeypatch):
    module, push = configure(plugin_instance, monkeypatch, 10000)
    push.return_value = {"submitted": False, "reason": "backpressure"}
    assert not run_async(plugin_instance.observe_game_changes()).value["submitted"]
    assert run_async(plugin_instance.present_status()).value["reason"] == "rate_limited"
    monkeypatch.setattr(module.time, "time", lambda: 10031)
    plugin_instance.status.return_value = Ok(snapshot(10031))
    push.return_value = {"submitted": True}
    assert run_async(plugin_instance.observe_game_changes()).value["submitted"]
    assert push.call_count == 2


def test_inventory_count_changes_do_not_trigger_automatic_speech(plugin_instance, run_async, monkeypatch):
    module, push = configure(plugin_instance, monkeypatch, 10000)
    first = snapshot(10000)
    first["facts"]["parts"][1]["text"] = "包里有：白桦木板×12、木斧 耐久20/59×1。"
    plugin_instance.status.return_value = Ok(first)
    assert run_async(plugin_instance.observe_game_changes()).value["submitted"]
    monkeypatch.setattr(module.time, "time", lambda: 10100)
    later = snapshot(10100)
    later["facts"]["parts"][1]["text"] = "包里有：白桦木板×20、木斧 耐久12/59×1。"
    plugin_instance.status.return_value = Ok(later)
    assert run_async(plugin_instance.observe_game_changes()).value["reason"] == "unchanged"
    assert push.call_count == 1


def test_new_critical_danger_bypasses_interval_once(plugin_instance, run_async, monkeypatch):
    module, push = configure(plugin_instance, monkeypatch, 10000)
    assert run_async(plugin_instance.observe_game_changes()).value["submitted"]
    monkeypatch.setattr(module.time, "time", lambda: 10010)
    plugin_instance.status.return_value = Ok(snapshot(10010, "生命 4/20"))
    assert run_async(plugin_instance.observe_game_changes()).value["submitted"]
    monkeypatch.setattr(module.time, "time", lambda: 10011)
    plugin_instance.status.return_value = Ok(snapshot(10011, "生命 3/20"))
    assert not run_async(plugin_instance.observe_game_changes()).value["submitted"]
    assert push.call_count == 2


def test_rate_limited_review_does_not_consume_the_next_speaking_topic(plugin_instance):
    mod = importlib.import_module(plugin_instance.__class__.__module__ + ".neko_commentary")
    settings = importlib.import_module("agent_mc_autonomy.configuration").Settings.parse({})
    def event(now, health):
        return importlib.import_module("agent_mc_autonomy.commentary").commentary_observation(snapshot(now, health), now)
    path, state, _ = mod.commentary_plan(plugin_instance, settings, "TestRole", event(10000, "生命20/20"), 10000, 90)
    mod.save_commentary(path, {**state, "submitted_at": 10000})
    _, quiet, note = mod.commentary_plan(plugin_instance, settings, "TestRole", event(10060, "生命12/20"), 10060, 90, review_id="review-one")
    assert not note["allowed"]
    mod.save_commentary(path, quiet)
    _, _, later = mod.commentary_plan(plugin_instance, settings, "TestRole", event(10120, "生命12/20"), 10120, 90, review_id="review-two")
    assert later["allowed"] and "health_changed" in later["topics"]


def test_death_speaking_opportunity_uses_real_episode_once_and_loads_bundled_skill(plugin_instance):
    mod = importlib.import_module(plugin_instance.__class__.__module__ + ".neko_commentary")
    settings = importlib.import_module("agent_mc_autonomy.configuration").Settings.parse({})
    observe = importlib.import_module("agent_mc_autonomy.commentary").commentary_observation
    event = observe(snapshot(10000), 10000)
    path, state, _ = mod.commentary_plan(plugin_instance, settings, "TestRole", event, 10000, 90)
    mod.save_commentary(path, {**state, "submitted_at": 10000})
    event = observe(snapshot(10001), 10001)
    event["payload"]["death_episode"] = {"id": "actual-native-death", "source_event": {"run": "run", "cursor": 7}}
    _, state, note = mod.commentary_plan(plugin_instance, settings, "TestRole", event, 10001, 90, review_id="death-review")
    assert note["allowed"] and note["urgent"]
    delivered = mod.commentary_delivery(note)
    assert delivered["本轮重点证据"]["death_episode"]["source_event"]["cursor"] == 7
    assert delivered["skill_source"] == "registered_tool_description" and "表达技能" not in delivered
    mod.save_commentary(path, {**state, "submitted_at": 10001})
    _, _, repeated = mod.commentary_plan(plugin_instance, settings, "TestRole", event, 10002, 90, review_id="later-review")
    assert not repeated["allowed"]
    quiet = mod.commentary_delivery(repeated)
    assert "本轮重点证据" not in quiet and "最终口播正文为空" not in quiet["instruction"]
