"""mini_games 游戏核心逻辑（纯标准库，不依赖插件 SDK，便于单测）

包含三大玩法：
  - 抽卡 Gacha：单抽 / 十连 / 保底 SSR(UR) / 十连保底 R / 图鉴统计
  - 今日运势：按「日期 + 会话」确定性随机，每天同一人结果稳定
  - 摇骰子：可指定颗数与面数
"""

from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass, field
from datetime import date
from typing import Dict, List, Optional

RARITIES = ["N", "R", "SR", "SSR", "UR"]
RARITY_ORDER = {"N": 0, "R": 1, "SR": 2, "SSR": 3, "UR": 4}

DEFAULT_WEIGHTS = {"N": 600, "R": 280, "SR": 90, "SSR": 25, "UR": 5}

# 猫娘主题卡池
POOL: Dict[str, List[str]] = {
    "UR": ["限定·星河猫娘", "黄金猫罐头", "创世小鱼干", "喵之神谕"],
    "SSR": ["彩虹项圈", "九命猫铃", "月光绒球", "星空猫窝"],
    "SR": ["毛线团", "猫薄荷", "小鱼饼干", "逗猫棒", "暖阳抱枕"],
    "R": ["普通猫粮", "纸箱子", "软软垫", "小鱼干×3", "猫抓板"],
    "N": ["一撮猫毛", "空罐头", "飘落的叶子", "掉落的橡皮筋"],
}

RARITY_EMOJI = {"N": "⚪", "R": "🟢", "SR": "🔵", "SSR": "🟣", "UR": "🟡"}

PITY_SSR_DEFAULT = 50


@dataclass
class Session:
    """单个玩家/会话的抽卡进度"""

    pity: int = 0
    total_pulls: int = 0
    collection: Dict[str, int] = field(default_factory=dict)

    def record(self, rarity: str, item: str) -> None:
        self.total_pulls += 1
        self.collection[item] = self.collection.get(item, 0) + 1
        if RARITY_ORDER[rarity] >= RARITY_ORDER["SSR"]:
            self.pity = 0
        else:
            self.pity += 1

    def summary(self) -> Dict:
        unique = len(self.collection)
        total_items = sum(self.collection.values())
        top = sorted(self.collection.items(), key=lambda kv: kv[1], reverse=True)[:5]
        return {
            "total_pulls": self.total_pulls,
            "pity": self.pity,
            "unique_items": unique,
            "total_items": total_items,
            "top_items": [{"item": k, "count": v} for k, v in top],
        }


class GachaEngine:
    def __init__(
        self,
        weights: Optional[Dict[str, int]] = None,
        pity_ssr: int = PITY_SSR_DEFAULT,
        rng: Optional[random.Random] = None,
    ):
        self.weights = dict(DEFAULT_WEIGHTS)
        if weights:
            for k in RARITIES:
                if k in weights and isinstance(weights[k], int) and weights[k] > 0:
                    self.weights[k] = weights[k]
        self.pity_ssr = pity_ssr
        self._rng = rng or random.Random()

    def _roll_rarity(self, pity: int) -> str:
        # 保底：达到阈值后强制 SSR 及以上
        if self.pity_ssr and (pity + 1) >= self.pity_ssr:
            choices = ["SSR", "UR"]
            w = [self.weights["SSR"], self.weights["UR"]]
            return self._rng.choices(choices, weights=w, k=1)[0]
        rarities = RARITIES
        w = [self.weights[r] for r in rarities]
        return self._rng.choices(rarities, weights=w, k=1)[0]

    def pull_one(self, session: Session) -> Dict:
        rarity = self._roll_rarity(session.pity)
        item = self._rng.choice(POOL[rarity])
        session.record(rarity, item)
        return _format_pull(rarity, item, session)

    def pull_ten(self, session: Session) -> Dict:
        results = []
        for i in range(10):
            # 第十抽兜底：若前面 9 抽都没出 R 及以上，强制保底 R
            if i == 9 and all(
                RARITY_ORDER[r["rarity"]] < RARITY_ORDER["R"] for r in results
            ):
                rarity = self._rng.choices(
                    ["R", "SR", "SSR", "UR"],
                    weights=[
                        self.weights["R"],
                        self.weights["SR"],
                        self.weights["SSR"],
                        self.weights["UR"],
                    ],
                    k=1,
                )[0]
                item = self._rng.choice(POOL[rarity])
                session.record(rarity, item)
                results.append(_format_pull(rarity, item, session))
            else:
                results.append(self.pull_one(session))
        best = max(results, key=lambda r: RARITY_ORDER[r["rarity"]])
        return {
            "results": results,
            "best_rarity": best["rarity"],
            "best_item": best["item"],
            "pity": session.pity,
            "total_pulls": session.total_pulls,
            "message": _ten_message(best, results),
        }


def _format_pull(rarity: str, item: str, session: Session) -> Dict:
    is_rare = RARITY_ORDER[rarity] >= RARITY_ORDER["SSR"]
    emoji = RARITY_EMOJI[rarity]
    msg = f"{emoji} {rarity}！猫娘捧出了【{item}】" + (
        " ✨欧气爆棚！" if is_rare else ""
    )
    return {
        "rarity": rarity,
        "item": item,
        "is_rare": is_rare,
        "pity": session.pity,
        "total_pulls": session.total_pulls,
        "message": msg,
    }


def _ten_message(best: Dict, results: List[Dict]) -> str:
    rare = [r for r in results if RARITY_ORDER[r["rarity"]] >= RARITY_ORDER["SSR"]]
    if rare:
        names = "、".join(f"【{r['item']}】" for r in rare)
        return f"十连出货！抽到 {len(rare)} 个稀有色：{names}，猫娘开心地转圈圈～"
    return "十连结束～虽然没有稀有色，但猫娘说你下次一定出货！"


# ── 今日运势 ──
_FORTUNE_LEVELS = [
    ("大吉", 10),
    ("中吉", 25),
    ("小吉", 30),
    ("平", 20),
    ("凶", 15),
]
_FORTUNE_COLORS = ["红", "橙", "黄", "绿", "青", "蓝", "紫", "粉", "白"]
_FORTUNE_ACTS = {
    "大吉": ["表白", "抽卡", "买彩票", "开工大吉", "发自拍"],
    "中吉": ["摸鱼", "聚餐", "逛街", "回消息", "晒太阳"],
    "小吉": ["读书", "运动", "整理房间", "撸猫", "喝奶茶"],
    "平": ["搬砖", "写代码", "好好吃饭", "早点睡", "散步"],
    "凶": ["谨慎投资", "少熬夜", "备份文件", "远离争吵", "喝热水"],
}
_FORTUNE_WARN = {
    "大吉": "今天运气拉满，冲就完事了！",
    "中吉": "稳中有喜，适合做点开心的小事。",
    "小吉": "平淡是真，小确幸正在路上。",
    "平": "平平淡淡，先把该做的做完。",
    "凶": "诸事稍缓，今天宜保守不宜冲动。",
}


def daily_fortune(session_id: str, day: Optional[str] = None) -> Dict:
    day = day or date.today().isoformat()
    seed_str = f"{day}#{session_id}"
    h = int(hashlib.md5(seed_str.encode("utf-8")).hexdigest(), 16)
    rng = random.Random(h)
    level = rng.choices(
        [lvl for lvl, _ in _FORTUNE_LEVELS],
        weights=[w for _, w in _FORTUNE_LEVELS],
        k=1,
    )[0]
    lucky_number = rng.randint(0, 9)
    lucky_color = rng.choice(_FORTUNE_COLORS)
    acts = rng.sample(_FORTUNE_ACTS[level], k=3)
    return {
        "day": day,
        "level": level,
        "lucky_number": lucky_number,
        "lucky_color": lucky_color,
        "suggested_activities": acts,
        "advice": _FORTUNE_WARN[level],
        "message": (
            f"【{day}】你的今日运势是「{level}」🎏 幸运数字 {lucky_number}，"
            f"幸运色 {lucky_color}。宜：{('、'.join(acts))}。{_FORTUNE_WARN[level]}"
        ),
    }


# ── 摇骰子 ──
def roll_dice(count: int = 1, sides: int = 6, seed: Optional[int] = None) -> Dict:
    count = max(1, min(int(count), 100))
    sides = max(2, min(int(sides), 1000))
    rng = random.Random(seed) if seed is not None else random
    results = [rng.randint(1, sides) for _ in range(count)]
    return {
        "results": results,
        "sum": sum(results),
        "count": count,
        "sides": sides,
        "message": f"🎲 掷出 {count} 颗 {sides} 面骰：{results}（合计 {sum(results)}）",
    }
