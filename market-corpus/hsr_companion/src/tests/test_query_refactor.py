"""Refactor contracts, not a game accuracy/latency benchmark.

Known rephrasing failure, structured requirements, compound answers, detached
snapshots, cancellation/timeout, trace isolation, and production-plan reuse.
"""

import asyncio
import importlib
import time
from dataclasses import FrozenInstanceError
from types import SimpleNamespace

import pytest
from plugin.plugins.hsr_companion.runtime.diagnostics import build_diagnostic_report
from plugin.plugins.hsr_companion.runtime.followup import AnswerRequest, answer_ready, reply_data
from plugin.plugins.hsr_companion.runtime.grounding import build_grounding
from plugin.plugins.hsr_companion.runtime.observation_store import ObservationSnapshot, ObservationStore
from plugin.plugins.hsr_companion.runtime.query_coordinator import answer_status, resolve_query, satisfied_fields
from plugin.plugins.hsr_companion.runtime.query_plan import build_query_plan
from plugin.plugins.hsr_companion.tests.test_current_observation import plugin_fixture
from plugin.plugins.hsr_companion.tests.test_diagnostics import inputs
from plugin.plugins.hsr_companion.tests.test_screen_facts import catalog


def visual(*, fresh=True):
    return {"fresh": fresh, "status": "confirmed", "page_type": "team_setup", "read_scope": "full",
            "scene": {"verified": fresh, "label": "队伍编成"}, "characters": [{"name": "角色甲"}],
            "controlled_character": None, "screen_facts": {"facts": [], "visible_text": [{"text": "页面说明"}]}}


@pytest.mark.parametrize("query", ["现在是什么页面？页面文字写了什么？",
                                  "当前页面是无名勋礼，请根据观察结果确认页面内容。",
                                  "请看看当前界面内容"])
def test_page_content_rephrasing_no_longer_skips_capture(query):
    plan = build_query_plan(query, "knowledge")
    assert plan.screen and plan.content and not plan.navigation
    assert "visible_text" in plan.fields and "characters" not in plan.fields
    assert plan.refresh_args()["require_content"]


def test_structured_requirements_survive_query_rewording_without_becoming_visual_facts():
    plan = build_query_plan("请确认内容", "knowledge", screen_fields=["page", "visible_text"])
    assert plan.fields == {"page", "visible_text"}
    result = build_grounding("无关重述", visual(), catalog(), plan=plan)
    assert result["requested"] and result["visible_text"][0]["text"] == "页面说明"
    assert not result["target_entity_ids"]
    with pytest.raises(FrozenInstanceError):
        plan.screen = False


@pytest.mark.parametrize("fields", ["page", ["任意字段"], [1], {"page": True}])
def test_invalid_structured_requirements_are_rejected(fields):
    with pytest.raises(ValueError):
        build_query_plan("test", screen_fields=fields)


@pytest.mark.parametrize("query", ["银狼的战技是什么", "星琼怎么获得", "爻光在哪获得"])
def test_named_knowledge_does_not_start_observation(query):
    plan = build_query_plan(query, "knowledge")
    assert not plan.screen and not plan.fields


def request(plan):
    return AnswerRequest("id", "已重述的句子", "猫娘", time.time(), 0, 30, "unknown",
                         plan.scene_only, plan.navigation, plan.content, plan.controlled, plan)


def test_compound_page_and_text_readiness_is_shared_with_followup():
    plan = build_query_plan("请确认", screen_fields=["page", "visible_text"])
    value = visual()
    value["screen_facts"]["visible_text"] = []
    assert satisfied_fields(plan, value) == {"page"}
    assert answer_status(plan, value, {"enabled": True})["state"] == "partial"
    assert not answer_ready(value, request(plan))
    value["screen_facts"]["visible_text"] = [{"text": "规则说明"}]
    assert answer_ready(value, request(plan))
    assert "characters" not in reply_data(value, request(plan))


def test_compound_location_and_roster_does_not_finish_on_location_alone():
    plan = build_query_plan("确认", screen_fields=["location", "characters"])
    value = visual()
    value["characters"] = []
    value["screen_facts"]["facts"] = [{"kind": "current_location", "value": "所在地"}]
    assert not answer_ready(value, request(plan))
    value["characters"] = [{"name": "角色甲"}]
    assert answer_ready(value, request(plan))
    assert reply_data(value, request(plan))["navigation"]["current_location"]["known"]


@pytest.mark.parametrize("state,expected", [("observing", "unconfirmed"), ("paused", "paused"),
                                           ("stopped", "paused"), ("error", "capture_failed")])
def test_unknown_answer_never_means_disconnect_or_a_promised_reply(state, expected):
    plan = build_query_plan("现在是什么界面")
    result = answer_status(plan, visual(fresh=False), {"enabled": True, "state": state})
    assert result["state"] == expected
    assert result["connection"] == "not_checked" and not result["followup_registered"]


def test_diagnostic_plan_only_exports_whitelisted_requirement_codes():
    values = inputs()
    values["last_tool"] = {"query_plan": {"screen": True, "read_path": "PRIVATE_SECRET",
                                          "fields": ["page", "visible_text", "PRIVATE_SECRET", {}],
                                          "query": "PRIVATE_SECRET", "path": "PRIVATE_SECRET"}}
    report = build_diagnostic_report(**values)
    assert "PRIVATE_SECRET" not in report["copy_text"]
    assert report["last_query"]["plan"] == {"screen": True, "read_path": "unknown", "fields": ["page", "visible_text"]}


def test_snapshot_cannot_be_mutated_through_source_or_export_and_rejects_new_serial():
    owner = SimpleNamespace(_observer_status={"enabled": True, "state": "observing"},
                            _observation_epoch=2, _observation_serial=3)
    value = visual()
    snap = ObservationSnapshot.select(owner, value)
    value["characters"][0]["name"] = "changed"
    copy = snap.evidence()
    copy["characters"].clear()
    assert snap.evidence()["characters"][0]["name"] == "角色甲"
    assert snap.matches(owner)
    owner._observation_serial += 1
    assert not snap.matches(owner)


def test_revocation_during_storage_read_cannot_publish_old_page(tmp_path):
    owner, values, _ = plugin_fixture(tmp_path)
    original_get = owner._get_store

    async def get(key):
        value = await original_get(key)
        owner._observation_epoch = 1
        return value

    owner._get_store = get
    value = asyncio.run(ObservationStore(owner).current())
    assert not value["fresh"] and not value["characters"]


def coordinator_owner(value=None):
    owner = SimpleNamespace(_observer_status={"enabled": True, "state": "observing"}, _answer_query_generation=1,
                            _observation_epoch=0, _observation_serial=0)

    async def settings():
        return {"enabled": False}

    async def current():
        return value or visual()

    owner._read_observer_settings = settings
    owner._current_visual_evidence = current
    return owner


def test_coordinator_copies_trace_and_never_launches_ocr_for_knowledge():
    owner = coordinator_owner()
    calls = []

    async def refresh(owner, **kwargs):
        calls.append(kwargs)
        owner._last_refresh_diagnostics = {"outcome": "ready", "stages": [{"stage": "query_finished"}]}
        return visual()

    async def run():
        result = await resolve_query(owner, build_query_plan("现在是什么界面"), refresh=refresh, timeout=1)
        owner._last_refresh_diagnostics["stages"].clear()
        assert result.refresh["stages"]
        await resolve_query(owner, build_query_plan("银狼的战技是什么"), refresh=refresh, timeout=1)

    asyncio.run(run())
    assert len(calls) == 1


def test_coordinator_bounds_slow_settings_and_releases_lock():
    owner = coordinator_owner()

    async def settings():
        await asyncio.sleep(1)

    async def forbidden(*args, **kwargs):
        raise AssertionError("No refresh after deadline")

    owner._read_observer_settings = settings
    result = asyncio.run(resolve_query(owner, build_query_plan("现在是什么界面"), refresh=forbidden, timeout=.03))
    assert result.refresh["outcome"] == "timeout"
    assert result.wait_seconds < .3
    assert not owner._query_coordinator_lock.locked()


def test_superseded_query_does_not_refresh_or_accept_current_names():
    owner = coordinator_owner()

    async def forbidden(*args, **kwargs):
        raise AssertionError("Obsolete question must not refresh")

    result = asyncio.run(resolve_query(owner, build_query_plan("现在是谁"), refresh=forbidden, timeout=1, generation=0))
    assert result.refresh["outcome"] == "cancelled"
    assert not result.snapshot.evidence()["fresh"]


def test_new_question_cancels_old_waiter_without_queuing_a_full_timeout():
    owner = coordinator_owner()
    entered = None
    calls = 0

    async def refresh(owner, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            entered.set()
            await asyncio.sleep(5)
        owner._last_refresh_diagnostics = {"outcome": "ready", "stages": []}
        return visual()

    async def run():
        nonlocal entered
        entered = asyncio.Event()
        first = asyncio.create_task(resolve_query(owner, build_query_plan("现在是什么界面"), refresh=refresh, timeout=1))
        await entered.wait()
        owner._answer_query_generation += 1
        second = await resolve_query(owner, build_query_plan("现在是什么界面"), refresh=refresh, timeout=.3)
        obsolete = await first
        assert second.snapshot.evidence()["fresh"] and second.refresh["outcome"] == "ready"
        assert obsolete.refresh["outcome"] == "cancelled" and not obsolete.snapshot.evidence()["fresh"]
        assert not owner._query_coordinator_lock.locked()

    asyncio.run(run())
    assert calls == 2


def test_caller_cancellation_propagates_and_releases_coordinator_lock():
    owner = coordinator_owner()

    async def refresh(owner, **kwargs):
        await asyncio.sleep(5)

    async def run():
        task = asyncio.create_task(resolve_query(owner, build_query_plan("现在是什么界面"), refresh=refresh, timeout=1))
        while getattr(owner, "_active_coordinated_refresh", None) is None:
            await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not owner._query_coordinator_lock.locked()
        assert owner._active_coordinated_refresh is None

    asyncio.run(run())


@pytest.mark.parametrize("change", ["epoch", "serial", "pause"])
def test_tool_final_await_cannot_return_old_screen_facts(tmp_path, monkeypatch, change):
    owner, values, _ = plugin_fixture(tmp_path)
    owner._observation_epoch = 0
    owner._observation_serial = 1
    value = visual()
    value["screen_facts"]["observation_serial"] = 1
    module = importlib.import_module(type(owner).__module__)

    async def refresh(owner, **kwargs):
        owner._last_refresh_diagnostics = {"outcome": "ready", "stages": []}
        return value

    async def audit(*args, **kwargs):
        if change == "epoch":
            owner._observation_epoch += 1
        elif change == "serial":
            owner._observation_serial += 1
        else:
            owner._observer_status.update(enabled=False, state="stopped")

    monkeypatch.setattr(module, "refresh_for_query", refresh)
    owner._record_tool_use = audit
    result = asyncio.run(owner.hsr_companion_prepare(query="现在是什么界面？都有谁？"))
    assert not result["current_visual_evidence"]["fresh"]
    assert not result["screen_identity_answer"]["names"]
    assert not result["screen_scene_answer"]["allowed"]
    assert "角色甲" not in str(result["authoritative_context"])
    assert not result["followup"]["pending"]


def test_tool_uses_one_plan_for_capture_grounding_and_followup(tmp_path, monkeypatch):
    owner, _, _ = plugin_fixture(tmp_path)
    owner._target_character = lambda: "猫娘"
    owner.ctx = SimpleNamespace(bus=SimpleNamespace(memory=SimpleNamespace(get=lambda **kw: [])))
    module = importlib.import_module(type(owner).__module__)
    original_build, original_ground = module.build_query_plan, module.build_grounding
    plans, calls = [], []

    def build(*args, **kwargs):
        plan = original_build(*args, **kwargs)
        plans.append(plan)
        return plan

    def ground(*args, **kwargs):
        assert kwargs["plan"] is plans[0]
        return original_ground(*args, **kwargs)

    async def refresh(owner, **kwargs):
        calls.append(kwargs)
        owner._last_refresh_diagnostics = {"outcome": "timeout", "stages": []}

    monkeypatch.setattr(module, "build_query_plan", build)
    monkeypatch.setattr(module, "build_grounding", ground)
    monkeypatch.setattr(module, "refresh_for_query", refresh)

    async def run():
        result = await owner.hsr_companion_prepare(query="确认内容", intent="knowledge", screen_fields=["page", "visible_text"])
        assert len(plans) == 1 and calls[0]["require_content"]
        assert result["followup"]["pending"] and owner._pending_answer.plan is plans[0]
        assert result["query_answer"]["followup_registered"]
        owner._note_speech_query()
        await asyncio.gather(owner._followup_task, return_exceptions=True)

    asyncio.run(run())


def test_tool_rephrased_page_query_calls_capture_instead_of_database_only(tmp_path, monkeypatch):
    owner, _, _ = plugin_fixture(tmp_path)
    owner._target_character = lambda: ""
    calls = []
    module = importlib.import_module(type(owner).__module__)

    async def refresh(owner, **kwargs):
        calls.append(kwargs)
        owner._last_refresh_diagnostics = {"outcome": "timeout", "stages": []}

    monkeypatch.setattr(module, "refresh_for_query", refresh)
    result = asyncio.run(owner.hsr_companion_prepare(
        query="当前页面是无名勋礼，请根据观察结果确认页面内容。", intent="knowledge"))
    assert calls and result["screen_context_answer"]["requested"]
    assert result["query_plan"]["read_path"] == "full"
    assert result["query_answer"]["state"] == "unconfirmed"
    assert not result["query_answer"]["followup_registered"]
