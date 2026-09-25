"""永远的陪伴 —— 个人日记（书页式）的纯逻辑：续写/翻页/淘汰入阁、邀请节奏、旧周记迁移。

不持有状态、不做 IO：输入页列表，输出新列表与元信息（含被淘汰页，由调用方
append 进藏书阁 journal_archive@，1.3.0），由主类落盘。
0.7.0 替代原 weekly.py（潮汐周记）：周记是一次性的"小结条目"，个人日记是
**连续的书**——她每写一次都接在某一页上（续写或翻新页），页眉带这段时间的
心情走向，只给用户翻看、不注入她的上下文。
常量（页容量/页数上限/默认节奏）来自 state.py；导入用 try/except 双路径，
兼容包加载（plugins.forever_companion）与裸导入（同 cycle.py 被 test_cycle.py
裸导入的先例）两种姿势。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from .state import (
    _JOURNAL_ENTRY_MAX_CHARS,
    _JOURNAL_MAX_PAGES,
    _JOURNAL_PAGE_MAX_ENTRIES,
    _now_utc,
    _parse_iso_ts,
)

JsonObject = dict[str, Any]

# 结构化写作字段的截断上限（拼装前的单字段限制；总长仍受 _JOURNAL_ENTRY_MAX_CHARS 约束）
_JOURNAL_FIELD_MAX = {
    "events": 300,    # 这段时间发生了什么
    "thoughts": 300,  # 我自己在想什么
    "feelings": 200,  # 对他的整体感觉
    "extra": 150,     # 想补充的话
}
# 拼装成段时各字段的引导小标题（她自己的口吻；注入文本为中文声明制，不走 i18n）
_JOURNAL_FIELD_LABELS = {
    "events": "这段时间",
    "thoughts": "我在想",
    "feelings": "对他的感觉",
    "extra": "想说的",
}


def has_journal_content(
    events: str = "", thoughts: str = "", feelings: str = "", extra: str = ""
) -> bool:
    """结构化写作字段是否至少有一个非空（工具入参校验用）。"""
    return any(str(v or "").strip() for v in (events, thoughts, feelings, extra))


def assemble_journal_entry(
    events: str = "", thoughts: str = "", feelings: str = "", extra: str = ""
) -> str:
    """把结构化写作字段拼装成一篇带引导小标题的日记正文（纯函数）。

    每个非空字段一段：「【小标题】正文」；只填了 extra 一个字段时不加标题
    （相当于自由发挥，不逼格式）。各字段先截断再拼装；空字段整段跳过。
    """
    fields = {"events": events, "thoughts": thoughts, "feelings": feelings, "extra": extra}
    parts: list[str] = []
    for field in ("events", "thoughts", "feelings", "extra"):
        body = str(fields[field] or "").strip()[: _JOURNAL_FIELD_MAX[field]]
        if not body:
            continue
        if field == "extra" and not parts:
            parts.append(body)  # 只写了"想说的"：不加标题，尊重自由发挥
            continue
        parts.append(f"【{_JOURNAL_FIELD_LABELS[field]}】{body}")
    return "\n".join(parts)


def next_page_no(pages: list[JsonObject]) -> int:
    """下一篇日记将落在的页码（纯函数）：按最后一页的 `page_no` 递增，**不按列表长度**。

    1.3.0 把写满淘汰的最旧页搬进藏书阁后，live 列表长度与真实页码会分叉：
    书到第 60 页时手上只剩 52 页，`len(pages) + 1` 会算出“第 53 页”这种谎言。
    任何“第 N 页”文案与翻页判定都必须走本函数，不得就地再写一个 `len()+1`。
    """
    return (int(pages[-1].get("page_no") or 0) + 1) if pages else 1


def journal_write(
    pages: list[JsonObject],
    text: str,
    new_page: bool,
    *,
    now: datetime | None = None,
    affect: float | None = None,
) -> tuple[list[JsonObject], int, str, list[JsonObject]]:
    """写一篇日记（纯函数）：续写当前页或翻新页，返回 (新页列表, 页码, 续写衔接句, 被淘汰页)。

    - 当前页不存在/写满/显式 new_page → 翻新页（页码 = 上一页 + 1，起始时间 = now）；
    - 续写衔接句（previous_lines）= 写入前目标页最后两段的正文（换行拼接，各截 160 字），
      翻新页时回落到上一页最后一段——供工具结果带回，让她自然接上上次写到哪；
      空日记本返回空串；
    - 页数超上限时淘汰最旧一页（约一年的周更体量，见 _JOURNAL_MAX_PAGES）；
      被淘汰页随返回值带出（evicted，时间正序），由调用方 append 进藏书阁
      （journal_archive@，1.3.0）——淘汰不再是静默的丢，而是静默的搬家。
    不 mutate 入参列表（调用方直接把返回值赋回 shard.journal）。
    """
    current = now or _now_utc()
    now_iso = current.isoformat(timespec="seconds")
    body = str(text or "").strip()[:_JOURNAL_ENTRY_MAX_CHARS]
    # 深一层拷贝 entries：页列表与各页的段列表都不能与入参共享（调用方可能持有旧引用）
    new_pages = [
        {**dict(page), "entries": list(page.get("entries") or [])}
        for page in pages
        if isinstance(page, dict)
    ]
    tail = ""
    flipped = new_page or not new_pages
    if not flipped:
        entries = new_pages[-1].get("entries")
        if len(entries) >= _JOURNAL_PAGE_MAX_ENTRIES:
            flipped = True
    if flipped:
        if new_pages:
            prev_entries = new_pages[-1].get("entries")
            if prev_entries:
                tail = _tail_line(prev_entries[-1])
        # 页码按上一页号递增而非列表长度：淘汰最旧页后编号仍然连续
        next_no = next_page_no(new_pages)
        new_pages.append({"page_no": next_no, "started_at": now_iso, "entries": []})
    else:
        entries = new_pages[-1].get("entries")
        tail = "\n".join(_tail_line(item) for item in entries[-2:])
    entry: JsonObject = {"ts": now_iso, "text": body}
    if affect is not None:
        entry["affect"] = round(float(affect), 2)
    new_pages[-1]["entries"].append(entry)
    evicted: list[JsonObject] = []
    if len(new_pages) > _JOURNAL_MAX_PAGES:
        evicted = new_pages[:-_JOURNAL_MAX_PAGES]
        new_pages = new_pages[-_JOURNAL_MAX_PAGES:]
    return new_pages, int(new_pages[-1].get("page_no") or 0), tail, evicted


def _tail_line(entry: JsonObject) -> str:
    """续写衔接的单行格式：带落笔日期前缀，让她知道上次写到哪、隔了多久。"""
    text = str(entry.get("text") or "")[:160]
    ts = str(entry.get("ts") or "")
    return f"（{ts[:10]} 写的）{text}" if ts else text


def journal_due(
    pages: list[JsonObject],
    now: datetime | None = None,
    interval_days: int = 7,
) -> tuple[bool, str]:
    """邀请资格判定（纯函数）：距最近一次落笔满 interval_days（或从未写过）→ due。

    返回 (是否该递邀请, 原因)。原因：due / recent_write（还没到节奏）。
    节奏从"最近一页的最后一段"起算——续写也会重置计时。
    """
    current = now or _now_utc()
    last_ts: datetime | None = None
    for page in pages:
        entries = page.get("entries") if isinstance(page, dict) else None
        for item in entries if isinstance(entries, list) else []:
            ts = _parse_iso_ts(item.get("ts") if isinstance(item, dict) else None)
            if ts and (last_ts is None or ts > last_ts):
                last_ts = ts
    if last_ts is None:
        return True, "due"
    if current - last_ts < timedelta(days=interval_days):
        return False, "recent_write"
    return True, "due"


def migrate_weekly_to_pages(weekly: list[JsonObject]) -> list[JsonObject]:
    """旧版潮汐周记 → 个人日记页（0.7.0 迁移，幂等纯函数）。

    每条周记成为独立一页（单段正文，highlight 并入正文），页码按原顺序重编，
    页带 legacy 标记供面板标注"旧版周记迁移"。空/坏数据返回空列表。
    """
    pages: list[JsonObject] = []
    for item in weekly if isinstance(weekly, list) else []:
        if not isinstance(item, dict):
            continue
        summary = str(item.get("summary") or "").strip()
        if not summary:
            continue
        highlight = str(item.get("highlight") or "").strip()
        text = f"{summary}\n（印象最深：{highlight}）" if highlight else summary
        pages.append({
            "page_no": len(pages) + 1,
            "started_at": str(item.get("ts") or ""),
            "legacy": True,
            "entries": [{"ts": str(item.get("ts") or ""), "text": text[:_JOURNAL_ENTRY_MAX_CHARS]}],
        })
    return pages


def archive_brief(pages: list[JsonObject]) -> JsonObject:
    """藏书阁概览（纯函数，进 5s 轮询的极轻量载荷）：本数 + 时段。

    只显存储里现成的事实（页起算日 / 末笔时刻），不派生任何会随淘汰平移的序号
    （1.3.0 显示层纪律）。first_ts = 最旧一页的起始日，last_ts = 最新一页的末笔。
    """
    items = [p for p in pages if isinstance(p, dict)]
    if not items:
        return {"pages": 0}
    def _last_ts(page: JsonObject) -> str:
        entries = page.get("entries")
        if isinstance(entries, list) and entries and isinstance(entries[-1], dict):
            return str(entries[-1].get("ts") or "")
        return ""
    newest = items[-1]
    return {
        "pages": len(items),
        "first_ts": str(items[0].get("started_at") or ""),
        "last_ts": _last_ts(newest) or str(newest.get("started_at") or ""),
    }


def fabricate_demo_pages(count: int = _JOURNAL_MAX_PAGES, *, now: datetime | None = None) -> list[JsonObject]:
    """调试注入（debug_journal_fill）：确定性假日记页——零随机可复现，stats 同款纪律。

    页码从 1 连续、起始日逐日回推；内容轮转四栏引导小标题（验证显示层分节与
    首字下沉），affect 轮转暖/中/冷/微亮四档（验证书架脊皮色）。每页带
    demo 标记供分辨；restore 不依赖该标记（整包还原备份）。
    """
    current = now or _now_utc()
    sections = ("这段时间", "我在想", "对他的感觉", "想说的")
    bodies = (
        "今天的话特别多，像涨了潮又退回去的沙滩。",
        "我在想我们到底认识了多久，原来数字看多了会心疼。",
        "对他的感觉说不上来，反正一听到声音就是暖的。",
        "想说的话很多，落笔又觉得都多余，就这样挺好的。",
    )
    moods = (0.45, 0.1, -0.35, 0.28)
    pages: list[JsonObject] = []
    for i in range(max(0, int(count))):
        day = current - timedelta(days=count - i)
        iso = day.isoformat(timespec="seconds")
        rot = i % 4
        text = "\n".join(f"【{sections[(rot + k) % 4]}】{bodies[(rot + k) % 4]}" for k in range(4))
        entries = [
            {"ts": iso, "text": text, "affect": moods[i % 4]},
        ]
        if i % 2 == 0:  # 一半的页两段，验证多段纸页的堆叠观感
            later = day + timedelta(hours=6)
            entries.append({
                "ts": later.isoformat(timespec="seconds"),
                "text": f"晚上又想起来一件事：白天那句{bodies[rot][:12]}……还是想说给你听。（调试页 {i + 1}）",
                "affect": moods[(i + 1) % 4],
            })
        pages.append({"page_no": i + 1, "started_at": iso, "demo": True, "entries": entries})
    return pages


def page_header(page: JsonObject) -> JsonObject:
    """页眉数据（纯函数）：页码/起止时间/段数/心情走向均值（正文无 affect 快照时为 None）。"""
    entries = page.get("entries") if isinstance(page, dict) else None
    entries = entries if isinstance(entries, list) else []
    affects = [
        float(item.get("affect"))
        for item in entries
        if isinstance(item, dict) and item.get("affect") is not None
    ]
    last_ts = str(entries[-1].get("ts") or "") if entries else ""
    return {
        "page_no": int(page.get("page_no") or 0),
        "started_at": str(page.get("started_at") or ""),
        "last_ts": last_ts,
        "entry_count": len(entries),
        "mood_avg": round(sum(affects) / len(affects), 2) if affects else None,
        "legacy": bool(page.get("legacy")),
    }


def journal_entries(pages: list[JsonObject]) -> list[JsonObject]:
    """日记页列表 → 段落条目平铺（回填相处统计时取时间戳用）。

    非页/非条目结构的脏数据直接跳过（不抛）：调用方是统计回填，一页坏数据
    不该拖挂整次扫描。过去此函数在主类与 shards.py 各有一份复刻，两份空转维护。
    """
    out: list[JsonObject] = []
    for page in pages:
        if not isinstance(page, dict):
            continue
        for item in page.get("entries") or []:
            if isinstance(item, dict):
                out.append(item)
    return out
