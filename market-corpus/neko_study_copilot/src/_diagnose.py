"""诊断与分数预估。

预估这件事必须**诚实**：给出保守 / 最可能 / 理想三档，并写清楚每档的前提条件。
最坏预期不是"吓唬人"，而是让人知道：如果什么都不改，最可能落在哪个位置。
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any, Optional

from . import _fmt as fmt
from ._planner import subject_label
from ._profiles import (
    ExamProfile,
    fail_risk,
    get_profile,
    gpa_benchmark_note,
    gpa_from_score,
    parse_credits,
    region_benchmark,
    region_note,
    weighted_gpa,
)
from ._store import StudyStore
from ._syllabus import KnowledgePoint, all_points, match_points, roi_ranking

# 有效学习一小时带来的掌握度增益（乘上「还剩多少没掌握」，因此自带边际递减）
LEARN_RATE_PER_HOUR = 0.008
# 每天的可用时长超过这个值后按边际收益衰减（熬夜与疲劳会让效率掉下来）
EFFICIENCY_KNEE_HOURS = 5.0
VOLATILITY_RATIO = 0.045      # 临场波动占总分比例

# 三档预期各自"把计划执行到几成"
EXECUTION_RATIO = {"conservative": 0.35, "likely": 0.65, "ideal": 1.0}


@dataclass
class Forecast:
    exam_type: str
    exam_name: str
    total_score: float
    current: float          # 现在就考大概能拿多少
    conservative: float     # 最坏预期
    likely: float           # 最可能
    ideal: float            # 较好预期
    volatility: float       # 波动区间（±）
    days_left: int
    pass_line: Optional[float]
    pass_probability: Optional[float]
    basis: list[str] = field(default_factory=list)
    per_subject: list[dict[str, Any]] = field(default_factory=list)
    # ── 大学学分制专用 ────────────────────────────────────────
    gpa: Optional[float] = None          # 学分加权绩点
    gpa_scale: float = 0.0               # 绩点满分（4.0 / 5.0）
    gpa_note: str = ""
    fail_risks: list[dict[str, Any]] = field(default_factory=list)   # 挂科风险科目

    def as_dict(self) -> dict[str, Any]:
        return {
            "exam_type": self.exam_type,
            "exam_name": self.exam_name,
            "total_score": self.total_score,
            "current": round(self.current, 1),
            "conservative": round(self.conservative, 1),
            "likely": round(self.likely, 1),
            "ideal": round(self.ideal, 1),
            "volatility": round(self.volatility, 1),
            "days_left": self.days_left,
            "pass_line": self.pass_line,
            "pass_probability": self.pass_probability,
            "basis": self.basis,
            "per_subject": self.per_subject,
            "gpa": self.gpa,
            "gpa_scale": self.gpa_scale,
            "gpa_note": self.gpa_note,
            "fail_risks": self.fail_risks,
        }


def _phi(z: float) -> float:
    """标准正态分布 CDF。"""
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def score_rate(mastery: float) -> float:
    """把掌握度换算成得分率。

    掌握度 0.5 时约 0.52 分率——留了"会但做不对"的折损；
    掌握度很高时逼近但不等于 1，因为总有失误和陌生题。
    """
    mastery = min(1.0, max(0.0, mastery))
    return min(0.95, max(0.05, 0.15 + 0.8 * (mastery ** 0.9)))


def subject_mastery_average(
    exam_type: str,
    subject: str,
    mastery: dict[str, float],
) -> tuple[float, int]:
    """按分值权重求科目的加权掌握度；没有记录时按 0.5 起步。"""
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


def effective_hours(days_left: int, daily_minutes: int, ratio: float = 1.0) -> float:
    """剩余时间能折算成多少"有效学习小时"。

    每天超过 EFFICIENCY_KNEE_HOURS 的部分按 45% 折算——熬夜不是线性产出，
    这样才不会得出"每天学 12 小时就能涨 300 分"的荒唐结论。
    """
    if days_left <= 0:
        return 0.0
    per_day = daily_minutes / 60.0
    if per_day <= EFFICIENCY_KNEE_HOURS:
        usable = per_day
    else:
        usable = EFFICIENCY_KNEE_HOURS + (per_day - EFFICIENCY_KNEE_HOURS) * 0.45
    return days_left * usable * ratio


def project_mastery(
    exam_type: str,
    subjects: list[str],
    mastery: dict[str, float],
    days_left: int,
    daily_minutes: int,
    ratio: float = 1.0,
) -> dict[str, float]:
    """预测按 ratio 的执行度学下来，各知识点的掌握度会变成多少。

    时间按分值权重分配（分多的点分到更多时间），增益与"还有多少没掌握"成正比，
    所以掌握度越高涨得越慢，天然有边际递减，且不设硬性上限。
    """
    points = all_points(exam_type, subjects)
    if not points:
        return dict(mastery)
    hours = effective_hours(days_left, daily_minutes, ratio)
    if hours <= 0:
        return dict(mastery)
    total_weight = sum(point.weight for point in points) or 1.0
    projected: dict[str, float] = {}
    for point in points:
        level = min(1.0, max(0.0, float(mastery.get(point.id, 0.5))))
        share = point.weight / total_weight
        gain = LEARN_RATE_PER_HOUR * hours * share * (1.0 - level)
        projected[point.id] = min(0.95, level + gain)
    return projected


def total_score_for(
    exam_profile: ExamProfile,
    subjects: list[str],
    exam_type: str,
    mastery: dict[str, float],
) -> tuple[float, float]:
    """返回 (实得总分, 计入科目的满分合计)。"""
    total = 0.0
    full_total = 0.0
    for subject in subjects:
        spec = exam_profile.subject(subject)
        full = spec.full_score if spec else 0.0
        level, _tracked = subject_mastery_average(exam_type, subject, mastery)
        total += full * score_rate(level)
        full_total += full
    return total, full_total


def build_forecast(
    profile: dict[str, Any],
    mastery: dict[str, float],
    days_left: int,
    pass_line: Optional[float] = None,
) -> Forecast:
    exam_type = str(profile.get("exam_type") or "gaokao")
    exam_profile = get_profile(exam_type)
    subjects = [item for item in str(profile.get("subjects") or "").split(",") if item]
    if not subjects:
        subjects = [item.key for item in exam_profile.subjects if not item.optional][:3]
        if not subjects:
            subjects = [item.key for item in exam_profile.subjects][:1]
    daily_minutes = int(profile.get("daily_minutes") or 180)

    credits = parse_credits(str(profile.get("credits") or ""))
    per_subject: list[dict[str, Any]] = []
    for subject in subjects:
        spec = exam_profile.subject(subject)
        full = spec.full_score if spec else 0.0
        level, tracked = subject_mastery_average(exam_type, subject, mastery)
        rate = score_rate(level)
        expected = full * rate
        credit = float(credits.get(subject, 0.0))
        per_subject.append(
            {
                "subject": subject,
                "name": subject_label(subject),
                "full_score": full,
                "mastery": round(level, 3),
                "rate": round(rate, 3),
                "expected": round(expected, 1),
                "tracked_points": tracked,
                "credit": credit,
                "risk": fail_risk(rate) if exam_profile.mode == "credit" else "",
                "gpa": round(gpa_from_score(expected / full * 100.0, exam_profile.gpa_scale or 4.0), 2)
                if exam_profile.mode == "credit" and full else None,
                "confidence": "较高" if tracked >= 3 else ("中等" if tracked else "低（尚无作答记录，按默认 50% 估计）"),
            }
        )

    current_total, full_total = total_score_for(exam_profile, subjects, exam_type, mastery)
    if full_total <= 0:
        full_total = exam_profile.total_score

    # 通过线要按"实际计入的科目满分"缩放：只选了 4 科时，750 制的本科线不能直接套
    scale = full_total / exam_profile.total_score if exam_profile.total_score else 1.0
    if pass_line is None:
        pass_line = _default_pass_line(exam_type, str(profile.get("region") or ""), exam_profile)
    if pass_line:
        pass_line = round(pass_line * scale, 1)

    conservative_map = project_mastery(
        exam_type, subjects, mastery, days_left, daily_minutes, EXECUTION_RATIO["conservative"]
    )
    likely_map = project_mastery(
        exam_type, subjects, mastery, days_left, daily_minutes, EXECUTION_RATIO["likely"]
    )
    ideal_map = project_mastery(
        exam_type, subjects, mastery, days_left, daily_minutes, EXECUTION_RATIO["ideal"]
    )
    conservative_raw, _ = total_score_for(exam_profile, subjects, exam_type, conservative_map)
    likely_raw, _ = total_score_for(exam_profile, subjects, exam_type, likely_map)
    ideal_raw, _ = total_score_for(exam_profile, subjects, exam_type, ideal_map)

    volatility = full_total * VOLATILITY_RATIO
    # 保守档要额外扣掉一次"发挥失常"的波动；理想档允许有一次超常发挥
    conservative = max(0.0, min(full_total, conservative_raw - volatility))
    likely = min(full_total, likely_raw)
    ideal = min(full_total, ideal_raw + volatility * 0.4)

    probability = None
    if pass_line:
        sigma = max(volatility / 1.5, 1.0)
        probability = round(_phi((likely - pass_line) / sigma), 3)

    growth = max(0.0, likely - current_total)
    basis = _build_basis(profile, per_subject, days_left, growth, volatility, exam_profile, full_total)

    # ── 学分制：换算绩点并列出挂科风险 ──────────────────────────
    gpa: Optional[float] = None
    gpa_note = ""
    fail_risks: list[dict[str, Any]] = []
    if exam_profile.mode == "credit":
        scale = exam_profile.gpa_scale or 4.0
        rows = [
            {"score": row["expected"] / row["full_score"] * 100.0 if row["full_score"] else 0.0,
             "credit": row["credit"] or 1.0}
            for row in per_subject
        ]
        gpa, _total_credit = weighted_gpa(rows, scale)
        gpa = round(gpa, 2)
        gpa_note = gpa_benchmark_note(gpa)
        if not credits:
            gpa_note += "（未填写学分，按各科等权计算；在面板里填了学分结果会准很多）"
        fail_risks = [
            {"subject": row["subject"], "name": row["name"], "expected": row["expected"],
             "rate": row["rate"], "risk": row["risk"], "credit": row["credit"]}
            for row in sorted(per_subject, key=lambda item: item["rate"])
            if row["rate"] < 0.7
        ]
        basis.append(
            "大学口径：及格线 60 分，但绩点按分数段换算，"
            f"当前学分加权绩点约 {gpa:.2f}/{scale:g}。"
        )
        if fail_risks:
            basis.append(
                "挂科风险科目（按得分率升序）："
                + "、".join(f"{row['name']}（{row['risk']}）" for row in fail_risks[:3])
            )

    return Forecast(
        exam_type=exam_type,
        exam_name=exam_profile.name,
        total_score=full_total,
        current=current_total,
        conservative=conservative,
        likely=likely,
        ideal=ideal,
        volatility=volatility,
        days_left=days_left,
        pass_line=pass_line,
        pass_probability=probability,
        basis=basis,
        per_subject=per_subject,
        gpa=gpa,
        gpa_scale=exam_profile.gpa_scale if exam_profile.mode == "credit" else 0.0,
        gpa_note=gpa_note,
        fail_risks=fail_risks,
    )


def _default_pass_line(exam_type: str, region: str, exam_profile: ExamProfile) -> Optional[float]:
    # 考试画像里自带合格线的，优先用它（大学 60、教资 70、软考 45×2 ……）
    if exam_profile.pass_line:
        return float(exam_profile.pass_line)
    if exam_type.startswith("cet"):
        return 425.0
    if exam_type == "kaoyan":
        return 300.0
    if exam_type == "gaokao":
        marks = region_benchmark(region)
        for key in ("本科", "特招", "一本"):
            if key in marks:
                return float(marks[key])
        return None
    return None


def _build_basis(
    profile: dict[str, Any],
    per_subject: list[dict[str, Any]],
    days_left: int,
    growth: float,
    volatility: float,
    exam_profile: ExamProfile,
    full_total: float,
) -> list[str]:
    basis: list[str] = []
    tracked = sum(row["tracked_points"] for row in per_subject)
    if tracked == 0:
        basis.append("目前没有任何作答记录，掌握度按默认的 50% 估计，误差最大；做几道题就能明显收窄区间。")
    else:
        basis.append(f"依据 {tracked} 个知识点的掌握度记录，按分值权重加权计算。")
    if abs(full_total - exam_profile.total_score) > 1:
        basis.append(
            f"只计入了你选择的科目（合计满分 {full_total:g}），不是 {exam_profile.name}的满分 "
            f"{exam_profile.total_score:g}，通过线已按同比例折算。"
        )
    weakest = sorted(per_subject, key=lambda row: row["mastery"])[:2]
    for row in weakest:
        basis.append(
            f"{row['name']}掌握度约 {row['mastery']:.0%}，折算得分率 {row['rate']:.0%}（满分 {row['full_score']:g}）"
        )
    region = str(profile.get("region") or "")
    if region:
        basis.append(region_note(region))
    if days_left > 0:
        basis.append(
            f"按每天 {int(profile.get('daily_minutes') or 180)} 分钟、还剩 {days_left} 天，"
            f"假设执行到六成半，预计可提升约 {growth:.0f} 分；每天超过 5 小时的部分已按 45% 折算效率。"
        )
    else:
        basis.append("已到考期，不再计入提升空间，只考虑临场波动。")
    basis.append(f"临场波动按总分的 {VOLATILITY_RATIO:.0%} 计（约 ±{volatility:.0f} 分）。")
    basis.append("三档含义：保守 = 计划只执行三成半且发挥偏差；最可能 = 执行六成半；较好 = 全程执行到位且发挥偏佳。")
    basis.append("所有分数均为估计值，最终以实际考试为准；区间会随作答记录增加不断收窄。")
    return basis


def format_forecast(forecast: Forecast) -> str:
    """三档分数预期。面板会把这些记号渲染成卡片 + 进度条。"""
    head = fmt.kv("考试", forecast.exam_name) + "\n"
    head += fmt.kv("满分", f"{forecast.total_score:g} 分") + "\n"
    head += fmt.kv("剩余", f"{forecast.days_left} 天")

    scores = [
        fmt.kv("现在就考", f"约 {forecast.current:.0f} 分"),
        fmt.kv("最坏预期", f"约 {forecast.conservative:.0f} 分（发挥失常 + 题目变形）"),
        fmt.kv("最可能", f"**约 {forecast.likely:.0f} 分**"),
        fmt.kv("较好预期", f"约 {forecast.ideal:.0f} 分（按计划执行到位）"),
        fmt.kv("波动区间", f"±{forecast.volatility:.0f} 分"),
    ]
    if forecast.pass_line:
        probability = forecast.pass_probability
        percent = f"{probability:.0%}" if probability is not None else "未知"
        scores.append(fmt.kv("通过线", f"{forecast.pass_line:g} 分，通过概率约 {percent}"))

    blocks = [fmt.section("总览", head), fmt.section("分数预期", fmt.bullets(scores))]
    # 没有任何作答记录时，上面的分数全是按默认 50% 推的——必须说清楚，别让它看起来像结论
    tracked_total = sum(int(row.get("tracked_points") or 0) for row in forecast.per_subject)
    if tracked_total == 0:
        blocks.insert(
            0,
            fmt.note(
                "⚠ 目前**没有任何作答记录**，下面的分数全部按默认掌握度 50% 估算，只能看量级、"
                "不能当结论。做几道题（或发一张卷子给我）之后重算，区间会立刻收窄。"
            ),
        )

    rows = []
    for row in forecast.per_subject:
        row_credit = f"，{row['credit']:g} 学分" if row.get("credit") else ""
        row_gpa = f"，绩点 {row['gpa']:.2f}" if row.get("gpa") is not None else ""
        rows.append(
            f"{row['name']}　{fmt.bar(row['rate'])} {fmt.pct(row['rate'])}"
            f"　约 {row['expected']:.0f}/{row['full_score']:g} 分{row_credit}{row_gpa}"
            f"　（{row['confidence']}）"
        )
    if rows:
        blocks.append(fmt.section("分科目", "\n".join(f"- {row}" for row in rows)))

    if forecast.gpa is not None:
        gpa_lines = [fmt.kv("学分加权绩点", f"{forecast.gpa:.2f} / {forecast.gpa_scale:g}")]
        if forecast.gpa_note:
            gpa_lines.append(fmt.note(forecast.gpa_note))
        blocks.append(fmt.section("绩点", "\n".join(gpa_lines)))

    if forecast.fail_risks:
        risk_rows = [
            f"{row['name']}：预计 {row['expected']:.0f} 分，得分率 {fmt.pct(row['rate'])} → {row['risk']}"
            for row in forecast.fail_risks[:4]
        ]
        blocks.append(fmt.section("挂科 / 及格风险（先保这些）", fmt.bullets(risk_rows)))

    if forecast.basis:
        blocks.append(fmt.section("依据", fmt.bullets(forecast.basis)))
    return fmt.join(*blocks)


# ── 题目 / 试卷文本扫描 ────────────────────────────────────────
_SCORE_PATTERNS = (
    re.compile(r"([\u4e00-\u9fff]{2,10})\s*[:：]?\s*(\d{1,3}(?:\.\d)?)\s*/\s*(\d{1,3})"),
    re.compile(r"([\u4e00-\u9fff]{2,10})\s*(\d{1,3}(?:\.\d)?)\s*分"),
)


@dataclass
class DiagnosisResult:
    subject: str
    matched: list[KnowledgePoint]
    weak: list[dict[str, Any]]
    scores: list[dict[str, Any]]
    summary: str
    # 有掌握度记录的知识点数：0 表示"薄弱项"是按权重排的，不是按他的真实水平
    tracked_points: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "subject": self.subject,
            "matched": [point.as_dict() for point in self.matched],
            "weak": self.weak,
            "scores": self.scores,
            "summary": self.summary,
            "tracked_points": self.tracked_points,
        }


def scan_scores(text: str) -> list[dict[str, Any]]:
    """从用户贴的成绩里抠出「科目 得分 / 满分」。"""
    rows: list[dict[str, Any]] = []
    for pattern in _SCORE_PATTERNS:
        for match in pattern.finditer(text or ""):
            name = match.group(1).strip()
            try:
                got = float(match.group(2))
                full = float(match.group(3)) if match.lastindex and match.lastindex >= 3 else 0.0
            except (TypeError, ValueError, IndexError):
                continue
            rows.append({"name": name, "score": got, "full": full})
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        if row["name"] in seen:
            continue
        seen.add(row["name"])
        deduped.append(row)
    return deduped


def scan_points(text: str, subject: str, exam_type: str, limit: int = 6) -> list[KnowledgePoint]:
    """在题目/试卷文本里找可能涉及的知识点。"""
    if not text:
        return []
    found: list[KnowledgePoint] = []
    seen: set[str] = set()
    # 先用整段匹配，再退化到按关键词逐段匹配
    for point in all_points(exam_type, [subject] if subject else None):
        if point.name in text and point.id not in seen:
            found.append(point)
            seen.add(point.id)
    if len(found) < limit:
        for chunk in re.split(r"[\n，。,.;；]+", text):
            chunk = chunk.strip()
            if len(chunk) < 2:
                continue
            for point in match_points(chunk, subject, exam_type, limit=1):
                if point.id not in seen:
                    found.append(point)
                    seen.add(point.id)
    return found[:limit]


def diagnose(
    store: StudyStore,
    profile: dict[str, Any],
    text: str = "",
    subject: str = "",
    exam_type: str = "",
) -> DiagnosisResult:
    """综合本地数据做一次诊断（大模型会在此基础上补充分析）。"""
    exam_type = exam_type or str(profile.get("exam_type") or "gaokao")
    mastery = store.mastery_map()
    matched = scan_points(text, subject, exam_type) if text else []
    scores = scan_scores(text)
    ranking = roi_ranking(exam_type, [subject] if subject else None, mastery, limit=8)
    weak = [
        {
            "name": row["point"]["name"],
            "subject": row["point"]["subject"],
            "mastery": row["mastery"],
            "roi": row["roi"],
            "forms": row["point"]["forms_label"],
            "traps": row["point"]["traps"][:2],
            "reason": row["reason"],
        }
        for row in ranking
    ]
    if matched:
        summary = (
            f"从你给的材料里识别到 {len(matched)} 个可能的知识点："
            + "、".join(point.name for point in matched[:5])
        )
    elif mastery:
        summary = "没能从材料里直接识别知识点，以下是按历史掌握度排出的薄弱项。"
    else:
        summary = "没能从材料里直接识别知识点。"
    if not text:
        summary += "（你还没给材料，这次只用了本地记录。）"
    if not mastery:
        summary += "**目前没有任何作答记录**：下面的薄弱项是按考点权重与考频排的，不代表你的真实水平。"
    if scores:
        summary += "；同时识别到成绩信息：" + "、".join(
            f"{row['name']} {row['score']:g}" + (f"/{row['full']:g}" if row["full"] else "") for row in scores[:6]
        )
    return DiagnosisResult(
        subject=subject, matched=matched, weak=weak, scores=scores, summary=summary, tracked_points=len(mastery)
    )


def format_diagnosis(result: DiagnosisResult) -> str:
    blocks: list[str] = []
    if result.summary:
        blocks.append(result.summary)
    if result.matched:
        rows = []
        for point in result.matched[:6]:
            forms = "、".join(point.forms)
            line = f"**{point.name}**　难度 {point.difficulty:g}/5　命题形式：{forms}"
            if point.traps:
                line += f"\n  ↳ 易错点：{point.traps[0]}"
            rows.append(line)
        blocks.append(fmt.section("可能涉及", fmt.bullets(rows)))
    if result.weak:
        rows = [f"**{row['name']}**：{row['reason']}　性价比 {row['roi']}" for row in result.weak[:6]]
        title = "优先补的薄弱点" if result.tracked_points else "优先补的方向（**暂无作答记录，按权重排序**）"
        blocks.append(fmt.section(title, fmt.bullets(rows)))
    return fmt.join(*blocks)
