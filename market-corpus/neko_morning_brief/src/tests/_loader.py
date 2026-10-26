"""Import the plugin under a synthetic package so tests never touch the SDK.

The real package ``plugin.plugins.neko_morning_brief`` needs ``plugin.sdk`` at
import time. Registering a stand-in package (``nmb``) whose ``__path__`` points
at the plugin directory lets ``nmb.paths`` and friends import normally, with
relative imports intact and no host checkout required.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ALIAS = "nmb"

if ALIAS not in sys.modules:
    package = types.ModuleType(ALIAS)
    package.__path__ = [str(ROOT)]  # type: ignore[attr-defined]
    package.__package__ = ALIAS
    sys.modules[ALIAS] = package

FIXTURES = Path(__file__).resolve().parent / "fixtures"
SCRATCH = Path(__file__).resolve().parent / "_scratch"


def setup() -> None:
    """Idempotent marker so tests can import this module for its side effect."""

    return None


def scratch_dir() -> Path:
    """Shared scratch directory; its subdirectories are created ahead of time."""

    return SCRATCH


def db_path(name: str = "brief") -> Path:
    """A unique SQLite path inside the scratch directory (files are disposable)."""

    import uuid

    SCRATCH.mkdir(parents=True, exist_ok=True)
    return SCRATCH / f"{name}-{uuid.uuid4().hex[:10]}.db"


def cleanup_db(path: Path) -> None:
    for suffix in ("", "-wal", "-shm"):
        try:
            Path(str(path) + suffix).unlink(missing_ok=True)
        except OSError:
            pass


def import_module(name: str):
    import importlib

    return importlib.import_module(f"{ALIAS}.{name}")
