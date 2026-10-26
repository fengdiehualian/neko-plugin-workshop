"""cdp_cookie 接入语义测试：绝不关闭/重启浏览器；静默探测不启动浏览器。

CI（Linux）与本地（Windows）都必须通过：测试显式构造 LOCALAPPDATA 下的
Chrome/Edge 目录，避免平台差异导致分支不同。
"""

from __future__ import annotations


def _cdp():
    from plugin.plugins.bili_viewer import cdp_cookie

    return cdp_cookie


def _setup_browser_dirs(monkeypatch, tmp_path) -> None:
    """模拟 Windows 的 LOCALAPPDATA 与 Chrome/Edge User Data 目录。"""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    (tmp_path / "Google" / "Chrome" / "User Data").mkdir(parents=True)
    (tmp_path / "Microsoft" / "Edge" / "User Data").mkdir(parents=True)


def test_ensure_reuses_existing_debug_port(monkeypatch, tmp_path):
    cdp_cookie = _cdp()
    _setup_browser_dirs(monkeypatch, tmp_path)
    monkeypatch.setattr(cdp_cookie, "_find_debug_port", lambda: 9333)
    port, detail = cdp_cookie.ensure_debug_browser(auto_launch=True)
    assert port == 9333
    assert "已连接" in detail


def test_ensure_never_kills_running_browser(monkeypatch, tmp_path):
    """浏览器占用中且无端口：绝不结束进程，尝试启动失败后仅给一次性设置引导。"""
    cdp_cookie = _cdp()
    _setup_browser_dirs(monkeypatch, tmp_path)
    killed = []

    def _fake_kill(cmd, **kw):
        killed.append(cmd)
        return type("P", (), {"returncode": 0})()

    monkeypatch.setattr(cdp_cookie, "_find_debug_port", lambda: None)
    monkeypatch.setattr(cdp_cookie, "_launch_debug_browser", lambda name: None)  # profile 被占用 → 启动失败
    monkeypatch.setattr(cdp_cookie.subprocess, "run", _fake_kill)
    port, detail = cdp_cookie.ensure_debug_browser(auto_launch=True)
    assert port is None
    assert "taskkill" not in detail and killed == []  # 没有发起任何结束进程操作
    assert "无需关闭或重启浏览器" in detail  # 全程不关闭浏览器，引导扩展通道


def test_ensure_silent_probe_does_not_launch(monkeypatch, tmp_path):
    """auto_launch=False（插件启动静默探测）：无论浏览器状态如何都不启动任何浏览器。"""
    cdp_cookie = _cdp()
    _setup_browser_dirs(monkeypatch, tmp_path)
    launched = []

    def _fake_launch(name):
        launched.append(name)
        return 9333

    monkeypatch.setattr(cdp_cookie, "_find_debug_port", lambda: None)
    monkeypatch.setattr(cdp_cookie, "_launch_debug_browser", _fake_launch)
    port, detail = cdp_cookie.ensure_debug_browser(auto_launch=False)
    assert port is None
    assert launched == []  # 静默探测绝不启动浏览器
    assert "静默探测" in detail


def test_ensure_launches_when_user_requests(monkeypatch, tmp_path):
    """用户主动请求接入（auto_launch=True）且无调试端口 → 直接尝试启动调试实例（不检查进程）。"""
    cdp_cookie = _cdp()
    _setup_browser_dirs(monkeypatch, tmp_path)
    launched = []
    monkeypatch.setattr(cdp_cookie, "_find_debug_port", lambda: None)
    monkeypatch.setattr(cdp_cookie, "_launch_debug_browser", lambda name: (launched.append(name) or 9333))
    port, detail = cdp_cookie.ensure_debug_browser(auto_launch=True)
    assert port == 9333
    assert launched == ["Chrome"]
    assert "已启动" in detail


def _fake_proc(exit_early: bool):
    class _P:
        def poll(self):
            return 0 if exit_early else None

    return _P()


def test_launch_tries_only_once_on_profile_busy(monkeypatch, tmp_path):
    """单实例保护下（进程被转交提前退出、端口不通）：只 Popen 一次，绝不循环换端口反复弹窗。"""
    cdp_cookie = _cdp()
    _setup_browser_dirs(monkeypatch, tmp_path)
    # 假 chrome.exe（必须真实存在，_launch 会 os.path.exists 检查）
    fake_exe = tmp_path / "fake_chrome.exe"
    fake_exe.write_bytes(b"MZ")
    monkeypatch.setattr(cdp_cookie, "_BROWSER_EXE_PATHS", {"Chrome": (str(fake_exe),)})
    monkeypatch.setattr(cdp_cookie, "_find_debug_port", lambda: None)
    monkeypatch.setattr(cdp_cookie, "_http_json", lambda url, timeout=1.0: None)  # 端口永不就绪
    pops = []

    def _fake_popen(cmd, **kw):
        pops.append(cmd)
        return _fake_proc(exit_early=True)  # 单实例转交 → 进程立即退出

    monkeypatch.setattr(cdp_cookie.subprocess, "Popen", _fake_popen)
    port = cdp_cookie._launch_debug_browser("Chrome")
    assert port is None
    assert len(pops) == 1  # 只启动一次，无 21 端口风暴
    assert "--remote-debugging-port=9333" in pops[0]


def test_launch_success_returns_port(monkeypatch, tmp_path):
    """调试实例正常启动（进程存活、端口就绪）→ 返回固定端口 9333。"""
    cdp_cookie = _cdp()
    _setup_browser_dirs(monkeypatch, tmp_path)
    fake_exe = tmp_path / "fake_chrome.exe"
    fake_exe.write_bytes(b"MZ")
    monkeypatch.setattr(cdp_cookie, "_BROWSER_EXE_PATHS", {"Chrome": (str(fake_exe),)})
    monkeypatch.setattr(cdp_cookie, "_http_json", lambda url, timeout=1.0: {"Browser": "ok"})
    pops = []

    def _fake_popen(cmd, **kw):
        pops.append(cmd)
        return _fake_proc(exit_early=False)  # 调试实例保持运行

    monkeypatch.setattr(cdp_cookie.subprocess, "Popen", _fake_popen)
    port = cdp_cookie._launch_debug_browser("Chrome")
    assert port == 9333
    assert len(pops) == 1
