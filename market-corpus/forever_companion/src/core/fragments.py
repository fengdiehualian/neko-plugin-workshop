"""永远的陪伴 —— 时光日记·自动碎片的纯逻辑（提取 prompt 组装 / 模型回复解析 / 检索过滤）。

不持有宿主上下文、不做 IO：小模型调用与落盘由主类（经 EmotionSenseService 的
直连通道）完成，本模块只负责"调之前"与"拿到回复之后"的纯数据处理。
常量（类型表/截断上限/prompt 模板）来自 state.py；导入用 try/except 双路径，
兼容包加载（plugins.forever_companion）与裸导入（同 cycle.py 被 test_cycle.py
裸导入的先例）两种姿势。
"""

from __future__ import annotations

from typing import Any

from .state import (
    _FRAGMENT_EXTRACTION_PROMPT,
    _FRAGMENT_KINDS,
    _FRAGMENT_NOTE_MAX_CHARS,
    _FRAGMENT_NUDGE_KINDS,
    _FRAGMENT_QUOTE_MAX_CHARS,
)

JsonObject = dict[str, Any]


def build_fragment_prompt(user_text: str, her_text: str) -> str:
    """组装碎片提取请求：主人的话是分析对象，她的回复只作语境（截断防 prompt 膨胀）。"""
    context = f"（猫娘当时的回复：{her_text[:120]}）" if str(her_text or "").strip() else ""
    return f"{_FRAGMENT_EXTRACTION_PROMPT}{str(user_text or '')[:300]}{context}"


def parse_fragment_response(raw: str) -> JsonObject | None:
    """解析小模型的碎片提取回复 → 归一化 dict；解析不出/字段坏返回 None。

    容忍模型在 JSON 前后附带解释文本（取第一个 {...} 块）；capture 缺省按 False
    处理（宁可漏记不可错记）；kind 不在类型表或缺 quote → None；
    confidence 钳制到 [0,1]。返回结构：{capture, kind, quote, note, confidence}。
    """
    import json as _json

    text = str(raw or "").strip()
    if not text:
        return None
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = _json.loads(text[start : end + 1])
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    try:
        confidence = max(0.0, min(1.0, float(data.get("confidence") or 0.0)))
    except (TypeError, ValueError):
        confidence = 0.0
    if not data.get("capture"):
        return {"capture": False, "confidence": confidence}
    kind = str(data.get("kind") or "").strip()
    if kind not in _FRAGMENT_KINDS:
        return None
    quote = str(data.get("quote") or "").strip()
    if not quote:
        return None  # 没有原话摘录的碎片无从对证，按解析失败丢弃
    return {
        "capture": True,
        "kind": kind,
        "quote": quote,
        "note": str(data.get("note") or "").strip(),
        "confidence": confidence,
    }


def fragment_record(ts_iso: str, phase: str, parsed: JsonObject) -> JsonObject:
    """解析结果 → 时光日记时间线条目（source=auto，quote/note 截断到记录上限）。"""
    return {
        "ts": ts_iso,
        "source": "auto",
        "phase": phase,
        "kind": str(parsed["kind"]),
        "quote": str(parsed["quote"])[:_FRAGMENT_QUOTE_MAX_CHARS],
        "note": str(parsed.get("note") or "")[:_FRAGMENT_NOTE_MAX_CHARS],
        "confidence": round(float(parsed.get("confidence") or 0.0), 2),
    }


def recall_fragments(
    diary: list[JsonObject],
    kind: str = "",
    query: str = "",
    limit: int = 10,
) -> list[JsonObject]:
    """时光日记检索（她自主调用的"翻旧账/回忆喜好"工具的取数纯函数）。

    只返回自动碎片（source=auto；她的手记不在此列）；kind 过滤、query 对
    quote+note 做不区分大小写的子串匹配；按时间倒序（最近的先想起），截 limit 条。
    limit 钳制到 1..30。
    """
    try:
        cap = max(1, min(30, int(limit)))
    except (TypeError, ValueError):
        cap = 10
    wanted_kind = str(kind or "").strip()
    needle = str(query or "").strip().lower()
    hits: list[JsonObject] = []
    for item in reversed(diary):
        if str(item.get("source") or "") != "auto":
            continue
        if wanted_kind and str(item.get("kind") or "") != wanted_kind:
            continue
        if needle:
            haystack = f"{item.get('quote') or ''} {item.get('note') or ''}".lower()
            if needle not in haystack:
                continue
        hits.append(item)
        if len(hits) >= cap:
            break
    return hits


def should_nudge_fight(
    record: JsonObject,
    mood_action: str,
    pause_actions: frozenset[str],
    nudge_kinds: frozenset[str] = _FRAGMENT_NUDGE_KINDS,
) -> bool:
    """吵架轻语判定（纯判定，节流由调用方按 shard 时间戳处理）：
    只有重度负面动作生效中、新碎片是"过激/厌恶"类才值得提醒她"你记得吗"。"""
    return bool(
        mood_action
        and mood_action in pause_actions
        and str(record.get("kind") or "") in nudge_kinds
    )
