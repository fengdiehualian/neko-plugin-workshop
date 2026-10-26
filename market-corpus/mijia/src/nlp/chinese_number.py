"""中文数字 → 阿拉伯数字。

支持口语变体：两=二=2；支持 十/百/千 组合（二十三→23）。
"""

import re
from typing import Optional

_CN_DIGITS = {
    "零": 0, "〇": 0,
    "一": 1, "二": 2, "两": 2,
    "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9,
}
_CN_UNITS = {"十": 10, "百": 100, "千": 1000}

CN_NUM_RE = re.compile(r"[零〇一二两三四五六七八九十百千]+")


def chinese_to_int(text: str) -> Optional[int]:
    """把中文数字字符串转成 int；含非数字字符时返回 None。"""
    s = (text or "").strip()
    if not s:
        return None
    total = 0
    cur = 0
    for ch in s:
        if ch in _CN_DIGITS:
            cur = _CN_DIGITS[ch]
        elif ch in _CN_UNITS:
            unit = _CN_UNITS[ch]
            if cur == 0:
                cur = 1
            cur *= unit
            total += cur
            cur = 0
        else:
            return None
    return total + cur
