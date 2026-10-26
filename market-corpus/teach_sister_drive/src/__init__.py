"""Session-scoped voice controls for the local Unity bridge.

The transcript hook is an observer: it never cancels or alters NEKO's ordinary
dialogue. Browser game sessions take precedence over this plugin fallback.
Speech/subtitles are owned by the browser playback adapter, not conversation
polling or an estimated TTS duration.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import queue
import re
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

from plugin.sdk.plugin import Err, NekoPluginBase, Ok, SdkError, custom_event, lifecycle, neko_plugin, plugin_entry

from . import game_installation
from .intent import ALL_ACTIONS, LESSON_ACTIONS, CommandPlan, parse_command

_DEFAULT_URL = "http://127.0.0.1:8765"
_TTL_MS = 1500
_DEDUPE_SECONDS = 12.0
_TERMINAL = {"executed", "rejected", "expired", "failed", "cancelled"}


class BridgeError(RuntimeError):
    pass


@dataclass
class _CommandJob:
    request_id: str
    plan: CommandPlan
    scene_id: str
    source: str
    created_at: float
    fingerprint: str
    paired: bool = False
    done: threading.Event = field(default_factory=threading.Event)
    receipt: dict | None = None


def _decode_bridge(raw: str, *, receipt: bool = False) -> dict:
    try:
        payload = json.loads(raw)
    except (ValueError, TypeError) as exc:
        raise BridgeError("游戏桥返回了无效 JSON") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("ok"), bool):
        raise BridgeError("游戏桥响应缺少明确的 ok 状态")
    if payload["ok"] is not True and not receipt:
        raise BridgeError(str(payload.get("error") or "游戏桥拒绝了请求")[:240])
    return payload


def _bridge_error(raw: str) -> str:
    """Legacy helper remains fail-closed for any external callers."""
    try:
        _decode_bridge(raw)
        return ""
    except BridgeError as exc:
        return str(exc)


def _fingerprint(text: str) -> str:
    normalized = re.sub(r"[\s，,。.!！?？;；]+", "", text.lower())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest() if normalized else ""


def _validate_receipt(payload: dict, request_id: str, action: str) -> dict:
    if payload.get("request_id") != request_id:
        raise BridgeError("游戏回执的指令编号不匹配")
    state = payload.get("status")
    if state not in _TERMINAL | {"queued", "executing"}:
        raise BridgeError("游戏回执缺少有效执行状态")
    if state in {"rejected", "expired", "failed", "cancelled"}:
        raise BridgeError(str(payload.get("error") or "游戏未执行此动作")[:240])
    if state in {"queued", "executing"}:
        return payload
    if payload.get("ok") is not True:
        raise BridgeError("游戏未确认成功执行")
    results = payload.get("results")
    if not isinstance(results, list) or len(results) != 1 or not isinstance(results[0], dict):
        raise BridgeError("游戏回执缺少单条动作结果")
    if results[0].get("action") != action or results[0].get("status") != state:
        raise BridgeError("游戏回执的动作或状态不匹配")
    return payload


@neko_plugin
class TeachSisterDrivePlugin(NekoPluginBase):
    def __init__(self, ctx=None):
        super().__init__(ctx)
        self._cfg: dict = {}
        self._stop = threading.Event()
        self._worker: threading.Thread | None = None
        self._jobs: queue.Queue[_CommandJob] = queue.Queue(maxsize=8)
        self._lock = threading.Lock()
        self._recent: deque[_CommandJob] = deque(maxlen=64)
        self._snapshot: dict = {}
        self._snapshot_at = 0.0
        self._last_receipt: dict = {}
        self._session_id = uuid.uuid4().hex

    def _section(self) -> dict:
        section = self._cfg.get("teach_sister_drive") or {}
        return section if isinstance(section, dict) else {}

    def _installation_locations(self):
        directory = Path(__file__).resolve().parent
        try:
            from plugin.sdk.shared.core.base_runtime import resolve_plugin_data_dir
        except ImportError as exc:
            raise game_installation.InstallationError("此插件需要新版 N.E.K.O，当前 Steam 版不受支持") from exc
        return directory, resolve_plugin_data_dir(self.ctx)

    @plugin_entry(
        id="detect_game",
        name="检测游戏安装",
        timeout=20.0,
        description="只读查找Steam游戏库和妹妹开车模组状态，不启动游戏、不修改文件。",
        llm_result_fields=["message", "version", "game_running"],
        input_schema={"type": "object", "properties": {"game_path": {"type": "string"}}},
    )
    async def detect_game(self, game_path: str = "", **_):
        try:
            directory, _ = self._installation_locations()
            result = await asyncio.to_thread(game_installation.installation_status, directory, game_path)
            available = await asyncio.to_thread(self._source_host_available)
            result["takeover_available"] = available
            result["takeover_reason"] = "source_plugin_launcher" if available else "source_launcher_required"
            return Ok(result)
        except (game_installation.InstallationError, OSError, ValueError, subprocess.SubprocessError) as exc:
            return Err(SdkError(str(exc)))

    def _source_host_available(self) -> bool:
        url = str(self._section().get("neko_url") or "http://127.0.0.1:48911").rstrip("/")
        parsed = urllib.parse.urlparse(url)
        if (parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
                or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment):
            return False
        try:
            with urllib.request.urlopen(url + "/api/teach-sister-drive/plugin-health", timeout=1.5) as response:
                payload = json.loads(response.read(4097))
            return (isinstance(payload, dict) and payload.get("ok") is True
                    and payload.get("input_policy") == "plugin" and payload.get("assets") == "plugin")
        except (OSError, ValueError):
            return False

    @plugin_entry(
        id="install_mod",
        name="安装游戏模组",
        timeout=120.0,
        description="用户要求安装时，将已校验的随包模组安装到选定游戏。游戏运行时拒绝修改，更新先备份。",
        llm_result_fields=["message", "status", "mod_version"],
        input_schema={"type": "object", "properties": {"game_path": {"type": "string"}}, "required": ["game_path"]},
    )
    async def install_mod(self, game_path: str, **_):
        try:
            directory, data = self._installation_locations()
            return Ok(await asyncio.to_thread(game_installation.install_mod, game_path, directory, data))
        except (game_installation.InstallationError, OSError, ValueError, subprocess.SubprocessError) as exc:
            return Err(SdkError(str(exc)))

    @plugin_entry(
        id="uninstall_mod",
        name="卸载游戏模组",
        timeout=30.0,
        description="用户要求卸载时，备份并移除本插件模组，保留共享加载器、其他模组和存档。游戏运行时拒绝修改。",
        llm_result_fields=["message", "status"],
        input_schema={"type": "object", "properties": {"game_path": {"type": "string"}}, "required": ["game_path"]},
    )
    async def uninstall_mod(self, game_path: str, **_):
        try:
            directory, data = self._installation_locations()
            return Ok(await asyncio.to_thread(game_installation.uninstall_mod, game_path, directory, data))
        except (game_installation.InstallationError, OSError, ValueError, subprocess.SubprocessError) as exc:
            return Err(SdkError(str(exc)))

    def _cat_name(self) -> str:
        return str(self._section().get("cat_name") or "悠怡").strip()

    def _base_url(self) -> str:
        value = os.environ.get("NEKO_DRIVE_URL") or self._section().get("base_url") or _DEFAULT_URL
        url = str(value).rstrip("/")
        parsed = urllib.parse.urlparse(url)
        if (
            parsed.scheme != "http"
            or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
            or parsed.username
            or parsed.password
        ):
            raise BridgeError("游戏桥必须使用无凭据的本机 HTTP 地址")
        return url

    def _http(self, path: str, body: dict | None = None, *, timeout: float = 0.6) -> dict:
        request = urllib.request.Request(
            self._base_url() + path,
            data=json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8") if body is not None else None,
            headers={"Content-Type": "application/json"},
            method="POST" if body is not None else "GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read(65537)
                if len(raw) > 65536:
                    raise BridgeError("游戏桥响应过大")
                return _decode_bridge(raw.decode("utf-8"), receipt=path == "/command" or path.startswith("/commands/"))
        except urllib.error.HTTPError as exc:
            try:
                _decode_bridge(exc.read(65536).decode("utf-8"))
            except (BridgeError, UnicodeError) as detail:
                raise BridgeError(str(detail)) from exc
            raise BridgeError(f"游戏桥 HTTP {exc.code}") from exc
        except (OSError, UnicodeError, ValueError) as exc:
            raise BridgeError("无法连接游戏桥或读取响应") from exc

    def _set_snapshot(self, snapshot: dict) -> None:
        with self._lock:
            self._snapshot = snapshot
            self._snapshot_at = time.monotonic()

    def _session_body(self, active: bool) -> dict:
        return {"owner": "plugin", "session_id": self._session_id, "lanlan_name": self._cat_name(), "active": active}

    def _maintain_session(self) -> None:
        try:
            health = self._http("/health")
            if health.get("protocol_version") != 2:
                raise BridgeError("需要第二版游戏桥")
            status = self._http("/status")
            if status.get("protocol_version") != 2 or not status.get("scene_id"):
                raise BridgeError("游戏状态缺少场景标识")
            microphone = (status.get("body") or {}).get("tutorial_state") or {}
            native_microphone = microphone.get("microphone_stage") in {"waiting_input", "awaiting_speech"}
            if self._section().get("fallback_enabled", False) is not True:
                if status.get("input_owner") == "plugin" and status.get("session_id") == self._session_id:
                    self._http("/session", self._session_body(False))
                    status = self._http("/status")
                self._set_snapshot(status)
                return
            # The native exercise needs its own microphone/LLM when there is no
            # browser adapter. Do not seize it again on the next heartbeat.
            if native_microphone and status.get("input_owner") == "plugin":
                self._http("/session", self._session_body(False))
                status = self._http("/status")
            elif not native_microphone and status.get("game_active") is True and status.get("input_owner") != "browser":
                self._http("/session", self._session_body(True))
                status = self._http("/status")
            self._set_snapshot(status)
        except BridgeError as exc:
            self._set_snapshot({"ok": False, "error": str(exc)})

    @lifecycle(id="startup", name="启动驾驶控制", description="连接游戏桥并监听明确驾驶口令")
    async def startup(self, **_):
        try:
            cfg = await self.config.dump(timeout=5.0)
            self._cfg = cfg if isinstance(cfg, dict) else {}
        except Exception:
            self._cfg = {}
        if self._worker is None or not self._worker.is_alive():
            self._stop.clear()
            await asyncio.to_thread(self._maintain_session)
            self._worker = threading.Thread(target=self._worker_loop, name="sister-drive-controls", daemon=True)
            self._worker.start()
        return Ok({"base_url": self._base_url(), "cat_name": self._cat_name(), "protocol_version": 2})

    @lifecycle(id="shutdown", name="停止驾驶控制", description="释放游戏控制会话并取消待执行指令")
    async def shutdown(self, **_):
        self._stop.set()
        if self._worker:
            await asyncio.to_thread(self._worker.join, 3.0)
        return Ok({"stopped": self._worker is None or not self._worker.is_alive()})

    def _worker_loop(self) -> None:
        next_heartbeat = time.monotonic() + 2.0
        try:
            while not self._stop.is_set():
                if time.monotonic() >= next_heartbeat:
                    self._maintain_session()
                    next_heartbeat = time.monotonic() + 2.0
                try:
                    job = self._jobs.get(timeout=0.1)
                except queue.Empty:
                    continue
                try:
                    self._execute(job)
                finally:
                    self._jobs.task_done()
        finally:
            while True:
                try:
                    job = self._jobs.get_nowait()
                except queue.Empty:
                    break
                self._complete(
                    job, {"ok": False, "request_id": job.request_id, "status": "cancelled", "error": "插件已停止"}
                )
                self._jobs.task_done()
            try:
                self._http("/session", self._session_body(False), timeout=0.5)
            except BridgeError:
                pass  # The bridge also expires abandoned leases independently.

    def _owner_error(self, status: dict) -> str:
        if status.get("ok") is not True:
            return str(status.get("error") or "游戏桥尚未就绪")
        if status.get("protocol_version") != 2:
            return "游戏桥版本不匹配，请安装新版模组"
        if status.get("input_owner") == "browser":
            return "游戏语音页面正在接管输入，请在该页面操作"
        if status.get("game_active") is not True or not status.get("scene_id"):
            return "当前没有可控制的游戏场景"
        if status.get("input_owner") != "plugin" or status.get("session_id") != self._session_id:
            return "尚未取得当前游戏的控制会话"
        if status.get("lanlan_name") != self._cat_name():
            return "游戏控制会话的角色不匹配"
        return ""

    def _enqueue(self, plan: CommandPlan, text: str, source: str) -> tuple[_CommandJob | None, str]:
        if self._stop.is_set() or self._worker is None or not self._worker.is_alive():
            return None, "驾驶插件尚未启动"
        now = time.monotonic()
        fingerprint = _fingerprint(text)
        with self._lock:
            error = self._owner_error(self._snapshot)
            if error:
                return None, error
            if now - self._snapshot_at > 3.0:
                return None, "游戏状态已过期，暂不执行指令"
            while self._recent and now - self._recent[0].created_at > _DEDUPE_SECONDS:
                self._recent.popleft()
            # Match only opposite sources, once per utterance. Two consecutive
            # identical spoken commands remain two independent requests.
            if fingerprint:
                for recent in self._recent:
                    reverse_window = source != "voice_transcript" or (
                        now - recent.created_at <= 0.75 and (recent.receipt is None or recent.receipt.get("ok") is True)
                    )
                    if (
                        recent.fingerprint == fingerprint
                        and recent.source != source
                        and not recent.paired
                        and recent.scene_id == self._snapshot["scene_id"]
                        and reverse_window
                    ):
                        recent.paired = True
                        return recent, ""
            job = _CommandJob(uuid.uuid4().hex, plan, str(self._snapshot["scene_id"]), source, now, fingerprint)
            try:
                self._jobs.put_nowait(job)
            except queue.Full:
                return None, "驾驶指令队列已满，请稍后重新下令"
            self._recent.append(job)
            return job, ""

    def _complete(self, job: _CommandJob, receipt: dict) -> None:
        job.receipt = receipt
        with self._lock:
            self._last_receipt = receipt
        job.done.set()

    def _execute(self, job: _CommandJob) -> None:
        deadline = job.created_at + _TTL_MS / 1000
        try:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or self._stop.is_set():
                raise BridgeError("指令已过期或插件已停止")
            status = self._http("/status", timeout=min(0.5, remaining))
            error = self._owner_error(status)
            if error:
                raise BridgeError(error)
            if status.get("scene_id") != job.scene_id:
                raise BridgeError("场景已经切换，旧指令已取消")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise BridgeError("指令在发送前已过期")
            body = {
                "request_id": job.request_id,
                "session_id": self._session_id,
                "scene_id": job.scene_id,
                "ttl_ms": max(1, int(remaining * 1000)),
                "source": job.source,
                "commands": job.plan.commands,
            }
            # A transport timeout is uncertain, not a reason to create a new ID
            # and execute again. Query the same receipt until its deadline.
            try:
                receipt = self._http("/command", body, timeout=min(0.8, remaining))
            except BridgeError:
                receipt = None
            while True:
                if receipt is not None:
                    receipt = _validate_receipt(receipt, job.request_id, job.plan.commands[0]["action"])
                    if receipt["status"] == "executed":
                        self._complete(job, receipt)
                        return
                if self._stop.is_set() or time.monotonic() >= deadline + 0.5:
                    raise BridgeError("未收到执行确认；未自动重发，请查看车辆状态")
                self._stop.wait(0.05)
                try:
                    receipt = self._http("/commands/" + job.request_id, timeout=0.3)
                except BridgeError:
                    receipt = None
        except BridgeError as exc:
            self._complete(job, {"ok": False, "request_id": job.request_id, "status": "unconfirmed", "error": str(exc)})
        except Exception:
            # No transcript or raw response is persisted in diagnostics.
            self._complete(
                job, {"ok": False, "request_id": job.request_id, "status": "failed", "error": "驾驶控制发生内部错误"}
            )

    @custom_event(
        event_type="voice_transcript",
        id="observe_driving_voice",
        name="驾驶语音观察器",
        description="仅将当前角色的明确驾驶口令排入队列，不改变普通对话流程",
    )
    async def observe_driving_voice(
        self, transcript: str = "", lanlan_name: str = "", metadata: dict | None = None, **_
    ):
        if self._section().get("fallback_enabled", False) is not True:
            return Ok({"action": "noop", "reason": "fallback_disabled"})
        if lanlan_name != self._cat_name():
            return Ok({"action": "noop", "reason": "different_character"})
        plan = parse_command(transcript)
        if not plan.commands:
            return Ok({"action": "noop", "reason": "no_immediate_command"})
        job, error = self._enqueue(plan, transcript, "voice_transcript")
        return Ok(
            {
                "action": "noop",
                "reason": "queued_observer_command" if job else "game_not_owned",
                "request_id": job.request_id if job else "",
                "queued": job is not None,
            }
        )

    @plugin_entry(
        id="status",
        name="查看驾驶状态",
        timeout=5.0,
        llm_result_fields=["message", "last_command"],
        description="读取游戏场景、车辆和最近指令回执；未确认的动作不能说已经完成。",
        input_schema={"type": "object", "properties": {}},
    )
    async def status(self, **_):
        try:
            payload = await asyncio.to_thread(self._http, "/status")
        except BridgeError as exc:
            return Err(SdkError(str(exc)))
        with self._lock:
            last = dict(self._last_receipt)
        return Ok({"message": _status_message(payload), "status": payload, "last_command": last})

    @plugin_entry(
        id="drive",
        name="驾驶",
        timeout=6.0,
        llm_result_fields=["message", "commands", "request_id", "status"],
        description="立即执行单个驾驶或教程动作。优先传用户原话 intent，保留否定、速度及时间。不能提前宣称成功；只有 executed 回执表示游戏控制器已应用指令，实际车速查看 telemetry。浏览器游戏页面接管时不重复操作。",
        input_schema={
            "type": "object",
            "properties": {
                "intent": {"type": "string", "description": "用户原话，不能删除否定或改写成动作"},
                "action": {"type": "string", "enum": list(ALL_ACTIONS) + [""], "description": "没有原话时才使用"},
                "target_speed": {"type": "number", "minimum": -20, "maximum": 60, "description": "km/h；倒车为负数"},
                "steering": {"type": "number", "minimum": -1, "maximum": 1, "description": "负数向左，正数向右"},
                "duration_ms": {"type": "integer", "minimum": 100, "maximum": 10000},
            },
        },
    )
    async def drive(
        self,
        action: str = "",
        intent: str = "",
        target_speed: float | None = None,
        steering: float | None = None,
        duration_ms: int | None = None,
        *,
        _ctx: dict | None = None,
        **_,
    ):
        context = _ctx if isinstance(_ctx, dict) else {}
        text = str(context.get("latest_user_request") or intent or "").strip()
        plan = parse_command(text, action, target_speed=target_speed, steering=steering, duration_ms=duration_ms)
        if not plan.commands:
            return Ok({"message": plan.error, "commands": [], "request_id": "", "status": "not_requested"})
        return await self._run_plan(plan, text)

    async def _run_plan(self, plan: CommandPlan, text: str):
        job, error = self._enqueue(plan, text, "agent")
        if job is None:
            return Err(SdkError(error))
        await asyncio.to_thread(job.done.wait, _TTL_MS / 1000 + 1.0)
        receipt = job.receipt or {"ok": False, "error": "指令尚未确认，请查看游戏状态", "status": "unconfirmed"}
        if receipt.get("ok") is not True or receipt.get("status") != "executed":
            return Err(SdkError(str(receipt.get("error") or "游戏未确认执行")))
        message = "游戏控制器已应用指令，实际车速和位置以状态为准"
        if plan.note:
            message += "；" + plan.note
        return Ok(
            {
                "message": message,
                "commands": job.plan.commands,
                "request_id": job.request_id,
                "status": receipt["status"],
                "telemetry": receipt.get("telemetry", {}),
            }
        )

    @plugin_entry(
        id="lesson",
        name="教程动作",
        timeout=6.0,
        llm_result_fields=["message", "status"],
        description="兼容教程入口，仍由统一控制器检查当前场景及教程等待步骤。不得跳过条件。",
        input_schema={
            "type": "object",
            "properties": {"step": {"type": "string", "enum": list(LESSON_ACTIONS) + ["accelerate"]}},
            "required": ["step"],
        },
    )
    async def lesson(self, step: str = "", *, _ctx: dict | None = None, **_):
        action = "forward" if step == "accelerate" else step
        context = _ctx if isinstance(_ctx, dict) else {}
        text = str(context.get("latest_user_request") or "").strip()
        plan = parse_command(text, action)
        if not plan.commands:
            return Ok({"message": plan.error, "status": "not_requested"})
        return await self._run_plan(plan, text)


def _status_message(payload: dict) -> str:
    parts = ["场景 " + str(payload.get("scene") or "未知")]
    for key in ("tutorial", "car", "mission"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            parts.append(value)
    parts.append("控制来源 " + str(payload.get("input_owner") or "未知"))
    return "。".join(parts)
