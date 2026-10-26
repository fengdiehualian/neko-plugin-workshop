"""Keep whole entry invocations outside a backup/restore, across host loops."""

from __future__ import annotations

import inspect
import threading
from contextvars import ContextVar
from functools import wraps

from plugin.sdk.plugin import Err, SdkError

from ..core.backup import BackupError

_CURRENT = ContextVar("forever_companion_data_operation", default=None)


class DataGate:
    def __init__(self):
        self.lock = threading.Lock()
        self.active = 0
        self.exclusive = False
        self.blocked = False

    def enter(self, exclusive=False):
        with self.lock:
            if self.exclusive or exclusive and self.active or not exclusive and self.blocked:
                return False
            if exclusive:
                self.exclusive = True
            else:
                self.active += 1
            return True

    def leave(self, exclusive=False):
        with self.lock:
            if exclusive:
                self.exclusive = False
            else:
                self.active -= 1


def _wrap(function, name):
    @wraps(function)
    async def guarded(self, *args, **kwargs):
        if _CURRENT.get() is self:
            return await function(self, *args, **kwargs)
        gate = self._data_gate
        startup = name == "startup"
        exclusive = startup or name in {"reset_all", "_debug_diary_fill"}
        if gate.blocked and not startup:
            if name == "dashboard" and not gate.exclusive:
                return {"backup_recovery_required": True, "status": {"enabled": False}}
            return Err(SdkError("backup_recovery_required"))
        if not gate.enter(exclusive=exclusive):
            return Err(SdkError("backup_recovery_required" if gate.blocked else "backup_busy"))
        token = _CURRENT.set(self)
        try:
            if startup:
                await self._backup_recover()
            return await function(self, *args, **kwargs)
        except BackupError as exc:
            return Err(SdkError(exc.code))
        finally:
            _CURRENT.reset(token)
            gate.leave(exclusive=exclusive)
    return guarded


def guard_data_operations(cls):
    # SDK discovery reads the class, so preserve signatures and all decorator
    # metadata with wraps. Also cover dynamically registered debug handlers.
    for name, member in inspect.getmembers(cls, inspect.iscoroutinefunction):
        if name == "data_backup" or not member.__module__.startswith(cls.__module__):
            continue
        if name.startswith("_") and not name.startswith("_debug_"):
            continue
        setattr(cls, name, _wrap(member, name))
    return cls
