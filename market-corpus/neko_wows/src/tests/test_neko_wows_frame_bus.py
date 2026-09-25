"""The provider-frame bus is the only source of shared-screen truth."""

from __future__ import annotations

import base64
import threading
from types import SimpleNamespace

import pytest
from neko_wows.domain.contracts import LANE_NORMAL, WowsConfig
from neko_wows.policy.tactic_policy import AdviceCandidate
from neko_wows.presentation.instructions import (
    LIVE_VISION_SPEAK_HINT,
    VISION_LOOK_BEFORE_SPEAK,
)
from neko_wows.presentation.prompt_router import PromptProfile, WowsPromptRouter
from neko_wows.vision import live as live_module

pytestmark = [pytest.mark.unit, pytest.mark.plugin_unit]


class _ObservedCondition(threading.Condition):
    def __init__(self, lock, waiting: threading.Event) -> None:
        super().__init__(lock)
        self._waiting = waiting

    def wait(self, timeout=None):
        self._waiting.set()
        return super().wait(timeout)


def _frame(**overrides):
    payload = {
        "frame_id": "frame-7",
        "type": "provider_frame",
        "timestamp": 998.0,
        "captured_at": 998.0,
        "source": "screen",
        "image_base64": base64.b64encode(b"provider-jpeg").decode("ascii"),
        "mime": "image/jpeg",
        "turn_id": "turn-4",
        "generation": 7,
        "lanlan_name": "beta",
        "metadata": {},
    }
    payload.update(overrides)
    return SimpleNamespace(**payload)


def test_fresh_provider_screen_frame_is_active_for_matching_role():
    """Dropping provider records or role selection would disable reuse."""
    probe = live_module.ProviderFrameProbe(
        lambda: [_frame()],
        clock=lambda: 100.0,
        wall_clock=lambda: 1000.0,
        spawn=lambda fn: fn(),
    )

    state = probe.snapshot(role="beta")

    assert state == {
        "active": True,
        "source": "screen",
        "age_seconds": 2.0,
        "role": "beta",
        "frame_id": "frame-7",
        "generation": 7,
        "provider_delivered": True,
    }


@pytest.mark.parametrize(
    "record",
    [
        _frame(source="camera"),
        _frame(lanlan_name="alpha"),
        _frame(captured_at=994.9, timestamp=994.9),
    ],
    ids=["camera", "other-role", "stale"],
)
def test_non_screen_wrong_role_and_stale_records_are_not_reused(record):
    """Accepting any of these would show an unrelated or obsolete picture."""
    probe = live_module.ProviderFrameProbe(
        lambda: [record],
        wall_clock=lambda: 1000.0,
        spawn=lambda fn: fn(),
    )

    assert probe.snapshot(role="beta")["active"] is False


def test_newest_matching_screen_frame_wins_even_if_records_are_unsorted():
    """Taking list order would reuse an older frame after replay reordering."""
    probe = live_module.ProviderFrameProbe(
        lambda: [
            _frame(frame_id="new", captured_at=999.0, timestamp=999.0, generation=9),
            _frame(frame_id="old", captured_at=997.0, timestamp=997.0, generation=7),
        ],
        wall_clock=lambda: 1000.0,
        spawn=lambda fn: fn(),
    )

    state = probe.snapshot(role="beta")

    assert state["frame_id"] == "new"
    assert state["generation"] == 9
    assert state["age_seconds"] == 1.0


def test_a_future_timestamp_cannot_displace_the_newest_valid_frame():
    """A future-dated record must not stay fresh or win latest-frame selection."""
    probe = live_module.ProviderFrameProbe(
        lambda: [
            _frame(frame_id="future", captured_at=1010.0, timestamp=1010.0),
            _frame(frame_id="valid", captured_at=999.0, timestamp=999.0),
        ],
        wall_clock=lambda: 1000.0,
        spawn=lambda fn: fn(),
    )

    state = probe.snapshot(role="beta")

    assert state["frame_id"] == "valid"
    assert state["age_seconds"] == 1.0


def test_an_unresolved_target_does_not_borrow_another_roles_frame():
    """A blank delivery target is not permission to cross character sessions."""
    probe = live_module.ProviderFrameProbe(
        lambda: [_frame(lanlan_name="beta")],
        wall_clock=lambda: 1000.0,
        spawn=lambda fn: fn(),
    )

    assert probe.snapshot(role="")["active"] is False


def test_fetch_frame_decodes_the_same_fresh_record_selected_for_status():
    """Status and pixels must not come from different role/frame selections."""
    probe = live_module.ProviderFrameProbe(
        lambda: [_frame()],
        wall_clock=lambda: 1000.0,
        spawn=lambda fn: fn(),
    )

    assert probe.fetch_frame(role="beta") == b"provider-jpeg"
    assert probe.snapshot(role="beta")["frame_id"] == "frame-7"


def test_fetch_frame_refreshes_a_cold_cache_before_returning():
    """A tool request must not miss a reusable frame just because the cache is cold."""
    queued = []
    probe = live_module.ProviderFrameProbe(
        lambda: [_frame()],
        wall_clock=lambda: 1000.0,
        spawn=queued.append,
    )

    assert probe.fetch_frame(role="beta") == b"provider-jpeg"
    assert queued == []


def test_fetch_frame_refreshes_an_expired_cache_before_returning():
    """A tool request must replace an expired cached record before returning."""
    monotonic = [100.0]
    wall = [1000.0]
    queued = []
    replies = iter([
        [_frame(frame_id="old", image_base64=base64.b64encode(b"old").decode())],
        [
            _frame(
                frame_id="fresh",
                captured_at=1003.0,
                timestamp=1003.0,
                image_base64=base64.b64encode(b"fresh").decode(),
            )
        ],
    ])
    probe = live_module.ProviderFrameProbe(
        lambda: next(replies),
        clock=lambda: monotonic[0],
        wall_clock=lambda: wall[0],
        spawn=queued.append,
    )
    probe.snapshot(role="beta")
    queued.pop()()
    monotonic[0] += 3.0
    wall[0] += 3.0

    assert probe.fetch_frame(role="beta") == b"fresh"
    assert queued == []


def test_fetch_frame_waits_for_an_in_flight_async_refresh():
    provider_started = threading.Event()
    release_provider = threading.Event()
    fetch_waiting = threading.Event()
    calls = []

    def fetch():
        calls.append(None)
        provider_started.set()
        release_provider.wait(timeout=2.0)
        return [_frame()]

    workers = []

    def spawn(fn):
        worker = threading.Thread(target=fn)
        workers.append(worker)
        worker.start()

    probe = live_module.ProviderFrameProbe(
        fetch,
        wall_clock=lambda: 1000.0,
        spawn=spawn,
    )
    probe._condition = _ObservedCondition(probe._lock, fetch_waiting)
    assert probe.snapshot(role="beta")["active"] is False
    assert provider_started.wait(timeout=1.0)

    result = []
    tool_worker = threading.Thread(
        target=lambda: result.append(probe.fetch_frame(role="beta"))
    )
    tool_worker.start()
    assert fetch_waiting.wait(timeout=1.0)
    release_provider.set()
    tool_worker.join(timeout=1.0)
    workers[0].join(timeout=1.0)

    assert not tool_worker.is_alive()
    assert not workers[0].is_alive()
    assert result == [b"provider-jpeg"]
    assert len(calls) == 1


def test_simultaneous_cold_frame_fetches_share_one_provider_query():
    provider_started = threading.Event()
    release_provider = threading.Event()
    fetch_waiting = threading.Event()
    calls = []

    def fetch():
        calls.append(None)
        provider_started.set()
        release_provider.wait(timeout=2.0)
        return [_frame()]

    probe = live_module.ProviderFrameProbe(fetch, wall_clock=lambda: 1000.0)
    probe._condition = _ObservedCondition(probe._lock, fetch_waiting)
    results = []
    workers = [
        threading.Thread(
            target=lambda: results.append(probe.fetch_frame(role="beta"))
        )
        for _ in range(2)
    ]
    workers[0].start()
    assert provider_started.wait(timeout=1.0)
    workers[1].start()
    assert fetch_waiting.wait(timeout=1.0)
    release_provider.set()
    for worker in workers:
        worker.join(timeout=1.0)

    assert all(not worker.is_alive() for worker in workers)
    assert results == [b"provider-jpeg", b"provider-jpeg"]
    assert len(calls) == 1


def test_invalid_provider_pixels_fail_closed_without_hiding_frame_activity():
    """A malformed copy cannot be handed to the screenshot tool."""
    probe = live_module.ProviderFrameProbe(
        lambda: [_frame(image_base64="not base64!!")],
        wall_clock=lambda: 1000.0,
        spawn=lambda fn: fn(),
    )

    assert probe.fetch_frame(role="beta") is None
    assert probe.snapshot(role="beta")["active"] is True


def _candidate() -> AdviceCandidate:
    return AdviceCandidate(
        event_id="low_health",
        lane=LANE_NORMAL,
        priority=50,
        severity=40,
        at=100.0,
        seq=7,
        battle_id="battle-1",
        summary="血量偏低",
        detail={"own_hp_ratio": 0.3},
        context={"visible_enemies": 4},
    )


def _request(*, screenshot_enabled: bool, live_vision_active: bool):
    return WowsPromptRouter(WowsConfig()).build(
        _candidate(),
        PromptProfile(
            channel_mode="dual",
            dry_run=True,
            screenshot_enabled=screenshot_enabled,
            live_vision_active=live_vision_active,
        ),
    )


def test_fresh_bus_frame_changes_wording_without_requesting_host_attachment():
    """The final host contract has no attach request or permission tokens."""
    request = _request(screenshot_enabled=True, live_vision_active=True)

    assert LIVE_VISION_SPEAK_HINT.strip() in request.text
    assert VISION_LOOK_BEFORE_SPEAK.strip() not in request.text
    assert "attach_live_frame" not in request.metadata
    assert "live_frame_permission_token" not in request.metadata
    assert "plugin_delivery_token" not in request.metadata


def test_enabled_but_stale_bus_does_not_pretend_a_frame_was_delivered():
    """A local switch cannot override the provider-delivery evidence gate."""
    request = _request(screenshot_enabled=True, live_vision_active=False)

    assert VISION_LOOK_BEFORE_SPEAK.strip() in request.text
    assert LIVE_VISION_SPEAK_HINT.strip() not in request.text


def test_plugin_reads_screen_records_from_the_sdk_bus():
    """Calling the retired live-vision RPC instead would make reuse inert."""
    from neko_wows import NekoWowsPlugin

    calls = []

    class Frames:
        def get(self, **kwargs):
            calls.append(kwargs)
            return [_frame()]

    plugin = object.__new__(NekoWowsPlugin)
    plugin.ctx = SimpleNamespace(bus=SimpleNamespace(frames=Frames()))

    assert plugin._get_provider_frames() == [_frame()]
    assert calls == [{
        "max_count": 8,
        "filter": {"source": "screen"},
        "timeout": 1.0,
    }]


def test_plugin_activity_no_longer_depends_on_a_permission_generation():
    """A fresh bus record alone is sufficient under the final host contract."""
    from neko_wows import NekoWowsPlugin

    class Probe:
        @staticmethod
        def is_active(*, role):
            return role == "beta"

    plugin = object.__new__(NekoWowsPlugin)
    plugin.cfg = WowsConfig(live_vision_enabled=True, target_lanlan="beta")
    plugin.live_vision = Probe()

    assert plugin._live_vision_active() is True
