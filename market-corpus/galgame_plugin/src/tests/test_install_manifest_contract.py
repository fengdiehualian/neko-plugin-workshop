from __future__ import annotations

import ast
import tomllib
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
EXPECTED_VERSION = "1.0.3"
EXPECTED_INSTALL_KINDS = {
    "textractor": {
        "entry_id": "galgame_install_textractor",
        "label": "Textractor",
        "queued_message": "Textractor install queued",
        "entry_timeout": 600.0,
    },
    "rapidocr_models": {
        "entry_id": "galgame_download_rapidocr_models",
        "label": "RapidOCR Models",
        "queued_message": "RapidOCR model download queued",
        "entry_timeout": 600.0,
    },
}


def _load_toml(path: Path) -> dict[str, object]:
    with path.open("rb") as handle:
        return tomllib.load(handle)


def _plugin_entry_timeouts() -> dict[str, float | None]:
    entries: dict[str, float | None] = {}
    for source_path in (PLUGIN_ROOT / "plugin_entries").glob("*.py"):
        tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                if not (
                    isinstance(decorator, ast.Call)
                    and isinstance(decorator.func, ast.Name)
                    and decorator.func.id == "plugin_entry"
                ):
                    continue
                keywords = {item.arg: item.value for item in decorator.keywords if item.arg}
                entry_id = ast.literal_eval(keywords["id"])
                timeout_node = keywords.get("timeout")
                entries[str(entry_id)] = (
                    float(ast.literal_eval(timeout_node))
                    if timeout_node is not None
                    else None
                )
    return entries


def test_install_manifest_matches_real_plugin_entries() -> None:
    manifest = _load_toml(PLUGIN_ROOT / "plugin.toml")
    install = manifest["plugin"]["install"]

    assert install["enabled"] is True
    assert install["ui_i18n_dir"] == "i18n/ui"
    assert install["tutorial_enabled"] is True
    assert install["kinds"] == EXPECTED_INSTALL_KINDS
    assert set(install["kinds"]) == {"textractor", "rapidocr_models"}
    assert (PLUGIN_ROOT / install["ui_i18n_dir"]).is_dir()

    decorator_timeouts = _plugin_entry_timeouts()
    for declaration in install["kinds"].values():
        entry_id = declaration["entry_id"]
        assert entry_id in decorator_timeouts
        assert declaration["entry_timeout"] == decorator_timeouts[entry_id] == 600.0


def test_package_entry_has_no_install_registry_side_effects() -> None:
    source = (PLUGIN_ROOT / "__init__.py").read_text(encoding="utf-8")
    assert "register_install_plugin" not in source
    assert "InstallKindRegistration" not in source
    assert "register_tutorial_migration_hook" not in source
    assert "_register_install_routes" not in source
    assert "_register_tutorial_migration_hook" not in source


def test_release_versions_are_synchronized() -> None:
    manifest = _load_toml(PLUGIN_ROOT / "plugin.toml")
    pyproject = _load_toml(PLUGIN_ROOT / "pyproject.toml")
    lock = _load_toml(PLUGIN_ROOT / "uv.lock")
    virtual_package = next(
        package
        for package in lock["package"]
        if package["name"] == pyproject["project"]["name"]
        and package.get("source") == {"virtual": "."}
    )

    assert manifest["plugin"]["version"] == EXPECTED_VERSION
    assert pyproject["project"]["version"] == EXPECTED_VERSION
    assert virtual_package["version"] == EXPECTED_VERSION
