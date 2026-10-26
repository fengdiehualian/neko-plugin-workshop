"""自动玩的"出错不停车"契约。

背景（这就是玩家说的"猫娘卡住了"的真因）：插件曾经把任何一次循环错误都写成
``autoplay_state = "error"``，而 ``loop_runner._autoplay_loop`` 只在 ``autoplay_state ==
"running"`` 时才干活 —— 于是**一次**被 mod 拒收的动作（例如整回合缓存的战斗计划里
target_index 指向已经死掉的敌人）就让自动玩**永久空转**，没有任何代码把它改回 running，
同时 ``_should_keep_polling`` 也不再认为需要轮询，外部控制租约一过期，游戏彻底没人驱动。

这一组测试钉住四件事：
1. 任何错误都不改 ``autoplay_state``（它只表达玩家意图）；
2. "状态漂移"类拒绝要作废缓存计划 + 强制重算，下一轮按现场重来；
3. 连不上 mod 这类错误不白扔计划（现场没变，重取计划没意义）；
4. 出错后仍继续轮询（保住外部控制租约）。
"""

from __future__ import annotations

import asyncio
import contextlib

import pytest
from plugin.plugins.sts2_autoplay.action_engine import STS2ActionEngine
from plugin.plugins.sts2_autoplay.service import STS2AutoplayService
from plugin.plugins.sts2_autoplay.transport_client import (
    STS2ActionRejected,
    STS2TransportError,
    rejection_from_envelope,
)


class DummyLogger:
    def debug(self, *a, **k):
        return None

    def info(self, *a, **k):
        return None

    def warning(self, *a, **k):
        return None

    def exception(self, *a, **k):
        return None


def _service() -> STS2AutoplayService:
    return STS2AutoplayService(DummyLogger(), lambda p: None, lambda **k: None)


def _seed_plan_cache(service: STS2AutoplayService) -> None:
    runner = service._loop_runner
    runner._solver_plan_sig = "sig"
    runner._solver_plan_cached = {"in_combat": True, "line": [{"steps": [{"kind": "play_card"}]}]}
    service._state.solver_step_index = 2


# ---- 错误不再把自动玩打成终止态 -------------------------------------------------------


@pytest.mark.unit
def test_rejected_action_keeps_autoplay_running() -> None:
    service = _service()
    service._state.autoplay_state = "running"
    _seed_plan_cache(service)

    service._mark_loop_error(
        STS2ActionRejected(
            "target_index is out of range for combat.enemies[]. (code=invalid_target, action=play_card)",
            code="invalid_target",
            action_name="play_card",
            screen="COMBAT",
        )
    )

    assert service._state.autoplay_state == "running", "一次被拒绝不能停掉自动玩"


@pytest.mark.unit
def test_rejected_action_records_health_without_touching_intent() -> None:
    service = _service()
    service._state.autoplay_state = "running"
    # 走真实入口构造：这样 last_error 里会带上 code=...，与线上看到的文本一致。
    exc = rejection_from_envelope(
        {"ok": False, "error": {"code": "invalid_action", "message": "Action is not available.", "details": {"action": "select_character", "screen": "CHARACTER_SELECT"}}},
        409,
    )

    service._mark_loop_error(exc)

    assert service._state.transport_state == "error"
    assert "invalid_action" in service._state.last_error
    assert "select_character" in service._state.last_error
    assert service._state.consecutive_errors == 1
    assert service._state.autoplay_state == "running"


@pytest.mark.unit
def test_retryable_rejection_invalidates_plan_cache_and_forces_replan() -> None:
    service = _service()
    service._state.autoplay_state = "running"
    _seed_plan_cache(service)

    service._mark_loop_error(STS2ActionRejected("boom", code="invalid_target", action_name="play_card"))

    assert service._loop_runner._solver_plan_cached is None, "旧计划必须丢掉"
    assert service._loop_runner._solver_plan_sig is None
    assert service._state.solver_step_index == 0, "新计划是从当前局面算的，下标从头开始"
    assert service._state.force_replan_requested is True, "手上的现场也是旧的，必须强制重算"


@pytest.mark.unit
def test_retryable_rejection_uses_mod_declared_retryable_flag() -> None:
    # mod 显式声明 retryable=true（如 state_unavailable）时，即使 code 不在本地清单里也照办。
    service = _service()
    service._state.autoplay_state = "running"
    _seed_plan_cache(service)

    service._mark_loop_error(STS2ActionRejected("boom", code="weird_code", retryable=True))

    assert service._state.force_replan_requested is True


@pytest.mark.unit
def test_transport_failure_keeps_plan_cache() -> None:
    # 连不上 mod：现场没变，白扔计划没意义。
    service = _service()
    service._state.autoplay_state = "running"
    _seed_plan_cache(service)

    service._mark_loop_error(STS2TransportError("无法连接 STS2-Agent: All connection attempts failed"))

    assert service._state.autoplay_state == "running"
    assert service._loop_runner._solver_plan_cached is not None
    assert service._state.force_replan_requested is False


@pytest.mark.unit
def test_non_retryable_rejection_keeps_plan_cache() -> None:
    service = _service()
    service._state.autoplay_state = "running"
    _seed_plan_cache(service)

    service._mark_loop_error(STS2ActionRejected("这个角色锁着", code="character_locked"))

    assert service._state.autoplay_state == "running"
    assert service._loop_runner._solver_plan_cached is not None
    assert service._state.force_replan_requested is False


# ---- 循环层：被拒一次之后必须继续尝试（死锁回归） --------------------------------------


@pytest.mark.unit
def test_autoplay_loop_keeps_stepping_after_a_rejection() -> None:
    """**这条就是"猫娘卡住了"的回归测试。**

    旧行为：第一次被拒 → autoplay_state 变 "error" → 循环守卫
    ``autoplay_state != "running"`` 永远成立 → 每 0.25 秒空转，一次都不再尝试。
    新行为：错误只记健康，循环退避后**继续**走下一步。
    """
    service = _service()
    service._state.autoplay_state = "running"
    service._cfg["action_interval_seconds"] = 0.01

    calls: list[int] = []

    async def flaky_step() -> dict:
        calls.append(len(calls) + 1)
        if len(calls) == 1:
            raise rejection_from_envelope(
                {"ok": False, "error": {"code": "invalid_target", "message": "target_index is out of range.", "details": {"action": "play_card"}}},
                409,
            )
        return {"status": "ok"}

    service.run_autoplay_step = flaky_step  # type: ignore[method-assign]

    async def drive() -> None:
        task = asyncio.ensure_future(service._loop_runner._autoplay_loop())
        try:
            for _ in range(400):
                await asyncio.sleep(0.01)
                if len(calls) >= 3:
                    break
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    asyncio.run(drive())

    assert len(calls) >= 3, f"被拒之后必须继续尝试，实际只走了 {len(calls)} 步"
    assert service._state.autoplay_state == "running"


# ---- 轮询与退避 -----------------------------------------------------------------------


@pytest.mark.unit
def test_should_keep_polling_while_in_error_state() -> None:
    # 即使某个历史版本把状态留在了 error，也绝不能停止轮询：租约一过期 mod 就以为插件走了。
    service = _service()
    service._state.autoplay_state = "error"
    service._cfg["companion_mode_enabled"] = False

    assert service._should_keep_polling() is True


@pytest.mark.unit
def test_autoplay_retry_backoff_grows_then_caps() -> None:
    service = _service()
    service._cfg["action_interval_seconds"] = 1.5

    service._state.consecutive_errors = 1
    first = service._autoplay_retry_backoff()
    service._state.consecutive_errors = 2
    second = service._autoplay_retry_backoff()
    service._state.consecutive_errors = 50
    capped = service._autoplay_retry_backoff()

    assert first == pytest.approx(1.5)
    assert second > first
    assert capped == pytest.approx(8.0), "封顶 8 秒，别让日志被刷爆"


@pytest.mark.unit
def test_force_replan_request_wins_over_cached_operation() -> None:
    service = _service()
    service._state.force_replan_requested = True
    snapshot = {
        "classification": {"state_name": "combat"},
        "agent_operation": {"action_type": "play_card", "kwargs": {"card_index": 2}, "decision_epoch": 99},
    }
    service._state.guidance_generation = 0

    assert service._should_rebuild_operation(snapshot) is True


# ---- 动作层：越界目标在最后一刻按现场修正 ----------------------------------------------


def _play_card_snapshot(*, valid: list[int] | None, requires_target: bool = True) -> dict:
    card: dict = {"index": 2, "card_id": "STRIKE_SILENT", "requires_target": requires_target}
    if valid is not None:
        card["valid_target_indices"] = valid
        card["target_index_space"] = "enemies"
    return {
        "action_registry": [
            {
                "id": "reg-play-card",
                "type": "play_card",
                "category": "combat",
                "label": "play_card",
                "raw": {"name": "play_card", "requires_index": True, "requires_target": False},
                "default_kwargs": {},
                "requires_index": True,
                "requires_target": False,
            }
        ],
        "raw_state": {"combat": {"hand": [card], "enemies": [{"index": 0, "enemy_id": "LEAF_SLIME_M", "is_alive": True}]}},
    }


@pytest.mark.unit
def test_stale_target_index_is_repaired_from_live_hand() -> None:
    # 整回合缓存的计划里 target_index=1，但现场只剩一个敌人（合法集 [0]）——必须就地改成 0。
    engine = STS2ActionEngine()
    snapshot = _play_card_snapshot(valid=[0])
    operation = {"action_type": "play_card", "kwargs": {"card_index": 2, "target_index": 1}}

    validated = engine.validate(snapshot, operation)

    assert validated is not None
    assert validated["kwargs"]["target_index"] == 0
    assert validated["target_repair"] == {"card_index": 2, "from": 1, "to": 0, "valid": [0]}


@pytest.mark.unit
def test_valid_target_index_is_left_alone() -> None:
    engine = STS2ActionEngine()
    snapshot = _play_card_snapshot(valid=[0, 1])
    operation = {"action_type": "play_card", "kwargs": {"card_index": 2, "target_index": 1}}

    validated = engine.validate(snapshot, operation)

    assert validated is not None
    assert validated["kwargs"]["target_index"] == 1
    assert validated["target_repair"] == {}


@pytest.mark.unit
def test_missing_target_is_filled_from_live_hand() -> None:
    engine = STS2ActionEngine()
    snapshot = _play_card_snapshot(valid=[0])
    operation = {"action_type": "play_card", "kwargs": {"card_index": 2}}

    validated = engine.validate(snapshot, operation)

    assert validated is not None
    assert validated["kwargs"]["target_index"] == 0


@pytest.mark.unit
def test_no_guess_when_live_hand_has_no_valid_set() -> None:
    # 拿不到合法集（旧 mod / 非战斗）就不猜：宁可发原值，也不擅自改目标。
    engine = STS2ActionEngine()
    snapshot = _play_card_snapshot(valid=None)
    operation = {"action_type": "play_card", "kwargs": {"card_index": 2, "target_index": 1}}

    validated = engine.validate(snapshot, operation)

    assert validated is not None
    assert validated["kwargs"]["target_index"] == 1
    assert validated["target_repair"] == {}


# ---- 错误分类 -------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize("code", ["invalid_target", "invalid_action", "state_unavailable"])
def test_state_drift_codes_are_retryable(code: str) -> None:
    payload = {"ok": False, "error": {"code": code, "message": "boom", "details": {"action": "play_card", "screen": "COMBAT"}}}

    exc = rejection_from_envelope(payload, 409)

    assert isinstance(exc, STS2ActionRejected)
    assert exc.retryable is True
    assert exc.code == code
    assert exc.action_name == "play_card"
    assert exc.screen == "COMBAT"


@pytest.mark.unit
def test_unknown_code_is_not_retryable_by_default() -> None:
    payload = {"ok": False, "error": {"code": "internal_error", "message": "boom"}}

    exc = rejection_from_envelope(payload, 500)

    assert isinstance(exc, STS2ActionRejected)
    assert exc.retryable is False


@pytest.mark.unit
def test_envelope_without_error_object_stays_a_plain_transport_error() -> None:
    exc = rejection_from_envelope({"ok": False}, 502)

    assert isinstance(exc, STS2ActionRejected) is False
    assert isinstance(exc, STS2TransportError)


@pytest.mark.unit
def test_rejection_as_dict_is_serializable_for_status() -> None:
    exc = STS2ActionRejected("boom", code="invalid_target", action_name="play_card", screen="COMBAT")

    assert exc.as_dict() == {
        "code": "invalid_target",
        "action": "play_card",
        "screen": "COMBAT",
        "retryable": True,
        "message": "boom",
    }
