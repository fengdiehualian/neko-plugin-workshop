"""HostPushGate and LiveStatePublisher behavior (C-owned)."""

import json

from kards_companion.delivery.contracts import (
    STATUS_ACCEPTED,
    STATUS_LEGACY_UNCONFIRMED,
    STATUS_REJECTED,
    STATUS_UNKNOWN,
    GateResult,
    HostPushConfig,
    HostPushGate,
    normalize_receipt,
)
from kards_companion.delivery.publisher import (
    LIVE_STATE_TOOL_NAME,
    LiveStatePublisher,
    PublishStatus,
    build_commentary_prompt,
    build_live_state_text,
    exit_text,
    push_was_submitted,
    semantic_fingerprint,
)
from kards_companion.runtime.situation import build_situation
from kards_companion.state.model import (
    E_HQ_DAMAGE,
    SCENE_MATCH,
    BoardUnit,
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


def make_state(**kw) -> MatchState:
    # Explicit source time: production always carries a real capture time, and
    # tests must not rely on a wall-clock default compared with FakeClock.
    base: dict = {"scene": SCENE_MATCH, "in_match": True, "my_hq": 20,
                  "enemy_hq": 20, "game_number": 1, "detected_at": 1000.0}
    base.update(kw)
    return MatchState(**base)


# ---------------------------------------------------------------- receipts

def test_receipt_normalization_is_honest() -> None:
    assert normalize_receipt({"submitted": True})[0] == STATUS_ACCEPTED
    assert normalize_receipt({"ok": True})[0] == STATUS_ACCEPTED
    assert normalize_receipt({"submitted": False})[0] == STATUS_REJECTED
    assert normalize_receipt({"ok": False})[0] == STATUS_REJECTED
    # Explicit false wins over a conflicting true field.
    assert normalize_receipt({"submitted": False, "ok": True})[0] == STATUS_REJECTED
    assert normalize_receipt({"submitted": True, "ok": False})[0] == STATUS_REJECTED
    # Old SDK without any receipt is unconfirmed, never host-accepted.
    assert normalize_receipt(None)[0] == STATUS_LEGACY_UNCONFIRMED
    # Empty/unknown shapes are unknown: do not auto-retry.
    assert normalize_receipt({})[0] == STATUS_UNKNOWN
    assert normalize_receipt(object())[0] == STATUS_UNKNOWN
    # Non-boolean strings are not explicit confirmations.
    assert normalize_receipt({"submitted": "false"})[0] == STATUS_UNKNOWN

    class ObjectReceipt:
        def __init__(self, submitted=None):
            self.submitted = submitted

    class IsOkOnly:
        def __init__(self, ok):
            self._ok = ok

        def is_ok(self):
            return self._ok

    assert normalize_receipt(ObjectReceipt(True))[0] == STATUS_ACCEPTED
    assert normalize_receipt(ObjectReceipt(False))[0] == STATUS_REJECTED
    assert normalize_receipt(IsOkOnly(True))[0] == STATUS_ACCEPTED
    assert normalize_receipt(IsOkOnly(False))[0] == STATUS_REJECTED
    # Explicit submitted/ok false wins over a conflicting true value.
    assert normalize_receipt({"submitted": True, "ok": False})[0] == STATUS_REJECTED


def test_gate_receipt_results() -> None:
    clock = FakeClock()
    for receipt, expected in ([True, STATUS_ACCEPTED],
                              [{"submitted": False}, STATUS_REJECTED],
                              [None, STATUS_LEGACY_UNCONFIRMED],
                              [{}, STATUS_UNKNOWN]):
        gate = HostPushGate(Recorder(receipt), now_fn=clock)
        result = gate.push("read", parts=[], visibility=[])
        assert isinstance(result, GateResult)
        assert result.status == expected
        assert result.submitted is (expected == STATUS_ACCEPTED)
        # Unknown/legacy are never retryable as transport errors.
        assert result.retryable is False


# ---------------------------------------------------------------- gate hard gates

def test_dry_run_never_calls_host_and_is_simulated() -> None:
    clock = FakeClock()
    push = Recorder(True)
    gate = HostPushGate(push, config=HostPushConfig(dry_run=True), now_fn=clock)
    result = gate.push("respond", parts=[])
    assert result.allowed and result.simulated and not result.submitted
    assert result.status == "simulated"
    assert push.calls == []
    assert gate.is_simulating


def test_consent_false_blocks_read_respond_expire_and_test() -> None:
    clock = FakeClock()
    push = Recorder(True)
    gate = HostPushGate(push, config=HostPushConfig(consent=False), now_fn=clock)
    for intent in ("read", "respond", "context", "expire", "test"):
        result = gate.push(intent)
        assert not result.allowed and not result.submitted
        assert result.reason == "consent_required"
    assert push.calls == []


def test_pause_blocks_terminal_respond_but_allows_expire() -> None:
    clock = FakeClock()
    push = Recorder(True)
    gate = HostPushGate(push, now_fn=clock)
    gate.pause("user_pause")
    terminal = gate.push("respond", terminal=True, parts=[])
    assert not terminal.allowed and terminal.reason == "paused"
    assert gate.push("expire").accepted
    assert len(push.calls) == 1
    gate.resume()
    assert gate.push("respond", terminal=True, parts=[]).accepted


def test_stop_epoch_and_token_invalidate_old_ticks() -> None:
    clock = FakeClock()
    push = Recorder(True)
    gate = HostPushGate(push, now_fn=clock)
    old_token = gate.get_token()
    old_epoch = gate.epoch

    gate.begin_stop("monitoring_stopped")
    assert gate.phase == "stopping"
    # A new expire is allowed while stopping.
    assert gate.push("expire").accepted
    # Normal/terminal pushes are refused.
    assert gate.push("respond", terminal=True).reason == "stopping"
    # An old tick using the pre-stop token is rejected.
    stale = gate.push("respond", context_token=old_token)
    assert stale.reason == "stale_context"
    stale_epoch = gate.push("respond", expected_epoch=old_epoch)
    assert stale_epoch.reason == "stale_epoch"
    # A tick that captured the token after begin_stop may still expire.
    stopping_token = gate.get_token()
    assert gate.push("expire", context_token=stopping_token).accepted

    gate.finish_stop()
    assert gate.push("expire").reason == "stopped"
    # A late begin_stop must not reopen the expire channel.
    gate.begin_stop("late")
    assert gate.push("expire").reason == "stopped"

    gate.reopen()
    assert gate.epoch > old_epoch
    assert gate.get_token() != old_token
    assert gate.push("respond", terminal=True, parts=[]).accepted
    assert gate.push("respond", context_token=old_token).reason == "stale_context"

    # A pause from the previous lifecycle does not survive a restart.
    gate.pause("user_pause")
    gate.begin_stop("restart")
    gate.finish_stop()
    gate.reopen()
    assert not gate.paused
    assert gate.push("respond", parts=[]).accepted


def test_test_intent_requires_explicit_one_shot_authorization() -> None:
    clock = FakeClock()
    push = Recorder(True)
    gate = HostPushGate(push, config=HostPushConfig(
        authorized_real_test=False), now_fn=clock)
    result = gate.push("test", parts=[{"type": "text", "text": "ping"}])
    assert not result.allowed and result.simulated
    assert result.reason == "test_not_authorized"
    assert push.calls == []

    authorized = HostPushGate(push, config=HostPushConfig(
        authorized_real_test=True), now_fn=clock)
    first = authorized.push("test")
    assert first.accepted and len(push.calls) == 1
    second = authorized.push("test")
    assert not second.allowed and second.reason == "test_authorization_used"
    assert len(push.calls) == 1
    # The main entry revokes and re-grants per explicit test action; a fresh
    # grant is a new one-shot, not a permanently burned gate.
    authorized.update_config(HostPushConfig(authorized_real_test=False))
    authorized.update_config(HostPushConfig(authorized_real_test=True))
    third = authorized.push("test")
    assert third.accepted and len(push.calls) == 2


def test_gate_records_are_bounded_and_contain_no_payload() -> None:
    gate = HostPushGate(Recorder(True), config=HostPushConfig(max_records=2))
    gate.push("respond", parts=[{"type": "text", "text": "SECRET_PROMPT"}],
              metadata={"api_" + "key": "SECRET_" + "KEY"})
    gate.push("respond")
    gate.push("respond")
    snapshot = gate.snapshot()
    assert len(snapshot["records"]) == 2
    rendered = json.dumps(snapshot)
    assert "SECRET_PROMPT" not in rendered
    assert "SECRET_KEY" not in rendered
    assert "parts" not in rendered


# ---------------------------------------------------------------- publisher

def test_publish_uses_gate_and_coalesces() -> None:
    clock = FakeClock()
    push = Recorder(True)
    gate = HostPushGate(push, now_fn=clock)
    publisher = LiveStatePublisher(gate=gate, now_fn=clock, refresh_seconds=5,
                                   max_age_seconds=30)
    state = make_state()

    first = publisher.publish(state, text="T1")
    second = publisher.publish(make_state(), text="T1")
    assert first.status is PublishStatus.SUBMITTED and first.submitted
    assert second.status is PublishStatus.COALESCED and second.coalesced
    assert len(push.calls) == 1
    assert push.calls[0]["visibility"] == []
    assert push.calls[0]["ai_behavior"] == "read"
    assert push.calls[0]["coalesce_key"].startswith("kards:live-state:")
    assert push.calls[0]["metadata"]["session_id"] == ""

    assert publisher.publish(make_state(my_hq=13), text="T2").submitted
    assert len(push.calls) == 2
    assert publisher.expire(reason="test", now=clock.t + 1)
    assert len(push.calls) == 3
    assert "已失效" in push.calls[2]["parts"][0]["text"]
    assert push.calls[2]["metadata"]["context_expired"] is True
    assert publisher.cursor is None


def test_refresh_longer_than_max_age_does_not_refresh_before_ttl() -> None:
    clock = FakeClock()
    push = Recorder(True)
    publisher = LiveStatePublisher(push_message=push, now_fn=clock,
                                   refresh_seconds=30, max_age_seconds=10)
    state = make_state()
    assert publisher.publish(state, text="A").submitted
    clock.t += 9
    assert publisher.publish(make_state(detected_at=1000.0), text="A").coalesced
    clock.t += 2  # TTL expired; a new observation must carry a new source time
    assert publisher.publish(make_state(detected_at=clock.t),
                             text="A").submitted
    assert len(push.calls) == 2


def test_dry_run_publisher_isolated_from_real_cursor() -> None:
    clock = FakeClock()
    push = Recorder(True)
    gate = HostPushGate(push, config=HostPushConfig(dry_run=True), now_fn=clock)
    publisher = LiveStatePublisher(gate=gate, now_fn=clock)
    result = publisher.publish(make_state(), text="DRY")
    assert result.status is PublishStatus.SUPPRESSED
    assert result.simulated and not result.submitted
    assert push.calls == []
    assert publisher.cursor is None
    assert publisher.simulation_cursor is not None

    publisher.update_config(dry_run=False)
    assert publisher.publish(make_state(my_hq=12), text="LIVE").submitted
    assert len(push.calls) == 1
    assert publisher.cursor is not None and publisher.cursor.complete


def test_consent_suppression_keeps_cursor_and_cleans_up_on_restore() -> None:
    clock = FakeClock()
    push = Recorder(True)
    gate = HostPushGate(push, now_fn=clock)
    publisher = LiveStatePublisher(gate=gate, now_fn=clock)
    assert publisher.publish(make_state(), text="A").submitted
    assert len(push.calls) == 1
    old_fingerprint = publisher.cursor.fingerprint

    publisher.update_config(consent=False)
    suppressed = publisher.publish(make_state(my_hq=10), text="B")
    assert suppressed.status is PublishStatus.SUPPRESSED
    assert not suppressed.delivered
    assert len(push.calls) == 1
    assert publisher.cursor is not None
    assert publisher.cursor.fingerprint == old_fingerprint  # no fake update
    assert publisher.pending_invalidation

    publisher.update_config(consent=True)
    clock.t += 1
    restored = publisher.publish(make_state(my_hq=10), text="B")
    assert restored.submitted
    # Tombstone for the old context, then the new state.
    assert len(push.calls) == 3
    assert push.calls[1]["metadata"]["context_expired"] is True
    assert push.calls[2]["metadata"]["context_expired"] is False
    assert not publisher.pending_invalidation


def test_expire_incomplete_retries_with_bounded_backoff_and_dedupes() -> None:
    clock = FakeClock()
    attempts = []

    def flaky(**kwargs):
        attempts.append(kwargs)
        return False

    publisher = LiveStatePublisher(push_message=flaky, now_fn=clock,
                                   expire_retry_base_seconds=1.0,
                                   expire_retry_max_seconds=4.0)
    assert not publisher.publish(make_state(), text="X")
    assert publisher.cursor is not None and not publisher.cursor.complete

    # First tombstone attempt fails; a duplicate call before the backoff is
    # suppressed (no second host attempt).
    assert not publisher.expire(reason="failed_publish")
    count = len(attempts)
    assert not publisher.expire(reason="failed_publish")
    assert len(attempts) == count

    clock.t += 1.0
    assert not publisher.expire(reason="failed_publish")
    assert len(attempts) == count + 1
    assert publisher.cursor is not None
    assert publisher.cursor.expire_attempts >= 1


def test_expire_unknown_receipt_is_terminal_and_never_retries() -> None:
    clock = FakeClock()
    push = Recorder({})
    publisher = LiveStatePublisher(push_message=push, now_fn=clock,
                                   expire_retry_base_seconds=2.0)
    # Unknown receipt: honest failure, but no immediate retry duplication.
    assert not publisher.publish(make_state(), text="X")
    assert publisher.cursor is not None

    push.result = {}  # tombstone also unknown
    assert not publisher.expire(reason="test")
    count = len(push.calls)
    assert not publisher.expire(reason="test")
    assert len(push.calls) == count
    assert publisher.cursor is not None
    assert publisher.cursor.confirmed is False

    # Advancing past any backoff must not re-send an unknown tombstone.
    clock.t += 100.0
    assert not publisher.expire(reason="test")
    assert len(push.calls) == count
    assert publisher.cursor is not None


def test_expire_legacy_none_receipt_is_terminal() -> None:
    clock = FakeClock()
    push = Recorder(None)
    publisher = LiveStatePublisher(push_message=push, now_fn=clock)
    assert not publisher.publish(make_state(), text="X")
    assert not publisher.expire(reason="test")
    count = len(push.calls)
    clock.t += 100.0
    assert not publisher.expire(reason="test")
    assert len(push.calls) == count
    assert publisher.cursor is not None
    assert publisher.cursor.confirmed is False


def test_unconfirmed_read_cursor_is_never_reported_as_coalesced() -> None:
    clock = FakeClock()
    push = Recorder({})
    publisher = LiveStatePublisher(push_message=push, now_fn=clock,
                                   refresh_seconds=30, max_age_seconds=60)
    first = publisher.publish(make_state(), text="X")
    assert first.status is PublishStatus.FAILED
    assert publisher.cursor is not None and publisher.cursor.confirmed is False

    second = publisher.publish(make_state(), text="X")
    assert second.status is PublishStatus.SUPPRESSED
    assert not second.delivered and not second.submitted
    assert second.reason == "unconfirmed_receipt"
    assert len(push.calls) == 1, "unconfirmed state is not re-pushed"


def test_dry_run_does_not_hide_consent_rejection() -> None:
    clock = FakeClock()
    push = Recorder(True)
    gate = HostPushGate(push, config=HostPushConfig(dry_run=True, consent=False),
                        now_fn=clock)
    publisher = LiveStatePublisher(gate=gate, now_fn=clock)

    result = publisher.publish(make_state(), text="X")
    assert result.status is PublishStatus.SUPPRESSED
    assert result.reason == "consent_required"
    assert push.calls == []

    entered = publisher.enter(make_state(), target="lanlan")
    assert entered.status is PublishStatus.SUPPRESSED
    assert entered.reason == "consent_required"
    assert push.calls == []


def test_publish_and_expire_use_expected_context_token() -> None:
    clock = FakeClock()
    push = Recorder(True)
    gate = HostPushGate(push, now_fn=clock)
    publisher = LiveStatePublisher(gate=gate, now_fn=clock)
    token = gate.get_token()
    gate.begin_stop("restart")
    gate.reopen()
    result = publisher.publish(make_state(), text="OLD", context_token=token)
    assert result.status is PublishStatus.FAILED
    assert result.reason in {"stale_context", "stale_epoch"}
    assert push.calls == []

    # A fresh token from the new epoch works.
    assert publisher.publish(make_state(), text="NEW").submitted


def test_context_enter_and_restore_use_gate_and_document_api() -> None:
    clock = FakeClock()
    push = Recorder(True)
    gate = HostPushGate(push, now_fn=clock)
    publisher = LiveStatePublisher(gate=gate, now_fn=clock, max_age_seconds=30)

    entered = publisher.enter(make_state(), target="lanlan", session_id="s1")
    assert entered.submitted
    call = push.calls[0]
    assert call["ai_behavior"] == "read" and call["visibility"] == []
    assert call["priority"] == 0
    assert call["target_lanlan"] == "lanlan"
    assert call["metadata"]["context_action"] == "enter"
    assert call["metadata"]["context_priority"] == 0
    assert "context_api" not in call["metadata"]
    assert LIVE_STATE_TOOL_NAME in call["parts"][0]["text"]
    assert "LiveStatePublisher" not in call["parts"][0]["text"]

    restored = publisher.restore(make_state(), target="lanlan", session_id="s1")
    assert restored.submitted
    exit_call = push.calls[1]
    assert exit_call["metadata"]["context_action"] == "restore"
    assert exit_call["metadata"]["context_expired"] is True
    assert exit_call["ai_behavior"] == "read" and exit_call["visibility"] == []
    exit_text = exit_call["parts"][0]["text"]
    assert "已暂停/结束" in exit_text
    assert "此前局势不再当前" in exit_text
    assert LIVE_STATE_TOOL_NAME in exit_text
    assert "恢复后的当前事实" not in exit_text
    assert publisher.context_cursor is None


def test_publish_failure_result_is_falsy_and_cursor_incomplete() -> None:
    clock = FakeClock()
    publisher = LiveStatePublisher(push_message=lambda **kw: False,
                                   now_fn=clock)
    result = publisher.publish(make_state(), text="X")
    assert result.status is PublishStatus.FAILED
    assert not result.submitted and not result.delivered and bool(result) is False
    assert publisher.cursor is not None and not publisher.cursor.complete


def test_fingerprint_and_wire_text_contracts() -> None:
    a = make_state(turn_number=3)
    b = make_state(turn_number=3)
    assert semantic_fingerprint(a) == semantic_fingerprint(b)
    assert semantic_fingerprint(a) != semantic_fingerprint(make_state(turn_number=4))
    d = make_state(turn_number=3)
    d.units = [BoardUnit(instance_id=1, side="mine", attack=2, health=2)]
    assert semantic_fingerprint(d) != semantic_fingerprint(a)

    text = build_live_state_text(a, observed_at=123.0, session_id="s1")
    payload = json.loads(text.removeprefix("KARDS:"))
    assert payload["query_tool"] == LIVE_STATE_TOOL_NAME
    assert payload["session_id"] == "s1"
    assert "'kards_live_state'" not in text


def test_commentary_prompt_is_bounded_and_excludes_untrusted_payloads() -> None:
    state = make_state()
    for uid in range(30):
        state.units.append(BoardUnit(instance_id=uid,
                                     side="mine" if uid % 2 else "enemy",
                                     attack=1, health=1, cost=1))
    prompt = build_commentary_prompt("对手展开了虎式。", state, mode="companion")
    assert prompt.startswith("KARDS 陪玩解说边界")
    assert "教练模式" not in prompt
    assert "不读取、不描述、不猜测图片" in prompt
    assert len(prompt) <= 1600
    coach = build_commentary_prompt("x", state, mode="coach")
    assert "教练模式" in coach
    long_text = build_live_state_text(state)
    assert long_text.startswith("KARDS:")
    assert len(long_text.encode("utf-8")) <= 900 + 64

# ---------------------------------------------------------------- regressions

def _prompt_body(prompt: str) -> dict:
    start = prompt.index("{")
    return json.loads(prompt[start:])


def test_expire_clears_core_and_context_with_separate_segments() -> None:
    clock = FakeClock()
    push = Recorder(True)
    publisher = LiveStatePublisher(push_message=push, now_fn=clock,
                                   max_age_seconds=30)
    assert publisher.publish(make_state(), text="R", target="a",
                             session_id="s1").submitted
    assert publisher.enter(make_state(), target="a", session_id="s1").submitted
    assert publisher.expire(reason="stopped", now=clock.t) is True
    assert publisher.cursor is None and publisher.context_cursor is None
    expired = push.calls[-2:]
    assert [call["metadata"]["segment"] for call in expired] == [
        "core", "context"]
    assert all(call["metadata"]["context_expired"] is True for call in expired)
    keys = {call["coalesce_key"] for call in expired}
    assert len(keys) == 2
    assert "已失效" in expired[0]["parts"][0]["text"]
    assert "已暂停/结束" in expired[1]["parts"][0]["text"]
    assert expired[1]["metadata"]["context_action"] == "restore"


def test_expire_one_slot_failure_still_attempts_the_other() -> None:
    clock = FakeClock()
    fail_core = True
    calls: list[dict] = []

    def push(**kwargs):
        calls.append(kwargs)
        kind = kwargs["metadata"]["kind"]
        if kind == "game_live_state_expired" and fail_core:
            return False
        return True

    publisher = LiveStatePublisher(push_message=push, now_fn=clock,
                                   max_age_seconds=30,
                                   expire_retry_base_seconds=1.0)
    assert publisher.publish(make_state(), text="R", target="a",
                             session_id="s1").submitted
    assert publisher.enter(make_state(), target="a", session_id="s1").submitted
    assert publisher.expire(reason="test", now=clock.t) is False
    # Core failed but the context tombstone was still attempted and cleared.
    assert publisher.cursor is not None and publisher.cursor.expire_attempts >= 1
    assert publisher.context_cursor is None
    assert publisher.pending_invalidation is True
    assert [call["metadata"]["segment"] for call in calls[-2:]] == [
        "core", "context"]

    fail_core = False
    clock.t += 2
    assert publisher.expire(reason="test", now=clock.t) is True
    assert publisher.cursor is None
    assert publisher.pending_invalidation is False


def test_enter_is_idempotent_for_accepted_and_unknown_receipts() -> None:
    clock = FakeClock()
    push = Recorder(True)
    publisher = LiveStatePublisher(push_message=push, now_fn=clock,
                                   max_age_seconds=30)
    assert publisher.enter(make_state(), target="a", session_id="s1").submitted
    again = publisher.enter(make_state(), target="a", session_id="s1")
    assert again.coalesced and again.reason == "context_unchanged"
    assert len(push.calls) == 1

    unknown_push = Recorder({})
    unknown = LiveStatePublisher(push_message=unknown_push, now_fn=clock,
                                 max_age_seconds=30)
    assert unknown.enter(make_state(), target="a",
                         session_id="s1").failed
    repeat = unknown.enter(make_state(), target="a", session_id="s1")
    assert repeat.suppressed and repeat.reason == "unconfirmed_receipt"
    assert len(unknown_push.calls) == 1


def test_enter_on_new_route_clears_old_core_then_context() -> None:
    clock = FakeClock()
    push = Recorder(True)
    publisher = LiveStatePublisher(push_message=push, now_fn=clock,
                                   max_age_seconds=30)
    assert publisher.publish(make_state(), text="R", target="alpha",
                             session_id="s1").submitted
    assert publisher.enter(make_state(), target="alpha",
                           session_id="s1").submitted
    assert publisher.enter(make_state(), target="beta",
                           session_id="s2").submitted
    assert publisher.cursor is None
    assert publisher.context_cursor is not None
    assert publisher.context_cursor.target == "beta"
    route_calls = push.calls[-3:]
    assert [call["metadata"]["segment"] for call in route_calls] == [
        "core", "context", "context"]
    assert route_calls[0]["metadata"]["context_expired"] is True
    assert route_calls[1]["metadata"]["context_expired"] is True
    assert route_calls[2]["metadata"]["context_action"] == "enter"
    assert route_calls[2]["target_lanlan"] == "beta"


def test_enter_capability_fingerprint_change_replaces_context() -> None:
    clock = FakeClock()
    push = Recorder(True)
    publisher = LiveStatePublisher(push_message=push, now_fn=clock,
                                   max_age_seconds=30)
    assert publisher.enter(make_state(), target="a", session_id="s1",
                           capabilities={"cards": {"supported": True}}
                           ).submitted
    changed = publisher.enter(make_state(), target="a", session_id="s1",
                              capabilities={"cards": {"supported": False}})
    assert changed.submitted
    assert len(push.calls) == 3
    assert push.calls[1]["metadata"]["context_expired"] is True
    assert push.calls[1]["metadata"]["segment"] == "context"
    assert push.calls[2]["metadata"]["context_action"] == "enter"
    # Repeating the new capabilities is idempotent.
    assert publisher.enter(make_state(), target="a", session_id="s1",
                           capabilities={"cards": {"supported": False}}
                           ).coalesced
    assert len(push.calls) == 3


def test_restore_only_retires_the_matching_slot() -> None:
    clock = FakeClock()
    push = Recorder(True)
    publisher = LiveStatePublisher(push_message=push, now_fn=clock,
                                   max_age_seconds=30)
    assert publisher.enter(make_state(), target="alpha",
                           session_id="s1").submitted
    before = len(push.calls)
    miss = publisher.restore(make_state(), target="beta", session_id="s2")
    assert miss.coalesced and miss.reason == "nothing_to_restore"
    assert len(push.calls) == before
    assert publisher.context_cursor is not None

    hit = publisher.restore(make_state(), target="alpha", session_id="s1")
    assert hit.submitted
    assert publisher.context_cursor is None
    assert push.calls[-1]["parts"][0]["text"] == exit_text()


def test_source_timestamp_is_preserved_and_expired_source_is_rejected() -> None:
    clock = FakeClock(1000.0)
    push = Recorder(True)
    publisher = LiveStatePublisher(push_message=push, now_fn=clock,
                                   max_age_seconds=60.0,
                                   refresh_seconds=5.0)
    assert publisher.publish(make_state(my_hq=20), text="A").submitted
    first = push.calls[-1]["metadata"]
    assert first["observed_at"] == 1000.0
    assert first["published_at"] == 1000.0
    assert first["source_time_origin"] == "state.detected_at"

    clock.t = 1005.0
    assert publisher.publish(make_state(my_hq=19, detected_at=1000.0),
                             text="B").submitted
    second = push.calls[-1]["metadata"]
    assert second["observed_at"] == 1000.0  # never refreshed to now
    assert second["published_at"] == 1005.0
    assert second["event_age_seconds"] == 5.0

    clock.t = 1061.0
    before = len(push.calls)
    stale = publisher.publish(make_state(my_hq=18, detected_at=1000.0),
                              text="C")
    assert stale.failed and stale.reason == "source_expired"
    assert len(push.calls) == before


def test_invalid_situation_source_rejects_read_but_enter_may_explain() -> None:
    clock = FakeClock()
    push = Recorder(True)
    publisher = LiveStatePublisher(push_message=push, now_fn=clock)
    state = make_state()
    state.situation = {
        "source": {"valid": False, "observed_at": 1000.0},
        "facts": {"my_hq": 20},
    }
    assert publisher.publish(state, text="R").failed
    assert not push.calls
    assert publisher.enter(state, target="a", session_id="s1").submitted
    assert push.calls[0]["metadata"]["context_action"] == "enter"


def test_warm_dry_run_reauthorization_cleans_old_core_first() -> None:
    clock = FakeClock()
    push = Recorder(True)
    publisher = LiveStatePublisher(push_message=push, now_fn=clock,
                                   max_age_seconds=30)
    assert publisher.publish(make_state(), text="WARM").submitted
    publisher.update_config(dry_run=True)
    assert publisher.pending_invalidation
    dry = publisher.publish(make_state(), text="DRY")
    assert dry.suppressed and dry.reason == "dry_run"
    assert len(push.calls) == 1
    publisher.update_config(dry_run=False)
    assert publisher.pending_invalidation
    live = publisher.publish(make_state(), text="LIVE")
    assert live.submitted
    kinds = [call["metadata"]["kind"] for call in push.calls]
    assert kinds == ["game_live_state", "game_live_state_expired",
                     "game_live_state"]
    assert not publisher.pending_invalidation


def test_authorization_restored_clears_old_core_and_context() -> None:
    clock = FakeClock()
    push = Recorder(True)
    publisher = LiveStatePublisher(push_message=push, now_fn=clock,
                                   max_age_seconds=30)
    assert publisher.publish(make_state(), text="A", target="a",
                             session_id="s1").submitted
    assert publisher.enter(make_state(), target="a", session_id="s1").submitted
    publisher.update_config(consent=False)
    suppressed = publisher.publish(make_state(), text="B", target="a",
                                   session_id="s1")
    assert suppressed.suppressed and suppressed.reason == "consent_required"
    assert publisher.pending_invalidation
    assert publisher.cursor is not None and publisher.context_cursor is not None
    publisher.update_config(consent=True)
    restored = publisher.publish(make_state(my_hq=19), text="B", target="a",
                                 session_id="s1")
    assert restored.submitted
    tail = push.calls[-3:]
    assert [call["metadata"]["kind"] for call in tail] == [
        "game_live_state_expired", "kards_context_exit", "game_live_state"]
    assert publisher.cursor is not None and publisher.context_cursor is None
    assert not publisher.pending_invalidation


def test_external_gate_token_rotation_is_detected_before_publish() -> None:
    clock = FakeClock()
    push = Recorder(True)
    gate = HostPushGate(push, now_fn=clock)
    publisher = LiveStatePublisher(gate=gate, now_fn=clock, max_age_seconds=30)
    assert publisher.publish(make_state(), text="A").submitted
    gate.pause("hold")
    gate.resume()
    assert publisher.publish(make_state(), text="A").submitted
    kinds = [call["metadata"]["kind"] for call in push.calls]
    assert kinds == ["game_live_state", "game_live_state_expired",
                     "game_live_state"]


def test_read_text_and_prompt_use_the_same_situation_facts() -> None:
    state = make_state()
    state.situation = {
        "schema": "kards_situation_v1",
        "source": {"valid": True, "observed_at": 1000.0,
                   "ui_observed_at": 1000.0, "frame_ids": [7, 8]},
        "facts": {"my_hq": 20, "my_turn": "unknown"},
        "unknown": ["my_turn"],
        "limits": {"max_units_per_side": 6},
        "capabilities": {"replace_pending": "unknown",
                         "blind_visible_chat": "supported"},
        "units": {"mine": [], "enemy": []},
        "cards": {"hand_count": 3, "ttl_remaining_seconds": 4.5},
    }
    payload = json.loads(build_live_state_text(state).removeprefix("KARDS:"))
    situation = payload["situation"]
    assert situation["facts"]["my_hq"] == 20
    assert situation["limits"]["max_units_per_side"] == 6
    assert situation["cards"]["hand_count"] == 3
    # The situation payload is the sole public fact source: no legacy empty
    # board keys are injected next to it.
    assert "mine" not in payload and "my_turn" not in payload

    event = GameEvent(
        kind=E_HQ_DAMAGE, priority=5, summary="对手打脸。", ts=1000.0,
        details={"side": "enemy", "turn": 3, "band": 1, "edge": "steady",
                 "secret_key": "NO", "evidence": {"count": 2,
                                                 "frame_ids": [7, 8],
                                                 "raw_ocr": "NO"}})
    body = _prompt_body(build_commentary_prompt("对手打脸。", state,
                                                event=event))
    assert body["event"]["kind"] == E_HQ_DAMAGE
    assert body["event"]["summary"] == "对手打脸。"
    assert body["event"]["side"] == "enemy"
    assert "secret_key" not in body["event"]
    assert body["event"]["evidence"]["count"] == 2
    assert body["event"]["evidence"]["frame_ids"] == [7, 8]
    assert "raw_ocr" not in body["event"]["evidence"]
    facts = body["facts"]
    assert facts["facts"]["my_hq"] == 20
    assert facts["limits"]["max_units_per_side"] == 6
    assert facts["cards"]["hand_count"] == 3
    capabilities = facts["capabilities"]
    if "sections" in capabilities:
        sections = capabilities["sections"]
        assert sections["blind_visible_chat"]["state"] == "supported"
        assert sections["replace_pending"]["state"] == "unknown"
    else:
        assert capabilities["blind_visible_chat"] == "supported"
        assert capabilities["replace_pending"] == "unknown"


def test_prompt_trimming_keeps_valid_json_and_limits() -> None:
    state = make_state()
    state.situation = {
        "source": {"valid": True, "observed_at": 1000.0},
        "facts": {"my_hq": 20},
        "unknown": [],
        "limits": {"max_units_per_side": 6, "max_prompt_chars": 1600},
        "capabilities": {f"class_{i}": True for i in range(10)},
        "units": {"mine": [{"id": i, "desc": "x" * 80} for i in range(40)],
                  "enemy": [{"id": i, "desc": "y" * 80} for i in range(40)]},
        "cards": {"history": [{"id": i} for i in range(40)]},
        "retained": [{"id": i, "desc": "z" * 80} for i in range(20)],
    }
    prompt = build_commentary_prompt("x", state, max_prompt_chars=1100)
    assert len(prompt) <= 1100
    body = _prompt_body(prompt)
    assert body["facts"]["limits"]["max_units_per_side"] == 6
    caps = body["facts"]["capabilities"]
    if "sections" in caps:
        caps = caps["sections"]
    assert "class_0" in caps
    assert "retained" not in body["facts"]
    # The last-resort budget keeps the real source and compact unit counts,
    # never a fabricated empty board.
    assert body["facts"]["source"]["observed_at"] == 1000.0
    inner = body["facts"].get("facts")
    assert isinstance(inner, dict)
    counts = (inner.get("units") or {}).get("counts") if isinstance(
        inner.get("units"), dict) else None
    assert isinstance(counts, dict) or isinstance(
        body["facts"].get("unit_counts"), dict)
    if isinstance(inner.get("units"), dict):
        assert "observed" not in inner["units"]  # verbose per-unit lists dropped


def test_prompt_raises_instead_of_slicing_json_when_budget_is_impossible() -> None:
    import pytest
    with pytest.raises(ValueError):
        build_commentary_prompt("x", make_state(), max_prompt_chars=100)


def test_legacy_prompt_marks_unknown_instead_of_zero() -> None:
    state = make_state(my_turn=None, my_hq=None)
    body = _prompt_body(build_commentary_prompt("x", state))
    facts = body["facts"]
    assert facts["source_kind"] == "legacy_state"
    assert facts["current"]["my_turn"] == "unknown"
    assert facts["current"]["my_hq"] == "unknown"
    assert "my_turn" in facts["unknown"] and "my_hq" in facts["unknown"]


def test_prompt_keeps_all_situation_keys_regardless_of_order() -> None:
    state = make_state()
    capabilities = {f"cap_{i}": "supported" for i in range(12)}
    capabilities["unknown_extra"] = "unknown"
    state.situation = {
        "source": {"valid": True, "observed_at": 1000.0},
        "facts": {f"fact_{i}": i for i in range(10)},
        "unknown": [],
        "limits": {"max_units_per_side": 6},
        "capabilities": capabilities,
    }
    body = _prompt_body(build_commentary_prompt("x", state))
    body_caps = body["facts"]["capabilities"]
    if "sections" in body_caps:
        body_caps = body_caps["sections"]
    assert set(body_caps) == set(capabilities)
    assert set(body["facts"]["facts"]) == set(state.situation["facts"])


def test_event_evidence_recursively_drops_private_keys() -> None:
    state = make_state()
    event = GameEvent(
        kind=E_HQ_DAMAGE, priority=5, summary="x", ts=1000.0,
        details={
            "side": "mine",
            "evidence": {
                "before": {
                    "side": "mine", "lane": "front", "slot": 2,
                    "image": "data", "api_key": "secret",
                    "raw_ocr": "text", "nested": {
                        "image": "data", "note": "ok"},
                },
                "count": 1,
            },
        })
    body = _prompt_body(build_commentary_prompt("x", state, event=event))
    evidence = body["event"]["evidence"]
    assert evidence["before"]["side"] == "mine"
    assert evidence["before"]["nested"] == {"note": "ok"}
    for leaked in ("image", "api_key", "raw_ocr"):
        assert leaked not in evidence["before"]
        assert leaked not in evidence["before"]["nested"]
    assert evidence["count"] == 1


def _real_situation(*, unit_count: int = 18) -> tuple[MatchState, dict]:
    """A realistic runtime.situation payload, not a hand-simplified dict."""
    state = make_state(game_number=3, turn_number=4, my_hq=None,
                       enemy_hq=None, detected_at=100.0)
    state.units = [
        BoardUnit(
            instance_id=i, side="mine" if i % 2 else "enemy", lane="front",
            slot=i, bbox=(0.1, 0.2, 0.3, 0.4), observed=True,
            trajectory=True, track_confirmed=True, last_seen_frame=7,
            observed_at=99.9, detection_confidence=0.9,
            card_name="T-34", name_confidence=0.9)
        for i in range(1, unit_count + 1)
    ]
    models = {
        "ui_det": {
            "state": "loaded", "loaded": True,
            "supported_classes": ["unit_board", "card_hand", "played_card",
                                  "frontline", "end_turn_button"],
            "unsupported_classes": ["hq", "end_turn_button_active",
                                    "screen_victory", "screen_defeat"],
            "diagnostics": "",
        },
        "card_cls": {"state": "loaded", "loaded": True},
        "trusted_card_count": 2,
    }
    observations = [{
        "observation_id": "card:7:0", "session_id": "session-a",
        "frame_id": 7, "source_frame": 7, "observed_at": 99.8,
        "region": "card_hand", "appearance": "hand_full",
        "card_id": "t34_1942", "name": "T-34 1942型", "conf": 0.91,
    }]
    situation = build_situation(
        state, models=models, observations=observations,
        session_id="session-a", frame_id=7, observed_at=100.0, now=100.0,
        ui_observed_at=100.0, context_valid=True, reason="in_match",
        observation_valid=True, detector_available=True,
        unit_diagnostics={"status": "ok", "observation_valid": True,
                          "detector_available": True},
        ui_signals=["frontline", "unit_board", "card_hand",
                    "end_turn_button"],
        events=[])
    state.situation = situation
    return state, situation


def test_real_situation_prompt_keeps_event_summary_side_and_unknown() -> None:
    state, situation = _real_situation(unit_count=18)
    assert isinstance(situation["unknown"], dict)
    event = GameEvent(
        kind="unit_played", priority=5, summary="对方场上新出现一支单位。",
        ts=100.0, details={"side": "enemy", "entity": "u12"})
    prompt = build_commentary_prompt(event.summary, state, event=event,
                                     max_prompt_chars=1600)
    assert len(prompt) <= 1600
    body = json.loads(prompt[prompt.index("{"):])
    # Event facts survive the last-resort budget: not just the kind.
    assert body["event"]["kind"] == "unit_played"
    assert body["event"]["summary"] == "对方场上新出现一支单位。"
    assert body["event"]["side"] == "enemy"
    assert body["event"]["entity"] == "u12"
    assert body["event"]["source"]["event_ts"] == 100.0
    # Unknown is a mapping with real reasons, not [].
    unknown = body["facts"]["unknown"]
    assert isinstance(unknown, dict)
    assert "hq" in unknown["unsupported"]
    assert "unit_attack_health" in unknown["unsupported"]
    assert unknown.get("turn_number") == "local_counter_not_actual"
    assert body["facts"]["source"]["session_id"] == "session-a"
    caps = body["facts"]["capabilities"]
    if "sections" in caps:
        caps = caps["sections"]
    assert caps["ui"]["available"] is True
    assert caps["cards"].get("trusted_card_count") == 2

    wire = json.loads(build_live_state_text(state).removeprefix("KARDS:"))
    read = wire["situation"]
    assert isinstance(read["unknown"], dict)
    assert "hq" in read["unknown"]["unsupported"]
    assert "unit_attack_health" in read["unknown"]["unsupported"]
    assert read["unknown"].get("turn_number") == "local_counter_not_actual"
    read_caps = read["capabilities"]["sections"]
    assert read_caps["ui"]["available"] is True
    assert read_caps["ui"].get("supported_count") == 5
    assert read_caps["cards"].get("trusted_card_count") == 2
    assert read["recognized_cards"][0]["card_id"] == "t34_1942"


def test_push_was_submitted_is_strict_about_legacy_receipts() -> None:
    assert push_was_submitted(None) is False
    assert push_was_submitted({}) is False
    assert push_was_submitted(True) is True
    assert push_was_submitted(False) is False
    assert push_was_submitted({"submitted": True}) is True
    assert push_was_submitted({"submitted": False, "ok": True}) is False
