from pathlib import Path

import pytest
from plugin.plugins.from_the_heart.contracts import (
    ContractError,
    ContractRepository,
    validate_request,
)

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
        "interaction_id": "interaction-1",
        "base_asset_sha256": BASE_SHA,
        "player_text": "我想吃寿司",
        "safe_context": {},
    }
    value.update(overrides)
    return value


def test_current_contract_contains_speechless_and_cache_miss_fallback(contract):
    assert contract.version == "1.2.4"
    assert "speechless" in contract.allowed_reactions
    assert "restaurant_speechless" in contract.visual_variant_keys
    assert contract.raw["cache_miss_fallback"]["asset_id"] == "seafood_lunch_yui_speechless"


def test_request_contract_rejects_old_version(contract):
    with pytest.raises(ContractError, match="version mismatch"):
        validate_request(contract, request_args(node_contract_version="1.1.0"))


def test_request_contract_rejects_invalid_base_asset(contract):
    with pytest.raises(ContractError, match="base asset"):
        validate_request(contract, request_args(base_asset_sha256="0" * 64))


def test_request_contract_rejects_oversized_player_text(contract):
    with pytest.raises(ContractError, match="too long"):
        validate_request(contract, request_args(player_text="x" * 201))
