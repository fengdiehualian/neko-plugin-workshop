"""wait_for_onebot_ready must short-circuit on hard startup failure, not idle-wait 20s.

Background: a hard failure (napcat_directory configured but missing, launcher absent,
process won't start) sets the startup error, and wait_for_onebot_ready must return
immediately instead of polling the full timeout (20s) — the frontend call() also polls
with a 20s cap, so the start button would report a spurious timeout.

An unconfigured napcat_directory is NOT a hard failure: the user may start NapCat
manually, so ensure_napcat_started stays silent and wait_for_onebot_ready still polls
for the OneBot connection that a manual launch provides.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from plugin.plugins.qq_auto_reply.napcat_service import QQNapcatService


def _plugin(*, qq_settings=None, startup_error=None, qq_client=None):
    return SimpleNamespace(
        settings=dict(qq_settings or {}),
        qq_client=qq_client,
        logger=SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None),
        emit_log=lambda *a, **k: None,
        startup_error=startup_error,
    )


def _make_service(plugin):
    service = QQNapcatService(
        get_settings=lambda: plugin.settings,
        get_qq_client=lambda: plugin.qq_client,
        config_dir="C:/tmp",
        logger=plugin.logger,
        emit_log=plugin.emit_log,
    )
    if plugin.startup_error:
        service.set_startup_error(plugin.startup_error)
    return service


def test_wait_shortcircuits_when_startup_error_already_set():
    """With startup_error already set, returns False immediately without polling."""
    plugin = _plugin(startup_error="启动器不存在: xxx")
    service = _make_service(plugin)

    # 若不走短路，会等满 timeout；这里验证立即返回
    import time
    start = time.monotonic()
    result = asyncio.run(service.wait_for_onebot_ready(timeout_seconds=5.0))
    elapsed = time.monotonic() - start

    assert result is False
    assert elapsed < 2.0  # 远小于 5s timeout，证明短路生效


def test_wait_polls_when_no_error_but_no_client():
    """Without startup_error and no client connected, polls normally and returns False on timeout."""
    plugin = _plugin(startup_error=None, qq_client=SimpleNamespace(is_connected=lambda: False))
    service = _make_service(plugin)

    result = asyncio.run(service.wait_for_onebot_ready(timeout_seconds=0.5, poll_interval=0.05))

    assert result is False
    # 超时后应设置「没有客户端连接」错误
    assert "没有客户端连接" in service.get_startup_error()


def test_wait_returns_true_when_connected():
    """With a client connected, returns True immediately and clears startup_error."""
    plugin = _plugin(
        startup_error="旧错误",
        qq_client=SimpleNamespace(is_connected=lambda: True),
    )
    service = _make_service(plugin)

    result = asyncio.run(service.wait_for_onebot_ready(timeout_seconds=5.0))

    assert result is True
    assert service.get_startup_error() == ""  # clear_startup_error 已执行


def test_ensure_started_no_dir_stays_silent_for_manual_launch(monkeypatch, tmp_path):
    """Unset napcat_directory AND no bundled NapCat → no error, no launch: the user may
    start NapCat manually, and wait_for_onebot_ready still polls for that connection.

    自带位置必须指到空目录：真实插件目录里可能就装着 NapCat（一键部署装在那儿），
    不隔离的话这条断言会随开发机上装没装而变 —— 那是在测环境，不是在测行为。
    """
    from plugin.plugins.qq_auto_reply import napcat_service as ns
    monkeypatch.setattr(ns, "bundled_napcat_dir", lambda: tmp_path)

    plugin = _plugin(qq_settings={})
    service = _make_service(plugin)

    asyncio.run(service.ensure_napcat_started())

    assert service.get_startup_error() == ""
    assert service.napcat_process is None
    # Unconfigured is not a hard failure: wait_for_onebot_ready keeps polling for a manually started OneBot
    assert not service.has_hard_startup_error()


def test_ensure_started_finds_bundled_napcat_without_config(monkeypatch, tmp_path):
    """自带位置有启动器时，**未配置也要启动**。

    一键部署把 NapCat 装到 ``<插件目录>/NapCat.Shell``，用户不填 napcat_directory
    是正常状态。这里曾经按「设置项填没填」判断，导致那种情况永远不启动、且毫无报错
    —— 表现就是"打开界面 NapCat 没起来"，没有任何线索可查。
    """
    from plugin.plugins.qq_auto_reply import napcat_platform
    from plugin.plugins.qq_auto_reply import napcat_service as ns

    # 启动器候选名是**平台相关**的（Windows 是 .bat 系列，POSIX 只有 napcat.mjs），
    # 而这里写的是 .bat —— 所以必须显式声明"测的是 Windows 那套语义"。继承宿主
    # 平台的话，这条在 Linux CI 上会被 find_launcher 判为"没有启动器"、静默不启动，
    # 断言必挂（它从落地起就一直红在 Linux 上，本地 Windows 跑不出来）。
    monkeypatch.setattr(napcat_platform, "is_windows", lambda: True)

    (tmp_path / "launcher-user.bat").write_text("@echo off", encoding="utf-8")
    monkeypatch.setattr(ns, "bundled_napcat_dir", lambda: tmp_path)

    launched: list = []

    class _Proc:
        pid = 4242
        returncode = None

        async def wait(self):
            return 0

        def kill(self):
            pass

    async def _fake_exec(*args, **kwargs):
        launched.append(args)
        return _Proc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)

    plugin = _plugin(qq_settings={})
    service = _make_service(plugin)

    asyncio.run(service.ensure_napcat_started())

    assert launched, "自带 NapCat 且未配置 napcat_directory 时应当启动"
    assert service.get_startup_error() == ""
    assert service.napcat_process is not None


def test_ensure_started_sets_hard_error_when_configured_dir_missing():
    """Configured-but-missing napcat_directory is a hard failure: ensure_napcat_started
    sets an explicit error (not silent), which short-circuits wait_for_onebot_ready."""
    plugin = _plugin(qq_settings={"napcat_directory": "C:/does/not/exist"})
    service = _make_service(plugin)

    asyncio.run(service.ensure_napcat_started())

    assert "launcher" in service.get_startup_error().lower()
    assert service.napcat_process is None
    # Hard failure: wait_for_onebot_ready short-circuits immediately instead of polling
    assert service.has_hard_startup_error()


def test_forward_mode_missing_launcher_is_not_hard_error():
    """Forward mode treats a missing local launcher as best-effort (warning, not a
    hard error) -- NapCat may be remote/manually started, so bootstrap() must not
    fail just because no local launcher is configured."""
    plugin = _plugin(qq_settings={
        "napcat_directory": "C:/does/not/exist",
        "qq_connection_mode": "napcat_forward",
    })
    service = _make_service(plugin)

    asyncio.run(service.ensure_napcat_started())

    assert service.napcat_process is None
    assert not service.has_hard_startup_error()


def test_transient_timeout_recognized_after_mode_switch():
    """A forward-mode timeout text written before switching to reverse must still be
    treated as transient (not a hard failure), so wait_for_onebot_ready keeps polling.

    Regression: has_hard_startup_error() used to compare the saved error against the
    CURRENT mode's transient text -- after a forward timeout, switching to reverse made
    the old forward text look like a hard failure and short-circuited the polling.
    """
    plugin = _plugin(qq_settings={"qq_connection_mode": "napcat_forward"})
    service = _make_service(plugin)
    # Forward mode writes the forward transient timeout text
    service._set_startup_error(service.FORWARD_TRANSIENT_TIMEOUT_ERROR)
    assert not service.has_hard_startup_error()
    # Switch to reverse: the old forward text must not be re-classified as hard
    plugin.settings["qq_connection_mode"] = "napcat"
    assert not service.has_hard_startup_error()
    # Reverse's own transient text is also transient under any mode
    service._set_startup_error(service.TRANSIENT_TIMEOUT_ERROR)
    assert not service.has_hard_startup_error()
