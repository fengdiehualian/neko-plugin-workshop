# -*- coding: utf-8 -*-
"""鼠标功能真实集成测试：自建交互窗口，点击后 UIA 读回状态闭环验证。"""
import asyncio
import base64
import ctypes
import importlib.util
import logging
import os
import os as _os
import sys
import tempfile
import types
from pathlib import Path

if _os.environ.get('KC_ITEST') != '1':
    import pytest
    pytest.skip('手动鼠标集成测试：需 KC_ITEST=1 且在真实桌面运行', allow_module_level=True)

PLUGIN_DIR = str(Path(__file__).resolve().parent.parent)
TEST_TITLE = "KC_MTEST_WINDOW_B3"

# ── plugin.sdk 假包（同 kc_itest v3）─────────────────────────────────
sdk_pkg = types.ModuleType("plugin")
sdk_pkg.__path__ = []
sys.modules["plugin"] = sdk_pkg
sdk_sub = types.ModuleType("plugin.sdk")
sdk_sub.__path__ = []
sys.modules["plugin.sdk"] = sdk_sub
sdk_plugin = types.ModuleType("plugin.sdk.plugin")

def _noop_dec(*dargs, **dkwargs):
    if len(dargs) == 1 and callable(dargs[0]) and not dkwargs:
        return dargs[0]
    def wrap(fn):
        return fn
    return wrap

class SdkError(Exception):
    pass

class Ok:
    def __init__(self, payload=None): self.payload = payload or {}
    def __getitem__(self, k): return self.payload[k]
    def get(self, k, d=None): return self.payload.get(k, d)
    def __contains__(self, k): return k in self.payload

class Err:
    def __init__(self, err): self.error = err

class NekoPluginBase:
    def __init__(self, ctx):
        self.ctx = ctx
        self.config = ctx.config
        self.store = ctx.store
        self.logger = ctx.logger
    def data_path(self, *parts):
        p = Path(DATA_DIR)
        for part in parts:
            p = p / part
        Path(str(p)).parent.mkdir(parents=True, exist_ok=True)
        return str(p)
    def push_message(self, **kwargs):
        return self.ctx.push_message(**kwargs)

sdk_plugin.neko_plugin = _noop_dec
sdk_plugin.NekoPluginBase = NekoPluginBase
sdk_plugin.SdkError = SdkError
sdk_plugin.Ok = Ok
sdk_plugin.Err = Err
for name in ("llm_tool", "lifecycle", "timer_interval", "message", "hook",
             "before_entry", "after_entry", "around_entry", "replace_entry", "plugin_entry"):
    setattr(sdk_plugin, name, _noop_dec)
sdk_plugin.PluginRouter = type("PluginRouter", (), {"include_router": lambda self, r, **k: None})
sdk_plugin.tr = lambda key, **kw: kw.get("default", key)
sdk_plugin.unwrap_or = lambda v, default=None: v if v is not None else default
sdk_plugin.ui = types.SimpleNamespace(action=_noop_dec, context=_noop_dec, guide=_noop_dec)
sys.modules["plugin.sdk.plugin"] = sdk_plugin

# 假 RapidOCR（本测试不需要 OCR，但模块导入链需要）
_pp = types.ModuleType("plugin.plugins")
_pp.__path__ = []
sys.modules["plugin.plugins"] = _pp
_sh = types.ModuleType("plugin.plugins._shared")
_sh.__path__ = []
sys.modules["plugin.plugins._shared"] = _sh
_rq = types.ModuleType("plugin.plugins._shared.rapidocr")
_rq.__path__ = []
sys.modules["plugin.plugins._shared.rapidocr"] = _rq
_ob = types.ModuleType("plugin.plugins._shared.rapidocr.ocr_backends")
class RapidOcrBackend:
    def __init__(self, *a, **k): pass
    def is_available(self): return True
    def extract_text_with_boxes(self, image): return "", []
_ob.RapidOcrBackend = RapidOcrBackend
sys.modules["plugin.plugins._shared.rapidocr.ocr_backends"] = _ob

# ── 假宿主上下文 ─────────────────────────────────────────────────────
DATA_DIR = tempfile.mkdtemp(prefix="kc_mtest_")

class FakeStore:
    enabled = True
    def __init__(self): self.data = {}
    async def get(self, key): return self.data.get(key)
    async def set(self, key, value): self.data[key] = value
    async def delete(self, key): self.data.pop(key, None)

class FakeConfig:
    def __init__(self, cfg): self._cfg = cfg
    async def dump(self, timeout=5.0): return self._cfg

class FakeCtx:
    def __init__(self):
        self.logger = logging.getLogger("kc_mtest")
        logging.basicConfig(level=logging.CRITICAL)
        self.store = FakeStore()
        self.config = FakeConfig({"keyboard_controller": {"command_require_confirmation": False}})
        self.pushed = []
    def push_message(self, **kwargs):
        self.pushed.append(kwargs)
        return types.SimpleNamespace(submitted=True)
    def data_path(self, *parts):
        p = Path(DATA_DIR)
        for part in parts:
            p = p / part
        Path(str(p)).parent.mkdir(parents=True, exist_ok=True)
        return str(p)

# ── 加载真实插件 ─────────────────────────────────────────────────────
spec = importlib.util.spec_from_file_location(
    "keyboard_controller_under_test", os.path.join(PLUGIN_DIR, "__init__.py"))
kc = importlib.util.module_from_spec(spec)
sys.modules["keyboard_controller_under_test"] = kc
spec.loader.exec_module(kc)

RESULTS = []

def report(name, passed, info=""):
    RESULTS.append((name, passed, info))
    print(f"{'PASS' if passed else 'FAIL'} {name}" + (f" | {info}" if info else ""))


FORM_SCRIPT = f'''
Add-Type -AssemblyName System.Windows.Forms
$sig = '[DllImport("user32.dll")] public static extern bool SetProcessDPIAware();'
Add-Type -MemberDefinition $sig -Name Dpi -Namespace Win32
[Win32.Dpi]::SetProcessDPIAware() | Out-Null
$f = New-Object System.Windows.Forms.Form
$f.Text = "{TEST_TITLE}"
$f.Size = New-Object System.Drawing.Size(560, 420)
$script:state = $false
$lbl = New-Object System.Windows.Forms.Label
$lbl.Name = "StateLabel"
$lbl.Text = "STATE:OFF"
$lbl.Font = New-Object System.Drawing.Font('Consolas', 20, [System.Drawing.FontStyle]::Bold)
$lbl.AutoSize = $true
$lbl.Location = New-Object System.Drawing.Point(30, 40)
$f.Controls.Add($lbl)
$btn = New-Object System.Windows.Forms.Button
$btn.Name = "ToggleBtn"
$btn.Text = "TOGGLE_BTN"
$btn.Font = New-Object System.Drawing.Font('Segoe UI', 14)
$btn.Size = New-Object System.Drawing.Size(200, 60)
$btn.Location = New-Object System.Drawing.Point(30, 120)
$btn.Add_Click({{ $script:state = -not $script:state; $lbl.Text = "STATE:" + $(if ($script:state) {{'ON'}} else {{'OFF'}}) }})
$f.Controls.Add($btn)
[System.Windows.Forms.Application]::Run($f)
'''


async def spawn_form():
    import subprocess as sp
    return sp.Popen(["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand",
                     base64.b64encode(FORM_SCRIPT.encode("utf-16-le")).decode("ascii")],
                    creationflags=0x00000200 | 0x08000000,
                    stdin=sp.DEVNULL, stdout=sp.DEVNULL, stderr=sp.DEVNULL).pid


async def get_state_text(plugin) -> str:
    r = await plugin.inspect_controls(max_results=20)
    if isinstance(r, kc.Err):
        return f"<Err {r.error}>"
    for c in r.get("controls") or []:
        if str(c.get("text") or "").startswith("STATE:"):
            return str(c.get("text"))
    return "<no-state-label>"


async def main():
    ctx = FakeCtx()
    plugin = kc.KeyboardControllerPlugin(ctx)
    report("startup", (await plugin.startup()) is not None)

    target_pid = await spawn_form()
    r = await plugin.wait_for_window(query=TEST_TITLE, timeout=20)
    wins = r.get("windows") or []
    test_hwnd = 0
    for cand in wins:
        hwnd_i = int(cand.get("hwnd") or 0)
        if await asyncio.to_thread(kc.win32.is_window, hwnd_i):
            test_hwnd = hwnd_i
            break
    report("spawn_form", test_hwnd > 0, f"hwnd={test_hwnd}")
    if not test_hwnd:
        return

    w32wins = await asyncio.to_thread(kc.win32.enumerate_windows)
    mine = next(w for w in w32wins if int(w.get("hwnd") or 0) == test_hwnd)
    plugin._target = {"pid": int(mine.get("pid")), "title": mine.get("title"),
                      "process_name": mine.get("process_name"), "hwnd": test_hwnd}
    plugin._target_hwnd_cache = None
    # 表单置前，保证点击落点可见可交互
    await asyncio.to_thread(kc.win32.focus_window, test_hwnd, attempts=3)
    await asyncio.sleep(0.5)

    state0 = await get_state_text(plugin)
    print(f"DBG 初始状态: {state0}")

    # ── 1. UIA 找按钮中心（供坐标点击）──
    r = await plugin.inspect_controls(max_results=20)
    btn = None
    for c in r.get("controls") or []:
        if "TOGGLE" in str(c.get("text") or "").upper() or c.get("class_name") == "System.Windows.Forms.Button":
            btn = c
            break
    report("inspect_find_button", btn is not None,
           f"center=({(btn or {}).get('center_x')},{(btn or {}).get('center_y')})" if btn else "")
    if btn is None:
        return

    bx, by = int(btn["center_x"]), int(btn["center_y"])

    # ── 2. click_in_window 真实点击 → 状态应 OFF→ON ──
    r = await plugin.click_in_window(x=bx, y=by, clicks=1)
    await asyncio.sleep(0.6)
    state1 = await get_state_text(plugin)
    report("click_in_window_toggles", "STATE:ON" in state1, f"{state0} -> {state1}")

    # ── 3. mouse_move 真实光标移动 → GetCursorPos 校验 ──
    u32 = ctypes.windll.user32
    pt = ctypes.wintypes.POINT()
    u32.GetCursorPos(ctypes.byref(pt))
    orig_cur = (pt.x, pt.y)
    target_cur = (orig_cur[0] + 137, orig_cur[1] + 73)
    await asyncio.to_thread(kc.win32.mouse_move, *target_cur)
    await asyncio.sleep(0.15)
    u32.GetCursorPos(ctypes.byref(pt))
    moved = (abs(pt.x - target_cur[0]) <= 2 and abs(pt.y - target_cur[1]) <= 2)
    report("mouse_move_position", moved, f"cursor=({pt.x},{pt.y}) want={target_cur}")
    await asyncio.to_thread(kc.win32.mouse_move, *orig_cur)  # 还原

    # ── 4. mouse_click（屏幕绝对坐标）再切一次 → ON→OFF ──
    # 换算窗口相对→屏幕绝对
    rect = await asyncio.to_thread(kc.win32.window_client_rect, test_hwnd)
    sx, sy = rect["left"] + bx, rect["top"] + by
    await asyncio.to_thread(kc.win32.mouse_click, sx, sy, button="left", clicks=1)
    await asyncio.sleep(0.6)
    state2 = await get_state_text(plugin)
    report("mouse_click_toggles", "STATE:OFF" in state2, f"-> {state2}")

    # ── 5. hold_mouse 长按点击（按下保持 0.15s 松开）→ 再切换 ──
    await asyncio.to_thread(kc.win32.mouse_move, sx, sy)
    await asyncio.to_thread(kc.win32.hold_mouse_button, "left", seconds=0.15)
    await asyncio.sleep(0.6)
    state3 = await get_state_text(plugin)
    report("hold_mouse_toggles", "STATE:ON" in state3, f"-> {state3}")

    # ── 6. mouse_drag 无异常 + 光标终点正确 ──
    await asyncio.to_thread(kc.win32.mouse_drag, sx, sy, sx + 120, sy + 80, button="left", steps=10)
    await asyncio.sleep(0.2)
    u32.GetCursorPos(ctypes.byref(pt))
    drag_end_ok = abs(pt.x - (sx + 120)) <= 2 and abs(pt.y - (sy + 80)) <= 2
    # 拖拽经过按钮可能触发点击，状态不定——只验证光标终点
    report("mouse_drag_endpoint", drag_end_ok, f"cursor=({pt.x},{pt.y})")

    # ── 7. mouse_wheel 无异常 ──
    await asyncio.to_thread(kc.win32.mouse_wheel, sx, sy, delta=240)
    report("mouse_wheel_no_exc", True)

    # ── 8. mouse_wheel 工具层 ──
    r = await plugin.mouse_wheel(x=sx, y=sy, delta=-240)
    report("mouse_wheel_tool", r.get("scrolled") is True)

    # 清理
    await plugin.clear_target()
    os.system(f"taskkill /PID {target_pid} /F >nul 2>&1")
    await asyncio.to_thread(kc.win32.mouse_move, *orig_cur)  # 光标还原

    failed = [n for n, p, _ in RESULTS if not p]
    print(f"\n==== {len(RESULTS)-len(failed)}/{len(RESULTS)} PASSED ====")
    if failed:
        print("FAILED:", failed)


if __name__ == '__main__':
    asyncio.run(main())
    # sys.exit 必须在 main 块内：放模块顶层时 KC_ITEST=1 下任何 import 都会直接退出进程。
    sys.exit(0 if all(p for _, p, _ in RESULTS) else 1)
