"""Exact approved user assets, independent of renderer/server availability."""

import hashlib
import json
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "assets" / "appearances"


def test_original_packer_is_retained_as_an_exact_source_reference():
    reference = ROOT / "sources/pack-neko-yui.py.txt"
    data = reference.read_bytes()
    assert len(data) == 1301
    assert hashlib.sha256(data).hexdigest() == "c3b211a7cd1a20bf5f941c6fb607ff1fd6371f15ae1bca601823acdebb9f601c"
    assert not (ROOT / "sources/pack-neko-yui.py").exists()


def test_bundled_yui_source_bytes_and_provenance():
    catalog = json.loads((ROOT / "catalog.json").read_text(encoding="utf-8"))
    assert catalog["schema"] == "mc-bundled-appearances/v1"
    assert catalog["source"] == "plugin_bundled"
    assert len(catalog["assets"]) == 8
    for relative, entry in catalog["assets"].items():
        source = ROOT / relative
        assert source.resolve().is_relative_to(ROOT.resolve())
        data = source.read_bytes()
        assert len(data) == entry["bytes"]
        assert hashlib.sha256(data).hexdigest() == entry["sha256"]
    png = (ROOT / "skins/yui-lolita-slim.png").read_bytes()
    assert hashlib.sha256(png).hexdigest() == "f8ed6510dec8647895fb633dde7cad0d3338fe7b39f78e2566d389e4ec33709f"
    assert png == (ROOT / "ysm/neko_yui/textures/default.png").read_bytes()
    assert catalog["appearances"][0]["model"] == "slim"
    definition = json.loads((ROOT / "ysm/neko_yui/ysm.json").read_text(encoding="utf-8"))
    assert definition["properties"]["height_scale"] == .9
    assert definition["properties"]["width_scale"] == .9
    assert definition["metadata"]["license"]["type"] == "Custom"
    assert "Kolby Nottingham" in (ROOT / "sources/LICENSE-MIT.txt").read_text()
    assert "Codex" in (ROOT / "PROVENANCE.md").read_text(encoding="utf-8")


def test_bundled_original_zip_and_seven_sources_match():
    archive = ROOT / "ysm/neko_yui.zip"
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == "dabb81be3a60a8a9927553e8ca53b975f8c5ab999bd5b71f8083d3e6e861b10e"
    manifest = json.loads((ROOT / "sources/neko-yui-manifest.json").read_text())
    with zipfile.ZipFile(archive) as zipped:
        assert set(zipped.namelist()) == set(manifest["files"])
        for relative, entry in manifest["files"].items():
            data = zipped.read(relative)
            assert data == (ROOT / "ysm/neko_yui" / relative).read_bytes()
            assert hashlib.sha256(data).hexdigest() == entry["sha256"]


def test_embedded_web_defaults_preserve_user_settings_and_disabled_start(plugin_module):
    helper = plugin_module.default_bundled_appearance_settings
    raw = {"viewer": {"enabled": True}, "autonomy": {"enabled": False}}
    selected = helper(raw)
    assert selected["viewer"]["bundled_appearance_catalog_file"] == str(ROOT.resolve() / "catalog.json")
    assert raw == {"viewer": {"enabled": True}, "autonomy": {"enabled": False}}
    assert selected["autonomy"]["enabled"] is False
    assert selected["viewer"]["default_appearance"] == "bundled:yui-ysm"
    assert plugin_module.Settings.parse(selected).identity == plugin_module.Settings.parse(raw).identity
    explicit = {"viewer": {"bundled_appearance_catalog_file": "explicit/catalog.json"}}
    assert helper(explicit)["viewer"] == {**explicit["viewer"], "default_appearance": "bundled:yui-ysm"}
    for appearance in ("server", "bundled:yui-slim", "native:misc/3_default_boy:red"):
        configured = {"viewer": {"bundled_appearance_catalog_file": "explicit/catalog.json", "default_appearance": appearance}}
        assert helper(configured) is configured
    malformed = {"viewer": "invalid"}
    assert helper(malformed) is malformed
