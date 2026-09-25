"""Own one Windows assistant tree, including children that outlive its launcher.

The private, non-inheritable Job handle belongs to the plugin. Windows closes it
even if the host terminates the plugin without running Python cleanup.
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import time
from ctypes import wintypes


class _BasicLimits(ctypes.Structure):
    _fields_ = [
        ("process_time", ctypes.c_int64), ("job_time", ctypes.c_int64),
        ("flags", wintypes.DWORD), ("min_working_set", ctypes.c_size_t),
        ("max_working_set", ctypes.c_size_t), ("active_limit", wintypes.DWORD),
        ("affinity", ctypes.c_size_t), ("priority", wintypes.DWORD),
        ("scheduling", wintypes.DWORD),
    ]


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [
        ("basic", _BasicLimits), ("io_counters", ctypes.c_uint64 * 6),
        ("process_memory", ctypes.c_size_t), ("job_memory", ctypes.c_size_t),
        ("peak_process_memory", ctypes.c_size_t), ("peak_job_memory", ctypes.c_size_t),
    ]


class _Accounting(ctypes.Structure):
    _fields_ = [
        ("user_time", ctypes.c_int64), ("kernel_time", ctypes.c_int64),
        ("period_user_time", ctypes.c_int64), ("period_kernel_time", ctypes.c_int64),
        ("page_faults", wintypes.DWORD), ("total_processes", wintypes.DWORD),
        ("active_processes", wintypes.DWORD), ("terminated_processes", wintypes.DWORD),
    ]


class _StartupInfoEx(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD), ("reserved", wintypes.LPWSTR),
        ("desktop", wintypes.LPWSTR), ("title", wintypes.LPWSTR),
        ("x", wintypes.DWORD), ("y", wintypes.DWORD),
        ("x_size", wintypes.DWORD), ("y_size", wintypes.DWORD),
        ("x_chars", wintypes.DWORD), ("y_chars", wintypes.DWORD),
        ("fill", wintypes.DWORD), ("flags", wintypes.DWORD),
        ("show", wintypes.WORD), ("reserved_size", wintypes.WORD),
        ("reserved_bytes", wintypes.LPVOID), ("stdin", wintypes.HANDLE),
        ("stdout", wintypes.HANDLE), ("stderr", wintypes.HANDLE),
        ("attributes", wintypes.LPVOID),
    ]


class _ProcessInfo(ctypes.Structure):
    _fields_ = [("process", wintypes.HANDLE), ("thread", wintypes.HANDLE),
                ("pid", wintypes.DWORD), ("tid", wintypes.DWORD)]


def _job_api():
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    signatures = {
        "CreateJobObjectW": ([wintypes.LPVOID, wintypes.LPCWSTR], wintypes.HANDLE),
        "SetInformationJobObject": ([wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD], wintypes.BOOL),
        "QueryInformationJobObject": ([wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD, wintypes.LPVOID], wintypes.BOOL),
        "TerminateJobObject": ([wintypes.HANDLE, wintypes.UINT], wintypes.BOOL),
        "CloseHandle": ([wintypes.HANDLE], wintypes.BOOL),
        "InitializeProcThreadAttributeList": ([wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(ctypes.c_size_t)], wintypes.BOOL),
        "UpdateProcThreadAttribute": ([wintypes.LPVOID, wintypes.DWORD, ctypes.c_size_t, wintypes.LPVOID, ctypes.c_size_t, wintypes.LPVOID, wintypes.LPVOID], wintypes.BOOL),
        "DeleteProcThreadAttributeList": ([wintypes.LPVOID], None),
        "CreateProcessW": ([wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.LPVOID, wintypes.LPVOID, wintypes.BOOL, wintypes.DWORD, wintypes.LPVOID, wintypes.LPCWSTR, ctypes.POINTER(_StartupInfoEx), ctypes.POINTER(_ProcessInfo)], wintypes.BOOL),
    }
    for name, (args, result) in signatures.items():
        function = getattr(api, name)
        function.argtypes, function.restype = args, result
    return api


class WindowsProcessTree:
    """The process operations used by DataLayerProcessManager, for the whole tree."""

    def __init__(self, args: list[str], *, cwd: str, env: dict[str, str]):
        import msvcrt

        self.args = args
        self._api = _job_api()
        self._process = None
        self._job = self._api.CreateJobObjectW(None, None)
        if not self._job:
            raise ctypes.WinError(ctypes.get_last_error())
        process_info = _ProcessInfo()
        attributes = None
        try:
            limits = _ExtendedLimits()
            limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not self._api.SetInformationJobObject(self._job, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
                raise ctypes.WinError(ctypes.get_last_error())
            size = ctypes.c_size_t()
            self._api.InitializeProcThreadAttributeList(None, 2, 0, ctypes.byref(size))
            attribute_buffer = ctypes.create_string_buffer(size.value)
            if not self._api.InitializeProcThreadAttributeList(attribute_buffer, 2, 0, ctypes.byref(size)):
                raise ctypes.WinError(ctypes.get_last_error())
            attributes = attribute_buffer
            startup = _StartupInfoEx()
            startup.cb = ctypes.sizeof(startup)
            startup.attributes = ctypes.addressof(attributes)
            with open(os.devnull, "r+b", buffering=0) as null:
                handle = msvcrt.get_osfhandle(null.fileno())
                os.set_handle_inheritable(handle, True)
                startup.flags = subprocess.STARTF_USESTDHANDLES
                startup.stdin = startup.stdout = startup.stderr = handle
                inherited = (wintypes.HANDLE * 1)(handle)
                jobs = (wintypes.HANDLE * 1)(self._job)
                for key, value in ((0x20002, inherited), (0x2000D, jobs)):
                    if not self._api.UpdateProcThreadAttribute(
                        attributes, 0, key, value, ctypes.sizeof(value), None, None,
                    ):
                        raise ctypes.WinError(ctypes.get_last_error())
                command = ctypes.create_unicode_buffer(subprocess.list2cmdline(args))
                environment = ctypes.create_unicode_buffer(
                    '\0'.join(f'{key}={value}' for key, value in sorted(env.items())) + '\0'
                )
                # JOB_LIST assigns ownership inside CreateProcess, with no gap
                # where a killed plugin could abandon an unassigned child.
                if not self._api.CreateProcessW(
                    args[0], command, None, None, True,
                    subprocess.CREATE_NO_WINDOW | 0x80000 | 0x400,
                    environment, cwd, ctypes.byref(startup), ctypes.byref(process_info),
                ):
                    raise ctypes.WinError(ctypes.get_last_error())
                self._process, self.pid = process_info.process, process_info.pid
        except BaseException:
            self._close()
            raise
        finally:
            if process_info.thread:
                self._api.CloseHandle(process_info.thread)
            if attributes is not None:
                self._api.DeleteProcThreadAttributeList(attributes)

    def poll(self) -> int | None:
        import _winapi

        accounting = _Accounting()
        if not self._api.QueryInformationJobObject(
            self._job, 1, ctypes.byref(accounting), ctypes.sizeof(accounting), None,
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        if accounting.active_processes or _winapi.WaitForSingleObject(self._process, 0) == _winapi.WAIT_TIMEOUT:
            return None
        return _winapi.GetExitCodeProcess(self._process)

    def terminate(self) -> None:
        if not self._api.TerminateJobObject(self._job, 1):
            raise ctypes.WinError(ctypes.get_last_error())

    kill = terminate

    def wait(self, timeout: float) -> int:
        deadline = time.monotonic() + timeout
        while (code := self.poll()) is None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(self.args, timeout)
            # Bounded exit polling only during an explicit stop, no background loop.
            time.sleep(min(0.01, remaining))
        return code

    def _close(self) -> None:
        for name in ("_job", "_process"):
            handle = getattr(self, name, None)
            if handle:
                self._api.CloseHandle(handle)
                setattr(self, name, None)

    def __del__(self):
        self._close()
