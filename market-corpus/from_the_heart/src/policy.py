"""Deterministic policy enforcement after optional model inference."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping

from .contracts import ContractError, NodeContract
from .dialogue import DialogueCandidate

_CONTROL_OR_MARKUP_RE = re.compile(r"[\[\]{}]|[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_SENTENCE_SPLIT_RE = re.compile(r"[。！？!?]+")


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    intent_key: str
    requested_extra_dish: str
    lobster_stance: str
    social_key: str
    reply_text: str
    reaction_key: str
    asset_id: str
    asset_sha256: str
    visual_variant_key: str
    visual_signature: dict[str, str]
    local_facts: list[dict[str, str]]
    dynamic_cg_candidate: bool
    used_fallback: bool


def _sentence_count(reply_text: str) -> int:
    return len([part for part in _SENTENCE_SPLIT_RE.split(reply_text) if part.strip()])


def _valid_reply(contract: NodeContract, policy: Mapping[str, Any], reply_text: str) -> bool:
    if not reply_text or len(reply_text) > contract.max_reply_chars:
        return False
    if "\n" in reply_text or "\r" in reply_text or _CONTROL_OR_MARKUP_RE.search(reply_text):
        return False
    if _sentence_count(reply_text) > contract.max_reply_sentences:
        return False
    reply_policy = contract.raw.get("reply_policy", {})
    forbidden = reply_policy.get("forbidden_fragments", []) if isinstance(reply_policy, Mapping) else []
    if any(isinstance(fragment, str) and fragment and fragment in reply_text for fragment in forbidden):
        return False
    required = policy.get("reply_must_include", [])
    if isinstance(required, list) and any(
        isinstance(fragment, str) and fragment not in reply_text for fragment in required
    ):
        return False
    return True


def _visual_signature(contract: NodeContract, variant_key: str) -> dict[str, str]:
    raw = contract.visual_variant(variant_key).get("visual_signature")
    if not isinstance(raw, Mapping):
        raise ContractError("INVALID_CONTRACT", "visual signature must be an object")
    allowed_keys = {"emotion", "gaze", "pose", "table_extra"}
    result = {
        str(key): str(value)
        for key, value in raw.items()
        if key in allowed_keys and isinstance(value, str) and value
    }
    if set(result) != allowed_keys:
        raise ContractError("INVALID_CONTRACT", "visual signature is incomplete")
    return result


def apply_policy(
    contract: NodeContract,
    *,
    player_text: str,
    candidate: DialogueCandidate | None,
) -> PolicyDecision:
    exact_intent = contract.exact_intent(player_text)
    candidate_intent = candidate.intent_key if candidate is not None else ""
    intent_key = exact_intent or (
        candidate_intent if candidate_intent in contract.allowed_intents else "out_of_scope"
    )
    requested_extra_dish = (
        candidate.requested_extra_dish
        if candidate is not None
        and candidate.requested_extra_dish in contract.semantic_values("requested_extra_dish")
        else "none"
    )
    lobster_stance = (
        candidate.lobster_stance
        if candidate is not None
        and candidate.lobster_stance in contract.semantic_values("lobster_stance")
        else "neutral"
    )
    social_key = (
        candidate.social_key
        if candidate is not None
        and candidate.social_key in contract.semantic_values("social_key")
        else "neutral"
    )
    if exact_intent is not None:
        requested_extra_dish = "none"
        lobster_stance = "neutral"
        social_key = "neutral"
    if lobster_stance == "refuse":
        intent_key = "refuse_lobster"
    if intent_key != "suggest_extra_dish":
        requested_extra_dish = "none"
    policy = contract.policy_for(intent_key)
    fallback_reply = str(policy.get("fallback_reply") or contract.fallback_reply).strip()

    allow_model_reply = (
        exact_intent is None
        and intent_key != "out_of_scope"
        and candidate_intent == intent_key
    )
    proposed_reply = candidate.reply_text.strip() if candidate is not None and allow_model_reply else ""
    reply_text = proposed_reply if _valid_reply(contract, policy, proposed_reply) else fallback_reply
    # Exact answers deliberately use the contract-owned deterministic line;
    # that is a successful resolution, not the client fallback path.
    used_fallback = exact_intent is None and reply_text != proposed_reply
    if not _valid_reply(contract, policy, reply_text):
        reply_text = contract.fallback_reply
        used_fallback = True

    reaction_key = str(policy["reaction_key"])
    asset_id = str(policy["asset_id"])
    visual_variant_key = str(policy["visual_variant_key"])
    if intent_key == "suggest_extra_dish" and requested_extra_dish == "crab":
        visual_variant_key = "restaurant_extra_crab_playful"
    local_facts: list[dict[str, str]] = []
    local_fact = policy.get("local_fact")
    local_fact_policy = contract.raw.get("local_fact_policy", {})
    allowed_local_keys = (
        local_fact_policy.get("allowed_keys", [])
        if isinstance(local_fact_policy, Mapping)
        else []
    )
    if isinstance(local_fact, Mapping):
        key = local_fact.get("key")
        value = requested_extra_dish if requested_extra_dish != "none" else local_fact.get("value")
        ttl = local_fact.get("ttl")
        if key in allowed_local_keys and isinstance(value, str) and ttl == "current_slot":
            local_facts.append({"key": str(key), "value": value, "ttl": "current_slot"})

    return PolicyDecision(
        intent_key=intent_key,
        requested_extra_dish=requested_extra_dish,
        lobster_stance=lobster_stance,
        social_key=social_key,
        reply_text=reply_text,
        reaction_key=reaction_key,
        asset_id=asset_id,
        asset_sha256=contract.asset_sha256(asset_id),
        visual_variant_key=visual_variant_key,
        visual_signature=_visual_signature(contract, visual_variant_key),
        local_facts=local_facts,
        dynamic_cg_candidate=bool(policy.get("dynamic_cg_candidate", False)),
        used_fallback=used_fallback,
    )


__all__ = ["PolicyDecision", "apply_policy"]
