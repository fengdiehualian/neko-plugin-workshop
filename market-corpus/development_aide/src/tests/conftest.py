"""Test bootstrap: load the plugin package for standalone test runs.

The repository root *is* the plugin package (``plugin.plugins.development_aide``
once mounted into N.E.K.O), but its directory name here is not a valid Python
identifier, so the module is loaded by file location.

Inside the N.E.K.O tree the host SDK is importable, and pytest imports this
same ``__init__.py`` a second time as the package
``plugin.plugins.development_aide``. Its ``NekoPluginBase`` resolves the plugin
directory and metadata from a live ``PluginContext`` that unit tests do not
have, so the copy the tests use is loaded while ``plugin.sdk.plugin`` is
temporarily shadowed: the package then selects its in-memory fallback and
behaves identically standalone and inside the host.

The shadow is restored immediately. It must not outlive this import, because
the host itself imports submodules of ``plugin.sdk.plugin`` and a leftover bare
module there breaks its own import chain.
"""
import importlib.util
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

if "development_aide" not in sys.modules:
    spec = importlib.util.spec_from_file_location(
        "development_aide",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["development_aide"] = module

    sentinel = object()
    previous = sys.modules.get("plugin.sdk.plugin", sentinel)
    sys.modules["plugin.sdk.plugin"] = types.ModuleType("plugin.sdk.plugin")
    try:
        spec.loader.exec_module(module)
    finally:
        if previous is sentinel:
            sys.modules.pop("plugin.sdk.plugin", None)
        else:
            sys.modules["plugin.sdk.plugin"] = previous
