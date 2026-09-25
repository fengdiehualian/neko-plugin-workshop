"""Typed contracts shared by the BrowserSkill runtime."""

from __future__ import annotations

import json
import re
from functools import lru_cache
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator

SessionDisposition = Literal["close_session", "keep_session"]
FinalSessionAction = Literal["defer", "keep", "close"]
TaskStatus = Literal["completed", "needs_user", "cancelled", "failed"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BrowserSkillErrorInfo(StrictModel):
    code: str
    message: str
    hint: str = ""
    retryable: bool = False


class BrowserTaskResult(StrictModel):
    success: bool
    status: TaskStatus
    summary: str
    details: str = ""
    current_url: str = ""
    steps: int = 0
    session_state: Literal["kept", "closed"] = "closed"
    continuation_available: bool = False
    session_decision_required: bool = False
    completion_source: str = ""
    warnings: list[str] = Field(default_factory=list)
    error: BrowserSkillErrorInfo | None = None

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class Availability(StrictModel):
    enabled: bool = True
    ready: bool
    reasons: list[str] = Field(default_factory=list)
    provider: str = "browser-skill"
    version: str = ""
    browsers: list[dict[str, Any]] = Field(default_factory=list)
    selected_browser: str = ""


class RuntimeSettings(StrictModel):
    bsk_executable: str = Field(default="bundled", max_length=4096)
    browser_label: str = ""
    routing_mode: Literal["auto", "native", "fallback", "hybrid"] = "auto"
    auto_start_daemon: bool = True
    session_scope: Literal["plugin", "conversation", "lanlan"] = "plugin"
    reuse_existing_window: bool = True
    allow_additional_agent_tabs: bool = False
    max_steps: int = Field(default=20, ge=1, le=50)
    active_timeout_seconds: float = Field(default=300.0, ge=10.0, le=1800.0)
    duplicate_suppression_seconds: float = Field(default=20.0, ge=0.0, le=120.0)
    session_keepalive_seconds: float = Field(default=120.0, ge=0.0, le=240.0)
    release_control_when_idle: bool = True
    llm_timeout_seconds: float = Field(default=45.0, ge=5.0, le=180.0)
    planner_max_completion_tokens: int = Field(default=1200, ge=256, le=4096)
    planner_correction_max_completion_tokens: int = Field(default=1600, ge=256, le=8192)
    help_timeout_seconds: int = Field(default=300, ge=30, le=1800)
    snapshot_max_depth: int = Field(default=16, ge=4, le=64)
    snapshot_max_tokens: int = Field(default=8000, ge=500, le=32000)
    scroll_max_pages: int = Field(default=1, ge=1, le=1)
    scroll_snapshot_max_tokens: int = Field(default=2000, ge=500, le=8000)
    scroll_settle_ms: int = Field(default=300, ge=50, le=2000)
    live_page_max_chars: int = Field(default=1200, ge=300, le=5000)
    html_max_bytes: int = Field(default=65536, ge=1024, le=524288)
    allow_tab_borrow: bool = True
    enable_vision_fallback: bool = True
    keep_session_for_media: bool = True
    debug_logging: bool = True

    @classmethod
    def from_mapping(cls, value: Any) -> "RuntimeSettings":
        return cls.model_validate(value if isinstance(value, dict) else {})


class BrowserInfo(StrictModel):
    # BrowserSkill may add informational status fields between compatible
    # releases. Keep action schemas strict, but tolerate such protocol growth.
    model_config = ConfigDict(extra="ignore")

    instance_id: str
    browser_name: str = ""
    browser_version: str = ""
    extension_version: str = ""
    label: str = ""
    session_count: int = 0
    connected_at_ms: int = 0
    version_skew: bool = False
    extension_protocol_version: str = ""


class BaseAction(StrictModel):
    action: str
    reason: str = Field(default="", max_length=800)


class NavigateAction(BaseAction):
    action: Literal["navigate"]
    url: str = Field(min_length=1, max_length=4096)
    wait_until: Literal["load", "domcontentloaded", "networkidle", "commit"] = "domcontentloaded"


class NavigateBackAction(BaseAction):
    action: Literal["navigate_back"]


class NavigateForwardAction(BaseAction):
    action: Literal["navigate_forward"]


class ReloadAction(BaseAction):
    action: Literal["reload"]
    hard: bool = False


class SnapshotAction(BaseAction):
    action: Literal["snapshot"]
    reason: str = Field(
        default="",
        max_length=800,
        description=(
            "Refresh the compact page snapshot. Use observe instead when the compact view "
            "does not contain enough semantic page detail."
        ),
    )


class ObserveAction(BaseAction):
    action: Literal["observe"]
    reason: str = Field(
        default="",
        max_length=800,
        description=(
            "Request a deeper semantic observation after snapshot when more detail is needed."
        ),
    )


class GetHtmlAction(BaseAction):
    action: Literal["get_html"]
    ref: str | None = Field(default=None, max_length=512)


class ScreenshotAction(BaseAction):
    action: Literal["screenshot"]
    ref: str | None = Field(default=None, max_length=512)
    question: str = Field(default="请描述与用户目标有关的可见内容。", max_length=1000)


class TargetAction(BaseAction):
    target: str = Field(
        min_length=1,
        max_length=512,
        description="Exact @eN ref from the latest observation; CSS selectors are not allowed.",
    )

    @field_validator("target")
    @classmethod
    def validate_target(cls, value: str) -> str:
        value = value.strip()
        if not value or "\n" in value or "\r" in value:
            raise ValueError("target must be a single non-empty ref or selector")
        return value


class ClickAction(TargetAction):
    action: Literal["click"]
    button: Literal["left", "middle", "right"] = "left"
    click_count: int = Field(default=1, ge=1, le=3)


class FillAction(TargetAction):
    action: Literal["fill"]
    value: str = Field(max_length=20000)
    submit: bool = Field(
        default=False,
        description=(
            "Also press Enter after filling. Allowed only for an observed harmless search field."
        ),
    )


class SelectAction(TargetAction):
    action: Literal["select"]
    values: list[str] = Field(min_length=1, max_length=20)


class PressAction(BaseAction):
    action: Literal["press"]
    key: str = Field(min_length=1, max_length=80)
    target: str | None = Field(default=None, max_length=512)


class ScrollAction(BaseAction):
    action: Literal["scroll"]
    # Keep every model-controlled movement contiguous and observable. The
    # model chooses total distance by issuing further one-viewport actions.
    direction: Literal["down", "up"] = "down"
    pages: int = Field(default=1, ge=1, le=1)
    target: str | None = Field(
        default=None,
        max_length=512,
        description="Optional ref or selector for a focusable scroll container.",
    )
    until: str = Field(
        default="",
        max_length=200,
        description="Optional visible text marker that stops the internal scroll batch early.",
    )


class WaitForNavigationAction(BaseAction):
    action: Literal["wait_for_navigation"]
    wait_until: Literal["load", "domcontentloaded", "networkidle", "commit"] = "load"
    timeout_seconds: int = Field(default=30, ge=1, le=120)


class TabListAction(BaseAction):
    action: Literal["tab_list"]
    scope: Literal["user", "agent", "all"] = "agent"


class TabCreateAction(BaseAction):
    action: Literal["tab_create"]
    url: str | None = Field(default=None, max_length=4096)


class TabSelectAction(BaseAction):
    action: Literal["tab_select"]
    tab_id: int


class BorrowTabAction(BaseAction):
    action: Literal["borrow_tab"]
    tab_id: int
    purpose: str = Field(min_length=1, max_length=800)


class ReturnTabAction(BaseAction):
    action: Literal["return_tab"]
    tab_id: int


class RequestHelpAction(BaseAction):
    action: Literal["request_help"]
    prompt: str = Field(min_length=1, max_length=2000)
    title: str = Field(default="需要你的操作", max_length=200)
    targets: list[str] = Field(default_factory=list, max_length=10)
    help_kind: Literal["login", "captcha", "otp", "confirmation", "other"] = "other"
    completion_criteria: dict[str, Any] | None = None


class DoneAction(BaseAction):
    action: Literal["done"]
    summary: str = Field(min_length=1, max_length=2000)
    details: str = Field(default="", max_length=6000)
    current_url: str = Field(default="", max_length=4096)
    primary_content_visible: bool = Field(
        default=False,
        description=(
            "True only when task-relevant primary content or the required final control is "
            "visible in the current viewport, not merely present off-screen in the DOM."
        ),
    )
    visible_evidence: str = Field(
        default="",
        max_length=1000,
        description=(
            "A short contiguous exact quote copied character-for-character from the latest "
            "viewport observation. Never paraphrase it."
        ),
    )
    visible_evidence_ref: str = Field(
        default="",
        max_length=32,
        pattern=r"^$|^@e\d+$",
        description=(
            "An optional exact @eN element ref copied from the latest observation that grounds "
            "the completion claim. Use it when a stable final element is better evidence than text."
        ),
    )
    session_disposition: SessionDisposition = Field(
        default="keep_session",
        description="Advisory only; the plugin runtime applies the high-level session decision.",
    )


class FailAction(BaseAction):
    action: Literal["fail"]
    summary: str = Field(min_length=1, max_length=2000)
    details: str = Field(default="", max_length=6000)
    error_code: str = Field(default="COMMAND_FAILED", max_length=100)
    retryable: bool = False
    session_disposition: Literal["close_session"] = "close_session"


AgentAction = Annotated[
    Union[
        NavigateAction,
        NavigateBackAction,
        NavigateForwardAction,
        ReloadAction,
        SnapshotAction,
        ObserveAction,
        GetHtmlAction,
        ScreenshotAction,
        ClickAction,
        FillAction,
        SelectAction,
        PressAction,
        ScrollAction,
        WaitForNavigationAction,
        TabListAction,
        TabCreateAction,
        TabSelectAction,
        BorrowTabAction,
        ReturnTabAction,
        RequestHelpAction,
        DoneAction,
        FailAction,
    ],
    Field(discriminator="action"),
]


class PlannerEnvelope(StrictModel):
    action: AgentAction


_ACTION_ADAPTER = TypeAdapter(AgentAction)


def _extract_json_object(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        value = json.loads(cleaned)
        if isinstance(value, dict):
            return value
    except json.JSONDecodeError:
        pass

    decoder = json.JSONDecoder()
    for index, char in enumerate(cleaned):
        if char != "{":
            continue
        try:
            value, _ = decoder.raw_decode(cleaned[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise ValueError("LLM response does not contain a JSON object")


def parse_agent_action(text: str) -> AgentAction:
    payload = _extract_json_object(text)
    if isinstance(payload.get("action"), dict):
        payload = payload["action"]
    return _ACTION_ADAPTER.validate_python(payload)


def _compact_prompt_schema(value: Any, *, property_map: bool = False) -> Any:
    """Drop model-facing JSON Schema decoration without weakening validation.

    Pydantic titles and serialized defaults are repeated on every Agent turn,
    but the runtime parser already owns those defaults.  Keep constraints,
    descriptions, required fields and every property name (including the
    request-help field literally named ``title``).
    """
    if isinstance(value, dict):
        compacted: dict[str, Any] = {}
        for key, item in value.items():
            if not property_map and key in {"title", "default"}:
                continue
            compacted[key] = _compact_prompt_schema(
                item,
                property_map=key == "properties",
            )
        return compacted
    if isinstance(value, list):
        return [_compact_prompt_schema(item) for item in value]
    return value


@lru_cache(maxsize=1)
def action_schema_json() -> str:
    schema = _compact_prompt_schema(PlannerEnvelope.model_json_schema())
    return json.dumps(schema, ensure_ascii=False, separators=(",", ":"))


def dump_model(value: BaseModel) -> dict[str, Any]:
    return value.model_dump(mode="json", exclude_none=True)
