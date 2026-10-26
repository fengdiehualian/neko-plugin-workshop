"""Read-only saved Cyrene replay through formal entry, no game/host/model IO."""
from __future__ import annotations

import argparse
import asyncio
import copy
import hashlib
import json
import sqlite3
import time
from pathlib import Path
from unittest.mock import patch


async def replay():
    import ormsgpack
    from plugin.plugins.hsr_companion.data import ExternalKnowledgeCatalog
    from plugin.plugins.hsr_companion.tests.test_current_observation import plugin_fixture

    root = Path(__file__).resolve().parents[1]
    database = Path("C:/Users/USER/AppData/Local/N.E.K.O/data-packs/hsr_companion/player-state/store.db")
    with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as connection:
        rows = {key: value for key, value in connection.execute(
            "SELECT key,value FROM kv_store WHERE key IN (?,?)", ("manual_player_builds", "uid_import_state"))}
    store = {key: ormsgpack.unpackb(value) for key, value in rows.items()}
    owner, values, sent = plugin_fixture(root / ".stage3-archive-fixture-unused")
    owner._catalog = ExternalKnowledgeCatalog(registry=owner._catalog.registry)
    values.update(copy.deepcopy(store))
    before = copy.deepcopy(values)
    owner._guidance_context = {"at": time.monotonic() - 1080, "kind": "challenge", "mode": "moc",
        "challenge_floor": 12, "challenge_difficulty": "normal", "execution_intent": "team_guide"}

    async def forbidden(*args, **kwargs):
        raise AssertionError("Saved-source replay cannot start capture, game read or download")

    with patch.object(owner, "_current_visual_evidence", forbidden), \
            patch.object(owner, "_read_game_state", forbidden), \
            patch.object(owner, "_wait_for_data_pack", forbidden):
        result = await owner.hsr_companion_prepare(query="那昔涟这把光锥不合适，还能先用这队吗？",
            intent="assess_cone", consultation={"scope": "saved_cone", "subject_state": "specified", "character": "昔涟"})
    guidance = result.get("guidance_answer") or {}
    # Private UID/raw account rows are never copied to this report.
    protected_keys = ("manual_player_builds", "uid_import_state", "player_context")
    return {"scope": "real_saved_archive_formal_tool_isolated_replay", "model_selection_tested": False,
        "archive_inputs_sha256": {key: hashlib.sha256(value).hexdigest() for key, value in rows.items()},
        "query_plan": result.get("query_plan"), "saved_cone_answer": guidance.get("saved_cone_answer"),
        "reply_plan": guidance.get("reply_plan"), "request_completion": result.get("request_completion"),
        "followup": result.get("followup"), "error": result.get("error"),
        "original_player_fields_unchanged_in_fixture": all(values.get(key) == before.get(key) for key in protected_keys),
        "real_database_opened_read_only": True, "host_cat_submissions": len(sent), "installation": False}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", required=True)
    args = parser.parse_args()
    report = Path(args.report).resolve()
    if not report.is_relative_to(Path(__file__).resolve().parents[1] / "dist") or report.exists():
        raise ValueError("Use a new plugin-owned report")
    result = asyncio.run(replay())
    with report.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    print(json.dumps({"screen": (result.get("query_plan") or {}).get("screen"),
                      "answer": result.get("saved_cone_answer"), "error": result.get("error")}, ensure_ascii=True))
