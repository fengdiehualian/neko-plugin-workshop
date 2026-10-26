"""Freeze Stage 3 sources and evidence; NEVER build/install or renew old tests.

The R4 identity check is deliberately retained in its red XML. Its yesterday
hashes cannot certify today's authorized changes; compare all other frozen
files here instead, with a narrow explicit change set.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "dist/request-chain-candidate-20261021-201831/candidate.json"
CHANGED = {"runtime/consultation_contract.py", "runtime/guidance.py", "runtime/semantic_guidance.py",
           "runtime/service_contract.py", "runtime/answer_message.py", "runtime/diagnostics.py", "runtime/build_info.py"}
NEW = {"runtime/saved_cone.py", "runtime/cone_message.py", "tests/test_recording_stage3_sources.py",
       "tests/test_recording_stage3_cone_delivery.py", "scripts/trace_recording_cone_stage3.py",
       "scripts/replay_saved_cone_stage3.py", "scripts/freeze_recording_stage3.py"}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def xml_result(path):
    suites = ET.parse(path).getroot()
    cases = list(suites.iter("testcase"))
    failures = [case for case in cases if case.find("failure") is not None or case.find("error") is not None]
    return {"file": path.relative_to(ROOT).as_posix(), "sha256": sha(path), "tests": len(cases),
            "failed": len(failures), "skipped": sum(case.find("skipped") is not None for case in cases),
            "failing_names": [case.get("name") for case in failures]}


def freeze(report, test_report):
    from plugin.plugins.hsr_companion.runtime.build_info import BUILD_REVISION, PLUGIN_VERSION

    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    old = baseline["source_hashes"]
    changed = {name for name, digest in old.items() if not (ROOT / name).is_file() or sha(ROOT / name) != digest}
    if changed != CHANGED:
        raise RuntimeError("Unexpected source delta: " + repr(sorted(changed ^ CHANGED)))
    installed = Path("C:/Users/USER/AppData/Local/N.E.K.O/.neko-plugin-installations/plugins/hsr_companion")
    installed_diff = [name for name, digest in old.items()
                      if not (installed / name).is_file() or sha(installed / name) != digest]
    tests = xml_result(test_report)
    if tests["failed"] or tests["skipped"] or installed_diff:
        raise RuntimeError("Do not freeze a failed/skipped batch or changed installation")
    sources = {name: sha(ROOT / name) for name in sorted(set(old) | NEW)}
    evidence_names = ["stage3-source-red-20261022.xml", "stage3-source-clarification-red-20261022.xml",
        "stage3-cone-fit-body-before-20261022.xml", "stage3-joint-protection-20261022.xml",
        "stage3-real-archive-replay-20261022.json", "stage3-live-cone-before-20261022.json",
        "stage3-live-cone-trace-20261022.json", "stage3-live-cone-prepared-20261022.json",
        "stage3-live-cone-resident-20261022.json", "stage3-live-cone-resident-readback-20261022.json",
        "stage3-final-live-cone-20261022.json"]
    evidence = {name: sha(ROOT / "dist" / name) for name in evidence_names}
    live = json.loads((ROOT / "dist/stage3-final-live-cone-20261022.json").read_text(encoding="utf-8"))
    result = {"schema_version": 1, "stage": 3, "build_revision": BUILD_REVISION, "plugin_version": PLUGIN_VERSION,
        "frozen_at": datetime.now(timezone.utc).isoformat(), "installed": False,
        "baseline": {"file": BASELINE.relative_to(ROOT).as_posix(), "sha256": sha(BASELINE),
                     "source_file_count": len(old)},
        "changed_baseline_files": sorted(changed), "new_files": sorted(NEW), "source_hashes": sources,
        "unchanged_baseline_count": len(old) - len(changed), "installed_baseline_differences": installed_diff,
        "tests": tests, "evidence_sha256": evidence,
        "historical_freeze_check": {"status": "old_identity_check_rejects_authorized_stage3_edits",
            "failed_test": "test_r4_freezes_both_r3_edits_and_r1_r2_protected_sources",
            "original_failure_retained": True, "old_manifest_or_test_changed": False,
            "replacement": "Every other one of the 312 baseline files is verified unchanged here"},
        "current_cone_live": {"build_revision": live.get("build_revision"), "direct_seconds": live.get("direct_seconds"),
            "followup_status": live.get("followup_status"), "projection": live.get("live_packet_projection_dry_run"),
            "restored": live.get("restored"), "failure": live.get("failure"),
            "installed_changed": live.get("installed_changed"), "helper_executable_changed": live.get("helper_executable_changed")},
        "remaining_blockers": [
            "Original 2026-10-21 missing-current-cone-evidence failure not reproduced; no invented OCR/proof repair",
            "Autonomous model source selection and final cat paraphrase not tested; a wrongly declared current_cone is not silently rewritten",
            "Main LLM upstream timeout origin unresolved; host retry/config deliberately unchanged"],
        "acceptance_limits": ["No reliability rate or latency percentile from these prepared-page samples",
            "Real cone queries were direct-ready; naturally triggered real cone supplement remains untriggered",
            "Notification formatter tested with real live packet, but never sent to the host cat",
            "No installation, OCR redesign, helper modification or whole-route user retest"]}
    if (live.get("build_revision") != BUILD_REVISION or not live.get("restored") or live.get("failure")
            or live.get("installed_changed") or live.get("helper_executable_changed")):
        raise RuntimeError("Final live environment/source guard did not pass; retain evidence, do not freeze")
    with report.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", required=True)
    parser.add_argument("--tests", required=True)
    args = parser.parse_args()
    report, tests = Path(args.report).resolve(), Path(args.tests).resolve(strict=True)
    if not report.is_relative_to(ROOT / "dist") or report.exists() or not tests.is_relative_to(ROOT / "dist"):
        raise ValueError("Use plugin-owned reports, with a new output path")
    result = freeze(report, tests)
    print(json.dumps({"build": result["build_revision"], "tests": result["tests"],
                      "unchanged_baseline_count": result["unchanged_baseline_count"], "installed": False}))
