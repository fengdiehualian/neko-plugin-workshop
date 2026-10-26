# pyright: reportMissingImports=false
# 独立仓无 sticker_manager 目录名，测试态由 conftest 的 importlib 别名接管（pytest 实测可解），
# 静态侧不可见；同 __init__.py 的 SDK 导入先例，文件级 pragma 免逐行尾注释的版本归属分歧
"""zip 直传会话（v0.6.0 面板选择文件导入）：services 层规则 + entry 面契约。

钉的行为：
- 只认 .zip；带目录形状（/ 或 \\）的名字直接拒、不静默剥皮，绝对路径/点开头同拒。
- seq 必须从 0 连续；乱序作废整个会话（宁可重选文件，不静默拼出坏 zip）。
- 单块与总量上限；写失败作废。
- finish 走 import_pack 同一把尺；无论成败暂存体都不留（会话是一次性的）。
- 空会话 finish 回 upload_empty。
"""

from __future__ import annotations

import base64
import io
import json
import zipfile
from pathlib import Path

from conftest import PNG_BYTES, FakeConfig, FakeHostContext, build_plugin
from sticker_manager.core.catalog import UPLOAD_CHUNK_BYTES, UPLOAD_MAX_TOTAL_BYTES
from sticker_manager.services.library import Library


def _make_pack(entries: dict[str, bytes], *, manifest: dict | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in entries.items():
            zf.writestr(f"stickers/{name}", data)
        if manifest is not None:
            zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False))
    return buf.getvalue()


def _drain(lib: Library, sid: str, blob: bytes, *, chunk: int = UPLOAD_CHUNK_BYTES) -> None:
    for seq, offset in enumerate(range(0, len(blob), chunk)):
        error = lib.upload_append(sid, seq, blob[offset : offset + chunk])
        assert error == "", f"append seq={seq} failed: {error}"


class TestUploadSession:
    def test_rejects_non_zip_names(self, tmp_path):
        lib = Library(tmp_path / "lib")
        for bad in ("pack.rar", "evil.png", "", "../x.zip", "  ", "/abs/pack.zip", ".hidden.zip"):
            sid, error = lib.upload_start(bad)
            assert sid == "" and error == "upload_not_zip", bad

    def test_roundtrip_manifest_pack(self, tmp_path):
        lib = Library(tmp_path / "lib")
        lib.load()
        blob = _make_pack(
            {"a.png": PNG_BYTES, "b.png": PNG_BYTES + b"\x01"},
            manifest={
                "version": 2,
                "app": "sticker_manager",
                "stickers": [
                    {
                        "file": "a.png",
                        "desc": "甲",
                        "tags": ["测试"],
                        "group": "上传组",
                        "caption": "梗义甲",
                        "visible_text": "",
                    },
                    {"file": "b.png", "desc": "乙", "tags": [], "group": "", "caption": "", "visible_text": ""},
                ],
            },
        )
        sid, error = lib.upload_start("pack.zip", size=len(blob))
        assert sid and not error
        _drain(lib, sid, blob, chunk=997)  # 故意小块多块
        summary, error = lib.upload_finish(sid)
        assert not error
        assert summary == {"imported": 2, "duplicates": 0, "rejected": 0, "failed": 0}
        hit = [s for s in lib.all() if s.desc == "甲"]
        assert hit and hit[0].group == "上传组" and hit[0].caption == "梗义甲"
        # 暂存体不留尸：uploads 目录里没有残留 .part
        leftovers = list(Path(lib.uploads_dir).glob(".*")) if lib.uploads_dir.exists() else []
        assert not leftovers

    def test_seq_gap_voids_session(self, tmp_path):
        lib = Library(tmp_path / "lib")
        sid, _ = lib.upload_start("p.zip")
        assert lib.upload_append(sid, 1, b"xx") == "upload_seq_gap"
        # 作废后连合法 seq 也不接受
        assert lib.upload_append(sid, 0, b"xx") == "upload_session_unknown"

    def test_oversize_total_voids_session(self, tmp_path):
        lib = Library(tmp_path / "lib")
        sid, error = lib.upload_start("p.zip", size=UPLOAD_MAX_TOTAL_BYTES + 1)
        assert sid == "" and error == "upload_too_large"
        sid, _ = lib.upload_start("p.zip")
        assert lib.upload_append(sid, 0, b"z" * (UPLOAD_CHUNK_BYTES + 1)) == "upload_too_large"
        assert lib.upload_append(sid, 0, b"z") == "upload_session_unknown"

    def test_finish_empty_or_unknown(self, tmp_path):
        lib = Library(tmp_path / "lib")
        sid, _ = lib.upload_start("p.zip")
        summary, error = lib.upload_finish(sid)
        assert summary == {} and error == "upload_empty"
        assert lib.upload_finish(sid) == ({}, "upload_session_unknown")
        assert lib.upload_finish("nope") == ({}, "upload_session_unknown")
        # 空会话收尾后盘上不留 .part
        leftovers = list(Path(lib.uploads_dir).glob(".*")) if lib.uploads_dir.exists() else []
        assert not leftovers

    def test_not_really_zip_reported_as_failed(self, tmp_path):
        # 名字是 .zip 但字节不是 zip：import_pack 的诚实账（failed 计数），不是崩溃
        lib = Library(tmp_path / "lib")
        lib.load()
        sid, _ = lib.upload_start("lie.zip")
        _drain(lib, sid, b"definitely not a zip file")
        summary, error = lib.upload_finish(sid)
        assert not error
        assert summary == {"imported": 0, "duplicates": 0, "rejected": 0, "failed": 1}

    def test_gc_drops_stale_sessions(self, tmp_path, monkeypatch):
        lib = Library(tmp_path / "lib")
        sid, _ = lib.upload_start("p.zip")
        lib._uploads[sid]["at"] -= 25 * 3600  # 假装死了一天的会话
        lib.upload_start("q.zip")
        assert sid not in lib._uploads


def _plugin(tmp_path):
    host = FakeHostContext(
        data_root=tmp_path,
        config=FakeConfig(data={"sticker_manager": {"enabled": True}}),
    )
    plugin, host = build_plugin(host)
    return plugin


class TestUploadEntries:
    def test_entry_roundtrip(self, tmp_path, run_async):
        plugin = _plugin(tmp_path)
        blob = _make_pack({"a.png": PNG_BYTES})
        start = run_async(plugin.import_upload_start_entry(name="pack.zip", size=len(blob)))
        assert start.is_ok()
        payload = start.value
        assert payload["note"] == "upload_opened"
        assert payload["chunk_bytes"] == UPLOAD_CHUNK_BYTES
        sid = payload["session"]
        b64 = base64.b64encode(blob).decode("ascii")
        chunked = run_async(plugin.import_upload_chunk_entry(session=sid, seq=0, data_base64=b64))
        assert chunked.is_ok() and chunked.value["received"] == 1
        fin = run_async(plugin.import_upload_finish_entry(session=sid))
        assert fin.is_ok()
        assert fin.value["imported"] == 1
        listed = run_async(plugin.list_entry(query=""))
        assert listed.value["count"] == 1

    def test_entry_input_guards(self, tmp_path, run_async):
        plugin = _plugin(tmp_path)
        assert str(run_async(plugin.import_upload_start_entry(name="a.png")).error) == "upload_not_zip"
        assert str(run_async(plugin.import_upload_start_entry()).error) == "upload_not_zip"
        assert str(run_async(plugin.import_upload_chunk_entry(session="x", seq=0, data_base64="!!!")).error) in {
            "upload_chunk_bad",
            "upload_session_unknown",
        }
        assert (
            str(run_async(plugin.import_upload_chunk_entry(session="", seq=0, data_base64="AAA=")).error)
            == "upload_session_unknown"
        )
        assert (
            str(run_async(plugin.import_upload_chunk_entry(session="s", seq="0", data_base64="AAA=")).error)
            == "upload_seq_gap"
        )
        assert (
            str(
                run_async(
                    plugin.import_upload_chunk_entry(session="s", seq=0, data_base64="A" * (4 * 1024 * 1024 + 4))
                ).error
            )
            == "upload_too_large"
        )
        assert str(run_async(plugin.import_upload_finish_entry(session="ghost")).error) == "upload_session_unknown"

    def test_session_isolated_per_library_instance(self, tmp_path):
        a = Library(tmp_path / "a")
        b = Library(tmp_path / "b")
        sid_a, _ = a.upload_start("p.zip")
        assert b.upload_append(sid_a, 0, b"x") == "upload_session_unknown"
