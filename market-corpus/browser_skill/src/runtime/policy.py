"""Safety policy independent from model-generated claims."""

from __future__ import annotations

import re
from urllib.parse import parse_qsl, unquote, urlsplit

from .models import AgentAction, ClickAction, FillAction, PressAction, TabCreateAction

_CRITICAL_PATTERNS = (
    r"提交订单|立即购买|确认购买|付款|支付|转账|充值|发送消息|发送邮件|发表评论",
    r"发布|删除|注销|修改密码|更改密码|授权|授予权限|提交表单|下载文件",
    r"\b(pay|purchase|buy now|place order|checkout|transfer|send|publish|post|delete|remove)\b",
    r"\b(change password|grant access|authorize|submit|download|install)\b",
)
_SENSITIVE_PATTERNS = (
    r"密码|口令|验证码|短信码|动态码|支付密码|安全码|银行卡|信用卡|身份证",
    r"\b(password|passwd|passcode|otp|captcha|verification code|security code)\b",
    r"\b(card number|credit card|cvv|cvc|social security|ssn)\b",
)
_EXPLICIT_USER_TAB_PATTERNS = (
    r"当前(标签|页面)|已经打开|我打开的|现有标签|用户标签",
    r"\b(current tab|open tab|existing tab|tab I opened|my tab)\b",
)
_ADDITIONAL_AGENT_TAB_PATTERNS = (
    r"新标签页|新标签|另开(?:一个)?标签|另(?:一|一个)标签页|第[二三四五六七八九十\d]+\s*个?标签页|[两二三四五六七八九十\d]+\s*个?标签页|多个标签|分别.*标签页|保留当前页面.*(?:另开|新开)",
    r"\b(new|another|additional|multiple|separate) tabs?\b|"
    r"\bopen .* in (?:a )?new tab\b|\b\d+ tabs?\b",
)
_NEGATED_ADDITIONAL_TAB_PATTERNS = (
    r"(?:不要|无需|不必|禁止|(?<!分)别)(?:再|使用|打开|创建|新建)?[^。；，,.]{0,8}(?:新|另|额外|多个|标签页)",
    r"\b(?:do not|don't|without|no need to)\b[^.]{0,24}\b(?:new|another|additional|multiple) tabs?\b",
)
_ZH_TAB_NUMBERS = {
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
}
_PERSISTENT_MEDIA_PATTERNS = (
    r"播放|放一?(?:首|个|段)?(?:歌|音乐|视频|电影)|看(?:视频|电影|直播)|听(?:歌|音乐)|直播|连续播放",
    r"\b(play|watch|listen(?: to)?|stream)\b.*\b(video|movie|music|song|audio|live|stream)?\b",
    r"\bkeep (?:the )?(?:video|music|audio|stream|browser) (?:playing|open)\b",
)
_STOP_MEDIA_PATTERNS = (
    r"停止播放|暂停播放|关掉(?:视频|音乐|播放器)|关闭播放",
    r"\b(stop|pause|close)\b.*\b(video|music|audio|playback|stream)\b",
)
_SEARCH_FIELD_PATTERNS = (
    r"搜索|搜一搜|查询|检索|查找",
    r"\b(search|query|find)\b",
)
_TEXT_ENTRY_CONTROL_PATTERN = re.compile(
    r"(?i)(?:\b(?:textbox|searchbox|combobox)\b|文本框|搜索框|组合框)"
)
_CONSEQUENTIAL_TEXT_ENTRY_PATTERNS = (
    r"消息|邮件|评论|回复|聊天|帖子|订单|表单|申请|转账|付款|支付",
    r"\b(?:message|email|comment|reply|chat|post|order|form|application|payment|transfer)\b",
)
_FINALIZATION_CONTROL_PATTERNS = (
    r"确认|提交|发送|支付|付款|购买|下单|删除|发布|下载|授权|转账|充值",
    r"\b(?:confirm|submit|send|pay|purchase|buy|order|delete|publish|post|download|authorize|transfer)\b",
)
_ACTIVATION_KEYS = {"enter", "return", "numpadenter"}
_CRITICAL_FINALIZATION_INTENT_PATTERNS = (
    r"(?:发送|提交|支付|付款|购买|下单|删除|发布|下载|授权|转账|充值)"
    r".{0,12}(?:消息|邮件|评论|回复|订单|表单|文件|权限|款项|商品|内容|帖子|申请)",
    r"^(?:请|帮我|替我|现在)?\s*(?:支付|付款|购买|下单|删除|发布|下载|授权|转账|充值)(?:吧|。)?$",
    r"\b(?:send|submit|pay(?:\s+for)?|buy|delete|publish|post|download|authorize|transfer)\b"
    r"(?:\s+(?:this|the|that|my|a|an))?\s+"
    r"(?:message|email|comment|reply|order|form|file|payment|item|product|post|application)\b",
    r"\b(?:place|confirm)\s+(?:this|the|that|my|a|an)?\s*(?:order|payment|purchase)\b",
    r"\bpurchase\s+(?:this|the|that|my|a|an)\s+(?:item|product|order)\b",
    r"^(?:please\s+)?(?:send|submit|pay|buy|delete|publish|post|download|authorize|transfer)\s+"
    r"(?:it|this|that)(?:\s+now)?[.!]?$",
    r"^(?:请|帮我|替我|现在)?\s*(?:发送|提交|支付|付款|购买|下单|删除|发布|下载|授权|转账|充值)"
    r"(?:它|这个|这条|该项|出去)(?:吧|。)?$",
)
_CRITICAL_ENTER_INTENT_PATTERNS = _CRITICAL_FINALIZATION_INTENT_PATTERNS + (
    r"(?:把|将).{0,16}(?:消息|邮件|评论|回复|订单|表单|文件|权限|款项|商品|内容|帖子|申请)"
    r".{0,12}(?:发(?:送|出去)?|提交|支付|付款|购买|下单|删除|发布|下载|授权|转账|充值)",
    r"(?:发|发送)(?:个|一条|这条|该条|一封)?(?:消息|邮件|评论|回复)",
    r"\b(?:send|submit|pay|buy|delete|publish|post|download|authorize|transfer)\b"
    r"(?:\s+\S+){1,12}",
)
_INFORMATIONAL_LINK_PATTERNS = (
    r"帮助|文档|指南|说明|政策|教程|常见问题|了解更多|查看详情|阅读",
    r"\b(?:help|docs?|documentation|guide|manual|policy|tutorial|faq|learn more|read more)\b",
)
_INFORMATIONAL_PATH_SEGMENTS = {
    "article",
    "articles",
    "blog",
    "doc",
    "docs",
    "documentation",
    "faq",
    "guide",
    "guides",
    "help",
    "learn",
    "manual",
    "policy",
    "support",
    "tutorial",
}
_INFORMATIONAL_COMPOSITE_MARKERS = {
    "doc",
    "docs",
    "documentation",
    "faq",
    "guide",
    "guides",
    "help",
    "manual",
    "policy",
    "tutorial",
}
_SIDE_EFFECT_PATH_SEGMENTS = {
    "authorize",
    "buy",
    "cancel",
    "checkout",
    "confirm",
    "deactivate",
    "delete",
    "destroy",
    "download",
    "grant",
    "install",
    "logout",
    "pay",
    "publish",
    "purchase",
    "remove",
    "revoke",
    "save",
    "send",
    "signout",
    "submit",
    "transfer",
    "unsubscribe",
    "upload",
}
_ACTION_QUERY_KEYS = frozenset(
    {"action", "command", "do", "method", "operation", "task"}
)
_SAFE_QUERY_VALUE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._~-]{0,47}$")


class PolicyViolation(ValueError):
    def __init__(self, code: str, message: str, *, replan_hint: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.replan_hint = str(replan_hint or "").strip()


def validate_http_url(url: str) -> str:
    value = str(url or "").strip()
    try:
        parsed = urlsplit(value)
    except ValueError as exc:
        raise PolicyViolation("ACTION_REJECTED", "网址格式无效") from exc
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        raise PolicyViolation("ACTION_REJECTED", "仅允许访问 http/https 页面")
    if parsed.username is not None or parsed.password is not None:
        raise PolicyViolation("ACTION_REJECTED", "网址中不得包含认证信息")
    return value


def _observation_controls(observation: str) -> list[str]:
    controls: list[str] = []
    for line in str(observation or "").splitlines():
        controls.extend(part.strip() for part in line.split(";") if part.strip())
    return controls


def observed_target_control(target: str | None, observation: str) -> str:
    if not target:
        return ""
    target = target.strip()
    ref_match = re.fullmatch(r"@?e(\d+)", target, flags=re.IGNORECASE)
    if ref_match:
        normalized_ref = f"@e{ref_match.group(1)}"
        ref_pattern = re.compile(
            rf"(?<![\w@]){re.escape(normalized_ref)}(?!\w)",
            flags=re.IGNORECASE,
        )
        for control in _observation_controls(observation):
            if ref_pattern.search(_observed_control_structure(control)):
                return control[:1000]
        return ""
    return ""


def observed_controls_match(
    target: str | None,
    before_observation: str,
    after_observation: str,
) -> bool:
    """Return whether a ref still names the same observed semantic control."""
    before = observed_target_control(target, before_observation)
    after = observed_target_control(target, after_observation)
    if not before or not after:
        return False

    def signature(value: str) -> str:
        value = re.sub(r"(?i)(?<![\w@])@e\d+(?!\w)", " ", value)
        # Focus/value/state markers may legitimately change while the user is
        # confirming. The stable role and accessible name define identity.
        value = re.sub(r"\[[^\]\r\n]*\]", " ", value)
        return re.sub(r"\s+", " ", value).strip().casefold()

    return signature(before) == signature(after)


def _observed_matching_line(target: str | None, observation: str) -> str:
    return observed_target_control(target, observation)


def _observed_control_structure(control_line: str) -> str:
    """Remove page-authored accessible names before reading snapshot state."""
    return re.sub(r'"(?:\\.|[^"\\])*"', '""', str(control_line or ""))


def _observed_control_is_focused(control_line: str) -> bool:
    """判断页面快照是否明确标记该控件当前获得焦点。"""
    structure = _observed_control_structure(control_line)
    return bool(
        re.search(
            r"(?i)(?:\[\s*focused\s*\]|\bfocused\s*[:=]\s*(?:true|yes|1)\b)",
            structure,
        )
    )


def _observed_control_ref(control_line: str) -> str:
    structure = _observed_control_structure(control_line)
    matches = re.findall(r"(?i)(?<![\w@])@e\d+(?!\w)", structure)
    return matches[-1] if matches else ""


def _matches(patterns: tuple[str, ...], text: str) -> bool:
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns)


def is_sensitive_fill(action: FillAction, observation: str) -> bool:
    # ``reason`` 不能授权动作，但可以保守地把一个已观察到的目标升级为
    # 人工处理。这样即使密码框的可访问名称是通用的，也不会自动发送秘密。
    target_line = _observed_matching_line(action.target, observation)
    return bool(
        target_line
        and _matches(_SENSITIVE_PATTERNS, f"{target_line} {action.reason}")
    )


def is_search_fill(action: FillAction, observation: str) -> bool:
    # submit=True performs an Enter press, so only the observed control may
    # authorize the shortcut. A model-authored reason such as "search" cannot
    # turn an ordinary form field into a harmless search box.
    target_line = _observed_matching_line(action.target, observation)
    return bool(target_line and _matches(_SEARCH_FIELD_PATTERNS, target_line))


def grounded_critical_press_target(
    action: AgentAction,
    instruction: str,
    observation: str,
    *,
    recent_fill_target: str | None = None,
) -> str | None:
    """Return the current ref that can safely receive a consequential Enter."""
    if not isinstance(action, PressAction):
        return None
    if action.key.strip().casefold() not in _ACTIVATION_KEYS:
        return None
    if not _matches(_CRITICAL_ENTER_INTENT_PATTERNS, instruction):
        return None
    if action.target:
        return action.target if _observed_matching_line(action.target, observation) else None
    if recent_fill_target and _observed_matching_line(recent_fill_target, observation):
        # A successful fill immediately before Enter is focus evidence, but the
        # ref must still exist in the current snapshot so the press can be bound.
        return recent_fill_target
    focused_text_entries = [
        control
        for control in _observation_controls(observation)
        if _TEXT_ENTRY_CONTROL_PATTERN.search(control)
        and _observed_control_is_focused(control)
    ]
    if len(focused_text_entries) != 1:
        return None
    return _observed_control_ref(focused_text_entries[0]) or None


def critical_press_has_grounded_target(
    action: AgentAction,
    instruction: str,
    observation: str,
    *,
    recent_fill_target: str | None = None,
) -> bool:
    """Return whether a consequential Enter has a current bindable target."""
    if not isinstance(action, PressAction):
        return True
    if action.key.strip().casefold() not in _ACTIVATION_KEYS:
        return True
    if not _matches(_CRITICAL_ENTER_INTENT_PATTERNS, instruction):
        return True
    return grounded_critical_press_target(
        action,
        instruction,
        observation,
        recent_fill_target=recent_fill_target,
    ) is not None


def requires_critical_confirmation(
    action: AgentAction,
    instruction: str,
    observation: str,
    *,
    recent_fill_target: str | None = None,
    recent_fill_was_search: bool = False,
) -> bool:
    if not isinstance(action, (ClickAction, PressAction)):
        return False
    target = getattr(action, "target", None)
    target_line = _observed_matching_line(target, observation)
    # Model-authored reasons and unmatched selectors are not authorization
    # evidence. Only the observed target control can make a click critical.
    if isinstance(action, ClickAction):
        search_controls = [
            control
            for control in _observation_controls(observation)
            if _TEXT_ENTRY_CONTROL_PATTERN.search(control)
            and _matches(_SEARCH_FIELD_PATTERNS, control)
        ]
        search_button = _matches(
            (r"搜索|查询|检索|查找", r"\b(?:search|query|find)\b"),
            target_line,
        )
        if (
            target_line
            and search_controls
            and _matches(_SEARCH_FIELD_PATTERNS, instruction)
            and not _matches(_CRITICAL_FINALIZATION_INTENT_PATTERNS, instruction)
            and search_button
        ):
            return False
        return bool(
            target_line
            and (
                _matches(_CRITICAL_PATTERNS, target_line)
                or (
                    _matches(_CRITICAL_FINALIZATION_INTENT_PATTERNS, instruction)
                    and _matches(_FINALIZATION_CONTROL_PATTERNS, target_line)
                )
            )
        )
    if not isinstance(action, PressAction) or action.key.strip().casefold() not in _ACTIVATION_KEYS:
        return False
    if not _matches(_CRITICAL_ENTER_INTENT_PATTERNS, instruction):
        return False

    selected_from_recent_fill = bool(
        recent_fill_target and (not target or target == recent_fill_target)
    )
    if not target_line and selected_from_recent_fill:
        target_line = _observed_matching_line(recent_fill_target, observation)
    if target_line and _matches(_SEARCH_FIELD_PATTERNS, target_line):
        return False
    if selected_from_recent_fill and recent_fill_was_search:
        return False
    # 无目标 Enter 会作用于浏览器当前焦点。没有最近填写记录时，
    # 只有快照明确标出 [focused] 的控件才足以授权；不能仅凭页面上
    # 恰好只有一个文本框，就推断 Enter 一定会提交它。
    action_has_observed_target = bool(target and target_line)
    selected_from_observed_focus = False
    if not target_line:
        focused_text_entry_lines = [
            control
            for control in _observation_controls(observation)
            if _TEXT_ENTRY_CONTROL_PATTERN.search(control)
            and _observed_control_is_focused(control)
        ]
        if len(focused_text_entry_lines) != 1:
            return False
        target_line = focused_text_entry_lines[0]
        selected_from_observed_focus = True
        if _matches(_SEARCH_FIELD_PATTERNS, target_line):
            return False

    if not _TEXT_ENTRY_CONTROL_PATTERN.search(target_line):
        # A targeted Enter can activate buttons and links just like a click.
        # Once the user's instruction is consequential, an observed explicit
        # target must not become a confirmation bypass merely because it is not
        # a text-entry control.
        return action_has_observed_target
    if _matches(_CRITICAL_PATTERNS + _CONSEQUENTIAL_TEXT_ENTRY_PATTERNS, target_line):
        return True
    # A generic field is confirmed only when Enter is tied to an observed
    # target or to the field just filled by the agent. This protects unlabeled
    # editors while leaving ambiguous target-less pages unblocked.
    return action_has_observed_target or selected_from_recent_fill or selected_from_observed_focus


def _http_url_policy(resolved_url: str) -> tuple[bool, bool]:
    """Return ``(side_effect, informational)`` for an HTTP(S) route."""
    try:
        parsed = urlsplit(resolved_url)
    except ValueError:
        return True, False
    if parsed.scheme.casefold() not in {"http", "https"} or not parsed.netloc:
        return True, False
    segments = [
        unquote(part).strip().casefold()
        for part in parsed.path.split("/")
        if part.strip()
    ]
    segment_set = set(segments)

    def segment_tokens(value: str) -> tuple[str, ...]:
        return tuple(
            token
            for token in re.split(r"[-_.~]+", unquote(value).strip().casefold())
            if token
        )

    def tokens_are_informational(tokens: tuple[str, ...]) -> bool:
        return bool(
            len(tokens) >= 3
            and (
                tokens[0] in _INFORMATIONAL_COMPOSITE_MARKERS
                or tokens[-1] in _INFORMATIONAL_COMPOSITE_MARKERS
            )
        )

    segment_tokens_list = [segment_tokens(segment) for segment in segments]
    # 精确动作端点不会仅因父路径叫 docs/help 就被豁免。
    # 例如 /docs/delete-account 中的 delete-account 不是独立端点动词，
    # 因此仍按信息页面处理；但 /docs/delete 仍然需要确认。
    if segment_set & _SIDE_EFFECT_PATH_SEGMENTS:
        return True, False
    # Web 框架常把端点动作写成复合词，而不是独立路径段
    # （例如 ``/account/delete-account``、``/users/remove_member``）。
    # 这里识别这类路径，同时避免把说明性文档 URL 当成副作用：
    # 路径中出现 docs/help/policy 等信息词时，复合词仍按信息页面处理；
    # 上面识别出的精确动作端点不受该豁免影响。
    informational_path = bool(segment_set & _INFORMATIONAL_PATH_SEGMENTS)
    strong_informational_path = bool(segment_set & _INFORMATIONAL_COMPOSITE_MARKERS)
    informational_composite = any(
        tokens_are_informational(tokens) for tokens in segment_tokens_list
    )
    composite_side_effect = any(
        len(tokens) > 1
        and bool(set(tokens) & _SIDE_EFFECT_PATH_SEGMENTS)
        and not tokens_are_informational(tokens)
        for tokens in segment_tokens_list
    )
    if composite_side_effect and not strong_informational_path:
        return True, False
    def query_part_has_side_effect(value: str) -> bool:
        normalized = unquote(value).strip().casefold()
        tokens = segment_tokens(normalized)
        return bool(normalized in _SIDE_EFFECT_PATH_SEGMENTS or set(tokens) & _SIDE_EFFECT_PATH_SEGMENTS)

    query_pairs = parse_qsl(parsed.query, keep_blank_values=True)
    if any(
        query_part_has_side_effect(key)
        or (
            key.casefold() in _ACTION_QUERY_KEYS
            and query_part_has_side_effect(value)
        )
        for key, value in query_pairs
    ):
        return True, False
    informational_query = any(
        key.casefold() in _ACTION_QUERY_KEYS
        and tokens_are_informational(segment_tokens(value))
        and not query_part_has_side_effect(value)
        for key, value in query_pairs
    )
    informational = bool(informational_path or informational_composite or informational_query)
    return False, informational


def http_url_requires_confirmation(resolved_url: str) -> bool:
    """Classify a direct URL without relying on a model-selected action type."""
    side_effect, _ = _http_url_policy(resolved_url)
    return side_effect


def http_url_confirmation_query(resolved_url: str) -> str:
    """Return a safe query hint for a direct side-effect URL confirmation."""
    try:
        parsed = urlsplit(resolved_url)
        query_pairs = parse_qsl(parsed.query, keep_blank_values=True)
    except (TypeError, ValueError, UnicodeError):
        return ""

    hints: list[str] = []
    for key, value in query_pairs:
        normalized_key = unquote(key).strip().casefold()
        if normalized_key not in _SIDE_EFFECT_PATH_SEGMENTS and normalized_key not in _ACTION_QUERY_KEYS:
            continue
        if normalized_key in _ACTION_QUERY_KEYS:
            normalized_value = unquote(value).strip()
            rendered_value = (
                normalized_value
                if _SAFE_QUERY_VALUE.fullmatch(normalized_value)
                else "<redacted>"
            )
        else:
            rendered_value = "<redacted>"
        hints.append(f"{normalized_key}={rendered_value}")
        if len(hints) >= 3:
            break
    return "&".join(hints)


def http_link_requires_confirmation(
    action: ClickAction,
    instruction: str,
    observation: str,
    resolved_url: str,
) -> bool:
    """判断 HTTP(S) 链接是否需要确认，避免把普通动作词都当成副作用。"""
    target_line = observed_target_control(action.target, observation)
    side_effect, informational_url = _http_url_policy(resolved_url)
    if side_effect:
        return True
    informational = bool(
        _matches(_INFORMATIONAL_LINK_PATTERNS, target_line)
        or informational_url
    )
    if informational:
        return False
    return requires_critical_confirmation(action, instruction, observation)


def user_tab_requested(instruction: str) -> bool:
    return _matches(_EXPLICIT_USER_TAB_PATTERNS, instruction)


def additional_agent_tab_requested(instruction: str) -> bool:
    text = str(instruction or "")
    return not _matches(_NEGATED_ADDITIONAL_TAB_PATTERNS, text) and _matches(
        _ADDITIONAL_AGENT_TAB_PATTERNS,
        text,
    )


def requested_agent_tab_count(instruction: str) -> int | None:
    """Return an explicit total Agent-tab count, if the user supplied one."""
    text = str(instruction or "")
    if not additional_agent_tab_requested(text):
        return None
    match = re.search(r"([2-9]|10)\s*个?标签页", text, flags=re.IGNORECASE)
    if match:
        return int(match.group(1))
    match = re.search(r"\b([2-9]|10)\s+tabs?\b", text, flags=re.IGNORECASE)
    if match:
        return int(match.group(1))
    match = re.search(r"([二两三四五六七八九十])\s*个?标签页", text)
    if match:
        return _ZH_TAB_NUMBERS[match.group(1)]
    if _matches(
        (
            r"新标签页|新标签|另开(?:一个)?标签|另(?:一|一个)标签页|第二个标签页",
            r"\b(?:new|another|additional|separate) tab\b|\bopen .* in (?:a )?new tab\b",
        ),
        text,
    ):
        return 2
    return None


def requires_persistent_session(instruction: str) -> bool:
    text = str(instruction or "")
    return not _matches(_STOP_MEDIA_PATTERNS, text) and _matches(
        _PERSISTENT_MEDIA_PATTERNS,
        text,
    )


def validate_action(action: AgentAction) -> None:
    if hasattr(action, "url"):
        url = getattr(action, "url")
        if url:
            validate_http_url(url)
    if isinstance(action, TabCreateAction) and action.url:
        validate_http_url(action.url)
