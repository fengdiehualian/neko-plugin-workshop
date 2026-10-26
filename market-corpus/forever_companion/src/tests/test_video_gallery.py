"""Video gallery contract and failure safety with a copying, fault-injectable Store."""

from __future__ import annotations

import ast
import asyncio
import base64
import copy
import json
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from forever_companion.core.media import video_chunk_key, video_manifest_key
from forever_companion.core.state import (
    _GALLERY_MAX_ITEMS,
    _GALLERY_VIDEO_CHUNK_BYTES,
    _GALLERY_VIDEO_MAX_BYTES,
    _STORE_GALLERY_UPLOADS,
)

# These assertions describe the pre-1.4 Store-chunk writer (including its
# removed 32/128 MiB budget).  File-backed media and legacy read compatibility
# are covered by test_media_files.py; keep this historical contract visible
# without treating obsolete write expectations as current regressions.
pytestmark = pytest.mark.skip(
    reason="superseded by file-backed media tests; legacy Store reads remain covered",
)

POSTER = "data:image/png;base64," + base64.b64encode(b"\x89PNG\r\n\x1a\nposter").decode("ascii")
IMAGE = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUg=="


def run(plugin, entry, **kwargs):
    return asyncio.run(getattr(plugin, entry)(**kwargs))


def encoded(raw):
    return base64.b64encode(raw).decode("ascii")


def mp4(size=96):
    header = b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isommp42"
    return header + b"x" * (size - len(header))


class CopyingStore:
    def __init__(self, tm, initial=None):
        self.tm = tm
        self.data = copy.deepcopy(initial or {})
        self.fail_get = set()
        self.fail_set = set()
        self.fail_delete = set()
        self.enabled = True
        self.delay = False

    async def get(self, key):
        if self.delay:
            await asyncio.sleep(0.002)
        if key in self.fail_get:
            return self.tm.Err(self.tm.SdkError("test_read_failure"))
        return self.tm.Ok(copy.deepcopy(self.data.get(key)))

    async def set(self, key, value):
        if self.delay:
            await asyncio.sleep(0.002)
        if key in self.fail_set:
            return self.tm.Err(self.tm.SdkError("test_write_failure"))
        if self.enabled:
            self.data[key] = copy.deepcopy(value)
        return self.tm.Ok(None)

    async def delete(self, key):
        if key in self.fail_delete:
            return self.tm.Err(self.tm.SdkError("test_delete_failure"))
        return self.tm.Ok(self.data.pop(key, None) is not None)


@pytest.fixture
def media_plugin(plugin_factory_full, tm):
    plugin = plugin_factory_full()
    plugin.store = CopyingStore(tm, plugin.store.data)
    return plugin


def begin(plugin, payload, **overrides):
    args = {"name": "wallpaper.mp4", "mime": "video/mp4", "size": len(payload), "thumb": POSTER, "poster": POSTER}
    args.update(overrides)
    return run(plugin, "gallery_add", op="video_begin", **args)


def send_chunks(plugin, upload_id, payload):
    for number, start in enumerate(range(0, len(payload), _GALLERY_VIDEO_CHUNK_BYTES)):
        result = run(
            plugin, "gallery_add", op="video_chunk", upload_id=upload_id,
            chunk_index=number, data_b64=encoded(payload[start:start + _GALLERY_VIDEO_CHUNK_BYTES]),
        )
        assert result.value == {"chunk_index": number, "accepted": True}


def add_video(plugin, payload=None, **overrides):
    payload = mp4() if payload is None else payload
    started = begin(plugin, payload, **overrides)
    upload_id = started.value["upload_id"]
    send_chunks(plugin, upload_id, payload)
    return run(plugin, "gallery_add", op="video_commit", upload_id=upload_id)


def error(result, tm, code):
    assert isinstance(result, tm.Err)
    assert str(result.error) == code


def test_video_roundtrip_is_chunked_and_metadata_remains_small(media_plugin, tm):
    payload = mp4(_GALLERY_VIDEO_CHUNK_BYTES + 43)
    result = add_video(media_plugin, payload)
    assert isinstance(result, tm.Ok)
    item = result.value["items"][0]
    assert item["kind"] == "video" and item["size"] == len(payload)
    assert item["mime"] == "video/mp4" and item["thumb"] == POSTER
    metadata = run(media_plugin, "get_gallery_image", item_id=item["id"]).value
    assert metadata == {
        "kind": "video", "mime": "video/mp4", "size": len(payload), "chunk_count": 2,
        "chunk_bytes": _GALLERY_VIDEO_CHUNK_BYTES, "poster": POSTER, "name": "wallpaper.mp4",
    }
    assert len(json.dumps(metadata)) < 1000
    restored = b""
    for number in range(metadata["chunk_count"]):
        block = run(media_plugin, "get_gallery_image", item_id=item["id"], chunk_index=number).value
        assert set(block) == {"chunk_index", "data_b64"}
        assert len(block["data_b64"]) <= 1_048_576
        restored += base64.b64decode(block["data_b64"])
    assert restored == payload
    batched = run(
        media_plugin, "get_gallery_image", item_id=item["id"],
        chunk_index=0, chunk_count=2,
    ).value
    assert set(batched) == {"chunks"}
    assert [block["chunk_index"] for block in batched["chunks"]] == [0, 1]
    assert b"".join(base64.b64decode(block["data_b64"]) for block in batched["chunks"]) == payload
    assert media_plugin.store.data[_STORE_GALLERY_UPLOADS]["uploads"] == {}
    assert all("data_b64" not in item and "poster" not in item for item in result.value["items"])


def test_maximum_video_keeps_every_transport_block_bounded(media_plugin, tm):
    result = add_video(media_plugin, mp4(_GALLERY_VIDEO_MAX_BYTES))
    assert isinstance(result, tm.Ok)
    gid = result.value["id"]
    metadata = run(media_plugin, "get_gallery_image", item_id=gid).value
    assert metadata["size"] == _GALLERY_VIDEO_MAX_BYTES
    assert metadata["chunk_count"] == 43
    chunks = [
        value for key, value in media_plugin.store.data.items() if key.startswith("gallery_video_chunk/")
    ]
    assert len(chunks) == 43 and max(len(chunk) for chunk in chunks) == 1_048_576
    assert sum(len(base64.b64decode(chunk)) for chunk in chunks) == _GALLERY_VIDEO_MAX_BYTES


@pytest.mark.parametrize("value", [True, False, 1.5, "96", None, [], 0, -1])
def test_video_size_rejects_nonintegers_and_nonpositive(media_plugin, tm, value):
    error(begin(media_plugin, mp4(), size=value), tm, "video_size_invalid")
    assert _STORE_GALLERY_UPLOADS not in media_plugin.store.data


def test_video_size_and_mime_bounds(media_plugin, tm):
    error(begin(media_plugin, mp4(), size=_GALLERY_VIDEO_MAX_BYTES + 1), tm, "video_too_large")
    for mime in ("video/quicktime", "text/html", None, []):
        error(begin(media_plugin, mp4(), mime=mime), tm, "video_type_unsupported")
    error(run(media_plugin, "gallery_add", op={"not": "an operation"}), tm, "video_operation_invalid")


@pytest.mark.parametrize("value", [
    "", None, "file:///private/image.png", "data:image/svg+xml;base64,PHN2Zy8+",
    "data:image/png;base64,notbase64!", "data:image/png;base64,aGVsbG8=",
    "data:image/png;base64," + "A" * 1_000_001,
], ids=["empty", "null", "path", "svg", "invalid-base64", "wrong-magic", "oversized"])
def test_video_poster_is_required_and_is_safe_bitmap_data(media_plugin, tm, value):
    error(begin(media_plugin, mp4(), poster=value), tm, "video_poster_invalid")
    error(begin(media_plugin, mp4(), thumb=value), tm, "video_poster_invalid")
    assert _STORE_GALLERY_UPLOADS not in media_plugin.store.data


def test_chunk_order_lengths_base64_and_idempotent_retry(media_plugin, tm):
    payload = mp4(_GALLERY_VIDEO_CHUNK_BYTES + 17)
    upload_id = begin(media_plugin, payload).value["upload_id"]
    first = encoded(payload[:_GALLERY_VIDEO_CHUNK_BYTES])
    tail = encoded(payload[_GALLERY_VIDEO_CHUNK_BYTES:])
    error(run(media_plugin, "gallery_add", op="video_chunk", upload_id=upload_id,
              chunk_index=1, data_b64=tail), tm, "video_chunk_out_of_order")
    for number in (-1, 2, "0", 0.0, True):
        error(run(media_plugin, "gallery_add", op="video_chunk", upload_id=upload_id,
                  chunk_index=number, data_b64=first), tm, "video_chunk_invalid")
    for bad in (None, first[:-4], first + "AAAA", "!" + first[1:]):
        error(run(media_plugin, "gallery_add", op="video_chunk", upload_id=upload_id,
                  chunk_index=0, data_b64=bad), tm, "video_chunk_invalid")
    accepted = run(media_plugin, "gallery_add", op="video_chunk", upload_id=upload_id,
                   chunk_index=0, data_b64=first)
    repeated = run(media_plugin, "gallery_add", op="video_chunk", upload_id=upload_id,
                   chunk_index=0, data_b64=first)
    assert accepted.value == repeated.value
    different = encoded(payload[:_GALLERY_VIDEO_CHUNK_BYTES - 1] + b"y")
    error(run(media_plugin, "gallery_add", op="video_chunk", upload_id=upload_id,
              chunk_index=0, data_b64=different), tm, "video_chunk_invalid")
    error(run(media_plugin, "gallery_add", op="video_chunk", upload_id=upload_id,
              chunk_index=1, data_b64=tail[:-4]), tm, "video_chunk_invalid")
    error(run(media_plugin, "gallery_add", op="video_commit", upload_id=upload_id),
          tm, "video_upload_incomplete")
    assert not media_plugin.store.data.get("gallery_index", {}).get("items")


def test_signature_validation_and_webm_minimum_header(media_plugin, tm):
    bad = b"not-a-video" * 9
    upload_id = begin(media_plugin, bad).value["upload_id"]
    error(run(media_plugin, "gallery_add", op="video_chunk", upload_id=upload_id,
              chunk_index=0, data_b64=encoded(bad)), tm, "video_signature_invalid")
    run(media_plugin, "gallery_add", op="video_abort", upload_id=upload_id)
    webm = b"\x1a\x45\xdf\xa3\x87\x42\x82\x84webm" + b"\x00" * 100
    result = add_video(media_plugin, webm, mime="video/webm", name="wallpaper.webm")
    assert isinstance(result, tm.Ok) and result.value["items"][0]["mime"] == "video/webm"


def test_abort_cleans_chunks_without_changing_existing_wallpaper(media_plugin, tm):
    old = run(media_plugin, "gallery_add", data_url=IMAGE).value["id"]
    run(media_plugin, "set_panel_appearance", bg_id=old, motion=False)
    payload = mp4()
    upload_id = begin(media_plugin, payload).value["upload_id"]
    send_chunks(media_plugin, upload_id, payload)
    assert isinstance(run(media_plugin, "gallery_add", op="video_abort", upload_id=upload_id), tm.Ok)
    assert video_chunk_key(upload_id, 0) not in media_plugin.store.data
    assert run(media_plugin, "get_panel_gallery").value["appearance"]["bg_id"] == old
    assert run(media_plugin, "get_panel_gallery").value["appearance"]["motion"] is False
    assert run(media_plugin, "gallery_add", op="video_abort", upload_id=upload_id).value == {"aborted": False}


def test_pending_count_capacity_and_video_budget(media_plugin, tm):
    begin(media_plugin, mp4())
    begin(media_plugin, mp4())
    error(begin(media_plugin, mp4()), tm, "video_upload_limit")
    for upload_id in list(media_plugin.store.data[_STORE_GALLERY_UPLOADS]["uploads"]):
        run(media_plugin, "gallery_add", op="video_abort", upload_id=upload_id)
    media_plugin.store.data["gallery_index"] = {
        "items": [
            {"id": f"g{number}", "kind": "video", "mime": "video/mp4", "size": _GALLERY_VIDEO_MAX_BYTES}
            for number in range(4)
        ],
        "next": 5,
    }
    error(begin(media_plugin, mp4()), tm, "video_storage_full")
    media_plugin.store.data["gallery_index"] = {
        "items": [{"id": f"g{number}"} for number in range(_GALLERY_MAX_ITEMS)], "next": 25,
    }
    error(begin(media_plugin, mp4()), tm, "gallery_full")


def test_not_ready_store_explicitly_rejects_video(media_plugin, tm):
    media_plugin.store.enabled = False
    error(begin(media_plugin, mp4()), tm, "video_store_not_ready")
    assert _STORE_GALLERY_UPLOADS not in media_plugin.store.data


def test_failed_chunk_registration_rolls_back_and_retry_works(media_plugin, tm):
    payload = mp4()
    upload_id = begin(media_plugin, payload).value["upload_id"]
    before = copy.deepcopy(media_plugin.store.data[_STORE_GALLERY_UPLOADS])
    media_plugin.store.fail_set.add(_STORE_GALLERY_UPLOADS)
    error(run(media_plugin, "gallery_add", op="video_chunk", upload_id=upload_id,
              chunk_index=0, data_b64=encoded(payload)), tm, "video_save_failed")
    assert media_plugin.store.data[_STORE_GALLERY_UPLOADS] == before
    assert video_chunk_key(upload_id, 0) not in media_plugin.store.data
    media_plugin.store.fail_set.clear()
    send_chunks(media_plugin, upload_id, payload)
    assert isinstance(run(media_plugin, "gallery_add", op="video_commit", upload_id=upload_id), tm.Ok)


def test_failed_chunk_rollback_is_reclaimable_after_restart(media_plugin, plugin_factory_full, tm):
    payload = mp4()
    upload_id = begin(media_plugin, payload).value["upload_id"]
    media_plugin.store.fail_set.add(_STORE_GALLERY_UPLOADS)
    media_plugin.store.fail_delete.add(video_chunk_key(upload_id, 0))
    error(run(media_plugin, "gallery_add", op="video_chunk", upload_id=upload_id,
              chunk_index=0, data_b64=encoded(payload)), tm, "video_save_failed")
    assert video_chunk_key(upload_id, 0) in media_plugin.store.data
    restarted = plugin_factory_full(store_initial=copy.deepcopy(media_plugin.store.data))
    assert isinstance(run(restarted, "get_panel_gallery"), tm.Ok)
    assert video_chunk_key(upload_id, 0) not in restarted.store.data
    assert not restarted.store.data[_STORE_GALLERY_UPLOADS]["uploads"]


def test_commit_publication_failure_keeps_old_wallpaper_and_retry_safe(media_plugin, tm):
    old_id = run(media_plugin, "gallery_add", data_url=IMAGE).value["id"]
    run(media_plugin, "set_panel_appearance", bg_id=old_id)
    payload = mp4()
    upload_id = begin(media_plugin, payload).value["upload_id"]
    send_chunks(media_plugin, upload_id, payload)
    original_set = media_plugin.store.set

    async def fail_publication(key, value):
        if key == "gallery_index" and any(item.get("kind") == "video" for item in value["items"]):
            return tm.Err(tm.SdkError("test_publication_failure"))
        return await original_set(key, value)

    media_plugin.store.set = fail_publication
    error(run(media_plugin, "gallery_add", op="video_commit", upload_id=upload_id), tm, "video_save_failed")
    assert [item["id"] for item in media_plugin.store.data["gallery_index"]["items"]] == [old_id]
    assert media_plugin.store.data["panel_appearance"]["bg_id"] == old_id
    reserved = media_plugin.store.data[_STORE_GALLERY_UPLOADS]["uploads"][upload_id]["item_id"]
    extra = run(media_plugin, "gallery_add", data_url=IMAGE).value["id"]
    assert extra != reserved
    media_plugin.store.set = original_set
    committed = run(media_plugin, "gallery_add", op="video_commit", upload_id=upload_id)
    assert committed.value["id"] == reserved
    assert {item["id"] for item in committed.value["items"]} == {old_id, extra, reserved}


def test_published_commit_survives_registry_cleanup_failure(media_plugin, plugin_factory_full, tm):
    payload = mp4()
    upload_id = begin(media_plugin, payload).value["upload_id"]
    send_chunks(media_plugin, upload_id, payload)
    original_set = media_plugin.store.set

    async def fail_registry_finalization(key, value):
        if key == _STORE_GALLERY_UPLOADS and not value["uploads"]:
            return tm.Err(tm.SdkError("test_cleanup_failure"))
        return await original_set(key, value)

    media_plugin.store.set = fail_registry_finalization
    committed = run(media_plugin, "gallery_add", op="video_commit", upload_id=upload_id)
    assert isinstance(committed, tm.Ok)
    restarted = plugin_factory_full(store_initial=copy.deepcopy(media_plugin.store.data))
    assert len(run(restarted, "get_panel_gallery").value["items"]) == 1
    block = run(restarted, "get_gallery_image", item_id=committed.value["id"], chunk_index=0)
    assert base64.b64decode(block.value["data_b64"]) == payload
    assert not restarted.store.data[_STORE_GALLERY_UPLOADS]["uploads"]


def test_stale_uploads_reclaimed_on_reopen(media_plugin, plugin_factory_full, tm):
    payload = mp4()
    upload_id = begin(media_plugin, payload).value["upload_id"]
    send_chunks(media_plugin, upload_id, payload)
    restarted = plugin_factory_full(store_initial=copy.deepcopy(media_plugin.store.data))
    assert run(restarted, "get_panel_gallery").value["items"] == []
    assert video_chunk_key(upload_id, 0) not in restarted.store.data
    error(run(restarted, "gallery_add", op="video_commit", upload_id=upload_id), tm, "video_upload_not_found")


def test_expiration_is_bounded_and_reports_expired(media_plugin, tm):
    upload_id = begin(media_plugin, mp4()).value["upload_id"]
    media_plugin.store.data[_STORE_GALLERY_UPLOADS]["uploads"][upload_id]["expires_at"] = 0
    error(run(media_plugin, "gallery_add", op="video_commit", upload_id=upload_id), tm, "video_upload_expired")
    assert not media_plugin.store.data[_STORE_GALLERY_UPLOADS]["uploads"]


def test_read_errors_do_not_overwrite_gallery_or_publish(media_plugin, tm):
    old_id = run(media_plugin, "gallery_add", data_url=IMAGE).value["id"]
    before = copy.deepcopy(media_plugin.store.data)
    media_plugin.store.fail_get.add("gallery_index")
    error(begin(media_plugin, mp4()), tm, "gallery_update_failed")
    error(run(media_plugin, "gallery_add", data_url=IMAGE), tm, "gallery_update_failed")
    error(run(media_plugin, "gallery_remove", item_id=old_id), tm, "gallery_update_failed")
    error(run(media_plugin, "set_panel_appearance", bg_id=old_id), tm, "gallery_update_failed")
    assert media_plugin.store.data == before


def test_chunk_integrity_is_checked_at_commit_and_read(media_plugin, tm):
    payload = mp4()
    upload_id = begin(media_plugin, payload).value["upload_id"]
    send_chunks(media_plugin, upload_id, payload)
    media_plugin.store.data[video_chunk_key(upload_id, 0)] = encoded(payload[:-1] + b"y")
    error(run(media_plugin, "gallery_add", op="video_commit", upload_id=upload_id), tm, "video_chunk_invalid")
    assert not media_plugin.store.data.get("gallery_index", {}).get("items")
    media_plugin.store.data[video_chunk_key(upload_id, 0)] = encoded(payload)
    gid = run(media_plugin, "gallery_add", op="video_commit", upload_id=upload_id).value["id"]
    media_plugin.store.data[video_chunk_key(upload_id, 0)] = encoded(payload[:-1] + b"y")
    error(run(media_plugin, "get_gallery_image", item_id=gid, chunk_index=0), tm, "video_read_failed")
    for value in (True, "0", -2, 1):
        error(run(media_plugin, "get_gallery_image", item_id=gid, chunk_index=value), tm, "video_chunk_invalid")


def test_video_delete_index_failure_does_not_destroy_published_assets(media_plugin, tm):
    gid = add_video(media_plugin).value["id"]
    run(media_plugin, "set_panel_appearance", bg_id=gid)
    before = copy.deepcopy(media_plugin.store.data[video_manifest_key(gid)])
    media_plugin.store.fail_set.add("gallery_index")
    error(run(media_plugin, "gallery_remove", item_id=gid), tm, "gallery_update_failed")
    assert media_plugin.store.data[video_manifest_key(gid)] == before
    assert run(media_plugin, "get_gallery_image", item_id=gid, chunk_index=0).value["data_b64"] == encoded(mp4())
    media_plugin.store.fail_set.clear()
    removed = run(media_plugin, "gallery_remove", item_id=gid)
    assert removed.value["items"] == [] and removed.value["appearance"]["bg_id"] == ""
    assert video_manifest_key(gid) not in media_plugin.store.data


def test_failed_delete_keeps_reclaimable_tombstone(media_plugin, plugin_factory_full, tm):
    gid = add_video(media_plugin).value["id"]
    run(media_plugin, "set_panel_appearance", bg_id=gid)
    storage_id = media_plugin.store.data[video_manifest_key(gid)]["storage_id"]
    media_plugin.store.fail_delete.add(video_chunk_key(storage_id, 0))
    error(run(media_plugin, "gallery_remove", item_id=gid), tm, "video_save_failed")
    assert media_plugin.store.data["gallery_index"]["items"] == []
    assert storage_id in media_plugin.store.data[_STORE_GALLERY_UPLOADS]["uploads"]
    restarted = plugin_factory_full(store_initial=copy.deepcopy(media_plugin.store.data))
    recovered = run(restarted, "get_panel_gallery").value
    assert recovered["items"] == [] and recovered["appearance"]["bg_id"] == ""
    assert video_chunk_key(storage_id, 0) not in restarted.store.data
    assert not restarted.store.data[_STORE_GALLERY_UPLOADS]["uploads"]


def test_gallery_writes_are_serialized_across_tasks_and_loops(media_plugin, tm):
    media_plugin.store.delay = True

    async def concurrent_tasks():
        results = await asyncio.gather(*(media_plugin.gallery_add(data_url=IMAGE) for _ in range(5)))
        assert all(isinstance(result, tm.Ok) for result in results)

    asyncio.run(asyncio.wait_for(concurrent_tasks(), timeout=5))
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(run, media_plugin, "gallery_add", data_url=IMAGE) for _ in range(3)]
        assert all(isinstance(future.result(timeout=5), tm.Ok) for future in futures)
    items = run(media_plugin, "get_panel_gallery").value["items"]
    assert len(items) == 8 and len({item["id"] for item in items}) == 8
    assert media_plugin._gallery_busy is False


def test_gallery_guard_releases_on_exception_and_cancellation(media_plugin, tm):
    async def exercise():
        async def fail():
            raise RuntimeError("test_failure")

        with pytest.raises(RuntimeError):
            await media_plugin._gallery_call(fail)
        assert not media_plugin._gallery_busy

        started = asyncio.Event()

        async def hang():
            started.set()
            await asyncio.sleep(60)

        task = asyncio.create_task(media_plugin._gallery_call(hang))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not media_plugin._gallery_busy
        assert isinstance(await media_plugin.gallery_add(data_url=IMAGE), tm.Ok)

    asyncio.run(exercise())


def test_media_error_codes_are_stable_and_translated_in_all_locales():
    root = Path(__file__).resolve().parents[1]
    codes = set()
    for path in (root / "core" / "media.py", root / "mixins" / "media.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "GalleryMediaError":
                if node.args and isinstance(node.args[0], ast.Constant):
                    codes.add(node.args[0].value)
    assert codes
    for code in codes:
        assert isinstance(code, str) and re.fullmatch(r"[a-z][a-z0-9_]*", code)
        key = "panel.errors." + re.sub(r"_([a-z0-9])", lambda match: match[1].upper(), code)
        for locale in ("en", "zh-CN", "zh-TW", "ja", "ko", "ru", "es", "pt"):
            bundle = json.loads((root / "i18n" / f"{locale}.json").read_text(encoding="utf-8"))
            assert key in bundle, f"{key} missing in {locale}"
