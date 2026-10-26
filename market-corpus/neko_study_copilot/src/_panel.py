"""面板服务：本地 127.0.0.1 HTTP + 跨线程异步桥。

面板跑在独立线程里（同步），但很多动作（出题、诊断、疏导）必须调用大模型，
那些是 async 的。这里用 :class:`AsyncBridge` 把协程丢回插件的主事件循环执行，
与 ``neko_game_copilot/_panel.py`` 的做法一致。

**为什么不用 ``http.server``**：宿主是 PyInstaller 冻结进程，标准库里只打包它
自己引用过的模块，``http.server`` 属于"很可能会缺"的那一类（已实证
``urllib.robotparser`` 就是缺的）。而它的缺失会连累整个插件 import 失败，
所以这里只用 ``socket`` 手写一个够用的小服务器：GET/POST/OPTIONS + JSON。
"""

from __future__ import annotations

import asyncio
import json
import socket
import threading
from pathlib import Path
from typing import Any, Callable, Optional

_MAX_HEAD = 64 * 1024
_MAX_BODY = 16 * 1024 * 1024

_MIME = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".ico": "image/x-icon",
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".ogg": "audio/ogg",
    ".wav": "audio/wav",
    ".flac": "audio/flac",
    ".woff2": "font/woff2",
    ".woff": "font/woff",
    ".ttf": "font/ttf",
    ".txt": "text/plain; charset=utf-8",
}


def guess_mime(name: str) -> str:
    """按扩展名给静态资源定 MIME。宿主托管时用不到，插件端口托管时靠它。"""
    return _MIME.get(Path(name).suffix.lower(), "application/octet-stream")


class PanelServer:
    """极简 HTTP/1.1 服务器：只为插件面板服务，不做通用用途。"""

    def __init__(
        self,
        port: int,
        html_provider: Callable[[], str],
        endpoints: dict[tuple[str, str], Callable[[dict[str, Any]], dict[str, Any]]],
        static_resolver: Optional[Callable[[str], Optional[tuple[bytes, str]]]] = None,
    ):
        self.port = int(port)
        self._html_provider = html_provider
        self._endpoints = endpoints
        # 静态资源解析器：(相对路径) -> (bytes, mime)；页面被插件自己的端口托管时
        # 背景图 / 字体都靠它，否则会 404（宿主托管时才由宿主负责）
        self._static = static_resolver
        self._sock: Optional[socket.socket] = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

    # ── 生命周期 ──────────────────────────────────────────────
    def start(self) -> bool:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("127.0.0.1", self.port))
            sock.listen(32)
        except OSError:
            return False
        self._sock = sock
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._serve, daemon=True, name=f"study-copilot-panel-{self.port}"
        )
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()
        sock = self._sock
        self._sock = None
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass

    def _serve(self) -> None:
        while not self._stop.is_set():
            sock = self._sock
            if sock is None:
                break
            try:
                conn, _addr = sock.accept()
            except OSError:
                break
            threading.Thread(target=self._handle_conn, args=(conn,), daemon=True).start()

    # ── 单条连接 ──────────────────────────────────────────────
    def _handle_conn(self, conn: socket.socket) -> None:
        try:
            conn.settimeout(60.0)
            head, rest = self._read_head(conn)
            if head is None:
                return
            method, target, headers = head
            length = 0
            try:
                length = int(headers.get("content-length") or 0)
            except ValueError:
                length = 0
            if length > _MAX_BODY:  # 面板只收发 JSON，超限直接拒掉
                self._write(conn, 413, self._json({"error": "请求体过大"}), "application/json; charset=utf-8")
                return
            body = rest[:length] if length > 0 else b""
            while length > 0 and len(body) < length:
                chunk = conn.recv(min(65536, length - len(body)))
                if not chunk:
                    break
                body += chunk
            route = target.split("?", 1)[0]
            status, payload, ctype = self._route(method, route, body)
            self._write(conn, status, payload, ctype)
        except Exception:
            pass
        finally:
            try:
                conn.close()
            except OSError:
                pass

    @staticmethod
    def _read_head(conn: socket.socket) -> tuple[Optional[tuple[str, str, dict[str, str]]], bytes]:
        buf = b""
        while b"\r\n\r\n" not in buf:
            try:
                chunk = conn.recv(65536)
            except OSError:
                return None, b""
            if not chunk:
                return None, b""
            buf += chunk
            if len(buf) > _MAX_HEAD:
                return None, b""
        head, _, rest = buf.partition(b"\r\n\r\n")
        lines = head.decode("iso-8859-1", errors="replace").split("\r\n")
        if not lines or not lines[0]:
            return None, b""
        parts = lines[0].split()
        method = (parts[0] if parts else "GET").upper()
        target = parts[1] if len(parts) > 1 else "/"
        headers: dict[str, str] = {}
        for line in lines[1:]:
            if ":" in line:
                key, value = line.split(":", 1)
                headers[key.strip().lower()] = value.strip()
        return (method, target, headers), rest

    # ── 路由 ──────────────────────────────────────────────────
    def _route(self, method: str, route: str, body: bytes) -> tuple[int, bytes, str]:
        if method == "OPTIONS":
            return 204, b"", "application/json; charset=utf-8"
        if method == "GET" and route in ("/", "/index.html"):
            try:
                html = self._html_provider()
            except Exception as exc:
                return 500, self._json({"error": str(exc)}), "application/json; charset=utf-8"
            return 200, html.encode("utf-8"), "text/html; charset=utf-8"
        fn = self._endpoints.get((method, route))
        if fn is None:
            if method == "GET" and self._static is not None:
                try:
                    hit = self._static(route.lstrip("/"))
                except Exception:
                    hit = None
                if hit is not None:
                    return 200, hit[0], hit[1]
            return 404, self._json({"error": f"未知接口 {method} {route}"}), "application/json; charset=utf-8"
        payload: dict[str, Any] = {}
        if body:
            try:
                parsed = json.loads(body.decode("utf-8", "replace"))
                if isinstance(parsed, dict):
                    payload = parsed
            except (json.JSONDecodeError, UnicodeDecodeError):
                payload = {}
        try:
            result = fn(payload)
        except Exception as exc:
            return 200, self._json({"ok": False, "error": str(exc)}), "application/json; charset=utf-8"
        if not isinstance(result, dict):
            result = {"ok": True, "result": result}
        return 200, self._json(result), "application/json; charset=utf-8"

    @staticmethod
    def _json(payload: dict[str, Any]) -> bytes:
        return json.dumps(payload, ensure_ascii=False).encode("utf-8")

    @staticmethod
    def _cache_header(ctype: str) -> str:
        """图片与字体可以放心缓存：否则每开一次面板都要重下几 MB 的背景图。"""
        head = (ctype or "").split(";")[0].strip().lower()
        if head.startswith(("image/", "font/")) or head in ("application/font-woff",):
            return "public, max-age=86400"
        return "no-store"

    @staticmethod
    def _write(conn: socket.socket, status: int, body: bytes, ctype: str) -> None:
        reason = {
            200: "OK",
            204: "No Content",
            404: "Not Found",
            413: "Payload Too Large",
            500: "Internal Server Error",
        }.get(status, "OK")
        head = (
            f"HTTP/1.1 {status} {reason}\r\n"
            f"Content-Type: {ctype}\r\n"
            f"Content-Length: {len(body)}\r\n"
            "Access-Control-Allow-Origin: *\r\n"
            "Access-Control-Allow-Methods: GET, POST, OPTIONS\r\n"
            "Access-Control-Allow-Headers: Content-Type\r\n"
            f"Cache-Control: {PanelServer._cache_header(ctype)}\r\n"
            "Connection: close\r\n"
            "\r\n"
        )
        conn.sendall(head.encode("ascii") + body)


def find_open_port(preferred: int, attempts: int = 12) -> int:
    import socket

    for offset in range(attempts):
        candidate = preferred + offset
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind(("127.0.0.1", candidate))
                return candidate
            except OSError:
                continue
    return preferred


class AsyncBridge:
    """把协程从面板线程安全地丢回一个**活着**的事件循环。

    为什么不能简单存下 ``asyncio.get_running_loop()``：实测宿主会把
    ``@lifecycle(id="startup")`` 跑在一个临时事件循环里，跑完就关掉。这时
    ``self._loop`` 立刻变成已关闭状态，插件本体明明活着（面板页面、HTTP 服务
    都正常），面板每个请求却只能回「插件尚未就绪」——非常容易误判成插件没启动。

    所以这里做三级兜底：

    1. 宿主给的循环还活着 → 用它（正常路径，与宿主 entry 共用一条循环）；
    2. 宿主循环已关闭 / 从未给过 → 自己起常驻后台线程养一个**私有循环**；
    3. 连私有循环都建不出来 → 明确报错，绝不静默失败。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._loop_note = "尚未绑定"
        self._fallback: Optional[asyncio.AbstractEventLoop] = None
        self._fallback_thread: Optional[threading.Thread] = None
        self._notify: Optional[Callable[[str], None]] = None

    # ── 绑定 ──────────────────────────────────────────────────
    def bind(self, loop: asyncio.AbstractEventLoop) -> None:
        with self._lock:
            self._loop = loop
            self._loop_note = f"宿主循环 id={id(loop)}"

    def bind_if_dead(self, loop: asyncio.AbstractEventLoop) -> bool:
        """宿主在别的循环里调 entry 时跟着换过去；没绑或已关闭才换。"""
        with self._lock:
            if self._loop is not None and not self._loop.is_closed():
                return False
            self._loop = loop
            self._loop_note = f"宿主循环 id={id(loop)}（运行中自动重绑）"
            return True

    def describe(self) -> str:
        with self._lock:
            loop = self._loop
            note = self._loop_note
        if loop is None:
            state = "未绑定"
        elif loop.is_closed():
            state = "已关闭"
        else:
            state = "运行中"
        own = self._fallback
        own_state = "无" if own is None else ("已关闭" if own.is_closed() else "运行中")
        return f"{note}（{state}）｜私有循环：{own_state}"

    def ready(self) -> bool:
        return self._pick_loop() is not None

    # ── 调度 ──────────────────────────────────────────────────
    def call(self, factory: Callable[[], Any], timeout: float = 90.0) -> Any:
        loop = self._pick_loop()
        if loop is None:
            raise RuntimeError(
                f"插件事件循环不可用（{self.describe()}），请把插件重启一次；"
                "如果反复出现，请把这句话截图给开发者。"
            )
        future = asyncio.run_coroutine_threadsafe(factory(), loop)
        return future.result(timeout)

    def _pick_loop(self) -> Optional[asyncio.AbstractEventLoop]:
        with self._lock:
            loop = self._loop
        if loop is not None and not loop.is_closed():
            return loop
        return self._ensure_fallback()

    def set_notifier(self, notify: Callable[[str], None]) -> None:
        """接一个日志回调，兜底发生时留下痕迹（否则这条路径会静默）。"""
        self._notify = notify

    def _ensure_fallback(self) -> Optional[asyncio.AbstractEventLoop]:
        created = False
        with self._lock:
            loop = self._fallback
            if loop is not None and not loop.is_closed():
                return loop
            try:
                loop = asyncio.new_event_loop()
            except Exception:
                loop = None
            if loop is not None:
                self._fallback = loop
                thread = threading.Thread(
                    target=self._run_fallback,
                    args=(loop,),
                    daemon=True,
                    name="study-copilot-private-loop",
                )
                self._fallback_thread = thread
                created = True
        if loop is None:
            return None
        if created:
            thread.start()
            self._notify_log(
                "[study_copilot] 宿主事件循环不可用（%s），已启用插件私有事件循环兜底",
                self.describe(),
            )
        return loop

    def _notify_log(self, fmt: str, *args: Any) -> None:
        notify = self._notify
        if notify is None:
            return
        try:
            notify(fmt % args if args else fmt)
        except Exception:
            pass

    @staticmethod
    def _run_fallback(loop: asyncio.AbstractEventLoop) -> None:
        asyncio.set_event_loop(loop)
        try:
            loop.run_forever()
        except Exception:
            pass
