"""Event-controlled Windows processes for tree ownership regression tests."""
import _winapi
import ctypes
import json
import os
import subprocess
import sys
from ctypes import wintypes
from pathlib import Path

api = ctypes.WinDLL('kernel32', use_last_error=True)
api.CreateEventW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
api.CreateEventW.restype = wintypes.HANDLE
api.SetEvent.argtypes = [wintypes.HANDLE]
api.SetEvent.restype = wintypes.BOOL


def create_event(name):
    handle = api.CreateEventW(None, True, False, name)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    return handle


def set_event(handle):
    if not api.SetEvent(handle):
        raise ctypes.WinError(ctypes.get_last_error())


def main():
    mode, prefix, output = sys.argv[1:]
    output = Path(output)
    events = {}

    def event(name):
        if name not in events:
            events[name] = create_event(prefix + name)
        return events[name]

    def ready(name, pid):
        (output / (name + '.json')).write_text(json.dumps(pid))
        set_event(event(name))

    def child_args(kind):
        return [sys.executable, __file__, kind, prefix, str(output)]

    try:
        if mode == 'child':
            ready('child', os.getpid())
            _winapi.WaitForSingleObject(event('release_child'), 120000)
        elif mode == 'parent':
            subprocess.Popen(child_args('child'), creationflags=subprocess.CREATE_NO_WINDOW)
            ready('parent', os.getpid())
            _winapi.WaitForSingleObject(event('release_parent'), 120000)
        else:
            if mode in {'owner', 'owner_during_spawn'}:
                sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
                from adapters import windows_process_tree as module
                if mode == 'owner_during_spawn':
                    native = module._job_api()

                    class BlockBeforeReturn:
                        def __getattr__(self, name):
                            return getattr(native, name)

                        def CreateProcessW(self, *args):
                            result = native.CreateProcessW(*args)
                            if result:
                                info = ctypes.cast(args[-1], ctypes.POINTER(module._ProcessInfo)).contents
                                ready('owner', info.pid)
                                _winapi.WaitForSingleObject(event('release_owner'), 120000)
                            return result

                    module._job_api = BlockBeforeReturn
                tree = module.WindowsProcessTree(child_args('parent'), cwd=str(output), env=dict(os.environ))
            else:
                tree = subprocess.Popen(child_args('parent'), creationflags=subprocess.CREATE_NO_WINDOW)
            ready('owner', tree.pid)
            _winapi.WaitForSingleObject(event('release_owner'), 120000)
    finally:
        for handle in events.values():
            _winapi.CloseHandle(handle)


if __name__ == '__main__':
    main()
