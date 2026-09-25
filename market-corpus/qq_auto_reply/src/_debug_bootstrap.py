"""Standalone-script bootstrap: put the repository root on ``sys.path``.

``debug_focus_shift_retro.py`` / ``debug_reply_buffer_summary.py`` import the
``plugin.plugins.qq_auto_reply`` package (and its siblings), which live at the
repository root (``plugin/plugins/qq_auto_reply/`` in the deployed layout).
Importing this module first inserts that root onto ``sys.path`` so those
imports resolve — without leaving ``import`` statements after code in the
calling script, which the strict ruff gate's ``--ignore-noqa`` E402 would reject.

The reference in each script (``_REPO_ROOT = _debug_bootstrap._REPO_ROOT``) also
keeps the module import "used" so F401 stays silent under ``--ignore-noqa``.
"""
import sys
from pathlib import Path

# 仓库根 = 本文件的 4 层父目录：qq_auto_reply → plugins → plugin → 仓库根
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
