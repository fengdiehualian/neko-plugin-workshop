# -*- coding: utf-8 -*-
# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

"""PCM audio helpers for the Stellaris advisor voice pack.

Everything here exists to turn the TTS service's output into files the game
will accept, and to be explicit about the one format fact that matters:

    Stellaris 4.5 vanilla advisor VO is **PCM 16-bit, mono, 44100 Hz**.
    (verified against sound/vo/notifications/*.wav -> audioFormat=1 ch=1 44100 16)

The TTS side returns PCM 16-bit mono 24000 Hz, so every clip needs a
24000 -> 44100 resample. We prefer ``scipy.signal.resample_poly`` (a proper
polyphase FIR, ratio 147/80) and fall back to pure-stdlib linear interpolation
when scipy is unavailable, so the plugin still works in a bare interpreter.
The fallback is audibly worse on sibilants; it is a safety net, not a peer.

numpy/scipy are present in the runtime venv (plugins are spawned with
``sys.executable``), but importing them at module scope would make this module
unimportable in a bare interpreter, which is exactly the case the fallback
covers. So they are imported lazily inside the functions.
"""

from __future__ import annotations

import array
import io
import struct
import wave
from dataclasses import dataclass
from pathlib import Path

# Stellaris 4.5 vanilla advisor VO format.
TARGET_RATE = 44100
TARGET_CHANNELS = 1
TARGET_SAMPLE_WIDTH = 2  # bytes -> PCM 16-bit

_WAVE_FORMAT_PCM = 1
_WAVE_FORMAT_IEEE_FLOAT = 3
_WAVE_FORMAT_EXTENSIBLE = 0xFFFE


class AudioError(ValueError):
    """Raised when the payload cannot be interpreted as PCM audio."""


@dataclass(frozen=True)
class Audio:
    """Mono float samples in [-1, 1] plus the rate they were sampled at."""

    samples: "array.array"  # typecode 'f'
    rate: int

    @property
    def duration_s(self) -> float:
        return len(self.samples) / float(self.rate or 1)


def _iter_chunks(data: bytes):
    """Yield (chunk_id, payload) for every RIFF chunk, tolerating padding."""
    if len(data) < 12 or data[0:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise AudioError("not a RIFF/WAVE payload")
    offset = 12
    while offset + 8 <= len(data):
        chunk_id = data[offset:offset + 4]
        (size,) = struct.unpack_from("<I", data, offset + 4)
        body = data[offset + 8:offset + 8 + size]
        yield chunk_id, body
        offset += 8 + size + (size & 1)  # chunks are word-aligned


def _parse_fmt(body: bytes) -> tuple[int, int, int, int]:
    """Return (format_tag, channels, rate, bits_per_sample)."""
    if len(body) < 16:
        raise AudioError("truncated fmt chunk")
    tag, channels, rate, _byte_rate, _block_align, bits = struct.unpack_from("<HHIIHH", body, 0)
    if tag == _WAVE_FORMAT_EXTENSIBLE:
        if len(body) < 40:
            raise AudioError("truncated WAVE_FORMAT_EXTENSIBLE fmt chunk")
        # The real format lives in the first two bytes of the SubFormat GUID.
        (tag,) = struct.unpack_from("<H", body, 24)
    return tag, channels, rate, bits


def _decode_pcm(body: bytes, bits: int, tag: int) -> array.array:
    """Decode one data chunk into mono floats in [-1, 1]."""
    out = array.array("f")
    if tag == _WAVE_FORMAT_IEEE_FLOAT:
        if bits == 32:
            count = len(body) // 4
            out.fromlist(list(struct.unpack_from(f"<{count}f", body, 0)))
        elif bits == 64:
            count = len(body) // 8
            out.fromlist([float(v) for v in struct.unpack_from(f"<{count}d", body, 0)])
        else:
            raise AudioError(f"unsupported float width {bits}")
    elif tag == _WAVE_FORMAT_PCM:
        if bits == 16:
            count = len(body) // 2
            raw = struct.unpack_from(f"<{count}h", body, 0)
            out.fromlist([v / 32768.0 for v in raw])
        elif bits == 8:
            # 8-bit WAV is unsigned, centred on 128.
            out.fromlist([(b - 128) / 128.0 for b in body])
        elif bits == 24:
            count = len(body) // 3
            vals = []
            for i in range(count):
                lo, mid, hi = body[3 * i], body[3 * i + 1], body[3 * i + 2]
                v = lo | (mid << 8) | (hi << 16)
                if v & 0x800000:
                    v -= 0x1000000
                vals.append(v / 8388608.0)
            out.fromlist(vals)
        elif bits == 32:
            count = len(body) // 4
            raw = struct.unpack_from(f"<{count}i", body, 0)
            out.fromlist([v / 2147483648.0 for v in raw])
        else:
            raise AudioError(f"unsupported PCM width {bits}")
    else:
        raise AudioError(f"unsupported WAVE format tag {tag}")
    return out


def decode_wav_bytes(data: bytes) -> Audio:
    """Decode a WAV payload into mono float samples.

    Multi-channel input is downmixed by averaging: the advisor bus is mono, and
    the TTS channels are already identical in practice.
    """
    fmt = None
    pcm_parts: list[array.array] = []
    for chunk_id, body in _iter_chunks(data):
        if chunk_id == b"fmt " and fmt is None:
            fmt = _parse_fmt(body)
        elif chunk_id == b"data":
            pcm_parts.append(body)
    if fmt is None:
        raise AudioError("missing fmt chunk")
    if not pcm_parts:
        raise AudioError("missing data chunk")

    tag, channels, rate, bits = fmt
    if channels < 1:
        raise AudioError("fmt chunk reports zero channels")

    interleaved = array.array("f")
    for part in pcm_parts:
        interleaved.extend(_decode_pcm(part, bits, tag))

    if channels == 1:
        return Audio(interleaved, rate)

    frames = len(interleaved) // channels
    mono = array.array("f", [0.0]) * frames
    inv = 1.0 / channels
    for frame in range(frames):
        base = frame * channels
        mono[frame] = sum(interleaved[base:base + channels]) * inv
    return Audio(mono, rate)


def _resample_numpy(samples: array.array, src: int, dst: int) -> array.array:
    from math import gcd

    import numpy as np
    from scipy.signal import resample_poly

    up, down = dst // gcd(src, dst), src // gcd(src, dst)
    arr = np.frombuffer(samples, dtype=np.float32)
    out = resample_poly(arr, up, down)
    return array.array("f", out.astype(np.float32).tolist())


def _resample_linear(samples: array.array, src: int, dst: int) -> array.array:
    """Pure-stdlib linear-interpolation resampler.

    Fallback only. Linear interpolation of a 24k source up to 44.1k leaves the
    images above 12 kHz in place, which is audible as a slight harshness on
    sibilants; scipy's polyphase path is the one we actually want.
    """
    if src == dst:
        return array.array("f", samples)
    n_out = int(round(len(samples) * dst / float(src)))
    if n_out <= 1:
        return array.array("f", samples)
    out = array.array("f", [0.0]) * n_out
    last = len(samples) - 1
    step = src / float(dst)
    for i in range(n_out):
        pos = i * step
        left = int(pos)
        if left >= last:
            out[i] = samples[last]
            continue
        frac = pos - left
        out[i] = samples[left] * (1.0 - frac) + samples[left + 1] * frac
    return out


def resample_mono(samples: array.array, src_rate: int, dst_rate: int) -> array.array:
    """Resample mono float samples. Uses scipy when available."""
    if src_rate <= 0 or dst_rate <= 0:
        raise AudioError(f"invalid sample rate {src_rate} -> {dst_rate}")
    if src_rate == dst_rate:
        return array.array("f", samples)
    if not len(samples):
        return array.array("f")
    try:
        return _resample_numpy(samples, src_rate, dst_rate)
    except Exception:
        return _resample_linear(samples, src_rate, dst_rate)


def trim_silence(samples: array.array, *, threshold: float = 0.012,
                 pad_ms: int = 25, rate: int = TARGET_RATE) -> array.array:
    """Trim leading/trailing near-silence, keeping a short pad.

    The TTS service pads every clip with a little lead-in silence; in-game that
    reads as the advisor answering late, because the notification is already on
    screen by the time the voice starts.
    """
    if not len(samples):
        return samples
    first, last = 0, len(samples) - 1
    while first <= last and abs(samples[first]) < threshold:
        first += 1
    while last >= first and abs(samples[last]) < threshold:
        last -= 1
    if first > last:
        return array.array("f")  # entirely silent
    pad = max(1, int(rate * pad_ms / 1000.0))
    return array.array("f", samples[max(0, first - pad):min(len(samples), last + 1 + pad)])


def to_pcm16_bytes(samples: array.array) -> bytes:
    """Pack float samples into little-endian signed 16-bit PCM with clipping."""
    out = array.array("h", bytes(2 * len(samples)))
    for i, value in enumerate(samples):
        scaled = int(value * 32767.0)
        if scaled > 32767:
            scaled = 32767
        elif scaled < -32768:
            scaled = -32768
        out[i] = scaled
    if struct.pack("<h", 1) != b"\x01\x00":  # pragma: no cover - big-endian host
        out.byteswap()
    return out.tobytes()


def wav_bytes_mono16(samples: array.array, rate: int = TARGET_RATE) -> bytes:
    """Serialise mono PCM 16-bit WAV bytes in exactly the game's format."""
    return wrap_pcm16_wav(to_pcm16_bytes(samples), rate)


def wrap_pcm16_wav(pcm: bytes, rate: int, channels: int = 1) -> bytes:
    """Wrap already-encoded little-endian PCM 16-bit bytes in a WAV container.

    Used to persist the provider's raw stream without a decode/encode round
    trip, which would be lossless anyway but costs time on every cached clip.
    """
    buf = io.BytesIO()
    with wave.open(buf, "wb") as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(TARGET_SAMPLE_WIDTH)
        handle.setframerate(rate)
        handle.writeframes(pcm)
    return buf.getvalue()


def write_wav_mono16(path: str | Path, samples: array.array, rate: int = TARGET_RATE) -> int:
    """Write a game-ready WAV and return its size in bytes."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = wav_bytes_mono16(samples, rate)
    target.write_bytes(payload)
    return len(payload)


def prepare_for_game(wav_payload: bytes, *, do_trim: bool = True) -> tuple[array.array, int]:
    """TTS WAV bytes -> (mono 44100 Hz float samples ready to write)."""
    decoded = decode_wav_bytes(wav_payload)
    resampled = resample_mono(decoded.samples, decoded.rate, TARGET_RATE)
    if do_trim:
        resampled = trim_silence(resampled, rate=TARGET_RATE)
    return resampled, TARGET_RATE


def describe_wav(payload: bytes) -> str:
    """Human-readable one-liner about a WAV payload, for logs and diagnostics."""
    try:
        audio = decode_wav_bytes(payload)
        return (f"{audio.rate}Hz mono {audio.duration_s:.2f}s "
                f"({len(payload)} bytes payload)")
    except AudioError as exc:
        return f"<unreadable: {exc}>"
