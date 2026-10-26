"""Yui Lock 电脑端 Web 面板。

浏览器打开 http://127.0.0.1:48913 （仅本机，不对局域网开放）：
- 实时状态：手机在线/电量/应用锁、电脑锁
- 按钮：锁手机屏幕 / 手机应用锁 / 解锁手机 / 锁电脑 / 解锁电脑
- 配对二维码（手机 App「扫码配对」用）

安全：
- 只监听 127.0.0.1；
- 校验 Host 头（防 DNS rebinding）；
- POST 动作必须带自定义头 X-YuiLock-Panel（跨站请求无法携带自定义头，
  浏览器会先发 CORS 预检并失败），且危险操作有 JS confirm；
- /api/status 不回显配对令牌。

用法（插件目录下）：
    uv run --with qrcode --with pillow python web_panel.py [--port 48913]

读取同目录 plugin.toml 的 [yuilock] 配置（token 必须与手机一致，否则控制命令会被拒绝）。
"""
from __future__ import annotations

import asyncio
import json
import socket
import sys
import tomllib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pclock
from client import YuiLockClient

ROOT = Path(__file__).resolve().parent
LOCKER = ROOT / "pc_locker.py"


def detect_lan_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("223.5.5.5", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


def load_config() -> dict:
    try:
        with open(ROOT / "plugin.toml", "rb") as f:
            data = tomllib.load(f)
        section = data.get("yuilock")
        return section if isinstance(section, dict) else {}
    except Exception:
        return {}


CFG = load_config()
CLIENT = YuiLockClient(
    host=CFG.get("host", "auto"),
    port=int(CFG.get("port", 48912) or 48912),
    token=str(CFG.get("token", "") or ""),
    transport=str(CFG.get("transport", "auto") or "auto"),
    bt_device_name=str(CFG.get("bt_device_name", "") or ""),
)
TOKEN = str(CFG.get("token", "") or "")
PORT = int(CFG.get("port", 48912) or 48912)
LAN_IP = detect_lan_ip()
PAIR_URI = f"yuilock://pair?h={LAN_IP}&p={PORT}&t={TOKEN}"
ALLOW_EXES = [sys.executable] + [str(p) for p in (CFG.get("pc_allow") or [])]

ACTIONS = {
    "lock_screen": lambda: CLIENT.lock_screen(),
    "lock_apps": lambda: CLIENT.lock_apps(),
    "unlock_phone": lambda: CLIENT.unlock(),
}
DANGEROUS = {"lock_screen", "lock_apps", "lock_pc"}


def run_action(name: str) -> dict:
    if name in ACTIONS:
        return asyncio.run(ACTIONS[name]())
    if name == "lock_pc":
        if pclock.is_locker_alive():
            return {"ok": True, "message": "电脑已经处于锁定状态"}
        r = pclock.spawn_locker(LOCKER, allow_exes=ALLOW_EXES)
        if r.get("ok"):
            return {"ok": True, "message": "电脑已锁定：打开任何程序都会被立即弹回桌面"}
        return {"ok": False, "message": f"锁定失败：{r.get('error')}"}
    if name == "unlock_pc":
        alive, msg = pclock.kill_locker()
        return {"ok": True, "message": msg + ("（原本未锁定）" if not alive else "")}
    return {"ok": False, "message": f"未知操作 {name}"}


PAGE = """<!doctype html>
<html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Yui Lock 面板</title>
<style>
body{background:#17171d;color:#eee;font-family:"Microsoft YaHei UI",system-ui;margin:0;padding:24px}
.card{background:#22222b;border-radius:14px;padding:18px 20px;max-width:520px;margin:0 auto 16px}
h1{font-size:20px;margin:0 0 4px;color:#E8536B}
small{color:#999}
#status{font-size:13px;line-height:1.8;white-space:pre-wrap;font-family:Consolas,monospace}
button{display:inline-block;margin:6px 6px 0 0;padding:10px 14px;border:0;border-radius:10px;
background:#E8536B;color:#fff;font-size:14px;cursor:pointer}
button.gray{background:#3a3a46}
#msg{margin-top:10px;font-size:13px;color:#ffd36e;min-height:18px}
img{width:200px;height:200px;background:#fff;border-radius:10px}
.row{display:flex;gap:18px;align-items:flex-start;flex-wrap:wrap}
</style></head><body>
<div class="card"><h1>🔒 Yui Lock 面板</h1>
<small>仅本机可访问 · 手机和电脑需同一 Wi-Fi（或蓝牙）</small>
<div id="status">加载中…</div>
<div class="row">
<div>
<button data-danger="1" onclick="act('lock_screen')">锁手机屏幕</button>
<button data-danger="1" onclick="act('lock_apps')">手机应用锁</button>
<button class="gray" onclick="act('unlock_phone')">解锁手机</button><br>
<button data-danger="1" onclick="act('lock_pc')">锁电脑</button>
<button class="gray" onclick="act('unlock_pc')">解锁电脑</button>
</div>
<div style="text-align:center">
<img src="/qr.png" alt="配对二维码（需安装 qrcode 库）">
<div><small>手机 App「扫码配对」</small></div>
</div>
</div>
<div id="msg"></div></div>
<script>
async function refresh(){
  try{const r=await fetch('/api/status');const j=await r.json();
  document.getElementById('status').textContent=j.text;}catch(e){
  document.getElementById('status').textContent='面板服务异常';}
}
async function act(name){
  const danger=document.querySelector(`button[onclick="act('${name}')"]`).dataset.danger==='1';
  if(danger&&!confirm('确定执行「'+name+'」？'))return;
  document.getElementById('msg').textContent='执行中…';
  try{const r=await fetch('/api/action',{method:'POST',
    headers:{'X-YuiLock-Panel':'1','Content-Type':'application/json'},
    body:JSON.stringify({action:name})});
  const j=await r.json();document.getElementById('msg').textContent=j.message||JSON.stringify(j);}
  catch(e){document.getElementById('msg').textContent='请求失败：'+e;}
  refresh();
}
refresh();setInterval(refresh,3000);
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):

    def _host_ok(self) -> bool:
        """防 DNS rebinding：Host 必须是 127.0.0.1 / localhost。"""
        host = (self.headers.get("Host") or "").lower()
        return host.startswith("127.0.0.1") or host.startswith("localhost")

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj: dict) -> None:
        self._send(200, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def do_GET(self):
        if not self._host_ok():
            self._send(403, b"forbidden", "text/plain")
            return
        if self.path == "/":
            self._send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")
        elif self.path == "/api/status":
            try:
                p = asyncio.run(CLIENT.ping())
            except Exception as exc:
                p = {"ok": False, "error": str(exc)}
            pc = pclock.is_locker_alive()
            if p.get("ok"):
                phone = (f"在线 · {p.get('device', '?')} · 电量 {p.get('battery', '?')}% · "
                         f"应用锁 {'开' if p.get('applock') else '关'} · "
                         f"锁屏权限 {'有' if p.get('admin') else '未激活'}")
                note = str(p.get("applock_note") or "")
                if note:
                    phone += f"\n手机备注：{note}"
                if not p.get("token_set"):
                    phone += "\n⚠ 手机未设令牌：控制命令会被拒绝，请在 plugin.toml 配置 token"
            else:
                phone = f"不在线（{p.get('error', '无响应')}）"
            self._json({"text": f"手机：{phone}\n电脑锁：{'🔒 已锁定' if pc else '未锁定'}\n"
                                f"配对令牌：{'已设置' if TOKEN else '⚠ 未设置'}\n"
                                f"局域网地址：{LAN_IP}:{PORT}"})
        elif self.path == "/qr.png":
            try:
                import qrcode
                qr = qrcode.QRCode(border=2)
                qr.add_data(PAIR_URI)
                qr.make(fit=True)
                import io
                buf = io.BytesIO()
                qr.make_image().save(buf, format="PNG")
                self._send(200, buf.getvalue(), "image/png")
            except Exception:
                self._send(404, b"qrcode lib missing", "text/plain")
        else:
            self._send(404, b"not found", "text/plain")

    def do_POST(self):
        if not self._host_ok():
            self._send(403, b"forbidden", "text/plain")
            return
        if self.path != "/api/action":
            self._send(404, b"not found", "text/plain")
            return
        # 防跨站：任意网页向 localhost POST 时带不了自定义头（会先触发 CORS 预检并失败）
        if (self.headers.get("X-YuiLock-Panel") or "") != "1":
            self._send(403, b"forbidden", "text/plain")
            return
        try:
            length = int(self.headers.get("Content-Length", 0) or 0)
            body = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
            name = str(body.get("action", ""))
            result = run_action(name)
            self._json(result)
        except Exception as exc:
            self._json({"ok": False, "message": f"执行失败：{exc}"})

    def log_message(self, fmt, *args):  # 安静模式
        pass


def main() -> None:
    port = 48913
    for i, a in enumerate(sys.argv):
        if a == "--port" and i + 1 < len(sys.argv):
            port = int(sys.argv[i + 1])
    if not TOKEN:
        print("[!] plugin.toml [yuilock] 未设置 token：手机端会拒绝控制命令")
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"Yui Lock 面板：http://127.0.0.1:{port}  （Ctrl+C 退出）")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
