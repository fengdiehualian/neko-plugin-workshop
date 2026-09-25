"""In-memory async task queue for writer analysis.

Analysis runs can take minutes — instead of blocking the UI action
call, tasks are queued and processed in the background.  The frontend
polls for completion via ``get_analysis_status``.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Callable, Coroutine
from typing import Any

AnalysisDoneCallback = Callable[[dict[str, Any]], None]


class AnalysisTask:
    """One queued / running / completed analysis."""

    __slots__ = (
        "article_chars",
        "created_at",
        "error",
        "finished_at",
        "mode",
        "model",
        "result",
        "started_at",
        "status",
        "task_id",
    )

    def __init__(self, task_id: str, model: str, mode: str, article_chars: int):
        self.task_id = task_id
        self.status: str = "queued"       # queued | running | done | error
        self.model = model
        self.mode = mode
        self.article_chars = article_chars
        self.created_at = time.time()
        self.started_at: float | None = None
        self.finished_at: float | None = None
        self.result: dict[str, Any] | None = None
        self.error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "status": self.status,
            "model": self.model,
            "mode": self.mode,
            "article_chars": self.article_chars,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "result": self.result,
            "error": self.error,
        }


class AnalysisTaskQueue:
    """Simple in-memory queue with background processing.

    Runs at most one analysis at a time to avoid memory spikes
    when multiple large texts are submitted concurrently.
    """

    MAX_TASKS = 50  # keep at most this many completed tasks
    MAX_CONCURRENT = 1  # one at a time — each analysis can be ~400k chars

    def __init__(self, logger: Any):
        self._logger = logger
        self._tasks: dict[str, AnalysisTask] = {}
        self._lock = asyncio.Lock()
        self._semaphore = asyncio.Semaphore(self.MAX_CONCURRENT)
        self._running: set[asyncio.Task] = set()

    async def submit(
        self,
        *,
        runner: Callable[[], Coroutine[Any, Any, dict[str, Any]]],
        model: str = "",
        mode: str = "standard",
        article_chars: int = 0,
        on_done: AnalysisDoneCallback | None = None,
    ) -> str:
        """Enqueue an analysis, start it in the background, return task_id."""
        task_id = uuid.uuid4().hex[:12]
        task = AnalysisTask(task_id, model, mode, article_chars)

        async with self._lock:
            self._tasks[task_id] = task
            # trim old tasks
            if len(self._tasks) > self.MAX_TASKS:
                finished = [
                    tid for tid, t in self._tasks.items()
                    if t.status in ("done", "error")
                ]
                overflow = len(self._tasks) - self.MAX_TASKS
                for old_id in finished[:overflow]:
                    del self._tasks[old_id]

        # fire background (tracked so we can cancel on shutdown)
        coro = self._run(task_id, runner, on_done)
        asyncio_task = asyncio.create_task(coro)
        self._running.add(asyncio_task)
        asyncio_task.add_done_callback(self._running.discard)
        return task_id

    async def _run(
        self,
        task_id: str,
        runner: Callable[[], Coroutine[Any, Any, dict[str, Any]]],
        on_done: AnalysisDoneCallback | None = None,
    ) -> None:
        task = self._tasks.get(task_id)
        if task is None:
            return
        task.status = "queued"
        async with self._semaphore:
            task.status = "running"
            task.started_at = time.time()
            try:
                self._logger.info(f"Analysis task {task_id} started")
                result = await runner()
                task.result = result
                task.status = "done"
                task.finished_at = time.time()
                self._logger.info(f"Analysis task {task_id} completed")
                if on_done is not None:
                    try:
                        on_done(task.to_dict())
                    except Exception as exc:  # noqa: BLE001 - callback failures must not fail completed tasks.
                        self._logger.warning(f"Analysis task {task_id} completion callback failed: {exc}")
            except asyncio.CancelledError:
                task.status = "error"
                task.error = "任务已取消"
                self._logger.info(f"Analysis task {task_id} cancelled")
            except Exception as exc:  # noqa: BLE001 - background task failures are stored on the task record.
                task.error = str(exc)
                task.status = "error"
                self._logger.warning(f"Analysis task {task_id} failed: {exc}")
            finally:
                if task.finished_at is None:
                    task.finished_at = time.time()

    async def get(self, task_id: str) -> dict[str, Any] | None:
        """Poll for task status / result."""
        task = self._tasks.get(task_id)
        if task is None:
            return None
        return task.to_dict()

    async def shutdown(self) -> None:
        """Cancel all running tasks and clear state (call on plugin shutdown)."""
        for t in list(self._running):
            t.cancel()
        if self._running:
            await asyncio.gather(*self._running, return_exceptions=True)
        self._running.clear()
        self._tasks.clear()
        self._logger.info("Analysis task queue shut down")

    async def list_recent(self, limit: int = 20) -> list[dict[str, Any]]:
        """Return recent tasks, newest first."""
        sorted_tasks = sorted(
            self._tasks.values(),
            key=lambda t: t.created_at,
            reverse=True,
        )
        return [t.to_dict() for t in sorted_tasks[:limit]]
