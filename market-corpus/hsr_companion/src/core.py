"""Pure domain logic for the Star Rail companion demo.

This module intentionally has no dependency on the N.E.K.O SDK so the knowledge,
profile, analysis, and event rules can be tested independently.
"""

from __future__ import annotations

import re
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Iterable

PROFILE_FIELDS = {
    "owned_characters",
    "favorite_characters",
    "training_targets",
    "common_teams",
    "current_goals",
    "preferences",
    "progression_note",
}

LIST_PROFILE_FIELDS = {
    "owned_characters",
    "favorite_characters",
    "training_targets",
    "common_teams",
    "current_goals",
}


def _normalize(value: Any) -> str:
    return "".join(str(value or "").strip().lower().split())


def _normalize_character_name(value: Any) -> str:
    """Normalize harmless typography without doing fuzzy name matching."""

    text = str(value or "").strip().lower()
    return re.sub(r"[\s·•・.&＆:：_—\-]+", "", text)


def _unique_strings(values: Iterable[Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        key = _normalize(text)
        if text and key not in seen:
            seen.add(key)
            result.append(text)
    return result


def merge_player_context(
    current: dict[str, Any] | None,
    changes: dict[str, Any] | None,
    *,
    mode: str = "merge",
    clear_fields: Iterable[str] = (),
) -> dict[str, Any]:
    """Merge an explicitly supplied player profile update.

    ``merge`` adds list values without duplicating them; ``replace`` replaces every
    supplied field. ``clear_fields`` supports correction and deletion without
    inventing a separate memory system.
    """

    profile = deepcopy(current or {})
    normalized_mode = str(mode or "merge").strip().lower()
    if normalized_mode not in {"merge", "replace"}:
        raise ValueError("mode must be 'merge' or 'replace'")

    for field in clear_fields:
        if field in PROFILE_FIELDS:
            profile.pop(field, None)

    for field, value in (changes or {}).items():
        if field not in PROFILE_FIELDS or value is None:
            continue
        if field in LIST_PROFILE_FIELDS:
            incoming = _unique_strings(value if isinstance(value, list) else [value])
            if normalized_mode == "merge":
                existing = profile.get(field, [])
                existing = existing if isinstance(existing, list) else [existing]
                profile[field] = _unique_strings([*existing, *incoming])
            else:
                profile[field] = incoming
        elif isinstance(value, dict):
            if normalized_mode == "merge" and isinstance(profile.get(field), dict):
                profile[field] = {**profile[field], **deepcopy(value)}
            else:
                profile[field] = deepcopy(value)
        else:
            profile[field] = str(value).strip()

    profile["updated_at"] = datetime.now(timezone.utc).isoformat()
    return profile


def build_profile_proposal(
    recognized_characters: Iterable[str],
    *,
    uncertain_characters: Iterable[str] = (),
    import_mode: str = "merge",
    source: str = "chat",
    confidence: str = "mixed",
    note: str = "",
    rejected_characters: Iterable[str] = (),
    registry_revision: str = "",
) -> dict[str, Any]:
    """Build a reviewable player-profile proposal from vision or chat input.

    Recognition is deliberately separated from the committed player profile.
    The caller must persist this as pending data and obtain confirmation before
    applying it.
    """

    mode = str(import_mode or "merge").strip().lower()
    if mode not in {"merge", "replace"}:
        raise ValueError("import_mode must be 'merge' or 'replace'")
    proposal_source = str(source or "chat").strip().lower()
    if proposal_source not in {"chat", "screenshot", "panel"}:
        proposal_source = "chat"
    confidence_key = str(confidence or "mixed").strip().lower()
    if confidence_key not in {"high", "mixed", "low"}:
        confidence_key = "mixed"

    recognized = _unique_strings(recognized_characters)
    recognized_keys = {_normalize(name) for name in recognized}
    uncertain = [name for name in _unique_strings(uncertain_characters) if _normalize(name) not in recognized_keys]
    rejected = _unique_strings(rejected_characters)
    if not recognized and not uncertain and not rejected:
        raise ValueError("proposal requires at least one character candidate")

    return {
        "recognized_characters": recognized,
        "uncertain_characters": uncertain,
        "rejected_characters": rejected,
        "import_mode": mode,
        "source": proposal_source,
        "confidence": confidence_key,
        "note": str(note or "").strip(),
        "confirmation_required": True,
        "can_confirm": bool(recognized),
        "validation_mode": "strict_registry_exact",
        "registry_revision": str(registry_revision or "").strip(),
    }


def apply_profile_proposal(
    current: dict[str, Any] | None,
    proposal: dict[str, Any],
) -> dict[str, Any]:
    """Apply a confirmed proposal while keeping dependent fields consistent."""

    recognized = _unique_strings(proposal.get("recognized_characters") or [])
    if not recognized:
        raise ValueError("cannot confirm a proposal without recognized characters")
    mode = str(proposal.get("import_mode") or "merge").strip().lower()
    if mode not in {"merge", "replace"}:
        raise ValueError("proposal has invalid import_mode")

    profile = merge_player_context(
        current,
        {"owned_characters": recognized},
        mode=mode,
    )
    if mode == "replace":
        owned_keys = {_normalize(name) for name in recognized}
        for field in ("favorite_characters", "training_targets"):
            existing = profile.get(field, [])
            if isinstance(existing, list):
                profile[field] = [name for name in existing if _normalize(name) in owned_keys]
    return profile


class KnowledgeBase:
    """Small searchable catalog with source provenance attached to every result."""

    def __init__(self, catalog: dict[str, Any], sources: dict[str, Any]):
        self.catalog_meta = dict(catalog.get("meta") or {})
        self.records = list(catalog.get("records") or [])
        self.sources = {
            item["source_id"]: item
            for item in sources.get("sources", [])
            if isinstance(item, dict) and item.get("source_id")
        }
        self._validate()

    def _validate(self) -> None:
        for record in self.records:
            if not record.get("id") or not record.get("name"):
                raise ValueError("knowledge records require id and name")
            refs = record.get("source_refs") or []
            if not refs:
                raise ValueError(f"knowledge record {record['id']} has no source_refs")
            missing = [source_id for source_id in refs if source_id not in self.sources]
            if missing:
                raise ValueError(f"knowledge record {record['id']} references unknown sources: {missing}")

    def _score(self, record: dict[str, Any], query: str) -> int:
        normalized_query = _normalize(query)
        if not normalized_query:
            return 0
        name = _normalize(record.get("name"))
        aliases = [_normalize(alias) for alias in record.get("aliases", [])]
        searchable = [
            name,
            *aliases,
            *[_normalize(tag) for tag in record.get("tags", [])],
            _normalize(record.get("category")),
            _normalize(record.get("summary")),
        ]
        if normalized_query == name:
            return 100
        if normalized_query in aliases:
            return 95
        if name.startswith(normalized_query):
            return 80
        if any(normalized_query in value for value in searchable):
            return 60
        return 0

    def _with_sources(self, record: dict[str, Any]) -> dict[str, Any]:
        item = deepcopy(record)
        item["sources"] = [deepcopy(self.sources[source_id]) for source_id in item.pop("source_refs", [])]
        item["catalog"] = deepcopy(self.catalog_meta)
        return item

    def lookup(
        self,
        query: str,
        *,
        category: str = "",
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        category_key = _normalize(category)
        scored: list[tuple[int, dict[str, Any]]] = []
        for record in self.records:
            if category_key and _normalize(record.get("category")) != category_key:
                continue
            score = self._score(record, query)
            if score:
                scored.append((score, record))
        scored.sort(key=lambda item: (-item[0], str(item[1].get("name"))))
        return [self._with_sources(record) for _, record in scored[: max(1, min(limit, 20))]]

    def find_character(self, name: str) -> dict[str, Any] | None:
        key = _normalize(name)
        for record in self.records:
            if record.get("category") != "character":
                continue
            names = [record.get("name"), *record.get("aliases", [])]
            if key in {_normalize(candidate) for candidate in names}:
                return self._with_sources(record)
        return None


class CharacterRegistry:
    """Strict canonical roster used as a hallucination firewall.

    Matching is exact after normalizing whitespace and common separator glyphs.
    It deliberately does not use edit distance or semantic similarity: a near
    miss is safer to reject than to turn into the wrong playable character.
    """

    def __init__(self, payload: dict[str, Any]):
        self.meta = dict(payload.get("meta") or {})
        self.records = [dict(item) for item in payload.get("records", []) if isinstance(item, dict)]
        self._by_key: dict[str, dict[str, Any]] = {}
        self._validate_and_index()

    def _validate_and_index(self) -> None:
        if not self.meta.get("upstream_revision"):
            raise ValueError("character registry requires an upstream revision")
        for record in self.records:
            name = str(record.get("name") or "").strip()
            source_ids = record.get("source_ids") or []
            if not name or not isinstance(source_ids, list) or not source_ids:
                raise ValueError("character registry records require name and source_ids")
            candidates = [
                name,
                record.get("tag"),
                *(record.get("aliases") or []),
                *(record.get("speech_aliases") or []),
            ]
            for candidate in candidates:
                key = _normalize_character_name(candidate)
                if not key:
                    continue
                existing = self._by_key.get(key)
                if existing and existing["name"] != name:
                    raise ValueError(
                        f"ambiguous character registry alias {candidate!r}: {existing['name']!r} vs {name!r}"
                    )
                self._by_key[key] = record

    @property
    def canonical_names(self) -> list[str]:
        return [str(item["name"]) for item in self.records]

    def resolve(self, candidate: str) -> dict[str, Any] | None:
        record = self._by_key.get(_normalize_character_name(candidate))
        return deepcopy(record) if record else None

    def validate_candidates(self, candidates: Iterable[str]) -> tuple[list[str], list[str]]:
        accepted: list[str] = []
        rejected: list[str] = []
        for candidate in _unique_strings(candidates):
            record = self.resolve(candidate)
            if record:
                accepted.append(str(record["name"]))
            else:
                rejected.append(candidate)
        return _unique_strings(accepted), _unique_strings(rejected)


def validate_character_candidates(
    *,
    registry: CharacterRegistry,
    recognized_characters: Iterable[str],
    uncertain_characters: Iterable[str] = (),
) -> dict[str, list[str]]:
    """Validate vision output against the pinned playable-character registry."""

    recognized, rejected_recognized = registry.validate_candidates(recognized_characters)
    uncertain, rejected_uncertain = registry.validate_candidates(uncertain_characters)
    recognized_keys = {_normalize_character_name(name) for name in recognized}
    uncertain = [name for name in uncertain if _normalize_character_name(name) not in recognized_keys]
    return {
        "recognized_characters": recognized,
        "uncertain_characters": uncertain,
        "rejected_characters": _unique_strings([*rejected_recognized, *rejected_uncertain]),
    }


def analyze_team(
    members: Iterable[str],
    *,
    knowledge: KnowledgeBase,
    player_context: dict[str, Any] | None = None,
    goal: str = "",
) -> dict[str, Any]:
    """Return transparent, deterministic Demo-level team observations."""

    member_names = _unique_strings(members)
    known: list[dict[str, Any]] = []
    unknown: list[str] = []
    for name in member_names:
        character = knowledge.find_character(name)
        if character:
            known.append(character)
        else:
            unknown.append(name)

    roles = {role for character in known for role in character.get("analysis_tags", {}).get("roles", [])}
    strengths: list[str] = []
    issues: list[str] = []
    next_steps: list[str] = []

    if len(member_names) > 4:
        issues.append("队伍超过四名角色；请确认输入是否混入候选角色。")
    if "damage" not in roles:
        issues.append("样本规则未识别到明确的主要输出角色。")
    else:
        strengths.append("队伍包含样本规则可识别的输出位。")
    if "sustain" not in roles:
        issues.append("样本规则未识别到生存位；长线战斗可能缺少治疗或保护。")
        next_steps.append("优先确认关卡是否需要加入治疗或保护角色。")
    else:
        strengths.append("队伍包含样本规则可识别的生存位。")
    if "support" in roles:
        strengths.append("队伍包含增益或减益辅助位。")

    context = player_context or {}
    owned = {_normalize(name) for name in context.get("owned_characters", [])}
    unavailable = [name for name in member_names if owned and _normalize(name) not in owned]
    if unavailable:
        issues.append("玩家档案中尚未记录这些角色：" + "、".join(unavailable))
        next_steps.append("如果已经拥有，请先更正玩家档案；否则把它们视为候选而非现成队伍。")

    if unknown:
        issues.append("Demo 小型知识库暂未收录：" + "、".join(unknown))
        next_steps.append("扩充并完成版本校验后，再对未收录角色给出结论。")

    if not next_steps:
        next_steps.append("结合目标关卡、敌人弱点和实战速度轴再做下一轮分析。")

    evidence = [
        {
            "record_id": character["id"],
            "name": character["name"],
            "source_ids": [source["source_id"] for source in character["sources"]],
        }
        for character in known
    ]
    return {
        "members": member_names,
        "goal": str(goal or "").strip(),
        "recognized_characters": [character["name"] for character in known],
        "unknown_characters": unknown,
        "strengths": strengths,
        "issues": issues,
        "recommended_next_steps": next_steps,
        "evidence": evidence,
        "analysis_scope": "demo_rule_based",
        "needs_live_version_verification": True,
        "disclaimer": "这是用于验证陪玩闭环的透明样本规则，不是完整配队结论。",
    }


def evaluate_event(
    *,
    event_id: str,
    occurred_at: str,
    expires_in_seconds: int,
    seen_event_ids: Iterable[str],
    now: datetime | None = None,
) -> dict[str, Any]:
    """Apply idempotency and expiry rules before a game event can reach N.E.K.O."""

    normalized_id = str(event_id or "").strip()
    if not normalized_id:
        return {"accepted": False, "reason": "missing_event_id"}
    if normalized_id in set(seen_event_ids):
        return {"accepted": False, "reason": "duplicate_event"}

    current = now or datetime.now(timezone.utc)
    try:
        occurred = datetime.fromisoformat(str(occurred_at).replace("Z", "+00:00"))
        if occurred.tzinfo is None:
            occurred = occurred.replace(tzinfo=timezone.utc)
        age = max(0.0, (current - occurred.astimezone(timezone.utc)).total_seconds())
    except (TypeError, ValueError):
        return {"accepted": False, "reason": "invalid_occurred_at"}

    ttl = max(1, min(int(expires_in_seconds or 300), 86400))
    if age > ttl:
        return {"accepted": False, "reason": "expired_event", "age_seconds": age}
    return {"accepted": True, "reason": "accepted", "age_seconds": age, "ttl": ttl}
