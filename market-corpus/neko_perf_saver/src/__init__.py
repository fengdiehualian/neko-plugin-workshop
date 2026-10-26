"""N.E.K.O 性能守护插件（neko_perf_saver）。

功能
----
当「指定软件」（默认：三角洲行动 Delta Force）在运行时，自动降低 N.E.K.O 自身的
内存与 GPU 占用：

1. **停止界面渲染**：挂起 Electron 渲染进程（必要时连 GPU 进程一起挂起）。
2. **关闭可以关闭的进程**：结束可被 Chromium 自动重建的工具进程（视频采集服务等），
   额外进程可在配置里自行添加。
3. **内存回收**：对 N.E.K.O 全进程调用 EmptyWorkingSet 回收工作集，并降低后台进程优先级。

恢复保证
--------
- 目标软件退出后自动恢复（可配置）；
- 插件被「停止 / 禁用 / 重载」时（``shutdown``）强制恢复；
- 插件被「冻结」（``freeze``）时恢复；``unfreeze`` 后重新进入守护；
- 插件进程**正常退出**时由 ``atexit`` 兜底恢复；
- 插件进程**被强杀**（Windows ``TerminateProcess``）或宿主崩溃时 ``atexit`` 不会执行，
  会话状态已落盘，插件**下次启动**会自动救援上次遗留的挂起进程 / 优先级 / 可重启的额外进程
  （窗口期：插件下次启动之前，详见 README「恢复保证」）。

设计约束（安全第一）
--------------------
只操作名字命中 ``neko_process_names`` 的 N.E.K.O 进程；**绝不碰目标游戏与任何
其它进程**（除非用户显式配置了 ``close_extra_processes``，且该配置带三重安全护栏：
目标软件/插件自身/系统关键进程永不结束、过宽通配拒绝、单次上限）。
"""

from __future__ import annotations

import asyncio
import atexit
import ctypes
import json
import logging
import os
import re
import subprocess
import threading
import time
from fnmatch import fnmatch
from pathlib import Path
from typing import Any, Dict, List, Optional

from plugin.sdk.plugin import (
    Err,
    NekoPluginBase,
    Ok,
    SdkError,
    lifecycle,
    neko_plugin,
    plugin_entry,
    quick_action,
    timer_interval,
)

# ──────────────────────────────────────────────────────────────────────
# 常量：默认配置
# ──────────────────────────────────────────────────────────────────────

_DEFAULT_TARGETS = [
    "DeltaForceClient-Win64-Shipping.exe",  # 三角洲行动（Steam / WeGame 主程序）
    "DeltaForceClient.exe",
]

_DEFAULTS: Dict[str, Any] = {
    # 总开关：false 时插件只待命，不会自动优化（仍可手动 apply_now）
    "saver_enabled": True,
    # 触发软件：进程名列表，支持 * 通配符（大小写不敏感）
    "target_processes": list(_DEFAULT_TARGETS),
    # 目标进程必须拥有可见窗口才算「在运行」
    "require_visible_window": False,
    # 巡检间隔（秒）
    "watch_interval_seconds": 4,
    # 已优化状态下，多久做一次维护（扫描新增渲染进程、回收内存）
    "maintenance_interval_seconds": 20,
    # ── 优化动作 ──
    "freeze_renderers": True,          # 挂起渲染进程 = 停止界面渲染（核心）
    "freeze_gpu": False,               # 挂起 GPU 进程（收益有限，且 Chromium 有看门狗，默认关）
    "freeze_utility_services": False,  # 挂起全部 Electron 工具进程（音频/网络/采集）
    "close_video_capture_service": True,   # 结束视频采集服务（可自动重建）
    "close_audio_service": False,          # 结束音频服务（会让 N.E.K.O 静音，可选）
    "close_network_service": False,        # 结束网络服务（激进，可选）
    "close_extra_processes": [],           # 额外要结束的进程名（尽力在恢复时重启）
    # ── 内存 / 调度 ──
    "trim_working_set": True,          # EmptyWorkingSet 回收 N.E.K.O 工作集
    "trim_backend_processes": True,    # 是否连 Python 后端（projectneko_server）一起回收
    "trim_interval_seconds": 60,       # 已优化状态下重复回收的间隔
    "lower_priority": True,            # 降低 N.E.K.O 进程优先级（IDLE）
    "lower_priority_backend": True,    # 是否连 Python 后端（projectneko_server）一起降优先级
    # ── 恢复策略 ──
    "skip_while_neko_foreground": True,  # N.E.K.O 窗口在前台时临时恢复，切走后再优化
    "restore_on_target_exit": True,      # 目标软件退出后自动恢复
    # ── 识别 N.E.K.O 进程 ──
    "neko_process_names": ["N.E.K.O.exe", "n.e.k.o.exe", "projectneko_server.exe"],
    # ── 使用前免责声明（US-001） ──
    # false（默认）= 还没确认过 ⇒ 面板打开时提示、apply_now 的返回里附软警告；
    # 面板勾选「以后都不再提醒」后由 acknowledge_disclaimer 入口写回 true。
    # 本键只影响「是否提示」，**不影响任何降负载行为**（决策 1A：软提示，不硬拒）。
    "disclaimer_acknowledged": False,
}

_BOOL_KEYS = {k for k, v in _DEFAULTS.items() if isinstance(v, bool)}
_INT_KEYS = {k for k, v in _DEFAULTS.items() if isinstance(v, int) and not isinstance(v, bool)}
_LIST_KEYS = {k for k, v in _DEFAULTS.items() if isinstance(v, list)}

# Windows 进程访问权限
_PROCESS_TERMINATE = 0x0001
_PROCESS_SET_QUOTA = 0x0100
_PROCESS_SET_INFORMATION = 0x0200
_PROCESS_QUERY_INFORMATION = 0x0400
_PROCESS_SUSPEND_RESUME = 0x0800
_PROCESS_QUERY_LIMITED = 0x1000
_PROCESS_ALL = 0x1F0FFF

_IDLE_PRIORITY_CLASS = 0x00000040
_NORMAL_PRIORITY_CLASS = 0x00000020

# ── 安全护栏（G2 / G5 / G7 / G11） ──
# close_extra_processes 命中这些系统关键进程时一律跳过（误配防护，不做可配置项）
_SYSTEM_PROTECTED = frozenset({
    "system", "registry", "idle", "memory compression", "secure system",
    "smss.exe", "csrss.exe", "wininit.exe", "winlogon.exe", "services.exe",
    "lsass.exe", "lsaiso.exe", "svchost.exe", "fontdrvhost.exe", "dwm.exe",
    "explorer.exe", "sihost.exe", "taskhostw.exe", "ctfmon.exe", "spoolsv.exe",
    "audiodg.exe", "conhost.exe", "runtimebroker.exe", "searchindexer.exe",
    "searchhost.exe", "startmenuexperiencehost.exe", "shellexperiencehost.exe",
    "textinputhost.exe", "applicationframehost.exe", "widgets.exe",
    "securityhealthservice.exe", "msmpeng.exe", "nissrv.exe", "wudfhost.exe",
    "logonui.exe", "taskmgr.exe", "powershell.exe", "cmd.exe",
})
# 单次优化最多结束的额外进程数量（防止一条通配配置批量杀进程）
_MAX_EXTRA_KILLS = 5
# 含通配符的模式，其“非通配字样”部分至少要有这么多字符（"*" / "?" / "*.exe" 会被拒绝）
_MIN_WILDCARD_LITERAL = 3
# PID 复用判定：记录的 create_time 与当前值相差超过该秒数 ⇒ 视为不同进程，跳过
_PID_CREATE_TIME_TOLERANCE = 0.5
# 前台保护检查间隔（秒）：不受 maintenance_interval_seconds 拖累（G7）
_FOREGROUND_CHECK_SECONDS = 1.0
# 恢复被挂起进程的最大尝试次数 + 两次尝试之间的间隔。
# 原因：NTDLL 的 NtSuspendProcess 是**计数式**的（每调用一次挂起计数 +1），
# 只要计数 > 1，单次 NtResumeProcess 之后进程依然是冻结的。计数被外部抬高
# 的现实场景：调试器附着、别的工具做了 toolhelp 快照、用户手动挂起等。
# 因此恢复必须「调用 → 校验 → 必要时重试」，绝不能只发一次就宣布成功。
_RESUME_ATTEMPTS = 3
_RESUME_RETRY_DELAY = 0.05
# saver_enabled=false 时巡检降频倍数（G11）
_DISABLED_INTERVAL_FACTOR = 8.0

_DETACHED_PROCESS = 0x00000008
_CREATE_NEW_PROCESS_GROUP = 0x00000200
_CREATE_NO_WINDOW = 0x08000000

_IS_WINDOWS = os.name == "nt"
_SESSION_FILE = "saver_session.json"
_SETTINGS_FILE = "saver_settings.json"
# US-001：免责声明确认时间（只由 acknowledge_disclaimer 写入；get_disclaimer 只读）
_DISCLAIMER_FILE = "disclaimer_ack.json"

# ── 使用前免责声明（US-001，冻结契约：文案逐字，不得改写） ──
_DISCLAIMER_TEXT = "本插件不保证不会触发游戏反作弊，使用本插件引起的后果与本插件无关。"
# apply_now 的软警告前缀（逐字包含上面的免责句；决策 1A：只提示，不拒绝执行）
_DISCLAIMER_WARNING = "尚未确认使用前免责声明：" + _DISCLAIMER_TEXT

_MISSING = object()  # 区分「键不存在」与「键的值是 None」

_STATE_APPLIED = "applied"        # 已优化
_STATE_PAUSED = "paused"          # 已优化但被前台检测临时恢复
_STATE_IDLE = "idle"              # 待命


# ──────────────────────────────────────────────────────────────────────
# Windows 底层操作（纯 ctypes，无第三方依赖）
# ──────────────────────────────────────────────────────────────────────

_LIB_CACHE: Dict[str, Any] = {}


def _lib(name: str):
    """懒加载 Win32 DLL，失败返回 None。"""
    if not _IS_WINDOWS:
        return None
    cached = _LIB_CACHE.get(name)
    if cached is None:
        try:
            cached = ctypes.WinDLL(name, use_last_error=True)
        except Exception:
            cached = False
        _LIB_CACHE[name] = cached
    return cached or None


def _open_process(pid: int, access: int) -> Optional[int]:
    k32 = _lib("kernel32")
    if k32 is None or not pid or pid <= 0:
        return None
    try:
        handle = k32.OpenProcess(access, False, int(pid))
    except Exception:
        return None
    return handle or None


def _close_handle(handle: int) -> None:
    if not handle:
        return
    try:
        _lib("kernel32").CloseHandle(handle)
    except Exception:
        pass


def proc_suspend(pid: int) -> bool:
    """挂起进程（NtSuspendProcess），可被 proc_resume 精确逆转。"""
    handle = _open_process(pid, _PROCESS_ALL) or _open_process(pid, _PROCESS_SUSPEND_RESUME)
    if not handle:
        return False
    try:
        ntdll = _lib("ntdll")
        status = ntdll.NtSuspendProcess(handle)
        return int(status) == 0
    except Exception:
        return False
    finally:
        _close_handle(handle)


def proc_resume(pid: int) -> bool:
    """恢复被挂起的进程（NtResumeProcess）。"""
    handle = _open_process(pid, _PROCESS_ALL) or _open_process(pid, _PROCESS_SUSPEND_RESUME)
    if not handle:
        return False
    try:
        ntdll = _lib("ntdll")
        status = ntdll.NtResumeProcess(handle)
        return int(status) == 0
    except Exception:
        return False
    finally:
        _close_handle(handle)


def proc_trim_working_set(pid: int) -> bool:
    """把进程工作集换出到页面文件（EmptyWorkingSet）。"""
    handle = (
        _open_process(pid, _PROCESS_QUERY_INFORMATION | _PROCESS_SET_QUOTA)
        or _open_process(pid, _PROCESS_ALL)
    )
    if not handle:
        return False
    try:
        psapi = _lib("psapi")
        return bool(psapi.EmptyWorkingSet(handle))
    except Exception:
        return False
    finally:
        _close_handle(handle)


def proc_terminate(pid: int) -> bool:
    handle = _open_process(pid, _PROCESS_TERMINATE) or _open_process(pid, _PROCESS_ALL)
    if not handle:
        return False
    try:
        k32 = _lib("kernel32")
        return bool(k32.TerminateProcess(handle, 1))
    except Exception:
        return False
    finally:
        _close_handle(handle)


def proc_priority(pid: int) -> Optional[int]:
    handle = _open_process(pid, _PROCESS_QUERY_INFORMATION | _PROCESS_QUERY_LIMITED)
    if not handle:
        return None
    try:
        k32 = _lib("kernel32")
        value = int(k32.GetPriorityClass(handle))
        return value or None
    except Exception:
        return None
    finally:
        _close_handle(handle)


def proc_set_priority(pid: int, priority_class: int) -> bool:
    handle = (
        _open_process(pid, _PROCESS_SET_INFORMATION)
        or _open_process(pid, _PROCESS_SET_INFORMATION | _PROCESS_QUERY_INFORMATION)
        or _open_process(pid, _PROCESS_ALL)
    )
    if not handle:
        return False
    try:
        k32 = _lib("kernel32")
        return bool(k32.SetPriorityClass(handle, int(priority_class)))
    except Exception:
        return False
    finally:
        _close_handle(handle)


def foreground_pid() -> int:
    """当前前台窗口所属进程 PID（0 表示未知）。"""
    u32 = _lib("user32")
    if u32 is None:
        return 0
    try:
        hwnd = u32.GetForegroundWindow()
        if not hwnd:
            return 0
        pid = ctypes.c_ulong(0)
        u32.GetWindowThreadProcessId(ctypes.c_void_p(hwnd), ctypes.byref(pid))
        return int(pid.value)
    except Exception:
        return 0


def pids_with_visible_window() -> set:
    """拥有可见顶层窗口的进程 PID 集合。"""
    u32 = _lib("user32")
    if u32 is None:
        return set()
    found: set = set()
    try:
        callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

        def _callback(hwnd, _lparam):
            try:
                if u32.IsWindowVisible(ctypes.c_void_p(hwnd)):
                    pid = ctypes.c_ulong(0)
                    u32.GetWindowThreadProcessId(ctypes.c_void_p(hwnd), ctypes.byref(pid))
                    if pid.value:
                        found.add(int(pid.value))
            except Exception:
                pass
            return True

        u32.EnumWindows(callback_type(_callback), 0)
    except Exception:
        return set()
    return found


# ──────────────────────────────────────────────────────────────────────
# 进程枚举
# ──────────────────────────────────────────────────────────────────────

try:  # psutil 随 N.E.K.O 一起分发；缺失时退化为 PowerShell 查询
    import psutil  # type: ignore
except Exception:  # pragma: no cover
    psutil = None  # type: ignore


class Proc:
    """进程信息轻量载体。"""

    __slots__ = ("pid", "ppid", "name", "exe", "cmdline", "create_time", "rss")

    def __init__(self, pid: int, ppid: int, name: str, exe: str, cmdline: List[str],
                 create_time: float, rss: int):
        self.pid = pid
        self.ppid = ppid
        self.name = name
        self.exe = exe
        self.cmdline = cmdline
        self.create_time = create_time
        self.rss = rss

    @property
    def cmd(self) -> str:
        return " ".join(self.cmdline)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "pid": self.pid,
            "name": self.name,
            "rss_mb": round(self.rss / 1048576.0, 1),
            "create_time": round(self.create_time, 3),
        }


_PS_ENUM = (
    "Get-CimInstance Win32_Process | "
    "Select-Object ProcessId,ParentProcessId,Name,ExecutablePath,CommandLine,CreationDate | "
    "ConvertTo-Json -Compress"
)
_DATE_RE = re.compile(r"\\?/Date\((\d+)\)\\?/")  # /Date(ms)/ 与 Windows PowerShell 的 \/Date(ms)\/


def _ps_creation_time(raw: Any) -> float:
    """解析 PowerShell 输出的时间。

    G5 关联缺陷：Windows PowerShell（5.1）的 `ConvertTo-Json` 会把 `DateTime` 序列化成
    ``"\\/Date(1789478376589)\\/"``（斜杠被转义）。旧正则只认 ``/Date(ms)/``，于是
    PowerShell 后端枚举出来的 ``create_time`` 全是 ``0.0`` —— 记录里没有可比对的时间，
    PID 复用校验在这条路径上会**静默失效**。这里同时兼容两种转义形式。
    """
    if not isinstance(raw, str) or not raw:
        return 0.0
    match = _DATE_RE.search(raw)
    if match:
        try:
            return int(match.group(1)) / 1000.0
        except Exception:
            return 0.0
    try:  # PowerShell 7 的 ISO 时间
        from datetime import datetime
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0


def _enum_with_psutil() -> List[Proc]:
    attrs = ["pid", "ppid", "name", "exe", "cmdline", "create_time", "memory_info"]
    result: List[Proc] = []
    for proc in psutil.process_iter():  # type: ignore[union-attr]
        try:
            info = proc.as_dict(attrs=attrs, ad_value=None)
        except Exception:
            continue
        pid = info.get("pid")
        if not pid:
            continue
        mem = info.get("memory_info")
        result.append(
            Proc(
                pid=int(pid),
                ppid=int(info.get("ppid") or 0),
                name=str(info.get("name") or ""),
                exe=str(info.get("exe") or ""),
                cmdline=[str(x) for x in (info.get("cmdline") or [])],
                create_time=float(info.get("create_time") or 0.0),
                rss=int(getattr(mem, "rss", 0) or 0),
            )
        )
    return result


def _enum_with_powershell() -> List[Proc]:
    try:
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", _PS_ENUM],
            capture_output=True,
            timeout=25,
            creationflags=_CREATE_NO_WINDOW,
        )
    except Exception:
        return []
    text = (completed.stdout or b"").decode("utf-8", "replace").strip()
    if not text:
        return []
    try:
        payload = json.loads(text)
    except Exception:
        return []
    if isinstance(payload, dict):
        payload = [payload]
    result: List[Proc] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        try:
            cmdline = _split_cmdline(str(item.get("CommandLine") or ""))
            result.append(
                Proc(
                    pid=int(item.get("ProcessId") or 0),
                    ppid=int(item.get("ParentProcessId") or 0),
                    name=str(item.get("Name") or ""),
                    exe=str(item.get("ExecutablePath") or ""),
                    cmdline=cmdline,
                    create_time=_ps_creation_time(item.get("CreationDate")),
                    rss=0,
                )
            )
        except Exception:
            continue
    return [p for p in result if p.pid > 0]


def _split_cmdline(raw: str) -> List[str]:
    """把命令行字符串切成 argv（够用的引号处理）。"""
    if not raw or not raw.strip():
        return []
    parts: List[str] = []
    buf: List[str] = []
    in_quote = False
    for ch in raw.strip():
        if ch == '"':
            in_quote = not in_quote
            continue
        if ch in " \t" and not in_quote:
            if buf:
                parts.append("".join(buf))
                buf = []
            continue
        buf.append(ch)
    if buf:
        parts.append("".join(buf))
    return parts


def _match_any(name: str, patterns: List[str]) -> bool:
    if not name:
        return False
    lowered = name.lower()
    for pattern in patterns:
        pat = str(pattern or "").strip()
        if not pat:
            continue
        pat_low = pat.lower()
        if "*" in pat_low or "?" in pat_low:
            if fnmatch(lowered, pat_low):
                return True
        elif lowered == pat_low or lowered.endswith(pat_low):
            return True
    return False


def _safe_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _truthy(value: Any) -> bool:
    """把入口收到的「字符串布尔」解析成 bool（用于 dry_run 这类可选参数）。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on", "开", "开启", "是", "dry", "dry_run"}
    return bool(value)


# ──────────────────────────────────────────────────────────────────────
# 插件主体
# ──────────────────────────────────────────────────────────────────────


@neko_plugin
class NekoPerfSaverPlugin(NekoPluginBase):
    """在指定软件运行时给 N.E.K.O 降负载，并在关闭时完整恢复。"""

    def __init__(self, ctx: Any):
        super().__init__(ctx)
        try:
            self.logger = self.enable_file_logging(log_level="INFO")
        except Exception:
            self.logger = getattr(ctx, "logger", None) or logging.getLogger("neko_perf_saver")

        self._lock = threading.RLock()
        self._busy = False
        self._cfg: Dict[str, Any] = dict(_DEFAULTS)
        self._overrides: Dict[str, Any] = {}

        self._state = _STATE_IDLE
        self._active_since = 0.0
        self._last_reason = ""
        self._soft_paused = False            # 因 N.E.K.O 处于前台而临时恢复
        self._last_check = 0.0
        self._last_maintenance = 0.0
        self._last_trim = 0.0
        self._last_fg_check = 0.0          # 前台保护检查节流（G7）
        self._fg_state: Optional[bool] = None  # 上次前台判定结果
        self._toml_section: Dict[str, Any] = {}  # 最近一次读到的 plugin.toml [perf_saver]（G1）
        self._dropped_overrides: List[str] = []  # 被 plugin.toml 覆盖回收的 override 键（G1）
        self._target_seen = False
        self._last_error = ""
        self._started_at = time.time()
        self._atexit_hooked = False
        # 最近一次操作（US-004：hosted UI 面板的「最近一次操作摘要」直接读这个字段）
        self._last_action: Dict[str, Any] = {}
        # US-001：免责声明确认时间（懒加载自 disclaimer_ack.json；None = 没有记录）
        self._disclaimer_ack_at: Optional[str] = None

        # 会话记录（供恢复使用）
        self._suspended: List[Dict[str, Any]] = []
        self._closed: List[Dict[str, Any]] = []
        self._priorities: List[Dict[str, Any]] = []

        self._enum_cache: List[Proc] = []
        self._enum_cache_at = 0.0
        self._state_dir: Optional[Path] = None

        # atexit 兜底：插件进程被直接杀掉时也要把进程还回去
        try:
            atexit.register(self._emergency_restore)
            self._atexit_hooked = True
        except Exception:
            pass

    # ──────────────────────────────────────────────────────────────
    # 生命周期
    # ──────────────────────────────────────────────────────────────

    @lifecycle(id="startup")
    async def on_startup(self, **_):
        self._state_dir = self._resolve_state_dir()
        self._overrides = self._load_json(_SETTINGS_FILE) or {}
        await self._reload_config()
        self._rescue_previous_session()
        static_ui = self._register_static_ui()
        self._started_at = time.time()
        self.logger.info(
            "neko_perf_saver 已就绪：saver_enabled=%s, targets=%s, backend=%s, state_dir=%s, static_ui=%s",
            self._cfg.get("saver_enabled"),
            self._cfg.get("target_processes"),
            "psutil" if psutil else "powershell",
            self._state_dir,
            static_ui,
        )
        return Ok({
            "status": "ready",
            "saver_enabled": bool(self._cfg.get("saver_enabled")),
            "targets": list(self._cfg.get("target_processes") or []),
            "backend": "psutil" if psutil else "powershell",
            "static_ui": static_ui,
        })

    @lifecycle(id="shutdown")
    async def on_shutdown(self, **_):
        # 关闭插件 = 必须把所有被我们动过的进程恢复原状
        report = await asyncio.to_thread(self._restore_sync, "plugin_shutdown", True)
        self.logger.info("neko_perf_saver 已停止，恢复结果：%s", json.dumps(report, ensure_ascii=False))
        return Ok({"status": "stopped", "restore": report})

    @lifecycle(id="freeze")
    async def on_freeze(self, **_):
        # 插件被挂起（宿主不再调度）→ 恢复进程，避免无人负责
        report = await asyncio.to_thread(self._restore_sync, "plugin_freeze", True)
        self.logger.info("neko_perf_saver 冻结，已恢复：%s", json.dumps(report, ensure_ascii=False))
        return Ok({"status": "frozen", "restore": report})

    @lifecycle(id="unfreeze")
    async def on_unfreeze(self, **_):
        self._last_check = 0.0
        self.logger.info("neko_perf_saver 已解冻，恢复守护")
        return Ok({"status": "unfrozen"})

    @lifecycle(id="config_change")
    async def on_config_change(self, old_config=None, new_config=None, mode=None, **_):
        # G1：用户在插件管理器配置页改了 plugin.toml ⇒ 该键的 override 必须让位，
        # 否则「用入口关掉、再用管理器打开」会永久失效（UI 撒谎）。
        dropped = self._reconcile_overrides(old_config, new_config)
        await self._reload_config()
        if dropped:
            self._save_json(_SETTINGS_FILE, self._overrides)
            self.logger.info("plugin.toml 的改动已生效，回收以下本地覆盖：%s", dropped)
        return Ok({
            "status": "config_updated",
            "saver_enabled": bool(self._cfg.get("saver_enabled")),
            "overrides_dropped": dropped,
        })

    def _register_static_ui(self) -> bool:
        """把 ``<plugin_dir>/static`` 交给宿主托管（US-004 的 hosted UI 面板）。

        与宿主自带插件（``wechat_integration`` / ``qq_auto_reply``：``[[plugin.ui.panel]]``
        + ``entry = "static/index.html"``）一致，在 startup 里注册静态目录，面板才能被
        ``/plugin/neko_perf_saver/ui/`` 正常提供。

        宿主 SDK 没有这个接口（旧版本 / 测试桩）时静默跳过：面板不可用不该拖垮插件本体。
        """
        register = getattr(self, "register_static_ui", None)
        if register is None:
            return False
        try:
            return bool(register("static"))
        except Exception as exc:
            try:
                self.logger.warning("注册静态面板目录失败（面板可能打不开）：%s", exc)
            except Exception:
                pass
            return False

    def _note_action(self, entry_id: str, ok: bool, summary: str) -> None:
        """记录「最近一次操作」，供 hosted UI 面板显示（US-004）。

        只保留最近一条；时间同时给本地时间字符串（给人看）与 epoch（给前端排序/计算）。
        """
        self._last_action = {
            "entry": str(entry_id),
            "ok": bool(ok),
            "summary": str(summary or ""),
            "at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
            "ts": round(time.time(), 3),
        }

    def _toml_values(self, config: Any) -> Dict[str, Any]:
        """从 lifecyle 回调给的配置对象里取 [perf_saver] 段（统一转成内部类型）。"""
        if not isinstance(config, dict):
            return {}
        section = config.get("perf_saver")
        if not isinstance(section, dict):
            section = config
        out: Dict[str, Any] = {}
        for key, value in section.items():
            if key in _DEFAULTS:
                coerced = _coerce_value(key, value)
                if coerced is not None:
                    out[key] = coerced
        return out

    def _reconcile_overrides(self, old_config: Any, new_config: Any) -> List[str]:
        """G1：把被 plugin.toml「明确改过」的键从本地 overrides 里摘掉。

        - 老/新 toml 里该键的值发生了变化 ⇒ 用户在管理器里动过它 ⇒ 回收 override；
        - override 的值已经和新 toml 一致 ⇒ 回收（删掉冗余覆盖，避免状态漂移）；
        - 新旧配置都拿不到时不做任何回收（宁可保守，也不误删用户设置）。
        """
        new = self._toml_values(new_config) if new_config is not None else {}
        old = self._toml_values(old_config) if old_config is not None else {}
        dropped: List[str] = []
        for key in list(self._overrides):
            if key not in new:
                continue
            if key in old and old.get(key) != new.get(key):
                dropped.append(key)
            elif self._overrides.get(key) == new.get(key):
                dropped.append(key)
        for key in dropped:
            self._overrides.pop(key, None)
        if dropped:
            self._dropped_overrides = sorted(set(self._dropped_overrides) | set(dropped))
        return dropped

    # ──────────────────────────────────────────────────────────────
    # 巡检定时器
    # ──────────────────────────────────────────────────────────────

    @timer_interval(id="watch", seconds=3, name="性能守护巡检", auto_start=True)
    async def watch(self, **_):
        try:
            await asyncio.to_thread(self._tick)
        except Exception as exc:  # 定时器异常不应中断后续巡检
            self._last_error = f"{type(exc).__name__}: {exc}"
            try:
                self.logger.warning("巡检异常：%s", self._last_error)
            except Exception:
                pass
        return Ok({"state": self._state})

    # ──────────────────────────────────────────────────────────────
    # 对外入口
    # ──────────────────────────────────────────────────────────────

    @plugin_entry(
        id="status",
        name="查看守护状态",
        description="查看当前是否处于降负载状态、触发的软件、被挂起/结束的进程，以及各项开关。",
        kind="service",
        llm_result_fields=["summary", "state", "target_running", "saver_enabled"],
    )
    @quick_action(icon="🛡️", priority=30)
    async def status(self, **_):
        info = await asyncio.to_thread(self._status_sync)
        return Ok(info)

    @plugin_entry(
        id="enable_saver",
        name="开启自动守护",
        description="开启自动守护：指定软件启动时自动降低 N.E.K.O 占用，退出后自动恢复。",
    )
    async def enable_saver(self, **_):
        changed = await self._set_option("saver_enabled", True)
        message = "自动守护已开启" if changed else "自动守护本来就是开启状态（无变化）"
        # 幂等：已经是开启状态时不再重复写盘，也不碰任何进程
        self._note_action("enable_saver", True, message)
        return Ok({
            "saver_enabled": True,
            "changed": changed,
            "message": message,
        })

    @plugin_entry(
        id="disable_saver",
        name="关闭自动守护",
        description="关闭自动守护并立即恢复所有被挂起的进程（插件本体仍在运行，可随时再开启）。",
    )
    async def disable_saver(self, **_):
        changed = await self._set_option("saver_enabled", False)
        report = await asyncio.to_thread(self._restore_sync, "saver_disabled", True)
        message = "自动守护已关闭，进程已恢复" if changed else "自动守护本来就是关闭状态（仅做恢复检查）"
        self._note_action("disable_saver", True, message)
        return Ok({
            "saver_enabled": False,
            "changed": changed,
            "restore": report,
            "message": message,
        })

    @plugin_entry(
        id="toggle_saver",
        name="切换自动守护",
        description="在开启/关闭自动守护之间切换；关闭时会立即恢复被挂起的进程。",
    )
    async def toggle_saver(self, **_):
        if bool(self._cfg.get("saver_enabled")):
            payload = await self.disable_saver(**_)
            text = "自动守护已关闭（进程已恢复）"
        else:
            payload = await self.enable_saver(**_)
            text = "自动守护已开启"
        # 面板上点的是「开关」，所以最近一次操作记 toggle_saver 而不是被委托的入口
        self._note_action("toggle_saver", True, text)
        return payload

    # ──────────────────────────────────────────────────────────────
    # 使用前免责声明（US-001）
    # ──────────────────────────────────────────────────────────────

    def _disclaimer_acknowledged_at(self) -> Optional[str]:
        """已确认时间（只读，懒加载；没有记录返回 None）。

        只在 ``disclaimer_ack.json`` 里落一个「人类可读时间戳」，供面板显示；
        真正的「确认过没有」以配置项 ``disclaimer_acknowledged`` 为准。
        """
        if self._disclaimer_ack_at is None:
            data = self._load_json(_DISCLAIMER_FILE)
            if isinstance(data, dict):
                stamp = data.get("acknowledged_at")
                if isinstance(stamp, str) and stamp.strip():
                    self._disclaimer_ack_at = stamp
        return self._disclaimer_ack_at

    def _disclaimer_state(self) -> Dict[str, Any]:
        """免责声明当前状态。**只读**：不写盘、不枚举也不触碰任何进程。"""
        acknowledged = bool(self._cfg.get("disclaimer_acknowledged"))
        return {
            "acknowledged": acknowledged,
            # required = 还需要提醒（未永久确认）；与承诺给面板的语义一一对应
            "required": not acknowledged,
            "text": _DISCLAIMER_TEXT,
            # dont_remind_again = 「以后都不再提醒」当前是否生效（= 已确认）
            "dont_remind_again": acknowledged,
            "acknowledged_at": self._disclaimer_acknowledged_at() if acknowledged else None,
        }

    @plugin_entry(
        id="get_disclaimer",
        name="查看免责声明",
        description=(
            "只读查看「使用前免责声明」：acknowledged=是否已确认、required=是否还需要提醒"
            "（= not acknowledged）、text=免责正文、acknowledged_at=确认时间。"
        ),
    )
    async def get_disclaimer(self, **_):
        # 只读入口：绝不写盘、绝不枚举/触碰任何进程
        return Ok(self._disclaimer_state())

    @plugin_entry(
        id="acknowledge_disclaimer",
        name="确认免责声明",
        description=(
            "确认已阅读「使用前免责声明」。dont_remind_again=true 表示「以后都不再提醒」"
            "（写入插件配置，之后不再提示）；false 表示这次知道了、下次仍提醒（不写盘）。"
        ),
    )
    async def acknowledge_disclaimer(self, dont_remind_again: str = "false", **_):
        raw: Any = dont_remind_again
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            # 没给值（或空值）按「这次知道了、下次仍提醒」处理：保守，不会永久静音提醒
            flag: Optional[bool] = False
        else:
            if isinstance(raw, (int, float)) and not isinstance(raw, bool) and float(raw) in (0.0, 1.0):
                raw = str(int(raw))  # 数字 1/0 与 "1"/"0" 等价
            # 复用 _DEFAULTS 的布尔解析风格：true/false、1/0、yes/no、开/关……
            flag = _coerce_value("disclaimer_acknowledged", raw)
        if flag is None:
            return Err(SdkError(
                f"无法解析参数 dont_remind_again：{raw!r}。"
                "请传 true 或 false（也接受 1/0、yes/no）。"
            ))

        if not flag:
            # 「这次知道了，下次仍提醒」⇒ 不写盘，required 仍然为 true（幂等、零副作用）
            self._note_action("acknowledge_disclaimer", True, "已了解免责声明（下次仍会提醒）")
            return Ok({
                "ok": True,
                "acknowledged": bool(self._cfg.get("disclaimer_acknowledged")),
                "dont_remind_again": bool(self._cfg.get("disclaimer_acknowledged")),
                "changed": False,
            })

        changed = await self._set_option("disclaimer_acknowledged", True)
        if changed:
            # 只有真的写盘那一次才更新确认时间（保证重复调用不产生任何再次写盘）
            self._disclaimer_ack_at = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
            self._save_json(_DISCLAIMER_FILE, {
                "acknowledged": True,
                "acknowledged_at": self._disclaimer_ack_at,
                "dont_remind_again": True,
            })
        acknowledged = bool(self._cfg.get("disclaimer_acknowledged"))
        self._note_action(
            "acknowledge_disclaimer", True,
            "已确认免责声明：以后不再提醒" if changed else "免责声明之前已确认（无变化）",
        )
        return Ok({
            "ok": True,
            "acknowledged": acknowledged,
            "dont_remind_again": acknowledged,
            "changed": bool(changed),
        })

    @plugin_entry(
        id="apply_now",
        name="立即优化",
        description="不等目标软件，立刻对 N.E.K.O 执行一次降负载（挂起渲染、关进程、回收内存）。",
    )
    async def apply_now(self, **_):
        result = dict(await asyncio.to_thread(self._apply_sync, "manual"))
        if not bool(self._cfg.get("disclaimer_acknowledged")):
            # US-001 + 决策 1A：未确认免责声明时只在返回值里附**软警告**，
            # 绝不因此拒绝执行（状态仍照常变为 applied）
            result["disclaimer_warning"] = _DISCLAIMER_WARNING
            result["disclaimer_required"] = True
        self._note_action("apply_now", True, result.get("summary") or result.get("message") or "已执行")
        return Ok(result)

    @plugin_entry(
        id="restore_now",
        name="立即恢复",
        description="立刻恢复所有被挂起/结束的进程，回到完全正常的 N.E.K.O。",
    )
    async def restore_now(self, **_):
        result = await asyncio.to_thread(self._restore_sync, "manual", True)
        self._note_action("restore_now", True, result.get("summary") or result.get("message") or "已执行")
        return Ok(result)

    @plugin_entry(
        id="set_target_processes",
        name="设置触发的软件",
        description=(
            "设置触发降负载的软件进程名，多个用逗号或换行分隔（大小写不敏感，支持 * 通配）。"
            "例如：DeltaForceClient-Win64-Shipping.exe,Cyberpunk2077.exe。留空表示清空。"
        ),
    )
    async def set_target_processes(self, processes: str = "", **_):
        items = [x.strip() for x in re.split(r"[,;\n\r]+", str(processes or "")) if x.strip()]
        await self._set_option("target_processes", items)
        return Ok({
            "target_processes": items,
            "message": f"已设置 {len(items)} 个触发软件" + ("（空列表）" if not items else ""),
        })

    @plugin_entry(
        id="set_option",
        name="修改设置",
        description=(
            "修改插件开关。key 取值见 plugin.toml 的 [perf_saver] 段，"
            "例如 freeze_renderers / close_audio_service / trim_working_set / lower_priority / "
            "watch_interval_seconds / skip_while_neko_foreground / restore_on_target_exit。"
        ),
    )
    async def set_option(self, key: str, value: str = "", dry_run: str = "", **_):
        if not key or key not in _DEFAULTS:
            return Err(SdkError(f"未知设置项：{key}。可选：{', '.join(sorted(_DEFAULTS))}"))
        coerced = _coerce_value(key, value)
        if coerced is None:
            return Err(SdkError(f"设置项 {key} 的值无法解析：{value!r}"))
        # G2：会「结束进程」的配置项必须先做护栏预检，并可 dry_run 预览
        if key == "close_extra_processes":
            preview = await asyncio.to_thread(self._preview_extra, coerced)
            if preview["accepted"] == 0 and coerced:
                return Err(SdkError(
                    f"close_extra_processes 的值被安全护栏全部拒绝：{preview['rejected']}。"
                    "请使用具体的进程名（如 notepad.exe），不要用 * 之类的通配。"
                ))
            if _truthy(dry_run):
                return Ok({
                    "key": key,
                    "value": coerced,
                    "dry_run": True,
                    "preview": preview,
                    "message": f"预览：将结束 {preview['matched_count']} 个进程（未写入配置）",
                })
            changed = await self._set_option(key, coerced)
            return Ok({
                "key": key,
                "value": coerced,
                "changed": changed,
                "preview": preview,
                "message": f"{key} 已设为 {coerced}；本次匹配到 {preview['matched_count']} 个进程",
            })
        changed = await self._set_option(key, coerced)
        return Ok({"key": key, "value": coerced, "changed": changed,
                   "message": f"{key} 已设为 {coerced}"})

    @plugin_entry(
        id="diagnose",
        name="环境诊断",
        description="报告运行环境：进程枚举后端、识别到的 N.E.K.O 进程及分类、目标软件是否在运行。",
        llm_result_fields=["summary", "backend", "target_running"],
    )
    async def diagnose(self, **_):
        return Ok(await asyncio.to_thread(self._diagnose_sync))

    # ──────────────────────────────────────────────────────────────
    # 配置
    # ──────────────────────────────────────────────────────────────

    async def _reload_config(self) -> None:
        raw: Dict[str, Any] = {}
        try:
            dumped = await self.config.dump(timeout=5.0)
            if isinstance(dumped, dict):
                raw = dumped
        except Exception as exc:
            self.logger.warning("读取 plugin.toml 配置失败，使用默认值：%s", exc)

        section = raw.get("perf_saver") if isinstance(raw.get("perf_saver"), dict) else {}
        merged = dict(_DEFAULTS)
        toml_values: Dict[str, Any] = {}
        for key, value in section.items():
            if key in _DEFAULTS:
                coerced = _coerce_value(key, value)
                if coerced is not None:
                    merged[key] = coerced
                    toml_values[key] = coerced
        # G1：override 与 plugin.toml 相等时没有存在意义，顺手回收，避免「双源漂移」越积越多
        redundant = [k for k, v in (self._overrides or {}).items() if k in toml_values and toml_values[k] == v]
        for key in redundant:
            self._overrides.pop(key, None)
        if redundant:
            self._dropped_overrides = sorted(set(self._dropped_overrides) | set(redundant))
            self._save_json(_SETTINGS_FILE, self._overrides)
        for key, value in (self._overrides or {}).items():
            if key in _DEFAULTS:
                coerced = _coerce_value(key, value)
                if coerced is not None:
                    merged[key] = coerced
        with self._lock:
            self._cfg = merged
            self._toml_section = toml_values

    def _config_source(self, key: str) -> str:
        """该键当前生效值来自哪里：``override``（入口设过）或 ``toml``（配置文件）。"""
        return "override" if key in (self._overrides or {}) else "toml"

    async def _set_option(self, key: str, value: Any) -> bool:
        """写入一个开关。返回值表示「是否真的发生了变化」。

        幂等保证：值与当前生效值完全一致时**不写盘、不派生副作用**（G1 + 幂等 AC）。
        """
        with self._lock:
            if self._cfg.get(key) == value and self._overrides.get(key, _MISSING) == value:
                return False
            if key not in (self._overrides or {}) and self._toml_section.get(key, _MISSING) == value:
                # toml 里已经是这个值 ⇒ 不必新增 override（否则会重新制造 G1 的双源问题）
                self._cfg[key] = value
                return False
            self._cfg[key] = value
            self._overrides[key] = value
        self._save_json(_SETTINGS_FILE, self._overrides)
        self._last_check = 0.0  # 让下一轮巡检立刻重新判断
        if key == "saver_enabled":
            self._persist_session()  # 让 saver_session.json 与开关状态保持一致
        return True

    # ──────────────────────────────────────────────────────────────
    # 状态目录 / 落盘
    # ──────────────────────────────────────────────────────────────

    def _resolve_state_dir(self) -> Optional[Path]:
        for name in ("data_path", "cache_path"):
            func = getattr(self, name, None)
            if not callable(func):
                continue
            for args in ((), ("perf_saver",)):
                try:
                    base = func(*args)
                except Exception:
                    continue
                if not base:
                    continue
                try:
                    path = Path(str(base))
                    path.mkdir(parents=True, exist_ok=True)
                    return path
                except Exception:
                    continue
        self.logger.warning("未找到可写的插件数据目录，会话状态将只保存在内存中")
        return None

    def _load_json(self, filename: str) -> Optional[Dict[str, Any]]:
        if self._state_dir is None:
            return None
        path = self._state_dir / filename
        try:
            if not path.exists():
                return None
            with path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
            return data if isinstance(data, dict) else None
        except Exception as exc:
            self.logger.warning("读取 %s 失败：%s", filename, exc)
            return None

    def _save_json(self, filename: str, payload: Dict[str, Any]) -> None:
        if self._state_dir is None:
            return
        path = self._state_dir / filename
        tmp = path.with_suffix(path.suffix + ".tmp")
        try:
            with tmp.open("w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
            os.replace(str(tmp), str(path))
        except Exception as exc:
            self.logger.warning("写入 %s 失败：%s", filename, exc)

    def _persist_session(self) -> None:
        self._save_json(_SESSION_FILE, {
            "state": self._state,
            "active_since": self._active_since,
            "reason": self._last_reason,
            "suspended": self._suspended,
            "closed": self._closed,
            "priorities": self._priorities,
            # G1/AC：会话文件与开关状态保持一致，便于外部核对
            "saver_enabled": bool(self._cfg.get("saver_enabled")),
            # G3：一旦这份文件是「非 idle」状态被读到，就说明上一轮没走正常恢复
            "owner_pid": os.getpid(),
            "atexit_hooked": bool(self._atexit_hooked),
            "updated_at": time.time(),
        })

    def _clear_session_file(self) -> None:
        self._save_json(_SESSION_FILE, {
            "state": _STATE_IDLE,
            "active_since": 0.0,
            "reason": "",
            "suspended": [],
            "closed": [],
            "priorities": [],
            "saver_enabled": bool(self._cfg.get("saver_enabled")),
            "owner_pid": os.getpid(),
            "atexit_hooked": bool(self._atexit_hooked),
            "updated_at": time.time(),
        })

    # ──────────────────────────────────────────────────────────────
    # 进程枚举与分类
    # ──────────────────────────────────────────────────────────────

    def _enum(self, force: bool = False, max_age: float = 1.5) -> List[Proc]:
        now = time.time()
        if not force and self._enum_cache and (now - self._enum_cache_at) < max_age:
            return self._enum_cache
        procs = _enum_with_psutil() if psutil is not None else []
        if not procs:
            procs = _enum_with_powershell()  # psutil 缺失/异常时退化
        self._enum_cache = procs
        self._enum_cache_at = now
        return procs

    def _neko_names(self) -> List[str]:
        return [str(x) for x in (self._cfg.get("neko_process_names") or [])]

    def _classify(self, proc: Proc) -> str:
        """把进程分类；返回 "" 表示不是 N.E.K.O 进程。"""
        if not _match_any(proc.name, self._neko_names()):
            return ""
        name_low = (proc.name or "").lower()
        cmd = proc.cmd
        if "projectneko_server" in name_low:
            return "backend_worker" if "--multiprocessing-fork" in cmd else "backend_main"
        if "--type=renderer" in cmd:
            return "renderer"
        if "--type=gpu-process" in cmd:
            return "gpu"
        if "--type=utility" in cmd:
            match = re.search(r"--utility-sub-type=([\w.\-]+)", cmd)
            subtype = match.group(1) if match else "utility"
            return f"utility:{subtype}"
        if "--type=" in cmd:
            return "other_child"
        return "main"

    def _neko_procs(self, procs: Optional[List[Proc]] = None) -> List[tuple]:
        procs = procs if procs is not None else self._enum()
        result = []
        for proc in procs:
            kind = self._classify(proc)
            if kind:
                result.append((proc, kind))
        return result

    def _target_running(self, procs: Optional[List[Proc]] = None) -> bool:
        patterns = [str(x) for x in (self._cfg.get("target_processes") or []) if str(x).strip()]
        if not patterns:
            return False
        procs = procs if procs is not None else self._enum()
        require_window = bool(self._cfg.get("require_visible_window"))
        visible = pids_with_visible_window() if require_window else None
        for proc in procs:
            if not proc.name or proc.pid == os.getpid():
                continue
            if _is_neko_name(proc.name, self._neko_names()):
                continue  # 绝不自触发
            if not _match_any(proc.name, patterns):
                continue
            if visible is not None and proc.pid not in visible:
                continue
            return True
        return False

    def _is_protected(self, proc: Proc) -> bool:
        """保护名单：自己、自己的祖先链、目标软件。"""
        if proc.pid == os.getpid():
            return True
        if _match_any(proc.name, [str(x) for x in (self._cfg.get("target_processes") or [])]):
            return True
        return False

    # ──────────────────────────────────────────────────────────────
    # 巡检主逻辑
    # ──────────────────────────────────────────────────────────────

    def _foreground_guard(self, procs: Optional[List[Proc]] = None) -> bool:
        """N.E.K.O 是否正在前台（带独立节流，不受 maintenance_interval_seconds 拖累，G7）。

        返回 True 表示「应当保持恢复状态、不要降负载」。
        """
        if not bool(self._cfg.get("skip_while_neko_foreground")):
            return False
        now = time.time()
        if self._fg_state is not None and (now - self._last_fg_check) < _FOREGROUND_CHECK_SECONDS:
            return self._fg_state
        self._last_fg_check = now
        fg = foreground_pid()
        neko_pids = {proc.pid for proc, _ in self._neko_procs(procs)}
        self._fg_state = bool(fg and fg in neko_pids)
        return self._fg_state

    def _tick(self) -> None:
        if self._busy:
            return
        self._busy = True
        try:
            now = time.time()
            enabled = bool(self._cfg.get("saver_enabled"))
            interval = max(1.0, _safe_float(self._cfg.get("watch_interval_seconds"), 4.0))
            if not enabled:
                interval *= _DISABLED_INTERVAL_FACTOR  # G11：关掉守护后降频，不空转
            if now - self._last_check < interval:
                return
            self._last_check = now

            # G11：关掉守护时不枚举进程、不做任何降负载动作；只在确实留下痕迹时收尾恢复
            if not enabled:
                self._target_seen = False
                if self._state != _STATE_IDLE or self._suspended or self._priorities:
                    self._restore_sync("saver_disabled", True)
                return

            procs = self._enum(force=True)
            self._target_seen = self._target_running(procs)

            # G7：前台保护前置到每轮巡检（1s 节流），不再等一个 maintenance 周期
            if self._state != _STATE_IDLE:
                if self._foreground_guard(procs):
                    if not self._soft_paused:
                        self._resume_only("neko_foreground")
                    return
                if self._soft_paused:
                    self._apply_sync("neko_background")

            if self._target_seen:
                if self._state == _STATE_IDLE:
                    self._apply_sync("target_running")
                elif self._state == _STATE_PAUSED:
                    self._apply_sync("target_running_resume")
                else:
                    self._maintenance()
            else:
                if self._state != _STATE_IDLE and bool(self._cfg.get("restore_on_target_exit")):
                    self._restore_sync("target_exited", True)
        finally:
            self._busy = False

    def _maintenance(self) -> None:
        """已优化状态下的周期性维护：新出现的渲染进程也挂起、定期回收内存。"""
        now = time.time()
        max_age = max(5.0, _safe_float(self._cfg.get("maintenance_interval_seconds"), 20.0))
        if now - self._last_maintenance < max_age:
            return
        self._last_maintenance = now

        # G8：补挂的 kind 集合与首次 _apply_sync 用同一份 _freeze_kinds()，不再硬编码 renderer
        freeze_kinds = self._freeze_kinds()
        # G10：只把「仍处于挂起状态」的记录算作已知，已恢复的进程必须能被重新挂起
        known = {rec["pid"] for rec in self._suspended if not rec.get("resumed")}
        for proc, kind in self._neko_procs():
            if proc.pid in known or self._is_protected(proc):
                continue
            if not self._kind_frozen(kind, freeze_kinds):
                continue
            if proc_suspend(proc.pid):
                self._record_suspended(proc, kind)
        # G9：走 _trim(force=False)，让 trim_interval_seconds 真正生效
        self._trim(force=False)

    # ──────────────────────────────────────────────────────────────
    # 优化 / 恢复
    # ──────────────────────────────────────────────────────────────

    def _freeze_kinds(self) -> set:
        """本次配置下要挂起的进程种类集合（G8：_apply_sync 与 _maintenance 共用一份）。"""
        kinds = set()
        if bool(self._cfg.get("freeze_renderers")):
            kinds.add("renderer")
        if bool(self._cfg.get("freeze_gpu")):
            kinds.add("gpu")
        if bool(self._cfg.get("freeze_utility_services")):
            kinds.add("utility")
        return kinds

    @staticmethod
    def _kind_frozen(kind: str, freeze_kinds: set) -> bool:
        if kind.startswith("utility:"):
            return "utility" in freeze_kinds
        return kind in freeze_kinds

    def _extra_candidates(self, procs: List[Proc], extras: List[str]) -> Dict[str, Any]:
        """G2：`close_extra_processes` 的三重护栏 + 候选进程筛选（纯计算，不杀任何进程）。

        护栏：① 目标软件（含所有 target_processes）永不碰；② 系统关键进程黑名单；
        ③ 过宽的通配模式（`*` / `?`）拒绝；④ 单次最多结束 `_MAX_EXTRA_KILLS` 个。
        """
        patterns = [str(x).strip() for x in (extras or []) if str(x).strip()]
        accepted: List[str] = []
        rejected: List[str] = []
        for pat in patterns:
            reason = _pattern_refusal("", pat)
            if reason:
                rejected.append(pat)
            else:
                accepted.append(pat)

        targets = [str(x) for x in (self._cfg.get("target_processes") or []) if str(x).strip()]
        neko_names = self._neko_names()
        candidates: List[Proc] = []
        skipped: List[Dict[str, Any]] = []
        for proc in procs:
            if not proc.name or not accepted:
                continue
            if not _match_any(proc.name, accepted):
                continue
            reason = ""
            if proc.pid == os.getpid():
                reason = "插件自身"
            elif _is_neko_name(proc.name, neko_names):
                reason = "N.E.K.O 自身"
            elif _match_any(proc.name, targets):
                reason = "目标软件（永不触碰）"
            elif _is_system_protected(proc.name):
                reason = "系统关键进程"
            elif proc.pid <= 4:
                reason = "系统进程"
            if reason:
                skipped.append({"pid": proc.pid, "name": proc.name, "skipped": reason})
                continue
            candidates.append(proc)
        # 顺序稳定：pid 小的先来，保证「截断」行为可预期
        candidates.sort(key=lambda p: p.pid)
        truncated = candidates[_MAX_EXTRA_KILLS:]
        candidates = candidates[:_MAX_EXTRA_KILLS]
        return {
            "patterns": patterns,
            "accepted": len(accepted),
            "rejected": rejected,
            "matches": [{"pid": p.pid, "name": p.name} for p in candidates],
            "matched_count": len(candidates),
            "skipped": skipped,
            "truncated": [{"pid": p.pid, "name": p.name} for p in truncated],
            "max_per_apply": _MAX_EXTRA_KILLS,
            "procs": candidates,
        }

    def _preview_extra(self, extras: List[str]) -> Dict[str, Any]:
        """供入口 dry_run / 设置回显用的预览（不含 procs 本体，便于 JSON 序列化）。"""
        info = self._extra_candidates(self._enum(force=True), extras)
        return {k: v for k, v in info.items() if k != "procs"}

    def _apply_sync(self, reason: str) -> Dict[str, Any]:
        try:
            return self._apply_locked(reason)
        except Exception as exc:
            # G12：异常路径也要落盘，避免「已挂起但没记录」导致恢复无据可依
            self._last_error = f"{type(exc).__name__}: {exc}"
            try:
                self._persist_session()
                self.logger.warning("降负载过程中异常，已落盘会话：%s", self._last_error)
            except Exception:
                pass
            raise

    def _apply_locked(self, reason: str) -> Dict[str, Any]:
        with self._lock:
            if self._state == _STATE_APPLIED and not self._soft_paused:
                return {"applied": False, "reason": reason, "message": "已处于优化状态"}
            if self._soft_paused:
                # 从 PAUSED 回到 APPLIED：先丢掉「已恢复」的历史记录，否则不会被重新挂起（G10）
                self._prune_session_records()
            result: Dict[str, Any] = {
                "applied": True,
                "reason": reason,
                "suspended": [],
                "closed": [],
                "trimmed": [],
                "priority": [],
                "errors": [],
            }
            procs = self._enum(force=True)

            # 1) 挂起渲染/GPU/工具进程 —— 停止界面渲染
            freeze_kinds = self._freeze_kinds()
            # G10：同一 pid 已有未恢复的记录时不再重复挂起（重复 NtSuspendProcess 需要等量 resume）
            already = {rec["pid"] for rec in self._suspended if not rec.get("resumed")}

            for proc, kind in self._neko_procs(procs):
                if self._is_protected(proc):
                    continue
                if proc.pid in already:
                    continue
                if not self._kind_frozen(kind, freeze_kinds):
                    continue
                if proc_suspend(proc.pid):
                    self._record_suspended(proc, kind)
                    result["suspended"].append(proc.as_dict())
                else:
                    result["errors"].append(f"suspend {proc.name}#{proc.pid} 失败")

            # 2) 结束可关闭的进程（Chromium 会按需重建）
            close_rules = []
            if bool(self._cfg.get("close_video_capture_service")):
                close_rules.append("video_capture")
            if bool(self._cfg.get("close_audio_service")):
                close_rules.append("audio.")
            if bool(self._cfg.get("close_network_service")):
                close_rules.append("network.")
            if close_rules:
                for proc, kind in self._neko_procs(procs):
                    if not kind.startswith("utility:"):
                        continue
                    subtype = kind.split(":", 1)[1].lower()
                    if not any(rule in subtype for rule in close_rules):
                        continue
                    if proc.pid in {rec["pid"] for rec in self._suspended if not rec.get("resumed")}:
                        continue
                    if proc_terminate(proc.pid):
                        rec = proc.as_dict()
                        rec.update({"kind": kind, "respawnable": True})
                        self._closed.append(rec)
                        result["closed"].append(rec)
                        self.logger.info("已结束可重建进程 %s#%s (%s)", proc.name, proc.pid, subtype)

            # 3) 额外进程（用户自行配置）—— 必须过安全护栏（G2）
            extras = [str(x) for x in (self._cfg.get("close_extra_processes") or []) if str(x).strip()]
            if extras:
                plan = self._extra_candidates(procs, extras)
                if plan["rejected"]:
                    result["errors"].append(f"已拒绝过宽/非法模式：{plan['rejected']}")
                    self.logger.warning("close_extra_processes 中以下模式被安全护栏拒绝：%s", plan["rejected"])
                if plan["truncated"]:
                    result["errors"].append(
                        f"候选进程超过单次上限 {_MAX_EXTRA_KILLS}，本次只结束前 {_MAX_EXTRA_KILLS} 个"
                    )
                if plan["skipped"]:
                    self.logger.info("close_extra_processes 跳过受保护进程：%s", plan["skipped"])
                for proc in plan["procs"]:
                    try:
                        cwd = None
                        if psutil is not None:
                            try:
                                cwd = psutil.Process(proc.pid).cwd()  # type: ignore[union-attr]
                            except Exception:
                                cwd = None
                        if _identity_ok({"pid": proc.pid, "create_time": round(proc.create_time, 3)}) and \
                                proc_terminate(proc.pid):
                            rec = proc.as_dict()
                            rec.update({
                                "kind": "extra",
                                "respawnable": False,
                                "cmdline": list(proc.cmdline),
                                "exe": proc.exe,
                                "cwd": cwd,
                            })
                            self._closed.append(rec)
                            result["closed"].append(rec)
                            self.logger.info("已结束额外进程 %s#%s（用户配置）", proc.name, proc.pid)
                    except Exception as exc:
                        result["errors"].append(f"close {proc.name}#{proc.pid} 失败: {exc}")

            # 4) 回收内存 + 降低优先级
            if bool(self._cfg.get("trim_working_set")):
                result["trimmed"] = self._trim(force=True)
            if bool(self._cfg.get("lower_priority")):
                # G13：后端是否降优先级由 lower_priority_backend 独立控制，
                # 不再借用 trim_backend_processes（两者语义无关）
                lower_backend = bool(self._cfg.get("lower_priority_backend"))
                already_prio = {rec["pid"] for rec in self._priorities if not rec.get("restored")}
                for proc, kind in self._neko_procs(procs):
                    if proc.pid in already_prio:
                        continue
                    if kind in ("backend_main", "backend_worker") and not lower_backend:
                        continue
                    old = proc_priority(proc.pid)
                    if old is None or old == _IDLE_PRIORITY_CLASS:
                        continue
                    if proc_set_priority(proc.pid, _IDLE_PRIORITY_CLASS):
                        self._priorities.append({
                            "pid": proc.pid,
                            "name": proc.name,
                            "create_time": round(proc.create_time, 3),
                            "old": old,
                        })
                        result["priority"].append(proc.as_dict())

            self._state = _STATE_APPLIED
            self._active_since = time.time()
            self._last_reason = reason
            self._last_maintenance = time.time()
            self._soft_paused = False
            self._persist_session()
            summary = (
                f"已降负载：挂起 {len(result['suspended'])} 个渲染/GPU 进程，"
                f"结束 {len(result['closed'])} 个可重建进程，"
                f"回收 {len(result['trimmed'])} 个进程工作集"
            )
            result["summary"] = summary
            self.logger.info("%s (%s)", summary, reason)
            self._report({"state": self._state, "summary": summary})
            return result

    def _prune_session_records(self) -> None:
        """G10：把「已经恢复/已经还原」的记录从会话里剔除，避免列表无界增长、计数失真。"""
        self._suspended = [rec for rec in self._suspended if not rec.get("resumed")]
        self._priorities = [rec for rec in self._priorities if not rec.get("restored")]

    def _resume_only(self, reason: str) -> Dict[str, Any]:
        """只恢复被挂起的进程，用于「N.E.K.O 回到前台」等临时场景。"""
        with self._lock:
            # 复用 _resume_suspended()：身份校验（G5）+ 恢复后校验/重试只有一处实现，
            # 避免两个恢复路径各自演化（这条路径曾经只发一次 NtResumeProcess）。
            already = {id(rec) for rec in self._suspended if rec.get("resumed")}
            resumed, failed, skipped = self._resume_suspended()
            for rec in self._suspended:
                if rec.get("resumed") and id(rec) not in already:
                    rec["resumed_reason"] = reason
            if failed:
                self.logger.warning("临时恢复（%s）有 %s 项未成功：%s", reason, len(failed), failed)
            for rec in self._priorities:
                if rec.get("restored"):
                    continue
                pid = int(rec.get("pid") or 0)
                if not pid or not _identity_ok(rec):
                    rec["restored"] = True
                    continue
                if proc_set_priority(pid, int(rec.get("old") or _NORMAL_PRIORITY_CLASS)):
                    rec["restored"] = True
                    rec["restored_reason"] = reason
            self._soft_paused = True
            self._last_reason = reason
            self._prune_session_records()  # G10：恢复完就不必再留着记录
            self._persist_session()
            self.logger.info("临时恢复（%s）：已恢复 %s 个挂起进程（跳过 %s）", reason, resumed, skipped)
            return {"resumed": resumed, "skipped": skipped, "reason": reason, "failed": failed}

    def _resume_suspended(self) -> tuple:
        """恢复 self._suspended 里的进程。

        返回 ``(成功数, 失败描述列表, 跳过的重复 PID 数)``。
        每一条恢复都是「调用 → 校验 → 必要时重试」（见 ``_RESUME_ATTEMPTS``）：
        ``proc_resume`` 返回成功 != 挂起计数归零，校验不通过就保留记录并报错。
        """
        resumed, failed, skipped = 0, [], 0
        for rec in self._suspended:
            pid = int(rec.get("pid") or 0)
            if not pid:
                continue
            if rec.get("resumed"):
                continue
            if not _identity_ok(rec):
                # G5：PID 已被复用 ⇒ 绝不能对无关进程调 NtResumeProcess
                rec["resumed"] = True
                rec["skipped"] = "pid_reused"
                skipped += 1
                self.logger.warning(
                    "跳过 pid %s（%s）：create_time 与记录不符，PID 已被复用",
                    pid, rec.get("name"),
                )
                continue
            # NtSuspendProcess 是**计数式**的：挂起计数 > 1 时（调试器附着、别人做过
            # toolhelp 快照……），单次 NtResumeProcess 之后进程依然是冻结的。所以这里
            # 「调用 → 校验 → 必要时重试」，失败则保留记录并报错，绝不谎报 resumed。
            recovered = False
            attempts = 0
            for attempts in range(1, _RESUME_ATTEMPTS + 1):
                proc_resume(pid)
                if not _still_suspended(pid):
                    recovered = True
                    break
                time.sleep(_RESUME_RETRY_DELAY)
            if recovered:
                rec.pop("failed", None)
                rec["resumed"] = True
                rec["resume_attempts"] = attempts
                resumed += 1
            else:
                alive = _pid_alive(pid)
                if alive:
                    rec["failed"] = "still_suspended"
                    failed.append(
                        f"resume {rec.get('name')}#{pid} 失败：重试 {attempts} 次后仍处挂起状态"
                    )
                    self.logger.error(
                        "恢复失败：%s#%s 仍处于挂起状态（已重试 %s 次），记录保留待下次救援",
                        rec.get("name"), pid, attempts,
                    )
                else:
                    rec["resumed"] = True  # 进程已自行退出
        return resumed, failed, skipped

    def _restore_sync(self, reason: str, clear: bool = True) -> Dict[str, Any]:
        with self._lock:
            result: Dict[str, Any] = {
                "restored": False,
                "reason": reason,
                "resumed": 0,
                "relaunched": [],
                "errors": [],
            }
            # G12：早退条件必须带上 _priorities，否则「只改了优先级」的中间态永不还原
            if (self._state == _STATE_IDLE and not self._suspended
                    and not self._closed and not self._priorities):
                result["message"] = "当前没有需要恢复的进程"
                return result

            resumed, failed, skipped = self._resume_suspended()
            result["resumed"] = resumed
            result["skipped_pid_reused"] = skipped
            result["errors"].extend(failed)

            # 优先级还原（G5：先校验 PID 没被复用）
            for rec in self._priorities:
                if rec.get("restored"):
                    continue
                pid = int(rec.get("pid") or 0)
                if not pid:
                    continue
                if not _identity_ok(rec):
                    rec["restored"] = True
                    rec["skipped"] = "pid_reused"
                    self.logger.warning("跳过优先级还原 pid %s：PID 已被复用", pid)
                    continue
                if proc_set_priority(pid, int(rec.get("old") or _NORMAL_PRIORITY_CLASS)):
                    rec["restored"] = True
                else:
                    alive = _pid_alive(pid)
                    if alive:
                        result["errors"].append(f"还原优先级 {rec.get('name')}#{pid} 失败")
                    else:
                        rec["restored"] = True  # 进程已退出，优先级随进程消失

            # 非可重建进程：尽力用原命令行重启
            for rec in self._closed:
                if rec.get("respawnable") or rec.get("relaunched"):
                    continue
                relaunched = _relaunch(rec)
                if relaunched:
                    rec["relaunched"] = True
                    result["relaunched"].append(rec.get("name"))
                else:
                    result["errors"].append(
                        f"无法自动重启 {rec.get('name')}（请手动打开或重启 N.E.K.O）"
                    )

            result["restored"] = True
            result["detail"] = {
                "suspended": len(self._suspended),
                "closed": len(self._closed),
                "closed_respawnable": sum(1 for r in self._closed if r.get("respawnable")),
            }
            result["summary"] = (
                f"已恢复 {resumed} 个挂起进程；结束的 {len(self._closed)} 个进程由 N.E.K.O 按需重建"
            )

            # G4：只有在「安全关键项全部处理干净」时才清空会话文件；
            # 有没解决的记录就留着，让下一次启动的救援（_rescue_previous_session）继续兜底，
            # 而不是静默丢弃。重启失败的额外进程也保留（下次启动再试一次），
            # 但它不影响内存状态回到 idle（否则 _tick 会每轮重复尝试重启）。
            leftover = [rec for rec in self._suspended if not rec.get("resumed")] + \
                       [rec for rec in self._priorities if not rec.get("restored")]
            failed_relaunch = [rec for rec in self._closed
                               if not rec.get("respawnable") and not rec.get("relaunched")]
            if clear and not leftover:
                self._state = _STATE_IDLE
                self._active_since = 0.0
                self._soft_paused = False
                self._suspended = []
                self._priorities = []
                self._closed = failed_relaunch
                if failed_relaunch:
                    result["pending_relaunch"] = [rec.get("name") for rec in failed_relaunch]
                    self._persist_session()
                else:
                    self._clear_session_file()
            elif clear and leftover:
                self._state = _STATE_APPLIED
                result["pending"] = len(leftover)
                result["errors"].append(
                    f"有 {len(leftover)} 项未能恢复，已写入会话文件，下次启动会继续尝试"
                )
                self._persist_session()
            self._last_reason = reason
            self.logger.info("恢复完成（%s）：%s", reason, json.dumps(result, ensure_ascii=False))
            self._report({"state": self._state, "summary": result["summary"]})
            return result

    def _emergency_restore(self) -> None:
        """解释器正常退出时的兜底（atexit）。

        ⚠️ 只在 Python 正常退出时执行：Windows 上「结束任务」/ 宿主强杀走
        `TerminateProcess`，**不会**触发 atexit。强杀场景唯一的兜底是下次
        `on_startup` → `_rescue_previous_session()`（见 README/DESIGN 的说明）。
        """
        try:
            if self._suspended or self._priorities:
                resumed, failed, skipped = self._resume_suspended()
                restored = 0
                for rec in self._priorities:
                    if rec.get("restored") or not _identity_ok(rec):
                        continue
                    if proc_set_priority(int(rec["pid"]), int(rec.get("old") or _NORMAL_PRIORITY_CLASS)):
                        rec["restored"] = True
                        restored += 1
                self._prune_session_records()
                self._persist_session()
                self.logger.warning(
                    "atexit 兜底恢复：resumed=%s, priority_restored=%s, failed=%s, skipped=%s",
                    resumed, restored, failed, skipped,
                )
        except Exception:
            pass

    def _rescue_previous_session(self) -> None:
        """启动时救援上一轮遗留的挂起进程（插件/N.E.K.O 崩溃过的情况）。

        G4 修复：读全部三类记录（suspended / priorities / closed），复用 `_resume_suspended()`
        与 `_identity_ok()`；失败项**不静默丢弃**（保留记录 + warning），只有全部处理干净才
        清空会话文件。
        """
        data = self._load_json(_SESSION_FILE)
        if not data:
            return
        suspended = [r for r in (data.get("suspended") or []) if isinstance(r, dict)]
        priorities = [r for r in (data.get("priorities") or []) if isinstance(r, dict)]
        closed = [r for r in (data.get("closed") or []) if isinstance(r, dict)]
        if not suspended and not priorities and not closed:
            return

        self._suspended = suspended
        self._priorities = priorities
        self._closed = closed
        # 只有「挂起/优先级」这类安全关键项才把状态标成 applied；
        # 仅剩重启失败的额外进程时保持 idle，避免 _tick 每轮重复尝试重启。
        self._state = _STATE_APPLIED if (suspended or priorities) else _STATE_IDLE

        resumed, failed, skipped = self._resume_suspended()
        restored = 0
        for rec in self._priorities:
            if rec.get("restored"):
                continue
            pid = int(rec.get("pid") or 0)
            if not pid:
                continue
            if not _identity_ok(rec):
                rec["restored"] = True
                rec["skipped"] = "pid_reused"
                skipped += 1
                continue
            if proc_set_priority(pid, int(rec.get("old") or _NORMAL_PRIORITY_CLASS)):
                rec["restored"] = True
                restored += 1
            elif not _pid_alive(pid):
                rec["restored"] = True

        # G4(a)：被结束且「不可重建」的额外进程，崩溃后也要拉回来
        relaunched: List[str] = []
        for rec in self._closed:
            if rec.get("respawnable") or rec.get("relaunched"):
                continue
            if _relaunch(rec):
                rec["relaunched"] = True
                relaunched.append(rec.get("name") or "?")
            else:
                failed.append(f"无法自动重启 {rec.get('name')}（请手动打开或重启 N.E.K.O）")

        leftover = [r for r in self._suspended if not r.get("resumed")] + \
                   [r for r in self._priorities if not r.get("restored")]
        orphan_closed = [r for r in self._closed
                         if not r.get("respawnable") and not r.get("relaunched")]
        self.logger.warning(
            "检测到上次未恢复的会话：resumed=%s, priority_restored=%s, relaunched=%s, "
            "pid_reused_skipped=%s, failed=%s, leftover=%s, orphan_closed=%s",
            resumed, restored, relaunched, skipped, failed, len(leftover),
            [r.get("name") for r in orphan_closed],
        )
        if leftover:
            # G4(b)：安全关键项没处理干净 ⇒ 留着记录 + 保持 applied，下次继续尝试
            self._persist_session()
        else:
            # 挂起/优先级都干净了 ⇒ 内存回 idle；重启失败的额外进程记录保留（下次启动再试）
            self._state = _STATE_IDLE
            self._suspended = []
            self._priorities = []
            self._closed = orphan_closed
            if orphan_closed:
                self._persist_session()
            else:
                self._clear_session_file()

    # ──────────────────────────────────────────────────────────────
    # 工具方法
    # ──────────────────────────────────────────────────────────────

    def _record_suspended(self, proc: Proc, kind: str) -> None:
        # G10：同一 pid 已有未恢复的记录就不再追加（否则 status 计数虚高、文件无限膨胀）
        for rec in self._suspended:
            if rec.get("pid") == proc.pid and not rec.get("resumed"):
                return
        self._suspended.append({
            "pid": proc.pid,
            "name": proc.name,
            "kind": kind,
            "create_time": round(proc.create_time, 3),
            "rss_mb": round(proc.rss / 1048576.0, 1),
            "resumed": False,
        })

    def _trim(self, force: bool = False) -> List[Dict[str, Any]]:
        now = time.time()
        interval = max(10.0, _safe_float(self._cfg.get("trim_interval_seconds"), 60.0))
        if not force and now - self._last_trim < interval:
            return []
        self._last_trim = now
        trimmed: List[Dict[str, Any]] = []
        with_backend = bool(self._cfg.get("trim_backend_processes"))
        for proc, kind in self._neko_procs():
            if kind.startswith("backend") and not with_backend:
                continue
            if proc_trim_working_set(proc.pid):
                trimmed.append(proc.as_dict())
        return trimmed

    def _status_sync(self) -> Dict[str, Any]:
        procs = self._enum(force=True)
        neko = self._neko_procs(procs)
        target_running = self._target_running(procs)
        state_text = {
            _STATE_APPLIED: "已优化（正在降低 N.E.K.O 占用）",
            _STATE_PAUSED: "已优化但临时恢复（N.E.K.O 在前台）",
            _STATE_IDLE: "待命（未降负载）",
        }.get(self._state, self._state)
        # 前台临时恢复是用 soft_paused 标记表达的（不是一个独立状态），这里同步反映出来
        if self._soft_paused and self._state == _STATE_APPLIED:
            state_text = "已优化但临时恢复（N.E.K.O 在前台）"
        summary = f"{state_text}；触发软件：{', '.join(self._cfg.get('target_processes') or []) or '未设置'}"
        if self._state == _STATE_APPLIED and not self._soft_paused:
            summary += f"；已挂起 {len(self._suspended)} 个进程"
        if not bool(self._cfg.get("saver_enabled")):
            summary += "；自动守护已关闭"
        return {
            "summary": summary,
            "state": self._state,
            "state_text": state_text,
            "saver_enabled": bool(self._cfg.get("saver_enabled")),
            "soft_paused": bool(self._soft_paused),
            "target_processes": list(self._cfg.get("target_processes") or []),
            "target_running": target_running,
            "active_since": self._active_since,
            "last_reason": self._last_reason,
            "last_error": self._last_error,
            # US-004：面板的「最近一次操作摘要」；空 dict 表示本次启动以来还没执行过操作
            "last_action": dict(self._last_action),
            "uptime_seconds": round(time.time() - self._started_at, 1),
            "backend": "psutil" if psutil else "powershell",
            # G3：atexit 只覆盖正常退出；强杀场景靠下次启动的救援，这里如实暴露状态
            "atexit_hooked": bool(self._atexit_hooked),
            "crash_recovery": "next_startup_rescue_only",
            "suspended": [{k: v for k, v in rec.items() if k != "resumed"} for rec in self._suspended],
            "closed": self._closed,
            "priorities": self._priorities,
            "neko_processes": [
                {"pid": p.pid, "name": p.name, "kind": kind, "rss_mb": round(p.rss / 1048576.0, 1)}
                for p, kind in neko
            ],
            "options": {k: self._cfg.get(k) for k in sorted(_DEFAULTS)},
            # G1：每个键的生效值来自 plugin.toml 还是入口覆盖，一眼可见
            "options_source": {k: self._config_source(k) for k in sorted(_DEFAULTS)},
            "overrides": dict(self._overrides or {}),
            "overrides_dropped": list(self._dropped_overrides),
            # US-001：使用前免责声明状态（只影响「是否提示」，不影响降负载行为）
            "disclaimer_acknowledged": bool(self._cfg.get("disclaimer_acknowledged")),
            "disclaimer_required": not bool(self._cfg.get("disclaimer_acknowledged")),
        }

    def _diagnose_sync(self) -> Dict[str, Any]:
        procs = self._enum(force=True)
        neko = self._neko_procs(procs)
        running = self._target_running(procs)
        return {
            "summary": (
                f"后端 {'psutil' if psutil else 'PowerShell'}；"
                f"识别到 {len(neko)} 个 N.E.K.O 进程；"
                f"触发软件{'正在运行' if running else '未运行'}"
            ),
            "backend": "psutil" if psutil else "powershell",
            "windows": _IS_WINDOWS,
            "state_dir": str(self._state_dir) if self._state_dir else None,
            "target_running": running,
            "target_processes": list(self._cfg.get("target_processes") or []),
            "neko_processes": [
                {"pid": p.pid, "name": p.name, "kind": kind, "rss_mb": round(p.rss / 1048576.0, 1),
                 "cmdline_hint": p.cmd[:160]}
                for p, kind in neko
            ],
            "capabilities": {
                "suspend": _IS_WINDOWS,
                "empty_working_set": _IS_WINDOWS,
                "foreground_detect": _IS_WINDOWS,
            },
            # G6：如实说明「GPU 占用」目前的手段与取证方式（测量脚本见 US-003 修复验证）
            "gpu": {
                "freeze_gpu": bool(self._cfg.get("freeze_gpu")),
                "freeze_kinds": sorted(self._freeze_kinds()),
                "gpu_processes": [
                    {"pid": p.pid, "name": p.name, "rss_mb": round(p.rss / 1048576.0, 1)}
                    for p, kind in neko if kind == "gpu"
                ],
                "note": (
                    "默认只挂起渲染进程（停止渲染提交 ⇒ GPU 占用随之为 0），"
                    "不直接挂起 GPU 进程；需要更强效果可开 freeze_gpu。"
                    "GPU 占用的量化取证请用 work/tools/gpu_probe.py。"
                ),
            },
        }

    def _report(self, payload: Dict[str, Any]) -> None:
        try:
            outcome = self.report_status(payload)
            if asyncio.iscoroutine(outcome):
                # 某些版本是异步接口；在同步上下文里就用后台线程收尾
                threading.Thread(
                    target=lambda: asyncio.run(outcome),  # type: ignore[arg-type]
                    daemon=True,
                ).start()
        except Exception:
            pass


# ──────────────────────────────────────────────────────────────────────
# 模块级工具
# ──────────────────────────────────────────────────────────────────────


def _is_neko_name(name: str, neko_names: List[str]) -> bool:
    return bool(name) and _match_any(name, neko_names)


def _still_suspended(pid: int) -> bool:
    """进程当前是否仍处于挂起（冻结）状态。

    用于「恢复之后校验」：``proc_resume`` 返回 0 只代表 NtResumeProcess 调用成功，
    不代表挂起计数已经归零。psutil 不可用时保守返回 False（不做额外重试）。
    """
    if psutil is None:
        return False
    try:
        return psutil.Process(pid).status() == psutil.STATUS_STOPPED
    except Exception:
        return False


def _pid_alive(pid: int) -> bool:
    if psutil is not None:
        try:
            return psutil.pid_exists(pid)  # type: ignore[union-attr]
        except Exception:
            pass
    handle = _open_process(pid, _PROCESS_QUERY_LIMITED)
    if handle:
        _close_handle(handle)
        return True
    return False


def _pid_create_time(pid: int) -> Optional[float]:
    """进程创建时间（秒）；无法获取时返回 None（表示「未知」，调用方自行决定）。

    G5：psutil 优先；**psutil 不可用时退回 PowerShell**（`Get-CimInstance` 的
    `CreationDate`，与 `_enum_with_powershell()` 同一数据源），这样「记录 create_time」
    与「校验 create_time」两条路径的口径始终一致，不会出现「只有 psutil 后端才做
    PID 复用校验」的漏洞。
    """
    if not pid or pid <= 0:
        return None
    if psutil is not None:
        try:
            return float(psutil.Process(pid).create_time())  # type: ignore[union-attr]
        except Exception:
            pass
    if not _IS_WINDOWS:
        return None
    try:
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             f"(Get-CimInstance Win32_Process -Filter \"ProcessId={int(pid)}\")"
             ".CreationDate | ConvertTo-Json -Compress"],
            capture_output=True,
            timeout=15,
            creationflags=_CREATE_NO_WINDOW,
        )
        text = (completed.stdout or b"").decode("utf-8", "replace").strip()
    except Exception:
        return None
    value = _ps_creation_time(text)
    return value or None


def _identity_ok(rec: Dict[str, Any]) -> bool:
    """PID 复用校验（G5）：记录里的 create_time 与当前进程创建时间比对。

    返回 True 表示「可以安全操作」：创建时间一致，或无法判定（缺记录 / 拿不到当前值）。
    返回 False 表示 PID 已被复用，**必须跳过**。
    """
    pid = int(rec.get("pid") or 0)
    if not pid:
        return False
    recorded = rec.get("create_time")
    if not recorded:
        return True  # 老记录没有 create_time，退化为只校验 pid 存活
    current = _pid_create_time(pid)
    if current is None:
        return True  # 拿不到当前值（非 psutil 后端）⇒ 不阻塞恢复
    return abs(float(current) - float(recorded)) <= _PID_CREATE_TIME_TOLERANCE


def _pattern_refusal(name: str, pattern: str) -> str:
    """返回拒绝原因；空串表示该模式可用。

    规则：不含通配符的进程名一律允许（`_match_any` 本身支持后缀匹配）；
    含通配符时，**第一个通配符之前**必须至少有 ``_MIN_WILDCARD_LITERAL`` 个字符，
    否则 `*`、`*.exe`、`?` 这类「等价于所有进程」的模式会被拒绝。
    """
    pat = str(pattern or "").strip()
    if not pat:
        return "空模式"
    low = pat.lower()
    if "*" not in low and "?" not in low:
        return ""
    prefix = re.split(r"[*?]", low, maxsplit=1)[0]
    if len(prefix) < _MIN_WILDCARD_LITERAL:
        return (
            f"通配模式 {pat!r} 过于宽泛（第一个通配符前至少要有 "
            f"{_MIN_WILDCARD_LITERAL} 个字符，例如 extra*.exe）"
        )
    return ""


def _is_system_protected(name: str) -> bool:
    """系统关键进程 / 同名伪装：命中即拒绝结束（G2 护栏之一）。"""
    if not name:
        return True
    return name.lower() in _SYSTEM_PROTECTED


def _relaunch(rec: Dict[str, Any]) -> bool:
    cmdline = rec.get("cmdline") or []
    if not cmdline:
        return False
    argv = [str(x) for x in cmdline]
    first = argv[0]
    if os.sep in first and not os.path.exists(first):
        return False  # 程序已经不在原位置了，不要盲目 CreateProcess
    try:
        subprocess.Popen(  # noqa: S603 - 记录下来的原始命令行，仅用于还原
            argv,
            cwd=rec.get("cwd") or None,
            close_fds=True,
            creationflags=_DETACHED_PROCESS | _CREATE_NEW_PROCESS_GROUP,
        )
        return True
    except Exception:
        return False


def _coerce_value(key: str, value: Any) -> Any:
    """按默认值类型把外部输入转成正确的类型，失败返回 None。"""
    if key in _BOOL_KEYS:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            text = value.strip().lower()
            if text in {"1", "true", "yes", "y", "on", "开", "开启", "是"}:
                return True
            if text in {"0", "false", "no", "n", "off", "关", "关闭", "否"}:
                return False
        return None
    if key in _INT_KEYS:
        try:
            number = int(float(value))
        except (TypeError, ValueError):
            return None
        if key.endswith("_seconds"):
            number = max(1, min(number, 3600))
        return number
    if key in _LIST_KEYS:
        if isinstance(value, (list, tuple)):
            return [str(x).strip() for x in value if str(x).strip()]
        if isinstance(value, str):
            return [x.strip() for x in re.split(r"[,;\n\r]+", value) if x.strip()]
        return None
    return value
