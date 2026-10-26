"""Shared, typed relic LABEL semantics; never a number/name correction rule."""
import re
import unicodedata

STAT_LABELS = (
    "生命值", "攻击力", "防御力", "速度", "暴击率", "暴击伤害", "击破特攻",
    "效果命中", "效果抵抗", "能量恢复效率", "治疗量加成", "属性伤害提高",
    "物理属性伤害提高", "火属性伤害提高", "冰属性伤害提高", "雷属性伤害提高",
    "风属性伤害提高", "量子属性伤害提高", "虚数属性伤害提高",
)


def label_text(text):
    """One adjacent UI symbol only, and only before a registered stat label.

    Do not normalize arbitrary identifiers, numeric crops, repeated symbols,
    misspelled labels or suffixes. Raw OCR text remains the actual evidence.
    """
    value = unicodedata.normalize("NFKC", str(text or "")).strip()
    if value and unicodedata.category(value[0])[0] in {"P", "S"}:
        rest = value[1:].lstrip()
        if any(rest.startswith(label) for label in STAT_LABELS):
            return rest
    return value


def label_key(text):
    value = re.sub(r"\s+", "", label_text(text))
    return value if value in STAT_LABELS else ""
