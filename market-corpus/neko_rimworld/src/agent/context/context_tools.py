"""Small model-facing contracts; native/request validation stays unchanged."""

from __future__ import annotations

import copy

from ...schema_serialization import compact_tools, remove_redundant_constraints


def model_tools(tools):
    """Only equivalent schema syntax reduction; each business owner owns its semantics."""
    result = copy.deepcopy(tools)
    for tool in result:
        function = tool.get("function", {})
        if isinstance(function.get("parameters"), dict):
            function["parameters"] = remove_redundant_constraints(function["parameters"])
    return compact_tools(result)
