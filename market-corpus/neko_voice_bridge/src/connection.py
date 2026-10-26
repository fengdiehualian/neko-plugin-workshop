"""One heartbeat-owned connection shared by Hosted UI and the EXE."""
import asyncio
import base64
import hashlib
import json
import time
import uuid
from urllib.parse import parse_qs, urlsplit, urlunsplit

from .bridge_client import BridgeClient
from .neko_backend import NekoBackend
from .worker import VoiceWorker


def parse_connection_code(value):
    if not isinstance(value, str) or len(value) > 4096:
        raise ValueError("INVALID_CONNECTION_CODE")
    value = value.strip()
    if value.startswith("NVB1:"):
        encoded = value[5:]
        value = base64.b64decode(encoded + "=" * (-len(encoded) % 4), altchars=b"-_", validate=True).decode()
    if value.startswith("http://") or value.startswith("https://"):
        parsed = urlsplit(value)
        fields = parse_qs(parsed.fragment, strict_parsing=True)
        if parsed.path not in ("", "/") or parsed.query or set(fields) != {"token"} or len(fields["token"]) != 1:
            raise ValueError("INVALID_CONNECTION_LINK")
        raw = {"bridge_url": urlunsplit((parsed.scheme, parsed.netloc, "", "", "")), "bridge_token": fields["token"][0]}
    else:
        raw = json.loads(value)
    client = BridgeClient(raw)
    return {"bridge_url": client.url, "bridge_token": client.token, "enabled": True}


async def read_character():
    from utils.config_manager import get_config_manager

    manager = get_config_manager()
    # The host reloads characters.json when its mtime changes.
    snapshot = await asyncio.to_thread(manager.get_character_data)
    profile = snapshot[1]
    character = snapshot[3].get(profile)
    if not profile or not isinstance(character, dict):
        raise RuntimeError("HOST_CHARACTER_UNAVAILABLE")
    display = character.get("昵称") or profile
    if not isinstance(display, str) or not display.strip() or len(display.strip()) > 160:
        raise RuntimeError("HOST_CHARACTER_UNAVAILABLE")
    revision = hashlib.sha256(json.dumps([profile, character, snapshot[5].get(profile)],
                                       sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()
    return {"name": display.strip(), "revision": revision}


class Connection:
    def __init__(self, config_path, *, character_reader=read_character, worker_factory=VoiceWorker, client_factory=BridgeClient):
        self.config_path = config_path
        self.context_sender = None
        self.character_reader, self.worker_factory, self.client_factory = character_reader, worker_factory, client_factory
        self.config = None
        self.client = None
        self.worker = None
        self.task = None
        self.lock = asyncio.Lock()
        self.session_id = uuid.uuid4().hex
        self.character = None
        self.bridge_state = None
        self.last_seen = 0
        self.error = None
        self.instance = None

    def snapshot(self):
        fresh = self.bridge_state is not None and time.monotonic() - self.last_seen < 4
        state = self.bridge_state if fresh else None
        connected = bool(state and state.get("plugin", {}).get("connected"))
        enabled = bool(self.config and self.config.get("enabled"))
        phase = ("receiving" if state.get("state") == "ACTIVE" else "connected") if connected else "reconnecting" if enabled else "disconnected"
        return {"phase": phase, "connected": connected, "paired": bool(self.config),
                "character_name": self.character["name"] if self.character else "",
                "bridge_state": state.get("state") if state else None,
                "rtc_connected": bool(state and state.get("rtc_connected")),
                "members": len(state.get("members", [])) if state else 0,
                "output_transport": state.get("speech", {}).get("output_transport", "websocket") if state else "websocket",
                "cable_ready": bool(state and state.get("speech", {}).get("cable_ready")),
                "wake_filter_enabled": state.get("wake_filter_enabled", True) if state else True,
                "processed": self.worker.processed if self.worker else 0,
                "error": self.error or (self.worker.last_error if self.worker else None),
                "speech_diagnostic": getattr(self.worker.speech, "last_diagnostic", None) if self.worker and self.worker.speech else None,
                "speech_error_history": list(getattr(self.worker.speech, "error_history", [])) if self.worker and self.worker.speech else [],
                "speech_error": self.worker.speech.last_error if self.worker and self.worker.speech else None}

    async def save(self):
        def write():
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.config_path.with_suffix(".tmp")
            with temp.open("w", encoding="utf-8") as handle:
                temp.chmod(0o600)
                json.dump(self.config, handle, ensure_ascii=False)
            temp.replace(self.config_path)
        await asyncio.to_thread(write)

    async def start(self, legacy_path=None):
        source = self.config_path if self.config_path.is_file() else legacy_path
        if source and source.is_file():
            try:
                raw = json.loads(await asyncio.to_thread(source.read_text, encoding="utf-8"))
                self.config = parse_connection_code(json.dumps(raw))
                self.config["enabled"] = raw.get("enabled", True) is True
                self.client = self.client_factory(self.config)
            except Exception:
                self.error = "INVALID_CONNECTION_CODE"
        self.task = asyncio.create_task(self.run())

    async def close_worker(self):
        worker, self.worker = self.worker, None
        if worker:
            await worker.stop()

    async def pair(self, code):
        config = parse_connection_code(code)
        candidate = self.client_factory(config)
        status = await candidate.request("/v1/status")
        if not status.get("instance_id") or status.get("speech", {}).get("mode") != "neko":
            raise ValueError("BRIDGE_VERSION_MISMATCH")
        async with self.lock:
            await self.unlink()
            self.config, self.client = config, candidate
            await self.save()
            await self.pulse()

    async def unlink(self):
        try:
            if self.client:
                await self.client.request("/v1/plugin/disconnect", {"session_id": self.session_id})
        except Exception:
            pass
        finally:
            self.bridge_state = None
            await self.close_worker()

    async def control(self, action):
        async with self.lock:
            if action == "disconnect":
                if self.config:
                    self.config["enabled"] = False
                    await self.save()
                await self.unlink()
                self.error = None
                return
            if not self.config or not self.client:
                raise RuntimeError("PAIR_FIRST")
            if action == "reconnect":
                self.config["enabled"] = True
                await self.save()
                await self.pulse()
                return
            if action not in ("start", "stop"):
                raise RuntimeError("INVALID_ACTION")
            if not self.config.get("enabled"):
                raise RuntimeError("BRIDGE_NOT_CONNECTED")
            await self.pulse()
            if not self.snapshot()["connected"]:
                raise RuntimeError("BRIDGE_NOT_CONNECTED")
            if action not in ("start", "stop"):
                raise RuntimeError("INVALID_ACTION")
            self.bridge_state = await self.client.request("/v1/join" if action == "start" else "/v1/leave", {})
            self.last_seen = time.monotonic()

    async def pulse(self):
        character = await self.character_reader()
        status = await self.client.request("/v1/status")
        if not status.get("instance_id"):
            raise RuntimeError("BRIDGE_VERSION_MISMATCH")
        if self.instance != status["instance_id"] or self.character != character:
            await self.close_worker()
        self.instance, self.character = status["instance_id"], character

        async def heartbeat():
            return await self.client.request("/v1/plugin/heartbeat", {
                "plugin_version": "0.7.1",
                "session_id": self.session_id, "character": character,
                "worker_ready": bool(self.worker and self.worker.task and not self.worker.task.done()),
                "error": self.worker.last_error if self.worker else None,
            })

        self.bridge_state = await heartbeat()
        if not self.worker or not self.worker.task or self.worker.task.done():
            await self.close_worker()
            settings = {"response_mode": "wake_word", "wake_words": [character["name"]],
                        "context_sender": self.context_sender, "allow_rtc_display_names": True, "allow_operator_confirmed": False}
            worker = self.worker_factory(self.client, NekoBackend(), settings)
            try:
                await worker.start()
            except BaseException:
                await worker.stop()
                raise
            self.worker = worker
            self.bridge_state = await heartbeat()
        self.last_seen = time.monotonic()
        self.error = None

    async def run(self):
        while True:
            async with self.lock:
                try:
                    if self.config and self.config.get("enabled") and self.client:
                        await self.pulse()
                    else:
                        self.character = await self.character_reader()
                except Exception:
                    self.error = "BRIDGE_OR_HOST_UNAVAILABLE"
                    self.bridge_state = None
                    try:
                        await self.close_worker()
                    except Exception:
                        pass
            await asyncio.sleep(1)

    async def close(self):
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            self.task = None
        await self.unlink()
