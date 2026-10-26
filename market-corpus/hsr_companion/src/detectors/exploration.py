"""Independent overworld controls, including HUDs without keyboard hints."""
from __future__ import annotations

import re


def generic_navigation(text):
    """A navigation button, optionally with a merged distance, not a place.

    Short leading icon glyphs establish only the UI control's presence. Never
    strip these glyphs to publish destination prose.
    """
    return bool(re.fullmatch(r".{0,4}前往任务目标(?:\s*\d+(?:\.\d+)?\s*(?:千米|米|km|m))?", text))


def _row_groups(boxes):
    """Group physical rows before resolving their possibly conflicting text."""
    groups = []
    for box in boxes:
        h = box["bottom"] - box["top"]
        group = next((g for g in groups if abs((g[0]["top"] + g[0]["bottom"] - box["top"] - box["bottom"]) / 2) <= h * .6
                      and abs(g[0]["left"] - box["left"]) <= h), None)
        if group is None:
            groups.append([box])
        else:
            group.append(box)
    return groups


def distinct_rows(boxes):
    """Keep a clearly stronger OCR retry; conflicting peers remain unknown."""
    groups = _row_groups(boxes)
    result = []
    for group in groups:
        group.sort(key=lambda b: (-b["score"], -len(b["text"])))
        best = group[0]
        def prefix_retry(other):
            # One same-frame physical line, not a semantic guess or a merge.
            # Only a high-confidence LONGER winner can subsume a shorter,
            # nearly contained proper-prefix retry. Conflicting peers stay.
            h = min(best["bottom"] - best["top"], other["bottom"] - other["top"])
            return (best["score"] >= .9 and other["score"] >= .9 and len(other["text"]) >= 8
                    and best["text"].startswith(other["text"]) and len(other["text"]) >= len(best["text"]) * .8
                    and abs(best["left"] - other["left"]) <= h * .15
                    and abs((best["top"] + best["bottom"] - other["top"] - other["bottom"]) / 2) <= h * .15
                    and other["right"] <= best["right"] + h * .1
                    and other["right"] - other["left"] >= (best["right"] - best["left"]) * .8)

        rivals = [b for b in group[1:] if b["text"] != best["text"] and not prefix_retry(b)]
        if not rivals or best["score"] - rivals[0]["score"] >= .05:
            result.append(best)
    return sorted(result, key=lambda b: b["top"])


def navigation_boxes(boxes, *, width, height):
    """Navigation presence is independent from whether its destination agrees.

    A HUD icon may be merged into up to four leading OCR glyphs. This only
    establishes control presence, not a cleaned destination or task value.
    """
    return [b for b in boxes if not b.get("literal_unconfirmed") and b["score"] >= .85 and b["right"] <= width * .55
            and height * .08 <= b["top"] <= height * .55
            and ((b.get("control_only") and b.get("navigation_prefix_verified")
                  and b["text"] in {"请前往", "距离目标", "追踪目标", "开启导航"})
                 or generic_navigation(b["text"])
                 or re.match(r"^.{0,4}(?:请前往|距离目标|追踪目标|开启导航)\S", b["text"]))]


def exploration_controls(boxes, *, width, height):
    """Independent HUD evidence, including no-team social interiors."""
    return exploration_control_evidence(boxes, width=width, height=height)[0]


def exploration_control_evidence(boxes, *, width, height):
    """Same admission rules, with content-free first-failing-condition evidence."""
    status = [b for b in boxes if b["score"] >= .85 and b["text"] == "状态效果"
              and b["left"] >= width * .7 and height * .1 <= b["top"] <= height * .5]
    navigation = navigation_boxes(boxes, width=width, height=height)
    headings = location_headings(boxes, width=width, height=height)
    trace = {"status_count": len(status), "navigation_count": len(navigation), "heading_count": len(headings)}
    if not navigation:
        return [], {**trace, "condition": "navigation"}
    if status:
        # Scene controls do not depend on the location's text being readable.
        # An ambiguous heading revokes location, not the independent task HUD.
        return [status[0], *navigation[:8]], {**trace, "condition": "accepted"}
    # Social interiors may hide the entire combat/team HUD. Require a complete
    # left quest stack AND the bottom chat input control, not a location name
    # whitelist or a lone "Enter" elsewhere on the page.
    stacks = task_stacks(boxes, width=width, height=height, layout_only=True)
    chat = [b for b in boxes if b["score"] >= .9 and b["text"].strip() == "Enter"
            and b["right"] <= width * .3 and b["top"] >= height * .8]
    trace.update(stack_count=len(stacks), chat_count=len(chat))
    if len(stacks) == 1 and chat:
        title, objective, nav = stacks[0]
        # The independent quest stack + navigation + chat control establish
        # this HUD, not the location value. Missing/ambiguous corner text must
        # only withhold current_location; its own .9/unique-heading rule stays.
        # Never publish layout-only row text as a confirmed quest value.
        return [nav, chat[0]], {**trace, "condition": "accepted"}
    return [], {**trace, "condition": "task_stack" if len(stacks) != 1 else "chat_control"}


def task_stacks(boxes, *, width, height, layout_only=False):
    """Bind title/objective to one navigation control by relative row geometry.

    Conflicting or missing rows must not promote a title to an objective. A
    dirty navigation prefix can prove presence, but not bind task values.
    """
    navs = [b for b in navigation_boxes(boxes, width=width, height=height)
            if b["text"].startswith("请前往") or generic_navigation(b["text"])]
    if not navs or max(b["top"] for b in navs) - min(b["top"] for b in navs) > min(b["bottom"] - b["top"] for b in navs):
        return []
    nav = min(navs, key=lambda b: b["bottom"] - b["top"])
    h = nav["bottom"] - nav["top"]
    def precedes(b):
        row_h = b["bottom"] - b["top"]
        baseline_gap = (nav["top"] + nav["bottom"] - b["top"] - b["bottom"]) / 2
        # Detector boxes may include shadows/two lines. Preserve ordered,
        # distinct baselines with at most half a shorter row of overlap.
        return (baseline_gap > min(row_h, h) * .5
                and max(0, b["bottom"] - nav["top"]) <= min(row_h, h) * .5)
    candidates = [b for b in boxes if b not in navs and not b.get("control_only")
                           and b["score"] >= .85 and re.search(r"[\u4e00-\u9fff]", b["text"])
                           and b["top"] >= height * .08 and b["right"] <= width * .55
                           and precedes(b) and nav["top"] - b["bottom"] <= h * 4
                           and abs(b["left"] - nav["left"]) <= h * 2
                           and b["text"] not in {"任务追踪", "当前任务", "任务目标", "开启导航"}]
    groups = sorted(_row_groups(candidates), key=lambda g: min((b["top"] + b["bottom"]) / 2 for b in g))
    # Resolve the nearest two PHYSICAL rows. Do not skip an ambiguous/failed
    # row and promote an older header into its place.
    above = ([max(group, key=lambda b: b["score"]) for group in groups[-2:]] if layout_only else
             distinct_rows([b for group in groups[-2:] for b in group]))
    if len(above) < 2 or nav["top"] - above[-1]["bottom"] > h * 1.5:
        return []
    title, objective = above[-2:]
    if title.get("literal_unconfirmed") or objective.get("literal_unconfirmed"):
        return []
    if objective["top"] - title["bottom"] > h * 1.5:
        return []
    return [(title, objective, nav)]


def location_headings(boxes, *, width, height):
    # This region only acquires location meaning after independent overworld
    # evidence. A short name in this corner alone never establishes a scene.
    rows = distinct_rows([b for b in boxes if not b.get("literal_unconfirmed") and b["score"] >= .9 and b["right"] <= width * .3
                          and b["bottom"] <= height * .055
                          and re.fullmatch(r"[\u4e00-\u9fff·「」『』—\- ]{2,24}", b["text"])])
    # OCR engines may split one heading at decorative quotes/whitespace. Join
    # only touching/near-touching, baseline-aligned fragments. Separated names
    # and overlapping competing reads stay ambiguous, not arbitrarily chosen.
    joined = []
    for row in sorted(rows, key=lambda b: b["left"]):
        previous = joined[-1] if joined else None
        h = min(row["bottom"] - row["top"], previous["bottom"] - previous["top"]) if previous else 0
        gap = row["left"] - previous["right"] if previous else 0
        same_line = previous and abs((row["top"] + row["bottom"] - previous["top"] - previous["bottom"]) / 2) <= h * .35
        if same_line and -h * .2 <= gap <= h * .6 and len(previous["text"] + row["text"]) <= 24:
            joined[-1] = {
                "text": previous["text"] + row["text"], "score": min(previous["score"], row["score"]),
                "left": previous["left"], "right": row["right"],
                "top": min(previous["top"], row["top"]), "bottom": max(previous["bottom"], row["bottom"]),
            }
        else:
            joined.append(row)
    return sorted(joined, key=lambda b: (b["top"], b["left"]))
