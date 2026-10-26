import json
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .host_layout import HostLayout
from .topup import open_official


def start_server(monitor, port=48923, request_reply=None, agent_status=None, recording=None, takeover=None, rhythm=None, companion=None, official_opener=None, application_opener=None):
    csrf = secrets.token_urlsafe(32)
    host_layout = HostLayout()
    reply_lock = threading.Lock()
    last_reply = [None]

    class LocalServer(ThreadingHTTPServer):
        def server_bind(self):
            # HTTPServer normally does a blocking reverse-DNS lookup during bind.
            # A loopback-only widget needs no DNS and must start inside NEKO's deadline.
            import socketserver

            socketserver.TCPServer.server_bind(self)
            self.server_name = "127.0.0.1"
            self.server_port = self.server_address[1]

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send(self, status, payload, content_type="application/json; charset=utf-8"):
            raw = (
                payload
                if isinstance(payload, bytes)
                else payload.encode()
                if isinstance(payload, str)
                else json.dumps(payload, ensure_ascii=False).encode()
            )
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'",
            )
            self.end_headers()
            self.wfile.write(raw)

        def allowed(self):
            expected = f"127.0.0.1:{self.server.server_port}"
            return (
                self.headers.get("Host") == expected
                and self.headers.get("Origin", "http://" + expected) == "http://" + expected
            )

        def do_GET(self):
            if not self.allowed():
                return self.send(403, {"error": "仅允许本机同源访问"})
            path = self.path.split("?")[0]
            if path == "/api/host-layout":
                return self.send(200, host_layout.snapshot())
            if path == "/api/status":
                return self.send(200, dict(monitor.snapshot(), csrf=csrf))
            if path == '/api/allowance':
                query = parse_qs(urlsplit(self.path).query, keep_blank_values=True)
                if set(query) - {'profile'} or any(len(v) != 1 or not v[0] for v in query.values()):
                    return self.send(400, {'error': '额度档案参数无效'})
                if not hasattr(monitor, 'allowance_history'):
                    return self.send(404, {'error': '此宿主尚未提供读数记录'})
                try:
                    return self.send(200, monitor.allowance_history(query.get('profile', [None])[0]))
                except ValueError as exc:
                    return self.send(400, {'error': str(exc)})
            if path == "/api/tracking":
                return self.send(200, monitor.tracking())
            if path == "/api/agent":
                return self.send(200, agent_status.snapshot() if agent_status else {"status": "unknown"})
            if path == "/api/diary":
                return self.send(200, recording.diary() if recording and hasattr(recording, "diary") else {"entries": [], "seconds": 0, "pet": 0, "ask": 0})
            if path == "/api/rhythm":
                return self.send(200, rhythm.snapshot() if rhythm else {"enabled": False, "active": False, "supported": False})
            if path == "/api/care":
                return self.send(200, companion.snapshot() if companion else {"enabled": False, "voice": False, "interval": 300, "character_name": "YUI", "background": False})
            names = {
                "/": ("index.html", "text/html; charset=utf-8"),
                "/widget": ("widget.html", "text/html; charset=utf-8"),
                "/settings.js": ("settings.js", "text/javascript; charset=utf-8"),
                "/topup.js": ("topup.js", "text/javascript; charset=utf-8"),
                "/quota-mood.js": ("quota-mood.js", "text/javascript; charset=utf-8"),
                "/quota-display.js": ("quota-display.js", "text/javascript; charset=utf-8"),
                "/food-bags.js": ("food-bags.js", "text/javascript; charset=utf-8"),
                "/food-bags.css": ("food-bags.css", "text/css; charset=utf-8"),
                "/chase-motion.js": ("chase-motion.js", "text/javascript; charset=utf-8"),
                "/chase-stage.js": ("chase-stage.js", "text/javascript; charset=utf-8"),
                "/whale-clips.js": ("whale-clips.js", "text/javascript; charset=utf-8"),
                "/whale-animation.js": ("whale-animation.js", "text/javascript; charset=utf-8"),
                "/chase-game.js": ("chase-game.js", "text/javascript; charset=utf-8"),
                "/chase-play.js": ("chase-play.js", "text/javascript; charset=utf-8"),
                "/care.js": ("care.js", "text/javascript; charset=utf-8"),
                "/quiet.js": ("quiet.js", "text/javascript; charset=utf-8"),
                "/diary.js": ("diary.js", "text/javascript; charset=utf-8"),
                "/notice": ("notice.html", "text/html; charset=utf-8"),
                "/quiet.css": ("quiet.css", "text/css; charset=utf-8"),
                "/chase-window.js": ("chase-window.js", "text/javascript; charset=utf-8"),
                "/quota-reactions.js": ("quota-reactions.js", "text/javascript; charset=utf-8"),
                "/chase.css": ("chase.css", "text/css; charset=utf-8"),
                "/chase": ("chase.html", "text/html; charset=utf-8"),
                "/chase/neko-white-cat-idle.gif": ("chase/neko-white-cat-idle.gif", "image/gif"),
                "/chase/neko-white-cat-move.gif": ("chase/neko-white-cat-move.gif", "image/gif"),
                "/chase/neko-white-cat-walk.gif": ("chase/neko-white-cat-walk.gif", "image/gif"),
                "/chase/neko-white-cat-react.gif": ("chase/neko-white-cat-react.gif", "image/gif"),
                "/chase/whale-maid.png": ("chase/whale-maid.png", "image/png"),
                "/chase/dsh-whale-run.png": ("chase/dsh-whale-run.png", "image/png"),
                "/chase/dsh-whale-shock.png": ("chase/dsh-whale-shock.png", "image/png"),
                "/chase/dsh-whale-angry.png": ("chase/dsh-whale-angry.png", "image/png"),
                "/chase/dsh-whale-hat.png": ("chase/dsh-whale-hat.png", "image/png"),
                "/chase/dsh-whale-hat-run.png": ("chase/dsh-whale-hat-run.png", "image/png"),
                "/chase/yui-dsh-style-run-12.png": ("chase/yui-dsh-style-run-12.png", "image/png"),
                "/chase/yui-hit.png": ("chase/yui-hit.png", "image/png"),
                "/chase/yui-dsh-style-run.png": ("chase/yui-dsh-style-run.png", "image/png"),
                "/chase/whale-maid-frightened-run.png": ("chase/whale-maid-frightened-run.png", "image/png"),
                "/chase/whale-maid-tiger-run.png": ("chase/whale-maid-tiger-run.png", "image/png"),
                "/chase/yui-frightened-run.png": ("chase/yui-frightened-run.png", "image/png"),
                "/settings.css": ("settings.css", "text/css; charset=utf-8"),
                "/account-page.js": ("account-page.js", "text/javascript; charset=utf-8"),
                "/rhythm.js": ("rhythm.js", "text/javascript; charset=utf-8"),
                "/rhythm-motion.js": ("rhythm-motion.js", "text/javascript; charset=utf-8"),
                "/widget.js": ("widget.js", "text/javascript; charset=utf-8"),
                "/widget.css": ("widget.css", "text/css; charset=utf-8"),
                "/outfits.js": ("outfits.js", "text/javascript; charset=utf-8"),
                "/yui-chibi.png": ("yui-chibi.png", "image/png"),
                "/app.js": ("app.js", "text/javascript; charset=utf-8"),
            }
            names["/brands/workbuddy.svg"] = ("brands/workbuddy.svg", "image/svg+xml")
            for brand in ("codex", "gpt", "claude", "deepseek", "trae", "gemini", "kimi", "qwen"):
                names[f"/brands/{brand}.png"] = (f"brands/{brand}.png", "image/png")
            for outfit in ("gpt", "claude", "deepseek", "gemini", "glm", "kimi", "qwen", "doubao", "jev"):
                names[f"/outfits/{outfit}.png"] = (f"outfits/{outfit}.png", "image/png")
            if path not in names:
                return self.send(404, {"error": "Not found"})
            name, content_type = names[path]
            self.send(200, (Path(__file__).parent / "static" / name).read_bytes(), content_type)

        def do_POST(self):
            if not self.allowed() or not secrets.compare_digest(self.headers.get("X-Neko-CSRF", ""), csrf):
                return self.send(403, {"error": "请重新打开本机面板"})
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 4096:
                    return self.send(413, {"error": "请求过大或为空"})
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict):
                    raise ValueError()
                if self.path == "/api/topup":
                    if set(body) - {"profile", "target"}:
                        raise ValueError("只接受平台与官方入口编号")
                    result = open_official(body.get("profile"), body.get("target"), official_opener, application_opener)
                    return self.send(200 if result["requested"] else 503, result)
                if self.path == "/api/rhythm":
                    if rhythm is None:
                        return self.send(503, {"error": "本地 BPM 模块尚未连接"})
                    return self.send(200, rhythm.action(body))
                if self.path == "/api/recording":
                    if body.get("action") == "game" and companion:
                        return self.send(200, companion.handle_game(body.get("kind"), body.get("event_id"), body.get("session_id")))
                    result = (
                        recording.action(
                            body.get("action", "status"), body.get("event_id"), body.get("session_id"),
                            body.get("kind"), body.get("message"),
                            **({"at": body["at"]} if "at" in body else {}),
                        ) if recording else {"active": False}
                    )
                    if takeover and result.get("active"):
                        takeover.begin(result["id"])
                    return self.send(200, result)
                if self.path == "/api/care":
                    if companion is None:
                        return self.send(503, {"error": "YUI 提醒服务尚未启动"})
                    if "action" in body:
                        if set(body) != {"action", "notice_id"}:
                            raise ValueError("提醒操作无效")
                        try:
                            return self.send(200, companion.respond(body["action"], body["notice_id"]))
                        except ValueError as error:
                            return self.send(400, {"error": str(error)})
                    return self.send(200, companion.configure(body))
                if self.path == "/api/takeover":
                    if takeover is None or not isinstance(body.get("session_id"), str) or type(body.get("ok")) is not bool:
                        return self.send(400, {"error": "接管状态无效"})
                    state = recording.snapshot() if recording else {}
                    # Native restoration writes an end marker before this HTTP
                    # request may arrive. Keep the last session's failure reason
                    # so its waiter can finish; never accept success after exit
                    # or let an older session complete a newer attempt.
                    if state.get("id") != body["session_id"] or (body["ok"] and not state.get("active")):
                        return self.send(200, {"accepted": False})
                    takeover.begin(state["id"])
                    accepted = takeover.report(body["session_id"], body["ok"], body.get("reason"))
                    if accepted and body["ok"] and hasattr(recording, "mark_presence"):
                        recording.mark_presence(body["session_id"])
                    return self.send(200, {"accepted": accepted})
                if self.path == "/api/usage":
                    return self.send(200, monitor.record_usage(body))
                if self.path == "/api/select":
                    return self.send(200, monitor.select(body.get("profile")))
                if self.path == "/api/tracking":
                    return self.send(200, monitor.configure_tracking(body))
                if self.path == "/api/config":
                    monitor.configure(body)
                    return self.send(200, monitor.refresh(profile=body.get("profile")) if hasattr(monitor, "tracking") else monitor.refresh())
                if self.path == "/api/refresh":
                    return self.send(200, monitor.refresh())
                if self.path == "/api/reply":
                    if request_reply is None:
                        return self.send(503, {"error": "原软件回应通道尚未连接"})
                    with reply_lock:
                        if last_reply[0] is not None and time.monotonic() - last_reply[0] < 30:
                            return self.send(429, {"error": "本喵刚收到啦，等一会再叫我喵～"})
                        last_reply[0] = time.monotonic()
                        line = body.get("line")
                        if line is not None and (not isinstance(line, str) or not 1 <= len(line) <= 140):
                            return self.send(400, {"error": "台词格式无效"})
                        result = request_reply(line) if line else request_reply()
                    return self.send(200 if result.get("submitted") else 503, result)
                return self.send(404, {"error": "Not found"})
            except (ValueError, TypeError, AttributeError):
                return self.send(400, {"error": "设置无效：请检查密钥、币种及数字范围"})
            except Exception:
                return self.send(500, {"error": "保存失败，请检查本机数据目录权限"})

    server = LocalServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread
