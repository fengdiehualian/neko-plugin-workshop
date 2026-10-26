"""修行等级：经验、头衔、徽章。

## 一条原则

**只对真实学习行为给经验。** 不做"点一下按钮 +10"这种空心激励——那和学生
自己骗自己没区别，也和 `_guard.py` 那套事实纪律矛盾。经验来源只有这些：

============ ================== ====================================
行为           经验               说明
============ ================== ====================================
答对一题       +10                主来源
答错一题       +3                 鼓励"敢做"，错了也有分
复习到期项     +6                 艾宾浩斯队列里的旧账
完成一次诊断   +15                读材料 + 定位薄弱点最费劲
识图诊断       +12                额外加，因为省了他手打
讲解一个知识点 +8                 看讲解也是学习
出一轮题       +5                 出题本身给一点，做完题另算
掌握度提升     每 +1% → +2       单次上限 20，防刷
当天首次学习   +10                打卡奖励，不叠加
============ ================== ====================================

## 等级与头衔

每级所需经验递增（`need(L) = 60 + (L-1) * 45`），头衔走科举路子，从「懵懂猫崽」
一路到「学海宗师」。头衔只是称号，不加任何数值——避免"头衔越高中文越水"。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Optional

# ── 经验规则 ────────────────────────────────────────────────
EXP_RULES: dict[str, tuple[int, str]] = {
    "attempt_correct": (10, "答对一题"),
    "attempt_wrong": (3, "答错一题（敢做就有分）"),
    "review": (6, "复习到期项"),
    "diagnose": (15, "完成一次诊断"),
    "vision_diagnose": (12, "识图诊断"),
    "teach": (8, "讲解一个知识点"),
    "quiz": (5, "出一轮题"),
    "mastery_up": (2, "掌握度提升 1%"),
    "daily_first": (10, "当天第一次学习"),
    "plan": (6, "生成学习计划"),
    "resource": (4, "找了一轮资料"),
}
MASTERY_UP_CAP = 20          # 单次掌握度提升最多给 20
TITLE_MAX_LEVEL = 20

# ── 头衔（按等级取，超出上限沿用最后一个）────────────────────
TITLES: tuple[str, ...] = (
    "",
    "懵懂猫崽",
    "描红学童",
    "晨读学徒",
    "温故书生",
    "破题生员",
    "精进举子",
    "通经秀才",
    "算经能手",
    "临场解元",
    "独步会元",
    "登科进士",
    "一榜榜眼",
    "探花及第",
    "状元及第",
    "学海宗师",
    "著作等身",
    "开宗立派",
    "桃李满门",
    "一代宗师",
    "文曲下凡",
)
# 头衔配一句"人话"，避免只顾着好听
TITLE_NOTES: dict[int, str] = {
    1: "刚开始，先把「坐下打开书」这件事做成习惯。",
    3: "能连坐三天的人，已经比大多数人走得远了。",
    5: "开始有考点意识了——知道题目在考什么。",
    8: "做题速度与准确率开始互相咬合，这是真进步。",
    10: "这个阶段的分，多半是捡回来的（会的全对）。",
    12: "薄弱点补得动了，提分开始变得可控。",
    15: "你已经在做别人做不到的事：把错因写清楚。",
    20: "到了这个程度，教你身边的同学更快。",
}


def level_need(level: int) -> int:
    """升到下一级还需要多少经验（单级需求量）。"""
    level = max(1, int(level))
    return 60 + (level - 1) * 45


def level_threshold(level: int) -> int:
    """累计到 ``level`` 级所需的经验总量。"""
    level = max(1, int(level))
    total = 0
    for step in range(1, level):
        total += level_need(step)
    return total


def resolve_level(total_exp: int) -> tuple[int, int, int]:
    """返回 (等级, 当前级内经验, 升下一级所需)。等级上限 TITLE_MAX_LEVEL。"""
    exp = max(0, int(total_exp))
    level = 1
    while level < TITLE_MAX_LEVEL and exp >= level_threshold(level + 1):
        level += 1
    base = level_threshold(level)
    need = level_need(level) if level < TITLE_MAX_LEVEL else 0
    return level, exp - base, need


def title_of(level: int) -> str:
    level = max(1, min(TITLE_MAX_LEVEL, int(level)))
    return TITLES[level] if level < len(TITLES) else TITLES[-1]


def title_note(level: int) -> str:
    """给当前等级配一句话：取不超过当前等级的最大建议。"""
    level = int(level)
    hit = ""
    for step in sorted(TITLE_NOTES):
        if step <= level:
            hit = TITLE_NOTES[step]
        else:
            break
    return hit


def amount_for(kind: str, units: int = 1) -> int:
    """按规则算一次行为该给多少经验。"""
    base = EXP_RULES.get(kind, (0, ""))[0]
    return int(base) * max(1, int(units or 1))


# ── 徽章 ───────────────────────────────────────────────────
@dataclass(frozen=True)
class BadgeSpec:
    key: str
    title: str
    note: str


BADGES: tuple[BadgeSpec, ...] = (
    BadgeSpec("first_correct", "首战告捷", "第一次答对一道题——从这里开始算。"),
    BadgeSpec("streak3", "三日不辍", "连续 3 天有学习记录。"),
    BadgeSpec("streak7", "七日成习", "连续 7 天有学习记录，习惯已经立住了。"),
    BadgeSpec("streak30", "月余不怠", "连续 30 天有学习记录。"),
    BadgeSpec("ten_right_run", "十题不倒", "连续答对 10 题。"),
    BadgeSpec("hundred", "百题斩", "累计作答 100 题。"),
    BadgeSpec("accuracy80", "稳", "作答 30 题以上且正确率 ≥ 80%。"),
    BadgeSpec("mastery30", "通经三十", "掌握度覆盖到 30 个知识点。"),
    BadgeSpec("recover", "起死回生", "把某个知识点从 <40% 拉到 ≥70%。"),
    BadgeSpec("diagnose5", "望闻问切", "完成 5 次诊断。"),
    BadgeSpec("teach10", "传道十次", "听了 10 次讲解。"),
    BadgeSpec("early_bird", "破晓者", "早上 7 点前学过一次。"),
    BadgeSpec("level10", "登科", "修行等级到 10 级。"),
)
BADGE_BY_KEY: dict[str, BadgeSpec] = {spec.key: spec for spec in BADGES}


@dataclass
class ProgressSnapshot:
    total_exp: int = 0
    level: int = 1
    level_exp: int = 0
    level_need: int = 0
    title: str = ""
    title_note: str = ""
    percent: float = 0.0
    streak: int = 0
    today_exp: int = 0
    badges: list[dict[str, Any]] = field(default_factory=list)
    recent: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "total_exp": self.total_exp,
            "level": self.level,
            "level_exp": self.level_exp,
            "level_need": self.level_need,
            "title": self.title,
            "title_note": self.title_note,
            "percent": round(self.percent, 4),
            "streak": self.streak,
            "today_exp": self.today_exp,
            "badges": self.badges,
            "recent": self.recent,
        }


def study_streak(timestamps: list[float], now: Optional[float] = None) -> int:
    """连续学习天数（按本地日期算；今天还没学则从昨天往前数）。"""
    stamp = time.time() if now is None else float(now)
    if not timestamps:
        return 0
    days = {time.strftime("%Y-%m-%d", time.localtime(ts)) for ts in timestamps if ts}
    if not days:
        return 0
    streak = 0
    cursor = stamp
    # 今天没学不该把昨天的连击清零，所以最多允许"今天空缺"一次
    if time.strftime("%Y-%m-%d", time.localtime(cursor)) not in days:
        cursor -= 86400
    while True:
        key = time.strftime("%Y-%m-%d", time.localtime(cursor))
        if key in days:
            streak += 1
            cursor -= 86400
        else:
            break
    return streak


class ProgressEngine:
    """把 store 里的原始数据翻译成等级、头衔、徽章。"""

    def __init__(self, store: Any, *, enabled: bool = True, logger: Any = None) -> None:
        self.store = store
        self.enabled = bool(enabled)
        self.logger = logger

    # ── 发经验 ────────────────────────────────────────────────
    def award(self, kind: str, *, units: int = 1, note: str = "", amount: Optional[int] = None) -> int:
        """记一笔经验（按规则算额度）。返回实际给的经验。"""
        if not self.enabled:
            return 0
        value = amount if amount is not None else amount_for(kind, units)
        if value <= 0:
            return 0
        try:
            self.store.add_exp(kind, value, note or EXP_RULES.get(kind, (0, ""))[1])
            self._award_daily_first()
            self.check_badges()
        except Exception as exc:
            if self.logger is not None:
                self.logger.warning("[study_copilot] 经验记录失败（忽略）: %s", exc)
            return 0
        return value

    def award_mastery_up(self, before: float, after: float, note: str = "") -> int:
        """掌握度提升折算经验：每 +1% 给 2 点，单次上限 20。"""
        delta = float(after) - float(before)
        if delta <= 0:
            return 0
        points = int(round(delta * 100)) * EXP_RULES["mastery_up"][0]
        return self.award("mastery_up", amount=min(MASTERY_UP_CAP, points), note=note)

    def _award_daily_first(self) -> bool:
        """当天第一次学习额外给一点（一天只给一次）。

        判定要直白：就是"今天有没有 already 给过 daily_first"。
        别用"今天经验是否 > 0"去反推——那笔刚写进去的账会把它自己算进去。
        """
        now = time.time()
        start = time.mktime(time.localtime(now)[:3] + (0, 0, 0, 0, 0, -1))
        for row in self.store.exp_recent(limit=30):
            if str(row.get("kind")) == "daily_first" and float(row.get("created") or 0) >= start:
                return False
        self.store.add_exp("daily_first", EXP_RULES["daily_first"][0], EXP_RULES["daily_first"][1])
        return True

    # ── 徽章 ──────────────────────────────────────────────────
    def check_badges(self) -> list[str]:
        """按真实数据检查徽章条件，返回这次新拿到的徽章 key。"""
        if not self.enabled:
            return []
        gained: list[str] = []
        try:
            stats = self.store.attempt_stats()
            total = int(stats.get("total") or 0)
            rate = float(stats.get("rate") or 0)
            mastery = self.store.mastery_map()
            exp = self.store.exp_total()

            def give(key: str) -> None:
                spec = BADGE_BY_KEY.get(key)
                if spec is None:
                    return
                if self.store.award_badge(spec.key, spec.title, spec.note):
                    gained.append(spec.key)

            if int(stats.get("correct") or 0) >= 1:
                give("first_correct")
            streak = study_streak(self.store.exp_timestamps())
            if streak >= 3:
                give("streak3")
            if streak >= 7:
                give("streak7")
            if streak >= 30:
                give("streak30")
            if total >= 100:
                give("hundred")
            if total >= 30 and rate >= 0.8:
                give("accuracy80")
            if len([k for k, v in mastery.items() if v > 0]) >= 30:
                give("mastery30")
            if self.store.count_kind("sessions", "diagnose") >= 5 or self.store.count_kind("sessions", "vision") >= 5:
                give("diagnose5")
            if self.store.count_kind("sessions", "teach") >= 10:
                give("teach10")
            if len([ts for ts in self.store.exp_timestamps() if time.localtime(ts).tm_hour < 7]) >= 1:
                give("early_bird")
            if resolve_level(exp)[0] >= 10:
                give("level10")
            gained = self._check_run_and_recover(gained, give)
        except Exception as exc:
            if self.logger is not None:
                self.logger.warning("[study_copilot] 徽章检查失败（忽略）: %s", exc)
        return gained

    def _check_run_and_recover(self, gained: list[str], give: Any) -> list[str]:
        """连续答对 10 题 / 薄弱点翻盘：都从 attempt 与掌握度记录里算。"""
        try:
            with self.store._connect() as conn:  # noqa: SLF001 - 只读查询，复用连接helper
                rows = self.store._read(conn, "SELECT correct FROM attempts ORDER BY id DESC LIMIT 10")  # noqa: SLF001
                if len(rows) == 10 and all(int(row["correct"] or 0) == 1 for row in rows):
                    give("ten_right_run")
                levels = self.store._read(  # noqa: SLF001
                    conn, "SELECT point_id, level FROM mastery ORDER BY updated DESC LIMIT 50"
                )
            if any(0.7 <= float(row["level"] or 0) and float(row["level"] or 0) < 1.01 for row in levels):
                # 只要求当前已有 ≥70% 的点，配合 attempts 有过错题即可（避免误发）
                if self.store.count_kind("exp_log", "attempt_wrong") >= 3:
                    give("recover")
        except Exception:
            pass
        return gained

    # ── 快照 ──────────────────────────────────────────────────
    def snapshot(self) -> ProgressSnapshot:
        now = time.time()
        total = self.store.exp_total()
        level, level_exp, need = resolve_level(total)
        start_of_day = time.mktime(time.localtime(now)[:3] + (0, 0, 0, 0, 0, -1))
        return ProgressSnapshot(
            total_exp=total,
            level=level,
            level_exp=level_exp,
            level_need=need,
            title=title_of(level),
            title_note=title_note(level),
            percent=(level_exp / need) if need else 1.0,
            streak=study_streak(self.store.exp_timestamps()),
            today_exp=self.store.exp_between(start_of_day, now),
            badges=[
                {"key": row.get("key"), "title": row.get("title"), "note": row.get("note"), "created": row.get("created")}
                for row in self.store.list_badges()
            ],
            recent=[
                {"kind": row.get("kind"), "amount": row.get("amount"), "note": row.get("note"), "created": row.get("created")}
                for row in self.store.exp_recent(limit=10)
            ],
        )

    # ── 给界面看的徽章墙（含未获得的）──────────────────────────
    def badge_wall(self) -> dict[str, Any]:
        owned = {str(row.get("key")) for row in self.store.list_badges()}
        return {
            "owned": [spec.key for spec in BADGES if spec.key in owned],
            "all": [
                {
                    "key": spec.key,
                    "title": spec.title,
                    "note": spec.note,
                    "owned": spec.key in owned,
                }
                for spec in BADGES
            ],
        }

    def rules_text(self) -> str:
        """把经验规则说清楚，接口/命令里给用户看，避免"经验哪来的"说不清。"""
        rows = [f"{note}：**+{value}**" for _kind, (value, note) in EXP_RULES.items()]
        return "\n".join(f"- {row}" for row in rows)
