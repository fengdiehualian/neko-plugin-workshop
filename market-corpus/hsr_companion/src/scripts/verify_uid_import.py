"""Read-only live import through formal plugin entries; no host/install writes.

Usage: python -m plugin.plugins.hsr_companion.scripts.verify_uid_import --uid <CN UID>
Only normalized data is held in memory. Output omits UID and raw account info.
"""

import argparse
import asyncio
import json
from copy import deepcopy
from pathlib import Path

from plugin.plugins.hsr_companion.core import CharacterRegistry
from plugin.plugins.hsr_companion.data import ExternalKnowledgeCatalog
from plugin.plugins.hsr_companion.runtime.uid_import import STORE_KEY, utcnow
from plugin.sdk.plugin import Ok, unwrap_or

from plugin.plugins.hsr_companion import HsrCompanionPlugin


async def verify(uid, *, use_system_proxy=False, seed_snapshot=None):
    root = Path(__file__).resolve().parents[1]
    registry = json.loads((root / "resources/character_registry.json").read_text(encoding="utf-8"))
    owner = object.__new__(HsrCompanionPlugin)
    owner._characters = CharacterRegistry(registry)
    owner._catalog = ExternalKnowledgeCatalog(registry=registry)
    values = {"player_context": {"owned_characters": []}, "last_local_vision": {"sentinel": "unchanged"}}
    if seed_snapshot is not None:
        values[STORE_KEY] = deepcopy(seed_snapshot)
    before_count = len(values.get(STORE_KEY, {}).get("characters", {}))

    async def get(key):
        return Ok(deepcopy(values.get(key)))

    async def save(key, value):
        values[key] = deepcopy(value)

    async def touch(reason):
        return {}, False

    async def audit(*args, **kwargs):
        return None

    async def corrections():
        return []

    owner._get_store, owner._store_value = get, save
    owner._touch_session, owner._record_tool_use, owner._read_corrections = touch, audit, corrections
    owner._read_session = audit
    owner.push_message = lambda **kwargs: (_ for _ in ()).throw(AssertionError("No host messages allowed"))
    staged = unwrap_or(await owner.preview_uid_import(uid, consent=True, use_system_proxy=use_system_proxy), {})[
        "uid_import"
    ]
    if staged["status"] != "preview":
        return {
            "state": "failed",
            "code": staged.get("error_code"),
            "error_info": staged.get("error_info"),
            "installed": False,
        }
    preview = staged["preview"]
    assert len(values[STORE_KEY].get("characters", {})) == before_count
    committed = unwrap_or(await owner.confirm_uid_import(preview["preview_id"]), {})["uid_import"]
    names = [row["name"] for row in committed["roster"]["characters"]]
    result = await owner.hsr_get_player_context(character_names=names[:12])
    assert values["last_local_vision"] == {"sentinel": "unchanged"}
    assert values["player_context"] == {"owned_characters": []}
    assert all(not row["visually_confirmed"] for row in result["imported_roster"]["characters"])
    return {
        "state": "passed",
        "checked_at": utcnow().isoformat(),
        "installed": False,
        "storage": "isolated_memory_not_installed_plugin",
        "network_mode": "system_proxy" if use_system_proxy else "inherit",
        "before_count": before_count,
        "preview_summary": preview["summary"],
        "saved_count": len(committed["roster"]["characters"]),
        "source": "Enka.Network",
        "cn_uid_read": True,
        "four_six_character_read": any(n in names for n in ("真珠", "绯英")),
        "formal_entries": ["preview_uid_import", "confirm_uid_import", "hsr_get_player_context"],
        "preview_requires_confirmation": True,
        "visual_and_manual_profile_unchanged": True,
        "characters": [
            {
                "name": row["name"],
                "level": row["level"],
                "eidolon": row["eidolon"],
                "cone": row["light_cone"],
                "relic_count": len(row["relics"]),
                "complete_relic_count": sum(x["complete"] for x in row["relics"]),
                "trace_count": len(row["traces"]),
                "missing": row["missing"],
                "panel_state": row["panel"]["state"],
            }
            for row in result["imported_roster"]["characters"]
        ],
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--uid", required=True)
    parser.add_argument(
        "--use-system-proxy", action="store_true", help="Only with explicit permission for the existing system proxy"
    )
    args = parser.parse_args()
    print(
        json.dumps(asyncio.run(verify(args.uid, use_system_proxy=args.use_system_proxy)), ensure_ascii=False, indent=2)
    )
