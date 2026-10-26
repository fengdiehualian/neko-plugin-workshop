"""记账猫娘 · 读取当前猫娘的人设文本。

用途：判断人设是否允许「哈气」。**只读，绝不写角色卡** ——
记账插件没有任何理由修改用户的人设数据。

读取方式与角色卡文件同源，但不做任何写入；路径解析失败时安静降级
（返回空人设 → 采用默认语气），不因此让记账功能不可用。
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any, Optional

CATGIRL_KEY = "猫娘"
CURRENT_KEY = "当前猫娘"

_DEFAULT_CONFIG = Path(os.environ.get("LOCALAPPDATA", "")) / "N.E.K.O" / "config" / "characters.json"


def _default_candidates() -> list[Path]:
    out: list[Path] = []
    local = os.environ.get("LOCALAPPDATA")
    if local:
        out.append(Path(local) / "N.E.K.O" / "config" / "characters.json")
    out.append(_DEFAULT_CONFIG)
    seen: set[str] = set()
    uniq: list[Path] = []
    for p in out:
        key = str(p).lower()
        if str(p) and key not in seen:
            seen.add(key)
            uniq.append(p)
    return uniq


class PersonaReader:
    """只读的角色卡访问。"""

    def __init__(self, config_path: Optional[str] = None, *, timeout: float = 10.0):
        self.timeout = timeout
        self.config_path = self._resolve(config_path)

    @staticmethod
    def _resolve(explicit: Optional[str]) -> Optional[Path]:
        if explicit:
            p = Path(explicit)
            return p if p.is_file() else None
        for p in _default_candidates():
            if p.is_file():
                return p
        return None

    def _read_sync(self) -> Optional[dict[str, Any]]:
        if not self.config_path:
            return None
        try:
            data = json.loads(self.config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return data if isinstance(data, dict) else None

    async def load(self) -> Optional[dict[str, Any]]:
        return await asyncio.to_thread(self._read_sync)

    async def current_catgirl(self, name: Optional[str] = None) -> Optional[str]:
        data = await self.load()
        if not data:
            return None
        if name:
            # 显式指定时确认存在
            catgirls = data.get(CATGIRL_KEY) or {}
            return name if name in catgirls else None
        current = data.get(CURRENT_KEY)
        if isinstance(current, str) and current.strip():
            return current.strip()
        catgirls = data.get(CATGIRL_KEY)
        if isinstance(catgirls, dict) and catgirls:
            return str(next(iter(catgirls)))
        return None

    async def persona_text(self, name: Optional[str] = None) -> str:
        """把该猫娘的所有文本字段拼成一段，供人设检测用。"""
        data = await self.load()
        if not data:
            return ""
        resolved = name or await self.current_catgirl()
        if not resolved:
            return ""
        entry = (data.get(CATGIRL_KEY) or {}).get(resolved)
        if not isinstance(entry, dict):
            return ""
        parts: list[str] = []
        for key, value in entry.items():
            if key == "_reserved":
                continue
            if isinstance(value, str) and value.strip():
                parts.append(value)
        return "\n".join(parts)
