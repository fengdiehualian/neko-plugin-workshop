"""Model-facing region survey; native geometry and coverage remain catalog-owned."""

import copy

from ...schema_serialization import schema_contract


def survey_spec(native):
    spec = copy.deepcopy(native)
    spec["description"] = (
        "Survey a region. Omitted include keeps the full survey; explicit include selects environment/objects, geometry adds intersecting areas/zones. objects overrides selection; cursor keeps pages. select filters objects, not hazards. Distances are to region center; footprint/coverage remain."
    )
    spec["parameters"]["properties"]["include"] = {
        "type": "array",
        "uniqueItems": True,
        "items": {"enum": ["environment", "objects", "geometry"]},
        "description": "Optional content in this same call: environment for hazards, objects for inventories, geometry for intersections. Unrequested environment is not evidence of safety.",
    }
    spec["description"] = (
        "Survey a region. Omitted include keeps the full survey; explicit include selects environment/objects, geometry adds intersecting areas/zones. objects overrides object selection; cursor keeps pages. select filters objects, not hazards. Distances are to the region center, not a route's closest point. Footprint/coverage remain."
    )
    spec["parameters"] = schema_contract(spec["parameters"])
    return spec
