# pyright: reportMissingImports=false
# 独立仓无 sticker_manager 目录名，测试态由 conftest 的 importlib 别名接管（pytest 实测可解），同 __init__.py 的 SDK 导入先例
"""v0.2.0 入口面增量：group 贯通、export_pack、import_inbox 认包、awareness_now、面板上下文。

与 test_entries 同风格：走 handler 本体，钉"面板/模型看到的形状"。
"""

from __future__ import annotations

import base64
import io
import zipfile

from conftest import (
    PNG_BYTES,
    FakeBus,
    FakeConfig,
    FakeHostContext,
    build_plugin,
    conversation_record,
)
from sticker_manager.core.configuration import (
    AwarenessSettings,
    SendSettings,
    StickerManagerSettings,
    StorageSettings,
)


def _make(tmp_path, *, enabled=True, records=None):
    host = FakeHostContext(
        data_root=tmp_path,
        config=FakeConfig(data={"sticker_manager": {"enabled": enabled}}),
        bus=FakeBus(list(records or [])),
    )
    plugin, host = build_plugin(host)
    plugin._settings = StickerManagerSettings(
        enabled=enabled,
        send=SendSettings(),
        storage=StorageSettings(),
        awareness=AwarenessSettings(),
    )
    return plugin, host


def _ctx(lanlan: str) -> dict:
    return {"_ctx": {"lanlan_name": lanlan}}


async def _add(plugin, desc="笑", group="", data=PNG_BYTES):
    return await plugin.add_entry(data_base64=base64.b64encode(data).decode("ascii"), desc=desc, tags="", group=group)


class TestGroupThroughEntry:
    def test_add_accepts_and_normalizes_group(self, tmp_path, run_async):
        plugin, _host = _make(tmp_path)
        result = run_async(_add(plugin, desc="猫猫挥手", group="  猫猫日常  "))
        assert result.is_ok()
        row = run_async(plugin.list_entry()).value["stickers"][0]
        assert row["group"] == "猫猫日常"  # 两端空白被收掉

    def test_update_sets_and_clears_group(self, tmp_path, run_async):
        plugin, _host = _make(tmp_path)
        sid = run_async(_add(plugin, group="猫猫日常")).value["id"]
        cleared = run_async(plugin.update_entry(id=sid, group="   ", **_ctx("K")))
        assert cleared.is_ok()
        assert run_async(plugin.list_entry()).value["stickers"][0]["group"] == ""
        # None（参数缺席）= 不改：与 tags 的"留空=不改"区分开
        run_async(plugin.update_entry(id=sid, desc="换描述", **_ctx("K")))
        assert run_async(plugin.list_entry()).value["stickers"][0]["group"] == ""

    def test_search_hits_group(self, tmp_path, run_async):
        plugin, _host = _make(tmp_path)
        run_async(_add(plugin, desc="图一", group="猫猫日常"))
        run_async(_add(plugin, desc="图二", group="狗狗日常"))
        rows = run_async(plugin.list_entry(query="猫猫日常")).value["stickers"]
        assert len(rows) == 1 and rows[0]["desc"] == "图一"

    def test_tool_catalog_line_carries_group(self, tmp_path, run_async):
        plugin, _host = _make(tmp_path)
        run_async(_add(plugin, desc="图一", group="猫猫日常"))
        listed = run_async(plugin.tool_sticker_list(**_ctx("K")))
        assert listed["ok"] is True
        assert "套图：猫猫日常" in listed["catalog"]

    def test_update_keeps_sha256_regression(self, tmp_path, run_async):
        # v0.1.1 的 lazy 回填掩盖了它：旧版 update 重建 Sticker 时丢了 sha256。
        plugin, _host = _make(tmp_path)
        sid = run_async(_add(plugin)).value["id"]
        run_async(plugin.update_entry(id=sid, desc="改过的", **_ctx("K")))
        row = run_async(plugin.list_entry()).value["stickers"][0]
        assert row["sha256"], "编辑不得丢内容指纹"


class TestExportEntry:
    def test_export_then_import_via_inbox(self, tmp_path, run_async):
        plugin, _host = _make(tmp_path)
        run_async(_add(plugin, desc="猫猫挥手", group="猫猫日常"))
        exported = run_async(plugin.export_pack_entry())
        assert exported.is_ok()
        assert exported.value["exported"] == 1
        assert exported.value["file"].endswith(".zip")
        # 包能被收件箱通道原样吃回来（去重挡住原图，计重复）
        inbox = plugin._library.inbox_dir
        inbox.mkdir(parents=True, exist_ok=True)
        (inbox / "roundtrip.zip").write_bytes(open(exported.value["file"], "rb").read())
        imported = run_async(plugin.import_inbox_entry())
        assert imported.is_ok()
        assert imported.value["duplicates"] == 1 and imported.value["imported"] == 0

    def test_export_empty_library_is_err(self, tmp_path, run_async):
        plugin, _host = _make(tmp_path)
        result = run_async(plugin.export_pack_entry())
        assert not result.is_ok()
        assert str(result.error) == "library_empty"


class TestInboxZipEntry:
    def test_zip_group_param_applies_to_bare_pack(self, tmp_path, run_async):
        plugin, _host = _make(tmp_path)
        inbox = plugin._library.inbox_dir
        inbox.mkdir(parents=True, exist_ok=True)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as pack:
            pack.writestr("挥手.png", PNG_BYTES)
        (inbox / "bare.zip").write_bytes(buf.getvalue())
        result = run_async(plugin.import_inbox_entry(tags="外来,批量", group="外来包"))
        assert result.is_ok()
        assert result.value["imported"] == 1
        row = run_async(plugin.list_entry()).value["stickers"][0]
        assert row["group"] == "外来包"
        assert row["tags"] == ["外来", "批量"]


class TestAwarenessNowEntry:
    def test_ok_path_pushes_silent_hint(self, tmp_path, run_async):
        plugin, host = _make(tmp_path, records=[conversation_record("c", 10.0, "K")])
        run_async(_add(plugin))
        result = run_async(plugin.awareness_now_entry(**_ctx("K")))
        assert result.is_ok()
        assert result.value["status"] == "injected"
        assert host.push.calls[0]["visibility"] == []

    def test_disabled_master_switch_reports_stable_code(self, tmp_path, run_async):
        plugin, _host = _make(tmp_path, enabled=False)
        run_async(_add(plugin))
        result = run_async(plugin.awareness_now_entry(**_ctx("K")))
        assert not result.is_ok()
        assert str(result.error) == "awareness_disabled"

    def test_no_target_reports_stable_code(self, tmp_path, run_async):
        plugin, _host = _make(tmp_path)
        run_async(_add(plugin))
        result = run_async(plugin.awareness_now_entry())  # 无 _ctx、无会话记录
        assert not result.is_ok()
        assert str(result.error) == "awareness_no_target"

    def test_empty_library_reports_stable_code(self, tmp_path, run_async):
        plugin, _host = _make(tmp_path, records=[conversation_record("c", 10.0, "K")])
        result = run_async(plugin.awareness_now_entry(**_ctx("K")))
        assert not result.is_ok()
        assert str(result.error) == "awareness_empty_library"


class TestDashboardPayload:
    def test_v020_fields_present(self, tmp_path, run_async):
        plugin, _host = _make(tmp_path, records=[conversation_record("c", 10.0, "K")])
        run_async(_add(plugin, desc="图一", group="猫猫日常"))
        payload = run_async(plugin.dashboard_context(**_ctx("K")))
        assert payload["counts"]["groups"] == 1
        # 轮 F：groups 从名字列表升成对象（带张数与分组说明）——面板 chips 直接用它；
        # J-1 再加 zone（面板按区渲染 tab）。
        assert payload["groups"] == [{"name": "猫猫日常", "count": 1, "desc": "", "zone": payload["active_zone"]}]
        assert payload["stickers"][0]["group"] == "猫猫日常"
        assert payload["stickers"][0]["zone"] == payload["active_zone"]
        assert [z["id"] for z in payload["zones"]] == [payload["active_zone"]]
        assert payload["zones"][0]["active"] is True and payload["zones"][0]["total"] == 1
        assert payload["awareness"]["status"] in {"", "injected", "waiting", "disabled", "no_target"}
        assert payload["config"]["awareness_enabled"] is True
        assert payload["config"]["awareness_interval_sec"] == 3600.0
