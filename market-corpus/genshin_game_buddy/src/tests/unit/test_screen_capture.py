"""screen_capture 单元测试：主动抓屏降级链 PrintWindow -> mss -> bridge 与黑帧判定。

契约（与 ``screen_capture.py`` 一一对应，函数名/签名不得漂移）::

    BLACK_FRAME_LUMA_THRESHOLD = 12.0
    CaptureResult(ok, image_base64, width, height, method, error, avg_luma)  # frozen
    find_genshin_window() -> dict | None        # {"hwnd","pid","title","rect"} 或 None
    is_black_frame(image_base64, ocr_len=None) -> bool
    _capture_printwindow(hwnd, rect) -> CaptureResult
    _capture_mss(rect) -> CaptureResult
    _capture_bridge(pid, title) -> CaptureResult
    capture_window_base64(target=None) -> CaptureResult

方法论：
  * 三路抓屏是**模块级函数**，monkeypatch.setattr(screen_capture, ...) 即可换掉，
    用 spy 记录调用顺序与次数来断言降级链；
  * 桩返回的 image 与 avg_luma 始终**成对一致**（黑图配低 luma、亮图配高 luma），
    这样无论实现用 is_black_frame(image) 还是 avg_luma 阈值判黑，断言都成立；
  * 造图用函数内 lazy ``from PIL import Image``（与 ocr_engine.py 一致，不顶层 import），
    图片全部是合成的均匀灰阶 PNG，不涉及任何用户原始画面；
  * ``capture_window_base64`` 是同步函数，用例直接调用，不碰线程/事件循环；
  * 本文件不写任何真实文件（全部内存桩，不需要 scratch/tmp）。

``find_genshin_window`` 的窗口枚举通过向 ``sys.modules`` / 模块属性注入
win32gui / pygetwindow / psutil / win32process 假模块来驱动——这样无论实现
内部用哪种枚举入口（``win32gui.EnumWindows`` / ``pygetwindow.getAllWindows`` /
``from win32gui import EnumWindows`` 顶层绑定），用例都能控住。
假模块对未覆盖的 API 记名返回 None，失败断言里会把未覆盖清单打出来。
"""

from __future__ import annotations

import base64
import dataclasses
import importlib
import inspect
import io
import sys

import pytest

# =============================================================================
# 公共构造
# =============================================================================


@pytest.fixture(scope="module")
def sc():
    """screen_capture 模块（并行改造中新增的模块，收集期不 import，失败发生在用例内）。"""
    return importlib.import_module("plugin.plugins.genshin_game_buddy.screen_capture")


def _png_b64(gray: int) -> str:
    """合成一张均匀灰阶 PNG 的裸 base64（无 data: 前缀）。

    均匀灰阶意味着任何亮度公式（均值 / 0.299R+0.587G+0.114B / PIL "L" 转换）
    算出来都等于 ``gray``，阈值断言因此是确定的。
    """
    from PIL import Image  # 与 ocr_engine.py 一致：函数内 lazy import

    image = Image.new("RGB", (32, 24), (gray, gray, gray))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def _result(sc, *, ok: bool, b64: str | None, method: str, luma: float, error: str = ""):
    """按字段名构造 CaptureResult（不依赖字段顺序）。"""
    return sc.CaptureResult(
        ok=ok,
        image_base64=b64,
        width=800,
        height=600,
        method=method,
        error=error,
        avg_luma=luma,
    )


class _Spy:
    """降级链 spy：记录调用顺序，并提供按路计数。"""

    def __init__(self, sc) -> None:
        self.sc = sc
        self.calls: list[str] = []

    def count(self, method: str) -> int:
        return self.calls.count(method)

    def wrap(self, name: str, *, ok: bool = True, bright: bool = True, error: str = "", exc: BaseException | None = None):
        def _fn(*_args, **_kwargs):
            self.calls.append(name)
            if exc is not None:
                raise exc
            if not ok:
                return _result(
                    self.sc, ok=False, b64=None, method=name, luma=0.0, error=error or f"{name} failed"
                )
            gray = 200 if bright else 0
            return _result(
                self.sc,
                ok=True,
                b64=_png_b64(gray),
                method=name,
                luma=float(gray),
                error=error,
            )

        return _fn


def _install_chain(monkeypatch: pytest.MonkeyPatch, sc, spy: _Spy, spec: dict) -> None:
    """按 spec 换掉三路抓屏：{method: {"ok","bright","error","exc"}}。"""
    for method, attr in (
        ("printwindow", "_capture_printwindow"),
        ("mss", "_capture_mss"),
        ("bridge", "_capture_bridge"),
    ):
        monkeypatch.setattr(sc, attr, spy.wrap(method, **spec.get(method, {})))


GAME_TARGET = {"hwnd": 1001, "pid": 101, "title": "原神", "rect": (10, 20, 1290, 740)}


# =============================================================================
# 契约面：函数名/签名/冻结 dataclass 不许漂移
# =============================================================================


@pytest.mark.unit
def test_contract__surface_names_and_signatures(sc) -> None:
    assert sc.BLACK_FRAME_LUMA_THRESHOLD == 12.0
    assert callable(sc.find_genshin_window)
    assert callable(sc.is_black_frame)
    assert callable(sc.capture_window_base64)
    for name in ("_capture_printwindow", "_capture_mss", "_capture_bridge"):
        assert callable(getattr(sc, name)), name

    def params(fn) -> list[str]:
        return list(inspect.signature(fn).parameters)

    assert params(sc.find_genshin_window) == []
    assert params(sc.is_black_frame) == ["image_base64", "ocr_len"]
    assert params(sc._capture_printwindow) == ["hwnd", "rect"]
    assert params(sc._capture_mss) == ["rect"]
    assert params(sc._capture_bridge) == ["pid", "title"]
    assert params(sc.capture_window_base64) == ["target"]


@pytest.mark.unit
def test_contract__capture_result_is_frozen_with_exact_fields(sc) -> None:
    assert dataclasses.is_dataclass(sc.CaptureResult)
    fields = {field.name for field in dataclasses.fields(sc.CaptureResult)}
    assert fields == {"ok", "image_base64", "width", "height", "method", "error", "avg_luma"}
    instance = _result(sc, ok=False, b64=None, method="printwindow", luma=0.0, error="e")
    with pytest.raises(dataclasses.FrozenInstanceError):
        instance.method = "mss"  # type: ignore[misc]


# =============================================================================
# 降级链：PrintWindow -> mss -> bridge
# =============================================================================


@pytest.mark.unit
def test_fallback__printwindow_first_hit_short_circuits(sc, monkeypatch) -> None:
    """首选命中：printwindow 返回 ok 且非黑 → 不再走 mss/bridge。"""
    spy = _Spy(sc)
    _install_chain(monkeypatch, sc, spy, {"printwindow": {"ok": True, "bright": True}})

    result = sc.capture_window_base64(dict(GAME_TARGET))

    assert result.ok is True
    assert result.method == "printwindow"
    assert spy.calls == ["printwindow"]  # 顺序即降级链
    assert spy.count("mss") == 0
    assert spy.count("bridge") == 0


@pytest.mark.unit
def test_fallback__image_base64_has_no_data_uri_prefix(sc, monkeypatch) -> None:
    """OcrEngine 只吃裸 base64：返回串不得带 data: 前缀。"""
    spy = _Spy(sc)
    _install_chain(monkeypatch, sc, spy, {"printwindow": {"ok": True, "bright": True}})

    result = sc.capture_window_base64(dict(GAME_TARGET))

    assert result.ok is True
    assert result.image_base64
    assert not result.image_base64.startswith("data:")
    assert "data:" not in result.image_base64
    assert base64.b64decode(result.image_base64)  # 裸 base64 必须可直接解码出图像字节


@pytest.mark.unit
def test_fallback__black_printwindow_degrades_to_mss(sc, monkeypatch) -> None:
    """黑帧降级：printwindow 拍到黑帧（avg_luma 低于阈值）→ 降级到 mss。"""
    spy = _Spy(sc)
    _install_chain(
        monkeypatch,
        sc,
        spy,
        {"printwindow": {"ok": True, "bright": False}, "mss": {"ok": True, "bright": True}},
    )

    result = sc.capture_window_base64(dict(GAME_TARGET))

    assert result.ok is True
    assert result.method == "mss"
    assert spy.calls == ["printwindow", "mss"]  # 顺序即降级链，不跳过 printwindow
    assert spy.count("printwindow") == 1
    assert spy.count("bridge") == 0


@pytest.mark.unit
@pytest.mark.parametrize("style", ["failed", "black"])
def test_fallback__all_paths_exhausted_returns_last_method_with_error(sc, monkeypatch, style: str) -> None:
    """三路全黑/全失败：ok=False、method 是最后一路、error 非空。"""
    spy = _Spy(sc)
    options = (
        {name: {"ok": False, "error": f"{name} failed"} for name in ("printwindow", "mss", "bridge")}
        if style == "failed"
        else {name: {"ok": True, "bright": False} for name in ("printwindow", "mss", "bridge")}
    )
    _install_chain(monkeypatch, sc, spy, options)

    result = sc.capture_window_base64(dict(GAME_TARGET))

    assert spy.calls == ["printwindow", "mss", "bridge"]  # 一路不落地走到底
    assert result.ok is False
    assert result.method == "bridge"  # method 必须是最后一路
    assert result.error  # 失败必须带可诊断的错误文案


@pytest.mark.unit
def test_fallback__exception_in_printwindow_is_contained(sc, monkeypatch) -> None:
    """资源清理/无异常冒泡：printwindow 内部炸掉也必须降级，不得把异常抛给调用方。"""
    spy = _Spy(sc)
    _install_chain(
        monkeypatch,
        sc,
        spy,
        {
            "printwindow": {"exc": RuntimeError("win32 handle leaked")},
            "mss": {"ok": True, "bright": True},
        },
    )

    result = sc.capture_window_base64(dict(GAME_TARGET))  # 不得抛异常

    assert result.ok is True
    assert result.method == "mss"
    assert spy.calls == ["printwindow", "mss"]
    assert spy.count("bridge") == 0


@pytest.mark.unit
def test_fallback__target_none_resolves_window_through_find_genshin_window(sc, monkeypatch) -> None:
    """target=None 时先找窗再走同一条降级链。"""
    monkeypatch.setattr(sc, "find_genshin_window", lambda: dict(GAME_TARGET))
    spy = _Spy(sc)
    _install_chain(monkeypatch, sc, spy, {"printwindow": {"ok": True, "bright": True}})

    result = sc.capture_window_base64()

    assert result.ok is True
    assert result.method == "printwindow"
    assert spy.calls == ["printwindow"]


# =============================================================================
# is_black_frame
# =============================================================================


@pytest.mark.unit
def test_is_black_frame__black_png_is_black(sc) -> None:
    assert sc.is_black_frame(_png_b64(0)) is True


@pytest.mark.unit
def test_is_black_frame__bright_png_is_not_black(sc) -> None:
    assert sc.is_black_frame(_png_b64(200)) is False


@pytest.mark.unit
def test_is_black_frame__luma_threshold_decides(sc) -> None:
    """阈值两侧各取一点（不取边界，避开舍入歧义）。"""
    threshold = int(sc.BLACK_FRAME_LUMA_THRESHOLD)
    assert sc.is_black_frame(_png_b64(threshold - 2)) is True
    assert sc.is_black_frame(_png_b64(threshold + 8)) is False


@pytest.mark.unit
def test_is_black_frame__ocr_len_overrides_luma(sc) -> None:
    """ocr_len 是严格判据：0 个字 = 判黑（即便画面很亮），有字 = 判非黑。"""
    bright = _png_b64(200)  # 用亮图做"严格"验证：luma 不得推翻 ocr_len 的判定
    assert sc.is_black_frame(bright, ocr_len=0) is True
    assert sc.is_black_frame(bright, ocr_len=5) is False
    assert sc.is_black_frame(_png_b64(0), ocr_len=0) is True


# =============================================================================
# find_genshin_window：win32gui / pygetwindow 假枚举
# =============================================================================


class _FakeProc:
    def __init__(self, pid: int, name: str) -> None:
        self.pid = pid
        self._name = name
        self.info = {"pid": pid, "name": name}

    def name(self) -> str:
        return self._name

    def exe(self) -> str:
        return f"C:\\Games\\{self._name}"


class _FakeWindow:
    """win32gui 句柄与 pygetwindow 窗口对象共用的替身。"""

    def __init__(self, hwnd: int, title: str, rect, pid: int, process_name: str, cls: str = "UnityWndClass") -> None:
        self.hwnd = hwnd
        self.title = title
        self.rect = tuple(rect)
        self.pid = pid
        self.tid = pid * 10
        self.process_name = process_name
        self.cls = cls
        self.visible = True

    @property
    def _hWnd(self) -> int:
        return self.hwnd

    @property
    def handle(self) -> int:
        return self.hwnd

    @property
    def left(self) -> int:
        return self.rect[0]

    @property
    def top(self) -> int:
        return self.rect[1]

    @property
    def right(self) -> int:
        return self.rect[2]

    @property
    def bottom(self) -> int:
        return self.rect[3]

    @property
    def width(self) -> int:
        return self.rect[2] - self.rect[0]

    @property
    def height(self) -> int:
        return self.rect[3] - self.rect[1]


class _FakeModule:
    """假 win32gui / pygetwindow / psutil / win32process。

    ``handlers`` 里是实现可能用到的枚举入口；其余属性给"记名返回 None"的兜底，
    未覆盖的调用会记进 ``unknown``，失败断言把它们打出来以便定位。
    """

    def __init__(self, name: str, handlers: dict, unknown: list[str]) -> None:
        self.name = name
        self.handlers = handlers
        self.unknown = unknown

    def __getattr__(self, attr: str):
        handlers = self.__dict__.get("handlers") or {}
        if attr in handlers:
            return handlers[attr]
        if attr.startswith("__"):
            raise AttributeError(attr)

        def _uncovered(*_args, **_kwargs):
            self.unknown.append(f"{self.name}.{attr}")
            return None

        return _uncovered


def _install_window_fakes(monkeypatch: pytest.MonkeyPatch, sc, windows: list[_FakeWindow]) -> list[str]:
    """把假枚举注入 sys.modules 与 screen_capture 的模块属性/顶层导入符号。"""
    unknown: list[str] = []
    by_hwnd = {w.hwnd: w for w in windows}
    procs = {w.pid: _FakeProc(w.pid, w.process_name) for w in windows}

    def enum_windows(callback, extra):
        for window in windows:
            callback(window.hwnd, extra)
        return True

    def is_window_visible(hwnd):
        window = by_hwnd.get(hwnd)
        return bool(window and window.visible)

    def get_window_text(hwnd):
        window = by_hwnd.get(hwnd)
        return window.title if window else ""

    def get_window_rect(hwnd):
        window = by_hwnd.get(hwnd)
        return window.rect if window else (0, 0, 0, 0)

    def get_window_thread_process_id(hwnd):
        window = by_hwnd.get(hwnd)
        return (window.tid, window.pid) if window else (0, 0)

    def get_class_name(hwnd):
        window = by_hwnd.get(hwnd)
        return window.cls if window else ""

    def get_module_file_name_ex(_handle, pid):
        proc = procs.get(pid) or _FakeProc(pid, "unknown.exe")
        return proc.exe()

    win32gui = _FakeModule(
        "win32gui",
        {
            "EnumWindows": enum_windows,
            "IsWindowVisible": is_window_visible,
            "IsWindow": lambda hwnd: hwnd in by_hwnd,
            "GetWindowText": get_window_text,
            "GetWindowRect": get_window_rect,
            "GetWindowThreadProcessId": get_window_thread_process_id,
            "GetClassName": get_class_name,
        },
        unknown,
    )
    pygetwindow = _FakeModule(
        "pygetwindow",
        {
            "getAllWindows": lambda: list(windows),
            "getWindowsWithTitle": lambda title: [
                w for w in windows if title.lower() in w.title.lower()
            ],
            "getActiveWindow": lambda: (windows[0] if windows else None),
        },
        unknown,
    )
    psutil = _FakeModule(
        "psutil",
        {
            "Process": lambda pid: procs.get(pid) or _FakeProc(pid, "unknown.exe"),
            "process_iter": lambda attrs=None: list(procs.values()),
        },
        unknown,
    )
    win32process = _FakeModule("win32process", {"GetModuleFileNameEx": get_module_file_name_ex}, unknown)

    fakes = {fake.name: fake for fake in (win32gui, pygetwindow, psutil, win32process)}
    for fake in fakes.values():
        monkeypatch.setitem(sys.modules, fake.name, fake)
        monkeypatch.setattr(sc, fake.name, fake, raising=False)
    # ``from win32gui import EnumWindows`` 这类顶层绑定也要换掉
    for name, value in list(vars(sc).items()):
        owner = getattr(value, "__module__", "")
        if owner in fakes:
            monkeypatch.setattr(sc, name, getattr(fakes[owner], name), raising=False)
    return unknown


def _game_windows() -> list[_FakeWindow]:
    return [
        _FakeWindow(1001, "原神", (10, 20, 1290, 740), 101, "YuanShen.exe"),
        _FakeWindow(1002, "Genshin Impact", (0, 0, 800, 600), 102, "YuanShen.exe"),
        _FakeWindow(2001, "记事本", (0, 0, 300, 200), 201, "notepad.exe", cls="Notepad"),
    ]


@pytest.mark.unit
def test_find_genshin_window__hits_when_game_window_present(sc, monkeypatch) -> None:
    """有 YuanShen.exe 窗口（中文/英文标题各一，另混一个无关窗口）→ 返回 dict。"""
    unknown = _install_window_fakes(monkeypatch, sc, _game_windows())

    result = sc.find_genshin_window()

    assert result is not None, f"未识别假原神窗口；未覆盖的 API 调用：{unknown}"
    assert {"hwnd", "pid", "title", "rect"} <= set(result)
    assert result["title"] in {"原神", "Genshin Impact"}
    assert result["pid"] in {101, 102}
    assert result["rect"] is not None


@pytest.mark.unit
def test_find_genshin_window__none_without_any_window(sc, monkeypatch) -> None:
    """一台机器上一个窗口都没有 → None（不崩、不返回空 dict）。"""
    unknown = _install_window_fakes(monkeypatch, sc, [])
    assert sc.find_genshin_window() is None, f"未覆盖的 API 调用：{unknown}"


@pytest.mark.unit
def test_find_genshin_window__none_without_genshin_window(sc, monkeypatch) -> None:
    """只有无关窗口 → None（必须过滤，不能返回第一个窗口充数）。"""
    windows = [
        _FakeWindow(2001, "记事本", (0, 0, 300, 200), 201, "notepad.exe", cls="Notepad"),
        _FakeWindow(2002, "Steam", (0, 0, 500, 400), 202, "steam.exe", cls="Chrome_WidgetWin_0"),
    ]
    unknown = _install_window_fakes(monkeypatch, sc, windows)
    assert sc.find_genshin_window() is None, f"未覆盖的 API 调用：{unknown}"


# =============================================================================
# capture_active_frame：本地进程优先，抓不到则前台窗口兜底（拍板 2.0.14 云游戏）
# =============================================================================


@pytest.mark.unit
def test_capture_active_frame__prefers_local_process(sc, monkeypatch) -> None:
    """本地游戏进程存在 → local_process 路径，按目标窗口抓。"""
    target = {"hwnd": 1, "pid": 2, "title": "原神", "rect": (0, 0, 800, 600)}
    seen: dict[str, object] = {}

    def _fake_capture(_target=None):
        seen["target"] = _target
        return sc.CaptureResult(True, "aGVsbG8=", 4, 4, "printwindow", "", 128.0)

    monkeypatch.setattr(sc, "find_genshin_window", lambda: target)
    monkeypatch.setattr(sc, "capture_window_base64", _fake_capture)

    result, path = sc.capture_active_frame()

    assert path == "local_process"
    assert result.ok is True
    assert seen["target"] is target, "本地路径必须把窗口目标传给 capture_window_base64"


# ----- mss 假模块（拍板 2.0.16 前景路径走 mss 主屏 → bridge 兜底） -----


class _FakeMssSct:
    """mss.mss().__enter__ 返回的对象：只需 .monitors。"""

    def __init__(self, monitors):
        self.monitors = monitors


class _FakeMssCtx:
    def __init__(self, monitors, raise_on_enter=False):
        self._monitors = monitors
        self._raise = raise_on_enter

    def __enter__(self):
        if self._raise:
            raise OSError("no_display")
        return _FakeMssSct(self._monitors)

    def __exit__(self, *args):
        return False


class _FakeMssModule:
    def __init__(self, monitors=None, raise_on_mss_call=False):
        self._monitors = monitors if monitors is not None else []
        self._raise = raise_on_mss_call

    def mss(self):
        if self._raise:
            raise OSError("no_display")
        return _FakeMssCtx(self._monitors)


def _install_mss_fakes(monkeypatch, sc, *, monitors=None, raise_on_mss_call=False):
    fake = _FakeMssModule(monitors=monitors, raise_on_mss_call=raise_on_mss_call)
    monkeypatch.setitem(sys.modules, "mss", fake)
    monkeypatch.setattr(sc, "mss", fake, raising=False)
    return fake


_ALL_MON = {"left": 0, "top": 0, "width": 1920, "height": 1080}
_PRIMARY_MON = {"left": 0, "top": 0, "width": 1920, "height": 1080}


@pytest.mark.unit
def test_capture_active_frame__foreground_via_mss_primary_monitor(sc, monkeypatch) -> None:
    """本地无游戏 → 前景路径走 mss 主屏（monitors[1]），不依赖 win32gui 枚举（拍板 2.0.16）。"""
    seen: dict[str, object] = {}

    def _fake_mss_capture(rect):
        seen["rect"] = rect
        return sc.CaptureResult(True, "aGVsbG8=", 4, 4, "mss", "", 128.0)

    monkeypatch.setattr(sc, "find_genshin_window", lambda: None)
    _install_mss_fakes(monkeypatch, sc, monitors=[_ALL_MON, _PRIMARY_MON])
    monkeypatch.setattr(sc, "_capture_mss", _fake_mss_capture)

    result, path = sc.capture_active_frame()

    assert path == "foreground"
    assert result.ok is True
    assert result.method == "mss"
    assert seen["rect"] == (0, 0, 1920, 1080), "前景路径必须用 mss.monitors[1] 的主屏 rect"


@pytest.mark.unit
def test_capture_active_frame__foreground_bridge_when_mss_capture_fails(sc, monkeypatch) -> None:
    """mss 抓失败（_capture_mss 返回 ok=False）→ 降级到宿主抓屏桥（bridge）。"""
    monkeypatch.setattr(sc, "find_genshin_window", lambda: None)
    _install_mss_fakes(monkeypatch, sc, monitors=[_ALL_MON, _PRIMARY_MON])
    monkeypatch.setattr(
        sc, "_capture_mss",
        lambda _rect: sc.CaptureResult(False, None, 0, 0, "mss", "error:OSError", -1.0),
    )
    monkeypatch.setattr(
        sc, "_capture_bridge",
        lambda _pid, _t: sc.CaptureResult(True, "YnJpZGdl", 4, 4, "bridge", "", 128.0),
    )

    result, path = sc.capture_active_frame()

    assert path == "foreground"
    assert result.ok is True
    assert result.method == "bridge", "mss 抓失败时必须降级到 bridge"


@pytest.mark.unit
def test_capture_active_frame__foreground_capture_failed_when_mss_module_and_bridge_fail(
    sc, monkeypatch
) -> None:
    """mss 模块本身炸（OSError）+ bridge 也炸 → capture_failed（附 mss/bridge 两路子错误）。"""
    monkeypatch.setattr(sc, "find_genshin_window", lambda: None)
    _install_mss_fakes(
        monkeypatch, sc, monitors=[_ALL_MON, _PRIMARY_MON], raise_on_mss_call=True
    )
    monkeypatch.setattr(
        sc, "_capture_bridge",
        lambda _pid, _t: sc.CaptureResult(False, None, 0, 0, "bridge", "error:HTTPError", -1.0),
    )

    result, path = sc.capture_active_frame()

    assert path == "foreground"
    assert result.ok is False
    assert result.error.startswith("capture_failed:")
    assert "mss=OSError" in result.error, "mss 子错误必须进 error 便于下次定位"
    assert "bridge=HTTPError" in result.error, "bridge 子错误必须进 error"


# =============================================================================
# find_foreground_window：GetForegroundWindow 拿不到时的 EnumWindows 兜底（拍板 2.0.15）
# =============================================================================


@pytest.mark.unit
def test_find_foreground_window__prefers_getforeground_when_available(sc, monkeypatch) -> None:
    """GetForegroundWindow 拿到 → 直接用它（不走 EnumWindows 兜底）。"""
    windows = [
        _FakeWindow(3001, "Edge", (0, 0, 1920, 1080), 301, "msedge.exe"),
        _FakeWindow(3002, "Notepad", (0, 0, 300, 200), 302, "notepad.exe"),
    ]
    _install_window_fakes(monkeypatch, sc, windows)
    monkeypatch.setattr(sc.win32gui, "GetForegroundWindow", lambda: 3002)

    result = sc.find_foreground_window()

    assert result is not None
    assert result["hwnd"] == 3002
    assert result["title"] == "Notepad"


@pytest.mark.unit
def test_find_foreground_window__falls_back_to_largest_visible_when_getforeground_zero(
    sc, monkeypatch
) -> None:
    """GetForegroundWindow 返回 0（worker 线程 / 前台在别的 session）→ 降级最大可见窗口。"""
    windows = [
        _FakeWindow(3001, "Edge", (0, 0, 1920, 1080), 301, "msedge.exe"),
        _FakeWindow(3002, "Notepad", (0, 0, 300, 200), 302, "notepad.exe"),
    ]
    unknown = _install_window_fakes(monkeypatch, sc, windows)

    result = sc.find_foreground_window()

    assert result is not None, f"未覆盖的 API 调用：{unknown}"
    assert result["hwnd"] == 3001
    assert result["title"] == "Edge"
    assert result["rect"] == (0, 0, 1920, 1080)


@pytest.mark.unit
def test_find_foreground_window__returns_none_when_no_large_window(sc, monkeypatch) -> None:
    """连一个 ≥500x400 的可见窗口都没有（只剩任务栏/桌面 helper）→ None → 上层报 no_hwnd。"""
    windows = [
        _FakeWindow(3002, "Taskbar-like", (0, 1040, 1920, 1080), 303, "explorer.exe"),
        _FakeWindow(3003, "Tiny", (0, 0, 200, 100), 304, "tiny.exe"),
    ]
    _install_window_fakes(monkeypatch, sc, windows)

    result = sc.find_foreground_window()

    assert result is None
