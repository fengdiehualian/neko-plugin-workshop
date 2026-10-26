"""Main-entry lifecycle contracts (audit P0-2 / P0-3 / P1-1 / P1-2 / P1-3).

These tests pin the orchestration guarantees that live only in ``__init__.py``:

* start/stop are mutually exclusive and epoch-guarded; a stop waits for an
  in-flight tick and a stale tick can never dispatch/read afterwards;
* an untrusted snapshot is the latest snapshot but never detector ``previous``;
* the first trusted frame after a pause rebases the match tracker instead of
  diffing across the unseen gap;
* every trusted non-dry-run tick drains the dispatcher, even with no candidate;
* stop clears dispatcher queue, arbiter, tracker and capture state (HUD/录像
  链路已移除)。

The host SDK is stubbed by ``tests/conftest.py``.  Main-entry helpers are
reused from :mod:`test_api_contract`, which loads ``__init__.py`` with the
recording SDK stub.
"""

from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from kards_companion.state.model import (
    E_HQ_DAMAGE,
    E_MATCH_END,
    E_MATCH_START,
    SCENE_MATCH,
    SCENE_MENU,
    SCENE_VICTORY,
    GameEvent,
    MatchState,
)
from test_api_contract import (
    MAIN,
    PLUGIN_CLASS,
    FakeCapture,
    FakeClassifier,
    FakeConfig,
    FakeLogger,
    FakeUiDetector,
    blocked_health,
    capture_result,
    trusted_health,
)


class StoppableCapture(FakeCapture):
    def __init__(self, results: list[Any], healths: list[Any]) -> None:
        super().__init__(results, healths)
        self.stop_calls = 0

    def stop(self) -> None:
        self.stop_calls += 1


@pytest.fixture
def plugin(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Any:
    logger = FakeLogger()
    pushes: list[dict[str, Any]] = []
    base = MAIN.NekoPluginBase
    monkeypatch.setattr(
        base, "enable_file_logging",
        lambda self, log_level=None: logger, raising=False)
    monkeypatch.setattr(
        base, "data_path", lambda self, name: Path(tmp_path) / name, raising=False)

    def record_push(self: Any, **kwargs: Any) -> dict[str, Any]:
        pushes.append(kwargs)
        return {"submitted": True}

    monkeypatch.setattr(base, "push_message", record_push, raising=False)
    instance = PLUGIN_CLASS(ctx=object())
    instance.config = FakeConfig()
    instance._events_log = []
    instance.pushes = pushes
    instance.scheduler.due = lambda *args, **kwargs: True
    # 编排测试不关心 dHash 帧差门控：恒脏让 unit/card 任务每 tick 可推进。
    monkeypatch.setattr(type(instance), "_change_dirty", _AlwaysDirty(),
                        raising=False)
    return instance


class _AlwaysDirty:
    """读恒 True、写忽略的描述符，绕过 change 任务的 dHash 门控。"""

    def __get__(self, obj, objtype=None):
        return True

    def __set__(self, obj, value):
        pass


# --------------------------------------------------------------- P0-3 snapshots

def test_untrusted_snapshot_is_latest_but_never_detector_previous(
    plugin: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    plugin._monitoring = True
    plugin._cfg = {"tick_ms": 333, "dry_run": True, "chatter_interval_seconds": 0}
    plugin.screen_classifier = FakeClassifier(SCENE_MATCH, True)
    plugin.ui_detector = FakeUiDetector()
    plugin._capture = FakeCapture(
        [
            capture_result(1, (0, 0, 0)),
            None,
            capture_result(3, (255, 255, 255)),
            capture_result(4, (10, 20, 30)),
        ],
        [trusted_health(1), blocked_health(), trusted_health(3), trusted_health(4)],
    )
    seen: list[tuple[Any, Any]] = []
    original = plugin.detectors.update

    def spy(previous: Any, current: Any) -> Any:
        seen.append((previous, current))
        return original(previous, current)

    monkeypatch.setattr(plugin.detectors, "update", spy)

    plugin._perceive_once()  # trusted #1
    first_trusted = plugin._last_trusted_snapshot
    plugin._perceive_once()  # untrusted: detectors must not run
    assert plugin._previous_snapshot is not None
    assert plugin._previous_snapshot.trusted is False
    untrusted = plugin._previous_snapshot
    assert plugin._last_trusted_snapshot is first_trusted

    plugin._perceive_once()  # resume: baseline, previous explicitly None
    second_trusted = plugin._last_trusted_snapshot
    assert second_trusted is not first_trusted
    plugin._perceive_once()  # normal trusted tick

    assert len(seen) == 3
    assert seen[0][0] is None
    assert seen[1][0] is None, "resume tick must not diff against stale memory"
    assert seen[2][0] is second_trusted
    assert all(previous is not untrusted for previous, _current in seen)


# ------------------------------------------------------------ P1-1 pause/resume

def test_pause_resume_first_frame_rebases_without_gap_event_storm(plugin: Any) -> None:
    plugin._monitoring = True
    plugin._cfg = {"tick_ms": 333, "chatter_interval_seconds": 0}
    plugin.screen_classifier = FakeClassifier(SCENE_MATCH, True)
    plugin.ui_detector = FakeUiDetector()
    plugin._capture = FakeCapture(
        [capture_result(1, (0, 0, 0)), None, capture_result(3, (255, 255, 255))],
        [trusted_health(1), blocked_health(), trusted_health(3)],
    )

    plugin._perceive_once()  # baseline match start
    assert [record["kind"] for record in plugin._events_log] == [E_MATCH_START]
    assert plugin.tracker.in_match

    plugin._perceive_once()  # untrusted: pause, no baseline update
    assert plugin._capture_pause_active is True
    plugin._perceive_once()  # resume: rebase instead of unit/turn diffs

    assert [record["kind"] for record in plugin._events_log] == [E_MATCH_START]
    assert plugin.tracker.in_match
    assert plugin.tracker.current is not None
    # HUD 数字链路已移除：my_hq 恒 None，恢复帧不再产生跨暂停的掉血 diff。
    assert plugin.tracker.current.my_hq is None
    assert any(entry["stage"] == "capture" and entry["outcome"] == "resumed"
               for entry in plugin.timeline.recent(20))


def test_pause_resume_through_menu_still_rebases_on_return_to_match(
    plugin: Any,
) -> None:
    class SequenceClassifier:
        def __init__(self, scenes: list[str]) -> None:
            self._scenes = list(scenes)
            self.calls: list[Any] = []
            self.scene = self._scenes[0]

        def classify(self, detections: Any) -> tuple[str, bool]:
            self.calls.append(detections)
            self.scene = self._scenes.pop(0)
            return self.scene, self.scene == SCENE_MATCH

    plugin._monitoring = True
    plugin._cfg = {"tick_ms": 333, "chatter_interval_seconds": 0}
    plugin.screen_classifier = SequenceClassifier(
        [SCENE_MATCH, SCENE_MENU, SCENE_MATCH])
    plugin.ui_detector = FakeUiDetector()
    plugin._capture = FakeCapture(
        [
            capture_result(1, (0, 0, 0)),
            None,
            capture_result(3, (50, 50, 50)),
            capture_result(4, (255, 255, 255)),
        ],
        [
            trusted_health(1),
            blocked_health(),
            trusted_health(3),
            trusted_health(4),
        ],
    )

    plugin._perceive_once()  # match baseline/start
    plugin._perceive_once()  # pause
    plugin._perceive_once()  # menu: still a resume baseline
    plugin._perceive_once()  # back to match without a gap event storm

    assert [record["kind"] for record in plugin._events_log] == [E_MATCH_START]
    assert plugin.tracker.in_match


# --------------------------------------------------------------- P1-2 dispatch

def test_every_trusted_tick_drains_even_without_candidates(
    plugin: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    plugin._monitoring = True
    plugin._cfg = {"tick_ms": 333, "chatter_interval_seconds": 0}
    plugin.screen_classifier = FakeClassifier(SCENE_MATCH, True)
    plugin.ui_detector = FakeUiDetector()
    plugin._capture = FakeCapture(
        [capture_result(1, (0, 0, 0)), capture_result(2, (0, 0, 0))],
        [trusted_health(1), trusted_health(2)],
    )
    ticks: list[int] = []
    original_tick = plugin.dispatcher.tick
    monkeypatch.setattr(
        plugin.dispatcher, "tick",
        lambda *a, **k: (ticks.append(1), original_tick(*a, **k))[1])

    plugin._perceive_once()  # match_start candidate
    plugin._perceive_once()  # quiet tick: no candidate, still ticks

    assert len(ticks) == 2


def test_dry_run_simulates_every_tick_without_host_push(
    plugin: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    plugin._monitoring = True
    plugin._cfg = {"tick_ms": 333, "dry_run": True, "chatter_interval_seconds": 0}
    plugin._apply_config({"kards": plugin._cfg})
    plugin.screen_classifier = FakeClassifier(SCENE_MATCH, True)
    plugin.ui_detector = FakeUiDetector()
    plugin._capture = FakeCapture(
        [capture_result(1, (0, 0, 0)), capture_result(2, (0, 0, 0))],
        [trusted_health(1), trusted_health(2)],
    )
    ticks: list[int] = []
    original_tick = plugin.dispatcher.tick
    monkeypatch.setattr(
        plugin.dispatcher, "tick",
        lambda *a, **k: (ticks.append(1), original_tick(*a, **k))[1])

    plugin._perceive_once()
    plugin._perceive_once()

    assert len(ticks) == 2, "dry-run must run the full tick/arbiter/safety chain"
    assert plugin.dispatcher.pending == 0
    assert plugin.pushes == []
    assert plugin.dispatcher.stats()["simulated"] >= 1
    assert plugin.dispatcher.simulated_records


# --------------------------------------------------------------- P1-3 stop

def test_stop_clears_dispatcher_arbiter_tracker_hud_snapshots(plugin: Any) -> None:
    plugin._monitoring = True
    plugin._cfg = {"tick_ms": 333}
    plugin.screen_classifier = FakeClassifier(SCENE_MATCH, True)
    plugin.ui_detector = FakeUiDetector()
    capture = StoppableCapture([], [blocked_health()])
    plugin._capture = capture

    plugin.tracker.update(MatchState(
        scene=SCENE_MATCH, in_match=True, my_hq=20, enemy_hq=20))
    assert plugin.tracker.in_match and plugin.tracker.game_number == 1

    marked = GameEvent(E_HQ_DAMAGE, 5, "marked",
                       details={"game": 1, "side": "mine", "loss": 5})
    queued = GameEvent(E_HQ_DAMAGE, 5, "queued",
                       details={"game": 1, "side": "enemy", "loss": 5})
    plugin.arbiter.mark_submitted(marked, now=time.time())
    plugin.arbiter.reset()
    assert plugin.dispatcher.submit(
        queued, MatchState(), now=time.time()).accepted
    assert plugin.dispatcher.pending == 1
    old_epoch = plugin._epoch
    pushes_before = len(plugin.pushes)
    classifier_calls = len(plugin.screen_classifier.calls)

    asyncio.run(plugin.monitor_stop())

    assert plugin._monitoring is False
    assert plugin._capture is None
    assert capture.stop_calls == 1
    assert plugin.dispatcher.pending == 0
    assert plugin._previous_snapshot is None
    assert plugin._last_trusted_snapshot is None
    assert plugin.tracker.in_match is False
    assert plugin.tracker.game_number == 0
    assert plugin._current_health().status == "stopped"
    assert plugin.arbiter.decide(
        marked, MatchState(), now=time.time() + 1).allowed

    # A tick holding the pre-stop capture/epoch must abort before detection
    # or push.
    plugin._capture = capture
    plugin._perceive_once(old_epoch)
    assert len(plugin.screen_classifier.calls) == classifier_calls
    assert len(plugin.pushes) == pushes_before
    assert plugin.dispatcher.pending == 0


def test_pause_and_stop_have_no_recording_side_effects(plugin: Any) -> None:
    # 录像链路已删除：pause/stop 不再触碰任何 recorder 对象。
    plugin._monitoring = True
    plugin._cfg = {"tick_ms": 333}
    plugin._session_id = "session-x"
    plugin.tracker.set_session_id(plugin._session_id)

    asyncio.run(plugin.monitor_pause())
    asyncio.run(plugin.monitor_resume())
    asyncio.run(plugin.monitor_stop())

    assert not hasattr(plugin, "recorder")
    assert not hasattr(plugin, "_on_capture_frame")
    recording_stages = [entry for entry in plugin.timeline.recent(30)
                        if entry["stage"] == "recording"]
    assert recording_stages == []
    assert plugin._session_id == ""


def test_resume_rebases_match_and_unit_trackers(plugin: Any,
                                                monkeypatch: pytest.MonkeyPatch) -> None:
    plugin._monitoring = True
    plugin.tracker.update(MatchState(
        scene=SCENE_MATCH, in_match=True, my_hq=20, enemy_hq=20))
    calls: list[str] = []
    monkeypatch.setattr(
        plugin.tracker, "rebase", lambda: calls.append("match"))
    monkeypatch.setattr(
        plugin._unit_tracker, "rebase", lambda: calls.append("unit"))

    asyncio.run(plugin.monitor_resume())

    assert calls == ["match", "unit"]
    assert plugin._user_paused is False


def test_stop_waits_for_inflight_tick_and_stale_tick_cannot_dispatch(
    plugin: Any,
) -> None:
    plugin._monitoring = True
    plugin._cfg = {"tick_ms": 333}
    plugin.screen_classifier = FakeClassifier(SCENE_MATCH, True)
    plugin.ui_detector = FakeUiDetector()
    started = threading.Event()
    release = threading.Event()

    class BlockingCapture(StoppableCapture):
        def capture_frame(self) -> Any:
            started.set()
            release.wait(5.0)
            return capture_result(1, (0, 0, 0))

        def health_snapshot(self) -> Any:
            return trusted_health(1)

    capture = BlockingCapture([], [trusted_health(1)])
    plugin._capture = capture
    old_epoch = plugin._epoch
    tick_thread = threading.Thread(
        target=plugin._run_tick, args=(old_epoch,), daemon=True)
    tick_thread.start()
    assert started.wait(2.0)

    stop_done = threading.Event()

    def run_stop() -> None:
        asyncio.run(plugin._stop_pipeline())
        stop_done.set()

    stopper = threading.Thread(target=run_stop, daemon=True)
    stopper.start()
    assert not stop_done.wait(0.2), "stop must wait for the in-flight tick"

    release.set()
    tick_thread.join(5.0)
    stopper.join(5.0)

    assert not tick_thread.is_alive()
    assert stop_done.is_set()
    assert plugin._capture is None
    assert plugin.screen_classifier.calls == [], "stale tick must exit before detection"
    assert plugin.pushes == []
    assert plugin.dispatcher.pending == 0


def test_start_resets_state_and_restart_uses_a_new_capture(
    plugin: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    created: list[Any] = []

    class StartableCapture(StoppableCapture):
        def __init__(self, **kwargs: Any) -> None:
            super().__init__([], [blocked_health()])
            self.kwargs = dict(kwargs)
            self.start_calls = 0
            created.append(self)

        def start(self) -> None:
            self.start_calls += 1

    monkeypatch.setattr(MAIN, "ScreenCapture", StartableCapture)
    plugin.tracker.update(MatchState(
        scene=SCENE_MATCH, in_match=True, my_hq=20, enemy_hq=20))
    plugin.dispatcher.submit(
        GameEvent(E_HQ_DAMAGE, 5, "stale",
                  details={"game": 1, "side": "mine", "loss": 5}),
        MatchState(), now=time.time() + 3600)
    plugin._previous_snapshot = object()
    plugin._last_trusted_snapshot = object()

    async def scenario() -> None:
        await plugin._start_pipeline()
        first = plugin._capture
        assert plugin._monitoring is True
        assert plugin._epoch == 1
        assert first is created[-1] and first.start_calls == 1
        # The producer thread is the sole capture owner; the frame-callback /
        # JPEG-ring recording chain no longer exists.
        assert first.kwargs["capture_thread_enabled"] is True
        assert "on_frame" not in first.kwargs
        assert "ring_enabled" not in first.kwargs
        assert first.kwargs["fps"] == 20.0
        assert plugin.dispatcher.pending == 0
        assert not hasattr(plugin, "_hud_cache")
        assert plugin.tracker.in_match is False
        assert plugin._previous_snapshot is None
        assert plugin._last_trusted_snapshot is None
        assert plugin._capture_pause_active is True, "no trusted frame yet"

        await plugin._stop_pipeline()
        assert plugin._capture is None
        assert first.stop_calls == 1
        assert plugin._epoch == 2

        await plugin._start_pipeline()
        second = plugin._capture
        assert second is not first
        assert second is created[-1] and second.start_calls == 1
        assert plugin._epoch == 3
        await plugin._stop_pipeline()
        assert second.stop_calls == 1

    asyncio.run(scenario())


def test_stop_never_stops_capture_installed_by_concurrent_start(
    plugin: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    plugin._monitoring = True
    plugin._cfg = {"tick_ms": 333}
    old_capture = StoppableCapture([], [blocked_health()])
    new_capture = StoppableCapture([], [blocked_health()])
    plugin._capture = old_capture

    async def concurrent_start(timeout: float = 5.0) -> bool:
        with plugin._lifecycle_lock:
            plugin._epoch += 1
            plugin._monitoring = True
            plugin._capture = new_capture
        return True

    monkeypatch.setattr(plugin, "_wait_for_ticks_idle", concurrent_start)

    asyncio.run(plugin._stop_pipeline())

    assert plugin._monitoring is True
    assert plugin._capture is new_capture
    assert new_capture.stop_calls == 0
    assert old_capture.stop_calls == 1


def test_repeated_untrusted_ticks_retry_expire_with_the_same_tick_token(
        plugin: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    plugin._monitoring = True
    plugin._cfg = {"tick_ms": 333, "chatter_interval_seconds": 0}
    plugin.screen_classifier = FakeClassifier(SCENE_MATCH, True)
    plugin.ui_detector = FakeUiDetector()
    plugin._capture = FakeCapture(
        [capture_result(1, (0, 0, 0)), None, None],
        [trusted_health(1), blocked_health(), blocked_health()],
    )
    expires: list[tuple[str, str | None]] = []
    original_expire = plugin.publisher.expire

    def spy_expire(*, reason: str = "unavailable",
                   context_token: str | None = None, now: float | None = None) -> bool:
        expires.append((reason, context_token))
        return original_expire(reason=reason, context_token=context_token, now=now)

    monkeypatch.setattr(plugin.publisher, "expire", spy_expire)

    plugin._perceive_once()  # trusted: publishes a read cursor
    plugin._perceive_once()  # first untrusted: purge + expire
    plugin._perceive_once()  # still untrusted: expire retry, no purge

    assert len(expires) == 2
    assert expires[0][1] and expires[0][1] == expires[1][1]
    assert expires[0][0] == expires[1][0]
    assert plugin._previous_snapshot is not None
    assert plugin._previous_snapshot.trusted is False
    assert plugin.tracker.in_match is True
    assert plugin.tracker.game_number == 1


def test_unknown_scene_purges_read_context_without_tracker_diff(
        plugin: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    class SequenceClassifier:
        def __init__(self, scenes: list[str]) -> None:
            self._scenes = list(scenes)
            self.calls: list[Any] = []
            self.scene = self._scenes[0]

        def classify(self, detections: Any) -> tuple[str, bool]:
            self.calls.append(detections)
            self.scene = self._scenes.pop(0)
            return self.scene, self.scene == SCENE_MATCH

    plugin._monitoring = True
    plugin._cfg = {"tick_ms": 333, "chatter_interval_seconds": 0}
    plugin.screen_classifier = SequenceClassifier(
        [SCENE_MATCH, MAIN.SCENE_UNKNOWN, SCENE_MATCH])
    plugin.ui_detector = FakeUiDetector()
    plugin._capture = FakeCapture(
        [capture_result(1, (0, 0, 0)), capture_result(2, (10, 10, 10)),
         capture_result(3, (20, 20, 20))],
        [trusted_health(1), trusted_health(2), trusted_health(3)],
    )
    purges: list[int] = []
    expires: list[str] = []
    original_purge = plugin._purge_dispatcher_queue
    original_expire = plugin._expire_read_context
    monkeypatch.setattr(plugin, "_purge_dispatcher_queue",
                        lambda: (purges.append(1), original_purge())[1])
    monkeypatch.setattr(plugin, "_expire_read_context",
                        lambda reason, token=None: (
                            expires.append(reason), original_expire(reason, token=token))[1])

    plugin._perceive_once()  # match
    plugin._perceive_once()  # unknown: no tracker diff, purge + expire
    assert purges and expires
    assert plugin.tracker.in_match is True
    assert plugin.tracker.game_number == 1
    assert plugin.tracker.current is not None
    assert plugin.tracker.current.scene == SCENE_MATCH
    assert plugin._last_scene == SCENE_MATCH


def test_confirmed_menu_exit_resets_match_but_keeps_game_number(
        plugin: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    class SequenceClassifier:
        def __init__(self, scenes: list[str]) -> None:
            self._scenes = list(scenes)
            self.scene = self._scenes[0]

        def classify(self, detections: Any) -> tuple[str, bool]:
            self.scene = self._scenes.pop(0)
            return self.scene, self.scene == SCENE_MATCH

    unit_resets: list[int] = []
    original_reset = plugin._unit_tracker.reset_match

    def reset_unit_match() -> None:
        unit_resets.append(1)
        original_reset()

    monkeypatch.setattr(plugin._unit_tracker, "reset_match", reset_unit_match)

    plugin._monitoring = True
    plugin._cfg = {"tick_ms": 333, "chatter_interval_seconds": 0}
    plugin.screen_classifier = SequenceClassifier(
        [SCENE_MATCH, SCENE_MENU, SCENE_MENU, SCENE_MENU])
    plugin.ui_detector = FakeUiDetector()
    plugin._capture = FakeCapture(
        [capture_result(i, (i, i, i)) for i in range(1, 5)],
        [trusted_health(i) for i in range(1, 5)],
    )

    plugin._perceive_once()
    assert plugin.tracker.in_match is True and plugin.tracker.game_number == 1
    plugin._perceive_once()  # menu pause
    plugin._perceive_once()  # menu pause
    assert plugin.tracker.in_match is True
    plugin._perceive_once()  # third menu frame confirms exit
    assert plugin.tracker.in_match is False
    assert plugin.tracker.game_number == 1
    assert plugin.scenario.snapshot()["confirmed_exit"] is True
    assert unit_resets, "UnitTracker must reset with MatchTracker"
    assert plugin._pipeline_state.units == []


def test_terminal_event_is_dispatched_before_current_match_memory_reset(
        plugin: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    class SceneSequence:
        def __init__(self, scenes: list[str]) -> None:
            self.scenes = list(scenes)

        def classify(self, detections: Any) -> tuple[str, bool]:
            scene = self.scenes.pop(0)
            return scene, scene == SCENE_MATCH

    unit_resets: list[int] = []
    original_reset = plugin._unit_tracker.reset_match
    monkeypatch.setattr(
        plugin._unit_tracker, "reset_match",
        lambda: (unit_resets.append(1), original_reset())[1])

    plugin._monitoring = True
    plugin._cfg = {"tick_ms": 333, "chatter_interval_seconds": 0,
                   "user_chat_quiet_window_seconds": 0.0,
                   "battle_output_quiet_window_seconds": 0.0,
                   "battle_output_backpressure_seconds": 0.0}
    plugin._apply_config({"kards": plugin._cfg})
    plugin.screen_classifier = SceneSequence([SCENE_MATCH, SCENE_VICTORY])
    plugin.ui_detector = FakeUiDetector()
    plugin._capture = FakeCapture(
        [capture_result(1, (0, 0, 0)), capture_result(2, (255, 255, 255))],
        [trusted_health(1), trusted_health(2)],
    )

    plugin._perceive_once()
    assert plugin.tracker.in_match is True
    plugin._perceive_once()

    # The confirmed terminal event is still dispatched, and mark_delivered is
    # safe after reset_match because the delivery ledger is session-scoped.
    responds = [p for p in plugin.pushes if p.get("ai_behavior") == "respond"]
    assert responds, "terminal event must reach the dispatcher"
    assert any(p["metadata"].get("kind") == E_MATCH_END for p in responds)
    assert plugin.tracker.in_match is False
    assert plugin.tracker.game_number == 1
    assert plugin.tracker.delivered_count >= 1
    assert plugin.scenario.snapshot()["confirmed_exit"] is True
    assert unit_resets, "terminal exit must reset UnitTracker with the match"
    assert plugin._pipeline_state.units == []


def test_start_and_stop_are_serialized_across_transition_guard(
        plugin: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    started = threading.Event()
    release = threading.Event()

    async def slow_start() -> None:
        started.set()
        await asyncio.to_thread(release.wait, 2.0)

    monkeypatch.setattr(plugin, "_start_pipeline_serialized", slow_start)

    async def scenario() -> None:
        start_task = asyncio.create_task(plugin._start_pipeline())
        assert await asyncio.to_thread(started.wait, 2.0)
        stop_task = asyncio.create_task(plugin._stop_pipeline(reason="test"))
        await asyncio.sleep(0.05)
        assert not stop_task.done(), "stop must wait for the active start transition"
        release.set()
        await start_task
        await stop_task
        assert plugin._monitoring is False

    asyncio.run(scenario())


def test_gate_reopen_failure_rolls_back_start_without_capture(
        plugin: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    created: list[Any] = []

    class StartableCapture(StoppableCapture):
        def __init__(self, **kwargs: Any) -> None:
            super().__init__([], [blocked_health()])
            created.append(self)

        def start(self) -> None:
            self.start_calls = getattr(self, "start_calls", 0) + 1

    monkeypatch.setattr(MAIN, "ScreenCapture", StartableCapture)
    plugin._ocr = object()
    plugin._detector = object()

    fail = {"on": True}
    original_reopen = plugin.gate.reopen

    def flaky_reopen() -> int:
        if fail["on"]:
            raise RuntimeError("gate down")
        return original_reopen()

    monkeypatch.setattr(plugin.gate, "reopen", flaky_reopen)

    failed = asyncio.run(plugin.monitor_start())
    assert failed.ok is False
    assert plugin._monitoring is False
    assert plugin._pipeline_started is False
    assert plugin._last_start_error == "gate_reopen"
    assert plugin._capture is None
    assert plugin._loop_task is None
    assert plugin._catalog_task is None
    assert created == [], "failed reopen must not create a capture/tasks"

    payload = asyncio.run(plugin.monitor_status()).data
    assert payload["monitoring"] is False
    assert payload["pipeline"]["started"] is False
    assert payload["pipeline"]["start_error"] == "gate_reopen"

    # The same generation can be retried after the gate recovers.
    fail["on"] = False
    retry = asyncio.run(plugin.monitor_start())
    assert retry.ok is True
    assert plugin._monitoring is True
    assert plugin._last_start_error == ""
    assert plugin._capture is created[-1]
    asyncio.run(plugin.monitor_stop())
