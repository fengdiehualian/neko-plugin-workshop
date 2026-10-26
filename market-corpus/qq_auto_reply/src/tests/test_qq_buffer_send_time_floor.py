"""发送时刻 = ``max(生成完成 + 发送停顿, 消息到达 + 收集窗口)`` —— 下限语义。

为什么值得单独钉：原来的实现是**加法**（`到达 → 生成 N 秒 → 再等 W 秒`），而生成
耗时不稳定（同一轮对话实测 1.0s 与 4.0s 两种，见用户日志），于是总延迟既慢又随机：
生成快时收集窗口小到后续消息几乎并不进来，生成慢时又白等一笔。改成下限之后
结果不依赖生成时间的随机性。
"""
from __future__ import annotations

import time
from types import SimpleNamespace

from plugin.plugins.qq_auto_reply.reply_buffer_service import (
    PendingReply,
    QQReplyBufferService,
)


def _svc(settings: dict) -> QQReplyBufferService:
    svc = QQReplyBufferService.__new__(QQReplyBufferService)
    svc.plugin = SimpleNamespace(
        _qq_settings=dict(settings), _emit_log=lambda *a, **k: None,
        _maybe_push_status_event=lambda *a, **k: None,
    )
    svc._pending = {}
    return svc


def _pending() -> PendingReply:
    return PendingReply(first_text="在吗", wait_seconds=0.0,
                        sender_id="1", is_group=True, group_id="2")


def test_generation_fast_falls_back_to_the_collect_window():
    """生成很快 → 取"到达 + 收集窗口"那条：窗口补足，后续消息才有机会并进来。"""
    svc = _svc({"buffer_collect_window_seconds": 3.0})
    p = _pending()                      # created_at ≈ now（刚到达）

    send_at = svc._send_at(p, 0.5, is_group=True)

    assert 2.9 < send_at - p.created_at < 3.2, "没有补足到收集窗口"


def test_generation_slow_does_not_wait_extra():
    """生成已超过窗口 → 只加发送停顿，一秒都不白等（这就是"下限"的意义）。"""
    svc = _svc({"buffer_collect_window_seconds": 3.0})
    p = _pending()
    p.created_at = time.time() - 10.0   # 生成花了 10 秒，窗口早就满足了

    now = time.time()
    send_at = svc._send_at(p, 0.5, is_group=True)

    assert 0.4 < send_at - now < 0.7, "生成慢时又白等了一笔"


def test_private_uses_its_own_window():
    """私聊窗口与群聊分开可配（一对一里对方在等，通常给得更短）。"""
    svc = _svc({"buffer_collect_window_seconds": 30.0,
                "buffer_collect_window_private_seconds": 1.0})
    p = _pending()

    assert 0.9 < svc._send_at(p, 0.0, is_group=False) - p.created_at < 1.2


def test_missing_keys_fall_back_to_defaults():
    """老配置没有这两个键 → 群 3.0 / 私聊 1.0，且不能炸。"""
    svc = _svc({})

    assert svc._collect_window(private=False) == 3.0
    assert svc._collect_window(private=True) == 1.0


def test_zero_window_means_no_collection_window():
    """设 0 = 不收集（到达即发，只留发送停顿）。0 必须被尊重，不能被当成"未设置"。"""
    svc = _svc({"buffer_collect_window_seconds": 0.0})
    p = _pending()

    now = time.time()
    send_at = svc._send_at(p, 0.5, is_group=True)

    assert 0.4 < send_at - now < 0.7, "0 被当成了未设置、回退到默认 3 秒"


def test_negative_window_is_clamped_to_zero():
    svc = _svc({"buffer_collect_window_seconds": -5.0})

    assert svc._collect_window(private=False) == 0.0
