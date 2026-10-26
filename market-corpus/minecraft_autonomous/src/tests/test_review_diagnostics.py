import asyncio
import importlib
import json
import logging
import uuid
from unittest.mock import AsyncMock

import pytest
from test_heartbeat import setup


def rows(caplog):
    return [json.loads(record.getMessage().split(" ", 1)[1]) for record in caplog.records
            if record.getMessage().startswith("minecraft_review_rpc ")]


def test_real_review_logs_inspect_final_and_command_without_private_payload(plugin_instance, run_async, monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    port, snapshot, _ = setup(plugin_instance, monkeypatch)
    port.observe(snapshot)
    review = port.offer()["review"]
    inspected = run_async(plugin_instance.minecraft_autonomy_review(review["id"], "inspect"))
    result = run_async(plugin_instance.minecraft_autonomy_review(review["id"], "insert", reason="private reasoning must not be logged",
        goal="private user goal", death_reflection=None))
    assert result["acknowledged"] is True
    emitted = rows(caplog)
    assert [row["event"] for row in emitted] == ["entered", "completed", "entered", "completed"]
    assert [row["phase"] for row in emitted] == ["inspect", "inspect", "final", "final"]
    assert emitted[0]["call_id"] == emitted[1]["call_id"] != emitted[2]["call_id"] == emitted[3]["call_id"]
    assert all(uuid.UUID(row["call_id"]) and row["review_id"] == review["id"] for row in emitted)
    assert emitted[1]["acknowledged"] is False and emitted[1]["evidence_ref_present"] == bool(inspected["evidence_ref"])
    assert emitted[3]["acknowledged"] is True and emitted[3]["request_id"] == result["request_id"]
    assert emitted[3]["error_category"] is None
    assert "private reasoning" not in caplog.text and "private user goal" not in caplog.text
    assert "生命" not in caplog.text


def test_categorical_rejection_unknown_error_and_malformed_identifiers_are_redacted(plugin_instance, run_async, caplog):
    caplog.set_level(logging.INFO)
    result = run_async(plugin_instance.minecraft_autonomy_review("private token/server", "continue", reason="private reason"))
    assert result == {"is_error": True, "error": "disabled_or_stopped"}
    assert rows(caplog)[-1]["error_category"] == "disabled_or_stopped" and rows(caplog)[-1]["review_id"] is None
    diagnostics = importlib.import_module(plugin_instance.__class__.__module__ + ".review_diagnostics")
    original = {"is_error": True, "error": "private-error-text", "request_id": "private-auth-id", "evidence_ref": "private-ref"}

    @diagnostics.review_completion
    async def fake(_plugin, _id, _decision, **_):
        return original

    assert run_async(fake(plugin_instance, "a" * 12, "inspect", context={"persona": "private persona"})) is original
    last = rows(caplog)[-1]
    assert last["error_category"] == "other_error" and last["request_id"] is None and last["evidence_ref_present"] is False
    assert all(secret not in caplog.text for secret in ("private token", "private reason", "private-error", "private-auth", "private-ref", "private persona"))


@pytest.mark.parametrize("cancelled", [False, True])
def test_original_exception_or_cancellation_is_preserved_without_exception_text(plugin_instance, run_async, monkeypatch, caplog, cancelled):
    caplog.set_level(logging.INFO)
    original = asyncio.CancelledError("private cancel") if cancelled else RuntimeError("private secret")
    monkeypatch.setattr(plugin_instance.config, "dump", AsyncMock(side_effect=original))
    with pytest.raises(type(original)) as caught:
        run_async(plugin_instance.minecraft_autonomy_review("b" * 12, "inspect"))
    assert caught.value is original
    emitted = rows(caplog)
    assert emitted[-1]["event"] == "raised" and emitted[-1]["call_id"] == emitted[0]["call_id"]
    assert emitted[-1]["error_category"] == ("cancelled" if cancelled else "exception")
    assert "private secret" not in caplog.text and "private cancel" not in caplog.text


def test_logger_failure_does_not_change_result_or_dispatch(plugin_instance, run_async, monkeypatch):
    def broken(*_args, **_kwargs):
        raise RuntimeError("private logger failure")
    monkeypatch.setattr(plugin_instance.logger, "info", broken)
    result = run_async(plugin_instance.minecraft_autonomy_review("c" * 12, "continue"))
    assert result == {"is_error": True, "error": "disabled_or_stopped"}
    assert plugin_instance._worker is None
