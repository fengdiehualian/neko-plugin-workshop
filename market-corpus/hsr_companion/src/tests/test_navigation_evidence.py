"""Real OCR geometry replay plus generated variants, not live accuracy claims."""
from io import BytesIO
from types import SimpleNamespace

import pytest
from PIL import Image
from plugin.plugins.hsr_companion.adapters.local_vision import LocalVisionAdapter
from plugin.plugins.hsr_companion.adapters.navigation_reader import recover_navigation_controls, reread_navigation_lines
from plugin.plugins.hsr_companion.detectors.exploration import navigation_boxes, task_stacks
from plugin.plugins.hsr_companion.detectors.layout import classify_supported_page
from plugin.plugins.hsr_companion.runtime.screen_facts import extract_screen_facts
from plugin.plugins.hsr_companion.tests.test_screen_facts import catalog


def row(text, left, top, right, bottom, score=.99):
    return dict(text=text, left=left, top=top, right=right, bottom=bottom, score=score)


def raw_hud():
    # Source: 2026-10-15 live OCR. Text is parameterized below; no screenshot.
    return [row("二维市", 80, 18, 167, 54),
            row("剧场及其复象", 100, 371, 301, 414),
            row("向虚照追问现况", 107, 426, 310, 459.5),
            row("请前往目的地甲", 93, 448, 492, 511, .9332),
            row("状态效果", 2360, 386, 2480, 420)]


def facts(boxes, size=(2560, 1600)):
    page = classify_supported_page("\n".join(b["text"] for b in boxes), boxes, width=size[0], height=size[1])
    screen = extract_screen_facts(boxes, width=size[0], height=size[1], page=page, catalog=catalog())
    return page, screen


@pytest.mark.parametrize("scale", [.5, .75, 1, 1.5])
@pytest.mark.parametrize("title,objective", [
    ("剧场及其复象", "向虚照追问现况"), ("另一段旅途", "寻找新的线索"),
    ("独立测试标题", "询问向导关于遗迹的消息"),
])
def test_task_binding_tolerates_overlapping_detection_box_not_just_one_task(scale, title, objective):
    boxes = raw_hud()
    boxes[1]["text"], boxes[2]["text"] = title, objective
    boxes = [{**b, **{k: b[k] * scale for k in ("left", "top", "right", "bottom")}} for b in boxes]
    page, screen = facts(boxes, (2560 * scale, 1600 * scale))
    assert page["page_type"] == "exploration_hud"
    values = {f["kind"]: f["value"] for f in screen["facts"]}
    assert values["tracked_quest"] == title
    assert values["quest_objective"] == objective
    assert "quest_destination" not in values  # Overlapping detector box is not a clean literal proof.


@pytest.mark.parametrize("failure", ["empty", "low", "disagree", "too_tall"])
def test_failed_literal_reread_keeps_control_but_cannot_publish_destination(failure):
    boxes = [raw_hud()[3], raw_hud()[4]]
    if failure == "too_tall":
        boxes[0]["bottom"] = boxes[0]["top"] + 90
    calls = []
    def recognize(sample):
        calls.append(sample.size)
        if failure == "empty":
            return []
        return [("请前往目的地甲" if len(calls) == 1 else "请前往目的地乙", .8 if failure == "low" else .99)]
    refined, meta = reread_navigation_lines(SimpleNamespace(recognize_line=recognize), Image.new("RGB", (2560, 1600)), boxes)
    assert len(navigation_boxes(refined, width=2560, height=1600)) == 1
    assert meta["control_only_retained"] == 1
    page, screen = facts(refined)
    assert page["page_type"] == "exploration_hud"
    assert not screen["facts"]
    assert not any("目的地" in b["text"] for b in screen["visible_text"] + screen["unclassified_text"])


@pytest.mark.parametrize("change", ["same_row", "too_much_overlap", "missing_objective", "conflicting_objective"])
def test_overlap_rule_does_not_guess_missing_or_conflicting_task(change):
    boxes = raw_hud()
    if change == "same_row":
        boxes[2].update(top=465, bottom=495)
    elif change == "too_much_overlap":
        boxes[3]["top"] = 420
    elif change == "missing_objective":
        boxes.pop(2)
    else:
        boxes.append({**boxes[2], "text": "另一个目标"})
    assert not task_stacks(boxes, width=2560, height=1600)


def test_ambiguous_nearest_row_does_not_promote_older_heading_to_task_title():
    boxes = raw_hud() + [row("更早的无关标题", 100, 320, 301, 350),
                         {**raw_hud()[1], "text": "冲突标题"}]
    assert not task_stacks(boxes, width=2560, height=1600)


def test_failed_nearest_row_stays_a_placeholder_not_an_older_task():
    boxes = raw_hud() + [row("更早的无关标题", 100, 320, 301, 350)]
    boxes[1]["literal_unconfirmed"] = True
    assert not task_stacks(boxes, width=2560, height=1600)
    _, screen = facts(boxes)
    assert not any(f["kind"] in {"tracked_quest", "quest_objective"} for f in screen["facts"])


def test_rejected_menu_label_cannot_reappear_through_text_only_classifier():
    boxes = [{**row("队伍编成", 100, 20, 250, 50), "literal_unconfirmed": True}]
    page, screen = facts(boxes)
    assert page["page_type"] == "unknown"
    assert not screen["visible_text"]


@pytest.mark.parametrize("recovered", [False, True])
def test_reread_padding_is_not_literal_evidence_unless_new_glyphs_were_recovered(recovered):
    original = row("维市" if recovered else "二维市", 80, 18, 167, 54)
    refined, _ = reread_navigation_lines(SimpleNamespace(recognize_line=lambda sample: [("二维市", .99)]),
        Image.new("RGB", (2560, 1600)), [original])
    assert refined[0]["text"] == "二维市"
    old_bounds = tuple(original[k] for k in ("left", "top", "right", "bottom"))
    new_bounds = tuple(refined[0][k] for k in ("left", "top", "right", "bottom"))
    assert (new_bounds != old_bounds) is recovered


@pytest.mark.parametrize("navigation", [True, False])
def test_unknown_thumbnail_recovers_native_hud_controls_on_same_frame(navigation):
    boxes = raw_hud()
    calls = []
    def extract(sample):
        calls.append(sample.size)
        if len(calls) == 1:
            return "", []  # Thumbnail missed the controls, not a disconnection.
        if len(calls) == 2:
            return "", boxes[:4]  # Left crop, native source coordinates.
        assert len(calls) == 3
        return "", [{**boxes[4], "left": 2360 - 1792, "right": 2480 - 1792,
                      "top": 386 - 160, "bottom": 420 - 160}]
    adapter = LocalVisionAdapter(catalog())
    adapter._get_backend = lambda: SimpleNamespace(extract_text_with_boxes=extract)
    stream = BytesIO()
    Image.new("RGB", (2560, 1600)).save(stream, format="PNG")
    selected = adapter.analyze_scene(stream.getvalue(), navigation=navigation)
    assert len(calls) == 3
    assert selected["page"]["page_type"] == "exploration_hud"
    assert selected["scene"]["verified"] and selected["recognition"]["scene_regions"]
    assert any(f["kind"] == "quest_objective" for f in selected["screen_facts"]["facts"])
    assert not selected["characters"]


def test_known_menu_keeps_precedence_and_does_not_read_exploration_crops():
    calls = []
    def extract(sample):
        calls.append(sample.size)
        return "", [row("队伍编成", 100, 20, 250, 50)]
    adapter = LocalVisionAdapter(catalog())
    adapter._get_backend = lambda: SimpleNamespace(extract_text_with_boxes=extract)
    stream = BytesIO()
    Image.new("RGB", (1280, 800)).save(stream, format="PNG")
    selected = adapter.analyze_navigation(stream.getvalue())
    assert len(calls) == 1
    assert selected["page"]["page_type"] == "team_setup"
    assert not any(f["kind"] == "current_location" for f in selected["screen_facts"]["facts"])


@pytest.mark.parametrize("score", [.8189, .832, .8454])
def test_low_average_navigation_needs_new_prefix_proof_not_lower_acceptance_threshold(score):
    boxes = raw_hud()
    boxes[3]["score"] = score  # 18:00 live failures; destination lowered the whole-line average.
    assert not navigation_boxes(boxes, width=2560, height=1600)
    backend = SimpleNamespace(recognize_line=lambda image: [("请前往千", .97)])
    recovered, meta = recover_navigation_controls(backend, Image.new("RGB", (2560, 1600)), boxes)
    assert meta["confirmed"] == 1
    page, screen = facts(recovered)
    assert page["page_type"] == "exploration_hud"
    assert {f["kind"] for f in screen["facts"]} >= {"current_location", "tracked_quest", "quest_objective"}
    assert "quest_destination" not in {f["kind"] for f in screen["facts"]}
    assert not any("目的地甲" in b["text"] for b in screen["visible_text"] + screen["unclassified_text"])


@pytest.mark.parametrize("readings", [[], [("请前往千", .89)], [("请前往目的地以及更多内容", .99)],
                                      [("请前住千", .99)], [("别的控件", .99)]])
def test_weak_or_wrong_prefix_cannot_rescue_navigation(readings):
    boxes = raw_hud()
    boxes[3]["score"] = .8189
    recovered, meta = recover_navigation_controls(SimpleNamespace(recognize_line=lambda image: readings),
        Image.new("RGB", (2560, 1600)), boxes)
    assert meta["confirmed"] == 0
    assert not navigation_boxes(recovered, width=2560, height=1600)
    assert facts(recovered)[0]["page_type"] == "unknown"


def test_one_scale_prefix_confirmation_is_not_enough():
    boxes = raw_hud()
    boxes[3]["score"] = .8189
    count = 0
    def recognize(image):
        nonlocal count
        count += 1
        return [("请前往千" if count % 2 else "其他文字", .99)]
    recovered, meta = recover_navigation_controls(SimpleNamespace(recognize_line=recognize),
        Image.new("RGB", (2560, 1600)), boxes)
    assert meta["confirmed"] == 0
    assert not navigation_boxes(recovered, width=2560, height=1600)


def test_recovered_control_still_requires_independent_hud_and_cannot_become_literal():
    boxes = raw_hud()[:4]
    boxes[3]["score"] = .8189
    backend = SimpleNamespace(recognize_line=lambda image: [("请前往千", .97)])
    image = Image.new("RGB", (2560, 1600))
    recovered, meta = recover_navigation_controls(backend, image, boxes)
    refined, _ = reread_navigation_lines(backend, image, recovered)
    assert meta["confirmed"] == 1
    assert facts(refined)[0]["page_type"] == "unknown"
    assert refined[-1]["control_only"]


def test_full_read_uses_same_prefix_recovery_not_navigation_only_special_case(monkeypatch):
    boxes = raw_hud()
    boxes[3]["score"] = .8189
    monkeypatch.setattr("plugin.plugins.hsr_companion.adapters.local_vision.read_native_text",
        lambda *args, **kwargs: ("\n".join(b["text"] for b in boxes), boxes, {}))
    adapter = LocalVisionAdapter(catalog())
    adapter._get_backend = lambda: SimpleNamespace(recognize_line=lambda image: [("请前往千", .97)])
    stream = BytesIO()
    Image.new("RGB", (2560, 1600)).save(stream, format="PNG")
    result = adapter.analyze(stream.getvalue())
    assert result["page"]["page_type"] == "exploration_hud"
    assert {f["kind"] for f in result["screen_facts"]["facts"]} >= {"current_location", "tracked_quest", "quest_objective"}
    assert "目的地甲" not in result["ocr"]["text"]
