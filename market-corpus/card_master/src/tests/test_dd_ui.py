"""斗地主浏览器面板（Hosted UI）的纯逻辑测试：状态组装、出牌核心、UI 入口。"""

import asyncio
import threading

from doudizhu import DouDizhuGame, card_str


def _plugin_with_game(landlord_idx: int = 0):
    """构造插件实例并开一局小游戏（不依赖真实运行环境）。

    注意：CardMasterPlugin 在函数内导入——插件包相对宿主树导入，
    顶层导入会因 isort 的 first-party 判定在本地/CI 之间产生分歧。
    """
    from plugin.plugins.card_master import CardMasterPlugin

    inst = CardMasterPlugin.__new__(CardMasterPlugin)
    inst._lock = threading.RLock()
    inst._game = None
    inst._game_log = []
    inst._live_sessions = {}
    inst._dd_game = None
    inst._dd_stats = {"wins": 0, "losses": 0, "role": "auto"}
    g = DouDizhuGame(["你", "YUI_左", "YUI_右"])
    g._set_landlord(landlord_idx, 2)
    inst._dd_game = g
    inst._dd_stats["role"] = "landlord" if landlord_idx == 0 else "farmer"
    return inst


def _run(coro):
    return asyncio.run(coro)


def test_ui_state_shape():
    """_dd_ui_state 组装面板渲染数据：字段齐全、must_play 正确、牌为可读字符串。"""
    inst = _plugin_with_game(landlord_idx=0)
    st = inst._dd_ui_state(inst._dd_game)
    for key in ("stage", "user_role", "user_hand", "bottom", "last_play",
                "last_player", "players", "current", "must_play", "winner", "stats"):
        assert key in st
    # 你是地主先手且无上一手 → must_play
    assert st["must_play"] is True
    assert st["user_role"] == "地主"
    assert len(st["players"]) == 3
    assert st["current"] == "你"
    assert len(st["user_hand"]) == 20
    # 手牌元素必须是可读字符串（如 '3♠'）
    assert all(isinstance(c, str) and c for c in st["user_hand"])
    assert st["stats"] == {"wins": 0, "losses": 0}


def test_ui_state_must_play_after_play():
    """出过牌（有 last_play）后不再 must_play，last_play 记录上一手。"""
    inst = _plugin_with_game(landlord_idx=0)
    g = inst._dd_game
    first = g.players[0]["hand"][0]
    r = g.play(0, [first])
    assert r.get("ok")
    st = inst._dd_ui_state(g)
    assert st["must_play"] is False
    assert st["last_play"] and all(isinstance(c, str) for c in st["last_play"])
    assert st["last_player"] == "你"


def test_ui_context_none_and_active():
    """dd_ui_context：无局返回 stage=none，有局返回完整 state。"""
    inst = _plugin_with_game(landlord_idx=1)
    ctx = inst.dd_ui_context()
    assert ctx["stage"] == "playing"
    assert ctx["user_role"] == "农民"
    # 无局
    inst2 = _plugin_with_game()
    inst2._dd_game = None
    assert inst2.dd_ui_context() == {"stage": "none"}


def test_ui_play_advances_to_user():
    """面板出牌：你出牌后插件自动推进 AI 直到轮到你或结束。"""
    inst = _plugin_with_game(landlord_idx=0)
    g = inst._dd_game
    first = g.players[0]["hand"][0]
    out = inst._dd_ui_do_play(card_str(first))
    assert out.get("is_error") is False, out
    assert "state" in out
    st = out["state"]
    assert st["stage"] in ("playing", "over")
    if st["stage"] == "playing":
        assert st["current"] == "你"  # 必须已推进到轮到你
    # last_play 应有牌（你出的或 AI 跟的）
    assert st["last_play"]


def test_ui_play_win_updates_stats():
    """面板出牌：你出完最后一张直接获胜 → 战绩更新。"""
    inst = _plugin_with_game(landlord_idx=0)
    g = inst._dd_game
    g.players[0]["hand"] = [3 * 4, 3 * 4 + 1]
    g.players[1]["hand"] = [5 * 4]
    g.players[2]["hand"] = [6 * 4]
    g.last_play = []
    g.last_player = None
    g.current = 0
    wins_before = inst._dd_stats["wins"]
    out = inst._dd_ui_do_play("3♠ 3♥")
    assert out.get("is_error") is False, out
    assert g.stage == "over"
    assert inst._dd_stats["wins"] == wins_before + 1
    assert out["state"]["stage"] == "over"
    assert out["state"]["winner"] == "你"


def test_ui_pass_no_crash():
    """面板'不要'：轮到用户且有上一手时正常流转，不抛错。"""
    inst = _plugin_with_game(landlord_idx=0)
    g = inst._dd_game
    # 制造'你'需要跟的局面
    g.current = 0
    g.last_play = [g.players[0]["hand"][0]]
    g.last_player = 1
    out = inst._dd_ui_do_play("不要")
    assert "state" in out
    assert inst._dd_game is g


def test_ui_play_no_game_and_wrong_turn():
    """面板出牌边界：无局 / 未轮到你时给出可理解的返回。"""
    inst = _plugin_with_game()
    inst._dd_game = None
    out = inst._dd_ui_do_play("3♠")
    assert out.get("is_error") is True
    assert out["state"] == {"stage": "none"}

    inst2 = _plugin_with_game(landlord_idx=0)
    g2 = inst2._dd_game
    g2.current = 1  # 假装轮到 AI
    out2 = inst2._dd_ui_do_play("3♠")
    assert out2.get("is_error") is False  # 不是错误，只是提示
    assert "AI" in out2["output"] or "YUI" in out2["output"]


def test_ui_new_game_returns_state():
    """面板开新局：返回 state、推进到轮到你或结束。"""
    inst = _plugin_with_game(landlord_idx=0)
    out = _run(inst.dd_ui_new_game())
    assert "state" in out
    st = out["state"]
    # 新开一局从叫地主阶段开始（v0.6.0 面板流程：bidding → playing → over）
    assert st["stage"] in ("bidding", "playing", "over")
    if st["stage"] == "playing":
        assert st["current"] == "你"
    assert st["stats"] == {"wins": 0, "losses": 0}
    assert st["user_role"] in ("地主", "农民")


def test_ui_entries_exist():
    """4 个面板入口方法存在且可调用（装饰器叠放后仍保留）。"""
    inst = _plugin_with_game(landlord_idx=0)
    for name in ("dd_ui_state", "dd_ui_new_game", "dd_ui_play", "dd_ui_pass"):
        assert callable(getattr(inst, name, None)), name
    # 无局时 dd_ui_state 是正常态：is_error=False、空 state、不带 error 键（面板初始化依赖）
    inst._dd_game = None
    out = _run(inst.dd_ui_state())
    assert out.get("is_error") is False
    assert out["state"] == {"stage": "none"}
    assert "error" not in out
