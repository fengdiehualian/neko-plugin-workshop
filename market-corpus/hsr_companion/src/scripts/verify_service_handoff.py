"""Read real external knowledge with isolated state; no host or game requests."""
from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path

from plugin.plugins.hsr_companion.data import ExternalKnowledgeCatalog
from plugin.plugins.hsr_companion.tests.test_current_observation import plugin_fixture


async def verify():
    root = Path(__file__).resolve().parents[1]
    registry = json.loads((root / "resources/character_registry.json").read_text(encoding="utf-8"))
    catalog = ExternalKnowledgeCatalog(registry=registry)
    if not catalog.status().get("installed"):
        raise RuntimeError("External knowledge pack not installed; not a successful live-pack check")
    with tempfile.TemporaryDirectory(prefix="hsr_stage1_") as directory:
        owner, values, sent = plugin_fixture(Path(directory))
        owner._catalog = catalog
        owner._observer_status.update(enabled=False, state="paused")

        async def forbidden(*args, **kwargs):
            raise AssertionError("Knowledge discussion must not access pixels or game state")

        owner._current_visual_evidence = forbidden
        owner._read_game_state = forbidden
        owner._wait_for_data_pack = forbidden
        for query in ("绯英没专武怎么配装", "没有爻光怎么配队", "我有爻光了",
                      "我有专了，遗器光锥怎么配？", "现在没有专武了", "现在有专武了"):
            result = await owner.hsr_companion_prepare(query=query)
            assert not result.get("is_error"), result
            guidance = result["guidance_answer"]
            assert guidance["guides"][0]["name"] == "绯英"
            assert not result["query_plan"]["screen"]
            if query in {"我有专了，遗器光锥怎么配？", "现在有专武了"}:
                assert not guidance["player_conditions"]["signature_excluded"]
                assert any(cone["role"] == "signature_reference" for cone in guidance["guides"][0]["light_cone_candidates"])
            if query == "现在没有专武了":
                assert guidance["player_conditions"]["signature_excluded"]
            print(json.dumps({"request": query, "subject": guidance["guides"][0]["name"],
                              "read_path": result["query_plan"]["read_path"],
                              "excluded": guidance["player_conditions"]["excluded_names"],
                              "signature_excluded": guidance["player_conditions"]["signature_excluded"],
                              "entry": owner._service_request["state"]}, ensure_ascii=False))
        assert not sent
        assert values["player_context"] == {"owned_characters": ["花火"]}


if __name__ == "__main__":
    asyncio.run(verify())
