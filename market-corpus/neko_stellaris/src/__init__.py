# -*- coding: utf-8 -*-
# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

"""Stellaris integration for N.E.K.O.

Two deliverables that answer two different halves of "let the character be my
Stellaris advisor":

**In-game advisor voice (`neko_stellaris_advisor`)** -- a real Stellaris mod
whose sound group is a complete Chinese re-recording of the advisor, rendered
with the user's own N.E.K.O voice. The game decides *when* to speak, so the
event timing is exact; the lines themselves are pre-rendered, which is the only
thing the engine permits. Variety comes from pooled variants per event.

**Live commentary (`stellaris_live`)** -- the genuinely real-time half. The
plugin tails ``logs/game.log`` (UTF-8, and the base game already writes an
event-selection line for everything the player is offered), filters it through a
throttling policy, and pushes a prompt so the character says something *about
what just happened*, in her own voice, through N.E.K.O's audio.

The split is not a shortcut. Stellaris cannot accept a new sound at runtime:
Clausewitz picks a registered sound name at the moment an event fires, mods
cannot read external files, and there is no inbound channel at all. So the only
way to get both "in the game" and "generated right now" is to put each half
where it can actually work.

See ``README.md`` in this directory for the operating guide and the known
limitations.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from plugin.sdk.plugin import (
    Err,
    NekoPluginBase,
    Ok,
    SdkError,
    lifecycle,
    neko_plugin,
    plugin_entry,
    ui,
)

from .core import bridge as bridge_core
from .core import combined as combined_core
from .core import gamelog, installer, livevoice, nekoconfig, voicepack
from .core.policy import CommentaryPolicy, Decision, PolicyConfig, build_prompt
from .core.tts import TtsSettings, count_cache_misses
from .core.tts import probe as tts_probe

ADVISOR_MOD_ID = voicepack.DEFAULT_MOD_ID
BRIDGE_MOD_ID = bridge_core.DEFAULT_MOD_ID

# Entry-level timeouts. Building a full pack is minutes of TTS, so the entry
# returns a job id instead of blocking; these bound the *synchronous* work only.
PLAN_TIMEOUT_S = 60.0


@dataclass
class Settings:
    """Runtime settings, resolved from plugin metadata, env, then defaults.

    Deliberately not read through ``PluginConfig``: that API's field layout is
    host-managed, and getting it wrong here would be a silent misconfiguration
    of something that spends real API calls. Metadata + env are checkable at
    startup and the resolution is reported in ``stellaris_status``.
    """

    voice_id: str = ""
    tts_url: str = ""
    tts_api_key: str = ""
    region: str = "cn"

    live_enabled: bool = False
    poll_interval_s: float = 2.0
    urgent_cooldown_s: float = 45.0
    digest_interval_s: float = 120.0
    max_per_minute: int = 2
    min_gap_s: float = 12.0
    ignore_ai_events: bool = True

    variants: int = voicepack.DEFAULT_VARIANTS
    auto_install: bool = True

    live_voice_enabled: bool = True
    live_model_usage: str = "analysis"
    live_budget_per_minute: int = 4
    live_min_seconds_between: float = 8.0

    # Startup self-check: does the pack in the game match the voice N.E.K.O is
    # using right now? Switching voice silently leaves the old one in game, so
    # the check exists to say so.
    check_on_startup: bool = True
    # Rebuild automatically when the check finds a difference. The work runs in
    # the background and only misses the cache, so a one-line edit is seconds and
    # a voice change is minutes.
    auto_refresh_voice: bool = True

    sources: dict[str, str] = field(default_factory=dict)

    def policy(self) -> PolicyConfig:
        return PolicyConfig(
            urgent_cooldown_s=self.urgent_cooldown_s,
            digest_interval_s=self.digest_interval_s,
            min_gap_s=self.min_gap_s,
            max_per_minute=self.max_per_minute,
            ignore_ai_events=self.ignore_ai_events,
        )


@dataclass
class Job:
    """A background unit of work the entries can start and poll."""

    id: str
    label: str
    state: str = "running"          # running | done | failed
    done: int = 0
    total: int = 0
    phase: str = ""
    result: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    started_at: float = field(default_factory=time.time)
    ended_at: float = 0.0
    task: "asyncio.Task[Any] | None" = None

    def payload(self) -> dict[str, Any]:
        return {
            "job_id": self.id,
            "label": self.label,
            "state": self.state,
            "phase": self.phase,
            "progress": f"{self.done}/{self.total}" if self.total else "",
            "percent": (round(100.0 * self.done / self.total) if self.total else 0),
            "result": self.result,
            "error": self.error,
            "elapsed_s": round((self.ended_at or time.time()) - self.started_at, 1),
        }


# Error lines this plugin has learned to care about. Counted for the panel's
# "is the current build healthy in the real game" card -- the same four greps the
# release checklist asks for, so the checklist and the UI cannot drift.
_ERROR_PATTERNS = {
    "bridge_events": "neko_bridge_events",
    "context_switch": "Invalid context switch",
    "missing_sound": "No sound scripted",
    "unsupported_scope": "unsupported scope",
}
_LOG_TAIL_BYTES = 4 * 1024 * 1024
_LOG_HEALTH_CACHE: dict[str, Any] = {}


def _log_health(user: Path | None) -> dict[str, Any]:
    """Count those lines in the tail of ``logs/error.log``.

    Reads only the tail: the file grows to tens of megabytes and the interesting
    part is always the session just played. Cached on (mtime, size) so a panel that
    refreshes every couple of seconds does not rescan it.
    """
    if user is None:
        return {"available": False, "path": ""}
    path = Path(user) / "logs" / "error.log"
    try:
        stat = path.stat()
    except OSError:
        return {"available": False, "path": str(path)}
    key = f"{path}|{stat.st_mtime_ns}|{stat.st_size}"
    if _LOG_HEALTH_CACHE.get("key") == key:
        return _LOG_HEALTH_CACHE["value"]
    try:
        with path.open("rb") as handle:
            handle.seek(max(0, stat.st_size - _LOG_TAIL_BYTES))
            text = handle.read().decode("utf-8", errors="replace")
    except OSError:
        return {"available": False, "path": str(path)}
    counts = {name: text.count(needle) for name, needle in _ERROR_PATTERNS.items()}
    value = {
        "available": True,
        "path": str(path),
        "size_kib": round(stat.st_size / 1024),
        "counts": counts,
        "clean": not any(counts.values()),
    }
    _LOG_HEALTH_CACHE.clear()
    _LOG_HEALTH_CACHE.update({"key": key, "value": value})
    return value


@neko_plugin
class NekoStellarisPlugin(NekoPluginBase):
    """Advisor voice-pack builder plus a live game-log commentator."""

    def __init__(self, ctx: Any):
        super().__init__(ctx)
        self.logger = ctx.logger
        self._jobs: dict[str, Job] = {}
        self._job_seq = 0
        self._live_task: asyncio.Task[Any] | None = None
        self._live_stop = asyncio.Event()
        self._live_stats: dict[str, Any] = {"state": "stopped"}
        self._live_engine: livevoice.LiveVoiceEngine | None = None
        self._live_engine_error = ""
        self._refresh_job: Job | None = None
        # Which copy of the pack the game is loading. Not always the one this
        # plugin wrote: publishing to the Workshop makes the launcher replace the
        # local install with the subscribed copy.
        self._active_install: installer.ActiveInstall | None = None
        self._settings = Settings()

    # ------------------------------------------------------------------ #
    # lifecycle
    # ------------------------------------------------------------------ #

    @lifecycle(id="startup")
    async def on_startup(self, **_):
        try:
            self._settings = self._resolve_settings()
        except Exception as exc:  # noqa: BLE001 - startup must not hard-fail
            self.logger.warning("读取配置失败，使用默认值: %s", exc)
            self._settings = Settings()

        self.logger.info("N.E.K.O Stellaris 插件就绪（%s）", self._describe_env())
        if self._settings.live_enabled:
            self._start_live()
        else:
            self.logger.info("实时解说未启用（live_enabled=false）")

        freshness = None
        if self._settings.check_on_startup:
            try:
                freshness = self._voice_freshness()
                self.logger.info("语音包自检：%s", freshness.summary())
                blocked, why = installer.published_copy_blocks_install(
                    self._stantis_user_dir(), mod_id=ADVISOR_MOD_ID)
                if freshness.needs_rebuild and self._settings.auto_refresh_voice:
                    if blocked:
                        # Rebuilding here would write a copy the game does not
                        # read while the published one stays stale -- and it would
                        # repeat on every startup, because the thing being
                        # compared never changes. Say so once instead.
                        self.logger.info("跳过自动重建：%s", why)
                        self.push_message(
                            visibility=["hud"], ai_behavior="blind",
                            parts=[{"type": "text",
                                    "text": f"顾问语音包需要更新，但游戏里生效的是创意工坊副本，"
                                            f"本地不会再装一份：{freshness.summary()}。"
                                            f"要更新它请重新上传工坊版本。"}],
                            source="stellaris_voice", priority=4)
                    else:
                        self._start_refresh(freshness)
                elif freshness.needs_rebuild:
                    self.push_message(
                        visibility=["hud"], ai_behavior="blind",
                        parts=[{"type": "text",
                                "text": f"顾问语音包需要更新：{freshness.summary()}。"
                                        f"运行 stellaris_voice_check 可重建。"}],
                        source="stellaris_voice", priority=4)
            except Exception as exc:  # noqa: BLE001 - a check must not block startup
                self.logger.warning("语音包自检失败: %s", exc)

        return Ok({
            "status": "ready",
            "live": self._live_stats.get("state", "stopped"),
            "voice_pack": freshness.summary() if freshness else "未检查",
        })

    @lifecycle(id="shutdown")
    async def on_shutdown(self, **_):
        await self._stop_live()
        for job in self._jobs.values():
            if job.task and not job.task.done():
                job.task.cancel()
        self.logger.info("N.E.K.O Stellaris 插件已停止")
        return Ok({"status": "stopped"})

    @lifecycle(id="config_change")
    async def on_config_change(self, **_):
        try:
            self._settings = self._resolve_settings()
        except Exception as exc:  # noqa: BLE001
            self.logger.warning("配置变更读取失败: %s", exc)
        return Ok({"status": "reloaded", **self._settings.sources})

    # ------------------------------------------------------------------ #
    # settings
    # ------------------------------------------------------------------ #

    def _toml_section(self) -> dict[str, Any]:
        """Read ``[stellaris]`` straight out of plugin.toml.

        ``self.metadata`` is host-managed and may or may not carry arbitrary
        sections, so the file is read directly with ``tomllib`` (3.11 stdlib)
        and cached. A missing or malformed file degrades to metadata + env
        rather than failing startup.
        """
        cached = getattr(self, "_toml_cache", None)
        if cached is not None:
            return cached
        section: dict[str, Any] = {}
        try:
            import tomllib

            path = self._plugin_root() / "plugin.toml"
            data = tomllib.loads(path.read_text(encoding="utf-8"))
            candidate = data.get("stellaris")
            if isinstance(candidate, dict):
                section = candidate
        except Exception as exc:  # noqa: BLE001
            self.logger.debug("读取 plugin.toml [stellaris] 失败: %s", exc)
        self._toml_cache = section
        return section

    def _setting(self, name: str, default: Any) -> Any:
        """plugin.toml, then metadata, then env, then the code default.

        Records which source won so ``stellaris_status`` can show it; a setting
        that silently did not apply is the most expensive kind of bug here.
        """
        meta = self.metadata
        meta_section = meta.get("stellaris") if isinstance(meta.get("stellaris"), dict) else {}
        for source, value in (
            ("plugin.toml", self._toml_section().get(name)),
            ("metadata", meta_section.get(name) if isinstance(meta_section, dict) else None),
            ("metadata", meta.get(f"stellaris_{name}")),
            ("env", os.environ.get(f"NEKO_STELLARIS_{name.upper()}")),
        ):
            if value is None or (isinstance(value, str) and not value.strip()):
                continue
            if isinstance(default, bool):
                text = str(value).strip().lower()
                parsed = text in ("1", "true", "yes", "on")
            elif isinstance(default, int) and not isinstance(default, bool):
                parsed = int(value)
            elif isinstance(default, float):
                parsed = float(value)
            else:
                parsed = str(value).strip()
            self._settings.sources[name] = source
            return parsed
        self._settings.sources[name] = "default"
        return default

    def _resolve_settings(self) -> Settings:
        """Build a :class:`Settings` from plugin.toml > metadata > env > defaults.

        Defaults are read off the dataclass rather than repeated as literals here.
        The earlier version restated them, and the two copies promptly drifted:
        changing ``PolicyConfig.urgent_cooldown_s`` to 45 left this file asking for
        30, so a host without ``[stellaris]`` in plugin.toml got the old policy.
        """
        defaults = Settings()
        settings = Settings()
        self._settings = settings  # _setting() records the winning source onto it
        for field_name, default in (
            ("voice_id", defaults.voice_id),
            ("tts_url", defaults.tts_url),
            ("tts_api_key", defaults.tts_api_key),
            ("region", defaults.region),
            ("live_enabled", defaults.live_enabled),
            ("poll_interval_s", defaults.poll_interval_s),
            ("urgent_cooldown_s", defaults.urgent_cooldown_s),
            ("digest_interval_s", defaults.digest_interval_s),
            ("max_per_minute", defaults.max_per_minute),
            ("min_gap_s", defaults.min_gap_s),
            ("ignore_ai_events", defaults.ignore_ai_events),
            ("variants", defaults.variants),
            ("auto_install", defaults.auto_install),
            ("live_voice_enabled", defaults.live_voice_enabled),
            ("live_model_usage", defaults.live_model_usage),
            ("live_budget_per_minute", defaults.live_budget_per_minute),
            ("live_min_seconds_between", defaults.live_min_seconds_between),
            ("check_on_startup", defaults.check_on_startup),
            ("auto_refresh_voice", defaults.auto_refresh_voice),
        ):
            setattr(settings, field_name, self._setting(field_name, default))
        return settings

    def _resolved_voice(self, override: str = "") -> nekoconfig.NekoSettings:
        return nekoconfig.load_settings(
            voice_override=override or self._settings.voice_id,
            url_override=self._settings.tts_url,
            key_override=self._settings.tts_api_key,
            region=self._settings.region,
        )

    def _tts_settings(self, override: str = "") -> TtsSettings:
        resolved = self._resolved_voice(override)
        return TtsSettings(
            url=resolved.url,
            api_key=resolved.api_key,
            voice_id=resolved.voice_id,
            concurrency=2,
        )

    def _describe_env(self) -> str:
        user = nekoconfig.find_stellaris_user_dir()
        return f"Stellaris 用户目录={'有' if user else '未找到'}，变体上限={self._settings.variants}"

    # ------------------------------------------------------------------ #
    # paths
    # ------------------------------------------------------------------ #

    def _plugin_root(self) -> Path:
        """Directory holding ``plugin.toml`` and ``data/``.

        ``self.plugin_dir`` is the parent of ``ctx.config_path``, which is the
        install directory in production. When that does not actually contain our
        files -- a development tree loaded with an unexpected ``config_path``, or
        a harness that only emulates part of the host -- fall back to the
        directory this module lives in, which is always right.
        """
        declared = Path(self.plugin_dir)
        if (declared / "data").is_dir() or (declared / "plugin.toml").is_file():
            return declared
        fallback = Path(__file__).resolve().parent
        self.logger.debug("plugin_dir=%s 不含本插件文件，回退到 %s", declared, fallback)
        return fallback

    def _dataset_path(self, name: str) -> Path:
        bundled = self._plugin_root() / "data" / name
        if bundled.is_file():
            return bundled
        raise SdkError(f"缺少内置数据集 {name}（插件安装不完整？）")

    def _stantis_user_dir(self) -> Path:
        user = nekoconfig.find_stellaris_user_dir()
        if not user:
            raise SdkError("未找到 Stellaris 用户目录（Documents/Paradox Interactive/Stellaris）")
        return user

    def _new_job(self, label: str, total: int = 0) -> Job:
        self._job_seq += 1
        job = Job(id=f"j{self._job_seq}", label=label, total=total)
        self._jobs[job.id] = job
        # Keep the registry bounded: the entries are polled by id, and a stalled
        # reader should not leak one Job per build forever.
        if len(self._jobs) > 32:
            for key in sorted(self._jobs)[:-32]:
                if self._jobs[key].state != "running":
                    self._jobs.pop(key, None)
        return job

    # ------------------------------------------------------------------ #
    # entries: diagnostics
    # ------------------------------------------------------------------ #

    # ------------------------------------------------------------------ #
    # voice-pack freshness
    # ------------------------------------------------------------------ #

    def _voice_freshness(self) -> voicepack.Freshness:
        """Compare the installed pack against the current voice and sources."""
        plan, spec, dataset, lines = self._load_plan_inputs(self._settings.variants)
        tts = self._tts_settings()
        # Resolve which copy the game loads before comparing anything: after the
        # launcher migrates a published mod, the old local path is gone, and
        # checking it answers "not installed" about a mod that is running right
        # now -- which then triggers an auto-install of a second copy.
        active = installer.find_active_install(self._stantis_user_dir(),
                                               mod_id=ADVISOR_MOD_ID)
        self._active_install = active
        mod_dir = (active.path if active is not None
                   else self._stantis_user_dir() / "mod" / ADVISOR_MOD_ID)
        fresh = voicepack.check_freshness(
            mod_dir, spec, dataset, lines, tts,
            cache_dir=nekoconfig.tts_cache_dir())

        # A pack installed before the two mods were merged is voice-only: its
        # audio is perfectly current, but the event bridge is missing, so live
        # commentary silently loses every detail the bridge supplies. A published
        # copy is exempt: what it contains is a decision, not drift, and nagging
        # about it would push the author to "fix" a shipped artefact locally.
        if active is None or not active.is_published:
            gap = combined_core.combined_stamp_is_stale(mod_dir)
            if gap and fresh.state == "fresh":
                return voicepack.Freshness(
                    "missing_bridge", [gap], fresh.installed, fresh.current,
                    fresh.current.clip_count if fresh.current else 0)
        return fresh

    def _start_refresh(self, freshness: voicepack.Freshness) -> None:
        """Kick off a background rebuild. Returns immediately."""
        if self._refresh_job is not None and self._refresh_job.state == "running":
            self.logger.info("语音包重建已在进行中，跳过")
            return
        plan, _spec, _ds, _ln = self._load_plan_inputs(self._settings.variants)
        job = self._new_job("语音包自检重建", total=plan.clip_count)
        self._refresh_job = job
        self.logger.info("开始重建语音包（%s，需合成 %d 条）",
                         freshness.state, freshness.clips_to_generate)
        self.push_message(
            visibility=["hud"], ai_behavior="blind",
            parts=[{"type": "text",
                    "text": f"游戏里的顾问语音包已过期，正在后台重建：{freshness.summary()}"}],
            source="stellaris_voice", priority=4)
        job.task = asyncio.create_task(self._run_voice_build(
            job, plan, self._tts_settings(), self.data_path("pack"),
            nekoconfig.tts_cache_dir(), True))

    @ui.action(label="检查游戏里那份是否最新", icon="🔍", group="build", order=20)
    @plugin_entry(
        id="stellaris_voice_check",
        name="检查并刷新顾问语音包",
        description=("对比游戏里已安装的语音包与 N.E.K.O 当前音色/台词表：换了音色或改了台词就会不一致。"
                     "action=check 只报告；refresh 直接重建并安装。"),
        input_schema={
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["check", "refresh"], "default": "check"},
            },
        },
        timeout=PLAN_TIMEOUT_S,
    )
    async def stellaris_voice_check(self, action: str = "check", **_):
        action = str(action or "check").strip().lower()
        if action == "refresh":
            job = self._new_job("手动重建语音包")
            plan, _spec, _ds, _ln = self._load_plan_inputs(self._settings.variants)
            job.total = plan.clip_count
            self._refresh_job = job
            job.task = asyncio.create_task(self._run_voice_build(
                job, plan, self._tts_settings(), self.data_path("pack"),
                nekoconfig.tts_cache_dir(), True))
            return Ok({**job.payload(),
                       "note": "已开始后台重建，用 stellaris_status 查看进度"})
        try:
            fresh = self._voice_freshness()
        except Exception as exc:  # noqa: BLE001
            return Err(SdkError(f"自检失败: {exc}"))
        return Ok({
            "state": fresh.state,
            "summary": fresh.summary(),
            "needs_rebuild": fresh.needs_rebuild,
            "clips_to_generate": fresh.clips_to_generate,
            "installed_voice": (fresh.installed.voice_id if fresh.installed else ""),
            "current_voice": (fresh.current.voice_id if fresh.current else ""),
            "reasons": fresh.reasons,
        })

    @plugin_entry(
        id="stellaris_status",
        name="群星集成状态",
        description="查看语音包与桥接 mod 的安装状态、实时解说状态、TTS 通道与路径解析结果。",
        input_schema={"type": "object", "properties": {}},
    )
    async def stellaris_status(self, **_):
        try:
            user = nekoconfig.find_stellaris_user_dir()
        except Exception:  # noqa: BLE001
            user = None
        resolved = self._resolved_voice()

        advisor = (installer.inspect_installation(user, mod_id=ADVISOR_MOD_ID)
                   if user else None)
        bridge = (installer.inspect_installation(user, mod_id=BRIDGE_MOD_ID)
                  if user else None)

        return Ok({
            "stellaris_user_dir": str(user) if user else "",
            "stellaris_install_dir": str(nekoconfig.find_stellaris_install_dir() or ""),
            "advisor_mod": advisor.summary() if advisor else "（未找到 Stellaris 目录）",
            "bridge_mod": bridge.summary() if bridge else "（未找到 Stellaris 目录）",
            "voice": {
                "voice_id": resolved.voice_id,
                "resolved_from": resolved.voice_source,
                "tts_url": resolved.url,
                "key_set": bool(resolved.api_key),
            },
            "game_log": (gamelog.describe_log(user) if user else "（未找到 Stellaris 目录）"),
            "live": dict(self._live_stats),
            "jobs": [job.payload() for job in self._jobs.values()][-5:],
            "settings_source": dict(self._settings.sources),
        })

    # ------------------------------------------------------------------ #
    # Hosted UI (面板)
    # ------------------------------------------------------------------ #

    @ui.context(id="dashboard", title="星辰喵语")
    async def get_dashboard_ui_context(self) -> dict[str, Any]:
        """State for the Hosted UI panel declared in ``ui/panel.tsx``.

        Composed from the same helpers the entries use, so the panel and
        ``stellaris_status`` cannot disagree about what is installed.
        """
        try:
            return await self._dashboard_state()
        except Exception as exc:  # noqa: BLE001 - a broken panel must not break the plugin
            self.logger.warning("面板状态构建失败: %s", exc)
            return {"error": str(exc)}

    async def _dashboard_state(self) -> dict[str, Any]:
        try:
            user = nekoconfig.find_stellaris_user_dir()
        except Exception:  # noqa: BLE001
            user = None
        plan, spec, _dataset, _lines = self._load_plan_inputs(self._settings.variants)
        resolved = self._resolved_voice()
        tts = self._tts_settings()

        active = installer.find_active_install(user, mod_id=ADVISOR_MOD_ID) if user else None
        published = installer.find_published_copy(user, mod_id=ADVISOR_MOD_ID) if user else None
        local_dir = (Path(user) / "mod" / ADVISOR_MOD_ID) if user else None
        local_exists = bool(local_dir and local_dir.is_dir())

        enabled: list[str] = []
        dlc_load = (Path(user) / "dlc_load.json") if user else None
        if dlc_load is not None and dlc_load.is_file():
            try:
                enabled = list(json.loads(dlc_load.read_text(encoding="utf-8"))
                               .get("enabled_mods") or [])
            except Exception:  # noqa: BLE001
                enabled = []
        entries = {f"mod/{ADVISOR_MOD_ID}.mod"}
        if published is not None:
            entries.add(f"mod/{published.stub.name}")
        enabled_ours = sorted(e for e in enabled if e in entries)

        # Two copies of the same mod is the one state that quietly breaks the game:
        # the bridge's on_action hooks are appended once per copy, so every bridged
        # event fires twice.
        duplicate = {
            "both_present": bool(local_exists and published is not None),
            "enabled": enabled_ours,
            "both_enabled": len(enabled_ours) > 1,
        }

        jobs = [job.payload() for job in self._jobs.values()]
        running = next((j for j in reversed(jobs) if j.get("state") == "running"), None)

        try:
            fresh = self._voice_freshness()
            freshness = {
                "state": fresh.state,
                "summary": fresh.summary(),
                "needs_rebuild": fresh.needs_rebuild,
                "clips_to_generate": fresh.clips_to_generate,
            }
        except Exception as exc:  # noqa: BLE001
            freshness = {"state": "unknown", "summary": str(exc), "needs_rebuild": False,
                         "clips_to_generate": 0}

        clips = [job.sound for job in voicepack.clip_jobs(plan)]
        cache_dir = nekoconfig.tts_cache_dir()
        try:
            missing = count_cache_misses([(j.sound, j.text) for j in voicepack.clip_jobs(plan)],
                                         tts, cache_dir)
        except Exception:  # noqa: BLE001 - a panel must still render
            missing = 0

        install_state: dict[str, Any] = {}
        if active is not None:
            info = installer.inspect_installation(user, mod_id=ADVISOR_MOD_ID) if user else None
            install_state = {
                "kind": active.kind,
                "path": str(active.path),
                "remote_id": active.remote_id,
                "version": info.version if info else "",
                "files": info.file_count if info else 0,
                "size_mib": round((info.bytes_total if info else 0) / 1048576, 1),
                "clips": info.clip_count if info else 0,
                "registered": bool(info.registered) if info else False,
                "summary": info.summary() if info else "",
            }

        return {
            "mod_id": ADVISOR_MOD_ID,
            "version": spec.mod_version,
            "user_dir": str(user) if user else "",
            "install": install_state,
            "freshness": freshness,
            "published": ({"remote_id": published.remote_id, "path": str(published.path)}
                          if published is not None else {}),
            "duplicate": duplicate,
            "clips": {
                "total": len(clips),
                "cached": max(0, len(clips) - missing),
                "missing": missing,
                "title": f"{plan.slot_count} 个声音 / {plan.clip_count} 条录音",
            },
            "tts": {
                "url": resolved.url,
                "voice_id": resolved.voice_id,
                "source": resolved.voice_source,
                "key_set": bool(resolved.api_key),
            },
            "live": dict(self._live_stats),
            "jobs": jobs[-5:],
            "running_job": running,
            "log_health": _log_health(user),
            "errors": list(plan.warnings),
        }

    @ui.action(label="试听一句", icon="🔊", group="test", order=30, refresh_context=False)
    @plugin_entry(
        id="stellaris_tts_probe",
        name="测试语音通道",
        description="用一句样例文本真实合成一次，验证当前音色与 TTS 通道可用。",
        input_schema={
            "type": "object",
            "properties": {
                "text": {"type": "string", "default": "你好呀，我是兰兰。"},
                "voice_id": {"type": "string", "default": ""},
            },
        },
    )
    async def stellaris_tts_probe(self, text: str = "你好呀，我是兰兰。", voice_id: str = "", **_):
        try:
            outcome = await asyncio.wait_for(
                tts_probe(self._tts_settings(voice_id), text), timeout=45.0)
        except Exception as exc:  # noqa: BLE001
            return Err(SdkError(f"语音通道不可用: {exc}"))
        return Ok({"ok": True, "detail": outcome})

    @plugin_entry(
        id="stellaris_list_voices",
        name="列出可用音色",
        description="列出 N.E.K.O 免费预设音色，以及当前实际会使用的音色。",
        input_schema={"type": "object", "properties": {}},
    )
    async def stellaris_list_voices(self, **_):
        resolved = self._resolved_voice()
        return Ok({
            "current": resolved.voice_id,
            "current_source": resolved.voice_source,
            "presets": nekoconfig.list_voices(),
        })

    # ------------------------------------------------------------------ #
    # entries: voice pack
    # ------------------------------------------------------------------ #

    def _load_plan_inputs(self, variants: int):
        """Return (plan, spec, dataset, lines).

        The dataset and line table come back too because the freshness stamp is
        a hash of them: without the inputs, a pack cannot be checked against the
        sources it claims to have been built from.
        """
        dataset = voicepack.load_dataset(self._dataset_path("advisor_sounds.json"))
        lines = voicepack.load_lines(self._dataset_path("lines.zh-CN.json"))
        spec = voicepack.PackSpec(max_variants=variants)
        return voicepack.plan_pack(dataset, lines, spec), spec, dataset, lines

    @plugin_entry(
        id="stellaris_voice_plan",
        name="预览语音包计划",
        description="不合成、不写盘，只报告将要生成多少条语音、覆盖哪些事件、有哪些警告。",
        input_schema={
            "type": "object",
            "properties": {
                "variants": {"type": "integer", "default": 0,
                             "description": "0 表示用预设的按组分档策略"},
            },
        },
        timeout=PLAN_TIMEOUT_S,
    )
    async def stellaris_voice_plan(self, variants: int = 0, **_):
        try:
            plan, _spec, _ds, _ln = self._load_plan_inputs(int(variants) or voicepack.DEFAULT_VARIANTS)
        except Exception as exc:  # noqa: BLE001
            return Err(SdkError(f"规划失败: {exc}"))

        return Ok({
            "summary": plan.summary(),
            "clips": plan.clip_count,
            "by_group": plan.coverage,
            "soundeffect_overrides": len(plan.effects),
            "warnings": plan.warnings,
            "output": str(self.data_path("pack")),
        })

    @ui.action(label="重建语音包", icon="🔨", group="build", order=10)
    @plugin_entry(
        id="stellaris_build_voice",
        name="生成顾问语音包",
        description=("用当前音色合成全部顾问语音并写出 mod 目录。耗时数分钟，"
                     "立即返回 job_id，用 stellaris_status 查看进度。install=true 时顺带安装。"),
        input_schema={
            "type": "object",
            "properties": {
                "variants": {"type": "integer", "default": 0},
                "voice_id": {"type": "string", "default": ""},
                "install": {"type": "boolean", "default": True},
            },
        },
        timeout=PLAN_TIMEOUT_S,
    )
    async def stellaris_build_voice(self, variants: int = 0, voice_id: str = "",
                                    install: bool = True, **_):
        try:
            plan, _spec, _ds, _ln = self._load_plan_inputs(int(variants) or voicepack.DEFAULT_VARIANTS)
        except Exception as exc:  # noqa: BLE001
            return Err(SdkError(f"规划失败: {exc}"))

        tts = self._tts_settings(voice_id)
        if not tts.voice_id:
            return Err(SdkError("没有可用的音色，先用 stellaris_list_voices 挑一个"))

        job = self._new_job("生成顾问语音包", total=plan.clip_count)
        out_dir = self.data_path("pack")
        cache_dir = self.cache_path("tts")
        job.task = asyncio.create_task(
            self._run_voice_build(job, plan, tts, out_dir, cache_dir, bool(install)))
        return Ok({
            **job.payload(),
            "voice_id": tts.voice_id,
            "voice_url": tts.url,
            "clips": plan.clip_count,
            "note": "已开始后台合成，用 stellaris_status 查看进度",
        })

    async def _run_voice_build(self, job: Job, plan: voicepack.PackPlan, tts: TtsSettings,
                               out_dir: Path, cache_dir: Path, do_install: bool) -> None:
        def on_progress(done: int, total: int, label: str) -> None:
            job.done, job.total, job.phase = done, total, f"合成 {label}"
            self.report_status({"status": "processing", "progress": job.payload()["percent"],
                                "message": job.phase})

        def on_phase(message: str) -> None:
            job.phase = message
            self.logger.info("[语音包] %s", message)

        try:
            dataset = voicepack.load_dataset(self._dataset_path("advisor_sounds.json"))
            lines = voicepack.load_lines(self._dataset_path("lines.zh-CN.json"))
            spec = combined_core.CombinedSpec()
            bridge_plan = self._bridge_plan()
            # Built as one mod, not just the audio: installing a voice-only tree
            # over the combined directory would silently drop events/ and
            # common/on_actions/, leaving the live commentary with no details and
            # no error anywhere.
            result = await combined_core.build_combined(
                plan, bridge_plan, out_dir, tts, spec=spec, cache_dir=cache_dir,
                dataset=dataset, lines=lines,
                on_progress=on_progress, on_phase=on_phase)
            job.result["build"] = result.summary()
            job.result["warnings"] = result.voice.plan.warnings
            job.result["clips_written"] = result.voice.clips_written

            if do_install and self._settings.auto_install:
                blocked, why = installer.published_copy_blocks_install(
                    self._stantis_user_dir(), mod_id=ADVISOR_MOD_ID)
                if blocked:
                    # Build, but do not install: the artifact still lands in the
                    # data root so it can be uploaded, while the copy the game
                    # loads stays the single published one.
                    job.result["install"] = "已跳过本地安装（游戏里生效的是创意工坊副本）"
                    job.result["notes"] = [why]
                else:
                    install_result = installer.install_pack(
                        out_dir, self._stantis_user_dir(),
                        mod_id=ADVISOR_MOD_ID,
                        mod_stub_text=combined_core.render_mod_stub(spec),
                        version=spec.mod_version,
                    )
                    job.result["install"] = install_result.summary()
                    job.result["notes"] = install_result.notes
                    job.result["notes"].extend(self._retire_standalone_bridge())

            job.state = "done"
            job.ended_at = time.time()
            self._announce_completion(job)
        except asyncio.CancelledError:
            job.state = "failed"
            job.error = "已取消"
            job.ended_at = time.time()
            raise
        except Exception as exc:  # noqa: BLE001
            job.state = "failed"
            job.error = str(exc)
            job.ended_at = time.time()
            self.logger.error("语音包生成失败: %s", exc, exc_info=True)
            self.push_message(
                visibility=["hud"], ai_behavior="blind",
                parts=[{"type": "text", "text": f"顾问语音包生成失败：{exc}"}],
                source="stellaris_voice", priority=6,
            )

    def _bridge_plan(self) -> bridge_core.BridgePlan:
        hooks = bridge_core.load_hooks(self._dataset_path("on_action_hooks.json"))
        return bridge_core.plan_bridge(
            hooks, bridge_core.BridgeSpec(name_zh=combined_core.DEFAULT_NAME_ZH,
                                          name_en=combined_core.DEFAULT_NAME_EN))

    def _retire_standalone_bridge(self) -> list[str]:
        """Remove the separate bridge mod now that it is part of the main one.

        Two enabled bridges append the same on_actions twice, so every hooked
        event fires and is logged twice. An upgrade must not be able to leave
        both behind, and the user cannot see the duplication from the launcher.
        """
        try:
            retired = installer.uninstall_pack(self._stantis_user_dir(),
                                               mod_id=BRIDGE_MOD_ID)
            return [f"已退掉旧的独立桥接 mod：{retired.summary()}"]
        except Exception as exc:  # noqa: BLE001
            return [f"旧的独立桥接 mod 未能自动清理（{exc}）；"
                    f"请在启动器里取消勾选它，否则事件会被记录两次"]

    def _announce_completion(self, job: Job) -> None:
        summary = job.result.get("build", "")
        install = job.result.get("install", "")
        text = f"顾问语音包已生成：{summary}" + (f"；{install}" if install else "")
        self.logger.info("%s", text)
        self.push_message(
            visibility=["hud"], ai_behavior="blind",
            parts=[{"type": "text", "text": text}],
            source="stellaris_voice", priority=5,
        )

    @plugin_entry(
        id="stellaris_install_voice",
        name="安装顾问语音包",
        description="把已生成的语音包安装到 Stellaris 本地 mod 目录并在 dlc_load.json 登记。",
        input_schema={"type": "object", "properties": {}},
    )
    async def stellaris_install_voice(self, **_):
        out_dir = self.data_path("pack")
        if not (out_dir / "descriptor.mod").is_file():
            return Err(SdkError("还没有生成过语音包，先调用 stellaris_build_voice"))
        blocked, why = installer.published_copy_blocks_install(
            self._stantis_user_dir(), mod_id=ADVISOR_MOD_ID)
        if blocked:
            return Err(SdkError(why))
        try:
            plan, spec, _ds, _ln = self._load_plan_inputs(voicepack.DEFAULT_VARIANTS)
            result = installer.install_pack(
                out_dir, self._stantis_user_dir(), mod_id=ADVISOR_MOD_ID,
                mod_stub_text=voicepack.render_mod_stub(spec),
                version=spec.mod_version)
        except Exception as exc:  # noqa: BLE001
            return Err(SdkError(f"安装失败: {exc}"))
        return Ok({"summary": result.summary(), "notes": result.notes,
                   "mod_dir": str(result.mod_dir)})

    # ------------------------------------------------------------------ #
    # entries: bridge mod
    # ------------------------------------------------------------------ #

    @plugin_entry(
        id="stellaris_build_bridge",
        name="生成事件桥接 mod",
        description=("生成把游戏事件写进 game.log 的 companion mod（不改动游戏内容），"
                     "install=true 时顺带安装。"),
        input_schema={
            "type": "object",
            "properties": {"install": {"type": "boolean", "default": True}},
        },
        timeout=PLAN_TIMEOUT_S,
    )
    async def stellaris_build_bridge(self, install: bool = True, **_):
        try:
            hooks = bridge_core.load_hooks(self._dataset_path("on_action_hooks.json"))
            plan = bridge_core.plan_bridge(hooks, bridge_core.BridgeSpec())
            out_dir = self.data_path("bridge_pack")
            result = bridge_core.build_bridge(plan, out_dir)
        except Exception as exc:  # noqa: BLE001
            return Err(SdkError(f"生成桥接 mod 失败: {exc}"))

        payload: dict[str, Any] = {
            "summary": result.summary(),
            "hooks": [{"goal": h.goal, "on_action": h.on_action,
                       "confidence": h.confidence} for h in plan.hooks],
            "skipped": [{"goal": g, "why": w} for g, w in result.skipped],
            "output": str(out_dir),
        }
        if install and self._settings.auto_install:
            try:
                install_result = installer.install_pack(
                    out_dir, self._stantis_user_dir(), mod_id=BRIDGE_MOD_ID,
                    mod_stub_text=bridge_core.render_mod_stub(plan.spec),
                    version=plan.spec.mod_version)
                payload["install"] = install_result.summary()
                payload["notes"] = install_result.notes
            except Exception as exc:  # noqa: BLE001
                payload["install_error"] = str(exc)
        return Ok(payload)

    @plugin_entry(
        id="stellaris_enable_in_launcher",
        name="把 mod 启用进启动器播放集",
        description=("Paradox 启动器会发现 mod/ 里的 .mod 存根，但不会把它加进播放集。"
                     "这个入口把已经发现的 mod 写进当前播放集并启用。"
                     "启动器必须处于关闭状态，否则它退出时会覆盖写入。"),
        input_schema={"type": "object", "properties": {}},
    )
    async def stellaris_enable_in_launcher(self, **_):
        user = self._stantis_user_dir()
        if installer.launcher_is_running():
            return Err(SdkError(
                "Paradox 启动器正在运行。先完全关闭它（含托盘图标），再调用这个入口；"
                "或者直接在启动器的 mod 列表里勾选这两个 mod。"))
        out: dict[str, Any] = {}
        for mod_id in (ADVISOR_MOD_ID, BRIDGE_MOD_ID):
            try:
                registration = installer.register_in_launcher_playset(user, mod_id=mod_id)
                out[mod_id] = registration.summary()
            except Exception as exc:  # noqa: BLE001
                out[mod_id] = f"失败: {exc}"
        return Ok(out)

    @plugin_entry(
        id="stellaris_live_voice",
        name="运行时再生控制",
        description=("游戏开着的时候改写顾问将要说的话。引擎按需读盘已实测确认，"
                     "所以不需要 reload 或控制台。status 看状态，once 立刻生成一条，"
                     "on/off 开关。需要先在插件详情页给 analysis 绑定一个模型槽位。"),
        input_schema={
            "type": "object",
            "properties": {
                "action": {"type": "string",
                           "enum": ["status", "once", "on", "off"], "default": "status"},
            },
        },
        timeout=120.0,
    )
    async def stellaris_live_voice(self, action: str = "status", **_):
        action = str(action or "status").strip().lower()
        if action in ("on", "off"):
            self._settings.live_voice_enabled = (action == "on")
            if action == "off":
                self._live_engine = None
                return Ok({"enabled": False})
            engine = self._ensure_live_engine()
            return Ok({"enabled": True,
                       "ready": engine is not None,
                       "error": self._live_engine_error})

        engine = self._ensure_live_engine()
        if engine is None:
            return Err(SdkError(
                f"运行时再生不可用：{self._live_engine_error or '未知原因'}。"
                "常见原因：语音包没安装；或没在插件详情页给 analysis 绑定模型槽位。"))
        if action == "once":
            engine.observe(gamelog.GameEvent("bridge", "war_declared_on_us"))
            written = await self._live_voice_step()
            return Ok({"written": written, **engine.status()})
        return Ok({
            "enabled": self._settings.live_voice_enabled,
            "mapping_entries": len(livevoice.GOAL_TO_EFFECT)
            + len(livevoice.NAMESPACE_TO_EFFECT),
            **engine.status(),
        })

    @ui.action(label="卸载游戏内的 mod", icon="🗑️", tone="danger", group="danger",
               order=90, confirm=True)
    @plugin_entry(
        id="stellaris_uninstall",
        name="卸载群星 mod",
        description="移除本插件安装的顾问语音包与事件桥接 mod（只删自己创建的目录）。",
        input_schema={
            "type": "object",
            "properties": {
                "voice": {"type": "boolean", "default": True},
                "bridge": {"type": "boolean", "default": True},
            },
        },
    )
    async def stellaris_uninstall(self, voice: bool = True, bridge: bool = True, **_):
        user = self._stantis_user_dir()
        out: dict[str, Any] = {}
        for flag, mod_id in ((voice, ADVISOR_MOD_ID), (bridge, BRIDGE_MOD_ID)):
            if not flag:
                continue
            try:
                out[mod_id] = installer.uninstall_pack(user, mod_id=mod_id).summary()
            except Exception as exc:  # noqa: BLE001
                out[mod_id] = f"失败: {exc}"
        return Ok(out)

    # ------------------------------------------------------------------ #
    # entries: live commentary
    # ------------------------------------------------------------------ #

    @plugin_entry(
        id="stellaris_live",
        name="实时解说控制",
        description=("start 开始尾随 game.log 并让角色回应；stop 停止；"
                     "once 立即读一次新日志；flush 把攒着的小事立刻说出来。"),
        input_schema={
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["start", "stop", "once", "flush", "status"],
                           "default": "status"},
            },
        },
    )
    async def stellaris_live(self, action: str = "status", **_):
        action = str(action or "status").strip().lower()
        if action == "start":
            if self._live_task and not self._live_task.done():
                return Ok({"state": "already_running", **self._live_stats})
            self._start_live()
            return Ok({"state": "started", **self._live_stats})
        if action == "stop":
            await self._stop_live()
            return Ok({"state": "stopped", **self._live_stats})
        if action == "status":
            return Ok(dict(self._live_stats))
        if action in ("once", "flush"):
            try:
                spoken = self._live_poll(force_flush=(action == "flush"))
            except Exception as exc:  # noqa: BLE001
                return Err(SdkError(f"读取日志失败: {exc}"))
            return Ok({"spoken": spoken, **self._live_stats})
        return Err(SdkError(f"未知 action: {action}"))

    def _ensure_live_engine(self) -> livevoice.LiveVoiceEngine | None:
        """Build the runtime-regeneration engine, once, if it is usable.

        Returns ``None`` (and records why) instead of raising: the live-voice
        half depends on the pack being installed *and* on a model slot being
        bound, and neither is a reason for the commentary half to stop working.
        """
        if self._live_engine is not None:
            return self._live_engine
        if not self._settings.live_voice_enabled:
            self._live_engine_error = "live_voice_enabled=false"
            return None
        try:
            # The slots to rewrite belong to whichever copy the game loads. After
            # a Workshop publish that is the subscribed directory, so hardcoding
            # the local path here would report "顾问语音包未安装" and silently take
            # live commentary down with it.
            active = installer.find_active_install(self._stantis_user_dir(),
                                                   mod_id=ADVISOR_MOD_ID)
            mod_dir = (active.path if active is not None
                       else self._stantis_user_dir() / "mod" / ADVISOR_MOD_ID)
            if not (mod_dir / "sound").is_dir():
                self._live_engine_error = "顾问语音包未安装"
                return None
            dataset = voicepack.load_dataset(self._dataset_path("advisor_sounds.json"))
            livevoice.load_meanings(dataset)
            lines = voicepack.load_lines(self._dataset_path("lines.zh-CN.json"))
            self._live_engine = livevoice.LiveVoiceEngine(
                mod_dir, lines,
                livevoice.LiveVoiceConfig(
                    model_usage=self._settings.live_model_usage,
                    budget_per_minute=self._settings.live_budget_per_minute,
                    min_seconds_between=self._settings.live_min_seconds_between,
                ),
                tts=self._tts_settings(),
            )
            self._live_engine_error = ""
            self.logger.info("运行时再生已就绪：%s", self._live_engine.inventory.summary())
        except Exception as exc:  # noqa: BLE001
            self._live_engine_error = str(exc)
            self.logger.warning("运行时再生不可用: %s", exc)
        return self._live_engine

    async def _live_voice_step(self) -> int:
        """One budget-limited regeneration pass. Never raises into the loop."""
        engine = self._ensure_live_engine()
        if engine is None or not engine.pending():
            return 0
        try:
            return await engine.drain(self.ctx)
        except Exception as exc:  # noqa: BLE001 - a bad line must not kill the loop
            self.logger.warning("运行时再生出错: %s", exc)
            engine.stats.failed += 1
            engine.stats.last_error = str(exc)
            return 0

    def _start_live(self) -> None:
        self._live_stop = asyncio.Event()
        self._live_task = asyncio.create_task(self._live_loop())
        self._live_stats = {"state": "starting"}

    async def _stop_live(self) -> None:
        task = self._live_task
        if not task or task.done():
            self._live_stats = {"state": "stopped"}
            self._live_task = None
            return
        self._live_stop.set()
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
        self._live_task = None
        self._live_stats = {"state": "stopped"}

    async def _live_loop(self) -> None:
        user = nekoconfig.find_stellaris_user_dir()
        log_path = gamelog.find_game_log(user) if user else None
        if not log_path:
            self._live_stats = {"state": "failed", "error": "未找到 logs/game.log"}
            self.logger.warning("实时解说无法启动：未找到 game.log")
            return

        tailer = gamelog.GameLogTailer(log_path)
        tailer.prime()
        self._live_policy = CommentaryPolicy(self._settings.policy())
        self._live_tailer = tailer
        self._live_stats = {"state": "running", "log": str(log_path)}

        self.logger.info("实时解说已启动，尾随 %s", log_path)
        try:
            while not self._live_stop.is_set():
                try:
                    self._live_poll()
                except Exception as exc:  # noqa: BLE001 - a poll must never kill the loop
                    self.logger.warning("日志轮询出错: %s", exc)
                try:
                    await self._live_voice_step()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    self.logger.warning("运行时再生步骤出错: %s", exc)
                await asyncio.sleep(max(1.0, self._settings.poll_interval_s))
        except asyncio.CancelledError:
            self.logger.info("实时解说已取消")
            raise
        finally:
            self._live_stats = {"state": "stopped"}

    def _live_poll(self, *, force_flush: bool = False) -> list[str]:
        """One poll: consume new lines, then let the policy decide.

        Returns the prompts that were actually pushed, so the ``once``/``flush``
        actions can report something concrete instead of "done".
        """
        policy: CommentaryPolicy = getattr(self, "_live_policy", None) or CommentaryPolicy(
            self._settings.policy())
        tailer: gamelog.GameLogTailer = getattr(self, "_live_tailer", None) or self._make_tailer()
        self._live_policy, self._live_tailer = policy, tailer

        pushed: list[str] = []

        for event in gamelog.iter_new_events(tailer):
            engine = self._ensure_live_engine()
            if engine is not None:
                engine.observe(event)
            decision = policy.consider(event)
            if decision.should_speak:
                pushed.append(self._emit(decision))
                policy.note_spoken()

        if force_flush:
            events = policy.drain_digest()
            if events:
                decision = Decision("speak", "digest", events, reason="手动 flush")
                pushed.append(self._emit(decision))
                policy.note_spoken()

        decision = policy.tick()
        if decision.should_speak:
            pushed.append(self._emit(decision))
            policy.note_spoken()

        self._live_stats.update({"state": "running", **policy.stats()})
        return pushed

    def _make_tailer(self) -> gamelog.GameLogTailer:
        log_path = gamelog.find_game_log(self._stantis_user_dir())
        if not log_path:
            raise SdkError("未找到 logs/game.log")
        tailer = gamelog.GameLogTailer(log_path)
        tailer.prime()
        return tailer

    def _emit(self, decision) -> str:
        """Push one commentary prompt and return the text that was sent."""
        prompt = build_prompt(decision)
        priority = 8 if decision.tier == "urgent" else (5 if decision.tier == "notable" else 3)
        result = self.push_message(
            visibility=[],
            ai_behavior="respond",
            parts=[{"type": "text", "text": prompt}],
            source="stellaris_live",
            priority=priority,
            metadata={"tier": decision.tier, "events": [e.key() for e in decision.events]},
        )
        if not result.get("submitted"):
            # Surface the transport reason instead of pretending it went out; the
            # utterance is deliberately *not* counted as spoken in that case.
            self.logger.warning("实时解说推送未提交: %s", result.get("reason"))
        return prompt
