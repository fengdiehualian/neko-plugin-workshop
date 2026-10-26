"""SDK-free adapters shared by the delivery and policy layers."""

from .target import (
    MAX_TARGET_LANLAN_CHARS,
    TARGET_ENV_VARS,
    TARGET_LANLAN_ENV,
    TargetResolution,
    clean_target_lanlan,
    resolve_target,
    resolve_target_lanlan,
)

__all__ = [
    "MAX_TARGET_LANLAN_CHARS",
    "TARGET_ENV_VARS",
    "TARGET_LANLAN_ENV",
    "TargetResolution",
    "clean_target_lanlan",
    "resolve_target",
    "resolve_target_lanlan",
]
