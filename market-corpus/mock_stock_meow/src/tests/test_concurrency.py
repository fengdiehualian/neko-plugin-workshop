"""P3：锁与并发。

对应 P3 契约 §1.3（锁的位置）、§14（锁与并发）、
§17 验收标准 9（锁覆盖整个事务区间，不丢更新）。
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest
from conftest import run


def _value(result: Any) -> dict[str, Any]:
    assert result.is_ok(), f"期望 Ok，实际 {result!r}"
    payload = result.unwrap()
    assert isinstance(payload, dict)
    return payload


def test_plugin_has_a_global_lock(plugin) -> None:
    """插件层必须有一把全局锁。"""
    assert isinstance(plugin._lock, type(threading.Lock()))


def test_entries_block_while_the_lock_is_held(plugin) -> None:
    """锁被占着时，入口函数必须等——这正是防"读-改-写"丢更新的机制。"""
    _value(run(plugin.join(seed=61)))
    box: dict[str, Any] = {}

    def call_status() -> None:
        box["value"] = run(plugin.status())

    thread = threading.Thread(target=call_status, daemon=True)
    with plugin._lock:
        thread.start()
        time.sleep(0.2)
        assert "value" not in box, "入口函数没有等锁"
    thread.join(timeout=10)
    assert "value" in box
    assert box["value"].is_ok()


def test_concurrent_buys_and_ticks_do_not_lose_updates(plugin) -> None:
    """并发买入 + 并发 tick：成交笔数与现金都不许丢。"""
    _value(run(plugin.join(seed=62)))
    market = _value(run(plugin.market()))
    stock = min(market["stocks"], key=lambda item: item["price"])
    initial = _value(run(plugin.status()))["cash"]

    def do_buy():
        return run(plugin.buy(stock["id"], 1.0))

    def do_tick():
        last = None
        for _ in range(3):
            last = run(plugin._run_one_tick())
        return last

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(do_buy) for _ in range(4)]
        futures.append(pool.submit(do_tick))
        results = [future.result(timeout=60) for future in futures]

    buys = []
    for result in results:
        if result is None:
            continue
        if isinstance(result, bool):
            # P11.1：``_run_one_tick`` 现在返回"这一拍跑没跑"的布尔值
            # （拿不到锁就**跳过**这一拍，而不是阻塞整个事件循环）——
            # 它不是错误，跳过即可。
            continue
        assert result.is_ok(), f"并发调用不应报错：{result!r}"
        payload = result.unwrap()
        if isinstance(payload, dict) and "cash_after" in payload:
            buys.append(payload)
    assert len(buys) == 4

    status = _value(run(plugin.status()))
    # 4 笔成交都要在（没有丢更新）
    assert status["trade_count"] == 4
    # 现金 = 最后完成的那笔买入之后的现金（tick 不改现金）
    expected_cash = min(float(item["cash_after"]) for item in buys)
    assert status["cash"] == pytest.approx(expected_cash, abs=1e-6)
    assert status["cash"] < initial


def test_tick_and_entry_do_not_deadlock(plugin) -> None:
    """tick 与入口互相调用会死锁——这里验证它们**不会**同时卡住。"""
    _value(run(plugin.join(seed=63)))
    market = _value(run(plugin.market()))
    stock = min(market["stocks"], key=lambda item: item["price"])

    def do_entry():
        return run(plugin.buy(stock["id"], 1.0))

    def do_tick():
        return run(plugin._run_one_tick())

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(do_entry), pool.submit(do_tick)]
        for future in futures:
            future.result(timeout=30)
