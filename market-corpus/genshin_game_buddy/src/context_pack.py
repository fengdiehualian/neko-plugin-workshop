"""语境块与查询返回体的构造：纯函数 + 硬长度预算。

两个产出
--------
* **语境块**（自动注入 LLM 上下文）：省字符、按行装配、超预算时**整行丢弃**，
  不切半个词。标题/口吻是最高优先级，术语行与末尾提示按顺序让位。
* **查询卡片**（``lookup_game_term`` 返回体）：单条也受字符上限约束。

本模块不含 i18n、不含 IO、不接触 ``self``：模板文本与标签由调用方渲染后传入，
因此可以脱离宿主单测。``source`` 字段刻意只出现在查询卡片里，不进语境块。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from .term_store import TermEntry, TermLibrary

#: 硬截断时使用的省略号。
_ELLIPSIS = "…"


def clip_text(text: str, limit: int) -> str:
    """把单行文本硬裁到 ``limit`` 个字符；``limit <= 0`` 返回空串。"""
    if not isinstance(text, str) or limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    if limit == 1:
        return _ELLIPSIS
    return text[: limit - 1] + _ELLIPSIS


@dataclass(frozen=True)
class ContextSelection:
    """语境块术语的选取结果。

    ``missing_keys`` 是**配置里写错的 key**（不是用户原文），可以安全进日志。
    """

    entries: tuple[TermEntry, ...] = ()
    missing_keys: tuple[str, ...] = ()


def select_context_terms(
    library: TermLibrary,
    *,
    forced_keys: Sequence[str] = (),
    limit: int = 5,
) -> ContextSelection:
    """决定哪些术语进自动注入的语境块。

    * 声明了 ``context_terms`` → **严格按给定 key 与顺序**注入，忽略 ``limit``；
      不存在的 key 记入 ``missing_keys`` 由调用方告警。
    * 未声明 → 按维度优先级（core → slang）在文件顺序上取前 ``limit`` 条。
    """
    if forced_keys:
        selected: list[TermEntry] = []
        seen: set[str] = set()
        missing: list[str] = []
        for raw in forced_keys:
            key = raw.strip() if isinstance(raw, str) else ""
            if not key:
                continue
            entry = library.get(key)
            if entry is None:
                missing.append(key)
                continue
            if entry.key in seen:
                continue
            seen.add(entry.key)
            selected.append(entry)
        return ContextSelection(entries=tuple(selected), missing_keys=tuple(missing))

    if not isinstance(limit, int) or limit <= 0:
        return ContextSelection()
    return ContextSelection(entries=library.context_candidates()[:limit])


def build_context_block(
    *,
    header: str,
    tone_line: str = "",
    terms_header: str = "",
    term_lines: Sequence[str] = (),
    empty_note: str = "",
    hint: str = "",
    max_chars: int = 800,
) -> str:
    """装配语境块，保证 ``len(result) <= max_chars``（``max_chars`` 至少按 1 处理）。

    装配顺序即优先级：标题 → 口吻 → 术语区 → 末尾提示。术语区只有在
    "标题行 + 至少一条术语行"放得下时才展开，避免出现悬空的"常用术语："。
    """
    budget = max(int(max_chars), 1)
    lines: list[str] = []

    def fits(*extra: str) -> bool:
        return len("\n".join([*lines, *extra])) <= budget

    if header:
        lines.append(header)

    if tone_line and fits(tone_line):
        lines.append(tone_line)

    clean_terms = [line for line in term_lines if line]
    if clean_terms and terms_header:
        if fits(terms_header, clean_terms[0]):
            lines.append(terms_header)
            for line in clean_terms:
                if fits(line):
                    lines.append(line)
                else:
                    break
    elif not clean_terms and empty_note and fits(empty_note):
        lines.append(empty_note)

    if hint and fits(hint):
        lines.append(hint)

    block = "\n".join(lines)
    if len(block) <= budget:
        return block
    # 只有标题本身就超预算时才会走到这里：按行回退，最后兜底硬裁标题。
    while lines and len("\n".join(lines)) > budget:
        lines.pop()
    if not lines:
        return clip_text(header, budget)
    block = "\n".join(lines)
    if len(block) <= budget:
        return block
    return clip_text(lines[0], budget)


def render_term_card(
    entry: TermEntry,
    *,
    entry_line: Callable[[TermEntry], str],
    slang_label: str = "",
    avoid_label: str = "",
    max_chars: int = 200,
) -> str:
    """渲染单条查询结果，整段不超过 ``max_chars`` 个字符。

    行级预算：放不下的子行直接丢弃；连首行都放不下时硬裁首行。
    """
    budget = max(int(max_chars), 1)
    lines = [entry_line(entry)]
    if entry.slang and slang_label:
        lines.append(f"{slang_label}{'、'.join(entry.slang)}")
    if entry.avoid and avoid_label:
        lines.append(f"{avoid_label}{entry.avoid}")

    kept: list[str] = []
    for line in lines:
        if len("\n".join([*kept, line])) <= budget:
            kept.append(line)
        else:
            break
    if not kept:
        kept = [clip_text(lines[0], budget)]
    return "\n".join(kept)


def build_lookup_block(header: str, cards: Sequence[str]) -> str:
    """把"命中 N 条"标题与各条卡片拼成返回给模型的文本块。"""
    parts = [header] if header else []
    parts.extend(card for card in cards if card)
    return "\n\n".join(parts)


__all__ = [
    "ContextSelection",
    "build_context_block",
    "build_lookup_block",
    "clip_text",
    "render_term_card",
    "select_context_terms",
]
