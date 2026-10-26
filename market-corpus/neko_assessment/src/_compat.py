"""默契测试核心逻辑：抽题、计分、回合状态机（纯函数 + JSON 存储）。

## 玩法（复刻双人秘密作答的机制，题目与实现全部自写）

一轮 10 题。每题两个人各答两问：**自己选什么** + **猜对方选什么**。
双方都交卷前，任何接口都不返回对方的答案；都交卷后同时揭晓：

- 选择一致 a/10：两人自己选的相同（心意相通）
- 你猜中她 b/10：你猜的就是她实际选的
- 她猜中你 c/10：她猜的就是你实际选的
- 综合默契分 = (a+b+c)/30 × 100，口径就这三项，没有黑箱。

## 计分口径（容易刻错的一把尺子）

直觉写法是 `(心意相通 + 你懂她 + 她懂你) / 3N × 100`，但这把尺子的**零点不是 0**：
每题 4 个选项，所以每一项瞎蒙都有 1/4 命中率，三项合计的期望是 `3N/4`
（10 题就是 7.5 次）。于是"毫无默契"也会显示成 25 分左右——分数永远在 20~30 徘徊，
**没有任何区分度**（实测两个完全随机作答的人，97% 的轮次落在 20 分以内）。

所以这里把**随机水平挪到零点**：``score=0`` 就是"和瞎猜一样"。
满分刻度取「高出随机 2.5 个标准差」而不是「三项全中」——后者概率是 (1/4)^30，
一辈子碰不到，拿它当 100 分等于上半截刻度全是废的。

## YUI 的答案从哪来（诚实第一）

- 模型在线：LLM 后台线程现场作答（source="llm"），那才是"她自己想的"；
- 模型不可用：用 ``_compat_data`` 里每题的出厂档案兜底（source="archive"），
  面板明确标注"离线档案"，绝不装作实时作答；
- 档案/模型都没有的极端情况：接口直接报错，不编答案。

## 存储

``data/compat.json``，``threading.Lock`` + ``.tmp`` 原子写（与塔罗、切水果同一套纪律）。
与测评记录（records.json）、切水果战绩（games.json）完全分开。
"""

from __future__ import annotations

import json
import math
import os
import random
import threading
import time
from pathlib import Path
from typing import Any, Optional

from ._compat_data import CATEGORIES, QUESTION_BY_ID, QUESTIONS

ROUND_SIZE = 10
MAX_ROUNDS = 30
VALID_STATUS = ("answering", "ready", "revealed", "abandoned")

# 满分刻度：高出随机水平 2.5 个标准差。（全中要 (1/4)^30，那是废刻度）
CHANCE_SIGMA_FOR_FULL = 2.5

# 分数档位文案（下限, 名称）
SCORE_BANDS: tuple[tuple[int, str], ...] = (
    (0, "和瞎猜差不多"),
    (1, "略高于瞎猜"),
    (21, "有点默契"),
    (46, "挺懂对方"),
    (71, "很默契"),
    (91, "极难出现的默契"),
)


def band_of(score: int) -> str:
    """把净默契分翻成一句人话。"""
    label = SCORE_BANDS[0][1]
    for floor, name in SCORE_BANDS:
        if int(score) >= floor:
            label = name
    return label


def mean_options(question_ids: list[str]) -> float:
    """本轮平均选项数——"瞎蒙命中率"就是它的倒数。"""
    counts = []
    for qid in question_ids:
        q = QUESTION_BY_ID.get(str(qid)) or {}
        counts.append(max(2, len(q.get("options") or [])))
    return (sum(counts) / len(counts)) if counts else 4.0


def chance_hits(question_ids: list[str]) -> float:
    """随机水平：三项指标各瞎蒙一遍，期望命中多少次。"""
    n = len(question_ids)
    return 3.0 * n / mean_options(question_ids) if n else 0.0


def score_from_counts(
    same: int,
    self_hit: int,
    yui_hit: int,
    total: int,
    options: float = 4.0,
) -> int:
    """净默契分：**0 = 随机水平**，100 = 高出随机 2.5 个标准差。

    只依赖原始计数，所以历史轮次也能用新口径重算（见 ``CompatStore.history``）。
    """
    n = max(1, int(total))
    m = max(2.0, float(options))
    hits = int(same) + int(self_hit) + int(yui_hit)
    base = 3.0 * n / m
    sd = math.sqrt(3.0 * n * (1.0 / m) * (1.0 - 1.0 / m))
    if sd <= 0:
        return 0
    z = (hits - base) / sd
    return int(max(0, min(100, round(z / CHANCE_SIGMA_FOR_FULL * 100))))


def question_public(question_id: str) -> Optional[dict]:
    """出题给前端：只带文本与选项，绝不带档案答案。"""
    q = QUESTION_BY_ID.get(str(question_id))
    if q is None:
        return None
    return {
        "id": q["id"],
        "cat": q["cat"],
        "cat_name": CATEGORIES.get(q["cat"], q["cat"]),
        "text": q["text"],
        "options": list(q["options"]),
    }


def sample_questions(count: int = ROUND_SIZE, rng: Optional[random.Random] = None) -> list[str]:
    """从 100 题里不放回抽 count 题（类别尽量铺开：每类先各拿一题再补满）。

    类别数（10）正好等于轮长（10），所以**每一轮都会覆盖全部 10 个类别**，
    不会出现某个类别长期抽不到的情况。
    """
    roller = rng or random
    want = max(1, min(len(QUESTIONS), int(count)))
    by_cat: dict[str, list[dict]] = {}
    for q in QUESTIONS:
        by_cat.setdefault(q["cat"], []).append(q)
    picked: list[dict] = []
    for cat in CATEGORIES:                       # 每个类别先来一题，覆盖面优先
        pool = list(by_cat.get(cat) or [])
        if pool and len(picked) < want:
            picked.append(roller.choice(pool))
    rest = [q for q in QUESTIONS if q not in picked]
    roller.shuffle(rest)
    picked.extend(rest[: want - len(picked)])
    roller.shuffle(picked)
    return [q["id"] for q in picked[:want]]



def validate_answers(question_ids: list[str], answers: Any) -> Optional[dict[str, list[int]]]:
    """校验用户提交：长度对齐、每题 own/guess 都是合法选项序号。不合法返回 None。"""
    if not isinstance(answers, list) or len(answers) != len(question_ids):
        return None
    own: list[int] = []
    guess: list[int] = []
    for qid, item in zip(question_ids, answers):
        q = QUESTION_BY_ID.get(str(qid))
        if q is None or not isinstance(item, dict):
            return None
        try:
            o = int(item.get("own"))
            g = int(item.get("guess"))
        except Exception:
            return None
        if not (0 <= o < len(q["options"]) and 0 <= g < len(q["options"])):
            return None
        own.append(o)
        guess.append(g)
    return {"own": own, "guess": guess}


def classify_round(
    question_ids: list[str],
    self_a: dict,
    yui_a: dict,
    answered: Any = None,
) -> dict:
    """揭晓计分：逐题对照 + 三项汇总 + 综合默契分。纯计算，方便测试。

    ``answered`` 给出**她真正答上来的题的下标**（缺省=全答）。这是必须的：
    逐题面谈时她可能有一两题没接上，那些题**不进计分**（``total`` 只算答上的
    题数、随机基线也只按答上的题算），并逐题标 ``skipped``。
    拿没答上的题当"擦肩而过"，等于把沉默算成默契差——那是冤枉她。
    """
    total_all = len(question_ids)
    if answered is None:
        hit_idx = set(range(total_all))
    else:
        hit_idx = set()
        for raw in answered:
            try:
                value = int(raw)
            except Exception:
                continue
            if 0 <= value < total_all:
                hit_idx.add(value)

    rows = []
    same = self_hit = yui_hit = 0
    used: list[str] = []
    for i, qid in enumerate(question_ids):
        q = QUESTION_BY_ID.get(str(qid)) or {}
        so = int(self_a["own"][i])
        sg = int(self_a["guess"][i])
        row = {
            "id": qid,
            "cat": q.get("cat"),
            "cat_name": CATEGORIES.get(q.get("cat"), q.get("cat")),
            "text": q.get("text", ""),
            "options": list(q.get("options") or []),
            "self_own": so,
            "self_guess": sg,
            "yui_own": None,
            "yui_guess": None,
            "tags": [],
        }
        if i not in hit_idx:
            row["skipped"] = True
            row["tags"] = ["她没答上（不计分）"]
            rows.append(row)
            continue
        yo = int(yui_a["own"][i])
        yg = int(yui_a["guess"][i])
        row["yui_own"] = yo
        row["yui_guess"] = yg
        used.append(str(qid))
        tags = []
        if so == yo:
            same += 1
            tags.append("心意相通")
        if sg == yo:
            self_hit += 1
            tags.append("你懂她")
        if yg == so:
            yui_hit += 1
            tags.append("她懂你")
        if not tags:
            tags.append("擦肩而过")
        row["tags"] = tags
        rows.append(row)

    n = len(used)
    if n <= 0:
        return {
            "same": 0, "self_hit": 0, "yui_hit": 0,
            "total": 0, "scored": 0, "skipped": total_all,
            "hits": 0, "chance_hits": 0.0,
            "score": 0, "band": "她一道都没答上",
            "rows": rows, "answered": [],
        }
    opts = mean_options(used)
    hits = same + self_hit + yui_hit
    score = score_from_counts(same, self_hit, yui_hit, n, opts)
    return {
        "same": same,
        "self_hit": self_hit,
        "yui_hit": yui_hit,
        "total": n,                      # 只数她答上的题（历史和面板都按这个口径）
        "scored": n,
        "skipped": total_all - n,
        "hits": hits,
        "chance_hits": round(chance_hits(used), 2),
        "score": score,
        "band": band_of(score),
        "rows": rows,
        "answered": sorted(hit_idx),
        "scored_ids": used,
    }


class CompatStore:
    """回合状态机 + data/compat.json（lock + 原子写）。

    状态：answering（等双方交卷）→ ready（双方齐了等揭晓）→ revealed；
    开新回合时旧的未完成回合直接标 abandoned（界面一次只玩一轮）。
    """

    def __init__(self, path: Path, *, logger: Any = None) -> None:
        self.path = Path(path)
        self.logger = logger
        self._lock = threading.Lock()

    def _read(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _write(self, state: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.path)

    def _rounds(self, state: dict) -> list:
        rounds = state.setdefault("rounds", [])
        if not isinstance(rounds, list):
            rounds = []
            state["rounds"] = rounds
        return rounds

    # ── 回合操作（都持锁读写）──────────────────────────────
    def start_round(self, round_id: str, question_ids: list[str]) -> dict:
        with self._lock:
            state = self._read()
            rounds = self._rounds(state)
            for old in rounds:                   # 一次只玩一轮：旧回合没交卷就作废
                if isinstance(old, dict) and old.get("status") in ("answering", "ready"):
                    old["status"] = "abandoned"
            entry = {
                "id": str(round_id),
                "ts": int(time.time()),
                "question_ids": [str(q) for q in question_ids],
                "status": "answering",
                "self": None,
                "yui": None,
                "yui_source": "",
                # 她逐题面谈时真正答上来的题下标。没答上的题在计分里剔除，
                # 但**留档**——面板要如实显示"这题她没接上"。
                "yui_answered": [],
                "remembered": False,      # 是否已回写进猫娘的长期记忆（见 mark_remembered）
                "result": None,
            }
            rounds.insert(0, entry)
            del rounds[MAX_ROUNDS:]
            try:
                self._write(state)
            except Exception as exc:
                if self.logger is not None:
                    self.logger.warning("[assessment] 默契回合创建失败：{}", exc)
        return dict(entry)

    def set_self(self, round_id: str, answers: dict) -> Optional[dict]:
        return self._fill(round_id, "self", answers)

    def set_yui(
        self,
        round_id: str,
        answers: dict,
        source: str,
        answered: Any = None,
    ) -> Optional[dict]:
        return self._fill(round_id, "yui", answers, source=source, answered=answered)

    def _fill(
        self,
        round_id: str,
        side: str,
        answers: dict,
        source: str = "",
        answered: Any = None,
    ) -> Optional[dict]:
        with self._lock:
            state = self._read()
            entry = self._find(state, round_id)
            if entry is None or entry.get("status") not in ("answering", "ready"):
                return None
            entry[side] = {"own": list(answers["own"]), "guess": list(answers["guess"])}
            if side == "yui":
                entry["yui_source"] = str(source or "")
                entry["yui_answered"] = (
                    list(range(len(entry["question_ids"])))
                    if answered is None else [int(i) for i in answered]
                )
            if entry.get("self") and entry.get("yui"):
                entry["status"] = "ready"
            try:
                self._write(state)
            except Exception as exc:
                if self.logger is not None:
                    self.logger.warning("[assessment] 默契回合写入失败：{}", exc)
            return dict(entry)

    def reveal(self, round_id: str) -> Optional[dict]:
        """双方齐了就计分落盘并返回完整对照；没齐返回带 status 的条目（不泄露答案）。"""
        with self._lock:
            state = self._read()
            entry = self._find(state, round_id)
            if entry is None:
                return None
            if entry.get("status") == "revealed" and isinstance(entry.get("result"), dict):
                return dict(entry)
            if entry.get("status") != "ready":
                return dict(entry)
            result = classify_round(
                entry["question_ids"],
                entry["self"],
                entry["yui"],
                entry.get("yui_answered"),
            )
            entry["result"] = result
            entry["status"] = "revealed"
            entry["revealed_ts"] = int(time.time())
            try:
                self._write(state)
            except Exception as exc:
                if self.logger is not None:
                    self.logger.warning("[assessment] 默契回合揭晓写入失败：{}", exc)
            return dict(entry)

    def get(self, round_id: str) -> Optional[dict]:
        entry = self._find(self._read(), round_id)
        return dict(entry) if entry else None

    def _find(self, state: dict, round_id: str) -> Optional[dict]:
        for entry in self._rounds(state):
            if isinstance(entry, dict) and entry.get("id") == str(round_id):
                return entry
        return None

    def mark_remembered(self, round_id: str) -> bool:
        """标记这一轮已经回写进猫娘的记忆；返回 True 表示本次是第一次。

        面板是轮询揭晓的，没有这个标记就会把同一轮反复灌进她的长期记忆。
        """
        with self._lock:
            state = self._read()
            entry = self._find(state, round_id)
            if entry is None or entry.get("remembered"):
                return False
            entry["remembered"] = True
            try:
                self._write(state)
            except Exception as exc:
                if self.logger is not None:
                    self.logger.warning("[assessment] 默契回写标记失败：{}", exc)
            return True

    def history(self, limit: int = 12) -> list[dict]:
        """已揭晓的回合摘要（首页展示用，不带逐题答案）。

        分数**按当前口径从原始计数重算**，所以调过计分规则后，旧轮次也会跟着
        显示成新刻度，不会一半新一半旧（原始数据只存 a/b/c，不存推导值）。
        """
        rows = []
        for entry in self._rounds(self._read()):
            if not isinstance(entry, dict) or entry.get("status") != "revealed":
                continue
            result = entry.get("result") or {}
            qids = [str(q) for q in (entry.get("question_ids") or [])]
            # 只按**她实际答上来的**那些题重算，与揭晓时的口径一致
            scored = [str(q) for q in (result.get("scored_ids") or qids)]
            total = int(result.get("total") or len(scored) or 1)
            same = int(result.get("same") or 0)
            self_hit = int(result.get("self_hit") or 0)
            yui_hit = int(result.get("yui_hit") or 0)
            score = score_from_counts(same, self_hit, yui_hit, total, mean_options(scored))
            rows.append({
                "id": entry.get("id"),
                "ts": entry.get("ts"),
                "score": score,
                "band": band_of(score),
                "same": same,
                "self_hit": self_hit,
                "yui_hit": yui_hit,
                "hits": same + self_hit + yui_hit,
                "chance_hits": round(chance_hits(scored), 2),
                "total": total,
                "skipped": int(result.get("skipped") or 0),
                "yui_source": entry.get("yui_source", ""),
            })
            if len(rows) >= limit:
                break
        return rows


__all__ = [
    "CATEGORIES",
    "QUESTIONS",
    "ROUND_SIZE",
    "SCORE_BANDS",
    "CompatStore",
    "band_of",
    "chance_hits",
    "classify_round",
    "mean_options",
    "question_public",
    "sample_questions",
    "score_from_counts",
    "validate_answers",
]
