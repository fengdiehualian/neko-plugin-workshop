"""斗地主引擎 — 3人斗地主，一副牌54张（含大小王）。

功能：发牌、叫地主、牌型识别、出牌验证、牌局状态机、简单AI出牌。
牌型：单张/对子/三张/三带一/三带二/顺子/连对/飞机/飞机带翅膀/四带二/炸弹/王炸。
"""
from __future__ import annotations

import random
from collections import Counter

# 牌点定义：3-15 对应 3-A，16=小王，17=大王
RANK_NAMES = {3: "3", 4: "4", 5: "5", 6: "6", 7: "7", 8: "8", 9: "9",
               10: "10", 11: "J", 12: "Q", 13: "K", 14: "A", 15: "2",
               16: "小王", 17: "大王"}
SUIT_NAMES = ["♠", "♥", "♦", "♣"]

# 顺子/连对/飞机允许的点数范围（3-A，不含2和王）
STRAIGHT_RANKS = list(range(3, 15))  # 3,4,...,A(14)


def new_deck() -> list[int]:
    """生成一副54张牌。每张牌用整数表示：rank*4 + suit，rank 3-15，suit 0-3；小王=16*4=64，大王=17*4=68。"""
    deck = []
    for rank in range(3, 16):  # 3-A(14), 2(15)
        for suit in range(4):
            deck.append(rank * 4 + suit)
    deck.append(16 * 4)  # 小王
    deck.append(17 * 4)  # 大王
    return deck


def card_rank(card: int) -> int:
    return card // 4


def card_suit(card: int) -> int:
    return card % 4


def card_str(card: int) -> str:
    r = card_rank(card)
    if r >= 16:
        return RANK_NAMES[r]
    return f"{RANK_NAMES[r]}{SUIT_NAMES[card_suit(card)]}"


def hand_str(cards: list[int]) -> str:
    return " ".join(card_str(c) for c in sorted(cards, key=lambda c: (card_rank(c), card_suit(c))))


def sort_cards(cards: list[int]) -> list[int]:
    return sorted(cards, key=lambda c: (card_rank(c), card_suit(c)))


# ══════════ 牌型识别 ══════════

HAND_TYPES = [
    "single", "pair", "triple", "triple_single", "triple_pair",
    "straight", "pair_straight", "airplane", "airplane_single", "airplane_pair",
    "four_two", "four_two_pair", "bomb", "rocket",
]

HAND_TYPE_NAMES = {
    "single": "单张", "pair": "对子", "triple": "三张",
    "triple_single": "三带一", "triple_pair": "三带二",
    "straight": "顺子", "pair_straight": "连对",
    "airplane": "飞机", "airplane_single": "飞机带单牌", "airplane_pair": "飞机带对子",
    "four_two": "四带二", "four_two_pair": "四带两对",
    "bomb": "炸弹", "rocket": "王炸",
}


def identify_hand(cards: list[int]) -> dict | None:
    """识别牌型。返回 {type, name, main_rank, length} 或 None（非法牌型）。

    main_rank: 主要牌点（用于比较大小），如顺子的最小点、飞机的最小点。
    length: 牌型长度（如顺子张数、飞机组数）。
    """
    if not cards:
        return None
    n = len(cards)
    ranks = [card_rank(c) for c in cards]
    counter = Counter(ranks)
    counts = sorted(counter.values(), reverse=True)
    unique_ranks = sorted(counter.keys())

    # 王炸
    if n == 2 and 16 in ranks and 17 in ranks:
        return {"type": "rocket", "name": "王炸", "main_rank": 17, "length": 2}

    # 炸弹
    if n == 4 and counts == [4]:
        return {"type": "bomb", "name": "炸弹", "main_rank": unique_ranks[0], "length": 1}

    # 单张
    if n == 1:
        return {"type": "single", "name": "单张", "main_rank": ranks[0], "length": 1}

    # 对子
    if n == 2 and counts == [2]:
        return {"type": "pair", "name": "对子", "main_rank": unique_ranks[0], "length": 1}

    # 三张
    if n == 3 and counts == [3]:
        return {"type": "triple", "name": "三张", "main_rank": unique_ranks[0], "length": 1}

    # 三带一
    if n == 4 and counts == [3, 1]:
        triple_rank = [r for r, c in counter.items() if c == 3][0]
        return {"type": "triple_single", "name": "三带一", "main_rank": triple_rank, "length": 1}

    # 三带二
    if n == 5 and counts == [3, 2]:
        triple_rank = [r for r, c in counter.items() if c == 3][0]
        return {"type": "triple_pair", "name": "三带二", "main_rank": triple_rank, "length": 1}

    # 顺子（5+张连续单牌，3-A，不含2和王）
    if n >= 5 and counts == [1] * n and all(r in STRAIGHT_RANKS for r in unique_ranks):
        if unique_ranks == list(range(unique_ranks[0], unique_ranks[0] + n)):
            return {"type": "straight", "name": f"顺子({n}张)", "main_rank": unique_ranks[0], "length": n}

    # 连对（3+对连续对子，3-A）
    if n >= 6 and n % 2 == 0 and counts == [2] * (n // 2) and all(r in STRAIGHT_RANKS for r in unique_ranks):
        pair_count = n // 2
        if unique_ranks == list(range(unique_ranks[0], unique_ranks[0] + pair_count)):
            return {"type": "pair_straight", "name": f"连对({pair_count}对)", "main_rank": unique_ranks[0], "length": pair_count}

    # 四带二（4张+2张单牌）
    if n == 6 and counts == [4, 1, 1]:
        four_rank = [r for r, c in counter.items() if c == 4][0]
        return {"type": "four_two", "name": "四带二", "main_rank": four_rank, "length": 1}

    # 四带两对（4张+2对）
    if n == 8 and counts == [4, 2, 2]:
        four_rank = [r for r, c in counter.items() if c == 4][0]
        return {"type": "four_two_pair", "name": "四带两对", "main_rank": four_rank, "length": 1}

    # 飞机（2+个连续三张，3-A）
    triple_ranks = sorted([r for r, c in counter.items() if c == 3])
    if len(triple_ranks) >= 2 and all(r in STRAIGHT_RANKS for r in triple_ranks):
        if triple_ranks == list(range(triple_ranks[0], triple_ranks[0] + len(triple_ranks))):
            t_count = len(triple_ranks)
            # 纯飞机
            if n == t_count * 3 and counts == [3] * t_count:
                return {"type": "airplane", "name": f"飞机({t_count}组)", "main_rank": triple_ranks[0], "length": t_count}
            # 飞机带单牌
            if n == t_count * 4 and counts.count(1) == t_count and counts.count(3) == t_count:
                return {"type": "airplane_single", "name": f"飞机带单牌({t_count}组)", "main_rank": triple_ranks[0], "length": t_count}
            # 飞机带对子
            if n == t_count * 5 and counts.count(2) == t_count and counts.count(3) == t_count:
                return {"type": "airplane_pair", "name": f"飞机带对子({t_count}组)", "main_rank": triple_ranks[0], "length": t_count}

    return None  # 非法牌型


def can_play(new_cards: list[int], last_cards: list[int] | None) -> bool:
    """判断 new_cards 能否压过 last_cards。last_cards 为 None 表示任意合法牌型都可出。"""
    new_type = identify_hand(new_cards)
    if new_type is None:
        return False
    if last_cards is None or len(last_cards) == 0:
        return True
    last_type = identify_hand(last_cards)
    if last_type is None:
        return True  # 上一手非法，视为无限制
    # 王炸最大
    if new_type["type"] == "rocket":
        return True
    # 炸弹可以压非炸弹非王炸
    if new_type["type"] == "bomb" and last_type["type"] not in ("bomb", "rocket"):
        return True
    # 同类型同长度比点数
    if new_type["type"] == last_type["type"] and new_type["length"] == last_type["length"]:
        return new_type["main_rank"] > last_type["main_rank"]
    # 炸弹压炸弹比点数
    if new_type["type"] == "bomb" and last_type["type"] == "bomb":
        return new_type["main_rank"] > last_type["main_rank"]
    return False


# ══════════ 实时出牌建议 ══════════

# 牌型价值权重（用于先手时选择"损失最小"的出牌：值越小越弱，先手优先出弱牌试探）
_TYPE_WEAKNESS = {
    "single": 1, "pair": 2, "triple": 3, "triple_single": 4, "triple_pair": 5,
    "straight": 6, "pair_straight": 7, "airplane": 8, "airplane_single": 9,
    "airplane_pair": 10, "four_two": 11, "four_two_pair": 12,
    "bomb": 100, "rocket": 200,
}


def _enumerate_candidates(hand: list[int]) -> list[dict]:
    """枚举手中所有可组成的牌型候选（含每张牌的具体列表）。"""
    ranks = [card_rank(c) for c in hand]
    counter = Counter(ranks)
    cands: list[dict] = []

    def add(ty: str, cards: list[int], main_rank: int, length: int):
        cands.append({"type": ty, "cards": cards, "main_rank": main_rank, "length": length})

    # 王炸
    if 16 in counter and 17 in counter:
        add("rocket", [16 * 4, 17 * 4], 17, 2)

    # 单张 / 对子 / 三张 / 炸弹（含4张）
    for r in sorted(counter):
        cards_r = [c for c in hand if card_rank(c) == r]
        add("single", [cards_r[0]], r, 1)
        if counter[r] >= 2:
            add("pair", cards_r[:2], r, 1)
        if counter[r] >= 3:
            add("triple", cards_r[:3], r, 1)
        if counter[r] >= 4:
            add("bomb", cards_r[:4], r, 1)

    # 三带一 / 三带二 / 四带二
    triple_ranks = [r for r in counter if counter[r] >= 3]
    for tr in triple_ranks:
        t3 = [c for c in hand if card_rank(c) == tr][:3]
        # 三带一（单牌不能是同一张三张的剩余，且优先不带王）
        for r in sorted(counter):
            if r == tr:
                continue
            extra = [c for c in hand if card_rank(c) == r][0]
            add("triple_single", t3 + [extra], tr, 1)
        # 三带二
        for r in sorted(counter):
            if r == tr or counter[r] < 2:
                continue
            pair = [c for c in hand if card_rank(c) == r][:2]
            add("triple_pair", t3 + pair, tr, 1)
    # 四带二（4张+2单）
    four_ranks = [r for r in counter if counter[r] == 4]
    for fr in four_ranks:
        f4 = [c for c in hand if card_rank(c) == fr][:4]
        others = [r for r in sorted(counter) if r != fr]
        if len(others) >= 2:
            add("four_two", f4 + [c for c in hand if card_rank(c) == others[0]][:1] +
                [c for c in hand if card_rank(c) == others[1]][:1], fr, 1)

    # 顺子（5+ 连续单牌，3-A）
    present = [r for r in STRAIGHT_RANKS if counter.get(r, 0) >= 1]
    for start in present:
        for length in range(5, 13):
            seq = list(range(start, start + length))
            if seq[-1] > 14 or not all(r in present for r in seq):
                break
            cards = []
            for r in seq:
                cards.append(next(c for c in hand if card_rank(c) == r and c not in cards))
            add("straight", cards, start, length)

    # 连对（3+ 对，3-A）
    pairs_present = [r for r in STRAIGHT_RANKS if counter.get(r, 0) >= 2]
    for start in pairs_present:
        for length in range(3, 11):
            seq = list(range(start, start + length))
            if seq[-1] > 14 or not all(r in pairs_present for r in seq):
                break
            cards = []
            for r in seq:
                cards.extend([c for c in hand if card_rank(c) == r][:2])
            add("pair_straight", cards, start, length)

    # 飞机（2+ 组连续三张，3-A），含带单/带对
    triples_present = [r for r in STRAIGHT_RANKS if counter.get(r, 0) >= 3]
    for start in triples_present:
        for length in range(2, 9):
            seq = list(range(start, start + length))
            if seq[-1] > 14 or not all(r in triples_present for r in seq):
                break
            base = []
            for r in seq:
                base.extend([c for c in hand if card_rank(c) == r][:3])
            t_count = length
            # 纯飞机
            add("airplane", list(base), start, t_count)
            # 飞机带单牌：需要 t_count 张不同的单牌（排除三张组本身）
            extra_singles = []
            for r in sorted(counter):
                if r in seq:
                    continue
                extra_singles.extend([c for c in hand if card_rank(c) == r])
                if len(extra_singles) >= t_count:
                    break
            if len(extra_singles) >= t_count:
                add("airplane_single", base + extra_singles[:t_count], start, t_count)
            # 飞机带对子：需要 t_count 对
            extra_pairs = []
            for r in sorted(counter):
                if r in seq:
                    continue
                if counter[r] >= 2:
                    extra_pairs.extend([c for c in hand if card_rank(c) == r][:2])
                    if len(extra_pairs) >= t_count * 2:
                        break
            if len(extra_pairs) >= t_count * 2:
                add("airplane_pair", base + extra_pairs[:t_count * 2], start, t_count)

    return cands


def _dedup_candidates(cands: list[dict]) -> list[dict]:
    """按（type, 排序后的牌集合）去重，防止同一组合被多次枚举。"""
    seen = set()
    out = []
    for c in cands:
        key = (c["type"], tuple(sorted(c["cards"])))
        if key in seen:
            continue
        seen.add(key)
        out.append(c)
    return out


def ai_bid(hand: list[int]) -> int:
    """AI 叫地主：按手牌强度决定叫分（0=不叫，1/2/3=叫分）。

    强度分 = 大王2.0 + 小王1.5 + 每个2计1.0 + 每个A计0.5 + 每个炸弹2.0。
    >=6 叫3分（直接当地主），>=4 叫2分，>=2 叫1分，否则不叫。
    """
    counter = Counter(card_rank(c) for c in hand)
    score = 0.0
    score += counter.get(17, 0) * 2.0   # 大王
    score += counter.get(16, 0) * 1.5   # 小王
    score += counter.get(15, 0) * 1.0   # 2
    score += counter.get(14, 0) * 0.5   # A
    for r, cnt in counter.items():
        if cnt >= 4 and r < 16:
            score += 2.0                # 炸弹
    if score >= 6.0:
        return 3
    if score >= 4.0:
        return 2
    if score >= 2.0:
        return 1
    return 0


# 先手出牌优先级：组合牌型优先（一次清多张），单张最后兜底
_LEAD_PRIORITY = {
    "airplane_pair": 0, "airplane_single": 0, "airplane": 0,
    "pair_straight": 0, "straight": 0,
    "triple_pair": 1, "triple_single": 1,
    "pair": 2,
    "triple": 3,
    "single": 4,
}


def suggest_play(hand: list[int], last_play: list[int] | None,
                 role: str = "farmer", top_k: int = 8) -> dict:
    """实时出牌建议：枚举手中全部可出候选，按策略排序，给出推荐。

    hand: 当前手牌（整数列表）；last_play: 上一手出的牌（None/空=先手）；
    role: 'landlord'（地主）/ 'farmer'（农民）/ ''（未知）。
    返回 {candidates, suggestion, should_pass, reason}。
    策略要点：
    - 先手：优先出最弱牌型（单张<对子<三张…）试探，保留炸弹/王炸；
      手牌整齐时优先出长顺子/连对减少张数。
    - 压牌：用最小点数能压的组合；压不动时建议过牌；身份为农民且
      队友刚出牌时优先过牌配合。
    - 炸弹/王炸只在关键局面使用（最后一手/必须抢出牌权/对方快走完）。
    """
    if not hand:
        return {"candidates": [], "suggestion": None, "should_pass": False,
                "reason": "手中无牌（已出完）。"}
    cands = _dedup_candidates(_enumerate_candidates(hand))

    # 过滤：能压过上一手
    playable = []
    if last_play:
        for c in cands:
            if can_play(c["cards"], last_play):
                playable.append(c)
    else:
        playable = cands

    if not playable:
        if last_play:
            last_info = identify_hand(last_play)
            last_name = HAND_TYPE_NAMES.get((last_info or {}).get("type", ""), "未知牌型")
            return {
                "candidates": [], "suggestion": None, "should_pass": True,
                "reason": f"手中没有能压过上一手（{last_name}）的牌，建议'不要'。",
            }
        return {
            "candidates": [], "suggestion": None, "should_pass": True,
            "reason": "手中没有可组成的合法牌型。",
        }

    # 排序：炸弹/王炸最后考虑；其余按"弱牌型优先、小点数优先"
    def sort_key(c: dict) -> tuple:
        ty = c["type"]
        if ty in ("bomb", "rocket"):
            return (10, c["main_rank"])
        return (_TYPE_WEAKNESS.get(ty, 50), c["main_rank"])

    ranked = sorted(playable, key=sort_key)

    # 选择推荐
    suggestion = None
    should_pass = False
    reason = ""
    if last_play:
        last_info = identify_hand(last_play)
        if last_info and last_info["type"] in ("bomb", "rocket"):
            if ranked[0]["type"] not in ("bomb", "rocket"):
                should_pass = True
                reason = "上一手是炸弹/王炸，除非有更大炸弹否则建议'不要'。"
            else:
                suggestion = ranked[0]
                if suggestion["type"] == "rocket":
                    reason = "用更大的王炸压制。"
                else:
                    reason = f"用更大的炸弹（{suggestion['main_rank']}）压制。"
        else:
            suggestion = ranked[0]
            if role == "farmer" and len(hand) > 6:
                reason = (f"农民身份且牌较多，用最小的{HAND_TYPE_NAMES.get(ranked[0]['type'], ranked[0]['type'])}"
                          f"（{hand_str(ranked[0]['cards'])}）压制，保留大牌。")
            else:
                reason = f"用{HAND_TYPE_NAMES.get(ranked[0]['type'], ranked[0]['type'])}（{hand_str(ranked[0]['cards'])}）压制上一手。"
    else:
        # 先手：优先出组合清牌（顺子/连对/飞机/三带/对子），单张留到最后兜底
        non_bomb = [c for c in ranked if c["type"] not in ("bomb", "rocket")]
        if non_bomb:
            lead_sorted = sorted(non_bomb, key=lambda c: (
                _LEAD_PRIORITY.get(c["type"], 50),
                -len(c["cards"]),
                c["main_rank"],
            ))
            suggestion = lead_sorted[0]
            reason = (f"先手出{len(suggestion['cards'])}张的"
                      f"{HAND_TYPE_NAMES.get(suggestion['type'], suggestion['type'])}"
                      f"（{hand_str(suggestion['cards'])}）清牌，单张/大牌留后面。")
        else:
            suggestion = ranked[0]
            reason = f"手中只剩炸弹/王炸，直接打出：{hand_str(ranked[0]['cards'])}。"

    # 组装候选输出（最多 top_k 个）
    candidates_out = []
    for c in ranked[:top_k]:
        candidates_out.append({
            "cards": hand_str(c["cards"]),
            "type": c["type"],
            "type_name": HAND_TYPE_NAMES.get(c["type"], c["type"]),
            "main_rank": c["main_rank"],
            "length": c["length"],
        })

    return {
        "candidates": candidates_out,
        "suggestion": {
            "cards": hand_str(suggestion["cards"]) if suggestion else None,
            "cards_raw": list(suggestion["cards"]) if suggestion else None,
            "type": suggestion["type"] if suggestion else None,
            "type_name": HAND_TYPE_NAMES.get(suggestion["type"], suggestion["type"]) if suggestion else None,
        } if suggestion else None,
        "should_pass": should_pass,
        "reason": reason,
    }


# ══════════ 牌局状态机 ══════════

class DouDizhuGame:
    """3人斗地主牌局。"""

    def __init__(self, player_names: list[str] | None = None, seed: int | None = None):
        if player_names is None:
            player_names = ["玩家", "AI_左", "AI_右"]
        if len(player_names) != 3:
            raise ValueError("斗地主必须3人")
        self.rng = random.Random(seed)
        self.players = [{"name": n, "hand": [], "role": "farmer"} for n in player_names]
        self.bottom_cards: list[int] = []
        self.landlord_idx: int | None = None
        self.current: int = 0
        self.last_play: list[int] = []
        self.last_player: int | None = None
        self.pass_count: int = 0
        self.stage: str = "dealing"  # dealing / bidding / playing / over
        self.history: list[dict] = []
        self._bid_history: list[dict] = []  # 当前轮叫分记录（重新发牌时清空）
        self._deal()

    def _deal(self):
        deck = new_deck()
        self.rng.shuffle(deck)
        for i in range(3):
            self.players[i]["hand"] = sort_cards(deck[i * 17:(i + 1) * 17])
        self.bottom_cards = sort_cards(deck[51:54])
        self.stage = "bidding"
        self.current = 0
        self._bid_history = []  # 清空当前轮叫分记录
        self.history.append({"event": "deal", "bottom": hand_str(self.bottom_cards)})

    def bid(self, player_idx: int, score: int) -> dict:
        """叫地主。score: 0=不叫, 1/2/3=叫分。返回 {ok, message}。"""
        if self.stage != "bidding":
            return {"ok": False, "error": "当前不是叫地主阶段"}
        if player_idx != self.current:
            return {"ok": False, "error": f"轮到 {self.players[self.current]['name']} 叫分"}
        if score not in (0, 1, 2, 3):
            return {"ok": False, "error": "叫分必须是 0(不叫)/1/2/3"}

        self.history.append({"player": self.players[player_idx]["name"], "bid": score})
        self._bid_history.append({"player": self.players[player_idx]["name"], "bid": score})

        if score == 3:
            # 叫3分直接当地主
            self._set_landlord(player_idx, 3)
            return {"ok": True, "message": f"{self.players[player_idx]['name']} 叫3分，成为地主！"}

        # 轮到下一个人
        self.current = (self.current + 1) % 3
        if self.current == 0:
            # 一轮结束，看最高分
            max_bid = max((h["bid"] for h in self._bid_history), default=0)
            if max_bid == 0:
                # 都不叫，重新发牌
                self._deal()
                return {"ok": True, "message": "无人叫地主，重新发牌"}
            # 找到叫最高分的人（倒序找第一个叫最高分的）
            for h in reversed(self._bid_history):
                if h["bid"] == max_bid:
                    name = h["player"]
                    idx = next(i for i, p in enumerate(self.players) if p["name"] == name)
                    self._set_landlord(idx, max_bid)
                    return {"ok": True, "message": f"{name} 叫{max_bid}分，成为地主！"}
        return {"ok": True, "message": f"{self.players[player_idx]['name']} {'不叫' if score == 0 else f'叫{score}分'}，轮到 {self.players[self.current]['name']}"}

    def _set_landlord(self, idx: int, score: int):
        self.landlord_idx = idx
        self.players[idx]["role"] = "landlord"
        self.players[idx]["hand"] = sort_cards(self.players[idx]["hand"] + self.bottom_cards)
        self.current = idx
        self.stage = "playing"
        self.last_play = []
        self.last_player = None
        self.pass_count = 0
        self.history.append({"event": "landlord", "player": self.players[idx]["name"], "score": score})

    def play(self, player_idx: int, cards: list[int]) -> dict:
        """出牌。cards: 要出的牌列表，空列表=不要。返回 {ok, message, result?}。"""
        if self.stage != "playing":
            return {"ok": False, "error": "当前不是出牌阶段"}
        if player_idx != self.current:
            return {"ok": False, "error": f"轮到 {self.players[self.current]['name']} 出牌"}

        p = self.players[player_idx]

        # 不要
        if not cards:
            if self.last_play is None or len(self.last_play) == 0:
                return {"ok": False, "error": "你是先手，必须出牌"}
            self.pass_count += 1
            self.history.append({"player": p["name"], "action": "pass"})
            self.current = (self.current + 1) % 3
            # 如果连续两人不要，出牌权回到最后出牌的人
            if self.pass_count >= 2:
                self.current = self.last_player
                self.last_play = []
                self.pass_count = 0
            return {"ok": True, "message": f"{p['name']} 不要", "game_state": self.get_state()}

        # 验证牌在手中
        hand_set = list(p["hand"])
        for c in cards:
            if c not in hand_set:
                return {"ok": False, "error": f"牌 {card_str(c)} 不在手中"}
            hand_set.remove(c)

        # 验证牌型
        if not can_play(cards, self.last_play):
            last_type = identify_hand(self.last_play)
            last_name = last_type["name"] if last_type else "无"
            return {"ok": False, "error": f"牌型不合法或压不过上一手({last_name})"}

        # 出牌
        for c in cards:
            p["hand"].remove(c)
        self.last_play = list(cards)
        self.last_player = player_idx
        self.pass_count = 0
        self.history.append({"player": p["name"], "action": "play", "cards": hand_str(cards)})

        # 检查胜利
        if len(p["hand"]) == 0:
            self.stage = "over"
            winner_role = "地主" if p["role"] == "landlord" else "农民"
            result = {"winner": p["name"], "role": winner_role,
                      "landlord": self.players[self.landlord_idx]["name"]}
            self.history.append({"event": "game_over", **result})
            return {"ok": True, "message": f"{p['name']}({winner_role})出完牌，获胜！", "result": result}

        self.current = (self.current + 1) % 3
        return {"ok": True, "message": f"{p['name']} 出 {hand_str(cards)}", "game_state": self.get_state()}

    def ai_play(self, player_idx: int) -> dict:
        """增强AI出牌：基于完整候选枚举 + 身份策略（复用 suggest_play）。

        策略：先手出最弱牌型试探并保留炸弹/王炸；压牌用最小能压的组合，
        压不过就过；农民身份且牌多时节省大牌；对方炸弹/王炸时除非有更大
        炸弹否则不出。
        """
        p = self.players[player_idx]
        hand = p["hand"]
        if not hand:
            return self.play(player_idx, [])

        role = "landlord" if p["role"] == "landlord" else "farmer"
        sug = suggest_play(hand, self.last_play if self.last_play else None, role=role)

        if sug.get("should_pass") or not sug.get("suggestion"):
            return self.play(player_idx, [])
        return self.play(player_idx, sug["suggestion"]["cards_raw"])

    def _find_playable_cards(self, hand: list[int], last_cards: list[int]) -> list[list[int]]:
        """找所有能压过 last_cards 的同类型牌组合（简化版，只找最小的几种）。"""
        last_type = identify_hand(last_cards)
        if last_type is None:
            return []
        results = []
        ranks = [card_rank(c) for c in hand]
        counter = Counter(ranks)

        if last_type["type"] == "single":
            for r in sorted(counter.keys()):
                if r > last_type["main_rank"]:
                    card = next(c for c in hand if card_rank(c) == r)
                    results.append([card])
                    break

        elif last_type["type"] == "pair":
            for r in sorted(counter.keys()):
                if r > last_type["main_rank"] and counter[r] >= 2:
                    cards = [c for c in hand if card_rank(c) == r][:2]
                    results.append(cards)
                    break

        elif last_type["type"] == "triple":
            for r in sorted(counter.keys()):
                if r > last_type["main_rank"] and counter[r] >= 3:
                    cards = [c for c in hand if card_rank(c) == r][:3]
                    results.append(cards)
                    break

        elif last_type["type"] == "triple_single":
            for r in sorted(counter.keys()):
                if r > last_type["main_rank"] and counter[r] >= 3:
                    triple = [c for c in hand if card_rank(c) == r][:3]
                    extra = next((c for c in hand if card_rank(c) != r), None)
                    if extra:
                        results.append(triple + [extra])
                        break

        elif last_type["type"] == "straight":
            length = last_type["length"]
            for start in STRAIGHT_RANKS:
                if start <= 14 - length + 1 and start > last_type["main_rank"]:
                    needed = list(range(start, start + length))
                    if all(counter.get(r, 0) >= 1 for r in needed):
                        cards = []
                        for r in needed:
                            cards.append(next(c for c in hand if card_rank(c) == r and c not in cards))
                        results.append(cards)
                        break

        return results

    def _find_bombs(self, hand: list[int]) -> list[list[int]]:
        counter = Counter(card_rank(c) for c in hand)
        bombs = []
        for r, cnt in sorted(counter.items()):
            if cnt >= 4 and r < 16:
                bombs.append([c for c in hand if card_rank(c) == r])
        return bombs

    def get_state(self) -> dict:
        p = self.players[self.current]
        return {
            "stage": self.stage,
            "current_player": p["name"],
            "current_role": "地主" if p["role"] == "landlord" else "农民",
            "current_hand": hand_str(p["hand"]),
            "current_hand_count": len(p["hand"]),
            "last_play": hand_str(self.last_play) if self.last_play else "（无）",
            "last_player": self.players[self.last_player]["name"] if self.last_player is not None else None,
            "landlord": self.players[self.landlord_idx]["name"] if self.landlord_idx is not None else None,
            "bottom_cards": hand_str(self.bottom_cards) if self.stage != "bidding" else "（未揭晓）",
            "players": [
                {"name": pl["name"], "role": "地主" if pl["role"] == "landlord" else "农民",
                 "card_count": len(pl["hand"]),
                 "hand": hand_str(pl["hand"]) if pl is p else f"（{len(pl['hand'])}张）"}
                for pl in self.players
            ],
        }
