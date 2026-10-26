from pathlib import Path

import pytest
from plugin.plugins.from_the_heart.contracts import (
    ContractError,
    ContractRepository,
    validate_request,
)
from plugin.plugins.from_the_heart.service import InteractionService

ROOT = Path(__file__).resolve().parents[1]
NODE_ID = "ch2.restaurant.favorite_food"
BASE_SHA = "01ce8fd74cd99fad908a8a3d7af9021fbc6735c22ebe3bd28d375e36d52d581d"


@pytest.fixture()
def contract():
    return ContractRepository(ROOT / "contracts").get(NODE_ID)


def request_args(**overrides):
    value = {
        "protocol_version": "1.0",
        "game_id": "from_the_heart",
        "game_version": "0.5.0-demo",
        "node_id": NODE_ID,
        "node_contract_version": "1.2.4",
        "interaction_id": "boundary-test",
        "base_asset_sha256": BASE_SHA,
        "player_text": "我想吃寿司",
        "safe_context": {},
    }
    value.update(overrides)
    return value


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("protocol_version", "2.0", "PROTOCOL_MISMATCH"),
        ("game_id", "other_game", "GAME_MISMATCH"),
        ("game_version", "9.9.9", "GAME_VERSION_MISMATCH"),
        ("node_contract_version", "1.1.0", "CONTRACT_VERSION_MISMATCH"),
        ("base_asset_sha256", "0" * 64, "BASE_ASSET_MISMATCH"),
    ],
)
def test_validate_request_rejects_protocol_identity_and_asset_drift(contract, field, value, code):
    with pytest.raises(ContractError) as error:
        validate_request(contract, request_args(**{field: value}))
    assert error.value.code == code


@pytest.mark.parametrize(
    "overrides",
    [
        {"interaction_id": ""},
        {"interaction_id": "x" * 129},
        {"player_text": ""},
        {"player_text": "x" * 201},
        {"safe_context": []},
    ],
)
def test_validate_request_rejects_invalid_input_boundary(contract, overrides):
    with pytest.raises(ContractError):
        validate_request(contract, request_args(**overrides))


def test_repository_rejects_unknown_node(contract):
    repository = ContractRepository(ROOT / "contracts")
    with pytest.raises(ContractError) as error:
        repository.get("ch2.restaurant.unknown")
    assert error.value.code == "UNKNOWN_NODE"


def test_contract_keeps_effects_and_visual_assets_within_allowlists(contract):
    forbidden = set(contract.raw["forbidden_effects"])
    assert {
        "change_story_node",
        "change_relationship",
        "set_game_variable",
        "change_ending",
    } <= forbidden
    assert contract.fallback_reaction in contract.allowed_reactions
    assert contract.fallback_asset_id in contract.raw["builtin_assets"]
    assert contract.raw["cache_miss_fallback"]["visual_variant_key"] in contract.visual_variant_keys


@pytest.mark.asyncio
async def test_service_model_failure_is_fail_closed_without_forbidden_effects(contract):
    class FailedDialogue:
        async def generate(self, contract, player_text):
            return None

    service = InteractionService(
        ContractRepository(ROOT / "contracts"),
        dialogue=FailedDialogue(),
    )
    result = await service.resolve(request_args(player_text="请随便聊聊"))

    assert result["accepted"] is False
    assert result["fallback_used"] is True
    assert result["generation"]["recommended"] is False
    assert not {
        "jump",
        "next_node",
        "ending",
        "relationship_delta",
        "set_variable",
        "unlock",
        "memory_write",
    } & result.keys()

