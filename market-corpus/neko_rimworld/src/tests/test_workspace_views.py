"""Hosted work reads survive restarts and never dispatch model/game calls."""

import asyncio
import hashlib
import sqlite3

import pytest

from ..workspace.store import WorkspaceStore
from ..workspace.views import WorkspaceViews
from .test_hosted_workspace import Script, call, configure, host_context
from .test_plugin import make_plugin


@pytest.fixture
async def plugin():
    value, _ = make_plugin({})
    yield value
    await value.shutdown()


def test_empty_view_does_not_create_database(tmp_path):
    path = tmp_path / "absent/work.sqlite"
    assert WorkspaceViews(path).read("list")["workspaces"] == []
    assert not path.parent.exists()


def test_history_filters_search_all_questions_and_keep_the_original_database(tmp_path):
    path = tmp_path / "work.sqlite"
    store = WorkspaceStore(path)
    for owner, colony, questions in [("Alpha", "colony-a", ["Earlier 100% food", "Later medicine"]),
                                      ("Beta", "colony-b", ["Earlier food", "Later clothing"])]:
        work = store.open_work(owner, colony)
        for question in questions:
            turn = store.accept(work, owner, question)
            store.start(work, owner, turn)
            store.finish(work, owner, turn, {"status": "ok", "answer": "Retained full answer"})
    store.close()
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    views = WorkspaceViews(path)
    assert views.read("list", search="Earlier food")["workspaces"][0]["owner"] == "Beta"
    assert views.read("list", search="100%", current_colony_id="colony-a", colony_only=True)["total"] == 1
    assert views.read("list", search="%", current_colony_id="colony-b", colony_only=True)["total"] == 0
    assert views.read("list", search="Alpha")["total"] == 1
    alpha = views.read("list", search="100%")["workspaces"][0]["workspace_id"]
    matches = views.read("work", workspace_id=alpha, search="100%")
    assert matches["total"] == 1 and matches["turns"][0]["question"] == "Earlier 100% food"
    assert views.read("work", workspace_id=alpha, search="alpha")["total"] == 2
    assert views.read("list", colony_only=True)["total"] == 0
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_current_colony_is_sorted_before_other_records_without_changing_saved_rows(tmp_path):
    path = tmp_path / "work.sqlite"
    store = WorkspaceStore(path)
    current = store.open_work("alpha", "current-colony")
    turn = store.accept(current, "alpha", "Earlier current-colony question")
    store.start(current, "alpha", turn)
    store.finish(current, "alpha", turn, {"status": "ok", "answer": "Saved answer"})
    other = store.open_work("beta", "other-colony")
    turn = store.accept(other, "beta", "Newer other-colony question")
    store.start(other, "beta", turn)
    store.finish(other, "beta", turn, {"status": "ok", "answer": "Other answer"})
    store.close()
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    ordinary = WorkspaceViews(path).read("list", limit=1)
    preferred = WorkspaceViews(path).read("list", limit=1, current_colony_id="current-colony")
    assert ordinary["workspaces"][0]["workspace_id"] == other
    assert preferred["workspaces"][0]["workspace_id"] == current
    assert preferred["total"] == 2 and preferred["next_offset"] == 1
    assert WorkspaceViews(path).read("list", offset=1, limit=1, current_colony_id="current-colony")["workspaces"][0]["workspace_id"] == other
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


@pytest.mark.asyncio
async def test_work_history_handoff_and_audit_survive_restart_without_replaying(tmp_path, monkeypatch):
    first, _ = make_plugin({})
    model = Script([call("submit_response", answer="Original answer.", detail="Original full detail.")])
    configure(first, tmp_path, [model])
    result = (await first.rimworld_consult(question="Original question", _ctx=host_context("alpha"))).value
    await first.shutdown()
    second, _ = make_plugin({})
    configure(second, tmp_path, [])
    second._bridge.session = None
    path = tmp_path / "workspaces/work.sqlite"
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    listing = (await second.workspace_view()).value
    assert listing["workspaces"][0]["owner"] == "alpha"
    assert not listing["workspaces"][0]["current_colony"]
    wid, tid = result["workspace"]["workspace_id"], result["workspace"]["turn_id"]
    work = (await second.workspace_view(action="work", workspace_id=wid)).value
    turn = (await second.workspace_view(action="turn", workspace_id=wid, turn_id=tid)).value
    audit = (await second.workspace_view(action="audit", workspace_id=wid, turn_id=tid, limit=1)).value
    assert work["turns"][0]["question"] == "Original question"
    assert turn["history_saved"] and "Original full detail." in turn["result"]["answer"]
    assert turn["handoff"]["summary"] == result["summary"]
    assert "not a receipt" in turn["handoff"]["meaning"]
    assert audit["workspace_id"] == wid and audit["turn_id"] == tid and audit["events_page"]["has_more"]
    assert second._workspace_service is None and len(model.requests) == 1

    def unreadable(_report_id):
        raise OSError("Offline report read failure")

    monkeypatch.setattr(second._reports, "read", unreadable)
    retained = (await second.workspace_view(action="turn", workspace_id=wid, turn_id=tid)).value
    assert retained["report_unavailable"] and "Original full detail." in retained["result"]["answer"]
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    await second.shutdown()


@pytest.mark.asyncio
async def test_reading_unconfirmed_turn_does_not_retry_or_recover_it(plugin, tmp_path, monkeypatch):
    model = Script([call("submit_response", answer="Complete answer.")])
    configure(plugin, tmp_path, [model])

    def fail(*args, **kwargs):
        raise sqlite3.OperationalError("Offline failed write")

    monkeypatch.setattr(WorkspaceStore, "finish", fail)
    result = (await plugin.rimworld_consult(question="Investigate", _ctx=host_context())).value
    wid, tid = result["workspace"]["workspace_id"], result["workspace"]["turn_id"]
    view = (await plugin.workspace_view(action="turn", workspace_id=wid, turn_id=tid)).value
    assert view["result"]["answer"] == "Complete answer." and not view["history_saved"]
    assert view["status"] == "running" and view["active"] is None
    assert len(model.requests) == 1
    assert (await plugin._workspace_service.storage.call("turn", wid, "neko", tid))["status"] == "running"


@pytest.mark.asyncio
async def test_current_execution_is_bound_to_its_work_and_full_entry_lifetime(plugin, tmp_path):
    started, release = asyncio.Event(), asyncio.Event()

    class Waiting(Script):
        async def complete(self, messages, tools, *, timeout):
            started.set()
            await release.wait()
            return await super().complete(messages, tools, timeout=timeout)

    model = Waiting([call("submit_response", answer="Completed.")])
    configure(plugin, tmp_path, [model])
    exporting, exported = asyncio.Event(), asyncio.Event()

    async def export(**kwargs):
        exporting.set()
        await exported.wait()
        return {"success": True}

    plugin.export_push = export
    task = asyncio.create_task(plugin.rimworld_consult(question="Active question", _ctx=host_context()))
    try:
        await asyncio.wait_for(started.wait(), 2)
        listing = (await plugin.workspace_view()).value
        row = listing["workspaces"][0]
        assert row["active"]["workspace_id"] == row["workspace_id"]
        assert row["active"]["turn_id"]
        release.set()
        await asyncio.wait_for(exporting.wait(), 2)
        assert (await plugin.dashboard_ui_context())["consultations_in_progress"] == 1
        exported.set()
        result = (await task).value
        assert (await plugin.dashboard_ui_context())["consultations_in_progress"] == 0
        view = (
            await plugin.workspace_view(
                action="turn", workspace_id=row["workspace_id"], turn_id=result["workspace"]["turn_id"]
            )
        ).value
        assert view["handoff"]["report"]["export"]["status"] == "exported"
    finally:
        release.set()
        exported.set()
        await task


@pytest.mark.asyncio
async def test_exchange_cannot_be_read_under_another_workspace(plugin, tmp_path):
    configure(
        plugin, tmp_path, [Script([call("submit_response", answer="A")]), Script([call("submit_response", answer="B")])]
    )
    a = (await plugin.rimworld_consult(question="A", _ctx=host_context("alpha"))).value
    b = (await plugin.rimworld_consult(question="B", _ctx=host_context("beta"))).value
    result = await plugin.workspace_view(
        action="turn", workspace_id=a["workspace"]["workspace_id"], turn_id=b["workspace"]["turn_id"]
    )
    assert result.is_err()


def test_evidence_is_exact_and_hash_checked(tmp_path):
    path = tmp_path / "work.sqlite"
    store = WorkspaceStore(path)
    wid = store.open_work("alpha", "colony")
    tid = store.accept(wid, "alpha", "Read facts")
    store.start(wid, "alpha", tid)
    source = {"observation_id": "source", "response": {"payload": {"data": {"flag": False, "value": None}}}}
    store.finish(wid, "alpha", tid, {"status": "ok", "answer": "Facts", "observations": [source]})
    store.close()
    view = WorkspaceViews(path).read("observation", workspace_id=wid, turn_id=tid, observation_id="source")
    assert view["observation"] == source and view["historical"] and not view["current_observation"]
    # Corrupt a disposable fixture, never an archived experiment or real work.
    with sqlite3.connect(path) as db:
        trigger = db.execute("SELECT sql FROM sqlite_master WHERE name='sealed_observation'").fetchone()[0]
        db.execute("DROP TRIGGER sealed_observation")
        db.execute("UPDATE observations SET payload='{}'")
        # Keep the supported schema so this test exercises evidence integrity,
        # rather than the separate missing-trigger admission failure.
        db.execute(trigger)
    with pytest.raises(ValueError, match="hash check"):
        WorkspaceViews(path).read("observation", workspace_id=wid, turn_id=tid, observation_id="source")


def test_reading_index_uses_saved_metadata_and_retains_raw_values(tmp_path):
    path = tmp_path / "work.sqlite"
    store = WorkspaceStore(path)
    wid = store.open_work("alpha", "colony")
    tid = store.accept(wid, "alpha", "Read facts")
    store.start(wid, "alpha", tid)
    source = {"observation_id": "source", "received_at": "2026-10-17T08:00:00Z",
              "request": {"operation": "query", "arguments": {"requests": [{"from": "Pawn", "select": {"name": {}}}]}},
              "response": {"payload": {"status": "partial", "error_code": "field_unavailable", "data": {"flag": False, "value": None}}}}
    store.finish(wid, "alpha", tid, {"status": "ok", "answer": "Facts", "observations": [source]})
    store.close()
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    indexed = WorkspaceViews(path).read("turn", workspace_id=wid, turn_id=tid)["evidence"][0]
    assert indexed["request"] == {"operation": "query", "arguments": {"requests": [{"from": "Pawn"}]}}
    assert indexed["received_at"] == source["received_at"] and "response" not in indexed
    assert indexed["status"] == "partial" and indexed["error_code"] == "field_unavailable"
    assert WorkspaceViews(path).read("observation", workspace_id=wid, turn_id=tid, observation_id="source")["observation"] == source
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
