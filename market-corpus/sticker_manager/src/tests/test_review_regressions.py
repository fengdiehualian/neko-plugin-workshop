"""Regression coverage for the four library/send review findings."""

from __future__ import annotations

import json
import threading
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from conftest import PNG_BYTES, FakeHostContext, build_plugin
from sticker_manager.core.configuration import SendSettings, StickerManagerSettings
from sticker_manager.core.pack import PACK_MAX_ENTRIES, parse_manifest
from sticker_manager.services.library import Library


def _library(root: Path) -> Library:
    library = Library(root)
    assert library.load().ok
    return library


def _pack(path: Path, *, count: int = 1, group: str = "") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    entries = [
        {"file": f"image-{index}.png", "desc": f"image {index}", "group": group}
        for index in range(count)
    ]
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("manifest.json", json.dumps({"stickers": entries}))
        for index, entry in enumerate(entries):
            archive.writestr("stickers/" + entry["file"], PNG_BYTES + str(index).encode())
    return path


def _overlap(first_call, second_call, entered, release):
    attempted = threading.Event()
    finished = threading.Event()

    def second():
        attempted.set()
        try:
            return second_call()
        finally:
            finished.set()

    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="review") as executor:
        first = executor.submit(first_call)
        try:
            assert entered.wait(5.0)
            other = executor.submit(second)
            assert attempted.wait(5.0)
            # A broken implementation finishes B before A's old snapshot is published.
            finished.wait(0.1)
        finally:
            release.set()
        return first.result(timeout=5.0), other.result(timeout=5.0)


def test_concurrent_additions_preserve_both_catalog_entries(tmp_path, monkeypatch):
    library = _library(tmp_path)
    entered, release = threading.Event(), threading.Event()
    write_text = Path.write_text

    def delayed_write(path, text, *args, **kwargs):
        if path.name == "catalog.json.tmp" and threading.current_thread().name.endswith("_0"):
            entered.set()
            assert release.wait(5.0)
        return write_text(path, text, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", delayed_write)
    first, second = _overlap(
        lambda: library.add(data=PNG_BYTES + b"A", desc="A", tags=[]),
        lambda: library.add(data=PNG_BYTES + b"B", desc="B", tags=[]),
        entered, release,
    )
    assert not first[1] and not second[1]
    assert library.count() == 2
    assert {row.desc for row in _library(tmp_path).all()} == {"A", "B"}


def test_failed_update_cannot_roll_back_another_threads_success(tmp_path, monkeypatch):
    library = _library(tmp_path)
    sticker, error = library.add(data=PNG_BYTES, desc="original", tags=[])
    assert not error
    entered, release = threading.Event(), threading.Event()
    replace = Path.replace

    def failed_replace(path, target):
        if path.name == "catalog.json.tmp" and threading.current_thread().name.endswith("_0"):
            entered.set()
            assert release.wait(5.0)
            raise PermissionError("simulated write failure")
        return replace(path, target)

    monkeypatch.setattr(Path, "replace", failed_replace)
    first, second = _overlap(
        lambda: library.update(sticker.id, desc="failed"),
        lambda: library.update(sticker.id, desc="successful"),
        entered, release,
    )
    assert first[1] == "library_io_error"
    assert not second[1]
    assert library.get(sticker.id).desc == "successful"
    assert _library(tmp_path).get(sticker.id).desc == "successful"


def test_concurrent_usage_appends_preserve_both_records(tmp_path, monkeypatch):
    library = _library(tmp_path)
    entered, release = threading.Event(), threading.Event()
    write_text = Path.write_text

    def delayed_write(path, text, *args, **kwargs):
        if path.name == "usage.json.tmp" and threading.current_thread().name.endswith("_0"):
            entered.set()
            assert release.wait(5.0)
        return write_text(path, text, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", delayed_write)
    _overlap(
        lambda: library.append_usage({"id": "A", "ok": True}, keep=200),
        lambda: library.append_usage({"id": "B", "ok": True}, keep=200),
        entered, release,
    )
    assert {row["id"] for row in library.read_usage()} == {"A", "B"}


def test_manifest_can_be_read_without_truncation_for_import_accounting():
    manifest = {"stickers": [{"file": f"{index}.png"} for index in range(PACK_MAX_ENTRIES + 3)]}
    assert len(parse_manifest(manifest)) == PACK_MAX_ENTRIES
    assert len(parse_manifest(manifest, limit=None)) == PACK_MAX_ENTRIES + 3


def test_oversize_manifest_counts_every_rejected_image_and_keeps_inbox_source(tmp_path):
    library = _library(tmp_path / "library")
    source = _pack(library.inbox_dir / "large.zip", count=PACK_MAX_ENTRIES + 1)
    summary = library.ingest_inbox(tags=[])
    assert summary == {"imported": PACK_MAX_ENTRIES, "duplicates": 0, "rejected": 1, "failed": 0}
    assert library.count() == PACK_MAX_ENTRIES
    assert source.is_file()
    retried = library.ingest_inbox(tags=[])
    assert retried == {"imported": 0, "duplicates": PACK_MAX_ENTRIES, "rejected": 1, "failed": 0}
    assert source.is_file()


def test_export_refuses_to_create_an_unimportable_oversize_pack(tmp_path):
    library = _library(tmp_path / "library")
    for index in range(PACK_MAX_ENTRIES + 1):
        sticker, error = library.add(data=PNG_BYTES + str(index).encode(), desc=str(index), tags=[])
        assert sticker is not None and not error
    result, error = library.export_pack()
    assert result == {} and error == "pack_too_many_entries"
    assert not list(library.exports_dir.glob("*.zip"))
    assert library.count() == PACK_MAX_ENTRIES + 1


@pytest.mark.parametrize("implicit", [False, True])
def test_cross_zone_category_conflict_is_rejected_without_changing_either_zone(
    tmp_path, implicit,
):
    library = _library(tmp_path / "library")
    original_zone = library.active_zone()
    if implicit:
        assert not library.add(data=PNG_BYTES + b"original", desc="original", tags=[], group="happy")[1]
    else:
        assert library.create_group("happy", "original description") == (True, "")
    target_zone, error = library.create_zone("other")
    assert not error
    source = _pack(library.inbox_dir / "conflict.zip", group="happy")
    before = library.count()
    summary = library.ingest_inbox(tags=[], zone=target_zone)
    assert summary == {"imported": 0, "duplicates": 0, "rejected": 1, "failed": 0}
    assert library.count() == before
    assert next(zone["total"] for zone in library.zones() if zone["id"] == target_zone) == 0
    assert source.is_file()
    assert library.active_zone() == original_zone
    if not implicit:
        assert library.group_descs()["happy"] == "original description"
        assert library.zone_of_group("happy") == original_zone


def test_cross_zone_conflict_does_not_reject_content_already_in_library(tmp_path):
    library = _library(tmp_path / "library")
    assert library.create_group("happy") == (True, "")
    assert not library.add(data=PNG_BYTES + b"0", desc="original", tags=[], group="happy")[1]
    target_zone, error = library.create_zone("other")
    assert not error
    source = _pack(tmp_path / "duplicate.zip", group="happy")
    assert library.import_pack(source, zone=target_zone) == {
        "imported": 0, "duplicates": 1, "rejected": 0, "failed": 0,
    }
    assert library.count() == 1


def test_same_zone_category_import_still_succeeds(tmp_path):
    library = _library(tmp_path / "library")
    assert library.create_group("happy") == (True, "")
    summary = library.import_pack(_pack(tmp_path / "same.zip", group="happy"), zone=library.active_zone())
    assert summary == {"imported": 1, "duplicates": 0, "rejected": 0, "failed": 0}


def test_unknown_zone_import_does_not_silently_fall_back_to_active_zone(tmp_path):
    library = _library(tmp_path / "library")
    summary = library.import_pack(_pack(tmp_path / "unknown.zip"), zone="deleted-zone")
    assert summary["failed"] == 1 and summary["imported"] == 0
    assert library.count() == 0


@pytest.mark.parametrize("source", ["tool", "agent", "panel"])
@pytest.mark.parametrize("edit,code", [
    ("disable", "sticker_disabled"),
    ("delete", "sticker_file_missing"),
    ("delete_file", "sticker_file_missing"),
    ("switch_zone", "sticker_zone_changed"),
    ("move_zone", "sticker_zone_changed"),
])
def test_upload_revalidates_sticker_before_submitting_any_caption_or_image(
    tmp_path, run_async, source, edit, code,
):
    plugin, host = build_plugin(FakeHostContext(data_root=tmp_path))
    library = plugin._library
    assert library.load().ok
    settings = StickerManagerSettings(enabled=True, send=SendSettings(inline_max_bytes=32))
    sticker, error = library.add(data=PNG_BYTES, desc="original", tags=[])
    assert not error

    async def upload(*args, **kwargs):
        if edit == "disable":
            assert not library.update(sticker.id, disabled=True)[1]
        elif edit == "delete":
            assert not library.remove(sticker.id)
        elif edit == "delete_file":
            library.image_path(sticker).unlink()
        else:
            zone, error = library.create_zone("other")
            assert not error
            if edit == "switch_zone":
                assert library.activate_zone(zone) == (True, "")
            else:
                assert library.create_group("other group", zone=zone) == (True, "")
                assert not library.update(sticker.id, group="other group")[1]
        return {"type": "image", "url": "https://stub.local/image.jpg"}

    host.images.upload = upload
    result = run_async(plugin._sender.send(
        sticker, lanlan="K", settings=settings, source=source, text="extra caption",
    ))
    if source == "panel" and edit in {"switch_zone", "move_zone"}:
        assert result.ok and len(host.push.calls) == 2
    else:
        assert not result.ok and result.code == code
        assert not host.push.calls and not result.text_submitted
        assert not plugin._sender._last_sent and not library.read_usage()
    assert not plugin._sender._inflight


def test_upload_uses_latest_sticker_metadata_when_it_remains_sendable(tmp_path, run_async):
    plugin, host = build_plugin(FakeHostContext(data_root=tmp_path))
    library = plugin._library
    assert library.load().ok
    sticker, error = library.add(data=PNG_BYTES, desc="old", tags=[])
    assert not error
    settings = StickerManagerSettings(enabled=True, send=SendSettings(inline_max_bytes=32))

    async def upload(*args, **kwargs):
        assert not library.update(sticker.id, desc="new")[1]
        return {"type": "image", "url": "https://stub.local/image.jpg"}

    host.images.upload = upload
    result = run_async(plugin._sender.send(sticker, lanlan="K", settings=settings, source="tool"))
    assert result.ok and result.desc == "new"
    assert library.get(sticker.id).desc == "new"
    assert library.get(sticker.id).use_count == 1
