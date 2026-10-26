"""N.E.K.O x D-one 桥接插件。

数据流::

    D-one (macOS 优先级便签)
      │  done-bridge.js (localStorage 变更 → HTTP POST)
      ▼
    本插件 HTTP Server (127.0.0.1:48917)
      │  diff 出任务事件 → 按优先级映射猫娘语气
      ▼
    push_message → NEKO 伙伴主动提醒

设计原则：
  - D-one 是数据权威方，插件只维护只读快照
  - 完整复刻 D-one 的“只看一件事”哲学：get_top_entry 只返回最高优先级
  - 语气风格可配置，默认猫娘三档（催促/认真/温柔），不绑定任何歌姬角色
"""

from __future__ import annotations

import glob
import json
import os
import sqlite3
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional

from plugin.sdk.plugin import (
    Err,
    NekoPluginBase,
    Ok,
    SdkError,
    lifecycle,
    neko_plugin,
    plugin_entry,
)

_DEFAULT_PORT = 48917
_DEFAULT_COOLDOWN = 90
_POLL_INTERVAL = 2.0
_MAX_EVENTS = 100
_VALID_STYLES = ("neko_default", "calm", "idol")
_VALID_PRIORITIES = (0, 1, 2)
_PRIORITY_NAMES = {0: "p0", 1: "p1", 2: "p2"}

# 各优先级对应的猫娘语气档位
_PRIORITY_TONE = {0: "urge", 1: "remind", 2: "gentle"}

# D-one 各版本线的 WebKit localStorage bundle id（自动发现子路径）
_DONE_BUNDLE_IDS = (
    "local.codex.prioritydesk",            # 正式版
    "local.codex.prioritydesk.mascotdev",  # 角色助手开发版
    "local.codex.prioritydesk.qa232",      # QA 版
    "local.codex.done.bilibili.concept",   # B 站概念版（key 不同）
)
_DONE_KEY_BY_BUNDLE = {
    "local.codex.done.bilibili.concept": "d-one-bili-concept-tasks-v1",
}
_DEFAULT_TASKS_KEY = "priority-desk-tasks-v1"


def _read_webkit_localstorage(db_path: str, key: str) -> Optional[List[Dict[str, Any]]]:
    """只读读取 WKWebView localStorage 里的任务 JSON。

    WKWebView 的 localstorage.sqlite3 中 value 直接以 UTF-16-LE 存储的 JSON 数组。
    返回 None 表示读取失败或无该 key。
    """
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            row = con.execute(
                "SELECT value FROM ItemTable WHERE key=?", (key,)
            ).fetchone()
        finally:
            con.close()
        if row is None or row[0] is None:
            return None
        raw = row[0]
        if isinstance(raw, str):
            raw = raw.encode("utf-16-le", errors="ignore")
        for enc in ("utf-16-le", "utf-8"):
            try:
                data = json.loads(raw.decode(enc))
                return data if isinstance(data, list) else None
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
        return None
    except Exception:
        return None


def _discover_done_sources(home: Optional[str] = None) -> List[Dict[str, str]]:
    """自动发现本机 D-one 各版本线的 localStorage 数据库。"""
    home = home or str(Path.home())
    sources: List[Dict[str, str]] = []
    for bundle in _DONE_BUNDLE_IDS:
        pattern = os.path.join(
            home, "Library", "WebKit", bundle,
            "WebsiteData", "Default", "*", "*", "LocalStorage", "localstorage.sqlite3",
        )
        for db in sorted(glob.glob(pattern)):
            sources.append(
                {
                    "bundle": bundle,
                    "db": db,
                    "key": _DONE_KEY_BY_BUNDLE.get(bundle, _DEFAULT_TASKS_KEY),
                }
            )
    return sources


def _pick_text(style: str, tone: str, text: str, user: str) -> str:
    """按风格和语气档位生成提醒台词。"""
    u = user or "你"
    if style == "calm":
        calm_map = {
            "urge": f"提醒：{u}有一个高优先级任务待处理——「{text}」。",
            "remind": f"「{text}」有更新，请注意安排时间。",
            "gentle": f"任务「{text}」已完成，做得好。",
            "praise": f"「{text}」完成，已记录。",
        }
        return calm_map.get(tone, calm_map["remind"])
    if style == "idol":
        # 歌姬风：不使用任何真实角色名称/台词，仅作语气风格参考
        idol_map = {
            "urge": f"{u}！最高优先级的「{text}」还在等着呢，加油冲鸭！✨",
            "remind": f"叮咚~「{text}」更新啦，别忘了它哦♪",
            "gentle": f"「{text}」完成！今天也是最棒的一天✨",
            "praise": f"「{text}」完成，太厉害了！✨",
        }
        return idol_map.get(tone, idol_map["remind"])
    # neko_default：傲娇猫娘三档
    neko_map = {
        "urge": f"哼，「{text}」还挂着呢……本喵可一直盯着{u}哦，快去做喵！",
        "remind": f"「{text}」有动静了，{u}可别把它忘在角落里喵。",
        "gentle": f"「{text}」完成啦~今天也辛苦了喵。",
        "praise": f"哦？「{text}」都做完了？勉强……夸夸{u}喵。",
    }
    return neko_map.get(tone, neko_map["remind"])


def _diff_tasks(old: Dict[str, Dict[str, Any]], new_list: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """对比旧快照与新任务列表，产出事件列表。

    事件结构：{"action", "task_id", "text", "priority"}
    action ∈ add / complete / reopen / delete / edit / promote / demote
    """
    events: List[Dict[str, Any]] = []
    new_ids = set()
    for t in new_list:
        tid = str(t.get("id", ""))
        if not tid:
            continue
        new_ids.add(tid)
        old_t = old.get(tid)
        priority = t.get("priority") if t.get("priority") in _VALID_PRIORITIES else 2
        text = str(t.get("text", "")).strip() or "(未命名任务)"
        if old_t is None:
            events.append({"action": "add", "task_id": tid, "text": text, "priority": priority})
            continue
        was_done = bool(old_t.get("done"))
        is_done = bool(t.get("done"))
        if not was_done and is_done:
            events.append({"action": "complete", "task_id": tid, "text": text, "priority": priority})
        elif was_done and not is_done:
            events.append({"action": "reopen", "task_id": tid, "text": text, "priority": priority})
        if old_t.get("priority") != priority and old_t.get("priority") in _VALID_PRIORITIES:
            action = "promote" if priority < old_t.get("priority") else "demote"
            events.append({"action": action, "task_id": tid, "text": text, "priority": priority})
        elif str(old_t.get("text", "")) != str(t.get("text", "")):
            events.append({"action": "edit", "task_id": tid, "text": text, "priority": priority})
    for tid in set(old) - new_ids:
        events.append(
            {
                "action": "delete",
                "task_id": tid,
                "text": str(old[tid].get("text", "(未命名任务)")),
                "priority": old[tid].get("priority", 2),
            }
        )
    return events


class _BridgeHTTPHandler(BaseHTTPRequestHandler):
    """处理 D-one 桥接请求。"""

    plugin_instance: Optional["NekoDoneBridgePlugin"] = None

    def do_GET(self):  # noqa: N802
        if self.path == "/health":
            self._respond(200, {"status": "ok", "plugin": "neko_done_bridge"})
        elif self.path == "/snapshot":
            self._respond_snapshot()
        else:
            self._respond(404, {"error": "Not found"})

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        try:
            data = json.loads(body) if body else {}
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._respond(400, {"error": "Invalid JSON"})
            return

        if self.path in ("/hook/tasks", "/hook/task-event", "/hook/set-style"):
            if not self._authorize():
                return
        if self.path == "/hook/tasks":
            self._respond(200, self.plugin_instance.on_tasks_snapshot(data))
        elif self.path == "/hook/task-event":
            self._respond(200, self.plugin_instance.on_task_event(data))
        elif self.path == "/hook/set-style":
            self._respond(200, self.plugin_instance.on_set_style(data))
        elif self.path == "/health":
            self._respond(200, {"status": "ok"})
        else:
            self._respond(404, {"error": "Not found"})

    def _respond_snapshot(self):
        if not self._authorize():
            return
        if self.plugin_instance is None:
            self._respond(500, {"error": "Plugin not initialized"})
            return
        self._respond(200, self.plugin_instance.build_snapshot())

    def _authorize(self) -> bool:
        token = self.plugin_instance._api_token if self.plugin_instance else ""
        if not token:
            return True
        auth = self.headers.get("Authorization", "")
        if auth == f"Bearer {token}":
            return True
        self._respond(401, {"error": "unauthorized"})
        return False

    def _respond(self, code: int, payload: Dict[str, Any]):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args):  # noqa: A002
        """静默默认日志，避免刷屏。"""


@neko_plugin
class NekoDoneBridgePlugin(NekoPluginBase):
    """D-one 桥接插件 - 让猫娘成为你的优先级任务管家。"""

    def __init__(self, ctx: Any):
        super().__init__(ctx)
        self._http_server: Optional[HTTPServer] = None
        self._http_thread: Optional[threading.Thread] = None
        self._port: int = _DEFAULT_PORT
        self._cooldown: int = _DEFAULT_COOLDOWN
        self._style: str = "neko_default"
        self._api_token: str = ""
        self._user_name: str = ""
        self._tasks: Dict[str, Dict[str, Any]] = {}
        self._events: List[Dict[str, Any]] = []
        self._last_push_time: float = 0.0
        self._lock = threading.Lock()
        self._poll_thread: Optional[threading.Thread] = None
        self._poll_stop = threading.Event()
        self._source_mtimes: Dict[str, float] = {}
        self._sources: List[Dict[str, str]] = []
        self._baseline_done = False

    # ── 生命周期 ─────────────────────────────────────────────────────────

    @lifecycle(id="startup")
    async def startup(self, **_):
        try:
            cfg = await self.config.dump(timeout=5.0)
            cfg = cfg if isinstance(cfg, dict) else {}
        except Exception as e:
            self.logger.warning("Failed to load config, using defaults: {}", e)
            cfg = {}
        b_cfg = cfg.get("neko_done_bridge") if isinstance(cfg.get("neko_done_bridge"), dict) else {}

        try:
            self._port = int(b_cfg.get("port", _DEFAULT_PORT))
        except (TypeError, ValueError):
            self._port = _DEFAULT_PORT
        try:
            self._cooldown = max(10, int(b_cfg.get("cooldown_seconds", _DEFAULT_COOLDOWN)))
        except (TypeError, ValueError):
            self._cooldown = _DEFAULT_COOLDOWN
        style = str(b_cfg.get("style", "neko_default"))
        self._style = style if style in _VALID_STYLES else "neko_default"
        self._api_token = str(b_cfg.get("api_token", "") or "")

        self._load_snapshot()
        self._user_name = self._get_user_name()

        try:
            self._start_http_server()
        except Exception as e:
            self.logger.error("Failed to start HTTP server on port {}: {}", self._port, e)

        # 启动零侵入轮询：直接监听 D-one 各版本的 WebKit localStorage
        self._sources = _discover_done_sources()
        if self._sources:
            self._poll_stop.clear()
            self._poll_thread = threading.Thread(
                target=self._poll_loop, daemon=True, name="neko-done-bridge-poll"
            )
            self._poll_thread.start()
            self.logger.info(
                "Polling {} D-one localStorage source(s): {}",
                len(self._sources),
                ", ".join(s["bundle"] for s in self._sources),
            )
        else:
            self.logger.info("No D-one localStorage found; HTTP bridge only.")

        try:
            self.register_static_ui("static")
        except Exception as e:
            self.logger.warning("Failed to register static UI: {}", e)

        self.logger.info(
            "NekoDoneBridge started: port={}, style={}, tasks={}",
            self._port,
            self._style,
            len(self._tasks),
        )
        return Ok({"status": "running", "port": self._port, "style": self._style})

    @lifecycle(id="shutdown")
    def shutdown(self, **_):
        self._poll_stop.set()
        self._save_snapshot()
        if self._http_server:
            try:
                self._http_server.shutdown()
            except Exception:
                pass
            try:
                self._http_server.server_close()
            except Exception:
                pass
            self._http_server = None
        if self._http_thread and self._http_thread.is_alive():
            self._http_thread.join(timeout=3)
        self.logger.info("NekoDoneBridge shutdown")
        return Ok({"status": "shutdown"})

    # ── HTTP 服务器 ──────────────────────────────────────────────────────

    def _start_http_server(self):
        _BridgeHTTPHandler.plugin_instance = self
        server = HTTPServer(("127.0.0.1", self._port), _BridgeHTTPHandler)
        self._http_server = server

        def run_server():
            try:
                self.logger.info("D-one bridge HTTP server listening on port {}", self._port)
                server.serve_forever()
            except Exception as e:
                self.logger.error("HTTP server error: {}", e)

        self._http_thread = threading.Thread(
            target=run_server, daemon=True, name="neko-done-bridge-http"
        )
        self._http_thread.start()

    # ── 零侵入轮询（WebKit localStorage）────────────────────────────────

    def _poll_loop(self):
        while not self._poll_stop.wait(_POLL_INTERVAL):
            try:
                self._poll_once()
            except Exception as e:
                self.logger.warning("Poll error: {}", e)

    def _poll_once(self):
        """检查各数据源 mtime，变化则读取并 diff。首轮静默建立基线。"""
        baseline = not self._baseline_done
        for src in self._sources:
            try:
                mtime = os.path.getmtime(src["db"])
            except OSError:
                continue
            if self._source_mtimes.get(src["db"]) == mtime:
                continue
            self._source_mtimes[src["db"]] = mtime
            tasks = _read_webkit_localstorage(src["db"], src["key"])
            if tasks is None:
                continue
            normalized = self._normalize_tasks(tasks, src["bundle"])
            if baseline:
                # 首轮：存量任务静默入库，不产生提醒
                with self._lock:
                    for t in normalized:
                        self._tasks[t["id"]] = t
            else:
                self.on_tasks_snapshot(
                    {"source": src["bundle"], "tasks": normalized}
                )
        if baseline:
            with self._lock:
                self._save_snapshot()
            self._baseline_done = True

    def _normalize_tasks(self, tasks: List[Any], bundle: str) -> List[Dict[str, Any]]:
        """D-one 原始任务 → 插件快照格式：id 加源前缀，visible → hidden。"""
        prefix = "bili-concept" if bundle == "local.codex.done.bilibili.concept" else bundle.rsplit(".", 1)[-1]
        out: List[Dict[str, Any]] = []
        for t in tasks:
            if not isinstance(t, dict):
                continue
            tid = str(t.get("id", ""))
            if not tid:
                continue
            out.append(
                {
                    "id": f"{prefix}:{tid}",
                    "text": str(t.get("text", "")).strip() or "(未命名任务)",
                    "priority": t.get("priority") if t.get("priority") in _VALID_PRIORITIES else 2,
                    "done": bool(t.get("done")),
                    "hidden": not bool(t.get("visible", True)),
                }
            )
        return out

    # ── 快照持久化 ───────────────────────────────────────────────────────

    def _load_snapshot(self):
        try:
            raw = self.store._read_value("tasks_snapshot", None)
            if isinstance(raw, str):
                data = json.loads(raw)
            elif isinstance(raw, dict):
                data = raw
            else:
                data = {}
            tasks = data.get("tasks", {})
            if isinstance(tasks, dict):
                self._tasks = tasks
        except Exception as e:
            self.logger.warning("Failed to load snapshot: {}", e)
            self._tasks = {}

    def _save_snapshot(self):
        try:
            self.store._write_value(
                "tasks_snapshot", json.dumps({"tasks": self._tasks}, ensure_ascii=False)
            )
        except Exception as e:
            self.logger.warning("Failed to save snapshot: {}", e)

    # ── 事件处理 ─────────────────────────────────────────────────────────

    def on_tasks_snapshot(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """接收 D-one 全量任务快照，diff 出事件。"""
        tasks = data.get("tasks")
        if not isinstance(tasks, list):
            return {"status": "error", "error": "tasks must be a list"}
        with self._lock:
            events = _diff_tasks(self._tasks, tasks)
            new_tasks: Dict[str, Dict[str, Any]] = {}
            for t in tasks:
                tid = str(t.get("id", ""))
                if not tid:
                    continue
                new_tasks[tid] = {
                    "id": tid,
                    "text": str(t.get("text", "")).strip() or "(未命名任务)",
                    "priority": t.get("priority") if t.get("priority") in _VALID_PRIORITIES else 2,
                    "done": bool(t.get("done")),
                    "hidden": bool(t.get("hidden")),
                    "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                }
            self._tasks = new_tasks
            self._save_snapshot()
            notified = self._record_events(events)
        return {"status": "ok", "task_count": len(new_tasks), "events": events, "notified": notified}

    def on_task_event(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """处理单个任务事件（用于模拟注入或轻量客户端）。"""
        action = str(data.get("action", ""))
        task = data.get("task") or {}
        tid = str(task.get("id", ""))
        if action not in ("add", "complete", "delete", "edit", "promote", "demote", "reopen"):
            return {"status": "error", "error": f"invalid action: {action}"}
        if not tid:
            return {"status": "error", "error": "task.id is required"}
        with self._lock:
            if action == "delete":
                old = self._tasks.pop(tid, None)
                text = str((old or task).get("text", "(未命名任务)"))
                priority = (old or task).get("priority", 2)
            else:
                priority = task.get("priority") if task.get("priority") in _VALID_PRIORITIES else 2
                text = str(task.get("text", "")).strip() or "(未命名任务)"
                self._tasks[tid] = {
                    "id": tid,
                    "text": text,
                    "priority": priority,
                    "done": bool(task.get("done")) if action != "complete" else True,
                    "hidden": bool(task.get("hidden")),
                    "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                }
            self._save_snapshot()
            notified = self._record_events(
                [{"action": action, "task_id": tid, "text": text, "priority": priority}]
            )
        return {"status": "ok", "action": action, "notified": notified}

    def build_snapshot(self) -> Dict[str, Any]:
        """面板/外部查询用的完整快照。"""
        with self._lock:
            groups: Dict[str, List[Dict[str, Any]]] = {"p0": [], "p1": [], "p2": []}
            for t in self._tasks.values():
                groups[_PRIORITY_NAMES[t["priority"]]].append(t)
            return {
                "status": "running",
                "port": self._port,
                "style": self._style,
                "cooldown": self._cooldown,
                "groups": groups,
                "events": list(reversed(self._events[-30:])),
            }

    def _record_events(self, events: List[Dict[str, Any]]) -> bool:
        """记录事件并按冷却决定是否推送提醒。返回是否已推送。"""
        if not events:
            return False
        now = time.time()
        self._events.extend(
            [{**e, "at": time.strftime("%H:%M:%S")} for e in events]
        )
        if len(self._events) > _MAX_EVENTS:
            self._events = self._events[-_MAX_EVENTS:]

        # 选择值得打扰的事件：优先级最高的那条
        rank = {"promote": 0, "add": 1, "complete": 2, "reopen": 3, "delete": 4, "demote": 5, "edit": 6}
        top = min(events, key=lambda e: rank.get(e["action"], 9))
        # edit / delete 静默记录，不打扰
        if top["action"] in ("edit", "delete"):
            return False
        if now - self._last_push_time < self._cooldown:
            return False
        self._last_push_time = now
        tone = "praise" if top["action"] == "complete" else _PRIORITY_TONE.get(top.get("priority", 2), "remind")
        self._notify(_pick_text(self._style, tone, top["text"], self._user_name), top)
        return True

    def _notify(self, text: str, event: Dict[str, Any]):
        try:
            self.push_message(
                source="neko_done_bridge",
                visibility=["chat"],
                ai_behavior="respond",
                parts=[{"type": "text", "text": text}],
                priority=5,
                metadata={
                    "activity_type": "task_" + str(event.get("action", "unknown")),
                    "task_id": event.get("task_id", ""),
                    "priority": event.get("priority", 2),
                },
            )
        except Exception as e:
            self.logger.error("push_message failed: {}", e)

    def _get_user_name(self) -> str:
        try:
            from utils.config_manager import get_config_manager

            cm = get_config_manager()
            master = cm.get_character_data().get("主人", {})
            name = master.get("档案名", "") or master.get("昵称", "")
            if name:
                return str(name)
        except Exception:
            pass
        try:
            stored = self.store._read_value("user_name", "")
            if stored:
                return str(stored)
        except Exception:
            pass
        return ""

    # ── 插件入口点（供 LLM / API 调用）──

    @plugin_entry(
        id="get_status",
        name="获取桥接状态",
        description="获取 D-one 桥接插件的运行状态：端口、语气风格、任务总数",
        llm_result_fields=["status", "port", "style", "task_count"],
    )
    async def get_status(self, **_):
        with self._lock:
            return Ok(
                {
                    "status": "running" if self._http_server else "stopped",
                    "port": self._port,
                    "style": self._style,
                    "task_count": len(self._tasks),
                }
            )

    @plugin_entry(
        id="get_tasks",
        name="获取任务列表",
        description="获取 D-one 当前全部任务，按 p0/p1/p2 优先级分组。当主人问「我有哪些事要做」「今天安排」时调用",
        llm_result_fields=["p0", "p1", "p2", "total"],
    )
    async def get_tasks(self, **_):
        with self._lock:
            groups: Dict[str, List[Dict[str, Any]]] = {"p0": [], "p1": [], "p2": []}
            for t in self._tasks.values():
                if not t.get("done"):
                    groups[_PRIORITY_NAMES[t["priority"]]].append(
                        {"text": t["text"], "id": t["id"]}
                    )
            total = sum(len(v) for v in groups.values())
            return Ok({**groups, "total": total})

    @plugin_entry(
        id="get_top_task",
        name="获取最高优先级任务",
        description="只获取当前最重要的一件事（D-one「只看一件事」哲学）。当主人问「我现在该干嘛」「接下来做什么」时调用",
        llm_result_fields=["has_task", "text", "priority"],
    )
    async def get_top_task(self, **_):
        with self._lock:
            candidates = [t for t in self._tasks.values() if not t.get("done")]
            if not candidates:
                return Ok({"has_task": False, "text": "", "priority": None})
            top = min(candidates, key=lambda t: t["priority"])
            return Ok(
                {"has_task": True, "text": top["text"], "priority": _PRIORITY_NAMES[top["priority"]]}
            )

    @plugin_entry(
        id="set_style",
        name="切换语气风格",
        description="切换猫娘提醒任务时的语气风格：neko_default（傲娇猫娘，默认）、calm（平静助理）、idol（元气应援）",
        input_schema={
            "type": "object",
            "properties": {
                "style": {
                    "type": "string",
                    "description": "风格：neko_default / calm / idol",
                    "enum": list(_VALID_STYLES),
                },
            },
            "required": ["style"],
        },
    )
    async def set_style(self, style: str, **_):
        if style not in _VALID_STYLES:
            return Err(SdkError(f"未知风格：{style}，可选 {', '.join(_VALID_STYLES)}"))
        self._style = style
        try:
            self.store._write_value("style", style)
        except Exception as e:
            self.logger.warning("Failed to persist style: {}", e)
        return Ok({"style": style})

    def on_set_style(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """HTTP 端点版风格切换（面板用）。"""
        style = str(data.get("style", ""))
        if style not in _VALID_STYLES:
            return {"status": "error", "error": f"invalid style: {style}"}
        self._style = style
        try:
            self.store._write_value("style", style)
        except Exception as e:
            self.logger.warning("Failed to persist style: {}", e)
        return {"status": "ok", "style": style}

    @plugin_entry(
        id="test_push",
        name="测试提醒推送",
        description="模拟一条 p0 任务新增事件，让猫娘立刻演示一次任务提醒",
    )
    async def test_push(self, **_):
        self._user_name = self._user_name or self._get_user_name()
        result = self.on_task_event(
            {
                "action": "add",
                "task": {
                    "id": f"test-{int(time.time())}",
                    "text": "这是 D-one 桥接的测试任务",
                    "priority": 0,
                    "done": False,
                },
            }
        )
        return Ok(result)
