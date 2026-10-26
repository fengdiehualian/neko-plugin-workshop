"""One bounded cooperative review, executed by the native producer thread.

No worker threads, model instances or images are created/retained here. The
caller owns cancellation, capture selection, evidence and delivery policy.
"""
from concurrent.futures import Future
import threading


class OCRWorkQueue:
    def __init__(self):
        self._lock = threading.Lock()
        self._producer = None
        self._pending = None
        self._running = False

    def begin(self):
        with self._lock:
            self._producer = threading.get_ident()

    def submit(self, callback):
        with self._lock:
            if self._producer is None or self._pending is not None or self._running:
                return None
            future = Future()
            self._pending = (future, callback)
            return future

    def pump(self):
        with self._lock:
            if self._producer != threading.get_ident() or self._running or self._pending is None:
                return
            future, callback = self._pending
            self._pending = None
            self._running = True
        try:
            if future.set_running_or_notify_cancel():
                try:
                    future.set_result(callback())
                except Exception as exc:
                    future.set_exception(exc)
        finally:
            with self._lock:
                self._running = False

    def end(self):
        with self._lock:
            self._producer = None
            pending, self._pending = self._pending, None
        if pending is not None:
            pending[0].cancel()
