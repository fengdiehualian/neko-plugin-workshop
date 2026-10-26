import asyncio
import importlib
import json
from contextlib import nullcontext
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from agent_mc_autonomy.ownership import session_lease
from agent_mc_autonomy.review import ReviewPort
from plugin.sdk.plugin import Err


def setup(plugin, monkeypatch):
    mod = importlib.import_module(plugin.__class__.__module__ + ".neko_heartbeat")
    raw = {"autonomy": {"enabled": True}, "server": {"host": "example.invalid", "username": "TestBot"},
           "heartbeat": {"enabled": True, "target": "TestRole", "idle_presence_seconds": 900,
                         "structured_reviews": False}}
    monkeypatch.setattr(plugin.config, "dump", AsyncMock(return_value=raw))
    directory = mod.review_port(plugin, mod.Settings.parse(raw)).directory
    port = ReviewPort(directory, now=lambda: 10000)
    monkeypatch.setattr(mod, "review_port", lambda *_: port)
    port.configure(mod.HeartbeatSettings.parse(raw).policy())
    snapshot = {"phase": "running", "game_connected": True, "owner_pid": 123,
                "updated_at": datetime.fromtimestamp(10000, timezone.utc).isoformat(), "standing_goal": "安全生存",
                "facts": {"parts": [{"key": "body", "text": "生命20/20"}, {"key": "gear", "text": "木镐1把"},
                                    {"key": "foodReserve", "text": "口粮0个"}, {"key": "queue", "text": "空闲"}]}}
    (port.directory / "status.json").write_text(json.dumps(snapshot), encoding="utf-8")
    push = Mock(return_value={"submitted": True})
    monkeypatch.setattr(plugin, "push_message", push)
    return port, snapshot, push


def test_disabled_never_reads_game(plugin_instance, run_async, monkeypatch):
    monkeypatch.setattr(plugin_instance, "status", AsyncMock(side_effect=AssertionError("must not poll game")))
    assert run_async(plugin_instance.game_decision_heartbeat()).value["reason"] == "disabled"


def test_retry_resumes_the_inspected_review_instead_of_restarting_inspection(plugin_instance, run_async, monkeypatch):
    port, snapshot, push = setup(plugin_instance, monkeypatch)
    port.observe(snapshot)
    first = run_async(plugin_instance.game_decision_heartbeat()).value["heartbeat_id"]
    run_async(plugin_instance.minecraft_autonomy_review(first, "inspect"))
    port.now = lambda: 10090
    snapshot["updated_at"] = datetime.fromtimestamp(10090, timezone.utc).isoformat()
    (port.directory / "status.json").write_text(json.dumps(snapshot), encoding="utf-8")
    port.observe(snapshot)
    retry = run_async(plugin_instance.game_decision_heartbeat()).value
    text = push.call_args.kwargs["parts"][0]["text"]
    assert retry["heartbeat_id"] == first and "已检查，待最终判断" in text
    assert "请直接提交continue/insert/revise" in text
    assert "调用minecraft_autonomy_review inspect，收到证据后" not in text
    assert not port.state()["review"].get("result")  # Notification never invents the brain's decision.
    final = run_async(plugin_instance.minecraft_autonomy_review(first, "continue", "Keep the current work"))
    assert final["acknowledged"]


def test_notification_leads_with_playing_and_conversation_before_private_review_steps(plugin_instance, run_async, monkeypatch):
    port, snapshot, push = setup(plugin_instance, monkeypatch)
    port.observe(snapshot)
    result = run_async(plugin_instance.game_decision_heartbeat()).value
    text = push.call_args.kwargs["parts"][0]["text"]
    assert text.startswith("你正亲自在玩Minecraft")
    assert text.index("感受或打算") < text.index("待检查并作出判断")
    assert text.index("生命20/20") < text.index("调用minecraft_autonomy_review")
    assert result["heartbeat_id"] in text
    assert not port.state()["review"].get("result")


@pytest.mark.parametrize("phase", ["blocked", "inhibited"])
def test_delivered_pending_review_refreshes_commentary_for_new_material_episode(plugin_instance, run_async, monkeypatch, phase):
    port, snapshot, push = setup(plugin_instance, monkeypatch)
    port.observe(snapshot)
    first = run_async(plugin_instance.game_decision_heartbeat()).value["heartbeat_id"]
    run_async(plugin_instance.minecraft_autonomy_review(first, "inspect"))
    run_async(plugin_instance.minecraft_autonomy_review(first, "continue", "continue useful work"))
    port.now = lambda: 10180
    snapshot["updated_at"] = datetime.fromtimestamp(10180, timezone.utc).isoformat()
    snapshot["planner_error"] = "temporary_request_unavailable"
    (port.directory / "status.json").write_text(json.dumps(snapshot), encoding="utf-8")
    port.observe(snapshot)
    # A real planner-error change delivers a review but has no speaking topic.
    assert port.state()["review"]["trigger"] == "state_changed"
    pending_id = run_async(plugin_instance.game_decision_heartbeat()).value["heartbeat_id"]
    assert not run_async(plugin_instance.minecraft_autonomy_review(pending_id, "inspect"))["commentary"]["allowed"]
    port.now = lambda: 10281
    snapshot["updated_at"] = datetime.fromtimestamp(10281, timezone.utc).isoformat()
    if phase == "blocked":
        snapshot["native_outcomes"] = [{"tool": "mc_do", "execution_id": "new-failure", "phase": "blocked",
            "receipt": "new actual failure", "original_event": {"run": "r", "cursor": 8}}]
    else:
        snapshot["body_events"] = [{"type": "minecraft.body", "run": "r", "cursor": 9,
            "text": "[身体受限] new actual inhibition"}]
    (port.directory / "status.json").write_text(json.dumps(snapshot), encoding="utf-8")
    port.observe(snapshot)
    assert port.state()["review"]["id"] == pending_id
    detail = run_async(plugin_instance.minecraft_autonomy_review(pending_id, "inspect"))
    note = detail["commentary"]
    assert note["allowed"] and (detail["native_outcomes"] or detail["body_events"])
    evidence_key = "native_outcomes" if phase == "blocked" else "body_events"
    assert detail[evidence_key][-1]["phase"] == phase
    acknowledged = run_async(plugin_instance.minecraft_autonomy_review(pending_id, "continue", "use the new episode"))
    assert acknowledged["commentary"] == {key: value for key, value in note.items() if key != "本轮重点证据"}
    assert not note.get("playback_verified", False)
    port.now = lambda: 10341
    snapshot["updated_at"] = datetime.fromtimestamp(10341, timezone.utc).isoformat()
    (port.directory / "status.json").write_text(json.dumps(snapshot), encoding="utf-8")
    port.observe(snapshot)
    assert not port.offer()["offered"]
    assert push.call_count == 2


@pytest.mark.parametrize("body_phase", [None, "inhibited", "recovered", "restriction_fact"])
def test_resolved_failure_does_not_override_current_preview(plugin_module, body_phase):
    heartbeat = importlib.import_module(plugin_module.__name__ + ".neko_heartbeat")
    evidence = {"facts": {"queue": "useful work", "body": "生命20/20", "foodReserve": "口粮0个"},
        "standing_goal": "survive", "tool_failure": None, "native_outcomes": [
            {"tool": "mc_do", "phase": "blocked", "receipt": "old path_stalled", "original_event": {"run": "r", "cursor": 8}},
            {"tool": "mc_do", "phase": "completed", "receipt": "known task completed", "original_event": {"run": "r", "cursor": 12}}]}
    if body_phase == "restriction_fact":
        evidence["facts"]["bodyRestriction"] = "current restriction"
        evidence["fact_originals"] = {"bodyRestriction": "fact:restriction"}
    elif body_phase:
        evidence["body_events"] = [{"type": "minecraft.body", "phase": body_phase, "text": "current " + body_phase,
            "original_event": {"run": "r", "cursor": 13}}]
    text = heartbeat.heartbeat_text({"id": "review", "evidence": evidence})
    assert "old path_stalled" not in text and "r:8" not in text and "mc_do/blocked" not in text
    if body_phase == "restriction_fact":
        assert "bodyRestriction/inhibited" in text and "current restriction" in text and "fact:restriction" in text
    elif body_phase:
        assert "minecraft.body/" + body_phase in text and "current " + body_phase in text and "r:13" in text
    else:
        assert "known task completed" in text and "r:12" in text


@pytest.mark.parametrize("runtime_error", [None, "outcome_unknown"])
def test_preview_error_phase_and_reference_share_one_source(plugin_module, runtime_error):
    heartbeat = importlib.import_module(plugin_module.__name__ + ".neko_heartbeat")
    evidence = {"facts": {}, "standing_goal": "survive", "phase": "failed" if runtime_error else "running",
        "runtime_error": runtime_error, "tool_failure": {"tool": "mc_do", "error": "unresolved native failure",
            "native_execution": {"phase": "partial"}, "native_event": {"run": "r", "cursor": 8}},
        "body_events": [{"type": "minecraft.body", "phase": "recovered", "text": "actual recovery",
            "original_event": {"run": "r", "cursor": 13}}]}
    text = heartbeat.heartbeat_text({"id": "review", "evidence": evidence})
    if runtime_error:
        assert "runtime/failed outcome_unknown" in text and "r:8" not in text and "r:13" not in text
    else:
        assert "mc_do/partial unresolved native failure" in text and "r:8" in text and "r:13" not in text


def test_actual_blocked_outcome_has_bounded_preview_and_unverified_reaction(plugin_instance, run_async, monkeypatch):
    port, snapshot, push = setup(plugin_instance, monkeypatch)
    snapshot["facts"]["parts"].append({"key": "clock", "text": "四周一片漆黑。"})
    snapshot["native_outcomes"] = [{"tool": "mc_do", "execution_id": "task-7", "phase": "blocked",
        "receipt": "path_stalled: no route " * 200, "observation_only": False, "goal_verified": False,
        "original_event": {"run": "run-1", "cursor": 8}}]
    snapshot["unactioned_replies"] = 3
    (port.directory / "status.json").write_text(json.dumps(snapshot), encoding="utf-8")
    port.observe(snapshot)
    result = run_async(plugin_instance.game_decision_heartbeat()).value
    cue = push.call_args.kwargs["parts"][0]["text"]
    assert push.call_args.kwargs["ai_behavior"] == "respond"
    assert "mc_do/blocked" in cue and "path_stalled" in cue and "run-1:8" in cue
    assert cue.index("四周一片漆黑") < cue.index("path_stalled")
    assert cue.index("path_stalled") < cue.index("调用minecraft_autonomy_review")
    assert "长期目标" not in cue and len(cue.encode()) < 750  # Full intent is returned by inspect, not duplicated in every cue.
    assert "未行动回复3次" in cue
    detail = run_async(plugin_instance.minecraft_autonomy_review(result["heartbeat_id"], "inspect"))
    assert detail["standing_goal"] == snapshot["standing_goal"]
    assert not detail["native_outcomes"][0]["goal_verified"]
    note = run_async(plugin_instance.minecraft_autonomy_review(result["heartbeat_id"], "continue", "Change strategy using actual receipt"))["commentary"]
    assert note["allowed"] and not result["playback_verified"]
    assert detail["native_outcomes"][0]["phase"] == "blocked"
    assert "本轮重点证据" not in note


def test_body_restriction_only_wakes_main_once_and_keeps_reason_inspectable(plugin_instance, run_async, monkeypatch):
    port, snapshot, push = setup(plugin_instance, monkeypatch)
    port.observe(snapshot)
    first = run_async(plugin_instance.game_decision_heartbeat()).value["heartbeat_id"]
    run_async(plugin_instance.minecraft_autonomy_review(first, "inspect"))
    run_async(plugin_instance.minecraft_autonomy_review(first, "continue", "keep useful work"))
    port.now = lambda: 10060
    snapshot["updated_at"] = datetime.fromtimestamp(10060, timezone.utc).isoformat()
    snapshot["facts"]["parts"].append({"key": "bodyRestriction", "text": "native inhibition reason"})
    (port.directory / "status.json").write_text(json.dumps(snapshot), encoding="utf-8")
    port.observe(snapshot)
    changed = run_async(plugin_instance.game_decision_heartbeat()).value
    assert changed["submitted"] and push.call_count == 2
    assert push.call_args.kwargs["ai_behavior"] == "respond"
    assert "native inhibition reason" in push.call_args.kwargs["parts"][0]["text"]
    detail = run_async(plugin_instance.minecraft_autonomy_review(changed["heartbeat_id"], "inspect"))
    assert detail["facts"]["bodyRestriction"] == "native inhibition reason"
    assert not run_async(plugin_instance.game_decision_heartbeat()).value["submitted"] and push.call_count == 2


def test_frontend_only_transports_framework_reviews(plugin_instance, run_async, monkeypatch):
    port, snapshot, push = setup(plugin_instance, monkeypatch)
    assert not run_async(plugin_instance.game_decision_heartbeat()).value["submitted"]
    assert not push.called  # Even fresh facts do not create a review in the adapter.
    port.observe(snapshot)  # Only the independent runtime does this.
    monkeypatch.setattr(ReviewPort, "observe", Mock(side_effect=AssertionError("frontend owns no scheduler")))
    result = run_async(plugin_instance.game_decision_heartbeat()).value
    assert result["submitted"] and not result["main_brain_acknowledged"] and not result["playback_verified"]
    event = push.call_args.kwargs
    assert event["target_lanlan"] == "TestRole" and event["ai_behavior"] == "respond"
    text = event["parts"][0]["text"]
    assert "minecraft_autonomy_review" in text and "口粮0个" in text and len(text.encode()) < 650
    assert text.index("口粮0个") < text.index("inspect") and "spoken_text" in text and "调用工具前不说话" not in text
    review_id = result["heartbeat_id"]
    run_async(plugin_instance.minecraft_autonomy_review(review_id, "inspect"))
    acknowledged = run_async(plugin_instance.minecraft_autonomy_review(review_id, "continue", "继续当前工作"))
    assert acknowledged["acknowledged"] and not acknowledged["dispatched"]
    assert {k: acknowledged[k] for k in port.state()["review"]["result"]} == port.state()["review"]["result"]
    assert acknowledged["commentary"]["allowed"]


def test_review_tool_routes_once_to_framework_mailbox(plugin_instance, run_async, monkeypatch):
    port, snapshot, _ = setup(plugin_instance, monkeypatch)
    port.observe(snapshot)
    review_id = run_async(plugin_instance.game_decision_heartbeat()).value["heartbeat_id"]
    run_async(plugin_instance.minecraft_autonomy_review(review_id, "inspect"))
    args = (review_id, "insert", "缺口粮", "收集食物")
    first = run_async(plugin_instance.minecraft_autonomy_review(*args))
    assert first == run_async(plugin_instance.minecraft_autonomy_review(*args))
    assert first["dispatched"] and not first["verified"]
    assert port.mailbox.get(first["request_id"])["delivery"] == "queued"


def test_long_review_uses_small_inspection_cue_without_losing_framework_evidence(plugin_instance, run_async, monkeypatch):
    port, snapshot, push = setup(plugin_instance, monkeypatch)
    snapshot["standing_goal"] = "长期生存，获取食物和庇护所。" * 80
    snapshot["facts"]["parts"][1]["text"] = "木镐1把、工作台1个、原木34个。" * 30
    snapshot["read_activity"] = {"mc_bag": 18, "mc_scout": 8}
    (port.directory / "status.json").write_text(json.dumps(snapshot), encoding="utf-8")
    port.observe(snapshot)
    review_id = run_async(plugin_instance.game_decision_heartbeat()).value["heartbeat_id"]
    cue = push.call_args.kwargs["parts"][0]["text"]
    assert len(cue.encode("utf-8")) <= 600
    assert cue.index("inspect") < cue.index("continue")
    assert "空闲" in cue and "26" in cue and "口粮0个" in cue
    detail = run_async(plugin_instance.minecraft_autonomy_review(review_id, "inspect"))
    assert "工作台1个" in detail["facts"]["gear"]
    assert detail["read_activity"] == snapshot["read_activity"]
    assert not port.state()["review"].get("result")


def test_routine_reviews_keep_strategy_but_do_not_require_spoken_readouts(plugin_instance, run_async, monkeypatch):
    port, snapshot, push = setup(plugin_instance, monkeypatch)
    port.observe(snapshot)
    first = run_async(plugin_instance.game_decision_heartbeat()).value["heartbeat_id"]
    run_async(plugin_instance.minecraft_autonomy_review(first, "inspect"))
    run_async(plugin_instance.minecraft_autonomy_review(first, "continue", "正常工作"))
    port.now = lambda: 10180
    snapshot["updated_at"] = datetime.fromtimestamp(10180, timezone.utc).isoformat()
    (port.directory / "status.json").write_text(json.dumps(snapshot), encoding="utf-8")
    port.observe(snapshot)
    review_id = run_async(plugin_instance.game_decision_heartbeat()).value["heartbeat_id"]
    cue = push.call_args.kwargs["parts"][0]["text"]
    assert "无需回复或口播" in cue
    assert push.call_args.kwargs["ai_behavior"] == "read"
    result = port.state()["review"]["result"]
    assert result["deferred"] and result["context_only"]
    assert not result["acknowledged"] and not result["dispatched"]
    assert port.state()["last_decision"]["heartbeat_id"] == first
    assert port.state()["review"]["id"] == review_id


def test_review_lineage_pages_are_forwarded_without_plugin_policy(plugin_instance, run_async, monkeypatch):
    port, snapshot, _ = setup(plugin_instance, monkeypatch)
    port.observe(snapshot)
    review_id = run_async(plugin_instance.game_decision_heartbeat()).value["heartbeat_id"]
    page = run_async(plugin_instance.minecraft_autonomy_review(review_id, "inspect", lineage_offset=0))
    assert page["goal_lineage"] == [] and page["has_more"] is False
    assert not port.mailbox.ready()


def test_revision_routes_through_framework_without_plugin_queue_policy(plugin_instance, run_async, monkeypatch):
    port, snapshot, _ = setup(plugin_instance, monkeypatch)
    port.observe(snapshot)
    first_id = run_async(plugin_instance.game_decision_heartbeat()).value["heartbeat_id"]
    run_async(plugin_instance.minecraft_autonomy_review(first_id, "inspect"))
    first = run_async(plugin_instance.minecraft_autonomy_review(first_id, "insert", "需要工具", "做好工具再找食物"))
    port.mailbox.activate_feedback(port.mailbox.claim())
    port.now = lambda: 10180
    snapshot.update(updated_at=datetime.fromtimestamp(10180, timezone.utc).isoformat(), current_goal=port.mailbox.active_goal())
    (port.directory / "status.json").write_text(json.dumps(snapshot), encoding="utf-8")
    port.observe(snapshot)
    review_id = run_async(plugin_instance.game_decision_heartbeat()).value["heartbeat_id"]
    assert run_async(plugin_instance.minecraft_autonomy_review(review_id, "inspect"))["stage_delivery"]["can_revise"]
    args = (review_id, "revise", "已有木剑", "找食物")
    result = run_async(plugin_instance.minecraft_autonomy_review(*args))
    assert result == run_async(plugin_instance.minecraft_autonomy_review(*args))
    assert result["dispatched"] and not result["verified"]
    assert port.mailbox.claim()["payload"]["revision_of"] == first["request_id"]


def test_stop_blocks_transport_and_tool(plugin_instance, run_async, monkeypatch):
    port, snapshot, push = setup(plugin_instance, monkeypatch)
    port.observe(snapshot)
    plugin_instance._operator_state(True)
    assert run_async(plugin_instance.game_decision_heartbeat()).value["reason"] == "explicit_stop"
    assert run_async(plugin_instance.minecraft_autonomy_review(port.state()["review"]["id"], "continue", "等候"))["error"] == "disabled_or_stopped"
    assert not push.called


def external_setup(plugin, monkeypatch):
    port, snapshot, push = setup(plugin, monkeypatch)
    plugin.config.dump.return_value["runtime"] = {"mode": "external", "data_directory": str(port.directory.parents[2])}
    plugin._operator_state(True)
    return port, snapshot, push


def test_explicit_resume_only_restores_host_participation(plugin_instance, run_async, monkeypatch):
    port, snapshot, push = external_setup(plugin_instance, monkeypatch)
    port.observe(snapshot)
    before = port.state()
    with session_lease(port.directory):
        for _ in range(2):
            result = run_async(plugin_instance.resume_observation()).value
            assert result["observer_resumed"] and not result["game_started"] and not result["task_dispatched"]
            assert not result["submitted"] and not result["main_brain_acknowledged"] and not result["playback_verified"]
    assert port.state() == before and not port.mailbox.ready() and not push.called
    assert plugin_instance._worker is None
    assert not json.loads(plugin_instance.data_path("operator.json").read_text())["explicit_stop"]
    assert run_async(plugin_instance.game_decision_heartbeat()).value["submitted"]


@pytest.mark.parametrize("fault", ["missing_lease", "other_account", "stale", "future", "failed", "disconnected", "disabled", "heartbeat_disabled", "embedded"])
def test_resume_rejects_inactive_or_disabled_runtime(plugin_instance, run_async, monkeypatch, fault):
    port, snapshot, push = external_setup(plugin_instance, monkeypatch)
    raw = plugin_instance.config.dump.return_value
    if fault in {"stale", "future"}:
        snapshot["updated_at"] = datetime.fromtimestamp(9900 if fault == "stale" else 10100, timezone.utc).isoformat()
    elif fault == "failed":
        snapshot["phase"] = "failed"
    elif fault == "disconnected":
        snapshot["game_connected"] = False
    elif fault == "disabled":
        raw["autonomy"]["enabled"] = False
    elif fault == "heartbeat_disabled":
        raw["heartbeat"]["enabled"] = False
    elif fault == "embedded":
        raw["runtime"]["mode"] = "embedded"
    (port.directory / "status.json").write_text(json.dumps(snapshot), encoding="utf-8")
    lease = nullcontext() if fault == "missing_lease" else session_lease(port.directory / "other" if fault == "other_account" else port.directory)
    with lease:
        assert isinstance(run_async(plugin_instance.resume_observation()), Err)
    assert json.loads(plugin_instance.data_path("operator.json").read_text())["explicit_stop"]
    assert not push.called and plugin_instance._worker is None and not port.mailbox.ready()


def test_stop_wins_over_inflight_observer_resume(plugin_instance, run_async, monkeypatch):
    port, _, push = external_setup(plugin_instance, monkeypatch)
    raw = plugin_instance.config.dump.return_value
    async def exercise():
        entered, release = asyncio.Event(), asyncio.Event()
        async def delayed(**_):
            entered.set()
            await release.wait()
            return raw
        monkeypatch.setattr(plugin_instance.config, "dump", delayed)
        with session_lease(port.directory):
            resuming = asyncio.create_task(plugin_instance.resume_observation())
            await entered.wait()
            stopping = asyncio.create_task(plugin_instance.stop())
            await asyncio.sleep(0)
            release.set()
            result = await resuming
            await stopping
            assert isinstance(result, Err) and result.error["code"] == "resume_cancelled"
    run_async(exercise())
    assert json.loads(plugin_instance.data_path("operator.json").read_text())["explicit_stop"]
    assert not push.called


def test_configuration_save_invalidates_inflight_observer_resume(plugin_instance, run_async, monkeypatch):
    port, _, push = external_setup(plugin_instance, monkeypatch)
    raw = plugin_instance.config.dump.return_value
    async def exercise():
        entered, release = asyncio.Event(), asyncio.Event()
        first = True
        async def delayed(**_):
            nonlocal first
            if first:
                first = False
                entered.set()
                await release.wait()
            return raw
        monkeypatch.setattr(plugin_instance.config, "dump", delayed)
        monkeypatch.setattr(plugin_instance.config, "update", AsyncMock())
        with session_lease(port.directory):
            resuming = asyncio.create_task(plugin_instance.resume_observation())
            await entered.wait()
            assert (await plugin_instance.save_settings({"server": {"username": "OtherBot"}})).value["saved"]
            release.set()
            result = await resuming
            assert isinstance(result, Err) and result.error["code"] == "resume_cancelled"
    run_async(exercise())
    assert json.loads(plugin_instance.data_path("operator.json").read_text())["explicit_stop"]
    assert not push.called


def test_narration_and_policy_configuration_are_host_adaptations(plugin_instance, run_async, monkeypatch):
    port, _, _ = setup(plugin_instance, monkeypatch)
    assert run_async(plugin_instance.observe_game_changes()).value["reason"] == "decision_heartbeat_owns_narration"
    plugin_instance._worker = SimpleNamespace(snapshot=lambda: {"worker_alive": True})
    update = AsyncMock()
    monkeypatch.setattr(plugin_instance.config, "update", update)
    assert run_async(plugin_instance.save_settings({"heartbeat": {"review_interval_seconds": 240}})).value["saved"]
    assert port.state()["policy"]["review_interval_seconds"] == 240
    assert update.await_count == 1
