"""Distill a short, non-spoiling approach hint from a Luogu solution (题解).

The inputs are a solution's markdown ``content`` plus the problem's tag names.
The goal is a *pointer* (technique + one idea), not a walkthrough or code —
so the reader still does the thinking. ``_hint`` stays free of SDK / network so
it can be unit-tested (mirrors ``_parsing``).
"""

from __future__ import annotations

import re

# Paragraphs that are first-person meta-commentary / fluff, not the approach.
_FLUFF_RE = re.compile(
    r"个人觉得|其实|没有那么难|不喜勿喷|前排|题解 P|本文章|转载|大佬|%%%%|Orz|"
    r"很久以前|自己的算法|交上去|发现是正解|先说|滑稽|占个坑|补丁|废话|勿喷|"
    r"恶搞|到此为止|不会做|水题|太简单|告辞|送分|没什么好说的|害了|%%",
    re.I,
)
# Paragraphs that merely restate the problem rather than give the approach.
_STATEMENT_RE = re.compile(r"^题意|题目描述|题目大意|翻译|概括|审题|题意分析|我们来看|题意是|重新描述", re.I)
# Verbs that indicate an algorithmic idea; used to score "approach" paragraphs.
_APPROACH_RE = re.compile(
    r"排序|贪心|枚举|遍历|循环|判断|比较|初始化|维护|更新|转移|选取|统计|二分|双指针|"
    r"扫描|递归|剪枝|映射|离散化|前缀|差分|线段树|单调|状态|dp|动态规划|背包|数据结构|"
    r"暴力|优化|模拟|最大值|最小值|结尾时刻|开头|分数规划|01分数|区间|线段|物品|"
    r"状态定义|转移方程|贪心选择|最优",
    re.I,
)
_CODE_FENCE_RE = re.compile(r"```.*?```", re.S)
_MARKDOWN_NOISE_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)|\[([^\]]*)\]\([^)]*\)")


def clean_content(content: str) -> str:
    """Strip code blocks / markdown noise. Keep paragraph breaks so the
    idea paragraphs (later in the editorial) don't get merged with the intro."""
    if not content:
        return ""
    text = _CODE_FENCE_RE.sub(" ", content)
    text = _MARKDOWN_NOISE_RE.sub(r"\1", text)
    text = text.replace("**", "").replace("##", "").replace("#", "") \
        .replace("$", "").replace("`", "")
    # collapse only runs of spaces/tabs; preserve newlines (paragraph breaks)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n", "\n\n", text)
    return text.strip()


def _paragraphs(text: str) -> list[str]:
    parts = re.split(r"\n\s*\n|\n|\s{2,}", text)
    return [p.strip() for p in parts if len(p.strip()) >= 8]


def _score_paragraph(para: str) -> float:
    """Higher = more likely to be the core approach statement."""
    if _FLUFF_RE.search(para):
        return -100.0
    if _STATEMENT_RE.search(para):
        return -50.0
    return float(len(_APPROACH_RE.findall(para))) - abs(len(para) - 80) / 200.0


def _idea_paragraphs(text: str) -> list[str]:
    paras = [p for p in _paragraphs(clean_content(text)) if _APPROACH_RE.search(p)]
    return sorted(paras, key=_score_paragraph, reverse=True)


def _first_sentence(para: str) -> str:
    sentence = re.split(r"[。；]\s*", para)[0]
    # strip parenthetical asides so the hint stays tight
    sentence = re.sub(r"[（(][^）)]{0,40}[）)]", "", sentence)
    return re.sub(r"[，,：:]\s*$", "", sentence).strip()


def first_idea(content: str) -> str:
    """Return the single best idea sentence extracted from a solution."""
    ideas = _idea_paragraphs(content)
    if not ideas:
        return ""
    best = ideas[0]
    sentence = _first_sentence(best)
    # If the first sentence is too terse, pull in the next clause.
    if len(sentence) < 12:
        parts = re.split(r"[。；]\s*", best)
        sentence = (parts[0] + "，" + parts[1]) if len(parts) > 1 else sentence
    return sentence[:160]


def make_hint(content: str, tag_names: tuple[str, ...], level: int = 2) -> str:
    """Build a hint string. level 1 = direction, 2 = one-line pointer (default),
    3 = a couple of idea sentences. Never returns code."""
    tags_disp = "、".join(t for t in tag_names if t) or "基础"
    base = f"这是「{tags_disp}」类问题"
    if level <= 1:
        return base + "。"
    idea = first_idea(content)
    if not idea:
        return base + "，抓住这类的核心特性就能入手。"
    if level == 2:
        return f"{base}：{idea}"
    # level 3: up to two sentences
    top = _idea_paragraphs(content)
    top = top[0] if top else idea
    sentences = [_first_sentence(top)]
    nxt = re.split(r"[。；]\s*", clean_content(top))
    if len(nxt) > 1:
        sentences.append(nxt[1].strip()[:140])
    body = "；".join(s for s in sentences if s)
    return f"{base}：{body}"


__all__ = ["clean_content", "first_idea", "make_hint"]
