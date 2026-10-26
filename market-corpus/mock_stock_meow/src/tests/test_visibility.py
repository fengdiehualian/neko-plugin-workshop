"""P2「局中隐藏」守卫单测。

这是"局中隐藏"这条教学前提的**唯一自动化守卫**（P2 契约 §6.3）。
只要有人往返回体里多塞一个 ``heat`` / ``seed`` / ``accuracy``，这里立刻红。

守卫方式：递归收集返回体的所有 dict key，逐个比对禁止清单。
另外再做两条结构性检查：

* 源码里不允许出现 ``dataclasses.asdict`` / ``vars(...)`` 这类整体导出
  （契约 §6.2：显式白名单构造）；
* ``settle`` 是**唯一**允许出现 ``seed`` 与 ``heat_curve`` 的入口。
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

import pytest
from conftest import run
from storage.repo import Repo

#: 契约 §6.1 的禁止字段清单。
FORBIDDEN = {
    "heat",
    "heat_curve",
    "seed",
    "reversal_days",
    "accuracy",
    "prey_index",
}

#: 变体守卫：这些词出现在任何 key 里都不允许（heat_history / market_seed 等）。
FORBIDDEN_TOKENS = ("heat", "seed", "reversal", "prey_index")

#: **白名单例外**（P5 起为空）。
#:
#: P4 §11 曾经允许 ``market`` 返回 ``reversal_today``（"今天是不是反转日"），
#: 守卫显式放行它以免后来者误以为守卫坏了。**P5 方案 B 把这个字段移除了**
#: —— 玩家要拿到"今天特殊"这个信息，得花一次分析机会换一句不带方向的模糊提示。
#: 于是整个 ``reversal`` 词族重新一律禁止，这里不再留任何例外。
ALLOWED_KEYS: set[str] = set()

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]


def _walk_keys(payload: Any) -> list[str]:
    """递归收集 payload 里的所有 dict key。"""
    keys: list[str] = []
    if isinstance(payload, dict):
        for key, value in payload.items():
            keys.append(str(key))
            keys.extend(_walk_keys(value))
    elif isinstance(payload, (list, tuple)):
        for item in payload:
            keys.extend(_walk_keys(item))
    return keys


def _assert_no_forbidden_keys(payload: Any, entry_id: str) -> None:
    """payload 里不允许出现任何禁止字段（``ALLOWED_KEYS`` 里的白名单除外）。"""
    for key in _walk_keys(payload):
        lowered = key.lower()
        if lowered in ALLOWED_KEYS:
            continue
        assert lowered not in FORBIDDEN, f"{entry_id} 泄漏了禁止字段：{key}"
        for token in FORBIDDEN_TOKENS:
            assert token not in lowered, f"{entry_id} 泄漏了禁止字段：{key}"


def _value(result: Any) -> dict[str, Any]:
    assert result.is_ok(), f"期望 Ok，实际 {result!r}"
    payload = result.unwrap()
    assert isinstance(payload, dict)
    return payload


@pytest.fixture()
def started(plugin) -> Any:
    """已经开局、看过盘、买过一笔的插件实例。"""
    _value(run(plugin.join(seed=987654321)))
    market = _value(run(plugin.market()))
    cheapest = min(market["stocks"], key=lambda item: item["price"])
    _value(run(plugin.buy(cheapest["id"], 1.0)))
    return plugin


# ---------------------------------------------------------------- 契约 §6.3


def test_status_hides_heat_and_seed(started) -> None:
    """status 的返回体不含 heat / seed。"""
    payload = _value(run(started.status()))
    _assert_no_forbidden_keys(payload, "status")


def test_market_hides_seed_and_reversal_days(started) -> None:
    """market 的返回体不含 seed / 反转日。"""
    payload = _value(run(started.market()))
    _assert_no_forbidden_keys(payload, "market")


def test_join_does_not_echo_seed(plugin) -> None:
    """join 即使由调用方指定 seed 也不回显。"""
    payload = _value(run(plugin.join(seed=987654321)))
    _assert_no_forbidden_keys(payload, "join")
    assert "seed" not in payload


def test_buy_sell_payloads_hide_forbidden_keys(started) -> None:
    """buy / sell 的返回体不含禁止字段。"""
    market = _value(run(started.market()))
    stock_id = min(market["stocks"], key=lambda item: item["price"])["id"]
    bought = _value(run(started.buy(stock_id, 1.0)))
    _assert_no_forbidden_keys(bought, "buy")
    sold = _value(run(started.sell(stock_id, 1.0)))
    _assert_no_forbidden_keys(sold, "sell")


def test_settle_is_the_only_entry_exposing_seed(started) -> None:
    """settle 是唯一允许出现 seed 与 heat_curve 的入口。"""
    # 先采集局中各入口的返回体（settle 之后对局即结束）
    in_game = (
        (_value(run(started.status())), "status"),
        (_value(run(started.market())), "market"),
    )
    for payload, entry_id in in_game:
        _assert_no_forbidden_keys(payload, entry_id)

    report = _value(run(started.settle()))
    assert report["seed"] == 987654321
    assert "heat_curve" in report


# ---------------------------------------------------------------- 补充守卫


def test_ui_context_payload_hides_forbidden_keys(started) -> None:
    """UI 面板上下文与 market 入口共用同一份白名单，因此同样不可能泄漏。"""
    payload = run(started.market_context())
    _assert_no_forbidden_keys(payload, "ui.context")


# ---------------------------------------------------------------- P3 追加


def test_daily_entry_hides_forbidden_keys(plugin) -> None:
    """P3 新增的 ``daily`` 入口同样不允许泄漏。"""
    _value(run(plugin.join(seed=51515151)))
    # 不在撤销窗口内 → 直接是错误；构造一个窗口内的调用
    plugin._daily_window_until = time.time() + 30
    payload = _value(run(plugin.daily(action="status")))
    _assert_no_forbidden_keys(payload, "daily")


def test_pending_action_and_event_hide_forbidden_keys(started) -> None:
    """``pending_action`` / ``pending_event`` 里不允许出现 heat 之类的 key。"""
    status = _value(run(started.status()))
    for field in ("pending_action", "pending_event"):
        payload = status.get(field)
        if payload is None:
            continue
        _assert_no_forbidden_keys(payload, f"status.{field}")


def test_heat_value_never_leaks_into_in_game_payloads(plugin) -> None:
    """**值层面**的守卫：拿一个哨兵 heat 值，逐个入口查它有没有漏出去。

    key 扫描挡不住"把 heat 塞进 value"，所以这里用数值哨兵补上这一层。
    """
    _value(run(plugin.join(seed=61616161)))
    market = _value(run(plugin.market()))
    stock = min(market["stocks"], key=lambda item: item["price"])
    _value(run(plugin.buy(stock["id"], 1.0)))

    # 往数据库里塞一个非常独特的哨兵 heat
    sentinel = 987.654321
    repo = Repo(plugin.db)
    row = run(repo.fetch_one("SELECT player_id FROM market_state WHERE id = 1"))
    player_id = row["player_id"]
    run(
        repo.transaction(
            [
                ("UPDATE players SET heat = ? WHERE id = ?", (sentinel, player_id)),
                (
                    "UPDATE heat_history SET heat = ? WHERE player_id = ?",
                    (sentinel, player_id),
                ),
            ]
        )
    )

    plugin._daily_window_until = time.time() + 30
    payloads = {
        "status": _value(run(plugin.status())),
        "market": _value(run(plugin.market())),
        "buy": _value(run(plugin.buy(stock["id"], 1.0))),
        "sell": _value(run(plugin.sell(stock["id"], 1.0))),
        "daily": _value(run(plugin.daily(action="status"))),
    }
    for entry_id, payload in payloads.items():
        dumped = json.dumps(payload, ensure_ascii=False)
        assert str(sentinel) not in dumped, f"{entry_id} 泄漏了 heat 数值"
        assert f"{sentinel:.3f}" not in dumped, f"{entry_id} 泄漏了 heat 数值"


def test_settle_is_allowed_to_show_heat(plugin) -> None:
    """反过来验证：``settle`` **确实**能看到 heat 曲线（否则守卫就是假的）。"""
    _value(run(plugin.join(seed=71717171)))
    market = _value(run(plugin.market()))
    stock = min(market["stocks"], key=lambda item: item["price"])
    _value(run(plugin.buy(stock["id"], 1.0)))
    run(plugin._run_one_tick())
    report = _value(run(plugin.settle()))
    assert report["heat_curve"]
    assert max(report["heat_curve"]) > 0


def test_error_payloads_do_not_leak_internal_state(plugin) -> None:
    """错误返回体同样不允许泄漏内部字段。"""
    result = run(plugin.buy("FAKE-001", 1))
    assert result.is_err()
    error = result.err()
    details = getattr(error, "details", None)
    if isinstance(details, dict):
        _assert_no_forbidden_keys(details, "error")


def test_plugin_layer_does_not_use_bulk_serialization() -> None:
    """结构性守卫：禁止 ``asdict`` / ``vars(...)`` 这类整体导出。"""
    source = (_PLUGIN_ROOT / "__init__.py").read_text(encoding="utf-8")
    assert not re.search(r"\basdict\s*\(", source), "禁止使用 dataclasses.asdict 整体导出"
    assert not re.search(r"\bvars\s*\(", source), "禁止使用 vars() 整体导出"


def test_forbidden_list_matches_contract() -> None:
    """禁止清单本身就是契约的一部分，改动必须是有意识的。"""
    assert FORBIDDEN == {
        "heat",
        "heat_curve",
        "seed",
        "reversal_days",
        "accuracy",
        "prey_index",
    }
