"""解压小游戏：切水果的规则、等级曲线与成就（从学习辅助猫娘移植）。

## 与心理测评的关系：**没有关系**

游戏的分数、等级、记录、成就是自成一套的，不影响任何测评结果，
也不会把游戏行为写进 records.json。玩就是玩，解压就是解压。

## 这里是"规则的唯一出处"

水果种类、分值、等级曲线、刷水果节奏都在这个文件里定义，通过 ``/api/game``
下发给前端，免得 JS 里再抄一份、两边慢慢跑偏（测试会校验前端拿到的是这套值）。

## 存储说明

陪学习版挂在 SQLite store 上；测评室这边用独立的 ``data/games.json``
（``threading.Lock`` + ``.tmp`` 原子写，与塔罗状态同一套纪律），坏文件自动降级为空记录。

## 电子板本地音源（可选）

Mikutap 不是开源许可（作者限定非盈利公共使用，音源是初音未来的采样），
商用分发不行。音源留在用户本机 ``data/mikutap_audio/``、插件就地读，
发行包里不含任何受版权保护的素材。
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Optional

GAME_FRUIT = "fruit"

# ── 水果：emoji 只是画在圆上的装饰，没有它也能玩 ────────────────
FRUITS: tuple[dict, ...] = (
    {"key": "watermelon", "name": "西瓜", "emoji": "🍉", "color": "#e8546b", "score": 25, "radius": 0.078, "weight": 1.0},
    {"key": "orange", "name": "橘子", "emoji": "🍊", "color": "#f0962e", "score": 15, "radius": 0.062, "weight": 1.1},
    {"key": "apple", "name": "苹果", "emoji": "🍎", "color": "#d8433c", "score": 15, "radius": 0.062, "weight": 1.1},
    {"key": "banana", "name": "香蕉", "emoji": "🍌", "color": "#e8c33c", "score": 20, "radius": 0.066, "weight": 0.9},
    {"key": "grape", "name": "葡萄", "emoji": "🍇", "color": "#8a5bd6", "score": 20, "radius": 0.060, "weight": 0.9},
    {"key": "kiwi", "name": "猕猴桃", "emoji": "🥝", "color": "#7bb03c", "score": 30, "radius": 0.050, "weight": 0.6},
    {"key": "peach", "name": "桃子", "emoji": "🍑", "color": "#f08a8a", "score": 25, "radius": 0.066, "weight": 0.8},
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
GAME_BADGES: tuple[dict, ...] = (
    {"key": "game:first_slice", "title": "第一刀", "note": "第一次切开水果。"},
    {"key": "game:score100", "title": "破百", "note": "单局拿到 100 分。"},
    {"key": "game:score500", "title": "果王", "note": "单局拿到 500 分。"},
    {"key": "game:combo8", "title": "连击达人", "note": "一次连击切中 8 个以上。"},
    {"key": "game:flawless", "title": "手稳", "note": "单局切中 20 个以上且一个都没漏。"},
    {"key": "game:endure90", "title": "持久", "note": "单局存活超过 90 秒。"},
    {"key": "game:total1000", "title": "千果", "note": "累计切中 1000 个水果。"},
    {"key": "game:level10", "title": "果园主", "note": "单局打到 10 级。"},
)
GAME_BADGE_BY_KEY = {spec["key"]: spec for spec in GAME_BADGES}


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


# ── 本地音源（可选）：让用户自己放采样进来 ────────────────────────
PAD_AUDIO_DIR = "mikutap_audio"
AUDIO_EXTS = (".mp3", ".ogg", ".wav", ".m4a", ".aac", ".flac")


def _natural_key(name: str) -> tuple:
    """让 a1 / a2 / a10 按人想的顺序排，而不是 a1, a10, a2。"""
    parts = re.split(r"(\d+)", name.lower())
    return tuple(int(part) if part.isdigit() else part for part in parts)


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


class GameStore:
    """游戏记录 + 成就（data/games.json，原子写 + 线程锁）。"""

    def __init__(self, path: Path, *, logger: Any = None) -> None:
        self.path = Path(path)
        self.logger = logger
        self._lock = threading.Lock()

    # ── 落盘 ────────────────────────────────────────────────
    def _read(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}            # 读失败当空局处理：可以没记录，不能玩不了

    def _write(self, state: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.path)

    def _update(self, mutate) -> dict:
        with self._lock:
            state = self._read()
            try:
                mutate(state)
                self._write(state)
            except Exception as exc:
                if self.logger is not None:
                    self.logger.warning("[assessment] 游戏状态写入失败：{}", exc)
            return state

    def _game_state(self, state: dict, game: str) -> dict:
        node = state.setdefault(game, {})
        if not isinstance(node, dict):
            node = {}
            state[game] = node
        return node

    # ── 前端需要的规则 ────────────────────────────────────────
    def config(self) -> dict[str, Any]:
        return {
            "game": GAME_FRUIT,
            "fruits": [dict(item) for item in FRUITS],
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
            "badges": [dict(spec) for spec in GAME_BADGES],
        }

    # ── 记录 ──────────────────────────────────────────────────
    def state(self, game: str = GAME_FRUIT) -> dict[str, Any]:
        node = self._game_state(self._read(), game)
        best = node.get("best") if isinstance(node.get("best"), dict) else {}
        runs = node.get("runs") if isinstance(node.get("runs"), list) else []
        owned = set(node.get("badges") or [])
        totals = {
            "runs": len(runs),
            "sliced": sum(int(r.get("sliced") or 0) for r in runs if isinstance(r, dict)),
            "missed": sum(int(r.get("missed") or 0) for r in runs if isinstance(r, dict)),
        }
        return {
            "best": {
                "score": int(best.get("score") or 0),
                "level": int(best.get("level") or 0),
                "max_combo": int(best.get("max_combo") or 0),
                "duration": float(best.get("duration") or 0),
                "sliced": int(best.get("sliced") or 0),
                "missed": int(best.get("missed") or 0),
            },
            "totals": totals,
            "recent": [
                {
                    "score": int(r.get("score") or 0),
                    "level": int(r.get("level") or 1),
                    "max_combo": int(r.get("max_combo") or 0),
                    "duration": float(r.get("duration") or 0),
                    "sliced": int(r.get("sliced") or 0),
                    "missed": int(r.get("missed") or 0),
                }
                for r in runs[:6]
                if isinstance(r, dict)
            ],
            "badges": [
                {"key": spec["key"], "title": spec["title"], "note": spec["note"], "owned": spec["key"] in owned}
                for spec in GAME_BADGES
            ],
            "note": "游戏成绩与测评记录是两条线：玩这个不会写进任何测评结果。",
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
            "created": time.time(),
        }
        with self._lock:
            state = self._read()
            node = self._game_state(state, game)
            runs = node.get("runs") if isinstance(node.get("runs"), list) else []
            before = {
                "runs": len(runs),
                "sliced": sum(int(r.get("sliced") or 0) for r in runs if isinstance(r, dict)),
            }
            best_before = int((node.get("best") or {}).get("score") or 0) if isinstance(node.get("best"), dict) else 0
            gained: list[str] = []
            try:
                runs.insert(0, payload)
                del runs[50:]                      # 只留最近 50 局
                node["runs"] = runs
                if payload["score"] > best_before:
                    node["best"] = {k: payload[k] for k in
                                    ("score", "level", "max_combo", "duration", "sliced", "missed")}
                owned = set(node.get("badges") or [])
                for key in judge_run(payload, before):
                    if key in GAME_BADGE_BY_KEY and key not in owned:
                        owned.add(key)
                        gained.append(key)
                node["badges"] = sorted(owned)
                self._write(state)
            except Exception as exc:
                if self.logger is not None:
                    self.logger.warning("[assessment] 游戏成绩保存失败：{}", exc)
                return {"ok": False, "error": str(exc), "state": self.state(game)}
        return {
            "ok": True,
            "run": {k: v for k, v in payload.items() if k != "created"},
            "gained": gained,
            "gained_titles": [GAME_BADGE_BY_KEY[key]["title"] for key in gained if key in GAME_BADGE_BY_KEY],
            "is_best": payload["score"] > best_before,
            "best_before": best_before,
            "state": self.state(game),
        }


def pad_audio_dir(data_dir: Any) -> Path:
    return Path(str(data_dir)) / PAD_AUDIO_DIR


def pad_audio_asset(data_dir: Any, name: str) -> Optional[tuple[bytes, str]]:
    """把用户放进 data/mikutap_audio/ 的音源发出去（防目录穿越，只认白名单后缀）。"""
    from ._panel import guess_mime

    root = pad_audio_dir(data_dir).resolve()
    target = (root / Path(str(name or "")).name).resolve()
    if root not in target.parents or not target.is_file():
        return None
    if target.suffix.lower() not in AUDIO_EXTS:
        return None
    try:
        return target.read_bytes(), guess_mime(target.name)
    except Exception:
        return None


__all__ = [
    "AUDIO_EXTS",
    "BOMB",
    "FRUITS",
    "GAME_BADGES",
    "GAME_BADGE_BY_KEY",
    "GAME_FRUIT",
    "GameStore",
    "PAD_AUDIO_DIR",
    "difficulty",
    "judge_run",
    "level_of",
    "pad_audio_asset",
    "scan_pad_audio",
]
