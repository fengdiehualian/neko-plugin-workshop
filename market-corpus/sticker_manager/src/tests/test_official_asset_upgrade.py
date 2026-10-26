# pyright: reportMissingImports=false
"""Official asset upgrades preserve owner data and remain retryable on failure."""
from __future__ import annotations

import json
import zipfile
from dataclasses import replace

from conftest import GIF_BYTES
from sticker_manager.core.catalog import content_sha256
from sticker_manager.core.labeling import index_by_digest
from sticker_manager.core.pack import PackEntry, pack_entry_from_raw
from sticker_manager.services.library import Library, LibraryResult


def _pack(path, data, *, version=1, legacy=None, digest=None):
    raw = {
        "pack_version": version,
        "groups": {"new group": "new group description"},
        "stickers": [{
            "file": "new.gif", "sha256": digest or content_sha256(data),
            "legacy_sha256": legacy or [], "desc": "new words", "caption": "new caption",
            "group": "new group", "tags": ["new tag"],
        }],
    }
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("manifest.json", json.dumps(raw))
        archive.writestr("stickers/new.gif", data)
    return path


def _seed(tmp_path):
    old = GIF_BYTES + b"old"
    lib = Library(tmp_path / "library")
    assert lib.load().ok
    pack = _pack(tmp_path / "old.zip", old)
    assert lib.seed_official(pack)["status"] == "seeded"
    return lib, lib.active_pool()[0], old


def _upgrade(tmp_path, old):
    new = GIF_BYTES + b"high quality frames"
    path = _pack(tmp_path / "new.zip", new, version=2, legacy=[content_sha256(old)])
    return path, new


def test_upgrade_preserves_identity_owner_fields_state_and_usage(tmp_path):
    lib, sticker, old = _seed(tmp_path)
    assert lib.update(sticker.id, desc="owner words", disabled=True)[0]
    lib._stickers[sticker.id] = replace(lib.get(sticker.id), use_count=7, last_used_at=123.0)
    assert lib.save().ok
    before = lib.get(sticker.id)
    old_path = lib.image_path(before)
    path, new = _upgrade(tmp_path, old)
    out = lib.refresh_official_labels(path)
    assert out["status"] == "refreshed" and out["images_updated"] == 1
    after = lib.get(sticker.id)
    assert after.id == before.id and after.zone == before.zone
    assert after.desc == before.desc and after.caption == before.caption
    assert after.owner_edited == before.owner_edited and after.disabled
    assert after.use_count == 7 and after.last_used_at == 123.0 and after.added_at == before.added_at
    assert after.sha256 == content_sha256(new) and lib.image_path(after).read_bytes() == new
    assert not old_path.exists()
    assert lib.refresh_official_labels(path) == {"status": "current", "version": 2}
    restored = Library(lib._root)
    assert restored.load().ok and restored.get(sticker.id) == after


def test_deleted_images_are_not_restored(tmp_path):
    lib, sticker, old = _seed(tmp_path)
    assert lib.remove(sticker.id) == ""
    path, _ = _upgrade(tmp_path, old)
    assert lib.refresh_official_labels(path)["images_updated"] == 0
    assert lib.count() == 0


def test_other_zone_images_are_untouched(tmp_path):
    lib, sticker, old = _seed(tmp_path)
    home = next(zone["id"] for zone in lib.zones() if not zone.get("builtin"))
    lib._stickers[sticker.id] = replace(sticker, zone=home, group="")
    assert lib.save().ok
    path, _ = _upgrade(tmp_path, old)
    out = lib.refresh_official_labels(path)
    assert out["images_updated"] == 0 and lib.get(sticker.id) == replace(sticker, zone=home, group="")
    assert lib.count() == 1



def test_force_restore_upgrades_before_import_so_images_do_not_duplicate(tmp_path):
    lib, sticker, old = _seed(tmp_path)
    path, new = _upgrade(tmp_path, old)
    out = lib.seed_official(path, force=True)
    assert out["status"] == "restored" and out["imported"] == 0 and out["duplicates"] == 1
    assert out["refresh"]["images_updated"] == 1 and lib.count() == 1
    assert lib.image_path(lib.get(sticker.id)).read_bytes() == new

def test_asset_corruption_does_not_stamp_version_or_change_library(tmp_path):
    lib, sticker, old = _seed(tmp_path)
    before_disk = lib.catalog_path.read_bytes()
    path = _pack(tmp_path / "bad.zip", GIF_BYTES + b"corrupt", version=2,
                 legacy=[content_sha256(old)], digest=content_sha256(b"different"))
    assert lib.refresh_official_labels(path)["status"] == "invalid_assets"
    assert lib.get(sticker.id) == sticker and lib.image_path(sticker).read_bytes() == old
    assert lib.official_pack_version() == 1 and lib.catalog_path.read_bytes() == before_disk


def test_catalog_failure_rolls_back_assets_labels_and_groups_then_retries(tmp_path, monkeypatch):
    lib, sticker, old = _seed(tmp_path)
    before_disk = lib.catalog_path.read_bytes()
    groups = lib.group_descs()
    group_zone = dict(lib._group_zone)
    path, new = _upgrade(tmp_path, old)
    real_save = lib.save
    monkeypatch.setattr(lib, "save", lambda: LibraryResult.failure("failed"))
    assert lib.refresh_official_labels(path)["status"] == "io"
    assert lib.get(sticker.id) == sticker and lib.group_descs() == groups and lib._group_zone == group_zone
    assert lib.official_pack_version() == 1 and lib.catalog_path.read_bytes() == before_disk
    assert lib.image_path(sticker).read_bytes() == old
    assert set(lib.stickers_dir.iterdir()) == {lib.image_path(sticker)}
    monkeypatch.setattr(lib, "save", real_save)
    assert lib.refresh_official_labels(path)["images_updated"] == 1
    assert lib.image_path(lib.get(sticker.id)).read_bytes() == new


def test_image_write_failure_rolls_back_and_remains_retryable(tmp_path, monkeypatch):
    lib, sticker, old = _seed(tmp_path)
    path, _ = _upgrade(tmp_path, old)
    real_replace = type(path).replace

    def failing_replace(source, destination):
        if source.suffix == ".tmp" and source.parent == lib.stickers_dir:
            raise OSError("image publish failed")
        return real_replace(source, destination)

    monkeypatch.setattr(type(path), "replace", failing_replace)
    assert lib.refresh_official_labels(path)["status"] == "io"
    assert lib.get(sticker.id) == sticker and lib.official_pack_version() == 1
    assert set(lib.stickers_dir.iterdir()) == {lib.image_path(sticker)}


def test_legacy_fingerprint_parser_and_conflicts():
    """Ambiguous aliases are never used for pairing."""
    digest = "a" * 64
    entry = pack_entry_from_raw({"file": "a.gif", "legacy_sha256": [digest, digest.upper(), "bad", 1]})
    assert entry.legacy_sha256 == [digest]
    first = PackEntry(file="a.gif", desc="a", sha256="b" * 64, legacy_sha256=[digest])
    second = PackEntry(file="b.gif", desc="b", sha256="c" * 64, legacy_sha256=[digest])
    assert digest not in index_by_digest([first, second])
    direct = PackEntry(file="c.gif", desc="c", sha256=digest)
    assert index_by_digest([first, direct])[digest] is direct


def test_downgrade_does_not_replace_new_media(tmp_path):
    lib, sticker, old = _seed(tmp_path)
    path, new = _upgrade(tmp_path, old)
    assert lib.seed_official(path)["refresh"]["images_updated"] == 1
    previous = lib.get(sticker.id)
    old_pack = _pack(tmp_path / "downgrade.zip", old, version=1)
    assert lib.seed_official(old_pack)["refresh"] == {"status": "current", "version": 1}
    assert lib.get(sticker.id) == previous and lib.image_path(previous).read_bytes() == new


def test_unmatched_corrupt_media_prevents_partial_upgrade(tmp_path):
    lib, sticker, old = _seed(tmp_path)
    path, _ = _upgrade(tmp_path, old)
    with zipfile.ZipFile(path) as archive:
        raw = json.loads(archive.read("manifest.json"))
        first = archive.read("stickers/new.gif")
    raw["stickers"].append({
        "file": "broken.gif", "sha256": "a" * 64, "legacy_sha256": ["b" * 64], "desc": "broken",
    })
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("manifest.json", json.dumps(raw))
        archive.writestr("stickers/new.gif", first)
        archive.writestr("stickers/broken.gif", GIF_BYTES + b"wrong")
    before = lib.catalog_path.read_bytes()
    assert lib.seed_official(path)["refresh"]["status"] == "invalid_assets"
    assert lib.catalog_path.read_bytes() == before and lib.get(sticker.id) == sticker


def test_ambiguous_alias_rejects_pack_without_stamping_version(tmp_path):
    lib, sticker, old = _seed(tmp_path)
    path, _ = _upgrade(tmp_path, old)
    with zipfile.ZipFile(path) as archive:
        raw = json.loads(archive.read("manifest.json"))
        first = archive.read("stickers/new.gif")
    second = GIF_BYTES + b"other motion"
    raw["stickers"].append({
        "file": "second.gif", "sha256": content_sha256(second),
        "legacy_sha256": [content_sha256(old)], "desc": "second",
    })
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("manifest.json", json.dumps(raw))
        archive.writestr("stickers/new.gif", first)
        archive.writestr("stickers/second.gif", second)
    assert lib.refresh_official_labels(path)["status"] == "invalid_assets"
    assert lib.official_pack_version() == 1 and lib.get(sticker.id) == sticker


def test_force_restore_refuses_an_older_official_pack(tmp_path):
    lib, sticker, old = _seed(tmp_path)
    path, _ = _upgrade(tmp_path, old)
    assert lib.seed_official(path)["refresh"]["status"] == "refreshed"
    older = _pack(tmp_path / "older.zip", old, version=1)
    out = lib.seed_official(older, force=True)
    assert out == {"status": "invalid_assets", "error": "official_pack_older"}
    assert lib.count() == 1 and lib.get(sticker.id).sha256 != content_sha256(old)


def test_same_named_user_zone_is_not_an_update_target(tmp_path):
    lib = Library(tmp_path / "library")
    assert lib.load().ok
    zone, error = lib.create_zone("官方")
    assert not error
    old = GIF_BYTES + b"user-owned"
    sticker, error = lib.add(data=old, desc="owner", tags=[], zone=zone)
    assert not error
    path, _ = _upgrade(tmp_path, old)
    assert lib.refresh_official_labels(path) == {"status": "no_pack"}
    assert lib.get(sticker.id) == sticker
