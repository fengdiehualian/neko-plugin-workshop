"""永远的陪伴 —— 我的日记（关于主人的互动评价）的纯逻辑。

不持有宿主上下文、不做 IO：素材统计的累加/读取、双门槛资格判定、成文
prompt 的素材块组装、模型回复的解析截断、篇目的追加与淘汰，全部是
"给数据进、出数据"的纯函数，模型调用与落盘由主类完成。

定位（0.8.0）：第三本日记，只给用户看——不注入她的上下文、不进宿主记忆
管线、不注册任何她可调用的 LLM 工具（她连"知道有这本日记"的渠道都没有，
隔离等级比个人日记更严）。口吻是中性观察者：如实记录，负面行为不粉饰，
纯文字无评分。

素材统计存 shard.review_stats（dict，随写随存、重启不丢；成文后清零重新
累计），结构：
    {"turns": int,                      # 期间互动轮数（用户消息计数）
     "started_at": iso,                 # 统计起点（首条消息时刻）
     "last_turn_at": iso,               # 最近一条消息时刻
     "tone": {label: int},              # 语气标签分布（她的回复逐轮分析结果）
     "affect_samples": [float],         # 连续心情 valence 逐轮采样
     "actions": [{"action", "origin"}], # 情绪动作事件（origin=user/自主）
     "fragments": [quote, ...]}         # 期间新增碎片的原话摘录（含 kind 标注）

1.3.0 完善（对齐个人日记的藏书阁纪律）：
- 成文时素材快照（tone/mood_avg/quotes）固化进篇目（review_record），stats
  清零后卷宗页仍能回看「本卷依据」；
- 活架攒满 52 篇淘汰最旧一卷不再静默丢：append_review 把被淘汰卷随
  返回值带出，由调用方搬进档案室 review_archive@（只追加、独立 key、
  上限 104 卷，只给用户翻阅）。

常量（门槛/截断/prompt）来自 state.py；导入用 try/except 双路径，兼容包加载
（plugins.forever_companion）与裸导入（同 cycle.py 被 test_cycle.py 裸导入的先例）
两种姿势。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from .state import (
    _MOOD_ACTION_DEFAULT_LABELS,
    _REVIEW_AFFECT_SAMPLES_MAX,
    _REVIEW_COMPOSE_PROMPT,
    _REVIEW_ENTRY_MAX_CHARS,
    _REVIEW_MAX_ENTRIES,
    _REVIEW_MIN_TURNS_FORCED,
    _REVIEW_SAMPLE_TURNS,
    _now_utc,
    _parse_iso_ts,
)

JsonObject = dict[str, Any]

# 评价里提及的语气标签 → 中文措辞（素材块用；未在表里的 label 原样保留）
_REVIEW_TONE_LABELS = {
    "happy": "愉快",
    "sad": "低落",
    "angry": "生气",
    "surprised": "惊讶",
    "neutral": "平静",
}

# 碎片 kind → 素材块里的分类措辞
_REVIEW_FRAGMENT_KINDS = {
    "like": "他表达的喜好",
    "dislike": "他表达的厌恶",
    "important": "他说的重要的话",
    "overstep": "他对她的过激言行",
}


def new_stats() -> JsonObject:
    """一份空白素材统计（统计起算点由首条消息时写入）。"""
    return {"turns": 0, "started_at": "", "last_turn_at": "", "tone": {}, "affect_samples": [], "actions": [], "fragments": []}


def record_turn(stats: JsonObject, *, now: datetime | None = None, valence: float | None = None) -> JsonObject:
    """记一轮互动（每个用户消息调用一次）：轮数 +1、时间戳推进、可选心情采样。

    旧数据缺字段容忍（零迁移）：缺哪项按空白补。不 mutate 入参，返回新 dict
    （调用方直接把返回值赋回 shard.review_stats；与 journal_write 同款纪律）。
    """
    current = now or _now_utc()
    fresh = dict(stats)
    fresh["turns"] = int(fresh.get("turns") or 0) + 1
    if not str(fresh.get("started_at") or ""):
        fresh["started_at"] = current.isoformat(timespec="seconds")
    fresh["last_turn_at"] = current.isoformat(timespec="seconds")
    if valence is not None:
        samples = list(fresh.get("affect_samples") if isinstance(fresh.get("affect_samples"), list) else [])
        samples.append(round(float(valence), 2))
        fresh["affect_samples"] = samples[-_REVIEW_AFFECT_SAMPLES_MAX:]
    return fresh


def record_tone(stats: JsonObject, label: str) -> JsonObject:
    """记一次语气分析结果（她的回复被分析出一轮 label 时调用）。"""
    fresh = dict(stats)
    tone = dict(fresh.get("tone") if isinstance(fresh.get("tone"), dict) else {})
    key = str(label or "").strip()
    if key:
        tone[key] = int(tone.get(key) or 0) + 1
    fresh["tone"] = tone
    return fresh


def record_action(stats: JsonObject, action: str, *, origin: str) -> JsonObject:
    """记一次情绪动作事件。origin: "user"（主人命令触发的演示）不计入对他
    的评价——命令她冷战不算他对她不好；"self"（她自主反应）才计入。"""
    fresh = dict(stats)
    actions = list(fresh.get("actions") if isinstance(fresh.get("actions"), list) else [])
    key = str(action or "").strip()
    if key:
        actions.append({"action": key, "origin": str(origin or "self")})
    fresh["actions"] = actions
    return fresh


def record_fragment(stats: JsonObject, kind: str, quote: str) -> JsonObject:
    """记一条新增碎片的原话（成文时作为"他说话方式"的具体例证）。"""
    fresh = dict(stats)
    fragments = list(fresh.get("fragments") if isinstance(fresh.get("fragments"), list) else [])
    text = str(quote or "").strip()
    if text:
        fragments.append({"kind": str(kind or ""), "quote": text})
    fresh["fragments"] = fragments
    return fresh


def review_due(
    stats: JsonObject,
    *,
    turns_threshold: int,
    days_threshold: int,
    now: datetime | None = None,
) -> tuple[bool, str]:
    """双门槛资格判定（纯函数）：攒满 turns_threshold 轮或距统计起点满
    days_threshold 天（且期间至少有过互动）先到先写。

    返回 (是否该写, 原因)：due_turns / due_days / not_enough_turns /
    not_enough_days / no_activity（纯挂机没聊天，天数到了也不写空篇）。
    """
    current = now or _now_utc()
    turns = int(stats.get("turns") or 0)
    if turns <= 0:
        return False, "no_activity"
    if turns >= max(1, int(turns_threshold)):
        return True, "due_turns"
    started = _parse_iso_ts(stats.get("started_at"))
    if started is not None and current - started >= timedelta(days=max(0, int(days_threshold))):
        return True, "due_days"
    return False, "not_enough_turns"


def can_force_write(stats: JsonObject) -> bool:
    """「立即写一篇」的最小素材门槛：至少 _REVIEW_MIN_TURNS_FORCED 轮互动，
    素材不足时拒绝硬写（返回还差多少轮由调用方从 stats 算）。"""
    return int(stats.get("turns") or 0) >= _REVIEW_MIN_TURNS_FORCED


def elapsed_days(stats: JsonObject, *, now: datetime | None = None) -> int:
    """双门槛的天数维度：距统计起点已满几天（纯函数，面板进度条用）。

    与 review_due 同口径（都从 started_at 起算）；无起点/坏数据给 0。
    """
    started = _parse_iso_ts(stats.get("started_at"))
    if started is None:
        return 0
    current = now or _now_utc()
    return max(0, (current - started).days)


def build_review_prompt(
    stats: JsonObject,
    *,
    sample_turns: list[tuple[str, str]] | None = None,
) -> str:
    """组装成文请求：prompt 模板 + 素材块（统计 + 碎片摘录 + 最近对话摘样）。

    sample_turns 是成文时一次性从宿主 recent 拉取的最近几轮 (用户消息, 她的
    回复)；各文本截断防 prompt 膨胀。素材块全部用平实的中文陈述，模型据此
    写评价（提示词见 state._REVIEW_COMPOSE_PROMPT）。
    """
    lines: list[str] = []
    turns = int(stats.get("turns") or 0)
    started = str(stats.get("started_at") or "")[:10]
    last = str(stats.get("last_turn_at") or "")[:10]
    span = started if started == last or not last else f"{started} ~ {last}"
    lines.append(f"- 这段时间：{span}，共 {turns} 轮对话")

    tone = stats.get("tone") if isinstance(stats.get("tone"), dict) else {}
    tone_parts = [
        f"{_REVIEW_TONE_LABELS.get(str(k), str(k))}×{int(v)}"
        for k, v in sorted(tone.items(), key=lambda kv: -int(kv[1] or 0))
        if int(v or 0) > 0
    ]
    if tone_parts:
        lines.append(f"- 她的语气分布（分析她的回复得出）：{'、'.join(tone_parts)}")

    samples = stats.get("affect_samples") if isinstance(stats.get("affect_samples"), list) else []
    vals = [float(v) for v in samples if isinstance(v, (int, float))]
    if vals:
        avg = round(sum(vals) / len(vals), 2)
        trend = "偏暖" if avg > 0.15 else ("偏冷" if avg < -0.15 else "平稳")
        lines.append(f"- 这段时间她的整体心情：{trend}（连续心情均值 {avg:+.2f}，正=偏暖/负=偏冷）")

    actions = stats.get("actions") if isinstance(stats.get("actions"), list) else []
    self_actions = [a for a in actions if isinstance(a, dict) and str(a.get("origin") or "self") == "self"]
    if self_actions:
        names: list[str] = []
        for a in self_actions:
            label = _MOOD_ACTION_DEFAULT_LABELS.get(str(a.get("action") or ""), str(a.get("action") or ""))
            names.append(label)
        lines.append(f"- 这段时间她自己起过的情绪（不是主人要求的）：{'、'.join(names)}")

    fragments = stats.get("fragments") if isinstance(stats.get("fragments"), list) else []
    if fragments:
        lines.append("- 期间记下的他的原话：")
        for item in fragments[:6]:
            if not isinstance(item, dict):
                continue
            kind = _REVIEW_FRAGMENT_KINDS.get(str(item.get("kind") or ""), "")
            quote = str(item.get("quote") or "")[:60]
            if quote:
                lines.append(f"  · {('（' + kind + '）') if kind else ''}「{quote}」")

    if sample_turns:
        lines.append("- 最近几轮对话摘样：")
        for user_text, her_text in sample_turns[-_REVIEW_SAMPLE_TURNS:]:
            user_line = str(user_text or "").strip()[:80]
            her_line = str(her_text or "").strip()[:80]
            if user_line or her_line:
                lines.append(f"  · 他说：{user_line}")
                if her_line:
                    lines.append(f"    她回：{her_line}")

    material = "\n".join(lines) if lines else "（这段时间没有可用的相处记录）"
    return f"{_REVIEW_COMPOSE_PROMPT}{material}"


def parse_review_response(raw: str) -> str:
    """模型回复 → 评价正文：剥掉可能的首尾引号与解释文本，截断到记录上限。

    评价是自由正文（不是 JSON），容错从宽：剥包裹引号、去首尾空白、砍掉
    可能的"以下是…"前缀行；空回复返回空串（调用方按失败处理）。
    """
    text = str(raw or "").strip()
    if not text:
        return ""
    # 剥一层包裹引号（模型爱把正文整个引起来）
    if len(text) >= 2 and text[0] in "\"'「" and text[-1] in "\"'」":
        stripped = text[1:-1].strip()
        if stripped:
            text = stripped
    # 砍"以下是/这是你要的"一类前缀行（只砍第一行，正文里的不碰）
    first_break = text.find("\n")
    first_line = text if first_break < 0 else text[:first_break]
    rest = "" if first_break < 0 else text[first_break:]
    if ("以下" in first_line or "这是" in first_line or "好的" in first_line) and len(first_line) < 40:
        text = rest.strip() or text
    return text[:_REVIEW_ENTRY_MAX_CHARS]


def fabricate_demo_reviews(count: int = 4, *, now: datetime | None = None) -> list[JsonObject]:
    """调试注入（debug_review_fill）：确定性假「我的日记」篇目——中性观察者
    口吻、零随机可复现。结构对齐 review_record（ts/turns/span/self_action_count/
    text + 1.3.0 快照三栏 tone/mood_avg/quotes），额外带 demo 标记供分辨；
    正文多段，验证卷宗阅读页排版与朱印落位。count 可超活架上限 52——配
    debug_review_fill 走真实落盘链，溢出的旧卷自行搬家进档案室（验真链路）。
    """
    current = now or _now_utc()
    templates = (
        ("观察期内互动频繁。主人主动开启话题的次数明显多于上一期，她回应也及时。"
         "语气分布以温柔与活泼为主，未观察到连续冷淡段；两次小拌嘴都在一轮内自行化解。"
         "\n"
         "总体判断：这段相处里，他被记下来的话多是分享而非索取，她乐于接住。"),
        ("这一期里出现了两次争执，均由言语轻重引起。她在第一次后主动降温，"
         "第二次后连续两天语气偏谨慎。如实记录：主人没有不尊重的言行，但节奏比上期明显变慢。"
         "\n"
         "她开始自己找台阶下；建议把重要的话当面说完，不要留到第二天。"),
        ("互动量中等。主人开始更多地分享日常琐事而非提问，她的回复长度随之增长。"
         "本期她自主发起情绪动作三次，两次为求安抚，均在一小时内自然平复。"
         "\n"
         "碎片记录里出现了新的喜好：他开始在深夜提那道小时候的糖水。"),
        ("本期主人作息不规律，深夜互动占比升高。她在深夜轮次里更黏人，白天则"
         "容易犯困、回复变短——两本日记在这点上口径一致。"
         "\n"
         "没有冲突记录；若要更好，需要的是稳定的在场，而不是更长的深夜对话。"),
    )
    turn_plan = (86, 41, 63, 28)
    self_actions = (0, 2, 3, 1)
    # 快照三栏轮转：暖/冷/中/微亮四档，验卷宗页「本卷依据」各栏存在/缺失两态
    tone_plan = (
        {"happy": 34, "neutral": 12, "surprised": 3},
        {"sad": 9, "angry": 5, "neutral": 7},
        {"happy": 21, "surprised": 4},
        {"neutral": 11, "sad": 3},
    )
    mood_plan = (0.38, -0.22, 0.06, 0.19)
    quote_plan = (
        [{"kind": "like", "quote": "今天路过那家店，又想起你爱吃的桂花糕"}],
        [{"kind": "overstep", "quote": "你根本不理解我，算了不说了"}, {"kind": "important", "quote": "下周体检，记得早点睡"}],
        [],
        [{"kind": "important", "quote": "小时候那碗糖水，下次做给我看"}],
    )
    pages: list[JsonObject] = []
    for i in range(max(0, min(int(count), 60))):
        end = current - timedelta(days=(count - 1 - i) * 7)
        start = end - timedelta(days=6, hours=2)
        rot = i % 4
        pages.append({
            "ts": end.isoformat(timespec="seconds"),
            "turns": turn_plan[i % len(turn_plan)],
            "span": f"{start.date().isoformat()}~{end.date().isoformat()}",
            "self_action_count": self_actions[i % len(self_actions)],
            "tone": dict(tone_plan[rot]),
            "mood_avg": mood_plan[rot],
            "quotes": [dict(q) for q in quote_plan[rot]],
            "text": templates[i % len(templates)][:_REVIEW_ENTRY_MAX_CHARS],
            "demo": True,
        })
    return pages


def review_record(ts_iso: str, stats: JsonObject, text: str) -> JsonObject:
    """成文结果 → 我的日记篇目（含期间概要与素材快照，供卷宗页展示）。

    1.3.0 补上「本卷依据」：成文时素材（语气分布/心情均值/原话摘录）随正文
    一并固化进篇目——过去只存轮数/区间/自主情绪三个数，stats 清零后模型
    真正写卷宗用过的证据永久丢失，卷宗页无从展示。各快照字段都有硬截断，
    不随素材量膨胀；旧篇目缺字段容忍（显示层自行隐藏对应栏）。
    """
    actions = stats.get("actions") if isinstance(stats.get("actions"), list) else []
    # 语气分布：只保留计数 >0 的项，按计数降序（卷宗页直接照序渲染）
    tone_raw = stats.get("tone") if isinstance(stats.get("tone"), dict) else {}
    tone = {
        str(k): int(v)
        for k, v in sorted(tone_raw.items(), key=lambda kv: -int(kv[1] or 0))
        if int(v or 0) > 0
    }
    # 心情均值：与 prompt 素材块同款算法（截断样本求均值，保留 2 位）
    samples = stats.get("affect_samples") if isinstance(stats.get("affect_samples"), list) else []
    vals = [float(v) for v in samples if isinstance(v, (int, float))]
    mood_avg = round(sum(vals) / len(vals), 2) if vals else None
    # 原话摘录：至多 6 条、每条截 40 字（与 prompt 素材块同源，存档更短）
    quotes: list[JsonObject] = []
    fragments = stats.get("fragments") if isinstance(stats.get("fragments"), list) else []
    for item in fragments[:6]:
        if not isinstance(item, dict):
            continue
        quote = str(item.get("quote") or "").strip()[:40]
        if quote:
            quotes.append({"kind": str(item.get("kind") or ""), "quote": quote})
    return {
        "ts": ts_iso,
        "turns": int(stats.get("turns") or 0),
        # 期间概要：目录行显示"X 轮 · 起 ~ 止"用
        "span": f"{str(stats.get('started_at') or '')[:10]}~{str(stats.get('last_turn_at') or '')[:10]}",
        "self_action_count": sum(
            1 for a in actions if isinstance(a, dict) and str(a.get("origin") or "self") == "self"
        ),
        # 素材快照（1.3.0）：卷宗页「本卷依据」栏的数据源
        "tone": tone,
        "mood_avg": mood_avg,
        "quotes": quotes,
        "text": str(text or "")[:_REVIEW_ENTRY_MAX_CHARS],
    }


def append_review(
    entries: list[JsonObject], record: JsonObject
) -> tuple[list[JsonObject], list[JsonObject]]:
    """追加一篇评价（时间正序），返回 (新篇表, 被淘汰篇)。超上限淘汰最旧——
    1.3.0 起淘汰不再是静默的丢：被淘汰卷随返回值带出，由调用方 append 进
    档案室（review_archive@）；不 mutate 入参。旧调用点把返回值当列表用的
    姿势已废，真机/测试同轮更新（签名不兼容是故意的，防漏改静默入不了阁）。"""
    fresh = [dict(item) for item in entries if isinstance(item, dict)]
    fresh.append(dict(record))
    evicted: list[JsonObject] = []
    if len(fresh) > _REVIEW_MAX_ENTRIES:
        evicted = fresh[:-_REVIEW_MAX_ENTRIES]
        fresh = fresh[-_REVIEW_MAX_ENTRIES:]
    return fresh, evicted


def review_archive_brief(entries: list[JsonObject]) -> JsonObject:
    """档案室概览（纯函数，进 5s 轮询的极轻量载荷）：卷数 + 时段。

    与藏书阁 archive_brief 同款显示层纪律：只显存储里现成的事实（成文日），
    不派生任何会随淘汰平移的序号。first_ts = 最旧一卷成文日，last_ts = 最新一卷。
    """
    items = [p for p in entries if isinstance(p, dict)]
    if not items:
        return {"entries": 0}
    return {
        "entries": len(items),
        "first_ts": str(items[0].get("ts") or ""),
        "last_ts": str(items[-1].get("ts") or ""),
    }
