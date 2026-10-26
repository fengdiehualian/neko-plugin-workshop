"""Character reset must clear records durably without affecting other roles."""

import asyncio
import copy
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest


def run(coro):
    return asyncio.run(coro)


def seed(name="Alice"):
    return {
        "lanlan_index": [name, "Other"],
        f"cycle@{name}": {
            "enabled": False, "anchor_date": "2026-08-01", "advance_days": 4,
            "params": {"cycle_length": 30}, "last_journal_invite_ts": 123,
        },
        f"mood@{name}": {"valence": 0.6, "history": [{"ts": 123, "action": "ripple"}]},
        f"diary@{name}": [{"ts": "2026-08-12", "entry": "Old fragment"}],
        f"journal@{name}": [{"page_no": 9, "entries": [{"ts": "2026-08-13", "text": "Old journal"}]}],
        f"journal_archive@{name}": [{"page_no": 2, "entries": [{"text": "Old archived journal"}]}],
        f"review@{name}": {"entries": [{"ts": "2026-08-14", "text": "Old review"}], "stats": {"turns": 20}},
        f"review_archive@{name}": [{"ts": "2026-08-11", "text": "Old archived review"}],
        f"stats@{name}": {"backfilled": True, "first_seen": "2026-08-01", "days": {"2026-08-14": {"turns": 20}}},
        f"weekly@{name}": [{"ts": "2025-08-01", "summary": "Legacy weekly"}],
        f"review_stats@{name}": {"turns": 50},
        f"pre_debug@journal@{name}": {"journal": [{"page_no": 1, "entries": []}]},
        f"pre_debug@diary@{name}": {"diary": [{"entry": "Before diary debug"}]},
        f"pre_debug@review@{name}": {"entries": [{"text": "Before debug"}]},
        f"pre_debug@stats@{name}": {"days": {"2025-08-01": {"turns": 10}}},
        f"journal@{name}|pre-debug": {"journal": [{"page_no": 1, "entries": []}]},
        f"review@{name}|pre-debug": {"entries": [{"text": "Legacy backup"}]},
        f"stats@{name}|pre-debug": {"days": {"2025-08-01": {"turns": 10}}},
        "cycle@Other": {"anchor_date": "2026-08-02", "advance_days": 6},
        "diary@Other": [{"entry": "Keep other character"}],
        "settings": {"birthday": {"date": "2000-08-27"}},
        "caps@Alice": {"review": True},
        "panel_appearance": {"bg_id": "local"},
        "gallery_video_file/local": {"storage_id": "untouched"},
    }


def test_reset_clears_all_records_queues_and_survives_restart(plugin_factory_full, tm):
    plugin = plugin_factory_full(current_lanlan="Alice", store_initial=seed())
    old = plugin._get_shard("Alice")
    old.pending_review_write = 123
    old.pending_journal_write = {"ts": 123}
    old.review_write_result = {"written": True}
    old.journal_write_result = {"written": True}
    before = copy.deepcopy(plugin.store.data)
    result = run(plugin.reset_all(expected_lanlan="Alice"))
    assert isinstance(result, tm.Ok)
    assert result.value["reset"] is True
    shard = plugin._get_shard("Alice")
    assert shard.diary == shard.journal == shard.journal_archive == shard.review == shard.review_archive == []
    assert not shard.mood.history and not shard.mood.action and shard.mood.valence == 0
    assert not shard.pending_journal_write and not shard.pending_review_write
    assert shard.journal_write_result is shard.review_write_result is None
    assert shard.review_stats["turns"] == 0
    assert shard.stats == {"backfilled": True}
    assert shard.cycle["advance_days"] == 0 and "enabled" not in shard.cycle
    assert shard.cycle["anchor_date"] == "2026-08-01" and shard.cycle["params"] == {"cycle_length": 30}
    for key in ("settings", "caps@Alice", "panel_appearance", "gallery_video_file/local", "cycle@Other", "diary@Other"):
        assert plugin.store.data[key] == before[key]
    assert not any(key.startswith("pre_debug@") or key.endswith("|pre-debug") for key in plugin.store.data)
    assert plugin.store.data["weekly@Alice"] == []
    assert plugin.store.data["review_stats@Alice"] == {}
    restarted = plugin_factory_full(current_lanlan="Alice", store_initial=copy.deepcopy(plugin.store.data))
    loaded = restarted._get_shard("Alice")
    assert loaded.diary == loaded.journal == loaded.journal_archive == loaded.review == loaded.review_archive == []
    assert loaded.stats == {"backfilled": True}
    assert loaded.review_stats["turns"] == 0
    assert restarted._enabled(loaded)


def test_reset_does_not_delete_real_role_with_debug_suffix(plugin_factory_full, tm):
    data = seed()
    name = "Alice|pre-debug"
    data["lanlan_index"].append(name)
    extra = {
        f"journal@{name}": [{"page_no": 1, "entries": [{"text": "Real role"}]}],
        f"review@{name}": {"entries": [{"text": "Real review"}]},
        f"stats@{name}": {"backfilled": True, "first_seen": "2025-01-01"},
    }
    data.update(copy.deepcopy(extra))
    plugin = plugin_factory_full(current_lanlan="Alice", store_initial=data)
    assert isinstance(run(plugin.reset_all()), tm.Ok)
    assert all(plugin.store.data[key] == value for key, value in extra.items())


def test_reset_rechecks_host_role_after_confirmation(plugin_factory_full, tm):
    plugin = plugin_factory_full(current_lanlan="Alice", store_initial=seed())
    before = copy.deepcopy(plugin.store.data)

    async def switched(_method, path, **_kwargs):
        return {"current_catgirl": "Other"} if path.endswith("/current_catgirl") else None

    plugin._proactive_http = switched
    result = run(plugin.reset_all(expected_lanlan="Alice"))
    assert isinstance(result, tm.Err) and str(result.error) == "mood_changed"
    assert plugin.store.data == before


@pytest.mark.parametrize("key", [
    "cycle@Alice", "mood@Alice", "diary@Alice", "journal@Alice", "review@Alice",
    "journal_archive@Alice", "review_archive@Alice", "stats@Alice",
    "weekly@Alice", "review_stats@Alice", "pre_debug@journal@Alice", "pre_debug@diary@Alice",
])
def test_reset_does_not_report_success_on_store_failure(plugin_factory_full, tm, key):
    plugin = plugin_factory_full(current_lanlan="Alice", store_initial=seed())
    save, delete = plugin.store.set, plugin.store.delete

    async def faulted_save(target, value):
        if target == key:
            return tm.Err(tm.SdkError("synthetic disk failure"))
        return await save(target, value)

    async def faulted_delete(target):
        if target == key:
            return tm.Err(tm.SdkError("synthetic disk failure"))
        return await delete(target)

    plugin.store.set = faulted_save
    plugin.store.delete = faulted_delete
    result = run(plugin.reset_all())
    assert isinstance(result, tm.Err) and str(result.error) == "persist_failed"
    assert not plugin._data_gate.exclusive


def test_reset_requires_ready_persistent_store(plugin_factory_full, tm):
    plugin = plugin_factory_full(current_lanlan="Alice", store_initial=seed())
    plugin.store.enabled = False
    before = copy.deepcopy(plugin.store.data)
    assert isinstance(run(plugin.reset_all()), tm.Err)
    assert plugin.store.data == before


def test_reset_waits_for_inflight_operations_and_blocks_new_ones(plugin_factory_full, tm):
    plugin = plugin_factory_full(current_lanlan="Alice", store_initial=seed())
    before = copy.deepcopy(plugin.store.data)
    assert plugin._data_gate.enter()
    try:
        assert str(run(plugin.reset_all()).error) == "backup_busy"
        assert plugin.store.data == before
    finally:
        plugin._data_gate.leave()
    entered, release = threading.Event(), threading.Event()
    save = plugin.store.set

    async def slow_save(key, value):
        if key == "cycle@Alice":
            entered.set()
            await asyncio.to_thread(release.wait, 5)
        return await save(key, value)

    plugin.store.set = slow_save
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(run, plugin.reset_all())
        assert entered.wait(2)
        try:
            assert str(run(plugin.dashboard()).error) == "backup_busy"
            assert str(run(plugin.data_backup(op="export")).error) == "backup_busy"
            assert str(run(plugin.reset_all()).error) == "backup_busy"
        finally:
            release.set()
        assert isinstance(future.result(timeout=5), tm.Ok)
    assert not plugin._data_gate.exclusive and plugin._data_gate.active == 0
