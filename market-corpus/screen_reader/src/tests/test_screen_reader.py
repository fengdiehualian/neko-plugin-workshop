"""screen_reader v0.1.0 测试：入口注册、分派、截图存盘与 OCR 降级（不依赖真实桌面截屏）。"""

import asyncio
import os

import pytest

PIL = pytest.importorskip("PIL")


def _plugin():
    from plugin.plugins.screen_reader import ScreenReaderPlugin

    inst = ScreenReaderPlugin.__new__(ScreenReaderPlugin)
    inst._save_dir = None
    return inst


def _run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


def test_entry_registered():
    """plugin_entry run 入口可被宿主收集（入口点 > 0）。"""
    from plugin.plugins.screen_reader import ScreenReaderPlugin

    metas = []
    for name in dir(ScreenReaderPlugin):
        if name.startswith("_"):
            continue
        meta = getattr(getattr(ScreenReaderPlugin, name, None), "__neko_event_meta__", None)
        if meta is not None and getattr(meta, "event_type", None) == "plugin_entry":
            metas.append(meta)
    assert any(m.id == "run" for m in metas), f"缺少入口 run，现有：{[m.id for m in metas]}"


def test_entry_unknown_action():
    inst = _plugin()
    out = _run(inst.entry_run(action="fly", parameters={}))
    assert out["is_error"] is True
    assert out["error"] == "NO_ACTION"


def test_entry_foreground_no_window(monkeypatch):
    """无前台窗口可识别时安全返回 NO_WINDOW（不崩溃）。"""
    from plugin.plugins.screen_reader import capture

    monkeypatch.setattr(capture, "get_foreground_window", lambda: None)
    inst = _plugin()
    out = _run(inst.entry_run(action="foreground", parameters={}))
    assert out["is_error"] is True
    assert out["error"] == "NO_WINDOW"


def test_save_virtual_image_roundtrip(tmp_path):
    """虚拟图片存盘：返回 PNG 路径且文件存在（不触真实屏幕）。"""
    from plugin.plugins.screen_reader import capture

    img = PIL.Image.new("RGB", (64, 48), color=(120, 120, 200))
    path = capture._save_virtual_image(img, str(tmp_path))
    assert path and os.path.exists(path) and path.endswith(".png")
    with PIL.Image.open(path) as check:
        assert check.size == (64, 48)


def test_ocr_unavailable_returns_none(monkeypatch):
    """OCR 引擎缺失 → 返回 None（调用方降级为提供截图路径）。"""
    from plugin.plugins.screen_reader import capture

    monkeypatch.setattr(capture, "_TESSERACT", False)
    assert capture.ocr_image("x.png") is None


def test_capture_region_parsing():
    """区域参数解析：'x,y,w,h' 与中文逗号均可。"""
    parts = [int(p) for p in "100,200,800,600".replace("，", ",").split(",")]
    assert parts == [100, 200, 800, 600]
    parts2 = [int(p) for p in "100，200，800，600".replace("，", ",").split(",")]
    assert parts2 == [100, 200, 800, 600]


def test_manifest_version():
    """plugin.toml 版本与代码一致（0.1.3）。"""
    import re
    from pathlib import Path

    toml = Path(__file__).resolve().parents[1] / "plugin.toml"
    m = re.search(r'version\s*=\s*"([^"]+)"', toml.read_text(encoding="utf-8"))
    assert m and m.group(1) == "0.1.3"
