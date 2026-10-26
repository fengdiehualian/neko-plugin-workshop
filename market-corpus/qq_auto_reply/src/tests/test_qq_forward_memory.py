"""转发记录进记忆：接收方域拿到「我转发了什么 + 被转发的原文」，并明确标注是转发来的。

使用者确认的做法（原话）：
> 所有转发都记。把被转发的原文一起写进接收方域，但是要明确标注是转发来的。1，2，3都做

为什么需要：转发以前是**一次性外发** —— `send_forward` 只调 API，不写任何会话历史/
记忆；源群那条 ai 行又因为只含 `<forward>` 标记而被当「未投递」排除。于是接收方事后
问「你转的那段里说的 X 是什么意思」，猫娘手里什么都没有，只能反问「是什么东西呀」。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from plugin.plugins.qq_auto_reply.reply_pipeline import QQReplyPipelineRunner

GROUP = "1048307485"
ADMIN = "820040531"
FRIEND = "2968007221"

#: **用真实 backlog 落盘的键名**（`sender_name`）。这里曾经写成 `sender_nickname` ——
#: 那是另一条路径（喂给插件的消息字典）的键，对 backlog 记录永远取不到，于是发言人
#: 全部回落到 QQ 号（转发卡片、回溯摘要里显示的都是数字而不是昵称）。
FORWARDED = [
    {"sender_name": "小明", "sender_id": "111", "text": "他先骂我的"},
    {"sender_name": "小红", "sender_id": "222", "text": "明明是你先"},
]


class _Bridge:
    """记录写入了哪个域、什么内容。"""

    def __init__(self) -> None:
        self.legacy: list[tuple[str, list]] = []
        self.scoped: list[tuple[dict, list]] = []
        self.fail = False

    @staticmethod
    def group_subject(gid) -> dict:
        return {"subject_kind": "group_chat", "subject_id": f"qq:{gid}"}

    @staticmethod
    def participant_subject(sid) -> dict:
        return {"subject_kind": "participant", "subject_id": f"qq:{sid}"}

    async def post_memory_history(self, endpoint, her_name, messages, timeout=5.0):
        if self.fail:
            raise RuntimeError("boom")
        self.legacy.append((endpoint, messages))
        return {"status": "ok"}

    async def post_scoped_memory_history(self, her_name, messages, *, subject, timeout=10.0):
        if self.fail:
            raise RuntimeError("boom")
        self.scoped.append((subject, messages))
        return {"status": "ok"}


def _runner(*, settings=None, admin=(ADMIN,)):
    bridge = _Bridge()
    logs: list[tuple[str, str]] = []
    level_map = {qq: "admin" for qq in admin}

    plugin = SimpleNamespace(
        memory_bridge=bridge,
        _qq_settings={
            "group_memory_enabled": True,
            "private_participant_memory_enabled": True,
            **(settings or {}),
        },
        permission_mgr=SimpleNamespace(
            get_permission_level=lambda sid: level_map.get(str(sid), "trusted"),
        ),
        _user_sessions={f"group:{GROUP}": {"her_name": "宅久皖萱"}},
        _build_session_key=lambda *, sender_id, is_group, group_id=None: (
            f"group:{group_id}" if is_group else f"private:{sender_id}"
        ),
        _emit_log=lambda level, msg: logs.append((level, msg)),
    )
    return QQReplyPipelineRunner(plugin), bridge, logs


def _text_of(messages) -> str:
    return "\n".join(
        part.get("text", "")
        for m in messages
        for part in (m.get("content") or [])
        if isinstance(part, dict)
    )


# ── 写进哪个域（跟既有 opt-in 门控同源）────────────────────────────

def test_group_target_writes_to_that_group_domain():
    runner, bridge, _ = _runner()
    asyncio.run(runner._record_forward_in_memory(
        source_group_id=GROUP, target_type="group", target_id="985066274",
        summary="给你们看看", forwarded=FORWARDED,
    ))
    subjects = [s["subject_id"] for s, _ in bridge.scoped]
    assert "qq:985066274" in subjects, subjects
    assert not bridge.legacy, "群目标不该写进主人的私有语料"


def test_private_admin_target_writes_to_the_legacy_private_corpus():
    """管理员走 /cache —— 与他和猫娘的私聊记忆**同一个域**，所以他一问就能召回。"""
    runner, bridge, _ = _runner()
    asyncio.run(runner._record_forward_in_memory(
        source_group_id=GROUP, target_type="private", target_id=ADMIN,
        summary="我赢了", forwarded=FORWARDED,
    ))
    assert bridge.legacy and bridge.legacy[0][0] == "cache", bridge.legacy


def test_private_friend_target_writes_to_their_participant_domain():
    runner, bridge, _ = _runner()
    asyncio.run(runner._record_forward_in_memory(
        source_group_id=GROUP, target_type="private", target_id=FRIEND,
        summary="", forwarded=FORWARDED,
    ))
    subjects = [s["subject_id"] for s, _ in bridge.scoped]
    assert f"qq:{FRIEND}" in subjects, subjects


@pytest.mark.parametrize(
    "settings,target_type,target_id",
    [
        ({"group_memory_enabled": False}, "group", "985066274"),
        ({"private_participant_memory_enabled": False}, "private", FRIEND),
        ({"private_participant_memory_enabled": False}, "private", "999999999"),
    ],
)
def test_target_is_not_written_when_its_switch_is_off(settings, target_type, target_id):
    """目标侧的开关关着就不写它那个域（源群的动作记录是另一个域，不受此限）。"""
    runner, bridge, _ = _runner(settings=settings)
    asyncio.run(runner._record_forward_in_memory(
        source_group_id=GROUP, target_type=target_type, target_id=target_id,
        summary="", forwarded=FORWARDED,
    ))
    touched = [s["subject_id"] for s, _ in bridge.scoped]
    assert f"qq:{target_id}" not in touched, f"目标域开关关着却写了: {touched}"
    assert not bridge.legacy, "目标的私有语料开关关着却写了"


def test_admin_target_is_governed_by_the_private_policy_not_the_group_switch():
    """管理员走 legacy 私有语料，由私聊政策决定 —— 关掉**群**记忆不该影响它。

    （读路径同源：`reply_context_node` 的 `private_memory_mode` 只看权限档位，
    群开关只管群域。）
    """
    runner, bridge, _ = _runner(settings={"group_memory_enabled": False})
    asyncio.run(runner._record_forward_in_memory(
        source_group_id=GROUP, target_type="private", target_id=ADMIN,
        summary="我赢了", forwarded=FORWARDED,
    ))
    assert bridge.legacy, "群开关误伤了管理员的私聊记忆"
    assert not bridge.scoped, "群开关关着却写了源群域"


def test_source_group_note_never_leaks_a_private_targets_qq():
    """源群那句**不写私聊对象的 QQ 号** —— 群记忆会被群聊回复召回，那是披露。"""
    runner, bridge, _ = _runner()
    asyncio.run(runner._record_forward_in_memory(
        source_group_id=GROUP, target_type="private", target_id=ADMIN,
        summary="我赢了", forwarded=FORWARDED,
    ))
    source_notes = [t for s, msgs in bridge.scoped
                    if s["subject_id"] == f"qq:{GROUP}" for t in [_text_of(msgs)]]
    assert source_notes, "源群没有动作记录"
    assert ADMIN not in source_notes[0], (
        f"源群记忆里出现了管理员的 QQ: {source_notes[0]!r}"
    )
    assert "私聊里的某人" in source_notes[0]


def test_source_group_note_keeps_a_group_target_id():
    """群目标可以写群号（本来就在群语境里）。"""
    runner, bridge, _ = _runner()
    asyncio.run(runner._record_forward_in_memory(
        source_group_id=GROUP, target_type="group", target_id="985066274",
        summary="", forwarded=FORWARDED,
    ))
    source_notes = [t for s, msgs in bridge.scoped
                    if s["subject_id"] == f"qq:{GROUP}" for t in [_text_of(msgs)]]
    assert source_notes and "985066274" in source_notes[0]


# ── 内容形状：必须标注是转发来的 ────────────────────────────────────

def test_record_marks_the_content_as_forwarded_and_attributes_each_speaker():
    """**使用者的明确要求**：原文要一起写，但必须标注是转发来的，不能像是猫娘说的。"""
    runner, bridge, _ = _runner()
    asyncio.run(runner._record_forward_in_memory(
        source_group_id=GROUP, target_type="private", target_id=ADMIN,
        summary="我赢了", forwarded=FORWARDED,
    ))
    text = _text_of(bridge.legacy[0][1])
    assert "【转发记录】" in text
    assert "不是我说的" in text, "没有标注'不是我说的'，提取器会把这句当成猫娘说的"
    assert "群友说的话" in text
    assert GROUP in text, "没说清原文来自哪个群"
    assert "小明(111): 他先骂我的" in text and "小红(222): 明明是你先" in text
    assert "我赢了" in text, "模型写的那句总结没记进去"


def test_record_is_written_as_the_bot_own_line_not_a_fake_user_row():
    """记 assistant 行 —— 伪造 human 行会被提取器抽成「用户说过」。"""
    runner, bridge, _ = _runner()
    asyncio.run(runner._record_forward_in_memory(
        source_group_id=GROUP, target_type="group", target_id="985066274",
        summary="", forwarded=FORWARDED,
    ))
    for _subject, messages in bridge.scoped:
        assert messages, "空批次"
        for msg in messages:
            assert msg["role"] == "assistant", f"出现了非 assistant 行: {msg['role']}"


def test_source_group_also_gets_a_short_action_note_without_the_original_text():
    """源群留一句动作记录，但**不附原文**（那些话本来就是那里的 human 行）。"""
    runner, bridge, _ = _runner()
    asyncio.run(runner._record_forward_in_memory(
        source_group_id=GROUP, target_type="private", target_id=ADMIN,
        summary="我赢了", forwarded=FORWARDED,
    ))
    source_posts = [t for s, msgs in bridge.scoped
                    if s["subject_id"] == f"qq:{GROUP}" for t in [_text_of(msgs)]]
    assert source_posts, "源群没有留下动作记录"
    note = source_posts[0]
    assert "【转发记录】" in note
    assert "他先骂我的" not in note, "源群那句不该重复附原文"


def test_forwarded_text_is_capped():
    """转发可以有 50 个节点 —— 记录必须有界，否则会把接收方的域淹掉。"""
    runner, _bridge, _ = _runner()
    many = [
        {"sender_nickname": f"人{i}", "sender_id": str(i), "text": "话" * 60}
        for i in range(60)
    ]
    text = QQReplyPipelineRunner._forward_memory_text(
        source_group_id=GROUP, target_label="private:1", summary="", forwarded=many,
    )
    assert text.count("\n") <= QQReplyPipelineRunner.FORWARD_MEMORY_MAX_LINES + 3, (
        f"记录没被截断，行数 {text.count(chr(10))}"
    )
    assert "未记入" in text, "截断处没有说明还有多少条没记"


def test_bridge_failure_only_logs():
    """转发已经发出去了，补记失败只降级成日志，不能把这一轮搞崩。"""
    runner, bridge, logs = _runner()
    bridge.fail = True
    asyncio.run(runner._record_forward_in_memory(
        source_group_id=GROUP, target_type="private", target_id=ADMIN,
        summary="", forwarded=FORWARDED,
    ))
    assert any(level == "WARNING" and "转发记录写入失败" in msg for level, msg in logs), logs


def test_speaker_label_helper_handles_every_real_shape():
    """发言人显示名的键名收口在一个 helper 里（三处读点曾各自硬编码，两处写错）。"""
    from plugin.plugins.qq_auto_reply.pipeline_models import backlog_sender_label

    # backlog 落盘的真实形状（`QQBacklogMessage.to_dict`）
    assert backlog_sender_label({"sender_name": "宅久", "sender_id": "820040531"}) == "宅久"
    # 另一条路径（喂给插件的消息字典）的键
    assert backlog_sender_label({"sender_nickname": "小明", "sender_id": "111"}) == "小明"
    # 只有 id 时回落成 QQ 号（而不是空串）
    assert backlog_sender_label({"sender_id": "222"}) == "222"
    # 空白名不算名字
    assert backlog_sender_label({"sender_name": "  ", "sender_id": "333"}) == "333"
    # 什么都没有时给默认值
    assert backlog_sender_label({}, default="群友") == "群友"


def test_her_name_resolution_never_silently_returns_empty():
    """角色名有三级回退 —— 写成 `getattr(plugin, '_her_name', '')` 会静默早退。

    这正是我第一版犯的错：插件上没有 `_her_name` 属性，拿到空串后在
    `if not her_name: return` 处直接返回，整个功能变成**空操作**。
    """
    runner, _bridge, _ = _runner()
    assert runner._resolve_her_name(GROUP) == "宅久皖萱"

    # 会话里没有 her_name 时也要有可用的回退（宿主配置或 "neko"）
    plugin = runner.plugin
    plugin._user_sessions = {}
    assert runner._resolve_her_name(GROUP), "回退链断了，会静默不写记忆"
