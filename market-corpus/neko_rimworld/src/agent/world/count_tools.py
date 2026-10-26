"""The public population-measure contract."""

from ...schema_serialization import schema_contract
from .semantic_surface import GROUPINGS, METRICS, POPULATIONS, _object_schema


def count_spec(directory=None):
    spec = dict(
        name="count",
        description='Count a selection, e.g. object={"kind":"colonists"},metric="records". units sums stack counts for things. With by, detail limits groups, not instance examples; without by, detail returns examples. Scope/completeness govern claims.',
        parameters={
            "type": "object",
            "properties": {
                "object": _object_schema(directory),
                "metric": {"type": "string", "enum": list(METRICS)},
                "by": {"type": "string", "enum": list(GROUPINGS)},
                "detail": {"type": "integer", "minimum": 0, "maximum": 100, "default": 0},
            },
            "required": ["object", "metric"],
            "additionalProperties": False,
        },
    )
    count = spec["parameters"]
    count["properties"]["object"]["properties"]["kind"]["enum"] = list(POPULATIONS)
    spec["parameters"] = schema_contract(spec["parameters"])
    return spec
