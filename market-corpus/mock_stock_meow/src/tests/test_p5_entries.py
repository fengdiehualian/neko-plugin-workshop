"""P5 新入口与面板载荷的单测。

守六条线：

1. **7 个 ``@ui.action`` 都在**，且**包住同一个** ``@plugin_entry``（源码扫描：
   ``@ui.action`` 必须在 ``@plugin_entry`` **上面一行** —— 宿主要求它是最外层）。
2. ``analyze``：每天 2 次机会、用完报错、隔天重置。
3. ``analyze`` **不泄漏方向**（细节 5）：返回体里不得出现 bullish / bearish /
   反转 / 利好 / 利空 这类词。
4. ``news_detail``：给标题与正文，但**永远不给真假**；找不到与不属于本局要分清。
5. 三个日终薄入口的行为与 ``daily(action=...)`` 一致。
6. 面板载荷：字段齐全、**K 线限流**（细节 2）、不含局中秘密。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from conftest import run
from storage.repo import Repo

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_SOURCE = (_PLUGIN_ROOT / "__init__.py").read_text(encoding="utf-8")

#: P5 契约 §3.2 的 7 个 + P5.5 改动 6 的 ``skip_day`` + P6 档 2 的 ``kline`` = 9 个，
#: 再加 P7 的 4 个、P7.4 的 1 个、P10 的 4 个 = 17 个，再加 P11 的 2 个 = 19 个，
#: 再加 P12 的 ``story_choice`` = 20 个，再加 P18.0 的 3 个教程动作 = 23 个。
EXPECTED_ACTIONS = (
    "buy",
    "sell",
    "analyze",
    "daily_claim",
    "daily_loan",
    "daily_reject",
    "news_detail",
    "skip_day",
    "kline",
    # ── P7：导演台（特制版）与破产宣告 ────────────────────────────
    "director",
    "declare_bankrupt",
    # ── P7.3：网贷弹窗的唯一出口（0.8.2 面板死锁事故的修复）──────
    "loan_prompt",
    # ── P7.4：开局入口（面板空闲态的双按钮 + 聊天指令的落点）────
    "join",
    # ── P10：节奏偏好（游戏速度 / 自动·手动 / 推进一拍）与引导已读 ──
    "set_game_speed",
    "set_auto_tick",
    "advance_tick",
    "onboarding_ack",
    # ── P11：猫娘外置交互引导（首次招呼 / 呼叫猫娘）──────────────
    "meow_greet",
    "meow_call",
    # ── P12：剧情选项（聊天卡片按钮的回调入口）────────────────────
    "story_choice",
    # ── P18.0：分档次动态教程（开始 / 下一步 / 跳过）──────────────
    "tutorial_begin",
    "tutorial_advance",
    "tutorial_skip",
)

#: 局中**不能出现的方向词**（细节 5）。
FORBIDDEN_DIRECTION_WORDS = (
    "bullish",
    "bearish",
    "反转",
    "利好",
    "利空",
    "偏多",
    "偏空",
)


def _value(result: Any) -> dict[str, Any]:
    assert result.is_ok(), f"期望 Ok，实际 {result!r}"
    payload = result.unwrap()
    assert isinstance(payload, dict)
    return payload


def _error_code(result: Any) -> str:
    assert result.is_err(), f"期望 Err，实际 {result!r}"
    return str(result.error.code)


def _ui_action_meta(member: Any) -> dict[str, Any] | None:
    """和宿主 ``core/host.py`` 一样：沿 ``__wrapped__`` 链找 UI action 元数据。"""
    candidates: list[Any] = [member]
    if hasattr(member, "__func__"):
        candidates.append(member.__func__)
    current = getattr(member, "__wrapped__", None)
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        candidates.append(current)
        if hasattr(current, "__func__"):
            candidates.append(current.__func__)
        current = getattr(current, "__wrapped__", None)
    for candidate in candidates:
        meta = getattr(candidate, "__neko_ui_action__", None)
        if isinstance(meta, dict):
            return meta
    return None


# ---------------------------------------------------------------- ① @ui.action


def test_all_seven_ui_actions_are_registered(plugin) -> None:
    """7 个 ``@ui.action`` 都有元数据，且 id 与入口 id 一致。"""
    for action_id in EXPECTED_ACTIONS:
        handler = getattr(plugin, action_id, None)
        assert handler is not None, f"缺少入口 {action_id}"
        meta = _ui_action_meta(handler)
        assert meta is not None, f"{action_id} 没有 @ui.action 元数据"
        assert meta["id"] == action_id


def test_ui_action_is_the_outermost_decorator() -> None:
    """源码扫描：``@ui.action`` 必须在 ``@plugin_entry`` **上面**（最外层）。

    装饰器自上而下书写，所以 ``@ui.action`` 的**下一层**装饰器必须是
    ``@plugin_entry``。
    """
    lines = _SOURCE.splitlines()
    checked = 0
    for index, line in enumerate(lines):
        if not re.match(r"\s*@ui\.action\(", line):
            continue
        checked += 1
        indent = len(line) - len(line.lstrip())
        # 跳过 @ui.action(...) 的参数块，找下一个同缩进的装饰器。
        cursor = index + 1
        while cursor < len(lines):
            current = lines[cursor]
            stripped = current.strip()
            if stripped.startswith("@") and (len(current) - len(current.lstrip())) == indent:
                break
            cursor += 1
        assert cursor < len(lines), f"@ui.action 后面没有装饰器（第 {index + 1} 行）"
        assert "@plugin_entry(" in lines[cursor], (
            "@ui.action 必须是最外层：它的下一层装饰器必须是 @plugin_entry，"
            f"实际是 {lines[cursor].strip()!r}"
        )
    assert checked == len(EXPECTED_ACTIONS), f"只找到 {checked} 个 @ui.action"


def test_ui_action_ids_match_entry_ids(plugin) -> None:
    """宿主用 ``ui_meta["id"] or entry_id`` 作为 action id，两者必须一致。"""
    for action_id in EXPECTED_ACTIONS:
        meta = _ui_action_meta(getattr(plugin, action_id))
        assert meta is not None
        assert meta["id"] == action_id


def test_dangerous_actions_ask_for_confirmation(plugin) -> None:
    """借钱与拒绝二道贩子必须带 ``confirm``（且是**字符串**，不是 Mapping）。"""
    loan = _ui_action_meta(plugin.daily_loan)
    assert loan is not None
    assert isinstance(loan["confirm"], str) and loan["confirm"]
    reject = _ui_action_meta(plugin.daily_reject)
    assert reject is not None
    assert isinstance(reject["confirm"], str) and reject["confirm"]


# ---------------------------------------------------------------- ② analyze


def test_analyze_consumes_one_daily_chance(plugin) -> None:
    run(plugin.join(mode="classic_beginner", seed=9101))
    first = _value(run(plugin.analyze()))
    assert first["analyses_left"] == 1
    second = _value(run(plugin.analyze()))
    assert second["analyses_left"] == 0


def test_analyze_runs_out_of_chances(plugin) -> None:
    """每天 2 次；第 3 次返回 ``analysis_limit_reached``。"""
    run(plugin.join(mode="classic_beginner", seed=9102))
    run(plugin.analyze())
    run(plugin.analyze())
    assert _error_code(run(plugin.analyze())) == "analysis_limit_reached"


def test_analyze_chances_reset_on_a_new_day(plugin) -> None:
    """换一天，机会重置（计数器按天记）。"""
    run(plugin.join(mode="classic_beginner", seed=9103))
    run(plugin.analyze())
    run(plugin.analyze())
    assert _error_code(run(plugin.analyze())) == "analysis_limit_reached"
    # 把"上次分析的那天"清掉 = 模拟到了新的一天。
    run(
        Repo(plugin.db).transaction(
            [("UPDATE players SET analyses_day = 0, analyses_used = 0", ())]
        )
    )
    payload = _value(run(plugin.analyze()))
    assert payload["analyses_left"] == 1


def test_analyze_counter_is_persisted(plugin) -> None:
    """用掉的次数要落库（重启不丢）。"""
    run(plugin.join(mode="classic_beginner", seed=9104))
    run(plugin.analyze())
    row = run(Repo(plugin.db).fetch_one("SELECT analyses_used FROM players"))
    assert row is not None
    assert int(row["analyses_used"]) == 1


def test_analyze_never_leaks_direction(plugin) -> None:
    """细节 5：analyze 的返回体里不得出现方向词。"""
    run(plugin.join(mode="classic_beginner", seed=9105))
    payload = _value(run(plugin.analyze()))
    blob = repr(payload)
    for word in FORBIDDEN_DIRECTION_WORDS:
        assert word not in blob, f"analyze 泄漏了方向：{word}"
    assert "reversal" not in blob.lower()


def test_analyze_market_note_is_vague_and_directionless(plugin) -> None:
    """当天异动提示：只说"今天特殊"，**不说是哪种特殊**。"""
    run(plugin.join(mode="classic_beginner", seed=9106))
    payload = _value(run(plugin.analyze()))
    note = payload["market_note"]
    assert isinstance(note, str)
    for word in FORBIDDEN_DIRECTION_WORDS:
        assert word not in note
    assert "反转" not in note


def test_the_market_note_is_not_in_the_panel_payload(plugin) -> None:
    """异动提示**只**在 analyze 里 —— 面板载荷里没有（否则"有成本"就没了）。"""
    run(plugin.join(mode="classic_beginner", seed=9107))
    payload = run(plugin.market_context())
    assert isinstance(payload, dict)
    assert "market_note" not in payload
    assert "异动" not in repr(payload)


def test_analyze_reports_a_fuzzy_rating(plugin) -> None:
    """分析给的是模糊评级（绿 / 黄 / 红），不给精确数值。"""
    run(plugin.join(mode="classic_beginner", seed=9108))
    payload = _value(run(plugin.analyze()))
    assert payload["rating"] in {"green", "yellow", "red"}
    assert "rating_label" in payload or "rating" in payload
    assert "chasing" in payload
    assert 0.0 <= payload["position_weight"] <= 1.0


def test_analyze_without_stock_id_picks_the_heaviest_position(plugin) -> None:
    """不指定标的 → 分析持仓最重的那只。"""
    run(plugin.join(mode="classic_beginner", seed=9109))
    market = _value(run(plugin.market()))
    cheapest = min(market["stocks"], key=lambda item: item["price"])
    run(plugin.buy(cheapest["id"], 1.0))
    payload = _value(run(plugin.analyze()))
    assert payload["stock_id"] == cheapest["id"]


# ---------------------------------------------------------------- ③ news_detail


def test_news_detail_gives_headline_and_content(plugin) -> None:
    """点开详情才给标题与正文。"""
    run(plugin.join(mode="classic_beginner", seed=9110))
    run(plugin._run_one_tick())
    news = _value(run(plugin.status()))["news_today"]
    assert news
    detail = _value(run(plugin.news_detail(news[0]["id"])))
    assert detail["id"] == news[0]["id"]
    assert detail["headline"]
    assert detail["content"]
    assert detail["kind"] in {"bullish", "bearish", "vague", "noise"}


def test_news_detail_never_leaks_truth(plugin) -> None:
    """详情**仍然不给真假**、不给原始 type、不给准确率。"""
    run(plugin.join(mode="classic_beginner", seed=9111))
    run(plugin._run_one_tick())
    news = _value(run(plugin.status()))["news_today"]
    detail = _value(run(plugin.news_detail(news[0]["id"])))
    blob = repr(detail).lower()
    for token in ("accuracy", "'true'", "'fake'", "'thunder'", "type"):
        assert token not in blob, f"新闻详情泄漏了真假线索：{token}"


def test_news_detail_of_an_unknown_id(plugin) -> None:
    run(plugin.join(mode="classic_beginner", seed=9112))
    assert _error_code(run(plugin.news_detail("news-does-not-exist"))) == "news_not_found"


def test_news_detail_of_another_game_is_forbidden(plugin) -> None:
    """别的局留下的新闻 id：存在但**不属于本局** → forbidden。"""
    run(plugin.join(mode="classic_beginner", seed=9113))
    # 走到第 2 天，拿一条第 2 天的新闻 id（第 1 天的 id 会被新局覆盖）。
    for _ in range(12):
        run(plugin._run_one_tick())
    old_news = _value(run(plugin.status()))["news_today"][0]["id"]
    assert "-2-" in old_news
    # 结算并开新局 → news_log 里的旧新闻还在，但已经不属于本局。
    run(plugin.settle())
    run(plugin.join(mode="classic_beginner", seed=9114))
    assert _error_code(run(plugin.news_detail(old_news))) == "news_detail_forbidden"


# ---------------------------------------------------------------- ④ 薄入口


def test_daily_claim_matches_daily_claim_action(plugin) -> None:
    run(plugin.join(mode="infinite", seed=9115))
    thin = _value(run(plugin.daily_claim()))
    assert thin["action"] == "claim"
    assert thin["granted"] > 0


def test_daily_loan_matches_daily_loan_action(plugin) -> None:
    run(plugin.join(mode="infinite", seed=9116))
    thin = _value(run(plugin.daily_loan()))
    assert thin["action"] == "loan"
    assert thin["granted"] > 0


def test_daily_reject_outside_the_window_is_refused(plugin) -> None:
    """撤销窗口外 → 与 ``daily(action="reject")`` 一样报错。"""
    run(plugin.join(mode="infinite", seed=9117))
    assert _error_code(run(plugin.daily_reject())) == "not_at_daily_boundary"


def test_daily_reject_inside_the_window_undoes_the_sell(plugin) -> None:
    """撤销窗口内 → 走的是"撤销"逻辑（而不是"现在不是撤销窗口"）。"""
    run(plugin.join(mode="infinite", seed=9118))
    market = _value(run(plugin.market()))
    cheapest = min(market["stocks"], key=lambda item: item["price"])
    run(plugin.buy(cheapest["id"], 1.0))
    plugin._daily_window_until = 1e18
    result = run(plugin.daily_reject())
    code = _error_code(result) if result.is_err() else "ok"
    assert code != "not_at_daily_boundary"


# ---------------------------------------------------------------- ⑤ 面板载荷


def test_panel_payload_has_every_block(plugin) -> None:
    """面板载荷字段齐全（P5 契约 §4.2 的 11 个区块）。"""
    run(plugin.join(mode="classic_beginner", seed=9119))
    payload = run(plugin.market_context())
    assert isinstance(payload, dict)
    for key in (
        "day",
        "tick",
        "phase",
        "ticks_to_settle",
        "cash",
        "total_assets",
        "next_bill",
        "bill_gap",
        "missed_bills",
        "distance_to_kill",
        "concentration",
        "positions",
        "stocks",
        "kline_stock_ids",
        "catgirl",
        "news_today",
        "settlement",
    ):
        assert key in payload, f"面板载荷缺字段：{key}"


def test_panel_payload_limits_the_kline(plugin) -> None:
    """细节 2：K 线只给 ``kline_stock_ids`` 里的标的，其余只给 price / tag。"""
    run(plugin.join(mode="classic_beginner", seed=9120))
    payload = run(plugin.market_context())
    assert isinstance(payload, dict)
    kline_ids = set(payload["kline_stock_ids"])
    assert len(kline_ids) <= 20
    for stock in payload["stocks"]:
        if stock["id"] in kline_ids:
            assert "kline" in stock
            assert len(stock["kline"]) <= 60
        else:
            assert "kline" not in stock
            # 不带 K 线的标的仍然给基础信息
            assert "price" in stock and "tag" in stock and "alive" in stock


def test_panel_payload_news_only_has_the_neutral_kind(plugin) -> None:
    """局中新闻只有分类（标题正文要点开才给）。"""
    run(plugin.join(mode="classic_beginner", seed=9121))
    run(plugin._run_one_tick())
    payload = run(plugin.market_context())
    assert isinstance(payload, dict)
    for item in payload["news_today"]:
        assert set(item) == {"id", "kind", "kind_label"}


def test_panel_payload_never_leaks_forbidden_fields(plugin) -> None:
    """面板载荷不含 heat / seed / 反转日 / 新闻真假。"""
    run(plugin.join(mode="classic_beginner", seed=9122))
    payload = run(plugin.market_context())
    assert isinstance(payload, dict)
    blob = repr(payload).lower()
    for token in ("heat", "seed", "reversal", "accuracy", "prey_index"):
        assert token not in blob, f"面板载荷泄漏了：{token}"


def test_panel_payload_carries_no_reversal_today(plugin) -> None:
    """方案 B：面板载荷里也没有 ``reversal_today``。"""
    run(plugin.join(mode="classic_beginner", seed=9123))
    payload = run(plugin.market_context())
    assert isinstance(payload, dict)
    assert "reversal_today" not in payload


def test_panel_payload_exposes_the_catgirl_public_state(plugin) -> None:
    """猫娘公开状态：持仓 / 现金 / 存活天数（**不给她的 heat**）。"""
    run(plugin.join(mode="infinite", seed=9124))
    payload = run(plugin.market_context())
    assert isinstance(payload, dict)
    catgirl = payload["catgirl"]
    assert catgirl is not None
    for key in ("cash", "positions_value", "days_survived", "today_action"):
        assert key in catgirl
    assert "heat" not in repr(catgirl).lower()


def test_panel_payload_shows_the_archived_settlement_after_the_game(plugin) -> None:
    """局终之后：面板要能看到归档的结算报告（P4 历史问题 4）。"""
    run(plugin.join(mode="classic_beginner", seed=9125))
    run(plugin.settle())
    payload = run(plugin.market_context())
    assert isinstance(payload, dict)
    assert payload["stage"] == "finished"
    assert payload["settlement"] is not None
    assert "seed" in payload["settlement"]


def test_market_entry_still_has_no_reversal_today(plugin) -> None:
    """``market`` 入口也不再有 ``reversal_today``。"""
    run(plugin.join(mode="classic_beginner", seed=9126))
    payload = _value(run(plugin.market()))
    assert "reversal_today" not in payload
