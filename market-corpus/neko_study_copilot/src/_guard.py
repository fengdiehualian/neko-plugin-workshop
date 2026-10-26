"""事实纪律：别把「常见情况」当成「你就是这样」。

真实事故：用户只丢来一句「我草泥马」，疏导输出里却出现了

    85 天、掌握度五成、身边还飘着「某某考了三次都没过」的声音

三件事全是编的：天数是按常规考期估算的，「掌握度五成」是**没有作答记录时的默认值**，
「考了三次都没过」是我们压力源模板里的一句通用描述。模型没错——它就是照着我们喂的
「已知情况」在讲；错在我们把**估算值**和**通用规律**混进了事实里，还顺手给了它一份
写好的三段式答案，于是无论用户说什么，出来都是同一套。

所以这里把纪律固化成提示词段，凡是会生成自然语言的地方都带上：

- :data:`FACT_RULES` —— 通用事实纪律（能说什么、不能说什么）
- :func:`facts_block` —— 「已知事实」清单（带来源）
- :func:`unknowns_block` —— 「不知道的事」清单，明确禁止推测成具体数字
- :func:`options_block` —— 「可选素材」：素材是参考，不是必须照抄的台词
- :func:`reply_first` —— 要求先回应对方刚说的那句话，再谈别的
"""

from __future__ import annotations

from typing import Iterable

FACT_RULES = (
    "【事实纪律】（违反这条，回答就作废）\n"
    "1. 「已知事实」里没写的信息，一个字都不要编——尤其是别人对他说过什么、\n"
    "   他考过几次、考了多少分、他家里人/老师/同学什么态度，这些你没资格替他假设；\n"
    "2. 标着「未知 / 未填写 / 估算」的，不许当成事实讲，也不许换算成具体数字或百分比；\n"
    "3. 「常见情况」只用在你心里，用来理解他；**不许写成他的经历**。\n"
    "   错误示范：「身边还飘着某某考了三次都没过的声音」——他从没这么说过；\n"
    "4. 真的需要这些信息时，问他一句，不要替他回答；\n"
    "5. 推断要显式标注（「我猜…」「如果我没理解错…」），不要和事实混在同一句话里；\n"
    "6. 不确定就说不确定。宁可少说，也不要编。"
)

_LAYOUT_RULE = (
    "【排版】用 markdown 小块：`##` 小节、`- ` 列表、`> ` 提示条。"
    "不要堆成一大段，也不要每句都换行。"
)


def facts_block(items: Iterable[tuple[str, str]]) -> str:
    """已知事实清单。``items`` 是 (来源, 事实) 二元组，来源让人能核对。"""
    rows = [f"- [{source}] {text}" for source, text in items if str(text).strip()]
    if not rows:
        return "## 已知事实\n- （无：档案基本是空的，请更多地问，而不是更多地讲）"
    return "## 已知事实\n" + "\n".join(rows)


def unknowns_block(items: Iterable[str]) -> str:
    """明确「不知道」的东西，防止模型拿默认值当真。"""
    rows = [f"- {text}" for text in items if str(text).strip()]
    if not rows:
        return ""
    return (
        "## 不知道的事（禁止当成事实、禁止换算成数字）\n"
        + "\n".join(rows)
        + "\n需要时直接问他一句。"
    )


def options_block(title: str, items: Iterable[str]) -> str:
    """可选素材：明确它是参考，不是必须念的台词。"""
    rows = [f"- {text}" for text in items if str(text).strip()]
    if not rows:
        return ""
    return (
        f"## {title}（**可选素材，不是给你的台词**）\n"
        + "\n".join(rows)
        + "\n只在贴合他这次的处境时才用其中一条；不贴合就全部丢掉，说你自己想的。"
        "**严禁**把素材按顺序复述一遍——那就是模板。"
    )


def reply_first(quote: str, *, what_it_is: str = "情绪") -> str:
    """先回应原话，再谈别的。信息太少时先问一句，而不是长篇开讲。"""
    quoted = (quote or "").strip()[:200]
    return (
        "## 必须先做的事\n"
        f"他这次说的是：{quoted or '（空）'}\n"
        f"1. 第一句话就要**直接回应这句话本身**：接住他的{what_it_is}，别绕开、别装没看见；\n"
        "2. 如果他只给了很短一句（比如一句抱怨、一个脏字、一个「嗯」），"
        "**不要长篇开讲**：先接一句，然后问他一个具体问题；\n"
        "3. 结尾不要写成通用总结；能落到「现在这一下做什么」就落，落不了就直接问。"
    )


def layout_rule() -> str:
    return _LAYOUT_RULE
