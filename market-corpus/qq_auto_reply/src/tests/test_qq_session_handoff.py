"""接续摘要：会话被回收时留一句"刚才聊到哪儿"，新会话开口就接得上。

**为什么要有这个**（使用者给的现场）：私聊里她说完"口误居然还全票通过啦"之后隔了
5 分半，下一句是"哇，这个小猫娘好可爱！和我有点像欸" —— 明显割裂。原因是会话被回收：

    SESSION_IDLE_TIMEOUT_SECONDS = 300  → 结算长期记忆 + **弹掉会话**
    → 下一条消息落在全新会话上（只有长期记忆，没有刚才那几轮）

这里钉四类不变量：

1. **摘要内容**：只取尾部几轮、每行截断、剥掉她自己的协议标签、跳过 system/工具行、
   未授权区间（`nonconsent_history_end` 之后）不进摘要。
2. **授权闸**：`memory_enabled` 为假的会话不留摘要（摘要会把原文写到磁盘，与记忆结算
   同一道闸），并把旧的抹掉；一次性会话（主动发言那类合成轮）也不留。
3. **一次性与时效**：注入后 `consume`；超过 TTL 不再注入；换了角色就不交给新角色。
4. **落盘**：换一个服务实例（模拟插件重启）仍能读到 —— 触发这个需求的那次割裂，一半
   原因就是重启把内存会话清空了，只放内存的摘要在最需要它的时刻正好不在。
"""

from __future__ import annotations

import json
import logging
import pathlib
import time
from types import SimpleNamespace

from plugin.plugins.qq_auto_reply.session_handoff_service import (
    MAX_LINE_CHARS,
    MAX_TURNS,
    QQSessionHandoffService,
)


class _Human:
    def __init__(self, content: str) -> None:
        self.content = content


class _AI:
    def __init__(self, content: str) -> None:
        self.content = content


class _System:
    def __init__(self, content: str) -> None:
        self.content = content


class _Session:
    def __init__(self, history: list) -> None:
        self._conversation_history = history


def _user_data(history: list, **overrides) -> dict:
    data = {
        "session": _Session(history),
        "memory_enabled": True,
        "her_name": "宅久皖萱",
        "is_group": False,
        "group_id": "",
    }
    data.update(overrides)
    return data


def _service(tmp_path: pathlib.Path) -> QQSessionHandoffService:
    plugin = SimpleNamespace(
        logger=logging.getLogger("qq.test"),
        data_path=lambda name: tmp_path / name,
    )
    return QQSessionHandoffService(plugin)


# ── 1. 摘要内容 ──────────────────────────────────────────────────

def test_note_keeps_only_the_tail_turns(tmp_path):
    history = []
    for index in range(6):
        history.append(_Human(f"对方第{index}句"))
        history.append(_AI(f"<msg>她第{index}句</msg>"))
    note = _service(tmp_path).build_note(session_key="k", user_data=_user_data(history))

    assert note is not None
    assert len(note["lines"]) == MAX_TURNS * 2, note["lines"]
    assert "对方第5句" in note["lines"][-2]
    assert "她第5句" in note["lines"][-1]
    assert "对方第0句" not in " ".join(note["lines"])


def test_note_strips_protocol_tags_and_truncates(tmp_path):
    long_line = "喵" * (MAX_LINE_CHARS + 50)
    history = [
        _Human("你看得到这个嘛[CQ:reply,id=999]"),
        _AI(f"<feeling>playful</feeling>\n<msg>{long_line}</msg>"),
    ]
    note = _service(tmp_path).build_note(session_key="k", user_data=_user_data(history))

    joined = " ".join(note["lines"])
    assert "<msg>" not in joined and "<feeling>" not in joined
    assert len(note["lines"][1]) <= MAX_LINE_CHARS + 4  # 「你：」前缀 + 截断


def test_internal_state_and_id_tags_drop_their_content(tmp_path):
    """`<feeling>` 的**内容**是内部状态、`<sticker>` 的内容是 ID，都不是她说过的话。

    （真小票里出现过 `你：playful看得到呀乖乖…` —— 只脱壳会留下内容。）
    """
    history = [
        _Human("看得到嘛"),
        _AI("<feeling>playful</feeling><msg>看得到呀乖乖</msg>"),
        _Human("再来一张"),
        _AI("<feeling>curious</feeling><msg><sticker>20</sticker></msg>"),
    ]
    note = _service(tmp_path).build_note(session_key="k", user_data=_user_data(history))

    first, second = note["lines"][-2], note["lines"][-1]
    assert "playful" not in first and "看得到呀乖乖" in first, first
    assert "curious" not in second and "20" not in second, second


def test_system_rows_never_enter_the_note(tmp_path):
    history = [_System("你是一个角色扮演大师"), _Human("在吗"), _AI("在的喵")]
    note = _service(tmp_path).build_note(session_key="k", user_data=_user_data(history))

    assert all("角色扮演大师" not in line for line in note["lines"])


def test_rows_before_the_authorization_boundary_are_excluded(tmp_path):
    """`nonconsent_history_end` 之前是未授权区间（边界=授权重新开始处）：
    那段原文不许进摘要 —— 摘要会被写到磁盘，等同于记忆写入。"""
    history = [
        _Human("没授权时说的"), _AI("没授权时答的"),
        _Human("授权后说的"), _AI("<msg>授权后答的</msg>"),
    ]
    note = _service(tmp_path).build_note(
        session_key="k", user_data=_user_data(history, nonconsent_history_end=2),
    )

    joined = " ".join(note["lines"])
    assert "授权后" in joined
    assert "没授权时" not in joined


def test_note_records_the_character(tmp_path):
    note = _service(tmp_path).build_note(
        session_key="k", user_data=_user_data([_Human("hi"), _AI("喵")]),
    )

    assert note["her_name"] == "宅久皖萱"


def test_empty_history_builds_no_note(tmp_path):
    assert _service(tmp_path).build_note(session_key="k", user_data=_user_data([])) is None
    assert _service(tmp_path).build_note(session_key="k", user_data={"session": None}) is None


# ── 2. 授权闸 ────────────────────────────────────────────────────

def test_memory_disabled_session_leaves_no_note(tmp_path):
    service = _service(tmp_path)

    assert service.capture("k", _user_data([_Human("hi"), _AI("喵")], memory_enabled=False)) is None
    assert service.peek("k") is None


def test_a_note_from_before_is_dropped_when_the_session_is_unauthorized(tmp_path):
    """记忆被关掉之后再回收会话：旧的摘要也得抹掉，不能留下来。"""
    service = _service(tmp_path)
    service.capture("k", _user_data([_Human("hi"), _AI("喵")]))
    assert service.peek("k") is not None

    service.capture("k", _user_data([_Human("hi"), _AI("喵")], memory_enabled=False))

    assert service.peek("k") is None


def test_ephemeral_session_leaves_no_note(tmp_path):
    """主动发言那类一次性会话不是"一段对话"，别把她的搭话留给下一个会话。"""
    service = _service(tmp_path)

    assert service.capture(
        "k", _user_data([_Human("[系统] 抛个话题"), _AI("喵")], ephemeral_session=True),
    ) is None


# ── 3. 一次性 / 时效 / 换角色 ────────────────────────────────────

def test_consume_makes_it_one_shot(tmp_path):
    service = _service(tmp_path)
    service.capture("k", _user_data([_Human("hi"), _AI("喵")]))

    service.consume("k")

    assert service.peek("k") is None


def test_expired_note_is_not_injected(tmp_path):
    service = _service(tmp_path)
    service.capture("k", _user_data([_Human("hi"), _AI("喵")]))
    service._notes["k"]["at"] = time.time() - 3600  # 一小时前

    assert service.peek("k") is None
    assert "k" not in service._notes, "过期条目应顺手清掉"


def test_note_is_not_handed_to_another_character(tmp_path):
    service = _service(tmp_path)
    service.capture("k", _user_data([_Human("hi"), _AI("喵")]))

    assert service.peek("k", her_name="YUI") is None
    assert service.peek("k", her_name="宅久皖萱") is not None


def test_forget_drops_the_note(tmp_path):
    service = _service(tmp_path)
    service.capture("k", _user_data([_Human("hi"), _AI("喵")]))

    assert service.forget("k") is True
    assert service.peek("k") is None
    assert service.forget("k") is False


# ── 4. 落盘 ──────────────────────────────────────────────────────

def test_note_survives_a_restart(tmp_path):
    first = _service(tmp_path)
    first.capture("private:820040531", _user_data([_Human("你看得到这个嘛"), _AI("喵")]))

    second = _service(tmp_path)  # 新实例 = 插件重启后的状态

    note = second.peek("private:820040531")
    assert note is not None and "你看得到这个嘛" in note["lines"][0]


def test_corrupt_store_does_not_break_startup(tmp_path):
    (tmp_path / "session_handoff.json").write_text("{ 这不是 JSON", encoding="utf-8")
    service = _service(tmp_path)

    assert service.peek("k") is None  # 不抛，按"没有摘要"处理
    service.capture("k", _user_data([_Human("hi"), _AI("喵")]))
    assert service.peek("k") is not None


def test_store_shape_is_json_with_notes(tmp_path):
    service = _service(tmp_path)
    service.capture("k", _user_data([_Human("hi"), _AI("喵")]))

    data = json.loads((tmp_path / "session_handoff.json").read_text(encoding="utf-8"))

    assert data["version"] == 1 and "k" in data["notes"]


# ── 5. 注入文本 ──────────────────────────────────────────────────

def test_render_section_contains_the_tail_and_a_gap_hint(tmp_path):
    service = _service(tmp_path)
    service.capture("k", _user_data([_Human("你看得到这个嘛"), _AI("<msg>口误全票通过啦</msg>")]))

    section = service.render_section("k")

    assert "口误全票通过啦" in section
    assert "你看得到这个嘛" in section
    assert "上一次对话" in section and "分钟前" in section
    assert "别照抄" in section or "别假装" in section, "要明确告诉她隔了一段时间、别硬接"
    assert "<msg>" not in section


def test_render_section_is_empty_without_a_note(tmp_path):
    assert _service(tmp_path).render_section("nope") == ""
