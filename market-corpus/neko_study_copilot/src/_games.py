"""小游戏：切水果的规则、等级曲线与成就。

## 与修行等级的关系：**没有关系**

游戏的分数、等级、记录、成就是**自成一套**的，不会给修行经验，也不会影响掌握度。
理由和 ``_progress.py`` 里写的一样：一旦"玩游戏能涨经验"，整套激励就变成了刷分——
学习者最后记住的是怎么刷分，不是学了什么。所以这里刻意做成两条独立的线，
面板上也写明了。游戏等级只由分数决定，失败了也不扣任何东西。

## 这里是"规则的唯一出处"

水果种类、分值、等级曲线、刷水果节奏都在这个文件里定义，通过 ``/api/game`` 下发给前端，
免得 JS 里再抄一份、两边慢慢跑偏（测试会校验前端拿到的是这套值）。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ._progress import BadgeSpec

GAME_FRUIT = "fruit"

# ── 水果：emoji 只是画在圆上的装饰，没有它也能玩 ────────────────
@dataclass(frozen=True)
class FruitSpec:
    key: str
    name: str
    emoji: str
    color: str          # 圆底主色
    score: int
    radius: float       # 相对画布高度的比例
    weight: float       # 出现权重


FRUITS: tuple[FruitSpec, ...] = (
    FruitSpec("watermelon", "西瓜", "🍉", "#e8546b", 25, 0.078, 1.0),
    FruitSpec("orange", "橘子", "🍊", "#f0962e", 15, 0.062, 1.1),
    FruitSpec("apple", "苹果", "🍎", "#d8433c", 15, 0.062, 1.1),
    FruitSpec("banana", "香蕉", "🍌", "#e8c33c", 20, 0.066, 0.9),
    FruitSpec("grape", "葡萄", "🍇", "#8a5bd6", 20, 0.060, 0.9),
    FruitSpec("kiwi", "猕猴桃", "🥝", "#7bb03c", 30, 0.050, 0.6),
    FruitSpec("peach", "桃子", "🍑", "#f08a8a", 25, 0.066, 0.8),
)

BOMB = {"key": "bomb", "name": "炸弹", "emoji": "💣", "color": "#3b3b46", "score": 0, "radius": 0.058}

# ── 等级曲线：纯看分数，不设上限以外的门槛 ────────────────────
LEVEL_STEP = 120          # 每 120 分升一级
LEVEL_MAX = 20
LIVES = 3                 # 三条命：漏水果或切炸弹各扣一条

# 刷水果节奏随等级变化（秒）与下落速度（画布高度/秒）
SPAWN_INTERVAL_START = 1.15
SPAWN_INTERVAL_MIN = 0.45
FALL_SPEED_START = 0.62
FALL_SPEED_GAIN = 0.045
BOMB_CHANCE_START = 0.0    # 默认没有炸弹，纯解压
BOMB_CHANCE_MAX = 0.16


def level_of(score: int) -> int:
    """分数 → 等级（1 起，20 封顶）。"""
    return max(1, min(LEVEL_MAX, 1 + int(max(0, int(score)) // LEVEL_STEP)))


def difficulty(level: int) -> dict[str, float]:
    """等级 → 这一局的节奏参数。前端照着用，别在 JS 里重算。"""
    step = max(0, min(LEVEL_MAX, int(level)) - 1)
    return {
        "spawn_interval": max(SPAWN_INTERVAL_MIN, SPAWN_INTERVAL_START - step * 0.045),
        "fall_speed": FALL_SPEED_START + step * FALL_SPEED_GAIN,
        "bomb_chance": min(BOMB_CHANCE_MAX, BOMB_CHANCE_START + step * 0.012),
    }


# ── 游戏成就 ────────────────────────────────────────────────
GAME_BADGES: tuple[BadgeSpec, ...] = (
    BadgeSpec("game:first_slice", "第一刀", "第一次切开水果。"),
    BadgeSpec("game:score100", "破百", "单局拿到 100 分。"),
    BadgeSpec("game:score500", "果王", "单局拿到 500 分。"),
    BadgeSpec("game:combo8", "连击达人", "一次连击切中 8 个以上。"),
    BadgeSpec("game:flawless", "手稳", "单局切中 20 个以上且一个都没漏。"),
    BadgeSpec("game:endure90", "持久", "单局存活超过 90 秒。"),
    BadgeSpec("game:total1000", "千果", "累计切中 1000 个水果。"),
    BadgeSpec("game:level10", "果园主", "单局打到 10 级。"),
)
GAME_BADGE_BY_KEY: dict[str, BadgeSpec] = {spec.key: spec for spec in GAME_BADGES}


def judge_run(run: dict[str, Any], totals: dict[str, Any]) -> list[str]:
    """判定这一局拿到了哪些成就。

    ``totals`` 是**这一局之前**的累计数据（因为累计类成就看的是历史 + 本局）。
    只做纯计算，方便测试直接构造数据验证。
    """
    gained: list[str] = []
    score = int(run.get("score") or 0)
    level = int(run.get("level") or 1)
    combo = int(run.get("max_combo") or 0)
    duration = float(run.get("duration") or 0)
    sliced = int(run.get("sliced") or 0)
    missed = int(run.get("missed") or 0)
    total_sliced = int(totals.get("sliced") or 0) + sliced

    if sliced >= 1:
        gained.append("game:first_slice")
    if score >= 100:
        gained.append("game:score100")
    if score >= 500:
        gained.append("game:score500")
    if combo >= 8:
        gained.append("game:combo8")
    if sliced >= 20 and missed == 0:
        gained.append("game:flawless")
    if duration >= 90:
        gained.append("game:endure90")
    if total_sliced >= 1000:
        gained.append("game:total1000")
    if level >= 10:
        gained.append("game:level10")
    return gained


class GameService:
    """游戏记录 + 成就。刻意不碰 ``ProgressEngine``：玩游戏不给修行经验。"""

    def __init__(self, store: Any, *, logger: Any = None) -> None:
        self.store = store
        self.logger = logger

    # ── 前端需要的规则 ────────────────────────────────────────
    def config(self) -> dict[str, Any]:
        return {
            "game": GAME_FRUIT,
            "fruits": [
                {
                    "key": item.key,
                    "name": item.name,
                    "emoji": item.emoji,
                    "color": item.color,
                    "score": item.score,
                    "radius": item.radius,
                    "weight": item.weight,
                }
                for item in FRUITS
            ],
            "bomb": dict(BOMB),
            "level_step": LEVEL_STEP,
            "level_max": LEVEL_MAX,
            "lives": LIVES,
            "spawn_interval_start": SPAWN_INTERVAL_START,
            "spawn_interval_min": SPAWN_INTERVAL_MIN,
            "fall_speed_start": FALL_SPEED_START,
            "fall_speed_gain": FALL_SPEED_GAIN,
            "bomb_chance_start": BOMB_CHANCE_START,
            "bomb_chance_max": BOMB_CHANCE_MAX,
            "badges": [{"key": spec.key, "title": spec.title, "note": spec.note} for spec in GAME_BADGES],
        }

    # ── 记录 ──────────────────────────────────────────────────
    def state(self, game: str = GAME_FRUIT) -> dict[str, Any]:
        best = self.store.game_best(game)
        totals = self.store.game_totals(game)
        owned = {str(row.get("key")) for row in self.store.list_badges()}
        return {
            "best": {
                "score": int(best.get("score") or 0),
                "level": int(best.get("level") or 0),
                "max_combo": int(best.get("max_combo") or 0),
                "duration": float(best.get("duration") or 0),
                "sliced": int(best.get("sliced") or 0),
                "missed": int(best.get("missed") or 0),
                "created": float(best.get("created") or 0),
            },
            "totals": totals,
            "recent": [
                {
                    "score": int(row.get("score") or 0),
                    "level": int(row.get("level") or 1),
                    "max_combo": int(row.get("max_combo") or 0),
                    "duration": float(row.get("duration") or 0),
                    "sliced": int(row.get("sliced") or 0),
                    "missed": int(row.get("missed") or 0),
                    "created": float(row.get("created") or 0),
                }
                for row in self.store.game_recent(game, limit=6)
            ],
            "badges": [
                {"key": spec.key, "title": spec.title, "note": spec.note, "owned": spec.key in owned}
                for spec in GAME_BADGES
            ],
            "note": "游戏成绩与修行等级是两条线：玩这个不会加经验，也不影响掌握度。",
        }

    def submit(self, run: dict[str, Any], game: str = GAME_FRUIT) -> dict[str, Any]:
        """收一局成绩：落库、判成就、返回最新记录。"""
        payload = {
            "score": max(0, int(run.get("score") or 0)),
            "level": max(1, int(run.get("level") or 1)),
            "max_combo": max(0, int(run.get("max_combo") or 0)),
            "duration": max(0.0, float(run.get("duration") or 0.0)),
            "sliced": max(0, int(run.get("sliced") or 0)),
            "missed": max(0, int(run.get("missed") or 0)),
        }
        before = self.store.game_totals(game)
        best_before = int(before.get("score") or 0)
        try:
            self.store.save_game_run(game, **payload)
        except Exception as exc:
            if self.logger is not None:
                self.logger.warning("[study_copilot] 游戏成绩保存失败: %s", exc)
            return {"ok": False, "error": str(exc), "state": self.state(game)}

        gained: list[str] = []
        for key in judge_run(payload, before):
            spec = GAME_BADGE_BY_KEY.get(key)
            if spec is None:
                continue
            if self.store.award_badge(spec.key, spec.title, spec.note):
                gained.append(key)
        state = self.state(game)
        return {
            "ok": True,
            "run": payload,
            "gained": gained,
            "gained_titles": [GAME_BADGE_BY_KEY[key].title for key in gained if key in GAME_BADGE_BY_KEY],
            "is_best": payload["score"] > best_before,
            "best_before": best_before,
            "state": state,
        }

    def pad_audio(self, data_dir: Any) -> list[str]:
        """本机音源列表（用户自己放的非商业自用采样）。"""
        try:
            return scan_pad_audio(data_dir)
        except Exception:
            return []

    def report(self, game: str = GAME_FRUIT) -> str:
        """把记录说成人话（供聊天里查询）。"""
        state = self.state(game)
        best = state["best"]
        totals = state["totals"]
        lines = [
            f"- 玩过 **{totals['runs']}** 局，累计切中 **{totals['sliced']}** 个水果",
            f"- 最高分：**{best['score']}**（{level_label(best['level'])}，最长连击 {best['max_combo']}）",
            f"- 单局最长存活：{format_time(best['duration'])}",
            f"- 累计漏掉：{totals['missed']} 个",
        ]
        owned = len([item for item in state["badges"] if item["owned"]])
        lines.append(f"- 游戏成就：{owned}/{len(state['badges'])}")
        return "\n".join(lines)


# ── 本地音源（可选）：让用户自己放采样进来 ────────────────────────
#
# 为什么不是"直接把 Mikutap 的音频抄进来"：Mikutap 不是开源许可，
# 作者条款写明「仅用于非盈利的公共使用用途，商业用途请联系作者」，
# 而它的音源是初音未来的采样（Crypton 的 IP）。这个插件要发到 N.E.K.O 官方市场
# （宿主本体在 Steam 上架），打包这些文件属于商用分发，不能做。
#
# 合规又好用的做法：音源留在用户本机，插件只负责"就地读"。
# 个人非商业自用正好是作者条款允许的范围，发行包里也不含任何受版权保护的素材。
PAD_AUDIO_DIR = "mikutap_audio"
AUDIO_EXTS = (".mp3", ".ogg", ".wav", ".m4a", ".aac", ".flac")


def scan_pad_audio(data_dir: Any) -> list[str]:
    """扫本机音源目录，按文件名自然序返回文件名列表（没有就返回空）。"""
    path = Path(str(data_dir)) / PAD_AUDIO_DIR
    if not path.is_dir():
        return []
    rows = [
        item.name
        for item in path.iterdir()
        if item.is_file() and item.suffix.lower() in AUDIO_EXTS and not item.name.startswith(".")
    ]
    return sorted(rows, key=_natural_key)


def _natural_key(name: str) -> tuple:
    """让 a1 / a2 / a10 按人想的顺序排，而不是 a1, a10, a2。"""
    import re

    parts = re.split(r"(\d+)", name.lower())
    return tuple(int(part) if part.isdigit() else part for part in parts)


def level_label(level: int) -> str:
    level = int(level or 0)
    return f"Lv.{level}" if level else "还没打过"


def format_time(seconds: float) -> str:
    total = max(0, int(seconds or 0))
    return f"{total // 60} 分 {total % 60} 秒" if total >= 60 else f"{total} 秒"


def bonus_for_combo(combo: int) -> int:
    """连击奖励：3 连起算，越多越夸张但设上限，避免刷分过猛。"""
    combo = int(combo or 0)
    if combo < 3:
        return 0
    return min(60, (combo - 2) * 6)


def started_at() -> float:
    return time.time()
