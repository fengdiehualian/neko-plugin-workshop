"""Frozen label/placeholder regressions; no OCR truth injected into production."""
from io import BytesIO

from PIL import Image
import pytest

from plugin.plugins.hsr_companion.adapters.relic_values import refine_relic_values
from plugin.plugins.hsr_companion.adapters.text_verification import proof_key, verify_text_regions
from plugin.plugins.hsr_companion.runtime.screen_facts import extract_screen_facts
from plugin.plugins.hsr_companion.tests.test_relic_fields import relic_boxes
from plugin.plugins.hsr_companion.tests.test_screen_facts import catalog


def parse(boxes):
    return extract_screen_facts(boxes, width=1280, height=800,
                               page={"page_type": "relic_detail"}, catalog=catalog())


def image_bytes():
    stream = BytesIO()
    Image.new("RGB", (200, 100)).save(stream, format="PNG")
    return stream.getvalue()


def proof(text="火属性伤害提高", scope="relic_stat_label"):
    return {"text": text, "validation_scope": scope,
            "region": dict(left=.2, top=.2, right=.8, bottom=.3)}


@pytest.mark.parametrize("marker", [":", "：", "·", "•", "*", "."])
def test_registered_label_ornament_has_same_parse_and_review_meaning(marker):
    boxes = relic_boxes()
    boxes[3]["text"] = marker + "生命值"
    main = next(f for f in parse(boxes)["facts"] if f["kind"] == "main_stat")
    label = next(p for p in main["evidence"] if "生命值" in p["text"])
    assert label["validation_scope"] == "relic_stat_label"

    class Backend:
        def recognize_line(self, sample):
            return [(marker + "生命值", .99)]

    assert verify_text_regions(Backend(), image_bytes(), [label]) == {proof_key(label)}


def test_observed_fire_main_label_colon_no_longer_rejects_correct_label():
    class Backend:
        def recognize_line(self, sample):
            return [(":火属性伤害提高", .941)]

    assert verify_text_regions(Backend(), image_bytes(), [proof()]) == {proof_key(proof())}


@pytest.mark.parametrize("actual,score", [("*攻击力", .884), ("防御力", .99),
    ("x攻击力", .99), ("**攻击力", .99), ("攻击力:35", .99)])
def test_label_semantics_do_not_approve_weak_unknown_conflicting_or_inline_reads(actual, score):
    class Backend:
        def recognize_line(self, sample):
            return [(actual, score)]

    assert not verify_text_regions(Backend(), image_bytes(), [proof("攻击力")])


@pytest.mark.parametrize("text,actual", [("16", ":16"), ("16", "17"), ("38.8%", "38.8"),
                                        ("某件装备", ":某件装备"), ("火属性伤害提高", ":火属性伤害提高")])
def test_ordinary_literals_are_not_given_label_privileges(text, actual):
    class Backend:
        def recognize_line(self, sample):
            return [(actual, .99)]

    assert not verify_text_regions(Backend(), image_bytes(), [proof(text, "literal")])


def test_same_coordinates_different_proof_responsibilities_do_not_collide():
    assert proof_key(proof()) != proof_key(proof(scope="literal"))
    assert proof_key(proof()) == proof_key(proof(":火属性伤害提高"))


def failed_placeholder(box):
    return {**box, "text": box["text"] + ":数值待复核", "score": .884608,
            "numeric_recheck": {"method": "same_frame_badge_separated_ocr", "confirmed": False}}


def test_failed_same_frame_placeholder_cannot_zero_a_correct_five_row_group():
    boxes = relic_boxes()
    boxes.append(failed_placeholder(boxes[3]))
    result = parse(boxes)
    assert len([f for f in result["facts"] if f["kind"] in {"main_stat", "sub_stat"}]) == 5
    assert not any(f["kind"] == "equipment_binding_missing" for f in result["facts"])


def test_malformed_shadow_label_is_not_sent_to_numeric_atlas_to_make_placeholder():
    boxes = relic_boxes()
    boxes.append({**boxes[3], "text": "·生命值.", "score": .884608})

    class Backend:
        def extract_text_with_boxes(self, sample):
            pytest.fail("A failed duplicate label must not create an unnecessary atlas call")

    updated, details = refine_relic_values(Image.new("RGB", (1280, 800)), boxes,
        page={"page_type": "relic_detail"}, backend=Backend())
    assert details["candidates"] == 0
    assert len([f for f in parse(updated)["facts"] if f["kind"] in {"main_stat", "sub_stat"}]) == 5


@pytest.mark.parametrize("case", ["only_placeholder", "different_row", "weak_correct", "conflicting_value"])
def test_placeholder_cannot_be_dropped_without_same_row_high_quality_numeric_support(case):
    boxes = relic_boxes()
    duplicate = failed_placeholder(boxes[3])
    if case == "only_placeholder":
        boxes.pop(3)
    elif case == "different_row":
        duplicate.update(top=duplicate["top"] + 25, bottom=duplicate["bottom"] + 25)
    elif case == "weak_correct":
        boxes[3]["score"] = .89
    else:
        boxes.append({**boxes[4], "text": "706"})
    boxes.append(duplicate)
    assert not any(f["kind"] == "main_stat" for f in parse(boxes)["facts"])


def test_conflicting_inline_value_is_not_a_failed_placeholder():
    boxes = relic_boxes()
    boxes.append({**boxes[3], "text": "生命值:706"})
    assert not any(f["kind"] == "main_stat" for f in parse(boxes)["facts"])


def test_inline_stat_numbers_keep_literal_evidence():
    boxes = relic_boxes()
    boxes[3]["text"] = "生命值:705"
    boxes.pop(4)
    main = next(f for f in parse(boxes)["facts"] if f["kind"] == "main_stat")
    assert all(p["validation_scope"] != "relic_stat_label" for p in main["evidence"])


def test_typed_label_survives_producer_commit_scope_and_formal_tool(tmp_path):
    import asyncio
    from types import SimpleNamespace
    from plugin.plugins.hsr_companion.runtime.live_refresh import stable_screen_content
    from plugin.plugins.hsr_companion.tests.test_chain_audit_20261017 import audit_owner, commit_raw, raw_page
    from plugin.plugins.hsr_companion.tests.test_delivery_fix_20261012 import frame

    boxes = relic_boxes()
    boxes.append(failed_placeholder(boxes[3]))
    raw = raw_page()
    raw["scene"].update(verified=True)
    raw["screen_facts"] = parse(boxes)
    raw["screen_facts"]["visible_text"] = []
    raw["recognition"]["scene_regions"] = []
    original, latest = BytesIO(), BytesIO()
    Image.new("RGB", (1280, 800), "white").save(original, format="PNG")
    Image.new("RGB", (1280, 800), "blue").save(latest, format="PNG")

    def verifier(payload, proofs, **kw):
        confirmed = set()
        for item in proofs:
            class Backend:
                def recognize_line(self, sample):
                    actual = (":" if item.get("validation_scope") == "relic_stat_label" else "") + item["text"]
                    return [(actual, .99)]
            confirmed.update(verify_text_regions(Backend(), payload, [item]))
        return confirmed

    reviewed = stable_screen_content(raw, original.getvalue(), latest.getvalue(), verifier=verifier)
    assert len([f for f in reviewed["screen_facts"]["facts"] if f["kind"] in {"main_stat", "sub_stat"}]) == 5

    async def run():
        owner, _, _ = audit_owner(tmp_path)
        owner._assistant_client = SimpleNamespace(fetch_frame=lambda seq: frame(latest.getvalue()))
        owner._vision = SimpleNamespace(verify_text_regions=verifier)
        await commit_raw(owner, reviewed, payload=latest.getvalue())
        result = await owner.hsr_companion_prepare(query="这件遗器的主副词条是什么？")
        assert result["query_answer"]["state"] == "ready", (result.get("query_timing"), result.get("current_visual_evidence"))
        card = result["equipment_answer"]
        assert card["main_stat"]["value"] == "705"
        assert len(card["sub_stats"]) == 4
        evidence = result["current_visual_evidence"]["screen_facts"]["facts"]
        assert any(p.get("validation_scope") == "relic_stat_label" for f in evidence for p in f["evidence"])
    asyncio.run(run())
