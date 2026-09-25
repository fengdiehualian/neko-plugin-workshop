import json
import time


class PlaymateDebugLogger:
    def __init__(self, plugin):
        self._plugin = plugin

    def record(self, kind, **data):
        if not getattr(self._plugin, "_playmate_debug_log_enabled", False):
            return
        try:
            log_dir = self._plugin.config_dir / "log"
            log_dir.mkdir(parents=True, exist_ok=True)
            path = log_dir / "playmate_debug.log"
            self._trim_if_needed(path)
            payload = {
                "time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                "kind": kind,
                **data,
            }
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
        except Exception as e:
            self._plugin.logger.warning(f"[PlaymateDebug] Failed to write log: {e}")

    def _trim_if_needed(self, path):
        max_bytes = int(getattr(self._plugin, "_playmate_debug_log_max_bytes", 0) or 0)
        if max_bytes <= 0 or not path.exists() or path.stat().st_size <= max_bytes:
            return
        keep_bytes = max(1024, max_bytes // 2)
        with open(path, "rb") as f:
            f.seek(max(0, path.stat().st_size - keep_bytes))
            data = f.read()
        first_newline = data.find(b"\n")
        if first_newline >= 0:
            data = data[first_newline + 1:]
        with open(path, "wb") as f:
            f.write(data)
