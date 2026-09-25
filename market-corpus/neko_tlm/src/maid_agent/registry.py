"""Validation and normalization for public maid action arguments."""

import uuid
from copy import deepcopy
from typing import Any, Dict


class ActionValidationError(ValueError):
    pass


class ActionRegistry:
    SUPPORTED_KINDS = frozenset({
        "navigate",
        "harvest_blocks",
        "excavate_segment",
        "autonomous_mining",
        "return_to_position",
    })

    @staticmethod
    def is_ore_selector(selector: Any) -> bool:
        """判断规范化选择器是否指向矿石方块或标签。"""
        if not isinstance(selector, dict):
            return False
        selector_type = str(selector.get("type") or "").strip().lower()
        selector_id = str(selector.get("id") or "").strip().lower()
        selector_path = selector_id.split(":", 1)[-1]
        return (
            selector_type == "tag"
            and (
                selector_path.endswith("_ores")
                or selector_path == "ores"
                or selector_path.startswith("ores/")
            )
        ) or (
            selector_type == "block" and selector_path.endswith("_ore")
        )

    def normalize(self, kind: str, args: Dict[str, Any]) -> Dict[str, Any]:
        kind = str(kind or "").strip().lower()
        if kind not in self.SUPPORTED_KINDS:
            raise ActionValidationError(
                f"Unsupported maid action kind: {kind or '<empty>'}. "
                f"Supported kinds: {', '.join(sorted(self.SUPPORTED_KINDS))}"
            )
        if not isinstance(args, dict):
            raise ActionValidationError("args must be an object")
        if kind == "navigate":
            return self._navigate(args)
        if kind == "excavate_segment":
            return self._excavate_segment(args)
        if kind == "autonomous_mining":
            return self._autonomous_mining(args)
        if kind == "return_to_position":
            return self._return_to_position(args)
        return self._harvest(args)

    def _return_to_position(self, args: Dict[str, Any]) -> Dict[str, Any]:
        allowed = {
            "destination",
            "target",
            "speed",
            "stop_distance",
            "operation_id",
            "route_policy",
            "placement_policy",
            "max_placements",
            "handoff_to_follow",
        }
        unknown = sorted(set(args) - allowed)
        if unknown:
            raise ActionValidationError(
                "return_to_position has unsupported fields: "
                + ", ".join(unknown)
            )

        has_destination = args.get("destination") is not None
        has_target = args.get("target") is not None
        if has_destination == has_target:
            raise ActionValidationError(
                "return_to_position requires exactly one of destination or target"
            )
        destination = str(args.get("destination") or "").strip().lower()
        if has_destination and destination not in {
            "surface", "mine_entry", "player"
        }:
            raise ActionValidationError(
                "return_to_position.destination must be surface, "
                "mine_entry or player"
            )

        route_policy = str(
            args.get("route_policy", "recorded_tunnels_first") or ""
        ).strip().lower()
        if route_policy not in {"recorded_tunnels_first", "safe_shortest"}:
            raise ActionValidationError(
                "return_to_position.route_policy must be "
                "recorded_tunnels_first or safe_shortest"
            )
        placement_policy = str(
            args.get("placement_policy", "safe_support_and_water_seal") or ""
        ).strip().lower()
        if placement_policy not in {
            "disabled", "safe_support_and_water_seal"
        }:
            raise ActionValidationError(
                "return_to_position.placement_policy must be disabled or "
                "safe_support_and_water_seal"
            )

        normalized = {
            "speed": self._number(
                args.get("speed", 0.7),
                "return_to_position.speed", 0.4, 1.0,
            ),
            "stop_distance": self._number(
                args.get("stop_distance", 1.5),
                "return_to_position.stop_distance", 1.0, 4.0,
            ),
            "route_policy": route_policy,
            "placement_policy": placement_policy,
            "max_placements": self._integer(
                args.get("max_placements", 0),
                "return_to_position.max_placements", 0, 4096,
            ),
        }
        if has_destination:
            normalized["destination"] = destination
        else:
            normalized["target"] = self._return_position(
                args.get("target"), "return_to_position.target"
            )
        handoff_to_follow = args.get("handoff_to_follow", False)
        if not isinstance(handoff_to_follow, bool):
            raise ActionValidationError(
                "return_to_position.handoff_to_follow must be a boolean"
            )
        if handoff_to_follow:
            if destination != "player":
                raise ActionValidationError(
                    "return_to_position.handoff_to_follow requires destination=player"
                )
            normalized["handoff_to_follow"] = True
        operation_id = str(args.get("operation_id") or "").strip()
        if operation_id:
            try:
                normalized["operation_id"] = str(uuid.UUID(operation_id))
            except (ValueError, AttributeError, TypeError):
                raise ActionValidationError(
                    "return_to_position.operation_id must be a UUID"
                ) from None
        return normalized

    @staticmethod
    def _return_position(value: Any, name: str) -> Dict[str, int]:
        if not isinstance(value, dict):
            raise ActionValidationError(
                f"{name} must be an object with y and optional x/z"
            )
        if "y" not in value:
            raise ActionValidationError(f"{name} is missing y")
        has_x = "x" in value
        has_z = "z" in value
        if has_x != has_z:
            raise ActionValidationError(
                f"{name}.x and {name}.z must both be present or both be omitted"
            )
        axes = ("x", "y", "z") if has_x else ("y",)
        return {
            axis: ActionRegistry._integer(
                value.get(axis), f"{name}.{axis}", -30_000_000, 30_000_000
            )
            for axis in axes
        }

    def _autonomous_mining(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize the Java-owned mining goal and its bounded preferences.

        Python deliberately does not expose route steps, excavation budgets or
        a queue of primitive actions here.  Once accepted, Java owns sensing,
        replanning and execution until the target count is reached, the player
        cancels, or the server reports a decision-requiring BLOCKED terminal.
        """
        allowed = {
            "selector",
            "target_count",
            "direction",
            "shape",
            "segment_length",
            "speed",
            "discovery_mode",
            "placement_policy",
            "max_placements",
        }
        unknown = sorted(set(args) - allowed)
        if unknown:
            raise ActionValidationError(
                "autonomous_mining has unsupported fields: "
                + ", ".join(unknown)
            )

        selector = args.get("selector")
        if not isinstance(selector, dict):
            raise ActionValidationError("autonomous_mining.selector must be an object")
        selector_unknown = sorted(set(selector) - {"type", "id"})
        if selector_unknown:
            raise ActionValidationError(
                "autonomous_mining.selector has unsupported fields: "
                + ", ".join(selector_unknown)
            )
        selector_type = str(selector.get("type") or "").strip().lower()
        selector_id = str(selector.get("id") or "").strip().lower()
        if selector_type not in {"block", "tag"}:
            raise ActionValidationError(
                "autonomous_mining.selector.type must be block or tag"
            )
        if not selector_id or ":" not in selector_id:
            raise ActionValidationError(
                "autonomous_mining.selector.id must be a namespaced resource id"
            )

        direction = str(args.get("direction", "auto") or "").strip().lower()
        if direction not in {"auto", "north", "south", "east", "west"}:
            raise ActionValidationError(
                "autonomous_mining.direction must be auto, north, south, east or west"
            )
        shape = str(args.get("shape", "auto") or "").strip().lower()
        if shape not in {"auto", "level", "staircase_down"}:
            raise ActionValidationError(
                "autonomous_mining.shape must be auto, level or staircase_down"
            )
        discovery_mode = str(
            args.get("discovery_mode", "loaded_scan") or ""
        ).strip().lower()
        if discovery_mode not in {"loaded_scan", "exposed_only"}:
            raise ActionValidationError(
                "autonomous_mining.discovery_mode must be loaded_scan or exposed_only"
            )
        placement_policy = str(
            args.get("placement_policy", "disabled") or ""
        ).strip().lower()
        if placement_policy not in {"disabled", "safe_support_and_water_seal"}:
            raise ActionValidationError(
                "autonomous_mining.placement_policy must be disabled or "
                "safe_support_and_water_seal"
            )
        return {
            "selector": {"type": selector_type, "id": selector_id},
            "target_count": self._integer(
                args.get("target_count", 1),
                "autonomous_mining.target_count",
                1,
                4096,
            ),
            "direction": direction,
            "shape": shape,
            "segment_length": self._integer(
                args.get("segment_length", 8),
                "autonomous_mining.segment_length",
                1,
                8,
            ),
            "speed": self._number(
                args.get("speed", 0.7),
                "autonomous_mining.speed",
                0.4,
                1.0,
            ),
            "discovery_mode": discovery_mode,
            "placement_policy": placement_policy,
            "max_placements": self._integer(
                args.get("max_placements", 0),
                "autonomous_mining.max_placements",
                0,
                4096,
            ),
        }

    def _excavate_segment(self, args: Dict[str, Any]) -> Dict[str, Any]:
        allowed = {"direction", "shape", "length"}
        unknown = sorted(set(args) - allowed)
        if unknown:
            raise ActionValidationError(
                f"excavate_segment has unsupported fields: {', '.join(unknown)}"
            )
        direction = str(args.get("direction") or "").strip().lower()
        if direction not in {"north", "south", "east", "west"}:
            raise ActionValidationError(
                "excavate_segment.direction must be north, south, east or west"
            )
        shape = str(args.get("shape", "level") or "").strip().lower()
        if shape not in {"level", "staircase_down"}:
            raise ActionValidationError(
                "excavate_segment.shape must be level or staircase_down"
            )
        return {
            "direction": direction,
            "shape": shape,
            "length": self._integer(
                args.get("length", 1), "excavate_segment.length", 1, 8
            ),
        }

    def _navigate(self, args: Dict[str, Any]) -> Dict[str, Any]:
        target = self._position(args.get("target"), "target")
        return {
            "target": target,
            "speed": self._number(args.get("speed", 0.7), "speed", 0.4, 1.0),
            "stop_distance": self._number(
                args.get("stop_distance", 1.5), "stop_distance", 1.0, 4.0
            ),
        }

    def _harvest(self, args: Dict[str, Any]) -> Dict[str, Any]:
        has_target = args.get("target_pos") is not None
        has_selector = args.get("selector") is not None
        if has_target == has_selector:
            raise ActionValidationError(
                "harvest_blocks requires exactly one of target_pos or selector"
            )

        normalized_target = None
        normalized_selector = None
        ore_selector = False
        if has_target:
            normalized_target = self._position(args.get("target_pos"), "target_pos")
        else:
            selector = args.get("selector")
            if not isinstance(selector, dict):
                raise ActionValidationError("selector must be an object")
            selector_type = str(selector.get("type") or "").strip().lower()
            selector_id = str(selector.get("id") or "").strip().lower()
            if selector_type not in ("block", "tag"):
                raise ActionValidationError("selector.type must be block or tag")
            if not selector_id or ":" not in selector_id:
                raise ActionValidationError(
                    "selector.id must be a namespaced Minecraft resource id"
                )
            normalized_selector = {"type": selector_type, "id": selector_id}
            ore_selector = self.is_ore_selector(normalized_selector)

        if "vein_mining" in args:
            vein_mining = self._boolean(args.get("vein_mining"), "vein_mining")
        else:
            vein_mining = ore_selector
        if vein_mining and not has_selector:
            raise ActionValidationError("vein_mining=true requires selector targeting")

        max_blocks_limit = 64 if vein_mining else 8
        # In whole-vein mode this is a minimum target, never a cutoff.  One
        # therefore means "finish the first vein encountered".
        max_blocks_default = 1
        data = {
            "search_radius": self._integer(
                args.get("search_radius", 12), "search_radius", 1, 12
            ),
            "max_blocks": self._integer(
                args.get("max_blocks", max_blocks_default),
                "max_blocks", 1, max_blocks_limit,
            ),
            "vein_mining": vein_mining,
            "tool_policy": str(args.get("tool_policy", "require_correct") or "").lower(),
            "speed": self._number(args.get("speed", 0.7), "speed", 0.4, 1.0),
        }
        if data["tool_policy"] not in ("require_correct", "allow_wrong"):
            raise ActionValidationError(
                "tool_policy must be require_correct or allow_wrong"
            )
        if has_target:
            data["target_pos"] = normalized_target
        else:
            data["selector"] = normalized_selector
        if args.get("mining_plan") is not None:
            data["mining_plan"] = self._mining_plan(
                args.get("mining_plan"), has_selector=has_selector
            )
        return deepcopy(data)

    def _mining_plan(self, value: Any, *, has_selector: bool) -> Dict[str, Any]:
        if not isinstance(value, dict):
            raise ActionValidationError("mining_plan must be an object")
        allowed = {
            "mode", "direction", "max_distance", "max_depth",
            "max_segments", "excavation_budget",
        }
        unknown = sorted(set(value) - allowed)
        if unknown:
            raise ActionValidationError(
                f"mining_plan has unsupported fields: {', '.join(unknown)}"
            )

        mode = str(value.get("mode", "nearby") or "").strip().lower()
        if mode not in ("nearby", "forward_tunnel", "staircase_down", "auto"):
            raise ActionValidationError(
                "mining_plan.mode must be nearby, forward_tunnel, staircase_down or auto"
            )
        if mode != "nearby" and not has_selector:
            raise ActionValidationError(
                "non-nearby mining_plan modes require selector targeting"
            )

        direction = str(
            value.get("direction", "maid_facing") or ""
        ).strip().lower()
        if direction not in ("maid_facing", "north", "south", "east", "west"):
            raise ActionValidationError(
                "mining_plan.direction must be maid_facing, north, south, east or west"
            )

        default_depth = 4 if mode in ("staircase_down", "auto") else 0
        max_depth = self._integer(
            value.get("max_depth", default_depth), "mining_plan.max_depth", 0, 12
        )
        if mode == "forward_tunnel" and max_depth != 0:
            raise ActionValidationError(
                "forward_tunnel requires mining_plan.max_depth=0"
            )
        if mode == "staircase_down" and max_depth == 0:
            raise ActionValidationError(
                "staircase_down requires positive mining_plan.max_depth"
            )

        max_distance = self._integer(
            value.get("max_distance", 8), "mining_plan.max_distance", 1, 16
        )
        if mode == "staircase_down" and max_depth > max_distance:
            raise ActionValidationError(
                "staircase_down requires max_distance >= max_depth"
            )
        if mode == "auto" and max_depth >= max_distance:
            raise ActionValidationError(
                "auto requires max_distance > max_depth"
            )

        max_segments = self._integer(
            value.get("max_segments", 1), "mining_plan.max_segments", 1, 4
        )
        default_excavation_budget = 64 if max_segments > 1 else 24

        return {
            "mode": mode,
            "direction": direction,
            "max_distance": max_distance,
            "max_depth": max_depth,
            "max_segments": max_segments,
            "excavation_budget": self._integer(
                value.get("excavation_budget", default_excavation_budget),
                "mining_plan.excavation_budget", 0, 256,
            ),
        }

    @staticmethod
    def _position(value: Any, name: str) -> Dict[str, int]:
        if not isinstance(value, dict):
            raise ActionValidationError(f"{name} must be an object with x, y and z")
        missing = [axis for axis in ("x", "y", "z") if axis not in value]
        if missing:
            raise ActionValidationError(f"{name} is missing {', '.join(missing)}")
        return {
            axis: ActionRegistry._integer(value.get(axis), f"{name}.{axis}", -30_000_000, 30_000_000)
            for axis in ("x", "y", "z")
        }

    @staticmethod
    def _number(value: Any, name: str, minimum: float, maximum: float) -> float:
        if isinstance(value, bool):
            raise ActionValidationError(f"{name} must be a number")
        try:
            number = float(value)
        except (TypeError, ValueError):
            raise ActionValidationError(f"{name} must be a number") from None
        if number < minimum or number > maximum:
            raise ActionValidationError(f"{name} must be between {minimum} and {maximum}")
        return number

    @staticmethod
    def _boolean(value: Any, name: str) -> bool:
        if not isinstance(value, bool):
            raise ActionValidationError(f"{name} must be a boolean")
        return value

    @staticmethod
    def _integer(value: Any, name: str, minimum: int, maximum: int) -> int:
        if isinstance(value, bool):
            raise ActionValidationError(f"{name} must be an integer")
        try:
            number = int(value)
        except (TypeError, ValueError):
            raise ActionValidationError(f"{name} must be an integer") from None
        if str(value).strip() not in (str(number), f"{number}.0") and not isinstance(value, int):
            raise ActionValidationError(f"{name} must be an integer")
        if number < minimum or number > maximum:
            raise ActionValidationError(f"{name} must be between {minimum} and {maximum}")
        return number
