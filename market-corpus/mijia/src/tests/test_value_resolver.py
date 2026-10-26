"""value_resolver 数值抽取 / 相对量 / 钳值测试（含 control_parser 联动回归）。"""

from nlp.control_parser import parse_control_command
from nlp.value_resolver import (
    extract_number,
    infer_prop_from_unit,
    parse_delta,
    parse_prop_value,
    resolve_adjust_target,
)


def test_extract_number():
    assert extract_number("26度") == (26, "度")
    assert extract_number("调到50%") == (50, "%")
    assert extract_number("温度23.5") == (23.5, "")


def test_extract_number_none():
    assert extract_number("调亮一点") is None


def test_parse_prop_value():
    assert parse_prop_value("亮度50") == ("亮度", 50, "")
    assert parse_prop_value("26度") == (None, 26, "度")


def test_infer_prop_from_unit():
    assert infer_prop_from_unit("度", 26) == "温度"
    assert infer_prop_from_unit("%", 50) == "亮度"
    assert infer_prop_from_unit("", 50) == "亮度"  # 0-100 整数兜底
    assert infer_prop_from_unit("", 200) is None


def test_parse_delta():
    assert parse_delta("调高5度") == 5.0
    assert parse_delta("调亮一点") is None


def test_parse_number_boundary():
    # 纯数字分界（前一位是中文）："卧室灯50%" → 亮度=50
    parsed = parse_control_command("卧室灯50%")
    assert parsed is not None
    assert parsed.device == "卧室灯"
    assert parsed.action == "set_prop"
    assert parsed.prop == "亮度"
    assert parsed.value == 50


def test_parse_single_tiao_split():
    # 单字"调"分界（后跟数字）："空调调26度" → 温度=26
    parsed = parse_control_command("空调调26度")
    assert parsed is not None
    assert parsed.device == "空调"
    assert parsed.prop == "温度"
    assert parsed.value == 26


def test_parse_verb_split_number():
    # 动词分界："空调调到26度" → 温度=26
    parsed = parse_control_command("空调调到26度")
    assert parsed is not None
    assert parsed.device == "空调"
    assert parsed.prop == "温度"
    assert parsed.value == 26


def test_parse_prop_with_value():
    # 属性词分界："灯亮度50%" → 亮度=50
    parsed = parse_control_command("灯亮度50%")
    assert parsed is not None
    assert parsed.device == "灯"
    assert parsed.prop == "亮度"
    assert parsed.value == 50


def test_relative_adjust_parsed():
    # 相对量（无显式 delta）："温度调高一点" → adjust_prop 方向 +1
    parsed = parse_control_command("空调温度调高一点")
    assert parsed is not None
    assert parsed.action == "adjust_prop"
    assert parsed.prop == "温度"
    assert parsed.direction == 1
    assert parsed.delta is None


def test_relative_adjust_with_explicit_delta():
    # "空调温度调高5度" 是相对调整（升高 5 度），而非绝对设定为 5
    parsed = parse_control_command("空调温度调高5度")
    assert parsed is not None
    assert parsed.action == "adjust_prop"
    assert parsed.prop == "温度"
    assert parsed.direction == 1
    assert parsed.delta == 5.0


def test_relative_decrease_chinese_numeral():
    # "空调调低两度" → 相对降低 2 度（中文数字"两"→2）
    parsed = parse_control_command("空调调低两度")
    assert parsed is not None
    assert parsed.action == "adjust_prop"
    assert parsed.prop == "温度"
    assert parsed.direction == -1
    assert parsed.delta == 2.0


def test_resolve_adjust_target_default_step():
    # 范围 [0,100,1]，无 delta：默认步长 = max(1, 100*0.1) = 10
    assert resolve_adjust_target(50, 1, None, [0, 100, 1]) == 60
    assert resolve_adjust_target(95, 1, None, [0, 100, 1]) == 100  # 钳到上限
    assert resolve_adjust_target(5, -1, None, [0, 100, 1]) == 0     # 钳到下限


def test_resolve_adjust_target_explicit_delta():
    assert resolve_adjust_target(50, 1, 5.0, [0, 100, 1]) == 55
    assert resolve_adjust_target(50, -1, 5.0, [0, 100, 1]) == 45


def test_resolve_adjust_target_step_align():
    # step=5：61 → 对齐到 60
    assert resolve_adjust_target(51, 1, None, [0, 100, 5]) == 60


def test_resolve_adjust_target_align_reclamp():
    # 步长对齐后可能越界（[0,100,60] 下 t=100 → round(100/60)*60=120），需重新钳回
    assert resolve_adjust_target(100, 1, None, [0, 100, 60]) == 100
    assert resolve_adjust_target(0, -1, None, [0, 100, 60]) == 0


def test_parse_split_verb_shecheng():
    # 分界动词"设成"："空调设成26度" → 温度=26
    parsed = parse_control_command("空调设成26度")
    assert parsed is not None
    assert parsed.device == "空调"
    assert parsed.action == "set_prop"
    assert parsed.prop == "温度"
    assert parsed.value == 26


def test_parse_mode_word_at_device_prefix():
    # 设备名以模式词开头："烘干机温度50度" 应切出设备"烘干机"而非"烘干机温度"
    # （"烘干"是模式词，偏移 0 处的匹配须被忽略）
    parsed = parse_control_command("烘干机温度50度")
    assert parsed is not None
    assert parsed.device == "烘干机"
    assert parsed.action == "set_prop"
    assert parsed.prop == "温度"
    assert parsed.value == 50


def test_parse_ac_temperature_no_verb():
    # "空调26度"：单字调分界不得命中"空调"里的"调"，否则切成设备"空"
    parsed = parse_control_command("空调26度")
    assert parsed is not None
    assert parsed.device == "空调"
    assert parsed.prop == "温度"
    assert parsed.value == 26


def test_parse_ac_bare_tiao_mode():
    # "空调调制冷"：裸"调"前缀应命中模式分支，而非把"冷"当相对降档
    parsed = parse_control_command("空调调制冷")
    assert parsed is not None
    assert parsed.device == "空调"
    assert parsed.action == "set_prop"
    assert parsed.prop == "模式"
    assert parsed.value == "制冷"


def test_parse_ac_bare_tiao_adjust():
    # "空调调亮一点"：裸"调"+修饰词 → 相对调亮
    parsed = parse_control_command("空调调亮一点")
    assert parsed is not None
    assert parsed.device == "空调"
    assert parsed.action == "adjust_prop"
    assert parsed.prop == "亮度"
    assert parsed.direction == 1


def test_parse_action_verb_prefix_not_switch():
    # "开始扫地" 不应被切成开关 "开"+"始扫地"
    assert parse_control_command("开始扫地") is None
