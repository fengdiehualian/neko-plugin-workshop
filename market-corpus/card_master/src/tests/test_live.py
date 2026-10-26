"""card_master live 实时打牌决策引擎测试。"""


import live as live_mod
from doudizhu import suggest_play
from engine import suggest_from_state

# ══════════ suggest_from_state（德州扑克纯函数决策） ══════════


class TestSuggestFromState:
    # 蒙特卡洛用固定种子，避免随机方差导致 flaky（曾出现 72o 胜率估计偏高误判 call）
    _SEED = 20260731

    def test_strong_hand_preflop(self):
        # AA 对单对手，无需跟注 → 应加注（engine 编码：A=48, K=44, T=32, 7=20, 2=0）
        adv = suggest_from_state(
            hand=[12 * 4, 12 * 4 + 1], community=[], to_call=0, pot=60,
            chips=980, active_count=2, big_blind=20, seed=self._SEED,
        )
        assert adv["suggestion"] == "raise"
        assert adv["win_rate"] > 60
        assert adv["raise_to"] is not None and adv["raise_to"] > 0

    def test_bad_hand_facing_big_call(self):
        # 72o 面对超大额跟注（底池赔率远高于其胜率）→ 应弃牌
        adv = suggest_from_state(
            hand=[5 * 4, 0 * 4 + 1], community=[], to_call=350, pot=500,
            chips=500, active_count=2, big_blind=20, seed=self._SEED,
        )
        assert adv["suggestion"] == "fold"

    def test_monster_on_flop(self):
        # 口袋对子 + 三张同点数翻牌 → 葫芦，加注
        adv = suggest_from_state(
            hand=[8 * 4, 8 * 4 + 1],
            community=[8 * 4 + 2, 5 * 4, 6 * 4 + 1],
            to_call=0, pot=100, chips=900, active_count=2, big_blind=20,
            seed=self._SEED,
        )
        assert adv["suggestion"] == "raise"

    def test_return_shape(self):
        adv = suggest_from_state(
            hand=[12 * 4, 11 * 4], community=[0 * 4, 3 * 4, 7 * 4],
            to_call=40, pot=200, chips=760, active_count=3, big_blind=20,
            seed=self._SEED,
        )
        for key in ("suggestion", "reason", "raise_to", "win_rate", "tie_rate",
                    "equity", "pot_odds", "pot_odds_detail", "your_hand",
                    "community", "pot", "to_call", "chips"):
            assert key in adv


# ══════════ suggest_play（斗地主完整候选出牌） ══════════


class TestSuggestPlay:
    def test_first_hand_plays_weakest(self):
        # 先手：v0.4 组合优先策略——优先出组合牌型清牌（这里出对子），单张留后面
        hand = [3 * 4, 4 * 4 + 1, 10 * 4, 10 * 4 + 1, 14 * 4, 14 * 4 + 1]
        sug = suggest_play(hand, None, role="farmer")
        assert sug["suggestion"] is not None
        assert sug["suggestion"]["type"] in ("pair", "single")
        assert sug["candidates"]

    def test_beat_with_minimal(self):
        # 上家出 8 对，用最小的 9 对压制
        hand = [3 * 4, 9 * 4, 9 * 4 + 1, 9 * 4 + 2, 14 * 4, 14 * 4 + 1]
        last = [8 * 4, 8 * 4 + 1]
        sug = suggest_play(hand, last, role="farmer")
        assert sug["suggestion"] is not None
        assert sug["suggestion"]["type"] == "pair"
        assert "9" in sug["suggestion"]["cards"]

    def test_pass_when_cannot_beat(self):
        hand = [3 * 4, 5 * 4 + 1, 6 * 4]
        last = [16 * 4, 17 * 4]  # 王炸（小王+大王）
        sug = suggest_play(hand, last, role="farmer")
        assert sug["should_pass"] is True
        assert sug["suggestion"] is None

    def test_bomb_kept_for_emergency(self):
        # 有炸弹也有小牌可压时，先手应出弱牌而非炸弹
        hand = [3 * 4, 3 * 4 + 1, 3 * 4 + 2, 3 * 4 + 3, 10 * 4, 10 * 4 + 1]
        sug = suggest_play(hand, None, role="farmer")
        assert sug["suggestion"]["type"] != "bomb"

    def test_rocket_and_bomb_enumeration(self):
        hand = [16 * 4, 17 * 4, 15 * 4, 15 * 4 + 1, 15 * 4 + 2, 15 * 4 + 3]
        sug = suggest_play(hand, None, role="farmer", top_k=20)
        types = {c["type"] for c in sug["candidates"]}
        assert "rocket" in types
        assert "bomb" in types

    def test_straight_detected(self):
        hand = [3 * 4, 4 * 4 + 1, 5 * 4 + 2, 6 * 4 + 3, 7 * 4, 14 * 4, 14 * 4 + 1]
        sug = suggest_play(hand, None, role="farmer")
        assert any(c["type"] == "straight" for c in sug["candidates"])


# ══════════ live 决策器 ══════════


class TestLiveAdvice:
    def test_poker_advice_valid(self):
        res = live_mod.advise_poker({
            "hole_cards": "As Kh", "community": "", "pot": 60,
            "to_call": 0, "my_chips": 940, "active_players": 2,
            "big_blind": 20,
        })
        assert res["ok"] is True
        assert res["advice"]["suggestion"] in ("raise", "check", "fold", "call")
        assert res["advice"]["steps"]

    def test_poker_advice_bad_cards(self):
        res = live_mod.advise_poker({"hole_cards": "Xx Yy"})
        assert res["ok"] is False
        assert "error" in res

    def test_doudizhu_advice(self):
        res = live_mod.advise_doudizhu({
            "my_hand": "3♠ 5♥ 5♦ 9♣ 9♠ J♥ J♣ 2♣ 大王",
            "last_play": "5♠ 5♥ 5♦",
            "my_role": "farmer",
        })
        assert res["ok"] is True
        assert res["suggestion"]["steps"]
        assert "建议" in res["output"]

    def test_doudizhu_advice_first(self):
        res = live_mod.advise_doudizhu({
            "my_hand": "3♠ 4♥ 8♦ 10♣ J♠",
            "last_play": "",
            "my_role": "landlord",
        })
        assert res["ok"] is True
        assert res["suggestion"]["should_pass"] is False

    def test_kards_advice_attack_hq(self):
        res = live_mod.advise_kards({
            "kredits": 6, "my_hq": 20, "opp_hq": 8,
            "my_hand": [{"name": "步兵", "cost": 2, "type": "unit", "attack": 2, "defense": 3}],
            "front_line": [{"name": "步兵A", "attack": 2, "defense": 3, "type": "步兵"}],
            "support_line": [],
            "opp_front": [],
            "opp_support": [],
            "is_my_turn": True,
        })
        assert res["ok"] is True
        joined = res["output"]
        assert "攻击敌方总部" in joined

    def test_kards_advice_not_my_turn(self):
        res = live_mod.advise_kards({
            "kredits": 6, "my_hq": 20, "opp_hq": 20,
            "is_my_turn": False,
        })
        assert res["ok"] is True
        assert "对手回合" in res["output"]

    def test_kards_advice_bad_kredits(self):
        res = live_mod.advise_kards({"kredits": "abc"})
        assert res["ok"] is False


# ══════════ 会话管理 ══════════


class TestLiveSession:
    def test_new_session(self):
        s = live_mod.new_session("poker", "1号位", {"big_blind": 20})
        assert s.session_id
        assert s.game_type == "poker"
        assert s.turn_counter == 0

    def test_handle_report_poker(self):
        s = live_mod.new_session("poker")
        res = live_mod.handle_report(s, {
            "hole_cards": "As Kh", "community": "", "pot": 60,
            "to_call": 0, "my_chips": 940, "active_players": 2, "big_blind": 20,
        })
        assert res["ok"] is True
        assert s.turn_counter == 1
        assert s.state["hole_cards"] == "As Kh"
        assert len(s.history) == 2

    def test_handle_report_unknown_game(self):
        s = live_mod.new_session("mahjong")
        res = live_mod.handle_report(s, {})
        assert res["ok"] is False

    def test_parse_dd_token(self):
        assert live_mod._parse_dd_token("小王") == 16 * 4
        assert live_mod._parse_dd_token("大王") == 17 * 4
        assert live_mod._parse_dd_token("10♥") == 10 * 4 + 1
        assert live_mod._parse_dd_token("A♣") == 14 * 4 + 3
        assert live_mod._parse_dd_token("2") == 15 * 4
        assert live_mod._parse_dd_token("垃圾牌") is None
