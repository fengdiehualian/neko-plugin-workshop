# -*- coding: utf-8 -*-
"""keyboard_controller 新增逻辑的独立单元测试。

不依赖 plugin.sdk：_key_map/_win32_input 通过包上下文加载（支持相对导入）；
__init__.py 因依赖宿主 SDK，仅做源码/AST 级断言。
"""
from __future__ import annotations

import ast
import importlib.util
import re
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_PKG = "kc_under_test"


def _load(name: str):
    """按包上下文加载插件内模块（支持相对导入），跨平台安全。"""
    if _PKG not in sys.modules:
        pkg = types.ModuleType(_PKG)
        pkg.__path__ = [str(ROOT)]
        sys.modules[_PKG] = pkg
    full = f"{_PKG}.{name}"
    if full in sys.modules:
        return sys.modules[full]
    spec = importlib.util.spec_from_file_location(full, ROOT / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[full] = module
    spec.loader.exec_module(module)
    return module


def _plugin_source() -> str:
    return (ROOT / "__init__.py").read_text(encoding="utf-8")


def _plugin_class_funcs() -> dict:
    src = _plugin_source()
    tree = ast.parse(src)
    cls = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.ClassDef) and n.name == "KeyboardControllerPlugin"
    )
    return {
        n.name: n
        for n in ast.walk(cls)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


# ── 键位解析 ─────────────────────────────────────────────────────────

def test_parse_combo_basic_and_errors():
    km = _load("_key_map")
    modifiers, main_vk = km.parse_combo("ctrl+c")
    assert main_vk > 0 and len(modifiers) == 1
    modifiers2, main_vk2 = km.parse_combo("space")
    assert main_vk2 > 0 and not modifiers2
    raised = False
    try:
        km.parse_combo("not_a_real_key_xx")
    except km.KeySpecError:
        raised = True
    assert raised


def test_supported_key_names_nonempty():
    km = _load("_key_map")
    names = [n.lower() for n in km.supported_key_names()]
    assert names and "ctrl" in names and "space" in names


# ── 输入安全（反作弊黑名单，跨平台可测）─────────────────────────────

def test_input_safety_blocks_anticheat_process():
    w32 = _load("_win32_input")
    reason = w32.input_safety_block_reason(
        pid=1234,
        hwnd=5678,
        process_name="BattlEye Service",
        window_title="Some Game",
    )
    assert reason and "deny marker" in reason.lower()


def test_input_safety_rejects_invalid_target():
    w32 = _load("_win32_input")
    reason = w32.input_safety_block_reason(
        pid=0,
        hwnd=0,
        process_name="notepad.exe",
        window_title="记事本",
    )
    assert reason  # no valid target window


def test_dpi_decorator_applied_to_core_functions():
    w32 = _load("_win32_input")
    for name in (
        "_virtual_screen",
        "enumerate_windows",
        "find_window_for_pid",
        "focus_window",
        "window_rect",
        "window_client_rect",
        "client_to_screen",
        "mouse_move",
        "mouse_click",
        "mouse_drag",
        "mouse_wheel",
        "move_window",
    ):
        fn = getattr(w32, name)
        assert getattr(fn, "__neko_dpi_aware__", False) is True, f"{name} 未套 DPI 装饰器"
        assert fn.__name__ == name  # functools.wraps 保留原名


def test_dpi_decorated_functions_are_callable():
    """回归：装饰器闭包必须真正调用到原函数（曾因 func/fn 笔误全体 NameError，
    导致线上所有注入工具报 Unexpected error）。非 Windows 平台各函数应走
    is_windows() 守卫优雅返回，而不是抛 NameError。"""
    w32 = _load("_win32_input")
    # 只读/无副作用调用，任何平台都安全
    w32.enumerate_windows()
    assert isinstance(w32.is_window(0), bool)
    assert isinstance(w32.window_rect(0), (dict, type(None)))
    assert isinstance(w32.window_client_rect(0), (dict, type(None)))
    assert w32.client_to_screen(0, 1, 1) is None
    sig = w32._virtual_screen()
    assert isinstance(sig, tuple) and len(sig) == 4

    if not w32.is_windows():
        # 注入类仅在非 Windows（CI）上验证守卫早退，避免测试机真动鼠标
        assert w32.mouse_move(0, 0) is None
        assert w32.mouse_click(0, 0) is None
        assert w32.mouse_wheel(0, 0) is None
        assert w32.move_window(0, 0, 0, 100, 100) is False
        assert w32.focus_window(0, attempts=1) is False


# ── 插件层源码断言（__init__.py 依赖宿主 SDK，不直接导入）───────────

def test_input_caps_constants_present_and_sane():
    src = _plugin_source()
    expected = {
        "_MAX_KEY_COUNT = 50": 50,
        "_MAX_CLICKS = 10": 10,
        "_MAX_HOLD_SECONDS = 120.0": 120.0,
        "_MAX_WAIT_SECONDS = 120.0": 120.0,
        "_MAX_SEQUENCE_STEPS = 100": 100,
        "_MAX_DRAG_STEPS = 200": 200,
    }
    for needle in expected:
        assert needle in src, needle
    # 上限引用必须真实接线（不止定义）
    for const in ("_MAX_KEY_COUNT", "_MAX_CLICKS", "_MAX_SEQUENCE_STEPS"):
        assert src.count(const) >= 3, f"{const} 定义后未被使用"


def test_sequence_supports_wait_hold_clipboard_threshold():
    funcs = _plugin_class_funcs()
    norm = ast.unparse(funcs["_normalize_sequence"])
    for needle in ("'wait'", "'hold'", "_MAX_SEQUENCE_STEPS", "_MAX_WAIT_SECONDS"):
        assert needle in norm, needle
    ps = ast.unparse(funcs["press_sequence"])
    assert "action == 'wait'" in ps
    assert "clipboard_threshold=self._clipboard_paste_min_chars" in ps


def test_launch_app_tool_exists_and_gated():
    funcs = _plugin_class_funcs()
    la = ast.unparse(funcs["launch_app"])
    assert "_launch_app_enabled" in la
    assert "spawn_detached" in la
    assert "_persist_target" in la
    assert "input_safety_block_reason" in la  # 设目标前过安全检查
    toml_text = (ROOT / "plugin.toml").read_text(encoding="utf-8")
    assert "launch_app_enabled = true" in toml_text


def test_backend_surface_parity():
    """win32 / linux 两个后端必须覆盖主模块引用的全部符号。

    防止“只在一个后端加了函数”的漂移——那会让另一平台运行时 AttributeError。
    """
    src = (ROOT / "__init__.py").read_text(encoding="utf-8")
    used = set(re.findall(r"\bwin32\.([A-Za-z_][A-Za-z0-9_]*)", src))
    assert used, "未发现 win32.* 引用，正则或结构变了"

    # 先注册包与公共依赖（_linux_input 有相对导入）
    _load("_key_map")
    linux_mod = _load("_linux_input")
    win32_mod = _load("_win32_input")

    for label, module in (("linux", linux_mod), ("win32", win32_mod)):
        exported = set(dir(module))
        missing = sorted(used - exported)
        assert not missing, f"{label} 后端缺少符号: {missing}"


def test_new_tools_inventory():
    funcs = _plugin_class_funcs()
    expected = {
        # 本文件覆盖的核心新增面
        "set_fullscreen", "set_safety_switches", "see_screen",
        "click_image", "click_text", "hold_mouse",
        "get_clipboard", "set_clipboard", "control_window",
        "wait_for_text", "wait_for_image", "wait_for_idle",
        "agent_brief", "recent_actions", "move_window", "launch_app",
        # 底层支撑（类方法）
        "_poll_frame_until", "_capture_frame", "_load_template_image",
        "_frame_point_to_screen",
    }
    missing = expected - set(funcs)
    assert not missing, f"缺少方法: {missing}"

    # 模块级辅助函数（不在类内）
    src = _plugin_source()
    tree = ast.parse(src)
    mod_funcs = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
    assert {"_frame_signature", "_signature_diff"} <= mod_funcs
