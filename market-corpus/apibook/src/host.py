"""Loopback integration; current settings import excludes all credential fields."""

import json
import re

import httpx

from .domain import DEFAULTS, SLOTS, PresetError, blank_preset, normalize_preset, text


def provider_catalog(raw):
    if not isinstance(raw, dict) or raw.get("success") is not True:
        raise PresetError("metadata_failed")
    core = raw.get("core_api_providers_full", {})
    assist = raw.get("assist_api_providers_full", {})
    keybook = raw.get("keybook_api_providers_full", {})
    registry = raw.get("api_key_registry", {})
    if not all(isinstance(v, dict) for v in (core, assist, keybook, registry)) or not core or not assist:
        raise PresetError("metadata_failed")
    if any(len(v) > 64 for v in (core, assist, keybook, registry)):
        raise PresetError("metadata_failed")
    labels = {key: str(v.get("name", key))[:160] for key, v in (keybook | core | assist).items() if isinstance(v, dict)}
    keys = {}
    for key, value in registry.items():
        field = value.get("config_field", "") if isinstance(value, dict) else ""
        if isinstance(field, str) and re.fullmatch(r"assistApiKey[A-Za-z0-9]+", field):
            keys[key] = field
            labels.setdefault(key, str(value.get("label", key))[:160])
    slot_providers = {}
    for slot in SLOTS:
        # Regular follow_core currently needs the frontend to materialize a
        # built-in model ID. Clearing IDs can pair the assist model with the
        # core endpoint. Keep built-in models host-managed instead of freezing
        # those IDs into a saved override.
        choices = ["follow_assist"]
        if slot in ("gameMain", "gameSummary"):
            choices = [DEFAULTS[slot], "follow_core", "follow_assist"]
        # Built-in providers belong to core/assist only: never materialize their
        # default URLs/models into per-slot overrides (including TTS dispatch).
        builtin = []
        if slot == "omni":
            choices = ["follow_core"]
        elif slot == "tts":
            choices = ["follow_core", "follow_assist"]
            if any(v.get("key") == "gptsovits" for v in raw.get("tts_providers", []) if isinstance(v, dict)):
                builtin.append("gptsovits")
        elif slot == "image":
            choices = ["disabled"]
        slot_providers[slot] = list(dict.fromkeys(choices + builtin + ["custom"]))
    # Public model hints only. Never copy provider credential/default-key fields.
    models = {
        "core": {p: str(v.get("core_model", ""))[:200] for p, v in core.items() if isinstance(v, dict)},
        "assist": {p: {s: str(v.get(s + "_model", v.get("conversation_model", "")))[:200]
                       for s in ("conversation", "summary", "correction", "emotion", "vision", "agent")}
                   for p, v in assist.items() if isinstance(v, dict)},
    }
    return {"core": list(core), "assist": list(assist), "labels": labels, "key_fields": keys,
            "slot_providers": slot_providers, "models": models}


def preset_from_host_config(raw, catalog):
    """Import a settings draft from an explicit non-secret field allowlist."""
    if not isinstance(raw, dict) or raw.get("success") is not True:
        raise PresetError("host_response_invalid")
    draft = blank_preset()
    draft["enableCustomApi"] = raw.get("enableCustomApi", False)
    for target, field in (("core", "coreApi"), ("assist", "assistApi")):
        provider = text(raw.get(field, "free"), 80, "invalid_provider")
        if provider not in catalog[target]:
            raise PresetError("unsupported_provider")
        draft[target] = provider
    skipped = []
    for slot in SLOTS:
        prefix = slot + "Model"
        provider = text(raw.get(prefix + "Provider", ""), 80, "invalid_provider")
        # Older settings may store a custom triple without a provider selector.
        if not provider:
            provider = "custom" if raw.get(prefix + "Url") else DEFAULTS[slot]
        if provider not in catalog["slot_providers"][slot]:
            skipped.append(slot)
            provider = DEFAULTS[slot]
        value = {"provider": provider}
        if provider in {"custom", "gptsovits"}:
            value.update(url=raw.get(prefix + "Url", ""), model=raw.get(prefix + "Id", ""), key="")
        draft["slots"][slot] = value
    # Validate the same limits as a saved preset, without requiring a draft name.
    draft = normalize_preset(draft | {"name": "import"}) | {"name": ""}
    return {"preset": draft, "skipped_slots": skipped}


class NekoHost:
    def __init__(self, port):
        if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
            raise PresetError("invalid_port")
        self.base = f"http://127.0.0.1:{port}"

    async def request(self, method, path, payload=None):
        # Per-call client: no proxy, redirects, retries or persistent sockets.
        async with httpx.AsyncClient(
            trust_env=False, follow_redirects=False,
            timeout=httpx.Timeout(75 if method == "POST" else 6, connect=3),
            limits=httpx.Limits(max_connections=1),
        ) as client:
            async with client.stream(method, self.base + path, json=payload) as response:
                if not 200 <= response.status_code < 300:
                    error = PresetError("host_http_error")
                    error.http_status = response.status_code
                    raise error
                content = bytearray()
                async for part in response.aiter_bytes():
                    if len(content) + len(part) > 1024 * 1024:
                        raise PresetError("host_response_limit")
                    content.extend(part)
                try:
                    return json.loads(bytes(content).decode("utf-8", errors="strict"))
                except (ValueError, UnicodeError):
                    raise PresetError("host_response_invalid") from None

    async def catalog(self):
        return provider_catalog(await self.request("GET", "/api/config/api_providers"))

    async def current_draft(self):
        catalog = await self.catalog()
        # The public endpoint masks secrets. Do not inspect or retain its Key
        # placeholders, display fragments, tokens, voice or unrelated settings.
        return preset_from_host_config(await self.request("GET", "/api/config/core_api"), catalog)

    async def apply(self, payload):
        result = await self.request("POST", "/api/config/core_api", payload)
        if not isinstance(result, dict):
            raise PresetError("host_response_invalid")
        if result.get("success") is not True:
            if "配置已保存但重新加载失败" in str(result.get("error", "")):
                raise PresetError("saved_reload_failed")
            raise PresetError("host_rejected")
        return {"sessions_ended": result.get("sessions_ended", 0)}
