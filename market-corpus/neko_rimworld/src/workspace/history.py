"""Read selected paths from retained original observations."""

import copy


def pointer(value, path):
    """Read a literal JSON pointer; missing is an error, null remains null."""
    if not isinstance(path, str) or not path.startswith("/"):
        raise ValueError("Use a JSON pointer beginning with /")
    node = value
    for part in path[1:].split("/"):
        if "~" in part.replace("~0", "").replace("~1", ""):
            raise ValueError("Invalid JSON pointer escape")
        key = part.replace("~1", "/").replace("~0", "~")
        if isinstance(node, dict) and key in node:
            node = node[key]
        elif (
            isinstance(node, list)
            and key.isascii()
            and key.isdecimal()
            and (key == "0" or not key.startswith("0"))
            and int(key) < len(node)
        ):
            node = node[int(key)]
        else:
            raise ValueError("The requested source path is missing")
    return copy.deepcopy(node)
