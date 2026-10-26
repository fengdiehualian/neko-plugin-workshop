"""内置斗地主小游戏引擎测试：增强 AI、cards_raw 字段、全流程收敛。"""


from doudizhu import (
    DouDizhuGame,
    can_play,
    hand_str,
    new_deck,
    suggest_play,
)

# ══════════ suggest_play 输出增强（cards_raw） ══════════


class TestSuggestPlayCardsRaw:
    def test_suggestion_has_cards_raw(self):
        hand = [3 * 4, 3 * 4 + 1, 8 * 4, 8 * 4 + 1, 10 * 4, 12 * 4]
        sug = suggest_play(hand, None, role="farmer")
        assert sug["suggestion"] is not None
        assert "cards_raw" in sug["suggestion"]
        assert isinstance(sug["suggestion"]["cards_raw"], list)
        assert len(sug["suggestion"]["cards_raw"]) > 0
        # cards_raw 必须是手牌子集且牌型合法
        assert all(c in hand for c in sug["suggestion"]["cards_raw"])
        assert can_play(sug["suggestion"]["cards_raw"], []) is not None

    def test_cards_raw_roundtrip_with_hand_str(self):
        hand = [3 * 4, 3 * 4 + 1, 8 * 4, 8 * 4 + 1, 10 * 4, 12 * 4]
        sug = suggest_play(hand, None, role="farmer")
        raw = sug["suggestion"]["cards_raw"]
        assert hand_str(raw) == sug["suggestion"]["cards"]
        assert all(c in hand for c in raw)

    def test_pass_suggestion_has_no_cards_raw(self):
        # 一手小牌面对大王单张：压不过 → should_pass，suggestion 为 None
        hand = [3 * 4, 4 * 4 + 1, 5 * 4]
        last = [17 * 4]  # 大王
        sug = suggest_play(hand, last, role="farmer")
        assert sug["should_pass"] is True
        assert sug["suggestion"] is None


# ══════════ 增强 AI 行为 ══════════


class TestEnhancedAI:
    def test_first_hand_avoids_wasting_bomb(self):
        # 先手且手牌有炸弹：按 v0.4 组合优先策略出牌（先清组合牌型，单张留后面），
        # 不以整副 4 张炸弹开局（炸弹留作压牌/残局）。
        g = DouDizhuGame(seed=1)
        g._set_landlord(0, 2)
        g.players[0]["hand"] = [3 * 4, 3 * 4 + 1, 3 * 4 + 2, 3 * 4 + 3, 5 * 4, 6 * 4, 7 * 4]
        g.last_play = []
        res = g.ai_play(0)
        assert res.get("ok")
        # 手牌从 7 张变 3 张 → 出了三带一（炸弹拆开清牌），未甩整副炸弹
        assert len(g.players[0]["hand"]) == 3
        assert "炸弹" not in res.get("message", "")

    def test_beats_pair_with_pair(self):
        # 上一手对子，AI 用更大对子压
        g = DouDizhuGame(seed=1)
        g._set_landlord(0, 2)
        g.players[0]["hand"] = [5 * 4, 5 * 4 + 1, 9 * 4, 9 * 4 + 1, 12 * 4, 13 * 4]
        g.last_play = [4 * 4, 4 * 4 + 1]  # 对4
        res = g.ai_play(0)
        assert res.get("ok")
        assert "出 5" in res["message"] or "对5" in res["message"]

    def test_passes_when_cannot_beat(self):
        # 大王单张压不了 → pass
        g = DouDizhuGame(seed=1)
        g._set_landlord(0, 2)
        g.players[0]["hand"] = [3 * 4, 4 * 4 + 1, 5 * 4, 6 * 4]
        g.last_play = [17 * 4]  # 大王
        res = g.ai_play(0)
        assert res.get("ok")
        assert "不要" in res["message"]

    def test_uses_bomb_when_stuck(self):
        # 面对对A（几乎最大对子），有炸弹就炸
        g = DouDizhuGame(seed=1)
        g._set_landlord(0, 2)
        g.players[0]["hand"] = [9 * 4, 9 * 4 + 1, 9 * 4 + 2, 9 * 4 + 3, 3 * 4, 5 * 4]
        g.last_play = [14 * 4, 14 * 4 + 1]  # 对A
        res = g.ai_play(0)
        assert res.get("ok")
        assert "炸" in res["message"] or "出" in res["message"]
        # 手牌从 6 张变 2 张 → 出了 4 张（炸弹）
        assert len(g.players[0]["hand"]) == 2

    def test_ai_plays_whole_hand_wins(self):
        # AI 一次性出完手牌 → 判胜（三张A压三张10）
        g = DouDizhuGame(seed=1)
        g._set_landlord(0, 2)
        g.players[0]["hand"] = [14 * 4, 14 * 4 + 1, 14 * 4 + 2]  # 三张A
        g.last_play = [10 * 4, 10 * 4 + 1, 10 * 4 + 2]  # 三张10
        res = g.ai_play(0)
        assert res.get("ok")
        assert res.get("result") is not None
        assert res["result"]["winner"] == g.players[0]["name"]
        assert g.stage == "over"


# ══════════ 小游戏全流程收敛（对应插件的 _dd_run_to_user） ══════════


def _run_ai_until_user(game, max_steps=200):
    """等价于插件 _dd_run_to_user：推进 AI 回合直到轮到用户或结束。"""
    moves = []
    guard = 0
    while game.stage == "playing" and game.current != 0 and guard < max_steps:
        guard += 1
        res = game.ai_play(game.current)
        moves.append(res.get("message", ""))
        if res.get("result"):
            break
    return moves, game


class TestGameFlow:
    def test_ai_landlord_first_then_user_turn(self):
        # 用户是农民（landlord=1），地主先手 → 自动推进后应轮到用户
        g = DouDizhuGame(["你", "YUI_左", "YUI_右"], seed=7)
        g._set_landlord(1, 2)
        assert g.current == 1
        moves, g2 = _run_ai_until_user(g)
        assert g2.stage == "playing"
        assert g2.current == 0
        assert moves  # 至少一个 AI 动作
        # 用户手牌仍是 17 张（AI 回合不动用户牌）
        assert len(g2.players[0]["hand"]) == 17

    def test_full_game_terminates_with_winner(self):
        # 完整对打到结束：必定有 winner 且步数有界
        g = DouDizhuGame(["你", "YUI_左", "YUI_右"], seed=42)
        g._set_landlord(1, 2)
        total = 0
        final_result = None
        guard = 0
        while g.stage == "playing" and guard < 500:
            guard += 1
            if g.current == 0:
                # 模拟用户：能压就压最小合法牌，否则不要（用 AI 决策兜底）
                hand = g.players[0]["hand"]
                if g.last_play:
                    sug = suggest_play(hand, g.last_play, role="farmer")
                    if sug.get("should_pass") or not sug.get("suggestion"):
                        res = g.play(0, [])
                    else:
                        res = g.play(0, sug["suggestion"]["cards_raw"])
                else:
                    sug = suggest_play(hand, None, role="farmer")
                    res = g.play(0, sug["suggestion"]["cards_raw"])
            else:
                res = g.ai_play(g.current)
            assert res.get("ok"), f"非法动作: {res}"
            total += 1
            if res.get("result"):
                final_result = res["result"]
                break
        assert final_result is not None, "对局没有正常结束"
        assert final_result["winner"] in ("你", "YUI_左", "YUI_右")
        assert final_result["role"] in ("地主", "农民")
        assert final_result["landlord"] in ("你", "YUI_左", "YUI_右")
        assert total < 500

    def test_user_wins_and_stats_logic(self):
        # 用户（农民）获胜场景：AI 先走回合回用户，用户拆对出单A、双AI过、
        # 回用户先手再出A → 用户出完判胜（role=农民、winner=你）
        g = DouDizhuGame(["你", "YUI_左", "YUI_右"], seed=3)
        g._set_landlord(1, 2)  # 用户是农民
        g.players[0]["hand"] = [14 * 4, 14 * 4 + 1]  # 对A
        g.players[1]["hand"] = [3 * 4, 9 * 4]
        g.players[2]["hand"] = [4 * 4, 8 * 4]
        g.last_play = []
        g.current = 1  # 轮到地主 AI
        # AI_左出3 → AI_右压4 → 轮到用户
        moves, _ = _run_ai_until_user(g)
        assert moves
        assert g.current == 0
        assert g.stage == "playing"
        assert g.last_play  # 上一手是单4
        # 用户出单A压单4
        res = g.play(0, [14 * 4])
        assert res.get("ok")
        # 两 AI 压不过A都 pass → 出牌权回用户（先手）
        r1 = g.play(g.current, [])
        assert r1.get("ok") and "不要" in r1["message"]
        r2 = g.play(g.current, [])
        assert r2.get("ok") and "不要" in r2["message"]
        assert g.current == 0 and not g.last_play
        # 用户先手出最后一张A → 获胜
        res_final = g.play(0, [14 * 4 + 1])
        assert res_final.get("result")
        assert res_final["result"]["role"] == "农民"
        assert res_final["result"]["winner"] == "你"
        assert g.stage == "over"

    def test_deck_has_54_cards(self):
        deck = new_deck()
        assert len(deck) == 54
        assert len(set(deck)) == 54
