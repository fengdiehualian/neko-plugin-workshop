"""Validated plugin-owned presets and one-way N.E.K.O configuration patches."""

import re
from copy import deepcopy
from urllib.parse import urlsplit

KEEP_SECRET = "__API_PRESETS_KEEP_SECRET__"
MAX_PRESETS = 100
MAX_VENDORS = 32
SLOTS = (
    "conversation", "summary", "gameMain", "gameSummary", "correction",
    "emotion", "vision", "agent", "omni", "tts", "image",
)
DEFAULTS = dict.fromkeys(SLOTS, "follow_assist") | {
    "omni": "follow_core", "gameMain": "follow_conversation",
    "gameSummary": "follow_summary", "image": "disabled",
}
FOLLOW_MODES = {"follow_core", "follow_assist", "follow_conversation", "follow_summary"}


class PresetError(ValueError):
    """Stable error codes; never include submitted secrets or URLs in errors."""


def text(value, limit, code, *, strip=True):
    if not isinstance(value, str) or len(value) > limit or "\x00" in value:
        raise PresetError(code)
    return value.strip() if strip else value


def secret(value, previous=""):
    value = text(value, 4096, "invalid_key")
    if value == KEEP_SECRET:
        if not previous:
            raise PresetError("missing_saved_key")
        return previous
    if value == "__NEKO_SECRET_MASKED__" or (value and set(value) <= {"*", "•"}):
        raise PresetError("invalid_key")
    if any(ord(c) < 33 or ord(c) > 126 for c in value):
        raise PresetError("invalid_key")
    return value


def endpoint(value, slot):
    value = text(value, 2048, "invalid_url")
    try:
        parsed = urlsplit(value)
        parsed.port
        schemes = {"ws", "wss"} if slot == "omni" else {"http", "https"}
        if slot == "image":
            schemes = {"https"}
        if (
            parsed.scheme not in schemes or not parsed.hostname
            or parsed.username or parsed.password or parsed.fragment or parsed.query
            or any(c.isspace() or ord(c) < 32 for c in value)
        ):
            raise ValueError
    except ValueError:
        raise PresetError("invalid_url") from None
    return value.rstrip("/")


def blank_preset():
    return {"name": "", "notes": "", "core": "free", "assist": "free", "keys": {}, "slots": {}, "vendors": {}, "enableCustomApi": False}


def custom_enabled(preset):
    """Preserve legacy apply behavior only when a preset has no explicit switch."""
    return preset.get("enableCustomApi", any(
        value["provider"] != DEFAULTS[slot] for slot, value in preset.get("slots", {}).items()
    ))


def normalize_preset(raw, previous=None):
    if not isinstance(raw, dict) or set(raw) - {"name", "notes", "core", "assist", "keys", "slots", "vendors", "enableCustomApi"}:
        raise PresetError("invalid_preset")
    if "enableCustomApi" in raw and type(raw["enableCustomApi"]) is not bool:
        raise PresetError("invalid_preset")
    old = previous or blank_preset()
    name = text(raw.get("name", ""), 80, "invalid_name")
    if not name:
        raise PresetError("invalid_name")
    core = text(raw.get("core", "free"), 80, "invalid_provider")
    assist = text(raw.get("assist", "free"), 80, "invalid_provider")
    if not core or not assist:
        raise PresetError("invalid_provider")
    keys, slots = raw.get("keys", {}), raw.get("slots", {})
    if not isinstance(keys, dict) or len(keys) > 64 or not isinstance(slots, dict) or set(slots) - set(SLOTS):
        raise PresetError("invalid_preset")
    clean_keys = {}
    for provider, value in keys.items():
        provider = text(provider, 80, "invalid_provider")
        if not provider:
            raise PresetError("invalid_provider")
        clean_keys[provider] = secret(value, old["keys"].get(provider, ""))
    vendors = raw.get("vendors", {})
    if not isinstance(vendors, dict) or len(vendors) > MAX_VENDORS:
        raise PresetError("vendor_limit")
    clean_vendors, vendor_names = {}, set()
    for vendor_id, value in vendors.items():
        if not isinstance(vendor_id, str) or not re.fullmatch(r"vendor_[1-9]\d{0,5}", vendor_id):
            raise PresetError("invalid_vendor")
        if not isinstance(value, dict) or set(value) != {"name", "url", "key"}:
            raise PresetError("invalid_vendor")
        vendor_name = text(value["name"], 80, "invalid_vendor")
        if not vendor_name or any(ord(c) < 32 for c in vendor_name) or vendor_name.casefold() in vendor_names:
            raise PresetError("invalid_vendor")
        vendor_names.add(vendor_name.casefold())
        url = value["url"]
        transport = "omni" if isinstance(url, str) and url.startswith(("ws://", "wss://")) else "conversation"
        clean_vendors[vendor_id] = {
            "name": vendor_name, "url": endpoint(url, transport),
            "key": secret(value["key"], old.get("vendors", {}).get(vendor_id, {}).get("key", "")),
        }
    clean_slots = {}
    for slot, value in slots.items():
        if not isinstance(value, dict) or set(value) - {"provider", "url", "model", "key", "vendor_id"}:
            raise PresetError("invalid_slot")
        provider = text(value.get("provider", DEFAULTS[slot]), 80, "invalid_provider")
        vendor_id = value.get("vendor_id")
        if vendor_id is not None and (provider != "custom" or not isinstance(vendor_id, str) or vendor_id not in clean_vendors):
            raise PresetError("invalid_vendor")
        allowed_follow = {"follow_core"} if slot == "omni" else {"follow_core", "follow_assist"}
        if slot == "gameMain":
            allowed_follow.add("follow_conversation")
        if slot == "gameSummary":
            allowed_follow.add("follow_summary")
        if slot == "image":
            allowed_follow = {"disabled"}
        if provider in FOLLOW_MODES | {"disabled"}:
            if provider not in allowed_follow:
                raise PresetError("invalid_follow")
            clean_slots[slot] = {"provider": provider}
        elif provider == "custom" or (slot == "tts" and provider == "gptsovits"):
            model = text(value.get("model", ""), 200, "invalid_model")
            if not model and provider != "gptsovits":
                raise PresetError("invalid_model")
            if any(ord(c) < 32 for c in model):
                raise PresetError("invalid_model")
            if vendor_id is not None:
                if set(value) - {"provider", "model", "vendor_id"}:
                    raise PresetError("invalid_slot")
                endpoint(clean_vendors[vendor_id]["url"], slot)
                clean_slots[slot] = {"provider": "custom", "model": model, "vendor_id": vendor_id}
            else:
                clean_slots[slot] = {
                    "provider": provider, "url": endpoint(value.get("url", ""), slot), "model": model,
                    "key": secret(value.get("key", ""), old["slots"].get(slot, {}).get("key", "")),
                }
        elif provider:
            clean_slots[slot] = {"provider": provider}
        else:
            raise PresetError("invalid_provider")
    return {
        "name": name, "notes": text(raw.get("notes", ""), 2000, "invalid_notes", strip=False),
        "core": core, "assist": assist, "keys": clean_keys, "slots": clean_slots, "vendors": clean_vendors,
        "enableCustomApi": custom_enabled(raw | {"slots": clean_slots}),
    }


def public_preset(preset):
    result = deepcopy(preset)
    result["keys"] = {p: KEEP_SECRET if key else "" for p, key in result["keys"].items()}
    for value in result["slots"].values():
        if value.get("key"):
            value["key"] = KEEP_SECRET
    for value in result.get("vendors", {}).values():
        value["key"] = KEEP_SECRET if value["key"] else ""
    return result


def effective_slot(preset, slot):
    """Materialize a plugin-local reference only for applying or probing."""
    value = preset["slots"].get(slot, {"provider": DEFAULTS[slot]})
    if "vendor_id" not in value:
        return value
    vendor = preset.get("vendors", {}).get(value["vendor_id"])
    if not vendor:
        raise PresetError("invalid_vendor")
    return {"provider": "custom", "model": value["model"], "url": endpoint(vendor["url"], slot), "key": vendor["key"]}


def active_slot(preset, slot):
    """Resolve enabled settings without altering saved content; GSV is independent."""
    if not custom_enabled(preset) and not (slot == "tts" and preset["slots"].get(slot, {}).get("provider") == "gptsovits"):
        return {"provider": DEFAULTS[slot]}
    return effective_slot(preset, slot)


def build_payload(preset, catalog):
    """Send a complete managed scope, never fetch host credentials or write its files."""
    core, assist = preset["core"], preset["assist"]
    if core not in catalog["core"] or assist not in catalog["assist"]:
        raise PresetError("unsupported_provider")
    needed = {p for p in (core, assist) if p != "free"}
    keys = preset["keys"]
    payload = {
        "coreApi": core, "assistApi": assist,
        "coreApiKey": "free-access" if core == "free" else keys.get(core, ""),
        "enableCustomApi": custom_enabled(preset), "gptsovitsEnabled": False, "useMimoTokenPlan": False,
    }
    for slot in SLOTS:
        value = effective_slot(preset, slot)
        provider = value["provider"]
        if provider not in catalog["slot_providers"][slot]:
            raise PresetError("unsupported_slot_provider")
        prefix = f"{slot}Model"
        payload.update({prefix + "Provider": provider, prefix + "Url": "", prefix + "Id": "", prefix + "ApiKey": ""})
        if provider not in FOLLOW_MODES | {"disabled"}:
            if provider in {"custom", "gptsovits"}:
                payload.update({prefix + "Url": value["url"], prefix + "Id": value["model"], prefix + "ApiKey": value["key"]})
                if slot == "tts":
                    payload["ttsProvider"] = provider
            else:
                if payload["enableCustomApi"]:
                    needed.add(provider)
    for provider in needed:
        if provider not in catalog["key_fields"] or not keys.get(provider):
            raise PresetError("required_key")
    for provider, key in keys.items():
        field = catalog["key_fields"].get(provider)
        if not field:
            raise PresetError("unsupported_provider")
        payload[field] = key
    if preset["slots"].get("tts", {}).get("provider", DEFAULTS["tts"]) in FOLLOW_MODES:
        payload["ttsProvider"] = ""
    return payload
