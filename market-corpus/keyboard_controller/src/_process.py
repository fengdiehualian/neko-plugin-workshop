# -*- coding: utf-8 -*-
"""进程管理：枚举、查详情、杀进程、等待进程退出。"""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import time
from typing import Any


def _is_windows() -> bool:
    return sys.platform == "win32"


def list_processes(query: str = "", max_results: int = 50) -> dict[str, Any]:
    """列出运行中的进程。

    query: 进程名关键字（留空=全部）
    max_results: 最多返回数
    """
    procs: list[dict[str, Any]] = []
    try:
        if _is_windows():
            # 使用 tasklist（最快最稳定）
            args = ["tasklist", "/FO", "CSV", "/NH"]
            if query:
                args += ["/FI", f"IMAGENAME eq *{query}*"]
            result = subprocess.run(args, capture_output=True, text=True, timeout=10)
            for line in result.stdout.strip().split("\n"):
                if not line.strip():
                    continue
                parts = [p.strip('"') for p in line.split('","')]
                if len(parts) >= 5 and len(procs) < max_results:
                    try:
                        procs.append({
                            "name": parts[0],
                            "pid": int(parts[1]),
                            "session": parts[2],
                            "session_num": parts[3],
                            "mem_kb": int(parts[4].replace(",", "").replace(" K", "")),
                        })
                    except (ValueError, IndexError):
                        continue
        else:
            result = subprocess.run(["ps", "-eo", "pid,comm,rss"], capture_output=True, text=True, timeout=10)
            for line in result.stdout.strip().split("\n")[1:]:
                if not line.strip() or len(procs) >= max_results:
                    continue
                parts = line.split(None, 2)
                if len(parts) >= 2:
                    try:
                        procs.append({
                            "name": parts[1],
                            "pid": int(parts[0]),
                            "mem_kb": int(parts[2]) if len(parts) > 2 else 0,
                        })
                    except (ValueError, IndexError):
                        continue
    except Exception as e:
        return {"ok": False, "error": str(e)}

    # 如果指定了 query，二次过滤
    if query and procs:
        ql = query.lower()
        procs = [p for p in procs if ql in p["name"].lower()]

    return {"ok": True, "count": len(procs), "processes": procs[:max_results]}


def process_info(pid: int) -> dict[str, Any]:
    """获取进程详情（名称、PID、内存、命令行、窗口标题列表）。"""
    try:
        if _is_windows():
            # 获取进程名 + 内存
            result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                capture_output=True, text=True, timeout=10,
            )
            name = ""
            mem_kb = 0
            for line in result.stdout.strip().split("\n"):
                parts = [p.strip('"') for p in line.split('","')]
                if len(parts) >= 5:
                    name = parts[0]
                    mem_kb = int(parts[4].replace(",", "").replace(" K", ""))
                    break

            # 获取命令行（WMIC）
            try:
                cmd_result = subprocess.run(
                    ["wmic", "process", "where", f"ProcessId={pid}", "get", "CommandLine", "/FORMAT:CSV"],
                    capture_output=True, text=True, timeout=10,
                )
                lines = cmd_result.stdout.strip().split("\n")
                cmdline = lines[1].strip().rstrip(",") if len(lines) > 1 else ""
            except Exception:
                cmdline = ""

            # 获取窗口标题
            titles = _get_window_titles_for_pid(pid)
        else:
            result = subprocess.run(
                ["ps", "-p", str(pid), "-o", "comm,rss,args"],
                capture_output=True, text=True, timeout=10,
            )
            lines = result.stdout.strip().split("\n")
            name = ""
            mem_kb = 0
            cmdline = ""
            if len(lines) > 1:
                parts = lines[1].split(None, 2)
                if len(parts) >= 1:
                    name = parts[0]
                if len(parts) >= 2:
                    mem_kb = int(parts[1])
                if len(parts) >= 3:
                    cmdline = parts[2]
            titles = []

        return {
            "ok": True,
            "pid": pid,
            "name": name,
            "mem_kb": mem_kb,
            "mem_mb": round(mem_kb / 1024, 1),
            "command_line": cmdline,
            "windows": titles,
            "window_count": len(titles),
        }
    except Exception as e:
        return {"ok": False, "error": str(e), "pid": pid}


def _get_window_titles_for_pid(pid: int) -> list[str]:
    """获取指定 PID 的所有可见窗口标题。"""
    if not _is_windows():
        return []

    titles: list[str] = []
    try:
        user32 = ctypes.windll.user32
        _ = ctypes.windll.kernel32

        WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_int, ctypes.c_int)
        windows: list[tuple[int, str]] = []

        def _enum(hwnd, _):
            if not user32.IsWindowVisible(hwnd):
                return True
            _, wpid = ctypes.c_uint(), ctypes.c_uint()
            ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(wpid))
            if wpid.value == pid:
                length = user32.GetWindowTextLengthW(hwnd)
                if length > 0:
                    buf = ctypes.create_unicode_buffer(length + 1)
                    user32.GetWindowTextW(hwnd, buf, length + 1)
                    if buf.value.strip():
                        windows.append((hwnd, buf.value))
            return True

        user32.EnumWindows(WNDENUMPROC(_enum), 0)
        titles = [t for _, t in windows]
    except Exception:
        pass
    return titles


def kill_process(pid: int, force: bool = False) -> dict[str, Any]:
    """终止进程。

    pid: 进程 ID
    force: True=强制终止，False=礼貌关闭

    安全护栏：拒绝终止自身进程、系统关键进程（杀掉即蓝屏/失能的一类）。
    这是 LLM 可直接调用的破坏性操作，宁可拒绝也不能让幻觉参数带走
    用户未保存的工作或整个系统。
    """
    try:
        if pid <= 0:
            return {"ok": False, "error": f"无效 PID：{pid}"}

        # 自身/父进程保护：杀掉即等于杀死插件宿主与 N.E.K.O 本体。
        protected_pids = {os.getpid()}
        ppid = os.ppid if hasattr(os, "ppid") else None
        if ppid:
            protected_pids.add(ppid)
        if pid in protected_pids:
            return {"ok": False, "error": f"拒绝终止自身/宿主进程（pid={pid}）", "pid": pid}

        # 系统关键进程保护：这些进程被终止会导致系统崩溃或失能。
        critical_names = {
            "wininit", "csrss", "smss", "services", "lsass", "winlogon",
            "system", "system idle process", "registry", "memory compression",
        }
        info = process_info(pid)
        pname = str(info.get("process_name") or "").lower()
        stem = pname.removesuffix(".exe") if pname else ""
        if stem in critical_names:
            return {
                "ok": False,
                "error": f"拒绝终止系统关键进程：{pname}（pid={pid}）",
                "pid": pid,
            }

        if _is_windows():
            if force:
                subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True, timeout=10)
            else:
                subprocess.run(["taskkill", "/PID", str(pid)], capture_output=True, timeout=10)
        else:
            sig = "-9" if force else "-15"
            subprocess.run(["kill", sig, str(pid)], capture_output=True, timeout=10)

        # 确认是否已终止
        time.sleep(0.3)
        alive = _process_exists(pid)
        if alive is None:
            # 权限不足等原因无法确认 —— 不能谎报"已终止"。
            return {
                "ok": False,
                "pid": pid,
                "force": force,
                "still_alive": None,
                "error": "已发送终止信号，但无权限确认进程状态（可能需要提升权限）",
            }
        return {
            "ok": not alive,
            "pid": pid,
            "force": force,
            "still_alive": alive,
            "message": f"进程 {pid} {'已终止' if not alive else '仍在运行'}",
        }
    except Exception as e:
        return {"ok": False, "error": str(e), "pid": pid}


def _process_exists(pid: int) -> bool | None:
    """进程是否存活。返回 None 表示无法判定（如 ACCESS_DENIED）。

    把"拒绝访问"当成"已退出"会让 kill_process 谎报成功——受保护进程
    （提权/系统进程）OpenProcess 失败恰恰说明它还活着。
    """
    try:
        if _is_windows():
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(0x0400, False, pid)  # PROCESS_QUERY_INFORMATION
            if handle:
                kernel32.CloseHandle(handle)
                return True
            # ERROR_ACCESS_DENIED=5：进程存在但查询被拒。
            if kernel32.GetLastError() == 5:
                return None
            return False
        else:
            os.kill(pid, 0)
            return True
    except PermissionError:
        return None
    except (OSError, ProcessLookupError):
        return False


def wait_for_process(pid: int, timeout: float = 30.0, interval: float = 0.5) -> dict[str, Any]:
    """等待进程退出。

    timeout: 最长等待秒数（钳制到 120s：工具层超时只能取消协程，
    to_thread 里的轮询线程本身不可取消，不钳制会空转到天荒地老）。
    interval: 轮询间隔
    """
    timeout = min(max(0.5, float(timeout or 30.0)), 120.0)
    started = time.time()
    while time.time() - started < timeout:
        if _process_exists(pid) is False:
            return {"ok": True, "pid": pid, "exited": True, "waited": round(time.time() - started, 2)}
        time.sleep(interval)
    return {"ok": True, "pid": pid, "exited": False, "waited": round(time.time() - started, 2)}

# 注意：不要在这里实现 is_elevated —— 旧实现把 TokenElevation(class=20) 的
# 返回值与 TokenElevationTypeFull(=2) 比较，恒为 False，属于误导性死代码，
# 已删除。真正的提权检查见 _win32_input.check_elevation（TokenElevation +
# TOKEN_ELEVATION 结构体，语义正确）。