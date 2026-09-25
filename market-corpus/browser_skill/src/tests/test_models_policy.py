from __future__ import annotations

import json

import pytest
from plugin.plugins.browser_skill.runtime.models import (
    ClickAction,
    FillAction,
    NavigateAction,
    PressAction,
    RuntimeSettings,
    ScrollAction,
    action_schema_json,
    parse_agent_action,
)
from plugin.plugins.browser_skill.runtime.policy import (
    PolicyViolation,
    additional_agent_tab_requested,
    critical_press_has_grounded_target,
    grounded_critical_press_target,
    http_link_requires_confirmation,
    http_url_confirmation_query,
    http_url_requires_confirmation,
    is_search_fill,
    is_sensitive_fill,
    observed_controls_match,
    observed_target_control,
    requested_agent_tab_count,
    requires_critical_confirmation,
    requires_persistent_session,
    user_tab_requested,
    validate_action,
    validate_http_url,
)
from pydantic import ValidationError


@pytest.mark.parametrize(
    "url",
    ["chrome://settings", "edge://flags", "file:///tmp/a", "javascript:alert(1)"],
)
def test_rejects_non_http_navigation(url: str) -> None:
    with pytest.raises(PolicyViolation, match="http/https"):
        validate_http_url(url)


def test_rejects_credentials_embedded_in_url() -> None:
    with pytest.raises(PolicyViolation, match="认证信息"):
        validate_action(NavigateAction(action="navigate", url="https://alice:secret@example.com"))


def test_parser_accepts_fenced_json_but_rejects_unknown_action() -> None:
    action = parse_agent_action(
        '```json\n{"action":"navigate","url":"https://example.com"}\n```'
    )
    assert isinstance(action, NavigateAction)
    with pytest.raises(ValidationError):
        parse_agent_action('{"action":"evaluate","script":"document.cookie"}')


def test_scroll_action_is_bounded_to_one_page() -> None:
    action = parse_agent_action(
        '{"action":"scroll","direction":"down","pages":1,"until":"下一条结果"}'
    )
    assert isinstance(action, ScrollAction)
    assert action.pages == 1
    with pytest.raises(ValidationError):
        parse_agent_action('{"action":"scroll","direction":"down","pages":2}')
    with pytest.raises(ValidationError):
        parse_agent_action('{"action":"scroll","direction":"bottom","pages":1}')


def test_agent_prompt_schema_is_compact_without_losing_constraints() -> None:
    schema_text = action_schema_json()
    schema = json.loads(schema_text)

    assert len(schema_text) < 10_000
    request_help = schema["$defs"]["RequestHelpAction"]["properties"]
    assert "title" in request_help
    assert request_help["prompt"]["maxLength"] == 2000
    assert "default" not in request_help["help_kind"]


def test_password_fill_is_always_human_work() -> None:
    action = FillAction(action="fill", target="@e7", value="must-not-be-sent")
    assert is_sensitive_fill(action, 'textbox "Password" @e7')


def test_sensitive_fill_uses_reason_only_to_escalate_an_observed_target() -> None:
    action = FillAction(
        action="fill",
        target="@e7",
        value="ordinary nickname",
        reason="Continue the password setup workflow",
    )
    assert is_sensitive_fill(action, 'textbox "昵称" @e7')
    assert not is_sensitive_fill(action, 'textbox "Password" @e8')


def test_fill_submit_is_limited_to_observed_search_fields() -> None:
    action = FillAction(action="fill", target="@e7", value="cats", submit=True)
    assert is_search_fill(action, 'textbox "搜索" @e7')
    assert not is_search_fill(action, 'textbox "昵称" @e7')
    assert not is_search_fill(
        FillAction(
            action="fill",
            target="@e7",
            value="cats",
            submit=True,
            reason="Search from this field",
        ),
        'textbox "昵称" @e7',
    )


def test_critical_click_requires_confirmation() -> None:
    action = ClickAction(action="click", target="@e9", reason="提交订单")
    assert requires_critical_confirmation(action, "购买商品", 'button "提交订单" @e9')
    assert not requires_critical_confirmation(
        ClickAction(action="click", target="@e13", reason="Submit search"),
        "Search BrowserSkill",
        'button "Search" @e13',
    )
    assert requires_critical_confirmation(
        ClickAction(action="click", target="@e13"),
        "Search BrowserSkill",
        'textbox "Search" @e12\nbutton "Submit" @e13',
    )
    assert requires_critical_confirmation(
        ClickAction(action="click", target="@e13"),
        "Search BrowserSkill",
        'textbox "Search" @e12\ntextbox "Cardholder" @e20\nbutton "Submit" @e13',
    )
    assert not requires_critical_confirmation(
        ClickAction(action="click", target="@e13", reason="提交订单"),
        "搜索 BrowserSkill",
        'button "搜索" @e13',
    )
    assert requires_critical_confirmation(
        ClickAction(action="click", target="@e9"),
        "购买这个商品",
        'button "确认" @e9',
    )


def test_observed_ref_matching_does_not_confuse_numeric_prefixes() -> None:
    observation = 'textbox "Search @e1 example" @e10; button "Delete" @e1'
    assert observed_target_control("@e1", observation) == 'button "Delete" @e1'
    assert observed_target_control("@e10", observation) == 'textbox "Search @e1 example" @e10'
    assert requires_critical_confirmation(
        ClickAction(action="click", target="@e1"),
        "删除这条内容",
        observation,
    )
    assert not requires_critical_confirmation(
        ClickAction(action="click", target="button[data-action=delete]"),
        "删除这条内容",
        observation,
    )


def test_confirmed_control_matching_uses_semantic_identity() -> None:
    before = 'button "Submit order" [focused] @e9'
    assert observed_controls_match(
        "@e9",
        before,
        'button "Submit order" [disabled] @e9',
    )
    assert not observed_controls_match(
        "@e9",
        before,
        'button "Delete account" @e9',
    )


def test_http_link_confirmation_preserves_document_navigation_exemption() -> None:
    assert not http_link_requires_confirmation(
        ClickAction(action="click", target="@e4"),
        "打开删除账户的帮助文档",
        'link "Delete account documentation" @e4',
        "https://example.com/docs/delete-account",
    )
    assert not http_link_requires_confirmation(
        ClickAction(action="click", target="@e4"),
        "打开账户页面",
        'link "Account information" @e4',
        "https://example.com/docs/delete-account",
    )
    assert http_link_requires_confirmation(
        ClickAction(action="click", target="@e4"),
        "删除这个项目",
        'link "Delete" @e4',
        "https://example.com/items/delete",
    )
    assert http_link_requires_confirmation(
        ClickAction(action="click", target="@e4"),
        "退出登录",
        'link "Logout" @e4',
        "https://example.com/account?action=logout",
    )
    assert http_link_requires_confirmation(
        ClickAction(action="click", target="@e4"),
        "管理账户",
        'link "Account settings" @e4',
        "https://example.com/account/delete-account",
    )
    assert http_link_requires_confirmation(
        ClickAction(action="click", target="@e4"),
        "管理成员",
        'link "Member settings" @e4',
        "https://example.com/users/remove_member",
    )
    assert http_link_requires_confirmation(
        ClickAction(action="click", target="@e4"),
        "管理账户",
        'link "Account settings" @e4',
        "https://example.com/account?action=delete-account",
    )
    assert not http_link_requires_confirmation(
        ClickAction(action="click", target="@e4"),
        "阅读注销账户政策",
        'link "Delete account policy" @e4',
        "https://example.com/account/delete-account-policy",
    )
    assert http_link_requires_confirmation(
        ClickAction(action="click", target="@e4"),
        "阅读注销账户政策",
        'link "Delete account policy" @e4',
        "https://example.com/account?action=delete-account-policy",
    )
    assert http_link_requires_confirmation(
        ClickAction(action="click", target="@e4"),
        "管理文章",
        'link "Article settings" @e4',
        "https://example.com/admin/delete-article",
    )
    assert http_link_requires_confirmation(
        ClickAction(action="click", target="@e4"),
        "管理工单",
        'link "Ticket settings" @e4',
        "https://example.com/tickets/delete-support-ticket",
    )
    assert http_link_requires_confirmation(
        ClickAction(action="click", target="@e4"),
        "管理工单",
        'link "Ticket settings" @e4',
        "https://example.com/tickets?action=delete-support-ticket",
    )
    assert http_link_requires_confirmation(
        ClickAction(action="click", target="@e4"),
        "管理工单",
        'link "Ticket settings" @e4',
        "https://example.com/support/delete-ticket",
    )


def test_direct_http_url_confirmation_cannot_be_bypassed_by_action_type() -> None:
    assert http_url_requires_confirmation("https://example.com/account/logout")
    assert http_url_requires_confirmation("https://example.com/account/confirm-order")
    assert http_url_requires_confirmation("https://example.com/chat?action=send-message")
    assert not http_url_requires_confirmation("https://example.com/docs/delete-account")
    assert not http_url_requires_confirmation("https://example.com/account/profile")


def test_direct_http_url_confirmation_query_hint_is_informative_but_redacted() -> None:
    assert (
        http_url_confirmation_query(
            "https://example.com/account?action=logout&token=secret-value"
        )
        == "action=logout"
    )
    assert (
        http_url_confirmation_query("https://example.com/account?delete=1")
        == "delete=<redacted>"
    )
    assert http_url_confirmation_query("https://example.com/account?token=secret-value") == ""


def test_targetless_enter_confirms_only_with_clear_consequential_control() -> None:
    action = PressAction(action="press", key="Enter")
    assert requires_critical_confirmation(
        action,
        "发送这条消息",
        'textbox "给 DeepSeek 发送消息" [focused] @e111',
    )
    assert critical_press_has_grounded_target(
        action,
        "发送这条消息",
        'textbox "给 DeepSeek 发送消息" [focused] @e111',
    )
    assert grounded_critical_press_target(
        action,
        "发送这条消息",
        'textbox "给 DeepSeek 发送消息" [focused] @e111',
    ) == "@e111"
    assert not critical_press_has_grounded_target(
        action,
        "发送这条消息",
        'textbox "Type [focused] for help" @e111',
    )
    assert not critical_press_has_grounded_target(
        action,
        "发送这条消息",
        'textbox "给 DeepSeek 发送消息" @e111',
    )
    assert not critical_press_has_grounded_target(
        action,
        "发送这条消息",
        'textbox "消息" @e111\ntextbox "收件人" @e112',
    )
    assert not critical_press_has_grounded_target(
        action,
        "发送这条消息",
        'textbox "消息" @e205',
        recent_fill_target="@e111",
    )
    assert critical_press_has_grounded_target(
        action,
        "搜索 BrowserSkill",
        'textbox "搜索" @e12',
    )
    assert not requires_critical_confirmation(
        action,
        "发送这条消息",
        'textbox "给 DeepSeek 发送消息" @e111',
    )
    assert not requires_critical_confirmation(
        action,
        "搜索 BrowserSkill",
        'textbox "搜索" @e12',
    )
    assert not requires_critical_confirmation(
        PressAction(action="press", key="Enter", reason="Submit the search query"),
        "Search BrowserSkill",
        'textbox "Search" @e12',
    )
    assert not requires_critical_confirmation(
        action,
        "阅读购买政策",
        'link "购买政策" @e4\ntextbox "站内搜索" @e12',
    )
    assert not requires_critical_confirmation(
        PressAction(action="press", key="Enter", target="@e12"),
        "搜索 BrowserSkill 后发送给我结果",
        'textbox "搜索" @e12\ntextbox "发送消息" @e111',
    )
    assert not requires_critical_confirmation(
        action,
        "搜索 BrowserSkill 后发送给我结果",
        'textbox "搜索" @e12\ntextbox "发送消息" @e111',
    )
    assert requires_critical_confirmation(
        PressAction(action="press", key="Enter", target="@e111"),
        "发送这条消息",
        'textbox "消息" @e111',
    )
    assert requires_critical_confirmation(
        PressAction(action="press", key="Enter", target="@e9"),
        "购买这个商品",
        'button "Purchase" [focused] @e9',
    )
    assert requires_critical_confirmation(
        action,
        "发送这条消息",
        'textbox "消息" @e111',
        recent_fill_target="@e111",
    )
    assert not requires_critical_confirmation(
        action,
        "发送这条消息",
        'textbox "消息" @e205\ntextbox "收件人" @e206',
        recent_fill_target="@e111",
    )
    assert not requires_critical_confirmation(
        action,
        "搜索 BrowserSkill 后发送给我结果",
        'textbox "搜索" @e12\ntextbox "消息" @e111',
        recent_fill_target="@e12",
        recent_fill_was_search=True,
    )
    assert requires_critical_confirmation(
        action,
        "把这条消息发出去",
        'textbox "消息" @e111',
        recent_fill_target="@e111",
    )
    assert requires_critical_confirmation(
        action,
        "发个消息",
        'textbox "消息" @e111',
        recent_fill_target="@e111",
    )
    assert requires_critical_confirmation(
        action,
        "Send hello",
        'textbox "Message" @e111',
        recent_fill_target="@e111",
    )
    assert not requires_critical_confirmation(
        action,
        "Search hello",
        'textbox "Search" @e12',
        recent_fill_target="@e12",
        recent_fill_was_search=True,
    )


@pytest.mark.parametrize(
    "instruction",
    ["操作我已经打开的页面", "请在 current tab 中继续", "使用现有标签完成任务"],
)
def test_explicit_user_tab_intent(instruction: str) -> None:
    assert user_tab_requested(instruction)


def test_media_playback_requires_persistent_session() -> None:
    assert requires_persistent_session("请播放这个视频")
    assert requires_persistent_session("watch the live stream")
    assert not requires_persistent_session("停止播放视频")


def test_additional_agent_tab_requires_explicit_user_intent() -> None:
    assert additional_agent_tab_requested("请在新标签页打开结果")
    assert additional_agent_tab_requested("在另一个标签页里搜索猫娘计划")
    assert additional_agent_tab_requested("分别在两个标签页打开这两个页面")
    assert additional_agent_tab_requested("open the result in a new tab")
    assert not additional_agent_tab_requested("打开结果页面")
    assert not additional_agent_tab_requested("不要打开新标签页")
    assert requested_agent_tab_count("在另一个标签页打开 GitHub") == 2
    assert requested_agent_tab_count("分别在三个标签页打开页面") == 3
    assert requested_agent_tab_count("open both pages in 2 tabs") == 2
    assert additional_agent_tab_requested("总共 2 个标签页")
    assert requested_agent_tab_count("总共 2 个标签页") == 2


def test_runtime_defaults_to_one_plugin_owned_agent_window() -> None:
    settings = RuntimeSettings()
    assert settings.session_scope == "plugin"
    assert settings.release_control_when_idle is True
    assert settings.reuse_existing_window is True
    assert settings.allow_additional_agent_tabs is False
    assert settings.planner_max_completion_tokens == 1200
    assert settings.planner_correction_max_completion_tokens == 1600
    assert settings.snapshot_max_tokens == 8000
    assert settings.scroll_max_pages == 1
    assert settings.scroll_snapshot_max_tokens == 2000
    assert settings.live_page_max_chars == 1200


def test_navigation_defaults_to_dom_content_loaded() -> None:
    action = NavigateAction(action="navigate", url="https://www.bing.com/search?q=neko")
    assert action.wait_until == "domcontentloaded"
