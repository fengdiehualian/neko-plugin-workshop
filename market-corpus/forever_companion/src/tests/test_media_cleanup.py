"""Crash/retry and explicit wallpaper clearing tests using isolated test data."""

from __future__ import annotations

import asyncio
import copy
import ctypes
import os
import sys
import threading

import pytest
from forever_companion.core.media import video_chunk_key
from tests.test_media_files import (
    FILE_MANIFEST_PREFIX,
    FILE_REGISTRY,
    IMAGE,
    FileTestStore,
    add_video,
    begin,
    encoded,
    expect_error,
    file_bytes,
    file_manifest,
    legacy_video_data,
    media_root,
    run,
    send_chunks,
    synthetic_mp4,
)

# Register the isolated file-media fixture module for full-suite collection.
pytest_plugins = ("tests.test_media_files",)


def clear(plugin, **kwargs):
    return run(plugin, "gallery_remove", op="clear_media", **kwargs)


def test_clear_requires_explicit_boolean_confirmation(file_plugin, tm):
    gid = add_video(file_plugin).value["id"]
    before = copy.deepcopy(file_plugin.store.data)
    for value in (None, False, "true", 1, [], {}):
        expect_error(clear(file_plugin, confirm=value), tm, "video_clear_confirmation_required")
        assert file_plugin.store.data == before
        assert file_bytes(file_plugin, gid) == synthetic_mp4()


def test_clear_removes_files_staging_legacy_and_preserves_diaries(file_plugin_factory, tm):
    initial = legacy_video_data(synthetic_mp4())
    initial["panel_bg"] = {"data_url": IMAGE, "dim": 0.3}
    unrelated = {
        "journal@TestUser": {"entries": ["private journal"]},
        "journal_archive@TestUser": ["archive"],
        "review@TestUser": {"entries": ["private review"]},
        "review_stats@TestUser": {"tokens": 412},
        "stats@TestUser": {"days": 81},
        "settings": {"private": "retain"},
    }
    initial.update(unrelated)
    plugin = file_plugin_factory(initial)
    image_id = run(plugin, "gallery_add", data_url=IMAGE).value["id"]
    video_id = add_video(plugin).value["id"]
    run(plugin, "set_panel_appearance", bg_id=video_id, motion=False)
    pending = begin(plugin, synthetic_mp4()).value["upload_id"]
    send_chunks(plugin, pending, synthetic_mp4())
    root = media_root(plugin)
    (root / ("b" * 32 + ".part")).write_bytes(b"orphan partial")
    (root / ("c" * 32 + ".bin")).write_bytes(b"orphan published data")
    adjacent = plugin._test_data_root / "unrelated-cache"
    adjacent.mkdir()
    sentinel = adjacent / "keep.txt"
    sentinel.write_text("unrelated", encoding="ascii")
    result = clear(plugin, confirm=True)
    assert isinstance(result, tm.Ok), str(getattr(result, "error", ""))
    assert result.value["items"] == [] and result.value["appearance"]["bg_id"] == ""
    assert not list(root.iterdir())
    assert not any(
        key.startswith(("gallery_img/", "gallery_video/", "gallery_video_chunk/", FILE_MANIFEST_PREFIX))
        for key in plugin.store.data
    )
    assert "panel_bg" not in plugin.store.data
    for key, value in unrelated.items():
        assert plugin.store.data[key] == value
    assert sentinel.read_text(encoding="ascii") == "unrelated"
    reopened = run(plugin, "get_panel_gallery").value
    assert reopened["items"] == [] and reopened["migrated"] is False
    restarted = file_plugin_factory(plugin.store.data, root=plugin._test_data_root)
    assert run(restarted, "get_panel_gallery").value["items"] == []
    assert image_id not in str(restarted.store.data)


def test_clear_store_delete_failure_reports_error_and_resumes(file_plugin, file_plugin_factory, tm):
    gid = add_video(file_plugin).value["id"]
    run(file_plugin, "set_panel_appearance", bg_id=gid)
    key = FILE_MANIFEST_PREFIX + gid
    file_plugin.store.fail_delete.add(key)
    expect_error(clear(file_plugin, confirm=True), tm, "video_cleanup_incomplete")
    assert key in file_plugin.store.data
    assert file_plugin.store.data[FILE_REGISTRY]["clear"] is not None
    restarted = file_plugin_factory(file_plugin.store.data, root=file_plugin._test_data_root)
    assert isinstance(clear(restarted, confirm=True), tm.Ok)
    assert key not in restarted.store.data
    assert restarted.store.data[FILE_REGISTRY]["clear"] is None
    assert run(restarted, "get_panel_gallery").value["items"] == []


def test_clear_file_permission_failure_is_retryable(file_plugin, monkeypatch, tm):
    from forever_companion.core import media_files

    gid = add_video(file_plugin).value["id"]
    payload = file_bytes(file_plugin, gid)
    original = media_files.clear_files

    def refuse(_root):
        return False

    monkeypatch.setattr(media_files, "clear_files", refuse)
    expect_error(clear(file_plugin, confirm=True), tm, "video_cleanup_incomplete")
    assert file_plugin.store.data[FILE_REGISTRY]["clear"] is not None
    assert list(media_root(file_plugin).glob("*.bin"))
    monkeypatch.setattr(media_files, "clear_files", original)
    assert isinstance(clear(file_plugin, confirm=True), tm.Ok)
    assert not list(media_root(file_plugin).glob("*.bin"))
    assert file_plugin.store.data[FILE_REGISTRY]["clear"] is None
    assert payload == synthetic_mp4()


def test_clear_read_failure_does_not_destroy_files(file_plugin, tm):
    gid = add_video(file_plugin).value["id"]
    before = copy.deepcopy(file_plugin.store.data)
    payload = file_bytes(file_plugin, gid)
    file_plugin.store.fail_get.add("gallery_index")
    expect_error(clear(file_plugin, confirm=True), tm, "gallery_update_failed")
    assert file_plugin.store.data == before
    assert file_bytes(file_plugin, gid) == payload


def test_stale_upload_is_reclaimed_but_published_file_survives(file_plugin, file_plugin_factory, tm):
    published = add_video(file_plugin).value["id"]
    payload = synthetic_mp4()
    pending = begin(file_plugin, payload).value["upload_id"]
    send_chunks(file_plugin, pending, payload)
    file_plugin.store.data[FILE_REGISTRY]["records"][pending]["pid"] = os.getpid() + 10_000_000
    file_plugin.store.data[FILE_REGISTRY]["records"][pending]["expires_at"] = 0
    assert (media_root(file_plugin) / (pending + ".part")).exists()
    restarted = file_plugin_factory(file_plugin.store.data, root=file_plugin._test_data_root)
    reopened = run(restarted, "get_panel_gallery")
    assert isinstance(reopened, tm.Ok)
    assert {item["id"] for item in reopened.value["items"]} == {published}
    assert not (media_root(restarted) / (pending + ".part")).exists()
    assert file_bytes(restarted, published) == payload
    expect_error(run(restarted, "gallery_add", op="video_commit", upload_id=pending),
                 tm, "video_upload_not_found")


def test_live_upload_from_another_instance_is_not_reclaimed(file_plugin_factory, tm):
    shared = FileTestStore(tm)
    first = file_plugin_factory(store=shared)
    second = file_plugin_factory(store=shared, root=first._test_data_root)
    payload = synthetic_mp4()
    pending = begin(first, payload).value["upload_id"]
    send_chunks(first, pending, payload)
    part = media_root(first) / (pending + ".part")
    assert part.exists()

    reopened = run(second, "get_panel_gallery")

    assert isinstance(reopened, tm.Ok)
    assert part.exists()
    assert pending in second.store.data[FILE_REGISTRY]["records"]
    committed = run(second, "gallery_add", op="video_commit", upload_id=pending)
    assert isinstance(committed, tm.Ok)
    assert file_bytes(second, committed.value["id"]) == payload


def test_missing_published_file_fails_closed_without_metadata_deletion(file_plugin, tm):
    gid = add_video(file_plugin).value["id"]
    manifest_before = copy.deepcopy(file_manifest(file_plugin, gid))
    registry_before = copy.deepcopy(file_plugin.store.data[FILE_REGISTRY])
    (media_root(file_plugin) / (manifest_before["storage_id"] + ".bin")).unlink()

    result = run(file_plugin, "get_panel_gallery")

    expect_error(result, tm, "video_read_failed")
    assert file_manifest(file_plugin, gid) == manifest_before
    assert file_plugin.store.data[FILE_REGISTRY] == registry_before
    assert file_plugin.store.data["gallery_index"]["items"][0]["id"] == gid


def test_failed_chunk_registry_save_can_retry_without_orphan(file_plugin, tm):
    payload = synthetic_mp4()
    sid = begin(file_plugin, payload).value["upload_id"]
    before = copy.deepcopy(file_plugin.store.data[FILE_REGISTRY])
    file_plugin.store.fail_set.add(FILE_REGISTRY)
    expect_error(run(file_plugin, "gallery_add", op="video_chunk", upload_id=sid,
                     chunk_index=0, data_b64=encoded(payload)), tm, "video_save_failed")
    assert file_plugin.store.data[FILE_REGISTRY] == before
    file_plugin.store.fail_set.clear()
    send_chunks(file_plugin, sid, payload)
    committed = run(file_plugin, "gallery_add", op="video_commit", upload_id=sid)
    assert isinstance(committed, tm.Ok)
    assert file_bytes(file_plugin, committed.value["id"]) == payload
    assert not list(media_root(file_plugin).glob("*.part"))


def test_file_commit_failure_keeps_existing_wallpaper_and_retry(file_plugin, monkeypatch, tm):
    from forever_companion.core import media_files

    old = add_video(file_plugin).value["id"]
    run(file_plugin, "set_panel_appearance", bg_id=old)
    payload = synthetic_mp4()
    sid = begin(file_plugin, payload).value["upload_id"]
    send_chunks(file_plugin, sid, payload)
    original = media_files.commit_file

    def refuse(*args, **kwargs):
        raise media_files.MediaFileError("video_save_failed")

    monkeypatch.setattr(media_files, "commit_file", refuse)
    expect_error(run(file_plugin, "gallery_add", op="video_commit", upload_id=sid),
                 tm, "video_save_failed")
    assert run(file_plugin, "get_panel_gallery").value["appearance"]["bg_id"] == old
    assert file_bytes(file_plugin, old) == payload
    monkeypatch.setattr(media_files, "commit_file", original)
    retry = run(file_plugin, "gallery_add", op="video_commit", upload_id=sid)
    assert isinstance(retry, tm.Ok)
    assert file_bytes(file_plugin, retry.value["id"]) == payload


def test_manifest_publication_failure_keeps_retryable_file(file_plugin, tm):
    payload = synthetic_mp4()
    sid = begin(file_plugin, payload).value["upload_id"]
    send_chunks(file_plugin, sid, payload)
    original_set = file_plugin.store.set

    async def refuse_manifest(key, value):
        if key.startswith(FILE_MANIFEST_PREFIX):
            return tm.Err(tm.SdkError("synthetic_manifest_failure"))
        return await original_set(key, value)

    file_plugin.store.set = refuse_manifest
    expect_error(run(file_plugin, "gallery_add", op="video_commit", upload_id=sid),
                 tm, "video_save_failed")
    assert not file_plugin.store.data.get("gallery_index", {}).get("items")
    file_plugin.store.set = original_set
    retry = run(file_plugin, "gallery_add", op="video_commit", upload_id=sid)
    assert isinstance(retry, tm.Ok)
    assert file_bytes(file_plugin, retry.value["id"]) == payload


def test_index_publication_failure_reserves_id_and_restart_reclaims_unpublished(file_plugin, file_plugin_factory, tm):
    old = run(file_plugin, "gallery_add", data_url=IMAGE).value["id"]
    run(file_plugin, "set_panel_appearance", bg_id=old)
    sid = begin(file_plugin, synthetic_mp4()).value["upload_id"]
    send_chunks(file_plugin, sid, synthetic_mp4())
    original_set = file_plugin.store.set

    async def refuse_publication(key, value):
        if key == "gallery_index" and any(item.get("kind") == "video" for item in value["items"]):
            return tm.Err(tm.SdkError("synthetic_index_failure"))
        return await original_set(key, value)

    file_plugin.store.set = refuse_publication
    expect_error(run(file_plugin, "gallery_add", op="video_commit", upload_id=sid),
                 tm, "video_save_failed")
    assert [item["id"] for item in file_plugin.store.data["gallery_index"]["items"]] == [old]
    assert file_plugin.store.data["panel_appearance"]["bg_id"] == old
    reserved = file_plugin.store.data[FILE_REGISTRY]["records"][sid]["item_id"]
    other = run(file_plugin, "gallery_add", data_url=IMAGE).value["id"]
    assert reserved != other
    file_plugin.store.data[FILE_REGISTRY]["records"][sid]["pid"] = os.getpid() + 10_000_000
    file_plugin.store.data[FILE_REGISTRY]["records"][sid]["expires_at"] = 0
    restarted = file_plugin_factory(file_plugin.store.data, root=file_plugin._test_data_root)
    assert {item["id"] for item in run(restarted, "get_panel_gallery").value["items"]} == {old, other}
    assert not list(media_root(restarted).glob("*.bin"))
    assert not list(media_root(restarted).glob("*.part"))


def test_registry_finalization_failure_preserves_published_on_restart(file_plugin, file_plugin_factory, tm):
    sid = begin(file_plugin, synthetic_mp4()).value["upload_id"]
    send_chunks(file_plugin, sid, synthetic_mp4())
    original_set = file_plugin.store.set

    async def refuse_published_registry(key, value):
        if key == FILE_REGISTRY and value["records"].get(sid, {}).get("status") == "published":
            return tm.Err(tm.SdkError("synthetic_finalize_failure"))
        return await original_set(key, value)

    file_plugin.store.set = refuse_published_registry
    committed = run(file_plugin, "gallery_add", op="video_commit", upload_id=sid)
    assert isinstance(committed, tm.Ok)
    restarted = file_plugin_factory(file_plugin.store.data, root=file_plugin._test_data_root)
    assert len(run(restarted, "get_panel_gallery").value["items"]) == 1
    assert file_bytes(restarted, committed.value["id"]) == synthetic_mp4()


def test_delete_failure_keeps_tombstone_and_restart_retries(file_plugin, file_plugin_factory, monkeypatch, tm):
    from forever_companion.core import media_files

    gid = add_video(file_plugin).value["id"]
    run(file_plugin, "set_panel_appearance", bg_id=gid)
    sid = file_manifest(file_plugin, gid)["storage_id"]
    original = media_files.delete_file

    def refuse(*args, **kwargs):
        return False

    monkeypatch.setattr(media_files, "delete_file", refuse)
    expect_error(run(file_plugin, "gallery_remove", item_id=gid), tm, "video_cleanup_incomplete")
    assert file_plugin.store.data["gallery_index"]["items"] == []
    assert sid in file_plugin.store.data[FILE_REGISTRY]["records"]
    assert (media_root(file_plugin) / (sid + ".bin")).exists()
    monkeypatch.setattr(media_files, "delete_file", original)
    restarted = file_plugin_factory(file_plugin.store.data, root=file_plugin._test_data_root)
    result = run(restarted, "get_panel_gallery")
    assert isinstance(result, tm.Ok)
    assert result.value["items"] == [] and result.value["appearance"]["bg_id"] == ""
    assert sid not in restarted.store.data[FILE_REGISTRY]["records"]
    assert not (media_root(restarted) / (sid + ".bin")).exists()


def test_aborting_file_upload_does_not_touch_published_file(file_plugin, tm):
    gid = add_video(file_plugin).value["id"]
    run(file_plugin, "set_panel_appearance", bg_id=gid)
    sid = begin(file_plugin, synthetic_mp4()).value["upload_id"]
    send_chunks(file_plugin, sid, synthetic_mp4())
    assert isinstance(run(file_plugin, "gallery_add", op="video_abort", upload_id=sid), tm.Ok)
    assert not (media_root(file_plugin) / (sid + ".part")).exists()
    assert file_bytes(file_plugin, gid) == synthetic_mp4()
    assert run(file_plugin, "get_panel_gallery").value["appearance"]["bg_id"] == gid


class EnumeratingTestStore(FileTestStore):
    async def keys(self, prefix=None):
        return self.tm.Ok([key for key in self.data if prefix is None or key.startswith(prefix)])


def test_clear_reclaims_unindexed_store_media_when_keys_are_available(file_plugin_factory, tm):
    store = EnumeratingTestStore(tm)
    plugin = file_plugin_factory(store=store)
    add_video(plugin)
    unrelated = {"journal@TestUser": ["retain"], "stats@TestUser": {"days": 4}}
    orphaned = {
        "gallery_img/g999": {"data_url": IMAGE},
        "gallery_video/g888": {"storage_id": "d" * 32},
        video_chunk_key("d" * 32, 0): encoded(synthetic_mp4()),
        FILE_MANIFEST_PREFIX + "g777": {"storage_id": "e" * 32, "version": 1},
        "panel_bg": {"data_url": IMAGE},
    }
    store.data.update(copy.deepcopy(unrelated))
    store.data.update(copy.deepcopy(orphaned))
    result = clear(plugin, confirm=True)
    assert isinstance(result, tm.Ok), str(getattr(result, "error", ""))
    for key in orphaned:
        assert key not in store.data
    for key, value in unrelated.items():
        assert store.data[key] == value


def test_reclaim_failure_still_counts_file_against_budget(file_plugin, monkeypatch, tm):
    from forever_companion.core import media_files

    assert isinstance(run(file_plugin, "gallery_add", op="video_policy",
                          single_limit_mib=64, total_limit_mib=64), tm.Ok)
    pending = begin(file_plugin, synthetic_mp4(), size=40 * 1024 * 1024).value["upload_id"]
    first = synthetic_mp4(768 * 1024)
    run(file_plugin, "gallery_add", op="video_chunk", upload_id=pending,
        chunk_index=0, data_b64=encoded(first))
    monkeypatch.setattr(media_files, "delete_file", lambda *_args, **_kwargs: False)
    result = run(file_plugin, "gallery_add", op="video_abort", upload_id=pending)
    expect_error(result, tm, "video_cleanup_incomplete")
    assert pending in file_plugin.store.data[FILE_REGISTRY]["records"]
    before = copy.deepcopy(file_plugin.store.data[FILE_REGISTRY])
    denied = begin(file_plugin, synthetic_mp4(), size=30 * 1024 * 1024)
    assert isinstance(denied, tm.Err)
    assert str(denied.error) in {"video_storage_full", "video_cleanup_incomplete"}
    expect_error(run(file_plugin, "gallery_add", op="video_policy",
                     single_limit_mib=64, total_limit_mib=64), tm, "video_cleanup_incomplete")
    assert file_plugin.store.data[FILE_REGISTRY]["records"][pending] == before["records"][pending]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows sharing violation regression")
def test_clear_locked_windows_file_reports_error_then_cleans_after_release(file_plugin, tm):
    gid = add_video(file_plugin).value["id"]
    sid = file_manifest(file_plugin, gid)["storage_id"]
    path = media_root(file_plugin) / (sid + ".bin")
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = [
        ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p,
        ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p,
    ]
    create_file.restype = ctypes.c_void_p
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [ctypes.c_void_p]
    close_handle.restype = ctypes.c_int
    handle = create_file(str(path), 0x80000000, 0x00000001, None, 3, 0x80, None)
    assert handle not in (None, ctypes.c_void_p(-1).value), ctypes.get_last_error()
    try:
        expect_error(clear(file_plugin, confirm=True), tm, "video_cleanup_incomplete")
        assert path.exists()
        assert file_plugin.store.data[FILE_REGISTRY]["clear"] is not None
    finally:
        assert close_handle(handle) != 0
    assert isinstance(clear(file_plugin, confirm=True), tm.Ok)
    assert not path.exists()
    assert file_plugin.store.data[FILE_REGISTRY]["clear"] is None


def test_cancelled_disk_worker_cannot_recreate_cache_after_clear(file_plugin, monkeypatch, tm):
    from forever_companion.core import media_files

    payload = synthetic_mp4()
    sid = begin(file_plugin, payload).value["upload_id"]
    started = threading.Event()
    release = threading.Event()
    original = media_files.write_chunk

    def held_write(*args, **kwargs):
        started.set()
        assert release.wait(timeout=5), "test did not release its disk worker"
        return original(*args, **kwargs)

    monkeypatch.setattr(media_files, "write_chunk", held_write)

    async def exercise():
        task = asyncio.create_task(file_plugin.gallery_add(
            op="video_chunk", upload_id=sid, chunk_index=0, data_b64=encoded(payload),
        ))
        assert await asyncio.to_thread(started.wait, 2)
        task.cancel()
        clearing = asyncio.create_task(file_plugin.gallery_remove(op="clear_media", confirm=True))
        try:
            await asyncio.sleep(0.05)
            assert not clearing.done(), "clear returned while a cancelled worker could still write"
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        result = await asyncio.wait_for(clearing, timeout=5)
        assert isinstance(result, tm.Ok)

    try:
        asyncio.run(exercise())
    finally:
        release.set()
    assert not list(media_root(file_plugin).iterdir())
    assert not file_plugin.store.data[FILE_REGISTRY]["records"]
