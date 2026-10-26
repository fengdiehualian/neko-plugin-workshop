# 和猫娘一起开飞机（简单飞机2副驾驶）插件 —— 本地模拟桥接服务
# Copyright (C) 2026 星河拓航工作室 (Galaxy Exploration Studio)
#
# 本文件是 sp2_copilot 插件的一部分，以 GNU General Public License v3.0 许可发布。
# 完整条款见仓库根目录的 LICENSE 文件。

"""模拟 SP2-Bridge 服务 —— 不开游戏也能试插件与面板。

用法::

    python tools/mock_bridge.py [--port 21580] [--scenario cruise|approach|stall|ground]

它按 ``docs/bridge-protocol.md`` 提供 ``/ping`` ``/state`` ``/aircraft``
``/navigation`` ``/environment`` 五个接口，数值会随时间缓慢变化，
方便观察面板刷新、告警触发和检查单切换。

**这个脚本只是开发辅助**：它不读取游戏，也不发送任何指令。
"""

from __future__ import annotations

import argparse
import json
import math
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict
from urllib.parse import urlparse

PROTOCOL = 1
_START = time.time()


def _elapsed() -> float:
    return time.time() - _START


def _scenario_state(scenario: str, t: float) -> Dict[str, Any]:
    """按场景生成一份遥测。数值都是编的，只为让面板动起来。"""
    if scenario == "ground":
        airspeed = 8.0 + 2.0 * math.sin(t / 6.0)
        on_ground, vs, agl = True, 0.0, 2.0
        throttle, flaps, gear_down = 0.15, 0.0, True
        heading = 90.0 + 4.0 * math.sin(t / 12.0)
    elif scenario == "approach":
        airspeed = 62.0 + 2.5 * math.sin(t / 7.0)
        on_ground, vs, agl = False, -4.2, max(40.0, 400.0 - t * 8.0)
        throttle, flaps, gear_down = 0.35, 1.0, True
        heading = 268.0 + 3.0 * math.sin(t / 9.0)
    elif scenario == "stall":
        airspeed = 38.0 + 1.5 * math.sin(t / 3.0)
        on_ground, vs, agl = False, -9.5, max(60.0, 900.0 - t * 6.0)
        throttle, flaps, gear_down = 0.2, 0.0, False
        heading = 12.0 + 10.0 * math.sin(t / 5.0)
    else:  # cruise
        airspeed = 145.0 + 4.0 * math.sin(t / 9.0)
        on_ground, vs, agl = False, 1.8 + 1.2 * math.sin(t / 5.0), 2400.0 + 30.0 * math.sin(t / 11.0)
        throttle, flaps, gear_down = 0.78, 0.0, False
        heading = 271.0 + 2.0 * math.sin(t / 14.0)

    pitch = 2.5 + 1.8 * math.sin(t / 8.0)
    roll = 6.0 * math.sin(t / 4.0)
    aoa = 3.0 + (9.0 if scenario == "stall" else 1.5 * math.sin(t / 7.0))

    return {
        "ok": True,
        "protocol": PROTOCOL,
        "scene": "flight",
        "in_world": True,
        "paused": False,
        "mission_time": round(t, 2),
        "aircraft": {"name": "Mock Jet", "type": "plane"},
        "flight": {
            "airspeed": round(airspeed, 2),
            "true_airspeed": round(airspeed * 1.06, 2),
            "ground_speed": round(airspeed + 2.0, 2),
            "vertical_speed": round(vs, 2),
            "altitude_msl": round(agl + 230.0, 2),
            "altitude_agl": round(agl, 2),
            "terrain_height": 230.2,
            "heading": round(heading % 360.0, 2),
            "pitch": round(pitch, 2),
            "roll": round(roll, 2),
            "aoa": round(aoa, 2),
            "sideslip": round(0.4 * math.sin(t / 6.0), 2),
            "g_force": round(1.0 + 0.15 * math.sin(t / 3.0), 3),
            "mach": round(airspeed / 340.0, 3),
            "stalled": scenario == "stall",
            "stall_warning": scenario == "stall",
            "on_ground": on_ground,
            "crashed": False,
        },
        "controls": {
            "throttle": throttle,
            "pitch_input": round(0.08 * math.sin(t / 4.0), 3),
            "roll_input": round(0.05 * math.sin(t / 5.0), 3),
            "yaw_input": 0.0,
            "brake": 0.0,
            "trim": 0.0,
            "flaps": flaps,
            "airbrake": 0.0,
            "spoiler": 0.0,
            "gear_down": gear_down,
            "autopilot": False,
            "autopilot_mode": "",
        },
    }


def _aircraft(scenario: str, t: float) -> Dict[str, Any]:
    fuel_pct = max(4.0, 62.0 - t / 30.0)
    engines = [
        {
            "index": 0,
            "name": "J-15 Left",
            "type": "jet",
            "running": True,
            "throttle": 0.78,
            "rpm": 11800,
            "power_percent": 88,
            "egt_c": 615,
            "damaged": False,
            "on_fire": False,
        },
        {
            "index": 1,
            "name": "J-15 Right",
            "type": "jet",
            "running": True,
            "throttle": 0.78,
            "rpm": 11900,
            "power_percent": 89,
            "egt_c": 622,
            "damaged": False,
            "on_fire": False,
        },
    ]
    # stall 场景里故意停掉一台，方便看「发动机停车」告警
    if scenario == "stall":
        engines[1]["running"] = False
        engines[1]["rpm"] = 0
        engines[1]["power_percent"] = 0

    return {
        "ok": True,
        "protocol": PROTOCOL,
        "name": "Mock Jet",
        "type": "plane",
        "part_count": 86,
        "mass_kg": 12450.0,
        "fuel": {"current_kg": round(2400.0 * fuel_pct / 100.0, 1), "capacity_kg": 2400.0, "percent": round(fuel_pct, 1)},
        "engines": engines,
        "gear": {"state": "down" if scenario in {"ground", "approach"} else "up", "down": scenario in {"ground", "approach"}},
        "surfaces": {"flaps_percent": 100.0 if scenario == "approach" else 0.0, "airbrake_percent": 0.0, "spoiler_percent": 0.0},
        "damage": {"integrity_percent": 96.0, "broken_parts": 0, "on_fire": False},
        "limits": {
            "vne_mps": 220.0,
            "vc_mps": 180.0,
            "stall_speed_mps": 42.0,
            "rotation_speed_mps": 55.0,
            "approach_speed_mps": 62.0,
            "max_g": 9.0,
            "min_g": -4.0,
            "max_mach": 0.95,
            "flap_limit_mps": 130.0,
            "gear_limit_mps": 120.0,
        },
    }


def _navigation(t: float) -> Dict[str, Any]:
    return {
        "ok": True,
        "protocol": PROTOCOL,
        "position": {"x": round(1200.0 + t * 60.0, 1), "y": 2400.0, "z": round(-4200.0 + t * 40.0, 1)},
        "heading": round((271.0 + 2.0 * math.sin(t / 14.0)) % 360.0, 2),
        "track": round((268.0 + 2.0 * math.sin(t / 14.0)) % 360.0, 2),
        "target": {
            "name": "Vetusta City Airport",
            "x": 9000.0,
            "y": 120.0,
            "z": -1200.0,
            "distance": round(max(300.0, 12400.0 - t * 40.0), 1),
            "bearing": 92.0,
            "elevation": 120.0,
        },
        "nearest_airport": {"name": "Hillside Strip", "distance": 3200.0, "bearing": 15.0},
        "wind": {"direction_deg": 210.0, "speed_mps": 6.4},
    }


def _environment() -> Dict[str, Any]:
    return {
        "ok": True,
        "protocol": PROTOCOL,
        "time_of_day": "14:32",
        "weather": "clear",
        "temperature_c": 15.2,
        "pressure_hpa": 1013.2,
        "wind": {"direction_deg": 210.0, "speed_mps": 6.4, "gust_mps": 8.1},
        "visibility_m": 10000.0,
        "sea_level_m": 0.0,
    }


class _Handler(BaseHTTPRequestHandler):
    scenario = "cruise"

    def _send(self, payload: Dict[str, Any], status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - http.server 的约定方法名
        path = urlparse(self.path).path.rstrip("/") or "/"
        t = _elapsed()
        if path == "/ping":
            self._send({
                "ok": True,
                "protocol": PROTOCOL,
                "service": "SP2-Bridge",
                "game": "SimplePlanes 2",
                "game_version": "mock",
                "in_world": True,
            })
        elif path == "/state":
            self._send(_scenario_state(self.scenario, t))
        elif path == "/aircraft":
            self._send(_aircraft(self.scenario, t))
        elif path == "/navigation":
            self._send(_navigation(t))
        elif path == "/environment":
            self._send(_environment())
        else:
            self._send({"ok": False, "error": f"未知路径 {path}"}, status=404)

    def log_message(self, fmt: str, *args: Any) -> None:
        # 默认会把每个请求打到 stderr，轮询时太吵，这里直接吞掉。
        return


def main() -> None:
    parser = argparse.ArgumentParser(description="SP2-Bridge 模拟服务")
    parser.add_argument("--port", type=int, default=21580)
    parser.add_argument(
        "--scenario",
        default="cruise",
        choices=["cruise", "approach", "stall", "ground"],
        help="模拟的飞行场景：cruise 巡航 / approach 进近 / stall 失速 / ground 地面",
    )
    args = parser.parse_args()

    _Handler.scenario = args.scenario
    server = ThreadingHTTPServer(("127.0.0.1", args.port), _Handler)
    print(f"SP2-Bridge 模拟服务已启动： http://127.0.0.1:{args.port}  场景={args.scenario}")
    print("按 Ctrl+C 结束。")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
