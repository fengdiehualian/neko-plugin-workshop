# -*- coding: utf-8 -*-
"""频率软提示：**现在它是频率的唯一机制**（原来只是硬闸前面的一道坡）。

历史：这道软提示当初是为硬闸配的 —— 硬闸（`reply_burst_max_replies` 条 /
`reply_burst_window_seconds`）到点**直接静默**，用户看到的是"她突然不理我了"，
所以先在提示词里让她自己收敛，硬闸当兜底。

2026-09-27 使用者口径「不要这个，有注意力控制频率了」→ **硬闸删除**（墓碑与三条理由见
`attention_gate_service`，删除契约见 `test_qq_no_burst_gate.py`）。于是这里测的东西
从"闸前的坡"变成了"唯一在管频率的那一层"：到参考条数的比例提醒少说/说短，
**超过参考条数再给更强的一档**（这一档以前不可达 —— 硬闸会把第 N+1 条直接拦掉，
计数根本到不了 limit 之上）。

口径仍与那两个 `reply_burst_*` 键共用（不新开一份窗口/参考条数）：两处漂移的话，
提示词里说的"你最近说了几条"与实际统计会各说各话。
"""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace

from plugin.plugins.qq_auto_reply.attention_service import QQAttentionService

GROUP = "1048307485"

SETTINGS = {
    "attention_max_score": 10.0,
    "attention_focus_threshold": 4.0,
    "attention_focus_hold_threshold": 2.0,
    "attention_min_threshold": 1.0,
    "attention_emotion_multipliers": {"calm": 0.0},
    "backlog_labels": [],
    "reply_burst_window_seconds": 60,
    "reply_burst_max_replies": 3,
    "pacing_hint_enabled": True,
    "pacing_hint_ratio": 0.6,
}


class _Clock:
    def __init__(self, start: int = 100_000) -> None:
        self.now = start

    def __call__(self) -> int:
        return self.now


def _service(**overrides):
    settings = dict(SETTINGS)
    settings.update(overrides)
    plugin = SimpleNamespace(
        group_permission_mgr=SimpleNamespace(list_groups=lambda: [{"group_id": GROUP}]),
        _qq_settings=settings,
        backlog_store=None,
        permission_mgr=None,
        _emit_log=lambda *a, **k: None,
        logger=logging.getLogger("qq.attention"),
    )
    svc = QQAttentionService(plugin)
    clock = _Clock()
    svc._current_time = clock
    return svc, clock


def _reply(svc, gid=GROUP):
    asyncio.run(svc.update_on_reply(gid))


# ── 计数口径 ────────────────────────────────────────────────────────

def test_replies_are_counted_within_the_window():
    svc, clock = _service()
    assert svc.recent_reply_count(GROUP) == 0
    _reply(svc)
    clock.now += 5
    _reply(svc)
    assert svc.recent_reply_count(GROUP) == 2


def test_window_expiry_drops_old_replies():
    svc, clock = _service()
    _reply(svc)
    clock.now += 61          # 窗口 60 秒
    assert svc.recent_reply_count(GROUP) == 0


def test_other_groups_do_not_share_the_counter():
    svc, clock = _service()
    _reply(svc, GROUP)
    assert svc.recent_reply_count("985066274") == 0


# ── 触发区间 ────────────────────────────────────────────────────────

def test_hint_appears_from_the_second_reply_and_gets_stronger_at_the_reference():
    """参考 3 条 + 0.6 → 第 2 条开始提醒；**超过参考条数后换成更强的一档**。

    2026-09-27 之后这里不再有硬闸（使用者：「不要这个，有注意力控制频率了」），
    所以"到第 3 条就不再提醒"那条老行为改成了"第 3 条起提醒得更直接"。
    """
    svc, clock = _service()
    _reply(svc)
    assert svc.pacing_hint(GROUP) == "", "第一条之后不该提醒"

    clock.now += 3
    _reply(svc)
    hint = svc.pacing_hint(GROUP)
    assert "注意节奏" in hint and "已经说了 2 条" in hint
    assert "让给群友" not in hint, "还没到参考条数就用最强的一档"

    clock.now += 3
    _reply(svc)
    stronger = svc.pacing_hint(GROUP)
    assert "注意节奏" in stronger, "到参考条数之后反而闭嘴了 —— 现在没有硬闸接管"
    assert "比平时密了" in stronger and "让给群友" in stronger


def test_hint_keeps_firing_well_past_the_reference():
    """没有硬闸兜底之后，说得越多提示越应该在（而不是到点就消失）。"""
    svc, clock = _service()
    for _ in range(6):
        clock.now += 3
        _reply(svc)
    hint = svc.pacing_hint(GROUP)
    assert "已经说了 6 条" in hint and "让给群友" in hint


def test_hint_can_be_switched_off():
    svc, clock = _service(pacing_hint_enabled=False)
    for _ in range(2):
        clock.now += 3
        _reply(svc)
    assert svc.pacing_hint(GROUP) == ""


def test_ratio_moves_the_trigger_point():
    """比例 0.9 + 参考 10 条 → 第 9 条才开始提醒。"""
    svc, clock = _service(reply_burst_max_replies=10, pacing_hint_ratio=0.9)
    for _ in range(8):
        clock.now += 3
        _reply(svc)
    assert svc.pacing_hint(GROUP) == ""
    clock.now += 3
    _reply(svc)
    assert "注意节奏" in svc.pacing_hint(GROUP)


def test_window_and_limit_come_from_the_burst_keys():
    """口径仍与那两个 burst 键共用 —— 改键，提示跟着改（它们现在是软提示的参考频率）。"""
    svc, clock = _service(reply_burst_window_seconds=600, reply_burst_max_replies=5, pacing_hint_ratio=0.6)
    for _ in range(3):
        clock.now += 3
        _reply(svc)
    # 3 >= round(5*0.6)=3 → 提醒，且文案里的窗口是 600 秒
    assert "600 秒" in svc.pacing_hint(GROUP)


def test_the_hint_never_says_she_will_be_silenced():
    """措辞不许再暗示"会被强制静默"（那道闸已经删了，说了就是骗她）。"""
    svc, clock = _service()
    seen = []
    for _ in range(5):
        clock.now += 3
        _reply(svc)
        seen.append(svc.pacing_hint(GROUP))
    for hint in seen:
        assert "静默" not in hint and "上限" not in hint, f"措辞还在提闸: {hint}"


def test_hint_logs_when_injected(caplog):
    """注入时必须落一条**文件日志**（真机验收靠它确认提示有没有生效）。"""
    import logging as _logging

    svc, clock = _service()
    for _ in range(2):
        clock.now += 3
        _reply(svc)
    with caplog.at_level(_logging.INFO):
        svc.pacing_hint(GROUP)
    assert any("[Pacing]" in r.message for r in caplog.records), "软提示注入没有留日志"


# ── 注入路径 ────────────────────────────────────────────────────────

def test_hint_reaches_the_prompt_context():
    svc, clock = _service()
    for _ in range(2):
        clock.now += 3
        _reply(svc)
    context = svc.get_attention_context(GROUP)
    assert "注意节奏" in context, "提示没有进提示词 —— 那她永远不会知道该收敛"


def test_disabled_hint_never_reaches_the_prompt_context():
    svc, clock = _service(pacing_hint_enabled=False)
    for _ in range(2):
        clock.now += 3
        _reply(svc)
    assert "注意节奏" not in svc.get_attention_context(GROUP)
