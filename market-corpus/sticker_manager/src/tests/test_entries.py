# pyright: reportMissingImports=false
# 独立仓无 sticker_manager 目录名，测试态由 conftest 的 importlib 别名接管（pytest 实测可解），同 __init__.py 的 SDK 导入先例
"""入口面测试：add/update/remove/send/list/preview/history/switch + 两个 llm_tool。

用真文件（tmp_path）+ 假宿主，走 handler 本体而不是内部服务，
钉的是"面板/模型看到的形状"。
"""

from __future__ import annotations

import base64
import hashlib
from dataclasses import replace

from conftest import GIF_BYTES, JPEG_BYTES, NOT_AN_IMAGE, PNG_BYTES, FakeConfig, FakeHostContext, build_plugin
from sticker_manager.core.configuration import SendSettings, StickerManagerSettings, StorageSettings


def _make(tmp_path, *, enabled=True):
    host = FakeHostContext(
        data_root=tmp_path,
        config=FakeConfig(data={"sticker_manager": {"enabled": enabled}}),
    )
    plugin, host = build_plugin(host)
    settings = StickerManagerSettings(enabled=enabled, send=SendSettings(), storage=StorageSettings())
    plugin._settings = settings
    return plugin, host


def _ctx(lanlan: str) -> dict:
    return {"_ctx": {"lanlan_name": lanlan}}


async def _add(plugin, desc="笑", tags="开心,猫", data=PNG_BYTES):
    return await plugin.add_entry(data_base64=base64.b64encode(data).decode("ascii"), desc=desc, tags=tags)


class TestAddEntry:
    def test_add_roundtrip(self, tmp_path, run_async):
        plugin, _host = _make(tmp_path)
        result = run_async(_add(plugin))
        assert result.is_ok()
        payload = result.value
        assert payload["note"] == "sticker_added"
        assert payload["desc"] == "笑"
        # 目录里能搜到，标签被拆开
        listed = run_async(plugin.list_entry(query="笑"))
        assert listed.value["count"] == 1
        assert listed.value["stickers"][0]["tags"] == ["开心", "猫"]

    def test_rejects_non_image(self, tmp_path, run_async):
        plugin, _host = _make(tmp_path)
        result = run_async(_add(plugin, data=NOT_AN_IMAGE))
        assert not result.is_ok()
        assert str(result.error) == "invalid_image"

    def test_desc_is_optional_now(self, tmp_path, run_async):
        # 轮 F：逐图描述不再是入库门槛（对齐参考系统：不写文本也能收，她靠分组说明选图）
        plugin, _host = _make(tmp_path)
        result = run_async(_add(plugin, desc="   "))
        assert result.is_ok()
        assert result.value["desc"] == ""
        # 可选不等于无尺：超限仍拦
        too_long = run_async(_add(plugin, desc="x" * 201))
        assert not too_long.is_ok()
        assert str(too_long.error) == "desc_too_long"

    def test_rejects_undecodable_base64(self, tmp_path, run_async):
        plugin, _host = _make(tmp_path)
        result = run_async(plugin.add_entry(data_base64="\x00not base64 at all!!", desc="坏", tags=""))
        assert not result.is_ok()
        assert str(result.error) == "image_undecodable"

    def test_management_works_while_disabled(self, tmp_path, run_async):
        # fail-closed 只管"发"，不管"藏"：开关关闭时收藏仍要能成功
        plugin, _host = _make(tmp_path, enabled=False)
        result = run_async(_add(plugin))
        assert result.is_ok()


class TestUpdateRemove:
    def test_update_desc_and_disabled(self, tmp_path, run_async):
        plugin, _host = _make(tmp_path)
        added = run_async(_add(plugin))
        sid = added.value["id"]
        updated = run_async(plugin.update_entry(id=sid, desc="大哭", disabled=True, **_ctx("K")))
        assert updated.is_ok()
        # 默认视图：禁用条目消失；include_disabled 能捞回并看到新描述
        assert run_async(plugin.list_entry()).value["stickers"] == []
        listed = run_async(plugin.list_entry(include_disabled=True))
        row = listed.value["stickers"][0]
        assert row["desc"] == "大哭" and row["disabled"] is True

    def test_update_missing(self, tmp_path, run_async):
        plugin, _host = _make(tmp_path)
        result = run_async(plugin.update_entry(id="ghost", desc="x"))
        assert not result.is_ok()
        assert str(result.error) == "sticker_not_found"

    def test_remove(self, tmp_path, run_async):
        plugin, _host = _make(tmp_path)
        sid = run_async(_add(plugin)).value["id"]
        assert run_async(plugin.remove_entry(id=sid)).is_ok()
        assert run_async(plugin.list_entry()).value["count"] == 0
        assert not (tmp_path / "library" / "stickers" / f"{sid}.png").exists()


class TestSendAndPreview:
    def test_panel_send_and_history(self, tmp_path, run_async):
        plugin, host = _make(tmp_path)
        sid = run_async(_add(plugin)).value["id"]
        result = run_async(plugin.send_entry(id=sid, **_ctx("K")))
        assert result.is_ok()
        (call,) = host.push.calls
        assert call["target_lanlan"] == "K"
        assert call["visibility"] == ["chat"] and call["ai_behavior"] == "read"
        history = run_async(plugin.history_entry(limit=10))
        assert history.value["usage"][0]["id"] == sid
        assert history.value["usage"][0]["source"] == "panel"

    def test_send_respects_cooldown(self, tmp_path, run_async):
        plugin, _host = _make(tmp_path)
        sid = run_async(_add(plugin)).value["id"]
        assert run_async(plugin.send_entry(id=sid, **_ctx("K"))).is_ok()
        second = run_async(plugin.send_entry(id=sid, **_ctx("K")))
        assert not second.is_ok()
        assert str(second.error) == "send_cooldown"

    def test_send_blocked_when_disabled(self, tmp_path, run_async):
        plugin, host = _make(tmp_path, enabled=False)
        sid = run_async(_add(plugin, data=PNG_BYTES)).value["id"]
        result = run_async(plugin.send_entry(id=sid, **_ctx("K")))
        assert not result.is_ok()
        assert str(result.error) == "not_enabled"
        assert host.push.calls == []

    def test_preview_missing_file(self, tmp_path, run_async):
        plugin, _host = _make(tmp_path)
        sid = run_async(_add(plugin)).value["id"]
        plugin._library.image_path(plugin._library.get(sid)).unlink()
        result = run_async(plugin.preview_entry(id=sid))
        assert not result.is_ok()
        assert str(result.error) == "sticker_file_missing"


class TestSwitchEntry:
    def test_switch_writes_config_and_reloads(self, tmp_path, run_async):
        plugin, host = _make(tmp_path, enabled=False)
        result = run_async(plugin.switch_entry(enabled=True))
        assert result.is_ok() and result.value["enabled"] is True
        assert host.config.writes == [("sticker_manager.enabled", True)]
        assert plugin._settings.enabled is True

    def test_switch_config_failure_reports(self, tmp_path, run_async):
        plugin, host = _make(tmp_path)
        host.config.set_error = RuntimeError("boom")
        result = run_async(plugin.switch_entry(enabled=False))
        assert not result.is_ok()
        assert str(result.error) == "config_unavailable"


class TestTools:
    def test_sticker_list_enabled(self, tmp_path, run_async):
        plugin, _host = _make(tmp_path)
        run_async(_add(plugin, desc="猫咪开心挥手", tags="开心"))
        out = run_async(plugin.tool_sticker_list(query="", **_ctx("K")))
        assert out["ok"] is True
        assert "猫咪开心挥手" in out["catalog"]

    def test_sticker_list_disabled_master_switch(self, tmp_path, run_async):
        plugin, _host = _make(tmp_path, enabled=False)
        out = run_async(plugin.tool_sticker_list())
        assert out["ok"] is False and out["reason"] == "not_enabled"
        assert "选图不进正文或括号动作" in out["hint"] and not out.get("catalog")

    def test_sticker_send_by_id(self, tmp_path, run_async):
        plugin, host = _make(tmp_path)
        sid = run_async(_add(plugin, desc="笑")).value["id"]
        out = run_async(plugin.tool_sticker_send(sticker_id=sid, **_ctx("K")))
        assert out["ok"] is True
        assert out["sent"] == sid
        assert host.push.calls

    def test_sticker_send_by_query(self, tmp_path, run_async):
        plugin, _host = _make(tmp_path)
        run_async(_add(plugin, desc="无语凝噎", tags="无语"))
        out = run_async(plugin.tool_sticker_send(query="无语", **_ctx("K")))
        assert out["ok"] is True

    def test_sticker_send_no_match(self, tmp_path, run_async):
        plugin, _host = _make(tmp_path)
        out = run_async(plugin.tool_sticker_send(query="不存在的", **_ctx("K")))
        assert out["ok"] is False
        assert out["reason"] == "no_match"

    def test_sticker_send_requires_id_or_query(self, tmp_path, run_async):
        # 轮 C 收紧：空手调用不再"顺手"发常货（旧行为：query 缺席=全量排序发第一）
        plugin, _host = _make(tmp_path)
        run_async(_add(plugin, desc="常货"))
        out = run_async(plugin.tool_sticker_send(**_ctx("K")))
        assert out["ok"] is False
        assert out["reason"] == "id_or_query_required"

    def test_sticker_send_tied_query_returns_candidates_not_a_send(self, tmp_path, run_async):
        # 头部并列（两张 desc 同词命中=同分同台账）：不拍板，回清单让她二次用 id 发。
        plugin, host = _make(tmp_path)
        first = run_async(_add(plugin, desc="开心挥手")).value["id"]
        second = run_async(_add(plugin, desc="开心鼓掌", data=JPEG_BYTES)).value["id"]
        out = run_async(plugin.tool_sticker_send(query="开心", **_ctx("K")))
        assert out["ok"] is True and out["sent"] == ""
        assert out["note"] == "multi_candidates" and out["count"] == 2
        assert f"[{first}]" in out["candidates"] and f"[{second}]" in out["candidates"]
        assert not host.push.calls  # 一发都没出
        follow = run_async(plugin.tool_sticker_send(sticker_id=second, **_ctx("K")))
        assert follow["ok"] is True and follow["sent"] == second

    def test_sticker_send_strict_best_still_requires_selection(self, tmp_path, run_async):
        plugin, host = _make(tmp_path)
        best = run_async(_add(plugin, desc="开心挥手")).value["id"]
        run_async(_add(plugin, desc="别的图", tags="比较开心", data=JPEG_BYTES))
        out = run_async(plugin.tool_sticker_send(query="开心", **_ctx("K")))
        assert out["ok"] is True and out["sent"] == ""
        assert best in out["candidate_ids"] and not host.push.calls

    def test_sticker_send_skips_disabled_pick(self, tmp_path, run_async):
        # 模型点名要一张被禁用的图：不直发，回退到搜索；搜索也不含禁用 → no_match
        plugin, _host = _make(tmp_path)
        sid = run_async(_add(plugin, desc="被禁的")).value["id"]
        run_async(plugin.update_entry(id=sid, disabled=True))
        out = run_async(plugin.tool_sticker_send(sticker_id=sid, **_ctx("K")))
        assert out["ok"] is False
        assert out["reason"] == "no_match"

    def test_failed_tool_send_enters_ledger(self, tmp_path, run_async):
        plugin, _host = _make(tmp_path)
        sid = run_async(_add(plugin)).value["id"]
        plugin._settings = replace(plugin._settings, send=SendSettings(cooldown_sec=3600.0))
        assert run_async(plugin.tool_sticker_send(sticker_id=sid, **_ctx("K")))["ok"] is True
        blocked = run_async(plugin.tool_sticker_send(sticker_id=sid, **_ctx("K")))
        assert blocked["ok"] is False
        assert blocked["reason"] == "send_cooldown"
        codes = [e.get("code") for e in plugin._library.read_usage() if not e.get("ok")]
        assert "send_cooldown" in codes


class TestDashboardContext:
    def test_shape(self, tmp_path, run_async):
        plugin, _host = _make(tmp_path)
        run_async(_add(plugin))
        payload = run_async(plugin.dashboard_context(**_ctx("K")))
        assert payload["enabled"] is True
        assert payload["lanlan"] == "K"
        assert payload["counts"]["total"] == 1
        assert payload["counts"]["enabled"] == 1
        assert payload["stickers"][0]["desc"] == "笑"
        assert "config" in payload and "error_code" not in payload

    def test_io_error_surfaces_as_code(self, tmp_path, run_async):
        plugin, _host = _make(tmp_path)
        (tmp_path / "library" / "catalog.json").parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / "library" / "catalog.json").write_text("{ broken", encoding="utf-8")
        payload = run_async(plugin.dashboard_context(**_ctx("K")))
        assert payload["error_code"] == "library_io_error"


class TestDedupAndRepair:
    def test_add_duplicate_returns_stable_code(self, tmp_path, run_async):
        plugin, _host = _make(tmp_path)
        assert run_async(_add(plugin)).is_ok()
        again = run_async(_add(plugin, desc="换个字也一样"))
        assert not again.is_ok()
        assert str(again.error) == "duplicate_image"

    def test_repair_entry_reports_counts(self, tmp_path, run_async):
        plugin, _host = _make(tmp_path)
        keep = run_async(_add(plugin)).value["id"]
        broken = run_async(_add(plugin, desc="坏", data=JPEG_BYTES)).value["id"]
        (tmp_path / "library" / "stickers" / f"{broken}.jpg").unlink()
        result = run_async(plugin.repair_entry())
        assert result.is_ok()
        payload = result.value
        assert payload["note"] == "library_repaired"
        assert payload["removed_entries"] == 1
        assert payload["purged_files"] == 0
        assert payload["backfilled_hashes"] == 0
        listed = run_async(plugin.list_entry())
        assert [s["id"] for s in listed.value["stickers"]] == [keep]

    def test_sticker_rows_carry_sha256(self, tmp_path, run_async):
        plugin, _host = _make(tmp_path)
        sid = run_async(_add(plugin)).value["id"]
        row = run_async(plugin.list_entry()).value["stickers"][0]
        assert row["id"] == sid
        assert row["sha256"] == hashlib.sha256(PNG_BYTES).hexdigest()


class TestInboxEntry:
    def test_import_inbox_counts_and_context_pending(self, tmp_path, run_async):
        plugin, _host = _make(tmp_path)
        inbox = tmp_path / "library" / "inbox"
        inbox.mkdir(parents=True)
        (inbox / "挥手_cat.png").write_bytes(PNG_BYTES)
        (inbox / "hi.gif").write_bytes(GIF_BYTES)
        (inbox / "hi_copy.gif").write_bytes(GIF_BYTES)  # 与上一张内容相同 → 重复
        pending = run_async(plugin.dashboard_context(**_ctx("K")))
        assert pending["inbox"]["pending"] == 3
        result = run_async(plugin.import_inbox_entry(tags="批量,导入"))
        assert result.is_ok()
        payload = result.value
        assert payload["note"] == "inbox_imported"
        assert payload["imported"] == 2
        assert payload["duplicates"] == 1
        assert payload["rejected"] == 0 and payload["failed"] == 0
        listed = run_async(plugin.list_entry())
        descs = {row["desc"] for row in listed.value["stickers"]}
        assert descs == {"挥手 cat", "hi"}
        assert all(row["tags"] == ["批量", "导入"] for row in listed.value["stickers"])
        after = run_async(plugin.dashboard_context(**_ctx("K")))
        assert after["inbox"]["pending"] == 0
        assert after["inbox"]["path"]

    def test_import_inbox_works_while_disabled(self, tmp_path, run_async):
        # fail-closed 只管"发"，收的通道（含收件箱）不受开关影响
        plugin, _host = _make(tmp_path, enabled=False)
        inbox = tmp_path / "library" / "inbox"
        inbox.mkdir(parents=True)
        (inbox / "hi.png").write_bytes(PNG_BYTES)
        result = run_async(plugin.import_inbox_entry())
        assert result.is_ok() and result.value["imported"] == 1


class TestPreviewChunks:
    """分段协议门：大图必须能被逐段拉全（躲宿主回包单帧上限）。"""

    def test_large_sticker_reassembles_via_chunks(self, tmp_path, run_async):
        plugin, _host = _make(tmp_path)
        big = bytes([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A]) + b"k" * (3 * 1024 * 1024 + 7)  # 跨两块
        sid = run_async(_add(plugin, data=big)).value["id"]
        parts: list[bytes] = []
        offset = 0
        for _ in range(8):
            result = run_async(plugin.preview_entry(id=sid, offset=offset))
            assert result.is_ok()
            payload = result.value
            chunk = base64.b64decode(payload["chunk_base64"])
            parts.append(chunk)
            if payload["done"]:
                assert chunk  # 收尾段不许空转
                break
            assert payload["next_offset"] == offset + len(chunk)
            offset = payload["next_offset"]
        else:
            raise AssertionError("chunk loop never finished")
        assert b"".join(parts) == big

    def test_offset_past_end_is_clean_done(self, tmp_path, run_async):
        plugin, _host = _make(tmp_path)
        sid = run_async(_add(plugin, data=PNG_BYTES)).value["id"]
        result = run_async(plugin.preview_entry(id=sid, offset=9999))
        assert result.is_ok()
        payload = result.value
        assert payload["done"] is True and payload["chunk_base64"] == ""

    def test_junk_offset_falls_back_to_zero(self, tmp_path, run_async):
        plugin, _host = _make(tmp_path)
        sid = run_async(_add(plugin, data=PNG_BYTES)).value["id"]
        for junk in (-5, "3", True, None):
            result = run_async(plugin.preview_entry(id=sid, offset=junk))
            assert result.is_ok() and result.value["offset"] == 0


class TestCaptionFields:
    """v0.3.0 轮 A：caption（梗义）/ visible_text（图内原文）的入口面契约。"""

    def test_add_carries_caption_and_visible_text(self, tmp_path, run_async):
        plugin, _host = _make(tmp_path)
        result = run_async(
            plugin.add_entry(
                data_base64=base64.b64encode(PNG_BYTES).decode("ascii"),
                desc="笑",
                caption="被催很久终于交差的得意",
                visible_text="就这？",
            )
        )
        assert result.is_ok()
        rows = run_async(plugin.list_entry()).value["stickers"]
        assert rows[0]["caption"] == "被催很久终于交差的得意"
        assert rows[0]["visible_text"] == "就这？"

    def test_add_rejects_oversized_caption(self, tmp_path, run_async):
        plugin, _host = _make(tmp_path)
        result = run_async(
            plugin.add_entry(
                data_base64=base64.b64encode(PNG_BYTES).decode("ascii"),
                desc="笑",
                caption="x" * 301,
            )
        )
        assert not result.is_ok() and str(result.error) == "caption_too_long"
        # 校验发生在入库前：坏请求不许留下半张图
        assert run_async(plugin.list_entry()).value["count"] == 0

    def test_add_rejects_oversized_visible_text(self, tmp_path, run_async):
        plugin, _host = _make(tmp_path)
        result = run_async(
            plugin.add_entry(
                data_base64=base64.b64encode(PNG_BYTES).decode("ascii"),
                desc="笑",
                visible_text="y" * 201,
            )
        )
        assert not result.is_ok() and str(result.error) == "visible_text_too_long"

    def test_update_desc_preserves_caption(self, tmp_path, run_async):
        # 防"手工枚举字段重建"整类回归：只改 desc，caption/sha256 必须原地保住
        plugin, _host = _make(tmp_path)
        sid = run_async(
            plugin.add_entry(
                data_base64=base64.b64encode(PNG_BYTES).decode("ascii"),
                desc="笑",
                caption="梗义",
            )
        ).value["id"]
        assert run_async(plugin.update_entry(id=sid, desc="大哭")).is_ok()
        rows = run_async(plugin.list_entry()).value["stickers"]
        assert rows[0]["desc"] == "大哭" and rows[0]["caption"] == "梗义"
        assert rows[0]["sha256"]

    def test_update_caption_empty_string_clears(self, tmp_path, run_async):
        plugin, _host = _make(tmp_path)
        sid = run_async(
            plugin.add_entry(
                data_base64=base64.b64encode(PNG_BYTES).decode("ascii"),
                desc="笑",
                caption="标错了的梗义",
            )
        ).value["id"]
        assert run_async(plugin.update_entry(id=sid, caption="")).is_ok()
        rows = run_async(plugin.list_entry()).value["stickers"]
        assert rows[0]["caption"] == ""

    def test_update_rejects_oversized_caption(self, tmp_path, run_async):
        plugin, _host = _make(tmp_path)
        sid = run_async(_add(plugin)).value["id"]
        result = run_async(plugin.update_entry(id=sid, caption="x" * 301))
        assert not result.is_ok() and str(result.error) == "caption_too_long"
