"""High-level BrowserSkill runtime facade used by the plugin entry."""

from __future__ import annotations

import asyncio
import hashlib
import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from utils.config_manager import get_config_manager

from .agent_loop import AgentLoop, LoopFailure, Planner, failure_result
from .bsk_client import BskClient, BskCommandError
from .control import BrowserTaskControl, BrowserTaskController, SteeringMode, _safe_url
from .models import (
    Availability,
    BrowserSkillErrorInfo,
    BrowserTaskResult,
    FinalSessionAction,
    RuntimeSettings,
)
from .session_manager import ChatBrowserSession, SessionManager

_CLOSE_INTENT = re.compile(
    r"关闭.*(?:浏览器|窗口|会话)|结束.*(?:浏览|会话)|"
    r"\b(?:close|stop|end)\s+(?:the\s+)?(?:browser|window|session)\b",
    flags=re.IGNORECASE,
)
_STATUS_INTENT = re.compile(
    r"(?:浏览器|任务|现在|当前).*(?:进度|状态|做到哪|在做什么)|"
    r"(?:进度|状态).*(?:怎么样|如何|呢)|"
    r"\b(?:browser|task).*(?:progress|status)|\bwhat.*(?:doing|happening)\b",
    flags=re.IGNORECASE,
)
_DEFERRED_CLOSE_INTENT = re.compile(
    r"(?:完成|做完|结束任务|最后|然后|之后|以后|再|并|后)\s*.*(?:关闭|结束)|"
    r"(?:关闭|结束).*?(?:之后|以后|再|然后)|"
    r"\b(?:after|when|once|then|finally)\b.*\b(?:close|stop|end)\b|"
    r"\b(?:close|stop|end)\b.*\b(?:after|then)\b",
    flags=re.IGNORECASE,
)
_SENSITIVE_PAGE_LINE = re.compile(
    r"(?i)(password|passwd|passcode|otp|one[- ]time|verification code|"
    r"验证码|密码|cookie|authorization|api[_ -]?key|secret|token)"
)


def _is_close_only_intent(text: str) -> bool:
    value = str(text or "").strip()
    return bool(
        value
        and _CLOSE_INTENT.search(value)
        and not _DEFERRED_CLOSE_INTENT.search(value)
        and len(value) <= 80
    )


class BrowserSkillRuntime:
    def __init__(
        self,
        *,
        settings: RuntimeSettings | None = None,
        config_manager: Any = None,
        client: BskClient | None = None,
        logger: Any = None,
        prompts_dir: Path | None = None,
    ) -> None:
        self.settings = settings or RuntimeSettings()
        self.config_manager = config_manager or get_config_manager()
        self.logger = logger
        self.client = client or BskClient(
            executable=self.settings.bsk_executable or None,
            logger=logger,
            debug_enabled=self.settings.debug_logging,
        )
        self.sessions = SessionManager(
            self.client,
            logger=logger,
            debug_enabled=self.settings.debug_logging,
        )
        self.prompts_dir = prompts_dir or Path(__file__).resolve().parent.parent / "prompts"
        self.loop = AgentLoop(
            client=self.client,
            sessions=self.sessions,
            config_manager=self.config_manager,
            settings=self.settings,
            prompts_dir=self.prompts_dir,
            logger=logger,
        )
        self._current_session: ChatBrowserSession | None = None
        self._cancel_requested = False
        self._request_lock = asyncio.Lock()
        self._recent_results: dict[str, tuple[float, BrowserTaskResult]] = {}
        self.controls = BrowserTaskController()

    def _debug(self, event: str, **fields: Any) -> None:
        if self.logger is not None and self.settings.debug_logging:
            self.logger.debug("BrowserSkill runtime event={} data={}", event, fields)

    def is_available(self) -> Availability:
        reasons: list[str] = []
        if not self.client.executable:
            reasons.append("BSK_NOT_INSTALLED")
        cfg = self.config_manager.get_model_api_config("agent")
        if not cfg.get("model") or not cfg.get("base_url"):
            reasons.append("AGENT_MODEL_NOT_CONFIGURED")
        return Availability(ready=not reasons, reasons=reasons)

    async def preflight(self) -> Availability:
        availability = await self.client.preflight(
            browser_label=self.settings.browser_label,
            auto_start_daemon=self.settings.auto_start_daemon,
        )
        cfg = self.config_manager.get_model_api_config("agent")
        if not cfg.get("model") or not cfg.get("base_url"):
            availability.ready = False
            if "AGENT_MODEL_NOT_CONFIGURED" not in availability.reasons:
                availability.reasons.append("AGENT_MODEL_NOT_CONFIGURED")
        return availability

    def cancel_running(self) -> None:
        self._cancel_requested = True

    async def run_instruction(
        self,
        instruction: str,
        *,
        conversation_id: str | None,
        start_url: str | None = None,
        raw_request: str = "",
        update_mode: str = "auto",
        final_session_action: FinalSessionAction = "defer",
        progress: Any = None,
        planner: Planner | None = None,
    ) -> BrowserTaskResult:
        direction = str(instruction or raw_request or "").strip()
        user_request = str(raw_request or direction).strip()
        intent_text = user_request or direction
        close_intent = _is_close_only_intent(intent_text)
        self._debug(
            "request",
            request=self._request_key(
                conversation_id=conversation_id,
                instruction=intent_text,
                start_url=start_url,
                final_session_action=final_session_action,
            )[:12],
            close_intent=close_intent,
            update_mode=str(update_mode),
        )
        active = self.controls.get_active(conversation_id)
        if active is not None:
            if close_intent:
                status = await self.steer(
                    conversation_id=conversation_id,
                    mode="cancel",
                )
                return self._steering_result(
                    active,
                    str(status.get("message") or "已请求取消浏览器任务"),
                )
            if _STATUS_INTENT.search(intent_text):
                status = active.status()
                return self._steering_result(
                    active,
                    str(status.get("message") or "浏览器任务仍在执行"),
                )
            if active.matches_known_request(direction) or (
                user_request != direction and active.matches_known_request(user_request)
            ):
                return self._steering_result(active, "重复请求已合并到正在执行的浏览器任务")
            mode = self._resolve_update_mode(update_mode, intent_text)
            status = await self.steer(
                conversation_id=conversation_id,
                mode=mode,
                requirement=direction,
                user_request=user_request,
            )
            return self._steering_result(
                active,
                str(status.get("message") or "已更新正在执行的浏览器任务"),
            )

        request_key = self._request_key(
            conversation_id=conversation_id,
            instruction=raw_request or instruction,
            start_url=start_url,
            final_session_action=final_session_action,
        )
        async with self._request_lock:
            cached = None if close_intent else self._get_recent_result(request_key)
            if cached is not None:
                if self.logger is not None:
                    self.logger.info(
                        "Suppressed duplicate BrowserSkill request key={}",
                        request_key[:12],
                    )
                self._record_duplicate_suppressed()
                return cached.model_copy(deep=True)

            control = self.controls.start(
                conversation_id=conversation_id,
                goal=str(instruction or raw_request or "").strip(),
                original_request=str(raw_request or instruction or "").strip(),
                action_limit=self.settings.max_steps,
            )
            try:
                result = await self._run_instruction_once(
                    instruction,
                    conversation_id=conversation_id,
                    start_url=start_url,
                    raw_request=raw_request,
                    final_session_action=final_session_action,
                    progress=progress,
                    planner=planner,
                    control=control,
                )
            except asyncio.CancelledError:
                self.controls.finish_cancelled(control)
                raise
            except BskCommandError:
                if control.cancel_requested:
                    result = failure_result(
                        LoopFailure(
                            "CANCELLED",
                            "浏览器任务已按新指令取消",
                            status="cancelled",
                        )
                    )
                    self.controls.finish(control, result)
                    return result
                self.controls.finish_failed(control, "BrowserSkill command failed")
                raise
            except Exception as exc:
                self.controls.finish_failed(control, type(exc).__name__)
                raise
            self.controls.finish(control, result)
            if (
                not close_intent
                and control.revision == 0
                and result.success
                and result.status == "completed"
            ):
                self._recent_results[request_key] = (time.monotonic(), result.model_copy(deep=True))
            return result

    async def _run_instruction_once(
        self,
        instruction: str,
        *,
        conversation_id: str | None,
        start_url: str | None = None,
        raw_request: str = "",
        final_session_action: FinalSessionAction = "defer",
        progress: Any = None,
        planner: Planner | None = None,
        control: BrowserTaskControl | None = None,
    ) -> BrowserTaskResult:
        instruction = str(instruction or raw_request or "").strip()
        if not instruction:
            return failure_result(LoopFailure("ACTION_REJECTED", "浏览器任务指令不能为空"))

        if _is_close_only_intent(raw_request or instruction):
            self._recent_results.clear()
            closed = await self.sessions.close_conversation(conversation_id)
            return BrowserTaskResult(
                success=True,
                status="completed",
                summary="已关闭当前聊天的浏览器会话" if closed else "当前聊天没有保留的浏览器会话",
                details="",
                steps=0,
                session_state="closed",
            )

        async def tracked_progress(**payload: Any) -> None:
            if control is not None:
                control.update_progress(
                    stage=payload.get("stage"),
                    message=payload.get("message"),
                    step=payload.get("step"),
                    action_limit=self.settings.max_steps,
                    progress=payload.get("progress"),
                )
                payload["metrics"] = {
                    "actions_used": control.step,
                    "action_limit": self.settings.max_steps,
                    "goal_revision": control.revision,
                    "applied_revision": control.applied_revision,
                    "pending_updates": control.pending_count,
                }
            if progress is not None:
                await progress(**payload)

        if progress is not None or control is not None:
            await tracked_progress(
                stage="preflight",
                message="正在检查 BrowserSkill 和浏览器连接",
                step=0,
            )

        availability = await self.preflight()
        self._debug(
            "preflight",
            ready=availability.ready,
            reasons=list(availability.reasons),
            browser=(availability.selected_browser or "")[:32],
        )
        if not availability.ready:
            return self._availability_failure(availability)

        ok, quota = await self.config_manager.aconsume_agent_daily_quota(
            source="browser_skill.run_browser_task",
            units=1,
        )
        if not ok:
            return BrowserTaskResult(
                success=False,
                status="failed",
                summary="Agent 每日免费额度已用完",
                details=f"used={quota.get('used', 0)}, limit={quota.get('limit', 0)}",
                session_state="closed",
                error=BrowserSkillErrorInfo(
                    code="AGENT_QUOTA_EXCEEDED",
                    message="Agent 每日免费额度已用完",
                    hint="更换 Agent 模型或等待额度重置。",
                    retryable=False,
                ),
            )

        self._cancel_requested = False
        session: ChatBrowserSession | None = None
        async with self.sessions.execution():
            try:
                if self._cancel_requested:
                    raise asyncio.CancelledError
                if progress is not None or control is not None:
                    await tracked_progress(
                        stage="starting_session",
                        message="正在连接 BrowserSkill Agent Window",
                        step=0,
                    )
                try:
                    session = await self.sessions.get_or_create(
                        conversation_id=conversation_id,
                        browser_id=availability.selected_browser,
                        reuse_existing=self.settings.reuse_existing_window,
                        adoption_key=self._adoption_key(raw_request or instruction),
                    )
                except BskCommandError as exc:
                    raise LoopFailure(
                        "SESSION_CONTROL_BLOCKED" if exc.is_session_busy else "SESSION_START_FAILED",
                        (
                            "浏览器仍在等待用户归还操作权"
                            if exc.is_session_busy
                            else "无法创建 BrowserSkill 会话"
                        ),
                        hint=(
                            "请在 BrowserSkill 人工接管浮层点击“继续/完成”，然后重试。"
                            if exc.is_session_busy
                            else exc.hint or str(exc)
                        ),
                        retryable=exc.retryable,
                        status="needs_user" if exc.is_session_busy else "failed",
                    ) from exc
                self._current_session = session
                self._debug(
                    "session_ready",
                    session=hashlib.sha256(session.bsk_session_id.encode("utf-8")).hexdigest()[:10],
                    reused=bool(session.last_observation or session.current_url),
                )
                result = await self.loop.run(
                    instruction=instruction,
                    raw_request=raw_request or instruction,
                    session=session,
                    start_url=start_url,
                    progress=tracked_progress,
                    planner=planner,
                    control=control,
                    final_session_action=final_session_action,
                )
                self._record_result(result)
                return result
            except asyncio.CancelledError:
                await self.client.cancel_active()
                if session is not None:
                    await asyncio.shield(self._preserve_after_task(session))
                self._record_failure("CANCELLED")
                raise
            except LoopFailure as exc:
                if session is not None:
                    if self._can_preserve_failure(exc.code):
                        kept = await self._preserve_after_task(session)
                    else:
                        await self.sessions.close_session(session)
                        kept = False
                result = failure_result(exc)
                if session is not None and kept:
                    self._mark_preserved(result)
                self._record_result(result)
                return result
            except FileNotFoundError:
                if session is not None:
                    await self.sessions.close_session(session)
                result = self._availability_failure(
                    Availability(ready=False, reasons=["BSK_NOT_INSTALLED"])
                )
                self._record_result(result)
                return result
            except BskCommandError as exc:
                cancelled = control is not None and control.cancel_requested
                if session is not None:
                    if cancelled:
                        kept = await self._preserve_after_task(session)
                    else:
                        await self.sessions.close_session(session)
                        kept = False
                error = (
                    LoopFailure(
                        "CANCELLED",
                        "浏览器任务已按新指令取消",
                        status="cancelled",
                    )
                    if control is not None and control.cancel_requested
                    else self.loop._map_bsk_error(exc)
                )
                result = failure_result(error)
                if session is not None and kept:
                    self._mark_preserved(result)
                self._record_result(result)
                return result
            except Exception as exc:
                if session is not None:
                    kept = await self._preserve_after_task(session)
                else:
                    kept = False
                if self.logger is not None:
                    self.logger.exception(
                        "BrowserSkill task failed internally: {}",
                        type(exc).__name__,
                    )
                result = failure_result(
                    LoopFailure(
                        "COMMAND_FAILED",
                        "BrowserSkill 插件内部执行失败",
                        hint=type(exc).__name__,
                        retryable=True,
                    )
                )
                if kept:
                    self._mark_preserved(result)
                self._record_result(result)
                return result
            finally:
                self._current_session = None

    def _request_key(
        self,
        *,
        conversation_id: str | None,
        instruction: str,
        start_url: str | None,
        final_session_action: FinalSessionAction = "defer",
    ) -> str:
        normalized = re.sub(r"\s+", " ", str(instruction or "")).strip().casefold()
        material = "\0".join(
            (
                conversation_id or "<one-shot>",
                normalized,
                start_url or "",
                final_session_action,
            )
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    @staticmethod
    def _adoption_key(instruction: str) -> str:
        normalized = re.sub(r"\s+", " ", str(instruction or "")).strip().casefold()
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest() if normalized else ""

    def _get_recent_result(self, request_key: str) -> BrowserTaskResult | None:
        ttl = self.settings.duplicate_suppression_seconds
        if ttl <= 0:
            self._recent_results.clear()
            return None
        now = time.monotonic()
        expired = [
            key
            for key, (created_at, _) in self._recent_results.items()
            if now - created_at > ttl
        ]
        for key in expired:
            self._recent_results.pop(key, None)
        recent = self._recent_results.get(request_key)
        return recent[1] if recent is not None else None

    @staticmethod
    def _record_duplicate_suppressed() -> None:
        try:
            from utils.instrument import counter

            counter("browser_skill_duplicate_suppressed", value=1)
        except Exception:
            pass

    async def cancel(self, conversation_id: str | None = None) -> None:
        """Stop the current task but keep its reusable Agent Window."""
        self._cancel_requested = True
        self._recent_results.clear()
        await self.client.cancel_active()
        session = self.sessions.find(conversation_id) or self._current_session
        if session is not None:
            await self._preserve_after_task(session)

    async def close(self, conversation_id: str | None = None) -> None:
        self._recent_results.clear()
        await self.client.cancel_active()
        if conversation_id:
            await self.sessions.close_conversation(conversation_id)
        else:
            await self.sessions.close_all()

    def get_status(self, conversation_id: str | None = None) -> dict[str, object]:
        return self.controls.status(conversation_id)

    async def inspect_page(
        self,
        conversation_id: str | None = None,
        *,
        refresh: bool = True,
    ) -> dict[str, object]:
        """Return bounded current-page state for the main LLM, never for logs."""
        session = self.sessions.find(conversation_id)
        if session is None:
            return {
                "available": False,
                "reason": "NO_RETAINED_SESSION",
                "content_trust": "untrusted_page_data",
            }

        active = self.controls.get_active(conversation_id) is not None
        reacquired = False
        refresh_error = ""
        if refresh and not active:
            try:
                await self.sessions.acquire_for_agent(session)
                reacquired = True
                tabs_payload = await asyncio.wait_for(
                    self.client.tab_list(session.bsk_session_id, scope="agent"),
                    timeout=6.0,
                )
                tabs = (
                    tabs_payload.get("tabs")
                    if isinstance(tabs_payload.get("tabs"), list)
                    else []
                )
                selected = next(
                    (
                        item
                        for item in tabs
                        if isinstance(item, dict)
                        and (
                            item.get("active") is True
                            or self._int_or_none(item.get("tab_id")) == session.current_tab_id
                        )
                    ),
                    next((item for item in tabs if isinstance(item, dict)), None),
                )
                if isinstance(selected, dict):
                    tab_id = self._int_or_none(selected.get("tab_id"))
                    if tab_id is not None:
                        session.current_tab_id = tab_id
                    session.current_url = str(selected.get("url") or session.current_url)
                    session.current_title = " ".join(
                        str(selected.get("title") or session.current_title).split()
                    )[:300]
                snapshot = await asyncio.wait_for(
                    self.client.snapshot(
                        session.bsk_session_id,
                        max_depth=min(self.settings.snapshot_max_depth, 16),
                        max_tokens=min(self.settings.snapshot_max_tokens, 4000),
                    ),
                    timeout=8.0,
                )
                session.last_observation = str(snapshot.get("text") or "")
                session.last_observation_at = time.time()
                snapshot_tab_id = self._int_or_none(snapshot.get("tab_id"))
                if snapshot_tab_id is not None:
                    session.current_tab_id = snapshot_tab_id
            except Exception as exc:
                refresh_error = type(exc).__name__
            finally:
                if reacquired and self.settings.release_control_when_idle:
                    await self.sessions.release_to_user(session)

        observation, truncated = self._page_preview(session.last_observation)
        age = (
            max(0, int(time.time() - session.last_observation_at))
            if session.last_observation_at
            else None
        )
        safe_url = _safe_url(session.current_url)
        try:
            domain = urlsplit(safe_url).hostname or ""
        except ValueError:
            domain = ""
        self._debug(
            "page_inspected",
            active=active,
            domain=domain,
            observation_chars=len(session.last_observation),
            refresh_error=refresh_error,
        )
        return {
            "available": bool(safe_url or observation),
            "url": safe_url,
            "title": session.current_title,
            "tab_id": session.current_tab_id,
            "observation": observation,
            "observation_truncated": truncated,
            "observation_age_seconds": age,
            "live_refresh": bool(refresh and not refresh_error and not active),
            "refresh_error": refresh_error,
            "control_owner": session.control_owner,
            "content_trust": "untrusted_page_data",
        }

    async def _preserve_after_task(self, session: ChatBrowserSession) -> bool:
        if not self.settings.reuse_existing_window:
            await self.sessions.close_session(session)
            return False
        return await self.sessions.preserve_session(
            session,
            interval_seconds=self.settings.session_keepalive_seconds,
            release_control=self.settings.release_control_when_idle,
        )

    @staticmethod
    def _can_preserve_failure(code: str) -> bool:
        return str(code or "").upper() not in {
            "BSK_EXTENSION_OFFLINE",
            "BROWSER_NOT_CONNECTED",
            "BSK_VERSION_SKEW",
            "SESSION_START_FAILED",
        }

    @staticmethod
    def _mark_preserved(result: BrowserTaskResult) -> None:
        result.session_state = "kept"
        result.continuation_available = True
        result.session_decision_required = True
        if result.details:
            result.details += "\n"
        result.details += (
            "当前 Agent Window 和页面已保留；空闲期间操作权已交还用户，"
            "后续仍可调用 BrowserSkill 自动复用。"
        )

    @staticmethod
    def _int_or_none(value: Any) -> int | None:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _page_preview(value: str, *, limit: int = 5000) -> tuple[str, bool]:
        lines: list[str] = []
        for raw_line in str(value or "").splitlines():
            line = " ".join(raw_line.split())
            if not line:
                continue
            sensitive = _SENSITIVE_PAGE_LINE.search(line)
            if sensitive:
                safe_prefix = line[: sensitive.start()].rstrip(" ;,:=-")
                lines.append(
                    f"{safe_prefix}; [敏感字段已省略]"
                    if safe_prefix
                    else "[敏感字段已省略]"
                )
            else:
                lines.append(line)
        text = "\n".join(lines)
        return text[:limit], len(text) > limit

    async def steer(
        self,
        *,
        conversation_id: str | None,
        mode: SteeringMode,
        requirement: str = "",
        user_request: str = "",
    ) -> dict[str, object]:
        control = self.controls.get_active(conversation_id)
        if control is None:
            return {
                "accepted": False,
                "active": False,
                "message": "当前聊天没有正在执行的 BrowserSkill 任务",
                "can_steer": False,
            }
        try:
            update, duplicate = control.submit(
                mode,
                requirement,
                user_request=user_request,
            )
        except ValueError as exc:
            return {
                **control.status(),
                "accepted": False,
                "message": str(exc),
            }
        if mode == "cancel" or control.stage == "waiting_for_user":
            await self.client.cancel_active()
        action = {"append": "补充要求", "replace": "切换执行方向", "cancel": "取消任务"}[mode]
        return {
            **control.status(),
            "accepted": True,
            "duplicate": duplicate,
            "update_revision": update.revision,
            "message": f"{action}已接收，将在当前动作边界生效",
        }

    @staticmethod
    def _resolve_update_mode(mode: str, instruction: str) -> SteeringMode:
        normalized = str(mode or "auto").strip().lower()
        if normalized in {"append", "replace", "cancel"}:
            return normalized  # type: ignore[return-value]
        if re.search(
            r"^(?:另外|同时|还要|并且|补充)(?:一下|是|：|:|，|,|\s|$)|"
            r"^(?:also|additionally|and also)(?:\s|:|,|$)",
            instruction,
            re.I,
        ):
            return "append"
        return "replace"

    @staticmethod
    def _steering_result(control: BrowserTaskControl, summary: str) -> BrowserTaskResult:
        status = control.status()
        return BrowserTaskResult(
            success=True,
            status="completed",
            summary=summary,
            details=(
                f"stage={status.get('stage', '')}, revision={status.get('goal_revision', 0)}, "
                f"pending={status.get('pending_updates', 0)}"
            ),
            current_url=str(status.get("current_url") or ""),
            steps=int(status.get("step") or 0),
            session_state="kept",
        )

    @staticmethod
    def _availability_failure(availability: Availability) -> BrowserTaskResult:
        reason = availability.reasons[0] if availability.reasons else "COMMAND_FAILED"
        messages = {
            "BSK_NOT_INSTALLED": (
                "未安装 BrowserSkill CLI",
                "请按 BrowserSkill 官方文档安装 bsk，并在 Chrome/Edge 安装扩展。",
            ),
            "BSK_BUNDLE_ERROR": (
                "内置 BrowserSkill CLI 不可用",
                "当前系统不受内置版本支持，或内置文件校验失败；可安装官方 bsk 并在插件中填写绝对路径。",
            ),
            "BSK_EXTENSION_OFFLINE": (
                "BrowserSkill 扩展未连接",
                "打开浏览器扩展并确认状态为 connected。",
            ),
            "BROWSER_NOT_CONNECTED": (
                "没有浏览器连接到 BrowserSkill",
                "打开已安装扩展的 Chrome/Edge，并确认扩展状态为 connected。",
            ),
            "MULTIPLE_BROWSERS": (
                "检测到多个 BrowserSkill 浏览器实例",
                "在插件配置 browser_label 中指定唯一的实例 ID 或标签。",
            ),
            "BSK_VERSION_SKEW": (
                "BrowserSkill CLI 与扩展版本不匹配",
                "将 bsk CLI 和浏览器扩展升级到相互兼容的版本。",
            ),
            "AGENT_MODEL_NOT_CONFIGURED": (
                "Agent 模型尚未配置",
                "请先在 N.E.K.O 模型设置中配置 Agent API。",
            ),
        }
        message, hint = messages.get(reason, ("BrowserSkill 当前不可用", "请运行 bsk status 检查状态。"))
        details = ""
        if availability.browsers:
            labels = [
                f"{item.get('instance_id', '')} ({item.get('label') or item.get('browser_name') or '-'})"
                for item in availability.browsers
            ]
            details = "可用浏览器：" + ", ".join(labels)
        return BrowserTaskResult(
            success=False,
            status="failed",
            summary=message,
            details=details or hint,
            session_state="closed",
            error=BrowserSkillErrorInfo(
                code=reason,
                message=message,
                hint=hint,
                retryable=reason
                not in {"BSK_NOT_INSTALLED", "BSK_BUNDLE_ERROR", "AGENT_MODEL_NOT_CONFIGURED"},
            ),
        )

    def _record_failure(self, code: str) -> None:
        try:
            from utils.instrument import counter

            counter("browser_skill_task", outcome="failed", error_code=code)
            counter(
                "browser_skill_task_duration_ms",
                value=int(self.loop.metrics.get("duration_ms", 0)),
            )
        except Exception:
            pass

    def _record_result(self, result: BrowserTaskResult) -> None:
        try:
            from utils.instrument import counter

            counter(
                "browser_skill_task",
                outcome="success" if result.success else "failed",
                error_code=result.error.code if result.error else "",
            )
            counter("browser_skill_steps", value=max(0, result.steps))
            counter("browser_skill_session", state=result.session_state)
            metrics = self.loop.metrics
            counter(
                "browser_skill_task_duration_ms",
                value=int(metrics.get("duration_ms", 0)),
            )
            counter(
                "browser_skill_human_help",
                value=int(metrics.get("human_help_count", 0)),
            )
            counter(
                "browser_skill_tab_borrow",
                value=int(metrics.get("borrow_count", 0)),
            )
            action_counts = metrics.get("action_counts", {})
            if isinstance(action_counts, dict):
                for action_type, count in action_counts.items():
                    counter(
                        "browser_skill_action",
                        value=max(0, int(count)),
                        action_type=str(action_type),
                    )
            token_usage = metrics.get("token_usage", {})
            if isinstance(token_usage, dict):
                for token_type in ("input_tokens", "output_tokens", "total_tokens"):
                    counter(
                        "browser_skill_agent_tokens",
                        value=max(0, int(token_usage.get(token_type) or 0)),
                        token_type=token_type,
                    )
        except Exception:
            pass
