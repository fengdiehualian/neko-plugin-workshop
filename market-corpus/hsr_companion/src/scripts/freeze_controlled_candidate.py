"""Freeze a local, verified plugin/helper pair; never install or publish it."""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime, timezone
from pathlib import Path


def sha(data):
    return hashlib.sha256(data).hexdigest()


def pytest_result(path):
    xml = ET.parse(path).getroot()
    suites = [xml] if xml.tag == "testsuite" else list(xml.findall("testsuite"))
    counts = {key: sum(int(s.get(key, 0)) for s in suites) for key in ("tests", "failures", "errors", "skipped")}
    if not counts["tests"] or counts["failures"] or counts["errors"]:
        raise ValueError(f"Regression is not green: {path.name}: {counts}")
    return {"file": path.name, "sha256": sha(path.read_bytes()), **counts}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--helper-dir", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    package, helper, report = args.package.resolve(), args.helper_dir.resolve(), args.report.resolve()
    if not all(p.is_relative_to(root / "dist") for p in (package, helper, report)) or report.exists():
        raise ValueError("Use existing plugin-owned dist artifacts and a new report")
    build = ast.parse((root / "runtime/build_info.py").read_text(encoding="utf-8"))
    identity = {n.targets[0].id: ast.literal_eval(n.value) for n in build.body if isinstance(n, ast.Assign)}
    if identity != {"PLUGIN_VERSION": "0.9.6", "BUILD_REVISION": "stage2a-20261018.1"}:
        raise ValueError("Candidate build differs from expected tested identity")
    tests = [pytest_result(root / "dist" / name) for name in
             ("admission-frozen-20261018.xml", "admission-safety-20261018.xml")]
    stress_path = root / "dist/cache-receipt-admission-fixed-20261018.json"
    stress = json.loads(stress_path.read_text(encoding="utf-8"))
    if (stress["iterations"], stress["failure_count"], stress["receipt_iterations"], stress["receipt_failure_count"]) != (1000, 0, 1000, 0):
        raise ValueError("Fixed repeated cache/receipt diagnosis did not pass")
    prefix = "payload/plugins/hsr_companion/"
    payload_hashes = {}
    with zipfile.ZipFile(package) as archive:
        for name in archive.namelist():
            if not name.startswith(prefix) or name.endswith("/"):
                continue
            relative = Path(name.removeprefix(prefix))
            if (relative.is_absolute() or ".." in relative.parts
                    or any(p in {"dist", "build", "__pycache__"} or p.startswith(".pytest_tmp") for p in relative.parts)):
                raise ValueError("Unexpected build/test artifact in plugin payload")
            data = archive.read(name)
            if data != (root / relative).read_bytes():
                raise ValueError(f"Package differs from final source: {relative}")
            payload_hashes[relative.as_posix()] = sha(data)
    for required in ("plugin.toml", "__init__.py", "runtime/build_info.py", "runtime/fact_lifecycle.py",
                     "runtime/query_reuse.py", "runtime/review_policy.py", "ui/panel_v09.tsx"):
        if required not in payload_hashes:
            raise ValueError(f"Missing package file: {required}")
    executable = helper / "hsr-vision-assistant.exe"
    executable_hash = sha(executable.read_bytes())
    if executable_hash != "99eac5b4698c2cf4e3abca3e5d874ed0c78e5ae3a3ab4eddc63a0a0e93a50271":
        raise ValueError("Helper is not the previously live-tested executable")
    helper_manifest = json.loads((helper / "hsr-vision-assistant-local-install.json").read_text(encoding="utf-8"))
    if helper_manifest["version"] != "0.9.3":
        raise ValueError("Unexpected helper version")
    for name, expected in helper_manifest["source_sha256"].items():
        if sha((root / "assistant_runtime" / name).read_bytes()) != expected:
            raise ValueError("Helper source differs from frozen binary build sources")
    helper_zip = helper / "hsr-vision-assistant-win-x64.zip"
    helper_zip_hash = sha(helper_zip.read_bytes())
    if helper_zip_hash != helper_manifest["sha256"]:
        raise ValueError("Helper ZIP differs from its manifest")
    with zipfile.ZipFile(helper_zip) as archive:
        entries = [n for n in archive.namelist() if n.endswith("/hsr-vision-assistant.exe") or n == "hsr-vision-assistant.exe"]
        if len(entries) != 1 or sha(archive.read(entries[0])) != executable_hash:
            raise ValueError("Helper ZIP executable differs from tested executable")
    old_live = root / "dist/label-contract-live-v1-20261017.json"
    previous = json.loads(old_live.read_text(encoding="utf-8"))
    unchanged = ("runtime/screen_facts.py", "adapters/local_vision.py", "adapters/relic_values.py",
                 "adapters/text_verification.py", "detectors/relic_geometry.py", "detectors/relic_labels.py")
    if any(payload_hashes[name] != previous["source_hashes"][name] for name in unchanged):
        raise ValueError("Recognition components changed since the historical real-image batch")
    result = {"schema_version": 1, "frozen_at": datetime.now(timezone.utc).isoformat(),
        "plugin_version": identity["PLUGIN_VERSION"], "build_revision": identity["BUILD_REVISION"],
        "status": "ready_for_controlled_installation_not_installed_not_published",
        "admission": {"partial_allowed_if_explicit": True, "wrong_confirmed_values_allowed": False,
            "old_item_mixing_allowed": False, "host_speech_validated": False,
            "current_build_real_game_test": False, "historical_live_gate": "5/6_not_met_unchanged"},
        "plugin": {"file": str(package), "sha256": sha(package.read_bytes()), "size_bytes": package.stat().st_size,
            "payload_source_match": True, "payload_file_count": len(payload_hashes), "payload_sha256": payload_hashes},
        "helper": {"version": "0.9.3", "file": str(executable), "sha256": executable_hash,
            "archive": str(helper_zip), "archive_sha256": helper_zip_hash, "source_sha256": helper_manifest["source_sha256"]},
        "tests": tests, "repeat_diagnosis": {"file": stress_path.name, "sha256": sha(stress_path.read_bytes()),
            "cache_attempts": 1000, "cache_failures": 0, "receipt_attempts": 1000, "receipt_failures": 0},
        "historical_live_evidence": {"file": old_live.name, "sha256": sha(old_live.read_bytes()),
            "unchanged_recognition_components": list(unchanged)},
        "known_limits": ["Occasional low-confidence labels yield explicit partial equipment results.",
            "Current-build cat paraphrasing and natural partial responses still require installation acceptance.",
            "No claim of long-term stability or complete page coverage."]}
    with report.open("x", encoding="utf-8") as output:
        json.dump(result, output, ensure_ascii=False, indent=2)
    print(json.dumps({"manifest": str(report), "build_revision": result["build_revision"],
        "package_size_bytes": result["plugin"]["size_bytes"], "payload_file_count": len(payload_hashes),
        "package_source_match": True, "helper_matches_tested_binary": True}))


if __name__ == "__main__":
    main()
