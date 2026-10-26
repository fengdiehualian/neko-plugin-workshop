"""回复消耗不得改变这个群的**热度档**，也不得重置静默计时。

现场（`backlog_state.json`，两个群都是这个终局）：

    { "attention_score": 0.775, "phase": "fall", "last_focus_reason": "reply_consume" }
    { "attention_score": 0.0,   "phase": "fall", "last_focus_reason": "reply_consume" }

``update_on_reply`` 曾经无条件 ``phase = "fall"`` 并覆盖 ``phase_started_at``，
于是**每次回复都把蜜月掐断**、逼这个群重新熬满 ``attention_fall_seconds``。
用户配置下那是 240 秒，期间消息加成只剩 ``attention_fall_boost_attenuation``
（0.3）、分数还按 ``attention_fall_rate`` 往下掉 —— 回复一次等于判这个群四分钟
不说话，且分数注定跌破 ``attention_focus_hold_threshold`` 的发送门控线，
后续消息全被静默忽略（用户看到的就是"发一句就没有后文"）。

2026-09-29 换成热度档之后这条约束更强了：**热度只看"这个群最后一条消息离现在多远"**，
`update_on_reply` 连 `last_message_at` 都不碰 —— 她自己的发言永远不能把一个已经凉了的
群算成热聊中（否则她会用自己的自言自语把群一直捂热）。
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from plugin.plugins.qq_auto_reply.attention_service import (
    QQAttentionService,
    QQGroupAttentionState,
)

#: 用户 business_config.json 里的实际注意力参数
LIVE_SETTINGS = {
    "attention_max_score": 10.0,
    "attention_focus_threshold": 4.0,
    "attention_focus_hold_threshold": 2.0,
    "attention_min_threshold": 1.0,
    "attention_base_rise_rate": 0.02,
    "attention_message_boost": 0.15,
    "attention_heat_warm_gap_seconds": 120,
    "attention_fall_rate": 0.015,
    "attention_consume_ratio": 0.3,
    "backlog_labels": [],
}


def _service() -> tuple[QQAttentionService, list[int]]:
    plugin = SimpleNamespace(
        _qq_settings=dict(LIVE_SETTINGS),
        backlog_store=None,
        group_permission_mgr=None,
        permission_mgr=None,
        _emit_log=lambda *a, **k: None,
        logger=SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None),
    )
    service = QQAttentionService(plugin)
    now = [1000]
    service._current_time = lambda: now[0]
    return service, now


def test_reply_does_not_change_the_heat_tier():
    """回复只消耗分数，热度档保持不变（修之前这里会变成 "fall"）。"""
    service, now = _service()
    state = QQGroupAttentionState(group_id="g1", attention_score=6.0)
    state.last_decay_at = now[0]
    state.last_message_at = now[0]            # 群里刚刚有人说话 → warm
    state.focus_acquired_at = now[0]
    service._write_state(state)

    asyncio.run(service.update_on_reply("g1"))

    after = service._load_state("g1")
    # 消耗语义是「绝对量」：cost = max_score × consume_ratio（本文件桩 ratio=0.3 ⇒ 3.0）
    expected = 6.0 - service._max_attention() * service._consume_ratio()
    assert after.attention_score == pytest.approx(max(0.0, expected), rel=1e-3)
    assert after.heat == "warm", "回复不得把群打进冷却档"
    assert after.last_focus_reason == "reply_consume"


def test_reply_does_not_restart_the_quiet_clock():
    """回复不得碰 ``last_message_at`` —— 否则她一回话就把静默计时抹掉。

    真机口径：她已经凉下来的群，靠她自己时不时说一句就能一直算"热聊中"，
    那样"热度"就不再是**群**的热度，而是她的自言自语。
    """
    service, now = _service()
    state = QQGroupAttentionState(group_id="g1", attention_score=6.0)
    state.last_decay_at = now[0]
    state.last_message_at = 500               # 群里最后一次说话是很久以前
    service._write_state(state)

    asyncio.run(service.update_on_reply("g1"))

    after = service._load_state("g1")
    assert after.last_message_at == 500
    assert after.heat == "cooling", "群里没人说话，她自己也发不出热度"


def test_group_stays_above_the_send_gate_after_one_reply():
    """用户症状的直接反证：回复一次后，分数必须仍在发送门控线（2.0）之上。"""
    service, now = _service()
    # 用**使用者真实配置**建场景：consume_ratio=0.1、max_score=10
    # ⇒ 回复一次固定扣 1.0 分（减性，与当前分数无关）。
    service.plugin._qq_settings.update({
        "attention_consume_ratio": 0.1,
    })
    state = QQGroupAttentionState(group_id="g1", attention_score=4.5)
    state.last_decay_at = now[0]
    state.last_message_at = now[0]             # 一个正在热聊的群
    state.focus_acquired_at = now[0]
    service._write_state(state)

    asyncio.run(service.update_on_reply("g1"))
    score_after_reply = service._load_state("g1").attention_score
    assert score_after_reply > 2.0, (
        f"回复一次后跌到 {score_after_reply:.2f}（门控线 2.0）—— "
        f"cost 应为 1.0 而不是随分数放大"
    )

    # 群里继续有人说话，推进 30 秒：热度档是 warm，分数应回补而不是下坠。
    now[0] += 30
    st = service._load_state("g1")
    st.last_message_at = now[0]                # 这 30 秒里有人说过话
    service._write_state(st)
    after = service._apply_decay(service._load_state("g1"), now[0])
    assert after.attention_score > score_after_reply, "热聊中应回补，而不是继续下坠"
    assert after.attention_score > 2.0, "且必须仍在发送门控线之上"
    assert after.heat == "warm"


def test_a_quiet_group_cools_down_even_right_after_she_replied():
    """她已经回过话，但群里随后安静下来 → 仍然转冷却档（这才是"群"的热度）。"""
    service, now = _service()
    state = QQGroupAttentionState(group_id="g1", attention_score=5.0)
    state.last_decay_at = now[0]
    state.last_message_at = now[0]
    service._write_state(state)

    asyncio.run(service.update_on_reply("g1"))

    now[0] += service._heat_warm_gap_seconds() + 1
    after = service._apply_decay(service._load_state("g1"), now[0])
    assert after.heat == "cooling"
    assert after.attention_score < 5.0, "冷却档必须往下掉"
