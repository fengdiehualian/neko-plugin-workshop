"""Read only the current model's saved anchor from NEKO's loopback API."""

import json
import math
import threading
import time
from urllib.request import HTTPRedirectHandler, ProxyHandler, build_opener


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def get_json(path):
    opener = build_opener(ProxyHandler({}), NoRedirect())
    with opener.open("http://127.0.0.1:48911" + path, timeout=0.8) as response:
        raw = response.read(131073)
    if len(raw) > 131072:
        raise ValueError("NEKO layout response too large")
    return json.loads(raw)


def number(value, minimum, maximum):
    return type(value) in (int, float) and math.isfinite(value) and minimum <= value <= maximum


def current_layout(model, preferences):
    if not isinstance(model, dict) or model.get("success") is not True or not isinstance(preferences, list):
        return {"available": False}
    info = model.get("model_info")
    path = info.get("path") if isinstance(info, dict) else None
    if not isinstance(path, str) or not path:
        return {"available": False}
    for row in preferences:
        if not isinstance(row, dict) or row.get("model_path") != path:
            continue
        position, viewport, scale = (row.get(key) for key in ("position", "viewport", "scale"))
        if not all(isinstance(value, dict) for value in (position, viewport, scale)):
            continue
        if not all(number(position.get(key), -100000, 100000) for key in ("x", "y")):
            continue
        if not all(number(viewport.get(key), 100, 20000) for key in ("width", "height")):
            continue
        if not all(number(scale.get(key), 0.001, 10) for key in ("x", "y")):
            continue
        return {"available": True, "position": {key: position[key] for key in ("x", "y")},
                "viewport": {key: viewport[key] for key in ("width", "height")},
                "scale": {key: scale[key] for key in ("x", "y")}}
    return {"available": False}


class HostLayout:
    def __init__(self, request=get_json):
        self.request = request
        self.lock = threading.Lock()
        self.cached = {"available": False}
        self.read_at = 0

    def snapshot(self):
        with self.lock:
            if time.monotonic() - self.read_at < 1:
                return self.cached
            try:
                self.cached = current_layout(self.request("/api/characters/current_live2d_model"),
                                             self.request("/api/config/preferences"))
            except (OSError, ValueError, TypeError):
                self.cached = {"available": False}
            self.read_at = time.monotonic()
            return self.cached
