"""Typed Scratch strategy AST compiler and isolated per-match state."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Iterable

from .block_registry import block_by_id, block_index, block_registry
from .strategy_atomic_migration import atomic_scripts_from_payload


class BlockCompileError(ValueError):
    """A strategy cannot be represented safely as executable blocks."""


_LANES = {"events": "帽子", "conditions": "布尔", "actions": "命令", "validations": "布尔", "learning": "命令"}
_ACTION_WORDS = {
    "移动": "move", "攻击": "attack", "部署": "deploy", "解除部署": "undeploy",
    "装载": "load", "卸载": "unload", "维修": "repair", "占领": "capture",
    "建造": "build", "生产": "produce", "安装插件": "install_upgrade",
    "使用": "support", "撤退": "retreat", "征召": "recruit", "编队": "assemble",
}
_CONTROL_WORDS = {"如果": "if", "重复": "repeat", "持续监视": "watch", "并行": "parallel", "等待": "wait", "当": "event"}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _infer_opcode(label: str, shape: str) -> str:
    for prefix, opcode in _CONTROL_WORDS.items():
        if label.startswith(prefix):
            return opcode
    for prefix, opcode in _ACTION_WORDS.items():
        if label.startswith(prefix):
            return opcode
    if shape == "布尔":
        return "condition.custom"
    if shape == "报告器":
        return "reporter.custom"
    return "custom.block"


def _definition_id(node: dict[str, Any]) -> str:
    return _text(node.get("block_id") or node.get("definition_id") or node.get("id"))


def _node(identifier: str, inputs: dict[str, Any] | None = None, stacks: dict[str, list[dict[str, Any]]] | None = None) -> dict[str, Any]:
    definition = block_by_id(identifier)
    if definition is None:
        raise BlockCompileError(f"积木注册表中不存在“{identifier}”")
    return {
        "node_id": "",
        "block_id": identifier,
        "label_zh": definition["name_zh"],
        "shape": definition["shape"],
        "output": definition.get("output", ""),
        "inputs": deepcopy(inputs or {}),
        "stacks": deepcopy(stacks or {}),
        "executor": definition.get("executor", ""),
    }


def _default_reporter(identifier: str, inputs: dict[str, Any] | None = None) -> dict[str, Any]:
    return _node(identifier, inputs)


def _legacy_command(value: Any) -> dict[str, Any]:
    """Keep old lanes for runtime compatibility while marking them as hidden."""
    if isinstance(value, dict):
        node = deepcopy(value)
        label = _text(node.get("label_zh") or node.get("name_zh") or node.get("legacy_id")) or "旧策略动作"
    else:
        node = {}
        label = _text(value) or "旧策略动作"
    node.update({
        "label_zh": label,
        "shape": node.get("shape") or node.get("kind") or "命令",
        "lane": "actions",
        "inputs": node.get("inputs") if isinstance(node.get("inputs"), dict) else {},
        "opcode": _text(node.get("opcode")) or _infer_opcode(label, "命令"),
        "legacy_hidden": True,
        "requires_validation": not bool(node.get("skip_validation")),
    })
    return node


def _legacy_threat(source: dict[str, Any]) -> str:
    conditions = source.get("conditions") if isinstance(source.get("conditions"), dict) else {}
    raw = _text(conditions.get("primary_threat") or source.get("primary_threat")).lower()
    if any(word in raw for word in ("transport", "drop", "air")):
        return "空投"
    if any(word in raw for word in ("armor", "tank", "vehicle")):
        return "装甲"
    if any(word in raw for word in ("naval", "submarine", "fleet")):
        return "海军"
    if any(word in raw for word in ("siege", "artillery")):
        return "攻城"
    if "super" in raw:
        return "超级武器"
    return "步兵"


def _legacy_action_to_standard(value: Any, source: dict[str, Any]) -> dict[str, Any]:
    raw = _text(value.get("legacy_id") or value.get("label_zh") or value.get("opcode")) if isinstance(value, dict) else _text(value)
    lowered = raw.lower()
    attack_team = _default_reporter("单位.指定编队", {"编队": "进攻队"})
    defense_team = _default_reporter("单位.指定编队", {"编队": "防御队"})
    safe_position = _default_reporter("空间.安全位置", {"对象": defense_team})
    if any(word in lowered for word in ("counter", "hard_counter", "intercept")):
        return _node("克制.切换方案", {"威胁类型": _legacy_threat(source)})
    if any(word in lowered for word in ("delay", "retreat", "regroup")):
        return _node("行动.撤退", {"队伍": defense_team, "位置": safe_position})
    if any(word in lowered for word in ("reinforce", "produce", "ratio")):
        return _node("队伍.补员", {"队伍": defense_team, "数量": 20})
    if any(word in lowered for word in ("commit", "attack", "assault", "break_", "destroy")):
        target = _default_reporter("空间.最高价值", {"候选目标": _default_reporter("建筑.生产")})
        return _node("行动.攻击", {"队伍": attack_team, "目标": target})
    if any(word in lowered for word in ("choke", "hold", "defend", "protect")):
        return _node("行动.移动", {"队伍": defense_team, "位置": _default_reporter("空间.基地中心")})
    if any(word in lowered for word in ("scout", "contest", "identify", "reassess")):
        return _node("控制.重新判断")
    if any(word in lowered for word in ("deploy", "garrison")):
        return _node("行动.部署", {"单位": _default_reporter("单位.空闲")})
    if any(word in lowered for word in ("repair", "restore")):
        return _node("行动.维修", {"对象": _default_reporter("建筑.受损")})
    return _node("控制.重新判断")


def _legacy_scripts(source: dict[str, Any], actions: list[Any]) -> list[dict[str, Any]]:
    body = [_legacy_action_to_standard(action, source) for action in actions]
    if not body:
        body = [_node("控制.重新判断")]
    event_id = "事件.敌军出现" if source.get("conditions") or source.get("strategy_type") == "combat_matrix" else "事件.对局开始"
    return [{**_node(event_id), "stacks": {"执行": body}}]


def _custom_index(definitions: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for item in definitions:
        if not isinstance(item, dict):
            continue
        identifier = _text(item.get("id"))
        name = _text(item.get("name_zh"))
        if identifier:
            result[identifier] = item
        if name:
            result[name] = item
    return result


def _accepted_outputs(expected: str) -> set[str]:
    registry = block_registry()
    aliases = registry.get("connection_types", {})
    accepted = {expected}
    accepted.update(aliases.get(expected, []))
    if expected == "任意值":
        accepted.add("任意值")
    return accepted


def _is_literal_type(value: Any, expected: str) -> bool:
    if expected in {"菜单", "单位菜单", "文字", "文字输入", "任意值"}:
        return not isinstance(value, (list, tuple))
    if expected in {"数字", "数字输入"}:
        if isinstance(value, bool):
            return False
        try:
            float(value)
            return True
        except (TypeError, ValueError):
            return False
    return False


def _compile_input(value: Any, expected: str, custom: dict[str, dict[str, Any]], stack: tuple[str, ...], path: str) -> Any:
    if isinstance(value, dict) and (_definition_id(value) or value.get("custom_id")):
        nested = _compile_node(value, custom, stack, path)
        output = _text(nested.get("output"))
        if output not in _accepted_outputs(expected):
            raise BlockCompileError(f"{path}需要“{expected}”，不能插入“{output or nested.get('shape')}”")
        return nested
    if _is_literal_type(value, expected):
        if expected in {"数字", "数字输入"}:
            number = float(value)
            return int(number) if number.is_integer() else number
        return deepcopy(value)
    raise BlockCompileError(f"{path}缺少“{expected}”类型的积木")


def _custom_definition(node: dict[str, Any], custom: dict[str, dict[str, Any]]) -> tuple[str, dict[str, Any] | None]:
    identifier = _text(node.get("custom_id"))
    block_identifier = _definition_id(node)
    if not identifier and block_identifier.startswith("自制."):
        identifier = block_identifier.split(".", 1)[1]
    return identifier, custom.get(identifier) or custom.get(_text(node.get("label_zh")))


def _compile_custom(node: dict[str, Any], identifier: str, definition: dict[str, Any], custom: dict[str, dict[str, Any]], stack: tuple[str, ...], path: str) -> dict[str, Any]:
    key = identifier or _text(definition.get("id") or definition.get("name_zh"))
    if key in stack:
        raise BlockCompileError(f"自制积木递归调用：{' → '.join(stack + (key,))}")
    shape = _text(definition.get("shape")) or "命令"
    parameters = definition.get("parameters") if isinstance(definition.get("parameters"), list) else []
    parameter_types = definition.get("parameter_types") if isinstance(definition.get("parameter_types"), dict) else {}
    inputs: dict[str, Any] = {}
    raw_inputs = node.get("inputs") if isinstance(node.get("inputs"), dict) else {}
    for parameter in parameters:
        name = _text(parameter.get("name_zh")) if isinstance(parameter, dict) else _text(parameter)
        expected = _text(parameter.get("type")) if isinstance(parameter, dict) else _text(parameter_types.get(name) or "任意值")
        if name:
            inputs[name] = _compile_input(raw_inputs.get(name), expected or "任意值", custom, stack + (key,), f"{path}/{name}")
    body = definition.get("body") or definition.get("children") or []
    if not isinstance(body, list):
        raise BlockCompileError(f"自制积木“{key}”的子脚本必须是积木数组")
    compiled_body = [_compile_node(child, custom, stack + (key,), f"{path}/自制脚本") for child in body]
    return {
        "node_id": _text(node.get("node_id")), "block_id": f"自制.{key}",
        "label_zh": _text(definition.get("name_zh")) or key, "shape": shape,
        "output": _text(definition.get("output")), "inputs": inputs,
        "stacks": {"执行": compiled_body} if compiled_body else {},
        "custom_definition": key, "opcode": "custom.invoke",
    }


def _compile_node(node: Any, custom: dict[str, dict[str, Any]], stack: tuple[str, ...], path: str) -> dict[str, Any]:
    if not isinstance(node, dict):
        raise BlockCompileError(f"{path}必须是积木对象")
    custom_id, custom_definition = _custom_definition(node, custom)
    if custom_definition is not None:
        return _compile_custom(node, custom_id, custom_definition, custom, stack, path)
    identifier = _definition_id(node)
    definition = block_by_id(identifier)
    if definition is None:
        raise BlockCompileError(f"{path}使用了未注册积木“{identifier or node.get('label_zh') or '未命名'}”")
    raw_inputs = node.get("inputs") if isinstance(node.get("inputs"), dict) else {}
    inputs: dict[str, Any] = {}
    for item in definition.get("inputs", []):
        name = item["name_zh"]
        raw = raw_inputs.get(name, item.get("default"))
        inputs[name] = _compile_input(raw, item["type"], custom, stack, f"{path}/{definition['name_zh']}/{name}")
    allowed_stacks = list(definition.get("stacks") or [])
    if definition.get("shape") == "帽子" and not allowed_stacks:
        allowed_stacks = ["执行"]
    raw_stacks = node.get("stacks") if isinstance(node.get("stacks"), dict) else {}
    if not raw_stacks and isinstance(node.get("children"), list) and allowed_stacks:
        raw_stacks = {allowed_stacks[0]: node["children"]}
    stacks: dict[str, list[dict[str, Any]]] = {}
    for name in allowed_stacks:
        children = raw_stacks.get(name, [])
        if not isinstance(children, list):
            raise BlockCompileError(f"{path}/{definition['name_zh']}/{name}必须是积木数组")
        compiled_children = [_compile_node(child, custom, stack, f"{path}/{definition['name_zh']}/{name}") for child in children]
        for child in compiled_children:
            if child.get("shape") not in {"命令", "容器"}:
                raise BlockCompileError(f"{path}/{definition['name_zh']}/{name}只能放入命令或控制积木")
        stacks[name] = compiled_children
    return {
        "node_id": _text(node.get("node_id")), "block_id": identifier,
        "label_zh": definition["name_zh"], "shape": definition["shape"],
        "output": definition.get("output", ""), "inputs": inputs, "stacks": stacks,
        "executor": definition.get("executor", ""), "opcode": identifier,
    }


def _flatten_commands(nodes: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for node in nodes:
        if node.get("shape") in {"命令", "容器"}:
            result.append(node)
        for children in (node.get("stacks") or {}).values():
            result.extend(_flatten_commands(children))
    return result


def _compile_scripts(raw_scripts: Any, custom: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(raw_scripts, list):
        raise BlockCompileError("scripts 必须是事件脚本数组")
    scripts = [_compile_node(node, custom, (), f"脚本 {index + 1}") for index, node in enumerate(raw_scripts)]
    for index, script in enumerate(scripts):
        if script.get("shape") != "帽子":
            raise BlockCompileError(f"脚本 {index + 1} 必须以事件帽子积木开始")
        body = (script.get("stacks") or {}).get("执行", [])
        if not body:
            raise BlockCompileError(f"脚本 {index + 1} 的事件下面还没有行动")
    return scripts


def compile_program(
    payload: dict[str, Any] | None, *, strategy_id: str = "", strategy_type: str = "",
) -> dict[str, Any]:
    """Compile a strategy into one strict tree made only of atomic blocks."""
    source = payload if isinstance(payload, dict) else {}
    program = source.get("block_program") if isinstance(source.get("block_program"), dict) else {}
    custom = _custom_index(source.get("custom_blocks") if isinstance(source.get("custom_blocks"), list) else [])
    raw_scripts = program.get("scripts")
    if not isinstance(raw_scripts, list) or not raw_scripts:
        raw_scripts = atomic_scripts_from_payload(
            source, strategy_id=strategy_id, strategy_type=strategy_type,
        )
    scripts = _compile_scripts(raw_scripts, custom)

    commands = _flatten_commands(scripts)
    diagnostics: list[str] = []
    if commands and not any(command.get("block_id", "").startswith("验证.") for command in commands):
        diagnostics.append("策略包含行动；建议用“等待直到”连接验证积木确认结果。")
    result_program = {"scripts": scripts}
    return {
        "schema": "mo.scratch.ast", "schema_version": 2,
        "variables": deepcopy(source.get("variables") if isinstance(source.get("variables"), dict) else {}),
        "lists": deepcopy(source.get("lists") if isinstance(source.get("lists"), dict) else {}),
        "custom_blocks": deepcopy(source.get("custom_blocks") if isinstance(source.get("custom_blocks"), list) else []),
        "program": result_program, "diagnostics": diagnostics,
    }


@dataclass
class BlockRuntime:
    """Isolated variable/list state for one running strategy instance."""

    variables: dict[str, Any] = field(default_factory=dict)
    lists: dict[str, list[Any]] = field(default_factory=dict)

    @classmethod
    def from_ast(cls, ast: dict[str, Any]) -> "BlockRuntime":
        raw_variables = ast.get("variables") if isinstance(ast.get("variables"), dict) else {}
        raw_lists = ast.get("lists") if isinstance(ast.get("lists"), dict) else {}
        variables = {
            str(name): deepcopy(value.get("value")) if isinstance(value, dict) and "value" in value else deepcopy(value)
            for name, value in raw_variables.items()
        }
        lists = {
            str(name): list(value.get("items") or []) if isinstance(value, dict) else list(value) if isinstance(value, list) else []
            for name, value in raw_lists.items()
        }
        return cls(variables, lists)

    def get(self, name: str, default: Any = 0) -> Any:
        return self.variables.get(str(name), default)

    def set(self, name: str, value: Any) -> Any:
        self.variables[str(name)] = value
        return value

    def change(self, name: str, amount: float = 1) -> Any:
        current = self.get(name, 0)
        try:
            value = float(current) + float(amount)
            value = int(value) if value.is_integer() else value
        except (TypeError, ValueError) as exc:
            raise BlockCompileError(f"变量“{name}”不是数字，不能增加") from exc
        return self.set(name, value)

    def add(self, name: str, value: Any) -> None:
        self.lists.setdefault(str(name), []).append(value)

    def remove(self, name: str, value: Any) -> bool:
        values = self.lists.setdefault(str(name), [])
        try:
            values.remove(value)
            return True
        except ValueError:
            return False

    def clear(self, name: str) -> None:
        self.lists[str(name)] = []

    def length(self, name: str) -> int:
        return len(self.lists.get(str(name), []))
