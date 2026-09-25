"""Atomic JSON persistence for high-level maid skill runs."""

from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from pathlib import Path
from typing import Iterable

from .base import SkillRun


class StaleCheckpointError(RuntimeError):
    pass


class SkillCheckpointStore:
    def __init__(self, directory, *, terminal_ttl: float = 600.0, clock=None):
        self.directory = Path(directory)
        self.terminal_ttl = max(0.0, float(terminal_ttl))
        self._clock = clock or time.time
        self._lock = asyncio.Lock()

    async def load_all(self) -> list[SkillRun]:
        async with self._lock:
            return await asyncio.to_thread(self._load_all_sync)

    async def save(self, run: SkillRun) -> None:
        self._canonical_uuid(run.skill_id)
        async with self._lock:
            await asyncio.to_thread(self._save_sync, run.as_dict())

    async def delete(self, skill_id: str) -> None:
        canonical = self._canonical_uuid(skill_id)
        async with self._lock:
            await asyncio.to_thread(self._delete_sync, canonical)

    async def purge_expired(self, runs: Iterable[SkillRun]) -> list[str]:
        now = float(self._clock())
        expired = [
            run.skill_id for run in runs
            if run.terminal and now - run.updated_at >= self.terminal_ttl
        ]
        if not expired:
            return []
        async with self._lock:
            await asyncio.to_thread(self._delete_many_sync, expired)
        return expired

    def _load_all_sync(self) -> list[SkillRun]:
        self.directory.mkdir(parents=True, exist_ok=True)
        runs = []
        now = float(self._clock())
        for path in sorted(self.directory.glob("*.json")):
            try:
                canonical = self._canonical_uuid(path.stem)
                if canonical != path.stem.lower():
                    continue
                with path.open("r", encoding="utf-8") as handle:
                    run = SkillRun.from_dict(json.load(handle))
                if self._canonical_uuid(run.skill_id) != canonical:
                    continue
                if run.terminal and now - run.updated_at >= self.terminal_ttl:
                    path.unlink(missing_ok=True)
                    continue
                runs.append(run)
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                # A corrupt/unknown checkpoint is deliberately left in place for
                # diagnosis.  It must never be guessed into executable state.
                continue
        return runs

    def _save_sync(self, payload) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        skill_id = self._canonical_uuid(payload.get("skill_id"))
        destination = self.directory / f"{skill_id}.json"
        incoming_revision = int(payload.get("revision", 0))
        if destination.exists():
            try:
                with destination.open("r", encoding="utf-8") as handle:
                    stored_revision = int(json.load(handle).get("revision", 0))
            except (OSError, TypeError, ValueError, json.JSONDecodeError):
                stored_revision = -1
            if stored_revision > incoming_revision:
                raise StaleCheckpointError(
                    f"Refusing revision {incoming_revision}; stored revision is {stored_revision}"
                )
        temporary = self.directory / f".{skill_id}.{uuid.uuid4().hex}.tmp"
        try:
            with temporary.open("w", encoding="utf-8", newline="\n") as handle:
                json.dump(
                    payload,
                    handle,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    default=str,
                )
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            for attempt in range(5):
                try:
                    os.replace(temporary, destination)
                    break
                except PermissionError:
                    if attempt == 4:
                        raise
                    # Windows may briefly retain a sharing lock after a reader
                    # closes the previous checkpoint. Retry the atomic replace.
                    time.sleep(0.01 * (attempt + 1))
        finally:
            temporary.unlink(missing_ok=True)

    def _delete_sync(self, canonical: str) -> None:
        (self.directory / f"{canonical}.json").unlink(missing_ok=True)

    def _delete_many_sync(self, skill_ids) -> None:
        for skill_id in skill_ids:
            try:
                canonical = self._canonical_uuid(skill_id)
            except ValueError:
                continue
            self._delete_sync(canonical)

    @staticmethod
    def _canonical_uuid(value) -> str:
        text = str(value or "").strip().lower()
        try:
            parsed = uuid.UUID(text)
        except (AttributeError, TypeError, ValueError) as exc:
            raise ValueError("skill_id must be a UUID") from exc
        canonical = str(parsed)
        if text != canonical:
            raise ValueError("skill_id must use canonical UUID form")
        return canonical
