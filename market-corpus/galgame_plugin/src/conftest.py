from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType

from tests.support.host_stubs import install_host_import_stubs

PLUGIN_ROOT = Path(__file__).resolve().parent
TEST_NAMESPACE = "market_plugins.galgame_plugin"


def _install_test_namespace() -> None:
    parent = sys.modules.setdefault("market_plugins", ModuleType("market_plugins"))
    parent.__path__ = []

    package = sys.modules.setdefault(TEST_NAMESPACE, ModuleType(TEST_NAMESPACE))
    package.__file__ = str(PLUGIN_ROOT / "__init__.py")
    package.__package__ = TEST_NAMESPACE
    package.__path__ = [str(PLUGIN_ROOT)]
    parent.galgame_plugin = package

    # The repository root is itself the plugin package.  A standalone checkout
    # does not have an importable parent package, but pytest still tries to
    # import its __init__.py while setting up tests below it.  Reserve that
    # collection-only name so pytest does not execute the runtime entry outside
    # a host namespace; tests import real modules through TEST_NAMESPACE.
    collection_package = sys.modules.setdefault("__init__", ModuleType("__init__"))
    collection_package.__file__ = str(PLUGIN_ROOT / "__init__.py")


_install_test_namespace()
install_host_import_stubs()
