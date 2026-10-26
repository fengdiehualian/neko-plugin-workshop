"""Portable smoke checks for the source_player plugin package.

These run in the Market verify/release workflow, on a Linux CI runner without a
game, a display, or Windows DLLs. Keep them stdlib-only and side-effect free:
parse the manifest and the entry module as text/AST instead of importing it
(importing would load ctypes/Windows capture code).
"""

from __future__ import annotations

import ast
import json
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "plugin.toml"
ENTRY = ROOT / "__init__.py"


def _manifest() -> dict:
    return tomllib.loads(MANIFEST.read_text(encoding="utf-8"))


def test_plugin_manifest_exists() -> None:
    assert MANIFEST.is_file()
    text = MANIFEST.read_text(encoding="utf-8")
    assert 'id = "source_player"' in text
    assert 'entry = "plugin.plugins.source_player:SourcePlayerPlugin"' in text


def test_identity_and_version_are_market_ready() -> None:
    plugin = _manifest()["plugin"]
    assert plugin["id"] == "source_player"
    assert re.fullmatch(r"[a-z][a-z0-9_]*", plugin["id"])
    assert re.fullmatch(r"\d+\.\d+\.\d+", plugin["version"])
    assert plugin["type"] == "plugin"
    assert plugin["entry"].split(":")[0] == f"plugin.plugins.{plugin['id']}"
    assert plugin["entry"].split(":")[1] == "SourcePlayerPlugin"
    assert plugin["name"].strip()
    assert len(plugin["description"]) > 20
    assert plugin["keywords"] and all(isinstance(k, str) and k.strip() for k in plugin["keywords"])


def test_runtime_never_starts_or_injects_on_install() -> None:
    manifest = _manifest()
    assert manifest["plugin_runtime"]["enabled"] is True
    # Enabling the plugin must not hijack the keyboard; the user starts it.
    assert manifest["plugin_runtime"]["auto_start"] is False
    assert manifest["source"]["autopilot_enabled"] is False
    assert manifest["source"]["require_foreground"] is True
    assert manifest["source"]["max_session_minutes"] > 0


def _decorated_ids(tree: ast.Module, decorator: str, keyword: str) -> list[str]:
    found: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            if not isinstance(dec, ast.Call):
                continue
            func = dec.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
            if name != decorator:
                continue
            for kw in dec.keywords:
                if kw.arg == keyword and isinstance(kw.value, ast.Constant):
                    found.append(str(kw.value.value))
    return found


def test_entry_class_and_decorators_are_declared() -> None:
    tree = ast.parse(ENTRY.read_text(encoding="utf-8"))
    classes = {n.name for n in tree.body if isinstance(n, ast.ClassDef)}
    assert "SourcePlayerPlugin" in classes
    assert any(isinstance(n, ast.ClassDef) and n.name == "SourcePlayerPlugin"
               and any(isinstance(d, ast.Name) and d.id == "neko_plugin" for d in n.decorator_list)
               for n in tree.body)

    entry_ids = _decorated_ids(tree, "plugin_entry", "id")
    tool_names = _decorated_ids(tree, "llm_tool", "name")
    # The four user-facing controls the README promises must exist.
    for required in ("source_play", "source_stop", "source_status", "source_selfcheck"):
        assert required in entry_ids, required
    for required in ("source_play", "source_stop", "source_resume", "source_status",
                     "source_look", "source_command", "source_directive"):
        assert required in tool_names, required
    assert len(entry_ids) == len(set(entry_ids)), "duplicate @plugin_entry ids"
    assert len(tool_names) == len(set(tool_names)), "duplicate @llm_tool names"


def test_runtime_entry_handlers_are_async() -> None:
    tree = ast.parse(ENTRY.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        is_entry = any(
            isinstance(dec, ast.Call) and getattr(dec.func, "id", getattr(dec.func, "attr", "")) == "plugin_entry"
            for dec in node.decorator_list
        )
        if is_entry:
            assert isinstance(node, ast.AsyncFunctionDef), f"{node.name} must be async"


def test_locale_files_are_valid_and_complete() -> None:
    i18n = _manifest()["plugin"]["i18n"]
    assert i18n["default_locale"] == "zh-CN"
    assert i18n["locales_dir"] == "i18n"
    base = None
    for locale in ("zh-CN", "en"):
        data = json.loads((ROOT / "i18n" / f"{locale}.json").read_text(encoding="utf-8"))
        assert data["plugin.name"] and data["plugin.description"]
        if base is None:
            base = set(data)
        else:
            assert set(data) == base, f"{locale} locale keys drifted"


def test_no_requirements_txt() -> None:
    # The platform rejects requirements.txt for plugin packages; runtime
    # dependencies must live in pyproject.toml and be vendored.
    assert not (ROOT / "requirements.txt").exists()
    assert not (ROOT / "vendor").exists() or (ROOT / "pyproject.toml").is_file()


def test_config_schema_covers_every_manifest_key() -> None:
    schema = json.loads((ROOT / "config.schema.json").read_text(encoding="utf-8"))
    assert schema["type"] == "object"
    source = schema["properties"]["source"]
    assert source["type"] == "object"
    schema_keys = set(source["properties"])
    manifest_keys = set(_manifest()["source"])
    assert schema_keys == manifest_keys, (
        f"schema-only={sorted(schema_keys - manifest_keys)} "
        f"manifest-only={sorted(manifest_keys - schema_keys)}"
    )
    for key, prop in source["properties"].items():
        # Every field needs a human title; descriptions are strongly encouraged.
        assert prop.get("title"), f"{key} has no title"
        assert prop.get("type") in {"string", "number", "integer", "boolean", "object", "array"}, key


def test_llm_tool_errors_use_the_documented_shape() -> None:
    text = ENTRY.read_text(encoding="utf-8")
    tree = ast.parse(text)
    functions = {n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    assert {"_tool_error", "_ok_or_err"} <= functions

    calls = {"_tool_error": 0, "_ok_or_err": 0}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in calls:
            calls[node.func.id] += 1
    assert calls["_tool_error"] >= 10, calls
    assert calls["_ok_or_err"] >= 6, calls

    # The helper must emit the exact shape main_server understands.
    assert '"is_error": True' in text
    assert '"error": code' in text

