"""neko_calabiqiu —— 卡拉彼丘陪伴插件。"""

from __future__ import annotations

import asyncio
import threading
import time
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

from .adapters.data_layer_process import DataLayerProcessManager
from .adapters.neko_dispatcher import NekoDispatcher
from .adapters.runtime_timeline import RuntimeTimeline, arbiter_chain_to_observe_records
from .adapters.scene_client import SceneClient
from .build_info import PLUGIN_VERSION
from .core.context_activation import should_activate_running_game_context
from .core.context_restore_retry import ContextRestoreRetry
from .core.contracts import CbqConfig, SceneState
from .core.instance_gate import release as release_instance_gate
from .core.instance_gate import try_claim as try_claim_instance_gate
from .core.instructions import CBQ_RESTORE_INSTRUCTIONS
from .core.runtime import CompanionRuntime
from .core.runtime_identity import runtime_sync_status

_CONFIG_SECTION = "neko_calabiqiu"


@neko_plugin
class NekoCalabiqiuPlugin(NekoPluginBase):
    def __init__(self, ctx: Any) -> None:
        super().__init__(ctx)
        try:
            self.logger = self.enable_file_logging(log_level="INFO")
        except Exception:  # noqa: BLE001
            self.logger = ctx.logger

        self.cfg = CbqConfig()
        self.data_layer_manager = DataLayerProcessManager(
            self.cfg, plugin_root=Path(__file__).resolve().parent, external_only=True
        )
        self.client = SceneClient(self.cfg.data_layer_url, self.cfg.http_timeout_seconds)
        self.timeline = RuntimeTimeline(observability_enabled=self.cfg.observability_enabled)
        self.dispatcher = NekoDispatcher(self, timeline=self.timeline)
        self.runtime = CompanionRuntime(
            self.cfg,
            push_event=lambda event, dry_run: self.dispatcher.push_event(event, dry_run=dry_run),
        )
        # 兼容面板/动作直接读 safety
        self.safety = self.runtime.safety

        self._lifecycle_lock = threading.RLock()
        self._state_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._shutdown_complete = threading.Event()
        self._shutdown_result: dict[str, Any] | None = None
        self._shutdown_join_timeout_seconds = 3.0
        self._instance_gate_key = str(Path(__file__).resolve().parent)
        self._instance_gate_token = object()
        self._instance_gate_claimed = False
        self._context_active = False
        self._context_restore = ContextRestoreRetry()
        self._disconnect_since = 0.0
        self._disconnect_debounce_seconds = 3.0

    @property
    def scene_state(self) -> SceneState:
        """场景态势（勿命名为 state：会覆盖 SDK 持久化字段导致启动失败）。"""
        return self.runtime.state

    async def _read_config(
        self, *, use_defaults_on_error: bool = False
    ) -> CbqConfig | None:
        try:
            dumped = await self.config.dump(timeout=5.0)
            if not isinstance(dumped, dict):
                raise TypeError("config dump must be a mapping")
            section = dumped.get(_CONFIG_SECTION)
            if section is None:
                data: dict[str, Any] = {}
            elif isinstance(section, dict):
                data = section
            else:
                raise TypeError("plugin config section must be a mapping")
        except Exception as exc:  # noqa: BLE001
            fallback = ", using defaults" if use_defaults_on_error else ", current config preserved"
            self.logger.warning(f"config load failed{fallback}: {type(exc).__name__}")
            if not use_defaults_on_error:
                return None
            data = {}
        return CbqConfig.from_mapping(data)

    async def _reload_config(self, *, use_defaults_on_error: bool = False) -> bool:
        cfg = await self._read_config(use_defaults_on_error=use_defaults_on_error)
        if cfg is None:
            return False
        self._apply_config(cfg)
        return True

    def _apply_config(self, cfg: CbqConfig) -> None:
        with self._lifecycle_lock:
            previous = self.cfg
            self.cfg = cfg
            self.data_layer_manager.configure(cfg)
            self.client = SceneClient(cfg.data_layer_url, cfg.http_timeout_seconds)
            self.runtime.configure(cfg)
            self.safety = self.runtime.safety
            self.timeline.configure(observability_enabled=cfg.observability_enabled)
            if (previous.enabled and not cfg.enabled) or (
                not previous.dry_run and cfg.dry_run
            ):
                self._reset_runtime_after_data_layer_loss()

    @lifecycle(id="startup")
    async def startup(self, **_):
        if self._thread is not None and (
            self._thread.is_alive() or not self._shutdown_complete.is_set()
        ):
            return Err(SdkError("previous plugin runtime is still shutting down"))
        if not try_claim_instance_gate(
            self._instance_gate_key, self._instance_gate_token
        ):
            return Err(SdkError("another plugin instance is still shutting down"))
        self._instance_gate_claimed = True
        try:
            await self._reload_config(use_defaults_on_error=True)
            data_layer_status = self.data_layer_manager.start_if_needed()
            self._shutdown_complete.clear()
            self._shutdown_result = None
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._loop, daemon=True, name="cbq-poll"
            )
            self._thread.start()
        except Exception:
            self._release_instance_gate()
            raise
        self.logger.info(
            f"neko_calabiqiu started (dry_run={self.cfg.dry_run}, url={self.cfg.data_layer_url}, "
            f"data_layer={data_layer_status.get('mode')})"
        )
        return Ok({"status": "running", "dry_run": self.cfg.dry_run, "data_layer": data_layer_status})

    @lifecycle(id="shutdown")
    def shutdown(self, **_):
        self._stop.set()
        worker = self._thread
        if worker is None or not worker.is_alive():
            worker = threading.Thread(
                target=self._finish_shutdown,
                daemon=True,
                name="cbq-shutdown",
            )
            worker.start()
        if worker is not threading.current_thread():
            worker.join(timeout=self._shutdown_join_timeout_seconds)
        if not self._shutdown_complete.is_set():
            self.logger.warning(
                "neko_calabiqiu shutdown deferred: poll or host dispatch is still busy"
            )
            return Ok({
                "status": "shutdown_pending",
                "data_layer": {"mode": "shutdown_pending"},
                "poll_thread_alive": bool(self._thread and self._thread.is_alive()),
                "context_restore_pending": bool(
                    self._context_active or self._context_restore.pending
                ),
            })
        return Ok(dict(self._shutdown_result or {"status": "shutdown"}))

    @lifecycle(id="config_change")
    async def on_config_change(self, **_):
        cfg = await self._read_config()
        if cfg is None:
            return Err(SdkError("config reload failed; current config preserved"))
        with self._lifecycle_lock:
            previous_signature = self.data_layer_manager.config_signature(self.cfg)
            self._apply_config(cfg)
            data_layer_status = self.data_layer_manager.snapshot()
            if previous_signature != self.data_layer_manager.config_signature(self.cfg):
                data_layer_status = self.data_layer_manager.restart_for_config_change()
            return Ok({
                "status": "reloaded",
                "dry_run": self.cfg.dry_run,
                "data_layer": data_layer_status,
            })

    def _loop(self) -> None:
        try:
            while not self._stop.is_set():
                try:
                    self._tick()
                except Exception as exc:  # noqa: BLE001
                    self.logger.warning(f"tick error: {type(exc).__name__}: {exc}")
                    self.safety.record_failure()
                # 自适应轮询：IDLE 时降频，IN_GAME 时高频
                with self._state_lock:
                    game_running = self.runtime.state.game_running
                    connected = self.runtime.state.connected
                interval = self.cfg.poll_interval_seconds
                if not connected or not game_running:
                    interval = max(interval, 2.0)  # IDLE 时 2 秒轮询一次
                self._stop.wait(interval)
        finally:
            self._finish_shutdown()

    def _finish_shutdown(self) -> None:
        """Complete cleanup once the poll thread has released lifecycle ownership."""
        if self._shutdown_complete.is_set():
            return
        with self._lifecycle_lock:
            if self._shutdown_complete.is_set():
                return
            data_layer_status = self.data_layer_manager.stop()
            self._reset_runtime_after_data_layer_loss()
            self._drain_context_restore()
            self._shutdown_result = {
                "status": "shutdown",
                "data_layer": data_layer_status,
                "context_restore_pending": self._context_restore.pending,
            }
            self.logger.info("neko_calabiqiu shutdown")
            self._shutdown_complete.set()
            self._release_instance_gate()

    def _release_instance_gate(self) -> None:
        if not self._instance_gate_claimed:
            return
        release_instance_gate(self._instance_gate_key, self._instance_gate_token)
        self._instance_gate_claimed = False

    def _tick(self) -> None:
        with self._lifecycle_lock:
            self._tick_locked()

    def _tick_locked(self) -> None:
        self._retry_context_restore()
        if self.cfg.data_layer_auto_start and not self.data_layer_manager.sync_game():
            self.timeline.mark_tick()
            self._reset_runtime_after_data_layer_loss()
            return
        if not self.cfg.enabled:
            return
        if not self.data_layer_manager.can_consume_scene():
            if self.cfg.data_layer_auto_start:
                self.data_layer_manager.restart_if_crashed()
            self.timeline.mark_tick()
            self._reset_runtime_after_data_layer_loss()
            return
        new_state = self.client.poll()
        # 数据层崩溃时尝试自动重启
        if not new_state.connected:
            self.data_layer_manager.invalidate_external_after_disconnect()
            if self.cfg.data_layer_auto_start:
                self.data_layer_manager.restart_if_crashed()
            self.timeline.mark_tick()
            now = time.monotonic()
            if self._disconnect_since <= 0:
                self._disconnect_since = now
            elif now - self._disconnect_since >= self._disconnect_debounce_seconds:
                self._reset_runtime_after_data_layer_loss()
            return
        self._disconnect_since = 0.0
        if not self.data_layer_manager.validate_scene_instance(
            new_state.data_layer_instance_id
        ):
            self.timeline.mark_tick()
            self._reset_runtime_after_data_layer_loss()
            return
        self.timeline.mark_tick()
        with self._state_lock:
            prev = self.runtime.state
            # 场景无变化时跳过 evaluate（frame_seq 未变且连接状态相同）
            if prev.frame_seq == new_state.frame_seq and (
                prev.connected == new_state.connected
                and prev.data_layer_instance_id == new_state.data_layer_instance_id
                and not new_state.events_tail
            ):
                self.runtime.state = new_state
                return
            try:
                result = self.runtime.evaluate(prev, new_state)
                self.runtime.state = new_state
            except Exception as exc:  # noqa: BLE001
                self.logger.warning(f"evaluate failed: {type(exc).__name__}: {exc}")
                self.safety.record_failure()
                return
        if result.candidates or result.chosen is not None:
            self.logger.info(f"[arbiter] scenario={result.scenario} chain={result.chain}")
            for record in arbiter_chain_to_observe_records(
                result.chain, scenario=result.scenario
            ):
                self.timeline.record_stage(**record)
                self.timeline.record_decision(
                    event_id=record.get("event_id"),
                    stage=str(record.get("stage") or "arbiter_dropped"),
                    outcome=str(record.get("outcome") or "dropped"),
                    reason=str(record.get("reason") or "unknown"),
                    scenario=result.scenario,
                    safety_status=self.safety.status(),
                    dry_run=self.cfg.dry_run,
                )
        if result.chosen is not None:
            self.logger.info(f"[output] {result.output}")
            if (
                result.chosen.event_id == "game_started"
                and not self.cfg.dry_run
                and str(result.output or "").startswith("pushed(context=game_started)")
            ):
                with self._state_lock:
                    self._context_active = True
                    self._context_restore.clear()
            if (
                result.chosen.event_id == "game_stopped"
                and not self.cfg.dry_run
                and str(result.output or "").startswith("pushed(context=game_stopped)")
            ):
                with self._state_lock:
                    self._context_active = False
                    self._context_restore.clear()
        if not self.cfg.dry_run and new_state.game_running and not self._context_active:
            try:
                self._ensure_running_game_context()
            except Exception as exc:  # noqa: BLE001
                self.logger.warning(f"context retry failed: {type(exc).__name__}: {exc}")

    def _reset_runtime_after_data_layer_loss(self) -> None:
        """Clear scene state and schedule reliable host-context restoration."""
        with self._state_lock:
            self.runtime.reset()
            if self._context_active:
                self._context_restore.request()
        self._disconnect_since = 0.0
        self._retry_context_restore()

    def _retry_context_restore(self, *, force: bool = False) -> bool:
        now = time.monotonic()
        with self._state_lock:
            if not self._context_restore.begin_attempt(now, ignore_backoff=force):
                return False
        try:
            restored = bool(self.dispatcher.push_context(CBQ_RESTORE_INSTRUCTIONS))
        except Exception as exc:  # noqa: BLE001
            restored = False
            self.logger.warning(f"context restore failed: {type(exc).__name__}: {exc}")
        with self._state_lock:
            self._context_restore.finish_attempt(success=restored, now=now)
            if restored:
                self._context_active = False
            attempts = self._context_restore.attempts
            next_attempt_at = self._context_restore.next_attempt_at
        if not restored:
            self.logger.warning(
                "context restore pending "
                f"(attempt={attempts}, "
                f"next_in={max(0.0, next_attempt_at - now):.1f}s)"
            )
        return restored

    def _drain_context_restore(self, *, max_attempts: int = 3) -> bool:
        """Use bounded immediate retries while shutdown still owns the dispatcher."""
        for _ in range(max_attempts):
            with self._state_lock:
                if not self._context_restore.pending:
                    return True
            self._retry_context_restore(force=True)
        with self._state_lock:
            pending = self._context_restore.pending
        if pending:
            self.logger.error(
                "context restore remained pending after bounded shutdown retries"
            )
        return not pending

    def _ensure_running_game_context(self) -> bool:
        """Inject the companion context when live output is enabled mid-session."""
        with self._state_lock:
            if not should_activate_running_game_context(
                dry_run=self.cfg.dry_run,
                game_running=self.scene_state.game_running,
                context_active=self._context_active,
            ):
                return False
            # Reserve the transition so concurrent UI actions cannot double-push.
            self._context_active = True
            self._context_restore.clear()
        try:
            output = self.dispatcher.push_game_started(dry_run=False)
        except Exception:
            with self._state_lock:
                self._context_active = False
            raise
        context_injected = output.startswith("pushed(context=game_started)")
        if not context_injected:
            with self._state_lock:
                self._context_active = False
        self.logger.info(f"[output] {output} (late live activation)")
        return context_injected

    def _dashboard_payload(self, s: SceneState) -> dict[str, Any]:
        data_layer = self.data_layer_manager.snapshot()
        model = dict(s.model or {})
        runtime_sync = runtime_sync_status(
            connected=s.connected,
            plugin_version=PLUGIN_VERSION,
            data_layer_version=s.data_layer_version,
            expected_weights=str(data_layer.get("expected_weights") or ""),
            actual_weights=str(model.get("weights_id") or ""),
            expected_infer_mode=str(data_layer.get("expected_infer_mode") or ""),
            actual_infer_mode=s.infer_mode,
            fallback_from=str(model.get("weights_fallback_from") or ""),
        )
        return {
            "plugin_version": PLUGIN_VERSION,
            "data_layer_version": s.data_layer_version,
            "runtime_sync": runtime_sync,
            "enabled": self.cfg.enabled,
            "dry_run": self.cfg.dry_run,
            "connected": s.connected,
            "scenario": s.scenario,
            "game_running": s.game_running,
            "game_region": s.game_region,
            "process_name": s.process_name,
            "session_phase": s.session_phase,
            "degraded": s.degraded,
            "degrade_reason": s.degrade_reason,
            "awareness": s.awareness,
            "flags": s.flags,
            "infer_mode": s.infer_mode,
            "classifier": dict(s.classifier or {}),
            "model": model,
            "data_layer": data_layer,
            "safety": self.safety.snapshot(),
            "context_active": self._context_active,
            "context_restore_pending": self._context_restore.pending,
            "observe": self.timeline.snapshot(),
        }

    @ui.context(id="dashboard", title="卡拉彼丘陪伴")
    async def dashboard_context(self):
        with self._state_lock:
            s = self.scene_state
        return self._dashboard_payload(s)

    @ui.action(id="assistant_control", label="助手进程", group="runtime", order=5, refresh_context=True)
    @plugin_entry(
        id="assistant_control", name="助手进程", description="启动、停止或重启独立安装的陪伴助手。",
        input_schema={"type": "object", "properties": {
            "operation": {"type": "string", "enum": ["start", "stop", "restart"]},
            "directory": {"type": "string"},
        }, "required": ["operation"]},
    )
    async def assistant_control(self, operation: str, directory: str = "", **_):
        try:
            if operation not in {"start", "stop", "restart"}:
                raise ValueError("未知的助手操作。")
            if directory.strip() and operation != "stop":
                root = self.data_layer_manager.validate_assistant_directory(directory)
                if str(root) != self.cfg.assistant_directory:
                    await self.config.set(f"{_CONFIG_SECTION}.assistant_directory", str(root))
            else:
                root = None

            def control():
                with self._lifecycle_lock:
                    if self._stop.is_set():
                        raise ValueError("插件正在关闭，请重新启动插件后操作。")
                    if root is not None:
                        self.cfg.assistant_directory = str(root)
                    status = self.data_layer_manager.control(operation)
                    if operation in {"stop", "restart"}:
                        self._reset_runtime_after_data_layer_loss()
                    if status.get("mode") in {"failed", "missing", "incompatible"}:
                        raise ValueError(str(status.get("last_error") or "助手未能启动。"))
                    return status

            return Ok({"data_layer": await asyncio.to_thread(control)})
        except Exception as exc:  # noqa: BLE001
            return Err(SdkError(str(exc)))

    @ui.action(id="set_dry_run", label="设置 dry_run", tone="primary", group="runtime", order=10, refresh_context=True)
    @plugin_entry(
        id="set_dry_run",
        name="设置 dry_run",
        description="开/关 dry_run。",
        input_schema={"type": "object", "properties": {"value": {"type": "boolean", "default": True}}},
    )
    async def set_dry_run(self, value: bool = True, **_):
        with self._lifecycle_lock:
            previous_dry_run = self.cfg.dry_run
            self.cfg.dry_run = bool(value)
            context_injected = False
            if not previous_dry_run and self.cfg.dry_run:
                self._reset_runtime_after_data_layer_loss()
            if not self.cfg.dry_run:
                try:
                    context_injected = self._ensure_running_game_context()
                except Exception as exc:  # noqa: BLE001
                    return Err(SdkError(f"context injection failed: {exc}"))
            return Ok({"dry_run": self.cfg.dry_run, "context_injected": context_injected})

    @ui.action(id="pause", label="急停", tone="danger", group="runtime", order=20, refresh_context=True)
    @plugin_entry(id="pause", name="急停", description="暂停所有提醒输出。")
    async def pause(self, **_):
        self.safety.pause()
        return Ok({"safety": self.safety.status()})

    @ui.action(id="resume", label="恢复", tone="success", group="runtime", order=30, refresh_context=True)
    @plugin_entry(id="resume", name="恢复", description="恢复提醒输出。")
    async def resume(self, **_):
        self.safety.resume()
        return Ok({"safety": self.safety.status()})

    @ui.action(id="test_say", label="演示警报", tone="info", group="diagnostics", order=40, refresh_context=False)
    @plugin_entry(
        id="test_say",
        name="演示警报",
        description="dry_run=false 时通过 N.E.K.O TTS 播放一条固定短警报。",
        input_schema={
            "type": "object",
            "properties": {"text": {"type": "string", "default": "右边有敌人！"}},
        },
    )
    async def test_say(self, text: str = "右边有敌人！", **_):
        if self.cfg.dry_run:
            self.timeline.record_stage(
                stage="test_say_blocked",
                outcome="blocked",
                reason="dry_run",
                kind="test",
                dry_run=True,
                pushed=False,
            )
            return Ok({"pushed": False, "blocked": "dry_run", "text": str(text)})
        if self.safety.stopped:
            self.timeline.record_stage(
                stage="test_say_blocked",
                outcome="blocked",
                reason=self.safety.status(),
                kind="test",
                dry_run=False,
                pushed=False,
            )
            return Ok({"pushed": False, "blocked": self.safety.status(), "text": str(text)})
        try:
            alert = str(text).replace("\r", " ").replace("\n", " ").strip()[:16]
            speech_result = self.dispatcher.speak_text(alert, event_id="test_say")
            self.timeline.record_stage(
                stage="test_say_pushed",
                outcome="pushed",
                reason="project_tts_queued",
                kind="test",
                ai_behavior="blind",
                dry_run=False,
                pushed=True,
                audio_queued=bool(speech_result.get("audio_queued")),
            )
            return Ok({
                "pushed": True,
                "text": alert,
                "tts_requested": True,
                "audio_queued": bool(speech_result.get("audio_queued")),
                "method": "project_tts",
            })
        except Exception as exc:  # noqa: BLE001
            self.timeline.record_stage(
                stage="test_say_failed",
                outcome="failed",
                reason=type(exc).__name__,
                kind="test",
                ai_behavior="blind",
                dry_run=False,
                pushed=False,
            )
            return Err(SdkError(f"test_say push failed: {exc}"))

    @plugin_entry(id="status", name="状态", description="查看当前连接/场景/安全状态。")
    def status(self, **_):
        with self._state_lock:
            s = self.scene_state
        return Ok(self._dashboard_payload(s))
