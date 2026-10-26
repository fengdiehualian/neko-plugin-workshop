"""Bounded, atomic UTF-8 storage in the host-assigned plugin data directory."""

import json
import os
import tempfile
from pathlib import Path
from uuid import uuid4

from .domain import MAX_PRESETS, PresetError, normalize_preset

MAX_BYTES = 2 * 1024 * 1024


class PresetRepository:
    def __init__(self, path: Path):
        self.path = path

    def migrate_legacy_id(self):
        """One-time migration from this plugin's declared predecessor ID."""
        if self.path.exists() or self.path.parent.name != "data" or self.path.parent.parent.name != "apibook":
            return False
        previous = self.path.parent.parent.parent / "api_presets" / "data" / "presets.json"
        if not previous.is_file():
            return False
        # Validate limits and UTF-8 before writing. Leave the original untouched;
        # an existing new-ID file always wins, including on subsequent restarts.
        self.write(PresetRepository(previous).read())
        return True

    def read(self):
        if not self.path.exists():
            return {"version": 1, "presets": [], "last_application": None}
        with self.path.open("r", encoding="utf-8", errors="strict") as handle:
            content = handle.read(MAX_BYTES + 1)
        if len(content.encode("utf-8")) > MAX_BYTES:
            raise PresetError("storage_limit")
        data = json.loads(content)
        if not isinstance(data, dict) or data.get("version") != 1 or not isinstance(data.get("presets"), list):
            raise PresetError("invalid_storage")
        if len(data["presets"]) > MAX_PRESETS:
            raise PresetError("storage_limit")
        seen = set()
        for item in data["presets"]:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str) or item["id"] in seen:
                raise PresetError("invalid_storage")
            seen.add(item["id"])
            if not isinstance(item.get("revision"), int) or item["revision"] < 1:
                raise PresetError("invalid_storage")
            item["preset"] = normalize_preset(item["preset"])
        return data

    def write(self, data):
        self.read()  # Verify existing UTF-8; never silently convert before replacing.
        serialized = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
        if len(serialized.encode("utf-8")) > MAX_BYTES:
            raise PresetError("storage_limit")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.path.parent, delete=False) as handle:
                temporary = Path(handle.name)
                os.chmod(temporary, 0o600)
                handle.write(serialized)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def find(self, data, preset_id):
        for item in data["presets"]:
            if item["id"] == preset_id:
                return item
        raise PresetError("not_found")

    def save(self, raw, preset_id="", revision=0):
        data = self.read()
        old = self.find(data, preset_id) if preset_id else None
        if old and old["revision"] != revision:
            raise PresetError("conflict")
        preset = normalize_preset(raw, old["preset"] if old else None)
        if any(item is not old and item["preset"]["name"].casefold() == preset["name"].casefold() for item in data["presets"]):
            raise PresetError("duplicate_name")
        if old:
            old.update(preset=preset, revision=old["revision"] + 1)
            item = old
        else:
            if len(data["presets"]) >= MAX_PRESETS:
                raise PresetError("preset_limit")
            item = {"id": uuid4().hex, "revision": 1, "preset": preset}
            data["presets"].append(item)
        self.write(data)
        return item

    def copy(self, preset_id, name):
        data = self.read()
        preset = self.find(data, preset_id)["preset"]
        return self.save(preset | {"name": name})

    def delete(self, preset_id, revision):
        data = self.read()
        item = self.find(data, preset_id)
        if item["revision"] != revision:
            raise PresetError("conflict")
        data["presets"].remove(item)
        if (data.get("last_application") or {}).get("preset_id") == preset_id:
            data["last_application"] = None
        self.write(data)

    def application(self, status):
        data = self.read()
        data["last_application"] = status
        self.write(data)
