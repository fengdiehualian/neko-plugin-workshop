"""学习计划生成：长周期分阶段 + 短周期排到每天。

核心思路：

1. 先算**还剩多少天**，决定基础 / 强化 / 冲刺三段的比例；
2. 再按「提分性价比」把每天的分钟数分给各科，而不是平均分配；
3. 复习队列单独占一块时间（艾宾浩斯），避免"学了新的忘旧的"；
4. 每个阶段末尾设一个检查点，用来更新掌握度，从而动态调整下一阶段。
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any, Optional

from . import _fmt as fmt
from ._profiles import get_profile, parse_credits, region_note
from ._syllabus import all_points, roi_ranking

MIN_SUBJECT_MINUTES = 20


def _weighted_mastery(exam_type: str, subject: str, mastery: dict[str, float]) -> tuple[float, int]:
    """科目的加权掌握度（按分值权重），以及已有记录的点数。"""
    points = all_points(exam_type, [subject])
    if not points:
        return 0.5, 0
    total_weight = sum(point.weight for point in points) or 1.0
    tracked = 0
    accumulated = 0.0
    for point in points:
        level = mastery.get(point.id)
        if level is None:
            level = 0.5
        else:
            tracked += 1
        accumulated += float(level) * point.weight
    return accumulated / total_weight, tracked


@dataclass
class StudyPlan:
    exam_type: str
    exam_name: str
    subjects: list[str]
    target_score: float
    exam_date: str
    days_left: int
    horizon: str
    phases: list[dict[str, Any]] = field(default_factory=list)
    allocation: dict[str, int] = field(default_factory=dict)
    weekly: list[dict[str, Any]] = field(default_factory=list)
    daily: list[dict[str, Any]] = field(default_factory=list)
    checkpoints: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    # 有作答记录的知识点数：0 表示这份计划的时间分配只是按默认值估的，
    # 输出时要如实说明，不能宣称"按提分性价比排的"。
    tracked_points: int = 0
    date_estimated: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "exam_type": self.exam_type,
            "exam_name": self.exam_name,
            "subjects": self.subjects,
            "target_score": self.target_score,
            "exam_date": self.exam_date,
            "days_left": self.days_left,
            "horizon": self.horizon,
            "phases": self.phases,
            "allocation": self.allocation,
            "weekly": self.weekly,
            "daily": self.daily,
            "checkpoints": self.checkpoints,
            "notes": self.notes,
            "tracked_points": self.tracked_points,
            "date_estimated": self.date_estimated,
        }


def resolve_exam_date(raw: str, exam_type: str) -> tuple[str, int]:
    """返回 (考试日期 YYYY-MM-DD, 剩余天数)。用户没填时按常规考期推算。"""
    today = dt.date.today()
    raw = (raw or "").strip()
    if raw:
        try:
            target = dt.date.fromisoformat(raw)
        except ValueError:
            target = None
        if target is None:
            try:
                month, day = raw.split("-")[:2]
                target = dt.date(today.year, int(month), int(day))
                if target < today:
                    target = dt.date(today.year + 1, int(month), int(day))
            except Exception:
                target = None
        if target:
            return target.isoformat(), max(0, (target - today).days)
    profile = get_profile(exam_type)
    default = (profile.default_date or "").strip()
    if default and "-" in default:
        try:
            month, day = default.split("-")[:2]
            target = dt.date(today.year, int(month), int(day))
            if target < today:
                target = dt.date(today.year + 1, int(month), int(day))
            return target.isoformat(), max(0, (target - today).days)
        except Exception:
            pass
    fallback = today + dt.timedelta(days=120)
    return fallback.isoformat(), 120


def phase_split(days_left: int) -> list[dict[str, Any]]:
    """按剩余天数划分阶段。返回每段占的比例。"""
    if days_left <= 0:
        return [{"name": "临考", "ratio": 1.0, "focus": "保温与状态调整"}]
    if days_left <= 30:
        return [
            {"name": "冲刺", "ratio": 0.7, "focus": "真题套卷 + 高频考点回炉"},
            {"name": "临考", "ratio": 0.3, "focus": "错题重做 + 作息与心态"},
        ]
    if days_left <= 90:
        return [
            {"name": "强化", "ratio": 0.55, "focus": "专题突破 + 中等题提速"},
            {"name": "冲刺", "ratio": 0.35, "focus": "真题套卷 + 弱点定点清除"},
            {"name": "临考", "ratio": 0.10, "focus": "错题重做 + 作息与心态"},
        ]
    if days_left <= 200:
        return [
            {"name": "基础", "ratio": 0.35, "focus": "过一遍全部考点，定义与基本方法"},
            {"name": "强化", "ratio": 0.40, "focus": "专题突破 + 中档题稳定度"},
            {"name": "冲刺", "ratio": 0.20, "focus": "真题套卷 + 压轴题争分"},
            {"name": "临考", "ratio": 0.05, "focus": "错题重做 + 作息与心态"},
        ]
    return [
        {"name": "基础", "ratio": 0.45, "focus": "系统过考点，建立知识框架"},
        {"name": "强化", "ratio": 0.35, "focus": "专题突破 + 题型归纳"},
        {"name": "冲刺", "ratio": 0.15, "focus": "真题套卷 + 压轴题"},
        {"name": "临考", "ratio": 0.05, "focus": "错题重做 + 作息与心态"},
    ]


def allocate_minutes(
    subjects: list[str],
    exam_type: str,
    mastery: dict[str, float],
    daily_minutes: int,
    credits: Optional[dict[str, float]] = None,
) -> dict[str, int]:
    """按「这门课还剩多少分可拿」分配每日分钟数，并给每科兜底。

    压力值 = (1 - 加权掌握度) × 该科满分，也就是"这门课离满分还差多少分"。
    只看知识点权重会导致权重标注大的科目（比如语文作文）吃掉全部时间，
    这是错的——决定时间分配的应该是**分值**。

    大学学分制额外乘两个系数：
    · 学分系数（学分 / 3）：同样差 20 分，5 学分的课对绩点的影响大于 2 学分的课；
    · 挂科风险系数（×1.6）：得分率在及格线附近的课优先抢救，因为挂科的代价远大于少考几分。
    """
    if not subjects:
        return {}
    exam_profile = get_profile(exam_type)
    credit_map = credits or {}
    pressure: dict[str, float] = {}
    for subject in subjects:
        spec = exam_profile.subject(subject)
        full = spec.full_score if spec else 100.0
        level, _tracked = _weighted_mastery(exam_type, subject, mastery)
        value = max((1.0 - level) * full, 1.0)
        if exam_profile.mode == "credit":
            credit = float(credit_map.get(subject, 0.0))
            if credit:
                value *= credit / 3.0
            if level < 0.62:      # 换算得分率约 0.68 以下，接近及格线
                value *= 1.6
        pressure[subject] = value

    total_pressure = sum(pressure.values()) or 1.0
    raw = {
        subject: daily_minutes * pressure[subject] / total_pressure
        for subject in subjects
    }
    # 兜底：保证每科至少 MIN_SUBJECT_MINUTES，再从超出最多的科目里扣
    floor_need = 0.0
    for subject in subjects:
        if raw[subject] < MIN_SUBJECT_MINUTES:
            floor_need += MIN_SUBJECT_MINUTES - raw[subject]
            raw[subject] = float(MIN_SUBJECT_MINUTES)
    if floor_need > 0:
        donors = sorted(
            (subject for subject in subjects if raw[subject] > MIN_SUBJECT_MINUTES),
            key=lambda item: -raw[item],
        )
        for subject in donors:
            spare = raw[subject] - MIN_SUBJECT_MINUTES
            if spare <= 0:
                continue
            cut = min(spare, floor_need)
            raw[subject] -= cut
            floor_need -= cut
            if floor_need <= 0:
                break
    return {subject: int(round(raw[subject])) for subject in subjects}


def build_plan(
    profile: dict[str, Any],
    mastery: dict[str, float],
    horizon: str = "long",
    daily_minutes: Optional[int] = None,
) -> StudyPlan:
    exam_type = str(profile.get("exam_type") or "gaokao")
    exam_profile = get_profile(exam_type)
    subjects = [item for item in str(profile.get("subjects") or "").split(",") if item]
    if not subjects:
        subjects = [item.key for item in exam_profile.subjects][:3]
    minutes = int(daily_minutes or profile.get("daily_minutes") or 180)
    exam_date, days_left = resolve_exam_date(str(profile.get("exam_date") or ""), exam_type)
    target_score = float(profile.get("target_score") or 0)

    allocation = allocate_minutes(
        subjects, exam_type, mastery, minutes, parse_credits(str(profile.get("credits") or ""))
    )
    phases = phase_split(days_left)
    cursor = 0
    for index, phase in enumerate(phases):
        span = int(round(days_left * phase["ratio"])) if days_left else 0
        if index == len(phases) - 1:
            span = max(0, days_left - cursor)
        phase["start_day"] = cursor + 1
        phase["end_day"] = cursor + span
        cursor += span

    ranking = roi_ranking(exam_type, subjects, mastery, limit=min(24, max(8, len(subjects) * 6)))
    weekly = _build_weekly(ranking, phases, days_left, subjects)
    daily = _build_daily(allocation, minutes, ranking)
    checkpoints = _build_checkpoints(phases, exam_date)
    notes = _build_notes(profile, days_left, target_score, exam_profile.total_score)

    # 有作答记录的知识点数：0 就意味着时间分配没有真实依据，得如实说明
    tracked_points = sum(_weighted_mastery(exam_type, subject, mastery)[1] for subject in subjects)
    if tracked_points == 0:
        notes.insert(
            0,
            "还没有任何作答记录：下面的时间分配是按默认掌握度估的（基本等于各科平均分），"
            "只能当起步框架。做过一轮题之后重新生成，才会真正按你的薄弱点排序。",
        )
    date_estimated = not str(profile.get("exam_date") or "").strip()
    if date_estimated:
        notes.insert(
            0,
            f"考试日期没填，天数按常规考期估算为 {days_left} 天；填上确切日期后阶段划分会重算。",
        )

    return StudyPlan(
        exam_type=exam_type,
        exam_name=exam_profile.name,
        subjects=subjects,
        target_score=target_score,
        exam_date=exam_date,
        days_left=days_left,
        horizon=horizon,
        phases=phases,
        allocation=allocation,
        weekly=weekly,
        daily=daily,
        checkpoints=checkpoints,
        notes=notes,
        tracked_points=tracked_points,
        date_estimated=date_estimated,
    )


def _build_weekly(
    ranking: list[dict[str, Any]],
    phases: list[dict[str, Any]],
    days_left: int,
    subjects: list[str],
) -> list[dict[str, Any]]:
    """按周排专题：每周以一科为主线轮换，再搭一个全局最高性价比的点。

    纯按全局 ROI 排序会让语文作文、英语听力这类高分值点连霸好几周，
    导致某一科几周都碰不到，所以这里强制轮换。
    """
    if days_left <= 0:
        return []
    weeks = max(1, min(12, (days_left + 6) // 7))
    by_subject: dict[str, list[dict[str, Any]]] = {}
    for row in ranking:
        by_subject.setdefault(str(row["point"]["subject"]), []).append(row)
    order = subjects or list(by_subject.keys())
    items: list[dict[str, Any]] = []
    cursor: dict[str, int] = {subject: 0 for subject in order}
    for week in range(1, weeks + 1):
        day = week * 7
        phase = next((item for item in phases if item["start_day"] <= day <= item["end_day"]), phases[-1])
        main = order[(week - 1) % len(order)]
        pool = by_subject.get(main) or ranking
        start = cursor.get(main, 0)
        picked = pool[start : start + 2]
        cursor[main] = start + (len(picked) or 1)
        if not picked:
            picked = ranking[:2]
        # 再搭一个全局性价比最高的，保证薄弱大户不被冷落
        extra = next((row for row in ranking if row not in picked), None)
        if extra:
            picked = picked + [extra]
        names = [row["point"]["name"] for row in picked]
        items.append(
            {
                "week": week,
                "phase": phase["name"],
                "main_subject": main,
                "focus": names,
                "goal": f"本周主线 {subject_label(main)}，完成 {len(names)} 个专题："
                        "先定义与基本题，再中等易错题，最后做 1 组限时练",
            }
        )
    return items


def _build_daily(
    allocation: dict[str, int],
    minutes: int,
    ranking: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    remaining = max(30, minutes)
    review = max(15, int(minutes * 0.2))
    blocks.append(
        {
            "slot": "开场",
            "minutes": review,
            "task": "复习到期卡片与上次错题（艾宾浩斯队列），不动新内容",
        }
    )
    remaining -= review
    focus = ranking[0]["point"]["name"] if ranking else "最薄弱的知识点"
    for subject, subject_minutes in allocation.items():
        if remaining <= 10:
            break
        used = min(subject_minutes, remaining)
        blocks.append(
            {
                "slot": f"{subject} 专题",
                "minutes": used,
                "task": f"按『定义题 → 中等易错题 → 限时练』推进，重点放在当前最薄弱的点：{focus}",
            }
        )
        remaining -= used
    if remaining > 10:
        blocks.append(
            {
                "slot": "收尾",
                "minutes": remaining,
                "task": "当天复盘：把错因写成一句话，登记到错题本，安排明天复习",
            }
        )
    return blocks


def _build_checkpoints(phases: list[dict[str, Any]], exam_date: str) -> list[dict[str, Any]]:
    rows = []
    for phase in phases:
        if phase["name"] == "临考":
            continue
        rows.append(
            {
                "phase": phase["name"],
                "day": phase["end_day"],
                "action": f"做一套{phase['name']}阶段测验，更新掌握度后重排下一阶段",
            }
        )
    rows.append({"phase": "临考", "day": 0, "action": f"考试日 {exam_date} 前 3 天只做错题与保温，不再开新专题"})
    return rows


def _build_notes(profile: dict[str, Any], days_left: int, target_score: float, total_score: float) -> list[str]:
    notes: list[str] = []
    region = str(profile.get("region") or "")
    if region:
        notes.append(region_note(region))
    if target_score and total_score:
        ratio = target_score / total_score
        if ratio >= 0.85:
            notes.append(f"目标 {target_score:g}/{total_score:g}（约 {ratio:.0%}）属于高位目标，压轴题必须拿分。")
        elif ratio >= 0.7:
            notes.append(f"目标 {target_score:g}/{total_score:g}（约 {ratio:.0%}）：先保证中档题全对，再争压轴。")
        else:
            notes.append(f"目标 {target_score:g}/{total_score:g}（约 {ratio:.0%}）：优先拿稳基础分，难题学会拿步骤分。")
    if days_left <= 30:
        notes.append("剩余不足 30 天，不要再开新专题，全部时间用在真题与错题上。")
    elif days_left <= 90:
        notes.append("时间够做两轮专题，但每轮都要配限时练，否则会『看懂但做不对』。")
    notes.append("每周留半天空白缓冲，计划被打断时用它补，不要靠熬夜补。")
    return notes


SUBJECT_LABELS: dict[str, str] = {
    "chinese": "语文",
    "math": "数学",
    "english": "英语",
    "physics": "物理",
    "chemistry": "化学",
    "biology": "生物",
    "politics": "政治",
    "history": "历史",
    "geography": "地理",
    "major": "专业课",
    "general": "待定科目",
    # ── 大学学分课 ────────────────────────────────────────────
    "math_adv": "高等数学",
    "linear": "线性代数",
    "prob": "概率论与数理统计",
    "cs": "计算机与程序设计",
    "economics": "经管类课程",
    # ── 证书类 ────────────────────────────────────────────────
    "quality": "综合素质",
    "pedagogy": "教育知识与教学能力",
    "subject_teaching": "学科知识与教学能力",
    "morning": "综合知识（上午）",
    "afternoon": "案例分析 / 论文（下午）",
    "accounting": "会计",
    "auditing": "审计",
    "finance": "财务成本管理",
    "econ_law": "经济法",
    "tax": "税法",
    "strategy": "公司战略与风险管理",
    "objective": "客观题",
    "subjective": "主观题",
    "choice": "选择题部分",
    "operation": "上机操作部分",
}


def subject_label(key: str) -> str:
    return SUBJECT_LABELS.get(key, key)


def format_plan(plan: StudyPlan) -> str:
    date_note = "（估算）" if plan.date_estimated else ""
    head = [
        fmt.kv("考试", plan.exam_name),
        fmt.kv("目标", f"{plan.target_score:g} 分" if plan.target_score else "未设定"),
        fmt.kv("考试日", f"{plan.exam_date}{date_note}（还剩 {plan.days_left} 天）"),
        fmt.kv("科目", "、".join(subject_label(item) for item in plan.subjects)),
    ]
    blocks = [fmt.section("总体", fmt.bullets(head))]

    if plan.allocation:
        total = sum(plan.allocation.values()) or 1
        rows = []
        for subject, minutes in plan.allocation.items():
            share = minutes / total
            rows.append(f"{subject_label(subject)}　{fmt.bar(share)} {minutes} 分钟")
        title = "每日时间分配（按提分性价比）" if plan.tracked_points else "每日时间分配（**暂无作答记录，等权起步**）"
        blocks.append(fmt.section(title, fmt.bullets(rows)))

    if plan.phases:
        rows = [
            f"**{phase['name']}期**　第 {phase['start_day']}-{phase['end_day']} 天　{phase['focus']}"
            for phase in plan.phases
        ]
        blocks.append(fmt.section("阶段安排", fmt.bullets(rows)))

    if plan.weekly:
        rows = []
        for week in plan.weekly[:8]:
            focus = "、".join(week["focus"])
            rows.append(f"第 {week['week']} 周（{week['phase']}期）：{focus}")
        blocks.append(fmt.section("周计划", fmt.numbered(rows)))

    if plan.daily:
        rows = [f"{block['slot']}　{block['minutes']} 分钟　{block['task']}" for block in plan.daily]
        blocks.append(fmt.section("每日模板", fmt.bullets(rows)))

    if plan.checkpoints:
        rows = [f"第 {row['day']} 天（{row['phase']}期结束）：{row['action']}" for row in plan.checkpoints]
        blocks.append(fmt.section("检查点", fmt.bullets(rows)))

    if plan.notes:
        blocks.append(fmt.section("提醒", "\n".join(fmt.note(item) for item in plan.notes)))
    return fmt.join(*blocks)
