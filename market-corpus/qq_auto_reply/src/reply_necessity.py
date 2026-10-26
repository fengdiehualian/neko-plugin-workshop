# -*- coding: utf-8 -*-
"""群聊「这句到底该不该接」的状态化判定。

**为什么要它**：原先这里是 `message_dispatcher._looks_like_human_followup` —— 一个只看
文本长度/前缀/问号的布尔启发式。调研（见 `docs/GROUP-CHAT-RESPONSE-MECHANISMS.md`）
给出了两条硬结论：

1. 我们那条规则**正好是 addressee 文献里最弱的基线**（「下一个说话人 = 上一个说话人」）：
   短会话 p@1 63.5%，长会话 Acc 只剩 **13.08%**（Le et al. 2019, D19-1199 Table 3），
   而我们的群恰在「人多话密」那一端。
2. CHI 2025 的 *Inner Thoughts* 论证：在**没人被点名**的场景，next-speaker prediction
   **本质上不适定**；可行做法是按若干维度给候选发言打分、过阈值才说。
   （8 条启发式里 Relevance 提及 77 次、Information Gap 33 次、Expected Impact 23 次。）

**分值配方**取自 MaiBot `src/maisaka/reply_necessity.py`（常量逐字对照，可考据），
并按我们的语境做了两处调整，都在下面常量处注明：

- 相关性强相关项用我们已有的信号（@ / 引用她 / focus / 私聊）；
- 「存在感惩罚」吃的是**近 300 秒内她自己发言占比**——这是全项目里唯一
  「说太多 → 降低再次发言意愿」的量化机制，也是我们最缺的那类自适应。
  2026-09-29 使用者口径「6 条就休息频率有点少了，发言惩罚减轻一点」→ 免费区从
  25% 放到 45%、满罚从 25 分降到 15 分（见那三个常量处的注释），机制本身保留。

本模块**纯逻辑**（不碰 plugin / 网络 / 文件），因此可以被单测穷举。
"""

from __future__ import annotations

import math
import re
import time
from collections import deque
from dataclasses import dataclass, field

from . import addressing

# ── 打分常量（MaiBot reply_necessity 对照；注释标出处）─────────────────────────
AT_SCORE = 100                 # MaiBot: has_at → 100
MENTION_SCORE = 80             # MaiBot: has_mention → 80
FOCUS_SCORE = 40               # MaiBot: focus_active → 40
PRIVATE_SCORE = 40             # MaiBot: 私聊 → 40
PLAIN_SCORE = 0                # MaiBot: 普通（没人叫）→ 0

QUESTION_SCORE = 15            # 问题
REQUEST_SCORE = 20             # 请求
OPINION_SCORE = 20             # 征询意见
LONG_LENGTH_40 = 5             # 总长 ≥40
LONG_LENGTH_120 = 10           # 总长 ≥120
SHORT_REACTION_PENALTY = -25   # 纯短反应批次（"哈哈/草/666" 之类）

PRESSURE_STANDARD_SCORE = 50.0  # 积压压力：ratio ≤ 1 时的满分
PRESSURE_MAX_SCORE = 100.0      # 上限
PRESSURE_FULL_RATIO = 4.0       # ratio > 1 时 log 曲线的饱和点
PRESSURE_IDLE_BONUS = 15        # 有积压且已到平均间隔 → 补分

# ── 存在感惩罚的三个旋钮（2026-09-29 减轻过一次，别照老文档"还原"）─────────────
#
# 为什么旧值（0.25 / 0.60 / 25）会变成"说 6 句就休息"：判定基线 `FOCUS_SCORE` 40 恰好
# **等于**阈值 40，而"她刚说完、群里还没攒起积压"时压力项几乎为 0 —— 于是**任何一分**
# 存在感惩罚都会把普通闲聊翻成 wait，真正的休息点其实由"免费区"决定（旧值 0.25，
# 也就是她占窗口四分之一就开始扣分）。
#
# 使用者口径：「6 条就休息频率有点少了，发言惩罚减轻一点」。现在：
#   · 占比 ≤ 45% 完全不罚（她与群里对半说话也不罚）；
#   · 到 80% 才罚满；满罚 15 分（每多占 1% 约扣 0.43 分）；
#   · 积压大 / 被提问时这点惩罚照样被盖过 —— 纯"她一个人刷屏"才会停。
SELF_RATIO_FREE = 0.45          # 近窗口内自己发言占比 ≤ 此值不罚
SELF_RATIO_FULL = 0.80          # 占比 ≥ 此值罚满
SELF_PENALTY_MAX = 15.0         # 存在感惩罚上限（2026-09-29 由 25 降下来）

# ── addressee（谁在跟谁说话）────────────────────────────────────────────────
#
# 见 `addressing.py`。这两项与上面那批 MaiBot 常数**不是同一种东西**：那批是"她多想
# 聊这个群"的量，这两项是"这句话是不是冲她来的"的量 —— 一个减分、一个加分，都作用在
# 同一个总分上，但依据完全不同（所以进 breakdown 的字段也分开，便于日志归因）。
#
#: 「明确在跟别人说话」（@别人 / 引用别人）的默认减分。
#:
#: 取 30 而不是 40 的理由：阈值恰好是 40，罚到 40 就等于"这类消息一律不接"，那是硬
#: 门控、不是减分（硬门控另有一个开关，见 `attention_gate_service`）。30 的语义是
#: 「她要靠内容分或积压压力把这一条捞回来」——一条"@了别人但顺手问她"的消息仍然接得住。
ADDRESSED_ELSEWHERE_PENALTY_DEFAULT = 30.0

#: 判定阈值默认值。
#:
#: ⚠️ 这个数是**行为旋钮**，不是照抄来的：MaiBot 用 80（它这一关同时承担「攒够几条
#: 才值得思考」），而我们把这一关放在**焦点群内部**（群间竞争已由注意力系统解决），
#: 所以要比 80 低、但必须高于「focus 的 40」——否则焦点群里的普通消息光靠 focus 分
#: 就能通过，这一关等于不存在（接线时被集成测试抓到的正是这个）。
#:
#: **默认值由真机回放定**：把 `backlog_state.json` 里 244 条真实群消息按时间回放
#: （模拟「判定通过 = 她回了一句」，因此存在感会像真实一样累积），得到
#:
#:     阈值  30 → 抑制 25.8%   40 → 49.6%   50 → 76.7%   60 → 86.0%   70 → 90.7%
#:
#: 60 会让她在真实群里近乎静音（只有被 @ 或积压巨大时才开口）——太狠，故取 **40**：
#: 「参加但不抢话」。这一档的实际效果是：
#:   · 被 @ / 引用她            → 100 / 80，**必过**
#:   · 群很忙（积压到阈值）     → 40 + 50 = 90，过
#:   · 有人提问（哪怕群里安静） → 40 + 15 + ≈1 = 56，过
#:   · 安静时的普通闲聊         → 40 + ≈1 = 41，过（**仍然搭话**，不像 60 那样一律静音）
#:   · 纯短反应（"哈哈"）       → 16，不过
#:   · 她刚说过一阵子（占比 >45%）→ 再扣最多 15（2026-09-29 由 25 降下来）→
#:     空场里普通闲聊 25 上下，不过；群里攒着积压时照样过 ← 主力机制
#:   · 连续不接 → 空闲退避接管，指数放长到 300s
#: 取 0 = 关闭这一关（回到「交给 LLM 自判」的老行为）；想更安静就往上调。
DEFAULT_TRIGGER_SCORE = 40.0

#: 短反应词表（MaiBot 逐字）：整条消息 ≤8 字符且全部落在集合里才算「纯短反应批次」。
SHORT_REACTIONS = frozenset({"哈哈", "哈哈哈", "草", "笑死", "好", "嗯", "啊", "哦", "6", "666", "？", "?"})

#: 「这话是对另一个 AI 说的」——MaiBot 里有硬编码词表，我们**不硬编码 bot 名字**，
#: 只保留第三方助手词表（那是它在源码里唯一的 addressee 显式处理），
#: 自己名字的匹配一律走 ``is_at_bot`` / ``is_reply_to_bot``。
OTHER_ASSISTANT_PATTERN = re.compile(
    r"^(?:DeepSeek|ChatGPT|Grok|豆包|千问|元宝|通义|Kimi|Claude)[，,、\s]",
    re.IGNORECASE,
)

_NOISE_PATTERNS = (
    re.compile(r"\[CQ:reply[^\]]*\]"),
    re.compile(r"\[reply\]"),
    re.compile(r"\[回复了.+?的消息: .+?\]"),
    re.compile(r"\[回复消息\]"),
    re.compile(r"@<[^>]+>"),
    re.compile(r"@\S+"),
)

_QUESTION_HINTS = ("?", "？", "吗", "呢", "怎么", "如何", "为什么", "为啥", "多少", "哪个", "哪儿")
_REQUEST_HINTS = ("帮我", "请帮", "能不能", "可以帮", "来一个", "给我", "麻烦你", "麻烦帮")
_OPINION_HINTS = ("怎么看", "意见", "建议", "觉得呢", "你认为", "你说呢", "评价一下")


def strip_noise(text: str) -> str:
    """剥掉回复/@ 等噪声，只留正文（MaiBot ``strip_reply_necessity_noise`` 的对应物）。"""
    out = str(text or "")
    for pattern in _NOISE_PATTERNS:
        out = pattern.sub(" ", out)
    out = re.sub(r"\s+", " ", out).strip()
    return out


def is_short_reaction_batch(text: str) -> bool:
    """纯短反应批次：整条 ≤8 字符且全落在 ``SHORT_REACTIONS`` 里。"""
    compact = strip_noise(text).replace(" ", "")
    if not compact or len(compact) > 8:
        return False
    # 允许多个短反应连写（"哈哈好"）：逐字符窗口核验太严，这里按集合成员切分。
    rest = compact
    while rest:
        matched = next((word for word in sorted(SHORT_REACTIONS, key=len, reverse=True) if rest.startswith(word)), "")
        if not matched:
            return False
        rest = rest[len(matched):]
    return True


@dataclass(frozen=True, slots=True)
class NecessitySignals:
    """判定输入。全部由调用方提供，模块本身不取任何数据。"""

    message_text: str = ""
    is_at_bot: bool = False
    is_reply_to_bot: bool = False
    is_group: bool = True
    focus_active: bool = False
    #: 该群近期未处理消息数（含当前这条）与阈值——压力分来源
    pending_count: int = 1
    pending_threshold: int = 3
    #: 近窗口内她自己发言占比（0~1）——存在感惩罚来源
    self_ratio: float = 0.0
    #: 已到平均消息间隔却仍无积压 → 补一点分（MaiBot 的 idle_reached_average）
    idle_reached_average: bool = False
    #: 连续多少条**别人的**消息既没 @ 她也没引用她（"这群人正在互相聊"）。
    #:
    #: 这是**结构性**信号：学术上「谁在跟谁说话」是接话判定里净收益最大的非指称特征，
    #: 而我们此前只有布尔量（`mentions_other_user`），没有"连续多少条"这个量。
    #: 2026-09-27 先把它算出来、写进日志，**默认不参与打分**（见 `score_necessity`
    #: 的 `human_pair_penalty` 默认 0.0）—— 等真机数据够了再决定扣多少。
    human_pair_streak: int = 0
    #: 「谁在跟谁说话」的结论档位（`addressing.KIND_*`）与指向对象。
    #: 空串 = 没有结论（开放平台通道 / 旁路调用 / 旧调用方）→ 完全退回改动前的行为。
    addressee_kind: str = ""
    addressee_target: str = ""
    #: 段首 @ 的就是别人（`addressing.AddresseeVerdict.first_at_other`）。
    #: 只用于日志措辞：减分不看它（@ 别人无论在第几段都要减），硬门控在门控层。
    addressee_first_at_other: bool = False


@dataclass(frozen=True, slots=True)
class NecessityBreakdown:
    relevance: int = 0
    content: int = 0
    pressure: int = 0
    presence: int = 0
    #: 「人对人」惩罚（默认 0：先只出数据，见 NecessitySignals.human_pair_streak）
    human_pair: int = 0
    #: addressee 净增减（负数=明确在跟别人说话；正数=文本里叫了她的名字）
    addressee: int = 0
    frequency_factor: float = 1.0
    raw: float = 0.0
    score: int = 0
    reasons: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class NecessityVerdict:
    decision: str            # "trigger" | "wait"
    score: int
    breakdown: NecessityBreakdown

    @property
    def reason(self) -> str:
        """给门控日志用的一行原因（可诊断，不含隐私文本）。"""
        return f"necessity_{self.decision}({self.score}:{'+'.join(self.breakdown.reasons) or '—'})"


def _relevance(signals: NecessitySignals) -> tuple[int, str]:
    if signals.is_at_bot:
        return AT_SCORE, "@"
    if signals.is_reply_to_bot:
        return MENTION_SCORE, "引用她"
    if not signals.is_group:
        return PRIVATE_SCORE, "私聊"
    if signals.focus_active:
        return FOCUS_SCORE, "focus"
    return PLAIN_SCORE, "普通"


def _content(signals: NecessitySignals) -> tuple[int, str]:
    text = strip_noise(signals.message_text)
    if not text:
        return 0, ""
    if is_short_reaction_batch(text):
        return SHORT_REACTION_PENALTY, "短反应"
    score = 0
    labels: list[str] = []
    if any(hint in text for hint in _QUESTION_HINTS):
        score += QUESTION_SCORE
        labels.append("提问")
    # 开头点名的是**别的助手**（"DeepSeek，帮我看看"）→ 请求/征询类加分作废：
    # 这话不是冲我们来的。（MaiBot 同款词表处理；我们不复刻它硬编码自己名字的坑。）
    addressed_elsewhere = bool(OTHER_ASSISTANT_PATTERN.match(text))
    if any(hint in text for hint in _REQUEST_HINTS) and not addressed_elsewhere:
        score += REQUEST_SCORE
        labels.append("请求")
    if any(hint in text for hint in _OPINION_HINTS) and not addressed_elsewhere:
        score += OPINION_SCORE
        labels.append("征询")
    if len(text) >= 120:
        score += LONG_LENGTH_120
        labels.append("长文120+")
    elif len(text) >= 40:
        score += LONG_LENGTH_40
        labels.append("长文40+")
    return score, "+".join(labels)


def _pressure(signals: NecessitySignals) -> tuple[int, str]:
    threshold = max(1, int(signals.pending_threshold or 1))
    pending = max(0, int(signals.pending_count or 0))
    if pending <= 0:
        return 0, ""
    ratio = pending / threshold
    if ratio <= 1.0:
        score = PRESSURE_STANDARD_SCORE * ratio * ratio          # 二次增长：1 条时几乎不推
        if signals.idle_reached_average:
            score += PRESSURE_IDLE_BONUS
        return min(int(round(score)), int(PRESSURE_STANDARD_SCORE)), "积压"
    overflow = ratio - 1.0
    # 对数曲线（MaiBot 原式：`50 + round(50 * log1p(overflow)/log1p(4.0))`）。
    # ⚠️ 乘子必须是 STANDARD(50) 而不是 MAX(100)：用 100 时 overflow≈1.16 就顶到上限，
    # 曲线退化成阶跃（这个错误被单测抓过）。
    score = PRESSURE_STANDARD_SCORE + PRESSURE_STANDARD_SCORE * math.log1p(overflow) / math.log1p(PRESSURE_FULL_RATIO)
    return min(int(round(score)), int(PRESSURE_MAX_SCORE)), "积压多"


def _presence(signals: NecessitySignals) -> tuple[int, str]:
    ratio = max(0.0, min(1.0, float(signals.self_ratio or 0.0)))
    if ratio <= SELF_RATIO_FREE:
        return 0, ""
    span = max(1e-6, SELF_RATIO_FULL - SELF_RATIO_FREE)
    penalty = SELF_PENALTY_MAX * min(1.0, (ratio - SELF_RATIO_FREE) / span)
    return -int(round(penalty)), "存在感"


def score_necessity(
    signals: NecessitySignals,
    *,
    threshold: float = DEFAULT_TRIGGER_SCORE,
    frequency: float = 1.0,
    human_pair_penalty: float = 0.0,
    human_pair_min_streak: int = 3,
    addressee_penalty: float = ADDRESSED_ELSEWHERE_PENALTY_DEFAULT,
) -> NecessityVerdict:
    """给一条消息打分并给出 trigger / wait。

    ``threshold <= 0`` ⇒ 恒 trigger（关闭这一关，回到「交给 LLM 自判」）。

    ``human_pair_penalty`` 默认 **0.0**：连续 ``human_pair_min_streak`` 条别人的消息既没
    @ 她也没引用她（= 这群人正在互相聊）时扣多少分。**先只出数据不扣分** ——
    真机日志里能看到 `人对人×N`，等数据够了再决定扣多少（改一个配置键即可，
    不用改代码）。这一点是刻意的：阈值 40 恰好等于焦点分 40，任何"顺手也调一下"
    都会让必要性的行为一次性变动两处，事后分不清是谁的功劳。

    ``addressee_penalty`` **默认就生效**（与上面那条相反，是有意的）：`human_pair_streak`
    只知道"这群人连着几条没跟她说话"，而 addressee 知道**这条消息明确指向了别人**
    （@别人 / 引用别人）—— 前者是需要慢慢攒的结构性信号，后者是逐条可判的事实，
    拿事实去减分不需要再等数据。信号为空串（没有结论）时这一项恒为 0。
    """
    relevance, rel_reason = _relevance(signals)
    content, content_reason = _content(signals)
    pressure, pressure_reason = _pressure(signals)
    presence, presence_reason = _presence(signals)
    pair_penalty, pair_reason = _human_pair(signals, human_pair_penalty, human_pair_min_streak)
    addressee_delta, addressee_reason = _addressee(signals, addressee_penalty)

    reasons = tuple(
        r for r in (
            rel_reason, content_reason, pressure_reason, presence_reason,
            pair_reason, addressee_reason,
        ) if r
    )
    raw = float(relevance + content + pressure + presence + addressee_delta - pair_penalty)
    # 频率因子：把「这个群我本来就不怎么说话」乘进总分（MaiBot 同款 0.5~1.0）。
    factor = 0.5 + 0.5 * max(0.0, min(1.0, float(frequency or 0.0)))
    score = max(0, int(round(raw * factor)))

    limit = float(threshold or 0.0)
    decision = "trigger" if limit <= 0 or score >= limit else "wait"
    breakdown = NecessityBreakdown(
        relevance=relevance,
        content=content,
        pressure=pressure,
        presence=presence,
        human_pair=-pair_penalty,
        addressee=addressee_delta,
        frequency_factor=factor,
        raw=raw,
        score=score,
        reasons=reasons,
    )
    return NecessityVerdict(decision=decision, score=score, breakdown=breakdown)


def _addressee(signals: NecessitySignals, penalty: float) -> tuple[int, str]:
    """「谁在跟谁说话」的净增减（与它的可读原因）。

    这里只处理"明确在跟别人说话"（``@别人`` / ``引用别人``）→ 减 ``penalty``。
    文本里叫了她的名字**不在这里**：使用者口径（2026-09-29）「叫猫娘名字也等于@她」，
    所以 `named_bot` 走的是门控第 2 步的等同 @ 旁路（`attention_gate_service`），
    在到这一关之前就已经返回了 —— 打分层里再给它加一次分只会是走不到的死分支
    （原 `NAMED_BOT_SCORE = 40` 已删）。

    **只减到"能减"为止，不把分数压成硬零**：真正的"这条根本不该接"由门控层的
    可选硬门控负责（`addressee_ignore_first_at_other`），打分器只表达倾向。
    """
    kind = str(getattr(signals, "addressee_kind", "") or "").strip()
    if kind in addressing.POINTED_ELSEWHERE:
        target = str(getattr(signals, "addressee_target", "") or "").strip()
        reason = "指向别人" if not target else f"指向别人({target})"
        if getattr(signals, "addressee_first_at_other", False):
            reason = f"{reason}·首段@"
        return -int(round(max(0.0, float(penalty or 0.0)))), reason
    return 0, ""


def _human_pair(
    signals: NecessitySignals, penalty: float, min_streak: int,
) -> tuple[int, str]:
    """「这群人正在互相聊」的扣分（与它的可读原因）。

    **罚 0 分时也出原因**（``人对人×N``）：这正是本次要收集的那份数据 ——
    日志里看不见的话，"先出数据再决定"就成了一句空话。
    """
    streak = max(0, int(signals.human_pair_streak or 0))
    floor = max(1, int(min_streak or 1))
    if streak < floor:
        return 0, ""
    reason = f"人对人×{streak}"
    if penalty <= 0:
        return 0, reason
    # 一句话没说给她的场合：扣分不超过内容分的量级（避免把"她本来想接的"直接掐死）
    return int(round(float(penalty))), reason


# ── 状态：近期发言窗口 + 空闲退避 ────────────────────────────────────────────

SELF = "__self__"


class GroupSpeechTracker:
    """每群近期发言窗口（**内存态**，重启即失，与 MaiBot 的同类状态一致）。

    它提供两个量：

    - ``pending_count``：她上次发言之后又来了几条（积压压力）
    - ``self_ratio``：窗口内她自己发言占比（存在感惩罚）
    """

    WINDOW_SECONDS = 300.0
    MAX_EVENTS = 200

    def __init__(self, *, window_seconds: float = WINDOW_SECONDS) -> None:
        self.window_seconds = float(window_seconds)
        self._events: dict[str, deque[tuple[float, str]]] = {}

    def _prune(self, group_id: str, now: float) -> deque[tuple[float, str]]:
        events = self._events.get(group_id)
        if events is None:
            events = deque(maxlen=self.MAX_EVENTS)
            self._events[group_id] = events
        cutoff = now - self.window_seconds
        while events and events[0][0] < cutoff:
            events.popleft()
        return events

    def record(self, group_id: str, *, now: float | None = None, speaker: str = "") -> None:
        key = str(group_id or "").strip()
        if not key:
            return
        ts = float(now if now is not None else time.time())
        self._prune(key, ts).append((ts, str(speaker or "")))

    def record_self(self, group_id: str, *, now: float | None = None) -> None:
        self.record(group_id, now=now, speaker=SELF)

    def pending_count(self, group_id: str, *, now: float | None = None) -> int:
        """她上次发言之后的他消息条数（没有她的发言时 = 窗口内全部）。"""
        key = str(group_id or "").strip()
        ts = float(now if now is not None else time.time())
        events = self._prune(key, ts)
        count = 0
        for _, speaker in reversed(events):
            if speaker == SELF:
                break
            count += 1
        return count

    def self_ratio(self, group_id: str, *, now: float | None = None) -> float:
        key = str(group_id or "").strip()
        ts = float(now if now is not None else time.time())
        events = self._prune(key, ts)
        if not events:
            return 0.0
        mine = sum(1 for _, speaker in events if speaker == SELF)
        return mine / len(events)

    def last_gap_seconds(self, group_id: str, *, now: float | None = None) -> float:
        """距窗口内最后一条消息过去了多久（没有任何消息时返回一个很大的值）。

        用途：MaiBot 的 ``idle_reached_average`` —— 「群里已经静了一会儿但仍有积压」
        属于值得接的信号，压力分给一点补偿。
        """
        key = str(group_id or "").strip()
        ts = float(now if now is not None else time.time())
        events = self._prune(key, ts)
        if not events:
            return float("inf")
        return max(0.0, ts - events[-1][0])

    def forget(self, group_id: str) -> None:
        self._events.pop(str(group_id or "").strip(), None)


class IdleBackoff:
    """空闲退避：连续「决定不接」之后把再次评估的间隔指数放长。

    常量取自 MaiBot（``no_action_backoff_*``）：15s 起、上限 300s、连续 2 次开始、
    积压 ≥6 条**绕过**退避。任何 @ / 引用她 / 她的新发言都会重置。
    """

    BASE_SECONDS = 15.0
    CAP_SECONDS = 300.0
    START_COUNT = 2
    BYPASS_PENDING = 6

    def __init__(self) -> None:
        self._count: dict[str, int] = {}
        self._until: dict[str, float] = {}

    def delay_seconds(self, group_id: str, *, now: float | None = None, pending_count: int = 0) -> float:
        """还需等待多少秒（0 = 可以立即评估）。积压够多直接绕过。"""
        key = str(group_id or "").strip()
        if pending_count >= self.BYPASS_PENDING:
            return 0.0
        ts = float(now if now is not None else time.time())
        remaining = float(self._until.get(key, 0.0)) - ts
        return max(0.0, remaining)

    def record_wait(self, group_id: str, *, now: float | None = None) -> float:
        """记一次「这轮不接」，返回本次设定的退避秒数。"""
        key = str(group_id or "").strip()
        if not key:
            return 0.0
        ts = float(now if now is not None else time.time())
        count = self._count.get(key, 0) + 1
        self._count[key] = count
        if count < self.START_COUNT:
            return 0.0
        exponent = max(0, count - self.START_COUNT)
        seconds = min(self.CAP_SECONDS, self.BASE_SECONDS * (2 ** exponent))
        self._until[key] = ts + seconds
        return seconds

    def reset(self, group_id: str) -> None:
        key = str(group_id or "").strip()
        self._count.pop(key, None)
        self._until.pop(key, None)
