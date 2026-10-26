from pathlib import Path

import pytest
from plugin.plugins.from_the_heart import FromTheHeartPlugin
from plugin.plugins.from_the_heart.dialogue import DialogueCandidate
from plugin.plugins.from_the_heart.service import InteractionService

NODE_ID = "ch2.restaurant.favorite_food"
BASE_SHA = "01ce8fd74cd99fad908a8a3d7af9021fbc6735c22ebe3bd28d375e36d52d581d"


def request_args(player_text="清蒸螃蟹"):
    return {
        "protocol_version": "1.0",
        "game_id": "from_the_heart",
        "game_version": "0.5.0-demo",
        "node_id": NODE_ID,
        "node_contract_version": "1.2.4",
        "interaction_id": "interaction-1",
        "base_asset_sha256": BASE_SHA,
        "player_text": player_text,
        "safe_context": {},
    }


class FakeDialogue:
    async def generate(self, contract, player_text):
        return DialogueCandidate(
            intent_key="suggest_extra_dish",
            requested_extra_dish="crab",
            lobster_stance="accept",
            social_key="neutral",
            reply_text="可以加一份螃蟹，不过龙虾也要吃哦。",
        )


def test_entry_class_uses_public_sdk_and_manifest_identity():
    source = Path(__file__).resolve().parents[1]
    manifest = (source / "plugin.toml").read_text(encoding="utf-8")
    entry = (source / "__init__.py").read_text(encoding="utf-8")
    assert 'id = "from_the_heart"' in manifest
    assert "NekoPluginBase" in entry
    assert "@plugin_entry" in entry
    assert "resolve_interaction" in entry


@pytest.mark.asyncio
async def test_resolve_returns_game_compatible_payload():
    from plugin.plugins.from_the_heart.contracts import ContractRepository

    root = Path(__file__).resolve().parents[1]
    service = InteractionService(
        ContractRepository(root / "contracts"),
        dialogue=FakeDialogue(),
    )
    result = await service.resolve(request_args("再来一份螃蟹吧"))
    assert result["node_contract_version"] == "1.2.4"
    assert result["accepted"] is True
    assert result["generation"] == {
        "recommended": False,
        "generation_key": None,
        "reason": "disabled",
    }
    assert result["cache_miss_fallback"]["reaction_key"] == "speechless"
    assert result["cache_miss_fallback"]["asset"]["relative_url"] is None
    assert "ending" not in result
    assert "relationship_delta" not in result


@pytest.mark.asyncio
async def test_exact_answer_does_not_call_dialogue():
    from plugin.plugins.from_the_heart.contracts import ContractRepository

    class ExplodingDialogue:
        async def generate(self, contract, player_text):
            raise AssertionError("exact answer must bypass model")

    root = Path(__file__).resolve().parents[1]
    service = InteractionService(
        ContractRepository(root / "contracts"),
        dialogue=ExplodingDialogue(),
    )
    result = await service.resolve(request_args("蒜蓉煎龙虾"))
    assert result["accepted"] is True
    assert result["intent_key"] == "favorite_correct"


def test_plugin_does_not_expose_ensure_cg_entry():
    metadata = getattr(FromTheHeartPlugin.resolve_interaction, "__neko_event_meta__")
    assert metadata.id == "resolve_interaction"
    assert not hasattr(FromTheHeartPlugin, "ensure_cg")
