import ctypes

import pytest
from plugin.plugins.neko_sous import process


class Function:
    def __init__(self, callback):
        self.callback = callback

    def __call__(self, *args):
        return self.callback(*args)


@pytest.mark.parametrize("names, expected", [
    (["other.exe", "OVERCOOKED2.EXE"], True),
    (["other.exe", "Overcooked2Launcher.exe"], False),
    ([], False),
])
def test_process_snapshot(monkeypatch, names, expected):
    closed = []
    entries = iter(names)

    def advance(handle, pointer):
        name = next(entries, None)
        if name is None:
            return False
        entry = ctypes.cast(pointer, ctypes.POINTER(process.ProcessEntry)).contents
        assert entry.dwSize == ctypes.sizeof(process.ProcessEntry)
        entry.szExeFile = name
        return True

    class Kernel:
        CreateToolhelp32Snapshot = Function(lambda flags, pid: 123)
        Process32FirstW = Function(advance)
        Process32NextW = Function(advance)
        CloseHandle = Function(lambda handle: closed.append(handle) or True)

    monkeypatch.setattr(process.sys, "platform", "win32")
    monkeypatch.setattr(process.ctypes, "WinDLL", lambda *a, **kw: Kernel(), raising=False)
    monkeypatch.setattr(process.ctypes, "get_last_error", lambda: 18, raising=False)
    assert process.game_running_sync() is expected
    assert closed == [123]


def test_unsupported_platform(monkeypatch):
    monkeypatch.setattr(process.sys, "platform", "linux")
    with pytest.raises(OSError):
        process.game_running_sync()


@pytest.mark.parametrize("invalid_handle", [False, True])
def test_snapshot_errors_are_not_reported_as_game_stopped(monkeypatch, invalid_handle):
    closed = []

    class Kernel:
        CreateToolhelp32Snapshot = Function(lambda flags, pid: ctypes.c_void_p(-1).value if invalid_handle else 123)
        Process32FirstW = Function(lambda *args: False)
        Process32NextW = Function(lambda *args: False)
        CloseHandle = Function(lambda handle: closed.append(handle) or True)

    monkeypatch.setattr(process.sys, "platform", "win32")
    monkeypatch.setattr(process.ctypes, "WinDLL", lambda *a, **kw: Kernel(), raising=False)
    monkeypatch.setattr(process.ctypes, "get_last_error", lambda: 5, raising=False)
    monkeypatch.setattr(process.ctypes, "WinError", lambda code: OSError(code, "snapshot failed"), raising=False)
    with pytest.raises(OSError):
        process.game_running_sync()
    assert closed == ([] if invalid_handle else [123])
