from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from neko_answer_isolated_matrix import _aggregate_attempts, _attempt_summary, _run, _verified_callback_tools

CASE = "constructed_round_v1"


def baseline(lane):
    evidence = json.loads((Path(__file__).parents[1] / ".github/e2e-evidence/v0.4.1.json").read_text(encoding="utf-8"))
    return evidence["matrix"]["cases"][0]["lanes"][lane]


def attempt(value, lane, index=1):
    return {
        "attempt": index, "status": value["probe"]["status"],
        "probe": copy.deepcopy(value["probe"]), "cleanup": dict(value["cleanup"]),
        **_attempt_summary(value["probe"], lane=lane, case_id=CASE),
    }


def test_real_lifecycle_does_not_require_passive_context_or_callback():
    lane = baseline("lifecycle")
    assert "route" not in lane["probe"]["cases"][0]
    result = _aggregate_attempts([attempt(lane, "lifecycle")])
    assert result["status"] == "PASS"
    assert result["repeats"][0]["snapshot_pass"] is True
    assert result["repeats"][0]["callback_pass"] is None
    assert result["metrics"]["callback_total"] == 0


def test_real_round_answer_without_model_call_keeps_callback_proof():
    lane = baseline("query")
    lane["probe"]["cases"][0]["route"]["tool_calls"] = []
    result = _aggregate_attempts([attempt(lane, "query")])
    assert result["status"] == "PASS"
    assert result["metrics"]["callback_passed"] == 1
    assert result["metrics"]["model_tool_called"] == 0


@pytest.mark.parametrize("key", ["probe_stopped", "main_stopped", "memory_stopped", "ports_released", "storage_removed"])
def test_earlier_cleanup_failure_cannot_be_hidden_by_later_success(key):
    first = attempt(baseline("query"), "query")
    last = attempt(baseline("query"), "query", 2)
    first["cleanup"][key] = False
    result = _aggregate_attempts([first, last])
    assert result["status"] == "FAIL"
    assert result["cleanup"][key] is False
    assert result["repeats"][0]["deterministic_pass"] is False
    assert result["probe_attempt"] == 2
    assert result["probe"] == last["probe"]


def test_missing_cleanup_fields_are_not_vacuously_successful():
    first = attempt(baseline("query"), "query")
    first["cleanup"] = {}
    result = _aggregate_attempts([first])
    assert result["status"] == "FAIL"
    assert not any(result["cleanup"].values())


def test_every_callback_proof_participates_in_coverage():
    first_value = baseline("query")
    first_value["probe"]["tool_callback_proofs"] = []
    first = attempt(first_value, "query")
    last = attempt(baseline("query"), "query", 2)
    result = _aggregate_attempts([first, last])
    assert result["status"] == "FAIL"
    assert result["metrics"]["callback_passed"] == 1
    assert _verified_callback_tools([{"lanes": {"query": result}}]) == []
    assert result["repeats"][0]["probe"]["tool_callback_proofs"] == []


def test_answers_and_capture_are_counted_separately_from_deterministic_status():
    attempts = []
    for index, (status, reasons) in enumerate([
        ("PASS", []), ("FAIL", ["board_card_missing"]),
        ("UNVERIFIED", ["answer_association_ambiguous"]),
        ("UNVERIFIED", ["answer_capture_incomplete"]),
    ], 1):
        value = baseline("query")
        case = value["probe"]["cases"][0]
        case["answer_observation_status"] = status
        case["answer_observation_reason_codes"] = reasons
        attempts.append(attempt(value, "query", index))
    result = _aggregate_attempts(attempts)
    assert result["status"] == "PASS"
    assert result["metrics"]["answer_verified"] == 1
    assert result["metrics"]["answer_failed"] == 1
    assert result["metrics"]["answer_unverified"] == 2
    assert result["metrics"]["capture_incomplete"] == 1


def test_capture_exception_reason_is_counted_without_answer_grading():
    value = baseline("lifecycle")
    value["probe"]["status"] = "ERROR"
    case = value["probe"]["cases"][0]
    case["answer_observation_status"] = "UNVERIFIED"
    case.pop("answer_observation_reason_codes", None)
    case["reason_codes"] = ["lifecycle_turn_incomplete"]
    result = _aggregate_attempts([attempt(value, "lifecycle")])
    assert result["status"] == "FAIL"
    assert result["metrics"]["answer_unverified"] == 1
    assert result["metrics"]["answer_failed"] == 0
    assert result["metrics"]["capture_incomplete"] == 1


@pytest.mark.parametrize("count", [0, -1, 21, None, True, 1.5, "invalid"])
def test_invalid_repeat_count_is_rejected_before_hosts_start(count):
    report, code = _run(SimpleNamespace(repeats=count))
    assert code == 1
    assert report["reason_code"] == "invalid_repeats"


def test_runner_aggregates_early_process_failure_across_all_repeats(tmp_path, monkeypatch):
    import neko_answer_isolated_matrix as matrix

    neko = tmp_path / "neko"
    (neko / "app").mkdir(parents=True)
    log = tmp_path / "Power.log"
    log.write_text("synthetic checkpoint", encoding="utf-8")

    def prepare(_source, destination, _assets, **kwargs):
        destination.mkdir(parents=True)
        return {"source_export": {}, "chat_assets": {}}

    calls = []

    def run_case(**kwargs):
        lane = kwargs["lane"]
        data = baseline(lane)
        calls.append(lane)
        if len(calls) == 1:
            data["cleanup"]["main_stopped"] = False
        return data["probe"], data["cleanup"]

    monkeypatch.setattr(matrix, "_prepare_runtime", prepare)
    monkeypatch.setattr(matrix, "_run_isolated_case", run_case)
    monkeypatch.setattr(matrix, "_neko_source_identity", lambda *_: {"revision": "c" * 40, "clean": True})
    monkeypatch.setattr(matrix, "_runtime_tree_fingerprint", lambda *_: {"sha256": "a" * 64, "file_count": 1})
    monkeypatch.setattr(matrix, "_runtime_versions", lambda *_: {"app_version": "test", "sdk_version": "test"})
    monkeypatch.setattr(matrix, "_python_runtime_identity", lambda *_: {})
    monkeypatch.setattr("release_evidence.source_fingerprint", lambda *_: "d" * 64)
    report, code = _run(SimpleNamespace(
        repeats=2, single_case=True, case=[(CASE, "20346", str(log))], lifecycle_edge=[],
        neko_root=str(neko), neko_python=sys.executable, runtime_assets_dir=str(tmp_path),
        storage_template=str(tmp_path), role="test",
    ))
    assert calls == ["lifecycle", "lifecycle", "query", "query"]
    assert code == 1
    assert report["status"] == "FAIL"
    assert report["cleanup"]["all_processes_stopped"] is False
    assert report["cases"][0]["lanes"]["lifecycle"]["cleanup"]["main_stopped"] is False
