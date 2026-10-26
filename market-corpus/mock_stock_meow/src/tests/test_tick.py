"""P3：心跳、到期判定与 tick 流水线。

对应 P3 契约 §2（``@timer_interval`` 声明）、§3（tick 内部结构）、
§17 验收标准 1 / 2 / 3。
"""

from __future__ import annotations

import time
from typing import Any

from conftest import run
from storage.repo import Repo


def _value(result: Any) -> dict[str, Any]:
    assert result.is_ok(), f"期望 Ok，实际 {result!r}"
    payload = result.unwrap()
    assert isinstance(payload, dict)
    return payload


def _error_code(result: Any) -> str:
    assert result.is_err(), f"期望 Err，实际 {result!r}"
    return str(getattr(result.err(), "code", ""))


# ---------------------------------------------------------------- 心跳到期判定


def test_heartbeat_first_call_only_records_baseline(plugin) -> None:
    """首次心跳只记基线，**不跳盘**（插件重启即跳盘是最糟的体验）。"""
    _value(run(plugin.join(seed=11)))
    assert plugin._last_tick_at == 0.0
    assert await_due(plugin) == 0
    assert plugin._last_tick_at > 0.0


def await_due(plugin) -> int:
    return run(plugin._due_ticks())


def test_heartbeat_respects_classic_eight_second_interval(plugin) -> None:
    """经典四档 8 秒/tick：不到 8 秒不推进，过了就推进一个。"""
    _value(run(plugin.join(mode="classic_arcade", seed=12)))
    run(plugin._due_ticks())  # 记基线
    plugin._last_tick_at = time.time() - 7.0
    assert await_due(plugin) == 0
    plugin._last_tick_at = time.time() - 9.0
    assert await_due(plugin) == 1


def test_heartbeat_respects_infinite_thirty_second_interval(plugin) -> None:
    """无限模式 30 秒/tick：间隔从配置读，不写死。"""
    _value(run(plugin.join(mode="infinite", seed=13)))
    run(plugin._due_ticks())
    plugin._last_tick_at = time.time() - 29.0
    assert await_due(plugin) == 0
    plugin._last_tick_at = time.time() - 31.0
    assert await_due(plugin) == 1


def test_heartbeat_catchup_is_capped_at_three(plugin) -> None:
    """离线/休眠后最多补 3 个 tick，不允许一口气补几十个。"""
    _value(run(plugin.join(seed=14)))
    run(plugin._due_ticks())
    plugin._last_tick_at = time.time() - 10_000.0
    assert await_due(plugin) == 3


def test_heartbeat_without_active_game_does_nothing(plugin) -> None:
    """没有进行中的对局时，心跳什么都不做。"""
    assert await_due(plugin) == 0


def test_heartbeat_entry_is_registered(plugin) -> None:
    """``market_tick`` 必须真的带 ``@timer_interval`` 的元数据。"""
    from plugin.sdk import EVENT_META_ATTR

    meta = getattr(type(plugin).market_tick, EVENT_META_ATTR, None)
    assert meta is not None
    assert meta.event_type == "timer"
    assert meta.id == "market_tick"
    assert meta.auto_start is True
    assert meta.extra["seconds"] == 1


# ---------------------------------------------------------------- tick 流水线


def test_tick_advances_the_market(plugin) -> None:
    """跑一个 tick，行情推进一格。"""
    _value(run(plugin.join(seed=21)))
    before = _value(run(plugin.market()))
    run(plugin._run_one_tick())
    after = _value(run(plugin.market()))
    assert after["tick"] == before["tick"] + 1
    assert after["stocks"] != before["stocks"]


def test_tick_persists_heat_history(plugin) -> None:
    """每个 tick 落一条 heat 采样。"""
    _value(run(plugin.join(seed=22)))
    run(plugin._run_one_tick())
    repo = Repo(plugin.db)
    row = run(repo.fetch_one("SELECT COUNT(*) AS n FROM heat_history"))
    assert row["n"] == 1


def test_tick_is_idempotent_against_reentry(plugin) -> None:
    """tick 正在跑时，重复触发会被跳过（防重入）。"""
    _value(run(plugin.join(seed=23)))
    plugin._tick_running = True
    before = _value(run(plugin.market()))["tick"]
    run(plugin._run_one_tick())
    assert _value(run(plugin.market()))["tick"] == before
    plugin._tick_running = False


def test_state_machine_reaches_trap_then_slaughter(plugin) -> None:
    """街机档按成交笔数触发：HONEYMOON → TRAP → SLAUGHTER。"""
    _value(run(plugin.join(mode="classic_arcade", seed=24)))
    repo = Repo(plugin.db)
    row = run(repo.fetch_one("SELECT player_id FROM market_state WHERE id = 1"))
    player_id = row["player_id"]

    run(repo.transaction([("UPDATE players SET trade_count = 30 WHERE id = ?", (player_id,))]))
    run(plugin._run_one_tick())
    assert _value(run(plugin.market()))["phase"] == "trap"

    run(repo.transaction([("UPDATE players SET trade_count = 60 WHERE id = ?", (player_id,))]))
    run(plugin._run_one_tick())
    assert _value(run(plugin.market()))["phase"] == "slaughter"


def test_day_advances_after_ticks_per_day_ticks(plugin) -> None:
    """经典档一天 12 个 tick，走满一天后 ``player.day`` +1。"""
    _value(run(plugin.join(mode="classic_beginner", seed=25)))
    for _ in range(12):
        run(plugin._run_one_tick())
    status = _value(run(plugin.status()))
    assert status["day"] == 2
    assert _value(run(plugin.market()))["day"] == 2


def test_tick_survives_when_database_is_unavailable(plugin) -> None:
    """数据库不可用时 tick 安静跳过，不抛异常、不中断定时器。"""
    _value(run(plugin.join(seed=26)))
    plugin.db.enabled = False
    run(plugin._run_one_tick())  # 不抛异常即通过
    plugin.db.enabled = True


def test_tick_increments_market_state_row(plugin) -> None:
    """market_state 的 tick 跟着走（它是可复现的锚点）。"""
    _value(run(plugin.join(seed=27)))
    run(plugin._run_one_tick())
    repo = Repo(plugin.db)
    row = run(repo.fetch_one("SELECT tick FROM market_state WHERE id = 1"))
    assert row["tick"] == 1
