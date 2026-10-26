"""N.E.K.O 《崩坏：星穹铁道》游戏搭子插件。"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from plugin.sdk.plugin import (
    Err,
    NekoPluginBase,
    Ok,
    PluginStore,
    SdkError,
    lifecycle,
    llm_tool,
    neko_plugin,
    plugin_entry,
    tr,
    ui,
    unwrap_or,
)

from .adapters import LocalVisionAdapter
from .assistant import VisionAssistantClient, VisionAssistantProcess
from .core import (
    CharacterRegistry,
    KnowledgeBase,
    apply_profile_proposal,
    build_profile_proposal,
    evaluate_event,
    merge_player_context,
)
from .data import ExternalKnowledgeCatalog
from .data.avatar_pack import AvatarTemplatePack
from .data.vision_runtime import VisionMatchRuntime
from .game_state import (
    GAME_STATE_LABELS,
    build_game_state_observation,
    public_game_state,
)
from .observer import capture_game_window, find_hsr_window, frame_difference
from .runtime import CompanionRuntime, build_event_delivery
from .runtime.chat_activity import latest_user_activity
from .runtime.consultation_contract import CONSULTATION_SCHEMA, bound_followup_plan
from .runtime.diagnostic_export import export_diagnostic
from .runtime.diagnostics import build_diagnostic_report
from .runtime.equipment import equipment_card, record_handoff
from .runtime.followup import DIRECT_WAIT_SECONDS, arm_followup, cancel_followup
from .runtime.grounding import build_grounding
from .runtime.manual_refresh import cancel_manual_refresh, public_manual_refresh, request_manual_refresh
from .runtime.live_refresh import (
    drain_analysis_worker,
    fact_regions,
    guarded_analysis,
    refresh_for_query,
    trace_refresh,
    validate_before_delivery,
    watch_latest_frame,
)
from .runtime.observation import (
    public_visual_evidence,
    visual_context_text,
)
from .runtime.observation_store import ObservationStore
from .runtime.progression import plan_progression
from .runtime.guidance import build_guidance, load_guidance_catalog, plan_guidance
from .runtime.service_contract import PREPARE_TOOL_DESCRIPTION, clear_discussion, mark_service_request, public_service_request, record_answer_delivery, response_contract
from .runtime.query_coordinator import answer_status, needs_followup, resolve_query
from .runtime.query_plan import SCREEN_FIELDS, SEMANTIC_INTENTS, build_query_plan
from .runtime.prepare_trace import prepare_failure, prepare_stage, read_records, trace_prepare
from .runtime.speech import PendingSpeech, SpeechArbiter
from .runtime.storage import migrate_legacy_store
from .runtime.team_overview import team_overview
from .runtime.uid_import import STORE_KEY as UID_STORE_KEY, profile_with_roster
from .runtime.uid_service import uid_service
from .runtime.player_builds import STORE_KEY as MANUAL_BUILDS_KEY, BuildError, player_build_service
from .runtime.data_freshness import data_freshness
from .session import (
    DEFAULT_SESSION_TTL_SECONDS,
    build_companion_context,
    normalize_observed_name,
    public_correction_summary,
    remember_character_correction,
    resolve_character_candidates,
    sanitize_character_corrections,
    session_is_active,
    start_session,
    stop_session,
    utc_now_iso,
    visual_fingerprint,
    visual_fingerprints_match,
)
from .vision import prepare_image_for_vision

PROFILE_PROPERTIES = {
    "owned_characters": {"type": "array", "items": {"type": "string"}},
    "favorite_characters": {"type": "array", "items": {"type": "string"}},
    "training_targets": {"type": "array", "items": {"type": "string"}},
    "common_teams": {"type": "array", "items": {"type": "string"}},
    "current_goals": {"type": "array", "items": {"type": "string"}},
    "preferences": {"type": "object", "additionalProperties": True},
    "progression_note": {"type": "string"},
}


def _load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        payload = json.load(file)
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def _request_is_recent(request: object, *, max_age_seconds: float = 120.0) -> bool:
    if not isinstance(request, dict) or not request.get("fingerprint"):
        return False
    try:
        created = datetime.fromisoformat(str(request.get("created_at") or "").replace("Z", "+00:00"))
    except ValueError:
        return False
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    age = (datetime.now(timezone.utc) - created.astimezone(timezone.utc)).total_seconds()
    return 0 <= age <= max(1.0, float(max_age_seconds))


@neko_plugin
class HsrCompanionPlugin(NekoPluginBase):
    """Star Rail knowledge and player-context capability for N.E.K.O."""

    def __init__(self, ctx: Any):
        super().__init__(ctx)
        # These packaged resources are deliberately fixed and auditable. Runtime
        # configuration stays available for a later source-adapter phase, where
        # changes can be validated before replacing the audited seed catalog.
        catalog_path = self.config_dir / "resources" / "knowledge.demo.json"
        sources_path = self.config_dir / "resources" / "sources.json"
        registry_path = self.config_dir / "resources" / "character_registry.json"
        self._knowledge = KnowledgeBase(
            _load_json(catalog_path),
            _load_json(sources_path),
        )
        registry_payload = _load_json(registry_path)
        self._characters = CharacterRegistry(registry_payload)
        self._catalog = ExternalKnowledgeCatalog(registry=registry_payload)
        # Some hosts install executable code under the same plugins/<id>
        # directory where the SDK defaults its data. A reinstall can replace
        # it. Keep this plugin's own KV beside its external data components.
        self._legacy_store = self.store
        self.store = PluginStore(plugin_id="hsr_companion", plugin_dir=self._catalog.root / "player-state",
                                 logger=self.logger, enabled=True)
        self._avatars = AvatarTemplatePack(_load_json(self.config_dir / "resources" / "avatar_templates.json"), root=self._catalog.root, runtime=VisionMatchRuntime(root=self._catalog.root))
        self._vision = LocalVisionAdapter(self._catalog, logger=self.logger, avatar_pack=self._avatars)
        self._assistant_client = VisionAssistantClient()
        self._assistant_process = VisionAssistantProcess(Path(__file__).resolve().parent)
        self._assistant_frame_seq = 0
        self._assistant_operation_in_progress = False
        self._proactive_status: dict[str, Any] = {"request_count": 0, "state": "waiting", "message": "尚未请求主动搭话"}
        self._announced_game_pid = 0
        self._game_presence_attempt_at = -1e9
        self._runtime = CompanionRuntime(confirmation_frames=2)
        self._speech_arbiter = SpeechArbiter()
        self._speech_task: asyncio.Task[None] | None = None
        self._max_experiences = 100
        self._event_dedup_limit = 200
        self._session_ttl_seconds = DEFAULT_SESSION_TTL_SECONDS
        self._context_audit_limit = 50
        self._tool_audit_limit = 100
        self._observer_task: asyncio.Task[None] | None = None
        self._data_pack_task: asyncio.Task[None] | None = None
        self._data_pack_runtime: dict[str, Any] = self._catalog.status()
        self._observer_last_signature: bytes | None = None
        self._observer_last_push_monotonic = 0.0
        self._last_visual_context_key = ""
        self._last_visual_context_at = 0.0
        self._observation_invalidated = False
        self._observer_status: dict[str, Any] = {
            "enabled": False,
            "proactive_enabled": True,
            "state": "stopped",
            "message": "自动观察尚未开启",
        }

    def _target_character(self) -> str:
        # Same optional target lookup used by neko_warthunder. No host writes,
        # account credentials or chat bodies are read here.
        if not hasattr(self, "ctx"):
            return ""  # No live host target during an isolated development check.
        current = getattr(self.ctx, "_current_lanlan", None)
        if isinstance(current, str) and current.strip():
            return current.strip()[:80]
        try:
            from utils.config_manager import get_config_manager

            data = get_config_manager().get_character_data()
            if isinstance(data, tuple) and len(data) >= 2 and isinstance(data[1], str):
                return data[1].strip()[:80]
        except Exception:
            pass
        return ""

    def push_message(self, **kwargs: Any):
        target = kwargs.get("target_lanlan") or self._target_character()
        if target:
            kwargs["target_lanlan"] = target
        responding = kwargs.get("ai_behavior") == "respond"
        if responding:
            transport = self._message_path_ready()
            if transport.get("available") is False:
                self._proactive_status.update(
                    state="error", submitted=False, message=transport["message"],
                    target_character=target,
                )
                return {"submitted": False, "reason": "message_service_unavailable"}
        if responding:
            self._proactive_status = {
                "state": "submitting", "message": "正在请求猫娘回应",
                "request_count": int(self._proactive_status.get("request_count") or 0) + 1,
                "last_request_at": utc_now_iso(), "target_character": target,
                "event_kind": (kwargs.get("metadata") or {}).get("kind") or "game_event",
            }
        try:
            result = super().push_message(**kwargs)
        except Exception as exc:
            if responding:
                self._proactive_status.update(state="error", message=f"搭话请求发送失败：{exc}")
            raise
        if responding:
            submitted = bool(result.get("submitted"))
            self._proactive_status.update(
                state="submitted" if submitted else "error", submitted=submitted,
                message="已提交搭话请求；尚无猫娘实际回复的回执" if submitted else f"搭话请求未提交：{result.get('reason') or '后台未接受'}",
            )
        return result

    @staticmethod
    def _message_path_ready() -> dict[str, Any]:
        # The SDK's submitted flag acknowledges ingestion, not the PUB bridge
        # that forwards to the character. Guard the explicit no-listener case.
        import socket
        import urllib.parse

        try:
            from plugin.settings import MESSAGE_PLANE_ZMQ_PUB_ENDPOINT

            endpoint = urllib.parse.urlsplit(str(MESSAGE_PLANE_ZMQ_PUB_ENDPOINT))
            if endpoint.scheme != "tcp" or endpoint.hostname not in {"127.0.0.1", "localhost", "::1"} or not endpoint.port:
                return {"available": None}
            with socket.create_connection((endpoint.hostname, endpoint.port), timeout=0.25):
                pass
            return {"available": True}
        except OSError:
            return {
                "available": False,
                "message": "N.E.K.O. 的插件消息服务未运行，猫娘收不到主动消息。请先在 N.E.K.O. 中开启“用户插件”；若已经开启，再重启 N.E.K.O.。识别与查询仍可使用，无需重装视觉助手。",
            }
        except (ImportError, ValueError):
            return {"available": None}

    def _get_speech_arbiter(self) -> SpeechArbiter:
        # Lazy construction also supports older plugin test/embedding fixtures.
        if not hasattr(self, "_speech_arbiter"):
            self._speech_arbiter = SpeechArbiter()
        return self._speech_arbiter

    def _cancel_pending_speech(self, reason: str) -> None:
        self._get_speech_arbiter().cancel(reason)
        task = getattr(self, "_speech_task", None)
        if task and not task.done() and task is not asyncio.current_task():
            task.cancel()

    def _note_speech_query(self) -> None:
        cancel_followup(self, "new_query")
        self._answer_query_generation = getattr(self, "_answer_query_generation", 0) + 1
        self._cancel_pending_speech("query_started")
        self._get_speech_arbiter().query_started(now=time.monotonic())

    async def _refresh_user_activity(self) -> bool:
        try:
            bus, target = self.bus, self._target_character()
        except AttributeError:
            bus, target = None, ""
        status, timestamp = await latest_user_activity(bus, target=target)
        wall_now, mono_now = time.time(), time.monotonic()
        self._chat_activity_status = status
        if timestamp is not None and (target, timestamp) != getattr(self, "_last_user_activity_seen", None):
            self._last_user_activity_seen = (target, timestamp)
            arbiter = self._get_speech_arbiter()
            if wall_now - timestamp < arbiter.quiet_seconds:
                arbiter.user_activity(now=mono_now - (wall_now - timestamp))
        # Older hosts without this capability retain the tool-entry guard.
        # A supported-but-failed read drops this cue; no blind retry/backlog.
        return status != "unavailable"

    def _speech_release_reason(self, cue: PendingSpeech, visual: dict) -> str:
        return self._get_speech_arbiter().check(
            cue, now=time.monotonic(), epoch=getattr(self, "_observation_epoch", 0),
            scene=str((visual.get("scene") or {}).get("primary_state") or "unknown"),
            page_type=str(visual.get("page_type") or "unknown"),
            available=bool(self._observer_status.get("enabled")
                           and self._observer_status.get("proactive_enabled", True)
                           and self._observer_status.get("state") == "observing"
                           and visual.get("fresh") and (visual.get("scene") or {}).get("verified")
                           and not getattr(self, "_observation_invalidated", False)),
            query_busy=getattr(self, "_visual_query_waiters", 0) > 0,
        )

    async def _release_pending_speech(self, cue: PendingSpeech, plan: Any) -> None:
        arbiter = self._get_speech_arbiter()
        failure_reason = "validation_failed"
        try:
            await asyncio.sleep(max(0, cue.ready_at - time.monotonic()))
            visual = await self._current_visual_evidence()
            if not await self._refresh_user_activity():
                arbiter.finish(cue, "activity_unavailable")
                return
            reason = self._speech_release_reason(cue, visual)
            if reason != "ready":
                arbiter.finish(cue, reason)
                return
            # Read-only current-frame validation, no OCR and no query wait.
            # This task is separate from observation commit / direct answers.
            if not await asyncio.wait_for(validate_before_delivery(self), timeout=2.5):
                arbiter.finish(cue, "validation_failed")
                return
            visual = await self._current_visual_evidence()
            if not await self._refresh_user_activity():
                arbiter.finish(cue, "activity_unavailable")
                return
            reason = self._speech_release_reason(cue, visual)
            if reason != "ready":
                arbiter.finish(cue, reason)
                return
            # No await between the final claim and synchronous SDK submission:
            # query/invalidation cannot interleave at this last boundary.
            failure_reason = "submission_failed"
            pushed = self.push_message(
                visibility=[], ai_behavior=plan.ai_behavior, source="hsr_companion",
                parts=[{"type": "text", "text": plan.text}], metadata=plan.metadata,
                priority=plan.priority, coalesce_key=plan.coalesce_key,
            )
            arbiter.finish(cue, "submitted" if isinstance(pushed, dict) and pushed.get("submitted") else "submission_failed")
        except asyncio.CancelledError:
            arbiter.finish(cue, "task_cancelled")
            raise
        except TimeoutError:
            arbiter.finish(cue, "validation_failed")
        except Exception as exc:
            arbiter.finish(cue, failure_reason)
            logger = getattr(self, "logger", None)
            if logger is not None:
                logger.warning("Automatic speech failed: %s", exc)

    def _queue_automatic_speech(self, event: Any, plan: Any, visual: dict) -> dict:
        self._cancel_pending_speech("coalesced_newer")
        arbiter = self._get_speech_arbiter()
        cue = arbiter.offer(
            event, now=time.monotonic(), epoch=getattr(self, "_observation_epoch", 0),
            scene=str((visual.get("scene") or {}).get("primary_state") or "unknown"),
            page_type=str(visual.get("page_type") or "unknown"),
        )
        if cue is not None:
            self._speech_task = asyncio.create_task(
                self._release_pending_speech(cue, plan), name="hsr-companion-speech")
        return {"submitted": False, "queued": cue is not None, "event_kind": event.kind,
                "reason": "pending_arbitration" if cue is not None else "suppressed_by_arbitration"}

    def _announce_game_presence(self, game: dict[str, Any]) -> None:
        pid = int(game.get("pid") or 0)
        now = time.monotonic()
        if not pid or pid == self._announced_game_pid:
            return
        if now - self._game_presence_attempt_at < 30:
            return
        self._game_presence_attempt_at = now
        pushed = self.push_message(
            visibility=[], ai_behavior="read", source="hsr_companion", priority=4,
            coalesce_key="hsr_game_presence",
            parts=[{"type": "text", "text": "[星铁观察连接] 已连接游戏窗口，仅更新静默状态，不要求欢迎或主动回应。具体场景、角色和任务必须等待当前视觉事实；连接恢复不等于游戏事件或任务完成。"}],
            metadata={"kind": "game_presence", "proactive_requested": False},
        )
        if pushed.get("submitted"):
            self._announced_game_pid = pid

    @lifecycle(id="startup")
    async def startup(self, **_):
        legacy = getattr(self, "_legacy_store", None)
        if legacy is not None:
            try:
                migrated = unwrap_or(await self.store.exists("_hsr_external_store_migration_v1"), False)
                if not migrated and (legacy.plugin_dir / legacy.db_name).is_file():
                    copied = await migrate_legacy_store(legacy, self.store)
                    self.logger.info("Migrated %s plugin KV keys to external player state", copied)
                await self._store_value("_hsr_external_store_migration_v1", True)
            finally:
                await legacy.close()
                self._legacy_store = None
        # Some released N.E.K.O hosts construct the plugin before the effective
        # config (including ``plugin.store.enabled``) has reached the SDK.  The
        # built-in memo/lifekit plugins keep the same compatibility guard.  This
        # only enables this plugin's own sandboxed KV store; it does not touch
        # host or game files.
        if not self.store.enabled:
            self.store.enabled = True
            self.logger.info("Store force-enabled for hsr_companion")
        corrections, disabled_corrections = sanitize_character_corrections(
            await self._read_corrections(), registry=self._characters
        )
        if disabled_corrections:
            await self._store_value("character_corrections", corrections)
            self.logger.info(
                "Disabled %s unsafe legacy character correction(s)",
                disabled_corrections,
            )
        legacy_state = await self._read_game_state()
        if legacy_state and str(legacy_state.get("verification") or "unverified") not in {
            "verified",
            "player_confirmed",
        }:
            # v0.7 allowed a model-authored state to occupy the authoritative
            # slot. Preserve it only as an audit hint and clear the slot so an
            # upgrade cannot display old model guesses as current truth.
            await self._store_value("last_unverified_game_state_hint", legacy_state)
            await self._store_value("current_game_state", {})
        await self._invalidate_current_observation("plugin_restarted")
        session = await self._read_session()
        if session and not session_is_active(session, ttl_seconds=self._session_ttl_seconds):
            session = stop_session(session, reason="expired_before_startup")
            await self._store_value("companion_session", session)
        elif session_is_active(session, ttl_seconds=self._session_ttl_seconds):
            await self._push_companion_context("plugin_startup_restore")
        observer_settings = unwrap_or(await self._get_store("observer_settings"), {})
        if not isinstance(observer_settings, dict):
            observer_settings = {}
        self._observer_status["proactive_enabled"] = bool(observer_settings.get("proactive_enabled", True))
        if observer_settings.get("enabled") is True:
            # startup is invoked through a transient asyncio.run() loop.  A
            # task created here would be cancelled as soon as startup returns;
            # restore it on the first panel/tool call instead.
            self._observer_status = {
                "enabled": True,
                "proactive_enabled": bool(observer_settings.get("proactive_enabled", True)),
                "state": "waiting",
                "message": "打开插件面板或开始聊天后恢复自动观察",
                "updated_at": utc_now_iso(),
                "restored": True,
            }
        if not self._catalog.status().get("installed") or self._catalog.status().get("update_available"):
            # Data-pack preparation is finite work, so await it here rather
            # than spawning it on the transient lifecycle loop.
            self._data_pack_runtime = {
                **self._catalog.status(),
                "state": "installing",
                "message": "正在准备星铁资料组件",
            }
            await self._install_data_pack()
        return Ok(
            {
                "status": "ready",
                "catalog": self._catalog.meta,
                "record_count": self._catalog.record_count,
                "coverage": self._catalog.coverage,
                "session_active": session_is_active(session, ttl_seconds=self._session_ttl_seconds),
                "observer_enabled": bool(self._observer_status.get("enabled")),
            }
        )

    @lifecycle(id="shutdown")
    async def shutdown(self, **_):
        await self._cancel_observer_task(update_status=False)
        task = self._data_pack_task
        self._data_pack_task = None
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        await asyncio.to_thread(self._vision.close)
        session = await self._read_session()
        if session_is_active(session, ttl_seconds=self._session_ttl_seconds):
            self.push_message(
                visibility=[],
                ai_behavior="read",
                parts=[
                    {
                        "type": "text",
                        "text": (
                            "[星铁陪玩插件暂时停止]\n"
                            "停止使用本插件此前注入的星铁会话规则；"
                            "插件重新启动并恢复会话后再继续使用。"
                        ),
                    }
                ],
                source="hsr_companion",
                priority=1,
                coalesce_key="hsr_session_context",
            )
        return Ok({"status": "stopped"})

    def _set_observer_status(self, state: str, message: str, **details: Any) -> None:
        self._observer_status = {
            "enabled": bool(self._observer_status.get("enabled")),
            "proactive_enabled": bool(self._observer_status.get("proactive_enabled", True)),
            "state": str(state or "unknown"),
            "message": str(message or ""),
            "updated_at": utc_now_iso(),
            **details,
        }

    async def _current_visual_evidence(self) -> dict[str, Any]:
        return await ObservationStore(self).current()

    def _push_current_visual_context(self, evidence: dict[str, Any]) -> None:
        key_value = {k: evidence.get(k) for k in ("status", "scene", "characters", "team", "controlled_character")}
        key_value["screen_facts"] = [
            {key: fact.get(key) for key in ("kind", "scope", "label", "value", "entity_id")}
            for fact in (evidence.get("screen_facts") or {}).get("facts") or []
        ]
        key = json.dumps(key_value, ensure_ascii=False, sort_keys=True)
        now = time.monotonic()
        if key == getattr(self, "_last_visual_context_key", "") and now - getattr(self, "_last_visual_context_at", 0.0) < 40:
            return
        pushed = self.push_message(
            visibility=[], ai_behavior="read", source="hsr_companion", priority=4,
            coalesce_key="hsr_current_visual",
            parts=[{"type": "text", "text": visual_context_text(evidence)}],
            metadata={"kind": "current_visual_evidence", "status": evidence["status"], "image_stored": False},
        )
        if isinstance(pushed, dict) and pushed.get("submitted"):
            self._last_visual_context_key, self._last_visual_context_at = key, now

    def _revoke_current_observation(self, reason="explicit_revocation") -> None:
        """Synchronous revocation: no cleanup/storage await may precede it."""
        ObservationStore(self).revoke(reason)

    def _stop_observation_now(self) -> None:
        clear_discussion(self)
        cancel_manual_refresh(self)
        cancel_followup(self, "observation_stopped")
        self._observer_stop_requested = True
        self._observer_status.update(enabled=False, state="stopped", message="自动观察已关闭", updated_at=utc_now_iso())
        self._revoke_current_observation()
        task = getattr(self, "_observer_task", None)
        if task and not task.done():
            task.cancel()
        self._push_current_visual_context(public_visual_evidence(
            {"invalidated": True, "source": "assistant_local_ocr"}, self._observer_status))

    async def _invalidate_current_observation(self, reason: str) -> None:
        if reason.startswith(("capture_unavailable", "capture_error")):
            # Transient capture failure is NOT evidence of a new page. Keep
            # successful data/guards, but immediately mask current answers.
            ObservationStore(self).suspend(reason)
            self._push_current_visual_context(await self._current_visual_evidence())
            return
        if reason.startswith(("capture_paused", "game_closed", "plugin_restarted", "observation_stopped")):
            cancel_followup(self, reason)
        self._revoke_current_observation(reason)
        # Revoke the passive context before potentially slow persistence.
        self._push_current_visual_context(public_visual_evidence(
            {"invalidated": True, "source": "assistant_local_ocr"}, self._observer_status))
        latest = unwrap_or(await self._get_store("last_local_vision"), {})
        latest = latest if isinstance(latest, dict) else {}
        previous_state = await self._read_game_state()
        had_facts = bool(previous_state or latest.get("characters") or (latest.get("screen_facts") or {}).get("facts")
                         or self._runtime.stable_scene != "unknown")
        self._runtime.invalidate_observation()
        self._observer_last_signature = None
        self._observer_last_push_monotonic = 0.0
        self._observer_status.pop("recognized_characters", None)
        self._observer_status.pop("scene", None)
        await self._store_value("current_game_state", {})
        await self._store_value("pending_game_state_candidate", {})
        await self._store_value("active_game_visual_request", {})
        if latest and (not latest.get("invalidated") or had_facts):
            await self._store_value("last_local_vision", {
                **latest, "invalidated": True, "invalidated_reason": reason,
                # Retain the last read for labelled panel history. The
                # invalidated bit keeps every current-answer projection empty.
            })
        if had_facts:
            self._push_current_visual_context(await self._current_visual_evidence())

    async def _publish_scene_preview(self, preview: dict[str, Any]) -> None:
        """Publish an explicit title early, with no inherited character facts."""
        epoch = getattr(self, "_observation_epoch", 0)
        latest = getattr(self, "_committed_observation", None)
        if latest is None:
            latest = unwrap_or(await self._get_store("last_local_vision"), {}) or {}
        if epoch != getattr(self, "_observation_epoch", 0) or getattr(self, "_observer_stop_requested", False):
            return
        previous_page = (latest.get("page") or {}).get("page_type")
        next_page = preview["page"]["page_type"]
        # A title preview is not a replacement for a full same-page read.
        # Query freshness remains controlled by validation, not empty previews.
        if (
            not latest.get("invalidated") and previous_page == next_page
            and (latest.get("screen_facts", {}).get("facts") or latest.get("characters"))
        ):
            return
        if previous_page and previous_page != next_page and not latest.get("invalidated"):
            from .runtime.revocation_trace import cause
            with cause(origin="scene_preview", basis="page_classification", candidate=True):
                await self._invalidate_current_observation("new_page_title")
            if getattr(self, "_observation_epoch", 0) != epoch + 1 or getattr(self, "_observer_stop_requested", False):
                return
        self._observation_serial = getattr(self, "_observation_serial", 0) + 1
        self._scene_preview = {
            **preview, "source": "assistant_scene_preview",
            "observed_at": preview.get("observed_at") or getattr(self, "_analysis_capture_at", utc_now_iso()),
            "observation_serial": self._observation_serial,
            "runtime": {"stable_scene": preview["scene"]["primary_state"], "stable_character_ids": []},
        }
        self._preview_frame_stamp = getattr(self, "_analysis_frame_stamp", None)
        self._observation_invalidated = False
        self._observer_status["state"] = "observing"
        await self._store_value("current_game_state", build_game_state_observation(
            primary_state=preview["scene"]["primary_state"], substate=next_page,
            confidence="high", evidence=preview["scene"]["evidence"],
            summary=f"已读取页面标题：{preview['page']['label']}；角色仍在识别",
            source="assistant_scene_preview", verification="verified",
        ))
        self._push_current_visual_context(await self._current_visual_evidence())
        if hasattr(self, "_visual_update_event"):
            self._visual_update_event.set()

    def _start_data_pack_task(self, *, force: bool = False) -> None:
        if self._data_pack_task and not self._data_pack_task.done():
            return
        if self._catalog.status().get("installed") and not self._catalog.status().get("update_available") and self._avatars.status().get("installed") and not force:
            self._data_pack_runtime = self._catalog.status()
            return
        self._data_pack_runtime = {
            **self._catalog.status(),
            "state": "installing",
            "message": "正在准备星铁资料组件",
        }
        self._data_pack_task = asyncio.create_task(self._install_data_pack(force=force), name="hsr-companion-data-pack")

    async def _install_data_pack(self, *, force: bool = False) -> None:
        try:
            # Portrait preparation is independent: existing knowledge stays usable.
            avatar_task = asyncio.create_task(asyncio.to_thread(self._avatars.install))
            try:
                status = self._catalog.status()
                if force or not status.get("installed") or status.get("update_available"):
                    status = await asyncio.to_thread(self._catalog.install_pinned)
            finally:
                await avatar_task
            self._data_pack_runtime = status
            if session_is_active(await self._read_session(), ttl_seconds=self._session_ttl_seconds):
                await self._push_companion_context("knowledge_pack_ready")
        except asyncio.CancelledError:
            self._data_pack_runtime = {
                **self._catalog.status(),
                "state": "missing",
                "message": "资料组件尚未准备，可在面板一键重试",
            }
            raise
        except Exception as exc:  # noqa: BLE001 - surfaced in panel, minimal registry remains safe
            self.logger.warning("HSR data pack preparation failed: %s", exc)
            self._data_pack_runtime = {
                **self._catalog.status(),
                "state": "error",
                "message": "资料组件准备失败，可在面板重试；角色防幻觉名单仍然有效",
                "error": f"{type(exc).__name__}: {exc}",
            }

    def _public_data_pack_status(self) -> dict[str, Any]:
        status = dict(self._data_pack_runtime or self._catalog.status())
        status.pop("database", None)
        status.pop("root", None)
        return status

    async def _wait_for_data_pack(self, *, force: bool = False) -> dict[str, Any]:
        self._start_data_pack_task(force=force)
        task = self._data_pack_task
        if task is not None:
            await task
        return self._public_data_pack_status()

    async def _apply_local_analysis(
        self,
        analysis: dict[str, Any],
        *,
        image_fingerprint: str,
        source: str,
        deliver_event: bool = True,
        observed_at: str | None = None,
    ) -> dict[str, Any]:
        """Commit only stabilized local observations and deliver one event at most."""

        epoch = getattr(self, "_observation_epoch", 0)
        corrections = await self._read_corrections()
        if epoch != getattr(self, "_observation_epoch", 0) or getattr(self, "_observer_stop_requested", False):
            return {"delivery": {"submitted": False, "reason": "observation_revoked"}}
        corrected_characters: list[dict[str, Any]] = []
        for candidate in list(analysis.get("characters") or []):
            if not isinstance(candidate, dict):
                continue
            record = self._catalog.get(str(candidate.get("entity_id") or ""))
            if not record or record.get("entity_type") != "character" or record.get("name") != candidate.get("name"):
                continue
            observed_key = normalize_observed_name(candidate.get("name"))
            candidate_region_fingerprint = str(candidate.get("visual_fingerprint") or "")
            matched = [
                item
                for item in corrections
                if isinstance(item, dict)
                and item.get("modality") == "vision"
                and item.get("scope") == "visual_fingerprint"
                and normalize_observed_name(item.get("observed_name")) == observed_key
                and candidate_region_fingerprint
                and visual_fingerprints_match(
                    item.get("scope_value"),
                    candidate_region_fingerprint,
                    max_distance=4,
                )
            ]
            if not matched:
                corrected_characters.append(dict(candidate))
                continue
            matched.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
            correction = matched[0]
            record = self._catalog.resolve_character(str(correction.get("canonical_name") or ""))
            if record:
                corrected_characters.append(
                    {
                        **dict(candidate),
                        "entity_id": record["id"],
                        "name": record["name"],
                        "evidence": "player_visual_region_correction",
                        "confidence": "high",
                        "correction_id": correction.get("correction_id"),
                    }
                )
        analysis = {**analysis, "characters": corrected_characters}
        from .runtime.fact_lifecycle import incomplete_same_page_read
        if source.startswith(("assistant", "plugin_compatibility")) and incomplete_same_page_read(self, analysis):
            # Do not let a weak same-page reread overwrite an independently
            # checkable complete result. Missing text is not new item data;
            # changed values/objects still replace the old commit normally.
            trace_refresh(self, "candidate_retained", reason="same_page_incomplete_read")
            from .runtime.page_control_trace import record_controls
            record_controls(self, "retained", analysis=analysis)
            if hasattr(self, "_visual_update_event"):
                self._visual_update_event.set()
            return {"delivery": {"submitted": False, "reason": "same_page_incomplete_read"}}
        await self._store_value(
            "active_game_visual_request",
            {
                "fingerprint": image_fingerprint,
                "created_at": utc_now_iso(),
                "source": source,
                "image_stored": False,
                "character_regions": [
                    {
                        "name": str(item.get("name") or ""),
                        "fingerprint": str(item.get("visual_fingerprint") or ""),
                    }
                    for item in corrected_characters
                    if item.get("visual_fingerprint")
                ],
            },
        )

        scene = analysis.get("scene") or {}
        if (
            (analysis.get("recognition") or {}).get("scene_only_query")
            and scene.get("verified") and scene.get("confidence") == "high"
            and self._runtime.stable_scene != scene.get("primary_state")
        ):
            # A query-only capture already passed latest-control validation;
            # do not keep the previous scene's two-full-frame waiting gate.
            self._runtime.invalidate_observation()
        event, decisions = self._runtime.ingest(analysis)
        scene = dict(analysis.get("scene") or {})
        stable_scene = self._runtime.stable_scene
        accepted = bool(stable_scene != "unknown" and stable_scene == scene.get("primary_state"))
        if accepted:
            observation = build_game_state_observation(
                primary_state=stable_scene,
                substate=str(scene.get("substate") or ""),
                confidence=str(scene.get("confidence") or "mixed"),
                evidence=list(scene.get("evidence") or []),
                summary=(
                    f"本地页面识别确认："
                    f"{(analysis.get('page') or {}).get('label') or GAME_STATE_LABELS.get(stable_scene, stable_scene)}"
                ),
                source=source,
                verification="verified",
                visual_fingerprint=image_fingerprint,
            )
            await self._store_value("current_game_state", observation)
            await self._store_value("pending_game_state_candidate", {})
            history = await self._read_game_state_history()
            history.append(
                {
                    **observation,
                    "accepted": True,
                    "transition_reason": "local_runtime_stabilized",
                }
            )
            await self._store_value("game_state_history", history[-50:])
        else:
            # Do not leave a prior menu/battle in the authoritative current slot.
            await self._store_value("current_game_state", {})
            await self._store_value("pending_game_state_candidate", {})

        self._observation_serial = getattr(self, "_observation_serial", 0) + 1
        public_analysis = {
            "observation_serial": self._observation_serial,
            "ok": bool(analysis.get("ok")),
            "scene": scene,
            "page": dict(analysis.get("page") or {}),
            "characters": list(analysis.get("characters") or []),
            "recognition": dict(analysis.get("recognition") or {}),
            "selection": dict(analysis.get("selection") or {}),
            "screen_facts": dict(analysis.get("screen_facts") or {}),
            "ocr": dict(analysis.get("ocr") or {}),
            "catalog_revision": self._catalog.revision,
            "image_stored": False,
            "visual_fingerprint": image_fingerprint,
            "source": source,
            "observed_at": observed_at or utc_now_iso(),
            "runtime": self._runtime.snapshot(),
            "decisions": decisions,
        }
        await self._store_value("last_local_vision", public_analysis)
        if epoch != getattr(self, "_observation_epoch", 0) or getattr(self, "_observer_stop_requested", False):
            return {"delivery": {"submitted": False, "reason": "observation_revoked"}}
        trace_refresh(self, "observation_committed", serial=public_analysis["observation_serial"])
        ObservationStore(self).commit(public_analysis)
        if public_visual_evidence(public_analysis, {"enabled": True, "state": "observing"})["read_scope"] == "full":
            self._last_full_commit_serial = self._observation_serial
        self._scene_preview = None
        self._observation_invalidated = False
        self._accepted_frame_stamp = getattr(self, "_next_frame_stamp", None)
        self._accepted_frame_page_type = public_analysis["page"].get("page_type", "unknown")
        self._accepted_frame_identity_guarded = getattr(self, "_next_frame_identity_guarded", False)
        self._accepted_frame_characters = [*public_analysis["characters"], *fact_regions(public_analysis)]
        self._accepted_frame_scene_only = getattr(self, "_next_frame_scene_only", False)
        self._accepted_frame_scene_regions = getattr(self, "_next_frame_scene_regions", ())
        self._accepted_frame_page_regions = getattr(self, "_next_frame_page_regions", ())
        from .runtime.page_control_trace import record_controls
        record_controls(self, "committed", analysis=public_analysis, controls=self._accepted_frame_page_regions)
        self._accepted_frame_delivery_guard = getattr(self, "_next_frame_delivery_guard", None)
        self._accepted_frame_query_guard = getattr(self, "_next_frame_query_guard", None)
        self._accepted_frame_full_query_guard = getattr(self, "_next_frame_full_query_guard", None)
        validation = analysis.get("_live_validation") or {}
        self._producer_validation = ({**validation, "serial": self._observation_serial,
            "challenge_revision": getattr(self, "_review_challenge_serial", 0),
            "capture_failure_revision": getattr(self, "_capture_failure_revision", 0)} if validation else None)
        if validation:
            from .runtime.fact_lifecycle import record_verified_regions
            guard = self._accepted_frame_delivery_guard
            if guard:
                record_verified_regions(self, {p[0] for p in guard[1]}, validation.get("checked_frame_at"))
        if (validation.get("epoch") == epoch
                and validation.get("query_generation") == getattr(self, "_query_refresh_generation", 0)):
            self._query_validated_read = (validation["query_generation"], epoch, self._observation_serial)
            trace_refresh(self, "completed_read_revalidated", serial=self._observation_serial)
        from .runtime.query_reuse import remember_reusable
        remember_reusable(self, public_analysis)
        if hasattr(self, "_visual_update_event"):
            self._visual_update_event.set()
        self._push_current_visual_context(await self._current_visual_evidence())

        delivery: dict[str, Any] = {"submitted": False, "reason": "no_stable_event"}
        query_busy = (getattr(self, "_visual_query_waiters", 0) > 0 or getattr(self, "_pending_answer", None) is not None
                      or time.monotonic() - getattr(self, "_last_visual_query_at", -1e9) < 15)
        if event is not None and deliver_event and not query_busy:
            observer_settings = await self._read_observer_settings()
            plan = build_event_delivery(
                event,
                catalog_revision=self._catalog.revision,
                proactive_enabled=bool(observer_settings.get("proactive_enabled", True)),
            )
            if plan.ai_behavior == "respond":
                delivery = self._queue_automatic_speech(event, plan, await self._current_visual_evidence())
            else:
                # A passive event adds no new facts beyond the context already
                # committed above. Do not fetch a second frame just to repeat it.
                delivery = {"submitted": False, "reason": "passive_context_already_synced"}
            delivery["ai_behavior"] = plan.ai_behavior
            delivery["event_kind"] = event.kind
            delivery["proactive_requested"] = bool(plan.metadata.get("proactive_requested"))
        return {**public_analysis, "delivery": delivery}

    def _start_observer_task(self, *, restored: bool = False) -> None:
        if getattr(self, "_observer_stop_requested", False):
            return
        self._observer_status["enabled"] = True
        if self._assistant_operation_in_progress:
            return
        if self._observer_task and not self._observer_task.done():
            return
        self._set_observer_status(
            "waiting",
            "正在寻找《崩坏：星穹铁道》窗口",
            restored=restored,
        )
        self._observer_wake_event = asyncio.Event()
        self._observer_task = asyncio.create_task(self._observer_loop(), name="hsr-companion-observer")

    async def _wait_observer_tick(self, delay: float) -> None:
        wake = getattr(self, "_observer_wake_event", None)
        if wake is None:
            await asyncio.sleep(delay)
            return
        try:
            await asyncio.wait_for(wake.wait(), timeout=delay)
        except TimeoutError:
            pass
        finally:
            wake.clear()

    async def _cancel_observer_task(self, *, update_status: bool = True) -> None:
        cancel_followup(self, "observation_stopped")
        if update_status:
            self._stop_observation_now()
        else:
            self._revoke_current_observation()
            self._push_current_visual_context(public_visual_evidence(
                {"invalidated": True, "source": "assistant_local_ocr"}, self._observer_status))
        self._cancel_pending_speech("observation_stopped")
        task = self._observer_task
        self._observer_task = None
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        # Stop the producer before draining its last speech task. Otherwise
        # it could create a new task while cancellation yields to the loop.
        self._cancel_pending_speech("observation_stopped")
        speech_task = getattr(self, "_speech_task", None)
        if speech_task and not speech_task.done():
            try:
                await speech_task
            except asyncio.CancelledError:
                pass
        self._speech_task = None
        await drain_analysis_worker(self)
        self._observer_last_signature = None
        self._observer_last_push_monotonic = 0.0
        self._assistant_frame_seq = 0
        await asyncio.to_thread(self._assistant_process.stop)
        await self._invalidate_current_observation("observation_stopped")
        if update_status:
            self._observer_status = {
                "enabled": False,
                "proactive_enabled": bool(self._observer_status.get("proactive_enabled", True)),
                "state": "stopped",
                "message": "自动观察已关闭",
                "updated_at": utc_now_iso(),
            }

    async def _observer_loop(self) -> None:
        while True:
            try:
                # Prefer the independent loopback assistant.  If the helper is
                # unavailable, the in-process compatibility path must also
                # use isolated window capture. Never fall back to desktop
                # pixels containing the character overlay or chat bubbles.
                health = await asyncio.to_thread(self._assistant_client.health)
                if not health.get("connected"):
                    await asyncio.to_thread(self._assistant_process.start_if_due)
                    await asyncio.sleep(0.25)
                    health = await asyncio.to_thread(self._assistant_client.health)
                helper_connected = bool(health.get("connected"))
                image_bytes: bytes
                mime: str
                signature: bytes
                public_window: dict[str, Any]
                capture_backend = "assistant"
                assistant_frame = None
                if helper_connected:
                    if int(health.get("frame_seq") or 0) < self._assistant_frame_seq:
                        self._assistant_frame_seq = 0
                    game = dict(health.get("game") or {})
                    capture = dict(health.get("capture") or {})
                    public_window = {
                        key: value
                        for key, value in game.items()
                        if key in {"title", "pid", "process_name", "left", "top", "width", "height", "minimized", "foreground"}
                    }
                    if capture.get("state") == "error":
                        await self._invalidate_current_observation("capture_error")
                        self._set_observer_status(
                            "error",
                            str(capture.get("message") or "视觉助手已连接，但读取游戏画面失败，请重新检查连接。"),
                            window=public_window,
                            assistant={**self._assistant_process.snapshot(), **health},
                            capture_backend=capture_backend,
                        )
                        await self._wait_observer_tick(3)
                        continue
                    if not game.get("running"):
                        await self._invalidate_current_observation("game_closed")
                        self._announced_game_pid = 0
                        self._set_observer_status(
                            "waiting",
                            "后台感知已就绪，正在等待《崩坏：星穹铁道》",
                            assistant={**self._assistant_process.snapshot(), **health},
                            capture_backend=capture_backend,
                        )
                        await self._wait_observer_tick(2)
                        continue
                    if capture.get("state") == "paused":
                        await self._invalidate_current_observation("capture_paused")
                        reason = str(capture.get("reason") or "")
                        self._set_observer_status(
                            "paused",
                            "星铁已最小化，后台感知已暂停"
                            if reason == "game_minimized"
                            else "当前助手仍要求游戏处于前台，请更新独立视觉助手"
                            if reason == "game_not_foreground"
                            else str(capture.get("message") or "游戏窗口暂时无法采集，恢复显示后会继续观察"),
                            window=public_window,
                            assistant={**self._assistant_process.snapshot(), **health},
                            capture_backend=capture_backend,
                        )
                        await self._wait_observer_tick(2)
                        continue
                    if capture.get("state") == "capturing" and capture.get("isolated_window") is not True:
                        await self._invalidate_current_observation("legacy_desktop_capture")
                        self._set_observer_status(
                            "error", "请更新独立视觉助手：旧版本会把猫娘和气泡截进游戏画面，已暂停使用。",
                            window=public_window, assistant={**self._assistant_process.snapshot(), **health},
                            capture_backend="legacy_desktop_rejected",
                        )
                        await self._wait_observer_tick(3)
                        continue
                    assistant_frame = await asyncio.to_thread(
                        self._assistant_client.fetch_frame,
                        self._assistant_frame_seq,
                    )
                    if assistant_frame is None:
                        if capture.get("frame_semantics") == "every_capture":
                            # Modern helpers already own an independent capture
                            # session. No new sequence is a temporary wait, not
                            # permission to start a competing in-process session.
                            self._set_observer_status(
                                "updating" if getattr(self, "_observation_pending_reason", "") else "observing",
                                "正在等待新的游戏画面", window=public_window,
                                assistant={**self._assistant_process.snapshot(), **health},
                                capture_backend=capture_backend,
                            )
                            await self._wait_observer_tick(.25)
                            continue
                        elapsed = time.monotonic() - self._observer_last_push_monotonic
                        pending = bool(self._runtime.pending_scene or self._runtime.pending_character_ids)
                        if (
                            getattr(self, "_visual_query_waiters", 0) > 0 or getattr(self, "_pending_answer", None) is not None
                            or getattr(self, "_observation_invalidated", False)
                            or (pending and elapsed >= 3) or elapsed >= 4
                        ):
                            # Compatibility with released helpers that sequence
                            # only changed pixels: independently capture AGAIN,
                            # never count a cached screenshot as a second frame.
                            window = await asyncio.to_thread(find_hsr_window)
                            if window is None or window.minimized:
                                await self._invalidate_current_observation("capture_unavailable")
                                await self._wait_observer_tick(1.5)
                                continue
                            image_bytes, mime, signature = await asyncio.to_thread(capture_game_window, window)
                            public_window = window.public()
                            capture_backend = "plugin_compatibility"
                        else:
                            self._set_observer_status(
                                "observing", "后台感知运行中，画面没有明显变化",
                                window=public_window, assistant={**self._assistant_process.snapshot(), **health},
                                capture_backend=capture_backend,
                                last_frame_at=self._observer_status.get("last_frame_at", ""),
                                last_delivery=self._observer_status.get("last_delivery", {}),
                            )
                            await self._wait_observer_tick(1.5)
                            continue
                    else:
                        self._assistant_frame_seq = assistant_frame.frame_seq
                        image_bytes = assistant_frame.image_bytes
                        mime = assistant_frame.mime
                        signature = assistant_frame.signature.encode("ascii", "ignore")
                        public_window = {
                            key: value for key, value in assistant_frame.game.items()
                            if key in {"title", "pid", "process_name", "left", "top", "width", "height", "minimized", "foreground"}
                        }
                else:
                    capture_backend = "plugin_compatibility"
                    window = await asyncio.to_thread(find_hsr_window)
                    if window is None:
                        await self._invalidate_current_observation("game_closed")
                        self._set_observer_status(
                            "waiting",
                            "备用识别正在等待星铁窗口；独立助手尚未连接",
                            assistant={**self._assistant_process.snapshot(), **health},
                            capture_backend=capture_backend,
                        )
                        await self._wait_observer_tick(3)
                        continue
                    public_window = window.public()
                    if window.minimized:
                        await self._invalidate_current_observation("capture_paused")
                        self._set_observer_status(
                            "paused", "星铁已最小化，自动观察已暂停", window=public_window,
                            capture_backend=capture_backend,
                            assistant={**self._assistant_process.snapshot(), **health},
                        )
                        await self._wait_observer_tick(3)
                        continue
                    image_bytes, mime, signature = await asyncio.to_thread(capture_game_window, window)
                if assistant_frame is not None and (getattr(self, "_visual_query_waiters", 0) > 0 or getattr(self, "_pending_answer", None) is not None) and not 0 <= time.time() - assistant_frame.timestamp <= 2:
                    if capture.get("frame_semantics") == "every_capture":
                        trace_refresh(self, "capture_rejected", reason="stale_frame")
                        await self._invalidate_current_observation("capture_unavailable_stale_frame")
                        self._set_observer_status("updating", "正在等待新的游戏画面")
                        await self._wait_observer_tick(.25)
                        continue
                    # Never spend a query's OCR budget on a known-old helper
                    # frame. Recapture this exact game window, not the desktop.
                    trace_refresh(self, "capture_rejected", reason="stale_frame")
                    window = await asyncio.to_thread(find_hsr_window)
                    if window is None or window.minimized or window.public().get("pid") != public_window.get("pid"):
                        await self._invalidate_current_observation("capture_unavailable")
                        await self._wait_observer_tick(.25)
                        continue
                    image_bytes, mime, signature = await asyncio.to_thread(capture_game_window, window)
                    public_window, assistant_frame, capture_backend = window.public(), None, "plugin_compatibility"
                if getattr(self, "_observer_stop_requested", False):
                    return
                self._observer_status["state"] = "observing"
                self._announce_game_presence(public_window)
                now = time.monotonic()
                elapsed = now - self._observer_last_push_monotonic
                difference = frame_difference(self._observer_last_signature, signature)
                self._watch_frame_timestamp = assistant_frame.timestamp if assistant_frame is not None else None
                changed = await watch_latest_frame(self, image_bytes, helper=capture_backend == "assistant")
                should_push = (
                    changed or self._observer_last_signature is None
                    or getattr(self, "_visual_query_waiters", 0) > 0 or getattr(self, "_pending_answer", None) is not None
                    or (elapsed >= 1.5 and bool(self._runtime.pending_scene or self._runtime.pending_character_ids))
                    or (elapsed >= 2 and difference >= 0.025)
                    or elapsed >= 4
                )
                if should_push:
                    if getattr(self, "_active_refresh_trace", None) is not None:
                        trace_refresh(self, "capture_selected", backend=capture_backend,
                                      age_seconds=round(time.time() - assistant_frame.timestamp, 3) if assistant_frame is not None else 0)
                    capture_started_at = time.monotonic()
                    captured_at = (
                        datetime.fromtimestamp(assistant_frame.timestamp, timezone.utc).isoformat()
                        if assistant_frame is not None else utc_now_iso()
                    )
                    self._analysis_capture_at = captured_at
                    self._analysis_game_pid = public_window.get("pid", 0)
                    image_fingerprint = await asyncio.to_thread(visual_fingerprint, image_bytes)
                    await self._store_value(
                        "active_game_visual_request",
                        {
                            "fingerprint": image_fingerprint,
                            "created_at": utc_now_iso(),
                            "mime": mime,
                            "source": "auto_window",
                            "image_stored": False,
                        },
                    )
                    # Released helpers may sequence only changed pixels. The
                    # independently recaptured compatibility frame still uses
                    # that connected helper to monitor transitions and publish
                    # early scene evidence; do not await the whole OCR pass.
                    analysis = await guarded_analysis(self, image_bytes, helper=helper_connected)
                    if analysis is None:
                        self._set_observer_status("updating", "正在核对当前界面信息")
                        await asyncio.sleep(.1)
                        continue
                    # Focus can move to chat during OCR without invalidating
                    # the isolated game surface. Closure/minimization cannot.
                    if capture_backend == "assistant":
                        trace_refresh(self, "health_check_started")
                        check = await asyncio.to_thread(self._assistant_client.health)
                        trace_refresh(self, "health_check_finished")
                        still_current = bool(
                            check.get("connected") and (check.get("game") or {}).get("running")
                            and not (check.get("game") or {}).get("minimized")
                            and (check.get("capture") or {}).get("state") == "capturing"
                            and (check.get("capture") or {}).get("isolated_window") is True
                            and (check.get("game") or {}).get("pid") == public_window.get("pid")
                        )
                    else:
                        check_window = await asyncio.to_thread(find_hsr_window)
                        still_current = bool(
                            check_window and not check_window.minimized
                            and check_window.public().get("pid") == public_window.get("pid")
                        )
                    if not still_current:
                        await self._invalidate_current_observation("capture_changed_during_analysis")
                        self._set_observer_status("paused", "分析期间游戏窗口不可用或已变化，等待新的游戏画面")
                        await self._wait_observer_tick(1.5)
                        continue
                    # Long successful production is a candidate, not a fresh
                    # answer. Its storage must not depend on a question's
                    # generation or on the age of the verifier's input frame.
                    # A current answer still needs its own scoped live receipt.
                    if time.monotonic() - capture_started_at > 30:
                        from .runtime.fact_lifecycle import late_read_proved
                        if not late_read_proved(self, analysis):
                            self._set_observer_status("updating", "本轮识别耗时较长，保留已确认结果并继续核对")
                            await self._wait_observer_tick(1.5)
                            continue
                        trace_refresh(self, "late_validated_candidate", answer_receipt_not_implied=True)
                    # 0.9 no longer relies on N.E.K.O's video/vision chat to
                    # interpret every frame. Only locally verified structured
                    # facts are delivered below; the raw frame stays inside
                    # the helper/plugin process and is never pushed to chat.
                    await self._store_value(
                        "active_game_visual_request",
                        {
                            "fingerprint": image_fingerprint,
                            "created_at": utc_now_iso(),
                            "mime": mime,
                            "source": "auto_window",
                            "image_stored": False,
                            "character_regions": [
                                {
                                    "name": str(item.get("name") or ""),
                                    "fingerprint": str(item.get("visual_fingerprint") or ""),
                                }
                                for item in analysis.get("characters") or []
                                if isinstance(item, dict) and item.get("visual_fingerprint")
                            ],
                        },
                    )
                    trace_refresh(self, "commit_started")
                    local_result = await self._apply_local_analysis(
                        analysis,
                        image_fingerprint=image_fingerprint,
                        observed_at=captured_at,
                        source=(
                            "assistant_local_ocr"
                            if assistant_frame is not None
                            else "plugin_compatibility_local_ocr"
                        ),
                    )
                    trace_refresh(self, "commit_finished")
                    self._observer_last_signature = signature
                    self._observer_last_push_monotonic = time.monotonic()
                    scene_key = str((analysis.get("scene") or {}).get("primary_state") or "unknown")
                    scene_label = GAME_STATE_LABELS.get(scene_key, GAME_STATE_LABELS["unknown"])
                    names = [
                        str(item.get("name") or "")
                        for item in analysis.get("characters") or []
                        if isinstance(item, dict)
                    ]
                    self._set_observer_status(
                        "observing",
                        f"本地识别：{scene_label}",
                        window=public_window,
                        last_frame_at=utc_now_iso(),
                        scene=scene_key,
                        recognized_characters=names,
                        ocr=dict(analysis.get("ocr") or {}),
                        capture_backend=capture_backend,
                        assistant={**self._assistant_process.snapshot(), **health},
                        runtime=self._runtime.snapshot(),
                        last_delivery={
                            "submitted": bool((local_result.get("delivery") or {}).get("submitted")),
                            "reason": str((local_result.get("delivery") or {}).get("reason") or ""),
                            "ai_behavior": str((local_result.get("delivery") or {}).get("ai_behavior") or ""),
                            "event_kind": str((local_result.get("delivery") or {}).get("event_kind") or ""),
                            "proactive_requested": bool(
                                (local_result.get("delivery") or {}).get("proactive_requested")
                            ),
                        },
                    )
                else:
                    self._set_observer_status(
                        "observing",
                        "画面没有明显变化，继续观察中",
                        window=public_window,
                        capture_backend=capture_backend,
                        assistant={**self._assistant_process.snapshot(), **health},
                        last_frame_at=self._observer_status.get("last_frame_at", ""),
                        last_delivery=self._observer_status.get("last_delivery", {}),
                    )
                await asyncio.sleep(0.25)
            except asyncio.CancelledError:
                raise
            except RuntimeError as exc:
                await self._invalidate_current_observation("capture_error")
                self._set_observer_status("error", str(exc))
                await self._wait_observer_tick(3)
            except Exception as exc:  # keep the plugin alive if capture fails
                await self._invalidate_current_observation("capture_error")
                self.logger.warning("HSR observer paused: %s", exc, exc_info=True)
                self._set_observer_status("error", "暂时无法读取星铁窗口，插件会自动重试")
                await self._wait_observer_tick(5)

    async def _store_value(self, key: str, value: Any) -> None:
        # Effective config can be refreshed after startup; a disabled SDK
        # store otherwise acknowledges writes without persisting the value.
        self.store.enabled = True
        stored = await self.store.set(key, value)
        if isinstance(stored, Err):
            raise RuntimeError(f"STORE_FAILED:{key}")

    async def _get_store(self, key: str):
        # Only this plugin's public SDK KV store, never the host's storage.
        self.store.enabled = True
        return await self.store.get(key)

    async def _set_user_operation(
        self,
        operation: str,
        state: str,
        message: str,
    ) -> dict[str, str]:
        """Persist the last user-visible operation result until it is replaced."""

        payload = {
            "operation": str(operation or "general"),
            "state": str(state or "info"),
            "message": str(message or ""),
            "updated_at": utc_now_iso(),
        }
        await self._store_value("last_user_operation", payload)
        return payload

    async def _read_user_operation(self) -> dict[str, Any]:
        value = unwrap_or(await self._get_store("last_user_operation"), {})
        return dict(value) if isinstance(value, dict) else {}

    async def _read_observer_settings(self) -> dict[str, Any]:
        value = unwrap_or(await self._get_store("observer_settings"), {})
        settings = dict(value) if isinstance(value, dict) else {}
        settings["enabled"] = bool(settings.get("enabled", False))
        settings["proactive_enabled"] = bool(settings.get("proactive_enabled", True))
        return settings

    async def _read_session(self) -> dict[str, Any]:
        value = unwrap_or(await self._get_store("companion_session"), {})
        return value if isinstance(value, dict) else {}

    async def _read_corrections(self) -> list[dict[str, Any]]:
        value = unwrap_or(await self._get_store("character_corrections"), [])
        if not isinstance(value, list):
            return []
        corrections = [dict(item) for item in value if isinstance(item, dict)]
        sanitized, disabled = sanitize_character_corrections(corrections, registry=self._characters)
        if disabled:
            # Run the migration at the read boundary as well as startup. This
            # makes a hot-reloaded plugin safe before its next full restart.
            await self._store_value("character_corrections", sanitized)
        return sanitized

    async def _read_context_audit(self) -> list[dict[str, Any]]:
        value = unwrap_or(await self._get_store("context_audit"), [])
        if not isinstance(value, list):
            return []
        return [dict(item) for item in value if isinstance(item, dict)]

    async def _read_tool_audit(self) -> list[dict[str, Any]]:
        value = unwrap_or(await self._get_store("tool_audit"), [])
        if not isinstance(value, list):
            return []
        return [dict(item) for item in value if isinstance(item, dict)]

    async def _record_tool_use(self, tool_name: str, *, mark_query: bool = True, **details: Any) -> None:
        # Other capability tools also compete with unsolicited conversation.
        # This is a quiet window, not a claim that the answer was played.
        if mark_query:
            self._note_speech_query()
        records = await self._read_tool_audit()
        records.append(
            {
                "at": utc_now_iso(),
                "tool": str(tool_name or ""),
                **{key: value for key, value in details.items() if value not in (None, "")},
            }
        )
        await self._store_value("tool_audit", records[-self._tool_audit_limit :])

    async def _append_context_audit(self, record: dict[str, Any]) -> None:
        records = await self._read_context_audit()
        records.append(dict(record))
        await self._store_value("context_audit", records[-self._context_audit_limit :])

    async def _read_visual_request(self) -> dict[str, Any]:
        value = unwrap_or(await self._get_store("active_visual_request"), {})
        return value if isinstance(value, dict) else {}

    async def _read_game_visual_request(self) -> dict[str, Any]:
        value = unwrap_or(await self._get_store("active_game_visual_request"), {})
        return value if isinstance(value, dict) else {}

    async def _read_game_state(self) -> dict[str, Any]:
        value = unwrap_or(await self._get_store("current_game_state"), {})
        if isinstance(value, dict) and str(value.get("source") or "").startswith(("assistant", "plugin_compatibility", "auto_")):
            visual = await self._current_visual_evidence()
            if not visual["fresh"] or not visual["scene"]["verified"]:
                return {}
        return value if isinstance(value, dict) else {}

    async def _read_game_state_history(self) -> list[dict[str, Any]]:
        value = unwrap_or(await self._get_store("game_state_history"), [])
        if not isinstance(value, list):
            return []
        return [dict(item) for item in value if isinstance(item, dict)]

    async def _read_pending_game_state(self) -> dict[str, Any]:
        value = unwrap_or(await self._get_store("pending_game_state_candidate"), {})
        return value if isinstance(value, dict) else {}

    async def _touch_session(self, reason: str) -> tuple[dict[str, Any], bool]:
        current = await self._read_session()
        was_active = session_is_active(current, ttl_seconds=self._session_ttl_seconds)
        session = start_session(current, reason=reason)
        await self._store_value("companion_session", session)
        return session, not was_active

    async def _push_companion_context(self, reason: str) -> dict[str, Any]:
        session, _ = await self._touch_session(reason)
        profile = await self._read_profile()
        pending = await self._read_pending_profile()
        corrections = await self._read_corrections()
        game_state = public_game_state(await self._read_game_state())
        current_visual = await self._current_visual_evidence()
        if not current_visual.get("fresh"):
            game_state = public_game_state({})
        context_text = build_companion_context(
            session=session,
            profile=profile,
            pending=pending,
            corrections=corrections,
            registry_meta=self._characters.meta,
            registry_count=len(self._characters.records),
            catalog_meta=self._catalog.meta,
            game_state=game_state,
            current_visual=current_visual,
        )
        push_result = self.push_message(
            visibility=[],
            ai_behavior="read",
            parts=[{"type": "text", "text": context_text}],
            source="hsr_companion",
            metadata={"kind": "hsr_session_context", "reason": reason},
            priority=4,
            coalesce_key="hsr_session_context",
        )
        delivery = dict(push_result) if isinstance(push_result, dict) else {}
        session["context_push_count"] = int(session.get("context_push_count") or 0) + 1
        session["last_context_push_at"] = utc_now_iso()
        session["last_context_reason"] = str(reason or "")
        session["last_context_delivery"] = {
            "submitted": bool(delivery.get("submitted")),
            "reason": str(delivery.get("reason") or ""),
        }
        await self._store_value("companion_session", session)
        await self._append_context_audit(
            {
                "at": session["last_context_push_at"],
                "reason": reason,
                "submitted": bool(delivery.get("submitted")),
                "delivery_reason": str(delivery.get("reason") or ""),
                "profile_character_count": len(profile.get("owned_characters") or []),
                "correction_count": len(corrections),
                "pending": bool(pending),
                "game_state": game_state.get("primary_state") or "unknown",
            }
        )
        return {"session": session, "delivery": delivery, "context": context_text}

    async def _start_companion_session(self, reason: str) -> dict[str, Any]:
        return await self._push_companion_context(reason or "user_start")

    async def _stop_companion_session(self, reason: str) -> dict[str, Any]:
        current = await self._read_session()
        session = stop_session(current, reason=reason or "user_stop")
        await self._store_value("companion_session", session)
        push_result = self.push_message(
            visibility=[],
            ai_behavior="read",
            parts=[
                {
                    "type": "text",
                    "text": (
                        "[星铁陪玩会话结束]\n"
                        "玩家已经结束本次《崩坏：星穹铁道》陪玩。"
                        "请恢复普通聊天，不要把后续内容自动理解为星铁事件；"
                        "已确认的插件档案仍保留。"
                    ),
                }
            ],
            source="hsr_companion",
            metadata={"kind": "hsr_session_restore", "reason": reason},
            priority=4,
            coalesce_key="hsr_session_context",
        )
        delivery = dict(push_result) if isinstance(push_result, dict) else {}
        await self._append_context_audit(
            {
                "at": utc_now_iso(),
                "reason": reason or "user_stop",
                "submitted": bool(delivery.get("submitted")),
                "delivery_reason": str(delivery.get("reason") or ""),
                "event": "session_stopped",
            }
        )
        return {"session": session, "delivery": delivery}

    async def _read_profile(self) -> dict[str, Any]:
        value = unwrap_or(await self._get_store("player_context"), {})
        return value if isinstance(value, dict) else {}

    async def _imported_roster(self, *, details=False, names=None):
        try:
            return await player_build_service(self).roster(details=details, names=names)
        except (RuntimeError, ValueError, TypeError, KeyError):
            # An optional account component cannot break existing screen/tools.
            return {"state": "unavailable", "characters": [], "roster_complete": False,
                    "visually_confirmed": False, "instruction": "导入资料暂不可读，不代表玩家没有这些角色。"}

    async def _profile_for_advice(self):
        return profile_with_roster(await self._read_profile(), await self._imported_roster())

    async def _write_profile(
        self,
        changes: dict[str, Any],
        *,
        mode: str = "merge",
        clear_fields: list[str] | None = None,
    ) -> dict[str, Any]:
        validated_changes = self._validate_profile_changes(changes)
        profile = merge_player_context(
            await self._read_profile(),
            validated_changes,
            mode=mode,
            clear_fields=clear_fields or [],
        )
        stored = await self.store.set("player_context", profile)
        if isinstance(stored, Err):
            raise RuntimeError("PLAYER_CONTEXT_STORE_FAILED")
        return profile

    def _validate_profile_changes(self, changes: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(changes, dict):
            raise ValueError("玩家档案更新必须是对象")
        validated = dict(changes)
        for field in ("owned_characters", "favorite_characters", "training_targets"):
            if field not in validated or validated[field] is None:
                continue
            raw = validated[field]
            candidates = raw if isinstance(raw, list) else [raw]
            accepted, rejected = self._characters.validate_candidates(candidates)
            if rejected:
                raise ValueError(
                    "角色数据库未收录：" + "、".join(rejected) + "。为避免把 AI 编造内容写进档案，本次更新已全部取消。"
                )
            validated[field] = accepted
        return validated

    async def _read_pending_profile(self) -> dict[str, Any]:
        value = unwrap_or(await self._get_store("pending_player_context"), {})
        if not isinstance(value, dict):
            return {}
        pending = dict(value)
        applied = pending.get("player_correction_applied")
        if not isinstance(applied, dict) or not applied.get("correction_id"):
            return pending
        correction_id = str(applied.get("correction_id") or "")
        correction = next(
            (item for item in await self._read_corrections() if str(item.get("correction_id") or "") == correction_id),
            None,
        )
        if correction is None or correction.get("active") is not False:
            return pending

        # A v0.8.1 pending proposal may already contain the result of the
        # unsafe rewrite. Downgrade it in place so opening the panel cannot
        # keep resurfacing that name as a confirmable recognition.
        recognized = [str(item).strip() for item in pending.get("recognized_characters") or [] if str(item).strip()]
        uncertain = list(
            dict.fromkeys(
                [
                    *recognized,
                    *[str(item).strip() for item in pending.get("uncertain_characters") or [] if str(item).strip()],
                ]
            )
        )
        pending.update(
            {
                "recognized_characters": [],
                "uncertain_characters": uncertain,
                "can_confirm": False,
                "confidence": "low",
                "visual_verification": "invalidated_legacy_correction",
                "player_correction_applied": {
                    **applied,
                    "active": False,
                    "disabled_reason": str(correction.get("disabled_reason") or ""),
                },
            }
        )
        await self._store_value("pending_player_context", pending)
        return pending

    async def _stage_profile_proposal(
        self,
        recognized_characters: list[str],
        *,
        uncertain_characters: list[str] | None = None,
        import_mode: str = "merge",
        source: str = "chat",
        confidence: str = "mixed",
        note: str = "",
    ) -> dict[str, Any]:
        session, started = await self._touch_session(f"stage_profile:{source}")
        if started:
            await self._push_companion_context(f"auto_start:{source}")
            session = await self._read_session()
        corrections = await self._read_corrections()
        modality = "vision" if source == "screenshot" else "voice" if source == "voice" else "chat"
        visual_request: dict[str, Any] = {}
        if modality == "vision":
            visual_request = await self._read_visual_request()
            game_visual_request = await self._read_game_visual_request()
            if _request_is_recent(game_visual_request) and str(game_visual_request.get("fingerprint") or "") == str(
                visual_request.get("fingerprint") or ""
            ):
                # The generic request carries roster import mode; the local
                # analysis request carries the OCR character-name regions.
                visual_request = {**visual_request, **game_visual_request}
        recent_visual_request = modality != "vision" or _request_is_recent(visual_request)
        visual_id = str(visual_request.get("fingerprint") or "") if recent_visual_request else ""
        recognized_input = list(recognized_characters or [])
        uncertain_input = list(uncertain_characters or [])
        visual_verification = "not_visual"
        if modality == "vision":
            plugin_visual_names = {
                normalize_observed_name(item.get("name"))
                for item in list(visual_request.get("character_regions") or [])
                if isinstance(item, dict) and item.get("name") and item.get("fingerprint")
            }
            plugin_bound = [
                name
                for name in recognized_input
                if recent_visual_request and normalize_observed_name(name) in plugin_visual_names
            ]
            model_only = [name for name in recognized_input if name not in plugin_bound]
            # A dialog model naming a real roster entry only proves that the
            # name exists. Only names found in an OCR character-name region
            # are allowed into the recognized side of a proposal.
            recognized_input = plugin_bound
            uncertain_input = [*model_only, *uncertain_input]
            if model_only or not recent_visual_request:
                confidence = "low"
                note = (f"{str(note or '').strip()} 未绑定到插件角色名区域的模型视觉候选只能作为不确定项。").strip()
            visual_verification = (
                "plugin_ocr_bound"
                if plugin_bound and not model_only
                else "mixed_plugin_and_model"
                if plugin_bound
                else "unverified_model_candidate"
            )
        recognized_resolution = resolve_character_candidates(
            recognized_input,
            registry=self._characters,
            corrections=corrections,
            modality=modality,
            visual_id=visual_id,
            session_id=str(session.get("session_id") or ""),
        )
        uncertain_resolution = resolve_character_candidates(
            uncertain_input,
            registry=self._characters,
            corrections=corrections,
            modality=modality,
            visual_id=visual_id,
            session_id=str(session.get("session_id") or ""),
        )
        recognized = list(recognized_resolution["accepted"])
        recognized_keys = {name.casefold() for name in recognized}
        uncertain = [name for name in uncertain_resolution["accepted"] if name.casefold() not in recognized_keys]
        ambiguous_candidates = [
            *recognized_resolution["ambiguous_candidates"],
            *uncertain_resolution["ambiguous_candidates"],
        ]
        # A saved correction between two real registry characters is only a
        # reminder that the input is ambiguous. Present both choices for
        # review, but never silently promote either one to a recognized fact.
        for ambiguity in ambiguous_candidates:
            for alternative in list(ambiguity.get("alternatives") or []):
                name = str(alternative or "").strip()
                if name and name.casefold() not in recognized_keys and name not in uncertain:
                    uncertain.append(name)
        rejected = list(
            dict.fromkeys(
                [
                    *recognized_resolution["rejected"],
                    *uncertain_resolution["rejected"],
                ]
            )
        )
        proposal = build_profile_proposal(
            recognized,
            uncertain_characters=uncertain,
            import_mode=import_mode,
            source=source,
            confidence=confidence,
            note=note,
            rejected_characters=rejected,
            registry_revision=str(self._characters.meta.get("upstream_revision") or ""),
        )
        proposal.update(
            {
                "proposal_id": uuid4().hex,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "session_id": str(session.get("session_id") or ""),
                "visual_fingerprint": visual_id,
                "visual_verification": visual_verification,
                "applied_corrections": [
                    *recognized_resolution["applied_corrections"],
                    *uncertain_resolution["applied_corrections"],
                ],
                "ambiguous_candidates": ambiguous_candidates,
            }
        )
        stored = await self.store.set("pending_player_context", proposal)
        if isinstance(stored, Err):
            raise RuntimeError("PROFILE_PROPOSAL_STORE_FAILED")
        await self._push_companion_context("profile_proposal_staged")
        return proposal

    async def _confirm_profile_proposal(self, proposal_id: str = "") -> dict[str, Any]:
        proposal = await self._read_pending_profile()
        if not proposal:
            raise ValueError("没有等待确认的识别结果")
        requested_id = str(proposal_id or "").strip()
        if requested_id and requested_id != str(proposal.get("proposal_id") or ""):
            raise ValueError("识别结果已经更新，请刷新后重新确认")
        profile = apply_profile_proposal(await self._read_profile(), proposal)
        stored = await self.store.set("player_context", profile)
        if isinstance(stored, Err):
            raise RuntimeError("PLAYER_CONTEXT_STORE_FAILED")
        await self.store.delete("pending_player_context")
        await self._push_companion_context("profile_confirmed")
        return {
            "profile": profile,
            "applied_characters": list(proposal.get("recognized_characters") or []),
            "import_mode": proposal.get("import_mode") or "merge",
            "proposal_id": proposal.get("proposal_id") or "",
            "memory_boundary": "识别结果经玩家确认后写入插件档案；不等同于 N.E.K.O 长期记忆。",
        }

    async def _remember_correction(
        self,
        *,
        observed_name: str,
        canonical_name: str,
        modality: str,
        note: str = "",
    ) -> dict[str, Any]:
        session, started = await self._touch_session(f"correction:{modality}")
        if started:
            await self._push_companion_context(f"auto_start:correction:{modality}")
            session = await self._read_session()
        pending = await self._read_pending_profile()
        visual_id = ""
        if str(modality or "").strip().lower() == "vision":
            visual_id = str(pending.get("visual_fingerprint") or "")
            if not visual_id:
                request = await self._read_game_visual_request()
                observed_key = normalize_observed_name(observed_name)
                matching_region = next(
                    (
                        item
                        for item in list(request.get("character_regions") or [])
                        if isinstance(item, dict)
                        and normalize_observed_name(item.get("name")) == observed_key
                        and item.get("fingerprint")
                    ),
                    None,
                )
                visual_id = str((matching_region or {}).get("fingerprint") or "")
            if not visual_id:
                raise ValueError(
                    "这次画面没有可绑定的角色名字区域，不能把整张相似界面记成角色纠错；"
                    "请在角色详情或队伍界面重新识别后再纠正。"
                )
        corrections, record = remember_character_correction(
            await self._read_corrections(),
            observed_name=observed_name,
            canonical_name=canonical_name,
            modality=modality,
            registry=self._characters,
            visual_id=visual_id,
            session_id=str(session.get("session_id") or ""),
            note=note,
        )
        await self._store_value("character_corrections", corrections)

        pending_updated = False
        if pending:
            observed_key = normalize_observed_name(observed_name)
            candidate_fields = [
                "recognized_characters",
                "uncertain_characters",
                "rejected_characters",
            ]
            matched = any(
                normalize_observed_name(item) == observed_key
                for field in candidate_fields
                for item in list(pending.get(field) or [])
            )
            if matched:
                pending_modality = (
                    "vision"
                    if str(pending.get("source") or "") == "screenshot"
                    else "voice"
                    if str(pending.get("source") or "") == "voice"
                    else "chat"
                )
                recognized_resolution = resolve_character_candidates(
                    list(pending.get("recognized_characters") or []),
                    registry=self._characters,
                    corrections=corrections,
                    modality=pending_modality,
                    visual_id=str(pending.get("visual_fingerprint") or ""),
                    session_id=str(session.get("session_id") or ""),
                )
                uncertain_resolution = resolve_character_candidates(
                    list(pending.get("uncertain_characters") or []),
                    registry=self._characters,
                    corrections=corrections,
                    modality=pending_modality,
                    visual_id=str(pending.get("visual_fingerprint") or ""),
                    session_id=str(session.get("session_id") or ""),
                )
                canonical = str(record.get("canonical_name") or "")
                recognized = list(recognized_resolution["accepted"])
                is_confirmation_only = record.get("scope") == "confirmation_only"
                if canonical and canonical not in recognized and not is_confirmation_only:
                    recognized.append(canonical)
                recognized_keys = {name.casefold() for name in recognized}
                uncertain = [
                    name for name in uncertain_resolution["accepted"] if name.casefold() not in recognized_keys
                ]
                ambiguous_candidates = [
                    *recognized_resolution["ambiguous_candidates"],
                    *uncertain_resolution["ambiguous_candidates"],
                ]
                for ambiguity in ambiguous_candidates:
                    for alternative in list(ambiguity.get("alternatives") or []):
                        name = str(alternative or "").strip()
                        if name and name.casefold() not in recognized_keys and name not in uncertain:
                            uncertain.append(name)
                rejected = [
                    item
                    for item in list(pending.get("rejected_characters") or [])
                    if normalize_observed_name(item) != observed_key
                ]
                pending.update(
                    {
                        "recognized_characters": recognized,
                        "uncertain_characters": uncertain,
                        "rejected_characters": rejected,
                        "can_confirm": bool(recognized),
                        "confidence": "mixed" if is_confirmation_only else "high",
                        "ambiguous_candidates": ambiguous_candidates,
                        "player_correction_applied": {
                            "observed_name": observed_name,
                            "canonical_name": canonical,
                            "correction_id": record.get("correction_id"),
                        },
                    }
                )
                await self._store_value("pending_player_context", pending)
                pending_updated = True

        context_result = await self._push_companion_context("player_correction_saved")
        return {
            "correction": record,
            "pending_profile_proposal": pending if pending_updated else {},
            "pending_updated": pending_updated,
            "session": context_result["session"],
            "context_delivery": context_result["delivery"],
            "instruction": (
                "这两个名字都是真实角色，已记为本次会话的歧义提醒；"
                "不得自动把其中一个替换成另一个，后续遇到时必须依据新证据或询问玩家。"
                if record.get("scope") == "confirmation_only"
                else "已记住玩家纠正。只有本次输入明确命中该别名或同一角色名区域时才能应用；"
                "不得把历史纠错主动复述为当前画面事实。"
            ),
        }

    @ui.action(
        id="start_companion_session",
        label="开启星铁陪玩",
        tone="success",
        refresh_context=True,
    )
    @plugin_entry(
        id="start_companion_session",
        name="开启星铁陪玩会话",
        description="开启会话并把玩家档案、纠错和严格事实边界主动送入 N.E.K.O 上下文。",
        input_schema={"type": "object", "properties": {}},
        llm_result_fields=["session", "delivery"],
    )
    async def start_companion_session(self, **_):
        try:
            return Ok(await self._start_companion_session("user_start"))
        except RuntimeError as exc:
            return Err(SdkError(str(exc)))

    @ui.action(
        id="stop_companion_session",
        label="结束本次陪玩",
        tone="default",
        refresh_context=True,
    )
    @plugin_entry(
        id="stop_companion_session",
        name="结束星铁陪玩会话",
        description="结束当前星铁场景并让 N.E.K.O 恢复普通聊天，插件档案不会删除。",
        input_schema={"type": "object", "properties": {}},
        llm_result_fields=["session", "delivery"],
    )
    async def stop_companion_session(self, **_):
        clear_discussion(self)
        try:
            return Ok(await self._stop_companion_session("user_stop"))
        except RuntimeError as exc:
            return Err(SdkError(str(exc)))

    @ui.action(
        id="refresh_companion_context",
        label="重新同步给猫娘",
        tone="primary",
        refresh_context=True,
    )
    @plugin_entry(
        id="refresh_companion_context",
        name="刷新星铁陪玩上下文",
        description="重新把当前玩家档案、纠错和事实规则注入 N.E.K.O，不触发立即回复。",
        input_schema={"type": "object", "properties": {}},
        llm_result_fields=["session", "delivery"],
    )
    async def refresh_companion_context(self, **_):
        try:
            return Ok(await self._push_companion_context("manual_refresh"))
        except RuntimeError as exc:
            return Err(SdkError(str(exc)))

    @ui.action(id="refresh_current_observation", label=tr("manualRefresh.label"), tone="default", refresh_context=True)
    @plugin_entry(
        id="refresh_current_observation", name=tr("manualRefresh.label"),
        description=tr("manualRefresh.description"),
        input_schema={"type": "object", "properties": {}},
        llm_result_fields=["manual_refresh", "last_operation"],
    )
    async def refresh_current_observation(self, **_):
        status = request_manual_refresh(self)
        key = "manualRefresh.requested" if status.get("state") == "running" else "manualRefresh.unavailable"
        operation = await self._set_user_operation("manual_refresh", "info" if status.get("state") == "running" else "error", self.i18n.t(key))
        return Ok({"manual_refresh": status, "last_operation": operation})

    @ui.action(
        id="remember_character_correction",
        label="记住角色纠正",
        tone="success",
        refresh_context=True,
    )
    @plugin_entry(
        id="remember_character_correction",
        name="记住玩家的星铁角色纠正",
        description=(
            "保存玩家明确给出的角色名纠错。语音或聊天别名可长期使用；"
            "视觉纠错只绑定当前角色名区域，避免误伤真实角色。"
            "方向必须是‘错误名字 → 玩家给出的正确名字’。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "observed_name": {
                    "type": "string",
                    "description": "纠正前的错误名字。例如‘不是花火，是火花’应填写花火",
                },
                "canonical_name": {
                    "type": "string",
                    "description": "玩家明确给出的正确名字。例如‘不是花火，是火花’应填写火花",
                },
                "modality": {
                    "type": "string",
                    "enum": ["chat", "voice", "vision"],
                    "default": "chat",
                },
                "note": {"type": "string", "default": ""},
            },
            "required": ["observed_name", "canonical_name"],
        },
        llm_result_fields=["correction", "pending_updated", "instruction"],
    )
    async def remember_character_correction_entry(
        self,
        observed_name: str,
        canonical_name: str,
        modality: str = "chat",
        note: str = "",
        **_,
    ):
        try:
            return Ok(
                await self._remember_correction(
                    observed_name=observed_name,
                    canonical_name=canonical_name,
                    modality=modality,
                    note=note,
                )
            )
        except (ValueError, RuntimeError) as exc:
            return Err(SdkError(str(exc)))

    @llm_tool(
        name="hsr_remember_character_correction",
        description=(
            "当玩家明确说星铁角色识别错了、名字听错了、或给出正确角色名时必须调用。"
            "它会把纠错保存到插件并立即刷新猫娘可见的星铁上下文。"
            "纠错方向必须是错误值到正确值：玩家说‘不是花火，是火花’时，"
            "observed_name 必须是‘花火’，canonical_name 必须是‘火花’，绝不能反过来。"
            "如果两个名字都是数据库中的真实角色，工具只会记录歧义，不会自动互换。"
            "不要只在自然语言里道歉而不调用本工具。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "observed_name": {
                    "type": "string",
                    "description": "纠正前的错误名字；如‘不是花火，是火花’，这里必须填花火",
                },
                "canonical_name": {
                    "type": "string",
                    "description": "玩家明确给出的正确角色名；如‘不是花火，是火花’，这里必须填火花",
                },
                "modality": {
                    "type": "string",
                    "enum": ["chat", "voice", "vision"],
                    "default": "chat",
                },
                "note": {"type": "string", "default": ""},
            },
            "required": ["observed_name", "canonical_name"],
        },
    )
    async def hsr_remember_character_correction(
        self,
        *,
        observed_name: str,
        canonical_name: str,
        modality: str = "chat",
        note: str = "",
        **_,
    ):
        try:
            await self._record_tool_use("hsr_remember_character_correction", modality=modality)
            return await self._remember_correction(
                observed_name=observed_name,
                canonical_name=canonical_name,
                modality=modality,
                note=note,
            )
        except (ValueError, RuntimeError) as exc:
            return {
                "output": {"reason": str(exc)},
                "is_error": True,
                "error": "CHARACTER_CORRECTION_FAILED",
            }

    @llm_tool(
        name="hsr_report_game_state",
        description=(
            "记录模型对玩家星铁截图的未验证视觉线索。这个工具不会把模型判断升级为插件事实，"
            "也不能确认角色身份；只有插件本地页面识别或玩家明确确认才是可信状态。"
            "无法确认时报告 unknown，不得猜测。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "primary_state": {
                    "type": "string",
                    "enum": list(GAME_STATE_LABELS),
                    "description": "当前画面的主要游戏状态",
                },
                "substate": {
                    "type": "string",
                    "description": "更具体的页面或玩法，例如角色详情、模拟宇宙、剧情过场",
                    "default": "",
                },
                "confidence": {
                    "type": "string",
                    "enum": ["high", "mixed", "low"],
                    "default": "mixed",
                },
                "evidence": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "支持判断的可见界面证据，不超过六条",
                    "default": [],
                },
                "summary": {
                    "type": "string",
                    "description": "对玩家当前画面的简短事实描述，不要编造画面外内容",
                    "default": "",
                },
            },
            "required": ["primary_state"],
        },
    )
    async def hsr_report_game_state(
        self,
        *,
        primary_state: str,
        substate: str = "",
        confidence: str = "mixed",
        evidence: list[str] | None = None,
        summary: str = "",
        **_,
    ):
        try:
            session, started = await self._touch_session("game_state_observed")
            if started:
                await self._push_companion_context("auto_start:game_state")
                session = await self._read_session()
            visual_request = await self._read_game_visual_request()
            previous_raw = await self._read_game_state()
            previous = public_game_state(previous_raw)
            observation = build_game_state_observation(
                primary_state=primary_state,
                substate=substate,
                confidence=confidence,
                evidence=evidence or [],
                summary=summary,
                source="model_visual_hint",
                verification="unverified",
                visual_fingerprint=str(visual_request.get("fingerprint") or ""),
            )
            accepted = False
            transition_reason = "unverified_model_hint_not_committed"
            await self._store_value("last_unverified_game_state_hint", observation)
            history = await self._read_game_state_history()
            history.append(
                {
                    **observation,
                    "accepted": accepted,
                    "transition_reason": transition_reason,
                }
            )
            await self._store_value("game_state_history", history[-50:])
            await self._record_tool_use(
                "hsr_report_game_state",
                primary_state=observation["primary_state"],
                confidence=observation["confidence"],
                accepted=accepted,
            )
            current = public_game_state(previous_raw)
            return {
                "game_state": current,
                "unverified_hint": public_game_state(observation),
                "transition": {
                    "from": previous.get("primary_state") or "unknown",
                    "to": current["primary_state"],
                    "changed": False,
                    "accepted": accepted,
                    "reason": transition_reason,
                },
                "session": session,
                "instruction": ("这只是模型视觉线索，插件不会把它当作已验证状态；不得据此断言角色身份或当前场景。"),
            }
        except (ValueError, RuntimeError) as exc:
            return {
                "output": {"reason": str(exc)},
                "is_error": True,
                "error": "GAME_STATE_REPORT_FAILED",
            }

    @llm_tool(
        name="hsr_companion_prepare",
        description=PREPARE_TOOL_DESCRIPTION,
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "玩家当前的星铁问题或意图"},
                "intent": {
                    "type": "string",
                    "enum": [
                        "conversation",
                        "character_identity",
                        "knowledge",
                        "team",
                        "profile",
                        "correction",
                        "game_state",
                    ] + sorted(SEMANTIC_INTENTS),
                    "description": "优先传已理解的精确目的：read_screen读取所需字段；explain_page解释页面用途；"
                                   "assess_relic/assess_cone/assess_panel评价眼前遗器/光锥/面板；"
                                   "equipment_guide/build_guide给资料建议；team_guide给角色配队，challenge_guide给本期/眼前关卡配队，不能沿用上一位角色；"
                                   "build_priority/build_explain/build_howto为养成重点/原因/怎么养，stat_threshold/stat_value为属性目标/玩家补数；"
                                   "continue_consultation沿用有效咨询目的但不沿用旧装备事实。其余值仅兼容旧调用。",
                    "default": "conversation",
                },
                "observed_character_names": {
                    "type": "array",
                    "items": {"type": "string"},
                    "default": [],
                },
                "modality": {
                    "type": "string",
                    "enum": ["chat", "voice", "vision"],
                    "default": "chat",
                },
                "category": {"type": "string", "default": ""},
                "discussion_character": {"type": "string", "default": "",
                    "description": "仅携带先前对话已明确的讨论角色，须为精确角色名；不是画面识别或账号拥有证明。当前问题明确新对象时以新对象为准。"},
                "consultation": CONSULTATION_SCHEMA,
                "player_reply": {"type": "string", "maxLength": 80,
                    "description": "仅玩家本条明确补充遗器缺项时填写其原话，如‘76’或‘防御力76’；按player_reply_contract绑定原装备。不从历史、画面、推荐或猜测填值，不把玩家回答改成看屏幕核验。"},
                "pending_reply": {"type": "object", "additionalProperties": False,
                    "description": "仅接续已登记followup：由你理解本条消息与原待办的关系。不是关键词判断，也不开始新的读屏。新问题通常不填本项，按正常intent发起以替换旧待办。",
                    "properties": {"request_id": {"type": "string"},
                        "relation": {"type": "string", "enum": ["confirm", "cancel", "new_request", "uncertain"]},
                        "player_message": {"type": "string", "maxLength": 4096, "description": "玩家本条原话，不自行改写"}},
                    "required": ["request_id", "relation", "player_message"]},
                "screen_fields": {"type": "array", "items": {"type": "string", "enum": list(SCREEN_FIELDS)},
                                  "description": "read_screen必填，仅列玩家需要的当前字段。精确评价意图可省略而使用该任务必需字段；"
                                                 "复合读屏保留必需字段后可增加实际需求。equipment_guide/build_guide通常不填；"
                                                 "只有尚不知讨论角色时可传characters确认对象，不读装备。"
                                                 "只读页面是page；解释用途或读文字是page+visible_text，不因‘干嘛’增加quest。不填答案。", "default": []},
                "response_detail": {"type": "string", "enum": ["brief", "full"],
                                    "description": "默认brief：普通评价先给判断，不逐项报数。仅玩家要求完整/逐项读数时用full；"
                                                   "为评价读取全部词条不代表玩家要听全部数值。明确完整请求仍保留全部已确认项。"},
                "cultivation_plan": {
                    "type": "object", "properties": {
                        "kind": {"type": "string", "enum": ["ascension", "trace"]},
                        "entity_id": {"type": "string", "description": "先查资料，使用 character_form 或 character_trace 的精确 ID"},
                        "current": {"type": "integer", "minimum": 0},
                        "target": {"type": "integer", "minimum": 0},
                    }, "required": ["kind", "entity_id", "current", "target"],
                },
            },
            "required": ["query"],
        },
    )
    @trace_prepare
    async def hsr_companion_prepare(
        self,
        *,
        query: str,
        intent: str = "conversation",
        observed_character_names: list[str] | None = None,
        modality: str = "chat",
        category: str = "",
        cultivation_plan: dict | None = None,
        screen_fields: list[str] | None = None,
        response_detail: str | None = None,
        discussion_character: str = "",
        consultation: dict | None = None,
        player_reply: str | None = None,
        pending_reply: dict | None = None,
        **_,
    ):
        if pending_reply is not None:
            prepare_stage("pending_reply")
            from .runtime.pending_reply import handle_pending_reply
            handled = await handle_pending_reply(self, pending_reply)
            prepare_stage("pending_reply", completed=True)
            return {"pending_reply": handled}
        prepare_stage("initialization")
        self._note_speech_query()
        answer_generation = self._answer_query_generation
        discussion_generation = getattr(self, "_discussion_generation", 0)
        asked_at = time.time()
        mark_service_request(self, answer_generation, "started")
        prepare_stage("initialization", completed=True)
        try:
            prepare_stage("equipment_reply")
            from .runtime.equipment_reply import try_equipment_reply
            supplied = await try_equipment_reply(self, query, player_reply=player_reply, generation=answer_generation) if (
                intent not in SEMANTIC_INTENTS or player_reply is not None) else None
            prepare_stage("equipment_reply", completed=True)
            if supplied is not None:
                return supplied
            prepare_stage("query_plan")
            query_plan = build_query_plan(query, intent, screen_fields=screen_fields, response_detail=response_detail)
            prepare_stage("query_plan", completed=True)
            prepare_stage("guidance_catalog")
            book = getattr(self, "_guidance_book", None)
            if book is None or book.data.get("available") is False:
                self._guidance_book = book = load_guidance_catalog(Path(__file__).parent / "resources/guidance.cn-4.6.json")
            prepare_stage("guidance_catalog", completed=True)
            prepare_stage("guidance_plan")
            guidance_plan = plan_guidance(query, query_plan, catalog=self._catalog, book=book,
                                          previous=getattr(self, "_guidance_context", None),
                                          explicit_fields=bool(screen_fields), discussion_character=discussion_character,
                                          consultation=consultation)
            query_plan = guidance_plan.observation_plan(query_plan)
            knowledge_guidance = guidance_plan.requested and not query_plan.screen
            if not guidance_plan.requested and not query_plan.screen:
                clear_discussion(self)
            mark_service_request(self, answer_generation, "planned")
            prepare_stage("guidance_plan", completed=True)
            prepare_stage("data_pack")
            if not knowledge_guidance and not self._catalog.status().get("installed"):
                await self._wait_for_data_pack()
            prepare_stage("data_pack", completed=True)
            prepare_stage("session")
            session, started = await self._touch_session(f"prepare:{intent}")
            if started and not knowledge_guidance:
                await self._push_companion_context(f"auto_start:prepare:{intent}")
                session = await self._read_session()
            prepare_stage("session", completed=True)
            prepare_stage("player_context")
            profile = await self._read_profile()
            if guidance_plan.requested:
                # Account conditions only; never insert imported builds into
                # ObservationStore/current_visual or re-run OCR for UID data.
                profile = profile_with_roster(profile, await self._imported_roster())
            pending = await self._read_pending_profile()
            corrections = await self._read_corrections()
            screen_identity_request = query_plan.identity
            screen_content_request = query_plan.content
            screen_request = query_plan.screen
            scene_only_request = query_plan.scene_only
            navigation_request = query_plan.navigation
            prepare_stage("player_context", completed=True)
            prepare_stage("observation")
            query_observation = await resolve_query(self, query_plan, refresh=refresh_for_query,
                                                    timeout=DIRECT_WAIT_SECONDS, generation=answer_generation,
                                                    read_unrequested=not knowledge_guidance)
            prepare_stage("observation", completed=True)
            prepare_stage("fact_projection")
            game_state = public_game_state({} if knowledge_guidance else await self._read_game_state())
            current_visual = query_observation.snapshot.evidence()
            prepare_stage("fact_projection", snapshot=query_observation.snapshot, visual=current_visual)
            visual_epoch = getattr(self, "_observation_epoch", 0)
            if navigation_request or scene_only_request:
                # Keep raw OCR for panel diagnostics, not for the language
                # model to promote an unclassified heading into a location.
                current_visual = {**current_visual, "screen_facts": {
                    **current_visual["screen_facts"], "unclassified_text": [],
                    **({"visible_text": []} if navigation_request else {}),
                }}
            if not current_visual.get("fresh"):
                game_state = public_game_state({})
            controlled_request = query_plan.controlled
            answer_characters = (
                [current_visual["controlled_character"]] if current_visual.get("controlled_character") else []
            ) if controlled_request else current_visual["characters"]
            visual_id = str(pending.get("visual_fingerprint") or "") if modality == "vision" else ""
            candidates = list(observed_character_names or [])
            if screen_request:
                # Model-provided guesses cannot manufacture current identities.
                candidates = [str(item["name"]) for item in answer_characters]
            prepare_stage("fact_projection", completed=True)
            prepare_stage("grounding")
            normalized_query = normalize_observed_name(query)
            if modality in {"voice", "chat"} and not screen_request:
                for correction in corrections:
                    if not isinstance(correction, dict):
                        continue
                    if correction.get("active") is False:
                        continue
                    observed = str(correction.get("observed_name") or "").strip()
                    observed_key = str(correction.get("observed_key") or "")
                    if (
                        observed
                        and observed_key
                        and observed_key in normalized_query
                        and observed not in candidates
                        and correction.get("scope") == "global_alias"
                        and correction.get("modality") in {modality, "any"}
                    ):
                        candidates.append(observed)
            resolution = resolve_character_candidates(
                candidates,
                registry=self._characters,
                corrections=[] if screen_request else corrections,
                modality="vision" if screen_request else modality,
                visual_id=visual_id,
                session_id=str(session.get("session_id") or ""),
            )
            type_map = {
                "character": ["character", "character_form"],
                "light_cone": ["light_cone"],
                "relic": ["relic_set"],
            }
            results = [] if screen_identity_request else self._catalog.search(
                query,
                entity_types=type_map.get(category, []),
                limit=5,
            )
            analysis_subject = {"analysis_subject_ids": guidance_plan.character_ids,
                                "analysis_subject_origin": guidance_plan.target_origin} if guidance_plan.request_focus else {}
            grounded = build_grounding(query, current_visual, self._catalog, category=category, plan=query_plan,
                                       **analysis_subject) if screen_content_request else {
                "requested": False, "facts": [], "knowledge_results": [],
            }
            if screen_content_request:
                results = grounded["knowledge_results"]
            authoritative_context = build_companion_context(
                session=session,
                profile={} if screen_request else profile,
                pending={} if screen_request else pending,
                corrections=corrections,
                registry_meta=self._characters.meta,
                registry_count=len(self._characters.records),
                catalog_meta=self._catalog.meta,
                game_state=game_state,
                current_visual=current_visual,
            )
            prepare_stage("grounding", completed=True)
            prepare_stage("tool_audit")
            await self._record_tool_use(
                "hsr_companion_prepare",
                mark_query=False,
                intent=intent,
                modality=modality,
                query=str(query or "")[:160],
                knowledge_hits=len(results),
                observed_count=len(candidates),
                refresh_diagnostics=query_observation.refresh,
                query_plan=query_plan.public(),
                guidance_plan=guidance_plan.public(),
                answer_status_at_audit=answer_status(query_plan, current_visual, self._observer_status),
            )
            prepare_stage("tool_audit", completed=True)
            prepare_stage("delivery_boundary")
            # The final boundary returns this packet's safe SUBSET. A changed
            # task/row must not erase an independently admitted page. No OCR,
            # borrowing a newer value, or assembling a cross-item card here.
            final_visual = query_observation.snapshot.delivery(self, generation=answer_generation) if screen_request else current_visual
            if navigation_request or scene_only_request:
                final_visual["screen_facts"]["unclassified_text"] = []
                if navigation_request:
                    final_visual["screen_facts"]["visible_text"] = []
            changed = {k: v for k, v in current_visual.items() if k != "delivery_boundary"} != {
                k: v for k, v in final_visual.items() if k != "delivery_boundary"}
            current_visual = final_visual
            if screen_request and changed:
                if not current_visual.get("fresh"):
                    game_state = public_game_state({})
                answer_characters = ([current_visual["controlled_character"]] if current_visual.get("controlled_character") else []
                                     ) if controlled_request else current_visual["characters"]
                resolution = resolve_character_candidates(
                    [str(item["name"]) for item in answer_characters], registry=self._characters,
                    corrections=[], modality="vision", visual_id="", session_id="")
                if screen_content_request:
                    grounded = build_grounding(query, current_visual, self._catalog, category=category, plan=query_plan,
                                               **analysis_subject)
                    results = grounded["knowledge_results"]
                authoritative_context = build_companion_context(
                    session=session, profile={} if screen_request else profile,
                    pending={} if screen_request else pending, corrections=corrections,
                    registry_meta=self._characters.meta, registry_count=len(self._characters.records),
                    catalog_meta=self._catalog.meta, game_state=game_state, current_visual=current_visual)
            if screen_request and visual_epoch == getattr(self, "_observation_epoch", 0):
                self._get_speech_arbiter().covered_by_tool(
                    epoch=visual_epoch,
                    scene=str((current_visual.get("scene") or {}).get("primary_state") or "unknown"),
                    page_type=str(current_visual.get("page_type") or "unknown"),
                    fresh=bool(current_visual.get("fresh")),
                    verified=bool((current_visual.get("scene") or {}).get("verified")), now=time.monotonic(),
                )
            prepare_stage("delivery_boundary", completed=True, snapshot=query_observation.snapshot, visual=current_visual)
            prepare_stage("evaluation")
            guidance = build_guidance(guidance_plan, query, book=book, catalog=self._catalog,
                                      profile=profile, visual=current_visual)
            resolved_guidance_plan = bound_followup_plan(guidance_plan, guidance)
            guidance_context = guidance.pop("context", None)
            if (answer_generation == self._answer_query_generation and guidance_context is not None
                    and discussion_generation == getattr(self, "_discussion_generation", 0)):
                self._guidance_context = guidance_context
            elif guidance_plan.requested:
                guidance = {"requested": True, "state": "superseded", "guides": [], "team_candidates": [],
                            "instruction": "本次问题或讨论会话已被更新/停止，不发送旧攻略或补答。"}
            if guidance_plan.requested:
                authoritative_context += "\n[本次攻略服务]\n" + (
                    "本题按guidance_answer.reply_plan回答；完整事实卡只供核对，资料候选不是当前穿戴。"
                    if (guidance.get("reply_plan") or {}).get("enabled") else guidance["instruction"])
                if knowledge_guidance:
                    authoritative_context += "\n本次没有请求观察画面；画面未知不影响带来源攻略，勿回答成识别失败。"
            prepare_stage("evaluation", completed=True)
            prepare_stage("request_completion")
            followup = {"pending": False, "reason": "not_needed",
                        "instruction": "本次没有登记后台补答；只说明实际未知部分，不得承诺稍后回复或谎称游戏加载。"}
            completion = guidance.get("completion") or {}
            from .runtime.request_completion import evaluate_request
            request_progress = evaluate_request(query_plan, current_visual, completion=completion)
            waiting_can_help = not completion.get("requested") or completion.get("can_wait_for_evidence")
            if not waiting_can_help:
                followup.update(reason=completion["state"], instruction=completion["instruction"])
            prepare_stage("request_completion", completed=True)
            prepare_stage("followup_registration")
            if (answer_generation == self._answer_query_generation
                    and waiting_can_help
                    and query_observation.refresh.get("outcome") != "cancelled"
                    and needs_followup(query_plan, current_visual, self._observer_status, completion=completion)):
                followup = arm_followup(self, query=query, visual=current_visual, asked_at=asked_at,
                                        scene_only=scene_only_request, navigation=navigation_request,
                                        content=screen_content_request, controlled=controlled_request, plan=query_plan,
                                        guidance_plan=resolved_guidance_plan if guidance_plan.requested else None,
                                        guidance_profile=profile if guidance_plan.requested else None,
                                        guidance_answer=guidance if guidance_plan.requested else None)
            prepare_stage("followup_registration", completed=True)
            mark_service_request(self, answer_generation, "completed")
            prepare_stage("payload")
            equipment_answer = equipment_card(current_visual) if "equipment" in query_plan.fields else {"state": "not_requested"}
            if "equipment" in query_plan.fields:
                record_handoff(self, equipment_answer, channel="direct")
            from .runtime.consultation import model_guidance
            payload = {
                "response_contract": response_contract(self, answer_generation, channel="direct"),
                "query_plan": query_plan.public(),
                "reply_demand": {"kind": "mixed" if guidance_plan.requested and query_plan.screen else
                                 "guidance" if guidance_plan.requested else "screen" if query_plan.screen else "knowledge",
                                 "guidance": guidance_plan.public(), "observation": query_plan.public()},
                "query_answer": answer_status(query_plan, current_visual, self._observer_status, followup),
                "request_completion": request_progress.public(body_complete=True),
                "followup": followup,
                "guidance_answer": model_guidance(guidance),
                "equipment_answer": equipment_answer,
                "session": session,
                "authoritative_context": authoritative_context,
                "player_context": {} if screen_request else profile,
                "pending_profile_proposal": {} if screen_request else pending,
                "character_resolution": resolution,
                "knowledge_results": results,
                "database": {
                    "revision": self._catalog.revision,
                    "coverage": self._catalog.coverage,
                    "strict_entity_ids": True,
                    "pack": self._public_data_pack_status(),
                },
                "correction_status": {
                    "active_count": sum(1 for item in corrections if item.get("active") is not False),
                    "disabled_count": sum(1 for item in corrections if item.get("active") is False),
                    "policy": "历史纠错不是当前事实；只使用 character_resolution.applied_corrections。",
                },
                "current_game_state": game_state,
                "current_visual_evidence": current_visual,
                "screen_context_answer": grounded,
                "cultivation_plan": plan_progression(cultivation_plan, catalog=self._catalog) if cultivation_plan is not None else {"requested": False},
                "screen_scene_answer": {
                    "requested": scene_only_request or "page" in query_plan.fields,
                    "allowed": bool((current_visual.get("scene") or {}).get("verified") or any(
                        f.get("kind") == "current_location" for f in current_visual["screen_facts"]["facts"])),
                    "page_type": current_visual["page_type"],
                    "label": current_visual["scene"]["label"],
                    "observed_at": current_visual["observed_at"],
                    "locations": [f for f in current_visual["screen_facts"]["facts"]
                                  if f.get("scope") == "location"],
                    "visible_text": current_visual["screen_facts"].get("visible_text", []) if not current_visual["scene"].get("verified") else [],
                    "instruction": "优先回答 locations 中明确的 current_location；任务目的地或地图选中地区不能当作所在地。"
                                   "其次回答已确认的页面，未知部分分开说明；无需等待角色名单。页面未知时可转述 visible_text，不能猜页面。"
                                   "是否补答只看 followup.pending；没有登记就不得承诺。",
                },
                "query_timing": {"refresh_wait_seconds": query_observation.wait_seconds,
                                 "refresh": query_observation.refresh},
                "screen_identity_answer": {
                    "requested": bool(query_plan.fields & {"characters", "controlled_character"}),
                    "allowed": bool(answer_characters),
                    "scope": "controlled_character" if controlled_request else "visible_identity",
                    "names": [item["name"] for item in answer_characters],
                    "instruction": current_visual["instruction"],
                    "unavailable_reply": ("目前尚未确认你正在操控哪一位角色。" if controlled_request else "目前无法确认画面中的角色。") if not answer_characters and not scene_only_request else "",
                },
                "answer_policy": {
                    "screen_information_is_not_web_search": (
                        "用户问当前页面或任务时，只用本次确认字段；网页不能判断玩家目前追踪哪个任务。"
                        "未确认不等于没有任务，不要泛搜页面类型来补答案。"
                        "只有任务身份已确认且用户另问攻略时，才考虑带任务名的资料查询。"
                    ) if screen_request else "",
                    "plugin_facts_first": True,
                    "unknown_means_uncertain": True,
                    "visual_results_require_confirmation": True,
                    "do_not_invent_characters": True,
                    "missing_pack_means_no_knowledge_answer": True,
                    "reviewed_guidance_scope": (
                        "guidance_answer 中有来源的策略候选可独立使用；外置实体不可用时不补写装备名称或效果。"
                        "是否能回答攻略看 guidance_answer，不以 query_answer 的画面状态代替。"
                    ) if guidance_plan.requested else "",
                    "consultation_reply": (
                        "本轮表达只按guidance_answer.reply_plan；完整事实卡、guides用于核对，不另起表达规则。"
                    ) if (guidance.get("reply_plan") or {}).get("enabled") else "",
                    "direct_scene_navigation_reply": (
                        "本次只用一到两句短句回答用户问到的当前地点、页面或任务目标；"
                        "原样保留已确认的地点和任务名称，未知字段简短说明。"
                        "不要追加未被问到的角色名单、阵容夸赞、重复总结、陪伴套话或出发追问；"
                        "不要播报插件确认过程；是否补答只看 followup.pending，没有登记就不得承诺。"
                    ) if scene_only_request or navigation_request else "",
                },
            }
            from .runtime.equipment_reply import offer_equipment_reply
            payload["player_reply_contract"] = offer_equipment_reply(
                self, current_visual, character_ids=resolved_guidance_plan.character_ids,
                assess=bool(guidance_plan.requested and guidance_plan.request_focus == "equipment"
                            and guidance_plan.consultation_action == "assess"),
            ) if ("sub_stat" in query_plan.required_equipment_kinds
                  and answer_generation == self._answer_query_generation
                  and query_observation.snapshot.matches(self)) else {"state": "not_needed"}
            prepare_stage("payload", completed=True)
            prepare_stage("answer_delivery")
            payload["delivery_receipt"] = record_answer_delivery(
                self, generation=answer_generation, channel="direct", payload=payload,
                visual=current_visual, answer=payload["query_answer"],
                selection_serial=query_observation.snapshot.serial)
            prepare_stage("answer_delivery", completed=True)
            return payload
        except asyncio.CancelledError as exc:
            prepare_failure(exc)
            mark_service_request(self, answer_generation, "cancelled")
            raise
        except (ValueError, RuntimeError) as exc:
            prepare_failure(exc)
            mark_service_request(self, answer_generation, "failed")
            return {
                "output": {"reason": str(exc)},
                "is_error": True,
                "error": "HSR_PREPARE_FAILED",
            }
        except Exception as exc:
            prepare_failure(exc)
            mark_service_request(self, answer_generation, "failed")
            raise

    @ui.action(
        id="start_auto_observation",
        label="开始自动观察",
        tone="success",
        refresh_context=True,
    )
    @plugin_entry(
        id="start_auto_observation",
        name="开始自动观察星铁窗口",
        description="经玩家明确开启后，只采集星铁窗口；切换聊天不影响观察，最小化或关闭游戏时暂停。画面仅在内存中处理，不操作游戏。",
        input_schema={"type": "object", "properties": {}},
        llm_result_fields=["observer", "session"],
    )
    async def start_auto_observation(self, **_):
        try:
            self._observer_stop_requested = False
            # First use must not block on a separate download. The bundled
            # strict character registry and local observer are already enough
            # to begin safely; richer knowledge can finish in the background.
            self._start_data_pack_task()
            settings = await self._read_observer_settings()
            settings["enabled"] = True
            await self._store_value("observer_settings", settings)
            self._observer_status["proactive_enabled"] = bool(settings.get("proactive_enabled", True))
            self._start_observer_task()
            context = await self._start_companion_session("auto_observation_started")
            await self._set_user_operation(
                "companion",
                "success",
                "陪玩已开启。打开或切回《崩坏：星穹铁道》后会自动识别。",
            )
            return Ok({"observer": dict(self._observer_status), **context})
        except RuntimeError as exc:
            await self._set_user_operation("companion", "error", "陪玩没有成功开启，请稍后重试。")
            return Err(SdkError(str(exc)))

    @ui.action(
        id="stop_auto_observation",
        label="停止自动观察",
        tone="default",
        refresh_context=True,
    )
    @plugin_entry(
        id="stop_auto_observation",
        name="停止自动观察星铁窗口",
        description="立即停止读取星铁窗口；玩家档案、纠错和当前陪玩会话仍保留。",
        input_schema={"type": "object", "properties": {}},
        llm_result_fields=["observer"],
    )
    async def stop_auto_observation(self, **_):
        self._stop_observation_now()
        try:
            settings = await self._read_observer_settings()
            settings["enabled"] = False
            await self._store_value("observer_settings", settings)
            await self._cancel_observer_task()
            await self._set_user_operation("companion", "success", "陪玩已暂停。")
            return Ok({"observer": dict(self._observer_status)})
        except RuntimeError as exc:
            await self._set_user_operation("companion", "error", "暂时无法暂停陪玩，请稍后重试。")
            return Err(SdkError(str(exc)))

    async def _assistant_status(self) -> dict[str, Any]:
        health = await asyncio.to_thread(self._assistant_client.health)
        process = self._assistant_process.snapshot()
        return {
            **process,
            **health,
            "running": bool(process.get("running") or health.get("connected")),
            "last_error": "" if health.get("connected") else process.get("last_error") or health.get("last_error", ""),
        }

    async def _wait_for_assistant(self) -> dict[str, Any]:
        # A frozen one-file helper may need several seconds to unpack on first launch.
        deadline = time.monotonic() + 15
        while True:
            status = await self._assistant_status()
            if status.get("connected"):
                return status
            if not status.get("running") or time.monotonic() >= deadline:
                raise RuntimeError(str(status.get("last_error") or "视觉助手启动后没有成功连接，请重试。"))
            await asyncio.sleep(0.3)

    @ui.action(
        id="install_background_perception",
        label="下载视觉助手",
        tone="primary",
        refresh_context=True,
    )
    @plugin_entry(
        id="install_background_perception",
        name="下载并安装星铁视觉助手",
        description="从插件自己的 GitHub Release 下载独立助手，校验后安装到插件外部；随后启动并检查连接。",
        input_schema={"type": "object", "properties": {}},
        llm_result_fields=["vision_assistant", "observer"],
    )
    async def install_background_perception(self, **_):
        settings = await self._read_observer_settings()
        was_enabled = bool(settings.get("enabled"))
        self._assistant_operation_in_progress = True
        try:
            existing_status = await self._assistant_status()
            if existing_status.get("installed"):
                operation = await self._set_user_operation(
                    "local_vision",
                    "success",
                    "视觉助手已经下载并安装，无需重复下载；运行和连接状态请查看面板。",
                )
                return Ok(
                    {
                        "vision_assistant": existing_status,
                        "observer": dict(self._observer_status),
                        "last_operation": operation,
                    }
                )
            await self._cancel_observer_task(update_status=False)
            await self._set_user_operation("local_vision", "working", "正在下载并校验视觉助手……")
            await asyncio.to_thread(self._assistant_process.install_component)
            await self._set_user_operation("local_vision", "working", "视觉助手已安装，正在启动并检查连接……")
            await asyncio.to_thread(self._assistant_process.start)
            status = await self._wait_for_assistant()
            operation = await self._set_user_operation(
                "local_vision",
                "success",
                "视觉助手已下载、校验并安装，正在运行且连接正常。",
            )
            return Ok(
                {
                    "vision_assistant": status,
                    "observer": dict(self._observer_status),
                    "last_operation": operation,
                }
            )
        except (OSError, ValueError, RuntimeError) as exc:
            await self._set_user_operation(
                "local_vision",
                "error",
                str(exc),
            )
            return Err(SdkError(str(exc)))
        finally:
            self._assistant_operation_in_progress = False
            if was_enabled:
                self._start_observer_task(restored=True)

    @ui.action(
        id="restart_background_perception",
        label="检查并修复后台感知",
        tone="default",
        refresh_context=True,
    )
    @plugin_entry(
        id="restart_background_perception",
        name="检查并修复星铁后台感知",
        description="重启插件自己的本地画面助手；不会操作游戏或修改 N.E.K.O 本体。",
        input_schema={"type": "object", "properties": {}},
        llm_result_fields=["vision_assistant", "observer"],
    )
    async def restart_background_perception(self, **_):
        settings = await self._read_observer_settings()
        was_enabled = bool(settings.get("enabled"))
        self._assistant_operation_in_progress = True
        try:
            await self._set_user_operation("local_vision", "working", "正在重新启动视觉助手并检查连接……")
            await self._cancel_observer_task(update_status=False)
            await asyncio.to_thread(self._assistant_process.start)
            status = await self._wait_for_assistant()
            operation = await self._set_user_operation(
                "local_vision",
                "success",
                "连接已经恢复，可以继续使用。",
            )
            return Ok(
                {
                    "vision_assistant": status,
                    "observer": dict(self._observer_status),
                    "last_operation": operation,
                }
            )
        except RuntimeError as exc:
            await self._set_user_operation(
                "local_vision",
                "error",
                str(exc),
            )
            return Err(SdkError(str(exc)))
        finally:
            self._assistant_operation_in_progress = False
            if was_enabled:
                self._start_observer_task(restored=True)

    @ui.action(
        id="set_proactive_companionship",
        label="切换主动陪伴",
        tone="default",
        refresh_context=True,
    )
    @plugin_entry(
        id="set_proactive_companionship",
        name="设置星铁主动陪伴",
        description="控制已验证游戏事件是否请求猫娘主动回应；关闭后仍会静默同步可靠状态。",
        input_schema={
            "type": "object",
            "properties": {"enabled": {"type": "boolean"}},
            "required": ["enabled"],
        },
        llm_result_fields=["proactive_companionship"],
    )
    async def set_proactive_companionship(self, enabled: bool, **_):
        if not enabled:
            self._observer_status["proactive_enabled"] = False
            self._cancel_pending_speech("proactive_disabled")
        try:
            settings = await self._read_observer_settings()
            settings["proactive_enabled"] = bool(enabled)
            await self._store_value("observer_settings", settings)
            self._observer_status["proactive_enabled"] = bool(enabled)
            return Ok(
                {
                    "proactive_companionship": {
                        "enabled": bool(enabled),
                        "message": (
                            "猫娘会在合适的星铁事件出现时主动回应"
                            if enabled
                            else "猫娘只接收静默状态，不会由插件主动发言"
                        ),
                    }
                }
            )
        except RuntimeError as exc:
            return Err(SdkError(str(exc)))

    @ui.action(
        id="prepare_data_pack",
        label="准备资料组件",
        tone="primary",
        refresh_context=True,
    )
    @plugin_entry(
        id="prepare_data_pack",
        name="准备星铁资料组件",
        description="在插件外部下载并校验固定版本的星铁结构化资料包。",
        input_schema={"type": "object", "properties": {}},
        llm_result_fields=["data_pack"],
    )
    async def prepare_data_pack(self, **_):
        await self._set_user_operation("data_pack", "working", "正在下载并校验游戏资料……")
        status = await self._wait_for_data_pack(force=True)
        if status.get("installed"):
            portraits_ready = self._avatars.status().get("installed")
            operation = await self._set_user_operation(
                "data_pack",
                "success" if portraits_ready else "info",
                "游戏资料和头像资料已经下载并校验完成。" if portraits_ready else "游戏资料已就绪，但头像资料没有准备完成。文字识别仍可使用，请在下方查看状态并重试。",
            )
            return Ok({"data_pack": status, "last_operation": operation})
        message = "游戏资料下载失败。基础角色名单仍可使用，可以稍后重试。"
        await self._set_user_operation("data_pack", "error", message)
        return Err(SdkError(message))

    @ui.action(id="diagnose_companion", label=tr("diagnostics.run"), refresh_context=False)
    @plugin_entry(
        id="diagnose_companion", name=tr("diagnostics.run"),
        description=tr("diagnostics.description"),
        input_schema={"type": "object", "properties": {
            "delivery_trace_seconds": {"type": "integer", "minimum": 0, "maximum": 120,
                "description": "仅明确排查转述错值时开启：短时内存记录必要装备字段，不保存截图；0关闭。默认不开启。"},
            "include_delivery_trace": {"type": "boolean", "default": False,
                "description": "仅明确请求时读取短时装备交付追踪；默认支持诊断/导出不包含游戏字段。"}},
            "additionalProperties": False},
        llm_result_fields=["diagnostic"],
    )
    async def diagnose_companion(self, delivery_trace_seconds=None, include_delivery_trace=False, **_):
        # Do not reuse demo_ui_context: it can restore the observer/downloads.
        # Health + existing state only. No OCR, restart, settings write, speech
        # or tool audit (which would itself change the speech quiet window).
        from .runtime.diagnostic_reader import diagnostic_inputs
        from .runtime.service_contract import configure_delivery_trace, delivery_value_trace

        if type(delivery_trace_seconds) is int and 0 <= delivery_trace_seconds <= 120:
            configure_delivery_trace(self, seconds=delivery_trace_seconds)

        assistant, audit, evidence, incomplete = await diagnostic_inputs(self)
        from .runtime.review_policy import review_status
        report = build_diagnostic_report(
            observer=dict(self._observer_status), assistant=assistant,
            evidence=evidence,
            data_pack=self._public_data_pack_status(), avatar_pack=self._avatars.status(),
            last_tool=audit[-1] if audit else {},
            speech=self._get_speech_arbiter().snapshot(), message_path=self._message_path_ready(),
            incomplete_sources=incomplete,
            service_request=getattr(self, "_service_request", None),
            equipment_handoffs=getattr(self, "_equipment_handoffs", []),
            followup_status=getattr(self, "_followup_status", {}),
            answer_deliveries=getattr(self, "_answer_deliveries", []),
            review_state=review_status(self),
            page_control_trace=getattr(self, "_page_control_trace", []),
            revocation_trace=getattr(self, "_revocation_trace", []),
            prepare_calls=await read_records(self),
        )
        trace = delivery_value_trace(self)
        if include_delivery_trace is True:
            report["delivery_value_trace"] = trace
        return Ok({"diagnostic": report})

    @ui.action(id="export_companion_diagnostic", label=tr("diagnostics.export"), refresh_context=False)
    @plugin_entry(
        id="export_companion_diagnostic", name=tr("diagnostics.export"),
        description=tr("diagnostics.exportDescription"),
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        llm_result_fields=["diagnostic", "export"],
    )
    async def export_companion_diagnostic(self, **_):
        result = await self.diagnose_companion()
        if isinstance(result, Err):
            return result
        report = unwrap_or(result, {})["diagnostic"]
        try:
            saved = await asyncio.to_thread(export_diagnostic, self.data_path("diagnostics"), report)
        except (OSError, ValueError):
            return Err(SdkError("诊断导出失败，请检查磁盘空间和插件数据目录的写入权限后重试；也可手动复制诊断文本。"))
        return Ok({"diagnostic": report, "export": saved})

    @ui.action(id="preview_uid_import", label=tr("uid.read", default="读取展示角色"), refresh_context=True)
    @plugin_entry(id="preview_uid_import", name=tr("uid.read", default="读取展示角色"),
                  description=tr("uid.readDescription", default="经玩家同意，查询国服公开展示；预览不自动写入角色库。"),
                  input_schema={"type": "object", "properties": {"uid": {"type": "string"}, "consent": {"type": "boolean"},
                                "use_system_proxy": {"type": "boolean", "default": False}},
                                "required": ["uid", "consent"], "additionalProperties": False},
                  llm_result_fields=["uid_import"], timeout=25)
    async def preview_uid_import(self, uid: str, consent: bool = False, use_system_proxy: bool = False, **_):
        try:
            return Ok({"uid_import": await uid_service(self).stage(uid, consent, use_system_proxy=use_system_proxy)})
        except RuntimeError:
            return Ok({"uid_import": {"status": "error", "error_code": "storage_failed"}})

    @ui.action(id="save_manual_build", label=tr("build.save", default="保存角色资料"), refresh_context=True)
    @plugin_entry(id="save_manual_build", name=tr("build.save", default="保存角色资料"),
                  description=tr("build.saveDescription", default="保存玩家在面板中明确录入的角色配装字段，不联网，不覆盖UID原始快照。"),
                  input_schema={"type": "object", "properties": {"form_id": {"type": "string"},
                                "changes": {"type": "object"}, "expected_revision": {"type": "string"}},
                                "required": ["form_id", "changes", "expected_revision"], "additionalProperties": False},
                  llm_result_fields=["player_build_save"])
    async def save_manual_build(self, form_id: str, changes: dict, expected_revision: str, **_):
        try:
            return Ok({"player_build_save": await player_build_service(self).save(form_id, changes, expected_revision)})
        except BuildError as exc:
            return Ok({"player_build_save": {"status": "error", "error_code": str(exc)}})
        except (RuntimeError, OSError):
            return Ok({"player_build_save": {"status": "error", "error_code": "storage_failed"}})

    @ui.action(id="confirm_uid_import", label=tr("uid.confirm", default="确认保存这批角色"), refresh_context=True)
    @plugin_entry(id="confirm_uid_import", name=tr("uid.confirm", default="确认保存这批角色"),
                  description=tr("uid.confirmDescription", default="合并玩家确认的展示配装；同角色整份更新，不删除未展示角色。"),
                  input_schema={"type": "object", "properties": {"preview_id": {"type": "string"}},
                                "required": ["preview_id"], "additionalProperties": False}, llm_result_fields=["uid_import"])
    async def confirm_uid_import(self, preview_id: str, **_):
        try:
            return Ok({"uid_import": await uid_service(self).confirm(preview_id)})
        except RuntimeError:
            return Ok({"uid_import": {"status": "error", "error_code": "storage_failed"}})

    @ui.action(id="discard_uid_import", label=tr("uid.discard", default="暂不保存"), refresh_context=True)
    @plugin_entry(id="discard_uid_import", name=tr("uid.discard", default="暂不保存"),
                  description=tr("uid.discardDescription", default="取消当前预览，不改变已导入角色和手动档案。"),
                  input_schema={"type": "object", "properties": {"preview_id": {"type": "string"}},
                                "required": ["preview_id"], "additionalProperties": False}, llm_result_fields=["uid_import"])
    async def discard_uid_import(self, preview_id: str, **_):
        try:
            return Ok({"uid_import": await uid_service(self).discard(preview_id)})
        except RuntimeError:
            return Ok({"uid_import": {"status": "error", "error_code": "storage_failed"}})

    @ui.action(id="clear_uid_import", label=tr("uid.clear", default="移除导入资料"), refresh_context=True)
    @plugin_entry(id="clear_uid_import", name=tr("uid.clear", default="移除导入资料"),
                  description=tr("uid.clearDescription", default="经确认仅移除UID及导入配装；不清空手动档案、记忆或当前画面。"),
                  input_schema={"type": "object", "properties": {"confirmed": {"type": "boolean"}},
                                "required": ["confirmed"], "additionalProperties": False}, llm_result_fields=["uid_import"])
    async def clear_uid_import(self, confirmed: bool = False, **_):
        try:
            return Ok({"uid_import": await uid_service(self).clear(confirmed)})
        except RuntimeError:
            return Ok({"uid_import": {"status": "error", "error_code": "storage_failed"}})

    @ui.context(id="demo", title="星铁游戏搭子")
    async def demo_ui_context(self, **_):
        # Upgraded users can already have knowledge but lack the new optional
        # portrait runtime. Prepare it on this long-lived loop, never in the
        # transient startup hook; failed downloads wait for an explicit retry.
        if self._avatars.status().get("state") == "missing":
            self._start_data_pack_task()
        observer_settings = await self._read_observer_settings()
        if (
            isinstance(observer_settings, dict)
            and observer_settings.get("enabled") is True
            and (self._observer_task is None or self._observer_task.done())
        ):
            # Hosted UI contexts run on the process command loop, which is
            # long-lived and can safely own the restored observer task.
            self._start_observer_task(restored=True)
        experiences = unwrap_or(await self._get_store("experiences"), [])
        if not isinstance(experiences, list):
            experiences = []
        profile = await self._read_profile()
        pending_profile = await self._read_pending_profile()
        session = await self._read_session()
        corrections = await self._read_corrections()
        game_state = public_game_state(await self._read_game_state())
        context_audit = await self._read_context_audit()
        tool_audit = await self._read_tool_audit()
        character_options = [
            {
                "name": str(record.get("name") or "").strip(),
                "entity_id": str(record.get("id") or "").strip(),
            }
            for record in self._catalog.records
            if record.get("entity_type") == "character" and record.get("name")
        ]
        local_vision = unwrap_or(await self._get_store("last_local_vision"), {})
        if not isinstance(local_vision, dict):
            local_vision = {}
        assistant_status = await self._assistant_status()
        observation_display = await ObservationStore(self).display()
        try:
            imported = uid_service(self).view(await uid_service(self).read())
        except (RuntimeError, ValueError, TypeError, KeyError):
            imported = {"status": "error", "error_code": "storage_failed", "roster": {"characters": []}}
        try:
            builds = await player_build_service(self).roster(editing=True)
            build_options = player_build_service(self).options()
        except (RuntimeError, ValueError, TypeError, KeyError, OSError):
            builds = {"state": "unavailable", "characters": []}
            build_options = {"characters": [], "light_cones": [], "outer_sets": [], "inner_sets": []}
        book = getattr(self, "_guidance_book", None)
        if book is None:
            self._guidance_book = book = load_guidance_catalog(Path(__file__).parent / "resources/guidance.cn-4.6.json")
        return {
            "status": "ready",
            "version": "0.9.6",
            "catalog": self._catalog.meta,
            "record_count": self._catalog.record_count,
            "database_coverage": self._catalog.coverage,
            "data_pack": self._public_data_pack_status(),
            "avatar_pack": self._avatars.status(),
            "last_operation": await self._read_user_operation(),
            "character_registry": {
                **self._characters.meta,
                "record_count": len(self._characters.records),
                "strict_validation": True,
            },
            "tool_count": 11,
            "session": {
                **session,
                "active": session_is_active(session, ttl_seconds=self._session_ttl_seconds),
                "ttl_seconds": self._session_ttl_seconds,
            },
            "context_delivery": context_audit[-1] if context_audit else {},
            "last_tool_use": tool_audit[-1] if tool_audit else {},
            "service_request": public_service_request(getattr(self, "_service_request", None)),
            "tool_use_count": len(tool_audit),
            "observer": {**self._observer_status, "assistant": assistant_status},
            "vision_assistant": assistant_status,
            "proactive_companionship": {
                "enabled": bool(observer_settings.get("proactive_enabled", True)),
                **self._proactive_status,
                "mode": "event_gated",
                "idle_scene": "exploration",
                "idle_interval_seconds": self._runtime.idle_chatter_seconds,
            },
            "local_vision": local_vision,
            "current_visual_evidence": observation_display["current"],
            "observation_display": observation_display,
            "manual_refresh": public_manual_refresh(self),
            "vision_backend": self._vision.status(),
            "runtime": self._runtime.snapshot(),
            "speech_arbitration": self._get_speech_arbiter().snapshot(),
            "query_followup": dict(getattr(self, "_followup_status", {"state": "idle"})),
            "correction_count": sum(1 for item in corrections if item.get("active") is not False),
            "disabled_correction_count": sum(1 for item in corrections if item.get("active") is False),
            "recent_corrections": list(
                reversed(
                    public_correction_summary([item for item in corrections if item.get("active") is not False])[-8:]
                )
            ),
            "current_game_state": game_state,
            "unverified_game_state_hint": public_game_state(
                unwrap_or(await self._get_store("last_unverified_game_state_hint"), {})
            ),
            "profile": profile,
            "uid_import": imported,
            "player_builds": builds,
            "build_options": build_options,
            "data_freshness": data_freshness(self._catalog, book),
            "pending_profile_proposal": pending_profile,
            "character_options": character_options,
            "experience_count": len(experiences),
            "recent_experiences": list(reversed(experiences[-5:])),
            "memory_boundary": (
                "玩家档案、会话和纠错保存在插件中，并会在陪玩开启时主动同步给 N.E.K.O；"
                "它们仍不等同于 N.E.K.O 本体的长期情感记忆。"
            ),
        }

    @ui.action(id="test_proactive_message", label="测试搭话", tone="default", refresh_context=False)
    @plugin_entry(
        id="test_proactive_message", name="测试星铁主动搭话",
        description="向当前猫娘请求一句连接测试回应，不依赖角色识别，不模拟游戏事件。",
        input_schema={"type": "object", "properties": {}},
        llm_result_fields=["proactive_companionship", "last_operation"],
    )
    async def test_proactive_message(self, **_):
        if self._observer_status.get("proactive_enabled") is False:
            operation = await self._set_user_operation("proactive_test", "info", "主动搭话已关闭，请先开启后再测试。")
            return Ok({"last_operation": operation, "proactive_companionship": self._proactive_status})
        try:
            result = self.push_message(
                visibility=[], ai_behavior="respond", source="hsr_companion", priority=5,
                parts=[{"type": "text", "text": "[星铁陪玩连接测试] 玩家点击了测试搭话。请以你的人格自然回复一句‘我在陪你’，不要说明自己识别到了任何游戏角色或画面。不需要开启语音或视频通话，至少应显示文字回复。"}],
                metadata={"kind": "connection_test", "proactive_requested": True},
            )
            success = bool(result.get("submitted"))
            operation = await self._set_user_operation(
                "proactive_test", "info" if success else "error",
                "搭话请求已提交。请看猫娘是否显示回复；如果没有，故障在消息接收或会话连接，不是角色识别。" if success else self._proactive_status["message"],
            )
            return Ok({"last_operation": operation, "proactive_companionship": self._proactive_status})
        except Exception as exc:
            operation = await self._set_user_operation("proactive_test", "error", f"搭话测试发送失败：{exc}")
            return Ok({"last_operation": operation, "proactive_companionship": self._proactive_status})

    @ui.action(
        id="submit_game_screenshot",
        label="识别当前游戏画面",
        tone="primary",
        refresh_context=False,
    )
    @plugin_entry(
        id="submit_game_screenshot",
        name="让 N.E.K.O 判断当前星铁画面",
        description=(
            "把用户主动选择的当前游戏截图交给 N.E.K.O，判断剧情、战斗、探索、菜单等状态；原图不写入插件存储。"
        ),
        input_schema={
            "type": "object",
            "properties": {"image_data_url": {"type": "string"}},
            "required": ["image_data_url"],
        },
        llm_result_fields=["submitted", "message", "image_stored"],
    )
    async def submit_game_screenshot(self, image_data_url: str, **_):
        try:
            image_bytes, mime = prepare_image_for_vision(image_data_url)
            image_fingerprint = await asyncio.to_thread(visual_fingerprint, image_bytes)
            await self._store_value(
                "active_game_visual_request",
                {
                    "fingerprint": image_fingerprint,
                    "created_at": utc_now_iso(),
                    "mime": mime,
                    "image_stored": False,
                    "source": "manual_screenshot_local_ocr",
                },
            )
            from .runtime.vision_scheduler import analyze_image
            analysis = await analyze_image(self, image_bytes)
            local_result = await self._apply_local_analysis(
                analysis,
                image_fingerprint=image_fingerprint,
                source="manual_screenshot_local_ocr",
                deliver_event=False,
            )
            scene = dict(local_result.get("scene") or {})
            characters = list(local_result.get("characters") or [])
            scene_key = str(scene.get("primary_state") or "unknown")
            names = [str(item.get("name") or "") for item in characters]
            prompt = (
                "[星铁插件本地截图识别结果]\n"
                f"场景：{scene_key}（{GAME_STATE_LABELS.get(scene_key, GAME_STATE_LABELS['unknown'])}）\n"
                f"置信度：{scene.get('confidence') or 'low'}\n"
                f"证据：{'；'.join(scene.get('evidence') or []) or '无足够证据'}\n"
                f"数据库确认角色：{'、'.join(names) if names else '无'}\n"
                f"数据库版本：{self._catalog.revision}\n"
                "请直接转述插件结果；unknown 就说明无法确认。禁止补猜未返回实体 ID 的角色。"
            )
            push_result = self.push_message(
                visibility=[],
                ai_behavior="respond",
                parts=[
                    {"type": "text", "text": prompt},
                    {"type": "image", "data": image_bytes, "mime": mime},
                ],
                source="hsr_companion",
                metadata={
                    "kind": "game_state_local_ocr_result",
                    "image_stored": False,
                    "catalog_revision": self._catalog.revision,
                },
                priority=5,
                coalesce_key="hsr_game_state_scan",
            )
            submitted = bool(push_result.get("submitted"))
            return Ok(
                {
                    "submitted": submitted,
                    "message": (
                        f"本地识别完成：{GAME_STATE_LABELS.get(scene_key, GAME_STATE_LABELS['unknown'])}。"
                        if submitted
                        else "本地识别已完成，但结果没有成功送达聊天。"
                    ),
                    "delivery_reason": push_result.get("reason"),
                    "image_stored": False,
                    "analysis": local_result,
                    "visual_fingerprint": image_fingerprint,
                }
            )
        except (ValueError, RuntimeError) as exc:
            return Err(SdkError(str(exc)))

    @ui.action(
        id="submit_roster_screenshot",
        label="识别角色截图",
        tone="primary",
        refresh_context=False,
    )
    @plugin_entry(
        id="submit_roster_screenshot",
        name="让 N.E.K.O 识别角色截图",
        description="把用户主动选择的角色列表截图交给 N.E.K.O 视觉能力；原图不写入插件存储，识别结果必须再次确认。",
        input_schema={
            "type": "object",
            "properties": {
                "image_data_url": {"type": "string"},
                "import_mode": {
                    "type": "string",
                    "enum": ["merge", "replace"],
                    "default": "merge",
                },
            },
            "required": ["image_data_url"],
        },
        llm_result_fields=["submitted", "message", "image_stored"],
    )
    async def submit_roster_screenshot(
        self,
        image_data_url: str,
        import_mode: str = "merge",
        **_,
    ):
        try:
            mode = str(import_mode or "merge").strip().lower()
            if mode not in {"merge", "replace"}:
                raise ValueError("请选择这是完整列表还是补充截图")
            image_bytes, mime = prepare_image_for_vision(image_data_url)
            image_fingerprint = await asyncio.to_thread(visual_fingerprint, image_bytes)
            await self._store_value(
                "active_visual_request",
                {
                    "fingerprint": image_fingerprint,
                    "created_at": utc_now_iso(),
                    "import_mode": mode,
                    "mime": mime,
                    "image_stored": False,
                },
            )
            await self._store_value(
                "active_game_visual_request",
                {
                    "fingerprint": image_fingerprint,
                    "created_at": utc_now_iso(),
                    "mime": mime,
                    "source": "roster_screenshot_local_ocr",
                    "image_stored": False,
                },
            )
            from .runtime.vision_scheduler import analyze_image
            analysis = await analyze_image(self, image_bytes)
            local_result = await self._apply_local_analysis(
                analysis,
                image_fingerprint=image_fingerprint,
                source="roster_screenshot_local_ocr",
                deliver_event=False,
            )
            names = [
                str(item.get("name") or "")
                for item in local_result.get("characters") or []
                if isinstance(item, dict) and item.get("entity_id")
            ]
            proposal: dict[str, Any] = {}
            if names:
                proposal = await self._stage_profile_proposal(
                    names,
                    import_mode=mode,
                    source="screenshot",
                    confidence="high",
                    note="由 0.9 后台感知页面 OCR 与版本化角色注册表严格匹配；等待玩家确认。",
                )
            prompt = (
                "[星铁插件本地角色识别结果]\n"
                f"数据库确认角色：{'、'.join(names) if names else '没有可靠匹配'}\n"
                f"数据库实体 ID：{'、'.join(str(item.get('entity_id')) for item in local_result.get('characters') or []) or '无'}\n"
                f"数据库版本：{self._catalog.revision}\n"
                + (
                    "识别结果已暂存，必须等待玩家确认后才能写入档案。"
                    if proposal
                    else "不要猜角色；请建议玩家截取包含清晰角色名字的列表区域。"
                )
            )
            push_result = self.push_message(
                visibility=[],
                ai_behavior="respond",
                parts=[
                    {"type": "text", "text": prompt},
                    {"type": "image", "data": image_bytes, "mime": mime},
                ],
                source="hsr_companion",
                metadata={
                    "kind": "roster_local_ocr_result",
                    "import_mode": mode,
                    "image_stored": False,
                    "catalog_revision": self._catalog.revision,
                },
                priority=5,
                coalesce_key="hsr_roster_scan",
            )
            submitted = bool(push_result.get("submitted"))
            return Ok(
                {
                    "submitted": submitted,
                    "message": (
                        "本地识别已完成，结果确认前不会写入档案。"
                        if names
                        else "没有识别到数据库中的角色，请截取包含清晰角色名字的区域。"
                    ),
                    "delivery_reason": push_result.get("reason"),
                    "image_stored": False,
                    "analysis": local_result,
                    "proposal": proposal,
                    "visual_fingerprint": image_fingerprint,
                }
            )
        except (ValueError, RuntimeError) as exc:
            return Err(SdkError(str(exc)))

    @plugin_entry(
        id="stage_player_context",
        name="暂存星铁玩家档案识别结果",
        description="暂存从截图或自然语言中整理出的角色列表，等待玩家确认后再写入正式档案。",
        input_schema={
            "type": "object",
            "properties": {
                "recognized_characters": {"type": "array", "items": {"type": "string"}},
                "uncertain_characters": {
                    "type": "array",
                    "items": {"type": "string"},
                    "default": [],
                },
                "import_mode": {
                    "type": "string",
                    "enum": ["merge", "replace"],
                    "default": "merge",
                },
                "source": {
                    "type": "string",
                    "enum": ["chat", "voice", "screenshot", "panel"],
                    "default": "chat",
                },
                "confidence": {
                    "type": "string",
                    "enum": ["high", "mixed", "low"],
                    "default": "mixed",
                },
                "note": {"type": "string", "default": ""},
            },
            "required": ["recognized_characters"],
        },
        llm_result_fields=["proposal", "confirmation_required", "rejected_characters"],
    )
    async def stage_player_context(
        self,
        recognized_characters: list[str],
        uncertain_characters: list[str] | None = None,
        import_mode: str = "merge",
        source: str = "chat",
        confidence: str = "mixed",
        note: str = "",
        **_,
    ):
        try:
            proposal = await self._stage_profile_proposal(
                recognized_characters,
                uncertain_characters=uncertain_characters,
                import_mode=import_mode,
                source=source,
                confidence=confidence,
                note=note,
            )
            return Ok(
                {
                    "proposal": proposal,
                    "confirmation_required": True,
                    "rejected_characters": proposal.get("rejected_characters", []),
                    "recognition_status": proposal.get("visual_verification", "not_visual"),
                }
            )
        except (ValueError, RuntimeError) as exc:
            return Err(SdkError(str(exc)))

    @llm_tool(
        name="hsr_stage_player_context",
        description=(
            "把玩家截图中识别到、或聊天中尚待确认的星铁角色列表暂存为候选档案。"
            "视觉识别必须使用本工具，绝不能直接调用 hsr_update_player_context 写正式档案。"
            "必须区分确定角色和不确定角色，且不得补猜未显示内容。"
            "插件会用固定版本角色数据库做严格校验，未收录名字会被拦截；"
            "但‘名字存在于数据库’不代表‘截图里就是这个角色’。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "recognized_characters": {"type": "array", "items": {"type": "string"}},
                "uncertain_characters": {
                    "type": "array",
                    "items": {"type": "string"},
                    "default": [],
                },
                "import_mode": {
                    "type": "string",
                    "enum": ["merge", "replace"],
                    "default": "merge",
                },
                "source": {
                    "type": "string",
                    "enum": ["chat", "voice", "screenshot", "panel"],
                    "default": "chat",
                },
                "confidence": {
                    "type": "string",
                    "enum": ["high", "mixed", "low"],
                    "default": "mixed",
                },
                "note": {"type": "string", "default": ""},
            },
            "required": ["recognized_characters"],
        },
    )
    async def hsr_stage_player_context(
        self,
        *,
        recognized_characters: list[str],
        uncertain_characters: list[str] | None = None,
        import_mode: str = "merge",
        source: str = "chat",
        confidence: str = "mixed",
        note: str = "",
        **_,
    ):
        try:
            proposal = await self._stage_profile_proposal(
                recognized_characters,
                uncertain_characters=uncertain_characters,
                import_mode=import_mode,
                source=source,
                confidence=confidence,
                note=note,
            )
            await self._record_tool_use(
                "hsr_stage_player_context",
                source=source,
                recognized_count=len(recognized_characters or []),
                uncertain_count=len(uncertain_characters or []),
            )
            return {
                "proposal": proposal,
                "confirmation_required": True,
                "rejected_characters": proposal.get("rejected_characters", []),
                "recognition_status": proposal.get("visual_verification", "not_visual"),
                "instruction": (
                    "这是插件刚处理的截图候选，可以请玩家核对；确认前不得写入档案。"
                    if proposal.get("visual_verification") == "plugin_bound"
                    else "这只是模型视觉猜测，数据库仅证明角色名存在，不能告诉玩家已经识别成功；"
                    "请明确说无法可靠确认，并建议使用插件面板识别包含清晰角色名的画面。"
                ),
            }
        except (ValueError, RuntimeError) as exc:
            return {
                "output": {"reason": str(exc)},
                "is_error": True,
                "error": "PROFILE_STAGE_FAILED",
            }

    @ui.action(
        id="confirm_profile_proposal",
        label="确认并保存",
        tone="success",
        refresh_context=True,
    )
    @plugin_entry(
        id="confirm_profile_proposal",
        name="确认星铁档案识别结果",
        description="在玩家明确确认后，把等待中的识别结果写入插件玩家档案。",
        input_schema={
            "type": "object",
            "properties": {"proposal_id": {"type": "string", "default": ""}},
        },
        llm_result_fields=["profile", "applied_characters", "memory_boundary"],
    )
    async def confirm_profile_proposal(self, proposal_id: str = "", **_):
        try:
            return Ok(await self._confirm_profile_proposal(proposal_id))
        except (ValueError, RuntimeError) as exc:
            return Err(SdkError(str(exc)))

    @llm_tool(
        name="hsr_confirm_player_context",
        description="仅当玩家明确说确认、保存或没问题时，确认最近一次待处理的星铁档案识别结果。",
        parameters={
            "type": "object",
            "properties": {"proposal_id": {"type": "string", "default": ""}},
        },
    )
    async def hsr_confirm_player_context(self, *, proposal_id: str = "", **_):
        try:
            await self._record_tool_use("hsr_confirm_player_context")
            return await self._confirm_profile_proposal(proposal_id)
        except (ValueError, RuntimeError) as exc:
            return {
                "output": {"reason": str(exc)},
                "is_error": True,
                "error": "PROFILE_CONFIRM_FAILED",
            }

    @ui.action(
        id="discard_profile_proposal",
        label="这次不保存",
        tone="default",
        refresh_context=True,
    )
    @plugin_entry(
        id="discard_profile_proposal",
        name="放弃星铁档案识别结果",
        description="丢弃当前等待确认的识别结果，不修改正式玩家档案。",
        input_schema={"type": "object", "properties": {}},
        llm_result_fields=["discarded"],
    )
    async def discard_profile_proposal(self, **_):
        pending = await self._read_pending_profile()
        await self.store.delete("pending_player_context")
        if session_is_active(await self._read_session(), ttl_seconds=self._session_ttl_seconds):
            await self._push_companion_context("profile_proposal_discarded")
        return Ok({"discarded": bool(pending), "profile_changed": False})

    @llm_tool(
        name="hsr_discard_player_context",
        description="当玩家明确否认、取消或要求不要保存最近的星铁档案识别结果时，丢弃待确认结果。",
        parameters={"type": "object", "properties": {}},
    )
    async def hsr_discard_player_context(self, **_):
        pending = await self._read_pending_profile()
        await self.store.delete("pending_player_context")
        await self._record_tool_use("hsr_discard_player_context")
        if session_is_active(await self._read_session(), ttl_seconds=self._session_ttl_seconds):
            await self._push_companion_context("profile_proposal_discarded")
        return {"discarded": bool(pending), "profile_changed": False}

    def _lookup_payload(self, query: str, category: str = "", limit: int = 5):
        query = str(query or "").strip()
        if not query:
            raise ValueError("query is required")
        type_map = {
            "character": ["character", "character_form"],
            "light_cone": ["light_cone"],
            "relic": ["relic_set"],
            "item": ["item"],
            "path": ["path"],
            "element": ["element"],
        }
        results = self._catalog.search(
            query,
            entity_types=type_map.get(category, []),
            limit=limit,
        )
        pack = self._public_data_pack_status()
        source = "external_v08" if pack.get("installed") else "minimal_character_registry"
        return {
            "query": query,
            "count": len(results),
            "results": results,
            "catalog": self._catalog.meta,
            "data_pack": pack,
            "result_source": source,
            "notice": (
                "未找到匹配资料。仅资料查询结束；游戏任务完成状态未知，不能称任务完成、失败或已领取奖励。"
                if not results else "结果来自固定版本、带来源的外部本地资料组件；查询完成不代表游戏任务完成。"
                if pack.get("installed")
                else "完整资料组件尚未就绪；当前只能确认最小角色注册表中的身份，不得补猜技能或攻略。"
            ),
        }

    @ui.action(id="lookup", label="查询知识", tone="primary", refresh_context=False)
    @plugin_entry(
        id="lookup",
        name="查询星铁知识",
        description="查询带来源、数据版本和校验状态的星铁结构化数据库。",
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "category": {"type": "string", "default": ""},
                "limit": {"type": "integer", "default": 5},
            },
            "required": ["query"],
        },
        llm_result_fields=["query", "count", "results", "notice"],
    )
    async def lookup(self, query: str, category: str = "", limit: int = 5, **_):
        try:
            return Ok(self._lookup_payload(query, category, limit))
        except ValueError as exc:
            return Err(SdkError(str(exc)))

    @llm_tool(
        name="hsr_lookup",
        description="查询《崩坏：星穹铁道》知识。结果包含来源和版本校验状态；回答事实问题时应优先调用。",
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "角色、光锥、遗器或机制名称",
                },
                "category": {
                    "type": "string",
                    "enum": [
                        "",
                        "character",
                        "light_cone",
                        "relic",
                        "item",
                        "path",
                        "element",
                        "mechanic",
                        "game_mode",
                    ],
                    "default": "",
                },
                "limit": {"type": "integer", "minimum": 1, "maximum": 20, "default": 5},
            },
            "required": ["query"],
        },
    )
    async def hsr_lookup(self, *, query: str, category: str = "", limit: int = 5, **_):
        self._note_speech_query()
        try:
            _, started = await self._touch_session("tool:hsr_lookup")
            if started:
                await self._push_companion_context("auto_start:hsr_lookup")
            await self._record_tool_use("hsr_lookup", query=str(query or "")[:160], category=category)
            return self._lookup_payload(query, category, limit)
        except ValueError as exc:
            return {
                "output": {"reason": str(exc)},
                "is_error": True,
                "error": "INVALID_QUERY",
            }

    @ui.action(
        id="update_player_context",
        label="更新玩家档案",
        tone="success",
        refresh_context=True,
    )
    @plugin_entry(
        id="update_player_context",
        name="更新玩家星铁语境",
        description="根据玩家明确提供的信息更新档案；角色字段必须通过固定版本角色数据库校验。",
        input_schema={
            "type": "object",
            "properties": {
                "changes": {"type": "object", "properties": PROFILE_PROPERTIES},
                "mode": {
                    "type": "string",
                    "enum": ["merge", "replace"],
                    "default": "merge",
                },
                "clear_fields": {
                    "type": "array",
                    "items": {"type": "string"},
                    "default": [],
                },
            },
            "required": ["changes"],
        },
        llm_result_fields=["profile", "memory_boundary"],
    )
    async def update_player_context(
        self,
        changes: dict[str, Any],
        mode: str = "merge",
        clear_fields: list[str] | None = None,
        **_,
    ):
        try:
            profile = await self._write_profile(changes, mode=mode, clear_fields=clear_fields)
            if session_is_active(await self._read_session(), ttl_seconds=self._session_ttl_seconds):
                await self._push_companion_context("profile_updated")
            return Ok(
                {
                    "profile": profile,
                    "memory_boundary": "插件结构化档案；不等同于 N.E.K.O 长期情感记忆。",
                }
            )
        except (ValueError, RuntimeError) as exc:
            return Err(SdkError(str(exc)))

    @llm_tool(
        name="hsr_update_player_context",
        description=(
            "仅在玩家用文字明确提供并要求记住、更正或删除星铁信息时，更新结构化游戏档案。"
            "不得用于截图或视觉推断；视觉结果必须先调用 hsr_stage_player_context 并等待确认。"
            "角色字段必须通过插件角色数据库校验，禁止编造或近似猜测。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "changes": {"type": "object", "properties": PROFILE_PROPERTIES},
                "mode": {
                    "type": "string",
                    "enum": ["merge", "replace"],
                    "default": "merge",
                },
                "clear_fields": {
                    "type": "array",
                    "items": {"type": "string"},
                    "default": [],
                },
            },
            "required": ["changes"],
        },
    )
    async def hsr_update_player_context(
        self,
        *,
        changes: dict[str, Any],
        mode: str = "merge",
        clear_fields: list[str] | None = None,
        **_,
    ):
        try:
            _, started = await self._touch_session("tool:hsr_update_player_context")
            if started:
                await self._push_companion_context("auto_start:hsr_update_player_context")
            profile = await self._write_profile(changes, mode=mode, clear_fields=clear_fields)
            await self._record_tool_use("hsr_update_player_context", mode=mode, field_count=len(changes or {}))
            await self._push_companion_context("profile_updated_by_chat")
            return {
                "profile": profile,
                "memory_boundary": "插件结构化档案；不等同于 N.E.K.O 长期情感记忆。",
            }
        except (ValueError, RuntimeError) as exc:
            return {
                "output": {"reason": str(exc)},
                "is_error": True,
                "error": "PROFILE_UPDATE_FAILED",
            }

    @ui.action(
        id="get_player_context",
        label="读取玩家档案",
        tone="default",
        refresh_context=False,
    )
    @plugin_entry(
        id="get_player_context",
        name="读取玩家星铁语境",
        description="读取玩家主动提供的结构化星铁档案。",
        llm_result_fields=["profile", "memory_boundary"],
    )
    async def get_player_context(self, **_):
        return Ok(
            {
                "profile": await self._profile_for_advice(),
                "memory_boundary": "插件结构化档案；不等同于 N.E.K.O 长期情感记忆。",
            }
        )

    @llm_tool(
        name="hsr_get_player_context",
        description="读取玩家星铁档案及手动/UID合并配装；手动字段优先，不查询网络、不证明当前画面。指定character_names可读取完整已有遗器和行迹；手动套装不等于单件词条。",
        parameters={"type": "object", "properties": {"character_names": {"type": "array", "items": {"type": "string"}, "maxItems": 12}}},
    )
    async def hsr_get_player_context(self, character_names: list[str] | None = None, **_):
        session, started = await self._touch_session("tool:hsr_get_player_context")
        if started:
            await self._push_companion_context("auto_start:hsr_get_player_context")
            session = await self._read_session()
        corrections = await self._read_corrections()
        await self._record_tool_use("hsr_get_player_context")
        if character_names is not None and (not isinstance(character_names, list) or len(character_names) > 12
                                           or any(not isinstance(name, str) or len(name) > 80 for name in character_names)):
            raise ValueError("character_names必须是至多12个角色名")
        return {
            "profile": await self._profile_for_advice(),
            "imported_roster": await self._imported_roster(details=bool(character_names), names=character_names),
            "session": session,
            "pending_profile_proposal": await self._read_pending_profile(),
            "correction_status": {
                "active_count": sum(1 for item in corrections if item.get("active") is not False),
                "disabled_count": sum(1 for item in corrections if item.get("active") is False),
                "policy": "历史纠错不会自动作为当前角色返回。",
            },
            "memory_boundary": "插件结构化档案；不等同于 N.E.K.O 长期情感记忆。",
        }

    async def _analysis_payload(self, members: list[str], goal: str = ""):
        return team_overview(members, catalog=self._catalog, goal=goal)

    @ui.action(id="analyze_team", label="分析队伍", tone="primary", refresh_context=False)
    @plugin_entry(
        id="analyze_team",
        name="分析星铁队伍",
        description="结合带来源的结构化目录、精选机制笔记和玩家档案做透明的基础队伍检查。",
        input_schema={
            "type": "object",
            "properties": {
                "members": {"type": "array", "items": {"type": "string"}},
                "goal": {"type": "string", "default": ""},
            },
            "required": ["members"],
        },
        llm_result_fields=[
            "members",
            "goal",
            "strengths",
            "issues",
            "recommended_next_steps",
            "evidence",
            "disclaimer",
            "analysis_available",
            "analysis_scope",
            "member_mechanics",
            "missing_information",
            "team_optimization_available",
        ],
    )
    async def analyze_team_entry(self, members: list[str], goal: str = "", **_):
        return Ok(await self._analysis_payload(members, goal))

    @llm_tool(
        name="hsr_analyze_team",
        description="核对星铁队伍并提供带来源的成员技能机制，用于解释而非模拟或最优配队。多形态先确认，缺失不等于阵容缺陷，禁止编造强弱、缺生存或调整方案。",
        parameters={
            "type": "object",
            "properties": {
                "members": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                },
                "goal": {"type": "string", "default": ""},
            },
            "required": ["members"],
        },
    )
    async def hsr_analyze_team(self, *, members: list[str], goal: str = "", **_):
        self._note_speech_query()
        _, started = await self._touch_session("tool:hsr_analyze_team")
        if started:
            await self._push_companion_context("auto_start:hsr_analyze_team")
        await self._record_tool_use("hsr_analyze_team", member_count=len(members or []), goal=goal[:160])
        return await self._analysis_payload(members, goal)

    async def _record_experience(
        self,
        *,
        event_type: str,
        summary: str,
        importance: str = "normal",
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        summary = str(summary or "").strip()
        if not summary:
            raise ValueError("summary is required")
        experiences = unwrap_or(await self._get_store("experiences"), [])
        if not isinstance(experiences, list):
            experiences = []
        item = {
            "experience_id": uuid4().hex,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "event_type": str(event_type or "other").strip() or "other",
            "summary": summary,
            "importance": importance if importance in {"normal", "important", "milestone"} else "normal",
            "tags": list(dict.fromkeys(str(tag).strip() for tag in (tags or []) if str(tag).strip())),
            "memory_status": "plugin_record_only",
        }
        experiences.append(item)
        experiences = experiences[-self._max_experiences :]
        stored = await self.store.set("experiences", experiences)
        if isinstance(stored, Err):
            raise RuntimeError("EXPERIENCE_STORE_FAILED")
        return item

    @ui.action(
        id="record_experience",
        label="记录游戏经历",
        tone="success",
        refresh_context=True,
    )
    @plugin_entry(
        id="record_experience",
        name="记录星铁经历",
        description="记录玩家明确确认的星铁经历；记录只保存在插件档案中。",
        input_schema={
            "type": "object",
            "properties": {
                "event_type": {"type": "string"},
                "summary": {"type": "string"},
                "importance": {
                    "type": "string",
                    "enum": ["normal", "important", "milestone"],
                    "default": "normal",
                },
                "tags": {"type": "array", "items": {"type": "string"}, "default": []},
            },
            "required": ["event_type", "summary"],
        },
        llm_result_fields=["experience", "memory_boundary"],
    )
    async def record_experience(
        self,
        event_type: str,
        summary: str,
        importance: str = "normal",
        tags: list[str] | None = None,
        **_,
    ):
        try:
            item = await self._record_experience(
                event_type=event_type,
                summary=summary,
                importance=importance,
                tags=tags,
            )
            return Ok(
                {
                    "experience": item,
                    "memory_boundary": "这是插件内可更正的事实记录，不会直接写入 N.E.K.O 长期记忆。",
                }
            )
        except (ValueError, RuntimeError) as exc:
            return Err(SdkError(str(exc)))

    @llm_tool(
        name="hsr_record_experience",
        description="当玩家明确确认一段值得保留的星铁经历时记录它。不要把猜测、攻略结论或未发生的愿望记成经历。",
        parameters={
            "type": "object",
            "properties": {
                "event_type": {"type": "string"},
                "summary": {"type": "string"},
                "importance": {
                    "type": "string",
                    "enum": ["normal", "important", "milestone"],
                    "default": "normal",
                },
                "tags": {"type": "array", "items": {"type": "string"}, "default": []},
            },
            "required": ["event_type", "summary"],
        },
    )
    async def hsr_record_experience(
        self,
        *,
        event_type: str,
        summary: str,
        importance: str = "normal",
        tags: list[str] | None = None,
        **_,
    ):
        try:
            _, started = await self._touch_session("tool:hsr_record_experience")
            if started:
                await self._push_companion_context("auto_start:hsr_record_experience")
            await self._record_tool_use("hsr_record_experience", event_type=event_type, importance=importance)
            return {
                "experience": await self._record_experience(
                    event_type=event_type,
                    summary=summary,
                    importance=importance,
                    tags=tags,
                ),
                "memory_boundary": "这是插件内可更正的事实记录，不会直接写入 N.E.K.O 长期记忆。",
            }
        except (ValueError, RuntimeError) as exc:
            return {
                "output": {"reason": str(exc)},
                "is_error": True,
                "error": "EXPERIENCE_RECORD_FAILED",
            }

    @plugin_entry(
        id="notify_game_event",
        name="提供星铁事件",
        description="向 N.E.K.O 提供一个去重、可过期的星铁事件。默认仅预览，不推送。",
        input_schema={
            "type": "object",
            "properties": {
                "event_id": {"type": "string"},
                "event_type": {"type": "string"},
                "summary": {"type": "string"},
                "occurred_at": {"type": "string", "description": "ISO 8601 时间"},
                "expires_in_seconds": {"type": "integer", "default": 300},
                "request_response": {"type": "boolean", "default": False},
                "dry_run": {"type": "boolean", "default": True},
            },
            "required": ["event_id", "event_type", "summary", "occurred_at"],
        },
        llm_result_fields=["accepted", "reason", "submitted", "dry_run"],
    )
    async def notify_game_event(
        self,
        event_id: str,
        event_type: str,
        summary: str,
        occurred_at: str,
        expires_in_seconds: int = 300,
        request_response: bool = False,
        dry_run: bool = True,
        **_,
    ):
        _, started = await self._touch_session(f"event:{event_type}")
        if started or str(event_type or "") == "game_session_start":
            await self._push_companion_context(f"event_context:{event_type}")
        seen = unwrap_or(await self._get_store("seen_event_ids"), [])
        seen = seen if isinstance(seen, list) else []
        decision = evaluate_event(
            event_id=event_id,
            occurred_at=occurred_at,
            expires_in_seconds=expires_in_seconds,
            seen_event_ids=seen,
        )
        if not decision["accepted"] or dry_run:
            return Ok({**decision, "submitted": False, "dry_run": bool(dry_run)})

        message = (
            "[星铁陪玩事件]\n"
            f"事件类型：{str(event_type).strip()}\n"
            f"已知事实：{str(summary).strip()}\n"
            "请结合你自己的人格、当前对话和玩家语境，自行决定如何自然回应；不要把事件扩写成未发生的事实。"
        )
        push_result = self.push_message(
            visibility=[],
            ai_behavior="respond" if request_response else "read",
            parts=[{"type": "text", "text": message}],
            source="hsr_companion",
            metadata={"event_id": event_id, "event_type": event_type},
            priority=5 if request_response else 1,
        )
        submitted = bool(push_result.get("submitted"))
        if submitted:
            seen.append(event_id)
            await self.store.set("seen_event_ids", seen[-self._event_dedup_limit :])
        return Ok(
            {
                **decision,
                "submitted": submitted,
                "dry_run": False,
                "delivery_reason": push_result.get("reason"),
                "ai_behavior": "respond" if request_response else "read",
            }
        )

    @ui.action(
        id="reset_demo_data",
        label="清空插件数据",
        tone="danger",
        confirm="只清空星铁插件保存的玩家档案、纠错、状态和经历？",
        refresh_context=True,
    )
    @plugin_entry(
        id="reset_demo_data",
        name="清空星铁插件数据",
        description="仅清空本插件保存的玩家档案、纠错、画面状态、经历和事件去重记录。",
        input_schema={"type": "object", "properties": {}},
        llm_result_fields=["cleared_keys"],
    )
    async def reset_demo_data(self, **_):
        self._uid_import_generation = getattr(self, "_uid_import_generation", 0) + 1
        await self._cancel_observer_task()
        self._last_successful_observation = None
        self._runtime.reset()
        if session_is_active(await self._read_session(), ttl_seconds=self._session_ttl_seconds):
            await self._stop_companion_session("reset_demo_data")
        cleared: list[str] = []
        for key in (
            UID_STORE_KEY,
            MANUAL_BUILDS_KEY,
            "player_context",
            "pending_player_context",
            "experiences",
            "seen_event_ids",
            "companion_session",
            "character_corrections",
            "context_audit",
            "tool_audit",
            "active_visual_request",
            "active_game_visual_request",
            "current_game_state",
            "last_unverified_game_state_hint",
            "game_state_history",
            "pending_game_state_candidate",
            "observer_settings",
            "last_local_vision",
        ):
            deleted = unwrap_or(await self.store.delete(key), False)
            if deleted:
                cleared.append(key)
        return Ok({"cleared_keys": cleared})


__all__ = ["HsrCompanionPlugin"]
