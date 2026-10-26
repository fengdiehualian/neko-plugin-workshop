"""P4 · 低保（主动领取 / 补领 / 斩杀线）单测。

规则：

* 低保**必须主动领**（``daily(action="claim")``），不领不到账；
* 允许补领：一次补领全部欠款，**上限 7 天**，更早的欠款作废；
* 领取是**周期内幂等**的：同一个发放周期重复领会报 ``welfare_already_claimed``；
* 连续未领的天数超过上限 → 第 8 天日终 ``welfare_neglect`` 斩杀。
"""

from __future__ import annotations

from conftest import run
from engine.models import Player
from engine.settlement import (
    claim_welfare,
    daily_settlement,
    welfare_cycle,
    welfare_entitlement,
)
from plugins.mock_stock_meow.storage.repo import Repo


def _error_code(result) -> str:
    assert result.is_err(), f"期望 Err，实际 {result!r}"
    return result.err().code


def _settle(player: Player, **kwargs):
    """跑一次无限模式的日终结算（参数有默认值）。"""
    params = {
        "mode": "infinite",
        "living_cost": 0.0,
        "next_day_living_cost": 0.0,
        "strike_limit": 3,
        "weekly_relief": 1050.0,
        "relief_interval_days": 7,
        "welfare_enabled": True,
        "welfare_claim_limit_days": 7,
    }
    params.update(kwargs)
    result = daily_settlement(player, **params)
    player.day += 1
    return result


# ---------------------------------------------------------------- 引擎层


def test_welfare_cycle_boundaries() -> None:
    """第 1~7 天是第 1 个周期，第 8~14 天是第 2 个周期。"""
    assert welfare_cycle(1, 7) == 1
    assert welfare_cycle(7, 7) == 1
    assert welfare_cycle(8, 7) == 2
    assert welfare_cycle(14, 7) == 2
    assert welfare_cycle(15, 7) == 3


def test_claim_grants_a_full_week() -> None:
    """第一次领取 → 到账 1050（一整周），欠款计数归零。"""
    player = Player(id="p1", name="玩家", cash=0.0, mode="infinite")
    claim = claim_welfare(player, day=1, weekly_relief=1050.0)
    assert claim.granted == 1050.0
    assert claim.days_owed == 7
    assert claim.already_claimed is False
    assert player.cash == 1050.0
    assert player.welfare_missed_days == 0
    assert player.welfare_claimed_day == 1


def test_claim_is_idempotent_within_a_cycle() -> None:
    """同一周期内重复领 → 不再发钱，并标记 already_claimed。"""
    player = Player(id="p1", name="玩家", cash=0.0, mode="infinite")
    claim_welfare(player, day=1, weekly_relief=1050.0)
    again = claim_welfare(player, day=3, weekly_relief=1050.0)
    assert again.already_claimed is True
    assert again.granted == 0.0
    assert player.cash == 1050.0


def test_claim_in_the_next_cycle_grants_again() -> None:
    """跨周期再领 → 又是一整周。"""
    player = Player(id="p1", name="玩家", cash=0.0, mode="infinite")
    claim_welfare(player, day=1, weekly_relief=1050.0)
    second = claim_welfare(player, day=8, weekly_relief=1050.0)
    assert second.already_claimed is False
    assert second.granted == 1050.0
    assert player.cash == 2100.0


def test_backfill_covers_the_missed_cycle() -> None:
    """漏掉一个周期后补领 → 补回那一整份低保。

    低保是**按周期发放**的（一个周期 = 一份 1050），补领也是按周期补：
    漏了几天不影响额度，超过一个周期的欠款作废。
    """
    player = Player(id="p1", name="玩家", cash=0.0, mode="infinite")
    claim_welfare(player, day=1, weekly_relief=1050.0)
    # 第 2~7 天没领（还在第 1 个周期内）→ 第 8 天领还是同一个周期
    assert welfare_entitlement(
        player, day=7, interval_days=7, claim_limit_days=7
    ) == 0
    # 第 8 天进入第 2 个周期 → 可以补领一份
    claim = claim_welfare(player, day=8, weekly_relief=1050.0)
    assert claim.already_claimed is False
    assert claim.granted == 1050.0
    assert player.cash == 2100.0


def test_backfill_is_capped_at_seven_days() -> None:
    """欠款超过 7 天 → 更早的欠款作废。"""
    player = Player(id="p1", name="玩家", cash=0.0, mode="infinite")
    player.welfare_claimed_day = 1
    player.welfare_missed_days = 30
    cycles = welfare_entitlement(
        player, day=30, interval_days=7, claim_limit_days=7
    )
    assert cycles == 1  # 最多补一个周期


def test_missed_days_increment_at_daily_settlement() -> None:
    """不领 → 日终结算时欠款 +1。"""
    player = Player(id="p1", name="玩家", cash=0.0, mode="infinite")
    for expected in (1, 2, 3):
        result = _settle(player)
        assert result.welfare_missed_days == expected
        assert player.welfare_missed_days == expected


def test_claiming_resets_the_missed_counter() -> None:
    """领了 → 欠款归零。"""
    player = Player(id="p1", name="玩家", cash=0.0, mode="infinite")
    _settle(player)
    _settle(player)
    assert player.welfare_missed_days == 2
    claim_welfare(player, day=player.day, weekly_relief=1050.0)
    assert player.welfare_missed_days == 0


def test_seven_missed_days_kills_on_the_eighth() -> None:
    """连续 7 天不领 → 第 8 天日终 ``welfare_neglect`` 斩杀。"""
    player = Player(id="p1", name="玩家", cash=100000.0, mode="infinite")
    for _ in range(7):
        result = _settle(player)
        assert result.dead is False
        assert result.welfare_missed_days <= 7
    # 第 8 天
    result = _settle(player)
    assert result.welfare_missed_days == 8
    assert result.dead is True
    assert player.death_reason == "welfare_neglect"
    assert player.death_title


def test_welfare_kill_does_not_beat_loan_default() -> None:
    """破产原因优先级：loan_default > welfare_neglect。"""
    player = Player(id="p1", name="玩家", cash=0.0, mode="infinite")
    player.day = 2
    player.loan_debt = 600.0
    player.loan_taken_day = 1  # 第 1 天借的，第 2 天到期
    player.welfare_missed_days = 7
    result = _settle(player, loan_enabled=True)
    assert result.loan_default is True
    assert result.death_reason == "loan_default"


# ---------------------------------------------------------------- 插件层


def test_claim_action_is_not_window_restricted(plugin) -> None:
    """领低保任何时候都能领（不受撤销窗口限制）。"""
    run(plugin.join(mode="infinite", seed=301))
    payload = run(plugin.daily(action="claim")).unwrap()
    assert payload["granted"] == 1050.0
    assert payload["cash_after"] == 1050.0
    assert payload["welfare_missed_days"] == 0


def test_claim_action_twice_reports_already_claimed(plugin) -> None:
    """同一周期重复领 → ``welfare_already_claimed``。"""
    run(plugin.join(mode="infinite", seed=302))
    run(plugin.daily(action="claim"))
    assert _error_code(run(plugin.daily(action="claim"))) == "welfare_already_claimed"


def test_classic_mode_has_no_welfare(plugin) -> None:
    """经典模式没有低保（领了也不给钱，但**不报错**）。"""
    run(plugin.join(mode="classic_beginner", seed=303))
    payload = run(plugin.daily(action="claim")).unwrap()
    assert payload["granted"] == 0.0


def test_status_exposes_welfare_state(plugin) -> None:
    """``status`` 里能看到欠款天数与现在能领回多少。"""
    run(plugin.join(mode="infinite", seed=304))
    payload = run(plugin.status()).unwrap()
    assert payload["welfare_missed_days"] == 0
    assert payload["pending_relief"] == 1050.0


def test_welfare_claim_is_persisted(plugin) -> None:
    """领取状态落库（重启后仍然记得领过）。"""
    run(plugin.join(mode="infinite", seed=305))
    repo = Repo(plugin.db)
    run(plugin.daily(action="claim"))
    row = run(
        repo.fetch_one(
            "SELECT cash, welfare_missed_days, welfare_claimed_day FROM players "
            "WHERE is_catgirl = 0"
        )
    )
    assert row["cash"] == 1050.0
    assert row["welfare_missed_days"] == 0
    assert row["welfare_claimed_day"] == 1
