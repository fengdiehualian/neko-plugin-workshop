"""关键词风格审计: 全部游戏的 keywords.json 统一命名规范。

规则(对应 docs/pitfalls.md §7):
1. 跨游戏不重复、不子串截胡(A 的短词 in B 的长词也算冲突);
2. 不允许单字关键词(「钓」「正」「反」这类会在正常聊天里乱命中) ——
   单字/泛化词只允许留在游戏内部 _RULES, 由会话内规则 3 兜底。
"""
from __future__ import annotations

import itertools
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CFG = ROOT / "data" / "config"


def _all_keywords() -> dict:
    out = {}
    for d in sorted(CFG.iterdir()):
        k = d / "keywords.json"
        if k.is_file():
            out[d.name] = json.loads(k.read_text(encoding="utf-8"))
    return out


def test_no_cross_game_duplicates_or_substrings() -> None:
    """keywords.json 不许跨游戏重复/子串截胡(parse_input 是子串匹配)。"""
    kws = _all_keywords()
    problems = []
    for a, b in itertools.combinations(kws, 2):
        for wa in kws[a]:
            for wb in kws[b]:
                if wa == wb:
                    problems.append(f"{a}:{wa} == {b}:{wb}")
                elif wa in wb:
                    problems.append(f"{a}:{wa} 截 {b}:{wb}")
                elif wb in wa:
                    problems.append(f"{b}:{wb} 截 {a}:{wa}")
    assert not problems, f"跨游戏关键词冲突: {problems}"


def test_no_single_char_keywords() -> None:
    """单字词不进 keywords.json(会话内仍可用, 由规则 3 + _RULES 兜底)。"""
    bad = [f"{gid}:{w}" for gid, words in _all_keywords().items()
           for w in words if len(str(w).strip()) <= 1]
    assert not bad, f"这些单字关键词会在闲聊里乱命中: {bad}"
