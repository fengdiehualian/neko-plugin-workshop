"""Make an external plugin checkout importable by the host test runner."""

from pathlib import Path

from plugin import plugins as plugin_plugins

plugin_plugins.__path__.insert(0, str(Path(__file__).resolve().parents[2]))
