"""Read-only replay of a recorded real panel through candidate prepare.

Validation is at the saved observation time, NOT proof it is current now.
No new recognition, host messages, installation or game input. The query
coordinator is explicitly substituted: this tests evaluation/delivery only.
"""
import asyncio
import copy
import importlib
import json
import sqlite3
import tempfile
from datetime import datetime
from pathlib import Path


async def run():
    import ormsgpack
    from plugin.plugins.hsr_companion.data import ExternalKnowledgeCatalog
    from plugin.plugins.hsr_companion.runtime.build_info import BUILD_REVISION
    from plugin.plugins.hsr_companion.runtime.observation import public_visual_evidence
    from plugin.plugins.hsr_companion.runtime.observation_store import ObservationSnapshot
    from plugin.plugins.hsr_companion.runtime.query_coordinator import QueryObservation
    from plugin.plugins.hsr_companion.tests.test_current_observation import plugin_fixture
    root = Path(__file__).resolve().parents[1]
    registry = json.loads((root / "resources/character_registry.json").read_text(encoding="utf-8"))
    catalog = ExternalKnowledgeCatalog(registry=registry)
    # Catalog selects the app's already configured external pack directory.
    path = catalog.root / "player-state" / "store.db"
    with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as connection:
        row = connection.execute("SELECT value FROM kv_store WHERE key=?", ("last_local_vision",)).fetchone()
    if not row:
        raise RuntimeError("No recorded observation")
    raw = ormsgpack.unpackb(row[0])
    if not any(f.get("scope") == "attributes" for f in raw.get("screen_facts", {}).get("facts", [])):
        raise RuntimeError("Recorded observation contains no attributes; do not synthesize them")
    at = datetime.fromisoformat(raw["observed_at"])
    current = public_visual_evidence(raw, {"enabled": True, "state": "observing"}, now=at)
    with tempfile.TemporaryDirectory(prefix="hsr_panel_replay_") as directory:
        owner, _, sent = plugin_fixture(Path(directory))
        owner._catalog = catalog
        owner._observation_serial = current["screen_facts"].get("observation_serial", 0)
        module = importlib.import_module(type(owner).__module__)
        original = module.resolve_query

        async def replay(plugin, plan, **kwargs):
            return QueryObservation(ObservationSnapshot.select(plugin, copy.deepcopy(current), fields=plan.fields),
                {"outcome": "recorded_snapshot_replay", "stages": []}, 0, kwargs["generation"])

        module.resolve_query = replay
        try:
            result = await owner.hsr_companion_prepare(query="这个角色的面板怎么样？")
        finally:
            module.resolve_query = original
        assessment = result["guidance_answer"]["assessment"]
        evaluation = assessment.get("evaluation", {})
        if not evaluation.get("available"):
            raise RuntimeError("Recorded panel could not form a sourced evaluation")
        observed = {r["label"]: r["value"] for r in current["screen_facts"]["facts"] if r.get("scope") == "attributes" and r.get("kind") == "stat"}
        assert {r["label"]: r["value"] for r in evaluation["rows"]} == observed
        assert not sent
        print(json.dumps({"build": BUILD_REVISION, "scope": "recorded_real_panel_replay_not_current_capture_or_host_speech",
            "observed_at": raw["observed_at"], "subject_ids": assessment["subject_ids"],
            "subject_origin": assessment["subject_origin"], "rows": evaluation["rows"],
            "focus_checks": evaluation["focus_checks"], "next_steps": evaluation["next_steps"],
            "reply_plan": result["guidance_answer"]["reply_plan"],
            "exact_value_match": True, "quality_score_available": evaluation["quality_score_available"]}, ensure_ascii=True))


if __name__ == "__main__":
    asyncio.run(run())
