"""Early scene evidence from native OCR tiles, never from old identities."""

from __future__ import annotations

from ..data import normalize_lookup_text
from .exploration import navigation_boxes
from .layout import PAGE_RULES, classify_supported_page, scene_control_regions
from .team import locate_team_rows


def exploration_control(box, *, width, height):
    """Exact exploration-only action plus ordinary action, in the HUD area."""
    return (float(box.get("score") or 0) >= .85 and box["left"] >= width * .70
            and box["top"] >= height * .65
            and normalize_lookup_text(box["text"]) in {"疾跑", "普攻", "秘技"})


def preview_regions(page, boxes, *, width, height):
    """Carry the same-capture control proof with its label, including merged UI.

    This does not classify a new page or borrow another frame's geometry.
    Called only AFTER the preview classifier has accepted this page.
    """
    result = scene_control_regions(page, boxes, width=width, height=height)
    terms = {normalize_lookup_text(t) for r in PAGE_RULES if r["page_type"] == page["page_type"]
             for t in (*r["strong"], *r["anchors"])}
    for b in boxes:
        if b.get("literal_unconfirmed") or b.get("score", 0) < .85:
            continue
        text = normalize_lookup_text(b["text"])
        hits = {t for t in terms if t in text}
        merged = len({t for t in hits if not any(t != other and t in other for other in hits)}) >= 2
        hud = (page["page_type"] == "exploration_hud" and
               (b in navigation_boxes(boxes, width=width, height=height)
                or exploration_control(b, width=width, height=height)
                or text in {"1", "2", "3", "4"} and b["left"] >= width * .955))
        if merged or hud:
            result.append({"text": b["text"], "control": True, "region": {
                k: round(float(b[k]) / limit, 5) for k, limit in (("left", width), ("right", width), ("top", height), ("bottom", height))}})
    return list({tuple(r["region"][k] for k in ("left", "top", "right", "bottom")): r for r in result}.values())


def scene_preview(boxes, *, width, height):
    # Only a complete, high-score title/control label is an early fact. A
    # mention in task prose or a footer button is not a page title. Unknown
    # and conflicting titles wait for full-frame analysis, rather than guessing.
    matches = set()
    control_pages = {"team_setup", "combat", "story_dialogue", "loading", "reward"}
    for box in boxes:
        if box.get("literal_unconfirmed"):
            continue
        if float(box.get("score") or 0) < .85:
            continue
        near_heading = (box["top"] + box["bottom"]) / 2 <= height * .24
        text = normalize_lookup_text(box["text"])
        for rule in PAGE_RULES:
            # These labels are scene-specific controls, not page titles. The
            # same exact label gate works anywhere on screen, including a
            # custom-named team page. Inventory/trial/archive footer buttons
            # remain excluded by the heading gate.
            if not near_heading and rule["page_type"] not in control_pages:
                continue
            terms = (*rule["strong"], "跃迁") if rule["page_type"] == "warp" else rule["strong"]
            exact = any(text == normalize_lookup_text(term) for term in terms)
            # OCR can merge a complete UI row (e.g. several footer controls).
            # Never accept one keyword inside prose: require two independent
            # registered UI anchors plus a strong control in that same row.
            hits = {normalize_lookup_text(term) for term in (*terms, *rule["anchors"])
                    if normalize_lookup_text(term) in text}
            independent = {term for term in hits if not any(term != other and term in other for other in hits)}
            merged_controls = (
                rule["page_type"] in control_pages and len(independent) >= 2
                and any(normalize_lookup_text(term) in text for term in terms)
            )
            if exact or merged_controls:
                matches.add(rule["page_type"])
    # Ordinary exploration may not print any page title. Numeric HUD slots
    # plus left-side quest navigation can establish its scene independently
    # of whether a single character name is readable or in the database.
    # Require ordered shortcuts plus independent navigation/action controls.
    if not matches:
        shortcuts = {str(b["text"]).strip() for b in boxes
                     if float(b.get("score") or 0) >= .85 and b["left"] >= width * .955
                     and height * .15 <= (b["top"] + b["bottom"]) / 2 <= height * .78}
        navigation = bool(navigation_boxes(boxes, width=width, height=height))
        actions = {normalize_lookup_text(b["text"]) for b in boxes if exploration_control(b, width=width, height=height)}
        action_hud = "疾跑" in actions and bool(actions & {"普攻", "秘技"})
        if len({"1", "2", "3", "4"} & shortcuts) >= 3 and (navigation or action_hud) and locate_team_rows(boxes, width=width, height=height):
            evidence = ["至少三个有序 HUD 快捷键", "左侧任务导航文字" if navigation else "探索操作控件组合"]
            return {
                "ok": True,
                "page": {"page_type": "exploration_hud", "scene": "exploration", "label": "探索与任务追踪",
                         "supported": True, "confidence": "high", "evidence": evidence},
                "scene": {"primary_state": "exploration", "substate": "exploration_hud", "confidence": "high",
                          "verified": True, "evidence": evidence,
                          "classifier": "current_hud_navigation_preview"},
                "characters": [],
                "recognition": {"phase": "scene_only", "text_reader": {"full_frame_covered": False},
                    "scene_regions": preview_regions({"page_type": "exploration_hud"}, boxes, width=width, height=height)},
            }
    if not matches:
        page = classify_supported_page("\n".join(box["text"] for box in boxes), boxes, width=width, height=height)
        if (page["page_type"] in {"challenge_setup", "skill_detail"}
                or page.get("classifier") == "exploration_status_navigation_layout") and page["confidence"] == "high":
            matches.add(page["page_type"])
    if len(matches) != 1:
        return None
    page = classify_supported_page("\n".join(box["text"] for box in boxes), boxes, width=width, height=height)
    if page["page_type"] not in matches or page["confidence"] != "high":
        return None
    return {
        "ok": True,
        "page": page,
        "scene": {
            "primary_state": page["scene"], "substate": page["page_type"],
            "confidence": "high", "verified": True,
            "evidence": page["evidence"], "classifier": "native_title_preview",
        },
        "characters": [],
        "recognition": {"phase": "scene_only", "text_reader": {"full_frame_covered": False},
            "scene_regions": preview_regions(page, boxes, width=width, height=height)},
    }
