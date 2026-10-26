"""File-backed media regression tests: temporary SDK roots and synthetic bytes only.

The MP4 payloads exercise upload framing, hashes, and persistence, not codecs.
No test opens a real host Store or the user's Steam installation.
"""

from __future__ import annotations

import asyncio
import base64
import copy
import hashlib
import json
import os
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest
from forever_companion.core.media import chunk_digest, video_chunk_count, video_chunk_key, video_manifest_key
from forever_companion.core.state import _GALLERY_VIDEO_CHUNK_BYTES

FILE_REGISTRY = "gallery_files_v1"
FILE_MANIFEST_PREFIX = "gallery_video_file/"
POSTER = "data:image/png;base64," + base64.b64encode(b"\x89PNG\r\n\x1a\nposter").decode("ascii")
IMAGE = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUg=="


def run(plugin, entry, **kwargs):
    return asyncio.run(getattr(plugin, entry)(**kwargs))


def encoded(raw):
    return base64.b64encode(raw).decode("ascii")


def synthetic_mp4(size=96):
    header = b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isommp42"
    return header + b"x" * (size - len(header))


class FileTestStore:
    """Copying Store with explicit faults and no filesystem implementation."""

    def __init__(self, tm, initial=None):
        self.tm = tm
        self.data = copy.deepcopy(initial or {})
        self.fail_get = set()
        self.fail_set = set()
        self.fail_delete = set()
        self.delay = False
        self.calls = []

    async def get(self, key):
        if self.delay:
            await asyncio.sleep(0.002)
        self.calls.append(("get", key))
        if key in self.fail_get:
            return self.tm.Err(self.tm.SdkError("synthetic_read_failure"))
        return self.tm.Ok(copy.deepcopy(self.data.get(key)))

    async def set(self, key, value):
        if self.delay:
            await asyncio.sleep(0.002)
        self.calls.append(("set", key))
        if key in self.fail_set:
            return self.tm.Err(self.tm.SdkError("synthetic_write_failure"))
        self.data[key] = copy.deepcopy(value)
        return self.tm.Ok(None)

    async def delete(self, key):
        self.calls.append(("delete", key))
        if key in self.fail_delete:
            return self.tm.Err(self.tm.SdkError("synthetic_delete_failure"))
        return self.tm.Ok(self.data.pop(key, None) is not None)


@pytest.fixture
def file_plugin_factory(boot_factory, tm, tmp_path):
    def factory(initial=None, *, root=None, store=None):
        data_root = Path(root) if root is not None else tmp_path / "sdk-persistent-data"
        data_root.mkdir(parents=True, exist_ok=True)
        plugin = boot_factory(store=store or FileTestStore(tm, initial))
        plugin.data_path = lambda relative="": data_root / relative
        plugin._test_data_root = data_root
        asyncio.run(plugin._load_state())
        asyncio.run(plugin._refresh_config())
        return plugin

    return factory


@pytest.fixture
def file_plugin(file_plugin_factory):
    return file_plugin_factory()


def begin(plugin, payload, **overrides):
    kwargs = {
        "name": "wallpaper.mp4", "mime": "video/mp4", "size": len(payload),
        "thumb": POSTER, "poster": POSTER,
    }
    kwargs.update(overrides)
    return run(plugin, "gallery_add", op="video_begin", **kwargs)


def send_chunks(plugin, upload_id, payload):
    for number, start in enumerate(range(0, len(payload), _GALLERY_VIDEO_CHUNK_BYTES)):
        result = run(
            plugin, "gallery_add", op="video_chunk", upload_id=upload_id, chunk_index=number,
            data_b64=encoded(payload[start:start + _GALLERY_VIDEO_CHUNK_BYTES]),
        )
        assert result.value == {"chunk_index": number, "accepted": True}


def add_video(plugin, payload=None, **overrides):
    payload = synthetic_mp4() if payload is None else payload
    started = begin(plugin, payload, **overrides)
    assert hasattr(started, "value"), str(getattr(started, "error", "begin failed"))
    upload_id = started.value["upload_id"]
    send_chunks(plugin, upload_id, payload)
    return run(plugin, "gallery_add", op="video_commit", upload_id=upload_id)


def expect_error(result, tm, code):
    assert isinstance(result, tm.Err)
    assert str(result.error) == code


def media_root(plugin):
    return plugin.data_path("wallpapers")


def file_manifest(plugin, item_id):
    return plugin.store.data[FILE_MANIFEST_PREFIX + item_id]


def file_bytes(plugin, item_id):
    manifest = file_manifest(plugin, item_id)
    return (media_root(plugin) / (manifest["storage_id"] + ".bin")).read_bytes()


def test_file_upload_stores_binary_and_no_base64_chunks(file_plugin, tm):
    payload = synthetic_mp4(_GALLERY_VIDEO_CHUNK_BYTES + 117)
    result = add_video(file_plugin, payload, name="../../private/unsafe.mp4")
    assert isinstance(result, tm.Ok)
    gid = result.value["id"]
    manifest = file_manifest(file_plugin, gid)
    assert manifest["version"] == 1 and manifest["storage"] == "file"
    assert file_bytes(file_plugin, gid) == payload
    assert not list(media_root(file_plugin).glob("*.part"))
    assert not any(key.startswith("gallery_video_chunk/") for key in file_plugin.store.data)
    assert not any(key.startswith("gallery_video/") for key in file_plugin.store.data)
    assert len(json.dumps(file_plugin.store.data[FILE_MANIFEST_PREFIX + gid])) < 2000
    assert str(file_plugin._test_data_root) not in json.dumps(file_plugin.store.data)
    metadata = run(file_plugin, "get_gallery_image", item_id=gid).value
    assert metadata["storage"] == "file" and metadata["schema_version"] == 1
    assert metadata["chunk_count"] == 2 and metadata["size"] == len(payload)
    assert "data_b64" not in metadata
    restored = b"".join(
        base64.b64decode(run(file_plugin, "get_gallery_image", item_id=gid, chunk_index=number).value["data_b64"])
        for number in range(metadata["chunk_count"])
    )
    assert hashlib.sha256(restored).digest() == hashlib.sha256(payload).digest()
    batched = run(
        file_plugin, "get_gallery_image", item_id=gid,
        chunk_index=0, chunk_count=2,
    ).value
    assert [block["chunk_index"] for block in batched["chunks"]] == [0, 1]
    assert hashlib.sha256(
        b"".join(base64.b64decode(block["data_b64"]) for block in batched["chunks"])
    ).digest() == hashlib.sha256(payload).digest()
    tail_batch = run(
        file_plugin, "get_gallery_image", item_id=gid,
        chunk_index=1, chunk_count=4,
    ).value
    assert [block["chunk_index"] for block in tail_batch["chunks"]] == [1]
    for invalid_count in (0, 5, True, 1.5, "2"):
        expect_error(
            run(
                file_plugin, "get_gallery_image", item_id=gid,
                chunk_index=0, chunk_count=invalid_count,
            ),
            tm,
            "video_chunk_invalid",
        )


def test_large_real_byte_upload_exceeds_old_48mib_and_64_chunks(file_plugin, tm):
    payload = synthetic_mp4(49 * 1024 * 1024 + 97)
    assert video_chunk_count(len(payload)) > 64
    result = add_video(file_plugin, payload)
    assert isinstance(result, tm.Ok)
    gid = result.value["id"]
    metadata = run(file_plugin, "get_gallery_image", item_id=gid).value
    assert metadata["size"] == len(payload)
    assert metadata["chunk_count"] == video_chunk_count(len(payload))
    observed = hashlib.sha256()
    for number in range(metadata["chunk_count"]):
        raw = run(file_plugin, "get_gallery_image", item_id=gid, chunk_index=number)
        assert isinstance(raw, tm.Ok)
        assert len(raw.value["data_b64"]) <= 1_048_576
        observed.update(base64.b64decode(raw.value["data_b64"]))
    assert observed.digest() == hashlib.sha256(payload).digest()
    assert len(file_bytes(file_plugin, gid)) == len(payload)
    assert not any(key.startswith("gallery_video_chunk/") for key in file_plugin.store.data)


@pytest.mark.parametrize("size", [_GALLERY_VIDEO_CHUNK_BYTES * 2 + 117, 198_941_764])
def test_varied_binary_upload_preserves_all_byte_values_and_short_tail(file_plugin, tm, size):
    header = synthetic_mp4(24)
    chunks = []
    for number, start in enumerate(range(0, size, _GALLERY_VIDEO_CHUNK_BYTES)):
        length = min(_GALLERY_VIDEO_CHUNK_BYTES, size - start)
        prefix = (header if number == 0 else number.to_bytes(4, "big")) + b"\r\n\x1a"
        pattern = bytes((value + number) % 256 for value in range(256))
        chunks.append(prefix + (pattern * ((length - len(prefix) + 255) // 256))[:length - len(prefix)])
    payload = b"".join(chunks)
    assert b"\r\n" in payload and b"\x1a" in payload
    assert len({chunk_digest(chunk) for chunk in chunks}) == len(chunks)
    started = begin(file_plugin, payload)
    assert isinstance(started, tm.Ok)
    upload_id = started.value["upload_id"]
    send_chunks(file_plugin, upload_id, payload)
    partial = media_root(file_plugin) / (upload_id + ".part")
    assert partial.read_bytes() == payload
    pending = file_plugin.store.data[FILE_REGISTRY]["records"][upload_id]
    assert pending["hashes"] == [chunk_digest(chunk) for chunk in chunks]
    result = run(file_plugin, "gallery_add", op="video_commit", upload_id=upload_id)
    assert isinstance(result, tm.Ok), str(getattr(result, "error", None))
    gid = result.value["id"]
    manifest = file_manifest(file_plugin, gid)
    assert manifest["hashes"] == [chunk_digest(chunk) for chunk in chunks]
    assert file_bytes(file_plugin, gid) == payload
    for number, chunk in enumerate(chunks):
        restored = run(file_plugin, "get_gallery_image", item_id=gid, chunk_index=number)
        assert isinstance(restored, tm.Ok)
        assert base64.b64decode(restored.value["data_b64"]) == chunk


@pytest.mark.parametrize("trailing_eof", [False, True])
def test_windows_text_eof_at_chunk_boundary_never_changes_media(file_plugin, tm, trailing_eof):
    first = synthetic_mp4(_GALLERY_VIDEO_CHUNK_BYTES - 1) + b"\x1a"
    tail = b"\r\nall-bytes:" + bytes(range(256))
    if trailing_eof:
        tail += b"\x1a"
    payload = first + tail
    result = add_video(file_plugin, payload)
    assert isinstance(result, tm.Ok)
    assert file_bytes(file_plugin, result.value["id"]) == payload


def test_chunk_receipts_query_durable_acceptance_without_mutating_store(file_plugin, tm):
    payload = synthetic_mp4(_GALLERY_VIDEO_CHUNK_BYTES + 17)
    started = begin(file_plugin, payload)
    assert started.value["chunk_receipts"] is True
    upload_id = started.value["upload_id"]
    first = payload[:_GALLERY_VIDEO_CHUNK_BYTES]
    assert isinstance(run(
        file_plugin, "gallery_add", op="video_chunk", upload_id=upload_id,
        chunk_index=0, data_b64=encoded(first),
    ), tm.Ok)
    before = copy.deepcopy(file_plugin.store.data)
    file_plugin.store.calls.clear()
    for number, accepted in [(0, True), (1, False), (0, True)]:
        result = run(
            file_plugin, "gallery_add", op="video_status", upload_id=upload_id, chunk_index=number,
        )
        assert isinstance(result, tm.Ok)
        assert result.value == {"chunk_index": number, "accepted": accepted}
    assert file_plugin.store.data == before
    assert all(method == "get" for method, _key in file_plugin.store.calls)
    assert (media_root(file_plugin) / (upload_id + ".part")).read_bytes() == first


def test_chunk_receipts_require_persisted_registry_not_only_physical_file(file_plugin, tm):
    payload = synthetic_mp4(_GALLERY_VIDEO_CHUNK_BYTES + 17)
    upload_id = begin(file_plugin, payload).value["upload_id"]
    first = payload[:_GALLERY_VIDEO_CHUNK_BYTES]
    chunk_request = {
        "op": "video_chunk", "upload_id": upload_id, "chunk_index": 0, "data_b64": encoded(first),
    }
    file_plugin.store.fail_set.add(FILE_REGISTRY)
    expect_error(run(file_plugin, "gallery_add", **chunk_request), tm, "video_save_failed")
    partial = media_root(file_plugin) / (upload_id + ".part")
    assert partial.read_bytes() == first
    record = file_plugin.store.data[FILE_REGISTRY]["records"][upload_id]
    assert record["next_chunk"] == 0 and record["hashes"] == []
    before = copy.deepcopy(file_plugin.store.data)
    file_plugin.store.calls.clear()
    unconfirmed = run(
        file_plugin, "gallery_add", op="video_status", upload_id=upload_id, chunk_index=0,
    )
    assert isinstance(unconfirmed, tm.Ok)
    assert unconfirmed.value == {"chunk_index": 0, "accepted": False}
    assert file_plugin.store.data == before
    assert all(method == "get" for method, _key in file_plugin.store.calls)
    file_plugin.store.fail_set.remove(FILE_REGISTRY)
    retried = run(file_plugin, "gallery_add", **chunk_request)
    assert isinstance(retried, tm.Ok)
    assert partial.read_bytes() == first
    confirmed = run(
        file_plugin, "gallery_add", op="video_status", upload_id=upload_id, chunk_index=0,
    )
    assert isinstance(confirmed, tm.Ok)
    assert confirmed.value == {"chunk_index": 0, "accepted": True}
    record = file_plugin.store.data[FILE_REGISTRY]["records"][upload_id]
    assert record["next_chunk"] == 1 and record["hashes"] == [chunk_digest(first)]


@pytest.mark.parametrize("invalid_index", [-1, 2, True, "0", None])
def test_chunk_receipts_reject_invalid_positions(file_plugin, tm, invalid_index):
    payload = synthetic_mp4(_GALLERY_VIDEO_CHUNK_BYTES + 17)
    upload_id = begin(file_plugin, payload).value["upload_id"]
    before = copy.deepcopy(file_plugin.store.data)
    expect_error(run(
        file_plugin, "gallery_add", op="video_status", upload_id=upload_id, chunk_index=invalid_index,
    ), tm, "video_chunk_invalid")
    assert file_plugin.store.data == before


@pytest.mark.parametrize("terminal", ["expired", "published", "deleting", "clear_pending", "abort", "clear"])
def test_chunk_receipts_never_acknowledge_expired_or_terminated_uploads(file_plugin, tm, terminal):
    payload = synthetic_mp4()
    upload_id = begin(file_plugin, payload).value["upload_id"]
    send_chunks(file_plugin, upload_id, payload)
    expected = "video_upload_not_found"
    registry = file_plugin.store.data[FILE_REGISTRY]
    if terminal == "expired":
        registry["records"][upload_id]["expires_at"] = 0
        expected = "video_upload_expired"
    elif terminal == "published":
        assert isinstance(run(file_plugin, "gallery_add", op="video_commit", upload_id=upload_id), tm.Ok)
    elif terminal == "deleting":
        registry["records"][upload_id]["status"] = "deleting"
    elif terminal == "clear_pending":
        registry["clear"] = {"keys": []}
        expected = "video_cleanup_incomplete"
    elif terminal == "abort":
        assert isinstance(run(file_plugin, "gallery_add", op="video_abort", upload_id=upload_id), tm.Ok)
    else:
        assert isinstance(run(file_plugin, "gallery_remove", op="clear_media", confirm=True), tm.Ok)
    before = copy.deepcopy(file_plugin.store.data)
    existing = {path.name: path.read_bytes() for path in media_root(file_plugin).iterdir()}
    file_plugin.store.calls.clear()
    expect_error(run(
        file_plugin, "gallery_add", op="video_status", upload_id=upload_id, chunk_index=0,
    ), tm, expected)
    assert file_plugin.store.data == before
    assert all(method == "get" for method, _key in file_plugin.store.calls)
    assert {path.name: path.read_bytes() for path in media_root(file_plugin).iterdir()} == existing


@pytest.mark.parametrize("failure", ["read_failed", "invalid_digest", "missing_record", "cleanup_pending"])
def test_chunk_receipts_fail_closed_without_registry_writes(file_plugin, tm, failure):
    payload = synthetic_mp4()
    upload_id = begin(file_plugin, payload).value["upload_id"]
    send_chunks(file_plugin, upload_id, payload)
    expected = "video_read_failed"
    if failure == "read_failed":
        file_plugin.store.fail_get.add(FILE_REGISTRY)
    elif failure == "invalid_digest":
        file_plugin.store.data[FILE_REGISTRY]["records"][upload_id]["hashes"] = ["invalid"]
    elif failure == "missing_record":
        del file_plugin.store.data[FILE_REGISTRY]["records"][upload_id]
        expected = "video_upload_not_found"
    else:
        file_plugin._gallery_cleanup_pending = True
        expected = "video_cleanup_incomplete"
    before = copy.deepcopy(file_plugin.store.data)
    file_plugin.store.calls.clear()
    expect_error(run(
        file_plugin, "gallery_add", op="video_status", upload_id=upload_id, chunk_index=0,
    ), tm, expected)
    assert file_plugin.store.data == before
    assert all(method == "get" for method, _key in file_plugin.store.calls)


def test_restart_upgrade_and_sdk_root_relocation_preserve_files(file_plugin, file_plugin_factory, tmp_path, tm):
    payload = synthetic_mp4(_GALLERY_VIDEO_CHUNK_BYTES + 23)
    gid = add_video(file_plugin, payload).value["id"]
    run(file_plugin, "set_panel_appearance", bg_id=gid, motion=False, brightness=118)
    initial = copy.deepcopy(file_plugin.store.data)
    restarted = file_plugin_factory(initial, root=file_plugin._test_data_root)
    metadata = run(restarted, "get_gallery_image", item_id=gid)
    assert isinstance(metadata, tm.Ok) and metadata.value["storage"] == "file"
    assert file_bytes(restarted, gid) == payload
    assert run(restarted, "get_panel_gallery").value["appearance"]["bg_id"] == gid
    relocated_root = tmp_path / "different-host-root" / "plugin-data"
    shutil.copytree(file_plugin._test_data_root, relocated_root)
    relocated = file_plugin_factory(initial, root=relocated_root)
    assert file_bytes(relocated, gid) == payload
    assert run(relocated, "get_panel_gallery").value["appearance"]["motion"] is False
    assert str(file_plugin._test_data_root) not in json.dumps(relocated.store.data)
    for _ in range(3):
        relocated = file_plugin_factory(relocated.store.data, root=relocated_root)
        assert file_bytes(relocated, gid) == payload
        assert len(run(relocated, "get_panel_gallery").value["items"]) == 1


def legacy_video_data(payload):
    sid = "a" * 32
    count = video_chunk_count(len(payload))
    chunks = [
        payload[number * _GALLERY_VIDEO_CHUNK_BYTES:(number + 1) * _GALLERY_VIDEO_CHUNK_BYTES]
        for number in range(count)
    ]
    item = {
        "id": "g9", "name": "legacy.mp4", "kind": "video", "mime": "video/mp4",
        "size": len(payload), "thumb": POSTER, "added_at": "2026-10-01T00:00:00+00:00",
    }
    data = {
        "gallery_index": {"items": [item], "next": 10},
        video_manifest_key("g9"): {
            "storage_id": sid, "mime": "video/mp4", "size": len(payload), "chunk_count": count,
            "poster": POSTER, "hashes": [chunk_digest(chunk) for chunk in chunks],
        },
        "panel_appearance": {"bg_id": "g9", "motion": False},
    }
    data.update({video_chunk_key(sid, number): encoded(chunk) for number, chunk in enumerate(chunks)})
    return data


def test_legacy_store_video_reads_unchanged_beside_new_file_media(file_plugin_factory, tm):
    payload = synthetic_mp4(_GALLERY_VIDEO_CHUNK_BYTES + 17)
    initial = legacy_video_data(payload)
    plugin = file_plugin_factory(initial)
    metadata = run(plugin, "get_gallery_image", item_id="g9").value
    restored = b"".join(
        base64.b64decode(run(plugin, "get_gallery_image", item_id="g9", chunk_index=number).value["data_b64"])
        for number in range(metadata["chunk_count"])
    )
    assert restored == payload
    assert run(plugin, "get_panel_gallery").value["appearance"]["bg_id"] == "g9"
    assert not list(media_root(plugin).glob("*.bin"))
    newer = add_video(plugin)
    assert isinstance(newer, tm.Ok) and newer.value["id"] != "g9"
    for key, value in initial.items():
        if key.startswith("gallery_video"):
            assert plugin.store.data[key] == value
    assert file_bytes(plugin, newer.value["id"]) == synthetic_mp4()


def test_unknown_file_registry_schema_refuses_writes_without_overwriting(file_plugin, tm):
    future = {"version": 999, "records": {"future-record": {"private": "retain"}}, "future": True}
    file_plugin.store.data[FILE_REGISTRY] = copy.deepcopy(future)
    before = copy.deepcopy(file_plugin.store.data)
    expect_error(begin(file_plugin, synthetic_mp4()), tm, "video_schema_unsupported")
    expect_error(run(file_plugin, "gallery_add", data_url=IMAGE), tm, "video_schema_unsupported")
    assert file_plugin.store.data == before


def test_unknown_file_manifest_schema_does_not_normalize_or_delete(file_plugin, tm):
    gid = add_video(file_plugin).value["id"]
    key = FILE_MANIFEST_PREFIX + gid
    file_plugin.store.data[key]["version"] = 777
    before = copy.deepcopy(file_plugin.store.data)
    payload = file_bytes(file_plugin, gid)
    expect_error(run(file_plugin, "get_gallery_image", item_id=gid), tm, "video_schema_unsupported")
    expect_error(run(file_plugin, "gallery_remove", item_id=gid), tm, "video_schema_unsupported")
    assert file_plugin.store.data == before
    assert file_bytes(file_plugin, gid) == payload


@pytest.mark.parametrize("field", ["single_limit_mib", "total_limit_mib"])
@pytest.mark.parametrize("bad", [True, "256", 0, -1, 4.5, None, [], 65536])
def test_policy_rejects_invalid_values_without_mutating_library(file_plugin, tm, field, bad):
    gid = add_video(file_plugin).value["id"]
    before = copy.deepcopy(file_plugin.store.data)
    kwargs = {"single_limit_mib": 256, "total_limit_mib": 2048, field: bad}
    expect_error(run(file_plugin, "gallery_add", op="video_policy", **kwargs), tm, "video_policy_invalid")
    assert file_plugin.store.data == before
    assert file_bytes(file_plugin, gid) == synthetic_mp4()


def test_lower_budget_retains_existing_media_and_counts_staging(file_plugin, tm):
    assert isinstance(run(file_plugin, "gallery_add", op="video_policy",
                          single_limit_mib=256, total_limit_mib=256), tm.Ok)
    payload = synthetic_mp4(33 * 1024 * 1024 + 17)
    gid = add_video(file_plugin, payload).value["id"]
    policy = run(file_plugin, "gallery_add", op="video_policy", single_limit_mib=32, total_limit_mib=64)
    assert isinstance(policy, tm.Ok)
    assert file_bytes(file_plugin, gid) == payload
    expect_error(begin(file_plugin, payload), tm, "video_too_large")
    pending = begin(file_plugin, synthetic_mp4(), size=30 * 1024 * 1024)
    assert isinstance(pending, tm.Ok)
    expect_error(begin(file_plugin, synthetic_mp4(), size=2 * 1024 * 1024), tm, "video_storage_full")
    assert file_bytes(file_plugin, gid) == payload
    assert isinstance(run(file_plugin, "gallery_add", op="video_abort",
                          upload_id=pending.value["upload_id"]), tm.Ok)
    assert isinstance(begin(file_plugin, synthetic_mp4()), tm.Ok)


def test_disk_space_refusal_does_not_allocate_media(file_plugin, monkeypatch, tm):
    from forever_companion.core import media_files

    monkeypatch.setattr(media_files.shutil, "disk_usage",
                        lambda _path: SimpleNamespace(total=100_000_000, used=99_999_999, free=1))
    result = begin(file_plugin, synthetic_mp4())
    expect_error(result, tm, "video_disk_full")
    assert not list(media_root(file_plugin).glob("*.part"))
    assert not list(media_root(file_plugin).glob("*.bin"))
    assert not file_plugin.store.data.get(FILE_REGISTRY, {}).get("records")


@pytest.mark.parametrize("operation", ["write_chunk", "read_chunk", "commit_file", "delete_file"])
@pytest.mark.parametrize("sid", ["../escape", "a/../../escape", r"..\escape", "/absolute", r"C:\private", "a" * 31])
def test_file_api_rejects_path_like_storage_ids(tmp_path, operation, sid):
    from forever_companion.core import media_files

    root = tmp_path / "wallpapers"
    root.mkdir()
    fn = getattr(media_files, operation)
    kwargs = {
        "write_chunk": (root, sid, 0, synthetic_mp4(), 96),
        "read_chunk": (root, sid, 0, 96, 96),
        "commit_file": (root, sid, 96),
        "delete_file": (root, sid),
    }
    with pytest.raises(media_files.MediaFileError, match="video_path_unsafe"):
        fn(*kwargs[operation])
    assert not list(root.iterdir())


def test_media_root_symlink_is_rejected_without_touching_target(tmp_path):
    from forever_companion.core import media_files

    outside = tmp_path / "outside"
    outside.mkdir()
    marker = outside / "retain.txt"
    marker.write_text("not media", encoding="ascii")
    root = tmp_path / "wallpapers"
    try:
        os.symlink(outside, root, target_is_directory=True)
    except OSError:
        pytest.skip("OS does not permit directory symlink creation")
    plugin = SimpleNamespace(data_path=lambda relative="": tmp_path / relative)
    with pytest.raises(media_files.MediaFileError, match="video_path_unsafe"):
        media_files.safe_wallpaper_root(plugin)
    assert marker.read_text(encoding="ascii") == "not media"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows directory reparse-point contract")
def test_media_root_windows_junction_is_rejected(tmp_path):
    from forever_companion.core import media_files

    outside = tmp_path / "outside"
    outside.mkdir()
    marker = outside / "retain.txt"
    marker.write_text("not media", encoding="ascii")
    root = tmp_path / "wallpapers"
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(root), str(outside)],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    plugin = SimpleNamespace(data_path=lambda relative="": tmp_path / relative)
    with pytest.raises(media_files.MediaFileError, match="video_path_unsafe"):
        media_files.safe_wallpaper_root(plugin)
    assert marker.read_text(encoding="ascii") == "not media"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows directory reparse-point contract")
def test_sdk_root_parent_junction_is_rejected(tmp_path):
    from forever_companion.core import media_files

    outside = tmp_path / "external-target"
    outside.mkdir()
    alias = tmp_path / "redirected-data-root"
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(alias), str(outside)],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    plugin = SimpleNamespace(data_path=lambda relative="": alias / "plugin-data" / relative)
    with pytest.raises(media_files.MediaFileError, match="video_path_unsafe"):
        media_files.safe_wallpaper_root(plugin)
    assert not (outside / "plugin-data" / "wallpapers").exists()


@pytest.mark.parametrize("location", ["plugin_dir", "config_dir"])
def test_sdk_storage_refuses_installation_or_config_directory(tmp_path, location):
    from forever_companion.core import media_files

    installation = tmp_path / "installed-plugin"
    installation.mkdir()
    plugin = SimpleNamespace(
        data_path=lambda relative="": installation / relative,
        **{location: installation},
    )
    with pytest.raises(media_files.MediaFileError, match="video_path_unsafe"):
        media_files.safe_wallpaper_root(plugin)
    assert not (installation / "wallpapers").exists()


def test_sdk_storage_refuses_outside_root_without_creating_it(tmp_path):
    from forever_companion.core import media_files

    base = tmp_path / "safe-data"
    outside = tmp_path / "outside"
    plugin = SimpleNamespace(data_path=lambda relative="": outside if relative else base)
    with pytest.raises(media_files.MediaFileError, match="video_path_unsafe"):
        media_files.safe_wallpaper_root(plugin)
    assert not outside.exists()


def test_sdk_storage_resolver_is_required_and_never_guesses_a_drive():
    from forever_companion.core import media_files

    with pytest.raises(media_files.MediaFileError, match="video_path_unsafe"):
        media_files.safe_wallpaper_root(SimpleNamespace())


def test_file_api_rejects_hardlink_payload_without_modifying_target(tmp_path):
    from forever_companion.core import media_files

    outside = tmp_path / "outside.bin"
    outside.write_bytes(synthetic_mp4())
    root = tmp_path / "wallpapers"
    root.mkdir()
    sid = "f" * 32
    os.link(outside, root / (sid + ".part"))
    with pytest.raises(media_files.MediaFileError, match="video_path_unsafe"):
        media_files.write_chunk(root, sid, 0, b"z" * 96, 96)
    assert outside.read_bytes() == synthetic_mp4()


def test_tampered_file_chunk_is_rejected_and_not_published(file_plugin, tm):
    payload = synthetic_mp4()
    upload_id = begin(file_plugin, payload).value["upload_id"]
    send_chunks(file_plugin, upload_id, payload)
    partial = media_root(file_plugin) / (upload_id + ".part")
    partial.write_bytes(payload[:-1] + b"z")
    messages = []
    file_plugin.logger.warning = lambda template, *args: messages.append(template.format(*args))
    expect_error(run(file_plugin, "gallery_add", op="video_commit", upload_id=upload_id),
                 tm, "video_chunk_invalid")
    assert not file_plugin.store.data.get("gallery_index", {}).get("items")
    assert messages == ["gallery video integrity failed: chunk_index=0"]


def test_uploads_serialized_across_threads_and_event_loops(file_plugin, tm):
    file_plugin.store.delay = True
    payload = synthetic_mp4(_GALLERY_VIDEO_CHUNK_BYTES + 41)
    first = begin(file_plugin, payload).value["upload_id"]
    second = begin(file_plugin, payload).value["upload_id"]
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(send_chunks, file_plugin, sid, payload) for sid in (first, second)]
        for future in futures:
            future.result(timeout=10)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(run, file_plugin, "gallery_add", op="video_commit", upload_id=sid)
            for sid in (first, second)
        ]
        results = [future.result(timeout=10) for future in futures]
    assert all(isinstance(result, tm.Ok) for result in results)
    ids = [result.value["id"] for result in results]
    assert len(set(ids)) == 2
    assert all(file_bytes(file_plugin, gid) == payload for gid in ids)
    assert len(run(file_plugin, "get_panel_gallery").value["items"]) == 2
    assert file_plugin._gallery_busy is False


def test_independent_plugin_instances_share_lock_and_live_upload_ownership(file_plugin_factory, tm):
    shared = FileTestStore(tm)
    shared.delay = True
    first = file_plugin_factory(store=shared)
    second = file_plugin_factory(store=shared, root=first._test_data_root)
    payload = synthetic_mp4(_GALLERY_VIDEO_CHUNK_BYTES + 41)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(begin, plugin, payload) for plugin in (first, second)]
        starts = [future.result(timeout=10) for future in futures]
    assert all(isinstance(result, tm.Ok) for result in starts)
    uploads = [result.value["upload_id"] for result in starts]
    assert len(set(uploads)) == 2
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(send_chunks, plugin, sid, payload)
            for plugin, sid in zip((first, second), uploads, strict=True)
        ]
        for future in futures:
            future.result(timeout=10)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(run, plugin, "gallery_add", op="video_commit", upload_id=sid)
            for plugin, sid in zip((first, second), uploads, strict=True)
        ]
        committed = [future.result(timeout=10) for future in futures]
    assert all(isinstance(result, tm.Ok) for result in committed)
    ids = [result.value["id"] for result in committed]
    assert len(set(ids)) == 2
    assert all(file_bytes(first, gid) == payload for gid in ids)
    assert {item["id"] for item in run(first, "get_panel_gallery").value["items"]} == set(ids)


def test_file_chunk_ack_loss_retries_same_hash_without_duplicate_write(file_plugin, monkeypatch, tm):
    from forever_companion.core import media_files

    payload = synthetic_mp4(_GALLERY_VIDEO_CHUNK_BYTES + 17)
    upload_id = begin(file_plugin, payload).value["upload_id"]
    original_write = media_files.write_chunk
    writes = []

    def tracked_write(*args):
        writes.append((args[1], args[2], len(args[3])))
        return original_write(*args)

    monkeypatch.setattr(media_files, "write_chunk", tracked_write)
    first = payload[:_GALLERY_VIDEO_CHUNK_BYTES]
    kwargs = {
        "op": "video_chunk", "upload_id": upload_id,
        "chunk_index": 0, "data_b64": encoded(first),
    }
    accepted = run(file_plugin, "gallery_add", **kwargs)
    retried = run(file_plugin, "gallery_add", **kwargs)
    assert isinstance(accepted, tm.Ok) and retried.value == accepted.value
    assert writes == [(upload_id, 0, len(first))]
    record = file_plugin.store.data[FILE_REGISTRY]["records"][upload_id]
    assert record["next_chunk"] == 1 and record["hashes"] == [chunk_digest(first)]

    different = first[:-1] + b"z"
    expect_error(
        run(file_plugin, "gallery_add", **{**kwargs, "data_b64": encoded(different)}),
        tm, "video_chunk_invalid",
    )
    assert writes == [(upload_id, 0, len(first))]
    assert (media_root(file_plugin) / (upload_id + ".part")).read_bytes() == first
    tail = run(
        file_plugin, "gallery_add", op="video_chunk", upload_id=upload_id,
        chunk_index=1, data_b64=encoded(payload[_GALLERY_VIDEO_CHUNK_BYTES:]),
    )
    assert isinstance(tail, tm.Ok)
    committed = run(file_plugin, "gallery_add", op="video_commit", upload_id=upload_id)
    assert isinstance(committed, tm.Ok)
    assert file_bytes(file_plugin, committed.value["id"]) == payload
    assert len(writes) == 2


def test_published_file_commit_ack_loss_is_idempotent_across_restart_and_clear(
    file_plugin, file_plugin_factory, monkeypatch, tm,
):
    from forever_companion.core import media_files

    payload = synthetic_mp4(_GALLERY_VIDEO_CHUNK_BYTES + 17)
    upload_id = begin(file_plugin, payload).value["upload_id"]
    send_chunks(file_plugin, upload_id, payload)
    original_commit = media_files.commit_file
    commits = []

    def tracked_commit(*args):
        commits.append(args[1])
        return original_commit(*args)

    monkeypatch.setattr(media_files, "commit_file", tracked_commit)
    first = run(file_plugin, "gallery_add", op="video_commit", upload_id=upload_id)
    assert isinstance(first, tm.Ok)
    retry = run(file_plugin, "gallery_add", op="video_commit", upload_id=upload_id)
    assert isinstance(retry, tm.Ok) and retry.value == first.value
    restarted = file_plugin_factory(file_plugin.store.data, root=file_plugin._test_data_root)
    after_restart = run(restarted, "gallery_add", op="video_commit", upload_id=upload_id)
    assert isinstance(after_restart, tm.Ok) and after_restart.value == first.value
    assert commits == [upload_id]
    assert [item["id"] for item in run(restarted, "get_panel_gallery").value["items"]] == [first.value["id"]]
    assert len(list(media_root(restarted).glob("*.bin"))) == 1
    assert not list(media_root(restarted).glob("*.part"))
    assert file_bytes(restarted, first.value["id"]) == payload

    cleared = run(restarted, "gallery_remove", op="clear_media", confirm=True)
    assert isinstance(cleared, tm.Ok)
    assert not list(media_root(restarted).iterdir())
    assert not restarted.store.data[FILE_REGISTRY]["records"]
    expect_error(
        run(restarted, "gallery_add", op="video_commit", upload_id=upload_id),
        tm, "video_upload_not_found",
    )
    assert run(restarted, "get_panel_gallery").value["items"] == []


def test_media_runtime_logs_report_real_chunk_size_without_payload_or_paths(file_plugin, tm):
    payload = synthetic_mp4()
    upload_id = begin(file_plugin, payload).value["upload_id"]
    messages = []
    file_plugin.logger.info = lambda template, *args: messages.append(template.format(*args))
    accepted = run(
        file_plugin, "gallery_add", op="video_chunk", upload_id=upload_id,
        chunk_index=0, data_b64=encoded(payload),
    )
    assert isinstance(accepted, tm.Ok)
    assert any("gallery media start: op=video_chunk chunk_index=0 size=96" in text for text in messages)
    assert any(
        "gallery media op: op=video_chunk chunk_index=0 size=96" in text and "status=ok" in text
        for text in messages
    )
    private_operation = "video_private_" + upload_id
    expect_error(
        run(file_plugin, "gallery_add", op=private_operation, upload_id=upload_id),
        tm, "video_operation_invalid",
    )
    assert any("op=invalid" in text and "status=error:video_operation_invalid" in text for text in messages)
    rendered = "\n".join(messages)
    assert upload_id not in rendered
    assert encoded(payload) not in rendered
    assert str(file_plugin._test_data_root) not in rendered
    assert private_operation not in rendered


def test_media_read_logs_report_metadata_chunk_sizes_and_failed_integrity(file_plugin, tm):
    payload = synthetic_mp4(_GALLERY_VIDEO_CHUNK_BYTES + 117)
    result = add_video(file_plugin, payload, name="private-source-name.mp4")
    assert isinstance(result, tm.Ok)
    item_id = result.value["id"]
    messages = []
    file_plugin.logger.info = lambda template, *args: messages.append(template.format(*args))
    assert isinstance(run(file_plugin, "get_gallery_image", item_id=item_id), tm.Ok)
    for number in range(2):
        assert isinstance(run(file_plugin, "get_gallery_image", item_id=item_id, chunk_index=number), tm.Ok)
    for number, size in [(-1, len(payload)), (0, _GALLERY_VIDEO_CHUNK_BYTES), (1, 117)]:
        assert any(
            f"gallery media op: op=gallery_read chunk_index={number} size={size}" in message
            and "status=ok" in message
            for message in messages
        )
    manifest = file_manifest(file_plugin, item_id)
    asset = media_root(file_plugin) / (manifest["storage_id"] + ".bin")
    asset.write_bytes(payload[:-1] + b"z")
    expect_error(
        run(file_plugin, "get_gallery_image", item_id=item_id, chunk_index=1),
        tm, "video_read_failed",
    )
    assert any(
        "op=gallery_read chunk_index=1" in message and "status=error:video_read_failed" in message
        for message in messages
    )
    rendered = "\n".join(messages)
    for private in [manifest["storage_id"], str(asset), "private-source-name.mp4", POSTER, encoded(payload)]:
        assert private not in rendered


@pytest.mark.parametrize("cancelled", [False, True])
@pytest.mark.parametrize("method_name", ["_video_operation", "_get_gallery_image"])
def test_media_runtime_logs_do_not_label_exceptions_as_success(file_plugin, cancelled, method_name):
    messages = []
    file_plugin.logger.info = lambda template, *args: messages.append(template.format(*args))
    failure = asyncio.CancelledError("private cancellation") if cancelled else RuntimeError("private exception")

    async def _video_operation(**_kwargs):
        raise failure

    _video_operation.__name__ = method_name
    with pytest.raises(type(failure)):
        asyncio.run(file_plugin._gallery_call(_video_operation, op="video_commit"))
    expected = "status=cancelled" if cancelled else "status=exception:RuntimeError"
    assert any(expected in text for text in messages)
    assert not any("status=ok" in text for text in messages)
    assert not any(str(failure) in text for text in messages)
