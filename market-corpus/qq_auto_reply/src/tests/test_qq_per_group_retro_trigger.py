"""「这个群凉转热」时触发回溯补回 —— 触发点从跨群焦点切换换成**每群自己的状态迁移**。

2026-09-29 之前：回溯补回挂在**跨群焦点切换**上（`check_focus_shift()` 由 dispatcher 调，
拿到 `FocusShiftResult` 再调 `run_retroactive_review(new_focus)`）。跨群取舍删掉后
（使用者口径：「每个群自己管自己的注意力」）没有切换事件，判据换成每群自己的判定：
`attention.is_in_conversation(group_id)` 从 False 变 True 的那一下。

这些断言钉住那个迁移，以及"别的群的状态不许影响本群"。
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from plugin.plugins.qq_auto_reply.attention_gate_service import QQAttentionGateService


class _FakeAttention:
    """只出「本群在不在聊」，并记下问过哪些群。"""

    def __init__(self, *, warm: set[str] | None = None, now: int = 1000):
        #: 「哪些群在聊」是**按群**的集合：谁在里面谁算热。用集合而不是一个布尔，
        #: 是为了让"判据落在本群身上吗"这件事可以被机械验证（去问别的群会直接答 False）。
        self.warm = set(warm or ())
        self._now = now
        self.asked: list[str] = []

    def _enabled(self) -> bool:
        return True

    def _current_time(self) -> int:
        return self._now

    def is_in_conversation(self, group_id: str) -> bool:
        self.asked.append(str(group_id))
        return str(group_id) in self.warm

    def get_last_focus_at(self, group_id: str) -> int:
        return 0

    def mark_focus(self, group_id: str) -> None:
        pass


def _plugin(attention) -> SimpleNamespace:
    return SimpleNamespace(
        attention_service=attention,
        logger=SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None),
        _emit_log=lambda *a, **k: None,
        _qq_settings={},
    )


def test_build_ignored_summary_uses_text_field():
    """The retroactive summary reads the ``text`` field of a backlog item
    (the key produced by ``QQBacklogMessage.to_dict``), not the nonexistent
    ``message_text`` -- otherwise the original message content would always be
    empty in the summary and the LLM would only see `[N] sender:  (id=...)`.
    """
    items = [
        {"sender_id": "820040531", "sender_nickname": "", "text": "anyone there?", "message_id": "19464022"},
        {"sender_id": "3429924750", "text": "yes here", "message_id": "660539476"},
    ]
    summary = QQAttentionGateService._build_ignored_summary(items)
    assert "[1] 820040531: anyone there? (id=19464022)" in summary
    assert "[2] 3429924750: yes here (id=660539476)" in summary
    # The summary must not contain an empty-content row
    assert "820040531:  (id=" not in summary


def test_first_sighting_is_not_a_transition():
    """第一次见到这个群（还没有上一次判定）不算"凉转热" —— 否则重载后会白补一次。"""
    attention = _FakeAttention(warm={"g1"})
    gate = QQAttentionGateService(_plugin(attention))

    assert gate._note_in_conversation("g1") is False
    assert gate._in_conversation["g1"] is True


def test_cold_to_warm_is_a_transition():
    """凉 → 热 = 迁移（该回头看看这个群这段时间聊了什么）。"""
    attention = _FakeAttention(warm=set())
    gate = QQAttentionGateService(_plugin(attention))
    gate._note_in_conversation("g1")          # 上一次判定：凉

    attention.warm.add("g1")
    assert gate._note_in_conversation("g1") is True


def test_warm_to_cold_is_not_a_transition():
    """热 → 凉不是补回的时机（她刚刚才在这个群说过话）。"""
    attention = _FakeAttention(warm={"g1"})
    gate = QQAttentionGateService(_plugin(attention))
    gate._note_in_conversation("g1")

    attention.warm.discard("g1")
    assert gate._note_in_conversation("g1") is False


def test_staying_warm_is_not_a_transition():
    """一直在聊的群不该每来一条消息就补一次。"""
    attention = _FakeAttention(warm={"g1"})
    gate = QQAttentionGateService(_plugin(attention))
    gate._note_in_conversation("g1")

    assert gate._note_in_conversation("g1") is False
    assert gate._note_in_conversation("g1") is False


def test_the_transition_is_per_group():
    """两个群各自记各的：g2 的热闹不许把 g1 的判定顶掉。"""
    attention = _FakeAttention(warm={"g2"})
    gate = QQAttentionGateService(_plugin(attention))
    gate._note_in_conversation("g1")          # g1 凉
    gate._note_in_conversation("g2")          # g2 热（第一次见到，不算迁移）

    assert gate._in_conversation == {"g1": False, "g2": True}

    attention.warm.add("g1")
    assert gate._note_in_conversation("g1") is True, "g1 自己的迁移丢了"


def test_mark_active_schedules_a_retro_review_on_the_transition():
    """`_mark_active`（每条被放行的消息都会调）在迁移那一刻排一次回溯补回。"""
    attention = _FakeAttention(warm=set())
    plugin = _plugin(attention)
    gate = QQAttentionGateService(plugin)
    scheduled: list[str] = []
    gate._schedule_retro_review = lambda group_id: scheduled.append(group_id)  # type: ignore[method-assign]

    gate._mark_active("g1")                   # 第一次：只记账
    assert scheduled == []

    attention.warm.add("g1")
    gate._mark_active("g1")                   # 凉转热 → 排一次
    assert scheduled == ["g1"]

    gate._mark_active("g1")                   # 继续热着 → 不再重复排
    assert scheduled == ["g1"]


def test_schedule_retro_review_creates_a_task_and_keeps_it_referenced():
    """排出去的任务要留在 `_retro_tasks` 里（强引用），否则它可能被 GC 掉。"""
    attention = _FakeAttention()
    gate = QQAttentionGateService(_plugin(attention))
    ran: list[str] = []

    async def fake_review(group_id: str) -> list[str]:
        ran.append(group_id)
        return []

    gate.run_retroactive_review = fake_review  # type: ignore[method-assign]

    async def run_and_assert() -> None:
        gate._schedule_retro_review("g1")
        assert len(gate._retro_tasks) == 1, "任务没有被强引用"
        await asyncio.gather(*list(gate._retro_tasks))

    asyncio.run(run_and_assert())
    assert ran == ["g1"]
    assert gate._retro_tasks == set(), "任务跑完后应从集合里摘掉"


# ── 补回的两道闸：门槛与按群冷却 ──────────────────────────────────


class _BacklogStore:
    def __init__(self, messages: list[dict]):
        self.messages = messages
        self.queries: list[str] = []

    async def get_unreviewed_messages_since(self, group_id, *, since_timestamp=0, limit=30):
        self.queries.append(str(group_id))
        return list(self.messages)[:limit]


class _BacklogService:
    def __init__(self):
        self.marked_all: list[str] = []
        self.marked_ids: list[tuple[str, set]] = []

    async def mark_group_reviewed_payload(self, group_id, message_ids=None):
        if message_ids is None:
            self.marked_all.append(str(group_id))
        else:
            self.marked_ids.append((str(group_id), set(message_ids)))


def _retro_plugin(messages: list[dict], settings: dict | None = None):
    attention = _FakeAttention()
    store = _BacklogStore(messages)
    service = _BacklogService()

    async def _explode(*_a, **_k):
        raise AssertionError("不该走到 LLM（这一轮应当被闸门挡下）")

    plugin = SimpleNamespace(
        attention_service=attention,
        logger=SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None),
        _emit_log=lambda *a, **k: None,
        _qq_settings=dict(settings or {}),
        backlog_store=store,
        backlog_service=service,
        reply_pipeline=SimpleNamespace(run=_explode),
    )
    return plugin, store, service


def _messages(count: int) -> list[dict]:
    return [
        {"message_id": f"m{i}", "sender_name": "甲", "text": f"第{i}条"}
        for i in range(count)
    ]


def test_retro_is_skipped_below_the_min_unreviewed():
    """只攒了一两条没看过的消息时不补 —— 她本来就在答当前这条，白花一次 LLM 调用。"""
    plugin, _store, service = _retro_plugin(_messages(2))
    gate = QQAttentionGateService(plugin)

    assert asyncio.run(gate.run_retroactive_review("g1")) == []
    assert service.marked_all == ["g1"], "没补也要把这批标成已看，否则下次还会被当成新的"
    assert gate._last_retro_at == {}, "被门槛挡下的那次不该消耗冷却"


def test_retro_runs_when_the_backlog_is_big_enough(monkeypatch):
    """攒够门槛才真正补一次，并记下这次时刻（冷却从这里算）。"""
    plugin, _store, _service = _retro_plugin(_messages(6))
    gate = QQAttentionGateService(plugin)
    calls: list[str] = []

    async def fake_pipeline(_request):
        calls.append("ran")
        return SimpleNamespace(action="ignore", reply_text="")

    plugin.reply_pipeline = SimpleNamespace(run=fake_pipeline)
    monkeypatch.setattr(
        "plugin.plugins.qq_auto_reply.attention_gate_service.QQAttentionGateService._build_ignored_summary",
        staticmethod(lambda msgs: "摘要"),
    )

    class _SessionSvc:
        def session_history_len(self, key):
            return 0

        def record_synthetic_prompt_rows(self, key, before):
            return None

    plugin.session_memory_service = _SessionSvc()
    plugin.runtime_service = SimpleNamespace(record_pipeline_outcome=lambda **k: None)
    plugin._admin_qq = "820040531"

    async def _locked(_key, fn):
        return await fn()

    plugin._run_with_session_lock = _locked

    assert asyncio.run(gate.run_retroactive_review("g1")) == []
    assert calls == ["ran"], "攒够了却没补"
    assert gate._last_retro_at["g1"] == 1000


def test_retro_respects_the_per_group_cooldown():
    """同一个群短期内只补一次（跨群取舍删掉后"凉转热"比"焦点切换"频繁得多）。"""
    plugin, _store, service = _retro_plugin(_messages(6))
    gate = QQAttentionGateService(plugin)
    gate._last_retro_at["g1"] = 1000          # 刚刚补过
    plugin._qq_settings["retroactive_review_cooldown_seconds"] = 300

    assert asyncio.run(gate.run_retroactive_review("g1")) == []
    assert service.marked_all == [] and service.marked_ids == [], "冷却期内的补回真的跑了"
    assert _store.queries == [], "冷场判定前就先查了库（白查一趟）"


def test_retro_cooldown_is_per_group():
    """另一个群的冷却与本群无关（每群自己的节奏）。"""
    plugin, store, _service = _retro_plugin(_messages(2))
    gate = QQAttentionGateService(plugin)
    gate._last_retro_at["g2"] = 1000

    asyncio.run(gate.run_retroactive_review("g1"))

    assert store.queries == ["g1"], "g2 的冷却把 g1 的补回也挡了"
