# -*- coding: utf-8 -*-
"""看门狗：**只有 trusted 群参与注意力竞争**，normal 群要走"回 / 转达"那条路。

**为什么钉**（2026-09-27 使用者拍板「normal 群不参与注意力竞争」）：

门控里原来那道「非焦点群 → ignore」的跨群闸门 2026-09-29 已删除（使用者口径：
「每个群自己管自己的注意力」），所以现在 trusted 群按**自己的分数**决定搭不搭话，
normal 群照旧直接放行 —— 两者都不再受"别的群是不是焦点"影响。

现在：normal / none 群
* 不进 `update_on_message`（不累计分数）；
* 不被注意力闸拦；
* 直接以 `normal_group_passthrough` 放行，由权限层决定回还是转达；
* 被 @ 时照旧必回，但**不上锁不参与竞争**（它不在竞争里）。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from plugin.plugins.qq_auto_reply.attention_gate_service import QQAttentionGateService
from plugin.plugins.qq_auto_reply.attention_service import QQAttentionService
from plugin.plugins.qq_auto_reply.tests.test_qq_necessity_gate import _FakeAttention

GROUP = "g1"
OTHER = "g2"


class _TrackingAttention(_FakeAttention):
    """在 _FakeAttention 上记下"有没有被当成竞争者对待"。"""

    def __init__(self, *, score: float = 5.0):
        super().__init__(score=score)
        self.locked: list[str] = []
        self.marked: list[str] = []
        self.boosted: list[str] = []

    def lock_group(self, group_id: str) -> None:
        self.locked.append(group_id)

    def mark_focus(self, group_id: str) -> None:
        self.marked.append(group_id)

    def wake_boost(self, group_id: str) -> None:
        self.boosted.append(group_id)


def _plugin(*, level: str, attention=None) -> SimpleNamespace:
    return SimpleNamespace(
        attention_service=attention or _TrackingAttention(),
        qq_client=SimpleNamespace(needs_attention=True, _sent_message_ids={}),
        permission_mgr=None,
        group_permission_mgr=SimpleNamespace(get_group_level=lambda gid: level),
        logger=SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None),
        _qq_settings={"backlog_labels": [], "buffer_max_count": 17},
        _emit_log=lambda *a, **k: None,
        _run_with_session_lock=None,
        reply_buffer_service=None,
        session_memory_service=None,
        reply_pipeline=None,
        runtime_service=None,
        _admin_qq="0",
        _build_session_key=lambda **k: "",
    )


def _evaluate(plugin, **kwargs):
    gate = QQAttentionGateService(plugin)
    payload = dict(group_id=GROUP, sender_id="u1", is_at_bot=False,
                   message_text="今天天气不错", quoted_message_id="", timestamp=1000)
    payload.update(kwargs)
    return asyncio.run(gate.evaluate(**payload))


# ── 门控侧 ──────────────────────────────────────────────────────────

def test_normal_group_passes_through_instead_of_being_dropped_as_non_focus():
    plugin = _plugin(level="normal")
    decision = _evaluate(plugin)

    assert decision.action == "reply", "normal 群的消息又被丢掉了 —— relay 会跟着一起没了"
    assert decision.reason == "normal_group_passthrough"


def test_normal_group_does_not_accumulate_attention():
    attention = _TrackingAttention()
    plugin = _plugin(level="normal", attention=attention)

    _evaluate(plugin)

    assert attention.calls == [], "normal 群还在往注意力里计分（会抢焦点）"


def test_normal_group_at_bot_replies_without_locking_focus():
    attention = _TrackingAttention()
    plugin = _plugin(level="normal", attention=attention)

    decision = _evaluate(plugin, is_at_bot=True)

    assert decision.action == "reply" and decision.force_reply
    assert attention.locked == [] and attention.marked == [] and attention.boosted == [], (
        "被 @ 的 normal 群不该上锁/抢焦点 —— 它不在竞争里"
    )


def test_trusted_group_is_judged_by_its_own_score():
    """trusted 群照常累计注意力；搭不搭话看**本群自己**的分数（不再看别的群）。"""
    attention = _TrackingAttention(score=5.0)
    plugin = _plugin(level="trusted", attention=attention)
    assert _evaluate(plugin).reason == "in_conversation"
    assert attention.calls == ["update_on_message"], "trusted 群必须照常累计注意力"

    cold = _plugin(level="trusted", attention=_TrackingAttention(score=0.5))
    assert _evaluate(cold).reason.startswith("low_attention")


# ── 模型侧：非参与者不持有焦点 ──────────────────────────────────────

def _service(*, levels: dict[str, str] | None, groups: list[str]):
    plugin = SimpleNamespace(
        group_permission_mgr=(SimpleNamespace(get_group_level=lambda gid: levels.get(gid, "none"))
                              if levels is not None else None),
        _qq_settings={},
        logger=SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None),
        _emit_log=lambda *a, **k: None,
        data_path=lambda *a: "",
    )
    service = QQAttentionService(plugin)
    states = []
    for group_id in groups:
        state = service._load_state(group_id)
        state.attention_score = 9.0 if group_id == GROUP else 5.0
        state.focus_acquired_at = 900
        state.last_focus_at = 900
        states.append(state)
    return service, states


def test_participation_follows_the_group_level():
    plugin = SimpleNamespace(group_permission_mgr=SimpleNamespace(
        get_group_level=lambda gid: {"g1": "trusted", "g2": "normal", "g3": "none"}[gid]))
    service = QQAttentionService(plugin)

    assert service.participates_in_attention("g1") is True
    assert service.participates_in_attention("g2") is False
    assert service.participates_in_attention("g3") is False


def test_no_permission_manager_means_everyone_participates():
    """单测桩 / 旧宿主不带权限管理器 —— 那时按"参与"处理，保持既有语义。"""
    service = QQAttentionService(SimpleNamespace(
        group_permission_mgr=None, _qq_settings={},
        logger=SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None)))

    assert service.participates_in_attention("whatever") is True


def test_normal_group_cannot_hold_focus_even_with_the_top_score():
    """被降级（trusted → normal）的群不能凭残留分数继续占着焦点。

    否则它的消息现在直接放行（不参与门控），而 trusted 群会被判 non_focus ——
    等于把 trusted 群静音到分数自然衰减完（实测要二十来分钟）。
    """
    service, states = _service(levels={GROUP: "normal", OTHER: "trusted"}, groups=[GROUP, OTHER])

    chosen = service._choose_focus_state(states, 1000)

    assert chosen is not None and chosen.group_id == OTHER, "normal 群（哪怕 9.0 分）抢到了焦点"
