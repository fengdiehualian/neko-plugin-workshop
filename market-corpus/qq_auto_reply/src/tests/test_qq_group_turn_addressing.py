# -*- coding: utf-8 -*-
"""群聊逐条标注：**显式指向优先，其次看对话时序**。

真机由来（使用者 2026-09-27：「为什么破冰完没有后续的回复，看」）：

```
17:37:21  她破冰开口（38 字），并按住焦点 120s          ← 新机制正常工作
17:37:51  群里回「是吗」——普通消息，没 @、没引用
17:37:53  她输出 <feeling>bored</feeling>（23 字符 + 换行 = 日志里那个固定的"24 字"），
          让出焦点，一个字都没回
```

`_build_group_turn_message` 当时只判**显式指向**（@ 你 / 引用你 / 引用别人 / @ 别人 /
@全体），判不出来就落进「不是冲你来的…不要每条都接」——于是"她刚开口、对方第一句回应"
这种最该接的情况被自己的标注劝退。

现在补了第二层判据：**她刚说完话，而这一条是之后的第一条发言** →
标注成「很可能是在接你的话，该接就接，别晾着对方」。

判据复用接话反馈闭环的 `msgs_after_reply`（`note_proactive_speech` / `update_on_reply`
清零，别人每说一条 +1；门控在建提示词之前已经为当前这条 +1 过，所以 `== 1` 恰好是
"她说完之后的第一条"），再加一个反馈窗口（默认 90s）限制时效。

本文件钉住：五种显式指向**优先级不变**（改动只影响"都判不出来"的那一支）、
时序标注的文案与条件、以及"取不到注意力服务时退回原行为"。
"""

from __future__ import annotations

from types import SimpleNamespace

from plugin.plugins.qq_auto_reply.attention_service import QQAttentionService
from plugin.plugins.qq_auto_reply.prompt_builder import QQPromptBuilder
from plugin.plugins.qq_auto_reply.prompting import QQAutoReplyPromptingMixin

GROUP = "985066274"
MINUTES = 60


def _turn(**over) -> str:
    args = dict(
        group_scene_mode="group_collective",
        user_title="宅久（主人）",
        sender_id="820040531",
        group_id=GROUP,
        message="是吗",
        current_message_id="m1",
    )
    args.update(over)
    return QQAutoReplyPromptingMixin._build_group_turn_message(**args)


# ── 一、五种显式指向优先级不变 ──────────────────────────────────────

def test_at_bot_still_wins():
    out = _turn(group_scene_mode="directed_user", first_reply_after_own_speech=True)
    assert "是冲你来的" in out and "很可能是在接你的话" not in out


def test_reply_to_bot_still_wins():
    out = _turn(is_reply_to_bot=True, first_reply_after_own_speech=True)
    assert "回复了你的某条消息" in out and "很可能是在接你的话" not in out


def test_quoting_someone_else_still_means_not_for_you():
    """对方在回复**别人** —— 即使她刚说过话，这条也算不到她头上。"""
    out = _turn(quoted_message_id="999", first_reply_after_own_speech=True)
    assert "不是冲你来的" in out and "很可能是在接你的话" not in out


def test_mentioning_someone_else_still_means_not_for_you():
    out = _turn(mentions_other_user=True, first_reply_after_own_speech=True)
    assert "不是冲你来的" in out and "很可能是在接你的话" not in out


def test_mention_all_is_unchanged():
    out = _turn(mentions_all=True, first_reply_after_own_speech=True)
    assert "@全体成员" in out


# ── 二、时序那一支 ─────────────────────────────────────────────────

def test_a_plain_message_right_after_she_spoke_is_flagged_as_hers():
    out = _turn(first_reply_after_own_speech=True)
    assert "很可能是在接你的话" in out, "她刚说完、第一条回应仍然被标成'不是冲你来的'"
    assert "不是冲你来的" not in out


def test_without_the_flag_the_default_wording_is_unchanged():
    out = _turn()
    assert "不是冲你来的" in out and "不要每条都接" in out
    assert "很可能是在接你的话" not in out


# ── 三、判据：复用 msgs_after_reply ────────────────────────────────

def _attention():
    plugin = SimpleNamespace(
        group_permission_mgr=SimpleNamespace(list_groups=lambda: [{"group_id": GROUP}]),
        _qq_settings={
            "attention_feedback_enabled": True,
            "attention_feedback_window_seconds": 90.0,
            "backlog_labels": [],
        },
        backlog_store=None,
        permission_mgr=None,
        _emit_log=lambda *a, **k: None,
        logger=SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None),
    )
    svc = QQAttentionService(plugin)
    now = {"t": 100_000}
    svc._current_time = lambda: now["t"]
    return svc, now


def _spoke(svc, *, msgs_after: int, ago: int = 5):
    st = svc._load_state(GROUP)
    st.last_reply_at = 100_000 - ago
    st.msgs_after_reply = msgs_after
    svc._write_state(st)
    return st


def test_the_first_message_after_she_spoke_qualifies():
    svc, now = _attention()
    _spoke(svc, msgs_after=1)
    assert svc.is_first_reply_after_own_speech(GROUP) is True


def test_the_second_message_does_not_qualify():
    """已经有人在中间说过话了 —— 这条不再算"接她的第一句"。"""
    svc, now = _attention()
    _spoke(svc, msgs_after=2)
    assert svc.is_first_reply_after_own_speech(GROUP) is False


def test_an_old_speech_does_not_qualify():
    """十分钟后冒出来的一句不该被算成"接她的话"（窗口默认 90s）。"""
    svc, now = _attention()
    _spoke(svc, msgs_after=1, ago=10 * MINUTES)
    assert svc.is_first_reply_after_own_speech(GROUP) is False


def test_never_spoke_does_not_qualify():
    svc, now = _attention()
    st = svc._load_state(GROUP)
    st.last_reply_at = 0
    st.msgs_after_reply = 1
    svc._write_state(st)
    assert svc.is_first_reply_after_own_speech(GROUP) is False


def test_empty_group_id_does_not_qualify():
    svc, now = _attention()
    _spoke(svc, msgs_after=1)
    assert svc.is_first_reply_after_own_speech("") is False


# ── 四、接线：prompt_builder 真的把这段上下文传下去了 ────────────────

class _AttentionStub:
    def __init__(self, *, answer: bool) -> None:
        self.answer = answer
        self.calls: list[str] = []

    def is_first_reply_after_own_speech(self, group_id, **_kw) -> bool:
        self.calls.append(str(group_id))
        return self.answer


def _builder(attention):
    plugin = SimpleNamespace(
        attention_service=attention,
        _build_group_turn_message=QQAutoReplyPromptingMixin._build_group_turn_message,
    )
    return QQPromptBuilder(plugin)


def test_prompt_builder_passes_the_flag_down():
    attention = _AttentionStub(answer=True)
    out = _builder(attention).build_prompt_message(
        is_group=True, group_facing=False, group_scene_mode="group_collective",
        user_title="宅久", sender_id="820040531", group_id=GROUP, message="是吗",
    )
    assert attention.calls == [GROUP], "没有去问「她刚说完之后的第一条吗」"
    assert "很可能是在接你的话" in out


def test_prompt_builder_falls_back_when_attention_is_missing():
    """取不到注意力服务 = 退回改动前的标注（不是报错、也不是乱标）。"""
    out = _builder(None).build_prompt_message(
        is_group=True, group_facing=False, group_scene_mode="group_collective",
        user_title="宅久", sender_id="820040531", group_id=GROUP, message="是吗",
    )
    assert "不是冲你来的" in out


def test_prompt_builder_survives_a_broken_attention_service():
    class _Boom:
        def is_first_reply_after_own_speech(self, *_a, **_k):
            raise RuntimeError("注意力状态炸了")

    out = _builder(_Boom()).build_prompt_message(
        is_group=True, group_facing=False, group_scene_mode="group_collective",
        user_title="宅久", sender_id="820040531", group_id=GROUP, message="是吗",
    )
    assert "不是冲你来的" in out, "标注降级失败还抛出去了 —— 整轮生成会被带死"


def test_private_sessions_are_untouched():
    """私聊不走这段标注（它只服务群聊的"这条是不是冲你来的"）。"""
    out = _builder(_AttentionStub(answer=True)).build_prompt_message(
        is_group=False, group_facing=False, group_scene_mode="",
        user_title="宅久", sender_id="820040531", group_id=None, message="在吗",
    )
    assert out == "在吗"


def test_the_real_attention_service_integrates():
    """真服务 + 真 builder：她刚破冰、群里回一句 → 标注该是"接你的话"。"""
    svc, now = _attention()
    st = svc._load_state(GROUP)
    st.last_reply_at = now["t"]
    st.msgs_after_reply = 1
    svc._write_state(st)
    out = _builder(svc).build_prompt_message(
        is_group=True, group_facing=False, group_scene_mode="group_collective",
        user_title="宅久", sender_id="820040531", group_id=GROUP, message="是吗",
    )
    assert "很可能是在接你的话" in out
