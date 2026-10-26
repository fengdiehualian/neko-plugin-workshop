# pyright: reportMissingImports=false
"""J-1（v0.11.0 区）：三层「区→分类→图」的门。

钉四条不变量：
1. 旧库（v1 无 zones 键）load 即宽松迁入默认区，且**当场补写一次盘**；
2. 区的生命周期：建/改名/说明/激活/拆（连带分类与图，最后一个区不许拆）；
3. 她只感知激活区——非激活区的分类不进目录、图发不出（连显式 id 也算没找到）；
4. 分类名全库唯一：跨区同名也回 group_exists（她的世界按名字过活，名字不能有两个家）。
"""

from __future__ import annotations

import base64
import json

from conftest import PNG_BYTES, FakeConfig, FakeHostContext, build_plugin
from sticker_manager.core.configuration import SendSettings, StickerManagerSettings, StorageSettings
from sticker_manager.services.library import Library


def _ctx(lanlan: str) -> dict:
    return {"_ctx": {"lanlan_name": lanlan}}


def _plugin(tmp_path):
    host = FakeHostContext(
        data_root=tmp_path,
        config=FakeConfig(data={"sticker_manager": {"enabled": True}}),
    )
    plugin, _host = build_plugin(host)
    plugin._settings = StickerManagerSettings(enabled=True, send=SendSettings(), storage=StorageSettings())
    return plugin


async def _add(plugin, desc="", group="", zone=""):
    payload = PNG_BYTES + f"|{desc}|{group}|{zone}".encode("utf-8")
    return await plugin.add_entry(
        data_base64=base64.b64encode(payload).decode("ascii"),
        desc=desc,
        group=group,
        zone=zone,
    )


# ----------------------------------------------------------------------
# 迁移：v1 旧库 → 默认区
# ----------------------------------------------------------------------


class TestLegacyMigration:
    def test_v1_library_moves_into_default_zone(self, tmp_path, run_async):
        catalog = tmp_path / "catalog.json"
        catalog.write_text(
            json.dumps(
                {
                    "version": 1,
                    "stickers": [
                        {"id": "aaaaaaaaaa", "file": "aaaaaaaaaa.png", "desc": "旧图", "group": "旧组"},
                        {"id": "bbbbbbbbbb", "file": "bbbbbbbbbb.png", "desc": "散图"},
                    ],
                    "groups": {"旧组": "旧库的一句话"},
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        (tmp_path / "stickers").mkdir()
        (tmp_path / "stickers" / "aaaaaaaaaa.png").write_bytes(PNG_BYTES)
        (tmp_path / "stickers" / "bbbbbbbbbb.png").write_bytes(PNG_BYTES + b"x")
        lib = Library(tmp_path)
        result = lib.load()
        assert result.ok
        zones = lib.zones()
        assert len(zones) == 1 and zones[0]["active"] and zones[0]["total"] == 2
        assert lib.zone_of_group("旧组") == lib.active_zone()
        # 迁移当场补写盘：再读就是带区的形状（写侧版本号随版本涨，这里钉"有区三键"这件事）
        saved = json.loads(catalog.read_text(encoding="utf-8"))
        assert saved["version"] == 3 and saved["zones"] and saved["group_zone"]["旧组"]

    def test_empty_library_gets_default_zone(self, tmp_path, run_async):
        lib = Library(tmp_path)
        assert lib.load().ok
        assert len(lib.zones()) == 1 and lib.active_zone()


# ----------------------------------------------------------------------
# 生命周期入口
# ----------------------------------------------------------------------


class TestZoneLifecycle:
    def test_create_rename_desc_activate(self, tmp_path, run_async):
        plugin = _plugin(tmp_path)
        created = run_async(plugin.zone_create_entry(zone="战斗夜", desc="打 boss 前"))
        assert created.is_ok()
        zone_id = created.value["zone_id"]
        # 重名拒
        assert str(run_async(plugin.zone_create_entry(zone="战斗夜")).error) == "zone_exists"
        # 空名拒
        assert str(run_async(plugin.zone_create_entry(zone="  ")).error) == "zone_required"
        assert run_async(plugin.zone_rename_entry(zone_id=zone_id, zone="深夜开黑")).is_ok()
        assert run_async(plugin.zone_set_desc_entry(zone_id=zone_id, desc="")).is_ok()
        assert run_async(plugin.zone_activate_entry(zone_id=zone_id)).is_ok()
        payload = run_async(plugin.dashboard_context(**_ctx("K")))
        assert payload["active_zone"] == zone_id
        names = {z["name"] for z in payload["zones"]}
        assert names == {"自制区", "深夜开黑"}
        # 不存在的区处处吃 zone_not_found
        assert str(run_async(plugin.zone_rename_entry(zone_id="zzz", zone="X")).error) == "zone_not_found"
        assert str(run_async(plugin.zone_activate_entry(zone_id="zzz")).error) == "zone_not_found"

    def test_remove_cascades_and_protects_last(self, tmp_path, run_async):
        plugin = _plugin(tmp_path)
        run_async(plugin.zone_create_entry(zone="临时仓库"))
        payload = run_async(plugin.dashboard_context(**_ctx("K")))
        temp = [z for z in payload["zones"] if z["name"] == "临时仓库"][0]
        run_async(plugin.group_create_entry(group="仓内组", zone=temp["id"]))
        added = run_async(_add(plugin, desc="仓图", group="仓内组"))
        assert added.is_ok()
        # 只剩一个区时不许拆；拆非末区连带删图删组
        gone = run_async(plugin.zone_remove_entry(zone_id=temp["id"]))
        assert gone.is_ok() and gone.value["removed"] == 1
        payload = run_async(plugin.dashboard_context(**_ctx("K")))
        assert all(g["name"] != "仓内组" for g in payload["groups"])
        assert all(s["id"] != added.value["id"] for s in payload["stickers"])
        assert not list((tmp_path / "stickers").glob(f"{added.value['id']}.*"))
        # 只剩一个区时不许拆
        assert str(run_async(plugin.zone_remove_entry(zone_id=payload["active_zone"])).error) == "zone_last"

    def test_remove_active_zone_reactivates_survivor(self, tmp_path, run_async):
        plugin = _plugin(tmp_path)
        created = run_async(plugin.zone_create_entry(zone="将成为世界"))
        zone_id = created.value["zone_id"]
        run_async(plugin.zone_activate_entry(zone_id=zone_id))
        run_async(plugin.zone_remove_entry(zone_id=zone_id))
        payload = run_async(plugin.dashboard_context(**_ctx("K")))
        assert payload["active_zone"] == payload["zones"][0]["id"]
        assert payload["zones"][0]["name"] == "自制区"


# ----------------------------------------------------------------------
# 她的世界：只感知激活区
# ----------------------------------------------------------------------


class TestInactiveZoneInvisibleToHer:
    def test_catalog_and_send_scoped_to_active(self, tmp_path, run_async):
        plugin = _plugin(tmp_path)
        # 激活区一张
        here = run_async(_add(plugin, desc="区内图", group="这边"))
        assert here.is_ok()
        # 另一个区一张
        created = run_async(plugin.zone_create_entry(zone="隔壁"))
        there_zone = created.value["zone_id"]
        run_async(plugin.group_create_entry(group="那边", zone=there_zone))
        there = run_async(_add(plugin, desc="区外图", group="那边"))
        assert there.is_ok()
        catalog = run_async(plugin.tool_sticker_list())
        assert "这边" in catalog["catalog"] and "那边" not in catalog["catalog"]
        assert "区内图" in catalog["catalog"] and "区外图" not in catalog["catalog"]
        # 显式 id 指到非激活区 = 对她不存在
        miss = run_async(plugin.tool_sticker_send(sticker_id=there.value["id"], **_ctx("K")))
        assert miss["ok"] is False and miss["reason"] == "no_match"
        # group 参数同理（连候选清单都不配拥有）
        miss_group = run_async(plugin.tool_sticker_send(group="那边", **_ctx("K")))
        assert miss_group["ok"] is False and miss_group["reason"] == "group_not_found"
        # query 也搜不到区外图
        miss_query = run_async(plugin.tool_sticker_send(query="区外图", **_ctx("K")))
        assert miss_query["ok"] is False
        # 切区之后镜像翻转
        run_async(plugin.zone_activate_entry(zone_id=there_zone))
        after = run_async(plugin.tool_sticker_list())
        assert "那边" in after["catalog"] and "这边" not in after["catalog"]
        hit = run_async(plugin.tool_sticker_send(sticker_id=there.value["id"], **_ctx("K")))
        assert hit["ok"] is True

    def test_cross_zone_category_name_collides(self, tmp_path, run_async):
        plugin = _plugin(tmp_path)
        assert run_async(plugin.group_create_entry(group="晚安")).is_ok()
        created = run_async(plugin.zone_create_entry(zone="第二个区"))
        other = created.value["zone_id"]
        assert str(run_async(plugin.group_create_entry(group="晚安", zone=other)).error) == "group_exists"

    def test_ungrouped_sticker_lands_in_viewed_zone(self, tmp_path, run_async):
        plugin = _plugin(tmp_path)
        created = run_async(plugin.zone_create_entry(zone="收图区"))
        zone_id = created.value["zone_id"]
        added = run_async(_add(plugin, desc="裸图", zone=zone_id))
        assert added.is_ok()
        payload = run_async(plugin.dashboard_context(**_ctx("K")))
        row = [s for s in payload["stickers"] if s["id"] == added.value["id"]][0]
        assert row["zone"] == zone_id
        # 不指定 zone 的 add 落激活区
        fallback = run_async(_add(plugin, desc="兜底图"))
        zone_of_fallback = [
            s for s in run_async(plugin.dashboard_context(**_ctx("K")))["stickers"] if s["id"] == fallback.value["id"]
        ][0]["zone"]
        assert zone_of_fallback == payload["active_zone"]
