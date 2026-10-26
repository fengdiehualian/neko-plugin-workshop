"""steam_manager 游戏助手单元测试：状态机、授权门禁、坐标换算、BMP 输出。

注意：与 card_master/media_generator 测试一致，插件模块在函数内导入，
避免 isort first-party 判定在本地/CI 间分歧。
"""

from __future__ import annotations

import struct
import time


def _fake_window(title: str = "Test Game", ts: float | None = None) -> dict:
    return {
        "hwnd": 12345,
        "title": title,
        "pid": 999,
        "process": "game.exe",
        "rect": (100, 100, 500, 400),
        "ts": ts if ts is not None else time.time(),
    }


# ══════════ ControlState 状态机 ══════════


def test_state_initial_flags() -> None:
    from plugin.plugins.steam_manager.control import ControlState

    state = ControlState()
    assert state.watch_enabled is False
    assert state.control_enabled is False
    assert len(state.frames) == 0


def test_watch_toggle_records_events() -> None:
    from plugin.plugins.steam_manager.control import ControlState

    state = ControlState()
    state.enable_watch()
    assert state.watch_enabled is True
    state.note_frame(_fake_window(), r"C:\tmp\frame.bmp")
    assert len(state.frames) == 1
    state.disable_watch()
    assert state.watch_enabled is False
    events = [t["event"] for t in state.timeline]
    assert "watch_start" in events
    assert "watch_stop" in events


def test_control_requires_window_evidence() -> None:
    from plugin.plugins.steam_manager.control import ControlState

    state = ControlState()
    # 无任何帧 → 拒绝开启接管
    assert state.enable_control() is False
    assert state.control_enabled is False
    # 有最近帧 → 允许开启
    state.note_frame(_fake_window(ts=time.time()), r"C:\tmp\frame.bmp")
    assert state.enable_control() is True
    assert state.control_enabled is True
    # 交还
    state.disable_control()
    assert state.control_enabled is False


def test_control_rejected_with_stale_frame() -> None:
    from plugin.plugins.steam_manager.control import ControlState

    state = ControlState()
    stale = _fake_window(ts=time.time() - 3600)
    state.note_frame(stale, r"C:\tmp\old.bmp")
    assert state.enable_control() is False


def test_recent_window_active() -> None:
    from plugin.plugins.steam_manager.control import ControlState

    state = ControlState()
    state.note_frame(_fake_window(ts=time.time()), "x.bmp")
    assert state.recent_window_active(within_seconds=10) is True
    state.note_frame(_fake_window(ts=time.time() - 100), "y.bmp")
    assert state.recent_window_active(within_seconds=10) is False


def test_summary_shape() -> None:
    from plugin.plugins.steam_manager.control import ControlState

    state = ControlState()
    state.note_frame(_fake_window(), r"C:\tmp\f.bmp")
    state.note_event("action", "click")
    summary = state.summary()
    assert summary["last_window"]["title"] == "Test Game"
    assert len(summary["frames"]) == 1
    assert summary["timeline"][0]["event"] == "action"


def test_frames_ring_buffer_bounded() -> None:
    from plugin.plugins.steam_manager.control import ControlState

    state = ControlState(max_frames=3)
    for i in range(10):
        state.note_frame(_fake_window(title=f"G{i}", ts=time.time()), f"{i}.bmp")
    assert len(state.frames) == 3
    assert state.frames[0]["title"] == "G7"


# ══════════ _parse_vk ══════════


def test_parse_vk() -> None:
    from plugin.plugins.steam_manager.__init__ import _parse_vk

    assert _parse_vk("a") == ord("A")
    assert _parse_vk("5") == 0x35
    assert _parse_vk("65") == 65
    assert _parse_vk("0") == 0x30
    assert _parse_vk("300") is None
    assert _parse_vk("") is None
    assert _parse_vk("abc") is None
    assert _parse_vk("中") is None


# ══════════ 坐标换算 ══════════


def test_window_client_to_screen() -> None:
    from plugin.plugins.steam_manager.control import window_client_to_screen

    win = _fake_window()  # rect (100,100)-(500,400) → w=400 h=300
    sx, sy = window_client_to_screen(win, 500, 500)
    assert sx == 300
    assert sy == 250
    sx, sy = window_client_to_screen(win, 0, 0)
    assert (sx, sy) == (100, 100)
    sx, sy = window_client_to_screen(win, 1000, 1000)
    assert (sx, sy) == (500, 400)


# ══════════ BMP 写入（字节校验） ══════════


def test_write_bmp_header_and_size(tmp_path) -> None:
    from plugin.plugins.steam_manager.control import _write_bmp

    # 4x2 的 32 位 BGRA 像素（每行 16 字节）
    pixels = bytes(range(4 * 4 * 2))
    path = _write_bmp(str(tmp_path), 4, 2, pixels)
    assert path and path.endswith(".bmp")
    data = open(path, "rb").read()
    assert data[:2] == b"BM"
    offset, width, height, planes, bpp = struct.unpack_from("<IiiHH", data, 14)
    assert offset == 40
    assert width == 4 and height == 2 and planes == 1 and bpp == 32
    file_size = len(data)
    # 4x2 的 32 位 BGRA：每行 4px*4B=16，2 行 → 54 + 16*2 = 86（数据与头部声明的
    # biSizeImage 一致，PIL/视觉可正常解码；此前头部声明 32bit 却只写 3 字节/像素
    # 的 24bit 数据，文件长度与头不符导致读图端报 truncated）
    assert file_size == 54 + 16 * 2

# ══════════ v0.3.1：帧目录清理回归测试 ══════════


def test_cleanup_old_frames_keeps_recent(tmp_path, monkeypatch) -> None:
    """FrameWatcher 定期清理：只保留最近 keep 个帧文件，防止磁盘膨胀。"""
    from plugin.plugins.steam_manager import control

    frame_dir = tmp_path / "frames"
    frame_dir.mkdir()
    for i in range(1, 11):
        (frame_dir / f"frame_{i:04d}.bmp").write_bytes(b"\x00" * 10)
    monkeypatch.setattr(control, "_FRAME_DIR", str(frame_dir))
    state = control.ControlState()
    watcher = control.FrameWatcher(state, interval=9.0)
    watcher._cleanup_old_frames(keep=3)
    remaining = sorted(f.name for f in frame_dir.iterdir())
    assert remaining == ["frame_0008.bmp", "frame_0009.bmp", "frame_0010.bmp"]
