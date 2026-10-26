# pyright: reportMissingImports=false
"""J-3（v0.13.0 官方包标签下发尺）：`refresh_official_labels` 的门。

补的是 J-2 留的洞：`import_pack` 对同指纹只计 duplicates 就跳过，`official_seeded`
又是一次性台账——重打的官方包带着新分类/新梗义进来，老装机一张也不会变。

钉六条尺：
1. **包比库新才刷**：同版本/更旧/不带 `pack_version` 一律什么都不做（主人的自打包永不当官方库）；
2. **只刷官方区**（builtin 位）：自制区的条目一个字都不动；
3. **主人改过的跳过**：`owner_edited` 立了位的条目，包再新也不覆盖；
4. **分类说明只补缺**：主人写过的组话不被包消音（与 `import_pack` 同一条尺）；
5. **只动文本**：图片文件、id、启停、使用台账一律不碰；
6. **版本落盘**：刷过的版本重读还在，写盘失败则不盖章（下次启动自然重试）。
"""

from __future__ import annotations

import json
import zipfile

from conftest import GIF_BYTES
from sticker_manager.core.catalog import Sticker, content_sha256
from sticker_manager.core.labeling import plan_label_refresh
from sticker_manager.core.pack import PackEntry
from sticker_manager.services.library import Library


def _gif(i: int) -> bytes:
    return GIF_BYTES + bytes([i]) * 32 + f"|v{i}".encode("utf-8")


def _make_pack(tmp_path, name: str, version: int | None, rows: list[tuple[bytes, str, str, str]], groups=None):
    """打一个官方包替身：rows = [(字节, desc, caption, 分类)]；version=None 表示不带版本键。"""
    manifest: dict = {"app": "sticker_manager", "stickers": [], "groups": groups or []}
    if version is not None:
        manifest["pack_version"] = version
    for idx, (data, desc, caption, group) in enumerate(rows):
        manifest["stickers"].append(
            {
                "file": f"{idx:03d}_{desc}.gif",
                "desc": desc,
                "tags": [group],
                "group": group,
                "sha256": content_sha256(data),
                "caption": caption,
                "visible_text": "",
            }
        )
    path = tmp_path / name
    with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED) as pack:
        pack.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False))
        for idx, (data, _desc, _caption, _group) in enumerate(rows):
            pack.writestr(f"stickers/{idx:03d}_{_desc}.gif", data)
    return path


def _seeded(tmp_path, count: int = 3):
    """先用不带版本的包播好种（v0.12.0 装机态：标签是文件名占位，版本尺为 0）。"""
    lib = Library(tmp_path / "library")
    lib.load()
    rows = [(_gif(i), f"占位{i}", "", "") for i in range(count)]
    pack = _make_pack(tmp_path, "old.zip", None, rows)
    out = lib.seed_official(pack)
    assert out["status"] == "seeded" and out["imported"] == count
    return lib, pack


class TestRefreshScale:
    def test_labeled_pack_refreshes_seeded_library(self, tmp_path):
        lib, _ = _seeded(tmp_path)
        rows = [(_gif(i), f"梗{i}", f"这张想说第{i}件事", "比心与撒娇") for i in range(3)]
        pack = _make_pack(tmp_path, "new.zip", 1, rows, groups=[{"name": "比心与撒娇", "desc": "示好贴贴时用"}])
        out = lib.refresh_official_labels(pack)
        assert out["status"] == "refreshed" and out["refreshed"] == 3 and out["unmatched"] == 0
        stickers = lib.active_pool()
        assert {s.caption for s in stickers} == {"这张想说第0件事", "这张想说第1件事", "这张想说第2件事"}
        assert {s.group for s in stickers} == {"比心与撒娇"}
        assert lib.group_descs()["比心与撒娇"] == "示好贴贴时用"
        # 她目录里的正文真的从占位变成了梗义
        assert all("梗" not in s.catalog_body(lib.group_descs()) for s in stickers)

    def test_owner_edited_words_survive_pack_update(self, tmp_path):
        lib, _ = _seeded(tmp_path)
        target = lib.active_pool()[0]
        ok, error = lib.update(target.id, desc="主人自己的话")
        assert ok and not error
        rows = [(_gif(i), f"梗{i}", "包里的话", "比心与撒娇") for i in range(3)]
        out = lib.refresh_official_labels(_make_pack(tmp_path, "new.zip", 1, rows))
        # 字段级让位：措辞（desc/caption）归主人，分类与标签照刷——整条跳过会让这张永远没分类。
        assert out["status"] == "refreshed" and out["refreshed"] == 3 and out["skipped_edited"] == 1
        kept = lib.get(target.id)
        assert kept.desc == "主人自己的话" and kept.caption == ""
        assert kept.group == "比心与撒娇" and kept.tags == ["比心与撒娇"]
        assert kept.owner_edited == ["desc"]
        assert kept.catalog_body(lib.group_descs()) == "主人自己的话"

    def test_same_or_older_version_does_nothing(self, tmp_path):
        lib, _ = _seeded(tmp_path)
        rows = [(_gif(i), f"梗{i}", "包里的话", "比心") for i in range(3)]
        pack = _make_pack(tmp_path, "v2.zip", 2, rows)
        assert lib.refresh_official_labels(pack)["status"] == "refreshed"
        again = lib.refresh_official_labels(pack)
        assert again == {"status": "current", "version": 2}
        older = _make_pack(tmp_path, "v1.zip", 1, rows)
        assert lib.refresh_official_labels(older)["status"] == "current"

    def test_pack_without_version_is_never_applied(self, tmp_path):
        lib, _ = _seeded(tmp_path)
        rows = [(_gif(i), "主人的自打包", "", "") for i in range(3)]
        out = lib.refresh_official_labels(_make_pack(tmp_path, "bare.zip", None, rows))
        assert out == {"status": "no_version"}
        assert lib.official_pack_version() == 0

    def test_other_zone_untouched(self, tmp_path):
        lib, _ = _seeded(tmp_path)
        home = [z["id"] for z in lib.zones() if not z.get("builtin")][0]
        mine, error = lib.add(data=GIF_BYTES + b"z" * 32 + b"|mine", desc="自藏", tags=[], zone=home, now=9.0)
        assert not error and mine is not None
        rows = [(_gif(i), f"梗{i}", "包里的话", "比心") for i in range(3)]
        lib.refresh_official_labels(_make_pack(tmp_path, "new.zip", 1, rows))
        assert lib.get(mine.id).desc == "自藏" and lib.get(mine.id).owner_edited == []

    def test_group_desc_only_fills_gaps(self, tmp_path):
        lib, _ = _seeded(tmp_path)
        rows = [(_gif(i), f"梗{i}", "包里的话", "我的类") for i in range(3)]
        pack = _make_pack(tmp_path, "new.zip", 1, rows, groups=[{"name": "我的类", "desc": "包说这么用"}])
        ok, error = lib.create_group("我的类", "主人先写的话", zone=lib.official_zone())
        assert ok and not error
        lib.refresh_official_labels(pack)
        assert lib.group_descs()["我的类"] == "主人先写的话"
        assert lib.zone_of_group("我的类") == lib.official_zone()

    def test_version_survives_reload_and_unreadable_pack(self, tmp_path):
        lib, _ = _seeded(tmp_path)
        rows = [(_gif(i), f"梗{i}", "包里的话", "比心") for i in range(3)]
        pack = _make_pack(tmp_path, "new.zip", 7, rows)
        lib.refresh_official_labels(pack)
        raw = json.loads((tmp_path / "library" / "catalog.json").read_text(encoding="utf-8"))
        assert raw["official_pack_version"] == 7
        again = Library(tmp_path / "library")
        assert again.load().ok and again.official_pack_version() == 7
        assert again.refresh_official_labels(tmp_path / "nope.zip")["status"] == "unreadable"

    def test_library_without_official_zone(self, tmp_path):
        lib = Library(tmp_path / "fresh")
        assert lib.load().ok
        assert lib.refresh_official_labels(tmp_path / "whatever.zip") == {"status": "no_pack"}


class TestPlanPure:
    """`core/labeling.py` 的判断尺：不碰 IO，逐条钉死。"""

    def test_identical_values_produce_no_patch(self):
        entry = PackEntry(file="a.gif", desc="甲", tags=[], group="", sha256="d1", caption="", visible_text="")
        sticker = _sticker("d1", desc="甲")
        plan = plan_label_refresh([sticker], [entry])
        assert plan.patches == [] and plan.refreshed == 0

    def test_entry_without_digest_never_pairs(self):
        entry = PackEntry(file="a.gif", desc="甲", tags=[], group="", sha256="", caption="x", visible_text="")
        plan = plan_label_refresh([_sticker("d1")], [entry])
        assert plan.unmatched == 1 and plan.patches == []

    def test_tags_compare_as_list_not_identity(self):
        entry = PackEntry(file="a.gif", desc="甲", tags=["x"], group="", sha256="d1", caption="", visible_text="")
        plan = plan_label_refresh([_sticker("d1", desc="甲", tags=["x"])], [entry])
        assert plan.patches == []
        other = PackEntry(file="a.gif", desc="甲", tags=["y"], group="", sha256="d1", caption="", visible_text="")
        assert plan_label_refresh([_sticker("d1", desc="甲", tags=["x"])], [other]).refreshed == 1


    def test_word_fields_protect_each_other(self):
        """主人只碰过 desc，caption 也要让位——否则她的话没被覆盖，却永远不露面。"""
        entry = PackEntry(file="a.gif", desc="包的话", tags=["t"], group="g", sha256="d1", caption="包的梗义", visible_text="")
        plan = plan_label_refresh([_sticker("d1", desc="主人的话", owner=["desc"])], [entry])
        fields = plan.patches[0][1]
        assert "desc" not in fields and "caption" not in fields
        assert fields["group"] == "g" and fields["tags"] == ["t"]

    def test_group_only_edit_does_not_silence_caption(self):
        entry = PackEntry(file="a.gif", desc="包的话", tags=[], group="g", sha256="d1", caption="包的梗义", visible_text="")
        plan = plan_label_refresh([_sticker("d1", desc="占位", owner=["group"])], [entry])
        assert plan.patches[0][1]["caption"] == "包的梗义"

    def test_unknown_owner_fields_are_dropped_on_load(self):
        raw = {"id": "s1", "file": "a.gif", "desc": "甲", "owner_edited": ["desc", "zone", 7, "nonsense"]}
        sticker = Sticker.from_dict(raw)
        assert sticker.owner_edited == ["desc"]


def _sticker(digest: str, *, desc: str = "占位", tags=None, owner=None):
    return Sticker(id=digest[::-1][:10] or "sid", file="x.gif", desc=desc, tags=list(tags or []), sha256=digest, owner_edited=list(owner or []))
