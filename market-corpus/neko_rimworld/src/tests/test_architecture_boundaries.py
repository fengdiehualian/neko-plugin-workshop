"""Current imports and public input projection have one supported path."""

import ast
import copy
from pathlib import Path

import pytest
from plugin.plugins.neko_rimworld.agent.context.model_context import model_size
from plugin.plugins.neko_rimworld.agent.runtime.tools import ToolRegistry
from plugin.plugins.neko_rimworld.agent.world.object_identity import canonical_object_identity


def test_production_module_import_graph_has_no_cycles_including_deferred_imports():
    root = Path(__file__).resolve().parents[1]
    paths = list(root.glob("*.py"))
    for package in ("agent", "game", "workspace", "mechanisms"):
        paths.extend((root / package).rglob("*.py"))
    modules = {}
    for path in paths:
        parts = list(path.relative_to(root).with_suffix("").parts)
        if parts[-1] == "__init__":
            parts.pop()
        modules[".".join(parts)] = path
    graph = {name: set() for name in modules}
    for name, path in modules.items():
        package = name if path.name == "__init__.py" else name.rpartition(".")[0]
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                targets = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                parts = package.split(".") if package else []
                prefix = ".".join(parts[:len(parts) - node.level + 1]) if node.level else ""
                target = ".".join(part for part in (prefix, node.module or "") if part)
                targets = [target, *[(target + "." if target else "") + alias.name for alias in node.names]]
            else:
                continue
            graph[name].update(target for target in targets if target in modules and target != name)

    completed = set()

    def visit(name, active):
        assert name not in active, "Import cycle: " + " -> ".join((*active, name))
        if name in completed:
            return
        for target in sorted(graph[name]):
            visit(target, (*active, name))
        completed.add(name)

    for name in graph:
        visit(name, ())


@pytest.mark.parametrize("value", [False, True, None])
def test_retired_context_mode_is_rejected_by_both_public_apis(value):
    with pytest.raises(TypeError, match="context_enabled"):
        ToolRegistry(context_enabled=value)
    with pytest.raises(TypeError, match="context_enabled"):
        model_size({}, context_enabled=value)


def test_canonical_location_retains_distinct_maps_unknowns_and_status_without_aliases():
    raw = {
        "id": "Pawn_7", "spawned": True, "map_id": "selection-map",
        "status": {"position": {"map_id": "own-map", "x": 1, "z": 2}, "drafted": True},
        "map_held": {"id": "holder-map", "label": "Observed holder map"},
        "held_position": {"map": "holder-map", "x": 3, "z": 4},
        "extension": {"unknown": None},
    }
    original = copy.deepcopy(raw)
    projected = canonical_object_identity(raw)
    assert projected["location"] == {
        "position": {"kind": "cell", "map": "own-map", "x": 1, "z": 2},
        "held_position": {"kind": "cell", "map": "holder-map", "x": 3, "z": 4},
    }
    assert projected["map_id"] == "selection-map" and projected["map_held"] == raw["map_held"]
    assert projected["status"] == {"drafted": True} and projected["extension"] == {"unknown": None}
    assert not {"position", "held_position", "map"} & projected.keys()
    assert canonical_object_identity(projected) == projected
    assert raw == original


def test_unbound_own_cell_is_unknown_even_when_holder_map_and_coordinates_exist():
    raw = {"id": "Thing_8", "position": {"x": 1, "z": 2},
           "map_held": {"id": "Map_2"}, "held_position": {"map": "Map_2", "x": 4, "z": 5}}
    projected = canonical_object_identity(raw)
    assert projected["location"]["position"] is None
    assert projected["location"]["held_position"] == {"kind": "cell", "map": "Map_2", "x": 4, "z": 5}


def test_identity_with_unread_coordinates_keeps_explicit_unknown_locations():
    projected = canonical_object_identity({"id": "Pawn_9", "map_id": "Map_0", "name": "Alex"})
    assert projected["location"] == {"position": None, "held_position": None}
    assert projected["map_id"] == "Map_0"
