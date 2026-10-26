from typing import Any

from plugin.sdk.plugin import Err, NekoPluginBase, Ok, neko_plugin, plugin_entry

from .contracts import ContractError, ContractRepository
from .service import InteractionService


@neko_plugin
class FromTheHeartPlugin(NekoPluginBase):
    def __init__(self, ctx: Any):
        super().__init__(ctx)
        self.contracts = ContractRepository(self.plugin_dir / "contracts")
        self.interactions = InteractionService(self.contracts)

    @plugin_entry(
        id="resolve_interaction",
        name="Resolve From the Heart interaction",
        description="Resolve one bounded game dialogue slot without changing story state.",
        input_schema={
            "type": "object",
            "required": [
                "protocol_version",
                "game_id",
                "game_version",
                "node_id",
                "node_contract_version",
                "interaction_id",
                "base_asset_sha256",
                "player_text",
            ],
            "properties": {
                "protocol_version": {"type": "string"},
                "game_id": {"type": "string"},
                "game_version": {"type": "string"},
                "node_id": {"type": "string"},
                "node_contract_version": {"type": "string"},
                "interaction_id": {"type": "string"},
                "base_asset_sha256": {"type": "string"},
                "player_text": {"type": "string", "maxLength": 200},
                "safe_context": {"type": "object"},
            },
            "additionalProperties": False,
        },
        timeout=4.0,
    )
    async def resolve_interaction(self, **kwargs: Any):
        try:
            return Ok(await self.interactions.resolve(kwargs))
        except ContractError as error:
            return Err({"code": error.code, "message": str(error)})
        except (OSError, RuntimeError, TypeError, ValueError) as error:
            return Err({"code": "INTERACTION_FAILED", "message": str(error)})
