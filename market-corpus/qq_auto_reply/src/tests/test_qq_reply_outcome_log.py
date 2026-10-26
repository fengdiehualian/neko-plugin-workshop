# -*- coding: utf-8 -*-
"""**唯一出口日志**：每一轮回答不管什么结局，都要留下一行 `[Reply] … 结局: …`。

真机来历（2026-09-29 15:28，群 985066274）：使用者问「什么?!」→ 门控**接了**
（`[Necessity] … 接（score=55 …）`）→ `AI 生成回复完成 (length: 24)` → **然后什么都没有**：
没有 `[Buffer] 排定投递`、没有 `[Send]`、也没有任何 warning。全天 grep 确认那条 24 字回复
**从没发出去**，而日志答不出"为什么"——他看到的就是"她没有后续"。

这一层以前只在**成功**那条路有日志，几条"生成了却没发出去"的出口**一行都不打**：
空回复（模型没给可发内容）、缓冲按 `ignore`/`relay` 收场（从不调 `schedule_reply`）、
发送前授权被撤销、以及"没有可投递的块"。现在每一轮都收敛到一行，原因由
`_note_outcome()` 记下、`run()` 打出来。

本文件钉四件事：

1. 每一轮**恰好一行** `[Reply]`（不是零行，也不是每加一个出口就多一行）；
2. 那行必须说清**结局**（已投递/未投递）与**原因**（决策 ignore、空回复、缓冲等待、授权撤销…）；
3. 异常也算结局：先记一行再往上抛，栈照旧不丢；
4. 原因用 `ContextVar` 不串台：多会话并发跑时不互相覆盖，嵌套一轮结束也不清掉外层的。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from plugin.plugins.qq_auto_reply import reply_pipeline as rp
from plugin.plugins.qq_auto_reply.pipeline_models import (
    QQDeliveryPlan,
    QQDeliveryResult,
    QQMessageBlock,
    QQReplyDecision,
    QQReplyOutcome,
    QQReplyRequest,
)
from plugin.plugins.qq_auto_reply.reply_pipeline import QQReplyPipelineRunner

GROUP = "985066274"


def _plugin(logs: list[str]) -> SimpleNamespace:
    plugin = SimpleNamespace(
        _emit_log=lambda level, msg: logs.append(f"[{level}] {msg}"),
        logger=SimpleNamespace(
            info=lambda msg, *a, **k: logs.append(str(msg)),
            warning=lambda msg, *a, **k: logs.append(str(msg)),
        ),
        _build_session_key=lambda *, sender_id, is_group, group_id=None: (
            f"group:{group_id}" if is_group else f"private:{sender_id}"
        ),
        _qq_settings={},
        session_memory_service=SimpleNamespace(record_tail_undelivered_ai_row=lambda *a, **k: None),
    )
    plugin.reply_buffer_service = None
    return plugin


def _request() -> QQReplyRequest:
    return QQReplyRequest(message_text="什么?!", sender_id="820040531", is_group=True, group_id=GROUP)


def _outcome_lines(logs: list[str]) -> list[str]:
    return [line for line in logs if line.startswith("[Reply] ")]


# ── 1 & 2：决策不接 / 已投递 ──────────────────────────────────────────

def test_the_ignore_path_logs_exactly_one_outcome_line():
    logs: list[str] = []
    plugin = _plugin(logs)
    plugin.reply_decision_node = SimpleNamespace(decide=lambda request: QQReplyDecision(
        action="ignore", permission_level="admin",
        attention_gate_reason="non_focus(focus=1048307485,score=4.0)",
    ))
    runner = QQReplyPipelineRunner(plugin)

    outcome = asyncio.run(runner.run(_request()))

    assert outcome.action == "ignore"
    lines = _outcome_lines(logs)
    assert len(lines) == 1, f"每一轮恰好一行: {lines}"
    assert "未投递" in lines[0] and "ignore" in lines[0]
    assert "non_focus" in lines[0], "门控给的原因要带出来，否则还是等于没说"
    assert GROUP in lines[0], "行里要有会话，多个群同时在跑时才分得清"


def test_a_delivered_round_logs_one_line(monkeypatch):
    logs: list[str] = []
    runner = QQReplyPipelineRunner(_plugin(logs))
    context = SimpleNamespace(
        permission_level="admin", is_group=True, group_id=GROUP, memory_context_used=False,
        persist_memory=None, scene_mode="", group_scene_mode="", core_memory_text="",
        recalled_memory_text="", traces=[],
    )
    outcome = QQReplyOutcome(action="reply", reply_text="在的呀", traces=[])
    model_result = SimpleNamespace(
        history_ai_row=None, traces=[], source="session", used_fallback=False, timed_out=False,
        allow_fallback=True, fallback_reason="", reply_text="在的呀",
    )
    plan = QQDeliveryPlan(target_type="group", target_id=GROUP, blocks=[QQMessageBlock(text="在的呀")])

    monkeypatch.setattr(runner, "_run_decision", lambda request: QQReplyDecision(
        action="reply", permission_level="admin"))
    monkeypatch.setattr(runner, "_run_context", _async(context))
    monkeypatch.setattr(runner, "_run_model", _async(model_result))
    monkeypatch.setattr(runner, "_run_postprocess", _async(outcome))
    monkeypatch.setattr(runner, "_build_delivery_plan", lambda request, outcome: plan)
    monkeypatch.setattr(runner, "_run_delivery", _async(
        QQDeliveryResult(delivered=True, target_type="group", target_id=GROUP, reply_text="在的呀")))

    result = asyncio.run(runner.run(_request()))

    assert result.delivery_result.delivered is True
    lines = _outcome_lines(logs)
    assert len(lines) == 1, f"每一轮恰好一行: {lines}"
    assert "已投递" in lines[0] and GROUP in lines[0]


def _async(value):
    async def _call(*_a, **_k):
        return value
    return _call


# ── 3：几条"静默"出口现在都带原因 ────────────────────────────────────

def _delivery_runner(logs: list[str], *, blocks, reply_text: str = "", buffer_on: bool = True):
    plugin = _plugin(logs)
    buffer = SimpleNamespace(
        is_enabled=lambda settings, is_group: buffer_on,
        send_pause_seconds=lambda *, private, settings: 3.2,
        schedule_reply=_async(True),
    )
    plugin.reply_buffer_service = buffer
    runner = QQReplyPipelineRunner(plugin)
    plan = QQDeliveryPlan(target_type="group", target_id=GROUP, blocks=blocks)
    outcome = QQReplyOutcome(action="reply", reply_text=reply_text, traces=[])
    return runner, plan, outcome


def test_an_empty_reply_now_carries_a_reason():
    """以前这条出口一行都不打 —— 真机那次"生成了没发出去"就落在这类静默出口上。"""
    logs: list[str] = []
    runner, plan, outcome = _delivery_runner(logs, blocks=[QQMessageBlock()])

    async def _drive():
        result = await runner._run_delivery(plan, _request(), outcome, context=None)
        return result, rp._OUTCOME_NOTE.get()

    result, note = asyncio.run(_drive())

    assert result.delivered is False
    assert note, "空回复这条出口必须留下原因"
    assert "空回复" in note


def test_an_empty_plan_is_not_blamed_on_the_connector():
    """计划为空（`deliver()` 返回 None）**不许**说成"连接器没有回执"。

    真机 2026-09-29 17:0x（群 1048307485 连续 6 轮）：模型每轮都回
    `<feeling>…</feeling><msg></msg>`（`length: 36`，每轮一模一样）→ 后处理既没有
    blocks 也没有正文 → `_build_delivery_plan` 返回 None → 直投分支里 `deliver(None)`
    立刻返回 None。当时那行写的是「投递未确认：连接器没有回执」，使用者读成"她发不出去"，
    真相是"她这一轮选择不说话" —— 传输层替内容层背了锅。
    """
    logs: list[str] = []
    runner, _plan, outcome = _delivery_runner(logs, blocks=[], reply_text="")
    # 空计划的真实形态：`deliver()` 在开头 `if not plan or not plan.blocks` 就返回 None，
    # 一个字节都不发、也不打 `[Send] … **未投递**（blocks=…）` 那行诊断。
    runner.plugin.reply_delivery_node = SimpleNamespace(deliver=_async(None))

    async def _drive():
        result = await runner._run_delivery(None, _request(), outcome, context=None)
        return result, rp._OUTCOME_NOTE.get()

    result, note = asyncio.run(_drive())

    assert note, "计划为空这条出口必须留下原因"
    assert "空回复" in note, f"又说成传输问题了：{note!r}"
    assert "连接器" not in note, f"计划为空跟连接器无关：{note!r}"
    # 原始输出必须一起落盘 —— 这一层只看得到"计划是空的"，"为什么空"只有原文能回答。
    assert any("计划为空" in line and "原始输出" in line for line in logs), (
        f"没把模型的原始输出打出来，下次还是查不出'为什么空'：{logs}"
    )
    # deliver() 那句 `if not plan or not plan.blocks: return None` 是唯一来源 ——
    # 结果对象本身可以为 None，上层只看 `delivered`。
    assert result is None or getattr(result, "delivered", False) is False


def test_the_buffer_exit_says_how_long_and_how_many_blocks():
    logs: list[str] = []
    runner, plan, outcome = _delivery_runner(
        logs, blocks=[QQMessageBlock(text="在的呀")], reply_text="在的呀")

    async def _drive():
        result = await runner._run_delivery(plan, _request(), outcome, context=None)
        return result, rp._OUTCOME_NOTE.get()

    result, note = asyncio.run(_drive())

    assert result.delivered is True
    assert "缓冲" in note and "1 块" in note, note


def test_a_revoked_consent_exit_carries_a_reason(monkeypatch):
    # 缓冲关掉：否则这一轮会先在缓冲那条分支返回，走不到授权闸。
    logs: list[str] = []
    runner, plan, outcome = _delivery_runner(
        logs, blocks=[QQMessageBlock(text="在的呀")], buffer_on=False)
    monkeypatch.setattr(runner, "_consent_revoked_before_send", lambda context: True)

    async def _drive():
        result = await runner._run_delivery(plan, _request(), outcome, context=SimpleNamespace())
        return result, rp._OUTCOME_NOTE.get()

    result, note = asyncio.run(_drive())

    assert result.delivered is False
    assert "撤销" in note


# ── 4：异常也是结局；原因不串台 ──────────────────────────────────────

def test_an_exception_logs_an_outcome_line_and_still_raises():
    logs: list[str] = []
    plugin = _plugin(logs)

    def _boom(request):
        raise RuntimeError("决策炸了")

    plugin.reply_decision_node = SimpleNamespace(decide=_boom)
    runner = QQReplyPipelineRunner(plugin)

    with pytest.raises(RuntimeError):
        asyncio.run(runner.run(_request()))

    lines = _outcome_lines(logs)
    assert len(lines) == 1 and "中断" in lines[0] and "RuntimeError" in lines[0]


def test_a_new_round_does_not_inherit_the_previous_reason():
    """原因不串台：`run()` 进来先清空，所以上一轮的原因不会出现在这一轮的结局行里。

    （注意不能用"另起一个 asyncio.run 就该是空的"来测 —— `asyncio.run` 的任务**继承**创建
    时的 context，真机里串台的风险来自"同一实例上挂属性"，所以判据是 per-round 的清空。）
    """
    logs: list[str] = []
    plugin = _plugin(logs)
    plugin.reply_decision_node = SimpleNamespace(decide=lambda request: QQReplyDecision(
        action="ignore", permission_level="admin"))
    runner = QQReplyPipelineRunner(plugin)

    rp._note_outcome("上一轮的原因")
    asyncio.run(runner.run(_request()))

    lines = _outcome_lines(logs)
    assert len(lines) == 1
    assert "上一轮的原因" not in lines[0], "这一轮的行只能带这一轮的原因"
    assert "ignore" in lines[0]


def test_a_nested_round_does_not_clear_the_outer_reason():
    async def _drive() -> str:
        rp._note_outcome("外层的一轮")
        token = rp._OUTCOME_NOTE.set("")       # 嵌套一轮进来时和 run() 做的事一样
        try:
            rp._note_outcome("内层的一轮")
            assert rp._OUTCOME_NOTE.get() == "内层的一轮"
        finally:
            rp._OUTCOME_NOTE.reset(token)
        return rp._OUTCOME_NOTE.get()

    assert asyncio.run(_drive()) == "外层的一轮"
