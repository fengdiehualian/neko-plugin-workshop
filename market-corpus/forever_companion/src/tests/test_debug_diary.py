"""Diary debug seeding preserves the first backup and stays within its role."""

import asyncio
import copy
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pytest
from forever_companion.core import backup
from forever_companion.core.fragments import fabricate_demo_diary


def run(coro):
    return asyncio.run(coro)


def prepared(factory):
    return factory(tide_extra={"debug_mode": True}, current_lanlan="Alice", store_initial={
        "lanlan_index": ["Alice", "Other"],
        "diary@Alice": [{"ts": "2026-10-01", "entry": "Real note", "extra": {"keep": [1]}}],
        "diary@Other": [{"entry": "Other role"}],
    })


def test_debug_entry_only_registered_while_enabled(plugin_factory):
    plugin = plugin_factory()
    plugin._sync_debug_entries()
    assert "debug_diary_fill" not in plugin._dyn_entries
    plugin._tide_cfg["debug_mode"] = True
    plugin._sync_debug_entries()
    assert callable(plugin._dyn_entries["debug_diary_fill"])
    schema = next(row[3] for row in plugin._DEBUG_ENTRIES if row[0] == "debug_diary_fill")
    assert schema["properties"]["entries"]["maximum"] == 120
    plugin._tide_cfg["debug_mode"] = False
    plugin._sync_debug_entries()
    assert "debug_diary_fill" not in plugin._dyn_entries


def test_demo_covers_all_types_unique_timestamps_and_multiple_dates():
    now = datetime(2026, 10, 18, 12, tzinfo=timezone.utc)
    items = fabricate_demo_diary(now=now)
    assert items == fabricate_demo_diary(now=now) and len(items) == 24
    assert {item["source"] for item in items} == {"self", "auto"}
    assert {item["kind"] for item in items if item["source"] == "auto"} == {
        "like", "dislike", "important", "overstep",
    }
    timestamps = [item["ts"] for item in items]
    assert timestamps == sorted(set(timestamps))
    assert len({stamp[:10] for stamp in timestamps}) > 1
    assert max(datetime.fromisoformat(stamp) for stamp in timestamps) <= now
    assert all(item["demo"] and "调试示例" not in (item.get("entry") or item.get("quote")) for item in items)


def test_existing_demo_prefix_hidden_in_dashboard_and_pagination_without_rewriting(plugin_factory_full):
    records = [
        {"ts": "2026-10-18T09:00:00Z", "source": "self", "demo": True, "entry": "【调试示例 1】旧手记"},
        {"ts": "2026-10-18T10:00:00Z", "source": "auto", "demo": True, "quote": "【调试示例 2】旧摘录"},
        {"ts": "2026-10-18T11:00:00Z", "source": "self", "demo": True, "entry": "【调试示例 】无编号"},
        {"ts": "2026-10-18T12:00:00Z", "source": "self", "entry": "【调试示例 4】真实记录保留"},
    ]
    plugin = plugin_factory_full(current_lanlan="Alice", store_initial={
        "lanlan_index": ["Alice"], "diary@Alice": copy.deepcopy(records),
        "pre_debug@diary@Alice": {"diary": [{"entry": "Original backup"}]},
    })
    before = copy.deepcopy(plugin.store.data)
    expected = ["【调试示例 4】真实记录保留", "无编号", "旧摘录", "旧手记"]
    dashboard = run(plugin.dashboard())
    assert [item.get("entry") or item.get("quote") for item in dashboard["diary_recent"]] == expected
    items = run(plugin.get_diary(limit=2)).value["items"]
    items += run(plugin.get_diary(limit=2, offset=2)).value["items"]
    assert [item.get("entry") or item.get("quote") for item in items] == expected
    assert plugin._get_shard("Alice").diary == records
    assert plugin.store.data["diary@Alice"] == before["diary@Alice"]
    assert plugin.store.data["pre_debug@diary@Alice"] == before["pre_debug@diary@Alice"]


def test_seed_restore_pagination_and_other_data_unchanged(plugin_factory_full, tm):
    plugin = prepared(plugin_factory_full)
    shard = plugin._get_shard("Alice")
    before = copy.deepcopy(plugin.store.data)
    other_state = copy.deepcopy((shard.stats, shard.review_stats, shard.journal, shard.review, shard.cycle))
    result = run(plugin._debug_diary_fill())
    assert isinstance(result, tm.Ok) and result.value["seeded"]
    assert result.value["entries_total"] == 24 and result.value["fragment_total"] == 12
    assert plugin.store.data["diary@Alice"] == shard.diary
    assert (shard.stats, shard.review_stats, shard.journal, shard.review, shard.cycle) == other_state
    assert not plugin._pushed
    first = run(plugin.get_diary(limit=12)).value
    second = run(plugin.get_diary(limit=12, offset=12)).value
    assert first["has_more"] and not second["has_more"]
    assert len(first["items"]) == len(second["items"]) == 12
    assert not {item["ts"] for item in first["items"]} & {item["ts"] for item in second["items"]}
    fragment = next(item for item in first["items"] if item["source"] == "auto")
    assert isinstance(run(plugin.delete_diary_item(ts=fragment["ts"])), tm.Ok)
    assert len(shard.diary) == 23
    run(plugin._debug_diary_fill(entries=8))
    assert plugin.store.data["pre_debug@diary@Alice"] == {"diary": before["diary@Alice"]}
    assert run(plugin._debug_diary_fill(restore=True)).value["restored"]
    assert plugin.store.data == before
    assert not run(plugin._debug_diary_fill(restore=True)).value["restored"]


@pytest.mark.parametrize(("entries", "expected"), [(1, 1), (0, 1), (-3, 1), (120, 120), (999, 120)])
def test_seed_count_bounds(plugin_factory_full, entries, expected):
    plugin = prepared(plugin_factory_full)
    assert run(plugin._debug_diary_fill(entries=entries)).value["entries_total"] == expected


@pytest.mark.parametrize("entries", ["bad", None, float("inf")])
def test_invalid_count_does_not_change_data(plugin_factory_full, tm, entries):
    plugin = prepared(plugin_factory_full)
    before = copy.deepcopy(plugin.store.data)
    assert isinstance(run(plugin._debug_diary_fill(entries=entries)), tm.Err)
    assert plugin.store.data == before


@pytest.mark.parametrize("operation", ["read", "backup", "seed", "restore", "drop"])
def test_storage_errors_keep_original_backup_and_allow_retry(plugin_factory_full, tm, operation, monkeypatch):
    plugin = prepared(plugin_factory_full)
    real = copy.deepcopy(plugin._get_shard("Alice").diary)
    key = "pre_debug@diary@Alice"
    restoring = operation in ("restore", "drop")
    if restoring:
        run(plugin._debug_diary_fill())
    previous = copy.deepcopy(plugin._get_shard("Alice").diary)
    original_get, original_set, original_delete = plugin.store.get, plugin.store.set, plugin.store.delete

    async def get(target):
        return tm.Err(tm.SdkError("read failure")) if target == key else await original_get(target)

    async def set_value(target, value):
        failed_key = key if operation == "backup" else "diary@Alice"
        return tm.Err(tm.SdkError("write failure")) if target == failed_key else await original_set(target, value)

    async def delete(target):
        return tm.Err(tm.SdkError("delete failure")) if target == key else await original_delete(target)

    with monkeypatch.context() as patched:
        if operation == "read":
            patched.setattr(plugin.store, "get", get)
        elif operation == "drop":
            patched.setattr(plugin.store, "delete", delete)
        else:
            patched.setattr(plugin.store, "set", set_value)
        result = run(plugin._debug_diary_fill(restore=restoring))
        assert isinstance(result, tm.Err)
    assert plugin._get_shard("Alice").diary == (real if operation == "drop" else previous)
    if operation in ("read", "backup"):
        assert key not in plugin.store.data
    else:
        assert plugin.store.data[key] == {"diary": real}
        assert run(plugin._debug_diary_fill(restore=True)).value["restored"]
        assert plugin.store.data["diary@Alice"] == real and key not in plugin.store.data


def test_restart_can_restore_and_explicit_role_stays_isolated(plugin_factory_full):
    plugin = prepared(plugin_factory_full)
    before = copy.deepcopy(plugin.store.data["diary@Other"])
    run(plugin._debug_diary_fill(lanlan="Other"))
    assert plugin.store.data["diary@Alice"][0]["entry"] == "Real note"
    restarted = plugin_factory_full(
        tide_extra={"debug_mode": True}, current_lanlan="Alice", store_initial=copy.deepcopy(plugin.store.data),
    )
    assert run(restarted._debug_diary_fill(lanlan="Other", restore=True)).value["restored"]
    assert restarted.store.data["diary@Other"] == before


def test_full_backup_roundtrip_retains_restore_payload(plugin_factory_full):
    plugin = prepared(plugin_factory_full)
    real = copy.deepcopy(plugin._get_shard("Alice").diary)
    run(plugin._debug_diary_fill())
    data = copy.deepcopy(plugin.store.data)
    data["gallery_index"] = {"next": 1, "items": []}
    archive, _ = backup.make_archive(data, "1.4.1")
    records, _ = backup.read_archive(archive)
    restarted = plugin_factory_full(tide_extra={"debug_mode": True}, current_lanlan="Alice", store_initial=records)
    assert run(restarted._debug_diary_fill(restore=True)).value["restored"]
    assert restarted.store.data["diary@Alice"] == real


def test_prune_removes_diary_backup_for_orphan(plugin_factory_full, tm):
    plugin = prepared(plugin_factory_full)
    run(plugin._debug_diary_fill(lanlan="Other"))

    async def known():
        return ["Alice"]

    plugin._fetch_known_catgirls = known
    assert isinstance(run(plugin.prune_lanlan(lanlan="Other")), tm.Ok)
    assert "pre_debug@diary@Other" not in plugin.store.data


def test_real_role_with_debug_suffix_is_never_used_as_backup(plugin_factory_full):
    plugin = prepared(plugin_factory_full)
    name = "Alice|pre-debug"
    plugin._lanlan_index.append(name)
    plugin.store.data[f"diary@{name}"] = [{"entry": "Real suffix role"}]
    run(plugin._debug_diary_fill())
    run(plugin._debug_diary_fill(restore=True))
    assert plugin.store.data[f"diary@{name}"] == [{"entry": "Real suffix role"}]


def test_store_unavailable_aborts_before_changing_records(plugin_factory_full, tm):
    plugin = prepared(plugin_factory_full)
    plugin.store.enabled = False
    before = copy.deepcopy(plugin.store.data)
    assert isinstance(run(plugin._debug_diary_fill()), tm.Err)
    assert plugin.store.data == before
    assert plugin._get_shard("Alice").diary == before["diary@Alice"]


@pytest.mark.parametrize("bad", [{}, {"diary": "bad"}, {"diary": [3]}])
def test_malformed_backup_does_not_overwrite_real_data(plugin_factory_full, tm, bad):
    plugin = prepared(plugin_factory_full)
    plugin.store.data["pre_debug@diary@Alice"] = bad
    before = copy.deepcopy(plugin.store.data)
    assert isinstance(run(plugin._debug_diary_fill()), tm.Err)
    assert isinstance(run(plugin._debug_diary_fill(restore=True)), tm.Err)
    assert plugin.store.data == before


def test_disabled_debug_rejects_stale_handler_without_data_changes(plugin_factory_full, tm):
    plugin = prepared(plugin_factory_full)
    plugin._tide_cfg["debug_mode"] = False
    before = copy.deepcopy(plugin.store.data)
    assert isinstance(run(plugin._debug_diary_fill()), tm.Err)
    assert plugin.store.data == before


def test_diary_debug_excludes_other_writers_and_competing_injections(plugin_factory_full):
    plugin = prepared(plugin_factory_full)
    entered, release = threading.Event(), threading.Event()
    original = plugin._save_shard_diary

    async def save(name, shard):
        entered.set()
        assert await asyncio.to_thread(release.wait, 5)
        return await original(name, shard)

    plugin._save_shard_diary = save
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(run, plugin._debug_diary_fill())
        assert entered.wait(2)
        try:
            assert str(run(plugin._debug_diary_fill(restore=True)).error) == "backup_busy"
            assert str(run(plugin.tool_write_diary(entry="Concurrent real note")).error) == "backup_busy"
            assert str(run(plugin.shutdown()).error) == "backup_busy"
        finally:
            release.set()
        assert future.result(timeout=5).value["seeded"]
    assert not plugin._data_gate.exclusive and plugin._data_gate.active == 0
    assert plugin.store.data["pre_debug@diary@Alice"]["diary"][0]["entry"] == "Real note"


@pytest.mark.parametrize("restoring", [False, True])
def test_cancelled_diary_debug_preserves_memory_and_original_backup(plugin_factory_full, restoring):
    plugin = prepared(plugin_factory_full)
    if restoring:
        run(plugin._debug_diary_fill())
    before = copy.deepcopy(plugin._get_shard("Alice").diary)
    original = plugin._save_shard_diary

    async def cancelled(*_args):
        raise asyncio.CancelledError

    plugin._save_shard_diary = cancelled
    with pytest.raises(asyncio.CancelledError):
        run(plugin._debug_diary_fill(restore=restoring))
    assert plugin._get_shard("Alice").diary == before
    assert plugin.store.data["diary@Alice"] == before
    assert plugin.store.data["pre_debug@diary@Alice"]["diary"][0]["entry"] == "Real note"
    assert not plugin._data_gate.exclusive
    plugin._save_shard_diary = original
    assert run(plugin._debug_diary_fill(restore=True)).value["restored"]


@pytest.mark.parametrize("unreadable", [False, True])
def test_unreliable_source_is_not_backed_up_as_empty_diary(plugin_factory_full, tm, unreadable):
    plugin = prepared(plugin_factory_full)
    before = copy.deepcopy(plugin.store.data)
    if unreadable:
        original = plugin.store.get

        async def get(key):
            return tm.Err(tm.SdkError("read failed")) if key == "diary@Alice" else await original(key)

        plugin.store.get = get
    else:
        plugin._get_shard("Alice").diary = []
    assert isinstance(run(plugin._debug_diary_fill()), tm.Err)
    assert plugin.store.data == before
