"""Make the plugin's vendored ``lib/`` directory importable.

``lib/`` bundles runtime dependencies that ship with the plugin rather than being
installed system-wide (e.g. the ``utils.connection`` OneBot/QQ connector). Several
``__init__.py`` and test imports resolve from it, so this module must be imported
(for its side effect) before any of those imports run.

The reference in ``__init__.py`` (``_LIB_DIR = _lib_bootstrap.lib_dir``) also keeps
this module import "used" so F401 stays silent under ``--ignore-noqa``.
"""
import sys
from pathlib import Path

lib_dir = Path(__file__).parent / "lib"
if lib_dir.exists() and str(lib_dir) not in sys.path:
    sys.path.insert(0, str(lib_dir))
