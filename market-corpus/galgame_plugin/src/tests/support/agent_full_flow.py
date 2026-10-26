from __future__ import annotations

import asyncio
from importlib import import_module
from typing import Any

from tests.support.memory_flow import (
    _Ctx as _Ctx,
)
from tests.support.memory_flow import (
    _event,
    _session_state,
)
from tests.support.memory_flow import (
    _Logger as _Logger,
)
from tests.support.memory_flow import (
    _make_effective_config as _make_effective_config,
)
from tests.support.memory_flow import (
    _make_plugin_dirs as _make_plugin_dirs,
)
from tests.support.plugin_runtime_stubs import install_plugin_runtime_stubs

install_plugin_runtime_stubs()

package = import_module("market_plugins.galgame_plugin")
game_llm_agent_module = import_module("market_plugins.galgame_plugin.game_llm_agent")
galgame_service = import_module("market_plugins.galgame_plugin.service")
models = import_module("market_plugins.galgame_plugin.models")
plugin_core = import_module("market_plugins.galgame_plugin.plugin_core")
host_adapter = import_module("market_plugins.galgame_plugin.host_agent_adapter")
GalgameBridgePlugin = plugin_core.GalgamePlugin
GameLLMAgent = game_llm_agent_module.GameLLMAgent
HostAgentError = host_adapter.HostAgentError
build_config = galgame_service.build_config
build_suggest_context = galgame_service.build_suggest_context
build_summarize_context = galgame_service.build_summarize_context

DATA_SOURCE_BRIDGE_SDK = models.DATA_SOURCE_BRIDGE_SDK
DATA_SOURCE_MEMORY_READER = models.DATA_SOURCE_MEMORY_READER
DATA_SOURCE_OCR_READER = models.DATA_SOURCE_OCR_READER
OCR_CAPTURE_PROFILE_STAGE_CONFIG = models.OCR_CAPTURE_PROFILE_STAGE_CONFIG
OCR_CAPTURE_PROFILE_STAGE_DEFAULT = models.OCR_CAPTURE_PROFILE_STAGE_DEFAULT
OCR_CAPTURE_PROFILE_STAGE_DIALOGUE = models.OCR_CAPTURE_PROFILE_STAGE_DIALOGUE
OCR_CAPTURE_PROFILE_STAGE_GALLERY = models.OCR_CAPTURE_PROFILE_STAGE_GALLERY
OCR_CAPTURE_PROFILE_STAGE_GAME_OVER = models.OCR_CAPTURE_PROFILE_STAGE_GAME_OVER
OCR_CAPTURE_PROFILE_STAGE_MENU = models.OCR_CAPTURE_PROFILE_STAGE_MENU
OCR_CAPTURE_PROFILE_STAGE_MINIGAME = models.OCR_CAPTURE_PROFILE_STAGE_MINIGAME
OCR_CAPTURE_PROFILE_STAGE_SAVE_LOAD = models.OCR_CAPTURE_PROFILE_STAGE_SAVE_LOAD
OCR_CAPTURE_PROFILE_STAGE_TITLE = models.OCR_CAPTURE_PROFILE_STAGE_TITLE

package.build_summarize_context = build_summarize_context
package.json_copy = plugin_core.json_copy


def _shared_state(
    *,
    mode: str = "choice_advisor",
    push_notifications: bool = True,
    connection_state: str = "active",
    stream_reset_pending: bool = False,
    game_id: str = "demo.alpha",
    session_id: str = "sess-a",
    last_seq: int = 2,
    snapshot: dict[str, object] | None = None,
    history_lines: list[dict[str, object]] | None = None,
    history_observed_lines: list[dict[str, object]] | None = None,
    history_choices: list[dict[str, object]] | None = None,
    history_events: list[dict[str, object]] | None = None,
    active_data_source: str | None = None,
    ocr_reader_runtime: dict[str, object] | None = None,
    memory_reader_runtime: dict[str, object] | None = None,
) -> dict[str, object]:
    snapshot_value = snapshot or _session_state(
        speaker="雪乃", text="当前台词", scene_id="scene-a", line_id="line-1"
    )
    shared = {
        "mode": mode,
        "push_notifications": push_notifications,
        "current_connection_state": connection_state,
        "stream_reset_pending": stream_reset_pending,
        "active_game_id": game_id,
        "active_session_id": session_id,
        "last_seq": last_seq,
        "latest_snapshot": snapshot_value,
        "history_events": list(history_events or []),
        "history_lines": list(history_lines or []),
        "history_observed_lines": list(history_observed_lines or []),
        "history_choices": list(history_choices or []),
        "screen_type": str(snapshot_value.get("screen_type") or ""),
        "screen_ui_elements": list(snapshot_value.get("screen_ui_elements") or []),
        "screen_confidence": float(snapshot_value.get("screen_confidence") or 0.0),
        "ocr_reader_runtime": dict(ocr_reader_runtime or {}),
        "memory_reader_runtime": dict(memory_reader_runtime or {}),
    }
    if active_data_source is not None:
        shared["active_data_source"] = active_data_source
    return shared


class _FakeHostAdapter:
    def __init__(self, *, ready: bool = True) -> None:
        self.ready = ready
        self.started: list[str] = []
        self.cancelled: list[str] = []
        self.tasks: dict[str, dict[str, object]] = {}
        self._counter = 0

    async def get_computer_use_availability(self, *, timeout: float = 1.5):
        del timeout
        return {"ready": self.ready, "reasons": [] if self.ready else ["computer_use unavailable"]}

    async def run_computer_use_instruction(self, instruction: str, *, lanlan_name: str = "", timeout: float = 5.0):
        del lanlan_name, timeout
        self._counter += 1
        task_id = f"task-{self._counter}"
        self.started.append(instruction)
        self.tasks[task_id] = {"id": task_id, "status": "running", "result": None}
        return {"task_id": task_id, "status": "running"}

    async def get_task(self, task_id: str, *, timeout: float = 2.0):
        del timeout
        return dict(self.tasks[task_id])

    async def cancel_task(self, task_id: str, *, timeout: float = 5.0):
        del timeout
        self.cancelled.append(task_id)
        self.tasks[task_id] = {"id": task_id, "status": "cancelled", "error": "Cancelled by test"}
        return {"success": True, "task_id": task_id, "status": "cancelled"}

    async def shutdown(self) -> None:
        return None


class _FakeLLMGateway:
    def __init__(self, *, suggest_payload: dict[str, object] | None = None, reply_payload: dict[str, object] | None = None, summarize_payload: dict[str, object] | None = None, delay: float = 0.0, summary_delay: float = 0.0) -> None:
        self.suggest_payload = suggest_payload or {"degraded": True, "choices": [], "diagnostic": "no llm"}
        self.reply_payload = reply_payload or {"degraded": True, "reply": "fallback", "diagnostic": "no llm"}
        self.summarize_payload = summarize_payload or {"degraded": True, "summary": "", "diagnostic": "no llm"}
        self.delay = delay
        self.summary_delay = summary_delay
        self.suggest_calls: list[dict[str, object]] = []
        self.reply_calls: list[dict[str, object]] = []
        self.summarize_calls: list[dict[str, object]] = []

    async def suggest_choice(self, context: dict[str, object]):
        self.suggest_calls.append(dict(context))
        if self.delay:
            await asyncio.sleep(self.delay)
        return dict(self.suggest_payload)

    async def agent_reply(self, context: dict[str, object]):
        self.reply_calls.append(dict(context))
        if self.delay:
            await asyncio.sleep(self.delay)
        return dict(self.reply_payload)

    async def summarize_scene(self, context: dict[str, object]):
        self.summarize_calls.append(dict(context))
        if self.summary_delay:
            await asyncio.sleep(self.summary_delay)
        return dict(self.summarize_payload)


class _BlockingSummaryGateway(_FakeLLMGateway):
    def __init__(self) -> None:
        super().__init__()
        self.summary_started = asyncio.Event()
        self.release_summary = asyncio.Event()

    async def summarize_scene(self, context: dict[str, object]):
        self.summarize_calls.append(dict(context))
        self.summary_started.set()
        await self.release_summary.wait()
        return {"degraded": False, "summary": f"llm summary for {context.get('scene_id') or 'unknown'}", "diagnostic": ""}


class _SerialProbeLLMGateway(_FakeLLMGateway):
    def __init__(self) -> None:
        super().__init__(reply_payload={"degraded": False, "reply": "ok", "diagnostic": ""})
        self.active_replies = 0
        self.max_active_replies = 0

    async def agent_reply(self, context: dict[str, object]):
        self.reply_calls.append(dict(context))
        self.active_replies += 1
        self.max_active_replies = max(self.max_active_replies, self.active_replies)
        try:
            await asyncio.sleep(0.02)
            return {"degraded": False, "reply": str(context.get("prompt") or "ok"), "diagnostic": ""}
        finally:
            self.active_replies -= 1


def _run_in_new_loop(awaitable: Any):
    with asyncio.Runner() as runner:
        return runner.run(awaitable)


async def _drain_agent_summary_tasks(agent: Any) -> None:
    for _ in range(4):
        tasks = [*agent._summary_tasks, *agent._scene_capsule_tasks]
        if not tasks:
            return
        await asyncio.gather(*tasks, return_exceptions=True)


def _summary_test_line(scene_id: str, index: int, *, session_id: str = "sess-a") -> dict[str, object]:
    del session_id
    return {"line_id": f"{scene_id}-line-{index}", "speaker": "Yukino", "text": f"{scene_id} dialogue line {index}.", "scene_id": scene_id, "route_id": "", "ts": f"2026-04-21T08:35:{index:02d}Z"}


def _summary_test_line_event(scene_id: str, index: int, *, seq: int, session_id: str = "sess-a") -> dict[str, object]:
    line = _summary_test_line(scene_id, index)
    return _event(seq=seq, event_type="line_changed", session_id=session_id, game_id="demo.alpha", payload={**line, "stability": "stable"}, ts=str(line["ts"]))
