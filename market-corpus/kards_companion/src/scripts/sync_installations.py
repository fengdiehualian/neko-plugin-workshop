#!/usr/bin/env python3
"""Copy the allowlisted kards_companion tree into N.E.K.O install roots.

Default is --check (read-only).  --apply is explicit.  Formal apply also needs
--host-verified plus a real schema-v1 acceptance report whose plugin_loaded,
delivery.submitted, sdk.accepted, and artifact hash pass; the flag alone is not
proof.  This tool never starts the host, writes target config/state, or prints
config/secret contents.  --apply prunes stale allowlisted files (with backup,
--no-prune-stale to keep them) so removed modules (OCR/recording) do not linger
in installations.  YOLO models under models/ deploy only when declared by
knowledge/models_manifest.json; formal apply additionally requires both models
(--no-require-models to relax for code-only dev sync).  Python 3.11+.
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import sys
import tempfile
import tomllib
from datetime import datetime
from pathlib import Path

CARDS = "dc06efb1180646e15953b73ccb9dc0683b83676bd0062d1b8ff5831771ec4661"
DEV, FORMAL = "kards_companion_dev", "kards_companion"
BACKUP_SPACE_MARGIN = 16 * 1024 * 1024
ALLOW = {"__init__.py", "plugin.toml", "README.md", "requirements.txt", "adapters",
         "delivery", "policy", "runtime", "state", "knowledge", "perception", "ui",
         "tests", "docs", "scripts"}
SKIP = {"__pycache__", ".pytest_cache", "env", "data", "config", "model",
        "dataset", "outputs", "records"}
# models/ 由 models_manifest.json 显式声明后进入同步集；SKIP 不再整体排除，
# 但 source_tree() 的目录扫描仍不收 models/，只有 model_files() 白名单文件会进。
MODEL_DIR = "models"
SECRET = re.compile(r"""(?im)^\s*["']?([A-Za-z0-9_.-]*(?:api[_-]?key|token|secret|password|authorization)[A-Za-z0-9_.-]*)["']?\s*[:=]\s*["']([^"'\n]{16,})["']""")
SECRET_VAL = re.compile(r"^(sk-|rk-|ghp_|github_pat_|xox[baprs]-|AKIA|ASIA|eyJ)", re.I)
ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]{5,}$")


def sha(data): return hashlib.sha256(data).hexdigest()


def fsha(path):
    with path.open("rb") as fh:
        return hashlib.file_digest(fh, "sha256").hexdigest()


def reparse(path):
    try:
        st = os.lstat(path)
    except OSError:
        return False
    return bool(getattr(st, "st_file_attributes", 0) &
                getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)) or path.is_symlink()


def bad_chain(anchor, target):
    try:
        parts = target.relative_to(anchor).parts
    except ValueError:
        parts, anchor = target.parts, Path(target.anchor)
    cur = anchor
    for part in (None, *parts):
        if part is not None:
            cur = cur / part
        if cur.exists() and reparse(cur):
            return str(cur)
    return None


def resolve_bundle_manifest(root, selection):
    path = root / "bundle-manifest.json" if selection is True else Path(selection)
    return path if path.is_absolute() else root / path


def bundle_input(root, selection):
    """Explicit opt-in: verify a payload manifest and return its file bytes."""
    path, files, errors = resolve_bundle_manifest(root, selection), {}, []
    try:
        import bundle_manifest
    except ImportError as exc:
        reason = f"bundle_module_import:{type(exc).__name__}"
        return path, {"enabled": True, "ok": False, "errors": [reason],
                      "warnings": [], "manifest": str(path)}, files, [reason]
    try:
        report = bundle_manifest.verify_manifest(root, path)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        reason = f"bundle_verify:{type(exc).__name__}"
        return path, {"enabled": True, "ok": False, "errors": [reason],
                      "warnings": [], "manifest": str(path)}, files, [reason]
    errors = [f"bundle:{reason}" for reason in report.get("errors", [])]
    optional_files = {}
    for name, entry in (report.get("optional") or {}).items():
        # NOTE(D): 局部变量改名 entry_path，避免覆盖上面的 manifest path
        # （双 optional 模型上线后该遮蔽会让 load_manifest 读错文件）。
        entry_path = entry.get("path") if isinstance(entry, dict) else None
        if isinstance(entry_path, str):
            optional_files[entry_path] = bool(entry.get("present"))
    if report.get("ok"):
        try:
            data = bundle_manifest.load_manifest(path)
            for item in data["files"]:
                rel = item["path"]
                if rel in optional_files and not optional_files[rel]:
                    continue  # optional payload missing is not a sync blocker
                try:
                    blob = (root / rel).read_bytes()
                except OSError as exc:
                    errors.append(f"bundle:read:{rel}:{type(exc).__name__}")
                    continue
                if len(blob) != item["size"] or sha(blob) != item["sha256"]:
                    errors.append(f"bundle:changed_during_read:{rel}")
                    continue
                files[rel] = blob
        except (OSError, ValueError, KeyError, TypeError) as exc:
            errors.append(f"bundle:manifest_reload:{type(exc).__name__}")
    summary = {key: report.get(key) for key in
               ("file_count", "total_bytes", "manifest_fingerprint", "required",
                "optional", "capabilities", "components", "warnings")}
    summary.update({"enabled": True, "manifest": str(path),
                    "ok": bool(report.get("ok")) and not errors,
                    "errors": list(dict.fromkeys(list(report.get("errors", [])) +
                                                 [reason.removeprefix("bundle:") for reason in errors]))})
    return path, summary, files, errors


def prunable(rel):
    """Stale files we own: top-level allowlisted code dirs/files and models/.

    Anything else (e.g. tool state like .mimosa, host config) is never pruned
    even if it shows up in the stale list."""
    head = rel.split("/", 1)[0]
    return head in ALLOW or head == MODEL_DIR


def should_backup(rel, bundle_paths):
    return rel in bundle_paths or not set(Path(rel).parts) & SKIP


def reparse_chain(path):
    cur, chain = Path(path), []
    while True:
        chain.append(cur)
        if cur.parent == cur:
            break
        cur = cur.parent
    return next((str(item) for item in reversed(chain) if reparse(item)), None)


def existing_ancestor(path):
    cur = Path(path)
    while not cur.exists() and not reparse(cur):
        if cur.parent == cur:
            break
        cur = cur.parent
    return cur


def backup_preflight(backup_root, work, targets, target_reports, bundle_paths):
    """Stat-only backup root and free-space check; never reads config files."""
    base = Path(backup_root)
    result = {"root": str(base), "work": str(work), "margin_bytes": BACKUP_SPACE_MARGIN,
              "backup_bytes": 0, "free_bytes": None, "required_bytes": None,
              "existing_ancestor": None, "ok": False, "reasons": []}
    reasons = result["reasons"]
    if not base.is_absolute():
        reasons.append("backup_root_not_absolute")
    bad = reparse_chain(base)
    if bad:
        reasons.append(f"backup_root_reparse:{bad}")
    anchor = existing_ancestor(base)
    result["existing_ancestor"] = str(anchor)
    if anchor.exists() and not anchor.is_dir():
        reasons.append("backup_root_not_directory")
    for target, tr in zip(targets, target_reports):
        for change in tr["changes"]:
            rel = change["path"]
            if change["target_sha256"] is None or not should_backup(rel, bundle_paths):
                continue
            try:
                result["backup_bytes"] += (target / rel).stat().st_size
            except OSError:
                reasons.append(f"backup_source_unreadable:{rel}")
    result["required_bytes"] = result["backup_bytes"] + BACKUP_SPACE_MARGIN
    try:
        result["free_bytes"] = shutil.disk_usage(anchor).free
    except OSError as exc:
        reasons.append(f"backup_space_unavailable:{type(exc).__name__}")
    if result["free_bytes"] is not None and result["free_bytes"] < result["required_bytes"]:
        reasons.append(f"backup_space_insufficient:{result['free_bytes']}<{result['required_bytes']}")
    result["ok"] = not reasons
    return result


def source_tree(root):
    if reparse(root):
        raise ValueError("source_reparse_root")
    out = {}
    for name in ALLOW:
        p = root / name
        if reparse(p):
            raise ValueError(f"source_reparse:{name}")
        if p.is_file():
            out[name] = p
        elif p.is_dir():
            for f in p.rglob("*"):
                rel = f.relative_to(root)
                if reparse(f):
                    raise ValueError(f"source_reparse:{rel.as_posix()}")
                if f.is_file() and f.suffix != ".pyc" and not set(rel.parts) & SKIP:
                    out[rel.as_posix()] = f
    return out


def _load_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def model_contract(root):
    """Collect manifest-declared YOLO model files and validate class maps.

    Returns (files, errors, warnings, models_meta).  files maps rel path under
    models/ to source Path for every entry whose declared file exists.  A model
    declared in the manifest but missing on disk is an error for formal apply
    (caller decides) and a warning otherwise.
    """
    files, errors, warnings, models_meta = {}, [], [], []
    manifest_path = root / "knowledge" / "models_manifest.json"
    try:
        data = _load_json(manifest_path)
    except (OSError, ValueError) as exc:
        return {}, [f"models_manifest_unreadable:{type(exc).__name__}"], warnings, models_meta
    if not isinstance(data, dict) or not isinstance(data.get("models"), list):
        return {}, ["models_manifest_shape_invalid"], warnings, models_meta
    class_map_cache = {}
    for entry in data["models"]:
        if not isinstance(entry, dict):
            errors.append("model_entry_not_object")
            continue
        role, rel = entry.get("role"), entry.get("file")
        if not role or not isinstance(rel, str) or not rel.startswith(f"{MODEL_DIR}/"):
            errors.append(f"model_file_invalid:{role}")
            continue
        map_rel, meta = entry.get("class_map"), {"role": role, "file": rel,
                                                 "declared_nc": entry.get("num_classes"),
                                                 "sha256": entry.get("sha256")}
        if isinstance(map_rel, str):
            map_path = root / map_rel
            try:
                cmap = class_map_cache.setdefault(map_rel, _load_json(map_path))
                classes = cmap.get("classes")
                if not isinstance(classes, list) or not classes:
                    errors.append(f"class_map_empty:{map_rel}")
                else:
                    ids = [c.get("id") for c in classes if isinstance(c, dict)]
                    if ids != list(range(len(classes))):
                        errors.append(f"class_map_ids_not_contiguous:{map_rel}")
                    if entry.get("num_classes") not in (None, len(classes)):
                        errors.append(f"num_classes_mismatch:{role}")
                    cmap_task, cls0 = cmap.get("task"), classes[0] if classes else {}
                    if cmap_task == "classify" and "card_id" not in cls0:
                        errors.append(f"class_map_card_id_missing:{map_rel}")
                    meta["class_map"] = map_rel
                    meta["nc"] = len(classes)
            except (OSError, ValueError) as exc:
                errors.append(f"class_map_unreadable:{map_rel}:{type(exc).__name__}")
        src = root / rel
        if reparse(src) or not src.is_file():
            warnings.append(f"model_file_missing:{rel}")
            models_meta.append(meta)
            continue
        digest = fsha(src)
        declared = entry.get("sha256")
        if isinstance(declared, str) and declared != digest:
            errors.append(f"model_sha256_mismatch:{rel}")
        meta["sha256"] = digest
        files[rel] = src
        models_meta.append(meta)
    return files, errors, warnings, models_meta


def manifest(root, channel):
    text = (root / "plugin.toml").read_text(encoding="utf-8")
    source, want = tomllib.loads(text)["plugin"], DEV if channel == "dev" else FORMAL
    if channel == "dev":
        values = {"id": DEV, "name": "KARDS 猫娘陪玩（dev）",
                  "entry": "plugin.plugins.kards_companion_dev:KardsCompanionPlugin"}
        lines, section = [], None
        for line in text.splitlines(keepends=True):
            if line.lstrip().startswith("[") and "]" in line:
                section = line.strip()
            m = re.match(r"^(id|name|entry)\s*=", line) if section == "[plugin]" else None
            lines.append(f'{m.group(1)} = "{values[m.group(1)]}"\n'
                         if m and m.group(1) in values else line)
        text = "".join(lines)
    derived = tomllib.loads(text)["plugin"]
    if derived.get("id") != want:
        raise ValueError(f"manifest_id_{derived.get('id')}")
    return text.encode(), {k: [source.get(k), derived.get(k)] for k in ("id", "name", "entry")
                            if source.get(k) != derived.get(k)}, want


def fingerprint(files):
    h = hashlib.sha256()
    for rel in sorted(files):
        if rel != "plugin.toml":  # channel-specific manifest is excluded
            h.update(rel.encode() + b"\0" + sha(files[rel]).encode() + b"\n")
    return h.hexdigest()


def cards(root):
    path = root / "knowledge" / "cards_builtin.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return f"cards_unreadable:{type(exc).__name__}"
    if not isinstance(data, dict) or not isinstance(data.get("cards"), list):
        return "cards_shape_invalid"
    if len(data["cards"]) != 1083:
        return f"cards_count_{len(data['cards'])}"
    return None if fsha(path) == CARDS else "cards_sha256_mismatch"


def secret_line(path):
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return f"scan_unavailable:{type(exc).__name__}", None
    for m in SECRET.finditer(text):
        value = m.group(2)
        long_rand = len(value) >= 24 and re.search(r"[A-Za-z]", value) and re.search(r"\d", value)
        if not ENV_NAME.match(value) and (SECRET_VAL.search(value) or long_rand):
            return "flagged", text.count("\n", 0, m.start(2)) + 1
    return "clean", None


def config_hashes(target):
    base, out = target / "config", {}
    if base.is_dir() and not reparse(base):
        out = {f.relative_to(target).as_posix(): fsha(f) for f in sorted(base.rglob("*"))
               if f.is_file() and not reparse(f)}
    return out


def inspect(files, target, want):
    r = {"target": str(target), "issues": [], "changes": [], "stale": [], "config_hashes": {}}
    if target.exists():
        if reparse(target) or not target.is_dir():
            r["issues"].append("target_not_plain_directory")
            return r
        r["config_hashes"] = config_hashes(target)
        mp = target / "plugin.toml"
        try:
            got = tomllib.loads(mp.read_text(encoding="utf-8")).get("plugin", {}) if mp.is_file() else None
            if got is None and any(target.iterdir()):
                r["issues"].append("target_identity_unverifiable")
            elif got is not None and got.get("id") != want:
                r["issues"].append(f"target_id_{got.get('id')}_expected_{want}")
        except (OSError, tomllib.TOMLDecodeError) as exc:
            r["issues"].append(f"target_manifest_{type(exc).__name__}")
        r["stale"] = [f.relative_to(target).as_posix() for f in target.rglob("*")
                      if f.is_file() and f.suffix != ".pyc"
                      and not set(f.relative_to(target).parts) & SKIP
                      and not bad_chain(target, f.parent)
                      and f.relative_to(target).as_posix() not in files]
    elif not target.parent.is_dir() or reparse(target.parent):
        r["issues"].append("target_parent_missing_or_reparse")
    for rel, data in files.items():
        dest, src = target / rel, sha(data)
        if not dest.exists():
            r["changes"].append({"path": rel, "source_sha256": src, "target_sha256": None})
        elif bad_chain(target, dest.parent) or reparse(dest) or not dest.is_file():
            r["issues"].append(f"target_path_not_plain_file:{rel}")
        elif fsha(dest) != src:
            r["changes"].append({"path": rel, "source_sha256": src, "target_sha256": fsha(dest)})
    return r


def evidence(path, schema_required):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [f"evidence_unreadable:{type(exc).__name__}"], [], None
    if not isinstance(data, dict):
        return ["evidence_not_object"], [], None
    plugin = data.get("plugin") if isinstance(data.get("plugin"), dict) else {}
    delivery = data.get("delivery") if isinstance(data.get("delivery"), dict) else {}
    sdk = data.get("sdk") if isinstance(data.get("sdk"), dict) else {}
    sm = data.get("source_manifest") if isinstance(data.get("source_manifest"), dict) else {}
    art = data.get("artifact") if isinstance(data.get("artifact"), dict) else {}
    reasons = []
    if schema_required and not (isinstance(data.get("schema"), str) and data["schema"].endswith("/1")):
        reasons.append("acceptance_schema_not_v1")
    if data.get("plugin_loaded", plugin.get("loaded")) is not True:
        reasons.append("plugin_loaded_not_true")
    if data.get("delivery_submitted", delivery.get("submitted")) is not True:
        reasons.append("delivery_submitted_not_true")
    if data.get("sdk_accepted", sdk.get("accepted")) is not True:
        reasons.append("sdk_accepted_not_true")
    got = [data.get("source_manifest_fingerprint"), sm.get("fingerprint"), data.get("artifact_sha256"),
           art.get("sha256"), data.get("source_manifest") if isinstance(data.get("source_manifest"), str) else None]
    fingerprints = sorted({x for x in got if isinstance(x, str) and x})
    if not fingerprints:
        reasons.append("source_manifest_fingerprint_missing")
    return reasons, fingerprints, data.get("plugin_id", plugin.get("id"))


def atomic_write(dest, data):
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{dest.name}.", suffix=".tmp", dir=dest.parent)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, dest)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def finish(report, code, message):
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"[sync] {message}", file=sys.stderr)
    return code


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="read-only preflight (default)")
    ap.add_argument("--apply", action="store_true", help="write changes atomically")
    ap.add_argument("--channel", choices=("dev", "formal"), default="dev")
    ap.add_argument("--host-verified", action="store_true", help="formal only; a claim, not proof")
    ap.add_argument("--acceptance-report", type=Path, help="formal evidence: schema-v1 report JSON")
    ap.add_argument("--verified-manifest", type=Path, help="formal evidence: simplified JSON")
    ap.add_argument("--bundle-manifest", nargs="?", const=True, metavar="PATH",
                    help="explicit opt-in: verify bundle-manifest.json (or PATH) and include payload")
    ap.add_argument("--no-prune-stale", action="store_true",
                    help="keep stale allowlisted files in targets (default prunes with backup)")
    ap.add_argument("--no-require-models", action="store_true",
                    help="formal only: allow apply without both YOLO models present")
    ap.add_argument("--backup-root", type=Path,
                    help="absolute backup parent (default: system temp)")
    args = ap.parse_args(argv)
    if args.check and args.apply:
        ap.error("--check and --apply are mutually exclusive")
    if args.host_verified and args.channel != "formal":
        ap.error("--host-verified is formal-only")
    prune = args.apply and not args.no_prune_stale
    apply = args.apply
    root, base = Path(__file__).resolve().parent.parent, os.environ.get("LOCALAPPDATA", "")
    suffix, apply, errors = (DEV if args.channel == "dev" else FORMAL), args.apply, []
    report = {"schema": "neko-kards-sync/1", "mode": "apply" if apply else "check",
              "channel": args.channel, "errors": errors, "targets": [],
              "formal_gate": {"required_for_apply": args.channel == "formal",
                              "host_verified_flag": args.host_verified, "evidence": None,
                              "accepted": False, "reasons": []}}
    if not base or not Path(base).is_absolute():
        errors.append("localappdata_missing_or_relative")
        return finish(report, 2, "LOCALAPPDATA missing/relative; no file written")
    local = Path(base) / "N.E.K.O"
    targets = [local / "plugins" / suffix, local / ".neko-plugin-installations" / "plugins" / suffix]
    try:
        paths, (manifest_bytes, manifest_diff, expected) = source_tree(root), manifest(root, args.channel)
        files = {rel: p.read_bytes() for rel, p in paths.items()}
        files["plugin.toml"] = manifest_bytes
    except (OSError, ValueError, KeyError, tomllib.TOMLDecodeError) as exc:
        errors.append(f"source_unreadable:{type(exc).__name__}")
        return finish(report, 2, "source unreadable; no file written")
    model_paths, model_errors, model_warnings, models_meta = model_contract(root)
    for rel, p in model_paths.items():
        if rel in files:
            errors.append(f"model_path_collision:{rel}")
        else:
            files[rel] = p.read_bytes()
    errors.extend(model_errors)
    report["models"] = {"meta": models_meta, "warnings": model_warnings,
                        "require_for_formal": args.channel == "formal" and not args.no_require_models}
    if args.channel == "formal" and not args.no_require_models:
        roles = {m.get("role") for m in models_meta if m.get("file") in files}
        for want in ("ui_detect", "card_classify"):
            if want not in roles:
                errors.append(f"model_required_for_formal:{want}")
    bundle_paths = set()
    if args.bundle_manifest is not None:
        _, bundle, bundle_files, bundle_errors = bundle_input(root, args.bundle_manifest)
        bundle_paths = set(bundle_files)
        report["bundle"] = bundle
        for rel, data in bundle_files.items():
            if rel in files:
                errors.append(f"bundle_path_collision:{rel}")
            else:
                files[rel] = data
        errors.extend(bundle_errors)
    else:
        report["bundle"] = {"enabled": False, "reason": "explicit_bundle_flag_not_set"}
    fp, card_error, (scan, scan_at) = fingerprint(files), cards(root), secret_line(root / "__init__.py")
    if card_error:
        errors.append(card_error)
    if scan != "clean":
        errors.append(f"secret_literal_detected:__init__.py:{scan_at}" if scan == "flagged" else scan)
    report.update({"artifact_fingerprint": fp, "manifest_diff": manifest_diff,
                   "cards_check": card_error or "ok", "secret_scan": scan})
    for target in targets:
        tr, bad = inspect(files, target, expected), bad_chain(local, target)
        if bad:
            tr["issues"].append(f"reparse_point:{bad}")
        report["targets"].append(tr)
    backup_base = args.backup_root or Path(tempfile.gettempdir())
    work = backup_base / f"neko-kards-sync-{args.channel}-{datetime.now():%Y%m%d-%H%M%S}"
    preflight = backup_preflight(backup_base, work, targets, report["targets"], bundle_paths)
    report["backup_preflight"] = preflight
    if apply and not preflight["ok"]:
        errors.extend(f"backup_preflight:{reason}" for reason in preflight["reasons"])
    gate, ev_path = report["formal_gate"], args.acceptance_report or args.verified_manifest
    if args.channel == "formal":
        if not args.host_verified:
            gate["reasons"].append("host_verified_flag_missing")
        if ev_path is None:
            gate["reasons"].append("acceptance_evidence_missing")
        elif args.acceptance_report and args.verified_manifest:
            gate["reasons"].append("choose_one_evidence_file")
        else:
            gate["evidence"] = str(ev_path)
            reasons, fps, plugin_id = evidence(ev_path, args.acceptance_report is not None)
            gate["reasons"] += reasons
            if fps and fps != [fp]:
                gate["reasons"].append("artifact_hash_mismatch")
            if plugin_id not in (None, DEV):
                gate["reasons"].append("evidence_plugin_id_not_dev")
        if apply and not gate["reasons"]:
            dev_files = dict(files)
            dev_files["plugin.toml"] = manifest(root, "dev")[0]
            dev_reports = [inspect(dev_files, t, DEV) for t in
                           (local / "plugins" / DEV, local / ".neko-plugin-installations" / "plugins" / DEV)]
            if any(d["issues"] or d["changes"] for d in dev_reports):
                gate["reasons"].append("dev_targets_not_in_sync_with_source")
        gate["accepted"] = apply and not gate["reasons"]
    if apply and args.channel == "formal" and gate["reasons"]:
        return finish(report, 3, "formal apply blocked: " + ", ".join(gate["reasons"]))
    if errors or any(t["issues"] for t in report["targets"]):
        return finish(report, 2, "preflight refused; no target file was written")
    if not apply:
        return finish(report, 0, "check only; no file written")
    for target, tr in zip(targets, report["targets"]):
        for change in tr["changes"]:
            if change["target_sha256"] and change["path"].endswith(".py"):
                status, line = secret_line(target / change["path"])
                if status != "clean":
                    errors.append(f"target_backup_scan:{change['path']}:{line}:{status}")
    if errors:
        return finish(report, 2, "backup refused; no target file was written")
    report["backup"] = []
    for target, tr in zip(targets, report["targets"]):
        label = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(target.relative_to(local)))
        for change in tr["changes"]:
            rel = change["path"]
            if change["target_sha256"] is not None and should_backup(rel, bundle_paths):
                old = (target / rel).read_bytes()
                dest = work / "backup" / label / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(old)
                report["backup"].append({"path": rel, "sha256": sha(old), "backup": str(dest)})
        target.mkdir(parents=True, exist_ok=True)
        for change in tr["changes"]:
            atomic_write(target / change["path"], files[change["path"]])
        tr["pruned"] = []
        if prune and tr["stale"]:
            for rel in tr["stale"]:
                if not prunable(rel):
                    continue
                victim = target / rel
                if bad_chain(target, victim.parent) or reparse(victim) or not victim.is_file():
                    errors.append(f"prune_target_not_plain_file:{rel}")
                    continue
                old = victim.read_bytes()
                dest = work / "backup" / label / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(old)
                victim.unlink()
                report["backup"].append({"path": rel, "sha256": sha(old), "backup": str(dest), "pruned": True})
                tr["pruned"].append(rel)
            for d in sorted({(target / rel).parent for rel in tr["pruned"]},
                            key=lambda p: len(p.parts), reverse=True):
                try:
                    if d.is_dir() and not any(d.iterdir()) and d != target and d.parent != target:
                        d.rmdir()
                except OSError:
                    pass
        tr["config_unchanged"] = config_hashes(target) == tr["config_hashes"]
        after = inspect(files, target, expected)
        leftover = [rel for rel in after["stale"] if prunable(rel)]
        tr["after"] = {"changes": len(after["changes"]), "issues": after["issues"],
                       "stale_prunable": len(leftover)}
        if after["changes"] or after["issues"] or (prune and leftover) or not tr["config_unchanged"]:
            errors.append(f"post_write_verification_failed:{target}")
    report["applied"] = not errors
    report["report_path"] = str(work / "report.json")
    work.mkdir(parents=True, exist_ok=True)
    (work / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                                      encoding="utf-8")
    return finish(report, 0 if not errors else 4,
                  f"apply {'ok' if not errors else 'failed'}; report={work / 'report.json'}")


if __name__ == "__main__":
    sys.exit(main())
