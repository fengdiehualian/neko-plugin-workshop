"""Smoke tests for neko_mail plugin (CI-safe, no module imports)."""

from pathlib import Path

ROOT = Path(__file__).parent.parent


def test_plugin_toml_exists():
    """plugin.toml must exist."""
    assert (ROOT / "plugin.toml").is_file()


def test_init_py_exists():
    """__init__.py must exist at root."""
    assert (ROOT / "__init__.py").is_file()


def test_readme_exists():
    """README.md must exist (case-sensitive)."""
    assert (ROOT / "README.md").is_file()


def test_plugin_toml_has_entry():
    """plugin.toml must declare entry with correct prefix."""
    content = (ROOT / "plugin.toml").read_text(encoding="utf-8")
    assert 'entry = "plugin.plugins.neko_mail:' in content
