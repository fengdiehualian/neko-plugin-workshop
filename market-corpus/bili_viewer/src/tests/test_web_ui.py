"""web_ui 面板端点测试：页面 / 一键接入 / 扩展推送 / 扩展状态。

使用随机端口启动真实本地 HTTP 服务，验证扩展通道（bili-bridge）可直连插件。
"""

from __future__ import annotations

import json
import socket
import time
import urllib.error
import urllib.request


def _web_ui():
    from plugin.plugins.bili_viewer import web_ui

    return web_ui


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _http_json(url: str, method: str = "GET", body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _start_panel(ext_fn):
    web_ui = _web_ui()

    def status_fn():
        return {
            "ok": True,
            "browser": "已连接调试浏览器",
            "browser_ok": True,
            "bound": False,
            "cookie_manual": False,
            "cookie_source": "none",
            "extension_online": False,
            "extension_last_seen": 0,
            "uname": "",
        }

    def bind_fn():
        return {"ok": True, "bound": True, "detail": "已启动调试实例"}

    panel = web_ui.BindPanel(status_fn, bind_fn, ext_fn=ext_fn, port=_free_port())
    url, started = panel.ensure_started()
    assert started is True
    return panel, url


def test_panel_serves_page(monkeypatch, tmp_path):
    panel, url = _start_panel(lambda *_: {"ok": True})
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            html = resp.read().decode("utf-8")
        assert "一键接入" in html
        assert "B站接入面板" in html
    finally:
        panel._server.shutdown()


def test_panel_bind_endpoint(monkeypatch, tmp_path):
    panel, url = _start_panel(lambda *_: {"ok": True})
    try:
        result = _http_json(url + "api/bind", method="POST")
        assert result["ok"] is True
        assert result["bound"] is True
    finally:
        panel._server.shutdown()


def test_extension_cookie_endpoint(monkeypatch, tmp_path):
    received = []
    panel, url = _start_panel(lambda cookie, at, source="": (received.append((cookie, at, source)) or {"ok": True, "bound": True}))
    try:
        body = {"cookie": "SESSDATA=abc; bili_jct=xyz", "at": int(time.time() * 1000), "source": "bili-bridge-extension"}
        result = _http_json(url + "api/extension-cookie", method="POST", body=body)
        assert result["ok"] is True
        assert received and received[0][0] == "SESSDATA=abc; bili_jct=xyz"
        assert received[0][1] == body["at"]
        assert received[0][2] == "bili-bridge-extension"
    finally:
        panel._server.shutdown()


def test_extension_status_endpoint(monkeypatch, tmp_path):
    panel, url = _start_panel(lambda *_: {"ok": True})
    try:
        result = _http_json(url + "api/extension-status")
        assert "online" in result
        assert "last_seen" in result
    finally:
        panel._server.shutdown()


def test_panel_started_once(monkeypatch, tmp_path):
    panel, url = _start_panel(lambda *_: {"ok": True})
    try:
        url2, started2 = panel.ensure_started()
        assert started2 is False
        assert url2 == url
    finally:
        panel._server.shutdown()


def test_panel_stop_releases_port(monkeypatch, tmp_path):
    """stop() 释放端口后，同一端口可再次启动（插件重载场景）。"""
    web_ui = _web_ui()
    panel, url = _start_panel(lambda *_: {"ok": True})
    port = panel._port
    panel.stop()
    # 停止后端口立即可复用（ThreadingHTTPServer 默认 SO_REUSEADDR）
    panel2 = web_ui.BindPanel(
        panel._status_fn, panel._bind_fn, ext_fn=panel._ext_fn, port=port
    )
    url2, started2 = panel2.ensure_started()
    try:
        assert started2 is True
        assert url2 == f"http://127.0.0.1:{port}/"
    finally:
        panel2.stop()


def test_panel_extension_tab_endpoint(monkeypatch, tmp_path):
    """扩展标签页端点：POST /api/extension-tab → tab_fn 回调收到 url/title/at/source。"""
    web_ui = _web_ui()
    received = []

    def tab_fn(url, title, at, source=""):
        received.append((url, title, at, source))
        return {"ok": True}

    panel = web_ui.BindPanel(
        lambda *_: {"ok": True},
        lambda *_: {"ok": True},
        ext_fn=lambda *_: {"ok": True},
        tab_fn=tab_fn,
        port=_free_port(),
    )
    _url, started = panel.ensure_started()
    assert started is True
    port = panel._port
    try:
        r = _http_json(
            f"http://127.0.0.1:{port}/api/extension-tab",
            "POST",
            {"url": "https://www.bilibili.com/video/BV1GJ411x7h7", "title": "测试_哔哩哔哩", "at": 123456, "source": "bili-bridge-chrome-extension"},
        )
        assert r.get("ok") is True
        assert received == [("https://www.bilibili.com/video/BV1GJ411x7h7", "测试_哔哩哔哩", 123456, "bili-bridge-chrome-extension")]
    finally:
        panel.stop()


def test_panel_post_origin_blocked(monkeypatch, tmp_path):
    """CSRF 防护：恶意网页 Origin 的 POST 被 403 拒绝；扩展 Origin 放行。"""
    web_ui = _web_ui()
    received = []

    def tab_fn(url, title, at, source=""):
        received.append(url)
        return {"ok": True}

    panel = web_ui.BindPanel(
        lambda *_: {"ok": True},
        lambda *_: {"ok": True},
        ext_fn=lambda *_: {"ok": True},
        tab_fn=tab_fn,
        port=_free_port(),
    )
    panel.ensure_started()
    port = panel._port

    def _post_with_origin(path: str, payload: dict, origin: str):
        data = json.dumps(payload).encode()
        req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data, method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("Origin", origin)
        return urllib.request.urlopen(req, timeout=10)

    try:
        # 恶意网页 Origin → 403，回调不触发
        try:
            with _post_with_origin("/api/extension-tab", {"url": "https://evil.example", "title": "x", "at": 1}, "https://evil.example"):
                assert False, "恶意 Origin 应被拒绝"
        except urllib.error.HTTPError as exc:
            assert exc.code == 403
        assert received == []
        # 扩展 Origin → 200 且回调触发
        with _post_with_origin(
            "/api/extension-tab",
            {"url": "https://www.bilibili.com/video/BV1GJ411x7h7", "title": "t", "at": 2},
            "chrome-extension://abcdefgh",
        ) as resp:
            assert resp.status == 200
        assert received == ["https://www.bilibili.com/video/BV1GJ411x7h7"]
        # 无 Origin（本地进程/curl）→ 放行
        with _post_with_origin(
            "/api/extension-tab",
            {"url": "https://live.bilibili.com/666", "title": "live", "at": 3},
            "",
        ) as resp:
            assert resp.status == 200
        assert len(received) == 2
    finally:
        panel.stop()


def test_extension_shot_accepts_large_body(monkeypatch, tmp_path):
    """截屏端点：大 payload（>默认 64KB，真实浏览器截图大小）必须放行（v0.6.39）。"""
    web_ui = _web_ui()
    received = []

    def shot_fn(body):
        received.append(body.get("image") or "")
        return {"ok": True}

    panel = web_ui.BindPanel(
        lambda *_: {"ok": True},
        lambda *_: {"ok": True},
        ext_fn=lambda *_: {"ok": True},
        shot_fn=shot_fn,
        port=_free_port(),
    )
    panel.ensure_started()
    port = panel._port
    big = "data:image/jpeg;base64," + ("A" * 200_000)  # ≈150KB base64
    try:
        data = json.dumps({"image": big, "url": "https://live.bilibili.com/1025219", "title": "t", "at": 1}).encode()
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/extension-shot", data=data, method="POST")
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=10) as resp:
            assert resp.status == 200
        assert received and received[0] == big
    finally:
        panel.stop()


def test_extension_cookie_body_still_limited(monkeypatch, tmp_path):
    """非截屏端点保持默认 64KB 上限：超大 payload 被拒为空，回调不收到内容（v0.6.39）。"""
    web_ui = _web_ui()
    received = []

    def ext_fn(cookie, at, source=""):
        received.append((cookie, at, source))
        return {"ok": True, "bound": True}

    panel = web_ui.BindPanel(
        lambda *_: {"ok": True},
        lambda *_: {"ok": True},
        ext_fn=ext_fn,
        port=_free_port(),
    )
    panel.ensure_started()
    port = panel._port
    try:
        data = json.dumps({"cookie": "SESSDATA=" + "x" * 100_000, "at": 1}).encode()
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/extension-cookie", data=data, method="POST")
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=10) as resp:
            assert resp.status == 200
        assert received and received[0][0] == ""  # body 超限 → 空 payload，未解析到 cookie
    finally:
        panel.stop()


def test_panel_get_origin_blocked(monkeypatch, tmp_path):
    """GET 端点对称校验：恶意网页 Origin 读状态也被 403（v0.6.39）。"""
    panel, url = _start_panel(lambda *_: {"ok": True})
    port = panel._port
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/status")
        req.add_header("Origin", "https://evil.example")
        try:
            with urllib.request.urlopen(req, timeout=10):
                assert False, "恶意 Origin 的 GET 应被拒绝"
        except urllib.error.HTTPError as exc:
            assert exc.code == 403
        # 面板自身 Origin 放行
        req2 = urllib.request.Request(f"http://127.0.0.1:{port}/api/status")
        req2.add_header("Origin", f"http://127.0.0.1:{port}")
        with urllib.request.urlopen(req2, timeout=10) as resp:
            assert resp.status == 200
    finally:
        panel.stop()
