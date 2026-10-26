"""★六维缺口 → 教训（把 `_deck_gap_vector` 接进反思上层）。

## 为什么需要这一层

玩家的口径（两段，必须合起来读）：

> 「这游戏需要确保有**短轴爆发**、**长轴成长**、**高质量 aoe 输出**、
>   **防御牌浓度**、**商店的选择**」
> 「教训是需要反思整个链路的，不能单纯的说"这局优先选防御/回血节点"，
>   需要**多维**，比如遇到群怪就死就反思**有没有拿 aoe 卡**，
>   遇到 boss 和精英就死需要反思**选卡的数值和强化卡选择**」

而改造前：

* `_deck_gap_vector`（`heuristic_planner.py:2992`）**早就实现了这六维**
  （output / defense / draw / energy / aoe / quality），量法只用 mod 已给的
  `deck[]` 字段，**不额外猜**。
* 但它的**唯一生产调用点**是商店评分（`heuristic_planner.py:2744`）
  ⇒ 六维只影响"买什么"，**从不影响"反思什么"**。
* 而 `_heuristic_lessons` 只读 `decision_memory` 的 reason 文本 + 死因。

⇒ 于是"死因"能定位到**类**（死在精英/群怪/被磨死），
  但说不出**牌组缺哪一维**。本模块补上这一段。

## 设计要点

1. **死因决定"该看哪一维"，缺口决定"缺多少"** —— 两者交叉，才是玩家要的多维反思。
   例：死在群怪 + `gap_aoe = 0.9` ⇒「群伤牌几乎没有」
       死在群怪 + `gap_aoe = 0.2` ⇒ 不缺群伤，那就别乱塞群伤牌（**不产出教训**）
2. **只在缺口显著时才说** —— 阈值下的维度保持沉默，避免"每局都说一遍所有维度"的噪声。
3. **不依赖 `heuristic_planner` 的实例方法**？—— 依赖。六维判据是**同一套**，
   另写一份必然漂移（那正是"量化"与"反思"分家的老毛病）。
   所以这里注入一个 planner 实例来算，而不是复制判据。

## 与商店评分的关系

同一份缺口向量现在有**两个消费者**：

    商店评分   → 决定"现在该不该买这一维"
    局末反思   → 决定"以后该重视哪一维"

这是刻意的：**决策与复盘共用同一把尺**，否则"买的时候按 A 算、复盘时按 B 说"
会得出互相矛盾的结论。
"""

from __future__ import annotations

from typing import Any

#: 维度 → 阈值。缺口高于它才值得说一句。
#:
#: 为什么不一刀切：六维的"可用范围"本来就不同 ——
#:   * `gap_aoe` 内建封顶 0.7（`heuristic_planner.py:3069`，规格说这情境是"缺口中"）
#:   * `gap_quality` 是 0.5*basic + 0.5*curses，实战里很少超过 0.5
#:   * `gap_output` / `gap_defense` 能达到 1.0（完全没有输出/格挡时）
#: ⇒ 用同一阈值会让低量程的维度**永远说不出话**。
DIM_THRESHOLDS: dict[str, float] = {
    "output": 0.45,
    "defense": 0.45,
    "draw": 0.50,
    "energy": 0.50,
    "aoe": 0.40,
    "quality": 0.30,
}

#: 维度 → 教训模板（第一人称口径，与 i18n A/B 层一致：猫娘自己在反思）。
#:
#: ⚠️ 这些话会进 `lessons`，再经 `_lesson_tokens` 抽 token 影响下一局决策。
#:    所以**必须**带上该维度的关键词（"群伤"/"格挡"/"升级"…），
#:    否则抽不出 token ⇒ 说了等于没说（这正是改造前三条死亡教训全是兜底句的原因）。
DIM_LESSON_TEMPLATES: dict[str, str] = {
    "output": "这局牌组的输出不够，下局我想早点补高伤的牌，别让节奏拖长",
    "defense": "这局格挡浓度太薄，下局我想多拿防御牌，别一路硬吃伤害",
    "draw": "这局牌组太胖又没什么抽牌，下局我想精简牌组或者拿抽牌手段",
    "energy": "这局费用曲线太顶，下局我想压低平均费用或者拿加能量的东西",
    # ⚠️ 「群伤」与「群怪」都写进模板 —— `_LESSON_KEYWORD_MAP` 收这两个词但不收大写 "AOE"
    #    （匹配是大小写敏感的，实测 `p._lesson_tokens(['xxxAOEyyy'])` 返回空）。
    #    模板必须用工整能抽出 token 的措辞，否则这条教训**说了等于没说**。
    "aoe": "这局几乎没有群伤牌，下局我想早点拿群伤，别被群怪多敌慢慢磨死",
    "quality": "这局牌组里基础牌和诅咒太多，下局我想早点升级关键牌、删掉废牌",
}

#: ★ 死因 → **优先反思的维度**（玩家原话直接映射）。
#:
#:   死在群怪 ⇒ 反思「有没有拿 aoe 卡」        ⇒ aoe
#:   死在精英 ⇒ 反思「选卡的数值」              ⇒ output
#:   死在 Boss ⇒ 反思「数值和强化卡选择」        ⇒ output + quality
#:   被慢慢磨死 ⇒ 反思「格挡浓度和回复」          ⇒ defense
#:   单房被打空 ⇒ 反思「短轴爆发」              ⇒ output
DEATH_KIND_TO_DIMS: dict[str, tuple[str, ...]] = {
    "aoe": ("aoe", "defense"),
    "elite": ("output", "quality"),
    "boss": ("output", "quality"),
    "attrition": ("defense", "quality"),
    "burst": ("output", "defense"),
}

#: 死因 → **该维度的收尾语气**（把"缺得厉害"和"只是偏低"区分开）。
SEVERE_MARK = "明显"
MILD_MARK = "有点"

#: ★ 各维度的**实际上限**（与 `_deck_gap_vector` 的实现对应）。
#:
#: 为什么必须知道上限：非点名维度用"越上限的比例"判"值得一提"，
#: 而 `gap_aoe` **内建封顶 0.7**（`heuristic_planner.py` 的 `0.7 * clamp01(...)`）。
#: 第一版用 `阈值 + 0.25` 当"极缺"线 ⇒ 对 aoe 是 0.65，**几乎每副牌都越得过** ——
#: 实测一副真实形态的成长期牌组 `gap_aoe = 0.70`，**恒定**触发。
#:
#: 后果：不论死因是什么，每条教训都夹一句"群伤也缺"，把死因信号**稀释**掉 ——
#: 那正是玩家抱怨的"反思不出来"。⇒ 改成**相对上限**判据（见 `NOTABLE_RATIO`）。
DIM_CEILINGS: dict[str, float] = {
    "output": 1.0,
    "defense": 1.0,
    "draw": 1.0,
    "energy": 1.0,
    "aoe": 0.7,     # ★ 内建封顶
    "quality": 1.0,
}

#: ★ 非点名维度要"值得一提"，缺口必须达到该维**上限**的这个比例。
#:
#: 实测（真实形态的成长期牌组）：
#:   `gap_aoe = 0.70 / 0.7 = 1.00` ⇒ 仍会触发（它确实极缺，该说）
#:   `gap_quality = 0.42 / 1.0 = 0.42` ⇒ **不再**在无关死因下乱说
NOTABLE_RATIO = 0.9

#: ★ 非点名维度**最多**带几条（防"顺带提一句"变成"顺带提一屏"）。
_NON_FOCUS_MAX_EXTRA = 1

#: ★ 总数上限：一局倒六个维度等于什么都没说。
_MAX_LESSONS = 3


def _fmt(gap: float) -> str:
    return f"{gap:.2f}"


def build_gap_lessons(
    deck: Any,
    *,
    planner: Any,
    floor: int = 0,
    current_hp: int = 0,
    max_hp: int = 0,
    death_kind: str = "",
    outcome: str = "",
) -> list[str]:
    """六维缺口 → 教训。

    ``planner`` 必须是 `STS2HeuristicPlanner` 实例（借用它的 `_deck_gap_vector`）。

    ★ 与死因**交叉**：死因规定了"该重点看哪几维"（`DEATH_KIND_TO_DIMS`），
      其它维度只有在缺口**顶到自身上限**（`NOTABLE_RATIO`）时才顺带提一句，
      且最多带 `_NON_FOCUS_MAX_EXTRA` 条 —— 避免一局倒出六个维度。

    ★ 没有牌组 / 读不出牌组 ⇒ **返回空**（不编造）。
      `_deck_gap_vector` 对空牌组返回全 0，而本函数对全 0 也不会产出教训，
      两条防线一致。

    ★ 排序：**点名维度优先**（先输出），非点名维度排在后面 ——
      这样 `[:_MAX_LESSONS]` 截断时优先保留"死因真正指向的那几维"。
      为什么需要：`dict` 顺序是 `DIM_LESSON_TEMPLATES` 的声明序，
      与死因无关 ⇒ 不排序的话，"群怪死"可能因为 aoe 声明靠后而被截掉。
    """
    cards = deck.get("cards") if isinstance(deck, dict) else deck
    if not isinstance(cards, list) or not cards:
        return []
    try:
        gaps = planner._deck_gap_vector(
            {"cards": cards},
            current_hp=int(current_hp or 0),
            max_hp=int(max_hp or 0),
            floor=int(floor or 0),
        )
    except Exception:
        # ★ 量化失败**不得**拖坏复盘 —— 宁可少一条教训。
        return []
    if not isinstance(gaps, dict):
        return []

    focus = DEATH_KIND_TO_DIMS.get(str(death_kind or "").lower(), ())
    focused: list[str] = []
    notable: list[str] = []
    for dim, template in DIM_LESSON_TEMPLATES.items():
        gap = gaps.get(dim)
        if not isinstance(gap, (int, float)):
            continue
        gap = float(gap)
        if dim in focus:
            # 死因点名的维度：过该维阈值就说
            if gap >= DIM_THRESHOLDS[dim]:
                mark = SEVERE_MARK if gap >= DIM_THRESHOLDS[dim] + 0.25 else MILD_MARK
                focused.append(f"{template}（{mark}缺，{_fmt(gap)}）")
        else:
            # 非点名维度：必须**顶到自己上限的 NOTABLE_RATIO**才顺带提一句。
            # ★ 用"上限比例"而不是"阈值 + 0.25"：见 `DIM_CEILINGS` 的注释 ——
            #   aoe 封顶 0.7，用绝对阈值会让它在几乎每局都触发，稀释死因信号。
            if gap >= DIM_CEILINGS[dim] * NOTABLE_RATIO:
                notable.append(f"{template}（也缺，{_fmt(gap)}）")

    # 点名维度排在前面 ⇒ 截断时不会把死因真正指向的维度砍掉。
    return (focused + notable[:_NON_FOCUS_MAX_EXTRA])[:_MAX_LESSONS]


#: ★ 维度 → 关键 token（供 `_lesson_tokens` 抽出，进而影响下一局决策）。
#:
#: 这些 token 必须与模板里的措辞**对应**，否则教训抽不出 token ⇒ 决策层收不到信号。
#: 实测锚点：`_LESSON_KEYWORD_MAP`（`heuristic_planner.py:354`）里
#:   attack→("输出","伤害","抢血","斩杀")、skill→("格挡","垫格挡","防御","保命","保血")。
DIM_TOKENS: dict[str, tuple[str, ...]] = {
    "output": ("输出", "高伤", "伤害"),
    "defense": ("格挡", "防御", "叠甲"),
    "draw": ("抽牌", "精简"),
    "energy": ("费用", "能量"),
    "aoe": ("群伤", "AOE", "群攻"),
    "quality": ("升级", "删牌", "废牌"),
}
