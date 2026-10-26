"""接续摘要的**两端接线**：会话死的时候捕获、会话生的时候注入。

单测 `test_qq_session_handoff.py` 盯的是这个服务自己（内容、授权、时效、落盘），
这里盯的是它有没有真的被接上 —— 一个没接线的功能和一个坏掉的功能，表现一样：
"她还是会割裂"，而没有报错。
"""

from __future__ import annotations

import asyncio
import pathlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from plugin.plugins.qq_auto_reply.session_handoff_service import QQSessionHandoffService
from plugin.plugins.qq_auto_reply.session_runtime_service import QQSessionRuntimeService

KEY = "private:820040531"


class _Recorder:
    def __init__(self) -> None:
        self.infos: list[str] = []
        self.warnings: list[str] = []

    def info(self, msg, *a, **k): self.infos.append(str(msg))
    def warning(self, msg, *a, **k): self.warnings.append(str(msg))
    def error(self, msg, *a, **k): pass
    def exception(self, msg, *a, **k): pass
    def debug(self, msg, *a, **k): pass


class _Human:
    def __init__(self, content: str) -> None:
        self.content = content


class _AI:
    def __init__(self, content: str) -> None:
        self.content = content


class _Session:
    def __init__(self, history) -> None:
        self._conversation_history = list(history)
        self.closed = False

    async def close(self) -> None:
        self.closed = True


def _plugin(tmp_path: pathlib.Path, sessions: dict, *, settings: dict | None = None):
    log = _Recorder()
    handoff = QQSessionHandoffService(
        SimpleNamespace(logger=log, data_path=lambda name: tmp_path / name),
    )
    plugin = SimpleNamespace(
        logger=log,
        _user_sessions=sessions,
        session_handoff_service=handoff,
        _has_pending_session_settlement=lambda key: False,
        reply_buffer_service=None,
        _qq_settings=dict(settings or {}),
        session_memory_service=SimpleNamespace(
            finalize_user_memory_session=AsyncMock(return_value=True),
        ),
        _emit_log=lambda *a, **k: None,
    )
    return plugin


# ── 会话死：discard 时捕获 ────────────────────────────────────────

def test_discard_captures_a_handoff_note(tmp_path):
    session = _Session([_Human("你看得到这个嘛"), _AI("<msg>口误全票通过啦</msg>")])
    plugin = _plugin(tmp_path, {KEY: {
        "session": session,
        "memory_enabled": True,
        "her_name": "宅久皖萱",
        "is_group": False,
        "group_id": "",
    }})
    service = QQSessionRuntimeService(plugin)

    discarded = asyncio.run(service.discard_session(KEY, reason="route_changed"))

    assert discarded is True
    note = plugin.session_handoff_service.peek(KEY)
    assert note is not None, "会话没了却没留摘要 —— 下一句还是会割裂"
    assert "口误全票通过啦" in note["lines"][-1]


def test_discard_does_not_capture_for_an_unauthorized_session(tmp_path):
    plugin = _plugin(tmp_path, {KEY: {
        "session": _Session([_Human("hi"), _AI("喵")]),
        "memory_enabled": False,
        "her_name": "宅久皖萱",
    }})
    service = QQSessionRuntimeService(plugin)

    asyncio.run(service.discard_session(KEY, reason="route_changed"))

    assert plugin.session_handoff_service.peek(KEY) is None


def test_discard_that_keeps_the_session_does_not_capture(tmp_path):
    """结算失败时会话被有意保留（记忆完整性优先）：那时**不该**捕获 ——
    会话还在，下一轮可能继续用，摘要在复用路径上会变成"上一段"的错位上下文。"""
    session = _Session([_Human("hi"), _AI("喵")])
    plugin = _plugin(tmp_path, {KEY: {
        "session": session,
        "memory_enabled": True,
        "her_name": "宅久皖萱",
    }})
    plugin.session_memory_service.finalize_user_memory_session = AsyncMock(return_value=False)
    plugin._has_pending_session_settlement = lambda key: False
    service = QQSessionRuntimeService(plugin)

    discarded = asyncio.run(service.discard_session(KEY, reason="route_changed"))

    assert discarded is False
    assert KEY in plugin._user_sessions
    assert plugin.session_handoff_service.peek(KEY) is None


# ── 会话生：注入到 instructions，并消费掉 ─────────────────────────

class _FakeSessionClient:
    last: "_FakeSessionClient | None" = None

    def __init__(self, **_kwargs) -> None:
        self.instructions: str | None = None
        _FakeSessionClient.last = self

    async def connect(self, instructions: str, native_audio: bool = False) -> None:
        self.instructions = instructions

    async def close(self) -> None:
        return None


def _context(**overrides):
    """`ensure_generation_session` 会读一大堆 context 字段（建 created 字典时），
    缺一个就 AttributeError 被吞成"创建会话失败" —— 所以这里给全。"""
    base = {
        "system_prompt": "## 你是一个角色扮演大师",
        "her_name": "宅久皖萱",
        "character_card_fields": {},
        "login_self_id": "10001",
        "login_status": "online",
        "login_nickname": "皖萱",
        "is_group": False,
        "group_id": None,
        "sender_id": "820040531",
        "user_title": "宅久",
        "user_nickname": "宅久",
        "permission_level": "admin",
        "persist_memory": True,
        "memory_context_used": False,
        "ephemeral_session": False,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _bootstrap_patches(client_cls):
    """要打在使用侧的模块上：`session_bootstrap_service` 在模块级 `from … import`，
    打 `main_logic.omni_offline_client` 上的名字对它无效（那会真的去连网）。"""
    import plugin.plugins.qq_auto_reply.session_bootstrap_service as bootstrap_module

    cm = SimpleNamespace(
        get_model_api_config=lambda slot: {
            "base_url": "https://x/v1", "api_key": "k", "model": "m",
        },
        aensure_region_resolved=AsyncMock(),
    )
    return (
        patch.object(bootstrap_module, "OmniOfflineClient", client_cls),
        patch.object(bootstrap_module, "get_config_manager", lambda: cm),
    )


def test_new_session_gets_the_handoff_section(tmp_path):
    from plugin.plugins.qq_auto_reply.session_bootstrap_service import (
        QQSessionBootstrapService,
    )

    plugin = _plugin(tmp_path, {})
    plugin.session_handoff_service.capture(
        KEY,
        {
            "session": _Session([_Human("你看得到这个嘛"), _AI("口误全票通过啦")]),
            "memory_enabled": True,
            "her_name": "宅久皖萱",
            "is_group": False,
        },
    )
    plugin._ai_connect_timeout_seconds = 5.0
    service = QQSessionBootstrapService(plugin)

    patch_client, patch_cm = _bootstrap_patches(_FakeSessionClient)
    with patch_client, patch_cm:
        created = asyncio.run(service.ensure_generation_session(_context(), KEY))

    assert created is not None, "会话没建起来，后面都不用谈"
    instructions = _FakeSessionClient.last.instructions or ""
    assert "## 你是一个角色扮演大师" in instructions, "原系统提示词不能丢"
    assert "上一次对话" in instructions, "接续摘要没进新会话的启动上下文"
    assert "口误全票通过啦" in instructions
    assert plugin.session_handoff_service.peek(KEY) is None, "注入成功就该消费掉（一次性）"


def test_new_session_of_another_character_does_not_get_the_note(tmp_path):
    from plugin.plugins.qq_auto_reply.session_bootstrap_service import (
        QQSessionBootstrapService,
    )

    plugin = _plugin(tmp_path, {})
    plugin.session_handoff_service.capture(
        KEY,
        {
            "session": _Session([_Human("你看得到这个嘛"), _AI("口误全票通过啦")]),
            "memory_enabled": True,
            "her_name": "宅久皖萱",
            "is_group": False,
        },
    )
    plugin._ai_connect_timeout_seconds = 5.0
    service = QQSessionBootstrapService(plugin)

    patch_client, patch_cm = _bootstrap_patches(_FakeSessionClient)
    with patch_client, patch_cm:
        asyncio.run(service.ensure_generation_session(_context(her_name="YUI"), KEY))

    assert "上一次对话" not in (_FakeSessionClient.last.instructions or ""), (
        "换人格后把上个角色的临场上下文交给新角色"
    )
    assert plugin.session_handoff_service.peek(KEY, her_name="宅久皖萱") is not None, (
        "只是不注入，不该把条目删掉（换回来还能用）"
    )


def test_connect_failure_keeps_the_note(tmp_path):
    """会话没连上（超时/报错）时整轮作废重试：摘要不该在那时被消费掉。"""
    from plugin.plugins.qq_auto_reply.session_bootstrap_service import (
        QQSessionBootstrapService,
    )

    class _Failing(_FakeSessionClient):
        async def connect(self, instructions: str, native_audio: bool = False) -> None:
            raise RuntimeError("connect failed")

    plugin = _plugin(tmp_path, {})
    plugin.session_handoff_service.capture(
        KEY,
        {
            "session": _Session([_Human("你看得到这个嘛"), _AI("口误全票通过啦")]),
            "memory_enabled": True,
            "her_name": "宅久皖萱",
        },
    )
    plugin._ai_connect_timeout_seconds = 5.0
    service = QQSessionBootstrapService(plugin)

    patch_client, patch_cm = _bootstrap_patches(_Failing)
    with patch_client, patch_cm:
        created = asyncio.run(service.ensure_generation_session(_context(), KEY))

    assert created is None
    assert plugin.session_handoff_service.peek(KEY) is not None, "连不上不该丢掉摘要"


@pytest.mark.parametrize("reason", ["prompt_override_changed", "identity_changed"])
def test_prompt_and_identity_discards_also_capture(tmp_path, reason):
    """换提示词 / 换登录身份也走 discard：同样要留摘要（否则那些路径仍会割裂）。"""
    plugin = _plugin(tmp_path, {KEY: {
        "session": _Session([_Human("hi"), _AI("喵")]),
        "memory_enabled": True,
        "her_name": "宅久皖萱",
    }})
    service = QQSessionRuntimeService(plugin)

    asyncio.run(service.discard_session(KEY, reason=reason))

    assert plugin.session_handoff_service.peek(KEY) is not None
