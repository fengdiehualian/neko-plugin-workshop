"""bili_viewer 网页接入面板：让 YUI / 用户通过浏览器网页完成浏览器接入与 B站绑定。

本地 HTTP 服务（127.0.0.1，零第三方依赖），懒启动（首次调用 bili_bind_web 时）。
页面上显示接入/绑定状态，并提供“一键接入”按钮：用户点击即授权插件尝试以
调试模式启动浏览器并读取 B站登录态——完全绕开本地进程状态判断，
残留进程（crashpad/后台 chrome.exe 等）不再阻挡接入。
"""

from __future__ import annotations

import collections
import json
import logging
import os
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_logger = logging.getLogger("N.E.K.O.Plugin_bili_viewer.plugin.bili_viewer")

_PANEL_HOST = "127.0.0.1"
_PANEL_PORT = 8765

_EXT_ONLINE_WINDOW = 120.0  # 扩展最近一次推送超过该秒数视为离线
_PANEL_URL = f"http://{_PANEL_HOST}:{_PANEL_PORT}/"

# 扩展通道请求体上限（字节）：截屏 base64（4K 屏 JPEG≈几 MB）与页面 DOM 实况
# 远大于默认 64KB，按端点分别放宽；同时防止无上限的磁盘/内存滥用。
_EXT_SHOT_MAX_BYTES = 16 * 1024 * 1024
_EXT_AUDIO_MAX_BYTES = 8 * 1024 * 1024  # 扩展声音分段（wav base64）上限：8s/16k/16bit 约 256KB，放宽防丢段
_EXT_DOM_MAX_BYTES = 2 * 1024 * 1024

# v0.2.17：扩展采集的直播弹幕缓冲 + @小号通知（YUI 实时看到弹幕、被@时提醒）
# v0.8.6：缓冲条目带房间号 (sender, text, at, room)——供"直播间活跃观众"提取
_DANMAKU_BUF: collections.deque = collections.deque(maxlen=120)  # (sender, text, at, room)
_MENTION_NOTIFY: dict = {"at": 0.0, "text": "", "sender": "", "room": ""}
_YUI_NICKNAMES = ("yui小猫娘", "小猫娘", "yui")  # 弹幕 @ 匹配昵称（小号默认昵称）

# v0.7.35：扩展指令队列——插件工具把"进入直播间"等命令写入，扩展 alarm 轮询拉取执行
_CMD_QUEUE: list = []          # [{id, type, room, created}]
_CMD_SEQ: int = 0


# v0.2.18：扩展推送的登录态按 uid 自保鲜（框架仅首次落盘，之后登录态变化时插件仍读旧文件
# 会 AUTH_FAILED——"绑定账号老是掉"根因之一）。只保留 B站 请求必需的核心字段，稳定写入。
_COOKIE_DIR = os.path.dirname(os.path.abspath(__file__))
_COOKIE_FILES = ("main_cookie.txt", "yui_cookie.txt")
_COOKIE_KEEP = ("SESSDATA=", "bili_jct=", "DedeUserID=", "DedeUserName=", "bili_ticket=", "buvid3=", "buvid4=")


def _core_cookie(cookie: str) -> str:
    """只保留 B站 身份/凭证核心字段，丢弃页面临时 cookie，避免不同页面推送的
    字段集合差异互相污染 cookie 文件（日志里 816B/846B 跳变的来源）。"""
    if not cookie:
        return ""
    kept = [p for p in cookie.split("; ") if p and any(p.startswith(k) for k in _COOKIE_KEEP)]
    return "; ".join(kept)


def _refresh_ext_cookie(cookie: str) -> dict:
    """扩展推送的有效登录态按 uid 刷新对应 cookie 文件（原子写）。

    命中规则：role.json 声明的角色 uid 优先；其次按现有 cookie 文件里 DedeUserID 匹配；
    全新 uid（两个文件都没有）不动——留给框架首次落盘逻辑。返回 {"uid","file","updated"}。
    """
    if not cookie or "SESSDATA=" not in cookie:
        return {"updated": False}
    m = re.search(r"DedeUserID=(\d+)", cookie)
    if not m:
        return {"updated": False}
    uid = m.group(1)
    core = _core_cookie(cookie)
    if not core or "SESSDATA=" not in core:
        return {"updated": False}
    target = None
    try:
        roles = json.load(open(os.path.join(_COOKIE_DIR, "role.json"), encoding="utf-8")) or {}
        if str(roles.get(uid)) == "yui":
            target = "yui_cookie.txt"
        elif str(roles.get(uid)) == "main":
            target = "main_cookie.txt"
    except (OSError, ValueError):
        roles = {}
    if not target:
        for fn in _COOKIE_FILES:
            p = os.path.join(_COOKIE_DIR, fn)
            try:
                if f"DedeUserID={uid}" in open(p, encoding="utf-8", errors="replace").read():
                    target = fn
                    break
            except OSError:
                continue
    if not target:
        return {"updated": False}
    dst = os.path.join(_COOKIE_DIR, target)
    tmp = dst + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(core)
        os.replace(tmp, dst)
        _logger.info("ext cookie refreshed: uid=%s file=%s len=%d", uid, target, len(core))
        return {"updated": True, "uid": uid, "file": target}
    except OSError as exc:
        _logger.warning("ext cookie refresh failed: %s", exc)
        return {"updated": False}


def get_recent_danmaku(limit: int = 40) -> list:
    """最近弹幕（供 __init__.py 实时读取）：[(sender, text, at), ...]"""
    return [(s, t, a) for s, t, a, _r in list(_DANMAKU_BUF)[-limit:]]


def get_recent_room(max_age: float = _EXT_ONLINE_WINDOW) -> str:
    """弹幕 WS 流最近的房间号（v0.8.6：页面真实直播房间的权威来源）。

    WS 弹幕连接绑定页面实际直播间，每来一条弹幕就刷新一条带 room 的记录——
    比"扩展标签页事件"可靠（后者只在切换/加载标签时推送一次，长时间停留会过期，
    感知回退窗口标题搜索就可能定位错房间）。返回 '' 表示没有新鲜弹幕房间。
    """
    now = time.time()
    for _s, _t, at, room in reversed(_DANMAKU_BUF):
        r = str(room or "").strip()
        if r and now - float(at or 0) <= max_age:
            return r
    return ""


def get_recent_viewers(room: str = "", limit: int = 50) -> list[str]:
    """最近弹幕发言者昵称去重（v0.8.6：直播间活跃观众近似来源）。

    B站 无公开 HTTP 接口返回"当前在线观众"（页面数字来自 WS），
    弹幕发言者是最可靠的可关注对象。room 非空时只统计该房间；
    排除空昵称与系统提示。按最近发言顺序（新→旧）去重。
    """
    seen: set[str] = set()
    out: list[str] = []
    room = (room or "").strip()
    for sender, _t, _a, r in reversed(_DANMAKU_BUF):
        s = str(sender or "").strip()
        if not s or s == "系统提示" or s == "系统":
            continue
        if room and str(r or "").strip() != room:
            continue
        if s in seen:
            continue
        seen.add(s)
        out.append(s)
        if len(out) >= limit:
            break
    return out


def get_mention_notify(clear: bool = True) -> dict:
    """最近一条 @小号 的通知（供感知循环读取后消费）"""
    info = dict(_MENTION_NOTIFY)
    if clear:
        _MENTION_NOTIFY["text"] = ""
        _MENTION_NOTIFY["sender"] = ""
        _MENTION_NOTIFY["room"] = ""
    return info

_PAGE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>B站接入面板</title>
<style>
  body{font-family:'Microsoft YaHei',sans-serif;max-width:720px;margin:40px auto;padding:0 20px;background:#1a1a2e;color:#eee}
  h1{color:#8ecae6} .card{background:#232345;border-radius:12px;padding:20px;margin:16px 0}
  .row{display:flex;justify-content:space-between;align-items:center;padding:8px 0;border-bottom:1px solid #333}
  .row:last-child{border-bottom:none}
  .ok{color:#7ae582;font-weight:bold} .warn{color:#ffd166} .bad{color:#ef476f;font-weight:bold}
  button{background:#3a86ff;color:#fff;border:none;border-radius:8px;padding:12px 24px;font-size:16px;cursor:pointer;margin-top:8px}
  button:hover{background:#2f6fd6} button:disabled{background:#555;cursor:not-allowed}
  #log{font-size:13px;color:#aaa;white-space:pre-wrap;margin-top:8px}
  .tip{font-size:13px;color:#999}
</style>
</head>
<body>
<h1>🅱️ B站接入面板</h1>
<div class="card">
  <div class="row"><span>浏览器调试实例</span><span id="browser" class="warn">检测中…</span></div>
  <div class="row"><span>B站登录态</span><span id="bili" class="warn">检测中…</span></div>
  <div class="row"><span>写权限(CSRF)</span><span id="csrf" class="warn">—</span></div>
  <div class="row"><span>登录账号</span><span id="uname">—</span></div>
  <div class="row"><span>当前标签</span><span id="curtab" class="tip">—</span></div>
</div>
<button id="btn" onclick="doBind()">一键接入（启动调试浏览器并读取 B站登录态）</button>
<div class="tip">说明：接入不会关闭/删除你的浏览器，全程无需退出浏览器。若默认浏览器正被占用无法再开调试实例，
优先使用"B站接入桥"扩展通道（扩展随浏览器自动同步登录态），或刷新本页查看扩展通道状态即可。</div>
<div id="log"></div>
<script>
async function refresh(){
  try{
    const r = await fetch('/api/status'); const s = await r.json();
    document.getElementById('browser').textContent = s.browser || '未接入';
    document.getElementById('browser').className = s.browser_ok ? 'ok' : 'warn';
    document.getElementById('bili').textContent = s.bound ? '已绑定（自动接入）' : (s.cookie_manual ? '已绑定（手动配置）' : '未绑定');
    document.getElementById('bili').className = s.bound || s.cookie_manual ? 'ok' : 'bad';
    const c = document.getElementById('csrf');
    if (s.has_csrf === true) { c.textContent = '就绪（可发弹幕）'; c.className = 'ok'; }
    else if (s.has_csrf === false) { c.textContent = '缺失，需重新登录 B站'; c.className = 'bad'; }
    else { c.textContent = '—'; c.className = 'warn'; }
    document.getElementById('uname').textContent = s.uname || '—';
    const t = s.current_tab && s.current_tab.url;
    document.getElementById('curtab').textContent = t ? (s.current_tab.title || s.current_tab.url).slice(0, 40) : '—';
  }catch(e){ document.getElementById('log').textContent = '状态刷新失败：' + e; }
}
async function doBind(){
  const btn = document.getElementById('btn'); btn.disabled = true;
  document.getElementById('log').textContent = '正在接入…（启动调试浏览器，最多约 10 秒）';
  try{
    const r = await fetch('/api/bind', {method:'POST'}); const s = await r.json();
    document.getElementById('log').textContent = s.detail || JSON.stringify(s);
  }catch(e){ document.getElementById('log').textContent = '接入失败：' + e; }
  btn.disabled = false; refresh();
}
setInterval(refresh, 3000); refresh();
</script>
</body>
</html>
"""


class _PanelHandler(BaseHTTPRequestHandler):
    """面板 HTTP 处理器；绑定/扩展回调由 BindPanel 挂到 server 实例属性上注入。"""

    def log_message(self, *_args):  # 静默默认访问日志
        pass

    # -- helpers --
    def _send(self, code: int, ctype: str, body: bytes) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, payload: dict) -> None:
        self._send(code, "application/json; charset=utf-8", json.dumps(payload, ensure_ascii=False).encode("utf-8"))

    def _read_json_body(self, max_len: int = 65536) -> dict:
        """读取并解析 JSON 请求体；超过 max_len 直接视为空（防内存/磁盘滥用）。

        extension-shot 通道（浏览器截屏 base64）与 extension-dom（页面 DOM 实况）
        的负载可能远超默认 64KB，调用方按端点传入更大上限；其余端点保持默认小上限。
        """
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0 or length > max_len:
                return {}
            return json.loads(self.rfile.read(length).decode("utf-8", "replace"))
        except Exception:
            return {}

    def _origin_allowed(self) -> bool:
        """来源校验：只接受本机面板、浏览器扩展与无来源（本地进程/curl）。

        恶意网页可通过 text/plain 的简单请求把伪造 cookie/tab 推送到本机
        127.0.0.1:8765（CSRF 式覆盖绑定），因此有 Origin/Referer 时必须放行
        可信来源，其余一律拒绝。
        """
        origin = (self.headers.get("Origin") or "").strip().rstrip("/")
        if origin:
            panel_origin = f"http://{_PANEL_HOST}:{self.server.server_address[1]}"
            if origin == panel_origin:
                return True
            if origin.startswith("chrome-extension://") or origin.startswith("edge-extension://"):
                return True
            return False
        referer = (self.headers.get("Referer") or "").strip()
        if referer:
            if referer.startswith(f"http://{_PANEL_HOST}:{self.server.server_address[1]}/"):
                return True
            if referer.startswith("chrome-extension://") or referer.startswith("edge-extension://"):
                return True
            return False
        return True  # 无来源（本地进程/脚本）视为可信

    # -- routes --
    def do_GET(self) -> None:
        if not self._origin_allowed():
            self._json(403, {"error": "origin not allowed"})
            return
        status_fn = self.server.status_fn
        if self.path.startswith("/api/status"):
            payload = dict(status_fn() or {})
            self._json(200, payload)
            return
        if self.path.startswith("/api/extension-status"):
            payload = dict(status_fn() or {})
            self._json(200, {
                "online": bool(payload.get("extension_online")),
                "last_seen": payload.get("extension_last_seen"),
                "bound": bool(payload.get("bound")),
            })
            return
        if self.path.startswith("/api/pending-command"):
            now = time.time()
            _CMD_QUEUE[:] = [c for c in _CMD_QUEUE if now - (c.get("created") or now) < 600]
            cmd = _CMD_QUEUE[0] if _CMD_QUEUE else {}
            self._json(200, cmd or {"id": None})
            return
        self._send(200, "text/html; charset=utf-8", _PAGE.encode("utf-8"))

    def do_POST(self) -> None:
        if not self._origin_allowed():
            self._json(403, {"error": "origin not allowed"})
            return
        if self.path.startswith("/api/bind"):
            try:
                payload = dict(self.server.bind_fn() or {})
            except Exception as exc:  # 绑定动作绝不让面板崩溃
                payload = {"ok": False, "detail": f"接入异常：{exc}"}
            self._json(200, payload)
            return
        if self.path.startswith("/api/queue-command"):
            global _CMD_SEQ
            body = self._read_json_body()
            cmd_type = body.get("type") or ""
            room = body.get("room") or ""
            browser = str(body.get("browser") or "").strip().lower()
            if cmd_type == "enter-live" and str(room).isdigit():
                _CMD_SEQ += 1
                _CMD_QUEUE.append({"id": _CMD_SEQ, "type": "enter-live", "room": str(room),
                                   "browser": browser or None, "created": time.time()})
                self._json(200, {"queued": True, "id": _CMD_SEQ})
            else:
                self._json(400, {"error": "bad command"})
            return
        if self.path.startswith("/api/pending-command"):
            body = self._read_json_body()
            ack = body.get("ack")
            if ack is not None:
                _CMD_QUEUE[:] = [c for c in _CMD_QUEUE if c.get("id") != ack]
            self._json(200, {"ok": True})
            return
        if self.path.startswith("/api/extension-cookie"):
            body = self._read_json_body()
            try:
                _refresh_ext_cookie(body.get("cookie", ""))  # v0.2.18 插件侧登录态自保鲜
                payload = dict(
                    self.server.ext_fn(
                        body.get("cookie", ""),
                        int(body.get("at", 0) or 0),
                        str(body.get("source", "") or ""),
                    )
                    or {}
                )
                _logger.info("ext cookie: cookie_len=%s at=%s source=%s ok=%s", len(str(body.get("cookie", "") or "")), body.get("at"), body.get("source"), payload.get("ok"))
            except Exception as exc:
                payload = {"ok": False, "detail": f"扩展推送处理异常：{exc}"}
                _logger.info("ext cookie: ERROR %s", exc)
            self._json(200, payload)
            return
        if self.path.startswith("/api/extension-tab"):
            body = self._read_json_body()
            try:
                payload = dict(
                    self.server.tab_fn(
                        str(body.get("url", "") or ""),
                        str(body.get("title", "") or ""),
                        int(body.get("at", 0) or 0),
                        str(body.get("source", "") or ""),
                    )
                    or {}
                )
                _logger.info("ext tab: url=%s title=%s source=%s", str(body.get("url", "") or "")[:100], str(body.get("title", "") or "")[:40], body.get("source"))
            except Exception as exc:
                payload = {"ok": False, "detail": f"扩展标签页推送处理异常：{exc}"}
                _logger.info("ext tab: ERROR %s", exc)
            self._json(200, payload)
            return
        if self.path.startswith("/api/extension-dom"):
            body = self._read_json_body(_EXT_DOM_MAX_BYTES)
            try:
                payload = dict(self.server.dom_fn(body) or {"ok": False})
                _logger.info("ext dom: kind=%s title=%s body_len=%s", body.get("kind"), str(body.get("title", "") or "")[:40], len(body))
            except Exception as exc:
                payload = {"ok": False, "detail": f"扩展页面DOM推送处理异常：{exc}"}
                _logger.info("ext dom: ERROR %s", exc)
            self._json(200, payload)
            return
        if self.path.startswith("/api/extension-shot"):
            body = self._read_json_body(_EXT_SHOT_MAX_BYTES)
            try:
                payload = dict(self.server.shot_fn(body) or {"ok": False})
                _logger.info("ext shot: img_len=%s url=%s", len(str(body.get("image", "") or "")), str(body.get("url", "") or "")[:80])
            except Exception as exc:
                payload = {"ok": False, "detail": f"扩展截屏推送处理异常：{exc}"}
                _logger.info("ext shot: ERROR %s", exc)
            self._json(200, payload)
            return
        # 注意：/api/extension-audio-state 必须先于 /api/extension-audio 判断，
        # 否则会被 audio 前缀匹配吞掉（audio-state 请求体没有 base64 声音字段）。
        if self.path.startswith("/api/extension-audio-state"):
            body = self._read_json_body(64 * 1024)
            try:
                payload = dict(self.server.audio_state_fn(body) or {"ok": False})
                _logger.info(
                    "ext audio-state: enabled=%s at=%s ok=%s ctx_state=%s ctx_err=%s",
                    body.get("enabled"), body.get("at"), payload.get("ok"),
                    body.get("ctx_state"), str(body.get("ctx_err") or "")[:200],
                )
            except Exception as exc:
                payload = {"ok": False, "detail": f"扩展声音状态推送处理异常：{exc}"}
                _logger.info("ext audio-state: ERROR %s", exc)
            self._json(200, payload)
            return
        if self.path.startswith("/api/extension-audio"):
            body = self._read_json_body(_EXT_AUDIO_MAX_BYTES)
            try:
                payload = dict(self.server.audio_fn(body) or {"ok": False})
                _logger.info("ext audio: body_len=%s seq=%s ok=%s", len(body), body.get("seq"), payload.get("ok"))
            except Exception as exc:
                payload = {"ok": False, "detail": f"扩展声音推送处理异常：{exc}"}
                _logger.info("ext audio: ERROR %s", exc)
            self._json(200, payload)
            return
        if self.path.startswith("/api/extension-danmaku"):
            body = self._read_json_body(64 * 1024)
            try:
                text = str(body.get("text", "") or "")[:200]
                sender = str(body.get("sender", "") or "")[:40]
                room = str(body.get("room", "") or "")[:20]
                if text:
                    _DANMAKU_BUF.append((sender, text, int(body.get("at") or 0) or time.time(), room))
                    low = text.lower()
                    if any(n in low for n in _YUI_NICKNAMES):
                        _MENTION_NOTIFY["at"] = time.time()
                        _MENTION_NOTIFY["text"] = text
                        _MENTION_NOTIFY["sender"] = sender
                        _MENTION_NOTIFY["room"] = room
                        _logger.info("ext danmaku: MENTION sender=%s room=%s text=%s", sender, room, text)
                    else:
                        _logger.info("ext danmaku: room=%s sender=%s text=%s", room, sender, text)
                payload = {"ok": True}
            except Exception as exc:
                payload = {"ok": False, "detail": f"扩展弹幕推送处理异常：{exc}"}
                _logger.info("ext danmaku: ERROR %s", exc)
            self._json(200, payload)
            return
        self._json(404, {"error": "not found"})


class BindPanel:
    """懒启动的单例面板：ensure_started() 返回 (url, started_now)。

    port 可覆盖（默认 8765），测试场景传入随机端口避免冲突。
    """

    def __init__(self, status_fn, bind_fn, ext_fn=None, tab_fn=None, dom_fn=None, shot_fn=None, audio_fn=None, audio_state_fn=None, port: int = _PANEL_PORT) -> None:
        self._status_fn = status_fn
        self._bind_fn = bind_fn
        self._ext_fn = ext_fn or (lambda _cookie, _at: {"ok": False, "detail": "扩展通道未启用"})
        self._tab_fn = tab_fn or (lambda _url, _title, _at: {"ok": True})
        self._dom_fn = dom_fn or (lambda _body: {"ok": False, "detail": "扩展DOM通道未启用"})
        self._shot_fn = shot_fn or (lambda _body: {"ok": False, "detail": "扩展截屏通道未启用"})
        self._audio_fn = audio_fn or (lambda _body: {"ok": False, "detail": "扩展声音通道未启用"})
        self._audio_state_fn = audio_state_fn or (lambda _body: {"ok": False, "detail": "扩展声音状态通道未启用"})
        self._port = port
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def ensure_started(self) -> tuple[str, bool]:
        with self._lock:
            if self._server is not None:
                return self.url(), False
            server = ThreadingHTTPServer((_PANEL_HOST, self._port), _PanelHandler)
            # 回调挂到 server 实例属性（每请求 handler 通过 self.server 取，避免类属性 self 绑定）
            server.status_fn = self._status_fn
            server.bind_fn = self._bind_fn
            server.ext_fn = self._ext_fn
            server.tab_fn = self._tab_fn
            server.dom_fn = self._dom_fn
            server.shot_fn = self._shot_fn
            server.audio_fn = self._audio_fn
            server.audio_state_fn = self._audio_state_fn
            thread = threading.Thread(target=server.serve_forever, name="bili-bind-panel", daemon=True)
            thread.start()
            self._server = server
            self._thread = thread
            return self.url(), True

    def url(self) -> str:
        return f"http://{_PANEL_HOST}:{self._port}/"

    def stop(self) -> None:
        """关闭面板服务并释放端口（插件重载/关闭时调用，避免旧线程占住 8765）。"""
        with self._lock:
            server, self._server = self._server, None
            self._thread = None
            if server is not None:
                try:
                    server.shutdown()
                except Exception:
                    pass
                try:
                    server.server_close()
                except Exception:
                    pass

    @property
    def running(self) -> bool:
        return self._server is not None
