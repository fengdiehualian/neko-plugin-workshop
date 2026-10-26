"""Run the pinned upstream's unmodified CLI without importing the live host.

This is a scaffolding/static-check launcher, NOT a host runtime compatibility test.
Usage: python scripts/upstream_cli.py /path/to/NEKO init|check|build ...
"""
import importlib
import os
import sys
import types
from pathlib import Path

sys.dont_write_bytecode = True
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

root = Path(sys.argv.pop(1)).resolve()
# Upstream plugin.__init__ eagerly starts importing the entire host stack.
# Namespace packages let its CLI use the real source modules without that facade.
for name in ("plugin", "plugin._types", "plugin.core", "plugin.server", "plugin.server.infrastructure"):
    module = types.ModuleType(name)
    module.__path__ = [str(root.joinpath(*name.split('.')))]
    sys.modules[name] = module
sys.path.insert(0, str(root))
raise SystemExit(importlib.import_module('plugin.neko_plugin_cli.cli').main())
