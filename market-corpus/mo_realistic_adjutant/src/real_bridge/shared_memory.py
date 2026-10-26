from __future__ import annotations

import mmap
import struct
import threading
import time
from collections import Counter
from dataclasses import dataclass
from typing import Any


MAPPING_NAME = "Local\\NekoMOBridge-v10"
ECONOMY_MAPPING_NAME = "Local\\NekoMOBridge-Economy-v1"
PRODUCTION_MAPPING_NAME = "Local\\NekoMOBridge-Production-v3"
MARKER_MAPPING_NAME = "Local\\NekoMOBridge-Markers-v1"
LEGACY_MAPPING_NAMES = (
    (9, "Local\\NekoMOBridge-v9"),
    (8, "Local\\NekoMOBridge-v8"),
)
MAGIC = 0x4D4F4E4B
ECONOMY_MAGIC = 0x45434F4D
PRODUCTION_MAGIC = 0x50524F44
ECONOMY_PROTOCOL_VERSION = 1
PRODUCTION_PROTOCOL_VERSION = 3
MARKER_MAGIC = 0x4D41524B
MARKER_PROTOCOL_VERSION = 1
PROTOCOL_VERSION = 10
HARVESTER_CAPACITY = 64
ORE_CELL_CAPACITY = 128
LANDMARK_CAPACITY = 128
ALERT_CAPACITY = 64
ENTITY_CAPACITY = 32768
SELECTOR_TYPE_CAPACITY = 12

HEADER = struct.Struct("<IHHI8i128s")
SNAPSHOT = struct.Struct("<17i")
HARVESTER = struct.Struct("<I24siiiffiii")
ORE_CELL = struct.Struct("<iii")
LANDMARK = struct.Struct("<I24s48s8i")
ALERT = struct.Struct("<IiiI24s48s5i")
ENTITY = struct.Struct("<I24s48s6if7i")
COMMAND = struct.Struct("<40sIiiiiIii288siii")
ECONOMY_TELEMETRY = struct.Struct("<IHH8i")
PRODUCTION_HEADER = struct.Struct("<IHI7i")
PRODUCTION_QUEUE = struct.Struct("<24s4i")
PRODUCTION_QUEUE_CAPACITY = 64
PRODUCTION_SIDEBAR = struct.Struct("<24s4i")
PRODUCTION_SIDEBAR_CAPACITY = 300
PRODUCTION_OWNED_TYPE = struct.Struct("<24s2i")
PRODUCTION_OWNED_TYPE_CAPACITY = 4096
PRODUCTION_TELEMETRY_SIZE = (
    PRODUCTION_HEADER.size
    + PRODUCTION_QUEUE.size * PRODUCTION_QUEUE_CAPACITY
    + PRODUCTION_SIDEBAR.size * PRODUCTION_SIDEBAR_CAPACITY
    + PRODUCTION_OWNED_TYPE.size * PRODUCTION_OWNED_TYPE_CAPACITY
)
MARKER_HEADER = struct.Struct("<IHH5i")
MARKER_RECORD = struct.Struct("<Iiii96s")
MARKER_CAPACITY = 3
MARKER_TELEMETRY_SIZE = MARKER_HEADER.size + MARKER_RECORD.size * MARKER_CAPACITY

HEADER_OFFSET = 0
SNAPSHOT_OFFSET = HEADER.size
HARVESTERS_OFFSET = SNAPSHOT_OFFSET + SNAPSHOT.size
ORE_CELLS_OFFSET = HARVESTERS_OFFSET + HARVESTER.size * HARVESTER_CAPACITY
LANDMARKS_OFFSET = ORE_CELLS_OFFSET + ORE_CELL.size * ORE_CELL_CAPACITY
ALERTS_OFFSET = LANDMARKS_OFFSET + LANDMARK.size * LANDMARK_CAPACITY
ENTITIES_OFFSET = ALERTS_OFFSET + ALERT.size * ALERT_CAPACITY
COMMAND_OFFSET = ENTITIES_OFFSET + ENTITY.size * ENTITY_CAPACITY
SHARED_SIZE = COMMAND_OFFSET + COMMAND.size

(
    TELEMETRY_SEQ_OFFSET,
    COMMAND_SEQ_OFFSET,
    COMMAND_ACK_SEQ_OFFSET,
    CONTROL_SEQ_OFFSET,
    CONTROL_ACK_SEQ_OFFSET,
    CONTROL_DESIRED_OFFSET,
    CONTROL_ACTIVE_OFFSET,
    LAST_STATUS_OFFSET,
) = tuple(12 + index * 4 for index in range(8))
LAST_ERROR_OFFSET = 44

ACTION_CODES = {
    "mine": 1,
    "return": 2,
    "retreat": 3,
    "stop": 4,
    "move": 5,
    "attack": 6,
    "repair": 7,
    "capture": 8,
    "enter": 9,
    "repair_building": 10,
    "guard": 11,
    "unload": 12,
    "assign_group": 13,
    "assign_group_batch": 14,
    "deploy": 15,
    "explore": 16,
    "produce": 17,
    "auto_attack": 18,
    "hunt": 19,
    "place_building": 20,
    "support_power": 21,
}
SELECTOR_REGIONS = {"all": 0, "left": 1, "right": 2, "top": 3, "bottom": 4}
SELECTOR_KINDS = {"all": 0, "infantry": 1, "vehicle": 2, "aircraft": 3}
MODE_NAMES = {0: "campaign", 3: "lan", 4: "internet", 5: "skirmish"}
LANDMARK_KINDS = {1: "mobile_base", 2: "construction_yard", 3: "tech_building"}
FACTION_NAMES = {0: "unknown", 1: "allied", 2: "soviet", 3: "epsilon", 4: "foehn"}
ALERT_KINDS = {1: "under_attack", 2: "object_lost", 3: "home_ore_low", 4: "home_ore_depleted"}
OBJECT_KINDS = {1: "unit", 2: "building"}
ENTITY_KINDS = {1: "infantry", 2: "vehicle", 3: "aircraft", 4: "building"}
OWNER_RELATIONS = {1: "self", 2: "ally", 3: "enemy", 4: "neutral"}


class NativeBridgeError(RuntimeError):
    pass


@dataclass(frozen=True)
class NativeHealth:
    connected: bool
    control_active: bool
    last_status: int
    last_error: str
    native_protocol: int | None = None
    power_telemetry: bool = False
    production_telemetry: bool = False


class SharedMemoryBridge:
    def __init__(
        self,
        mapping_name: str = MAPPING_NAME,
        legacy_mapping_names: tuple[tuple[int, str], ...] | None = None,
        economy_mapping_name: str = ECONOMY_MAPPING_NAME,
        production_mapping_name: str = PRODUCTION_MAPPING_NAME,
        marker_mapping_name: str = MARKER_MAPPING_NAME,
    ) -> None:
        candidates = [(PROTOCOL_VERSION, mapping_name)]
        if legacy_mapping_names is None:
            legacy_mapping_names = (
                LEGACY_MAPPING_NAMES if mapping_name == MAPPING_NAME else ()
            )
        candidates.extend(legacy_mapping_names)
        self._memories = [
            (version, mmap.mmap(-1, SHARED_SIZE, tagname=name))
            for version, name in candidates
        ]
        self._native_protocol: int | None = None
        self._memory = self._memories[0][1]
        self._economy_memory = mmap.mmap(
            -1, ECONOMY_TELEMETRY.size, tagname=economy_mapping_name
        )
        self._production_memory = mmap.mmap(
            -1, PRODUCTION_TELEMETRY_SIZE, tagname=production_mapping_name
        )
        self._marker_memory = mmap.mmap(
            -1, MARKER_TELEMETRY_SIZE, tagname=marker_mapping_name
        )
        self._write_lock = threading.Lock()
        self._telemetry_lock = threading.Lock()
        self._last_telemetry_seq: int | None = None
        self._last_change_monotonic = time.monotonic()

    def close(self) -> None:
        for _version, memory in self._memories:
            memory.close()
        self._economy_memory.close()
        self._production_memory.close()
        self._marker_memory.close()

    def health(self) -> NativeHealth:
        selected = None
        header = None
        for version, memory in self._memories:
            candidate = HEADER.unpack(memory[: HEADER.size])
            if (
                candidate[0] == MAGIC
                and candidate[1] == version
                and candidate[2] == HEADER.size
                and candidate[3] == SHARED_SIZE
            ):
                selected = (version, memory)
                header = candidate
                break
        if selected is None or header is None:
            self._native_protocol = None
            header = self._read_header()
            error = header[12].split(b"\0", 1)[0].decode("utf-8", errors="replace")
            if not error:
                if header[0] == 0:
                    error = (
                        "native shared memory is uninitialized; restart Mental Omega "
                        "after installing matching bridge files"
                    )
                elif header[3] != SHARED_SIZE:
                    error = (
                        f"native shared memory size mismatch: "
                        f"native={header[3]} expected={SHARED_SIZE}"
                    )
                else:
                    error = "native shared memory header is incompatible or incomplete"
            return NativeHealth(False, False, header[11], error, None, False, False)
        self._native_protocol, self._memory = selected
        error = header[12].split(b"\0", 1)[0].decode("utf-8", errors="replace")
        return NativeHealth(
            True,
            header[10] == 1,
            header[11],
            error,
            self._native_protocol,
            self._economy_header_valid(),
            self._production_header_valid(),
        )

    def _economy_header_valid(self) -> bool:
        try:
            values = ECONOMY_TELEMETRY.unpack(
                self._economy_memory[: ECONOMY_TELEMETRY.size]
            )
        except (ValueError, struct.error):
            return False
        return (
            values[0] == ECONOMY_MAGIC
            and values[1] == ECONOMY_PROTOCOL_VERSION
            and values[2] == ECONOMY_TELEMETRY.size
        )

    def _production_header_valid(self) -> bool:
        try:
            values = PRODUCTION_HEADER.unpack(
                self._production_memory[: PRODUCTION_HEADER.size]
            )
        except (ValueError, struct.error):
            return False
        return (
            self._native_protocol == PROTOCOL_VERSION
            and values[0] == PRODUCTION_MAGIC
            and values[1] == PRODUCTION_PROTOCOL_VERSION
            and values[2] == PRODUCTION_TELEMETRY_SIZE
        )

    def state(self) -> dict[str, Any]:
        self._require_connected()
        for _ in range(20):
            seq_before = self._read_i32(TELEMETRY_SEQ_OFFSET)
            if seq_before & 1:
                time.sleep(0.002)
                continue
            raw = self._memory[:SHARED_SIZE]
            seq_after = self._read_i32(TELEMETRY_SEQ_OFFSET)
            if seq_before == seq_after and not (seq_after & 1):
                state = self._decode_state(raw)
                self._merge_economy_telemetry(state)
                self._merge_production_telemetry(state)
                self._merge_marker_telemetry(state)
                with self._telemetry_lock:
                    now = time.monotonic()
                    if self._last_telemetry_seq != seq_after:
                        self._last_telemetry_seq = seq_after
                        self._last_change_monotonic = now
                    state["snapshot_age_ms"] = int(
                        (now - self._last_change_monotonic) * 1000
                    )
                return state
        raise NativeBridgeError("native telemetry is busy")

    def _merge_economy_telemetry(self, state: dict[str, Any]) -> None:
        """Merge optional v1 economy data without affecting main protocol support."""
        economy = state.setdefault("economy", {})
        economy["power_available"] = False
        for _ in range(10):
            before = struct.unpack_from("<i", self._economy_memory, 8)[0]
            if before & 1:
                time.sleep(0.001)
                continue
            raw = self._economy_memory[: ECONOMY_TELEMETRY.size]
            after = struct.unpack_from("<i", self._economy_memory, 8)[0]
            if before != after or after & 1:
                continue
            values = ECONOMY_TELEMETRY.unpack(raw)
            if (
                values[0] != ECONOMY_MAGIC
                or values[1] != ECONOMY_PROTOCOL_VERSION
                or values[2] != ECONOMY_TELEMETRY.size
                or values[4] != 1
            ):
                return
            state_frame = state.get("game", {}).get("frame")
            if isinstance(state_frame, int) and abs(values[5] - state_frame) > 30:
                return
            state_player = state.get("player", {}).get("house_id")
            if isinstance(state_player, int) and values[6] != state_player:
                return
            player = state.setdefault("player", {})
            player["credits"] = values[7]
            economy.update(
                {
                    "credits": values[7],
                    "power_available": True,
                    "power_output": values[8],
                    "power_drain": values[9],
                    "power_surplus": values[10],
                    "low_power": values[10] < 0,
                    "telemetry_version": ECONOMY_PROTOCOL_VERSION,
                }
            )
            return

    def _merge_production_telemetry(self, state: dict[str, Any]) -> None:
        production = state.setdefault("production", {})
        production.update({
            "available": False,
            "native_connected": self._native_protocol == PROTOCOL_VERSION,
            "queue": [],
            "sidebar_available": [],
            "support_powers": [],
            "owned_counts": {},
            "reason": "production telemetry header is unavailable",
        })
        if not self._production_header_valid():
            return

        for _ in range(10):
            before = struct.unpack_from("<i", self._production_memory, 8)[0]
            if before & 1:
                time.sleep(0.001)
                continue
            raw = self._production_memory[:PRODUCTION_TELEMETRY_SIZE]
            after = struct.unpack_from("<i", self._production_memory, 8)[0]
            if before != after or after & 1:
                continue
            header = PRODUCTION_HEADER.unpack_from(raw)
            if (
                header[0] != PRODUCTION_MAGIC
                or header[1] != PRODUCTION_PROTOCOL_VERSION
                or header[2] != PRODUCTION_TELEMETRY_SIZE
                or header[4] != 1
            ):
                production["reason"] = "production telemetry is being updated"
                return
            state_frame = state.get("game", {}).get("frame")
            if isinstance(state_frame, int) and abs(header[5] - state_frame) > 30:
                production["reason"] = "production telemetry does not match the active player or frame"
                return
            state_player = state.get("player", {}).get("house_id")
            if isinstance(state_player, int) and header[6] != state_player:
                return
            kind_names = {
                1: "infantry",
                2: "vehicle",
                3: "aircraft",
                4: "building",
                5: "support_power",
            }
            queue = []
            count = max(0, min(header[7], PRODUCTION_QUEUE_CAPACITY))
            for index in range(count):
                record = PRODUCTION_QUEUE.unpack_from(
                    raw, PRODUCTION_HEADER.size + index * PRODUCTION_QUEUE.size
                )
                progress = max(0, min(int(record[3]), 54))
                queue.append(
                    {
                        "type_id": _cstring(record[0]),
                        "kind": kind_names.get(record[1], "unknown"),
                        "queued_count": max(0, int(record[2])),
                        "progress": progress,
                        "progress_ratio": round(progress / 54.0, 4),
                        "on_hold": record[4] == 1,
                    }
                )
            sidebar = []
            support_powers = []
            sidebar_count = max(0, min(header[8], PRODUCTION_SIDEBAR_CAPACITY))
            sidebar_offset = (
                PRODUCTION_HEADER.size
                + PRODUCTION_QUEUE.size * PRODUCTION_QUEUE_CAPACITY
            )
            for index in range(sidebar_count):
                record = PRODUCTION_SIDEBAR.unpack_from(
                    raw, sidebar_offset + index * PRODUCTION_SIDEBAR.size
                )
                item = {
                    "type_id": _cstring(record[0]),
                    "kind": kind_names.get(record[1], "unknown"),
                    "tab_index": int(record[2]),
                    "cameo_index": int(record[3]),
                    "has_factory": record[4] == 1,
                }
                if record[1] == 5:
                    support_powers.append({
                        "type_id": item["type_id"],
                        "kind": "support_power",
                        "super_index": item["tab_index"],
                        "ready": item["cameo_index"] == 100,
                        "available": item["has_factory"],
                        "charge_percent": item["cameo_index"],
                    })
                else:
                    sidebar.append(item)
            owned_counts = {}
            owned_count = max(0, min(header[9], PRODUCTION_OWNED_TYPE_CAPACITY))
            owned_offset = (
                sidebar_offset
                + PRODUCTION_SIDEBAR.size * PRODUCTION_SIDEBAR_CAPACITY
            )
            for index in range(owned_count):
                record = PRODUCTION_OWNED_TYPE.unpack_from(
                    raw, owned_offset + index * PRODUCTION_OWNED_TYPE.size
                )
                type_id = _cstring(record[0]).upper()
                if type_id and int(record[2]) > 0:
                    owned_counts[type_id] = int(record[2])
            production.update(
                {
                    "available": True,
                    "telemetry_version": PRODUCTION_PROTOCOL_VERSION,
                    "queue": queue,
                    "sidebar_available": sidebar,
                    "support_powers": support_powers,
                    "owned_counts": owned_counts,
                    "reason": "ok" if sidebar else "native build planner currently has no buildable entries",
                }
            )
            return

    def command_support_power(
        self, payload: dict[str, Any], timeout: float = 1.0
    ) -> dict[str, Any]:
        self._require_connected()
        if self._native_protocol != PROTOCOL_VERSION:
            raise NativeBridgeError("support powers require the current native bridge")
        type_id = payload.get("type_id")
        if not isinstance(type_id, str) or not type_id.strip():
            raise NativeBridgeError("type_id must be a non-empty string")
        try:
            encoded = type_id.strip().upper().encode("ascii")
        except UnicodeEncodeError as exc:
            raise NativeBridgeError("type_id must use ASCII") from exc
        if len(encoded) > 23:
            raise NativeBridgeError("type_id exceeds the native 23-byte limit")
        target = payload.get("target_cell")
        if not isinstance(target, dict):
            raise NativeBridgeError("target_cell must be an object")
        x, y = target.get("x"), target.get("y")
        if any(isinstance(value, bool) or not isinstance(value, int) for value in (x, y)):
            raise NativeBridgeError("target coordinates must be integers")
        if not all(-32768 <= value <= 32767 for value in (x, y)):
            raise NativeBridgeError("target coordinates are outside the native cell range")
        request_id = str(payload.get("request_id", ""))[:39].encode(
            "ascii", errors="ignore"
        )
        type_blob = encoded.ljust(24, b"\0").ljust(
            SELECTOR_TYPE_CAPACITY * 24, b"\0"
        )
        packed = COMMAND.pack(
            request_id, 0, ACTION_CODES["support_power"], 6,
            x, y, 0, 0, 1, type_blob, 0, 0, 0,
        )
        with self._write_lock:
            sequence = self._read_i32(COMMAND_SEQ_OFFSET) + 1
            self._memory[COMMAND_OFFSET : COMMAND_OFFSET + COMMAND.size] = packed
            self._write_i32(COMMAND_SEQ_OFFSET, sequence)
            self._wait_for(COMMAND_ACK_SEQ_OFFSET, sequence, timeout)
            result = COMMAND.unpack_from(self._memory, COMMAND_OFFSET)
            return {
                "status": self._read_i32(LAST_STATUS_OFFSET),
                "matched_count": result[10],
                "accepted_count": result[11],
                "failed_count": result[12],
                "last_error": self._read_last_error(),
            }

    def _merge_marker_telemetry(self, state: dict[str, Any]) -> None:
        state["markers"] = []
        state["marker_telemetry_available"] = False
        for _ in range(10):
            before = struct.unpack_from("<i", self._marker_memory, 8)[0]
            if before & 1:
                time.sleep(0.001)
                continue
            raw = self._marker_memory[:MARKER_TELEMETRY_SIZE]
            after = struct.unpack_from("<i", self._marker_memory, 8)[0]
            if before != after or after & 1:
                continue
            header = MARKER_HEADER.unpack_from(raw)
            if (
                header[0] != MARKER_MAGIC
                or header[1] != MARKER_PROTOCOL_VERSION
                or header[2] != MARKER_TELEMETRY_SIZE
                or header[4] != 1
            ):
                return
            state_frame = state.get("game", {}).get("frame")
            if isinstance(state_frame, int) and abs(header[5] - state_frame) > 30:
                return
            state_player = state.get("player", {}).get("house_id")
            if isinstance(state_player, int) and header[6] != state_player:
                return
            count = max(0, min(header[7], MARKER_CAPACITY))
            markers = []
            for index in range(count):
                record = MARKER_RECORD.unpack_from(
                    raw, MARKER_HEADER.size + index * MARKER_RECORD.size
                )
                markers.append({
                    "marker_id": int(record[0]),
                    "slot": int(record[1]),
                    "cell": {"x": int(record[2]), "y": int(record[3])},
                    "text": _cstring(record[4]),
                })
            state["markers"] = markers
            state["marker_telemetry_available"] = True
            return

    def set_control(
        self, enabled: bool, timeout: float = 1.0, *, network_opt_in: bool = False
    ) -> bool:
        self._require_connected()
        with self._write_lock:
            sequence = self._read_i32(CONTROL_SEQ_OFFSET) + 1
            desired = 2 if enabled and network_opt_in else (1 if enabled else 0)
            self._write_i32(CONTROL_DESIRED_OFFSET, desired)
            self._write_i32(CONTROL_SEQ_OFFSET, sequence)
            self._wait_for(CONTROL_ACK_SEQ_OFFSET, sequence, timeout)
            return self._read_i32(CONTROL_ACTIVE_OFFSET) == 1

    def command(self, payload: dict[str, Any], timeout: float = 1.0) -> int:
        self._require_connected()
        action = ACTION_CODES.get(str(payload.get("action", "")))
        if action is None:
            raise NativeBridgeError("unknown unit action")
        if self._native_protocol == 8 and action in {
            ACTION_CODES["deploy"], ACTION_CODES["explore"],
            ACTION_CODES["auto_attack"], ACTION_CODES["hunt"],
        }:
            raise NativeBridgeError(
                f"{payload.get('action')} requires native bridge protocol v9 or newer"
            )
        unit_id = payload.get("unit_id")
        if isinstance(unit_id, bool) or not isinstance(unit_id, int):
            raise NativeBridgeError("unit_id must be an integer")
        if not 0 <= unit_id <= 0xFFFFFFFF:
            raise NativeBridgeError("unit_id is outside the native uint32 range")
        target = payload.get("target_cell")
        has_target = isinstance(target, dict)
        x = target.get("x", 0) if has_target else 0
        y = target.get("y", 0) if has_target else 0
        if any(isinstance(value, bool) or not isinstance(value, int) for value in (x, y)):
            raise NativeBridgeError("target coordinates must be integers")
        if has_target and not all(-32768 <= value <= 32767 for value in (x, y)):
            raise NativeBridgeError("target coordinates are outside the native cell range")
        request_id = str(payload.get("request_id", ""))[:39].encode("ascii", errors="ignore")
        target_object_id = payload.get("target_object_id", 0)
        if isinstance(target_object_id, bool) or not isinstance(target_object_id, int):
            raise NativeBridgeError("target_object_id must be an integer")
        if not 0 <= target_object_id <= 0xFFFFFFFF:
            raise NativeBridgeError("target_object_id is outside the native uint32 range")
        if has_target and target_object_id:
            raise NativeBridgeError("a command cannot target both a cell and an object")
        override_team = payload.get("override_team", False)
        if not isinstance(override_team, bool):
            raise NativeBridgeError("override_team must be a boolean")
        if override_team and action not in {
            ACTION_CODES["move"], ACTION_CODES["retreat"], ACTION_CODES["explore"]
        }:
            raise NativeBridgeError("team override is only available for movement orders")
        append_waypoint = payload.get("append_waypoint", False)
        if not isinstance(append_waypoint, bool):
            raise NativeBridgeError("append_waypoint must be a boolean")
        if append_waypoint and action not in {ACTION_CODES["move"], ACTION_CODES["explore"]}:
            raise NativeBridgeError("waypoints are only available for move and explore")
        if append_waypoint and override_team:
            raise NativeBridgeError("append_waypoint and override_team cannot be combined")
        group_number = payload.get("group_number")
        replace_group = payload.get("replace_group", False)
        if action == ACTION_CODES["assign_group"]:
            if isinstance(group_number, bool) or not isinstance(group_number, int):
                raise NativeBridgeError("group_number must be an integer")
            if not 0 <= group_number <= 9:
                raise NativeBridgeError("group_number must be between 0 and 9")
            if not isinstance(replace_group, bool):
                raise NativeBridgeError("replace_group must be a boolean")
            if has_target or target_object_id:
                raise NativeBridgeError("assign_group does not accept another target")
            target_mode = 3
            x = group_number
            y = 1 if replace_group else 0
        else:
            if group_number is not None or replace_group is not False:
                raise NativeBridgeError("group options require assign_group")
            target_mode = 1 if has_target else 2 if target_object_id else 0
        packed = COMMAND.pack(
            request_id,
            unit_id,
            action,
            target_mode,
            x,
            y,
            target_object_id,
            2 if append_waypoint else 1 if override_team else 0,
            0,
            bytes(SELECTOR_TYPE_CAPACITY * 24),
            0,
            0,
            0,
        )
        with self._write_lock:
            sequence = self._read_i32(COMMAND_SEQ_OFFSET) + 1
            self._memory[COMMAND_OFFSET : COMMAND_OFFSET + COMMAND.size] = packed
            self._write_i32(COMMAND_SEQ_OFFSET, sequence)
            self._wait_for(COMMAND_ACK_SEQ_OFFSET, sequence, timeout)
            return self._read_i32(LAST_STATUS_OFFSET)

    def command_group(self, payload: dict[str, Any], timeout: float = 1.0) -> dict[str, int]:
        self._require_connected()
        group_number = payload.get("group_number")
        if isinstance(group_number, bool) or not isinstance(group_number, int):
            raise NativeBridgeError("group_number must be an integer")
        if not 0 <= group_number <= 9:
            raise NativeBridgeError("group_number must be between 0 and 9")
        replace_group = payload.get("replace_group", True)
        if not isinstance(replace_group, bool):
            raise NativeBridgeError("replace_group must be a boolean")
        region = str(payload.get("region", "all")).casefold()
        if region not in SELECTOR_REGIONS:
            raise NativeBridgeError("region must be all, left, right, top, or bottom")
        kind = str(payload.get("kind", "all")).casefold()
        if kind not in SELECTOR_KINDS:
            raise NativeBridgeError("kind must be all, infantry, vehicle, or aircraft")
        raw_type_ids = payload.get("type_ids", [])
        if not isinstance(raw_type_ids, list):
            raise NativeBridgeError("type_ids must be an array")
        if len(raw_type_ids) > SELECTOR_TYPE_CAPACITY:
            raise NativeBridgeError(
                f"type_ids may contain at most {SELECTOR_TYPE_CAPACITY} values"
            )
        encoded_type_ids: list[bytes] = []
        for value in raw_type_ids:
            if not isinstance(value, str) or not value.strip():
                raise NativeBridgeError("each type_id must be a non-empty string")
            try:
                encoded = value.strip().upper().encode("ascii")
            except UnicodeEncodeError as exc:
                raise NativeBridgeError("type_ids must use ASCII") from exc
            if len(encoded) > 23:
                raise NativeBridgeError("type_id exceeds the native 23-byte limit")
            encoded_type_ids.append(encoded)
        type_blob = b"".join(value.ljust(24, b"\0") for value in encoded_type_ids)
        type_blob = type_blob.ljust(SELECTOR_TYPE_CAPACITY * 24, b"\0")
        request_id = str(payload.get("request_id", ""))[:39].encode(
            "ascii", errors="ignore"
        )
        packed = COMMAND.pack(
            request_id,
            0,
            ACTION_CODES["assign_group_batch"],
            3,
            group_number,
            1 if replace_group else 0,
            SELECTOR_KINDS[kind],
            SELECTOR_REGIONS[region],
            len(encoded_type_ids),
            type_blob,
            0,
            0,
            0,
        )
        with self._write_lock:
            sequence = self._read_i32(COMMAND_SEQ_OFFSET) + 1
            self._memory[COMMAND_OFFSET : COMMAND_OFFSET + COMMAND.size] = packed
            self._write_i32(COMMAND_SEQ_OFFSET, sequence)
            self._wait_for(COMMAND_ACK_SEQ_OFFSET, sequence, timeout)
            result = COMMAND.unpack_from(self._memory, COMMAND_OFFSET)
            return {
                "status": self._read_i32(LAST_STATUS_OFFSET),
                "matched_count": result[10],
                "accepted_count": result[11],
                "failed_count": result[12],
            }

    def command_production(
        self, payload: dict[str, Any], timeout: float = 1.0
    ) -> dict[str, Any]:
        self._require_connected()
        if not self._production_header_valid():
            raise NativeBridgeError(
                "unit production requires the current native bridge"
            )
        type_id = payload.get("type_id")
        if not isinstance(type_id, str) or not type_id.strip():
            raise NativeBridgeError("type_id must be a non-empty string")
        try:
            encoded = type_id.strip().upper().encode("ascii")
        except UnicodeEncodeError as exc:
            raise NativeBridgeError("type_id must use ASCII") from exc
        if len(encoded) > 23:
            raise NativeBridgeError("type_id exceeds the native 23-byte limit")
        quantity = payload.get("quantity", 1)
        if isinstance(quantity, bool) or not isinstance(quantity, int):
            raise NativeBridgeError("quantity must be an integer")
        if not 1 <= quantity <= 50:
            raise NativeBridgeError("quantity must be between 1 and 50")
        request_id = str(payload.get("request_id", ""))[:39].encode(
            "ascii", errors="ignore"
        )
        type_blob = encoded.ljust(24, b"\0").ljust(
            SELECTOR_TYPE_CAPACITY * 24, b"\0"
        )
        packed = COMMAND.pack(
            request_id,
            0,
            ACTION_CODES["produce"],
            4,
            quantity,
            0,
            0,
            0,
            1,
            type_blob,
            0,
            0,
            0,
        )
        with self._write_lock:
            sequence = self._read_i32(COMMAND_SEQ_OFFSET) + 1
            self._memory[COMMAND_OFFSET : COMMAND_OFFSET + COMMAND.size] = packed
            self._write_i32(COMMAND_SEQ_OFFSET, sequence)
            self._wait_for(COMMAND_ACK_SEQ_OFFSET, sequence, timeout)
            result = COMMAND.unpack_from(self._memory, COMMAND_OFFSET)
            return {
                "status": self._read_i32(LAST_STATUS_OFFSET),
                "matched_count": result[10],
                "accepted_count": result[11],
                "failed_count": result[12],
                "last_error": self._read_last_error(),
            }

    def command_building_placement(
        self, payload: dict[str, Any], timeout: float = 1.0
    ) -> dict[str, Any]:
        self._require_connected()
        if self._native_protocol != PROTOCOL_VERSION:
            raise NativeBridgeError(
                "building placement requires native bridge protocol v10"
            )
        type_id = payload.get("type_id")
        if not isinstance(type_id, str) or not type_id.strip():
            raise NativeBridgeError("type_id must be a non-empty string")
        try:
            encoded = type_id.strip().upper().encode("ascii")
        except UnicodeEncodeError as exc:
            raise NativeBridgeError("type_id must use ASCII") from exc
        if len(encoded) > 23:
            raise NativeBridgeError("type_id exceeds the native 23-byte limit")
        target = payload.get("target_cell")
        if not isinstance(target, dict):
            raise NativeBridgeError("target_cell must be an object")
        x, y = target.get("x"), target.get("y")
        if any(isinstance(value, bool) or not isinstance(value, int) for value in (x, y)):
            raise NativeBridgeError("target coordinates must be integers")
        if not all(-32768 <= value <= 32767 for value in (x, y)):
            raise NativeBridgeError("target coordinates are outside the native cell range")
        target_object_id = payload.get("target_object_id", 0)
        if (
            isinstance(target_object_id, bool)
            or not isinstance(target_object_id, int)
            or not 0 <= target_object_id <= 0xFFFFFFFF
        ):
            raise NativeBridgeError("target_object_id must be an unsigned integer")
        request_id = str(payload.get("request_id", ""))[:39].encode(
            "ascii", errors="ignore"
        )
        type_blob = encoded.ljust(24, b"\0").ljust(
            SELECTOR_TYPE_CAPACITY * 24, b"\0"
        )
        packed = COMMAND.pack(
            request_id, 0, ACTION_CODES["place_building"], 5,
            x, y, target_object_id, 0, 1, type_blob, 0, 0, 0,
        )
        with self._write_lock:
            sequence = self._read_i32(COMMAND_SEQ_OFFSET) + 1
            self._memory[COMMAND_OFFSET : COMMAND_OFFSET + COMMAND.size] = packed
            self._write_i32(COMMAND_SEQ_OFFSET, sequence)
            self._wait_for(COMMAND_ACK_SEQ_OFFSET, sequence, timeout)
            result = COMMAND.unpack_from(self._memory, COMMAND_OFFSET)
            return {
                "status": self._read_i32(LAST_STATUS_OFFSET),
                "matched_count": result[10],
                "accepted_count": result[11],
                "failed_count": result[12],
                "last_error": self._read_last_error(),
            }

    def _read_last_error(self) -> str:
        return bytes(self._memory[LAST_ERROR_OFFSET : LAST_ERROR_OFFSET + 128]).split(
            b"\0", 1
        )[0].decode("utf-8", errors="replace")

    def query_entities(self, payload: dict[str, Any]) -> dict[str, Any]:
        state = self.state()
        entities = state.get("visible_entities", [])
        relation = payload.get("relation")
        if relation is not None:
            if isinstance(relation, str):
                relations = {relation}
            elif isinstance(relation, list) and all(
                isinstance(value, str) for value in relation
            ):
                relations = set(relation)
            else:
                raise NativeBridgeError("relation must be a string or array of strings")
            entities = [item for item in entities if item.get("relation") in relations]
        type_ids = payload.get("type_ids")
        if type_ids is not None:
            if not isinstance(type_ids, list) or not all(
                isinstance(value, str) for value in type_ids
            ):
                raise NativeBridgeError("type_ids must be an array of strings")
            wanted_types = {value.upper() for value in type_ids}
            entities = [
                item for item in entities
                if str(item.get("type_id", "")).upper() in wanted_types
            ]
        for key in (
            "kind", "controllable", "in_tactical_team", "group_number", "alive", "visible"
        ):
            if key in payload and payload[key] is not None:
                entities = [item for item in entities if item.get(key) == payload[key]]
        exclude_kind = payload.get("exclude_kind")
        if exclude_kind is not None:
            if not isinstance(exclude_kind, str):
                raise NativeBridgeError("exclude_kind must be a string")
            entities = [item for item in entities if item.get("kind") != exclude_kind]
        health_below = payload.get("health_below")
        if health_below is not None:
            if isinstance(health_below, bool) or not isinstance(health_below, (int, float)):
                raise NativeBridgeError("health_below must be a number")
            threshold = max(0.01, min(float(health_below), 1.0))
            entities = [
                item for item in entities
                if isinstance(item.get("health_ratio"), (int, float))
                and not isinstance(item.get("health_ratio"), bool)
                and float(item["health_ratio"]) < threshold
            ]
        region = str(payload.get("region", "all")).casefold()
        if region not in SELECTOR_REGIONS:
            raise NativeBridgeError("region must be all, left, right, top, or bottom")
        entities = _select_spatial_half(entities, region)
        matched_count = len(entities)
        limit = payload.get("limit", 200)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 0 <= limit <= 2000:
            raise NativeBridgeError("limit must be an integer between 0 and 2000")
        returned = entities[:limit]
        return {
            "protocol_version": PROTOCOL_VERSION,
            "native_protocol_version": self._native_protocol,
            "matched_count": matched_count,
            "returned_count": len(returned),
            "entities": returned,
            "grouped_summaries": {
                "type_id": dict(Counter(str(item.get("type_id", "")) for item in entities)),
                "relation": dict(Counter(str(item.get("relation", "")) for item in entities)),
                "kind": dict(Counter(str(item.get("kind", "")) for item in entities)),
                "controllable": dict(
                    Counter(str(bool(item.get("controllable"))).lower() for item in entities)
                ),
                "in_tactical_team": dict(
                    Counter(str(bool(item.get("in_tactical_team"))).lower() for item in entities)
                ),
            },
            "entity_feed": state.get("entity_feed", {}),
            "snapshot_age_ms": state.get("snapshot_age_ms"),
        }

    def _decode_state(self, raw: bytes) -> dict[str, Any]:
        if len(raw) != SHARED_SIZE:
            raise NativeBridgeError("native shared memory has an invalid size")
        header = HEADER.unpack_from(raw, HEADER_OFFSET)
        if (
            header[0] != MAGIC
            or header[1] != self._native_protocol
            or header[2] != HEADER.size
            or header[3] != SHARED_SIZE
        ):
            raise NativeBridgeError("Mental Omega native bridge is not connected")
        values = SNAPSHOT.unpack_from(raw, SNAPSHOT_OFFSET)
        harvester_count = max(0, min(values[11], HARVESTER_CAPACITY))
        ore_count = max(0, min(values[12], ORE_CELL_CAPACITY))
        landmark_count = max(0, min(values[13], LANDMARK_CAPACITY))
        alert_count = max(0, min(values[14], ALERT_CAPACITY))
        entity_count = max(0, min(values[16], ENTITY_CAPACITY))
        harvesters = []
        for index in range(harvester_count):
            record = HARVESTER.unpack_from(raw, HARVESTERS_OFFSET + index * HARVESTER.size)
            harvesters.append(
                {
                    "unit_id": record[0],
                    "type_id": _cstring(record[1]),
                    "owner_house_id": record[2],
                    "alive": record[3] == 1,
                    "controllable": record[4] == 1,
                    "health_ratio": round(record[5], 4),
                    "cargo_ratio": round(record[6], 4),
                    "mission_id": record[7],
                    "cell": {"x": record[8], "y": record[9]},
                }
            )
        ore_cells = []
        for index in range(ore_count):
            cell = ORE_CELL.unpack_from(raw, ORE_CELLS_OFFSET + index * ORE_CELL.size)
            ore_cells.append({"x": cell[0], "y": cell[1], "value": cell[2]})
        landmarks = []
        for index in range(landmark_count):
            record = LANDMARK.unpack_from(
                raw, LANDMARKS_OFFSET + index * LANDMARK.size
            )
            landmarks.append(
                {
                    "object_id": record[0],
                    "type_id": _cstring(record[1]),
                    "display_name": _cstring(record[2]),
                    "owner_house_id": record[3],
                    "kind": LANDMARK_KINDS.get(record[4], "unknown"),
                    "faction": FACTION_NAMES.get(record[5], "unknown"),
                    "alive": record[6] == 1,
                    "controllable": record[7] == 1,
                    "captured": record[8] == 1,
                    "cell": {"x": record[9], "y": record[10]},
                }
            )
        alerts = []
        for index in range(alert_count):
            record = ALERT.unpack_from(raw, ALERTS_OFFSET + index * ALERT.size)
            alerts.append(
                {
                    "event_id": record[0],
                    "frame": record[1],
                    "kind": ALERT_KINDS.get(record[2], "unknown"),
                    "object_id": record[3],
                    "type_id": _cstring(record[4]),
                    "display_name": _cstring(record[5]),
                    "object_kind": OBJECT_KINDS.get(record[6], "unknown"),
                    "severity": record[7],
                    "cell": {"x": record[8], "y": record[9]},
                    "value": record[10],
                }
            )
        entities = []
        for index in range(entity_count):
            record = ENTITY.unpack_from(raw, ENTITIES_OFFSET + index * ENTITY.size)
            capability_flags = record[13]
            upgrade_slot_count = (capability_flags >> 24) & 0x7
            upgrade_occupied_count = (capability_flags >> 27) & 0x7
            entities.append(
                {
                    "object_id": record[0],
                    "type_id": _cstring(record[1]),
                    "display_name": _cstring(record[2]),
                    "owner_house_id": record[3],
                    "relation": OWNER_RELATIONS.get(record[4], "unknown"),
                    "kind": ENTITY_KINDS.get(record[5], "unknown"),
                    "alive": record[6] == 1,
                    "controllable": record[7] == 1,
                    "visible": record[8] == 1,
                    "health_ratio": round(record[9], 4),
                    "cell": {"x": record[10], "y": record[11]},
                    "mission_id": record[12],
                    "capability_flags": capability_flags,
                    "ammo": (capability_flags >> 8) & 0xFF,
                    "max_ammo": (capability_flags >> 16) & 0xFF,
                    "upgrade_slot_count": upgrade_slot_count,
                    "upgrade_occupied_count": upgrade_occupied_count,
                    "upgrade_free_slots": max(
                        0, upgrade_slot_count - upgrade_occupied_count,
                    ),
                    "is_engineer": bool(capability_flags & 1),
                    "transport_capacity": bool(capability_flags & 2),
                    "in_tactical_team": bool(capability_flags & 4),
                    "can_deploy": bool(capability_flags & 8),
                    "is_deployed": bool(capability_flags & 16),
                    "passenger_count": record[14],
                    "engineer_passenger_count": record[15],
                    "group_number": record[16],
                }
            )
        return {
            "protocol_version": PROTOCOL_VERSION,
            "native_protocol_version": self._native_protocol,
            "game": {
                "running": values[0] == 1,
                "mode": MODE_NAMES.get(values[1], "unknown"),
                "network_game": values[2] == 1,
                "frame": values[3],
                "paused": values[4] == 1,
            },
            "player": {"house_id": values[5], "credits": values[6]},
            "map": {
                "origin_x": values[7],
                "origin_y": values[8],
                "width": values[9],
                "height": values[10],
            },
            "snapshot_age_ms": 0,
            "harvesters": harvesters,
            "ore_cells": ore_cells,
            "landmarks": landmarks,
            "alerts": alerts,
            "economy": {"home_ore_value": values[15]},
            "visible_entities": entities,
            "entity_feed": {
                "count": entity_count,
                "capacity": ENTITY_CAPACITY,
                "truncated": entity_count >= ENTITY_CAPACITY,
            },
        }

    def _require_connected(self) -> None:
        if not self.health().connected:
            raise NativeBridgeError("Mental Omega native bridge is not connected")

    def _read_header(self) -> tuple:
        return HEADER.unpack(self._memory[: HEADER.size])

    def _read_i32(self, offset: int) -> int:
        return struct.unpack_from("<i", self._memory, offset)[0]

    def _write_i32(self, offset: int, value: int) -> None:
        struct.pack_into("<i", self._memory, offset, value)

    def _wait_for(self, offset: int, expected: int, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._read_i32(offset) == expected:
                return
            time.sleep(0.01)
        raise NativeBridgeError("native game thread did not acknowledge the request")


def _cstring(value: bytes) -> str:
    return value.split(b"\0", 1)[0].decode("utf-8", errors="replace")


def _select_spatial_half(entities: list[dict[str, Any]], region: str) -> list[dict[str, Any]]:
    if region == "all":
        return list(entities)
    horizontal = region in {"left", "right"}

    def key(item: dict[str, Any]) -> tuple[int, int]:
        cell = item.get("cell")
        if not isinstance(cell, dict):
            return (0, int(item.get("object_id", 0)))
        x, y = cell.get("x"), cell.get("y")
        axis = x - y if horizontal else x + y
        return (int(axis), int(item.get("object_id", 0)))

    ordered = sorted(entities, key=key)
    split = (len(ordered) + 1) // 2
    return ordered[:split] if region in {"left", "top"} else ordered[split:]
