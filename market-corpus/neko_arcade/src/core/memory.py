"""游戏经历记忆：短期会话 + 长期里程碑（store 持久化）。"""

from __future__ import annotations

import time
from typing import Any, Dict, List

STORE_KEY = "game_memories"


class GameMemory:
    """记忆库。"""

    def __init__(self, plugin: Any) -> None:
        self.plugin = plugin
        self._session: List[Dict[str, Any]] = []
        self._milestones: Dict[str, Any] = {}

    async def _load(self) -> Dict[str, Any]:
        if not self._milestones:
            data = await self.plugin.store.get(STORE_KEY)
            # SDK store.get 返回 Ok/Err 包装（数据在 Ok.value），统一解开
            if hasattr(data, "is_err") and callable(data.is_err) and data.is_err():
                data = None
            elif hasattr(data, "value"):
                data = data.value
            elif hasattr(data, "data"):
                data = data.data
            self._milestones = data if isinstance(data, dict) else {}
        return self._milestones

    async def _save(self) -> None:
        await self.plugin.store.set(STORE_KEY, self._milestones)

    def record(self, game_id: str, outcome: str, facts: List[Dict], text: str) -> None:
        self._session.append({"ts": time.time(), "game": game_id, "outcome": outcome,
                              "facts": facts[:5], "neko_said": text[:80]})
        if len(self._session) > 20:
            self._session = self._session[-20:]

    def recent_session(self, n: int = 5) -> str:
        lines = []
        for e in self._session[-n:]:
            names = [str(f.get("name", f.get("item", ""))) for f in (e.get("facts") or []) if f.get("name") or f.get("item")]
            lines.append(f"[{e['game']}] {e['outcome']}" + (f"（{', '.join(names)}）" if names else ""))
        return "；".join(lines)

    async def bump_stat(self, game_id: str, key: str, amount: int = 1) -> None:
        m = await self._load()
        stats = m.setdefault("stats", {}).setdefault(game_id, {})
        stats[key] = stats.get(key, 0) + amount
        await self._save()

    async def set_record(self, game_id: str, key: str, value: Any, label: str = "") -> bool:
        m = await self._load()
        records = m.setdefault("records", {}).setdefault(game_id, {})
        old = records.get(key)
        if old is None or (isinstance(value, (int, float)) and value > old):
            records[key] = {"value": value, "label": label, "ts": time.time()}
            await self._save()
            return True
        return False

    async def record_species(self, game_id: str, name: str) -> bool:
        m = await self._load()
        seen = m.setdefault("species", {}).setdefault(game_id, [])
        if name in seen:
            return False
        seen.append(name)
        await self._save()
        return True

    async def register_play(self, game_id: str) -> None:
        m = await self._load()
        stats = m.setdefault("stats", {}).setdefault(game_id, {})
        stats["plays"] = stats.get("plays", 0) + 1
        stats["last_play_ts"] = time.time()
        await self._save()

    async def get_stats(self, game_id: str) -> Dict[str, Any]:
        m = await self._load()
        return m.get("stats", {}).get(game_id, {})

    async def snapshot(self) -> Dict[str, Any]:
        m = await self._load()
        summary = {}
        for gid, st in (m.get("stats", {}) or {}).items():
            summary[gid] = {"plays": st.get("plays", 0), "last": st.get("last_play_ts")}
        return {"games": summary, "session": self.recent_session()}