"""Page-independent, exact database name extraction from OCR labels.

Separate text recognition from identity semantics. A whole OCR row may contain
several names and UI fields; ordinary sentences are not character labels.
No edit-distance winner or voice homophone is used for visual recognition.
"""

from __future__ import annotations

import re
from typing import Any

from ..data import normalize_lookup_text

# Field vocabulary, not character names or page-specific coordinates. Covers
# label decorations shared by lists, inventory, trials, banners and profiles.
_FIELDS = re.compile(
    r"(?:毁灭|巡猎|智识|同谐|虚无|存护|丰饶|记忆|欢愉|"
    r"物理|火属性|冰属性|雷属性|风属性|量子|虚数|属性|命途|"
    r"等级|级|lv|max|生命值|生命|攻击力|防御力|速度|"
    r"角色|光锥|遗器|行迹|星魂|星级|详情|试用|支援|"
    r"已装备|装备中|穿戴者|装备者|已拥有|未拥有|未获得|"
    r"已获得|已解锁|未解锁|推荐|限定|概率提升|概率up|up|"
    r"[0-9]+|[/+%=★☆·•×→←|])",
    re.IGNORECASE,
)


def label_fields_only(text: str) -> bool:
    """A whole separate OCR line contains UI fields, not a person or prose."""
    value = normalize_lookup_text(text)
    return bool(value) and not _FIELDS.sub("", value).strip()


def read_name_label(text: str, catalog: Any) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Read complete names, longest first, and validate the remaining fields.

    The whole line's geometry is retained; substring offsets are NOT treated as
    accurate pixel coordinates. Unknown prose never becomes an on-screen actor.
    """
    value = normalize_lookup_text(text)
    if not value or len(value) > 300:
        return [], {"literal_names": 0, "prose_rejected": 0}
    exact = catalog.resolve_character(text)
    if exact:
        return [exact], {"literal_names": 1, "prose_rejected": 0}
    # match_characters_in_text deliberately protects single-character names in
    # prose. After removing field decorations, retry that exact-label case.
    candidates = catalog.match_characters_in_text(text)
    spans: list[tuple[int, int, dict[str, Any]]] = []
    for record in candidates:
        # Canonical spellings and explicitly vetted OCR aliases only. Player
        # nicknames and speech aliases are not evidence printed by the game.
        labels = sorted(
            {normalize_lookup_text(label) for label in [record["name"], *(record.get("ocr_aliases") or [])] if label},
            key=len,
            reverse=True,
        )
        for label in labels:
            for match in re.finditer(re.escape(label), value):
                start, end = match.span()
                if not any(start < other_end and end > other_start for other_start, other_end, _ in spans):
                    spans.append((start, end, record))
    # Prefer a complete long name (银狼LV.999), not its shorter database entry.
    spans.sort(key=lambda hit: hit[0])
    remaining = list(value)
    for start, end, _ in spans:
        remaining[start:end] = " " * (end - start)
    residual = _FIELDS.sub("", "".join(remaining)).strip()
    if not spans:
        decorated = catalog.resolve_character(_FIELDS.sub("", value))
        if decorated:
            return [decorated], {"literal_names": 1, "prose_rejected": 0}
    if not spans or residual:
        return [], {"literal_names": len(spans), "prose_rejected": int(bool(spans))}
    unique = {record["id"]: record for _, _, record in spans}
    return list(unique.values()), {"literal_names": len(unique), "prose_rejected": 0}
