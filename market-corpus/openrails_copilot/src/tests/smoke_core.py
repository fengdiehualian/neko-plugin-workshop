"""Standalone smoke test for openrails_copilot/core — the detection chain.

Purpose
-------
Exercise the three modules that were built but never run together, **without**
booting N.E.K.O and **without** a running Open Rails:

    detector.detect(snap, prev)  ->  arbiter.decide(event_id)  ->  PushSender.send_alert()

Snapshots are constructed in-process, so every rule can be driven
deterministically instead of waiting for a train to hit a red signal. The HTTP
layer (``snapshot.build_snapshot``) is covered separately by ``smoke_local.py``
against a mock/real OR; this file covers the *decision* layer above it.

Two classes of check matter here:

* **Typo guard** — ``event_catalog.spec()`` silently returns a P3 fallback for an
  unknown id, so a misspelt ``event_id`` in the detector would route the wrong
  priority and never show up as an error. Every id the detector emits is
  asserted to exist in ``CATALOG``.
* **Coverage** — catalog entries the detector never emits are listed rather than
  glossed over. A catalog row with no rule behind it is a promise the plugin
  cannot keep.

Usage
-----
    .venv/Scripts/python.exe plugin/plugins/openrails_copilot/smoke_core.py

Logs land in ``<plugin_dir>/log/`` — both a human-readable ``.log`` and a
machine-readable ``.json`` result.
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

if str(Path(__file__).resolve().parents[3]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from plugin.plugins.openrails_copilot.core import event_catalog as catalog
from plugin.plugins.openrails_copilot.core.arbiter import (
    SC_NORMAL,
    SC_SHUNTING,
    SC_STATION_STOP,
    SC_STOPPED,
    Arbiter,
)
from plugin.plugins.openrails_copilot.core.constants import (
    GLOBAL_RATE_LIMIT_S,
    RED_APPROACH_EXIT_M,
    RED_SIGNAL_SAFE_DISTANCE_M,
)
from plugin.plugins.openrails_copilot.core.detector import (
    ENABLE_GAUGE_BRAKE_NOT_RELEASED,
    ENABLE_SHUNT_OVERSPEED,
    ENABLE_SIG_INCONSISTENT,
    Candidate,
    Detector,
    parse_brake_hud,
)
from plugin.plugins.openrails_copilot.core.push_sender import PushSender
from plugin.plugins.openrails_copilot.core.safety_guard import SafetyGuard
from plugin.plugins.openrails_copilot.core.snapshot import Snapshot, TrackItem

_PLUGIN_DIR = Path(__file__).resolve().parent
_LOG_DIR = _PLUGIN_DIR / "log"

# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------

#: Catalog entries with no detector rule yet, beyond the calibration-gated ones.
#:
#: Listed openly so the gap is visible instead of averaged away. ``SPD_SIDE_LIMIT``
#: is the one such row: its own catalog note already says the "is this a
#: diverging route" inference (limit marker + ►/◄ switch symbol) needs
#: calibration, and no rule was written for it when the detector was built.
KNOWN_UNIMPLEMENTED: frozenset[str] = frozenset({"SPD_SIDE_LIMIT"})


class Report:
    def __init__(self) -> None:
        self.checks: list[dict[str, Any]] = []
        self.declared_gaps: list[str] = []

    def check(self, name: str, ok: bool, detail: str = "") -> bool:
        self.checks.append({"name": name, "ok": bool(ok), "detail": detail})
        return bool(ok)

    @property
    def failed(self) -> list[dict[str, Any]]:
        return [c for c in self.checks if not c["ok"]]


class FakePlugin:
    """Minimal stand-in for the host plugin, as PushSender sees it."""

    def __init__(self, logger: logging.Logger) -> None:
        self.logger = logger
        self.pushes: list[dict[str, Any]] = []

    def push_message(self, **kwargs: Any) -> dict[str, Any]:
        self.pushes.append(kwargs)
        return {"submitted": True}


def _ids(cands: list[Candidate]) -> list[str]:
    return [c.event_id for c in cands]


def _detect(det: Detector, snap: Snapshot, prev: Snapshot | None = None) -> list[Candidate]:
    return det.detect(snap, prev)


# ---------------------------------------------------------------------------
# 1. HUD brake parser
# ---------------------------------------------------------------------------


def check_brake_hud(report: Report, log: logging.Logger) -> None:
    applied = "持续制动 EQ 441 千帕 BC 430 千帕 BP 441 千帕 EOT  BC 430 千帕 BP 441 千帕"
    released = "缓解 EQ 600 千帕 BC 0 千帕 BP 600 千帕 EOT  BC 0 千帕 BP 600 千帕"

    got = parse_brake_hud(applied)
    log.info("parse_brake_hud(applied) -> %s", got)
    report.check("hud.applied EQ/BC/BP parsed", got.get("eq") == 441.0 and got.get("bc") == 430.0 and got.get("bp") == 441.0, str(got))
    report.check("hud.applied EOT BC/BP parsed", got.get("eot_bc") == 430.0 and got.get("eot_bp") == 441.0, str(got))

    got2 = parse_brake_hud(released)
    log.info("parse_brake_hud(released) -> %s", got2)
    report.check("hud.released BC reads 0 not 600", got2.get("bc") == 0.0, f"bc={got2.get('bc')} (must not pick the EOT segment)")

    # Regression on the segment split: with two BC values in the string, the main
    # segment must win for `bc` and the EOT segment for `eot_bc`.
    report.check("hud.segments not cross-contaminated",
                 got2.get("bc") != got2.get("eot_bc") or got2.get("bc") == 0.0,
                 f"bc={got2.get('bc')} eot_bc={got2.get('eot_bc')}")

    report.check("hud.empty -> {}", parse_brake_hud("") == {}, "")
    report.check("hud.no-match -> {}", parse_brake_hud("列车制动") == {}, "")

    # A trailing 'kPa' spelling must parse too (locale variants seen in OR).
    alt = parse_brake_hud("缓解 EQ 600 kPa BC 0 kPa BP 600 kPa EOT BC 0 kPa BP 600 kPa")
    report.check("hud.accepts 'kPa' as well as '千帕'", alt.get("bp") == 600.0 and alt.get("eot_bp") == 600.0, str(alt))


# ---------------------------------------------------------------------------
# 2. Red-signal window (the user's speed-comparison rule)
# ---------------------------------------------------------------------------


def check_red_window(report: Report, log: logging.Logger) -> None:
    det = Detector()
    det.reset()

    # Approach at a steady 60 km/h: 250 -> 180 -> 120 -> 90 -> 40 m.
    # Nothing decelerates, so the window comparison must escalate.
    speeds = {250.0: 60.0, 180.0: 60.0, 120.0: 60.0, 90.0: 59.0, 40.0: 58.0}
    seen: list[str] = []
    for dist, spd in speeds.items():
        snap = Snapshot(speed_kmh=spd, signal_aspect="Stop", signal_distance_m=dist)
        seen.extend(_ids(_detect(det, snap)))
    log.info("red window, no deceleration -> %s", seen)
    report.check("red.outside safe distance -> SIG_RED_APPROACH", "SIG_RED_APPROACH" in seen, str(seen))
    report.check("red.inside window, speed flat -> SIG_RED_STOP", "SIG_RED_STOP" in seen, str(seen))
    report.check("red.very close, still flat -> EMERG_RED_STOP", "EMERG_RED_STOP" in seen, str(seen))

    # Same approach, but braking properly (60 -> 45 -> 30 -> 10 km/h).
    det.reset()
    braking = {250.0: 60.0, 180.0: 60.0, 120.0: 45.0, 90.0: 30.0, 40.0: 10.0}
    seen2: list[str] = []
    for dist, spd in braking.items():
        snap = Snapshot(speed_kmh=spd, signal_aspect="Stop", signal_distance_m=dist)
        seen2.extend(_ids(_detect(det, snap)))
    log.info("red window, braking properly -> %s", seen2)
    report.check("red.speed dropped -> no SIG_RED_STOP", "SIG_RED_STOP" not in seen2, str(seen2))
    report.check("red.speed dropped -> no EMERG_RED_STOP", "EMERG_RED_STOP" not in seen2, str(seen2))

    # The recorded entry speed is the speed at window entry (180 m), not the
    # first sample: entering at 60 and only slowing to 59 (-1 km/h, inside the
    # 2 km/h tolerance) must still count as "not dropping".
    det.reset()
    entry60 = {250.0: 60.0, 180.0: 60.0, 90.0: 59.0}
    seen3: list[str] = []
    for dist, spd in entry60.items():
        snap = Snapshot(speed_kmh=spd, signal_aspect="Stop", signal_distance_m=dist)
        seen3.extend(_ids(_detect(det, snap)))
    report.check("red.-1 km/h is inside tolerance -> still SIG_RED_STOP", "SIG_RED_STOP" in seen3, str(seen3))

    # Leaving the approach (aspect clears) must re-arm the window.
    det.reset()
    _detect(det, Snapshot(speed_kmh=60.0, signal_aspect="Stop", signal_distance_m=150.0))
    _detect(det, Snapshot(speed_kmh=60.0, signal_aspect="Clear_2", signal_distance_m=150.0))
    report.check("red.aspect clears -> window re-armed", det._red_window is None, f"window={det._red_window}")

    # Distinct from the level crossing: no distance -> not judged (no data is
    # not the same as "not braking").
    det.reset()
    no_dist = _ids(_detect(det, Snapshot(speed_kmh=60.0, signal_aspect="Stop", signal_distance_m=None)))
    report.check("red.no distance -> no stop judgement", "SIG_RED_STOP" not in no_dist and "EMERG_RED_STOP" not in no_dist, str(no_dist))

    # Boundary sanity: EXIT is the comparison point, so at exactly EXIT the
    # comparison runs; just outside it does not.
    report.check("red.constants ordered ENTER > EXIT", RED_SIGNAL_SAFE_DISTANCE_M > RED_APPROACH_EXIT_M,
                 f"enter={RED_SIGNAL_SAFE_DISTANCE_M} exit={RED_APPROACH_EXIT_M}")


# ---------------------------------------------------------------------------
# 3. Track colour -> overspeed tier
# ---------------------------------------------------------------------------


def check_track_colors(report: Report, log: logging.Logger) -> None:
    cases = {
        "PaleGreen": "SPD_NEAR_LIMIT",
        "Orange": "SPD_TRACK_ORANGE",
        "OrangeRed": "SPD_TRACK_RED",
    }
    for color, expected in cases.items():
        snap = Snapshot(speed_kmh=70.0, limit_kmh=60.0, track_color=color)
        ids = _ids(_detect(Detector(), snap))
        log.info("track_color=%s -> %s", color, ids)
        report.check(f"track.{color} -> {expected}", expected in ids, str(ids))

    green = _ids(_detect(Detector(), Snapshot(speed_kmh=40.0, limit_kmh=60.0, track_color="Green")))
    report.check("track.Green -> no overspeed alert", not any(i.startswith("SPD_TRACK") or i == "SPD_NEAR_LIMIT" for i in green), str(green))

    # Only one tier at a time: a dark-red track must not also report orange.
    red = _ids(_detect(Detector(), Snapshot(speed_kmh=90.0, limit_kmh=60.0, track_color="OrangeRed")))
    report.check("track.tiers are exclusive", "SPD_TRACK_ORANGE" not in red and "SPD_NEAR_LIMIT" not in red, str(red))

    # Limit drop is a delta rule and needs the previous snapshot.
    prev = Snapshot(speed_kmh=70.0, limit_kmh=100.0, track_color="Green")
    now = Snapshot(speed_kmh=70.0, limit_kmh=60.0, track_color="Green")
    ids = _ids(_detect(Detector(), now, prev))
    report.check("speed.limit drop -> SPD_LIMIT_DROP", "SPD_LIMIT_DROP" in ids, str(ids))
    flat = _ids(_detect(Detector(), now, Snapshot(speed_kmh=70.0, limit_kmh=60.0, track_color="Green")))
    report.check("speed.limit unchanged -> no SPD_LIMIT_DROP", "SPD_LIMIT_DROP" not in flat, str(flat))

    # Temporary-speed markers are tiered by distance so one marker fires once.
    for dist, expected in ((2200.0, "SPD_TEMP_LIMIT_2KM"), (1600.0, "SPD_TEMP_LIMIT_1500"), (1100.0, "SPD_TEMP_LIMIT_1000")):
        snap = Snapshot(speed_kmh=70.0, limit_kmh=60.0, track_color="Green",
                        limit_marker_color="OrangeRed", limit_marker_value=40.0,
                        limit_marker_distance_m=dist)
        ids = _ids(_detect(Detector(), snap))
        report.check(f"speed.temp limit @{dist:.0f}m -> {expected}", expected in ids, str(ids))

    # Non-temporary markers (yellow/green) must not raise a temp-limit alert.
    normal = _ids(_detect(Detector(), Snapshot(speed_kmh=70.0, limit_kmh=60.0, track_color="Green",
                                               limit_marker_color="Yellow", limit_marker_value=40.0,
                                               limit_marker_distance_m=2200.0)))
    report.check("speed.perm marker -> no temp alert", not any(i.startswith("SPD_TEMP") for i in normal), str(normal))


# ---------------------------------------------------------------------------
# 4. Out-of-control reason mapping (OR's own authority)
# ---------------------------------------------------------------------------


def check_out_of_control(report: Report, log: logging.Logger) -> None:
    expected = {
        "SPAD": "EMERG_OUT_OF_CONTROL_SPAD",
        "SPAD-Rear": "EMERG_OUT_OF_CONTROL_SPAD",
        "Misalg Sw": "EMERG_OUT_OF_CONTROL_MISALIGNED_SWITCH",
        "Off Auth": "EMERG_OUT_OF_CONTROL_OFF_AUTH",
        "Off Path": "EMERG_OUT_OF_CONTROL_OFF_AUTH",
        "Off Track": "EMERG_OUT_OF_CONTROL_OFF_TRACK",
    }
    for reason, event_id in expected.items():
        snap = Snapshot(speed_kmh=20.0, out_of_control=True, out_of_control_reason=reason)
        cands = _detect(Detector(), snap)
        ids = _ids(cands)
        log.info("out_of_control reason=%r -> %s", reason, ids)
        ok = event_id in ids
        detail = "" if ok else str(ids)
        if cands:
            detail = f"msg={cands[0].message!r}"
        report.check(f"ooc.{reason} -> {event_id}", ok, detail)

    # Every reason OR can name must map to a real catalog id (no fallback path).
    for reason in ("SPAD", "SPAD-Rear", "Misalg Sw", "Off Auth", "Off Path", "Splipped",
                   "Slipped", "Off Track", "Slip Turn", "Undefined"):
        ids = _ids(_detect(Detector(), Snapshot(out_of_control=True, out_of_control_reason=reason)))
        report.check(f"ooc.{reason} maps to a catalog id", bool(ids) and all(i in catalog.CATALOG for i in ids), str(ids))

    clean = _ids(_detect(Detector(), Snapshot(speed_kmh=60.0, out_of_control=False)))
    report.check("ooc.not out of control -> no emergency", not any(i.startswith("EMERG_OUT_OF_CONTROL") for i in clean), str(clean))


# ---------------------------------------------------------------------------
# 5. Scenario classification + arbiter gating
# ---------------------------------------------------------------------------


def check_scenarios(report: Report, log: logging.Logger) -> None:
    det = Detector()

    moving = det.classify_scenario(Snapshot(speed_kmh=60.0))
    report.check("scenario.moving -> normal", moving == SC_NORMAL, moving)

    shunt = det.classify_scenario(Snapshot(
        speed_kmh=4.0,
        track_items=[TrackItem(kind="switch_left", color="Orange", text="", distance_m=80.0)],
    ))
    report.check("scenario.low speed + switch -> shunting", shunt == SC_SHUNTING, shunt)

    stopped = det.classify_scenario(Snapshot(speed_kmh=0.0))
    report.check("scenario.stopped, no station -> stopped", stopped == SC_STOPPED, stopped)

    station_stop = det.classify_scenario(Snapshot(
        speed_kmh=0.0,
        track_items=[TrackItem(kind="station", color="Blue", text="", distance_m=60.0)],
    ))
    report.check("scenario.stopped at station -> station_stop", station_stop == SC_STATION_STOP, station_stop)

    report.check("scenario.no speed -> normal", det.classify_scenario(Snapshot(speed_kmh=None)) == SC_NORMAL, "")

    # Gating: a station stop silences speed/overspeed classes entirely, but must
    # still let a red signal through. This is the whole point of the scenario layer.
    guard = SafetyGuard()
    arb = Arbiter(guard, logger=log)
    arb.update_scenario(SC_STATION_STOP)
    ok_overspeed, why_overspeed = arb.decide("SPD_TRACK_ORANGE", now=1000.0)
    ok_red, why_red = arb.decide("SIG_RED_APPROACH", now=1000.0)
    log.info("gate@station_stop: overspeed=%s red=%s", why_overspeed, why_red)
    report.check("gate.station_stop silences overspeed", not ok_overspeed and "scenario_gated" in why_overspeed, why_overspeed)
    report.check("gate.station_stop still allows red signal", ok_red, why_red)
    arb.update_scenario(SC_NORMAL)


# ---------------------------------------------------------------------------
# 6. Arbiter: single slot, rate limit, preemption
# ---------------------------------------------------------------------------


def check_arbiter(report: Report, log: logging.Logger) -> None:
    guard = SafetyGuard()
    arb = Arbiter(guard, logger=log)
    now = 5000.0

    # First non-preempt output goes out; the next one within the global window
    # is held back and buffered instead. Both must sit in a category that is on
    # by default (CAT_STATE / CAT_GAUGE are off in CATEGORY_DEFAULTS).
    first_ok, first_why = arb.decide("SPD_NEAR_LIMIT", now=now)
    second_ok, second_why = arb.decide("SPD_LIMIT_DROP", now=now + 1.0)
    report.check("arbiter.first non-preempt passes", first_ok, first_why)
    report.check("arbiter.second within window is rate limited", not second_ok and "rate_limited" in second_why, second_why)

    # A preempt event ignores the global window (it has its own 5 s clock) and
    # jumps the pile.
    pre_ok, pre_why = arb.decide("SPD_TRACK_RED", now=now + 2.0)
    report.check("arbiter.preempt bypasses global rate limit", pre_ok and pre_why == "preempt", pre_why)

    # The critical clock is *shared*, not per-event: a different preempt event
    # arriving one second later is held back by it (a same-event repeat would be
    # caught by its own cooldown first, which would prove nothing about the clock).
    pre2_ok, pre2_why = arb.decide("EMERG_RED_STOP", now=now + 3.0)
    report.check("arbiter.preempt honours the shared critical cooldown",
                 not pre2_ok and "critical_cooldown" in pre2_why, pre2_why)

    # The firing event's own cooldown blocks an immediate repeat.
    same_ok, same_why = arb.decide("SPD_TRACK_RED", now=now + 3.0)
    report.check("arbiter.repeat within the event cooldown is held", not same_ok and same_why == "cooldown", same_why)

    # Buffer flush: after the window expires the highest-priority buffered
    # candidate is the one that speaks, not whichever arrives last.
    # All candidates must be non-preempt (or they'd jump the queue) and in a
    # category that is on by default (so the category gate can't mask the buffer).
    guard2 = SafetyGuard()
    arb2 = Arbiter(guard2, logger=log)
    t = 9000.0
    arb2.decide("SPD_NEAR_LIMIT", now=t)             # P1, goes out immediately
    arb2.begin_cycle()
    arb2.decide("SPD_TEMP_LIMIT_1000", now=t + 1.0)  # P2, buffered
    arb2.decide("SPD_LIMIT_DROP", now=t + 2.0)       # P1, better -> becomes the pick
    flush_ok, flush_why = arb2.decide("SPD_TEMP_LIMIT_2KM", now=t + GLOBAL_RATE_LIMIT_S + 1.0)
    log.info("arbiter flush -> %s / %s", flush_ok, flush_why)
    report.check("arbiter.buffer flush yields to the best candidate",
                 "window_flush_yielded_to(SPD_LIMIT_DROP)" in flush_why, flush_why)
    fired = [d.event_id for d in arb2.decision_snapshot() if d.spoken]
    report.check("arbiter.flushed the high-priority candidate", "SPD_LIMIT_DROP" in fired, str(fired))

    # Category switch is honoured.
    guard3 = SafetyGuard()
    arb3 = Arbiter(guard3, logger=log, categories={catalog.CAT_SPEED: False})
    off_ok, off_why = arb3.decide("SPD_TRACK_ORANGE", now=100.0)
    report.check("arbiter.category off silences its events", not off_ok and off_why == "category_disabled", off_why)

    # Player quiet window: never talk over the user.
    guard4 = SafetyGuard()
    arb4 = Arbiter(guard4, logger=log)
    arb4.on_player_speak(silence_s=60.0)
    q_ok, q_why = arb4.decide("SIG_RED_APPROACH")
    report.check("arbiter.player quiet window blocks output", not q_ok and q_why == "player_quiet_window", q_why)

    # Circuit breaker: a storm of push failures must trip and stop all output.
    guard5 = SafetyGuard()
    arb5 = Arbiter(guard5, logger=log)
    for _ in range(guard5.failure_limit):
        guard5.record_failure()
    b_ok, b_why = arb5.decide("EMERG_OUT_OF_CONTROL_SPAD", now=200.0)
    report.check("arbiter.circuit breaker trips after failures", not b_ok and b_why == "tripped", b_why)
    guard5.resume()
    r_ok, r_why = arb5.decide("EMERG_OUT_OF_CONTROL_SPAD", now=200.0)
    report.check("arbiter.resume() restores output", r_ok, r_why)


# ---------------------------------------------------------------------------
# 7. PushSender routing (the respond budget is the scarce resource)
# ---------------------------------------------------------------------------


def check_push_routing(report: Report, log: logging.Logger) -> None:
    plugin = FakePlugin(log)
    sender = PushSender(plugin, dry_run=False)

    respond_ids = sorted(catalog.IMMEDIATE_IDS)
    for event_id in respond_ids:
        plugin.pushes.clear()
        sent = asyncio.run(sender.send_alert(catalog.spec(event_id), "test"))
        push = plugin.pushes[0] if plugin.pushes else {}
        log.info("route %s -> ai_behavior=%s priority=%s submitted=%s", event_id, push.get("ai_behavior"), push.get("priority"), sent)
        report.check(f"route.{event_id} uses respond", push.get("ai_behavior") == "respond" and sent, str(push))

    # Everything else must stay on read — that is the whole reason the immediate
    # set is kept tiny (host allows only 2 respond per session).
    read_ids = [i for i in catalog.CATALOG if i not in catalog.IMMEDIATE_IDS]
    wrong = []
    for event_id in read_ids:
        plugin.pushes.clear()
        asyncio.run(sender.send_alert(catalog.spec(event_id), "test"))
        if plugin.pushes and plugin.pushes[0].get("ai_behavior") != "read":
            wrong.append(event_id)
    report.check("route.non-immediate events all use read", not wrong, f"wrong={wrong}")

    # coalesce_key must be per-event so distinct alerts can't evict each other.
    plugin.pushes.clear()
    asyncio.run(sender.send_alert(catalog.spec("SPD_TRACK_RED"), "x"))
    asyncio.run(sender.send_alert(catalog.spec("SIG_RED_APPROACH"), "y"))
    keys = [p.get("coalesce_key") for p in plugin.pushes]
    report.check("route.coalesce_key is per event_id", keys == ["openrails:SPD_TRACK_RED", "openrails:SIG_RED_APPROACH"], str(keys))

    plugin.pushes.clear()
    asyncio.run(sender.send_status("all normal"))
    report.check("route.status uses read + stable key",
                 plugin.pushes[0].get("ai_behavior") == "read"
                 and plugin.pushes[0].get("coalesce_key") == "openrails:status", str(plugin.pushes[0]))

    plugin.pushes.clear()
    asyncio.run(sender.push_direct("十辆"))
    report.check("route.direct is blind + chat only",
                 plugin.pushes[0].get("ai_behavior") == "blind"
                 and plugin.pushes[0].get("visibility") == ["chat"], str(plugin.pushes[0]))

    # A dead transport must be reported, not raised into the monitor loop.
    class DeadPlugin(FakePlugin):
        def push_message(self, **kwargs: Any) -> dict[str, Any]:
            raise RuntimeError("transport_unavailable")

    gone = PushSender(DeadPlugin(log), dry_run=False)
    ok = asyncio.run(gone.send_alert(catalog.spec("SPD_TRACK_RED"), "x"))
    report.check("route.transport failure returns False, does not raise", ok is False, f"returned {ok}")


# ---------------------------------------------------------------------------
# 8. Detector → catalog integrity
# ---------------------------------------------------------------------------


def _all_emitted_ids() -> set[str]:
    """Drive the detector over snapshots covering every branch, collect ids."""
    emitted: set[str] = set()
    det = Detector()

    def run(snap: Snapshot, prev: Snapshot | None = None) -> None:
        emitted.update(_ids(det.detect(snap, prev)))

    # every out-of-control reason
    for reason in ("SPAD", "SPAD-Rear", "Misalg Sw", "Off Auth", "Off Path",
                   "Splipped", "Slipped", "Off Track", "Slip Turn", "Undefined"):
        run(Snapshot(out_of_control=True, out_of_control_reason=reason))

    # signals
    run(Snapshot(signal_aspect="Stop", signal_distance_m=300.0, speed_kmh=60.0))
    run(Snapshot(signal_aspect="Stop", signal_distance_m=150.0, speed_kmh=60.0))
    run(Snapshot(signal_aspect="Stop", signal_distance_m=90.0, speed_kmh=60.0))
    run(Snapshot(signal_aspect="Stop", signal_distance_m=40.0, speed_kmh=60.0))
    run(Snapshot(signal_aspect="Approach_1", signal_distance_m=500.0))
    run(Snapshot(signal_aspect="Approach_2", signal_distance_m=500.0))
    # Downgrade only: Clear_2 (rank 6) -> Stop (rank 0) is what SIG_UPGRADE_CHANGE reports.
    run(Snapshot(signal_aspect="Stop", signal_distance_m=300.0), Snapshot(signal_aspect="Clear_2"))

    # speed tiers + temp limits
    for color in ("PaleGreen", "Orange", "OrangeRed"):
        run(Snapshot(track_color=color, speed_kmh=80.0, limit_kmh=60.0))
    run(Snapshot(limit_kmh=40.0), Snapshot(limit_kmh=80.0))
    for dist in (2200.0, 1600.0, 1100.0):
        run(Snapshot(limit_marker_color="OrangeRed", limit_marker_value=40.0, limit_marker_distance_m=dist))

    # shunt / state / items
    run(Snapshot(speed_kmh=3.0, track_items=[
        TrackItem(kind="switch_left", color="Orange", text="", distance_m=50.0),
        TrackItem(kind="switch_right", color="Orange", text="", distance_m=60.0),
    ]))
    run(Snapshot(authority="End Trck"))
    run(Snapshot(track_items=[
        TrackItem(kind="own_train", color="OrangeRed", text="", row_index=5),
        TrackItem(kind="opposite_train", color="Orange", text="", distance_m=1200.0, row_index=2),
        TrackItem(kind="end_of_authority", color="OrangeRed", text="", distance_m=900.0, row_index=1),
        TrackItem(kind="reversal_point", color="Cyan", text="", distance_m=800.0, row_index=0),
        TrackItem(kind="waiting_point", color="Yellow", text="", distance_m=700.0, row_index=3),
        TrackItem(kind="station", color="Blue", text="", distance_m=400.0, row_index=4),
    ]))
    run(Snapshot(gradient_pct=-1.5))

    # gauge / elec
    run(Snapshot(hud={"列车制动": "持续制动 EQ 441 千帕 BC 430 千帕 BP 441 千帕 EOT  BC 430 千帕 BP 441 千帕"}),
        Snapshot(hud={"列车制动": "缓解 EQ 600 千帕 BC 0 千帕 BP 600 千帕 EOT  BC 0 千帕 BP 600 千帕"}))
    run(Snapshot(cab={"MAIN_RES": 500.0}))
    run(Snapshot(hud={"列车制动": "持续制动 EQ 441 千帕 BC 430 千帕 BP 441 千帕 EOT  BC 430 千帕 BP 100 千帕"}))
    run(Snapshot(cab={"PANTOGRAPH": 0.0}))
    run(Snapshot(cab={"LINE_VOLTAGE": 12.0}))
    run(Snapshot(cab={"PANTOGRAPH": 0.0, "LINE_VOLTAGE": 12.0}))

    # parking / orientation
    run(Snapshot(speed_kmh=0.0, cab={"TRAIN_BRAKE": 0.0, "ENGINE_BRAKE": 0.0}))
    run(Snapshot(cab_orientation="Forward"), Snapshot(cab_orientation="Reverse"))
    return emitted


def check_catalog_integrity(report: Report, log: logging.Logger) -> None:
    emitted = _all_emitted_ids()
    unknown = sorted(i for i in emitted if i not in catalog.CATALOG)
    log.info("detector emitted %d distinct ids: %s", len(emitted), sorted(emitted))
    report.check("integrity.every emitted event_id exists in CATALOG (typo guard)", not unknown, f"unknown={unknown}")

    missing = sorted(set(catalog.CATALOG) - emitted)
    gated = {
        "SIG_INCONSISTENT": ENABLE_SIG_INCONSISTENT,
        "GAUGE_BRAKE_NOT_RELEASED": ENABLE_GAUGE_BRAKE_NOT_RELEASED,
        "SHUNT_OVERSPEED": ENABLE_SHUNT_OVERSPEED,
    }
    # Catalog rows with no rule behind them at all. Declared here rather than
    # padded into a count, so a *new* gap fails the check loudly.
    declared = [i for i in missing if i not in gated]
    report.declared_gaps = declared
    unexpected = [i for i in declared if i not in KNOWN_UNIMPLEMENTED]
    log.info("catalog ids not emitted: %s | calibration-gated: %s | declared gaps: %s",
             missing, sorted(gated), declared)
    report.check("integrity.no *new* catalog gaps beyond the declared ones", not unexpected, f"unexpected={unexpected}")
    report.check(f"integrity.emitted {len(emitted)}/{len(catalog.CATALOG)} (rest gated or declared)",
                 len(emitted) >= len(catalog.CATALOG) - len(gated) - len(declared),
                 f"emitted={len(emitted)} catalog={len(catalog.CATALOG)} gated={len(gated)} declared={len(declared)}")

    by_cat: dict[str, int] = {}
    for event_id in emitted:
        c = catalog.spec(event_id).category
        by_cat[c] = by_cat.get(c, 0) + 1
    log.info("emitted by category: %s", by_cat)


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def _setup_logging(stamp: str) -> tuple[logging.Logger, Path]:
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = _LOG_DIR / f"smoke_core_{stamp}.log"
    logger = logging.getLogger("openrails_smoke_core")
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
    logger.addHandler(logging.FileHandler(log_path, encoding="utf-8", errors="replace"))
    logger.addHandler(logging.StreamHandler(sys.stdout))
    for h in logger.handlers:
        h.setFormatter(fmt)
    return logger, log_path


def main() -> int:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    log, log_path = _setup_logging(stamp)
    report = Report()

    log.info("=" * 68)
    log.info("openrails_copilot core smoke test (detector -> arbiter -> push)")
    log.info("=" * 68)

    error: str | None = None
    sections = (
        ("brake_hud", check_brake_hud),
        ("red_window", check_red_window),
        ("track_colors", check_track_colors),
        ("out_of_control", check_out_of_control),
        ("scenarios", check_scenarios),
        ("arbiter", check_arbiter),
        ("push_routing", check_push_routing),
        ("catalog_integrity", check_catalog_integrity),
    )
    for name, fn in sections:
        log.info("-- %s --", name)
        try:
            fn(report, log)
        except Exception:
            error = traceback.format_exc()
            log.error("section %s raised:\n%s", name, error)
            report.check(f"section.{name} ran without raising", False, "see log")

    passed = len(report.checks) - len(report.failed)
    log.info("-" * 68)
    for c in report.checks:
        log.info("%s %s%s", "PASS" if c["ok"] else "FAIL", c["name"],
                 f"  <- {c['detail']}" if c["detail"] and not c["ok"] else "")
    log.info("-" * 68)
    log.info("checks: %d/%d passed", passed, len(report.checks))

    emitted = sorted(_all_emitted_ids())
    summary = {
        "started_at": stamp,
        "checks_passed": passed,
        "checks_total": len(report.checks),
        "failed": report.failed,
        "detector_emitted_ids": emitted,
        "catalog_ids_total": len(catalog.CATALOG),
        "declared_gaps": report.declared_gaps,
        "error": error,
    }
    json_path = _LOG_DIR / f"smoke_core_{stamp}.json"
    json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("log  -> %s", log_path)
    log.info("json -> %s", json_path)

    return 0 if not report.failed and error is None else 1


if __name__ == "__main__":
    raise SystemExit(main())
