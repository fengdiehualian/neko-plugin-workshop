from __future__ import annotations

import copy
import heapq
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any


InterruptHandler = Callable[[dict[str, Any], dict[str, Any]], None]


@dataclass(order=True)
class Interrupt:
    sort_key: tuple[int, int] = field(init=False, repr=False)
    priority: int
    sequence: int
    kind: str = field(compare=False)
    payload: dict[str, Any] = field(compare=False, default_factory=dict)
    created_at: float = field(compare=False, default_factory=time.monotonic)

    def __post_init__(self) -> None:
        self.sort_key = (-int(self.priority), int(self.sequence))


@dataclass
class Timer:
    timer_id: str
    due_at: float
    interval: float | None
    kind: str
    payload: dict[str, Any]
    cancelled: bool = False


class InterruptController:
    """Priority interrupt queue with masking and bounded dispatch per frame."""

    def __init__(self, *, clock=time.monotonic) -> None:
        self._clock = clock
        self._handlers: dict[str, InterruptHandler] = {}
        self._queue: list[Interrupt] = []
        self._sequence = 0
        self._mask: set[str] = set()
        self._lock = threading.RLock()

    def register(self, kind: str, handler: InterruptHandler) -> None:
        with self._lock:
            self._handlers[str(kind)] = handler

    def post(self, kind: str, payload: dict[str, Any] | None = None, *, priority: int = 50) -> str:
        with self._lock:
            self._sequence += 1
            event = Interrupt(
                int(priority), self._sequence, str(kind),
                copy.deepcopy(payload or {}), self._clock(),
            )
            heapq.heappush(self._queue, event)
            return f"irq-{self._sequence}"

    def mask(self, *kinds: str) -> None:
        with self._lock:
            self._mask.update(str(kind) for kind in kinds)

    def unmask(self, *kinds: str) -> None:
        with self._lock:
            self._mask.difference_update(str(kind) for kind in kinds)

    def dispatch(self, state: dict[str, Any], *, budget: int | None = None) -> int:
        handled = 0
        while budget is None or handled < budget:
            with self._lock:
                if not self._queue:
                    break
                event = heapq.heappop(self._queue)
                handler = self._handlers.get(event.kind)
                if event.kind in self._mask or handler is None:
                    continue
            handler(event.payload, state)
            handled += 1
        return handled

    @property
    def pending(self) -> int:
        with self._lock:
            return len(self._queue)


class TimerScheduler:
    """Monotonic timers that post interrupts instead of executing game commands."""

    def __init__(self, interrupts: InterruptController, *, clock=time.monotonic) -> None:
        self._interrupts = interrupts
        self._clock = clock
        self._timers: dict[str, Timer] = {}
        self._lock = threading.RLock()

    def schedule(
        self,
        kind: str,
        *,
        delay: float = 0.0,
        interval: float | None = None,
        payload: dict[str, Any] | None = None,
    ) -> str:
        timer_id = uuid.uuid4().hex[:16]
        timer = Timer(
            timer_id, self._clock() + max(0.0, float(delay)),
            None if interval is None else max(0.01, float(interval)),
            str(kind), copy.deepcopy(payload or {}),
        )
        with self._lock:
            self._timers[timer_id] = timer
        return timer_id

    def cancel(self, timer_id: str) -> bool:
        with self._lock:
            timer = self._timers.get(str(timer_id))
            if timer is None:
                return False
            timer.cancelled = True
            self._timers.pop(str(timer_id), None)
            return True

    def tick(self) -> int:
        now = self._clock()
        fired = 0
        with self._lock:
            timers = list(self._timers.values())
        for timer in timers:
            if timer.cancelled or timer.due_at > now:
                continue
            self._interrupts.post(timer.kind, timer.payload, priority=int(timer.payload.get("priority", 40)))
            fired += 1
            with self._lock:
                if timer.cancelled or timer.timer_id not in self._timers:
                    continue
                if timer.interval is None:
                    self._timers.pop(timer.timer_id, None)
                else:
                    timer.due_at = now + timer.interval
        return fired

    @property
    def active(self) -> int:
        with self._lock:
            return len(self._timers)


class StateDMA:
    """Read-only state channels with copy-on-publish semantics.

    This is the plugin-side equivalent of DMA channels: producers publish a
    frame once, consumers read independent immutable channel copies without
    blocking the native bridge or mutating the source snapshot.
    """

    CHANNELS = ("game", "player", "map", "visible_entities", "alerts", "economy", "production", "landmarks")

    def __init__(self) -> None:
        self._channels: dict[str, Any] = {}
        self._frame: int | None = None
        self._sequence = 0
        self._lock = threading.RLock()

    def publish(self, state: dict[str, Any], channels: Iterable[str] | None = None) -> int:
        selected = tuple(channels or self.CHANNELS)
        with self._lock:
            self._sequence += 1
            for channel in selected:
                if channel in state:
                    self._channels[channel] = copy.deepcopy(state[channel])
            frame = state.get("game", {}).get("frame") if isinstance(state.get("game"), dict) else None
            self._frame = frame if isinstance(frame, int) else self._frame
            self._sequence += 1
            return self._sequence

    def read(self, channel: str, default: Any = None) -> Any:
        with self._lock:
            return copy.deepcopy(self._channels.get(str(channel), default))

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {"frame": self._frame, "sequence": self._sequence, "channels": copy.deepcopy(self._channels)}


class FlagBank:
    """Thread-safe writable task flags with edge-triggered interrupts."""

    def __init__(self, interrupts: InterruptController, *, clock=time.monotonic) -> None:
        self._interrupts = interrupts
        self._clock = clock
        self._flags: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()

    def set(
        self, name: str, value: Any = True, *,
        source: str = "runtime", priority: int = 60,
    ) -> bool:
        key = str(name).strip()
        if not key:
            raise ValueError("flag name cannot be empty")
        now = self._clock()
        with self._lock:
            previous = self._flags.get(key, {}).get("value")
            changed = previous != value
            self._flags[key] = {
                "name": key, "value": copy.deepcopy(value),
                "source": str(source), "updated_at": now,
            }
        if changed:
            self._interrupts.post(
                "flag_changed",
                {"name": key, "previous": previous, "value": copy.deepcopy(value), "source": source},
                priority=priority,
            )
        return changed

    def reserve(self, count: int, *, prefix: str = "user_flag") -> int:
        total = max(0, int(count))
        width = max(3, len(str(max(0, total - 1))))
        now = self._clock()
        with self._lock:
            for index in range(total):
                key = f"{prefix}_{index:0{width}d}"
                self._flags.setdefault(key, {
                    "name": key, "value": False,
                    "source": "reserved", "updated_at": now,
                })
        return total

    def clear(self, name: str, *, source: str = "runtime", priority: int = 60) -> bool:
        return self.set(name, False, source=source, priority=priority)

    def get(self, name: str, default: Any = None) -> Any:
        with self._lock:
            return copy.deepcopy(self._flags.get(str(name), {}).get("value", default))

    def compare_and_set(
        self, name: str, expected: Any, value: Any, *, source: str = "runtime", priority: int = 60
    ) -> bool:
        with self._lock:
            current = self._flags.get(str(name), {}).get("value")
            if current != expected:
                return False
        self.set(name, value, source=source, priority=priority)
        return True

    def snapshot(self, prefix: str | None = None) -> dict[str, dict[str, Any]]:
        with self._lock:
            return {
                key: copy.deepcopy(value)
                for key, value in self._flags.items()
                if prefix is None or key.startswith(prefix)
            }


class CommandRuntime:
    """Combines DMA publication, timers and priority interrupts per watcher tick."""

    def __init__(self, *, clock=time.monotonic) -> None:
        self.interrupts = InterruptController(clock=clock)
        self.timers = TimerScheduler(self.interrupts, clock=clock)
        self.dma = StateDMA()
        self.flags = FlagBank(self.interrupts, clock=clock)
        self._last_frame: int | None = None

    def tick(self, state: dict[str, Any], *, interrupt_budget: int | None = None) -> dict[str, int]:
        self.dma.publish(state)
        timers = self.timers.tick()
        frame = state.get("game", {}).get("frame") if isinstance(state.get("game"), dict) else None
        changed = int(isinstance(frame, int) and frame != self._last_frame)
        self._last_frame = frame if isinstance(frame, int) else self._last_frame
        handled = self.interrupts.dispatch(state, budget=interrupt_budget)
        return {"timers_fired": timers, "interrupts_handled": handled, "frame_changed": changed}
