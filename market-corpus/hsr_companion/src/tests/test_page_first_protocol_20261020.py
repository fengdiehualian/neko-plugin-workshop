"""Frozen three-field scheduling and semantic pending-reply acceptance.

Synthetic pixels/public bus fixtures; not a claim about host model tool choice.
"""
import asyncio
import time
from dataclasses import replace
from types import SimpleNamespace

import pytest
from plugin.plugins.hsr_companion.runtime import live_refresh
from plugin.plugins.hsr_companion.runtime.query_plan import build_query_plan
from plugin.plugins.hsr_companion.tests.test_chain_audit_20261017 import audit_owner, commit_raw, navigation_raw


@pytest.mark.parametrize("state", ["busy", "timeout"])
def test_three_fields_checks_page_before_unrelated_text(tmp_path, monkeypatch, state):
    owner, _, _ = audit_owner(tmp_path)
    calls = []
    plan = build_query_plan("我打开的是啥页面？", "read_screen", screen_fields=["page", "quest", "visible_text"])

    async def reuse(owner, **kw):
        calls.append((kw["plan"].fields, kw.get("pixel_only", False)))
        if kw["plan"].fields == {"page"}:
            from plugin.plugins.hsr_companion.runtime.delivery_scope import project_evidence
            return project_evidence(await owner._current_visual_evidence(), kw["plan"])
        assert kw.get("pixel_only"), "Unrelated native text verification ran before the independent page"
        return None  # The text worker is busy/timed out; it cannot authorize text.

    async def run():
        await commit_raw(owner, navigation_raw())
        monkeypatch.setattr(live_refresh, "try_reuse", reuse)
        result = await live_refresh.refresh_for_query(owner, timeout=.2, query_plan=plan, **plan.refresh_args())
        assert calls[0][0] == {"page"}
        assert result["scene"]["verified"]
        assert not result["screen_facts"]["facts"]
        assert owner._last_refresh_diagnostics["outcome"] == "partial"
    asyncio.run(run())


def test_coarse_invalidation_has_causal_receipt_not_a_guess(tmp_path):
    owner, _, _ = audit_owner(tmp_path)
    async def run():
        await commit_raw(owner, navigation_raw())
        await live_refresh.invalidate_frame_change(owner, None, None, [], "page_changed_during_analysis")
        row = owner._revocation_trace[-1]
        assert row["origin"] == "frame_change"
        assert row["basis"] == "coarse_pixels_only"
        assert row["scope"] == "observation" and row["control_count"] == 0
        assert row["candidate_present"]
    asyncio.run(run())


def test_confirm_message_keeps_original_request_and_deadline(monkeypatch):
    from plugin.plugins.hsr_companion.runtime import followup
    from plugin.plugins.hsr_companion.runtime.pending_reply import handle_pending_reply, message_allows
    from plugin.plugins.hsr_companion.tests.test_page_followup import followup_owner, request
    owner, _ = followup_owner(monkeypatch)
    # The confirming event must be newer than the question but already in
    # the past. asked_at + .001 alone races a fast reader into the future.
    req = replace(request(), asked_at=time.time() - .01)
    owner._pending_answer = req
    owner._answer_query_generation = 7
    raw = {"type": "user_message", "lanlan": "猫娘", "_ts": req.asked_at + .001, "content": "好"}
    owner.bus.memory.get = lambda **kw: [raw]
    async def run():
        for _ in range(2):
            result = await handle_pending_reply(owner, {"request_id": req.request_id, "relation": "confirm", "player_message": "好"})
            assert result["accepted"]
            assert owner._pending_answer is req and owner._pending_answer.expires_at == req.expires_at
            assert owner._answer_query_generation == 7
        from plugin.plugins.hsr_companion.runtime.chat_activity import latest_user_activity
        _, marker = await latest_user_activity(owner.bus, target="猫娘", limit=8, include_identity=True)
        assert message_allows(owner, req, marker)
        raw.update(_ts=time.time(), content="不用了")
        _, marker = await latest_user_activity(owner.bus, target="猫娘", limit=8, include_identity=True)
        assert not message_allows(owner, req, marker)  # A later unclassified message is NOT covered by old confirmation.
        followup.cancel_followup(owner, "test_cleanup")
    asyncio.run(run())


@pytest.mark.parametrize("relation", ["cancel", "new_request"])
def test_semantic_end_prevents_old_followup(monkeypatch, relation):
    from plugin.plugins.hsr_companion.runtime.pending_reply import handle_pending_reply
    from plugin.plugins.hsr_companion.tests.test_page_followup import followup_owner, request
    owner, sent = followup_owner(monkeypatch)
    req = replace(request(), asked_at=time.time() - .01)
    owner._pending_answer = req
    owner.bus.memory.get = lambda **kw: [{"type": "user_message", "lanlan": "猫娘", "_ts": req.asked_at + .001, "content": "不用了"}]
    async def run():
        result = await handle_pending_reply(owner, {"request_id": req.request_id, "relation": relation, "player_message": "不用了"})
        assert result["accepted"] and owner._pending_answer is None
        assert not sent
    asyncio.run(run())


def test_unbound_or_stale_semantic_reply_cannot_authorize(monkeypatch):
    from plugin.plugins.hsr_companion.runtime.pending_reply import handle_pending_reply
    from plugin.plugins.hsr_companion.tests.test_page_followup import followup_owner, request
    owner, _ = followup_owner(monkeypatch)
    req = request()
    owner._pending_answer = req
    owner.bus.memory.get = lambda **kw: [{"type": "user_message", "lanlan": "猫娘", "_ts": req.asked_at + .001, "content": "新问题"}]
    async def run():
        for rid, text in [("obsolete", "新问题"), (req.request_id, "好")]:
            result = await handle_pending_reply(owner, {"request_id": rid, "relation": "confirm", "player_message": text})
            assert not result["accepted"]
        assert owner._pending_answer is req
    asyncio.run(run())


@pytest.mark.parametrize("change", ["text", "page", "stale"])
def test_actual_reviewer_page_is_independent_but_not_unconditionally_authorized(tmp_path, monkeypatch, change):
    from plugin.plugins.hsr_companion.runtime.query_coordinator import satisfied_fields
    from plugin.plugins.hsr_companion.tests.test_delivery_fix_20261012 import frame, picture, text_result
    owner, _, _ = audit_owner(tmp_path)
    calls = []
    raw = text_result()
    raw["page"]["page_type"] = "exploration_hud"
    owner._assistant_client.fetch_frame = lambda seq: frame(picture("page" if change == "page" else "text"), stale=change == "stale")
    def verify(payload, proofs, **kw):
        calls.append(len(proofs))
        return set()
    owner._vision.verify_text_regions = verify
    async def run():
        await commit_raw(owner, raw)
        plan = build_query_plan("我打开的是啥页面？", "read_screen", screen_fields=["page", "quest", "visible_text"])
        result = await live_refresh.refresh_for_query(owner, timeout=.35, query_plan=plan, **plan.refresh_args())
        if change == "text":
            assert result is not None and satisfied_fields(plan, result) == {"page"}
            assert not calls, "Unrelated text must not enqueue native OCR on the page admission path"
        else:
            assert result is None, "Old page cannot pass changed controls or stale capture"
    asyncio.run(run())


def test_cold_three_field_request_admits_scene_worker_not_full_batch(tmp_path):
    owner, _, _ = audit_owner(tmp_path)
    async def produce():
        while not getattr(owner, "_visual_query_waiters", 0):
            await asyncio.sleep(.005)
        assert owner._scene_query_waiters == owner._visual_query_waiters
        assert not getattr(owner, "_content_query_waiters", 0)
        raw = navigation_raw()
        raw["screen_facts"]["facts"] = []
        raw["recognition"]["phase"] = "scene_only"
        await commit_raw(owner, raw)
    async def run():
        plan = build_query_plan("当前页面和任务文字", "read_screen", screen_fields=["page", "quest", "visible_text"])
        worker = asyncio.create_task(produce())
        result = await live_refresh.refresh_for_query(owner, timeout=1, query_plan=plan, **plan.refresh_args())
        await worker
        assert result["scene"]["verified"]
        assert not getattr(owner, "_content_query_waiters", 0)
    asyncio.run(run())


@pytest.mark.parametrize("where", ["before_result", "during_formatting"])
def test_formal_three_fields_confirm_and_complete_once(tmp_path, monkeypatch, where):
    from plugin.plugins.hsr_companion.runtime import followup
    from plugin.plugins.hsr_companion.runtime.answer_message import render_followup
    from plugin.sdk.plugin import NekoPluginBase
    owner, _, sent = audit_owner(tmp_path)
    owner._target_character = lambda: "猫娘"
    events = []
    owner.ctx = SimpleNamespace(bus=NekoPluginBase(SimpleNamespace(metadata={}, plugin_id="isolated-page-reply",
        bus=SimpleNamespace(memory=SimpleNamespace(get=lambda **kw: list(events))))).bus)
    monkeypatch.setattr(followup, "FOLLOWUP_GRACE_SECONDS", .02)
    monkeypatch.setattr(followup, "FOLLOWUP_SECONDS", 3)
    entered = []
    def render(*args, **kw):
        if where == "during_formatting" and not entered:
            events.append({"type": "user_message", "lanlan": "猫娘", "content": "好", "_ts": time.time()})
            entered.append(True)
        return render_followup(*args, **kw)
    monkeypatch.setattr("plugin.plugins.hsr_companion.runtime.answer_message.render_followup", render)
    async def run():
        initial = navigation_raw()
        initial["screen_facts"]["facts"] = [f for f in initial["screen_facts"]["facts"] if f["scope"] != "quest"]
        await commit_raw(owner, initial)
        first = await owner.hsr_companion_prepare(query="我打开的是啥页面？", intent="read_screen",
            screen_fields=["page", "quest", "visible_text"])
        assert first["query_answer"]["state"] == "partial"
        req = owner._pending_answer
        assert req is not None and first["followup"]["pending_reply_contract"]["request_id"] == req.request_id
        if where == "before_result":
            events.append({"type": "user_message", "lanlan": "猫娘", "content": "好", "_ts": time.time()})
        else:
            await commit_raw(owner, navigation_raw())
            deadline = time.monotonic() + 3
            while not entered:
                assert time.monotonic() < deadline, (owner._followup_status, owner._pending_answer)
                await asyncio.sleep(.01)
        generation, expires = owner._answer_query_generation, req.expires_at
        for index in range(2):
            reply = await owner.hsr_companion_prepare(query="好", pending_reply={"request_id": req.request_id,
                "relation": "confirm", "player_message": "好"})
            if not reply["pending_reply"]["accepted"]:
                assert index == 1 and owner._pending_answer is None, (reply, owner._followup_status)
        assert req.expires_at == expires
        assert owner._pending_answer is req or owner._followup_status["state"] == "submitted"
        assert owner._answer_query_generation == generation
        if where == "before_result":
            await commit_raw(owner, navigation_raw())
        await owner._followup_task
        pushes = [s for s in sent if s.get("metadata", {}).get("kind") == "query_followup"]
        assert len(pushes) == 1 and pushes[0]["metadata"]["request_id"] == req.request_id
        assert pushes[0]["metadata"]["outcome"] == "ready"
    asyncio.run(run())


def test_revocation_diagnostics_never_export_arbitrary_cause():
    from plugin.plugins.hsr_companion.runtime.revocation_trace import cause, public_trace, record
    owner = SimpleNamespace(_observation_epoch=2, _observation_serial=4, _service_request={"request_id": "PRIVATE"})
    with cause(origin="PRIVATE", basis="PRIVATE", scope="PRIVATE"):
        record(owner, "PRIVATE", 1)
    assert "PRIVATE" not in str(public_trace(owner))


@pytest.mark.parametrize("action", ["cancel", "new_question"])
def test_formal_new_question_or_cancel_ends_old_obligation(tmp_path, monkeypatch, action):
    from plugin.plugins.hsr_companion.runtime import followup
    from plugin.sdk.plugin import NekoPluginBase
    owner, _, sent = audit_owner(tmp_path)
    owner._target_character = lambda: "猫娘"
    events = []
    owner.ctx = SimpleNamespace(bus=NekoPluginBase(SimpleNamespace(plugin_id="isolated-pending-negative", metadata={},
        bus=SimpleNamespace(memory=SimpleNamespace(get=lambda **kw: list(events))))).bus)
    monkeypatch.setattr(followup, "FOLLOWUP_GRACE_SECONDS", .1)
    async def run():
        initial = navigation_raw()
        initial["screen_facts"]["facts"] = []
        await commit_raw(owner, initial)
        await owner.hsr_companion_prepare(query="当前页面与任务文字", intent="read_screen",
            screen_fields=["page", "quest", "visible_text"])
        req, task = owner._pending_answer, owner._followup_task
        assert req is not None
        events.append({"type": "user_message", "lanlan": "猫娘", "content": "不用了", "_ts": time.time()})
        if action == "cancel":
            result = await owner.hsr_companion_prepare(query="不用了", pending_reply={"request_id": req.request_id,
                "relation": "cancel", "player_message": "不用了"})
            assert result["pending_reply"]["accepted"]
        else:
            await owner.hsr_companion_prepare(query="现在是什么页面", intent="read_screen", screen_fields=["page"])
        await asyncio.gather(task, return_exceptions=True)
        assert owner._pending_answer is None
        assert not any(s.get("metadata", {}).get("kind") == "query_followup" for s in sent)
    asyncio.run(run())


def test_confirmation_does_not_mistake_own_activity_guard_for_message_mismatch(monkeypatch):
    from plugin.plugins.hsr_companion.runtime.chat_activity import latest_user_activity
    from plugin.plugins.hsr_companion.runtime.pending_reply import handle_pending_reply
    from plugin.plugins.hsr_companion.tests.test_page_followup import followup_owner, request
    owner, _ = followup_owner(monkeypatch)
    req = request()
    owner._pending_answer = req
    def get(**kwargs):
        time.sleep(.06)  # The submission guard is already reading the same public bus.
        return [{"type": "user_message", "lanlan": req.target, "content": "好", "_ts": req.asked_at + .001}]
    owner.bus.memory.get = get
    async def run():
        guarding = asyncio.create_task(latest_user_activity(owner.bus, target=req.target, include_identity=True))
        await asyncio.sleep(.01)
        result = await handle_pending_reply(owner, {"request_id": req.request_id, "relation": "confirm", "player_message": "好"})
        await guarding
        assert result["accepted"], result
        assert owner._pending_answer is req
    asyncio.run(run())
