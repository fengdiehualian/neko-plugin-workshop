"""Application service for the bounded From the Heart interaction."""

from __future__ import annotations

from typing import Any, Mapping

from .contracts import ContractRepository, validate_request
from .dialogue import DialogueGenerator
from .policy import apply_policy


class InteractionService:
    """Resolve one game dialogue slot without owning game state."""

    def __init__(
        self,
        contracts: ContractRepository,
        *,
        dialogue: DialogueGenerator | None = None,
    ) -> None:
        self.contracts = contracts
        self.dialogue = dialogue or DialogueGenerator()

    async def resolve(self, args: Mapping[str, Any]) -> dict[str, Any]:
        node_id = str(args.get("node_id") or "")
        contract = self.contracts.get(node_id)
        player_text = validate_request(contract, args)

        exact_intent = contract.exact_intent(player_text)
        candidate = None
        if exact_intent is not None:
            from .dialogue import DialogueCandidate

            candidate = DialogueCandidate(
                intent_key=exact_intent,
                requested_extra_dish="none",
                lobster_stance="neutral",
                social_key="neutral",
                reply_text="",
            )
        else:
            try:
                candidate = await self.dialogue.generate(contract, player_text)
            except Exception:
                # Model failure is deliberately converted into the deterministic
                # policy path. Raw player text is never logged here.
                candidate = None

        decision = apply_policy(contract, player_text=player_text, candidate=candidate)
        asset_id = decision.asset_id
        cache_miss = contract.raw["cache_miss_fallback"]
        cache_miss_asset_id = str(cache_miss["asset_id"])

        return {
            "protocol_version": "1.0",
            "game_id": "from_the_heart",
            "game_version": str(args.get("game_version")),
            "node_id": contract.node_id,
            "node_contract_version": contract.version,
            "base_asset_sha256": contract.base_asset_sha256,
            "interaction_id": str(args.get("interaction_id")),
            "accepted": candidate is not None,
            "intent_key": decision.intent_key,
            "semantic": {
                "requested_extra_dish": decision.requested_extra_dish,
                "lobster_stance": decision.lobster_stance,
                "social_key": decision.social_key,
            },
            "reply_text": decision.reply_text,
            "reaction_key": decision.reaction_key,
            "local_facts": decision.local_facts,
            "visual_signature": decision.visual_signature,
            "visual_variant_key": decision.visual_variant_key,
            "asset": {
                "status": "builtin",
                "asset_id": asset_id,
                "sha256": contract.asset_sha256(asset_id),
                "relative_url": None,
            },
            "generation": {
                "recommended": False,
                "generation_key": None,
                "reason": "disabled",
            },
            "cache_miss_fallback": {
                "reply_text": str(cache_miss["reply_text"]),
                "reaction_key": str(cache_miss["reaction_key"]),
                "visual_variant_key": str(cache_miss["visual_variant_key"]),
                "visual_signature": dict(cache_miss["visual_signature"]),
                "asset": {
                    "status": "fallback",
                    "asset_id": cache_miss_asset_id,
                    "sha256": contract.asset_sha256(cache_miss_asset_id),
                    "relative_url": None,
                },
            },
            "fallback_used": bool(decision.used_fallback),
        }


__all__ = ["InteractionService"]
