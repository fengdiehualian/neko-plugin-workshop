"""教学与出题引擎。

三段式难度（用户要求的教学节奏）：

- **基础期**（掌握度 < 0.45）：题面直接对应定义，一步到位，不设坑。
  目的是"看得懂定义就会做"，先把正反馈建立起来。
- **中期**（0.45 ~ 0.75）：中等难度，允许**设坑**——但坑必须是这个知识点的
  真实易错点（定义边界、使用条件、符号方向），不是故意绕人。
- **后期**（> 0.75）：综合创新题，跨知识点组合，按该点在大题中的真实形态出题。

硬性约束：任何阶段的题都**只能出在该知识点真实会出现的题型里**
（见 :func:`_syllabus.validate_form`）。例如「名句默写」永远不会变成一道论述题，
「交变电流」也不会变成压轴计算题。出题前必须先查题型白名单。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from . import _fmt as fmt
from ._persona import directive as persona_directive
from ._profiles import form_label
from ._syllabus import KnowledgePoint, validate_form

STAGE_BASIC = "basic"
STAGE_MEDIUM = "medium"
STAGE_HARD = "hard"

STAGE_LABELS: dict[str, str] = {
    STAGE_BASIC: "基础（直接套定义）",
    STAGE_MEDIUM: "中期（中等 + 易错点设坑）",
    STAGE_HARD: "后期（综合创新 / 压轴）",
}


@dataclass
class TeachContext:
    point: KnowledgePoint
    stage: str
    mastery: float
    style: str
    exam_name: str
    region: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "point": self.point.as_dict(),
            "stage": self.stage,
            "stage_label": STAGE_LABELS.get(self.stage, self.stage),
            "mastery": self.mastery,
            "style": self.style,
            "exam_name": self.exam_name,
            "region": self.region,
        }


def decide_stage(mastery: float, requested: str = "auto") -> str:
    if requested in (STAGE_BASIC, STAGE_MEDIUM, STAGE_HARD):
        return requested
    if mastery < 0.45:
        return STAGE_BASIC
    if mastery < 0.75:
        return STAGE_MEDIUM
    return STAGE_HARD


def stage_guidance(point: KnowledgePoint, stage: str) -> str:
    forms = "、".join(form_label(item) for item in point.forms)
    head = f"题型硬约束：本题只能出成 {forms}。除此之外任何题型都是无效题，绝不许出。"
    if stage == STAGE_BASIC:
        return (
            f"{head}\n"
            "难度：基础。题干直接对应定义或公式的最直接用法，一步到两步出结果，不设陷阱。\n"
            "目的：让学习者确认「定义说的是什么」，建立正反馈。\n"
            "解析要求：先写用到的定义/公式，再代入，最后给出结论。"
        )
    if stage == STAGE_MEDIUM:
        traps = "\n".join(f"  - {item}" for item in point.traps[:3]) or "  - （该点暂无登记易错点，请按定义边界自行设计）"
        return (
            f"{head}\n"
            "难度：中等。两步到三步，需要判断「该不该用这个结论」才能做对。\n"
            "可以设坑，但坑必须来自下面的真实易错点（定义边界、使用条件、符号方向），"
            "不能靠文字游戏或超纲知识：\n"
            f"{traps}\n"
            "解析要求：明确指出坑在哪里、满足什么条件才能用这个结论、不满足时会错成什么样。"
        )
    tail = (
        "，且该知识点常年作为压轴大题载体，建议按真实大题形态出（多问、分步给分）"
        if point.big
        else "。注意：该知识点在真题里不进大题，创新也要在允许题型内做（如多选题、开放式填空、多条件判断），不要硬凑成解答题"
    )
    return (
        f"{head}\n"
        "难度：综合创新。跨知识点组合，或给出真实考试中不常见的提问角度。\n"
        f"出题形态{tail}。\n"
        "解析要求：给出完整的思路链（怎么想到这一步）、关键步骤、以及最容易丢分的地方。"
    )


def build_teach_prompt(context: TeachContext, resources: str = "", persona: str = "full") -> tuple[str, str]:
    """返回 (system, user) 两段提示词。``persona`` 控制人设强度（见 ``_persona``）。"""
    point = context.point
    style_hint = {
        "gentle": "语气温和鼓励，多用『我们可以这样想』，出错时先肯定思路再纠正；",
        "strict": "语气严谨直接，明确指出问题，不留模糊表述；",
        "concise": "极度精简，只给关键信息，不展开废话；",
    }.get(context.style, "")
    system = (
        f"你是{context.exam_name}的辅导老师。{style_hint}\n"
        f"{persona_directive(persona)}\n"
        "教学必须遵守以下结构，不要跳步：\n"
        "1. 这个知识点到底是什么（定义 + 它解决什么问题）\n"
        "2. 使用条件与常见误区（什么时候能用、什么时候不能用）\n"
        "3. 一道配套例题（按当前阶段难度）+ 完整解析\n"
        "4. 一道同类变式，让学生自己先想\n"
        "5. 一句话小结，便于记忆\n"
        "如果学生明显缺少前置知识，先补前置，再讲当前点。"
    )
    traps = "\n".join(f"- {item}" for item in point.traps) or "- （暂无登记）"
    user = (
        f"知识点：{point.name}（{point.stage}{_subject_cn(point.subject)}）\n"
        f"当前阶段：{STAGE_LABELS.get(context.stage, context.stage)}，学习者掌握度约 {context.mastery:.0%}\n"
        f"考生地区：{context.region or '未填写'}\n"
        f"命题规律：{point.name} 在真题里只以 {'、'.join(form_label(item) for item in point.forms)} 的形式出现\n"
        f"典型易错点：\n{traps}\n"
        f"出题指引：\n{stage_guidance(point, context.stage)}\n"
    )
    if point.prerequisites:
        user += f"前置知识：{'、'.join(point.prerequisites)}\n"
    if resources:
        user += f"\n可参考的公开资料（按需引用，不必全用）：\n{resources}\n"
    user += "\n请按系统提示的五段结构讲解，并给出配套例题。"
    return system, user


def build_quiz_prompt(
    point: KnowledgePoint,
    stage: str,
    count: int,
    exam_name: str,
    region: str,
    with_traps: bool = True,
    resources: str = "",
    persona: str = "full",
) -> tuple[str, str]:
    system = (
        f"你是{exam_name}的命题老师。你要出的题必须**完全符合真实考试的命题规律**。\n"
        f"{persona_directive(persona)}\n"
        "绝对禁止：\n"
        "  · 出该知识点在真题里不会出现的题型（例如把只考选择的考点出成解答题）；\n"
        "  · 超纲、引入本阶段不该出现的知识点；\n"
        "  · 题干有歧义或答案不唯一却没说明；\n"
        "  · 编造不存在的公式、定理、数据。\n"
        "每题输出格式（严格遵守，用 JSON 数组返回）：\n"
        '[{"form":"题型标识","question":"题干","options":["A...","B..."],'
        '"answer":"答案","solution":"分步解析","trap":"易错点提示（可为空字符串）"}]\n'
        "选择题必须给 options，其它题型 options 填空数组。"
    )
    user = (
        f"知识点：{point.name}\n"
        f"数量：{count} 道\n"
        f"阶段：{STAGE_LABELS.get(stage, stage)}\n"
        f"考生地区：{region or '未填写'}\n"
        f"{stage_guidance(point, stage)}\n"
    )
    if with_traps:
        user += "每题都要在 trap 字段写清楚：做错的人通常是哪一步想岔了。\n"
    if resources:
        user += f"\n可参考的公开资料：\n{resources}\n"
    user += "\n请直接返回 JSON 数组，不要任何额外文字。"
    return system, user


def build_grade_prompt(
    point: KnowledgePoint,
    question: str,
    answer: str,
    exam_name: str,
    persona: str = "full",
) -> tuple[str, str]:
    system = (
        f"你是{exam_name}的阅卷老师。按真实考试的给分标准批改，做到：\n"
        f"{persona_directive(persona)}\n"
        "1. 判定对错，给出得分率（0-1 之间）；\n"
        "2. 指出具体错在第几步，而不是只说『错了』；\n"
        "3. 区分『不会』和『会但做错』——后者只扣过程分，并指出触发错误的条件；\n"
        "4. 给出一条针对性的改进建议。\n"
        "返回 JSON：{\"correct\":true/false,\"score_rate\":0.0-1.0,"
        "\"error_step\":\"\",\"reason\":\"\",\"advice\":\"\"}"
    )
    user = (
        f"知识点：{point.name}\n"
        f"题目：{question}\n"
        f"学生作答：{answer}\n"
        f"该点典型易错点：{'；'.join(point.traps[:3]) or '暂无登记'}\n"
        "请返回 JSON。"
    )
    return system, user


def validate_question_plan(point: KnowledgePoint, stage: str, form: str = "") -> tuple[bool, str]:
    """出题前的最后一道闸：题型是否合法。"""
    if form:
        ok, message = validate_form(point, form)
        if not ok:
            return False, message
        return True, ""
    if stage == STAGE_HARD and not point.big:
        return True, (
            f"提示：{point.name} 不是压轴大题载体，后期'创新题'请限制在 "
            f"{'、'.join(form_label(item) for item in point.forms)} 之内，不要硬凑解答题。"
        )
    return True, ""


def _subject_cn(subject: str) -> str:
    return {
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
        "general": "",
    }.get(subject, subject)


def format_questions(questions: list[dict[str, Any]]) -> str:
    """把 JSON 题目渲染成给学习者的文本（题干 + 选项 + 答案 + 解析）。"""
    if not questions:
        return "这次没能生成题目，换个知识点或稍后重试。"
    lines: list[str] = []
    for index, item in enumerate(questions, start=1):
        question = str(item.get("question") or "").strip()
        form = str(item.get("form") or "")
        tag = form_label(form) if form else "练习"
        lines.append(f"**{index}. [{tag}]** {question}")
        for option in item.get("options") or []:
            lines.append(f"　　{option}")
        answer = str(item.get("answer") or "").strip()
        if answer:
            lines.append(f"　　{fmt.bold('参考答案')}：{answer}")
        solution = str(item.get("solution") or "").strip()
        if solution:
            lines.append(f"　　{fmt.bold('解析')}：{solution}")
        trap = str(item.get("trap") or "").strip()
        if trap:
            lines.append(f"　　{fmt.bold('易错点')}：{trap}")
        lines.append("")
    return "\n".join(lines).strip()


def quiz_intro(point: KnowledgePoint, stage: str, count: int) -> str:
    forms = "、".join(form_label(item) for item in point.forms)
    return fmt.join(
        f"围绕 **{point.name}** 出 {count} 道题，当前阶段：{STAGE_LABELS.get(stage, stage)}。",
        fmt.note(f"{point.name} 在真题里只以 {forms} 出现，所以下面的题也都是这个形态。"),
    )


def next_stage_after(correct: bool, current: str, mastery: float) -> str:
    """答完一轮后决定下一轮难度。"""
    _ = mastery
    if current == STAGE_BASIC:
        return STAGE_MEDIUM if correct else STAGE_BASIC
    if current == STAGE_MEDIUM:
        return STAGE_HARD if correct else STAGE_MEDIUM
    return STAGE_HARD if correct else STAGE_MEDIUM
