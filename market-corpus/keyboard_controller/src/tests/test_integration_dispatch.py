# -*- coding: utf-8 -*-
"""合并工具派发层验证：25 个 tool_* 全部走一遍（复用 kc_itest 桩）。"""
import asyncio
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
    pytest.skip('手动派发集成测试：需 KC_ITEST=1 且在真实桌面运行', allow_module_level=True)

# 本插件源码根目录（与 test_integration_mouse/manual 一致的相对定位；
# 之前硬编码了开发机的临时目录绝对路径，换机器必挂且测的不是本插件）。
PLUGIN_DIR = str(Path(__file__).resolve().parent.parent)
TEST_TITLE = "KC_DISPATCH_TEST_C9"

# ── 桩（同 kc_itest）────────────────────────────────────────────────
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

DATA_DIR = tempfile.mkdtemp(prefix="kc_dispatch_")

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
        self.logger = logging.getLogger("kc_dispatch")
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

spec = importlib.util.spec_from_file_location(
    "keyboard_controller_under_test", os.path.join(PLUGIN_DIR, "__init__.py"))
kc = importlib.util.module_from_spec(spec)
sys.modules["keyboard_controller_under_test"] = kc
spec.loader.exec_module(kc)

RESULTS = []

class _E(kc.Err):
    def __init__(self, msg): super().__init__(msg)

async def safe(coro):
    """生产环境中真实 @llm_tool 会把 SdkError 转 Err；测试里等价捕获。"""
    try:
        return await coro
    except kc.SdkError as exc:
        return _E(str(exc))

def report(name, passed, info=""):
    RESULTS.append((name, passed, info))
    print(f"{'PASS' if passed else 'FAIL'} {name}" + (f" | {info}" if info else ""))


async def main():
    ctx = FakeCtx()
    p = kc.KeyboardControllerPlugin(ctx)
    await p.startup()

    is_win = sys.platform == "win32"

    # 1. target: set(用 find_windows 找到的真实 pid)/get/clear + 非法 action
    r = await safe(p.tool_target(action="bogus"))
    report("target_bad_action", isinstance(r, kc.Err))
    if is_win:
        r = await safe(p.tool_find_windows(query=""))
        wins = (r.get("windows") or []) if isinstance(r, kc.Ok) else []
        some_pid = int(wins[0]["pid"]) if wins else 0
        r = await safe(p.tool_target(action="set", pid=some_pid))
        report("target_set", some_pid > 0 and not isinstance(r, kc.Err),
               str(getattr(r, "error", ""))[:40] if isinstance(r, kc.Err) else f"pid={some_pid}")
        r = await safe(p.tool_target(action="get"))
        report("target_get", isinstance(r, kc.Ok))
        r = await safe(p.tool_target(action="clear"))
        report("target_clear", isinstance(r, kc.Ok))

    # 2. clipboard
    r = await safe(p.tool_clipboard(op="set", text="dispatch-test-123"))
    r = await safe(p.tool_clipboard(op="get"))
    ok = isinstance(r, kc.Ok) and "dispatch-test-123" in str(r.get("text", ""))
    report("clipboard_roundtrip", ok)
    r = await safe(p.tool_clipboard(op="bogus"))
    report("clipboard_bad_op", isinstance(r, kc.Err))

    # 3. task_state（set 为后台落盘，最终一致——让出事件循环再读）
    await safe(p.tool_task_state(action="set", goal="合并工具验证", status="进行中", next_step="跑完派发测试"))
    await asyncio.sleep(0.1)
    r = await safe(p.tool_task_state(action="get"))
    st = (r.get("task_state") or {}) if isinstance(r, kc.Ok) else {}
    report("task_state_roundtrip", isinstance(st, dict) and "合并工具验证" in str(st.get("goal", "")))
    await safe(p.tool_task_state(action="set", clear=True))

    # 4. context
    r = await safe(p.tool_context(kind="brief"))
    report("context_brief", isinstance(r, kc.Ok))
    r = await safe(p.tool_context(kind="recent", n=3))
    report("context_recent", isinstance(r, kc.Ok))
    r = await safe(p.tool_context(kind="bogus"))
    report("context_bad_kind", isinstance(r, kc.Err))

    # 5. file
    r = await safe(p.tool_file(op="write", path="dispatch_t.txt", content="hello-v2"))
    r = await safe(p.tool_file(op="read", path="dispatch_t.txt"))
    report("file_write_read", isinstance(r, kc.Ok) and "hello-v2" in str(r.get("content", "")))
    r = await safe(p.tool_file(op="list", path=""))
    report("file_list", isinstance(r, kc.Ok))
    r = await safe(p.tool_file(op="bogus", path="x"))
    report("file_bad_op", isinstance(r, kc.Err))

    # 6. press 非法键 → Err；type 空文本 → Err（不依赖窗口）
    r = await safe(p.tool_press(keys="not_a_real_key_xyz"))
    report("press_bad_key", isinstance(r, kc.Err))
    r = await safe(p.tool_type(text=""))
    report("type_empty", isinstance(r, kc.Err))
    r = await safe(p.tool_sequence(sequence=[]))
    report("sequence_empty", isinstance(r, kc.Err))

    # 7. wait_for 非法 kind；window 等不存在窗口 1 秒
    r = await safe(p.tool_wait_for(kind="bogus"))
    report("wait_bad_kind", isinstance(r, kc.Err))
    r = await safe(p.tool_wait_for(kind="window", query="KC_NO_SUCH_WINDOW_XY", timeout=1, interval=0.3))
    ok = isinstance(r, kc.Ok) and r.get("found") is False
    report("wait_window_timeout", ok)

    # 8. mouse 非法 action；wheel 缺坐标
    r = await safe(p.tool_mouse(action="bogus"))
    report("mouse_bad_action", isinstance(r, kc.Err))
    r = await safe(p.tool_mouse(action="click"))
    report("mouse_click_missing_xy", isinstance(r, kc.Err))
    if is_win:
        # move 需要先有目标窗口
        r = await safe(p.tool_find_windows(query=""))
        wins = (r.get("windows") or []) if isinstance(r, kc.Ok) else []
        if wins:
            await safe(p.tool_target(action="set", pid=int(wins[0]["pid"])))
        import ctypes
        pt = ctypes.wintypes.POINT()
        ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
        r = await safe(p.tool_mouse(action="move", x=int(pt.x), y=int(pt.y)))
        report("mouse_move_dispatch", not isinstance(r, kc.Err),
               str(getattr(r, "error", ""))[:40] if isinstance(r, kc.Err) else "ok")
        await safe(p.tool_target(action="clear"))

    # 9. screen 非法 op；window 非法 action
    r = await safe(p.tool_screen(op="bogus"))
    report("screen_bad_op", isinstance(r, kc.Err))
    r = await safe(p.tool_window(action="bogus"))
    report("window_bad_action", isinstance(r, kc.Err))

    # 10. diary
    r = await safe(p.tool_diary(action="status"))
    report("diary_status", isinstance(r, kc.Ok))
    r = await safe(p.tool_diary(action="note", detail="派发测试随笔"))
    report("diary_note", isinstance(r, kc.Ok))
    r = await safe(p.tool_diary(action="read", date=""))
    report("diary_read", isinstance(r, kc.Ok))
    r = await safe(p.tool_diary(action="bogus"))
    report("diary_bad_action", isinstance(r, kc.Err))

    # 11. find/click/screen-ocr/control_action/inspect：无窗口时优雅报错（不崩）
    r = await safe(p.tool_find(kind="text", query="x"))
    report("find_no_target_graceful", isinstance(r, kc.Err))
    r = await safe(p.tool_click(method="bogus"))
    report("click_bad_method", isinstance(r, kc.Err))
    r = await safe(p.tool_control_action(op="bogus", name="x"))
    report("control_action_bad_op", isinstance(r, kc.Err))
    r = await safe(p.tool_run_command(command="echo dispatch-ok"))
    report("run_command_dispatch", isinstance(r, kc.Ok) and "dispatch-ok" in str(r.get("stdout", "")) + str(r.get("output", "")))
    r = await safe(p.tool_set_command_whitelist(prefixes="echo"))
    report("whitelist_dispatch", not isinstance(r, kc.Err))
    r = await safe(p.tool_set_input_speed(profile="turbo"))
    r2 = await safe(p.tool_set_input_speed(profile="normal"))
    report("speed_dispatch", not isinstance(r, kc.Err) and not isinstance(r2, kc.Err))
    r = await safe(p.tool_set_window_watch(titles=""))
    report("watch_dispatch", not isinstance(r, kc.Err))
    r = await safe(p.tool_launch_app(command=""))
    report("launch_app_empty_cmd", isinstance(r, kc.Err))

    failed = [n for n, ok2, _ in RESULTS if not ok2]
    print(f"\n==== {len(RESULTS)-len(failed)}/{len(RESULTS)} PASSED ====")
    if failed:
        print("FAILED:", failed)


if __name__ == '__main__':
    asyncio.run(main())
    # sys.exit 必须在 main 块内：放模块顶层时 KC_ITEST=1 下任何 import
    # 都会直接退出进程（pytest 收集阶段即中断）。
    sys.exit(0 if all(ok for _, ok, _ in RESULTS) else 1)
