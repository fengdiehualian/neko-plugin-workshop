from __future__ import annotations

import json
from pathlib import Path

import pytest
from plugin.plugins.browser_skill import _read_local_settings, _write_local_settings


def _virtual_path_io(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    storage: dict[str, str] = {}

    monkeypatch.setattr(Path, "mkdir", lambda *_args, **_kwargs: None)

    def write_text(path: Path, value: str, **_kwargs: object) -> int:
        storage[str(path)] = value
        return len(value)

    def read_text(path: Path, **_kwargs: object) -> str:
        if str(path) not in storage:
            raise FileNotFoundError(str(path))
        return storage[str(path)]

    def replace(path: Path, target: Path) -> Path:
        storage[str(target)] = storage.pop(str(path))
        return target

    monkeypatch.setattr(Path, "write_text", write_text)
    monkeypatch.setattr(Path, "read_text", read_text)
    monkeypatch.setattr(Path, "replace", replace)
    return storage


def test_plugin_local_settings_round_trip_without_active_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = _virtual_path_io(monkeypatch)
    path = Path("virtual") / "browser_skill_settings.json"
    expected = {
        "session_scope": "plugin",
        "reuse_existing_window": True,
        "allow_additional_agent_tabs": False,
    }

    _write_local_settings(path, expected)

    assert _read_local_settings(path) == expected
    assert str(path.with_suffix(".json.tmp")) not in storage
    assert json.loads(storage[str(path)]) == expected


def test_invalid_plugin_local_settings_file_is_ignored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = _virtual_path_io(monkeypatch)
    path = Path("virtual") / "browser_skill_settings.json"
    storage[str(path)] = "not-json"

    assert _read_local_settings(path) == {}
