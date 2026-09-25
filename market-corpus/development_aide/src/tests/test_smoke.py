"""Market smoke tests.

They run without the N.E.K.O host SDK thanks to the package's in-memory
fallback: import, startup lifecycle and UI context must always work.
"""
import asyncio
from pathlib import Path

from development_aide import (
    DEFAULT_MAX_CHARS,
    MAX_BATCH_FILES,
    DevelopmentAidePlugin,
    coerce_read_limit,
)


def test_plugin_manifest_declares_canonical_entry():
    """The host resolves installed plugins as ``plugins.<dir>:Class``."""
    manifest = Path(__file__).resolve().parents[1] / "plugin.toml"
    text = manifest.read_text(encoding="utf-8")

    assert 'id = "development_aide"' in text
    assert 'entry = "plugin.plugins.development_aide:DevelopmentAidePlugin"' in text


def test_plugin_imports_with_safe_defaults():
    plugin = DevelopmentAidePlugin(None)

    assert plugin.read_only is True
    assert plugin.max_chars == DEFAULT_MAX_CHARS
    assert plugin.enable_code_review is True
    assert MAX_BATCH_FILES > 0


def test_startup_lifecycle_runs_standalone():
    plugin = DevelopmentAidePlugin(None)

    result = asyncio.run(plugin.on_startup())

    assert result["status"] == "ready"


def test_settings_context_has_config_and_status():
    plugin = DevelopmentAidePlugin(None)

    context = asyncio.run(plugin.settings_context())

    assert "config" in context
    assert "status" in context
    assert context["status"]["features_enabled"]["code_review"] is True


def test_default_read_limit_is_safe():
    limit, capped = coerce_read_limit(None)

    assert limit == DEFAULT_MAX_CHARS
    assert capped is False
