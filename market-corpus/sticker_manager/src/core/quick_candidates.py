"""Bounded, fully described candidates visible before a model calls any tool."""

from __future__ import annotations

from .catalog import Sticker, format_catalog_for_model, search_intent_candidates

QUICK_CANDIDATE_LIMIT = 12
QUICK_CANDIDATE_CHARS = 2000
QUICK_CANDIDATE_TITLE = "【快捷候选：先读说明，贴切可直接选 ID】"
# These are browsing intents, never a decision about the current conversation.
_BROWSE_INTENTS = (
    "开心", "难过", "害羞", "期待", "抗议", "求摸摸",
    "陪伴", "感谢", "困", "无语", "喜欢", "轻松",
)
# The 0.9.10 everyday profile is shared by natural/eager, not current-turn selection.
_EAGER_BROWSE_INTENTS = (
    "开心", "赞同", "调皮", "喜欢", "害羞", "撒娇",
    "陪伴", "无语", "犯困", "难过", "抗议", "期待",
    "感谢", "求摸摸", "轻松", "舒服", "满足", "惊讶", "晚安",
)


def quick_candidates_for_tool(
    stickers: list[Sticker], groups: dict[str, str] | None = None, *,
    limit: int = QUICK_CANDIDATE_LIMIT, recent_ids: set[str] | None = None,
    eager: bool = False,
) -> str:
    """Cover common intents without truncating a candidate's qualifications."""
    excluded = recent_ids or set()
    pool = [
        s for s in stickers
        if not s.disabled and s.id not in excluded and (s.caption.strip() or s.desc.strip())
    ]
    if eager:
        pool.sort(key=lambda s: s.id)
    limit = min(QUICK_CANDIDATE_LIMIT, max(0, limit))
    if not limit:
        return ""
    lines: list[str] = []
    chosen: set[str] = set()
    size = 0
    for intent in _EAGER_BROWSE_INTENTS if eager else _BROWSE_INTENTS:
        matches = search_intent_candidates(pool, intent)
        ranks = {sticker.id: index for index, sticker in enumerate(matches)}
        candidates = sorted(
            matches,
            key=lambda s: (s.last_used_at, s.use_count, ranks[s.id] if eager else 0, s.id),
        )
        for sticker in candidates:
            if sticker.id in chosen:
                continue
            line = format_catalog_for_model([sticker], 1, groups)
            added = len(line) + bool(lines)
            if size + added > QUICK_CANDIDATE_CHARS:
                continue
            lines.append(line)
            chosen.add(sticker.id)
            size += added
            break
        if len(lines) >= limit:
            break
    return "\n".join(lines)
