# -*- coding: utf-8 -*-
"""跨群「人对人」连击计数：先出数据，默认不改行为。

这是四项增益里的 ④。它补的是必要性判断里最弱的一维 ——
「谁在跟谁说话」。我们此前只有布尔量（`mentions_other_user`），
而 `focus` 一项恰好等于阈值 40，于是焦点群内实质是"有积压就接"。

`human_pair_streak` = **连续多少条别人的消息既没 @ 她也没引用她**（= 这群人正在互相聊）。

按使用者 2026-09-27 的决定：**先把数据算出来、不动阈值** ——
`necessity_human_pair_penalty` 默认 **0.0**，分数一分不变，但依据里会出现 `人对人×N`。
等真机日志攒够，再改这一个配置键决定扣多少（不用改代码）。

因此这个文件里最重要的一条是 `test_default_penalty_changes_nothing`：
默认配置下，连击多长都不影响分数。
"""

from __future__ import annotations

from types import SimpleNamespace

from plugin.plugins.qq_auto_reply.attention_gate_service import QQAttentionGateService
from plugin.plugins.qq_auto_reply.reply_necessity import NecessitySignals, score_necessity

GROUP = "1048307485"
OTHER_GROUP = "985066274"


def _signals(**kw) -> NecessitySignals:
    base = dict(
        message_text="今天天气不错",
        is_group=True,
        focus_active=True,
        pending_count=1,
        pending_threshold=10,     # 压力分为 0，隔离出"人对人"这一项
        self_ratio=0.0,
    )
    base.update(kw)
    return NecessitySignals(**base)


# ── 打分侧 ─────────────────────────────────────────────────────────

def test_default_penalty_changes_nothing():
    """**本次最重要的一条**：默认 0 分，连击多长都不改分数。"""
    short = score_necessity(_signals(human_pair_streak=1), threshold=40)
    long_ = score_necessity(_signals(human_pair_streak=50), threshold=40)
    assert short.score == long_.score
    assert long_.breakdown.human_pair == 0


def test_streak_is_visible_as_a_reason_even_without_penalty():
    """罚 0 分也要出依据 —— 否则"先收数据"就是空话。"""
    verdict = score_necessity(_signals(human_pair_streak=4), threshold=40)
    assert "人对人×4" in verdict.breakdown.reasons


def test_streak_below_the_floor_is_not_reported():
    verdict = score_necessity(_signals(human_pair_streak=2), threshold=40, human_pair_min_streak=3)
    assert "人对人×2" not in verdict.breakdown.reasons


def test_penalty_reduces_the_score_when_configured():
    base = score_necessity(_signals(human_pair_streak=5), threshold=40)
    docked = score_necessity(
        _signals(human_pair_streak=5), threshold=40,
        human_pair_penalty=20.0, human_pair_min_streak=3,
    )
    assert base.score - docked.score == 20
    assert docked.breakdown.human_pair == -20


def test_penalty_only_applies_at_or_above_the_floor():
    below = score_necessity(
        _signals(human_pair_streak=2), threshold=40,
        human_pair_penalty=20.0, human_pair_min_streak=3,
    )
    assert below.breakdown.human_pair == 0


# ── 计数侧（门控）──────────────────────────────────────────────────

class _FakeAttention:
    def get_group_multiplier(self, group_id: str) -> float:
        return 1.0


def _gate(**settings):
    base = {"reply_necessity_threshold": 40.0, "backlog_labels": []}
    base.update(settings)
    plugin = SimpleNamespace(
        _qq_settings=base,
        logger=SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None),
        _emit_log=lambda *a, **k: None,
        attention_service=_FakeAttention(),
        group_permission_mgr=None,
        permission_mgr=None,
    )
    return QQAttentionGateService(plugin)


def test_streak_counts_others_messages_without_her_being_addressed():
    gate = _gate()
    assert gate._record_human_pair(GROUP, addressed_to_bot=False) == 1
    assert gate._record_human_pair(GROUP, addressed_to_bot=False) == 2
    assert gate._record_human_pair(GROUP, addressed_to_bot=False) == 3


def test_addressing_her_resets_the_streak():
    gate = _gate()
    gate._record_human_pair(GROUP, addressed_to_bot=False)
    gate._record_human_pair(GROUP, addressed_to_bot=False)
    assert gate._record_human_pair(GROUP, addressed_to_bot=True) == 0
    assert gate._record_human_pair(GROUP, addressed_to_bot=False) == 1


def test_streak_is_per_group():
    gate = _gate()
    gate._record_human_pair(GROUP, addressed_to_bot=False)
    gate._record_human_pair(GROUP, addressed_to_bot=False)
    assert gate._record_human_pair(OTHER_GROUP, addressed_to_bot=False) == 1
    assert gate._human_pair_streak[GROUP] == 2


def test_blank_group_id_is_ignored():
    gate = _gate()
    assert gate._record_human_pair("  ", addressed_to_bot=False) == 0


# ── 门控 → 信号：连击真的进了打分输入 ──────────────────────────────

def test_gate_feeds_the_streak_into_the_scoring():
    gate = _gate()
    for _ in range(3):
        gate._record_human_pair(GROUP, addressed_to_bot=False)
    verdict = gate._evaluate_necessity(
        group_id=GROUP, sender_id="u1", message_text="今天天气不错",
        is_reply_to_bot=False, now=1000.0,
    )
    assert "人对人×3" in verdict.breakdown.reasons, "连击没有进打分输入"


def test_gate_penalty_default_is_zero_and_configurable():
    """默认 0（不改行为），可配置成别的值（那时才真的扣分）。"""
    assert _gate()._human_pair_penalty() == 0.0
    assert _gate(necessity_human_pair_penalty=20)._human_pair_penalty() == 20.0
    assert _gate(necessity_human_pair_min_streak=5)._human_pair_min_streak() == 5
    assert _gate()._human_pair_min_streak() == 3


def test_gate_penalty_survives_bad_values():
    """配置里写坏了不许把门控打崩（宁可当 0）。"""
    assert _gate(necessity_human_pair_penalty="abc")._human_pair_penalty() == 0.0
    assert _gate(necessity_human_pair_min_streak=None)._human_pair_min_streak() == 3
