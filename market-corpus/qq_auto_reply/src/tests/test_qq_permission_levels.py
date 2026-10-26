# -*- coding: utf-8 -*-
"""权限收敛后的判定语义（用户口径）。

- ``none``（不在名单）→ ignore
- ``normal`` → **被 @ 或引用她**才回；其余消息按概率转发给主人（relay）
- ``trusted`` → 走注意力门控，放行即 reply
- ``open`` → **级别已删除**，配置里残留的会被别名升为 ``trusted``
- 私聊 → 一律 reply（只把真实级别带下去给下游用）
"""

from __future__ import annotations

import inspect
from types import SimpleNamespace

import pytest
from plugin.plugins.qq_auto_reply.group_permission import GroupPermissionManager
from plugin.plugins.qq_auto_reply.permission import PermissionManager
from plugin.plugins.qq_auto_reply.pipeline_models import QQReplyRequest
from plugin.plugins.qq_auto_reply.reply_decision_node import QQReplyDecisionNode


class _Groups:
    def __init__(self, level: str = "normal"):
        self._level = level

    def get_group_level(self, group_id: str) -> str:
        return self._level

    def get_normal_relay_probability(self, group_id: str) -> float:
        return 0.1


class _Users:
    def __init__(self, level: str = "none"):
        self._level = level

    def get_permission_level(self, user_id: str) -> str:
        return self._level


def _node(group_level: str = "normal", user_level: str = "none") -> QQReplyDecisionNode:
    plugin = SimpleNamespace(
        attention_service=None,
        group_permission_mgr=_Groups(group_level),
        permission_mgr=_Users(user_level),
    )
    return QQReplyDecisionNode(plugin)


def _group_request(*, is_at_bot: bool = False, is_reply_to_bot: bool = False) -> QQReplyRequest:
    return QQReplyRequest(
        message_text="在吗",
        is_group=True,
        group_id="1048307485",
        sender_id="2197648807",
        is_at_bot=is_at_bot,
        is_reply_to_bot=is_reply_to_bot,
    )


@pytest.mark.parametrize(
    ("is_at_bot", "is_reply_to_bot", "expected_action"),
    [
        (True, False, "reply"),    # 被 @
        (False, True, "reply"),    # 引用了她
        (True, True, "reply"),
        (False, False, "relay"),   # 没叫她 → 只转发给主人
    ],
)
def test_normal_group_replies_only_when_addressed(is_at_bot, is_reply_to_bot, expected_action):
    decision = _node("normal").decide(_group_request(is_at_bot=is_at_bot, is_reply_to_bot=is_reply_to_bot))
    assert decision.action == expected_action


def test_trusted_group_replies_without_being_addressed():
    """trusted 走注意力门控——门控在 dispatcher 层已放行，这里就该回。"""
    decision = _node("trusted").decide(_group_request())
    assert decision.action == "reply"
    assert decision.attention_gate_reason == "attention_gate"


def test_unknown_group_is_ignored():
    decision = _node("none").decide(_group_request(is_at_bot=True))
    assert decision.action == "ignore"
    assert decision.attention_gate_reason == "permission_none"


@pytest.mark.parametrize("user_level", ["none", "normal", "trusted"])
def test_private_always_replies_regardless_of_level(user_level):
    """私聊不存在「@ 才算叫我」——一律回，只把真实级别带下去。"""
    request = QQReplyRequest(message_text="在吗", is_group=False, sender_id="10001")
    decision = _node("normal", user_level).decide(request)
    assert decision.action == "reply"
    assert decision.permission_level == user_level


def test_group_permission_manager_no_longer_accepts_open_level():
    assert "open" not in GroupPermissionManager.VALID_LEVELS
    assert GroupPermissionManager.VALID_LEVELS == {"trusted", "normal"}


def test_legacy_open_level_is_promoted_to_trusted():
    """配置里残留的 "open" 会被自动升为 trusted（1048307485 就是这么升的）。"""
    mgr = GroupPermissionManager([{"group_id": "1048307485", "level": "open"}])
    assert mgr.get_group_level("1048307485") == "trusted"
    assert GroupPermissionManager([{"group_id": "X", "level": "truth"}]).get_group_level("X") == "trusted"


def test_add_group_has_no_open_probability_parameter():
    """open 概率参数已删除——留着它就会有人继续传，而它再也不会被读。"""
    params = inspect.signature(GroupPermissionManager.add_group).parameters
    assert "normal_relay_probability" in params
    assert "open_reply_probability" not in params
    assert not hasattr(GroupPermissionManager, "get_open_reply_probability")


def test_managers_survive_string_entries():
    """手写配置常见 ["123456"] 字符串条目——真机炸过一次 startup
    （'str' object has no attribute 'get'）。字符串按默认档收录、
    空串/None 跳过，绝不让一条脏数据把整个插件炸掉。"""
    users = PermissionManager(
        trusted_users=["123456", {"qq": "42", "level": "admin"}, None, "", 99]
    )
    assert users.get_permission_level("123456") == "trusted"
    assert users.get_permission_level("99") == "trusted"
    assert users.get_permission_level("42") == "admin"
    assert users.get_permission_level("777") == "none"

    groups = GroupPermissionManager(
        trusted_groups=["1048307485", {"group_id": "1", "level": "trusted"}, None, ""]
    )
    # 群的默认档现在是 trusted（见下一条测试），字符串条目也按默认档收录。
    assert groups.get_group_level("1048307485") == "trusted"
    assert groups.get_group_level("1") == "trusted"
    assert groups.get_group_level("2") == "none"


def test_group_level_defaults_to_trusted():
    """群名单「没写级别」＝ 信任群，只有显式写 normal 才是仅转发。

    `normal` 的语义是「没被 @ 就只按低概率转发给主人」，接近不说话；把它当默认档，
    用户加了群却看不到她开口（真机踩过这个静默默认）。漏写 / 写空 / 写错级别都不该
    把群降级成普通群 —— 想降级请显式写 ``"level": "normal"``。
    """
    from plugin.plugins.qq_auto_reply.group_permission import DEFAULT_GROUP_LEVEL

    assert DEFAULT_GROUP_LEVEL == "trusted"
    assert GroupPermissionManager([{"group_id": "1"}]).get_group_level("1") == "trusted"
    assert GroupPermissionManager([{"group_id": "1", "level": ""}]).get_group_level("1") == "trusted"
    assert GroupPermissionManager([{"group_id": "1", "level": "trused"}]).get_group_level("1") == "trusted"
    assert GroupPermissionManager(["1"]).get_group_level("1") == "trusted"
    assert GroupPermissionManager._normalize_level(None) == "trusted"
    assert GroupPermissionManager._normalize_level("") == "trusted"

    # 显式 normal 必须留着：它是「只在被 @ 时按概率转发」这条语义的唯一入口。
    assert GroupPermissionManager([{"group_id": "1", "level": "normal"}]).get_group_level("1") == "normal"
    assert GroupPermissionManager([{"group_id": "1", "level": "NORMAL"}]).get_group_level("1") == "normal"

    mgr = GroupPermissionManager()
    mgr.add_group("2")
    assert mgr.get_group_level("2") == "trusted"
    mgr.add_group("3", "normal")
    assert mgr.get_group_level("3") == "normal"
    assert mgr.list_groups() == [
        {"group_id": "2", "level": "trusted"},
        {"group_id": "3", "level": "normal"},
    ]
