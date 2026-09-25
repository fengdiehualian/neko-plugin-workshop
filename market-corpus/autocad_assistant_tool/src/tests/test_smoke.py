"""Smoke test for autocad_assistant_tool plugin.

Verifies that the plugin module can be imported and the entry class exists.
Runs in isolation — no N.E.K.O SDK or AutoCAD required.
"""
import sys
from pathlib import Path


def test_plugin_importable():
    """Plugin package must be importable."""
    root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(root))
    import autocad_controller as _ac
    _ = _ac  # silence unused-import (CI uses --ignore-noqa)


def test_plugin_class_exists():
    """entry class AutoCADAssistantPlugin must exist in __init__.py."""
    root = Path(__file__).resolve().parent.parent
    init_py = root / "__init__.py"
    source = init_py.read_text(encoding="utf-8")
    assert "class AutoCADAssistantPlugin" in source, "AutoCADAssistantPlugin class not found"


def test_entry_points_defined():
    """Plugin must declare entry points via @plugin_entry."""
    root = Path(__file__).resolve().parent.parent
    init_py = root / "__init__.py"
    source = init_py.read_text(encoding="utf-8")
    assert "@plugin_entry" in source, "No @plugin_entry found — plugin exposes no tools"


def test_command_catalog_not_empty():
    """COMMAND_CATALOG must declare drawing commands."""
    root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(root))
    from autocad_controller import COMMAND_CATALOG
    assert len(COMMAND_CATALOG) > 0, "COMMAND_CATALOG is empty"
    # sanity check: a few expected drawing commands
    for action in ("line", "rectangle", "circle", "arc"):
        assert action in COMMAND_CATALOG, f"Expected command '{action}' missing from catalog"
