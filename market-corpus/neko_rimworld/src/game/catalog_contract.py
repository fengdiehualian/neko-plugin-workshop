"""Current native directory checks shared by transport and semantic admission."""


def has_retired_capability_alias(world):
    """Reject an obsolete advertised selection capability; physical facets are separate."""
    if not isinstance(world, dict):
        return False
    meanings = world.get("capability_meanings")
    if isinstance(meanings, dict) and "production" in meanings:
        return True
    tools = world.get("tools")
    if not isinstance(tools, list):
        return False
    for tool in tools:
        if not isinstance(tool, dict) or tool.get("name") != "survey_space":
            continue
        try:
            values = tool["parameters"]["properties"]["select"]["properties"]["capabilities"]["items"]["enum"]
        except (KeyError, TypeError):
            continue
        if isinstance(values, list) and "production" in values:
            return True
    return False
