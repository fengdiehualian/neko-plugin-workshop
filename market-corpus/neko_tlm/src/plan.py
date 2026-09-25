"""会话内的结构化 Minecraft 目标板状态。

本模块有意将计划功能保持轻量且限定在游戏范围内。N.E.K.O 宿主负责推理和
对话；插件只负责存储、渲染当前 Minecraft 目标板，并同步到 HUD 和上下文。
"""

import re
import time

MAX_TITLE_LENGTH = 80
MAX_STEP_LENGTH = 120
MAX_STEPS = 12


_STEP_PREFIX_RE = re.compile(r"^\s*(?:(?:[-*]\s*)|(?:\d+[.)、]\s*))")
_CHECKBOX_RE = re.compile(r"^\s*(?:\[([ xX])\]|([✓✔]))\s*")


def empty_plan():
    return {"title": "", "steps": [], "updated_at": 0}


def normalize_plan_state(state):
    if not isinstance(state, dict):
        return empty_plan()

    title = _clean_text(state.get("title", ""), MAX_TITLE_LENGTH)
    updated_at = state.get("updated_at", 0)
    try:
        updated_at = int(updated_at)
    except Exception:
        updated_at = 0

    steps = []
    for raw in state.get("steps") or []:
        if isinstance(raw, dict):
            text = _clean_text(raw.get("text", ""), MAX_STEP_LENGTH)
            done = bool(raw.get("done", False))
        else:
            text = _clean_text(raw, MAX_STEP_LENGTH)
            done = False
        if text:
            steps.append({"text": text, "done": done})
        if len(steps) >= MAX_STEPS:
            break

    if not title and not steps:
        updated_at = 0
    return {"title": title, "steps": steps, "updated_at": updated_at}


def plan_from_text(text):
    text = str(text or "").strip()
    if not text:
        return empty_plan()

    lines = [_clean_text(line, MAX_STEP_LENGTH) for line in text.splitlines()]
    lines = [line for line in lines if line]
    if not lines:
        return empty_plan()

    title = ""
    step_lines = lines
    if len(lines) > 1 and _looks_like_title(lines[0]):
        title = _clean_text(lines[0], MAX_TITLE_LENGTH)
        step_lines = lines[1:]

    steps = []
    for line in step_lines[:MAX_STEPS]:
        done, cleaned = _parse_step_line(line)
        if cleaned:
            steps.append({"text": cleaned, "done": done})

    if not title and not steps:
        title = _clean_text(lines[0], MAX_TITLE_LENGTH)
    return normalize_plan_state({"title": title, "steps": steps, "updated_at": int(time.time())})


def update_plan_state(current_state, *, plan=None, title=None, steps=None,
                      completed_steps=None, uncompleted_steps=None,
                      append_steps=None, clear=False):
    if clear:
        return empty_plan()

    if plan is not None:
        return plan_from_text(plan)

    state = normalize_plan_state(current_state)
    if title is not None:
        state["title"] = _clean_text(title, MAX_TITLE_LENGTH)

    if steps is not None:
        state["steps"] = _normalize_steps_argument(steps)

    if append_steps:
        state["steps"].extend(_normalize_steps_argument(append_steps))
        state["steps"] = state["steps"][:MAX_STEPS]

    _mark_steps(state["steps"], completed_steps, True)
    _mark_steps(state["steps"], uncompleted_steps, False)

    if not state["title"] and not state["steps"]:
        return empty_plan()

    state["updated_at"] = int(time.time())
    return normalize_plan_state(state)


def plan_to_text(state):
    state = normalize_plan_state(state)
    if not state["title"] and not state["steps"]:
        return ""

    lines = []
    if state["title"]:
        lines.append(state["title"])
    for idx, step in enumerate(state["steps"], 1):
        box = "[x]" if step.get("done") else "[ ]"
        lines.append(f"{idx}. {box} {step.get('text', '')}")
    return "\n".join(lines)


def plan_summary(state):
    state = normalize_plan_state(state)
    total = len(state["steps"])
    done = sum(1 for step in state["steps"] if step.get("done"))
    return {
        "title": state["title"],
        "total_steps": total,
        "completed_steps": done,
        "pending_steps": max(0, total - done),
        "plan": plan_to_text(state),
    }


def _normalize_steps_argument(raw_steps):
    steps = []
    if not isinstance(raw_steps, list):
        raw_steps = [raw_steps]
    for raw in raw_steps:
        if isinstance(raw, dict):
            text = _clean_text(raw.get("text", ""), MAX_STEP_LENGTH)
            done = bool(raw.get("done", False))
        else:
            done, text = _parse_step_line(str(raw or ""))
        if text:
            steps.append({"text": text, "done": done})
        if len(steps) >= MAX_STEPS:
            break
    return steps


def _mark_steps(steps, indexes, done):
    if indexes is None:
        return
    if not isinstance(indexes, list):
        indexes = [indexes]
    for raw_index in indexes:
        try:
            index = int(raw_index) - 1
        except Exception:
            continue
        if 0 <= index < len(steps):
            steps[index]["done"] = done


def _parse_step_line(line):
    line = _clean_text(line, MAX_STEP_LENGTH)
    done = False
    match = _CHECKBOX_RE.match(line)
    if match:
        token = (match.group(1) or match.group(2) or "").lower()
        done = token == "x" or token in ("✓", "✔")
        line = line[match.end():]
    line = _STEP_PREFIX_RE.sub("", line, count=1)
    match = _CHECKBOX_RE.match(line)
    if match:
        token = (match.group(1) or match.group(2) or "").lower()
        done = token == "x" or token in ("✓", "✔")
        line = line[match.end():]
    return done, _clean_text(line, MAX_STEP_LENGTH)


def _looks_like_title(line):
    stripped = str(line or "").strip()
    if stripped.endswith((":", "：")):
        return True
    if _CHECKBOX_RE.match(stripped):
        return False
    if _STEP_PREFIX_RE.sub("", stripped, count=1) != stripped:
        return False
    return any(keyword in stripped for keyword in ("目标", "计划", "今天", "今日", "当前"))


def _clean_text(value, limit):
    text = str(value or "").replace("\r", " ").replace("\n", " ").strip()
    while "  " in text:
        text = text.replace("  ", " ")
    return text[:limit]
