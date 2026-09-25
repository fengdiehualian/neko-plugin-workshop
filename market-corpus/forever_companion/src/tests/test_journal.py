"""个人日记（书页式）纯逻辑的单测（独立仓库可跑，不依赖宿主）。

覆盖：续写/翻页/写满自动翻页/页数淘汰、续写衔接句、邀请节奏判定、
旧版潮汐周记迁移、页眉统计。journal_write/journal_due 支持显式 now 注入，
不依赖系统时钟。运行方式：uv run python -m pytest tests -q
"""

import asyncio
import io
import sys
import tokenize
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from forever_companion.core.journal import (  # noqa: E402
    assemble_journal_entry,
    has_journal_content,
    journal_due,
    journal_write,
    migrate_weekly_to_pages,
    next_page_no,
    page_header,
)

NOW = datetime(2026, 8, 29, 12, 0, 0, tzinfo=timezone.utc)


# ---------- 写入：续写 / 翻页 / 淘汰 ----------


def test_first_write_creates_page_one() -> None:
    pages, page_no, tail, evicted = journal_write([], "第一篇", False, now=NOW, affect=0.3)
    assert page_no == 1
    assert tail == ""
    assert evicted == []
    assert pages[0]["page_no"] == 1
    assert pages[0]["started_at"] == NOW.isoformat(timespec="seconds")
    entry = pages[0]["entries"][0]
    assert entry["text"] == "第一篇"
    assert entry["affect"] == 0.3


def test_continue_appends_to_current_page() -> None:
    pages, page_no, tail, _ = journal_write([], "第一段", False, now=NOW)
    later = NOW + timedelta(days=1)
    pages2, page_no2, tail2, _ = journal_write(pages, "第二段", False, now=later)
    assert page_no2 == 1  # 还在同一页
    assert "第一段" in tail2  # 续写衔接带出上次写的
    assert [e["text"] for e in pages2[0]["entries"]] == ["第一段", "第二段"]
    # 原 pages 不被 mutate
    assert len(pages[0]["entries"]) == 1


def test_new_page_flag_starts_fresh_page_with_previous_tail() -> None:
    pages, _, _, _ = journal_write([], "上一页的结尾", False, now=NOW)
    later = NOW + timedelta(days=3)
    pages2, page_no2, tail2, _ = journal_write(pages, "新的一页", True, now=later)
    assert page_no2 == 2
    # 翻页时衔接上一页最后一段（带落笔日期前缀）
    assert tail2.endswith("上一页的结尾")
    assert "写的）" in tail2
    assert pages2[1]["started_at"] == later.isoformat(timespec="seconds")


def test_full_page_auto_flips() -> None:
    pages: list[dict] = []
    for i in range(8):  # 页容量 8
        pages, _, _, _ = journal_write(pages, f"段{i}", False, now=NOW + timedelta(minutes=i))
    assert len(pages) == 1
    pages, page_no, _, _ = journal_write(pages, "第9段", False, now=NOW)
    assert page_no == 2  # 写满自动翻页
    assert [e["text"] for e in pages[0]["entries"]][-1] == "段7"


def test_page_cap_evicts_oldest_into_evicted_list() -> None:
    pages: list[dict] = []
    all_evicted: list[dict] = []
    for i in range(60):  # 造 60 页（上限 52）
        pages, _, _, evicted = journal_write(pages, f"页{i}", True, now=NOW + timedelta(days=i))
        all_evicted.extend(evicted)
    assert len(pages) == 52
    assert pages[0]["page_no"] == 9  # 最旧的 8 页被淘汰
    assert pages[-1]["page_no"] == 60
    # 1.3.0 藏书阁：淘汰页随返回值带出（时间正序、内容完整），不再是静默丢弃
    assert [p["page_no"] for p in all_evicted] == list(range(1, 9))
    assert all_evicted[0]["entries"][0]["text"] == "页0"
    # 淘汰后新页号仍连续递增（累计页码永不重编，面板显示层据此说真话）
    assert [p["page_no"] for p in pages] == list(range(9, 61))


# ---------- 页码单一来源：邀请文案与写入页码不得各算一套 ----------


def test_next_page_no_empty_book_starts_at_one() -> None:
    assert next_page_no([]) == 1


def test_next_page_no_survives_eviction_where_len_plus_one_lies() -> None:
    """淘汰发生后 `len(pages)+1` 与真实页码分叉——邀请文案必须跟真实页码。"""
    pages: list[dict] = []
    for i in range(60):
        pages, _, _, _ = journal_write(pages, f"页{i}", True, now=NOW + timedelta(days=i))
    assert len(pages) == 52  # 活架只剩 52 页
    assert pages[-1]["page_no"] == 60
    assert next_page_no(pages) == 61
    # 旧算法会递出“第 53 页”：与她真写下去拿到的页码对不上
    assert len(pages) + 1 != next_page_no(pages)


def test_next_page_no_matches_what_journal_write_assigns() -> None:
    """不变式：next_page_no 预告的页码 == journal_write 实际落笔的页码。"""
    pages: list[dict] = []
    for i in range(5):
        pages, _, _, _ = journal_write(pages, f"预热{i}", True, now=NOW + timedelta(days=i))
    predicted = next_page_no(pages)
    _, assigned, _, _ = journal_write(pages, "下一笔", True, now=NOW + timedelta(days=99))
    assert assigned == predicted


def test_invite_text_does_not_recompute_page_number_from_list_length() -> None:
    """静态门：禁止在注入文案里就地用 `len(...)+1` 推页码。

    1.3.0 把淘汰改成“搬进藏书阁”后，活架长度不再等于累计页数，
    日记邀请文案长期报着比真实页码小的数——显示层说谎。
    页码唯一合法来源是 core.journal.next_page_no。

    用 tokenize 只看代码 token，跳过字符串/注释/docstring：否则本仓自己的
    说明文字（如 next_page_no 的 docstring）会把门误抢。
    """
    root = Path(__file__).resolve().parents[1]
    here = Path(__file__).resolve()
    skipped_names = {"COMMENT", "STRING", "FSTRING_START", "FSTRING_MIDDLE", "FSTRING_END", "NL", "NEWLINE", "INDENT", "DEDENT"}
    skipped = {getattr(tokenize, n) for n in skipped_names if hasattr(tokenize, n)}

    offenders: list[str] = []
    for py in sorted(root.rglob("*.py")):
        if "vendor" in py.parts or py.resolve() == here:
            continue
        source = py.read_text(encoding="utf-8")
        lines = source.splitlines()
        # 每行收集：出现过 len( 以及出现过 `+ 1`
        has_len: set[int] = set()
        has_plus_one: set[int] = set()
        try:
            tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
        except tokenize.TokenError:
            continue
        for index, tok in enumerate(tokens):
            if tok.type in skipped:
                continue
            if tok.type == tokenize.NAME and tok.string == "len":
                has_len.add(tok.start[0])
            if (
                tok.type == tokenize.OP
                and tok.string == "+"
                and index + 1 < len(tokens)
                and tokens[index + 1].type == tokenize.NUMBER
                and tokens[index + 1].string == "1"
            ):
                has_plus_one.add(tok.start[0])
        for lineno in sorted(has_len & has_plus_one):
            if "页" in (lines[lineno - 1] if lineno <= len(lines) else ""):
                offenders.append(f"{py.relative_to(root)}:{lineno}: {lines[lineno - 1].strip()[:90]}")
    assert not offenders, (
        "页码必须走 core.journal.next_page_no（活架长度≠累计页数），不得就地 len()+1：\n  "
        + "\n  ".join(offenders)
    )


def test_entry_text_truncated_to_cap() -> None:
    pages, _, _, _ = journal_write([], "长" * 2000, False, now=NOW)
    # 0.7.1 起结构化四字段拼装，单条上限放宽到 900
    assert len(pages[0]["entries"][0]["text"]) == 900


# ---------- 邀请节奏 ----------


def test_due_when_never_written() -> None:
    due, reason = journal_due([], now=NOW, interval_days=7)
    assert due is True and reason == "due"


def test_not_due_within_interval() -> None:
    pages, _, _, _ = journal_write([], "昨天写的", False, now=NOW - timedelta(days=1))
    due, reason = journal_due(pages, now=NOW, interval_days=7)
    assert due is False and reason == "recent_write"


def test_due_after_interval_even_without_new_pages() -> None:
    pages, _, _, _ = journal_write([], "八天前写的", False, now=NOW - timedelta(days=8))
    due, _ = journal_due(pages, now=NOW, interval_days=7)
    assert due is True


def test_continue_resets_cadence() -> None:
    # 首段写在 9 天前，但续写发生在 1 天前 → 节奏以"最近一次落笔"起算
    pages, _, _, _ = journal_write([], "九天前", False, now=NOW - timedelta(days=9))
    pages, _, _, _ = journal_write(pages, "昨天续写", False, now=NOW - timedelta(days=1))
    due, reason = journal_due(pages, now=NOW, interval_days=7)
    assert due is False and reason == "recent_write"


# ---------- 旧周记迁移 ----------


def test_migrate_weekly_records_to_pages() -> None:
    weekly = [
        {"ts": "2026-08-01T10:00:00+00:00", "summary": "第一周小结", "highlight": "那场雨"},
        {"ts": "2026-08-08T10:00:00+00:00", "summary": "第二周小结", "highlight": ""},
    ]
    pages = migrate_weekly_to_pages(weekly)
    assert len(pages) == 2
    assert pages[0]["page_no"] == 1
    assert pages[0]["legacy"] is True
    assert "第一周小结" in pages[0]["entries"][0]["text"]
    assert "那场雨" in pages[0]["entries"][0]["text"]
    # 无 highlight 的不拼那句
    assert "印象最深" not in pages[1]["entries"][0]["text"]


def test_migrate_empty_and_bad_data() -> None:
    assert migrate_weekly_to_pages([]) == []
    assert migrate_weekly_to_pages(["bad", {"summary": ""}, None]) == []


# ---------- 页眉统计 ----------


def test_page_header_stats() -> None:
    pages, _, _, _ = journal_write([], "a", False, now=NOW, affect=0.4)
    pages, _, _, _ = journal_write(pages, "b", False, now=NOW, affect=-0.2)
    header = page_header(pages[0])
    assert header["page_no"] == 1
    assert header["entry_count"] == 2
    assert header["mood_avg"] == 0.1
    assert header["last_ts"] == pages[0]["entries"][-1]["ts"]
    assert header["legacy"] is False


def test_page_header_without_affect_snapshots() -> None:
    header = page_header({"page_no": 3, "started_at": "t", "entries": [{"ts": "t", "text": "x"}]})
    assert header["mood_avg"] is None
    assert header["entry_count"] == 1


# ---------- 结构化字段拼装 ----------


def test_assemble_all_sections_labeled() -> None:
    text = assemble_journal_entry(
        events="他带我去了海边。", thoughts="我有点紧张。", feelings="喜欢多一点。", extra="下次想看日出。"
    )
    lines = text.splitlines()
    assert lines[0] == "【这段时间】他带我去了海边。"
    assert lines[1] == "【我在想】我有点紧张。"
    assert lines[2] == "【对他的感觉】喜欢多一点。"
    assert lines[3] == "【想说的】下次想看日出。"


def test_assemble_skips_empty_fields() -> None:
    text = assemble_journal_entry(thoughts="只想写心事。")
    assert text == "【我在想】只想写心事。"
    # 只写 extra：不加标题，尊重自由发挥
    assert assemble_journal_entry(extra="随手一句话") == "随手一句话"
    assert assemble_journal_entry() == ""


def test_assemble_truncates_each_field() -> None:
    text = assemble_journal_entry(events="长" * 500)
    # events 单字段截到 300
    assert len(text) == len("【这段时间】") + 300


def test_has_journal_content() -> None:
    assert has_journal_content("", "有内容", "", "") is True
    assert has_journal_content("   ", "", "", "") is False
    assert has_journal_content() is False


# ---------- 1.2.3：递邀双模式（手动 respond 当面递到 / 周期与冷却 read 静默） ----------


def run(coro):
    return asyncio.run(coro)


def test_forced_invite_delivers_respond_then_cooldown_read(plugin_factory) -> None:
    """面板 force 首递走 respond（当场起轮她立刻收到）；10 分钟内再按回落 read 补递。"""
    p = plugin_factory()
    shard = run(p._ensure_shard("default"))
    invited, deliver = run(p._maybe_journal_invite("default", shard, force=True))
    assert (invited, deliver) == (True, "respond")
    push = p._pushed[-1]
    assert push["ai_behavior"] == "respond"
    assert push["visibility"] == []
    text = push["parts"][0]["text"]
    assert "[潮汐·日记邀请]" in text and "{MASTER_NAME}" in text
    assert push["coalesce_key"] == "forever_companion.journal_invite"
    assert push["metadata"]["deliver"] == "respond"
    assert p._journal_invite_pending(shard) is True, "递出即挂起，她落笔才解除"

    # 冷却窗口内（刚递出）：再 force 不重复起轮，改 read 补递
    invited2, deliver2 = run(p._maybe_journal_invite("default", shard, force=True))
    assert (invited2, deliver2) == (True, "read")
    assert p._pushed[-1]["ai_behavior"] == "read"
    assert "（内心状态提醒）" in p._pushed[-1]["parts"][0]["text"]

    # 手动把水位拨回 11 分钟前 → 冷却已过，又回到 respond 档
    shard.last_journal_invite_ts -= 11 * 60
    invited3, deliver3 = run(p._maybe_journal_invite("default", shard, force=True))
    assert (invited3, deliver3) == (True, "respond")


def test_periodic_invite_stays_read(plugin_factory) -> None:
    """tick 周期递邀保持 read：安静的生命节律不该起轮打扰。"""
    p = plugin_factory()
    run(p._ensure_shard("default"))
    invited, deliver = run(p._maybe_journal_invite("default", p._get_shard("default")))
    assert (invited, deliver) == (True, "read")
    assert p._pushed[-1]["ai_behavior"] == "read"


def test_invite_gated_by_switches(plugin_factory) -> None:
    """三道闸任一关闭都拒绝递邀（fail-closed 不破）。"""
    p = plugin_factory()
    shard = run(p._ensure_shard("default"))
    p._journal_cfg["enabled"] = False
    assert run(p._maybe_journal_invite("default", shard, force=True)) == (False, "")
    p._journal_cfg["enabled"] = True
    p._mood_cfg["enabled"] = False
    assert run(p._maybe_journal_invite("default", shard, force=True)) == (False, "")


def test_invite_journal_entry_reports_mode(plugin_factory) -> None:
    """面板入口四态码：respond 当面递到 / read 冷却内悄悄提醒（i18n 契约第九轮：
    后端只回 invited/mode 码，文案由 panel.journal.invitedRespond/invitedQuiet 翻译）。"""
    p = plugin_factory()
    run(p._ensure_shard("default"))
    res = run(p.invite_journal())
    v = res.value
    assert v["invited"] is True and v["mode"] == "respond"
    assert "note" not in v, "面板可见文案不再由后端发人话句（tests/test_i18n_contract.py 钉死）"
    res2 = run(p.invite_journal())
    assert res2.value["invited"] is True and res2.value["mode"] == "read"


# ---------- 1.2.4 审查修复：递邀的提交结果必须看 ----------


def test_forced_invite_reports_transport_failure(plugin_factory) -> None:
    """传输拒收时不得报「已递到」：返回 (False, failed)、手动档回滚水位可立刻重按，
    且不挂「正等她落笔」的假提示（面板文案与挂起态都由水位驱动）。"""
    p = plugin_factory()
    shard = run(p._ensure_shard("default"))
    p.push_message = lambda **kw: {"submitted": False, "reason": "backpressure"}
    assert run(p._maybe_journal_invite("default", shard, force=True)) == (False, "failed")
    assert shard.last_journal_invite_ts == 0.0, "手动档失败要回滚水位，用户可以立刻重按"
    assert p._journal_invite_pending(shard) is False

    v = run(p.invite_journal()).value
    assert v["invited"] is False and v["mode"] == "failed"
    assert "note" not in v, "失败语义由 mode=failed 码承载（面板 inviteFailed 支），不再发裸串"


def test_periodic_invite_failure_keeps_watermark(plugin_factory) -> None:
    """tick 周期档传输拒收：如实报 failed，但水位不回滚——通道一直坏时
    每趟监督都重推会刷屏，让它按 24h 节流等下一轮。"""
    p = plugin_factory()
    shard = run(p._ensure_shard("default"))
    p.push_message = lambda **kw: {"submitted": False, "reason": "transport_unavailable"}
    assert run(p._maybe_journal_invite("default", shard)) == (False, "failed")
    assert shard.last_journal_invite_ts > 0.0, "周期档失败保留水位，按 24h 节流重试"


def test_push_stub_without_submitted_counts_as_success(plugin_factory) -> None:
    """只有显式 submitted=False 才算失败：返回 None 或不含该键的旧形状按成功，
    避免桩实现/未来 SDK 变更把正常递邀误判成传输故障。"""
    p = plugin_factory()
    shard = run(p._ensure_shard("default"))
    p.push_message = lambda **kw: None
    assert run(p._maybe_journal_invite("default", shard, force=True)) == (True, "respond")
    p.push_message = lambda **kw: {}
    shard.last_journal_invite_ts = 0.0
    assert run(p._maybe_journal_invite("default", shard, force=True)) == (True, "respond")


def test_debug_journal_force_ignores_cadence(plugin_factory) -> None:
    """debug_journal(force=true) 必须真能强制递出：旧实现漏传 force，走非 force 档
    还有一道 journal_due（距上次落笔满 interval_days）闸，昨天刚写过日记的机器上
    静默 invited=false，与 README「立即推一次邀请」和 1.2.3 注释都不符。"""
    p = plugin_factory()
    shard = run(p._ensure_shard("default"))
    recent = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(timespec="seconds")
    shard.journal = [{"page_no": 1, "started_at": recent, "entries": [{"ts": recent, "text": "昨天写过"}]}]
    v = run(p._debug_journal(force=True)).value
    assert v["due"] is False, "前置条件：按节奏她还不该被邀请"
    assert v["invited"] is True, "force 档要跳过 7 天节奏，立即递一次"
    assert v["deliver"] == "respond"
