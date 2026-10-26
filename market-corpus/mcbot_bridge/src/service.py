"""State and semantics for the mcbot bridge.

This module owns everything that is *about the game* rather than about the
wire: the single in-flight task slot, the reverse-question bookkeeping, and
the translation of bridge events into ``push_message`` cues the character can
act on.

Deliberately absent: any nudge/polling loop. The sibling
``game_agent_minecraft`` plugin needs one (~130 lines of ``_system_prompt_loop``)
because mc-agent never volunteers information, so the only way to keep the
model moving is to poll game state and prod it. mcbot *pushes* — its
``/v1/events`` stream delivers progress, reverse questions and completion as
they happen — so that entire layer is unnecessary here. Losing it is the main
structural win of this plugin over its sibling.

This module imports no plugin SDK types; ``push_message`` arrives as an
injected callable, which keeps the service unit-testable on its own.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from collections import deque
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from . import prompts
from .client import (
    McbotBridgeError,
    McbotClient,
    McbotUnreachable,
    SseFrame,
)

DEFAULT_BASE_URL = "http://127.0.0.1:17897"

# Bridge contract this plugin is written against. The bridge advertises it on
# status and on every event; anything else is a legacy-compatible bridge, not a
# v1.0 one, and must not be treated as passing v1.0 acceptance.
CONTRACT_VERSION = "1.0"

# Task terminal statuses. Only "completed" is success — cancelled and
# superseded mean the work stopped, not that it finished, and reporting either
# as success is the exact trap the sibling plugin documents.
TASK_STATUS_COMPLETED = "completed"
TASK_STATUSES_TERMINAL = ("completed", "failed", "cancelled", "superseded")
# Statuses that are non-events for narration: the task was replaced or stopped
# on purpose, and the LLM already knows. Matches the sibling plugin's
# [NO-SWITCH-CUE] decision — cue'ing them just triggers a retry storm.
TASK_STATUSES_SILENT = ("cancelled", "superseded")

# mcbot answers these three as *commands*, not as work. `AgentRunner` matches
# them literally and reports COMPLETED for all of them, but `BRIDGE.md` is
# explicit that a command's `completed` only means "accepted": `恢复目标` starts
# the real run under a *new* task_id, and `目标状态` performs no work at all.
# Narrating any of them as "I finished" tells the owner a goal is satisfied.
COMMAND_DIRECTIVES = ("恢复目标", "目标状态", "放弃目标")

# Safety cap on a done report before it reaches the model. The cue is the only
# channel for the outcome — the tool call itself returns an empty envelope — so
# this sits far above a normal report and only guards against a runaway one.
DONE_TEXT_LIMIT = 4000

# Minimum age before an overwrite is honored. Mirrors the sibling plugin's
# anti-thrash floor: without it, a model that re-dispatches in the same breath
# as its first call would interrupt work it just started.
OVERWRITE_MIN_SURVIVAL_S = 2.0

# Cap on remembered reverse questions. mcbot numbers them monotonically and
# expires them server-side; this is only a memory bound for the local cache.
MAX_PENDING_QUESTIONS = 32

# The bridge drops an unanswered question after 120s; a cached one older than
# that is a question nobody can answer any more.
QUESTION_TTL_S = 120.0

# Cap on frames parked while a dispatch is awaiting its task id. The window is
# one HTTP round-trip wide, so anything beyond this is the bridge misbehaving;
# the bound exists so a malformed stream cannot grow the list without limit.
_MAX_BUFFERED_EVENTS = 32

# Cap on remembered SSE frames and finished task ids. Only needs to cover one
# replay window after a reconnect, so it is a memory bound, not a history.
_SEEN_FRAME_LIMIT = 256

# How often the SSE cursor may be written to disk. Every frame would be a
# write per tool step on the event path; the cost of being behind is a few
# duplicated cues after a crash, against a whole replayed ring without it.
_CURSOR_SAVE_INTERVAL_S = 5.0

# Priority scale is repo-wide, HIGHER = more important:
#   alert 9 > question 8 > completion 7 > progress/state 4
PRIORITY_STATE = 4
PRIORITY_PROGRESS = 4
PRIORITY_COMPLETION = 7
PRIORITY_QUESTION = 8


# The bridge builds its authorisation questions server-side and both shapes end
# in a fixed marker (PathTask.java: "具体清单：" / "要动的方块："). Questions the
# executor's own brain asks are free-form and carry no marker. The distinction
# matters because an authorisation answer is matched as a *whole* keyword:
# TaskPolicy.affirmative() only accepts 是/是的/同意/确认/允许/可以/批准/yes/
# approve/confirm/ok, so a perfectly reasonable "就这个路线" is refused —
# observed live 2026-10-13, where the relay worked but the approval did not.
# If the bridge rewords these markers the note simply stops appearing.
_AUTHORIZATION_QUESTION_MARKERS = ("具体清单", "要动的方块")


def is_authorization_question(text: str) -> bool:
    """True when the bridge is asking the owner to approve a concrete change."""
    return any(marker in text for marker in _AUTHORIZATION_QUESTION_MARKERS)


def task_looks_like_identifier(text: str) -> bool:
    """True when a "task" is really a bare tool/function name.

    Models occasionally answer a request by naming a function instead of
    describing a goal — ``break_block``, ``move_to``, ``goToCoordinates(10,
    64, 20)``. Passed through, mcbot's brain treats the token as a directive and
    the model then sees its own identifier echoed in the progress stream, which
    is exactly the shape that produces dispatch loops. Refusing it with a hint
    costs one turn and removes the failure mode.

    Heuristic, not a parser: a short single token with no whitespace and no
    CJK, or anything with call syntax. Ordinary short requests ("过来") must
    still pass, hence the permissive length and the explicit CJK bail-out.
    """
    stripped = text.strip()
    if not stripped:
        return False
    if any("一" <= ch <= "鿿" for ch in stripped):
        return False
    if "(" in stripped and ")" in stripped:
        return True
    if any(ch.isspace() for ch in stripped):
        return False
    if len(stripped) > 40:
        return False
    return all(ch.isalnum() or ch in "_." for ch in stripped)


def _as_float(value: Any, default: float) -> float:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else default


def _as_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return bool(value)
    return default


def _as_str(value: Any, default: str = "") -> str:
    return value if isinstance(value, str) else default


def normalise_base_url(raw: Any) -> str:
    """Trim a configured base URL to something ``httpx`` can join paths onto.

    ``httpx`` appends the request path to ``base_url``, so a trailing slash or
    a stray path segment silently produces ``//v1/task`` or ``/foo/v1/task``.
    """
    text = _as_str(raw).strip()
    if not text:
        return DEFAULT_BASE_URL
    text = text.rstrip("/")
    if not text:
        return DEFAULT_BASE_URL
    if "://" not in text:
        text = "http://" + text
    return text


def default_game_dir() -> str:
    """Best-effort platform default Minecraft directory, or ``""``.

    Only a convenience for the common case; the operator can set ``game_dir``
    explicitly when Minecraft lives somewhere else (a launcher profile, a
    portable install, a different drive).
    """
    try:
        if os.name == "nt":
            appdata = os.environ.get("APPDATA")
            return str(Path(appdata) / ".minecraft") if appdata else ""
        home = os.path.expanduser("~")
        if sys.platform == "darwin":
            return str(Path(home) / "Library" / "Application Support" / "minecraft")
        return str(Path(home) / ".minecraft")
    except Exception:
        return ""


class PendingTask:
    """The single in-flight task slot.

    ``task_id`` is mcbot's own numeric id, returned synchronously by
    ``POST /v1/task``. That is a nicer handle than the sibling plugin's
    self-minted uuid: the id needed to correlate the later ``done`` event is
    known the moment the task is accepted, with no echo round-trip.
    """

    __slots__ = (
        "text", "task_id", "started_at", "event", "result", "watchdog",
        "awaiting_id", "buffered",
    )

    def __init__(self, text: str) -> None:
        self.text = text
        self.task_id: Optional[int] = None
        self.started_at = time.monotonic()
        self.event = asyncio.Event()
        self.result: Optional[Dict[str, Any]] = None
        # Set by the service when the slot is taken; cleared when it is
        # released, so a task that resolves early does not leave a sleeper
        # parked on the wait budget.
        self.watchdog: Optional[asyncio.Task[None]] = None
        # True between claiming the slot and learning the task id from the
        # POST response. The bridge takes its event cursor *before* submitting,
        # so a fast ``done`` can land on the stream while the HTTP call is
        # still in flight — those frames are parked here and replayed once the
        # id is known, instead of being matched against a task we cannot yet
        # identify.
        self.awaiting_id = True
        self.buffered: list[Dict[str, Any]] = []


class McbotService:
    """Bridge state machine: one task slot, one event stream, one token."""

    def __init__(
        self,
        *,
        logger: Any = None,
        push_message_fn: Callable[..., Any],
    ) -> None:
        self.logger = logger
        self._push_message = push_message_fn
        self._lang = prompts.DEFAULT_LANG

        # --- configuration (parsed by configure(), clamped on the way in) ---
        self._base_url = DEFAULT_BASE_URL
        self._game_dir = ""
        self._token_file = ""
        self._reconnect_interval = 5.0
        self._http_timeout = 10.0
        self._ask_timeout = 150.0
        self._task_wait_timeout = 900.0
        self._stream_events = True
        self._progress_min_interval = 6.0
        self._state_min_interval = 6.0

        # --- transport ---
        self._client: Optional[McbotClient] = None
        self._bridge_task: Optional[asyncio.Task[None]] = None
        self._stopping = False

        # --- token ---
        self._token = ""
        self._token_path = ""
        self._token_error = ""

        # --- bridge session (v1.0) ---
        # The bridge mints a session_id per run and restarts SSE ids at 1. When
        # it changes, anything we believed about the old session is void: the
        # cursor must reset or the new session's events stay invisible.
        self._session_id = ""
        self._contract_version = ""
        self._contract_warned = False
        self._companion = ""

        # --- SSE cursor, remembered across plugin restarts ---
        # A client that arrives without Last-Event-ID gets the bridge's whole
        # 200-frame ring replayed. Losing the cursor on restart therefore
        # re-narrated minutes of finished work as if it had just happened
        # (live 2026-10-13 20:00:07: a reload re-pushed 15 stale cues and the
        # character reported a job as finished that had not run). Keeping it
        # on disk is what the v1.0 contract asks for: the cursor is reused
        # while the session is unchanged.
        self._cursor_path: Optional[Path] = (
            Path(__file__).resolve().parent / "data" / "cursor.json"
        )
        self._persisted_cursor: Optional[tuple[str, str]] = None
        self._last_cursor_save_at = 0.0

        # --- game state ---
        self._pending: Optional[PendingTask] = None
        self._pending_lock = asyncio.Lock()
        self._watchdogs: set[asyncio.Task[None]] = set()
        self._pending_questions: Dict[str, Dict[str, Any]] = {}
        self._seen_frames: set[tuple] = set()
        self._seen_frames_order: deque[tuple] = deque()
        self._finished_tasks: set[str] = set()
        self._finished_order: deque[str] = deque()
        # task_id -> the directive we dispatched, so a `done` can be framed by
        # what was asked. Only the command directives below need it; ordinary
        # work never consults this map.
        self._task_directives: Dict[int, str] = {}
        self._task_directive_order: deque[int] = deque()
        self._last_progress_at = 0.0
        self._last_state_at = 0.0
        self._last_event_at = 0.0
        self._last_stream_error = ""

    # ==================================================================
    # Configuration
    # ==================================================================

    def set_lang(self, lang: str) -> None:
        self._lang = lang or prompts.DEFAULT_LANG

    def configure(self, cfg: Optional[Dict[str, Any]]) -> None:
        """Parse ``[mcbot]`` into typed, clamped fields.

        Every read has a default so a missing key degrades to the shipped
        behaviour instead of raising during startup.
        """
        cfg = cfg if isinstance(cfg, dict) else {}
        self._base_url = normalise_base_url(cfg.get("base_url"))
        self._game_dir = _as_str(cfg.get("game_dir")).strip()
        self._token_file = _as_str(cfg.get("token_file")).strip()
        self._reconnect_interval = max(
            0.5, _as_float(cfg.get("reconnect_interval_seconds"), 5.0)
        )
        self._http_timeout = max(
            0.5, _as_float(cfg.get("http_timeout_seconds"), 10.0)
        )
        self._ask_timeout = max(5.0, _as_float(cfg.get("ask_timeout_seconds"), 150.0))
        self._task_wait_timeout = max(
            5.0, _as_float(cfg.get("task_wait_timeout_seconds"), 900.0)
        )
        self._stream_events = _as_bool(cfg.get("stream_events"), True)
        self._progress_min_interval = max(
            1.0, _as_float(cfg.get("progress_min_interval_seconds"), 6.0)
        )
        self._state_min_interval = max(
            1.0, _as_float(cfg.get("state_min_interval_seconds"), 6.0)
        )

    # Fields whose change requires tearing the transport down and rebuilding
    # it. Pure-value changes (timeouts, intervals) apply in place.
    _TRANSPORT_KEYS = (
        "_base_url",
        "_game_dir",
        "_token_file",
        "_reconnect_interval",
        "_stream_events",
    )

    async def reload_config_live(self, cfg: Optional[Dict[str, Any]]) -> bool:
        """Apply new config; returns ``True`` when the transport was restarted."""
        before = tuple(getattr(self, key) for key in self._TRANSPORT_KEYS)
        self.configure(cfg)
        after = tuple(getattr(self, key) for key in self._TRANSPORT_KEYS)
        if before == after or self._bridge_task is None:
            # A token rotation on disk is picked up by the running loop's next
            # tick; nothing to rebuild.
            return False
        await self.stop()
        await self.start()
        return True

    # ==================================================================
    # Lifecycle — must run on the host's long-lived event loop
    # ==================================================================

    async def start(self) -> None:
        """Spawn the bridge supervisor. Idempotent."""
        existing = self._bridge_task
        if existing is not None and not existing.done():
            return
        self._stopping = False
        self._bridge_task = asyncio.create_task(
            self._bridge_loop(), name="mcbot_bridge.bridge_loop"
        )

    async def stop(self) -> None:
        """Cancel the supervisor, close the client, release any waiter."""
        self._stopping = True
        task = self._bridge_task
        self._bridge_task = None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                self._log_warning("bridge loop shutdown error: {}", type(exc).__name__)

        for watchdog in tuple(self._watchdogs):
            watchdog.cancel()
        self._watchdogs.clear()

        # Before the client goes away: a clean stop that forgot the cursor
        # would hand the next run the bridge's whole ring buffer.
        await self._flush_cursor()

        client = self._client
        self._client = None
        if client is not None:
            await client.close()

        pending = self._pending
        self._pending = None
        if pending is not None and not pending.event.is_set():
            pending.result = {"status": "cancelled", "query": pending.text}
            pending.event.set()

    async def _bridge_loop(self) -> None:
        """Own the connection policy: refresh the token, then stream.

        One iteration = one connection attempt plus the retry pause. The token
        is re-read every pass because mcbot writes ``bridge.token`` only on the
        first world join — a plugin enabled before Minecraft was ever launched
        must pick the token up later without a restart.
        """
        while not self._stopping:
            try:
                await self._refresh_token()
                if not self._token:
                    # No credentials yet. mcbot writes bridge.token on the
                    # first world join, so this is the normal state before a
                    # world has ever been entered — and also what a wrong
                    # game_dir looks like. Attempting a request anyway sends an
                    # empty bearer token, which httpx rejects as a malformed
                    # header, so the retry loop would otherwise spam a protocol
                    # error every interval instead of saying what is wrong.
                    self._log_info(
                        "no bridge token yet ({}); waiting {:.0f}s",
                        self._token_error or "unknown", self._reconnect_interval,
                    )
                    await asyncio.sleep(self._reconnect_interval)
                    continue
                await self._ensure_client()
                client = self._client
                if client is not None and self._stream_events:
                    # Reconcile the session *before* opening the stream: v1.0
                    # only lets the old cursor be reused when the session is
                    # unchanged, and this is where a changed one is detected.
                    await self._adopt_session()
                    await client.pump_events(self.handle_event)
                else:
                    await asyncio.sleep(self._reconnect_interval)
                    continue
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # Never let one bad pass kill the supervisor; the loop is the
                # only thing that can bring the stream back.
                self._log_warning(
                    "bridge loop error: {}: {}", type(exc).__name__, exc
                )

            if self._stopping:
                break
            try:
                await asyncio.sleep(self._reconnect_interval)
            except asyncio.CancelledError:
                break

    # ==================================================================
    # Token
    # ==================================================================

    def resolve_token_path(self) -> Optional[Path]:
        """Locate ``bridge.token``, or ``None`` when no directory is usable."""
        explicit = self._token_file
        if explicit:
            return Path(explicit).expanduser()
        game_dir = self._game_dir or default_game_dir()
        if not game_dir:
            return None
        return Path(game_dir).expanduser() / "mcbot" / "bridge.token"

    async def _refresh_token(self) -> None:
        path = self.resolve_token_path()
        if path is None:
            self._token = ""
            self._token_path = ""
            self._token_error = "NO_GAME_DIR"
            return
        self._token_path = str(path)
        try:
            # Disk read off the event loop — a stalled network drive must not
            # freeze every handler in the process.
            raw = await asyncio.to_thread(path.read_text, encoding="utf-8")
        except FileNotFoundError:
            self._token = ""
            self._token_error = "TOKEN_MISSING"
            return
        except OSError as exc:
            self._token = ""
            self._token_error = type(exc).__name__
            return
        token = raw.strip()
        if not token:
            self._token = ""
            self._token_error = "TOKEN_EMPTY"
            return
        if token != self._token:
            # Log the fact of rotation, never the value.
            self._log_info("bridge token loaded ({} chars)", len(token))
        self._token = token
        self._token_error = ""

    async def _ensure_client(self) -> None:
        client = self._client
        if client is None:
            client = McbotClient(
                self._base_url,
                self._token,
                http_timeout=self._http_timeout,
                ask_timeout=self._ask_timeout,
                logger=self.logger,
            )
            await client.open()
            self._client = client
            return
        client.set_token(self._token)

    # ==================================================================
    # SSE cursor, remembered between runs
    # ==================================================================

    def _read_cursor_file(self) -> Optional[tuple[str, str]]:
        path = self._cursor_path
        if path is None:
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        session = raw.get("session_id")
        event_id = raw.get("last_event_id")
        if not isinstance(session, str) or not session:
            return None
        if not isinstance(event_id, str) or not event_id:
            return None
        return session, event_id

    def _write_cursor_file(self, session: str, event_id: str) -> None:
        path = self._cursor_path
        if path is None:
            return
        tmp = path.with_name(path.name + ".tmp")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(
                json.dumps({"session_id": session, "last_event_id": event_id}),
                encoding="utf-8",
            )
            # Replaced, never truncated in place: a half-written cursor would
            # be read as "no cursor" at exactly the moment it is needed.
            os.replace(tmp, path)
        except OSError as exc:
            self._log_warning(
                "could not remember the SSE cursor: {}", type(exc).__name__
            )

    async def _maybe_persist_cursor(self) -> None:
        """Remember the cursor, at most once every few seconds.

        Called for every handled frame, but a write is only worth making when
        the id has moved and the interval has passed: this is disk IO on the
        event path. Being a few frames behind costs a couple of repeated cues
        after a crash; not persisting at all costs the whole replayed ring.
        """
        client = self._client
        if client is None or self._cursor_path is None or self._stopping:
            return
        session = self._session_id
        event_id = client.last_event_id
        if not session or not event_id:
            return
        if self._persisted_cursor == (session, event_id):
            return
        now = time.monotonic()
        if now - self._last_cursor_save_at < _CURSOR_SAVE_INTERVAL_S:
            return
        self._last_cursor_save_at = now
        self._persisted_cursor = (session, event_id)
        await asyncio.to_thread(self._write_cursor_file, session, event_id)

    async def _flush_cursor(self) -> None:
        """Write the cursor once more, so a clean stop does not replay."""
        client = self._client
        session = self._session_id
        event_id = client.last_event_id if client is not None else ""
        if not session or not event_id:
            return
        if self._persisted_cursor == (session, event_id):
            return
        self._persisted_cursor = (session, event_id)
        await asyncio.to_thread(self._write_cursor_file, session, event_id)

    async def _sync_session(self, session: str) -> None:
        """Adopt ``session``, reusing the cursor only where v1.0 allows it.

        A session that matches the one left on disk is a continuation, so the
        remembered ``Last-Event-ID`` is still valid and is restored — that is
        what keeps a plugin restart from being handed the bridge's whole ring
        buffer. Anything else is a new session whose ids restart at 1, where a
        stale cursor would make every event invisible.
        """
        if not session or session == self._session_id:
            return
        client = self._client
        saved = await asyncio.to_thread(self._read_cursor_file)
        if client is not None and saved is not None and saved[0] == session:
            self._session_id = session
            client.note_session(session)
            client.restore_cursor(saved[1])
            self._persisted_cursor = saved
            self._log_info(
                "bridge session unchanged; resuming the event stream at id={}",
                saved[1],
            )
            return
        await self._invalidate_session_state()
        self._session_id = session
        if client is not None:
            client.note_session(session)
            client.reset_cursor()

    # ==================================================================
    # Bridge session (v1.0)
    # ==================================================================

    async def _adopt_session(self) -> None:
        """Query status and reconcile the bridge session before streaming.

        v1.0 requires the connector to check status after any disconnect: only
        when the session is unchanged may the previous ``Last-Event-ID`` be
        reused. A new session invalidates everything the old one knew — its
        tasks are not resumed or re-dispatched, its questions are void, and the
        cursor resets because ids restart at 1.
        """
        client = self._client
        if client is None:
            return
        try:
            payload = await client.get_status()
        except McbotBridgeError:
            return

        version = _as_str(payload.get("contract_version"))
        self._contract_version = version
        if version != CONTRACT_VERSION and not self._contract_warned:
            self._contract_warned = True
            self._log_warning(
                "bridge advertises contract {!r}, this plugin targets {!r};"
                " treating it as legacy-compatible, not v1.0",
                version or "missing", CONTRACT_VERSION,
            )

        session = _as_str(payload.get("session_id"))
        client.note_session(session)
        companion = _as_str(payload.get("companion")).strip()
        if companion:
            await self._note_companion(companion)
        await self._sync_session(session)

    async def _invalidate_session_state(self) -> None:
        """Discard everything that belonged to the previous bridge session.

        The connected task is marked *unknown*, not failed and not succeeded:
        a bridge that went away cannot report what happened to work it was
        holding, and the contract forbids claiming either.
        """
        pending = self._pending
        if pending is not None:
            async with self._pending_lock:
                self._release_lock_free(pending)
            if not pending.event.is_set():
                pending.result = {
                    "status": "unknown",
                    "query": pending.text,
                    "reason": "session_changed",
                }
                pending.event.set()
        if self._pending_questions:
            self._pending_questions.clear()
        self._log_info("bridge session changed; previous session state discarded")

    async def try_claim_pending(
        self, text: str, *, overwrite: Any = False
    ) -> Optional[PendingTask]:
        """Atomically take the single task slot, or return ``None`` if busy.

        Check-and-claim runs under one lock so two concurrent ``mcbot_task``
        calls cannot both see an empty slot and both dispatch.
        """
        overwrite_flag = overwrite is True
        async with self._pending_lock:
            current = self._pending
            if current is not None:
                if not overwrite_flag:
                    self._log_info("claim refused: task slot busy")
                    return None
                age = time.monotonic() - current.started_at
                if age < OVERWRITE_MIN_SURVIVAL_S:
                    self._log_info("overwrite rejected: anti-thrash floor")
                    return None
        if current is not None and not await self._cancel_superseded(current):
            return None
        async with self._pending_lock:
            if self._pending is not current:
                return None
            if current is not None:
                # Being replaced is not a malfunction, so the old waiter gets
                # "superseded" rather than "failed".
                if not current.event.is_set():
                    current.result = {"status": "superseded", "query": current.text}
                    current.event.set()
                self._cancel_watchdog(current)
            claimed = PendingTask(text)
            self._pending = claimed
        self._spawn_watchdog(claimed)
        return claimed

    async def _cancel_superseded(self, current: PendingTask) -> bool:
        """Stop the in-flight task on the bridge before an overwrite claims the slot.

        Refuses the overwrite unless the bridge positively confirms the cancel:
        a task whose id is not known yet cannot be targeted, and a failed or
        unconfirmed cancel must not leave two tasks running in the game.
        """
        client = self._client
        if current.task_id is None or client is None:
            self._log_info("overwrite refused: in-flight task cannot be cancelled yet")
            return False
        try:
            payload = await client.cancel_task(current.task_id)
        except McbotBridgeError as exc:
            self._log_warning("overwrite refused: cancel failed ({})", exc.code)
            return False
        if payload.get("ok") is not True:
            self._log_warning("overwrite refused: cancel not acknowledged")
            return False
        return True

    def _spawn_watchdog(self, pending: PendingTask) -> None:
        """Release the slot if no ``done``/``state`` arrives in time.

        Without this a task whose completion event was lost (bridge restart
        mid-task, crashed companion) would wedge the slot forever and every
        later dispatch would report "busy".
        """
        task = asyncio.create_task(
            self._watch_pending(pending), name="mcbot_bridge.watchdog"
        )
        pending.watchdog = task
        self._watchdogs.add(task)

        def _done(finished: asyncio.Task[None]) -> None:
            self._watchdogs.discard(finished)
            if finished.cancelled():
                return
            exc = finished.exception()
            if exc is not None:
                self._log_warning(
                    "watchdog error: {}: {}", type(exc).__name__, exc
                )

        task.add_done_callback(_done)

    async def _watch_pending(self, pending: PendingTask) -> None:
        try:
            await asyncio.sleep(self._task_wait_timeout)
        except asyncio.CancelledError:
            return
        if pending.event.is_set():
            return
        async with self._pending_lock:
            if self._pending is not pending:
                return
            self._pending = None
        pending.result = {"status": "timeout", "query": pending.text}
        pending.event.set()
        self._log_info("task {} exceeded the wait budget", pending.task_id)
        # The outcome is unknown, not failed: the task may still be running in
        # the game, so the cue says so and nothing is re-dispatched.
        self._push_cue(
            "CUE_TIMEOUT",
            ai_behavior="respond",
            priority=PRIORITY_COMPLETION,
            coalesce_key=self._event_key("timeout", pending.task_id or "unknown"),
            text=pending.text[:200],
        )

    async def dispatch_claimed(self, pending: PendingTask) -> Dict[str, Any]:
        """Send a claimed task. Returns ``{}`` on success, an error envelope otherwise.

        Returns immediately: ``wait_s=0`` tells mcbot to queue the task and
        answer with its id, so the dialog LLM's turn is never held for the
        length of the in-game action. The outcome arrives over SSE.
        """
        client = self._client
        if client is None:
            await self._release(pending)
            return {
                "output": {"error": prompts.t("ERR_UNREACHABLE", lang=self._lang)},
                "is_error": True,
                "error": "BRIDGE_NOT_STARTED",
            }
        try:
            payload = await client.post_task(pending.text, wait_s=0)
        except McbotBridgeError as exc:
            await self._release(pending)
            return self.error_envelope(exc, query=pending.text)

        task_id = payload.get("task_id")
        if isinstance(task_id, int) and not isinstance(task_id, bool):
            pending.task_id = task_id
            self._remember_directive(task_id, pending.text)
        # The id is known now, so frames that arrived mid-POST can be
        # attributed. Replay before checking the window result below.
        pending.awaiting_id = False
        buffered, pending.buffered = pending.buffered, []
        for parked in buffered:
            await self._apply_done(parked)
        self._log_info("task dispatched (id={})", pending.task_id)

        # `done: true` means the task finished inside the response window; with
        # wait_s=0 that is an instantly-resolved directive and no `done` event
        # will follow. The contract puts the report in `fragments`, not `text`,
        # and carries `status` whenever done is true.
        if payload.get("done") is True and not pending.event.is_set():
            fragments = payload.get("fragments")
            text = _as_str(payload.get("text")).strip()
            if not text and isinstance(fragments, list):
                text = " ".join(str(part) for part in fragments if part)
            await self._apply_done({
                "task_id": pending.task_id,
                "text": text,
                "status": _as_str(payload.get("status")).strip() or TASK_STATUS_COMPLETED,
            })
        return {}

    def _release_lock_free(self, pending: PendingTask) -> None:
        if self._pending is pending:
            self._pending = None
        self._cancel_watchdog(pending)

    def _cancel_watchdog(self, pending: PendingTask) -> None:
        watchdog = pending.watchdog
        pending.watchdog = None
        if watchdog is not None and not watchdog.done():
            watchdog.cancel()

    async def _release(self, pending: PendingTask) -> None:
        async with self._pending_lock:
            self._release_lock_free(pending)

    # ==================================================================
    # Inbound events
    # ==================================================================

    async def handle_event(self, frame: SseFrame) -> None:
        """Route one SSE frame to a cue, and keep the task slot honest."""
        payload = frame.json_data()
        if payload is None:
            return
        # ``ev`` is contractually required on every payload and is the
        # authoritative discriminator; the SSE ``event:`` line is a mirror.
        event = _as_str(payload.get("ev")).strip() or frame.event

        session = _as_str(payload.get("session_id"))
        if session and session != self._session_id:
            # The bridge restarted between the status probe and the stream, or
            # status was unreachable. Adopt the session — reusing the cursor if
            # this is the same session we left, dropping it otherwise.
            await self._sync_session(session)

        # A replayed frame (reconnect after a failed handler) must not narrate
        # twice. Marked only after success, so a failed frame is retried.
        seen_key = (self._session_id, event, frame.id) if frame.id else None
        if seen_key is not None and seen_key in self._seen_frames:
            return

        self._last_event_at = time.monotonic()

        if event == "progress":
            await self._on_progress(payload)
        elif event == "done":
            await self._on_done(payload)
        elif event == "question":
            await self._on_question(payload)
        elif event == "state":
            await self._on_state(payload)
        # Unknown event types are ignored on purpose: the bridge may grow new
        # ones and a forward-compatible reader should not need a release.

        if seen_key is not None:
            self._remember_frame(seen_key)
            await self._maybe_persist_cursor()

    def _remember_frame(self, key: tuple) -> None:
        if len(self._seen_frames_order) >= _SEEN_FRAME_LIMIT:
            self._seen_frames.discard(self._seen_frames_order.popleft())
        self._seen_frames_order.append(key)
        self._seen_frames.add(key)

    def _event_key(self, kind: str, ident: Any) -> str:
        return f"mcbot_{kind}:{self._session_id or '-'}/{ident}"


    async def _on_progress(self, payload: Dict[str, Any]) -> None:
        text = _as_str(payload.get("text")).strip()
        if not text:
            return
        tool = _as_str(payload.get("tool")).strip()
        if tool and payload.get("ok") is False:
            # A failed step mid-task. The executor often recovers on its own, so
            # this is only worth the dialog as a midway note, and throttled.
            now = time.monotonic()
            if now - self._last_progress_at < self._progress_min_interval:
                return
            self._last_progress_at = now
            self._push_cue(
                "CUE_PROGRESS_MIDWAY",
                ai_behavior="read",
                priority=PRIORITY_PROGRESS,
                coalesce_key="mcbot_progress",
                text=text[:400],
            )
            return
        # Successful tool receipts, guardrail notices and long-running job
        # frames are narration for the panel, not something to say aloud.
        self._push_cue(
            "CUE_PROGRESS",
            ai_behavior="blind",
            priority=PRIORITY_PROGRESS,
            coalesce_key="mcbot_progress",
            visibility=["hud"] if tool and payload.get("ok") is True else [],
            text=text[:400],
        )

    async def _on_done(self, payload: Dict[str, Any]) -> None:
        pending = self._pending
        if pending is not None and pending.awaiting_id:
            # Park it: the bridge takes its cursor before submitting, so a fast
            # done can beat the POST response, and until we know our task id we
            # cannot tell it apart from another task's.
            if len(pending.buffered) < _MAX_BUFFERED_EVENTS:
                pending.buffered.append(payload)
            return
        await self._apply_done(payload)

    def _remember_directive(self, task_id: int, text: str) -> None:
        """Remember what we asked for, so a done can be framed by it.

        Bounded like the other seen-sets: a long session must not grow this
        without limit, and a forgotten directive only costs the generic cue.
        """
        if task_id in self._task_directives:
            return
        self._task_directives[task_id] = text
        self._task_directive_order.append(task_id)
        while len(self._task_directive_order) > _SEEN_FRAME_LIMIT:
            self._task_directives.pop(self._task_directive_order.popleft(), None)

    def _command_by_task(self, task_id: Any) -> str:
        """The command word when this task was one of the command directives."""
        if not isinstance(task_id, int) or isinstance(task_id, bool):
            return ""
        directive = self._task_directives.get(task_id, "").strip()
        return directive if directive in COMMAND_DIRECTIVES else ""

    def _clip_done_text(self, text: str) -> str:
        """Bound a done report, saying so when the tail is dropped."""
        if len(text) <= DONE_TEXT_LIMIT:
            return text
        return text[:DONE_TEXT_LIMIT] + prompts.t("DONE_TEXT_TRUNCATED", lang=self._lang)

    async def _apply_done(self, payload: Dict[str, Any]) -> None:
        """Release the matching slot and narrate the outcome.

        ``status`` decides the framing. v1.0 can report completed / failed /
        cancelled / superseded, and only the first is success — presenting a
        failure or a cancellation as "done" is the same defect the sibling
        plugin documents for mc-agent.
        """
        task_id = payload.get("task_id")
        text = _as_str(payload.get("text")).strip()
        status = _as_str(payload.get("status")).strip().lower() or TASK_STATUS_COMPLETED

        finished_key = None
        if isinstance(task_id, int) and not isinstance(task_id, bool):
            finished_key = self._event_key("done", task_id)
            if finished_key in self._finished_tasks:
                return
            self._finished_tasks.add(finished_key)
            self._finished_order.append(finished_key)
            if len(self._finished_order) > _SEEN_FRAME_LIMIT:
                self._finished_tasks.discard(self._finished_order.popleft())

        pending = self._pending
        if pending is not None:
            ours = pending.task_id is None or (
                isinstance(task_id, int)
                and not isinstance(task_id, bool)
                and task_id == pending.task_id
            )
            if ours:
                await self._resolve_pending(pending, text, status=status)
            # else: another (e.g. in-game `@bot`-driven) task finished — our
            # slot stays claimed, but the outcome is still worth narrating.

        if isinstance(task_id, int) and not isinstance(task_id, bool):
            self._prune_questions(task_id)

        if status in TASK_STATUSES_SILENT:
            # Replaced or stopped on purpose; the LLM caused it and knows.
            # Cue'ing it invites a re-dispatch of work already abandoned.
            return
        if not text:
            return
        # A command directive reports `completed` when it is merely *accepted*
        # (`BRIDGE.md`): 恢复目标 then runs under a new task id, and 目标状态
        # does no work. "I finished" would be a lie in both cases.
        command = self._command_by_task(task_id) if status == TASK_STATUS_COMPLETED else ""
        key = (
            "CUE_COMMAND_RECEIPT"
            if command
            else "CUE_DONE" if status == TASK_STATUS_COMPLETED else "CUE_DONE_FAILED"
        )
        self._push_cue(
            key,
            ai_behavior="respond",
            priority=PRIORITY_COMPLETION,
            coalesce_key=self._event_key("done", task_id if finished_key else "unknown"),
            text=self._clip_done_text(text),
            status=status,
            command=command,
        )

    async def _resolve_pending(
        self, pending: PendingTask, text: str, *, status: str = TASK_STATUS_COMPLETED
    ) -> None:
        async with self._pending_lock:
            self._release_lock_free(pending)
        if not pending.event.is_set():
            pending.result = {
                "status": status,
                "query": pending.text,
                "text": text,
                "task_id": pending.task_id,
            }
            pending.event.set()

    async def _on_question(self, payload: Dict[str, Any]) -> None:
        question_id = _as_str(payload.get("question_id")).strip()
        text = _as_str(payload.get("text")).strip()
        if not question_id or not text:
            return
        self._prune_questions()
        if len(self._pending_questions) >= MAX_PENDING_QUESTIONS:
            oldest = min(
                self._pending_questions,
                key=lambda qid: self._pending_questions[qid]["asked_at"],
            )
            self._pending_questions.pop(oldest, None)
        self._pending_questions[question_id] = {
            "text": text,
            "task_id": payload.get("task_id"),
            "asked_at": time.time(),
        }
        # Never truncated: an authorisation question must reach the owner in
        # full, or the owner approves a narrower scope than the bridge grants.
        # An authorisation question additionally needs the owner told *how* to
        # answer: the bridge matches the whole reply against a keyword list, so
        # a natural "就这个路线" is refused where a bare "确认" is accepted.
        self._push_cue(
            "CUE_QUESTION",
            ai_behavior="respond",
            priority=PRIORITY_QUESTION,
            coalesce_key=self._event_key("question", question_id),
            text=text,
            auth_note=(
                prompts.t("CUE_QUESTION_AUTH_NOTE", lang=self._lang)
                if is_authorization_question(text)
                else ""
            ),
        )

    def _prune_questions(self, task_id: Any = None, *, all_tasks: bool = False) -> None:
        """Drop expired questions, and those belonging to a finished task."""
        cutoff = time.time() - QUESTION_TTL_S
        for qid, item in tuple(self._pending_questions.items()):
            if item["asked_at"] < cutoff or all_tasks or (
                task_id is not None and item["task_id"] == task_id
            ):
                del self._pending_questions[qid]

    def has_open_question(self) -> bool:
        """True while at least one reverse question is still inside its window.

        Prunes first: a stale entry would otherwise make the caller claim we
        are waiting on an answer the bridge has already given up on.
        """
        self._prune_questions()
        return bool(self._pending_questions)

    async def _on_state(self, payload: Dict[str, Any]) -> None:
        text = _as_str(payload.get("text")).strip()
        if not text:
            return
        task_id = payload.get("task_id")
        if text.startswith("主人已回答"):
            self._push_cue(
                "CUE_STATE",
                ai_behavior="read",
                priority=PRIORITY_STATE,
                coalesce_key="mcbot_state",
                text=text[:400],
            )
            return
        if isinstance(task_id, int) and not isinstance(task_id, bool) and task_id == 0:
            # A companion session event (summoned, joined). Re-read status: the
            # name, not the frame, is the source of truth for who is in the body.
            await self.probe_status()
            now = time.monotonic()
            if now - self._last_state_at < self._state_min_interval:
                return
            self._last_state_at = now
            self._push_cue(
                "CUE_STATE",
                ai_behavior="read",
                priority=PRIORITY_STATE,
                coalesce_key="mcbot_state",
                text=text[:400],
            )
            return
        # queued / running / parked: no bearing on the conversation.
        self._push_cue(
            "CUE_STATE",
            ai_behavior="blind",
            priority=PRIORITY_STATE,
            coalesce_key="mcbot_state",
            visibility=[],
            text=text[:400],
        )

    async def _note_companion(self, name: str) -> None:
        previous = self._companion
        if name == previous:
            return
        self._companion = name
        if not previous:
            return
        self._push_cue(
            "CUE_BODY_CHANGED",
            ai_behavior="read",
            priority=PRIORITY_STATE,
            coalesce_key="mcbot_body",
            name=name,
        )

    # ==================================================================
    # Outbound tool support
    # ==================================================================

    async def ask(self, text: str) -> Dict[str, Any]:
        """Ask the companion a question directly (synchronous, slow)."""
        client = self._client
        if client is None:
            return {
                "output": {"error": prompts.t("ERR_UNREACHABLE", lang=self._lang)},
                "is_error": True,
                "error": "BRIDGE_NOT_STARTED",
            }
        try:
            payload = await client.ask(text)
        except McbotBridgeError as exc:
            return self.error_envelope(exc, query=text)
        answer = _as_str(payload.get("answer")).strip()
        if not answer:
            return prompts.t("ASK_NO_ANSWER", lang=self._lang)
        return prompts.t("ASK_ANSWER", lang=self._lang, answer=answer)

    async def answer(self, question_id: str, text: str) -> Dict[str, Any]:
        """Relay an answer to a reverse question the companion asked.

        An explicit ``question_id`` is forwarded as given: the bridge is the
        authority on whether it is still open. Without one, the answer binds to
        the only cached question still inside its window — never to the newest
        of several, which could be the wrong one.
        """
        client = self._client
        if client is None:
            return {
                "output": {"error": prompts.t("ERR_UNREACHABLE", lang=self._lang)},
                "is_error": True,
                "error": "BRIDGE_NOT_STARTED",
            }
        self._prune_questions()
        qid = _as_str(question_id).strip()
        if not qid:
            if not self._pending_questions:
                return self._plain_error("ANSWER_NO_OPEN_QUESTION", "NO_OPEN_QUESTION")
            if len(self._pending_questions) > 1:
                return self._plain_error("ANSWER_AMBIGUOUS_QUESTION", "AMBIGUOUS_QUESTION")
            qid = next(iter(self._pending_questions))
        try:
            await client.answer(qid, text)
        except McbotBridgeError as exc:
            if exc.status == 404:
                self._pending_questions.pop(qid, None)
                return self._plain_error("ANSWER_UNKNOWN_QUESTION", "QUESTION_EXPIRED")
            return self.error_envelope(exc, query=qid)
        self._pending_questions.pop(qid, None)
        return prompts.t("ANSWER_OK", lang=self._lang)

    def _plain_error(self, key: str, code: str) -> Dict[str, Any]:
        return {
            "output": {"error": prompts.t(key, lang=self._lang)},
            "is_error": True,
            "error": code,
        }

    async def cancel(self, task_id: Optional[int] = None) -> Dict[str, Any]:
        """Cancel one task by id, or everything when ``task_id`` is None or 0.

        Only the local slot whose id matches is cleared: cancelling a task the
        bridge started on its own must not release a dispatch that is still
        running.
        """
        cancel_all = not task_id
        if self._pending is None and cancel_all:
            return prompts.t("CANCEL_NOTHING", lang=self._lang)
        client = self._client
        if client is None:
            return {
                "output": {"error": prompts.t("ERR_UNREACHABLE", lang=self._lang)},
                "is_error": True,
                "error": "BRIDGE_NOT_STARTED",
            }
        try:
            payload = await client.cancel_task(None if cancel_all else task_id)
        except McbotBridgeError as exc:
            return self.error_envelope(exc, query=str(task_id or ""))
        if payload.get("ok") is not True:
            return prompts.t("CANCEL_REFUSED", lang=self._lang)
        pending = self._pending
        if pending is not None and (cancel_all or pending.task_id == task_id):
            await self._resolve_pending(pending, "", status="cancelled")
        self._prune_questions(None if cancel_all else task_id, all_tasks=cancel_all)
        return prompts.t("CANCEL_OK", lang=self._lang)

    # ==================================================================
    # Status
    # ==================================================================

    def is_connected(self) -> bool:
        """True while the SSE event stream is live."""
        client = self._client
        return bool(client is not None and client.is_streaming)

    def has_pending_task(self) -> bool:
        return self._pending is not None

    def current_task_text(self) -> str:
        pending = self._pending
        return pending.text if pending is not None else ""

    async def probe_status(self) -> Dict[str, Any]:
        """Fetch a live status snapshot; ``{}`` when unreachable."""
        client = self._client
        if client is None:
            return {}
        try:
            payload = await client.get_status()
        except McbotBridgeError as exc:
            self._last_stream_error = exc.code
            return {}
        companion = _as_str(payload.get("companion")).strip()
        if companion:
            await self._note_companion(companion)
        return payload

    @property
    def companion(self) -> str:
        return self._companion

    def get_status(self) -> Dict[str, Any]:
        """Local snapshot for the status entry — never performs I/O."""
        pending = self._pending
        client = self._client
        return {
            "connected": self.is_connected(),
            "companion": self._companion,
            "base_url": self._base_url,
            "session_id": self._session_id,
            "contract_version": self._contract_version,
            "token_path": self._token_path,
            "token_ok": bool(self._token),
            "token_error": self._token_error,
            "stream_enabled": self._stream_events,
            "stream_error": (
                client.last_error if client is not None else ""
            ) or self._last_stream_error,
            "pending_task": pending.text if pending is not None else "",
            "pending_task_id": pending.task_id if pending is not None else None,
            "pending_questions": len(self._pending_questions),
            "pending_question_items": [
                {"id": qid, "text": item.get("text", "")}
                for qid, item in self._pending_questions.items()
            ],
            "last_event_age_seconds": (
                round(time.monotonic() - self._last_event_at, 1)
                if self._last_event_at
                else None
            ),
        }

    # ==================================================================
    # Cue emission
    # ==================================================================

    def _push_cue(
        self,
        key: str,
        *,
        ai_behavior: str,
        priority: int,
        coalesce_key: str,
        visibility: Optional[list[str]] = None,
        **fmt: Any,
    ) -> None:
        """Format and push one cue. Never raises into the caller.

        ``visibility=[]`` means the user does not see the cue parts themselves:
        the dialog LLM absorbs them and, for ``respond`` cues, voices the
        outcome in its own words on the turn it opens. ``hud`` routes a cue to
        the panel only. The host reads nothing but the text parts, so anything
        the model must act on has to be written into the text.
        """
        # Bracketed so the body name reads as a locator for the model, which the
        # gag tells it not to voice. Empty when the name is unknown, so no
        # template needs a conditional.
        companion = self._companion
        fmt.setdefault("identity", f"（{companion}）" if companion else "")
        # Only the question cue uses it; defaulting here keeps every other
        # template's str.format() from raising on an unused placeholder.
        fmt.setdefault("auth_note", "")
        try:
            text = prompts.t(
                key, lang=self._lang, internal_gag=prompts.gag(self._lang), **fmt
            )
        except Exception as exc:
            self._log_warning("cue {} could not be formatted: {}", key, type(exc).__name__)
            return
        if not text:
            return
        try:
            self._push_message(
                source="mcbot_bridge",
                visibility=list(visibility or []),
                ai_behavior=ai_behavior,
                parts=[{"type": "text", "text": text}],
                priority=priority,
                coalesce_key=coalesce_key,
            )
        except Exception as exc:
            # Delivery is best-effort; a failure here must not corrupt the
            # event pipeline that called us.
            self._log_warning("cue {} push failed: {}", key, type(exc).__name__)
            return
        # Logged on success too: without this the only evidence that a cue left
        # the plugin lives in the host's log, which makes any "the character
        # never mentioned it" report impossible to diagnose from this side.
        # Key/behaviour/size only — the body is conversational content and is
        # never written to the log.
        #
        # INFO, not DEBUG: the first run of this line was written at debug and
        # was therefore invisible at the default level during a real test, which
        # defeats its purpose. The volume is bounded by the same throttles that
        # bound the cues themselves, so it is affordable.
        self._log_info(
            "cue {} pushed ({} prio={} {} chars)",
            key, ai_behavior, priority, len(text),
        )

    def error_envelope(self, exc: McbotBridgeError, *, query: str = "") -> Dict[str, Any]:
        """Translate a bridge failure into a model-facing error envelope.

        ``output`` carries something the character can say out loud; ``error``
        carries the machine code. The exception's own text is never included —
        the model writes tool results into conversation history permanently, so
        a host name, a port or the bridge's operator-facing prose would be
        persisted in the transcript.
        """
        if isinstance(exc, McbotUnreachable):
            message = prompts.t("ERR_UNREACHABLE", lang=self._lang)
        elif exc.code in prompts.BRIDGE_ERROR_MESSAGES:
            message = prompts.bridge_error(exc.code, lang=self._lang)
        else:
            # Unknown category (the bridge may add codes): degrade to a generic
            # line rather than leaking the code itself.
            message = prompts.bridge_error(
                exc.code, lang=self._lang, status=exc.status
            )
        output: Dict[str, Any] = {"error": message}
        if query:
            output["query"] = query[:200]
        return {"output": output, "is_error": True, "error": exc.code}

    # ==================================================================
    # Logging — metadata only. Task text, answers and reverse questions are
    # conversational content and never go through the logger.
    # ==================================================================

    def _log_info(self, msg: str, *args: Any) -> None:
        self._emit("info", msg, *args)

    def _log_warning(self, msg: str, *args: Any) -> None:
        self._emit("warning", msg, *args)

    def _emit(self, level: str, msg: str, *args: Any) -> None:
        if self.logger is None:
            return
        try:
            getattr(self.logger, level)("[Mcbot] " + msg, *args)
        except Exception:
            pass


__all__ = [
    "McbotService",
    "PendingTask",
    "DEFAULT_BASE_URL",
    "default_game_dir",
    "normalise_base_url",
    "task_looks_like_identifier",
]
