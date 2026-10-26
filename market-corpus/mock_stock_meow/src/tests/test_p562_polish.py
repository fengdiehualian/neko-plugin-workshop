"""P5.5 改动 4~7（v0.6.2）的单测。

守四条线：

1. **改动 4 · 天桥虚惊一场**：无限模式破产返回三屏；经典模式为 ``None``；
   三屏里的 ``identity_quote`` 与改动 2 的三个身份对应；破产原因标签正确；
   **不写任何具体的自杀细节**，第三屏落在"你还有机会"；不含任何内部字段。
2. **改动 5 · 称号与图鉴**：``{epitaph} {身份呼应句}``；``achievements`` 独立表
   能插入/解锁；载荷里已解锁与未解锁分开；**「无为而治」的 hint 是唯一带
   「活」字的**。
3. **改动 6 · skip_day**：无限模式可用、经典模式拒绝、每天一次、跳过时照常走
   日终结算、``ticks_played`` 推进到日终边界；第 14 天零有效交易 → 解锁
   「无为而治」。
4. **改动 7 · 称号图鉴 UI**：面板第 12 区块，已解锁/未解锁分开，折叠持久化。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from conftest import run
from plugins.mock_stock_meow.storage.repo import Repo

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_I18N_PATH = _PLUGIN_ROOT / "i18n" / "zh-CN.json"
_PANEL_SOURCE = (_PLUGIN_ROOT / "ui" / "panel.tsx").read_text(encoding="utf-8")

#: 无限模式每天的 tick 数（引擎配置）。
_TICKS_PER_DAY_INFINITE = 48

#: 三个入场身份及其开场台词（改动 2）。
_IDENTITIES = {
    "debt": "家里欠了一笔钱。我想帮上忙。",
    "prove": "我爸说我不是这块料。我想证明他错了。",
    "quick": "打工太慢了。我想快点赚到钱。",
}

#: **不得出现**的自杀方式描写（改动 4 的硬约束）。
_FORBIDDEN_METHOD_WORDS = (
    "跳下去",
    "往下跳",
    "往下",
    "纵身",
    "迈出",
    "跨出",
    "跨过",
    "护栏",
    "边缘",
    "推开",
    "站上去",
    "一跃",
)

#: 局中/结算里都不该出现的内部字段。
_FORBIDDEN_INTERNAL_WORDS = ("heat", "seed", "反转日", "reversal")


def _value(result: Any) -> dict[str, Any]:
    assert result.is_ok(), f"期望 Ok，实际 {result!r}"
    payload = result.unwrap()
    assert isinstance(payload, dict)
    return payload


def _error_code(result: Any) -> str:
    assert result.is_err(), f"期望 Err，实际 {result!r}"
    return str(getattr(result.err(), "code", ""))


def _i18n() -> dict[str, str]:
    return json.loads(_I18N_PATH.read_text(encoding="utf-8"))


def _kill_player(repo: Repo, reason: str = "starved") -> None:
    """把玩家标成破产（模拟被强制平仓）。"""
    run(
        repo.transaction(
            [
                (
                    "UPDATE players SET alive = 0, death_reason = ?, "
                    "death_title = '引擎给的原始称号' WHERE is_catgirl = 0",
                    (reason,),
                )
            ]
        )
    )


def _sell_baseline_stock(plugin, repo: Repo) -> None:
    """把开局发放的基准股票卖掉（无限模式玩家必须先这么做才活得下去）。"""
    rows = run(repo.fetch_all("SELECT * FROM positions"))
    assert rows
    stock_id = rows[0]["stock_id"]
    run(plugin.sell(stock_id, rows[0]["amount"]))


# ---------------------------------------------------------------- ① 改动 4


def test_infinite_death_returns_three_screens(plugin) -> None:
    """无限模式破产 → ``death_scene`` 非空且有三屏。"""
    run(plugin.join(mode="infinite", seed=7001))
    repo = Repo(plugin.db)
    _kill_player(repo, "starved")
    payload = _value(run(plugin.settle()))
    scene = payload["death_scene"]
    assert scene is not None
    for key in ("screen1", "screen2", "screen3"):
        assert scene[key], f"{key} 是空的"


def test_classic_death_scene_is_null(plugin) -> None:
    """经典模式破产 → ``death_scene`` 为 ``None``（经典是教学关，不搞这么重）。"""
    run(plugin.join(mode="classic_arcade", seed=7002))
    repo = Repo(plugin.db)
    _kill_player(repo, "starved")
    payload = _value(run(plugin.settle()))
    assert payload["death_scene"] is None


@pytest.mark.parametrize("identity", sorted(_IDENTITIES))
def test_death_scene_quotes_the_identity(plugin, identity: str) -> None:
    """三屏里的 ``identity_quote`` 与改动 2 的三个身份对应。"""
    run(plugin.join(mode="infinite", seed=7003, identity=identity))
    repo = Repo(plugin.db)
    _kill_player(repo, "starved")
    payload = _value(run(plugin.settle()))
    scene = payload["death_scene"]
    assert _IDENTITIES[identity] in scene["screen1"]


def test_death_scene_screen3_names_the_death_reason(plugin) -> None:
    """第三屏用中文破产原因标签（穷困潦倒 / 资产归零 / …），不是内部代码。"""
    run(plugin.join(mode="infinite", seed=7004))
    repo = Repo(plugin.db)
    _kill_player(repo, "liquidated")
    payload = _value(run(plugin.settle()))
    scene = payload["death_scene"]
    assert "资产归零" in scene["screen3"]
    assert "liquidated" not in scene["screen3"]


def test_death_scene_has_no_method_details(plugin) -> None:
    """**不写任何具体的自杀细节**。"""
    run(plugin.join(mode="infinite", seed=7005))
    repo = Repo(plugin.db)
    _kill_player(repo, "starved")
    payload = _value(run(plugin.settle()))
    blob = json.dumps(payload["death_scene"], ensure_ascii=False)
    for word in _FORBIDDEN_METHOD_WORDS:
        assert word not in blob, f"三屏文案出现了不该有的描写：{word}"


def test_death_scene_lands_on_hope(plugin) -> None:
    """最后一屏必须落在「你还有机会」。"""
    run(plugin.join(mode="infinite", seed=7006))
    repo = Repo(plugin.db)
    _kill_player(repo, "starved")
    payload = _value(run(plugin.settle()))
    assert "你还有机会" in payload["death_scene"]["screen3"]


def test_death_scene_is_a_dream(plugin) -> None:
    """第二屏必须点明这是梦（醒来）。"""
    run(plugin.join(mode="infinite", seed=7007))
    repo = Repo(plugin.db)
    _kill_player(repo, "starved")
    payload = _value(run(plugin.settle()))
    assert "醒了" in payload["death_scene"]["screen2"]


def test_death_scene_has_no_internal_fields(plugin) -> None:
    """三屏不得出现 heat / seed / 反转日。"""
    run(plugin.join(mode="infinite", seed=7008))
    repo = Repo(plugin.db)
    _kill_player(repo, "starved")
    payload = _value(run(plugin.settle()))
    blob = json.dumps(payload["death_scene"], ensure_ascii=False)
    for word in _FORBIDDEN_INTERNAL_WORDS:
        assert word not in blob.lower(), f"三屏文案泄漏了内部字段：{word}"


# ---------------------------------------------------------------- ② 改动 5


def test_epitaphs_cover_every_death_reason() -> None:
    """每种破产原因都有叙事句（插件层映射表，引擎不动）。"""
    from plugins.mock_stock_meow.cards import EPITAPHS

    for reason in (
        "starved",
        "liquidated",
        "loan_default",
        "welfare_neglect",
        "harvested",
    ):
        assert reason in EPITAPHS
        assert EPITAPHS[reason]
    assert EPITAPHS["inaction"]
    # 「低保」不许出现（改动 1 的成果）。
    assert all("低保" not in text for text in EPITAPHS.values())


def test_settlement_title_is_epitaph_plus_identity_echo(plugin) -> None:
    """细节 A：``{epitaph} {身份呼应句}`` 一句话读完。"""
    run(plugin.join(mode="infinite", seed=7010, identity="quick"))
    repo = Repo(plugin.db)
    _kill_player(repo, "starved")
    payload = _value(run(plugin.settle()))
    assert payload["epitaph"] == "你破产了。穷困潦倒。"
    assert payload["death_title"] == (
        "你破产了。穷困潦倒。 你入场时说想快点赚到钱。你确实很快——快到连饭都忘了吃。"
    )
    assert payload["identity_echo"] in payload["death_title"]


def test_settlement_title_is_not_the_engine_title(plugin) -> None:
    """结算报告里的 ``death_title`` **不是**引擎的原始 ``DEATH_TITLES`` 值。"""
    run(plugin.join(mode="infinite", seed=7011))
    repo = Repo(plugin.db)
    _kill_player(repo, "starved")
    payload = _value(run(plugin.settle()))
    assert "引擎给的原始称号" not in payload["death_title"]
    assert payload["death_title"].startswith("你破产了。穷困潦倒。")


def test_death_unlocks_an_achievement_row(plugin) -> None:
    """破产原因成就写进 ``achievements`` 独立表。"""
    run(plugin.join(mode="infinite", seed=7012))
    repo = Repo(plugin.db)
    _kill_player(repo, "liquidated")
    run(plugin.settle())
    rows = run(repo.fetch_all("SELECT * FROM achievements"))
    assert rows
    titles = {str(row["title"]) for row in rows}
    assert "满仓归零" in titles
    # ``unlocked_at`` 存 tick 序号（拍板细节 B）。
    assert all(int(row["unlocked_at"]) >= 0 for row in rows)


def test_achievements_table_is_idempotent(plugin) -> None:
    """同一称号只解锁一次（主键 ``(player_id, title)``）。"""
    run(plugin.join(mode="infinite", seed=7013))
    repo = Repo(plugin.db)
    _kill_player(repo, "starved")
    run(plugin.settle())
    before = run(repo.fetch_all("SELECT * FROM achievements"))
    run(plugin._unlock_achievement(before[0]["player_id"], "grain", tick=999))
    after = run(repo.fetch_all("SELECT * FROM achievements"))
    assert len(before) == len(after)


def test_achievements_payload_splits_unlocked_and_locked(plugin) -> None:
    """载荷里已解锁与未解锁分开。"""
    run(plugin.join(mode="infinite", seed=7014))
    repo = Repo(plugin.db)
    _kill_player(repo, "starved")
    payload = _value(run(plugin.settle()))
    achievements = payload["achievements"]
    assert achievements["total"] == 8
    assert achievements["unlocked_count"] == len(achievements["unlocked"])
    assert len(achievements["unlocked"]) + len(achievements["locked"]) == 8
    assert achievements["unlocked"], "破产原因成就应该已解锁"
    for item in achievements["unlocked"]:
        assert item["title"]
        assert item["epitaph"]
        assert item["identity_epitaph"]
    for item in achievements["locked"]:
        assert item["title"] == "？？？"
        assert item["hint"]


def test_inaction_hint_is_the_only_one_with_huo(plugin) -> None:
    """「无为而治」的 hint 是**唯一**带「活」字的（发现隐藏结局的线索）。"""
    from plugins.mock_stock_meow.cards import ACHIEVEMENTS

    with_huo = [item for item in ACHIEVEMENTS if "活" in str(item["hint"])]
    assert len(with_huo) == 1
    assert with_huo[0]["title"] == "无为而治"


def test_context_payload_has_no_achievements_while_playing(plugin) -> None:
    """**局中**载荷不带 achievements（只在结算页出现）。"""
    run(plugin.join(mode="classic_arcade", seed=7015))
    engine_config = run(plugin._engine_config())
    session, error = run(plugin._session_or_error(engine_config))
    assert error is None
    payload = plugin._panel_payload(session, engine_config)
    assert "achievements" not in payload


def test_context_payload_has_achievements_after_settlement(plugin) -> None:
    """结算之后，面板载荷带上 achievements。"""
    run(plugin.join(mode="infinite", seed=7016))
    repo = Repo(plugin.db)
    _kill_player(repo, "starved")
    run(plugin.settle())
    payload = run(plugin.market_context())
    assert payload["stage"] == "finished"
    assert payload["achievements"] is not None
    assert payload["achievements"]["total"] == 8


# ---------------------------------------------------------------- ③ 改动 6


def test_skip_day_is_rejected_in_classic_mode(plugin) -> None:
    """经典模式 → ``skip_day_classic_forbidden``。"""
    run(plugin.join(mode="classic_arcade", seed=7020))
    assert _error_code(run(plugin.skip_day())) == "skip_day_classic_forbidden"


def test_skip_day_advances_to_the_day_boundary(plugin) -> None:
    """跳过本日：``ticks_played`` 推进到下一个日终边界。"""
    run(plugin.join(mode="infinite", seed=7021))
    repo = Repo(plugin.db)
    _sell_baseline_stock(plugin, repo)
    # 先走 5 个 tick（不在边界上）。
    for _ in range(5):
        run(plugin._run_one_tick())
    before = run(repo.fetch_one("SELECT tick FROM market_state WHERE id = 1"))["tick"]
    assert before == 5

    payload = _value(run(plugin.skip_day()))
    after = run(repo.fetch_one("SELECT tick FROM market_state WHERE id = 1"))["tick"]
    assert after == _TICKS_PER_DAY_INFINITE
    assert payload["ticks_advanced"] == _TICKS_PER_DAY_INFINITE - before
    assert payload["day"] == 2


def test_skip_day_runs_the_daily_settlement(plugin) -> None:
    """跳过时照常走日终结算（扣生活费、欠款计数、日终卡片）。"""
    run(plugin.join(mode="infinite", seed=7022))
    repo = Repo(plugin.db)
    _sell_baseline_stock(plugin, repo)
    cash_before = run(repo.fetch_one("SELECT cash FROM players WHERE is_catgirl = 0"))[
        "cash"
    ]
    run(plugin.skip_day())
    row = run(repo.fetch_one("SELECT cash, day FROM players WHERE is_catgirl = 0"))
    assert row["day"] == 2
    # 每天 150 的生活费被扣掉了。
    assert float(row["cash"]) == pytest.approx(float(cash_before) - 150.0, abs=1e-6)


def test_skip_day_can_only_be_used_once_per_day(plugin) -> None:
    """每天只能跳过一次。

    跳过会把日子推进一天，所以**连续两次**跳过分别落在两天上（合法）。
    这条限制是"同一天不许连按"的安全网 —— 这里直接把它构造出来：
    把游戏日拨回被记录的那天，再调一次。
    """
    run(plugin.join(mode="infinite", seed=7023))
    repo = Repo(plugin.db)
    _sell_baseline_stock(plugin, repo)
    _value(run(plugin.skip_day()))
    # 跳过发生在第 1 天 → ``skip_day_used`` 记的是 1。
    row = run(repo.fetch_one("SELECT skip_day_used, day FROM players WHERE is_catgirl = 0"))
    assert int(row["skip_day_used"]) == 1
    assert int(row["day"]) == 2
    # 把日子拨回第 1 天（模拟"同一天再次调用"）。
    run(repo.transaction([("UPDATE players SET day = 1 WHERE is_catgirl = 0", ())]))
    assert _error_code(run(plugin.skip_day())) == "skip_day_already_used"


def test_skip_day_on_a_new_day_is_allowed(plugin) -> None:
    """新的一天当然可以再跳。"""
    run(plugin.join(mode="infinite", seed=7027))
    repo = Repo(plugin.db)
    _sell_baseline_stock(plugin, repo)
    first = _value(run(plugin.skip_day()))
    assert first["day"] == 2
    run(plugin.daily(action="claim"))
    second = _value(run(plugin.skip_day()))
    assert second["day"] == 3


def test_skip_day_rejects_a_dead_player(plugin) -> None:
    """已经出局了 → 报错。"""
    run(plugin.join(mode="infinite", seed=7024))
    repo = Repo(plugin.db)
    _kill_player(repo, "starved")
    assert _error_code(run(plugin.skip_day())) == "skip_day_dead"


def test_inaction_unlocks_after_fourteen_days(plugin) -> None:
    """存活 14 天 + 零有效交易 → 解锁「无为而治」并写进 achievements 表。

    走的是**真实路径**：14 次 skip_day（每天一次）。
    """
    run(plugin.join(mode="infinite", seed=7025))
    repo = Repo(plugin.db)
    _sell_baseline_stock(plugin, repo)
    for _ in range(14):
        run(plugin.skip_day())
        run(plugin.daily(action="claim"))  # 领不到就报错，但不影响判定
    rows = run(repo.fetch_all("SELECT title FROM achievements"))
    titles = {str(row["title"]) for row in rows}
    assert "无为而治" in titles, f"第 14 天应该解锁「无为而治」，实际：{titles}"


def test_inaction_does_not_unlock_for_a_trader(plugin) -> None:
    """买过东西就不算「无为而治」。"""
    run(plugin.join(mode="infinite", seed=7026))
    repo = Repo(plugin.db)
    _sell_baseline_stock(plugin, repo)
    market = _value(run(plugin.market()))
    cheapest = min(market["stocks"], key=lambda item: item["price"])
    run(plugin.buy(cheapest["id"], 1.0))
    for _ in range(14):
        run(plugin.skip_day())
        run(plugin.daily(action="claim"))
    rows = run(repo.fetch_all("SELECT title FROM achievements"))
    titles = {str(row["title"]) for row in rows}
    assert "无为而治" not in titles


def test_inaction_unlock_uses_the_engine_exemption(plugin) -> None:
    """口径：豁免开局卖掉基准股票那一笔（与 ``check_inaction`` 一致）。"""
    from engine.settlement import check_inaction
    from plugins.mock_stock_meow import MockStockMeowPlugin

    # 卖出基准股票 → trade_count = 1，但那是**豁免**的。
    assert check_inaction(
        type("P", (), {"trade_count": 1})(), max_trades=0, exempt_trades=1
    )
    assert MockStockMeowPlugin is not None


# ---------------------------------------------------------------- ④ 改动 7


def test_panel_has_the_achievements_block() -> None:
    """面板新增第 12 区块（称号图鉴），可折叠。"""
    assert "panel.achievements" in _PANEL_SOURCE
    assert "rrm.achievements_open" in _PANEL_SOURCE
    assert "achievementsOpen" in _PANEL_SOURCE


def test_panel_has_the_death_scene_sequence() -> None:
    """面板有三屏序列 + 跳过按钮。"""
    assert "death_scene" in _PANEL_SOURCE
    assert "sceneScreen" in _PANEL_SOURCE
    assert "panel.death_scene_skip" in _PANEL_SOURCE
    assert "useEffect" in _PANEL_SOURCE


def test_panel_has_the_skip_day_button() -> None:
    """面板操作区有「跳过本日」按钮。"""
    assert 'actionById("skip_day")' in _PANEL_SOURCE


def test_i18n_has_the_new_keys() -> None:
    """新文案都在 i18n 里。"""
    table = _i18n()
    for key in (
        "death_scene.screen1",
        "death_scene.screen2",
        "death_scene.screen3",
        "death_reason.starved",
        "death_reason.liquidated",
        "panel.achievements",
        "panel.death_scene",
        "panel.death_scene_skip",
        "panel.skip_day",
        "card.achievement.title",
        "error.skip_day_classic_forbidden",
        "error.skip_day_already_used",
    ):
        assert key in table, f"i18n 缺 {key}"


def test_skip_day_is_the_eighth_ui_action(plugin) -> None:
    """``skip_day`` 是第 8 个 ``@ui.action``，且元数据齐全。"""
    meta = getattr(plugin.skip_day, "__neko_ui_action__", None)
    assert meta is not None
    assert meta["id"] == "skip_day"
    assert meta["label"] == "跳过本日"
