# -*- coding: utf-8 -*-
"""「正在和她对话的人」（`dialogue_partner`）：一来一回才算。

使用者口径（2026-09-29）：「可以让猫娘对正在聊天的对象的禁言做出反应吗」→
对象口径选的是「正在和她对话的人」。这份用例把那个口径钉死：

* 他找过她 **且** 她回过他，才算"正在对话"；
* 只有单向（他 @ 过她 / 她回过他）不算；
* 超出窗口不算（默认 600 秒，与禁言反应的冷却同长）；
* 状态只服务"最近"，重启即失、每群有条数上限。
"""
from __future__ import annotations

import pytest
from plugin.plugins.qq_auto_reply.dialogue_partner import DialoguePartnerTracker

GROUP = "1048307485"
ALICE = "1782348687"
BOB = "3658428358"


def _tracker(**kw) -> DialoguePartnerTracker:
    return DialoguePartnerTracker(**kw)


def test_one_round_trip_counts_as_dialogue():
    t = _tracker()
    t.note_address(GROUP, ALICE, now=1000)
    t.note_reply(GROUP, ALICE, now=1005)

    assert t.is_in_dialogue(GROUP, ALICE, now=1010) is True
    assert t.dialogue_partners(GROUP, now=1010) == [ALICE]


def test_addressing_alone_is_not_dialogue():
    """只 @ 过她一声、她没回过 → 不算"正在和她对话"。"""
    t = _tracker()
    t.note_address(GROUP, ALICE, now=1000)

    assert t.is_in_dialogue(GROUP, ALICE, now=1001) is False
    assert t.dialogue_partners(GROUP, now=1001) == []


def test_replying_alone_is_not_dialogue():
    """她回过他、但他从没找过她（例如她自己接了一句）→ 不算。"""
    t = _tracker()
    t.note_reply(GROUP, ALICE, now=1000)

    assert t.is_in_dialogue(GROUP, ALICE, now=1001) is False


def test_the_window_expires():
    t = _tracker(window_seconds=600)
    t.note_address(GROUP, ALICE, now=1000)
    t.note_reply(GROUP, ALICE, now=1000)

    assert t.is_in_dialogue(GROUP, ALICE, now=1599) is True
    assert t.is_in_dialogue(GROUP, ALICE, now=1601) is False, "过了窗口还认为在对话"
    assert t.dialogue_partners(GROUP, now=1601) == []


def test_a_stale_address_does_not_keep_the_dialogue_alive():
    """十分钟前叫过她、刚刚回过他 —— 半条腿过期就不算（两条都要在窗口内）。"""
    t = _tracker(window_seconds=600)
    t.note_address(GROUP, ALICE, now=1000)
    t.note_reply(GROUP, ALICE, now=1700)

    assert t.is_in_dialogue(GROUP, ALICE, now=1700) is False


def test_only_the_person_in_dialogue_is_reported():
    t = _tracker()
    t.note_address(GROUP, ALICE, now=1000)
    t.note_reply(GROUP, ALICE, now=1001)
    t.note_address(GROUP, BOB, now=1002)          # 只叫过她

    assert t.is_in_dialogue(GROUP, ALICE, now=1003) is True
    assert t.is_in_dialogue(GROUP, BOB, now=1003) is False
    assert t.dialogue_partners(GROUP, now=1003) == [ALICE]


def test_groups_do_not_share_state():
    t = _tracker()
    t.note_address(GROUP, ALICE, now=1000)
    t.note_reply(GROUP, ALICE, now=1001)

    assert t.is_in_dialogue("另一个群", ALICE, now=1002) is False


def test_blank_ids_are_ignored():
    t = _tracker()
    t.note_address("", ALICE, now=1000)
    t.note_reply(GROUP, "", now=1000)

    assert t.is_in_dialogue("", ALICE) is False
    assert t.is_in_dialogue(GROUP, "") is False
    assert t.dialogue_partners(GROUP) == []


def test_partners_are_capped_and_pruned():
    t = _tracker()
    for index in range(t.MAX_PARTNERS + 5):
        uid = f"u{index}"
        t.note_address(GROUP, uid, now=1000 + index)
        t.note_reply(GROUP, uid, now=1000 + index)

    partners = t.dialogue_partners(GROUP, now=1100)
    assert len(partners) <= t.MAX_PARTNERS, "这个表无界增长"
    # 最近互动的那些留下，最旧的被淘汰
    assert "u0" not in partners and f"u{t.MAX_PARTNERS + 4}" in partners


def test_partners_are_sorted_by_recency():
    t = _tracker()
    for index, uid in enumerate((ALICE, BOB)):
        t.note_address(GROUP, uid, now=1000 + index * 10)
        t.note_reply(GROUP, uid, now=1000 + index * 10)

    assert t.dialogue_partners(GROUP, now=1030) == [BOB, ALICE]


def test_forget_clears_a_group():
    t = _tracker()
    t.note_address(GROUP, ALICE, now=1000)
    t.note_reply(GROUP, ALICE, now=1000)
    assert t.is_in_dialogue(GROUP, ALICE, now=1001) is True

    t.forget(GROUP)

    assert t.is_in_dialogue(GROUP, ALICE, now=1001) is False


def test_default_clock_is_used_when_now_is_missing():
    """不传 now 时用墙上时钟（真实路径就是这么调的）。"""
    t = _tracker()
    t.note_address(GROUP, ALICE)
    t.note_reply(GROUP, ALICE)

    assert t.is_in_dialogue(GROUP, ALICE) is True


def test_bad_now_values_fall_back_to_the_clock():
    t = _tracker()
    t.note_address(GROUP, ALICE, now="不是数字")
    t.note_reply(GROUP, ALICE, now=None)

    assert t.is_in_dialogue(GROUP, ALICE) is True


@pytest.mark.parametrize("window", [60.0, 600.0])
def test_window_is_configurable(window):
    t = _tracker(window_seconds=window)
    t.note_address(GROUP, ALICE, now=1000)
    t.note_reply(GROUP, ALICE, now=1000)

    assert t.is_in_dialogue(GROUP, ALICE, now=1000 + window) is True
    assert t.is_in_dialogue(GROUP, ALICE, now=1000 + window + 1) is False
