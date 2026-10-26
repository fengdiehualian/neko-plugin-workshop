"""Versioned node contracts for the From the Heart game bridge."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class ContractError(ValueError):
    """Raised when a node contract or request violates the stable protocol."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _required_string(data: Mapping[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ContractError("INVALID_CONTRACT", f"missing non-empty string: {key}")
    return value.strip()


def _required_mapping(data: Mapping[str, Any], key: str) -> dict[str, Any]:
    value = data.get(key)
    if not isinstance(value, Mapping):
        raise ContractError("INVALID_CONTRACT", f"missing object: {key}")
    return {str(item_key): item_value for item_key, item_value in value.items()}


def _string_list(data: Mapping[str, Any], key: str) -> tuple[str, ...]:
    value = data.get(key)
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise ContractError("INVALID_CONTRACT", f"invalid string list: {key}")
    return tuple(value)


@dataclass(frozen=True, slots=True)
class NodeContract:
    raw: dict[str, Any]
    node_id: str
    version: str
    supported_game_versions: tuple[str, ...]
    allowed_intents: tuple[str, ...]
    allowed_reactions: tuple[str, ...]
    max_input_chars: int
    max_reply_chars: int
    max_reply_sentences: int
    base_asset_id: str
    base_asset_sha256: str
    fallback_reply: str
    fallback_reaction: str
    fallback_asset_id: str
    visual_variant_keys: tuple[str, ...]

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "NodeContract":
        raw = {str(key): item for key, item in value.items()}
        input_policy = _required_mapping(raw, "input_policy")
        reply_policy = _required_mapping(raw, "reply_policy")
        base_asset = _required_mapping(raw, "base_asset")
        fallback = _required_mapping(raw, "fallback")
        builtin_assets = _required_mapping(raw, "builtin_assets")
        intent_policies = _required_mapping(raw, "intent_policies")
        visual_variants = _required_mapping(raw, "visual_variants")
        semantic_schema = _required_mapping(raw, "semantic_schema")

        base_sha = _required_string(base_asset, "sha256").lower()
        if not _SHA256_RE.fullmatch(base_sha):
            raise ContractError("INVALID_CONTRACT", "base asset sha256 is invalid")
        for asset_id, digest in builtin_assets.items():
            if not isinstance(digest, str) or not _SHA256_RE.fullmatch(digest.lower()):
                raise ContractError("INVALID_CONTRACT", f"invalid asset sha256: {asset_id}")

        allowed_intents = _string_list(raw, "allowed_intents")
        allowed_reactions = _string_list(raw, "allowed_reactions")
        if set(allowed_intents) != set(intent_policies):
            raise ContractError("INVALID_CONTRACT", "intent policies must exactly cover allowed intents")
        for key in ("requested_extra_dish", "lobster_stance", "social_key"):
            _string_list(semantic_schema, key)
        if not visual_variants:
            raise ContractError("INVALID_CONTRACT", "visual variants cannot be empty")

        max_input_chars = int(input_policy.get("max_chars", 0))
        max_reply_chars = int(reply_policy.get("max_chars", 0))
        max_reply_sentences = int(reply_policy.get("max_sentences", 0))
        if min(max_input_chars, max_reply_chars, max_reply_sentences) <= 0:
            raise ContractError("INVALID_CONTRACT", "input/reply limits must be positive")

        contract = cls(
            raw=raw,
            node_id=_required_string(raw, "node_id"),
            version=_required_string(raw, "node_contract_version"),
            supported_game_versions=_string_list(raw, "supported_game_versions"),
            allowed_intents=allowed_intents,
            allowed_reactions=allowed_reactions,
            max_input_chars=max_input_chars,
            max_reply_chars=max_reply_chars,
            max_reply_sentences=max_reply_sentences,
            base_asset_id=_required_string(base_asset, "asset_id"),
            base_asset_sha256=base_sha,
            fallback_reply=_required_string(fallback, "reply_text"),
            fallback_reaction=_required_string(fallback, "reaction_key"),
            fallback_asset_id=_required_string(fallback, "asset_id"),
            visual_variant_keys=tuple(sorted(visual_variants)),
        )
        for intent_key in contract.allowed_intents:
            contract.policy_for(intent_key)
        contract.asset_sha256(contract.fallback_asset_id)
        contract.generation_recipe(contract.policy_for("out_of_scope")["visual_variant_key"])
        return contract

    def policy_for(self, intent_key: str) -> dict[str, Any]:
        policies = _required_mapping(self.raw, "intent_policies")
        policy = policies.get(intent_key)
        if not isinstance(policy, Mapping):
            raise ContractError("INVALID_CONTRACT", f"missing intent policy: {intent_key}")
        normalized = {str(key): value for key, value in policy.items()}
        reaction = _required_string(normalized, "reaction_key")
        if reaction not in self.allowed_reactions:
            raise ContractError("INVALID_CONTRACT", f"reaction is not allowed: {reaction}")
        self.asset_sha256(_required_string(normalized, "asset_id"))
        variant_key = _required_string(normalized, "visual_variant_key")
        if variant_key not in self.visual_variant_keys:
            raise ContractError("INVALID_CONTRACT", f"visual variant is not allowed: {variant_key}")
        return normalized

    def semantic_values(self, key: str) -> tuple[str, ...]:
        schema = _required_mapping(self.raw, "semantic_schema")
        return _string_list(schema, key)

    def visual_variant(self, variant_key: str) -> dict[str, Any]:
        variants = _required_mapping(self.raw, "visual_variants")
        value = variants.get(variant_key)
        if not isinstance(value, Mapping):
            raise ContractError("INVALID_CONTRACT", f"unknown visual variant: {variant_key}")
        normalized = {str(key): item for key, item in value.items()}
        signature = _required_mapping(normalized, "visual_signature")
        if set(signature) != {"emotion", "gaze", "pose", "table_extra"}:
            raise ContractError("INVALID_CONTRACT", "visual signature is incomplete")
        if not all(isinstance(item, str) and item for item in signature.values()):
            raise ContractError("INVALID_CONTRACT", "visual signature values are invalid")
        protected = normalized.get("protected_objects")
        if not isinstance(protected, list) or not all(
            isinstance(item, str) and item for item in protected
        ):
            raise ContractError("INVALID_CONTRACT", "protected objects are invalid")
        return normalized

    def generation_recipe(self, variant_key: str) -> dict[str, Any]:
        cg_policy = _required_mapping(self.raw, "cg_policy")
        variant = self.visual_variant(variant_key)
        return {
            "recipe_schema_version": _required_string(cg_policy, "recipe_schema_version"),
            "game_id": "from_the_heart",
            "node_id": self.node_id,
            "node_contract_version": self.version,
            "base_asset_sha256": self.base_asset_sha256,
            "visual_variant_key": variant_key,
            "visual_signature": _required_mapping(variant, "visual_signature"),
            "protected_objects": list(variant["protected_objects"]),
            "workflow_version": _required_string(cg_policy, "workflow_version"),
            "generator_version": _required_string(cg_policy, "generator_version"),
            "output_profile": _required_string(cg_policy, "output_profile"),
        }

    def asset_sha256(self, asset_id: str) -> str:
        assets = _required_mapping(self.raw, "builtin_assets")
        digest = assets.get(asset_id)
        if not isinstance(digest, str) or not _SHA256_RE.fullmatch(digest.lower()):
            raise ContractError("INVALID_CONTRACT", f"unknown builtin asset: {asset_id}")
        return digest.lower()

    def exact_intent(self, player_text: str) -> str | None:
        exact = _required_mapping(self.raw, "exact_intents")
        value = exact.get(player_text.strip())
        return value if isinstance(value, str) and value in self.allowed_intents else None


class ContractRepository:
    """Eagerly loads small immutable contracts before request handling begins."""

    def __init__(self, contract_dir: Path):
        self._contracts: dict[str, NodeContract] = {}
        for path in sorted(contract_dir.glob("*.json")):
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, Mapping):
                raise ContractError("INVALID_CONTRACT", f"contract root must be an object: {path.name}")
            contract = NodeContract.from_mapping(payload)
            if contract.node_id in self._contracts:
                raise ContractError("INVALID_CONTRACT", f"duplicate node id: {contract.node_id}")
            self._contracts[contract.node_id] = contract
        if not self._contracts:
            raise ContractError("INVALID_CONTRACT", "no node contracts found")

    def get(self, node_id: str) -> NodeContract:
        contract = self._contracts.get(str(node_id))
        if contract is None:
            raise ContractError("UNKNOWN_NODE", "unknown node contract")
        return contract


def validate_request(contract: NodeContract, args: Mapping[str, Any]) -> str:
    if str(args.get("protocol_version") or "") != "1.0":
        raise ContractError("PROTOCOL_MISMATCH", "unsupported protocol version")
    if str(args.get("game_id") or "") != "from_the_heart":
        raise ContractError("GAME_MISMATCH", "unsupported game id")
    if str(args.get("game_version") or "") not in contract.supported_game_versions:
        raise ContractError("GAME_VERSION_MISMATCH", "unsupported game version")
    if str(args.get("node_contract_version") or "") != contract.version:
        raise ContractError("CONTRACT_VERSION_MISMATCH", "node contract version mismatch")
    if str(args.get("base_asset_sha256") or "").lower() != contract.base_asset_sha256:
        raise ContractError("BASE_ASSET_MISMATCH", "base asset sha256 mismatch")
    interaction_id = str(args.get("interaction_id") or "").strip()
    if not interaction_id or len(interaction_id) > 128:
        raise ContractError("INVALID_INTERACTION_ID", "interaction id is invalid")
    player_text = args.get("player_text")
    if not isinstance(player_text, str):
        raise ContractError("INVALID_INPUT", "player text must be a string")
    normalized = player_text.strip()
    if not normalized:
        raise ContractError("INVALID_INPUT", "player text cannot be empty")
    if len(normalized) > contract.max_input_chars:
        raise ContractError("INVALID_INPUT", "player text is too long")
    safe_context = args.get("safe_context", {})
    if not isinstance(safe_context, Mapping):
        raise ContractError("INVALID_INPUT", "safe_context must be an object")
    return normalized


__all__ = [
    "ContractError",
    "ContractRepository",
    "NodeContract",
    "validate_request",
]
