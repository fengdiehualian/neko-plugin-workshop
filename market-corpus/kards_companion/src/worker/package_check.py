"""Offline bundle layout resolution and runtime self-check.

Scope (phase 1)
---------------
* Resolve every worker/runtime/model path from the bundle root, using Windows
  absolute paths.  ``PATH`` and the per-user ``site-packages`` are never
  consulted for resolution.
* Read the single authoritative distribution manifest, ``bundle-manifest.json``
  (schema ``neko-kards-bundle-manifest/1``), exactly as produced by
  ``scripts/bundle_manifest.py``.  The worker checks that manifest shape, the
  payload paths it declares, the required markers, the optional
  ``kards_ui_det`` / ``kards_card_cls`` model entries, component metadata, and
  the manifest fingerprint.  The OCR runtime marker and the old ``kards_det``
  declaration were removed with the OCR pipeline.
* Read optional ``worker-runtime.json`` (schema ``kards-worker-runtime/1``) for
  the worker's own vendored-Python / dependency / model declarations.  This
  file is *not* a second copy of the distribution manifest and never replaces
  ``bundle-manifest.json``.  When it is absent, the worker reports the bundle
  manifest component entries only and does not invent dependency states.
* Report a structured, JSON-serializable snapshot of the offline runtime:
  vendored Python root, optional dependency states, and the state of each
  declared model (``kards_ui_det`` / ``kards_card_cls``).
* Never create directories, never download, never import the host SDK, and
  never read user secrets / chat / video / training data.  The only large files
  this module reads are the declared models, and only to verify a checksum
  that the manifest itself declares.

A missing model is reported as ``missing`` and is never turned
into ``ready``.  A dependency that is importable from the host/system Python
but not from the bundle is reported as ``external`` and cannot satisfy the
offline guarantee.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import importlib.util
import json
import os
import platform
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SELFCHECK_SCHEMA = "kards-offline-runtime-check/1"
WORKER_RUNTIME_SCHEMA = "kards-worker-runtime/1"
DISTRIBUTION_MANIFEST_SCHEMA = "neko-kards-bundle-manifest/1"
# Compatibility alias: there is only one manifest schema now.  The old
# ``kards-offline-bundle-manifest/1`` contract is retired.
MANIFEST_SCHEMA = DISTRIBUTION_MANIFEST_SCHEMA

REQUIRES_PYTHON = "3.11"
PLATFORM_TAG = "win_amd64"
CPU_ONLY = True

WORKER_RUNTIME_FILENAME = "worker-runtime.json"
DISTRIBUTION_MANIFEST_FILENAME = "bundle-manifest.json"
ASSETS_MANIFEST_CANDIDATES = ("assets/cards/manifest.json", "assets/manifest.json")
NOTICE_CANDIDATES = (
    "THIRD_PARTY_NOTICES.md",
    "THIRD_PARTY_NOTICES.txt",
    "THIRD_PARTY_NOTICES",
)
BUNDLE_DIRS = ("worker", "vendor", "assets", "models")
REQUIRED_MARKERS = (
    ("python_worker", ("worker/entry.py", "worker/bootstrap.py")),
    ("assets_manifest", ASSETS_MANIFEST_CANDIDATES),
    ("third_party_notices", NOTICE_CANDIDATES),
)
JSON_REQUIRED_MARKERS = frozenset({"assets_manifest"})

# 双 YOLO 模型：kards_ui_det（界面检测）/ kards_card_cls（卡牌分类）。
# 旧 kards_det 声明已删除；模型缺失只降级不阻塞。
MODEL_IDS = {
    "kards_ui_det": "models/ui_det.onnx",
    "kards_card_cls": "models/card_cls.onnx",
}
DEFAULT_MODEL_RELATIVE = MODEL_IDS["kards_ui_det"]

VENDOR_ROOT_RELATIVE = "vendor"
VENDOR_PYTHON_RELATIVE = "vendor/python"
VENDOR_PYTHON_EXE_RELATIVE = "vendor/python/python.exe"
VENDOR_SITE_PACKAGES_RELATIVE = "vendor/python/Lib/site-packages"
PYTHON_DLL_NAME = "python311.dll"

MAX_WORKER_RUNTIME_BYTES = 256 * 1024
MAX_DISTRIBUTION_MANIFEST_BYTES = 8 * 1024 * 1024
MAX_MARKER_BYTES = 4 * 1024 * 1024

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
_IMPORT_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*$")

_TOP_LEVEL_WORKER_RUNTIME_KEYS = frozenset(
    {
        "schema",
        "bundle_id",
        "bundle_version",
        "generated_at",
        "compatibility",
        "runtime",
        "dependencies",
        "models",
        "files",
        "notes",
    }
)
_WORKER_COMPAT_KEYS = frozenset({"requires_python", "platform", "cpu_only"})
_WORKER_RUNTIME_SECTION_KEYS = frozenset(
    {"python_root", "python_exe", "site_packages"}
)
_DEPENDENCY_KEYS = frozenset(
    {"name", "import_names", "required", "version", "dist_info", "sha256", "capability"}
)
_MODEL_KEYS = frozenset(
    {"id", "path", "required", "sha256", "size_bytes", "source", "license"}
)
_RUNTIME_FILE_KEYS = frozenset({"path", "sha256", "size_bytes"})

_DISTRIBUTION_MANIFEST_KEYS = frozenset(
    {
        "schema",
        "generated_at",
        "plugin",
        "allowed_dirs",
        "forbidden_components",
        "files",
        "components",
        "required",
        "optional",
        "capabilities",
        "warnings",
        "integrity",
    }
)
_PLUGIN_KEYS = frozenset({"id", "version"})
_FILE_ENTRY_KEYS = frozenset(
    {"path", "size", "sha256", "component", "version", "license"}
)
_COMPONENT_META_KEYS = frozenset({"version", "license", "file_count", "size_bytes"})
_REQUIRED_STATE_KEYS = frozenset({"expected", "path", "present", "size", "sha256"})
_OPTIONAL_STATE_KEYS = frozenset(
    {"path", "state", "present", "size", "sha256"}
)
_CAPABILITY_KEYS = frozenset(
    {"bundle_ready", "kards_ui_det", "kards_card_cls"}
)
_INTEGRITY_KEYS = frozenset({"file_count", "total_bytes", "sha256"})


class BundlePathError(ValueError):
    """A path inside the bundle was relative, escaping, or otherwise unsafe."""

    def __init__(self, code: str, message: str, *, field: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.field = field


@dataclass(frozen=True)
class BundleLayout:
    bundle_root: Path
    worker_root: Path
    worker_runtime_path: Path
    distribution_manifest_path: Path
    assets_manifest_path: Path
    vendor_root: Path
    python_root: Path
    python_exe: Path
    site_packages: Path
    models_dir: Path
    model_paths: dict
    requirements_path: Path


@dataclass(frozen=True)
class DependencySpec:
    name: str
    import_names: tuple[str, ...]
    required: bool
    capability: str


DEFAULT_DEPENDENCIES: tuple[DependencySpec, ...] = (
    DependencySpec("numpy", ("numpy",), True, "numeric"),
    DependencySpec("PIL", ("PIL",), True, "image"),
    DependencySpec("cv2", ("cv2",), False, "vision"),
    DependencySpec("onnxruntime", ("onnxruntime",), False, "detector"),
)


@dataclass(frozen=True)
class DistributionFile:
    path: str
    size: int
    sha256: str
    component: str
    version: str
    license: str
    resolved: Path


# --------------------------------------------------------------------------- paths


def _norm(path: str | Path) -> str:
    return os.path.normcase(os.path.abspath(str(path)))


def is_within_root(root: str | Path, candidate: str | Path) -> bool:
    """True when ``candidate`` is the root or inside it (Windows case-safe)."""
    try:
        root_norm = _norm(root)
        candidate_norm = _norm(candidate)
        common = os.path.commonpath([root_norm, candidate_norm])
    except (OSError, ValueError):
        return False
    return common == root_norm


def _has_parent_segment(raw: str) -> bool:
    return any(part == ".." for part in raw.replace("\\", "/").split("/"))


def _require_windows_absolute(raw: str, *, field: str = "bundle_root") -> Path:
    if not isinstance(raw, str) or not raw.strip():
        raise BundlePathError("%s_empty" % field, "%s must be a non-empty string" % field, field=field)
    if "\x00" in raw:
        raise BundlePathError("%s_contains_nul" % field, "%s contains a NUL byte" % field, field=field)
    if raw.startswith(("\\\\", "//")):
        raise BundlePathError(
            "%s_not_windows_absolute" % field,
            "%s must be a drive-letter Windows path, not a UNC/device path" % field,
            field=field,
        )
    if _has_parent_segment(raw):
        raise BundlePathError(
            "%s_parent_segment" % field,
            "%s must not contain '..' segments" % field,
            field=field,
        )
    path = Path(raw)
    if not path.is_absolute():
        raise BundlePathError(
            "%s_not_absolute" % field,
            "%s must be an absolute path" % field,
            field=field,
        )
    drive = path.drive
    if os.name == "nt" and (len(drive) != 2 or drive[1] != ":"):
        raise BundlePathError(
            "%s_not_windows_absolute" % field,
            "%s must start with a drive letter such as C:\\" % field,
            field=field,
        )
    return path


def resolve_bundle_root(bundle_root: str | Path | None = None) -> Path:
    """Return an absolute, existing bundle root.

    ``None`` means "the directory that contains this worker package".  An
    explicit root is accepted only when it is an absolute drive-letter path and
    contains no ``..`` segments, so a caller cannot smuggle traversal through
    the CLI.
    """
    if bundle_root is None:
        root = Path(__file__).resolve().parent.parent
    else:
        root = _require_windows_absolute(str(bundle_root))
    try:
        resolved = root.resolve()
    except OSError as exc:
        raise BundlePathError("bundle_root_unresolvable", str(exc), field="bundle_root") from exc
    if not resolved.is_dir():
        raise BundlePathError(
            "bundle_root_missing",
            "bundle root is not an existing directory: %s" % resolved,
            field="bundle_root",
        )
    return resolved


def resolve_member_path(
    root: str | Path,
    relative: str,
    *,
    field: str = "path",
) -> Path:
    """Resolve a bundle-relative path and reject anything that escapes it.

    Rules: relative, forward-slash segments, no drive/colon, no absolute
    prefix, no ``.``/``..``/empty segments, and the fully resolved path must
    stay inside ``root``.  Existing symlinks/junctions are resolved before the
    containment check, so links pointing outside are rejected.
    """
    if not isinstance(relative, str) or not relative:
        raise BundlePathError("unsafe_path", "%s must be a non-empty relative path" % field, field=field)
    if "\x00" in relative:
        raise BundlePathError("unsafe_path", "%s contains a NUL byte" % field, field=field)
    if "\\" in relative:
        raise BundlePathError(
            "unsafe_path",
            "%s must use forward-slash bundle-relative paths" % field,
            field=field,
        )
    if ":" in relative:
        raise BundlePathError(
            "unsafe_path",
            "%s must not contain a drive or alternate-data-stream colon" % field,
            field=field,
        )
    if relative.startswith(("/", "\\")):
        raise BundlePathError(
            "unsafe_path",
            "%s must be relative to the bundle root" % field,
            field=field,
        )
    normalized = relative.replace("\\", "/")
    parts = normalized.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise BundlePathError(
            "unsafe_path",
            "%s must not contain empty, '.' or '..' segments" % field,
            field=field,
        )
    root_path = Path(root).resolve()
    try:
        candidate = (root_path / Path(*parts)).resolve()
    except OSError as exc:
        raise BundlePathError("unsafe_path", "%s cannot be resolved: %s" % (field, exc), field=field) from exc
    if not is_within_root(root_path, candidate):
        raise BundlePathError(
            "unsafe_path",
            "%s escapes the bundle root" % field,
            field=field,
        )
    return candidate


def resolve_layout(bundle_root: str | Path | None = None) -> BundleLayout:
    """Build the fixed bundle layout without creating anything.

    Default members are also containment-checked: a ``vendor``/``models``
    junction or symlink that points outside the bundle is rejected before any
    stat/read follows it.
    """
    root = resolve_bundle_root(bundle_root)

    def member(relative: str, field: str) -> Path:
        return resolve_member_path(root, relative, field=field)

    return BundleLayout(
        bundle_root=root,
        worker_root=member("worker", "worker_root"),
        worker_runtime_path=member(WORKER_RUNTIME_FILENAME, "worker_runtime_path"),
        distribution_manifest_path=member(
            DISTRIBUTION_MANIFEST_FILENAME, "distribution_manifest_path"
        ),
        assets_manifest_path=member(ASSETS_MANIFEST_CANDIDATES[0], "assets.manifest_path"),
        vendor_root=member(VENDOR_ROOT_RELATIVE, "vendor_root"),
        python_root=member(VENDOR_PYTHON_RELATIVE, "runtime.python_root"),
        python_exe=member(VENDOR_PYTHON_EXE_RELATIVE, "runtime.python_exe"),
        site_packages=member(VENDOR_SITE_PACKAGES_RELATIVE, "runtime.site_packages"),
        models_dir=member("models", "models_dir"),
        model_paths={
            model_id: member(relative, "models.%s.path" % model_id)
            for model_id, relative in MODEL_IDS.items()
        },
        requirements_path=member("requirements-bundle.txt", "requirements_bundle"),
    )


# ----------------------------------------------------------------------- manifest


def _issue(
    code: str,
    resource: str,
    message: str,
    *,
    severity: str = "blocker",
    field: str = "",
    affects_readiness: bool = True,
) -> dict[str, Any]:
    item: dict[str, Any] = {
        "code": code,
        "resource": resource,
        "message": message,
        "severity": severity,
        "affects_readiness": bool(affects_readiness),
    }
    if field:
        item["field"] = field
    return item


def _append_issue(target: list[dict[str, Any]], item: dict[str, Any]) -> None:
    key = (item.get("code"), item.get("resource"), item.get("field", ""))
    for existing in target:
        if (existing.get("code"), existing.get("resource"), existing.get("field", "")) == key:
            return
    target.append(item)


def _reject_json_constant(value: str) -> None:
    raise ValueError("non-finite JSON number %s is not allowed" % value)


def _is_notice_path(rel: str) -> bool:
    return rel in NOTICE_CANDIDATES


def _bundle_prefix(rel: str) -> str:
    if _is_notice_path(rel):
        return "notices"
    parts = rel.split("/")
    if not parts or parts[0] not in BUNDLE_DIRS:
        raise BundlePathError(
            "distribution_manifest_unsafe_path",
            "path is outside the allowed bundle payload: %s" % rel,
            field="path",
        )
    return parts[0]


def _validate_component(raw: Any, root: Path, *, field: str) -> str:
    if not isinstance(raw, str) or not raw or raw != raw.strip():
        raise BundlePathError(
            "distribution_manifest_field_invalid",
            "%s must be a non-empty string" % field,
            field=field,
        )
    if raw == "notices" or _is_notice_path(raw):
        return raw
    try:
        resolve_member_path(root, raw, field=field)
    except BundlePathError as exc:
        raise BundlePathError("distribution_manifest_unsafe_path", exc.message, field=field) from exc
    if raw.split("/", 1)[0] not in BUNDLE_DIRS:
        raise BundlePathError(
            "distribution_manifest_field_invalid",
            "%s must name the notices entry or a payload directory" % field,
            field=field,
        )
    return raw


def _component_matches(rel: str, component: str) -> bool:
    if _is_notice_path(rel):
        return component == "notices" or component == rel
    return rel == component or rel.startswith(component + "/")


def _components_from_files(files: list[DistributionFile]) -> dict[str, dict[str, Any]]:
    components: dict[str, dict[str, Any]] = {}
    for item in files:
        entry = components.setdefault(
            item.component,
            {
                "version": item.version,
                "license": item.license,
                "file_count": 0,
                "size_bytes": 0,
            },
        )
        if entry["version"] != item.version or entry["license"] != item.license:
            entry["version"] = "<inconsistent>"
            entry["license"] = "<inconsistent>"
        entry["file_count"] += 1
        entry["size_bytes"] += item.size
    return dict(sorted(components.items()))


def _file_fingerprint(files: list[DistributionFile]) -> str:
    """Same fingerprint recipe as scripts/bundle_manifest.py."""
    digest = hashlib.sha256()
    for item in sorted(files, key=lambda entry: entry.path):
        row = "\0".join(
            (
                item.path,
                str(item.size),
                item.sha256,
                item.component,
                item.version,
                item.license,
            )
        )
        digest.update(row.encode("utf-8") + b"\n")
    return digest.hexdigest()


def _validate_distribution_manifest(
    data: dict[str, Any],
    root: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Validate the scripts-produced manifest shape without trusting it."""
    issues: list[dict[str, Any]] = []
    view: dict[str, Any] = {
        "components": {},
        "required": {},
        "optional": {},
        "files": [],
        "warnings": [],
        "integrity": {},
    }
    if data.get("schema") != DISTRIBUTION_MANIFEST_SCHEMA:
        _append_issue(
            issues,
            _issue(
                "distribution_manifest_schema_mismatch",
                "distribution_manifest",
                "bundle-manifest.json schema must be %s" % DISTRIBUTION_MANIFEST_SCHEMA,
                field="schema",
            ),
        )
        return view, issues

    extra = sorted(set(data) - _DISTRIBUTION_MANIFEST_KEYS)
    if extra:
        _append_issue(
            issues,
            _issue(
                "distribution_manifest_field_invalid",
                "distribution_manifest",
                "unknown top-level fields: %s" % ", ".join(extra),
            ),
        )

    if not isinstance(data.get("generated_at"), str) or not data.get("generated_at"):
        _append_issue(
            issues,
            _issue(
                "distribution_manifest_field_invalid",
                "distribution_manifest",
                "generated_at must be a non-empty string",
                field="generated_at",
            ),
        )

    plugin = data.get("plugin")
    if not isinstance(plugin, dict):
        _append_issue(
            issues,
            _issue(
                "distribution_manifest_field_invalid",
                "distribution_manifest",
                "plugin must be an object",
                field="plugin",
            ),
        )
    else:
        plugin_extra = sorted(set(plugin) - _PLUGIN_KEYS)
        if plugin_extra:
            _append_issue(
                issues,
                _issue(
                    "distribution_manifest_field_invalid",
                    "distribution_manifest",
                    "unknown plugin fields: %s" % ", ".join(plugin_extra),
                    field="plugin",
                ),
            )
        plugin_id = plugin.get("id")
        if plugin_id is not None and (
            not isinstance(plugin_id, str) or not plugin_id or not _SAFE_NAME_RE.match(plugin_id)
        ):
            _append_issue(
                issues,
                _issue(
                    "distribution_manifest_field_invalid",
                    "distribution_manifest",
                    "plugin.id is invalid",
                    field="plugin.id",
                ),
            )
        if not isinstance(plugin.get("version"), str) or not plugin.get("version"):
            _append_issue(
                issues,
                _issue(
                    "distribution_manifest_field_invalid",
                    "distribution_manifest",
                    "plugin.version must be a non-empty string",
                    field="plugin.version",
                ),
            )

    allowed_dirs = data.get("allowed_dirs")
    if not isinstance(allowed_dirs, list) or not allowed_dirs:
        _append_issue(
            issues,
            _issue(
                "distribution_manifest_field_invalid",
                "distribution_manifest",
                "allowed_dirs must be a non-empty array",
                field="allowed_dirs",
            ),
        )
    else:
        seen_dirs: set[str] = set()
        for index, value in enumerate(allowed_dirs):
            if not isinstance(value, str) or value not in BUNDLE_DIRS:
                _append_issue(
                    issues,
                    _issue(
                        "distribution_manifest_field_invalid",
                        "distribution_manifest",
                        "allowed_dirs[%d] is not a known payload directory" % index,
                        field="allowed_dirs[%d]" % index,
                    ),
                )
                continue
            if value in seen_dirs:
                _append_issue(
                    issues,
                    _issue(
                        "distribution_manifest_field_invalid",
                        "distribution_manifest",
                        "duplicate allowed directory %r" % value,
                        field="allowed_dirs[%d]" % index,
                    ),
                )
            seen_dirs.add(value)

    forbidden = data.get("forbidden_components")
    if forbidden is not None and (
        not isinstance(forbidden, list)
        or not all(isinstance(item, str) for item in forbidden)
    ):
        _append_issue(
            issues,
            _issue(
                "distribution_manifest_field_invalid",
                "distribution_manifest",
                "forbidden_components must be an array of strings",
                field="forbidden_components",
            ),
        )

    files = data.get("files")
    entries: list[DistributionFile] = []
    by_path: dict[str, DistributionFile] = {}
    if not isinstance(files, list):
        _append_issue(
            issues,
            _issue(
                "distribution_manifest_field_invalid",
                "distribution_manifest",
                "files must be an array",
                field="files",
            ),
        )
    else:
        for index, item in enumerate(files):
            field = "files[%d]" % index
            if not isinstance(item, dict):
                _append_issue(
                    issues,
                    _issue(
                        "distribution_manifest_field_invalid",
                        "distribution_manifest",
                        "%s must be an object" % field,
                        field=field,
                    ),
                )
                continue
            entry_extra = sorted(set(item) - _FILE_ENTRY_KEYS)
            if entry_extra:
                _append_issue(
                    issues,
                    _issue(
                        "distribution_manifest_field_invalid",
                        "distribution_manifest",
                        "%s has unknown fields: %s" % (field, ", ".join(entry_extra)),
                        field=field,
                    ),
                )
            raw_path = item.get("path")
            if not isinstance(raw_path, str):
                _append_issue(
                    issues,
                    _issue(
                        "distribution_manifest_field_invalid",
                        "distribution_manifest",
                        "%s.path must be a string" % field,
                        field=field + ".path",
                    ),
                )
                continue
            try:
                resolved = resolve_member_path(root, raw_path, field=field + ".path")
                _bundle_prefix(raw_path)
            except BundlePathError as exc:
                _append_issue(
                    issues,
                    _issue(
                        "distribution_manifest_unsafe_path",
                        "distribution_manifest",
                        exc.message,
                        field=field + ".path",
                    ),
                )
                continue
            component = ""
            try:
                component = _validate_component(
                    item.get("component"),
                    root,
                    field=field + ".component",
                )
            except BundlePathError as exc:
                _append_issue(
                    issues,
                    _issue(exc.code, "distribution_manifest", exc.message, field=field + ".component"),
                )
            if component and not _component_matches(raw_path, component):
                _append_issue(
                    issues,
                    _issue(
                        "distribution_manifest_field_invalid",
                        "distribution_manifest",
                        "%s.component does not prefix its path" % field,
                        field=field + ".component",
                    ),
                )
            size = item.get("size")
            if isinstance(size, bool) or not isinstance(size, int) or size < 0:
                _append_issue(
                    issues,
                    _issue(
                        "distribution_manifest_field_invalid",
                        "distribution_manifest",
                        "%s.size must be a non-negative integer" % field,
                        field=field + ".size",
                    ),
                )
                continue
            digest = item.get("sha256")
            if not isinstance(digest, str) or not _SHA256_RE.match(digest):
                _append_issue(
                    issues,
                    _issue(
                        "distribution_manifest_field_invalid",
                        "distribution_manifest",
                        "%s.sha256 must be 64 lowercase hex chars" % field,
                        field=field + ".sha256",
                    ),
                )
                continue
            version = item.get("version")
            license_value = item.get("license")
            if not isinstance(version, str) or not version:
                _append_issue(
                    issues,
                    _issue(
                        "distribution_manifest_field_invalid",
                        "distribution_manifest",
                        "%s.version must be a non-empty string" % field,
                        field=field + ".version",
                    ),
                )
                continue
            if not isinstance(license_value, str) or not license_value:
                _append_issue(
                    issues,
                    _issue(
                        "distribution_manifest_field_invalid",
                        "distribution_manifest",
                        "%s.license must be a non-empty string" % field,
                        field=field + ".license",
                    ),
                )
                continue
            if raw_path in by_path:
                _append_issue(
                    issues,
                    _issue(
                        "distribution_manifest_field_invalid",
                        "distribution_manifest",
                        "duplicate file path %r" % raw_path,
                        field=field + ".path",
                    ),
                )
                continue
            entry = DistributionFile(
                raw_path,
                size,
                digest,
                component or _bundle_prefix(raw_path),
                version,
                license_value,
                resolved,
            )
            entries.append(entry)
            by_path[entry.path] = entry

    computed_components = _components_from_files(entries)
    declared_components = data.get("components")
    if not isinstance(declared_components, dict):
        _append_issue(
            issues,
            _issue(
                "distribution_manifest_field_invalid",
                "distribution_manifest",
                "components must be an object",
                field="components",
            ),
        )
    else:
        for key, value in declared_components.items():
            field = "components.%s" % key if isinstance(key, str) else "components"
            if not isinstance(key, str):
                _append_issue(
                    issues,
                    _issue(
                        "distribution_manifest_field_invalid",
                        "distribution_manifest",
                        "component key must be a string",
                        field="components",
                    ),
                )
                continue
            try:
                _validate_component(key, root, field=field)
            except BundlePathError as exc:
                _append_issue(
                    issues,
                    _issue(exc.code, "distribution_manifest", exc.message, field=field),
                )
                continue
            if not isinstance(value, dict):
                _append_issue(
                    issues,
                    _issue(
                        "distribution_manifest_field_invalid",
                        "distribution_manifest",
                        "%s must be an object" % field,
                        field=field,
                    ),
                )
                continue
            meta_extra = sorted(set(value) - _COMPONENT_META_KEYS)
            if meta_extra:
                _append_issue(
                    issues,
                    _issue(
                        "distribution_manifest_field_invalid",
                        "distribution_manifest",
                        "%s has unknown fields: %s" % (field, ", ".join(meta_extra)),
                        field=field,
                    ),
                )
            for key_name in ("version", "license"):
                if not isinstance(value.get(key_name), str) or not value.get(key_name):
                    _append_issue(
                        issues,
                        _issue(
                            "distribution_manifest_field_invalid",
                            "distribution_manifest",
                            "%s.%s must be a non-empty string" % (field, key_name),
                            field=field + "." + key_name,
                        ),
                    )
            for key_name in ("file_count", "size_bytes"):
                number = value.get(key_name)
                if isinstance(number, bool) or not isinstance(number, int) or number < 0:
                    _append_issue(
                        issues,
                        _issue(
                            "distribution_manifest_field_invalid",
                            "distribution_manifest",
                            "%s.%s must be a non-negative integer" % (field, key_name),
                            field=field + "." + key_name,
                        ),
                    )
        if declared_components != computed_components:
            _append_issue(
                issues,
                _issue(
                    "distribution_manifest_field_invalid",
                    "distribution_manifest",
                    "components do not match the files array",
                    field="components",
                ),
            )

    required_data = data.get("required")
    required_view: dict[str, dict[str, Any]] = {}
    if not isinstance(required_data, dict):
        _append_issue(
            issues,
            _issue(
                "distribution_manifest_field_invalid",
                "distribution_manifest",
                "required must be an object",
                field="required",
            ),
        )
    else:
        expected_names = {name for name, _ in REQUIRED_MARKERS}
        unknown_required = sorted(set(required_data) - expected_names)
        if unknown_required:
            _append_issue(
                issues,
                _issue(
                    "distribution_manifest_field_invalid",
                    "distribution_manifest",
                    "unknown required markers: %s" % ", ".join(unknown_required),
                    field="required",
                ),
            )
        for name, alternatives in REQUIRED_MARKERS:
            field = "required.%s" % name
            state: dict[str, Any] = {
                "expected": list(alternatives),
                "path": None,
                "present": False,
            }
            raw_state = required_data.get(name)
            if not isinstance(raw_state, dict):
                _append_issue(
                    issues,
                    _issue(
                        "distribution_required_missing",
                        "distribution_manifest",
                        "required marker %r is not declared" % name,
                        field=field,
                    ),
                )
                required_view[name] = state
                continue
            state_extra = sorted(set(raw_state) - _REQUIRED_STATE_KEYS)
            if state_extra:
                _append_issue(
                    issues,
                    _issue(
                        "distribution_manifest_field_invalid",
                        "distribution_manifest",
                        "%s has unknown fields: %s" % (field, ", ".join(state_extra)),
                        field=field,
                    ),
                )
            if raw_state.get("expected") != list(alternatives):
                _append_issue(
                    issues,
                    _issue(
                        "distribution_manifest_field_invalid",
                        "distribution_manifest",
                        "%s.expected must equal %r" % (field, list(alternatives)),
                        field=field + ".expected",
                    ),
                )
            path_value = raw_state.get("path")
            present = raw_state.get("present")
            if present is not True or not isinstance(path_value, str) or path_value not in alternatives:
                _append_issue(
                    issues,
                    _issue(
                        "distribution_required_missing",
                        "distribution_manifest",
                        "required marker %r is not present" % name,
                        field=field + ".path",
                    ),
                )
                required_view[name] = state
                continue
            try:
                resolve_member_path(root, path_value, field=field + ".path")
            except BundlePathError as exc:
                _append_issue(
                    issues,
                    _issue(
                        "distribution_manifest_unsafe_path",
                        "distribution_manifest",
                        exc.message,
                        field=field + ".path",
                    ),
                )
                required_view[name] = state
                continue
            entry = by_path.get(path_value)
            if entry is None:
                _append_issue(
                    issues,
                    _issue(
                        "distribution_required_missing",
                        "distribution_manifest",
                        "required marker %r is not listed in files" % name,
                        field=field + ".path",
                    ),
                )
                required_view[name] = state
                continue
            if raw_state.get("size") != entry.size or raw_state.get("sha256") != entry.sha256:
                _append_issue(
                    issues,
                    _issue(
                        "distribution_required_invalid",
                        "distribution_manifest",
                        "%s size/sha256 do not match the file entry" % field,
                        field=field,
                    ),
                )
            state.update(
                {
                    "path": path_value,
                    "present": True,
                    "size": entry.size,
                    "sha256": entry.sha256,
                }
            )
            required_view[name] = state

    optional_data = data.get("optional")
    optional_view: dict[str, dict[str, Any]] = {}
    if not isinstance(optional_data, dict):
        _append_issue(
            issues,
            _issue(
                "distribution_manifest_field_invalid",
                "distribution_manifest",
                "optional must be an object",
                field="optional",
            ),
        )
    else:
        unknown_optional = sorted(set(optional_data) - set(MODEL_IDS))
        if unknown_optional:
            _append_issue(
                issues,
                _issue(
                    "distribution_manifest_field_invalid",
                    "distribution_manifest",
                    "unknown optional entries: %s" % ", ".join(unknown_optional),
                    field="optional",
                ),
            )
        for model_id, model_relative in MODEL_IDS.items():
            field = "optional.%s" % model_id
            raw_optional = optional_data.get(model_id)
            if not isinstance(raw_optional, dict):
                _append_issue(
                    issues,
                    _issue(
                        "distribution_manifest_field_invalid",
                        "distribution_manifest",
                        "%s must be an object" % field,
                        field=field,
                    ),
                )
                continue
            optional_extra = sorted(set(raw_optional) - _OPTIONAL_STATE_KEYS)
            if optional_extra:
                _append_issue(
                    issues,
                    _issue(
                        "distribution_manifest_field_invalid",
                        "distribution_manifest",
                        "%s has unknown fields: %s" % (field, ", ".join(optional_extra)),
                        field=field,
                    ),
                )
            path_value = raw_optional.get("path")
            if path_value != model_relative:
                _append_issue(
                    issues,
                    _issue(
                        "distribution_manifest_field_invalid",
                        "distribution_manifest",
                        "%s.path must be %r" % (field, model_relative),
                        field=field + ".path",
                    ),
                )
            else:
                try:
                    resolve_member_path(root, path_value, field=field + ".path")
                except BundlePathError as exc:
                    _append_issue(
                        issues,
                        _issue(
                            "distribution_manifest_unsafe_path",
                            "model:%s" % model_id,
                            exc.message,
                            field=field + ".path",
                        ),
                    )
            present = raw_optional.get("present")
            if present is True:
                entry = by_path.get(model_relative)
                if entry is None:
                    _append_issue(
                        issues,
                        _issue(
                            "distribution_manifest_field_invalid",
                            "distribution_manifest",
                            "%s is present but not listed in files" % field,
                            field=field,
                        ),
                    )
                elif (
                    raw_optional.get("size") != entry.size
                    or raw_optional.get("sha256") != entry.sha256
                    or raw_optional.get("state") != "present"
                ):
                    _append_issue(
                        issues,
                        _issue(
                            "distribution_manifest_field_invalid",
                            "distribution_manifest",
                            "%s size/sha256/state do not match the file entry" % field,
                            field=field,
                        ),
                    )
                else:
                    optional_view[model_id] = {
                        "path": model_relative,
                        "state": "present",
                        "present": True,
                        "size": entry.size,
                        "sha256": entry.sha256,
                    }
            elif present is False and raw_optional.get("state") == "missing":
                optional_view[model_id] = {
                    "path": model_relative,
                    "state": "missing",
                    "present": False,
                }
            else:
                _append_issue(
                    issues,
                    _issue(
                        "distribution_manifest_field_invalid",
                        "distribution_manifest",
                        "%s.present/state is invalid" % field,
                        field=field,
                    ),
                )

    capabilities = data.get("capabilities")
    if not isinstance(capabilities, dict):
        _append_issue(
            issues,
            _issue(
                "distribution_manifest_field_invalid",
                "distribution_manifest",
                "capabilities must be an object",
                field="capabilities",
            ),
        )
    else:
        extra_caps = sorted(set(capabilities) - _CAPABILITY_KEYS)
        if extra_caps:
            _append_issue(
                issues,
                _issue(
                    "distribution_manifest_field_invalid",
                    "distribution_manifest",
                    "unknown capabilities: %s" % ", ".join(extra_caps),
                    field="capabilities",
                ),
            )
        if not isinstance(capabilities.get("bundle_ready"), bool):
            _append_issue(
                issues,
                _issue(
                    "distribution_manifest_field_invalid",
                    "distribution_manifest",
                    "capabilities.bundle_ready must be boolean",
                    field="capabilities.bundle_ready",
                ),
            )
        for key_name in MODEL_IDS:
            if not isinstance(capabilities.get(key_name), str) or not capabilities.get(key_name):
                _append_issue(
                    issues,
                    _issue(
                        "distribution_manifest_field_invalid",
                        "distribution_manifest",
                        "capabilities.%s must be a non-empty string" % key_name,
                        field="capabilities.%s" % key_name,
                    ),
                )

    manifest_warnings = data.get("warnings")
    if manifest_warnings is not None and (
        not isinstance(manifest_warnings, list)
        or not all(isinstance(item, str) for item in manifest_warnings)
    ):
        _append_issue(
            issues,
            _issue(
                "distribution_manifest_field_invalid",
                "distribution_manifest",
                "warnings must be an array of strings",
                field="warnings",
            ),
        )
        manifest_warnings = []

    integrity = data.get("integrity")
    if not isinstance(integrity, dict):
        _append_issue(
            issues,
            _issue(
                "distribution_manifest_field_invalid",
                "distribution_manifest",
                "integrity must be an object",
                field="integrity",
            ),
        )
    else:
        integrity_extra = sorted(set(integrity) - _INTEGRITY_KEYS)
        if integrity_extra:
            _append_issue(
                issues,
                _issue(
                    "distribution_manifest_field_invalid",
                    "distribution_manifest",
                    "integrity has unknown fields: %s" % ", ".join(integrity_extra),
                    field="integrity",
                ),
            )
        file_count = integrity.get("file_count")
        total_bytes = integrity.get("total_bytes")
        digest = integrity.get("sha256")
        if isinstance(file_count, bool) or not isinstance(file_count, int) or file_count < 0:
            _append_issue(
                issues,
                _issue(
                    "distribution_manifest_field_invalid",
                    "distribution_manifest",
                    "integrity.file_count must be a non-negative integer",
                    field="integrity.file_count",
                ),
            )
        elif file_count != len(entries):
            _append_issue(
                issues,
                _issue(
                    "distribution_manifest_integrity_mismatch",
                    "distribution_manifest",
                    "integrity.file_count does not match files",
                    field="integrity.file_count",
                ),
            )
        if isinstance(total_bytes, bool) or not isinstance(total_bytes, int) or total_bytes < 0:
            _append_issue(
                issues,
                _issue(
                    "distribution_manifest_field_invalid",
                    "distribution_manifest",
                    "integrity.total_bytes must be a non-negative integer",
                    field="integrity.total_bytes",
                ),
            )
        elif total_bytes != sum(entry.size for entry in entries):
            _append_issue(
                issues,
                _issue(
                    "distribution_manifest_integrity_mismatch",
                    "distribution_manifest",
                    "integrity.total_bytes does not match files",
                    field="integrity.total_bytes",
                ),
            )
        if not isinstance(digest, str) or not _SHA256_RE.match(digest):
            _append_issue(
                issues,
                _issue(
                    "distribution_manifest_field_invalid",
                    "distribution_manifest",
                    "integrity.sha256 must be 64 lowercase hex chars",
                    field="integrity.sha256",
                ),
            )
        elif digest != _file_fingerprint(entries):
            _append_issue(
                issues,
                _issue(
                    "distribution_manifest_integrity_mismatch",
                    "distribution_manifest",
                    "integrity.sha256 does not match the file entries",
                    field="integrity.sha256",
                ),
            )

    view.update(
        {
            "components": computed_components,
            "required": required_view,
            "optional": optional_view,
            "files": entries,
            "warnings": list(manifest_warnings) if isinstance(manifest_warnings, list) else [],
            "integrity": integrity if isinstance(integrity, dict) else {},
        }
    )
    return view, issues


def _read_worker_runtime(layout: BundleLayout) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    path = layout.worker_runtime_path
    info: dict[str, Any] = {
        "path": str(path),
        "present": False,
        "state": "absent",
        "schema": None,
        "bundle_id": None,
        "bundle_version": None,
        "errors": [],
    }
    try:
        present = path.exists()
    except OSError as exc:
        info["state"] = "invalid"
        info["errors"].append(
            _issue("worker_runtime_unreadable", "worker_runtime", "worker-runtime.json stat failed: %s" % exc)
        )
        return None, info
    if not present:
        return None, info
    info["present"] = True
    if not path.is_file():
        info["state"] = "invalid"
        info["errors"].append(
            _issue("worker_runtime_invalid", "worker_runtime", "worker-runtime.json is not a regular file")
        )
        return None, info
    try:
        size = path.stat().st_size
        if size > MAX_WORKER_RUNTIME_BYTES:
            info["state"] = "invalid"
            info["errors"].append(
                _issue(
                    "worker_runtime_too_large",
                    "worker_runtime",
                    "worker-runtime.json is %d bytes; limit is %d" % (size, MAX_WORKER_RUNTIME_BYTES),
                )
            )
            return None, info
        text = path.read_text(encoding="utf-8")
        data = json.loads(text, parse_constant=_reject_json_constant)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError) as exc:
        info["state"] = "invalid"
        info["errors"].append(
            _issue(
                "worker_runtime_invalid_json",
                "worker_runtime",
                "worker-runtime.json cannot be parsed: %s" % exc,
            )
        )
        return None, info
    if not isinstance(data, dict):
        info["state"] = "invalid"
        info["errors"].append(
            _issue("worker_runtime_invalid", "worker_runtime", "worker-runtime.json root must be a JSON object")
        )
        return None, info

    info["schema"] = data.get("schema") if isinstance(data.get("schema"), str) else None
    info["bundle_id"] = data.get("bundle_id") if isinstance(data.get("bundle_id"), str) else None
    info["bundle_version"] = (
        data.get("bundle_version") if isinstance(data.get("bundle_version"), str) else None
    )

    errors = _validate_worker_runtime(data, layout.bundle_root)
    info["errors"] = errors
    info["state"] = "invalid" if errors else "loaded"
    return data if not errors else None, info


def _read_distribution_json(
    path: Path,
    *,
    max_bytes: int,
    code_prefix: str,
    resource: str,
    blockers: list[dict[str, Any]],
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Read a required distribution-side JSON marker (assets/OCR)."""
    info: dict[str, Any] = {
        "path": str(path),
        "present": False,
        "state": "missing",
        "schema": None,
        "errors": [],
    }

    def fail(code: str, message: str, *, state: str = "invalid") -> None:
        info["state"] = state
        issue = _issue(code, resource, message)
        info["errors"].append(issue)
        _append_issue(blockers, issue)

    try:
        present = path.exists()
    except OSError as exc:
        fail("%s_unreadable" % code_prefix, "stat failed: %s" % exc)
        return None, info
    if not present:
        fail("%s_missing" % code_prefix, "%s is missing" % path.name, state="missing")
        return None, info
    info["present"] = True
    if not path.is_file():
        fail("%s_invalid" % code_prefix, "%s is not a regular file" % path.name)
        return None, info
    try:
        size = path.stat().st_size
        if size <= 0:
            fail("%s_empty" % code_prefix, "%s is empty" % path.name)
            return None, info
        if size > max_bytes:
            fail(
                "%s_too_large" % code_prefix,
                "%s is %d bytes; limit is %d" % (path.name, size, max_bytes),
            )
            return None, info
        data = json.loads(
            path.read_text(encoding="utf-8"),
            parse_constant=_reject_json_constant,
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError) as exc:
        fail("%s_invalid_json" % code_prefix, "%s cannot be parsed: %s" % (path.name, exc))
        return None, info
    if not isinstance(data, dict):
        fail("%s_invalid" % code_prefix, "%s root must be a JSON object" % path.name)
        return None, info
    if isinstance(data.get("schema"), str):
        info["schema"] = data["schema"]
    info["state"] = "loaded"
    return data, info


def _first_existing_candidate(
    root: Path,
    candidates: tuple[str, ...],
    *,
    field: str,
) -> tuple[str, Path | None, str | None]:
    """Return the first safe candidate, preferring an existing file.

    The third element is the last path-safety error, so callers can fail
    closed instead of stat'ing a path that escaped the bundle root.
    """
    first_safe: tuple[str, Path] | None = None
    last_error: str | None = None
    for relative in candidates:
        try:
            path = resolve_member_path(root, relative, field=field)
        except BundlePathError as exc:
            last_error = exc.message
            continue
        if first_safe is None:
            first_safe = (relative, path)
        if path.is_file():
            return relative, path, None
    if first_safe is not None:
        return first_safe[0], first_safe[1], None
    return candidates[0], None, last_error


def _check_distribution_files(
    view: dict[str, Any],
    blockers: list[dict[str, Any]],
    warnings: list[dict[str, Any]],
) -> None:
    for entry in view.get("files", []):
        try:
            exists = entry.resolved.is_file()
        except OSError as exc:
            _append_issue(
                blockers,
                _issue(
                    "distribution_file_unreadable",
                    "distribution_manifest",
                    "declared file cannot be stat'ed: %s" % exc,
                    field="files.%s" % entry.path,
                ),
            )
            continue
        if not exists:
            if entry.path in set(MODEL_IDS.values()):
                _append_issue(
                    warnings,
                    _issue(
                        "model_missing",
                        "model:%s" % entry.path,
                        "declared optional model is missing: %s" % entry.path,
                        severity="warning",
                        affects_readiness=True,
                    ),
                )
            else:
                _append_issue(
                    blockers,
                    _issue(
                        "distribution_file_missing",
                        "distribution_manifest",
                        "declared bundle file is missing: %s" % entry.path,
                        field="files.%s" % entry.path,
                    ),
                )
            continue
        try:
            actual_size = entry.resolved.stat().st_size
        except OSError as exc:
            _append_issue(
                blockers,
                _issue(
                    "distribution_file_unreadable",
                    "distribution_manifest",
                    "declared file cannot be sized: %s" % exc,
                    field="files.%s" % entry.path,
                ),
            )
            continue
        if actual_size != entry.size:
            _append_issue(
                blockers,
                _issue(
                    "distribution_file_size_mismatch",
                    "distribution_manifest",
                    "declared file size %d != manifest %d for %s"
                    % (actual_size, entry.size, entry.path),
                    field="files.%s.size" % entry.path,
                ),
            )


def _check_distribution_required_files(
    view: dict[str, Any],
    blockers: list[dict[str, Any]],
) -> None:
    by_path = {entry.path: entry for entry in view.get("files", [])}
    for name, state in view.get("required", {}).items():
        if state.get("present") is not True:
            continue
        path_value = state.get("path")
        if not isinstance(path_value, str):
            continue
        entry = by_path.get(path_value)
        if entry is None:
            continue
        try:
            if not entry.resolved.is_file():
                _append_issue(
                    blockers,
                    _issue(
                        "distribution_required_missing",
                        "distribution_manifest",
                        "required component %r file is missing: %s" % (name, path_value),
                        field="required.%s.path" % name,
                    ),
                )
                continue
            digest = _sha256_file(entry.resolved)
        except OSError as exc:
            _append_issue(
                blockers,
                _issue(
                    "distribution_required_unreadable",
                    "distribution_manifest",
                    "required component %r cannot be read: %s" % (name, exc),
                    field="required.%s.path" % name,
                ),
            )
            continue
        if digest != entry.sha256:
            _append_issue(
                blockers,
                _issue(
                    "distribution_required_checksum_mismatch",
                    "distribution_manifest",
                    "required component %r sha256 does not match the manifest" % name,
                    field="required.%s.sha256" % name,
                ),
            )


def _check_distribution(
    layout: BundleLayout,
    blockers: list[dict[str, Any]],
    warnings: list[dict[str, Any]],
) -> dict[str, Any]:
    """Check distribution readiness without writing bundle-manifest.json.

    ``bundle-manifest.json`` is owned by ``scripts/bundle_manifest.py`` and its
    schema is ``neko-kards-bundle-manifest/1``.  This worker only reads it.  A
    missing/invalid distribution manifest, missing vendor payload, missing
    OCR runtime marker, or missing assets manifest keeps the self-check
    blocked.  File hashing for every payload file remains the distribution
    tool's job; required markers and the optional model are verified here.
    """
    distribution: dict[str, Any] = {
        "manifest": {
            "path": str(layout.distribution_manifest_path),
            "present": False,
            "state": "missing",
            "schema": None,
            "errors": [],
        },
        "components": {},
        "required": {},
        "optional": {},
        "assets_manifest": {
            "path": str(layout.assets_manifest_path),
            "present": False,
            "state": "missing",
            "errors": [],
        },
        "vendor_root": {
            "path": str(layout.vendor_root),
            "present": False,
            "state": "missing",
        },
        "third_party_notices": {
            "path": None,
            "present": False,
            "state": "missing",
            "pending_template": False,
        },
    }

    vendor_present = layout.vendor_root.is_dir()
    distribution["vendor_root"] = {
        "path": str(layout.vendor_root),
        "present": vendor_present,
        "state": "present" if vendor_present else "missing",
    }
    if not vendor_present:
        _append_issue(
            blockers,
            _issue("vendor_missing", "vendor", "vendor/ is missing: %s" % layout.vendor_root),
        )

    manifest_data, manifest_info = _read_distribution_json(
        layout.distribution_manifest_path,
        max_bytes=MAX_DISTRIBUTION_MANIFEST_BYTES,
        code_prefix="distribution_manifest",
        resource="distribution_manifest",
        blockers=blockers,
    )
    distribution["manifest"] = manifest_info
    if manifest_data is not None:
        view, issues = _validate_distribution_manifest(manifest_data, layout.bundle_root)
        for issue in issues:
            _append_issue(blockers, issue)
            manifest_info["errors"].append(issue)
        if issues:
            manifest_info["state"] = "invalid"
        else:
            manifest_info["state"] = "loaded"
        distribution["components"] = {
            key: dict(value) for key, value in view["components"].items()
        }
        distribution["required"] = view["required"]
        distribution["optional"] = view["optional"]
        _check_distribution_files(view, blockers, warnings)
        _check_distribution_required_files(view, blockers)

    assets_relative, assets_path, assets_error = _first_existing_candidate(
        layout.bundle_root,
        ASSETS_MANIFEST_CANDIDATES,
        field="assets.manifest_path",
    )
    if assets_path is None:
        issue = _issue(
            "assets_manifest_unsafe_path",
            "assets_manifest",
            "no safe assets manifest candidate: %s" % (assets_error or "unknown"),
        )
        assets_info = {
            "path": str(layout.bundle_root / assets_relative),
            "relative_path": assets_relative,
            "present": False,
            "state": "invalid",
            "schema": None,
            "errors": [issue],
        }
        _append_issue(blockers, issue)
    else:
        _, assets_info = _read_distribution_json(
            assets_path,
            max_bytes=MAX_MARKER_BYTES,
            code_prefix="assets_manifest",
            resource="assets_manifest",
            blockers=blockers,
        )
        assets_info["relative_path"] = assets_relative
    distribution["assets_manifest"] = assets_info

    notices_path: Path | None = None
    notices_state = "missing"
    pending_template = False
    for name in NOTICE_CANDIDATES:
        try:
            candidate = resolve_member_path(
                layout.bundle_root, name, field="third_party_notices"
            )
        except BundlePathError as exc:
            _append_issue(
                blockers,
                _issue("third_party_notices_unsafe_path", "third_party_notices", exc.message),
            )
            continue
        if candidate.is_file():
            notices_path = candidate
            notices_state = "present"
            break
    if notices_path is None:
        _append_issue(
            blockers,
            _issue(
                "third_party_notices_missing",
                "third_party_notices",
                "THIRD_PARTY_NOTICES(.md/.txt) is missing",
            ),
        )
    else:
        try:
            prefix = notices_path.read_text(encoding="utf-8", errors="replace")[:8192]
        except OSError:
            prefix = ""
        if "BLOCKED_PENDING_BUILD" in prefix:
            pending_template = True
            _append_issue(
                warnings,
                _issue(
                    "third_party_notices_pending",
                    "third_party_notices",
                    "third-party notices are still a BLOCKED_PENDING_BUILD template",
                    severity="warning",
                    affects_readiness=True,
                ),
            )
    distribution["third_party_notices"] = {
        "path": None if notices_path is None else str(notices_path),
        "present": notices_path is not None,
        "state": notices_state,
        "pending_template": pending_template,
    }
    return distribution


def _validate_relative_field(
    raw: Any,
    root: Path,
    *,
    resource: str,
    field: str,
    issues: list[dict[str, Any]],
) -> None:
    if not isinstance(raw, str):
        _append_issue(issues, _issue("worker_runtime_field_invalid", resource, "%s must be a string" % field, field=field))
        return
    try:
        resolve_member_path(root, raw, field=field)
    except BundlePathError as exc:
        _append_issue(
            issues,
            _issue("unsafe_path", resource, exc.message, field=field),
        )


def _validate_worker_runtime(data: dict[str, Any], root: Path) -> list[dict[str, Any]]:
    """Small, dependency-free validator for the fields the worker relies on."""
    issues: list[dict[str, Any]] = []

    extra = sorted(set(data) - _TOP_LEVEL_WORKER_RUNTIME_KEYS)
    if extra:
        _append_issue(
            issues,
            _issue(
                "worker_runtime_field_invalid",
                "worker_runtime",
                "unknown top-level fields: %s" % ", ".join(extra),
            ),
        )

    if data.get("schema") != WORKER_RUNTIME_SCHEMA:
        _append_issue(
            issues,
            _issue(
                "worker_runtime_schema_mismatch",
                "worker_runtime",
                "schema must be %s" % WORKER_RUNTIME_SCHEMA,
                field="schema",
            ),
        )
    for key in ("bundle_id", "bundle_version", "generated_at"):
        if not isinstance(data.get(key), str) or not data.get(key):
            _append_issue(
                issues,
                _issue("worker_runtime_field_invalid", "worker_runtime", "%s must be a non-empty string" % key, field=key),
            )
    bundle_id = data.get("bundle_id")
    if isinstance(bundle_id, str) and bundle_id and not _SAFE_NAME_RE.match(bundle_id):
        _append_issue(
            issues,
            _issue("worker_runtime_field_invalid", "worker_runtime", "bundle_id has invalid characters", field="bundle_id"),
        )

    compatibility = data.get("compatibility")
    if not isinstance(compatibility, dict):
        _append_issue(
            issues,
            _issue("worker_runtime_field_invalid", "worker_runtime", "compatibility must be an object", field="compatibility"),
        )
    else:
        extra_compat = sorted(set(compatibility) - _WORKER_COMPAT_KEYS)
        if extra_compat:
            _append_issue(
                issues,
                _issue(
                    "worker_runtime_field_invalid",
                    "worker_runtime",
                    "compatibility has unknown fields: %s" % ", ".join(extra_compat),
                    field="compatibility",
                ),
            )
        if compatibility.get("requires_python") != REQUIRES_PYTHON:
            _append_issue(
                issues,
                _issue(
                    "incompatible_target",
                    "worker_runtime",
                    "requires_python must be %r" % REQUIRES_PYTHON,
                    field="compatibility.requires_python",
                ),
            )
        if compatibility.get("platform") != PLATFORM_TAG:
            _append_issue(
                issues,
                _issue(
                    "incompatible_target",
                    "worker_runtime",
                    "platform must be %r" % PLATFORM_TAG,
                    field="compatibility.platform",
                ),
            )
        if compatibility.get("cpu_only") is not True:
            _append_issue(
                issues,
                _issue(
                    "incompatible_target",
                    "worker_runtime",
                    "cpu_only must be true",
                    field="compatibility.cpu_only",
                ),
            )

    runtime = data.get("runtime")
    if not isinstance(runtime, dict):
        _append_issue(
            issues,
            _issue("worker_runtime_field_invalid", "worker_runtime", "runtime must be an object", field="runtime"),
        )
    else:
        extra_runtime = sorted(set(runtime) - _WORKER_RUNTIME_SECTION_KEYS)
        if extra_runtime:
            _append_issue(
                issues,
                _issue(
                    "worker_runtime_field_invalid",
                    "worker_runtime",
                    "runtime has unknown fields: %s" % ", ".join(extra_runtime),
                    field="runtime",
                ),
            )
        for key in ("python_root", "python_exe", "site_packages"):
            _validate_relative_field(
                runtime.get(key),
                root,
                resource="vendored_python",
                field="runtime.%s" % key,
                issues=issues,
            )

    dependencies = data.get("dependencies")
    if not isinstance(dependencies, list) or not dependencies:
        _append_issue(
            issues,
            _issue(
                "worker_runtime_field_invalid",
                "worker_runtime",
                "dependencies must be a non-empty array",
                field="dependencies",
            ),
        )
    else:
        for index, item in enumerate(dependencies):
            field = "dependencies[%d]" % index
            if not isinstance(item, dict):
                _append_issue(issues, _issue("worker_runtime_field_invalid", "worker_runtime", "%s must be an object" % field, field=field))
                continue
            item_extra = sorted(set(item) - _DEPENDENCY_KEYS)
            if item_extra:
                _append_issue(
                    issues,
                    _issue(
                        "worker_runtime_field_invalid",
                        "worker_runtime",
                        "%s has unknown fields: %s" % (field, ", ".join(item_extra)),
                        field=field,
                    ),
                )
            name = item.get("name")
            if not isinstance(name, str) or not name or not _SAFE_NAME_RE.match(name):
                _append_issue(issues, _issue("worker_runtime_field_invalid", "worker_runtime", "%s.name is invalid" % field, field=field + ".name"))
            import_names = item.get("import_names")
            if (
                not isinstance(import_names, list)
                or not import_names
                or not all(
                    isinstance(value, str) and value and _IMPORT_NAME_RE.match(value)
                    for value in import_names
                )
            ):
                _append_issue(
                    issues,
                    _issue(
                        "worker_runtime_field_invalid",
                        "worker_runtime",
                        "%s.import_names is invalid" % field,
                        field=field + ".import_names",
                    ),
                )
            if not isinstance(item.get("required"), bool):
                _append_issue(issues, _issue("worker_runtime_field_invalid", "worker_runtime", "%s.required must be boolean" % field, field=field + ".required"))
            for optional_key in ("version", "dist_info", "capability"):
                if optional_key in item and (
                    not isinstance(item[optional_key], str) or not item[optional_key]
                ):
                    _append_issue(
                        issues,
                        _issue(
                            "worker_runtime_field_invalid",
                            "worker_runtime",
                            "%s.%s must be a non-empty string" % (field, optional_key),
                            field=field + "." + optional_key,
                        ),
                    )
            if "sha256" in item and (
                not isinstance(item["sha256"], str) or not _SHA256_RE.match(item["sha256"])
            ):
                _append_issue(issues, _issue("worker_runtime_field_invalid", "worker_runtime", "%s.sha256 is invalid" % field, field=field + ".sha256"))

    models = data.get("models")
    if not isinstance(models, list) or not models:
        _append_issue(
            issues,
            _issue(
                "worker_runtime_field_invalid",
                "worker_runtime",
                "models must be a non-empty array",
                field="models",
            ),
        )
    else:
        seen: set[str] = set()
        for index, item in enumerate(models):
            field = "models[%d]" % index
            if not isinstance(item, dict):
                _append_issue(issues, _issue("worker_runtime_field_invalid", "worker_runtime", "%s must be an object" % field, field=field))
                continue
            item_extra = sorted(set(item) - _MODEL_KEYS)
            if item_extra:
                _append_issue(
                    issues,
                    _issue(
                        "worker_runtime_field_invalid",
                        "worker_runtime",
                        "%s has unknown fields: %s" % (field, ", ".join(item_extra)),
                        field=field,
                    ),
                )
            model_id = item.get("id")
            if not isinstance(model_id, str) or not model_id or not _SAFE_NAME_RE.match(model_id):
                _append_issue(issues, _issue("worker_runtime_field_invalid", "worker_runtime", "%s.id is invalid" % field, field=field + ".id"))
            else:
                if model_id in seen:
                    _append_issue(issues, _issue("worker_runtime_field_invalid", "worker_runtime", "duplicate model id %r" % model_id, field=field + ".id"))
                seen.add(model_id)
            _validate_relative_field(
                item.get("path"),
                root,
                resource="model:%s" % (model_id if isinstance(model_id, str) else "unknown"),
                field=field + ".path",
                issues=issues,
            )
            if not isinstance(item.get("required"), bool):
                _append_issue(issues, _issue("worker_runtime_field_invalid", "worker_runtime", "%s.required must be boolean" % field, field=field + ".required"))
            sha = item.get("sha256")
            if not isinstance(sha, str) or not _SHA256_RE.match(sha):
                _append_issue(issues, _issue("worker_runtime_field_invalid", "worker_runtime", "%s.sha256 is required and must be 64 lowercase hex chars" % field, field=field + ".sha256"))
            size = item.get("size_bytes")
            if isinstance(size, bool) or not isinstance(size, int) or size < 0:
                _append_issue(issues, _issue("worker_runtime_field_invalid", "worker_runtime", "%s.size_bytes must be a non-negative integer" % field, field=field + ".size_bytes"))
            for optional_key in ("source", "license"):
                if optional_key in item and (
                    not isinstance(item[optional_key], str) or not item[optional_key]
                ):
                    _append_issue(
                        issues,
                        _issue(
                            "worker_runtime_field_invalid",
                            "worker_runtime",
                            "%s.%s must be a non-empty string" % (field, optional_key),
                            field=field + "." + optional_key,
                        ),
                    )

    files = data.get("files")
    if files is not None:
        if not isinstance(files, list):
            _append_issue(issues, _issue("worker_runtime_field_invalid", "worker_runtime", "files must be an array", field="files"))
        else:
            for index, item in enumerate(files):
                field = "files[%d]" % index
                if not isinstance(item, dict):
                    _append_issue(issues, _issue("worker_runtime_field_invalid", "worker_runtime", "%s must be an object" % field, field=field))
                    continue
                extra_file = sorted(set(item) - _RUNTIME_FILE_KEYS)
                if extra_file:
                    _append_issue(
                        issues,
                        _issue(
                            "worker_runtime_field_invalid",
                            "worker_runtime",
                            "%s has unknown fields: %s" % (field, ", ".join(extra_file)),
                            field=field,
                        ),
                    )
                _validate_relative_field(
                    item.get("path"),
                    root,
                    resource="worker_runtime",
                    field=field + ".path",
                    issues=issues,
                )
                sha = item.get("sha256")
                if not isinstance(sha, str) or not _SHA256_RE.match(sha):
                    _append_issue(issues, _issue("worker_runtime_field_invalid", "worker_runtime", "%s.sha256 is invalid" % field, field=field + ".sha256"))
                size = item.get("size_bytes")
                if isinstance(size, bool) or not isinstance(size, int) or size < 0:
                    _append_issue(issues, _issue("worker_runtime_field_invalid", "worker_runtime", "%s.size_bytes must be a non-negative integer" % field, field=field + ".size_bytes"))

    if "notes" in data and (not isinstance(data["notes"], str) or len(data["notes"]) > 4096):
        _append_issue(
            issues,
            _issue("worker_runtime_field_invalid", "worker_runtime", "notes must be a string of at most 4096 chars", field="notes"),
        )
    return issues


# ------------------------------------------------------------------ runtime check


def _compatibility(checking_interpreter_is_vendored: bool | None = None) -> dict[str, Any]:
    current_python = platform.python_version()
    machine = platform.machine()
    platform_matches = (
        sys.platform == "win32"
        and machine.lower() in {"amd64", "x86_64"}
        and sys.maxsize > 2**32
    )
    return {
        "requires_python": REQUIRES_PYTHON,
        "platform": PLATFORM_TAG,
        "cpu_only": CPU_ONLY,
        "current_python_version": current_python,
        "current_python_matches": tuple(sys.version_info[:2]) == (3, 11),
        "current_platform": "%s/%s" % (sys.platform, machine),
        "current_platform_matches": platform_matches,
        "checking_interpreter_is_vendored": checking_interpreter_is_vendored,
    }


def _same_path(left: str | Path, right: str | Path) -> bool:
    try:
        return _norm(Path(left).resolve()) == _norm(Path(right).resolve())
    except OSError:
        return False


def _effective_runtime_paths(
    layout: BundleLayout,
    worker_runtime: dict[str, Any] | None,
) -> dict[str, Path]:
    paths = {
        "python_root": layout.python_root,
        "python_exe": layout.python_exe,
        "site_packages": layout.site_packages,
    }
    if worker_runtime is None:
        return paths
    runtime = worker_runtime.get("runtime")
    if not isinstance(runtime, dict):
        return paths
    for key in paths:
        raw = runtime.get(key)
        if not isinstance(raw, str):
            continue
        try:
            paths[key] = resolve_member_path(layout.bundle_root, raw, field="runtime.%s" % key)
        except BundlePathError:
            # Validation already recorded a blocker; keep the safe default.
            continue
    return paths


def _check_python(
    layout: BundleLayout,
    runtime_paths: dict[str, Path],
    blockers: list[dict[str, Any]],
    warnings: list[dict[str, Any]],
) -> dict[str, Any]:
    python_root = Path(runtime_paths["python_root"])
    python_exe = Path(runtime_paths["python_exe"])
    site_packages = Path(runtime_paths["site_packages"])
    exe_present = python_exe.is_file()
    site_packages_present = site_packages.is_dir()
    python_dll = python_root / PYTHON_DLL_NAME
    dll_present = python_dll.is_file()
    is_vendored = exe_present and _same_path(sys.executable, python_exe)

    if not exe_present:
        _append_issue(
            blockers,
            _issue(
                "vendor_python_missing",
                "vendored_python",
                "vendored interpreter is missing: %s" % python_exe,
            ),
        )
    if not site_packages_present:
        _append_issue(
            blockers,
            _issue(
                "vendor_site_packages_missing",
                "vendored_site_packages",
                "vendored site-packages is missing: %s" % site_packages,
            ),
        )
    if exe_present and not dll_present:
        _append_issue(
            warnings,
            _issue(
                "python311_dll_missing",
                "vendored_python",
                "%s is missing next to the interpreter" % PYTHON_DLL_NAME,
                severity="warning",
                affects_readiness=False,
            ),
        )
    if not is_vendored:
        _append_issue(
            warnings,
            _issue(
                "external_interpreter",
                "python",
                "self-check is running under %s, not the bundle interpreter" % sys.executable,
                severity="warning",
                affects_readiness=False,
            ),
        )
    if tuple(sys.version_info[:2]) != (3, 11):
        _append_issue(
            warnings,
            _issue(
                "current_python_mismatch",
                "python",
                "current interpreter is %s; bundle requires %s" % (platform.python_version(), REQUIRES_PYTHON),
                severity="warning",
                affects_readiness=False,
            ),
        )
    if not layout.worker_root.is_dir():
        _append_issue(
            blockers,
            _issue("worker_missing", "worker", "worker directory is missing: %s" % layout.worker_root),
        )

    return {
        "runtime_root": str(python_root),
        "python_exe": str(python_exe),
        "site_packages": str(site_packages),
        "python_exe_present": exe_present,
        "site_packages_present": site_packages_present,
        "python311_dll_present": dll_present,
        "current": {
            "executable": sys.executable,
            "version": platform.python_version(),
            "version_info": [int(value) for value in sys.version_info[:3]],
            "implementation": platform.python_implementation(),
            "is_vendored": bool(is_vendored),
        },
    }


def _spec_origin(spec: Any) -> str | None:
    origin = getattr(spec, "origin", None)
    if isinstance(origin, str) and origin not in {"built-in", "frozen"}:
        return origin
    locations = getattr(spec, "submodule_search_locations", None)
    if locations:
        for location in locations:
            if isinstance(location, str) and location:
                return location
    return None


def _distribution_version(import_name: str) -> str | None:
    try:
        distributions = importlib.metadata.packages_distributions().get(import_name) or []
    except Exception:
        distributions = []
    for distribution in distributions:
        try:
            return importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError:
            continue
        except Exception:
            continue
    try:
        return importlib.metadata.version(import_name)
    except Exception:
        return None


def _probe_dependency(spec: DependencySpec, runtime_paths: dict[str, Path]) -> dict[str, Any]:
    result: dict[str, Any] = {
        "name": spec.name,
        "import_names": list(spec.import_names),
        "required": spec.required,
        "capability": spec.capability,
        "state": "missing",
        "import_name": None,
        "origin": None,
        "version": None,
        "error": None,
        "details": {},
    }
    for import_name in spec.import_names:
        try:
            found = importlib.util.find_spec(import_name)
        except (ImportError, ValueError, AttributeError) as exc:
            result["error"] = "%s: %s" % (type(exc).__name__, exc)
            continue
        if found is None:
            continue

        origin = _spec_origin(found)
        if origin is not None:
            try:
                origin = str(Path(origin).resolve())
            except OSError:
                pass
        result["import_name"] = import_name
        result["origin"] = origin
        inside_bundle = origin is not None and any(
            is_within_root(root, origin)
            for root in (runtime_paths["python_root"], runtime_paths["site_packages"])
        )
        if not inside_bundle:
            result["state"] = "external"
            result["error"] = "importable only from outside the bundle"
            return result

        try:
            module = importlib.import_module(import_name)
        except BaseException as exc:  # noqa: BLE001 - DLL/import failure must be reportable
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            result["state"] = "broken"
            result["error"] = "%s: %s" % (type(exc).__name__, str(exc)[:240])
            return result

        result["state"] = "ready"
        result["version"] = _distribution_version(import_name)
        if import_name == "onnxruntime":
            try:
                providers = list(module.get_available_providers())
            except Exception as exc:
                result["details"]["provider_probe_error"] = "%s: %s" % (type(exc).__name__, exc)
            else:
                result["details"]["providers"] = [str(item) for item in providers]
                result["details"]["cpu_only_providers"] = providers == ["CPUExecutionProvider"]
        return result

    if result["error"] is None:
        result["error"] = "no importable module name"
    return result


def _check_dependencies(
    runtime_paths: dict[str, Path],
    worker_runtime: dict[str, Any] | None,
    blockers: list[dict[str, Any]],
    warnings: list[dict[str, Any]],
) -> dict[str, Any]:
    # No worker-runtime.json means no worker dependency declarations.  Report
    # nothing here; the bundle manifest components are the only authority we
    # have, and inventing dependency states would be false.
    if worker_runtime is None:
        return {}

    declared: dict[str, dict[str, Any]] = {}
    raw_dependencies = worker_runtime.get("dependencies")
    if isinstance(raw_dependencies, list):
        for item in raw_dependencies:
            if isinstance(item, dict) and isinstance(item.get("name"), str):
                declared[item["name"]] = item

    results: dict[str, Any] = {}
    for spec in DEFAULT_DEPENDENCIES:
        probe = _probe_dependency(spec, runtime_paths)
        declaration = declared.pop(spec.name, None)
        # A manifest may upgrade an optional capability to required, but it may
        # not downgrade numpy/Pillow: a bundle without them is not publishable.
        required = spec.required
        if declaration is not None and declaration.get("required") is True:
            required = True
        probe["required"] = required
        results[spec.name] = probe

        if probe["state"] != "ready":
            severity = "blocker" if required else "warning"
            target = blockers if required else warnings
            _append_issue(
                target,
                _issue(
                    "dependency_%s" % probe["state"],
                    "dependency:%s" % spec.name,
                    "%s is %s%s"
                    % (
                        spec.name,
                        probe["state"],
                        (": " + probe["error"]) if probe.get("error") else "",
                    ),
                    severity=severity,
                    affects_readiness=True,
                ),
            )
            continue

        expected_version = declaration.get("version") if declaration is not None else None
        if isinstance(expected_version, str) and probe.get("version") and expected_version != probe["version"]:
            _append_issue(
                blockers,
                _issue(
                    "dependency_version_mismatch",
                    "dependency:%s" % spec.name,
                    "%s manifest version %s != installed %s"
                    % (spec.name, expected_version, probe["version"]),
                ),
            )

    for name in sorted(declared):
        _append_issue(
            warnings,
            _issue(
                "unsupported_dependency_declaration",
                "dependency:%s" % name,
                "manifest declares dependency %r outside the fixed phase-1 probe set" % name,
                severity="warning",
                affects_readiness=False,
            ),
        )
    return results


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _relative_display(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


def _model_declaration(
    model_id: str,
    worker_runtime: dict[str, Any] | None,
    distribution: dict[str, Any] | None,
) -> tuple[dict[str, Any] | None, str | None]:
    if worker_runtime is not None and isinstance(worker_runtime.get("models"), list):
        for item in worker_runtime["models"]:
            if isinstance(item, dict) and item.get("id") == model_id:
                return item, WORKER_RUNTIME_FILENAME
    if distribution is not None:
        optional = distribution.get("optional", {}).get(model_id)
        if isinstance(optional, dict) and optional.get("present") is True:
            return (
                {
                    "id": model_id,
                    "path": optional.get("path"),
                    "required": False,
                    "sha256": optional.get("sha256"),
                    "size_bytes": optional.get("size"),
                },
                DISTRIBUTION_MANIFEST_FILENAME,
            )
    return None, None


def _check_model(
    model_id: str,
    layout: BundleLayout,
    worker_runtime: dict[str, Any] | None,
    distribution: dict[str, Any] | None,
    dependencies: dict[str, Any],
    blockers: list[dict[str, Any]],
    warnings: list[dict[str, Any]],
) -> dict[str, Any]:
    declaration, source = _model_declaration(model_id, worker_runtime, distribution)
    default_path = layout.model_paths[model_id]

    if declaration is not None:
        try:
            model_path = resolve_member_path(
                layout.bundle_root,
                declaration["path"],
                field="models.%s.path" % model_id,
            )
        except (BundlePathError, KeyError, TypeError):
            model_path = default_path
    else:
        model_path = default_path

    declared = declaration is not None
    required = bool(declaration.get("required")) if declaration is not None else False
    expected_sha = declaration.get("sha256") if declaration is not None else None
    expected_size = declaration.get("size_bytes") if declaration is not None else None

    info: dict[str, Any] = {
        "id": model_id,
        "relative_path": _relative_display(layout.bundle_root, model_path),
        "path": str(model_path),
        "declared": declared,
        "declaration_source": source,
        "required": required,
        "exists": False,
        "size_bytes": None,
        "expected_size_bytes": expected_size if isinstance(expected_size, int) else None,
        "expected_sha256": expected_sha if isinstance(expected_sha, str) else None,
        "sha256": None,
        "state": "missing",
        "ready": False,
        "reason": "file_missing",
    }

    try:
        exists = model_path.is_file()
    except OSError as exc:
        info["state"] = "unreadable"
        info["reason"] = "%s: %s" % (type(exc).__name__, exc)
        _append_issue(
            blockers,
            _issue("model_unreadable", "model:%s" % model_id, "model stat failed: %s" % exc),
        )
        return info

    info["exists"] = bool(exists)
    if not exists:
        message = "%s model is missing: %s" % (model_id, model_path)
        if required:
            _append_issue(blockers, _issue("model_missing", "model:%s" % model_id, message))
        else:
            _append_issue(
                warnings,
                _issue(
                    "model_missing",
                    "model:%s" % model_id,
                    message,
                    severity="warning",
                    affects_readiness=True,
                ),
            )
        return info

    try:
        size = model_path.stat().st_size
    except OSError as exc:
        info["state"] = "unreadable"
        info["reason"] = "%s: %s" % (type(exc).__name__, exc)
        _append_issue(
            blockers,
            _issue("model_unreadable", "model:%s" % model_id, "model stat failed: %s" % exc),
        )
        return info
    info["size_bytes"] = int(size)

    if not declared:
        info["state"] = "present_unverified"
        info["reason"] = "not_declared_in_manifest"
        _append_issue(
            warnings,
            _issue(
                "model_unverified",
                "model:%s" % model_id,
                "%s exists but no manifest checksum declares it; never reported ready" % model_id,
                severity="warning",
                affects_readiness=True,
            ),
        )
        return info

    if size == 0:
        info["state"] = "invalid"
        info["reason"] = "empty_file"
        _append_issue(
            blockers,
            _issue("model_invalid", "model:%s" % model_id, "declared model %s is empty" % model_id),
        )
        return info

    if isinstance(expected_size, int) and size != expected_size:
        info["state"] = "size_mismatch"
        info["reason"] = "size_mismatch"
        _append_issue(
            blockers,
            _issue(
                "model_size_mismatch",
                "model:%s" % model_id,
                "%s size %d != manifest %d" % (model_id, size, expected_size),
            ),
        )
        return info

    if not isinstance(expected_sha, str):
        info["state"] = "present_unverified"
        info["reason"] = "manifest_checksum_missing"
        _append_issue(
            warnings,
            _issue(
                "model_unverified",
                "model:%s" % model_id,
                "declared %s has no sha256; never reported ready" % model_id,
                severity="warning",
                affects_readiness=True,
            ),
        )
        return info

    try:
        actual_sha = _sha256_file(model_path)
    except OSError as exc:
        info["state"] = "unreadable"
        info["reason"] = "%s: %s" % (type(exc).__name__, exc)
        _append_issue(
            blockers,
            _issue("model_unreadable", "model:%s" % model_id, "model checksum read failed: %s" % exc),
        )
        return info
    info["sha256"] = actual_sha

    if actual_sha != expected_sha:
        info["state"] = "checksum_mismatch"
        info["reason"] = "checksum_mismatch"
        _append_issue(
            blockers,
            _issue(
                "model_checksum_mismatch",
                "model:%s" % model_id,
                "%s sha256 does not match the manifest" % model_id,
            ),
        )
        return info

    onnx_state = (dependencies.get("onnxruntime") or {}).get("state")
    if onnx_state != "ready":
        info["state"] = "present_verified"
        info["reason"] = "runtime_unavailable"
        severity_target = blockers if required else warnings
        _append_issue(
            severity_target,
            _issue(
                "model_runtime_unavailable",
                "model:%s" % model_id,
                "%s checksum matches but onnxruntime is %s" % (model_id, onnx_state),
                severity="blocker" if required else "warning",
                affects_readiness=True,
            ),
        )
        return info

    info["state"] = "ready"
    info["ready"] = True
    info["reason"] = "checksum_match"
    return info


def _status_from(blockers: list[dict[str, Any]], warnings: list[dict[str, Any]]) -> str:
    if any(item.get("severity") == "blocker" for item in blockers):
        return "blocked"
    if any(item.get("affects_readiness") for item in warnings):
        return "degraded"
    return "ready"


def _empty_result(bundle_root: str | None) -> dict[str, Any]:
    return {
        "schema": SELFCHECK_SCHEMA,
        "bundle_root": bundle_root,
        "worker_root": None,
        "runtime_root": None,
        "status": "blocked",
        "ok": False,
        "compatibility": _compatibility(),
        "worker_runtime": {
            "path": None,
            "present": False,
            "state": "unavailable",
            "schema": None,
            "bundle_id": None,
            "bundle_version": None,
            "errors": [],
        },
        "distribution": {
            "manifest": {
                "path": None,
                "present": False,
                "state": "unavailable",
                "schema": None,
                "errors": [],
            },
            "components": {},
            "required": {},
            "optional": {},
            "assets_manifest": {
                "path": None,
                "present": False,
                "state": "unavailable",
                "errors": [],
            },
            "vendor_root": {
                "path": None,
                "present": False,
                "state": "unavailable",
            },
            "third_party_notices": {
                "path": None,
                "present": False,
                "state": "unavailable",
                "pending_template": False,
            },
        },
        "components": {},
        "python": {
            "runtime_root": None,
            "python_exe": None,
            "site_packages": None,
            "python_exe_present": False,
            "site_packages_present": False,
            "python311_dll_present": False,
            "current": {
                "executable": sys.executable,
                "version": platform.python_version(),
                "version_info": [int(value) for value in sys.version_info[:3]],
                "implementation": platform.python_implementation(),
                "is_vendored": False,
            },
        },
        "dependencies": {},
        "model": {
            model_id: {
                "id": model_id,
                "relative_path": relative,
                "path": None,
                "declared": False,
                "declaration_source": None,
                "required": False,
                "exists": False,
                "size_bytes": None,
                "expected_size_bytes": None,
                "expected_sha256": None,
                "sha256": None,
                "state": "missing",
                "ready": False,
                "reason": "bundle_root_unavailable",
            }
            for model_id, relative in MODEL_IDS.items()
        },
        "blockers": [],
        "warnings": [],
        "blocked_resources": [],
        "degraded_resources": [],
        "policy": {
            "downloads_allowed": False,
            "creates_directories": False,
            "host_sdk_imports": False,
            "binary_frames_in_protocol": False,
        },
    }


def _finalize(result: dict[str, Any]) -> dict[str, Any]:
    result["blocked_resources"] = sorted(
        {str(item.get("resource")) for item in result["blockers"] if item.get("resource")}
    )
    result["degraded_resources"] = sorted(
        {
            str(item.get("resource"))
            for item in result["warnings"]
            if item.get("resource") and item.get("affects_readiness")
        }
    )
    result["status"] = _status_from(result["blockers"], result["warnings"])
    result["ok"] = result["status"] == "ready"
    return result


def _check_layout(layout: BundleLayout) -> dict[str, Any]:
    result = _empty_result(str(layout.bundle_root))
    result["worker_root"] = str(layout.worker_root)
    result["worker_runtime"]["path"] = str(layout.worker_runtime_path)

    blockers: list[dict[str, Any]] = result["blockers"]
    warnings: list[dict[str, Any]] = result["warnings"]

    runtime_data, runtime_info = _read_worker_runtime(layout)
    result["worker_runtime"] = runtime_info
    if runtime_info.get("state") == "absent":
        _append_issue(
            warnings,
            _issue(
                "worker_runtime_absent",
                "worker_runtime",
                "worker-runtime.json is optional and absent; only bundle manifest component entries are reported",
                severity="warning",
                affects_readiness=True,
            ),
        )
    else:
        for issue in runtime_info.get("errors", []):
            _append_issue(blockers, issue)

    result["distribution"] = _check_distribution(layout, blockers, warnings)
    result["components"] = {
        key: dict(value)
        for key, value in result["distribution"].get("components", {}).items()
    }

    runtime_paths = _effective_runtime_paths(layout, runtime_data)
    result["runtime_root"] = str(runtime_paths["python_root"])
    result["python"] = _check_python(layout, runtime_paths, blockers, warnings)

    result["compatibility"] = _compatibility(result["python"]["current"]["is_vendored"])
    dependencies = _check_dependencies(runtime_paths, runtime_data, blockers, warnings)
    result["dependencies"] = dependencies
    result["model"] = {
        model_id: _check_model(
            model_id,
            layout,
            runtime_data,
            result["distribution"],
            dependencies,
            blockers,
            warnings,
        )
        for model_id in MODEL_IDS
    }
    return _finalize(result)


def _resource_for_field(field: str) -> str:
    head = (field or "bundle_root").split(".", 1)[0]
    if head.startswith("models"):
        return "model:%s" % head
    if head in {"runtime", "vendor", "python"}:
        return "vendored_python"
    if head.startswith("worker_runtime"):
        return "worker_runtime"
    if head.startswith("distribution"):
        return "distribution_manifest"
    if head.startswith("assets"):
        return "assets_manifest"
    if head.startswith("third_party"):
        return "third_party_notices"
    if head == "worker":
        return "worker"
    return "bundle_root"


def check_bundle(bundle_root: str | Path | None = None) -> dict[str, Any]:
    """Return the offline runtime self-check as a JSON-serializable dict.

    This function is safe to call from the system Python: it reads the
    distribution ``bundle-manifest.json`` only, and it only reports packages as
    ``ready`` when their import origin is inside the bundle and an optional
    valid ``worker-runtime.json`` declared them.  Missing resources produce
    ``status == "blocked"`` or ``"degraded"``; they never produce ``ready`` and
    never trigger downloads or directory creation.
    """
    try:
        layout = resolve_layout(bundle_root)
    except BundlePathError as exc:
        result = _empty_result(None if bundle_root is None else str(bundle_root))
        result["blockers"].append(
            _issue(
                exc.code,
                _resource_for_field(exc.field),
                exc.message,
                field=exc.field or "bundle_root",
            )
        )
        return _finalize(result)
    try:
        return _check_layout(layout)
    except Exception as exc:  # noqa: BLE001 - --check must never crash
        result = _empty_result(str(layout.bundle_root))
        result["worker_root"] = str(layout.worker_root)
        result["worker_runtime"]["path"] = str(layout.worker_runtime_path)
        result["blockers"].append(
            _issue(
                "internal_error",
                "worker",
                "self-check failed: %s: %s" % (type(exc).__name__, str(exc)[:240]),
            )
        )
        return _finalize(result)
