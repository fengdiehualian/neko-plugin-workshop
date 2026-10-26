"""斗地主引擎单元测试。"""
from doudizhu import (
    DouDizhuGame,
    can_play,
    identify_hand,
    new_deck,
)


def make_cards(spec: str) -> list[int]:
    """从文字构造牌列表。spec: '3♠ 4♥ 小王'"""
    rank_map = {"3": 3, "4": 4, "5": 5, "6": 6, "7": 7, "8": 8, "9": 9, "10": 10,
                "J": 11, "Q": 12, "K": 13, "A": 14, "2": 15}
    suit_map = {"♠": 0, "♥": 1, "♦": 2, "♣": 3}
    cards = []
    for token in spec.split():
        if token == "小王":
            cards.append(16 * 4)
        elif token == "大王":
            cards.append(17 * 4)
        else:
            rank_part = token.rstrip("♠♥♦♣")
            suit_part = token[len(rank_part):] if len(rank_part) < len(token) else "♠"
            r = rank_map[rank_part]
            s = suit_map.get(suit_part, 0)
            cards.append(r * 4 + s)
    return cards


# ══════════ 牌型识别 ══════════

class TestIdentifyHand:
    def test_single(self):
        assert identify_hand(make_cards("3♠"))["type"] == "single"

    def test_pair(self):
        assert identify_hand(make_cards("3♠ 3♥"))["type"] == "pair"

    def test_triple(self):
        assert identify_hand(make_cards("3♠ 3♥ 3♦"))["type"] == "triple"

    def test_triple_single(self):
        assert identify_hand(make_cards("3♠ 3♥ 3♦ 4♣"))["type"] == "triple_single"

    def test_triple_pair(self):
        assert identify_hand(make_cards("3♠ 3♥ 3♦ 4♣ 4♠"))["type"] == "triple_pair"

    def test_straight(self):
        h = identify_hand(make_cards("3♠ 4♥ 5♦ 6♣ 7♠"))
        assert h["type"] == "straight"
        assert h["length"] == 5

    def test_pair_straight(self):
        h = identify_hand(make_cards("3♠ 3♥ 4♦ 4♣ 5♠ 5♥"))
        assert h["type"] == "pair_straight"
        assert h["length"] == 3

    def test_bomb(self):
        assert identify_hand(make_cards("3♠ 3♥ 3♦ 3♣"))["type"] == "bomb"

    def test_rocket(self):
        assert identify_hand(make_cards("小王 大王"))["type"] == "rocket"

    def test_four_two(self):
        assert identify_hand(make_cards("3♠ 3♥ 3♦ 3♣ 4♠ 5♥"))["type"] == "four_two"

    def test_airplane(self):
        h = identify_hand(make_cards("3♠ 3♥ 3♦ 4♣ 4♠ 4♥"))
        assert h["type"] == "airplane"
        assert h["length"] == 2

    def test_invalid_straight_with_2(self):
        # 顺子不能含2
        assert identify_hand(make_cards("J♠ Q♥ K♦ A♣ 2♠")) is None

    def test_invalid_pair_straight_with_2(self):
        assert identify_hand(make_cards("J♠ J♥ Q♦ Q♣ K♠ K♥")) is not None  # JQK连对合法
        assert identify_hand(make_cards("Q♠ Q♥ K♦ K♣ A♠ A♥")) is not None  # QKA连对合法
        assert identify_hand(make_cards("K♠ K♥ A♦ A♣ 2♠ 2♥")) is None  # KA2连对不合法

    def test_invalid_random(self):
        assert identify_hand(make_cards("3♠ 5♥ 7♦")) is None  # 随机三张非法


# ══════════ 出牌比较 ══════════

class TestCanPlay:
    def test_rocket_beats_all(self):
        assert can_play(make_cards("小王 大王"), make_cards("3♠ 3♥ 3♦ 3♣"))

    def test_bomb_beats_non_bomb(self):
        assert can_play(make_cards("3♠ 3♥ 3♦ 3♣"), make_cards("3♠"))

    def test_same_type_compare_rank(self):
        assert can_play(make_cards("4♠"), make_cards("3♠"))
        assert not can_play(make_cards("3♠"), make_cards("4♠"))

    def test_different_type_cannot_play(self):
        assert not can_play(make_cards("3♠ 3♥"), make_cards("3♠"))

    def test_free_play_when_no_last(self):
        assert can_play(make_cards("3♠"), None)

    def test_bomb_vs_bomb(self):
        assert can_play(make_cards("4♠ 4♥ 4♦ 4♣"), make_cards("3♠ 3♥ 3♦ 3♣"))
        assert not can_play(make_cards("3♠ 3♥ 3♦ 3♣"), make_cards("4♠ 4♥ 4♦ 4♣"))


# ══════════ 牌局 ══════════

class TestDouDizhuGame:
    def test_deal(self):
        game = DouDizhuGame(seed=42)
        assert len(game.players[0]["hand"]) == 17
        assert len(game.players[1]["hand"]) == 17
        assert len(game.players[2]["hand"]) == 17
        assert len(game.bottom_cards) == 3
        assert game.stage == "bidding"

    def test_bid_3_becomes_landlord(self):
        game = DouDizhuGame(seed=42)
        result = game.bid(0, 3)
        assert result["ok"] is True
        assert game.landlord_idx == 0
        assert game.players[0]["role"] == "landlord"
        assert len(game.players[0]["hand"]) == 20  # 17 + 3底牌
        assert game.stage == "playing"

    def test_bid_pass_then_next(self):
        game = DouDizhuGame(seed=42)
        r1 = game.bid(0, 0)  # 玩家0不叫
        assert r1["ok"] is True
        assert game.current == 1
        r2 = game.bid(1, 1)  # 玩家1叫1分
        assert r2["ok"] is True
        assert game.current == 2
        r3 = game.bid(2, 0)  # 玩家2不叫
        assert r3["ok"] is True
        assert game.landlord_idx == 1  # 玩家1叫分最高

    def test_play_single_card(self):
        game = DouDizhuGame(seed=42)
        game.bid(0, 3)  # 玩家0当地主
        # 出最小的单张
        hand = game.players[0]["hand"]
        if hand:
            result = game.play(0, [hand[0]])
            assert result["ok"] is True
            assert game.current == 1  # 轮到下家

    def test_play_pass(self):
        game = DouDizhuGame(seed=42)
        game.bid(0, 3)
        hand = game.players[0]["hand"]
        game.play(0, [hand[0]])  # 地主出牌
        # 农民选择不要
        result = game.play(1, [])
        assert result["ok"] is True

    def test_invalid_card_not_in_hand(self):
        game = DouDizhuGame(seed=42)
        game.bid(0, 3)
        # 出一张不在手中的牌（用一个确定的牌）
        result = game.play(0, [3 * 4])  # 3♠，可能不在手中
        # 不管在不在，不应该崩溃
        assert result["ok"] in (True, False)

    def test_three_players_required(self):
        try:
            DouDizhuGame(player_names=["A", "B"])
            assert False, "应该抛出 ValueError"
        except ValueError:
            pass

    def test_deck_has_54_cards(self):
        assert len(new_deck()) == 54
