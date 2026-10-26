"""Restrict official packaging path discovery to an explicit build sandbox."""

import os
from pathlib import Path

try:
    root = Path(os.environ["NEKO_STORAGE_SELECTED_ROOT"])
    sandbox = Path(os.environ["NEKO_PACKAGE_SANDBOX"]).resolve()
    if not root.is_absolute() or root.resolve().parent != sandbox:
        raise RuntimeError("invalid isolated build storage root")

    import utils.config_manager as cm

    cm.ConfigManager._get_documents_directory = lambda self: sandbox
    cm.ConfigManager._get_legacy_storage_candidates = lambda self: []
    cm.ConfigManager._get_legacy_document_candidates = lambda self: []
    cm._ensure_config_manager_migrated = lambda: cm._config_manager
    os.environ["NEKO_ISOLATED_BUILD_ACTIVE"] = "1"
except Exception as exc:
    raise SystemExit(f"Build isolation initialization failed: {exc}") from exc
