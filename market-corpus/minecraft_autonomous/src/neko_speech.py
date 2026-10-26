"""Explicit public output-only adapter; never generates text or owns body work.

Only explicit tool/planner spoken_text crosses this sanitizer. The host's ordinary
generated final answer is outside this plugin's public API boundary.
"""

import asyncio
import hashlib
import json
import re
import sqlite3
from urllib.parse import urlsplit

import httpx
from agent_mc_autonomy.configuration import _boolean, _integer, _text

from .neko_commentary import load_commentary, save_commentary


def sanitize_speech(value):
    if not isinstance(value, str) or len(value) > 600:
        return ""
    # Marked reasoning is internal even when a model puts it in spoken_text.
    # Ordinary English/game names remain speech; never guess from language.
    value = re.sub(r"<think(?:ing)?\s*>.*?</think(?:ing)?\s*>", "", value, flags=re.I | re.S)
    if re.search(r"</think(?:ing)?\s*>", value, flags=re.I):
        value = re.sub(r"^.*?</think(?:ing)?\s*>", "", value, count=1, flags=re.I | re.S)
    value = re.split(r"<think(?:ing)?\s*>", value, maxsplit=1, flags=re.I)[0]
    # Reserved metadata accepts whitespace/chunk splits, nested brackets and
    # future suffixes. Ordinary bracketed game names remain ordinary speech.
    prefix = r"(?:neko\s*_\s*\w+|tts\s*[_:-]\s*\w+|voice\s*[_:-]\s*\w+|speech\s*[_:-]\s*\w+|emotion|prosody|control)"
    value = re.sub(r"<\s*" + prefix + r"\b[^>]*>.*?</\s*[^>]+>", "", value, flags=re.I | re.S)
    value = re.sub(r"[\[<【]+\s*/?\s*" + prefix + r"\b[^\]>】]*(?:[\]>】]+|$)", "", value, flags=re.I | re.S)
    placeholder = (r"no[\s-]+(?:new[\s-]+)?(?:commentary|comments?)"
                   r"(?:[\s-]+(?:is[\s-]+)?needed)?(?:[\s-]+(?:for[\s-]+)?this[\s-]+round)?"
                   r"|本轮(?:无需|不需要|没有)口播|保持安静|无需回复|最终口播正文为空")
    # Normalize wrappers only for recognition, before accepting any speech.
    # Unicode whitespace can alternate with nested delimiters. Check the full
    # message before individual lines so internal newlines cannot leave a
    # speakable fragment behind. Only recognition uses this normalized view.
    wrappers = " \t\r\n[]【】<>〈〉《》()（）{}「」『』\"'‘’“”`*.!。！"
    edges = r"[\s" + re.escape(wrappers) + "]"
    def placeholder_only(text):
        normalized = re.sub("^" + edges + "+|" + edges + "+$", "", text)
        return re.fullmatch(placeholder, normalized, flags=re.I) is not None
    if placeholder_only(value):
        return ""
    value = "\n".join(line for line in value.splitlines() if not placeholder_only(line))
    value = value.strip(" \t\r\n[]【】<>")
    return value if any(c.isalnum() for c in value) else ""


def output_settings(raw, target):
    section = raw.get("speech_output", {})
    if not isinstance(section, dict):
        raise ValueError("speech_output must be a table")
    _boolean(section, "planner_narration", True)
    _integer(section, "min_interval_seconds", 30, 10, 900)
    if not _boolean(section, "enabled", False):
        return None
    base = _text(section, "base_url").rstrip("/")
    url = urlsplit(base)
    if (url.scheme not in {"http", "https"} or url.hostname not in {"localhost", "127.0.0.1", "::1"}
            or url.username or url.password or url.query or url.fragment or url.path):
        raise ValueError("speech_output requires an explicit local Neko base URL")
    return base


def enqueue_speech(plugin, raw, settings, target, port, generation, text, correlation, *, admission=None, source_binding=None):
    """Reserve locally before the goal mailbox; HTTP/audio never blocks it.

    Durable reservations survive crashes as unknown and are never replayed.
    At most one output request is active per plugin; busy is an honest refusal.
    """
    common = {**correlation, "playback_verified": False}
    path = port.directory / "speech-output.json"
    state = load_commentary(path)
    key = correlation["opportunity_id"]
    records = state.get("records", {})
    if key in records:
        return records[key]
    base = output_settings(raw, target)
    if not base or not target:
        return {**common, "status": "disabled"}
    line = sanitize_speech(text)
    if not line:
        return {**common, "status": "empty_or_control_only"}
    digest = hashlib.sha256(line.encode()).hexdigest()
    binding = source_binding or (json.loads(admission.binding_json) if admission and admission.matches(correlation) else None)
    if binding is not None:
        from .neko_recent_context import owned_runtime
        if owned_runtime(plugin, settings, target, port, generation) != binding:
            binding = None
    if source_binding is not None and binding is None:
        return {**common, "status": "rejected", "reason": "runtime_not_owned"}
    if binding is not None and (source_binding is not None or admission.ordinary):
        for prior in reversed(list(records.values())):
            bound = prior.get("runtime_binding")
            if (isinstance(bound, dict) and all(bound.get(k) == binding[k] for k in ("actor", "identity", "session", "run", "scope"))
                    and prior.get("status") in {"accepted", "queued", "reserved_unknown", "unknown"}
                    and prior.get("text_sha256") == digest and type(prior.get("offered_at")) in {int, float}
                    and 0 <= port.now() - prior["offered_at"] <= 300):
                receipt = {**common, "status": "repeat_suppressed", "text_sha256": digest,
                           "runtime_binding": binding, "offered_at": port.now(), "transport_accepted": False,
                           "prior_opportunity_id": prior["opportunity_id"], "prior_speech_id": prior.get("speech_id"),
                           "audio_queued": False, "audio_completed": False}
                records[key] = receipt
                save_commentary(path, {"records": dict(list(records.items())[-32:])})
                return receipt
    tasks = getattr(plugin, "_speech_output_tasks", set())
    if tasks:
        return {**common, "status": "busy"}
    receipt = {**common, "status": "reserved_unknown", "text_sha256": digest,
               "offered_at": port.now(), "transport_accepted": False}
    if binding is not None:
        receipt["runtime_binding"] = binding
    records[key] = receipt
    save_commentary(path, {"records": dict(list(records.items())[-32:])})
    task = asyncio.create_task(_deliver(plugin, base, target, port, generation, line, receipt, path))
    plugin._speech_output_tasks = tasks
    tasks.add(task)
    task.add_done_callback(tasks.discard)
    return {**receipt, "status": "enqueued", "delivery_receipt": "speech-output.json"}


def _current(plugin, port, generation, receipt):
    from .neko_heartbeat import is_stopped
    current = (plugin._generation == generation and not plugin._configuration_writes and not is_stopped(plugin)
               and port.speech_current(receipt))
    if current and receipt.get("source") == "planner":
        from agent_mc_autonomy.ownership import session_is_owned
        current = session_is_owned(port.directory)
    if current and receipt.get("runtime_binding"):
        from agent_mc_autonomy.mailbox import observation
        snapshot, bound = observation(port.directory), receipt["runtime_binding"]
        current = all(snapshot.get(k) == bound[v] for k, v in (
            ("runtime_session", "session"), ("native_run", "run"), ("settings_identity", "identity"), ("game_identity", "scope")))
    return current


async def _deliver(plugin, base, target, port, generation, line, receipt, path):
    status, accepted, reason = "unknown", False, "transport_unknown"
    sent = False
    public = {}
    try:
        if not _current(plugin, port, generation, receipt):
            status, reason = "rejected", "stopped_or_superseded"
            return
        async with httpx.AsyncClient(timeout=3, trust_env=False, follow_redirects=False) as client:
            # Public capability discovery, never private host globals or route takeover.
            capability = await client.get(base + "/openapi.json")
            if capability.status_code != 200 or "post" not in capability.json().get("paths", {}).get("/api/game/{game_type}/speak", {}):
                status, reason = "unsupported", "public_output_endpoint_unavailable"
                return
            if not _current(plugin, port, generation, receipt):
                status, reason = "rejected", "stopped_or_superseded"
                return
            sent = True
            response = await client.post(base + "/api/game/minecraft_autonomous/speak", headers={"Origin": base}, json={
                "lanlan_name": target, "session_id": "minecraft-" + hashlib.sha256(receipt["session"].encode()).hexdigest()[:24],
                "line": line, "request_id": receipt["decision_id"], "sdk_speech_correlation_id": receipt["opportunity_id"],
                "interrupt_audio": False, "emit_turn_end": False, "wait_for_audio_completion": True,
                "mirror_text": True, "playback_gain": 1.0}, timeout=httpx.Timeout(55, connect=3))
            result = response.json()
            accepted = response.status_code == 200 and result.get("ok") is True
            if isinstance(result, dict):
                speech_id = result.get("speech_id")
                if isinstance(speech_id, str) and re.fullmatch(r"[A-Za-z0-9_.:-]{1,160}", speech_id):
                    public["speech_id"] = speech_id
                for field in ("audio_queued", "audio_completed"):
                    if type(result.get(field)) is bool:
                        public[field] = result[field]
            if accepted:
                status, reason = "accepted", "host_output_accepted"
            elif result.get("ok") is False:
                status = "rejected"
                # Bounded machine codes only; never persist raw HTTP bodies or secrets.
                code = result.get("reason", "host_output_rejected")
                reason = code if isinstance(code, str) and re.fullmatch(r"[a-z_]{1,80}", code) else "host_output_rejected"
    except asyncio.CancelledError:
        status, reason = ("unknown", "cancelled_after_submission") if sent else ("rejected", "cancelled_before_submission")
        raise
    except (httpx.HTTPError, ValueError, TypeError, AttributeError):
        status, reason = ("unknown", "transport_unknown") if sent else ("unavailable", "capability_check_failed")
    finally:
        state = load_commentary(path)
        records = state.get("records", {})
        records[receipt["opportunity_id"]] = {**receipt, "status": status, "reason": reason,
            "transport_accepted": accepted, "updated_at": port.now(), **public,
            "playback_verified": False}
        try:
            save_commentary(path, {"records": dict(list(records.items())[-32:])})
            if receipt.get("source") == "planner":
                port.delivered(receipt, status)
        except (OSError, sqlite3.Error):
            plugin.logger.warning("Minecraft speech receipt persistence failed; output remains unknown and must not replay.")
