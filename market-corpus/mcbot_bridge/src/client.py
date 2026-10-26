"""HTTP + SSE client for the local mcbot bridge.

mcbot (a Fabric mod, MC 1.21.11) exposes a loopback-only bridge while its
client is in a world:

* ``POST /v1/task``              ``{text, wait_s}``     -> ``{task_id, done, fragments[]}``
* ``POST /v1/task/{id}/cancel``                         -> ``{ok}``
* ``POST /v1/ask``               ``{text}``             -> ``{answer}`` (504 after 135s)
* ``POST /v1/answer``            ``{question_id,text}`` -> ``{ok}`` (404 if unknown)
* ``GET  /v1/status``                                   -> status JSON
* ``GET  /v1/events``                                   -> SSE: progress/done/question/state

Auth is ``Authorization: Bearer <token>`` where the token is the contents of
``<gameDir>/mcbot/bridge.token``. ``/v1/events`` also accepts ``?token=``
because a browser ``EventSource`` cannot set headers — we send the header
instead so the token never lands in a URL or an access log.

Scope of this module is **transport only**: it opens connections, speaks the
wire protocol, and normalizes failures into typed errors. It holds no game
state and no task bookkeeping, and it deliberately does not import the plugin
SDK, so it can be unit-tested standalone.

Connection *policy* (when to retry, how to re-acquire a token that does not
exist yet) lives in the service, not here: ``pump_events`` makes exactly one
connection attempt and returns when it ends, leaving the outer loop to its
caller. That split exists because mcbot generates ``bridge.token`` on the
*first world join* and tears the whole HTTP server down on DISCONNECT — so
"the token does not exist" and "the bridge is not listening" are both routine,
recoverable states that the caller must be able to re-evaluate every cycle.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, Optional

import httpx

# Read timeout on the SSE stream. The bridge emits a ``: ping`` comment every
# 15s, so a 60s read window is four missed heartbeats — long enough to shrug
# off a scheduling hiccup, short enough that a silently-dead TCP connection is
# detected and reconnected instead of hanging the reader forever.
SSE_READ_TIMEOUT_S = 60.0


class McbotBridgeError(Exception):
    """A bridge failure the service layer translates into user-facing text.

    ``status`` is the HTTP status when the failure came from a response, else
    ``None`` (transport error, timeout, malformed payload). ``code`` is the
    machine-readable ``{DOMAIN}_{ISSUE}`` tag the plugin returns to the LLM —
    never the raw transport detail, which may name the host and port.
    """

    def __init__(
        self,
        message: str,
        *,
        status: Optional[int] = None,
        code: str = "BRIDGE_ERROR",
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code


class McbotAuthError(McbotBridgeError):
    """401 — the token is absent, stale, or was rotated."""

    def __init__(self, message: str = "bridge rejected the bearer token") -> None:
        super().__init__(message, status=401, code="UNAUTHORIZED")


class McbotUnreachable(McbotBridgeError):
    """Could not open a connection — the bridge is down or not listening.

    Expected and routine rather than an error condition: mcbot only serves
    ``127.0.0.1:17897`` while its client is actually in a world.
    """

    def __init__(self, message: str = "mcbot bridge is not reachable") -> None:
        super().__init__(message, code="UNREACHABLE")


@dataclass(frozen=True, slots=True)
class SseFrame:
    """One parsed Server-Sent Event.

    ``data`` is the raw payload (multi-line payloads joined with ``\\n`` per
    the SSE spec). Use :meth:`json_data` to decode the bridge's JSON payloads.
    """

    event: str
    data: str
    id: str = ""

    def json_data(self) -> Optional[Dict[str, Any]]:
        """Decode ``data`` as a JSON object, or ``None`` if it is not one."""
        try:
            decoded = json.loads(self.data)
        except (json.JSONDecodeError, TypeError):
            return None
        return decoded if isinstance(decoded, dict) else None


OnSseFrame = Callable[[SseFrame], Awaitable[None]]


class McbotClient:
    """Bridge transport: five REST calls plus a single-connection SSE pump."""

    def __init__(
        self,
        base_url: str,
        token: str = "",
        *,
        http_timeout: float = 10.0,
        ask_timeout: float = 150.0,
        logger: Any = None,
    ) -> None:
        self.base_url = base_url
        self.token = token
        self.http_timeout = http_timeout
        # mcbot's /v1/ask holds the connection until it has an answer, giving
        # up with 504 after 135s. Our per-request budget must exceed that, so
        # we observe mcbot's own failure rather than inventing a client-side
        # timeout that races it.
        self.ask_timeout = ask_timeout
        self.logger = logger

        self._http: Optional[httpx.AsyncClient] = None
        self._sse_open = False
        self._last_error: str = ""
        self._last_event_id: str = ""
        # Bridge-instance identity, learned from status or any event frame.
        # v1.0 mints a fresh one per bridge run and restarts SSE ids at 1, so a
        # cursor from a previous session must never be replayed into a new one.
        self._session_id: str = ""

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def open(self) -> None:
        """Create the underlying HTTP client (idempotent)."""
        if self._http is not None:
            return
        self._http = httpx.AsyncClient(
            base_url=self.base_url,
            headers=self._auth_headers(),
            timeout=httpx.Timeout(self.http_timeout),
        )

    async def close(self) -> None:
        self._sse_open = False
        http = self._http
        self._http = None
        if http is not None:
            try:
                await http.aclose()
            except Exception:
                # Best-effort teardown; we are stopping either way.
                pass

    def set_token(self, token: str) -> None:
        """Swap the bearer token used for subsequent requests."""
        self.token = token
        http = self._http
        if http is not None:
            http.headers["Authorization"] = f"Bearer {token}"

    def _auth_headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}

    @property
    def is_streaming(self) -> bool:
        """True while the SSE event stream is established."""
        return self._sse_open

    @property
    def last_error(self) -> str:
        """Short tag for the last streaming failure (never user text)."""
        return self._last_error

    @property
    def last_event_id(self) -> str:
        return self._last_event_id

    @property
    def session_id(self) -> str:
        """Bridge session the last observed frame or status belonged to."""
        return self._session_id

    def note_session(self, session_id: Any) -> None:
        """Record the bridge session from a status payload."""
        if isinstance(session_id, str) and session_id:
            self._session_id = session_id

    def reset_cursor(self) -> None:
        """Drop the SSE cursor.

        Required whenever the bridge session changes: ids restart at 1, so a
        stale cursor makes the server replay ``id > cursor`` — an empty set —
        and the new session's events stay invisible until its ids climb past
        the old high-water mark. That failure is silent, which is why the
        cursor is reset on session change rather than trusted.
        """
        self._last_event_id = ""

    def restore_cursor(self, event_id: Any) -> None:
        """Re-seat the SSE cursor from outside this process.

        Only valid for a session the caller has confirmed is unchanged — see
        :meth:`reset_cursor` for what a stale id costs. Without this, every
        plugin restart arrives with no ``Last-Event-ID`` and the bridge replays
        its entire ring buffer as if those events had just happened.
        """
        if isinstance(event_id, str) and event_id:
            self._last_event_id = event_id

    # ------------------------------------------------------------------
    # REST
    # ------------------------------------------------------------------

    async def post_task(self, text: str, *, wait_s: int = 0) -> Dict[str, Any]:
        """Dispatch a task. ``wait_s=0`` returns as soon as mcbot queues it.

        ``wait_s`` is an **integer** in the contract (``BRIDGE.md``: optional,
        clamped to 0..120, non-integers rejected). Sending ``0.0`` happens to be
        accepted today only because the bridge strips trailing zeros before
        checking the scale; a stricter implementation would answer 400.
        """
        return await self._request_json(
            "POST", "/v1/task", json_body={"text": text, "wait_s": wait_s}
        )

    async def cancel_task(self, task_id: Optional[int] = None) -> Dict[str, Any]:
        """Cancel one task, or every task when ``task_id`` is omitted.

        v1.0 routes cancellation only through ``/v1/task/{id}/cancel``
        (``BridgeService.handle`` matches ``/v1/task/-?\\d+/cancel``); the bare
        ``/v1/task/cancel`` path this plugin used to send has never existed and
        falls through to a 404. "Cancel everything" is expressed as id ``0``.
        """
        target = 0 if task_id is None else int(task_id)
        return await self._request_json("POST", f"/v1/task/{target}/cancel")

    async def ask(self, text: str) -> Dict[str, Any]:
        """Ask the companion a question and wait for its answer."""
        return await self._request_json(
            "POST", "/v1/ask", json_body={"text": text}, timeout=self.ask_timeout
        )

    async def answer(self, question_id: str, text: str) -> Dict[str, Any]:
        """Answer a question the companion asked via a ``question`` event."""
        return await self._request_json(
            "POST",
            "/v1/answer",
            json_body={"question_id": question_id, "text": text},
        )

    async def get_status(self) -> Dict[str, Any]:
        """Fetch the bridge's status snapshot."""
        return await self._request_json("GET", "/v1/status")

    # ------------------------------------------------------------------
    # SSE
    # ------------------------------------------------------------------

    async def pump_events(self, on_event: OnSseFrame) -> None:
        """Open the event stream once and pump frames until it ends.

        Returns — never raises — on EOF or any transport failure. The caller's
        loop owns the retry cadence, so a dead bridge produces one log line
        per interval instead of an exception per attempt.
        """
        http = self._http
        if http is None:
            await self.open()
            http = self._http
        if http is None:
            return
        headers: Dict[str, str] = {}
        if self._last_event_id:
            # mcbot keeps a 200-event ring buffer and replays anything newer
            # than this id, so a reconnect does not lose the events that
            # arrived while we were away.
            headers["Last-Event-ID"] = self._last_event_id
        cursor_blocked = False
        try:
            timeout = httpx.Timeout(self.http_timeout, read=SSE_READ_TIMEOUT_S)
            async with http.stream(
                "GET", "/v1/events", headers=headers, timeout=timeout
            ) as response:
                if response.status_code == 401:
                    self._last_error = "UNAUTHORIZED"
                    self._log_warning("event stream rejected the bearer token")
                    await response.aread()
                    return
                response.raise_for_status()
                self._sse_open = True
                self._last_error = ""
                self._log_info("event stream connected")
                async for frame in self._iter_frames(response):
                    payload = frame.json_data()
                    if isinstance(payload, dict):
                        sid = payload.get("session_id")
                        if isinstance(sid, str) and sid:
                            self._session_id = sid
                    try:
                        await on_event(frame)
                    except Exception as exc:
                        # A misbehaving consumer must not tear down the stream:
                        # log and keep pumping. The cursor stops here for the
                        # rest of this connection, so a reconnect replays from
                        # the failed frame instead of skipping past it.
                        cursor_blocked = True
                        self._log_error(
                            "event handler failed for {!r}: {}: {}",
                            frame.event, type(exc).__name__, exc,
                        )
                        continue
                    # Advance only after the frame is fully handled: v1.0 asks
                    # the connector to move the cursor on success, not on
                    # receipt, so a mid-handling failure loses nothing.
                    if frame.id and not cursor_blocked:
                        self._last_event_id = frame.id
        except httpx.HTTPStatusError as exc:
            self._last_error = f"HTTP {exc.response.status_code}"
            self._log_warning(
                "event stream rejected: HTTP {}", exc.response.status_code
            )
        except (httpx.HTTPError, OSError) as exc:
            self._last_error = type(exc).__name__
            self._log_warning("event stream failed: {}: {}", type(exc).__name__, exc)
        except Exception as exc:
            # Never re-raise: an unexpected parser bug would otherwise kill
            # the caller's supervisor loop and silently end all updates.
            self._last_error = type(exc).__name__
            self._log_error(
                "event stream unexpected error: {}: {}", type(exc).__name__, exc
            )
        finally:
            self._sse_open = False

    @staticmethod
    async def _iter_frames(response: httpx.Response):
        """Parse an SSE byte stream into :class:`SseFrame` objects.

        Implements the wire format directly rather than adding a dependency
        (``httpx-sse`` is not in the project's set): a blank line terminates a
        frame, ``:`` prefixes a comment (mcbot's 15s heartbeat), and fields are
        ``name: value`` with one optional leading space.
        """
        event_type = ""
        data_lines: list[str] = []
        last_id = ""
        async for line in response.aiter_lines():
            if line == "":
                if data_lines:
                    yield SseFrame(
                        event=event_type or "message",
                        data="\n".join(data_lines),
                        id=last_id,
                    )
                event_type = ""
                data_lines = []
                continue
            if line.startswith(":"):
                continue  # heartbeat / comment
            field, _, value = line.partition(":")
            if value.startswith(" "):
                value = value[1:]
            if field == "event":
                event_type = value
            elif field == "data":
                data_lines.append(value)
            elif field == "id":
                last_id = value

    # ------------------------------------------------------------------
    # Request plumbing
    # ------------------------------------------------------------------

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        json_body: Optional[Dict[str, Any]] = None,
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Issue one REST call and decode a JSON object response.

        Every failure mode is normalized into a :class:`McbotBridgeError`
        subclass carrying a ``code`` the service can translate. ``httpx``
        exceptions never escape this method.

        The message deliberately carries the method and path but not the
        response body: bodies can echo user text, and this string is logged.
        """
        http = self._http
        if http is None:
            raise McbotUnreachable("bridge client is not started")
        kwargs: Dict[str, Any] = {}
        if json_body is not None:
            kwargs["json"] = json_body
        if timeout is not None:
            kwargs["timeout"] = httpx.Timeout(timeout)
        try:
            response = await http.request(method, path, **kwargs)
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            raise McbotUnreachable() from exc
        except httpx.TimeoutException as exc:
            raise McbotBridgeError(
                f"{method} {path} timed out", code="BRIDGE_TIMEOUT"
            ) from exc
        except (httpx.HTTPError, OSError) as exc:
            raise McbotBridgeError(
                f"{method} {path} failed: {type(exc).__name__}", code="BRIDGE_TRANSPORT"
            ) from exc

        if response.status_code == 401:
            raise McbotAuthError()
        if response.status_code >= 400:
            # v1.0 error bodies are ``{"error": ..., "error_code": ...}``. The
            # code is a stable category the caller can branch on; the message
            # is prose and is deliberately not propagated (it can echo request
            # detail, and this string is logged).
            code = "BRIDGE_HTTP_ERROR"
            try:
                body = response.json()
            except (json.JSONDecodeError, ValueError):
                body = None
            if isinstance(body, dict):
                raw_code = body.get("error_code")
                if isinstance(raw_code, str) and raw_code:
                    code = raw_code
            raise McbotBridgeError(
                f"{method} {path} returned HTTP {response.status_code}",
                status=response.status_code,
                code=code,
            )
        try:
            payload = response.json()
        except (json.JSONDecodeError, ValueError) as exc:
            raise McbotBridgeError(
                f"{method} {path} returned a non-JSON body", code="BRIDGE_BAD_PAYLOAD"
            ) from exc
        if not isinstance(payload, dict):
            raise McbotBridgeError(
                f"{method} {path} returned a non-object payload",
                code="BRIDGE_BAD_PAYLOAD",
            )
        return payload

    # ------------------------------------------------------------------
    # Logging helpers — silent when no logger is supplied, and never handed
    # task text or answers. Conversational content goes through ``print``
    # only; see the privacy note in the plugin README.
    # ------------------------------------------------------------------

    def _log_info(self, msg: str, *args: Any) -> None:
        self._emit("info", msg, *args)

    def _log_warning(self, msg: str, *args: Any) -> None:
        self._emit("warning", msg, *args)

    def _log_error(self, msg: str, *args: Any) -> None:
        self._emit("error", msg, *args)

    def _emit(self, level: str, msg: str, *args: Any) -> None:
        if self.logger is None:
            return
        try:
            getattr(self.logger, level)("[Mcbot] " + msg, *args)
        except Exception:
            # Log emission itself failing (rotation mid-write, closed
            # handler) must never surface as a caller-visible error.
            pass


__all__ = [
    "McbotClient",
    "McbotBridgeError",
    "McbotAuthError",
    "McbotUnreachable",
    "SseFrame",
    "OnSseFrame",
]
