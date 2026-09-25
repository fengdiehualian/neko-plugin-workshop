from __future__ import annotations

import os
import sys
from pathlib import Path

PLUGIN_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def _find_neko_repository() -> Path:
    configured = os.environ.get("NEKO_REPO_ROOT", "").strip()
    candidates = []
    if configured:
        candidates.append(Path(configured).expanduser())

    candidates.extend(
        (
            PLUGIN_REPOSITORY_ROOT.parent / "N.E.K.O",
            PLUGIN_REPOSITORY_ROOT.parents[2]
            if len(PLUGIN_REPOSITORY_ROOT.parents) > 2
            else PLUGIN_REPOSITORY_ROOT,
        )
    )
    for candidate in candidates:
        resolved = candidate.resolve()
        if (resolved / "plugin" / "sdk").is_dir():
            return resolved
    raise RuntimeError(
        "N.E.K.O checkout not found. Set NEKO_REPO_ROOT to its repository root."
    )


for import_root in (PLUGIN_REPOSITORY_ROOT, _find_neko_repository()):
    value = str(import_root)
    if value not in sys.path:
        sys.path.insert(0, value)
