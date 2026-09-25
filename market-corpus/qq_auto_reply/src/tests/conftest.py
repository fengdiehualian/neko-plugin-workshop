"""Test bootstrap: put the plugin's ``lib/`` and the N.E.K.O app root first on ``sys.path``.

The plugin's tests import app packages (``plugin``, ``utils``, ``config``, ``main_logic``)
that live at the N.E.K.O root and may resolve vendored deps from ``lib/``. A generic
top-level name like ``utils`` is easy to shadow by an earlier ``sys.path`` entry, so
prepend the app root (and ``lib/``) explicitly — duplicates are harmless, since import
resolution uses the first matching directory.
"""
import sys
from pathlib import Path

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]     # <...>/qq_auto_reply
_LIB = _PLUGIN_ROOT / "lib"

# Scan upward for the N.E.K.O app root (a directory holding the `plugin` and `utils`
# top-level packages). Deployed layout: <root>/plugin/plugins/qq_auto_reply -> <root>.
_APP_ROOT = _PLUGIN_ROOT
for _ancestor in _PLUGIN_ROOT.parents:
    if (_ancestor / "plugin").is_dir() and (_ancestor / "utils").is_dir():
        _APP_ROOT = _ancestor
        break

sys.path.insert(0, str(_APP_ROOT))
sys.path.insert(0, str(_LIB))
