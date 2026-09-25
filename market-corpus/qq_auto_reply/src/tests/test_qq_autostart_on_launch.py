"""自启：插件启动时自动拉起 NapCat 并接上自动回复。

是 opt-in（``auto_start_on_launch``，默认关）：NapCat 为注入会接管 QQ 进程
（必要时结束掉正在运行的那个），不该由插件替用户决定。

三条最要紧的约定：

1. **``startup()`` 里既不建任务、也不内联跑**，只留 ``_deferred_startup_tasks``
   标记；真正起任务的是 ``_on_command_loop_start``（宿主在常驻 loop 上主动调，
   **不需要插件界面被打开**），各 entry 分流里另挂一份兜底。
   自启、trust 池推送、旧消息清理都走这条路。

   宿主各阶段的 loop 不一样：startup 是 ``asyncio.run(...)`` 开的**一次性** loop，
   入口才跑在**常驻** loop 上（定时任务、自定义事件也各自 ``asyncio.run``，同样
   一次性）。所以在 startup 里：

   - ``create_task`` 出来的任务活不过钩子返回（实测 1 秒内被取消，日志只剩一行
     「[自启] 被取消」，表现成"开了自启还要手点启动"）；
   - 内联更糟 —— ``create_subprocess_exec`` 会把 NapCat 子进程句柄绑死在一条
     已关闭的 loop 上，之后再也杀不掉它。

2. **顺序依连接模式而定**：反向（``napcat``）插件是监听方 —— 自动回复必须先起来；
   正向（``napcat_forward``）插件是拨号方 —— NapCat 得先在。

另外**不等 OneBot 就绪**：那只是报个状态，两个模式的连接都自愈，等它只会把最多
20 秒压在插件握手上。
"""
from __future__ import annotations

import asyncio
import inspect
from types import MethodType, SimpleNamespace

import pytest
from plugin.plugins.qq_auto_reply import QQAutoReplyPlugin
from plugin.sdk.plugin import Err, Ok, SdkError

REVERSE = {"auto_start_on_launch": True, "qq_connection_mode": "napcat"}
FORWARD = {"auto_start_on_launch": True, "qq_connection_mode": "napcat_forward"}


def _plugin(settings: dict, *, startup_error: str = "", napcat_raises=None,
            start_result=None):
    calls: list[str] = []

    async def _ensure():
        calls.append("ensure")
        if napcat_raises is not None:
            raise napcat_raises

    async def _wait():
        calls.append("wait")
        return True

    async def _start():
        calls.append("start")
        return start_result if start_result is not None else Ok({"status": "started"})

    logs: list[str] = []
    p = SimpleNamespace(
        _qq_settings=settings,
        _emit_log=lambda level, msg: logs.append(f"{level}:{msg}"),
        napcat_service=SimpleNamespace(
            ensure_napcat_started=_ensure,
            get_startup_error=lambda: startup_error,
            wait_for_onebot_ready=_wait,
        ),
        runtime_ops_service=SimpleNamespace(start_auto_reply=_start),
    )
    p._autostart_on_launch = MethodType(QQAutoReplyPlugin._autostart_on_launch, p)
    return p, calls, logs


# ── 关着的时候什么都不做 ────────────────────────────────────

async def test_disabled_does_nothing():
    p, calls, logs = _plugin({})

    await p._autostart_on_launch()

    assert calls == []
    assert logs == []


async def test_explicit_false_does_nothing():
    p, calls, _ = _plugin({"auto_start_on_launch": False})

    await p._autostart_on_launch()

    assert calls == []


# ── 反向模式：先把监听竖起来 ────────────────────────────────

async def test_reverse_mode_starts_the_listener_before_napcat():
    """插件是监听方 —— 监听没竖起来，NapCat 拨进来也没人接。"""
    p, calls, logs = _plugin(REVERSE)

    await p._autostart_on_launch()

    assert calls == ["start", "ensure"], "自动回复必须在起 NapCat 之前"
    assert any("完成" in m for m in logs)


# ── 正向模式：NapCat 得先在 ─────────────────────────────────

async def test_forward_mode_keeps_napcat_first():
    """插件是拨号方 —— 顺序与反向相反，不能一刀切。"""
    p, calls, _ = _plugin(FORWARD)

    await p._autostart_on_launch()

    assert calls == ["ensure", "start"]


# ── 不等 OneBot 就绪 ────────────────────────────────────────

async def test_never_waits_for_onebot():
    """等它只是报个状态，而连接是自愈的 —— 白白把最多 20 秒压在插件握手上。"""
    for settings in (REVERSE, FORWARD):
        p, calls, _ = _plugin(settings)
        await p._autostart_on_launch()
        assert "wait" not in calls, f"{settings['qq_connection_mode']} 下又去等 OneBot 了"


# ── NapCat 有硬错误 ─────────────────────────────────────────

async def test_reverse_mode_still_reports_a_hard_napcat_error():
    """反向模式下监听已经起来了，NapCat 起不来照样要报 —— 只是不再"跳过运行时"。

    监听空转是无害的（用户之后手动拉起 NapCat 一样能连上），这与一键部署的行为
    一致；但错误必须让用户看见。
    """
    p, calls, logs = _plugin(REVERSE, startup_error="找不到启动器")

    await p._autostart_on_launch()

    assert calls == ["start", "ensure"]
    assert any("找不到启动器" in m for m in logs)


async def test_forward_hard_error_skips_the_runtime():
    """正向模式保持原契约：NapCat 起不来就别硬起自动回复，只会再报一个与真因无关的错。"""
    p, calls, logs = _plugin(FORWARD, startup_error="找不到启动器")

    await p._autostart_on_launch()

    assert calls == ["ensure"]
    assert any("找不到启动器" in m for m in logs)


# ── 失败姿态 ────────────────────────────────────────────────

async def test_exception_is_swallowed_and_logged():
    """自启失败不该把插件带下去 —— 插件本身是好的，用户还能手动开。"""
    p, calls, logs = _plugin(REVERSE, napcat_raises=RuntimeError("QQ 被占用"))

    await p._autostart_on_launch()          # 不抛

    assert calls == ["start", "ensure"]
    assert any("QQ 被占用" in m for m in logs)


async def test_runtime_rejection_is_logged_not_raised():
    p, calls, logs = _plugin(REVERSE,
                             start_result=Err(SdkError("NOT_INITIALIZED: 未初始化")))

    await p._autostart_on_launch()

    assert calls == ["start", "ensure"]
    assert any("完成" in m for m in logs)   # 被拒由服务层自己报


async def test_cancellation_is_logged_and_reraised():
    """取消是 ``BaseException``，被 ``except Exception`` 漏掉 = 静默消失。

    这条路上真发生过：日志里只剩孤零零一行「被取消」，别的什么都没有。
    """
    p, _, logs = _plugin(REVERSE, napcat_raises=asyncio.CancelledError())

    with pytest.raises(asyncio.CancelledError):
        await p._autostart_on_launch()

    assert any("被取消" in m for m in logs), "取消必须留一行日志"


# ── 每一步都留痕 ────────────────────────────────────────────

async def test_every_stage_is_logged():
    p, _, logs = _plugin(REVERSE)

    await p._autostart_on_launch()

    joined = "\n".join(logs)
    for marker in ("[自启] 开始", "1/3", "2/3", "完成"):
        assert marker in joined, f"少了阶段日志: {marker}"


# ── 起在常驻 loop 上 ────────────────────────────────────────

def test_startup_leaves_a_pending_flag_instead_of_tasks():
    """**这条是整件事的核心。**

    startup 钩子跑在 ``asyncio.run`` 开的一次性 loop（L1）上，入口才在常驻 loop
    （L2）上。所以 startup 里既不能 ``create_task``（任务活不过钩子返回），也不能
    内联（``create_subprocess_exec`` 会把 NapCat 句柄绑死在一条已关闭的 loop 上）。
    """
    src = inspect.getsource(QQAutoReplyPlugin.startup)

    assert "self._deferred_startup_tasks = True" in src, "没有留下待办标记"
    for bad in ("create_task(self._autostart_on_launch",
                "create_task(self._purge_old_reviewed_loop",
                "create_task(self._session_housekeeping_loop",
                "push_legacy_speaker_trust_forever()\n            )"):
        assert bad not in src, f"startup 里又建后台任务了：{bad!r}"
    assert "await self._autostart_on_launch()" not in src, (
        "在 startup 里内联跑自启了 —— NapCat 子进程会被绑死在一次性的 loop 上")


def _kick_plugin(**overrides):
    started: list[str] = []

    async def _mk(name):
        started.append(name)

    p = SimpleNamespace(
        _deferred_startup_tasks=True,
        _autostart_on_launch=lambda: _mk("autostart"),
        _purge_old_reviewed_loop=lambda: _mk("purge"),
        settings_service=SimpleNamespace(
            push_legacy_speaker_trust_forever=lambda: _mk("trust")),
    )
    for key, value in overrides.items():
        setattr(p, key, value)
    p._kick_deferred_startup_tasks = MethodType(
        QQAutoReplyPlugin._kick_deferred_startup_tasks, p)
    p._on_command_loop_start = MethodType(QQAutoReplyPlugin._on_command_loop_start, p)
    return p, started


async def test_kick_starts_all_the_deferred_tasks_once():
    p, started = _kick_plugin()

    p._kick_deferred_startup_tasks()

    assert p._deferred_startup_tasks is False, "标记没清掉 —— 每次入口调用都会再踢一次"
    tasks = [p._autostart_task, p._trust_migration_task, p._purge_task]
    p._kick_deferred_startup_tasks()          # 第二次必须是空转
    assert [p._autostart_task, p._trust_migration_task, p._purge_task] == tasks

    await asyncio.gather(*tasks)
    assert sorted(started) == ["autostart", "purge", "trust"]


async def test_kick_is_a_noop_without_a_pending_flag():
    p, _ = _kick_plugin(_deferred_startup_tasks=False)

    p._kick_deferred_startup_tasks()

    assert getattr(p, "_autostart_task", None) is None


async def test_the_host_hook_kicks_without_any_ui():
    """**主路径**：宿主在常驻 loop 上主动调 ``_on_command_loop_start``。

    这是插件里唯一一个"由宿主主动调、且跑在常驻 loop 上"的地方 —— 有了它，
    自启**不需要插件界面被打开**（入口轮询那条只是兜底）。
    """
    p, started = _kick_plugin()

    await p._on_command_loop_start()

    tasks = [p._autostart_task, p._trust_migration_task, p._purge_task]
    await asyncio.gather(*tasks)
    assert sorted(started) == ["autostart", "purge", "trust"]


def test_entry_dispatchers_also_kick_as_a_fallback():
    """兜底：老版本宿主可能不调 ``_on_command_loop_start``。

    入口是插件里另一个跑在常驻 loop 上的地方，所以这条退路本身是对的 ——
    只是要等界面轮询 ``query`` 才轮到它。标记幂等，两条路不会重复起。
    """
    for name in ("_query_dispatch", "_runtime_dispatch",
                 "_config_dispatch", "_deploy_dispatch"):
        src = inspect.getsource(getattr(QQAutoReplyPlugin, name))
        assert "_kick_deferred_startup_tasks()" in src, f"{name} 没有踢待办任务"
