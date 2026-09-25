from __future__ import annotations

import asyncio
import threading
import time
from dataclasses import asdict
from datetime import datetime
from typing import Any, Mapping

from plugin.sdk.plugin import (
    Err,
    NekoPluginBase,
    Ok,
    SdkError,
    get_os_activity_snapshot,
    lifecycle,
    neko_plugin,
    plugin_entry,
    timer_interval,
    tr,
    ui,
)

from .config import RecommendationConfig, normalize_settings_update
from .feedback import apply_feedback_to_profile, settle_history
from .gate import evaluate_gate
from .llm_gateway import BackgroundLlm
from .openbiliclaw_compat import (
    OpenBiliClawCompatibilityServer,
    apply_behavior_event_batch,
)
from .openbiliclaw_profile import fetch_openbiliclaw_profile
from .openbiliclaw_recommendations import fetch_openbiliclaw_recommendations
from .proactive_policy import fetch_main_proactive_policy
from .profile import (
    active_interests,
    apply_profile_updates,
    heuristic_updates,
    message_from_memory_record,
)
from .prompting import (
    build_neko_handoff_prompt,
    canonical_candidate_url,
)
from .ranking import rank_candidates, was_previously_delivered
from .sources import discover_from_plugins
from .state import StateRepository


@neko_plugin
class ProactiveRecommenderPlugin(NekoPluginBase):
    """Personalized proactive content recommendations, entirely plugin-side."""

    _COMPAT_SHUTDOWN_TIMEOUT_SECONDS = 0.5

    def __init__(self, ctx: Any) -> None:
        super().__init__(ctx)
        self._config = RecommendationConfig()
        self._state = StateRepository(self.store)
        self._llm = BackgroundLlm(self.logger)
        self._ready = False
        self._stopping = threading.Event()
        self._initialization_lock = threading.Lock()
        self._cycle_lock = threading.Lock()
        self._compat_server: OpenBiliClawCompatibilityServer | None = None

    async def _load_config(self) -> None:
        # Hosted UI writes the plugin's base config. Reading that same layer
        # avoids an older active profile masking a just-saved value.
        payload = await self.ctx.get_own_base_config(timeout=5.0)
        raw = payload.get("config") if isinstance(payload, Mapping) else payload
        self._config = RecommendationConfig.from_mapping(
            raw if isinstance(raw, Mapping) else {}
        )

    async def _ensure_ready(self) -> bool:
        """Initialize once even if an older host skips lifecycle.startup."""
        if self._ready:
            return True
        if self._stopping.is_set():
            return False
        if not self._initialization_lock.acquire(blocking=False):
            return False
        try:
            if self._ready:
                return True
            if self._stopping.is_set():
                return False
            # The effective-config read refreshes SDK runtime helpers in the
            # child process. Settings still come from the base layer below so
            # the Hosted UI and runtime use the same editable values.
            await self.ctx.get_own_config(timeout=5.0)
            await self._load_config()
            if not self.store.enabled:
                raise RuntimeError(
                    "PluginStore must be enabled for proactive_recommender"
                )
            await self._state.load()
            if self._stopping.is_set():
                return False
            self._ready = True
            await self._sync_compat_server()
            await self._sync_openbiliclaw_profile(time.time(), force=True)
            self.logger.info(
                "proactive recommender ready (enabled={}, shadow={}, "
                "openbiliclaw={})",
                self._config.enabled,
                self._config.shadow_mode,
                self._compat_server.running if self._compat_server else False,
            )
            return True
        except Exception:
            self._ready = False
            raise
        finally:
            self._initialization_lock.release()

    @lifecycle(id="startup")
    async def startup(self, **_: Any):
        self._stopping.clear()
        try:
            ready = await self._ensure_ready()
        except Exception as exc:
            return Err(SdkError(f"plugin initialization failed: {type(exc).__name__}"))
        return Ok(
            {
                "ready": ready,
                "openbiliclaw": self._compatibility_state(),
            }
        )

    @lifecycle(id="shutdown")
    async def shutdown(self, **_: Any):
        started_at = time.monotonic()
        self._stopping.set()
        self._ready = False
        cycle_active = self._cycle_lock.locked()
        compat_server = self._compat_server
        self._compat_server = None
        compat_stopped = True
        if compat_server is not None:
            compat_started_at = time.monotonic()
            compat_stopped = await compat_server.stop(
                timeout=self._COMPAT_SHUTDOWN_TIMEOUT_SECONDS
            )
            self.logger.info(
                "proactive recommender compatibility shutdown "
                "(stopped={}, elapsed={:.3f}s)",
                compat_stopped,
                time.monotonic() - compat_started_at,
            )
        self.logger.info(
            "proactive recommender shutdown complete "
            "(cycle_active={}, elapsed={:.3f}s)",
            cycle_active,
            time.monotonic() - started_at,
        )
        return Ok(
            {
                "ready": False,
                "cycle_active": cycle_active,
                "compat_server_stopped": compat_stopped,
            }
        )

    @lifecycle(id="config_change")
    async def config_change(self, **_: Any):
        if self._stopping.is_set():
            return Ok({"reloaded": False, "reason": "plugin_stopping"})
        await self._load_config()
        await self._sync_compat_server()
        return Ok({"reloaded": True})

    async def _sync_compat_server(self) -> None:
        if self._stopping.is_set():
            return
        desired = (
            self._config.openbiliclaw_host,
            self._config.openbiliclaw_port,
        )
        current = self._compat_server
        if not self._config.openbiliclaw_enabled:
            if current is not None:
                await current.stop()
                self._compat_server = None
            return
        if current is not None and (current.host, current.port) == desired and current.running:
            return
        if current is not None:
            await current.stop()
        server = OpenBiliClawCompatibilityServer(
            host=desired[0],
            port=desired[1],
            on_events=self._ingest_platform_events,
            status_provider=self._openbiliclaw_runtime_status,
            logger=self.logger,
        )
        self._compat_server = server
        try:
            await server.start()
        except Exception as exc:
            self.logger.warning(
                "OpenBiliClaw compatibility server failed on {}:{}: {}",
                desired[0],
                desired[1],
                type(exc).__name__,
            )

    def _compatibility_state(self) -> dict[str, Any]:
        if self._compat_server is not None:
            status = self._compat_server.snapshot()
        else:
            status = {
                "enabled": self._config.openbiliclaw_enabled,
                "running": False,
                "host": self._config.openbiliclaw_host,
                "port": self._config.openbiliclaw_port,
                "endpoint": (
                    f"http://{self._config.openbiliclaw_host}:"
                    f"{self._config.openbiliclaw_port}"
                ),
                "connected_clients": 0,
                "started_at": 0.0,
                "last_error": "",
                "cookie_ingest": False,
            }
        status["compatibility_level"] = "behavior-events+recommendation-pull"
        status["backend_endpoint"] = (
            f"http://127.0.0.1:{self._config.openbiliclaw_backend_port}"
        )
        return status

    def _openbiliclaw_runtime_status(self) -> dict[str, Any]:
        snapshot = self._state.snapshot()
        platform_events = snapshot.get("platform_events", {})
        recommendation_sync = snapshot.get("openbiliclaw_recommendations", {})
        profile_sync = snapshot.get("openbiliclaw_profile", {})
        return {
            "initialized": True,
            "recommendation_count": len(snapshot.get("candidates", [])),
            "pending_signal_events": 0,
            "unread_count": 0,
            "neko_compatibility": True,
            "behavior_events_accepted": int(platform_events.get("accepted", 0)),
            "recommendations_fetched": int(
                recommendation_sync.get("last_fetched", 0)
            ),
            "recommendation_sync_error": str(
                recommendation_sync.get("last_error", "")
            ),
            "profile_initialized": bool(
                profile_sync.get("data", {}).get("initialized", False)
                if isinstance(profile_sync.get("data"), Mapping)
                else False
            ),
            "profile_sync_error": str(profile_sync.get("last_error", "")),
        }

    async def _ingest_platform_events(
        self, events: list[Mapping[str, Any]]
    ) -> dict[str, Any]:
        result: dict[str, Any] = {}

        def mutate(state: dict[str, Any]) -> None:
            result.update(apply_behavior_event_batch(state, events, now=time.time()))

        await self._state.update(mutate)
        return result

    async def _new_messages(self) -> list[dict[str, Any]]:
        try:
            records = await asyncio.to_thread(
                self.bus.memory.get,
                bucket_id=self._config.memory_bucket,
                limit=100,
                timeout=5.0,
            )
        except Exception as exc:
            # Browser-derived OpenBiliClaw recommendations remain useful when
            # the optional NEKO conversation-context channel is unavailable.
            self.logger.debug(
                "local conversation context unavailable: {}", type(exc).__name__
            )
            return []
        snapshot = self._state.snapshot()
        processed = set(
            str(value) for value in snapshot.get("processed_message_ids", [])
        )
        output = []
        for record in records:
            message = message_from_memory_record(record)
            if message is not None and message["id"] not in processed:
                output.append(message)
        return output

    async def _sync_openbiliclaw_recommendations(self, now: float) -> int:
        if not self._config.openbiliclaw_enabled:
            return 0
        result = await fetch_openbiliclaw_recommendations(
            port=self._config.openbiliclaw_backend_port
        )
        snapshot = self._state.snapshot()
        interests = active_interests(snapshot.get("profile"), include_trial=True)
        ranked = rank_candidates(
            result.candidates, interests, snapshot.get("history", [])
        )
        delivered_ids = {
            str(item.get("candidate_id"))
            for item in snapshot.get("history", [])
            if isinstance(item, Mapping)
        }
        existing_ids = {
            str(item.get("id"))
            for item in snapshot.get("candidates", [])
            if isinstance(item, Mapping)
        }
        new_items = [
            item
            for item in ranked
            if str(item.get("id")) not in delivered_ids
            and str(item.get("id")) not in existing_ids
        ]

        def mutate(state: dict[str, Any]) -> None:
            status = state.setdefault("openbiliclaw_recommendations", {})
            status["last_sync_at"] = now
            status["last_error"] = result.error
            status["last_fetched"] = len(result.candidates)
            status["endpoint"] = result.endpoint
            status["total_imported"] = int(status.get("total_imported", 0)) + len(
                new_items
            )
            if result.error:
                return
            existing = {
                str(item.get("id")): dict(item)
                for item in state.get("candidates", [])
                if isinstance(item, Mapping)
            }
            existing.update(
                {
                    str(item["id"]): item
                    for item in ranked
                    if str(item["id"]) not in delivered_ids
                }
            )
            state["candidates"] = sorted(
                existing.values(),
                key=lambda item: float(item.get("score", 0.0)),
                reverse=True,
            )[:50]

        await self._state.update(mutate)
        if result.error:
            self.logger.debug(
                "OpenBiliClaw recommendation sync unavailable: {}", result.error
            )
        return len(new_items)

    async def _sync_openbiliclaw_profile(
        self, now: float, *, force: bool = False
    ) -> None:
        if not self._config.openbiliclaw_enabled:
            return
        cached = self._state.snapshot().get("openbiliclaw_profile", {})
        if (
            not force
            and isinstance(cached, Mapping)
            and now - float(cached.get("last_sync_at", 0.0)) < 300
        ):
            return
        result = await fetch_openbiliclaw_profile(
            port=self._config.openbiliclaw_backend_port
        )

        def mutate(state: dict[str, Any]) -> None:
            status = state.setdefault("openbiliclaw_profile", {})
            status["last_sync_at"] = now
            status["last_error"] = result.error
            status["endpoint"] = result.endpoint
            if not result.error:
                status["data"] = result.profile

        await self._state.update(mutate)
        if result.error:
            self.logger.debug(
                "OpenBiliClaw profile sync unavailable: {}", result.error
            )

    async def _update_profile(self, messages: list[dict[str, Any]], now: float) -> None:
        if not messages:
            snapshot = self._state.snapshot()
            settled = settle_history(
                snapshot.get("history", []),
                [],
                now=now,
                reply_window_seconds=self._config.reply_window_minutes * 60,
                ignored_window_seconds=self._config.ignored_window_minutes * 60,
            )
            if settled != snapshot.get("history", []):

                def mutate(state: dict[str, Any]) -> None:
                    state["profile"] = apply_feedback_to_profile(
                        state.get("profile", {}),
                        state.get("history", []),
                        settled,
                        now=now,
                    )
                    state["history"] = settled

                await self._state.update(mutate)
            return

        updates: list[dict[str, Any]] = []
        if self._config.background_llm:
            updates = await self._llm.extract_interests(messages)
        if not updates:
            updates = heuristic_updates(messages)

        def mutate(state: dict[str, Any]) -> None:
            state["profile"] = apply_profile_updates(
                state.get("profile"), updates, now=now
            )
            seen = [str(value) for value in state.get("processed_message_ids", [])]
            seen.extend(str(message["id"]) for message in messages)
            state["processed_message_ids"] = list(dict.fromkeys(seen))[-500:]
            state["last_user_message_at"] = max(
                float(message.get("timestamp", 0.0)) for message in messages
            )
            before = state.get("history", [])
            settled = settle_history(
                state.get("history", []),
                messages,
                now=now,
                reply_window_seconds=self._config.reply_window_minutes * 60,
                ignored_window_seconds=self._config.ignored_window_minutes * 60,
            )
            state["profile"] = apply_feedback_to_profile(
                state["profile"], before, settled, now=now
            )
            state["history"] = settled

        await self._state.update(mutate)

    def _discovery_due(
        self, snapshot: Mapping[str, Any], now: float, force: bool
    ) -> bool:
        if force:
            return True
        candidates = (
            snapshot.get("candidates")
            if isinstance(snapshot.get("candidates"), list)
            else []
        )
        fresh = [
            item
            for item in candidates
            if isinstance(item, Mapping)
            and now - float(item.get("discovered_at", 0.0))
            < self._config.candidate_ttl_hours * 3600
        ]
        return (
            len(fresh) < 5
            and now - float(snapshot.get("last_discovery_at", 0.0)) >= 3600
        )

    async def _discover(self, now: float, *, force: bool = False) -> int:
        snapshot = self._state.snapshot()
        if not self._discovery_due(snapshot, now, force):
            return 0
        interests = active_interests(snapshot.get("profile"), include_trial=True)
        if not interests:
            return 0
        queries = [str(item.get("name") or "") for item in interests[:2]]
        try:
            candidates = await discover_from_plugins(
                self.plugins,
                queries,
                web_search=self._config.web_search,
                bilibili=self._config.bilibili,
            )
        except Exception as exc:
            self.logger.warning(
                "recommendation discovery failed: {}", type(exc).__name__
            )
            return 0
        if self._config.background_llm and candidates:
            scores = await self._llm.assess_candidates(interests, candidates)
            for candidate in candidates:
                score = scores.get(str(candidate.get("id")))
                if score:
                    candidate["llm_relevance"] = score["relevance"]
                    candidate["llm_quality"] = score["quality"]
        ranked = rank_candidates(candidates, interests, snapshot.get("history", []))
        delivered_ids = {
            str(item.get("candidate_id"))
            for item in snapshot.get("history", [])
            if isinstance(item, Mapping)
        }

        def mutate(state: dict[str, Any]) -> None:
            existing = {
                str(item.get("id")): dict(item)
                for item in state.get("candidates", [])
                if isinstance(item, Mapping)
            }
            existing.update(
                {
                    str(item["id"]): item
                    for item in ranked
                    if str(item["id"]) not in delivered_ids
                }
            )
            state["candidates"] = sorted(
                existing.values(),
                key=lambda item: float(item.get("score", 0.0)),
                reverse=True,
            )[:50]
            state["last_discovery_at"] = now

        await self._state.update(mutate)
        return len(ranked)

    async def _proactive_policy(self) -> dict[str, Any]:
        result = await fetch_main_proactive_policy()
        if not result.error:
            return {
                "mode": result.mode,
                "settings": result.settings,
                "source": "main_api",
            }
        self.logger.warning(
            "main proactive policy unavailable: {}", result.error
        )
        return {
            "mode": "unavailable",
            "settings": {"proactiveChatEnabled": False},
            "source": "fail_closed",
        }

    async def _deliver(self, now: float) -> dict[str, Any]:
        if self._stopping.is_set():
            return {"submitted": False, "reason": "plugin_stopping"}
        snapshot = self._state.snapshot()
        ttl = self._config.candidate_ttl_hours * 3600
        candidates = [
            dict(item)
            for item in snapshot.get("candidates", [])
            if isinstance(item, Mapping)
            and now - float(item.get("discovered_at", 0.0)) < ttl
        ]
        eligible = [
            item
            for item in candidates
            if float(item.get("score", 0.0)) >= self._config.score_threshold
            and str(item.get("title") or "").strip()
            and canonical_candidate_url(item)
            and not was_previously_delivered(item, snapshot.get("history", []))
        ]
        if not eligible:
            return {"submitted": False, "reason": "no_eligible_candidate"}

        proactive_policy = await self._proactive_policy()
        proactive_settings = proactive_policy.get("settings", {})
        proactive_enabled = (
            proactive_policy.get("mode") != "off"
            and isinstance(proactive_settings, Mapping)
            and bool(proactive_settings.get("proactiveChatEnabled", True))
        )
        privacy_state, idle_seconds = "unavailable", None
        try:
            activity = await get_os_activity_snapshot("proactive_recommender")
            privacy_state = activity.privacy_state
            idle_seconds = activity.system_idle_seconds
        except Exception:
            pass
        decision = evaluate_gate(
            config=self._config,
            now=datetime.fromtimestamp(now).astimezone(),
            history=snapshot.get("history", []),
            proactive_enabled=proactive_enabled,
            privacy_state=privacy_state,
            idle_seconds=idle_seconds,
            last_user_message_at=float(snapshot.get("last_user_message_at", 0.0)),
        )
        if not decision.allowed:
            return {"submitted": False, "reason": decision.reason}

        candidate = eligible[0]
        mode = "shadow" if self._config.shadow_mode else "live"
        submitted = False
        reason = "shadow_mode"
        if mode == "live":
            if self._stopping.is_set():
                return {"submitted": False, "reason": "plugin_stopping"}
            handoff_prompt = build_neko_handoff_prompt(candidate)
            if not handoff_prompt:
                return {
                    "submitted": False,
                    "reason": "invalid_candidate_content",
                    "candidate_id": candidate.get("id"),
                    "mode": mode,
                }
            receipt = self.push_message(
                source="proactive_recommender",
                visibility=[],
                ai_behavior="respond",
                parts=[{"type": "text", "text": handoff_prompt}],
                priority=3,
                coalesce_key=f"proactive_recommender:{candidate.get('id')}",
            )
            submitted = (
                bool(receipt.get("submitted"))
                if isinstance(receipt, Mapping)
                else False
            )
            receipt_reason = (
                str(receipt.get("reason", "rejected"))
                if isinstance(receipt, Mapping)
                else "rejected"
            )
            reason = (
                "handoff_submitted"
                if submitted
                else receipt_reason
            )

        def mutate(state: dict[str, Any]) -> None:
            state["last_handoff"] = {
                "candidate_id": candidate.get("id"),
                "title": candidate.get("title"),
                "source": candidate.get("source"),
                "timestamp": now,
                "mode": mode,
                "submitted": submitted,
                "reason": reason,
                "policy_source": proactive_policy.get("source"),
            }
            if mode == "shadow" or submitted:
                state["candidates"] = [
                    item
                    for item in state.get("candidates", [])
                    if isinstance(item, Mapping)
                    and str(item.get("id")) != str(candidate.get("id"))
                ]
                state.setdefault("history", []).append(
                    {
                        "candidate_id": candidate.get("id"),
                        "title": candidate.get("title"),
                        "url": canonical_candidate_url(candidate),
                        "source": candidate.get("source"),
                        "matched_interests": candidate.get("matched_interests", []),
                        "score": candidate.get("score"),
                        "timestamp": now,
                        "local_date": datetime.fromtimestamp(now)
                        .astimezone()
                        .date()
                        .isoformat(),
                        "mode": mode,
                        "submitted": submitted,
                        "outcome": (
                            "shadow" if mode == "shadow" else "handoff_submitted"
                        ),
                    }
                )
                state["history"] = state["history"][-200:]

        await self._state.update(mutate)
        return {
            "submitted": submitted,
            "reason": reason,
            "candidate_id": candidate.get("id"),
            "candidate_title": candidate.get("title"),
            "candidate_source": candidate.get("source"),
            "mode": mode,
        }

    async def _run_cycle(self, *, force_discovery: bool = False) -> dict[str, Any]:
        if self._stopping.is_set():
            return {"enabled": False, "reason": "plugin_stopping"}
        if not self._ready:
            return {"enabled": False, "reason": "plugin_not_ready"}
        if not self._config.enabled:
            return {"enabled": False, "reason": "plugin_disabled"}
        now = time.time()
        messages = await self._new_messages()
        if self._stopping.is_set():
            return {"enabled": False, "reason": "plugin_stopping"}
        await self._update_profile(messages, now)
        if self._stopping.is_set():
            return {"enabled": False, "reason": "plugin_stopping"}
        await self._sync_openbiliclaw_profile(now)
        if self._stopping.is_set():
            return {"enabled": False, "reason": "plugin_stopping"}
        imported = await self._sync_openbiliclaw_recommendations(now)
        if self._stopping.is_set():
            return {"enabled": False, "reason": "plugin_stopping"}
        discovered = imported + await self._discover(now, force=force_discovery)
        if self._stopping.is_set():
            return {"enabled": False, "reason": "plugin_stopping"}
        delivery = await self._deliver(now)
        result = {
            "enabled": True,
            "messages_processed": len(messages),
            "discovered": discovered,
            "delivery": delivery,
        }

        def remember_run(state: dict[str, Any]) -> None:
            state["last_run"] = {**result, "timestamp": now}

        await self._state.update(remember_run)
        return result

    def _dashboard_state(self) -> dict[str, Any]:
        snapshot = self._state.snapshot()
        interests = active_interests(snapshot.get("profile"), include_trial=True)
        candidates = [
            {
                "id": item.get("id"),
                "title": item.get("title"),
                "source": item.get("source"),
                "score": item.get("score"),
                "matched_interests": item.get("matched_interests", []),
                "discovered_at": item.get("discovered_at"),
            }
            for item in snapshot.get("candidates", [])[:12]
            if isinstance(item, Mapping)
        ]
        today = datetime.now().astimezone().date().isoformat()
        history = [
            dict(item)
            for item in snapshot.get("history", [])[-12:]
            if isinstance(item, Mapping)
        ]
        all_history = [
            item
            for item in snapshot.get("history", [])
            if isinstance(item, Mapping)
        ]
        platform_events = snapshot.get("platform_events", {})
        openbiliclaw_recommendations = snapshot.get(
            "openbiliclaw_recommendations", {}
        )
        openbiliclaw_profile = snapshot.get("openbiliclaw_profile", {})
        profile_data = (
            openbiliclaw_profile.get("data", {})
            if isinstance(openbiliclaw_profile, Mapping)
            else {}
        )
        profile_likes = (
            profile_data.get("likes", []) if isinstance(profile_data, Mapping) else []
        )
        return {
            "ready": self._ready,
            "store_enabled": bool(self.store.enabled),
            "config": asdict(self._config),
            "interests": [
                {
                    "name": item.get("name"),
                    "weight": item.get("weight"),
                    "status": item.get("status"),
                    "evidence_count": item.get("evidence_count", 0),
                    "negative_count": item.get("negative_count", 0),
                }
                for item in interests[:12]
            ],
            "candidates": candidates,
            "history": history,
            "last_run": snapshot.get("last_run", {}),
            "last_handoff": snapshot.get("last_handoff", {}),
            "openbiliclaw": {
                **self._compatibility_state(),
                "events": {
                    "accepted": int(platform_events.get("accepted", 0)),
                    "duplicate": int(platform_events.get("duplicate", 0)),
                    "rejected": int(platform_events.get("rejected", 0)),
                    "by_platform": dict(platform_events.get("by_platform", {})),
                    "last_event_at": float(platform_events.get("last_event_at", 0.0)),
                },
                "recommendations": dict(openbiliclaw_recommendations),
                "profile": dict(openbiliclaw_profile),
            },
            "metrics": {
                "profile_domain_count": len(profile_likes),
                "local_interest_count": len(interests),
                "candidate_count": len(snapshot.get("candidates", [])),
                "today_handoff_count": sum(
                    1
                    for item in all_history
                    if item.get("local_date") == today
                    and item.get("mode") == "live"
                    and item.get("submitted")
                ),
                "platform_event_count": int(platform_events.get("accepted", 0)),
            },
            "privacy": {
                "memory_window_minutes": 60,
                "raw_conversations_persisted": False,
                "sensitive_inference": False,
                "browser_cookies_persisted": False,
                "raw_platform_events_persisted": False,
            },
        }

    @ui.context(id="dashboard", title=tr("panel.title", default="个性化主动推荐"))
    async def dashboard_context(self) -> dict[str, Any]:
        await self._ensure_ready()
        return self._dashboard_state()

    @ui.action(
        id="update_recommendation_settings",
        label=tr("actions.updateSettings.label", default="保存推荐设置"),
        tone="success",
        group="config",
        order=10,
        refresh_context=True,
    )
    @plugin_entry(
        id="update_recommendation_settings",
        name=tr("entries.updateSettings.name", default="更新推荐设置"),
        description=tr(
            "entries.updateSettings.description",
            default="更新 Hosted UI 中公开的推荐开关、来源和频率门控。",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "enabled": {"type": "boolean"},
                "shadow_mode": {"type": "boolean"},
                "background_llm": {"type": "boolean"},
                "web_search": {"type": "boolean"},
                "bilibili": {"type": "boolean"},
                "openbiliclaw_enabled": {"type": "boolean"},
                "openbiliclaw_port": {
                    "type": "integer",
                    "minimum": 1024,
                    "maximum": 65535,
                },
                "openbiliclaw_backend_port": {
                    "type": "integer",
                    "minimum": 1024,
                    "maximum": 65535,
                },
                "daily_limit": {"type": "integer", "minimum": 0, "maximum": 20},
                "min_interval_minutes": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 1440,
                },
                "min_user_silence_minutes": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 1440,
                },
                "max_idle_seconds": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 86400,
                },
                "score_threshold": {
                    "type": "number",
                    "minimum": 0,
                    "maximum": 1,
                },
                "quiet_start": {
                    "type": "string",
                    "pattern": "^(?:[01]\\d|2[0-3]):[0-5]\\d$",
                },
                "quiet_end": {
                    "type": "string",
                    "pattern": "^(?:[01]\\d|2[0-3]):[0-5]\\d$",
                },
            },
            "additionalProperties": False,
        },
    )
    async def update_recommendation_settings(self, **kwargs: Any):
        await self._ensure_ready()
        try:
            updates = normalize_settings_update(kwargs)
        except (TypeError, ValueError) as exc:
            return Err(SdkError(str(exc)))
        recommendation_patch = asdict(self._config)
        for key in (
            "openbiliclaw_enabled",
            "openbiliclaw_host",
            "openbiliclaw_port",
            "openbiliclaw_backend_port",
        ):
            recommendation_patch.pop(key, None)
        current_sources = {
            "web_search": recommendation_patch.pop("web_search"),
            "bilibili": recommendation_patch.pop("bilibili"),
        }
        source_updates = {
            key: updates.pop(key)
            for key in ("web_search", "bilibili")
            if key in updates
        }
        current_sources.update(source_updates)
        recommendation_patch.update(updates)
        recommendation_patch["sources"] = current_sources
        compat_updates = {}
        if "openbiliclaw_enabled" in recommendation_patch:
            compat_updates["enabled"] = recommendation_patch.pop(
                "openbiliclaw_enabled"
            )
        if "openbiliclaw_port" in recommendation_patch:
            compat_updates["port"] = recommendation_patch.pop("openbiliclaw_port")
        if "openbiliclaw_backend_port" in recommendation_patch:
            compat_updates["backend_port"] = recommendation_patch.pop(
                "openbiliclaw_backend_port"
            )
        try:
            config_patch: dict[str, Any] = {"recommendation": recommendation_patch}
            if compat_updates:
                config_patch["openbiliclaw"] = compat_updates
            payload = await self.ctx.update_own_config(config_patch)
            await self._load_config()
            await self._sync_compat_server()
        except Exception as exc:
            return Err(SdkError(f"settings update failed: {type(exc).__name__}"))
        return Ok(
            {
                "updated": True,
                "persisted": (
                    payload.get("persisted", True)
                    if isinstance(payload, Mapping)
                    else True
                ),
                "config": asdict(self._config),
            }
        )

    @timer_interval(id="recommendation_cycle", seconds=60, auto_start=True)
    async def recommendation_cycle(self, **_: Any):
        if self._stopping.is_set():
            return Ok({"skipped": "plugin_stopping"})
        # Timer callbacks can overlap when a network source is slow.
        if not self._cycle_lock.acquire(blocking=False):
            return Ok({"skipped": "cycle_running"})
        try:
            if self._stopping.is_set():
                return Ok({"skipped": "plugin_stopping"})
            if not await self._ensure_ready():
                return Ok({"skipped": "plugin_initializing"})
            return Ok(await self._run_cycle())
        except Exception as exc:
            self.logger.exception("recommendation cycle failed")
            return Err(SdkError(f"recommendation cycle failed: {type(exc).__name__}"))
        finally:
            self._cycle_lock.release()

    @plugin_entry(
        id="recommendation_status",
        name=tr("entries.status.name", default="推荐状态"),
        description=tr(
            "entries.status.description",
            default="查看个性化主动推荐状态，不返回用户原始对话。",
        ),
    )
    async def recommendation_status(self, **kwargs: Any):
        await self._ensure_ready()
        snapshot = self._state.snapshot()
        interests = active_interests(snapshot.get("profile"), include_trial=True)
        return Ok(
            {
                "enabled": self._config.enabled,
                "shadow_mode": self._config.shadow_mode,
                "interests": [
                    {
                        "name": item.get("name"),
                        "weight": item.get("weight"),
                        "status": item.get("status"),
                    }
                    for item in interests[:12]
                ],
                "candidate_count": len(snapshot.get("candidates", [])),
                "openbiliclaw": self._dashboard_state().get("openbiliclaw", {}),
                "history": snapshot.get("history", [])[-10:],
            }
        )

    @ui.action(
        id="recommendation_run_once",
        label=tr("actions.runOnce.label", default="立即检查一次"),
        tone="primary",
        group="runtime",
        order=20,
        refresh_context=True,
    )
    @plugin_entry(
        id="recommendation_run_once",
        name=tr("entries.run_once.name", default="运行一次推荐周期"),
        description=tr(
            "entries.run_once.description",
            default="立即执行一次画像、候选发现和门控；仍遵守 enabled、shadow_mode 与所有安全限制。",
        ),
    )
    async def recommendation_run_once(self, **_: Any):
        if not self._cycle_lock.acquire(blocking=False):
            return Ok({"skipped": "cycle_running"})
        try:
            if not await self._ensure_ready():
                return Ok({"skipped": "plugin_initializing"})
            return Ok(await self._run_cycle(force_discovery=True))
        finally:
            self._cycle_lock.release()

    @plugin_entry(
        id="recommendation_feedback",
        name=tr("entries.feedback.name", default="推荐反馈"),
        description=tr(
            "entries.feedback.description",
            default="将一次推荐标记为喜欢、不喜欢或忽略。",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "candidate_id": {"type": "string"},
                "outcome": {
                    "type": "string",
                    "enum": ["engaged", "rejected", "ignored"],
                },
            },
            "required": ["outcome"],
        },
    )
    async def recommendation_feedback(
        self, outcome: str, candidate_id: str = "", **_: Any
    ):
        if outcome not in {"engaged", "rejected", "ignored"}:
            return Err(SdkError("outcome must be engaged, rejected, or ignored"))
        changed = False

        def mutate(state: dict[str, Any]) -> None:
            nonlocal changed
            before = [dict(item) for item in state.get("history", [])]
            for item in reversed(state.get("history", [])):
                if item.get("mode") != "live":
                    continue
                if candidate_id and str(item.get("candidate_id")) != candidate_id:
                    continue
                item["outcome"] = outcome
                item["settled_at"] = time.time()
                changed = True
                break
            if changed:
                state["profile"] = apply_feedback_to_profile(
                    state.get("profile", {}),
                    before,
                    state.get("history", []),
                    now=time.time(),
                )

        await self._state.update(mutate)
        return Ok({"updated": changed, "outcome": outcome})


__all__ = ["ProactiveRecommenderPlugin"]
