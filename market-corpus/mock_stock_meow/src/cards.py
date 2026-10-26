"""P5 卡片体系（8 种）。

架构定位
--------
本模块属于**第一层：N.E.K.O. 插件层**，铁律是「只做翻译」：

* **只产数据**：每个构造器返回 :class:`Card`（纯文本 + 一段 HTML + 结构化 payload）。
* **不投递**：投递由 ``__init__.py`` 做（``await create_card(self.ctx, ...)``）。
  本模块不碰 ``self.ctx``，也不 import 任何 ``plugin.*`` —— 与 P4 的模式层
  铁律（``modes/`` 不 import 插件层）保持同一种干净。

局中隐藏铁律（P4 §14）在这里**同样成立**
------------------------------------------
卡片正文、HTML、``summary``、``payload`` 一律不得出现：

``heat`` / ``heat_curve`` / ``seed`` / ``reversal_days`` / ``accuracy`` /
``prey_index`` 及其中文同义词（热度 / 热度值 / 种子 / 反转日清单 / 准确率 / 真实度）。

``tests/test_cards.py`` 会对全部 8 种卡片的渲染结果做词族扫描，出现任一禁词即 FAIL。

文案来源
--------
卡片标题 / 按钮文案 / 猫娘嘲讽文案统一放在 ``i18n/zh-CN.json``（``card.*`` 与
``taunt.*`` 命名空间），本模块只做**读取与拼装**，保证"一份文案、一处维护"。
"""

from __future__ import annotations

import html
import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

__all__ = [
    "ACHIEVEMENTS",
    "CARD_CSS",
    "CARD_KINDS",
    "DEFAULT_IDENTITY",
    "DEATH_REASON_LABELS",
    "EPITAPHS",
    "IDENTITIES",
    "TAUNT_TRIGGERS",
    "Card",
    "achievement_by_key",
    "achievement_by_title",
    "build_achievement_card",
    "build_bill_card",
    "build_catgirl_daily_card",
    "build_harvest_card",
    "build_kill_card",
    "build_news_flash_card",
    "build_pawnbroker_card",
    "build_taunt_card",
    "build_welfare_card",
    "death_reason_label",
    "identity_echo",
    "identity_intro",
    "identity_label",
    "pick_catgirl_silence",
    "pick_taunt",
    "t",
    "taunt_variants",
]

#: 8 种卡片的 kind。
CARD_KINDS: tuple[str, ...] = (
    "harvest",
    "welfare",
    "bill",
    "kill",
    "catgirl_daily",
    "taunt",
    "news_flash",
    "pawnbroker",
    # ── P5.5 改动 6：称号解锁卡（第 9 种）────────────────────────
    "achievement",
)

_I18N_PATH = Path(__file__).resolve().parent / "i18n" / "zh-CN.json"


# ---------------------------------------------------------------------------
# 文案
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def _i18n() -> dict[str, str]:
    """读取 ``i18n/zh-CN.json``（扁平 key → 文案）。读不到就退化成空表。"""
    try:
        raw = json.loads(_I18N_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):  # pragma: no cover - 打包损坏时的兜底
        return {}
    if not isinstance(raw, dict):
        return {}
    return {str(key): str(value) for key, value in raw.items() if isinstance(value, str)}


#: 区分"没给默认值"和"给了空默认值"（空串是**合法**的默认值）。
_MISSING = object()


def t(key: str, default: Any = _MISSING) -> str:
    """取一条文案。

    * key 存在 → 返回文案；
    * key 不存在且给了 ``default`` → 返回 ``default``（**空串也算给了**）；
    * key 不存在且没给 ``default`` → 返回 key 本身（便于排查缺文案）。
    """
    value = _i18n().get(key)
    if value is not None:
        return value
    if default is _MISSING:
        return key
    return str(default)


#: 猫娘嘲讽的 5 个触发点。
TAUNT_TRIGGERS: tuple[str, ...] = (
    "heavy_delisted",
    "cash_kill",
    "add_down",
    "catgirl_dead",
    "harvest",
)

# ---------------------------------------------------------------------------
# 入场身份（P5.5 · 改动 2）
# ---------------------------------------------------------------------------

#: 三个入场身份。
IDENTITIES: tuple[str, ...] = ("debt", "prove", "quick")

#: 默认身份：**不想打工**的那一类（也是最好收割的那一类）。
DEFAULT_IDENTITY = "quick"

#: 猫娘沉默旁白的出现概率（P5.5 · 改动 3）。
SILENCE_PROBABILITY = 0.3

# ---------------------------------------------------------------------------
# 破产叙事与称号图鉴（P5.5 · 改动 4 / 改动 5）
# ---------------------------------------------------------------------------

#: 破产原因 → 结算页的一句叙事。
#:
#: **这是插件层的映射表** —— 引擎的 ``DEATH_TITLES`` 一行不动（P5.5 总约束），
#: 引擎那一份是"机制说明"，这一份是"故事"。
EPITAPHS: dict[str, str] = {
    "starved": "你破产了。穷困潦倒。",
    "liquidated": "你破产了。资产归零。",
    "loan_default": "你彻底破产了，抵押了所有能抵押的东西。为了还债，你被迫签下了终身给猫娘打工的契约，每天负责端茶倒水、买小鱼干。",
    "welfare_neglect": "你连续七天没去领工资。",
    "harvested": "你破产了。被精准收割的那一刻。",
    "inaction": "你活到了最后，因为你什么都没做。",
}

#: 破产原因 → 中文短标签（改动 4 的第三屏要用）。
DEATH_REASON_LABELS: dict[str, str] = {
    "starved": "穷困潦倒",
    "liquidated": "资产归零",
    "loan_default": "贷款违约",
    "welfare_neglect": "连续七天没领工资",
    "harvested": "被精准收割",
    "inaction": "无为而治",
}

#: 称号图鉴目录（P5.5 · 改动 5 / 改动 7）。
#:
#: * ``key`` —— 稳定标识（落库用）；
#: * ``title`` —— 称号名；
#: * ``hint`` —— 未解锁时显示的提示（**「无为而治」的 hint 是唯一带「活」字的**，
#:   这是玩家发现隐藏结局的线索）；
#: * ``source`` —— 解锁来源（破产原因枚举 / 特殊条件 / 空 = 不可解锁的留白）。
ACHIEVEMENTS: tuple[dict[str, Any], ...] = (
    {
        "key": "grain",
        "title": "颗粒无收",
        "hint": "你破产了。穷困潦倒。",
        "source": "starved",
    },
    {
        "key": "zero",
        "title": "满仓归零",
        "hint": "你破产了。资产归零。",
        "source": "liquidated",
    },
    {
        "key": "debt",
        "title": "债台高筑",
        "hint": "你彻底破产了，抵押了所有能抵押的东西。为了还债，你被迫签下了终身给猫娘打工的契约，每天负责端茶倒水、买小鱼干。",
        "source": "loan_default",
    },
    {
        "key": "unpaid",
        "title": "七天不领",
        "hint": "你连续七天没去领工资。",
        "source": "welfare_neglect",
    },
    {
        "key": "sheared",
        "title": "精准收割",
        "hint": "你破产了。被精准收割的那一刻。",
        "source": "harvested",
    },
    {
        "key": "inaction",
        "title": "无为而治",
        "hint": "你活到了最后，因为你什么都没做。",
        "source": "inaction",
    },
    {
        "key": "broke",
        "title": "一贫如洗",
        "hint": "你走的时候，账上什么都没有。",
        "source": "broke",
    },
    {
        "key": "unknown",
        "title": "？？？",
        "hint": "？？？",
        "source": "",
    },
)

#: 成就解锁的**通知文案**（改动 6：第 14 天解锁「无为而治」时给玩家一句）。
ACHIEVEMENT_UNLOCK_NOTE = "你活到了第 {days} 天。你什么都没做。称号解锁：{title}。"


def death_reason_label(reason: str | None) -> str:
    """破产原因的中文短标签（改动 4 的第三屏用）。"""
    if not reason:
        return t("death_reason.unknown", "出局")
    return t(f"death_reason.{reason}", DEATH_REASON_LABELS.get(reason, "出局"))


def achievement_by_source(source: str) -> dict[str, Any] | None:
    """按解锁来源找称号条目。"""
    for item in ACHIEVEMENTS:
        if item["source"] == source:
            return dict(item)
    return None


def achievement_by_key(key: str) -> dict[str, Any] | None:
    """按稳定标识找称号条目。"""
    for item in ACHIEVEMENTS:
        if item["key"] == key:
            return dict(item)
    return None


def achievement_by_title(title: str) -> dict[str, Any] | None:
    """按称号名找条目（落库用的是称号名）。"""
    for item in ACHIEVEMENTS:
        if item["title"] == title:
            return dict(item)
    return None


def identity_label(identity: str) -> str:
    """身份的短标签（面板 / 返回体用）。"""
    if identity not in IDENTITIES:
        identity = DEFAULT_IDENTITY
    return t(f"identity.label.{identity}", "不想打工的")


def identity_intro(identity: str) -> str:
    """入场台词（join 的返回体里给一句）。"""
    if identity not in IDENTITIES:
        identity = DEFAULT_IDENTITY
    return t(f"identity.intro.{identity}", "打工太慢了。我想快点赚到钱。")


def identity_echo(identity: str) -> str:
    """身份呼应句（**只在破产结算页**出现）。"""
    if identity not in IDENTITIES:
        identity = DEFAULT_IDENTITY
    return t(
        f"identity.echo.{identity}",
        "你入场时说想快点赚到钱。你确实很快——快到连饭都忘了吃。",
    )


def pick_catgirl_silence(
    seed: int, probability: float = SILENCE_PROBABILITY
) -> bool:
    """这一局要不要出现"猫娘沉默"旁白（P5.5 · 改动 3）。

    **按 seed 稳定** —— 与猫娘嘲讽同一套机制：同一个 seed 必然给出同一个答案
    （重开一局、重启插件都一样）。
    """
    buckets = 100
    threshold = max(0, min(buckets, int(round(probability * buckets))))
    return _stable_index(seed, "catgirl_silence", buckets) < threshold


def _fill(text: str, params: dict[str, Any] | None) -> str:
    """填变量：``{name}`` → 值。"""
    if not params:
        return text
    for key, value in params.items():
        text = text.replace("{" + str(key) + "}", str(value))
    return text


def taunt_variants(trigger: str) -> list[str]:
    """某个触发点的全部文案变体（读 ``taunt.<trigger>.<n>``）。"""
    variants: list[str] = []
    for index in range(1, 64):
        value = t(f"taunt.{trigger}.{index}", "")
        if not value:
            break
        variants.append(value)
    return variants


def _stable_index(seed: int, trigger: str, count: int) -> int:
    """**稳定**的下标。

    刻意**不用** ``hash()`` —— 它的随机化种子是**每进程**的，同一个 seed 在
    不同进程里会选到不同变体（同 seed 必出同一句是硬要求）。
    """
    accumulator = int(seed) & 0xFFFFFFFF
    for char in trigger:
        accumulator = (accumulator * 131 + ord(char)) & 0xFFFFFFFF
    return accumulator % max(1, count)


def pick_taunt(
    trigger: str, seed: int, params: dict[str, Any] | None = None
) -> str:
    """按 seed **稳定**地挑一句猫娘嘲讽，并填上变量。

    没有对应文案时返回空串（调用方据此跳过推送）。
    """
    variants = taunt_variants(trigger)
    if not variants:
        return ""
    index = _stable_index(seed, trigger, len(variants))
    return _fill(variants[index], params)


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Card:
    """一张待投递的卡片。

    ``html`` 是渲染体，``summary`` 是**纯文本**折叠摘要（卡片不渲染时的降级
    文本，必须是人话），``body`` 是纯文本正文，``payload`` 是结构化数据
    （**不进 HTML**，供插件层与测试使用）。
    """

    kind: str
    title: str
    summary: str
    body: str
    html: str
    actions: dict[str, dict[str, Any]] = field(default_factory=dict)
    payload: dict[str, Any] = field(default_factory=dict)

    def action_spec(self) -> dict[str, dict[str, Any]]:
        """交给宿主的按钮表（``entry`` + ``args``）。"""
        return {name: dict(action) for name, action in self.actions.items()}


#: 卡片通用样式（交给 ``create_card(css=...)``）。
CARD_CSS = (
    ".jmm-card{font-size:13px;line-height:1.6}"
    ".jmm-card b{font-weight:600}"
    ".jmm-card .jmm-kv{color:#7a8494}"
    ".jmm-card .jmm-warn{color:#c2410c}"
    ".jmm-card .jmm-bad{color:#b91c1c}"
    ".jmm-card .jmm-good{color:#15803d}"
    ".jmm-card .jmm-echo{margin:6px 0 0;color:#4b5563;font-style:italic}"
    ".jmm-card .jmm-silence{margin:6px 0 0;color:#6b7280}"
    ".jmm-card button{margin:4px 8px 0 0;padding:4px 10px;cursor:pointer}"
)


def _esc(value: Any) -> str:
    """HTML 转义（任何外部文本进 HTML 前必过）。"""
    return html.escape(str(value), quote=True)


def _kv(label: str, value: Any, cls: str = "") -> str:
    """一行「键：值」。"""
    style = f' style="color:{cls}"' if cls else ""
    return (
        f'<div><span class="jmm-kv">{_esc(label)}</span>'
        f'<span{style}>{_esc(value)}</span></div>'
    )


def _money(value: Any) -> str:
    """金额显示：保留两位小数。"""
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return str(value)


def _pct(value: Any) -> str:
    """比例显示：百分数。"""
    try:
        return f"{float(value) * 100:.0f}%"
    except (TypeError, ValueError):
        return str(value)


# ---------------------------------------------------------------------------
# 1. 收割卡片
# ---------------------------------------------------------------------------


def build_harvest_card(
    *,
    tick: int,
    day: int,
    stock_id: str,
    stock_name: str,
    concentration: float,
    severity: float,
    price_before: float,
    price_after: float,
    amount: float,
    trigger: str = "",
) -> Card:
    """玩家被收割时推送。

    含 tick、股票 id、集中度、severity —— **不含 heat**。
    """
    drop = max(0.0, float(price_before) - float(price_after))
    loss = drop * float(amount)
    title = t("card.harvest.title", "收割")
    body = "\n".join(
        (
            f"第 {tick} tick（第 {day} 天），{stock_name}（{stock_id}）被精准下杀。",
            f"价格 {price_before:.4f} → {price_after:.4f}，你这一仓亏了 {loss:.2f}。",
            f"你的持仓占比 {concentration:.0%}，这一刀的下刀力度 {severity:.0%}。",
        )
    )
    html_body = (
        f'<div class="jmm-card"><b>{_esc(title)}</b>'
        + _kv("标的", f"{stock_name}（{stock_id}）")
        + _kv("时间", f"第 {tick} tick · 第 {day} 天")
        + _kv("持仓占比", _pct(concentration))
        + _kv("下刀力度", _pct(severity))
        + _kv("价格", f"{price_before:.4f} → {price_after:.4f}")
        + _kv("这一仓的浮亏", _money(loss), cls="jmm-bad")
        + (
            f'<div class="jmm-kv">触发源：{_esc(trigger)}</div>'
            if trigger
            else ""
        )
        + "</div>"
    )
    return Card(
        kind="harvest",
        title=title,
        summary=f"你的 {stock_name} 被收割了，浮亏 {loss:.2f}。",
        body=body,
        html=html_body,
        payload={
            "tick": int(tick),
            "day": int(day),
            "stock_id": stock_id,
            "concentration": round(float(concentration), 4),
            "severity": round(float(severity), 4),
            "price_before": round(float(price_before), 6),
            "price_after": round(float(price_after), 6),
            "loss": round(loss, 4),
        },
    )


# ---------------------------------------------------------------------------
# 2. 工资卡片（P5.5 改动 1：低保 → 打工工资）
# ---------------------------------------------------------------------------


def build_welfare_card(
    *,
    cycle: int,
    amount: float,
    days_owed: int = 0,
    already_claimed: bool = False,
    deadline_day: int | None = None,
    interval_days: int = 7,
) -> Card:
    """工资周期到了 —— 提示玩家去领（**主动领，不领不到账**）。

    经典模式没有工资，不会推这张卡。

    P5.5：叙事从"低保"改成"打工" —— 拿到的不是救济，是**干活挣的钱**。
    数值（金额、周期）由引擎配置驱动，这里只做翻译。
    """
    title = t("card.welfare.title", "工资到账")
    ledger = _fill(
        t(
            "card.welfare.ledger",
            "这周打工赚的 {amount}。够活 {days} 天。如果你不碰股市的话。",
        ),
        {"amount": f"{amount:.0f}", "days": int(interval_days)},
    )
    if already_claimed:
        body = "这周工资你已经领过了，别重复伸手。"
    else:
        body = ledger + "不领就没有——这笔钱不会自己长腿跑过来。"
    html_body = (
        f'<div class="jmm-card"><b>{_esc(title)}</b>'
        + _kv("周期", f"第 {cycle} 周")
        + _kv("金额", _money(amount))
        + (_kv("补领天数", f"{days_owed} 天") if days_owed else "")
        + (
            _kv("领取截止", f"第 {deadline_day} 天")
            if deadline_day is not None
            else ""
        )
        + "</div>"
    )
    actions: dict[str, dict[str, Any]] = {}
    if not already_claimed:
        actions["claim"] = {"entry": "daily_claim", "args": {}, "label": "去领"}
        html_body = html_body.replace(
            "</div>",
            '<button data-neko-action="claim">去领</button></div>',
        )
    return Card(
        kind="welfare",
        title=title,
        summary=(
            f"第 {cycle} 周工资 {amount:.0f} 可以领了。"
            if not already_claimed
            else f"第 {cycle} 周工资你已经领过了。"
        ),
        body=body,
        html=html_body,
        actions=actions,
        payload={
            "cycle": int(cycle),
            "amount": round(float(amount), 4),
            "days_owed": int(days_owed),
            "already_claimed": bool(already_claimed),
        },
    )


# ---------------------------------------------------------------------------
# 3. 账单卡片
# ---------------------------------------------------------------------------


def build_bill_card(
    *,
    day: int,
    living_cost: float,
    next_cost: float,
    cash: float,
    gap: float,
    missed_bills: int = 0,
) -> Card:
    """日终现钱不足 —— 账单预告。

    这张卡**只陈述事实**（不威胁、不说教）：账单、你的钱、差多少。
    """
    title = t("card.bill.title", "账单")
    body = (
        f"第 {day} 天结算，你的现金 {cash:.2f}，"
        f"交不起 {max(0.0, gap):.2f}。差额就是差额，数字不会因为你看着它就变小。"
    )
    html_body = (
        f'<div class="jmm-card"><b>{_esc(title)}</b>'
        + _kv("今日账单", _money(living_cost))
        + _kv("明日账单", _money(next_cost))
        + _kv("你的现金", _money(cash))
        + _kv("缺口", _money(max(0.0, gap)), cls="jmm-warn")
        + (_kv("连续交不起", f"{missed_bills} 次") if missed_bills else "")
        + "</div>"
    )
    return Card(
        kind="bill",
        title=title,
        summary=f"第 {day} 天的账单差 {max(0.0, gap):.2f}，你的现金 {cash:.2f}。",
        body=body,
        html=html_body,
        payload={
            "day": int(day),
            "living_cost": round(float(living_cost), 4),
            "next_cost": round(float(next_cost), 4),
            "cash": round(float(cash), 4),
            "gap": round(float(gap), 4),
            "missed_bills": int(missed_bills),
        },
    )


# ---------------------------------------------------------------------------
# 4. 斩杀卡片
# ---------------------------------------------------------------------------


def build_kill_card(
    *,
    death_reason: str,
    death_title: str,
    days_survived: int,
    total_asset: float,
    harvest_count: int = 0,
    identity_echo: str = "",
    catgirl_silence: str = "",
) -> Card:
    """玩家被强制平仓 —— 结算时刻的最后一击。

    含破产原因、破产称号、存活天数、最终资产、收割次数。

    P5.5 改动 2/3：结算时刻追加**身份呼应句**与**猫娘沉默旁白**
    （两者都只在结算时出现，局中永远不会看到）。
    """
    title = t("card.kill.title", "出局")
    body = (
        f"{death_title}（{death_reason}）。"
        f"你活了 {days_survived} 天，最后账上 {total_asset:.2f}。"
    )
    if identity_echo:
        body += identity_echo
    if catgirl_silence:
        body += catgirl_silence
    html_body = (
        f'<div class="jmm-card"><b>{_esc(title)}</b>'
        + _kv("破产原因", f"{death_title}（{death_reason}）", cls="jmm-bad")
        + _kv("存活天数", f"{days_survived} 天")
        + _kv("最终资产", _money(total_asset))
        + _kv("被收割", f"{harvest_count} 次")
        + (f'<p class="jmm-echo">{_esc(identity_echo)}</p>' if identity_echo else "")
        + (
            f'<p class="jmm-silence">{_esc(catgirl_silence)}</p>'
            if catgirl_silence
            else ""
        )
        + "</div>"
    )
    return Card(
        kind="kill",
        title=title,
        summary=f"{death_title}，你活了 {days_survived} 天。",
        body=body,
        html=html_body,
        payload={
            "death_reason": death_reason,
            "death_title": death_title,
            "days_survived": int(days_survived),
            "total_asset": round(float(total_asset), 4),
            "harvest_count": int(harvest_count),
            "identity_echo": identity_echo,
            "catgirl_silence": catgirl_silence,
        },
    )


# ---------------------------------------------------------------------------
# 5. 猫娘播报卡片
# ---------------------------------------------------------------------------


def build_catgirl_daily_card(
    *,
    day: int,
    action: str,
    cash: float,
    positions_value: float,
    days_survived: int,
    position_count: int = 0,
) -> Card:
    """每日猫娘动态。

    只给**公开状态**（持仓、现金、存活天数、当天动作）—— **不给她买卖了哪只**、
    不给她的 heat。
    """
    title = t("card.catgirl_daily.title", "猫娘日报")
    body = (
        f"第 {day} 天，她今天{action}。"
        f"现金 {cash:.2f}，账面 {positions_value:.2f}，已经活了 {days_survived} 天。"
    )
    html_body = (
        f'<div class="jmm-card"><b>{_esc(title)}</b>'
        + _kv("第几天", f"第 {day} 天")
        + _kv("今天做了什么", action)
        + _kv("现金", _money(cash))
        + _kv("持仓市值", _money(positions_value))
        + _kv("持仓数", f"{position_count} 只")
        + _kv("存活天数", f"{days_survived} 天")
        + "</div>"
    )
    return Card(
        kind="catgirl_daily",
        title=title,
        summary=f"第 {day} 天：猫娘{action}，现金 {cash:.2f}。",
        body=body,
        html=html_body,
        payload={
            "day": int(day),
            "action": action,
            "cash": round(float(cash), 4),
            "positions_value": round(float(positions_value), 4),
            "days_survived": int(days_survived),
            "position_count": int(position_count),
        },
    )


# ---------------------------------------------------------------------------
# 6. 猫娘嘲讽卡片
# ---------------------------------------------------------------------------


def build_taunt_card(*, trigger: str, text: str, detail: str = "") -> Card:
    """猫娘嘲讽 —— 玩家失败时（或猫娘自己破产时）的一记冷嘲。

    ``text`` 是**待审核文案**（来自 ``i18n`` 的 ``taunt.*`` 命名空间），
    本模块只负责把它装进卡片。
    """
    title = t("card.taunt.title", "猫娘想说两句")
    body = text if not detail else f"{text}\n\n{detail}"
    html_body = (
        f'<div class="jmm-card"><b>{_esc(title)}</b>'
        + f"<div>{_esc(text)}</div>"
        + (f'<div class="jmm-kv">{_esc(detail)}</div>' if detail else "")
        + "</div>"
    )
    return Card(
        kind="taunt",
        title=title,
        summary=text,
        body=body,
        html=html_body,
        payload={"trigger": trigger, "text": text, "detail": detail},
    )


# ---------------------------------------------------------------------------
# 7. 突发新闻卡片
# ---------------------------------------------------------------------------


def build_news_flash_card(
    *,
    news_id: str,
    kind: str,
    kind_label: str,
    headline: str,
    content: str,
    day: int,
    stock_id: str | None = None,
) -> Card:
    """雷新闻发布时推送。

    **只给中性分类**（利好 / 利空 / 传闻 / 噪音）；**不给真假、不给原始 type**。
    """
    title = t("card.news_flash.title", "突发新闻")
    body = f"第 {day} 天，{kind_label}：{headline}"
    html_body = (
        f'<div class="jmm-card"><b>{_esc(title)}</b>'
        + _kv("分类", kind_label)
        + _kv("标题", headline)
        + _kv("正文", content)
        + (
            '<button data-neko-action="detail">看详情</button>' "</div>"
        )
    )
    return Card(
        kind="news_flash",
        title=title,
        summary=f"{kind_label}：{headline}",
        body=body,
        html=html_body,
        actions={
            "detail": {
                "entry": "news_detail",
                "args": {"news_id": news_id},
                "label": "看详情",
            }
        },
        payload={
            "news_id": news_id,
            "kind": kind,
            "kind_label": kind_label,
            "day": int(day),
            "stock_id": stock_id,
        },
    )


# ---------------------------------------------------------------------------
# 8. 二道贩子提醒卡片
# ---------------------------------------------------------------------------


def build_pawnbroker_card(
    *,
    day: int,
    gap: float,
    discount: float,
    legs: list[dict[str, Any]],
    cash_after: float,
    window_seconds: int = 0,
) -> Card:
    """二道贩子报价 —— 强制卖出**前**的提醒。

    按钮语义（P5 契约 §2.4 修正版）：

    * **"拒绝" → ``daily_reject``**（撤销这次强制卖出）；
    * **"接受"不需要按钮** —— 不调 ``daily_reject`` 就是默认接受，
      强制卖出照常执行（方案 A：引擎零改动）。
    """
    title = t("card.pawnbroker.title", "二道贩子来敲门")
    lines = [
        f"第 {day} 天，你的现金交不起账单，缺口 {max(0.0, gap):.2f}。",
        f"二道贩子按回收价 {discount:.0%} 收你的仓，凑齐之后大概还剩 {cash_after:.2f}。",
        "接受就成交。不想成交就点「拒绝」——代价是下次的账单你自己扛。",
    ]
    body = "\n".join(lines)
    html_body = (
        f'<div class="jmm-card"><b>{_esc(title)}</b>'
        + _kv("缺口", _money(max(0.0, gap)), cls="jmm-warn")
        + _kv("回收价折扣", _pct(discount))
        + _kv("成交后剩余现金", _money(cash_after))
    )
    for leg in legs:
        html_body += _kv(
            "要收走",
            f"{leg.get('stock_name', leg.get('stock_id', ''))}"
            f" × {leg.get('amount', 0)}（约 {leg.get('notional', 0):.2f}）",
            cls="jmm-bad",
        )
    html_body += (
        (
            f'<div class="jmm-kv">反悔窗口还有 {window_seconds} 秒</div>'
            if window_seconds
            else ""
        )
        + '<button data-neko-action="reject">拒绝</button>'
        + "</div>"
    )
    return Card(
        kind="pawnbroker",
        title=title,
        summary=f"二道贩子要按 {discount:.0%} 回收价收你的仓，缺口 {max(0.0, gap):.2f}。",
        body=body,
        html=html_body,
        # 只有"拒绝"这一个按钮：接受 = 什么都不做（默认行为）。
        actions={"reject": {"entry": "daily_reject", "args": {}, "label": "拒绝"}},
        payload={
            "day": int(day),
            "gap": round(float(gap), 4),
            "discount": round(float(discount), 4),
            "cash_after": round(float(cash_after), 4),
            "legs": [dict(leg) for leg in legs],
        },
    )


# ---------------------------------------------------------------------------
# 9. 称号解锁卡片（P5.5 · 改动 6）
# ---------------------------------------------------------------------------


def build_achievement_card(
    *,
    title_name: str,
    note: str,
    unlocked_count: int = 0,
    total: int = 0,
    identity_echo: str = "",
) -> Card:
    """称号解锁通知。

    只给**称号名**与**一句叙事** —— 不给 heat / seed / 任何内部字段。
    """
    title = t("card.achievement.title", "称号解锁")
    body = note
    if identity_echo:
        body = f"{body}{identity_echo}"
    html_body = (
        f'<div class="jmm-card"><b>{_esc(title)}</b>'
        + _kv("称号", title_name, cls="jmm-good")
        + _kv("图鉴", f"{unlocked_count}/{total}")
        + f'<p class="jmm-echo">{_esc(note)}</p>'
        + (f'<p class="jmm-silence">{_esc(identity_echo)}</p>' if identity_echo else "")
        + "</div>"
    )
    return Card(
        kind="achievement",
        title=title,
        summary=f"称号解锁：{title_name}。",
        body=body,
        html=html_body,
        payload={
            "achievement": title_name,
            "note": note,
            "unlocked_count": int(unlocked_count),
            "total": int(total),
            "identity_echo": identity_echo,
        },
    )