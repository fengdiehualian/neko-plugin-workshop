"""P19.0 守卫：入门档教程补全 + 直播特供版的两个隐藏开关 + 旧作词清零。

这一版做三件事，每件都是「说错了就出事」的硬契约，所以逐条钉死：

1. **入门档（``classic_beginner``）必须有教程** —— 复用街机的 4 步流程，
   但第 4 步（看水位）**单独**多注入一条「这一关开始有生活费了」的新事实。
   街机那句「这一关没有生活费要交」在入门档是**假话**，抄过去就是教错规则。
2. **直播特供版的两个隐藏开关**（``[director] force_black_swan`` /
   ``show_progress``）—— 默认全关、必须同时满足 ``enabled = true`` 才生效、
   纯净版的载荷里**一个字节都不多**。
3. **旧作专有名词清零** —— 源码 / 测试 / 注释里**零明文**。

另外顺手钉住一个 P18.0 留下的**真缺陷**：前两关的紧凑水位条漏了
``rrm-anchor-gauge`` 锚点 class ⇒ 街机档教程第 4 步**从来没圈到过东西**。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from conftest import engine_mode_code_changes, run

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_PANEL = _PLUGIN_ROOT / "ui" / "panel.tsx"
_I18N = _PLUGIN_ROOT / "i18n" / "zh-CN.json"
_CONFIG = _PLUGIN_ROOT / "config.example.toml"

#: 扫描范围：插件仓库的全部文本文件。
_TEXT_SUFFIXES = {
    ".py",
    ".md",
    ".json",
    ".toml",
    ".tsx",
    ".ts",
    ".txt",
    ".yml",
    ".yaml",
    ".cfg",
    ".ini",
}
_SKIP_DIRS = {
    ".git",
    "__pycache__",
    ".venv",
    ".pytest_cache",
    ".ruff_cache",
    "node_modules",
    "vendor",
}

#: P19.0 独立复核用的旧作词 —— **自己构造**（不去依赖 P15 那张表，
#: 否则守卫表被改弱了，这里也会跟着一起瞎）。
_LEGACY_TERMS: tuple[str, ...] = tuple(
    "".join(chr(cp) for cp in codepoints)
    for codepoints in (
        (0x4E45, 0x7559, 0x7F8E),
        (0x0046, 0x0058, 0x6218, 0x58EB),
        (0x53CD, 0x5411, 0x66B4, 0x5BCC),
        (0x6A, 0x75, 0x6C, 0x69, 0x75, 0x6D, 0x65, 0x69),
        (0x0046, 0x0058),
    )
)

#: 内联 data URI 的 base64 载荷 —— 随机字节，不是人写的字。
_BASE64_RUN = re.compile(r"[A-Za-z0-9+/]{40,}={0,2}")


def _panel_code() -> str:
    """面板源码去掉注释后的正文（保留 CSS —— CSS 也要查）。"""
    source = _PANEL.read_text(encoding="utf-8")
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    source = re.sub(r"//[^\n]*", "", source)
    return source


def _i18n() -> dict[str, str]:
    return json.loads(_I18N.read_text(encoding="utf-8"))


def _set_business_config(plugin: Any, ctx: Any, payload: dict[str, Any]) -> None:
    """改业务配置并**清掉 5 秒缓存**（否则读到的还是旧值）。"""
    ctx.config_payload = dict(payload)
    plugin._business_config_cache = None
    plugin._business_config_cached_at = 0.0
    run(plugin._business_config())


def _session(plugin: Any) -> Any:
    """当前对局的 Session。"""
    engine_config = run(plugin._engine_config())
    session, error = run(plugin._session_or_error(engine_config))
    assert error is None, error
    assert session is not None
    return session


def _crashes(plugin: Any) -> list[dict[str, Any]]:
    return [item for item in plugin._director_queue if item.get("op") == "crash"]


# ══════════════════════════════════════════════════════════════════════
# ① 入门档的教程（必须有，且不能教错规则）
# ══════════════════════════════════════════════════════════════════════


def test_the_beginner_level_has_a_four_step_lesson() -> None:
    """入门档**必须有教程**（P19.0 拍板：绝不能没有）。"""
    from plugins.mock_stock_meow.tutorial import TIER_STEPS

    assert "classic_beginner" in TIER_STEPS, "入门档不能没有教程"
    assert len(TIER_STEPS["classic_beginner"]) == 4


def test_the_beginner_lesson_reuses_the_arcade_flow() -> None:
    """前 3 步与街机档**同一套文案与锚点**（操作教学与档位无关）。"""
    from plugins.mock_stock_meow.tutorial import TIER_STEPS

    arcade = TIER_STEPS["classic_arcade"]
    beginner = TIER_STEPS["classic_beginner"]
    for index in range(3):
        assert beginner[index].anchor == arcade[index].anchor, index
        assert beginner[index].title_key == arcade[index].title_key, index
        assert beginner[index].body_key == arcade[index].body_key, index
        assert beginner[index].auto_advance == arcade[index].auto_advance, index


def test_the_beginner_gauge_step_never_says_there_is_no_living_cost() -> None:
    """街机那句「这一关没有生活费要交」在入门档是**假话** —— 不许抄。"""
    from plugins.mock_stock_meow.tutorial import TIER_STEPS

    step = TIER_STEPS["classic_beginner"][3]
    assert step.anchor == "gauge"
    table = _i18n()
    for text in (step.fact, table[step.title_key], table[step.body_key]):
        assert "没有生活费" not in text, text
    # 步骤自己的事实与正文**必须**提到生活费（这一关的核心规则）。
    assert "生活费" in step.fact
    assert "生活费" in table[step.body_key]


def test_the_beginner_lesson_really_starts(plugin: Any) -> None:
    """开一局入门档 ⇒ 教程真的开了（不是只有表里有定义）。"""
    run(plugin.join(mode="classic_beginner", seed=7201))
    tutorial = run(plugin.market_context())["tutorial"]
    assert tutorial["active"] is True
    assert tutorial["tier"] == "classic_beginner"
    assert tutorial["total"] == 4


# ══════════════════════════════════════════════════════════════════════
# ② 第 4 步的「额外事实」（单独成段，经 push_message 注入）
# ══════════════════════════════════════════════════════════════════════


def test_the_beginner_gauge_step_gets_its_own_extra_fact() -> None:
    """第 4 步**单独**多注入一条事实（入门档开始有生活费了）。"""
    from plugins.mock_stock_meow.tutorial import TIER_EXTRA_FACTS

    extra = TIER_EXTRA_FACTS.get("classic_beginner") or {}
    assert "beginner_watch_gauge" in extra, "第 4 步必须有一条额外事实"
    fact = extra["beginner_watch_gauge"]
    assert "入门档" in fact
    assert "生活费" in fact


def test_the_extra_fact_reaches_the_prompt_as_its_own_block() -> None:
    """额外事实**单独成段**（不揉进步骤自己的 fact 里）。"""
    from plugins.mock_stock_meow.tutorial import TIER_EXTRA_FACTS, TIER_STEPS, build_step_prompt

    step = TIER_STEPS["classic_beginner"][3]
    fact = TIER_EXTRA_FACTS["classic_beginner"][step.key]
    prompt = build_step_prompt("classic_beginner", step)
    assert fact in prompt
    assert "【另外要补充的一件事】" in prompt
    # 顺序：步骤自己的事在前，补充的那条在后。
    assert prompt.index(step.fact) < prompt.index("【另外要补充的一件事】")


def test_the_extra_fact_never_leaks_into_other_tiers() -> None:
    """别的档位**不许**拿到入门档的那条事实。"""
    from plugins.mock_stock_meow.tutorial import TIER_EXTRA_FACTS, TIER_STEPS, build_step_prompt

    assert "classic_arcade" not in TIER_EXTRA_FACTS
    arcade = TIER_STEPS["classic_arcade"]
    for step in arcade:
        prompt = build_step_prompt("classic_arcade", step)
        assert "【另外要补充的一件事】" not in prompt, step.key


def test_the_extra_fact_carries_no_performance_instruction() -> None:
    """P13 契约：只给**处境与内容约束**，不给表演指令。"""
    from plugins.mock_stock_meow.tutorial import TIER_EXTRA_FACTS

    for facts in TIER_EXTRA_FACTS.values():
        for fact in facts.values():
            for banned in ("请用", "语气", "傲娇", "撒娇", "假装", "戏谑", "嘲讽"):
                assert banned not in fact, f"{banned}：{fact}"


def test_the_extra_fact_has_no_numbers() -> None:
    """提示词里**不带数值**（与 ``_hard_rules`` 同一条纪律）。"""
    from plugins.mock_stock_meow.tutorial import TIER_EXTRA_FACTS

    for facts in TIER_EXTRA_FACTS.values():
        for fact in facts.values():
            assert not re.search(r"\d", fact), fact


# ══════════════════════════════════════════════════════════════════════
# ③ 入门档专属的挽留句（「我前面教过你的」）
# ══════════════════════════════════════════════════════════════════════


def _fake_player(mode: str, day: int, reason: str) -> Any:
    class _Player:
        pass

    player = _Player()
    player.mode = mode
    player.day = day
    player.death_reason = reason
    return player


def test_the_beginner_early_break_line_gets_the_special_line() -> None:
    """入门档前 3 天被生活费清出去 ⇒ 猫娘多一句「我前面教过你的」。"""
    from plugins.mock_stock_meow import MockStockMeowPlugin

    line = MockStockMeowPlugin._beginner_taught_you_line(
        _fake_player("classic_beginner", 2, "liquidated")
    )
    assert line, "该给这句挽留"
    assert "我前面教过你的" in line


def test_the_special_line_only_applies_to_the_early_beginner_break() -> None:
    """其他档位 / 其他天数 / 其他出局原因**一律不给**。"""
    from plugins.mock_stock_meow import MockStockMeowPlugin

    fn = MockStockMeowPlugin._beginner_taught_you_line
    assert fn(_fake_player("classic_arcade", 2, "liquidated")) == ""
    assert fn(_fake_player("classic_advanced", 2, "liquidated")) == ""
    assert fn(_fake_player("classic_beginner", 5, "liquidated")) == ""
    assert fn(_fake_player("classic_beginner", 2, "delisted")) == ""
    assert fn(_fake_player("classic_beginner", 3, "starved")) != ""


def test_the_special_line_has_an_i18n_key() -> None:
    """那句挽留句在 i18n 里（面板文案统一走词典）。"""
    assert "taunt.cash_kill.beginner_early" in _i18n()
    assert "我前面教过你的" in _i18n()["taunt.cash_kill.beginner_early"]


# ══════════════════════════════════════════════════════════════════════
# ④ 隐藏开关（默认全关 + 必须同时满足 enabled）
# ══════════════════════════════════════════════════════════════════════


def test_the_two_hidden_switches_are_documented_and_off_by_default() -> None:
    """``config.example.toml`` 里两个开关都在，且**默认 false**。"""
    toml = _CONFIG.read_text(encoding="utf-8")
    assert "force_black_swan = false" in toml
    assert "show_progress = false" in toml


def test_the_hidden_switches_need_the_director_to_be_on(plugin: Any, ctx: Any) -> None:
    """开关必须**同时**满足 ``enabled = true``（纯净版不做后台干预）。"""
    _set_business_config(plugin, ctx, {"director": {"enabled": False, "force_black_swan": True}})
    assert plugin._director_flag("force_black_swan") is False
    _set_business_config(plugin, ctx, {"director": {"enabled": True, "force_black_swan": True}})
    assert plugin._director_flag("force_black_swan") is True
    assert plugin._director_flag("show_progress") is False


# ══════════════════════════════════════════════════════════════════════
# ⑤ 黑天鹅（force_black_swan）
# ══════════════════════════════════════════════════════════════════════


def test_the_black_swan_fires_when_a_single_trade_sets_a_new_record(
    plugin: Any, ctx: Any
) -> None:
    """单笔盈利刷新本局纪录 ⇒ 全仓暴跌排进队列。"""
    _set_business_config(plugin, ctx, {"director": {"enabled": True, "force_black_swan": True}})
    run(plugin.join(mode="classic_arcade", seed=7301))
    session = _session(plugin)
    run(plugin._maybe_force_black_swan(session, 50.0, "FAKE-001"))
    queued = _crashes(plugin)
    assert queued, "应该排进暴跌队列"
    assert any(item.get("stock_id") == "FAKE-001" for item in queued)


def test_the_black_swan_covers_the_whole_position(plugin: Any, ctx: Any) -> None:
    """**全仓**暴跌：手里还攥着的每一只都要排上。"""
    _set_business_config(plugin, ctx, {"director": {"enabled": True, "force_black_swan": True}})
    run(plugin.join(mode="classic_arcade", seed=7302))
    session = _session(plugin)
    run(plugin.buy("FAKE-001", 1.0))
    run(plugin.buy("FAKE-002", 1.0))
    session = _session(plugin)
    held = {
        str(stock_id)
        for stock_id, position in session.player.positions.items()
        if position.amount > 0
    }
    run(plugin._maybe_force_black_swan(session, 50.0, "FAKE-001"))
    queued = {str(item.get("stock_id")) for item in _crashes(plugin)}
    assert held, "手里得有仓位才谈得上全仓"
    assert held <= queued, f"手里的 {held} 没全排上：{queued}"


def test_the_black_swan_only_fires_once_per_game(plugin: Any, ctx: Any) -> None:
    """一局只来一次（重复暴跌是把戏，不是节目效果）。"""
    _set_business_config(plugin, ctx, {"director": {"enabled": True, "force_black_swan": True}})
    run(plugin.join(mode="classic_arcade", seed=7303))
    session = _session(plugin)
    run(plugin._maybe_force_black_swan(session, 50.0, "FAKE-001"))
    first = len(plugin._director_queue)
    run(plugin._maybe_force_black_swan(session, 9999.0, "FAKE-002"))
    assert len(plugin._director_queue) == first


def test_the_black_swan_ignores_a_trade_that_is_not_a_new_record(
    plugin: Any, ctx: Any
) -> None:
    """没刷新纪录的盈利**不触发**（否则每笔都崩，没法播）。"""
    _set_business_config(plugin, ctx, {"director": {"enabled": True, "force_black_swan": True}})
    run(plugin.join(mode="classic_arcade", seed=7304))
    session = _session(plugin)
    run(plugin._maybe_force_black_swan(session, 50.0, "FAKE-001"))
    plugin._director_queue.clear()
    run(plugin._maybe_force_black_swan(session, 10.0, "FAKE-002"))
    assert not _crashes(plugin), "没刷新纪录却崩了"


def test_the_black_swan_ignores_losses(plugin: Any, ctx: Any) -> None:
    """亏钱的卖出**不触发**（最上头的是赚，不是亏）。"""
    _set_business_config(plugin, ctx, {"director": {"enabled": True, "force_black_swan": True}})
    run(plugin.join(mode="classic_arcade", seed=7305))
    session = _session(plugin)
    run(plugin._maybe_force_black_swan(session, -100.0, "FAKE-001"))
    assert not _crashes(plugin)


def test_the_black_swan_is_silent_when_the_switch_is_off(plugin: Any, ctx: Any) -> None:
    """开关关闭 ⇒ 一条暴跌都不排。"""
    _set_business_config(plugin, ctx, {"director": {"enabled": True}})
    run(plugin.join(mode="classic_arcade", seed=7306))
    session = _session(plugin)
    run(plugin._maybe_force_black_swan(session, 500.0, "FAKE-001"))
    assert not _crashes(plugin)


def test_a_new_game_resets_the_black_swan_record(plugin: Any, ctx: Any) -> None:
    """新的一局 ⇒ 纪录归零（上一局的纪录不能挡住本局）。

    走**真实路径**：结算掉上一局 ⇒ 再开一局（join 不许连开，
    ``already_joined`` 是 P5 契约里的错误码）。
    """
    _set_business_config(plugin, ctx, {"director": {"enabled": True, "force_black_swan": True}})
    run(plugin.join(mode="classic_arcade", seed=7307))
    session = _session(plugin)
    run(plugin._maybe_force_black_swan(session, 50.0, "FAKE-001"))
    assert plugin._black_swan_fired is True
    run(plugin.settle())
    run(plugin.join(mode="classic_arcade", seed=7308))
    assert plugin._black_swan_fired is False
    assert plugin._black_swan_peak == 0.0


def test_the_black_swan_reuses_the_engine_crash_function() -> None:
    """暴跌复用引擎既有的 ``apply_jump`` —— 引擎层一行不改。"""
    source = (_PLUGIN_ROOT / "__init__.py").read_text(encoding="utf-8")
    assert "apply_jump" in source
    assert "engine.market" in source


# ══════════════════════════════════════════════════════════════════════
# ⑥ 进度可见（show_progress）
# ══════════════════════════════════════════════════════════════════════


def test_the_pure_version_payload_has_no_heat(plugin: Any, ctx: Any) -> None:
    """纯净版的载荷里**没有** heat（引擎明令局中隐藏）。"""
    run(plugin.join(mode="classic_arcade", seed=7310))
    payload = run(plugin.market_context())
    assert "director_progress" not in payload
    assert "heat" not in str(payload).lower()


def test_the_progress_block_shows_the_hype_value_and_the_chapter(
    plugin: Any, ctx: Any
) -> None:
    """开关打开 ⇒ 面板拿到上头值与当前章节。

    键名是 ``hype`` 而不是引擎字段名 —— 面板源码受 P5 契约 §4.1 的
    静态 lint 约束（``tests/test_hosted_tsx.py``：面板里不许出现
    ``heat`` / ``seed`` / ``reversal`` 这些字样），所以后端换了键名。
    **闸门在后端**：开关不开，这个键根本不存在。
    """
    _set_business_config(plugin, ctx, {"director": {"enabled": True, "show_progress": True}})
    run(plugin.join(mode="classic_arcade", seed=7311))
    progress = run(plugin.market_context())["director_progress"]
    assert "hype" in progress
    assert "chapter" in progress
    assert progress["chapter_total"] == 4


def test_the_panel_source_never_spells_the_secret_field(plugin: Any, ctx: Any) -> None:
    """面板源码里**不许**出现引擎内部字段名（含注释之外的正文）。

    这是 P5 契约 §4.1 的静态 lint（``tests/test_hosted_tsx.py`` 也在守），
    这里再钉一遍 —— 因为 P19.0 恰好踩到了这条线（导演进度要显示那个量）。
    """
    code = _panel_code()
    for field in ("heat", "seed", "reversal", "accuracy", "prey_index"):
        assert not re.search(rf"\b{field}\b", code), f"面板引用了局中秘密字段：{field}"


def test_the_progress_block_is_absent_when_only_the_director_is_on(
    plugin: Any, ctx: Any
) -> None:
    """只开导演台**不开** show_progress ⇒ 载荷里没有这个键。"""
    _set_business_config(plugin, ctx, {"director": {"enabled": True}})
    run(plugin.join(mode="classic_arcade", seed=7312))
    payload = run(plugin.market_context())
    assert "director_progress" not in payload
    assert "heat" not in str(payload).lower()


def test_the_panel_renders_the_progress_block_only_when_it_is_there() -> None:
    """面板**只在有值时**渲染这一块（开关关闭 ⇒ 与以前逐像素一样）。"""
    code = _panel_code()
    assert "state.director_progress" in code
    assert "director.progress.hype" in code
    assert "director.progress.chapter" in code
    assert "{state.director_progress ?" in code


def test_the_progress_block_adds_no_dangerous_css() -> None:
    """这一版**一行新 CSS 都不加**（只是多两枚徽标）。"""
    code = _panel_code()
    assert ".rrm-director-progress{" not in code.replace(" ", "")


# ══════════════════════════════════════════════════════════════════════
# ⑦ P18.0 留下的真缺陷：紧凑水位条漏了锚点 class
# ══════════════════════════════════════════════════════════════════════


def test_the_compact_gauge_also_carries_the_anchor_class() -> None:
    """紧凑水位条（P16.0 的前两关）**也要**带 ``rrm-anchor-gauge``。

    之前紧凑分支漏了这个 class ⇒ 街机 / 入门档的教程第 4 步
    **从来没圈到过任何东西**。
    """
    code = _panel_code()
    compact_at = code.index("if (props.compact)")
    assert "rrm-anchor-gauge" in code[compact_at : compact_at + 900], "紧凑分支漏了锚点 class"


def test_every_tutorial_anchor_exists_in_both_gauge_renderings() -> None:
    """``gauge`` 锚点在水位的**两种形态**里都在（紧凑 / 完整）。"""
    code = _panel_code()
    assert code.count("rrm-anchor-gauge") >= 2


# ══════════════════════════════════════════════════════════════════════
# ⑧ 旧作词清零
# ══════════════════════════════════════════════════════════════════════


def _iter_text_files() -> list[Path]:
    out: list[Path] = []
    for path in sorted(_PLUGIN_ROOT.rglob("*")):
        if not path.is_file():
            continue
        if any(part in _SKIP_DIRS for part in path.relative_to(_PLUGIN_ROOT).parts):
            continue
        if path.suffix.lower() not in _TEXT_SUFFIXES:
            continue
        out.append(path)
    return out


def test_no_legacy_brand_plaintext_anywhere_in_the_repository() -> None:
    """全仓（源码 / 测试 / 注释 / 词典 / 配置）**零明文**。"""
    offenders: list[str] = []
    for path in _iter_text_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        rel = path.relative_to(_PLUGIN_ROOT).as_posix()
        # base64 是随机字节，不是人写的字 —— 剥掉再扫。
        cleaned = _BASE64_RUN.sub(" ", text)
        for lineno, line in enumerate(cleaned.splitlines(), start=1):
            for term in _LEGACY_TERMS:
                if term in line:
                    offenders.append(f"{rel}:{lineno}: 命中 {len(term)} 字旧作词")
    assert not offenders, "仓库里还有旧作专有名词：\n" + "\n".join(offenders)


def test_the_readme_keeps_the_generic_disclaimer() -> None:
    """README 的免责声明是**通用说法**（不带任何具体名字）。"""
    readme = (_PLUGIN_ROOT / "README.md").read_text(encoding="utf-8")
    assert "灵感来自早期同类虚构作品" in readme
    assert "不使用任何已有作品的版权内容、角色名与视觉素材" in readme
    for term in _LEGACY_TERMS:
        assert term not in readme, term


# ══════════════════════════════════════════════════════════════════════
# ⑨ 红线
# ══════════════════════════════════════════════════════════════════════


def test_the_engine_and_mode_layers_are_untouched() -> None:
    """引擎层 / 模式层的**可执行代码**一个字节都不许改。"""
    assert engine_mode_code_changes() == []


def test_the_version_is_bumped_to_0_10_2() -> None:
    """版本号 = 0.10.5。"""
    from plugins.mock_stock_meow import PLUGIN_VERSION

    assert PLUGIN_VERSION == "0.10.5"
    toml = (_PLUGIN_ROOT / "plugin.toml").read_text(encoding="utf-8")
    assert 'version = "0.10.5"' in toml
