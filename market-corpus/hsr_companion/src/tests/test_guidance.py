"""Frozen service contracts: builds/challenges must not replace observation."""
from __future__ import annotations

import asyncio
import copy
import json
import time
from datetime import datetime
from pathlib import Path

import pytest
from plugin.plugins.hsr_companion.data import ExternalKnowledgeCatalog
from plugin.plugins.hsr_companion.runtime.guidance import GuidanceCatalog, build_guidance, plan_guidance
from plugin.plugins.hsr_companion.runtime.query_plan import build_query_plan
from plugin.plugins.hsr_companion.tests.test_current_observation import plugin_fixture

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime.fromisoformat("2026-10-15T22:00:00+08:00")


@pytest.fixture
def book():
    return GuidanceCatalog(json.loads((ROOT / "resources/guidance.cn-4.6.json").read_text(encoding="utf-8")))


@pytest.fixture
def catalog(tmp_path):
    registry = json.loads((ROOT / "resources/character_registry.json").read_text(encoding="utf-8"))
    return ExternalKnowledgeCatalog(registry=registry, root=tmp_path)


def plan(query, book, catalog, **kwargs):
    return plan_guidance(query, build_query_plan(query, "conversation"), book=book, catalog=catalog, now=NOW, **kwargs)


def answer(query, book, catalog, **kwargs):
    selected = plan(query, book, catalog, **kwargs)
    return build_guidance(selected, query, book=book, catalog=catalog, profile={}, now=NOW)


@pytest.mark.parametrize("query,name", [("真珠遗器光锥怎么配？", "真珠"), ("绯英怎么配装？", "绯英"),
                                          ("本期新角色遗器怎么配", "真珠"), ("本期复刻角色光锥推荐", "绯英")])
def test_named_and_current_banner_builds_without_screen(book, catalog, query, name):
    result = answer(query, book, catalog)
    assert [g["name"] for g in result["guides"]] == [name]
    assert result["guides"][0]["main_stats"]
    assert result["guides"][0]["sources"]
    assert plan(query, book, catalog).requires_pixels is False
    assert result["player_conditions"]["owned_characters"] == []
    assert not any(t["account_ready"] for t in result["team_candidates"])


@pytest.mark.parametrize("query", ["现在在哪，任务是什么", "现在是什么页面", "这个光锥效果是什么", "这件遗器的主词条是什么",
                                    "右边是谁", "这个技能说了什么", "我有多少星琼", "绯英是什么命途", "今天好开心"])
def test_original_non_advice_requests_not_hijacked(book, catalog, query):
    assert not plan(query, book, catalog).requested


def test_missing_signature_and_teammate_followup_not_new_build_target(book, catalog):
    first = answer("绯英怎么配装", book, catalog)
    second = answer("没专武呢？", book, catalog, previous=first["context"])
    assert second["guides"][0]["name"] == "绯英"
    assert all(c["role"] != "signature_reference" for c in second["guides"][0]["light_cone_candidates"])
    third = answer("没有爻光呢？", book, catalog, previous=second["context"])
    assert [g["name"] for g in third["guides"]] == ["绯英"]
    assert third["player_conditions"]["excluded_names"] == ["爻光"]
    assert third["player_conditions"]["signature_excluded"]
    assert all("爻光" not in t["members"] for t in third["team_candidates"])


def test_excluded_names_in_first_question_not_targets(book, catalog):
    result = answer("绯英没有爻光怎么配队", book, catalog)
    assert [g["name"] for g in result["guides"]] == ["绯英"]
    assert result["player_conditions"]["excluded_names"] == ["爻光"]


def test_natural_cultivation_question_and_short_negative_followup(book, catalog):
    first = answer("真珠怎么养？", book, catalog)
    assert first["guides"][0]["name"] == "真珠"
    assert not plan("真珠怎么养？", book, catalog).requires_pixels
    second = answer("没爻光能用吗？", book, catalog, previous=first["context"])
    assert second["guides"][0]["name"] == "真珠"
    assert second["player_conditions"]["excluded_names"] == ["爻光"]
    assert all("爻光" not in t["members"] for t in second["team_candidates"])


def test_already_built_roster_followup_retains_subject(book, catalog):
    first = answer("绯英配队推荐", book, catalog)
    result = answer("我已经练了绯英、爻光、真珠", book, catalog, previous=first["context"])
    assert result["guides"][0]["name"] == "绯英"
    assert set(result["player_conditions"]["owned_characters"]) == {"绯英", "爻光", "真珠"}


def test_owned_statement_is_bounded_condition_not_profile_write(book, catalog):
    first = answer("绯英配队推荐", book, catalog)
    result = answer("我有绯英、爻光、真珠，推荐怎么配", book, catalog, previous=first["context"])
    assert result["guides"][0]["name"] == "绯英"
    assert set(result["player_conditions"]["owned_characters"]) == {"绯英", "爻光", "真珠"}
    assert result["player_conditions"]["roster_complete"] is False
    assert result["team_candidates"][0]["account_ready"] is False  # protagonist form unknown


def test_new_subject_resets_old_constraints_and_expired_context_is_not_used(book, catalog):
    first = answer("绯英没专武怎么配装", book, catalog)
    assert not answer("真珠光锥推荐", book, catalog, previous=first["context"])["player_conditions"]["signature_excluded"]
    stale = {**first["context"], "at": time.monotonic() - 601}
    expired = plan("没有爻光呢", book, catalog, previous=stale)
    # Stage one: an expired topic needs clarification, not silent route loss.
    assert expired.requested and not expired.character_ids


@pytest.mark.parametrize("query", ["这件遗器适合绯英吗，推荐怎么配", "现在在哪，真珠怎么配装", "这期试炼怎么打"])
def test_visual_dependencies_remain_when_needed(book, catalog, query):
    assert plan(query, book, catalog).observation_plan(build_query_plan(query, "conversation")).screen


def test_named_general_advice_does_not_owe_model_screen_hints(book, catalog):
    query = "绯英配装推荐"
    base = build_query_plan(query, "conversation", screen_fields=["equipment"])
    chosen = plan_guidance(query, base, catalog=catalog, book=book, explicit_fields=True)
    assert not chosen.observation_plan(base).screen
    # The player's actual current-screen request still wins over this route.
    query = "看屏幕帮绯英推荐配装"
    base = build_query_plan(query, "conversation", screen_fields=["equipment"])
    chosen = plan_guidance(query, base, catalog=catalog, book=book, explicit_fields=True)
    assert chosen.observation_plan(base) == base


@pytest.mark.parametrize("mode,title", [("混沌回忆", "来生泅渡"), ("末日幻影", "支配遗忘"),
                                       ("虚构叙事", "立界开篇"), ("异相仲裁", "落叶归根")])
def test_calendar_selects_current_per_mode_not_version_only(book, catalog, mode, title):
    result = answer(f"本期{mode}怎么打怎么配队", book, catalog)
    assert [g["name"] for g in result["guides"]] == [title]
    assert result["guides"][0]["period_status"] == "active"
    assert result["guides"][0]["strategy"]


@pytest.mark.parametrize("date,mode,title", [("2026-10-19T03:59:59+08:00", "虚构叙事", "立界开篇"),
    ("2026-10-19T04:00:00+08:00", "虚构叙事", "取象为文"),
    ("2026-11-02T04:00:00+08:00", "混沌回忆", "物竞天择")])
def test_exact_period_boundaries(book, catalog, date, mode, title):
    chosen = plan(f"本期{mode}怎么打", book, catalog)
    result = build_guidance(chosen, "", book=book, catalog=catalog, profile={}, now=datetime.fromisoformat(date))
    assert [g["name"] for g in result["guides"]] == [title]


def test_named_future_period_marked_and_unknown_trial_clarifies(book, catalog):
    assert answer("取象为文怎么配队", book, catalog)["guides"][0]["period_status"] == "upcoming"
    result = answer("这期试炼怎么配队", book, catalog)
    assert not result["guides"]
    assert result["state"] == "needs_input"


def test_all_endgame_overview_has_four_current_periods_not_arbitrary_trial(book, catalog):
    result = answer("本期各个高难副本怎么打怎么配队", book, catalog)
    assert {g["mode"] for g in result["guides"]} == {"moc", "pf", "as", "aa"}
    assert all(g["period_status"] == "active" for g in result["guides"])
    assert not plan("本期各个高难副本怎么配队", book, catalog).requires_pixels


@pytest.mark.parametrize("node,title,member", [("骑士一", "帕姆王", "银狼LV.999"),
    ("骑士二", "死龙残躯", "遐蝶"), ("骑士三", "深魇蝗灾", "海瑟音"), ("王棋", "亚婆离", "绯英")])
def test_node_specific_strategy_not_same_generic_elation_team(book, catalog, node, title, member):
    result = answer(f"本期异相仲裁{node}怎么配队怎么打", book, catalog)
    nodes = result["guides"][0]["nodes"]
    assert len(nodes) == 1 and title in nodes[0]["name"]
    assert nodes[0]["strategy"] and nodes[0]["sources"]
    assert any(member in t["members"] for t in result["team_candidates"])


def test_new_node_replaces_previous_node(book, catalog):
    first = answer("异相仲裁骑士一怎么打", book, catalog)
    result = answer("骑士二怎么打", book, catalog, previous=first["context"])
    assert [n["id"] for n in result["guides"][0]["nodes"]] == ["aa.knight2"]


def test_new_node_from_another_mode_does_not_inherit_old_period(book, catalog):
    first = answer("虚构叙事怎么打", book, catalog)
    result = answer("骑士一怎么打", book, catalog, previous=first["context"])
    assert [g["mode"] for g in result["guides"]] == ["aa"]
    assert [n["id"] for n in result["guides"][0]["nodes"]] == ["aa.knight1"]


def test_all_modes_followup_retains_scope(book, catalog):
    first = answer("本期各个高难怎么配队", book, catalog)
    result = answer("我有绯英、爻光、真珠怎么配队", book, catalog, previous=first["context"])
    assert {g["mode"] for g in result["guides"]} == {"moc", "pf", "as", "aa"}


def test_team_reference_does_not_invent_excluded_replacement(book, catalog):
    result = answer("绯英没有爻光怎么配队", book, catalog)
    assert all(not t["account_ready"] for t in result["team_candidates"])
    assert all("爻光" not in t["members"] for t in result["team_candidates"])
    assert result["team_candidates"][0]["unconfirmed_slots"]


@pytest.mark.parametrize("mutation", ["source", "duplicate", "overlap", "id", "slots", "mode"])
def test_invalid_strategy_schema_rejected(book, mutation):
    data = copy.deepcopy(book.data)
    if mutation == "source":
        data["characters"][0]["source_refs"] = ["missing"]
    elif mutation == "duplicate":
        data["teams"].append(copy.deepcopy(data["teams"][0]))
    elif mutation == "overlap":
        data["periods"][-1]["starts_at"] = data["periods"][0]["starts_at"]
    elif mutation == "id":
        data["characters"][0]["alternative_cones"][0]["id"] = "bad"
    elif mutation == "slots":
        data["teams"][0]["slots"].pop()
    else:
        data["periods"][0]["mode"] = "another_game"
    with pytest.raises(ValueError):
        GuidanceCatalog(data)


def test_missing_pack_does_not_fabricate_equipment(book, catalog):
    result = answer("绯英遗器光锥推荐", book, catalog)
    assert result["state"] == "partial"
    assert all(not e["available"] and "name" not in e for e in result["guides"][0]["equipment_candidates"])


def test_wrong_path_cones_rejected_and_input_catalog_not_mutated(book, catalog, monkeypatch):
    real_get = catalog.get

    def get(entity_id):
        if entity_id.startswith("light_cone."):
            return {"id": entity_id, "name": "测试用不适配光锥", "entity_type": "light_cone", "path": "Hunt",
                    "sources": [{"source_id": "synthetic-test"}]}
        return real_get(entity_id)

    monkeypatch.setattr(catalog, "get", get)
    result = answer("绯英光锥推荐", book, catalog)
    assert result["guides"][0]["light_cone_candidates"] == []
    assert any("命途不一致" in m for m in result["missing_information"])


def test_prepare_guidance_paused_never_reads_observation_or_rewrites_profile(tmp_path):
    plugin, values, sent = plugin_fixture(tmp_path)
    plugin._observer_status.update(enabled=False, state="paused")
    before = copy.deepcopy(values["player_context"])

    async def forbidden(*args, **kwargs):
        raise AssertionError("named strategy must not read pixels/game state")

    plugin._current_visual_evidence = forbidden
    plugin._read_game_state = forbidden
    plugin._wait_for_data_pack = forbidden

    async def run():
        result = await plugin.hsr_companion_prepare(query="绯英遗器光锥怎么配")
        assert not result.get("is_error")
        assert result["guidance_answer"]["guides"][0]["name"] == "绯英"
        assert result["query_answer"]["state"] == "not_requested"
        assert result["query_timing"]["refresh_wait_seconds"] == 0
        assert not result["followup"]["pending"]
        assert "context" not in result["guidance_answer"]
        next_result = await plugin.hsr_companion_prepare(query="没专武呢")
        assert next_result["guidance_answer"]["player_conditions"]["signature_excluded"]
        assert values["player_context"] == before
        assert not sent

    asyncio.run(run())


def test_normal_query_clears_service_context(tmp_path):
    plugin, _, _ = plugin_fixture(tmp_path)

    async def run():
        await plugin.hsr_companion_prepare(query="绯英怎么配装")
        assert plugin._guidance_context
        result = await plugin.hsr_companion_prepare(query="今天好开心")
        assert result["guidance_answer"] == {"requested": False}
        assert plugin._guidance_context is None

    asyncio.run(run())


def test_visual_result_followup_includes_strategy_for_original_demand(book, catalog, monkeypatch):
    from plugin.plugins.hsr_companion.runtime import followup
    from plugin.plugins.hsr_companion.tests.test_page_followup import followup_owner, visual

    query = "这期试炼怎么配队"
    value = visual("endgame_menu")
    value["screen_facts"]["visible_text"] = [{"text": "异相仲裁 落叶归根"}]
    owner, sent = followup_owner(monkeypatch, value=value)
    owner._guidance_book, owner._catalog = book, catalog
    strategy = plan(query, book, catalog)
    observation = strategy.observation_plan(build_query_plan(query, "conversation"))

    async def run():
        armed = followup.arm_followup(owner, query=query, visual=visual("unknown", ready=False), asked_at=time.time(),
                                     scene_only=False, navigation=False, content=True, controlled=False,
                                     plan=observation, guidance_plan=strategy, guidance_profile={})
        assert armed["pending"]
        await owner._followup_task

    asyncio.run(run())
    assert len(sent) == 1
    payload = owner._test_answer_payloads[-1]
    assert payload["guidance_answer"]["guides"] and payload["guidance_answer"]["team_candidates"]
    from utils.result_parser import parse_push_message_content
    received = parse_push_message_content(sent[0]["parts"][0]["text"], lang="zh")
    assert "落叶归根" in received and "参考" in received and "不证明账号持有" in received
    for name in payload["guidance_answer"]["team_candidates"][0]["members"]:
        if name:
            assert name in received
    assert owner._guidance_context["mode"] == "aa"
