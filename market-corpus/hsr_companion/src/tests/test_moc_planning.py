"""D2 fixed constraints: account plans are not pixels or damage simulations."""
import asyncio
from copy import deepcopy
from datetime import datetime

import pytest
from plugin.plugins.hsr_companion.runtime.challenge import assign_teams, build_challenge_plan
from plugin.plugins.hsr_companion.runtime.guidance import GuidanceCatalog, build_guidance
from plugin.plugins.hsr_companion.runtime.query_plan import build_query_plan
from plugin.plugins.hsr_companion.tests import test_guidance
from plugin.plugins.hsr_companion.tests.test_guidance import plan

NOW = datetime.fromisoformat("2026-10-19T12:00:00+08:00")


@pytest.fixture
def book():
    return test_guidance.book.__wrapped__()


@pytest.fixture
def catalog(tmp_path):
    return test_guidance.catalog.__wrapped__(tmp_path)


def test_ordinary_floor_and_difficulty_are_explicit(book, catalog):
    selected = plan("按我保存的角色，本期混沌普通12层怎么配队？", book, catalog)
    assert selected.challenge_floor == 12 and selected.challenge_difficulty == "normal"
    assert not selected.requires_pixels


def test_star_mode_is_not_ordinary_and_unrelated_questions_remain_old_routes(book, catalog):
    selected = plan("混沌星启12层怎么配队", book, catalog)
    assert selected.challenge_difficulty == "star"
    for query in ("现在在哪，任务要做什么", "这件遗器完整主副词条是什么", "真珠防御力多少合适"):
        assert not build_challenge_plan(plan(query, book, catalog), book=book, catalog=catalog, profile={}, now=NOW)["requested"]


def test_missing_floor_or_difficulty_is_not_guessed(book, catalog):
    for query in ("这期混沌怎么配队", "混沌12层怎么配队", "普通混沌11层怎么配队"):
        result = build_challenge_plan(plan(query, book, catalog), book=book, catalog=catalog, profile={}, now=NOW)
        assert result["state"] in {"needs_scope", "out_of_scope"}
        assert not result["assignments"]


def test_unspecified_stage_never_falls_back_to_generic_complete_teams(book, catalog):
    result = challenge_answer("本期混沌12层怎么配队", book, catalog, {})
    assert result["state"] == "needs_scope" and not result["team_candidates"]
    assert result["guides"]  # Period facts still work, only node teams need scope.
    assert "哪一层" not in result["challenge_plan"]["question"]


def member(form, identity=None):
    return {"form_id": form, "identity_id": identity or form, "name": form, "eidolon": 0,
            "light_cone": None, "panel": {"state": "not_provided"}}


def option(name, forms):
    return {"id": name, "members": [member(form) for form in forms], "missing_slots": [],
            "conditions": [], "ready": True}


def test_global_assignment_avoids_greedy_shared_role_conflict():
    candidates = [[option("upper_a", ["a", "b", "c", "s"]), option("upper_b", ["a", "b", "c", "t"])],
                  [option("lower", ["d", "e", "f", "s"])]]
    chosen = assign_teams(candidates)
    assert [team["id"] for team in chosen] == ["upper_b", "lower"]


def test_same_identity_different_form_cannot_be_used_twice():
    upper, lower = option("upper", ["a", "b", "c", "s"]), option("lower", ["d", "e", "f", "t"])
    upper["members"][-1]["identity_id"] = lower["members"][-1]["identity_id"] = "same_person"
    assert assign_teams([[upper], [lower]]) == []


def test_partial_does_not_invent_missing_member_or_mutate_input():
    incomplete = option("missing", ["a", "b", "c"])
    incomplete.update(ready=False, missing_slots=[{"choices": ["s"], "reason": "not_in_saved_roster"}])
    before = deepcopy(incomplete)
    assert assign_teams([[incomplete], [option("lower", ["d", "e", "f", "t"])]]) == []
    assert incomplete == before


def test_expired_period_cannot_be_claimed_as_current(book, catalog):
    selected = plan("本期混沌普通12层怎么配队", book, catalog)
    result = build_challenge_plan(selected, book=book, catalog=catalog, profile={},
                                  now=datetime.fromisoformat("2026-11-02T04:00:00+08:00"))
    assert not result["assignments"] and result["state"] == "coverage_unavailable"


@pytest.fixture
def builds(book, catalog, monkeypatch):
    """Synthetic account for constraint assertions, NOT the real user's roster."""
    names = {"1506": "银狼LV.999", "1501": "火花", "1502": "爻光", "1503": "真珠",
             "1401": "大黑塔", "1405": "那刻夏", "1403": "缇宝", "1208": "符玄"}
    lookup, records = catalog.get, {}
    ids = {f for t in book.data["challenge_teams"] for slot in t["slots"] for f in slot}
    for form_id in ids:
        number = form_id.split(".")[-1]
        identity = catalog.resolve_character(names[number]) if number in names else None
        records[form_id] = {"id": form_id, "name": names.get(number, number), "entity_type": "character_form",
                            "identity_id": identity["id"] if identity else "character.fixture_" + number, "path_id": "test_path",
                            "sources": [{"source": "synthetic_test"}]}
    records["light_cone.fixture"] = {"id": "light_cone.fixture", "name": "测试光锥", "path": "test_path"}
    records["light_cone.wrong"] = {"id": "light_cone.wrong", "path": "wrong_path"}
    monkeypatch.setattr(catalog, "get", lambda key: deepcopy(records[key]) if key in records else lookup(key))
    rows = [{"form_id": "character_form." + number, "identity_id": records["character_form." + number]["identity_id"],
             "name": name, "level": 80, "eidolon": 0,
             "light_cone": {"id": "light_cone.fixture", "name": "测试光锥", "superimposition": 1},
             "panel": {"state": "provided", "values": {"def": "3132"}} if number == "1503" else {"state": "not_provided"}}
            for number, name in names.items()]
    return {"imported_builds": {"scope": "player_confirmed_builds", "characters": rows}}


def challenge_answer(query, book, catalog, profile, **kwargs):
    return build_guidance(plan(query, book, catalog, **kwargs.pop("routing", {})), query,
                          book=book, catalog=catalog, profile=profile, now=NOW, **kwargs)


def test_complete_account_gets_two_disjoint_conditional_teams(book, catalog, builds):
    before = deepcopy(builds)
    answer = challenge_answer("本期混沌普通12层怎么配队", book, catalog, builds)
    result = answer["challenge_plan"]
    assert result["state"] == "ready" and len(result["assignments"]) == 2
    assert len({m["identity_id"] for t in result["assignments"] for m in t["members"]}) == 8
    assert all(not t["guaranteed_clear"] and not t["damage_simulated"] for t in result["assignments"])
    assert result["missing_information"]  # Missing panels do not block conditional advice.
    assert not answer["team_candidates"]  # Generic examples cannot replace allocated teams.
    assert builds == before


def test_stage_lookup_needs_title_not_player_opening_enemy_details(book, catalog, builds):
    query = "这期怎么配队？"
    selected = plan(query, book, catalog)
    assert selected.kind == "challenge" and selected.requires_pixels
    assert selected.observation_plan(build_query_plan(query)).fields == {"visible_text"}
    value = {"fresh": True, "scene": {"verified": True}, "screen_facts": {"visible_text": [
        {"text": "来生泅渡 其十二"}, {"text": "剩余10轮以上"}, {"text": "剩余20轮以上"}]}}
    result = build_guidance(selected, query, book=book, catalog=catalog, profile=builds, visual=value, now=NOW)
    challenge = result["challenge_plan"]
    assert challenge["state"] == "ready"
    assert [h["enemies"][0]["name"] for h in challenge["stage"]["halves"]] == [
        "火花大会@Official", "第一位天才，隐德来希，赞达尔"]
    assert result["context"]["challenge_floor"] == 12
    assert result["context"]["challenge_difficulty"] == "normal"
    assert not challenge["stage"]["wave_data_complete"]


@pytest.mark.parametrize("labels,fresh", [("来生泅渡 其十二", True),
    ("来生泅渡 其十二 剩余15轮以上 剩余30轮以上", True),
    ("来生泅渡 其十二 剩余10轮以上 剩余20轮以上", False)])
def test_incomplete_star_or_old_screen_cannot_claim_normal_teams(book, catalog, builds, labels, fresh):
    result = challenge_answer("这期怎么配队", book, catalog, builds, visual={"fresh": fresh,
        "scene": {"verified": True}, "screen_facts": {"visible_text": [{"text": labels}]}})
    assert not (result.get("challenge_plan") or {}).get("assignments")


def test_exclusion_followup_keeps_scope_and_never_fills_unrecorded_role(book, catalog, builds):
    first = challenge_answer("本期混沌普通12层怎么配队", book, catalog, builds)
    query = "没有爻光呢？"
    selected = plan(query, book, catalog, previous=first["context"])
    assert selected.challenge_floor == 12 and selected.challenge_difficulty == "normal"
    result = build_guidance(selected, query, book=book, catalog=catalog, profile=builds, now=NOW)["challenge_plan"]
    assert result["state"] == "needs_input" and not result["assignments"]
    assert all(m["name"] != "爻光" for half in result["candidates"] for t in half for m in t["members"])
    assert "爻光" in result["question"] and result["question"].count("？") == 1
    assert result["provisional_halves"]


def test_lower_followup_only_asks_lower_gap_not_unrelated_upper_role(book, catalog, builds):
    builds["imported_builds"]["characters"] = [r for r in builds["imported_builds"]["characters"]
                                                if r["name"] not in {"爻光", "那刻夏"}]
    first = challenge_answer("本期混沌普通12层怎么配队", book, catalog, builds)
    result = challenge_answer("下半为什么这么配？", book, catalog, builds,
                               routing={"previous": first["context"]})["challenge_plan"]
    assert result["focus_side"] == "lower"
    assert "那刻夏" in result["question"] and "爻光" not in result["question"]


@pytest.mark.parametrize("missing", [None, -1, 5])
def test_required_eidolon_is_not_assumed_for_unselected_variant(book, catalog, builds, missing):
    # Limit lower-half choices to this reviewed E6 variant.
    data = deepcopy(book.data)
    data["challenge_stages"][0]["halves"][1]["team_refs"] = ["moc.evanescia_elation"]
    source = builds["imported_builds"]["characters"]
    for number in ("1505", "8009"):
        form = catalog.get("character_form." + number)
        source.append({"form_id": form["id"], "identity_id": form["identity_id"], "name": form["name"],
                       "eidolon": missing if number == "8009" else 0})
    result = challenge_answer("本期混沌普通12层怎么配队", GuidanceCatalog(data), catalog, builds)["challenge_plan"]
    assert not result["assignments"]
    assert any(g["reason"] == "reference_condition_not_met" for t in result["candidates"][1] for g in t["condition_gaps"])


def test_off_path_cone_warns_without_blocking_or_rewriting_saved_build(book, catalog, builds):
    cone = {"id": "light_cone.wrong", "name": "跨命途测试光锥", "superimposition": 1}
    builds["imported_builds"]["characters"][0]["light_cone"] = cone
    before = deepcopy(builds)
    result = challenge_answer("本期混沌普通12层怎么配队", book, catalog, builds)["challenge_plan"]
    assert result["state"] == "ready" and len(result["assignments"]) == 2
    assert len({m["identity_id"] for t in result["assignments"] for m in t["members"]}) == 8
    assert builds == before
    assert result["assignments"][0]["members"][0]["light_cone"] == cone
    warning = result["warnings"][0]
    assert warning["reason"] == "cone_path_mismatch" and warning["severity"] == "warning"
    assert warning["light_cone_ability_active"] is False and warning["side"] == "upper"
    assert "银狼LV.999" in warning["message"] and "光锥技能不生效" in warning["message"]
    assert all(not t["guaranteed_clear"] and not t["damage_simulated"] for t in result["assignments"])


def test_unknown_cone_is_not_invented_or_declared_off_path(book, catalog, builds):
    builds["imported_builds"]["characters"][0]["light_cone"] = None
    result = challenge_answer("本期混沌普通12层怎么配队", book, catalog, builds)["challenge_plan"]
    assert result["assignments"] and result["assignments"][0]["members"][0]["light_cone"] is None
    assert not result["warnings"]


@pytest.mark.parametrize("change", ["source", "orphan", "duplicate", "malformed"])
def test_optional_challenge_data_failure_is_isolated(book, catalog, builds, change):
    data = deepcopy(book.data)
    if change == "source":
        data["challenge_stages"][0]["source_refs"] = []
    elif change == "orphan":
        data["challenge_stages"][0]["halves"][0]["team_refs"] = ["missing"]
    elif change == "duplicate":
        data["challenge_teams"].append(deepcopy(data["challenge_teams"][0]))
    else:
        data["challenge_teams"] = None
    changed = GuidanceCatalog(data)
    result = challenge_answer("本期混沌普通12层怎么配队", changed, catalog, builds)
    assert result["challenge_plan"]["state"] == "coverage_unavailable"
    assert not result["challenge_plan"]["assignments"]
    old = challenge_answer("真珠遗器光锥怎么配", changed, catalog, builds)
    assert old["guides"][0]["name"] == "真珠" and "challenge_plan" not in old


@pytest.mark.parametrize("off_path", [False, True])
def test_formal_prepare_named_stage_uses_saved_builds_without_pixels(tmp_path, book, catalog, builds, off_path):
    from plugin.plugins.hsr_companion.tests.test_current_observation import plugin_fixture
    plugin, values, sent = plugin_fixture(tmp_path)
    plugin._guidance_book, plugin._catalog = book, catalog
    before = deepcopy(values["player_context"])
    if off_path:
        builds["imported_builds"]["characters"][0]["light_cone"] = {"id": "light_cone.wrong", "superimposition": 1}

    async def forbidden(*args, **kwargs):
        raise AssertionError("explicit stage advice must not require OCR/network")

    async def roster():
        return deepcopy(builds["imported_builds"])

    plugin._imported_roster = roster
    plugin._current_visual_evidence = plugin._read_game_state = plugin._wait_for_data_pack = forbidden

    async def run():
        result = await plugin.hsr_companion_prepare(query="本期混沌普通12层怎么配队")
        challenge = result["guidance_answer"]["challenge_plan"]
        assert challenge["assignments"] and challenge["state"] == "ready"
        assert bool(challenge["warnings"]) is off_path
        assert result["query_timing"]["refresh_wait_seconds"] == 0
        assert not result["followup"]["pending"] and "context" not in result["guidance_answer"]
        pearl = next(m for t in challenge["assignments"] for m in t["members"] if m["name"] == "真珠")
        assert pearl["panel"]["values"]["def"] == "3132" and pearl["light_cone"]["superimposition"] == 1
        assert values["player_context"] == before and not sent

    asyncio.run(run())


def test_delayed_stage_advice_transfers_actual_two_teams_not_generic_examples(book, catalog, builds):
    from plugin.plugins.hsr_companion.runtime.answer_message import render_followup
    from utils.result_parser import parse_push_message_content
    query = "本期混沌普通12层怎么配队"
    result = challenge_answer(query, book, catalog, builds)
    text, receipt = render_followup({"guidance_answer": result}, plan=build_query_plan(query), ready=True)
    received = parse_push_message_content(text, lang="zh")
    assert receipt["complete"] and received == text
    assert "来生泅渡" in received and "上半：" in received and "下半：" in received
    for team in result["challenge_plan"]["assignments"]:
        assert all(m["name"] in received for m in team["members"])


def test_delayed_team_advice_must_deliver_off_path_warning_with_roster(book, catalog, builds):
    from plugin.plugins.hsr_companion.runtime.answer_message import render_followup
    from utils.result_parser import parse_push_message_content
    query = "本期混沌普通12层怎么配队"
    builds["imported_builds"]["characters"][0]["light_cone"] = {
        "id": "light_cone.wrong", "name": "跨命途测试光锥", "superimposition": 1}
    result = challenge_answer(query, book, catalog, builds)
    text, receipt = render_followup({"guidance_answer": result}, plan=build_query_plan(query), ready=True)
    received = parse_push_message_content(text, lang="zh")
    assert receipt["complete"] and received == text
    assert "上半：" in received and "下半：" in received
    assert "跨命途测试光锥" in received and "光锥技能不生效" in received
    assert "不保证通关" in received


def test_channel_limit_cannot_silently_drop_off_path_warning(book, catalog, builds, monkeypatch):
    from plugin.plugins.hsr_companion.runtime import answer_message
    query = "本期混沌普通12层怎么配队"
    builds["imported_builds"]["characters"][0]["light_cone"] = {"id": "light_cone.wrong", "superimposition": 1}
    result = challenge_answer(query, book, catalog, builds)
    # Force the optional-reason fallback; the warning must stay essential.
    monkeypatch.setattr(answer_message, "bridge_preview", lambda text: text if "理由：" not in text else text[:20])
    text, receipt = answer_message.render_followup({"guidance_answer": result}, plan=build_query_plan(query), ready=True)
    assert receipt["complete"] and "理由：" not in text
    assert "光锥技能不生效" in text and "上半：" in text and "下半：" in text
