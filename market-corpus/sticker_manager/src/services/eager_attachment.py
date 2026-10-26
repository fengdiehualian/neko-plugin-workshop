"""Eager-only, reply-complete image selection with no conversational continuation."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import threading
import time
from collections import OrderedDict
from contextlib import suppress
from typing import Any

from ..core.attachment_selection import (
    MODEL_USAGE,
    SELECTION_PROMPT,
    ConversationTurn,
    conversation_turn,
    parse_selection,
    selection_candidates,
)
from .lanlan import LanlanResolver, records_of
from .local_http import read_local_json
from .turns import user_turn_of

MODEL_TIMEOUT_SEC = 12.0
MAX_REPLY_AGE_SEC = 20.0
CHECK_INTERVAL_SEC = 0.5


class EagerAttachment:
    def __init__(self, plugin: Any):
        self._plugin = plugin
        self.control_lock = threading.RLock()
        self._poll_lock = threading.Lock()
        self._epoch = 0
        self._closed = False
        self._bootstrapped = False
        self._session_ticket = None
        self._seen: OrderedDict[tuple[str, str, str, float], None] = OrderedDict()
        self._gaps: dict[str, int] = {}
        self._delivered: dict[str, float] = {}
        self._selection = None
        self._pending_turn: ConversationTurn | None = None
        self._retry_at = 0.0
        self._log_key = None
        self._log_at = 0.0
        self._status = "idle"
        self._attempts = 0
        self._sent = 0

    def invalidate(self, *, closing: bool = False) -> None:
        with self.control_lock:
            self._epoch += 1
            self._closed = self._closed or closing
            self._bootstrapped = False
            self._seen.clear()
            self._gaps.clear()
            self._delivered.clear()
            self._session_ticket = None
            self._pending_turn = None
            self._retry_at = 0.0
            self._status = "stopped" if self._closed else "idle"
            if self._selection is not None:
                loop, task = self._selection
                if not loop.is_closed():
                    loop.call_soon_threadsafe(task.cancel)

    def snapshot(self) -> dict[str, Any]:
        with self.control_lock:
            return {
                "status": self._status, "attempts": self._attempts, "sent": self._sent,
                "reply_gap": max(self._gaps.values(), default=0),
            }

    def _active(self, epoch: int) -> bool:
        settings = self._plugin._settings
        return (
            not self._closed and self._epoch == epoch
            and settings.enabled and settings.send.eagerness == "eager"
        )

    def _note(self, status: str, turn: ConversationTurn | None = None) -> dict[str, str]:
        with self.control_lock:
            self._status = status
            key = status, turn.key if turn else None
            if key != self._log_key or time.monotonic() - self._log_at >= 60:
                self._log_key, self._log_at = key, time.monotonic()
                identity = hashlib.sha256(turn.identity.encode()).hexdigest()[:16] if turn else "-"
                self._plugin.logger.info(
                    f"eager attachment: status={status} reply_hash={identity}"
                    f" gap={self._gaps.get(turn.role, 0) if turn else 0}"
                    f" attempts={self._attempts} sent={self._sent}"
                )
        return {"status": status}

    async def _read(self) -> tuple[list[ConversationTurn], list[Any]]:
        bus = self._plugin.bus
        conversations = bus.conversations.get
        memory = bus.memory.get
        raw = await asyncio.to_thread(conversations, max_count=200, timeout=0.8)
        if inspect.isawaitable(raw):
            raw = await raw
        users = await asyncio.to_thread(memory, bucket_id="default", limit=200, timeout=0.8)
        if inspect.isawaitable(users):
            users = await users
        if getattr(raw, "error", None) is not None or getattr(users, "error", None) is not None:
            raise RuntimeError("conversation_bus_unavailable")
        turns = [turn for row in records_of(raw) if (turn := conversation_turn(dict(row))) is not None]
        # Memory SDK wrappers are normalized by user_turn_of, not conversation adapters.
        return sorted(turns, key=lambda turn: turn.observed_at), list(users)

    async def _ready(self, role: str) -> bool:
        base = LanlanResolver(self._plugin)._api_base()
        current = await asyncio.to_thread(read_local_json, base + "/api/characters/current_catgirl", timeout=0.5)
        health = await asyncio.to_thread(read_local_json, base + "/api/debug/health", timeout=0.5)
        return (
            isinstance(current, dict) and current.get("current_catgirl") == role
            and isinstance(health, dict)
            and health.get("current", {}).get("is_responding", {}).get(role) is False
        )

    def _remember(self, turn: ConversationTurn) -> None:
        self._seen[turn.key] = None
        while len(self._seen) > 512:
            self._seen.popitem(last=False)

    @staticmethod
    def _user_before(turn: ConversationTurn, turns: list[ConversationTurn]) -> ConversationTurn | None:
        preceding = [
            item for item in turns if item.role == turn.role and item.kind == "user_message"
            and item.started_at <= turn.started_at
        ]
        return max(preceding, key=lambda item: item.started_at, default=None)

    def _log_guard(self, ticket: Any, epoch: int) -> bool:
        if ticket is None or not self._active(epoch):
            return False
        self._plugin._sender._turn_end.poll(ticket)
        return not (ticket.invalid or ticket.superseded or ticket.session_changed) and ticket.caught_up

    async def _current(
        self, turn: ConversationTurn, user: ConversationTurn, ticket: Any, epoch: int, version: int,
    ) -> bool:
        if not self._log_guard(ticket, epoch) or time.time() - turn.finished_at > MAX_REPLY_AGE_SEC:
            return False
        activity, busy, sent_at = self._plugin._sender.activity(turn.role)
        if busy or activity != version or sent_at >= user.started_at:
            return False
        turns, records = await self._read()
        if any(
            item.role == turn.role and item.started_at > turn.started_at and item.key != turn.key
            for item in turns
        ):
            return False
        if any(
            (item := user_turn_of(raw)) is not None
            and item.lanlan == turn.role and item.ts > turn.started_at
            for raw in records
        ):
            return False
        return self._log_guard(ticket, epoch) and await self._ready(turn.role)

    async def poll(self) -> dict[str, str]:
        if not self._poll_lock.acquire(blocking=False):
            return {"status": "busy"}
        try:
            epoch = self._epoch
            if not self._active(epoch):
                return {"status": "inactive"}
            adapter = self._plugin._sender._turn_end
            if self._session_ticket is not None:
                adapter.poll(self._session_ticket)
                if self._session_ticket.invalid or self._session_ticket.session_changed:
                    self.invalidate()
                    epoch = self._epoch
            if self._session_ticket is None:
                self._session_ticket = adapter.arm("_eager_session_monitor_")
            if self._session_ticket is None:
                return self._note("guard_unavailable")
            turns, _ = await self._read()
            with self.control_lock:
                if not self._active(epoch):
                    return {"status": "cancelled"}
                fresh = [turn for turn in turns if turn.key not in self._seen]
                for turn in fresh:
                    self._remember(turn)
                if not self._bootstrapped:
                    self._bootstrapped = True
                    return self._note("ready")
                threshold = max(1, self._plugin._settings.send.eager_attachment_turns)
                for role in self._gaps:
                    _, _, sent_at = self._plugin._sender.activity(role)
                    if sent_at > self._delivered.get(role, 0):
                        self._delivered[role] = sent_at
                        self._gaps[role] = 0
                replies = [turn for turn in fresh if turn.kind == "assistant_message"]
                for turn in replies:
                    if time.time() - turn.finished_at > MAX_REPLY_AGE_SEC:
                        continue
                    user = self._user_before(turn, turns)
                    if user is None:
                        continue
                    _, _, sent_at = self._plugin._sender.activity(turn.role)
                    self._delivered[turn.role] = max(self._delivered.get(turn.role, 0), sent_at)
                    if sent_at >= user.started_at:
                        self._gaps[turn.role] = 0
                    else:
                        self._gaps[turn.role] = min(threshold, self._gaps.get(turn.role, 0) + 1)
                if replies:
                    self._pending_turn = max(replies, key=lambda item: item.finished_at)
                turn = self._pending_turn
            if turn is None:
                return {"status": self._status}
            if time.time() - turn.finished_at > MAX_REPLY_AGE_SEC:
                self._pending_turn = None
                return self._note("expired", turn)
            user = self._user_before(turn, turns)
            if user is None or self._gaps.get(turn.role, 0) < threshold:
                self._pending_turn = None
                return self._note("waiting", turn)
            if time.monotonic() < self._retry_at:
                return {"status": self._status}
            ticket = adapter.arm(turn.role)
            version, _, _ = self._plugin._sender.activity(turn.role)
            if not await self._current(turn, user, ticket, epoch, version):
                return self._note("superseded", turn)
            # A brief host-busy state can retry observation, never model work.
            # Claim this reply before entering any asynchronous selection.
            self._pending_turn = None
            return await self._choose(turn, user, turns, ticket, epoch, version)
        except asyncio.CancelledError:
            if not self._active(epoch):
                return {"status": "cancelled"}
            raise
        except Exception:
            # Never log exception messages: gateway failures may contain private data.
            return self._note("observer_unavailable")
        finally:
            self._poll_lock.release()

    async def _choose(self, turn, user, turns, ticket, epoch, version):
        library, sender = self._plugin._library, self._plugin._sender
        if not library.load().ok:
            return self._note("library_unavailable", turn)
        zone = library.active_zone()
        rows = selection_candidates(
            library.active_pool(), turn.text, sender.recent_sent_ids(turn.role, self._plugin._settings),
            library.group_descs(),
        )
        if not rows:
            return self._note("empty_candidates", turn)
        context = sorted(
            [item for item in turns if item.role == turn.role and item.started_at <= turn.started_at],
            key=lambda item: item.started_at,
        )[-12:]
        # Do not cut a reply whose omitted ending could contain an output restriction.
        if any(len(item.text) > 2000 for item in context) or sum(len(item.text) for item in context) > 10000:
            return self._note("context_too_large", turn)
        payload = {
            "context": [{"role": item.kind, "text": item.text} for item in context],
            "reply": turn.text, "candidates": rows,
        }
        self._attempts += 1
        self._note("selecting", turn)
        task = asyncio.create_task(self._model_select(payload))
        with self.control_lock:
            self._selection = (asyncio.get_running_loop(), task)
            if not self._active(epoch):
                task.cancel()
        try:
            async with asyncio.timeout(MODEL_TIMEOUT_SEC):
                while True:
                    done, _ = await asyncio.wait({task}, timeout=CHECK_INTERVAL_SEC)
                    if not await self._current(turn, user, ticket, epoch, version):
                        return self._note("superseded", turn)
                    if done:
                        response = task.result()
                        break
        except TimeoutError:
            return self._note("model_timeout", turn)
        except Exception as exc:
            body = getattr(exc, "body", None)
            error = body.get("error", body) if isinstance(body, dict) else {}
            code = error.get("code") if isinstance(error, dict) else ""
            status = "model_unbound" if code == "model_usage_not_bound" else "model_unavailable"
            self._retry_at = time.monotonic() + 60
            return self._note(status, turn)
        finally:
            if not task.done():
                task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await task
            with self.control_lock:
                self._selection = None
        choices = getattr(response, "choices", None)
        if not choices or choices[0].finish_reason != "stop":
            return self._note("invalid_result", turn)
        sid, reason = parse_selection(choices[0].message.content, rows)
        if not sid:
            if reason in {"text_only", "serious_task"}:
                self._gaps[turn.role] = 0
            return self._note(reason, turn)
        if not await self._current(turn, user, ticket, epoch, version) or library.active_zone() != zone:
            return self._note("superseded", turn)
        sticker = library.get(sid)
        if sticker is None:
            return self._note("candidate_unavailable", turn)
        selected = next(row for row in rows if row["id"] == sid)

        def valid_submission():
            current = library.get(sid)
            return (
                self._log_guard(ticket, epoch) and library.active_zone() == zone
                and time.time() - turn.finished_at <= MAX_REPLY_AGE_SEC
                and current is not None
                and current.caption == selected["caption"] and current.desc == selected["description"]
                and current.visible_text == selected["visible_text"] and current.tags == selected["tags"]
                and current.group == selected["group"]
                and library.group_descs().get(current.group, "") == selected["group_description"]
            )

        result = await sender.send(
            sticker, lanlan=turn.role, settings=self._plugin._settings, source="eager",
            expected_activity=version, submission_lock=self.control_lock,
            submission_guard=valid_submission,
        )
        if not result.ok and not result.failure_recorded:
            sender.note_attempt_failed(
                lanlan=turn.role, sticker_id=sid, code=result.code, settings=self._plugin._settings,
                now=time.time(), source="eager",
            )
        self._plugin._runstats.note_delivery(ok=result.ok, reason=result.code)
        if result.ok and not result.queued:
            self._gaps[turn.role] = 0
            self._sent += 1
            return self._note("submitted", turn)
        return self._note(result.code or "delivery_failed", turn)

    async def _model_select(self, payload):
        models = self._plugin.ctx.models
        client = await models.get_client()
        return await client.chat.completions.create(
            model=MODEL_USAGE, stream=False, max_tokens=256,
            messages=[
                {"role": "system", "content": SELECTION_PROMPT},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
        )
