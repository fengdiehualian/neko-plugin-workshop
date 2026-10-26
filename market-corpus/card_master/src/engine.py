"""card_master engine — 牌组、牌型评估、德州扑克牌局、胜率计算、自我对弈。

纯标准库实现，无外部依赖。牌用整数 0-51 表示：
  rank = card // 4  (0=2, 1=3, ..., 8=10, 9=J, 10=Q, 11=K, 12=A)
  suit = card % 4   (0=♠, 1=♥, 2=♦, 3=♣)
字符串表示：rank_char + suit_char，如 "As" "Kh" "Td" "2c"。
"""
from __future__ import annotations

import random
from itertools import combinations

# ══════════ 常量 ══════════

RANK_CHARS = "23456789TJQKA"
SUIT_CHARS = "shdc"
SUIT_UNICODE = {"s": "♠", "h": "♥", "d": "♦", "c": "♣"}

HAND_NAMES = [
    "高牌", "一对", "两对", "三条", "顺子",
    "同花", "葫芦", "四条", "同花顺", "皇家同花顺",
]

# ══════════ 牌的工具函数 ══════════


def card_str(card: int) -> str:
    """整数牌 → 字符串，如 0→'2s'，51→'Ac'。"""
    return RANK_CHARS[card // 4] + SUIT_CHARS[card % 4]


def parse_card(s: str) -> int:
    """字符串 → 整数牌。支持 'As' 'ah' 'TD' '2c' 大小写。"""
    s = s.strip()
    if len(s) != 2:
        raise ValueError(f"牌格式错误: '{s}'（应为2字符，如 As）")
    r = RANK_CHARS.find(s[0].upper())
    su = SUIT_CHARS.find(s[1].lower())
    if r < 0 or su < 0:
        raise ValueError(f"牌格式错误: '{s}'")
    return r * 4 + su


def hand_str(cards: list[int]) -> str:
    """手牌列表 → 可读字符串，如 [51,50] → 'Ac Ah'。"""
    return " ".join(card_str(c) for c in sorted(cards, reverse=True))


def new_deck(rng: random.Random | None = None) -> list[int]:
    """洗好的 52 张牌。"""
    deck = list(range(52))
    (rng or random).shuffle(deck)
    return deck


# ══════════ 牌型评估（德州扑克，5张 / 7选5） ══════════


def _evaluate_5(cards: list[int]) -> tuple[int, list[int]]:
    """评估 5 张牌的牌型。返回 (等级 0-9, 决胜牌列表)。

    决胜牌列表用于同牌型比大小，按重要性从高到低排列。
    """
    ranks = sorted((c // 4 for c in cards), reverse=True)
    suits = [c % 4 for c in cards]

    # 统计点数出现次数
    count: dict[int, int] = {}
    for r in ranks:
        count[r] = count.get(r, 0) + 1
    # 按 (次数, 点数) 排序
    groups = sorted(count.items(), key=lambda x: (x[1], x[0]), reverse=True)

    is_flush = len(set(suits)) == 1

    # 顺子检测（A 可作 1）
    unique_ranks = sorted(set(ranks), reverse=True)
    is_straight = False
    straight_high = 0
    if len(unique_ranks) == 5:
        if unique_ranks[0] - unique_ranks[4] == 4:
            is_straight = True
            straight_high = unique_ranks[0]
        # A-2-3-4-5 特殊顺子
        elif unique_ranks == [12, 3, 2, 1, 0]:
            is_straight = True
            straight_high = 3  # 5 高（用 rank 3=5 表示）

    if is_straight and is_flush:
        if straight_high == 12:  # 10-J-Q-K-A
            return (9, [12])
        return (8, [straight_high])

    # 四条
    if groups[0][1] == 4:
        kicker = groups[1][0] if len(groups) > 1 else -1
        return (7, [groups[0][0], kicker])

    # 葫芦
    if groups[0][1] == 3 and groups[1][1] >= 2:
        return (6, [groups[0][0], groups[1][0]])

    if is_flush:
        return (5, ranks)

    if is_straight:
        return (4, [straight_high])

    # 三条
    if groups[0][1] == 3:
        kickers = sorted([g[0] for g in groups[1:]], reverse=True)
        return (3, [groups[0][0]] + kickers)

    # 两对
    if groups[0][1] == 2 and groups[1][1] == 2:
        pair_ranks = sorted([groups[0][0], groups[1][0]], reverse=True)
        kicker = groups[2][0] if len(groups) > 2 else -1
        return (2, pair_ranks + [kicker])

    # 一对
    if groups[0][1] == 2:
        kickers = sorted([g[0] for g in groups[1:]], reverse=True)
        return (1, [groups[0][0]] + kickers)

    # 高牌
    return (0, ranks)


def evaluate_hand(cards: list[int]) -> dict:
    """评估 5 或 7 张牌的最佳牌型。返回 {name, category, tiebreaker, cards}。"""
    if len(cards) == 5:
        cat, tb = _evaluate_5(cards)
        best = cards
    elif len(cards) >= 5:
        best_cat = -1
        best_tb: list[int] = []
        best_combo: list[int] = []
        for combo in combinations(cards, 5):
            cat, tb = _evaluate_5(list(combo))
            if (cat, tb) > (best_cat, best_tb):
                best_cat, best_tb, best_combo = cat, tb, list(combo)
        cat, tb, best = best_cat, best_tb, best_combo
    else:
        return {"name": "牌不足", "category": -1, "tiebreaker": [], "cards": cards}

    return {
        "name": HAND_NAMES[cat],
        "category": cat,
        "tiebreaker": tb,
        "cards": best,
        "cards_str": hand_str(best),
    }


def compare_hands(a: list[int], b: list[int]) -> int:
    """比较两手牌（5或7张）。返回 1=a赢, -1=b赢, 0=平局。"""
    ea = evaluate_hand(a)
    eb = evaluate_hand(b)
    if (ea["category"], ea["tiebreaker"]) > (eb["category"], eb["tiebreaker"]):
        return 1
    if (ea["category"], ea["tiebreaker"]) < (eb["category"], eb["tiebreaker"]):
        return -1
    return 0


# ══════════ 德州扑克牌局 ══════════


class TexasHoldemGame:
    """德州扑克牌局状态机。支持 2-10 人，无限注。"""

    STAGES = ["preflop", "flop", "turn", "river", "showdown"]

    def __init__(self, players: list[dict], small_blind: int = 10, big_blind: int = 20,
                 seed: int | None = None):
        """players: [{name, chips}]，至少2人。"""
        if len(players) < 2:
            raise ValueError("至少需要 2 名玩家")
        if len(players) > 10:
            raise ValueError("最多 10 名玩家")
        self.rng = random.Random(seed)
        self.players = [
            {"name": p["name"], "chips": p["chips"], "hand": [],
             "bet": 0, "folded": False, "all_in": False, "has_acted": False}
            for p in players
        ]
        self.small_blind = small_blind
        self.big_blind = big_blind
        self.deck: list[int] = []
        self.community: list[int] = []
        self.pot = 0
        self.stage = "preflop"
        self.dealer = 0  # 庄家位索引
        self.current = 0  # 当前行动玩家索引
        self.current_bet = 0
        self.min_raise = big_blind
        self.history: list[dict] = []
        self.hand_number = 0
        self._start_hand()

    def _active_players(self) -> list[int]:
        """未弃牌的玩家索引。"""
        return [i for i, p in enumerate(self.players) if not p["folded"]]

    def _next_active(self, idx: int) -> int:
        """从 idx 之后下一个未弃牌玩家。"""
        n = len(self.players)
        for step in range(1, n + 1):
            i = (idx + step) % n
            if not self.players[i]["folded"]:
                return i
        return idx

    def _start_hand(self):
        """开始新一手牌：洗牌、发牌、下盲注。"""
        self.hand_number += 1
        self.deck = new_deck(self.rng)
        self.community = []
        self.pot = 0
        self.stage = "preflop"
        self.current_bet = 0
        self.min_raise = self.big_blind
        n = len(self.players)

        for p in self.players:
            p["hand"] = []
            p["bet"] = 0
            p["folded"] = False
            p["all_in"] = False
            p["has_acted"] = False

        # 发牌（每人2张，从庄家下家开始）
        for _ in range(2):
            for i in range(n):
                idx = (self.dealer + 1 + i) % n
                self.players[idx]["hand"].append(self.deck.pop())

        # 下盲注
        sb_idx = (self.dealer + 1) % n
        bb_idx = (self.dealer + 2) % n
        self._place_bet(sb_idx, self.small_blind)
        self._place_bet(bb_idx, self.big_blind)
        self.current_bet = self.big_blind
        self.current = (bb_idx + 1) % n
        # 跳过已弃牌/全下
        while self.players[self.current]["folded"] or self.players[self.current]["all_in"]:
            self.current = (self.current + 1) % n

        self.history.append({
            "hand": self.hand_number,
            "stage": "preflop",
            "dealer": self.players[self.dealer]["name"],
            "small_blind": self.small_blind,
            "big_blind": self.big_blind,
        })

    def _place_bet(self, idx: int, amount: int) -> int:
        """玩家下注，返回实际下注额（不超过其筹码）。"""
        p = self.players[idx]
        actual = min(amount, p["chips"])
        p["chips"] -= actual
        p["bet"] += actual
        self.pot += actual
        if p["chips"] == 0:
            p["all_in"] = True
        return actual

    def _bet_to_match(self) -> int:
        """当前玩家需要跟注的金额。"""
        p = self.players[self.current]
        return max(0, self.current_bet - p["bet"])

    def _is_betting_round_complete(self) -> bool:
        """判断本轮下注是否结束（所有未弃牌玩家下注额相等且都已行动）。"""
        active = self._active_players()
        if len(active) <= 1:
            return True
        all_matched = all(self.players[i]["bet"] == self.current_bet for i in active)
        all_acted = all(self.players[i]["has_acted"] or self.players[i]["all_in"] for i in active)
        return all_matched and all_acted

    def _advance_stage(self):
        """进入下一阶段：发公共牌、重置下注。"""
        # 收集所有玩家的下注到底池（bet 已经在 pot 里了，重置 bet）
        for p in self.players:
            p["bet"] = 0
            p["has_acted"] = False
        self.current_bet = 0
        self.min_raise = self.big_blind

        if self.stage == "preflop":
            self.community = self.deck[-3:]  # 翻牌3张（从牌堆末尾取）
            del self.deck[-3:]
            self.stage = "flop"
        elif self.stage == "flop":
            self.community.append(self.deck.pop())
            self.stage = "turn"
        elif self.stage == "turn":
            self.community.append(self.deck.pop())
            self.stage = "river"
        elif self.stage == "river":
            self.stage = "showdown"
            return

        # 当前行动玩家 = 庄家下家第一个未弃牌
        self.current = self._next_active(self.dealer)
        self.history.append({"hand": self.hand_number, "stage": self.stage,
                             "community": hand_str(self.community)})

    def _showdown(self) -> dict:
        """摊牌比大小，分配底池。返回结果。"""
        active = self._active_players()
        results = []
        for i in active:
            p = self.players[i]
            ev = evaluate_hand(p["hand"] + self.community)
            results.append({"idx": i, "name": p["name"], "hand": ev, "chips": p["chips"]})

        # 找赢家（可能多人平分）
        best = max(results, key=lambda r: (r["hand"]["category"], r["hand"]["tiebreaker"]))
        winners = [r for r in results
                   if (r["hand"]["category"], r["hand"]["tiebreaker"]) ==
                   (best["hand"]["category"], best["hand"]["tiebreaker"])]
        share = self.pot // len(winners)
        remainder = self.pot - share * len(winners)
        for i, w in enumerate(winners):
            # 剩余筹码给第一个赢家（按位置顺序）
            self.players[w["idx"]]["chips"] += share + (remainder if i == 0 else 0)

        result = {
            "stage": "showdown",
            "pot": self.pot,
            "community": hand_str(self.community),
            "winners": [{"name": w["name"], "hand": w["hand"]["name"],
                          "cards": hand_str(self.players[w["idx"]]["hand"]),
                          "best_5": w["hand"]["cards_str"], "share": share} for w in winners],
            "all_hands": [{"name": r["name"], "cards": hand_str(self.players[r["idx"]]["hand"]),
                            "best": r["hand"]["name"]} for r in results],
        }
        self.history.append(result)
        self.pot = 0
        return result

    # ══════════ 公开 API ══════════

    def action(self, action: str, amount: int = 0) -> dict:
        """执行行动。action: fold/check/call/raise/all_in。amount 为 raise 总额。

        返回 {ok, message, game_state} 或 {ok:False, error}。
        """
        if self.stage == "showdown":
            return {"ok": False, "error": "牌局已结束，请调用 next_hand 开始新一手"}

        p = self.players[self.current]
        if p["folded"] or p["all_in"]:
            return {"ok": False, "error": f"{p['name']} 已弃牌或全下，不能行动"}

        action = action.lower().strip()
        to_call = self._bet_to_match()

        if action == "fold":
            p["folded"] = True
            p["has_acted"] = True
            self.history.append({"hand": self.hand_number, "stage": self.stage,
                                 "player": p["name"], "action": "fold"})
            # 只剩一人 → 直接获胜
            if len(self._active_players()) == 1:
                winner = self.players[self._active_players()[0]]
                winner["chips"] += self.pot
                result = {"stage": "fold_end", "winner": winner["name"], "pot": self.pot,
                          "community": hand_str(self.community)}
                self.history.append(result)
                self.pot = 0
                self.stage = "showdown"
                return {"ok": True, "message": f"{p['name']} 弃牌，{winner['name']} 赢得 {result['pot']} 底池", "result": result}

        elif action == "check":
            if to_call > 0:
                return {"ok": False, "error": f"当前需跟注 {to_call}，不能 check（请 call 或 fold）"}
            p["has_acted"] = True
            self.history.append({"hand": self.hand_number, "stage": self.stage,
                                 "player": p["name"], "action": "check"})

        elif action == "call":
            if to_call == 0:
                return {"ok": False, "error": "无需跟注，请 check"}
            actual = self._place_bet(self.current, to_call)
            p["has_acted"] = True
            self.history.append({"hand": self.hand_number, "stage": self.stage,
                                 "player": p["name"], "action": "call", "amount": actual})
            if p["all_in"]:
                pass  # 全下后自动进入下一阶段由轮询处理

        elif action == "raise":
            if amount <= 0:
                return {"ok": False, "error": "raise 需要指定金额（总额，含已下注）"}
            total_bet = amount
            if total_bet < self.current_bet + self.min_raise:
                return {"ok": False, "error": f"加注至少到 {self.current_bet + self.min_raise}（当前 {self.current_bet}，最小加注 {self.min_raise}）"}
            add = total_bet - p["bet"]
            actual = self._place_bet(self.current, add)
            if p["all_in"] and p["bet"] < total_bet:
                # 全下但不够加注额 → 视为全下跟注，不重开下注轮
                pass
            else:
                previous_bet = self.current_bet
                self.current_bet = total_bet
                self.min_raise = max(self.big_blind, total_bet - previous_bet)
                # 重置其他玩家 has_acted（除了当前玩家）
                for i, pl in enumerate(self.players):
                    if i != self.current and not pl["folded"]:
                        pl["has_acted"] = False
            p["has_acted"] = True
            self.history.append({"hand": self.hand_number, "stage": self.stage,
                                 "player": p["name"], "action": "raise", "amount": total_bet})

        elif action == "all_in":
            all_in_amount = p["chips"] + p["bet"]
            add = p["chips"]
            self._place_bet(self.current, add)
            p["all_in"] = True
            p["has_acted"] = True
            if all_in_amount > self.current_bet:
                self.min_raise = max(self.big_blind, all_in_amount - self.current_bet)
                self.current_bet = all_in_amount
                for i, pl in enumerate(self.players):
                    if i != self.current and not pl["folded"]:
                        pl["has_acted"] = False
            self.history.append({"hand": self.hand_number, "stage": self.stage,
                                 "player": p["name"], "action": "all_in", "amount": all_in_amount})

        else:
            return {"ok": False, "error": f"未知行动: {action}（支持 fold/check/call/raise/all_in）"}

        # 检查是否进入下一阶段
        if self._is_betting_round_complete():
            self._advance_stage()
            if self.stage == "showdown":
                result = self._showdown()
                return {"ok": True, "message": "摊牌", "result": result}
        else:
            # 下一个玩家
            self.current = self._next_active(self.current)

        return {"ok": True, "message": f"{p['name']} {action}", "game_state": self.get_state()}

    def next_hand(self) -> dict:
        """开始下一手牌（移动庄家位）。"""
        self.dealer = (self.dealer + 1) % len(self.players)
        self._start_hand()
        return {"ok": True, "message": f"第 {self.hand_number} 手牌开始", "game_state": self.get_state()}

    def get_state(self) -> dict:
        """获取当前牌局状态（对当前玩家可见的信息）。"""
        current_p = self.players[self.current]
        return {
            "hand_number": self.hand_number,
            "stage": self.stage,
            "pot": self.pot,
            "current_bet": self.current_bet,
            "to_call": self._bet_to_match(),
            "min_raise_to": self.current_bet + self.min_raise,
            "community": hand_str(self.community) if self.community else "（无）",
            "community_count": len(self.community),
            "current_player": current_p["name"],
            "current_player_hand": hand_str(current_p["hand"]),
            "current_player_chips": current_p["chips"],
            "players": [
                {"name": p["name"], "chips": p["chips"], "bet": p["bet"],
                 "folded": p["folded"], "all_in": p["all_in"],
                 "has_acted": p["has_acted"],
                 "hand": hand_str(p["hand"]) if p is current_p else "（背面）"}
                for p in self.players
            ],
            "active_count": len(self._active_players()),
        }


# ══════════ 胜率计算（蒙特卡洛） ══════════


def calculate_odds(hand: list[int], community: list[int], opponents: int = 1,
                   simulations: int = 2000, seed: int | None = None) -> dict:
    """蒙特卡洛模拟计算当前手牌胜率。

    hand: 你的2张手牌
    community: 公共牌（0-5张）
    opponents: 对手数量
    simulations: 模拟次数
    返回 {win_rate, tie_rate, lose_rate, simulations, details}
    """
    rng = random.Random(seed)
    known = set(hand + community)
    wins = 0
    ties = 0
    losses = 0

    for _ in range(simulations):
        remaining = [c for c in range(52) if c not in known]
        rng.shuffle(remaining)

        # 发完整公共牌（补到5张）
        full_community = list(community)
        need = 5 - len(full_community)
        full_community += remaining[:need]
        del remaining[:need]

        # 发对手手牌
        my_best = evaluate_hand(hand + full_community)
        my_key = (my_best["category"], my_best["tiebreaker"])

        all_tie = True
        any_win = False
        for _ in range(opponents):
            opp_hand = remaining[:2]
            del remaining[:2]
            opp_best = evaluate_hand(opp_hand + full_community)
            opp_key = (opp_best["category"], opp_best["tiebreaker"])
            if opp_key > my_key:
                any_win = False
                all_tie = False
                break
            elif opp_key < my_key:
                any_win = True
                all_tie = False
        else:
            if all_tie:
                ties += 1
            elif any_win:
                wins += 1
            else:
                losses += 1
            continue
        losses += 1

    total = wins + ties + losses
    return {
        "win_rate": round(wins / total * 100, 1) if total else 0,
        "tie_rate": round(ties / total * 100, 1) if total else 0,
        "lose_rate": round(losses / total * 100, 1) if total else 0,
        "simulations": total,
        "opponents": opponents,
        "your_hand": hand_str(hand),
        "community": hand_str(community) if community else "（无）",
    }


# ══════════ 出牌建议 ══════════


def suggest_from_state(hand: list[int], community: list[int], to_call: int, pot: int,
                       chips: int, active_count: int, big_blind: int,
                       current_bet: int = 0, simulations: int = 1500,
                       seed: int | None = None) -> dict:
    """从外部局面状态直接给出出牌建议（不依赖 game 对象）。

    供实时打牌（live）模式复用：YUI 从屏幕上读取局面后传入，返回与
    suggest_action 相同的决策结构。参数：
    hand: 你的2张手牌；community: 公共牌（0-5张）；to_call: 当前需跟注额；
    pot: 底池；chips: 你的筹码；active_count: 仍在局中的玩家人数；
    big_blind: 大盲注；current_bet: 当前轮最大下注（live 模式下可不传）；
    seed: 蒙特卡洛随机种子（不传则每次随机，适合实时使用；传固定值可复现）。
    """
    # 计算胜率
    odds = calculate_odds(hand, community, opponents=max(1, active_count - 1),
                          simulations=simulations, seed=seed)
    win_rate = odds["win_rate"]
    tie_rate = odds["tie_rate"]
    equity = win_rate + tie_rate * 0.5  # 有效胜率（平局算一半）

    # 底池赔率
    if to_call > 0:
        pot_odds = to_call / (pot + to_call) * 100
        pot_odds_str = f"跟注 {to_call} 赢 {pot + to_call}，底池赔率 {pot_odds:.1f}%"
    else:
        pot_odds = 0
        pot_odds_str = "无需跟注（可 check）"

    # 决策
    if to_call == 0:
        if equity > 60:
            suggestion = "raise"
            reason = f"胜率 {win_rate}% 很高，主动加注建立底池"
            raise_amount = min(chips, max(big_blind * 3, int(pot * 0.6)))
        elif equity > 40:
            suggestion = "check"
            reason = f"胜率 {win_rate}% 中等，check 看翻牌，控制底池"
            raise_amount = 0
        else:
            suggestion = "check"
            reason = f"胜率 {win_rate}% 偏低，check 免费看牌"
            raise_amount = 0
    else:
        if equity > pot_odds + 15:
            suggestion = "raise"
            reason = f"胜率 {win_rate}% 远超底池赔率 {pot_odds:.1f}%，加注获取价值"
            raise_amount = min(chips, max(to_call * 2 + current_bet, int(pot * 0.75)))
        elif equity > pot_odds:
            suggestion = "call"
            reason = f"胜率 {win_rate}% 略高于底池赔率 {pot_odds:.1f}%，跟注合理"
            raise_amount = 0
        elif equity > pot_odds - 5 and win_rate > 20:
            suggestion = "call"
            reason = f"胜率 {win_rate}% 接近底池赔率 {pot_odds:.1f}%，可跟注投机（隐含赔率）"
            raise_amount = 0
        else:
            suggestion = "fold"
            reason = f"胜率 {win_rate}% 低于底池赔率 {pot_odds:.1f}%，弃牌节省筹码"
            raise_amount = 0

    return {
        "suggestion": suggestion,
        "reason": reason,
        "raise_to": raise_amount if suggestion == "raise" else None,
        "win_rate": win_rate,
        "tie_rate": tie_rate,
        "equity": round(equity, 1),
        "pot_odds": round(pot_odds, 1) if to_call > 0 else 0,
        "pot_odds_detail": pot_odds_str,
        "your_hand": hand_str(hand),
        "community": hand_str(community) if community else "（无）",
        "pot": pot,
        "to_call": to_call,
        "chips": chips,
    }


def suggest_action(game: TexasHoldemGame, simulations: int = 1500) -> dict:
    """基于当前牌局给出出牌建议（胜率 + 底池赔率 + 位置）。"""
    state = game.get_state()
    p = game.players[game.current]
    return suggest_from_state(
        list(p["hand"]), list(game.community),
        state["to_call"], state["pot"], p["chips"],
        state["active_count"], game.big_blind,
        current_bet=game.current_bet, simulations=simulations,
    )


# ══════════ 自我对弈 ══════════


def self_play(players: list[dict] | None = None, rounds: int = 1,
               small_blind: int = 10, big_blind: int = 20,
               seed: int | None = None, verbose: bool = True) -> dict:
    """AI 自我对弈训练。每个玩家基于胜率做简单决策。

    返回 {rounds, results, summary, play_log}
    """
    if players is None:
        players = [{"name": f"AI_{i+1}", "chips": 1000} for i in range(6)]

    game = TexasHoldemGame(players, small_blind, big_blind, seed)
    play_log = []
    results = []

    for r in range(rounds):
        # 检查是否有人出局
        alive = [p for p in game.players if p["chips"] > 0]
        if len(alive) <= 1:
            play_log.append({"round": r, "event": "game_over", "winner": alive[0]["name"] if alive else "无"})
            break

        round_log = []
        max_actions = 100  # 防止死循环
        actions = 0
        while game.stage != "showdown" and actions < max_actions:
            actions += 1
            sug = suggest_action(game, simulations=800)
            act = sug["suggestion"]
            amt = sug.get("raise_to") or 0
            result = game.action(act, amount=amt)
            if verbose:
                round_log.append({
                    "stage": game.stage,
                    "player": game.players[game.current]["name"] if game.stage != "showdown" else "-",
                    "action": act,
                    "amount": amt,
                    "win_rate": sug["win_rate"],
                    "reason": sug["reason"],
                })
            if not result.get("ok"):
                round_log.append({"error": result.get("error")})
                break

        if game.stage == "showdown":
            # 获取最后一次 showdown 结果
            showdown = next((h for h in reversed(game.history) if h.get("stage") == "showdown" or h.get("stage") == "fold_end"), {})
            results.append({"round": r + 1, "result": showdown})
            if verbose:
                round_log.append({"showdown": showdown})

        play_log.append({"round": r + 1, "actions": round_log})

        if r < rounds - 1 and game.stage == "showdown":
            game.next_hand()

    # 总结
    chip_summary = sorted([{"name": p["name"], "chips": p["chips"]} for p in game.players],
                          key=lambda x: x["chips"], reverse=True)
    return {
        "rounds_played": len(results),
        "results": results,
        "final_chips": chip_summary,
        "winner": chip_summary[0]["name"] if chip_summary else None,
        "play_log": play_log if verbose else "（已省略详细日志，设 verbose=True 查看）",
    }
