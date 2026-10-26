"""Real external knowledge + synthetic pixels through the tool entry.

No game/host requests, install, OCR, model calls, profile writes or speech.
This verifies data compatibility and tool contracts, not live OCR accuracy.
"""
from __future__ import annotations

import asyncio
import importlib
import json
import tempfile
from pathlib import Path


async def run():
    from plugin.plugins.hsr_companion.data import ExternalKnowledgeCatalog
    from plugin.plugins.hsr_companion.runtime.equipment import equipment_card
    from plugin.plugins.hsr_companion.runtime.observation_store import ObservationSnapshot
    from plugin.plugins.hsr_companion.runtime.query_coordinator import QueryObservation
    from plugin.plugins.hsr_companion.tests.test_current_observation import plugin_fixture
    from plugin.plugins.hsr_companion.tests.test_stage2_assessment import relic

    root = Path(__file__).resolve().parents[1]
    registry = json.loads((root / "resources/character_registry.json").read_text(encoding="utf-8"))
    catalog = ExternalKnowledgeCatalog(registry=registry)
    if not catalog.status().get("installed"):
        raise RuntimeError("External pack required; no successful real-pack claim")
    with tempfile.TemporaryDirectory(prefix="hsr_stage2_") as directory:
        owner, values, sent = plugin_fixture(Path(directory))
        owner._catalog = catalog
        # Published, structured synthetic observations stand in for pixels.
        # Keep the entry, demand handoff, sources and evaluation code genuine.
        current = relic()
        owner._observation_serial = 96
        async def resolve(plugin, plan, **kwargs):
            return QueryObservation(ObservationSnapshot.select(plugin, current, fields=plan.fields),
                                    {"outcome": "reused", "stages": []}, 0, kwargs["generation"])
        module = importlib.import_module(type(owner).__module__)
        original = module.resolve_query
        module.resolve_query = resolve
        try:
            for query in ("这个遗器词条怎么样", "绯英", "这个呢"):
                if query == "这个呢":
                    current = relic(main="防御力", value="54.0%", serial=97,
                                    subs=[("暴击率", "8.7%"), ("暴击伤害", "11.6%"), ("速度", "2"), ("生命值", "80")])
                    owner._observation_serial = 97
                result = await owner.hsr_companion_prepare(query=query)
                assert not result.get("is_error"), result
                card = result["equipment_answer"]
                assert card == equipment_card(current)
                assessment = result["guidance_answer"]["assessment"]
                assert assessment["equipment_answer"] == card
                if query != "这个遗器词条怎么样":
                    assert assessment["evaluation_available"]
                    guide = result["guidance_answer"]["guides"][0]
                    assert guide["mechanics"]["forms"], "Actual character mechanism data missing"
                    assert guide["mechanics"]["forms"][0]["skills"]
                    assert guide["sources"]
                    assert any(c["available"] for c in guide["light_cone_candidates"])
                print(json.dumps({"query": query, "demand": result["reply_demand"]["guidance"],
                    "main": card["main_stat"], "sub_count": len(card["sub_stats"]),
                    "evaluation_available": assessment["evaluation_available"],
                    "main_verdict": (assessment.get("evaluation", {}).get("main_stat") or {}).get("verdict"),
                    "entry": owner._service_request["state"]}, ensure_ascii=False))
            assert values["player_context"] == {"owned_characters": ["花火"]}
            assert not sent
        finally:
            module.resolve_query = original


if __name__ == "__main__":
    asyncio.run(run())
