"""Load plugin modules without executing the N.E.K.O runtime entrypoint."""

import sys
import types
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[2]


def bootstrap():
    if "neko_tlm" not in sys.modules:
        package = types.ModuleType("neko_tlm")
        package.__path__ = [str(PLUGIN_ROOT)]
        sys.modules["neko_tlm"] = package


def bootstrap_sdk():
    bootstrap()
    # The market workflow runs tests from an environment where the real N.E.K.O
    # SDK may already be imported.  The unit tests intentionally assert the
    # legacy mapping-shaped result contract, so do not silently keep the SDK's
    # Ok/Err Result objects here (they are not subscriptable).
    plugin_package = sys.modules.setdefault(
        "plugin", types.ModuleType("plugin")
    )
    sdk_package = sys.modules.setdefault(
        "plugin.sdk", types.ModuleType("plugin.sdk")
    )
    sdk_module = sys.modules.setdefault(
        "plugin.sdk.plugin", types.ModuleType("plugin.sdk.plugin")
    )
    sdk_module.Ok = lambda value: {"output": value, "is_error": False}
    sdk_module.Err = lambda value: {
        "output": {"error": str(value)},
        "is_error": True,
        "error": "ERROR",
    }
    # Keep parent-module attributes coherent when the real SDK package was
    # loaded before this helper was called.
    plugin_package.sdk = sdk_package
    sdk_package.plugin = sdk_module
