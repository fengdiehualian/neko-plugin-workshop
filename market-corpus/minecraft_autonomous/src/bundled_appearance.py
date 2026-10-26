"""Neko Web presentation defaults, separate from server/account appearance."""

from pathlib import Path

CATALOG_FILE = Path(__file__).resolve().parent / "assets" / "appearances" / "catalog.json"


def default_bundled_appearance_settings(raw: dict) -> dict:
    """Supply embedded Web defaults without writing or replacing user choices."""
    viewer = raw.get("viewer", {})
    if not isinstance(viewer, dict):
        return raw
    defaults = {}
    if viewer.get("bundled_appearance_catalog_file", "") == "":
        defaults["bundled_appearance_catalog_file"] = str(CATALOG_FILE)
    if "default_appearance" not in viewer:
        defaults["default_appearance"] = "bundled:yui-ysm"
    return {**raw, "viewer": {**viewer, **defaults}} if defaults else raw
