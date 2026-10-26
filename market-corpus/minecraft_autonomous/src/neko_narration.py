"""Thin public host voice transport for the framework's optional speech outbox."""

import asyncio
import sqlite3

from agent_mc_autonomy.configuration import Settings, _boolean, _integer
from agent_mc_autonomy.death_memory import scope_key
from agent_mc_autonomy.host_context import HostContextPort
from agent_mc_autonomy.narration import NarrationPort
from plugin.sdk.plugin import Ok, TransportError, timer_interval

from .neko_heartbeat import HeartbeatSettings, is_stopped, review_port
from .neko_host_context import HostContextSettings, character_profile
from .neko_recent_context import owned_runtime
from .neko_speech import enqueue_speech, output_settings


class NekoNarration:
    @timer_interval(id="native_planner_voice", seconds=2, name="转发自主游玩台词",
                    description="独立语音线程转发同次规划台词并等待有界回执，不调用模型，不阻塞或调度游戏。")
    async def native_planner_voice(self, **_):
        try:
            result = await self._native_planner_voice()
            if result.get("submitted"):
                # Official timer handlers run in their own asyncio.run loop.
                # Keep THIS timer's delivery alive until its bounded completion;
                # the independent body and other SDK threads keep advancing.
                loop = asyncio.get_running_loop()
                owned = [task for task in tuple(getattr(self, "_speech_output_tasks", ())) if task.get_loop() is loop]
                if owned:
                    await asyncio.gather(*owned)
            return Ok(result)
        except (OSError, sqlite3.Error, ValueError, TypeError, KeyError, TransportError):
            # No provider error body, key, profile or spoken line enters logs.
            return Ok({"submitted": False, "reason": "voice_context_unavailable"})

    async def _native_planner_voice(self):
        with self._control_lock:
            generation = self._generation
            if self._configuration_writes or is_stopped(self):
                return {"submitted": False, "reason": "stopped_or_configuring"}
        raw = await self.config.dump()
        section = raw.get("speech_output", {})
        if not _boolean(section, "enabled", False) or not _boolean(section, "planner_narration", True):
            return {"submitted": False, "reason": "disabled"}
        settings = Settings.parse(raw)
        review = review_port(self, settings)
        target = HeartbeatSettings.parse(raw).target
        # Validate the explicit origin before deriving its public character URL.
        base = output_settings(raw, target or "current_character")
        profile_settings = HostContextSettings.parse(raw)
        if not profile_settings.enabled:
            return {"submitted": False, "reason": "persona_sharing_disabled"}
        profile_settings = HostContextSettings(True, profile_settings.character_api_url or base + "/api/characters")
        # Never query the host when there is no owned, fresh body to narrate.
        if owned_runtime(self, settings, target or "current_character", review, generation) is None:
            return {"submitted": False, "reason": "runtime_not_owned"}
        profile = await character_profile(self, profile_settings, target, timeout=0.5, allow_current=True)
        target = profile.get("agent_name")
        binding = owned_runtime(self, settings, target, review, generation)
        if not profile or binding is None:
            return {"submitted": False, "reason": "character_not_bound"}
        with self._control_lock:
            if owned_runtime(self, settings, target, review, generation) != binding:
                return {"submitted": False, "reason": "binding_changed"}
            HostContextPort(review.directory, now=review.now).publish_static(profile, scope=scope_key(binding["scope"]))
            port = NarrationPort(review.directory, now=review.now)
            if not port.subscribe(binding, interval=_integer(section, "min_interval_seconds", 30, 10, 900)):
                return {"submitted": False, "reason": "subscription_unavailable"}
            if getattr(self, "_speech_output_tasks", None):
                return {"submitted": False, "reason": "busy"}
            claimed = port.take(binding)
            if claimed is None:
                return {"submitted": False, "reason": "no_new_line"}
            line = claimed.pop("line")
            result = enqueue_speech(self, raw, settings, target, port, generation, line, claimed, source_binding=binding)
            return {"submitted": result.get("status") == "enqueued", "speech": result, "extra_model_calls": 0}
