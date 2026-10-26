"""Published copy and static Hosted action discovery must match the panel."""

import asyncio
import json
import re
import tomllib
from pathlib import Path

from ..entry import NekoRimworldPlugin

ROOT = Path(__file__).resolve().parents[1]


def test_ui_languages_cover_the_same_messages_and_keep_interpolation_parameters():
    bundles = {locale: json.loads((ROOT / "locales" / f"{locale}.json").read_text(encoding="utf-8"))
               for locale in ("en", "zh-CN")}
    assert bundles["en"].keys() == bundles["zh-CN"].keys()
    for key, english in bundles["en"].items():
        assert english.strip() and bundles["zh-CN"][key].strip()
        assert set(re.findall(r"\{(\w+)\}", english)) == set(re.findall(r"\{(\w+)\}", bundles["zh-CN"][key]))
    for source in (ROOT / "ui").rglob("*.ts*"):
        for key in re.findall(r'\bt\("([\w.]+)"', source.read_text(encoding="utf-8")):
            assert key in bundles["en"], (source, key)
    manifest = tomllib.loads((ROOT / "plugin.toml").read_text(encoding="utf-8"))
    assert manifest["plugin"]["i18n"] == {"default_locale": "en", "locales_dir": "locales"}


def test_static_save_and_history_schemas_match_their_executable_entries():
    manifest = tomllib.loads((ROOT / "plugin.toml").read_text(encoding="utf-8"))
    entries = {row["id"]: row for row in manifest["entries"]}
    for name in ("begin_settings_save", "get_settings_save", "workspace_view"):
        metadata = getattr(NekoRimworldPlugin, name).__neko_event_meta__
        assert entries[name]["input_schema"] == metadata.input_schema
        assert entries[name]["metadata"]["agent_auto"] is False
    schema = entries["begin_settings_save"]["input_schema"]["properties"]
    assert all(schema[key]["writeOnly"] for key in ("api_key", "pairing_token", "ui_nonce"))


def test_surface_title_uses_the_same_language_resources_as_the_panel():
    from plugin.sdk.shared.i18n import load_plugin_i18n_from_dir, resolve_i18n_refs

    manifest = tomllib.loads((ROOT / "plugin.toml").read_text(encoding="utf-8"))
    title = manifest["plugin"]["ui"]["panel"][0]["title"]
    messages = load_plugin_i18n_from_dir(ROOT / "locales", default_locale="en")
    assert resolve_i18n_refs(title, messages, locale="en") == "RimWorld advice"
    assert resolve_i18n_refs(title, messages, locale="zh-CN") == "RimWorld 建议"


def test_registered_guide_loads_separately_without_a_running_plugin(monkeypatch):
    from types import SimpleNamespace

    from plugin.core.state import state
    from plugin.core.ui_manifest import normalize_plugin_ui_manifest
    from plugin.server.application.plugins import ui_query_service

    manifest = tomllib.loads((ROOT / "plugin.toml").read_text(encoding="utf-8"))
    metadata = {
        "id": "neko_rimworld",
        "config_path": str(ROOT / "plugin.toml"),
        "plugin_ui": normalize_plugin_ui_manifest(manifest, plugin_id="neko_rimworld"),
        "i18n": manifest["plugin"]["i18n"],
        "entries": manifest["entries"],
    }
    monkeypatch.setattr(ui_query_service, "_get_plugin_meta_sync", lambda _: metadata)
    monkeypatch.setitem(state.plugin_hosts, "neko_rimworld", SimpleNamespace(is_alive=lambda: False))
    service = ui_query_service.PluginUiQueryService()
    for locale, title in (("en", "RimWorld setup guide"), ("zh-CN", "RimWorld 使用教程")):
        result = asyncio.run(service.get_surfaces("neko_rimworld", locale=locale))
        assert not result["warnings"]
        assert [row["kind"] for row in result["surfaces"]] == ["panel", "guide"]
        guide = result["surfaces"][1]
        assert guide["title"] == title
        assert guide["available"] and guide["mode"] == "hosted-tsx"
        assert guide["permissions"] == []
        context = asyncio.run(service.get_surface_context(
            "neko_rimworld", kind="guide", surface_id="quickstart", locale=locale,
        ))
        assert not context["warnings"]
        assert context["state"] == {} and context["actions"] == []
        assert context["config"]["value"] == {} and context["config"]["readonly"]
        source = asyncio.run(service.get_surface_source(
            "neko_rimworld", kind="guide", surface_id="quickstart", locale=locale,
        ))
        assert source["entry"] == "ui/guide/guide-page.tsx"
        assert {row["path"] for row in source["dependencies"]} == {"ui/guide/content.ts"}
