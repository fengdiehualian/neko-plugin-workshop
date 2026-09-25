"""Shipping 主进程看守（系统公开进程列表）。"""

from __future__ import annotations

import ctypes
import subprocess
import sys
import time
from ctypes import wintypes
from dataclasses import dataclass
from typing import Callable

SHIPPING_PROCESSES: dict[str, str] = {
    "Strinova-Win64-Shipping.exe": "global",
    "Calabiyau-Win64-Shipping.exe": "cn",
    "PMGame-Win64-Shipping.exe": "unknown",
}

LAUNCHER_PROCESSES: frozenset[str] = frozenset(
    {
        "CalabiYau.exe",
        "Calabiyau.exe",
        "Strinova.exe",
    }
)

ListProcessesFn = Callable[[], set[str]]


def _list_process_names_toolhelp() -> set[str]:
    """Enumerate executable names without spawning ``tasklist``.

    Toolhelp takes a read-only system snapshot.  It does not open a handle to
    the game process, read process memory, inject code, or install hooks.
    """

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_snapshot = kernel32.CreateToolhelp32Snapshot
    create_snapshot.argtypes = (wintypes.DWORD, wintypes.DWORD)
    create_snapshot.restype = wintypes.HANDLE
    process_first = kernel32.Process32FirstW
    process_first.argtypes = (wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W))
    process_first.restype = wintypes.BOOL
    process_next = kernel32.Process32NextW
    process_next.argtypes = (wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W))
    process_next.restype = wintypes.BOOL
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = (wintypes.HANDLE,)
    close_handle.restype = wintypes.BOOL

    snapshot = create_snapshot(0x00000002, 0)  # TH32CS_SNAPPROCESS
    invalid_handle = ctypes.c_void_p(-1).value
    if snapshot == invalid_handle:
        raise ctypes.WinError(ctypes.get_last_error())

    names: set[str] = set()
    entry = PROCESSENTRY32W()
    entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
    try:
        ok = bool(process_first(snapshot, ctypes.byref(entry)))
        while ok:
            name = str(entry.szExeFile or "").strip()
            if name:
                names.add(name)
            ok = bool(process_next(snapshot, ctypes.byref(entry)))
        if ctypes.get_last_error() != 18:  # ERROR_NO_MORE_FILES
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        close_handle(snapshot)
    return names


def list_process_names() -> set[str]:
    """返回当前进程名集合。"""
    names: set[str] = set()
    if sys.platform != "win32":
        try:
            out = subprocess.check_output(
                ["ps", "-A", "-o", "comm="],
                text=True,
                stderr=subprocess.DEVNULL,
                timeout=2,
            )
            for line in out.splitlines():
                base = line.strip().split("/")[-1]
                if base:
                    names.add(base)
        except (OSError, subprocess.SubprocessError) as exc:
            raise OSError("process enumeration failed") from exc
        return names

    try:
        return _list_process_names_toolhelp()
    except (OSError, ValueError):
        # Compatibility fallback for unusual/restricted Windows runtimes.
        pass

    try:
        out = subprocess.check_output(
            ["tasklist", "/FO", "CSV", "/NH"],
            text=True,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise OSError("process enumeration failed") from exc
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith('"'):
            end = line.find('",', 1)
            name = line[1:end] if end > 1 else line.strip('"')
        else:
            name = line.split(",")[0].strip().strip('"')
        if name:
            names.add(name)
    return names


@dataclass
class ProcessSnapshot:
    running: bool = False
    region: str = "unknown"
    process_name: str | None = None
    launcher_present: bool = False
    changed: bool = False
    edge: str | None = None  # game_started | game_stopped | None
    uptime_s: float | None = None
    started_at: float | None = None


class ProcessWatcher:
    def __init__(
        self,
        *,
        debounce: int = 2,
        list_processes: ListProcessesFn | None = None,
    ) -> None:
        self.debounce = max(1, int(debounce))
        self._list = list_processes or list_process_names
        self._stable_running = False
        self._pending_running: bool | None = None
        self._pending_count = 0
        self._region = "unknown"
        self._process_name: str | None = None
        self._launcher_present = False
        self._started_at: float | None = None

    @property
    def running(self) -> bool:
        return self._stable_running

    def poll(self, now: float | None = None) -> ProcessSnapshot:
        now = time.time() if now is None else now
        try:
            names = self._list()
        except OSError:
            # An unknown observation must not count as game exit or bridge two
            # otherwise non-consecutive observations during debounce.
            self._pending_running = None
            self._pending_count = 0
            raise
        hit_name, region = self._match_shipping(names)
        launcher = self._match_launcher(names)
        raw_running = hit_name is not None

        edge: str | None = None
        changed = False
        if raw_running == self._stable_running:
            self._pending_running = None
            self._pending_count = 0
            if raw_running:
                self._process_name = hit_name
                self._region = region
        else:
            if self._pending_running is None or self._pending_running != raw_running:
                self._pending_running = raw_running
                self._pending_count = 1
            else:
                self._pending_count += 1
            if self._pending_count >= self.debounce:
                prev = self._stable_running
                self._stable_running = raw_running
                self._pending_running = None
                self._pending_count = 0
                changed = True
                if raw_running and not prev:
                    self._process_name = hit_name
                    self._region = region
                    self._started_at = now
                    edge = "game_started"
                elif (not raw_running) and prev:
                    edge = "game_stopped"
                    self._started_at = None
                    self._process_name = None
                    self._region = "unknown"

        self._launcher_present = launcher
        uptime = (now - self._started_at) if (self._stable_running and self._started_at) else None
        return ProcessSnapshot(
            running=self._stable_running,
            region=self._region if self._stable_running else "unknown",
            process_name=self._process_name if self._stable_running else None,
            launcher_present=self._launcher_present,
            changed=changed,
            edge=edge,
            uptime_s=uptime,
            started_at=self._started_at if self._stable_running else None,
        )

    @staticmethod
    def _match_shipping(names: set[str]) -> tuple[str | None, str]:
        lower_map = {n.lower(): n for n in names}
        for proc, region in SHIPPING_PROCESSES.items():
            if proc.lower() in lower_map:
                return lower_map[proc.lower()], region
        return None, "unknown"

    @staticmethod
    def _match_launcher(names: set[str]) -> bool:
        lower = {n.lower() for n in names}
        return any(x.lower() in lower for x in LAUNCHER_PROCESSES)
