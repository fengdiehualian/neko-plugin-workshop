"""投递已开始的 pending 不得再收新消息 —— 否则会把刚答过的话再答一遍。

实测现场（私聊连续三条）：

    04:43:03  [Buffer] 调度延迟回复: wait=0.6s text=是说之前那些人生大节点的事吗？
    04:43:04  [Voice] 未检测到语音段            ← 上一条回复**正在发出去**
    04:43:04  收到消息: text=我说
    04:43:04  [Buffer] 预缓冲追加（共2条），等待 6.3s，跳过 LLM 生成
    04:43:10  缓冲2条消息，走 pipeline 生成总结...

两条日志的顺序就是 bug：`[Voice]` 在前，追加在后。第一轮把 msg2 单独答了，第二轮
因为 message_count=2 走总结分支，把 msg2 **又**卷进去答了一遍。
"""
from __future__ import annotations

import time
from types import SimpleNamespace

import pytest
from plugin.plugins.qq_auto_reply.reply_buffer_service import (
    PendingReply,
    QQReplyBufferService,
)


def _service() -> QQReplyBufferService:
    svc = QQReplyBufferService.__new__(QQReplyBufferService)
    svc.plugin = SimpleNamespace(
        _qq_settings={}, _emit_log=lambda *a, **k: None,
        _maybe_push_status_event=lambda *a, **k: None,
    )
    svc._pending = {}
    return svc


def _existing_pending(*, delivering: bool) -> PendingReply:
    """一个"回复已排期、正在等发"的 pending（task 未完成 → 追加条件是满足的）。"""
    class _Task:
        def done(self) -> bool:
            return False

        def cancel(self) -> None:      # _supersede 会取消旧任务
            pass

    p = PendingReply(first_text="但是这块都成心魔了", wait_seconds=0.6,
                     sender_id="820040531", is_group=False, group_id="")
    p.message_count = 1
    p.task = _Task()
    p.delivering = delivering
    return p


def test_pending_defaults_to_not_delivering():
    p = PendingReply(first_text="x", wait_seconds=1.0, sender_id="1",
                     is_group=False, group_id="")
    assert p.delivering is False


@pytest.mark.parametrize("delivering,expect_appended", [(False, True), (True, False)])
async def test_append_is_refused_once_delivery_started(delivering, expect_appended):
    # async：合并路径会 asyncio.create_task 起新一轮投递，需要运行中的事件循环
    # （根 pytest.ini 是 asyncio_mode=auto，async 测试自动跑在循环里）。
    svc = _service()
    p = _existing_pending(delivering=delivering)
    svc._pending["private:820040531"] = p

    skipped = svc.pre_buffer("private:820040531", "我说", "820040531", False, "")

    if expect_appended:
        # 投递还没开始 → 照旧合并（这是用户认可的"很人类"行为）
        assert skipped is True
        assert p.message_count == 2
        assert svc._pending["private:820040531"] is p
    else:
        # 投递已开始 → 拒绝合并，让这条消息新建缓冲、走自己的 pipeline
        assert skipped is False, "投递已开始的 pending 仍然收下了新消息"
        assert p.message_count == 1, "旧 pending 被改动了"
        assert svc._pending["private:820040531"] is not p, "没有为这条消息新建缓冲"


async def test_new_pending_after_delivery_is_fresh_and_undelivering():
    svc = _service()
    svc._pending["private:820040531"] = _existing_pending(delivering=True)

    svc.pre_buffer("private:820040531", "我说", "820040531", False, "")

    fresh = svc._pending["private:820040531"]
    assert fresh.delivering is False
    assert fresh.message_count == 1
    assert fresh.buffered_user_texts == ["我说"]
    assert fresh.task is None, "新缓冲的等待任务应由 schedule_reply 启动"
    assert fresh.wait_until > time.time() - 1
