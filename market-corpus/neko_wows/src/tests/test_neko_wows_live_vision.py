"""Provider-frame caching, screenshot reuse, and plugin wiring."""

from __future__ import annotations

import base64
import math

import pytest
from neko_wows.domain.contracts import WowsConfig
from neko_wows.vision import tool as tool_module
from neko_wows.vision.live import ProviderFrameProbe
from neko_wows.vision.store import ShotStore
from neko_wows.vision.tool import (
    REASON_CAPTURE_FAILED,
    SOURCE_FULLSCREEN,
    SOURCE_LIVE_SHARE,
    ScreenshotService,
)

pytestmark = [pytest.mark.unit, pytest.mark.plugin_unit]


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _frame(**overrides):
    frame = {
        "frame_id": "frame-1",
        "source": "screen",
        "captured_at": 1000.0,
        "lanlan_name": "lanlan",
        "generation": 1,
        "image_base64": base64.b64encode(b"shared-frame").decode("ascii"),
    }
    frame.update(overrides)
    return frame


def _probe(replies, *, clock=None, spawn=lambda fn: fn(), ttl=2.0):
    calls = []
    probe_clock = clock or _Clock()

    def fetch():
        calls.append(None)
        reply = replies[min(len(calls) - 1, len(replies) - 1)]
        if isinstance(reply, Exception):
            raise reply
        return reply

    return ProviderFrameProbe(
        fetch,
        ttl=ttl,
        clock=probe_clock,
        wall_clock=probe_clock,
        spawn=spawn,
    ), calls


def test_first_read_is_nonblocking_and_learns_after_the_worker_runs():
    spawned = []
    probe, calls = _probe([[_frame()]], spawn=spawned.append)

    assert probe.snapshot(role="lanlan")["active"] is False
    assert calls == []

    spawned.pop()()
    assert probe.snapshot(role="lanlan")["active"] is True
    assert len(calls) == 1


def test_cached_state_is_reused_inside_the_two_second_ttl():
    clock = _Clock()
    probe, calls = _probe([[_frame()]], clock=clock)

    probe.snapshot(role="lanlan")
    clock.advance(1.0)

    assert probe.snapshot(role="lanlan")["active"] is True
    assert len(calls) == 1


def test_expired_cache_is_inactive_until_one_refresh_finishes():
    clock = _Clock()
    spawned = []
    probe, calls = _probe(
        [[_frame()], [_frame(captured_at=1003.0)]],
        clock=clock,
        spawn=spawned.append,
    )

    probe.snapshot(role="lanlan")
    spawned.pop()()
    assert probe.snapshot(role="lanlan")["active"] is True

    clock.advance(3.0)
    for _ in range(5):
        assert probe.snapshot(role="lanlan")["active"] is False

    assert len(spawned) == 1
    spawned.pop()()
    assert probe.snapshot(role="lanlan")["active"] is True
    assert len(calls) == 2


def test_failed_query_is_cached_as_inactive_until_the_next_poll_window():
    clock = _Clock()
    probe, calls = _probe([RuntimeError("bus unavailable")], clock=clock)

    assert probe.snapshot(role="lanlan")["active"] is False
    clock.advance(1.0)
    assert probe.snapshot(role="lanlan")["active"] is False
    assert len(calls) == 1


def test_a_frame_expires_after_five_seconds_even_when_the_query_cache_has_not():
    clock = _Clock()
    probe, calls = _probe([[_frame()]], clock=clock, ttl=10.0)

    assert probe.snapshot(role="lanlan")["active"] is True
    clock.advance(5.1)

    assert probe.snapshot(role="lanlan")["active"] is False
    assert len(calls) == 1


@pytest.mark.parametrize("captured_at", [math.nan, math.inf, -math.inf])
def test_non_finite_frame_timestamps_fail_closed(captured_at):
    probe, _calls = _probe([[_frame(captured_at=captured_at)]])

    assert probe.snapshot(role="lanlan")["active"] is False


def _service(tmp_path, *, live_frame_provider, monkeypatch, capture=b"captured"):
    monkeypatch.setattr(tool_module, "find_game_window", lambda _dir: None)
    monkeypatch.setattr(tool_module, "capture_jpeg", lambda _window: capture)
    cfg = WowsConfig(screenshot_enabled=True, screenshot_min_interval_seconds=0.0)
    store = ShotStore(tmp_path / "shots", cfg.screenshot_retain_count)
    service = ScreenshotService(
        cfg,
        store,
        lambda: {"in_battle": True},
        live_frame_provider=live_frame_provider,
    )
    return service, store


def test_screenshot_tool_reuses_provider_pixels_without_capturing(tmp_path, monkeypatch):
    def capture(_window):
        raise AssertionError("screen was captured despite a reusable provider frame")

    service, store = _service(
        tmp_path,
        live_frame_provider=lambda: b"shared-frame",
        monkeypatch=monkeypatch,
    )
    monkeypatch.setattr(tool_module, "capture_jpeg", capture)

    result = service.look()

    assert result["output"]["source"] == SOURCE_LIVE_SHARE
    assert base64.b64decode(result["images"][0]["data_b64"]) == b"shared-frame"
    assert store.load(result["output"]["shot_id"]) == b"shared-frame"


@pytest.mark.parametrize(
    "provider",
    [
        lambda: None,
        lambda: (_ for _ in ()).throw(RuntimeError("bus unavailable")),
    ],
    ids=["no-frame", "provider-raised"],
)
def test_screenshot_tool_falls_back_to_capture(tmp_path, monkeypatch, provider):
    service, store = _service(
        tmp_path, live_frame_provider=provider, monkeypatch=monkeypatch)

    result = service.look()

    assert result["output"]["source"] == SOURCE_FULLSCREEN
    assert store.load(result["output"]["shot_id"]) == b"captured"


def test_capture_failure_is_reported_when_no_provider_pixels_exist(tmp_path, monkeypatch):
    service, _store = _service(
        tmp_path,
        live_frame_provider=lambda: None,
        monkeypatch=monkeypatch,
        capture=None,
    )

    assert service.look()["output"]["reason"] == REASON_CAPTURE_FAILED


class _RoleRecordingProbe:
    def __init__(self) -> None:
        self.calls = []

    def is_active(self, *, role):
        self.calls.append(("active", role))
        return True

    def fetch_frame(self, *, role):
        self.calls.append(("frame", role))
        return b"frame"

    def status(self, *, role):
        self.calls.append(("status", role))
        return {
            "active": True,
            "usable": True,
            "polled": True,
            "source": "screen",
            "age_seconds": 0.5,
            "role": role,
            "frame_id": "frame-1",
            "generation": 1,
            "provider_delivered": True,
        }


def _plugin_with(cfg, probe):
    from neko_wows import NekoWowsPlugin

    plugin = object.__new__(NekoWowsPlugin)
    plugin.cfg = cfg
    plugin.live_vision = probe
    return plugin


def test_plugin_uses_the_delivery_target_role_for_status_and_pixels():
    probe = _RoleRecordingProbe()
    plugin = _plugin_with(
        WowsConfig(live_vision_enabled=True, target_lanlan="beta"), probe)

    assert plugin._live_vision_active() is True
    assert plugin._live_frame() == b"frame"
    assert plugin._live_vision_payload(plugin.cfg)["in_use"] is True
    assert probe.calls == [
        ("active", "beta"),
        ("frame", "beta"),
        ("status", "beta"),
    ]


def test_disabled_reuse_short_circuits_the_probe():
    probe = _RoleRecordingProbe()
    plugin = _plugin_with(WowsConfig(live_vision_enabled=False), probe)

    assert plugin._live_vision_active() is False
    assert plugin._live_frame() is None
    assert plugin._live_vision_payload(plugin.cfg)["in_use"] is False
    assert probe.calls == []
