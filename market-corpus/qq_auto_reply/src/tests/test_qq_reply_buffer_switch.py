"""回复缓冲的两个独立开关（群聊 / 私聊）。

缓冲动机不同 —— 群聊是为了不逐条抢话，私聊是为了等对方把话说完 —— 所以分开控制，
而不是一个总开关。**缺键一律按"开"**：老配置里根本没这两个键，按关处理会让升级后
所有人的回复突然变成无延迟，那不是开关该带来的变化。
"""
from __future__ import annotations

from pathlib import Path

from plugin.plugins.qq_auto_reply.config_store import QQAutoReplyConfigStore
from plugin.plugins.qq_auto_reply.reply_buffer_service import QQReplyBufferService as S

# ── 缺键按开 ────────────────────────────────────────────────

def test_defaults_to_enabled_when_key_missing():
    """老配置里没有这两个键 —— 默认开才与历史行为一致。"""
    for settings in ({}, None):
        assert S.is_enabled(settings, is_group=True) is True
        assert S.is_enabled(settings, is_group=False) is True


# ── 两个开关互不影响 ────────────────────────────────────────

def test_group_off_leaves_private_on():
    settings = {S.GROUP_ENABLED_KEY: False}
    assert S.is_enabled(settings, is_group=True) is False
    assert S.is_enabled(settings, is_group=False) is True


def test_private_off_leaves_group_on():
    settings = {S.PRIVATE_ENABLED_KEY: False}
    assert S.is_enabled(settings, is_group=False) is False
    assert S.is_enabled(settings, is_group=True) is True


def test_both_can_be_off():
    settings = {S.GROUP_ENABLED_KEY: False, S.PRIVATE_ENABLED_KEY: False}
    assert S.is_enabled(settings, is_group=True) is False
    assert S.is_enabled(settings, is_group=False) is False


def test_only_a_missing_key_counts_as_on():
    """配置从前端来，可能是 0 / "" / None —— 显式关掉就得算关。"""
    for falsy in (False, 0, "", None):
        assert S.is_enabled({S.GROUP_ENABLED_KEY: falsy}, is_group=True) is False
        assert S.is_enabled({S.PRIVATE_ENABLED_KEY: falsy}, is_group=False) is False


def test_truthy_values_enable():
    for truthy in (True, 1, "1", "true"):
        assert S.is_enabled({S.GROUP_ENABLED_KEY: truthy}, is_group=True) is True


# ── 默认值落在配置层 ────────────────────────────────────────

def test_default_config_ships_both_keys_defaulting_true(tmp_path: Path):
    """默认值必须真的写进 config_store：界面读的是 dashboard 的 settings，
    而 dashboard 的取值口径依赖这两个键存在。"""
    cfg = QQAutoReplyConfigStore(tmp_path).default_config()

    assert cfg[S.GROUP_ENABLED_KEY] is True
    assert cfg[S.PRIVATE_ENABLED_KEY] is True
