"""P4 · 高利贷单测。

规则：

* ``daily(action="loan")`` 借 500，**次日**还 600；
* 每天最多借一次（``loan_already_taken``）；
* 有未还的贷款时不能再借（``loan_pending``）；
* 到期还不上 → ``loan_default`` 斩杀。
"""

from __future__ import annotations

from conftest import run
from engine.models import Player
from engine.settlement import daily_settlement
from plugins.mock_stock_meow.storage.repo import Repo


def _error_code(result) -> str:
    assert result.is_err(), f"期望 Err，实际 {result!r}"
    return result.err().code


def _settle(player: Player, **kwargs):
    params = {
        "mode": "infinite",
        "living_cost": 0.0,
        "next_day_living_cost": 0.0,
        "strike_limit": 3,
        "weekly_relief": 1050.0,
        "relief_interval_days": 7,
        "welfare_enabled": False,
        "welfare_claim_limit_days": 7,
        "loan_enabled": True,
    }
    params.update(kwargs)
    result = daily_settlement(player, **params)
    player.day += 1
    return result


# ---------------------------------------------------------------- 引擎层


def test_loan_is_repaid_the_next_day() -> None:
    """借 500 → 次日自动扣 600（手里有 700，还完剩 100）。"""
    player = Player(id="p1", name="玩家", cash=700.0, mode="infinite")
    player.loan_debt = 600.0
    player.loan_taken_day = 1
    player.day = 2

    result = _settle(player)
    assert result.loan_repaid == 600.0
    assert result.loan_default is False
    assert player.loan_debt == 0.0
    assert player.cash == 100.0


def test_loan_is_not_due_on_the_same_day() -> None:
    """当天借的当天不还（次日才到期）。"""
    player = Player(id="p1", name="玩家", cash=500.0, mode="infinite")
    player.loan_debt = 600.0
    player.loan_taken_day = 1
    player.day = 1

    result = _settle(player)
    assert result.loan_repaid == 0.0
    assert player.loan_debt == 600.0
    assert player.cash == 500.0


def test_loan_default_kills_the_player() -> None:
    """到期还不上 → ``loan_default`` 斩杀。"""
    player = Player(id="p1", name="玩家", cash=100.0, mode="infinite")
    player.loan_debt = 600.0
    player.loan_taken_day = 1
    player.day = 2

    result = _settle(player)
    assert result.loan_default is True
    assert result.dead is True
    assert player.death_reason == "loan_default"
    assert player.death_title


# ---------------------------------------------------------------- 插件层


def test_loan_action_grants_five_hundred(plugin) -> None:
    """借 500 到手，负债记 600，明天还。"""
    run(plugin.join(mode="infinite", seed=401))
    payload = run(plugin.daily(action="loan")).unwrap()
    assert payload["granted"] == 500.0
    assert payload["repay"] == 600.0
    assert payload["cash_after"] == 500.0
    assert payload["loan_debt"] == 600.0
    assert payload["due_day"] == 2


def test_loan_twice_in_one_day_is_rejected(plugin) -> None:
    """每天最多借一次。"""
    run(plugin.join(mode="infinite", seed=402))
    run(plugin.daily(action="loan"))
    assert _error_code(run(plugin.daily(action="loan"))) == "loan_already_taken"


def test_loan_is_blocked_while_a_loan_is_outstanding(plugin) -> None:
    """上一笔没还上 → 不许再借。"""
    run(plugin.join(mode="infinite", seed=403))
    repo = Repo(plugin.db)
    run(plugin.daily(action="loan"))
    # 把领取日改掉，模拟"隔了一天但还没还"
    run(
        repo.transaction(
            [
                (
                    "UPDATE players SET loan_taken_day = 0, loan_debt = 600 WHERE is_catgirl = 0",
                    (),
                )
            ]
        )
    )
    assert _error_code(run(plugin.daily(action="loan"))) == "loan_pending"


def test_loan_is_persisted(plugin) -> None:
    """借款状态落库。"""
    run(plugin.join(mode="infinite", seed=404))
    repo = Repo(plugin.db)
    run(plugin.daily(action="loan"))
    row = run(
        repo.fetch_one(
            "SELECT cash, loan_debt, loan_taken_day FROM players WHERE is_catgirl = 0"
        )
    )
    assert row["cash"] == 500.0
    assert row["loan_debt"] == 600.0
    assert row["loan_taken_day"] == 1


def test_status_exposes_loan_debt(plugin) -> None:
    """``status`` 里能看到高利贷余额。"""
    run(plugin.join(mode="infinite", seed=405))
    payload = run(plugin.status()).unwrap()
    assert payload["loan_debt"] == 0.0
    run(plugin.daily(action="loan"))
    payload = run(plugin.status()).unwrap()
    assert payload["loan_debt"] == 600.0


def test_classic_mode_has_no_loan(plugin) -> None:
    """经典模式没有高利贷。"""
    run(plugin.join(mode="classic_beginner", seed=406))
    payload = run(plugin.daily(action="loan")).unwrap()
    assert payload["granted"] == 0.0


def test_settle_reports_loan_summary(plugin) -> None:
    """结算报告里有高利贷小结。"""
    run(plugin.join(mode="infinite", seed=407))
    run(plugin.daily(action="loan"))
    payload = run(plugin.settle()).unwrap()
    assert payload["loan_summary"]["available"] is True
    assert payload["loan_summary"]["outstanding"] == 600.0
    assert payload["loan_summary"]["borrowed"] is True
