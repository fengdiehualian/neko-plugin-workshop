"""数值抽取 + 相对量计算 + 钳值。

从 MijiaPlugin（plugin/plugins/mijia/__init__.py）原 `_parse_control_command`
的数值正则与 smart_control 内 adjust_prop 分支的目标值计算逻辑搬出，不改规则。
"""

import re
from typing import Any, Optional

from .chinese_number import chinese_to_int
from .intent_terms import PROP_TERMS, SPLIT_VERBS

# 中文数字（排除量词："一点/一些/一下" 里的"一"不是数值）
_CN_NUM = r"[零〇一二两三四五六七八九十百千]+(?!点|些|下)"
# 数字 + 可选单位（档/度/% 等，用于属性推断消除"2档→亮度"歧义）
_NUMBER_WITH_UNIT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(%|度|℃|°|档|档位)?")
# 中文数字 + 可选单位
_CN_NUM_UNIT_RE = re.compile(r"(" + _CN_NUM + r")\s*(%|度|℃|°|档|档位)?")
# 相对量 delta 提取
_DELTA_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(%|度|℃)?")
_CN_DELTA_RE = re.compile(r"(" + _CN_NUM + r")\s*(%|度|℃)?")
# 中文后紧跟数字的分界（原纯数字分界正则）
CN_DIGIT_BOUNDARY_RE = re.compile(r"(?<=[一-鿿])(\d)")


def extract_number(text: str) -> Optional[tuple[Any, str]]:
    """从文本中抽取第一个 (数值, 单位)；支持阿拉伯数字与中文数字。

    含小数按 float，否则按 int。
    """
    m = _NUMBER_WITH_UNIT_RE.search(text)
    if m:
        num_str = m.group(1)
        unit = m.group(2) or ""
        value = float(num_str) if "." in num_str else int(num_str)
        return value, unit
    m = _CN_NUM_UNIT_RE.search(text)
    if m:
        n = chinese_to_int(m.group(1))
        if n is not None:
            return n, m.group(2) or ""
    return None


def infer_prop_from_unit(unit: str, value: Any) -> Optional[str]:
    """无属性名时按单位推断属性：度/℃/°→温度；档/档位→档位；% 或 0-100 整数→亮度。"""
    if unit in ("度", "℃", "°"):
        return "温度"
    if unit in ("档", "档位"):
        return "档位"
    if unit == "%" or (isinstance(value, int) and 0 <= value <= 100):
        return "亮度"
    return None


def parse_prop_value(intent: str) -> Optional[tuple[Optional[str], Any, str]]:
    """解析"属性+数值"意图，返回 (prop_name, value, unit)。

    可选设置动词前缀 + 可选属性词 + 数字（阿拉伯或中文） + 可选单位。
    """
    m = re.search(
        r"(?:" + SPLIT_VERBS + r")?\s*(" + PROP_TERMS + r")?"
        r"(\d+(?:\.\d+)?|" + _CN_NUM + r")\s*(%|度|℃|°|档|档位)?",
        intent,
    )
    if not m:
        return None
    prop_name = m.group(1)
    num_str = m.group(2)
    unit = m.group(3) or ""
    if num_str.isdigit():
        value = float(num_str) if "." in num_str else int(num_str)
    else:
        n = chinese_to_int(num_str)
        if n is None:
            return None
        value = n
    return prop_name, value, unit


def parse_delta(text: str) -> Optional[float]:
    """从文本中提取相对调整的 delta 数值；支持中文数字（如"调低两度"→2）。"""
    m = _DELTA_RE.search(text)
    if m:
        return float(m.group(1))
    m = _CN_DELTA_RE.search(text)
    if m:
        n = chinese_to_int(m.group(1))
        if n is not None:
            return float(n)
    return None


def resolve_adjust_target(
    cur_value: float, direction: int, delta: Optional[float], value_range: list
) -> float:
    """计算相对调整的目标值（原 adjust_prop 分支逻辑）。

    - 显式 delta：cur + direction * delta
    - 无 delta：默认步长为 max(step, 范围*10%)
    - 结果 clamp 到 [v_min, v_max]，并按 step 对齐（step>1 时）
    """
    v_min = value_range[0] if len(value_range) >= 1 else 0
    v_max = value_range[1] if len(value_range) >= 2 else 100
    step = value_range[2] if len(value_range) >= 3 else 1

    if delta is not None:
        target = cur_value + direction * delta
    else:
        range_size = v_max - v_min
        default_step = max(step, range_size * 0.1)
        target = cur_value + direction * default_step

    target = max(v_min, min(v_max, target))
    if step > 1:
        target = round((target - v_min) / step) * step + v_min
        # 对齐可能越界（如 [0,100,60] 下 t=100 → round(100/60)*60=120），重新钳回
        target = max(v_min, min(v_max, target))
    return target
