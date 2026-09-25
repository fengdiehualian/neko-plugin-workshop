"""地图知识库：世界坐标定位 + 设施查询（M3 骨架）。

数据源分两档：
- 静态降级（开箱即用）：限速推断道路等级、城市距离表
- M3 增强（TruckLib 导出后加载）：.mbd 解析出的道路/红绿灯/加油站/服务区坐标
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

# 道路等级判定（按限速 km/h）
ROAD_LEVEL_BY_LIMIT = [
    (80, "高速", "motorway"),
    (60, "国道", "national"),
    (30, "市区", "urban"),
    (0, "乡道", "local"),
]


class MapKnowledge:
    """地图知识库：定位 + 设施查询。"""

    def __init__(self) -> None:
        self._facilities: List[Dict[str, Any]] = []  # M3: TruckLib 导出的设施
        self._roads: List[Dict[str, Any]] = []       # M3: 道路段
        self._loaded = False
        self._version = ""
        self._load_static()

    def _load_static(self) -> None:
        p = Path(__file__).resolve().parent.parent / "data" / "map" / "map_kb.json"
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            self._facilities = data.get("facilities", [])
            self._roads = data.get("roads", [])
            self._version = data.get("version", "")
            self._loaded = bool(self._facilities or self._roads)
        except (OSError, json.JSONDecodeError):
            pass

    def load_external(self, path: Path) -> bool:
        """加载 TruckLib 导出的地图知识库 JSON。"""
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            self._facilities = data.get("facilities", [])
            self._roads = data.get("roads", [])
            self._version = data.get("version", "")
            self._loaded = True
            return True
        except (OSError, json.JSONDecodeError):
            return False

    def reload(self) -> bool:
        """重新加载默认地图知识库（解析完成后调用）。"""
        self._load_static()
        return self._loaded

    def road_level(self, speed_limit_kmh: float) -> str:
        """按限速推断道路等级（静态降级）；无效限速返回空串。"""
        if speed_limit_kmh <= 0:
            return ""
        for threshold, label, _code in ROAD_LEVEL_BY_LIMIT:
            if speed_limit_kmh >= threshold:
                return label
        return "乡道"

    def nearest_facility(self, x: float, z: float,
                         kind: str = "service", max_km: float = 100.0) -> Optional[Dict[str, Any]]:
        """找最近的设施（M3 有坐标数据时）。"""
        best = None
        best_dist = max_km
        for f in self._facilities:
            if f.get("kind") != kind:
                continue
            dx = f.get("x", 0) - x
            dz = f.get("z", 0) - z
            dist = (dx * dx + dz * dz) ** 0.5 / 1000.0  # 游戏单位→km 近似
            if dist < best_dist:
                best_dist = dist
                best = {**f, "distance_km": round(dist, 1)}
        return best

    def snapshot(self) -> Dict[str, Any]:
        return {
            "loaded": self._loaded,
            "version": self._version,
            "facilities": len(self._facilities),
            "roads": len(self._roads),
        }
