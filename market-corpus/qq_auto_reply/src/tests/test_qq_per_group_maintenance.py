"""按群维护循环：谁的群记忆有增量谁推摘要（按**每群自己的**节奏）。

2026-09-29 这一天这条循环上原本有两件事：群记忆摘要 + 冷场破冰。**主动破冰整套已删除**
（使用者口径「干脆不要这个先」—— 它的触发判据是"静默 1800 秒"，而使用者要求不要用时间
判断；四个事件驱动备选给他选之后他选了先不要）。墓碑见 `docs/SESSION-HANDOFF.md` §42。

剩下这件事的判据全部来自**每个群自己的状态**（推送游标 + 上次推送时刻），与别的群无关。
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from plugin.plugins.qq_auto_reply.attention_gate_service import QQAttentionGateService

GROUP_A = "1048307485"
GROUP_B = "985066274"
NOW = 1_000_000


class _FakeAttention:
    """最小注意力桩：给出群清单与每群状态（状态字段由用例自己摆）。"""

    def __init__(self, states: dict[str, SimpleNamespace] | None = None, now: int = NOW):
        self.states = states if states is not None else {}
        self._now = now
        self.persisted = 0

    def _enabled(self) -> bool:
        return True

    def _current_time(self) -> int:
        return self._now

    def _normalized_groups(self) -> list[str]:
        return list(self.states)

    def get_state(self, group_id: str) -> SimpleNamespace:
        return self.states[str(group_id)]

    def participates_in_attention(self, group_id: str) -> bool:
        return True


def _state(**kwargs) -> SimpleNamespace:
    base = dict(group_id="", attention_score=1.0, last_message_at=0, lock_until=0)
    base.update(kwargs)
    return SimpleNamespace(**base)


class _Gate(QQAttentionGateService):
    """把摘要推送替换成可观察的记账，专注测"判据"。"""

    def __init__(self, plugin):
        super().__init__(plugin)
        self.digests: list[str] = []

    async def _push_group_digest(self, group_id: str) -> None:
        self.digests.append(group_id)


def _gate(states: dict[str, SimpleNamespace], settings: dict | None = None) -> _Gate:
    plugin = SimpleNamespace(
        attention_service=_FakeAttention(states),
        logger=SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None),
        _emit_log=lambda *a, **k: None,
        _qq_settings=dict(settings or {}),
    )
    return _Gate(plugin)


# ── 群记忆摘要：按每群自己的节奏推 ────────────────────────────────

def test_digest_is_pushed_on_its_own_interval():
    """两次推送之间要隔够 `group_memory_digest_interval_seconds`。"""
    gate = _gate(
        {GROUP_A: _state()},
        {"group_memory_enabled": True, "group_memory_digest_interval_seconds": 300},
    )

    assert asyncio.run(gate._maybe_push_digest(GROUP_A, NOW)) is True
    assert asyncio.run(gate._maybe_push_digest(GROUP_A, NOW + 60)) is False
    assert asyncio.run(gate._maybe_push_digest(GROUP_A, NOW + 301)) is True
    assert gate.digests == [GROUP_A, GROUP_A]


def test_digest_interval_is_per_group():
    """一个群推过不影响另一个群（每群自己的节奏）。"""
    gate = _gate(
        {GROUP_A: _state(), GROUP_B: _state()},
        {"group_memory_enabled": True, "group_memory_digest_interval_seconds": 300},
    )

    asyncio.run(gate._maybe_push_digest(GROUP_A, NOW))
    asyncio.run(gate._maybe_push_digest(GROUP_B, NOW))

    assert gate.digests == [GROUP_A, GROUP_B]


def test_digest_is_skipped_when_group_memory_is_off():
    """群记忆是显式 opt-in —— 关着就一条都不许推。"""
    gate = _gate({GROUP_A: _state()}, {"group_memory_enabled": False})

    assert asyncio.run(gate._maybe_push_digest(GROUP_A, NOW)) is False
    assert gate.digests == []


def test_digest_can_be_disabled_with_zero_interval():
    gate = _gate(
        {GROUP_A: _state()},
        {"group_memory_enabled": True, "group_memory_digest_interval_seconds": 0},
    )

    assert asyncio.run(gate._maybe_push_digest(GROUP_A, NOW)) is False


# ── 一整轮维护：所有群都过一遍 ────────────────────────────────────

def test_maintenance_tick_covers_every_group():
    """一轮维护要把每个群都过一遍。"""
    states = {GROUP_A: _state(), GROUP_B: _state()}
    gate = _gate(states, {"group_memory_enabled": True})

    asyncio.run(gate.run_maintenance_tick())

    assert sorted(gate.digests) == sorted([GROUP_A, GROUP_B]), "摘要该覆盖所有群"


def test_maintenance_tick_survives_a_broken_group():
    """某个群的状态坏掉不许让整轮停摆（否则后面的群永远轮不到）。"""
    class _Exploding(_FakeAttention):
        def get_state(self, group_id: str):
            if group_id == GROUP_A:
                raise RuntimeError("boom")
            return self.states[group_id]

    plugin = SimpleNamespace(
        attention_service=_Exploding({GROUP_B: _state()}),
        logger=SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None),
        _emit_log=lambda *a, **k: None,
        _qq_settings={"group_memory_enabled": True},
    )
    gate = _Gate(plugin)

    asyncio.run(gate.run_maintenance_tick())

    # A 不在群清单里（桩只给 B），这里要钉的是"不抛异常"：
    assert gate.digests == [GROUP_B]


def test_maintenance_tick_does_not_touch_icebreaker_state():
    """这条循环现在**只**推摘要 —— 破冰相关的钩子已经不存在了。

    结构性断言：`_maybe_break_ice` / `_try_icebreaker` / `_icebreaker_idle_seconds`
    都不该在服务上（谁把它们加回来，这条会红）。
    """
    gate = _gate({GROUP_A: _state()})

    for name in ("_maybe_break_ice", "_try_icebreaker", "_icebreaker_idle_seconds",
                 "_icebreaker_hold_seconds", "_pick_proactive_topic",
                 "_DEFAULT_PROACTIVE_TOPICS", "_last_icebreaker_at"):
        assert not hasattr(gate, name), f"{name} 又回来了（主动破冰已删除）"


def test_maintenance_loop_keeps_running_after_a_failing_round():
    """单轮炸了循环必须活着 —— 死掉的循环只表现为"群记忆再也不推了"。"""
    gate = _gate({GROUP_A: _state(last_message_at=NOW - 7200)})
    calls: list[int] = []

    async def failing_tick() -> None:
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("第一轮炸了")

    gate.run_maintenance_tick = failing_tick  # type: ignore[method-assign]

    async def run_three_rounds() -> None:
        task = asyncio.create_task(gate._maintenance_loop(0.01))
        for _ in range(60):
            if len(calls) >= 3:
                break
            await asyncio.sleep(0.01)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(run_three_rounds())

    assert len(calls) >= 3, "第一轮的异常把维护循环杀死了"


def test_stop_proactive_loop_is_idempotent():
    """没启动过 / 停两次都不许炸（插件停止时会调）。"""
    gate = _gate({GROUP_A: _state()})

    asyncio.run(gate.stop_proactive_loop())
    asyncio.run(gate.start_proactive_loop())
    asyncio.run(gate.stop_proactive_loop())
    asyncio.run(gate.stop_proactive_loop())


def test_starting_the_loop_leaves_a_log_line():
    """启动时留一行凭据。

    这条循环是纯后台的（推群记忆），"它到底起没起"在没有这行日志时完全无法区分
    "循环没起来"和"群里就是没有增量"。
    """
    lines: list[str] = []
    plugin = SimpleNamespace(
        attention_service=_FakeAttention({GROUP_A: _state()}),
        logger=SimpleNamespace(info=lambda msg, *a, **k: lines.append(str(msg)),
                               warning=lambda *a, **k: None),
        _emit_log=lambda *a, **k: None,
        _qq_settings={},
    )
    gate = _Gate(plugin)

    async def run() -> None:
        await gate.start_proactive_loop()
        await gate.stop_proactive_loop()

    asyncio.run(run())

    assert any("按群维护循环已启动" in line for line in lines), lines


def test_the_loop_starts_once_per_process():
    """宿主把插件初始化跑不止一遍 —— 第二条循环必须被挡住。"""
    from plugin.plugins.qq_auto_reply import attention_gate_service as gate_module

    def _make(lines: list[str]) -> _Gate:
        plugin = SimpleNamespace(
            attention_service=_FakeAttention({GROUP_A: _state()}),
            logger=SimpleNamespace(info=lambda msg, *a, **k: lines.append(str(msg)),
                                   warning=lambda *a, **k: None),
            _emit_log=lambda *a, **k: None,
            _qq_settings={},
        )
        return _Gate(plugin)

    first_lines: list[str] = []
    second_lines: list[str] = []
    first = _make(first_lines)
    second = _make(second_lines)

    async def run() -> None:
        try:
            await first.start_proactive_loop()
            await second.start_proactive_loop()      # 第二个实例（宿主第二次初始化）
            assert second._maintenance_task is None, "第二个实例也起了一条循环"
        finally:
            await second.stop_proactive_loop()
            await first.stop_proactive_loop()

    asyncio.run(run())

    assert sum("按群维护循环已启动" in line for line in first_lines) == 1
    assert not any("按群维护循环已启动" in line for line in second_lines), second_lines
    assert gate_module._maintenance_owner() is None, "停掉之后归属者要清掉，否则下次重载起不来"


def test_the_single_flight_marker_survives_a_module_reimport():
    """单飞标记挂在 `sys` 上，不是本模块的全局。

    插件重载会把本模块**重新导入**（新模块对象 = 新的模块全局），标记若放在模块全局里
    就会跟着重置 —— 而"两条循环同时在跑"正是重载/多次初始化时才出现的问题。
    """
    import sys

    from plugin.plugins.qq_auto_reply import attention_gate_service as gate_module

    plugin = SimpleNamespace(
        attention_service=_FakeAttention({GROUP_A: _state()}),
        logger=SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None),
        _emit_log=lambda *a, **k: None,
        _qq_settings={},
    )
    gate = _Gate(plugin)

    async def run() -> None:
        try:
            await gate.start_proactive_loop()
            assert getattr(sys, gate_module._MAINTENANCE_OWNER_ATTR) is gate, (
                "标记不在 sys 上 —— 重载后再导入一次本模块，闸门就没了"
            )
        finally:
            await gate.stop_proactive_loop()

    asyncio.run(run())


def test_a_stopped_loop_can_be_started_again():
    """重载/重连都要能再起一条（归属者清理干净了）。"""
    lines: list[str] = []
    plugin = SimpleNamespace(
        attention_service=_FakeAttention({GROUP_A: _state()}),
        logger=SimpleNamespace(info=lambda msg, *a, **k: lines.append(str(msg)),
                               warning=lambda *a, **k: None),
        _emit_log=lambda *a, **k: None,
        _qq_settings={},
    )
    gate = _Gate(plugin)

    async def run() -> None:
        await gate.start_proactive_loop()
        await gate.stop_proactive_loop()
        await gate.start_proactive_loop()
        await gate.stop_proactive_loop()

    asyncio.run(run())

    assert sum("按群维护循环已启动" in line for line in lines) == 2, lines
