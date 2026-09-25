"""Conversation-scoped BrowserSkill session ownership and cleanup."""

from __future__ import annotations

import asyncio
import hashlib
import time
import uuid
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

from .bsk_client import BskClient, BskCommandError


class _LoopLock:
    """Lazily creates a lock for the current plugin event loop."""

    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lock: asyncio.Lock | None = None

    def get(self) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        if self._loop is not loop:
            if self._lock is not None and self._lock.locked():
                raise RuntimeError("BrowserSkill lock cannot move loops while held")
            self._loop = loop
            self._lock = asyncio.Lock()
        assert self._lock is not None
        return self._lock


@dataclass(slots=True)
class ChatBrowserSession:
    conversation_id: str
    bsk_session_id: str
    browser_id: str
    reusable: bool
    adoption_key: str = ""
    borrowed_tab_ids: set[int] = field(default_factory=set)
    current_tab_id: int | None = None
    current_url: str = ""
    current_title: str = ""
    last_observation: str = ""
    last_observation_at: float = 0.0
    last_used_at: float = field(default_factory=time.time)
    control_owner: str = "agent"


class SessionManager:
    def __init__(
        self,
        client: BskClient,
        *,
        logger: Any = None,
        debug_enabled: bool = True,
    ) -> None:
        self.client = client
        self.logger = logger
        self.debug_enabled = debug_enabled
        self._sessions: dict[str, ChatBrowserSession] = {}
        self._global_lock = _LoopLock()
        self._keepalive_tasks: dict[str, asyncio.Task[None]] = {}
        self._handoff_tasks: dict[str, asyncio.Task[None]] = {}

    @property
    def sessions(self) -> dict[str, ChatBrowserSession]:
        return dict(self._sessions)

    @staticmethod
    def _tag(value: str) -> str:
        return hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:10]

    def _debug(self, event: str, **fields: Any) -> None:
        if self.logger is not None and self.debug_enabled:
            self.logger.debug("BrowserSkill session event={} data={}", event, fields)

    def find(self, conversation_id: str | None = None) -> ChatBrowserSession | None:
        """Find a tracked session without starting or mutating browser state."""
        key = str(conversation_id or "").strip()
        if key:
            return self._sessions.get(key)
        unique = {item.bsk_session_id: item for item in self._sessions.values()}
        if len(unique) == 1:
            return next(iter(unique.values()))
        return None

    @asynccontextmanager
    async def execution(self) -> AsyncIterator[None]:
        async with self._global_lock.get():
            yield

    async def get_or_create(
        self,
        *,
        conversation_id: str | None,
        browser_id: str,
        reuse_existing: bool = True,
        adoption_key: str = "",
    ) -> ChatBrowserSession:
        adoption_key = str(adoption_key or "").strip()
        reusable = bool(str(conversation_id or "").strip())
        key = str(conversation_id).strip() if reusable else f"one-shot:{uuid.uuid4().hex}"
        existing = self._sessions.get(key)
        if existing is not None:
            if await self._is_live(existing.bsk_session_id):
                await self.acquire_for_agent(existing)
                if key == "browser-skill:main-dialog" and adoption_key:
                    existing.adoption_key = adoption_key
                existing.last_used_at = time.time()
                self._debug(
                    "reuse_exact",
                    conversation=self._tag(key),
                    session=self._tag(existing.bsk_session_id),
                )
                return existing
            # A session that disappeared from daemon status can still have a
            # long-running idle request-help peer. Cancel that handoff before
            # forgetting the stale session; this releases only the handoff and
            # does not issue session stop or close the browser window.
            await self.stop_idle_handoff(existing)
            await self.stop_keepalive(existing)
            self._sessions.pop(key, None)

        if reuse_existing:
            # Older native-tool callbacks may lack role/conversation context and
            # use this one technical placeholder. A later scoped fallback may
            # adopt it only when both calls carry the same user-request key.
            # Browser identity alone is not enough to cross a scope boundary.
            placeholder_key = "browser-skill:main-dialog"
            candidate = self._sessions.get(placeholder_key)
            if (
                key != placeholder_key
                and candidate is not None
                and candidate.browser_id == browser_id
                and adoption_key
                and candidate.adoption_key == adoption_key
            ):
                if await self._is_live(candidate.bsk_session_id):
                    await self.acquire_for_agent(candidate)
                    self._sessions.pop(placeholder_key, None)
                    candidate.conversation_id = key
                    candidate.reusable = reusable
                    candidate.last_used_at = time.time()
                    self._sessions[key] = candidate
                    self._debug(
                        "reuse_placeholder",
                        conversation=self._tag(key),
                        session=self._tag(candidate.bsk_session_id),
                    )
                    return candidate
                await self.stop_idle_handoff(candidate)
                await self.stop_keepalive(candidate)
                self._sessions.pop(placeholder_key, None)

        payload = await self.client.start_session(browser_id)
        session = ChatBrowserSession(
            conversation_id=key,
            bsk_session_id=str(payload["session_id"]),
            browser_id=str(payload.get("browser_instance_id") or browser_id),
            reusable=reusable,
            adoption_key=adoption_key,
        )
        self._sessions[key] = session
        self._debug(
            "created",
            conversation=self._tag(key),
            session=self._tag(session.bsk_session_id),
            browser=self._tag(session.browser_id),
            reusable=session.reusable,
        )
        return session

    async def _is_live(self, session_id: str) -> bool:
        status: dict[str, Any] | None = None
        last_error: Exception | None = None
        for _ in range(2):
            try:
                status = await self.client.status()
                break
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                last_error = exc
        if status is None:
            # A transient diagnostic failure is not evidence that the session
            # disappeared. Starting a replacement here would leak the old
            # Agent Window and create a second one.
            assert last_error is not None
            raise last_error
        sessions = status.get("sessions") if isinstance(status.get("sessions"), list) else []
        return any(
            isinstance(item, dict) and str(item.get("session_id") or "") == session_id
            for item in sessions
        )

    def track_borrowed(self, session: ChatBrowserSession, tab_id: int) -> None:
        session.borrowed_tab_ids.add(int(tab_id))

    def untrack_borrowed(self, session: ChatBrowserSession, tab_id: int) -> None:
        session.borrowed_tab_ids.discard(int(tab_id))

    async def return_borrowed(self, session: ChatBrowserSession) -> None:
        for tab_id in list(session.borrowed_tab_ids):
            try:
                await self.client.tab_return(session.bsk_session_id, tab_id)
            except Exception as exc:
                if self.logger is not None:
                    self.logger.warning(
                        "BrowserSkill could not return borrowed tab {}: {}",
                        tab_id,
                        type(exc).__name__,
                    )
            else:
                session.borrowed_tab_ids.discard(tab_id)

    async def close_session(self, session: ChatBrowserSession) -> None:
        await self.stop_idle_handoff(session)
        await self.stop_keepalive(session)
        await self.return_borrowed(session)
        self._sessions.pop(session.conversation_id, None)
        self._debug("closing", session=self._tag(session.bsk_session_id))
        try:
            await self.client.stop_session(session.bsk_session_id)
        except Exception as exc:
            if self.logger is not None:
                self.logger.warning(
                    "BrowserSkill session cleanup failed for {}: {}",
                    session.bsk_session_id,
                    type(exc).__name__,
                )

    async def preserve_session(
        self,
        session: ChatBrowserSession,
        *,
        interval_seconds: float,
        release_control: bool = False,
    ) -> bool:
        """End task ownership while keeping its Agent Window reusable."""
        await self.return_borrowed(session)
        if not session.reusable:
            await self.close_session(session)
            return False
        session.last_used_at = time.time()
        if release_control and await self.release_to_user(session):
            self._debug(
                "preserved_user_control",
                session=self._tag(session.bsk_session_id),
            )
        else:
            self.start_keepalive(session, interval_seconds=interval_seconds)
            self._debug("preserved", session=self._tag(session.bsk_session_id))
        return True

    async def release_to_user(self, session: ChatBrowserSession) -> bool:
        """Keep a reusable session alive while yielding its window to the user."""
        await self.stop_keepalive(session)
        existing = self._handoff_tasks.get(session.bsk_session_id)
        if existing is not None and not existing.done():
            session.control_owner = "user"
            return True
        spawn_peer = getattr(self.client, "spawn_peer", None)
        if not callable(spawn_peer):
            return False
        peer = spawn_peer()
        task = asyncio.create_task(
            self._idle_handoff_loop(session, peer),
            name=f"browser-skill-user-control-{session.bsk_session_id}",
        )
        self._handoff_tasks[session.bsk_session_id] = task
        session.control_owner = "user"

        def discard(done: asyncio.Task[None]) -> None:
            if self._handoff_tasks.get(session.bsk_session_id) is done:
                self._handoff_tasks.pop(session.bsk_session_id, None)

        task.add_done_callback(discard)
        # Give the request-help subprocess a scheduling turn before the task
        # result is returned to the host.
        await asyncio.sleep(0)
        return True

    async def acquire_for_agent(self, session: ChatBrowserSession) -> None:
        """End idle human handoff before issuing the next agent command."""
        stopped_handoff = await self.stop_idle_handoff(session)
        await self.stop_keepalive(session)
        if stopped_handoff:
            await self._wait_for_agent_slot(session)
        session.control_owner = "agent"
        self._debug("agent_control", session=self._tag(session.bsk_session_id))

    async def stop_idle_handoff(self, session: ChatBrowserSession) -> bool:
        task = self._handoff_tasks.pop(session.bsk_session_id, None)
        if task is None or task.done():
            return False
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        return True

    async def _wait_for_agent_slot(self, session: ChatBrowserSession) -> None:
        """Verify daemon and extension finished returning control."""
        deadline = time.monotonic() + 6.0
        last_error: BskCommandError | None = None
        while True:
            try:
                await self.client.tab_list(session.bsk_session_id, scope="agent")
                return
            except BskCommandError as exc:
                if not exc.is_session_busy:
                    raise
                last_error = exc
                if time.monotonic() >= deadline:
                    self._debug(
                        "agent_control_blocked",
                        session=self._tag(session.bsk_session_id),
                    )
                    raise last_error
                await asyncio.sleep(0.25)

    async def _idle_handoff_loop(
        self,
        session: ChatBrowserSession,
        client: BskClient,
    ) -> None:
        current = asyncio.current_task()
        while self._handoff_tasks.get(session.bsk_session_id) is current:
            try:
                payload = await client.request_help(
                    session.bsk_session_id,
                    title="浏览器已交给你",
                    prompt=(
                        "Browser Agent 当前没有执行任务，你可以直接操作此窗口。"
                        "下一次任务开始时插件会自动收回控制并重新读取页面。"
                    ),
                    targets=[],
                    timeout_seconds=86400,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                session.control_owner = "agent"
                if self.logger is not None:
                    self.logger.warning(
                        "BrowserSkill idle user handoff stopped for {}: {}",
                        session.bsk_session_id,
                        type(exc).__name__,
                    )
                return
            if str(payload.get("outcome") or "").lower() == "disabled":
                session.control_owner = "agent"
                if self.logger is not None:
                    self.logger.warning(
                        "BrowserSkill idle user handoff is disabled for {}",
                        session.bsk_session_id,
                    )
                return
            # If the user explicitly returns control while there is still no
            # task, immediately yield it again.  A queued task cancels this
            # loop first via acquire_for_agent().
            await asyncio.sleep(0.1)

    def start_keepalive(self, session: ChatBrowserSession, *, interval_seconds: float) -> None:
        if interval_seconds <= 0:
            return
        existing = self._keepalive_tasks.get(session.bsk_session_id)
        if existing is not None and not existing.done():
            return
        task = asyncio.create_task(
            self._keepalive_loop(session, interval_seconds),
            name=f"browser-skill-keepalive-{session.bsk_session_id}",
        )
        self._keepalive_tasks[session.bsk_session_id] = task

        def discard(done: asyncio.Task[None]) -> None:
            if self._keepalive_tasks.get(session.bsk_session_id) is done:
                self._keepalive_tasks.pop(session.bsk_session_id, None)

        task.add_done_callback(discard)

    async def stop_keepalive(self, session: ChatBrowserSession) -> None:
        task = self._keepalive_tasks.pop(session.bsk_session_id, None)
        if task is None or task.done():
            return
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    async def _keepalive_loop(
        self,
        session: ChatBrowserSession,
        interval_seconds: float,
    ) -> None:
        while True:
            await asyncio.sleep(interval_seconds)
            try:
                # A session-bound, read-only command refreshes BrowserSkill's
                # daemon activity timestamp without touching media playback.
                await self.client.tab_list(session.bsk_session_id, scope="agent")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if self.logger is not None:
                    self.logger.warning(
                        "BrowserSkill keepalive stopped for {}: {}",
                        session.bsk_session_id,
                        type(exc).__name__,
                    )
                return

    async def close_conversation(self, conversation_id: str | None) -> bool:
        key = str(conversation_id or "").strip()
        if not key:
            return False
        session = self._sessions.get(key)
        if session is None:
            return False
        await self.close_session(session)
        return True

    async def close_all(self) -> None:
        for session in list(self._sessions.values()):
            await self.close_session(session)
