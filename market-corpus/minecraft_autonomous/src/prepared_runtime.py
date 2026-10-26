"""Neko's explicit configuration boundary for the neutral prepared runtime."""

import copy
from pathlib import Path

from agent_mc_autonomy.runtime_bundle import BundleError, apply_runtime_defaults, verify_bundle

from .bundled_appearance import default_bundled_appearance_settings
from .bundled_extension import default_bundled_extension_settings


def resolve_host_runtime(raw):
    """Return effective settings and a cheap installation hint; never save or start."""
    if isinstance(raw.get("runtime"), dict) and raw["runtime"].get("mode") == "external":
        return copy.deepcopy(raw), "external"
    try:
        effective = apply_runtime_defaults(raw)
        runtime = effective.get("runtime", {})
        state = "custom"
        if isinstance(runtime, dict) and runtime.get("bundle_root"):
            verify_bundle(Path(runtime["bundle_root"]), expected_manifest_sha256=runtime.get("bundle_manifest_sha256"), full=False)
            state = "prepared"
    except BundleError as error:
        effective = copy.deepcopy(raw)
        bound = isinstance(raw.get("runtime"), dict) and bool(raw["runtime"].get("bundle_root"))
        state = "missing" if not bound and any(code in str(error) for code in ("metadata_unavailable", "localappdata_unavailable")) else "incompatible"
        # Keep an unprepared full UI draft eligible for later neutral defaults.
        if isinstance(effective.get("backend", {}), dict):
            effective.setdefault("backend", {}).setdefault("node", "")
        if isinstance(effective.get("viewer", {}), dict):
            effective.setdefault("viewer", {}).setdefault("default_appearance", "bundled:yui-ysm")
        return effective, state
    return default_bundled_extension_settings(default_bundled_appearance_settings(effective)), state


def require_prepared_runtime(state):
    if state in {"missing", "incompatible"}:
        raise ValueError("离线运行环境缺失或不匹配。请解压完整离线包，双击 prepare.cmd 后重试；保存设置不会启动游戏。")
