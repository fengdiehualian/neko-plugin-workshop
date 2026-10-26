"""套图包（v0.2.0）：core 协议纯函数 + services 的导出/导入/安全门。

四族门：
1. `safe_member_name`：zip-slip 的三面（目录/上跳、盘符/绝对、隐藏项）——
   注意它**返回 basename**：防逃逸靠"这个名字永远不会被拿去拼路径写盘"，
   导入落盘全走 add()（服务端发号），条目名只用于在包内定位字节。
2. manifest roundtrip：build → parse 元数据守恒；坏条目宽松丢、不炸整包。
3. Library 导出：空库如实拒；zip 里 manifest + stickers/ 形状真实。
4. Library 导入：带 manifest / 裸图 / 损坏 / zip-slip / 超限 的计数与删留纪律。
"""

from __future__ import annotations

import io
import json
import zipfile

from conftest import JPEG_BYTES, NOT_AN_IMAGE, PNG_BYTES, WEBP_BYTES
from sticker_manager.core.catalog import Sticker
from sticker_manager.core.pack import (
    PACK_DIR_PREFIX,
    PACK_MANIFEST_FILENAME,
    PACK_MANIFEST_VERSION,
    PACK_MAX_ENTRIES,
    build_manifest,
    parse_manifest,
    safe_member_name,
)
from sticker_manager.services.library import ERR_EMPTY, Library


def _library(root) -> Library:
    lib = Library(root)
    assert lib.load().ok
    return lib


def _zip_bytes(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as pack:
        for name, data in members.items():
            pack.writestr(name, data)
    return buf.getvalue()


def _seed(root, count: int = 3) -> tuple[Library, list[Sticker]]:
    lib = _library(root)
    made: list[Sticker] = []
    for index in range(count):
        sticker, error = lib.add(
            data=[PNG_BYTES, JPEG_BYTES, WEBP_BYTES][index % 3],
            desc=f"表情{index}",
            tags=["t"],
            group="猫猫日常" if index % 2 == 0 else "",
            now=100.0 + index,
        )
        assert error == "" and sticker is not None
        made.append(sticker)
    return lib, made


class TestSafeMemberName:
    def test_plain_and_prefixed_basenames_pass(self):
        assert safe_member_name("a.png") == "a.png"
        assert safe_member_name("stickers/a.png") == "a.png"
        assert safe_member_name("win\\path\\a.gif") == "a.gif"

    def test_escaping_and_hidden_shapes_rejected(self):
        assert safe_member_name("../evil.png") == "evil.png"  # 压平：只用于包内定位
        assert safe_member_name("..") == ""
        assert safe_member_name("/") == ""
        assert safe_member_name("C:/abs.png") == ""  # 冒号（盘符）直接拒
        assert safe_member_name(".hidden.png") == ""
        assert safe_member_name(None) == ""
        assert safe_member_name("   ") == ""


class TestManifestProtocol:
    def test_roundtrip_preserves_metadata(self):
        sticker = Sticker(
            id="abc", file="abc.png", desc="猫猫挥手", tags=["开心"], group="猫猫日常", sha256="ff"
        )
        manifest = build_manifest([sticker])
        assert manifest["version"] == PACK_MANIFEST_VERSION
        entries = parse_manifest(manifest)
        assert len(entries) == 1
        entry = entries[0]
        assert (entry.file, entry.desc, entry.group) == ("abc.png", "猫猫挥手", "猫猫日常")
        assert entry.tags == ["开心"]
        assert entry.sha256 == "ff"

    def test_bad_entries_are_dropped_not_fatal(self):
        manifest = {
            "stickers": [
                "not-a-dict",
                {"file": "..", "desc": "逃逸名"},  # 安全名判空：丢
                {"file": "ok.png", "desc": ""},  # 空描述：回退文件名清洗，留
            ]
        }
        entries = parse_manifest(manifest)
        assert len(entries) == 1
        assert entries[0].desc == "ok"  # desc_from_filename("ok.png")

    def test_unshaped_manifest_gives_empty_list(self):
        assert parse_manifest(None) == []
        assert parse_manifest({"stickers": "nope"}) == []

    def test_entry_cap(self):
        manifest = {"stickers": [{"file": f"i{n}.png"} for n in range(PACK_MAX_ENTRIES + 10)]}
        assert len(parse_manifest(manifest)) == PACK_MAX_ENTRIES


class TestExport:
    def test_empty_library_refuses_loudly(self, tmp_path):
        lib = _library(tmp_path)
        result, error = lib.export_pack()
        assert error == ERR_EMPTY and result == {}

    def test_zip_carries_manifest_and_images(self, tmp_path):
        lib, made = _seed(tmp_path, 3)
        result, error = lib.export_pack(now=1000.0)
        assert error == "" and result["exported"] == 3
        assert result["file"].endswith(".zip")
        with zipfile.ZipFile(result["file"]) as pack:
            names = set(pack.namelist())
            assert PACK_MANIFEST_FILENAME in names
            for sticker in made:
                assert f"{PACK_DIR_PREFIX}{sticker.file}" in names
            manifest = json.loads(pack.read(PACK_MANIFEST_FILENAME).decode("utf-8"))
        assert len(manifest["stickers"]) == 3
        assert all(entry["desc"].startswith("表情") for entry in manifest["stickers"])

    def test_second_export_in_same_second_does_not_overwrite(self, tmp_path):
        lib, _ = _seed(tmp_path, 1)
        first, _ = lib.export_pack(now=1000.0)
        second, _ = lib.export_pack(now=1000.0)
        assert first["file"] != second["file"]


class TestImport:
    def test_pack_roundtrip_between_two_libraries(self, tmp_path):
        source, _ = _seed(tmp_path / "src", 3)
        exported, error = source.export_pack(now=100.0)
        assert error == ""
        target = _library(tmp_path / "dst")
        inbox = target.inbox_dir
        inbox.mkdir(parents=True, exist_ok=True)
        (inbox / "pack.zip").write_bytes(open(exported["file"], "rb").read())
        summary = target.ingest_inbox(tags=[])
        assert summary["imported"] == 3 and summary["rejected"] == 0
        descs = {s.desc for s in target.all()}
        assert descs == {"表情0", "表情1", "表情2"}
        assert {s.group for s in target.all()} == {"猫猫日常", ""}  # manifest 的分组随包带入
        # 导入的 id 是新发的（不继承来源库的号）
        source_ids = {s.id for s in source.all()}
        assert source_ids.isdisjoint({s.id for s in target.all()})
        # 全收下的包：源文件删（与单图通道同一纪律）
        assert not (inbox / "pack.zip").exists()

    def test_bare_zip_imports_with_filename_desc(self, tmp_path):
        lib = _library(tmp_path)
        pack_bytes = _zip_bytes(
            {
                "开心猫.png": PNG_BYTES,
                "nested/挥手.jpg": JPEG_BYTES,
                "note.txt": NOT_AN_IMAGE,
                ".hidden.png": PNG_BYTES,  # 隐藏项：safe_member_name 直接拒，整条不收
            }
        )
        inbox = lib.inbox_dir
        inbox.mkdir(parents=True, exist_ok=True)
        (inbox / "bare.zip").write_bytes(pack_bytes)
        summary = lib.ingest_inbox(tags=["批量"], group="外来包")
        assert summary["imported"] == 2
        assert summary["rejected"] >= 1  # note.txt 过不了魔数
        by_desc = {s.desc for s in lib.all()}
        assert "开心猫" in by_desc and "挥手" in by_desc  # 文件名清洗成描述
        assert all(s.group == "外来包" for s in lib.all())  # 整批 group 参数生效
        assert all("批量" in s.tags for s in lib.all())

    def test_manifest_group_beats_batch_group(self, tmp_path):
        lib = _library(tmp_path)
        manifest = {
            "version": 1,
            "stickers": [{"file": "a.png", "desc": "包内描述", "group": "包里分组"}],
        }
        pack_bytes = _zip_bytes(
            {
                PACK_MANIFEST_FILENAME: json.dumps(manifest).encode("utf-8"),
                PACK_DIR_PREFIX + "a.png": PNG_BYTES,
            }
        )
        inbox = lib.inbox_dir
        inbox.mkdir(parents=True, exist_ok=True)
        (inbox / "g.zip").write_bytes(pack_bytes)
        summary = lib.ingest_inbox(tags=[], group="整批默认")
        assert summary["imported"] == 1
        assert lib.all()[0].group == "包里分组"

    def test_corrupt_zip_counts_failed_and_stays(self, tmp_path):
        lib = _library(tmp_path)
        inbox = lib.inbox_dir
        inbox.mkdir(parents=True, exist_ok=True)
        (inbox / "broken.zip").write_bytes(b"PK not really a zip")
        summary = lib.ingest_inbox(tags=[])
        assert summary["failed"] == 1
        assert (inbox / "broken.zip").exists()  # 没收成：留着（改好重试纪律）

    def test_partial_pack_stays_for_retry(self, tmp_path):
        lib = _library(tmp_path)
        pack_bytes = _zip_bytes({"good.png": PNG_BYTES, "junk.txt": NOT_AN_IMAGE})
        inbox = lib.inbox_dir
        inbox.mkdir(parents=True, exist_ok=True)
        (inbox / "mixed.zip").write_bytes(pack_bytes)
        summary = lib.ingest_inbox(tags=[])
        assert summary["imported"] == 1 and summary["rejected"] == 1
        assert (inbox / "mixed.zip").exists()  # 有没收下的：包留着
        # 重跑：已收的那张只会计重复，不会双份
        before = lib.count()
        summary2 = lib.ingest_inbox(tags=[])
        assert lib.count() == before
        assert summary2["duplicates"] == 1

    def test_zip_slip_member_never_writes_outside_library(self, tmp_path):
        lib = _library(tmp_path)
        pack_bytes = _zip_bytes({"../../evil.png": PNG_BYTES})
        inbox = lib.inbox_dir
        inbox.mkdir(parents=True, exist_ok=True)
        (inbox / "slip.zip").write_bytes(pack_bytes)
        summary = lib.ingest_inbox(tags=[])
        assert summary["imported"] == 1
        # 落盘名由服务端重新发号（<id>.png），恶意名连"存活"的机会都没有：
        assert list(tmp_path.rglob("evil.png")) == []
        assert not (tmp_path.parent / "evil.png").exists()
        assert lib.all()[0].file.endswith(".png") and lib.image_path(lib.all()[0]).is_file()


class TestCaptionInPack:
    """v0.3.0 轮 A：caption/visible_text 随套图包迁移；旧包（无键）宽松兼容。"""

    def test_roundtrip_between_two_libraries_carries_caption(self, tmp_path):
        source = _library(tmp_path / "src")
        sticker, error = source.add(
            data=PNG_BYTES,
            desc="笑",
            tags=["t"],
            caption="被催很久终于交差",
            visible_text="就这？",
            now=100.0,
        )
        assert error == "" and sticker is not None
        exported, error = source.export_pack(now=200.0)
        assert error == ""
        with zipfile.ZipFile(exported["file"]) as pack:
            manifest = json.loads(pack.read(PACK_MANIFEST_FILENAME).decode("utf-8"))
        assert manifest["version"] == PACK_MANIFEST_VERSION
        assert manifest["stickers"][0]["caption"] == "被催很久终于交差"
        target = _library(tmp_path / "dst")
        target.inbox_dir.mkdir(parents=True, exist_ok=True)
        (target.inbox_dir / "pack.zip").write_bytes(open(exported["file"], "rb").read())
        summary = target.ingest_inbox(tags=[])
        assert summary["imported"] == 1
        imported = target.all()[0]
        assert imported.caption == "被催很久终于交差"
        assert imported.visible_text == "就这？"

    def test_v1_manifest_without_caption_is_loose(self, tmp_path):
        # 旧包（v1，根本没这两个键）：照常导入，caption 回空——宽松兼容不加迁移
        manifest = {"version": 1, "stickers": [{"file": "a.png", "desc": "老包描述"}]}
        pack_bytes = _zip_bytes(
            {
                PACK_MANIFEST_FILENAME: json.dumps(manifest).encode("utf-8"),
                PACK_DIR_PREFIX + "a.png": PNG_BYTES,
            }
        )
        lib = _library(tmp_path)
        lib.inbox_dir.mkdir(parents=True, exist_ok=True)
        (lib.inbox_dir / "old.zip").write_bytes(pack_bytes)
        summary = lib.ingest_inbox(tags=[])
        assert summary["imported"] == 1
        assert lib.all()[0].caption == ""
