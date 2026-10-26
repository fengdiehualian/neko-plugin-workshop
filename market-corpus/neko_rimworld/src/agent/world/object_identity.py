"""One object location projection over current native and public-query facts."""

import copy


def canonical_object_identity(value):
    """Expose own/held cells once without changing the retained observation."""
    identity = copy.deepcopy(value)
    if not isinstance(identity, dict) or not identity.get("id"):
        return identity
    status = identity.get("status") if isinstance(identity.get("status"), dict) else {}
    location = identity.get("location") if isinstance(identity.get("location"), dict) else {}
    own = location.get("position", status.get("position", identity.get("position")))
    held = location.get("held_position", identity.get("held_position"))

    def cell(position, *, own=False):
        if not isinstance(position, dict) or any(type(position.get(k)) is not int for k in ("x", "z")):
            return None
        map_id = position.get("map", position.get("map_id"))
        if not map_id and own and identity.get("spawned") is True:
            map_id = identity.get("map_id") or (identity.get("map_held") or {}).get("id") or identity.get("map")
        if not isinstance(map_id, str) or not map_id:
            return None
        return {"kind": "cell", "map": map_id, "x": position["x"], "z": position["z"]}

    identity["location"] = {
        **location,
        "position": None if identity.get("spawned") is False else cell(own, own=True),
        "held_position": cell(held),
    }
    if "map" in identity:
        identity.setdefault("map_id", identity.pop("map"))
    identity.pop("position", None)
    identity.pop("held_position", None)
    if "position" in status:
        remaining_status = {key: item for key, item in status.items() if key != "position"}
        if remaining_status:
            identity["status"] = remaining_status
        else:
            identity.pop("status", None)
    return identity


def normalize_object_identities(result):
    result = copy.deepcopy(result)
    for row in result.get("facts", []):
        if isinstance(row.get("object"), dict):
            row["object"] = canonical_object_identity(row["object"])
        identity = row.get("facts", {}).get("identity")
        if isinstance(identity, dict) and isinstance(identity.get("object"), dict):
            identity["object"] = canonical_object_identity(identity["object"])
    return result
