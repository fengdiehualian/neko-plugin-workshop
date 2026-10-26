import hashlib
import io
import json
import os
import zipfile
from pathlib import Path

import pytest
from sticker_manager.services import bundled_pack


def _split(tmp_path, data=None, chunk_bytes=31):
    if data is None:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("manifest.json", b'{"version":3}')
            archive.writestr("stickers/a.gif", b"GIF89a" * 18)
        data = buffer.getvalue()
    path = tmp_path / "official_pack.zip"
    rows = []
    for index, offset in enumerate(range(0, len(data), chunk_bytes), 1):
        chunk = data[offset:offset + chunk_bytes]
        name = f"{path.name}.part{index:03d}"
        (tmp_path / name).write_bytes(chunk)
        rows.append({"name": name, "size": len(chunk), "sha256": hashlib.sha256(chunk).hexdigest()})
    manifest_path = Path(str(path) + ".parts.json")
    manifest = {"schema_version": 1, "size": len(data), "sha256": hashlib.sha256(data).hexdigest(), "parts": rows}
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return path, manifest_path, manifest, data


def test_split_zip_reads_members_and_closes_all_handles(tmp_path):
    path, _, _, _ = _split(tmp_path)
    assert bundled_pack.pack_exists(path)
    with bundled_pack.open_pack(path) as archive:
        assert archive.read("manifest.json") == b'{"version":3}'
        assert archive.read("stickers/a.gif") == b"GIF89a" * 18
        assert archive.testzip() is None
        files = list(archive._owned_stream._files)
    assert all(file.closed for file in files)


def test_legacy_zip_and_missing_pack(tmp_path):
    path = tmp_path / "old.zip"
    assert not bundled_pack.pack_exists(path)
    with pytest.raises(ValueError):
        bundled_pack.open_pack(path)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("a", "legacy")
    assert bundled_pack.pack_exists(path)
    with bundled_pack.open_pack(path) as archive:
        assert archive.read("a") == b"legacy"


def test_cross_part_reads_seeks_and_eof(tmp_path):
    data = b"abcdefghijklmno"
    path, _, manifest, _ = _split(tmp_path, data, chunk_bytes=4)
    with bundled_pack.SplitArchive(
        [path.parent / row["name"] for row in manifest["parts"]], [row["size"] for row in manifest["parts"]],
    ) as stream:
        assert stream.read(5) == data[:5]
        assert stream.tell() == 5
        assert stream.seek(-2, os.SEEK_CUR) == 3
        assert stream.read(6) == data[3:9]
        assert stream.seek(-4, os.SEEK_END) == 11
        buffer = bytearray(10)
        assert stream.readinto(buffer) == 4
        assert buffer[:4] == data[-4:]
        assert stream.read() == b""
        assert stream.seek(100) == 100
        assert stream.read() == b""
        with pytest.raises(OSError):
            stream.seek(-1)
        with pytest.raises(ValueError):
            stream.seek(0, 99)
    with pytest.raises(ValueError):
        stream.read()


@pytest.mark.parametrize("change", [
    lambda m: m.update(schema_version=True),
    lambda m: m.update(size=0),
    lambda m: m.update(size=True),
    lambda m: m.update(size=m["size"] + 1),
    lambda m: m.update(sha256="0" * 64),
    lambda m: m.update(parts=[]),
    lambda m: m["parts"][0].update(name="../private"),
    lambda m: m["parts"][0].update(name="C:\\private"),
    lambda m: m["parts"][0].update(size=64 * 1024 * 1024 + 1),
    lambda m: m["parts"][0].update(sha256="not-a-digest"),
    lambda m: m["parts"].reverse(),
])
def test_invalid_manifests_fail_closed(tmp_path, change):
    path, manifest_path, manifest, _ = _split(tmp_path)
    change(manifest)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError):
        bundled_pack.open_pack(path)


@pytest.mark.parametrize("action", ["missing", "short", "corrupt"])
def test_missing_truncated_or_corrupt_parts_are_rejected_after_cached_open(tmp_path, action):
    path, _, manifest, _ = _split(tmp_path)
    with bundled_pack.open_pack(path):
        pass
    part = path.parent / manifest["parts"][0]["name"]
    original = part.read_bytes()
    if action == "missing":
        part.unlink()
    elif action == "short":
        part.write_bytes(original[:-1])
    else:
        part.write_bytes(b"X" * len(original))
    with pytest.raises(ValueError):
        bundled_pack.open_pack(path)


def test_manifest_change_invalidates_hash_cache(tmp_path):
    path, manifest_path, manifest, _ = _split(tmp_path)
    with bundled_pack.open_pack(path):
        pass
    manifest["parts"][0]["sha256"] = "0" * 64
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="digest"):
        bundled_pack.open_pack(path)


def test_manifest_limit_and_invalid_zip(tmp_path):
    path, manifest_path, _, _ = _split(tmp_path)
    manifest_path.write_bytes(b" " * (bundled_pack.MAX_MANIFEST_BYTES + 1))
    with pytest.raises(ValueError, match="too_large"):
        bundled_pack.open_pack(path)
    other = tmp_path / "other"
    other.mkdir()
    path, _, _, _ = _split(other, b"not a zip")
    with pytest.raises(zipfile.BadZipFile):
        bundled_pack.open_pack(path)


def test_symbolic_link_part_is_rejected(tmp_path):
    path, _, manifest, _ = _split(tmp_path)
    part = path.parent / manifest["parts"][0]["name"]
    private = tmp_path / "private"
    part.rename(private)
    try:
        part.symlink_to(private)
    except OSError:
        pytest.skip("symlink creation is not available")
    with pytest.raises(ValueError):
        bundled_pack.open_pack(path)
