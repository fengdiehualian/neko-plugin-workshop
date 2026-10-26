# -*- coding: utf-8 -*-
"""Unit tests for the mcbot_bridge plugin's service layer.

Exercises the in-process behaviour without a real HTTP bridge or the
user_plugin_server: config parsing, the single-slot claim (busy / overwrite /
anti-thrash), the SSE frame → ``push_message`` cue mapping, the watchdog that
keeps a lost completion event from wedging the slot, and the rule that raw
transport detail never reaches the model.

The plugin facade (``__init__.py``) is exercised separately through the SDK's
auto-register pipeline; here we drive :class:`McbotService` directly, the same
way the sibling ``test_game_agent_minecraft_plugin.py`` does.
"""
from __future__ import annotations

import asyncio
import itertools
import json
import sys
import time
from pathlib import Path

import pytest

try:
    # Standalone plugin repository: tests/conftest.py registers the plugin
    # directory as a ``mcbot_bridge`` package, because the repository folder is
    # named for the market convention (``n.e.k.o_plugin_mcbot_bridge``) and is
    # not a valid Python identifier.
    from mcbot_bridge_plugin.client import (  # type: ignore[import-not-found]
        McbotAuthError,
        McbotBridgeError,
        McbotClient,
        McbotUnreachable,
        SseFrame,
    )
    from mcbot_bridge_plugin.service import (  # type: ignore[import-not-found]
        DONE_TEXT_LIMIT,
        McbotService,
        is_authorization_question,
        normalise_base_url,
        task_looks_like_identifier,
    )
except ModuleNotFoundError:  # pragma: no cover - depends on checkout layout
    # N.E.K.O. source tree: the plugin is a submodule of this repository.
    _ROOT = Path(__file__).resolve().parents[2]
    if str(_ROOT) not in sys.path:
        sys.path.insert(0, str(_ROOT))

    from plugin.plugins.mcbot_bridge.client import (  # noqa: E402
        McbotAuthError,
        McbotBridgeError,
        McbotClient,
        McbotUnreachable,
        SseFrame,
    )
    from plugin.plugins.mcbot_bridge.service import (  # noqa: E402
        DONE_TEXT_LIMIT,
        McbotService,
        is_authorization_question,
        normalise_base_url,
        task_looks_like_identifier,
    )


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeClient:
    """Stand-in for ``McbotClient``.

    The real client owns the HTTP session; here we record calls and return
    canned payloads or raise, so the service's own logic is what is under test.
    """

    def __init__(
        self,
        *,
        task_id: int | None = 7,
        ask_answer: str = "在挖石头",
        raise_on: dict[str, Exception] | None = None,
        status_payload: dict | None = None,
    ) -> None:
        self.base_url = "http://127.0.0.1:17897"
        self.token = ""
        self._task_id = task_id
        self._ask_answer = ask_answer
        self._raise_on = raise_on or {}
        self._status_payload = status_payload or {"in_game": True, "companion": "aimi"}
        self.calls: list[tuple] = []
        self._streaming = False
        self.last_error = ""
        self.session_id = ""
        self.cursor_resets = 0
        self.last_event_id = ""
        self.restored_cursors: list[str] = []
        self.cancel_result: dict = {"ok": True}

    # -- transport surface the service uses -------------------------------

    async def open(self) -> None:
        self.calls.append(("open",))
        self._streaming = True

    async def close(self) -> None:
        self.calls.append(("close",))
        self._streaming = False

    def set_token(self, token: str) -> None:
        self.token = token
        self.calls.append(("set_token", token))

    @property
    def is_streaming(self) -> bool:
        return self._streaming

    async def post_task(self, text: str, *, wait_s: int = 0) -> dict:
        self.calls.append(("post_task", text, wait_s))
        self._maybe_raise("post_task")
        return {"task_id": self._task_id, "done": False, "fragments": []}

    async def cancel_task(self, task_id=None) -> dict:
        self.calls.append(("cancel_task", task_id))
        self._maybe_raise("cancel_task")
        return self.cancel_result

    async def ask(self, text: str) -> dict:
        self.calls.append(("ask", text))
        self._maybe_raise("ask")
        return {"answer": self._ask_answer}

    async def answer(self, question_id: str, text: str) -> dict:
        self.calls.append(("answer", question_id, text))
        self._maybe_raise("answer")
        return {"ok": True}

    async def get_status(self) -> dict:
        self.calls.append(("get_status",))
        self._maybe_raise("get_status")
        return dict(self._status_payload)

    def note_session(self, session_id) -> None:
        if isinstance(session_id, str) and session_id:
            self.session_id = session_id

    def reset_cursor(self) -> None:
        self.cursor_resets += 1
        self.last_event_id = ""

    def restore_cursor(self, event_id) -> None:
        if isinstance(event_id, str) and event_id:
            self.last_event_id = event_id
            self.restored_cursors.append(event_id)

    def _maybe_raise(self, method: str) -> None:
        exc = self._raise_on.get(method)
        if exc is not None:
            raise exc


def _make_service(*, push_calls: list | None = None) -> tuple[McbotService, list]:
    captured = push_calls if push_calls is not None else []

    def fake_push(**kwargs):
        captured.append(kwargs)

    service = McbotService(logger=None, push_message_fn=fake_push)
    # Tests never touch the real runtime state directory; the cursor tests opt
    # back in with their own tmp path.
    service._cursor_path = None
    return service, captured


async def _settle() -> None:
    """Let queued tasks (watchdogs, resolve coroutines) run."""
    for _ in range(5):
        await asyncio.sleep(0)


def _frame(event: str, **payload) -> SseFrame:
    return SseFrame(event=event, data=json.dumps(payload), id="1")


_FRAME_SEQ = itertools.count(10_000)


def _fresh(event: str, **payload) -> SseFrame:
    """Like ``_frame`` but with a unique id, so replays are not deduped."""
    return SseFrame(event=event, data=json.dumps(payload), id=str(next(_FRAME_SEQ)))


# ---------------------------------------------------------------------------
# configure() / helpers
# ---------------------------------------------------------------------------


def test_configure_uses_shipped_defaults_when_keys_missing():
    service, _ = _make_service()
    service.configure({})
    status = service.get_status()
    assert status["base_url"] == "http://127.0.0.1:17897"
    assert status["stream_enabled"] is True


def test_configure_clamps_hostile_values():
    service, _ = _make_service()
    service.configure({
        "reconnect_interval_seconds": -5,
        "http_timeout_seconds": 0,
        "ask_timeout_seconds": 1,
        "task_wait_timeout_seconds": -1,
        "progress_min_interval_seconds": 0,
    })
    assert service._reconnect_interval == 0.5
    assert service._http_timeout == 0.5
    assert service._ask_timeout == 5.0
    assert service._task_wait_timeout == 5.0
    assert service._progress_min_interval == 1.0


def test_configure_ignores_wrongly_typed_values():
    service, _ = _make_service()
    service.configure({"stream_events": "yes", "base_url": 42, "game_dir": None})
    assert service._stream_events is True
    assert service._base_url == "http://127.0.0.1:17897"
    assert service._game_dir == ""


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("127.0.0.1:17897", "http://127.0.0.1:17897"),
        ("http://127.0.0.1:17897/", "http://127.0.0.1:17897"),
        ("http://127.0.0.1:17897///", "http://127.0.0.1:17897"),
        ("", "http://127.0.0.1:17897"),
        ("   ", "http://127.0.0.1:17897"),
        ("http://host:1/path", "http://host:1/path"),
    ],
)
def test_normalise_base_url(raw, expected):
    # A trailing slash would make httpx join paths into ``//v1/task``.
    assert normalise_base_url(raw) == expected


@pytest.mark.parametrize(
    "text,expected",
    [
        ("break_block", True),
        ("goToCoordinates(1, 2, 3)", True),
        ("move_to", True),
        ("去挖点木头", False),
        ("chop some wood", False),
        ("挖 three 个", False),
        ("", False),
    ],
)
def test_task_looks_like_identifier(text, expected):
    assert task_looks_like_identifier(text) is expected


# ---------------------------------------------------------------------------
# Single task slot
# ---------------------------------------------------------------------------


async def test_claim_takes_the_slot_and_reports_busy_after():
    service, _ = _make_service()
    first = await service.try_claim_pending("挖点木头")
    assert first is not None
    assert service.has_pending_task()
    assert await service.try_claim_pending("再来点") is None
    assert service.current_task_text() == "挖点木头"


async def test_overwrite_is_refused_inside_the_anti_thrash_floor():
    service, _ = _make_service()
    first = await service.try_claim_pending("挖点木头")
    # Same instant: the floor exists so a model cannot interrupt work it just
    # started by re-dispatching in the same breath.
    assert await service.try_claim_pending("改成挖铁", overwrite=True) is None
    assert service._pending is first


async def test_overwrite_after_the_floor_supersedes_the_old_task():
    service, _ = _make_service()
    client = _FakeClient(task_id=5)
    service._client = client
    first = await service.try_claim_pending("挖点木头")
    assert first is not None
    await service.dispatch_claimed(first)
    first.started_at -= 10.0  # age past the floor
    second = await service.try_claim_pending("改成挖铁", overwrite=True)
    assert second is not None
    assert ("cancel_task", 5) in client.calls
    assert first.event.is_set()
    # Superseded, not failed — being replaced is not a malfunction.
    assert first.result["status"] == "superseded"


async def test_overwrite_is_refused_when_the_old_task_cannot_be_cancelled():
    service, _ = _make_service()
    first = await service.try_claim_pending("挖点木头")
    assert first is not None
    first.started_at -= 10.0
    # No task id yet, so there is nothing to cancel: the new one must not start.
    assert await service.try_claim_pending("改成挖铁", overwrite=True) is None
    assert service._pending is first


async def test_stop_releases_a_waiting_slot():
    service, _ = _make_service()
    claimed = await service.try_claim_pending("挖点木头")
    await service.stop()
    assert claimed is not None and claimed.event.is_set()
    assert not service.has_pending_task()


async def test_watchdog_releases_the_slot_when_no_completion_arrives():
    service, pushes = _make_service()
    service._task_wait_timeout = 0.05
    claimed = await service.try_claim_pending("挖点木头")
    assert claimed is not None
    await asyncio.sleep(0.2)
    # Without the watchdog a lost `done` event would wedge the slot forever
    # and every later dispatch would answer "busy".
    assert service._pending is None
    assert any(call["coalesce_key"].startswith("mcbot_timeout:") for call in pushes)
    await service.stop()


# ---------------------------------------------------------------------------
# dispatch_claimed
# ---------------------------------------------------------------------------


async def test_dispatch_records_mcbot_task_id_and_returns_empty_on_success():
    service, _ = _make_service()
    client = _FakeClient(task_id=42)
    service._client = client
    claimed = await service.try_claim_pending("挖点木头")
    assert claimed is not None
    assert await service.dispatch_claimed(claimed) == {}
    assert claimed.task_id == 42
    # Fire-and-forget: the dialog LLM's turn must not wait for the action.
    assert ("post_task", "挖点木头", 0.0) in client.calls
    await service.stop()


async def test_dispatch_releases_the_slot_when_the_bridge_is_unreachable():
    service, _ = _make_service()
    service._client = _FakeClient(raise_on={"post_task": McbotUnreachable()})
    claimed = await service.try_claim_pending("挖点木头")
    assert claimed is not None
    envelope = await service.dispatch_claimed(claimed)
    assert envelope["is_error"] is True
    assert envelope["error"] == "UNREACHABLE"
    assert not service.has_pending_task()


async def test_dispatch_without_a_client_returns_a_friendly_error():
    service, _ = _make_service()
    claimed = await service.try_claim_pending("挖点木头")
    assert claimed is not None
    envelope = await service.dispatch_claimed(claimed)
    assert envelope["is_error"] is True
    assert envelope["error"] == "BRIDGE_NOT_STARTED"
    assert not service.has_pending_task()


# ---------------------------------------------------------------------------
# SSE → cue mapping
# ---------------------------------------------------------------------------


async def test_failed_tool_step_becomes_a_midway_read_cue_and_is_throttled():
    service, pushes = _make_service()
    failed = dict(tool="attack", ok=False)
    await service.handle_event(_fresh("progress", text="step one", **failed))
    await service.handle_event(_fresh("progress", text="step two", **failed))
    # A failed step is the only progress that reaches the dialog, and the
    # floor keeps a run of failures from flooding it.
    assert len(pushes) == 1
    assert pushes[0]["ai_behavior"] == "read"
    assert pushes[0]["coalesce_key"] == "mcbot_progress"
    assert pushes[0]["priority"] == 4
    assert pushes[0]["visibility"] == []
    assert "step one" in pushes[0]["parts"][0]["text"]


async def test_successful_tool_step_is_blind_and_panel_only():
    service, pushes = _make_service()
    await service.handle_event(_frame("progress", text="scan_area ✔ 我在", tool="scan_area", ok=True))
    assert len(pushes) == 1
    assert pushes[0]["ai_behavior"] == "blind"
    assert pushes[0]["visibility"] == ["hud"]


async def test_guardrail_and_job_progress_are_blind():
    service, pushes = _make_service()
    await service.handle_event(_fresh("progress", text="（护栏）已叫停"))
    await service.handle_event(_fresh("progress", text="第 3/7 格", job_id="j1", tool="fill"))
    assert [c["ai_behavior"] for c in pushes] == ["blind", "blind"]
    assert all(c["visibility"] == [] for c in pushes)


async def test_done_resolves_the_matching_task_and_responds():
    service, pushes = _make_service()
    service._client = _FakeClient(task_id=9)
    claimed = await service.try_claim_pending("挖点木头")
    assert claimed is not None
    await service.dispatch_claimed(claimed)

    await service.handle_event(_frame("done", task_id=9, text="挖到 3 个圆石", status="completed"))
    assert not service.has_pending_task()
    assert claimed.event.is_set()
    # v1.0 terminal vocabulary, not an invented "ok".
    assert claimed.result["status"] == "completed"

    completion = [c for c in pushes if c["coalesce_key"].startswith("mcbot_done:")]
    assert len(completion) == 1
    assert completion[0]["ai_behavior"] == "respond"
    assert completion[0]["priority"] == 7
    await service.stop()


@pytest.mark.parametrize("directive", ["恢复目标", "目标状态", "放弃目标"])
async def test_a_command_directive_is_narrated_as_a_receipt_not_as_finished(directive):
    """mcbot reports COMPLETED for these commands, but only as *accepted*.

    `恢复目标` then runs the goal under a new task id, and `目标状态` does no
    work at all — calling either "I finished" tells the owner a goal is met.
    """
    service, pushes = _make_service()
    service.set_lang("en")
    service._client = _FakeClient(task_id=12)
    claimed = await service.try_claim_pending(directive)
    assert claimed is not None
    await service.dispatch_claimed(claimed)

    await service.handle_event(
        _frame("done", task_id=12, text="Recovery accepted; the goal verifies the world first.",
               status="completed")
    )
    cues = [c for c in pushes if c["coalesce_key"].startswith("mcbot_done:")]
    assert len(cues) == 1
    text = cues[0]["parts"][0]["text"]
    assert "Command receipt" in text
    assert directive in text
    assert "I finished" not in text
    assert "I've stopped" not in text


async def test_a_failed_command_keeps_the_failure_frame():
    service, pushes = _make_service()
    service.set_lang("en")
    service._client = _FakeClient(task_id=12)
    claimed = await service.try_claim_pending("恢复目标")
    await service.dispatch_claimed(claimed)

    await service.handle_event(
        _frame("done", task_id=12, text="RECOVERY_EMPTY: nothing to restore.", status="failed")
    )
    cues = [c for c in pushes if c["coalesce_key"].startswith("mcbot_done:")]
    assert len(cues) == 1
    assert "didn't pull it off" in cues[0]["parts"][0]["text"]


async def test_an_ordinary_task_still_gets_the_finished_frame():
    service, pushes = _make_service()
    service.set_lang("en")
    service._client = _FakeClient(task_id=13)
    claimed = await service.try_claim_pending("collect three logs")
    await service.dispatch_claimed(claimed)

    await service.handle_event(
        _frame("done", task_id=13, text="Collected 3 logs.", status="completed")
    )
    text = [c for c in pushes if c["coalesce_key"].startswith("mcbot_done:")][0]["parts"][0]["text"]
    assert "I finished" in text


async def test_dispatch_sends_wait_s_as_an_integer():
    """The contract types ``wait_s`` as an integer; ``0.0`` only works by luck.

    mcbot strips trailing zeros before checking the scale, so a float slips
    through today — a stricter implementation answers 400.
    """
    service, _ = _make_service()
    client = _FakeClient(task_id=3)
    service._client = client
    claimed = await service.try_claim_pending("collect three logs")
    await service.dispatch_claimed(claimed)

    call = next(c for c in client.calls if c[0] == "post_task")
    assert call[2] == 0
    assert type(call[2]) is int
    assert json.dumps({"wait_s": call[2]}) == '{"wait_s": 0}'


async def test_a_long_done_report_reaches_the_model_intact():
    """The cue is the only channel for the outcome — the tool returns {}."""
    service, pushes = _make_service()
    service._client = _FakeClient(task_id=14)
    claimed = await service.try_claim_pending("build the wall")
    await service.dispatch_claimed(claimed)

    report = "row " + ("d" * 1200) + " tail-marker"
    await service.handle_event(_frame("done", task_id=14, text=report, status="completed"))
    text = [c for c in pushes if c["coalesce_key"].startswith("mcbot_done:")][0]["parts"][0]["text"]
    # Under the old 600-char cap this tail was silently lost.
    assert "tail-marker" in text


async def test_a_runaway_done_report_is_clipped_with_a_marker():
    service, pushes = _make_service()
    service.set_lang("en")
    service._client = _FakeClient(task_id=15)
    claimed = await service.try_claim_pending("build the wall")
    await service.dispatch_claimed(claimed)

    report = "z" * (DONE_TEXT_LIMIT + 500)
    await service.handle_event(_frame("done", task_id=15, text=report, status="completed"))
    text = [c for c in pushes if c["coalesce_key"].startswith("mcbot_done:")][0]["parts"][0]["text"]
    assert "rest is omitted" in text
    # The frame and the gag add their own text; the report itself is capped.
    assert text.count("z") == DONE_TEXT_LIMIT


async def test_done_for_another_task_leaves_our_slot_alone():
    service, _ = _make_service()
    service._client = _FakeClient(task_id=9)
    claimed = await service.try_claim_pending("挖点木头")
    assert claimed is not None
    await service.dispatch_claimed(claimed)

    # e.g. the user drove the companion via the in-game chat prefix.
    await service.handle_event(_frame("done", task_id=99, text="别的活做完了"))
    assert service.has_pending_task()
    await service.stop()


async def test_question_is_recorded_and_asks_the_user():
    service, pushes = _make_service()
    await service.handle_event(
        _frame("question", question_id="q3", text="要我把它烧掉吗", task_id=1)
    )
    assert service.get_status()["pending_questions"] == 1
    question = [c for c in pushes if c["coalesce_key"].startswith("mcbot_question:")]
    assert question and question[0]["ai_behavior"] == "respond"
    assert question[0]["priority"] == 8


async def test_companion_session_state_is_read_and_reprobes_status():
    service, pushes = _make_service()
    service._client = _FakeClient()
    await service.handle_event(_frame("state", task_id=0, text="同伴已召唤"))
    assert pushes and pushes[0]["ai_behavior"] == "read"
    assert pushes[0]["coalesce_key"] == "mcbot_state"
    assert ("get_status",) in service._client.calls
    assert service.companion == "aimi"


async def test_answered_state_is_read_and_never_throttled():
    service, pushes = _make_service()
    service._state_min_interval = 60.0
    await service.handle_event(_fresh("state", task_id=0, text="同伴已召唤"))
    await service.handle_event(_fresh("state", task_id=5, text="主人已回答 q3"))
    reads = [c for c in pushes if c["ai_behavior"] == "read"]
    assert len(reads) == 2


async def test_task_state_is_blind():
    service, pushes = _make_service()
    await service.handle_event(_frame("state", task_id=5, text="排队中"))
    assert pushes and pushes[0]["ai_behavior"] == "blind"


async def test_unknown_and_malformed_events_are_ignored():
    service, pushes = _make_service()
    await service.handle_event(_frame("invented_event", text="x"))
    await service.handle_event(SseFrame(event="progress", data="not json", id=""))
    assert pushes == []


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


async def test_answer_confirms_and_forgets_the_question():
    service, _ = _make_service()
    service._client = _FakeClient()
    await service.handle_event(_frame("question", question_id="q3", text="烧掉吗"))
    result = await service.answer("q3", "烧")
    assert isinstance(result, str)
    assert service.get_status()["pending_questions"] == 0


async def test_answer_on_an_expired_question_is_reported_as_such():
    service, _ = _make_service()
    service._client = _FakeClient(
        raise_on={"answer": McbotBridgeError("gone", status=404, code="BRIDGE_HTTP_ERROR")}
    )
    result = await service.answer("q-old", "烧")
    assert result["is_error"] is True
    assert result["error"] == "QUESTION_EXPIRED"


async def test_ask_returns_the_companions_answer_verbatim():
    service, _ = _make_service()
    service._client = _FakeClient(ask_answer="在挖石头")
    assert "在挖石头" in await service.ask("你在干嘛")


async def test_ask_falls_back_when_the_companion_stays_silent():
    service, _ = _make_service()
    service._client = _FakeClient(ask_answer="")
    result = await service.ask("你在干嘛")
    assert isinstance(result, str) and result


async def test_cancel_without_a_client_is_an_error():
    service, _ = _make_service()
    claimed = await service.try_claim_pending("挖点木头")
    assert claimed is not None
    result = await service.cancel()
    assert result["is_error"] is True


# ---------------------------------------------------------------------------
# Bridge contract v1.0 conformance
# ---------------------------------------------------------------------------


def _client_with_transport(handler):
    """A client wired to an in-process transport, for asserting request shape."""
    import httpx


    client = McbotClient("http://127.0.0.1:17897", "tok")
    client._http = httpx.AsyncClient(
        base_url="http://127.0.0.1:17897",
        headers={"Authorization": "Bearer tok"},
        transport=httpx.MockTransport(handler),
    )
    return client


async def test_cancel_all_targets_id_zero_not_a_bare_path():
    """v1.0 has no /v1/task/cancel route; cancel-all is task_id 0.

    The bare path this plugin used to send never existed and 404s.
    """
    seen: list[str] = []
    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return httpx.Response(200, json={"ok": True}, request=request)

    client = _client_with_transport(handler)
    await client.cancel_task()
    await client.cancel_task(12)
    await client.close()

    assert seen == ["/v1/task/0/cancel", "/v1/task/12/cancel"]


async def test_rest_error_code_is_parsed_from_the_envelope():
    """v1.0 error bodies carry a stable ``error_code`` the caller branches on."""
    import httpx


    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            413,
            json={"error": "body exceeds 65536 bytes", "error_code": "BODY_TOO_LARGE"},
            request=request,
        )

    client = _client_with_transport(handler)
    with pytest.raises(McbotBridgeError) as caught:
        await client.post_task("x" * 100)
    await client.close()

    assert caught.value.code == "BODY_TOO_LARGE"
    assert caught.value.status == 413


async def test_reset_cursor_clears_the_last_event_id():
    """Ids restart at 1 per bridge, so a stale cursor must be droppable."""

    client = McbotClient("http://127.0.0.1:17897", "tok")
    client._last_event_id = "30"
    client.reset_cursor()
    assert client.last_event_id == ""


async def test_a_failed_frame_holds_the_cursor_so_reconnect_replays_it():
    import httpx

    body = (
        'id: 1\nevent: progress\ndata: {"task_id": 1, "text": "a"}\n\n'
        'id: 2\nevent: progress\ndata: {"task_id": 1, "text": "b"}\n\n'
        'id: 3\nevent: progress\ndata: {"task_id": 1, "text": "c"}\n\n'
    )
    client = _client_with_transport(
        lambda request: httpx.Response(200, content=body.encode(), headers={"content-type": "text/event-stream"})
    )
    seen: list[str] = []

    async def handler(frame):
        seen.append(frame.id)
        if frame.id == "2":
            raise RuntimeError("consumer broke")

    await client.pump_events(handler)
    assert seen == ["1", "2", "3"]
    assert client.last_event_id == "1"


async def test_adopt_session_resets_the_cursor_when_the_session_changes():
    service, _ = _make_service()
    client = _FakeClient(
        status_payload={"contract_version": "1.0", "session_id": "sess-A", "in_game": True}
    )
    service._client = client

    await service._adopt_session()
    assert service._session_id == "sess-A"
    assert service._contract_version == "1.0"
    resets_after_first = client.cursor_resets

    # Same session (e.g. a config reload): cursor reused, no reset.
    await service._adopt_session()
    assert client.cursor_resets == resets_after_first

    # New world -> new bridge -> ids restart at 1.
    client._status_payload = {
        "contract_version": "1.0", "session_id": "sess-B", "in_game": True,
    }
    await service._adopt_session()
    assert service._session_id == "sess-B"
    assert client.cursor_resets == resets_after_first + 1


async def test_session_change_invalidates_the_in_flight_task_as_unknown():
    """A bridge that went away cannot report what happened; never claim success."""
    service, _ = _make_service()
    client = _FakeClient(status_payload={"session_id": "sess-A"})
    service._client = client
    await service._adopt_session()

    claimed = await service.try_claim_pending("挖点木头")
    assert claimed is not None

    client._status_payload = {"session_id": "sess-B"}
    await service._adopt_session()

    assert claimed.event.is_set()
    assert claimed.result["status"] == "unknown"
    assert not service.has_pending_task()


async def test_session_change_discards_open_questions():
    service, _ = _make_service()
    client = _FakeClient(status_payload={"session_id": "sess-A"})
    service._client = client
    await service._adopt_session()
    await service.handle_event(
        _frame("question", question_id="q1", task_id=1, text="烧掉吗", session_id="sess-A")
    )
    assert service.get_status()["pending_questions"] == 1

    client._status_payload = {"session_id": "sess-B"}
    await service._adopt_session()
    assert service.get_status()["pending_questions"] == 0


async def test_a_frame_from_another_session_stops_writing_to_old_state():
    """v1.0: session_id mismatch means stop attributing to the old session."""
    service, _ = _make_service()
    service._session_id = "sess-A"
    claimed = await service.try_claim_pending("挖点木头")
    assert claimed is not None

    await service.handle_event(
        _frame("done", task_id=1, text="别的工作", status="completed", session_id="sess-B")
    )
    # The old task must not be resolved by a different session's event.
    assert claimed.result is None or claimed.result.get("status") != "completed"


@pytest.mark.parametrize("status", ["failed"])
async def test_a_failed_done_is_never_framed_as_success(status):
    service, pushes = _make_service()
    claimed = await service.try_claim_pending("挖点木头")
    assert claimed is not None
    claimed.awaiting_id = False

    await service.handle_event(
        _frame("done", task_id=1, text="找不到路", status=status, session_id=service._session_id)
    )
    assert claimed.result["status"] == status
    cues = [c for c in pushes if c["coalesce_key"].startswith("mcbot_done:")]
    assert len(cues) == 1
    text = cues[0]["parts"][0]["text"]
    # The failure wording, not the "finished" one.
    assert "didn't pull it off" in text or "没做成" in text


@pytest.mark.parametrize("status", ["cancelled", "superseded"])
async def test_cancelled_and_superseded_dones_stay_silent(status):
    """Deliberate stops are not news — cue'ing them invites a retry storm."""
    service, pushes = _make_service()
    claimed = await service.try_claim_pending("挖点木头")
    assert claimed is not None
    claimed.awaiting_id = False

    await service.handle_event(
        _frame("done", task_id=1, text="已取消", status=status, session_id=service._session_id)
    )
    assert claimed.result["status"] == status
    assert not service.has_pending_task()
    assert [c for c in pushes if c["coalesce_key"].startswith("mcbot_done:")] == []


async def test_a_done_arriving_before_the_post_returns_is_still_grouped():
    """The bridge takes its cursor before submitting, so a fast done can win."""
    service, pushes = _make_service()
    client = _FakeClient(task_id=77)
    service._client = client

    claimed = await service.try_claim_pending("挖点木头")
    assert claimed is not None
    assert claimed.awaiting_id is True

    # Arrives while the POST is still in flight: parked, not mis-attributed.
    await service.handle_event(
        _frame("done", task_id=77, text="做完了", status="completed")
    )
    assert claimed.event.is_set() is False
    assert len(claimed.buffered) == 1

    await service.dispatch_claimed(claimed)
    assert claimed.awaiting_id is False
    assert claimed.event.is_set()
    assert claimed.result["status"] == "completed"
    assert any(c["coalesce_key"].startswith("mcbot_done:") for c in pushes)


async def test_an_unattributable_done_does_not_clear_our_slot():
    service, _ = _make_service()
    claimed = await service.try_claim_pending("挖点木头")
    assert claimed is not None
    claimed.task_id = 5
    claimed.awaiting_id = False

    await service.handle_event(
        _frame("done", task_id=99, text="别人的活", status="completed")
    )
    assert service.has_pending_task()
    assert not claimed.event.is_set()


async def test_unknown_error_codes_degrade_without_leaking_the_code():
    service, _ = _make_service()
    service.set_lang("en")
    envelope = service.error_envelope(
        McbotBridgeError("internal detail", status=500, code="SOME_FUTURE_CODE"),
        query="挖点木头",
    )
    text = json.dumps(envelope, ensure_ascii=False)
    # The machine code may ride along in the `error` field; the human-facing
    # message must not, and neither may the exception's own text.
    assert "SOME_FUTURE_CODE" not in envelope["output"]["error"]
    assert "internal detail" not in text
    assert envelope["output"]["error"]


async def test_known_error_codes_map_to_speakable_text():
    service, _ = _make_service()
    service.set_lang("en")
    envelope = service.error_envelope(
        McbotBridgeError("body exceeds 65536 bytes", status=413, code="BODY_TOO_LARGE")
    )
    assert "too long to pass along" in envelope["output"]["error"]


async def test_status_exposes_the_session_and_contract_version():
    service, _ = _make_service()
    service._client = _FakeClient(
        status_payload={"contract_version": "1.0", "session_id": "sess-A"}
    )
    await service._adopt_session()
    status = service.get_status()
    assert status["session_id"] == "sess-A"
    assert status["contract_version"] == "1.0"


# ---------------------------------------------------------------------------
# Privacy: raw transport detail must never reach the model
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "exc",
    [
        McbotUnreachable(),
        McbotAuthError(),
        McbotBridgeError("POST /v1/task timed out", code="BRIDGE_TIMEOUT"),
        McbotBridgeError("boom", status=500, code="BRIDGE_HTTP_ERROR"),
    ],
)
def test_error_envelope_carries_no_transport_detail(exc):
    service, _ = _make_service()
    envelope = service.error_envelope(exc, query="挖点木头")
    text = json.dumps(envelope, ensure_ascii=False)
    # Tool results persist in the conversation transcript, so a host, a port or
    # an endpoint path must never appear in them.
    for leak in ("127.0.0.1", "17897", "/v1/", "POST", "ConnectError", "timed out"):
        assert leak not in text
    assert envelope["is_error"] is True
    assert envelope["output"]["error"]


async def test_cancel_with_nothing_running_says_so():
    from mcbot_bridge_plugin import prompts

    service, _ = _make_service()
    assert await service.cancel(None) == prompts.t("CANCEL_NOTHING", lang=service._lang)


async def test_cancel_of_another_task_id_leaves_the_pending_task_running():
    service, _ = _make_service()
    client = _FakeClient(task_id=5)
    service._client = client
    claimed = await service.try_claim_pending("挖点木头")
    await service.dispatch_claimed(claimed)
    await service.cancel(9)
    assert ("cancel_task", 9) in client.calls
    assert service._pending is claimed
    assert not claimed.event.is_set()


async def test_cancel_refused_by_the_bridge_keeps_the_task_running():
    from mcbot_bridge_plugin import prompts

    service, _ = _make_service()
    client = _FakeClient(task_id=5)
    client.cancel_result = {"ok": False}
    service._client = client
    claimed = await service.try_claim_pending("挖点木头")
    await service.dispatch_claimed(claimed)
    assert await service.cancel(None) == prompts.t("CANCEL_REFUSED", lang=service._lang)
    assert service._pending is claimed


async def test_question_cue_keeps_the_full_text_without_a_metadata_channel():
    service, pushes = _make_service()
    long_text = "问" * 300
    await service.handle_event(
        _frame("question", question_id="q9", text=long_text, task_id=1)
    )
    cues = [c for c in pushes if c["coalesce_key"].startswith("mcbot_question:")]
    assert len(cues) == 1
    assert "metadata" not in cues[0]
    assert long_text in str(cues[0])


async def test_answer_without_an_id_binds_to_the_only_open_question():
    service, _ = _make_service()
    service._client = _FakeClient()
    await service.handle_event(_frame("question", question_id="q3", text="烧掉吗", task_id=1))
    result = await service.answer("", "烧")
    assert isinstance(result, str)
    assert ("answer", "q3", "烧") in service._client.calls


async def test_answer_without_an_id_is_refused_when_several_are_open():
    service, _ = _make_service()
    service._client = _FakeClient()
    await service.handle_event(_fresh("question", question_id="q1", text="a", task_id=1))
    await service.handle_event(_fresh("question", question_id="q2", text="b", task_id=1))
    result = await service.answer("", "烧")
    assert result["error"] == "AMBIGUOUS_QUESTION"
    assert not [c for c in service._client.calls if c[0] == "answer"]


async def test_answer_without_an_id_is_refused_when_none_are_open():
    service, _ = _make_service()
    service._client = _FakeClient()
    result = await service.answer("", "烧")
    assert result["error"] == "NO_OPEN_QUESTION"


async def test_explicit_answer_id_is_forwarded_to_the_bridge():
    service, _ = _make_service()
    service._client = _FakeClient()
    await service.answer("q42", "好")
    assert ("answer", "q42", "好") in service._client.calls


async def test_expired_questions_are_not_answerable_by_default():
    service, _ = _make_service()
    service._client = _FakeClient()
    await service.handle_event(_frame("question", question_id="q1", text="a", task_id=1))
    service._pending_questions["q1"]["asked_at"] -= 200
    result = await service.answer("", "烧")
    assert result["error"] == "NO_OPEN_QUESTION"


async def test_questions_are_forgotten_when_their_task_finishes():
    service, _ = _make_service()
    service._client = _FakeClient(task_id=9)
    claimed = await service.try_claim_pending("挖点木头")
    await service.dispatch_claimed(claimed)
    await service.handle_event(_frame("question", question_id="q1", text="a", task_id=9))
    await service.handle_event(_frame("done", task_id=9, text="挖好了", status="completed"))
    assert service.get_status()["pending_questions"] == 0
    await service.stop()


async def test_companion_name_is_only_taken_from_status_not_from_frames():
    service, _ = _make_service()
    service._client = _FakeClient(status_payload={"contract_version": "1.0"})
    await service.handle_event(_frame("progress", companion="ghost", text="砍树", tool="x", ok=False))
    assert service.companion == ""


async def test_companion_name_is_bracketed_into_cues():
    service, pushes = _make_service()
    await service._note_companion("aimi")
    await service.handle_event(_frame("progress", task_id=1, text="砍了三棵树"))
    cues = [c for c in pushes if c["coalesce_key"] == "mcbot_progress"]
    assert len(cues) == 1
    assert "（aimi）" in str(cues[0])


async def test_a_changed_companion_name_announces_the_new_body_once():
    service, pushes = _make_service()
    await service._note_companion("aimi")
    await service._note_companion("aimi")
    assert pushes == []
    await service._note_companion("luna")
    cues = [c for c in pushes if c["coalesce_key"] == "mcbot_body"]
    assert len(cues) == 1
    assert service.companion == "luna"


async def test_state_cues_are_throttled_by_the_minimum_interval():
    service, pushes = _make_service()
    service._client = _FakeClient()
    service._state_min_interval = 60.0
    await service.handle_event(_fresh("state", task_id=0, text="a"))
    await service.handle_event(_fresh("state", task_id=0, text="b"))
    assert len([c for c in pushes if c["ai_behavior"] == "read"]) == 1


def test_done_cue_carries_the_internal_state_gag():
    service, pushes = _make_service()
    service.set_lang("en")
    service._push_cue(
        "CUE_DONE",
        ai_behavior="respond",
        priority=7,
        coalesce_key="mcbot_done:x",
        text="mined 3 cobble",
        status="completed",
    )
    assert len(pushes) == 1
    text = pushes[0]["parts"][0]["text"]
    assert "mined 3 cobble" in text
    # The gag is why the character narrates in-world instead of reporting
    # "SSE" / "mcbot" / "tool" to the user.
    assert "Do NOT narrate internals" in text


def test_progress_cue_does_not_repeat_the_gag():
    service, pushes = _make_service()
    service.set_lang("en")
    service._push_cue(
        "CUE_PROGRESS",
        ai_behavior="read",
        priority=4,
        coalesce_key="mcbot_progress",
        text="mined 3 cobble",
    )
    assert "Do NOT narrate internals" not in pushes[0]["parts"][0]["text"]


# ---------------------------------------------------------------------------
# The open-question hint on mcbot_task
#
# Live 2026-10-13: the owner answered a relayed question, the model called
# mcbot_task instead of mcbot_answer, the dispatch was refused as busy, and the
# companion's question then timed out unanswered. The question cue's turn was
# long over by the time the owner replied, so nothing else in the model's
# context says an answer is expected — the redirect rides on this tool's own
# result, the one thing the model is certainly reading.
# ---------------------------------------------------------------------------


def test_has_open_question_ignores_expired_entries():
    service, _ = _make_service()
    service._pending_questions["q1"] = {
        "text": "打吗",
        "asked_at": time.time() - 9999.0,
    }
    assert service.has_open_question() is False


class _FacadeStub:
    """Just enough of the plugin facade for the tool body.

    The SDK decorators are identity stubs under the standalone harness, so the
    tool can be driven unbound without standing up a plugin context — which the
    conftest stub deliberately does not provide.
    """

    _lang = "zh"

    def __init__(self, service):
        self._service = service

    async def _ensure_service_started(self):
        return None


async def test_task_result_carries_the_open_question_hint(plugin_module):
    service, _ = _make_service()
    await service.handle_event(_frame("question", question_id="q1", text="打还是侦察"))
    await service.try_claim_pending("占着槽位")  # force the busy branch
    await _settle()

    result = await plugin_module.McbotBridgePlugin.mcbot_task(
        _FacadeStub(service), text="我们先侦查"
    )

    assert "mcbot_answer" in result


async def test_task_result_omits_the_hint_when_nothing_is_open(plugin_module):
    service, _ = _make_service()
    await service.try_claim_pending("占着槽位")
    await _settle()

    result = await plugin_module.McbotBridgePlugin.mcbot_task(
        _FacadeStub(service), text="我们先侦查"
    )

    assert "mcbot_answer" not in result


# ---------------------------------------------------------------------------
# Authorisation questions have to tell the owner how to answer
#
# The bridge matches the *whole* trimmed reply against 是/是的/同意/确认/允许/可以/
# 批准/yes/approve/confirm/ok. Live 2026-10-13: the relay worked and the model
# picked the right tool, but the owner's "就这个路线" was refused as
# unapproved, so the companion's route change died on a wording mismatch.
# ---------------------------------------------------------------------------


def test_authorisation_questions_are_recognised_by_the_bridges_markers():
    assert is_authorization_question("路线目的地 105, 68, 101；具体清单：place@105,78,95 air")
    assert is_authorization_question("要动的方块：石头×2")
    assert not is_authorization_question("你是要我过去清怪，还是只远远侦察不接触？")


async def test_authorisation_question_tells_the_owner_the_exact_word():
    service, pushes = _make_service()
    service.set_lang("zh")
    await service.handle_event(
        _frame(
            "question",
            question_id="q1",
            text="路线目的地 105, 68, 101；挖 0 格、放 11 格。具体清单：place@105,78,95 air",
        )
    )
    assert "确认" in pushes[0]["parts"][0]["text"]


async def test_ordinary_question_carries_no_authorisation_note():
    service, pushes = _make_service()
    service.set_lang("zh")
    await service.handle_event(
        _frame("question", question_id="q2", text="你是要我过去清怪，还是只远远侦察不接触？")
    )
    assert "确认" not in pushes[0]["parts"][0]["text"]


# ---------------------------------------------------------------------------
# The SSE cursor survives a restart
#
# Without Last-Event-ID the bridge replays its whole 200-frame ring. A plugin
# restart used to arrive with no cursor, so every reload re-narrated minutes of
# finished work: live 2026-10-13 20:00:07 pushed 15 stale cues in one second,
# and the character reported a job as finished that had not run.
# ---------------------------------------------------------------------------


async def test_remembered_cursor_is_resumed_after_a_restart(tmp_path):
    cursor = tmp_path / "data" / "cursor.json"
    service, _ = _make_service()
    service._cursor_path = cursor
    service._client = _FakeClient()
    service._session_id = "sess-A"
    service._client.last_event_id = "42"
    await service._maybe_persist_cursor()
    assert cursor.exists()

    # A fresh process probes status, sees the same session, and resumes.
    restarted, _ = _make_service()
    restarted._cursor_path = cursor
    restarted._client = _FakeClient()
    await restarted._sync_session("sess-A")

    assert restarted._client.restored_cursors == ["42"]
    assert restarted._client.cursor_resets == 0


async def test_remembered_cursor_is_dropped_when_the_session_changed(tmp_path):
    cursor = tmp_path / "data" / "cursor.json"
    service, _ = _make_service()
    service._cursor_path = cursor
    service._client = _FakeClient()
    service._session_id = "sess-A"
    service._client.last_event_id = "42"
    await service._maybe_persist_cursor()

    restarted, _ = _make_service()
    restarted._cursor_path = cursor
    restarted._client = _FakeClient()
    await restarted._sync_session("sess-B")

    assert restarted._client.restored_cursors == []
    assert restarted._client.cursor_resets == 1


async def test_no_cursor_is_written_when_no_frames_have_arrived(tmp_path):
    cursor = tmp_path / "data" / "cursor.json"
    service, _ = _make_service()
    service._cursor_path = cursor
    service._client = _FakeClient()
    service._session_id = "sess-A"
    await service._maybe_persist_cursor()
    assert not cursor.exists()
