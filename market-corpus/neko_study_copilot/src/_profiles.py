"""考试画像：科目、题型、分值、时间分配、地区差异。

这是「效益最大化」的数学基础——先知道每张卷子的分是怎么分布的，
才能算出「补哪一块，涨分最快」。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

# 题型枚举：出题与诊断都用这套标识
FORM_CHOICE = "choice"          # 选择题
FORM_FILL = "fill"              # 填空 / 默写
FORM_CALC = "calc"              # 计算题
FORM_SOLVE = "solve"            # 解答题 / 大题
FORM_PROOF = "prove"            # 证明题
FORM_EXPERIMENT = "experiment"  # 实验题
FORM_READING = "reading"        # 阅读理解
FORM_WRITING = "writing"        # 写作 / 作文
FORM_TRANSLATION = "translation"  # 翻译
FORM_LISTENING = "listening"    # 听力
FORM_SHORT = "short"            # 简答
FORM_ESSAY = "essay"            # 论述题（大学文科、考研分析题）
FORM_CASE = "case"              # 案例分析（法考、软考下午题、经管类）
FORM_CODING = "coding"          # 编程 / 上机操作

FORM_LABELS: dict[str, str] = {
    FORM_CHOICE: "选择题",
    FORM_FILL: "填空题",
    FORM_CALC: "计算题",
    FORM_SOLVE: "解答题",
    FORM_PROOF: "证明题",
    FORM_EXPERIMENT: "实验题",
    FORM_READING: "阅读理解",
    FORM_WRITING: "写作",
    FORM_TRANSLATION: "翻译",
    FORM_LISTENING: "听力",
    FORM_SHORT: "简答题",
    FORM_ESSAY: "论述题",
    FORM_CASE: "案例分析题",
    FORM_CODING: "编程 / 上机操作题",
}

FORM_LABELS_EN: dict[str, str] = {
    FORM_CHOICE: "Multiple choice",
    FORM_FILL: "Fill in the blank",
    FORM_CALC: "Calculation",
    FORM_SOLVE: "Problem solving",
    FORM_PROOF: "Proof",
    FORM_EXPERIMENT: "Experiment",
    FORM_READING: "Reading",
    FORM_WRITING: "Writing",
    FORM_TRANSLATION: "Translation",
    FORM_LISTENING: "Listening",
    FORM_SHORT: "Short answer",
    FORM_ESSAY: "Essay",
    FORM_CASE: "Case analysis",
    FORM_CODING: "Programming / lab task",
}


def form_label(form: str) -> str:
    return FORM_LABELS.get(form, form)


@dataclass
class SectionSpec:
    """卷子里的一个板块。"""

    name: str
    form: str
    count: int = 0
    score_each: float = 0.0
    total: float = 0.0
    minutes: int = 0
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "form": self.form,
            "form_label": form_label(self.form),
            "count": self.count,
            "score_each": self.score_each,
            "total": self.total,
            "minutes": self.minutes,
            "note": self.note,
        }


@dataclass
class SubjectSpec:
    key: str
    name: str
    full_score: float
    minutes: int
    sections: list[SectionSpec] = field(default_factory=list)
    optional: bool = False
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "name": self.name,
            "full_score": self.full_score,
            "minutes": self.minutes,
            "optional": self.optional,
            "note": self.note,
            "sections": [section.as_dict() for section in self.sections],
        }


@dataclass
class ExamProfile:
    key: str
    name: str
    total_score: float
    default_date: str          # 常规考期，用于用户没填日期时估算
    subjects: list[SubjectSpec] = field(default_factory=list)
    note: str = ""
    # 考试自身的合格线（会按实际计入科目满分等比缩放）。None 表示没有统一合格线
    pass_line: Optional[float] = None
    # 计分方式：score 分数制 / credit 学分制（大学） / band 等级或分数带（雅思托福）
    mode: str = "score"
    # 大学学分制用的绩点满分（4.0 或 5.0）；0 表示不适用
    gpa_scale: float = 0.0

    def subject(self, key: str) -> Optional[SubjectSpec]:
        for item in self.subjects:
            if item.key == key:
                return item
        return None

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "name": self.name,
            "total_score": self.total_score,
            "default_date": self.default_date,
            "note": self.note,
            "pass_line": self.pass_line,
            "mode": self.mode,
            "gpa_scale": self.gpa_scale,
            "subjects": [item.as_dict() for item in self.subjects],
        }


def _gaokao_chinese() -> SubjectSpec:
    return SubjectSpec(
        key="chinese",
        name="语文",
        full_score=150,
        minutes=150,
        sections=[
            SectionSpec("现代文阅读Ⅰ（信息类）", FORM_READING, 5, 3, 15, 20, "多选+单选混合，重信息比对"),
            SectionSpec("现代文阅读Ⅱ（文学类）", FORM_READING, 4, 4, 16, 20, "小说/散文，重手法与主旨"),
            SectionSpec("文言文阅读", FORM_READING, 5, 3, 15, 20, "断句、文化常识、翻译"),
            SectionSpec("古代诗歌阅读", FORM_READING, 2, 4.5, 9, 12, "手法与情感"),
            SectionSpec("名篇名句默写", FORM_FILL, 1, 6, 6, 5, "纯记忆，性价比最高的一块"),
            SectionSpec("语言文字运用", FORM_CHOICE, 5, 3, 15, 18, "成语、病句、衔接、修辞"),
            SectionSpec("写作", FORM_WRITING, 1, 60, 60, 55, "议论文为主，审题决定成败"),
        ],
    )


def _gaokao_math() -> SubjectSpec:
    return SubjectSpec(
        key="math",
        name="数学",
        full_score=150,
        minutes=120,
        sections=[
            SectionSpec("单项选择题", FORM_CHOICE, 8, 5, 40, 30, "前 6 题基础，7-8 题中档"),
            SectionSpec("多项选择题", FORM_CHOICE, 4, 5, 20, 20, "漏选得部分分，错选零分"),
            SectionSpec("填空题", FORM_FILL, 4, 5, 20, 15, "答案唯一，过程不评分"),
            SectionSpec("解答题", FORM_SOLVE, 6, 12, 70, 55, "数列/三角、立体几何、概率统计、解析几何、导数"),
        ],
        note="解答题按步骤给分，写不出完整结论也要拿过程分。",
    )


def _gaokao_english() -> SubjectSpec:
    return SubjectSpec(
        key="english",
        name="英语",
        full_score=150,
        minutes=120,
        sections=[
            SectionSpec("听力", FORM_LISTENING, 20, 1.5, 30, 20),
            SectionSpec("阅读理解", FORM_READING, 15, 2.5, 37.5, 35, "四选一，含主旨/推断/词义"),
            SectionSpec("七选五", FORM_READING, 5, 2.5, 12.5, 10),
            SectionSpec("完形填空", FORM_FILL, 15, 1, 15, 15),
            SectionSpec("语法填空", FORM_FILL, 10, 1.5, 15, 10),
            SectionSpec("应用文写作", FORM_WRITING, 1, 15, 15, 15),
            SectionSpec("读后续写 / 概要写作", FORM_WRITING, 1, 25, 25, 25, "新高考读后续写，浙江等地为概要写作"),
        ],
    )


def _gaokao_science(name: str, key: str, score: float, minutes: int) -> SubjectSpec:
    if key == "physics":
        sections = [
            SectionSpec("单项选择题", FORM_CHOICE, 7, 4, 28, 18),
            SectionSpec("多项选择题", FORM_CHOICE, 3, 6, 18, 12),
            SectionSpec("实验题", FORM_EXPERIMENT, 2, 16, 16, 18),
            SectionSpec("计算题", FORM_CALC, 3, 12, 38, 40, "力学、电磁学、能量综合"),
        ]
    elif key == "chemistry":
        sections = [
            SectionSpec("选择题", FORM_CHOICE, 14, 3, 42, 25),
            SectionSpec("工艺流程 / 实验综合", FORM_EXPERIMENT, 2, 14, 28, 25),
            SectionSpec("原理综合题", FORM_CALC, 2, 15, 30, 25, "平衡常数、电化学、热化学"),
        ]
    else:
        sections = [
            SectionSpec("选择题", FORM_CHOICE, 15, 2, 30, 20),
            SectionSpec("非选择题", FORM_SOLVE, 5, 14, 70, 45, "含遗传概率、实验设计、调节与生态"),
        ]
    return SubjectSpec(key=key, name=name, full_score=score, minutes=minutes, sections=sections, optional=True)


def _cet(score_per_unit: float, is_cet6: bool) -> ExamProfile:
    name = "大学英语六级" if is_cet6 else "大学英语四级"
    writing = 106.5
    listening = 248.5
    reading = 248.5
    translation = 106.5
    return ExamProfile(
        key="cet6" if is_cet6 else "cet4",
        name=name,
        total_score=710,
        default_date="12-15" if is_cet6 else "06-15",
        subjects=[
            SubjectSpec(
                key="english",
                name="英语",
                full_score=710,
                minutes=125,
                sections=[
                    SectionSpec("写作", FORM_WRITING, 1, writing, writing, 30, "短文写作，建议背模板但别生搬"),
                    SectionSpec("听力理解", FORM_LISTENING, 25, listening / 25, listening, 25,
                                "新闻/讲座 + 长对话 + 篇章；六级听力语速更快"),
                    SectionSpec("阅读理解", FORM_READING, 30, reading / 30, reading, 40,
                                "选词填空 + 长篇匹配 + 仔细阅读"),
                    SectionSpec("翻译", FORM_TRANSLATION, 1, translation, translation, 30,
                                "汉译英段落，四六级分差最大的一块"),
                ],
            )
        ],
        note=f"总分 710，425 分算通过（报考六级需四级 425+）。单位分 {score_per_unit:.1f}。",
    )


def build_gaokao() -> ExamProfile:
    return ExamProfile(
        key="gaokao",
        name="普通高等学校招生全国统一考试（高考）",
        total_score=750,
        default_date="06-07",
        subjects=[
            _gaokao_chinese(),
            _gaokao_math(),
            _gaokao_english(),
            _gaokao_science("物理", "physics", 100, 75),
            _gaokao_science("化学", "chemistry", 100, 75),
            _gaokao_science("生物", "biology", 100, 75),
            _gaokao_science("政治", "politics", 100, 75),
            _gaokao_science("历史", "history", 100, 75),
            _gaokao_science("地理", "geography", 100, 75),
        ],
        note="新高考：语数外各 150 + 首选（物理/历史）100 原始分 + 再选两科各 100 等级赋分。",
    )


def build_zhongkao() -> ExamProfile:
    return ExamProfile(
        key="zhongkao",
        name="初中学业水平考试（中考）",
        total_score=750,
        default_date="06-15",
        subjects=[
            SubjectSpec(
                key="chinese", name="语文", full_score=120, minutes=150,
                sections=[
                    SectionSpec("积累与运用", FORM_CHOICE, 6, 3, 18, 15, "字音字形、病句、名著"),
                    SectionSpec("古诗文默写", FORM_FILL, 1, 10, 10, 8, "纯送分，务必拿满"),
                    SectionSpec("文言文阅读", FORM_READING, 5, 3, 15, 18),
                    SectionSpec("现代文阅读", FORM_READING, 6, 4, 24, 30),
                    SectionSpec("写作", FORM_WRITING, 1, 50, 50, 50),
                ],
            ),
            SubjectSpec(
                key="math", name="数学", full_score=120, minutes=120,
                sections=[
                    SectionSpec("选择题", FORM_CHOICE, 10, 3, 30, 20),
                    SectionSpec("填空题", FORM_FILL, 6, 3, 18, 15),
                    SectionSpec("解答题", FORM_SOLVE, 8, 9, 72, 60, "含压轴的二次函数与几何综合"),
                ],
            ),
            SubjectSpec(
                key="english", name="英语", full_score=120, minutes=100,
                sections=[
                    SectionSpec("听力", FORM_LISTENING, 20, 1, 20, 20),
                    SectionSpec("单项选择 / 语法", FORM_CHOICE, 15, 1, 15, 12),
                    SectionSpec("完形填空", FORM_FILL, 10, 1.5, 15, 15),
                    SectionSpec("阅读理解", FORM_READING, 20, 2, 40, 35),
                    SectionSpec("写作", FORM_WRITING, 1, 20, 20, 25),
                ],
            ),
            SubjectSpec(
                key="physics", name="物理", full_score=70, minutes=60, optional=True,
                sections=[
                    SectionSpec("选择题", FORM_CHOICE, 8, 2, 16, 12),
                    SectionSpec("实验与探究", FORM_EXPERIMENT, 3, 8, 24, 20),
                    SectionSpec("计算题", FORM_CALC, 3, 10, 30, 25),
                ],
            ),
            SubjectSpec(
                key="chemistry", name="化学", full_score=50, minutes=50, optional=True,
                sections=[
                    SectionSpec("选择题", FORM_CHOICE, 12, 1.5, 18, 12),
                    SectionSpec("填空与简答", FORM_FILL, 5, 4, 20, 20),
                    SectionSpec("计算题", FORM_CALC, 1, 6, 6, 8),
                ],
            ),
        ],
        note="各地分值差异极大（总分 600-830 不等），请在面板按本地政策校正。",
    )


def build_kaoyan() -> ExamProfile:
    return ExamProfile(
        key="kaoyan",
        name="全国硕士研究生招生考试（考研初试）",
        total_score=500,
        default_date="12-21",
        subjects=[
            SubjectSpec(
                key="politics", name="思想政治理论", full_score=100, minutes=180,
                sections=[
                    SectionSpec("单项选择题", FORM_CHOICE, 16, 1, 16, 20),
                    SectionSpec("多项选择题", FORM_CHOICE, 17, 2, 34, 30, "多选少选均不得分"),
                    SectionSpec("分析题", FORM_SHORT, 5, 10, 50, 90),
                ],
            ),
            SubjectSpec(
                key="english", name="英语（一/二）", full_score=100, minutes=180,
                sections=[
                    SectionSpec("完形填空", FORM_FILL, 20, 0.5, 10, 15),
                    SectionSpec("阅读理解 A", FORM_READING, 20, 2, 40, 60),
                    SectionSpec("新题型", FORM_READING, 5, 2, 10, 15),
                    SectionSpec("翻译", FORM_TRANSLATION, 1, 10, 10, 20, "英一为长难句，英二为段落"),
                    SectionSpec("写作", FORM_WRITING, 2, 15, 30, 50, "小作文 10 + 大作文 20（英二 15+15）"),
                ],
            ),
            SubjectSpec(
                key="math", name="数学（一/二/三）", full_score=150, minutes=180,
                sections=[
                    SectionSpec("选择题", FORM_CHOICE, 10, 5, 50, 35),
                    SectionSpec("填空题", FORM_FILL, 6, 5, 30, 25),
                    SectionSpec("解答题", FORM_SOLVE, 6, 11.7, 70, 90),
                ],
            ),
            SubjectSpec(
                key="major", name="专业课", full_score=150, minutes=180,
                sections=[SectionSpec("按招生单位自命题", FORM_SOLVE, 1, 150, 150, 180)],
                optional=True,
            ),
        ],
        note="政治与英语为统考，数学按专业分一二三，专业课由招生单位命题。",
    )


def build_final() -> ExamProfile:
    return ExamProfile(
        key="final",
        name="校内考试 / 期末测评（通用）",
        total_score=100,
        default_date="",
        subjects=[
            SubjectSpec(
                key="general", name="待指定科目", full_score=100, minutes=90,
                sections=[
                    SectionSpec("选择题", FORM_CHOICE, 10, 3, 30, 20),
                    SectionSpec("填空题", FORM_FILL, 5, 4, 20, 15),
                    SectionSpec("解答题", FORM_SOLVE, 5, 10, 50, 55),
                ],
            )
        ],
        note="通用模板，请在面板里改成你学校实际的题型与分值。",
    )


def _uni_subject(key: str, name: str, sections: list[SectionSpec], minutes: int = 120,
                 optional: bool = True) -> SubjectSpec:
    return SubjectSpec(key=key, name=name, full_score=100, minutes=minutes,
                       sections=sections, optional=optional)


def build_university() -> ExamProfile:
    """大学期末考试（学分制 / 绩点制）。

    大学的「效益最大化」和高中完全不同：及格只占 60 分，剩下的 40 分才决定绩点，
    而绩点直接关系到保研、奖学金、出国申请。所以默认策略是：
    先按学分 × 挂科风险排序保住及格，再在高学分科目上冲绩点。
    """
    return ExamProfile(
        key="university",
        name="大学期末 / 学分课（GPA 制）",
        total_score=100,
        default_date="",
        pass_line=60.0,
        mode="credit",
        gpa_scale=4.0,
        subjects=[
            _uni_subject("math_adv", "高等数学", [
                SectionSpec("选择题", FORM_CHOICE, 10, 3, 30, 25, "概念判断为主"),
                SectionSpec("填空题", FORM_FILL, 5, 4, 20, 20),
                SectionSpec("计算题", FORM_CALC, 4, 9, 36, 45, "极限、积分、微分方程是三大主力"),
                SectionSpec("证明题", FORM_PROOF, 1, 14, 14, 30, "中值定理与不等式证明"),
            ], minutes=120, optional=False),
            _uni_subject("linear", "线性代数", [
                SectionSpec("选择题", FORM_CHOICE, 8, 3, 24, 20),
                SectionSpec("填空题", FORM_FILL, 5, 4, 20, 18),
                SectionSpec("计算与证明", FORM_CALC, 4, 14, 56, 55, "行列式、方程组、特征值、二次型"),
            ]),
            _uni_subject("prob", "概率论与数理统计", [
                SectionSpec("选择题", FORM_CHOICE, 8, 3, 24, 20),
                SectionSpec("填空题", FORM_FILL, 5, 4, 20, 18),
                SectionSpec("计算与证明", FORM_CALC, 4, 14, 56, 55, "分布、数字特征、参数估计、假设检验"),
            ]),
            _uni_subject("english", "大学英语", [
                SectionSpec("听力", FORM_LISTENING, 25, 1, 25, 25),
                SectionSpec("阅读", FORM_READING, 20, 1.5, 30, 35),
                SectionSpec("翻译", FORM_TRANSLATION, 1, 15, 15, 20),
                SectionSpec("写作", FORM_WRITING, 1, 15, 15, 25),
                SectionSpec("词汇与语法", FORM_CHOICE, 15, 1, 15, 15),
            ], optional=False),
            _uni_subject("politics", "思政课（马原 / 毛概 / 史纲 / 思修）", [
                SectionSpec("单项选择题", FORM_CHOICE, 20, 1, 20, 20),
                SectionSpec("多项选择题", FORM_CHOICE, 10, 2, 20, 20, "多选少选均不得分"),
                SectionSpec("简答题", FORM_SHORT, 3, 10, 30, 30),
                SectionSpec("论述题", FORM_ESSAY, 1, 30, 30, 50, "必须结合材料，只背原理只能拿半分"),
            ]),
            _uni_subject("cs", "计算机与程序设计", [
                SectionSpec("选择题", FORM_CHOICE, 15, 2, 30, 25),
                SectionSpec("填空题 / 程序阅读", FORM_FILL, 5, 4, 20, 25, "读程序写结果、填语句"),
                SectionSpec("编程题", FORM_CODING, 3, 16.7, 50, 70, "边界条件与复杂度是主要扣分点"),
            ]),
            _uni_subject("physics", "大学物理", [
                SectionSpec("选择题", FORM_CHOICE, 10, 3, 30, 25),
                SectionSpec("填空题", FORM_FILL, 5, 4, 20, 20),
                SectionSpec("计算题", FORM_CALC, 4, 12.5, 50, 55),
            ]),
            _uni_subject("economics", "经管类（微观 / 宏观 / 管理学）", [
                SectionSpec("选择题", FORM_CHOICE, 15, 2, 30, 25),
                SectionSpec("计算题", FORM_CALC, 3, 8, 24, 25, "弹性、均衡、成本与国民收入核算"),
                SectionSpec("简答题", FORM_SHORT, 3, 8, 24, 25),
                SectionSpec("案例分析", FORM_CASE, 1, 22, 22, 45, "必须套用模型再结合案例"),
            ]),
            _uni_subject("major", "专业课（按培养方案自命题）", [
                SectionSpec("按任课教师命题", FORM_SOLVE, 1, 100, 100, 120),
            ]),
        ],
        note="大学是学分制：及格线 60，但绩点按分数段换算（如 90+ → 4.0）。"
             "策略分两层——先按『学分 × 挂科风险』保住及格，再向高学分科目要绩点。",
    )


def build_teacher_cert() -> ExamProfile:
    return ExamProfile(
        key="cert_teacher",
        name="中小学教师资格考试（笔试）",
        total_score=150,
        default_date="03-09",
        pass_line=70.0,
        mode="score",
        subjects=[
            SubjectSpec(
                key="quality", name="综合素质", full_score=150, minutes=120,
                sections=[
                    SectionSpec("单项选择题", FORM_CHOICE, 29, 2, 58, 30, "职业理念、教育法律法规、文化素养"),
                    SectionSpec("材料分析题", FORM_CASE, 3, 14, 42, 45, "必考三观：教育观、学生观、教师观"),
                    SectionSpec("写作", FORM_WRITING, 1, 50, 50, 55, "多为议论文，跑题直接不及格"),
                ],
            ),
            SubjectSpec(
                key="pedagogy", name="教育知识与教学能力", full_score=150, minutes=120,
                sections=[
                    SectionSpec("单项选择题", FORM_CHOICE, 21, 2, 42, 25),
                    SectionSpec("辨析题", FORM_SHORT, 4, 8, 32, 25, "先判断对错再说明理由，判断错基本零分"),
                    SectionSpec("简答题", FORM_SHORT, 4, 10, 40, 35),
                    SectionSpec("材料分析题", FORM_CASE, 2, 18, 36, 35),
                ],
            ),
            SubjectSpec(
                key="subject_teaching", name="学科知识与教学能力", full_score=150, minutes=120,
                sections=[
                    SectionSpec("单项选择题", FORM_CHOICE, 15, 3, 45, 30, "学科本体知识"),
                    SectionSpec("案例分析题", FORM_CASE, 2, 20, 40, 35, "评析教学片段"),
                    SectionSpec("教学设计题", FORM_SOLVE, 1, 65, 65, 55, "写完整教案，模板化拿分最快"),
                ],
                optional=True,
            ),
        ],
        note="卷面 150 分，折合为报告分后 70 分合格（卷面约需 90 分上下，按当次折算）。"
             "笔试合格后还有面试（结构化 + 试讲）。",
    )


def build_soft_exam() -> ExamProfile:
    return ExamProfile(
        key="cert_soft",
        name="计算机技术与软件专业技术资格考试（软考）",
        total_score=150,
        default_date="05-25",
        pass_line=90.0,
        mode="score",
        subjects=[
            SubjectSpec(
                key="morning", name="综合知识（上午）", full_score=75, minutes=150,
                sections=[
                    SectionSpec("单项选择题", FORM_CHOICE, 75, 1, 75, 150, "覆盖面极广，含英语题 5 分"),
                ],
            ),
            SubjectSpec(
                key="afternoon", name="案例分析 / 论文（下午）", full_score=75, minutes=150,
                sections=[
                    SectionSpec("案例分析题", FORM_CASE, 3, 25, 75, 150,
                                "高级资格为论文写作，需写 2000-3000 字项目经历"),
                ],
            ),
        ],
        note="上午 75 道选择 + 下午案例（或论文），两科必须同一次考试都达到 45 分才算通过，"
             "单科成绩不保留。合格线通常稳定在 45 分。",
    )


def _cpa_subject(key: str, name: str) -> SubjectSpec:
    return SubjectSpec(
        key=key, name=name, full_score=100, minutes=150, optional=(key != "accounting"),
        sections=[
            SectionSpec("单项选择题", FORM_CHOICE, 12, 2, 24, 25),
            SectionSpec("多项选择题", FORM_CHOICE, 10, 2, 20, 25, "少选得 0.5，多选错选零分"),
            SectionSpec("计算分析题", FORM_CALC, 2, 10, 20, 35),
            SectionSpec("综合题", FORM_SOLVE, 1, 36, 36, 55),
        ],
    )


def build_cpa() -> ExamProfile:
    return ExamProfile(
        key="cert_cpa",
        name="注册会计师全国统一考试（专业阶段）",
        total_score=100,
        default_date="08-25",
        pass_line=60.0,
        mode="score",
        subjects=[
            _cpa_subject("accounting", "会计"),
            _cpa_subject("auditing", "审计"),
            _cpa_subject("finance", "财务成本管理"),
            _cpa_subject("econ_law", "经济法"),
            _cpa_subject("tax", "税法"),
            _cpa_subject("strategy", "公司战略与风险管理"),
        ],
        note="专业阶段单科 100 分、60 分及格，需在连续五个年度内通过六科，之后还有综合阶段。"
             "建议按『会计 → 税法/财管 → 审计/经济法/战略』的关联顺序报考。",
    )


def build_law_exam() -> ExamProfile:
    return ExamProfile(
        key="cert_law",
        name="国家统一法律职业资格考试（法考）",
        total_score=480,
        default_date="09-15",
        pass_line=288.0,
        mode="score",
        subjects=[
            SubjectSpec(
                key="objective", name="客观题", full_score=300, minutes=180,
                sections=[
                    SectionSpec("单项选择题", FORM_CHOICE, 100, 1, 100, 60),
                    SectionSpec("多项选择题", FORM_CHOICE, 60, 2, 120, 70),
                    SectionSpec("不定项选择题", FORM_CHOICE, 40, 2, 80, 50),
                ],
            ),
            SubjectSpec(
                key="subjective", name="主观题", full_score=180, minutes=240,
                sections=[
                    SectionSpec("案例分析题", FORM_CASE, 4, 30, 120, 150),
                    SectionSpec("论述题", FORM_ESSAY, 1, 60, 60, 90, "习近平法治思想为必考论述"),
                ],
                optional=True,
            ),
        ],
        note="客观题 300 分、180 分合格，通过后才有资格考主观题（180 分、108 分合格）。"
             "客观题合格成绩保留两年。",
    )


def build_ielts() -> ExamProfile:
    return ExamProfile(
        key="ielts",
        name="雅思 IELTS（学术类为主）",
        total_score=9,
        default_date="",
        pass_line=None,
        mode="band",
        subjects=[
            SubjectSpec(
                key="english", name="雅思总分（四项均分）", full_score=9, minutes=170,
                sections=[
                    SectionSpec("听力", FORM_LISTENING, 40, 1, 40, 30, "填空为主，拼写错误直接丢分"),
                    SectionSpec("阅读", FORM_READING, 40, 1, 40, 60, "判断题 TRUE/FALSE/NOT GIVEN 是分水岭"),
                    SectionSpec("写作", FORM_WRITING, 2, 1, 2, 60, "Task1 图表描述 + Task2 议论文"),
                    SectionSpec("口语", FORM_SHORT, 3, 1, 3, 15, "Part1/2/3，流利度与连贯性权重最高"),
                ],
            )
        ],
        note="总分 9 分为四项**均分**（不是相加），出现 0.5 分段。常见门槛：6.5（单项不低于 6.0）、7.0。"
             "口语与写作是中国考生的普遍短板，也是提分空间最大的两块。",
    )


def build_toefl() -> ExamProfile:
    return ExamProfile(
        key="toefl",
        name="托福 TOEFL iBT",
        total_score=120,
        default_date="",
        pass_line=None,
        mode="band",
        subjects=[
            SubjectSpec(
                key="english", name="托福总分（四项合计）", full_score=120, minutes=180,
                sections=[
                    SectionSpec("阅读", FORM_READING, 1, 30, 30, 35),
                    SectionSpec("听力", FORM_LISTENING, 1, 30, 30, 35),
                    SectionSpec("口语", FORM_SHORT, 4, 7.5, 30, 20),
                    SectionSpec("写作", FORM_WRITING, 2, 15, 30, 30, "综合写作 + 学术讨论写作"),
                ],
            )
        ],
        note="四科各 30 分，合计 120。常见门槛：80（多数院校最低线）、100（名校常用线）。"
             "听力贯穿口语与综合写作，是四项的地基。",
    )


def build_ncse() -> ExamProfile:
    return ExamProfile(
        key="ncse",
        name="全国计算机等级考试（NCRE，二 / 三级）",
        total_score=100,
        default_date="03-30",
        pass_line=60.0,
        mode="score",
        subjects=[
            SubjectSpec(
                key="choice", name="选择题（公共基础 + 科目知识）", full_score=40, minutes=30,
                sections=[SectionSpec("单项选择题", FORM_CHOICE, 40, 1, 40, 30)],
            ),
            SubjectSpec(
                key="operation", name="操作题（上机）", full_score=60, minutes=90,
                sections=[SectionSpec("上机操作", FORM_CODING, 3, 20, 60, 90,
                                      "二级 Office 为文档/表格/演示操作，二级 Python/C 为编程")],
            ),
        ],
        note="60 分及格（90 分为优秀），一次通过即可拿证；成绩不保留单科。"
             "操作题是主要失分区，必须上机真练，只看书基本过不了。",
    )


PROFILES: dict[str, ExamProfile] = {
    "gaokao": build_gaokao(),
    "zhongkao": build_zhongkao(),
    "cet4": _cet(710 / 100, False),
    "cet6": _cet(710 / 100, True),
    "kaoyan": build_kaoyan(),
    "final": build_final(),
    "university": build_university(),
    "cert_teacher": build_teacher_cert(),
    "cert_soft": build_soft_exam(),
    "cert_cpa": build_cpa(),
    "cert_law": build_law_exam(),
    "ielts": build_ielts(),
    "toefl": build_toefl(),
    "ncse": build_ncse(),
}

# 每个考试在未指定科目时的默认科目组
DEFAULT_SUBJECTS: dict[str, list[str]] = {
    "gaokao": ["chinese", "math", "english", "physics", "chemistry", "biology"],
    "zhongkao": ["chinese", "math", "english", "physics", "chemistry"],
    "cet4": ["english"],
    "cet6": ["english"],
    "kaoyan": ["politics", "english", "math"],
    "final": ["general"],
    "university": ["math_adv", "english", "politics", "cs"],
    "cert_teacher": ["quality", "pedagogy"],
    "cert_soft": ["morning", "afternoon"],
    "cert_cpa": ["accounting", "tax", "finance"],
    "cert_law": ["objective"],
    "ielts": ["english"],
    "toefl": ["english"],
    "ncse": ["choice", "operation"],
}

EXAM_ALIASES: dict[str, str] = {
    "高考": "gaokao",
    "新高考": "gaokao",
    "全国卷": "gaokao",
    "中考": "zhongkao",
    "初中学业水平考试": "zhongkao",
    "四级": "cet4",
    "英语四级": "cet4",
    "cet4": "cet4",
    "六级": "cet6",
    "英语六级": "cet6",
    "cet6": "cet6",
    "四六级": "cet4",
    "考研": "kaoyan",
    "研究生考试": "kaoyan",
    "期末": "final",
    "校内考试": "final",
    # ── 大学生 ────────────────────────────────────────────────
    "大学": "university",
    "大学期末": "university",
    "期末考试": "final",
    "大学期末考试": "university",
    "学分课": "university",
    "绩点": "university",
    "gpa": "university",
    "保研": "university",
    "挂科": "university",
    "补考": "university",
    "重修": "university",
    "教资": "cert_teacher",
    "教师资格证": "cert_teacher",
    "教师资格": "cert_teacher",
    "软考": "cert_soft",
    "计算机技术与软件": "cert_soft",
    "软件设计师": "cert_soft",
    "注会": "cert_cpa",
    "cpa": "cert_cpa",
    "注册会计师": "cert_cpa",
    "法考": "cert_law",
    "司考": "cert_law",
    "法律职业资格": "cert_law",
    "雅思": "ielts",
    "ielts": "ielts",
    "托福": "toefl",
    "toefl": "toefl",
    "计算机二级": "ncse",
    "计算机等级考试": "ncse",
    "ncre": "ncse",
}

# 地区 → 卷种 / 备注。用于「根据所在地区推断」并给出差异化建议。
REGION_NOTES: dict[str, str] = {
    "广东": "新高考 I 卷（3+1+2），竞争激烈，本科线常年偏高。",
    "山东": "新高考 I 卷（3+3），考生基数大，数学卷难度中等偏上。",
    "江苏": "新高考 I 卷（3+1+2），数学与英语难度全国领先。",
    "浙江": "新高考自主命题（3+3），英语读后续写为特色题型。",
    "河北": "新高考 I 卷（3+1+2）。",
    "湖北": "新高考 I 卷（3+1+2）。",
    "湖南": "新高考 I 卷（3+1+2）。",
    "福建": "新高考 I 卷（3+1+2）。",
    "河南": "老高考全国乙卷（语数外 + 文综/理综），考生数量全国第一。",
    "四川": "老高考全国甲卷。",
    "陕西": "老高考全国乙卷。",
    "北京": "自主命题，卷子整体偏灵活，语文微写作是特色。",
    "上海": "自主命题（3+3），英语一年两考。",
    "天津": "自主命题（3+3）。",
    "重庆": "新高考 II 卷（3+1+2）。",
    "辽宁": "新高考 II 卷（3+1+2）。",
    "海南": "新高考 II 卷（3+3），采用标准分转换。",
}

# 主要省份一本/本科线参考区间（仅供粗略定位，务必以当年官方为准）
REGION_BENCHMARK: dict[str, dict[str, int]] = {
    "广东": {"本科": 430, "特招": 530},
    "山东": {"本科": 440, "特招": 520},
    "江苏": {"本科": 450, "特招": 520},
    "浙江": {"本科": 440, "特招": 590},
    "河南": {"本科": 420, "一本": 520},
    "四川": {"本科": 430, "一本": 530},
    "北京": {"本科": 430, "特招": 520},
    "上海": {"本科": 400, "特招": 500},
}


# ── 大学绩点换算 ──────────────────────────────────────────────
# 百分制 → 绩点。不同学校分段略有差异，这里取最常见的两套。
GPA_TABLE_4: tuple[tuple[float, float], ...] = (
    (90, 4.0), (85, 3.7), (82, 3.3), (78, 3.0), (75, 2.7),
    (72, 2.3), (68, 2.0), (64, 1.5), (60, 1.0), (0, 0.0),
)
GPA_TABLE_5: tuple[tuple[float, float], ...] = (
    (90, 5.0), (85, 4.5), (82, 4.1), (78, 3.7), (75, 3.3),
    (72, 3.0), (68, 2.5), (64, 2.0), (60, 1.5), (0, 0.0),
)

# 保研 / 奖学金的常见绩点门槛（各校差异极大，仅作定位参考）
GPA_BENCHMARK: tuple[tuple[float, str], ...] = (
    (3.8, "多数院校保研的安全区"),
    (3.5, "保研与一等奖学金的常见门槛"),
    (3.0, "多数奖学金与交换项目的门槛"),
    (2.5, "学位与毕业相关的常见底线"),
    (2.0, "再往下就会有学业警示风险"),
)


def gpa_from_score(score: float, scale: float = 4.0) -> float:
    """百分制分数换算成绩点（4.0 制或 5.0 制）。"""
    table = GPA_TABLE_5 if scale >= 5.0 else GPA_TABLE_4
    for threshold, value in table:
        if score >= threshold:
            return value
    return 0.0


def weighted_gpa(
    rows: list[dict[str, Any]],
    scale: float = 4.0,
) -> tuple[float, float]:
    """学分加权绩点。rows 形如 [{"score": 85, "credit": 3}]，返回 (绩点, 学分合计)。"""
    total_credit = 0.0
    accumulated = 0.0
    for row in rows:
        credit = float(row.get("credit") or 0.0)
        score = float(row.get("score") or 0.0)
        total_credit += credit
        accumulated += gpa_from_score(score, scale) * credit
    if total_credit <= 0:
        return 0.0, 0.0
    return accumulated / total_credit, total_credit


def parse_credits(raw: str) -> dict[str, float]:
    """解析学分配置，形如 "math_adv:5,english:3,politics:3"。"""
    result: dict[str, float] = {}
    for chunk in (raw or "").replace("，", ",").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        name, _, value = chunk.partition(":")
        name = name.strip()
        try:
            credit = float(value.strip())
        except ValueError:
            continue
        if name and credit > 0:
            result[name] = credit
    return result


def fail_risk(rate: float) -> str:
    """按得分率给出挂科 / 及格风险的定性判断。"""
    if rate < 0.5:
        return "高风险（很可能挂科，必须优先）"
    if rate < 0.6:
        return "偏高（在及格线边缘）"
    if rate < 0.7:
        return "中等（及格基本安全，但拖绩点）"
    if rate < 0.85:
        return "较低"
    return "很低"


def gpa_benchmark_note(gpa: float) -> str:
    for threshold, text in GPA_BENCHMARK:
        if gpa >= threshold:
            return f"绩点 {gpa:.2f}：{text}"
    return f"绩点 {gpa:.2f}：低于 2.0，需要优先处理学业警示风险"


def resolve_exam_type(raw: str) -> str:
    if not raw:
        return "gaokao"
    text = raw.strip()
    key = text.lower()
    if key in PROFILES:
        return key
    for candidate in (text, key, key.replace("-", "_").replace(" ", "_")):
        hit = EXAM_ALIASES.get(candidate)
        if hit:
            return hit
    # 大小写不敏感的兜底：再扫一遍别名
    for alias, target in EXAM_ALIASES.items():
        if alias.lower() == key:
            return target
    return "gaokao"


def get_profile(exam_type: str) -> ExamProfile:
    return PROFILES.get(resolve_exam_type(exam_type), PROFILES["gaokao"])


def list_exam_types() -> list[dict[str, Any]]:
    return [
        {
            "key": key,
            "name": profile.name,
            "total_score": profile.total_score,
            "mode": profile.mode,
            "pass_line": profile.pass_line,
            "subjects": DEFAULT_SUBJECTS.get(key, []),
        }
        for key, profile in PROFILES.items()
    ]


def subject_keys(exam_type: str, chosen: str = "") -> list[str]:
    """确定该考生实际要考的科目。"""
    profile = get_profile(exam_type)
    if chosen:
        picked = [item.strip() for item in chosen.replace("，", ",").split(",") if item.strip()]
        resolved = [key for key in picked if profile.subject(key)]
        if resolved:
            return resolved
    preset = DEFAULT_SUBJECTS.get(profile.key)
    if preset:
        return [key for key in preset if profile.subject(key)] or preset
    return [item.key for item in profile.subjects]


def region_note(region: str) -> str:
    if not region:
        return "未填写地区，按全国通用卷处理。"
    known = REGION_NOTES.get(region)
    if known:
        return f"{region}：{known}"
    return f"{region}：暂无内置卷种信息，按通用策略处理，请以本地考试院公告为准。"


def region_benchmark(region: str) -> dict[str, int]:
    return REGION_BENCHMARK.get(region, {})


def format_profile(profile: ExamProfile, subjects: Optional[list[str]] = None) -> str:
    """把卷子结构渲染成给大模型看的紧凑文本。"""
    lines = [f"{profile.name}｜总分 {profile.total_score}｜常规考期 {profile.default_date}"]
    if profile.note:
        lines.append(f"说明：{profile.note}")
    for subject in profile.subjects:
        if subjects and subject.key not in subjects:
            continue
        lines.append(f"\n【{subject.name}】满分 {subject.full_score}，建议用时 {subject.minutes} 分钟")
        for section in subject.sections:
            detail = f"{section.count} 题 × {section.score_each:g} 分 = {section.total:g} 分"
            lines.append(f"  · {section.name}（{form_label(section.form)}）{detail}，约 {section.minutes} 分钟")
            if section.note:
                lines.append(f"    {section.note}")
    return "\n".join(lines)
