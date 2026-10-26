"""Session-authoritative item identities, with a disk cache for display only."""

import json
import threading
from pathlib import Path
from typing import Any, Dict, List


class ModItemRegistry:
    def __init__(self, base_dir: str) -> None:
        self.dir = Path(base_dir) / "data" / "mod_items"
        self.dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.mods = {}
        self.uses = {}
        self.tags = {}
        self._records = {}
        self._aliases = {}
        self._by_id = {}
        self.live = False
        self.load_cached()

    @staticmethod
    def _normalize(name: str) -> str:
        return "".join(str(name or "").casefold().replace("_", "").split())

    def _rebuild(self) -> None:
        self.mods, self.uses, self.tags = {}, {}, {}
        self._aliases, self._by_id = {}, {}
        for mod, entries in self._records.items():
            aliases = {}
            for source in entries:
                iid = int(source.get("id", -1))
                if iid <= 0:
                    continue
                item = dict(source, mod=mod)
                item.setdefault("full_name", mod + "/" + item["name"])
                self._by_id[iid] = item
                names = [item["name"], item["full_name"], item.get("display_name", ""),
                         *item.get("aliases", [])]
                for name in names:
                    if name:
                        key = self._normalize(name)
                        aliases.setdefault(key, set()).add(iid)
                        self._aliases.setdefault(key, set()).add(iid)
            # Keep the existing public indexes, excluding ambiguous aliases.
            self.mods[mod] = {n: next(iter(ids)) for n, ids in aliases.items() if len(ids) == 1}
            self.uses[mod] = {n: self._by_id[i].get("use", "misc") for n, i in self.mods[mod].items()}
            self.tags[mod] = {n: self._by_id[i].get("tags", []) for n, i in self.mods[mod].items()}

    def invalidate(self) -> None:
        with self._lock:
            self.live = False

    def load_cached(self) -> None:
        with self._lock:
            for path in self.dir.glob("*.json"):
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                    self._records[data.get("mod", path.stem)] = data.get("items", [])
                except (OSError, ValueError, TypeError):
                    continue
            self._rebuild()

    def sync_from_enum(self, mods: List[Dict[str, Any]]) -> Dict[str, List[str]]:
        result = {"added": [], "updated": [], "removed": []}
        if not mods:
            self.invalidate()
            return result
        with self._lock:
            incoming = {m["mod"]: m.get("items", []) for m in mods if m.get("mod")}
            if not any(incoming.values()):
                self.live = False
                return result
            for mod, items in incoming.items():
                if mod not in self._records:
                    result["added"].append(mod)
                elif self._records[mod] != items:
                    result["updated"].append(mod)
                self._write_file(mod, items)
            for mod in self._records.keys() - incoming.keys():
                result["removed"].append(mod)
                path = self._cache_path(mod)
                if path is not None:
                    try:
                        path.unlink(missing_ok=True)
                    except OSError:
                        pass
            self._records = incoming
            self._rebuild()
            self.live = True
        return result

    def _cache_path(self, mod):
        path = (self.dir / (mod + ".json")).resolve()
        return path if path.parent == self.dir.resolve() else None

    def _write_file(self, mod, items):
        path = self._cache_path(mod)
        if path is None:
            return
        try:
            path.write_text(json.dumps({"mod": mod, "items": items}, ensure_ascii=False,
                                       indent=2), encoding="utf-8")
        except OSError:
            pass

    def resolve(self, name: str) -> int:
        with self._lock:
            if not self.live:
                return -1
            key = self._normalize(name)
            if key.isdecimal():
                return int(key) if int(key) in self._by_id else -1
            ids = self._aliases.get(key, set())
            if len(ids) > 1:
                return -2  # Ambiguous: callers must not fall back to a guessed ID.
            return next(iter(ids), -1)

    def describe(self, name: str) -> Dict[str, Any]:
        with self._lock:
            iid = self.resolve(name)
            return dict(self._by_id.get(iid, {"id": iid, "use": "misc", "tags": []}))

    def use_of(self, name: str) -> str:
        return self.describe(name).get("use", "misc")

    def find_by_use(self, use: str) -> List[int]:
        with self._lock:
            return [i for i, item in self._by_id.items() if self.live and item.get("use") == use]

    def find_by_tag(self, tag: str) -> List[int]:
        with self._lock:
            return [i for i, item in self._by_id.items() if self.live and tag in item.get("tags", [])]

    def mod_list(self) -> List[Dict[str, Any]]:
        return [{"mod": mod, "count": len(items)} for mod, items in self._records.items()]
