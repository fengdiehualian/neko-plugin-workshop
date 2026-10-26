"""注意力行为基线：用**固定的消息/回复时序**断言行为，而不是断言内部数值。

为什么需要这一份（草案 docs/attention-redesign-draft.md §7 步骤 0）：
  注意力子系统此前几乎没有行为覆盖 —— 已有测试多在验「某个阈值算得对」，而它
  真实的失败模式是**时序性的静默失效**（相位被打断、分数被抽干、焦点悄悄丢失）。
  这类问题只有「模拟一段真实对话、断言它没掉线」才抓得住。

本文件同时充当重构基线：步 1/2 改完这些断言必须仍然全绿。
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from plugin.plugins.qq_auto_reply.attention_service import QQAttentionService

#: 使用者 business_config.json 里的真实参数
LIVE = {
    "attention_max_score": 10.0,
    "attention_focus_threshold": 4.0,
    "attention_focus_hold_threshold": 2.0,
    "attention_min_threshold": 1.0,
    "attention_base_rise_rate": 0.08,
    "attention_message_boost": 0.15,
    "attention_keyword_boost_ratio": 1.8,
    "attention_heat_warm_gap_seconds": 120,
    "attention_fall_rate": 0.015,
    # 2026-09-29 起真机是 0.05（满格 0.5 分/条），见下面两条用例
    "attention_consume_ratio": 0.05,
    "attention_at_bot_boost": 3.0,
    "attention_question_boost": 1.5,
    "attention_wake_boost_ratio": 0.75,
    "attention_decay_interval_seconds": 5.0,
    "attention_frequency_target_gap": 30.0,
    "attention_frequency_min_multiplier": 0.15,
    "attention_frequency_max_multiplier": 1.8,
    "attention_emotion_multipliers": {"calm": 0.0},
    "backlog_labels": [],
}


class _Clock:
    """可推进的冻结时钟 —— 时序断言必须能精确控制时间。"""

    def __init__(self, start: int = 100_000) -> None:
        self.now = start

    def __call__(self) -> int:
        return self.now


def _service(groups=("A", "B")):
    plugin = SimpleNamespace(
        group_permission_mgr=SimpleNamespace(
            list_groups=lambda: [{"group_id": g} for g in groups]
        ),
        _qq_settings=dict(LIVE),
        backlog_store=None,          # 落盘不是本文件被测对象
        permission_mgr=None,
        _emit_log=lambda *a, **k: None,
        logger=SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None),
    )
    svc = QQAttentionService(plugin)
    clock = _Clock()
    svc._current_time = clock
    return svc, clock


def _seed(svc, gid, score, *, now, focus=True, last_message_at=None):
    """摆一个群的状态。

    热度档的判据是"这个群最后一条消息离现在多远"，所以摆分数时必须一起摆
    `last_message_at`（默认 = now，即"刚刚还有人说话" → warm）。
    """
    st = svc._load_state(gid)
    st.attention_score = score
    st.last_decay_at = now
    st.last_message_at = now if last_message_at is None else last_message_at
    if focus:
        st.focus_acquired_at = now
        st.last_focus_at = now
    svc._write_state(st)


def _message(svc, gid, clock):
    asyncio.run(svc.update_on_message({
        "group_id": gid, "user_id": "u1", "content": "继续聊",
        "timestamp": clock.now, "is_at_bot": False,
    }))


def _reply(svc, gid):
    asyncio.run(svc.update_on_reply(gid))


# ── 意图 1：热群必须能「一直聊很久」 ────────────────────────────────
#
# 使用者原话：「这个群有我喜欢的话题，我能一直聊很久」。
# 一段稳定活跃的对话里，猫娘不该中途掉出可回复状态。
#
# 场景：群每 30 秒有人说话，猫娘每 60 秒回一条，持续 4 分钟。

def _simulate_long_conversation(svc, clock, *, gid="A", start=4.5, minutes=4):
    """返回 [(经过秒数, 分数, 热度档)] 轨迹。"""
    t0 = clock.now
    _seed(svc, gid, start, now=t0)
    traj: list[tuple[int, float, str]] = []
    for step in range(1, minutes * 2 + 1):          # 每步 30 秒
        clock.now = t0 + step * 30
        elapsed = clock.now - t0
        # 每 30 秒群里有新消息；每 60 秒猫娘回一条
        _message(svc, gid, clock)
        if elapsed % 60 == 0:
            _reply(svc, gid)
        st = svc._apply_decay(svc._load_state(gid), clock.now)
        svc._write_state(st)
        traj.append((elapsed, st.attention_score, st.heat))
    return traj


def test_hot_group_stays_replyable_through_a_long_conversation():
    """持续活跃 4 分钟期间，分数必须始终高于发送门控线。"""
    svc, clock = _service()
    traj = _simulate_long_conversation(svc, clock)
    floor = svc._focus_send_threshold()
    low = [(t, s, p) for t, s, p in traj if s < floor]
    detail = "\n".join(f"    +{t:3d}s  score={s:5.2f}  heat={p}" for t, s, p in traj)
    assert not low, (
        f"热群在持续对话中掉出可回复状态（发送线 {floor}）：\n{detail}\n"
        f"  掉线点：{low}"
    )


# ── 意图 2：回复的代价不该随当前分数放大 ────────────────────────────
#
# 现在是乘性消耗（score *= 1 - ratio）：分数越高扣得越多。
# 使用者反复撞上「越热越容易掉」。用户要的是「能一直聊」，热群不该罚得更重。

def test_reply_cost_is_not_proportional_to_current_score():
    """同样比例下，高分群与低分群被扣掉的**绝对量**应当相同（减性语义）。"""
    svc, _ = _service()
    _seed(svc, "A", 4.0, now=svc._current_time())
    _seed(svc, "B", 8.0, now=svc._current_time())

    before_a = svc._load_state("A").attention_score
    _reply(svc, "A")
    cost_a = before_a - svc._load_state("A").attention_score

    before_b = svc._load_state("B").attention_score
    _reply(svc, "B")
    cost_b = before_b - svc._load_state("B").attention_score

    assert cost_a == pytest.approx(cost_b, rel=1e-3), (
        f"回复代价随分数放大：4.0 扣 {cost_a:.2f}、8.0 扣 {cost_b:.2f}。"
        f"使用者要的是一直聊，热群不该被罚得更重。"
    )


def test_full_attention_now_survives_twelve_replies():
    """满格 10 分时，她连发 **12 条**才掉到焦点线、**16 条**才掉到在聊线。

    使用者口径（2026-09-29）：「满 10 的注意力发 6 条就踩线了」→
    `attention_consume_ratio` 从 0.10 降到 **0.05**（满格时每条 0.5 分）。
    旧值下这两个数是 6 条 / 8 条 —— 这条用例正是那时的红。
    """
    svc, clock = _service()
    cost = svc._max_attention() * svc._consume_ratio()
    assert cost == pytest.approx(0.5), f"每条消耗 {cost} 分（max_score × consume_ratio）"

    on_focus = int((svc._max_attention() - svc._focus_threshold()) / cost)
    on_talk = int((svc._max_attention() - svc.conversation_threshold()) / cost)
    assert (on_focus, on_talk) == (12, 16), (
        f"满格只撑得住 {on_focus}/{on_talk} 条，与 0.05 的预期（12/16）不符"
    )

    # 真的回 12 条：仍然站在焦点线上（时钟不动，排除时间衰减的干扰）
    _seed(svc, "A", svc._max_attention(), now=clock.now)
    for _ in range(on_focus):
        _reply(svc, "A")
    assert svc._load_state("A").attention_score >= svc._focus_threshold()

    _reply(svc, "A")                       # 第 13 条才掉下去
    assert svc._load_state("A").attention_score < svc._focus_threshold()


def test_consume_ratio_default_and_fallback_agree():
    """兜底值必须与真源默认值一致：这里漂过一次（真源 0.3、读取端 0.1）。

    只塞部分 settings 的调用方（测试、debug 脚本）拿到的是**兜底值** —— 两处不一致时，
    用真机配置跑出来的手感与自动化验证的完全是两回事。
    """
    from plugin.plugins.qq_auto_reply import settings_schema as schema

    svc, _ = _service()
    svc.plugin._qq_settings = {}          # 一个键都不给 → 只能吃兜底值
    declared = schema.BY_KEY["attention_consume_ratio"].default
    assert declared == pytest.approx(0.05)
    assert svc._consume_ratio() == pytest.approx(declared)


# ── 意图 3：回血不能被档位压制 ──────────────────────────────────────
#
# 老相位机在 fall 里把消息加成乘 0.3（"让位的群不许赖着不走"）：30 秒衰减 0.45、
# 群友发一条只补 0.045 —— 净增速恒为负，跌下去就爬不回来。跨群取舍删掉后连
# "让位"都没有了，这条压制也一并取消；现在的回血只取决于**这个群自己**热不热。

def test_message_boost_is_the_same_in_both_heat_tiers():
    """同样一条消息，在冷却档与热聊档里的加成必须一样。"""
    svc, clock = _service()
    now = clock.now

    _seed(svc, "A", 5.0, now=now)                       # 刚刚还有人说话 → warm
    _message(svc, "A", clock)
    warm_boost = svc._load_state("A").attention_score - 5.0

    _seed(svc, "B", 5.0, now=now, last_message_at=now - 3600)   # 静了一小时 → cooling
    _message(svc, "B", clock)
    cooling_boost = svc._load_state("B").attention_score - 5.0

    assert cooling_boost == pytest.approx(warm_boost, rel=0.05), (
        f"冷却档的消息加成被压掉：warm={warm_boost:.3f} cooling={cooling_boost:.3f}。"
        f"这条压制让凉下来的群无法回血。"
    )


# ── 意图 4：分数必须有余量支撑「一直聊」 ────────────────────────────
#
# 自然增长曾被 min(focus_threshold, ...) 封顶在档位线上：刚到线即「刚好够线、
# 零余量」，于是每次回复消耗都会把群打到线下。要能一直聊，线上必须有空间。

def test_score_can_exceed_focus_threshold():
    """热聊中的自然增长不得被档位线封顶。"""
    svc, clock = _service()
    now = clock.now
    _seed(svc, "A", 4.0, now=now)

    # 一直有人说话（每 10 秒一条），期间没有回复消耗：分数应能涨过档位线
    for step in range(1, 7):                    # 6 × 10s
        clock.now = now + step * 10
        _message(svc, "A", clock)
        st = svc._apply_decay(svc._load_state("A"), clock.now)
        svc._write_state(st)

    score = svc._load_state("A").attention_score
    assert score > svc._focus_threshold(), (
        f"分数被档位线封顶（score={score:.2f}, 档位线={svc._focus_threshold()}）。"
        f"到线后没有余量，任何一次回复消耗都会把群打到线下。"
    )


# ── 意图 5：锁（@猫娘 / 唤醒词）─────────────────────────────────────
#
# 设计（草案 §2/§3）：无锁时归属 = 分数最高的群、**随时可变**（这就是「看一眼
# 新群、没兴趣就回旧群」）；有锁时该群独占，其余群不参与竞争。
# 两者是**独立信号** —— 分数是「我多想聊」，锁是「有人点名叫我」。

def _focus_of(svc, clock):
    """当前归属（走真实的焦点选择路径）。"""
    states = [svc._load_state(g) for g in ("A", "B")]
    for s in states:
        svc._write_state(s)
    chosen = svc._choose_focus_state(states, clock.now)
    return chosen.group_id if chosen else ""


def test_lock_wins_over_a_higher_scoring_group():
    """被锁的群即使分数明显低于别人，也必须保持归属。"""
    svc, clock = _service()
    now = clock.now
    _seed(svc, "A", 3.0, now=now)      # 被锁，但分数低
    _seed(svc, "B", 9.0, now=now)      # 分数高得多，想抢
    svc.lock_group("A")

    assert _focus_of(svc, clock) == "A", (
        "锁未生效：高分群抢走了被 @ 的群 —— 有人点名叫我时我必须回头应对"
    )


def test_lock_expires_and_score_arbitration_resumes():
    """锁到期后回到纯分数仲裁。"""
    svc, clock = _service()
    now = clock.now
    _seed(svc, "A", 3.0, now=now)
    _seed(svc, "B", 9.0, now=now)
    svc.lock_group("A")
    assert _focus_of(svc, clock) == "A"

    clock.now = now + svc._lock_seconds() + 1     # 锁过期
    assert _focus_of(svc, clock) == "B", "锁过期后应交还给分数最高的群"


def test_relocking_resets_the_timer():
    """再被 @ 一次应重新锁满（与人对重复召唤的直觉一致）。"""
    svc, clock = _service()
    now = clock.now
    _seed(svc, "A", 3.0, now=now)
    _seed(svc, "B", 9.0, now=now)

    svc.lock_group("A")
    clock.now = now + svc._lock_seconds() - 5     # 快到期了
    svc.lock_group("A")                            # 又被叫一次

    clock.now = now + svc._lock_seconds() + 1     # 按第一次算已过期
    assert _focus_of(svc, clock) == "A", (
        "重复 @ 未重置计时：按第一次的时间算已经过期了"
    )


def test_lock_seconds_zero_disables_locking():
    """`attention_lock_seconds=0` 时锁不生效 —— 回到纯分数仲裁。"""
    svc, clock = _service()
    svc.plugin._qq_settings["attention_lock_seconds"] = 0
    now = clock.now
    _seed(svc, "A", 3.0, now=now)
    _seed(svc, "B", 9.0, now=now)
    svc.lock_group("A")
    assert _focus_of(svc, clock) == "B", "锁时长 0 时不应锁住"


def test_lock_is_persisted():
    """锁必须能存下来/读回来（重启后 lock_until 不能丢）。"""
    svc, clock = _service()
    _seed(svc, "A", 3.0, now=clock.now)
    svc.lock_group("A")
    raw = svc._load_state("A").to_dict()
    assert int(raw.get("lock_until") or 0) > 0, "to_dict 丢了 lock_until"
    restored = type(svc._load_state("A")).from_dict(raw, group_id="A")
    assert restored.lock_until == raw["lock_until"], "from_dict 丢了 lock_until"


# ── 意图：「本群在聊」这条判据（2026-09-29 起按群算，取代全局焦点）────
#
# 使用者口径：「每个群自己管自己的注意力」。于是门控/必要度/复读三处共用的判据
# 从"我是不是唯一焦点"换成"本群自己的分数过没过保持线"。

def test_in_conversation_follows_the_groups_own_score():
    """本群分数过保持线才算在聊；别的群多热都不改变它。"""
    svc, clock = _service()
    now = clock.now
    _seed(svc, "A", 2.5, now=now)
    _seed(svc, "B", 9.9, now=now)

    assert svc.is_in_conversation("A") is True
    assert svc.is_in_conversation("B") is True

    _seed(svc, "A", 1.9, now=now)   # A 凉了，B 仍然很热
    assert svc.is_in_conversation("A") is False, (
        "凉了的群因为别的群热就被算成「在聊」 —— 跨群取舍又回来了"
    )
    assert svc.is_in_conversation("B") is True


def test_in_conversation_is_false_for_unknown_or_empty_groups():
    """没见过的群（或空 group_id）不算在聊 —— 门控那边就不会误放行。"""
    svc, clock = _service()
    _seed(svc, "A", 9.0, now=clock.now)

    assert svc.is_in_conversation("B") is False, "从未出现的群不该被算成在聊"
    assert svc.is_in_conversation("") is False
    assert svc.is_in_conversation("   ") is False


def test_conversation_threshold_reads_the_live_config_key():
    """这条线来自 `attention_focus_hold_threshold` —— 键名不许改。

    改名的后果是**老配置静默失效**：使用者配置里写的 2.0 会被忽略、悄悄退回默认值。
    """
    svc, _ = _service()

    assert svc.conversation_threshold() == pytest.approx(
        LIVE["attention_focus_hold_threshold"]
    )
    assert svc._focus_send_threshold() == svc.conversation_threshold()

    svc.plugin._qq_settings["attention_focus_hold_threshold"] = 3.5
    assert svc.conversation_threshold() == pytest.approx(3.5)
    _seed(svc, "A", 3.0, now=svc._current_time())
    assert svc.is_in_conversation("A") is False, "阈值调高后 3.0 应该算凉了"
