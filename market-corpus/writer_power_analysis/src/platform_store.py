"""JSON file store for writer_power_analysis platform presets.

Stores user-saved API endpoint configurations as a JSON array file
on disk, similar to how qq_auto_reply's QQAutoReplyConfigStore
persists its business config.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from utils.file_utils import atomic_write_json_async, read_json_async


class PlatformPresetStore:
    """Persist platform presets to a JSON file in the plugin data dir."""

    FILE_NAME = "platform_presets.json"

    def __init__(self, base_dir: Path):
        self._path = Path(base_dir) / self.FILE_NAME
        self._lock = asyncio.Lock()

    @property
    def path(self) -> Path:
        return self._path

    async def load_all(self) -> list[dict[str, Any]]:
        """Read all saved presets. Returns empty list if file missing."""
        if not self._path.is_file():
            return []
        payload = await read_json_async(self._path)
        if not isinstance(payload, list):
            return []
        return [
            item for item in payload
            if isinstance(item, dict)
            and isinstance(item.get("id"), str)
            and isinstance(item.get("baseUrl"), str)
        ]

    async def save(self, preset: dict[str, Any]) -> list[dict[str, Any]]:
        """Add or update a preset (dedup by baseUrl), then write to disk."""
        async with self._lock:
            all_presets = await self.load_all()
            # dedup by baseUrl
            filtered = [p for p in all_presets if p.get("baseUrl") != preset.get("baseUrl")]
            filtered.append({
                "id": str(preset.get("id") or ""),
                "label": str(preset.get("label") or ""),
                "baseUrl": str(preset.get("baseUrl") or ""),
                "modelListPath": str(preset.get("modelListPath") or "/v1/models"),
                "model": str(preset.get("model") or ""),
                "apiKey": str(preset.get("apiKey") or ""),
            })
            await atomic_write_json_async(self._path, filtered)
            return filtered

    async def delete(self, preset_id: str) -> list[dict[str, Any]]:
        """Remove a preset by id."""
        async with self._lock:
            all_presets = await self.load_all()
            kept = [p for p in all_presets if p.get("id") != preset_id]
            await atomic_write_json_async(self._path, kept)
            return kept
