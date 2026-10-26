"""地图知识库：世界坐标定位 + 设施查询（M3 骨架）。

数据源分两档：
- 静态降级（开箱即用）：限速推断道路等级、城市距离表
- M3 增强（TruckLib 导出后加载）：.mbd 解析出的道路/红绿灯/加油站/服务区坐标
"""

from __future__ import annotations

import json
import math
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
    """地图知识库：定位 + 设施查询 + 道路匹配。"""

    # 道路网格索引的格边长（游戏单位≈米）。74k 段全量扫描太慢，
    # 按格子分桶后只查邻近 9 格（实测 11 米精度、单次查询 <1ms）。
    _GRID = 2000.0

    def __init__(self) -> None:
        self._facilities: List[Dict[str, Any]] = []  # M3: TruckLib 导出的设施
        self._roads: List[Dict[str, Any]] = []       # M3: 道路段
        self._loaded = False
        self._version = ""
        self._grid: Dict[tuple, List[Dict[str, Any]]] = {}
        self._load_static()

    def _load_static(self) -> None:
        p = Path(__file__).resolve().parent.parent / "data" / "map" / "map_kb.json"
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            self._facilities = data.get("facilities", [])
            self._roads = data.get("roads", [])
            self._version = data.get("version", "")
            self._loaded = bool(self._facilities or self._roads)
            self._build_index()
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
            self._build_index()
            return True
        except (OSError, json.JSONDecodeError):
            return False

    def _build_index(self) -> None:
        """把道路段按两端点所在格分桶（段可能跨格，两格都放）。"""
        grid: Dict[tuple, List[Dict[str, Any]]] = {}
        g = self._GRID
        for rd in self._roads:
            for px, pz in ((rd.get("x", 0.0), rd.get("z", 0.0)),
                           (rd.get("x2", 0.0), rd.get("z2", 0.0))):
                key = (int(px // g), int(pz // g))
                bucket = grid.get(key)
                if bucket is None:
                    grid[key] = [rd]
                else:
                    bucket.append(rd)
        self._grid = grid

    def _near_roads(self, x: float, z: float, radius_km: float = 1.0) -> List[Dict[str, Any]]:
        """取邻近网格里的道路段（去重）。"""
        g = self._GRID
        span = max(1, int((radius_km * 1000.0) // g))
        cx, cz = int(x // g), int(z // g)
        seen: set = set()
        out: List[Dict[str, Any]] = []
        for ix in range(cx - span, cx + span + 1):
            for iz in range(cz - span, cz + span + 1):
                for rd in self._grid.get((ix, iz), ()):
                    rid = id(rd)
                    if rid in seen:
                        continue
                    seen.add(rid)
                    out.append(rd)
        return out

    def nearest_road(self, x: float, z: float,
                     max_km: float = 1.0) -> Optional[Dict[str, Any]]:
        """最近的映射道路段（点到线段距离，含端点到线段投影）。

        实测：行驶中距离约 0.01 km（11 米）——坐标与遥测同一坐标系。
        """
        if not self._roads:
            return None
        best = None
        best_d = max_km * 1000.0
        for rd in self._near_roads(x, z, max_km):
            ax, az = rd.get("x", 0.0), rd.get("z", 0.0)
            bx, bz = rd.get("x2", 0.0), rd.get("z2", 0.0)
            dx, dz = bx - ax, bz - az
            seg2 = dx * dx + dz * dz
            if seg2 <= 1e-6:
                px, pz = ax, az
            else:
                t = ((x - ax) * dx + (z - az) * dz) / seg2
                t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
                px, pz = ax + t * dx, az + t * dz
            d = ((x - px) ** 2 + (z - pz) ** 2) ** 0.5
            if d < best_d:
                best_d = d
                best = {"road_type": rd.get("road_type", ""),
                        "distance_km": round(d / 1000.0, 3),
                        "x": px, "z": pz}
        return best

    def road_points_near(self, x: float, z: float,
                         radius_km: float = 0.08) -> List[tuple]:
        """半径内的道路段端点坐标 [(x, z), ...]——前视点跟踪（pure pursuit）用。

        道路段是无序点云（没有路口拓扑），所以转向控制不做"沿段追踪"，
        而是取附近路点、在车头坐标系里挑一个合适的前视目标点。
        """
        if not self._roads:
            return []
        r2 = (radius_km * 1000.0) ** 2
        out: List[tuple] = []
        for rd in self._near_roads(x, z, radius_km):
            for px, pz in ((rd.get("x", 0.0), rd.get("z", 0.0)),
                           (rd.get("x2", 0.0), rd.get("z2", 0.0))):
                if (px - x) ** 2 + (pz - z) ** 2 <= r2:
                    out.append((px, pz))
        return out

    def road_density(self, x: float, z: float, radius_km: float = 1.0) -> int:
        """半径内道路段数量——路网密度（城区远高于野外，可做市区判据）。"""
        if not self._roads:
            return 0
        r2 = (radius_km * 1000.0) ** 2
        n = 0
        for rd in self._near_roads(x, z, radius_km):
            for px, pz in ((rd.get("x", 0.0), rd.get("z", 0.0)),
                           (rd.get("x2", 0.0), rd.get("z2", 0.0))):
                if (px - x) ** 2 + (pz - z) ** 2 <= r2:
                    n += 1
                    break
        return n

    def curve_ahead(self, x: float, z: float,
                    lookahead_km: float = 0.35, min_turn_deg: float = 30.0) -> Optional[float]:
        """前方弯道强度：返回前方路段方向相对"当前路段方向"的最大偏转角（弧度）。

        检测到明显拐弯就报"前方有弯道，注意减速"。
        刻意不判断左/右——遥测的车头朝向约定未经实测标定（rotationY 的
        轴向/正负未验证），报错方向比不报更糟；只给"有弯道 + 大致距离"。
        返回 None 表示前方够直（或数据不足）。
        """
        if not self._roads:
            return None
        near = self._near_roads(x, z, max(0.2, lookahead_km))
        if not near:
            return None

        def _dir(rd: Dict[str, Any]) -> Optional[float]:
            dx = rd.get("x2", 0.0) - rd.get("x", 0.0)
            dz = rd.get("z2", 0.0) - rd.get("z", 0.0)
            if abs(dx) < 1e-6 and abs(dz) < 1e-6:
                return None
            return math.atan2(dz, dx)

        # 当前所在段 = 距车最近的那一段
        base_rd: Optional[Dict[str, Any]] = None
        base_d = 1e18
        for rd in near:
            mx = (rd.get("x", 0.0) + rd.get("x2", 0.0)) / 2.0
            mz = (rd.get("z", 0.0) + rd.get("z2", 0.0)) / 2.0
            d = (mx - x) ** 2 + (mz - z) ** 2
            if d < base_d:
                base_d, base_rd = d, rd
        if base_rd is None:
            return None
        base = _dir(base_rd)
        if base is None:
            return None

        look2 = (lookahead_km * 1000.0) ** 2
        best = 0.0
        for rd in near:
            mx = (rd.get("x", 0.0) + rd.get("x2", 0.0)) / 2.0
            mz = (rd.get("z", 0.0) + rd.get("z2", 0.0)) / 2.0
            if (mx - x) ** 2 + (mz - z) ** 2 > look2 or (mx - x) ** 2 + (mz - z) ** 2 < 1.0:
                continue
            d2 = _dir(rd)
            if d2 is None:
                continue
            diff = abs((d2 - base + math.pi) % (2 * math.pi) - math.pi)
            if diff > best:
                best = diff
        if best < math.radians(min_turn_deg):
            return None
        return best

    def reload(self) -> bool:
        """重新加载默认地图知识库。"""
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
