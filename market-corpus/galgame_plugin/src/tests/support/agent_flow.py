from __future__ import annotations

import asyncio
from typing import Any


class FakeHostAdapter:
    async def get_computer_use_availability(self, *, timeout: float = 1.5):
        del timeout
        return {"ready": True, "reasons": []}

    async def shutdown(self) -> None:
        return None


class FakeLLMGateway:
    def __init__(self) -> None:
        self.summarize_calls: list[dict[str, object]] = []

    async def suggest_choice(self, context: dict[str, object]):
        del context
        return {"degraded": True, "choices": [], "diagnostic": "no llm"}

    async def agent_reply(self, context: dict[str, object]):
        del context
        return {"degraded": True, "reply": "fallback", "diagnostic": "no llm"}

    async def summarize_scene(self, context: dict[str, object]):
        self.summarize_calls.append(dict(context))
        return {"degraded": True, "summary": "", "diagnostic": "no llm"}


def shared_state(
    *,
    snapshot: dict[str, object],
    mode: str = "choice_advisor",
    history_lines: list[dict[str, object]] | None = None,
    history_observed_lines: list[dict[str, object]] | None = None,
) -> dict[str, Any]:
    return {
        "mode": mode,
        "push_notifications": True,
        "current_connection_state": "active",
        "stream_reset_pending": False,
        "active_game_id": "demo.alpha",
        "active_session_id": "sess-a",
        "last_seq": 2,
        "latest_snapshot": snapshot,
        "history_events": [],
        "history_lines": list(history_lines or []),
        "history_observed_lines": list(history_observed_lines or []),
        "history_choices": [],
        "screen_type": str(snapshot.get("screen_type") or ""),
        "screen_ui_elements": list(snapshot.get("screen_ui_elements") or []),
        "screen_confidence": float(snapshot.get("screen_confidence") or 0.0),
        "ocr_reader_runtime": {},
        "memory_reader_runtime": {},
    }


async def drain_summary_tasks(agent: Any) -> None:
    for _ in range(4):
        tasks = [*agent._summary_tasks, *agent._scene_capsule_tasks]
        if not tasks:
            return
        await asyncio.gather(*tasks, return_exceptions=True)
    remaining = [task for task in [*agent._summary_tasks, *agent._scene_capsule_tasks] if not task.done()]
    if remaining:
        raise AssertionError(f"summary tasks still pending: {len(remaining)}")
