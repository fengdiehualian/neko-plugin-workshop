"""One bounded, silent preparation queue for the current character."""
from __future__ import annotations

import asyncio
import threading
import uuid
from collections import deque
from functools import wraps
from pathlib import Path


def locked(method):
    @wraps(method)
    def call(self, *args, **kwargs):
        with self.lock:
            return method(self, *args, **kwargs)
    return call


class VoiceBank:
    def __init__(self, lines, synthesize, identity, *, max_bytes=16_000_000, cache_dir=None):
        self.lines, self.synthesize, self.identity = lines, synthesize, identity
        self.max_bytes = max_bytes
        self.character = None
        self.fingerprint = None
        self.token = str(uuid.uuid4())
        self.clips, self.failures = {}, {}
        self.task = None
        self.enabled, self.suspended, self.busy = True, False, False
        # Installed hosts can dispatch entries on different event loops. Never
        # hold an asyncio lock across identity I/O or share its waiters across loops.
        self.lock = threading.RLock()
        self.refresh_revision = 0
        self.work_generation = self.pause_revision = 0
        self.history = deque(maxlen=48)
        self.cache_dir = Path(cache_dir).resolve() if cache_dir else None
        self.resources = {}

    def clean_files(self):
        if not self.cache_dir or not self.cache_dir.exists():
            return
        for file in self.cache_dir.iterdir():
            token, _, event = file.stem.partition('_')
            try:
                uuid.UUID(token)
            except ValueError:
                continue
            if event in self.lines and file.suffix in ('.wav','.ogg') and file.resolve().parent == self.cache_dir:
                file.unlink(missing_ok=True)

    def publish(self, token, event, clip, valid):
        self.cache_dir.mkdir(parents=True,exist_ok=True)
        filename = token + '_' + event + ('.ogg' if clip[1] == 'audio/ogg' else '.wav')
        file = self.cache_dir / filename
        file.write_bytes(clip[0])
        if not valid():
            file.unlink(missing_ok=True)
            return None
        return filename

    @locked
    def record(self, event, outcome):
        self.history.append({'event': event, 'outcome': outcome})

    @locked
    def _cancel(self):
        self.work_generation += 1
        if self.task and not self.task.done():
            loop = self.task.get_loop()
            if not loop.is_closed():
                try:
                    current = asyncio.get_running_loop()
                except RuntimeError:
                    current = None
                if current is loop:
                    self.task.cancel()
                else:
                    loop.call_soon_threadsafe(self.task.cancel)
        self.task = None

    async def refresh(self, character):
        with self.lock:
            revision = self.pause_revision
            self.refresh_revision += 1
            request = self.refresh_revision
        try:
            fingerprint = await self.identity(character)
        except Exception:
            fingerprint = None
        with self.lock:
            if request != self.refresh_revision:
                return self.token
            if character != self.character or fingerprint != self.fingerprint:
                changed_character = self.character is not None and character != self.character
                self._cancel()
                self.character, self.fingerprint = character, fingerprint
                self.token = str(uuid.uuid4())
                self.clips.clear()
                self.failures.clear()
                self.resources.clear()
                self.clean_files()
                if changed_character and revision == self.pause_revision:
                    self.suspended = False
            return self.token

    @locked
    def pause(self):
        self.pause_revision += 1
        self.suspended = True
        self._cancel()

    @locked
    def set_busy(self, busy):
        self.busy = bool(busy)
        if self.busy:
            self._cancel()

    @locked
    def enable(self, enabled):
        self.enabled = enabled
        if enabled:
            self.retry()
        else:
            self.pause()

    @locked
    def retry(self):
        self.suspended = False
        self.failures.clear()

    @locked
    def ensure(self):
        if not self.enabled or self.suspended or self.busy or not self.fingerprint or self.task:
            return
        if any(event not in self.clips and event not in self.failures for event in self.lines):
            self.task = asyncio.create_task(self._prepare(self.token, self.work_generation))

    async def _prepare(self, token, generation):
        task = asyncio.current_task()
        def valid():
            with self.lock:
                return token == self.token and generation == self.work_generation and self.enabled and not self.suspended and not self.busy
        try:
            for event, (text, _) in self.lines.items():
                with self.lock:
                    if not valid():
                        return
                    if event in self.clips or event in self.failures:
                        continue
                    character = self.character
                self.record(event, 'preparing')
                try:
                    clip = await self.synthesize(character, text, valid, emit=None)
                    with self.lock:
                        if not valid():
                            return
                        if not clip[0]:
                            raise ValueError('empty_audio')
                        if sum(len(item[0]) for item in self.clips.values()) + len(clip[0]) > self.max_bytes:
                            raise ValueError('audio_limit')
                    filename = await asyncio.to_thread(self.publish,token,event,clip,valid) if self.cache_dir and valid() else None
                    with self.lock:
                        if not valid():
                            if filename:
                                (self.cache_dir / filename).unlink(missing_ok=True)
                            return
                        self.clips[event] = clip
                        if filename:
                            self.resources[event] = filename
                        self.record(event, 'ready')
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    with self.lock:
                        if valid():
                            # No provider messages, credentials or configuration enter diagnostics.
                            code = getattr(exc, 'code', None)
                            self.failures[event] = code if code in {'timeout','provider_unavailable','provider_error','audio_limit','empty_audio'} else type(exc).__name__
                            self.record(event, 'failed')
        except asyncio.CancelledError:
            self.record('', 'cancelled')
        finally:
            with self.lock:
                if self.task is task:
                    self.task = None

    @locked
    def clip(self, event):
        return self.clips.get(event) if self.enabled and self.fingerprint else None

    @locked
    def resource(self, event):
        return self.resources.get(event) if self.enabled and self.fingerprint else None

    @locked
    def snapshot(self):
        state = 'disabled' if not self.enabled else 'waiting' if not self.character else 'paused' if self.suspended or self.busy else 'unavailable' if not self.fingerprint else 'preparing' if self.task else 'ready' if len(self.clips) == len(self.lines) else 'partial' if self.failures else 'preparing'
        return {'state': state, 'character': self.character, 'token': self.token,
                'ready': len(self.clips), 'total': len(self.lines), 'failed': list(self.failures), 'errors':dict(self.failures),
                'clips': {event:'/ui/voice-cache/' + filename for event,filename in self.resources.items()} if self.enabled and self.fingerprint else {},
                'events': {event: 'ready' if event in self.clips else 'failed' if event in self.failures else 'pending' for event in self.lines},
                'recent': list(self.history)}
