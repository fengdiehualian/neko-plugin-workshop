"""bili_viewer CDP 运行时接入：从正在运行的 Chrome/Edge 直接读取 B站 Cookie。

为什么需要它：Chrome 新版（2025+）启用 App-Bound Encryption，Cookie 用 v20 加密，
密钥绑定浏览器进程，离线文件解密不可行；而 CDP（DevTools 协议）让浏览器进程
自己解密并返回明文 Cookie——真正做到"接入正在使用的浏览器"，无需关闭浏览器。

实现要点：
- 零第三方依赖（urllib + socket + struct + base64 手写 WebSocket 客户端）；
- 优先连接已运行的调试实例；没有则按需以调试模式启动浏览器（复用原 profile）；
- 仅绑定 127.0.0.1，Cookie 不上传、不写日志；
- 浏览器保持正常运行（只是多开一个本地调试端口），用户无感。
"""

from __future__ import annotations

import base64
import json
import os
import socket
import struct
import subprocess
import time
import urllib.parse
import urllib.request

_DEBUG_PORT_BASE = 9333
_DEBUG_PORT_MAX = 9333 + 20
_POPEN_DETACHED = 0x00000008 | 0x08000000

_BROWSER_EXE_PATHS: dict[str, tuple[str, ...]] = {
    "Chrome": (
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
    ),
    "Edge": (
        os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ),
}

_BROWSER_DIRS: dict[str, str] = {
    "Chrome": os.path.join("Google", "Chrome", "User Data"),
    "Edge": os.path.join("Microsoft", "Edge", "User Data"),
}

_BROWSER_EXES: dict[str, str] = {"Chrome": "chrome.exe", "Edge": "msedge.exe"}

_STATUS_BOUND = "bound"
_STATUS_NOT_LOGGED_IN = "not_logged_in"
_STATUS_NO_BROWSER = "no_browser"
_STATUS_FAIL = "fail"

_KEEP_NAMES = (
    "SESSDATA",
    "bili_jct",
    "DedeUserID",
    "DedeUserID__ckMd5",
    "buvid3",
    "buvid4",
    "b_nut",
    "CURRENT_FNVAL",
    "bili_ticket",
    "bili_ticket_expires",
    "sid",
    "fingerprint",
    "fingerprintp",
    "buvid_fp",
)


# ══════════ 极简 WebSocket 客户端（仅客户端帧 + 文本） ══════════


def _ws_connect(ws_url: str, timeout: float = 5.0):
    u = ws_url.replace("ws://", "http://").replace("wss://", "https://")
    parsed = urllib.parse.urlsplit(u)
    host, port = parsed.hostname, parsed.port or 80
    path = parsed.path + ("?" + parsed.query if parsed.query else "")
    sock = socket.create_connection((host, port), timeout=timeout)
    key = base64.b64encode(os.urandom(16)).decode()
    req = (
        f"GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\nUpgrade: websocket\r\n"
        f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
        "Sec-WebSocket-Version: 13\r\nOrigin: http://localhost\r\n\r\n"
    )
    sock.sendall(req.encode())
    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = sock.recv(4096)
        if not chunk:
            raise OSError("ws handshake: conn closed")
        buf += chunk
    head, _, rest = buf.partition(b"\r\n\r\n")
    if b"101" not in head.split(b"\r\n")[0]:
        raise OSError("ws handshake failed: " + head[:200].decode(errors="replace"))
    if rest:
        sock.sendall(rest)
    return sock


def _ws_send(sock, payload: bytes) -> None:
    mask = os.urandom(4)
    n = len(payload)
    if n < 126:
        header = b"\x81" + bytes([0x80 | n])
    elif n < 65536:
        header = b"\x81" + bytes([0x80 | 126]) + struct.pack(">H", n)
    else:
        header = b"\x81" + bytes([0x80 | 127]) + struct.pack(">Q", n)
    masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    sock.sendall(header + mask + masked)


def _ws_recv(sock, timeout: float = 4.0) -> bytes:
    sock.settimeout(timeout)

    def _read_exact(n: int) -> bytes:
        buf = b""
        while len(buf) < n:
            c = sock.recv(n - len(buf))
            if not c:
                raise OSError("ws conn closed")
            buf += c
        return buf

    hdr = _read_exact(2)
    op = hdr[0] & 0x0F
    ln = hdr[1] & 0x7F
    if ln == 126:
        ln = struct.unpack(">H", _read_exact(2))[0]
    elif ln == 127:
        ln = struct.unpack(">Q", _read_exact(8))[0]
    mask = _read_exact(4) if hdr[1] & 0x80 else None
    data = _read_exact(ln)
    if mask:
        data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
    if op == 9:  # ping
        return _ws_recv(sock, timeout)
    if op == 8:
        raise OSError("ws closed by peer")
    if op == 2:
        raise OSError("ws binary frame unexpected")
    return data


def _cdp_call(ws_url: str, method: str, params: dict | None = None, timeout: float = 4.0) -> dict:
    sock = _ws_connect(ws_url)
    try:
        _ws_send(sock, json.dumps({"id": 1, "method": method, "params": params or {}}).encode())
        raw = b""
        while True:
            raw += _ws_recv(sock, timeout)
            try:
                msg = json.loads(raw)
                if msg.get("id") == 1:
                    return msg
            except Exception:
                continue
    finally:
        sock.close()


# ══════════ 调试实例管理 ══════════


def _http_json(url: str, timeout: float = 5.0) -> dict | None:
    try:
        return json.load(urllib.request.urlopen(url, timeout=timeout))
    except Exception:
        return None


def _find_debug_port() -> int | None:
    """并发探测 21 个调试端口（每端口 0.5s 预算），避免串行 21s 阻塞面板状态。"""
    import concurrent.futures

    def _probe(port: int) -> int | None:
        if _http_json(f"http://127.0.0.1:{port}/json/version", timeout=0.5) is not None:
            return port
        return None

    ports = list(range(_DEBUG_PORT_BASE, _DEBUG_PORT_MAX))
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        for found in pool.map(_probe, ports):
            if found is not None:
                return found
    return None


def _launch_debug_browser(name: str) -> int | None:
    """以调试模式启动浏览器（复用原 profile），返回调试端口；失败返回 None。

    只启动**一次**（固定端口 _DEBUG_PORT_BASE），绝不循环换端口反复启动：
    Chrome 单实例保护下，若默认 profile 正被实例占用，新进程会把启动参数
    转交给已有实例并自行退出——表现为已有 Chrome 不断弹出 about:blank
    空白页。因此一旦检测到进程提前退出且端口未就绪，立即放弃，不重试。
    """
    exe = next((p for p in _BROWSER_EXE_PATHS.get(name, ()) if os.path.exists(p)), None)
    if not exe:
        return None
    root = os.path.join(os.environ.get("LOCALAPPDATA", ""), _BROWSER_DIRS[name])
    if not os.path.isdir(root):
        return None
    port = _DEBUG_PORT_BASE
    try:
        proc = subprocess.Popen(
            [
                exe,
                f"--remote-debugging-port={port}",
                f"--user-data-dir={root}",
                # 仅放行本机来源（本插件 WS 客户端 Origin=http://localhost）；
                # 绝不用 --remote-allow-origins=*，否则任意网页可劫持 CDP 读取全部站点 Cookie。
                "--remote-allow-origins=http://localhost",
                "--no-first-run",
                "--no-default-browser-check",
                "about:blank",
            ],
            creationflags=_POPEN_DETACHED,
        )
    except Exception:
        return None
    # 短轮询（最多 8s）：正常 1-3s 内端口就绪；进程提前退出（profile 被占）则快速放弃
    deadline = time.monotonic() + 8.0
    while time.monotonic() < deadline:
        time.sleep(0.4)
        if _http_json(f"http://127.0.0.1:{port}/json/version", timeout=0.4) is not None:
            return port
        try:
            if proc.poll() is not None:
                return None
        except Exception:
            pass
    return None


def ensure_debug_browser(auto_launch: bool = True) -> tuple[int | None, str]:
    """确保存在可用的调试浏览器实例，返回 (port, detail)。

    全面接入语义：绝不自动关闭/重启浏览器；**不做进程状态判断**——
    残留进程不再阻挡接入（直接尝试以调试模式启动，Chrome 单实例保护会
    自行拒绝 profile 已被占用的情况，插件只做快速尝试与降级提示）。
    - 已有调试实例 → 直接复用；
    - 无调试实例：
      auto_launch=True（用户主动请求接入/在网页面板点击接入）→ 直接尝试启动调试实例；
      若 profile 正被占用导致启动失败 → 返回一次性设置引导；
      auto_launch=False（插件启动静默探测）→ 仅探测已开端口，不启动任何浏览器。
    """
    port = _find_debug_port()
    if port is not None:
        return port, "已连接正在运行的调试浏览器"
    # 复查优化（v0.7.3）：Chrome 启动失败时继续尝试 Edge，最后统一给引导；
    # 之前 Chrome 失败会立即 return，Edge 永远不会被尝试。
    for name in ("Chrome", "Edge"):
        exe_name = _BROWSER_EXES[name]
        if not os.path.isdir(os.path.join(os.environ.get("LOCALAPPDATA", ""), _BROWSER_DIRS[name])):
            continue
        if not auto_launch:
            return None, f"{name} 未开启接入端口（本次为静默探测，不启动浏览器）"
        port = _launch_debug_browser(name)
        if port is not None:
            return port, f"已启动 {name} 调试实例（浏览器保持运行）"
        if name == "Chrome":
            continue  # Chrome 启动失败（profile 被占/无可执行文件）→ 试 Edge
        return None, (
            f"{name} 正在使用中且未开启接入端口：默认配置的 {exe_name} 已在运行，"
            "无法再开一个调试实例。无需关闭或重启浏览器：请确认已安装并重载'B站接入桥'扩展"
            "（扩展会随浏览器自动同步登录态，无需任何关闭操作），或刷新接入面板查看扩展通道状态。"
        )
    return None, "未找到可启动的 Chrome/Edge 浏览器"


# ══════════ CDP 取 Cookie ══════════


def _extract_bili_cookie(cookies: list[dict]) -> str:
    items: list[tuple[str, str]] = []
    seen: set[str] = set()
    for c in cookies:
        name = str(c.get("name", ""))
        value = str(c.get("value", ""))
        domain = str(c.get("domain", ""))
        if "bilibili" not in domain:
            continue
        if name not in _KEEP_NAMES or not value or name in seen:
            continue
        if any(ch in value for ch in ';",'):
            continue
        seen.add(name)
        items.append((name, value))
    parts = [f"{n}={v}" for n, v in items]
    return "; ".join(parts)


def get_bili_cookie_via_cdp(port: int) -> tuple[str, str, str]:
    """通过 CDP 从运行中的浏览器读取 B站 Cookie。

    返回 (status, cookie_string, detail)：bound / not_logged_in / fail。

    注意：Chrome 155+ 已移除 Browser.getCookies（-32601），改用 Storage.getCookies；
    Network.getAllCookies 作为 page 会话兜底。
    """
    try:
        ver = _http_json(f"http://127.0.0.1:{port}/json/version", timeout=3)
        if not ver or not ver.get("webSocketDebuggerUrl"):
            return _STATUS_FAIL, "", "调试端口无响应"
        browser_ws = ver["webSocketDebuggerUrl"]

        def _try(ws: str, method: str) -> str:
            msg = _cdp_call(ws, method, timeout=3.0)
            if msg.get("error"):
                return ""
            cookies = msg.get("result", {}).get("cookies", []) or []
            return _extract_bili_cookie(cookies)

        cookie = _try(browser_ws, "Storage.getCookies")
        if "SESSDATA=" not in cookie:
            cookie = _try(browser_ws, "Browser.getCookies")  # 旧版 Chrome 兼容
        if "SESSDATA=" not in cookie:
            targets = _http_json(f"http://127.0.0.1:{port}/json/list", timeout=3) or []
            page = next((t for t in targets if t.get("type") == "page"), None)
            if page and page.get("webSocketDebuggerUrl"):
                cookie = _try(page["webSocketDebuggerUrl"], "Network.getAllCookies")
        if "SESSDATA=" in cookie:
            return _STATUS_BOUND, cookie, "已从运行中的浏览器读取 B站登录态"
        if cookie:
            return _STATUS_NOT_LOGGED_IN, cookie, "浏览器中有游客 Cookie 但无登录态（SESSDATA 缺失或已过期）"
        return _STATUS_NOT_LOGGED_IN, "", "浏览器中未找到 B站 Cookie（登录态缺失或已过期）"
    except Exception as exc:
        return _STATUS_FAIL, "", f"CDP 读取失败：{str(exc)[:80]}"


def bind_via_cdp(auto_launch: bool = True) -> tuple[str, str, str]:
    """一键接入正在使用的浏览器：确保调试实例 → CDP 读取 B站 Cookie 并绑定。

    auto_launch=False 时仅复用已开启的调试实例（静默探测），不主动启动浏览器。
    返回 (status, cookie_string, detail)。
    """
    port, detail = ensure_debug_browser(auto_launch=auto_launch)
    if port is None:
        return _STATUS_NO_BROWSER, "", detail
    return get_bili_cookie_via_cdp(port)
