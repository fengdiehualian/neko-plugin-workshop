"""Single-slot dispatcher behavior, gate integration and policy boundaries."""

import json
from dataclasses import replace

from kards_companion.delivery.arbiter import ArbiterConfig, CommentaryArbiter
from kards_companion.delivery.publisher import (
    PublishResult,
    PublishStatus,
    build_commentary_prompt,
)
from kards_companion.policy.dispatcher import (
    DeliveryDispatcher,
    DispatcherConfig,
)
from kards_companion.policy.safety import (
    CLOSED,
    OPEN,
    OutputSafetyConfig,
    OutputSafetyGuard,
    SafetyCircuitBreaker,
    SafetyConfig,
)
from kards_companion.runtime.contracts import IN_MATCH
from kards_companion.state.model import (
    E_HQ_CRITICAL,
    E_HQ_DAMAGE,
    E_MATCH_END,
    E_TURN_SWITCH,
    E_UNIT_DIED,
    GameEvent,
    MatchState,
)


class FakeClock:
    def __init__(self, t: float = 1000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


class Recorder:
    def __init__(self, result=True) -> None:
        self.calls: list[dict] = []
        self.result = result

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        if callable(self.result):
            return self.result(kwargs)
        return self.result


class FakeReadPublisher:
    def __init__(self, status="submitted", reason="") -> None:
        self.status = status
        self.reason = reason
        self.calls: list[dict] = []

    def publish(self, state, *, text, target="", session_id="",
                context_token=None, source_observed_at=None, now=None):
        self.calls.append({
            "state": state, "text": text, "target": target,
            "session_id": session_id, "context_token": context_token,
            "source_observed_at": source_observed_at, "now": now,
        })
        return PublishResult(PublishStatus(self.status), target=target,
                             reason=self.reason or self.status)


def make_arbiter(**overrides) -> CommentaryArbiter:
    base: dict = {
        "mode": "companion",
        "min_priority": 1,
        "cooldown_seconds": 0.0,
        "critical_cooldown_seconds": 0.0,
        "duplicate_window_seconds": 0.0,
        "max_event_age_seconds": 12.0,
    }
    base.update(overrides)
    return CommentaryArbiter(ArbiterConfig(**base))


def make_guard(clock, **overrides) -> OutputSafetyGuard:
    base: dict = {
        "failure_threshold": 5,
        "failure_window_seconds": 60.0,
        "min_output_interval_seconds": 0.0,
        "critical_cooldown_seconds": 0.0,
    }
    base.update(overrides)
    return OutputSafetyGuard(OutputSafetyConfig(**base), now_fn=clock)


def make_config(**overrides) -> DispatcherConfig:
    base: dict = {
        "battle_event_ttl_seconds": 12.0,
        "user_chat_quiet_window_seconds": 0.0,
        "battle_output_quiet_window_seconds": 0.0,
        "battle_output_backpressure_seconds": 0.0,
        "max_attempts": 2,
    }
    base.update(overrides)
    return DispatcherConfig(**base)


def make_dispatcher(clock=None, *, config=None, arbiter=None, guard=None,
                    push=None, on_delivered=None, read_publisher=None,
                    read_breaker=None, breaker=None, known_targets=None,
                    prompt_builder=None):
    clock = clock or FakeClock()
    recorder = push if push is not None else Recorder(True)
    dispatcher = DeliveryDispatcher(
        arbiter or make_arbiter(),
        push_message=recorder,
        read_publisher=read_publisher,
        breaker=breaker,
        read_breaker=read_breaker,
        safety_guard=guard if guard is not None else make_guard(clock),
        config=config or make_config(),
        now_fn=clock,
        prompt_builder=prompt_builder or (lambda summary, state: summary),
        on_delivered=on_delivered,
        known_targets=known_targets,
    )
    return dispatcher, recorder, clock


def event(kind: str = E_HQ_DAMAGE, *, occurrence: int = 1,
          ts: float = 1000.0, side: str = "mine", summary: str | None = None,
          **details) -> GameEvent:
    return GameEvent(
        kind=kind,
        priority=5,
        summary=summary or f"{kind}:{occurrence}",
        ts=float(ts),
        details={"game": 1, "occurrence": occurrence, "side": side,
                 **details},
    )


def state(**kw) -> MatchState:
    # Explicit capture time: production state always has one, and FakeClock
    # tests must not compare a wall-clock default with the injected clock.
    base: dict = {"game_number": 1, "in_match": True, "my_hq": 20,
                  "enemy_hq": 20, "detected_at": 1000.0}
    base.update(kw)
    return MatchState(**base)


# ---------------------------------------------------------------- batch / slot

def test_tick_batch_selects_highest_not_first_and_metadata_contract() -> None:
    dispatcher, push, clock = make_dispatcher()
    candidates = [
        event(E_TURN_SWITCH, occurrence=1, ts=clock.t),
        event(E_UNIT_DIED, occurrence=2, side="enemy", ts=clock.t),
        event(E_HQ_DAMAGE, occurrence=3, side="mine", ts=clock.t - 2),
    ]

    outcomes = dispatcher.tick(
        candidates, state(), scenario=IN_MATCH, session_id="s1",
        match_id=1, now=clock.t)

    assert len(push.calls) == 1, "single slot permits one host attempt"
    call = push.calls[0]
    metadata = call["metadata"]
    assert metadata["kind"] == E_HQ_DAMAGE
    # Canonical mine-side damage priority wins the batch.
    assert metadata["priority"] == 7
    assert call["visibility"] == []
    assert call["ai_behavior"] == "respond"
    # War Thunder contract fields are flat in metadata, not top-level.
    assert "replace_pending" not in call
    assert "interrupt_pending" not in call
    assert "interrupt_battle_event" not in call
    # Every normal battle event uses the single host replace slot; only a
    # confirmed own-side critical preempts/interrupts.
    assert metadata["replace_pending"] is True
    assert metadata["replace_pending_intent"] is True
    assert metadata["interrupt_pending"] is False
    assert metadata["interrupt_battle_event"] is False
    assert metadata["plugin"] == "kards"
    assert metadata["domain"] == "kards"
    assert metadata["coalesce"] == "kards:battle_event"
    assert metadata["coalesce_key"] == "kards:battle_event"
    assert metadata["event_id"].startswith("s1|1|hq_damage|mine|")
    assert metadata["edge"] in {"enter", "upgrade", "recovery", "steady"}
    assert metadata["level"] in {"info", "notice", "warning", "critical", "terminal"}
    for key in ("battle_reply_contract", "live_reply_contract"):
        assert metadata[key] == "short_tts_line"
    assert metadata["max_reply_chars"] == 28
    assert "reply_max_chars" not in metadata
    assert "reply_contract" not in metadata
    assert "delivery_reply_contract" not in metadata
    assert metadata["plugin_recommended_reply"] == ""
    assert metadata["recommended_reply"] == ""
    assert metadata["plugin_owned_output"] is False
    assert metadata["reply_style_contract"] == "one_sentence_only"
    assert metadata["domain_prompt_contract"] == "kards_commentary_v1"
    assert metadata["response_module_hint"] == "host_llm_commentary"
    assert metadata["dialogue_policy_owner"] == "plugin"
    assert metadata["plugin_dialogue_policy"]["owner"] == "plugin"
    assert metadata["plugin_dialogue_policy"]["pace"] == "plugin"
    assert metadata["plugin_dialogue_policy"]["persona"] == "host"
    assert metadata["plugin_quiet_window_policy"]["owner"] == "plugin"
    assert metadata["host_callback_contract_version"] == "neko.callback.v1"
    contract = metadata["host_callback_contract"]
    assert "callback" not in metadata
    assert contract["version"] == "neko.callback.v1"
    assert contract["kind"] == "realtime_cue"
    assert set(contract["delivery"]) == {
        "coalesce_key", "replace_pending", "interrupt_pending", "priority",
        "expires_at", "max_age_seconds", "ttl_remaining_seconds"}
    assert set(contract["freshness"]) == {
        "event_ts", "event_age_seconds", "event_max_age_seconds",
        "event_expires_at", "event_time_valid"}
    assert contract["target"] == {"lanlan": ""}
    assert metadata["host_capabilities"] == {
        "replace_pending": "unknown",
        "interrupt_pending": "unknown",
        "blind_visible_chat": "unknown",
    }
    # TTL is current remaining delivery time; max_age_seconds is the source
    # event spec and expires_at is the original admission deadline.
    assert 0.0 < metadata["delivery_ttl_seconds"] <= 4.0
    assert metadata["event_ts"] == 998.0
    assert metadata["event_age_seconds"] == 2.0
    assert contract["delivery"]["max_age_seconds"] == metadata["event_max_age_seconds"]
    assert contract["delivery"]["ttl_remaining_seconds"] == metadata["delivery_ttl_seconds"]
    assert contract["delivery"]["expires_at"] == metadata["delivery_expires_at"]
    assert metadata["state_observed_at"] == 1000.0
    assert metadata["state_age_seconds"] == 0.0
    # Non-winners are reported as dropped, never as attempts.
    statuses = [outcome.status for outcome in outcomes]
    assert statuses.count("submitted") == 1
    assert statuses.count("dropped") == 2
    assert dispatcher.pending == 0


def test_plugin_owned_output_only_opted_in_with_trusted_short_line() -> None:
    dispatcher, push, clock = make_dispatcher(
        config=make_config(plugin_owned_output_enabled=True))
    dispatcher.tick(
        [event(E_HQ_DAMAGE, occurrence=1, ts=clock.t,
               recommended_reply="稳住，别慌。")],
        state(), scenario=IN_MATCH, session_id="s", match_id=1, now=clock.t)
    call = push.calls[0]
    assert call["visibility"] == ["chat"]
    # Blind plugin-owned chat shows the validated line, not the prompt.
    assert call["ai_behavior"] == "blind"
    assert call["parts"][0]["text"] == "稳住，别慌。"
    assert call["metadata"]["plugin_recommended_reply"] == "稳住，别慌。"
    assert call["metadata"]["recommended_reply"] == "稳住，别慌。"
    assert call["metadata"]["delivery_mode"] == "plugin_owned_visible_chat"
    assert call["metadata"]["plugin_owned_output"] is True
    assert call["metadata"]["plugin_owned_output_enabled"] is True
    assert "respond" not in call

    # Default is the invisible respond channel even if a reply is supplied.
    dispatcher2, push2, clock2 = make_dispatcher()
    dispatcher2.tick(
        [event(E_HQ_DAMAGE, occurrence=1, ts=clock2.t,
               recommended_reply="稳住，别慌。")],
        state(), scenario=IN_MATCH, session_id="s", match_id=1, now=clock2.t)
    default_call = push2.calls[0]
    assert default_call["visibility"] == []
    assert default_call["ai_behavior"] == "respond"
    assert "respond" not in default_call
    assert default_call["metadata"]["plugin_owned_output"] is False
    assert default_call["metadata"]["plugin_owned_output_enabled"] is False
    assert default_call["metadata"]["delivery_mode"] == "host_respond"
    assert default_call["parts"][0]["text"] != "稳住，别慌。"


def test_single_slot_submit_drain_and_replacement() -> None:
    dispatcher, push, clock = make_dispatcher()
    low = event(E_TURN_SWITCH, occurrence=1, ts=clock.t)
    high = event(E_UNIT_DIED, occurrence=2, side="enemy", ts=clock.t)

    first = dispatcher.submit(low, state(), now=clock.t)
    assert first.accepted and first.queue_depth == 1
    second = dispatcher.submit(high, state(), now=clock.t)
    assert second.accepted and second.queue_depth == 1
    assert dispatcher.pending == 1

    outcomes = dispatcher.drain(now=clock.t)
    assert len(push.calls) == 1
    assert push.calls[0]["metadata"]["kind"] == E_UNIT_DIED
    assert [outcome.status for outcome in outcomes] == ["submitted"]
    assert dispatcher.pending == 0
    assert dispatcher.stats()["queued"] == 1
    assert dispatcher.stats()["replaced"] == 1


def test_terminal_replaces_ordinary_pending() -> None:
    dispatcher, push, clock = make_dispatcher(
        config=make_config(battle_output_backpressure_seconds=20.0))
    dispatcher.tick([event(E_UNIT_DIED, occurrence=1, side="enemy",
                           ts=clock.t)],
                    state(), scenario=IN_MATCH, session_id="s", match_id=1,
                    now=clock.t)
    assert len(push.calls) == 1

    clock.t += 1
    outcomes = dispatcher.tick(
        [event(E_TURN_SWITCH, occurrence=2, ts=clock.t)], state(),
        scenario=IN_MATCH, session_id="s", match_id=1, now=clock.t)
    assert [outcome.status for outcome in outcomes] == ["quiet_window"]
    assert dispatcher.pending == 1

    clock.t += 1
    outcomes = dispatcher.tick(
        [event(E_MATCH_END, occurrence=3, ts=clock.t)], state(),
        scenario=IN_MATCH, session_id="s", match_id=1, now=clock.t)
    assert [outcome.status for outcome in outcomes] == ["replaced", "submitted"]
    assert [call["metadata"]["kind"] for call in push.calls] == [
        E_UNIT_DIED, E_MATCH_END]
    assert dispatcher.pending == 0


def test_own_confirmed_critical_preempts_ordinary_pending() -> None:
    dispatcher, push, clock = make_dispatcher(
        config=make_config(battle_output_backpressure_seconds=20.0))
    dispatcher.tick([event(E_UNIT_DIED, occurrence=1, side="enemy",
                           ts=clock.t)],
                    state(), scenario=IN_MATCH, session_id="s", match_id=1,
                    now=clock.t)
    clock.t += 1
    dispatcher.tick([event(E_TURN_SWITCH, occurrence=2, ts=clock.t)], state(),
                    scenario=IN_MATCH, session_id="s", match_id=1,
                    now=clock.t)
    assert dispatcher.pending == 1

    clock.t += 1
    critical = event(E_HQ_CRITICAL, occurrence=3, side="mine", hp=3, band=1,
                     turn=3, ts=clock.t)
    outcomes = dispatcher.tick([critical], state(), scenario=IN_MATCH,
                               session_id="s", match_id=1, now=clock.t)
    assert [outcome.status for outcome in outcomes] == ["replaced", "submitted"]
    call = push.calls[-1]
    assert call["metadata"]["kind"] == E_HQ_CRITICAL
    assert call["metadata"]["interrupt_pending"] is True
    assert call["metadata"]["interrupt_battle_event"] is True
    assert call["metadata"]["level"] == "critical"
    assert dispatcher.pending == 0


# ---------------------------------------------------------------- TTL / retries

def test_ttl_metadata_uses_remaining_and_retry_never_refreshes_ts() -> None:
    clock = FakeClock(1000.0)
    dispatcher, push, _ = make_dispatcher(
        clock, push=Recorder(False), config=make_config(
            battle_event_ttl_seconds=6.0, max_attempts=2))
    target = event(E_HQ_DAMAGE, occurrence=1, ts=1000.0)
    outcomes = dispatcher.tick([target], state(), scenario=IN_MATCH,
                               session_id="s", match_id=1, now=1000.0)
    assert [outcome.status for outcome in outcomes] == ["retry"]
    first = push.calls[0]["metadata"]
    assert first["event_ts"] == 1000.0
    assert first["delivery_ttl_seconds"] == 6.0

    clock.t += 1
    outcomes = dispatcher.tick([], state(), scenario=IN_MATCH,
                               session_id="s", match_id=1, now=clock.t)
    assert [outcome.status for outcome in outcomes] == ["failed"]
    second = push.calls[1]["metadata"]
    assert second["event_ts"] == 1000.0
    assert second["delivery_ttl_seconds"] == 5.0
    assert second["delivery_expires_at"] == first["delivery_expires_at"]
    assert dispatcher.pending == 0


def test_pending_expires_and_is_never_marked() -> None:
    dispatcher, push, clock = make_dispatcher()
    target = event(E_TURN_SWITCH, occurrence=1, ts=clock.t)
    dispatcher.tick([target], state(), scenario=IN_MATCH, session_id="s",
                    match_id=1, now=clock.t)
    assert len(push.calls) == 1

    later = event(E_TURN_SWITCH, occurrence=2, ts=clock.t + 10)
    # No new candidate: the waiting slot would already be expired.
    dispatcher.submit(later, state(), now=clock.t + 10)
    outcomes = dispatcher.drain(now=clock.t + 20)
    assert outcomes[0].status == "expired"
    assert dispatcher.pending == 0
    assert dispatcher.arbiter.snapshot()["submitted_count"] == 1


def test_candidate_that_cannot_wait_for_cooldown_is_dropped() -> None:
    clock = FakeClock(1000.0)
    dispatcher, push, _ = make_dispatcher(
        clock,
        arbiter=make_arbiter(duplicate_window_seconds=0.0),
        config=make_config(user_chat_quiet_window_seconds=1000.0))
    dispatcher.note_user_chat(now=clock.t)
    target = event(E_TURN_SWITCH, occurrence=1, ts=clock.t)
    outcomes = dispatcher.tick([target], state(), scenario=IN_MATCH,
                               session_id="s", match_id=1, now=clock.t)
    # Quiet is a waiting reason; once the now-valid event expires it drops.
    assert outcomes[0].status == "quiet_window"
    assert push.calls == []
    clock.t += 7
    outcomes = dispatcher.tick([], state(), scenario=IN_MATCH, session_id="s",
                               match_id=1, now=clock.t)
    assert outcomes[0].status == "expired"
    assert dispatcher.pending == 0


# ---------------------------------------------------------------- callbacks

def test_on_delivered_once_and_callback_exception_never_resends() -> None:
    calls: list[tuple[str, dict]] = []

    def on_delivered(ev, state_snapshot, metadata):
        calls.append((ev.kind, metadata))
        raise RuntimeError("post-acceptance failure")

    dispatcher, push, clock = make_dispatcher(
        push=Recorder({"submitted": True}), on_delivered=on_delivered)
    dispatcher.tick([event(E_HQ_DAMAGE, occurrence=1, ts=clock.t)], state(),
                    scenario=IN_MATCH, session_id="s", match_id=1,
                    now=clock.t)
    assert len(push.calls) == 1
    assert [kind for kind, _ in calls] == [E_HQ_DAMAGE]
    assert dispatcher.pending == 0
    dispatcher.tick([], state(), scenario=IN_MATCH, session_id="s",
                    match_id=1, now=clock.t + 1)
    assert len(push.calls) == 1 and len(calls) == 1


def test_unknown_and_legacy_receipts_are_not_retried_or_marked() -> None:
    dispatcher, push, clock = make_dispatcher(push=Recorder(None))
    outcomes = dispatcher.tick(
        [event(E_HQ_DAMAGE, occurrence=1, ts=clock.t)], state(),
        scenario=IN_MATCH, session_id="s", match_id=1, now=clock.t)
    assert [outcome.status for outcome in outcomes] == ["unconfirmed"]
    assert dispatcher.pending == 0
    assert dispatcher.arbiter.snapshot()["submitted_count"] == 0
    dispatcher.tick([], state(), scenario=IN_MATCH, session_id="s",
                    match_id=1, now=clock.t + 1)
    assert len(push.calls) == 1

    dispatcher2, push2, clock2 = make_dispatcher(push=Recorder({}))
    outcomes = dispatcher2.tick(
        [event(E_HQ_DAMAGE, occurrence=1, ts=clock2.t)], state(),
        scenario=IN_MATCH, session_id="s", match_id=1, now=clock2.t)
    assert [outcome.status for outcome in outcomes] == ["unknown"]
    assert dispatcher2.pending == 0
    dispatcher2.tick([], state(), scenario=IN_MATCH, session_id="s",
                     match_id=1, now=clock2.t + 1)
    assert len(push2.calls) == 1


# ---------------------------------------------------------------- hard gates

def test_dry_run_simulates_full_chain_without_host_or_real_mark() -> None:
    dispatcher, push, clock = make_dispatcher(
        config=make_config(dry_run=True))
    target = event(E_HQ_DAMAGE, occurrence=1, ts=clock.t)
    outcomes = dispatcher.tick([target], state(), scenario=IN_MATCH,
                               session_id="s", match_id=1, now=clock.t)
    assert [outcome.status for outcome in outcomes] == ["simulated"]
    assert push.calls == []
    assert dispatcher.gate.is_simulating
    assert dispatcher.arbiter.snapshot()["submitted_count"] == 0
    records = dispatcher.simulated_records
    assert len(records) == 1
    assert set(records[0]) == {
        "kind", "priority", "reason", "status", "identity", "timestamp",
        "target_present"}
    assert "summary" not in str(records[0])

    dispatcher.update_config(replace(dispatcher.config, dry_run=False))
    assert not dispatcher.gate.is_simulating
    next_event = event(E_UNIT_DIED, occurrence=2, side="enemy", ts=clock.t)
    outcomes = dispatcher.tick([next_event], state(), scenario=IN_MATCH,
                               session_id="s", match_id=1, now=clock.t)
    assert [outcome.status for outcome in outcomes] == ["submitted"]
    assert len(push.calls) == 1
    assert dispatcher.arbiter.snapshot()["submitted_count"] == 1


def test_consent_false_blocks_even_terminal_and_pause_blocks_terminal() -> None:
    dispatcher, push, clock = make_dispatcher(
        config=make_config(llm_data_consent=False))
    terminal = event(E_MATCH_END, occurrence=1, ts=clock.t)
    outcomes = dispatcher.tick([terminal], state(), scenario=IN_MATCH,
                               session_id="s", match_id=1, now=clock.t)
    assert [outcome.status for outcome in outcomes] == ["suppressed"]
    assert outcomes[0].reason == "consent_required"
    assert push.calls == []
    assert dispatcher.arbiter.snapshot()["submitted_count"] == 0

    dispatcher2, push2, clock2 = make_dispatcher()
    dispatcher2.gate.pause("user_pause")
    terminal2 = event(E_MATCH_END, occurrence=1, ts=clock2.t)
    outcomes = dispatcher2.tick([terminal2], state(), scenario=IN_MATCH,
                                session_id="s", match_id=1, now=clock2.t)
    assert outcomes[0].status == "suppressed"
    assert outcomes[0].reason == "paused"
    assert push2.calls == []


def test_stale_tick_token_cannot_push_after_reopen() -> None:
    dispatcher, push, clock = make_dispatcher()
    stale = dispatcher.gate.get_token()
    dispatcher.gate.begin_stop("restart")
    dispatcher.gate.reopen()

    outcomes = dispatcher.tick(
        [event(E_HQ_DAMAGE, occurrence=1, ts=clock.t)], state(),
        scenario=IN_MATCH, session_id="s", match_id=1,
        context_token=stale, now=clock.t)
    assert [outcome.status for outcome in outcomes] == ["stale_epoch"]
    assert push.calls == []
    assert dispatcher.pending == 0

    # Nothing is silently retried with the new token.
    dispatcher.tick([], state(), scenario=IN_MATCH, session_id="s",
                    match_id=1, now=clock.t + 1)
    assert push.calls == []


# ---------------------------------------------------------------- routing

def test_event_target_override_is_per_item_and_never_pollutes() -> None:
    dispatcher, push, clock = make_dispatcher(
        config=make_config(target_lanlan="cfg-target"))
    dispatcher.tick(
        [event(E_TURN_SWITCH, occurrence=1, ts=clock.t,
               target_lanlan="event-target")],
        state(), scenario=IN_MATCH, session_id="s", match_id=1, now=clock.t)
    assert push.calls[0]["target_lanlan"] == "event-target"

    dispatcher.tick(
        [event(E_HQ_DAMAGE, occurrence=2, side="mine", ts=clock.t + 1)],
        state(), scenario=IN_MATCH, session_id="s", match_id=1,
        now=clock.t + 1)
    assert push.calls[1]["target_lanlan"] == "cfg-target"


def test_multiple_known_targets_are_route_unavailable() -> None:
    dispatcher, push, clock = make_dispatcher(
        known_targets=("alpha", "beta"))
    outcomes = dispatcher.tick(
        [event(E_HQ_DAMAGE, occurrence=1, ts=clock.t)], state(),
        scenario=IN_MATCH, session_id="s", match_id=1, now=clock.t)
    assert push.calls == []
    assert [outcome.status for outcome in outcomes] == ["dropped"]
    assert outcomes[0].reason == "route_unavailable"
    assert dispatcher.stats()["route_unavailable"] == 1


def test_tick_target_change_purges_pending_without_set_context() -> None:
    dispatcher, push, clock = make_dispatcher()
    dispatcher.submit(event(E_HQ_DAMAGE, occurrence=1, ts=clock.t,
                            target_lanlan="alpha"),
                      state(), now=clock.t)
    assert dispatcher.pending == 1
    outcomes = dispatcher.tick([], state(), scenario=IN_MATCH, session_id="",
                               match_id=1, target_lanlan="beta",
                               now=clock.t + 1)
    assert [outcome.status for outcome in outcomes] == ["purged"]
    assert outcomes[0].reason == "target_changed"
    assert dispatcher.pending == 0
    assert push.calls == []


def test_gate_epoch_change_purges_pending_before_attempt() -> None:
    dispatcher, push, clock = make_dispatcher()
    dispatcher.submit(event(E_HQ_DAMAGE, occurrence=1, ts=clock.t),
                      state(), now=clock.t)
    assert dispatcher.pending == 1
    dispatcher.gate.pause("hold")
    dispatcher.gate.resume()

    outcomes = dispatcher.tick([], state(), scenario=IN_MATCH, session_id="",
                               match_id=1, now=clock.t + 1)
    assert [outcome.status for outcome in outcomes] == ["purged"]
    assert outcomes[0].reason == "context_token_changed"
    assert dispatcher.pending == 0
    assert push.calls == []


def test_context_change_purges_pending() -> None:
    dispatcher, push, clock = make_dispatcher()
    dispatcher.gate.pause("hold")
    dispatcher.submit(event(E_HQ_DAMAGE, occurrence=1, ts=clock.t),
                      state(), now=clock.t)
    assert dispatcher.pending == 1

    dispatcher.set_context(target_lanlan="other")
    assert dispatcher.pending == 0
    assert dispatcher.stats()["purged"] >= 1
    assert push.calls == []


# ---------------------------------------------------------------- quiet modes

def test_user_chat_quiet_window_waits_then_expires() -> None:
    dispatcher, push, clock = make_dispatcher(
        config=make_config(user_chat_quiet_window_seconds=60.0))
    dispatcher.note_user_chat(now=clock.t, target_lanlan="lanlan",
                              is_voice=True)
    target = event(E_TURN_SWITCH, occurrence=1, ts=clock.t,
                   target_lanlan="lanlan")
    outcomes = dispatcher.tick([target], state(), scenario=IN_MATCH,
                               session_id="s", match_id=1, now=clock.t)
    assert [outcome.status for outcome in outcomes] == ["quiet_window"]
    assert push.calls == []
    assert dispatcher.pending == 1

    clock.t += 7
    outcomes = dispatcher.tick([], state(), scenario=IN_MATCH, session_id="s",
                               match_id=1, now=clock.t)
    assert outcomes[0].status == "expired"

    # A new event after the window is delivered normally.
    clock.t = 1061
    outcomes = dispatcher.tick(
        [event(E_HQ_DAMAGE, occurrence=2, side="mine", ts=clock.t)],
        state(), scenario=IN_MATCH, session_id="s", match_id=1, now=clock.t)
    assert [outcome.status for outcome in outcomes] == ["submitted"]


def test_critical_only_and_no_interrupt_policies() -> None:
    dispatcher, push, clock = make_dispatcher(
        config=make_config(interrupt_mode="critical_only"))
    outcomes = dispatcher.tick(
        [event(E_UNIT_DIED, occurrence=1, side="enemy", ts=clock.t)], state(),
        scenario=IN_MATCH, session_id="s", match_id=1, now=clock.t)
    assert [outcome.status for outcome in outcomes] == ["dropped"]
    assert outcomes[0].reason == "critical_only"
    assert push.calls == []

    critical = event(E_HQ_CRITICAL, occurrence=2, side="mine", hp=3, band=1,
                     turn=2, ts=clock.t + 1)
    outcomes = dispatcher.tick([critical], state(), scenario=IN_MATCH,
                               session_id="s", match_id=1, now=clock.t + 1)
    assert [outcome.status for outcome in outcomes] == ["submitted"]

    # no_interrupt: even a terminal event does not interrupt active chat.
    dispatcher2, push2, clock2 = make_dispatcher(
        config=make_config(interrupt_mode="no_interrupt",
                           user_chat_quiet_window_seconds=60.0))
    dispatcher2.note_user_chat(now=clock2.t)
    outcomes = dispatcher2.tick(
        [event(E_MATCH_END, occurrence=1, ts=clock2.t)], state(),
        scenario=IN_MATCH, session_id="s", match_id=1, now=clock2.t)
    assert outcomes[0].status == "quiet_window"
    assert outcomes[0].reason.endswith("_no_interrupt")
    assert push2.calls == []


def test_quiet_unit_result_downgrades_to_passive_read_context() -> None:
    read = FakeReadPublisher("submitted")
    dispatcher, push, clock = make_dispatcher(
        read_publisher=read,
        config=make_config(user_chat_quiet_window_seconds=60.0))
    dispatcher.note_user_chat(now=clock.t)
    outcomes = dispatcher.tick(
        [event(E_UNIT_DIED, occurrence=1, side="enemy", ts=clock.t,
               summary="敌方单位阵亡。")],
        state(), scenario=IN_MATCH, session_id="s", match_id=1, now=clock.t)
    assert [outcome.status for outcome in outcomes] == ["passive_context"]
    assert push.calls == []
    assert len(read.calls) == 1
    # The read channel carries the event-time state snapshot, with the real
    # source timestamp, not a publication-time pseudo-observation.
    assert read.calls[0]["text"].startswith("KARDS:")
    assert '"observed_at":1000.0' in read.calls[0]["text"]
    assert '"kind":"kards_summary"' in read.calls[0]["text"]
    stats = dispatcher.stats()
    assert stats["passive_context"] == 1
    assert stats["submitted"] == 0
    assert dispatcher.arbiter.snapshot()["submitted_count"] == 0


# ---------------------------------------------------------------- breakers

def test_read_half_open_probe_is_released_on_coalesced_and_suppressed() -> None:
    clock = FakeClock()
    read_breaker = SafetyCircuitBreaker(
        SafetyConfig(failure_threshold=1, recovery_seconds=5.0,
                     half_open_max_probes=1),
        now_fn=clock)
    read = FakeReadPublisher("failed")
    dispatcher, _push, _ = make_dispatcher(
        clock, read_publisher=read, read_breaker=read_breaker)

    assert not dispatcher.push_read(state(), text="X", now=clock.t)
    assert read_breaker.state(now=clock.t) == OPEN
    clock.t += 5.0

    read.status = "coalesced"
    assert dispatcher.push_read(state(), text="X", now=clock.t)
    assert read_breaker.snapshot(now=clock.t).probes == 0

    read.status = "suppressed"
    assert not dispatcher.push_read(state(), text="X", now=clock.t)
    assert read_breaker.snapshot(now=clock.t).probes == 0

    # A final failure must be able to consume a fresh half-open probe; this
    # would fail with safety_open if either earlier path leaked a reservation.
    read.status = "failed"
    assert not dispatcher.push_read(state(), text="X", now=clock.t)
    assert read_breaker.state(now=clock.t) == OPEN


def test_respond_half_open_probe_released_when_quiet_downgraded_to_read() -> None:
    clock = FakeClock()
    breaker = SafetyCircuitBreaker(
        SafetyConfig(failure_threshold=1, recovery_seconds=5.0,
                     half_open_max_probes=1),
        now_fn=clock)
    read = FakeReadPublisher("submitted")
    dispatcher, _push, _ = make_dispatcher(
        clock, push=Recorder(False), breaker=breaker, read_publisher=read,
        config=make_config(max_attempts=1,
                           user_chat_quiet_window_seconds=60.0))

    failed = dispatcher.tick(
        [event(E_HQ_DAMAGE, occurrence=1, side="mine", ts=clock.t)], state(),
        scenario=IN_MATCH, session_id="s", match_id=1, now=clock.t)
    assert [outcome.status for outcome in failed] == ["failed"]
    assert breaker.state(now=clock.t) == OPEN

    dispatcher.note_user_chat(now=clock.t)
    clock.t += 5.0
    outcomes = dispatcher.tick(
        [event(E_UNIT_DIED, occurrence=2, side="enemy", ts=clock.t)], state(),
        scenario=IN_MATCH, session_id="s", match_id=1, now=clock.t)
    assert [outcome.status for outcome in outcomes] == ["passive_context"]
    assert breaker.snapshot(now=clock.t).probes == 0
    assert breaker.state(now=clock.t) == "half_open"


def test_read_and_respond_breakers_are_independent() -> None:
    clock = FakeClock()
    read_breaker = SafetyCircuitBreaker(
        SafetyConfig(failure_threshold=1, recovery_seconds=5.0), now_fn=clock)
    read = FakeReadPublisher("failed")
    dispatcher, push, _ = make_dispatcher(
        clock, read_publisher=read, read_breaker=read_breaker)

    assert not dispatcher.push_read(state(), text="X", now=clock.t)
    assert read_breaker.state(now=clock.t) == OPEN
    # A failed read does not block respond and does not open the respond breaker.
    outcomes = dispatcher.tick(
        [event(E_HQ_DAMAGE, occurrence=1, ts=clock.t)], state(),
        scenario=IN_MATCH, session_id="s", match_id=1, now=clock.t)
    assert [outcome.status for outcome in outcomes] == ["submitted"]
    assert dispatcher.breaker_snapshot().state == CLOSED
    # Read channel is independently blocked now.
    assert not dispatcher.push_read(state(), text="X", now=clock.t)
    assert len(read.calls) == 1


# ---------------------------------------------------------------- config / copy

def test_update_config_always_purges_pending_and_syncs_gate() -> None:
    dispatcher, push, clock = make_dispatcher(
        config=make_config(battle_output_backpressure_seconds=20.0))
    dispatcher.tick([event(E_UNIT_DIED, occurrence=1, side="enemy",
                           ts=clock.t)],
                    state(), scenario=IN_MATCH, session_id="s", match_id=1,
                    now=clock.t)
    clock.t += 1
    dispatcher.tick([event(E_TURN_SWITCH, occurrence=2, ts=clock.t)], state(),
                    scenario=IN_MATCH, session_id="s", match_id=1,
                    now=clock.t)
    assert dispatcher.pending == 1
    token = dispatcher.gate.get_token()

    outcomes = dispatcher.update_config(
        replace(dispatcher.config, target_lanlan="new-target"))
    assert dispatcher.pending == 0
    assert [outcome.status for outcome in outcomes] == ["purged"]
    assert dispatcher.gate.get_token() == token  # consent/dry unchanged


def test_candidate_state_is_deep_copied_against_later_mutation() -> None:
    seen: list[int | None] = []
    dispatcher, push, clock = make_dispatcher(
        prompt_builder=lambda summary, snapshot: (
            seen.append(snapshot.my_hq) or summary))
    original = state(my_hq=20)
    dispatcher.submit(event(E_HQ_DAMAGE, occurrence=1, ts=clock.t), original,
                      now=clock.t)
    original.my_hq = 1
    dispatcher.drain(now=clock.t)
    assert seen == [20]
    assert push.calls


# ---------------------------------------------------------------- regressions

def test_normal_payload_uses_only_verified_top_level_kwargs() -> None:
    dispatcher, push, clock = make_dispatcher(
        config=make_config(target_lanlan="lanlan"))
    dispatcher.tick([event(E_HQ_DAMAGE, occurrence=1, ts=clock.t)], state(),
                    scenario=IN_MATCH, session_id="s", match_id=1,
                    now=clock.t)
    call = push.calls[0]
    assert set(call) == {
        "source", "visibility", "ai_behavior", "parts", "priority",
        "coalesce_key", "metadata", "target_lanlan",
    }
    assert "respond" not in call
    assert call["visibility"] == []
    assert call["ai_behavior"] == "respond"
    assert call["target_lanlan"] == "lanlan"


def test_payload_facts_use_event_time_state_source_and_age() -> None:
    dispatcher, push, clock = make_dispatcher(
        prompt_builder=build_commentary_prompt)
    snapshot = state(detected_at=995.0, my_hq=17)
    dispatcher.tick([event(E_HQ_DAMAGE, occurrence=1, ts=998.0)], snapshot,
                    scenario=IN_MATCH, session_id="s", match_id=1,
                    now=clock.t)
    call = push.calls[0]
    metadata = call["metadata"]
    assert metadata["state_observed_at"] == 995.0
    assert metadata["state_age_seconds"] == 5.0
    assert metadata["state_source"] == "event_time_snapshot"
    assert metadata["event_ts"] == 998.0
    assert metadata["event_age_seconds"] == 2.0
    prompt = call["parts"][0]["text"]
    body = json.loads(prompt[prompt.index("{"):])
    assert body["facts"]["source"]["observed_at"] == 995.0
    assert body["facts"]["source"]["age_seconds"] == 5.0
    assert body["facts"]["current"]["my_hq"] == 17


def test_read_downgrade_stale_source_releases_half_open_probe() -> None:
    clock = FakeClock()
    read_breaker = SafetyCircuitBreaker(
        SafetyConfig(failure_threshold=1, recovery_seconds=5.0,
                     half_open_max_probes=1),
        now_fn=clock)
    read = FakeReadPublisher("failed", reason="transport")
    dispatcher, push, _ = make_dispatcher(
        clock, read_publisher=read, read_breaker=read_breaker,
        config=make_config(user_chat_quiet_window_seconds=60.0))

    assert not dispatcher.push_read(state(), text="X", now=clock.t)
    assert read_breaker.state(now=clock.t) == OPEN
    clock.t += 5.0

    # A stale/invalid source is a local rejection: the half-open probe must be
    # returned, not counted as a transport failure.
    read.status = "failed"
    read.reason = "source_expired"
    assert not dispatcher.push_read(state(), text="X", now=clock.t)
    assert read_breaker.state(now=clock.t) == "half_open"
    assert read_breaker.snapshot(now=clock.t).probes == 0
    assert dispatcher.stats()["read_stale"] == 1
    assert push.calls == []


def test_quiet_downgrade_stale_source_keeps_respond_probe_released() -> None:
    clock = FakeClock()
    breaker = SafetyCircuitBreaker(
        SafetyConfig(failure_threshold=1, recovery_seconds=5.0,
                     half_open_max_probes=1), now_fn=clock)
    read = FakeReadPublisher("failed", reason="source_expired")
    dispatcher, push, _ = make_dispatcher(
        clock, push=Recorder(False), breaker=breaker, read_publisher=read,
        config=make_config(max_attempts=1,
                           user_chat_quiet_window_seconds=60.0))
    dispatcher.note_user_chat(now=clock.t)
    outcomes = dispatcher.tick(
        [event(E_UNIT_DIED, occurrence=1, side="enemy", ts=clock.t)],
        state(), scenario=IN_MATCH, session_id="s", match_id=1,
        now=clock.t)
    assert [outcome.status for outcome in outcomes] == ["quiet_window"]
    assert push.calls == []
    assert dispatcher.pending == 1
    assert dispatcher.read_breaker_snapshot().state == CLOSED
    assert dispatcher.stats()["read_stale"] == 1
