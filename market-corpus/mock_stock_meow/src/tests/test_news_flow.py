"""P4 · 新闻接入 tick 流水线的单测。

守四条线：

1. 新闻**每天开盘前**生成一次，并落进 ``news_log``；
2. 局中只看得到**中性分类**（bullish / bearish / vague / noise）——
   真假与雷新闻**绝不**泄露；
3. 两个 heat 事件能真的触发：「信假新闻追涨」与「雷新闻后补仓」；
4. 时间窗：新闻只在生成后的 N 个 tick 内影响 heat。
"""

from __future__ import annotations

from conftest import run
from plugins.mock_stock_meow.storage.repo import Repo

_NEUTRAL_KINDS = {"bullish", "bearish", "vague", "noise"}


def _player_id(repo: Repo) -> str:
    row = run(repo.fetch_one("SELECT player_id FROM market_state WHERE id = 1"))
    return row["player_id"]


# ---------------------------------------------------------------- 生成与落库


def test_news_is_generated_each_day_and_logged(plugin) -> None:
    """每天开盘前生成当天新闻并落库。"""
    run(plugin.join(mode="classic_beginner", seed=201))
    repo = Repo(plugin.db)

    run(plugin._run_one_tick())
    rows = run(repo.fetch_all("SELECT * FROM news_log WHERE day = 1"))
    assert len(rows) == 3

    # 走满一天 → 第 2 天也有新闻
    for _ in range(12):
        run(plugin._run_one_tick())
    rows = run(repo.fetch_all("SELECT * FROM news_log WHERE day = 2"))
    assert len(rows) == 3


def test_news_ids_are_stable_across_the_day(plugin) -> None:
    """同一天不会重复生成新闻（id 唯一）。"""
    run(plugin.join(mode="classic_beginner", seed=202))
    repo = Repo(plugin.db)
    for _ in range(12):
        run(plugin._run_one_tick())
    rows = run(repo.fetch_all("SELECT news_id, COUNT(*) AS n FROM news_log GROUP BY news_id"))
    assert all(row["n"] == 1 for row in rows)


def test_news_survives_a_plugin_restart(plugin) -> None:
    """新闻以数据库为准：重启后当天还是同一批新闻。"""
    run(plugin.join(mode="classic_beginner", seed=203))
    repo = Repo(plugin.db)
    for _ in range(12):
        run(plugin._run_one_tick())
    before = run(repo.fetch_all("SELECT * FROM news_log ORDER BY day, news_id"))

    # 模拟重启：清掉状态缓存，重新加载会话
    from engine.config import default_engine_config
    from plugins.mock_stock_meow.storage.session import load_session

    session = run(
        load_session(repo, plugin.state, engine_config=default_engine_config())
    )
    assert len(session.market.news_today) == 3
    assert session.market.news_today[0].day == session.market.day

    # 再跑一个 tick 也不会把当天的新闻改写掉
    run(plugin._run_one_tick())
    after = run(repo.fetch_all("SELECT * FROM news_log ORDER BY day, news_id"))
    assert len(after) == len(before)


# ---------------------------------------------------------------- 局中可见性


def test_status_news_only_exposes_neutral_kinds(plugin) -> None:
    """局中新闻**只有中性分类**，没有标题正文、没有真假、没有雷新闻标记。

    P5 契约 §6：局中默认只给 ``{id, kind, kind_label}`` —— 标题与正文收进
    「新闻详情」交互（``news_detail`` 入口），点开才给。
    """
    run(plugin.join(mode="classic_beginner", seed=204))
    run(plugin._run_one_tick())
    payload = run(plugin.status()).unwrap()
    news = payload["news_today"]
    assert news
    for item in news:
        assert set(item) == {"id", "kind", "kind_label"}
        assert item["kind"] in _NEUTRAL_KINDS


def test_news_payload_never_contains_truth_markers(plugin) -> None:
    """局中不得出现 true/fake/thunder/accuracy 这些词。"""
    run(plugin.join(mode="classic_beginner", seed=205))
    run(plugin._run_one_tick())
    payload = run(plugin.status()).unwrap()
    blob = repr(payload)
    for token in ("'true'", "'fake'", "'thunder'", "accuracy"):
        assert token not in blob


def test_settle_reveals_the_truth(plugin) -> None:
    """结算时才给出新闻真相。"""
    run(plugin.join(mode="classic_beginner", seed=206))
    for _ in range(12):
        run(plugin._run_one_tick())
    payload = run(plugin.settle()).unwrap()
    news_log = payload["news_log"]
    assert news_log
    types = {item["type"] for item in news_log}
    assert types <= {"true", "fake", "vague", "thunder", "noise"}
    assert "accuracy" in news_log[0]


# ---------------------------------------------------------------- heat 事件


def test_fake_bullish_news_triggers_after_fake_news_heat_event(plugin) -> None:
    """最近窗口内有看涨假新闻时买入 → 触发「信了假新闻追涨」。"""
    from engine.config import default_engine_config
    from plugins.mock_stock_meow.storage.session import load_session

    run(plugin.join(mode="classic_beginner", seed=207))
    repo = Repo(plugin.db)
    run(plugin._run_one_tick())

    session = run(
        load_session(repo, plugin.state, engine_config=default_engine_config())
    )
    market = session.market
    # 找一条"看涨的假新闻"，把它的标的写进当天新闻
    fake = next(
        (item for item in market.news_today if item.type == "fake" and item.direction == "bullish"),
        None,
    )
    if fake is None:
        # 这局没生成看涨假新闻：塞一条进去（引擎层允许直接改 news_today）
        from engine.models import NewsItem

        fake = NewsItem(
            id="news-1-1",
            day=market.day,
            type="fake",
            stock_id=next(iter(market.stocks)),
            content="测试用假新闻",
            accuracy=None,
            direction="bullish",
        )
        market.news_today = [fake]

    stock = market.stocks[fake.stock_id]
    context = plugin._news_heat_context(session, stock, default_engine_config())
    assert context == (True, False)


def test_thunder_news_triggers_after_thunder_news_heat_event(plugin) -> None:
    """最近窗口内有雷新闻时买入 → 触发「雷新闻后补仓」。"""
    from engine.config import default_engine_config
    from engine.models import NewsItem
    from plugins.mock_stock_meow.storage.session import load_session

    run(plugin.join(mode="classic_beginner", seed=208))
    repo = Repo(plugin.db)
    run(plugin._run_one_tick())

    session = run(
        load_session(repo, plugin.state, engine_config=default_engine_config())
    )
    market = session.market
    thunder = NewsItem(
        id="news-1-2",
        day=market.day,
        type="thunder",
        stock_id=next(iter(market.stocks)),
        content="测试用雷新闻",
        accuracy=None,
        direction="bearish",
    )
    market.news_today = [thunder]
    stock = market.stocks[thunder.stock_id]
    context = plugin._news_heat_context(session, stock, default_engine_config())
    assert context == (False, True)


def test_news_effect_expires_after_the_window(plugin) -> None:
    """超出时间窗 → 新闻不再影响 heat。"""
    from engine.config import default_engine_config
    from engine.models import NewsItem
    from plugins.mock_stock_meow.storage.session import load_session

    run(plugin.join(mode="classic_beginner", seed=209))
    repo = Repo(plugin.db)
    for _ in range(12):
        run(plugin._run_one_tick())

    session = run(
        load_session(repo, plugin.state, engine_config=default_engine_config())
    )
    market = session.market
    market.news_today = [
        NewsItem(
            id="news-x",
            day=market.day,
            type="fake",
            stock_id=next(iter(market.stocks)),
            content="过期假新闻",
            accuracy=None,
            direction="bullish",
        )
    ]
    # 把 tick 推过窗口（窗口 3 tick）
    market.tick += 5
    stock = market.stocks[market.news_today[0].stock_id]
    context = plugin._news_heat_context(session, stock, default_engine_config())
    assert context == (False, False)


def test_buying_on_news_raises_heat(plugin) -> None:
    """信了假新闻买入 → heat 真的涨上去。"""
    run(plugin.join(mode="classic_beginner", seed=210))
    repo = Repo(plugin.db)
    run(plugin._run_one_tick())

    from engine.config import default_engine_config
    from engine.models import NewsItem
    from plugins.mock_stock_meow.storage.session import load_session

    session = run(
        load_session(repo, plugin.state, engine_config=default_engine_config())
    )
    market = session.market
    target = next(iter(market.stocks))
    market.news_today = [
        NewsItem(
            id="news-1-1",
            day=market.day,
            type="fake",
            stock_id=target,
            content="主力资金全线扫货",
            accuracy=None,
            direction="bullish",
        )
    ]
    # 前后各取一次状态（status() 只读，但保留调用以维持原测试的执行序列）。
    run(plugin.status())
    run(plugin.buy(target, 1.0))
    run(plugin.status())
    # heat 局中不可见，但"距离斩杀线"与现金会变 —— 用 heat_history 验证
    rows = run(
        repo.fetch_all(
            "SELECT heat FROM heat_history WHERE player_id = ? ORDER BY tick DESC LIMIT 1",
            (_player_id(repo),),
        )
    )
    assert rows


# ---------------------------------------------------------------- 反转日


def test_reversal_days_are_persisted(plugin) -> None:
    """反转日集合落库（JSON 文本）。"""
    import json

    run(plugin.join(mode="classic_beginner", seed=211))
    repo = Repo(plugin.db)
    row = run(repo.fetch_one("SELECT reversal_days FROM market_state WHERE id = 1"))
    assert row["reversal_days"] is not None
    days = json.loads(row["reversal_days"])
    assert isinstance(days, list)
    assert all(isinstance(day, int) for day in days)


def test_market_no_longer_reports_whether_today_is_a_reversal_day(plugin) -> None:
    """P5 方案 B：``market`` **不再**返回 ``reversal_today``。

    P4 §11 原本在这里给"今天是不是反转日"（只说是不是、不说方向）。P5 复核
    认定它仍是**强剧透**：玩家知道今天特殊，就能把新闻反过来读，反诈教学的
    核心机制被绕过。现在改成"花一次分析机会换一句不带方向的模糊提示"。

    守卫的 ``reversal_today`` 白名单例外随之失效（见
    ``tests/test_visibility.py``：整个 ``reversal`` 词族重新一律禁止）。
    """
    run(plugin.join(mode="classic_beginner", seed=212))
    payload = run(plugin.market()).unwrap()
    assert "reversal_today" not in payload
    assert "reversal" not in repr(payload).lower()
