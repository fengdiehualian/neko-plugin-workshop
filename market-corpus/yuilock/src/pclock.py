"""电脑锁机进程管理：N.E.K.O. 插件与 web_panel.py 共用。

防 PID 复用误杀：锁机进程启动时带随机 --instance 标识并把 {pid, instance}
写入状态文件；结束前用 PowerShell 校验该 PID 的命令行确实包含本次 instance
才执行 taskkill，否则视为进程已消失（崩溃/重启），只清理状态。
所有 OpenProcess 句柄均 CloseHandle 释放。
"""
from __future__ import annotations

import ctypes
import json
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

CREATE_NO_WINDOW = 0x08000000
STATE_PATH = Path(tempfile.gettempdir()) / "yuilock_pc_lock.json"
LOCK_PATH = Path(tempfile.gettempdir()) / "yuilock_pc_lock.lock"


class _FileLock:
    """Windows 文件锁（msvcrt）：包住「检查是否已锁 → 启动 → 写状态」全程，防并发双开。"""

    def __enter__(self):
        self._fp = open(LOCK_PATH, "a+b")
        try:
            import msvcrt
            msvcrt.locking(self._fp.fileno(), msvcrt.LK_LOCK, 1)
        except OSError:
            pass  # 拿不到锁也继续（尽力互斥）
        return self

    def __exit__(self, *exc):
        try:
            self._fp.seek(0)
            import msvcrt
            msvcrt.locking(self._fp.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        self._fp.close()
        return False


def _read_state() -> dict | None:
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _win_process_cmdline(pid: int) -> str:
    if sys.platform != "win32":
        return ""
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f"(Get-CimInstance Win32_Process -Filter 'ProcessId={int(pid)}').CommandLine"],
            capture_output=True, text=True, timeout=10,
            creationflags=CREATE_NO_WINDOW)
        return (out.stdout or "").strip()
    except Exception:
        return ""


def _win_process_path(pid: int) -> str:
    """返回进程可执行文件完整路径；句柄确保释放。"""
    if sys.platform != "win32":
        return ""
    try:
        k32 = ctypes.WinDLL("kernel32")
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        if not h:
            return ""
        try:
            buf = ctypes.create_unicode_buffer(1024)
            size = ctypes.c_ulong(1024)
            if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                return buf.value
            return ""
        finally:
            k32.CloseHandle(h)
    except Exception:
        return ""


def is_locker_alive() -> bool:
    st = _read_state()
    if not st:
        return False
    return _state_matches_process(st)


def _state_matches_process(st: dict) -> bool:
    """状态文件必须与真实进程三重对上：
    1) 可执行文件路径 == 启动时记录的路径；
    2) 命令行里有完整的「--instance <本次 uuid>」参数对；
    3) pid 存活（以上查询本身即验证）。
    状态文件损坏（pid 非整数等）一律返回 False，绝不抛错。"""
    try:
        pid = int(st.get("pid"))
    except (TypeError, ValueError):
        return False
    instance = str(st.get("instance", ""))
    exe = str(st.get("exe", "")).strip().lower()
    if not instance or not exe:
        return False
    path = _win_process_path(pid).lower()
    if not path or path != exe:
        return False
    args = _win_process_cmdline(pid).split()
    for i, a in enumerate(args):
        if a == "--instance" and i + 1 < len(args) and args[i + 1].strip() == instance:
            return True
    return False


def spawn_locker(script: Path, allow_exes: list[str] | None = None) -> dict:
    if sys.platform != "win32":
        return {"ok": False, "error": "锁定电脑仅支持 Windows"}
    with _FileLock():
        if is_locker_alive():
            return {"ok": False, "error": "已有锁机进程在运行，不要重复锁定"}
        instance = uuid.uuid4().hex
        python = Path(sys.executable)
        pythonw = python.with_name("pythonw.exe")
        exe = str(pythonw if pythonw.exists() else python)
        args = [exe, str(script), "--instance", instance, "--state", str(STATE_PATH)]
        for p in (allow_exes or []):
            args += ["--allow", str(p)]
        try:
            proc = subprocess.Popen(args, creationflags=CREATE_NO_WINDOW,
                                    cwd=str(script.parent))
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        STATE_PATH.write_text(json.dumps(
            {"pid": proc.pid, "instance": instance, "exe": exe.lower()}),
            encoding="utf-8")
        return {"ok": True, "pid": proc.pid, "instance": instance}


def kill_locker() -> tuple[bool, str]:
    """结束锁机进程。返回 (是否确实结束过, 给用户看的消息)。
    必须三重校验（路径/instance 参数对/pid 存活）全部通过才 taskkill，
    状态文件被篡改或 PID 被复用时宁可不动手；坏状态文件按无效清理，不抛错。"""
    with _FileLock():
        st = _read_state()
        if not st:
            return False, "电脑当前没有锁定"
        if not _state_matches_process(st):
            try:
                STATE_PATH.unlink()
            except Exception:
                pass
            return False, "锁机进程已不存在（可能崩溃或重启过），残留状态已清理"
        pid = int(st["pid"])
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                           capture_output=True, creationflags=CREATE_NO_WINDOW)
        try:
            STATE_PATH.unlink()
        except Exception:
            pass
        return True, "电脑已解锁，恢复自由"
