"""Next-build acceptance contracts (the frozen 0.9.5 plan is NOT edited).

Matrix: community page families/negative prose; same-page animation vs UI
changes; unknown readable text; explicit answer readiness, exactly-one push,
new query/user message/page/target/pause cancellation and bounded failure.
Synthetic OCR/pixels test algorithms, NOT real-game accuracy or first-speech
latency. Real acceptance must include rapid switches and ordinary play.
"""

import asyncio
import copy
import importlib
import time
from dataclasses import replace
from io import BytesIO
from types import SimpleNamespace

import pytest
from PIL import Image, ImageDraw
from plugin.plugins.hsr_companion.detectors.layout import classify_supported_page, scene_control_regions
from plugin.plugins.hsr_companion.detectors.scene_preview import scene_preview
from plugin.plugins.hsr_companion.runtime import followup
from plugin.plugins.hsr_companion.runtime.chat_activity import latest_user_activity
from plugin.plugins.hsr_companion.runtime.live_refresh import (
    fact_regions,
    frame_stamp,
    invalidate_frame_change,
    watch_latest_frame,
)
from plugin.plugins.hsr_companion.runtime.observation import asks_scene_only, public_visual_evidence
from plugin.plugins.hsr_companion.runtime.screen_facts import extract_screen_facts
from plugin.plugins.hsr_companion.tests.test_current_observation import plugin_fixture
from plugin.plugins.hsr_companion.tests.test_experience_fixes import box, catalog


def png(image):
    stream = BytesIO()
    image.save(stream, format="PNG")
    return stream.getvalue()


@pytest.mark.parametrize("title,control,page", [
    ("任务", "停止追踪", "mission_menu"), ("任务详情", "任务奖励", "mission_menu"),
    ("星际和平指南", "生存索引", "guide"), ("无名勋礼", "本周任务", "battle_pass"),
    ("地图", "空间锚点", "map_menu"), ("教程", "战斗教程", "tutorial"),
    ("短信", "联系人", "messages"), ("拍照", "隐藏界面", "camera"),
    ("合成", "材料置换", "synthesize"), ("设置", "声音设置", "settings"),
    ("商店", "余烬兑换", "shop"), ("开拓等级", "好友", "phone_menu"),
    ("旅情事记", "奖励指南", "activity_menu"),
])
def test_community_page_family_title_and_control(title, control, page):
    boxes = [box(title, 25, 20), box(control, 320, 270)]
    result = classify_supported_page("\n".join(b["text"] for b in boxes), boxes, width=1000, height=600)
    assert result["page_type"] == page and result["confidence"] == "high"
    assert scene_preview(boxes, width=1000, height=600)["page"]["page_type"] == page
    assert scene_control_regions(result, boxes, width=1000, height=600)


@pytest.mark.parametrize("title", ["任务", "教程", "设置", "商店", "星轨航图", "无名客的荣勋"])
def test_generic_heading_alone_or_in_body_does_not_establish_page(title):
    for boxes in ([box(title, 25, 20)], [box(title, 25, 350), box("任务奖励", 300, 450)]):
        page = classify_supported_page("\n".join(b["text"] for b in boxes), boxes, width=1000, height=600)
        assert page["page_type"] == "unknown"
        assert scene_preview(boxes, width=1000, height=600) is None


def test_unknown_readable_page_does_not_become_loading_or_location(tmp_path):
    boxes = [box("新活动说明", 20, 20), box("请先完成前置挑战", 30, 200), box("UID 123456789", 50, 550)]
    page = classify_supported_page("\n".join(b["text"] for b in boxes), boxes, width=1000, height=600)
    facts = extract_screen_facts(boxes, width=1000, height=600, page=page, catalog=catalog(tmp_path))
    assert page["page_type"] == "unknown" and page["scene"] != "loading"
    assert facts["visible_text"][0]["text"] == "新活动说明"
    assert "123456789" not in str(facts)
    assert not any(f["kind"] == "current_location" for f in facts["facts"])
    assert asks_scene_only("现在在什么页面？")
    stale = public_visual_evidence({"invalidated": True, "screen_facts": facts}, {})
    assert stale["screen_facts"]["visible_text"] == []


def test_menu_background_animation_uses_ui_proofs_but_changed_title_revokes():
    a = Image.new("RGB", (640, 400), "black")
    draw = ImageDraw.Draw(a)
    draw.text((20, 20), "PAGE A", fill="white")
    b = a.copy()
    ImageDraw.Draw(b).rectangle((0, 100, 640, 400), fill="white")
    c = b.copy()
    ImageDraw.Draw(c).rectangle((0, 0, 150, 60), fill="white")
    region = {"region": {"left": 0, "top": 0, "right": .25, "bottom": .15}}
    revoked = []

    async def invalidate(reason):
        revoked.append(reason)

    owner = SimpleNamespace(_accepted_frame_stamp=frame_stamp(png(a)), _accepted_frame_characters=[],
                            _accepted_frame_scene_regions=[region], _accepted_frame_page_type="warp",
                            _invalidate_current_observation=invalidate)
    async def run():
        assert not await watch_latest_frame(owner, png(b), helper=True)
        assert await watch_latest_frame(owner, png(c), helper=True)
    asyncio.run(run())
    assert len(revoked) == 1


def visual(page="team_setup", *, ready=True):
    return {"fresh": ready, "page_type": page, "scene": {"verified": ready, "label": "队伍编成"},
            "characters": [{"name": "角色甲"}], "screen_facts": {"facts": []}, "read_scope": "full"}


def request(*, scene_only=True, navigation=False, content=False):
    return followup.AnswerRequest("request-1", "现在是什么界面", "猫娘", time.time(), time.monotonic(),
                                  time.monotonic() + 10, "unknown", scene_only, navigation, content, False)


def test_navigation_needs_both_place_and_objective_not_names_or_destination():
    value = visual("exploration_hud")
    value["screen_facts"]["facts"] = [{"kind": "quest_destination", "value": "目的地"}]
    req = request(navigation=True)
    assert not followup.answer_ready(value, req)
    value["screen_facts"]["facts"] += [{"kind": "current_location", "value": "所在地"},
                                        {"kind": "quest_objective", "value": "目标"}]
    assert followup.answer_ready(value, req)
    assert "characters" not in followup.reply_data(value, req)


def test_content_followup_cannot_be_satisfied_by_unrelated_fact():
    value = visual("unknown")
    value["screen_facts"]["facts"] = [{"kind": "quest_objective", "scope": "quest", "value": "无关任务"}]
    req = replace(request(scene_only=False, content=True), query="当前页面文字写了什么")
    assert not followup.answer_ready(value, req)
    value["screen_facts"]["visible_text"] = [{"text": "新页面说明"}]
    assert followup.answer_ready(value, req)
    value["read_scope"] = "scene"
    assert not followup.answer_ready(value, req)


def followup_owner(monkeypatch, *, value=None):
    sent = []
    owner = SimpleNamespace(_observer_status={"enabled": True, "state": "observing"}, _pending_answer=None, _observation_epoch=0,
                            _observation_serial=((value or {}).get("screen_facts") or {}).get("observation_serial", 0),
                            _target_character=lambda: "猫娘", _cancel_pending_speech=lambda reason: None,
                            _get_speech_arbiter=lambda: SimpleNamespace(query_started=lambda **kw: None),
                            push_message=lambda **kw: sent.append(kw) or {"submitted": True})
    owner.bus = SimpleNamespace(memory=SimpleNamespace(get=lambda **kw: []))
    owner._test_answer_payloads = []
    record = followup.record_answer_delivery

    def capture(*args, **kwargs):
        owner._test_answer_payloads.append(copy.deepcopy(kwargs["payload"]))
        return record(*args, **kwargs)

    monkeypatch.setattr(followup, "record_answer_delivery", capture)
    async def current():
        return value if value is not None else visual()
    async def activity(*args, **kwargs):
        return "available", None
    async def validated(owner, evidence, **kwargs):
        if owner._observer_status.get("state") == "error":
            owner._observer_status["state"] = "observing"  # Fake successful live recovery boundary.
        return evidence
    owner._current_visual_evidence = current
    monkeypatch.setattr(followup, "FOLLOWUP_GRACE_SECONDS", 0)
    monkeypatch.setattr(followup, "latest_user_activity", activity)
    monkeypatch.setattr(followup, "validated_delivery", validated)
    return owner, sent


def test_delayed_result_triggers_one_targeted_respond_not_read(monkeypatch):
    owner, sent = followup_owner(monkeypatch)
    async def run():
        armed = followup.arm_followup(owner, query="现在是什么界面", visual=visual(ready=False), asked_at=time.time(),
                                     scene_only=True, navigation=False, content=False, controlled=False)
        assert armed["pending"]
        await owner._followup_task
        assert owner._pending_answer is None
        await followup.deliver_followup(owner, request())
    asyncio.run(run())
    assert len(sent) == 1 and sent[0]["ai_behavior"] == "respond"
    assert sent[0]["target_lanlan"] == "猫娘" and sent[0]["metadata"]["outcome"] == "ready"
    assert "角色甲" not in str(sent)


@pytest.mark.parametrize("change", ["new_query", "pause", "paused_state", "target", "page", "user_message", "activity_failure", "activity_unsupported", "invalidate_at_validation"])
def test_cancelled_followup_never_submits(monkeypatch, tmp_path, change):
    owner, sent = followup_owner(monkeypatch)
    async def run():
        req = replace(request(), page_type="warp" if change == "page" else "unknown")
        owner._pending_answer = req
        if change == "new_query":
            followup.cancel_followup(owner, "new_query")
        if change == "pause":
            owner._observer_status["enabled"] = False
        if change == "paused_state":
            owner._observer_status["state"] = "paused"
        if change == "target":
            owner._target_character = lambda: "其他猫娘"
        if change in {"user_message", "activity_failure", "activity_unsupported"}:
            async def activity(*args, **kwargs):
                return ("available", req.asked_at + 1) if change == "user_message" else ("unsupported" if change == "activity_unsupported" else "unavailable", None)
            monkeypatch.setattr(followup, "latest_user_activity", activity)
        if change == "invalidate_at_validation":
            async def invalidate(owner, evidence, **kwargs):
                owner._observation_epoch += 1
                followup.cancel_followup(owner, "invalidated")
                return evidence
            monkeypatch.setattr(followup, "validated_delivery", invalidate)
        await followup.deliver_followup(owner, req)
    asyncio.run(run())
    assert not sent


def test_followup_timeout_finishes_wait_once_without_fabricated_answer(monkeypatch):
    owner, sent = followup_owner(monkeypatch, value=visual(ready=False))
    async def run():
        req = replace(request(), expires_at=0)
        owner._pending_answer = req
        await followup.deliver_followup(owner, req)
        await followup.deliver_followup(owner, req)
    asyncio.run(run())
    assert not sent
    assert owner._pending_answer is None
    assert owner._followup_status["state"] == "expired"
    assert owner._followup_status["outcome"] == "timeout"


def test_prepare_timeout_registers_obligation_but_new_query_cancels_it(tmp_path, monkeypatch):
    owner, _, _ = plugin_fixture(tmp_path)
    owner._target_character = lambda: "猫娘"
    owner.ctx = SimpleNamespace(bus=SimpleNamespace(memory=SimpleNamespace(get=lambda **kw: [])))
    module = importlib.import_module(type(owner).__module__)
    async def timeout(owner, **kwargs):
        # Coordination consumes part of the same deadline; OS scheduling
        # must not be assumed to take exactly zero milliseconds.
        assert 0 < kwargs["timeout"] <= followup.DIRECT_WAIT_SECONDS
        owner._last_refresh_diagnostics = {"outcome": "timeout"}
        return None
    monkeypatch.setattr(module, "refresh_for_query", timeout)
    async def run():
        result = await owner.hsr_companion_prepare(query="现在是什么页面？")
        assert result["followup"]["pending"]
        task = owner._followup_task
        owner._note_speech_query()
        assert owner._pending_answer is None
        await asyncio.gather(task, return_exceptions=True)
    asyncio.run(run())


def test_prepare_returns_unknown_page_text_without_registering_fake_wait(tmp_path, monkeypatch):
    owner, _, _ = plugin_fixture(tmp_path)
    module = importlib.import_module(type(owner).__module__)

    async def completed(owner, **kwargs):
        owner._last_refresh_diagnostics = {"outcome": "ready"}
        return await owner._current_visual_evidence()  # Explicit checked-result boundary, not timeout fallback.

    monkeypatch.setattr(module, "refresh_for_query", completed)

    async def run():
        from plugin.plugins.hsr_companion.tests.test_current_observation import analysis
        value = analysis(roles=False)
        value["recognition"].update(phase="scene_only_complete", scene_only_query=True)
        region = {"left": .1, "top": .1, "right": .5, "bottom": .15}
        value["screen_facts"] = {"facts": [], "visible_text": [{"text": "未知活动的规则说明", "role": "heading_candidate", "region": region}]}
        assert fact_regions(value) == [{"region": region}]
        await owner._apply_local_analysis(value, image_fingerprint="unknown-page", source="assistant_local_ocr")
        assert {"region": region} in owner._accepted_frame_characters
        answer = await owner.hsr_companion_prepare(query="现在是什么页面？")
        assert not answer["followup"]["pending"]
        assert answer["current_visual_evidence"]["page_type"] == "unknown"
        assert answer["screen_scene_answer"]["visible_text"][0]["text"] == "未知活动的规则说明"
        assert "页面未知时可转述 visible_text" in answer["screen_scene_answer"]["instruction"]

    asyncio.run(run())


def test_unsupported_host_does_not_register_a_promise(monkeypatch):
    owner, sent = followup_owner(monkeypatch)
    owner.bus = None
    result = followup.arm_followup(owner, query="现在是什么界面", visual=visual(ready=False), asked_at=time.time(),
                                  scene_only=True, navigation=False, content=False, controlled=False)
    assert not result["pending"] and result["reason"] == "activity_guard_unsupported"
    assert owner._pending_answer is None and not sent


@pytest.mark.parametrize("at", ["activity", "validation", "final_activity"])
def test_obsolete_worker_cannot_cancel_replacement_request(monkeypatch, at):
    owner, sent = followup_owner(monkeypatch)
    first = request()
    second = replace(first, request_id="replacement")
    calls = 0

    async def activity(*args, **kwargs):
        nonlocal calls
        calls += 1
        if at == "activity" or at == "final_activity" and calls == 2:
            owner._pending_answer = second
            return "unavailable", None
        return "available", None

    async def validate(owner, evidence, **kwargs):
        if at == "validation":
            owner._pending_answer = second
        return evidence

    monkeypatch.setattr(followup, "latest_user_activity", activity)
    monkeypatch.setattr(followup, "validated_delivery", validate)
    owner._pending_answer = first
    asyncio.run(followup.deliver_followup(owner, first))
    assert owner._pending_answer is second and not sent


def test_bounded_activity_read_sees_new_user_before_assistant_records():
    calls = []

    async def get(**kwargs):
        calls.append(kwargs)
        return [{"type": "user_message", "lanlan": "猫娘", "_ts": 100},
                {"type": "assistant_message", "lanlan": "猫娘", "_ts": 101},
                {"type": "user_message", "lanlan": "另一只", "_ts": 101}]

    result = asyncio.run(latest_user_activity(SimpleNamespace(memory=SimpleNamespace(get=get)),
                                            target="猫娘", now=102, limit=8))
    assert result == ("available", 100)
    assert calls == [{"bucket_id": "猫娘", "limit": 8, "timeout": .25}]


@pytest.mark.parametrize("changed_title", [False, True])
def test_partial_content_update_never_preserves_old_quest_or_roster(changed_title):
    previous = Image.new("RGB", (640, 400), "black")
    current = previous.copy()
    ImageDraw.Draw(current).rectangle((0, 0, 200, 60) if changed_title else (0, 250, 500, 390), fill="white")
    control = {"region": {"left": 0, "top": 0, "right": .3, "bottom": .15}}
    known = {**visual(), "observed_at": "2026-10-12T10:00:00+00:00"}
    known["scene"].update(primary_state="menu")
    known["screen_facts"]["facts"] = [{"kind": "quest_objective", "value": "旧任务"}]
    previews = []
    owner = SimpleNamespace(_observation_epoch=0)

    async def read():
        return known

    async def invalidate(reason):
        owner._observation_epoch += 1

    async def publish(value):
        previews.append(value)

    owner._current_visual_evidence = read
    owner._invalidate_current_observation = invalidate
    owner._publish_scene_preview = publish
    asyncio.run(invalidate_frame_change(owner, frame_stamp(png(previous)), frame_stamp(png(current)),
                                        [control], "content_changed"))
    assert owner._observation_epoch == 1
    if changed_title:
        assert not previews
    else:
        assert len(previews) == 1 and previews[0]["page"]["page_type"] == "team_setup"
        assert previews[0]["characters"] == [] and previews[0]["screen_facts"] == {"facts": [], "visible_text": []}
        assert previews[0]["observed_at"] == known["observed_at"]  # No artificial TTL extension.


def test_invalidation_during_partial_read_cannot_republish_old_page():
    revoked, published = [], []
    owner = SimpleNamespace(_observation_epoch=0)

    async def read():
        owner._observation_epoch += 1
        return visual()

    async def invalidate(reason):
        revoked.append(reason)

    async def publish(value):
        published.append(value)

    owner._current_visual_evidence, owner._invalidate_current_observation, owner._publish_scene_preview = read, invalidate, publish
    stamp = frame_stamp(png(Image.new("RGB", (640, 400), "black")))
    asyncio.run(invalidate_frame_change(owner, stamp, stamp, [{"region": {"left": 0, "top": 0, "right": .3, "bottom": .15}}], "changed"))
    assert not revoked and not published
