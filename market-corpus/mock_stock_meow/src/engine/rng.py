"""Deterministic random source derivation.

Every random draw in the engine goes through :func:`derive_rng` so that the
same game seed plus the same context tuple always reproduces the same stream.

Why not a single global ``random.Random(seed)`` instance?  Because the engine
must be serialisable: ``MarketState`` / ``Player`` / ``Trade`` are plain data
that get written to JSON at settlement time, and a live RNG object cannot be
stored.  Deriving a fresh RNG from ``(seed, day, tick, ...)`` keeps the whole
game state serialisable while staying bit-for-bit reproducible.

The derivation uses :mod:`hashlib` instead of the builtin ``hash()`` on
purpose: ``hash()`` of a ``str`` is randomised per process by
``PYTHONHASHSEED`` and would break reproducibility across runs.
"""

from __future__ import annotations

import hashlib
import random

__all__ = ["derive_rng", "derive_stream_rng"]


def derive_rng(seed: int, *parts: int) -> random.Random:
    """Return a reproducible :class:`random.Random` for ``seed`` and ``parts``.

    The same arguments always produce the same stream, in this process and in
    any other process on any platform.

    >>> derive_rng(7, 1, 2).random() == derive_rng(7, 1, 2).random()
    True
    >>> derive_rng(7, 1, 2).random() == derive_rng(7, 1, 3).random()
    False
    """
    digest = hashlib.blake2b(digest_size=8)
    digest.update(str(int(seed)).encode("ascii"))
    for part in parts:
        digest.update(b"|")
        digest.update(str(int(part)).encode("ascii"))
    return random.Random(int.from_bytes(digest.digest(), "big"))


def derive_stream_rng(seed: int, stream_id: int, *parts: int) -> random.Random:
    """Return the random stream of ``stream_id`` (P3 契约 §15).

    Every subsystem draws from its **own** stream id, so changing one stream
    never perturbs the others.  ``stream_id`` must be an int constant defined
    in :mod:`engine.config` (``STREAM_MARKET`` and friends) -- never a string,
    because the derivation only accepts ints.

    >>> derive_stream_rng(7, 100_001, 1).random() == derive_stream_rng(7, 100_001, 1).random()
    True
    >>> derive_stream_rng(7, 100_001, 1).random() != derive_stream_rng(7, 100_002, 1).random()
    True
    """
    return derive_rng(seed, int(stream_id), *parts)
