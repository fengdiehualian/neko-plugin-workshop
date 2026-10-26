"""Read the host's own plugin-management API to see whether the built-in search runs.

Two search plugins exposed to the model at the same time is confusing and wasteful:
the model may pick the built-in ``web_search`` and the user pays for a backend we
do not control. Stopping it is the user's own click in the plugin centre -- this
module only *reads* the result, because writing was measured to be unreliable from
here (see below).

Why loopback HTTP instead of importing the host's ``PluginLifecycleService``:

* **Process boundary.** A user plugin runs in its *own* subprocess. The live
  registry (``plugin.core.state.state.plugin_hosts``) is a plain module-level
  dict inside the host process, so an imported service object here would see an
  empty local copy and report a plugin that is very much alive as absent.
* **No privileged surface is needed.** ``/plugin/status`` is one of the endpoints
  the official plugin centre already calls, and ``require_admin`` is a no-op
  dependency, so this stays inside the host's public contract instead of touching
  host files (which are read-only for a distributed plugin anyway).

Why this module stopped at reading: ``POST /plugin/web_search/stop`` has been
observed sitting behind the host's own registry reload for 8+ seconds and answering
*after* our client gave up -- while the change had in fact landed. A plugin cannot
tell "slow acknowledgement" from "failed", so every attempt looked like a broken
toggle, and the retry path (a 20 s background tick) had to disarm itself once it had
confirmed, which left a built-in that came back later never re-stopped. The status
read ``GET /plugin/status`` is cheap by comparison: it is served from in-process
caches (``plugin/core/status.py:79-113``).

Every request is loopback-only and must never be proxied, so they all go out
through :func:`_net.request` with ``policy=_net.POLICY_NONE``.
"""

from __future__ import annotations

import json
import os
import socket
import urllib.parse
from dataclasses import dataclass, field
from typing import Any

from . import _net

# Hard whitelist. Nothing else may be read through this module, even if a future
# prompt-injection convinces the model to name another plugin.
ALLOWED_PLUGIN_IDS = frozenset({"web_search"})

# Convenience constant for the panel: the only plugin this module exists to flip.
BUILTIN_SEARCH_PLUGIN_ID = "web_search"

# Last-resort origin; also what ``config.network.USER_PLUGIN_BASE`` is built from.
FALLBACK_BASE_URL = "http://127.0.0.1:48916"

# Environment names carrying the *live* port (the launcher republishes it when the
# preferred port was busy). Checked in this order.
BASE_URL_ENV_KEYS = ("NEKO_USER_PLUGIN_SERVER_PORT", "USER_PLUGIN_SERVER_PORT")

DEFAULT_TIMEOUT_SECONDS = 4.0

CODE_NOT_RUNNING = "PLUGIN_NOT_RUNNING"
CODE_OPERATION_BUSY = "PLUGIN_OPERATION_BUSY"

# User-facing copy: plain Chinese a beginner can act on. Never interpolate a raw
# exception string here -- it can carry URLs and internal paths.
MESSAGE_UNREACHABLE = "未能连上宿主管理接口，稍后再点一次「查状态」，或直接到插件中心确认『网络搜索』"
# Measured on a packaged host: a management request can sit behind the registry
# reload for 8+ seconds after a plugin starts and answer late, so "no answer in
# time" says nothing about whether anything changed.
MESSAGE_SLOW = "宿主管理接口这次回答太慢（多半正在重载插件列表），稍后再点一次「查状态」"
MESSAGE_STATUS_FAILED = "未能读取宿主插件状态，请到插件中心确认『网络搜索』是否在运行"
MESSAGE_BAD_RESPONSE = "宿主管理接口返回了无法识别的内容，请到插件中心确认『网络搜索』是否在运行"

_RUNNING_TOKENS = frozenset({
    "running", "started", "start", "active", "online", "alive",
    "healthy", "ok", "ready", "degraded", "restarting",
})
_STOPPED_TOKENS = frozenset({
    "stopped", "stop", "stopped_by_user", "not_running", "notrunning", "idle",
    "offline", "disabled", "crashed", "failed", "error", "unknown",
    "unloaded", "absent", "none", "",
})
_LIVENESS_KEYS = ("running", "is_running", "alive", "is_alive", "enabled_and_running")
_STATE_KEYS = ("status", "state", "phase", "lifecycle_state")
_OK_STATUSES = (200, 201, 202, 204)


@dataclass
class HostPluginState:
    """What we believe about one host plugin. ``raw`` is the payload plus ``error``."""

    plugin_id: str
    exists: bool = False
    running: bool = False
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def error(self) -> str:
        """Normalised Chinese hint set by :meth:`HostPluginControl.status`."""
        value = self.raw.get("error") if isinstance(self.raw, dict) else ""
        return str(value or "")

    def as_context_dict(self) -> dict[str, Any]:
        """Shape the panel's ``host_search`` context field expects."""
        return {
            "exists": bool(self.exists),
            "running": bool(self.running),
            "error": self.error,
        }


# --- base URL resolution --------------------------------------------------


def _normalise_base(raw: Any) -> str:
    """Return a loopback http origin for ``raw``, or ``""`` when it is not one."""
    value = str(raw or "").strip().rstrip("/")
    if not value:
        return ""
    if "://" not in value:
        value = f"http://{value}"
    parts = urllib.parse.urlsplit(value)
    if parts.scheme not in {"http", "https"}:
        return ""
    host = (parts.hostname or "").strip("[]").strip(".").casefold()
    if host not in {"127.0.0.1", "localhost", "::1", "ip6-loopback"}:
        return ""
    if parts.query or parts.fragment:
        return ""
    return value


def _call_config_resolver() -> str:
    from config import resolve_user_plugin_base  # type: ignore[import-not-found]

    return str(resolve_user_plugin_base())


def _call_network_resolver() -> str:
    from config.network import resolve_user_plugin_base  # type: ignore[import-not-found]

    return str(resolve_user_plugin_base())


def _read_config_constant() -> str:
    from config import USER_PLUGIN_BASE  # type: ignore[import-not-found]

    return str(USER_PLUGIN_BASE)


def _base_from_host_config() -> str:
    """Tier 1/2: ask the host project, whose root is already on ``sys.path``.

    ``resolve_user_plugin_base`` is preferred because it reports the port that is
    really bound today; the host only re-exports ``USER_PLUGIN_BASE`` from the
    ``config`` package, so ``config.network`` is tried before giving up.
    """
    for loader in (_call_config_resolver, _call_network_resolver, _read_config_constant):
        value = ""
        try:
            value = _normalise_base(loader())
        except Exception:  # a broken or absent config package must not be fatal
            value = ""
        if value:
            return value
    return ""


def _base_from_environment() -> str:
    """Tier 3: the port the launcher published as an environment override."""
    for key in BASE_URL_ENV_KEYS:
        raw = ""
        try:
            raw = (os.environ.get(key) or "").strip()
        except Exception:
            return ""
        if not raw:
            continue
        try:
            port = int(raw)
        except ValueError:
            continue
        if 1 <= port <= 65535:
            value = _normalise_base(f"http://127.0.0.1:{port}")
            if value:
                return value
    return ""


def resolve_base_url(explicit: str = "") -> str:
    """Best available plugin-server origin: explicit -> host config -> env -> constant.

    The tiers are checked lazily, so a caller that already knows the base (the
    panel, a test) never imports the host's ``config`` package.
    """
    if str(explicit or "").strip():
        value = _normalise_base(explicit)
        if value:
            return value
    for tier in (_base_from_host_config, _base_from_environment):
        value = tier()
        if value:
            return value
    return FALLBACK_BASE_URL


# --- payload inspection ---------------------------------------------------


def _as_text(value: Any) -> str:
    return value if isinstance(value, str) else ("" if value is None else str(value))


def _parse_json(body: bytes) -> Any:
    if not body:
        return None
    try:
        return json.loads(body.decode("utf-8", "replace"))
    except Exception:
        return None


def _parse_object(body: bytes) -> dict[str, Any]:
    data = _parse_json(body)
    return dict(data) if isinstance(data, dict) else {}


def _token(value: Any) -> str:
    return _as_text(value).strip().casefold()


def _contains(text: str, markers: tuple[str, ...]) -> bool:
    return any(marker in text for marker in markers)


def _liveness_from(payload: Any) -> bool | None:
    """True/False when the payload states it, None when it simply does not."""
    if not isinstance(payload, dict):
        return None
    for key in _LIVENESS_KEYS:
        if key not in payload:
            continue
        value = payload.get(key)
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
    for key in _STATE_KEYS:
        if key not in payload:
            continue
        value = payload.get(key)
        if isinstance(value, dict):
            nested = _liveness_from(value)
            if nested is not None:
                return nested
        elif isinstance(value, bool):
            return value
        else:
            text = _token(value)
            if text in _RUNNING_TOKENS:
                return True
            if text in _STOPPED_TOKENS:
                return False
    return None


def _looks_like_status_record(payload: Any, plugin_id: str) -> bool:
    """True for the host's per-plugin status record, whatever else it carries.

    The host answers with a synthetic ``{"status": {"status": "stopped"}}`` record
    for an id it has never seen, so ``exists`` here means "the management endpoint
    returned a status record for this id", not "the plugin is installed".
    """
    if not isinstance(payload, dict) or not payload:
        return False
    echoed = _as_text(payload.get("plugin_id")).strip()
    if echoed and echoed != plugin_id:
        return False
    return bool(echoed) or "status" in payload or "state" in payload or "source" in payload


def _error_code_of(payload: dict[str, Any], header: str = "") -> str:
    candidates: list[Any] = [header, payload.get("code"), payload.get("error_code")]
    detail = payload.get("detail")
    if isinstance(detail, dict):
        candidates.extend([detail.get("code"), detail.get("error_code")])
    for candidate in candidates:
        text = _as_text(candidate).strip()
        if text:
            return text[:64]
    return ""


def _message_of(payload: dict[str, Any]) -> str:
    detail = payload.get("detail")
    message = _as_text(payload.get("message"))
    if not message and isinstance(detail, str):
        message = detail
    return message.strip()


def _code_from_exception(error: BaseException) -> str:
    """Recover a machine code even though ``_net`` drops error response bodies."""
    for attribute in ("code", "error_code", "errorcode"):
        text = _as_text(getattr(error, attribute, "")).strip()
        if text:
            return text[:64]
    message = _as_text(getattr(error, "message", "")) or _as_text(error)
    folded = _token(message)
    if _contains(folded, ("plugin_not_running", "not_running")):
        return CODE_NOT_RUNNING
    if _contains(folded, ("plugin_operation_busy", "operation_busy")):
        return CODE_OPERATION_BUSY
    return ""


def _status_of(error: Any) -> int:
    """Read ``.status`` off an exception *or* a response, defensively."""
    try:
        return int(getattr(error, "status", 0) or 0)
    except (TypeError, ValueError):
        return 0


def _timed_out(error: BaseException) -> bool:
    """Did *our own* transport say the request hung past its timeout?

    Matched against ``_net``'s fixed copy (never upstream text, which could be
    anything). A hang means the host is slow to answer -- the caller should
    re-check the state instead of sending the user to the plugin centre.
    """
    if isinstance(error, (TimeoutError, socket.timeout)):
        return True
    return "请求超时" in _as_text(error)


def _header_of(response: Any, name: str) -> str:
    getter = getattr(response, "header", None)
    if not callable(getter):
        return ""
    try:
        return _as_text(getter(name)).strip()
    except Exception:
        return ""


def _with_code(message: str, code: str) -> str:
    cleaned = "".join(char for char in _as_text(code) if 32 < ord(char) < 127)[:48]
    if not cleaned:
        return message
    suffix = f"（{cleaned}）"
    return message if message.endswith(suffix) else f"{message}{suffix}"


# --- the control object ---------------------------------------------------


def _check_plugin_id(plugin_id: Any) -> str:
    """Exact-match whitelist. Anything else is a caller bug, so it raises."""
    value = plugin_id if isinstance(plugin_id, str) else ""
    if value not in ALLOWED_PLUGIN_IDS:
        raise ValueError(
            f"refusing to read host plugin {value!r}: only {sorted(ALLOWED_PLUGIN_IDS)} "
            "is reported on by this plugin"
        )
    return value


class HostPluginControl:
    """Synchronous, stateless client for ``/plugin/status`` on the host's loopback API.

    ``status`` normalises *every* operational failure into a value the panel can
    print directly: a :class:`HostPluginState` whose ``raw`` carries ``error``. The
    only exception that escapes is :class:`ValueError` for a non-whitelisted plugin
    id.
    """

    def __init__(self, base_url: str = "", timeout: float = DEFAULT_TIMEOUT_SECONDS) -> None:
        self.base_url: str = resolve_base_url(base_url)
        try:
            resolved = float(timeout)
        except (TypeError, ValueError):
            resolved = DEFAULT_TIMEOUT_SECONDS
        self.timeout: float = resolved if resolved > 0 else DEFAULT_TIMEOUT_SECONDS

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"HostPluginControl(base_url={self.base_url!r}, timeout={self.timeout!r})"

    # -- transport

    def _call(self, method: str, path: str, params: dict[str, str] | None = None) -> Any:
        """One loopback request. ``policy=none`` keeps system proxies out of it."""
        return _net.request(
            method,
            f"{self.base_url}{path}",
            params=params,
            headers={"Accept": "application/json"},
            policy=_net.POLICY_NONE,
            proxy_url="",
            timeout=self.timeout,
        )

    # -- reads

    def status(self, plugin_id: str) -> HostPluginState:
        """Read one plugin's state. Operational failures never raise."""
        pid = _check_plugin_id(plugin_id)
        try:
            response = self._call("GET", "/plugin/status", {"plugin_id": pid})
        except Exception as error:
            return self._failed_state(pid, error)

        status = _status_of(response)
        payload = _parse_object(getattr(response, "body", b""))
        if status not in _OK_STATUSES:
            code = _error_code_of(payload, _header_of(response, "X-Error-Code"))
            hint = _with_code(MESSAGE_STATUS_FAILED, code)
            return HostPluginState(pid, False, False, {"error": hint, "status": status})
        if not _looks_like_status_record(payload, pid):
            raw: dict[str, Any] = {"error": MESSAGE_BAD_RESPONSE}
            if payload:
                raw["payload"] = payload
            else:
                raw["payload_type"] = _as_text(type(_parse_json(getattr(response, "body", b""))).__name__)
            return HostPluginState(pid, False, False, raw)

        running = _liveness_from(payload)
        live = bool(running)
        exists = True if live else running is not None or _looks_registered(payload, pid)
        raw = dict(payload)
        message = _message_of(payload)
        if message:
            raw["message"] = message
        return HostPluginState(pid, exists, live, raw)

    def _failed_state(self, plugin_id: str, error: BaseException) -> HostPluginState:
        if isinstance(error, _net.HttpStatusCodeError):
            hint = _with_code(MESSAGE_STATUS_FAILED, _code_from_exception(error))
        elif _timed_out(error):
            hint = MESSAGE_SLOW
        else:
            hint = MESSAGE_UNREACHABLE
        return HostPluginState(plugin_id, False, False, {"error": hint})


def _looks_registered(payload: dict[str, Any], plugin_id: str) -> bool:
    """Weak "the host knows this plugin" signal for a stopped record."""
    if _as_text(payload.get("plugin_id")).strip() == plugin_id:
        return True
    status_value = payload.get("status")
    return isinstance(status_value, dict) and bool(status_value)
