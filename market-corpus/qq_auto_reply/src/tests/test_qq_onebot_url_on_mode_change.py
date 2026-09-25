"""切换连接模式时的通信地址重置。

`onebot_url` 的**含义**随模式而变：反向是 N.E.K.O 的监听地址（``0.0.0.0:6199``），
正向是拨到 NapCat 服务端的地址（``127.0.0.1:3001``）。沿用旧值几乎必然错。

踩过的坑：napcat.html 的保存表单**每次都把地址框的值一起提交**，而那个框在切方向时
不会跟着换 —— 旧默认值被当成"用户显式指定"，重置被守卫挡掉，正向模式于是去拨
``0.0.0.0``，Windows 报 ``WinError 1214 指定的网络名格式无效``。
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

from plugin.plugins.qq_auto_reply.settings_service import QQSettingsService

REVERSE = "ws://0.0.0.0:6199"
FORWARD = "ws://127.0.0.1:3001"


class _Plugin(SimpleNamespace):
    """未知属性一律给惰性替身。

    `save_settings` 会顺带碰一串服务；逐个铺太脆，而这条用例只关心
    `onebot_url` 的去向。显式设过的属性优先，兜底只补没写的。
    """

    def __getattr__(self, name):
        value = SimpleNamespace()
        setattr(self, name, value)
        return value


def _svc(mode: str, url: str) -> tuple[QQSettingsService, SimpleNamespace]:
    plugin = _Plugin(
        _qq_settings={"qq_connection_mode": mode, "onebot_url": url},
        _emit_log=lambda *a, **k: None,
        _mask_token=lambda t: "***",
        _ensure_qq_client_initialized=lambda: None,
        attention_service=None,
        qq_client=None,
        attention_gate_service=None,
        napcat_service=SimpleNamespace(clear_startup_error=lambda: None),
        config_store=SimpleNamespace(
            normalize_reply_mode=lambda v: v or "text",
            normalize_backlog_labels=lambda v: v,
            _normalize_strategy_mode=lambda v: v or "neko_dynamic",
        ),
    )
    svc = QQSettingsService(plugin)
    svc.persist_business_config = AsyncMock(return_value=True)
    return svc, plugin


async def _save(svc, **kw):
    return await svc.save_settings(**kw)


# ── 旧模式的默认值回传，要重置 ──────────────────────────────

async def test_stale_reverse_default_is_reset_when_switching_to_forward():
    """这条就是 WinError 1214 的成因。"""
    svc, plugin = _svc("napcat", REVERSE)

    await _save(svc, qq_connection_mode="napcat_forward", onebot_url=REVERSE)

    assert plugin._qq_settings["onebot_url"] == FORWARD


async def test_stale_forward_default_is_reset_when_switching_to_reverse():
    svc, plugin = _svc("napcat_forward", FORWARD)

    await _save(svc, qq_connection_mode="napcat", onebot_url=FORWARD)

    assert plugin._qq_settings["onebot_url"] == REVERSE


async def test_omitted_url_is_reset():
    svc, plugin = _svc("napcat", REVERSE)

    await _save(svc, qq_connection_mode="napcat_forward")

    assert plugin._qq_settings["onebot_url"] == FORWARD


# ── 用户自己填的地址仍然优先 ────────────────────────────────

async def test_custom_url_survives_a_mode_change():
    """不是那两个默认值就说明是用户自己填的 —— 不许动。"""
    svc, plugin = _svc("napcat", "ws://192.168.1.9:7000")

    await _save(svc, qq_connection_mode="napcat_forward", onebot_url="ws://192.168.1.9:7000")

    assert plugin._qq_settings["onebot_url"] == "ws://192.168.1.9:7000"


async def test_same_mode_does_not_touch_the_url():
    """模式没变就不重置 —— 否则每次保存都会把用户的地址冲掉。"""
    svc, plugin = _svc("napcat_forward", "ws://10.1.2.3:9999")

    await _save(svc, qq_connection_mode="napcat_forward", onebot_url="ws://10.1.2.3:9999")

    assert plugin._qq_settings["onebot_url"] == "ws://10.1.2.3:9999"
