"""Read-only compatibility adapter for hosts without a reply-complete SDK event.

Only new, character-specific Main log records can complete a ticket. Later
input invalidates its old reply. Health is an extra busy guard, never a
substitute for the log event. No chat is persisted.
"""

from __future__ import annotations

import json
import logging
import os
import re
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .lanlan import LanlanResolver

_READ_LIMIT = 256 * 1024
_LINE_LIMIT = 64 * 1024
_MAIN_FILENAME = re.compile(r"N\.E\.K\.O_Main_\d{8}\.log")
_END_LINE = re.compile(
    rb"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} - "
    rb"N\.E\.K\.O\.Main\.main_logic\.cross_server - INFO - "
    rb"\[(.+)\] turn_end analyze check: history=\d+ recent=\d+ "
    rb"has_user=(?:True|False) had_input=(?:True|False)"
)
_INPUT_LINE = re.compile(
    rb"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} - "
    rb"N\.E\.K\.O\.Main\.main_logic\.core - INFO - "
    rb"\[(.+)\] voice user_transcript session=\S+ "
    rb"ws_connected=(?:True|False) len=[1-9]\d*\r?$"
)


def log_directories(data_root: Path) -> list[Path]:
    """Prefer this process's storage/logging paths over legacy user locations."""
    candidates: list[Path] = []
    selected = os.environ.get("NEKO_STORAGE_SELECTED_ROOT")
    if selected and Path(selected).is_absolute():
        candidates.append(Path(selected) / "logs")
    for handler in logging.getLogger().handlers:
        filename = getattr(handler, "baseFilename", None)
        if filename:
            parent = Path(filename).parent
            candidates.append(parent.parent if parent.name == "plugin" else parent)
    for parent in data_root.parents:
        if parent.name == "plugins":
            candidates.append(parent.parent / "logs")
            break
    local = os.environ.get("LOCALAPPDATA")
    if local:
        candidates.append(Path(local) / "N.E.K.O" / "logs")
    candidates.append(Path.home() / "Documents" / "N.E.K.O" / "logs")
    return list(dict.fromkeys(candidates))


@dataclass
class EndTicket:
    path: Path
    identity: tuple[int, int]
    offset: int
    character: bytes
    anchor: bytes = b""
    partial: bytes = b""
    skip_line: bool = False
    matched: bool = False
    invalid: bool = False
    end_count: int = 0
    superseded: bool = False
    caught_up: bool = True
    session_changed: bool = False


class TurnEndLogs:
    def __init__(self, plugin: Any, directories: list[Path]):
        self._plugin = plugin
        self._directories = directories

    def arm(self, lanlan: str) -> EndTicket | None:
        if not lanlan:
            return None
        for directory in self._directories:
            try:
                paths = [
                    path for path in directory.glob("N.E.K.O_Main_*.log")
                    if _MAIN_FILENAME.fullmatch(path.name)
                ]
                paths.sort(key=lambda p: p.stat().st_mtime_ns, reverse=True)
                for path in paths:
                    with path.open("rb") as stream:
                        stat = os.fstat(stream.fileno())
                        if stat.st_size:
                            stream.seek(stat.st_size - 1)
                            skip_line = stream.read(1) != b"\n"
                        else:
                            skip_line = False
                        stream.seek(max(0, stat.st_size - 64))
                        anchor = stream.read(64)
                        return EndTicket(
                            path, (stat.st_dev, stat.st_ino), stat.st_size,
                            lanlan.encode("utf-8"), anchor=anchor, skip_line=skip_line,
                        )
            except OSError:
                continue
        return None

    def poll(self, ticket: EndTicket) -> bool:
        if ticket.invalid:
            return False
        # Open briefly: a retained Windows file handle can block host rotation.
        candidates = [ticket.path] + [
            ticket.path.with_name(f"{ticket.path.name}.{n}") for n in range(1, 6)
        ]
        for path in candidates:
            try:
                with path.open("rb") as stream:
                    stat = os.fstat(stream.fileno())
                    if (stat.st_dev, stat.st_ino) != ticket.identity:
                        continue
                    if stat.st_size < ticket.offset:
                        ticket.invalid = True
                        return False
                    stream.seek(ticket.offset - len(ticket.anchor))
                    if stream.read(len(ticket.anchor)) != ticket.anchor:
                        ticket.invalid = True
                        return False
                    stream.seek(ticket.offset)
                    chunk = stream.read(_READ_LIMIT)
                    ticket.offset += len(chunk)
                    ticket.anchor = (ticket.anchor + chunk)[-64:]
                self._consume(ticket, chunk)
                ticket.caught_up = (
                    ticket.offset == stat.st_size and not ticket.partial and not ticket.skip_line
                )
                if path != ticket.path and ticket.offset == stat.st_size:
                    # The old file is exhausted; continue in the new active file.
                    with ticket.path.open("rb") as stream:
                        current = os.fstat(stream.fileno())
                    ticket.identity = (current.st_dev, current.st_ino)
                    ticket.offset = 0
                    ticket.anchor = b""
                    ticket.caught_up = False
                return ticket.matched and ticket.caught_up
            except OSError:
                continue
        ticket.invalid = True
        return False

    @staticmethod
    def _consume(ticket: EndTicket, chunk: bytes) -> None:
        lines = (ticket.partial + chunk).split(b"\n")
        ticket.partial = lines.pop()
        for line in lines:
            if ticket.skip_line:
                ticket.skip_line = False
                continue
            if (
                b"Voice lifecycle received action=start_session" in line
                or b"Voice lifecycle received action=end_session" in line
                or b"session end " in line
                or b"current_catgirl" in line
            ):
                ticket.session_changed = True
            match = _END_LINE.match(line)
            if match and match.group(1) == ticket.character:
                ticket.end_count += 1
                if ticket.end_count > 1:
                    ticket.invalid = True
                ticket.matched = True
            else:
                match = _INPUT_LINE.match(line)
                if match and match.group(1) == ticket.character:
                    ticket.superseded = True
        if len(ticket.partial) > _LINE_LIMIT:
            ticket.partial = b""
            ticket.skip_line = True

    def busy_states(self) -> dict[str, bool]:
        """Cheap loopback-only diagnostic, bounded even if the ring is large."""
        url = LanlanResolver(self._plugin)._api_base() + "/api/debug/health"
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(url, timeout=0.6) as response:
                data = response.read(1024 * 1024 + 1)
            if len(data) > 1024 * 1024:
                return {}
            payload = json.loads(data)
            states = payload["current"]["is_responding"]
            if not isinstance(states, dict):
                return {}
            return {key: value for key, value in states.items() if isinstance(key, str) and isinstance(value, bool)}
        except (OSError, ValueError, KeyError, TypeError):
            return {}
