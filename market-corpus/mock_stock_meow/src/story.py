"""P12：剧情引擎（四幕叙事 + 外置 Yui 驱动 + 剧情数值效果）。

这一层**只管叙事**：加载剧情数据、判定什么时候该演哪一幕、把这一幕变成
**给外置猫娘 Yui 的提示词**、把选项变成**聊天卡片上的按钮**。

红线
----
* **引擎层、模式层零改动** —— 本文件不 import ``engine`` / ``modes`` 的任何
  规则，只读 ``snapshot`` 里插件层已经算好的数字。
* **500 局基线不受影响** —— 基线脚本 ``tests/simulate.py`` 只 import
  ``engine.*`` 与 ``modes.*``，**从不 import 插件层**，所以本文件再大也动不了它。
* **剧情数值效果全部在插件层**：本文件只声明"某个选项会产生什么效果"，
  真正改钱的是 ``__init__.py``（照抄 ``_take_loan`` 的写法：改内存 + 写库）。

三套文本（**别搞混**，P13 的纪律）
----------------------------------
* **处境**（``chapter["situation"]``）—— **客观事实**：发生了什么客观变化、
  玩家现在处于什么处境。**不许**写语气、不许写台词、不许写"你要提醒玩家
  什么"。这些是 P12 的旧病根 —— 把猫娘演成了复读机。
* **提示词**（``chapter_prompt`` / ``reply_prompt`` **现场拼装**）—— 处境 +
  客观数据 + 一句**开放式的请求**。宿主 ``push_message(ai_behavior="respond")``
  会把它塞进对话上下文并**立刻触发一个 AI 回合**，猫娘用**她自己的方式**回应。
  我们只负责**抛出事件的引子**，怎么说由她决定。
* **场景文本**（``chapter["scene"]``）—— 面板沉浸模式显示的**纯文本**
  （推送失败时的降级文案）。**不含任何猫娘立绘** —— 外置有 Yui 了，面板不画猫。

占位符纪律
----------
``{MASTER_NAME}`` / ``{LANLAN_NAME}`` 由宿主在 LLM 注入点展开（``str.replace``
语义，**不是** ``str.format``）。所以本模块**只做** ``str.replace`` 级别的
替换，**严禁** ``str.format`` —— 那会把 ``{MASTER_NAME}`` 炸成 ``KeyError``。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

#: 玩家称呼占位符（宿主按 session 展开 ``master_name``）。
MASTER = "{MASTER_NAME}"

#: 角色自称占位符（宿主按 session 展开 ``lanlan_name``）。
LANLAN = "{LANLAN_NAME}"


# ----------------------------------------------------------------------
# 节流常量
# ----------------------------------------------------------------------

#: 剧情推送的**全局冷却**（秒）。
#:
#: 剧情比日常搭话重 —— 它会打断玩家正在做的事。所以两次剧情推送之间至少隔
#: 这么久。**配合**宿主的 ``coalesce_key``（同章连发自动合并成最新的），
#: 双保险。
STORY_PUSH_COOLDOWN_SECONDS = 20.0

#: 剧情推送的**优先级**（宿主侧：数字越大越重要）。
#:
#: 宿主自己的档位：memo reminder = 8、bilibili 礼物/SC = 9；P11 的日常搭话用 2。
#: 剧情比日常搭话重要，但不该挤掉提醒类消息 ⇒ 取 **6**。
STORY_PUSH_PRIORITY = 6

#: 剧情提示词的 ``coalesce_key`` 前缀。
#:
#: 宿主 v2 契约：**同一个 key** 的排队提示在**两条投递路径上都合并成最新的** ⇒
#: 同一章反复触发只会留下一条，不会刷屏。
STORY_COALESCE_PREFIX = "mock_stock_meow.story"


# ----------------------------------------------------------------------
# 提示词模板（P13：只给事实，不给表演指令）
# ----------------------------------------------------------------------

#: 事件提示词的开头。
STORY_EVENT_HEADER = "《拟股喵喵》事件提示。"

#: 提示词的**结尾** —— 我们**唯一**允许对 Yui 说的话。
#:
#: 它只说明"发生了什么，你可以回应"，**不规定**她用什么语气、说什么内容、
#: 要不要安慰、要不要嘲讽。**怎么演是她的事**（P13 的核心）。
STORY_OPENING_ASK = "请结合当前的情景，自然地给予玩家回应。"

#: 处境缺失时的兜底（老数据文件可能还没写 ``situation``）。
STORY_FALLBACK_SITUATION = "玩家正在《拟股喵喵》里进行一局模拟交易，账户数据由系统实时结算。"


# ----------------------------------------------------------------------
# 加载
# ----------------------------------------------------------------------

#: 加载后的剧情数据缓存（``None`` = 还没加载过）。
_STORY_CACHE: dict[str, Any] | None = None

#: 剧情文件路径（插件根目录，与 ``community_roster.json`` 同一套读法）。
_STORY_PATH = Path(__file__).resolve().parent / "story.json"


def story_path() -> Path:
    """剧情数据文件路径（测试用）。"""
    return _STORY_PATH


def load_story() -> dict[str, Any]:
    """读 ``story.json``；**读不到就返回空剧情**（插件照常跑，只是没有剧情）。

    绝不抛异常 —— 剧情是"锦上添花"，一个坏掉的数据文件不该让插件启动即崩。
    """
    global _STORY_CACHE
    if _STORY_CACHE is not None:
        return _STORY_CACHE
    try:
        data = json.loads(_STORY_PATH.read_text(encoding="utf-8"))
    except Exception:  # pragma: no cover - 数据文件缺失/损坏不影响主流程
        _STORY_CACHE = {"chapters": []}
        return _STORY_CACHE
    if not isinstance(data, dict):
        _STORY_CACHE = {"chapters": []}
        return _STORY_CACHE
    chapters = data.get("chapters")
    if not isinstance(chapters, list):
        chapters = []
    clean: list[dict[str, Any]] = []
    for item in chapters:
        if not isinstance(item, dict):
            continue
        chapter_id = str(item.get("id") or "").strip()
        if not chapter_id:
            continue
        item["id"] = chapter_id
        item["choices"] = [
            choice
            for choice in (item.get("choices") or [])
            if isinstance(choice, dict) and str(choice.get("id") or "").strip()
        ]
        clean.append(item)
    clean.sort(key=lambda item: int(item.get("order") or 0))
    _STORY_CACHE = {"chapters": clean, "version": data.get("version", 1)}
    return _STORY_CACHE


def reset_cache() -> None:
    """清掉缓存（测试用）。"""
    global _STORY_CACHE
    _STORY_CACHE = None


# ----------------------------------------------------------------------
# 取用
# ----------------------------------------------------------------------


def chapters() -> list[dict[str, Any]]:
    """所有章节（按 ``order`` 升序）。"""
    return list(load_story().get("chapters") or [])


def chapter_by_id(chapter_id: str) -> dict[str, Any] | None:
    """按 id 取章节；找不到返回 ``None``。"""
    key = str(chapter_id or "").strip()
    for chapter in chapters():
        if chapter.get("id") == key:
            return chapter
    return None


def choice_by_id(chapter: dict[str, Any] | None, choice_id: str) -> dict[str, Any] | None:
    """按 id 取选项；找不到返回 ``None``。"""
    if not chapter:
        return None
    key = str(choice_id or "").strip()
    for choice in chapter.get("choices") or []:
        if choice.get("id") == key:
            return choice
    return None


def effect_of(choice: dict[str, Any] | None) -> dict[str, Any]:
    """取选项的数值效果描述（读不到就是"无效果"）。"""
    if not choice:
        return {"type": "none"}
    effect = choice.get("effect")
    if not isinstance(effect, dict):
        return {"type": "none"}
    return effect


def effect_tag(choice: dict[str, Any] | None) -> str:
    """取效果的"埋点标签"（沉没成本 / 不甘心等），给后续章节的提示词用。"""
    effect = effect_of(choice)
    tag = str(effect.get("tag") or "").strip()
    if tag:
        return tag
    if choice:
        return str(choice.get("id") or "").strip()
    return ""


# ----------------------------------------------------------------------
# 触发判定
# ----------------------------------------------------------------------


def trigger_matches(trigger: dict[str, Any] | None, snapshot: dict[str, Any]) -> bool:
    """这一章的触发条件**现在**满足吗？

    判定用的全是**插件层已经算好的数字**（``snapshot``），引擎层 / 模式层
    一行都不碰。

    两种触发类型：

    * ``join`` —— 开局就该演（第一章）；
    * ``watch`` —— **持续观察**型：心跳每次判定都看一眼，条件一旦满足就演
      （后面三章都是它 —— "断供"不是一个时刻，是一段状态）。
    """
    if not isinstance(trigger, dict):
        return False
    kind = str(trigger.get("type") or "").strip()

    if kind == "join":
        return True

    if kind != "watch":
        return False

    loss_ratio = float(snapshot.get("loss_ratio") or 0.0)
    min_loss = float(trigger.get("min_loss_ratio") or 0.0)
    if min_loss > 0.0 and loss_ratio >= min_loss:
        return True

    strikes = int(snapshot.get("strikes") or 0)
    need_strikes = int(trigger.get("or_strikes") or 0)
    if need_strikes > 0 and strikes >= need_strikes:
        return True

    trades = int(snapshot.get("trade_count") or 0)
    need_trades = int(trigger.get("or_trade_count") or 0)
    if need_trades > 0 and trades >= need_trades:
        return True

    if bool(trigger.get("or_cash_below_bill")):
        cash = float(snapshot.get("cash") or 0.0)
        bill = float(snapshot.get("next_bill") or 0.0)
        if bill > 0.0 and cash < bill:
            return True

    return False


def next_chapter(
    snapshot: dict[str, Any],
    seen: list[str] | set[str] | tuple[str, ...],
    pending: str = "",
) -> dict[str, Any] | None:
    """下一幕该演什么？（``None`` = 现在没戏）

    规则：

    * 章节按 ``order`` 顺序推进；
    * ``requires`` 里的前置章节必须**都演过**（四幕是递进的，不许跳）；
    * 演过的不再演；
    * 有一幕**待选择**时不再开新的一幕（先让玩家把上一幕选完）。
    """
    if pending:
        return None
    seen_set = {str(item) for item in seen}
    for chapter in chapters():
        chapter_id = str(chapter.get("id") or "")
        if not chapter_id or chapter_id in seen_set:
            continue
        requires = [str(item) for item in (chapter.get("requires") or [])]
        if any(item not in seen_set for item in requires):
            continue
        if trigger_matches(chapter.get("trigger"), snapshot):
            return chapter
    return None


# ----------------------------------------------------------------------
# 文本
# ----------------------------------------------------------------------


def _substitute(text: str, snapshot: dict[str, Any]) -> str:
    """替换本模块自己的占位符（**``str.replace`` 语义，不是 format**）。

    ``{MASTER_NAME}`` / ``{LANLAN_NAME}`` **原样留下** —— 那是宿主展开的。
    """
    out = str(text or "")
    replacements = {
        "{DAY}": str(int(snapshot.get("day") or 0)),
        "{CASH}": _round(snapshot.get("cash")),
        "{BILL}": _round(snapshot.get("next_bill")),
        "{LOSS}": f"{float(snapshot.get('loss_ratio') or 0.0):.0%}",
        "{TRADES}": str(int(snapshot.get("trade_count") or 0)),
        "{TOTAL}": _round(snapshot.get("total_assets")),
        "{INITIAL}": _round(snapshot.get("initial_cash")),
    }
    for key, value in replacements.items():
        out = out.replace(key, value)
    return out


def _round(value: Any) -> str:
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return "0.00"


def chapter_situation(chapter: dict[str, Any]) -> str:
    """这一幕的**客观处境**（客观事实，**不含任何表演指令**）。

    读不到（老数据文件）就用 :data:`STORY_FALLBACK_SITUATION` 兜底 ——
    宁可少说一句，也不能退回"请用 XX 语气说"那种指令。
    """
    text = str((chapter or {}).get("situation") or "").strip()
    return text or STORY_FALLBACK_SITUATION


def _facts_line(snapshot: dict[str, Any]) -> str:
    """**客观数据行** —— 只报数字，不解释、不建议、不下判断。"""
    return (
        f"客观数据：第 {int(snapshot.get('day') or 0)} 天；"
        f"可动用现金 {_round(snapshot.get('cash'))}；"
        f"初始资金 {_round(snapshot.get('initial_cash'))}；"
        f"账户总资产 {_round(snapshot.get('total_assets'))}；"
        f"累计亏损 {float(snapshot.get('loss_ratio') or 0.0):.0%}；"
        f"成交 {int(snapshot.get('trade_count') or 0)} 笔；"
        f"下一笔生活费 {_round(snapshot.get('next_bill'))}。"
    )


def _effect_detail(granted: float, debt_amount: float, due_days: int) -> str:
    """这次选择**实际**产生的数值变化（只报事实）。"""
    parts: list[str] = []
    if granted > 0.0:
        parts.append(f"现金 +{float(granted):.2f}")
    if debt_amount > 0.0:
        parts.append(
            f"新增一笔 {float(debt_amount):.2f} 的借款，{int(due_days)} 天后到期，需偿还 {float(debt_amount):.2f}"
        )
    if not parts:
        parts.append("本次选择没有产生任何数值变化")
    return "；".join(parts)


def chapter_prompt(chapter: dict[str, Any], snapshot: dict[str, Any]) -> str:
    """这一幕给外置 Yui 的**提示词**：处境 + 客观数据 + 一句开放式请求。

    **P13 红线**：这里拼出来的每一个字都必须是"事实"或"开放的请求"。
    任何"请用 XX 语气""提醒他 YY""别把玩家 ZZ"之类的**表演指令**都不许有
    —— 猫娘怎么说，是**她**的事；我们只负责抛出事件的引子。
    """
    chapter = chapter or {}
    lines = [
        STORY_EVENT_HEADER,
        f"当前剧情节点：{chapter_title(chapter)}（节点 id：{str(chapter.get('id') or '')}）。",
        f"玩家：{MASTER}。",
        f"当前处境：{chapter_situation(chapter)}",
        _facts_line(snapshot),
        STORY_OPENING_ASK,
    ]
    return _substitute("\n".join(lines), snapshot)


def reply_prompt(
    chapter: dict[str, Any] | None,
    choice: dict[str, Any] | None,
    snapshot: dict[str, Any],
    granted: float = 0.0,
    debt_amount: float = 0.0,
    due_days: int = 0,
) -> str:
    """玩家选完之后的**回应提示词**（同样只给事实，不给表演指令）。"""
    chapter = chapter or {}
    lines = [
        STORY_EVENT_HEADER,
        f"当前剧情节点：{chapter_title(chapter)}（节点 id：{str(chapter.get('id') or '')}）。",
        f"玩家：{MASTER}。",
        f"玩家刚刚做出的选择：{choice_label(choice or {})}（{choice_hint(choice or {})}）。",
        f"这次选择产生的实际变化：{_effect_detail(granted, debt_amount, due_days)}",
        f"当前处境：{chapter_situation(chapter)}",
        _facts_line(snapshot),
        STORY_OPENING_ASK,
    ]
    return _substitute("\n".join(lines), snapshot)


def chapter_scene(chapter: dict[str, Any], snapshot: dict[str, Any]) -> str:
    """这一幕在面板沉浸模式里显示的**纯文本场景**。"""
    return _substitute(str((chapter or {}).get("scene") or ""), snapshot)


def chapter_title(chapter: dict[str, Any]) -> str:
    """幕次标题。"""
    return str((chapter or {}).get("title") or "")


def choice_label(choice: dict[str, Any]) -> str:
    """选项按钮文案。"""
    return str((choice or {}).get("label") or "")


def choice_hint(choice: dict[str, Any]) -> str:
    """选项按钮下方的小字（沉没成本 / 不甘心就写在这里）。"""
    return str((choice or {}).get("hint") or "")


# ----------------------------------------------------------------------
# 聊天卡片（选项按钮就挂在 Yui 气泡旁边）
# ----------------------------------------------------------------------

#: 剧情卡片的样式（交给宿主的 ``create_card(css=...)``）。
STORY_CARD_CSS = (
    ".jmm-story{font-size:13px;line-height:1.6}"
    ".jmm-story .jmm-title{font-weight:700;margin-bottom:4px}"
    ".jmm-story .jmm-opt{display:block;width:100%;text-align:left;"
    "margin:6px 0 0;padding:8px 10px;cursor:pointer;border-radius:8px;"
    "border:1px solid rgba(120,130,145,.28);background:rgba(120,130,145,.06)}"
    ".jmm-story .jmm-opt b{font-weight:600}"
    ".jmm-story .jmm-hint{display:block;font-size:12px;color:#7a8494;margin-top:2px}"
)


def _escape(text: str) -> str:
    """HTML 转义（卡片内容是插件自己写的，但依然转义 —— 习惯要养成）。"""
    return (
        str(text or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def chapter_card(chapter: dict[str, Any], snapshot: dict[str, Any]) -> dict[str, Any]:
    """这一幕的**聊天卡片**（``html`` + ``actions``）。

    选项按钮的 ``data-neko-action`` 就是选项 id；宿主前端
    （``react-neko-chat/src/HtmlCardBlock.tsx``）点击后 POST 回插件的
    ``story_choice`` 入口。
    """
    chapter = chapter or {}
    chapter_id = str(chapter.get("id") or "")
    blocks: list[str] = []
    actions: dict[str, dict[str, Any]] = {}
    for choice in chapter.get("choices") or []:
        choice_id = str(choice.get("id") or "")
        if not choice_id:
            continue
        label = _escape(choice_label(choice))
        hint = _escape(choice_hint(choice))
        blocks.append(
            f'<button class="jmm-opt" data-neko-action="{_escape(choice_id)}">'
            f"<b>{label}</b>"
            + (f'<span class="jmm-hint">{hint}</span>' if hint else "")
            + "</button>"
        )
        actions[choice_id] = {
            "entry": "story_choice",
            "args": {"chapter_id": chapter_id, "choice_id": choice_id},
            "label": choice_label(choice),
        }
    html = (
        '<div class="jmm-story">'
        f'<div class="jmm-title">{_escape(chapter_title(chapter))}</div>'
        + "".join(blocks)
        + "</div>"
    )
    summary = _substitute(str(chapter.get("scene") or ""), snapshot)
    return {"html": html, "summary": summary, "actions": actions, "css": STORY_CARD_CSS}


def coalesce_key(chapter_id: str) -> str:
    """这一幕的 ``coalesce_key``（同章连发自动合并成最新的）。"""
    return f"{STORY_COALESCE_PREFIX}.{chapter_id}"
