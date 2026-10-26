"""能力层：设备属性能力校验（单位/范围/枚举）+ 轻量 CommandIR。

边界：NLP 只产生"明确实体 + 明确属性 + 值 + 单位"；本层依据**设备能力**
校验该组合是否兼容（如 亮度 不接受 档 单位），不做语义候选/消歧。
"""

from dataclasses import dataclass
from typing import Any, Optional

# 中文属性 → 语义单位
_UNIT_BY_ATTR = {
    "温度": "celsius", "水温": "celsius",
    "亮度": "percent", "湿度": "percent", "音量": "percent",
    "风速": "level", "档位": "level", "吸力": "level",
    "模式": "enum", "颜色": "enum",
}
# 属性名关键词（英文 spec 名 → 中文属性），用于从设备属性推断语义单位
_ATTR_KEYWORDS = {
    "温度": ["温度", "temperature", "temp"],
    "水温": ["水温", "tds", "water"],
    "亮度": ["亮度", "brightness"],
    "湿度": ["湿度", "humidity"],
    "音量": ["音量", "volume"],
    "风速": ["风速", "风量", "fan", "fan level", "fan_level", "fan-speed"],
    "档位": ["档位", "档", "level", "suction", "heat level"],
    "吸力": ["吸力", "suction"],
    "模式": ["模式", "mode"],
    "颜色": ["颜色", "color", "rgb"],
}
# 命令携带的单位 → 语义单位
_COMMAND_UNIT = {
    "度": "celsius", "℃": "celsius", "°": "celsius",
    "%": "percent",
    "档": "level", "档位": "level",
}


@dataclass
class CommandIR:
    """轻量中间表示：路由结果 + 匹配设备 展平成统一结构。"""

    raw_text: str = ""
    action: str = ""
    room: str = ""
    device: str = ""
    device_id: str = ""
    attribute: str = ""
    value: Any = None
    unit: str = ""
    direction: int = 1
    delta: Optional[float] = None


@dataclass
class ValidationResult:
    """能力校验结果。

    status: "valid" | "invalid_unit" | "invalid_value" | "unknown_attribute"
    """

    status: str
    message: str = ""


def command_unit(unit: str) -> Optional[str]:
    """命令单位 → 语义单位（度→celsius、%→percent、档→level）。"""
    return _COMMAND_UNIT.get(unit)


def to_command_ir(result) -> CommandIR:
    """把路由结果展平为轻量 CommandIR（供能力校验与诊断；鸭子类型，避免循环导入）。"""
    ir = CommandIR(raw_text=getattr(result, "raw_text", ""), action=getattr(result, "branch", ""))
    match = getattr(result, "match", None)
    if match is not None and match.status == "ok" and match.device is not None:
        ir.room = match.device.get("room_name", "")
        ir.device = match.device.get("name", "")
        ir.device_id = match.device.get("did", "")
    else:
        ir.device = getattr(result, "device_hint", "")
    parsed = getattr(result, "parsed", None)
    if parsed is not None:
        ir.attribute = parsed.prop or ""
        ir.value = parsed.value
        ir.unit = parsed.unit
        ir.direction = parsed.direction
        ir.delta = parsed.delta
    elif ir.action == "switch":
        ir.attribute = "开关"
    return ir


def property_unit(prop: dict) -> Optional[str]:
    """从设备属性推断语义单位（枚举优先，其次按属性名关键词）。"""
    if prop.get("value_list"):
        return "enum"
    name = (prop.get("name") or "").lower()
    for cn, keys in _ATTR_KEYWORDS.items():
        if any(k in name for k in keys):
            return _UNIT_BY_ATTR.get(cn)
    return None


def validate_command(prop: dict, attribute: str, value: Any, unit: str) -> ValidationResult:
    """校验属性/值/单位是否与设备能力兼容（单位 + 范围）。"""
    cmd_u = command_unit(unit)
    prop_u = property_unit(prop)
    if cmd_u and prop_u and prop_u != "enum" and cmd_u != prop_u:
        return ValidationResult(
            "invalid_unit",
            f"属性'{attribute}'不接受单位'{unit}'（设备能力单位不是它）",
        )
    vr = prop.get("value_range") or []
    if value is not None and isinstance(value, (int, float)) and len(vr) >= 2:
        if value < vr[0] or value > vr[1]:
            return ValidationResult(
                "invalid_value",
                f"'{attribute}'={value} 超出设备支持范围 {vr[:2]}",
            )
    return ValidationResult("valid")
