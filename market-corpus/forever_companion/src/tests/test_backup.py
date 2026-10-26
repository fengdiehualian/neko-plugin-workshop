"""Backup acceptance uses fake hosts, synthetic records and temporary roots."""

from __future__ import annotations

import asyncio
import base64
import copy
import io
import json
import os
import threading
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from forever_companion.core import backup, backup_files
from forever_companion.services.data_guard import guard_data_operations

PNG = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jS1kAAAAASUVORK5CYII="


def run(awaitable):
    return asyncio.run(awaitable)


def value(result):
    assert hasattr(result, "value"), str(getattr(result, "error", "missing value"))
    return result.value


def records():
    return {
        "lanlan_index": ["Alice", "Old @ role"],
        "settings": {"birthday": {"date": "06-12"}, "review": {"turns_threshold": 27}},
        "guide": {"wizard": "done", "at": "", "version": "1.4.0"},
        "caps@*": {"whisper": True},
        "cycle@Alice": {"enabled": True, "anchor_date": "2026-08-01", "advance_days": 3},
        "mood@Alice": {"action": "", "valence": -0.2, "arousal": 0.4, "history": []},
        "diary@Alice": [{"ts": "2026-10-01T19:00:00+08:00", "source": "self", "entry": "Original record"}],
        "journal@Alice": [{"page_no": 85, "title": "Original title", "entries": [{"ts": "2026-10-01", "text": "Original page"}]}],
        "journal_archive@Alice": [{"page_no": 4, "entries": [{"ts": "2024-09-01", "text": "Archive"}]}],
        "review@Alice": {"entries": [{"entry_no": 37, "ts": "2026-10-01", "text": "Observer"}], "stats": {"turns": 9}},
        "review_archive@Alice": [{"entry_no": 2, "ts": "2024-10-01", "text": "Old observer"}],
        "stats@Alice": {"backfilled": True, "first_seen": "2024-01-01"},
        "diary@Old @ role": [{"ts": "2025-06-01", "entry": "Deleted role remains"}],
        "pre_debug@journal@Alice": {"journal": [{"page_no": 1, "entries": []}], "archive": []},
        "pre_debug@diary@Alice": {"diary": [{"entry": "Before diary debug", "source": "self"}]},
        "gallery_index": {"next": 8, "items": [{"id": "g1", "name": "Image", "mime": "image/png", "thumb": "", "size": 68}]},
        "gallery_img/g1": {"data_url": PNG, "mime": "image/png", "size": 68},
        "panel_appearance": {"bg_id": "g1", "dim": 0.2},
    }


def attached(factory, tmp_path, initial=None):
    plugin = factory(current_lanlan="Alice", store_initial=initial or records())
    root = tmp_path / "isolated-data"
    plugin.data_path = lambda *parts: root.joinpath(*parts)
    return plugin


def stage(plugin, content):
    opened = value(run(plugin.data_backup(op="begin", size=len(content))))
    uid = opened["upload_id"]
    for offset in range(0, len(content), backup.CHUNK_BYTES):
        chunk = content[offset:offset + backup.CHUNK_BYTES]
        reply = value(run(plugin.data_backup(op="chunk", upload_id=uid, offset=offset, data_b64=base64.b64encode(chunk).decode())))
        assert reply["received"] == offset + len(chunk)
    return uid, value(run(plugin.data_backup(op="preview", upload_id=uid)))


def test_archive_roundtrip_and_identity():
    original = records()
    content, manifest = backup.make_archive(original, "1.4.0")
    restored, actual = backup.read_archive(content)
    assert restored == original
    assert manifest == actual
    assert actual["summary"] == {"roles": ["Alice", "Old @ role"], "diary": 2, "journal": 2, "review": 2, "images": 1}


def test_141_backup_remains_readable_and_reexports_without_record_changes():
    original = records()
    original.pop("pre_debug@diary@Alice")
    old_archive, _ = backup.make_archive(original, "1.4.1")
    loaded, manifest = backup.read_archive(old_archive)
    assert loaded == original and manifest["format_version"] == 1
    new_archive, _ = backup.make_archive(loaded, "1.4.2")
    reloaded, _ = backup.read_archive(new_archive)
    assert reloaded == original


@pytest.mark.parametrize("mutation,expected", [
    ("hash", "backup_checksum_failed"),
    ("version", "backup_version_unsupported"),
    ("extra", "backup_invalid"),
    ("duplicate", "backup_invalid"),
    ("bad_json", "backup_invalid"),
])
def test_bad_archives_are_rejected(mutation, expected):
    content, _ = backup.make_archive(records(), "1.4.0")
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        data = archive.read("data.json")
    if mutation == "hash":
        data += b" "
    if mutation == "version":
        manifest["format_version"] = 2
    if mutation == "bad_json":
        manifest = []
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        archive.writestr("data.json", data)
        if mutation == "extra":
            archive.writestr("../../store.db", b"x")
        if mutation == "duplicate":
            with pytest.warns(UserWarning, match="Duplicate name"):
                archive.writestr("data.json", data)
    with pytest.raises(backup.BackupError, match=expected):
        backup.read_archive(output.getvalue())


def test_export_excludes_all_dynamic_media_and_host_credentials(plugin_factory_full, tmp_path):
    initial = records()
    initial.update({
        "gallery_video/v1": {"secret_path": "ignored"},
        "gallery_video_chunk/sid/0": {"data_b64": "ignored"},
        "gallery_files_v1": {"version": 1, "entries": {}},
        "host_secret": {"api_key": "must-not-export"},
        "proactive_state": {"prev": {"master": True}, "paused_by": ["Alice"]},
        "gallery_img/animated": {"data_url": "data:image/gif;base64,R0lGODlh"},
    })
    initial["gallery_index"]["items"] += [
        {"id": "v1", "kind": "video", "name": "Video"},
        {"id": "animated", "name": "Animation"},
    ]
    initial["panel_appearance"]["bg_id"] = "v1"
    plugin = attached(plugin_factory_full, tmp_path, initial)
    result = value(run(plugin.data_backup(op="export")))
    restored, manifest = backup.read_archive(Path(result["path"]).read_bytes())
    assert manifest["excluded_media"] == 2
    assert restored["panel_appearance"]["bg_id"] == ""
    assert not any(key.startswith(("gallery_video", "gallery_files")) for key in restored)
    assert "proactive_state" not in restored and "host_secret" not in restored
    assert restored["gallery_img/g1"]["data_url"] == PNG
    assert Path(result["path"]).parent == plugin.data_path("backups")
    assert Path(result["path"]).suffix == ".fc-backup"


def test_restore_replaces_all_roles_keeps_videos_and_reloads_memory(plugin_factory_full, tmp_path):
    plugin = attached(plugin_factory_full, tmp_path)
    initial_video = {"version": 1, "entries": {"v1": {"storage_id": "local-only"}}}
    plugin.store.data.update({
        "gallery_files_v1": copy.deepcopy(initial_video),
        "gallery_video_file/v1": {"size": 63},
        "gallery_video_chunk/old/0": {"data_b64": "untouched"},
        "diary@Extra": [{"entry": "Must be replaced"}],
        "caps@Extra": {"review": True},
    })
    plugin.store.data["gallery_index"]["items"].append({"id": "v1", "kind": "video"})
    local_files = plugin.data_path("wallpapers")
    local_files.mkdir(parents=True)
    (local_files / "video.bin").write_bytes(b"local-video")
    incoming = records()
    incoming["diary@Alice"][0]["entry"] = "Restored exact text"
    content, _ = backup.make_archive(incoming, "1.4.0")
    uid, preview = stage(plugin, content)
    assert plugin.store.data["diary@Alice"][0]["entry"] == "Original record"
    result = value(run(plugin.data_backup(op="apply", upload_id=uid, token=preview["token"], confirm=True)))
    assert result["restored"]
    assert plugin._shards["Alice"].diary == incoming["diary@Alice"]
    assert plugin._shards["Alice"].journal[0]["page_no"] == 85
    assert plugin._shards["Alice"].journal_archive == incoming["journal_archive@Alice"]
    assert plugin._shards["Alice"].review_archive == incoming["review_archive@Alice"]
    assert "diary@Extra" not in plugin.store.data and "caps@Extra" not in plugin.store.data
    assert plugin.store.data["gallery_files_v1"] == initial_video
    assert plugin.store.data["gallery_video_chunk/old/0"] == {"data_b64": "untouched"}
    assert (local_files / "video.bin").read_bytes() == b"local-video"
    assert any(item.get("id") == "v1" for item in plugin.store.data["gallery_index"]["items"])
    previous, _ = backup.read_archive(Path(result["previous"]["path"]).read_bytes())
    assert previous["diary@Alice"][0]["entry"] == "Original record"
    assert "diary@Extra" in previous
    assert not plugin.data_path("backups", "restore-journal.json").exists()


def test_image_id_conflict_preserves_local_animation(plugin_factory_full, tmp_path):
    plugin = attached(plugin_factory_full, tmp_path)
    animated = {"data_url": "data:image/gif;base64,R0lGODlh"}
    current = copy.deepcopy(plugin.store.data)
    current["gallery_img/g1"] = animated
    current["panel_bg"] = animated
    plan = plugin._backup_restore_plan(records(), current)
    assert plan["gallery_img/g1"] == animated
    assert plan["panel_bg"] == animated
    gid = plan["panel_appearance"]["bg_id"]
    assert gid != "g1"
    assert plan[f"gallery_img/{gid}"]["data_url"] == PNG


def test_restore_write_failure_rolls_back(plugin_factory_full, tmp_path, tm):
    plugin = attached(plugin_factory_full, tmp_path)
    before = copy.deepcopy(plugin.store.data)
    incoming = records()
    incoming["diary@Alice"] = [{"entry": "new"}]
    uid, preview = stage(plugin, backup.make_archive(incoming, "1.4.0")[0])
    original = plugin.store.set
    failed = False

    async def fail_once(key, data):
        nonlocal failed
        if key == "journal@Alice" and not failed:
            failed = True
            return tm.Err(tm.SdkError("synthetic"))
        return await original(key, data)

    plugin.store.set = fail_once
    result = run(plugin.data_backup(op="apply", upload_id=uid, token=preview["token"], confirm=True))
    assert str(result.error) == "backup_restore_failed"
    assert plugin.store.data == before
    assert not plugin._data_gate.blocked
    assert plugin._shards["Alice"].diary == before["diary@Alice"]


def test_failed_rollback_blocks_writes_and_restart_recovers(plugin_factory_full, tmp_path, tm):
    plugin = attached(plugin_factory_full, tmp_path)
    before = copy.deepcopy(plugin.store.data)
    incoming = records()
    incoming["diary@Alice"] = [{"entry": "new"}]
    uid, preview = stage(plugin, backup.make_archive(incoming, "1.4.0")[0])
    original = plugin.store.set

    async def fail(key, data):
        if key == "journal@Alice":
            return tm.Err(tm.SdkError("synthetic"))
        return await original(key, data)

    plugin.store.set = fail
    result = run(plugin.data_backup(op="apply", upload_id=uid, token=preview["token"], confirm=True))
    assert str(result.error) == "backup_recovery_required"
    assert str(run(plugin.toggle()).error) == "backup_recovery_required"
    protected = copy.deepcopy(plugin.store.data)
    assert run(plugin.dashboard()) == {"backup_recovery_required": True, "status": {"enabled": False}}
    assert plugin.store.data == protected
    assert plugin.data_path("backups", "restore-journal.json").exists()
    plugin.store.set = original
    restarted = attached(plugin_factory_full, tmp_path)
    restarted.store = plugin.store
    value(run(restarted.startup()))
    assert restarted.store.data == before
    assert not restarted.data_path("backups", "restore-journal.json").exists()


def test_confirmation_invalid_settings_and_store_readiness(plugin_factory_full, tmp_path):
    plugin = attached(plugin_factory_full, tmp_path)
    content, _ = backup.make_archive(records(), "1.4.0")
    uid, preview = stage(plugin, content)
    assert str(run(plugin.data_backup(op="apply", upload_id=uid, token=preview["token"])).error) == "backup_confirmation_required"
    assert str(run(plugin.data_backup(op="apply", upload_id=uid, token="wrong", confirm=True)).error) == "backup_confirmation_required"
    value(run(plugin.data_backup(op="abort", upload_id=uid)))
    invalid = records()
    invalid["settings"]["tide"] = {"cycle_length": "broken"}
    content = backup.make_archive(invalid, "1.4.0")[0]
    opened = value(run(plugin.data_backup(op="begin", size=len(content))))
    value(run(plugin.data_backup(op="chunk", upload_id=opened["upload_id"], data_b64=base64.b64encode(content).decode())))
    result = run(plugin.data_backup(op="preview", upload_id=opened["upload_id"]))
    assert str(result.error) == "backup_invalid"
    plugin.store.enabled = False
    assert str(run(plugin.data_backup(op="export")).error) == "backup_store_unavailable"


def test_operation_gate_spans_entire_inflight_entry(plugin_factory_full, tmp_path):
    plugin = attached(plugin_factory_full, tmp_path)
    entered = threading.Event()
    release = threading.Event()

    class SlowPlugin(type(plugin)):
        async def slow_entry(self):
            entered.set()
            await asyncio.to_thread(release.wait, 5)

    # The production class decorator wraps every public async entry, keeping
    # its signature/metadata. Exercise the same path across separate loops.
    SlowPlugin.__module__ = type(plugin).__module__
    SlowPlugin.slow_entry.__module__ = type(plugin).__module__
    guard_data_operations(SlowPlugin)
    plugin.__class__ = SlowPlugin
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(run, plugin.slow_entry())
        assert entered.wait(2)
        try:
            assert str(run(plugin.data_backup(op="export")).error) == "backup_busy"
        finally:
            release.set()
        future.result(timeout=5)
    assert plugin._data_gate.active == 0


def test_backup_root_refuses_code_directory_and_links(plugin_factory_full, tmp_path):
    plugin = attached(plugin_factory_full, tmp_path)
    plugin.plugin_dir = plugin.data_path()
    with pytest.raises(backup.BackupError, match="backup_path_unavailable"):
        backup_files.root_for(plugin)
    del plugin.plugin_dir
    root = backup_files.root_for(plugin)
    outside = tmp_path / "outside"
    outside.write_bytes(b"untouched")
    try:
        (root / "linked.fc-backup").symlink_to(outside)
    except OSError:
        pytest.skip("symlink privilege unavailable")
    with pytest.raises(backup.BackupError):
        backup_files.write_atomic(root, "linked.fc-backup", b"replace")
    assert outside.read_bytes() == b"untouched"


@pytest.mark.parametrize("key,bad", [
    ("journal@Alice", [{"entries": [3]}]),
    ("journal_archive@Alice", [{"page_no": "x", "entries": []}]),
    ("review@Alice", {"entries": [], "stats": {"tone": {"happy": "many"}}}),
    ("review_stats@Alice", {"affect_samples": ["bad"]}),
    ("stats@Alice", {"days": {"2026-10-01": {"turns": {}}}}),
    ("stats@Alice", {"months": {"2026-09": {"tone": []}}}),
    ("pre_debug@journal@Alice", {"journal": [{"entries": "broken"}]}),
    ("pre_debug@diary@Alice", {"diary": [{"entry": []}]}),
    ("pre_debug@diary@Alice", {}),
    ("caps@*", {"whisper": "false"}),
    ("mood@Alice", {"valence": float("inf")}),
    ("cycle@Alice", {"params": []}),
    ("panel_appearance", {"bg_id": []}),
    ("gallery_index", {"next": "bad", "items": []}),
])
def test_nested_invalid_records_rejected(key, bad):
    source = records()
    source[key] = bad
    with pytest.raises(backup.BackupError, match="backup_invalid"):
        backup.make_archive(source, "1.4.0")


def test_gallery_capacity_and_multiple_collisions(plugin_factory_full, tmp_path):
    plugin = attached(plugin_factory_full, tmp_path)
    current = {"gallery_index": {"items": [{"id": "g1", "kind": "video"}, {"id": "g2", "kind": "video"}]}}
    incoming = records()
    incoming["gallery_index"]["items"].append({"id": "g2", "name": "Second"})
    incoming["gallery_img/g2"] = {"data_url": PNG}
    plan = plugin._backup_restore_plan(incoming, current)
    assert len({item["id"] for item in plan["gallery_index"]["items"]}) == 4
    assert plan["gallery_img/" + plan["panel_appearance"]["bg_id"]]["data_url"] == PNG
    current["gallery_index"]["items"] = [{"id": f"v{n}", "kind": "video"} for n in range(23)]
    with pytest.raises(backup.BackupError, match="backup_gallery_full"):
        plugin._backup_restore_plan(incoming, current)


def test_custom_settings_and_debug_backups_roundtrip(plugin_factory_full, tmp_path):
    plugin = attached(plugin_factory_full, tmp_path)
    plugin.config.section["fragments"]["daily_limit"] = 4
    plugin.config.section["tide"]["phases"]["follicular"]["mood_note"] = "Custom phase note"
    plugin.store.data.update({
        "review_stats@Alice": {"turns": 6, "tone": {"happy": 3}, "affect_samples": [0.4]},
        "weekly@Alice": [{"ts": "2024-01-01", "summary": "Legacy"}],
        "pre_debug@review@Alice": {"entries": [{"title": "Real", "text": "Preserved"}], "stats": {"turns": 8}, "archive": []},
        "stats@Alice|pre-debug": {"days": {"2024-01-01": {"turns": 2}}},
    })
    exported = value(run(plugin.data_backup(op="export")))
    source, _ = backup.read_archive(Path(exported["path"]).read_bytes())
    assert source["settings"]["fragments"]["daily_limit"] == 4
    assert source["settings"]["tide"]["phases"]["follicular"]["mood_note"] == "Custom phase note"
    assert "confidence_threshold" not in source["settings"]["fragments"]
    run(plugin._backup_validate_settings(source))
    assert source["pre_debug@review@Alice"]["entries"][0]["text"] == "Preserved"
    assert source["stats@Alice|pre-debug"]["days"]["2024-01-01"]["turns"] == 2
    proactive = {"prev": {"master": True}, "paused_by": ["Local"]}
    plugin.store.data["proactive_state"] = copy.deepcopy(proactive)
    uid, preview = stage(plugin, Path(exported["path"]).read_bytes())
    value(run(plugin.data_backup(op="apply", upload_id=uid, token=preview["token"], confirm=True)))
    assert plugin.store.data["proactive_state"] == proactive


def test_legacy_motion_setting_can_import_its_own_export(plugin_factory_full, tmp_path):
    original = records()
    original["settings"]["capabilities"] = {"motion": "full", "hide_disabled_tools": False}
    plugin = attached(plugin_factory_full, tmp_path, original)
    exported = value(run(plugin.data_backup(op="export")))
    content = Path(exported["path"]).read_bytes()
    incoming, _ = backup.read_archive(content)
    assert incoming["settings"]["capabilities"]["motion"] == "full"
    uid, preview = stage(plugin, content)
    value(run(plugin.data_backup(op="apply", upload_id=uid, token=preview["token"], confirm=True)))
    assert plugin.store.data["settings"]["capabilities"]["motion"] == "full"


def test_config_differences_and_saved_nested_settings_both_survive():
    config = {"tide": {"phases": {"menstrual": {"prompt": "Custom", "mood_note": "Config note"}}}}
    defaults = {"tide": {"phases": {"menstrual": {"prompt": "Default", "mood_note": ""}}}}
    saved = {"tide": {"phases": {"menstrual": {"mood_note": "Saved note"}}}}
    assert backup.config_overrides(config, saved, defaults) == {
        "tide": {"phases": {"menstrual": {"prompt": "Custom", "mood_note": "Saved note"}}},
    }


def test_reload_failure_keeps_journal_until_recovery_succeeds(plugin_factory_full, tmp_path):
    plugin = attached(plugin_factory_full, tmp_path)
    before = copy.deepcopy(plugin.store.data)
    uid, preview = stage(plugin, backup.make_archive(records(), "1.4.0")[0])
    original = plugin._refresh_config

    async def broken_reload():
        raise RuntimeError("synthetic")

    plugin._refresh_config = broken_reload
    result = run(plugin.data_backup(op="apply", upload_id=uid, token=preview["token"], confirm=True))
    assert str(result.error) == "backup_recovery_required"
    assert plugin.store.data == before
    assert plugin.data_path("backups", "restore-journal.json").exists()
    plugin._refresh_config = original
    value(run(plugin.data_backup(op="recover")))
    assert plugin.store.data == before
    assert not plugin._data_gate.blocked
    assert not plugin.data_path("backups", "restore-journal.json").exists()


def test_reload_store_read_fault_cannot_commit(plugin_factory_full, tmp_path, tm):
    plugin = attached(plugin_factory_full, tmp_path)
    uid, preview = stage(plugin, backup.make_archive(records(), "1.4.0")[0])
    original = plugin.store.get
    before = copy.deepcopy(plugin.store.data)
    failures = 0

    async def fail_reload_once(key):
        nonlocal failures
        if plugin._backup_strict_store and key == "settings" and not failures:
            failures += 1
            return tm.Err(tm.SdkError("synthetic"))
        return await original(key)

    plugin.store.get = fail_reload_once
    result = run(plugin.data_backup(op="apply", upload_id=uid, token=preview["token"], confirm=True))
    assert str(result.error) == "backup_restore_failed"
    assert plugin.store.data == before
    assert not plugin._data_gate.blocked


def test_upload_retry_offsets_replacement_and_expiry(plugin_factory_full, tmp_path, monkeypatch):
    plugin = attached(plugin_factory_full, tmp_path)
    first = value(run(plugin.data_backup(op="begin", size=4)))["upload_id"]
    args = {"op": "chunk", "upload_id": first, "data_b64": base64.b64encode(b"test").decode()}
    assert value(run(plugin.data_backup(**args)))["received"] == 4
    assert value(run(plugin.data_backup(**args)))["received"] == 4
    assert str(run(plugin.data_backup(**{**args, "offset": 1})).error) == "backup_upload_invalid"
    assert str(run(plugin.data_backup(**{**args, "data_b64": "!!!!"})).error) == "backup_upload_invalid"
    root = plugin.data_path("backups")
    stale = root / ("import-" + "a" * 32 + ".part")
    stale.write_bytes(b"orphan")
    os.utime(stale, (time.time() - 2000, time.time() - 2000))
    second = value(run(plugin.data_backup(op="begin", size=2)))["upload_id"]
    assert first != second
    assert not stale.exists() and not (root / f"import-{first}.part").exists()
    monkeypatch.setattr(time, "time", lambda: plugin._backup_upload["at"] + 1801)
    assert str(run(plugin.data_backup(op="preview", upload_id=second)).error) == "backup_upload_invalid"


def test_cancelled_apply_rolls_back(plugin_factory_full, tmp_path):
    plugin = attached(plugin_factory_full, tmp_path)
    uid, preview = stage(plugin, backup.make_archive(records(), "1.4.0")[0])
    original = plugin._backup_write_records
    before = copy.deepcopy(plugin.store.data)
    first = True

    async def interrupted(records_to_write, deleted):
        nonlocal first
        if first:
            first = False
            plugin.store.data["diary@Alice"] = []
            raise asyncio.CancelledError
        await original(records_to_write, deleted)

    plugin._backup_write_records = interrupted
    result = run(plugin.data_backup(op="apply", upload_id=uid, token=preview["token"], confirm=True))
    assert str(result.error) == "backup_restore_failed"
    assert plugin.store.data == before
    assert not plugin._data_gate.blocked
    assert not plugin._data_gate.exclusive


def test_role_names_preserved_even_with_debug_like_suffix():
    original = records()
    name = "Role @ name|pre-debug"
    original["cycle@" + name] = {"advance_days": 0}
    original["journal@" + name] = [{"page_no": 9, "entries": [{"text": "Exact role name"}]}]
    original["lanlan_index"] = sorted([*original["lanlan_index"], name])
    result, _ = backup.read_archive(backup.make_archive(original, "1.4.0")[0])
    assert result == original
    assert "Role @ name" not in backup.roles(result)


def test_cancellation_after_marker_removal_reestablishes_recovery(plugin_factory_full, tmp_path, monkeypatch, tm):
    plugin = attached(plugin_factory_full, tmp_path)
    before = copy.deepcopy(plugin.store.data)
    uid, preview = stage(plugin, backup.make_archive(records(), "1.4.0")[0])
    remove = backup_files.remove
    original = plugin.store.set
    cancelled = False

    def interrupted(root, name):
        nonlocal cancelled
        remove(root, name)
        if name == "restore-journal.json" and not cancelled:
            cancelled = True
            raise asyncio.CancelledError

    async def fail_rollback(key, data):
        if cancelled:
            return tm.Err(tm.SdkError("synthetic"))
        return await original(key, data)

    monkeypatch.setattr(backup_files, "remove", interrupted)
    plugin.store.set = fail_rollback
    result = run(plugin.data_backup(op="apply", upload_id=uid, token=preview["token"], confirm=True))
    assert str(result.error) == "backup_recovery_required"
    assert plugin.data_path("backups", "restore-journal.json").exists()
    plugin.store.set = original
    value(run(plugin.data_backup(op="recover")))
    assert plugin.store.data == before
