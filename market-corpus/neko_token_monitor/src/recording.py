"""Durable interaction history and a retryable, asynchronous memory outbox."""

import copy
import json
import os
import threading
import uuid
from datetime import datetime
from pathlib import Path
from urllib.request import ProxyHandler, Request, build_opener

from .diary import build_diary, parsed


def now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


GAME_KINDS = {
    "start": "猫粮不足，YUI 变成小白猫开始追逐大肥鱼", "catch": "小白猫追到大肥鱼并轻咬",
    "reverse": "大肥鱼累计被追到十次，戴上虎头帽反追 Q 版 YUI", "counter_tag": "反追中的 YUI 被大肥鱼逮住",
    "flee": "YUI 正在害怕地逃跑并呼救", "rescue": "用户把 YUI 抱回原站位，精神恢复，本轮怒气清除",
    "pause": "暂停追逐", "resume": "恢复追逐", "stop": "追逐结束或监测界面关闭",
}


def quota_text(quota):
    if not quota:
        return "猫粮余额未知，未编造余额"
    amount = quota.get("remaining")
    unit = quota.get("unit", "")
    text = f"{quota.get('model') or quota.get('source') or '当前来源'}猫粮剩余 {amount} {unit}" if amount is not None else "猫粮余额未知"
    if quota.get("percent") is not None:
        text += f"（剩余 {quota['percent']:.1f}%）"
    text += f"；依据：{quota.get('basis', '未知')}"
    if quota.get("stale"):
        text += "；上次记录已过期，不能当成实时余额"
    return text


class Recording:
    def __init__(self, directory, importer=None):
        self.path = Path(directory) / "recording.json"
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock = threading.RLock()
        self.importer = importer or self.import_memory
        self.wake = threading.Event()
        self.stop = threading.Event()
        self.worker = None
        self.end_watcher = None
        self.load_error = None
        self.character_name = "YUI"
        raw = None
        for path in (self.path, self.path.with_suffix(".bak")):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(raw, dict):
                    raise ValueError()
                break
            except (OSError, ValueError):
                raw = None
        if raw is None and self.path.exists():
            # Never overwrite the only copy of a damaged record.
            self.load_error = "互动记录文件无法读取，已保留原文件；请先修复或备份后重试喵～"
        if raw and raw.get("version") == 2:
            self.data = raw
        else:
            sessions = [raw] if raw and raw.get("id") else []
            self.data = {"version": 2, "sessions": sessions}
        self.data.setdefault("sessions", [])
        self.data.setdefault("observations", [])

    def save(self):
        if self.load_error:
            raise ValueError(self.load_error)
        raw = json.dumps(self.data, ensure_ascii=False)
        for path in (self.path, self.path.with_suffix(".bak")):
            temporary = path.with_suffix(path.suffix + ".tmp")
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(path)

    def snapshot(self):
        self.reconcile_ends()
        with self.lock:
            sessions = self.data["sessions"]
            current = sessions[-1] if sessions else {"active": False}
            def clean(row):
                return {k: v for k, v in row.items() if k not in ("events", "interaction_events")}
            return dict(
                clean(current),
                initialized=bool(sessions),
                history=[clean(row) for row in reversed(sessions[-100:])],
                totals={
                    "pet": sum(row.get("pet", 0) for row in sessions),
                    "ask": sum(row.get("ask", 0) for row in sessions),
                    "sessions": len(sessions),
                },
                game_totals={
                    "catches": sum(row.get("game", {}).get("catch", 0) for row in sessions),
                    "rescues": sum(row.get("game", {}).get("rescue", 0) for row in sessions),
                },
                pending_memory=sum(bool((row.get("summary") or row.get("game_events")) and not row.get("memory_saved")) for row in sessions + self.data["observations"]),
                error=self.load_error,
            )

    def action(self, action, event_id=None, session_id=None, kind=None, message=None, quota=None, at=None):
        self.reconcile_ends()
        if action not in ("start", "pet", "ask", "note", "game", "end", "status", "retry"):
            raise ValueError("Unknown recording action")
        if event_id is not None and (not isinstance(event_id, str) or not 1 <= len(event_id) <= 100):
            raise ValueError("Invalid event id")
        if session_id is not None and (not isinstance(session_id, str) or not 1 <= len(session_id) <= 100):
            raise ValueError("Invalid session id")
        if action == "note" and (
            kind not in ("agent", "connection") or not isinstance(message, str) or not 1 <= len(message) <= 240
        ):
            raise ValueError("Invalid status note")
        if action == "game" and (kind not in GAME_KINDS or not event_id):
            raise ValueError("Invalid game event")
        with self.lock:
            accepted = False
            if self.load_error:
                return self.snapshot()
            sessions = self.data["sessions"]
            current = sessions[-1] if sessions else None
            if action == "start" and not (current and current.get("active")):
                current = dict(active=True, id=str(uuid.uuid4()), start=now(), pet=0, ask=0, events=[], character_name=self.character_name, diary_version=1)
                sessions.append(current)
            elif action in ("pet", "ask"):
                # Replayed clicks are attributed to their original session, even after a crash/exit.
                target = next((row for row in sessions if row["id"] == session_id), None) if session_id else current
                if target and (target.get("active") or session_id):
                    seen = target.setdefault("events", [])
                    if not event_id or event_id not in seen:
                        if at is not None:
                            point, received, start, end = parsed(at), parsed(now()), parsed(target.get("start")), parsed(target.get("end"))
                            if (point is None or received is None or point.timestamp() > received.timestamp() + 300
                                    or start is not None and point.timestamp() < start.timestamp() - 300
                                    or end is not None and point.timestamp() > end.timestamp() + 300):
                                raise ValueError("Invalid click timestamp")
                        target[action] = target.get(action, 0) + 1
                        if at is not None:
                            target.setdefault("interaction_events", []).append({"kind": action, "at": at})
                        if event_id:
                            seen.append(event_id)
                        if target.get("summary"):
                            target["summary"] = self.summary(target)
                            target["memory_saved"] = False
            elif action == "game":
                target = next((row for row in sessions if row["id"] == session_id), None) if session_id else current
                if not target or not (target.get("active") or session_id):
                    return self.snapshot()
                seen = target.setdefault("events", [])
                if event_id not in seen:
                    accepted = True
                    stats = target.setdefault("game", {})
                    stats[kind] = stats.get(kind, 0) + 1
                    seen.append(event_id)
                    events = target.setdefault("game_events", [])
                    events.append({"at": now(), "kind": kind, "quota": copy.deepcopy(quota)})
                    target["memory_saved"] = False
                    target["memory_revision"] = target.get("memory_revision", 0) + 1
            elif action == "note":
                target = next((row for row in sessions if row["id"] == session_id), None) if session_id else current
                if not target or not (target.get("active") or session_id):
                    return self.snapshot()
                seen = target.setdefault("events", [])
                if not event_id or event_id not in seen:
                    notes = target.setdefault("status_events", [])
                    if not notes or notes[-1]["kind"] != kind or notes[-1]["message"] != message:
                        notes.append({"at": now(), "kind": kind, "message": message})
                        del notes[:-200]
                    if event_id:
                        seen.append(event_id)
            elif action == "end":
                target = next((row for row in sessions if row["id"] == session_id), None) if session_id else current
                if target and target.get("active"):
                    self.end_session(target)
            if action != "status":
                self.save()
            if action in ("end", "retry") or action == "game" and kind in ("reverse", "rescue", "stop"):
                self.wake.set()
            result = self.snapshot()
            if action == "game":
                result["event_accepted"] = accepted
            if action == "end":
                # An end replay from an old/replaced store is already resolved;
                # acknowledge it without touching the current session.
                result["ended_session_id"] = target["id"] if target else session_id
            return result

    def end_session(self, row):
        row.update(active=False, end=now(), memory_saved=False)
        row["summary"] = self.summary(row)
        row["memory_revision"] = row.get("memory_revision", 0) + 1

    def mark_presence(self, session_id):
        with self.lock:
            current = self.data["sessions"][-1] if self.data["sessions"] else {}
            if self.load_error or not current.get("active") or current.get("id") != session_id:
                return False
            if not current.get("presence_start"):
                current["presence_start"] = now()
                self.save()
            return True

    def diary(self):
        self.reconcile_ends()
        with self.lock:
            result = build_diary(self.data["sessions"], self.data["observations"])
            result["technical"] = [
                {key: copy.deepcopy(row.get(key)) for key in ("id", "start", "end", "active", "memory_saved", "memory_error", "status_events")}
                for row in reversed(self.data["sessions"][-30:])
            ]
            result["deliveries"] = [{"at": row["start"], "text": row["summary"]} for row in self.data["observations"][-60:] if row.get("kind") == "yui_dialogue"]
            return result

    def reconcile_ends(self):
        """Native exit markers survive a dead WebKit page or unavailable HTTP transport."""
        with self.lock:
            if self.load_error:
                return
            for path in self.path.parent.glob("closed-*.json"):
                try:
                    if path.is_symlink() or path.stat().st_size > 512:
                        continue
                    identifier = path.name.removeprefix("closed-").removesuffix(".json")
                    if str(uuid.UUID(identifier)) != identifier:
                        continue
                    marker = json.loads(path.read_text(encoding="utf-8"))
                    if not isinstance(marker, dict) or marker.get("session_id") != identifier:
                        continue
                    target = next((row for row in self.data["sessions"] if row["id"] == identifier), None)
                    if target:
                        if target.get("active"):
                            self.end_session(target)
                        self.save()
                        self.wake.set()
                    path.unlink(missing_ok=True)
                except (OSError, ValueError, TypeError):
                    # Retain uncommitted markers so a transient disk error cannot reopen a session.
                    continue

    def remember(self, kind, text, quota=None, event_id=None):
        """Durable background observations, including when no widget session is active."""
        with self.lock:
            if self.load_error:
                return False
            rows = self.data["observations"]
            if event_id and any(row["id"] == event_id for row in rows):
                return False
            rows.append({"id": event_id or str(uuid.uuid4()), "start": now(), "end": now(), "pet": 0, "ask": 0,
                         "kind": kind, "summary": text, "quota": copy.deepcopy(quota), "memory_saved": False, "character_name": self.character_name})
            self.save()
            self.wake.set()
            return True

    @staticmethod
    def summary(row):
        return f"在此期间共摸头 {row.get('pet', 0)} 次，询问 {row.get('ask', 0)} 次喵～"

    def flush_pending(self):
        with self.lock:
            pending = [
                copy.deepcopy(row)
                for row in self.data["sessions"] + self.data["observations"]
                if (row.get("summary") or row.get("game_events")) and not row.get("memory_saved")
            ]
        for row in pending:
            if self.stop.is_set():
                break
            try:
                saved = bool(self.importer(row))
            except Exception:
                saved = False
            with self.lock:
                target = next(item for item in self.data["sessions"] + self.data["observations"] if item["id"] == row["id"])
                if target.get("summary") == row.get("summary") and target.get("memory_revision", 0) == row.get("memory_revision", 0):
                    target["memory_saved"] = saved
                    target["memory_error"] = None if saved else "本机记录已保存，YUI 记忆待重试"
                    self.save()

    def start(self):
        if self.worker and self.worker.is_alive():
            return
        if self.load_error:
            return

        def run():
            while not self.stop.is_set():
                self.wake.clear()
                try:
                    self.flush_pending()
                except Exception:
                    pass
                self.wake.wait(60)

        self.worker = threading.Thread(target=run, daemon=True)
        self.worker.start()
        def watch_ends():
            while not self.stop.wait(0.5):
                self.reconcile_ends()
        self.end_watcher = threading.Thread(target=watch_ends, daemon=True, name="catfood-session-ends")
        self.end_watcher.start()

    def close(self):
        self.stop.set()
        self.wake.set()
        if self.worker:
            self.worker.join(1)
        if self.end_watcher:
            self.end_watcher.join(1)

    @staticmethod
    def memory_text(row):
        if row.get("kind"):
            return f"猫粮事件 {row['id']}（{row['start']}）：{row['summary']}。{quota_text(row.get('quota'))}。"
        text = f"猫粮互动记录 {row['id']}：用户与 YUI 在 {row['start']} 至 {row.get('end') or '本次记录时仍在进行'}期间，点击人物摸头 {row.get('pet', 0)} 次，点击气泡询问余额或空闲状态 {row.get('ask', 0)} 次。"
        game = row.get("game", {})
        if game:
            text += f"本段追到肥鱼 {game.get('catch', 0)} 次、攻防转换 {game.get('reverse', 0)} 次、YUI 被反追逮住 {game.get('counter_tag', 0)} 次、用户抱回 {game.get('rescue', 0)} 次。这些是猫粮小游戏中的互动，不是真实伤害。\n"
        text += "\n".join(f"{event['at']}：{GAME_KINDS.get(event['kind'], event['kind'])}；{quota_text(event.get('quota'))}。" for event in row.get("game_events", []))
        return text

    @staticmethod
    def import_memory(row):
        text = Recording.memory_text(row)
        payload = {
            "character_name": row.get("character_name", "YUI"),
            "source_format": "auto",
            "files": [{"path": "MEMORY.md", "content": "# 猫粮互动记录\n\n- " + text + "\n"}],
        }
        req = Request(
            "http://127.0.0.1:48911/api/memory/external_import/commit",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        with build_opener(ProxyHandler({})).open(req, timeout=30) as response:
            result = json.load(response)
        return bool(result.get("success") and (result.get("added_facts", 0) or result.get("skipped_duplicates", 0)))
