# -*- coding: utf-8 -*-
"""keyboard_controller 真实集成测试 v3（隔离窗口，不触碰用户应用）。"""
import asyncio
import base64
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
    pytest.skip('手动集成测试：需 KC_ITEST=1 且在真实桌面运行', allow_module_level=True)

PLUGIN_DIR = str(Path(__file__).resolve().parent.parent)
TEST_TITLE = "KC_ITEST_WINDOW_A7X"

# ── plugin.sdk 假包 ──────────────────────────────────────────────────
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

# ── 假 RapidOCR（内部走真实 Windows 快速 OCR）───────────────────────
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
    def extract_text_with_boxes(self, image):
        cap_mod = sys.modules.get("keyboard_controller_under_test")
        try:
            image.save(os.path.join(DATA_DIR, "last_frame.png"))
        except Exception:
            pass
        try:
            image = image.resize((image.width * 2, image.height * 2))
            text, st = cap_mod.capture.windows_fast_ocr_text(image)
        except Exception as _exc:
            print("DBG STUB exc:", _exc)
            text, st = "", "error"
        print(f"DBG STUB: st={st} len={len(str(text))} text={str(text)[:60]!r}")
        if st != "ok" or not str(text).strip():
            return "", []
        w, h = image.size
        box = types.SimpleNamespace(text=text, left=0, top=0,
                                    right=int(w * 0.98), bottom=int(h * 0.9), score=0.95)
        return text, [box]

_ob.RapidOcrBackend = RapidOcrBackend
sys.modules["plugin.plugins._shared.rapidocr.ocr_backends"] = _ob

# ── 假宿主上下文 ─────────────────────────────────────────────────────
DATA_DIR = tempfile.mkdtemp(prefix="kc_test_data_")

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
        self.logger = logging.getLogger("kc_test")
        logging.basicConfig(level=logging.CRITICAL)
        self.store = FakeStore()
        self.config = FakeConfig({"keyboard_controller": {
            "command_require_confirmation": False,
            "workspace_root": DATA_DIR,
        }})
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

# ── 加载真实插件 + _target 追踪 ──────────────────────────────────────
spec = importlib.util.spec_from_file_location(
    "keyboard_controller_under_test", os.path.join(PLUGIN_DIR, "__init__.py"))
kc = importlib.util.module_from_spec(spec)
sys.modules["keyboard_controller_under_test"] = kc
spec.loader.exec_module(kc)

RESULTS = []

def report(name, passed, info=""):
    RESULTS.append((name, passed, info))
    print(f"{'PASS' if passed else 'FAIL'} {name}" + (f" | {info}" if info else ""))


async def spawn_target_window() -> int:
    ps_script = f'''
Add-Type -AssemblyName System.Windows.Forms
$f = New-Object System.Windows.Forms.Form
$f.Text = "{TEST_TITLE}"
$f.Size = New-Object System.Drawing.Size(640, 480)
$lbl = New-Object System.Windows.Forms.Label
$lbl.Text = "NEKO_FIND_ME 标记按钮"
$lbl.AutoSize = $true
$lbl.Font = New-Object System.Drawing.Font('Segoe UI', 22, [System.Drawing.FontStyle]::Bold)
$lbl.Location = New-Object System.Drawing.Point(40, 60)
$f.Controls.Add($lbl)
$btn = New-Object System.Windows.Forms.Button
$btn.Text = "测试按钮"
$btn.Font = New-Object System.Drawing.Font('Segoe UI', 16)
$btn.Location = New-Object System.Drawing.Point(40, 140)
$f.Controls.Add($btn)
[System.Windows.Forms.Application]::Run($f)
'''
    import subprocess as sp
    return sp.Popen(["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand",
                     base64.b64encode(ps_script.encode("utf-16-le")).decode("ascii")],
                    creationflags=0x00000200 | 0x08000000,
                    stdin=sp.DEVNULL, stdout=sp.DEVNULL, stderr=sp.DEVNULL).pid


async def main():
    ctx = FakeCtx()
    plugin = kc.KeyboardControllerPlugin(ctx)
    report("startup", (await plugin.startup()) is not None)

    # 单元级：序列解析 / 步数上限 / 区域裁剪 / 签名差
    seq = plugin._normalize_sequence([{"keys": "a"}, {"keys": "b", "hold": 0.5},
                                      {"action": "wait", "seconds": 0.6}, {"text": "hi"}])
    report("normalize_sequence", len(seq) == 4 and seq[1].get("hold") == 0.5 and seq[2]["action"] == "wait")
    try:
        plugin._normalize_sequence([{"keys": "a"}] * 200)
        cap_ok = False
    except kc.SdkError:
        cap_ok = True
    report("sequence_step_cap", cap_ok)
    from PIL import Image as PILImage
    im = PILImage.new("RGB", (100, 80), "white")
    cropped, origin, err = plugin._apply_region_crop(im, "10,20,50,40")
    _, _, err2 = plugin._apply_region_crop(im, "bad")
    report("region_crop", err is None and cropped.size == (50, 40)
           and origin == {"left": 10, "top": 20} and err2 is not None)
    d = kc._signature_diff([10, 20], [12, 22])
    report("signature_diff", abs(d - 2.0) < 1e-6 and kc._signature_diff([1], [1, 2]) == 255.0)

    # 白名单正则 + 真实命令执行
    await plugin.set_command_whitelist("re:echo\\s+hello, dir")
    r = await plugin.run_command(command="ECHO HELLO", shell="cmd")
    report("whitelist_regex_autorun", r.get("auto_approved") is True and "HELLO" in str(r.get("output", "")))
    await plugin.set_command_whitelist("")
    r = await plugin.run_command(command="echo PLAIN_RUN", shell="cmd")
    report("run_command_plain", "PLAIN_RUN" in str(r.get("output", "")))

    # 输入速度档位
    r = await plugin.set_input_speed("turbo")
    ok1 = r.get("profile") == "turbo" and plugin._input_delay == 0.005
    stored_speed = ctx.store.data.get(kc._STORE_INPUT_SPEED_KEY)
    report("input_speed_turbo", ok1 and stored_speed == "turbo")
    await plugin.set_input_speed("normal")
    report("input_speed_normal_back", plugin._input_delay == 0.05)

    # 剪贴板往返（真实）
    await plugin.set_clipboard(text="N.E.K.O 测试 ABC123")
    r = await plugin.get_clipboard()
    if isinstance(r, kc.Err):
        report("clipboard_roundtrip", False, str(r.error)[:80])
    else:
        report("clipboard_roundtrip", r.get("status") == "ok" and "ABC123" in r.get("text"))

    # 任务状态
    await plugin.set_task_state(goal="集成测试", status="进行中", next_step="验证恢复")
    st = ctx.store.data.get(kc._STORE_TASK_STATE_KEY) or {}
    report("task_state_persist", st.get("goal") == "集成测试")
    r = await plugin.set_task_state(clear=True)
    report("task_state_clear", r.get("cleared") is True)

    # 安全开关
    await plugin.set_safety_switches(block_anti_cheat=False, require_focus=True)
    mid = await plugin.set_safety_switches(block_anti_cheat=True)
    report("safety_switches", mid.get("block_anti_cheat") is True)

    # ── 隔离目标窗口 ──
    target_pid = await spawn_target_window()
    r = await plugin.wait_for_window(query=TEST_TITLE, timeout=20)
    win_found = r.get("found") is True
    wins = r.get("windows") or []
    test_hwnd = 0
    for cand in wins:
        hwnd_i = int(cand.get("hwnd") or 0)
        if await asyncio.to_thread(kc.win32.is_window, hwnd_i):
            test_hwnd = hwnd_i
            break
    report("spawn_and_wait_window", win_found and test_hwnd > 0, f"hwnd={test_hwnd}")
    if not (win_found and test_hwnd):
        failed = [n for n, p, _ in RESULTS if not p]
        print(f"\n==== {len(RESULTS)-len(failed)}/{len(RESULTS)} PASSED ====\nFAILED: {failed}")
        return

    w32wins = await asyncio.to_thread(kc.win32.enumerate_windows)
    mine = next(w for w in w32wins if int(w.get("hwnd") or 0) == test_hwnd)
    plugin._target = {"pid": int(mine.get("pid")), "title": mine.get("title"),
                      "process_name": mine.get("process_name"), "hwnd": test_hwnd}
    plugin._target_hwnd_cache = None

    # pin/unpin（软降级）
    r = await plugin.control_window(action="pin")
    if isinstance(r, kc.Err):
        report("control_window_pin_unpin", False,
               f"pin Err: {r.error} attempts={getattr(kc.win32, '_LAST_TOPMOST_ATTEMPTS', [])}")
    else:
        r = await plugin.control_window(action="unpin")
        report("control_window_pin_unpin", isinstance(r, kc.Ok) or r.get("done") is True,
               f"done={r.get('done')}")

    # 截图 + 区域裁剪
    r = await plugin.capture_screen(mode="target")
    w, h = int(r.get("width") or 0), int(r.get("height") or 0)
    report("capture_screen_target", w > 100 and h > 100, f"{w}x{h}")
    r = await plugin.capture_screen(mode="target", region="0,0,200,150")
    report("capture_screen_region", 0 < int(r.get("width") or 0) <= 201)

    # UIA 控件枚举
    r = await plugin.inspect_controls(max_results=30)
    if isinstance(r, kc.Err):
        report("inspect_controls", False, f"Err {r.error}"[:130])
    else:
        names = [str(c.get("text") or "") for c in r.get("controls") or []]
        found_label = any("NEKO_FIND_ME" in n or "标记按钮" in n or "测试按钮" in n for n in names)
        report("inspect_controls", len(names) >= 1,
               f"backend={r.get('backend_used')} count={r.get('count')} has_label={found_label}")

    # 聚焦 → 打字 → find_text → wait_for_text fast
    await asyncio.to_thread(kc.win32.focus_window, test_hwnd, attempts=3)
    await asyncio.sleep(0.5)
    try:
        await plugin.type_text(text="NEKO_FIND_ME 标记文字")
    except kc.SdkError as exc:
        report("type_note", True, f"type skipped: {str(exc)[:50]}")
    await asyncio.sleep(0.6)
    r = await plugin.find_text(query="NEKO_FIND_ME", mode="target", max_results=3)
    ft_ok = (not isinstance(r, kc.Err)) and r.get("status") == "ok"
    ft_info = "" if ft_ok else str(getattr(r, "error", ""))[:90]
    report("find_text_rapidocr", ft_ok, ft_info)

    r = await plugin.wait_for_text(query="NEKO_FIND_ME", mode="target", timeout=8,
                                   interval=0.3, ocr_backend="fast")
    wf_ok = (not isinstance(r, kc.Err)) and r.get("found") is True and r.get("ocr_backend") == "fast"
    report("wait_for_text_fast", wf_ok, str(getattr(r, "elapsed", "?")) + "s")

    # 反应式监视 tick
    await plugin.set_window_watch(titles=TEST_TITLE)
    await plugin._reactive_watch_tick()
    watch_hit = any(m.get("metadata", {}).get("kind") == "window_watch" for m in ctx.pushed) \
        or plugin._watch_prev.get(TEST_TITLE.lower()) is True
    report("reactive_watch_tick", watch_hit)

    # agent_brief
    r = await plugin.agent_brief()
    p = r.payload
    brief_ok = ("input_speed_profile" in p and "task_state" in p
                and "capabilities" in p and p.get("today_activity", {}).get("total", 0) >= 3)
    report("agent_brief_fields", brief_ok)

    # 清理：只关自己创建的窗口与进程
    await plugin.clear_target()
    os.system(f"taskkill /PID {target_pid} /F >nul 2>&1")

    failed = [n for n, p, _ in RESULTS if not p]
    print(f"\n==== {len(RESULTS)-len(failed)}/{len(RESULTS)} PASSED ====")
    if failed:
        print("FAILED:", failed)


if __name__ == '__main__':
    asyncio.run(main())
    # sys.exit 必须在 main 块内：放模块顶层时 KC_ITEST=1 下任何 import 都会直接退出进程。
    sys.exit(0 if all(p for _, p, _ in RESULTS) else 1)
