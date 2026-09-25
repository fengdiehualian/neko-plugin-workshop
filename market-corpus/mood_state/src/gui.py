"""心情/精力状态 GUI 的本地 HTTP API（供原生窗口 gui_window.py 调用）。

纯标准库实现：ThreadingHTTPServer，/ 仍保留调试用单文件网页
（SVG 270° 仪表盘，蓝白圆角风格），/state、/adjust、/set_enabled 给原生窗口用。
仪表盘语义沿用 Open-LLM-VTuber launcher/gauge.py：270° 弧、红→琥珀→绿、
±1 按钮、Shift 点击 ±5。
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Optional

_PAGE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>心情精力状态</title>
<style>
  :root {
    --accent: #40C5F1;
    --accent-soft: #e0f7ff;
    --accent-softer: #f0f9ff;
    --bg: #f7f8fa;
    --card: #ffffff;
    --text: #33475b;
    --muted: #8aa0b4;
    --red: #F87171; --amber: #F5A623; --green: #3ECF8E;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    background: var(--bg);
    font-family: "Segoe UI", "Microsoft YaHei", system-ui, sans-serif;
    color: var(--text);
    display: flex; justify-content: center; align-items: flex-start;
    min-height: 100vh; padding: 28px 16px;
  }
  .card {
    background: var(--card);
    border-radius: 24px;
    box-shadow: 0 8px 30px rgba(64, 197, 241, .14);
    padding: 24px 28px 20px;
    width: 420px;
    border: 1px solid var(--accent-soft);
  }
  header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 6px; }
  h1 { font-size: 17px; font-weight: 700; }
  h1 small { color: var(--muted); font-weight: 400; font-size: 12px; margin-left: 6px; }
  .switch { position: relative; width: 44px; height: 24px; border-radius: 999px;
    background: #d7dee6; border: none; cursor: pointer; transition: background .2s; }
  .switch.on { background: var(--accent); }
  .switch::after { content: ""; position: absolute; top: 3px; left: 3px; width: 18px; height: 18px;
    border-radius: 50%; background: #fff; transition: left .2s; box-shadow: 0 1px 3px rgba(0,0,0,.2); }
  .switch.on::after { left: 23px; }
  .gauges { display: flex; gap: 18px; justify-content: center; margin: 10px 0 4px; }
  .gauge { background: var(--accent-softer); border-radius: 20px; padding: 14px 12px 12px;
    width: 178px; text-align: center; }
  .gauge h2 { font-size: 13px; color: var(--muted); font-weight: 600; margin-bottom: 2px; }
  svg { display: block; margin: 0 auto; }
  .track { stroke: var(--accent-soft); }
  .val { font-size: 26px; font-weight: 800; fill: var(--text); }
  .btns { display: flex; gap: 10px; justify-content: center; margin-top: 8px; }
  .btns button {
    width: 40px; height: 32px; border: none; border-radius: 12px; cursor: pointer;
    background: var(--accent-soft); color: #1493c4; font-size: 16px; font-weight: 700;
    transition: background .15s, transform .05s;
  }
  .btns button:hover { background: #c9efff; }
  .btns button:active { transform: scale(.94); }
  .note { margin-top: 12px; font-size: 12px; color: var(--muted); min-height: 18px;
    background: var(--accent-softer); border-radius: 12px; padding: 6px 10px; }
  .hint { margin-top: 8px; font-size: 11px; color: var(--muted); text-align: center; }
  .off .gauge, .off .note { opacity: .45; pointer-events: none; }
</style>
</head>
<body>
<div class="card" id="card">
  <header>
    <h1>心情精力状态 <small>mood_state</small></h1>
    <button class="switch" id="sw" title="启用/停用"></button>
  </header>
  <div class="gauges">
    <div class="gauge">
      <h2>心情 mood</h2>
      <svg width="140" height="120" viewBox="0 0 140 120">
        <path class="track" d="" fill="none" stroke-width="11" stroke-linecap="round" id="t1"/>
        <path d="" fill="none" stroke-width="11" stroke-linecap="round" id="a1"/>
        <text class="val" x="70" y="72" text-anchor="middle" id="v1">--</text>
      </svg>
      <div class="btns">
        <button data-d="mood" data-v="-1">−</button>
        <button data-d="mood" data-v="1">＋</button>
      </div>
    </div>
    <div class="gauge">
      <h2>精力 energy</h2>
      <svg width="140" height="120" viewBox="0 0 140 120">
        <path class="track" d="" fill="none" stroke-width="11" stroke-linecap="round" id="t2"/>
        <path d="" fill="none" stroke-width="11" stroke-linecap="round" id="a2"/>
        <text class="val" x="70" y="72" text-anchor="middle" id="v2">--</text>
      </svg>
      <div class="btns">
        <button data-d="energy" data-v="-1">−</button>
        <button data-d="energy" data-v="1">＋</button>
      </div>
    </div>
  </div>
  <div class="note" id="note"></div>
  <div class="hint">点击 ±1 · Shift 点击 ±5</div>
</div>
<script>
const CX = 70, CY = 62, R = 48, SPAN = 270, START = 135;
function pt(deg) {
  const a = deg * Math.PI / 180;
  return [CX + R * Math.cos(a), CY + R * Math.sin(a)];
}
function arc(frac) {
  const sweep = SPAN * frac;
  if (sweep <= 0.5) return "";
  const [x0, y0] = pt(START);
  const [x1, y1] = pt(START + sweep);
  return `M ${x0.toFixed(2)} ${y0.toFixed(2)} A ${R} ${R} 0 ${sweep > 180 ? 1 : 0} 1 ${x1.toFixed(2)} ${y1.toFixed(2)}`;
}
function color(v) { return v < 30 ? "#F87171" : v < 60 ? "#F5A623" : "#3ECF8E"; }
function draw(trackId, arcId, valId, v) {
  document.getElementById(trackId).setAttribute("d", arc(1));
  const a = document.getElementById(arcId);
  a.setAttribute("d", arc(v / 100));
  a.setAttribute("stroke", color(v));
  document.getElementById(valId).textContent = v;
}
let state = null;
async function refresh() {
  try {
    const r = await fetch("/state"); state = await r.json();
  } catch (e) { return; }
  document.getElementById("card").classList.toggle("off", !state.enabled);
  document.getElementById("sw").classList.toggle("on", state.enabled);
  draw("t1", "a1", "v1", state.mood);
  draw("t2", "a2", "v2", state.energy);
  document.getElementById("note").textContent =
    (state.eval_note ? "备注：" + state.eval_note + " · " : "") +
    state.mood_text + " " + state.energy_text;
}
document.querySelectorAll(".btns button").forEach(b => {
  b.addEventListener("click", ev => {
    const step = ev.shiftKey ? 5 : 1;
    const delta = parseInt(b.dataset.v) * step;
    fetch("/adjust", { method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({ dimension: b.dataset.d, delta }) }).then(refresh);
  });
});
document.getElementById("sw").addEventListener("click", async () => {
  await fetch("/set_enabled", { method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({ enabled: !state.enabled }) });
  refresh();
});
refresh(); setInterval(refresh, 2000);
</script>
</body>
</html>
"""


class _GuiHandler(BaseHTTPRequestHandler):
    """极简路由：/ 页面、/state 查询、/adjust 与 /set_enabled 调整。"""

    plugin: Any = None  # 由 start_gui_server 注入

    def log_message(self, fmt, *args):  # 静默，避免刷屏插件日志
        pass

    def _send(self, code: int, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj: Any, code: int = 200):
        self._send(
            code,
            json.dumps(obj, ensure_ascii=False).encode("utf-8"),
            "application/json",
        )

    def do_GET(self):
        if self.path == "/" or self.path.startswith("/?"):
            self._send(200, _PAGE.encode("utf-8"), "text/html; charset=utf-8")
        elif self.path == "/state":
            self._json(self.plugin.gui_state_snapshot())
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError):
            return self._json({"error": "bad json"}, 400)

        if self.path == "/adjust":
            dim = body.get("dimension")
            delta = body.get("delta")
            if dim not in ("mood", "energy") or not isinstance(delta, (int, float)):
                return self._json({"error": "bad params"}, 400)
            state = self.plugin.gui_adjust(dim, int(delta))
            return self._json(state)
        if self.path == "/set_enabled":
            state = self.plugin.gui_set_enabled(bool(body.get("enabled")))
            return self._json(state)
        return self._json({"error": "not found"}, 404)


class GuiServer:
    """127.0.0.1 上的小网页 GUI；线程内运行，shutdown 即停。"""

    def __init__(self, plugin: Any, port: int):
        self._plugin = plugin
        self._server: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self.url = ""
        self._wanted_port = port

    def start(self) -> str:
        handler = type("BoundHandler", (_GuiHandler,), {"plugin": self._plugin})
        try:
            self._server = ThreadingHTTPServer(
                ("127.0.0.1", self._wanted_port), handler
            )
        except OSError:
            self._server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.url = f"http://127.0.0.1:{self._server.server_address[1]}/"
        self._thread = threading.Thread(
            target=self._server.serve_forever, daemon=True, name="mood-state-gui"
        )
        self._thread.start()
        return self.url

    def stop(self):
        if self._server:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread:
            self._thread.join(timeout=2)
            self._thread = None


def start_gui_server(plugin: Any, port: int) -> GuiServer:
    srv = GuiServer(plugin, port)
    srv.start()
    return srv
