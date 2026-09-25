"""运行时入口 ``runtime`` —— 启停与切换连接方式，靠 action 分流。

要紧的一条：``apply_runtime_settings`` **不重连**，而 ``start_auto_reply`` 在
``_running`` 为真时会早退成 ``already_running``。所以运行中切模式如果不显式停掉重连，
跑着的连接仍然是旧模式建的 —— 配置看着对了却不生效。下面钉住"跑着才重连、没跑只存设置"。
"""
from __future__ import annotations

from types import SimpleNamespace

from plugin.plugins.qq_auto_reply import CONNECTION_MODES, QQAutoReplyPlugin


def _plugin(*, running=False, restart_result=None):
    p = QQAutoReplyPlugin.__new__(QQAutoReplyPlugin)
    p._qq_settings = {"qq_connection_mode": "napcat"}
    p._running = running
    p.logger = SimpleNamespace(info=lambda *a: None, warning=lambda *a: None,
                               error=lambda *a: None)
    calls = {"persist": 0, "applied": 0, "restart": 0}

    async def _persist():
        calls["persist"] += 1

    async def _restart(auto_start):
        calls["restart"] += 1
        return restart_result

    p.settings_service = SimpleNamespace(
        persist_business_config=_persist,
        apply_runtime_settings=lambda s: calls.__setitem__("applied", calls["applied"] + 1),
    )
    p._restart_auto_reply_runtime = _restart
    return p, calls


# ── action 分流 ─────────────────────────────────────────────

async def test_dispatch_routes_start_and_stop():
    """start / stop 转发到 runtime_ops_service 的同名方法。"""
    p, _ = _plugin()
    seen: list[str] = []

    async def _start():
        seen.append("start")
        return "started"

    async def _stop():
        seen.append("stop")
        return "stopped"

    p.runtime_ops_service = SimpleNamespace(start_auto_reply=_start, stop_auto_reply=_stop)

    assert await p.runtime(action="start") == "started"
    assert await p.runtime(action="stop") == "stopped"
    assert seen == ["start", "stop"]


async def test_dispatch_rejects_unknown_action():
    """未知 action 必须明确报错，而不是静默什么都不做。"""
    p, _ = _plugin()
    r = await p.runtime(action="restart")

    assert r.is_err()
    assert "BAD_ACTION" in str(r.error)
    assert "restart" in str(r.error)


async def test_dispatch_rejects_empty_action():
    p, _ = _plugin()
    assert (await p.runtime(action="")) .is_err()


# ── 校验 ────────────────────────────────────────────────────

async def test_rejects_unknown_mode():
    """未知模式必须拒绝，且不能把脏值写进设置、也不能落盘。"""
    p, calls = _plugin()
    r = await p.runtime(action="set_mode", mode="onebot_9000")

    assert r.is_err()
    assert "BAD_MODE" in str(r.error)
    assert p._qq_settings["qq_connection_mode"] == "napcat"   # 原值未动
    assert calls == {"persist": 0, "applied": 0, "restart": 0}


def test_all_three_modes_are_accepted_by_the_constant():
    assert CONNECTION_MODES == ("napcat", "napcat_forward", "open_platform")


# ── 没在跑：只存设置，别顺手把机器人启动起来 ────────────────

async def test_not_running_only_saves():
    p, calls = _plugin(running=False)
    r = await p.runtime(action="set_mode", mode="open_platform")

    assert r.is_ok()
    assert r.value == {"status": "saved", "mode": "open_platform",
                       "restarted": False, "error": ""}
    assert p._qq_settings["qq_connection_mode"] == "open_platform"
    assert calls["persist"] == 1 and calls["applied"] == 1
    assert calls["restart"] == 0                              # 关键：没有偷偷启动


async def test_restart_false_while_running_only_saves():
    p, calls = _plugin(running=True, restart_result={"ok": True, "status": "", "error": ""})
    r = await p.runtime(action="set_mode", mode="napcat_forward", restart=False)

    assert r.value["status"] == "saved"
    assert r.value["restarted"] is False
    assert calls["restart"] == 0


# ── 运行中：必须停掉重连，否则新模式不生效 ──────────────────

async def test_running_reconnects():
    p, calls = _plugin(running=True, restart_result={"ok": True, "status": "started", "error": ""})
    r = await p.runtime(action="set_mode", mode="open_platform")

    assert r.value == {"status": "reconnected", "mode": "open_platform",
                       "restarted": True, "error": ""}
    assert calls["restart"] == 1


async def test_running_reconnect_failure_is_reported():
    """重连失败要如实报 —— 设置已经改了，不能只说"成功"让用户以为在跑。"""
    p, _ = _plugin(running=True,
                   restart_result={"ok": False, "status": "", "error": "RuntimeError: 连不上"})
    r = await p.runtime(action="set_mode", mode="open_platform")

    assert r.value["status"] == "reconnect_failed"
    assert r.value["restarted"] is False
    assert "连不上" in r.value["error"]
    # 设置仍然改了：下次启动会按新模式走
    assert p._qq_settings["qq_connection_mode"] == "open_platform"


async def test_persist_failure_does_not_block_the_switch():
    """落盘失败不该让切换整个失败 —— 内存里已经改了，运行时该重连还是要重连。"""
    p, calls = _plugin(running=True, restart_result={"ok": True, "status": "started", "error": ""})

    async def _boom():
        raise OSError("disk full")

    p.settings_service.persist_business_config = _boom

    r = await p.runtime(action="set_mode", mode="open_platform")

    assert r.is_ok()
    assert r.value["status"] == "reconnected"
    assert calls["restart"] == 1
