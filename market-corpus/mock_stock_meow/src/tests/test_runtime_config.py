"""运行时能力对齐（0.7.4 真机复盘）的回归防线。

**事故背景（Steam 版宿主 0.9.0 真机）**

宿主在插件进程启动时给到的 ``ctx._effective_config`` 里**可能压根没有**
``[plugin.database]`` / ``[plugin_state]`` 段 —— 插件拿到的
``db.enabled`` 停在 ``False``，于是：

* ``on_startup`` 建表必然失败（"plugin database is disabled"）；
* 1 秒心跳每秒钟都撞在"数据库不可用"上，**tick 永远不推进**；
* 面板永远停在"数据库不可用。"。

而 0.7.3 的修复（``refresh_runtime_config()`` 不带参数）**等于什么都没做** ——
不带参数时它只会重新解析插件进程里那份**缺能力段**的缓存。

**0.7.4 的修法**

1. ``_effective_runtime_config()`` 把三份来源合成一份完整配置：
   **宿主缓存 > 读到的配置 > 插件自己的 ``plugin.toml`` 清单**；
2. ``_apply_runtime_config()`` 把合成结果喂给 ``refresh_runtime_config``；
3. ``_due_ticks()`` 先同步配置再读库；读不到库就**刷新 + 重试**一次，
   并且**不重置时间基线**（否则库恢复后时间差被抹平，市场永远追不上）。

清单兜底的合法性来自 SDK 自己的话：*"plugin.toml manifest metadata is
authoritative"*（``plugin/sdk/shared/core/config.py``）。但宿主**显式**给过
的值仍然优先 —— 只在**缺段**时补，不覆盖。
"""

from __future__ import annotations

import time
from typing import Any

from conftest import make_plugin, run
from plugins.mock_stock_meow.cards import build_taunt_card
from plugins.mock_stock_meow.storage.repo import DbUnavailableError


def _strip_capabilities(ctx: Any) -> None:
    """把宿主给的能力段删掉 —— 复现真机上"缺 ``[plugin.database]``"的状态。"""
    ctx._effective_config = {
        key: value
        for key, value in ctx._effective_config.items()
        if key not in ("plugin", "plugin_state")
    }


def test_manifest_fills_the_missing_database_capability(ctx) -> None:
    """宿主没给 ``[plugin.database]`` 时，用插件自己的清单补上。"""
    _strip_capabilities(ctx)
    instance = make_plugin(ctx)

    assert instance.db.enabled is False, "前置条件：构造时宿主没给能力段"
    run(instance.on_startup())

    assert instance.db.enabled is True, "清单里的 [plugin.database] 应当兜底生效"
    assert instance.db.db_name == "mock_stock_meow.db"


def test_manifest_fills_the_missing_state_backend(ctx) -> None:
    """宿主没给 ``[plugin_state]`` 时，用清单补上（默认 backend=file）。"""
    _strip_capabilities(ctx)
    instance = make_plugin(ctx)
    run(instance.on_startup())

    assert instance.state.backend == "file"


def test_host_config_wins_over_the_manifest(ctx) -> None:
    """宿主**显式**关掉数据库时，插件不得用清单把它顶回来。"""
    ctx._effective_config = {
        "plugin": {"database": {"enabled": False}},
        "plugin_state": {"backend": "off"},
    }
    instance = make_plugin(ctx)
    run(instance.on_startup())

    assert instance.db.enabled is False, "宿主的显式配置优先于插件清单"
    assert instance.state.backend == "off"


def test_refresh_runtime_capabilities_applies_the_dumped_config(ctx) -> None:
    """``_refresh_runtime_capabilities()`` 要把读到的配置真正应用下去。"""
    _strip_capabilities(ctx)
    instance = make_plugin(ctx)
    ctx.config_payload = {"plugin_state": {"backend": "off"}}

    run(instance._refresh_runtime_capabilities())

    assert instance.state.backend == "off"


def test_host_cache_wins_over_the_dumped_config(ctx) -> None:
    """宿主缓存（权威）与读到的配置冲突时，**宿主那份优先**。"""
    instance = make_plugin(ctx)
    ctx.config_payload = {"plugin_state": {"backend": "off"}}

    run(instance._refresh_runtime_capabilities())

    assert instance.state.backend == "file", "宿主缓存里的 plugin_state 优先"


def test_due_ticks_refreshes_and_retries_when_the_db_is_unavailable(
    plugin, monkeypatch
) -> None:
    """读不到库时：刷新配置 + 重试一次，重试成功就算出该补几个 tick。"""
    calls = {"count": 0}

    async def flaky_fetch_one(sql, params=()):
        calls["count"] += 1
        if calls["count"] == 1:
            raise DbUnavailableError("plugin database is disabled")
        return {"player_id": "p1", "mode": "classic_beginner"}

    monkeypatch.setattr(plugin._repo, "fetch_one", flaky_fetch_one)
    plugin._last_tick_at = time.time() - 20.0

    due = run(plugin._due_ticks())

    assert calls["count"] == 2, "第一次读不到库应当刷新配置后重试一次"
    assert due == 2, "经典入门 8 秒一 tick，20 秒应当补 2 个"


def test_due_ticks_keeps_the_baseline_while_the_db_is_down(plugin, monkeypatch) -> None:
    """库一直读不到时**不能**重置时间基线（否则库恢复后永远追不上）。"""
    async def broken_fetch_one(sql, params=()):
        raise DbUnavailableError("plugin database is disabled")

    monkeypatch.setattr(plugin._repo, "fetch_one", broken_fetch_one)
    baseline = plugin._last_tick_at = time.time() - 30.0

    due = run(plugin._due_ticks())

    assert due == 0
    assert plugin._last_tick_at == baseline, "库不可用时必须保留时间基线"


def test_due_ticks_resets_the_baseline_when_there_is_no_game(
    plugin, monkeypatch
) -> None:
    """确实没有开局（读得到、但没有行）时仍然重置基线。"""
    async def empty_fetch_one(sql, params=()):
        return None

    monkeypatch.setattr(plugin._repo, "fetch_one", empty_fetch_one)
    plugin._last_tick_at = time.time() - 30.0

    due = run(plugin._due_ticks())

    assert due == 0
    assert plugin._last_tick_at > time.time() - 5.0, "没有开局时应当重置基线"


def test_push_card_falls_back_to_a_text_message(plugin, monkeypatch) -> None:
    """宿主没有卡片 API（0.9.0 真机）时，降级成一条纯文本聊天消息。"""
    sent: list[dict[str, Any]] = []

    monkeypatch.setattr(plugin.ctx, "create_card", None, raising=False)
    monkeypatch.setattr(
        plugin.ctx,
        "push_message",
        lambda **kwargs: sent.append(kwargs),
        raising=False,
    )

    card = build_taunt_card(trigger="on_player_default", text="嘴上说说而已")
    run(plugin._push_card(card))

    assert sent, "宿主没有卡片 API 时应当降级成一条文本消息"
    assert sent[0]["parts"][0]["text"] == card.summary
    assert sent[0]["ai_behavior"] == "blind", "降级消息同样不许 AI 读"


def test_push_card_uses_create_card_when_the_host_provides_it(
    plugin, monkeypatch
) -> None:
    """宿主**有**卡片 API 时，仍然优先走 ``create_card``（保持卡片形态）。"""
    created: list[dict[str, Any]] = []
    pushed: list[dict[str, Any]] = []

    async def fake_create_card(**kwargs):
        created.append(kwargs)

    monkeypatch.setattr(plugin.ctx, "create_card", fake_create_card, raising=False)
    monkeypatch.setattr(
        plugin.ctx,
        "push_message",
        lambda **kwargs: pushed.append(kwargs),
        raising=False,
    )

    card = build_taunt_card(trigger="on_player_default", text="嘴上说说而已")
    run(plugin._push_card(card))

    assert created and not pushed, "有卡片 API 时不该降级"
    assert created[0]["html"] == card.html
    assert created[0]["summary"] == card.summary
