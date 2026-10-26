"""The long-lived service behind the plugin facade.

Responsibilities are deliberately narrow and testable:

* keep one :class:`capture.ScreenCapturer` alive and hand the newest frame to
  the analyser;
* discover the game the user opened, adopt its window and keep the foreground
  guard honest;
* run the reactive :class:`policy.PlayPolicy` between LLM turns;
* stream frames and a structured situation report into the dialog LLM so the
  catgirl can narrate and override the plan;
* yield to the player the instant a human touches WASD/fire (or presses F8),
  and release every held key when doing so.

Everything is driven by the host's long-lived command loop.  No task is started
from ``@lifecycle(startup)`` -- that hook runs on a transient ``asyncio.run``
loop whose tasks are cancelled when it returns (the ICEY plugin hit this).
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from . import capture as capture_mod
from . import catalog, discovery, prompts
from . import hud as hud_mod
from . import input as input_mod
from . import policy as policy_mod
from . import vision as vision_mod

__all__ = ["ServiceConfig", "SourceService"]


def _as_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    if isinstance(value, (int, float)):
        return bool(value)
    return default


def _as_float(value: Any, default: float, *, low: float | None = None,
              high: float | None = None) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        result = float(default)
    if low is not None:
        result = max(low, result)
    if high is not None:
        result = min(high, result)
    return result


def _as_int(value: Any, default: int, *, low: int | None = None,
            high: int | None = None) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError):
        result = int(default)
    if low is not None:
        result = max(low, result)
    if high is not None:
        result = min(high, result)
    return result


def _as_str(value: Any, default: str = "") -> str:
    if value is None:
        return default
    text = str(value).strip()
    return text if text else default


@dataclass
class ServiceConfig:
    capture_backend: str = "auto"
    capture_monitor: int = 0
    min_capture_edge: int = 160
    target_fps: float = 4.0
    frame_max_edge_px: int = 1024
    frame_jpeg_quality: int = 75
    frame_max_bytes: int = 102400
    stream_frames_to_llm: bool = True
    frame_stream_interval_seconds: float = 8.0
    commentary_interval_seconds: float = 25.0
    problem_cooldown_seconds: float = 60.0
    require_foreground: bool = True
    autopilot_enabled: bool = False
    autopilot_tick_seconds: float = 0.35
    dry_run: bool = False
    max_session_minutes: float = 45.0
    blind_frame_limit: int = 8
    look_units_per_degree: float = 15.15
    horizontal_fov: float = 90.0
    vertical_fov: float = 59.0
    invert_y: bool = False
    yield_on_human_input: bool = True
    human_input_grace_seconds: float = 12.0
    pause_hotkey_enabled: bool = True
    auto_resume_from_menu: bool = True
    menu_stable_ticks: int = 3
    start_in_game_required: bool = True
    objective: str = ""
    plan_steps: tuple[str, ...] = ()
    keys: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any] | None) -> "ServiceConfig":
        if not isinstance(raw, Mapping):
            raw = {}
        cfg = cls()
        cfg.capture_backend = _as_str(raw.get("capture_backend"), cfg.capture_backend).lower()
        if cfg.capture_backend not in ("auto", "dxgi", "gdi", "pil"):
            cfg.capture_backend = "auto"
        cfg.capture_monitor = _as_int(raw.get("capture_monitor"), cfg.capture_monitor, low=0, high=8)
        cfg.min_capture_edge = _as_int(raw.get("min_capture_edge"), cfg.min_capture_edge, low=32, high=2048)
        cfg.target_fps = _as_float(raw.get("target_fps"), cfg.target_fps, low=0.5, high=15.0)
        cfg.frame_max_edge_px = _as_int(raw.get("frame_max_edge_px"), cfg.frame_max_edge_px, low=320, high=2048)
        cfg.frame_jpeg_quality = _as_int(raw.get("frame_jpeg_quality"), cfg.frame_jpeg_quality, low=10, high=95)
        cfg.frame_max_bytes = _as_int(raw.get("frame_max_bytes"), cfg.frame_max_bytes, low=0, high=250000)
        cfg.stream_frames_to_llm = _as_bool(raw.get("stream_frames_to_llm"), cfg.stream_frames_to_llm)
        cfg.frame_stream_interval_seconds = _as_float(
            raw.get("frame_stream_interval_seconds"), cfg.frame_stream_interval_seconds, low=1.0, high=120.0)
        cfg.commentary_interval_seconds = _as_float(
            raw.get("commentary_interval_seconds"), cfg.commentary_interval_seconds, low=5.0, high=300.0)
        cfg.problem_cooldown_seconds = _as_float(
            raw.get("problem_cooldown_seconds"), cfg.problem_cooldown_seconds, low=5.0, high=600.0)
        cfg.require_foreground = _as_bool(raw.get("require_foreground"), cfg.require_foreground)
        cfg.autopilot_enabled = _as_bool(raw.get("autopilot_enabled"), cfg.autopilot_enabled)
        cfg.autopilot_tick_seconds = _as_float(
            raw.get("autopilot_tick_seconds"), cfg.autopilot_tick_seconds, low=0.1, high=3.0)
        cfg.dry_run = _as_bool(raw.get("dry_run"), cfg.dry_run)
        cfg.max_session_minutes = _as_float(
            raw.get("max_session_minutes"), cfg.max_session_minutes, low=0.0, high=600.0)
        cfg.blind_frame_limit = _as_int(raw.get("blind_frame_limit"), cfg.blind_frame_limit, low=2, high=120)
        cfg.look_units_per_degree = _as_float(
            raw.get("look_units_per_degree"), cfg.look_units_per_degree, low=0.2, high=200.0)
        cfg.horizontal_fov = _as_float(raw.get("horizontal_fov"), cfg.horizontal_fov, low=60.0, high=120.0)
        cfg.vertical_fov = _as_float(raw.get("vertical_fov"), cfg.vertical_fov, low=30.0, high=100.0)
        cfg.invert_y = _as_bool(raw.get("invert_y"), cfg.invert_y)
        cfg.yield_on_human_input = _as_bool(raw.get("yield_on_human_input"), cfg.yield_on_human_input)
        cfg.human_input_grace_seconds = _as_float(
            raw.get("human_input_grace_seconds"), cfg.human_input_grace_seconds, low=2.0, high=120.0)
        cfg.pause_hotkey_enabled = _as_bool(raw.get("pause_hotkey_enabled"), cfg.pause_hotkey_enabled)
        cfg.auto_resume_from_menu = _as_bool(raw.get("auto_resume_from_menu"), cfg.auto_resume_from_menu)
        cfg.menu_stable_ticks = _as_int(raw.get("menu_stable_ticks"), cfg.menu_stable_ticks, low=2, high=30)
        cfg.start_in_game_required = _as_bool(
            raw.get("start_in_game_required"), cfg.start_in_game_required)
        cfg.objective = _as_str(raw.get("objective"), "")
        raw_steps = raw.get("plan_steps")
        if isinstance(raw_steps, (list, tuple)):
            cfg.plan_steps = tuple(str(item).strip() for item in raw_steps if str(item).strip())
        raw_keys = raw.get("keys")
        if isinstance(raw_keys, Mapping):
            cfg.keys = {
                str(key): str(value).strip()
                for key, value in raw_keys.items()
                if isinstance(value, str) and value.strip()
            }
        return cfg

    def as_dict(self) -> dict[str, Any]:
        return {
            "capture_backend": self.capture_backend,
            "capture_monitor": self.capture_monitor,
            "min_capture_edge": self.min_capture_edge,
            "target_fps": self.target_fps,
            "frame_max_edge_px": self.frame_max_edge_px,
            "frame_jpeg_quality": self.frame_jpeg_quality,
            "frame_max_bytes": self.frame_max_bytes,
            "stream_frames_to_llm": self.stream_frames_to_llm,
            "frame_stream_interval_seconds": self.frame_stream_interval_seconds,
            "commentary_interval_seconds": self.commentary_interval_seconds,
            "problem_cooldown_seconds": self.problem_cooldown_seconds,
            "require_foreground": self.require_foreground,
            "autopilot_enabled": self.autopilot_enabled,
            "autopilot_tick_seconds": self.autopilot_tick_seconds,
            "dry_run": self.dry_run,
            "max_session_minutes": self.max_session_minutes,
            "blind_frame_limit": self.blind_frame_limit,
            "look_units_per_degree": self.look_units_per_degree,
            "horizontal_fov": self.horizontal_fov,
            "vertical_fov": self.vertical_fov,
            "invert_y": self.invert_y,
            "yield_on_human_input": self.yield_on_human_input,
            "human_input_grace_seconds": self.human_input_grace_seconds,
            "pause_hotkey_enabled": self.pause_hotkey_enabled,
            "auto_resume_from_menu": self.auto_resume_from_menu,
            "menu_stable_ticks": self.menu_stable_ticks,
            "start_in_game_required": self.start_in_game_required,
            "objective": self.objective,
            "plan_steps": list(self.plan_steps),
            "keys": dict(self.keys),
        }


class SourceService:
    """Owns capture, input, policy and the two background loops."""

    def __init__(
        self,
        *,
        logger: Any = None,
        push_message_fn: Callable[..., Any] | None = None,
        data_dir: Any = None,
    ) -> None:
        self._logger = logger
        self._push = push_message_fn
        self._data_dir = data_dir
        self.cfg = ServiceConfig()
        self._lang = prompts.DEFAULT_LANG

        self._running = False
        self._tasks: list[asyncio.Task] = []
        self._capturer: capture_mod.ScreenCapturer | None = None
        self._injector: input_mod.KeyInjector | None = None
        self._analyzer = vision_mod.FrameAnalyzer()
        self._hud = hud_mod.HudTracker()
        self._policy: policy_mod.PlayPolicy | None = None

        self._game: catalog.GameProfile | None = None
        self._running_game: discovery.RunningGame | None = None
        self._running_games: list[discovery.RunningGame] = []
        self._last_scan_at = 0.0
        self._adopted_pid = 0
        self._mode = "waiting"           # waiting | playing | soft_yield | paused | stopped
        self._pause_latched = False
        self._has_entered_gameplay = False
        self._gameplay_ticks = 0
        self._menu_ticks = 0
        self._last_menu_press_at = 0.0
        self._blind_ticks = 0
        self._last_hotkey_down = False
        self._human_seen_at = 0.0
        self._away = False
        self._session_started_at = 0.0
        self._played_seconds = 0.0
        self._last_tick_at = 0.0
        self._tick_count = 0
        self._frames_seen = 0

        self._latest_frame: capture_mod.Frame | None = None
        self._latest_features: vision_mod.FrameFeatures | None = None
        self._latest_hud: hud_mod.HudState = hud_mod.HudState()
        self._latest_decision: policy_mod.Decision | None = None
        self._last_ops: list[policy_mod.Op] = []
        self._last_intent = ""
        self._last_ops_at = 0.0

        self._last_stream_at = 0.0
        self._last_commentary_at = 0.0
        self._last_problem_at = 0.0
        self._last_problem_key = ""
        self._waiting_announced_at = 0.0
        self._menu_announced_at = 0.0
        self._takeover_announced_at = 0.0
        self._resume_announced_at = 0.0
        self._last_started_announce_at = 0.0

        self._pending_commands: list[policy_mod.Command] = []
        self._objective = ""
        self._plan_steps: list[str] = []
        self._errors: list[str] = []
        self._events: list[dict[str, Any]] = []
        self.frames_streamed = 0
        self.commentaries = 0

    # ------------------------------------------------------------------
    # wiring
    # ------------------------------------------------------------------

    def set_lang(self, lang: str | None) -> None:
        self._lang = prompts.normalise_lang(lang)

    def configure(self, raw: Mapping[str, Any] | None) -> ServiceConfig:
        previous = self.cfg
        self.cfg = ServiceConfig.from_dict(raw)
        self._objective = self.cfg.objective
        self._plan_steps = list(self.cfg.plan_steps)
        if self._policy is not None:
            self._policy.look = policy_mod.LookCalibration(
                units_per_degree=self.cfg.look_units_per_degree,
                horizontal_fov=self.cfg.horizontal_fov,
                vertical_fov=self.cfg.vertical_fov,
                invert_y=self.cfg.invert_y,
            )
            self._policy.keys.update(self.cfg.keys)
        if previous.require_foreground != self.cfg.require_foreground and self._injector is not None:
            self._injector.require_foreground = self.cfg.require_foreground
        if previous.dry_run != self.cfg.dry_run and self._injector is not None:
            self._injector.dry_run = self.cfg.dry_run
        return self.cfg

    def _log(self, level: str, message: str, *args: Any) -> None:
        logger = self._logger
        if logger is None:
            return
        try:
            getattr(logger, level)("[source] " + message, *args)
        except Exception:
            pass

    def _record_error(self, message: str) -> None:
        text = str(message)[:400]
        self._errors.append(f"{time.strftime('%H:%M:%S')} {text}")
        self._errors = self._errors[-20:]
        self._log("warning", "{}", text)

    def _event(self, kind: str, detail: str = "") -> None:
        self._events.append({"at": round(time.time(), 2), "kind": kind, "detail": str(detail)[:200]})
        self._events = self._events[-30:]

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        discovery.enable_dpi_awareness()
        self._spawn(self._control_loop(), "control")
        self._spawn(self._narration_loop(), "narration")
        self._spawn(self._hotkey_loop(), "hotkey")
        self._log("info", "service started (tick={:.2f}s)", self.cfg.autopilot_tick_seconds)

    async def stop(self) -> None:
        self._running = False
        tasks = list(self._tasks)
        self._tasks.clear()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._release_all()
        if self._capturer is not None:
            try:
                self._capturer.close()
            except Exception:
                pass
            self._capturer = None
        self._log("info", "service stopped")

    def _spawn(self, coro, name: str) -> None:
        task = asyncio.create_task(coro, name=f"source-{name}")
        self._tasks.append(task)
        task.add_done_callback(self._on_task_done)

    def _on_task_done(self, task: asyncio.Task) -> None:
        if task.cancelled():
            return
        try:
            exc = task.exception()
        except Exception:
            exc = None
        if exc is not None:
            self._record_error(f"task {task.get_name()} failed: {type(exc).__name__}: {exc}")

    # ------------------------------------------------------------------
    # game/window lifecycle
    # ------------------------------------------------------------------

    def installed_ids(self) -> list[str]:
        try:
            return list(discovery.installed_games().keys())
        except Exception as exc:
            self._log("debug", "installed scan failed: {}", exc)
            return []

    def running_ids(self) -> list[str]:
        return [game.profile.id for game in self._running_games]

    def _scan_running(self, *, force: bool = False) -> None:
        now = time.time()
        if not force and (now - self._last_scan_at) < 2.0:
            return
        self._last_scan_at = now
        try:
            self._running_games = discovery.find_running_games()
        except Exception as exc:
            self._running_games = []
            self._record_error(f"running-game scan failed: {type(exc).__name__}: {exc}")

    def _select_running(self) -> discovery.RunningGame | None:
        if self._game is None:
            return None
        for running in self._running_games:
            if running.profile.id == self._game.id:
                return running
        return None

    def ensure_running_game(self, *, force: bool = False) -> discovery.RunningGame | None:
        self._scan_running(force=force)
        running = self._select_running()
        if running is None:
            self._running_game = None
            return None
        if not running.window:
            self._running_game = running
            return None
        if not running.window.is_usable(min_edge=self.cfg.min_capture_edge):
            self._running_game = running
            return None
        self._running_game = running
        if self._adopted_pid != running.pid:
            self._adopted_pid = running.pid
            self._apply_running(running)
        return running

    def _apply_running(self, running: discovery.RunningGame) -> None:
        """Called once when a new process/window is adopted."""
        self._has_entered_gameplay = False
        self._gameplay_ticks = 0
        self._menu_ticks = 0
        self._blind_ticks = 0
        self._analyzer.reset()
        self._hud.reset()
        self._latest_frame = None
        self._latest_features = None
        self._mode = "waiting" if not self.cfg.autopilot_enabled else "playing"
        self._pause_latched = False
        self._session_started_at = time.time()
        self._played_seconds = 0.0
        self._event("adopted", running.profile.id)
        self._log("info", "adopted {} pid={}", running.profile.id, running.pid)
        self._push_cue(
            "CUE_PREFIX_STARTED",
            prompts.t("GAME_FOUND", lang=self._lang, game=running.profile.name_zh),
        )

    def ensure_window(self, *, refresh: bool = False) -> discovery.RunningGame | None:
        return self.ensure_running_game(force=refresh)

    def _is_game_foreground(self) -> bool:
        running = self._running_game
        return bool(running and running.window and discovery.is_foreground(running.window.hwnd))

    # ------------------------------------------------------------------
    # capture
    # ------------------------------------------------------------------

    def _capture_region(self) -> tuple[int, int, int, int] | None:
        running = self._running_game
        if running is None or running.window is None:
            return None
        window = running.window
        if not window.is_usable(min_edge=self.cfg.min_capture_edge):
            return None
        rect = discovery.client_rect_on_screen(window.hwnd)
        if rect[2] - rect[0] < self.cfg.min_capture_edge or rect[3] - rect[1] < self.cfg.min_capture_edge:
            rect = window.rect
        if rect[2] - rect[0] < self.cfg.min_capture_edge or rect[3] - rect[1] < self.cfg.min_capture_edge:
            return None
        return rect

    def _ensure_capturer(self) -> capture_mod.ScreenCapturer:
        if self._capturer is None:
            self._capturer = capture_mod.ScreenCapturer(
                logger=self._logger,
                monitor=self.cfg.capture_monitor,
                preferred=self.cfg.capture_backend,
            )
        return self._capturer

    async def _capture_frame(self) -> capture_mod.Frame | None:
        region = self._capture_region()
        if region is None:
            return None
        try:
            capturer = self._ensure_capturer()
        except Exception as exc:
            self._record_error(f"capture init failed: {type(exc).__name__}: {exc}")
            return None
        frame = capturer.capture(region, retries=1)
        if frame is None:
            # Desktop Duplication legitimately reports "no new frame"; reuse the
            # previous image if it is fresh rather than treating it as blindness.
            previous = self._latest_frame
            if previous is not None and (time.time() - previous.captured_at) < 1.2:
                return previous
            await asyncio.sleep(0.03)
            frame = capturer.capture(region, retries=2)
        if frame is not None:
            self._latest_frame = frame
            self._frames_seen += 1
        return frame

    # ------------------------------------------------------------------
    # input
    # ------------------------------------------------------------------

    def _ensure_injector(self) -> input_mod.KeyInjector:
        if self._injector is None:
            self._injector = input_mod.KeyInjector(
                logger=self._logger,
                require_foreground=self.cfg.require_foreground,
                foreground_check=self._is_game_foreground,
                dry_run=self.cfg.dry_run,
            )
        return self._injector

    def _release_all(self) -> None:
        injector = self._injector
        if injector is None:
            return
        try:
            injector.release_all()
        except Exception as exc:
            self._log("debug", "release_all failed: {}", exc)

    async def _sleep_watched(self, seconds: float) -> bool:
        """Sleep in short slices, yielding the moment the player takes over.

        A single ``asyncio.sleep(1.0)`` inside a key hold would make the catgirl
        deaf to the player for a whole second; at 4 fps that is enough to miss a
        short key press.  Polling every 100 ms makes "jump in anytime" real.
        """
        remaining = max(0.0, float(seconds))
        while remaining > 0:
            slice_seconds = min(0.1, remaining)
            await asyncio.sleep(slice_seconds)
            remaining -= slice_seconds
            if self._mode != "playing":
                return True
            if self.cfg.yield_on_human_input:
                keys = self._human_keys()
                if keys:
                    self._human_seen_at = time.time()
                    self._yield_to_human(keys)
                    return True
        return False

    async def _run_ops(self, ops: list[policy_mod.Op]) -> None:
        injector = self._injector
        if injector is None or not ops:
            return
        self._last_ops = list(ops)
        self._last_ops_at = time.time()
        for op in ops:
            if not self._running or self._mode not in ("playing", "soft_yield"):
                return
            try:
                if op.kind == "wait":
                    if await self._sleep_watched(max(0.0, op.seconds)):
                        return
                elif op.kind == "look":
                    injector.look(op.dx, op.dy)
                    await asyncio.sleep(0.06)
                elif op.kind == "tap":
                    if not op.key:
                        continue
                    injector.key_down(op.key)
                    try:
                        if await self._sleep_watched(max(0.01, op.seconds)):
                            return
                    finally:
                        injector.key_up(op.key)
                elif op.kind == "hold":
                    if not op.key:
                        continue
                    injector.key_down(op.key)
                    try:
                        if await self._sleep_watched(max(0.01, op.seconds)):
                            return
                    finally:
                        injector.key_up(op.key)
                elif op.kind == "click":
                    injector.click(button=op.button)
                elif op.kind == "mouse_down":
                    injector.mouse_down(op.button)
                elif op.kind == "mouse_up":
                    injector.mouse_up(op.button)
            except (input_mod.InputGuardError, input_mod.InputUnavailable, ValueError) as exc:
                self._note_input_blocked(exc)
                return
        await asyncio.sleep(0.02)

    def _note_input_blocked(self, exc: Exception) -> None:
        if isinstance(exc, input_mod.InputGuardError) and "dry_run" in str(exc):
            self._push_problem("DRY_RUN_NOTE")
        elif isinstance(exc, input_mod.InputGuardError):
            self._push_problem("NOT_FOCUSED")
        else:
            self._push_problem("INPUT_FAILED", str(exc))
        self._log("debug", "input blocked: {}", exc)

    # ------------------------------------------------------------------
    # loops
    # ------------------------------------------------------------------

    async def _control_loop(self) -> None:
        self._log("info", "control loop started")
        while self._running:
            started = time.monotonic()
            try:
                await self._control_tick()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._record_error(f"control tick failed: {type(exc).__name__}: {exc}")
                await asyncio.sleep(0.5)
            elapsed = time.monotonic() - started
            await asyncio.sleep(max(0.02, self.cfg.autopilot_tick_seconds - elapsed))

    async def _control_tick(self) -> None:
        if not self._running:
            return
        if not self.cfg.autopilot_enabled:
            # Safety net: a stop that raced an in-flight hold must never leave a
            # key down.  This runs on the same loop, so it sees the final state.
            if self._injector is not None and (
                    self._injector.held_keys or self._injector.held_buttons):
                self._release_all()
            return
        now = time.monotonic()
        dt = min(2.0, max(0.0, now - self._last_tick_at)) if self._last_tick_at else 0.0
        self._last_tick_at = now
        self._tick_count += 1

        if self._session_expired():
            self._push_cue("CUE_PREFIX_DONE", prompts.t("SESSION_LIMIT", lang=self._lang))
            self.stop_playing(reason="session-limit")
            return

        running = self.ensure_running_game()
        if running is None:
            selected = next(
                (item for item in self._running_games
                 if self._game is not None and item.profile.id == self._game.id),
                None,
            )
            if selected is not None and selected.window is not None and \
                    not selected.window.is_usable(min_edge=self.cfg.min_capture_edge):
                self._push_problem(
                    "WINDOW_MINIMIZED",
                    f"window={selected.window.width}x{selected.window.height}, "
                    f"minimized={selected.window.minimized}",
                )
            else:
                self._announce_waiting()
            return

        if self._mode == "stopped":
            return

        if not self._is_game_foreground():
            if not self._away:
                self._away = True
                self._release_all()
                self._push_problem("NOT_FOCUSED")
            return
        if self._away:
            self._away = False
            self._event("foreground", "back")

        if self._mode in ("paused", "stopped"):
            self._release_all()
            return

        frame = await self._capture_frame()
        if frame is None:
            self._blind_ticks += 1
            if self._blind_ticks == max(2, self.cfg.blind_frame_limit // 2):
                self._push_problem("NO_CAPTURE", self._capture_error_detail())
            return

        features = self._analyzer.analyze(frame.image)
        hud = self._hud.observe(frame.image, features)
        self._latest_features = features
        self._latest_hud = hud

        if features.black:
            self._blind_ticks += 1
            if self._blind_ticks >= self.cfg.blind_frame_limit:
                self._release_all()
                if self._blind_ticks == self.cfg.blind_frame_limit:
                    self._push_problem("BLIND_CAPTURE", self._capture_error_detail())
                return
        else:
            self._blind_ticks = 0

        if self.cfg.yield_on_human_input and self._mode in ("playing", "soft_yield"):
            keys = self._human_keys()
            if keys:
                self._human_seen_at = time.time()
                if self._mode == "playing":
                    self._yield_to_human(keys)
                return
            if self._mode == "soft_yield":
                if (time.time() - self._human_seen_at) >= self.cfg.human_input_grace_seconds and \
                        not self._pause_latched:
                    self._mode = "playing"
                    self._push_cue("CUE_PREFIX_STARTED", prompts.t("RESUMED", lang=self._lang))
                return

        menu = discovery.menu_active(running.window.hwnd) if running.window else False
        if menu:
            self._menu_ticks += 1
            self._latest_decision = policy_mod.Decision(
                policy_mod.PolicyState.MENU,
                "游戏停在菜单/暂停画面，我先不按键。",
                [],
                ["menu"],
                0.8,
            )
            self._last_intent = self._latest_decision.intent
            self._handle_menu()
            return
        self._menu_ticks = 0

        if not self._has_entered_gameplay:
            self._gameplay_ticks += 1
            if self._gameplay_ticks >= 2:
                self._has_entered_gameplay = True
                self._event("gameplay", "started")
                self._push_cue(
                    "CUE_PREFIX_STARTED",
                    prompts.t("PLAYING", lang=self._lang, game=running.profile.name_zh),
                )

        if self.cfg.start_in_game_required and not self._has_entered_gameplay:
            return

        if self._mode == "soft_yield":
            return

        # Normal playing tick.
        if self._mode == "waiting":
            self._mode = "playing"
        self._played_seconds += dt

        decision = self._policy.decide(
            features, hud, now=time.time(), menu=False, playing=True
        ) if self._policy is not None else policy_mod.Decision(
            policy_mod.PolicyState.WAITING, "策略还没准备好。", [], ["no-policy"], 0.1)
        self._latest_decision = decision
        self._last_intent = decision.intent
        await self._run_ops(decision.ops)
        if decision.state in (policy_mod.PolicyState.STUCK, policy_mod.PolicyState.COMBAT):
            self._maybe_push_event(decision)

    async def _hotkey_loop(self) -> None:
        """F8 works even while the autopilot is stopped, so it can resume it."""
        while self._running:
            try:
                if self.cfg.pause_hotkey_enabled:
                    down = input_mod.KeyInjector.pause_hotkey_down()
                    if down and not self._last_hotkey_down:
                        if self._mode == "paused" and self._game is not None:
                            self.resume_playing(reason="hotkey")
                        elif self.cfg.autopilot_enabled:
                            self.stop_playing(reason="hotkey")
                    self._last_hotkey_down = down
                await asyncio.sleep(0.12)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._record_error(f"hotkey loop failed: {type(exc).__name__}: {exc}")
                await asyncio.sleep(0.5)

    async def _narration_loop(self) -> None:
        self._log("info", "narration loop started")
        while self._running:
            try:
                await asyncio.sleep(0.8)
                self._narration_tick()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._record_error(f"narration failed: {type(exc).__name__}: {exc}")
                await asyncio.sleep(1.0)

    def _narration_tick(self) -> None:
        frame = self._latest_frame
        if frame is None:
            return
        if self._mode not in ("playing", "soft_yield") or self._game is None:
            return
        if (time.time() - frame.captured_at) > 3.0:
            return
        now = time.time()
        if self.cfg.stream_frames_to_llm and \
                (now - self._last_stream_at) >= self.cfg.frame_stream_interval_seconds:
            self._last_stream_at = now
            if self._stream_frame(frame):
                self.frames_streamed += 1
        if (now - self._last_commentary_at) >= self.cfg.commentary_interval_seconds:
            self._last_commentary_at = now
            self._push_commentary(frame)

    # ------------------------------------------------------------------
    # menu / takeover / hotkey
    # ------------------------------------------------------------------

    def _human_keys(self) -> list[str]:
        try:
            return self._ensure_injector().physical_keys_down()
        except Exception:
            return []

    def _yield_to_human(self, keys: list[str]) -> None:
        self._mode = "soft_yield"
        self._human_seen_at = time.time()
        self._release_all()
        if (time.time() - self._takeover_announced_at) > 20.0:
            self._takeover_announced_at = time.time()
            detail = "、".join(keys[:3])
            self._push_cue(
                "CUE_PREFIX_STARTED",
                prompts.t("TAKEOVER", lang=self._lang, keys=detail),
            )
        self._event("takeover", ",".join(keys[:4]))

    def _handle_menu(self) -> None:
        running = self._running_game
        if not running:
            return
        if not self._has_entered_gameplay:
            now = time.time()
            if (now - self._menu_announced_at) > 45.0:
                self._menu_announced_at = now
                self._push_cue(
                    "CUE_PREFIX_STARTED",
                    prompts.t("MENU_WAITING", lang=self._lang, game=running.profile.name_zh),
                )
            return
        if not self.cfg.auto_resume_from_menu:
            return
        if self._menu_ticks < self.cfg.menu_stable_ticks:
            return
        if (time.time() - self._human_seen_at) < self.cfg.human_input_grace_seconds:
            return
        if (time.time() - self._last_menu_press_at) < 3.0:
            return
        # Escape closes a Source pause menu.  It is harmless on a main menu,
        # and unlike Enter it cannot start a new game.
        injector = self._ensure_injector()
        try:
            injector.tap("ESC", hold_seconds=0.06)
            self._last_menu_press_at = time.time()
            self._menu_ticks = 0
            self._event("menu-resume", "esc")
        except Exception as exc:
            self._note_input_blocked(exc)

    # ------------------------------------------------------------------
    # public control API
    # ------------------------------------------------------------------

    def play(self, game: catalog.GameProfile, *, objective: str = "") -> dict[str, Any]:
        self._game = game
        self._policy = policy_mod.PlayPolicy(
            game,
            look=policy_mod.LookCalibration(
                units_per_degree=self.cfg.look_units_per_degree,
                horizontal_fov=self.cfg.horizontal_fov,
                vertical_fov=self.cfg.vertical_fov,
                invert_y=self.cfg.invert_y,
            ),
            key_overrides=self.cfg.keys,
        )
        self._hud = hud_mod.HudTracker(style=game.hud_style)
        self._injector = input_mod.KeyInjector(
            logger=self._logger,
            require_foreground=self.cfg.require_foreground,
            foreground_check=self._is_game_foreground,
            dry_run=self.cfg.dry_run,
        )
        self._objective = objective or self.cfg.objective or self._default_objective(game)
        self._plan_steps = list(self.cfg.plan_steps)
        self._mode = "waiting"
        self._pause_latched = False
        self._adopted_pid = 0
        self._session_started_at = time.time()
        self._played_seconds = 0.0
        self._latest_frame = None
        self._latest_features = None
        self._latest_decision = None
        self._last_intent = ""
        self._last_ops = []
        self._running_game = None
        self._running_games = []
        self._last_scan_at = 0.0

        running = self.ensure_running_game(force=True)
        self.cfg.autopilot_enabled = True
        self._push_cue(
            "CUE_PREFIX_STARTED",
            prompts.t(
                "STARTED",
                lang=self._lang,
                game=game.name_zh,
                objective=self._objective,
            ),
        )
        self._event("play", game.id)
        return {
            "game": game.summary(),
            "running": running is not None,
            "mode": self._mode,
            "objective": self._objective,
            "dry_run": self.cfg.dry_run,
            "status": self.get_status(),
        }

    def stop_playing(self, *, reason: str = "user") -> dict[str, Any]:
        self.cfg.autopilot_enabled = False
        self._mode = "paused"
        self._pause_latched = True
        self._release_all()
        self._push_cue("CUE_PREFIX_DONE", prompts.t("STOPPED", lang=self._lang))
        self._event("stop", reason)
        return self.get_status()

    def resume_playing(self, *, reason: str = "user") -> dict[str, Any]:
        if self._game is None:
            return {"ok": False, "summary": "还没有选游戏；先说要玩哪一部。", "status": self.get_status()}
        self.cfg.autopilot_enabled = True
        self._pause_latched = False
        self._human_seen_at = 0.0
        self._mode = "playing" if self._running_game else "waiting"
        self._push_cue("CUE_PREFIX_STARTED", prompts.t("RESUMED", lang=self._lang))
        self._event("resume", reason)
        return {"ok": True, "summary": "好，我接着来。", "status": self.get_status()}

    def apply_command(self, text: str, *, seconds: float = 0.0) -> dict[str, Any]:
        command = policy_mod.parse_instruction(text, seconds=seconds)
        if not command.recognized:
            return {
                "accepted": False,
                "command": command.summary(),
                "summary": (
                    f"没听懂这个操作：{str(text)[:50]!r}。说得更具体一点，"
                    "比如「往前走」「打」「按 E」「蓝门」「停手」。"
                ),
            }
        if command.kind == "stop":
            self.stop_playing(reason="command")
            return {"accepted": True, "command": command.summary(),
                    "summary": "好，我停手，键盘还给你。"}
        if command.kind == "resume":
            result = self.resume_playing(reason="command")
            return {"accepted": True, "command": command.summary(),
                    "summary": result.get("summary", "我接着来。")}
        if command.kind == "mode":
            self.set_directive(
                mode=command.action,
                goal=command.text or command.action,
                steps="",
                seconds=command.seconds or 30.0,
            )
            return {"accepted": True, "command": command.summary(),
                    "summary": f"收到，按「{command.action}」模式来。"}

        if self._game is None:
            self._pending_commands.append(command)
            return {"accepted": True, "queued": True, "command": command.summary(),
                    "summary": "记下了；等你说是哪部游戏、并打开它，我就做。"}

        self._policy.set_directive(
            policy_mod.Directive(mode="command", text=text, seconds=0.0,
                                 command=command, issued_at=time.time())
        )
        ops = self._policy.command_ops(command) if self._policy else []
        return {
            "accepted": True,
            "queued": False,
            "command": command.summary(),
            "ops": [op.summary() for op in ops],
            "summary": f"收到，马上执行「{text}」。",
        }

    def set_directive(self, *, mode: str, goal: str, steps: str = "",
                      seconds: float = 0.0) -> dict[str, Any]:
        mode = mode if mode in ("explore", "fight", "puzzle", "vehicle", "wait") else "explore"
        self._objective = str(goal or "").strip() or self._objective
        if steps:
            self._plan_steps = [
                item.strip(" 0123456789.-、，,;；")
                for item in str(steps).replace("\n", "；").split("；")
                if item.strip()
            ][:8]
        if self._policy is not None:
            self._policy.set_directive(
                policy_mod.Directive(
                    mode=mode,
                    text=goal,
                    seconds=float(seconds or 30.0),
                    issued_at=time.time(),
                )
            )
        self._push_cue(
            "CUE_PREFIX_STARTED",
            prompts.t("PLAN", lang=self._lang, goal=self._objective,
                      steps="；".join(self._plan_steps) or "边打边看"),
        )
        return {
            "accepted": True,
            "mode": mode,
            "objective": self._objective,
            "steps": list(self._plan_steps),
            "summary": f"记下目标：{self._objective}。",
        }

    def aim(self, x: float, y: float, *, fire: bool = False,
            button: str = "left") -> dict[str, Any]:
        frame = self._latest_frame
        width = frame.width if frame is not None else 1600
        height = frame.height if frame is not None else 900
        xf, yf = float(x), float(y)
        if xf > 1.0 or yf > 1.0:
            xf = xf / max(1, width)
            yf = yf / max(1, height)
        xf = max(0.0, min(1.0, xf))
        yf = max(0.0, min(1.0, yf))
        look = policy_mod.LookCalibration(
            units_per_degree=self.cfg.look_units_per_degree,
            horizontal_fov=self.cfg.horizontal_fov,
            vertical_fov=self.cfg.vertical_fov,
            invert_y=self.cfg.invert_y,
        )
        dx, dy = look.counts_for_delta(xf, yf)
        injector = self._ensure_injector()
        try:
            injector.look(dx, dy)
            if fire:
                injector.click(button=button)
        except Exception as exc:
            return {"ok": False, "summary": f"瞄准失败：{type(exc).__name__}: {exc}"}
        return {
            "ok": True,
            "target": [round(xf, 3), round(yf, 3)],
            "counts": [round(dx, 1), round(dy, 1)],
            "fired": bool(fire),
            "summary": f"视角已转向 ({xf:.2f}, {yf:.2f})" + ("，并开了火。" if fire else "。"),
        }

    async def run_sequence(self, actions: Any) -> dict[str, Any]:
        """Execute a short multi-step plan atomically (LLM-friendly).

        Each item is one of: a parsed command ("往前走"), ``aim`` with x/y,
        ``shoot`` with portal, or ``wait``.  The total is capped so a single
        tool call can never hold the keyboard for an unbounded time, and a
        human takeover interrupts at the next 100 ms slice.
        """
        if self._game is None:
            return {"ok": False, "summary": "还没选游戏；先 source_play。"}
        if not self._running_game:
            return {"ok": False, "summary": "没看到正在运行的游戏窗口。"}
        if self._mode not in ("playing", "soft_yield"):
            return {"ok": False, "summary": f"现在不是可操作状态（{self._mode}）；先进入关卡并让我接手。"}
        if not isinstance(actions, (list, tuple)) or not actions:
            return {"ok": False, "summary": "actions 需要是一个非空数组。"}

        look = policy_mod.LookCalibration(
            units_per_degree=self.cfg.look_units_per_degree,
            horizontal_fov=self.cfg.horizontal_fov,
            vertical_fov=self.cfg.vertical_fov,
            invert_y=self.cfg.invert_y,
        )
        ops: list[policy_mod.Op] = []
        summary_steps: list[str] = []
        total = 0.0
        for raw in list(actions)[:12]:
            if not isinstance(raw, Mapping):
                continue
            action = str(raw.get("action") or "").strip().lower()
            if not action:
                continue
            seconds = _as_float(raw.get("seconds"), 0.0, low=0.0, high=5.0)
            seconds = min(seconds, max(0.05, 8.0 - total))
            if action == "wait":
                ops.append(policy_mod.Op("wait", seconds=max(0.05, seconds or 0.2)))
                summary_steps.append(f"等{max(0.05, seconds or 0.2):.1f}s")
                total += max(0.05, seconds or 0.2)
                continue
            if action == "aim":
                x = _as_float(raw.get("x"), 0.5, low=0.0, high=1.0)
                y = _as_float(raw.get("y"), 0.5, low=0.0, high=1.0)
                dx, dy = look.counts_for_delta(x, y)
                ops.append(policy_mod.Op("look", dx=dx, dy=dy))
                summary_steps.append(f"瞄({x:.2f},{y:.2f})")
                total += 0.1
                continue
            if action in ("shoot", "fire", "portal_blue", "portal_orange",
                          "blue", "orange", "left", "right"):
                if action in ("portal_blue", "blue", "left"):
                    button = "left"
                elif action in ("portal_orange", "orange", "right"):
                    button = "right"
                else:
                    button = "left"
                ops.append(policy_mod.Op("click", button=button))
                summary_steps.append("开火" if action in ("shoot", "fire") else f"{button}门")
                total += 0.08
                continue
            command = policy_mod.parse_instruction(action, seconds=seconds)
            if not command.recognized:
                continue
            built = self._policy.command_ops(command) if self._policy else []
            ops.extend(built)
            summary_steps.append(action)
            total += max(0.1, seconds or 0.3)
            if total >= 8.0:
                break

        if not ops:
            return {"ok": False, "summary": "这些动作都没听懂，什么也没按。"}
        await self._run_ops(ops)
        interrupted = self._mode == "soft_yield"
        return {
            "ok": True,
            "steps": summary_steps,
            "ops": [op.summary() for op in ops],
            "interrupted": interrupted,
            "summary": (
                "动作序列执行到一半，你接手了，我就停了。"
                if interrupted else
                f"做完了 {len(summary_steps)} 个动作：" + " → ".join(summary_steps)
            ),
        }

    def shoot(self, portal: str = "primary") -> dict[str, Any]:
        mapping = {
            "blue": "left", "left": "left", "primary": "left", "1": "left", "mouse1": "left",
            "orange": "right", "right": "right", "secondary": "right", "2": "right", "mouse2": "right",
        }
        button = mapping.get(str(portal).strip().lower(), "left")
        injector = self._ensure_injector()
        try:
            injector.click(button=button)
        except Exception as exc:
            return {"ok": False, "summary": f"开火失败：{type(exc).__name__}: {exc}"}
        label = "蓝门/左键" if button == "left" else "橙门/右键"
        return {"ok": True, "button": button, "summary": f"按下了{label}。"}

    # ------------------------------------------------------------------
    # status / diagnostics
    # ------------------------------------------------------------------

    def _capture_error_detail(self) -> str:
        if self._capturer is None:
            return "capturer not created yet"
        return str(getattr(self._capturer, "last_error", "") or "capture returned no frame")

    def _session_expired(self) -> bool:
        if self.cfg.max_session_minutes <= 0 or not self._session_started_at:
            return False
        return self._played_seconds > self.cfg.max_session_minutes * 60.0

    def _default_objective(self, game: catalog.GameProfile | None = None) -> str:
        if game is None:
            game = self._game
        if game is not None and game.family == "portal":
            return "找到出口，解开当前测试室"
        return "沿主线推进，清掉挡路的敌人，找到下一扇门"

    def get_status(self) -> dict[str, Any]:
        # Keep the status honest even before source_play has adopted anything:
        # the user may have just opened the game to show it to the catgirl.
        if (time.time() - self._last_scan_at) > 2.0:
            self._scan_running(force=True)
        running = self._running_game
        window = running.window if running else None
        policy_state = self._latest_decision.state.value if self._latest_decision else "waiting"
        status = {
            "ok": True,
            "selected_game": self._game.summary() if self._game else None,
            "objective": self._objective,
            "plan_steps": list(self._plan_steps),
            "installed": [
                catalog.GAME_BY_ID[gid].summary()
                for gid in self.installed_ids() if gid in catalog.GAME_BY_ID
            ],
            "running": [game.summary() for game in self._running_games],
            "window": window.summary() if window else None,
            "focused": self._is_game_foreground(),
            "cursor_visible": discovery.cursor_visible(),
            "menu_active": discovery.menu_active(window.hwnd) if window else None,
            "capture": self._capturer.status() if self._capturer is not None else None,
            "autopilot_enabled": self.cfg.autopilot_enabled,
            "mode": self._mode,
            "dry_run": self.cfg.dry_run,
            "policy": {
                "state": policy_state,
                "intent": self._last_intent,
                "last_ops": [op.summary() for op in self._last_ops],
            },
            "features": self._latest_features.summary() if self._latest_features else None,
            "hud": self._latest_hud.summary(),
            "session_seconds": round(self._played_seconds, 1),
            "ticks": self._tick_count,
            "frames_seen": self._frames_seen,
            "frames_streamed": self.frames_streamed,
            "commentaries": self.commentaries,
            "takeover": {
                "mode": self._mode,
                "human_grace_seconds": self.cfg.human_input_grace_seconds,
                "pause_hotkey": "F8" if self.cfg.pause_hotkey_enabled else None,
            },
            "events": list(self._events[-12:]),
            "last_error": self._errors[-1] if self._errors else "",
            "errors": list(self._errors[-5:]),
        }
        game_name = self._game.name_zh if self._game else "未选游戏"
        status["summary"] = (
            f"{game_name}：mode={self._mode} autopilot={self.cfg.autopilot_enabled} "
            f"running={len(self._running_games)} focused={status['focused']} "
            f"cursor={status['cursor_visible']} state={policy_state} "
            f"played={status['session_seconds']}s "
            f"last={self._last_intent or 'idle'}"
        )
        return status

    async def selfcheck(self) -> dict[str, Any]:
        self._scan_running(force=True)
        problems: list[str] = []
        checks: dict[str, Any] = {}
        installed = self.installed_ids()
        checks["installed"] = [
            {"id": gid, "name": catalog.GAME_BY_ID[gid].name_zh}
            for gid in installed if gid in catalog.GAME_BY_ID
        ]
        checks["running"] = [game.summary() for game in self._running_games]
        if not installed:
            problems.append("没有在 Steam 库里找到半条命/传送门系列；确认 Steam 库路径可读。")

        running = self._running_game or self.ensure_running_game(force=True)
        checks["selected"] = self._game.id if self._game else None
        if self._game is not None and running is None:
            problems.append(f"《{self._game.name_zh}》没有运行：需要主人先手动打开并进入关卡。")
        if running is not None:
            checks["window"] = running.summary()
            if running.window is None:
                problems.append("进程在跑，但没找到可用的游戏窗口。")
            elif not running.window.is_usable(min_edge=self.cfg.min_capture_edge):
                problems.append(
                    f"窗口不可用（可能最小化/太小）：{running.window.rect}；"
                    "插件不会对着 1x1 的画面按键。"
                )
            checks["foreground"] = self._is_game_foreground()
            if not checks["foreground"]:
                problems.append("游戏窗口不在最前面；插件只会等待，不会往别的窗口按键。")
            checks["cursor_visible"] = discovery.cursor_visible()
            checks["menu_active"] = discovery.menu_active(running.window.hwnd)

        try:
            capturer = self._ensure_capturer()
            checks["capture"] = capturer.probe()
            if running is not None:
                region = self._capture_region()
                frame = capturer.capture(region, retries=2)
                if frame is None:
                    problems.append("截图失败：" + self._capture_error_detail())
                else:
                    features = self._analyzer.analyze(frame.image)
                    checks["frame"] = frame.summary()
                    checks["features"] = features.summary()
                    self._latest_frame = frame
                    self._latest_features = features
                    if features.black:
                        problems.append(
                            "截到的画面是全黑的：独占全屏下请优先使用 dxgi 后端；"
                            "窗口/无边框模式则 GDI 也可用。"
                        )
        except Exception as exc:
            checks["capture"] = {"error": f"{type(exc).__name__}: {exc}"}
            problems.append(f"截图后端初始化失败：{type(exc).__name__}: {exc}")

        injector = self._ensure_injector()
        checks["input"] = injector.stats()
        if not injector.available:
            problems.append("SendInput 不可用（非 Windows 或 user32 加载失败）。")
        if not injector.layout_is_valid():
            problems.append("INPUT 结构体大小不符合 ABI，注入会失败。")
        checks["config"] = self.cfg.as_dict()
        checks["dry_run"] = self.cfg.dry_run
        checks["ok"] = not problems
        checks["problems"] = problems
        if self.cfg.dry_run:
            checks["dry_run_note"] = "dry_run 开着：只观察、不按键；要说「开始玩」并关掉 dry_run 才会真正操作。"
        return checks

    def look(self, note: str = "") -> dict[str, Any]:
        if not self._running:
            return {"ok": False, "summary": "服务还没启动。"}
        running = self.ensure_running_game(force=True)
        if running is None or running.window is None:
            return {
                "ok": False,
                "summary": "没看到游戏窗口。主人要先手动打开游戏并进入关卡。",
            }
        if not self._is_game_foreground():
            return {
                "ok": False,
                "summary": "游戏窗口不在最前面，现在截图会截到别的东西；先把游戏切回前台。",
            }
        frame = self._latest_frame
        if frame is None or (time.time() - frame.captured_at) > 1.0:
            try:
                frame = self._ensure_capturer().capture(self._capture_region(), retries=2)
            except Exception as exc:
                return {"ok": False, "summary": f"截图失败：{type(exc).__name__}: {exc}"}
        if frame is None:
            return {"ok": False, "summary": "截图失败：" + self._capture_error_detail()}
        self._latest_frame = frame
        features = self._analyzer.analyze(frame.image)
        hud = self._hud.observe(frame.image, features)
        self._latest_features = features
        self._latest_hud = hud
        if self.cfg.stream_frames_to_llm:
            self._stream_frame(frame)
        detail = vision_mod.frame_features_summary(features)
        return {
            "ok": True,
            "note": str(note or ""),
            "game": self._game.name_zh if self._game else None,
            "features": features.summary(),
            "hud": hud.summary(),
            "summary": f"看了一眼《{self._game.name_zh if self._game else '游戏'}》：{detail}。",
        }

    # ------------------------------------------------------------------
    # LLM-facing prompts
    # ------------------------------------------------------------------

    def _default_objective_text(self) -> str:
        if self._objective:
            return self._objective
        if self._game is None:
            return "还没选游戏"
        return self._default_objective(self._game)

    def _situation_text(self) -> str:
        running = self._running_game
        game_name = self._game.name_zh if self._game else "未选游戏"
        features = self._latest_features
        hud = self._latest_hud
        decision = self._latest_decision
        lines = [
            f"游戏：{game_name}",
            f"状态：{self._mode} / {decision.state.value if decision else 'waiting'}",
            f"目标：{self._default_objective_text()}",
            f"画面：{vision_mod.frame_features_summary(features)}",
            f"HUD：{'有' if hud.present else '没确认'}，字幕{'有' if hud.subtitle else '无'}，"
            f"受伤提示={'有' if hud.damage_flash else '无'}",
            f"我的判断：{decision.intent if decision else '先观察一下'}",
        ]
        if decision is not None and decision.ops:
            lines.append("刚才的操作：" + "；".join(op.summary() for op in decision.ops[:4]))
        if self._plan_steps:
            lines.append("计划：" + " → ".join(self._plan_steps[:4]))
        if running is not None and running.window is not None:
            lines.append(f"窗口：{running.window.width}x{running.window.height}，"
                         f"{'在前台' if self._is_game_foreground() else '不在前台'}")
        return "\n".join(lines)

    def _push_parts(self, parts: list[dict[str, Any]], *, ai_behavior: str,
                    priority: int, coalesce_key: str, visibility=None) -> bool:
        if self._push is None:
            return False
        try:
            self._push(
                source="source_player",
                visibility=list(visibility if visibility is not None else []),
                ai_behavior=ai_behavior,
                parts=parts,
                priority=priority,
                coalesce_key=coalesce_key,
            )
            return True
        except Exception as exc:
            self._log("debug", "push failed: {}", exc)
            return False

    def _push_cue(self, prefix_key: str, body: str) -> bool:
        text = prompts.t(prefix_key, lang=self._lang) + "\n" + body
        return self._push_parts([{"type": "text", "text": text}],
                                ai_behavior="respond", priority=7,
                                coalesce_key="source_cue")

    def _stream_frame(self, frame: capture_mod.Frame) -> bool:
        payload = capture_mod.encode_jpeg(
            frame.image,
            max_edge=self.cfg.frame_max_edge_px,
            quality=self.cfg.frame_jpeg_quality,
            max_bytes=self.cfg.frame_max_bytes,
        )
        if not payload:
            return False
        return self._push_parts(
            [{"type": "image", "data": payload, "mime": "image/jpeg"}],
            ai_behavior="read", priority=2, coalesce_key="source_frame",
        )

    def _push_commentary(self, frame: capture_mod.Frame) -> None:
        if self._game is None:
            return
        text = prompts.commentary(
            lang=self._lang,
            situation=self._situation_text(),
            game=self._game.name_zh,
            objective=self._default_objective_text(),
        )
        parts: list[dict[str, Any]] = [{"type": "text", "text": text}]
        payload = capture_mod.encode_jpeg(
            frame.image,
            max_edge=self.cfg.frame_max_edge_px,
            quality=self.cfg.frame_jpeg_quality,
            max_bytes=self.cfg.frame_max_bytes,
        )
        if payload:
            parts.append({"type": "image", "data": payload, "mime": "image/jpeg"})
        if self._push_parts(parts, ai_behavior="respond", priority=4,
                            coalesce_key="source_commentary"):
            self.commentaries += 1
            self._event("commentary", self._last_intent)

    def _maybe_push_event(self, decision: policy_mod.Decision) -> None:
        now = time.time()
        key = decision.state.value
        if key == "combat" and (now - self._last_commentary_at) > 8.0:
            self._push_cue("CUE_PREFIX_STARTED", prompts.t(
                "COMBAT", lang=self._lang, intent=decision.intent))
            self._last_commentary_at = now
        elif key == "stuck" and (now - self._last_commentary_at) > 15.0:
            self._push_cue("CUE_PREFIX_STARTED", prompts.t(
                "STUCK", lang=self._lang, intent=decision.intent))
            self._last_commentary_at = now

    def _announce_waiting(self) -> None:
        now = time.time()
        if (now - self._waiting_announced_at) < self.cfg.problem_cooldown_seconds:
            return
        self._waiting_announced_at = now
        game_name = self._game.name_zh if self._game else "半条命/传送门"
        self._push_cue(
            "CUE_PREFIX_STARTED",
            prompts.t("WAITING", lang=self._lang, game=game_name),
        )

    def _push_problem(self, key: str, detail: str = "") -> None:
        now = time.time()
        # One problem cue per cooldown window, regardless of which problem it is.
        # Otherwise a dry-run refusal on every tick alternates between two keys
        # and defeats the per-key throttle (observed as 11 identical cues in 3s).
        if (now - self._last_problem_at) < self.cfg.problem_cooldown_seconds:
            return
        self._last_problem_at = now
        self._last_problem_key = key
        self._push_cue("CUE_PREFIX_STARTED", prompts.t(key, lang=self._lang, detail=detail or "unknown"))
