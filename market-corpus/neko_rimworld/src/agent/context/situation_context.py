"""Deterministic, bounded background from a retained native situation snapshot."""

from __future__ import annotations

import copy
import hashlib
import json

from ..model.tokenization import tokenizer

TOKEN_BUDGET = 1_000
SECTIONS = ("threats", "conditions", "alerts", "quests", "events")


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _count(text):
    encoding = tokenizer()
    if encoding is None:
        # UTF-8 bytes are a conservative upper bound for a byte-level tokenizer.
        return len(text.encode("utf-8")), "utf8_bytes_upper_bound"
    return len(encoding.encode(text, disallowed_special=())), encoding.name


def _items(data):
    """Group only direct native Quest identity; keep each distinct source fact."""
    sections = data.get("sections", {})
    rows = {name: [] for name in SECTIONS}
    quests = {}
    # The native producer orders alerts by native priority, quests by active
    # state, conditions by start tick and archive items newest first.
    for name in ("quests", "threats", "conditions", "alerts", "events"):
        for original in sections.get(name, {}).get("items", []):
            item = copy.deepcopy(original)
            quest_id = item.get("quest_id") if name != "quests" else item.get("id")
            if quest_id is not None:
                group = quests.setdefault(str(quest_id), {"quest_id": quest_id, "facts": []})
                fact = {"section": name, **item}
                if fact not in group["facts"]:
                    group["facts"].append(fact)
            else:
                rows[name].append(item)
    rows["quests"].extend(quests.values())
    return rows


def _fact(item):
    """Shorten presentation fields, retaining omission explicitly and raw in audit."""
    value = {}
    for key, child in item.items():
        if key in {"source", "runtime_type", "priority_value"}:
            continue
        limit = {"description": 280, "label": 120, "name": 120}.get(key)
        if limit is not None and isinstance(child, str) and len(child) > limit:
            value[key] = child[:limit]
            value[key + "_truncated"] = True
        elif isinstance(child, dict):
            value[key] = _fact(child)
        elif isinstance(child, list):
            limit = 4 if key in {"targets", "members", "facts"} else len(child)
            value[key] = [_fact(row) if isinstance(row, dict) else copy.deepcopy(row) for row in child[:limit]]
            if len(child) > limit:
                value[key + "_omitted"] = len(child) - limit
        else:
            value[key] = copy.deepcopy(child)
    return value


def _minimal_fact(item):
    """Retain identity and native decision cues before dropping a source entry."""
    keys = {
        "id", "quest_id", "section", "kind", "label", "label_truncated", "priority", "state", "quest_state",
        "scope", "map_id", "created_tick", "start_tick", "appearance_tick", "acceptance_expire_tick",
        "visible_hostile_count", "facts", "facts_omitted",
    }
    value = {key: copy.deepcopy(child) for key, child in item.items() if key in keys}
    if isinstance(value.get("label"), str) and len(value["label"]) > 64:
        value["label"] = value["label"][:64]
        value["label_truncated"] = True
    if "facts" in value:
        value["facts"] = [_minimal_fact(fact) for fact in value["facts"]]
    value["details_omitted"] = True
    return value


def compile_situation(snapshot):
    observation = snapshot.observation.to_dict()
    data = observation["response"]["payload"]["data"]
    raw = _json(observation)
    candidates = {name: [_fact(row) for row in rows] for name, rows in _items(data).items()}
    sections = data.get("sections", {})
    view = {
        "status": data.get("status", observation["response"]["payload"]["status"]),
        "situation_at_tick": data["sample"]["finished_tick"],
        "sample_started_tick": data["sample"].get("started_tick"),
        "atomic": data["sample"].get("atomic"),
        "map_at_sample": copy.deepcopy(data.get("map")),
        "clock": copy.deepcopy(data.get("clock")),
        "sections": {
            name: {"status": sections.get(name, {}).get("status", "unavailable"), "items": []}
            for name in SECTIONS
        },
    }
    for name in SECTIONS:
        section = sections.get(name, {})
        if section.get("scope"):
            view["sections"][name]["native_scope" if name == "quests" else "scope"] = section["scope"]
        if name == "quests":
            view["sections"][name]["group_scope"] = "Linked sources; event-only does not establish current/actionable quests."
        if section.get("reason"):
            view["sections"][name]["reason"] = section["reason"]
        if section.get("reasons"):
            view["sections"][name]["reasons"] = copy.deepcopy(section["reasons"])
        if section.get("error"):
            error = copy.deepcopy(section["error"])
            if isinstance(error.get("message"), str) and len(error["message"]) > 160:
                error["message"] = error["message"][:160]
                error["message_truncated"] = True
            view["sections"][name]["error"] = error
    # Native implementation names remain in the raw observation. These two
    # duplicate source labels pay for explicit grouped-Quest scope, not facts.
    for name in ("map_at_sample", "clock"):
        if isinstance(view[name], dict):
            view[name].pop("source", None)

    def render():
        for name in SECTIONS:
            omitted = len(candidates[name]) - len(view["sections"][name]["items"])
            if omitted:
                view["sections"][name]["omitted_items"] = omitted
            else:
                view["sections"][name].pop("omitted_items", None)
        return "Game situation snapshot (sampled background data):\n" + _json(view)

    # Keep every section visible and retain native order within each section.
    # A long high-priority/new entry gets a small traceable fact before omission;
    # if even that cannot fit, never replace it with a shorter later entry.
    exhausted = set()
    # Reserve the first native entry of each populated column before expanding
    # details, so one verbose earlier column cannot crowd all later columns out.
    for name in SECTIONS:
        if candidates[name]:
            view["sections"][name]["items"].append(_minimal_fact(candidates[name][0]))
            if _count(render())[0] > TOKEN_BUDGET:
                view["sections"][name]["items"].pop()
                exhausted.add(name)
    for name in SECTIONS:
        if view["sections"][name]["items"]:
            minimum = view["sections"][name]["items"][0]
            view["sections"][name]["items"][0] = candidates[name][0]
            if _count(render())[0] > TOKEN_BUDGET:
                view["sections"][name]["items"][0] = minimum
    for index in range(1, max((len(items) for items in candidates.values()), default=0)):
        for name in SECTIONS:
            if name in exhausted or index >= len(candidates[name]):
                continue
            view["sections"][name]["items"].append(candidates[name][index])
            if _count(render())[0] > TOKEN_BUDGET:
                view["sections"][name]["items"][-1] = _minimal_fact(candidates[name][index])
                if _count(render())[0] > TOKEN_BUDGET:
                    view["sections"][name]["items"].pop()
                    exhausted.add(name)
    content = render()
    if _count(content)[0] > TOKEN_BUDGET:
        # Oversized source metadata is still available in the original evidence.
        view.pop("clock", None)
        view["map_at_sample"] = data.get("sample", {}).get("map_id")
        for section in view["sections"].values():
            if "reason" in section:
                section["reason"] = "See original observation for source limitation."
            if "error" in section:
                section["error"] = {"code": section["error"].get("code"), "detail_omitted": True}
        content = render()
    tokens, method = _count(content)
    if tokens > TOKEN_BUDGET:
        raise ValueError("Situation metadata exceeds the background budget")
    return {
        "status": view["status"],
        "observation_id": observation["observation_id"],
        "raw_sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
        "projection_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        "sample": copy.deepcopy(data["sample"]),
        "cache_hit": snapshot.cache_hit,
        "checked_tick": snapshot.checked_tick,
        "content": content,
        "tokens": tokens,
        "tokenizer": method,
        "token_budget": TOKEN_BUDGET,
    }


def unavailable_situation(code, message, *, observation=None):
    """A disclosed source failure, never a fabricated empty or current snapshot."""
    failure = {"status": "unavailable", "error_code": code, "message": message}
    content = "Game situation snapshot (sampled background data):\n" + _json(failure)
    # Native errors are bounded on the wire, but disclose only their stable code
    # if an exceptional diagnostic cannot fit. Full error stays in the audit.
    if _count(content)[0] > TOKEN_BUDGET:
        failure.pop("message")
        content = "Game situation snapshot (sampled background data):\n" + _json(failure)
    tokens, method = _count(content)
    return {
        **failure,
        "observation_id": observation.get("observation_id") if observation else None,
        "raw_sha256": hashlib.sha256(_json(observation).encode()).hexdigest() if observation else None,
        "projection_sha256": hashlib.sha256(content.encode()).hexdigest(),
        "cache_hit": False, "sample": None, "checked_tick": None,
        "content": content, "tokens": tokens, "tokenizer": method, "token_budget": TOKEN_BUDGET,
    }
