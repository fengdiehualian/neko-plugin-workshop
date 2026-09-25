from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path

_PLUGIN_ROOT = Path(__file__).resolve().parent.parent
_CANONICAL_MODULE = "plugin.plugins.browser_skill"


def _load_repository_candidate() -> None:
    """Make standalone release checks test this checkout, not a host copy."""
    importlib.import_module("plugin.plugins")
    current = sys.modules.get(_CANONICAL_MODULE)
    current_file = Path(str(getattr(current, "__file__", ""))).resolve()
    if current is not None and current_file == (_PLUGIN_ROOT / "__init__.py"):
        return

    spec = importlib.util.spec_from_file_location(
        _CANONICAL_MODULE,
        _PLUGIN_ROOT / "__init__.py",
        submodule_search_locations=[str(_PLUGIN_ROOT)],
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load BrowserSkill repository candidate")
    module = importlib.util.module_from_spec(spec)
    sys.modules[_CANONICAL_MODULE] = module
    spec.loader.exec_module(module)


_load_repository_candidate()
