"""P4 · 模式层（``modes/``）单测。

守三条线：

1. **分层铁律**：``modes/`` 绝不 import 任何 ``plugin.*``（源码扫描）——
   模式层不认识 N.E.K.O 的存在。
2. **注册表**：五个档位都能拿到对应的模式对象，未知档位返回 ``None``。
3. **两个模式的开局**：经典模式预计划反转日；无限模式发放基准股票。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from conftest import run
from plugins.mock_stock_meow.modes import (
    MODES,
    ClassicMode,
    InfiniteMode,
    ModeProtocol,
    get_mode,
)
from plugins.mock_stock_meow.modes.shared import ModeContext, TickOutcome
from plugins.mock_stock_meow.storage.repo import Repo

_MODES_ROOT = Path(__file__).resolve().parents[1] / "modes"


# ---------------------------------------------------------------- 分层铁律


def test_modes_package_has_the_four_contract_files() -> None:
    """契约 §2.1：``modes/`` 由这四个文件组成。"""
    names = {path.name for path in _MODES_ROOT.glob("*.py")}
    assert names == {"__init__.py", "shared.py", "classic.py", "infinite.py"}


def test_modes_never_import_plugin_packages() -> None:
    """模式层不认识 N.E.K.O：源码里不许出现 ``plugin.*`` 的 import。

    同时禁止**第三方**绝对导入：模式层只依赖标准库与自己的 ``..engine``。
    """
    import sys

    stdlib = set(sys.stdlib_module_names)
    offenders: list[str] = []
    for path in sorted(_MODES_ROOT.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split(".")[0]
                    if root == "plugin":
                        offenders.append(f"{path.name}: import {alias.name}")
                    elif root not in stdlib:
                        offenders.append(f"{path.name}: 第三方导入 {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                root = (node.module or "").split(".")[0]
                if root == "plugin":
                    offenders.append(f"{path.name}: from {node.module} import ...")
                elif node.level == 0 and root not in stdlib:
                    offenders.append(f"{path.name}: 第三方导入 {node.module}")
    assert offenders == [], offenders


def test_modes_only_use_relative_engine_imports() -> None:
    """模式层只能 import 自己的 ``..engine``（引擎层内部是相对导入）。

    * ``from .xxx`` —— 同包内的模块（classic / infinite / shared）
    * ``from ..engine`` —— 引擎层
    """
    own = {"classic", "infinite", "shared", ""}
    for path in sorted(_MODES_ROOT.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or node.level == 0:
                continue
            module = (node.module or "").split(".")[0]
            if node.level == 1:
                assert module in own, f"{path.name} 同包导入了 {node.module}"
            else:
                assert module == "engine", f"{path.name} 越层导入了 {node.module}"


def test_modes_do_not_touch_database_or_io() -> None:
    """模式层不碰数据库、不做 IO。"""
    banned = ("sqlite3", "open(", "Path(", "os.", "io.", "shutil")
    for path in sorted(_MODES_ROOT.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        for token in banned:
            assert token not in source, f"{path.name} 里出现了 {token}"


# ---------------------------------------------------------------- 注册表


def test_registry_covers_all_five_modes() -> None:
    assert set(MODES) == {
        "classic_arcade",
        "classic_beginner",
        "classic_advanced",
        "classic_hell",
        "infinite",
    }


def test_classic_modes_share_one_instance() -> None:
    """经典四档共用一套流程，差异全在 DifficultyConfig 里。"""
    for name in ("classic_arcade", "classic_beginner", "classic_advanced", "classic_hell"):
        assert isinstance(get_mode(name), ClassicMode)


def test_infinite_mode_is_its_own_class() -> None:
    assert isinstance(get_mode("infinite"), InfiniteMode)


def test_unknown_mode_returns_none() -> None:
    assert get_mode("classic_novice") is None
    assert get_mode("不存在的模式") is None


@pytest.mark.parametrize("name", sorted(MODES))
def test_every_mode_satisfies_the_protocol(name: str) -> None:
    """四个方法一个都不能少。"""
    mode = get_mode(name)
    assert mode is not None
    assert isinstance(mode, ModeProtocol)
    for method in ("setup", "tick", "daily_settlement", "is_finished"):
        assert callable(getattr(mode, method))


# ---------------------------------------------------------------- 两个模式的开局


def test_classic_setup_plans_reversal_days(plugin) -> None:
    """经典模式开局预计划 1~3 个反转日。"""
    run(plugin.join(mode="classic_beginner", seed=101))
    repo = Repo(plugin.db)
    row = run(repo.fetch_one("SELECT reversal_days FROM market_state WHERE id = 1"))
    assert row["reversal_days"] is not None
    import json

    days = json.loads(row["reversal_days"])
    assert 1 <= len(days) <= 3
    assert all(1 <= day <= 30 for day in days)


def test_classic_setup_generates_day_one_news(plugin) -> None:
    """开局当天就有新闻（开盘前生成）。"""
    run(plugin.join(mode="classic_beginner", seed=102))
    repo = Repo(plugin.db)
    rows = run(repo.fetch_all("SELECT * FROM news_log WHERE day = 1"))
    assert len(rows) == 3


def test_infinite_setup_grants_baseline_stock(plugin) -> None:
    """无限模式：现金 0 + 基准股票（市值 ≈ 7 天生活费）。"""
    payload = run(plugin.join(mode="infinite", seed=103)).unwrap()
    assert payload["initial_cash"] == 0.0
    assert "baseline_stock" in payload

    repo = Repo(plugin.db)
    rows = run(repo.fetch_all("SELECT * FROM positions"))
    assert len(rows) == 1
    row = rows[0]
    market = run(plugin.market()).unwrap()
    price = next(
        item["price"] for item in market["stocks"] if item["id"] == row["stock_id"]
    )
    # 成本价 = 市价（不送便宜）
    assert row["avg_cost"] == pytest.approx(price)
    # 市值 ≈ 1050
    assert row["amount"] * price == pytest.approx(1050.0, rel=0.01)


def test_infinite_baseline_stock_is_the_most_volatile_one(plugin) -> None:
    """基准股票从波动率最高的标的里挑。"""
    run(plugin.join(mode="infinite", seed=104))
    repo = Repo(plugin.db)
    rows = run(repo.fetch_all("SELECT * FROM positions"))
    stock_id = rows[0]["stock_id"]

    from engine.config import default_engine_config
    from plugins.mock_stock_meow.storage.session import load_session

    session = run(
        load_session(repo, plugin.state, engine_config=default_engine_config())
    )
    sigmas = {
        stock_id_: stock.sigma for stock_id_, stock in session.market.stocks.items()
    }
    assert sigmas[stock_id] == max(sigmas.values())


def test_infinite_player_starts_with_zero_cash(plugin) -> None:
    """无限模式玩家开局现金是 0（猫娘才有本金）。"""
    run(plugin.join(mode="infinite", seed=105))
    repo = Repo(plugin.db)
    player = run(repo.fetch_one("SELECT cash FROM players WHERE is_catgirl = 0"))
    catgirl = run(repo.fetch_one("SELECT cash FROM players WHERE is_catgirl = 1"))
    assert player["cash"] == 0.0
    assert catgirl["cash"] == 1050.0


# ---------------------------------------------------------------- 调度


def test_tick_dispatches_to_the_mode_layer(plugin) -> None:
    """插件层的 tick 只是调度：跑完之后行情确实推进了。"""
    run(plugin.join(mode="classic_beginner", seed=106))
    before = run(plugin.market()).unwrap()
    run(plugin._run_one_tick())
    after = run(plugin.market()).unwrap()
    assert after["tick"] == before["tick"] + 1


def test_mode_context_carries_the_session_state(plugin) -> None:
    """ModeContext 拿得到玩家、市场与配置。"""
    from engine.config import default_engine_config
    from engine.models import MarketState, Player

    ctx = ModeContext(
        player=Player(id="p", name="玩家", cash=1.0, mode="classic_beginner"),
        market=MarketState(seed=1),
        difficulty=default_engine_config().difficulties["classic_beginner"],
        engine_config=default_engine_config(),
        mode="classic_beginner",
    )
    assert ctx.ticks_per_day == 12
    assert isinstance(TickOutcome(), TickOutcome)


# ---------------------------------------------------------------- 经典四档


def test_classic_tiers_keep_their_own_rules() -> None:
    """四档的差异全在 ``DifficultyConfig`` 里：街机无生活费、进阶 T+1、地狱 10 倍杠杆。"""
    from engine.config import default_engine_config

    difficulties = default_engine_config().difficulties
    arcade = difficulties["classic_arcade"]
    beginner = difficulties["classic_beginner"]
    advanced = difficulties["classic_advanced"]
    hell = difficulties["classic_hell"]

    assert arcade.has_living_cost is False
    assert beginner.has_living_cost is True
    assert advanced.t_plus_one is True
    assert hell.leverage == 10.0
    assert hell.harvest_trigger == "prey_index"
    # 四档共用同一套经典模式流程
    for name in ("classic_arcade", "classic_beginner", "classic_advanced", "classic_hell"):
        assert isinstance(get_mode(name), ClassicMode)


def test_all_classic_tiers_auto_finish_at_total_game_days() -> None:
    """四档都在第 30 个游戏日自动终局。"""
    from engine.config import default_engine_config
    from engine.models import MarketState, Player
    from plugins.mock_stock_meow.modes.shared import ModeContext

    config = default_engine_config()
    for name in ("classic_arcade", "classic_beginner", "classic_advanced", "classic_hell"):
        mode = get_mode(name)
        ctx = ModeContext(
            player=Player(id="p", name="玩家", cash=0.0, mode=name, day=30),
            market=MarketState(seed=1),
            difficulty=config.difficulties[name],
            engine_config=config,
            mode=name,
        )
        assert mode.is_finished(ctx) is False
        ctx.player.day = 31
        assert mode.is_finished(ctx) is True


def test_infinite_mode_never_finishes() -> None:
    """无限模式没有终点。"""
    from engine.config import default_engine_config
    from engine.models import MarketState, Player
    from plugins.mock_stock_meow.modes.shared import ModeContext

    config = default_engine_config()
    mode = get_mode("infinite")
    ctx = ModeContext(
        player=Player(id="p", name="玩家", cash=0.0, mode="infinite", day=9999),
        market=MarketState(seed=1),
        difficulty=config.difficulties["infinite"],
        engine_config=config,
        mode="infinite",
    )
    assert mode.is_finished(ctx) is False


# ---------------------------------------------------------------- 派生键冻结


def test_market_derivation_key_is_frozen() -> None:
    """P4 起**行情派生键冻结**：``derive_stream_rng(seed, STREAM_MARKET, day, tick, index)``。

    任何改动都必须走版本迁移 —— 这里用源码扫描守住它。
    """
    import ast

    source = (
        Path(__file__).resolve().parents[1] / "engine" / "market.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "derive_stream_rng"
    ]
    price_calls = [
        call
        for call in calls
        if len(call.args) >= 4 and isinstance(call.args[1], ast.Name)
    ]
    assert price_calls, "没找到行情派生调用"
    streams = {call.args[1].id for call in price_calls}
    # 行情层只允许两条流：行情流与退市流 —— 多一条就意味着派生键变了
    assert streams == {"STREAM_MARKET", "STREAM_DELISTING"}, streams
    # 行情流的流 ID 必须还是 100001
    from engine.config import STREAM_MARKET

    assert STREAM_MARKET == 100001
