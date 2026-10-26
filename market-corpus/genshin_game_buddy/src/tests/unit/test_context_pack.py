"""A-24 / A-25 / A-27 / A-33 / C-06 ~ C-08 / D-09：语境块与查询卡片的长度预算。

``context_pack`` 是纯函数模块（无 IO、无 i18n、无 self），所以这里直接喂模板文本，
连插件都不用构造。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from plugin.plugins.genshin_game_buddy.context_pack import (
    build_context_block,
    build_lookup_block,
    clip_text,
    render_term_card,
    select_context_terms,
)
from plugin.plugins.genshin_game_buddy.term_store import load_library

HEADER = "【当前陪聊游戏】原神"
TONE = "口吻：熟悉提瓦特大陆"
TERMS_HEADER = "常用术语："
HINT = "需要更细的术语时调用 lookup_game_term 查询。"
EMPTY_NOTE = "（这款游戏暂未提供常驻术语。）"

TERM_LINES = [
    "- 元素反应：两种元素相互作用产生的额外效果。",
    "- 深境螺旋：每月刷新的高层挑战副本。",
    "- 抽卡：通过祈愿获得角色与武器。",
    "- 体力：进入副本消耗的资源。",
    "- 欧：运气好。",
]


def _block(**overrides):
    kwargs = {
        "header": HEADER,
        "tone_line": TONE,
        "terms_header": TERMS_HEADER,
        "term_lines": TERM_LINES,
        "empty_note": EMPTY_NOTE,
        "hint": HINT,
        "max_chars": 800,
    }
    kwargs.update(overrides)
    return build_context_block(**kwargs)


# =============================================================================
# clip_text
# =============================================================================


@pytest.mark.unit
@pytest.mark.parametrize(
    ("text", "limit", "expected"),
    [
        ("abc", 5, "abc"),
        ("abcde", 5, "abcde"),
        ("abcdef", 5, "abcd…"),
        ("abcdef", 1, "…"),
        ("abcdef", 0, ""),
        ("abcdef", -3, ""),
        ("", 5, ""),
    ],
)
def test_clip_text(text: str, limit: int, expected: str) -> None:
    assert clip_text(text, limit) == expected


# =============================================================================
# A-24 / A-25：条数上限与字符硬上限
# =============================================================================


@pytest.mark.unit
def test_context_pack__max_terms__limits_term_lines() -> None:
    two = _block(term_lines=TERM_LINES[:2])
    assert sum(1 for line in two.splitlines() if line.startswith("- ")) == 2

    zero_terms = _block(term_lines=[], max_chars=800)
    assert not any(line.startswith("- ") for line in zero_terms.splitlines())
    # 没有术语行时块仍然生成，且带上"暂无常驻术语"的说明
    assert zero_terms
    assert EMPTY_NOTE in zero_terms


@pytest.mark.unit
@pytest.mark.parametrize("limit", [1, 16, 40, 60, 80, 100, 200, 800])
def test_context_pack__max_chars__hard_truncates_within_budget(limit: int) -> None:
    block = _block(max_chars=limit)
    assert len(block) <= limit


@pytest.mark.unit
def test_context_pack__max_chars__truncates_on_complete_lines() -> None:
    limit = 100
    block = _block(max_chars=limit)
    assert len(block) <= limit
    allowed = {HEADER, TONE, TERMS_HEADER, EMPTY_NOTE, HINT, *TERM_LINES}
    # 预算足够容纳标题，所以每一行都必须是完整的整行（不许出现半个词）
    assert set(block.splitlines()) <= allowed
    assert all(line in allowed for line in block.splitlines())


@pytest.mark.unit
def test_context_pack__max_chars__shrinking_drops_lower_priority_lines_first() -> None:
    short = _block(max_chars=60)
    long = _block(max_chars=800)
    assert len(short) < len(long)
    # 标题是最高优先级：只要放得下就必须在
    assert short.splitlines()[0] == HEADER
    # 末尾提示是最低优先级：预算被压时会先消失
    assert HINT in long
    assert HINT not in short


@pytest.mark.unit
def test_context_pack__degenerate_budget_still_bounded() -> None:
    # 连标题都放不下：只能硬裁，但绝不能超预算
    block = _block(max_chars=5)
    assert len(block) <= 5
    assert block.endswith("…")


@pytest.mark.unit
def test_context_pack__no_dangling_terms_header() -> None:
    # 预算只够放标题 + 术语标题、放不下任何一条术语时，不许留下悬空的"常用术语："
    block = _block(max_chars=len(HEADER) + len(TERMS_HEADER) + len(TONE) + 2)
    lines = block.splitlines()
    if TERMS_HEADER in lines:
        assert any(line.startswith("- ") for line in lines)


@pytest.mark.unit
def test_context_pack__empty_inputs_do_not_crash() -> None:
    assert build_context_block(header="") == ""
    assert build_context_block(header="", max_chars=0) == ""
    assert build_context_block(header=HEADER, max_chars=1) == "…"


# =============================================================================
# A-33：context_terms 覆盖自动选取
# =============================================================================


def _library(terms_root: Path):
    (terms_root / "terms" / "demo").mkdir(parents=True, exist_ok=True)
    (terms_root / "terms" / "demo" / "core.toml").write_text(
        '[game]\ntone = "t"\n\n'
        '[terms."甲"]\nkind = "机制"\nbrief = "甲说明"\n\n'
        '[terms."乙"]\nkind = "机制"\nbrief = "乙说明"\n\n'
        '[terms."丙"]\nkind = "机制"\nbrief = "丙说明"\n',
        encoding="utf-8",
    )
    (terms_root / "terms" / "demo" / "slang.toml").write_text(
        '[terms."丁"]\nkind = "黑话"\nbrief = "丁说明"\n',
        encoding="utf-8",
    )
    library = load_library(
        default_root=terms_root,
        override_root=None,
        game_id="demo",
        display_name="示例",
        terms_dir="terms/demo",
    )
    return library


@pytest.mark.unit
def test_context_pack__context_terms__overrides_auto_selection(terms_root: Path) -> None:
    library = _library(terms_root)

    automatic = select_context_terms(library, limit=2)
    assert [entry.key for entry in automatic.entries] == ["甲", "乙"]

    forced = select_context_terms(library, forced_keys=["丙", "甲"], limit=2)
    # 严格按给定顺序，且忽略条数上限
    assert [entry.key for entry in forced.entries] == ["丙", "甲"]
    assert forced.missing_keys == ()


@pytest.mark.unit
def test_context_pack__context_terms__unknown_keys_are_reported_not_fatal(terms_root: Path) -> None:
    library = _library(terms_root)
    forced = select_context_terms(library, forced_keys=["不存在", "甲"], limit=1)
    assert [entry.key for entry in forced.entries] == ["甲"]
    assert forced.missing_keys == ("不存在",)


@pytest.mark.unit
def test_context_pack__auto_selection_is_core_then_slang(terms_root: Path) -> None:
    library = _library(terms_root)
    auto = select_context_terms(library, limit=4)
    assert [entry.key for entry in auto.entries] == ["甲", "乙", "丙", "丁"]


@pytest.mark.unit
def test_context_pack__auto_selection_limit_zero_yields_nothing(terms_root: Path) -> None:
    library = _library(terms_root)
    assert select_context_terms(library, limit=0).entries == ()
    assert select_context_terms(library, limit=-1).entries == ()


# =============================================================================
# A-27 / C-06：查询卡片
# =============================================================================


def _entry(**overrides):
    from plugin.plugins.genshin_game_buddy.term_store import TermEntry

    values = {
        "key": "元素反应",
        "kind": "机制",
        "brief": "两种元素相互作用产生的额外效果。",
        "slang": ("打反应", "上元素"),
        "avoid": "不要用属性克制描述原神",
        "source": "官方wiki",
    }
    values.update(overrides)
    return TermEntry(**values)


@pytest.mark.unit
@pytest.mark.parametrize("limit", [40, 60, 80, 120, 200])
def test_lookup__max_chars_per_term__truncates_each_entry(limit: int) -> None:
    entry = _entry(brief="说明" * 100, avoid="注意" * 100)
    card = render_term_card(
        entry,
        entry_line=lambda item: f"【{item.key}】（{item.kind}）{item.brief}",
        slang_label="相关说法：",
        avoid_label="注意不要说：",
        max_chars=limit,
    )
    assert len(card) <= limit
    assert card


@pytest.mark.unit
def test_lookup__card_keeps_slang_and_avoid_when_they_fit() -> None:
    card = render_term_card(
        _entry(),
        entry_line=lambda item: f"【{item.key}】（{item.kind}）{item.brief}",
        slang_label="相关说法：",
        avoid_label="注意不要说：",
        max_chars=500,
    )
    assert "相关说法：打反应、上元素" in card
    assert "注意不要说：不要用属性克制描述原神" in card


@pytest.mark.unit
def test_lookup__source_is_carried_but_never_injected(terms_root: Path) -> None:
    _library(terms_root)  # 先建目录
    (terms_root / "terms" / "demo" / "core.toml").write_text(
        '[game]\ntone = "t"\n\n'
        '[terms."甲"]\nkind = "机制"\nbrief = "甲说明"\nsource = "SOURCE-MARKER-aa11"\n',
        encoding="utf-8",
    )
    library = load_library(
        default_root=terms_root,
        override_root=None,
        game_id="demo",
        display_name="示例",
        terms_dir="terms/demo",
    )
    entry = library.get("甲")
    # 查询返回体会带上 source（调用方负责渲染），但它绝不进自动注入的语境块
    assert entry is not None and entry.source == "SOURCE-MARKER-aa11"
    block = _block(term_lines=[f"- {entry.key}：{entry.brief}"])
    assert "SOURCE-MARKER-aa11" not in block


@pytest.mark.unit
def test_build_lookup_block__joins_header_and_cards() -> None:
    assert build_lookup_block("找到 2 条：", ["A", "B"]) == "找到 2 条：\n\nA\n\nB"
    assert build_lookup_block("", ["A"]) == "A"
    assert build_lookup_block("", []) == ""
    assert build_lookup_block("header", []) == "header"
