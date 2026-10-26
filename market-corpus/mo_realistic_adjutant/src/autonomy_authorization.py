from __future__ import annotations

import re
from typing import Any


_NEGATIONS = (
    "不要", "别", "不许", "不允许", "禁止", "停止", "停下", "取消", "关闭",
    "撤销", "结束自主", "暂停自主", "别自主", "不要自主", "不能自主",
    "没有授权", "未授权", "并未授权", "并没有授权", "没有允许", "不授权",
)
_QUESTION_MARKERS = (
    "能不能", "能否", "可以吗", "可不可以", "是否可以", "会不会", "行不行",
    "怎么", "如何", "为什么", "是否支持",
)
_DIRECT_AUTONOMY = (
    "完全自主", "全自主", "自主作战", "自主对战", "自由作战", "自由发挥",
    "全权操作", "全权控制", "全权自由操作", "接管这局", "接管本局",
    "托管这局", "托管本局", "自动打完", "自己打完", "自己玩这局",
)
_AUTHORIZATION = (
    "授权", "允许你", "交给你", "由你", "你来", "不用问我", "无需询问",
    "不需要问我", "全权", "接管", "托管",
)
_AUTONOMY = (
    "自主", "自动", "自由操作", "自由行动", "自己操作", "自己决定", "独立作战",
)
_VICTORY = (
    "击败", "打败", "战胜", "消灭对手", "消灭敌人", "赢下", "拿下这局",
    "赢得这局", "取得胜利", "直到胜利", "打到赢", "推平对面", "摧毁对面",
)
_GAME_SCOPE = (
    "这局", "本局", "对局", "作战", "战斗", "对手", "对面", "敌人", "敌军",
    "基地", "部队", "心灵终结", "红警", "战场",
)


def _compact(text: str) -> str:
    return re.sub(r"[\s\u3000，。！？；：,.!?;:'\"“”‘’（）()【】\[\]<>《》]+", "", text).casefold()


def classify_autonomy_authorization(text: Any) -> dict[str, Any]:
    """Classify an explicit natural-language authorization conservatively."""
    original = str(text or "").strip()
    compact = _compact(original)
    if not compact:
        return {"authorized": False, "reason": "empty", "text": original}
    if any(token in compact for token in _NEGATIONS):
        return {"authorized": False, "reason": "negated", "text": original}
    if any(token in compact for token in _QUESTION_MARKERS):
        return {"authorized": False, "reason": "question", "text": original}

    direct = next((token for token in _DIRECT_AUTONOMY if token in compact), None)
    grant = next((token for token in _AUTHORIZATION if token in compact), None)
    autonomy = next((token for token in _AUTONOMY if token in compact), None)
    victory = next((token for token in _VICTORY if token in compact), None)
    game_scope = next((token for token in _GAME_SCOPE if token in compact), None)
    authorized = bool(
        game_scope
        and victory
        and (direct or (grant and autonomy))
    )
    return {
        "authorized": authorized,
        "reason": "explicit_autonomous_victory" if authorized else "insufficient_authorization",
        "text": original,
        "evidence": {
            "direct": direct,
            "grant": grant,
            "autonomy": autonomy,
            "victory": victory,
            "game_scope": game_scope,
        },
    }


def extract_user_text(value: Any, *, depth: int = 0) -> str:
    """Extract user text from SDK message kwargs or a user-context record."""
    if depth > 5 or value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (list, tuple)):
        for item in reversed(value):
            found = extract_user_text(item, depth=depth + 1)
            if found:
                return found
        return ""
    if not isinstance(value, dict):
        raw = getattr(value, "raw", None)
        if raw is not None:
            return extract_user_text(raw, depth=depth + 1)
        return ""
    role = str(value.get("role") or value.get("type") or "").casefold()
    if role and role not in {"user", "human", "user_message", "chat", "message"}:
        return ""
    for key in ("text", "content", "message", "user_message", "line", "query"):
        candidate = value.get(key)
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    for key in ("payload", "data", "raw", "record", "message"):
        found = extract_user_text(value.get(key), depth=depth + 1)
        if found:
            return found
    return ""
