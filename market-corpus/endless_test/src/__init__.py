"""Endless Test ↔ N.E.K.O. bridge.

三个职责：
1. 起一个本地 HTTP 服务，把《无尽测试》单文件页面分享出去。
2. 把游戏数据统一存在插件目录（state.json），网页/面板/全屏窗口共用同一份。
3. 给 Neko 提供入口：开局、读档案、记战报、重置。

启动方式：
    launch_web      默认浏览器打开
    launch_panel    插件面板内嵌（返回面板地址）
    launch_window   独立全屏窗口（Edge/Chrome --app 模式，无地址栏）
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
import uuid
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import parse_qs, urlparse

from plugin.sdk.plugin import (
    Err,
    NekoPluginBase,
    Ok,
    SdkError,
    lifecycle,
    neko_plugin,
    plugin_entry,
)

try:
    from plugin.sdk.plugin import llm_tool
except Exception:  # 旧 SDK 没有 llm_tool 时退化成普通入口，至少不炸
    def llm_tool(*_a, **_k):
        def _wrap(fn):
            return fn
        return _wrap

try:
    from . import brain
except Exception:  # pragma: no cover - 独立运行时兜底
    import brain  # type: ignore


def _class_name(cid: str) -> str:
    return brain.CLASSES.get(cid, {}).get("name", cid)


DEFAULT_PORT = 48931
STATE_VERSION = 1

_BROWSER_CANDIDATES = (
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
)

_MIME = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".svg": "image/svg+xml",
    ".woff2": "font/woff2",
    ".ico": "image/x-icon",
}


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _profile_dir(root: Path) -> Path:
    """窗口模式专用浏览器配置，放在插件安装目录之外的稳定位置。"""
    base = os.getenv("NEKO_PLUGIN_DATA_DIR") or os.getenv("NEKO_DATA_DIR") or ""
    if base:
        return Path(base) / "endless_test" / "app-profile"
    return Path.home() / "AppData" / "Local" / "endless_test" / "app-profile"


def _window_args(url: str, root: Path, size: str) -> list:
    """独立窗口参数：无边框、无地址栏、无菜单，像原生客户端。"""
    # 浏览器配置放数据目录：安装目录会在重装时被清空，而且运行中会锁文件
    profile = _profile_dir(root)
    profile.mkdir(parents=True, exist_ok=True)
    return [
        f"--app={url}",
        "--kiosk",
        f"--window-size={size}",
        "--window-position=0,0",
        "--no-first-run",
        "--no-default-browser-check",
        "--no-service-autorun",
        "--disable-features=Translate,MediaRouter,GlobalMediaControls",
        "--disable-session-crashed-bubble",
        "--autoplay-policy=no-user-gesture-required",
        "--overscroll-history-navigation=0",
        "--disable-pinch",
        f"--user-data-dir={profile}",
    ]


def _find_browser() -> Optional[str]:
    for path in _BROWSER_CANDIDATES:
        if os.path.isfile(path):
            return path
    for name in ("msedge", "chrome"):
        found = shutil.which(name)
        if found:
            return found
    return None


@neko_plugin
class EndlessTestPlugin(NekoPluginBase):
    """无尽测试插件：托管页面 + 统一存档。"""

    def __init__(self, ctx: Any):
        super().__init__(ctx)
        self.file_logger = self.enable_file_logging(log_level="INFO")
        self.logger = self.file_logger
        self._lock = threading.RLock()
        self._http: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self._port = DEFAULT_PORT
        self._root = Path(__file__).resolve().parent
        self._static_dir = self._root / "static"
        self._state_path: Optional[Path] = None
        self._ally_push_chat = True
        self._ally = {"class": "", "tree": [], "plan": [], "reason": "", "since": 0.0}
        self._session = {"token": "", "mode": "", "since": 0.0, "seen": 0.0}
        self._session_lock = threading.RLock()
        self._window_proc: Optional[subprocess.Popen] = None
        # AI 伙伴：全部决策来自 Neko，插件只做状态中转
        self._ally = {"class": "", "tree": [], "plan": [], "reason": "", "since": 0.0}
        self._ally_order = {"stance": "follow", "focus": "nearest", "buy": [], "say": "", "cast": "none", "id": 0, "at": 0.0}
        self._ally_snap: Dict[str, Any] = {}
        self._ally_snap_at = 0.0
        self._ally_owned: list = []
        self._ally_nudge_at = 0.0
        self._ally_lock = threading.RLock()
        self._ally_stop = threading.Event()
        self._ally_thread: Optional[threading.Thread] = None

    # ---------------- 路径 ----------------

    def _resolve_state_path(self) -> Path:
        if self._state_path:
            return self._state_path
        try:
            base = Path(self.data_path("state.json"))
            path = base if base.suffix == ".json" else base / "state.json"
        except Exception:
            path = self._root / "state.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        self._state_path = path
        return path

    def _game_file(self) -> Path:
        return self._static_dir / "index.html"

    # ---------------- 数据 ----------------

    def _read_state(self) -> Dict[str, Any]:
        path = self._resolve_state_path()
        with self._lock:
            if not path.is_file():
                return {"v": STATE_VERSION, "updated_at": "", "data": {}, "runs": []}
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception as exc:
                self.logger.warning("state.json unreadable, starting fresh: {}", exc)
                return {"v": STATE_VERSION, "updated_at": "", "data": {}, "runs": []}
        if not isinstance(payload, dict):
            return {"v": STATE_VERSION, "updated_at": "", "data": {}, "runs": []}
        payload.setdefault("v", STATE_VERSION)
        payload.setdefault("data", {})
        payload.setdefault("runs", [])
        return payload

    def _write_state(self, payload: Dict[str, Any]) -> None:
        path = self._resolve_state_path()
        payload["v"] = STATE_VERSION
        payload["updated_at"] = _now()
        tmp = path.with_suffix(".tmp")
        text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        with self._lock:
            tmp.write_text(text, encoding="utf-8")
            os.replace(tmp, path)

    def _merge_state(self, incoming: Dict[str, Any]) -> Dict[str, Any]:
        """按 key 合并：incoming 覆盖同名 key，保留其余。"""
        payload = self._read_state()
        data = payload.get("data")
        if not isinstance(data, dict):
            data = {}
        for key, value in (incoming or {}).items():
            if not isinstance(key, str):
                continue
            if value is None:
                data.pop(key, None)
            else:
                data[key] = value
        payload["data"] = data
        return payload

    # ---------------- HTTP ----------------

    def _start_http(self) -> None:
        if self._http:
            return
        plugin = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
                return

            def _cors(self) -> None:
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Methods", "GET, PUT, POST, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "Content-Type")
                self.send_header("Cache-Control", "no-store")

            def _send(self, code: int, body: bytes, mime: str) -> None:
                self.send_response(code)
                self.send_header("Content-Type", mime)
                self.send_header("Content-Length", str(len(body)))
                self._cors()
                self.end_headers()
                try:
                    self.wfile.write(body)
                except Exception:
                    pass

            def _json(self, code: int, payload: Any) -> None:
                self._send(code, json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                           "application/json; charset=utf-8")

            def do_OPTIONS(self) -> None:  # noqa: N802
                self.send_response(204)
                self._cors()
                self.send_header("Content-Length", "0")
                self.end_headers()

            def do_GET(self) -> None:  # noqa: N802
                route = urlparse(self.path).path
                if route == "/api/state":
                    self._json(200, plugin._read_state())
                    return
                if route == "/api/profile":
                    self._json(200, plugin._profile())
                    return
                if route == "/api/ping":
                    self._json(200, {"ok": True, "plugin": "endless_test", "port": plugin._port})
                    return
                if route == "/api/ally":
                    self._json(200, {"ok": True, "say": plugin._ally_line("tick", {})})
                    return
                if route == "/api/ally/meta":
                    self._json(200, plugin._ally_meta())
                    return
                if route == "/api/session":
                    self._json(200, plugin._session_state())
                    return
                if route == "/api/launch":
                    mode = (parse_qs(urlparse(self.path).query).get("mode") or ["web"])[0]
                    self._json(200, plugin._launch_mode(mode))
                    return
                plugin._serve_static(self, route)

            def do_PUT(self) -> None:  # noqa: N802
                self._handle_write()

            def do_POST(self) -> None:  # noqa: N802
                self._handle_write()

            def _handle_write(self) -> None:
                route = urlparse(self.path).path
                if route not in ("/api/state", "/api/merge", "/api/ally", "/api/report", "/api/quit",
                                 "/api/ally/start", "/api/ally/state", "/api/ally/command", "/api/ally/order",
                                 "/api/session/claim", "/api/session/ping", "/api/session/release"):
                    self._json(404, {"ok": False, "error": "not found"})
                    return
                try:
                    length = int(self.headers.get("Content-Length") or 0)
                    raw = self.rfile.read(length) if length else b"{}"
                    incoming = json.loads(raw.decode("utf-8") or "{}")
                except Exception as exc:
                    self._json(400, {"ok": False, "error": f"bad payload: {exc}"})
                    return
                try:
                    if route == "/api/session/claim":
                        self._json(200, plugin._session_claim(str((incoming or {}).get("mode") or "web")))
                        return
                    if route == "/api/session/ping":
                        self._json(200, plugin._session_ping(str((incoming or {}).get("token") or "")))
                        return
                    if route == "/api/session/release":
                        self._json(200, plugin._session_release(str((incoming or {}).get("token") or "")))
                        return
                    if route == "/api/quit":
                        self._json(200, plugin._handle_quit(incoming if isinstance(incoming, dict) else {}))
                        return
                    if route == "/api/ally":
                        self._json(200, plugin._handle_ally(incoming if isinstance(incoming, dict) else {}))
                        return
                    if route == "/api/ally/start":
                        self._json(200, plugin._handle_ally_start(incoming if isinstance(incoming, dict) else {}))
                        return
                    if route == "/api/ally/state":
                        self._json(200, plugin._handle_ally_state(incoming if isinstance(incoming, dict) else {}))
                        return
                    if route == "/api/ally/command":
                        self._json(200, plugin._handle_ally_command(incoming if isinstance(incoming, dict) else {}))
                        return
                    if route == "/api/ally/order":
                        self._json(200, plugin._handle_ally_order_debug(incoming if isinstance(incoming, dict) else {}))
                        return
                    if route == "/api/report":
                        self._json(200, plugin._handle_report(incoming if isinstance(incoming, dict) else {}))
                        return
                    if route == "/api/merge":
                        data = incoming.get("data") if isinstance(incoming, dict) else None
                        payload = plugin._merge_state(data if isinstance(data, dict) else {})
                    else:
                        payload = incoming if isinstance(incoming, dict) else {}
                        payload.setdefault("data", {})
                    plugin._write_state(payload)
                    self._json(200, {"ok": True, "updated_at": payload.get("updated_at", "")})
                except Exception as exc:
                    plugin.logger.error("state write failed: {}", exc)
                    self._json(500, {"ok": False, "error": str(exc)})

        last_error: Optional[Exception] = None
        for port in (self._port, self._port + 1, self._port + 2, 0):
            try:
                server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
                self._port = server.server_address[1]
                self._http = server
                break
            except OSError as exc:
                last_error = exc
        if not self._http:
            raise RuntimeError(f"cannot bind http server: {last_error}")

        self._thread = threading.Thread(
            target=self._http.serve_forever, name="endless-test-http", daemon=True
        )
        self._thread.start()
        self.logger.info("Endless Test served at http://127.0.0.1:{}", self._port)

    def _serve_static(self, handler: BaseHTTPRequestHandler, route: str) -> None:
        rel = "index.html" if route in ("/", "") else route.lstrip("/")
        target = (self._static_dir / rel).resolve()
        try:
            target.relative_to(self._static_dir.resolve())
        except ValueError:
            handler._json(403, {"ok": False, "error": "forbidden"})
            return
        if not target.is_file():
            handler._json(404, {"ok": False, "error": f"missing {rel}"})
            return
        body = target.read_bytes()
        handler._send(200, body, _MIME.get(target.suffix.lower(), "application/octet-stream"))

    def _stop_http(self) -> None:
        if self._http:
            try:
                self._http.shutdown()
                self._http.server_close()
            except Exception:
                pass
            self._http = None
        self._thread = None

    # ---------------- 启动方式 ----------------

    def _url(self, mode: str = "") -> str:
        base = f"http://127.0.0.1:{self._port}/"
        return base + (f"?mode={mode}" if mode else "")

    def _open_browser(self, url: str) -> bool:
        try:
            if hasattr(os, "startfile"):
                os.startfile(url)  # type: ignore[attr-defined]
                return True
        except Exception as exc:
            self.logger.warning("startfile failed: {}", exc)
        try:
            browser = _find_browser()
            if not browser:
                return False
            subprocess.Popen([browser, url], close_fds=True)
            return True
        except Exception as exc:
            self.logger.warning("browser launch failed: {}", exc)
            return False

    def _open_window(self, size: str = "1280,720") -> bool:
        browser = _find_browser()
        if not browser:
            return False
        args = [browser] + _window_args(self._url("window"), self._root, size)
        try:
            subprocess.Popen(args, close_fds=True)
            return True
        except Exception as exc:
            self.logger.warning("window launch failed: {}", exc)
            return False


    def _launch_mode(self, mode: str) -> Dict[str, Any]:
        """给面板按钮用：web / window / panel。"""
        try:
            self._start_http()
        except Exception as exc:
            return {"ok": False, "error": f"服务未启动: {exc}"}
        if mode in ("web", "window"):
            st = self._session_state()
            if st["active"] and st["mode"] != mode:
                label = {"panel": "插件面板", "web": "网页", "window": "全屏程序"}.get(st["mode"], st["mode"])
                return {"ok": False, "error": f"已经有一个游戏在运行（{label}）", "active_mode": st["mode"]}
        if mode == "window":
            browser = _find_browser()
            if not browser:
                return {"ok": False, "error": "没找到 Edge/Chrome"}
            args = [browser] + _window_args(self._url("window"), self._root, "1280,720")
            try:
                subprocess.Popen(args, close_fds=True)
            except Exception as exc:
                return {"ok": False, "error": str(exc)}
            return {"ok": True, "mode": mode, "url": self._url()}
        if mode == "web":
            ok = self._open_browser(self._url("web"))
            return {"ok": ok, "mode": mode, "url": self._url(),
                    "error": "" if ok else "浏览器没能自动打开"}
        return {"ok": True, "mode": "panel", "url": self._url()}

    # ---------------- 档案 ----------------

    def _profile(self) -> Dict[str, Any]:
        state = self._read_state()
        data = state.get("data") if isinstance(state.get("data"), dict) else {}
        meta_raw = data.get("nanite_meta_v1")
        meta: Dict[str, Any] = {}
        if isinstance(meta_raw, str) and meta_raw:
            try:
                meta = json.loads(meta_raw)
            except Exception:
                meta = {}
        runs = state.get("runs") if isinstance(state.get("runs"), list) else []
        best = None
        for run in runs:
            if not isinstance(run, dict):
                continue
            secs = run.get("seconds") or 0
            if best is None or secs > (best.get("seconds") or 0):
                best = run
        return {
            "clears": meta.get("clears", 0),
            "kills": meta.get("kills", 0),
            "runs_recorded": len(runs),
            "last_run": runs[-1] if runs else None,
            "best_run": best,
            "class_ranks": meta.get("classes", {}),
            "boss_best": meta.get("bossBest", {}),
            "updated_at": state.get("updated_at", ""),
        }


    def _register_ui(self) -> bool:
        """注册静态 UI；面板必须 no-store，否则宿主会缓存一小时旧版。"""
        cfg = {
            "enabled": True,
            "directory": str(self._static_dir),
            "index_file": "panel.html",
            "cache_control": "no-store, must-revalidate",
            "plugin_id": getattr(self, "plugin_id", "endless_test"),
        }
        notify = getattr(self, "_notify_static_ui_registered", None)
        if callable(notify):
            try:
                self._static_ui_config = cfg
                notify(cfg)
                self.logger.info("static ui registered with no-store")
                return True
            except Exception as exc:
                self.logger.warning("notify_static_ui_registered failed: {}", exc)
        try:
            return bool(self.register_static_ui("static"))
        except Exception as exc:
            self.logger.warning("register_static_ui failed: {}", exc)
            return False


    # ---------------- 单实例会话 ----------------

    SESSION_TTL = 12.0

    def _session_alive(self) -> bool:
        with self._session_lock:
            return bool(self._session["token"]) and (time.time() - self._session["seen"]) < self.SESSION_TTL

    def _session_state(self) -> Dict[str, Any]:
        with self._session_lock:
            alive = self._session_alive()
            return {
                "active": alive,
                "mode": self._session["mode"] if alive else "",
                "since": self._session["since"] if alive else 0,
                "age": round(time.time() - self._session["since"], 1) if alive else 0,
            }

    def _session_claim(self, mode: str) -> Dict[str, Any]:
        mode = (mode or "web").strip().lower()
        with self._session_lock:
            if self._session_alive():
                if self._session["mode"] == mode and mode == "panel":
                    self._session["seen"] = time.time()   # 同一面板重载，接管
                else:
                    return {
                        "ok": False,
                        "error": "已经有一个游戏在运行了",
                        "active_mode": self._session["mode"],
                        "state": self._session_state(),
                    }
            else:
                self._session["token"] = uuid.uuid4().hex
                self._session["mode"] = mode
                self._session["since"] = time.time()
            self._session["seen"] = time.time()
            return {"ok": True, "token": self._session["token"], "mode": mode,
                    "state": self._session_state()}

    def _session_ping(self, token: str) -> Dict[str, Any]:
        with self._session_lock:
            if token and token == self._session["token"]:
                self._session["seen"] = time.time()
                return {"ok": True, "state": self._session_state()}
        return {"ok": False, "error": "会话已被接管", "state": self._session_state()}

    def _session_release(self, token: str) -> Dict[str, Any]:
        with self._session_lock:
            if token and token == self._session["token"]:
                self._session = {"token": "", "mode": "", "since": 0.0, "seen": 0.0}
                return {"ok": True}
        return {"ok": False, "error": "token 不匹配"}

    # ---------------- AI 伙伴 ----------------

    def _ally_line(self, event: str, ctx: Dict[str, Any]) -> str:
        hp = ctx.get("hp")
        boss = ctx.get("boss")
        kills = ctx.get("kills") or 0
        danger = isinstance(hp, (int, float)) and hp <= 35
        if event == "start":
            return "我跟着你，别乱冲"
        if event == "boss":
            return f"{boss or 'Boss'} 来了，先躲一轮"
        if event == "lowhp":
            return "血太少了，往后退"
        if event == "killstreak":
            return f"{kills} 连杀了，继续压"
        if event == "hurt":
            return "挨打了，换个角度"
        if event == "dead":
            return "这局先记下来，下次换流派"
        if event == "win":
            return "打穿了，厉害"
        if danger:
            return "小心点，快没血了"
        return "我在你侧后方"

    def _push_chat(self, text: str, event: str, ctx: Dict[str, Any]) -> bool:
        """让 Neko 在聊天里说一句。失败不影响游戏。"""
        try:
            push = getattr(self.ctx, "push_message", None)
            if not push:
                return False
            push(
                source="endless_test",
                visibility=["chat"],
                ai_behavior="respond",
                parts=[{"type": "text", "text": text}],
                priority=4,
                metadata={"activity_type": f"endless_test.{event}", **({"ctx": ctx} if ctx else {})},
            )
            return True
        except Exception as exc:
            self.logger.warning("push_message failed: {}", exc)
            return False

    def _handle_ally(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        event = str(payload.get("event") or "tick")
        ctx = payload.get("ctx") if isinstance(payload.get("ctx"), dict) else {}
        line = self._ally_line(event, ctx)
        pushed = False
        if self._ally_push_chat:
            # 只在关键事件推聊天，避免刷屏
            if event in {"boss", "lowhp", "dead", "win", "killstreak"}:
                pushed = self._push_chat(line, event, {k: v for k, v in ctx.items() if k in ("hp", "kills", "boss", "branch")})
        return {"ok": True, "say": line, "pushed": pushed, "event": event}

    # ---------------- AI 伙伴：由 Neko 本体做决策 ----------------
    # 游戏只负责执行（走位 / 瞄准 / 开火 / 结算），
    # 职业、姿态、集火目标、科技购买、说什么，全部由 Neko 通过 llm_tool 决定。
    # 插件只做三件事：整理战场快照 → 唤醒 Neko → 把决定交回游戏。

    ALLY_STANCES = ("follow", "engage", "focus", "guard", "retreat", "free")
    ALLY_FOCUS = ("boss", "elite", "nearest", "none")

    def _ally_meta(self) -> Dict[str, Any]:
        return {
            "ok": True,
            "classes": [
                {"id": cid, "name": brain.CLASSES[cid]["name"], "role": brain.CLASSES[cid]["role"],
                 "color": brain.CLASSES[cid]["color"], "tag": brain.CLASSES[cid]["tag"]}
                for cid in brain.CLASS_ORDER
            ],
            "current": dict(self._ally) if self._ally.get("class") else None,
            "order": dict(self._ally_order),
            "source": "neko",
        }

    def _push_ai(self, text: str, kind: str, coalesce_key: str = "ally_tick", priority: int = 9) -> bool:
        """把一条提示送进 Neko 的上下文，并要求它这一轮做出决定。

        kind/coalesce_key 分开：主人的指挥不能被周期性的战术请求合并掉。
        """
        try:
            push = getattr(self.ctx, "push_message", None)
            if not push:
                return False
            push(
                source="endless_test",
                visibility=[],
                ai_behavior="respond",
                parts=[{"type": "text", "text": text}],
                priority=priority,
                coalesce_key=coalesce_key,
                metadata={"activity_type": f"endless_test.{kind}"},
            )
            return True
        except Exception as exc:
            self.logger.warning("ally push_message failed: {}", exc)
            return False

    # ---- Neko 可调用的工具 ----

    @llm_tool(
        name="ally_pick_class",
        description=(
            "为《无尽测试》里的 AI 伙伴挑选职业。收到 [AI伙伴组建] 提示时调用一次。"
            "class_id 必须是提示里给出的职业 id；reason 用一句话说明为什么选它，"
            "这句话会显示在游戏里。只调用工具，不要用聊天消息代替。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "class_id": {"type": "string", "description": "职业 id，如 blade/cannon/field/link/aegis/vector/blight/phase"},
                "reason": {"type": "string", "description": "一句话理由，会显示给玩家"},
            },
            "required": ["class_id"],
        },
        timeout=15.0,
    )
    async def ally_pick_class(self, class_id: Any = None, reason: Any = None, **_):
        cid = str(class_id or "").strip().lower()
        if cid not in brain.CLASSES:
            return {"summary": "无效职业 id，可选：" + "/".join(brain.CLASS_ORDER)}
        text = str(reason or "").strip()[:40] or f"我打{_class_name(cid)}"
        with self._ally_lock:
            if not self._ally.get("since"):
                return {"summary": "游戏里目前没有正在组建的 AI 伙伴，忽略这次调用。"}
            self._ally["class"] = cid
            self._ally["tree"] = brain.tech_tree(cid)
            self._ally["plan"] = []
            self._ally["reason"] = text
            self._ally_order.update({"stance": "follow", "focus": "nearest", "buy": [], "say": text,
                                     "at": time.time()})
        return {"summary": f"已选择 {brain.CLASSES[cid]['name']}"}

    @llm_tool(
        name="ally_order",
        description=(
            "给《无尽测试》里的 AI 伙伴下达下一步姿态。每次收到 [AI伙伴战术请求] 都必须调用一次。"
            "stance：follow 跟随 / engage 压上 / focus 集火 / guard 护主 / retreat 撤退 / free 自由发挥；"
            "focus：boss / elite / nearest / none；buy 是这次想买的研究节点 id（可留空，最多 3 个）；"
            "say 是伙伴战场气泡里的一句话（可留空）。只调用工具，不要说话。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "stance": {"type": "string", "enum": ["follow", "engage", "focus", "guard", "retreat", "free"]},
                "focus": {"type": "string", "enum": ["boss", "elite", "nearest", "none"]},
                "buy": {"type": "array", "items": {"type": "string"}, "description": "研究节点 id"},
                "cast": {"type": "string", "enum": ["none", "skill", "ult"],
                         "description": "要不要放大招：none 不放 / skill 放技能 / ult 放终技（冷却好了才有用）"},
                "say": {"type": "string", "description": "战场气泡台词"},
            },
            "required": ["stance"],
        },
        timeout=15.0,
    )
    async def ally_order(self, stance: Any = None, focus: Any = None,
                         buy: Any = None, say: Any = None, cast: Any = None, **_):
        st = self._ally
        if not st.get("class"):
            return {"summary": "游戏里没有在运行的 AI 伙伴，忽略这次调用。"}
        s = str(stance or "").strip().lower()
        if s not in self.ALLY_STANCES:
            return {"summary": "stance 无效，可选：" + "/".join(self.ALLY_STANCES)}
        f = str(focus or "nearest").strip().lower()
        if f not in self.ALLY_FOCUS:
            f = "nearest"
        ids = buy if isinstance(buy, list) else []
        valid = {n["id"] for n in st.get("tree", [])}
        ids = [str(i) for i in ids if str(i) in valid][:3]
        text = str(say or "").strip()[:34]
        c = str(cast or "none").strip().lower()
        if c not in ("none", "skill", "ult"):
            c = "none"
        with self._ally_lock:
            oid = int(self._ally_order.get("id") or 0) + 1
            self._ally_order = {"stance": s, "focus": f, "buy": ids, "say": text,
                                "cast": c, "id": oid, "at": time.time()}
        extra = f" / 买 {len(ids)} 项" if ids else ""
        if c != "none":
            extra += f" / 施放 {c}"
        return {"summary": f"已下达：{s} / {f}" + extra}

    # ---- 战场快照 → 提示 ----

    def _ally_digest(self) -> str:
        s = self._ally_snap or {}
        st = self._ally
        cls = st.get("class") or ""
        owned = set(self._ally_owned)
        avail = [n for n in st.get("tree", []) if n["id"] not in owned][:5]
        techs = "；".join(
            f"{n['id']}={n['name']}（{n['desc']}，{n['cost']}点）" for n in avail
        ) or "已买满"
        cur = self._ally_order
        return (
            f"【游戏系统 · AI伙伴】这是《无尽测试》发来的系统事件，不是主人在说话。\n"
            f"你现在操控的同伴是 {_class_name(cls)}，战斗进行到第 {int(s.get('t') or 0)} 秒。\n"
            f"技能：普通技能 {s.get('skillCd')}s 冷却 · 终技 {s.get('ultCd')}s 冷却（要不要放由你决定，用 cast 字段：none/skill/ult）。\n"
            f"局面：同伴HP {s.get('hp')}/{s.get('maxHp')}，质量 {s.get('mass')}/{s.get('massMax')}（攻击要耗质量），"
            f"主人HP {round(float(s.get('playerHpRatio') or 0) * 100)}%，"
            f"周围敌人 {s.get('enemies')}，Boss：{s.get('boss') or '无'}，研究点 {s.get('research')}。\n"
            f"当前姿态 {cur.get('stance')} / 集火 {cur.get('focus')}。可买节点：{techs}。\n"
            f"你现在唯一要做的事：调用 ally_order 工具，给出下一步姿态（可用 say 字段说一句）。\n"
            f"不要只用聊天文字回答，必须先调用工具；不要复述数据。"
        )

    def _ally_tick(self) -> None:
        now = time.time()
        if not self._ally.get("class"):
            return
        if not self._ally_snap or now - self._ally_snap_at > 12:
            return                     # 游戏没在跑，别打扰 Neko
        if now - self._ally_nudge_at < 8:
            return
        if now - float(self._ally_order.get("at") or 0) < 5:
            return
        self._ally_nudge_at = now
        pushed = self._push_ai(self._ally_digest(), "ally_tick")
        self.logger.info("ally: tick nudge pushed={}", pushed)

    def _ally_loop(self) -> None:
        while not self._ally_stop.is_set():
            try:
                self._ally_tick()
            except Exception as exc:
                self.logger.warning("ally loop: {}", exc)
            self._ally_stop.wait(1.0)

    # ---- 游戏调用的接口 ----

    def _handle_ally_start(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        branch = str(payload.get("playerBranch") or "") or "还没选"
        diff = str(payload.get("difficulty") or "D1")
        dbg = str(payload.get("debugClass") or "").strip().lower()
        if dbg in brain.CLASSES:
            # QA 用：不经过 Neko，直接指定职业，便于自动化验证执行层
            with self._ally_lock:
                self._ally = {"class": dbg, "tree": brain.tech_tree(dbg), "plan": [],
                              "reason": "我来支援", "since": time.time()}
                self._ally_owned = []
            cls0 = brain.CLASSES[dbg]
            return {"ok": True, "class": dbg, "name": cls0["name"], "color": cls0["color"],
                    "role": cls0["role"], "range": cls0["range"], "reason": "我来支援",
                    "tree": self._ally["tree"], "plan": [], "source": "debug"}
        classes = "\n".join(
            f"- {cid}: {brain.CLASSES[cid]['name']}（{brain.CLASSES[cid]['role']}）{brain.CLASSES[cid]['tag']}"
            for cid in brain.CLASS_ORDER
        )
        with self._ally_lock:
            self._ally = {"class": "", "tree": [], "plan": [], "reason": "", "since": time.time()}
            self._ally_order = {"stance": "follow", "focus": "nearest", "buy": [], "say": "",
                                "cast": "none", "id": 0, "at": 0.0}
            self._ally_owned = []
            self._ally_nudge_at = time.time() - 6
        prompt = (
            f"[AI伙伴组建] 主人要带 AI 伙伴一起打《无尽测试》。难度 {diff}，主人职业：{branch}。\n"
            f"从下面挑一个互补职业，调用 ally_pick_class 工具，reason 写一句话理由（会显示在游戏里）：\n{classes}"
        )
        self.logger.info("ally: ask Neko to pick a class")
        pushed = self._push_ai(prompt, "ally_start")
        self.logger.info("ally: start nudge pushed={}", pushed)
        deadline = time.time() + 18
        while time.time() < deadline and not self._ally.get("class"):
            time.sleep(.2)
        st = self._ally
        if not st.get("class"):
            return {"ok": False, "error": "Neko 还没选好职业", "retry": True}
        cls = brain.CLASSES[st["class"]]
        return {
            "ok": True, "class": st["class"], "name": cls["name"], "color": cls["color"],
            "role": cls["role"], "range": cls["range"], "reason": st["reason"],
            "tree": st["tree"], "plan": [], "source": "neko",
        }

    def _handle_ally_state(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        snap = payload.get("snap") if isinstance(payload.get("snap"), dict) else payload
        self._ally_snap = snap
        self._ally_snap_at = time.time()
        if isinstance(payload.get("owned"), list):
            self._ally_owned = [str(x) for x in payload["owned"]]
        if not self._ally.get("class"):
            return {"ok": False, "intent": "follow", "say": "", "buy": [], "source": "neko"}
        o = self._ally_order
        return {
            "ok": True, "source": "neko", "class": self._ally.get("class", ""),
            "intent": o.get("stance") or "follow", "focus": o.get("focus") or "nearest",
            "buy": list(o.get("buy") or []), "say": o.get("say") or "",
            "cast": o.get("cast") or "none", "orderId": int(o.get("id") or 0),
            "orderAt": float(o.get("at") or 0),
            "age": round(time.time() - float(o.get("at") or 0), 1),
        }

    def _handle_ally_command(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        text = str(payload.get("text") or "").strip()[:80]
        if not text:
            return {"ok": False, "reply": "没听清"}
        ok = self._push_ai(
            f"【游戏系统 · 主人的指挥】主人在《无尽测试》里对你说：{text}\n"
            f"这不是战术周期提示，优先处理。请调用 ally_order 更新姿态，并在 say 里回一句短话。",
            "ally_command",
            coalesce_key="ally_command",
            priority=10,
        )
        # 主人刚说过话，战术节流重置，避免紧接着又来一条系统请求
        self._ally_nudge_at = time.time()
        return {"ok": bool(ok), "heard": text, "reply": "", "source": "neko"}

    def _handle_ally_order_debug(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """本地调试/QA 用：绕过 Neko 直接注入一条指令。"""
        s = str(payload.get("stance") or "follow").lower()
        if s not in self.ALLY_STANCES:
            return {"ok": False, "error": "bad stance"}
        cast = str(payload.get("cast") or "none").lower()
        if cast not in ("none", "skill", "ult"):
            cast = "none"
        with self._ally_lock:
            self._ally_order = {
                "stance": s,
                "focus": str(payload.get("focus") or "nearest").lower(),
                "buy": payload.get("buy") if isinstance(payload.get("buy"), list) else [],
                "say": str(payload.get("say") or "")[:34],
                "cast": cast,
                "id": int(self._ally_order.get("id") or 0) + 1,
                "at": time.time(),
            }
        return {"ok": True, "order": dict(self._ally_order)}

    def _handle_quit(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """关掉由插件启动的全屏程序；游戏里的「退出游戏」会调这里。"""
        proc = self._window_proc
        if not proc or proc.poll() is not None:
            self._window_proc = None
            return {"ok": False, "error": "没有正在运行的全屏程序"}
        pid = proc.pid
        ok = False
        try:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                               capture_output=True, timeout=8)
                ok = True
            else:
                proc.terminate()
                try:
                    proc.wait(timeout=4)
                except Exception:
                    proc.kill()
                ok = True
        except Exception as exc:
            self.logger.warning("quit window failed: {}", exc)
        finally:
            self._window_proc = None
            self._session_release(self._session.get("token") or "")
        return {"ok": ok, "pid": pid}

    def _handle_report(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        state = self._read_state()
        runs = state.get("runs")
        if not isinstance(runs, list):
            runs = []
        run = {
            "at": _now(),
            "difficulty": str(payload.get("difficulty") or "D1")[:16],
            "seconds": int(payload.get("seconds") or 0),
            "kills": int(payload.get("kills") or 0),
            "branch": str(payload.get("branch") or "")[:24],
            "won": bool(payload.get("won")),
        }
        runs.append(run)
        state["runs"] = runs[-200:]
        self._write_state(state)
        return {"ok": True, "run": run}

    # ---------------- 生命周期 ----------------

    @lifecycle(id="startup")
    async def on_startup(self, **_):
        if not self.store.enabled:
            self.store.enabled = True
        try:
            self._start_http()
        except Exception as exc:
            self.logger.error("HTTP server failed: {}", exc)
        if not self._register_ui():
            self.logger.warning("static ui registration failed")
        self._read_state()
        # AI 伙伴：唤醒 Neko 的决策循环
        self._ally_stop.clear()
        if self._ally_thread is None or not self._ally_thread.is_alive():
            self._ally_thread = threading.Thread(target=self._ally_loop, name="endless-ally", daemon=True)
            self._ally_thread.start()
        self.logger.info("Endless Test ready at {}", self._url())
        return Ok({"status": "ready", "url": self._url(), "port": self._port})

    @lifecycle(id="shutdown")
    async def on_shutdown(self, **_):
        self._ally_stop.set()
        self._stop_http()
        self.logger.info("Endless Test stopped")
        return Ok({"status": "stopped"})

    # ---------------- 入口 ----------------

    @plugin_entry(
        id="launch_window",
        name="全屏窗口开局",
        description="以独立全屏窗口启动《无尽测试》，没有地址栏，像正常游戏程序。",
        input_schema={
            "type": "object",
            "properties": {
                "fullscreen": {"type": "boolean", "description": "是否全屏，默认 true"},
                "size": {"type": "string", "description": "窗口尺寸，如 1280,720"},
            },
        },
        llm_result_fields=["ok", "url", "message"],
    )
    async def launch_window(self, fullscreen: bool = True, size: str = "1280,720", **_):
        try:
            self._start_http()
        except Exception as exc:
            return Err(SdkError(f"游戏服务启动失败: {exc}"))
        browser = _find_browser()
        if not browser:
            return Err(SdkError("没找到 Edge/Chrome，无法启动全屏窗口"))
        args = [browser] + _window_args(self._url("window"), self._root, size)
        if fullscreen:
            args.append("--start-fullscreen")
        args += ["--no-first-run", "--no-default-browser-check",
                 f"--user-data-dir={self._root / '.browser-profile'}"]
        try:
            self._window_proc = subprocess.Popen(args, close_fds=True)
        except Exception as exc:
            return Err(SdkError(f"启动失败: {exc}"))
        return Ok({
            "ok": True,
            "url": self._url(),
            "message": f"已启动全屏窗口（{os.path.basename(browser)}）",
        })

    @plugin_entry(
        id="launch_panel",
        name="插件面板开局",
        description="返回插件面板地址，在 Neko 面板内游玩。",
        llm_result_fields=["ok", "url", "message"],
    )
    async def launch_panel(self, **_):
        try:
            self._start_http()
        except Exception as exc:
            return Err(SdkError(f"游戏服务启动失败: {exc}"))
        return Ok({
            "ok": True,
            "url": self._url(),
            "message": f"面板地址: {self._url()}",
        })

    @plugin_entry(
        id="get_state",
        name="读取存档数据",
        description="读取插件统一保存的全部游戏数据。",
        llm_result_fields=["ok", "updated_at", "keys", "state"],
    )
    async def get_state(self, **_):
        state = self._read_state()
        data = state.get("data") if isinstance(state.get("data"), dict) else {}
        return Ok({
            "ok": True,
            "updated_at": state.get("updated_at", ""),
            "keys": sorted(data.keys()),
            "state": state,
            "message": f"共 {len(data)} 项数据，更新于 {state.get('updated_at') or '从未'}",
        })

    @plugin_entry(
        id="put_state",
        name="写入存档数据",
        description="把游戏数据写回插件存储，按 key 合并。",
        input_schema={
            "type": "object",
            "properties": {
                "data": {"type": "object", "description": "键值对，值通常是 JSON 字符串"},
            },
            "required": ["data"],
        },
        llm_result_fields=["ok", "keys", "message"],
    )
    async def put_state(self, data: Optional[Dict[str, Any]] = None, **_):
        if not isinstance(data, dict):
            return Err(SdkError("data 必须是对象"))
        try:
            payload = self._merge_state(data)
            self._write_state(payload)
        except Exception as exc:
            return Err(SdkError(f"写入失败: {exc}"))
        return Ok({
            "ok": True,
            "keys": len(payload.get("data") or {}),
            "updated_at": payload.get("updated_at", ""),
            "message": f"已保存 {len(data)} 项",
        })

    @plugin_entry(
        id="get_profile",
        name="查看玩家档案",
        description="汇总玩家进度：通关次数、击杀、最近与最佳一局、职业等级。",
        llm_result_fields=["ok", "profile", "message"],
    )
    async def get_profile(self, **_):
        profile = self._profile()
        last = profile.get("last_run") or {}
        message = (
            f"通关 {profile['clears']} 次，累计击杀 {profile['kills']}，"
            f"记录 {profile['runs_recorded']} 局"
        )
        if last:
            message += f"，最近一局 {last.get('difficulty', '?')} 存活 {last.get('seconds', 0)} 秒"
        return Ok({"ok": True, "profile": profile, "message": message})

    @plugin_entry(
        id="report_run",
        name="记录一局战报",
        description="记录一局的难度、时长、击杀、职业与结果。",
        input_schema={
            "type": "object",
            "properties": {
                "difficulty": {"type": "string", "description": "难度，如 D3"},
                "seconds": {"type": "number", "description": "存活秒数"},
                "kills": {"type": "number", "description": "击杀数"},
                "branch": {"type": "string", "description": "职业，如 blade"},
                "won": {"type": "boolean", "description": "是否通关"},
                "detail": {"type": "object", "description": "其他附加信息"},
            },
            "required": ["seconds"],
        },
        llm_result_fields=["ok", "run", "message"],
    )
    async def report_run(
        self,
        seconds: float = 0,
        difficulty: str = "",
        kills: int = 0,
        branch: str = "",
        won: bool = False,
        detail: Optional[Dict[str, Any]] = None,
        **_,
    ):
        state = self._read_state()
        runs = state.get("runs")
        if not isinstance(runs, list):
            runs = []
        run = {
            "at": _now(),
            "difficulty": difficulty or "D1",
            "seconds": int(seconds or 0),
            "kills": int(kills or 0),
            "branch": branch or "",
            "won": bool(won),
        }
        if isinstance(detail, dict):
            run["detail"] = detail
        runs.append(run)
        state["runs"] = runs[-200:]
        try:
            self._write_state(state)
        except Exception as exc:
            return Err(SdkError(f"记录失败: {exc}"))
        return Ok({"ok": True, "run": run, "message": f"已记录：{run['difficulty']} {run['seconds']} 秒"})

    @plugin_entry(
        id="reset_state",
        name="重置全部数据",
        description="清空插件里保存的所有《无尽测试》数据。",
        input_schema={
            "type": "object",
            "properties": {
                "confirm": {"type": "boolean", "description": "必须为 true 才会执行"},
            },
            "required": ["confirm"],
        },
        llm_result_fields=["ok", "message"],
    )
    async def reset_state(self, confirm: bool = False, **_):
        if not confirm:
            return Err(SdkError("需要 confirm=true 才会清空数据"))
        try:
            self._write_state({"v": STATE_VERSION, "updated_at": _now(), "data": {}, "runs": []})
        except Exception as exc:
            return Err(SdkError(f"重置失败: {exc}"))
        return Ok({"ok": True, "message": "已清空全部数据"})

    @plugin_entry(
        id="status",
        name="查看服务状态",
        description="查看游戏服务端口、静态页面与数据文件状态。",
        llm_result_fields=["ok", "url", "serving", "has_game_file", "state_file", "message"],
    )
    async def status(self, **_):
        game = self._game_file()
        state = self._resolve_state_path()
        serving = bool(self._http) and bool(self._thread and self._thread.is_alive())
        return Ok({
            "ok": True,
            "url": self._url() if serving else "",
            "serving": serving,
            "has_game_file": game.is_file(),
            "state_file": str(state),
            "state_size": state.stat().st_size if state.is_file() else 0,
            "message": (
                f"服务{'运行中' if serving else '未启动'} · "
                f"页面{'已就绪' if game.is_file() else '缺失'} · "
                f"数据{'已存在' if state.is_file() else '尚未创建'}"
            ),
        })
