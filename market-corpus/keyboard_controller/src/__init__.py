"""按键控制插件 (Keyboard Controller) v0.5.0

让猫娘通过键盘/鼠标操作电脑上的游戏或软件，并支持：
- 截图 + OCR 读屏 + 像素色值/帧差异/多尺度模板匹配/GIF 录屏
- 手柄模拟（XInput 虚拟按钮/摇杆/扳机）
- 进程管理（列表/查详情/杀进程/等待退出）
- HTTP 网络请求 + 文件下载 + Ping
- 系统通知推送
- 条件触发器（颜色/变化/文字 → 自动执行动作）
- 操作宏录制与回放

安全边界：
- 仅对已 set_target 的窗口注入（除非配置 allow_unguided_input）
- 反作弊进程名/标题拒绝注入
- 目标进程提权高于宿主时拒绝注入
- 注入前必须成功聚焦目标窗口，否则 Err
"""

from __future__ import annotations  # noqa: I001

import asyncio
import json
import os
import re
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from PIL import Image

from plugin.sdk.plugin import (
    Err,
    NekoPluginBase,
    Ok,
    SdkError,
    lifecycle,
    neko_plugin,
    plugin_entry,
    timer_interval,
    tr,
    ui,
    unwrap_or,
)

from . import _audio_analysis as audio_analysis
from . import _command_exec as command_exec
from . import _diary as diary
from . import _file_ops as file_ops
from . import _gamepad as gamepad
from . import _network as network
from . import _notify as notify_mod
from . import _process as process_mgr
from . import _screen_advanced as screen_advanced
from . import _screen_capture as capture
from . import _template_match as template_match
from ._input_backend import backend as win32
from ._key_map import (
    KeySpecError,
    parse_combo,
    supported_key_names,
)
from ._macro import (
    Macro,
    MacroRecorder,
    macro_template_click_sequence,
    macro_template_launch_game,
    macro_template_loop,
    macro_template_press_sequence,
    play_macro,
)
from ._tools_v2 import ConsolidatedToolsMixin
from ._triggers import Trigger, TriggerEngine  # noqa: I001

_STORE_TARGET_KEY = "target"
_STORE_CONFIRM_KEY = "command_require_confirmation"
_STORE_DIARY_KEY = "diary_enabled"
_STORE_DIARY_DAY_KEY = "diary_last_written_day"
_STORE_SAFETY_ANTI_CHEAT_KEY = "safety_block_anti_cheat"
_STORE_SAFETY_ELEVATED_KEY = "safety_block_elevated_target"
_STORE_SAFETY_FOCUS_KEY = "safety_require_focus"
_STORE_TASK_STATE_KEY = "task_state"
_MAX_TASK_FIELD_CHARS = 500
_FULLSCREEN_PID = -1
_FULLSCREEN_TITLE = "全屏模式（整块屏幕）"

# ── 输入强度上限（防 LLM 参数失控长时间占用真实键鼠）────────────────
_MAX_KEY_COUNT = 50        # 单次最多按键次数
_MAX_CLICKS = 10           # 单次最多连点次数
_MAX_HOLD_SECONDS = 120.0  # 长按时长上限（秒）
_MAX_WAIT_SECONDS = 120.0  # 序列 wait 步骤时长上限（秒）
_MAX_SEQUENCE_STEPS = 100  # 序列步数上限
_MAX_DRAG_STEPS = 200      # 拖拽插值步数上限
_MAX_SET_CLIPBOARD_CHARS = 200_000  # set_clipboard 文本长度上限

# ── 用户可选：输入速度档位 ──────────────────────────────────────────
_INPUT_SPEED_PROFILES: dict[str, tuple[float, float]] = {
    #            键间隔     打字字符间隔
    "slow":   (0.10, 0.03),
    "normal": (0.05, 0.01),
    "fast":   (0.02, 0.005),
    "turbo":  (0.005, 0.001),
}
_STORE_INPUT_SPEED_KEY = "input_speed_profile"
_STORE_CMD_WHITELIST_KEY = "command_auto_approve_prefixes"
_STORE_WATCH_TITLES_KEY = "reactive_watch_titles"
_REACTIVE_WATCH_INTERVAL = 10


def _frame_signature(frame, size: tuple[int, int] = (96, 54)) -> list[int]:
    """粗粒度灰度签名（PIL 缩放后取像素值），用于画面稳定性比较。

    96x54（而非更小的 64x36）是为了保留居中小加载圈/进度条的能量，
    避免加载动画在降采样后被抹平造成假稳定。"""
    small = frame.convert("L").resize(size)
    return list(small.getdata())


def _signature_diff(a: list[int], b: list[int]) -> float:
    """两个签名的平均绝对差（0~255）。尺寸不符视为完全不同。"""
    if not a or not b or len(a) != len(b):
        return 255.0
    total = sum(abs(x - y) for x, y in zip(a, b))
    return total / len(a)
_PENDING_MAX = 20
_PENDING_TTL_SECONDS = 600.0


def _is_windows() -> bool:
    """历史命名；语义为“当前平台受支持”（Windows / Linux X11 桌面）。"""
    return sys.platform in ("win32", "linux")


@neko_plugin
class KeyboardControllerPlugin(ConsolidatedToolsMixin, NekoPluginBase):
    """向游戏/软件窗口注入键盘/鼠标输入，并提供截图 OCR 读屏。"""

    def __init__(self, ctx):
        super().__init__(ctx)
        self.logger = ctx.logger
        self._target: Optional[dict[str, Any]] = None
        self._target_hwnd_cache: Optional[tuple[int, int, dict[str, Any], float]] = None
        self._cfg: dict[str, Any] = {}
        self._allow_unguided = False
        self._focus_retries = 3
        self._input_delay = 0.05
        self._type_delay = 0.01
        self._save_screenshots = True
        self._clipboard_paste_min_chars = 80
        self._launch_app_enabled = True
        self._watch_titles: list[str] = []
        self._watch_prev: dict[str, bool] = {}
        self._block_anti_cheat = True
        self._block_elevated = True
        self._require_focus = True
        self._diary: Optional[diary.DiaryLog] = None
        self._diary_enabled = True
        self._diary_dir = "memories"
        self._diary_flush_seconds = diary.DEFAULT_AUTO_FLUSH_SECONDS
        self._diary_last_flush = 0.0

        # ── v0.5.0 新增子系统 ──────────────────────────────────────────
        self._trigger_engine = TriggerEngine(logger=None)
        self._trigger_engine.set_action_handler(self._macro_execute_step)
        self._macro_recorder = MacroRecorder()
        self._gif_recorder: Optional[screen_advanced._GifRecorder] = None
        self._prev_frame = None  # 帧差异参考帧
        self._pending_commands: list[dict[str, Any]] = []

    # ── 生命周期 ──────────────────────────────────────────────────────

    @lifecycle(id="startup")
    async def startup(self, **_):
        cfg = await self.config.dump(timeout=5.0)
        self._cfg = cfg if isinstance(cfg, dict) else {}
        kb_cfg = self._cfg.get("keyboard_controller", {})
        if not isinstance(kb_cfg, dict):
            kb_cfg = {}

        self._allow_unguided = bool(kb_cfg.get("allow_unguided_input", False))
        self._focus_retries = max(1, int(kb_cfg.get("focus_retries", 3)))
        self._input_delay = float(kb_cfg.get("input_delay_seconds", 0.05))
        self._type_delay = float(kb_cfg.get("default_type_delay_seconds", 0.01))
        self._save_screenshots = bool(kb_cfg.get("save_screenshots", True))
        self._clipboard_paste_min_chars = max(1, int(kb_cfg.get("clipboard_paste_min_chars", 80)))
        self._launch_app_enabled = bool(kb_cfg.get("launch_app_enabled", True))
        self._audio_capture_seconds = float(kb_cfg.get("audio_capture_seconds", audio_analysis._CAPTURE_SECONDS_DEFAULT))
        self._command_timeout = float(kb_cfg.get("command_timeout_seconds", command_exec.DEFAULT_TIMEOUT_SECONDS))
        self._command_max_output = int(kb_cfg.get("command_max_output_chars", command_exec.DEFAULT_MAX_OUTPUT_CHARS))
        self._command_default_shell = str(kb_cfg.get("command_default_shell", "auto") or "auto").strip()
        self._command_require_confirmation = bool(kb_cfg.get("command_require_confirmation", True))
        self._command_auto_prefixes: list[str] = [
            p.strip().lower() for p in str(kb_cfg.get("command_auto_approve_prefixes", "") or "").replace("，", ",").split(",")
            if p.strip()
        ]

        # ── 输入速度档位（配置默认 → store 覆盖）────────────────────
        self._input_speed_profile = str(kb_cfg.get("input_speed_profile", "normal") or "normal").strip().lower()
        if self._input_speed_profile not in _INPUT_SPEED_PROFILES:
            self._input_speed_profile = "normal"

        # ── 安全边界开关（默认全开）─────────────────────────────────
        self._block_anti_cheat = bool(kb_cfg.get("safety_block_anti_cheat", True))
        self._block_elevated = bool(kb_cfg.get("safety_block_elevated_target", True))
        self._require_focus = bool(kb_cfg.get("safety_require_focus", True))
        self._pending_commands: list[dict[str, Any]] = []
        self._pending_lock = asyncio.Lock()

        # ── 日记 ─────────────────────────────────────────────────────
        stored_diary_enabled = unwrap_or(await self.store.get(_STORE_DIARY_KEY), None)
        if isinstance(stored_diary_enabled, bool):
            self._diary_enabled = stored_diary_enabled
        else:
            self._diary_enabled = bool(kb_cfg.get("diary_enabled", True))
            await self.store.set(_STORE_DIARY_KEY, self._diary_enabled)
        self._diary_dir = str(kb_cfg.get("diary_dir", "memories") or "memories").strip()
        self._diary_flush_seconds = max(
            30, int(kb_cfg.get("diary_auto_flush_seconds", diary.DEFAULT_AUTO_FLUSH_SECONDS))
        )
        self._diary = diary.DiaryLog(
            enabled=self._diary_enabled,
            max_events_per_day=int(kb_cfg.get("diary_max_events_per_day", diary.DEFAULT_MAX_EVENTS_PER_DAY)),
            locale=str(kb_cfg.get("diary_locale", "zh-CN") or "zh-CN"),
        )
        self._diary_last_flush = time.time()

        self._workspace_root = str(kb_cfg.get("workspace_root", "") or "").strip()
        if not _is_windows():
            self.logger.warning("keyboard_controller only supports Windows; input entries will fail")
            return Ok({"status": "unsupported_platform", "platform": sys.platform})

        stored = unwrap_or(await self.store.get(_STORE_TARGET_KEY), None)
        if isinstance(stored, dict):
            stored_pid = int(stored.get("pid") or 0)
            if stored_pid > 0 or stored_pid == _FULLSCREEN_PID:
                self._target = dict(stored)

        stored_confirm = unwrap_or(await self.store.get(_STORE_CONFIRM_KEY), None)
        if isinstance(stored_confirm, bool):
            self._command_require_confirmation = stored_confirm
        else:
            default_confirm = bool(kb_cfg.get("command_require_confirmation", True))
            self._command_require_confirmation = default_confirm
            await self.store.set(_STORE_CONFIRM_KEY, default_confirm)

        for store_key, attr in (
            (_STORE_SAFETY_ANTI_CHEAT_KEY, "_block_anti_cheat"),
            (_STORE_SAFETY_ELEVATED_KEY, "_block_elevated"),
            (_STORE_SAFETY_FOCUS_KEY, "_require_focus"),
        ):
            stored_flag = unwrap_or(await self.store.get(store_key), None)
            if isinstance(stored_flag, bool):
                setattr(self, attr, stored_flag)
            else:
                await self.store.set(store_key, getattr(self, attr))

        # 输入速度档位 + 命令白名单（store 覆盖配置默认）
        stored_speed = unwrap_or(await self.store.get(_STORE_INPUT_SPEED_KEY), None)
        if isinstance(stored_speed, str) and stored_speed in _INPUT_SPEED_PROFILES:
            self._input_speed_profile = stored_speed
        else:
            await self.store.set(_STORE_INPUT_SPEED_KEY, self._input_speed_profile)
        stored_prefixes = unwrap_or(await self.store.get(_STORE_CMD_WHITELIST_KEY), None)
        if isinstance(stored_prefixes, list) and all(isinstance(x, str) for x in stored_prefixes):
            self._command_auto_prefixes = [x for x in stored_prefixes if x.strip()]
        else:
            await self.store.set(_STORE_CMD_WHITELIST_KEY, self._command_auto_prefixes)
        stored_watch = unwrap_or(await self.store.get(_STORE_WATCH_TITLES_KEY), None)
        if isinstance(stored_watch, list) and all(isinstance(x, str) for x in stored_watch):
            self._watch_titles = [x for x in stored_watch if x.strip()]
            self._watch_prev = {}
        else:
            await self.store.set(_STORE_WATCH_TITLES_KEY, self._watch_titles)
        profile_delays = _INPUT_SPEED_PROFILES[self._input_speed_profile]
        self._input_delay, self._type_delay = profile_delays

        default_window = str(kb_cfg.get("default_target_window", "") or "").strip()
        if self._target is None and default_window:
            found = await self._auto_find_target(default_window)
            if found is not None:
                self._target = found

        self.logger.info(
            "keyboard_controller started, target={} allow_unguided={} anti_cheat_block={} elevated_block={} focus_required={}",
            (self._target or {}).get("pid"),
            self._allow_unguided,
            self._block_anti_cheat,
            self._block_elevated,
            self._require_focus,
        )
        return Ok({"status": "running", "target": self._target})

    @lifecycle(id="shutdown")
    def shutdown(self, **_):
        try:
            capture.close_ocr_backend()
        except Exception:
            pass
        try:
            if self._diary is not None and self._diary.enabled():
                root = self._diary_dir_path()
                day = datetime.now().strftime("%Y-%m-%d")
                self._diary.flush_day(root, day)
        except Exception:
            pass
        return Ok({"status": "shutdown"})

    # ── 内部辅助 ───────────────────────────────────────────────────────

    async def _auto_find_target(self, query: str) -> Optional[dict[str, Any]]:
        windows = await asyncio.to_thread(win32.enumerate_windows)
        needle = query.strip().lower()
        for win in windows:
            title = str(win.get("title") or "").lower()
            proc = str(win.get("process_name") or "").lower()
            if needle and (needle in title or needle in proc):
                return win
        return None

    async def _persist_target(self, target: Optional[dict[str, Any]]) -> None:
        self._target_hwnd_cache = None
        if target is None:
            await self.store.delete(_STORE_TARGET_KEY)
        else:
            await self.store.set(_STORE_TARGET_KEY, target)

    # ── 日记辅助 ─────────────────────────────────────────────────────

    def _diary_dir_path(self) -> Path:
        base = self.data_path()
        return Path(base).joinpath(self._diary_dir)

    def _diary_record(self, kind: str, detail: str, *, ok: bool = True) -> None:
        if self._diary is None:
            return
        try:
            self._diary.record(kind, detail, ok=ok)
        except Exception as exc:
            self.logger.debug("diary record skipped: {}", exc)

    async def _diary_flush_if_due(self, *, force: bool = False) -> bool:
        """若距上次写盘超过间隔（或 force），把今天的事件落到磁盘。"""
        if self._diary is None or not self._diary.enabled():
            return False
        now = time.time()
        if not force and now - self._diary_last_flush < self._diary_flush_seconds:
            return False
        try:
            root = self._diary_dir_path()
            day = datetime.now().strftime("%Y-%m-%d")
            path = await asyncio.to_thread(self._diary.flush_day, root, day)
            self._diary_last_flush = now
            if path is not None:
                await self.store.set(_STORE_DIARY_DAY_KEY, day)
            return path is not None
        except Exception as exc:
            self.logger.debug("diary flush failed: {}", exc)
            return False

    def _require_operable_window(self) -> tuple[int, dict[str, Any]]:
        """解析注入目标，返回 (hwnd, window)。未满足安全边界时抛 SdkError。"""
        if not _is_windows():
            raise SdkError("仅支持 Windows / Linux(X11) 桌面环境")

        if self._target is not None:
            pid = int(self._target.get("pid") or 0)
            if pid == _FULLSCREEN_PID:
                # 全屏模式：不锁定窗口，键盘发往前台、鼠标按屏幕绝对坐标；
                # 安全检查针对当前前台窗口进程。
                foreground = self._foreground_window()
                if foreground is None:
                    raise SdkError("全屏模式下没有可校验的前台窗口，请先点击任意窗口")
                window = foreground
                hwnd = int(window.get("hwnd") or 0)
            elif pid <= 0:
                raise SdkError("目标窗口 pid 无效")
            else:
                # TTL 句柄缓存：连点/序列等突发调用在 2 秒窗口内复用句柄，
                # 只做 IsWindow 廉价校验，跳过 EnumWindows 全量遍历。
                cached = self._target_hwnd_cache
                if (
                    cached is not None
                    and cached[0] == pid
                    and time.time() - cached[3] < 2.0
                    and win32.is_window(cached[1])
                ):
                    return cached[1], cached[2]
                window = win32.find_window_for_pid(pid)
                if window is None:
                    self._target_hwnd_cache = None
                    raise SdkError(f"找不到 pid={pid} 的可见窗口，目标可能已关闭")
                hwnd = int(window.get("hwnd") or 0)
                if hwnd <= 0:
                    raise SdkError("无法解析目标窗口句柄")
                self._target_hwnd_cache = (pid, hwnd, window, time.time())
        elif self._allow_unguided:
            foreground = self._foreground_window()
            if foreground is None:
                raise SdkError("没有可操作的前台窗口")
            window = foreground
            hwnd = int(window.get("hwnd") or 0)
        else:
            raise SdkError("尚未设置目标窗口，请先调用 set_target")

        block = win32.input_safety_block_reason(
            pid=int(window.get("pid") or 0),
            hwnd=hwnd,
            process_name=str(window.get("process_name") or ""),
            window_title=str(window.get("title") or ""),
            block_anti_cheat=self._block_anti_cheat,
            block_elevated=self._block_elevated,
        )
        if block:
            raise SdkError(f"安全策略拒绝注入：{block}")

        return hwnd, window

    def _foreground_window(self) -> Optional[dict[str, Any]]:
        if not _is_windows():
            return None
        return win32.foreground_window()

    def _focus_or_raise(self, hwnd: int) -> None:
        focused = win32.focus_window(hwnd, attempts=self._focus_retries, retry_delay=0.25)
        if not focused and self._require_focus:
            raise SdkError("无法聚焦目标窗口（前台窗口未切换到目标），为避免误输入已取消注入")

    def _normalize_sequence(self, steps: Any) -> list[dict[str, Any]]:
        if not isinstance(steps, list) or not steps:
            raise SdkError("sequence 必须是包含按键步骤的数组")
        normalized: list[dict[str, Any]] = []
        if len(steps) > _MAX_SEQUENCE_STEPS:
            raise SdkError(f"序列步骤过多（{_MAX_SEQUENCE_STEPS} 步上限），请拆分多次调用")
        for step in steps:
            if isinstance(step, str):
                normalized.append({"keys": step})
            elif isinstance(step, dict):
                keys = step.get("keys")
                has_text = step.get("text") is not None
                action = str(step.get("action") or "").strip().lower()
                has_mouse = action in ("click", "move", "drag", "wheel")
                has_wait = action == "wait"
                if not keys and not has_text and not has_mouse and not has_wait:
                    raise SdkError("sequence 步骤需要 keys、text 或 action(click/move/drag/wheel/wait) 之一")
                item: dict[str, Any] = {}
                if keys:
                    item["keys"] = keys
                if step.get("count") is not None:
                    item["count"] = min(_MAX_KEY_COUNT, max(1, int(step["count"])))
                if step.get("delay") is not None:
                    try:
                        item["delay"] = max(0.0, float(step["delay"]))
                    except (TypeError, ValueError):
                        raise SdkError("delay 必须是秒数（数字）")
                if has_wait:
                    item["action"] = "wait"
                    raw_seconds = step.get("seconds") if step.get("seconds") is not None else step.get("delay")
                    try:
                        item["seconds"] = min(_MAX_WAIT_SECONDS, max(0.05, float(raw_seconds or 1.0)))
                    except (TypeError, ValueError):
                        raise SdkError("wait 步骤需要数字 seconds（或 delay）")
                    normalized.append(item)
                    continue
                if step.get("hold") is not None:
                    try:
                        item["hold"] = min(_MAX_HOLD_SECONDS, max(0.05, float(step["hold"])))
                    except (TypeError, ValueError):
                        raise SdkError("hold 必须是按住秒数（数字）")
                    if not keys:
                        raise SdkError("hold 只支持按键步骤（需要 keys）")
                if has_text:
                    item["text"] = str(step["text"])
                if has_mouse:
                    item["action"] = action
                    if step.get("x") is not None:
                        item["x"] = int(step["x"])
                    if step.get("y") is not None:
                        item["y"] = int(step["y"])
                    if step.get("x2") is not None:
                        item["x2"] = int(step["x2"])
                    if step.get("y2") is not None:
                        item["y2"] = int(step["y2"])
                    if step.get("button") is not None:
                        item["button"] = str(step["button"])
                    if step.get("delta") is not None:
                        item["delta"] = int(step["delta"])
                    if step.get("steps") is not None:
                        item["steps"] = int(step["steps"])
                normalized.append(item)
            else:
                raise SdkError(f"不支持的 sequence 步骤类型: {type(step).__name__}")
        return normalized

    # ── 窗口定位 ───────────────────────────────────────────────────────

    @ui.action(
        label=tr("actions.findWindows.label", default="Find windows"),
        icon="F",
        group="target",
        order=10,
        refresh_context=False,
    )
    @plugin_entry(
        id="find_windows",
        name=tr("entries.findWindows.name", default="查找窗口"),
        description="按窗口标题或进程名关键字搜索可见窗口，返回 pid、标题、进程名。",
        input_schema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "标题/进程名关键字，留空则列出全部可见窗口",
                },
            },
            "required": ["query"],
        },
    )
    async def find_windows(self, query: str = "", **_) -> Any:
        if not _is_windows():
            return Err(SdkError("仅支持 Windows / Linux(X11) 桌面环境"))
        windows = await asyncio.to_thread(win32.enumerate_windows)
        needle = str(query or "").strip().lower()
        if needle:
            windows = [
                w for w in windows
                if needle in str(w.get("title") or "").lower()
                or needle in str(w.get("process_name") or "").lower()
            ]
        windows = sorted(windows, key=lambda w: (
            (w.get("rect") or {}).get("right", 0) - (w.get("rect") or {}).get("left", 0),
            -(w.get("rect") or {}).get("top", 0),
        ), reverse=True)
        limited = windows[:50]
        return Ok({
            "count": len(limited),
            "total": len(windows),
            "windows": [
                {
                    "pid": int(w.get("pid") or 0),
                    "title": str(w.get("title") or ""),
                    "process_name": str(w.get("process_name") or ""),
                    "hwnd": int(w.get("hwnd") or 0),
                }
                for w in limited
            ],
        })

    @ui.action(
        label=tr("actions.setTarget.label", default="Set target"),
        icon="T",
        group="target",
        order=20,
        refresh_context=True,
    )
    @plugin_entry(
        id="set_target",
        name=tr("entries.setTarget.name", default="设置目标窗口"),
        description="锁定一个目标窗口，之后所有按键/鼠标操作和窗口截图都注入到它。按 pid 或窗口标题关键字定位。",
        input_schema={
            "type": "object",
            "properties": {
                "pid": {
                    "type": "integer",
                    "description": "目标进程的 pid（优先）",
                },
                "query": {
                    "type": "string",
                    "description": "窗口标题/进程名关键字；pid 未提供时用关键字搜索第一个匹配项",
                },
            },
        },
    )
    async def set_target(self, pid: int | None = None, query: str = "", **_) -> Any:
        if not _is_windows():
            return Err(SdkError("仅支持 Windows / Linux(X11) 桌面环境"))
        if pid and int(pid) > 0:
            window = await asyncio.to_thread(win32.find_window_for_pid, int(pid))
            if window is None:
                return Err(SdkError(f"找不到 pid={pid} 的可见窗口"))
        else:
            needle = str(query or "").strip()
            if not needle:
                return Err(SdkError("请提供 pid 或窗口关键字"))
            window = await self._auto_find_target(needle)
            if window is None:
                return Err(SdkError(f"找不到匹配 {needle!r} 的窗口"))
        target = {
            "pid": int(window.get("pid") or 0),
            "title": str(window.get("title") or ""),
            "process_name": str(window.get("process_name") or ""),
            "hwnd": int(window.get("hwnd") or 0),
        }
        block = await asyncio.to_thread(
            win32.input_safety_block_reason,
            pid=target["pid"],
            hwnd=target["hwnd"],
            process_name=target["process_name"],
            window_title=target["title"],
            block_anti_cheat=self._block_anti_cheat,
            block_elevated=self._block_elevated,
        )
        if block:
            return Err(SdkError(f"安全策略拒绝设置该目标：{block}"))
        self._target = target
        await self._persist_target(target)
        self._diary_record("target", f"设置目标窗口：{target['title']}（pid {target['pid']}）")
        self.logger.info("target set: pid={} title={}", target["pid"], target["title"])
        return Ok({"target": target, "message": f"目标窗口已设为：{target['title']}（pid {target['pid']}）"})

    @plugin_entry(
        id="get_target",
        name=tr("entries.getTarget.name", default="查询目标窗口"),
        description="返回当前锁定的目标窗口信息（pid、标题、进程名），以及前台窗口是否匹配。",
    )
    async def get_target(self, **_) -> Any:
        if self._target is None:
            return Ok({"target": None, "message": "尚未设置目标窗口"})
        target = dict(self._target)
        focused = False
        if _is_windows():
            pid = int(target.get("pid") or 0)
            if pid == _FULLSCREEN_PID:
                focused = self._foreground_window() is not None
            else:
                window = await asyncio.to_thread(win32.find_window_for_pid, pid)
                if window is not None:
                    focused = await asyncio.to_thread(
                        win32.foreground_matches,
                        int(window.get("hwnd") or 0),
                        pid,
                    )
        return Ok({
            "target": {
                "pid": target.get("pid"),
                "title": target.get("title"),
                "process_name": target.get("process_name"),
            },
            "focused": focused,
            "message": f"当前目标：{target.get('title')}（pid {target.get('pid')}）",
        })

    @ui.action(
        label=tr("actions.clearTarget.label", default="Clear target"),
        icon="C",
        group="target",
        order=30,
        confirm=tr("actions.clearTarget.confirm", default="清除当前目标窗口？"),
        refresh_context=True,
    )
    @plugin_entry(
        id="clear_target",
        name=tr("entries.clearTarget.name", default="清除目标窗口"),
        description="解除当前锁定的目标窗口。",
    )
    async def clear_target(self, **_) -> Any:
        self._target = None
        await self._persist_target(None)
        self._diary_record("target", "清除目标窗口")
        return Ok({"target": None, "message": "目标窗口已清除"})

    @ui.action(
        label=tr("actions.setFullscreen.label", default="Fullscreen control"),
        icon="⛶",
        group="target",
        order=20,
        refresh_context=True,
    )
    @plugin_entry(
        id="set_fullscreen",
        name=tr("entries.setFullscreen.name", default="进入全屏控制"),
        description="切换到全屏键鼠控制模式（不锁定具体窗口，对整块屏幕操作）。",
    )
    async def set_fullscreen(self, **_) -> Any:
        if not _is_windows():
            return Err(SdkError("仅支持 Windows / Linux(X11) 桌面环境"))
        target = {
            "pid": _FULLSCREEN_PID,
            "hwnd": 0,
            "title": _FULLSCREEN_TITLE,
            "process_name": "__screen__",
        }
        self._target = target
        await self._persist_target(target)
        self._diary_record("target", "进入全屏键鼠控制模式")
        self.logger.info("fullscreen control mode enabled")
        return Ok({
            "target": {"pid": target["pid"], "title": target["title"]},
            "message": "已进入全屏键鼠控制：可直接用 mouse_click/press_keys 等对整块屏幕操作（keyboard_clear_target 退出）",
        })

    # ── 键盘注入 ───────────────────────────────────────────────────────

    @ui.action(
        label=tr("actions.pressKeys.label", default="Press keys"),
        icon="K",
        group="input",
        order=10,
        refresh_context=False,
    )
    @plugin_entry(
        id="press_keys",
        name=tr("entries.pressKeys.name", default="按按键/组合键"),
        description=(
            "向目标窗口注入按键或组合键。支持单键与组合键，组合键用 '+' 连接，"
            "例如：'space'、'enter'、'ctrl+c'、'alt+f4'、'win+d'、'shift+F5'。"
            "按键前会自动把目标窗口切到前台；若聚焦失败则不会注入。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "keys": {
                    "type": "string",
                    "description": "按键或组合键，如 'space' / 'ctrl+c' / 'alt+f4'",
                },
                "count": {
                    "type": "integer",
                    "description": "重复次数（默认 1）",
                },
            },
            "required": ["keys"],
        },
        llm_result_fields=["pressed", "keys", "message"],
    )
    async def press_keys(self, keys: str, count: int = 1, **_) -> Any:
        hwnd, window = await asyncio.to_thread(self._require_operable_window)
        try:
            modifiers, main_vk = parse_combo(keys)
        except KeySpecError as exc:
            return Err(SdkError(str(exc)))
        await asyncio.to_thread(self._focus_or_raise, hwnd)
        repeat = min(_MAX_KEY_COUNT, max(1, int(count or 1)))

        def _inject():
            for _ in range(repeat):
                win32.press_combo(modifiers, main_vk, delay=self._input_delay)

        await asyncio.to_thread(_inject)
        self._diary_record(
            "input",
            f"按键 {keys} ×{repeat} → {window.get('title')}",
        )
        return Ok({
            "pressed": True,
            "keys": str(keys),
            "count": repeat,
            "target": str(window.get("title") or ""),
            "message": f"已向 {window.get('title')} 注入 {keys}",
        })

    @plugin_entry(
        id="hold_key",
        name=tr("entries.holdKey.name", default="长按按键"),
        description="按下并按住按键/组合键持续指定秒数后松开。",
        input_schema={
            "type": "object",
            "properties": {
                "keys": {"type": "string", "description": "按键或组合键"},
                "seconds": {"type": "number", "default": 1.0, "description": "按住时长（秒）"},
            },
            "required": ["keys"],
        },
        llm_result_fields=["held", "keys", "seconds", "message"],
    )
    async def hold_key(self, keys: str, seconds: float = 1.0, **_) -> Any:
        hwnd, window = await asyncio.to_thread(self._require_operable_window)
        try:
            parse_combo(keys)
        except KeySpecError as exc:
            return Err(SdkError(str(exc)))
        await asyncio.to_thread(self._focus_or_raise, hwnd)
        await asyncio.to_thread(win32.hold_key, str(keys), seconds=min(_MAX_HOLD_SECONDS, max(0.05, float(seconds or 1.0))))
        self._diary_record(
            "input",
            f"长按 {keys} {max(0.05, float(seconds or 1.0))} 秒 → {window.get('title')}",
        )
        return Ok({
            "held": True,
            "keys": str(keys),
            "seconds": round(max(0.05, float(seconds or 1.0)), 2),
            "target": str(window.get("title") or ""),
            "message": f"已向 {window.get('title')} 长按 {keys} {seconds} 秒",
        })

    @plugin_entry(
        id="hold_mouse",
        name=tr("entries.holdMouse.name", default="长按鼠标"),
        description="在指定位置按住鼠标键一段时间后松开。",
        input_schema={
            "type": "object",
            "properties": {
                "x": {"type": "integer", "description": "屏幕 x 坐标；留空=当前位置"},
                "y": {"type": "integer", "description": "屏幕 y 坐标；留空=当前位置"},
                "button": {"type": "string", "enum": ["left", "right", "middle"], "default": "left"},
                "seconds": {"type": "number", "default": 1.0},
            },
        },
        llm_result_fields=["held", "button", "seconds", "message"],
    )
    async def hold_mouse(self, button: str = "left", seconds: float = 1.0, x: int | None = None, y: int | None = None, **_) -> Any:
        hwnd, window = await asyncio.to_thread(self._require_operable_window)
        await asyncio.to_thread(self._focus_or_raise, hwnd)
        if x is not None and y is not None:
            await asyncio.to_thread(win32.mouse_move, int(x), int(y))
        held = min(_MAX_HOLD_SECONDS, max(0.05, float(seconds or 0.05)))
        await asyncio.to_thread(win32.hold_mouse_button, str(button or "left"), seconds=held)
        pos = f"({int(x)}, {int(y)})" if x is not None and y is not None else "当前位置"
        self._diary_record("input", f"长按{str(button or 'left')}键 {held:.2f} 秒 @{pos}")
        return Ok({
            "held": True,
            "button": str(button or "left"),
            "seconds": round(held, 2),
            "target": str(window.get("title") or ""),
            "message": f"已在 {pos} 长按{str(button or 'left')}键 {round(held, 2)} 秒",
        })

    @ui.action(
        label=tr("actions.typeText.label", default="Type text"),
        icon="W",
        group="input",
        order=20,
        refresh_context=False,
    )
    @plugin_entry(
        id="type_text",
        name=tr("entries.typeText.name", default="输入文本"),
        description="向目标窗口输入一段文本（Unicode，支持中文；长文本自动用剪贴板粘贴）。",
        input_schema={
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "要输入的文本"},
            },
            "required": ["text"],
        },
        llm_result_fields=["typed", "length", "used_clipboard", "message"],
    )
    async def type_text(self, text: str, **_) -> Any:
        hwnd, window = await asyncio.to_thread(self._require_operable_window)
        payload = str(text or "")
        if not payload:
            return Err(SdkError("文本为空"))
        await asyncio.to_thread(self._focus_or_raise, hwnd)
        used_clipboard = len(payload) >= self._clipboard_paste_min_chars
        await asyncio.to_thread(
            win32.type_text,
            payload,
            char_delay=self._type_delay,
            use_clipboard=True,
        )
        self._diary_record(
            "input",
            f"输入文本 {len(payload)} 字符{'（剪贴板粘贴）' if used_clipboard else ''} → {window.get('title')}",
        )
        return Ok({
            "typed": True,
            "length": len(payload),
            "used_clipboard": used_clipboard,
            "target": str(window.get("title") or ""),
            "message": f"已向 {window.get('title')} 输入 {len(payload)} 个字符"
                       + ("（长文本，经剪贴板粘贴）" if used_clipboard else ""),
        })

    @plugin_entry(
        id="press_sequence",
        name=tr("entries.pressSequence.name", default="按键序列"),
        description=(
            "依次执行一串操作步骤。每步可为 'keys' 组合键（+可选 count/delay/hold 长按秒数）、'text' 输入文本，"
            "或鼠标动作 {\"action\": \"click\"|\"move\"|\"drag\"|\"wheel\", \"x\"..\"y\"..}，"
            "或等待 {\"action\": \"wait\", \"seconds\": 1.5}（等动画/加载）。步骤之间默认按 delay 间隔执行。"
            "示例：[{\"keys\":\"ctrl+c\"}, {\"keys\":\"space\",\"hold\":1.5}, {\"text\":\"hi\"}, {\"action\":\"click\",\"x\":100,\"y\":200}]"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "sequence": {
                    "type": "array",
                    "description": "操作步骤列表，如 [{\"keys\": \"ctrl+c\"}, {\"keys\": \"enter\"}] 或鼠标动作",
                    "items": {
                        "anyOf": [
                            {"type": "string"},
                            {
                                "type": "object",
                                "properties": {
                                    "keys": {"type": "string"},
                                    "count": {"type": "integer"},
                                    "delay": {"type": "number"},
                                    "hold": {"type": "number", "description": "按住秒数（长按键步骤）"},
                                    "text": {"type": "string"},
                                    "action": {"type": "string", "enum": ["click", "move", "drag", "wheel", "wait"]},
                                    "seconds": {"type": "number", "description": "action=wait 时的等待秒数"},
                                    "x": {"type": "integer"},
                                    "y": {"type": "integer"},
                                    "x2": {"type": "integer"},
                                    "y2": {"type": "integer"},
                                    "button": {"type": "string"},
                                    "delta": {"type": "integer"},
                                    "steps": {"type": "integer"},
                                },
                            },
                        ]
                    },
                },
            },
            "required": ["sequence"],
        },
        llm_result_fields=["executed", "steps", "message"],
    )
    async def press_sequence(self, sequence: Any, **_) -> Any:
        hwnd, window = await asyncio.to_thread(self._require_operable_window)
        try:
            steps = self._normalize_sequence(sequence)
        except SdkError as exc:
            return Err(exc)
        await asyncio.to_thread(self._focus_or_raise, hwnd)

        parsed: list[dict[str, Any]] = []
        for step in steps:
            keys = step.get("keys")
            item: dict[str, Any] = {
                "count": min(_MAX_KEY_COUNT, max(1, int(step.get("count") or 1))),
                "delay": float(step.get("delay") or self._input_delay),
            }
            if step.get("text") is not None:
                item["text"] = str(step["text"])
            elif step.get("action") is not None:
                item["action"] = str(step["action"])
                for field in ("x", "y", "x2", "y2", "delta", "steps"):
                    if step.get(field) is not None:
                        item[field] = int(step[field])
                if step.get("seconds") is not None:
                    item["seconds"] = min(_MAX_WAIT_SECONDS, max(0.05, float(step["seconds"])))
                item["button"] = str(step.get("button") or "left")
            elif keys is not None:
                try:
                    modifiers, main_vk = parse_combo(str(keys))
                except KeySpecError as exc:
                    return Err(SdkError(f"序列第 {len(parsed) + 1} 步无效：{exc}"))
                item["modifiers"] = modifiers
                item["main_vk"] = main_vk
                if step.get("hold") is not None:
                    item["hold"] = max(0.05, float(step["hold"]))
            else:
                return Err(SdkError(f"序列第 {len(parsed) + 1} 步缺少 keys/text/action"))
            parsed.append(item)

        def _run():
            last = len(parsed) - 1
            for i, item in enumerate(parsed):
                if "text" in item:
                    win32.type_text(
                        item["text"],
                        char_delay=self._type_delay,
                        clipboard_threshold=self._clipboard_paste_min_chars,
                    )
                elif "action" in item:
                    action = item["action"]
                    if action == "wait":
                        time.sleep(max(0.05, float(item.get("seconds") or 0.05)))
                        continue
                    if action == "click":
                        win32.mouse_click(
                            int(item.get("x") or 0), int(item.get("y") or 0),
                            button=item["button"], clicks=item["count"],
                        )
                    elif action == "move":
                        win32.mouse_move(int(item.get("x") or 0), int(item.get("y") or 0))
                    elif action == "drag":
                        win32.mouse_drag(
                            int(item.get("x") or 0), int(item.get("y") or 0),
                            int(item.get("x2") or 0), int(item.get("y2") or 0),
                            button=item["button"], steps=int(item.get("steps") or 20),
                        )
                    elif action == "wheel":
                        win32.mouse_wheel(
                            int(item.get("x") or 0), int(item.get("y") or 0),
                            delta=int(item.get("delta") or 120),
                        )
                else:
                    hold_seconds = float(item.get("hold") or 0)
                    if hold_seconds > 0:
                        for m in item["modifiers"]:
                            win32.key_down(m)
                        win32.key_down(item["main_vk"])
                        win32._wait_seconds(hold_seconds)
                        win32.key_up(item["main_vk"])
                        for m in reversed(item["modifiers"]):
                            win32.key_up(m)
                    else:
                        for _ in range(item["count"]):
                            win32.press_combo(item["modifiers"], item["main_vk"], delay=item["delay"])
                if i < last and item["delay"] > 0:
                    time.sleep(item["delay"])

        await asyncio.to_thread(_run)
        self._diary_record("input", f"按键序列 {len(parsed)} 步 → {window.get('title')}")
        return Ok({
            "executed": True,
            "steps": len(parsed),
            "target": str(window.get("title") or ""),
            "message": f"已向 {window.get('title')} 执行 {len(parsed)} 步按键序列",
        })

    @plugin_entry(
        id="list_supported_keys",
        name=tr("entries.listKeys.name", default="支持的键名"),
        description="列出 press_keys / press_sequence 支持的所有键名。",
    )
    async def list_supported_keys(self, **_) -> Any:
        names = supported_key_names()
        return Ok({"count": len(names), "keys": names})

    # ── 鼠标注入 ───────────────────────────────────────────────────────

    @plugin_entry(
        id="mouse_move",
        name=tr("entries.mouseMove.name", default="移动鼠标"),
        description="把鼠标移动到屏幕绝对坐标 (x, y)。",
        input_schema={
            "type": "object",
            "properties": {
                "x": {"type": "integer", "description": "屏幕 x 坐标"},
                "y": {"type": "integer", "description": "屏幕 y 坐标"},
            },
            "required": ["x", "y"],
        },
        llm_result_fields=["moved", "x", "y"],
    )
    async def mouse_move(self, x: int, y: int, **_) -> Any:
        if not _is_windows():
            return Err(SdkError("仅支持 Windows / Linux(X11) 桌面环境"))
        hwnd, _window = await asyncio.to_thread(self._require_operable_window)
        await asyncio.to_thread(self._focus_or_raise, hwnd)
        await asyncio.to_thread(win32.mouse_move, int(x), int(y))
        self._diary_record("input", f"鼠标移动到 ({x}, {y})")
        return Ok({"moved": True, "x": int(x), "y": int(y), "message": f"鼠标已移动到 ({x}, {y})"})

    @ui.action(
        label=tr("actions.mouseClick.label", default="Click"),
        icon="M",
        group="input",
        order=30,
        refresh_context=False,
    )
    @plugin_entry(
        id="mouse_click",
        name=tr("entries.mouseClick.name", default="鼠标点击"),
        description="在屏幕绝对坐标 (x, y) 处点击（默认单击；clicks=2 双击）。",
        input_schema={
            "type": "object",
            "properties": {
                "x": {"type": "integer", "description": "屏幕 x 坐标"},
                "y": {"type": "integer", "description": "屏幕 y 坐标"},
                "button": {
                    "type": "string",
                    "enum": ["left", "right", "middle"],
                    "default": "left",
                    "description": "鼠标键",
                },
                "clicks": {
                    "type": "integer",
                    "default": 1,
                    "description": "点击次数（1=单击，2=双击）",
                },
            },
            "required": ["x", "y"],
        },
        llm_result_fields=["clicked", "x", "y", "clicks"],
    )
    async def mouse_click(self, x: int, y: int, button: str = "left", clicks: int = 1, **_) -> Any:
        hwnd, window = await asyncio.to_thread(self._require_operable_window)
        await asyncio.to_thread(self._focus_or_raise, hwnd)
        clicks = min(_MAX_CLICKS, max(1, int(clicks or 1)))
        await asyncio.to_thread(
            win32.mouse_click,
            int(x), int(y),
            button=str(button or "left"),
            clicks=clicks,
        )
        self._diary_record(
            "input",
            f"鼠标{str(button or 'left')}键点击 ({x}, {y}) ×{clicks}",
        )
        return Ok({
            "clicked": True,
            "x": int(x),
            "y": int(y),
            "button": str(button or "left"),
            "clicks": clicks,
            "message": f"已在 ({x}, {y}) {'双击' if clicks >= 2 else '点击'}",
        })

    @plugin_entry(
        id="mouse_drag",
        name=tr("entries.mouseDrag.name", default="鼠标拖拽"),
        description="从 (x1,y1) 按住并拖到 (x2,y2) 再松开。",
        input_schema={
            "type": "object",
            "properties": {
                "x1": {"type": "integer", "description": "起点 x"},
                "y1": {"type": "integer", "description": "起点 y"},
                "x2": {"type": "integer", "description": "终点 x"},
                "y2": {"type": "integer", "description": "终点 y"},
                "button": {
                    "type": "string",
                    "enum": ["left", "right", "middle"],
                    "default": "left",
                    "description": "鼠标键",
                },
                "steps": {"type": "integer", "default": 20, "description": "插值步数"},
            },
            "required": ["x1", "y1", "x2", "y2"],
        },
        llm_result_fields=["dragged", "from", "to"],
    )
    async def mouse_drag(self, x1: int, y1: int, x2: int, y2: int, button: str = "left", steps: int = 20, **_) -> Any:
        hwnd, window = await asyncio.to_thread(self._require_operable_window)
        await asyncio.to_thread(self._focus_or_raise, hwnd)
        await asyncio.to_thread(
            win32.mouse_drag,
            int(x1), int(y1), int(x2), int(y2),
            button=str(button or "left"),
            steps=min(_MAX_DRAG_STEPS, max(1, int(steps or 20))),
        )
        self._diary_record(
            "input",
            f"鼠标拖拽 ({x1}, {y1}) → ({x2}, {y2})",
        )
        return Ok({
            "dragged": True,
            "from": [int(x1), int(y1)],
            "to": [int(x2), int(y2)],
            "message": f"已从 ({x1}, {y1}) 拖到 ({x2}, {y2})",
        })

    @plugin_entry(
        id="mouse_wheel",
        name=tr("entries.mouseWheel.name", default="鼠标滚轮"),
        description="在 (x, y) 处滚动鼠标滚轮（delta 正上负下）。",
        input_schema={
            "type": "object",
            "properties": {
                "x": {"type": "integer", "description": "屏幕 x 坐标"},
                "y": {"type": "integer", "description": "屏幕 y 坐标"},
                "delta": {"type": "integer", "default": 120, "description": "滚动量，正=上，负=下"},
            },
            "required": ["x", "y"],
        },
        llm_result_fields=["scrolled", "x", "y", "delta"],
    )
    async def mouse_wheel(self, x: int, y: int, delta: int = 120, **_) -> Any:
        if not _is_windows():
            return Err(SdkError("仅支持 Windows / Linux(X11) 桌面环境"))
        hwnd, _window = await asyncio.to_thread(self._require_operable_window)
        await asyncio.to_thread(self._focus_or_raise, hwnd)
        await asyncio.to_thread(win32.mouse_wheel, int(x), int(y), delta=int(delta or 120))
        self._diary_record(
            "input",
            f"滚轮滚动 ({x}, {y}) delta={int(delta or 120)}",
        )
        return Ok({
            "scrolled": True,
            "x": int(x),
            "y": int(y),
            "delta": int(delta or 120),
            "message": f"已在 ({x}, {y}) 滚动滚轮",
        })

    @ui.action(
        label=tr("actions.getWindowRect.label", default="Get window rect"),
        icon="W",
        group="target",
        order=40,
        refresh_context=False,
    )
    @plugin_entry(
        id="get_window_rect",
        name=tr("entries.getWindowRect.name", default="获取窗口坐标"),
        description="获取目标窗口的屏幕坐标与客户区信息。",
        input_schema={"type": "object", "properties": {}},
        llm_result_fields=["ok", "window_rect", "client_rect", "message"],
    )
    async def get_window_rect(self, **_) -> Any:
        hwnd, window = await asyncio.to_thread(self._require_operable_window)
        wrect = await asyncio.to_thread(win32.window_rect, hwnd)
        crect = await asyncio.to_thread(win32.window_client_rect, hwnd)
        if wrect is None:
            return Err(SdkError("无法获取窗口坐标，目标可能已关闭"))
        self._diary_record(
            "window",
            f"获取窗口坐标 {wrect.get('width', wrect['right']-wrect['left'])}x{wrect.get('height', wrect['bottom']-wrect['top'])} @({wrect['left']}, {wrect['top']})",
        )
        return Ok({
            "ok": True,
            "window_rect": wrect,
            "client_rect": crect,
            "title": str(window.get("title") or ""),
            "message": f"窗口位于 ({wrect['left']}, {wrect['top']})，{wrect.get('width', wrect['right']-wrect['left'])}x{wrect.get('height', wrect['bottom']-wrect['top'])}",
        })

    @plugin_entry(
        id="move_window",
        name=tr("entries.moveWindow.name", default="移动/缩放窗口"),
        description="把目标窗口移动到指定位置（可同时改大小）。",
        input_schema={
            "type": "object",
            "properties": {
                "x": {"type": "integer", "description": "新位置 x"},
                "y": {"type": "integer", "description": "新位置 y"},
                "width": {"type": "integer", "description": "新宽度（可选）"},
                "height": {"type": "integer", "description": "新高度（可选）"},
            },
            "required": ["x", "y"],
        },
        llm_result_fields=["moved", "window_rect", "message"],
    )
    async def move_window(self, x: int, y: int, width: int | None = None, height: int | None = None, **_) -> Any:
        hwnd, window = await asyncio.to_thread(self._require_operable_window)
        if int((self._target or {}).get("pid") or 0) == _FULLSCREEN_PID:
            return Err(SdkError("全屏模式没有可移动的窗口，请先 set_target 锁定具体窗口"))
        wrect = await asyncio.to_thread(win32.window_rect, hwnd)
        if wrect is None:
            return Err(SdkError("无法获取当前窗口坐标，目标可能已关闭"))
        new_w = max(120, int(width)) if width else int(wrect.get("width") or (wrect["right"] - wrect["left"]))
        new_h = max(80, int(height)) if height else int(wrect.get("height") or (wrect["bottom"] - wrect["top"]))
        ok = await asyncio.to_thread(win32.move_window, hwnd, int(x), int(y), new_w, new_h)
        if not ok:
            return Err(SdkError("移动窗口失败（SetWindowPos 被拒绝，窗口可能已关闭或被系统限制）"))
        self._diary_record(
            "window",
            f"移动窗口 → ({int(x)}, {int(y)}) {new_w}x{new_h}：{window.get('title')}",
        )
        return Ok({
            "moved": True,
            "window_rect": {"left": int(x), "top": int(y), "width": new_w, "height": new_h},
            "message": f"已把「{window.get('title')}」移到 ({int(x)}, {int(y)}) 并设为 {new_w}x{new_h}",
        })

    # ── 剪贴板 / 窗口状态 ─────────────────────────────────────────────

    @plugin_entry(
        id="get_clipboard",
        name=tr("entries.getClipboard.name", default="读取剪贴板"),
        description="读取当前剪贴板的文本内容。",
    )
    async def get_clipboard(self, **_) -> Any:
        if not _is_windows():
            return Err(SdkError("仅支持 Windows / Linux(X11) 桌面环境"))
        text = await asyncio.to_thread(win32.get_clipboard_text)
        tries = 0
        while (text is None or not str(text).strip()) and tries < 3:
            # 剪贴板管理器/云同步可能瞬时清空，稍候重读
            await asyncio.sleep(0.06)
            text = await asyncio.to_thread(win32.get_clipboard_text)
            tries += 1
        if text is None or not str(text).strip():
            return Ok({"status": "no_text", "text": "", "message": "剪贴板为空或不含文本"})
        cap = max(500, self._command_max_output)
        shown = str(text)[:cap]
        self._diary_record("clipboard", f"读取剪贴板 {len(text)} 字符")
        return Ok({
            "status": "ok",
            "text": shown,
            "length": len(text),
            "truncated": len(text) > len(shown),
            "message": f"已读取剪贴板 {len(text)} 字符" + ("（已截断）" if len(text) > len(shown) else ""),
        })

    @plugin_entry(
        id="set_clipboard",
        name=tr("entries.setClipboard.name", default="写剪贴板"),
        description="把文本写入剪贴板（配合 ctrl+v 粘贴）。",
        input_schema={
            "type": "object",
            "properties": {"text": {"type": "string", "description": "要写入的文本"}},
            "required": ["text"],
        },
    )
    async def set_clipboard(self, text: str = "", **_) -> Any:
        if not _is_windows():
            return Err(SdkError("仅支持 Windows / Linux(X11) 桌面环境"))
        payload = str(text or "")
        if not payload:
            return Err(SdkError("text 不能为空"))
        if len(payload) > _MAX_SET_CLIPBOARD_CHARS:
            return Err(SdkError(f"文本过长（上限 {_MAX_SET_CLIPBOARD_CHARS} 字符）"))
        ok = await asyncio.to_thread(win32.set_clipboard_text, payload)
        if not ok:
            return Err(SdkError("写入剪贴板失败（可能被其他程序占用），请稍后重试"))
        self._diary_record("clipboard", f"写入剪贴板 {len(payload)} 字符")
        return Ok({
            "written": True,
            "length": len(payload),
            "message": f"已写入剪贴板 {len(payload)} 字符，可用 press_keys('ctrl+v') 粘贴",
        })

    @plugin_entry(
        id="control_window",
        name=tr("entries.controlWindow.name", default="窗口控制"),
        description="最小化/最大化/还原/关闭/置顶目标窗口。",
        input_schema={
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["minimize", "maximize", "restore", "close", "pin", "unpin"]},
            },
            "required": ["action"],
        },
        llm_result_fields=["done", "action", "title", "message"],
    )
    async def control_window(self, action: str, **_) -> Any:
        hwnd, window = self._require_operable_window()
        if int((self._target or {}).get("pid") or 0) == _FULLSCREEN_PID:
            return Err(SdkError("全屏模式没有可控制的窗口，请先 set_target 锁定具体窗口"))
        act = str(action or "").strip().lower()
        title = str(window.get("title") or "")
        if act == "close":
            ok = await asyncio.to_thread(win32.close_window, hwnd)
            detail = f"已向「{title}」发送关闭请求（应用可能弹出保存确认）"
        elif act in ("minimize", "maximize", "restore"):
            ok = await asyncio.to_thread(win32.show_window, hwnd, act)
            detail = f"已把「{title}」{ {'minimize': '最小化', 'maximize': '最大化', 'restore': '还原'}[act] }"
        elif act in ("pin", "unpin"):
            topmost = act == "pin"
            ok = False
            # 打包应用启动后可能换 pid/句柄（引导进程退出→商店版接管）。
            # 失败时：清缓存重解析；再失败则按标题重新定位并接管新目标；最多 8 轮。
            for attempt in range(8):
                ok = await asyncio.to_thread(win32.set_topmost, hwnd, topmost)
                if ok:
                    break
                self._target_hwnd_cache = None
                await asyncio.sleep(1.0)
                try:
                    hwnd, window = await asyncio.to_thread(self._require_operable_window)
                    title = str(window.get("title") or title)
                except SdkError:
                    pass
            if not ok:
                # 按 pid 找不到 → 尝试按标题重新定位（pid 可能已变）
                title_l = str(window.get("title") or "").strip().lower()
                proc_l = str(window.get("process_name") or "").strip().lower()
                if title_l or proc_l:
                    windows = await asyncio.to_thread(win32.enumerate_windows)
                    for win in windows:
                        t2 = str(win.get("title") or "").lower()
                        p2 = str(win.get("process_name") or "").lower()
                        hit = (title_l and title_l in t2) or (proc_l and proc_l in p2)
                        if not hit:
                            continue
                        new_target = {
                            "pid": int(win.get("pid") or 0),
                            "title": str(win.get("title") or ""),
                            "process_name": str(win.get("process_name") or ""),
                            "hwnd": int(win.get("hwnd") or 0),
                        }
                        block = win32.input_safety_block_reason(
                            pid=new_target["pid"], hwnd=new_target["hwnd"],
                            process_name=new_target["process_name"],
                            window_title=new_target["title"],
                            block_anti_cheat=self._block_anti_cheat,
                            block_elevated=self._block_elevated,
                        )
                        if block:
                            break
                        self._target = new_target
                        self._target_hwnd_cache = None
                        await self._persist_target(new_target)
                        hwnd = new_target["hwnd"]
                        window = new_target
                        ok = await asyncio.to_thread(win32.set_topmost, hwnd, topmost)
                        break
            if ok:
                detail = f"已把「{str(window.get('title') or title)}」{'置顶' if topmost else '取消置顶'}"
                self._diary_record("window", detail)
                return Ok({"done": True, "action": act, "title": title, "message": detail})
            # 软降级：个别宿主框架窗口拒绝跨进程 TOPMOST，不作为错误
            warn = (f"窗口「{title}」暂不支持置顶操作（系统拒绝），其余功能不受影响")
            self.logger.warning("set_topmost failed for {} ({})", title, act)
            return Ok({"done": False, "action": act, "title": title, "message": warn})
        else:
            return Err(SdkError("action 必须是 minimize/maximize/restore/close/pin/unpin"))
        if not ok:
            return Err(SdkError(f"窗口操作失败（{act}），目标可能已关闭"))
        self._diary_record("window", f"窗口{detail}")
        return Ok({"done": True, "action": act, "title": title, "message": detail})

    # ── UIA 控件感知（.NET UIAutomation via PowerShell）───────────────

    _UIA_INSPECT_TEMPLATE = r"""
$ProgressPreference = 'SilentlyContinue'
$ErrorActionPreference = 'Stop'
try { Add-Type -AssemblyName UIAutomationClient } catch {}
try { Add-Type -AssemblyName UIAutomationTypes } catch {}
Add-Type -Namespace KC -Name Native -MemberDefinition '[DllImport("user32.dll")] public static extern bool ClientToScreen(IntPtr hWnd, ref POINT lpPoint); [StructLayout(LayoutKind.Sequential)] public struct POINT { public int X; public int Y; }'
$hwnd = [IntPtr]::new(%HWND%)
$el = [System.Windows.Automation.AutomationElement]::FromHandle($hwnd)
if (-not $el) { [Console]::Out.Write('[]'); exit 0 }
# 坐标基准统一为【客户区左上角】（与 keyboard_click_in_window 的 x/y 语义一致）。
# 此前用 BoundingRectangle（窗口矩形，含标题栏）作原点，导致检查结果的
# center_x/center_y 直接喂给点击工具时会整体偏移一个非客户区尺寸。
$kcOrigin = New-Object 'KC.Native+POINT'
$kcOrigin.X = 0; $kcOrigin.Y = 0
[KC.Native]::ClientToScreen($hwnd, [ref]$kcOrigin) | Out-Null
$origin = @{ X = [double]$kcOrigin.X; Y = [double]$kcOrigin.Y }
$out = New-Object System.Collections.Generic.List[object]
function Add-One($c) {
    if ($null -eq $c) { return }
    try {
        $r = $c.Current.BoundingRectangle
        if ($r.Width -le 1 -or $r.Height -le 1) { return }
        $name = $c.Current.Name
        if ([string]::IsNullOrWhiteSpace($name)) { return }
        $relX = [int]([Math]::Round($r.X - $origin.X))
        $relY = [int]([Math]::Round($r.Y - $origin.Y))
        if ([Math]::Abs($relX) -gt 100000 -or [Math]::Abs($relY) -gt 100000) { return }
        $script:out.Add([ordered]@{
            class_name   = [string]$c.Current.ClassName
            text         = [string]$name
            control_type = [string]$c.Current.LocalizedControlType
            left         = $relX
            top          = $relY
            width        = [int][Math]::Round($r.Width)
            height       = [int][Math]::Round($r.Height)
            center_x     = [int]($relX + [Math]::Round($r.Width / 2))
            center_y     = [int]($relY + [Math]::Round($r.Height / 2))
        })
    } catch { return }
}
# 两级广度兜底：深层元素（内容岛内部）可能返回异空间坐标，主路失败才走这里。
# 主路：Descendants 全量 + 坐标异常过滤；超时/失败重试一次，再退两级遍历。
try {
    $found = $el.FindAll([System.Windows.Automation.TreeScope]::Descendants, [System.Windows.Automation.Condition]::TrueCondition)
    foreach ($c in $found) { Add-One $c }
} catch {
    Start-Sleep -Milliseconds 800
    try {
        $found = $el.FindAll([System.Windows.Automation.TreeScope]::Descendants, [System.Windows.Automation.Condition]::TrueCondition)
        foreach ($c in $found) { Add-One $c }
    } catch {}
}
# 兜底：主路空手而归时，退回两级广度遍历（坐标实测正确）。
if ($out.Count -eq 0) {
    $kids = @()
    try { $kids = @($el.FindAll([System.Windows.Automation.TreeScope]::Children, [System.Windows.Automation.Condition]::TrueCondition)) } catch {}
    foreach ($k in $kids) {
        Add-One $k
        if ($out.Count -ge %MAXNODES%) { break }
    }
    if ($out.Count -lt %MAXNODES%) {
        foreach ($k in $kids) {
            try {
                $grandkids = $k.FindAll([System.Windows.Automation.TreeScope]::Children, [System.Windows.Automation.Condition]::TrueCondition)
                foreach ($g in $grandkids) {
                    Add-One $g
                    if ($out.Count -ge %MAXNODES%) { break }
                }
            } catch {}
            if ($out.Count -ge %MAXNODES%) { break }
        }
    }
}
[Console]::Out.Write((ConvertTo-Json -InputObject @($out.ToArray()) -Depth 3 -Compress))
"""

    async def _uia_inspect(self, hwnd: int, max_nodes: int = 400) -> tuple[Optional[list[dict[str, Any]]], str]:
        """运行 .NET UIAutomation 枚举（PowerShell），返回 (controls|None, error)。"""
        script = self._UIA_INSPECT_TEMPLATE.replace("%HWND%", str(int(hwnd))).replace("%MAXNODES%", str(max_nodes))
        result = await asyncio.to_thread(
            command_exec.run_command,
            script,
            shell="powershell",
            timeout=max(30.0, float(getattr(self, "_command_timeout", 30)) * 2),
            max_output_chars=200_000,
        )
        output = str(result.get("output") or "").strip()
        if not result.get("success"):
            return None, (output[:300] or f"returncode={result.get('returncode')}")
        try:
            parsed = json.loads(output)
        except Exception as exc:
            return None, f"UIA 输出解析失败：{exc}"
        if isinstance(parsed, dict):
            parsed = [parsed]
        controls: list[dict[str, Any]] = []
        for item in parsed or []:
            if not isinstance(item, dict):
                continue
            controls.append({
                "class_name": item.get("class_name"),
                "text": item.get("text"),
                "control_type": item.get("control_type"),
                "left": item.get("left"),
                "top": item.get("top"),
                "width": item.get("width"),
                "height": item.get("height"),
                "center_x": item.get("center_x"),
                "center_y": item.get("center_y"),
            })
        return controls, ""

    @plugin_entry(
        id="inspect_controls",
        name=tr("entries.inspectControls.name", default="检查窗口控件"),
        description="枚举目标窗口控件（UIA 全框架 / 经典子窗口），供精准点击。",
        input_schema={
            "type": "object",
            "properties": {
                "include_empty": {"type": "boolean", "default": False},
                "max_results": {"type": "integer", "default": 30},
                "backend": {"type": "string", "enum": ["auto", "uia", "win32"], "default": "auto"},
            },
        },
        llm_result_fields=["backend_used", "controls", "count", "message"],
    )
    async def inspect_controls(self, include_empty: bool = False, max_results: int = 30, backend: str = "auto", **_) -> Any:
        if not _is_windows():
            return Err(SdkError("仅支持 Windows / Linux(X11) 桌面环境"))
        hwnd, window = await asyncio.to_thread(self._require_operable_window)
        limit = max(1, min(100, int(max_results or 30)))
        backend_norm = str(backend or "auto").strip().lower()
        title = str(window.get("title") or "")

        controls: Optional[list[dict[str, Any]]] = None
        used_backend = ""
        error_note = ""

        if sys.platform == "win32" and backend_norm in ("auto", "uia"):
            uia_controls, err = await self._uia_inspect(hwnd, max_nodes=max(limit * 4, 400))
            if uia_controls is not None:
                controls = uia_controls
                used_backend = "uia"
            else:
                error_note = err[:200]
                if backend_norm == "uia":
                    return Err(SdkError(f"UIA 枚举失败：{error_note}"))
                used_backend = "win32"

        if controls is None and sys.platform == "win32":
            raw = await asyncio.to_thread(win32.enumerate_child_controls, hwnd, 200)
            controls = [
                c for c in raw
                if include_empty or str(c.get("text") or "").strip()
            ]
            used_backend = used_backend or "win32"
        elif controls is None:
            # 非 Windows 平台：linux 子窗口枚举
            raw = await asyncio.to_thread(win32.enumerate_child_controls, hwnd, 200)
            controls = [
                c for c in raw
                if include_empty or str(c.get("text") or "").strip()
            ]
            used_backend = "x11"

        shown = controls[:limit]
        for c in shown:
            c.pop("hwnd", None)
        self._diary_record("inspect", f"检查控件[{used_backend}] {len(shown)}/{len(controls)} 个：{title}")
        message = (
            f"[{used_backend}] 共 {len(controls)} 个有名称控件（显示前 {len(shown)} 个）。"
            "用 center_x/center_y 配合 keyboard_click_in_window 精准点击。"
        )
        if error_note and used_backend == "win32":
            message += f"（UIA 回退原因：{error_note}）"
        return Ok({
            "backend_used": used_backend,
            "controls": shown,
            "count": len(shown),
            "total_with_name": len(controls),
            "message": message,
        })

    _UIA_CONTROL_TEMPLATE = r"""
$ProgressPreference = 'SilentlyContinue'
$ErrorActionPreference = 'Stop'
try { Add-Type -AssemblyName UIAutomationClient } catch {}
try { Add-Type -AssemblyName UIAutomationTypes } catch {}
$hwnd = [IntPtr]::new(%HWND%)
$el = [System.Windows.Automation.AutomationElement]::FromHandle($hwnd)
if (-not $el) { [Console]::Out.Write('{"status":"error","error":"element not found"}'); exit 0 }
$needle = '%NEEDLE%'.ToLower()
$action = '%ACTION%'
$wantIndex = %INDEX%
$value = '%VALUE%'
$matchesList = New-Object System.Collections.Generic.List[object]
try {
    $found = $el.FindAll([System.Windows.Automation.TreeScope]::Descendants, [System.Windows.Automation.Condition]::TrueCondition)
    foreach ($c in $found) {
        try {
            $n = $c.Current.Name
            if ([string]::IsNullOrWhiteSpace($n)) { continue }
            if (-not $n.ToLower().Contains($needle)) { continue }
            $matchesList.Add($c)
            if ($matchesList.Count -gt 200) { break }
        } catch { continue }
    }
} catch {
    [Console]::Out.Write(('{"status":"error","error":"' + $_.Exception.Message.Replace('"','''') + '"}')); exit 0
}
if ($matchesList.Count -eq 0 -or $wantIndex -ge $matchesList.Count) {
    [Console]::Out.Write(('{"status":"not_found","total_matches":' + $matchesList.Count + '}')); exit 0
}
$c = $matchesList[$wantIndex]
$n = [string]$c.Current.Name
$ct = [string]$c.Current.LocalizedControlType
$r = $c.Current.BoundingRectangle
$originEl = $el.Current.BoundingRectangle
function Out-Json($status, $extra) {
    $base = [ordered]@{
        status = $status
        name = $n
        control_type = $ct
        total_matches = $matchesList.Count
        center_x = [int]([Math]::Round($r.X + $r.Width / 2 - $originEl.X))
        center_y = [int]([Math]::Round($r.Y + $r.Height / 2 - $originEl.Y))
    }
    foreach ($k in $extra.Keys) { $base[$k] = $extra[$k] }
    $obj = [pscustomobject]$base
    [Console]::Out.Write(($obj | ConvertTo-Json -Depth 3 -Compress))
}
if ($action -eq 'invoke') {
    $pat = $null
    if ($c.TryGetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern, [ref]$pat)) {
        $pat.Invoke()
        Out-Json 'invoked' @{}
        exit 0
    }
    $tg = $null
    if ($c.TryGetCurrentPattern([System.Windows.Automation.TogglePattern]::Pattern, [ref]$tg)) {
        $tg.Toggle()
        Out-Json 'toggled' @{}
        exit 0
    }
    Out-Json 'no_invoke_pattern' @{'hint'='可用返回的 center 坐标走 keyboard_click_in_window'}
    exit 0
}
if ($action -eq 'expand') {
    $ec = $null
    if ($c.TryGetCurrentPattern([System.Windows.Automation.ExpandCollapsePattern]::Pattern, [ref]$ec)) {
        $ec.Expand()
        Out-Json 'expanded' @{}
        exit 0
    }
    Out-Json 'no_expand_pattern' @{}
    exit 0
}
if ($action -eq 'collapse') {
    $ec = $null
    if ($c.TryGetCurrentPattern([System.Windows.Automation.ExpandCollapsePattern]::Pattern, [ref]$ec)) {
        $ec.Collapse()
        Out-Json 'collapsed' @{}
        exit 0
    }
    Out-Json 'no_collapse_pattern' @{}
    exit 0
}
if ($action -eq 'select') {
    $si = $null
    if ($c.TryGetCurrentPattern([System.Windows.Automation.SelectionItemPattern]::Pattern, [ref]$si)) {
        $si.Select()
        Out-Json 'selected' @{}
        exit 0
    }
    Out-Json 'no_select_pattern' @{}
    exit 0
}
if ($action -eq 'scrollinto') {
    $sc = $null
    if ($c.TryGetCurrentPattern([System.Windows.Automation.ScrollItemPattern]::Pattern, [ref]$sc)) {
        $sc.ScrollIntoView()
        $r2 = $c.Current.BoundingRectangle
        $cx2 = [int]([Math]::Round($r2.X + $r2.Width / 2 - $originEl.X))
        $cy2 = [int]([Math]::Round($r2.Y + $r2.Height / 2 - $originEl.Y))
        Out-Json 'scrolled' @{'center_x'=$cx2; 'center_y'=$cy2}
        exit 0
    }
    Out-Json 'no_scroll_pattern' @{}
    exit 0
}
if ($action -eq 'getvalue') {
    $vp = $null
    if ($c.TryGetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern, [ref]$vp)) {
        Out-Json 'ok' @{'value'=[string]$vp.Current.Value}
        exit 0
    }
    Out-Json 'no_value_pattern' @{'value'= [string]$n}
    exit 0
}
if ($action -eq 'setvalue') {
    $vp = $null
    if ($c.TryGetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern, [ref]$vp)) {
        try {
            $vp.SetValue($value)
            Out-Json 'set' @{'value'=$value}
            exit 0
        } catch {
            Out-Json 'set_failed' @{'error'=$_.Exception.Message}
            exit 0
        }
    }
    Out-Json 'no_value_pattern' @{}
    exit 0
}
[Console]::Out.Write('{"status":"unknown_action"}')
"""

    async def _uia_control_action(
        self, hwnd: int, *, action: str, needle: str, index: int = 0, value: str = ""
    ) -> tuple[Optional[dict[str, Any]], str]:
        """UIA 按名操作控件（invoke/getvalue/setvalue），返回 (result|None, error)。"""
        script = (
            self._UIA_CONTROL_TEMPLATE
            .replace("%HWND%", str(int(hwnd)))
            .replace("%ACTION%", action)
            .replace("%NEEDLE%", str(needle).replace("'", "''"))
            .replace("%INDEX%", str(max(0, int(index))))
            .replace("%VALUE%", str(value).replace("'", "''"))
        )
        result = await asyncio.to_thread(
            command_exec.run_command,
            script,
            shell="powershell",
            timeout=max(30.0, float(getattr(self, "_command_timeout", 30)) * 2),
            max_output_chars=20_000,
        )
        output = str(result.get("output") or "").strip()
        # 剥离可能的 CLIXML 前缀噪音
        if output.startswith("#< CLIXML"):
            output = output.split("\n", 1)[-1].strip() if "\n" in output else ""
        if not result.get("success"):
            return None, (output[:300] or f"returncode={result.get('returncode')}")
        try:
            parsed = json.loads(output)
        except Exception as exc:
            return None, f"UIA 输出解析失败：{exc}"
        if not isinstance(parsed, dict):
            snippet = str(parsed)[:150]
            return None, f"UIA 输出格式异常：{snippet}"
        return parsed, ""

    def _uia_result_payload(self, parsed: dict[str, Any]) -> dict[str, Any]:
        payload = dict(parsed)
        payload.pop("status", None)
        return payload

    @plugin_entry(
        id="click_control",
        name=tr("entries.clickControl.name", default="点击控件"),
        description="按名称点击目标窗口控件（UIA Invoke，后台可操作）。",
        input_schema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "控件文本子串"},
                "index": {"type": "integer", "default": 0},
            },
            "required": ["name"],
        },
        llm_result_fields=["clicked", "name", "control_type", "message"],
    )
    async def click_control(self, name: str, index: int = 0, **_) -> Any:
        if sys.platform != "win32":
            return Err(SdkError("该功能仅支持 Windows"))
        name = str(name or "").strip()
        if not name:
            return Err(SdkError("name 不能为空"))
        hwnd, window = await asyncio.to_thread(self._require_operable_window)
        parsed, err = await self._uia_control_action(hwnd, action="invoke", needle=name, index=index)
        if parsed is None:
            return Err(SdkError(f"控件点击失败：{err}"))
        status = str(parsed.get("status") or "")
        payload = self._uia_result_payload(parsed)
        if status == "invoked" or status == "toggled":
            self._diary_record("input", f"UIA 点击控件「{parsed.get('name')}」[{status}]：{window.get('title')}")
            return Ok({
                "clicked": True,
                "method": status,
                **payload,
                "message": f"已通过 UIA {('切换' if status == 'toggled' else '点击')}控件「{parsed.get('name')}」（{payload.get('control_type')}）",
            })
        if status == "no_invoke_pattern":
            cx, cy = payload.get("center_x"), payload.get("center_y")
            hint = f"，可改用 keyboard_click_in_window({cx}, {cy}) 坐标点击" if isinstance(cx, int) and isinstance(cy, int) else ""
            return Ok({
                "clicked": False,
                **payload,
                "message": f"控件「{parsed.get('name')}」不支持 Invoke 模式{hint}",
            })
        if status == "not_found":
            return Ok({
                "clicked": False,
                "total_matches": payload.get("total_matches", 0),
                "message": f"未找到文本包含「{name}」的控件，可先 keyboard_inspect_controls 确认名称",
            })
        return Err(SdkError(f"控件点击异常：{payload.get('error') or status}"))

    @plugin_entry(
        id="get_control_value",
        name=tr("entries.getControlValue.name", default="读取控件值"),
        description="读取目标窗口控件的值（UIA Value 模式）。",
        input_schema={
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "index": {"type": "integer", "default": 0},
            },
            "required": ["name"],
        },
        llm_result_fields=["value", "name", "message"],
    )
    async def get_control_value(self, name: str, index: int = 0, **_) -> Any:
        if sys.platform != "win32":
            return Err(SdkError("该功能仅支持 Windows"))
        name = str(name or "").strip()
        if not name:
            return Err(SdkError("name 不能为空"))
        hwnd, _window = await asyncio.to_thread(self._require_operable_window)
        parsed, err = await self._uia_control_action(hwnd, action="getvalue", needle=name, index=index)
        if parsed is None:
            return Err(SdkError(f"读取控件失败：{err}"))
        status = str(parsed.get("status") or "")
        payload = self._uia_result_payload(parsed)
        if status == "ok":
            return Ok({"value": payload.get("value"), "name": payload.get("name"), "message": f"已读取控件「{payload.get('name')}」的值"})
        if status == "no_value_pattern":
            return Ok({"value": payload.get("value"), "name": payload.get("name"), "message": "该控件不支持 Value 模式，已回退返回其名称"})
        return Ok({
            "value": None,
            "message": f"未找到文本包含「{name}」的控件，可先 keyboard_inspect_controls 确认名称",
        })

    @plugin_entry(
        id="set_control_text",
        name=tr("entries.setControlText.name", default="写入控件文本"),
        description="向目标窗口输入框写入文本（UIA SetValue，免聚焦免打字）。",
        input_schema={
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "text": {"type": "string"},
                "index": {"type": "integer", "default": 0},
            },
            "required": ["name", "text"],
        },
        llm_result_fields=["written", "name", "message"],
    )
    async def set_control_text(self, name: str, text: str, index: int = 0, **_) -> Any:
        if sys.platform != "win32":
            return Err(SdkError("该功能仅支持 Windows"))
        name = str(name or "").strip()
        if not name:
            return Err(SdkError("name 不能为空"))
        text = str(text or "")
        hwnd, window = await asyncio.to_thread(self._require_operable_window)
        parsed, err = await self._uia_control_action(hwnd, action="setvalue", needle=name, index=index, value=text)
        if parsed is None:
            return Err(SdkError(f"写入控件失败：{err}"))
        status = str(parsed.get("status") or "")
        payload = self._uia_result_payload(parsed)
        if status == "set":
            self._diary_record("input", f"UIA 写入控件「{parsed.get('name')}」{len(text)} 字符：{window.get('title')}")
            return Ok({"written": True, "name": payload.get("name"), "message": f"已向控件「{payload.get('name')}」写入 {len(text)} 字符"})
        if status == "set_failed":
            return Err(SdkError(f"写入被拒绝（可能是只读/密码框）：{payload.get('error')}"))
        if status == "no_value_pattern":
            return Err(SdkError("该控件不支持文本写入（无 Value 模式），可改用 click 后 type_text"))
        return Err(SdkError(f"未找到文本包含「{name}」的输入框，可先 keyboard_inspect_controls 确认名称"))

    @plugin_entry(
        id="control_pattern",
        name=tr("entries.controlPattern.name", default="控件模式操作"),
        description="对匹配控件执行 UIA 模式（展开/收起/选中/滚动到可见）。",
        input_schema={
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "action": {"type": "string", "enum": ["expand", "collapse", "select", "scrollinto"]},
                "index": {"type": "integer", "default": 0},
            },
            "required": ["name", "action"],
        },
        llm_result_fields=["done", "status", "name", "message"],
    )
    async def control_pattern(self, name: str, action: str, index: int = 0, **_) -> Any:
        if sys.platform != "win32":
            return Err(SdkError("该功能仅支持 Windows"))
        name = str(name or "").strip()
        act = str(action or "").strip().lower()
        if not name:
            return Err(SdkError("name 不能为空"))
        if act not in ("expand", "collapse", "select", "scrollinto"):
            return Err(SdkError("action 必须是 expand/collapse/select/scrollinto"))
        hwnd, window = await asyncio.to_thread(self._require_operable_window)
        parsed, err = await self._uia_control_action(hwnd, action=act, needle=name, index=index)
        if parsed is None:
            return Err(SdkError(f"控件操作失败：{err}"))
        status = str(parsed.get("status") or "")
        payload = self._uia_result_payload(parsed)
        done_map = {
            "expanded": "已展开", "collapsed": "已收起", "selected": "已选中", "scrolled": "已滚动到可见",
        }
        if status in done_map:
            self._diary_record("input", f"UIA {done_map[status]}控件「{parsed.get('name')}」：{window.get('title')}")
            return Ok({
                "done": True,
                "status": status,
                **payload,
                "message": f"控件「{parsed.get('name')}」{done_map[status]}",
            })
        return Ok({
            "done": False,
            "status": status,
            **payload,
            "message": (
                f"控件「{parsed.get('name')}」不支持该模式（{status}）。"
                if status != "not_found"
                else f"未找到文本包含「{name}」的控件，可先 keyboard_inspect_controls 确认名称。"
            ),
        })

    @plugin_entry(
        id="wait_for_window",
        name=tr("entries.waitForWindow.name", default="等待窗口"),
        description="轮询等待匹配的窗口出现或消失。",
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "窗口标题/进程名关键字"},
                "disappear": {"type": "boolean", "default": False},
                "timeout": {"type": "number", "default": 15.0},
                "interval": {"type": "number", "default": 0.5},
            },
            "required": ["query"],
        },
        llm_result_fields=["found", "windows", "elapsed", "message"],
    )
    async def wait_for_window(self, query: str, disappear: bool = False, timeout: float = 15.0, interval: float = 0.5, **_) -> Any:
        if not _is_windows():
            return Err(SdkError("仅支持 Windows / Linux(X11) 桌面环境"))
        needle = str(query or "").strip().lower()
        if not needle:
            return Err(SdkError("query 不能为空"))
        wait_seconds = min(_MAX_WAIT_SECONDS, max(1.0, float(timeout or 15.0)))
        poll_gap = min(5.0, max(0.2, float(interval or 0.5)))
        started = time.time()
        deadline = started + wait_seconds

        def _find():
            matches = []
            for win in win32.enumerate_windows():
                title_l = str(win.get("title") or "").lower()
                proc_l = str(win.get("process_name") or "").lower()
                if needle in title_l or needle in proc_l:
                    matches.append({
                        "pid": win.get("pid"),
                        "hwnd": win.get("hwnd"),
                        "title": win.get("title"),
                        "process_name": win.get("process_name"),
                    })
            return matches

        while True:
            matches = await asyncio.to_thread(_find)
            exists = len(matches) > 0
            if disappear and not exists:
                elapsed = round(time.time() - started, 2)
                self._diary_record("wait", f"窗口「{query}」已消失（{elapsed}s）")
                return Ok({
                    "found": False,
                    "gone": True,
                    "windows": [],
                    "elapsed": elapsed,
                    "message": f"{elapsed}s 后确认「{query}」窗口已全部关闭",
                })
            if not disappear and exists:
                elapsed = round(time.time() - started, 2)
                top = matches[0]
                self._diary_record("wait", f"等到窗口「{top.get('title')}」（{elapsed}s）")
                return Ok({
                    "found": True,
                    "gone": False,
                    "windows": matches[:5],
                    "elapsed": elapsed,
                    "message": (
                        f"{elapsed}s 后等到 {len(matches)} 个匹配窗口，"
                        f"第一个：「{top.get('title')}」（pid {top.get('pid')}），"
                        "可用 keyboard_set_target(pid=...) 锁定后操作"
                    ),
                })
            if time.time() >= deadline:
                break
            await asyncio.sleep(poll_gap)

        elapsed = round(time.time() - started, 2)
        expect = "消失" if disappear else "出现"
        return Ok({
            # 等出现超时 = 没等到 → found=False；
            # 等消失超时 = 窗口仍在 → found=True（gone=False）
            "found": bool(disappear),
            "gone": False,
            "windows": [],
            "elapsed": elapsed,
            "message": f"{elapsed}s 内未等到「{query}」{expect}，可加大 timeout 重试或改用精确关键字。",
        })

    @plugin_entry(
        id="launch_app",
        name=tr("entries.launchApp.name", default="启动应用"),
        description="启动应用并等待其窗口出现后自动设为目标。",
        input_schema={
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "启动命令"},
                "window_query": {"type": "string", "description": "窗口标题/进程名关键字（可选）"},
                "timeout": {"type": "number", "default": 15.0},
            },
            "required": ["command"],
        },
        llm_result_fields=["launched", "target", "elapsed", "message"],
    )
    async def launch_app(self, command: str, window_query: str = "", timeout: float = 15.0, **_) -> Any:
        if not _is_windows():
            return Err(SdkError("仅支持 Windows / Linux(X11) 桌面环境"))
        if not self._launch_app_enabled:
            return Err(SdkError("launch_app 已被配置禁用（launch_app_enabled=false），请改用 run_command 手动启动"))
        command = str(command or "").strip()
        if not command:
            return Err(SdkError("command 不能为空"))
        query = str(window_query or "").strip() or command.split()[0].strip().strip('"')
        wait_seconds = min(60.0, max(2.0, float(timeout or 15.0)))

        result_ok, spawn_err = await asyncio.to_thread(
            command_exec.spawn_detached,
            command,
        )
        if not result_ok:
            return Err(SdkError(f"启动命令失败：{spawn_err[:200]}"))

        started = time.time()
        deadline = started + wait_seconds
        found: Optional[dict[str, Any]] = None
        needle = query.lower()
        # 快照启动前已有的匹配窗口：单实例应用（记事本等）再次“启动”只会
        # 在旧进程里开新窗口，甚至直接聚焦旧窗口——必须优先认领“新出现的”。
        pre_existing: set[int] = set()
        try:
            for win in await asyncio.to_thread(win32.enumerate_windows):
                t_l = str(win.get("title") or "").lower()
                p_l = str(win.get("process_name") or "").lower()
                if needle in t_l or needle in p_l:
                    pre_existing.add(int(win.get("hwnd") or 0))
        except Exception:
            pass
        while time.time() < deadline:
            windows = await asyncio.to_thread(win32.enumerate_windows)
            candidates_new = []
            candidates_old = []
            for win in windows:
                title_l = str(win.get("title") or "").lower()
                proc_l = str(win.get("process_name") or "").lower()
                if needle in title_l or needle in proc_l:
                    hwnd_i = int(win.get("hwnd") or 0)
                    if hwnd_i in pre_existing:
                        candidates_old.append(win)
                    else:
                        candidates_new.append(win)
            if candidates_new:
                found = candidates_new[0]
            elif candidates_old:
                found = candidates_old[0]
            if found is None:
                await asyncio.sleep(0.5)
                continue
            # 稳定性确认：部分应用（如打包版记事本）启动后引导进程会退出、
            # 由商店版接管（pid 变化）。要求窗口在 0.8s 后仍存在才算数。
            await asyncio.sleep(0.8)
            recheck = [w for w in await asyncio.to_thread(win32.enumerate_windows)
                       if int(w.get("hwnd") or 0) == int(found.get("hwnd") or 0)]
            if recheck:
                break
            found = None

        if found is None:
            return Ok({
                "launched": True,
                "target": None,
                "elapsed": round(time.time() - started, 2),
                "message": (
                    f"启动命令已执行，但 {wait_seconds}s 内未出现匹配「{query}」的窗口。"
                    "可用 keyboard_find_windows(query=...) 手动查找，或换更精确的 window_query 重试。"
                ),
            })

        target = {
            "pid": int(found.get("pid") or 0),
            "title": str(found.get("title") or ""),
            "process_name": str(found.get("process_name") or ""),
            "hwnd": int(found.get("hwnd") or 0),
        }
        block = await asyncio.to_thread(
            win32.input_safety_block_reason,
            pid=target["pid"],
            hwnd=target["hwnd"],
            process_name=target["process_name"],
            window_title=target["title"],
            block_anti_cheat=self._block_anti_cheat,
            block_elevated=self._block_elevated,
        )
        if block:
            return Err(SdkError(f"应用已启动，但安全策略拒绝将其设为目标：{block}"))
        self._target = target
        await self._persist_target(target)
        elapsed = round(time.time() - started, 2)
        self._diary_record("target", f"launch_app 启动并锁定目标：{target['title']}（pid {target['pid']}，{elapsed}s）")
        return Ok({
            "launched": True,
            "target": {"pid": target["pid"], "title": target["title"], "process_name": target["process_name"]},
            "elapsed": elapsed,
            "message": f"已启动并在 {elapsed}s 内锁定目标：{target['title']}（pid {target['pid']}），可直接开始键鼠操作",
        })

    # ── 轮询等待（异步时机）───────────────────────────────────────────

    async def _poll_frame_until(self, hwnd: int, *, mode: str, timeout: float, interval: float, check):
        """轮询截帧直到 ``check(frame)`` 返回非 None 或超时。

        Returns ``(found, result, elapsed)``；超时时 result 为 {"last_error": ...}。
        """
        mode_norm = str(mode or "target").strip().lower()
        if mode_norm not in ("target", "fullscreen"):
            raise SdkError("mode 必须是 'target' 或 'fullscreen'")
        wait_seconds = min(_MAX_WAIT_SECONDS, max(1.0, float(timeout or 10.0)))
        interval = min(5.0, max(0.2, float(interval or 0.5)))
        started = time.time()
        deadline = started + wait_seconds
        last_error = ""
        while True:
            try:
                frame = await self._capture_frame(mode=mode_norm)
                result = await check(frame, mode_norm)
                last_error = ""
            except SdkError as exc:
                result = None
                last_error = str(exc)
            if result is not None:
                return True, result, round(time.time() - started, 2)
            if time.time() >= deadline:
                return False, {"last_error": last_error}, round(time.time() - started, 2)
            await asyncio.sleep(interval)

    @plugin_entry(
        id="wait_for_text",
        name=tr("entries.waitForText.name", default="等待文字出现"),
        description="轮询等待屏幕出现指定文字，返回坐标（供点击）。",
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "要等待的文字"},
                "mode": {"type": "string", "enum": ["target", "fullscreen"], "default": "target"},
                "index": {"type": "integer", "default": 0},
                "timeout": {"type": "number", "default": 10.0},
                "interval": {"type": "number", "default": 0.5},
            },
            "required": ["query"],
        },
        llm_result_fields=["found", "screen_x", "screen_y", "elapsed", "message"],
    )
    async def wait_for_text(self, query: str, mode: str = "target", index: int = 0, timeout: float = 10.0, interval: float = 0.5, ocr_backend: str = "auto", **_) -> Any:
        if not _is_windows():
            return Err(SdkError("仅支持 Windows / Linux(X11) 桌面环境"))
        query = str(query or "").strip()
        if not query:
            return Err(SdkError("query 不能为空"))
        idx = max(0, int(index or 0))
        backend_norm = str(ocr_backend or "auto").strip().lower()
        if backend_norm not in ("auto", "fast", "rapid"):
            return Err(SdkError("ocr_backend 必须是 auto/fast/rapid"))
        use_fast = backend_norm in ("auto", "fast") and sys.platform == "win32"
        if backend_norm == "fast" and use_fast and not await asyncio.to_thread(capture.windows_fast_ocr_available):
            return Err(SdkError("系统快速 OCR 不可用（OcrEngine 创建失败），请改用 ocr_backend='rapid'"))

        async def _check(frame, mode_norm):
            if use_fast:
                text, st = await asyncio.to_thread(capture.windows_fast_ocr_text, frame)
                if st != "ok":
                    return None
                # 系统 OCR 词间会插空格、下划线常被识别成连字符——归一化后比对
                def _norm(s: str) -> str:
                    return str(s or "").lower().replace(" ", "").replace("-", "").replace("_", "")
                norm_hay = _norm(text)
                norm_needle = _norm(query)
                if norm_needle and norm_needle in norm_hay:
                    # 快速通道无坐标框：命中即返回标记，坐标由 find_text 补取
                    return {"fast": True, "text": str(text)}
                return None
            _text, boxes, status = await asyncio.to_thread(capture.ocr_image_with_boxes, frame, query=query)
            if status != "ok" or not boxes:
                return None
            box = boxes[min(idx, len(boxes) - 1)]
            fw, fh = frame.size
            sx, sy = await self._frame_point_to_screen(
                hwnd,
                mode=mode_norm,
                x=(box["left"] + box["right"]) / 2,
                y=(box["top"] + box["bottom"]) / 2,
                frame_size=(fw, fh),
            )
            return {"box": box, "screen_x": sx, "screen_y": sy, "total_matches": len(boxes)}

        hwnd, window = await asyncio.to_thread(self._require_operable_window)
        try:
            found, result, elapsed = await self._poll_frame_until(hwnd, mode=mode, timeout=timeout, interval=interval, check=_check)
        except SdkError as exc:
            return Err(exc)

        if found:
            if result.get("fast"):
                self._diary_record("wait", f"快速OCR等到「{query[:20]}」（{elapsed}s）")
                return Ok({
                    "found": True,
                    "screen_x": None,
                    "screen_y": None,
                    "matched_text": result.get("text"),
                    "elapsed": elapsed,
                    "ocr_backend": "fast",
                    "message": (
                        f"{elapsed}s 后屏幕上已出现包含「{query}」的文字（系统快速 OCR）。"
                        "需要精确坐标请再调 keyboard_find_text(query=...)。"
                    ),
                })
            box = result["box"]
            matched = str(box.get("text") or "")
            self._diary_record("wait", f"等待「{query[:20]}」出现（{elapsed}s）")
            return Ok({
                "found": True,
                "screen_x": result["screen_x"],
                "screen_y": result["screen_y"],
                "matched_text": matched,
                "total_matches": result["total_matches"],
                "elapsed": elapsed,
                "message": f"{elapsed}s 后等到「{matched[:24]}」（屏幕 {result['screen_x']}, {result['screen_y']}）",
            })
        last_error = str(result.get("last_error") or "")
        hint = f"（期间错误：{last_error}）" if last_error else ""
        return Ok({
            "found": False,
            "elapsed": elapsed,
            "message": f"{elapsed}s 内未等到包含「{query}」的文字{hint}。可加大 timeout 重试，或用 keyboard_capture/see_screen 看当前画面。",
        })

    @plugin_entry(
        id="wait_for_image",
        name=tr("entries.waitForImage.name", default="等待图片出现"),
        description="轮询等待屏幕出现与模板匹配的画面，返回坐标。",
        input_schema={
            "type": "object",
            "properties": {
                "template_path": {"type": "string", "description": "模板图片路径"},
                "mode": {"type": "string", "enum": ["target", "fullscreen"], "default": "target"},
                "min_score": {"type": "number", "default": 0.75},
                "index": {"type": "integer", "default": 0},
                "timeout": {"type": "number", "default": 10.0},
                "interval": {"type": "number", "default": 0.5},
            },
            "required": ["template_path"],
        },
        llm_result_fields=["found", "screen_x", "screen_y", "score", "elapsed", "message"],
    )
    async def wait_for_image(self, template_path: str, mode: str = "target", min_score: float = 0.75, index: int = 0, timeout: float = 10.0, interval: float = 0.5, **_) -> Any:
        if not _is_windows():
            return Err(SdkError("仅支持 Windows / Linux(X11) 桌面环境"))
        if not template_match.is_available():
            return Err(SdkError("numpy 不可用，无法进行模板匹配"))
        idx = max(0, int(index or 0))
        try:
            template_image = await self._load_template_image(template_path)
        except SdkError as exc:
            return Err(exc)
        hwnd, window = await asyncio.to_thread(self._require_operable_window)

        async def _check(frame, mode_norm):
            matches = await asyncio.to_thread(
                template_match.find_template,
                frame, template_image,
                min_score=float(min_score or 0.75),
                top_k=max(1, idx + 1),
            )
            if not matches:
                return None
            match = matches[min(idx, len(matches) - 1)]
            fw, fh = frame.size
            sx, sy = await self._frame_point_to_screen(
                hwnd,
                mode=mode_norm,
                x=match["x"],
                y=match["y"],
                frame_size=(fw, fh),
            )
            return {"match": match, "screen_x": sx, "screen_y": sy, "total_matches": len(matches)}

        try:
            found, result, elapsed = await self._poll_frame_until(hwnd, mode=mode, timeout=timeout, interval=interval, check=_check)
        except SdkError as exc:
            return Err(exc)

        if found:
            match = result["match"]
            self._diary_record("wait", f"等待图片匹配 score={match.get('score')}（{elapsed}s）：{str(template_path)[:40]}")
            return Ok({
                "found": True,
                "screen_x": result["screen_x"],
                "screen_y": result["screen_y"],
                "score": match.get("score"),
                "total_matches": result["total_matches"],
                "elapsed": elapsed,
                "message": f"{elapsed}s 后等到匹配（屏幕 {result['screen_x']}, {result['screen_y']}，score={match.get('score')}）",
            })
        last_error = str(result.get("last_error") or "")
        hint = f"（期间错误：{last_error}）" if last_error else ""
        return Ok({
            "found": False,
            "elapsed": elapsed,
            "message": f"{elapsed}s 内未等到匹配画面{hint}。可降低 min_score、加大 timeout 重试。",
        })

    @plugin_entry(
        id="wait_for_idle",
        name=tr("entries.waitForIdle.name", default="等待画面稳定"),
        description="轮询等待画面停止变化（加载/动画结束），返回是否稳定。",
        input_schema={
            "type": "object",
            "properties": {
                "mode": {"type": "string", "enum": ["target", "fullscreen"], "default": "target"},
                "timeout": {"type": "number", "default": 15.0},
                "interval": {"type": "number", "default": 0.5},
                "threshold": {"type": "number", "default": 3.0},
                "stable_count": {"type": "integer", "default": 2},
            },
        },
        llm_result_fields=["stable", "elapsed", "message"],
    )
    async def wait_for_idle(self, mode: str = "target", timeout: float = 15.0, interval: float = 0.5, threshold: float = 3.0, stable_count: int = 2, **_) -> Any:
        if not _is_windows():
            return Err(SdkError("仅支持 Windows / Linux(X11) 桌面环境"))
        hwnd, window = await asyncio.to_thread(self._require_operable_window)
        await asyncio.to_thread(self._focus_or_raise, hwnd)
        mode_norm = str(mode or "target").strip().lower()
        if mode_norm not in ("target", "fullscreen"):
            return Err(SdkError("mode 必须是 'target' 或 'fullscreen'"))
        wait_seconds = min(_MAX_WAIT_SECONDS, max(1.0, float(timeout or 15.0)))
        poll_gap = min(5.0, max(0.2, float(interval or 0.5)))
        diff_limit = min(50.0, max(0.5, float(threshold or 3.0)))
        need_stable = min(5, max(1, int(stable_count or 2)))
        started = time.time()
        deadline = started + wait_seconds
        prev_sig: Optional[list[int]] = None
        stable_run = 0
        while True:
            try:
                frame = await self._capture_frame(mode=mode_norm)
            except SdkError:
                if time.time() >= deadline:
                    break
                await asyncio.sleep(poll_gap)
                continue
            sig = await asyncio.to_thread(_frame_signature, frame)
            if prev_sig is not None:
                if _signature_diff(prev_sig, sig) < diff_limit:
                    stable_run += 1
                else:
                    stable_run = 0
            prev_sig = sig
            if stable_run >= need_stable:
                elapsed = round(time.time() - started, 2)
                self._diary_record("wait", f"画面稳定（{elapsed}s，{need_stable} 次连续一致）[{mode_norm}]")
                return Ok({
                    "stable": True,
                    "elapsed": elapsed,
                    "stable_count": stable_run,
                    "message": f"画面已稳定 {elapsed}s（连续 {stable_run} 次截屏几乎一致），可以进行下一步",
                })
            if time.time() >= deadline:
                break
            await asyncio.sleep(poll_gap)
        elapsed = round(time.time() - started, 2)
        return Ok({
            "stable": False,
            "elapsed": elapsed,
            "stable_count": stable_run,
            "message": f"{elapsed}s 内画面仍在变化（当前连续稳定 {stable_run}/{need_stable} 次）。可加大 timeout/threshold 重试，或用 see_screen 看实际画面。",
        })

    # ── Agent 自省（态势/回溯）────────────────────────────────────────

    # ── 上下文管理（任务状态暂存）─────────────────────────────────────

    @plugin_entry(
        id="set_task_state",
        name=tr("entries.setTaskState.name", default="记录任务状态"),
        description="把任务目标/进度/下一步写入持久化暂存本（上下文管理）。",
        input_schema={
            "type": "object",
            "properties": {
                "goal": {"type": "string"},
                "status": {"type": "string"},
                "next_step": {"type": "string"},
                "clear": {"type": "boolean", "default": False},
            },
        },
        llm_result_fields=["saved", "task_state", "message"],
    )
    async def set_task_state(self, goal: str = "", status: str = "", next_step: str = "", clear: bool = False, **_) -> Any:
        if clear:
            await self.store.delete(_STORE_TASK_STATE_KEY)
            self._diary_record("note", "清空任务状态")
            return Ok({"cleared": True, "task_state": None, "message": "任务状态已清空"})

        current = unwrap_or(await self.store.get(_STORE_TASK_STATE_KEY), None)
        state = dict(current) if isinstance(current, dict) else {}
        updated: list[str] = []
        for field, raw in (("goal", goal), ("status", status), ("next_step", next_step)):
            value = str(raw or "").strip()[:_MAX_TASK_FIELD_CHARS]
            if value:
                state[field] = value
                updated.append(field)
        if not updated and not state.get("goal"):
            return Err(SdkError("请至少提供 goal/status/next_step 之一（或 clear=true 清空）"))
        state["updated_at"] = datetime.now().strftime("%H:%M:%S")
        await self.store.set(_STORE_TASK_STATE_KEY, state)
        summary = "；".join(f"{f}={state.get(f)}" for f in ("goal", "status", "next_step") if state.get(f))
        self._diary_record("note", f"更新任务状态：{summary[:120]}")
        return Ok({
            "saved": True,
            "updated_fields": updated,
            "task_state": state,
            "message": f"任务状态已保存（{len(updated)} 个字段更新），上下文丢失后可用 get_task_state/agent_brief 恢复",
        })

    @plugin_entry(
        id="get_task_state",
        name=tr("entries.getTaskState.name", default="读取任务状态"),
        description="读取持久化的任务状态。",
    )
    async def get_task_state(self, **_) -> Any:
        state = unwrap_or(await self.store.get(_STORE_TASK_STATE_KEY), None)
        if not isinstance(state, dict) or not any(state.get(f) for f in ("goal", "status", "next_step")):
            return Ok({"task_state": None, "message": "暂无任务状态记录，可用 set_task_state 写入"})
        summary = "；".join(f"{f}={state.get(f)}" for f in ("goal", "status", "next_step") if state.get(f))
        return Ok({"task_state": state, "message": f"当前任务：{summary}"})

    @plugin_entry(
        id="agent_brief",
        name=tr("entries.agentBrief.name", default="态势一览"),
        description="一次拿到目标/安全开关/能力可用性/最近动作概览。",
    )
    async def agent_brief(self, **_) -> Any:
        is_fullscreen = bool(self._target) and int((self._target or {}).get("pid") or 0) == _FULLSCREEN_PID
        focused = False
        if _is_windows():
            if self._target is None:
                focused = False
            elif is_fullscreen:
                focused = (await asyncio.to_thread(win32.foreground_hwnd)) > 0
            else:
                win_obj = await asyncio.to_thread(win32.find_window_for_pid, int(self._target.get("pid") or 0))
                if win_obj is not None:
                    focused = await asyncio.to_thread(
                        win32.foreground_matches,
                        int(win_obj.get("hwnd") or 0),
                        int(self._target.get("pid") or 0),
                    )
        capture_info: dict[str, Any] = {}
        audio_info: dict[str, Any] = {}
        if _is_windows():
            capture_info = await asyncio.to_thread(capture.describe_capture)
            audio_info = await asyncio.to_thread(audio_analysis.describe_audio)

        day = datetime.now().strftime("%Y-%m-%d")
        counts: dict[str, int] = {}
        recent_items: list[dict[str, Any]] = []
        if self._diary is not None:
            counts = self._diary.counts(day)
            for e in self._diary.events(day)[-5:]:
                recent_items.append({
                    "time": datetime.fromtimestamp(float(e.get("ts") or 0)).strftime("%H:%M:%S"),
                    "kind": str(e.get("kind") or ""),
                    "detail": str(e.get("detail") or ""),
                    "ok": bool(e.get("ok", True)),
                })

        async with self._pending_lock:
            pending_count = len([
                p for p in self._pending_commands
                if p.get("status") in ("pending", "running")
            ])

        task_state = unwrap_or(await self.store.get(_STORE_TASK_STATE_KEY), None)
        if not isinstance(task_state, dict) or not any(task_state.get(f) for f in ("goal", "status", "next_step")):
            task_state = None

        brief = {
            "task_state": task_state,
            "platform_ok": _is_windows(),
            "target": None if self._target is None else {
                "pid": self._target.get("pid"),
                "title": self._target.get("title"),
                "process_name": self._target.get("process_name"),
            },
            "fullscreen_mode": is_fullscreen,
            "focused": focused,
            "safety": {
                "block_anti_cheat": bool(getattr(self, "_block_anti_cheat", True)),
                "block_elevated_target": bool(getattr(self, "_block_elevated", True)),
                "require_focus": bool(getattr(self, "_require_focus", True)),
                "allow_unguided": bool(getattr(self, "_allow_unguided", False)),
            },
            "command_confirmation": bool(self._command_require_confirmation),
            "input_speed_profile": getattr(self, "_input_speed_profile", "normal"),
            "command_whitelist": list(getattr(self, "_command_auto_prefixes", []) or []),
            "pending_commands": pending_count,
            "capabilities": {
                "ocr": bool(capture_info.get("ocr_available", False)),
                "template_match": template_match.is_available(),
                "audio": bool(audio_info.get("available", False)),
            },
            "today_activity": {"date": day, "counts": counts, "total": sum(counts.values())},
            "recent_actions": recent_items,
        }
        return Ok({**brief, "message": (
            f"任务={((task_state or {}).get('status') or (task_state or {}).get('goal') or '未记录')}；"
            f"目标={'全屏模式' if is_fullscreen else ((self._target or {}).get('title') or '未设置')}，"
            f"前台{'已' if focused else '未'}聚焦；OCR {'可用' if brief['capabilities']['ocr'] else '不可用'}；"
            f"今日 {brief['today_activity']['total']} 条操作"
        )})

    @plugin_entry(
        id="recent_actions",
        name=tr("entries.recentActions.name", default="最近操作"),
        description="返回最近的操作事件列表（供猫娘回溯上下文）。",
        input_schema={
            "type": "object",
            "properties": {"n": {"type": "integer", "default": 10}},
        },
        llm_result_fields=["items", "count", "date"],
    )
    async def recent_actions(self, n: int = 10, **_) -> Any:
        count = min(50, max(1, int(n or 10)))
        if self._diary is None:
            return Ok({"items": [], "count": 0, "date": datetime.now().strftime("%Y-%m-%d"), "message": "日记未启用"})
        day = datetime.now().strftime("%Y-%m-%d")
        events = self._diary.events(day)[-count:]
        items = [
            {
                "time": datetime.fromtimestamp(float(e.get("ts") or 0)).strftime("%H:%M:%S"),
                "kind": str(e.get("kind") or ""),
                "detail": str(e.get("detail") or ""),
                "ok": bool(e.get("ok", True)),
            }
            for e in events
        ]
        return Ok({
            "items": items,
            "count": len(items),
            "date": day,
            "message": f"共 {len(items)} 条最近操作" + ("（日记未启用写入，仅有本次会话内存记录）" if not items else ""),
        })

    @ui.action(
        label=tr("actions.clickInWindow.label", default="Click in window"),
        icon="C",
        group="input",
        order=35,
        refresh_context=False,
    )
    @plugin_entry(
        id="click_in_window",
        name=tr("entries.clickInWindow.name", default="窗口内点击"),
        description="在目标窗口内部相对坐标处点击（自动换算屏幕坐标）。",
        input_schema={
            "type": "object",
            "properties": {
                "x": {"type": "integer", "description": "窗口内 x"},
                "y": {"type": "integer", "description": "窗口内 y"},
                "button": {"type": "string", "enum": ["left", "right", "middle"], "default": "left"},
                "clicks": {"type": "integer", "default": 1},
            },
            "required": ["x", "y"],
        },
        llm_result_fields=["clicked", "screen_x", "screen_y", "message"],
    )
    async def click_in_window(self, x: int, y: int, button: str = "left", clicks: int = 1, **_) -> Any:
        if self._target is not None and int(self._target.get("pid") or 0) == _FULLSCREEN_PID:
            return Err(SdkError("全屏模式下没有窗口内坐标概念，请直接使用 keyboard_mouse_click 屏幕绝对坐标"))
        hwnd, window = await asyncio.to_thread(self._require_operable_window)
        await asyncio.to_thread(self._focus_or_raise, hwnd)
        rect = await asyncio.to_thread(win32.window_client_rect, hwnd)
        if rect is None:
            return Err(SdkError("无法获取窗口客户区，目标可能已关闭"))
        cx, cy = int(x), int(y)
        if cx < 0 or cy < 0 or cx >= rect["width"] or cy >= rect["height"]:
            return Err(SdkError(
                f"坐标 ({cx}, {cy}) 超出窗口客户区 {rect['width']}x{rect['height']}，"
                "请先调用 keyboard_get_window_rect 确认范围后再试"
            ))
        sx, sy = rect["left"] + cx, rect["top"] + cy
        clicks = min(_MAX_CLICKS, max(1, int(clicks or 1)))
        await asyncio.to_thread(
            win32.mouse_click, sx, sy, button=str(button or "left"), clicks=clicks,
        )
        self._diary_record(
            "input",
            f"窗口内{str(button or 'left')}键点击 ({x}, {y}) ×{clicks}",
        )
        return Ok({
            "clicked": True,
            "screen_x": sx,
            "screen_y": sy,
            "clicks": clicks,
            "message": f"已点击窗口内 ({x}, {y})（屏幕 {sx}, {sy}）",
        })

    async def _load_template_image(self, template_path: str):
        """解析并加载模板图片（绝对路径或工作区相对路径），失败抛 SdkError。"""
        path = str(template_path or "").strip()
        if not path:
            raise SdkError("template_path 不能为空")

        def _resolve() -> str:
            template_file = os.path.abspath(path)
            if os.path.isfile(template_file):
                return template_file
            alt = os.path.join(self._workspace_path(), path)
            if os.path.isfile(alt):
                return alt
            raise SdkError(f"找不到模板图片：{path}")

        template_file = await asyncio.to_thread(_resolve)
        try:
            from PIL import Image

            image = await asyncio.to_thread(Image.open, template_file)
            return await asyncio.to_thread(image.convert, "RGB")
        except Exception as exc:
            raise SdkError(f"无法加载模板图片：{exc}") from exc

    async def _frame_point_to_screen(
        self,
        hwnd: int,
        *,
        mode: str,
        x: float,
        y: float,
        frame_size: tuple[int, int],
    ) -> tuple[int, int]:
        """把截图帧内坐标换算为屏幕绝对坐标（自动补偿截图缩放），失败抛 SdkError。"""
        fw, fh = int(frame_size[0]), int(frame_size[1])
        mode_norm = str(mode or "target").strip().lower()
        if mode_norm == "target":
            wrect = await asyncio.to_thread(win32.window_rect, hwnd)
            if wrect is None:
                raise SdkError("无法获取窗口坐标，目标可能已关闭")
            ox, oy = int(wrect["left"]), int(wrect["top"])
            rw, rh = int(wrect["right"]) - ox, int(wrect["bottom"]) - oy
        else:
            vx, vy, vw, vh = await asyncio.to_thread(win32._virtual_screen)
            ox, oy = vx, vy
            rw, rh = vw, vh
        if fw <= 0 or fh <= 0 or rw <= 0 or rh <= 0:
            raise SdkError("截图区域尺寸无效，无法换算坐标")
        return ox + int(round(float(x) * rw / fw)), oy + int(round(float(y) * rh / fh))

    async def _capture_frame(self, *, mode: str):
        """mode='target' 截目标窗口、'fullscreen' 截全屏，失败抛 SdkError。"""
        mode = str(mode or "target").strip().lower()
        if mode not in ("target", "fullscreen"):
            raise SdkError("mode 必须是 'target' 或 'fullscreen'")
        try:
            if mode == "target":
                if self._target is None:
                    raise SdkError("尚未设置目标窗口，请先调用 set_target（或改用 mode='fullscreen'）")
                pid = int(self._target.get("pid") or 0)
                window = await asyncio.to_thread(capture.target_window_for_capture, pid)
                if window is None:
                    raise SdkError(f"找不到 pid={pid} 的可见窗口，目标可能已关闭")
                return await asyncio.to_thread(capture.capture_window, window)
            return await asyncio.to_thread(capture.capture_fullscreen)
        except Exception as exc:
            if isinstance(exc, SdkError):
                raise
            raise SdkError(f"截图失败：{exc}") from exc

    def _apply_region_crop(
        self, image, region: str
    ) -> tuple[Any, Optional[dict[str, int]], Optional[str]]:
        """按 'x,y,w,h'（帧内相对像素，可含空格）裁剪截图。

        Returns (cropped_image|原图, crop_origin|None, error|None)。
        裁剪后匹配/OCR 的坐标需加回 origin 才是全帧坐标。
        """
        raw = str(region or "").strip()
        if not raw:
            return image, None, None
        parts = [p for p in re.split(r"[,\s]+", raw) if p]
        if len(parts) != 4:
            return None, None, "region 必须是 'x,y,w,h' 四个数字"
        try:
            rx, ry, rw, rh = (int(float(p)) for p in parts)
        except ValueError:
            return None, None, "region 必须是四个整数"
        if rw <= 0 or rh <= 0:
            return None, None, "region 宽高必须为正"
        fw, fh = image.size
        left = max(0, min(int(rx), fw - 1))
        top = max(0, min(int(ry), fh - 1))
        right = max(left + 1, min(int(rx + rw), fw))
        bottom = max(top + 1, min(int(ry + rh), fh))
        cropped = image.crop((left, top, right, bottom))
        return cropped, {"left": left, "top": top}, None

    def _offset_match(self, match: dict[str, Any], crop_origin: Optional[dict[str, int]]) -> dict[str, Any]:
        """把模板匹配结果平移回全帧坐标系。"""
        if not crop_origin:
            return match
        shifted = dict(match)
        shifted["x"] = int(match.get("x", 0)) + crop_origin["left"]
        shifted["y"] = int(match.get("y", 0)) + crop_origin["top"]
        return shifted

    @ui.action(
        label=tr("actions.findImage.label", default="Find image"),
        icon="I",
        group="capture",
        order=30,
        refresh_context=False,
    )
    @plugin_entry(
        id="find_image",
        name=tr("entries.findImage.name", default="查找图片"),
        description="在屏幕/窗口内查找与模板图片匹配的位置，返回中心坐标。",
        input_schema={
            "type": "object",
            "properties": {
                "template_path": {"type": "string", "description": "模板图片路径"},
                "mode": {"type": "string", "enum": ["target", "fullscreen"], "default": "target"},
                "min_score": {"type": "number", "default": 0.75},
                "max_results": {"type": "integer", "default": 5},
            },
            "required": ["template_path"],
        },
        llm_result_fields=["ok", "matches", "mode", "message"],
    )
    async def find_image(self, template_path: str, mode: str = "target", min_score: float = 0.75, max_results: int = 5, region: str = "", **_) -> Any:
        if not _is_windows():
            return Err(SdkError("仅支持 Windows / Linux(X11) 桌面环境"))
        if not template_match.is_available():
            return Err(SdkError("numpy 不可用，无法进行模板匹配"))
        try:
            template_image = await self._load_template_image(template_path)
            frame_image = await self._capture_frame(mode=mode)
            frame_image, crop_origin, reg_err = self._apply_region_crop(frame_image, region)
            if reg_err:
                return Err(SdkError(reg_err))
        except SdkError as exc:
            return Err(exc)

        raw_matches = await asyncio.to_thread(
            template_match.find_template,
            frame_image, template_image,
            min_score=float(min_score or 0.75),
            top_k=int(max_results or 5),
        )
        matches = [self._offset_match(m, crop_origin) for m in raw_matches]
        return Ok({
            "ok": True,
            "matches": matches,
            "mode": str(mode or "target").strip().lower(),
            "count": len(matches),
            "message": (
                f"找到 {len(matches)} 处匹配"
                + ("；大屏截图可能被缩放，要直接点击请用 keyboard_click_image" if matches else "")
            ),
        })

    @plugin_entry(
        id="click_image",
        name=tr("entries.clickImage.name", default="找图点击"),
        description="查找模板图片匹配位置并直接点击（自动处理截图缩放）。",
        input_schema={
            "type": "object",
            "properties": {
                "template_path": {"type": "string", "description": "模板图片路径"},
                "mode": {"type": "string", "enum": ["target", "fullscreen"], "default": "target"},
                "min_score": {"type": "number", "default": 0.75},
                "index": {"type": "integer", "default": 0},
                "button": {"type": "string", "enum": ["left", "right", "middle"], "default": "left"},
                "clicks": {"type": "integer", "default": 1},
            },
            "required": ["template_path"],
        },
        llm_result_fields=["clicked", "screen_x", "screen_y", "score", "message"],
    )
    async def click_image(
        self,
        template_path: str,
        mode: str = "target",
        min_score: float = 0.75,
        index: int = 0,
        button: str = "left",
        clicks: int = 1,
        **_,
    ) -> Any:
        if not _is_windows():
            return Err(SdkError("仅支持 Windows / Linux(X11) 桌面环境"))
        if not template_match.is_available():
            return Err(SdkError("numpy 不可用，无法进行模板匹配"))

        hwnd, window = await asyncio.to_thread(self._require_operable_window)
        await asyncio.to_thread(self._focus_or_raise, hwnd)
        try:
            template_image = await self._load_template_image(template_path)
            frame_image = await self._capture_frame(mode=mode)
        except SdkError as exc:
            return Err(exc)

        matches = await asyncio.to_thread(
            template_match.find_template,
            frame_image, template_image,
            min_score=float(min_score or 0.75),
            top_k=max(3, int(index or 0) + 3),
        )
        idx = max(0, int(index or 0))
        if not matches or idx >= len(matches):
            return Err(SdkError(
                f"未找到可点击的匹配（index={idx}，共 {len(matches)} 处），"
                "可降低 min_score 或确认模板图片与当前画面一致"
            ))
        match = matches[idx]
        fw, fh = frame_image.size

        mode_norm = str(mode or "target").strip().lower()
        try:
            sx, sy = await self._frame_point_to_screen(
                hwnd, mode=mode_norm, x=match["x"], y=match["y"], frame_size=(fw, fh),
            )
        except SdkError as exc:
            return Err(exc)
        n_clicks = min(_MAX_CLICKS, max(1, int(clicks or 1)))
        await asyncio.to_thread(
            win32.mouse_click, sx, sy,
            button=str(button or "left"),
            clicks=n_clicks,
        )
        self._diary_record(
            "input",
            f"找图点击 ({sx}, {sy}) score={match.get('score')} [{mode_norm}] 模板={str(template_path)[:40]}",
        )
        return Ok({
            "clicked": True,
            "screen_x": sx,
            "screen_y": sy,
            "score": match.get("score"),
            "index": idx,
            "total_matches": len(matches),
            "clicks": n_clicks,
            "message": f"已点击第 {idx + 1} 处匹配（屏幕 {sx}, {sy}，score={match.get('score')}）",
        })

    @plugin_entry(
        id="click_text",
        name=tr("entries.clickText.name", default="找字点击"),
        description="查找屏幕文字位置并直接点击其中心（自动处理缩放）。",
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "要点击的文字"},
                "mode": {"type": "string", "enum": ["target", "fullscreen"], "default": "target"},
                "index": {"type": "integer", "default": 0},
                "button": {"type": "string", "enum": ["left", "right", "middle"], "default": "left"},
                "clicks": {"type": "integer", "default": 1},
            },
            "required": ["query"],
        },
        llm_result_fields=["clicked", "screen_x", "screen_y", "matched_text", "message"],
    )
    async def click_text(self, query: str, mode: str = "target", index: int = 0, button: str = "left", clicks: int = 1, region: str = "", **_) -> Any:
        if not _is_windows():
            return Err(SdkError("仅支持 Windows / Linux(X11) 桌面环境"))
        query = str(query or "").strip()
        if not query:
            return Err(SdkError("query 不能为空"))
        hwnd, window = await asyncio.to_thread(self._require_operable_window)
        await asyncio.to_thread(self._focus_or_raise, hwnd)
        try:
            image = await self._capture_frame(mode=mode)
            orig_size = image.size
            image, crop_origin, reg_err = self._apply_region_crop(image, region)
            if reg_err:
                return Err(SdkError(reg_err))
        except SdkError as exc:
            return Err(exc)

        _text, boxes, status = await asyncio.to_thread(capture.ocr_image_with_boxes, image, query=query)
        idx = max(0, int(index or 0))
        if status == "unavailable" or status == "ocr_failed":
            return Err(SdkError(f"OCR 不可用或失败（status={status}）"))
        if status != "ok" or idx >= len(boxes):
            detail = "未识别到任何文字" if status == "empty" else f"没有找到包含「{query}」的文字（共 {len(boxes)} 处匹配）"
            return Err(SdkError(f"{detail}，可先用 keyboard_capture 确认识别内容后再试"))

        box = boxes[idx]
        fw, fh = orig_size
        cx = (box["left"] + box["right"]) / 2 + (crop_origin["left"] if crop_origin else 0)
        cy = (box["top"] + box["bottom"]) / 2 + (crop_origin["top"] if crop_origin else 0)
        try:
            sx, sy = await self._frame_point_to_screen(
                hwnd, mode=str(mode or "target"), x=cx, y=cy, frame_size=(fw, fh),
            )
        except SdkError as exc:
            return Err(exc)
        n_clicks = min(_MAX_CLICKS, max(1, int(clicks or 1)))
        await asyncio.to_thread(win32.mouse_click, sx, sy, button=str(button or "left"), clicks=n_clicks)
        matched = str(box.get("text") or "")
        self._diary_record("input", f"找字点击「{matched[:24]}」({sx}, {sy}) [{str(mode or 'target')}]")
        return Ok({
            "clicked": True,
            "screen_x": sx,
            "screen_y": sy,
            "matched_text": matched,
            "score": box.get("score"),
            "index": idx,
            "total_matches": len(boxes),
            "clicks": n_clicks,
            "message": f"已点击「{matched[:24]}」（屏幕 {sx}, {sy}）",
        })

    @plugin_entry(
        id="see_screen",
        name=tr("entries.seeScreen.name", default="视觉截图"),
        description="截取屏幕图像推送到对话，供多模态模型直接看图分析。",
        input_schema={
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "enum": ["target", "fullscreen"],
                    "default": "target",
                },
            },
        },
        llm_result_fields=["pushed", "width", "height", "message"],
    )
    async def see_screen(self, mode: str = "target", **_) -> Any:
        if not _is_windows():
            return Err(SdkError("仅支持 Windows / Linux(X11) 桌面环境"))
        hwnd, window = await asyncio.to_thread(self._require_operable_window)
        await asyncio.to_thread(self._focus_or_raise, hwnd)
        try:
            image = await self._capture_frame(mode=mode)
        except SdkError as exc:
            return Err(exc)

        import io as _io

        buffer = _io.BytesIO()
        image.save(buffer, format="JPEG", quality=72)
        jpeg_bytes = buffer.getvalue()
        width, height = image.size

        mode_norm = str(mode or "target").strip().lower()
        target_label = "全屏" if mode_norm == "fullscreen" else str(window.get("title") or "目标窗口")
        try:
            receipt = self.push_message(
                parts=[
                    {"type": "image", "data": jpeg_bytes, "mime": "image/jpeg"},
                    {
                        "type": "text",
                        "text": (
                            f"[按键控制] 当前屏幕截图（mode={mode_norm}，目标：{target_label}）。"
                            "请描述你看到的界面，并决定下一步键鼠操作。"
                        ),
                    },
                ],
                visibility=["chat"],
                ai_behavior="respond",
                source="keyboard_controller",
                metadata={"plugin_id": "keyboard_controller", "kind": "vision_capture"},
            )
        except Exception as exc:
            return Err(SdkError(f"推送截图失败：{exc}"))
        if not bool(getattr(receipt, "submitted", True)):
            return Err(SdkError("截图推送未被接收（host 未确认），请稍后重试"))

        self._diary_record("capture", f"视觉截图已推送（{mode_norm}）{width}x{height}")
        return Ok({
            "pushed": True,
            "width": width,
            "height": height,
            "message": (
                f"截图已作为带图像的新消息推送到对话（{width}x{height}），"
                "请在接下来的消息中直接查看该图像后再决定操作"
            ),
        })

    # ── 截图 + OCR（供非视觉模型读屏） ───────────────────────────────

    @ui.action(
        label=tr("actions.capture.label", default="Capture + OCR"),
        icon="S",
        group="capture",
        order=10,
        refresh_context=False,
    )
    @plugin_entry(
        id="capture_screen",
        name=tr("entries.capture.name", default="截图并 OCR"),
        description=(
            "截图并 OCR 识别文字，让非视觉模型也能读屏。mode='target' 截已设置的目标窗口；"
            "mode='fullscreen' 截全屏。返回识别文本与图像信息。include_boxes=true 时附加文字块坐标。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "enum": ["target", "fullscreen"],
                    "default": "target",
                    "description": "'target' 截目标窗口（需已设 target）；'fullscreen' 截全屏",
                },
                "include_boxes": {
                    "type": "boolean",
                    "default": False,
                    "description": "是否返回每个文字块的坐标",
                },
            },
        },
        llm_result_fields=["text", "status", "boxes", "width", "height", "image_path", "message"],
    )
    async def capture_screen(self, mode: str = "target", include_boxes: bool = False, region: str = "", **_) -> Any:
        if not _is_windows():
            return Err(SdkError("仅支持 Windows / Linux(X11) 桌面环境"))
        mode = str(mode or "target").strip().lower()
        if mode not in ("target", "fullscreen"):
            return Err(SdkError("mode 必须是 'target' 或 'fullscreen'"))

        try:
            if mode == "target":
                if self._target is None:
                    return Err(SdkError("尚未设置目标窗口，请先调用 set_target（或改用 mode='fullscreen'）"))
                pid = int(self._target.get("pid") or 0)
                window = await asyncio.to_thread(capture.target_window_for_capture, pid)
                if window is None:
                    return Err(SdkError(f"找不到 pid={pid} 的可见窗口，目标可能已关闭"))
                image = await asyncio.to_thread(capture.capture_window, window)
                title = str(window.get("title") or "")
            else:
                image = await asyncio.to_thread(capture.capture_fullscreen)
                title = ""
        except Exception as exc:
            return Err(SdkError(f"截图失败：{exc}"))
        image, crop_origin, reg_err = self._apply_region_crop(image, region)
        if reg_err:
            return Err(SdkError(reg_err))

        width, height = image.size
        boxes: list[dict[str, Any]] = []
        if bool(include_boxes):
            text, boxes, ocr_status = await asyncio.to_thread(capture.ocr_image_with_boxes, image)
        else:
            text, ocr_status = await asyncio.to_thread(capture.ocr_image, image)

        image_path = ""
        if self._save_screenshots:
            try:
                import time

                filename = f"capture_{int(time.time())}.png"
                image_path = str(await asyncio.to_thread(capture.save_png, image, self.data_path("screenshots", filename)))
            except Exception as exc:
                self.logger.debug("capture screenshot save skipped: {}", exc)
                image_path = ""

        message = f"截图完成 {width}x{height}"
        if ocr_status == "ok":
            message += f"，识别到 {len(text)} 个字符"
        elif ocr_status == "empty":
            message += "，未识别到文字"
        else:
            message += f"，OCR 不可用或失败（status={ocr_status}）"

        self._diary_record(
            "capture",
            f"{mode} 截图 {width}x{height}，OCR {len(text)} 字符",
            ok=ocr_status == "ok",
        )

        return Ok({
            "mode": mode,
            "text": text,
            "status": ocr_status,
            "boxes": boxes,
            "width": width,
            "height": height,
            "title": title,
            "image_path": image_path,
            "image_base64": capture.encode_jpeg_base64(image) if ocr_status != "ok" or self._save_screenshots else "",
            "message": message,
        })

    @plugin_entry(
        id="find_text",
        name=tr("entries.findText.name", default="查找屏幕文字坐标"),
        description="在屏幕上查找指定文字并返回坐标，供点击定位用。",
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "要查找的文字"},
                "mode": {
                    "type": "string",
                    "enum": ["target", "fullscreen"],
                    "default": "target",
                    "description": "截取范围",
                },
                "max_results": {"type": "integer", "default": 5, "description": "最多返回几个匹配块"},
            },
            "required": ["query"],
        },
        llm_result_fields=["status", "matches", "mode", "message"],
    )
    async def find_text(self, query: str, mode: str = "target", max_results: int = 5, region: str = "", **_) -> Any:
        if not _is_windows():
            return Err(SdkError("仅支持 Windows / Linux(X11) 桌面环境"))
        query = str(query or "").strip()
        if not query:
            return Err(SdkError("query 不能为空"))
        mode = str(mode or "target").strip().lower()
        if mode not in ("target", "fullscreen"):
            return Err(SdkError("mode 必须是 'target' 或 'fullscreen'"))

        try:
            if mode == "target":
                if self._target is None:
                    return Err(SdkError("尚未设置目标窗口，请先调用 set_target（或改用 mode='fullscreen'）"))
                pid = int(self._target.get("pid") or 0)
                window = await asyncio.to_thread(capture.target_window_for_capture, pid)
                if window is None:
                    return Err(SdkError(f"找不到 pid={pid} 的可见窗口，目标可能已关闭"))
                image = await asyncio.to_thread(capture.capture_window, window)
            else:
                image = await asyncio.to_thread(capture.capture_fullscreen)
            image, crop_origin, reg_err = self._apply_region_crop(image, region)
            if reg_err:
                return Err(SdkError(reg_err))
        except Exception as exc:
            return Err(SdkError(f"截图失败：{exc}"))

        _, matches, status = await asyncio.to_thread(
            capture.ocr_image_with_boxes, image, max_boxes=int(max_results or 5), query=query,
        )
        # 瞬时空帧加固：偶发截到空白/渲染中画面时自动重试两次
        retry_empty = 0
        while status == "empty" and retry_empty < 2:
            retry_empty += 1
            await asyncio.sleep(0.6)
            try:
                if mode == "target":
                    window = await asyncio.to_thread(capture.target_window_for_capture, pid)
                    if window is None:
                        break
                    image = await asyncio.to_thread(capture.capture_window, window)
                else:
                    image = await asyncio.to_thread(capture.capture_fullscreen)
                image, crop_origin, reg_err = self._apply_region_crop(image, region)
                if reg_err:
                    break
                _, matches, status = await asyncio.to_thread(
                    capture.ocr_image_with_boxes, image, max_boxes=int(max_results or 5), query=query,
                )
            except Exception:
                break
        # 多尺度重试：小字号文本在原始分辨率下可能漏识/误识（如字母被吞），
        # 2x 放大后用同一后端再试一次；命中则把坐标除回原尺度。
        if status == "no_match":
            try:
                big = image.resize((int(image.width * 2), int(image.height * 2)))
            except Exception:
                big = None
            if big is not None:
                try:
                    _, matches2, status2 = await asyncio.to_thread(
                        capture.ocr_image_with_boxes, big,
                        max_boxes=int(max_results or 5), query=query,
                    )
                    if status2 == "ok" and matches2:
                        for b in matches2:
                            for _k in ("left", "right", "top", "bottom"):
                                b[_k] = int(round(float(b.get(_k, 0)) / 2))
                        matches, status = matches2, status2
                except Exception:
                    pass
        if crop_origin:
            for b in matches:
                b["left"] = int(b.get("left", 0)) + crop_origin["left"]
                b["right"] = int(b.get("right", 0)) + crop_origin["left"]
                b["top"] = int(b.get("top", 0)) + crop_origin["top"]
                b["bottom"] = int(b.get("bottom", 0)) + crop_origin["top"]
        if status == "no_match":
            self._diary_record("text", f"查找「{query}」未找到（{mode}）")
            return Ok({
                "status": "no_match",
                "matches": [],
                "query": query,
                "mode": mode,
                "message": f"屏幕上没有找到包含「{query}」的文字。可以先用 keyboard_capture 看看实际识别到了什么。",
            })
        if status != "ok":
            return Err(SdkError(f"OCR 不可用或失败（status={status}）"))

        self._diary_record(
            "text",
            f"查找「{query}」找到 {len(matches)} 处（{mode}）",
        )
        return Ok({
            "status": "ok",
            "matches": matches,
            "query": query,
            "mode": mode,
            "width": image.size[0],
            "height": image.size[1],
            "message": f"找到 {len(matches)} 处包含「{query}」的文字",
        })

    @ui.action(
        label=tr("actions.saveShot.label", default="Save screenshot"),
        icon="P",
        group="capture",
        order=20,
        refresh_context=False,
    )
    @plugin_entry(
        id="save_screenshot",
        name=tr("entries.saveShot.name", default="保存截图"),
        description="截取目标窗口（或全屏）并保存 PNG 到插件 data 目录，返回文件路径。供视觉模型或人工查看。",
        input_schema={
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "enum": ["target", "fullscreen"],
                    "default": "target",
                    "description": "'target' 截目标窗口（需已设 target）；'fullscreen' 截全屏",
                },
            },
        },
        llm_result_fields=["saved", "path", "width", "height", "message"],
    )
    async def save_screenshot(self, mode: str = "target", **_) -> Any:
        if not _is_windows():
            return Err(SdkError("仅支持 Windows / Linux(X11) 桌面环境"))
        mode = str(mode or "target").strip().lower()
        if mode not in ("target", "fullscreen"):
            return Err(SdkError("mode 必须是 'target' 或 'fullscreen'"))
        try:
            if mode == "target":
                if self._target is None:
                    return Err(SdkError("尚未设置目标窗口，请先调用 set_target（或改用 mode='fullscreen'）"))
                pid = int(self._target.get("pid") or 0)
                window = await asyncio.to_thread(capture.target_window_for_capture, pid)
                if window is None:
                    return Err(SdkError(f"找不到 pid={pid} 的可见窗口"))
                image = await asyncio.to_thread(capture.capture_window, window)
            else:
                image = await asyncio.to_thread(capture.capture_fullscreen)
        except Exception as exc:
            return Err(SdkError(f"截图失败：{exc}"))
        import time

        filename = f"shot_{int(time.time())}.png"
        path = str(await asyncio.to_thread(capture.save_png, image, self.data_path("screenshots", filename)))
        width, height = image.size
        return Ok({
            "saved": True,
            "path": path,
            "width": width,
            "height": height,
            "message": f"截图已保存到 {path}",
        })

    @plugin_entry(
        id="capture_status",
        name=tr("entries.captureStatus.name", default="截图/OCR 状态"),
        description="返回截图与 OCR 可用性（Windows 支持、OCR 是否安装、mss 是否可用）。",
    )
    async def capture_status(self, **_) -> Any:
        status = await asyncio.to_thread(capture.describe_capture)
        return Ok({**status, "save_screenshots": bool(self._save_screenshots)})

    # ── Shell 命令执行（供非视觉模型驱动自动化） ───────────────────

    @plugin_entry(
        id="run_command",
        name=tr("entries.runCommand.name", default="执行命令"),
        description="在电脑上执行一条 shell 命令并返回输出。用于自动化操作（查询/安装/管理文件）。确认模式开启时先入队等待用户确认。",
        input_schema={
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "要执行的 shell 命令",
                },
                "shell": {
                    "type": "string",
                    "enum": ["auto", "cmd", "powershell"],
                    "default": "auto",
                    "description": "使用的 shell（Windows 默认 cmd.exe）",
                },
                "cwd": {
                    "type": "string",
                    "description": "工作目录（留空=默认）",
                },
            },
            "required": ["command"],
        },
        llm_result_fields=["status", "token", "success", "returncode", "output", "timed_out"],
    )
    async def run_command(self, command: str, shell: str = "auto", cwd: str = "", **_) -> Any:
        command = str(command or "").strip()
        if not command:
            return Err(SdkError("命令不能为空"))
        shell = str(shell or "auto")
        cwd = str(cwd or "").strip()
        if cwd and not os.path.isabs(cwd):
            cwd = os.path.join(self._workspace_path(), cwd)
        if cwd and not os.path.isdir(cwd):
            return Err(SdkError(f"工作目录不存在：{cwd}"))

        if self._command_auto_prefixes:
            cmd_lower = command.lower().lstrip()
            for prefix in self._command_auto_prefixes:
                # 支持 "re:" 前缀的正则条目（完整匹配）；其余按普通前缀匹配
                hit = False
                if prefix.startswith("re:") and len(prefix) > 3:
                    try:
                        hit = re.fullmatch(prefix[3:], command.lower()) is not None
                    except re.error:
                        hit = False
                else:
                    hit = cmd_lower.startswith(prefix)
                if hit:
                    result = await self._execute_command(command, shell, cwd=cwd)
                    success = bool(result.get("success"))
                    output = str(result.get("output") or "")
                    self._diary_record(
                        "command",
                        f"白名单直执行（{shell}）：{command[:80]}",
                        ok=success,
                    )
                    return Ok({
                        "status": "done" if success else "failed",
                        "success": success,
                        "returncode": result.get("returncode"),
                        "output": output,
                        "timed_out": bool(result.get("timed_out")),
                        "auto_approved": True,
                        "message": f"命令命中白名单规则「{prefix}」，已自动执行（退出码 {result.get('returncode')}）",
                    })

        if self._command_require_confirmation:
            token = uuid.uuid4().hex[:12]
            pending = {
                "token": token,
                "command": command,
                "shell": shell,
                "created_at": time.time(),
                "status": "pending",
                "output": "",
                "returncode": None,
                "timed_out": False,
            }
            async with self._pending_lock:
                self._expire_pending_locked(now=time.time())
                if len(self._pending_commands) >= _PENDING_MAX:
                    return Err(SdkError(f"待确认命令队列已满（>{_PENDING_MAX} 条），请先在面板处理。"))
                self._pending_commands.append(pending)
            return Ok({
                "status": "awaiting_confirmation",
                "token": token,
                "command": command,
                "shell": shell,
                "message": f"命令已加入待确认队列（token={token}）。请告知用户在「按键控制」面板确认后执行。",
            })

        result = await self._execute_command(command, shell, cwd=cwd)
        self._diary_record(
            "command",
            f"执行命令（{shell}）：{command[:80]}",
            ok=bool(result.get("success")),
        )
        if not result.get("success") and result.get("timed_out"):
            return Err(SdkError(result.get("output", "命令执行超时")))
        return Ok(result)

    def _expire_pending_locked(self, *, now: float) -> None:
        keep: list[dict[str, Any]] = []
        for item in self._pending_commands:
            status = item.get("status")
            if status in ("done", "failed", "rejected"):
                continue
            if status == "running":
                keep.append(item)
                continue
            if now - float(item.get("created_at") or 0) > _PENDING_TTL_SECONDS:
                continue
            keep.append(item)
        self._pending_commands = keep

    async def _execute_command(self, command: str, shell: str, cwd: str = "") -> dict[str, Any]:
        return await asyncio.to_thread(
            command_exec.run_command,
            command,
            shell=shell,
            timeout=self._command_timeout,
            max_output_chars=self._command_max_output,
            cwd=str(cwd or "").strip(),
        )

    @plugin_entry(
        id="list_pending_commands",
        name=tr("entries.listPending.name", default="待确认命令"),
        description="列出等待用户确认执行的 shell 命令（含 token、命令与状态）。",
    )
    async def list_pending_commands(self, **_) -> Any:
        async with self._pending_lock:
            self._expire_pending_locked(now=time.time())
            items = [
                {
                    "token": p.get("token"),
                    "command": p.get("command"),
                    "shell": p.get("shell"),
                    "status": p.get("status"),
                    "created_at": p.get("created_at"),
                    "output": p.get("output", "") if p.get("status") == "done" else "",
                }
                for p in self._pending_commands
            ]
        return Ok({"items": items, "count": len(items), "require_confirmation": bool(self._command_require_confirmation)})

    @ui.action(
        label=tr("actions.setCommandConfirm.label", default="命令确认开关"),
        icon="K",
        group="command",
        order=5,
        refresh_context=True,
    )
    @plugin_entry(
        id="set_command_confirmation",
        name=tr("entries.setCommandConfirm.name", default="切换命令确认"),
        description="开启/关闭 shell 命令执行前的用户确认（写入 store，重启保持）。",
        input_schema={
            "type": "object",
            "properties": {
                "enabled": {
                    "type": "boolean",
                    "description": "true=开启确认，false=关闭确认",
                },
            },
            "required": ["enabled"],
        },
        llm_result_fields=["enabled", "message"],
    )
    async def set_command_confirmation(self, enabled: bool, **_) -> Any:
        value = bool(enabled)
        self._command_require_confirmation = value
        await self.store.set(_STORE_CONFIRM_KEY, value)
        return Ok({
            "enabled": value,
            "message": f"命令执行确认已{'开启' if value else '关闭'}",
        })

    @ui.action(
        label=tr("actions.setSafetySwitches.label", default="Safety switches"),
        icon="S",
        group="command",
        order=6,
        refresh_context=True,
    )
    @plugin_entry(
        id="set_safety_switches",
        name=tr("entries.setSafetySwitches.name", default="切换安全边界开关"),
        description=(
            "逐项开启/关闭安全边界：反作弊拦截、提权拦截、注入前焦点校验。"
            "仅传入需要修改的开关；全部写入 store，重启保持。仅供面板使用，不暴露给 LLM。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "block_anti_cheat": {
                    "type": "boolean",
                    "description": "true=拒绝反作弊进程目标，false=放行",
                },
                "block_elevated_target": {
                    "type": "boolean",
                    "description": "true=拒绝提权进程目标，false=放行",
                },
                "require_focus": {
                    "type": "boolean",
                    "description": "true=聚焦失败取消注入，false=尽力聚焦但不因此失败",
                },
            },
        },
    )
    async def set_safety_switches(
        self,
        block_anti_cheat: bool | None = None,
        block_elevated_target: bool | None = None,
        require_focus: bool | None = None,
        **_,
    ) -> Any:
        changed: list[str] = []
        if block_anti_cheat is not None:
            self._block_anti_cheat = bool(block_anti_cheat)
            await self.store.set(_STORE_SAFETY_ANTI_CHEAT_KEY, self._block_anti_cheat)
            changed.append(f"反作弊拦截={'开' if self._block_anti_cheat else '关'}")
        if block_elevated_target is not None:
            self._block_elevated = bool(block_elevated_target)
            await self.store.set(_STORE_SAFETY_ELEVATED_KEY, self._block_elevated)
            changed.append(f"提权拦截={'开' if self._block_elevated else '关'}")
        if require_focus is not None:
            self._require_focus = bool(require_focus)
            await self.store.set(_STORE_SAFETY_FOCUS_KEY, self._require_focus)
            changed.append(f"焦点校验={'开' if self._require_focus else '关'}")
        if not changed:
            return Err(SdkError("请至少提供一个要切换的安全开关"))
        message = f"安全边界已更新：{'，'.join(changed)}"
        self.logger.info("safety switches updated: {}", message)
        return Ok({
            "block_anti_cheat": bool(self._block_anti_cheat),
            "block_elevated_target": bool(self._block_elevated),
            "require_focus": bool(self._require_focus),
            "message": message,
        })

    @ui.action(
        label=tr("actions.setInputSpeed.label", default="Input speed"),
        icon="V",
        group="command",
        order=7,
        refresh_context=True,
    )
    @plugin_entry(
        id="set_input_speed",
        name=tr("entries.setInputSpeed.name", default="切换输入速度"),
        description="切换全局键鼠输入节奏档位（slow/normal/fast/turbo）。",
        input_schema={
            "type": "object",
            "properties": {"profile": {"type": "string", "enum": ["slow", "normal", "fast", "turbo"]}},
            "required": ["profile"],
        },
        llm_result_fields=["profile", "input_delay", "type_delay", "message"],
    )
    async def set_input_speed(self, profile: str, **_) -> Any:
        profile_norm = str(profile or "").strip().lower()
        if profile_norm not in _INPUT_SPEED_PROFILES:
            return Err(SdkError("profile 必须是 slow/normal/fast/turbo"))
        self._input_speed_profile = profile_norm
        input_delay, type_delay = _INPUT_SPEED_PROFILES[profile_norm]
        self._input_delay, self._type_delay = input_delay, type_delay
        await self.store.set(_STORE_INPUT_SPEED_KEY, profile_norm)
        zh_name = {"slow": "慢速", "normal": "标准", "fast": "快速", "turbo": "极速"}[profile_norm]
        message = f"输入速度已切换为{zh_name}（按键间隔 {input_delay}s，打字间隔 {type_delay}s），立即生效"
        self.logger.info("input speed -> {} ({}, {})", profile_norm, input_delay, type_delay)
        return Ok({
            "profile": profile_norm,
            "input_delay": input_delay,
            "type_delay": type_delay,
            "message": message,
        })

    @plugin_entry(
        id="set_command_whitelist",
        name=tr("entries.setCommandWhitelist.name", default="命令白名单"),
        description="设置免确认命令前缀白名单（逗号分隔）。",
        input_schema={
            "type": "object",
            "properties": {"prefixes": {"type": "string"}},
            "required": ["prefixes"],
        },
        llm_result_fields=["prefixes", "count", "message"],
    )
    async def set_command_whitelist(self, prefixes: str, **_) -> Any:
        raw = str(prefixes or "").replace("，", ",")
        parsed = [p.strip().lower() for p in raw.split(",") if p.strip()]
        self._command_auto_prefixes = parsed
        await self.store.set(_STORE_CMD_WHITELIST_KEY, parsed)
        shown = ", ".join(parsed) if parsed else "（空）"
        self._diary_record("note", f"命令白名单更新：{shown[:120]}")
        return Ok({
            "prefixes": parsed,
            "count": len(parsed),
            "message": f"命令白名单已更新（{len(parsed)} 项）：{shown}。匹配前缀的命令将跳过确认直接执行。",
        })

    @ui.action(
        label=tr("actions.confirmCommand.label", default="确认执行"),
        icon="Y",
        group="command",
        order=10,
        refresh_context=True,
    )
    @plugin_entry(
        id="confirm_command",
        name=tr("entries.confirmCommand.name", default="确认执行命令"),
        description="确认并执行一条待确认的 shell 命令（按 token 匹配）。确认后命令才会真正执行。",
        input_schema={
            "type": "object",
            "properties": {
                "token": {"type": "string", "description": "待确认命令的 token"},
            },
            "required": ["token"],
        },
        llm_result_fields=["status", "token", "success", "returncode", "output"],
    )
    async def confirm_command(self, token: str, **_) -> Any:
        token = str(token or "").strip()
        async with self._pending_lock:
            self._expire_pending_locked(now=time.time())
            target = next((p for p in self._pending_commands if p.get("token") == token and p.get("status") == "pending"), None)
            if target is None:
                return Err(SdkError(f"没有找到待确认的命令（token={token}），可能已确认、已拒绝或已过期。"))
            if target.get("status") == "running":
                return Err(SdkError(f"命令（token={token}）正在执行中，请稍候。"))
            target["status"] = "running"
            command = str(target.get("command") or "")
            shell = str(target.get("shell") or "auto")
            output = str(target.get("output") or "")
            returncode = target.get("returncode")
            timed_out = bool(target.get("timed_out"))

        result = await self._execute_command(command, shell)
        success = bool(result.get("success"))
        returncode = result.get("returncode")
        output = str(result.get("output") or "")
        timed_out = bool(result.get("timed_out"))
        self._diary_record(
            "command",
            f"执行命令（{shell}）：{command[:80]}",
            ok=success,
        )
        async with self._pending_lock:
            for p in self._pending_commands:
                if p.get("token") == token:
                    p["status"] = "done" if success else "failed"
                    p["returncode"] = returncode
                    p["output"] = output
                    p["timed_out"] = timed_out
                    break

        self._push_command_result(token, command, success, output)
        if timed_out:
            return Err(SdkError(output or "命令执行超时"))
        return Ok({
            "status": "done" if success else "failed",
            "token": token,
            "success": success,
            "returncode": returncode,
            "output": output,
            "message": f"命令已执行（token={token}），退出码 {returncode}",
        })

    @ui.action(
        label=tr("actions.rejectCommand.label", default="拒绝"),
        icon="N",
        group="command",
        order=20,
        refresh_context=True,
    )
    @plugin_entry(
        id="reject_command",
        name=tr("entries.rejectCommand.name", default="拒绝命令"),
        description="拒绝一条待确认的 shell 命令（按 token 匹配），不执行。",
        input_schema={
            "type": "object",
            "properties": {
                "token": {"type": "string", "description": "待确认命令的 token"},
            },
            "required": ["token"],
        },
    )
    async def reject_command(self, token: str, **_) -> Any:
        token = str(token or "").strip()
        async with self._pending_lock:
            self._expire_pending_locked(now=time.time())
            target = next((p for p in self._pending_commands if p.get("token") == token), None)
            if target is None:
                return Err(SdkError(f"没有找到待确认的命令（token={token}）。"))
            if target.get("status") == "running":
                return Err(SdkError(f"命令（token={token}）正在执行中，无法拒绝。"))
            target["status"] = "rejected"
        self.logger.info("command rejected: token={}", token)
        return Ok({"status": "rejected", "token": token, "message": f"已拒绝命令（token={token}）。"})

    def _push_command_result(self, token: str, command: str, success: bool, output: str) -> None:
        try:
            self.push_message(
                visibility=["chat"],
                ai_behavior="respond",
                parts=[
                    {
                        "type": "text",
                        "text": (
                            f"命令执行结果（token={token}）:\n> {command}\n\n"
                            f"{'[成功]' if success else '[失败]'}\n"
                            f"```\n{output}\n```"
                        ),
                    }
                ],
            )
        except Exception as exc:
            self.logger.debug("push command result failed: {}", exc)

    # ── 工作区文件读写（供非视觉模型 vibe-coding） ─────────────────

    def _workspace_path(self) -> str:
        root = str(getattr(self, "_workspace_root", "") or "").strip()
        if not root:
            root = os.path.expandvars(r"%USERPROFILE%\Documents")
        return os.path.abspath(os.path.expanduser(root))
    @plugin_entry(
        id="list_files",
        name=tr("entries.listFiles.name", default="列出目录"),
        description="列出工作区内目录的文件与子目录。",
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "要列出的目录路径（相对工作区，可省略）"},
            },
        },
        llm_result_fields=["ok", "path", "total", "entries", "error"],
    )
    async def list_files(self, path: str = "", **_) -> Any:
        result = await asyncio.to_thread(file_ops.list_dir, self._workspace_path(), path or ".")
        return Ok(result) if result.get("ok") else Err(SdkError(result.get("error", "列出失败")))

    @plugin_entry(
        id="read_file",
        name=tr("entries.readFile.name", default="读取文件"),
        description="读取工作区内文本文件（支持分段）。",
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "文件路径"},
                "start_line": {"type": "integer", "description": "起始行（默认 1）"},
                "line_count": {"type": "integer", "description": "读取行数（0=到末尾）"},
            },
            "required": ["path"],
        },
        llm_result_fields=["ok", "size", "content", "truncated_lines", "line_info", "error"],
    )
    async def read_file(self, path: str, start_line: int = 1, line_count: int = 0, **_) -> Any:
        result = await asyncio.to_thread(
            file_ops.read_file,
            self._workspace_path(),
            path,
            start_line=int(start_line or 1),
            line_count=int(line_count or 0),
        )
        return Ok(result) if result.get("ok") else Err(SdkError(result.get("error", "读取失败")))

    @plugin_entry(
        id="write_file",
        name=tr("entries.writeFile.name", default="写入文件"),
        description="在工作区内写入/追加文本到文件。",
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "文件路径"},
                "content": {"type": "string", "description": "内容"},
                "append": {"type": "boolean", "description": "是否追加"},
            },
            "required": ["path", "content"],
        },
        llm_result_fields=["ok", "path", "created", "bytes_written", "message", "error"],
    )
    async def write_file(self, path: str, content: str = "", append: bool = False, **_) -> Any:
        result = await asyncio.to_thread(
            file_ops.write_file,
            self._workspace_path(),
            path,
            content or "",
            append=bool(append),
        )
        return Ok(result) if result.get("ok") else Err(SdkError(result.get("error", "写入失败")))

    # ── 主机音频分析（供非视觉模型"听"电脑声音） ──────────────────

    @ui.action(
        label=tr("actions.analyzeAudio.label", default="Analyze audio"),
        icon="A",
        group="audio",
        order=10,
        refresh_context=False,
    )
    @plugin_entry(
        id="analyze_audio",
        name=tr("entries.analyzeAudio.name", default="分析主机音频"),
        description="监听电脑当前播放的声音并返回频谱特征与解读。",
        input_schema={
            "type": "object",
            "properties": {
                "duration": {
                    "type": "number",
                    "description": "监听时长（秒），默认 4，上限 15",
                },
            },
        },
        llm_result_fields=[
            "available", "silence", "volume_db", "centroid_hz", "dominant_hz",
            "low_pct", "mid_pct", "high_pct", "interpretation", "error",
        ],
    )
    async def analyze_audio(self, duration: float = 0, **_) -> Any:
        seconds = float(duration or self._audio_capture_seconds)
        result = await asyncio.to_thread(audio_analysis.capture_and_analyze, seconds)
        if not result.get("available"):
            return Err(SdkError(result.get("error", "音频分析不可用")))
        self._diary_record(
            "audio",
            "分析主机音频：" + str(result.get("interpretation") or result.get("silence", "有声")),
        )
        return Ok(result)

    @plugin_entry(
        id="audio_status",
        name=tr("entries.audioStatus.name", default="音频分析状态"),
        description="返回主机音频分析可用性（Windows 支持、numpy 是否可用）。",
    )
    async def audio_status(self, **_) -> Any:
        status = await asyncio.to_thread(audio_analysis.describe_audio)
        return Ok({
            **status,
            "capture_seconds_default": float(self._audio_capture_seconds),
            "capture_seconds_max": float(audio_analysis._CAPTURE_SECONDS_MAX),
        })

    # ── 日记 ─────────────────────────────────────────────────────────

    @timer_interval(id="diary_auto_flush", seconds=3600, auto_start=True)
    async def diary_auto_flush(self, **_):
        await self._diary_flush_if_due()
        return Ok({"flushed": True})

    # ── 反应式窗口监视 ────────────────────────────────────────────────

    async def _reactive_watch_tick(self) -> None:
        """每 10s 检查监视名单窗口的出现/消失，变化即推送给猫娘（read 模式入上下文）。"""
        titles = list(getattr(self, "_watch_titles", []) or [])
        if not titles or not _is_windows():
            return
        try:
            windows = await asyncio.to_thread(win32.enumerate_windows)
        except Exception:
            return
        prev: dict[str, bool] = dict(getattr(self, "_watch_prev", {}) or {})
        current: dict[str, bool] = {}
        for needle in titles:
            exists = False
            for win in windows:
                title_l = str(win.get("title") or "").lower()
                proc_l = str(win.get("process_name") or "").lower()
                if needle in title_l or needle in proc_l:
                    exists = True
                    break
            current[needle] = exists
            if prev.get(needle, None) is not None and prev[needle] != exists:
                verb = "已打开" if exists else "已关闭"
                try:
                    self.push_message(
                        parts=[{"type": "text", "text": f"[按键控制] 监视的窗口「{needle}」{verb}。"}],
                        visibility=["hud"],
                        ai_behavior="read",
                        source="keyboard_controller",
                        metadata={"plugin_id": "keyboard_controller", "kind": "window_watch"},
                    )
                    self._diary_record("watch", f"监视窗口「{needle}」{verb}")
                except Exception as exc:
                    self.logger.debug("reactive watch push failed: {}", exc)
        self._watch_prev = current

    @timer_interval(id="reactive_window_watch", seconds=10, auto_start=True)
    async def reactive_window_watch(self, **_):
        try:
            await self._reactive_watch_tick()
        except Exception as exc:
            self.logger.debug("reactive watch tick failed: {}", exc)
        return Ok({"ok": True})

    @timer_interval(id="trigger_engine_tick", seconds=5, auto_start=True)
    async def trigger_engine_tick(self, **_):
        """触发器引擎轮询：每 5 秒检查一次条件。"""
        engine = self._trigger_engine
        if engine.count == 0:
            return Ok({"ok": True, "triggers": 0})
        try:
            # 每轮先异步抓一帧（避免在线程/事件循环里同步阻塞截图），
            # 触发器条件统一用这帧判断。
            frame = await self._grab_frame()
            fired = await engine.tick(frame)
            if fired:
                # 记录具体动作而非只有数量：触发器是无人值守执行链，
                # 日志必须能回答"它到底自动干了什么"。
                summary = "; ".join(
                    f"{f.get('trigger_id')}:{f.get('action')}" for f in fired
                )
                self._diary_record("trigger", f"触发器触发 {len(fired)} 个：{summary}")
                self.logger.info("trigger engine: {} fired ({})", len(fired), summary)
            return Ok({"ok": True, "triggers": engine.count, "fired": len(fired)})
        except Exception as exc:
            self.logger.debug("trigger engine tick failed: {}", exc)
        return Ok({"ok": True, "triggers": engine.count})

    # auto_start 必须为 True：宿主只在启动时拉起 auto_start 的 timer，SDK
    # 没有事后启动 timer 的 API —— 之前用 False 导致录制期间一帧都不采，
    # GIF 永远只有 stop 时补拍的那 1 帧。tick 内有 not is_recording 早退
    # 守卫，空转开销可忽略。
    @timer_interval(id="gif_capture_tick", seconds=1, auto_start=True)
    async def gif_capture_tick(self, **_):
        """GIF 录屏帧捕获（录制时自动激活）。"""
        if not self._gif_recorder or not self._gif_recorder.is_recording:
            return Ok({"ok": True})
        try:
            frame = await self._grab_frame()
            if frame:
                self._gif_recorder.add(frame)
        except Exception as exc:
            self.logger.debug("gif capture tick failed: {}", exc)
        return Ok({"ok": True})

    @plugin_entry(
        id="set_window_watch",
        name=tr("entries.setWindowWatch.name", default="设置窗口监视"),
        description="设置窗口出现/消失的反应式监视名单。",
        input_schema={
            "type": "object",
            "properties": {"titles": {"type": "string"}},
            "required": ["titles"],
        },
        llm_result_fields=["titles", "count", "message"],
    )
    async def set_window_watch(self, titles: str, **_) -> Any:
        raw = str(titles or "").replace("，", ",")
        parsed = [p.strip().lower() for p in raw.split(",") if p.strip()]
        self._watch_titles = parsed
        self._watch_prev = {}
        await self.store.set(_STORE_WATCH_TITLES_KEY, parsed)
        shown = ", ".join(parsed) if parsed else "（空）"
        return Ok({
            "titles": parsed,
            "count": len(parsed),
            "message": f"窗口监视已更新（{len(parsed)} 项）：{shown}。窗口出现/关闭时会自动通知。",
        })


    @ui.action(
        label=tr("actions.diaryStatus.label", default="Diary status"),
        icon="D",
        group="diary",
        order=10,
        refresh_context=False,
    )
    @plugin_entry(
        id="diary_status",
        name=tr("entries.diaryStatus.name", default="日记状态"),
        description="查看日记功能状态与今天的记录统计。",
    )
    async def diary_status(self, **_) -> Any:
        if self._diary is None:
            return Ok({"enabled": False, "message": "日记未初始化"})
        day = datetime.now().strftime("%Y-%m-%d")
        counts = self._diary.counts(day)
        return Ok({
            "enabled": self._diary.enabled(),
            "date": day,
            "dir": str(self._diary_dir_path()),
            "event_count": sum(counts.values()),
            "counts": counts,
            "summary": diary.summarize_counts(counts, locale=self._diary._locale),
            "flushed": bool((self._diary_dir_path() / f"{day}.md").is_file()),
            "auto_flush_seconds": self._diary_flush_seconds,
            "max_events_per_day": self._diary._max_events_per_day,
        })

    @ui.action(
        label=tr("actions.diaryWrite.label", default="Write diary now"),
        icon="D",
        group="diary",
        order=20,
        refresh_context=True,
    )
    @plugin_entry(
        id="diary_write_now",
        name=tr("entries.diaryWrite.name", default="立即写日记"),
        description="把今天记录的操作整理成 Markdown 日记写入 memories/。",
    )
    async def diary_write_now(self, **_) -> Any:
        day = datetime.now().strftime("%Y-%m-%d")
        path = await self._diary_flush_if_due(force=True)
        if path is None:
            return Ok({"date": day, "written": False, "message": "今天还没有可写入的日记事件"})
        return Ok({
            "date": day,
            "written": True,
            "file": str(path),
            "message": f"日记已写入 {path}",
        })

    @plugin_entry(
        id="diary_read",
        name=tr("entries.diaryRead.name", default="读取日记"),
        description="读取某一天的日记 Markdown 文本；date 留空读今天。",
        input_schema={
            "type": "object",
            "properties": {
                "date": {
                    "type": "string",
                    "description": "日期 YYYY-MM-DD，留空读今天",
                },
            },
        },
        llm_result_fields=["date", "event_count", "markdown", "message"],
    )
    async def diary_read(self, date: str = "", **_) -> Any:
        if self._diary is None:
            return Err(SdkError("日记未初始化"))
        day = str(date or "").strip() or datetime.now().strftime("%Y-%m-%d")
        data = self._diary.read_day(self._diary_dir_path(), day)
        if not data["markdown"]:
            return Ok({
                "date": day,
                "event_count": 0,
                "markdown": "",
                "message": f"{day} 没有日记记录",
            })
        return Ok({
            "date": day,
            "event_count": data["event_count"],
            "markdown": data["markdown"],
            "message": f"{day} 的日记（{data['event_count']} 条事件）",
        })

    @plugin_entry(
        id="diary_note",
        name=tr("entries.diaryNote.name", default="日记随笔"),
        description="往今天的日记追加一条随笔记录。",
        input_schema={
            "type": "object",
            "properties": {
                "detail": {"type": "string", "description": "随笔正文"},
            },
            "required": ["detail"],
        },
        llm_result_fields=["added", "detail", "message"],
    )
    async def diary_note(self, detail: str = "", **_) -> Any:
        text = str(detail or "").strip()
        if not text:
            return Err(SdkError("随笔内容为空"))
        self._diary_record("note", text)
        return Ok({
            "added": True,
            "detail": text,
            "message": "已记入今天的日记",
        })

    @plugin_entry(
        id="set_diary_enabled",
        name=tr("entries.setDiaryEnabled.name", default="开关日记"),
        description="开启/关闭自动写日记（写入 store，重启保持）。",
        input_schema={
            "type": "object",
            "properties": {
                "enabled": {"type": "boolean", "description": "true=开启，false=关闭"},
            },
            "required": ["enabled"],
        },
        llm_result_fields=["enabled", "message"],
    )
    async def set_diary_enabled(self, enabled: bool, **_) -> Any:
        value = bool(enabled)
        self._diary_enabled = value
        if self._diary is not None:
            self._diary.set_enabled(value)
        await self.store.set(_STORE_DIARY_KEY, value)
        return Ok({
            "enabled": value,
            "message": f"自动写日记已{'开启' if value else '关闭'}",
        })

    # ── v0.5.0 新增派发方法 ────────────────────────────────────────────

    async def _grab_frame(self) -> Any:
        """截取当前目标窗口或全屏，返回 PIL Image（或 None）。

        优先截目标窗口（已 set_target），否则截全屏。供屏幕高级感知复用。
        """
        if not _is_windows():
            return None
        try:
            if self._target is not None:
                pid = int(self._target.get("pid") or 0)
                if pid == _FULLSCREEN_PID:
                    return await asyncio.to_thread(capture.capture_fullscreen)
                window = await asyncio.to_thread(capture.target_window_for_capture, pid)
                if window is not None:
                    return await asyncio.to_thread(capture.capture_window, window)
            return await asyncio.to_thread(capture.capture_fullscreen)
        except Exception as exc:
            self.logger.debug("grab frame failed: {}", exc)
            return None

    async def _screen_color(self, x: int | None, y: int | None,
                            target: list | None, tolerance: int,
                            max_results: int, region: str, **_) -> Any:
        """屏幕像素颜色检测。"""
        frame = await self._grab_frame()
        if frame is None:
            return Err(SdkError("截图失败"))
        if target:
            # 搜索色块
            target_tuple = tuple(int(c) for c in target[:3]) if len(target) >= 3 else (0, 0, 0)
            parsed_region = None
            if region:
                # LLM 传来的 region 可能不是纯数字（"100,200,50,50px" 等），
                # 解析失败返回明确错误而不是裸 ValueError 500。
                parts = [p.strip() for p in str(region).split(",") if p.strip()]
                try:
                    nums = [int(p) for p in parts]
                except ValueError:
                    return Err(SdkError(f"region 格式无效：{region}（应为 'x,y,w,h'）"))
                if len(nums) == 4:
                    parsed_region = (nums[0], nums[1], nums[2], nums[3])
            result = await asyncio.to_thread(
                screen_advanced.find_color, frame, target_tuple, tolerance, parsed_region, max_results
            )
            return Ok(result)
        elif x is not None and y is not None:
            result = await asyncio.to_thread(screen_advanced.get_pixel, frame, x, y)
            return Ok(result)
        else:
            return Err(SdkError("op=color 需要 (x,y) 或 target"))

    async def _screen_diff(self, threshold: int, min_area: int, **_) -> Any:
        """帧差异检测。"""
        frame = await self._grab_frame()
        if frame is None:
            return Err(SdkError("截图失败"))
        prev = self._prev_frame
        self._prev_frame = frame
        if prev is None:
            return Ok({"ok": True, "status": "first_frame", "message": "已记录第一帧，请再调用一次比较"})
        result = await asyncio.to_thread(screen_advanced.frame_diff, prev, frame, threshold, min_area)
        return Ok(result)

    async def _screen_multi_match(self, template_path: str, scales: list | None,
                                  min_score: float, **_) -> Any:
        """多尺度模板匹配。"""
        if not template_path:
            return Err(SdkError("需要 template_path"))
        frame = await self._grab_frame()
        if frame is None:
            return Err(SdkError("截图失败"))
        try:
            tmpl = Image.open(template_path)
        except Exception as e:
            return Err(SdkError(f"无法打开模板图：{e}"))
        result = await asyncio.to_thread(
            screen_advanced.multi_scale_match, frame, tmpl, scales, min_score
        )
        return Ok(result)

    async def _screen_gif(self, action: str, fps: float, save_path: str, **_) -> Any:
        """GIF 录制控制。"""
        action = str(action or "").strip().lower()
        if action == "start":
            if self._gif_recorder and self._gif_recorder.is_recording:
                return Err(SdkError("已在录制中，请先 stop（重复 start 会静默丢弃上一段录制）"))
            fps = min(max(float(fps or 2.0), 0.5), 20.0)
            self._gif_recorder = screen_advanced._GifRecorder()
            self._gif_recorder.start(fps=fps)
            return Ok({"ok": True, "status": "recording", "fps": fps})
        if action == "stop":
            if not self._gif_recorder or not self._gif_recorder.is_recording:
                return Err(SdkError("未在录制中"))
            frame = await self._grab_frame()
            if frame:
                self._gif_recorder.add(frame)
            if save_path:
                # 用户指定路径必须落在工作区内（与 write_file 同一沙箱约定，
                # 防止任意路径写入）；默认路径是插件自身数据目录，不受限。
                resolved = file_ops.resolve_path(self._workspace_path(), save_path)
                if resolved is None:
                    return Err(SdkError("save_path 无效或超出工作区"))
                save_path = resolved
            else:
                save_path = str(self.data_path("recording_{}.gif".format(int(time.time()))))
            result = await asyncio.to_thread(self._gif_recorder.save, save_path)
            self._gif_recorder.stop()
            return Ok(result)
        if action == "status":
            if self._gif_recorder and self._gif_recorder.is_recording:
                return Ok({
                    "ok": True, "recording": True,
                    "frames": self._gif_recorder.frame_count,
                    "elapsed_s": round(self._gif_recorder.elapsed, 1),
                    "fps": self._gif_recorder.fps,
                })
            return Ok({"ok": True, "recording": False, "frames": 0})
        return Err(SdkError(f"未知 gif_action：{action}（可选 start/stop/status）"))

    async def _gamepad_dispatch(self, op: str, button: str, hold_seconds: float,
                                left_x: float, left_y: float, right_x: float, right_y: float,
                                left: float, right: float, **_) -> Any:
        """手柄模拟派发（线程池执行，press_button 含 time.sleep）。"""
        op = str(op or "").strip().lower()
        if op == "button":
            # 与其他注入入口同一纪律：过安全开关（反作弊/提权拦截）+ 焦点
            # 校验。此前 button 是唯一绕过 _require_operable_window 的注入
            # 路径，按键会打进当前前台任意窗口。
            hwnd, window = await asyncio.to_thread(self._require_operable_window)
            await asyncio.to_thread(self._focus_or_raise, hwnd)
            result = await asyncio.to_thread(gamepad.press_button, button, hold_seconds)
            self._diary_record("input", f"手柄按钮 {button} → {window.get('title')}")
            return Ok(result)
        if op == "stick":
            return Ok(gamepad.set_thumbstick(left_x, left_y, right_x, right_y))
        if op == "trigger":
            return Ok(gamepad.set_triggers(left, right))
        if op == "list":
            return Ok(gamepad.list_buttons())
        if op == "status":
            return Ok(gamepad.can_emulate())
        return Err(SdkError(f"未知 op：{op}（可选 button/stick/trigger/list/status）"))

    async def _process_dispatch(self, op: str, query: str, pid: int | None,
                                force: bool, timeout: float, max_results: int, **_) -> Any:
        """进程管理派发（线程池执行，subprocess 会阻塞）。"""
        op = str(op or "").strip().lower()
        if op == "list":
            return Ok(await asyncio.to_thread(process_mgr.list_processes, query or "", max_results))
        if op == "info":
            if pid is None:
                return Err(SdkError("op=info 需要 pid"))
            return Ok(await asyncio.to_thread(process_mgr.process_info, pid))
        if op == "kill":
            if pid is None:
                return Err(SdkError("op=kill 需要 pid"))
            result = await asyncio.to_thread(process_mgr.kill_process, pid, force)
            # 破坏性操作必须留痕（其他所有操作都写日记，此前 kill 是例外）。
            self._diary_record(
                "process",
                f"终止进程 pid={pid} force={force}",
                ok=bool(result.get("ok")),
            )
            return Ok(result)
        if op == "wait":
            if pid is None:
                return Err(SdkError("op=wait 需要 pid"))
            return Ok(await asyncio.to_thread(process_mgr.wait_for_process, pid, timeout))
        return Err(SdkError(f"未知 op：{op}（可选 list/info/kill/wait）"))

    async def _network_dispatch(self, op: str, url: str, method: str,
                                headers: dict | None, body: str,
                                json_body: Any, path: str,
                                host: str, count: int, timeout: float, **_) -> Any:
        """网络请求派发（线程池执行，urlopen 会阻塞）。"""
        op = str(op or "").strip().lower()
        if op == "http":
            if not url:
                return Err(SdkError("op=http 需要 url"))
            return Ok(await asyncio.to_thread(
                network.http_request, url, method, headers, body, json_body, timeout))
        if op == "download":
            if not url or not path:
                return Err(SdkError("op=download 需要 url 和 path"))
            # 与 write_file 同一沙箱约定：路径必须落在工作区内。此前 path
            # 原样透传，绝对路径/..\穿越可覆盖工作区外任意文件。
            resolved = file_ops.resolve_path(self._workspace_path(), path)
            if resolved is None:
                return Err(SdkError("path 无效或超出工作区"))
            return Ok(await asyncio.to_thread(network.download_file, url, resolved, timeout))
        if op == "ping":
            if not host:
                return Err(SdkError("op=ping 需要 host"))
            return Ok(await asyncio.to_thread(network.ping_host, host, count, timeout))
        return Err(SdkError(f"未知 op：{op}（可选 http/download/ping）"))

    async def _show_notification(self, title: str, body: str, icon: str, **_) -> Any:
        """系统通知（线程池执行，避免 MessageBoxW/subprocess 阻塞事件循环）。"""
        return Ok(await asyncio.to_thread(notify_mod.notify, title, body, icon))

    async def _trigger_dispatch(self, action: str, trigger_id: str,
                                condition: str, cond_params: dict | None,
                                act: str, act_params: dict | None,
                                cooldown_s: float, max_fires: int, **_) -> Any:
        """触发器管理派发。"""
        action = str(action or "").strip().lower()
        engine = self._trigger_engine
        if action == "add":
            if not condition or not act:
                return Err(SdkError("action=add 需要 condition 和 act"))
            condition = str(condition).strip().lower()
            # 能力校验：text（需 OCR 注入）和 window（需标题轮询）从未实现，
            # 此前注册成功但永不触发、无任何提示。注册时就拒绝。
            if condition in ("text", "window"):
                return Err(SdkError(
                    f"条件 {condition} 尚未实现（text 需 OCR、window 需标题轮询），"
                    "可选 color/change"
                ))
            tid = trigger_id or f"trigger_{int(time.time())}"
            t = Trigger(
                trigger_id=tid,
                condition=condition,
                params=cond_params or {},
                action=act,
                action_params=act_params or {},
                cooldown_s=cooldown_s,
                max_fires=max_fires,
            )
            return Ok(engine.add(t))
        if action == "remove":
            if not trigger_id:
                return Err(SdkError("action=remove 需要 trigger_id"))
            return Ok(engine.remove(trigger_id))
        if action == "list":
            return Ok({"ok": True, "triggers": engine.list_all(), "count": engine.count})
        if action == "enable":
            if not trigger_id:
                return Err(SdkError("action=enable 需要 trigger_id"))
            return Ok(engine.enable(trigger_id, True))
        if action == "disable":
            if not trigger_id:
                return Err(SdkError("action=disable 需要 trigger_id"))
            return Ok(engine.enable(trigger_id, False))
        if action == "clear":
            engine.clear()
            return Ok({"ok": True, "message": "已清空所有触发器"})
        return Err(SdkError(f"未知 action：{action}（可选 add/remove/list/enable/disable/clear）"))

    async def _macro_dispatch(self, action: str, name: str,
                              step_type: str, step_params: dict | None,
                              delay: float, path: str,
                              macro_json: dict | None,
                              template_name: str, template_params: dict | None,
                              repeat: int, repeat_delay: float, **_) -> Any:
        """宏录制/回放派发。"""
        action = str(action or "").strip().lower()
        recorder = self._macro_recorder
        if action == "record_start":
            return Ok(recorder.start(name=name))
        if action == "record_step":
            if not step_type:
                return Err(SdkError("action=record_step 需要 step_type"))
            return Ok(recorder.record_step(step_type, step_params or {}, delay))
        if action == "record_stop":
            return Ok(recorder.stop())
        if action == "record_cancel":
            recorder.cancel()
            return Ok({"ok": True, "message": "录制已取消"})
        if action == "play":
            macro = None
            if path:
                # 宏文件是可执行指令的载体（可含 launch_app/run_command 步骤），
                # 任意路径读取 = 让 LLM 把工作区外的 JSON 当宏回放。约束到工作区。
                resolved = file_ops.resolve_path(self._workspace_path(), path)
                if resolved is None:
                    return Err(SdkError("宏文件路径无效或超出工作区"))
                try:
                    macro = Macro.load(resolved)
                except Exception as e:
                    return Err(SdkError(f"无法加载宏文件：{e}"))
            elif macro_json:
                try:
                    macro = Macro.from_dict(macro_json)
                except Exception as e:
                    return Err(SdkError(f"macro_json 无效：{e}"))
            else:
                return Err(SdkError("action=play 需要 path 或 macro_json"))
            if repeat > 1:
                macro.repeat = repeat
            if repeat_delay != 0.5:
                macro.repeat_delay = repeat_delay
            # 创建执行器
            async def _executor(step_type_inner, params_inner):
                return await self._macro_execute_step(step_type_inner, params_inner)
            result = await play_macro(macro, _executor)
            return Ok(result)
        if action == "list":
            templates = {
                "launch_game": "启动游戏并等待窗口：command + window_query + wait_s",
                "click_sequence": "顺序点击坐标：clicks [{x,y},...] + delay",
                "press_sequence": "顺序按键：keys [key1,key2,...] + delay",
                "loop": "循环已有宏：inner + times + interval",
            }
            return Ok({"ok": True, "templates": templates})
        if action == "template":
            if not template_name:
                return Err(SdkError("action=template 需要 template_name"))
            tp = template_params or {}
            if template_name == "launch_game":
                macro = macro_template_launch_game(
                    tp.get("command", ""), tp.get("window_query", ""), tp.get("wait_s", 5.0)
                )
            elif template_name == "click_sequence":
                clicks = [(c["x"], c["y"]) for c in tp.get("clicks", [])]
                macro = macro_template_click_sequence(clicks, tp.get("delay", 0.3))
            elif template_name == "press_sequence":
                macro = macro_template_press_sequence(tp.get("keys", []), tp.get("delay", 0.1))
            elif template_name == "loop":
                inner = Macro.from_dict(tp.get("inner", {}))
                macro = macro_template_loop(inner, tp.get("times", 10), tp.get("interval", 0.5))
            else:
                return Err(SdkError(f"未知模板：{template_name}（可选 launch_game/click_sequence/press_sequence/loop）"))
            return Ok(macro.to_dict())
        return Err(SdkError(f"未知 action：{action}（可选 record_start/record_step/record_stop/record_cancel/play/list/template）"))

    async def _macro_execute_step(self, step_type: str, params: dict[str, Any]) -> Any:
        """执行宏中的单步操作。"""
        step_type = str(step_type or "").strip().lower()
        if step_type == "press":
            return await self.press_keys(**params)
        if step_type == "type":
            return await self.type_text(**params)
        if step_type == "mouse_move":
            return await self.mouse_move(**params)
        if step_type in ("mouse_click", "click"):
            # "click" 是触发器/工具描述里宣称的别名，此前没有分支 ——
            # 每次命中都失败还消耗 max_fires 配额。
            return await self.mouse_click(**params)
        if step_type == "mouse_drag":
            return await self.mouse_drag(**params)
        if step_type == "mouse_wheel":
            return await self.mouse_wheel(**params)
        if step_type == "wait":
            seconds = params.get("seconds", 0.5)
            await asyncio.sleep(float(seconds))
            return {"ok": True, "waited": seconds}
        if step_type == "launch_app":
            return await self.launch_app(**params)
        if step_type == "run_command":
            return await self.run_command(**params)
        if step_type == "sequence":
            return await self.press_sequence(**params)
        if step_type == "notify":
            return await self._show_notification(**params)
        return {"ok": False, "error": f"不支持的步骤类型：{step_type}"}

    # ── Hosted UI ───────────────────────────────────────────────────────

    @ui.context(id="dashboard", title=tr("panel.title", default="按键控制"))
    async def get_dashboard_ui_context(self) -> dict[str, Any]:
        target = None
        focused = False
        target_alive = False
        if self._target is not None:
            target = {
                "pid": self._target.get("pid"),
                "title": self._target.get("title"),
                "process_name": self._target.get("process_name"),
            }
        if _is_windows() and self._target is not None:
            if int(self._target.get("pid") or 0) == _FULLSCREEN_PID:
                target_alive = True
                focused = (await asyncio.to_thread(win32.foreground_hwnd)) > 0
            else:
                window = await asyncio.to_thread(win32.find_window_for_pid, int(self._target.get("pid") or 0))
                if window is not None:
                    target_alive = True
                    focused = await asyncio.to_thread(
                        win32.foreground_matches,
                        int(window.get("hwnd") or 0),
                        int(self._target.get("pid") or 0),
                    )
        if self._target is not None and _is_windows() and not target_alive:
            self._target = None
            await self._persist_target(None)
            target = None
            self.logger.info("target window gone, cleared stale target")
        capture_info = {}
        if _is_windows():
            capture_info = await asyncio.to_thread(capture.describe_capture)
        audio_info = {}
        if _is_windows():
            audio_info = await asyncio.to_thread(audio_analysis.describe_audio)
        async with self._pending_lock:
            self._expire_pending_locked(now=time.time())
            pending = [
                {
                    "token": p.get("token"),
                    "command": p.get("command"),
                    "shell": p.get("shell"),
                    "status": p.get("status"),
                    "created_at": p.get("created_at"),
                    "output": p.get("output", "") if p.get("status") == "done" else "",
                }
                for p in self._pending_commands
            ]
        diary_state = {
            "enabled": bool(self._diary_enabled),
            "dir": str(self._diary_dir_path()),
        }
        if self._diary is not None:
            day = datetime.now().strftime("%Y-%m-%d")
            counts = self._diary.counts(day)
            diary_state["date"] = day
            diary_state["event_count"] = sum(counts.values())
            diary_state["counts"] = counts
            diary_state["summary"] = diary.summarize_counts(counts, locale=self._diary._locale)
            diary_state["flushed"] = bool((self._diary_dir_path() / f"{day}.md").is_file())
        return {
            "platform": sys.platform,
            "windows_supported": _is_windows(),
            "target": target,
            "focused": focused,
            "allow_unguided": bool(getattr(self, "_allow_unguided", False)),
            "safety_block_anti_cheat": bool(getattr(self, "_block_anti_cheat", True)),
            "safety_block_elevated_target": bool(getattr(self, "_block_elevated", True)),
            "safety_require_focus": bool(getattr(self, "_require_focus", True)),
            "store_enabled": bool(self.store.enabled),
            "save_screenshots": bool(self._save_screenshots),
            "ocr_available": bool(capture_info.get("ocr_available", False)),
            "mss_available": bool(capture_info.get("mss_available", False)),
            "audio_available": bool(audio_info.get("available", False)),
            "command_require_confirmation": bool(self._command_require_confirmation),
            "pending_commands": pending,
            "diary": diary_state,
            "message": None,
        }
