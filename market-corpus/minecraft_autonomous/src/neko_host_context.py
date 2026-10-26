"""Explicit host profile read and main-brain context transport; no memory clone."""

import time
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx
from agent_mc_autonomy.configuration import _boolean, _text
from agent_mc_autonomy.host_context import HostContextPort, metadata


@dataclass(frozen=True)
class HostContextSettings:
    enabled: bool = True
    character_api_url: str = ""

    @classmethod
    def parse(cls, raw):
        section = raw.get("host_context", {})
        if not isinstance(section, dict):
            raise ValueError("host_context must be a table")
        url = _text(section, "character_api_url").rstrip("/")
        if url:
            parsed = urlparse(url)
            if (parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
                    or parsed.path != "/api/characters" or parsed.username or parsed.password or parsed.query or parsed.fragment):
                raise ValueError("host_context_character_api_must_be_explicit_local_public_endpoint")
        return cls(_boolean(section, "enabled", True), url)


async def character_profile(plugin, config, target, *, fresh=False, timeout=3, allow_current=False):
    if not config.character_api_url or not target and not allow_current:
        return {}
    key = (config.character_api_url, target)
    cache = getattr(plugin, "_host_profile_cache", None)
    if not fresh and cache and cache[0] == key and time.monotonic() - cache[1] < (60 if target else 2):
        return cache[2]
    try:
        async with httpx.AsyncClient(timeout=timeout, trust_env=False, follow_redirects=False) as client:
            response = await client.get(config.character_api_url, params={"language": "zh-CN"})
            response.raise_for_status()
            raw = response.json()
        actor = target or raw.get("当前猫娘")
        profile = raw.get("猫娘", {}).get(actor, {})
        persona = profile.get("system_prompt", "")
        if not isinstance(actor, str) or not actor or len(actor.encode("utf-8")) > 160 or not isinstance(persona, str):
            return {}
        if not persona.strip():
            # Default Neko characters use structured traits; system_prompt is
            # optional. Use only public persona fields, never model/voice data
            # or the entire _reserved object.
            traits = []
            for label in ("性格", "核心特质", "行为特点", "口癖", "自称", "昵称", "爱好", "雷点", "厌恶", "隐藏设定", "一句话台词", "种族", "年龄", "性别", "性格原型"):
                value = profile.get(label)
                if isinstance(value, list):
                    value = "；".join(v for v in value[:8] if isinstance(v, str))
                if isinstance(value, str) and value.strip():
                    traits.append(label + ":" + value.strip().encode("utf-8")[:600].decode("utf-8", errors="ignore"))
            persona = "\n".join(traits)
        if not persona.strip():
            return {}
        # A bounded profile excerpt, explicitly not the final host request or
        # runtime-reflected persona. Never forward the whole character object.
        persona = persona.encode("utf-8")[:2400].decode("utf-8", errors="ignore")
        result = {"agent_name": actor, "persona": persona, "persona_source": "character_profile"}
        if not fresh:
            plugin._host_profile_cache = (key, time.monotonic(), result)
        return result
    except (httpx.HTTPError, ValueError, TypeError, AttributeError):
        return {}


def context_request(port, now, *, enabled=True):
    if not enabled:
        return {"available": False, "needed": False, "reason": "disabled"}
    existing = HostContextPort(port.directory, now=now).read(port.mailbox.intent_revision())
    persona_needed = existing is None or not existing["stable"].get("persona")
    memory_needed = existing is None or not existing["dynamic"].get("memories_selected")
    return {**metadata(existing), "needed": persona_needed or memory_needed,
            "persona_needed": persona_needed, "memory_needed": memory_needed,
            "instructions": "先提交最终判断。context完全可选，needed仅表示可补充，非完成条件；只传你明确选择的当前关注、人格或记忆原id/source/text。公开角色资料不是记忆召回；[]仅表示明确选择为空。"}



async def shared_context(plugin, raw, target, supplied, review, *, existing=None):
    config = HostContextSettings.parse(raw)
    if not config.enabled:
        return None
    profile = await character_profile(plugin, config, target)
    if supplied is not None and not isinstance(supplied, dict):
        raise ValueError("host_context_fields_invalid")
    supplied = supplied or {}
    if set(supplied) - {"agent_name", "persona", "current_intent", "memories"}:
        raise ValueError("host_context_fields_invalid")
    actor = target or profile.get("agent_name") or supplied.get("agent_name")
    if not actor:
        return None
    if supplied.get("agent_name") and supplied["agent_name"] != actor:
        raise ValueError("host_context_character_mismatch")
    if not supplied and not profile:
        return None
    previous = existing if existing and existing["stable"]["agent_name"] == actor else None
    intent = supplied.get("current_intent", (previous or {}).get("dynamic", {}).get("current_intent", ""))
    # Missing fields preserve only fresh, session/intent-bound selected context;
    # an explicitly supplied [] clears memory. This is not a host memory clone.
    stable = previous["stable"] if previous else {}
    dynamic = previous["dynamic"] if previous else {}
    return {"agent_name": actor, "persona": supplied.get("persona", stable.get("persona") or profile.get("persona", "")),
            "persona_source": "host_projection" if "persona" in supplied else stable.get("persona_source", profile.get("persona_source", "host_projection")),
            "current_intent": intent, "memories": supplied.get("memories", dynamic.get("memories", [])),
            "memories_selected": "memories" in supplied or dynamic.get("memories_selected", False)}
