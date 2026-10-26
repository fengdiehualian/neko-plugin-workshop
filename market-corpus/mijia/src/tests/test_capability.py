"""capability 能力层单元测试：单位/范围校验 + 薄 CommandIR。"""

from nlp.capability import (
    CommandIR,
    command_unit,
    property_unit,
    to_command_ir,
    validate_command,
)
from nlp.control_parser import ParseResult
from nlp.device_matcher import MatchResult
from nlp.router import RouteResult

# 模拟设备属性（名/类型/访问/范围/枚举）
BRIGHTNESS_PROP = {"name": "Brightness", "type": "uint8", "access": "read_write", "value_range": [0, 100, 1]}
TEMPERATURE_PROP = {"name": "Target Temperature", "type": "float", "access": "read_write", "value_range": [16, 30, 1]}
MODE_PROP = {"name": "Mode", "type": "uint", "access": "read_write", "value_list": [{"value": 0, "description": "Constant Humidity"}, {"value": 1, "description": "Sleep"}]}


def test_command_unit():
    assert command_unit("档") == "level"
    assert command_unit("档位") == "level"
    assert command_unit("度") == "celsius"
    assert command_unit("%") == "percent"
    assert command_unit("") is None


def test_property_unit():
    assert property_unit(BRIGHTNESS_PROP) == "percent"
    assert property_unit(TEMPERATURE_PROP) == "celsius"
    assert property_unit(MODE_PROP) == "enum"
    assert property_unit({"name": "Switch Status"}) is None


def test_validate_unit_mismatch():
    # 亮度 不接受 档 单位（reviewer 案例：亮度调到三档 → INVALID_UNIT）
    r = validate_command(BRIGHTNESS_PROP, "亮度", 3, "档")
    assert r.status == "invalid_unit"


def test_validate_unit_ok():
    assert validate_command(TEMPERATURE_PROP, "温度", 26, "度").status == "valid"
    assert validate_command(BRIGHTNESS_PROP, "亮度", 50, "%").status == "valid"
    # 无单位命令不触发单位校验（单位由设备能力推断）
    assert validate_command(TEMPERATURE_PROP, "温度", 26, "").status == "valid"


def test_validate_range():
    r = validate_command(TEMPERATURE_PROP, "温度", 40, "度")
    assert r.status == "invalid_value"


def test_to_command_ir():
    result = RouteResult(
        branch="control", raw_text="主卧空调风速调到三",
        match=MatchResult("ok", [{"did": "dev_master_ac", "name": "空调", "room_name": "主卧"}]),
        parsed=ParseResult(device="主卧空调", action="set_prop", prop="风速", value=3),
    )
    ir = to_command_ir(result)
    assert isinstance(ir, CommandIR)
    assert ir.raw_text == "主卧空调风速调到三"
    assert ir.room == "主卧"
    assert ir.device == "空调"
    assert ir.device_id == "dev_master_ac"
    assert ir.attribute == "风速"
    assert ir.value == 3
