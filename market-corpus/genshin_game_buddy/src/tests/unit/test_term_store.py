"""B / C 节：术语库维度覆盖、字段校验、两层合并与查询匹配。

真实术语树（``terms/``）的用例直接跑随包分发的那份文件，
因为"注册表声明了什么、目录里就必须有什么"只有在真树上才成立。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import PLUGIN_DIR, load_manifest, write_dimension, write_terms_tree
from plugin.plugins.genshin_game_buddy.term_store import (
    CONTEXT_DIMENSIONS,
    DIMENSIONS,
    MAX_BRIEF_CHARS,
    TermLoadError,
    load_library,
)

REAL_GAMES = ("genshin", "wuthering_waves")

NOTE_MARKER = "NOTES-MARKER-7f31c9"


def _load(root: Path, game_id: str = "demo", *, override: Path | None = None, terms_dir: str | None = None):
    warnings: list[str] = []
    library = load_library(
        default_root=root,
        override_root=override,
        game_id=game_id,
        display_name="示例游戏",
        terms_dir=terms_dir or f"terms/{game_id}",
        warn=warnings.append,
    )
    return library, warnings


# =============================================================================
# B 节：真实术语树
# =============================================================================


@pytest.mark.unit
@pytest.mark.parametrize("game_id", REAL_GAMES)
def test_terms__real_tree__every_declared_game_has_four_dimension_files(game_id: str) -> None:
    manifest = load_manifest()
    entry = manifest["games"][game_id]
    terms_dir = PLUGIN_DIR / entry["terms_dir"]
    assert terms_dir.is_dir(), f"{game_id}: terms_dir missing"
    for dimension in DIMENSIONS:
        assert (terms_dir / f"{dimension}.toml").is_file(), f"{game_id}/{dimension}.toml missing"


@pytest.mark.unit
@pytest.mark.parametrize("game_id", REAL_GAMES)
def test_terms__real_tree__core_has_game_table_with_tone(game_id: str) -> None:
    manifest = load_manifest()
    library, warnings = _load(PLUGIN_DIR, game_id, terms_dir=manifest["games"][game_id]["terms_dir"])
    assert library.tone.strip(), f"{game_id}: tone is empty"
    assert not warnings, warnings


@pytest.mark.unit
@pytest.mark.parametrize("game_id", REAL_GAMES)
@pytest.mark.parametrize("dimension", ["characters", "slang", "systems"])
def test_terms__real_tree__non_core_files_have_no_game_table(game_id: str, dimension: str) -> None:
    manifest = load_manifest()
    text = (PLUGIN_DIR / manifest["games"][game_id]["terms_dir"] / f"{dimension}.toml").read_text(
        encoding="utf-8"
    )
    # 逐行判断，避免注释里提到 [game] 造成误报
    assert not any(line.strip().startswith("[game") for line in text.splitlines())


@pytest.mark.unit
def test_terms__real_tree__wuwa_slang_may_have_zero_terms() -> None:
    manifest = load_manifest()
    library, warnings = _load(
        PLUGIN_DIR, "wuthering_waves", terms_dir=manifest["games"]["wuthering_waves"]["terms_dir"]
    )
    assert "slang" in library.loaded_dimensions
    assert "slang" in library.empty_dimensions
    assert not warnings, warnings
    # 整个游戏仍然可用：core 的三条机制术语照样能查到
    assert library.size() >= 3
    assert library.match("声骸", 3)


@pytest.mark.unit
def test_terms__real_tree__genshin_loads_all_four_dimensions() -> None:
    manifest = load_manifest()
    library, warnings = _load(PLUGIN_DIR, "genshin", terms_dir=manifest["games"]["genshin"]["terms_dir"])
    assert set(library.loaded_dimensions) == set(DIMENSIONS)
    assert library.failed_dimensions == ()
    assert not warnings, warnings
    assert library.size() > 0


@pytest.mark.unit
def test_terms__real_tree__no_orphan_terms_directory() -> None:
    manifest = load_manifest()
    declared = {
        (PLUGIN_DIR / entry["terms_dir"]).resolve() for entry in manifest["games"].values()
    }
    terms_root = PLUGIN_DIR / "terms"
    for child in terms_root.iterdir():
        if child.is_dir():
            assert child.resolve() in declared, f"orphan terms dir: {child.name}"


@pytest.mark.unit
def test_terms__real_tree__no_unknown_dimension_files() -> None:
    for game_dir in (PLUGIN_DIR / "terms").iterdir():
        if not game_dir.is_dir():
            continue
        for path in game_dir.glob("*.toml"):
            if path.stem == "scenes":
                continue  # scenes.toml is scene definition, not a term dimension
            assert path.stem in DIMENSIONS, f"unexpected dimension file: {path}"


# =============================================================================
# B-12 / A-31：维度白名单与目录错误
# =============================================================================


@pytest.mark.unit
def test_term_store__unknown_dimension_file_is_ignored(terms_root: Path) -> None:
    write_terms_tree(
        terms_root,
        "demo",
        core='[game]\ntone = "t"\n\n[terms."甲"]\nkind = "机制"\nbrief = "说明"\n',
    )
    write_dimension(terms_root, "demo", "extra", '[terms."野"]\nkind = "机制"\nbrief = "不该被读"\n')
    library, warnings = _load(terms_root)
    assert [entry.key for entry in library.entries] == ["甲"]
    assert library.get("野") is None
    assert not warnings, warnings


@pytest.mark.unit
def test_term_store__terms_dir_missing_raises_stable_code(terms_root: Path) -> None:
    with pytest.raises(TermLoadError) as exc:
        _load(terms_root, "nope")
    assert exc.value.code == "terms_dir_missing"


@pytest.mark.unit
def test_term_store__unsafe_terms_dir_is_refused(terms_root: Path) -> None:
    with pytest.raises(TermLoadError) as exc:
        _load(terms_root, "demo", terms_dir="../../escape")
    assert exc.value.code == "terms_dir_missing"


@pytest.mark.unit
def test_term_store__dir_without_whitelisted_files_yields_empty_library(terms_root: Path) -> None:
    write_dimension(terms_root, "demo", "extra", "[terms]\n")
    library, _warnings = _load(terms_root)
    assert library.entries == ()
    assert library.loaded_dimensions == ()


# =============================================================================
# C-01 ~ C-08：字段校验
# =============================================================================


@pytest.mark.unit
def test_term__missing_kind__entry_rejected_with_warning(terms_root: Path) -> None:
    write_terms_tree(
        terms_root,
        "demo",
        core=(
            '[terms."坏的"]\nbrief = "有 brief 没 kind"\n\n'
            '[terms."好的"]\nkind = "机制"\nbrief = "完整"\n'
        ),
    )
    library, warnings = _load(terms_root)
    assert [entry.key for entry in library.entries] == ["好的"]
    assert library.skipped_terms == ("core:坏的",)
    assert any("kind is required" in message for message in warnings)


@pytest.mark.unit
def test_term__brief__missing_rejected_and_too_long_warned(terms_root: Path) -> None:
    long_brief = "长" * (MAX_BRIEF_CHARS + 5)
    write_terms_tree(
        terms_root,
        "demo",
        core=(
            '[terms."无说明"]\nkind = "机制"\n\n'
            f'[terms."太长的"]\nkind = "机制"\nbrief = "{long_brief}"\n'
        ),
    )
    library, warnings = _load(terms_root)
    assert [entry.key for entry in library.entries] == ["太长的"]
    assert library.skipped_terms == ("core:无说明",)
    assert any("brief is required" in message for message in warnings)
    assert any("soft limit" in message for message in warnings)


@pytest.mark.unit
def test_term__optional_fields_are_carried(terms_root: Path) -> None:
    write_terms_tree(
        terms_root,
        "demo",
        core=(
            '[terms."甲"]\nkind = "角色"\nbrief = "说明"\n'
            'aliases = ["甲甲", ""]\nslang = ["黑话"]\n'
            'avoid = "别说错"\nsource = "官方wiki"\n'
            "unknown_field = 1\n"
        ),
    )
    library, warnings = _load(terms_root)
    entry = library.get("甲")
    assert entry is not None
    assert entry.aliases == ("甲甲",)  # 空元素被丢掉
    assert entry.slang == ("黑话",)
    assert entry.avoid == "别说错"
    assert entry.source == "官方wiki"
    assert not warnings, warnings


@pytest.mark.unit
def test_term__aliases__participate_in_lookup(terms_root: Path) -> None:
    write_terms_tree(
        terms_root,
        "demo",
        characters='[terms."雷电将军"]\nkind = "角色"\nbrief = "雷元素单手剑"\naliases = ["雷神", "影"]\n',
    )
    library, _warnings = _load(terms_root)
    assert [entry.key for entry in library.match("雷神", 3)] == ["雷电将军"]
    assert [entry.key for entry in library.match("影", 3)] == ["雷电将军"]
    assert [entry.key for entry in library.match("雷电将军", 3)] == ["雷电将军"]


@pytest.mark.unit
def test_term__slang__participates_in_lookup(terms_root: Path) -> None:
    write_terms_tree(
        terms_root,
        "demo",
        slang='[terms."歪了"]\nkind = "黑话"\nbrief = "没出当期"\nslang = ["歪出"]\n',
    )
    library, _warnings = _load(terms_root)
    assert [entry.key for entry in library.match("歪出", 3)] == ["歪了"]


@pytest.mark.unit
def test_terms__game_tone__read_from_core_only(terms_root: Path) -> None:
    write_terms_tree(terms_root, "demo", core='[game]\ntone = "第一个口吻"\n[terms."甲"]\nkind = "机制"\nbrief = "x"\n')
    library, warnings = _load(terms_root)
    assert library.tone == "第一个口吻"
    assert not warnings, warnings

    write_terms_tree(
        terms_root,
        "demo",
        characters='[game]\ntone = "不该生效"\n\n[terms."乙"]\nkind = "角色"\nbrief = "y"\n',
    )
    library, warnings = _load(terms_root)
    assert library.tone == "第一个口吻"  # core 仍然赢
    assert any("only meaningful in core.toml" in message for message in warnings)


@pytest.mark.unit
def test_terms__game_notes__never_injected(terms_root: Path) -> None:
    write_terms_tree(
        terms_root,
        "demo",
        core=(
            "[game]\n"
            'tone = "口吻"\n'
            f'notes = "{NOTE_MARKER}"\n\n'
            '[terms."甲"]\nkind = "机制"\nbrief = "说明"\n'
        ),
    )
    library, _warnings = _load(terms_root)
    blob = "\n".join(
        [library.tone, *[f"{e.key}{e.kind}{e.brief}{e.avoid}{e.source}" for e in library.entries]]
    )
    assert NOTE_MARKER not in blob


@pytest.mark.unit
def test_terms__malformed_core_games_table_is_ignored(terms_root: Path) -> None:
    write_terms_tree(terms_root, "demo", core='game = "不是表"\n[terms."甲"]\nkind = "机制"\nbrief = "说明"\n')
    library, warnings = _load(terms_root)
    assert library.tone == ""
    assert library.size() == 1
    assert any("[game] is not a table" in message for message in warnings)


# =============================================================================
# C-09 ~ C-14：两层合并
# =============================================================================


@pytest.mark.unit
def test_merge__override_layer_replaces_default_term_field_by_field(
    terms_root: Path, scratch: Path
) -> None:
    write_terms_tree(
        terms_root,
        "demo",
        core='[terms."甲"]\nkind = "机制"\nbrief = "默认说明"\nslang = ["默认黑话"]\n',
    )
    override = scratch / "override"
    write_terms_tree(override, "demo", core='[terms."甲"]\nbrief = "覆盖说明"\n')

    library, warnings = _load(terms_root, override=override)
    entry = library.get("甲")
    assert entry is not None
    assert entry.brief == "覆盖说明"  # 被覆盖
    assert entry.kind == "机制"  # 继承
    assert entry.slang == ("默认黑话",)  # 继承
    assert not warnings, warnings


@pytest.mark.unit
def test_merge__override_layer_adds_new_term(terms_root: Path, scratch: Path) -> None:
    write_terms_tree(terms_root, "demo", core='[terms."甲"]\nkind = "机制"\nbrief = "默认"\n')
    override = scratch / "override"
    write_terms_tree(override, "demo", core='[terms."乙"]\nkind = "机制"\nbrief = "新增"\n')
    library, _warnings = _load(terms_root, override=override)
    assert sorted(entry.key for entry in library.entries) == sorted(["甲", "乙"])
    assert library.get("乙") is not None
    assert library.get("甲") is not None


@pytest.mark.unit
def test_merge__override_layer_does_not_wipe_other_dimensions(
    terms_root: Path, scratch: Path
) -> None:
    write_terms_tree(
        terms_root,
        "demo",
        core='[terms."甲"]\nkind = "机制"\nbrief = "默认甲"\n',
        characters='[terms."丙"]\nkind = "角色"\nbrief = "默认丙"\n',
        slang='[terms."丁"]\nkind = "黑话"\nbrief = "默认丁"\n',
    )
    override = scratch / "override"
    write_terms_tree(override, "demo", core='[terms."甲"]\nbrief = "覆盖甲"\n')

    library, _warnings = _load(terms_root, override=override)
    assert library.get("丙") is not None
    assert library.get("丁") is not None
    assert set(library.loaded_dimensions) == {"core", "characters", "slang"}


@pytest.mark.unit
def test_merge__malformed_override_is_skipped_default_still_usable(
    terms_root: Path, scratch: Path
) -> None:
    write_terms_tree(terms_root, "demo", core='[terms."甲"]\nkind = "机制"\nbrief = "默认"\n')
    override = scratch / "override"
    write_dimension(override, "demo", "core", '[terms."甲"\nbrief = "坏掉的 TOML"\n')
    library, warnings = _load(terms_root, override=override)
    assert library.get("甲") is not None
    assert library.get("甲").brief == "默认"
    assert any("malformed toml" in message for message in warnings)


@pytest.mark.unit
def test_merge__malformed_default_skips_file_only(terms_root: Path) -> None:
    write_terms_tree(
        terms_root,
        "demo",
        core='[terms."甲"]\nkind = "机制"\nbrief = "能查"\n',
        characters='[terms."乙"\nbroken\n',
    )
    library, warnings = _load(terms_root)
    assert [entry.key for entry in library.entries] == ["甲"]
    assert library.match("乙", 3) == ()
    assert "characters" in library.failed_dimensions
    assert any("malformed toml" in message for message in warnings)


@pytest.mark.unit
def test_term_store__override_root_is_the_given_root_not_a_guessed_path(
    terms_root: Path, scratch: Path
) -> None:
    write_terms_tree(terms_root, "demo", core='[terms."甲"]\nkind = "机制"\nbrief = "默认"\n')
    override = scratch / "somewhere" / "else"
    write_terms_tree(override, "demo", core='[terms."甲"]\nbrief = "来自指定根"\n')
    library, _warnings = _load(terms_root, override=override)
    assert library.get("甲").brief == "来自指定根"


@pytest.mark.unit
def test_merge__duplicate_key_across_dimensions_first_dimension_wins(
    terms_root: Path,
) -> None:
    write_terms_tree(
        terms_root,
        "demo",
        core='[terms."同名"]\nkind = "机制"\nbrief = "来自 core"\n',
        slang='[terms."同名"]\nkind = "黑话"\nbrief = "来自 slang"\n',
    )
    library, warnings = _load(terms_root)
    assert [entry for entry in library.entries if entry.key == "同名"][0].brief == "来自 core"
    assert any("already defined" in message for message in warnings)


# =============================================================================
# 查询匹配分级
# =============================================================================


@pytest.fixture
def lookup_library(terms_root: Path):
    write_terms_tree(
        terms_root,
        "demo",
        core=(
            '[terms."元素反应"]\nkind = "机制"\nbrief = "两种元素相互作用产生的额外效果。"\n'
            'aliases = ["反应"]\nslang = ["打反应"]\n'
        ),
        characters='[terms."雷电将军"]\nkind = "角色"\nbrief = "雷元素单手剑角色。"\naliases = ["雷神"]\n',
        systems='[terms."蒸发"]\nkind = "机制"\nbrief = "火与水反应，提升伤害。"\n',
    )
    library, _warnings = _load(terms_root)
    return library


@pytest.mark.unit
def test_lookup__exact_beats_prefix_beats_substring(lookup_library) -> None:
    assert [e.key for e in lookup_library.match("元素反应", 3)] == ["元素反应"]
    assert [e.key for e in lookup_library.match("反应", 3)][0] == "元素反应"
    assert [e.key for e in lookup_library.match("雷", 3)] == []
    assert [e.key for e in lookup_library.match("雷神", 3)][0] == "雷电将军"


@pytest.mark.unit
def test_lookup__respects_limit(lookup_library) -> None:
    matches = lookup_library.match("元素", 1)
    assert len(matches) == 1
    assert lookup_library.match("元素", 0) == ()
    assert lookup_library.match("元素", -1) == ()


@pytest.mark.unit
def test_lookup__empty_query_returns_nothing(lookup_library) -> None:
    assert lookup_library.match("", 3) == ()
    assert lookup_library.match(None, 3) == ()
    assert lookup_library.match("   ", 3) == ()


@pytest.mark.unit
def test_lookup__brief_text_is_searchable(lookup_library) -> None:
    assert [e.key for e in lookup_library.match("单手剑", 3)] == ["雷电将军"]
    assert [e.key for e in lookup_library.match("提升伤害", 3)] == ["蒸发"]


@pytest.mark.unit
def test_context_candidates__core_before_slang(terms_root: Path) -> None:
    write_terms_tree(
        terms_root,
        "demo",
        core='[terms."甲"]\nkind = "机制"\nbrief = "core 条目"\n',
        slang='[terms."乙"]\nkind = "黑话"\nbrief = "slang 条目"\n',
        characters='[terms."丙"]\nkind = "角色"\nbrief = "不进语境块"\n',
    )
    library, _warnings = _load(terms_root)
    assert [entry.key for entry in library.context_candidates()] == ["甲", "乙"]
    assert set(CONTEXT_DIMENSIONS) == {"core", "slang"}
