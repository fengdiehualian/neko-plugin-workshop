"""Read-only agent activity from N.E.K.O. tasks and optional CrossPet hooks."""

import hashlib
import json
import os
import threading
import time
import urllib.request
from pathlib import Path

AGENTS = {
    "gpt": "Codex",
    "claude": "Claude Code",
    "deepseek": "DeepSeek",
    "gemini": "Gemini",
    "glm": "GLM",
    "kimi": "Kimi",
    "qwen": "通义千问",
    "doubao": "豆包",
    "jev": "JEV",
    "current": "其他 AI",
}
ACTIVE_POSES = {"listening", "thinking", "reading", "writing", "running", "searching", "drawing", "delegating", "compact"}
INACTIVE_POSES = {"idle", "sleeping", "happy", "proud", "surprised", "oops", "asking"}
# CrossPet writes at lifecycle events, not continuously while a model or tool works.
# Stop/AfterAgent explicitly clears the active pose; this limit only handles lost hooks.
MAX_EVENT_AGE = 30 * 60
HOSTS = ("gpt", "claude", "deepseek", "gemini", "glm", "kimi", "workbuddy", "zcode", "hermes")
LOCAL_CACHE_SECONDS = 0.2
NEKO_REFRESH_SECONDS = 2


def model_label(value):
    """Allow bounded model metadata; never pass through arbitrary event content."""
    if not isinstance(value, str) or not 0 < len(value) <= 120:
        return None
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        return None
    return value.strip() or None


def classify(payload):
    if not isinstance(payload, dict) or payload.get("error") or not isinstance(payload.get("tasks"), list):
        return {"status": "unknown"}
    tasks = payload["tasks"]
    if any(
        not isinstance(t, dict)
        or t.get("status")
        not in ("queued", "running", "completed", "failed", "cancelled", "canceled", "timeout", "timed_out")
        for t in tasks
    ):
        return {"status": "unknown"}
    busy = any(t["status"] in ("running", "queued") for t in tasks)
    return {"status": "busy" if busy else "idle"}


def crosspet_activity(directory=None, now=None):
    """Use provider, pose, timestamp and bounded model metadata only."""
    root = Path(directory or os.environ.get("CROSSPET_STATE_DIR") or (
        Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData/Local"))) / "CrossPet/state"
        if os.name == "nt" else "/tmp/crosspet"
    ))
    now = time.time() if now is None else now
    active = []
    seen = False
    hints = []
    # Current CrossPet keeps the actual model in <host>-host.json, separate
    # from the character activity file. Older hooks simply omit this metadata.
    for host in HOSTS:
        try:
            path = root / f"{host}-host.json"
            if path.stat().st_size > 4096:
                continue
            hint = json.loads(path.read_text(encoding="utf-8"))
            stamp = hint.get("ts")
            model = model_label(hint.get("model"))
            if model and isinstance(stamp, (int, float)) and not isinstance(stamp, bool) and 0 <= now - stamp <= MAX_EVENT_AGE:
                hints.append({"host": host, "character": hint.get("character"), "model": model, "at": stamp})
        except (OSError, ValueError, TypeError, AttributeError):
            continue
    hints.sort(key=lambda hint: hint["at"], reverse=True)
    for key, label in AGENTS.items():
        path = root / f"{key}-state.json"
        try:
            if path.stat().st_size > 4096:
                continue
            item = json.loads(path.read_text(encoding="utf-8"))
            pose, stamp = item.get("pose"), item.get("ts")
            if not isinstance(stamp, (int, float)) or isinstance(stamp, bool) or not 0 <= now - stamp <= MAX_EVENT_AGE:
                continue
            if pose not in ACTIVE_POSES | INACTIVE_POSES:
                continue
            seen = True
            if pose in ACTIVE_POSES:
                entry = {"id": key, "name": label, "activity": pose, "at": stamp}
                model = model_label(item.get("model"))
                if model:
                    entry["model"] = model
                else:
                    hint = next((hint for hint in hints if hint["character"] == key and abs(hint["at"] - stamp) <= 10), None)
                    if hint:
                        entry.update(model=hint["model"], host=hint["host"])
                active.append(entry)
        except (OSError, ValueError, TypeError, AttributeError):
            continue
    active.sort(key=lambda item: item["at"], reverse=True)
    return {"active": active, "seen": seen}


class AgentStatus:
    def __init__(self, directory=None):
        self.lock = threading.Lock()
        self.directory = directory
        self.local_checked = float("-inf")
        self.local_cached = {"active": [], "seen": False}
        self.neko_checked = float("-inf")
        self.neko_cached = {"status": "unknown"}
        self.neko_refreshing = False
        self.task_states = {}
        self.completion_events = []

    def _read_neko(self):
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open("http://127.0.0.1:48911/api/agent/tasks", timeout=2) as response:
            payload = json.loads(response.read(1048576))
        result = classify(payload)
        if result["status"] != "unknown":
            result["tasks"] = [
                {"id": str(row.get("task_id") or row.get("id")), "status": row["status"]}
                for row in payload["tasks"]
                if isinstance(row.get("task_id") or row.get("id"), (str, int))
                and not isinstance(row.get("task_id") or row.get("id"), bool)
                and 0 < len(str(row.get("task_id") or row.get("id"))) <= 160
            ]
        return result

    def observe_tasks(self, result, now=None):
        """Only a known task entering completed is a completion; idle/poses are not."""
        if result.get("status") == "unknown" or "tasks" not in result:
            return
        now = time.time() if now is None else now
        rows = {row["id"]: row["status"] for row in result["tasks"]}
        for identifier, status in rows.items():
            if status == "completed" and self.task_states.get(identifier) in ("queued", "running"):
                self.completion_events.append({
                    "id": "neko-" + hashlib.sha256(identifier.encode()).hexdigest(),
                    "status": "completed", "source": "neko_tasks", "agent": "N.E.K.O Agent", "at": now,
                })
        self.task_states = rows
        self.completion_events = [event for event in self.completion_events if 0 <= now - event["at"] <= 120][-100:]

    def _refresh_neko(self):
        try:
            neko = self._read_neko()
        except Exception:
            neko = {"status": "unknown"}
        with self.lock:
            self.observe_tasks(neko)
            self.neko_cached = neko
            self.neko_checked = time.monotonic()
            self.neko_refreshing = False

    def snapshot(self):
        with self.lock:
            now = time.monotonic()
            if now - self.neko_checked >= NEKO_REFRESH_SECONDS and not self.neko_refreshing:
                self.neko_refreshing = True
                threading.Thread(target=self._refresh_neko, daemon=True, name="catfood-neko-status").start()
            # A slow/offline NEKO endpoint must not delay local hook updates.
            neko = self.neko_cached if now - self.neko_checked <= NEKO_REFRESH_SECONDS + 2 else {"status": "unknown"}
            if now - self.local_checked >= LOCAL_CACHE_SECONDS:
                self.local_cached = crosspet_activity(self.directory)
                self.local_checked = time.monotonic()
            local = self.local_cached
            active = [dict(agent) for agent in local["active"]]
            if neko["status"] == "busy":
                active.append({"id": "neko", "name": "N.E.K.O Agent", "activity": "running"})
            status = "busy" if active else "idle" if neko["status"] == "idle" or local["seen"] else "unknown"
            return {"status": status, "active_agents": active, "neko_status": neko["status"],
                    "completed_events": [dict(event) for event in self.completion_events if 0 <= time.time() - event["at"] <= 120],
                    "completion_source": "N.E.K.O 明确完成的任务；应用空闲与 CrossPet 姿态不算完成",
                    "source": "CrossPet 本机事件" if local["seen"] else "N.E.K.O 任务",
                    "poll_interval_ms": 500, "local_cache_ms": 200}
