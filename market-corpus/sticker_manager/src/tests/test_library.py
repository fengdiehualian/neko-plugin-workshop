"""services/library.py 的持久层测试（tmp_path 文件系统，真写盘）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import GIF_BYTES, JPEG_BYTES, NOT_AN_IMAGE, PNG_BYTES
from sticker_manager.services.library import ERR_NOT_FOUND, Library


def _library(root) -> Library:
    lib = Library(root)
    assert lib.load().ok
    return lib


class TestAddAndLoad:
    def test_add_writes_file_and_catalog(self, tmp_path):
        lib = _library(tmp_path)
        sticker, error = lib.add(data=PNG_BYTES, desc="笑", tags=["开心"], now=100.0)
        assert error == "" and sticker is not None
        # 文件真实落盘，扩展名由魔数决定
        path = lib.image_path(sticker)
        assert path.is_file()
        assert path.read_bytes() == PNG_BYTES
        assert path.name == f"{sticker.id}.png"
        # 重载后条目还在
        lib2 = _library(tmp_path)
        assert lib2.get(sticker.id) is not None
        assert lib2.get(sticker.id).desc == "笑"

    def test_add_rejects_non_image(self, tmp_path):
        lib = _library(tmp_path)
        sticker, error = lib.add(data=NOT_AN_IMAGE, desc="坏", tags=[])
        assert sticker is None
        assert error == "invalid_image"
        assert lib.count() == 0

    def test_extension_follows_magic_not_request(self, tmp_path):
        # 入库口只认文件头：喂 jpg 字节就是 .jpg，没有 filename 参数可撒谎
        lib = _library(tmp_path)
        sticker, _ = lib.add(data=JPEG_BYTES, desc="照片", tags=[])
        assert sticker.file.endswith(".jpg")

    def test_gif_keeps_gif_extension(self, tmp_path):
        lib = _library(tmp_path)
        sticker, _ = lib.add(data=GIF_BYTES, desc="动图", tags=[])
        assert sticker.file.endswith(".gif")

    def test_catalog_survives_bad_entries(self, tmp_path):
        lib = _library(tmp_path)
        sticker, _ = lib.add(data=PNG_BYTES, desc="好", tags=[])
        # 手写坏条目进 catalog.json：好条目必须还在（宽松丢弃，不炸整本）
        raw = json.loads(lib.catalog_path.read_text(encoding="utf-8"))
        raw["stickers"].append({"nope": True})
        raw["stickers"].append({"id": "bad", "file": 42})
        lib.catalog_path.write_text(json.dumps(raw), encoding="utf-8")
        lib2 = _library(tmp_path)
        assert lib2.get(sticker.id) is not None
        assert lib2.get("bad") is None

    def test_unreadable_catalog_reports_io_code(self, tmp_path):
        (tmp_path).mkdir(parents=True, exist_ok=True)
        (tmp_path / "catalog.json").write_text("{ not json", encoding="utf-8")
        lib = Library(tmp_path)
        result = lib.load()
        assert not result.ok
        assert result.code == "library_io_error"
        assert lib.io_dirty is True


class TestCatalogReadProtection:
    @pytest.mark.parametrize("broken", ["{ broken", "[]", "null", '{"stickers": {}}', "{}"])
    def test_failed_read_cannot_be_cached_as_success_or_overwritten(self, tmp_path, broken):
        lib = _library(tmp_path)
        sticker, _ = lib.add(data=PNG_BYTES, desc="original", tags=[])
        lib.catalog_path.write_text(broken, encoding="utf-8")
        damaged = Library(tmp_path)
        assert not damaged.load().ok
        assert not damaged.load().ok
        assert not damaged.save().ok
        assert damaged.catalog_path.read_text(encoding="utf-8") == broken
        damaged.repair()
        assert lib.image_path(sticker).read_bytes() == PNG_BYTES

    def test_transient_read_failure_recovers_without_losing_last_good_state(self, tmp_path, monkeypatch):
        lib = _library(tmp_path)
        sticker, _ = lib.add(data=PNG_BYTES, desc="original", tags=[])
        original = lib.catalog_path.read_bytes()
        reader = Path.read_text

        def unreadable(path, *args, **kwargs):
            if path == lib.catalog_path:
                raise PermissionError("temporary read failure")
            return reader(path, *args, **kwargs)

        with monkeypatch.context() as patch:
            patch.setattr(Path, "read_text", unreadable)
            assert not lib.load(force=True).ok
            assert lib.get(sticker.id) is not None
            assert not lib.load().ok
            assert not lib.save().ok
            assert lib.add(data=JPEG_BYTES, desc="new", tags=[])[1] == "library_io_error"
        assert lib.catalog_path.read_bytes() == original
        assert lib.load().ok and not lib.io_dirty
        assert lib.count() == 1 and lib.get(sticker.id) is not None
        assert lib.add(data=JPEG_BYTES, desc="new", tags=[])[1] == ""

    def test_deleted_catalog_after_load_is_not_treated_as_fresh_install(self, tmp_path):
        lib = _library(tmp_path)
        sticker, _ = lib.add(data=PNG_BYTES, desc="original", tags=[])
        lib.catalog_path.unlink()
        assert not lib.load(force=True).ok
        assert not lib.save().ok
        lib.repair()
        assert lib.image_path(sticker).is_file()
        assert not lib.catalog_path.exists()
        restarted = Library(tmp_path)
        assert not restarted.load().ok
        assert not restarted.save().ok
        restarted.repair()
        assert lib.image_path(sticker).is_file()

    def test_repaired_catalog_restores_normal_operations(self, tmp_path):
        lib = _library(tmp_path)
        sticker, _ = lib.add(data=PNG_BYTES, desc="original", tags=[])
        original = lib.catalog_path.read_bytes()
        lib.catalog_path.write_text("{ broken", encoding="utf-8")
        assert not lib.load(force=True).ok
        lib.catalog_path.write_bytes(original)
        assert lib.load().ok and lib.get(sticker.id) is not None
        assert lib.save().ok

    def test_write_failure_does_not_permanently_block_retry(self, tmp_path, monkeypatch):
        lib = _library(tmp_path)
        sticker, _ = lib.add(data=PNG_BYTES, desc="original", tags=[])
        with monkeypatch.context() as patch:
            def fail_replace(*args, **kwargs):
                raise PermissionError("temporary write failure")
            patch.setattr(Path, "replace", fail_replace)
            assert lib.update(sticker.id, desc="changed")[1] == "library_io_error"
        assert lib.update(sticker.id, desc="changed")[1] == ""
        assert _library(tmp_path).get(sticker.id).desc == "changed"


class TestMutations:
    def test_update_desc_and_disable(self, tmp_path):
        lib = _library(tmp_path)
        sticker, _ = lib.add(data=PNG_BYTES, desc="旧", tags=["a"], now=1.0)
        updated, error = lib.update(sticker.id, desc="新", disabled=True)
        assert error == ""
        assert updated.desc == "新"
        assert updated.disabled is True
        assert updated.tags == ["a"]
        # 重载确认落盘
        lib2 = _library(tmp_path)
        assert lib2.get(sticker.id).desc == "新"
        assert lib2.get(sticker.id).disabled is True

    def test_update_missing_returns_not_found(self, tmp_path):
        lib = _library(tmp_path)
        _updated, error = lib.update("nope", desc="x")
        assert error == ERR_NOT_FOUND

    def test_remove_deletes_entry_and_file(self, tmp_path):
        lib = _library(tmp_path)
        sticker, _ = lib.add(data=PNG_BYTES, desc="删我", tags=[])
        assert lib.remove(sticker.id) == ""
        assert lib.get(sticker.id) is None
        assert not lib.image_path(sticker).exists()
        lib2 = _library(tmp_path)
        assert lib2.get(sticker.id) is None

    def test_remove_missing(self, tmp_path):
        lib = _library(tmp_path)
        assert lib.remove("nope") == ERR_NOT_FOUND

    def test_touch_used_persists(self, tmp_path):
        lib = _library(tmp_path)
        sticker, _ = lib.add(data=PNG_BYTES, desc="用", tags=[])
        lib.touch_used(sticker.id, now=55.0)
        lib2 = _library(tmp_path)
        reloaded = lib2.get(sticker.id)
        assert reloaded.use_count == 1
        assert reloaded.last_used_at == 55.0


class TestUsageLedger:
    def test_append_and_read_newest_first(self, tmp_path):
        lib = _library(tmp_path)
        lib.append_usage({"at": 1.0, "id": "a", "ok": True}, keep=100)
        lib.append_usage({"at": 2.0, "id": "b", "ok": False}, keep=100)
        entries = lib.read_usage(limit=10)
        assert [e["id"] for e in entries] == ["b", "a"]

    def test_keep_trims_oldest(self, tmp_path):
        lib = _library(tmp_path)
        for i in range(30):
            lib.append_usage({"at": float(i), "id": f"s{i}", "ok": True}, keep=20)
        entries = lib.read_usage(limit=100)
        assert len(entries) == 20
        assert entries[0]["id"] == "s29"
        assert entries[-1]["id"] == "s10"

    def test_missing_usage_file_is_empty(self, tmp_path):
        lib = _library(tmp_path)
        assert lib.read_usage() == []


class TestDeduplication:
    def test_same_bytes_rejected_as_duplicate(self, tmp_path):
        lib = _library(tmp_path)
        first, error = lib.add(data=PNG_BYTES, desc="笑", tags=[])
        assert error == "" and first.sha256  # 新入库即带内容指纹
        second, error = lib.add(data=PNG_BYTES, desc="又笑", tags=[])
        assert second is None and error == "duplicate_image"
        # 被拒的图不许留下孤儿文件
        files = list(lib.stickers_dir.iterdir())
        assert [f.name for f in files] == [first.file]

    def test_different_bytes_still_accepted(self, tmp_path):
        lib = _library(tmp_path)
        assert lib.add(data=PNG_BYTES, desc="甲", tags=[])[1] == ""
        assert lib.add(data=JPEG_BYTES, desc="乙", tags=[])[1] == ""

    def test_legacy_entry_without_hash_detected_and_backfilled(self, tmp_path):
        lib = _library(tmp_path)
        sticker, _ = lib.add(data=PNG_BYTES, desc="旧", tags=[])
        # 模拟 v0.1.1 之前的旧数据：catalog 里没有 sha256 键
        raw = json.loads(lib.catalog_path.read_text(encoding="utf-8"))
        for entry in raw["stickers"]:
            entry.pop("sha256", None)
        lib.catalog_path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
        legacy = _library(tmp_path)
        assert legacy.get(sticker.id).sha256 == ""
        dup, error = legacy.add(data=PNG_BYTES, desc="重", tags=[])
        assert dup is None and error == "duplicate_image"
        # 查重时顺手回填：重载后旧条目带上指纹
        after = _library(tmp_path)
        assert after.get(sticker.id).sha256 != ""


class TestRepair:
    def test_removes_entries_with_missing_files_and_orphans(self, tmp_path):
        lib = _library(tmp_path)
        keep, _ = lib.add(data=PNG_BYTES, desc="留", tags=[])
        gone, _ = lib.add(data=JPEG_BYTES, desc="图没了", tags=[])
        lib.image_path(gone).unlink()
        orphan = lib.stickers_dir / "deadbeef00.png"
        orphan.write_bytes(b"\x89PNG\r\n\x1a\n" + b"1" * 32)
        counts = lib.repair()
        assert counts["removed_entries"] == 1
        assert counts["purged_files"] == 1
        assert counts["backfilled_hashes"] == 0
        assert lib.get(keep.id) is not None and lib.get(gone.id) is None
        assert not orphan.exists()
        after = _library(tmp_path)
        assert after.get(gone.id) is None

    def test_backfills_hashes_of_legacy_entries(self, tmp_path):
        lib = _library(tmp_path)
        sticker, _ = lib.add(data=PNG_BYTES, desc="旧", tags=[])
        raw = json.loads(lib.catalog_path.read_text(encoding="utf-8"))
        for entry in raw["stickers"]:
            entry.pop("sha256", None)
        lib.catalog_path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
        legacy = _library(tmp_path)
        counts = legacy.repair()
        assert counts["backfilled_hashes"] == 1
        assert counts["removed_entries"] == 0
        assert legacy.get(sticker.id).sha256 != ""

    def test_repair_on_empty_library_is_clean(self, tmp_path):
        lib = _library(tmp_path)
        assert lib.repair() == {"removed_entries": 0, "purged_files": 0, "backfilled_hashes": 0}


class TestInbox:
    def _put(self, lib, name, data):
        lib.inbox_dir.mkdir(parents=True, exist_ok=True)
        (lib.inbox_dir / name).write_bytes(data)

    def test_imports_with_filename_desc_and_removes_sources(self, tmp_path):
        lib = _library(tmp_path)
        self._put(lib, "开心_cat.png", PNG_BYTES)
        self._put(lib, "无语.jpg", JPEG_BYTES)
        assert lib.ingest_inbox is not None
        summary = lib.ingest_inbox(tags=["批量"])
        assert summary == {"imported": 2, "duplicates": 0, "rejected": 0, "failed": 0}
        rows = lib.all()
        descs = {s.desc for s in rows}
        assert descs == {"开心 cat", "无语"}
        assert all(s.tags == ["批量"] for s in rows)
        # 成功的源文件被消耗掉
        assert lib.inbox_files() == []

    def test_duplicates_skipped_and_source_removed(self, tmp_path):
        lib = _library(tmp_path)
        assert lib.add(data=PNG_BYTES, desc="原版", tags=[])[1] == ""
        self._put(lib, "same.png", PNG_BYTES)
        summary = lib.ingest_inbox(tags=[])
        assert summary["duplicates"] == 1 and summary["imported"] == 0
        assert lib.inbox_files() == []  # 重复件也删，不然每轮重报
        assert lib.count() == 1

    def test_oversize_and_bad_files_stay_for_retry(self, tmp_path):
        lib = _library(tmp_path)
        self._put(lib, "big.png", b"\x89PNG\r\n\x1a\n" + b"1" * 120)
        self._put(lib, "fake.png", NOT_AN_IMAGE)
        summary = lib.ingest_inbox(tags=[], max_bytes=64)
        assert summary["rejected"] == 2
        names = [p.name for p in lib.inbox_files()]
        assert names == ["big.png", "fake.png"]  # 留着让用户处置
        assert lib.count() == 0

    def test_hidden_and_empty_inbox(self, tmp_path):
        lib = _library(tmp_path)
        assert lib.ingest_inbox(tags=[]) == {"imported": 0, "duplicates": 0, "rejected": 0, "failed": 0}
        self._put(lib, ".desktop.ini", b"junk")
        assert lib.inbox_files() == []  # 隐藏项不进计数也不进导入
        assert lib.ingest_inbox(tags=[])["imported"] == 0
        assert (lib.inbox_dir / ".desktop.ini").exists()
