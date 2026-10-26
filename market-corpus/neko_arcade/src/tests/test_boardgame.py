"""棋类对弈: 6 个子玩法的规则 + 模式路由 + 适配器流程。

规则用"标准值/标准杀法"钉: 象棋开局 44 手、国际象棋开局 20 手 + 愚人杀、
黑白棋开局 4 点、围棋提子与两停一手数子。适配器只验路由与状态搬运。
"""

from __future__ import annotations

import asyncio
import json
import random
from pathlib import Path

from plugin.plugins.neko_arcade.games.boardgame import BoardGame
from plugin.plugins.neko_arcade.games.boardgame.boards import chess as ch
from plugin.plugins.neko_arcade.games.boardgame.boards import connect4 as c4
from plugin.plugins.neko_arcade.games.boardgame.boards import go as go_m
from plugin.plugins.neko_arcade.games.boardgame.boards import gomoku as gm
from plugin.plugins.neko_arcade.games.boardgame.boards import othello as ot
from plugin.plugins.neko_arcade.games.boardgame.boards import xiangqi as xq

ROOT = Path(__file__).resolve().parent.parent
CFG = ROOT / "data" / "config" / "boardgame"


class StubPlugin:
    def __init__(self):
        self.data = {}

    async def store_get_user(self, gid, uid, default=None):
        return self.data.get((gid, uid), default)

    async def store_save_user(self, gid, uid, data):
        self.data[(gid, uid)] = data


class FakeRender:
    def __init__(self):
        self.specs = []

    async def page(self, title, **kw):
        self.specs.append({"title": title, **kw})
        return [b"PNG"]


def _game(**over):
    g = BoardGame(StubPlugin())
    cfg = json.loads((CFG / "config.json").read_text(encoding="utf-8"))
    cfg.update(over)
    g._config = cfg
    g._rng = random.Random(20260922)
    g._render = FakeRender()
    return g


# ── 规则 ────────────────────────────────────
def test_gomoku_win_and_block() -> None:
    st = gm.new_state()
    for mv in ("A1", "A2", "B1", "B2", "C1", "C2", "D1", "E2", "E1"):   # 黑先成五
        gm.apply_move(st, gm.parse_move(mv, st))
    assert st.get("winner") == 1 or st.get("over")
    st2 = gm.new_state()
    for mv in ("A1", "H8", "B1", "H9", "C1", "H10", "D1"):               # 黑四连, 该堵
        gm.apply_move(st2, gm.parse_move(mv, st2))
    her = gm.cat_move(st2, 3, random.Random(1))
    assert gm.rc_to_coord(*her) in ("E1", "A5", "E5")


def test_xiangqi_opening_44_and_rules() -> None:
    assert len(xq.Position().legal_moves(xq.RED)) == 44          # 象棋标准值
    pos = xq.Position()
    pos.board[(1, 1)] = (xq.RED, "N")
    pos.board[(1, 2)] = (xq.RED, "P")                            # 蹩马腿
    assert [m[1] for m in pos.pseudo_moves(xq.RED) if m[0] == (1, 1)] == [(3, 2)]
    p2 = xq.Position()
    p2.board = {(0, 0): (xq.RED, "C"), (0, 3): (xq.RED, "P"), (0, 6): (xq.BLACK, "R"),
                (4, 0): (xq.RED, "K"), (4, 9): (xq.BLACK, "K")}
    squares = {m[1] for m in p2.pseudo_moves(xq.RED) if m[0] == (0, 0)}
    assert (0, 6) in squares and (0, 4) not in squares           # 炮翻山吃车


def test_othello_opening_and_flip() -> None:
    st = ot.new_state()
    assert sorted(ot.coord(*m) for m in ot.legal_moves(st)) == ["c4", "d3", "e6", "f5"]
    out = ot.apply_move(st, ot.parse_move("d3", st))
    assert out["ok"] and out["event"] == "flip"
    assert ot.review({"board": ot.new_state()["board"]}, (3, 2))["quality"] in ("ok", "best")


def test_connect4_drop_and_win() -> None:
    st = c4.new_state()
    assert c4.parse_move("d", st) == c4.parse_move("4", st) == 3
    for col in (3, 0, 3, 0, 3, 0):
        c4.apply_move(st, col)
    out = c4.apply_move(st, 3)
    assert out.get("over") == "red"                              # 我方四连


def test_go_capture_ko_and_scoring() -> None:
    st = go_m.new_state()
    st["board"] = ["w........"] + st["board"][1:]                 # a1 一颗白子
    go_m.apply_move(st, go_m.parse_move("b1", st))
    assert st["caps"]["b"] == 0                                  # 还有 a2 这口气
    go_m.apply_move(st, go_m.parse_move("b1", st)) if False else None
    st2 = go_m.new_state()
    st2["board"] = ["w........", "........."] + st2["board"][2:]
    go_m.apply_move(st2, go_m.parse_move("b1", st2))             # 黑 b1
    st2["turn"] = "b"
    go_m.apply_move(st2, go_m.parse_move("a2", st2))             # 黑 a2 → 提 a1
    assert st2["caps"]["b"] == 1
    assert go_m.parse_move("过", st2) == "pass"                   # 停一手
    st3 = go_m.new_state()
    go_m.apply_move(st3, "pass")
    out = go_m.apply_move(st3, "pass")
    assert out.get("over") and isinstance(st3.get("score"), list)  # 两停一手 → 数子终局


def test_chess_opening_mate_and_castling() -> None:
    st = ch.new_state()
    assert st["turn"] == "w"
    assert len(ch.legal_moves(st)) == 20                          # 国际象棋标准值
    st2 = ch.new_state()
    for mv in ("f2 f3", "e7 e5", "g2 g4"):
        ch.apply_move(st2, ch.parse_move(mv, st2))
    out = ch.apply_move(st2, ch.parse_move("d8 h4", st2))         # 愚人杀
    assert out["event"] == "cat_check" and out.get("over") == "black"
    st3 = ch.new_state()
    st3["board"][0][5] = ""
    st3["board"][0][6] = ""
    assert ((4, 0), (6, 0)) in ch.legal_moves(st3)                # 短易位


# ── 适配器: 模式路由与状态搬运 ───────────────
def test_every_mode_can_start_and_move() -> None:
    opens = {"gomoku": "H8", "othello": "d3", "xiangqi": "b3 b7",
             "connect4": "d", "go": "d4", "chess": "e2 e4"}
    words = {"gomoku": "五子棋", "othello": "黑白棋", "xiangqi": "中国象棋",
             "connect4": "四子棋", "go": "围棋", "chess": "国际象棋"}
    for mode, word in words.items():
        game = _game()
        start = asyncio.run(game.handle_action("u", word))
        assert start["outcome"] == "start", (mode, start)
        move = asyncio.run(game.handle_action("u", opens[mode]))
        assert move["outcome"] not in ("illegal", "idle"), (mode, move)
        data = asyncio.run(game._load("u"))
        assert data["game"]["mode"] == mode
        assert data["game"]["moves"], mode


def test_chat_does_not_move_and_mode_switch_restarts() -> None:
    game = _game()
    asyncio.run(game.handle_action("u", "五子棋"))
    asyncio.run(game.handle_action("u", "H8"))
    before = len(asyncio.run(game._load("u"))["game"]["moves"])
    chat = asyncio.run(game.handle_action("u", "你今天开心吗"))
    assert chat["outcome"] == "chat"
    assert len(asyncio.run(game._load("u"))["game"]["moves"]) == before
    switch = asyncio.run(game.handle_action("u", "象棋"))
    assert switch["outcome"] == "start"
    assert asyncio.run(game._load("u"))["game"]["mode"] == "xiangqi"


def test_undo_resign_and_status() -> None:
    game = _game()
    asyncio.run(game.handle_action("u", "围棋"))
    asyncio.run(game.handle_action("u", "d4"))
    asyncio.run(game.handle_action("u", "悔棋"))
    assert asyncio.run(game._load("u"))["game"]["moves"] == []
    asyncio.run(game.handle_action("u", "认输"))
    data = asyncio.run(game._load("u"))
    assert data["game"].get("result") == "black" and data["stats"]["losses"] == 1
    status = asyncio.run(game.get_status("u"))
    assert status["wins"] + status["losses"] >= 1


def test_keywords_and_help_cover_all_modes() -> None:
    kws = json.loads((CFG / "keywords.json").read_text(encoding="utf-8"))
    for word in ("五子棋", "象棋", "黑白棋", "四子棋", "围棋", "国际象棋"):
        assert word in kws, word
    assert all(len(str(w)) >= 2 for w in kws)
    help_data = json.loads((CFG / "help.json").read_text(encoding="utf-8"))
    cmds = [row[0] for row in help_data["commands"]]
    for word in ("下棋", "五子棋", "围棋", "国际象棋"):
        assert word in cmds, word


def test_board_image_is_attached_every_step() -> None:
    """线上回归: 出图链路必须通(合并时曾漏掉出图, 只有文字)。"""
    game = _game(show_image="always")
    start = asyncio.run(game.handle_action("u", "五子棋"))
    assert start.get("images"), "开局就要带棋盘图"
    move = asyncio.run(game.handle_action("u", "H8"))
    assert move.get("images"), "每落一手也要带棋盘图"
    assert game._render.specs[0]["blocks"][0]["type"] == "board"
    # 渲染不可用时退回文字棋盘(不能只剩一行状态)
    game2 = _game()
    game2._render = None
    out = asyncio.run(game2.handle_action("u", "围棋"))
    assert "a" in out["message"] and "." in out["message"]


# ── 状态收尾与输入容错(线上反馈: 认输回了"没有对决") ──
def test_finished_game_talks_straight() -> None:
    game = _game()
    asyncio.run(game.handle_action("u", "五子棋"))
    asyncio.run(game.handle_action("u", "认输"))
    again = asyncio.run(game.handle_action("u", "我认输"))
    assert again["outcome"] == "finished"
    assert "已经结束" in again["message"]
    assert "没有对决" not in again["message"]
    undo = asyncio.run(game.handle_action("u", "悔棋"))
    assert undo["outcome"] == "finished"


def test_resign_without_game_points_to_start() -> None:
    game = _game()
    out = asyncio.run(game.handle_action("u", "我认输"))
    assert out["outcome"] == "idle"
    assert "没在下棋" in out["message"] and "下棋" in out["message"]


def test_switching_mode_records_aborted_game() -> None:
    game = _game()
    asyncio.run(game.handle_action("u", "五子棋"))
    asyncio.run(game.handle_action("u", "H8"))
    out = asyncio.run(game.handle_action("u", "围棋"))
    assert out["outcome"] == "start"
    data = asyncio.run(game._load("u"))
    assert data["game"]["mode"] == "go" and data["game"]["moves"] == []
    assert data["stats"]["aborted"] == 1                      # 上一局记了"中途换棋"


def test_move_input_is_forgiving() -> None:
    for text in ("H8", "h8", "下在H8", "H 8", "我想下 H8 这里"):
        game = _game()
        asyncio.run(game.handle_action("u", "五子棋"))
        out = asyncio.run(game.handle_action("u", text))
        assert out["outcome"] not in ("illegal", "idle", "chat"), (text, out["outcome"])
    for text in ("e2 e4", "e2e4", "下在 e2 e4", "E2→E4"):
        game = _game()
        asyncio.run(game.handle_action("u", "国际象棋"))
        out = asyncio.run(game.handle_action("u", text))
        assert out["outcome"] not in ("illegal", "idle", "chat"), (text, out["outcome"])


def test_image_modes_control_spam() -> None:
    """出图三档: always 每步出 / key 只关键节点 / never 全不出。"""
    always = _game(show_image="always")
    asyncio.run(always.handle_action("u", "五子棋"))
    for mv in ("A1", "A2", "B1"):                     # 前两步不是关键节点
        out = asyncio.run(always.handle_action("u", mv))
        assert out.get("images"), ("always 应每步出图", mv)

    key = _game(show_image="key")
    start = asyncio.run(key.handle_action("u", "五子棋"))
    assert start.get("images"), "开局是关键节点, 要出图"
    quiet = asyncio.run(key.handle_action("u", "A1"))   # 普通落子: 不出图
    assert not quiet.get("images")
    assert "棋盘" in quiet["message"]                   # 提示可随时要图
    ask = asyncio.run(key.handle_action("u", "棋盘"))    # 显式要图 → 一定出
    assert ask.get("images")

    never = _game(show_image="never")
    asyncio.run(never.handle_action("u", "五子棋"))
    out = asyncio.run(never.handle_action("u", "A1"))
    assert not out.get("images")
    assert not never._render.specs                      # 一次都没渲染


# ── 对局交互体验: 开局带履历 + 悔棋余量 ──
def test_start_line_carries_record_and_last_result() -> None:
    game = _game()
    first = asyncio.run(game.handle_action("u", "五子棋"))
    assert "胜" in first["message"] and "负" in first["message"]      # 战绩
    assert "悔棋剩 3 次" in first["message"]                          # 悔棋余量
    asyncio.run(game.handle_action("u", "认输"))                      # 她赢了
    again = asyncio.run(game.handle_action("u", "重开"))
    assert "上次是本喵赢了" in again["message"], again["message"]


def test_undo_left_counts_down_and_explains() -> None:
    game = _game(undo_limit=1)
    asyncio.run(game.handle_action("u", "五子棋"))
    early = asyncio.run(game.handle_action("u", "悔棋"))               # 还没落子
    assert "至少下两手" in early["message"]
    assert "悔棋剩 1 次" in early["message"]
    asyncio.run(game.handle_action("u", "H8"))
    after = asyncio.run(game.handle_action("u", "悔棋"))
    assert "悔棋剩 0 次" in after["message"]
    asyncio.run(game.handle_action("u", "H9"))
    spent = asyncio.run(game.handle_action("u", "悔棋"))
    assert "都用完了" in spent["message"]


# ── 宿主猫娘人格: 游戏不管人设, 插件带人设返回交互 ──
def test_host_persona_reaches_game_lines(tmp_path, monkeypatch) -> None:
    host = tmp_path / "config" / "characters"
    host.mkdir(parents=True)
    (host / "zh-CN.json").write_text(json.dumps({
        "主人": {"昵称": "碳基生物"},
        "当前猫娘": "YUI",
        "猫娘": {"YUI": {"昵称": "YUI", "自称": "本喵",
                         "核心特质": ["理智可靠", "嘴上傲娇", "内心温柔"],
                         "一句话台词": "哼，盯着碳基生物别乱来的，当然只有本喵啦~",
                         "行为特点": {"喜欢待在碳基生物身边": True},
                         "厌恶": ["被忽视或冷落", "重复说之前说过的话"]}}},
        ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("NEKO_CHARACTERS_FILE", str(host / "zh-CN.json"))

    from plugin.plugins.neko_arcade.core.companion import Companion
    from plugin.plugins.neko_arcade.core.persona import Persona, load_host_persona

    raw = load_host_persona({})
    assert raw and raw["name"] == "YUI" and raw["user_call"] == "碳基生物"
    assert "理智可靠" in raw["traits"] and "本喵" in raw["self_call"]
    comp = Companion(persona=Persona(raw), llm=None)
    block = comp.persona_block()
    for token in ("YUI", "本喵", "理智可靠", "本喵啦", "碳基生物"):
        assert token in block, token
    prompt = comp._prompt("player_move", {"event_desc": "主人落子"}, "", "五子棋", "")
    for token in ("理智可靠", "碳基生物", "本喵啦"):
        assert token in prompt, token


def test_game_lines_come_from_injected_companion() -> None:
    """游戏不自己造人设: 台词必须出自本体注入的陪伴层。"""
    class FakeComp:
        def __init__(self):
            self.calls = []

        async def line(self, game_id, event, **kw):
            self.calls.append((game_id, event, kw.get("situation", "")))
            return f"（{event}·人格化台词）"

    game = _game()
    comp = FakeComp()
    game._companion_svc = comp
    out = asyncio.run(game.handle_action("u", "五子棋"))
    assert "人格化台词" in out["message"]
    assert comp.calls and comp.calls[0][0] == "boardgame"
