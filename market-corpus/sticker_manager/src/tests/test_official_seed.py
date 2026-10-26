# pyright: reportMissingImports=false
"""J-2（v0.12.0 官方区内置 P2A）：播种尺的门。

钉五条不变量：
1. **只播一次**：`official_seeded` 台账在册就不再碰包；坏包（rejected/failed>0）不盖章、下拍重试；
2. **同名收编**：主人手建的「官方」区被补打 builtin 位就地收编，不造重名区；
3. **新装默认激活官方区，旧库不抢台**：只有播种前全库零图才把她的世界切到官方区；
4. **幂等**：force 重播吃指纹查重，一张不重入；
5. **入口与快照**：`zone_restore_official` 缺包回 `official_pack_missing`；dashboard 带 official 三键；
   宽松读——`official_seeded` 非布尔一律当未播。
"""

from __future__ import annotations

import json
import zipfile

from conftest import GIF_BYTES, FakeConfig, FakeHostContext, build_plugin
from sticker_manager.core.configuration import SendSettings, StickerManagerSettings, StorageSettings
from sticker_manager.services.library import Library


def _make_src_pack(root) -> "object":
    """用真代码打一个官方包替身：3 张 gif 进库再 export_pack；回包里的 zip 路径（Path）。"""
    src = Library(root / "src")
    src.load()
    for i, name in enumerate(["官方甲", "官方乙", "官方丙"]):
        sticker, error = src.add(
            data=GIF_BYTES + bytes([i]) * 32 + ("|" + name).encode("utf-8"), desc=name, tags=[], now=1000.0 + i
        )
        assert not error, error
        assert sticker is not None
    result, error = src.export_pack(now=2000.0)
    assert not error, error
    from pathlib import Path

    return Path(result["file"])


def _plugin(tmp_path, pack_path=None):
    host = FakeHostContext(
        data_root=tmp_path,
        config=FakeConfig(data={"sticker_manager": {"enabled": True}}),
    )
    plugin, _host = build_plugin(host)
    plugin._settings = StickerManagerSettings(enabled=True, send=SendSettings(), storage=StorageSettings())
    if pack_path is not None:
        plugin._official_pack_path = lambda: pack_path  # 实例面换尺（假包替身，路径同形）
    return plugin


def _lib(tmp_path) -> Library:
    lib = Library(tmp_path / "library")
    lib.load()
    return lib


class TestSeedLifecycle:
    def test_fresh_install_seeds_and_activates_official(self, tmp_path):
        pack = _make_src_pack(tmp_path)
        lib = _lib(tmp_path)
        out = lib.seed_official(pack)
        assert out["status"] == "seeded" and out["imported"] == 3 and out["duplicates"] == 0
        zone_id = lib.official_zone()
        assert zone_id and lib.active_zone() == zone_id  # 新装默认激活官方区
        assert lib.official_seeded() is True
        meta = [z for z in lib.zones() if z["id"] == zone_id][0]
        assert meta["name"] == "官方" and meta["builtin"] is True
        # 盘形：两个新键都落了盘
        raw = json.loads((tmp_path / "library" / "catalog.json").read_text(encoding="utf-8"))
        assert raw.get("official_seeded") is True
        assert any(z.get("builtin") for z in raw["zones"])

    def test_seed_once_then_silent(self, tmp_path):
        pack = _make_src_pack(tmp_path)
        lib = _lib(tmp_path)
        lib.seed_official(pack)
        # 台账在册：再喊（非 force）不碰包、不挪图、不切区
        lib.add(
            data=GIF_BYTES + b"x" * 35 + ("|自制").encode("utf-8"),
            desc="自制",
            tags=[],
            zone=lib.active_zone(),
            now=9999.0,
        )
        out = lib.seed_official(pack)
        # 台账在册：再喊（非 force）不碰包、不挪图、不切区。
        # 但 J-3 起"already"仍走一次标签刷新——这包的替身来自 export_pack，不带 pack_version，
        # 所以尺据 no_version 什么都不做（主人的自打包永远不该被当成官方库覆写）。
        assert out == {"status": "already", "refresh": {"status": "no_version"}}
        assert lib.count() == 4 and len([s for s in lib.active_pool() if s.desc == "自制"]) == 1

    def test_existing_library_not_hijacked(self, tmp_path):
        pack = _make_src_pack(tmp_path)
        lib = _lib(tmp_path)
        home = lib.active_zone()  # 默认自制区
        lib.add(data=GIF_BYTES + b"a" * 35 + ("|old").encode("utf-8"), desc="旧藏", tags=[], zone=home, now=500.0)
        out = lib.seed_official(pack)
        assert out["status"] == "seeded" and lib.count() == 4
        assert lib.active_zone() == home  # 旧库不抢台
        assert lib.zone_of_sticker(lib.get([s.id for s in lib.all() if s.desc == "官方甲"][0])) == lib.official_zone()

    def test_manual_official_zone_co_opted(self, tmp_path):
        pack = _make_src_pack(tmp_path)
        lib = _lib(tmp_path)
        manual, error = lib.create_zone("官方", "主人手建的坑")
        assert not error
        lib.seed_official(pack)
        zones = [z for z in lib.zones() if z["name"] == "官方"]
        assert len(zones) == 1 and zones[0]["id"] == manual and zones[0]["builtin"] is True
        assert zones[0]["total"] == 3

    def test_force_reseed_is_duplicate_safe(self, tmp_path):
        pack = _make_src_pack(tmp_path)
        lib = _lib(tmp_path)
        lib.seed_official(pack)
        out = lib.seed_official(pack, force=True)
        assert out["status"] == "restored" and out["imported"] == 0 and out["duplicates"] == 3
        assert lib.count() == 3

    def test_dirty_pack_does_not_seal_ledger(self, tmp_path):
        # 包里掺一张坏图：rejected>0 → 台账不盖章（下次启动重试），但没盖的也不假装成功
        dirty = tmp_path / "dirty.zip"
        with zipfile.ZipFile(dirty, "w") as z:
            z.writestr("stickers/good1.gif", (GIF_BYTES + b"1" * 40))
            z.writestr("stickers/bad.gif", b"definitely not a gif")
        lib = _lib(tmp_path)
        out = lib.seed_official(dirty)
        assert out["imported"] == 1 and out["rejected"] == 1
        assert lib.official_seeded() is False
        # 重试：好图只进一次（指纹查重），坏图仍拒
        out2 = lib.seed_official(dirty)
        assert out2["imported"] == 0 and out2["duplicates"] == 1 and out2["rejected"] == 1
        assert lib.count() == 1


class TestLenientLoad:
    def test_non_bool_seeded_ledger_reads_as_unseeded(self, tmp_path):
        pack = _make_src_pack(tmp_path)
        lib = _lib(tmp_path)
        lib.seed_official(pack)
        catalog = tmp_path / "library" / "catalog.json"
        raw = json.loads(catalog.read_text(encoding="utf-8"))
        raw["official_seeded"] = "yes please"
        catalog.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
        again = _lib(tmp_path)
        again._root = lib._root  # 同一数据根
        assert again.official_seeded() is False

    def test_old_catalog_shape_loads_without_new_keys(self, tmp_path):
        # v0.11 的盘（无 builtin / official_seeded 键）：读得动，且不被判成官方区
        root = tmp_path / "library"
        root.mkdir(parents=True)
        (root / "catalog.json").write_text(
            json.dumps(
                {
                    "version": 2,
                    "stickers": [],
                    "groups": {},
                    "zones": [{"id": "z1", "name": "官方", "desc": "旧手建"}],
                    "active_zone": "z1",
                    "group_zone": {},
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        lib = Library(root)
        assert lib.load().ok
        assert lib.official_seeded() is False
        assert lib.official_zone() == "z1"  # 同名收编的尺：名字也能认出目标
        out = lib.seed_official(_make_src_pack(tmp_path))
        assert out["zone"] == "z1" and out["created"] is False


class TestEntryFace:
    def test_restore_entry_without_pack(self, tmp_path, run_async):
        plugin = _plugin(tmp_path / "p1", pack_path=tmp_path / "p1" / "nowhere.zip")
        result = run_async(plugin.zone_restore_official_entry())
        assert not result.is_ok()
        assert str(result.error) == "official_pack_missing"

    def test_restore_entry_after_zone_removal(self, tmp_path, run_async):
        pack = _make_src_pack(tmp_path)
        plugin = _plugin(tmp_path / "p2", pack_path=pack)
        started = run_async(plugin.on_startup())
        assert started.is_ok() and started.value["official_seed"] == "seeded"
        lib = plugin._library
        official = lib.official_zone()
        home = [z["id"] for z in lib.zones() if z["id"] != official][0]
        lib.add(data=GIF_BYTES + b"h" * 35 + ("|home").encode("utf-8"), desc="自藏", tags=[], zone=home, now=9000.0)
        ok, error, _ = lib.remove_zone(official)
        assert ok and not error
        state = run_async(plugin.dashboard_context())
        assert state["official"]["zone"] == "" and state["official"]["pack"] is True  # 按钮该出现的判据
        result = run_async(plugin.zone_restore_official_entry())
        assert result.is_ok() and result.value["status"] == "restored" and result.value["imported"] == 3
        # 旧库（有自藏）恢复不抢台
        assert lib.active_zone() == home

    def test_startup_survives_without_pack(self, tmp_path, run_async):
        plugin = _plugin(tmp_path / "p3", pack_path=tmp_path / "p3" / "absent.zip")
        started = run_async(plugin.on_startup())
        assert started.is_ok() and started.value["official_seed"] == "absent"
        assert plugin._library.official_seeded() is False
