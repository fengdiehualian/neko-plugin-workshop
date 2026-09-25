"""Offline and explicit-online ship reference support for Neko WoWS."""

from .context import BattleShipContextManager, ContextObservation, ShipCatalogEvent
from .models import (
    CatalogMeta,
    CatalogShip,
    ShipCounts,
    ShipProfile,
    ShipResolution,
)
from .official_api import OfficialWowsApiClient
from .renderer import ShipReferenceRenderer
from .resolver import ShipResolver
from .store import NullCatalogSnapshot, ShipCatalogStore

__all__ = [
    "CatalogMeta",
    "CatalogShip",
    "BattleShipContextManager",
    "ContextObservation",
    "NullCatalogSnapshot",
    "OfficialWowsApiClient",
    "ShipCounts",
    "ShipCatalogStore",
    "ShipCatalogEvent",
    "ShipProfile",
    "ShipReferenceRenderer",
    "ShipResolution",
    "ShipResolver",
]
