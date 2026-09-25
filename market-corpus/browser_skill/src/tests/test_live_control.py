from __future__ import annotations

from plugin.plugins.browser_skill import (
    _browser_session_key,
    _hud_live_status_text,
    _live_status_payload,
)
from plugin.plugins.browser_skill.runtime.control import BrowserTaskController
from plugin.plugins.browser_skill.runtime.models import BrowserTaskResult
from plugin.sdk.shared.core.push_message_schema import translate_push_message


def test_controller_reports_safe_live_status_and_terminal_result() -> None:
    controller = BrowserTaskController()
    control = controller.start(
        conversation_id="chat-1",
        goal="搜索资料",
        original_request="搜索资料",
    )
    control.update_progress(stage="acting", message="正在执行 click", step=3, action_limit=20)
    control.update_url("https://user:secret@example.com/path?q=private#fragment")
    update, duplicate = control.submit("append", "只看官方来源")
    duplicate_update, is_duplicate = control.submit("append", "只看官方来源")

    status = controller.status("chat-1")
    assert status["active"] is True
    assert status["current_url"] == "https://example.com/path"
    assert status["pending_updates"] == 1
    assert update.revision == 1 and not duplicate
    assert duplicate_update.revision == update.revision and is_duplicate

    controller.finish(
        control,
        BrowserTaskResult(
            success=True,
            status="completed",
            summary="完成",
            session_state="kept",
        ),
    )
    terminal = controller.status("chat-1")
    assert terminal["active"] is False
    assert terminal["terminal_status"] == "completed"


def test_controller_exposes_safe_continuation_checkpoint() -> None:
    controller = BrowserTaskController()
    control = controller.start(
        conversation_id="chat-1",
        goal="长任务",
        original_request="完成长任务",
        action_limit=20,
    )
    controller.finish(
        control,
        BrowserTaskResult(
            success=False,
            status="needs_user",
            summary="已到达检查点",
            session_state="kept",
            continuation_available=True,
            session_decision_required=True,
        ),
    )

    status = _live_status_payload(controller.status("chat-1"))
    assert status["terminal_status"] == "needs_user"
    assert status["continuation_available"] is True
    assert status["session_decision_required"] is True
    assert status["session_state"] == "kept"


def test_live_context_payload_excludes_free_text_and_page_content() -> None:
    payload = _live_status_payload(
        {
            "active": True,
            "stage": "acting",
            "step": 3,
            "action_limit": 20,
            "current_action": "click",
            "current_url": "https://example.com/path",
            "goal": "搜索官方资料",
            "goal_revision": 1,
            "pending_updates": 0,
            "message": "页面正文不应进入上下文",
            "summary": "模型生成的总结也不应进入实时状态",
            "page": {"observation": "secret page body"},
            "page_text": "secret page body",
        }
    )
    assert payload["stage"] == "acting"
    assert payload["action_limit"] == 20
    assert payload["goal_revision"] == 1
    assert "message" not in payload
    assert "summary" not in payload
    assert "page" not in payload
    assert "page_text" not in payload

    explicit = _live_status_payload(
        {"stage": "idle", "page": {"content_trust": "untrusted_page_data"}},
        include_page=True,
    )
    assert explicit["page"] == {"content_trust": "untrusted_page_data"}


def test_live_context_uses_passive_hidden_coalesced_push_message() -> None:
    wire = translate_push_message(
        source="browser_skill.live_status",
        visibility=[],
        ai_behavior="read",
        parts=[{"type": "text", "text": "safe status"}],
        coalesce_key="browser_skill.live_status:abc123",
    )
    assert wire["visibility"] == []
    assert wire["ai_behavior"] == "read"
    assert wire["coalesce_key"] == "browser_skill.live_status:abc123"
    assert wire["parts"] == [{"type": "text", "text": "safe status"}]


def test_hud_progress_is_short_visible_and_blind() -> None:
    text = _hud_live_status_text(
        {
            "active": True,
            "stage": "acting",
            "step": 3,
            "current_action": "click",
            "message": "untrusted verbose message",
            "page": {"text": "secret page body"},
        }
    )
    assert text == "浏览器任务：正在点击页面控件 · 已执行 3 个动作"
    assert "untrusted" not in text and "secret" not in text
    assert _hud_live_status_text(
        {"stage": "waiting_for_user", "terminal_status": "needs_user", "step": 12}
    ) == "浏览器任务：需要用户继续处理"

    wire = translate_push_message(
        source="browser_skill.live_status.hud",
        visibility=["hud"],
        ai_behavior="blind",
        parts=[{"type": "text", "text": text}],
        coalesce_key="browser_skill.live_status.hud:abc123",
    )
    assert wire["visibility"] == ["hud"]
    assert wire["ai_behavior"] == "blind"
    assert wire["coalesce_key"] == "browser_skill.live_status.hud:abc123"


def test_session_key_falls_back_to_stable_lanlan_scope() -> None:
    context = {"conversation_id": "chat-1", "lanlan_name": "Neko"}
    assert _browser_session_key(context, scope="conversation") == "chat-1"
    first = _browser_session_key(context)
    second = _browser_session_key({"conversation_id": "chat-2", "lanlan_name": "Neko"})
    assert first == second
    assert first is not None and first.startswith("lanlan:")
    assert _browser_session_key({}) is None
