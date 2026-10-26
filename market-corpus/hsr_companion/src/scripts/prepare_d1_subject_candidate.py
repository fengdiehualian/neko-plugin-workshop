"""Freeze/check/package the D1 candidate only. NO host calls or installation."""
from __future__ import annotations

import hashlib
import json
import shutil
import zipfile
from datetime import datetime
from pathlib import Path


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    from plugin.neko_plugin_cli.commands.validate_cmd import validate_plugin_dir
    from plugin.neko_plugin_cli.core import build_plugin, inspect_package

    root = Path(__file__).resolve().parents[1]
    revision = "demo-d1-subject-fit-20261020.1"
    assert revision in (root / "runtime/build_info.py").read_text(encoding="utf-8")
    baseline = root / "dist/hsr_companion-0.9.6-demo-page-first-pending-20261020.2.neko-plugin"
    allowed = {"__init__.py", "DESIGN.md", "runtime/build_info.py", "runtime/consultation.py",
               "runtime/guidance.py", "runtime/semantic_guidance.py", "runtime/answer_message.py",
               "runtime/service_contract.py", "runtime/diagnostics.py", "tests/test_page_first_protocol_20261020.py"}
    additions = {"runtime/consultation_contract.py", "tests/test_d1_subject_contract_20261020.py",
                 "scripts/prepare_d1_subject_candidate.py"}
    prefix = "payload/plugins/hsr_companion/"
    with zipfile.ZipFile(baseline) as archive:
        old_hashes = {name[len(prefix):]: hashlib.sha256(archive.read(name)).hexdigest()
                      for name in archive.namelist() if name.startswith(prefix) and not name.endswith("/")}
    changes = [name for name, value in old_hashes.items() if digest(root / name) != value]
    assert not set(changes) - allowed, "Unrelated source drift: " + repr(set(changes) - allowed)
    run = root / "dist" / ("d1-subject-fit-candidate-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
    run.mkdir()
    stage = run / "hsr_companion"
    source_hashes = {}
    for name in sorted(set(old_hashes) | additions):
        source, target = (root / name).resolve(strict=True), (stage / name).resolve()
        assert source.is_relative_to(root) and target.is_relative_to(stage)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        source_hashes[name] = digest(source)
    issues = validate_plugin_dir(stage)
    assert not any(level == "error" for level, _ in issues), issues
    package = run / ("hsr_companion-0.9.6-" + revision + ".neko-plugin")
    build_plugin(stage, package)
    inspected = inspect_package(package)
    assert inspected.package_id == "hsr_companion" and inspected.payload_hash_verified is True
    installed = Path("C:/Users/USER/AppData/Local/N.E.K.O/.neko-plugin-installations/plugins/hsr_companion")
    installed_unchanged = installed.exists() and all(digest(installed / name) == value for name, value in old_hashes.items())
    report = {"goal": "D1 demand, subject and sourced assessment handoff", "build_revision": revision,
        "plugin_version": "0.9.6", "installed": False, "host_messages_sent": False, "live_capture_performed": False,
        "baseline": str(baseline), "baseline_sha256": digest(baseline), "changed_source_files": sorted(changes),
        "new_source_files": sorted(additions), "all_other_baseline_files_unchanged": True,
        "installed_plugin_still_matches_previous_frozen_package": installed_unchanged,
        "package": str(package), "package_sha256": digest(package), "payload_hash_verified": True,
        "check": {"errors": sum(level == "error" for level, _ in issues),
                  "warnings": [message for level, message in issues if level == "warning"]},
        "source_hashes": source_hashes,
        "evidence_limits": ["Historical user-confirmed values reconstructed as structured facts, not historical pixel replay.",
            "Formal tool and receiving formatter verified in isolated tests, not a live cat response.",
            "Actual historical wrong build_guide call retained; absent or jointly wrong semantic declarations cannot be auto-corrected.",
            "Real host tool choice and speech require separately authorized installation acceptance.",
            "Real confirmation/followup not triggered by two direct answers must stay not_exercised."],
        "next_acceptance": [
            {"action": "Ask whether the current relic suits 绯英 in natural language.",
             "hypothesis": "Model selects assessment and binds the character; answer evaluates actual rows and fixed main-stat rules."},
            {"action": "Switch one relic and ask 这个呢; only if naturally pending, reply 好 and keep the page.",
             "hypothesis": "Same character and purpose, new item facts; a real followup completes that assessment without deadline renewal."}],
        "remaining_risks": ["Autonomous model declarations and final speech not yet accepted.",
            "OCR completeness and changed-item delivery latency are unchanged known limitations.",
            "Unsolicited speech arbitration and historical global invalidation causality remain deferred."]}
    with (run / "result.json").open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    print(json.dumps({k: report[k] for k in ("build_revision", "installed", "package", "package_sha256", "check",
                     "installed_plugin_still_matches_previous_frozen_package")}, ensure_ascii=True))
    print(str(run / "result.json"))


if __name__ == "__main__":
    main()
