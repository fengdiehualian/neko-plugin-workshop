"""Deterministic bounded backoff state for restoring the host context."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ContextRestoreRetry:
    pending: bool = False
    attempts: int = 0
    next_attempt_at: float = 0.0
    in_flight: bool = False
    max_backoff_seconds: float = 60.0

    def request(self) -> None:
        if self.pending:
            return
        self.pending = True
        self.attempts = 0
        self.next_attempt_at = 0.0

    def begin_attempt(self, now: float, *, ignore_backoff: bool = False) -> bool:
        if (
            not self.pending
            or self.in_flight
            or (not ignore_backoff and now < self.next_attempt_at)
        ):
            return False
        self.in_flight = True
        return True

    def finish_attempt(self, *, success: bool, now: float) -> None:
        self.in_flight = False
        if success:
            self.clear()
            return
        self.attempts += 1
        delay = min(2.0 ** max(0, self.attempts - 1), self.max_backoff_seconds)
        self.next_attempt_at = now + delay

    def clear(self) -> None:
        self.pending = False
        self.attempts = 0
        self.next_attempt_at = 0.0
        self.in_flight = False
