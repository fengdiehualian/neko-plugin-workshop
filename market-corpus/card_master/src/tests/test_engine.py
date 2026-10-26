"""card_master 引擎与 Kards 规则单元测试。"""
import kards
from engine import (
    TexasHoldemGame,
    calculate_odds,
    evaluate_hand,
    parse_card,
    self_play,
)

# ══════════ 牌型评估 ══════════

class TestHandEvaluation:
    def test_royal_flush(self):
        hand = [parse_card(c) for c in "As Ks Qs Js Ts".split()]
        assert evaluate_hand(hand)["name"] == "皇家同花顺"

    def test_straight_flush(self):
        hand = [parse_card(c) for c in "9s 8s 7s 6s 5s".split()]
        assert evaluate_hand(hand)["name"] == "同花顺"

    def test_four_kind(self):
        hand = [parse_card(c) for c in "As Ah Ad Ac Ks".split()]
        assert evaluate_hand(hand)["name"] == "四条"

    def test_full_house(self):
        hand = [parse_card(c) for c in "As Ah Ad Ks Kh".split()]
        assert evaluate_hand(hand)["name"] == "葫芦"

    def test_flush(self):
        hand = [parse_card(c) for c in "As Ks Qs Js 9s".split()]
        assert evaluate_hand(hand)["name"] == "同花"

    def test_straight(self):
        hand = [parse_card(c) for c in "9s 8d 7h 6c 5s".split()]
        assert evaluate_hand(hand)["name"] == "顺子"

    def test_three_kind(self):
        hand = [parse_card(c) for c in "As Ah Ad Ks Qs".split()]
        assert evaluate_hand(hand)["name"] == "三条"

    def test_two_pair(self):
        hand = [parse_card(c) for c in "As Ah Kd Ks Qs".split()]
        assert evaluate_hand(hand)["name"] == "两对"

    def test_one_pair(self):
        hand = [parse_card(c) for c in "As Ah Kd Qs Jh".split()]
        assert evaluate_hand(hand)["name"] == "一对"

    def test_high_card(self):
        hand = [parse_card(c) for c in "As Kd Qh Js 9c".split()]
        assert evaluate_hand(hand)["name"] == "高牌"

    def test_best_5_of_7(self):
        hand = [parse_card(c) for c in "As Ks Qs Js Ts 2d 3c".split()]
        result = evaluate_hand(hand)
        assert result["name"] == "皇家同花顺"
        assert "Ts" in result["cards_str"]


# ══════════ 牌局状态机 ══════════

class TestTexasHoldemGame:
    def test_create_game(self):
        game = TexasHoldemGame([{"name": "A", "chips": 1000}, {"name": "B", "chips": 1000}], seed=42)
        state = game.get_state()
        assert state["stage"] == "preflop"
        assert state["pot"] == 30  # 小盲10 + 大盲20

    def test_min_players(self):
        try:
            TexasHoldemGame([{"name": "A", "chips": 1000}])
            assert False, "应该抛出 ValueError"
        except ValueError:
            pass

    def test_fold_ends_hand(self):
        game = TexasHoldemGame([{"name": "A", "chips": 1000}, {"name": "B", "chips": 1000}], seed=1)
        result = game.action("fold")
        assert result["ok"] is True
        assert game.stage == "showdown"

    def test_raise_min_amount(self):
        game = TexasHoldemGame([{"name": "A", "chips": 1000}, {"name": "B", "chips": 1000}], seed=1)
        # 大盲20，最小加注到40（20+20），尝试加注到30应该失败
        result = game.action("raise", amount=30)
        assert result["ok"] is False

    def test_raise_logic(self):
        game = TexasHoldemGame(
            [{"name": "A", "chips": 1000}, {"name": "B", "chips": 1000}, {"name": "C", "chips": 1000}], seed=1
        )
        r1 = game.action("raise", amount=60)
        assert r1["ok"] is True
        assert game.current_bet == 60
        assert game.min_raise == 40  # 60 - 20

        r2 = game.action("raise", amount=140)
        assert r2["ok"] is True
        assert game.current_bet == 140
        assert game.min_raise == 80  # 140 - 60

    def test_check_when_no_bet(self):
        game = TexasHoldemGame([{"name": "A", "chips": 1000}, {"name": "B", "chips": 1000}], seed=1)
        # 当前玩家需跟注（大盲），不能 check
        result = game.action("check")
        assert result["ok"] is False


# ══════════ 胜率计算 ══════════

class TestCalculateOdds:
    def test_aa_high_winrate(self):
        hand = [parse_card("As"), parse_card("Ah")]
        result = calculate_odds(hand, [], opponents=1, simulations=500, seed=1)
        assert result["win_rate"] > 70  # AA 对 1 对手胜率约 85%

    def test_returns_percentages(self):
        hand = [parse_card("As"), parse_card("Kh")]
        result = calculate_odds(hand, [], opponents=1, simulations=100, seed=2)
        assert 0 <= result["win_rate"] <= 100
        assert 0 <= result["tie_rate"] <= 100
        assert 0 <= result["lose_rate"] <= 100


# ══════════ 自我对弈 ══════════

class TestSelfPlay:
    def test_single_round(self):
        players = [{"name": f"AI_{i+1}", "chips": 1000} for i in range(4)]
        result = self_play(players, rounds=1, seed=7, verbose=False)
        assert result["rounds_played"] >= 1
        assert result["winner"] is not None
        assert len(result["final_chips"]) == 4


# ══════════ Kards 规则 ══════════

class TestKards:
    def test_basic_rules_not_empty(self):
        assert len(kards.get_rules("basic")) > 100

    def test_all_topics(self):
        for topic in ["basic", "resource", "battlefield", "card_types", "unit_types", "learning"]:
            assert len(kards.get_rules(topic)) > 50, f"topic {topic} 为空"

    def test_keyword_count(self):
        assert len(kards.KEYWORDS) >= 17

    def test_keyword_lookup(self):
        result = kards.get_keyword("闪击")
        assert "闪击" in result

    def test_nation_count(self):
        assert len(kards.NATIONS) == 9

    def test_nation_lookup(self):
        result = kards.get_nation("德国")
        assert "德国" in result
        assert "主要国" in result

    def test_deck_check_valid(self):
        deck = [
            {"name": "总部", "type": "HQ", "nation": "德国", "count": 1},
            {"name": "步兵", "type": "unit", "nation": "德国", "count": 20},
            {"name": "指令", "type": "order", "nation": "德国", "count": 10},
            {"name": "波兰单位", "type": "unit", "nation": "波兰", "count": 9},
        ]
        result = kards.check_deck(deck)
        assert result["valid"] is True
        assert result["summary"]["total"] == 40

    def test_deck_check_invalid_two_hq(self):
        deck = [
            {"name": "总部", "type": "HQ", "nation": "德国", "count": 2},
            {"name": "单位", "type": "unit", "nation": "德国", "count": 38},
        ]
        result = kards.check_deck(deck)
        assert result["valid"] is False

    def test_deck_check_wrong_count(self):
        deck = [
            {"name": "总部", "type": "HQ", "nation": "德国", "count": 1},
            {"name": "单位", "type": "unit", "nation": "德国", "count": 30},
        ]
        result = kards.check_deck(deck)
        assert result["valid"] is False  # 只有31张
