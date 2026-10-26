from __future__ import annotations

import asyncio
import copy
import json
from types import SimpleNamespace

import pytest
from plugin.plugins.hsr_companion.runtime.diagnostic_export import export_diagnostic
from plugin.plugins.hsr_companion.runtime.diagnostics import build_diagnostic_report
from plugin.plugins.hsr_companion.tests.test_current_observation import analysis, plugin_fixture
from plugin.sdk.plugin import Err, unwrap


def inputs():
    return dict(
        observer={"enabled": True, "state": "observing", "proactive_enabled": True},
        assistant={"installed": True, "connected": True, "running": True, "game": {"running": True}},
        evidence={"fresh": True}, data_pack={"installed": True}, avatar_pack={"installed": True},
        last_tool={}, speech={"pending_count": 0}, message_path={"available": True},
    )


def test_report_allowlist_cannot_export_chat_game_names_paths_or_errors():
    values = inputs()
    secret = "DO_NOT_EXPORT_character_uid_chat_path_token"
    for name in ("observer", "assistant", "evidence", "data_pack", "avatar_pack", "speech", "message_path"):
        values[name].update(raw=secret, message=secret, last_error=secret, resource_root=secret)
    values["last_tool"] = {"query": secret, "at": secret, "refresh_diagnostics": {
        "outcome": secret, "wait_seconds": float("nan"), "stages": [{"stage": secret, "reason": secret,
                                                                  "after_seconds": float("inf"), "game_text": secret}],
    }}
    before = copy.deepcopy(values)
    result = build_diagnostic_report(**values)
    assert secret not in json.dumps(result)
    snapshot = json.loads(result["copy_text"])
    assert snapshot["last_query"]["wait_seconds"] is None
    assert snapshot["last_query"]["at"] is None
    assert snapshot["last_query"]["stages"][0]["stage"] == "unknown"
    assert values["assistant"] == before["assistant"]


def test_stopped_observation_is_not_marked_as_a_fault_or_current_fact():
    values = inputs()
    values["observer"] = {"enabled": False, "state": "stopped"}
    result = build_diagnostic_report(**values)
    assert result["checks"][0] == {"name": "observation", "level": "info", "detail": "stopped"}
    assert next(check for check in result["checks"] if check["name"] == "facts")["detail"] == "unavailable"
    assert not json.loads(result["copy_text"])["current_observation"]["fresh"]


def test_report_retains_bounded_timing_but_never_claims_playback():
    values = inputs()
    values["last_tool"] = {"refresh_diagnostics": {"outcome": "timeout", "wait_seconds": 20.031,
                                                   "stages": [{"stage": "analysis_finished", "processing_seconds": 10.922}] * 80}}
    result = json.loads(build_diagnostic_report(**values)["copy_text"])
    assert len(result["last_query"]["stages"]) == 48
    assert result["last_query"]["wait_seconds"] == 20.031
    assert result["speech"]["ack_scope"] == "sdk_submission_only"
    assert not result["speech"]["can_retract_submitted"]


def test_report_preserves_resident_worker_scope_without_raw_payload():
    values = inputs()
    values["last_tool"] = {"refresh_diagnostics": {"outcome": "timeout", "stages": [
        {"stage": "query_joined_worker", "scope": "full", "worker_elapsed_seconds": 7.2, "raw": "PRIVATE"},
        {"stage": "analysis_finished", "scope": "PRIVATE", "health_polls": 4, "frame_transfers": 1},
    ]}}
    result = json.loads(build_diagnostic_report(**values)["copy_text"])
    stages = result["last_query"]["stages"]
    assert stages[0]["scope"] == "full" and stages[0]["worker_elapsed_seconds"] == 7.2
    assert stages[1]["scope"] == "unknown"
    assert stages[1]["health_polls"] == 4 and stages[1]["frame_transfers"] == 1
    assert "PRIVATE" not in json.dumps(result)


def test_diagnosis_does_not_restore_observer_download_run_ocr_or_speak(tmp_path):
    owner, values, sent = plugin_fixture(tmp_path)
    owner._observer_status = {"enabled": False, "state": "stopped"}
    values["last_local_vision"] = analysis(scene="menu")
    values["tool_audit"] = [{"at": "2026-10-11T12:02:05+00:00", "query": "private"}]
    owner._avatars = SimpleNamespace(status=lambda: {"installed": True})
    owner._message_path_ready = lambda: {"available": True}

    async def health():
        return {"connected": False, "installed": True}

    def forbidden(*args, **kwargs):
        raise AssertionError("Diagnosis must be read-only")

    owner._assistant_status = health
    owner._start_observer_task = forbidden
    owner._start_data_pack_task = forbidden
    owner._note_speech_query = forbidden
    before = copy.deepcopy(values)
    result = unwrap(asyncio.run(owner.diagnose_companion()))
    assert result["diagnostic"]["copy_text"]
    assert values == before
    assert sent == []
    assert owner._observer_status == {"enabled": False, "state": "stopped"}


def test_export_writes_utf8_json_without_overwriting_previous_files(tmp_path):
    report = build_diagnostic_report(**inputs())
    directory = tmp_path / "diagnostics"
    first = export_diagnostic(directory, report)
    second = export_diagnostic(directory, report)
    from pathlib import Path
    assert first["path"] != second["path"]
    assert len(list(directory.glob("*.json"))) == 2
    assert Path(first["path"]).parent == directory.resolve()
    assert json.loads(Path(first["path"]).read_text(encoding="utf-8")) == json.loads(report["copy_text"])
    assert first["checked_at"] == report["checked_at"]
    assert first["path"] not in Path(first["path"]).read_text(encoding="utf-8")


@pytest.mark.parametrize("text", ["not JSON", "[]", '"' + "a" * 131072 + '"'],
                         ids=["malformed", "not-object", "oversized"])
def test_export_rejects_invalid_or_oversized_report_before_creating_files(tmp_path, text):
    directory = tmp_path / "diagnostics"
    with pytest.raises(ValueError):
        export_diagnostic(directory, {"copy_text": text})
    assert not directory.exists()


def test_export_action_uses_only_server_snapshot_and_plugin_data_directory(tmp_path):
    owner, values, sent = plugin_fixture(tmp_path)
    report = build_diagnostic_report(**inputs())

    async def diagnose():
        from plugin.sdk.plugin import Ok
        return Ok({"diagnostic": report})

    owner.diagnose_companion = diagnose
    owner.data_path = lambda *parts: tmp_path.joinpath(*parts)
    before = copy.deepcopy(values)
    # Even direct entry callers cannot select an output path or report text.
    result = unwrap(asyncio.run(owner.export_companion_diagnostic(path="outside", copy_text="PRIVATE")))
    from pathlib import Path
    content = Path(result["export"]["path"]).read_text(encoding="utf-8")
    assert json.loads(content) == json.loads(report["copy_text"])
    assert "PRIVATE" not in content
    assert values == before
    assert sent == []


def test_export_disk_failure_is_not_reported_as_success(tmp_path):
    owner, _, sent = plugin_fixture(tmp_path)
    report = build_diagnostic_report(**inputs())

    async def diagnose():
        from plugin.sdk.plugin import Ok
        return Ok({"diagnostic": report})

    owner.diagnose_companion = diagnose
    blocked = tmp_path / "not-a-directory"
    blocked.write_text("keep", encoding="utf-8")
    owner.data_path = lambda *parts: blocked
    result = asyncio.run(owner.export_companion_diagnostic())
    assert isinstance(result, Err)
    assert blocked.read_text(encoding="utf-8") == "keep"
    assert sent == []
