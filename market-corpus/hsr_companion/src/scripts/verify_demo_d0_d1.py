"""Real external catalog + structured test snapshots through real prepare.

No OCR, host requests, game input, installation or messages. This is data and
delivery integration evidence, NOT live accuracy or cat paraphrase acceptance.
"""
from __future__ import annotations

import asyncio
import importlib
import json
import tempfile
from pathlib import Path


async def run():
    from plugin.plugins.hsr_companion.data import ExternalKnowledgeCatalog
    from plugin.plugins.hsr_companion.runtime import service_contract
    from plugin.plugins.hsr_companion.runtime.equipment import equipment_card
    from plugin.plugins.hsr_companion.runtime.observation_store import ObservationSnapshot
    from plugin.plugins.hsr_companion.runtime.observation import public_visual_evidence
    from plugin.plugins.hsr_companion.runtime.query_coordinator import QueryObservation
    from plugin.plugins.hsr_companion.tests.test_current_observation import plugin_fixture
    from plugin.plugins.hsr_companion.tests.test_stage1_repairs import fact, visual
    from plugin.plugins.hsr_companion.tests.test_stage2_assessment import relic
    root = Path(__file__).resolve().parents[1]
    registry = json.loads((root / "resources/character_registry.json").read_text(encoding="utf-8"))
    catalog = ExternalKnowledgeCatalog(registry=registry)
    if not catalog.status().get("installed"):
        raise RuntimeError("Real external data pack required")
    with tempfile.TemporaryDirectory(prefix="hsr_demo_verify_") as directory:
        owner, values, sent = plugin_fixture(Path(directory))
        owner._catalog = catalog
        current = relic(subs=[("攻击力", "19"), ("防御力", "35"), ("速度", "6"), ("暴击率", "5.8%")])
        current["screen_facts"]["observation_serial"] = 100

        async def resolve(plugin, plan, **kwargs):
            chosen = current if plan.screen else public_visual_evidence({"invalidated": True}, owner._observer_status)
            return QueryObservation(ObservationSnapshot.select(plugin, chosen, fields=plan.fields),
                                    {"outcome": "reused", "stages": []}, 0, kwargs["generation"])

        module = importlib.import_module(type(owner).__module__)
        original = module.resolve_query
        module.resolve_query = resolve
        results = []
        try:
            service_contract.configure_delivery_trace(owner, seconds=120)
            for role, identity, cone_id in (("真珠", "character.pearl", "light_cone.23055"),
                                            ("绯英", "character.evanescia", "light_cone.23058"),
                                            ("黄泉", "character.acheron", "light_cone.23024")):
                current = relic(subs=[("攻击力", "19"), ("防御力", "35"), ("速度", "6"), ("暴击率", "5.8%")])
                owner._observation_serial = current["screen_facts"]["observation_serial"]
                result = await owner.hsr_companion_prepare(query=f"这件遗器适合{role}吗？主副词条是什么？")
                card = result["equipment_answer"]
                evaluation = result["guidance_answer"]["assessment"]["evaluation"]
                assert card == equipment_card(current)
                assert evaluation["content_digest"] == card["content_digest"]
                assert evaluation["available"] and evaluation["next_steps"]
                assert evaluation["sub_stats"][0]["value"] == "19"
                assert result["delivery_receipt"]["equipment_consistency"] == "matches"
                reply = result["guidance_answer"]["reply_plan"]
                assert reply["mode"] == "full" and len(reply["evidence_rows"]) == 5
                assert reply["evidence_rows"][1]["value"] == "19"
                assert reply["max_sentences"] is None
                guide = result["guidance_answer"]["guides"][0]
                assert guide["identity_id"] == identity
                assert guide["mechanics"]["forms"][0]["skills"]
                assert all(c["available"] for c in guide["equipment_candidates"])
                assert all(c["available"] and c["path"] == ("Warlock" if role == "黄泉" else "Elation")
                           for c in guide["light_cone_candidates"])
                results.append({"role": role, "check": "relic_and_real_data", "passed": True})

                current = visual(*[fact("stat", key, value) for key, value in (
                    ("生命值", "3789"), ("攻击力", "3123"), ("防御力", "3567"), ("速度", "134"),
                    ("暴击率", "54.2%"), ("暴击伤害", "181.4%"), ("能量恢复效率", "119.4%"))])
                current = {**relic(), **current}
                for row in current["screen_facts"]["facts"]:
                    row["scope"] = "attributes"
                current["scene"]["label"] = "角色属性"
                current["observed_at"] = "2026-10-18T03:40:00+00:00"
                current["screen_facts"]["observation_serial"] = 101
                owner._observation_serial = 101
                result = await owner.hsr_companion_prepare(query=f"{role}面板怎么样？")
                evaluation = result["guidance_answer"]["assessment"]["evaluation"]
                assert evaluation["available"] and evaluation["focus_checks"] and evaluation["next_steps"]
                assert {r["label"]: r["value"] for r in evaluation["rows"]} == {
                    r["label"]: r["value"] for r in current["screen_facts"]["facts"]}
                assert not evaluation["quality_score_available"]
                reply = result["guidance_answer"]["reply_plan"]
                assert reply["mode"] == "brief" and len(reply["evidence_rows"]) <= 2
                assert reply["next_step"] and reply["max_questions"] <= 1
                results.append({"role": role, "check": "panel_exact_values_and_explanations", "passed": True})

                for query, goal in (("我应该先关注什么好？", "priority"), ("光锥推荐哪个？", "equipment_guide")):
                    follow = await owner.hsr_companion_prepare(query=query)
                    reply = follow["guidance_answer"]["reply_plan"]
                    assert not follow["query_plan"]["screen"] and not follow["followup"]["pending"]
                    assert reply["goal"] == goal and not reply["evidence_rows"]
                    assert follow["guidance_answer"]["guides"][0]["identity_id"] == identity
                    assert reply["turn_contract"]["after_answer"] == "yield_to_player"
                    assert reply["next_step"] if goal == "priority" else reply["discussion_options"]
                    results.append({"role": role, "check": goal + "_no_pixels_no_old_values", "passed": True})

                record = catalog.get(cone_id)
                current = visual({"kind": "entity_label", "scope": "equipment", "label": "光锥",
                                  "value": record["name"], "entity_type": "light_cone", "entity_id": cone_id})
                current = {**relic(), **current}
                current["scene"]["label"] = "光锥详情"
                current["observed_at"] = "2026-10-18T03:40:01+00:00"
                current["screen_facts"]["observation_serial"] = 102
                owner._observation_serial = 102
                result = await owner.hsr_companion_prepare(query=f"这个光锥适合{role}吗？")
                evaluation = result["guidance_answer"]["assessment"]["evaluation"]
                assert evaluation["available"] and evaluation["entity_id"] == cone_id
                assert evaluation["verdict"] == "signature_reference"
                assert result["guidance_answer"]["reply_plan"]["primary_direction"] == "光锥适用条件"
                results.append({"role": role, "check": "cone_conditions", "passed": True})
            assert values["player_context"] == {"owned_characters": ["花火"]}
            assert not sent
        finally:
            module.resolve_query = original
        print(json.dumps({"scope": "real_catalog_structured_snapshots_formal_tool_not_live_ocr_or_host_speech",
                          "results": results}, ensure_ascii=True))


if __name__ == "__main__":
    asyncio.run(run())
