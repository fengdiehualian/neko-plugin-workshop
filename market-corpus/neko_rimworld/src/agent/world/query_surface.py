"""Compile the closed-enum semantic plan onto the Mod's public query contract.

The model never writes field names, native argument keys or block structure; this
module turns a validated plan into the same request the Mod already accepts, and
resolves field types, argument schemas and native notes from the schema directory
the Mod advertises.

This is the production copy of the compiler. It deliberately contains no
answer-side projection: the semantic interface renders results as plain text, so
the interning/projection helpers used by other prototypes are not here. The
inverse reader used for historical replay is also not here.
"""

import copy

OPTIONS = {"args", "where", "select", "aggregate", "group_by", "order_by", "limit", "offset", "on"}


class SurfaceError(ValueError):
    pass


class Surface:
    def __init__(self, directory):
        self.roots = {r["from"]: r for r in directory["roots"]}
        self.index = directory["field_index"]
        self.argument_schemas = {}
        self.notes = copy.deepcopy(directory.get("field_notes", {}))
        for entry in directory.get("common_fields", []):
            self.notes[entry["type"]] = {field["name"]: field.get("description") for field in entry.get("fields", [])}

    def add_query_schema(self, schema):
        branches = schema["properties"]["requests"]["items"]["allOf"][0]["oneOf"]
        self.argument_schemas = {b["properties"]["from"]["const"]: b["properties"]["args"] for b in branches}


    def child_type(self, kind, field):
        spec = self.index.get(kind, {}).get(field)
        if spec is None:
            raise SurfaceError("Unknown field " + kind + "." + field)
        return spec.split(" ")[0].strip("[]!")

    def path_type(self, root, path):
        kind = self.roots[root]["type"]
        for segment in path.split(".") if path else []:
            name, sep, concrete = segment.partition("@")
            kind = self.child_type(kind, name)
            if sep:
                if concrete not in self.index:
                    raise SurfaceError("Unknown concrete type: " + concrete)
                # Actual allowed polymorphic branches remain validated by Mod.
                kind = concrete
        return kind




    def compile(self, command):
        root = command["source"]
        if root not in self.roots or not isinstance(command.get("blocks"), list) or len(command["blocks"]) > 512:
            raise SurfaceError("Invalid source/blocks")
        result = {"from": root}
        assigned = set()

        def node_at(path):
            self.path_type(root, path)
            node = result
            for segment in path.split(".") if path else []:
                name, branch, kind = segment.partition("@")
                selected = node.setdefault("select", {})
                if not isinstance(selected, dict):
                    raise SurfaceError("Defaults cannot be merged with explicit children")
                node = selected.setdefault(name, {})
                if not isinstance(node, dict):
                    raise SurfaceError("Overlapping scalar/default selection")
                if branch:
                    node = node.setdefault("on", {}).setdefault(kind, {})
            return node

        for block in command["blocks"]:
            path = block["at"]
            if path in assigned:
                raise SurfaceError("Duplicate block: " + path)
            assigned.add(path)
            if len(path.split(".")) > 13:
                raise SurfaceError("Path too deep")
            node = node_at(path)
            for key, value in block.items():
                if key == "at":
                    continue
                if key == "fields":
                    if value is True:
                        if node.get("select"):
                            raise SurfaceError("Overlapping default selection")
                        node["select"] = True
                    else:
                        node.setdefault("select", {})
                        for field in value:
                            parent, dot, leaf = field.rpartition(".")
                            field_path = ".".join(filter(None, [path, parent])) if dot else path
                            name = leaf if dot else field
                            selected = node_at(field_path).setdefault("select", {})
                            if not isinstance(selected, dict) or name in selected:
                                raise SurfaceError("Overlapping field")
                            self.child_type(self.path_type(root, field_path), name)
                            selected[name] = True
                elif key == "empty_on":
                    node["on"] = {}
                elif key in OPTIONS - {"select", "on"}:
                    node[key] = copy.deepcopy(value)
                else:
                    raise SurfaceError("Unknown block option: " + key)
        return result
