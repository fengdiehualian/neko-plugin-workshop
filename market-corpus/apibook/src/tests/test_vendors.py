"""Plugin-local providers resolve only into existing custom model fields."""
import asyncio
import json

import pytest
from apibook.connectivity import test_preset as run_probes
from apibook.domain import KEEP_SECRET, PresetError, build_payload, normalize_preset, public_preset
from apibook.host import provider_catalog
from apibook.storage import PresetRepository


def make_preset():
    return {
        "name": "book", "core": "free", "assist": "free",
        "vendors": {"vendor_1": {"name": "Vendor", "url": "https://custom.example.test/v1", "key": "vendor-secret"}},
        "slots": {"conversation": {"provider": "custom", "vendor_id": "vendor_1", "model": "one"}},
    }


def metadata():
    return {
        "success": True,
        "core_api_providers_full": {"free": {"core_url": "wss://free.example.test", "core_model": "free"}},
        "assist_api_providers_full": {"free": {"openrouter_url": "https://free.example.test/v1", "conversation_model": "free"}},
        "api_key_registry": {},
    }


def test_book_save_copy_update_clear_mask_and_legacy(tmp_path):
    repo = PresetRepository(tmp_path / "presets.json")
    saved = repo.save(make_preset())
    assert normalize_preset({"name": "legacy"})["vendors"] == {}
    masked = public_preset(saved["preset"])
    assert "vendor-secret" not in json.dumps(masked)
    assert masked["vendors"]["vendor_1"]["key"] == KEEP_SECRET
    updated = repo.save(masked, saved["id"], saved["revision"])
    copy = repo.copy(saved["id"], "copy")
    copy["preset"]["vendors"]["vendor_1"].update(name="Different", url="https://new.example.test/v1", key="new-secret")
    changed = repo.save(copy["preset"], copy["id"], copy["revision"])
    payload = build_payload(changed["preset"], provider_catalog(metadata()))
    assert payload["conversationModelProvider"] == "custom"
    assert payload["conversationModelUrl"] == "https://new.example.test/v1"
    assert payload["conversationModelApiKey"] == "new-secret"
    assert payload["conversationModelId"] == "one"
    assert not any("vendor" in key.lower() for key in payload)
    original = repo.find(repo.read(), saved["id"])["preset"]
    assert original["vendors"]["vendor_1"]["key"] == "vendor-secret"
    assert normalize_preset(public_preset(original), original) == original
    cleared = public_preset(original)
    cleared["vendors"]["vendor_1"]["key"] = ""
    assert repo.save(cleared, saved["id"], updated["revision"])["preset"]["vendors"]["vendor_1"]["key"] == ""


@pytest.mark.parametrize("mutation,code", [
    (lambda p: p["vendors"].update({f"vendor_{i}": {"name": str(i), "url": "https://x.test", "key": ""} for i in range(1, 34)}), "vendor_limit"),
    (lambda p: p["vendors"].clear(), "invalid_vendor"),
    (lambda p: p["vendors"]["vendor_1"].update(name=""), "invalid_vendor"),
    (lambda p: p["vendors"].update(vendor_2=p["vendors"]["vendor_1"].copy()), "invalid_vendor"),
    (lambda p: p["vendors"]["vendor_1"].update(url="https://x.test?key=secret"), "invalid_url"),
    (lambda p: p["slots"]["conversation"].update(url="https://override.test"), "invalid_slot"),
    (lambda p: p["slots"].update(omni={"provider": "custom", "vendor_id": "vendor_1", "model": "voice"}), "invalid_url"),
])
def test_vendor_bounds_and_invalid_references(mutation, code):
    raw = make_preset()
    mutation(raw)
    with pytest.raises(PresetError, match=code):
        normalize_preset(raw)


def test_selected_vendor_probes_use_latest_credentials_and_deduplicate():
    raw = make_preset()
    raw["slots"]["summary"] = {"provider": "custom", "vendor_id": "vendor_1", "model": "one"}
    raw["slots"]["emotion"] = {"provider": "custom", "vendor_id": "vendor_1", "model": "two"}
    raw["slots"]["agent"] = {"provider": "custom", "url": "https://custom.example.test/v1/", "model": "one", "key": "vendor-secret"}
    raw["vendors"]["vendor_2"] = {"name": "Unused", "url": "https://unused.example.test", "key": "unused-secret"}
    preset = normalize_preset(raw)
    calls = []

    class Host:
        async def request(self, method, path, payload=None):
            if method == "GET":
                return metadata()
            calls.append(payload)
            return {"success": payload.get("model") != "two", "error_code": "auth_failed"}

    result = asyncio.run(run_probes(Host(), preset))
    matching = [c for c in calls if c.get("url") == "https://custom.example.test/v1"]
    assert len(matching) == 2 and {c["model"] for c in matching} == {"one", "two"}
    assert all(c["api_key"] == "vendor-secret" and not c["is_free"] for c in matching)
    group = next(g for g in result["groups"] if "conversation" in g["functions"])
    assert {"conversation", "summary", "agent", "gameMain", "gameSummary"}.issubset(group["functions"])
    assert next(g for g in result["groups"] if "emotion" in g["functions"])["status"] == "failed"
    assert "secret" not in json.dumps(result) and "example.test" not in json.dumps(result)
    assert not any(c.get("api_key") == "unused-secret" for c in calls)


def test_id_migration_preserves_original_and_never_overwrites_new_data(tmp_path):
    old = PresetRepository(tmp_path / "api_presets" / "data" / "presets.json")
    new = PresetRepository(tmp_path / "apibook" / "data" / "presets.json")
    assert new.migrate_legacy_id() is False
    item = old.save(make_preset())
    original = old.path.read_bytes()
    assert new.migrate_legacy_id() is True
    assert new.read()["presets"][0]["id"] == item["id"]
    assert old.path.read_bytes() == original
    new.save(new.read()["presets"][0]["preset"] | {"notes": "new"}, item["id"], item["revision"])
    assert new.migrate_legacy_id() is False
    assert new.read()["presets"][0]["preset"]["notes"] == "new"
    new.path.write_bytes(b"\xff")
    assert new.migrate_legacy_id() is False  # Never replace existing invalid data.
