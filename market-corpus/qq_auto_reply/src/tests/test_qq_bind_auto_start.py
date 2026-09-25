"""扫码绑定收尾：凭据换过之后按新凭据重启自动回复。

重点是那条**必须丢对象**的约束：``create_onebot_connection`` 在构造时就把
app_id/client_secret 拷进连接对象，而 ``_ensure_qq_client_initialized`` 见对象非空
就早退。所以"停 → 丢 → 启"里少了中间那步，收尾会拿**刚被轮换掉的旧密钥**去连，
现象是"配置明明写对了却连不上"。"丢对象"那条用例就是钉这个的。
"""
from __future__ import annotations

import inspect
from types import MethodType, SimpleNamespace

from plugin.plugins.qq_auto_reply import QQAutoReplyPlugin
from plugin.sdk.plugin import Err, Ok, SdkError


def _plugin(*, start_result=None, start_raises=None, stop_raises=None, client=object()):
    """只装被测方法用到的那几样：logger / _stop_auto_reply_runtime / qq_client / runtime_ops_service。

    ``start_auto_reply`` 走 ``runtime_ops_service``，**不要**把它直接挂在实例上：
    入口方法已并进 ``runtime``，直接挂实例会让"入 口被删掉"这类断口被桩悄悄兜住
    （曾经真的漏过一次，见 ``__init__.py`` 里 ``_restart_auto_reply_runtime`` 的注释）。
    """
    calls: dict = {"stop": [], "client_at_start": "unset"}

    async def _stop(*, stop_napcat):
        calls["stop"].append(stop_napcat)
        if stop_raises is not None:
            raise stop_raises

    async def _start(**kwargs):
        # record 那一刻的 qq_client —— 用来断言"丢对象"真的发生在启动之前
        calls["client_at_start"] = plugin.qq_client
        if start_raises is not None:
            raise start_raises
        return start_result

    plugin = SimpleNamespace(
        logger=SimpleNamespace(info=lambda *a: None, warning=lambda *a: None, error=lambda *a: None),
        qq_client=client,
        _stop_auto_reply_runtime=_stop,
        runtime_ops_service=SimpleNamespace(start_auto_reply=_start),
    )
    plugin._restart_auto_reply_runtime = MethodType(
        QQAutoReplyPlugin._restart_auto_reply_runtime, plugin)
    return plugin, calls


# ── auto_start=False：只写配置，不动运行时 ──────────────────

async def test_auto_start_false_touches_nothing():
    plugin, calls = _plugin(start_result=Ok({"status": "started"}))

    out = await plugin._restart_auto_reply_runtime(False)

    assert out == {"ok": False, "status": "", "error": ""}
    assert calls["stop"] == []
    assert calls["client_at_start"] == "unset"
    assert plugin.qq_client is not None      # 没被丢


# ── 正常路径 ────────────────────────────────────────────────

async def test_starts_and_reports_status():
    plugin, calls = _plugin(start_result=Ok({"status": "started"}))

    out = await plugin._restart_auto_reply_runtime(True)

    assert out == {"ok": True, "status": "started", "error": ""}
    # NapCat 与该流程无关，不该顺手被杀
    assert calls["stop"] == [False]


async def test_client_is_dropped_before_start():
    """回归：启动时连接对象必须是 None —— 否则会拿着轮换前的旧密钥去连。"""
    plugin, calls = _plugin(start_result=Ok({"status": "started"}))

    await plugin._restart_auto_reply_runtime(True)

    assert calls["client_at_start"] is None


async def test_already_running_is_still_ok():
    """运行时本来就在跑时 start 会回 already_running —— 那是成功，不是失败。"""
    plugin, _ = _plugin(start_result=Ok({"status": "already_running"}))

    out = await plugin._restart_auto_reply_runtime(True)

    assert out["ok"] is True
    assert out["status"] == "already_running"


# ── 失败路径 ────────────────────────────────────────────────

async def test_start_rejection_is_reported_not_raised():
    """启动被拒不能把整个绑定结果变成失败 —— 凭据此时已经写好了。"""
    plugin, calls = _plugin(start_result=Err(SdkError("NOT_INITIALIZED: QQ 客户端未初始化")))

    out = await plugin._restart_auto_reply_runtime(True)

    assert out["ok"] is False
    assert "NOT_INITIALIZED" in out["error"]
    assert calls["client_at_start"] is None


async def test_start_exception_is_swallowed():
    plugin, _ = _plugin(start_raises=RuntimeError("connect refused"))

    out = await plugin._restart_auto_reply_runtime(True)

    assert out["ok"] is False
    assert "RuntimeError" in out["error"] and "connect refused" in out["error"]


async def test_stop_failure_does_not_block_rebuild():
    """停不下来不该阻断重建 —— 对象照样丢，启动照样试。"""
    plugin, calls = _plugin(start_result=Ok({"status": "started"}),
                            stop_raises=RuntimeError("stop boom"))

    out = await plugin._restart_auto_reply_runtime(True)

    assert out["ok"] is True
    assert calls["client_at_start"] is None


async def test_stop_failure_alone_still_reports_ok_status():
    """stop 抛异常但 start 正常 → 整体仍是成功（stop 的异常只记日志）。"""
    plugin, _ = _plugin(start_result=Ok({"status": "started"}),
                        stop_raises=TimeoutError())

    out = await plugin._restart_auto_reply_runtime(True)

    assert out == {"ok": True, "status": "started", "error": ""}


# ── 三条接入流程共用同一组字段 ──────────────────────────────

def test_deploy_actions_take_auto_start():
    """三条接入流程（绑定轮询 / 一键部署 / 补写配置）合并后共用 `deploy` 入口，
    它们各自的 auto_start 都要在 schema 里声明，且默认开。"""
    from plugin.sdk.shared.core.decorators import EVENT_META_ATTR
    props = getattr(QQAutoReplyPlugin.deploy, EVENT_META_ATTR).input_schema["properties"]
    assert props["auto_start"]["default"] is True

    for action in ("bind_poll", "one_click", "apply_onebot"):
        params = inspect.signature(
            getattr(QQAutoReplyPlugin, f"_deploy_{action}")).parameters
        assert list(params) == ["self", "kw"], action


def test_auto_start_fields_normalizes():
    """三条流程的返回值共用同一组键名 —— 前端不必按流程分支猜字段。"""
    assert QQAutoReplyPlugin._auto_start_fields(
        {"ok": True, "status": "started", "error": ""}) == {
        "auto_started": True, "auto_start_status": "started", "auto_start_error": ""}

    # 键缺失时给确定的默认值，不抛、也不漏键（前端少判一个分支）
    assert QQAutoReplyPlugin._auto_start_fields({}) == {
        "auto_started": False, "auto_start_status": "", "auto_start_error": ""}
