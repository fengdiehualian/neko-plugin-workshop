"""Real pinned knowledge through formal prepare; no game/model/installation.

Only knowledge requests are executed. Live frames, helper, host messages and
the user's persistent player store are not used or changed.
"""
import argparse
import asyncio
import importlib
import json
import tempfile
from pathlib import Path


async def verify():
    from plugin.plugins.hsr_companion.data import ExternalKnowledgeCatalog
    from plugin.plugins.hsr_companion.runtime.build_info import BUILD_REVISION
    from plugin.plugins.hsr_companion.runtime.observation import public_visual_evidence
    from plugin.plugins.hsr_companion.runtime.observation_store import ObservationSnapshot
    from plugin.plugins.hsr_companion.runtime.query_coordinator import QueryObservation
    from plugin.plugins.hsr_companion.tests.test_current_observation import plugin_fixture
    root = Path(__file__).resolve().parents[1]
    registry = json.loads((root / "resources/character_registry.json").read_text(encoding="utf-8"))
    catalog = ExternalKnowledgeCatalog(registry=registry)
    if not catalog.status().get("installed"):
        raise RuntimeError("Real pinned external knowledge pack is required")
    with tempfile.TemporaryDirectory(prefix="hsr_d1_fit_verify_", dir=root / "dist") as directory:
        if not Path(directory).resolve().is_relative_to((root / "dist").resolve()):
            raise ValueError("Temporary target escaped plugin workspace")
        owner, values, sent = plugin_fixture(Path(directory))
        owner._catalog = catalog

        async def resolve(plugin, plan, **kwargs):
            assert not plan.screen
            value = public_visual_evidence({"invalidated": True}, owner._observer_status)
            return QueryObservation(ObservationSnapshot.select(plugin, value, fields=plan.fields),
                                    {"outcome": "not_requested"}, 0, kwargs["generation"])

        module = importlib.import_module(type(owner).__module__)
        original = module.resolve_query
        module.resolve_query = resolve
        checks = []
        try:
            first = await owner.hsr_companion_prepare(query="真珠防御力要多少合适？")
            reply = first["guidance_answer"]["reply_plan"]
            rule = reply["stat_advice"]
            assert rule["available"] and rule["activation_value"] == 2400
            assert rule["cap_value"] == 6000 and rule["max_self_elation_percent"] == 140
            assert rule["trace_id"] == "character_trace.1503101" and rule["sources"]
            assert rule["revision"] == "dbe8cdfcfb0bf657b9fe1cf92d5537f118ccb487"
            assert rule["recommended_target"] is None and not rule["graduation_threshold_available"]
            assert "上限" in reply["conclusion"] and "位面球" in reply["next_step"]
            assert not first["query_plan"]["screen"] and not first["followup"]["pending"]
            checks.append({"kind": "real_pinned_trace_formal_advice", "passed": True,
                           "conclusion": reply["conclusion"], "next_step": reply["next_step"],
                           "trace_id": rule["trace_id"], "revision": rule["revision"],
                           "activation": rule["activation_value"], "cap": rule["cap_value"],
                           "max_self_elation_percent": rule["max_self_elation_percent"]})
            number = await owner.hsr_companion_prepare(query="3132")
            reply = number["guidance_answer"]["reply_plan"]
            assert reply["stated_stat"]["origin"] == "player_statement" and not reply["evidence_rows"]
            assert reply["stat_advice"]["comparison"]["assumes_trace_active_and_same_basis"]
            assert reply["stat_advice"]["comparison"]["relation"] == "above_activation_below_cap"
            assert not number["query_plan"]["screen"] and not number["followup"]["pending"]
            checks.append({"kind": "formal_player_number_continuation", "passed": True,
                           "conclusion": reply["conclusion"], "next_step": reply["next_step"],
                           "not_a_visual_reading": True, "not_a_graduation_grade": True})
            assert not sent and values["player_context"] == {"owned_characters": ["花火"]}
        finally:
            module.resolve_query = original
    return {"build_revision": BUILD_REVISION, "passed": len(checks), "checks": checks,
            "scope": "Actual pinned external data and formal tool; not live OCR or cat speech.",
            "installed": False, "host_messages_sent": False, "player_profile_changed": False}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    destination = args.out.resolve()
    if destination.parent != (root / "dist").resolve():
        parser.error("out must be a new report directly inside plugin dist")
    result = asyncio.run(verify())
    with destination.open("x", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
    print(json.dumps({"passed": result["passed"], "report": str(destination), "installed": False}))
