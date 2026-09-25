"""日志信号消费端测试：LogDetector 映射 + 场景门控 + 赛后胜负播报。"""

from __future__ import annotations

from neko_calabiqiu.adapters.neko_dispatcher import _alert_pool
from neko_calabiqiu.core.arbiter import Arbiter
from neko_calabiqiu.core.contracts import (
    DEGRADED,
    EVENT_CATALOG,
    IDLE,
    IN_GAME,
    CbqConfig,
    CompanionEvent,
    EventSpec,
    SceneState,
)
from neko_calabiqiu.core.runtime import CompanionRuntime
from neko_calabiqiu.core.safety_guard import SafetyGuard
from neko_calabiqiu.detectors.log import LogDetector


def _event_item(seq: int, event_type: str, payload: dict, ts: float = 100.0) -> dict:
    return {
        "event_seq": seq,
        "frame_seq": seq,
        "ts": ts,
        "type": event_type,
        "payload": payload,
    }


def test_log_detector_maps_all_log_events():
    state = SceneState(
        connected=True,
        game_running=True,
        events_tail=[
            _event_item(1, "match_phase", {"phase": "character_select"}),
            _event_item(2, "match_phase", {"phase": "in_progress"}),
            _event_item(3, "player_death", {}),
            _event_item(4, "match_result", {"result": "win"}),
            _event_item(5, "match_result", {"result": "lose"}),
        ],
    )
    det = LogDetector()
    events = det.feed(SceneState(connected=True), state)
    assert [(e.event_id, e.level) for e in events] == [
        ("character_select", "warning"),
        ("match_started", "warning"),
        ("player_death", "critical"),
        ("match_win", "warning"),
        ("match_lose", "warning"),
    ]
    # 幂等：同 seq 不重复
    assert det.feed(SceneState(connected=True), state) == []


def test_log_detector_ignores_match_end_and_unknown():
    state = SceneState(
        connected=True,
        game_running=True,
        events_tail=[
            _event_item(1, "match_phase", {"phase": "match_end"}),
            _event_item(2, "match_result", {"result": "draw"}),
            _event_item(3, "some_future_event", {}),
        ],
    )
    det = LogDetector()
    assert det.feed(SceneState(connected=True), state) == []


def test_runtime_engine_includes_log_detector():
    runtime = CompanionRuntime(CbqConfig())
    ids = {d.id for d in runtime.engine.detectors}
    assert "log" in ids


def test_match_result_survives_require_game_process_after_quit():
    """进程已退出（game_running=False 且状态稳定）时胜负事件仍可播报。"""
    runtime = CompanionRuntime(
        CbqConfig(dry_run=True, require_game_process=True),
        push_event=lambda _e, _dry: "dry_run(x)",
    )
    # prev 与 cur 同为退出态：不会触发 lifecycle 兜底的 game_stopped 抢占
    prev = SceneState(connected=True, game_running=False)
    cur = SceneState(
        connected=True,
        game_running=False,
        events_tail=[_event_item(10, "match_result", {"result": "win"})],
    )
    result = runtime.evaluate(prev, cur, now=200.0)
    assert result.chosen is not None
    assert result.chosen.event_id == "match_win"


def test_player_death_blocked_when_game_not_running():
    runtime = CompanionRuntime(
        CbqConfig(dry_run=True, require_game_process=True),
        push_event=lambda _e, _dry: "dry_run(x)",
    )
    prev = SceneState(connected=True, game_running=False)
    cur = SceneState(
        connected=True,
        game_running=False,
        events_tail=[_event_item(11, "player_death", {})],
    )
    result = runtime.evaluate(prev, cur, now=200.0)
    assert result.chosen is None  # 进程已退出时死亡事件被挡


def test_match_category_gated_in_scenarios():
    select = CompanionEvent("character_select", level="warning", created_at=1.0)
    win = CompanionEvent("match_win", level="warning", created_at=1.0)
    # IDLE 允许 match 类别（赛后结果）
    chosen_idle, _ = Arbiter(SafetyGuard(CbqConfig())).decide([select, win], IDLE, now=10.0)
    assert chosen_idle is not None and chosen_idle.event_id == "match_win"
    # IN_GAME 允许
    chosen_game, _ = Arbiter(SafetyGuard(CbqConfig())).decide([select, win], IN_GAME, now=20.0)
    assert chosen_game is not None
    # DEGRADED 仍允许无屏日志信号，只有视觉类被挡
    chosen_degraded, _ = Arbiter(SafetyGuard(CbqConfig())).decide([select, win], DEGRADED, now=30.0)
    assert chosen_degraded is not None and chosen_degraded.event_id == "match_win"


def test_degraded_runtime_keeps_log_events_but_gates_vision():
    runtime = CompanionRuntime(
        CbqConfig(dry_run=True, require_game_process=True),
        push_event=lambda _e, _dry: "dry_run(x)",
    )
    prev = SceneState(connected=True, game_running=True, degraded=True)
    cur = SceneState(
        connected=True,
        game_running=True,
        degraded=True,
        events_tail=[
            _event_item(20, "target_appeared", {"bearing": "screen_left", "threat_level": "far"}),
            _event_item(21, "match_phase", {"phase": "character_select"}),
            _event_item(22, "player_death", {}),
        ],
    )
    result = runtime.evaluate(prev, cur, now=200.0)
    assert result.scenario == DEGRADED
    assert {c.event_id for c in result.candidates} == {"enemy_spotted", "character_select", "player_death"}
    assert result.chosen is not None
    assert result.chosen.event_id == "player_death"
    assert any(item["event_id"] == "enemy_spotted" and item["reason"].startswith("scenario_gated") for item in result.chain)


def test_catalog_has_all_log_events_with_sane_spec():
    for event_id in ("character_select", "match_started", "player_death", "match_win", "match_lose"):
        spec = EVENT_CATALOG[event_id]
        assert isinstance(spec, EventSpec)
        assert spec.cooldown_seconds >= 0
    # 优先级排序：死亡应压过选人
    assert EVENT_CATALOG["player_death"].priority > EVENT_CATALOG["character_select"].priority


def test_alert_pool_covers_log_events():
    for event_id in ("character_select", "match_started", "player_death", "match_win", "match_lose"):
        pool = _alert_pool(CompanionEvent(event_id))
        assert pool
        assert all("喵" in line for line in pool)