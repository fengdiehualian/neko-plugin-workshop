"""考点图谱与命题规律。

这是出题引擎的"宪法"。核心是 :attr:`KnowledgePoint.forms` —— 一个知识点**只会在哪些题型里出现**：

- 名句默写、字音字形只出现在填空/选择，绝不会变成大题；
- 导数、解析几何、遗传概率这些才进解答题当压轴；
- 听力题永远不会被写成"请写出一段对话"。

出题时必须先查 :func:`allowed_forms`，再决定题型，否则会出现"这道题高考根本不会这么考"的废题。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Optional

from . import _fmt as fmt
from ._profiles import (
    FORM_CALC,
    FORM_CASE,
    FORM_CHOICE,
    FORM_CODING,
    FORM_ESSAY,
    FORM_EXPERIMENT,
    FORM_FILL,
    FORM_LISTENING,
    FORM_PROOF,
    FORM_READING,
    FORM_SHORT,
    FORM_SOLVE,
    FORM_TRANSLATION,
    FORM_WRITING,
    form_label,
)

STAGE_BY_EXAM: dict[str, str] = {
    "gaokao": "高中",
    "zhongkao": "初中",
    "cet4": "大学",
    "cet6": "大学",
    "kaoyan": "大学",
    "final": "高中",
    "university": "大学",
    "cert_teacher": "大学",
    "cert_soft": "大学",
    "cert_cpa": "大学",
    "cert_law": "大学",
    "ielts": "大学",
    "toefl": "大学",
    "ncse": "大学",
}

# 试卷里的科目 key → 考点图谱里的科目 key。
# 同一套知识会分散在多张卷子上（如教资的综合素质与教育知识），这里做归并。
SUBJECT_SYLLABUS_KEY: dict[str, str] = {
    "quality": "teacher",
    "pedagogy": "teacher",
    "subject_teaching": "teacher",
    "morning": "soft",
    "afternoon": "soft",
    "accounting": "cpa",
    "auditing": "cpa",
    "finance": "cpa",
    "econ_law": "cpa",
    "tax": "cpa",
    "strategy": "cpa",
    "objective": "law",
    "subjective": "law",
    "choice": "ncse",
    "operation": "ncse",
}


def syllabus_key(subject: str) -> str:
    """把试卷科目映射到考点图谱科目。"""
    return SUBJECT_SYLLABUS_KEY.get(subject, subject)

FREQ_FACTOR: dict[str, float] = {"high": 1.5, "mid": 1.0, "low": 0.6}


@dataclass
class KnowledgePoint:
    id: str
    subject: str
    stage: str
    name: str
    forms: tuple[str, ...]
    frequency: str = "mid"
    difficulty: float = 3.0          # 1-5
    weight: float = 3.0              # 该点在本科目里的分值权重，用于算提分性价比
    prerequisites: tuple[str, ...] = ()
    traps: tuple[str, ...] = ()
    big: bool = False                # 是否常年作为压轴大题载体
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "subject": self.subject,
            "stage": self.stage,
            "name": self.name,
            "forms": list(self.forms),
            "forms_label": [form_label(item) for item in self.forms],
            "frequency": self.frequency,
            "difficulty": self.difficulty,
            "weight": self.weight,
            "prerequisites": list(self.prerequisites),
            "traps": list(self.traps),
            "big": self.big,
            "note": self.note,
        }


# 行格式：(名称, 允许题型, 频率, 难度, 权重, 前置, 易错点, 是否压轴, 备注)


def _rows_math_senior() -> tuple:
    return (
        ("集合与常用逻辑用语", (FORM_CHOICE,), "high", 1.0, 2.0, (),
         ("忽略空集的情况", "充分条件与必要条件方向弄反", "含参数的集合不分类讨论"), False, "通常是整张卷第一题"),
        ("复数", (FORM_CHOICE,), "high", 1.0, 2.0, (),
         ("把虚部写成带 i 的数", "复数的模当成实部平方", "共轭复数符号搞错"), False, "送分题，必须拿"),
        ("平面向量", (FORM_CHOICE, FORM_FILL), "mid", 2.0, 3.0, (),
         ("夹角公式分子分母颠倒", "共线条件漏掉零向量", "投影的正负号"), False, "常与三角、几何综合"),
        ("不等式与基本不等式", (FORM_CHOICE, FORM_FILL), "mid", 2.5, 3.0, (),
         ("忘记『一正二定三相等』中的定值条件", "取等条件不成立就乱用", "含参不等式不讨论开口方向"), False, "填空题高频"),
        ("函数概念、单调性与奇偶性", (FORM_CHOICE, FORM_FILL, FORM_SOLVE), "high", 3.0, 4.0, (),
         ("判断奇偶性前不先看定义域是否关于原点对称", "复合函数单调性『同增异减』用错", "分段函数分界点不单独讨论"), False, "贯穿全卷"),
        ("指数函数与对数函数", (FORM_CHOICE, FORM_FILL), "mid", 2.5, 3.0, (),
         ("对数运算性质用错（尤其除法）", "底数大于 1 与 0 到 1 的单调性搞反", "换底公式记错"), False, "比较大小的常客"),
        ("三角函数的图像与性质", (FORM_CHOICE, FORM_FILL), "high", 3.0, 3.5, (),
         ("相位变换左右方向搞反", "由图像求 ω 时忽略周期公式", "单调区间忘记加 k"), False, "选填必考"),
        ("解三角形", (FORM_CHOICE, FORM_FILL, FORM_SOLVE), "high", 3.0, 5.0, ("三角函数",),
         ("正弦定理的『两解』情形漏掉", "余弦定理符号写错", "面积公式与周长条件混用"), True, "解答题第一道常客"),
        ("数列", (FORM_CHOICE, FORM_FILL, FORM_SOLVE), "high", 3.5, 6.0, (),
         ("由 Sn 求 an 时漏掉 n=1 的检验", "等比公比为 1 或 -1 不单独讨论", "裂项相消的系数配错"), True, "解答题核心，也常压轴"),
        ("导数及其应用", (FORM_CHOICE, FORM_FILL, FORM_SOLVE), "high", 5.0, 8.0, ("函数",),
         ("求导前不化简导致计算爆炸", "极值点当成最值点", "含参讨论不按判别式与端点分类", "零点存在性证明不构造端点函数值"), True, "压轴题主力"),
        ("立体几何与空间向量", (FORM_CHOICE, FORM_FILL, FORM_SOLVE), "high", 4.0, 7.0, (),
         ("建系不说明、坐标写错", "法向量求错导致二面角符号反了", "忘记判断二面角是锐角还是钝角"), True, "解答题稳定板块"),
        ("解析几何（直线、圆、圆锥曲线）", (FORM_CHOICE, FORM_FILL, FORM_SOLVE), "high", 5.0, 8.0, (),
         ("联立后忘记判别式大于零", "设直线斜率时不讨论斜率不存在", "韦达定理代入后不检验范围"), True, "计算量最大的压轴"),
        ("概率与统计", (FORM_CHOICE, FORM_FILL, FORM_SOLVE), "high", 3.5, 6.0, (),
         ("超几何分布与二项分布混淆", "期望方差公式套错模型", "正态分布对称性用错", "独立性检验的临界值看错"), True, "解答题常客，套路固定"),
        ("计数原理与二项式定理", (FORM_CHOICE, FORM_FILL), "mid", 3.0, 3.0, (),
         ("排列组合重复计数或漏计", "二项式通项的指数配错", "分组分配不除以组数的阶乘"), False, "只在选填出现"),
        ("推理与证明、数学归纳法", (FORM_CHOICE, FORM_PROOF), "low", 3.0, 2.0, (),
         ("归纳假设没写清楚", "从 n=k 到 n=k+1 的递推不成立"), False, "低频，多在选择里以逻辑题出现"),
        ("数学建模与实际应用题", (FORM_CHOICE, FORM_SOLVE), "mid", 3.5, 4.0, (),
         ("单位不统一", "实际问题不检验结果合理性"), False, "常与函数、导数结合"),
    )


def _rows_physics_senior() -> tuple:
    return (
        ("匀变速直线运动与运动图像", (FORM_CHOICE, FORM_CALC, FORM_EXPERIMENT), "high", 2.5, 5.0, (),
         ("v-t 图像面积与斜率含义搞混", "刹车问题不判断何时停下", "自由落体与竖直上抛的正方向不统一"), False, "打点计时器实验同源"),
        ("相互作用与牛顿运动定律", (FORM_CHOICE, FORM_CALC, FORM_EXPERIMENT), "high", 3.5, 7.0, (),
         ("受力分析漏力（尤其摩擦力有无）", "整体法与隔离法选错", "连接体加速度关系写错"), True, "力学计算题基础"),
        ("曲线运动与万有引力", (FORM_CHOICE, FORM_CALC), "high", 3.5, 6.0, (),
         ("向心力是效果力，不能当成性质力加进去", "卫星变轨的能量变化判断反了", "第一宇宙速度与环绕速度混淆"), False, "选择题高频"),
        ("功、能、机械能守恒", (FORM_CHOICE, FORM_CALC), "high", 4.0, 7.0, (),
         ("摩擦力做功的正负号", "机械能守恒条件没判断就乱用", "动能定理漏掉某个力做功"), True, "计算题主力"),
        ("动量与碰撞", (FORM_CHOICE, FORM_CALC), "high", 4.0, 6.0, (),
         ("动量守恒条件（合外力为零）不写", "碰撞后速度不合理的解没有舍去", "弹性碰撞与非弹性碰撞公式混用"), True, "压轴常客"),
        ("静电场", (FORM_CHOICE, FORM_CALC), "high", 4.0, 6.0, (),
         ("场强叠加不按矢量合成", "电势高低与电势能正负搞反", "电场力做功与路径无关记成有关"), False, "选择+计算"),
        ("恒定电流", (FORM_CHOICE, FORM_EXPERIMENT), "mid", 3.0, 4.0, (),
         ("闭合电路欧姆定律的动态分析顺序错", "电表的内阻影响忽略", "伏安法内外接法选错"), False, "实验题主力"),
        ("磁场与电磁感应", (FORM_CHOICE, FORM_CALC), "high", 5.0, 8.0, (),
         ("左手定则与右手定则混用", "安培力做功与焦耳热的能量关系理不清", "导体棒切割的等效电源判断错"), True, "最难的计算题"),
        ("交变电流", (FORM_CHOICE,), "mid", 2.5, 3.0, (),
         ("有效值与最大值混淆", "变压器匝数比与电流比弄反", "远距离输电的损耗电压算错"), False, "基本只在选择题出现"),
        ("热学（气体实验定律）", (FORM_CHOICE, FORM_CALC), "mid", 3.0, 4.0, (),
         ("温度没换算成热力学温度", "压强单位不统一", "活塞受力分析漏掉大气压"), False, "选考计算题"),
        ("光学与近代物理", (FORM_CHOICE,), "high", 2.0, 4.0, (),
         ("光电效应的截止频率与遏止电压关系搞反", "能级跃迁的能量差算错", "折射率与临界角公式记反"), False, "纯选择题，属于必拿分"),
        ("物理实验（含数据处理）", (FORM_EXPERIMENT,), "high", 3.0, 6.0, (),
         ("有效数字与单位不写", "图像法处理数据时斜率含义搞错", "仪器读数规则（游标卡尺、螺旋测微器）记错"), False, "实验题单独成块"),
    )


def _rows_chemistry_senior() -> tuple:
    return (
        ("化学与 STSE、传统文化中的化学", (FORM_CHOICE,), "high", 1.0, 2.0, (),
         ("把物理变化当成化学变化", "常见材料的成分记混（如玻璃、陶瓷）"), False, "第一题，必拿分"),
        ("物质的量与阿伏加德罗常数", (FORM_CHOICE, FORM_CALC), "high", 2.5, 4.0, (),
         ("标准状况下非气体物质也用 22.4", "溶液浓度题忽略体积变化", "可逆反应中微粒数判断"), False, "选择题常客"),
        ("离子反应与氧化还原反应", (FORM_CHOICE, FORM_FILL), "high", 3.0, 5.0, (),
         ("离子方程式不配平电荷", "拆分时把弱电解质和沉淀拆开", "氧化还原的先后顺序判断错"), False, "贯穿全卷"),
        ("元素周期律与元素推断", (FORM_CHOICE, FORM_FILL), "high", 3.0, 5.0, (),
         ("半径比较时电子层与核电荷数的主次搞反", "金属性与非金属性对应的性质记混"), False, "推断题基础"),
        ("元素化合物（钠镁铝铁铜、氯硫氮）", (FORM_CHOICE, FORM_FILL), "high", 3.0, 5.0, (),
         ("反应条件与产物对应错（浓度、温度不同产物不同）", "离子共存判断漏掉隐含的氧化还原"), False, "流程题基础"),
        ("化学反应与能量（热化学）", (FORM_CHOICE, FORM_CALC), "mid", 3.0, 4.0, (),
         ("焓变的符号与吸放热对应关系", "盖斯定律加减方程式时系数没同步", "键能计算与总能量计算混用"), False, "原理题常客"),
        ("化学反应速率与化学平衡", (FORM_CHOICE, FORM_CALC), "high", 4.0, 7.0, (),
         ("平衡状态的判断标志选错（要『变量不变』）", "压强对平衡的影响漏掉气体分子数变化", "平衡常数只受温度影响记错", "转化率与体积分数混淆"), True, "原理综合题"),
        ("水溶液中的离子平衡", (FORM_CHOICE, FORM_CALC), "high", 4.5, 7.0, (),
         ("水的电离被抑制时 Kw 仍用常温值", "电离与水解的强弱判断反了", "Ksp 计算时离子浓度幂次写错", "电荷守恒与物料守恒式列错"), True, "最难的选择与计算"),
        ("电化学（原电池与电解池）", (FORM_CHOICE, FORM_CALC), "high", 3.5, 6.0, (),
         ("原电池与电解池的电极名称搞反", "电解时放电顺序记错", "离子交换膜的类型判断错", "电极反应式的电荷与介质没配平"), False, "选择+原理题"),
        ("有机化学基础", (FORM_CHOICE, FORM_FILL), "high", 4.0, 7.0, (),
         ("官能团名称写错字", "同分异构体数目数漏或数重", "反应类型判断错（取代与加成）", "有机方程式的条件与小分子漏写"), True, "合成推断大题"),
        ("物质结构与性质（选考）", (FORM_CHOICE, FORM_FILL), "mid", 3.5, 5.0, (),
         ("杂化轨道类型判断错", "晶胞中微粒数的均摊法算错", "氢键与范德华力的影响混淆"), False, "结构题"),
        ("化学实验综合", (FORM_EXPERIMENT, FORM_FILL), "high", 3.5, 6.0, (),
         ("检验气体时没有排除干扰", "沉淀洗涤是否干净的检验方法写不出来", "防倒吸与尾气处理装置选错"), True, "实验大题"),
    )


def _rows_chinese_senior() -> tuple:
    return (
        ("字音字形与词语辨析", (FORM_CHOICE,), "mid", 1.5, 2.0, (),
         ("形近字读半边", "多音字按常见读音套"), False, "新高考多并入语用题"),
        ("成语与熟语运用", (FORM_CHOICE,), "high", 2.0, 3.0, (),
         ("望文生义", "感情色彩与语境不符", "对象误用（如『美轮美奂』只形容建筑）"), False, "语用选择"),
        ("病句辨析与修改", (FORM_CHOICE, FORM_FILL), "high", 2.5, 3.0, (),
         ("搭配不当与成分残缺分辨不清", "两面词（能否、是否）的照应问题", "介词开头导致主语残缺"), False, "语用必考"),
        ("语言连贯、衔接与补写", (FORM_CHOICE, FORM_FILL), "high", 2.5, 3.0, (),
         ("补写句子不关注前后文的逻辑词", "主语一致性没保持"), False, "语用主观题"),
        ("修辞手法与表达效果", (FORM_CHOICE, FORM_SHORT), "mid", 2.5, 3.0, (),
         ("比喻与比拟分不清", "只答手法不答效果"), False, "散见于阅读与语用"),
        ("文言实词与虚词", (FORM_CHOICE,), "high", 3.0, 4.0, (),
         ("以今义释古义", "一词多义不结合语境", "18 个常见虚词用法混淆"), False, "文言选择"),
        ("文言句式与断句", (FORM_CHOICE, FORM_FILL), "high", 3.0, 4.0, (),
         ("断句不看虚词标志（夫、盖、也、矣）", "宾语前置与定语后置辨认错", "固定句式（『奈……何』）不认得"), False, "断句+翻译"),
        ("古代文化常识", (FORM_CHOICE,), "mid", 2.0, 2.0, (),
         ("官职升降的称谓搞反（擢、贬、谪）", "科举名次与称谓对不上"), False, "选择题"),
        ("古代诗歌鉴赏", (FORM_CHOICE, FORM_SHORT), "high", 3.5, 6.0, (),
         ("情感概括空泛，不结合诗句", "手法辨识错误（借景抒情与托物言志）", "炼字题只说『生动形象』不给理由"), False, "简答题"),
        ("名篇名句默写", (FORM_FILL,), "high", 1.5, 6.0, (),
         ("同音字写错（『唯』与『惟』）", "理解性默写不审题导致写错篇目"), False, "纯记忆，性价比最高"),
        ("信息类文本阅读", (FORM_CHOICE, FORM_SHORT), "high", 3.0, 6.0, (),
         ("把『推断』当『原文』", "论证分析题不辨论证方法与论点", "选项的偷换概念与以偏概全"), False, "现代文阅读Ⅰ"),
        ("文学类文本阅读", (FORM_CHOICE, FORM_SHORT), "high", 4.0, 8.0, (),
         ("主旨拔高或过度解读", "人物形象分析没有文本依据", "叙述视角与人称的作用说不出"), False, "现代文阅读Ⅱ"),
        ("写作（议论文）", (FORM_WRITING,), "high", 4.0, 60.0, (),
         ("审题偏题（抓错核心概念）", "论据与论点不匹配", "结构不清晰，没有分论点", "通篇说理没有现实关照"), True, "分值最大的一块"),
    )


def _rows_english_senior() -> tuple:
    return (
        ("听力理解", (FORM_LISTENING,), "high", 2.5, 30.0, (),
         ("只听关键词不看选项预设", "数字、时间、地点信息漏记"), False, "可提前读题"),
        ("阅读理解之细节题", (FORM_CHOICE, FORM_READING), "high", 2.5, 10.0, (),
         ("不回原文定位，凭印象选", "选项与原文的偷换词没看出来"), False, "最容易提分的一块"),
        ("阅读理解之推断与主旨", (FORM_CHOICE, FORM_READING), "high", 3.5, 12.0, (),
         ("把细节当主旨", "推断过度，原文无依据", "标题题选了以偏概全的那个"), False, "拉开差距的关键"),
        ("七选五（段落补全）", (FORM_READING, FORM_CHOICE), "high", 3.0, 12.5, (),
         ("不利用代词与连接词的指代关系", "段落首尾句的衔接不看"), False, "技巧性强"),
        ("完形填空", (FORM_FILL,), "high", 3.0, 15.0, (),
         ("不读全文就填空", "忽视上下文的复现线索", "近义词的搭配区分不清"), False, "语境题"),
        ("语法填空", (FORM_FILL,), "high", 3.0, 15.0, (),
         ("有提示词与无提示词的应对策略不分", "词性转换方向搞反", "时态语态与从句引导词漏判"), False, "套路最固定，提分快"),
        ("应用文写作", (FORM_WRITING,), "high", 3.0, 15.0, (),
         ("格式与人称写错", "要点遗漏", "中式英语与从句堆砌"), False, "背框架可稳拿基础分"),
        ("读后续写 / 概要写作", (FORM_WRITING,), "high", 4.5, 25.0, (),
         ("情节与原文断裂、人物性格前后不一", "只写对话不写心理与动作", "时态与原文不一致"), True, "最拉分的一块"),
        ("词汇、短语与固定搭配", (FORM_FILL, FORM_CHOICE), "high", 2.5, 8.0, (),
         ("单词认识但搭配不认识", "形近词混淆（affect/effect）"), False, "贯穿全卷的基础"),
        ("长难句分析", (FORM_READING, FORM_TRANSLATION), "mid", 3.5, 6.0, (),
         ("找不准主干，被插入语带偏", "从句嵌套时关系词指代判断错"), False, "影响阅读与翻译"),
    )


def _rows_biology_senior() -> tuple:
    return (
        ("细胞的分子组成与结构", (FORM_CHOICE,), "high", 2.0, 4.0, (),
         ("原核与真核的区别记混", "细胞器功能张冠李戴（尤其高尔基体在动植物中的差异）"), False, "选择题"),
        ("细胞代谢（呼吸与光合）", (FORM_CHOICE, FORM_FILL, FORM_EXPERIMENT), "high", 4.0, 8.0, (),
         ("光合与呼吸的气体交换关系理不清", "坐标图的光补偿点、饱和点判断错", "实验的自变量与因变量找反"), True, "实验与曲线题"),
        ("遗传的分子基础", (FORM_CHOICE, FORM_FILL), "high", 3.0, 5.0, (),
         ("复制与转录翻译的模板、原料、酶混淆", "碱基计算不考虑双链"), False, "选择+填空"),
        ("遗传规律与概率计算", (FORM_CHOICE, FORM_CALC), "high", 4.5, 10.0, (),
         ("显隐性判断错", "伴性遗传与常染色体遗传不区分", "致死与从性遗传的特殊比例不认得", "概率计算不乘上前提条件"), True, "压轴大题"),
        ("变异、育种与进化", (FORM_CHOICE, FORM_FILL), "mid", 3.0, 5.0, (),
         ("基因突变与染色体变异分不清", "育种方法的原理与优缺点对应错", "进化的实质不是个体而是种群"), False, "选择+简答"),
        ("稳态与调节（神经、体液、免疫）", (FORM_CHOICE, FORM_FILL), "high", 3.5, 7.0, (),
         ("反射弧的组成与兴奋传导方向", "激素的分级调节与反馈调节搞反", "体液免疫与细胞免疫的细胞分工混淆"), False, "选择+简答"),
        ("种群、群落与生态系统", (FORM_CHOICE, FORM_FILL), "high", 3.0, 7.0, (),
         ("种群密度调查方法（样方法、标志重捕法）适用对象搞错", "能量流动的传递效率算错", "群落演替的类型判断错"), False, "选择+简答"),
        ("实验设计与结果分析", (FORM_EXPERIMENT, FORM_SHORT), "high", 4.0, 7.0, (),
         ("不设置对照或对照设置不合理", "无关变量不控制（等量、适宜、相同）", "结论超出实验所能支持的范围"), True, "拉开差距的主观题"),
    )


def _rows_english_college() -> tuple:
    return (
        ("听力：短篇新闻 / 讲座", (FORM_LISTENING,), "high", 3.0, 50.0, (),
         ("只抓词不抓结构", "开头的主旨句漏听"), False, "六级讲座篇幅更长"),
        ("听力：长对话与篇章", (FORM_LISTENING,), "high", 3.0, 100.0, (),
         ("不预览选项", "转折词后的内容才是考点"), False, "占比最大"),
        ("选词填空（15 选 10）", (FORM_FILL,), "mid", 3.0, 35.5, (),
         ("不先判词性就硬填", "忽略语法结构线索"), False, "性价比低，可后置"),
        ("长篇阅读匹配", (FORM_READING,), "high", 2.5, 71.0, (),
         ("逐字读导致时间不够", "不圈关键词（专有名词、数字）"), False, "技巧性强，易拿分"),
        ("仔细阅读", (FORM_CHOICE, FORM_READING), "high", 3.5, 142.0, (),
         ("主旨与细节混淆", "推断题选了原文照抄的干扰项"), True, "分值最高的一块"),
        ("段落翻译（汉译英）", (FORM_TRANSLATION,), "high", 4.0, 106.5, (),
         ("中式直译、缺少主谓结构", "时态语态不统一", "专有名词与文化词不会转换"), True, "短期可明显提分"),
        ("短文写作", (FORM_WRITING,), "high", 3.5, 106.5, (),
         ("模板生硬、没有观点展开", "衔接词单一", "语法错误集中在从句"), False, "背框架有效"),
        ("高频词汇与学术搭配", (FORM_FILL, FORM_CHOICE), "high", 2.5, 30.0, (),
         ("认识但不会拼写", "搭配记错（make / do / take）"), False, "贯穿全卷"),
        ("长难句与语法结构", (FORM_READING, FORM_TRANSLATION), "high", 3.5, 40.0, (),
         ("主干找不出来", "分词作状语的逻辑主语判断错"), False, "影响阅读与翻译"),
    )


def _rows_math_junior() -> tuple:
    return (
        ("有理数与实数运算", (FORM_CHOICE, FORM_FILL), "high", 1.5, 4.0, (),
         ("符号错误", "运算顺序与去括号规则"), False, "基础送分"),
        ("一元二次方程与判别式", (FORM_CHOICE, FORM_FILL, FORM_SOLVE), "high", 3.0, 8.0, (),
         ("忽略二次项系数不为零", "韦达定理使用时忘记判别式前提"), False, "解答题常见"),
        ("一次函数与反比例函数", (FORM_CHOICE, FORM_FILL), "high", 2.5, 6.0, (),
         ("k、b 的几何意义搞反", "反比例函数的增减性不分区间说"), False, "选择题常考图像"),
        ("二次函数（含压轴）", (FORM_FILL, FORM_SOLVE), "high", 4.5, 14.0, (),
         ("顶点式与交点式转换错", "动点问题不分类讨论", "最值问题忽略自变量取值范围"), True, "中考数学压轴主力"),
        ("三角形、全等与相似", (FORM_CHOICE, FORM_FILL, FORM_SOLVE), "high", 3.5, 10.0, (),
         ("全等与相似的判定条件混用", "对应关系写错导致边角配错"), True, "几何证明"),
        ("四边形与圆", (FORM_CHOICE, FORM_FILL, FORM_SOLVE), "high", 3.5, 10.0, (),
         ("圆周角定理与圆心角关系", "切线的判定不先连半径", "辅助线不会作"), True, "几何综合"),
        ("锐角三角函数与解直角三角形", (FORM_CHOICE, FORM_FILL, FORM_SOLVE), "mid", 3.0, 6.0, (),
         ("正弦余弦对边邻边搞反", "实际应用题不画示意图"), False, "应用题"),
        ("概率与统计", (FORM_CHOICE, FORM_FILL, FORM_SOLVE), "mid", 2.5, 6.0, (),
         ("树状图与列表遗漏", "频率与概率混淆"), False, "套路题"),
        ("不等式与不等式组", (FORM_CHOICE, FORM_FILL, FORM_SOLVE), "mid", 2.5, 6.0, (),
         ("两边同除以负数不变号", "数轴表示时实心空心点搞错"), False, "基础"),
    )


def _rows_math_college() -> tuple:
    return (
        ("极限与连续", (FORM_CHOICE, FORM_FILL, FORM_SOLVE), "high", 3.0, 10.0, (),
         ("等价无穷小替换在加减中用错", "洛必达法则条件不验证", "左右极限不分别求"), False, "高数基础"),
        ("导数与微分中值定理", (FORM_CHOICE, FORM_FILL, FORM_SOLVE), "high", 4.0, 15.0, (),
         ("罗尔与拉格朗日定理条件不写", "极值判别用错二阶导数"), True, "证明题常客"),
        ("一元函数积分学", (FORM_CHOICE, FORM_FILL, FORM_SOLVE), "high", 4.0, 15.0, (),
         ("换元不换限", "分部积分的 u、v 选错", "变限积分求导漏掉上限导数"), True, "计算量大"),
        ("多元函数微分与二重积分", (FORM_CHOICE, FORM_FILL, FORM_SOLVE), "high", 4.5, 15.0, (),
         ("偏导与全微分混淆", "积分次序交换时区域画错"), True, "数一重点"),
        ("级数", (FORM_CHOICE, FORM_FILL, FORM_SOLVE), "mid", 4.0, 10.0, (),
         ("收敛半径求错", "交错级数判别条件漏项", "和函数求导后不还原"), False, "数一数三"),
        ("行列式与矩阵", (FORM_CHOICE, FORM_FILL), "high", 3.0, 10.0, (),
         ("矩阵乘法不满足交换律", "伴随矩阵的公式记错", "初等变换与初等矩阵对应关系反了"), False, "线代基础"),
        ("线性方程组与向量组", (FORM_CHOICE, FORM_SOLVE), "high", 4.0, 15.0, (),
         ("解的结构（特解+通解）写不完整", "极大无关组与秩的关系不清"), True, "线代大题"),
        ("特征值与二次型", (FORM_CHOICE, FORM_SOLVE), "high", 4.0, 12.0, (),
         ("相似对角化条件判断错", "正交变换与配方法混淆"), True, "线代压轴"),
        ("概率论与数理统计", (FORM_CHOICE, FORM_FILL, FORM_SOLVE), "high", 4.0, 20.0, (),
         ("分布函数与密度函数关系搞混", "条件概率与独立性判断错", "参数估计的矩估计与极大似然步骤遗漏"), True, "数一数三大头"),
    )


def _rows_politics_college() -> tuple:
    return (
        ("马克思主义基本原理", (FORM_CHOICE, FORM_SHORT), "high", 3.5, 24.0, (),
         ("哲学原理与方法论对应错", "政治经济学概念混淆（剩余价值、不变资本）"), True, "分析题常考"),
        ("毛泽东思想和中国特色社会主义理论体系", (FORM_CHOICE, FORM_SHORT), "high", 3.0, 30.0, (),
         ("会议与文件的时间线记混", "概念表述不准确被多选扣分"), True, "分值最大"),
        ("中国近现代史纲要", (FORM_CHOICE, FORM_SHORT), "mid", 2.5, 20.0, (),
         ("事件顺序颠倒", "条约内容记混"), False, "记忆为主"),
        ("思想道德修养与法律基础", (FORM_CHOICE, FORM_SHORT), "mid", 2.0, 16.0, (),
         ("道德与法律的边界", "法条适用场景"), False, "易拿分"),
        ("形势与政策、当代世界经济与政治", (FORM_CHOICE, FORM_SHORT), "mid", 2.5, 10.0, (),
         ("时政记忆不牢", "分析题不结合材料"), True, "考前冲刺性价比高"),
    )


def _rows_math_advanced() -> tuple:
    """高等数学。"""
    return (
        ("函数、极限与连续", (FORM_CHOICE, FORM_FILL, FORM_CALC), "high", 2.5, 8.0, (),
         ("等价无穷小在加减法中直接替换", "洛必达法则不先验证 0/0 或 ∞/∞", "分段函数在分界点不分别求左右极限"),
         False, "整门课的起点，极限算错后面全崩"),
        ("导数与微分", (FORM_CHOICE, FORM_FILL, FORM_CALC, FORM_SOLVE), "high", 3.5, 12.0, (),
         ("复合函数求导漏一层", "隐函数求导不对 y 用链式法则", "参数方程二阶导直接对一阶导再求导而忘记除以 x'(t)"),
         False, "计算题主力"),
        ("中值定理与导数应用", (FORM_CHOICE, FORM_FILL, FORM_PROOF), "high", 4.0, 12.0, ("导数与微分",),
         ("罗尔 / 拉格朗日定理的条件不写就直接用", "构造辅助函数没有依据", "极值与最值不比较端点"),
         True, "证明题的第一大来源"),
        ("不定积分", (FORM_CHOICE, FORM_FILL, FORM_CALC), "high", 3.0, 10.0, (),
         ("忘记加常数 C", "分部积分的 u、dv 选反导致越算越复杂", "三角代换不换回原变量"),
         False, "技巧性强，靠题型积累"),
        ("定积分与反常积分", (FORM_CHOICE, FORM_FILL, FORM_CALC), "high", 3.5, 12.0, ("不定积分",),
         ("换元不换限", "奇偶性与周期性不利用", "反常积分不先判断敛散性就直接算"),
         False, "常与面积、体积结合"),
        ("多元函数微分学", (FORM_CHOICE, FORM_FILL, FORM_CALC), "high", 4.0, 12.0, ("导数与微分",),
         ("偏导与全微分混淆", "复合函数求导不画变量关系图导致漏项", "求条件极值不写拉格朗日函数"),
         False, "数一重点，计算量集中"),
        ("重积分与曲线曲面积分", (FORM_CHOICE, FORM_FILL, FORM_CALC), "mid", 4.0, 10.0, ("定积分与反常积分",),
         ("积分区域画错", "交换积分次序时上下限写反", "高斯公式与斯托克斯公式的适用条件不判断"),
         True, "数一压轴常客"),
        ("无穷级数", (FORM_CHOICE, FORM_FILL, FORM_CALC, FORM_PROOF), "mid", 4.0, 10.0, (),
         ("收敛半径端点不单独讨论", "交错级数判别漏掉单调递减条件", "求和函数求导后不积分还原"),
         False, "套路固定，背住判别法就能拿分"),
        ("常微分方程", (FORM_CHOICE, FORM_FILL, FORM_CALC, FORM_SOLVE), "high", 4.0, 12.0, (),
         ("可分离变量与齐次方程识别错", "二阶常系数非齐次的特解形式设错", "不代回初值条件确定常数"),
         True, "应用题与计算题都爱考"),
        ("空间解析几何与向量代数", (FORM_CHOICE, FORM_FILL), "low", 2.5, 6.0, (),
         ("叉乘的方向与右手定则", "平面与直线方程的相互转化"),
         False, "低频，多为选择填空"),
    )


def _rows_linear_algebra() -> tuple:
    """线性代数。"""
    return (
        ("行列式", (FORM_CHOICE, FORM_FILL, FORM_CALC), "high", 2.5, 10.0, (),
         ("行列式与矩阵记号混淆", "展开时代数余子式的符号写错", "范德蒙德行列式的形式记不住"),
         False, "基础，必须熟练"),
        ("矩阵及其运算", (FORM_CHOICE, FORM_FILL, FORM_CALC), "high", 3.0, 12.0, ("行列式",),
         ("默认矩阵乘法可交换", "转置与逆的运算顺序搞反", "伴随矩阵的公式与秩的关系记错"),
         False, "整门课的语言"),
        ("向量组的线性相关性", (FORM_CHOICE, FORM_FILL, FORM_PROOF), "high", 4.0, 14.0, ("矩阵及其运算",),
         ("相关性定义与具体的线性组合系数不分", "极大无关组不唯一就说成唯一", "用秩判断时行列式与向量组对象搞错"),
         True, "证明题主战场"),
        ("线性方程组", (FORM_CHOICE, FORM_CALC, FORM_SOLVE), "high", 4.0, 18.0, ("向量组的线性相关性",),
         ("解的结构写成特解或通解之一", "含参数讨论时不分 r(A) 与 r(A|b)", "基础解系的个数算成 n-r(A|b)"),
         True, "线代第一大题"),
        ("特征值与特征向量", (FORM_CHOICE, FORM_CALC, FORM_SOLVE), "high", 4.0, 16.0, ("矩阵及其运算",),
         ("特征向量不能是零向量这一条被忽略", "不同特征值对应特征向量线性无关用反", "重根时几何重数与代数重数不分"),
         True, "线代第二大题"),
        ("相似矩阵与对角化", (FORM_CHOICE, FORM_CALC, FORM_PROOF), "mid", 4.0, 14.0, ("特征值与特征向量",),
         ("可对角化的充要条件只写一半", "实对称矩阵的特殊性质忘记用"),
         True, "常与二次型联合出题"),
        ("二次型", (FORM_CHOICE, FORM_CALC, FORM_SOLVE), "mid", 3.5, 12.0, ("相似矩阵与对角化",),
         ("正交变换与配方法得出的标准形混为一谈", "惯性指数与秩的关系", "正定判定的顺序主子式条件写不全"),
         False, "压轴常客"),
    )


def _rows_probability() -> tuple:
    """概率论与数理统计。"""
    return (
        ("随机事件与概率", (FORM_CHOICE, FORM_FILL, FORM_CALC), "high", 2.5, 12.0, (),
         ("互斥与独立混淆", "条件概率公式的分子分母搞反", "全概率公式的划分不完备"),
         False, "基础但处处用到"),
        ("一维随机变量及其分布", (FORM_CHOICE, FORM_FILL, FORM_CALC), "high", 3.0, 14.0, (),
         ("分布函数与密度函数关系搞混", "连续型随机变量在某点的概率当成密度值", "常见分布的参数意义记错"),
         False, "选择题高频"),
        ("多维随机变量及其分布", (FORM_CHOICE, FORM_FILL, FORM_CALC), "high", 4.0, 14.0, ("一维随机变量及其分布",),
         ("独立性判断只验边缘不验联合", "卷积公式的积分限写错", "二维正态的条件分布记不住"),
         True, "计算题分水岭"),
        ("随机变量的数字特征", (FORM_CHOICE, FORM_CALC, FORM_SOLVE), "high", 4.0, 16.0, ("一维随机变量及其分布",),
         ("方差公式 D(X)=E(X²)-E²(X) 记成反的", "协方差与相关系数不分", "不独立时直接用可加性"),
         True, "大题必考"),
        ("大数定律与中心极限定理", (FORM_CHOICE, FORM_PROOF), "mid", 3.0, 8.0, (),
         ("三个大数定律的条件张冠李戴", "中心极限定理的标准化形式漏掉 n"),
         False, "多为选择与简答"),
        ("抽样分布与统计量", (FORM_CHOICE, FORM_FILL), "mid", 3.0, 10.0, (),
         ("χ²、t、F 三种分布的构造记混", "样本方差除以 n 还是 n-1"),
         False, "统计推断的基础"),
        ("参数估计", (FORM_CHOICE, FORM_CALC, FORM_SOLVE), "high", 4.5, 16.0, ("抽样分布与统计量",),
         ("矩估计用错阶数", "极大似然估计不先取对数就求导", "估计量的无偏性判断漏算期望"),
         True, "统计部分分值最大"),
        ("假设检验", (FORM_CHOICE, FORM_CALC, FORM_SOLVE), "mid", 3.5, 10.0, ("参数估计",),
         ("原假设与备择假设设反", "两类错误的概念颠倒", "拒绝域与 P 值的关系说不清"),
         False, "考频中等，套路固定"),
    )


def _rows_cs() -> tuple:
    """计算机与程序设计（大学计算机基础 / 数据结构 / 程序设计）。"""
    return (
        ("程序基础：变量、运算与控制结构", (FORM_CHOICE, FORM_FILL, FORM_CODING), "high", 2.5, 12.0, (),
         ("整数除法与浮点除法的区别", "循环边界写错导致多算或少算一次", "运算符优先级（尤其 && 与 ||）"),
         False, "送分块，必须拿满"),
        ("数组、字符串与指针", (FORM_CHOICE, FORM_FILL, FORM_CODING), "high", 3.5, 14.0, (),
         ("数组越界", "指针未初始化就解引用", "字符串结尾 '\\0' 的空间漏算"),
         False, "C 语言的核心失分区"),
        ("线性表、栈与队列", (FORM_CHOICE, FORM_FILL, FORM_CODING), "high", 3.0, 12.0, (),
         ("栈空/队满的判定条件写反", "循环队列的队空与队满区分不开", "链表操作时指针丢失导致断链"),
         False, "读程序写结果的高频来源"),
        ("树与二叉树", (FORM_CHOICE, FORM_FILL, FORM_CODING), "high", 4.0, 14.0, (),
         ("前中后序遍历的递归顺序记混", "由两种遍历序列重建二叉树时根的位置找错", "哈夫曼树的带权路径长度算错"),
         True, "数据结构大题主力"),
        ("图（遍历、最短路、最小生成树）", (FORM_CHOICE, FORM_FILL, FORM_CODING), "high", 4.5, 14.0, (),
         ("DFS 与 BFS 的结果序列写错", "Dijkstra 不能处理负权边这一前提被忽略", "Prim 与 Kruskal 的适用场景不分"),
         True, "压轴级考点"),
        ("排序与查找", (FORM_CHOICE, FORM_FILL, FORM_CODING), "high", 3.5, 14.0, (),
         ("各排序算法的稳定性记错", "快排每一趟的结果写不出来", "二分查找的边界条件与返回值"),
         True, "必考，且容易拿满分"),
        ("递归、复杂度与算法设计", (FORM_CHOICE, FORM_SHORT, FORM_CODING), "mid", 3.5, 10.0, (),
         ("时间复杂度只算循环层数不看数据规模变化", "递归没有出口或出口条件写错"),
         False, "简答题常考"),
        ("操作系统（进程、调度、死锁、内存）", (FORM_CHOICE, FORM_SHORT), "high", 3.5, 12.0, (),
         ("进程与线程的概念混淆", "死锁的四个条件记不全", "银行家算法的安全性判定步骤遗漏"),
         False, "选择题与简答题"),
        ("计算机网络（体系结构与 TCP/IP）", (FORM_CHOICE, FORM_SHORT), "high", 3.5, 12.0, (),
         ("OSI 七层与 TCP/IP 四层的对应搞错", "TCP 三次握手与四次挥手的序号变化", "子网划分与掩码计算错误"),
         False, "记忆量大但不难"),
        ("数据库与 SQL", (FORM_CHOICE, FORM_SHORT, FORM_CODING), "mid", 3.5, 12.0, (),
         ("范式判断（尤其 2NF 与 3NF 的区别）", "连接查询漏掉连接条件导致笛卡尔积", "GROUP BY 与 HAVING 的用法混淆"),
         False, "操作题与简答都有"),
    )


def _rows_physics_college() -> tuple:
    """大学物理。"""
    return (
        ("质点运动学与牛顿定律", (FORM_CHOICE, FORM_FILL, FORM_CALC), "high", 2.5, 12.0, (),
         ("位矢、位移与路程不分", "变力作用下不先列微分方程", "自然坐标系下的切向与法向加速度搞混"),
         False, "基础计算"),
        ("动量、功与能量守恒", (FORM_CHOICE, FORM_CALC), "high", 3.0, 12.0, (),
         ("守恒条件不判断就用", "变力做功不积分而是直接用 Fs", "碰撞后不符合物理实际的解不舍去"),
         False, "选择题与计算题"),
        ("刚体定轴转动", (FORM_CHOICE, FORM_CALC), "mid", 3.5, 10.0, (),
         ("转动惯量的平行轴定理用错", "力矩与角动量的方向判断", "角动量守恒与机械能守恒的适用条件不分"),
         False, "中频考点"),
        ("静电场与高斯定理", (FORM_CHOICE, FORM_FILL, FORM_CALC), "high", 3.5, 14.0, (),
         ("高斯面选取不满足对称性", "电势零点改变后电势差不变这一性质被忽略", "场强叠加不做矢量分解"),
         True, "电磁学大题"),
        ("恒定磁场与电磁感应", (FORM_CHOICE, FORM_CALC), "high", 4.0, 14.0, (),
         ("安培环路定理的对称性条件", "动生与感生电动势的判断", "楞次定律判断感应电流方向时方向搞反"),
         True, "最难的一块"),
        ("振动与波", (FORM_CHOICE, FORM_FILL, FORM_CALC), "mid", 3.0, 12.0, (),
         ("初相位求错", "波的干涉加强与减弱条件写反", "驻波的波腹与波节位置"),
         False, "公式多但套路固定"),
        ("光学（干涉、衍射、偏振）", (FORM_CHOICE, FORM_FILL, FORM_CALC), "mid", 3.0, 12.0, (),
         ("光程差不计算介质中的波长变化", "半波损失漏掉", "光栅方程的级次判断"),
         False, "计算题常客"),
        ("热学与气体动理论", (FORM_CHOICE, FORM_FILL, FORM_CALC), "mid", 2.5, 10.0, (),
         ("内能、热量与功的符号约定不统一", "循环效率算错", "麦克斯韦速率分布三种速率搞混"),
         False, "记忆为主"),
        ("近代物理初步（量子与相对论）", (FORM_CHOICE,), "low", 2.0, 6.0, (),
         ("光电效应的方程与截止频率", "不确定关系的表达式"),
         False, "多为概念选择题"),
    )


def _rows_economics() -> tuple:
    """经管类（微观经济学 / 宏观经济学 / 管理学）。"""
    return (
        ("供求、弹性与市场均衡", (FORM_CHOICE, FORM_CALC, FORM_SHORT), "high", 3.0, 12.0, (),
         ("点弹性与弧弹性用混", "供给与需求同时移动时均衡量方向判断不清", "弹性与总收益的关系记反"),
         False, "微观入门，必考"),
        ("消费者选择与效用最大化", (FORM_CHOICE, FORM_CALC), "mid", 3.5, 10.0, (),
         ("边际替代率递减与边际效用递减混淆", "角点解不检查非负约束", "收入效应与替代效应方向判断错"),
         False, "计算题"),
        ("生产、成本与市场结构", (FORM_CHOICE, FORM_CALC, FORM_SHORT), "high", 4.0, 14.0, (),
         ("边际成本曲线穿过 ATC 的最低点这一性质用反", "完全竞争短期停产条件写成 P<ATC 而非 P<AVC",
          "垄断与垄断竞争长期均衡的差别"),
         True, "微观大题"),
        ("市场失灵、外部性与公共物品", (FORM_CHOICE, FORM_SHORT, FORM_CASE), "mid", 3.0, 10.0, (),
         ("外部性与产权界定的政策对应错", "公共物品的两大特征说不全"),
         False, "简答与案例"),
        ("国民收入核算与宏观经济模型", (FORM_CHOICE, FORM_CALC, FORM_SHORT), "high", 3.5, 14.0, (),
         ("支出法与收入法算出的 GDP 不相等就以为算错", "乘数效应漏掉边际消费倾向", "IS-LM 移动的斜率与挤出效应"),
         True, "宏观大题"),
        ("货币、利率与财政货币政策", (FORM_CHOICE, FORM_SHORT, FORM_CASE), "mid", 3.5, 12.0, (),
         ("货币乘数与法定准备金率的关系搞反", "扩张性政策在流动性陷阱中失效的理由说不出"),
         False, "简答与材料题"),
        ("管理学基础（计划、组织、领导、控制）", (FORM_CHOICE, FORM_SHORT, FORM_CASE), "high", 3.0, 12.0, (),
         ("管理理论流派与代表人物对不上", "案例分析只复述案例不套用理论"),
         False, "记忆 + 案例"),
    )


def _rows_teacher_cert() -> tuple:
    """教师资格证（综合素质 + 教育知识与能力 + 学科教学）。"""
    return (
        ("教育观、学生观、教师观（三观）", (FORM_CHOICE, FORM_CASE, FORM_WRITING), "high", 2.5, 20.0, (),
         ("只答理念不结合材料", "学生观的『两独一发』说不完整", "教师角色的转变与行为转变混为一谈"),
         True, "材料分析题必考，背住就能拿分"),
        ("教育法律法规与教师职业道德", (FORM_CHOICE, FORM_CASE), "mid", 2.5, 12.0, (),
         ("法律责任的主体判断错（学校 / 教师 / 监护人）", "职业道德六条与具体行为对不上"),
         False, "选择题为主"),
        ("文化素养（历史、科技、文学、艺术常识）", (FORM_CHOICE,), "low", 2.0, 8.0, (),
         ("中外历史事件与人物的对应", "科技成就的时间顺序"),
         False, "范围广、性价比低，考前过一遍即可"),
        ("教育学基础（课程、教学、德育）", (FORM_CHOICE, FORM_SHORT, FORM_CASE), "high", 3.5, 18.0, (),
         ("教学原则与教学方法对不上", "德育原则与德育方法混用", "辨析题只判断不说明理由"),
         True, "辨析与简答的主战场"),
        ("心理学基础（认知、学习理论、发展心理）", (FORM_CHOICE, FORM_SHORT, FORM_CASE), "high", 3.5, 16.0, (),
         ("行为主义与人本主义的代表人物混淆", "皮亚杰四阶段与维果茨基的最近发展区分不清",
          "辨析题把『遗忘』与『消退』当成一回事"),
         True, "简答题高频"),
        ("教学设计与教学实施", (FORM_CASE, FORM_SOLVE, FORM_SHORT), "high", 4.0, 20.0, (),
         ("教案缺少教学目标与教学重难点", "导入环节写得像总结", "不写板书设计与作业布置"),
         True, "教学设计题分值最大，模板化最有效"),
        ("教学评价与班级管理", (FORM_CHOICE, FORM_SHORT, FORM_CASE), "mid", 3.0, 10.0, (),
         ("形成性评价与总结性评价分不清", "突发事件处理的原则（教育性、客观性）说不出"),
         False, "选择 + 材料"),
        ("写作（议论文）", (FORM_WRITING,), "high", 4.0, 50.0, (),
         ("立意偏离教育主题", "只堆事例不分析", "字数不足 800 字直接降档"),
         True, "综合素质分值最大的一块"),
    )


def _rows_soft_exam() -> tuple:
    """软考（计算机技术与软件专业技术资格）。"""
    return (
        ("计算机组成与体系结构", (FORM_CHOICE,), "high", 2.5, 8.0, (),
         ("原码反码补码的表示范围", "Cache 与主存的映射方式", "流水线周期与吞吐率的计算"),
         False, "上午题必考"),
        ("操作系统原理", (FORM_CHOICE,), "mid", 3.0, 8.0, (),
         ("PV 操作与前趋图", "死锁预防与避免的区别", "页面置换算法的缺页次数"),
         False, "上午题"),
        ("数据结构与算法基础", (FORM_CHOICE, FORM_CASE), "high", 3.5, 10.0, (),
         ("时间复杂度估算", "哈夫曼编码与树的带权路径长度"),
         False, "上午 + 下午都会出现"),
        ("数据库与系统设计基础", (FORM_CHOICE, FORM_CASE), "high", 3.5, 10.0, (),
         ("ER 模型与关系模式的转换", "范式判断（尤其 BCNF）", "UML 图的种类与用途"),
         False, "下午案例常客"),
        ("软件工程与项目管理", (FORM_CHOICE, FORM_CASE), "high", 4.0, 16.0, (),
         ("开发模型（瀑布、迭代、敏捷）的适用场景", "CMMI 与软件过程的等级", "质量管理与配置管理的概念"),
         True, "上午题占比最大的一块"),
        ("网络与信息安全", (FORM_CHOICE, FORM_CASE), "mid", 3.5, 10.0, (),
         ("对称与非对称加密的用途", "防火墙与入侵检测的层次", "数字签名与数字证书的区别"),
         False, "记忆性较强"),
        ("知识产权与标准化", (FORM_CHOICE,), "low", 2.0, 6.0, (),
         ("著作权归属（职务作品的判定）", "专利与商业秘密的保护期限"),
         False, "送分题，务必拿满"),
        ("案例计算（关键路径、挣值分析）", (FORM_CASE, FORM_CALC), "high", 4.5, 18.0, ("软件工程与项目管理",),
         ("关键路径的总时差与自由时差算错", "挣值分析的 CV/SV/CPI/SPI 公式记混", "不做单位换算"),
         True, "下午案例必有一道计算题"),
        ("论文写作（高级资格）", (FORM_ESSAY,), "high", 4.5, 20.0, (),
         ("摘要写成了引言", "通篇理论没有项目实例与数据", "字数不足 2000 字直接不及格"),
         True, "高级资格的下午题，必须提前准备项目素材"),
    )


def _rows_cpa() -> tuple:
    """注册会计师专业阶段（六科通用考点骨架）。"""
    return (
        ("会计：金融资产与长期股权投资", (FORM_CHOICE, FORM_CALC, FORM_SOLVE), "high", 4.5, 18.0, (),
         ("成本法与权益法的转换不做追溯调整", "其他综合收益与公允价值变动损益的归类搞混",
          "处置时未把原计入其他综合收益的部分转出"),
         True, "会计第一大难点"),
        ("会计：收入、合并报表与所得税", (FORM_CHOICE, FORM_CALC, FORM_SOLVE), "high", 5.0, 20.0, (),
         ("五步法确认收入的履约义务拆分错", "合并报表抵销分录漏掉内部交易", "递延所得税资产的确认不看未来应纳税所得额"),
         True, "分值最高、最难的一块"),
        ("审计：风险评估与审计程序", (FORM_CHOICE, FORM_CASE, FORM_SOLVE), "high", 4.0, 16.0, (),
         ("控制测试与实质性程序的先后顺序", "函证与监盘的程序要求写不全", "审计意见类型的判断条件"),
         False, "综合题必考"),
        ("财管：资本成本、资本结构与企业估值", (FORM_CHOICE, FORM_CALC, FORM_SOLVE), "high", 4.5, 18.0, (),
         ("WACC 的权重用账面价值还是市场价值", "杠杆系数与每股收益无差别点的公式记错",
          "现金流折现的终值与现值期数搞反"),
         True, "计算量最大"),
        ("财管：本量利分析与营运资本管理", (FORM_CHOICE, FORM_CALC), "mid", 3.5, 12.0, (),
         ("边际贡献与毛利混淆", "保本点的安全边际率公式", "现金周转期的正负号"),
         False, "相对好拿分"),
        ("税法：增值税与企业所得税", (FORM_CHOICE, FORM_CALC, FORM_SOLVE), "high", 4.0, 18.0, (),
         ("视同销售的判定", "进项税额转出的时点", "企业所得税的纳税调整项目漏项"),
         True, "政策更新快，必须用当年税率"),
        ("经济法：公司法、证券法与合同法", (FORM_CHOICE, FORM_CASE), "high", 3.5, 14.0, (),
         ("公司法修订后的出资期限与股东权利", "要约与承诺的生效时点", "担保物权的优先顺位"),
         False, "记忆量大"),
        ("战略：战略分析、选择与风险管理", (FORM_CHOICE, FORM_CASE, FORM_SHORT), "mid", 3.0, 12.0, (),
         ("PEST 与波特五力的要素张冠李戴", "SWOT 的组合策略写反", "风险类型与应对策略对不上"),
         False, "六科里最容易突击"),
    )


def _rows_law_exam() -> tuple:
    """法律职业资格考试。"""
    return (
        ("民法：物权、合同与侵权", (FORM_CHOICE, FORM_CASE), "high", 4.5, 25.0, (),
         ("物权变动的登记要件与对抗要件不分", "合同效力与物权变动混淆", "善意取得的构成要件缺一不可"),
         True, "客观题与主观题分值都最高"),
        ("刑法：犯罪构成与刑罚", (FORM_CHOICE, FORM_CASE), "high", 4.5, 20.0, (),
         ("犯罪未遂与不能犯的区分", "共同犯罪的脱离与中止", "数罪并罚与想象竞合的处理规则"),
         True, "主观题第一道常考"),
        ("民事诉讼法与仲裁", (FORM_CHOICE, FORM_CASE), "high", 4.0, 16.0, (),
         ("管辖的级别与地域判断顺序错", "举证责任倒置的适用情形", "再审与上诉的区别"),
         False, "程序性强，套路固定"),
        ("刑事诉讼法", (FORM_CHOICE, FORM_CASE), "high", 4.0, 16.0, (),
         ("强制措施的期限记混", "非法证据排除的适用范围", "二审上诉不加刑的例外"),
         False, "细节多，靠反复刷题"),
        ("行政法与行政诉讼法", (FORM_CHOICE, FORM_CASE), "mid", 3.5, 14.0, (),
         ("行政行为的可诉性判断", "复议前置的情形", "举证责任由被告承担这一原则常被忘记"),
         False, "中等难度"),
        ("商法（公司法、破产、票据、保险）", (FORM_CHOICE, FORM_CASE), "mid", 3.5, 12.0, (),
         ("股东资格确认与股权让与担保", "破产债权的清偿顺序", "票据无因性的例外"),
         False, "与民法交叉多"),
        ("理论法（法治思想、宪法、法理学、司法制度）", (FORM_CHOICE, FORM_ESSAY), "high", 3.0, 16.0, (),
         ("论述题只写口号不结合法治思想的具体要求", "法的价值冲突的解决原则", "立法体制与备案审查"),
         True, "主观题论述必考，性价比极高"),
        ("三国法、经济法与环境资源法", (FORM_CHOICE,), "low", 2.5, 10.0, (),
         ("国际条约的适用与保留", "反垄断与不正当竞争的行为类型"),
         False, "小法，冲刺期背即可"),
    )


def _rows_ncse() -> tuple:
    """全国计算机等级考试（二级 / 三级）。"""
    return (
        ("公共基础：数据结构与算法", (FORM_CHOICE,), "high", 2.5, 10.0, (),
         ("栈与队列的操作特点", "二叉树结点数与深度的关系", "各类排序的时间复杂度"),
         False, "选择题必考 10 分"),
        ("公共基础：程序设计基础与软件工程", (FORM_CHOICE,), "mid", 2.0, 8.0, (),
         ("结构化程序设计的三种基本结构", "软件测试与调试的区别", "软件生命周期阶段"),
         False, "记忆性送分"),
        ("公共基础：数据库设计基础", (FORM_CHOICE,), "mid", 2.0, 8.0, (),
         ("三种关系运算（选择、投影、连接）", "E-R 图与关系模式"),
         False, "送分块"),
        ("科目本体知识（Python / C / Office 按科目）", (FORM_CHOICE, FORM_CODING), "high", 3.5, 24.0, (),
         ("Python 的缩进与可变默认参数", "C 语言的指针与数组关系", "Office 高级操作的功能区位置"),
         True, "因科目而异，占分最大"),
        ("上机操作：基本操作题", (FORM_CODING,), "high", 2.5, 18.0, (),
         ("不按题目要求的文件名与路径保存", "改完不运行验证", "填空题漏掉冒号或括号"),
         False, "最容易因为粗心丢分"),
        ("上机操作：综合应用题", (FORM_CODING,), "high", 4.0, 32.0, (),
         ("不会拆步骤，一上来就想写完整程序", "边界输入（空、极值）没有处理", "不会用调试与打印排查"),
         True, "决定能不能拿证的一块"),
    )


SUBJECT_ROWS: dict[tuple[str, str], tuple] = {
    ("math", "高中"): _rows_math_senior(),
    ("physics", "高中"): _rows_physics_senior(),
    ("chemistry", "高中"): _rows_chemistry_senior(),
    ("chinese", "高中"): _rows_chinese_senior(),
    ("english", "高中"): _rows_english_senior(),
    ("biology", "高中"): _rows_biology_senior(),
    ("english", "大学"): _rows_english_college(),
    ("math", "初中"): _rows_math_junior(),
    ("math", "大学"): _rows_math_college(),
    ("politics", "大学"): _rows_politics_college(),
    # ── 大学专业课 ────────────────────────────────────────────
    ("math_adv", "大学"): _rows_math_advanced(),
    ("linear", "大学"): _rows_linear_algebra(),
    ("prob", "大学"): _rows_probability(),
    ("cs", "大学"): _rows_cs(),
    ("physics", "大学"): _rows_physics_college(),
    ("economics", "大学"): _rows_economics(),
    # ── 证书类 ────────────────────────────────────────────────
    ("teacher", "大学"): _rows_teacher_cert(),
    ("soft", "大学"): _rows_soft_exam(),
    ("cpa", "大学"): _rows_cpa(),
    ("law", "大学"): _rows_law_exam(),
    ("ncse", "大学"): _rows_ncse(),
}


def _slug(name: str) -> str:
    return re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "_", name).strip("_")


def _build_points() -> dict[str, KnowledgePoint]:
    index: dict[str, KnowledgePoint] = {}
    for (subject, stage), rows in SUBJECT_ROWS.items():
        for row in rows:
            name = row[0]
            point_id = f"{subject}.{stage}.{_slug(name)}"
            index[point_id] = KnowledgePoint(
                id=point_id,
                subject=subject,
                stage=stage,
                name=name,
                forms=tuple(row[1]),
                frequency=row[2],
                difficulty=float(row[3]),
                weight=float(row[4]),
                prerequisites=tuple(row[5]) if len(row) > 5 else (),
                traps=tuple(row[6]) if len(row) > 6 else (),
                big=bool(row[7]) if len(row) > 7 else False,
                note=row[8] if len(row) > 8 else "",
            )
    return index


POINTS: dict[str, KnowledgePoint] = _build_points()


def stage_for(exam_type: str) -> str:
    return STAGE_BY_EXAM.get(exam_type, "高中")


def points_for(subject: str, exam_type: str) -> list[KnowledgePoint]:
    stage = stage_for(exam_type)
    key = syllabus_key(subject)
    rows = [point for point in POINTS.values() if point.subject == key and point.stage == stage]
    if not rows:
        rows = [point for point in POINTS.values() if point.subject == key]
    return rows


def all_points(exam_type: str, subjects: Optional[list[str]] = None) -> list[KnowledgePoint]:
    rows = [point for point in POINTS.values() if point.stage == stage_for(exam_type)]
    if subjects:
        keys = {syllabus_key(item) for item in subjects}
        rows = [point for point in rows if point.subject in keys]
    return rows


def get_point(point_id: str) -> Optional[KnowledgePoint]:
    """按 id 精确取知识点（掌握度表里的键就是 id）。"""
    return POINTS.get(point_id)


def find_point(keyword: str, subject: str = "", exam_type: str = "gaokao") -> Optional[KnowledgePoint]:
    """按关键词模糊匹配知识点。"""
    keyword = (keyword or "").strip()
    if not keyword:
        return None
    candidates = points_for(subject, exam_type) if subject else all_points(exam_type)
    for point in candidates:
        if point.name == keyword:
            return point
    for point in candidates:
        if keyword in point.name or point.name in keyword:
            return point
    # 退化到全局搜索
    for point in POINTS.values():
        if keyword and (keyword in point.name or point.name in keyword):
            return point
    return None


def match_points(keyword: str, subject: str = "", exam_type: str = "gaokao", limit: int = 3) -> list[KnowledgePoint]:
    keyword = (keyword or "").strip()
    if not keyword:
        return []
    candidates = points_for(subject, exam_type) if subject else all_points(exam_type)
    scored: list[tuple[int, KnowledgePoint]] = []
    for point in candidates:
        score = 0
        if point.name == keyword:
            score = 100
        elif keyword in point.name:
            score = 80 - abs(len(point.name) - len(keyword))
        elif point.name in keyword:
            score = 70
        else:
            hits = sum(1 for token in keyword if token and token in point.name)
            if hits:
                score = 20 + hits
        if score:
            scored.append((score, point))
    scored.sort(key=lambda item: (-item[0], item[1].name))
    return [point for _, point in scored[:limit]]


def allowed_forms(point: KnowledgePoint) -> tuple[str, ...]:
    return point.forms


def validate_form(point: KnowledgePoint, form: str) -> tuple[bool, str]:
    """出题前校验：这个知识点会不会以这种题型出现。"""
    if form in point.forms:
        return True, ""
    labels = "、".join(form_label(item) for item in point.forms)
    return False, (
        f"『{point.name}』在真题里只以 {labels} 的形式出现，不会出成{form_label(form)}。"
        f"请改用 {labels}，或换一个适合{form_label(form)}的知识点。"
    )


def big_points(subject: str, exam_type: str) -> list[KnowledgePoint]:
    return [point for point in points_for(subject, exam_type) if point.big]


def roi_ranking(
    exam_type: str,
    subjects: Optional[list[str]],
    mastery: dict[str, float],
    limit: int = 8,
) -> list[dict[str, Any]]:
    """提分性价比排序。

    分数 = (1 - 掌握度) × 分值权重 × 频率系数 × (1 + 压轴加成) ÷ (1 + 难度系数)
    含义：还差得多、分值大、考得勤、相对好下手的，先补它。
    """
    rows: list[tuple[float, KnowledgePoint]] = []
    for point in all_points(exam_type, subjects):
        level = float(mastery.get(point.id, 0.5))
        level = min(1.0, max(0.0, level))
        gap = 1.0 - level
        if gap <= 0.02:
            continue
        score = (
            gap
            * point.weight
            * FREQ_FACTOR.get(point.frequency, 1.0)
            * (1.2 if point.big else 1.0)
            / (1.0 + point.difficulty / 6.0)
        )
        rows.append((score, point))
    rows.sort(key=lambda item: -item[0])
    return [
        {
            "point": point.as_dict(),
            "roi": round(score, 2),
            "mastery": round(float(mastery.get(point.id, 0.5)), 2),
            "reason": _roi_reason(point, float(mastery.get(point.id, 0.5))),
        }
        for score, point in rows[:limit]
    ]


def _roi_reason(point: KnowledgePoint, mastery: float) -> str:
    if mastery < 0.35:
        level = "基本没掌握"
    elif mastery < 0.6:
        level = "掌握得比较浅"
    elif mastery < 0.8:
        level = "会一半、容易丢分"
    else:
        level = "比较稳，主要是保持手感"
    tail = "，且常年出大题" if point.big else "，主要出在小型题里"
    return f"{level}（掌握度约 {mastery:.0%}），分值权重 {point.weight:g}{tail}"


def format_point(point: KnowledgePoint) -> str:
    """知识点画像：给学习者看的版本（与 ``format_profile`` 不同，后者是给模型看的）。"""
    head = fmt.bold(point.name)
    head += f"　难度 {point.difficulty:g}/5　考频 {point.frequency}　权重 {point.weight:g}"
    rows = [fmt.kv("命题形式", "、".join(form_label(item) for item in point.forms))]
    if point.prerequisites:
        rows.append(fmt.kv("前置知识", "、".join(point.prerequisites)))
    if point.note:
        rows.append(fmt.kv("命题备注", point.note))
    blocks = [head, "\n".join(rows)]
    if point.big:
        blocks.append(fmt.note("这是压轴大题的常见载体。"))
    if point.traps:
        blocks.append(fmt.section("典型易错点", fmt.bullets(point.traps)))
    return fmt.join(*blocks)


def format_points(points: list[KnowledgePoint], limit: int = 12) -> str:
    return "\n\n".join(format_point(point) for point in points[:limit])
