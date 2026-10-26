"""Load the plugin's helper modules without importing the plugin package.

``plugin/__init__.py`` pulls in the N.E.K.O SDK. These unit tests cover pure
parsing/provider/resilience logic, so each module is loaded directly from its
file and given a package identity only for sibling imports.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parents[1]
PACKAGE = "_better_web_search_under_test"


def _ensure_namespace() -> types.ModuleType:
    module = sys.modules.get(PACKAGE)
    if module is None:
        module = types.ModuleType(PACKAGE)
        module.__path__ = [str(PLUGIN_DIR)]  # type: ignore[attr-defined]
        sys.modules[PACKAGE] = module
    return module


def load(name: str):
    full = f"{PACKAGE}.{name}"
    cached = sys.modules.get(full)
    if cached is not None:
        return cached
    namespace = _ensure_namespace()
    spec = importlib.util.spec_from_file_location(full, PLUGIN_DIR / f"{name}.py")
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise ImportError(f"cannot load {name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[full] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(full, None)
        raise
    setattr(namespace, name, module)
    return module


FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def fixture_bytes(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()
