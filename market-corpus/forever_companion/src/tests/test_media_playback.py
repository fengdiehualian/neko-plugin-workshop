"""Direct playback uses only verified, bounded copies in isolated SDK roots."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest
from forever_companion.core import media_files, media_playback
from forever_companion.core.media import GalleryMediaError
from forever_companion.core.state import _GALLERY_VIDEO_CHUNK_BYTES, _STORE_GALLERY_INDEX
from tests.test_media_files import (
    add_video,
    begin,
    expect_error,
    file_bytes,
    file_manifest,
    media_root,
    run,
    synthetic_mp4,
)

pytest_plugins = ("tests.test_media_files",)


def enable(plugin):
    calls = []

    def register(directory, **options):
        calls.append((Path(directory), options))
        return True

    plugin.register_static_ui = register
    return calls


def direct(plugin, gid):
    return run(plugin, "get_gallery_image", item_id=gid, prefer_direct=True)


def test_direct_copy_reuses_bytes_across_panel_reopens_without_store_writes(file_plugin, tm):
    payload = synthetic_mp4(_GALLERY_VIDEO_CHUNK_BYTES + 29)
    gid = add_video(file_plugin, payload).value["id"]
    calls = enable(file_plugin)
    file_plugin.store.calls.clear()
    result = direct(file_plugin, gid)
    assert isinstance(result, tm.Ok)
    name = result.value["playback_path"].rsplit("/", 1)[1]
    directory, options = calls[0]
    assert directory == file_plugin.data_path("wallpaper_playback")
    assert options["cache_control"] == media_playback.CACHE_CONTROL
    assert directory != media_root(file_plugin)
    assert {path.name for path in directory.iterdir()} == {"index.html", name}
    assert (directory / name).read_bytes() == payload
    signature = (directory / name).stat()
    second = direct(file_plugin, gid).value
    assert second["playback_path"] == result.value["playback_path"]
    assert (directory / name).stat().st_mtime_ns == signature.st_mtime_ns
    assert len(calls) == 1
    assert not any(operation != "get" for operation, _key in file_plugin.store.calls)
    assert str(directory) not in str(second)
    assert file_manifest(file_plugin, gid)["storage_id"] not in name
    summary = run(file_plugin, "get_panel_gallery").value["media_storage"]
    assert summary["playback_cache_bytes"] == len(payload)
    assert summary["occupied_bytes"] == len(payload) * 2


def test_direct_is_opt_in_and_missing_sdk_keeps_blob_protocol(file_plugin):
    gid = add_video(file_plugin).value["id"]
    assert "playback_path" not in direct(file_plugin, gid).value
    calls = enable(file_plugin)
    for preference in (False, None, "true", 1):
        result = run(file_plugin, "get_gallery_image", item_id=gid, prefer_direct=preference)
        assert "playback_path" not in result.value
    assert calls == []


def test_gallery_bootstrap_includes_selected_video_and_scans_storage_once(file_plugin, monkeypatch):
    gid = add_video(file_plugin).value["id"]
    run(file_plugin, "set_panel_appearance", bg_id=gid, motion=True)
    calls = enable(file_plugin)
    original = file_plugin._media_storage_summary
    scans = []

    async def summary(index=None):
        scans.append(index)
        return await original(index)

    monkeypatch.setattr(file_plugin, "_media_storage_summary", summary)
    file_plugin.store.calls.clear()
    result = run(file_plugin, "get_panel_gallery", include_background=True, prefer_direct=True).value
    assert result["background"]["id"] == gid
    media = result["background"]["media"]
    assert media["kind"] == "video" and media["playback_path"].endswith(".mp4")
    assert len(calls) == 1
    assert len(scans) == 1
    assert sum(key == _STORE_GALLERY_INDEX for operation, key in file_plugin.store.calls if operation == "get") == 2
    # The cleanup and snapshot share one guard; no third index/summary read for playback.
    assert result["appearance"]["bg_id"] == gid
    assert "background" not in run(file_plugin, "get_panel_gallery").value


@pytest.mark.parametrize("motion,preference", [(False, True), (True, False), (True, "true")])
def test_gallery_bootstrap_respects_static_and_reduced_motion(file_plugin, motion, preference):
    gid = add_video(file_plugin).value["id"]
    run(file_plugin, "set_panel_appearance", bg_id=gid, motion=motion)
    calls = enable(file_plugin)
    result = run(
        file_plugin, "get_panel_gallery", include_background=True, prefer_direct=preference,
    ).value
    assert result["background"]["media"]["kind"] == "video"
    assert "playback_path" not in result["background"]["media"]
    assert calls == []


def test_selected_video_bootstrap_failure_does_not_block_gallery(file_plugin, tm, monkeypatch):
    gid = add_video(file_plugin).value["id"]
    run(file_plugin, "set_panel_appearance", bg_id=gid)
    async def unavailable(*_args, **_kwargs):
        raise GalleryMediaError("video_read_failed")

    monkeypatch.setattr(file_plugin, "_video_get", unavailable)
    result = run(file_plugin, "get_panel_gallery", include_background=True)
    assert isinstance(result, tm.Ok)
    assert result.value["appearance"]["bg_id"] == gid
    assert result.value["items"]
    assert "background" not in result.value


def test_gallery_bootstrap_returns_selected_image_and_keeps_body_out_by_default(file_plugin):
    image = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUg=="
    gid = run(file_plugin, "gallery_add", data_url=image, name="test.png").value["id"]
    run(file_plugin, "set_panel_appearance", bg_id=gid)
    result = run(file_plugin, "get_panel_gallery", include_background=True).value
    assert result["background"]["id"] == gid
    assert result["background"]["media"]["data_url"] == image
    assert "background" not in run(file_plugin, "get_panel_gallery").value


def test_registration_failure_falls_back_and_retries_on_next_open(file_plugin):
    gid = add_video(file_plugin).value["id"]
    file_plugin.register_static_ui = lambda *_args, **_kwargs: False
    assert "playback_path" not in direct(file_plugin, gid).value
    enable(file_plugin)
    assert direct(file_plugin, gid).value["playback_path"]

def test_optional_sdk_exception_falls_back_without_breaking_gallery(file_plugin):
    gid = add_video(file_plugin).value["id"]

    def unavailable(*_args, **_kwargs):
        raise RuntimeError("synthetic SDK capability unavailable")

    file_plugin.register_static_ui = unavailable
    assert "playback_path" not in direct(file_plugin, gid).value
    assert run(file_plugin, "get_gallery_image", item_id=gid, chunk_index=0).value["data_b64"]


def test_copy_integrity_failure_never_publishes_corrupt_bytes(file_plugin, tm):
    payload = synthetic_mp4(_GALLERY_VIDEO_CHUNK_BYTES + 20)
    gid = add_video(file_plugin, payload).value["id"]
    enable(file_plugin)
    manifest = file_manifest(file_plugin, gid)
    source = media_root(file_plugin) / (manifest["storage_id"] + ".bin")
    source.write_bytes(payload[:-1] + b"!")
    assert "playback_path" not in direct(file_plugin, gid).value
    assert [path.name for path in media_playback.root(file_plugin).iterdir()] == ["index.html"]
    assert not (source.parent.parent / ".wallpaper-playback.part").exists()
    expect_error(run(file_plugin, "get_gallery_image", item_id=gid, chunk_index=1), tm, "video_read_failed")


def test_changed_original_and_changed_copy_are_revalidated(file_plugin):
    payload = synthetic_mp4()
    gid = add_video(file_plugin, payload).value["id"]
    enable(file_plugin)
    name = direct(file_plugin, gid).value["playback_path"].rsplit("/", 1)[1]
    target = media_playback.root(file_plugin) / name
    target.write_bytes(payload[:-1] + b"!")
    assert direct(file_plugin, gid).value["playback_path"]
    assert target.read_bytes() == payload
    source = media_root(file_plugin) / (file_manifest(file_plugin, gid)["storage_id"] + ".bin")
    source.write_bytes(payload[:-1] + b"?")
    assert "playback_path" not in direct(file_plugin, gid).value
    assert not target.exists()


def test_playback_eviction_is_bounded_and_never_evicts_originals(file_plugin, monkeypatch):
    ids = [add_video(file_plugin, synthetic_mp4(100 + number)).value["id"] for number in range(3)]
    enable(file_plugin)
    # Upload reservations reclaim playback copies, so populate after all imports.
    for gid in ids:
        assert direct(file_plugin, gid).value["playback_path"]
    assert len(list(media_playback.root(file_plugin).glob("*.mp4"))) == 2
    assert all(file_bytes(file_plugin, gid) for gid in ids)
    monkeypatch.setattr(media_playback, "_GALLERY_VIDEO_HARD_MAX_BYTES", 150)
    direct(file_plugin, ids[0])
    assert media_playback.usage(file_plugin) <= 150


def test_playback_eviction_prefers_least_recently_used_copy(file_plugin):
    ids = [add_video(file_plugin, synthetic_mp4(100 + number)).value["id"] for number in range(3)]
    enable(file_plugin)
    first = direct(file_plugin, ids[0]).value["playback_path"].rsplit("/", 1)[1]
    second = direct(file_plugin, ids[1]).value["playback_path"].rsplit("/", 1)[1]
    direct(file_plugin, ids[0])
    third = direct(file_plugin, ids[2]).value["playback_path"].rsplit("/", 1)[1]
    assert {path.name for path in media_playback.root(file_plugin).glob("*.mp4")} == {first, third}
    assert second not in file_plugin._playback_verified


def test_playback_eviction_skips_copy_still_open_in_windows(file_plugin, monkeypatch):
    ids = [add_video(file_plugin, synthetic_mp4(100 + number)).value["id"] for number in range(3)]
    enable(file_plugin)
    first = direct(file_plugin, ids[0]).value["playback_path"].rsplit("/", 1)[1]
    direct(file_plugin, ids[1])
    original = Path.unlink

    def locked(path, *args, **kwargs):
        if path.name == first:
            raise PermissionError("synthetic active Range reader")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", locked)
    result = direct(file_plugin, ids[2]).value
    assert result["playback_path"]
    third = result["playback_path"].rsplit("/", 1)[1]
    assert {path.name for path in media_playback.root(file_plugin).glob("*.mp4")} == {first, third}
    assert all(file_bytes(file_plugin, gid) for gid in ids)


def test_playback_eviction_with_all_copies_locked_preserves_bounds(file_plugin, monkeypatch):
    ids = [add_video(file_plugin, synthetic_mp4(100 + number)).value["id"] for number in range(3)]
    enable(file_plugin)
    names = {direct(file_plugin, gid).value["playback_path"].rsplit("/", 1)[1] for gid in ids[:2]}
    original = Path.unlink

    def locked(path, *args, **kwargs):
        if path.name in names:
            raise PermissionError("synthetic active Range reader")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", locked)
    assert "playback_path" not in direct(file_plugin, ids[2]).value
    assert {path.name for path in media_playback.root(file_plugin).glob("*.mp4")} == names
    assert all(file_bytes(file_plugin, gid) for gid in ids)


@pytest.mark.skipif(sys.platform != "win32", reason="requires Windows file sharing semantics")
def test_playback_eviction_with_real_windows_reader(file_plugin):
    ids = [add_video(file_plugin, synthetic_mp4(100 + number)).value["id"] for number in range(3)]
    enable(file_plugin)
    first = direct(file_plugin, ids[0]).value["playback_path"].rsplit("/", 1)[1]
    direct(file_plugin, ids[1])
    directory = media_playback.root(file_plugin)
    with (directory / first).open("rb") as reader:
        with pytest.raises(PermissionError):
            (directory / first).unlink()
        result = direct(file_plugin, ids[2]).value
        assert result["playback_path"]
        assert reader.read() == synthetic_mp4(100)
        assert len(list(directory.glob("*.mp4"))) == 2


def test_playback_disk_exhaustion_falls_back_and_preserves_original(file_plugin, monkeypatch):
    gid = add_video(file_plugin).value["id"]
    enable(file_plugin)
    monkeypatch.setattr(media_files, "disk_usage", lambda _root: (0, 0))
    assert "playback_path" not in direct(file_plugin, gid).value
    assert file_bytes(file_plugin, gid) == synthetic_mp4()

def test_playback_respects_pending_upload_reservations(file_plugin, monkeypatch):
    gid = add_video(file_plugin).value["id"]
    pending = synthetic_mp4(100)
    begin(file_plugin, pending)
    enable(file_plugin)
    monkeypatch.setattr(
        media_files, "disk_usage",
        lambda _root: (media_playback._GALLERY_VIDEO_RESERVE_BYTES + len(synthetic_mp4()) + 99, 1_000_000_000),
    )
    assert "playback_path" not in direct(file_plugin, gid).value
    assert media_playback.usage(file_plugin) == 0


def test_playback_cache_cannot_exceed_library_budget(file_plugin):
    gid = add_video(file_plugin, synthetic_mp4(33 * 1024 * 1024)).value["id"]
    run(file_plugin, "gallery_add", op="video_policy", single_limit_mib=64, total_limit_mib=64)
    enable(file_plugin)
    assert "playback_path" not in direct(file_plugin, gid).value
    assert media_playback.usage(file_plugin) == 0


@pytest.mark.parametrize("operation", ["delete", "clear", "shutdown", "upload"])
def test_media_lifecycle_reclaims_playback_copies(file_plugin, operation):
    gid = add_video(file_plugin).value["id"]
    enable(file_plugin)
    name = direct(file_plugin, gid).value["playback_path"].rsplit("/", 1)[1]
    target = media_playback.root(file_plugin) / name
    if operation == "delete":
        run(file_plugin, "gallery_remove", item_id=gid)
    elif operation == "clear":
        run(file_plugin, "gallery_remove", op="clear_media", confirm=True)
    elif operation == "shutdown":
        asyncio.run(file_plugin._media_shutdown())
    else:
        begin(file_plugin, synthetic_mp4())
    assert not target.exists()
    assert not file_plugin._playback_verified


def test_playback_root_reparse_is_rejected_without_touching_destination(file_plugin, tmp_path):
    gid = add_video(file_plugin).value["id"]
    enable(file_plugin)
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "keep.txt"
    sentinel.write_bytes(b"private")
    path = file_plugin.data_path("wallpaper_playback")
    try:
        path.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation unavailable")
    try:
        assert "playback_path" not in direct(file_plugin, gid).value
        assert sentinel.read_bytes() == b"private"
    finally:
        path.unlink()


def test_failed_clear_of_playback_is_reported_and_can_resume(file_plugin, tm, monkeypatch):
    gid = add_video(file_plugin).value["id"]
    enable(file_plugin)
    direct(file_plugin, gid)
    original = media_playback.clear

    def fail(_plugin):
        raise OSError("synthetic locked playback file")

    monkeypatch.setattr(media_playback, "clear", fail)
    expect_error(
        run(file_plugin, "gallery_remove", op="clear_media", confirm=True),
        tm, "video_cleanup_incomplete",
    )
    monkeypatch.setattr(media_playback, "clear", original)
    assert run(file_plugin, "gallery_remove", op="clear_media", confirm=True).value["cleared"]
