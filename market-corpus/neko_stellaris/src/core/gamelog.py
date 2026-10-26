# -*- coding: utf-8 -*-
# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

"""Tail and parse Stellaris' ``logs/game.log``.

This is the *inbound* half of the integration, and it is the only direction the
engine actually supports: Clausewitz mods cannot read external files, but they
can write to the log, and the base game already does. Two useful line shapes
were confirmed against a real 4.5.2 log:

    [05:40:22][eventcommands.cpp:88]: Event crisis.7515 added info about event
        selection. selectedOption 0, human 1, playerEventId 1705

        -> every event the player was offered, with `human 1` marking the ones
           that involved the player rather than an AI empire. This is a
           ready-made, mod-free event stream.

    [05:37:32][effect_impl.cpp:22217]: [2395.1.1] Log effect, file: ... line: 179.
        Spawning Astral Scar in system 戈洛维亚 for country 和平波万奈特国度.

        -> the base game's own ``log`` effect calls, and the channel our
           companion mod uses to emit structured events.

Two details that look like trivia and are not:

* The file is **UTF-8**, not the system codepage. Decoding it as GBK turns
  帝国 names into mojibake, and those names end up in prompts.
* Reads can split a multi-byte character and a write can land mid-line, so the
  reader keeps an incremental decoder plus a partial-line buffer. A naive
  ``readline`` on a growing file drops or corrupts exactly the newest line --
  the one worth reacting to.
"""

from __future__ import annotations

import codecs
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

logger = logging.getLogger("neko_stellaris.gamelog")

# Marker our companion mod writes so the payload can be found regardless of the
# surrounding engine prefix, which varies by call site (effect_impl vs scripted
# effect) and by game version.
BRIDGE_MARKER = "NEKO"
BRIDGE_VERSION = "v1"

_EVENT_RE = re.compile(
    r"Event\s+(?P<id>[A-Za-z0-9_.\-]+)\s+added info about event selection\.\s*"
    r"selectedOption\s+(?P<option>-?\d+),\s*human\s+(?P<human>-?\d+),\s*"
    r"playerEventId\s+(?P<player_event>-?\d+)"
)
_VERSION_RE = re.compile(r"Game Version:\s*(?P<version>.+?)\s*$")

# In-game date prefix, e.g. "[2395.1.1]", present on `log` effect lines.
_DATE_RE = re.compile(r"\[(?P<year>\d{4})\.(?P<month>\d{1,2})\.(?P<day>\d{1,2})\]")
# Wall-clock prefix every line carries, e.g. "[05:37:32]". Vanilla
# event-selection lines have *only* this -- no in-game date -- so it is the only
# ordering signal available for them.
_TIME_RE = re.compile(r"^\[(?P<h>\d{2}):(?P<m>\d{2}):(?P<s>\d{2})\]")


@dataclass(frozen=True)
class GameEvent:
    """One parsed event worth considering for commentary."""

    kind: str                 # "player_event" | "bridge" | "session"
    goal: str                 # bridge goal, or "vanilla.<namespace>" for player events
    payload: dict[str, str] = field(default_factory=dict)
    event_id: str = ""
    option: int = -1
    human: int = -1
    player_event_id: int = -1
    game_date: str = ""
    log_time: str = ""
    raw: str = ""
    seen_at: float = field(default_factory=time.time)

    def key(self) -> str:
        """Dedup identity for one *occurrence* of something.

        The tailer already reads each byte exactly once, so this is not about
        normal operation: it exists so a restart's backfill does not re-announce
        recent history, and so a symmetric event that the engine fires once per
        side (the audit flagged `on_status_quo` and the communications hooks as
        doing exactly that) collapses to one.

        ``log_time`` is part of the key because goal + date + payload alone is
        *too* coarse: two genuinely separate occurrences on the same in-game date
        with identical fields -- two research projects finishing in the same
        month, say -- would be silently swallowed as duplicates. The wall clock
        differs between the two, while a re-read of the same line reproduces it
        exactly.
        """
        if self.kind == "player_event":
            return f"ev:{self.event_id}:{self.player_event_id}"
        if self.kind == "bridge":
            return (f"br:{self.goal}:{self.log_time}:{self.game_date}:"
                    f"{sorted(self.payload.items())}")
        return f"{self.kind}:{self.raw[:64]}"

    def _stamp(self) -> str:
        """Best available timestamp: in-game date, else wall clock, else nothing.

        Vanilla event-selection lines carry no in-game date, so falling back to
        the log's wall clock keeps them ordered instead of rendering as an empty
        bracket pair.
        """
        if self.game_date:
            return f"[{self.game_date}] "
        if self.log_time:
            return f"[{self.log_time}] "
        return ""

    def describe(self) -> str:
        if self.kind == "bridge":
            detail = ", ".join(f"{k}={v}" for k, v in self.payload.items() if v)
            return f"{self._stamp()}{self.goal}" + (f" ({detail})" if detail else "")
        if self.kind == "player_event":
            namespace = self.event_id.split(".")[0] if "." in self.event_id else self.event_id
            return (f"{self._stamp()}事件 {self.event_id}（{namespace} 分支，"
                    f"玩家选项 {self.option}）")
        return self.raw[:120]


def _stamps(line: str) -> tuple[str, str]:
    """Return (in-game date, wall clock), either possibly empty."""
    date_match = _DATE_RE.search(line)
    time_match = _TIME_RE.search(line)
    game_date = _format_date(date_match) if date_match else ""
    log_time = (f"{time_match.group('h')}:{time_match.group('m')}:{time_match.group('s')}"
                if time_match else "")
    return game_date, log_time


def parse_line(line: str) -> GameEvent | None:
    """Parse one log line into a :class:`GameEvent`, or ``None`` if uninteresting."""
    if BRIDGE_MARKER in line:
        bridge = _parse_bridge(line)
        if bridge is not None:
            return bridge

    match = _EVENT_RE.search(line)
    if match:
        event_id = match.group("id")
        namespace = event_id.split(".")[0] if "." in event_id else event_id
        game_date, log_time = _stamps(line)
        return GameEvent(
            kind="player_event",
            goal=f"vanilla.{namespace}",
            event_id=event_id,
            option=int(match.group("option")),
            human=int(match.group("human")),
            player_event_id=int(match.group("player_event")),
            game_date=game_date,
            log_time=log_time,
            raw=line,
        )

    version = _VERSION_RE.search(line)
    if version:
        _, log_time = _stamps(line)
        return GameEvent(kind="session", goal="game_version",
                         payload={"version": version.group("version").strip()},
                         log_time=log_time, raw=line)
    return None


def _format_date(match: re.Match[str]) -> str:
    return f"{match.group('year')}.{int(match.group('month'))}.{int(match.group('day'))}"


def _parse_bridge(line: str) -> GameEvent | None:
    """Parse ``NEKO|v1|<goal>|k=v|k=v`` embedded anywhere in the line.

    Deliberately tolerant: the payload is located by marker rather than by
    matching the engine's log prefix, because that prefix differs between a
    plain ``log`` effect and one invoked from a scripted effect, and it has
    changed between game versions before.
    """
    index = line.find(BRIDGE_MARKER + "|")
    if index < 0:
        return None
    payload_text = line[index:].strip()
    parts = [p.strip() for p in payload_text.split("|")]
    if len(parts) < 3 or parts[0] != BRIDGE_MARKER:
        return None

    version = parts[1]
    if version != BRIDGE_VERSION:
        # Unknown schema: surface it once rather than guessing at field meaning.
        logger.info("忽略未知的桥接载荷版本 %r", version)
        return None

    goal = parts[2]
    if not goal:
        return None

    fields: dict[str, str] = {}
    for chunk in parts[3:]:
        if not chunk:
            continue
        key, sep, value = chunk.partition("=")
        if not sep:
            continue
        if sep:
            fields[key.strip()] = value.strip()

    game_date, log_time = _stamps(line)
    return GameEvent(
        kind="bridge",
        goal=goal,
        payload=fields,
        game_date=game_date,
        log_time=log_time,
        raw=line,
    )


class GameLogTailer:
    """Incremental, restart-safe reader for a growing UTF-8 log file.

    Handles the three things that actually go wrong when tailing a game log:

    * the game truncates or replaces the file on restart -> detect a smaller
      size and rewind;
    * a read lands mid-line -> hold the tail in a buffer until a newline shows
      up, rather than emitting a fragment as if it were a whole line;
    * a read lands mid-character -> keep the decoder's partial state.
    """

    def __init__(self, path: str | Path, *, from_start: bool = False,
                 max_backfill_bytes: int = 262144):
        self.path = Path(path)
        self._offset = 0 if from_start else None
        self._buffer = ""
        self._decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self._max_backfill = max_backfill_bytes
        self._identity: tuple[int, int] | None = None

    def _stat(self) -> tuple[int, int] | None:
        try:
            stat = self.path.stat()
        except OSError:
            return None
        return stat.st_size, stat.st_mtime_ns

    def reset(self, *, to_end: bool = True) -> None:
        """Forget position. ``to_end`` skips history (the usual choice at start)."""
        self._buffer = ""
        self._decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self._identity = None
        if to_end:
            stat = self._stat()
            self._offset = stat[0] if stat else 0
        else:
            self._offset = 0

    def prime(self) -> None:
        """Position at end-of-file if we have never read anything.

        A session that starts mid-game should not replay an hour of history, but
        a bounded backfill is still useful: it recovers the tail of whatever just
        happened, so the first thing the character says is not context-free.
        """
        if self._offset is not None:
            return
        stat = self._stat()
        if stat is None:
            self._offset = 0
            return
        size = stat[0]
        if size <= self._max_backfill:
            self._offset = 0
        else:
            self._offset = size - self._max_backfill
            # Skip the partial line the backfill starts in the middle of.
            try:
                with self.path.open("rb") as handle:
                    handle.seek(self._offset)
                    handle.readline()
                    self._offset = handle.tell()
            except OSError:
                self._offset = size

    def read_new_lines(self) -> list[str]:
        """Return complete new lines since the last call."""
        if self._offset is None:
            self.prime()

        stat = self._stat()
        if stat is None:
            return []
        size, mtime = stat

        if self._identity is None:
            self._identity = (size, mtime)
        if size < self._offset:
            # Truncated (new session) or replaced: start over from the top.
            logger.info("game.log 被截断或轮转，重新从 0 读取")
            self._offset = 0
            self._buffer = ""
            self._decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self._identity = (size, mtime)

        if size == self._offset:
            return []

        try:
            with self.path.open("rb") as handle:
                handle.seek(self._offset)
                chunk = handle.read(size - self._offset)
                self._offset = handle.tell()
        except OSError as exc:
            logger.debug("读取 game.log 失败: %s", exc)
            return []

        text = self._buffer + self._decoder.decode(chunk)
        if text.endswith("\n"):
            self._buffer = ""
            complete = text.splitlines()
        else:
            complete = text.splitlines()
            self._buffer = complete.pop() if complete else ""
        return [line for line in complete if line.strip()]


def iter_new_events(tailer: GameLogTailer) -> Iterator[GameEvent]:
    """Parse whatever is new, skipping lines that carry no event."""
    for line in tailer.read_new_lines():
        event = parse_line(line)
        if event is not None:
            yield event


def find_game_log(stellaris_user_dir: str | Path) -> Path | None:
    """``<user>/logs/game.log``, the file the base game writes events to."""
    candidate = Path(stellaris_user_dir) / "logs" / "game.log"
    return candidate if candidate.is_file() else None


def describe_log(stellaris_user_dir: str | Path) -> str:
    """One-line diagnostics for the plugin status entry."""
    path = find_game_log(stellaris_user_dir)
    if path is None:
        return "未找到 logs/game.log（游戏还没运行过？）"
    stat = path.stat()
    age = max(0.0, time.time() - stat.st_mtime)
    return (f"{path.name}: {stat.st_size / 1024:.0f} KiB，"
            f"{age:.0f} 秒前更新")
