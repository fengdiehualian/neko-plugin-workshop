"""P5.5 体验打磨层的单测。

守四条线：

1. **改动 1（低保 → 打工工资）**：用户可见文案里**零个**「低保」；
   引擎的常量**逐字未动**（引擎零改动），但插件层改写后的称号不含「低保」。
2. **改动 2（入场三选一）**：三个身份都能落库；非法身份被拒；
   ``status`` 与**破产结算页**给出身份；**局中**载荷不含呼应句。
3. **改动 3（猫娘「她不说」）**：两个新变体在 i18n 里；
   选取**按 seed 稳定**；猫娘沉默旁白概率 30% 且只在有猫娘时出现。
4. **红线**：``engine/`` 与 ``modes/`` 零改动（``Player`` 没有 identity 字段、
   ``DEATH_TITLES`` 原样保留）；版本号 = 0.6.1。
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

from conftest import run
from storage.repo import Repo

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_I18N_PATH = _PLUGIN_ROOT / "i18n" / "zh-CN.json"
_PLUGIN_SOURCE = (_PLUGIN_ROOT / "__init__.py").read_text(encoding="utf-8")
_CARDS_SOURCE = (_PLUGIN_ROOT / "cards.py").read_text(encoding="utf-8")

#: 本次要清掉的词。
OLD_WORD = "低保"

#: 引擎常量里**应该仍有**的词（证明引擎没被改过 —— 靠插件层挡住玩家视线）。
ENGINE_OLD_WORD = "低保"


def _value(result: Any) -> dict[str, Any]:
    assert result.is_ok(), f"期望 Ok，实际 {result!r}"
    payload = result.unwrap()
    assert isinstance(payload, dict)
    return payload


def _error_code(result: Any) -> str:
    assert result.is_err(), f"期望 Err，实际 {result!r}"
    error = result.err()
    return str(getattr(error, "code", ""))


def _i18n() -> dict[str, str]:
    return json.loads(_I18N_PATH.read_text(encoding="utf-8"))


def _string_literals_without_docstrings(source: str) -> list[str]:
    """取出模块里**所有非 docstring** 的字符串字面量。

    用来区分"注释 / docstring 里可以出现"与"用户可见文案里不许出现"。
    """
    tree = ast.parse(source)
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                docstrings.add(id(body[0].value))
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    ]


# ---------------------------------------------------------------- ④ 红线


def test_engine_player_has_no_identity_field() -> None:
    """红线：``engine/models.py::Player`` **不能**有 identity 字段。

    P5.5 总约束写死「``engine/`` 一行不动」；identity 落在插件层的 ``Session`` 上。
    """
    from engine.models import Player

    assert not hasattr(Player("p", "n", 0.0, "classic_arcade"), "identity")
    assert "identity" not in {field for field in Player.__dataclass_fields__}


def test_engine_death_titles_are_untouched() -> None:
    """红线：引擎的 ``DEATH_TITLES`` **逐字未动**（仍含旧词）。

    玩家看不到它 —— 靠插件层按 ``death_reason`` 改写（见下面那条）。
    """
    from engine.settlement import DEATH_TITLES

    assert ENGINE_OLD_WORD in DEATH_TITLES["welfare_neglect"]


def test_plugin_overrides_death_title_by_reason() -> None:
    """插件层按 ``death_reason`` 改写称号 —— 改完**不含**旧词。"""
    from engine.models import Player
    from plugins.mock_stock_meow import MockStockMeowPlugin

    player = Player("p", "n", 0.0, "infinite")
    player.death_reason = "welfare_neglect"
    player.death_title = "连续七天没去领低保。制度不养闲人。"
    MockStockMeowPlugin._override_death_title(player)
    assert OLD_WORD not in (player.death_title or "")
    assert "工资" in (player.death_title or "")

    # **未知**破产原因**不**被改写（引擎那 5 种全部要覆盖，见 P14.1）。
    other = Player("p", "n", 0.0, "classic_arcade")
    other.death_reason = "not_a_real_reason"
    other.death_title = "交不起第三天的账单。"
    MockStockMeowPlugin._override_death_title(other)
    assert other.death_title == "交不起第三天的账单。"


def test_version_is_a_patch_bump() -> None:
    """版本号 = 0.10.5（P21.5：面板白屏根因修复 —— 持仓区块少一层 ``<Stack>`` 包裹导致 TSX 语法错误 + 新增纯 Python TSX 结构体检守卫；P21.0：体验减负 —— 渐进式 UI 三档密度 + 全屏沉浸 + 新手兜底幽灵提示 + 术语去金融化 + 名字限 6 字；P20.0：命名系统与名字彩蛋引擎；P19.0：入门档教程补全 + 直播特供版隐藏开关；P18.0：分档次动态教程；P16.0：前两关爽感重塑 + Yui 深度情绪感知；P15.0：原创化去品牌化；P14.1：去死亡化合规整改；P14.0：资金水位可视化 + 多标的仓位联动 + 模式引导重构；P13.0：Yui 灵魂化 + 交易去量子化；P12.0 是 0.9.6；P11.1：并发根治；P11.0 是 0.9.4；P10 是 0.9.3；P9.0 是 0.9.2；P8.1 是 0.9.1；P8.0 是 0.9.0；P7.4 是 0.8.4；P7.3 是 0.8.3；P7.2 是 0.8.2；P7.1 是 0.8.1；P7 是 0.8.0）。"""
    from plugins.mock_stock_meow import PLUGIN_VERSION

    assert PLUGIN_VERSION == "0.10.5"
    toml = (_PLUGIN_ROOT / "plugin.toml").read_text(encoding="utf-8")
    assert 'version = "0.10.5"' in toml


# ---------------------------------------------------------------- ① 改动 1


def test_i18n_has_no_old_word() -> None:
    """i18n 的**所有 value**里都不许有「低保」。"""
    offenders = [(key, value) for key, value in _i18n().items() if OLD_WORD in value]
    assert not offenders, f"i18n 里还留着「{OLD_WORD}」：{offenders}"


def test_plugin_layer_string_literals_have_no_old_word() -> None:
    """插件层的**字符串字面量**（非 docstring）里不许有「低保」。

    注释与 docstring 里允许 —— 那不是玩家能看到的字。
    """
    offenders = [text for text in _string_literals_without_docstrings(_PLUGIN_SOURCE) if OLD_WORD in text]
    assert not offenders, f"插件层还有可见的「{OLD_WORD}」：{offenders}"


def test_cards_layer_string_literals_have_no_old_word() -> None:
    """卡片层的字符串字面量里不许有「低保」。"""
    offenders = [text for text in _string_literals_without_docstrings(_CARDS_SOURCE) if OLD_WORD in text]
    assert not offenders, f"卡片层还有可见的「{OLD_WORD}」：{offenders}"


def test_welfare_card_speaks_in_worker_terms() -> None:
    """工资卡片：标题是「工资到账」，正文是打工叙事。"""
    from plugins.mock_stock_meow.cards import build_welfare_card

    card = build_welfare_card(cycle=2, amount=1050.0, interval_days=7)
    assert card.title == "工资到账"
    assert "打工" in card.body
    assert "1050" in card.body
    assert "够活 7 天" in card.body
    assert OLD_WORD not in card.body
    assert OLD_WORD not in card.summary
    assert OLD_WORD not in card.html

    claimed = build_welfare_card(cycle=2, amount=1050.0, already_claimed=True)
    assert "工资" in claimed.body
    assert OLD_WORD not in claimed.body


def test_daily_claim_entry_speaks_in_worker_terms(plugin) -> None:
    """面板按钮与入口名都改成「领工资」。"""
    meta = getattr(plugin.daily_claim, "__neko_ui_action__", None)
    assert meta is not None
    assert meta["label"] == "领工资"
    assert OLD_WORD not in meta["label"]

    run(plugin.join(mode="infinite", seed=9201))
    status = _value(run(plugin.status()))
    assert OLD_WORD not in repr(status)


def test_welfare_neglect_kill_title_is_rewritten_end_to_end() -> None:
    """端到端：引擎判 ``welfare_neglect`` → 插件层把称号改写成「工资」。

    走的是**真实**的日终结算（引擎判破产）+ 真实的插件层改写方法，
    不是拿字符串硬凑。
    """
    from engine.models import Player
    from engine.settlement import daily_settlement
    from plugins.mock_stock_meow import MockStockMeowPlugin

    player = Player(id="p1", name="玩家", cash=100000.0, mode="infinite")
    params = {
        "mode": "infinite",
        "living_cost": 0.0,
        "next_day_living_cost": 0.0,
        "strike_limit": 3,
        "weekly_relief": 1050.0,
        "relief_interval_days": 7,
        "welfare_enabled": True,
        "welfare_claim_limit_days": 7,
    }
    for _ in range(8):
        daily_settlement(player, **params)
        player.day += 1
    assert player.death_reason == "welfare_neglect"
    # 引擎给的称号里**有**旧词（引擎零改动）。
    assert OLD_WORD in (player.death_title or "")

    # 插件层改写之后 —— 玩家看到的是「工资」。
    MockStockMeowPlugin._override_death_title(player)
    assert OLD_WORD not in (player.death_title or "")
    assert "工资" in (player.death_title or "")


def test_welfare_summary_speaks_in_worker_terms(plugin) -> None:
    """结算页的工资小结用「工资」措辞。"""
    run(plugin.join(mode="infinite", seed=9202))
    engine_config = run(plugin._engine_config())
    from plugins.mock_stock_meow.storage.session import load_session

    session = run(load_session(plugin._repo, plugin.state, engine_config=engine_config))
    assert session is not None
    summary = plugin._welfare_summary(session, engine_config)
    assert "工资" in summary["note"]
    assert OLD_WORD not in summary["note"]


def test_welfare_already_claimed_error_speaks_in_worker_terms(plugin) -> None:
    """错误码文案改成「本周工资已领过」。"""
    run(plugin.join(mode="infinite", seed=9203))
    run(plugin.daily_claim())
    result = run(plugin.daily_claim())
    assert _error_code(result) == "welfare_already_claimed"
    assert "工资" in str(result.err())


# ---------------------------------------------------------------- ② 改动 2


def test_join_accepts_the_three_identities(plugin) -> None:
    """三个身份都能开局并落库。

    同一张 ``market_state`` 行只放得下一局，所以每个身份换一个干净的实例。
    """
    for identity in ("debt", "prove", "quick"):
        instance = _fresh_plugin(plugin)
        run(instance.join(mode="classic_arcade", seed=9301, identity=identity))
        row = run(
            Repo(instance.db).fetch_one("SELECT identity FROM players")
        )
        assert row is not None
        assert row["identity"] == identity


def test_join_defaults_to_quick(plugin) -> None:
    """默认身份是 ``quick``（不想打工的那一类）。"""
    run(plugin.join(mode="classic_arcade", seed=9302))
    row = run(Repo(plugin.db).fetch_one("SELECT identity FROM players"))
    assert row is not None
    assert row["identity"] == "quick"


def test_join_rejects_an_unknown_identity(plugin) -> None:
    """非法身份 → ``invalid_identity``。"""
    result = run(plugin.join(mode="classic_arcade", seed=9303, identity="invalid"))
    assert _error_code(result) == "invalid_identity"


def test_join_response_carries_the_identity_and_its_line(plugin) -> None:
    """``join`` 的返回体带身份与开场台词。"""
    payload = _value(run(plugin.join(mode="classic_arcade", seed=9304, identity="debt")))
    assert payload["identity"] == "debt"
    assert payload["identity_intro"] == "家里欠了一笔钱。我想帮上忙。"


def test_status_returns_the_identity(plugin) -> None:
    """``status`` 返回体含 ``identity``（但**不含**呼应句）。"""
    run(plugin.join(mode="classic_arcade", seed=9305, identity="prove"))
    payload = _value(run(plugin.status()))
    assert payload["identity"] == "prove"
    assert "identity_echo" not in payload


def test_panel_payload_never_carries_the_identity_echo(plugin) -> None:
    """**局中**载荷里没有身份呼应句（呼应句只在破产结算页）。"""
    run(plugin.join(mode="classic_arcade", seed=9306, identity="debt"))
    engine_config = run(plugin._engine_config())
    session, error = run(plugin._session_or_error(engine_config))
    assert error is None
    assert session is not None
    payload = plugin._panel_payload(session, engine_config)
    assert "identity_echo" not in payload
    assert "identity" not in payload


def test_settlement_payload_carries_the_identity_echo(plugin) -> None:
    """破产结算页：三个身份 → 三句不同的呼应句。"""
    echoes: dict[str, str] = {}
    for identity in ("debt", "prove", "quick"):
        instance = _fresh_plugin(plugin)
        run(instance.join(mode="classic_arcade", seed=9307, identity=identity))
        # 直接把玩家改成破产（模拟被强制平仓），再走结算报告。
        run(
            Repo(instance.db).transaction(
                [
                    (
                        "UPDATE players SET alive = 0, death_reason = 'liquidated', "
                        "death_title = '二道贩子按回收价收走了你的筹码。'",
                        (),
                    )
                ]
            )
        )
        engine_config = run(instance._engine_config())
        from plugins.mock_stock_meow.storage.session import load_session

        session = run(load_session(instance._repo, instance.state, engine_config=engine_config))
        assert session is not None
        payload = run(instance._build_settlement_payload(session, engine_config))
        echoes[identity] = payload["identity_echo"]
        assert payload["identity"] == identity

    assert len(set(echoes.values())) == 3, f"三个身份的呼应句应各不相同：{echoes}"
    assert echoes["debt"] == "你入场时说想帮家里还债。现在家里要帮你还债了。"
    assert echoes["prove"] == "你入场时说想证明自己。你证明了：满仓的人，清盘得最快。"
    assert echoes["quick"] == "你入场时说想快点赚到钱。你确实很快——快到连饭都忘了吃。"


def test_survivor_settlement_has_no_identity_echo(plugin) -> None:
    """活到终局（没破产）→ **不给**身份呼应句。"""
    run(plugin.join(mode="classic_arcade", seed=9308, identity="debt"))
    engine_config = run(plugin._engine_config())
    from plugins.mock_stock_meow.storage.session import load_session

    session = run(load_session(plugin._repo, plugin.state, engine_config=engine_config))
    assert session is not None
    payload = run(plugin._build_settlement_payload(session, engine_config))
    assert payload["identity_echo"] == ""


def test_kill_card_carries_the_identity_echo(plugin) -> None:
    """聊天结算卡片也带身份呼应句。"""
    from plugins.mock_stock_meow.cards import build_kill_card

    card = build_kill_card(
        death_reason="liquidated",
        death_title="出局",
        days_survived=12,
        total_asset=3.5,
        identity_echo="你入场时说想帮家里还债。现在家里要帮你还债了。",
    )
    assert "你入场时说想帮家里还债" in card.body
    assert card.payload["identity_echo"] == "你入场时说想帮家里还债。现在家里要帮你还债了。"


# ---------------------------------------------------------------- ③ 改动 3


def test_two_new_taunt_variants_exist() -> None:
    """两个新增的留白变体写进了 i18n。"""
    table = _i18n()
    assert table["taunt.cash_kill.4"] == "我见过很多人像你一样。他们后来怎样了？我不说。"
    assert table["taunt.catgirl_dead.4"] == "你别问我以前亏过多少。我不说。"


def test_taunt_variants_grew_to_four() -> None:
    """两个触发点各 4 个变体（加了留白，但**不加新触发点**）。"""
    from plugins.mock_stock_meow.cards import TAUNT_TRIGGERS, taunt_variants

    assert len(taunt_variants("cash_kill")) == 4
    assert len(taunt_variants("catgirl_dead")) == 4
    # 触发点数量不变（5 个）。
    assert len(TAUNT_TRIGGERS) == 5


def test_taunt_pick_is_stable_per_seed() -> None:
    """同一个 seed 必出同一句（与既有机制一致）。"""
    from plugins.mock_stock_meow.cards import pick_taunt

    for seed in (11, 222, 3333, 44444):
        first = pick_taunt("cash_kill", seed)
        second = pick_taunt("cash_kill", seed)
        assert first == second
        assert first


def test_catgirl_silence_is_stable_and_about_thirty_percent() -> None:
    """猫娘沉默旁白：按 seed 稳定，概率 ≈30%。"""
    from plugins.mock_stock_meow.cards import pick_catgirl_silence

    seeds = list(range(1, 1001))
    hits = sum(1 for seed in seeds if pick_catgirl_silence(seed))
    ratio = hits / len(seeds)
    assert 0.25 <= ratio <= 0.35, f"概率偏离 30% 太多：{ratio:.3f}"
    for seed in seeds[:50]:
        assert pick_catgirl_silence(seed) == pick_catgirl_silence(seed)


def test_catgirl_silence_needs_a_catgirl(plugin) -> None:
    """**没有猫娘**的局（经典模式）→ 不给旁白。"""
    run(plugin.join(mode="classic_arcade", seed=9401))
    engine_config = run(plugin._engine_config())
    from plugins.mock_stock_meow.storage.session import load_session

    session = run(load_session(plugin._repo, plugin.state, engine_config=engine_config))
    assert session is not None
    assert session.catgirl is None
    payload = run(plugin._build_settlement_payload(session, engine_config))
    assert payload["catgirl_silence"] == ""


def test_catgirl_silence_appears_in_an_infinite_settlement(plugin) -> None:
    """无限模式（有猫娘）→ 旁白由 seed 稳定决定，且结算页真的有这个字段。"""
    run(plugin.join(mode="infinite", seed=9402))
    engine_config = run(plugin._engine_config())
    from plugins.mock_stock_meow.storage.session import load_session

    session = run(load_session(plugin._repo, plugin.state, engine_config=engine_config))
    assert session is not None
    assert session.catgirl is not None
    payload = run(plugin._build_settlement_payload(session, engine_config))
    # 有猫娘 → 字段存在（要么是旁白句，要么是空串，取决于 seed）。
    assert "catgirl_silence" in payload
    if payload["catgirl_silence"]:
        assert payload["catgirl_silence"] == "猫娘站在旁边，什么都没说。"


def test_inaction_worker_narrative_exists() -> None:
    """「无为而治」的打工叙事写进了 i18n。"""
    assert (
        _i18n()["settlement.inaction_worker"]
        == "你每周打工、交生活费、不碰股市。刚好活下来。"
    )


# ---------------------------------------------------------------- 工具


def _fresh_plugin(plugin: Any) -> Any:
    """复制出一个"干净"的新实例（换个临时库），避免同库多局互相干扰。

    结算测试里要连续开三局不同的身份 —— 同一张 ``market_state`` 行放不下。
    """
    import tempfile
    from pathlib import Path as _Path

    from conftest import FakeCtx, make_plugin

    root = _Path(tempfile.mkdtemp(prefix="jmm-p55-"))
    import os

    os.environ["NEKO_STORAGE_SELECTED_ROOT"] = str(root / "storage")
    instance = make_plugin(FakeCtx(config_path=root / "plugin.toml"))
    result = run(instance.on_startup())
    assert result.is_ok()
    return instance
