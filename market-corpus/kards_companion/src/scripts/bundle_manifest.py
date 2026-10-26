#!/usr/bin/env python3
"""Generate and verify the kards_companion offline bundle manifest.

The manifest covers only the distribution payload: worker/, vendor/, assets/,
models/, plus the root THIRD_PARTY_NOTICES file.  It is separate
from the source allowlist in sync_installations.py, which is unchanged.  This
tool never downloads files, installs packages, starts the host, or writes an
installation target.  It writes nothing unless --output is given explicitly.
Python 3.11+.
"""

import argparse
import hashlib
import json
import os
import re
import stat
import sys
import tempfile
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

SCHEMA = "neko-kards-bundle-manifest/1"
BUNDLE_DIRS = ("worker", "vendor", "assets", "models")
VENDOR_SITE_PACKAGES = "vendor/python/Lib/site-packages"
VENDOR_SITE_PACKAGES_FOLDED = VENDOR_SITE_PACKAGES.casefold()
VENDOR_PACKAGE_RESOURCES = frozenset({"data", "config"})
FORBIDDEN_NAMES = {
    "training": "training", "dataset": "dataset", "videos": "Videos",
    "recording": "recording", "data": "data", "config": "config",
    ".pytest_cache": ".pytest_cache", "whl": "whl", "keys": "keys",
}
GENERATED_NAMES = {"__pycache__": "__pycache__"}
IGNORED_DIRS = {"__pycache__"}
IGNORED_SUFFIXES = {".pyc"}
FORBIDDEN_SUFFIXES = {".pyc": "pyc", ".whl": "whl"}
NOTICE_CANDIDATES = ("THIRD_PARTY_NOTICES.md", "THIRD_PARTY_NOTICES.txt", "THIRD_PARTY_NOTICES")
REQUIRED = (
    ("python_worker", ("worker/entry.py", "worker/bootstrap.py")),
    ("assets_manifest", ("assets/cards/manifest.json", "assets/manifest.json")),
    ("third_party_notices", NOTICE_CANDIDATES),
)
JSON_REQUIRED = {"assets_manifest"}
# 双 YOLO 模型为可选载荷：kards_ui_det / kards_card_cls；旧 kards_det 声明删除。
OPTIONAL_MODELS = {
    "kards_ui_det": "models/ui_det.onnx",
    "kards_card_cls": "models/card_cls.onnx",
}
NOASSERTION = "NOASSERTION"
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
DRIVE_RE = re.compile(r"^[A-Za-z]:")
REPARSE_FLAG = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


class BundleError(ValueError):
    """Unsafe, incomplete, or invalid bundle input."""


def sha(data):
    return hashlib.sha256(data).hexdigest()


def fsha(path):
    with path.open("rb") as fh:
        return hashlib.file_digest(fh, "sha256").hexdigest()


def is_reparse(path):
    try:
        st = os.lstat(path)
    except OSError:
        return False
    return bool(getattr(st, "st_file_attributes", 0) & REPARSE_FLAG) or stat.S_ISLNK(st.st_mode)


def inside_vendor_site_packages(rel):
    folded = rel.casefold()
    return (folded == VENDOR_SITE_PACKAGES_FOLDED or
            folded.startswith(VENDOR_SITE_PACKAGES_FOLDED + "/"))


def forbidden_component(rel):
    """Return the forbidden component for a payload-relative path, if any.

    The vendored CPython runtime is the one fixed exception: its site-packages
    tree may contain ``data/`` and ``config/`` package resources.  Caches,
    wheels, keys, training data and recordings stay forbidden everywhere.
    """
    inside_vendor = inside_vendor_site_packages(rel)
    for part in PurePosixPath(rel).parts:
        folded = part.casefold()
        if folded == "site-packages":
            if inside_vendor:
                continue
            return "site-packages"
        if folded in FORBIDDEN_NAMES:
            if inside_vendor and folded in VENDOR_PACKAGE_RESOURCES:
                continue
            return FORBIDDEN_NAMES[folded]
        if folded in GENERATED_NAMES:
            return GENERATED_NAMES[folded]
    return None


def check_relpath(value):
    """Return a canonical POSIX relative path or raise BundleError."""
    if not isinstance(value, str):
        raise BundleError("bundle_path_not_string")
    if not value or "\x00" in value or "\\" in value or ":" in value:
        raise BundleError(f"bundle_path_unsafe:{value}")
    if len(value) > 1024:
        raise BundleError(f"bundle_path_too_long:{len(value)}")
    raw = value.split("/")
    if any(part in ("", ".", "..") for part in raw):
        raise BundleError(f"bundle_path_unsafe:{value}")
    pure = PurePosixPath(value)
    if pure.is_absolute() or pure.drive or DRIVE_RE.match(value):
        raise BundleError(f"bundle_path_unsafe:{value}")
    reason = forbidden_component(value)
    if reason:
        raise BundleError(f"bundle_forbidden_path:{value}:{reason}")
    if pure.suffix.casefold() in FORBIDDEN_SUFFIXES:
        raise BundleError(f"bundle_forbidden_path:{value}:{FORBIDDEN_SUFFIXES[pure.suffix.casefold()]}")
    return pure.as_posix()


def bundle_component(rel):
    """Only payload directories and the root notice are part of the bundle."""
    if rel in NOTICE_CANDIDATES:
        return "notices"
    parts = PurePosixPath(rel).parts
    if len(parts) >= 2 and parts[0] in BUNDLE_DIRS:
        return parts[0]
    raise BundleError(f"bundle_path_not_allowed:{rel}")


def check_component(value):
    if not isinstance(value, str) or not value or value != value.strip():
        raise BundleError(f"manifest_component_invalid:{value}")
    if value == "notices" or value in NOTICE_CANDIDATES:
        return value
    rel = check_relpath(value)
    if PurePosixPath(rel).parts[0] not in BUNDLE_DIRS:
        raise BundleError(f"manifest_component_not_allowed:{value}")
    return rel


def component_matches(rel, component):
    if rel in NOTICE_CANDIDATES:
        return component == "notices" or component == rel
    return rel == component or rel.startswith(component + "/")


def _meta_prefix(value):
    if value in BUNDLE_DIRS or value in NOTICE_CANDIDATES:
        return value
    rel = check_relpath(value)
    bundle_component(rel)
    return rel


def normalise_components(components):
    out = {}
    for key, value in (components or {}).items():
        prefix = _meta_prefix(key)
        if not isinstance(value, Mapping):
            raise BundleError(f"component_meta_invalid:{key}")
        version = value.get("version", "unversioned")
        license_value = value.get("license", NOASSERTION)
        if not isinstance(version, str) or not version.strip():
            raise BundleError(f"component_meta_invalid_version:{key}")
        if not isinstance(license_value, str) or not license_value.strip():
            raise BundleError(f"component_meta_invalid_license:{key}")
        out[prefix] = {"version": version.strip(), "license": license_value.strip()}
    return out


def plugin_meta(root):
    try:
        data = tomllib.loads((root / "plugin.toml").read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return None, "unversioned"
    plugin = data.get("plugin") if isinstance(data, dict) else None
    if not isinstance(plugin, dict):
        return None, "unversioned"
    plugin_id = plugin.get("id") if isinstance(plugin.get("id"), str) else None
    version = plugin.get("version")
    if not isinstance(version, str) or not version.strip():
        version = "unversioned"
    return plugin_id, version.strip()


def resolve_entry_meta(rel, components, version_default):
    matches = [key for key in components if rel == key or rel.startswith(key + "/")]
    if matches:
        key = max(matches, key=len)
        return key, components[key]["version"], components[key]["license"]
    component = bundle_component(rel)
    version = "unversioned" if component == "vendor" else version_default
    return component, version, NOASSERTION


def entry_fingerprint(entries):
    h = hashlib.sha256()
    for entry in sorted(entries, key=lambda item: item.path):
        row = "\0".join((entry.path, str(entry.size), entry.sha256,
                         entry.component, entry.version, entry.license))
        h.update(row.encode("utf-8") + b"\n")
    return h.hexdigest()


@dataclass(frozen=True)
class BundleEntry:
    path: str
    size: int
    sha256: str
    component: str
    version: str
    license: str

    def as_dict(self):
        return {"path": self.path, "size": self.size, "sha256": self.sha256,
                "component": self.component, "version": self.version,
                "license": self.license}


@dataclass
class BundleScan:
    root: Path
    plugin_id: str | None = None
    plugin_version: str = "unversioned"
    entries: list = field(default_factory=list)
    required: dict = field(default_factory=dict)
    optional: dict = field(default_factory=dict)
    errors: list = field(default_factory=list)
    warnings: list = field(default_factory=list)

    @property
    def ok(self):
        return not self.errors and all(item.get("present") for item in self.required.values())

    def capabilities(self):
        ready = not self.errors and bool(self.required) and \
            all(item.get("present") for item in self.required.values())
        caps = {"bundle_ready": ready}
        for model_id in OPTIONAL_MODELS:
            caps[model_id] = self.optional.get(model_id, {}).get("state", "missing")
        return caps

    def summary(self):
        return {"ok": self.ok, "root": str(self.root), "file_count": len(self.entries),
                "total_bytes": sum(entry.size for entry in self.entries),
                "plugin": {"id": self.plugin_id, "version": self.plugin_version},
                "required": self.required, "optional": self.optional,
                "capabilities": self.capabilities(), "errors": list(self.errors),
                "warnings": list(self.warnings)}


def _walk_allowed(root, errors):
    """Yield (rel, path, stat) for the allowed payload and root notice only.

    Reparse points and forbidden components are recorded and never descended.
    """
    found = []
    for dirname in BUNDLE_DIRS:
        directory = root / dirname
        try:
            st = os.lstat(directory)
        except FileNotFoundError:
            continue
        except OSError as exc:
            errors.append(f"bundle_dir_unreadable:{dirname}:{type(exc).__name__}")
            continue
        if is_reparse(directory) or bool(getattr(st, "st_file_attributes", 0) & REPARSE_FLAG):
            errors.append(f"bundle_reparse:{dirname}")
            continue
        if not stat.S_ISDIR(st.st_mode):
            errors.append(f"bundle_path_not_directory:{dirname}")
            continue
        stack = [directory]
        while stack:
            current = stack.pop()
            try:
                items = sorted(os.scandir(current), key=lambda item: (item.name.casefold(), item.name))
            except OSError as exc:
                rel = current.relative_to(root).as_posix()
                errors.append(f"bundle_dir_unreadable:{rel}:{type(exc).__name__}")
                continue
            for item in items:
                path = Path(item.path)
                rel = path.relative_to(root).as_posix()
                try:
                    st = os.lstat(path)
                except OSError as exc:
                    errors.append(f"bundle_path_unreadable:{rel}:{type(exc).__name__}")
                    continue
                if is_reparse(path) or bool(getattr(st, "st_file_attributes", 0) & REPARSE_FLAG):
                    errors.append(f"bundle_reparse:{rel}")
                    continue
                if stat.S_ISDIR(st.st_mode):
                    if item.name.casefold() in IGNORED_DIRS:
                        continue
                    reason = forbidden_component(rel)
                    if reason:
                        errors.append(f"bundle_forbidden_path:{rel}:{reason}")
                        continue
                    stack.append(path)
                    continue
                if not stat.S_ISREG(st.st_mode):
                    errors.append(f"bundle_not_regular_file:{rel}")
                    continue
                if path.suffix.casefold() in IGNORED_SUFFIXES:
                    continue
                try:
                    rel = check_relpath(rel)
                except BundleError as exc:
                    errors.append(str(exc))
                    continue
                found.append((rel, path, st))
    for name in NOTICE_CANDIDATES:
        path = root / name
        try:
            st = os.lstat(path)
        except FileNotFoundError:
            continue
        except OSError as exc:
            errors.append(f"bundle_path_unreadable:{name}:{type(exc).__name__}")
            continue
        if is_reparse(path) or bool(getattr(st, "st_file_attributes", 0) & REPARSE_FLAG):
            errors.append(f"bundle_reparse:{name}")
            continue
        if not stat.S_ISREG(st.st_mode):
            errors.append(f"bundle_not_regular_file:{name}")
            continue
        found.append((name, path, st))
    return found


def _required_state(root, scan):
    by_path = {entry.path: entry for entry in scan.entries}
    for name, alternatives in REQUIRED:
        hits = [candidate for candidate in alternatives if candidate in by_path]
        state = {"expected": list(alternatives), "path": None, "present": False}
        if not hits:
            scan.errors.append(f"required_missing:{name}:{alternatives[0]}")
            scan.required[name] = state
            continue
        path = hits[0]
        entry = by_path[path]
        state.update({"path": path, "present": True, "size": entry.size, "sha256": entry.sha256})
        if entry.size <= 0:
            scan.errors.append(f"required_empty:{name}:{path}")
        elif name in JSON_REQUIRED:
            try:
                with (root / path).open("rb") as fh:
                    payload = json.load(fh)
                if not isinstance(payload, dict):
                    raise ValueError("not_object")
            except (OSError, ValueError):
                scan.errors.append(f"required_invalid_json:{name}:{path}")
        scan.required[name] = state


def _optional_state(scan):
    by_path = {entry.path: entry for entry in scan.entries}
    for model_id, relative in OPTIONAL_MODELS.items():
        entry = by_path.get(relative)
        state = {"path": relative, "state": "present" if entry else "missing",
                 "present": bool(entry)}
        if entry:
            state.update({"size": entry.size, "sha256": entry.sha256})
            if entry.size <= 0:
                scan.errors.append(f"optional_empty:{model_id}")
        else:
            scan.warnings.append(f"optional_missing:{model_id}")
        scan.optional[model_id] = state


def _vendor_runtime_warnings(root, warnings):
    """Surface missing vendored Python without adding a fifth required marker."""
    if not (root / "vendor/python/python.exe").is_file():
        warnings.append("vendor_python_runtime_missing:vendor/python/python.exe")
    if not (root / "vendor/python/Lib/site-packages").is_dir():
        warnings.append("vendor_site_packages_missing:vendor/python/Lib/site-packages")


def scan_bundle(root, components=None):
    """Read-only scan of the payload.  Missing required files are errors."""
    root = Path(root)
    meta = normalise_components(components)
    scan = BundleScan(root=root)
    try:
        st = os.lstat(root)
    except FileNotFoundError:
        scan.errors.append("bundle_root_missing")
        return scan
    except OSError as exc:
        scan.errors.append(f"bundle_root_unreadable:{type(exc).__name__}")
        return scan
    if is_reparse(root) or bool(getattr(st, "st_file_attributes", 0) & REPARSE_FLAG):
        scan.errors.append("bundle_root_reparse")
        return scan
    if not stat.S_ISDIR(st.st_mode):
        scan.errors.append("bundle_root_not_directory")
        return scan
    scan.plugin_id, scan.plugin_version = plugin_meta(root)
    for rel, path, st in _walk_allowed(root, scan.errors):
        try:
            digest = fsha(path)
        except OSError as exc:
            scan.errors.append(f"bundle_file_unreadable:{rel}:{type(exc).__name__}")
            continue
        component, version, license_value = resolve_entry_meta(
            rel, meta, scan.plugin_version)
        scan.entries.append(BundleEntry(rel, st.st_size, digest, component,
                                        version, license_value))
        if license_value == NOASSERTION:
            scan.warnings.append(f"license_unverified:{component}")
    scan.entries.sort(key=lambda entry: entry.path)
    _required_state(root, scan)
    _optional_state(scan)
    _vendor_runtime_warnings(root, scan.warnings)
    if sum(1 for name in NOTICE_CANDIDATES if (root / name).is_file()) > 1:
        scan.warnings.append("multiple_third_party_notices")
    scan.errors = list(dict.fromkeys(scan.errors))
    scan.warnings = list(dict.fromkeys(scan.warnings))
    return scan


def _manifest_from_scan(scan, generated_at=None):
    entries = [entry.as_dict() for entry in scan.entries]
    components = {}
    for entry in scan.entries:
        item = components.setdefault(entry.component, {
            "version": entry.version, "license": entry.license,
            "file_count": 0, "size_bytes": 0})
        item["file_count"] += 1
        item["size_bytes"] += entry.size
    return {
        "schema": SCHEMA,
        "generated_at": generated_at or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "plugin": {"id": scan.plugin_id, "version": scan.plugin_version},
        "allowed_dirs": list(BUNDLE_DIRS),
        "forbidden_components": [FORBIDDEN_NAMES[key] for key in sorted(FORBIDDEN_NAMES)],
        "files": entries,
        "components": dict(sorted(components.items())),
        "required": scan.required,
        "optional": scan.optional,
        "capabilities": scan.capabilities(),
        "warnings": list(scan.warnings),
        "integrity": {"file_count": len(entries),
                      "total_bytes": sum(entry.size for entry in scan.entries),
                      "sha256": entry_fingerprint(scan.entries)},
    }


def generate_manifest(root, components=None, *, generated_at=None):
    """Generate a manifest, refusing incomplete or unsafe payloads."""
    scan = scan_bundle(root, components)
    if not scan.ok:
        raise BundleError("; ".join(scan.errors) or "bundle_incomplete")
    return _manifest_from_scan(scan, generated_at)


def load_manifest(source):
    if isinstance(source, Mapping):
        data = source
    else:
        path = Path(source)
        if is_reparse(path):
            raise BundleError("manifest_reparse")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise BundleError(f"manifest_unreadable:{type(exc).__name__}")
        except ValueError:
            raise BundleError("manifest_invalid_json")
    if not isinstance(data, dict):
        raise BundleError("manifest_not_object")
    if data.get("schema") != SCHEMA:
        raise BundleError(f"manifest_schema:{data.get('schema')}")
    return data


def _manifest_entries(data):
    raw = data.get("files")
    if not isinstance(raw, list):
        return [], ["manifest_files_not_list"]
    entries, errors, seen = [], [], set()
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            errors.append(f"manifest_entry_not_object:{index}")
            continue
        try:
            rel = check_relpath(item.get("path"))
        except BundleError as exc:
            errors.append(str(exc))
            continue
        try:
            bundle_component(rel)
        except BundleError as exc:
            errors.append(str(exc))
            continue
        if rel in seen:
            errors.append(f"manifest_duplicate_path:{rel}")
            continue
        seen.add(rel)
        size, digest = item.get("size"), item.get("sha256")
        component = item.get("component")
        version = item.get("version")
        license_value = item.get("license")
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            errors.append(f"manifest_entry_invalid_size:{rel}")
            continue
        if not isinstance(digest, str) or not SHA_RE.match(digest):
            errors.append(f"manifest_entry_invalid_sha256:{rel}")
            continue
        valid = True
        for field_name, value in (("component", component), ("version", version),
                                  ("license", license_value)):
            if not isinstance(value, str) or not value.strip():
                errors.append(f"manifest_entry_invalid_{field_name}:{rel}")
                valid = False
        if not valid:
            continue
        try:
            component = check_component(component)
        except BundleError as exc:
            errors.append(str(exc))
            continue
        if not component_matches(rel, component):
            errors.append(f"manifest_component_mismatch:{rel}:{component}")
            continue
        entries.append(BundleEntry(rel, size, digest, component,
                                   version.strip(), license_value.strip()))
    return entries, errors


def _root_plain(root):
    try:
        st = os.lstat(root)
    except FileNotFoundError:
        return "bundle_root_missing"
    except OSError as exc:
        return f"bundle_root_unreadable:{type(exc).__name__}"
    if is_reparse(root) or bool(getattr(st, "st_file_attributes", 0) & REPARSE_FLAG):
        return "bundle_root_reparse"
    if not stat.S_ISDIR(st.st_mode):
        return "bundle_root_not_directory"
    return None


def _verify_required(root, by_path, actual, report):
    for name, alternatives in REQUIRED:
        hits = [candidate for candidate in alternatives if candidate in by_path]
        state = {"expected": list(alternatives), "path": None, "present": False}
        if not hits:
            report["errors"].append(f"required_missing:{name}:{alternatives[0]}")
            report["required"][name] = state
            continue
        path = hits[0]
        entry = by_path[path]
        present = path in actual
        state.update({"path": path, "present": present,
                      "size": entry.size, "sha256": entry.sha256})
        if not present:
            report["errors"].append(f"required_missing:{name}:{path}")
        else:
            actual_size = actual[path].st_size
            if entry.size <= 0 or actual_size <= 0:
                report["errors"].append(f"required_empty:{name}:{path}")
            if name in JSON_REQUIRED:
                try:
                    with (root / path).open("rb") as fh:
                        payload = json.load(fh)
                    if not isinstance(payload, dict):
                        raise ValueError("not_object")
                except (OSError, ValueError):
                    report["errors"].append(f"required_invalid_json:{name}:{path}")
        report["required"][name] = state


def _verify_optional(by_path, actual, report):
    for model_id, relative in OPTIONAL_MODELS.items():
        entry = by_path.get(relative)
        st = actual.get(relative)
        present = st is not None
        state = {"path": relative, "state": "present" if present else "missing",
                 "present": present}
        if present:
            if st.st_size <= 0:
                report["errors"].append(f"optional_empty:{model_id}")
            if entry is None:
                report["warnings"].append(f"optional_unlisted:{model_id}")
            else:
                state.update({"size": entry.size, "sha256": entry.sha256})
        else:
            report["warnings"].append(f"optional_missing:{model_id}")
        report["optional"][model_id] = state


def verify_manifest(root, manifest):
    """Verify a manifest against the payload; required files fail closed."""
    root = Path(root)
    report = {"schema": SCHEMA, "ok": False, "errors": [], "warnings": [],
              "file_count": 0, "total_bytes": 0, "manifest_fingerprint": None,
              "required": {}, "optional": {}, "components": {},
              "capabilities": {model_id: "missing"
                               for model_id in OPTIONAL_MODELS}}
    try:
        data = load_manifest(manifest)
    except BundleError as exc:
        report["errors"].append(str(exc))
        return report
    declared_warnings = data.get("warnings")
    if isinstance(declared_warnings, list):
        report["warnings"].extend(
            warning for warning in declared_warnings if isinstance(warning, str))
    root_error = _root_plain(root)
    if root_error:
        report["errors"].append(root_error)
        return report
    entries, entry_errors = _manifest_entries(data)
    report["errors"].extend(entry_errors)
    by_path = {entry.path: entry for entry in entries}

    walked = _walk_allowed(root, report["errors"])
    actual = {rel: st for rel, _, st in walked}
    optional_paths = set(OPTIONAL_MODELS.values())
    for entry in entries:
        st = actual.get(entry.path)
        if st is None:
            if entry.path in optional_paths:
                report["warnings"].append(f"optional_missing:{entry.path}")
            else:
                report["errors"].append(f"file_missing:{entry.path}")
            continue
        if st.st_size != entry.size:
            report["errors"].append(f"size_mismatch:{entry.path}")
            continue
        try:
            digest = fsha(root / entry.path)
        except OSError as exc:
            if entry.path in optional_paths:
                report["warnings"].append(f"optional_unreadable:{entry.path}")
            else:
                report["errors"].append(
                    f"file_unreadable:{entry.path}:{type(exc).__name__}")
            continue
        if digest != entry.sha256:
            report["errors"].append(f"sha256_mismatch:{entry.path}")

    for rel in sorted(set(actual) - set(by_path)):
        if rel in optional_paths:
            report["warnings"].append(f"optional_unlisted:{rel}")
        else:
            report["errors"].append(f"unlisted_file:{rel}")

    components = {}
    for entry in entries:
        item = components.setdefault(entry.component, {
            "version": entry.version, "license": entry.license,
            "file_count": 0, "size_bytes": 0})
        if item["version"] != entry.version or item["license"] != entry.license:
            report["errors"].append(f"component_metadata_inconsistent:{entry.component}")
        item["file_count"] += 1
        item["size_bytes"] += entry.size
        if entry.license == NOASSERTION:
            report["warnings"].append(f"license_unverified:{entry.component}")
    report["components"] = dict(sorted(components.items()))

    _verify_required(root, by_path, actual, report)
    _verify_optional(by_path, actual, report)
    report["file_count"] = len(entries)
    report["total_bytes"] = sum(entry.size for entry in entries)
    computed = entry_fingerprint(entries)
    report["manifest_fingerprint"] = computed
    integrity = data.get("integrity")
    if not isinstance(integrity, dict):
        report["errors"].append("manifest_integrity_missing")
    else:
        declared = integrity.get("sha256")
        if not isinstance(declared, str) or not SHA_RE.match(declared):
            report["errors"].append("manifest_integrity_invalid")
        elif declared != computed:
            report["errors"].append("manifest_fingerprint_mismatch")
        if integrity.get("file_count") != len(entries):
            report["errors"].append("manifest_file_count_mismatch")
        if integrity.get("total_bytes") != report["total_bytes"]:
            report["errors"].append("manifest_total_bytes_mismatch")
    ready = (not report["errors"] and bool(report["required"])
             and all(item.get("present") for item in report["required"].values()))
    report["capabilities"] = {"bundle_ready": ready}
    for model_id in OPTIONAL_MODELS:
        report["capabilities"][model_id] = report["optional"].get(
            model_id, {}).get("state", "missing")
    report["errors"] = list(dict.fromkeys(report["errors"]))
    report["warnings"] = list(dict.fromkeys(report["warnings"]))
    report["ok"] = not report["errors"]
    return report


def write_manifest(path, manifest):
    path = Path(path)
    parent = path.parent
    probe = parent
    while not probe.exists() and not is_reparse(probe):
        if probe.parent == probe:
            break
        probe = probe.parent
    if is_reparse(parent) or (probe.exists() and not probe.is_dir()):
        raise BundleError("manifest_output_parent_not_plain")
    parent.mkdir(parents=True, exist_ok=True)
    if is_reparse(path):
        raise BundleError("manifest_output_reparse")
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise BundleError("manifest_output_exists_unreadable")
        if not isinstance(existing, dict) or existing.get("schema") != manifest.get("schema"):
            raise BundleError("manifest_output_schema_conflict")
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=parent)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write((json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def parse_component_args(values):
    out = {}
    for spec in values:
        parts = spec.split("=", 2)
        if len(parts) != 3 or not all(part.strip() for part in parts):
            raise BundleError(f"component_arg_invalid:{spec}")
        prefix, version, license_value = (part.strip() for part in parts)
        out[prefix] = {"version": version, "license": license_value}
    return out


def finish(report, code, message):
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"[bundle] {message}", file=sys.stderr)
    return code


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--generate", action="store_true",
                      help="scan the payload and emit a manifest")
    mode.add_argument("--check", nargs="?", const=True, metavar="MANIFEST",
                      help="verify a manifest (default: <root>/bundle-manifest.json)")
    ap.add_argument("--output", type=Path,
                    help="with --generate, atomically write this manifest path")
    ap.add_argument("--component", action="append", default=[],
                    metavar="PREFIX=VERSION=LICENSE",
                    help="explicit version/license metadata; repeatable")
    args = ap.parse_args(argv)
    if args.check is not None and args.output is not None:
        ap.error("--output requires --generate")
    try:
        components = parse_component_args(args.component)
    except BundleError as exc:
        return finish({"schema": SCHEMA, "ok": False, "errors": [str(exc)]}, 2, str(exc))
    root = args.root
    if args.check is not None:
        path = root / "bundle-manifest.json" if args.check is True else Path(args.check)
        if not path.is_absolute():
            path = root / path
        report = verify_manifest(root, path)
        report["manifest"] = str(path)
        return finish(report, 0 if report["ok"] else 2,
                      f"verify {'ok' if report['ok'] else 'blocked'}: "
                      f"{len(report['errors'])} error(s)")
    scan = scan_bundle(root, components)
    if not scan.ok:
        return finish(scan.summary(), 2, f"generate blocked: {len(scan.errors)} error(s)")
    manifest = _manifest_from_scan(scan)
    if args.output:
        path = args.output if args.output.is_absolute() else root / args.output
        try:
            write_manifest(path, manifest)
        except (OSError, BundleError) as exc:
            return finish({"schema": SCHEMA, "ok": False, "errors": [str(exc)]},
                          2, f"write blocked: {type(exc).__name__}")
        report = {"schema": SCHEMA, "ok": True, "root": str(root),
                  "manifest": str(path), "file_count": len(scan.entries),
                  "total_bytes": manifest["integrity"]["total_bytes"],
                  "manifest_fingerprint": manifest["integrity"]["sha256"],
                  "required": manifest["required"], "optional": manifest["optional"],
                  "capabilities": manifest["capabilities"],
                  "warnings": list(scan.warnings)}
        return finish(report, 0, f"manifest written: {path}")
    return finish(manifest, 0, "generated in memory; no file written")


if __name__ == "__main__":
    sys.exit(main())
