"""Local vision-assistant client and process lifecycle.

The assistant is deliberately a separate process.  It owns game-window
capture and exposes only a loopback HTTP endpoint; the plugin owns OCR,
database validation, stabilization and N.E.K.O delivery.  A separately
downloaded assistant bundle can replace the lightweight source launcher
without changing this contract.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ASSISTANT_PROTOCOL_VERSION = "1"
DEFAULT_ASSISTANT_URL = "http://127.0.0.1:8219"
MAX_FRAME_BYTES = 16 * 1024 * 1024
MAX_COMPONENT_BYTES = 512 * 1024 * 1024
DEFAULT_COMPONENT_MANIFEST_URL = (
    "https://github.com/WhiteFrostune/n.e.k.o_plugin_hsr_companion/"
    "releases/download/v0.9.6/hsr-vision-assistant-manifest.json"
)


def default_assistant_root() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    return base / "N.E.K.O" / "data-packs" / "hsr_companion" / "assistant"


def _validated_loopback_url(value: str) -> str:
    parsed = urllib.parse.urlsplit(str(value or "").strip())
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("视觉助手地址必须是本机 HTTP 地址")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("视觉助手地址格式无效")
    port = parsed.port or 80
    if not 1 <= port <= 65535:
        raise ValueError("视觉助手端口无效")
    return f"http://127.0.0.1:{port}"


@dataclass(frozen=True)
class AssistantFrame:
    frame_seq: int
    timestamp: float
    image_bytes: bytes
    mime: str
    signature: str
    game: dict[str, Any]
    capture: dict[str, Any]
    analysis: dict[str, Any] | None = None


class VisionAssistantClient:
    def __init__(self, base_url: str = DEFAULT_ASSISTANT_URL, *, timeout: float = 1.2) -> None:
        self.base_url = _validated_loopback_url(base_url)
        self.timeout = max(0.2, min(float(timeout), 5.0))
        self.last_error = ""
        self.service_version = ""
        self._binary_frames = False
        self._frame_lock = threading.Lock()
        self._frame_finished_at = 0.0
        self._frame_result: AssistantFrame | None = None

    def _get_json(self, path: str) -> dict[str, Any]:
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            headers={"Accept": "application/json", "User-Agent": "N.E.K.O-hsr-companion/0.9"},
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            if int(getattr(response, "status", 200)) != 200:
                raise RuntimeError("视觉助手返回了异常状态")
            raw = response.read(MAX_FRAME_BYTES * 4 // 3 + 65537)
            if len(raw) > MAX_FRAME_BYTES * 4 // 3 + 65536:
                raise ValueError("视觉助手返回的数据过大")
            payload = json.loads(raw.decode("utf-8", "replace"))
        if not isinstance(payload, dict):
            raise ValueError("视觉助手返回格式无效")
        protocol = str(payload.get("protocol_version") or "")
        if protocol != ASSISTANT_PROTOCOL_VERSION:
            raise ValueError(f"视觉助手协议不兼容：{protocol or '未知'}")
        self.service_version = str(payload.get("service_version") or "")
        self._binary_frames = payload.get("frame_transport") == "binary_bmp_v1"
        self.last_error = ""
        return payload

    def _get_binary_frame(self, since_frame: int) -> tuple[dict[str, Any], bytes]:
        request = urllib.request.Request(
            f"{self.base_url}/api/frame.bin?since_frame={max(0, int(since_frame))}",
            headers={"Accept": "application/vnd.neko.hsr-frame", "User-Agent": "N.E.K.O-hsr-companion/0.9"},
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            if int(getattr(response, "status", 200)) != 200 or response.headers.get("Content-Type") != "application/vnd.neko.hsr-frame":
                raise ValueError("视觉助手二进制画面格式无效")
            raw = response.read(MAX_FRAME_BYTES + 65541)
        if len(raw) < 4 or len(raw) > MAX_FRAME_BYTES + 65540:
            raise ValueError("视觉助手二进制画面大小异常")
        length = struct.unpack("!I", raw[:4])[0]
        if not 0 < length <= 65536 or len(raw) < 4 + length:
            raise ValueError("视觉助手画面元数据大小异常")
        payload = json.loads(raw[4:4 + length])
        image = raw[4 + length:]
        if (not isinstance(payload, dict) or payload.get("protocol_version") != ASSISTANT_PROTOCOL_VERSION
                or payload.get("image_bytes_length") != len(image)):
            raise ValueError("视觉助手画面元数据与图片不匹配")
        self.service_version = str(payload.get("service_version") or "")
        return payload, image

    def health(self) -> dict[str, Any]:
        try:
            payload = self._get_json("/health")
            return {
                "connected": True,
                "protocol_version": ASSISTANT_PROTOCOL_VERSION,
                "service_version": self.service_version,
                "frame_seq": int(payload.get("frame_seq") or 0),
                "game": dict(payload.get("game") or {}),
                "capture": dict(payload.get("capture") or {}),
                "last_error": "",
            }
        except (OSError, ValueError, RuntimeError, urllib.error.URLError) as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            return {
                "connected": False,
                "protocol_version": ASSISTANT_PROTOCOL_VERSION,
                "service_version": self.service_version,
                "frame_seq": 0,
                "last_error": self.last_error,
            }

    def fetch_frame(self, since_frame: int = 0) -> AssistantFrame | None:
        """Coalesce simultaneous reads, not later reads or capture timestamps.

        OCR, queries and guards share this client. A waiter can use the same
        successful transfer that finished after it arrived, provided its
        requested sequence/freshness still match. Sequential calls always
        fetch again; this is not a new stale-frame cache.
        """
        arrived = time.monotonic()
        contended = not self._frame_lock.acquire(blocking=False)
        if contended and not self._frame_lock.acquire(timeout=self.timeout):
            raise RuntimeError("视觉助手画面读取正在进行，本次等待超时")
        try:
            shared = self._frame_result
            if (contended and shared is not None and self._frame_finished_at >= arrived
                    and shared.frame_seq > max(0, int(since_frame))
                    and 0 <= time.time() - shared.timestamp <= 2.0):
                return copy.deepcopy(shared)
            self._frame_result = None
            result = self._fetch_frame(since_frame)
            self._frame_result = result
            self._frame_finished_at = time.monotonic()
            return copy.deepcopy(result)
        finally:
            self._frame_lock.release()

    def _fetch_frame(self, since_frame: int = 0) -> AssistantFrame | None:
        try:
            image_bytes = None
            if self._binary_frames:
                try:
                    payload, image_bytes = self._get_binary_frame(since_frame)
                except urllib.error.HTTPError as exc:
                    if exc.code not in {404, 415}:
                        raise
                    self._binary_frames = False  # Explicitly unsupported extension, not corrupt data.
                    payload = self._get_json(f"/api/frame?since_frame={max(0, int(since_frame))}")
            else:
                payload = self._get_json(f"/api/frame?since_frame={max(0, int(since_frame))}")
            frame_seq = int(payload.get("frame_seq") or 0)
            encoded = str(payload.get("image_base64") or "")
            if (not image_bytes and not encoded) or frame_seq <= int(since_frame):
                return None
            game = dict(payload.get("game") or {})
            capture = dict(payload.get("capture") or {})
            if capture.get("state") == "capturing" and capture.get("isolated_window") is not True:
                raise RuntimeError("请更新独立视觉助手：旧版本采集的是桌面画面，可能包含猫娘遮挡。")
            timestamp = float(payload.get("timestamp") or 0.0)
            if (
                capture.get("state") != "capturing" or not game.get("running")
                or game.get("minimized")
                or not 0 <= time.time() - timestamp <= 15
            ):
                return None
            mime = str(payload.get("mime") or "image/jpeg").lower()
            if mime not in {"image/jpeg", "image/png", "image/webp", "image/bmp"}:
                raise ValueError("视觉助手返回了不支持的图像格式")
            if image_bytes is None:
                image_bytes = base64.b64decode(encoded, validate=True)
            if not image_bytes or len(image_bytes) > MAX_FRAME_BYTES:
                raise ValueError("视觉助手返回的画面大小异常")
            analysis = payload.get("analysis")
            self.last_error = ""
            return AssistantFrame(
                frame_seq=frame_seq,
                timestamp=timestamp,
                image_bytes=image_bytes,
                mime=mime,
                signature=str(payload.get("signature") or ""),
                game=game,
                capture=capture,
                analysis=dict(analysis) if isinstance(analysis, dict) else None,
            )
        except (OSError, ValueError, RuntimeError, urllib.error.URLError) as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            raise RuntimeError(str(exc) if isinstance(exc, RuntimeError) else "暂时无法读取星铁后台感知助手") from exc


class VisionAssistantProcess:
    """Own the helper process started by this plugin, never arbitrary processes."""

    def __init__(
        self,
        plugin_root: Path,
        *,
        assistant_root: Path | None = None,
        base_url: str = DEFAULT_ASSISTANT_URL,
    ) -> None:
        self.plugin_root = Path(plugin_root).resolve()
        self.assistant_root = Path(assistant_root or default_assistant_root()).resolve()
        self.base_url = _validated_loopback_url(base_url)
        self._process: subprocess.Popen[bytes] | None = None
        self._launch_mode = "stopped"
        self._last_error = ""
        self._last_start_at = 0.0
        self._process_lock = threading.RLock()

    @property
    def port(self) -> int:
        return int(urllib.parse.urlsplit(self.base_url).port or 80)

    def _installed_executable(self) -> Path | None:
        current = self.assistant_root / "current.json"
        if not current.is_file():
            return None
        try:
            payload = json.loads(current.read_text(encoding="utf-8"))
            relative = Path(str(payload.get("executable") or ""))
            if relative.is_absolute() or ".." in relative.parts:
                return None
            executable = (self.assistant_root / relative).resolve()
            executable.relative_to(self.assistant_root)
            return executable if executable.is_file() else None
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return None

    def _source_launcher(self) -> Path | None:
        launcher = self.plugin_root / "assistant_runtime" / "server.py"
        return launcher if launcher.is_file() else None

    def _source_launchable(self) -> bool:
        # Nuitka hosts do not necessarily expose sys.frozen. Only an actual
        # Python/PyPy executable can run the source helper as a script.
        executable_name = Path(sys.executable).stem.lower()
        return bool(
            self._source_launcher() is not None
            and not getattr(sys, "frozen", False)
            and executable_name.startswith(("python", "pypy"))
        )

    def command(self) -> tuple[list[str], str]:
        arguments = ["--port", str(self.port), "--parent-pid", str(os.getpid())]
        executable = self._installed_executable()
        if executable is not None:
            return [str(executable), *arguments], "external_bundle"
        launcher = self._source_launcher()
        if launcher is not None and self._source_launchable():
            return [sys.executable, str(launcher), *arguments], "source_compatibility"
        raise RuntimeError("独立视觉助手尚未下载；桌面版不能直接运行助手源码，请下载助手。")

    def start_if_due(self) -> dict[str, Any]:
        """Avoid repeatedly spawning a failing helper on every capture tick."""
        with self._process_lock:
            return self._start_if_due_locked()

    def _start_if_due_locked(self) -> dict[str, Any]:
        if self._process is not None and self._process.poll() is None:
            return self.snapshot()
        if self._last_start_at and time.monotonic() - self._last_start_at < 30:
            return self.snapshot()
        try:
            return self.start()
        except RuntimeError:
            return self.snapshot()

    def install_component(self, manifest_url: str = DEFAULT_COMPONENT_MANIFEST_URL) -> dict[str, Any]:
        """Install a signed-by-hash release asset into the plugin data root.

        The URL is fixed to this plugin's public GitHub repository.  The
        archive is checked before extraction and every member is constrained
        to a fresh version directory.
        """

        parsed_manifest = urllib.parse.urlsplit(str(manifest_url or ""))
        if (
            parsed_manifest.scheme != "https"
            or parsed_manifest.hostname != "github.com"
            or not parsed_manifest.path.startswith("/WhiteFrostune/n.e.k.o_plugin_hsr_companion/releases/")
        ):
            raise ValueError("视觉组件清单必须来自插件的 GitHub 仓库")
        request = urllib.request.Request(
            manifest_url,
            headers={"Accept": "application/json", "User-Agent": "N.E.K.O-hsr-companion/0.9"},
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                raw_manifest = response.read(128 * 1024 + 1)
        except (OSError, urllib.error.URLError) as exc:
            if isinstance(exc, urllib.error.HTTPError) and exc.code == 404:
                raise RuntimeError("下载失败：发布页尚未提供视觉助手，请稍后重试。") from exc
            raise RuntimeError("下载失败：无法连接视觉助手下载源，请检查网络后重试。") from exc
        if len(raw_manifest) > 128 * 1024:
            raise ValueError("视觉组件清单大小异常")
        try:
            manifest = json.loads(raw_manifest.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("视觉组件清单格式无效") from exc
        if not isinstance(manifest, dict):
            raise ValueError("视觉组件清单格式无效")
        version = str(manifest.get("version") or "").strip()
        protocol = str(manifest.get("protocol_version") or "").strip()
        archive_url = str(manifest.get("archive_url") or "").strip()
        expected_sha256 = str(manifest.get("sha256") or "").strip().lower()
        executable_name = str(manifest.get("executable") or "hsr-vision-assistant.exe").strip()
        if (
            not version
            or len(version) > 64
            or any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-" for ch in version)
            or protocol != ASSISTANT_PROTOCOL_VERSION
        ):
            raise ValueError("视觉组件版本或协议不兼容")
        archive_parts = urllib.parse.urlsplit(archive_url)
        if (
            archive_parts.scheme != "https"
            or archive_parts.hostname != "github.com"
            or not archive_parts.path.startswith("/WhiteFrostune/n.e.k.o_plugin_hsr_companion/releases/download/")
        ):
            raise ValueError("视觉组件必须来自插件的 GitHub Release")
        if len(expected_sha256) != 64 or any(ch not in "0123456789abcdef" for ch in expected_sha256):
            raise ValueError("视觉组件缺少有效的 SHA-256")
        executable = Path(executable_name)
        if executable.is_absolute() or ".." in executable.parts or executable.suffix.lower() != ".exe":
            raise ValueError("视觉组件入口无效")

        self.assistant_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="hsr-assistant-", dir=self.assistant_root) as temporary:
            temporary_root = Path(temporary)
            archive_path = temporary_root / "component.zip"
            digest = hashlib.sha256()
            total = 0
            archive_request = urllib.request.Request(
                archive_url,
                headers={"Accept": "application/octet-stream", "User-Agent": "N.E.K.O-hsr-companion/0.9"},
            )
            try:
                with urllib.request.urlopen(archive_request, timeout=60) as response, archive_path.open("wb") as output:
                    while True:
                        chunk = response.read(1024 * 1024)
                        if not chunk:
                            break
                        total += len(chunk)
                        if total > MAX_COMPONENT_BYTES:
                            raise ValueError("视觉组件超过允许大小")
                        digest.update(chunk)
                        output.write(chunk)
            except (OSError, urllib.error.URLError) as exc:
                raise RuntimeError("暂时无法下载后台感知组件") from exc
            if digest.hexdigest().lower() != expected_sha256:
                raise ValueError("视觉组件校验失败，已拒绝安装")

            unpacked = temporary_root / "unpacked"
            unpacked.mkdir()
            try:
                with zipfile.ZipFile(archive_path) as bundle:
                    for member in bundle.infolist():
                        relative = Path(member.filename.replace("\\", "/"))
                        if relative.is_absolute() or ".." in relative.parts:
                            raise ValueError("视觉组件包含不安全路径")
                        mode = (member.external_attr >> 16) & 0o170000
                        if mode == 0o120000:
                            raise ValueError("视觉组件不得包含符号链接")
                    bundle.extractall(unpacked)
            except zipfile.BadZipFile as exc:
                raise ValueError("视觉组件压缩包损坏") from exc
            if not (unpacked / executable).is_file():
                raise ValueError("视觉组件缺少可执行入口")

            target = (self.assistant_root / version).resolve()
            target.relative_to(self.assistant_root)
            if target.exists():
                shutil.rmtree(target)
            shutil.move(str(unpacked), str(target))
            current = {
                "version": version,
                "protocol_version": protocol,
                "sha256": expected_sha256,
                "executable": str(Path(version) / executable),
                "source": archive_url,
            }
            temporary_current = self.assistant_root / "current.json.tmp"
            temporary_current.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary_current.replace(self.assistant_root / "current.json")
        return {**self.snapshot(), "version": version, "sha256": expected_sha256}

    def start(self) -> dict[str, Any]:
        with self._process_lock:
            return self._start_locked()

    def _start_locked(self) -> dict[str, Any]:
        if self._process is not None and self._process.poll() is None:
            return self.snapshot()
        self._last_start_at = time.monotonic()
        try:
            command, mode = self.command()
        except RuntimeError as exc:
            self._launch_mode = "unavailable"
            self._last_error = str(exc)
            raise
        flags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            self._process = subprocess.Popen(
                command,
                cwd=str(Path(command[0]).parent if mode == "external_bundle" else self.plugin_root),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=flags,
            )
            self._launch_mode = mode
            self._last_error = ""
        except OSError as exc:
            self._process = None
            self._launch_mode = "error"
            self._last_error = f"{type(exc).__name__}: {exc}"
            raise RuntimeError("无法启动星铁后台感知助手") from exc
        return self.snapshot()

    def stop(self) -> dict[str, Any]:
        with self._process_lock:
            return self._stop_locked()

    def _stop_locked(self) -> dict[str, Any]:
        process = self._process
        self._process = None
        if process is not None and process.poll() is None:
            if os.name == "nt":
                # A one-file PyInstaller bundle owns both a launcher and a
                # child. Terminating only its launcher leaves capture alive.
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)),
                    check=False,
                )
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=4)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
        self._launch_mode = "stopped"
        self._last_start_at = 0.0
        self._last_error = ""
        return self.snapshot()

    def snapshot(self) -> dict[str, Any]:
        process = self._process
        running = bool(process is not None and process.poll() is None)
        exit_code = process.poll() if process is not None and not running else None
        if exit_code is not None:
            self._launch_mode = "error"
            self._last_error = f"视觉助手已退出（退出码 {exit_code}），请重新启动助手。"
        installed = self._installed_executable() is not None
        source_available = self._source_launcher() is not None
        return {
            "installed": installed,
            "download_status": "installed" if installed else "not_installed",
            "source_available": source_available,
            "source_launchable": self._source_launchable(),
            "running": running,
            "owned": running,
            "pid": int(process.pid) if running and process is not None else None,
            "mode": self._launch_mode,
            "exit_code": exit_code,
            "protocol_version": ASSISTANT_PROTOCOL_VERSION,
            "base_url": self.base_url,
            "resource_root": str(self.assistant_root),
            "last_error": self._last_error,
        }


__all__ = [
    "ASSISTANT_PROTOCOL_VERSION",
    "AssistantFrame",
    "DEFAULT_ASSISTANT_URL",
    "DEFAULT_COMPONENT_MANIFEST_URL",
    "VisionAssistantClient",
    "VisionAssistantProcess",
    "default_assistant_root",
]
