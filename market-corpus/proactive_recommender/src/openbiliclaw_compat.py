from __future__ import annotations

import asyncio
import hashlib
import re
import threading
import time
from collections import Counter
from collections.abc import Awaitable, Callable, Mapping
from typing import Any
from urllib.parse import urlparse

from .profile import apply_profile_updates

EventHandler = Callable[[list[Mapping[str, Any]]], Awaitable[dict[str, Any]]]
StatusProvider = Callable[[], Mapping[str, Any]]

_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_+.#-]{2,}|[\u4e00-\u9fff]{2,8}")
_STOPWORDS = {
    "首页",
    "视频",
    "内容",
    "推荐",
    "播放",
    "关注",
    "收藏",
    "分享",
    "comment",
    "favorite",
    "follow",
    "homepage",
    "video",
}
_SENSITIVE_MARKERS = (
    "身份证",
    "手机号",
    "家庭住址",
    "银行卡",
    "病历",
    "性取向",
    "宗教信仰",
    "政治立场",
    "password",
    "phone number",
    "home address",
    "medical record",
    "religion",
    "sexual orientation",
)
_NEGATIVE_TYPES = {"dislike", "not_interested", "dismiss", "quick_exit"}
_STRONG_TYPES = {"like", "favorite", "collect", "coin", "share", "follow", "comment"}
_MEDIUM_TYPES = {"click", "search", "meaningful_dwell", "video_complete", "watch_end"}
_PLATFORM_HOSTS = {
    "bilibili.com": "bilibili",
    "xiaohongshu.com": "xiaohongshu",
    "douyin.com": "douyin",
    "youtube.com": "youtube",
    "youtu.be": "youtube",
    "x.com": "twitter",
    "twitter.com": "twitter",
    "zhihu.com": "zhihu",
    "weibo.com": "weibo",
    "reddit.com": "reddit",
    "linux.do": "linuxdo",
    "v2ex.com": "v2ex",
    "bgm.tv": "bangumi",
}


def infer_platform(value: object, url: object = "") -> str:
    explicit = str(value or "").strip().lower()
    if explicit:
        return explicit[:32]
    hostname = (urlparse(str(url or "")).hostname or "").lower()
    for suffix, platform in _PLATFORM_HOSTS.items():
        if hostname == suffix or hostname.endswith(f".{suffix}"):
            return platform
    return "web"


def normalize_timestamp(value: object, *, now: float | None = None) -> float:
    fallback = time.time() if now is None else now
    try:
        timestamp = float(value)
    except (TypeError, ValueError):
        return fallback
    if timestamp > 10_000_000_000:
        timestamp /= 1000.0
    if timestamp <= 0 or timestamp > fallback + 86400:
        return fallback
    return timestamp


def event_fingerprint(event: Mapping[str, Any]) -> str:
    producer_id = str(event.get("event_id") or "").strip()
    if producer_id:
        return hashlib.sha256(producer_id.encode("utf-8")).hexdigest()[:24]
    stable = "\n".join(
        str(event.get(key) or "") for key in ("type", "url", "title", "timestamp")
    )
    return hashlib.sha256(stable.encode("utf-8")).hexdigest()[:24]


def _topic_text(event: Mapping[str, Any]) -> str:
    metadata = event.get("metadata")
    metadata = metadata if isinstance(metadata, Mapping) else {}
    parts: list[str] = [str(event.get("title") or "")]
    for key in ("search_query", "query", "author", "creator", "up_name", "topic"):
        value = metadata.get(key)
        if isinstance(value, str):
            parts.append(value)
    for key in ("tags", "keywords", "topics"):
        values = metadata.get(key)
        if isinstance(values, list):
            parts.extend(str(value) for value in values[:12])
    return " ".join(parts)[:1600]


def behavior_event_updates(events: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    positive: Counter[str] = Counter()
    negative: Counter[str] = Counter()
    confidence: dict[str, float] = {}
    for event in events:
        event_type = str(event.get("type") or "").strip().lower()
        polarity = "negative" if event_type in _NEGATIVE_TYPES else "positive"
        strength = 0.78 if event_type in _STRONG_TYPES else 0.58 if event_type in _MEDIUM_TYPES else 0.38
        target = negative if polarity == "negative" else positive
        for token in _TOKEN_RE.findall(_topic_text(event).lower()):
            topic = token.strip().lower()
            if (
                topic in _STOPWORDS
                or len(topic) > 80
                or any(marker in topic for marker in _SENSITIVE_MARKERS)
            ):
                continue
            target[topic] += 1
            confidence[f"{polarity}:{topic}"] = max(
                confidence.get(f"{polarity}:{topic}", 0.0), strength
            )
    updates: list[dict[str, Any]] = []
    for polarity, counter in (("positive", positive), ("negative", negative)):
        for topic, count in counter.most_common(12):
            updates.append(
                {
                    "topic": topic,
                    "polarity": polarity,
                    "confidence": min(0.95, confidence[f"{polarity}:{topic}"] + 0.05 * (count - 1)),
                }
            )
    return updates


def apply_behavior_event_batch(
    state: dict[str, Any],
    events: list[Mapping[str, Any]],
    *,
    now: float,
) -> dict[str, Any]:
    valid: list[tuple[Mapping[str, Any], str, str, float]] = []
    rejected: list[dict[str, Any]] = []
    for index, event in enumerate(events):
        event_type = str(event.get("type") or "").strip().lower()
        if not event_type:
            rejected.append({"index": index, "type": "", "reason": "type_required"})
            continue
        valid.append(
            (
                event,
                event_fingerprint(event),
                infer_platform(event.get("source_platform"), event.get("url")),
                normalize_timestamp(event.get("timestamp"), now=now),
            )
        )

    processed = [str(value) for value in state.get("processed_platform_event_ids", [])]
    processed_set = set(processed)
    stats = state.setdefault("platform_events", {})
    by_platform = stats.setdefault("by_platform", {})
    last_event_at = float(stats.get("last_event_at", 0.0))
    accepted_events: list[Mapping[str, Any]] = []
    duplicate_count = 0
    for event, fingerprint, platform, timestamp in valid:
        if fingerprint in processed_set:
            duplicate_count += 1
            continue
        processed.append(fingerprint)
        processed_set.add(fingerprint)
        accepted_events.append(event)
        by_platform[platform] = int(by_platform.get(platform, 0)) + 1
        last_event_at = max(last_event_at, timestamp)
    if accepted_events:
        state["profile"] = apply_profile_updates(
            state.get("profile"), behavior_event_updates(accepted_events), now=now
        )
    state["processed_platform_event_ids"] = processed[-1000:]
    stats["accepted"] = int(stats.get("accepted", 0)) + len(accepted_events)
    stats["duplicate"] = int(stats.get("duplicate", 0)) + duplicate_count
    stats["rejected"] = int(stats.get("rejected", 0)) + len(rejected)
    stats["last_event_at"] = last_event_at
    return {
        # Duplicate retries are accepted at the protocol boundary so the MV3
        # durable buffer can drain, but they do not alter the profile.
        "accepted": len(valid),
        "duplicates": duplicate_count,
        "rejected": rejected,
    }


class OpenBiliClawCompatibilityServer:
    """Loopback OpenBiliClaw extension ingress for NEKO recommendation signals."""

    def __init__(
        self,
        *,
        host: str,
        port: int,
        on_events: EventHandler,
        status_provider: StatusProvider,
        logger: Any,
    ) -> None:
        self.host = host
        self.port = port
        self._on_events = on_events
        self._status_provider = status_provider
        self._logger = logger
        self._runner: Any = None
        self._clients: set[Any] = set()
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop_event: asyncio.Event | None = None
        self.running = False
        self.started_at = 0.0
        self.last_error = ""

    def snapshot(self) -> dict[str, Any]:
        return {
            "enabled": True,
            "running": self.running,
            "host": self.host,
            "port": self.port,
            "endpoint": f"http://{self.host}:{self.port}",
            "connected_clients": len(self._clients),
            "started_at": self.started_at,
            "last_error": self.last_error,
            "cookie_ingest": False,
            "compatibility_level": "behavior-events",
        }

    async def start(self) -> None:
        if self.running:
            return
        ready = threading.Event()
        self.last_error = ""
        self._thread = threading.Thread(
            target=self._thread_main,
            args=(ready,),
            name=f"openbiliclaw-compat-{self.port}",
            daemon=True,
        )
        self._thread.start()
        started = await asyncio.to_thread(ready.wait, 5.0)
        if not started:
            self.last_error = "startup_timeout"
            await self.stop()
            raise RuntimeError(self.last_error)
        if not self.running:
            raise RuntimeError(self.last_error or "startup_failed")

    def _thread_main(self, ready: threading.Event) -> None:
        try:
            asyncio.run(self._serve(ready))
        except Exception as exc:
            if not self.last_error:
                self.last_error = f"{type(exc).__name__}: {exc}"[:240]
            ready.set()

    async def _serve(self, ready: threading.Event) -> None:
        try:
            from aiohttp import web
        except ImportError as exc:
            self.last_error = "aiohttp_not_installed"
            ready.set()
            raise RuntimeError(self.last_error) from exc

        @web.middleware
        async def cors(request: Any, handler: Any) -> Any:
            if request.method == "OPTIONS":
                response = web.Response(status=204)
            else:
                response = await handler(request)
            response.headers["Access-Control-Allow-Origin"] = "*"
            response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
            response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
            return response

        app = web.Application(client_max_size=1024 * 1024, middlewares=[cors])
        app.router.add_get("/api/ping", self._ping)
        app.router.add_get("/api/health", self._health)
        app.router.add_get("/api/runtime-status", self._runtime_status)
        app.router.add_get("/api/runtime-stream", self._runtime_stream)
        app.router.add_post("/api/events", self._events)
        app.router.add_get("/api/notifications/pending", self._empty_item)
        app.router.add_get("/api/cognition-updates/pending", self._empty_item)
        app.router.add_get("/api/delight/pending", self._empty_item)
        app.router.add_post("/api/notifications/sent", self._ack)
        app.router.add_post("/api/cognition-updates/seen", self._ack)
        app.router.add_post("/api/delight/sent", self._ack)
        app.router.add_post("/api/bilibili/cookie", self._reject_bilibili_cookie)
        app.router.add_post("/api/sources/dy/cookie", self._reject_douyin_cookie)
        app.router.add_route("OPTIONS", "/{path:.*}", self._options)

        self._loop = asyncio.get_running_loop()
        self._stop_event = asyncio.Event()
        runner = web.AppRunner(app)
        self._runner = runner
        await runner.setup()
        try:
            site = web.TCPSite(runner, self.host, self.port)
            await site.start()
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"[:240]
            await runner.cleanup()
            self._runner = None
            ready.set()
            raise
        self.running = True
        self.started_at = time.time()
        self.last_error = ""
        ready.set()
        try:
            await self._stop_event.wait()
        finally:
            for client in tuple(self._clients):
                try:
                    await client.close(code=1001, message=b"plugin stopping")
                except Exception:
                    pass
            self._clients.clear()
            await runner.cleanup()
            self._runner = None
            self._stop_event = None
            self._loop = None
            self.running = False

    async def stop(self, *, timeout: float = 5.0) -> bool:
        loop = self._loop
        stop_event = self._stop_event
        if loop is not None and stop_event is not None and loop.is_running():
            loop.call_soon_threadsafe(stop_event.set)
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            await asyncio.to_thread(thread.join, max(0.0, float(timeout)))
        stopped = thread is None or not thread.is_alive()
        if stopped:
            self._thread = None
            self.running = False
        elif self._logger is not None:
            self._logger.warning(
                "OpenBiliClaw compatibility server did not stop within {:.3f}s",
                timeout,
            )
        return stopped

    async def _ping(self, _: Any) -> Any:
        from aiohttp import web

        return web.json_response({"status": "ok", "service": "neko-openbiliclaw-compat"})

    async def _health(self, _: Any) -> Any:
        from aiohttp import web

        return web.json_response(
            {"status": "ok", "service": "neko-openbiliclaw-compat", "profile_ready": True}
        )

    async def _runtime_status(self, _: Any) -> Any:
        from aiohttp import web

        return web.json_response(dict(self._status_provider()))

    async def _runtime_stream(self, request: Any) -> Any:
        from aiohttp import WSMsgType, web

        websocket = web.WebSocketResponse(heartbeat=25.0)
        await websocket.prepare(request)
        self._clients.add(websocket)
        await websocket.send_json(
            {"type": "compat.connected", "service": "neko", "timestamp": time.time()}
        )
        try:
            async for message in websocket:
                if message.type in {WSMsgType.CLOSE, WSMsgType.CLOSED, WSMsgType.ERROR}:
                    break
        finally:
            self._clients.discard(websocket)
        return websocket

    async def _events(self, request: Any) -> Any:
        from aiohttp import web

        try:
            payload = await request.json()
        except Exception:
            return web.json_response(
                {"accepted": 0, "rejected": [{"index": 0, "type": "", "reason": "invalid_json"}]},
                status=400,
            )
        events = payload.get("events") if isinstance(payload, Mapping) else None
        if not isinstance(events, list):
            return web.json_response(
                {"accepted": 0, "rejected": [{"index": 0, "type": "", "reason": "events_required"}]},
                status=422,
            )
        if len(events) > 256:
            return web.json_response(
                {"accepted": 0, "rejected": [{"index": 0, "type": "", "reason": "batch_too_large"}]},
                status=413,
            )
        valid = [event for event in events if isinstance(event, Mapping)]
        result = await self._on_events(valid)
        rejected = list(result.get("rejected", []))
        for index, event in enumerate(events):
            if not isinstance(event, Mapping):
                rejected.append({"index": index, "type": "", "reason": "invalid_event"})
        return web.json_response(
            {"accepted": int(result.get("accepted", 0)), "rejected": rejected}
        )

    async def _empty_item(self, _: Any) -> Any:
        from aiohttp import web

        return web.json_response({"item": None})

    async def _ack(self, request: Any) -> Any:
        from aiohttp import web

        try:
            payload = await request.json()
        except Exception:
            payload = {}
        return web.json_response({"ok": True, **(payload if isinstance(payload, dict) else {})})

    async def _reject_bilibili_cookie(self, _: Any) -> Any:
        from aiohttp import web

        return web.json_response(
            {
                "ok": False,
                "authenticated": False,
                "message": "NEKO compatibility mode does not read or store browser cookies.",
                "error_code": "cookie_storage_disabled",
            }
        )

    async def _reject_douyin_cookie(self, _: Any) -> Any:
        from aiohttp import web

        return web.json_response(
            {
                "ok": False,
                "has_cookie": False,
                "cookie_names": [],
                "message": "NEKO compatibility mode does not read or store browser cookies.",
                "error_code": "cookie_storage_disabled",
            }
        )

    async def _options(self, _: Any) -> Any:
        from aiohttp import web

        return web.Response(status=204)


__all__ = [
    "OpenBiliClawCompatibilityServer",
    "apply_behavior_event_batch",
    "behavior_event_updates",
    "event_fingerprint",
    "infer_platform",
    "normalize_timestamp",
]
