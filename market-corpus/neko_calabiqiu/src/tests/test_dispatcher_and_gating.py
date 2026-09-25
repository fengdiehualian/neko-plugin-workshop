from __future__ import annotations

from neko_calabiqiu.adapters.neko_dispatcher import NekoDispatcher, _alert_pool
from neko_calabiqiu.adapters.runtime_timeline import RuntimeTimeline
from neko_calabiqiu.core.arbiter import Arbiter
from neko_calabiqiu.core.context_activation import should_activate_running_game_context
from neko_calabiqiu.core.contracts import (
    IDLE,
    IN_GAME,
    CbqConfig,
    CompanionEvent,
    SceneState,
)
from neko_calabiqiu.core.runtime import CompanionRuntime
from neko_calabiqiu.core.safety_guard import SafetyGuard


class _FakePlugin:
    def __init__(self) -> None:
        self.cfg = CbqConfig(
            dry_run=False,
            output_backpressure_seconds=100,
            output_event_max_age_seconds=8,
            vision_output_event_max_age_seconds=8,
        )
        self.messages = []
        self.speeches = []

    def push_message(self, **kwargs):
        self.messages.append(kwargs)

    def speak_project_tts(self, line, **kwargs):
        self.speeches.append((line, kwargs))
        return {"ok": True, "audio_queued": True, "method": "project_tts"}


def test_dispatcher_dry_run_and_context():
    plugin = _FakePlugin()
    plugin.cfg.dry_run = True
    d = NekoDispatcher(plugin)
    assert d.push_event(CompanionEvent("game_started", level="critical"), dry_run=True).startswith("dry_run")
    assert d.push_event(CompanionEvent("enemy_spotted"), dry_run=True).startswith("dry_run")
    assert plugin.messages == []


def test_dispatcher_real_context_uses_push_message():
    plugin = _FakePlugin()
    d = NekoDispatcher(plugin)
    assert d.push_event(CompanionEvent("game_started", level="critical"), dry_run=False).startswith("pushed")
    assert len(plugin.messages) == 1
    assert plugin.messages[0]["ai_behavior"] == "read"
    assert plugin.messages[0]["metadata"]["kind"] == "context"


def test_live_activation_catches_up_only_for_running_uninjected_context():
    assert should_activate_running_game_context(
        dry_run=False, game_running=True, context_active=False
    )
    assert not should_activate_running_game_context(
        dry_run=True, game_running=True, context_active=False
    )
    assert not should_activate_running_game_context(
        dry_run=False, game_running=False, context_active=False
    )
    assert not should_activate_running_game_context(
        dry_run=False, game_running=True, context_active=True
    )


def test_dispatcher_real_push_and_backpressure():
    plugin = _FakePlugin()
    d = NekoDispatcher(plugin)
    e1 = CompanionEvent("enemy_spotted", level="warning", created_at=1000.0)
    # monkey: use created_at near now
    import time

    e1.created_at = time.time()
    assert d.push_event(e1, dry_run=False).startswith("pushed")
    assert len(plugin.speeches) == 1
    assert plugin.messages == []
    e2 = CompanionEvent("enemy_left_view", level="warning", created_at=time.time())
    assert "output_backpressure" in d.push_event(e2, dry_run=False)


def test_delivery_reuses_short_reply_and_host_callback_contract():
    plugin = _FakePlugin()
    plugin.cfg.output_backpressure_seconds = 0
    timeline = RuntimeTimeline(observability_enabled=True)
    d = NekoDispatcher(plugin, timeline=timeline, clock=lambda: 107.5)
    event = CompanionEvent(
        "enemy_spotted",
        level="warning",
        edge="enter",
        created_at=100.0,
        payload={"bearing": "screen_right"},
    )

    assert d.push_event(event, dry_run=False).startswith("pushed(")
    line, speech_kwargs = plugin.speeches[0]
    delivery = d._build_delivery(
        event,
        recommended_reply=line,
        target_lanlan="",
        freshness={
            "event_ts": 100.0,
            "event_age_seconds": 7.5,
            "event_max_age_seconds": 8.0,
            "event_expires_at": 108.0,
        },
    )
    metadata = delivery.metadata
    assert line == "右边发现敌人喵！"
    assert speech_kwargs["event_id"] == "enemy_spotted"
    # 普通方位警报不再打断正在进行的对话语音（只有贴脸 enemy_nearby/critical 才打断）
    assert speech_kwargs["interrupt_audio"] is False
    assert delivery.ai_behavior == "blind"
    assert delivery.visibility == ("chat",)
    assert delivery.text == "右边发现敌人喵！"
    assert metadata["reply_contract"] == "short_tts_line"
    assert metadata["plugin_recommended_reply"] == "右边发现敌人喵！"
    assert metadata["plugin_owned_output"] is True
    assert metadata["direct_tts"] is True
    assert metadata["delivery_method"] == "project_tts"
    assert metadata["replace_pending"] is True
    assert metadata["interrupt_policy"] == "drop"
    assert metadata["delivery_ttl_seconds"] == 0.5
    assert metadata["host_callback_contract"]["delivery"]["max_age_seconds"] == 8.0
    assert timeline.snapshot()["last_output_status"]["stage"] == "dispatcher_pushed"


def test_repeated_event_is_collapsed_before_host_push():
    plugin = _FakePlugin()
    plugin.cfg.output_backpressure_seconds = 0
    now = [100.0]
    d = NekoDispatcher(plugin, clock=lambda: now[0])
    first = CompanionEvent(
        "enemy_spotted", created_at=100.0, payload={"bearing": "screen_left"}
    )
    assert d.push_event(first, dry_run=False).startswith("pushed(")
    now[0] = 100.5
    repeated = CompanionEvent(
        "enemy_spotted", created_at=100.5, payload={"bearing": "screen_left"}
    )
    assert "repeated_event_collapsed" in d.push_event(repeated, dry_run=False)

    now[0] = 106.0
    fresh = CompanionEvent(
        "enemy_spotted", created_at=106.0, payload={"bearing": "screen_left"}
    )
    assert d.push_event(fresh, dry_run=False).startswith("pushed(")
    assert len(plugin.speeches) == 2


def test_expired_event_is_observable_and_never_pushed():
    plugin = _FakePlugin()
    timeline = RuntimeTimeline(observability_enabled=True)
    d = NekoDispatcher(plugin, timeline=timeline, clock=lambda: 110.0)
    event = CompanionEvent("enemy_spotted", created_at=100.0)

    assert "event_expired" in d.push_event(event, dry_run=False)
    assert plugin.messages == []
    assert plugin.speeches == []
    output = timeline.snapshot()["last_output_status"]
    assert output["stage"] == "dispatcher_suppressed"
    assert output["reason"] == "event_expired"


def test_runtime_rolls_back_arbiter_and_safety_when_dispatcher_suppresses():
    event = CompanionEvent("enemy_spotted", created_at=100.0)

    class _FixedEngine:
        def feed(self, _prev, _cur):
            return [event]

    cfg = CbqConfig(dry_run=False, global_rate_limit_seconds=60)
    runtime = CompanionRuntime(cfg, push_event=lambda _event, _dry_run: "suppressed(test)")
    runtime.engine = _FixedEngine()
    state = SceneState(connected=True, game_running=True)

    first = runtime.evaluate(state, state, now=100.0)
    second = runtime.evaluate(state, state, now=101.0)

    assert first.chosen is event
    assert second.chosen is event
    # 被 suppress（未真正占用输出）的投递必须回滚仲裁与安全时钟，
    # 否则同一事件会被当作“已消耗限流/冷却”，导致恢复后无法再触发。
    assert runtime.arbiter._last_fired == {}
    assert runtime.safety._last_fire_at == 0.0


def test_direct_alerts_rotate_without_repeating_the_same_line():
    plugin = _FakePlugin()
    plugin.cfg.output_backpressure_seconds = 0
    now = [100.0]
    d = NekoDispatcher(plugin, clock=lambda: now[0])

    for offset in (0.0, 7.0, 14.0):
        now[0] = 100.0 + offset
        event = CompanionEvent(
            "enemy_spotted",
            created_at=now[0],
            payload={"bearing": "screen_right"},
        )
        assert d.push_event(event, dry_run=False).startswith("pushed(")

    lines = [line for line, _kwargs in plugin.speeches]
    assert lines == [
        "右边发现敌人喵！",
        "注意右边，有敌人喵！",
        "右边来人啦喵！",
    ]
    assert len(set(lines)) == len(lines)


def test_every_direct_alert_variant_contains_cat_suffix():
    events = [
        CompanionEvent("enemy_spotted", payload={"bearing": "screen_left"}),
        CompanionEvent("enemy_spotted"),
        CompanionEvent("enemy_nearby", payload={"bearing": "screen_right"}),
        CompanionEvent("enemy_nearby"),
        CompanionEvent("enemy_left_view"),
        CompanionEvent("multi_threat"),
    ]
    lines = [line for event in events for line in _alert_pool(event)]
    assert lines
    assert all("喵" in line for line in lines)


def test_idle_scenario_gates_vision():
    arb = Arbiter(SafetyGuard(CbqConfig()))
    vision = CompanionEvent("enemy_spotted", level="warning", created_at=1.0)
    life = CompanionEvent("game_stopped", level="critical", created_at=1.0)
    chosen, chain = arb.decide([vision, life], IDLE, now=10.0)
    assert chosen is not None and chosen.event_id == "game_stopped"
    assert any(c.get("reason", "").startswith("scenario_gated") for c in chain)

    chosen2, _ = arb.decide([vision], IN_GAME, now=100.0)
    assert chosen2 is not None and chosen2.event_id == "enemy_spotted"


def test_only_near_critical_alert_interrupts_audio():
    """普通方位警报不打断对话；只有贴脸 enemy_nearby(critical) 才打断语音。"""
    plugin = _FakePlugin()
    plugin.cfg.output_backpressure_seconds = 0
    now = [100.0]
    d = NekoDispatcher(plugin, clock=lambda: now[0])

    # 普通发现敌人 → 不打断对话语音
    spotted = CompanionEvent(
        "enemy_spotted", level="warning", created_at=now[0],
        payload={"bearing": "screen_right"},
    )
    assert d.push_event(spotted, dry_run=False).startswith("pushed(")
    _, kw = plugin.speeches[-1]
    assert kw["interrupt_audio"] is False

    # 贴脸 enemy_nearby(critical) → 允许打断
    now[0] += 5.0
    nearby = CompanionEvent("enemy_nearby", level="critical", created_at=now[0])
    assert d.push_event(nearby, dry_run=False).startswith("pushed(")
    _, kw2 = plugin.speeches[-1]
    assert kw2["interrupt_audio"] is True


def test_chatter_event_uses_chatter_pool():
    """chatter 事件应从 alerts.chatter 话术池取词，而不是误用敌情话术（enemy_spotted）。"""
    chatter_lines = _alert_pool(CompanionEvent("chatter", level="info", created_at=0.0))
    assert chatter_lines
    known = {
        "暂时没看到敌人喵，先喘口气～",
        "这波稳住喵，我陪着你看！",
        "有点安静喵，注意耳朵听脚步声哦～",
        "别紧张喵，有我陪你！",
        "打得很稳喵，继续保持！",
    }
    assert all(line in known for line in chatter_lines)
    # 与敌情话术区分开：chatter 用的不是 enemy_spotted 的 bearing 话术池
    assert set(chatter_lines) != set(_alert_pool(CompanionEvent("enemy_spotted")))
