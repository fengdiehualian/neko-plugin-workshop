# -*- coding: utf-8 -*-
# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

"""Text-to-speech over N.E.K.O's own TTS channel.

Why not just call the host's HTTP API? Because there isn't one that takes
arbitrary text and returns audio. ``GET /api/characters/voice_preview`` takes
only a ``voice_id`` and always speaks a fixed preview sentence
(``voice_preview.py`` -> ``_loc(VOICE_PREVIEW_TEXTS, ...)``), and the
``/speak`` routes play through the host's audio device without handing back the
bytes. A plugin that needs *arbitrary text rendered to a file* has to speak the
provider protocol itself, which is what this module does.

The protocol is the Step-shaped streaming one used by the free/StepFun TTS
path, mirrored from ``main_routers/characters_router/voice_preview.py::
_synthesize_step_voice_preview``:

    connect -> tts.connection.done {session_id}
            -> tts.create  {session_id, voice_id, response_format, sample_rate}
            -> tts.text.delta {session_id, text}
            -> tts.text.done  {session_id}
            -> tts.response.audio.delta {audio: base64(wav)}   (0..n)
            -> tts.response.done | tts.response.audio.done

Each ``tts.response.audio.delta`` carries a *complete little WAV*, not raw PCM,
which is why every delta goes through the WAV decoder rather than being
concatenated as bytes.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Sequence

from .audiowav import AudioError, decode_wav_bytes, to_pcm16_bytes
from .audiowav import wrap_pcm16_wav as _wrap_pcm16

logger = logging.getLogger("neko_stellaris.tts")

DEFAULT_TTS_URL = "wss://www.lanlan.tech/tts"
DEFAULT_TTS_URL_INTL = "wss://www.lanlan.app/tts"
DEFAULT_SAMPLE_RATE = 24000

# The free channel is a shared service and enforces its concurrency hard: four
# simultaneous sockets earn a `1008 (policy violation) Too many concurrent
# requests` close, which is measured behaviour, not caution. Two in flight keeps
# a 250-clip pack to roughly ten minutes without provoking the limiter.
DEFAULT_CONCURRENCY = 1
_MAX_ATTEMPTS = 6
_BACKOFF_BASE_S = 1.5
# Rate-limit rejections are not transient in the same way a dropped socket is:
# retrying at the normal cadence just re-trips the same guard, so those wait
# noticeably longer.
_RATE_LIMIT_BACKOFF_BASE_S = 5.0
_RATE_LIMIT_MARKERS = ("too many concurrent", "policy violation", "rate limit",
                       "429", "503")


class TtsError(RuntimeError):
    """Raised when a line could not be synthesized after every attempt."""


@dataclass(frozen=True)
class TtsSettings:
    """Everything needed to reach the TTS channel."""

    url: str = DEFAULT_TTS_URL
    api_key: str = ""
    voice_id: str = ""
    sample_rate: int = DEFAULT_SAMPLE_RATE
    language_code: str = ""
    # Per-attempt budget for the whole connect+synth exchange. A short advisor
    # line comes back in ~2s; 30s only ever fires when the service is unwell.
    timeout_s: float = 30.0
    concurrency: int = DEFAULT_CONCURRENCY

    def fingerprint(self) -> str:
        """Stable identity of the *voice*, used as the cache key prefix."""
        raw = f"{self.url}|{self.voice_id}|{self.sample_rate}|{self.language_code}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


@dataclass
class TtsStats:
    done: int = 0
    cached: int = 0
    failed: int = 0
    bytes_written: int = 0
    errors: list[str] = field(default_factory=list)
    # Adaptive-throttle telemetry: how often the service pushed back and what
    # spacing that settled on. Surfaced so a slow batch is explainable.
    rate_limit_rejections: int = 0
    final_gap_s: float = 0.0


def _merge_delta(accumulated: bytes, incoming: bytes) -> bytes:
    """Fold one audio delta into the accumulated PCM stream.

    The service normally sends disjoint segments that simply concatenate. Two
    other shapes have been observed to be *possible* by inspection of the
    protocol (the server decides), and both would corrupt the clip if treated as
    a plain append:

    * a cumulative delta (``incoming`` is ``accumulated`` plus more) -> replace
    * a repeated delta (``incoming`` is a prefix already present) -> ignore

    Cheap byte-prefix tests settle all three cases without decoding twice.
    """
    if not accumulated:
        return incoming
    if not incoming:
        return accumulated
    if incoming.startswith(accumulated):
        return incoming          # cumulative
    if accumulated.startswith(incoming):
        return accumulated       # exact repeat / stale retry
    return accumulated + incoming


class TtsSession:
    """One websocket plus one provider session, reused for many lines.

    The free endpoint's limiter counts *connections*, not requests. Measured
    against it: sixteen lines back to back on one session produced no rejections
    and cost 0.54s each, while opening a connection per line -- even only two at a
    time -- earns ``1008 (policy violation) Too many concurrent requests`` on
    roughly half of them, and each rejection costs a multi-second backoff. The
    batch then spends most of its wall clock waiting.

    Same measurements, same endpoint: connect + handshake ~0.20s, first line
    ~1.1s, every line after that ~0.55s. So a full 248-clip pack goes from
    ~8-9 minutes to ~2-3.

    A session is deliberately not shared between concurrent workers: one connection
    at a time is what the limiter tolerates.
    """

    def __init__(self, settings: TtsSettings) -> None:
        self._settings = settings
        self._ws: Any = None
        self._session_id = ""
        self._created = False
        self.lines = 0

    @property
    def connected(self) -> bool:
        return self._ws is not None and self._created

    async def _connect(self) -> None:
        try:
            import websockets
        except ImportError as exc:  # pragma: no cover - dependency is in the venv
            raise TtsError("websockets 不可用，无法合成语音") from exc

        settings = self._settings
        headers = {"Authorization": f"Bearer {settings.api_key or ''}"}
        ws = await websockets.connect(settings.url, additional_headers=headers,
                                      open_timeout=15)
        self._ws = ws
        while True:
            raw = await asyncio.wait_for(ws.recv(), timeout=10.0)
            if isinstance(raw, bytes):
                continue
            event = json.loads(raw)
            kind = event.get("type")
            if kind == "tts.connection.done":
                self._session_id = (event.get("data") or {}).get("session_id") or ""
                break
            if kind == "tts.response.error":
                raise TtsError(f"握手失败: {event.get('data')}")
        if not self._session_id:
            raise TtsError("TTS 未返回 session_id")

        create: dict[str, Any] = {
            "session_id": self._session_id,
            "voice_id": settings.voice_id,
            "response_format": "wav",
            "sample_rate": settings.sample_rate,
        }
        if settings.language_code:
            create["language_code"] = settings.language_code
        await ws.send(json.dumps({"type": "tts.create", "data": create}))
        self._created = True

    async def close(self) -> None:
        ws, self._ws = self._ws, None
        self._created = False
        self._session_id = ""
        if ws is not None:
            try:
                await ws.close()
            except Exception:  # noqa: BLE001 - closing must never mask the real error
                pass

    async def speak(self, text: str) -> tuple[bytes, int]:
        """Synthesize one line, returning (pcm16_bytes, sample_rate).

        Any failure closes the session: the turn is then desynchronised, and a
        retry that reuses it would read the abandoned response as its own.
        """
        if not text.strip():
            raise TtsError("空文本")
        if not self._settings.voice_id.strip():
            raise TtsError("缺少 voice_id")
        if not self.connected:
            await self.close()
            await self._connect()
        ws = self._ws
        if ws is None:  # pragma: no cover - _connect sets it or raises
            raise TtsError("TTS 会话未建立")

        pcm = b""
        rate = self._settings.sample_rate
        try:
            await ws.send(json.dumps({
                "type": "tts.text.delta",
                "data": {"session_id": self._session_id, "text": text},
            }))
            await ws.send(json.dumps({
                "type": "tts.text.done",
                "data": {"session_id": self._session_id},
            }))

            while True:
                raw = await asyncio.wait_for(ws.recv(), timeout=self._settings.timeout_s)
                if isinstance(raw, bytes):
                    continue
                event = json.loads(raw)
                kind = event.get("type")
                if kind == "tts.response.error":
                    raise TtsError(f"合成失败: {event.get('data')}")
                if kind == "tts.response.audio.delta":
                    blob = (event.get("data") or {}).get("audio", "")
                    if blob:
                        try:
                            chunk = decode_wav_bytes(base64.b64decode(blob))
                        except (AudioError, ValueError) as exc:
                            raise TtsError(f"音频块无法解析: {exc}") from exc
                        rate = chunk.rate
                        pcm = _merge_delta(pcm, to_pcm16_bytes(chunk.samples))
                elif kind == "tts.response.done":
                    # The end of the *turn*, not of the audio: ``audio.done`` comes
                    # first and this closes the response. Waiting for this one is
                    # what keeps the next line in sync -- breaking at ``audio.done``
                    # leaves this message queued, and the next call reads it as its
                    # own completion and returns no audio at all.
                    break
        except asyncio.TimeoutError as exc:
            await self.close()
            raise TtsError("TTS 超时") from exc
        except Exception:
            await self.close()
            raise

        if not pcm:
            await self.close()
            raise TtsError("TTS 未返回音频")
        self.lines += 1
        return pcm, rate


async def synthesize_raw(text: str, settings: TtsSettings) -> tuple[bytes, int]:
    """Synthesize one line and return (pcm16_bytes, sample_rate).

    ``pcm16_bytes`` is raw little-endian 16-bit PCM at the provider's rate, with
    any multi-delta segments already merged. Nothing is resampled here; the
    caller decides the final rate.

    One line per call, so this opens and closes a session; batch callers should
    keep a :class:`TtsSession` instead and pay the handshake once.
    """
    session = TtsSession(settings)
    try:
        return await session.speak(text)
    finally:
        await session.close()


def _is_rate_limited(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(marker in text for marker in _RATE_LIMIT_MARKERS)


class Pacer:
    """Adaptive spacing gate shared by every worker in one batch.

    Measured behaviour of the free endpoint: even two in-flight sockets earn
    ``1008 (policy violation) Too many concurrent requests`` on roughly half the
    requests, and sustained pressure escalates to ``Rate limit exceeded``. Retrying
    recovers, but without pacing the batch spends most of its wall clock in
    backoff.

    So the gate widens itself every time a rejection is seen and never narrows
    again within the batch. That is deliberately one-way: the failure is not
    transient noise, and a controller that relaxes after a lucky streak just
    re-trips the same guard. A full pack converges to whatever spacing the
    service actually tolerates.
    """

    def __init__(self, initial_gap: float = 0.0, *, max_gap: float = 4.0,
                 step: float = 0.35) -> None:
        self._gap = max(0.0, initial_gap)
        self._max_gap = max_gap
        self._step = step
        self._next_at = 0.0
        self._lock = asyncio.Lock()
        self.rejections = 0

    @property
    def gap(self) -> float:
        return round(self._gap, 2)

    def penalise(self) -> None:
        self.rejections += 1
        self._gap = min(self._max_gap, self._gap + self._step)

    async def wait(self) -> None:
        """Block until this worker's slot opens, reserving the next one."""
        async with self._lock:
            now = time.monotonic()
            delay = max(0.0, self._next_at - now)
            self._next_at = max(now, self._next_at) + self._gap
        if delay > 0:
            await asyncio.sleep(delay)


async def synthesize_with_retry(text: str, settings: TtsSettings,
                                *, label: str = "",
                                pacer: Pacer | None = None,
                                session: "TtsSession | None" = None) -> tuple[bytes, int]:
    """``synthesize_raw`` with bounded exponential backoff.

    Rate-limit rejections back off on a longer ladder than ordinary failures,
    because retrying a concurrency rejection at the normal cadence simply trips
    the same guard again -- and they also widen ``pacer`` for the whole batch.

    Pass ``session`` to reuse an open connection. A failed turn closes it, so the
    retry below reconnects on its own; that is deliberate, since a session whose
    turn was abandoned cannot be trusted to line its next response up.
    """
    last: Exception | None = None
    for attempt in range(_MAX_ATTEMPTS):
        if pacer is not None:
            await pacer.wait()
        try:
            if session is not None:
                return await session.speak(text)
            return await synthesize_raw(text, settings)
        except Exception as exc:  # noqa: BLE001 - provider errors are open-ended
            last = exc
            limited = _is_rate_limited(exc)
            if limited and pacer is not None:
                pacer.penalise()
            if attempt == _MAX_ATTEMPTS - 1:
                break
            base = _RATE_LIMIT_BACKOFF_BASE_S if limited else _BACKOFF_BASE_S
            delay = base * (2 ** attempt)
            logger.warning("TTS 第 %d/%d 次失败（%s）: %s，%.1fs 后重试",
                           attempt + 1, _MAX_ATTEMPTS, label or text[:12], exc, delay)
            await asyncio.sleep(delay)
    raise TtsError(f"{label or text[:12]}: {last}")


ProgressFn = Callable[[int, int, str], None]


def cache_key(settings: TtsSettings, text: str) -> str:
    """Content address of one clip: exactly what changes what it sounds like.

    Voice, endpoint, sample rate and language are part of the fingerprint, so
    switching the character's voice invalidates every clip while editing a single
    line invalidates exactly one. Knowing that difference is what lets the startup
    check say "248 clips to redo" versus "3 clips to redo" before committing to the
    work.

    The slot name is deliberately *not* part of it. It used to be, and the cost was
    invisible until the take naming had to change: renaming a slot re-synthesized
    audio that was already on disk, byte-identical, against a rate-limited
    endpoint. A rename is not supposed to cost money. :func:`legacy_cache_key`
    exists only to find files written under the old scheme.
    """
    digest = hashlib.sha256(
        f"{settings.fingerprint()}|{text}".encode("utf-8")).hexdigest()
    return digest[:24]


def legacy_cache_key(settings: TtsSettings, slot: str, text: str) -> str:
    """The key shape used while the slot name was still part of it."""
    digest = hashlib.sha256(
        f"{settings.fingerprint()}|{slot}|{text}".encode("utf-8")).hexdigest()
    return digest[:24]


def cache_path(settings: TtsSettings, slot: str, text: str, cache_dir: str | Path) -> Path:
    base = Path(cache_dir)
    path = base / f"{cache_key(settings, text)}.wav"
    if path.is_file():
        return path
    # Adopt a clip recorded under the old key rather than paying for it again.
    # Renamed in place, so this shim runs once per file and then finds nothing.
    stale = base / f"{legacy_cache_key(settings, slot, text)}.wav"
    if stale.is_file():
        try:
            stale.rename(path)
            return path
        except OSError:
            return stale
    return path


def count_cache_misses(
    items: Sequence[tuple[str, str]],
    settings: TtsSettings,
    cache_dir: str | Path,
) -> int:
    """How many of ``items`` would actually hit the network.

    Counts a cached file as a hit only if it decodes, matching what
    :func:`synthesize_many` does -- a truncated file left by a killed process
    must not be reported as free work.
    """
    misses = 0
    for slot, text in items:
        path = cache_path(settings, slot, text, cache_dir)
        if not path.is_file() or path.stat().st_size <= 44:
            misses += 1
            continue
        try:
            decode_wav_bytes(path.read_bytes())
        except Exception:  # noqa: BLE001
            misses += 1
    return misses


async def synthesize_many(
    items: Sequence[tuple[str, str]],
    settings: TtsSettings,
    *,
    cache_dir: Path | None = None,
    on_progress: ProgressFn | None = None,
    stats: TtsStats | None = None,
) -> dict[str, tuple[bytes, int]]:
    """Synthesize ``items`` = [(cache_key, text), ...] with bounded concurrency.

    ``cache_dir`` makes the run resumable: a clip whose text, voice and URL are
    unchanged is read from disk instead of being paid for again. Synthesizing a
    full voice pack is a multi-minute batch and *will* be interrupted at least
    once, so resumability is a correctness property here, not a nicety.
    """
    stats = stats if stats is not None else TtsStats()
    results: dict[str, tuple[bytes, int]] = {}
    total = len(items)
    if not total:
        return results

    cache_dir = Path(cache_dir) if cache_dir else None
    if cache_dir:
        cache_dir.mkdir(parents=True, exist_ok=True)

    lock = asyncio.Lock()
    semaphore = asyncio.Semaphore(max(1, settings.concurrency))
    # No opening delay any more: it existed to soften the burst of *connections*
    # a batch used to open, and a batch now opens one. It still widens itself on a
    # rejection, so a limiter that turns out to be rate-based rather than
    # connection-based is discovered by the first rejection instead of assumed.
    pacer = Pacer(initial_gap=0.0)
    completed = 0

    def _cache_path(key: str, text: str) -> Path | None:
        if cache_dir is None:
            return None
        return cache_path(settings, key, text, cache_dir)

    async def _one(key: str, text: str, session: "TtsSession") -> None:
        nonlocal completed
        path = _cache_path(key, text)
        if path is not None and path.exists() and path.stat().st_size > 44:
            try:
                payload = path.read_bytes()
                # Decode here rather than trusting the extension: a truncated
                # file left by a killed process must not be served as a cache hit.
                chunk = decode_wav_bytes(payload)
                async with lock:
                    results[key] = (to_pcm16_bytes(chunk.samples), chunk.rate)
                    stats.cached += 1
                    completed += 1
                    if on_progress:
                        on_progress(completed, total, key)
                return
            except Exception:
                logger.warning("缓存文件损坏，重新合成: %s", path.name)
                path.unlink(missing_ok=True)

        async with semaphore:
            try:
                pcm, rate = await synthesize_with_retry(text, settings, label=key,
                                                        pacer=pacer, session=session)
            except Exception as exc:  # noqa: BLE001
                async with lock:
                    stats.failed += 1
                    completed += 1
                    stats.errors.append(f"{key}: {exc}")
                    if on_progress:
                        on_progress(completed, total, key)
                logger.error("合成失败 %s: %s", key, exc)
                return
        if path is not None:
            try:
                # The provider already handed us a valid WAV at its native rate,
                # so persist it verbatim. Resampling to 44100 happens at
                # pack-build time, which means changing the target rate never
                # invalidates an existing cache.
                tmp = path.with_suffix(".part")
                tmp.write_bytes(_wrap_pcm16(pcm, rate))
                tmp.replace(path)
            except Exception as exc:  # noqa: BLE001
                logger.warning("缓存写入失败 %s: %s", path.name, exc)
        async with lock:
            results[key] = (pcm, rate)
            stats.done += 1
            stats.bytes_written += len(pcm)
            completed += 1
            if on_progress:
                on_progress(completed, total, key)

    # One long-lived session per worker. The limiter counts connections, so the
    # number of workers is the number of connections the endpoint sees at once --
    # which is why the default is 1 and why a worker keeps its session instead of
    # opening one per line.
    workers = max(1, settings.concurrency)
    queue: asyncio.Queue[tuple[str, str]] = asyncio.Queue()
    for item in items:
        queue.put_nowait(item)

    async def _worker() -> None:
        session = TtsSession(settings)
        try:
            while True:
                try:
                    key, text = queue.get_nowait()
                except asyncio.QueueEmpty:
                    return
                await _one(key, text, session)
        finally:
            await session.close()

    await asyncio.gather(*(_worker() for _ in range(workers)))
    if pacer.rejections:
        logger.info("本批次遇到 %d 次限流，最终请求间隔收敛到 %.2fs",
                    pacer.rejections, pacer.gap)
    stats.rate_limit_rejections = pacer.rejections
    stats.final_gap_s = pacer.gap
    return results


async def probe(settings: TtsSettings, text: str = "你好呀，我是兰兰。") -> str:
    """One-line connectivity check used by the plugin's status entry."""
    started = time.monotonic()
    pcm, rate = await synthesize_raw(text, settings)
    elapsed = time.monotonic() - started
    seconds = len(pcm) / float(2 * rate) if rate else 0.0
    return f"OK {rate}Hz {seconds:.2f}s in {elapsed:.2f}s"
