# pyright: reportMissingImports=false
# 独立仓无 sticker_manager 目录名，测试态由 conftest 的 importlib 别名接管（pytest 实测可解），同 __init__.py 的 SDK 导入先例
"""轮 F（v0.7.0 轻打标）：分组描述、两级目录、组内选图、批量整理、manifest v3。

对齐参考系统的手感：描述挂分类（一句话即目录正文）、逐图零必填、
"组调性对、具体哪张你定"→组内随机；整理靠批量而不是逐张开弹窗。
"""

from __future__ import annotations

import base64
import io
import json
import zipfile

import pytest
from conftest import PNG_BYTES, FakeConfig, FakeHostContext, build_plugin
from sticker_manager.core.catalog import Sticker, format_group_overview
from sticker_manager.core.configuration import SendSettings, StickerManagerSettings, StorageSettings
from sticker_manager.services.library import Library


def _ctx(lanlan: str) -> dict:
    return {"_ctx": {"lanlan_name": lanlan}}


def _plugin(tmp_path, *, send: SendSettings | None = None):
    host = FakeHostContext(
        data_root=tmp_path,
        config=FakeConfig(data={"sticker_manager": {"enabled": True}}),
    )
    plugin, _host = build_plugin(host)
    plugin._settings = StickerManagerSettings(enabled=True, send=send or SendSettings(), storage=StorageSettings())
    return plugin


async def _add(plugin, desc="", tags="", group="", caption=""):
    # 字节里拼入字段内容：不同参数组合必不撞 sha256（只拼长度会同长碰撞 duplicate_image）
    payload = PNG_BYTES + f"|{desc}|{tags}|{group}|{caption}".encode("utf-8")
    return await plugin.add_entry(
        data_base64=base64.b64encode(payload).decode("ascii"),
        desc=desc,
        tags=tags,
        group=group,
        caption=caption,
    )


# ----------------------------------------------------------------------
# core：catalog_body 回落链
# ----------------------------------------------------------------------


class TestCatalogBodyChain:
    def test_precedence(self):
        base = dict(id="a", file="a.png")
        assert Sticker(**base, desc="", caption="梗").catalog_body() == "梗"
        assert Sticker(**base, desc="述", caption="").catalog_body({"G": "组话"}) == "述"
        assert Sticker(**base, desc="", caption="", group="G").catalog_body({"G": "组话"}) == "组话"
        assert Sticker(**base, desc="", caption="", group="G").catalog_body({}) == "套图「G」里的一张"
        assert Sticker(**base, desc="", caption="").catalog_body() == "未标注"

    def test_group_overview_shape(self):
        a = Sticker(id="a", file="a.png", desc="甲", group="常用")
        b = Sticker(id="b", file="b.png", desc="乙", group="常用")
        c = Sticker(id="c", file="c.png", desc="丙")
        d = Sticker(id="d", file="d.png", desc="丁", group="禁用组", disabled=True)
        text = format_group_overview([a, b, c, d], {"常用": "开心时", "禁用组": "别看"})
        assert text.splitlines() == ["・常用（2 张） — 开心时", "・未分组（1 张）"]


# ----------------------------------------------------------------------
# library：分组说明持久
# ----------------------------------------------------------------------


class TestLibraryGroups:
    def test_set_persist_clear(self, tmp_path):
        """轮 I 语义：清空说明≠删掉分类——`{名: ""}` = “在册但没写说明”。"""
        lib = Library(tmp_path / "lib")
        lib.load()
        ok, err = lib.set_group_desc(" 猫猫日常 ", " 被夸时用 ")
        assert ok and not err
        assert lib.group_descs() == {"猫猫日常": "被夸时用"}
        reloaded = Library(tmp_path / "lib")
        reloaded.load()
        assert reloaded.group_descs() == {"猫猫日常": "被夸时用"}
        ok, _ = lib.set_group_desc("猫猫日常", "")
        assert ok
        assert lib.group_descs() == {"猫猫日常": ""}
        # 说明清空后的分类仍然在册，并且活过一次重载
        assert "猫猫日常" in lib.group_names()
        again = Library(tmp_path / "lib")
        again.load()
        assert again.group_descs() == {"猫猫日常": ""}
        assert "猫猫日常" in again.group_names()

    def test_clamps_and_guards(self, tmp_path):
        lib = Library(tmp_path / "lib")
        lib.load()
        assert lib.set_group_desc("", "没名字") == (False, "group_required")
        ok, _ = lib.set_group_desc("G", "x" * 400)
        assert ok and len(lib.group_descs()["G"]) == 300  # 入口层拦，库层钳位兜底

    def test_poisoned_groups_section(self, tmp_path):
        root = tmp_path / "lib"
        root.mkdir(parents=True)
        (root / "catalog.json").write_text(
            json.dumps(
                {
                    "version": 1,
                    "stickers": [],
                    "groups": {"好组": "ok", "空组": "   ", "坏值": 42, "": "无名"},
                }
            ),
            encoding="utf-8",
        )
        lib = Library(root)
        result = lib.load(force=True)
        assert result.ok
        # 轮 I：空白说明洗成空串后**保留键**（= 一个没写说明的分类）；
        # 非字符串值与无名键仍是垃圾，照旧丢。
        assert lib.group_descs() == {"好组": "ok", "空组": ""}


class TestCategoryLifecycle:
    """轮 I（分类优先）：先立分类→往分类里收图→整间拆掉（连带删图，主人拍板 1C）。"""

    def test_create_empty_category_is_registered(self, tmp_path):
        lib = Library(tmp_path / "lib")
        lib.load()
        assert lib.create_group(" 晚安 ", " 凌晨两点说累了时用 ") == (True, "")
        assert lib.group_descs() == {"晚安": "凌晨两点说累了时用"}
        assert "晚安" in lib.group_names()
        # 零张图的分类活过一次重载（面板建完刷新不丢 = 不是“建完就消失”）
        again = Library(tmp_path / "lib")
        again.load()
        assert again.group_descs() == {"晚安": "凌晨两点说累了时用"}

    def test_create_guards(self, tmp_path):
        lib = Library(tmp_path / "lib")
        lib.load()
        assert lib.create_group("  ", "没名字") == (False, "group_required")
        assert lib.create_group("G", "一次") == (True, "")
        assert lib.create_group("G", "两次") == (False, "group_exists")
        assert lib.group_descs() == {"G": "一次"}  # 撞名不许静默改写主人的话

    def test_create_collides_with_implicit_group(self, tmp_path, run_async):
        """图已经住着的组名也算“在册”：再建一次是错事，该去「编辑说明」。"""
        plugin = _plugin(tmp_path)
        run_async(_add(plugin, desc="甲", group="猫猫日常"))
        assert str(run_async(plugin.group_create_entry(group="猫猫日常", desc="x")).error) == "group_exists"
        # 同一名字补说明：合法且立刻生效
        assert run_async(plugin.group_set_desc_entry(group="猫猫日常", desc="想被摸摸时用")).is_ok()

    def test_entry_shapes(self, tmp_path, run_async):
        plugin = _plugin(tmp_path)
        assert str(run_async(plugin.group_create_entry()).error) == "group_required"
        assert str(run_async(plugin.group_create_entry(group="新组", desc="x" * 301)).error) == "group_desc_too_long"
        created = run_async(plugin.group_create_entry(group="新组", desc=""))
        assert created.is_ok() and created.value["note"] == "group_created"
        payload = run_async(plugin.dashboard_context(**_ctx("K")))
        # 空分类在面板 state 里带 count=0（v0.8.0“空区块不出现”在本轮被反转：
        # 人不看得到就永远传不进图），但下面的“她的视角”门证明它不露脸。
        # J-1：groups 行多了 zone 字段（面板按它分区渲染）；新分类落在激活区。
        assert {"name": "新组", "count": 0, "desc": "", "zone": payload["active_zone"]} in payload["groups"]

    def test_remove_empty_category(self, tmp_path, run_async):
        plugin = _plugin(tmp_path)
        run_async(plugin.group_create_entry(group="拆得掉的安静"))
        gone = run_async(plugin.group_remove_entry(group="拆得掉的安静"))
        assert gone.is_ok() and gone.value["note"] == "group_removed" and gone.value["removed"] == 0
        assert "拆得掉的安静" not in plugin._library.group_names()
        assert str(run_async(plugin.group_remove_entry(group="拆得掉的安静")).error) == "group_not_found"

    def test_remove_takes_its_stickers_and_files(self, tmp_path, run_async):
        """1C 拍板：删分类 = 连带删图。目录、文件两个面都得真干净。"""
        plugin = _plugin(tmp_path)
        run_async(_add(plugin, desc="甲", group="拆间"))
        run_async(_add(plugin, desc="乙", group="拆间"))
        run_async(_add(plugin, desc="丙", group="留下"))
        lib = plugin._library
        files_before = sorted(p.name for p in lib.stickers_dir.iterdir())
        assert len(files_before) == 3
        gone = run_async(plugin.group_remove_entry(group="拆间"))
        assert gone.is_ok() and gone.value["removed"] == 2
        left = run_async(plugin.list_entry()).value["stickers"]
        assert [r["desc"] for r in left] == ["丙"]
        files_after = sorted(p.name for p in lib.stickers_dir.iterdir())
        assert len(files_before) - len(files_after) == 2  # 孤儿文件不能留在盘上
        assert "拆间" not in lib.group_names()
        assert "留下" in lib.group_names()

    def test_empty_category_never_reaches_her(self, tmp_path, run_async):
        """防回归门（本轮核心不变量）：空分类是管理概念，不是发送目标。

        她看到的分类目录、组名候选、发图目标全部只从“有图的”算：
        否则会出现“她选中一个空分类→永远发不出”的鬼打墙。
        """
        plugin = _plugin(tmp_path, send=SendSettings(cooldown_sec=0.0, recent_dedup_count=0))
        run_async(plugin.group_create_entry(group="永远空的", desc="这句话不该出现在她眼前"))
        run_async(_add(plugin, desc="甲", group="有货的"))
        listed = run_async(plugin.tool_sticker_list(**_ctx("K")))
        assert listed["ok"] and listed["catalog"]
        assert "永远空的" not in listed["catalog"]
        assert "有货的" in listed["catalog"]
        sent = run_async(plugin.tool_sticker_send(group="永远空的", **_ctx("K")))
        assert sent["ok"] is False and sent["reason"] == "group_not_found"
        assert "永远空的" not in sent["hint"]


# ----------------------------------------------------------------------
# 入口面
# ----------------------------------------------------------------------


class TestGroupDescEntry:
    def test_roundtrip_and_dashboard(self, tmp_path, run_async):
        plugin = _plugin(tmp_path)
        added = run_async(_add(plugin, desc="图一", group="猫猫日常"))
        assert added.is_ok()
        result = run_async(plugin.group_set_desc_entry(group="猫猫日常", desc="被夸/撒娇时用"))
        assert result.is_ok()
        assert result.value["note"] == "group_desc_set"
        payload = run_async(plugin.dashboard_context(**_ctx("K")))
        assert payload["groups"] == [
            {"name": "猫猫日常", "count": 1, "desc": "被夸/撒娇时用", "zone": payload["active_zone"]}
        ]
        # 空串=清除，但组还在（有图在用）
        cleared = run_async(plugin.group_set_desc_entry(group="猫猫日常", desc="  "))
        assert cleared.is_ok() and cleared.value["cleared"] is True

    def test_guards(self, tmp_path, run_async):
        plugin = _plugin(tmp_path)
        assert str(run_async(plugin.group_set_desc_entry()).error) == "group_required"
        assert str(run_async(plugin.group_set_desc_entry(group="不存在", desc="x")).error) == "group_not_found"
        run_async(_add(plugin, desc="图", group="G"))
        too_long = "x" * 301
        assert str(run_async(plugin.group_set_desc_entry(group="G", desc=too_long)).error) == "group_desc_too_long"


class TestBatchEntries:
    def _ids(self, plugin, run_async):
        listed = run_async(plugin.list_entry())
        return [row["id"] for row in listed.value["stickers"]]

    def test_batch_update_ops(self, tmp_path, run_async):
        plugin = _plugin(tmp_path)
        for i in range(3):
            run_async(_add(plugin, desc=f"图{i}", tags="旧"))
        ids = self._ids(plugin, run_async)
        assert run_async(plugin.batch_update_entry(ids=ids, tags_add="甲,乙")).value["updated"] == 3
        listed = run_async(plugin.list_entry())
        for row in listed.value["stickers"]:
            assert set(["旧", "甲", "乙"]) <= set(row["tags"])
        assert run_async(plugin.batch_update_entry(ids=ids, tags_remove="旧")).value["updated"] == 3
        listed = run_async(plugin.list_entry())
        assert all("旧" not in row["tags"] for row in listed.value["stickers"])
        assert run_async(plugin.batch_update_entry(ids=ids, group="一批", disabled=True)).value["updated"] == 3
        listed = run_async(plugin.list_entry(include_disabled=True))
        assert all(row["group"] == "一批" and row["disabled"] for row in listed.value["stickers"])

    def test_batch_guards(self, tmp_path, run_async):
        plugin = _plugin(tmp_path)
        assert str(run_async(plugin.batch_update_entry(ids=[])).error) == "batch_empty"
        assert str(run_async(plugin.batch_update_entry(ids=["a"])).error) == "batch_noop"
        result = run_async(plugin.batch_update_entry(ids=["幽灵"], tags_add="x"))
        assert result.is_ok()
        assert result.value["updated"] == 0 and result.value["missing"] == ["幽灵"]
        assert str(run_async(plugin.batch_remove_entry(ids=[])).error) == "batch_empty"

    @pytest.mark.parametrize("patch", [
        {"tags_add": ""}, {"tags_remove": "， 、 "}, {"tags_add": ["", " "], "tags_remove": []},
        {"tags_add": "x" * 25},
    ])
    def test_empty_tag_operations_do_not_write_or_report_updates(self, tmp_path, run_async, patch):
        plugin = _plugin(tmp_path)
        run_async(_add(plugin, desc="留", tags="旧"))
        ids = self._ids(plugin, run_async)
        before = plugin._library.catalog_path.read_bytes()
        result = run_async(plugin.batch_update_entry(ids=ids, **patch))
        assert not result.is_ok() and str(result.error) == "batch_noop"
        assert plugin._library.catalog_path.read_bytes() == before

    def test_batch_remove(self, tmp_path, run_async):
        plugin = _plugin(tmp_path)
        run_async(_add(plugin, desc="留"))
        run_async(_add(plugin, desc="删A"))
        run_async(_add(plugin, desc="删B"))
        ids = self._ids(plugin, run_async)
        result = run_async(plugin.batch_remove_entry(ids=[ids[0], ids[1], "幽灵"]))
        assert result.value["removed"] == 2 and result.value["missing"] == ["幽灵"]
        assert run_async(plugin.list_entry()).value["count"] == 1


# ----------------------------------------------------------------------
# 工具面：组内选图与两级目录
# ----------------------------------------------------------------------


class TestSendGroup:
    def _fill(self, plugin, run_async):
        run_async(_add(plugin, desc="甲", group="happy组"))
        run_async(_add(plugin, desc="乙", group="happy组"))

    def test_exact_group_requires_per_image_choice(self, tmp_path, run_async):
        plugin = _plugin(tmp_path, send=SendSettings(cooldown_sec=0.0, recent_dedup_count=0))
        self._fill(plugin, run_async)
        out = run_async(plugin.tool_sticker_send(group="happy组", **_ctx("K")))
        assert out["ok"] and out["sent"] == ""
        listed = run_async(plugin.list_entry())
        group_ids = {r["id"] for r in listed.value["stickers"] if r["group"] == "happy组"}
        assert set(out["candidate_ids"]) == group_ids and len(group_ids) == 2
        assert not plugin._library.read_usage()
        out = run_async(plugin.tool_sticker_send(sticker_id=out["candidate_ids"][-1], **_ctx("K")))
        assert out["sent"] in group_ids
        listed = run_async(plugin.list_entry())
        touched = [r for r in listed.value["stickers"] if r["id"] == out["sent"]][0]
        assert touched["use_count"] == 1

    def test_recent_excluded_then_repeat(self, tmp_path, run_async):
        plugin = _plugin(tmp_path, send=SendSettings(cooldown_sec=0.0, recent_dedup_count=5))
        self._fill(plugin, run_async)
        first = run_async(plugin.tool_sticker_send(group="happy组", **_ctx("K")))
        chosen = first["candidate_ids"][0]
        first = run_async(plugin.tool_sticker_send(sticker_id=chosen, **_ctx("K")))
        assert first["sent"] == chosen
        second = run_async(plugin.tool_sticker_send(group="happy组", **_ctx("K")))
        assert second["ok"] and second["sent"] == "" and second["count"] == 1
        assert chosen not in second["candidate_ids"]
        run_async(plugin.tool_sticker_send(sticker_id=second["candidate_ids"][0], **_ctx("K")))
        third = run_async(plugin.tool_sticker_send(group="happy组", **_ctx("K")))
        assert third["ok"] is False and third["reason"] == "recent_repeat"
        forced = run_async(plugin.tool_sticker_send(group="happy组", force=True, **_ctx("K")))
        assert forced["ok"] is True and forced["sent"] == "" and forced["count"] == 2
        assert run_async(plugin.tool_sticker_send(sticker_id=chosen, force=True, **_ctx("K")))["sent"] == chosen

    def test_group_errors_are_honest(self, tmp_path, run_async):
        plugin = _plugin(tmp_path, send=SendSettings(cooldown_sec=0.0))
        run_async(_add(plugin, desc="甲", group="猫猫日常A"))
        run_async(_add(plugin, desc="乙", group="猫猫日常B"))
        missing = run_async(plugin.tool_sticker_send(group="没有这组", **_ctx("K")))
        assert missing["ok"] is False and missing["reason"] == "group_not_found"
        assert missing["groups"] == ["猫猫日常A", "猫猫日常B"]
        ambiguous = run_async(plugin.tool_sticker_send(group="猫猫日常", **_ctx("K")))
        assert ambiguous["ok"] is True and ambiguous["note"] == "group_candidates"
        assert "猫猫日常A" in ambiguous["candidates"]

    def test_id_outranks_group(self, tmp_path, run_async):
        plugin = _plugin(tmp_path, send=SendSettings(cooldown_sec=0.0))
        run_async(_add(plugin, desc="甲", group="G"))
        other = run_async(_add(plugin, desc="乙", group="H"))
        target_id = other.value["id"]
        out = run_async(plugin.tool_sticker_send(sticker_id=target_id, group="G", **_ctx("K")))
        assert out["ok"] and out["sent"] == target_id


class TestListTwoTier:
    def test_no_query_shows_overview_first(self, tmp_path, run_async):
        plugin = _plugin(tmp_path)
        run_async(_add(plugin, desc="图一", group="常用"))
        run_async(plugin.group_set_desc_entry(group="常用", desc="开心时"))
        out = run_async(plugin.tool_sticker_list())
        assert out["ok"] and out["catalog"].startswith("【套图分类】")
        assert "・常用（1 张） — 开心时" in out["catalog"] and "【条目】" in out["catalog"]

    def test_group_scoped_listing(self, tmp_path, run_async):
        plugin = _plugin(tmp_path)
        run_async(_add(plugin, desc="甲", group="A组"))
        run_async(_add(plugin, desc="乙", group="B组"))
        out = run_async(plugin.tool_sticker_list(group="A组"))
        assert out["ok"] and "甲" in out["catalog"] and "乙" not in out["catalog"]
        none = run_async(plugin.tool_sticker_list(group="不存在"))
        assert none["ok"] and none["count"] == 0


# ----------------------------------------------------------------------
# manifest v3：分组说明随包迁移（只补缺不覆盖）
# ----------------------------------------------------------------------


class TestManifestV3Groups:
    def test_export_import_roundtrip(self, tmp_path, run_async):
        src = _plugin(tmp_path / "src")
        run_async(_add(src, desc="图一", group="猫猫日常"))
        run_async(src.group_set_desc_entry(group="猫猫日常", desc="被夸时用"))
        packed = run_async(src.export_pack_entry())
        assert packed.is_ok()
        zip_bytes = open(packed.value["file"], "rb").read()
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
        assert manifest["version"] == 3
        assert manifest["groups"] == [{"name": "猫猫日常", "desc": "被夸时用"}]

        dst_root = tmp_path / "dst"
        dst = _plugin(dst_root)
        inbox = dst._library.inbox_dir
        inbox.mkdir(parents=True, exist_ok=True)
        (inbox / "pack.zip").write_bytes(zip_bytes)
        ingested = run_async(dst.import_inbox_entry())
        assert ingested.is_ok() and ingested.value["imported"] == 1
        assert dst._library.group_descs() == {"猫猫日常": "被夸时用"}
        # 只补缺不覆盖：本地已有组话时，包里的不能消音
        run_async(dst.group_set_desc_entry(group="猫猫日常", desc="我自己的写法"))
        (inbox / "pack2.zip").write_bytes(zip_bytes)
        run_async(dst.import_inbox_entry())
        assert dst._library.group_descs() == {"猫猫日常": "我自己的写法"}
