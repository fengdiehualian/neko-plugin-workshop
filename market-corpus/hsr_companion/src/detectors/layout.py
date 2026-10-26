"""Conservative Star Rail page recognition using OCR layout anchors.

Page classification refines meaning, but does not gate database-validated name
reading. Unknown pages can expose printed names without claiming actor identity.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import asdict, is_dataclass
from typing import Any, Iterable

from ..data import normalize_lookup_text
from .exploration import exploration_controls
from .names import read_name_label
from .page_catalog import COMMUNITY_PAGE_RULES

_LEVEL_SUFFIX = re.compile(
    r"^(.+?)\s*(?:(?:l\s*v\s*\.?|等级\s*[:：]?)\s*(?:\d{1,3}(?:\s*/\s*\d{1,3})?)?|\d{1,3}\s*级)\s*$",
    re.IGNORECASE,
)


def character_label(text: str) -> tuple[str, bool]:
    """Remove only a complete level suffix, never arbitrary surrounding prose."""
    value = unicodedata.normalize("NFKC", str(text or "")).strip()
    match = _LEVEL_SUFFIX.fullmatch(value)
    return (match.group(1).strip(), True) if match else (value, False)


PAGE_RULES: tuple[dict[str, Any], ...] = (
    {
        "page_type": "relic_detail",
        "scene": "menu",
        "label": "遗器详情与替换",
        "strong": ("遗器详情", "遗器替换"),
        "anchors": ("套装效果", "重掷", "卸下", "强化"),
        "minimum": 3,
    },
    {
        "page_type": "inventory",
        "scene": "menu",
        "label": "背包与装备",
        "strong": ("背包",),
        "anchors": ("穿戴者", "已装备", "筛选"),
        "minimum": 3,
    },
    {
        "page_type": "archive",
        "scene": "menu",
        "label": "角色图鉴",
        "strong": ("角色图鉴",),
        "anchors": ("角色档案", "资料库"),
        "minimum": 2,
    },
    {
        "page_type": "trial",
        "scene": "menu",
        "label": "角色试用",
        "strong": ("角色试用",),
        "anchors": ("试用关卡", "试用奖励"),
        "minimum": 2,
    },
    {
        "page_type": "support_list",
        "scene": "menu",
        "label": "支援角色列表",
        "strong": ("支援列表",),
        "anchors": ("支援角色", "刷新列表"),
        "minimum": 2,
    },
    {
        "page_type": "assignment",
        "scene": "menu",
        "label": "委托派遣",
        "strong": ("委托派遣",),
        "anchors": ("委托奖励", "派遣角色"),
        "minimum": 2,
    },
    {
        "page_type": "exploration_hud",
        "scene": "exploration",
        "label": "探索与跑图",
        "strong": ("切换角色",),
        "anchors": ("任务追踪", "当前任务", "普通攻击", "秘技", "距离目标"),
        "minimum": 2,
    },
    {
        "page_type": "activity_menu",
        "scene": "menu",
        "label": "活动与玩法菜单",
        "strong": ("活动札记", "旅情事记", "角色选择"),
        "anchors": ("活动说明", "奖励指南", "常时传略", "角色列表"),
        "minimum": 2,
    },
    {
        "page_type": "skill_detail",
        "scene": "menu",
        "label": "技能与行迹详情",
        "strong": ("技能详情", "行迹详情"),
        "anchors": ("简略描述", "详细描述", "技能升级", "已达最大等级", "等级"),
        "minimum": 2,
    },
    {
        "page_type": "character_detail",
        "scene": "menu",
        "label": "角色详情",
        "strong": ("角色详情", "角色光锥", "角色遗器"),
        "anchors": ("光锥", "遗器", "行迹", "星魂", "属性"),
        "minimum": 2,
    },
    {
        "page_type": "challenge_setup",
        "scene": "team_setup",
        "label": "忘却之庭挑战准备",
        "strong": ("忘却之庭",),
        "anchors": ("挑战目标", "预设编队", "角色列表"),
        "minimum": 2,
        "heading_required": True,
        "required_anchors": 2,
    },
    {
        "page_type": "challenge_setup",
        "scene": "team_setup",
        "label": "副本挑战准备",
        "strong": (),
        "anchors": ("可遭遇", "快捷补充拓力", "挑战次数", "挑战"),
        "minimum": 2,
    },
    {
        "page_type": "team_setup",
        "scene": "team_setup",
        "label": "队伍编成",
        "strong": ("队伍编成", "快速编队", "阵容推荐提示"),
        "anchors": ("开始挑战", "支援角色", "出战", "队伍配置", "常用队伍"),
        "minimum": 1,
    },
    {
        "page_type": "combat",
        "scene": "combat",
        "label": "回合制战斗",
        "strong": ("战技点", "我方回合", "敌方回合", "我方行动中", "敌方行动中"),
        "anchors": ("终结技", "弱点击破", "行动序列", "剩余轮次", "剩余行动值"),
        "minimum": 2,
    },
    {
        "page_type": "story_dialogue",
        "scene": "story",
        "label": "剧情对话",
        "strong": ("对话记录", "选择你的回答"),
        "anchors": ("自动播放", "跳过剧情", "点击继续"),
        "minimum": 1,
    },
    {
        "page_type": "warp",
        "scene": "warp",
        "label": "跃迁",
        "strong": ("角色活动跃迁", "光锥活动跃迁", "跃迁记录", "联动跃迁"),
        "anchors": ("星轨专票", "跃迁", "十连跃迁"),
        "minimum": 1,
    },
    {
        "page_type": "reward",
        "scene": "reward",
        "label": "结算与奖励",
        "strong": ("挑战成功", "通关奖励", "获得角色"),
        "anchors": ("获得物品", "战利品", "领取奖励"),
        "minimum": 2,
    },
    {
        "page_type": "map_or_quest",
        "scene": "exploration",
        "label": "地图与任务",
        "strong": ("任务追踪", "当前任务"),
        "anchors": ("导航", "距离目标", "开启导航", "传送"),
        "minimum": 1,
    },
    {
        "page_type": "loading",
        "scene": "loading",
        "label": "登录与加载",
        "strong": ("正在加载", "点击进入"),
        "anchors": ("列车跃迁中", "数据加载"),
        "minimum": 1,
    },
) + COMMUNITY_PAGE_RULES


def _box_dict(box: Any) -> dict[str, Any]:
    if is_dataclass(box):
        value = asdict(box)
    elif isinstance(box, dict):
        value = dict(box)
    else:
        value = {key: getattr(box, key, None) for key in ("text", "left", "top", "right", "bottom", "score")}
    return {
        "text": str(value.get("text") or "").strip(),
        "left": float(value.get("left") or 0.0),
        "top": float(value.get("top") or 0.0),
        "right": float(value.get("right") or 0.0),
        "bottom": float(value.get("bottom") or 0.0),
        "score": float(value.get("score") or 0.0),
        "control_only": value.get("control_only") is True,
        "navigation_prefix_verified": value.get("navigation_prefix_verified") is True,
        "team_slot_verified": value.get("team_slot_verified") is True,
        "name_validation": dict(value.get("name_validation") or {}),
    }


def classify_supported_page(
    text: str,
    boxes: Iterable[Any],
    *,
    width: int,
    height: int,
) -> dict[str, Any]:
    raw_boxes = list(boxes)
    materialized = [_box_dict(box) for box in raw_boxes
                    if not isinstance(box, dict) or not box.get("literal_unconfirmed")]
    if any(isinstance(box, dict) and box.get("literal_unconfirmed") for box in raw_boxes):
        text = "\n".join(box["text"] for box in materialized)
    compact = normalize_lookup_text(text)
    # Compatibility for text-only callers. Actual OCR text is assembled from
    # exactly these boxes, so its prose must not become a UI anchor.
    text_only_context = compact != normalize_lookup_text("".join(box["text"] for box in materialized))

    def ui_term(term: str, rule: dict[str, Any]) -> bool:
        target = normalize_lookup_text(term)
        terms = {normalize_lookup_text(t) for t in (*rule["strong"], *rule["anchors"])}
        for box in materialized:
            if box["score"] < .65:
                continue
            value = normalize_lookup_text(box["text"])
            if value == target:
                return True
            if value.startswith(target) and re.fullmatch(r"[0-9ivx/]+", value[len(target):]):
                return True  # A control can carry its numeric value.
            if rule["page_type"] == "warp" and value.endswith(target):
                prefix = value[:-len(target)]
                if re.fullmatch(r"[a-z0-9]{1,20}", prefix) and box["top"] < height * .32:
                    return True  # E.g. a collab title, not arbitrary prose.
            hits = {t for t in terms if t in value}
            independent = {t for t in hits if not any(t != other and t in other for other in hits)}
            remaining = value
            for hit in sorted(hits, key=len, reverse=True):
                remaining = remaining.replace(hit, "")
            remaining = re.sub(r"(?:已启用|已开启|已关闭|启用|开启|关闭|显示|返回|取消|[0-9ivx/])", "", remaining)
            if target in hits and len(independent) >= 2 and not remaining:
                return True
        return text_only_context and target in compact

    def heading_term(term: str) -> bool:
        hits = [box for box in materialized if normalize_lookup_text(term) in normalize_lookup_text(box["text"])]
        # Geometry is authoritative when the actual OCR supplies it. Text-only
        # callers remain compatible, but a footer action is not a page title.
        if not hits:
            return normalize_lookup_text(term) in compact
        return any(box["score"] >= 0.65 and (box["top"] + box["bottom"]) / 2 <= height * 0.32 for box in hits)

    candidates: list[tuple[int, dict[str, Any], list[str]]] = []
    for rule in PAGE_RULES:
        strong = [term for term in rule["strong"] if ui_term(term, rule)]
        if rule.get("heading_required") or rule["page_type"] in {"trial", "archive", "inventory", "assignment", "support_list", "relic_detail", "character_detail"}:
            strong = [term for term in strong if heading_term(term)]
        anchors = [term for term in rule["anchors"] if ui_term(term, rule)]
        if rule.get("heading_required"):
            # Some menus repeat their own title in the control vocabulary.
            # One OCR title must not count as two independent UI proofs.
            anchors = [term for term in anchors if normalize_lookup_text(term) not in {
                normalize_lookup_text(title) for title in strong}]
        if rule.get("heading_required") and (not strong or len(anchors) < rule.get("required_anchors", 1)):
            continue
        if rule["page_type"] == "warp" and ui_term("跃迁", rule) and heading_term("跃迁"):
            strong.append("跃迁页标题")
        if not strong and len(anchors) < int(rule["minimum"]):
            continue
        score = len(strong) * 4 + len(anchors)
        candidates.append((score, rule, strong + anchors))
    if not candidates:
        if exploration_controls(materialized, width=width, height=height):
            return {
                "page_type": "exploration_hud", "scene": "exploration", "label": "探索与任务追踪",
                "confidence": "high", "supported": True, "image_size": [int(width), int(height)],
                "evidence": ["探索 HUD 控件组合", "左侧任务导航"],
                "classifier": "exploration_status_navigation_layout",
            }
        return {
            "page_type": "unknown",
            "label": "暂不支持的画面",
            "scene": "unknown",
            "confidence": "low",
            "evidence": [],
            "classifier": "hsr_layout_ocr_v1",
            "supported": False,
            "image_size": [int(width), int(height)],
        }
    candidates.sort(key=lambda value: -value[0])
    score, rule, hits = candidates[0]
    return {
        "page_type": rule["page_type"],
        "label": rule["label"],
        "scene": rule["scene"],
        "confidence": "high" if score >= 4 or len(hits) >= 2 else "mixed",
        "evidence": [f"界面锚点：{term}" for term in hits[:6]],
        "classifier": "hsr_layout_ocr_v1",
        "supported": True,
        "image_size": [int(width), int(height)],
    }


def scene_control_regions(page: dict, boxes: Iterable[Any], *, width: int, height: int) -> list[dict]:
    """Keep same-frame UI geometry separate from actor/content geometry."""
    materialized = [_box_dict(box) for box in boxes]
    page_type = page.get("page_type")
    terms = {normalize_lookup_text(term) for rule in PAGE_RULES if rule["page_type"] == page_type
             for term in (*rule["strong"], *rule["anchors"])}
    if page_type == "warp":
        terms.add(normalize_lookup_text("跃迁"))
    controls = exploration_controls(materialized, width=width, height=height) if page_type == "exploration_hud" else []
    result = []
    for box in materialized:
        text = normalize_lookup_text(box["text"])
        if (box["score"] < .65 or not (box in controls or text in terms)
                or not (0 <= box["left"] < box["right"] <= width and 0 <= box["top"] < box["bottom"] <= height)):
            continue
        # Classification already consumed this >=.65 anchor. Retain its
        # literal for INTERNAL new-frame verification, not public visible text.
        # The verifier still requires two >=.85 exact reads before accepting
        # changed pixels; losing the string makes that verification impossible.
        result.append({"text": box["text"], "control": True,
                       "region": {key: round(float(box[key]) / limit, 5) for key, limit in (
            ("left", width), ("right", width), ("top", height), ("bottom", height))}})
    return result


def select_proved_equipment_controls(page, controls, proved, *, width, height):
    """A redundant tab is not mandatory when a proved UI set still classifies.

    Reuse the SAME classifier and exact literals/positions, not 'any anchor'.
    Failed/absent controls never enter the returned guard. Other page families
    keep their existing all-control policy; field/object proofs are separate.
    """
    admitted = [p for p in controls if proved(p)]
    if len(admitted) == len(controls):
        return admitted
    if page.get("page_type") not in {"character_detail", "relic_detail"}:
        return []
    boxes = [{"text": p.get("text", ""), "score": 1.,
              **{k: p["region"][k] * (width if k in {"left", "right"} else height)
                 for k in ("left", "top", "right", "bottom")}} for p in admitted if p.get("region")]
    classified = classify_supported_page("\n".join(b["text"] for b in boxes), boxes, width=width, height=height)
    return admitted if classified.get("page_type") == page.get("page_type") else []


def _box_allowed(page_type: str, box: dict[str, Any], *, width: int, height: int) -> bool:
    if box["score"] < 0.50 or not box["text"]:
        return False
    center_x = (box["left"] + box["right"]) / 2.0
    center_y = (box["top"] + box["bottom"]) / 2.0
    if page_type in {"character_detail", "skill_detail"}:
        return center_y <= height * 0.35 or (center_x <= width * 0.35 and center_y <= height * 0.55)
    if page_type == "story_dialogue":
        return center_x <= width * 0.65 and center_y >= height * 0.42
    if page_type in {"team_setup", "warp", "reward"}:
        return True
    if page_type == "combat":
        return center_y >= height * 0.55 or (center_x >= width * 0.72 and center_y <= height * 0.78)
    if page_type in {"exploration_hud", "map_or_quest", "unknown"}:
        return center_x >= width * 0.72 and height * 0.08 <= center_y <= height * 0.80
    return False


def detect_characters_in_page(
    page: dict[str, Any],
    boxes: Iterable[Any],
    catalog: Any,
    *,
    width: int,
    height: int,
) -> list[dict[str, Any]]:
    page_type = str(page.get("page_type") or "unknown")
    materialized = [_box_dict(box) for box in boxes]
    found: list[dict[str, Any]] = []
    for box in materialized:
        if box["score"] < 0.50 or not box["text"]:
            continue
        cleaned, has_level = character_label(box["text"])
        # Full registered names win over suffix heuristics. For example,
        # 银狼LV.999 is its own database entity, not 银狼 at level 999.
        exact_record = catalog.resolve_character(box["text"])
        suffix_removed = has_level
        if exact_record is not None:
            cleaned = box["text"]
            suffix_removed = False
        # A name is readable anywhere; calling it a HUD teammate needs stronger
        # page or immediately adjacent level evidence.
        adjacent_level = any(
            other is not box
            and other["score"] >= 0.50
            and re.fullmatch(r"(?:lv\.?|等级)\s*\d{0,3}", unicodedata.normalize("NFKC", other["text"]), re.IGNORECASE)
            and abs((other["top"] + other["bottom"] - box["top"] - box["bottom"]) / 2)
            <= max(12, box["bottom"] - box["top"])
            and 0 <= other["left"] - box["right"] <= width * 0.06
            for other in materialized
        )
        records, _ = read_name_label(box["text"], catalog)
        if not records:
            records, _ = read_name_label(cleaned, catalog)
        for record in records:
            record_id = str(record.get("id") or "")
            if not record_id:
                continue
            label = normalize_lookup_text(cleaned)
            exact = normalize_lookup_text(record.get("name")) == label
            scope = "screen_label"
            # Page/position refine meaning, never suppress basic name reading.
            # One merged OCR row is not proof of individual actor positions.
            if len(records) == 1 and _box_allowed(page_type, box, width=width, height=height):
                scope = {
                    "story_dialogue": "dialogue_speaker",
                    "character_detail": "detail_subject",
                    "skill_detail": "detail_subject",
                    "team_setup": "team_selection",
                    "warp": "banner_label",
                    "reward": "reward_label",
                    "combat": "hud_label",
                    "exploration_hud": "hud_label",
                    "map_or_quest": "hud_label",
                }.get(
                    page_type,
                    "hud_label" if has_level or adjacent_level or box.get("team_slot_verified") else "screen_label",
                )
            elif page_type == "team_setup":
                scope = "team_selection"
            found.append(
                {
                    "entity_id": record_id,
                    "name": str(record.get("name") or ""),
                    "evidence": f"{page_type}_name_region"
                    if scope != "screen_label"
                    else "database_validated_screen_label",
                    "identity_scope": scope,
                    "region_precision": "label" if len(records) == 1 else "merged_ocr_line",
                    "raw_label": box["text"],
                    "label_normalized": cleaned,
                    "level_suffix_removed": suffix_removed,
                    "confidence": "high" if exact and box["score"] >= 0.70 else "mixed",
                    "ocr_score": round(float(box["score"]), 3),
                    "name_validation": dict(box["name_validation"])
                    if box["name_validation"].get("entity_id") == record_id and len(records) == 1 else {},
                    "region": {
                        "left": round(box["left"] / max(width, 1), 4),
                        "top": round(box["top"] / max(height, 1), 4),
                        "right": round(box["right"] / max(width, 1), 4),
                        "bottom": round(box["bottom"] / max(height, 1), 4),
                    },
                }
            )
    found.sort(
        key=lambda item: (
            item["region_precision"] != "label",
            not bool(item.get("name_validation")),
            item["confidence"] != "high",
            -float(item.get("ocr_score") or 0.0),
            item["entity_id"],
        )
    )
    # A merged native line can precede an accurately segmented retry. Do not
    # let iteration order discard that later, more precise name geometry.
    unique = {}
    for item in found:
        unique.setdefault(item["entity_id"], item)
    return list(unique.values())[:32]


def analyze_layout(
    text: str,
    boxes: Iterable[Any],
    catalog: Any,
    *,
    width: int,
    height: int,
) -> dict[str, Any]:
    materialized = [_box_dict(box) for box in boxes]
    page = classify_supported_page(text, materialized, width=width, height=height)
    characters = detect_characters_in_page(page, materialized, catalog, width=width, height=height)
    if page["page_type"] == "unknown" and any(
        item["identity_scope"] == "hud_label"
        and item["region"]["left"] >= 0.72
        and 0.08 <= (item["region"]["top"] + item["region"]["bottom"]) / 2 <= 0.80
        for item in characters
    ):
        # The ordinary overworld often shows task prose, not the literal words
        # "任务追踪"/"普通攻击". Require BOTH a database-confirmed level/name
        # in the right HUD and navigation/task text in the left task region.
        # A name alone, dialogue prose or a menu mention is insufficient.
        quest_boxes = [
            box
            for box in materialized
            if box["score"] >= 0.7
            and box["right"] <= width * 0.55
            and height * 0.08 <= (box["top"] + box["bottom"]) / 2 <= height * 0.55
            and re.search(r"(?:请前往|前往.+(?:城区|区域|寻找|调查)|距离目标|追踪目标|开启导航)", box["text"])
        ]
        if quest_boxes:
            page = {
                **page,
                "page_type": "exploration_hud",
                "scene": "exploration",
                "label": "探索与任务追踪",
                "confidence": "mixed",
                "supported": True,
                "evidence": ["右侧等级角色名已匹配数据库", "左侧任务区域存在导航文字"],
            }
            # Once navigation + a level/name confirm exploration, refine the
            # remaining names' meaning without suppressing generic labels.
            characters = detect_characters_in_page(page, materialized, catalog, width=width, height=height)
    return {
        "page": page,
        "scene": {
            "primary_state": page["scene"],
            "substate": page["page_type"],
            "confidence": page["confidence"],
            "evidence": list(page["evidence"]),
            "classifier": page["classifier"],
            "verified": bool(page["supported"]),
        },
        "characters": characters,
        "recognition": {
            "method": "ocr_name_label",
            "scene_required": False,
            "portrait_model": False,
            "ocr_box_count": len(materialized),
            "confirmed_label_count": len(characters),
            "reason": "labels_confirmed" if characters else "no_validated_name_label",
        },
        "boxes": materialized,
    }


__all__ = [
    "PAGE_RULES",
    "analyze_layout",
    "classify_supported_page",
    "detect_characters_in_page",
]
