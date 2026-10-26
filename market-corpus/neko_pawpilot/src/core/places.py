"""地点簿：自学习"地名 → 世界坐标"，用于途经播报与地点文化介绍。

为什么要自学习：ETS2 的遥测**不提供"当前所在城市"**——只有世界坐标 (x,z)，
而地图知识库里也没有地名（道路段只有 prefab 类型、设施点只有 kind）。
但有两个可靠地名来源：

1. 任务：`citySrc` / `cityDst`（接单与到货那一刻的坐标就是那两个城市的位置）
2. 过境：渡轮 / 火车的 `sourceName` / `targetName`（真实站点名）

于是把每次拿到的 (地名 → 当前坐标) 记进记忆，开得越多地点越全；
之后就能判断"正经过 X 附近"，进而让猫娘顺手介绍这个地方的风土。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

KIND = "places"


class PlaceBook:
    """地点坐标簿（持久化在宿主记忆里，跨会话累积）。"""

    def __init__(self, memory: Any) -> None:
        self.memory = memory
        self._passed: Dict[str, float] = {}   # 本次会话已播报过的地点（内存态）

    # ── 学习 ────────────────────────────────────────────────
    def learn(self, name: str, x: float, z: float, kind: str = "city") -> bool:
        """记录/更新一个地点的坐标。同名地点按最近一次命中更新（精度受益）。"""
        name = (name or "").strip()
        if not name or (x == 0 and z == 0):
            return False
        entry = self.memory.query(KIND, name) or {}
        visits = int(entry.get("visits", 0)) + 1
        self.memory.remember(KIND, name, {
            "x": float(x), "z": float(z), "kind": kind, "visits": visits,
        }, importance=0.8)
        return True

    # ── 查询 ────────────────────────────────────────────────
    def known(self) -> List[Dict[str, Any]]:
        store = self.memory.query(KIND) or {}
        out = []
        for name, v in store.items():
            if not isinstance(v, dict):
                continue
            out.append({"name": name, **v})
        return out

    def count(self) -> int:
        return len(self.memory.query(KIND) or {})

    def query_visits(self, name: str) -> int:
        """某地点到访次数（用于"我们以前来过这里"）。"""
        entry = self.memory.query(KIND, (name or "").strip())
        if not isinstance(entry, dict):
            return 0
        return int(entry.get("visits", 0) or 0)

    def nearest(self, x: float, z: float, max_km: float = 6.0) -> Optional[Dict[str, Any]]:
        """最近的地名（返回 name/distance_km/visits）。"""
        best = None
        best_d = max_km * 1000.0
        for item in self.known():
            dx = float(item.get("x", 0.0)) - x
            dz = float(item.get("z", 0.0)) - z
            d = (dx * dx + dz * dz) ** 0.5
            if d < best_d:
                best_d = d
                best = {**item, "distance_km": round(d / 1000.0, 2)}
        return best

    def passing(self, x: float, z: float, now: float,
                radius_km: float = 6.0, cooldown_s: float = 1800.0) -> Optional[Dict[str, Any]]:
        """是否"正经过某地"（进入半径且本轮会话未播报过 / 超过冷却）。

        返回地点 dict（含 distance_km），否则 None。
        """
        near = self.nearest(x, z, max_km=radius_km)
        if not near:
            return None
        name = near.get("name") or ""
        if not name:
            return None
        last = self._passed.get(name)
        if last is not None and now - last < cooldown_s:
            return None
        self._passed[name] = now
        return near
