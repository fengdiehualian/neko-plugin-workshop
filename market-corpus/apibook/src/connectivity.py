"""Request-scoped probes of plugin-owned credentials; no host configuration writes."""

import asyncio
import json
import re

import httpx

from .domain import SLOTS, PresetError, active_slot
from .host import provider_catalog

ERROR_CODES = {
    "auth_failed", "timeout", "dns_error", "connection_refused", "ssl_error",
    "missing_params", "empty_response", "unknown", "ws_error",
}


def safe_http_status(result):
    """Retain only a status number, never arbitrary upstream text/URLs/secrets."""
    status = result.get("http_status", result.get("status_code"))
    if type(status) is int and 100 <= status <= 599:
        return status
    error = result.get("error")
    if isinstance(error, str):
        match = re.match(r"^HTTP\s+([1-5]\d{2})\b", error[:32])
        if match:
            return int(match[1])
    return None


def probe_plan(preset, metadata):
    """At most 13 groups. Identical endpoint/model/credential/protocol share one probe."""
    catalog = provider_catalog(metadata)
    core_profiles = metadata["core_api_providers_full"]
    assist_profiles = metadata["assist_api_providers_full"]
    groups = {}

    def target(urls=(), model="", key="", protocol="openai_compatible", *, body=None, note="", status="untested", is_free=False):
        urls = tuple(dict.fromkeys(str(u).strip().rstrip("/") for u in urls if u))
        if len(urls) > 8 or any(len(u) > 2048 for u in urls) or len(str(model)) > 200:
            raise PresetError("metadata_failed")
        if protocol == "openai_compatible" and urls and urls[0].endswith("/messages"):
            protocol = "anthropic"  # The host normalizes this endpoint identically.
        return {"urls": urls, "model": str(model), "key": key, "protocol": protocol,
                "body": body, "note": note, "status": status, "is_free": is_free}

    def builtin(provider, scope, slot="conversation"):
        profiles = core_profiles if scope == "core" else assist_profiles
        profile = profiles.get(provider)
        if provider not in catalog[scope] or not isinstance(profile, dict):
            return target(status="unsupported", note="unsupported")
        free = profile.get("is_free_version") is True or provider == "free"
        key = "free-access" if free else preset["keys"].get(provider, "")
        url_field = "core_url" if scope == "core" else "openrouter_url"
        candidates = profile.get(url_field + "s") or [profile.get(url_field, "")]
        if not isinstance(candidates, list):
            raise PresetError("metadata_failed")
        model_field = "core_model" if scope == "core" else slot + "_model"
        model = profile.get(model_field) or profile.get("conversation_model", "")
        protocol = "websocket" if scope == "core" else profile.get("provider_type", "openai_compatible")
        note = ""
        if scope == "core" and not any(candidates):
            # Mirrors the core endpoint's SDK-provider credential-only fallback.
            fallback = builtin(provider, "assist")
            fallback["note"] = "credential_only"
            return fallback
        if protocol not in {"websocket", "anthropic", "openai_compatible"}:
            return target(model=model, status="unsupported", note="unsupported")
        body = {"provider_key": provider, "provider_scope": "core", "api_key": key} if scope == "core" else None
        status = "untested" if any(candidates) and model and (key or free) else "not_configured"
        return target(candidates, model, key, protocol, body=body, status=status, note=note, is_free=free)

    def resolve(slot):
        value = active_slot(preset, slot)
        provider = value["provider"]
        if provider == "disabled":
            return target(status="not_configured", note="disabled")
        if slot == "image":
            return target(model=value.get("model", ""), status="unsupported", note="unsupported")
        if slot == "tts" and provider != "custom":
            # Runtime built-in speech depends on the character's voice/worker,
            # which this plugin neither owns nor imports. A chat probe is not TTS.
            return target(model=value.get("model", ""), status="unsupported", note="voice_required")
        if provider == "custom":
            protocol = "websocket" if slot == "omni" else "tts" if slot == "tts" else "openai_compatible"
            return target([value["url"]], value["model"], value["key"], protocol,
                          note="default_voice" if slot == "tts" else "")
        if provider == "follow_conversation":
            return resolve("conversation")
        if provider == "follow_summary":
            return resolve("summary")
        if slot == "omni":
            return builtin(preset["core"], "core")
        scope = "core" if provider == "follow_core" else "assist"
        selected = preset[scope]
        if slot in {"gameMain", "gameSummary"}:
            # The host resolves explicit game follow modes to these two models.
            model_slot = "summary" if slot == "gameSummary" else "conversation"
        else:
            model_slot = slot
        return builtin(selected, "assist", model_slot)

    def add(function, value):
        # Keys remain only in request-scoped fingerprints/payloads, never in results.
        identity = (value["urls"], value["model"], value["key"], value["protocol"], value["status"],
                    value["note"] if value["status"] != "untested" else "")
        if identity not in groups:
            groups[identity] = value | {"functions": []}
        elif value["note"]:
            groups[identity]["note"] = value["note"]
        # Metadata-trusted free endpoints use the host's reachability policy.
        # A custom model with a different URL/model/key/protocol never inherits it.
        groups[identity]["is_free"] = groups[identity]["is_free"] or value["is_free"]
        groups[identity]["functions"].append(function)

    add("core", builtin(preset["core"], "core"))
    add("assist", builtin(preset["assist"], "assist"))
    for slot in SLOTS:
        add(slot, resolve(slot))
    return list(groups.values())


def public_groups(groups):
    return [{"functions": g["functions"], "model": g["model"], "status": g["status"],
             "note": g["note"], "error_code": g.get("error_code", ""),
             "http_status": g.get("http_status"),
             "free_policy": g["is_free"] and g["protocol"] in {"openai_compatible", "anthropic"}} for g in groups]


async def test_preset(host, preset):
    metadata = await host.request("GET", "/api/config/api_providers")
    groups = probe_plan(preset, metadata)
    semaphore = asyncio.Semaphore(4)

    async def one(group):
        if group["status"] != "untested":
            return
        async with semaphore:
            try:
                async def request():
                    result = None
                    for url in group["urls"]:
                        payload = group["body"] or {
                            "url": url, "api_key": group["key"], "model": group["model"],
                            "provider_type": group["protocol"],
                            # Preserve native free-provider policy. Explicit custom
                            # models stay strict and still probe their actual model.
                            "is_free": group["is_free"],
                        }
                        if group["protocol"] == "tts":
                            payload = payload | {"sub_type": "openai_tts"}
                        result = await host.request("POST", "/api/config/test_connectivity", payload)
                        if not isinstance(result, dict) or not isinstance(result.get("success"), bool):
                            raise PresetError("host_response_invalid")
                        if result["success"]:
                            return result
                        if group["body"]:  # Built-in core endpoint already tries its candidates.
                            break
                    return result

                result = await asyncio.wait_for(request(), timeout=18)
                group["status"] = "connected" if result["success"] else "failed"
                group["http_status"] = safe_http_status(result)
                code = result.get("error_code", "unknown")
                group["error_code"] = "" if result["success"] else code if code in ERROR_CODES else "unknown"
            except (TimeoutError, httpx.TimeoutException):
                group.update(status="failed", error_code="timeout")
            except PresetError as exc:
                group.update(status="failed", error_code="backend_unavailable",
                             http_status=safe_http_status({"http_status": getattr(exc, "http_status", None)}))
            except Exception:
                # Do not expose raw upstream error text, URLs or credentials.
                group.update(status="failed", error_code="backend_unavailable")

    tasks = [asyncio.create_task(one(group)) for group in groups]
    try:
        await asyncio.gather(*tasks)
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        # Request-scoped secrets and fingerprints have no persistent cache.
    result = {"groups": public_groups(groups), "tested": sum(g["status"] in {"connected", "failed"} for g in groups)}
    # Guard output shape/size even if public metadata is malformed.
    if len(json.dumps(result, ensure_ascii=False).encode("utf-8")) > 16384:
        raise PresetError("metadata_failed")
    return result
