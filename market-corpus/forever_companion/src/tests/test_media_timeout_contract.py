"""Keep hosted media calls inside an explicit, plugin-owned timeout budget."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _entry_timeout_map() -> dict[str, float]:
    tree = ast.parse((ROOT / "mixins" / "panel.py").read_text(encoding="utf-8"))
    constants: dict[str, float] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name) and isinstance(node.value, ast.Constant):
                if isinstance(node.value.value, (int, float)):
                    constants[target.id] = float(node.value.value)

    result: dict[str, float] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef):
            continue
        for decorator in node.decorator_list:
            if not (
                isinstance(decorator, ast.Call)
                and isinstance(decorator.func, ast.Name)
                and decorator.func.id == "plugin_entry"
            ):
                continue
            entry_id = next(
                (
                    keyword.value.value
                    for keyword in decorator.keywords
                    if keyword.arg == "id"
                    and isinstance(keyword.value, ast.Constant)
                    and isinstance(keyword.value.value, str)
                ),
                None,
            )
            timeout_name = next(
                (
                    keyword.value.id
                    for keyword in decorator.keywords
                    if keyword.arg == "timeout"
                    and isinstance(keyword.value, ast.Name)
                ),
                None,
            )
            if entry_id and timeout_name in constants:
                result[entry_id] = constants[timeout_name]
    return result


def test_media_entries_have_explicit_long_timeout_budgets() -> None:
    timeouts = _entry_timeout_map()
    assert timeouts["gallery_add"] == 120.0
    assert timeouts["get_panel_gallery"] == 120.0
    assert timeouts["get_gallery_image"] == 120.0
    assert timeouts["gallery_set_thumb"] == 120.0
    assert timeouts["gallery_remove"] == 300.0
