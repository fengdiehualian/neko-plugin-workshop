"""可选拉起/停止 vendored 数据层进程。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .windows_process_tree import WindowsProcessTree

if __package__ == "adapters":  # standalone release-tool execution
    from build_info import DATA_LAYER_VERSION
    from core.contracts import CbqConfig
    from core.game_process import ProcessWatcher
else:
    from ..build_info import DATA_LAYER_VERSION
    from ..core.contracts import CbqConfig
    from ..core.game_process import ProcessWatcher


_DATA_LAYER_SERVICE = "neko_calabiqiu_vision"
_DATA_LAYER_CONFIG_FIELDS = (
    "data_layer_url",
    "data_layer_auto_start",
    "assistant_directory",
    "data_layer_infer_mode",
    "data_layer_infer_backend",
    "data_layer_device",
    "data_layer_infer_interval_seconds",
    "data_layer_stale_targets_seconds",
    "data_layer_weights",
    "data_layer_log_path",
    "data_layer_classifier",
    "data_layer_classifier_template_dir",
    "data_layer_classifier_confirm",
    "data_layer_ocr_interval_seconds",
    "data_layer_ocr_timeout_seconds",
    "self_filter_enabled",
)


def _file_name(value: object) -> str:
    return str(value or "").strip().replace("\\", "/").rsplit("/", 1)[-1]


@dataclass(frozen=True, slots=True)
class DataLayerHealth:
    reachable: bool
    compatible: bool
    reason: str
    payload: dict[str, Any]


def inspect_data_layer_health(
    base_url: str,
    timeout: float,
    *,
    expected_weights: str = "",
    expected_infer_mode: str = "",
) -> DataLayerHealth:
    url = f"{base_url.rstrip('/')}/api/health"
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if not 200 <= int(getattr(resp, "status", 200)) < 300:
                return DataLayerHealth(False, False, "http_error", {})
            payload = json.loads(resp.read(64 * 1024).decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError, UnicodeError, json.JSONDecodeError):
        return DataLayerHealth(False, False, "unreachable", {})

    if not isinstance(payload, dict):
        return DataLayerHealth(True, False, "invalid_payload", {})
    if payload.get("service") != _DATA_LAYER_SERVICE:
        return DataLayerHealth(True, False, "service_mismatch", payload)
    if str(payload.get("service_version") or "") != DATA_LAYER_VERSION:
        return DataLayerHealth(True, False, "version_mismatch", payload)
    if not str(payload.get("service_instance_id") or "").strip():
        return DataLayerHealth(True, False, "instance_id_missing", payload)

    expected_model = _file_name(expected_weights)
    actual_model = _file_name(payload.get("weights_id"))
    fallback_from = _file_name(payload.get("weights_fallback_from"))
    infer_mode = str(payload.get("infer_mode") or "").strip().lower()
    expected_mode = str(expected_infer_mode or "").strip().lower()
    if expected_mode and infer_mode != expected_mode:
        return DataLayerHealth(True, False, "model_mismatch", payload)
    if expected_mode == "stub":
        return DataLayerHealth(True, True, "ok", payload)
    if expected_mode == "yolo" and (
        not expected_model or payload.get("model_loaded") is not True
    ):
        return DataLayerHealth(True, False, "model_mismatch", payload)
    if expected_model == "stub":
        if infer_mode != "stub":
            return DataLayerHealth(True, False, "model_mismatch", payload)
    elif expected_model and actual_model != expected_model:
        expected_fallback = (
            "best.nekomodel" if expected_model.lower().endswith(".nekomodel") else "best.onnx"
        )
        if fallback_from != expected_model or actual_model != expected_fallback:
            return DataLayerHealth(True, False, "model_mismatch", payload)
    return DataLayerHealth(True, True, "ok", payload)


def check_data_layer_health(
    base_url: str,
    timeout: float,
    *,
    expected_weights: str = "",
    expected_infer_mode: str = "",
) -> bool:
    return inspect_data_layer_health(
        base_url,
        timeout,
        expected_weights=expected_weights,
        expected_infer_mode=expected_infer_mode,
    ).compatible


def _resolve_python(plugin_root: Path) -> Path:
    win = plugin_root / ".venv-infer" / "Scripts" / "python.exe"
    if win.is_file():
        return win
    posix = plugin_root / ".venv-infer" / "bin" / "python"
    if posix.is_file():
        return posix
    return Path(sys.executable)


def _spawn_assistant_tree(args: list[str], *, cwd: str) -> WindowsProcessTree:
    from .windows_process_tree import WindowsProcessTree

    return WindowsProcessTree(args, cwd=cwd, env={**os.environ, "PYTHONIOENCODING": "utf-8"})


class DataLayerProcessManager:
    def __init__(self, config: CbqConfig, *, plugin_root: Path, external_only: bool = False) -> None:
        self._lock = threading.RLock()
        self.config = config
        self.plugin_root = Path(plugin_root)
        self._external_only = external_only
        self._game_watcher = ProcessWatcher(debounce=2)
        self._game_poll_at: float | None = None
        self._game_paused = False
        self._game_watch_error: str | None = None
        self._process: subprocess.Popen[Any] | WindowsProcessTree | None = None
        self._mode = "unknown"
        self._started_by_plugin = False
        self._last_error: str | None = None
        self._launch_cmd: list[str] = []
        self._spawned_at: float = 0.0
        # 崩溃自动重启状态
        self._restart_attempts = 0
        self._restart_backoff = 0.0
        self._restart_cooldown_until = 0.0
        self._health_reason = "unchecked"
        self._health_payload: dict[str, Any] = {}
        self._pending_scene_instance_id = ""
        self._last_snapshot: dict[str, Any] = {}
        self.snapshot()

    def configure(self, config: CbqConfig) -> None:
        with self._lock:
            self.config = config
            self._pending_scene_instance_id = ""
            self._reset_restart_state()

    @staticmethod
    def config_signature(config: CbqConfig) -> tuple[object, ...]:
        return tuple(getattr(config, field) for field in _DATA_LAYER_CONFIG_FIELDS)

    def _expected_weights(self) -> str:
        if self._expected_infer_mode() == "stub":
            return "stub"
        if self._external_only:
            return self.config.data_layer_weights or "best-v8-640.nekomodel"
        weights = self._resolve_weights()
        return str(weights or "")

    def _expected_infer_mode(self) -> str:
        infer_mode = (self.config.data_layer_infer_mode or "auto").strip().lower()
        if self._external_only:
            return "stub" if infer_mode == "stub" else "yolo"
        weights = self._resolve_weights()
        if infer_mode == "stub" or (infer_mode == "auto" and weights is None):
            return "stub"
        return "yolo"

    def _resolve_weights(self) -> Path | None:
        # Only legacy bundled launches and the standalone assistant need local files.
        # The market plugin does not contain the data_layer package.
        if __package__ == "adapters":
            from data_layer.config import resolve_weights_path
        else:
            from ..data_layer.config import resolve_weights_path
        return resolve_weights_path(self.plugin_root, self.config.data_layer_weights)

    def _inspect_health(self) -> DataLayerHealth:
        result = inspect_data_layer_health(
            self.config.data_layer_url,
            self.config.http_timeout_seconds,
            expected_weights=self._expected_weights(),
            expected_infer_mode=self._expected_infer_mode(),
        )
        self._health_reason = result.reason
        self._health_payload = dict(result.payload)
        return result

    def _mark_incompatible(self, health: DataLayerHealth) -> dict[str, Any]:
        self._pending_scene_instance_id = ""
        self._terminate_owned_process()
        self._mode = "incompatible"
        self._last_error = f"data_layer_{health.reason}"
        return self.snapshot()

    def _terminate_owned_process(self) -> bool:
        if not self._started_by_plugin or self._process is None:
            return False
        try:
            self._process.terminate()
            self._process.wait(timeout=self.config.data_layer_shutdown_timeout_seconds)
        except subprocess.TimeoutExpired:
            if self._external_only and sys.platform == "win32":
                raise
            self._process.kill()
            self._process.wait(timeout=self.config.data_layer_shutdown_timeout_seconds)
        self._process = None
        self._started_by_plugin = False
        self._spawned_at = 0.0
        return True

    def validate_assistant_directory(self, directory: str) -> Path:
        """Validate the separately extracted Windows bundle without importing it."""
        root = Path(directory.strip().strip('"')).expanduser()
        if not directory.strip() or not root.is_absolute():
            raise ValueError("请填写助手解压目录的完整路径。")
        root = root.resolve(strict=True)
        for relative in (
            ".venv-infer/Scripts/python.exe", ".venv-infer/pyvenv.cfg",
            "runtime/python/python.exe", "tools/start_data_layer.py",
            "tools/rebind_venv.ps1", "config/plugin.toml", "data_layer/__main__.py",
        ):
            if not (root / relative).is_file():
                raise ValueError(f"助手目录不完整，缺少 {relative}。请选完整解压后的目录。")
        with (root / "config/plugin.toml").open("rb") as handle:
            settings = tomllib.load(handle).get("neko_calabiqiu", {})
        def local_port(url: str) -> int:
            parsed = urllib.parse.urlsplit(url)
            if (parsed.scheme != "http" or parsed.hostname not in {"localhost", "127.0.0.1"}
                    or parsed.username is not None or parsed.password is not None
                    or parsed.path not in {"", "/"} or parsed.query or parsed.fragment):
                raise ValueError("进程控制仅支持本机 HTTP 地址。")
            return parsed.port or 80
        if local_port(str(settings.get("data_layer_url") or "http://127.0.0.1:8212")) != local_port(self.config.data_layer_url):
            raise ValueError("助手与插件的端口不一致，请检查两边的 data_layer_url。")
        return root

    def control(self, operation: str) -> dict[str, Any]:
        with self._lock:
            if operation not in {"start", "stop", "restart"}:
                raise ValueError("未知的助手操作。")
            if operation != "start" and not self._started_by_plugin and self._inspect_health().reachable:
                raise ValueError("已有从外部启动的助手占用端口。首次切换请先关闭旧助手，再由面板启动；之后即可直接在面板停止和重启。")
            self._game_paused = operation == "stop" and self._external_only and self.config.data_layer_auto_start
            if operation in {"stop", "restart"}:
                self.stop()
            if operation == "stop":
                self._mode = "stopped"
                self._last_error = None
                return self.snapshot()
            self._reset_restart_state()
            return self._start_if_needed_locked(explicit=True)

    def sync_game(self) -> bool:
        """Use the existing plugin tick to own the assistant only during a game."""
        with self._lock:
            if not self._external_only or not self.config.data_layer_auto_start:
                return True
            if not self.config.enabled:
                self.stop()
                return False
            now = time.monotonic()
            sampled = self._game_poll_at is None or now - self._game_poll_at >= 1.0
            if sampled:
                self._game_poll_at = now
                try:
                    game = self._game_watcher.poll(now)
                except OSError:
                    self._game_watch_error = "无法读取游戏进程，已暂停自动启停，保留当前助手状态。"
                    return False
                self._game_watch_error = None
                if game.edge == "game_started":
                    self._game_paused = False
                    self._mode = "starting" if self._started_by_plugin else "unknown"
                    self._reset_restart_state()
                elif game.edge == "game_stopped":
                    self._game_paused = False
            if self._game_watch_error:
                return False
            if not self._game_watcher.running:
                if self._started_by_plugin:
                    if not sampled:
                        return False
                    try:
                        self.stop()  # Only stops a process this manager owns.
                    except (OSError, subprocess.SubprocessError):
                        self._mode = "failed"
                        self._last_error = "游戏已退出，但助手停止失败；将重试，也可在面板点击停止助手。"
                        return False
                if not self._game_paused:
                    self._mode = "waiting_game"
                return False
            if self._game_paused:
                return False
            if self._mode in {"unknown", "waiting_game"}:
                self._start_if_needed_locked()
            return True

    def _reset_restart_state(self) -> None:
        self._restart_attempts = 0
        self._restart_backoff = 0.0
        self._restart_cooldown_until = 0.0

    def _build_cmd(self) -> list[str]:
        if self._external_only:
            raise RuntimeError("external assistant must be started separately")
        py = _resolve_python(self.plugin_root)
        parsed = urllib.parse.urlsplit(self.config.data_layer_url)
        try:
            port = parsed.port
        except ValueError as exc:
            raise ValueError("managed_data_layer_url_has_invalid_port") from exc
        host = (parsed.hostname or "").lower()
        if (
            parsed.scheme.lower() != "http"
            # ThreadingHTTPServer currently binds AF_INET only. Reject ::1
            # until the server selects AF_INET6 explicitly.
            or host not in {"127.0.0.1", "localhost"}
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.path not in ("", "/")
        ):
            raise ValueError("managed_data_layer_url_must_be_local_http_origin")
        port = 80 if port is None else port
        weights = self._resolve_weights()
        infer_mode = (self.config.data_layer_infer_mode or "auto").strip().lower()
        if infer_mode == "auto" and weights is None:
            infer_mode = "stub"
        cmd = [
            str(py),
            "-m",
            "data_layer",
            "--host",
            host,
            "--port",
            str(port),
            "--infer-mode",
            infer_mode,
        ]
        infer_backend = (self.config.data_layer_infer_backend or "auto").strip().lower()
        infer_device = (self.config.data_layer_device or "auto").strip().lower()
        if infer_mode != "stub":
            cmd.extend(["--backend", infer_backend or "auto"])
            cmd.extend(["--device", infer_device or "auto"])
        infer_interval = getattr(self.config, "data_layer_infer_interval_seconds", None)
        if infer_interval:
            cmd.extend(["--infer-interval", str(float(infer_interval))])
        stale_targets = getattr(self.config, "data_layer_stale_targets_seconds", None)
        if stale_targets is not None:
            cmd.extend(["--stale-targets", str(float(stale_targets))])
        if weights is not None and infer_mode != "stub":
            # 传相对路径，减少日志/快照里泄露开发机绝对路径
            try:
                rel = weights.relative_to(self.plugin_root.resolve())
                warg = str(rel)
            except ValueError:
                warg = str(weights)
            cmd.extend(["--weights", warg])
        if not self.config.self_filter_enabled:
            cmd.append("--no-self-filter")
        if self.config.data_layer_log_path.strip():
            # 显式日志路径（无屏信号源）；相对 plugin_root 时转绝对路径
            log_path = Path(self.config.data_layer_log_path)
            if not log_path.is_absolute():
                log_path = self.plugin_root / log_path
            cmd.extend(["--log-path", str(log_path)])
        # 画面状态识别（选人/死亡/胜负）
        classifier = (self.config.data_layer_classifier or "disabled").strip().lower()
        if classifier in ("heuristic", "ocr", "hybrid", "templates"):
            cmd.extend(["--classifier", classifier])
            if (
                classifier in ("templates", "hybrid")
                and self.config.data_layer_classifier_template_dir.strip()
            ):
                tdir = Path(self.config.data_layer_classifier_template_dir)
                if not tdir.is_absolute():
                    tdir = self.plugin_root / tdir
                cmd.extend(["--classifier-template-dir", str(tdir)])
            cmd.extend(["--classifier-confirm", str(int(self.config.data_layer_classifier_confirm))])
            ocr_interval = getattr(self.config, "data_layer_ocr_interval_seconds", None)
            if ocr_interval is not None:
                cmd.extend(["--ocr-interval", str(float(ocr_interval))])
            ocr_timeout = getattr(self.config, "data_layer_ocr_timeout_seconds", None)
            if ocr_timeout is not None:
                cmd.extend(["--ocr-timeout", str(float(ocr_timeout))])
        return cmd

    def _refresh_starting(self) -> None:
        if self._mode == "managed" and self._started_by_plugin and self._process is not None:
            if self._process.poll() is not None:
                self._mode = "failed"
                self._last_error = "助手进程已退出，请点击重启助手重试。"
        if self._mode != "starting":
            return
        health = self._inspect_health()
        if health.compatible:
            self._mode = "managed"
            self._last_error = None
            self._reset_restart_state()
            return
        if health.reachable:
            self._mark_incompatible(health)
            return
        if self._process is not None and self._process.poll() is not None:
            self._mode = "failed"
            self._last_error = "process_exited_before_healthy"
            return
        # 超过配置超时仍未健康：保持 starting/managed 语义，标记超时但不阻塞宿主
        if self._spawned_at and (
            time.monotonic() - self._spawned_at > self.config.data_layer_startup_timeout_seconds
        ):
            self._last_error = "startup_timeout_still_waiting"

    def restart_if_crashed(self) -> dict[str, Any] | None:
        with self._lock:
            return self._restart_if_crashed_locked()

    def _restart_if_crashed_locked(self) -> dict[str, Any] | None:
        """检测崩溃并自动重启（指数退避）。返回 None 表示无需重启。"""
        now = time.monotonic()
        if now < self._restart_cooldown_until:
            return None
        # 只尝试重启由插件启动的进程
        if not self._started_by_plugin:
            return None
        # 检查是否已死
        if self._process is not None and self._process.poll() is None:
            # 进程仍在运行
            return None
        health = self._inspect_health()
        if health.compatible:
            # 健康检查通过，不需要重启
            self._mode = "external"
            self._started_by_plugin = False
            self._process = None
            self._reset_restart_state()
            return self.snapshot()
        if health.reachable:
            return self._mark_incompatible(health)
        # 尝试重启
        self._restart_attempts += 1
        if self._restart_attempts > 3:
            self._last_error = f"restart_exceeded_max_attempts({self._restart_attempts})"
            self._restart_cooldown_until = now + 30.0 * 60.0  # 30 分钟后重试
            # The long cooldown ends the current retry cycle. Reset the attempt
            # counter so the next cycle can actually spawn again.
            self._restart_attempts = 0
            self._restart_backoff = 0.0
            return None
        self._restart_backoff = min(2.0 ** self._restart_attempts, 8.0)  # 2s, 4s, 8s
        self._restart_cooldown_until = now + self._restart_backoff
        self._last_error = f"restart_attempt_{self._restart_attempts}"
        self._process = None
        self._mode = "unknown"
        return self.start_if_needed()

    def start_if_needed(self) -> dict[str, Any]:
        with self._lock:
            return self._start_if_needed_locked()

    def _start_if_needed_locked(self, *, explicit: bool = False) -> dict[str, Any]:
        """拉起数据层但不等待模型冷启动（避免宿主生命周期超时）。"""
        if self._external_only and self.config.data_layer_auto_start:
            if self._game_paused and not explicit:
                if not self._started_by_plugin:
                    self._mode = "stopped"
                return self.snapshot(refresh=False)
            if not self.config.enabled or not self._game_watcher.running or self._game_watch_error:
                if not self._started_by_plugin:
                    self._mode = "waiting_game"
                    self._last_error = None
                return self.snapshot(refresh=False)
        health = self._inspect_health()
        if health.compatible:
            if explicit and not self._started_by_plugin:
                raise ValueError("已有从外部启动的助手。首次切换请先关闭旧助手，再使用面板启动。")
            if self._process is not None and self._process.poll() is not None:
                self._process = None
                self._started_by_plugin = False
            self._mode = "managed" if self._started_by_plugin else "external"
            self._last_error = None
            return self.snapshot()
        if health.reachable:
            return self._mark_incompatible(health)
        if self._started_by_plugin and self._process is not None and self._process.poll() is None:
            return self.snapshot()
        if self._started_by_plugin:
            self._process = None
            self._started_by_plugin = False
        if (not explicit and not self.config.data_layer_auto_start) or (
            self._external_only and not self.config.assistant_directory
        ):
            self._mode = "missing"
            self._last_error = "assistant_not_running" if self._external_only else None
            return self.snapshot()
        try:
            launch_root = self.plugin_root
            if self._external_only:
                if sys.platform != "win32":
                    raise ValueError("当前助手完整包仅支持 Windows。")
                launch_root = self.validate_assistant_directory(self.config.assistant_directory)
                subprocess.run(
                    ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                     "-File", str(launch_root / "tools/rebind_venv.ps1"), "-Root", str(launch_root)],
                    cwd=str(launch_root), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NO_WINDOW, check=True, timeout=20,
                )
                self._launch_cmd = [str(_resolve_python(launch_root)),
                                    str(launch_root / "tools/start_data_layer.py"), "--standalone"]
                self._process = _spawn_assistant_tree(self._launch_cmd, cwd=str(launch_root))
            else:
                self._launch_cmd = self._build_cmd()
                self._process = subprocess.Popen(
                    self._launch_cmd, cwd=str(launch_root),
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
            self._started_by_plugin = True
            self._mode = "starting"
            self._spawned_at = time.monotonic()
            self._last_error = None
        except Exception as exc:  # noqa: BLE001
            self._mode = "failed"
            self._last_error = f"{type(exc).__name__}:{exc}"
            return self.snapshot()

        if self._external_only:
            # Acknowledge process creation immediately. The existing poll loop
            # validates readiness; the UI action does not wait for model loading.
            return self.snapshot(refresh=False)

        # 短探活：若已在几百毫秒内起来则直接 managed；否则立即返回 starting
        for _ in range(5):
            health = self._inspect_health()
            if health.compatible:
                self._mode = "managed"
                return self.snapshot()
            if health.reachable:
                return self._mark_incompatible(health)
            if self._process is not None and self._process.poll() is not None:
                self._mode = "failed"
                self._last_error = "process_exited_before_healthy"
                return self.snapshot()
            time.sleep(0.1)
        return self.snapshot()

    def restart_for_config_change(self) -> dict[str, Any]:
        """Restart only a process owned by this plugin, then validate the new target."""
        with self._lock:
            was_owned = self._started_by_plugin
            self.stop()
            self._mode = "unknown"
            self._last_error = None
            return self._start_if_needed_locked(explicit=was_owned)

    def stop(self) -> dict[str, Any]:
        with self._lock:
            if self._terminate_owned_process():
                self._mode = "stopped"
                self._health_payload = {}
                self._pending_scene_instance_id = ""
                self._last_error = None
            return self.snapshot()

    def can_consume_scene(self) -> bool:
        """Only a health-validated data layer may feed the companion runtime."""
        with self._lock:
            self._refresh_starting()
            if self._external_only and self._mode in {"missing", "incompatible", "unknown"}:
                # The assistant may start or be replaced after the plugin starts.
                self._revalidate_scene_identity()
            if self._mode in {"unverified_external", "unverified_scene"}:
                self._revalidate_scene_identity()
            return self._mode in {"managed", "external"}

    def _revalidate_scene_identity(self) -> bool:
        """Revalidate service/model and, when known, the observed scene instance."""
        health = self._inspect_health()
        if not health.compatible:
            if health.reachable:
                self._mark_incompatible(health)
            return False
        health_instance_id = str(health.payload.get("service_instance_id") or "").strip()
        if (
            self._pending_scene_instance_id
            and health_instance_id != self._pending_scene_instance_id
        ):
            self._mode = "unverified_scene"
            self._health_reason = "instance_mismatch"
            self._last_error = "data_layer_instance_mismatch"
            return False
        self._mode = "managed" if self._started_by_plugin else "external"
        self._pending_scene_instance_id = ""
        self._last_error = None
        return True

    def validate_scene_instance(self, instance_id: str) -> bool:
        """Ensure a scene belongs to the exact service instance validated by health."""
        with self._lock:
            observed = str(instance_id or "").strip()
            validated = str(self._health_payload.get("service_instance_id") or "").strip()
            if self._mode in {"managed", "external"} and observed and observed == validated:
                return True
            self._pending_scene_instance_id = observed
            self._mode = "unverified_scene"
            if not observed:
                self._health_reason = "instance_id_missing"
                self._last_error = "data_layer_instance_id_missing"
                return False
            return self._revalidate_scene_identity()

    def invalidate_external_after_disconnect(self) -> bool:
        """Require a fresh identity check after an external scene endpoint disconnects."""
        with self._lock:
            if self._mode != "external":
                return False
            self._mode = "unverified_external"
            self._pending_scene_instance_id = ""
            self._last_error = "external_scene_disconnected"
            self._health_reason = "scene_disconnected"
            self._health_payload = {}
            return True

    def snapshot(self, *, refresh: bool = True) -> dict[str, Any]:
        # A hosted UI context has a short deadline. During interpreter setup or
        # shutdown, serve the last complete snapshot instead of blocking its loop.
        if not self._lock.acquire(blocking=False):
            return dict(self._last_snapshot)
        try:
            if refresh:
                self._refresh_starting()
            self._last_snapshot = {
                "mode": self._mode,
                "follow_game": self._external_only and self.config.data_layer_auto_start,
                "game_running": self._game_watcher.running,
                "game_paused": self._game_paused,
                "game_watch_error": self._game_watch_error,
                "assistant_directory": self.config.assistant_directory,
                "started_by_plugin": self._started_by_plugin,
                "pid": getattr(self._process, "pid", None),
                "last_error": self._last_error,
                "health_reason": self._health_reason,
                "service_version": str(self._health_payload.get("service_version") or ""),
                "service_instance_id": str(
                    self._health_payload.get("service_instance_id") or ""
                ),
                "pending_scene_instance_id": self._pending_scene_instance_id,
                "actual_weights": str(self._health_payload.get("weights_id") or ""),
                "expected_infer_mode": self._expected_infer_mode(),
                "expected_weights": self._expected_weights(),
                "url": self.config.data_layer_url,
                "launch_cmd": list(self._launch_cmd),
                "python": "" if self._external_only else str(_resolve_python(self.plugin_root)),
                "weights": self._expected_weights(),
            }
            return dict(self._last_snapshot)
        finally:
            self._lock.release()
