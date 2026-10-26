"""休眠：这个群静默够久 → 睡下 → **只答点名**。

使用者口径（2026-09-29）：「群聊如果冷漠了就休眠，大概就是半个小时没有任何发言」。

- 触发判据只有本群自己的 `last_message_at`（默认 `dormancy_idle_seconds = 1800`）；
- 睡下的效果（跨群取舍删除后重新定义）：**只答点名** —— @ / 引用她 / 关键词照旧必回，
  其余消息在门控里被忽略（**但照常计分**：分数涨回 `dormancy_wake_score` 就自己醒）；
- 唤醒只有两条：点名即醒（`mark_focus`）或群里又热闹到 `dormancy_wake_score`；
  **没有到点自动醒**（2026-09-29 使用者：「到点自动醒也不要」）；
- 睡着期间分数**冻住**（已经凉了，再掉只是把"醒来从零熬"做一遍）。
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from plugin.plugins.qq_auto_reply import settings_schema
from plugin.plugins.qq_auto_reply.attention_gate_service import QQAttentionGateService
from plugin.plugins.qq_auto_reply.attention_service import (
    QQAttentionService,
    QQGroupAttentionState,
)

GROUP = "1048307485"
NOW = 1_000_000

SETTINGS = {
    "attention_max_score": 10.0,
    "attention_focus_threshold": 4.0,
    "attention_focus_hold_threshold": 2.0,
    "attention_min_threshold": 1.0,
    "attention_heat_warm_gap_seconds": 120,
    "attention_fall_rate": 0.015,
    "attention_consume_ratio": 0.1,
    "dormancy_enabled": True,
    "dormancy_idle_seconds": 1800,
    "dormancy_wake_score": 2.0,
    "attention_emotion_multipliers": {"calm": 0.0},
    "backlog_labels": [],
}


def _service(**overrides) -> tuple[QQAttentionService, list[int]]:
    plugin = SimpleNamespace(
        _qq_settings={**SETTINGS, **overrides},
        backlog_store=None,
        group_permission_mgr=None,
        permission_mgr=None,
        _emit_log=lambda *a, **k: None,
        logger=SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None),
    )
    svc = QQAttentionService(plugin)
    now = [NOW]
    svc._current_time = lambda: now[0]
    return svc, now


def _seed(svc, *, score: float = 5.0, last_message_at: int, now: int = NOW):
    st = QQGroupAttentionState(group_id=GROUP, attention_score=score, last_message_at=last_message_at)
    st.last_decay_at = now
    svc._write_state(st)
    return st


# ── 什么时候睡 ──────────────────────────────────────────────────────

def test_a_group_quiet_for_half_an_hour_falls_asleep():
    svc, now = _service()
    _seed(svc, last_message_at=now[0] - 1800)

    assert asyncio.run(svc.decay_all()) is None

    st = svc._load_state(GROUP)
    assert st.dormant is True, "静默半小时却还醒着"
    assert st.heat == "dormant"
    assert svc.is_dormant(GROUP) is True


def test_a_group_quiet_for_less_than_that_stays_awake():
    svc, now = _service()
    _seed(svc, last_message_at=now[0] - 1799)

    asyncio.run(svc.decay_all())

    assert svc.is_dormant(GROUP) is False


def test_a_group_that_never_spoke_does_not_sleep():
    """从没说过话的群是「还没认识」，不是「冷漠了」。"""
    svc, _ = _service()
    _seed(svc, last_message_at=0)

    asyncio.run(svc.decay_all())

    assert svc.is_dormant(GROUP) is False


def test_the_idle_threshold_is_configurable():
    svc, now = _service(dormancy_idle_seconds=600)
    _seed(svc, last_message_at=now[0] - 700)

    asyncio.run(svc.decay_all())

    assert svc.is_dormant(GROUP) is True


def test_zero_idle_disables_automatic_dormancy():
    """`dormancy_idle_seconds=0` = 关掉自动休眠（0 是合法值，不能被 `or` 吞掉）。"""
    svc, now = _service(dormancy_idle_seconds=0)
    _seed(svc, last_message_at=now[0] - 999_999)

    asyncio.run(svc.decay_all())

    assert svc._dormancy_idle_seconds() == 0
    assert svc.is_dormant(GROUP) is False


def test_the_master_switch_disables_it():
    svc, now = _service(dormancy_enabled=False)
    _seed(svc, last_message_at=now[0] - 999_999)

    asyncio.run(svc.decay_all())

    assert svc._dormancy_enabled() is False
    assert svc.is_dormant(GROUP) is False
    # 关掉时**连标记都不许立**：否则用户把开关再打开，那些旧标记会立刻让群重新睡下
    # —— 看上去就像"开关没记住我刚才关过"。
    st = svc._load_state(GROUP)
    assert st.dormant is False


def test_sleeping_is_idempotent():
    """已经睡着的群不会被重复"睡下"（日志不该每 5 秒刷一条）。"""
    svc, now = _service()
    _seed(svc, last_message_at=now[0] - 1800)
    asyncio.run(svc.decay_all())
    assert svc._maybe_fall_asleep(svc._load_state(GROUP), now[0]) is False


# ── 睡着的时候分数冻住 ──────────────────────────────────────────────

def test_a_dormant_group_keeps_its_score():
    svc, now = _service()
    st = QQGroupAttentionState(group_id=GROUP, attention_score=3.5, last_message_at=now[0] - 1800)
    st.last_decay_at = now[0]
    st.dormant = True

    after = svc._apply_decay(st, now[0] + 100_000)

    assert after.heat == "dormant"
    assert after.attention_score == pytest.approx(3.5), "睡着的群分数该冻住（醒来不必从零熬）"


def test_waking_keeps_the_accumulated_score():
    svc, now = _service()
    st = QQGroupAttentionState(group_id=GROUP, attention_score=3.5, last_message_at=now[0] - 1800)
    st.last_decay_at = now[0]
    st.dormant = True
    svc._write_state(st)

    assert svc.wake_from_dormancy(GROUP, reason="at") is True
    assert svc._load_state(GROUP).attention_score == pytest.approx(3.5)


# ── 怎么醒 ──────────────────────────────────────────────────────────

def test_being_named_wakes_the_group():
    """`mark_focus` 是点名类路径（@ / 关键词 / 引用她）共同的落点。"""
    svc, now = _service()
    _seed(svc, last_message_at=now[0] - 1800)
    asyncio.run(svc.decay_all())
    assert svc.is_dormant(GROUP) is True

    svc.mark_focus(GROUP)

    assert svc.is_dormant(GROUP) is False
    assert svc._load_state(GROUP).last_focus_reason == "wake:addressed"


def test_waking_moves_the_retro_cursor_forward():
    """睡着那段时间的账**不补**：`last_focus_at`（回溯补回的游标）推到此刻。"""
    svc, now = _service()
    _seed(svc, last_message_at=now[0] - 1800)
    asyncio.run(svc.decay_all())
    assert svc.get_last_focus_at(GROUP) < now[0]

    svc.wake_from_dormancy(GROUP, reason="at")

    assert svc.get_last_focus_at(GROUP) == now[0]


def test_there_is_no_auto_wake():
    """**「到点自动醒」这件事不存在**（2026-09-29 使用者：「到点自动醒也不要」）。

    睡下的群不因为「时间过去了」而醒：这套模型里连一个「到点醒的时刻」字段都没有
    （`dormant` 就是个布尔量）。只有点名、或群里又热闹起来能叫醒它。
    """
    svc, now = _service()
    _seed(svc, last_message_at=now[0] - 1800)
    asyncio.run(svc.decay_all())
    assert svc.is_dormant(GROUP) is True

    now[0] += 30 * 24 * 3600          # 睡一个月
    asyncio.run(svc.decay_all())

    assert svc.is_dormant(GROUP) is True, "时间过去了就自己醒了"
    assert not hasattr(svc._load_state(GROUP), "dormant_until"), "又冒出「到点醒」的时间戳字段"
    assert not hasattr(svc, "_dormancy_auto_wake_seconds"), "自动醒的读取口又被加回来了"


def test_wake_reports_first_time_only():
    svc, now = _service()
    _seed(svc, last_message_at=now[0] - 1800)
    asyncio.run(svc.decay_all())

    assert svc.wake_from_dormancy(GROUP) is True
    assert svc.wake_from_dormancy(GROUP) is False


def test_an_old_archive_cannot_sleep_a_group():
    """启动时清一次旧标记：旧存档里睡下的群不该在升级后继续睡着。

    `from_dict` **会**读 `dormant`（`_load_state` 每次都经它重建，不读就等于
    「睡下 → 下一次读状态又醒了」），所以清理放在 `load_cached_state`（插件启动时
    一次性清空）—— 那样既不破坏本次运行内的往返，也不回放陈年标记。
    """
    from types import SimpleNamespace

    from plugin.plugins.qq_auto_reply.attention_service import QQAttentionService

    class _Store:
        def __init__(self, payload: dict):
            self.payload = payload

        async def load(self) -> dict:
            return self.payload

        async def update_group_attention_state(self, _payload) -> None:
            return None

    legacy = {
        GROUP: {
            "group_id": GROUP, "attention_score": 5.0,
            "dormant": True,
            "last_message_at": NOW - 60,
        },
    }
    plugin = SimpleNamespace(
        _qq_settings=dict(SETTINGS),
        backlog_store=_Store({"group_attention_state": legacy}),
        # 必须给一个"信任列表里有这个群"的权限管理器：`cleanup_stale_cache()` 会把
        # 不在信任列表里的群**整个删掉**，那样这条测试就测不到迁移，只测到"缓存被清空"。
        group_permission_mgr=SimpleNamespace(list_groups=lambda: [{"group_id": GROUP}]),
        permission_mgr=None,
        _emit_log=lambda *a, **k: None,
        logger=SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None),
    )
    svc = QQAttentionService(plugin)
    svc._current_time = lambda: NOW

    asyncio.run(svc.load_cached_state())

    assert svc.is_dormant(GROUP) is False, "旧存档的休眠标记在启动时被回放了"


def test_dormancy_state_survives_a_restart():
    """`to_dict` / `from_dict` 必须带上休眠标记（重启不该让它忘记自己在睡）。"""
    svc, now = _service()
    _seed(svc, last_message_at=now[0] - 1800)
    asyncio.run(svc.decay_all())

    raw = svc._load_state(GROUP).to_dict()
    restored = QQGroupAttentionState.from_dict(raw, group_id=GROUP)
    assert restored.dormant is True


# ── 苏醒：群里又热闹起来了（建模到注意力上）────────────────────────
#
# 使用者口径（2026-09-29）：「如果群里聊得热火朝天就苏醒。这一块可以建模到注意力上」。
# 所以睡着期间分数**只由"有人说话"推动**（不随时间回落、也不自然增长），涨回唤醒线
# 就苏醒；唤醒发生在那条消息加分**之后**，于是"把分数顶过线的那一条"自己就能被正常回。

def _sleep(svc, now, *, score: float = 0.0) -> None:
    st = QQGroupAttentionState(
        group_id=GROUP, attention_score=score, last_message_at=now[0] - 1800,
    )
    st.last_decay_at = now[0]
    svc._write_state(st)
    asyncio.run(svc.decay_all())
    assert svc.is_dormant(GROUP) is True


def _speak(svc, now, *, text: str = "继续聊", at_bot: bool = False) -> None:
    asyncio.run(svc.update_on_message({
        "group_id": GROUP, "user_id": "u1", "content": text,
        "timestamp": now[0], "is_at_bot": at_bot,
    }))


def test_one_plain_message_does_not_wake_a_sleeping_group():
    svc, now = _service()
    _sleep(svc, now)

    _speak(svc, now)

    assert svc.is_dormant(GROUP) is True, "一条普通消息就把睡着的群叫醒了"


def test_plain_chatter_still_scores_while_dormant():
    """睡着的群**照常计分** —— 那正是"群里又聊热就醒"的燃料。

    门控那边只负责"这一轮不回"；计分发生在 `evaluate()` 的**第 1 步**
    （`attention.update_on_message`），早于第 4.5 步的休眠判定。所以注释里那句
    「也不计分」是错的，谁按它去"修"，就会把唯一的自动苏醒路径掐掉。
    """
    svc, now = _service()
    _sleep(svc, now, score=0.0)

    _speak(svc, now)

    st = svc._load_state(GROUP)
    assert st.attention_score == pytest.approx(0.15), "睡着期间普通消息的加成没入账"
    assert st.dormant is True, "一条消息还不该醒（唤醒线是 2.0）"


def test_a_lively_burst_wakes_the_group():
    svc, now = _service()
    _sleep(svc, now)

    for i in range(20):          # 0.15/条 → 20 条越过 2.0
        now[0] += 1
        _speak(svc, now)
        if not svc.is_dormant(GROUP):
            break

    st = svc._load_state(GROUP)
    assert svc.is_dormant(GROUP) is False, "群里连着聊了二十条还没醒"
    assert st.last_focus_reason == "wake:lively"


def test_the_wake_score_is_configurable():
    svc, now = _service(dormancy_wake_score=5.0)
    _sleep(svc, now)

    for _ in range(20):          # 顶到 3.0 左右，还不够 5.0
        now[0] += 1
        _speak(svc, now)
    assert svc.is_dormant(GROUP) is True, "唤醒线配高了却还是醒了"
    assert svc._load_state(GROUP).attention_score < 5.0

    for _ in range(30):
        now[0] += 1
        _speak(svc, now)
    assert svc.is_dormant(GROUP) is False


def test_a_dormant_group_does_not_decay_over_time():
    """睡着期间分数**不随时间回落**：只有"有人说话"能改变它。

    否则一个慢慢漏消息的群永远攒不到唤醒线，而"热起来就醒"这件事会依赖 tick 相位。
    """
    svc, now = _service()
    _sleep(svc, now, score=1.2)

    after = svc._apply_decay(svc._load_state(GROUP), now[0] + 100_000)

    assert after.attention_score == pytest.approx(1.2)
    assert after.heat == "dormant"


def test_waking_by_liveliness_moves_the_retro_cursor():
    svc, now = _service()
    _sleep(svc, now)

    for _ in range(20):
        now[0] += 1
        _speak(svc, now)
        if not svc.is_dormant(GROUP):
            break

    assert svc.get_last_focus_at(GROUP) == now[0]


def test_a_batch_count_can_wake_the_group_too():
    """缓冲汇总那类"一次补一批"的路径也要能叫醒它。"""
    svc, now = _service()
    _sleep(svc, now)

    asyncio.run(svc.update_on_message_count(GROUP, message_count=20))

    assert svc.is_dormant(GROUP) is False


def test_the_wake_score_defaults_to_the_in_conversation_line():
    """默认唤醒线 = 门控那条「本群在聊的线」（同一个值，但不是同一个键）。"""
    svc, _ = _service()

    assert svc._dormancy_wake_score() == pytest.approx(svc.conversation_threshold())


# ── 门控：睡着的群只答点名 ─────────────────────────────────────────

class _FakeAttention:
    def __init__(self, *, dormant: bool = False, score: float = 5.0):
        self._dormant = dormant
        self._score = score
        self._now = NOW
        self.focus_calls: list[str] = []

    def _enabled(self) -> bool:
        return True

    def _current_time(self) -> int:
        return self._now

    def is_dormant(self, group_id: str) -> bool:
        return self._dormant

    def get_state(self, group_id: str):
        return SimpleNamespace(attention_score=self._score)

    def is_in_conversation(self, group_id: str) -> bool:
        return self._score >= 2.0

    def conversation_threshold(self) -> float:
        return 2.0

    def _minimum_threshold(self) -> float:
        return 1.0

    def _focus_threshold(self) -> float:
        return 4.0

    def _focus_send_threshold(self) -> float:
        return 2.0

    def get_group_multiplier(self, group_id: str) -> float:
        return 1.0

    def is_first_reply_after_own_speech(self, group_id: str) -> bool:
        return False

    async def update_on_message(self, message: dict) -> None:
        return None

    def mark_focus(self, group_id: str) -> None:
        self.focus_calls.append(group_id)
        self._dormant = False          # 点名即醒（与真实现一致）

    def lock_group(self, group_id: str) -> None:
        return None

    def wake_boost(self, group_id: str) -> None:
        return None


def _gate(attention) -> QQAttentionGateService:
    plugin = SimpleNamespace(
        attention_service=attention,
        qq_client=SimpleNamespace(needs_attention=True, _sent_message_ids={}),
        permission_mgr=None,
        group_permission_mgr=SimpleNamespace(get_group_level=lambda gid: "trusted"),
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
    return QQAttentionGateService(plugin)


def _evaluate(attention, **kwargs):
    gate = _gate(attention)
    payload = dict(group_id=GROUP, sender_id="u1", is_at_bot=False,
                   message_text="今天天气不错", quoted_message_id="", timestamp=NOW)
    payload.update(kwargs)
    return asyncio.run(gate.evaluate(**payload))


def test_a_dormant_group_ignores_plain_messages():
    decision = _evaluate(_FakeAttention(dormant=True))

    assert decision.action == "ignore"
    assert decision.reason == "dormant"


def test_a_dormant_group_still_answers_when_named():
    attention = _FakeAttention(dormant=True)

    decision = _evaluate(attention, is_at_bot=True)

    assert decision.action == "reply" and decision.force_reply is True
    assert attention.focus_calls == [GROUP], "点名没走到唤醒"
    assert attention.is_dormant(GROUP) is False


def test_a_dormant_group_still_answers_a_quote():
    decision = _evaluate(_FakeAttention(dormant=True), quoted_message_id="m1", is_reply_to_bot=True)

    assert decision.action == "reply"
    assert decision.reason == "reply_to_bot"


def test_a_dormant_group_still_answers_keywords():
    attention = _FakeAttention(dormant=True)
    gate = _gate(attention)
    gate.plugin._qq_settings = {"backlog_labels": [{
        "id": "issue", "label": "Issue", "keywords": ["error"], "priority": 100,
    }]}

    decision = asyncio.run(gate.evaluate(
        group_id=GROUP, sender_id="u1", is_at_bot=False, message_text="has error",
        quoted_message_id="", timestamp=NOW,
    ))

    assert decision.action == "reply"
    assert decision.reason == "keyword:issue"


def test_an_awake_group_is_not_affected():
    decision = _evaluate(_FakeAttention(dormant=False))

    assert decision.action == "reply"


# ── 设置项 ──────────────────────────────────────────────────────────

def test_the_three_keys_are_declared_with_the_agreed_defaults():
    assert settings_schema.BY_KEY["dormancy_enabled"].default is True
    assert settings_schema.BY_KEY["dormancy_idle_seconds"].default == 1800
    assert settings_schema.BY_KEY["dormancy_wake_score"].default == pytest.approx(2.0)
    for key in ("dormancy_enabled", "dormancy_idle_seconds", "dormancy_wake_score"):
        assert key in settings_schema.SAVEABLE_KEYS


def test_there_is_no_auto_wake_key():
    """「到点自动醒」连配置键都不该存在（2026-09-29 使用者否掉）。"""
    assert "dormancy_auto_wake_seconds" not in settings_schema.BY_KEY
    assert "dormancy_auto_wake_seconds" not in settings_schema.SAVEABLE_KEYS
    assert "dormancy_auto_wake_seconds" not in settings_schema.defaults()
