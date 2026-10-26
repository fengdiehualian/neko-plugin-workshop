"""「谁在跟谁说话」——这条群消息到底是指向她的，还是别人之间的对话。

## 为什么单独一个模块

决策链路上「该不该接」有两个性质不同的输入：**她多想聊这个群**（注意力）与**这条
话是不是冲她来的**（addressee）。前者已有 `attention_service`，后者此前只以 6 个互斥
字符串的形式活在提示词里（`prompting._build_group_turn_message`）——**不进任何分数、
不进任何门控**。后果是：一条明确「@ 了别人」的消息，它的必要性相关分与「谁都没提
她」完全一样（都是 `PLAIN_SCORE = 0`），只靠模型自己在提示词里读那句"不要自作多情"
来收敛。

调研结论（`docs/GROUP-CHAT-RESPONSE-MECHANISMS.md` §1.6）也指向同一件事：「谁在跟谁
说话」是接话判定里净收益最大的非指称特征，而各家主流实现都只有事件级二值门控。
本模块把它做成**一个可判定的结论**（而不是若干布尔量的散装组合），让打分、门控、
提示词三层共用同一份真相。

## 判据阶梯（优先级从高到低，先命中先返回）

1. `at_bot` —— @ 了她（含"@ 名单里有她"，且**压过**同时 @ 别人的情况）
2. `reply_to_bot` —— 引用/回复了她的消息
3. `at_all` —— @全体（对全群广播，不针对某个人）
4. `reply_to_other` —— 引用的是**别人**的消息
5. `at_other` —— @ 了别人
6. `named_bot` —— 文本里出现她的名字/别名（没 @ 也是在叫她）
7. `after_own_speech` —— 她刚说完，这是之后的第一条发言（时序线索）
8. `group_chatter` —— 判不出来，按群内闲聊处理

前 3 档是「冲她来的」，4/5 档是「明确指向别人」（`POINTED_ELSEWHERE`），6/7 档是
「可能是她」，8 档是「大概不是」。

## 原料早就在线上了，卡的是一个过期假设

`_vendor/connection_onebot/onebot_client.py:232-284` 的 `_extract_interaction_context`
早在解析回复段时就取出了 `quoted_sender_id`（**被引用的那条是谁发的**）与
`mentioned_user_ids`（@ 了哪些人，不只是布尔量），并把 `mentions_bot` 一起给了出来。
插件侧此前一个都没读 —— `attention_gate_service._record_human_pair` 的 docstring 甚至
写着「NapCat 侧要拿被引用者的 uid 得额外 `get_msg`」，**这个前提是错的**：uid 就在回复
段的 `data.user_id` 里。于是"谁回谁"这件事一直没人做。本模块消费这三个字段，顺带把
那句 docstring 改对了。

## 范围：只对 NapCat / OneBot 通道生效

开放平台通道上每条群消息都已经是 @ 她的（`attention_gate_service.evaluate` 第一步就
`no_attention_needed` 返回回复），"该不该插话"在那条通道上不存在，硬套这套判据只会
把已有的引用/昵称信息误当衰减信号。所以调用方（dispatcher）只在 NapCat 通道构造结论，
开放平台通道一律给 `None` —— **退化成改动前的行为**，不是"另一份实现"。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

#: 结论档位。字符串常量而不是 Enum：它们要进日志、trace 与配置叙述，
#: 且需要能被 JSON 直接序列化（trace 会落进 pipeline 快照）。
KIND_AT_BOT = "at_bot"
KIND_REPLY_TO_BOT = "reply_to_bot"
KIND_AT_ALL = "at_all"
KIND_REPLY_TO_OTHER = "reply_to_other"
KIND_AT_OTHER = "at_other"
KIND_NAMED_BOT = "named_bot"
KIND_AFTER_OWN_SPEECH = "after_own_speech"
KIND_GROUP_CHATTER = "group_chatter"

#: 「明确指向别人」的两档 —— 必要性减分与（可选的）硬门控都只认它们。
#: `at_all` **不在**这里：@全体是广播，人人都被点到，不该算"在跟别人说话"。
POINTED_ELSEWHERE = frozenset({KIND_REPLY_TO_OTHER, KIND_AT_OTHER})

#: 「冲她来的」两档（@她 / 引用她）—— 门控里的唯一旁路。
POINTED_AT_BOT = frozenset({KIND_AT_BOT, KIND_REPLY_TO_BOT})

#: 名字命中的最小长度。单字名（如"猫"）在群聊里几乎必然误命中，宁可漏判：
#: 漏判的代价只是退回"她本来就会读提示词自判"，误判的代价是她对每一句闲聊都当点名。
NAME_MIN_CHARS = 2


@dataclass(frozen=True, slots=True)
class AddresseeVerdict:
    """「这条消息在跟谁说话」的结论。

    刻意做成**只读结论**而不是一堆布尔参数：打分、门控、提示词三层要看到同一份判断，
    各自重算一遍就会漂移（这个仓库里已经有过两处"同一个事实两个真源"的教训）。
    """

    kind: str = KIND_GROUP_CHATTER
    #: 指向的对象（QQ 号）。`at_all` 为空；判不出来也为空。
    target_id: str = ""
    #: 段首那个 @ 指向谁（"" = 首段不是 @）。只服务"首段 @ 别人"这一条硬判据，
    #: 取自原始段序 —— 连接器给的是布尔量，压掉了"第几段"这个信息。
    first_at: str = ""
    #: 命中的名字（`named_bot` 档）。日志用，不进分数。
    matched_name: str = ""
    #: 可读依据，进日志与 trace（不含消息正文）。
    evidence: str = ""

    @property
    def points_elsewhere(self) -> bool:
        """明确在跟别人说话？"""
        return self.kind in POINTED_ELSEWHERE

    @property
    def points_at_bot(self) -> bool:
        """明确在跟她说话？"""
        return self.kind in POINTED_AT_BOT

    @property
    def first_at_other(self) -> bool:
        """段首就 @ 了别人（且不是 @全体，也不是她）。

        AstrBot 的唤醒判据原文：*如果是群聊，且第一个消息段是 At 消息，但不是 At
        机器人或 At 全体成员，则不唤醒*。我们把它做成**可选**的硬门控（默认关），
        默认只用减分 —— 群里 @ 别人但话题确实在她身上时，硬拦会误伤。

        "是不是她"在 `resolve_addressee` 里就已经处理掉了：段首 @ 的是她时
        `first_at` 被抹成空串（那种消息早在第 1 档就返回 `at_bot` 了）。
        """
        return bool(self.first_at) and self.first_at != "all"

    def as_trace(self) -> dict[str, str]:
        """进 pipeline trace 的形状（`QQReplyRequest` 只带三个字符串字段）。"""
        return {
            "kind": self.kind,
            "target_id": self.target_id,
            "evidence": self.evidence,
        }


def first_at_target(segments: Any) -> str:
    """**段首**那个 @ 指向谁；首段不是 @ 就返回 ""。

    只看第一段，因为判据本身就是"第一句就在跟别人说话"—— 中间或末尾的 @ 别人
    不改变这条消息冲谁而来（那种情况走 `at_other` 档，仍然减分，但不硬拦）。
    支持数组段与 CQ 串两种形态（与 `QQMessageEnricher._message_segments` 同源，
    由调用方把段喂进来）。
    """
    if isinstance(segments, list):
        if not segments:
            return ""
        head = segments[0]
        if not isinstance(head, dict) or str(head.get("type") or "") != "at":
            return ""
        data = head.get("data")
        if not isinstance(data, dict):
            return ""
        return str(data.get("qq") or "").strip()
    if isinstance(segments, str):
        # CQ 串形态：只有**开头**就是 [CQ:at,...] 才算段首 @。
        text = segments.lstrip()
        if not text.startswith("[CQ:at,"):
            return ""
        head, _, _ = text.partition("]")
        for part in head.split(","):
            key, _, value = part.partition("=")
            if key.strip().lower() == "qq":
                return value.strip()
    return ""


def matched_name(text: str, names: Iterable[str]) -> str:
    """文本里命中的她的名字/别名（取最长的一个）。没命中返回 ""。"""
    body = str(text or "")
    if not body:
        return ""
    hits = [
        str(name).strip()
        for name in names
        if len(str(name).strip()) >= NAME_MIN_CHARS and str(name).strip() in body
    ]
    if not hits:
        return ""
    return max(hits, key=len)


def configured_names(settings: dict[str, Any] | None, *, host_names: Iterable[str] = ()) -> tuple[str, ...]:
    """她的名字清单 = 本体人设里的名字 + 用户在 `addressee_names` 里补的别名。

    为什么默认能空手开工：本体人设里的 `her_name` 是现成的、随本体升级而变，
    不需要用户在插件里再抄一遍（抄一遍就会漂移）。`addressee_names` 只用来补
    "群里怎么叫她"（猫娘 / 喵喵 / 小名），那部分本体不可能知道。

    名字的顺序有意义吗？没有 —— `matched_name` 自己取最长的那个。
    """
    names: list[str] = [str(name).strip() for name in host_names if str(name).strip()]
    raw = (settings or {}).get("addressee_names")
    if isinstance(raw, list):
        names.extend(str(item).strip() for item in raw if str(item).strip())
    elif isinstance(raw, str) and raw.strip():
        # 兼容手改配置文件写成逗号串的情况（与其它 list 键的容错口径一致）。
        names.extend(part.strip() for part in raw.split(",") if part.strip())
    # 去重但保序：日志里读起来与配置一致，便于对照。
    seen: set[str] = set()
    out: list[str] = []
    for name in names:
        if name not in seen:
            seen.add(name)
            out.append(name)
    return tuple(out)


def resolve_addressee(
    *,
    self_id: str = "",
    text: str = "",
    is_at_bot: bool = False,
    is_reply_to_bot: bool = False,
    mentions_all: bool = False,
    mentioned_user_ids: Iterable[str] = (),
    quoted_message_id: str = "",
    quoted_sender_id: str = "",
    segments: Any = None,
    names: Iterable[str] = (),
    first_reply_after_own_speech: bool = False,
) -> AddresseeVerdict:
    """按判据阶梯给出一条消息的「在跟谁说话」结论。

    所有输入都由调用方提供（本模块不读配置、不查库、不发请求），因此可以纯函数地
    单测每一条判据 —— 这也正是把它从 `attention_gate_service` 里拆出来的理由：
    那段 `evaluate()` 已经 800 行，再塞一层判定没人能读。
    """
    me = str(self_id or "").strip()
    others = [str(uid).strip() for uid in mentioned_user_ids if str(uid).strip() and str(uid).strip() != me]
    first_at = first_at_target(segments)
    if me and first_at == me:
        first_at = ""            # 段首 @ 的就是她 → 不是"@ 别人"，见 first_at_other

    if is_at_bot or (me and me in {str(uid).strip() for uid in mentioned_user_ids}):
        return AddresseeVerdict(
            kind=KIND_AT_BOT,
            target_id=me,
            first_at=first_at,
            evidence="at_bot",
        )
    if is_reply_to_bot:
        return AddresseeVerdict(
            kind=KIND_REPLY_TO_BOT,
            target_id=me,
            first_at=first_at,
            evidence="reply_to_bot",
        )
    if mentions_all:
        return AddresseeVerdict(kind=KIND_AT_ALL, first_at=first_at, evidence="at_all")
    if quoted_message_id:
        # 到了这里 `is_reply_to_bot` 已经是假 → 被引用的是**别人**。uid 拿得到就带上，
        # 拿不到（老形态只给 message_id）也不影响结论：引用第三方这件事本身就成立。
        target = str(quoted_sender_id or "").strip()
        return AddresseeVerdict(
            kind=KIND_REPLY_TO_OTHER,
            target_id=target,
            first_at=first_at,
            evidence=f"reply_to_other:{target}" if target else "reply_to_other",
        )
    if others:
        return AddresseeVerdict(
            kind=KIND_AT_OTHER,
            target_id=others[0],
            first_at=first_at,
            evidence=f"at_other:{others[0]}" + (f"(+{len(others) - 1})" if len(others) > 1 else ""),
        )
    hit = matched_name(text, names)
    if hit:
        return AddresseeVerdict(
            kind=KIND_NAMED_BOT, first_at=first_at, matched_name=hit, evidence=f"named:{hit}",
        )
    if first_reply_after_own_speech:
        return AddresseeVerdict(kind=KIND_AFTER_OWN_SPEECH, first_at=first_at, evidence="after_own_speech")
    return AddresseeVerdict(kind=KIND_GROUP_CHATTER, first_at=first_at, evidence="none")


def _nickname_from(data: list[Any], full: str) -> str:
    """人设里的「昵称」= 名字的简称。`data[3]` 是 `{她的名字: {属性…}}`。

    全名对不上时（人设刚改过名、或键的写法不同）退一步：人设里只有一个角色就用它 ——
    这份数据的用途是"她叫什么"，不是"校验配置一致性"。宿主自己的配置页也把「昵称」
    当一等字段（`characters_router/crud.py` 的 `fields_to_translate=['昵称']`）。
    """
    if len(data) < 4 or not isinstance(data[3], dict):
        return ""
    basic = data[3]
    bucket = basic.get(full) if full else None
    if not isinstance(bucket, dict):
        candidates = [value for value in basic.values() if isinstance(value, dict)]
        bucket = candidates[0] if len(candidates) == 1 else None
    if not isinstance(bucket, dict):
        return ""
    return str(bucket.get("昵称") or "").strip()


def host_names(plugin: Any) -> tuple[str, ...]:
    """从本体取她的名字**与名字的简称**（取不到就给空元组，绝不因为取不到而拦住判据）。

    与 `__init__._free_route_system_prompt` 同源同口径（那份人设文本的 index 约定是
    本体定的：`data[0]` 主人名、`data[1]` 她的名字、`data[3]` 她的档案）。

    简称必须**自动**带上：使用者平时就是这么叫她的（2026-09-29 口径：「不需要猫娘，
    就是她自己的名字以及名字的简称」），而它本来就在人设里 —— 让用户再去
    `addressee_names` 里抄一遍，就是本仓库一贯反对的"抄一遍就会随本体漂移"。
    `addressee_names` 只补人设不可能知道的叫法（群里起的外号）。

    取不到时只剩用户配的别名 —— 是**降级**，不是失败：判据 6 档里少一档，其余六档照常工作。
    """
    try:
        from utils.config_manager import get_config_manager

        data = list(get_config_manager().get_character_data() or [])
    except Exception:
        return ()
    full = str(data[1] or "").strip() if len(data) > 1 else ""
    names: list[str] = [full] if full else []
    nickname = _nickname_from(data, full)
    if nickname and nickname not in names:
        names.append(nickname)
    return tuple(names)


def segments_of(message: Any, enricher: Any = None) -> Any:
    """取原始消息段（数组或 CQ 串）。

    复用 `QQMessageEnricher._message_segments` —— 它是"主消息的段到底在哪个键下"这件事
    在本仓库的**唯一真相**（历史上五处入口各写一种写法，只有一种是对的，见那里的
    docstring）。取不到 enricher 时返回 None，判据降级（首段 @ 那一档失效）。
    """
    if enricher is None or not isinstance(message, dict):
        return None
    try:
        return enricher._message_segments(message)
    except Exception:
        return None


def verdict_kwargs(verdict: AddresseeVerdict | None) -> dict[str, str]:
    """给 `QQReplyRequest` 用的三个字段（None → 全空串，等于"没有结论"）。"""
    if verdict is None:
        return {"addressee_kind": "", "addressee_target": "", "addressee_evidence": ""}
    return {
        "addressee_kind": verdict.kind,
        "addressee_target": verdict.target_id,
        "addressee_evidence": verdict.evidence,
    }


def verdict_from_request(request: Any) -> AddresseeVerdict | None:
    """从 `QQReplyRequest` 还原结论（给提示词层用）。

    只还原得出"是什么档、指向谁"这两个事实 —— `first_at` 与 `matched_name` 留在
    dispatcher 那边（它们只服务减分与硬门控，不服务文案）。请求里没有结论
    （开放平台通道、合成轮、旁路调用）时返回 None，提示词层退回改动前的 6 个标签。
    """
    kind = str(getattr(request, "addressee_kind", "") or "").strip()
    if not kind:
        return None
    return AddresseeVerdict(
        kind=kind,
        target_id=str(getattr(request, "addressee_target", "") or "").strip(),
        evidence=str(getattr(request, "addressee_evidence", "") or "").strip(),
    )


def aim_label(verdict: AddresseeVerdict | None, *, target_label: str = "") -> str:
    """把结论渲染成一句话（提示词层与日志共用，避免两处措辞各写一份）。

    `target_label` 是调用方能给出的**对象名字**（如被引用者在本地的配置昵称）。
    拿不到名字时一律说"别人"，**不退化成一个裸 QQ 号** —— 提示词里
    "对方在回复20000的消息"对人（模型）没有信息量，只是把内部 id 摆到了台面上；
    指向的对象在日志与 trace 里有（`evidence`），那里才是排查要看的地方。
    """
    if verdict is None:
        return ""
    who = str(target_label or "").strip() or "别人"
    if verdict.kind == KIND_REPLY_TO_OTHER:
        return f"对方在回复{who}的消息 —— 这条不是冲你来的"
    if verdict.kind == KIND_AT_OTHER:
        return f"对方在跟{who}说话 —— 这条不是冲你来的"
    if verdict.kind == KIND_NAMED_BOT:
        return "这条消息里提到了你（没 @ 你）—— 大概率是在叫你"
    if verdict.kind == KIND_AT_ALL:
        return "对方在对全群广播（@全体成员）—— 与你或群里正在聊的事相关时可以回"
    return ""
