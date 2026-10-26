"""Ephemeral rounds: stopping wins over delayed starts; heartbeats cannot revive rounds."""

from __future__ import annotations

import re
import threading
import time
import uuid
from collections import OrderedDict
from collections.abc import Callable

from .connection import LEASE_SECONDS


class AdSession:
    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._lock = threading.RLock()
        self._active: str | None = None
        self._deadline = 0.0
        self._retired: OrderedDict[str, float] = OrderedDict()
        self._ready = False
        self._epoch = str(uuid.uuid4())
        self.lease_seconds = LEASE_SECONDS

    @staticmethod
    def valid_id(round_id: str) -> bool:
        return isinstance(round_id, str) and re.fullmatch(r"[a-f0-9-]{36}", round_id) is not None

    def _retire(self, round_id: str) -> None:
        self._retired[round_id] = self._clock()
        # Retain tombstones far longer than any bounded reconnect/request.
        while self._retired and next(iter(self._retired.values())) < self._clock() - 600:
            self._retired.popitem(last=False)

    def _expire(self) -> None:
        if self._active and self._clock() >= self._deadline:
            self._retire(self._active)
            self._active = None

    def ready(self) -> None:
        with self._lock:
            self._ready = True

    def start(self, round_id: str, confirmed: bool, epoch: str) -> dict:
        with self._lock:
            self._expire()
            if not self._ready or confirmed is not True or not self.valid_id(round_id) or epoch != self._epoch:
                return {"active": False, "reason": "confirmation_required"}
            if round_id in self._retired:
                return {"active": False, "reason": "retired"}
            if self._active and self._active != round_id:
                return {"active": False, "reason": "another_round_running"}
            self._active = round_id
            self._deadline = self._clock() + self.lease_seconds
            return {"active": True, "round_id": round_id}

    def heartbeat(self, round_id: str) -> dict:
        with self._lock:
            self._expire()
            active = self._ready and self._active is not None and self._active == round_id
            if active:
                self._deadline = self._clock() + self.lease_seconds
            return {"active": active, "round_id": round_id}

    def stop(self, round_id: str = "") -> dict:
        with self._lock:
            if not round_id:
                # Invalidate an in-flight start that has not reached the host yet.
                self._epoch = str(uuid.uuid4())
            if self.valid_id(round_id):
                self._retire(round_id)
            if not round_id or self._active == round_id:
                if self._active:
                    self._retire(self._active)
                self._active = None
            return {"active": False}

    def shutdown(self) -> dict:
        with self._lock:
            self._ready = False
            return self.stop()

    def snapshot(self) -> dict:
        with self._lock:
            self._expire()
            return {"active": self._active is not None, "ready": self._ready, "epoch": self._epoch,
                    "retired_round_ids": list(self._retired)[-64:]}
