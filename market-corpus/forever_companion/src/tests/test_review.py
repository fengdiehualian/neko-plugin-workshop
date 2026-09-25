"""我的日记（0.8.0）测试：纯逻辑（素材统计/门槛/prompt/解析）+ 主链路
（喂入点/成文落盘/入口/隔离性/设置）。

依赖 tests/conftest.py 注入的宿主 SDK 桩与 ``tm`` / ``plugin_factory`` /
``plugin_factory_full`` fixture（不要 ``from conftest import ...``，见其头部说明）。
模型直连与槽位解析用 monkeypatch 桩掉，只验证门控与落盘语义。
"""

from __future__ import annotations

import asyncio
import time
from datetime import timedelta

import pytest


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# 纯逻辑：review.py
# ---------------------------------------------------------------------------


def test_record_turn_accumulates_and_samples(tm) -> None:
    stats = tm.new_stats()
    stats = tm.record_turn(stats, valence=0.3)
    stats = tm.record_turn(stats, valence=-0.1)
    assert stats["turns"] == 2
    assert stats["affect_samples"] == [0.3, -0.1]
    assert stats["started_at"] == stats["last_turn_at"]
    # 首 started_at 固定为第一条消息时刻，后续不漂移
    stats2 = tm.record_turn(stats)
    assert stats2["started_at"] == stats["started_at"]
    # 不 mutate 入参
    assert stats["turns"] == 2


def test_record_tone_and_action_origins(tm) -> None:
    stats = tm.new_stats()
    stats = tm.record_tone(stats, "happy")
    stats = tm.record_tone(stats, "happy")
    stats = tm.record_tone(stats, "sad")
    assert stats["tone"] == {"happy": 2, "sad": 1}
    stats = tm.record_action(stats, "ebb_tide", origin="self")
    stats = tm.record_action(stats, "storm_surge", origin="user")
    assert stats["actions"] == [
        {"action": "ebb_tide", "origin": "self"},
        {"action": "storm_surge", "origin": "user"},
    ]


def test_review_due_dual_thresholds(tm) -> None:
    stats = tm.new_stats()
    # 无互动：天数再多也不写（纯挂机不写空篇）
    due, reason = tm.review_due(stats, turns_threshold=50, days_threshold=7)
    assert (due, reason) == (False, "no_activity")

    # 攒满轮数门槛
    stats = tm.record_turn(stats)
    stats = tm.record_turn(stats)
    due, reason = tm.review_due(stats, turns_threshold=2, days_threshold=7)
    assert (due, reason) == (True, "due_turns")

    # 轮数不够但天数够（把 started_at 拨回 8 天前）
    stats2 = tm.record_turn(tm.new_stats())
    old = tm._parse_iso_ts(stats2["started_at"]) - timedelta(days=8)
    stats2["started_at"] = old.isoformat(timespec="seconds")
    due, reason = tm.review_due(stats2, turns_threshold=50, days_threshold=7)
    assert (due, reason) == (True, "due_days")

    # 都不够
    due, reason = tm.review_due(tm.record_turn(tm.new_stats()), turns_threshold=50, days_threshold=7)
    assert (due, reason) == (False, "not_enough_turns")


def test_build_review_prompt_material(tm) -> None:
    stats = tm.new_stats()
    for _ in range(30):
        stats = tm.record_turn(stats, valence=0.2)
    stats = tm.record_tone(stats, "happy")
    stats = tm.record_action(stats, "ebb_tide", origin="self")
    stats = tm.record_action(stats, "ripple", origin="user")  # 命令触发的演示
    stats = tm.record_fragment(stats, "overstep", "你就是个破烂玩具")
    prompt = tm.build_review_prompt(stats, sample_turns=[("今晚陪陪我", "好呀好呀")])
    assert "共 30 轮对话" in prompt
    assert "愉快×1" in prompt
    assert "她自己起过的情绪" in prompt
    assert "冷战沉默" in prompt
    assert "心有涟漪" not in prompt  # origin=user 的不进"她自己起的情绪"行
    assert "你就是个破烂玩具" in prompt
    assert "他说：今晚陪陪我" in prompt
    assert "她回：好呀好呀" in prompt


def test_parse_review_response_strips_wrappers(tm) -> None:
    assert tm.parse_review_response("") == ""
    assert tm.parse_review_response(None) == ""
    # 剥包裹引号
    assert tm.parse_review_response("「他最近很温柔。」") == "他最近很温柔。"
    # 砍前缀解释行（保留正文）
    raw = "好的，以下是这篇评价：\n他最近很温柔，语气也耐心了许多。"
    assert tm.parse_review_response(raw) == "他最近很温柔，语气也耐心了许多。"
    # 前缀行太长不砍（避免误伤正文首段）
    long_first = "这段时间他表现得非常好，几乎每天都会主动来聊天并且关心她的近况与身体状况"
    assert tm.parse_review_response(long_first) == long_first


def test_append_review_evicts_oldest(tm) -> None:
    entries = []
    for i in range(tm._REVIEW_MAX_ENTRIES):
        entries, ev = tm.append_review(entries, tm.review_record(f"2026-01-{i + 1:02d}", {"turns": i}, f"第{i}篇"))
        assert ev == [], "未超上限不得带出淘汰卷"
    assert len(entries) == tm._REVIEW_MAX_ENTRIES
    # 1.3.0 档案室：超限追加返回 (新篇表, 被淘汰卷)——被淘汰卷随返回值带出，
    # 由调用方搬进 review_archive@（签名不兼容是故意的，防漏改静默丢卷）
    entries, evicted = tm.append_review(entries, tm.review_record("2026-03-01", {"turns": 99}, "新篇"))
    assert len(entries) == tm._REVIEW_MAX_ENTRIES
    assert [e["text"] for e in evicted] == ["第0篇"], "最旧一卷被带出"
    assert entries[0]["text"] == "第1篇"  # 活架最旧推进到第二卷
    assert entries[-1]["text"] == "新篇"


# ---------------------------------------------------------------------------
# 主链路：喂入点与成文（monkeypatch 模型直连）
# ---------------------------------------------------------------------------


def _with_turns(p, turns: int):
    """给 default shard 的素材统计灌 turns 轮（直接经纯函数，不走门控）。"""
    shard = p._get_shard("default")
    stats = shard.review_stats or tm_new()
    for _ in range(turns):
        stats = tm_record_turn(stats)
    shard.review_stats = stats
    return p


def tm_new():
    import sys

    mod = sys.modules["forever_companion"]
    return mod.new_stats()


def tm_record_turn(stats):
    import sys

    mod = sys.modules["forever_companion"]
    return mod.record_turn(stats)


def test_feed_review_turn_gated_by_enabled(plugin_factory) -> None:
    p = plugin_factory()
    shard = p._get_shard("default")
    p._review_cfg["enabled"] = False
    p._feed_review_turn("default", shard)
    assert not shard.review_stats.get("turns")
    p._review_cfg["enabled"] = True
    p._feed_review_turn("default", shard)
    assert shard.review_stats["turns"] == 1


def test_handle_new_user_message_feeds_review(plugin_factory) -> None:
    p = plugin_factory(tide_extra={"inject_mode": "off"})
    shard = run(p._ensure_shard("default"))
    run(p._handle_new_user_message(1000.0, "你好呀", "default"))
    assert shard.review_stats.get("turns") == 1
    # Store 落盘：review@<角色> 里带着 stats
    saved = p.store.data["review@default"]
    assert saved["stats"]["turns"] == 1


def test_maybe_write_review_full_flow(plugin_factory) -> None:
    p = _with_turns(plugin_factory(), 60)
    prompts = []

    def fake_post(base_url, api_key, model, prompt, logger=None):
        prompts.append(prompt)
        return "他最近对她很温柔，几乎每天都会主动来聊天。"

    monkey = pytest.MonkeyPatch()
    monkey.setattr(p, "_resolve_tone_slot", lambda cfg, slot: {"base_url": "http://x", "api_key": "k", "model": "m"})
    monkey.setattr(p, "_post_chat_completion", fake_post)
    try:
        written, reason = run(p._maybe_write_review("default", p._get_shard("default")))
        assert written is True
        shard = p._get_shard("default")
        assert len(shard.review) == 1
        assert shard.review[0]["turns"] == 60
        assert shard.review[0]["text"].startswith("他最近对她很温柔")
        # 成文后 stats 清零重新累计
        assert shard.review_stats["turns"] == 0
        # prompt 里带了素材块
        assert "共 60 轮对话" in prompts[0]
        # 落盘原子：篇目与清零后的 stats 一次写入
        saved = p.store.data["review@default"]
        assert len(saved["entries"]) == 1
        assert saved["stats"]["turns"] == 0
    finally:
        monkey.undo()


def test_maybe_write_review_gates(plugin_factory) -> None:
    p = _with_turns(plugin_factory(), 3)
    monkey = pytest.MonkeyPatch()
    monkey.setattr(p, "_resolve_tone_slot", lambda cfg, slot: {"base_url": "http://x", "api_key": "k", "model": "m"})
    monkey.setattr(p, "_post_chat_completion", lambda *a, **k: "正文")
    try:
        # 门槛未到：不写
        written, reason = run(p._maybe_write_review("default", p._get_shard("default")))
        assert written is False
        assert reason == "not_enough_turns"

        # force 但素材不足：拒绝硬写
        written, reason = run(p._maybe_write_review("default", p._get_shard("default"), force=True))
        assert written is False
        assert reason == "not_enough_material"

        # 槽位解析不出：休眠（素材给足，确保越过门槛后停在槽位判定）
        shard = p._get_shard("default")
        shard.review_stats = tm_new()
        for _ in range(60):
            shard.review_stats = tm_record_turn(shard.review_stats)
        monkey.setattr(p, "_resolve_tone_slot", lambda cfg, slot: None)
        written, reason = run(p._maybe_write_review("default", shard))
        assert written is False
        assert reason == "slot_unresolved"
    finally:
        monkey.undo()


def test_get_and_clear_review_entries(plugin_factory, tm) -> None:
    p = _with_turns(plugin_factory(), 30)
    shard = run(p._ensure_shard("default"))
    shard.review, _ev = tm.append_review([], tm.review_record("2026-08-01T00:00:00", {"turns": 30}, "第一篇"))
    res = run(p.get_review())
    assert res.value["entries"][0]["text"] == "第一篇"
    assert res.value["progress"]["turns"] == 30
    assert res.value["progress"]["turns_threshold"] == 50
    # 清空
    res = run(p.clear_review())
    assert res.value["cleared"] == 1
    assert p._get_shard("default").review == []
    assert p._get_shard("default").review_stats["turns"] == 0


def test_review_not_exposed_to_llm_tools(tm) -> None:
    """隔离性：我的日记不注册任何 @llm_tool--她没有读取渠道。

    扫描范围是主包源码 + 同包全部 .py（拆分后 @llm_tool 分布在多个模块），
    断言语义不变：收集到的工具方法名里不得含 "review"。
    """
    import inspect
    import re
    from pathlib import Path

    root = Path(tm.__file__).parent
    sources = [inspect.getsource(tm)]
    sources += [
        f.read_text(encoding="utf-8")
        for f in sorted(root.rglob("*.py"))
        if f.name != "__init__.py" and ".venv" not in f.parts
        and "tests" not in f.parts and "__pycache__" not in f.parts
    ]
    # @llm_tool(...) 与 async def 之间隔任意行（参数多行），用宽松正则配对收集
    tool_methods = set()
    pattern = re.compile(r'@llm_tool\((.*?)\)\s*\n\s*async def (\w+)', re.DOTALL)
    for src in sources:
        for match in pattern.finditer(src):
            tool_methods.add(match.group(2))
    assert tool_methods, "llm_tool 方法应当非空（正则失效则测试本身要修）"
    assert not any("review" in name for name in tool_methods), tool_methods


def test_update_settings_review_fields(plugin_factory, tm) -> None:
    p = plugin_factory()
    res = run(p.update_settings(
        review_enabled=False,
        review_turns_threshold=100,
        review_days_threshold=14,
        review_slot="conversation",
    ))
    assert isinstance(res, tm.Ok)
    snap = res.value
    assert snap["review_enabled"] is False
    assert snap["review_turns_threshold"] == 100
    assert snap["review_days_threshold"] == 14
    assert snap["review_slot"] == "conversation"
    # 非法槽位被拒
    res = run(p.update_settings(review_slot="emotion"))
    assert isinstance(res, tm.Err)
    # 越界值被钳制
    res = run(p.update_settings(review_turns_threshold=99999))
    assert res.value["review_turns_threshold"] == 500


def test_dashboard_carries_review_brief(plugin_factory) -> None:
    p = _with_turns(plugin_factory(), 12)
    payload = run(p.dashboard())
    brief = payload["review_brief"]
    assert brief["enabled"] is True
    assert brief["entries"] == 0
    assert brief["progress_turns"] == 12
    assert brief["turns_threshold"] == 50


def test_prune_lanlan_removes_review_keys(plugin_factory_full, tm) -> None:
    store_initial = {
        "lanlan_index": ["孤儿"],
        "cycle@孤儿": {"enabled": True},
        "review@孤儿": {"entries": [{"ts": "2026-01-01", "text": "x"}], "stats": {"turns": 3}},
    }
    # 宿主名单可读且不含"孤儿"；当前角色是 default
    async def fake_http(method, path, body=None, headers=None):
        if path == "/api/characters":
            return {"猫娘": {"default": {}}}
        if path == "/api/characters/current_catgirl":
            return {"current_catgirl": "default"}
        return None

    p = plugin_factory_full(store_initial=store_initial, http=fake_http)
    res = run(p.prune_lanlan(lanlan="孤儿"))
    assert isinstance(res, tm.Ok)
    assert "review@孤儿" not in p.store.data


def test_mood_action_origins_recorded(plugin_factory) -> None:
    """set_mood（用户命令）标 origin=user；LLM 工具路径标 origin=self。"""
    p = plugin_factory()
    monkey = pytest.MonkeyPatch()

    async def fake_resolve():
        return "default"

    monkey.setattr(p, "_resolve_current_lanlan", fake_resolve)
    try:
        run(p.set_mood(action="ebb_tide", minutes=10, reason="测试"))
        shard = p._get_shard("default")
        actions = shard.review_stats.get("actions") or []
        assert actions == [{"action": "ebb_tide", "origin": "user"}]

        # LLM 工具路径（tool_cold_violence）标 self
        run(p.tool_cold_violence(minutes=10, reason="测试"))
        actions = p._get_shard("default").review_stats["actions"]
        assert actions[-1] == {"action": "ebb_tide", "origin": "self"}
    finally:
        monkey.undo()


def _stub_model(p, monkey, reply="这段时间他常来陪她聊天。"):
    monkey.setattr(p, "_resolve_tone_slot", lambda cfg, slot: {"base_url": "http://x", "api_key": "k", "model": "m"})
    monkey.setattr(p, "_post_chat_completion", lambda *a, **k: reply)


def test_write_review_now_entry_queues_then_drain_composes(plugin_factory) -> None:
    """受理式入口（1.2.3）：点击秒回 accepted，模型成文延后到队列消费。"""
    p = _with_turns(plugin_factory(), 20)
    monkey = pytest.MonkeyPatch()
    _stub_model(p, monkey)
    try:
        res = run(p.write_review_now())
        v = res.value
        assert v["accepted"] is True and v["queued"] is True
        assert v["written"] is False
        shard = p._get_shard("default")
        assert shard.pending_review_write > 0
        assert len(shard.review) == 0, "受理阶段绝不同步成文（这正是旧版卡面板的根因）"
        assert shard.review_stats["turns"] == 20, "受理不动素材"

        # tick 队列消费：真成文 + 结果回流（面板按 ts 弹完成 toast 的数据源）
        run(p._drain_pending_review_writes())
        assert shard.pending_review_write == 0
        assert len(shard.review) == 1
        assert shard.review_write_result["written"] is True
        assert shard.review_write_result["reason"] == "written"
        assert shard.review_write_result["ts"] > 0
        assert shard.review_stats["turns"] == 0
    finally:
        monkey.undo()


def test_write_review_now_gate_rejects_at_click(plugin_factory) -> None:
    """门槛拒绝在点击时同步给出（不等队列）：素材不足/开关未开当场 note。"""
    p = _with_turns(plugin_factory(), 3)
    monkey = pytest.MonkeyPatch()
    _stub_model(p, monkey)
    try:
        res = run(p.write_review_now())
        v = res.value
        assert v["accepted"] is False and v["queued"] is False
        assert v["reason"] == "not_enough_material"
        # i18n 契约第九轮：数据字段代替文案（面板用 turns/min_turns 插值翻译）
        assert v["turns"] == 3 and v["min_turns"] >= 10
        assert p._get_shard("default").pending_review_write == 0, "拒绝不得入队"
    finally:
        monkey.undo()

    p2 = _with_turns(plugin_factory(), 20)
    p2._review_cfg["enabled"] = False
    res2 = run(p2.write_review_now())
    assert res2.value["reason"] == "disabled"
    assert "note" not in res2.value


def test_write_review_now_duplicate_click_rejected(plugin_factory) -> None:
    """队列槽位只有一个：上一篇还在写/还排着时，重复点击不叠加。"""
    p = _with_turns(plugin_factory(), 20)
    monkey = pytest.MonkeyPatch()
    _stub_model(p, monkey)
    try:
        first = run(p.write_review_now()).value
        assert first["accepted"] is True
        pending_mark = p._get_shard("default").pending_review_write
        again = run(p.write_review_now()).value
        assert again["accepted"] is False
        assert again["reason"] == "already_writing"
        assert p._get_shard("default").pending_review_write == pending_mark, "重复点击不得重置排队时刻"

        # 队列消费跑完（成文落盘、素材清零）：没重新攒够素材前不能再点，
        # 重新喂素材后恢复受理
        run(p._drain_pending_review_writes())
        blocked = run(p.write_review_now()).value
        assert blocked["accepted"] is False and blocked["reason"] == "not_enough_material"
        _with_turns(p, 20)
        third = run(p.write_review_now()).value
        assert third["accepted"] is True
    finally:
        monkey.undo()


def test_maybe_write_review_inflight_lock(plugin_factory) -> None:
    """在飞锁（1.2.3）：同角色成文期间再入立即 in_flight，不并跑两趟模型。"""
    p = _with_turns(plugin_factory(), 20)
    monkey = pytest.MonkeyPatch()
    calls = {"n": 0}

    def counting_post(*a, **k):
        calls["n"] += 1
        return "正文"

    monkey.setattr(p, "_resolve_tone_slot", lambda cfg, slot: {"base_url": "http://x", "api_key": "k", "model": "m"})
    monkey.setattr(p, "_post_chat_completion", counting_post)
    try:
        p._review_writing.add("default")
        written, reason = run(p._maybe_write_review("default", p._get_shard("default"), force=True))
        assert (written, reason) == (False, "in_flight")
        assert calls["n"] == 0, "在飞时不得再进模型"
        p._review_writing.discard("default")

        # 队列消费撞上在飞：pending 放回队里，不记失败结果
        shard = p._get_shard("default")
        shard.pending_review_write = 123.0
        p._review_writing.add("default")
        run(p._drain_pending_review_writes())
        assert shard.pending_review_write == 123.0
        assert shard.review_write_result is None
        p._review_writing.discard("default")

        # 正常一趟跑完后锁必须释放（finally 清理，不留残余）
        run(p._drain_pending_review_writes())
        assert "default" not in p._review_writing
        assert len(shard.review) == 1
    finally:
        monkey.undo()


def test_journal_invite_pending_state(plugin_factory, tm) -> None:
    """邀请挂起态（1.2.3）：递邀晚于末笔即挂起，她落笔（新页新段）即解除。"""
    from datetime import datetime, timedelta, timezone

    p = plugin_factory()
    shard = run(p._ensure_shard("default"))
    assert p._journal_invite_pending(shard) is False, "没递过邀不挂起"

    # 模拟 _maybe_journal_invite 递出（force 路径会写节流位）：空日记本 → 挂起
    shard.last_journal_invite_ts = time.time()
    assert p._journal_invite_pending(shard) is True, "递过邀、一个字没落笔 → 挂起"

    # 她写了新的一页（段 ts 晚于邀请）→ 解除
    newer = (datetime.now(timezone.utc) + timedelta(seconds=5)).isoformat(timespec="seconds")
    shard.journal = [{"page_no": 1, "started_at": newer, "entries": [{"ts": newer, "text": "今天他陪我聊了很久"}]}]
    assert p._journal_invite_pending(shard) is False

    # 再次递邀（新邀请晚于末笔）→ 重新挂起
    shard.last_journal_invite_ts = time.time() + 60
    assert p._journal_invite_pending(shard) is True


def test_dashboard_exposes_review_writing_and_invite_pending(plugin_factory) -> None:
    """dashboard 回流通道：writing 点亮按钮禁用态、last_result 驱动完成 toast、
    journal_invite_pending 驱动日记页挂起提示。"""
    p = _with_turns(plugin_factory(), 20)
    run(p._ensure_shard("default"))
    shard = p._get_shard("default")
    shard.pending_review_write = 1.0
    shard.review_write_result = {"ts": 2.0, "written": True, "reason": "written"}
    payload = run(p.dashboard())
    brief = payload["review_brief"]
    assert brief["writing"] is True
    assert brief["last_result"]["written"] is True
    # dashboard 头部先跑 _supervise_once：空日记本的周期邀请节奏判定为 due，
    # 一趟例行递邀后挂起位会被点亮——顺带证明回流通道活着
    assert payload["journal_invite_pending"] is True



def test_slot_dormancy_diagnosis_free_route_vs_no_model(tm) -> None:
    """休眠原因诊断：免费路由（宿主防滥用边界）与未配模型给出不同指引。"""
    # 免费路由：core=free + assist=free（Steam 免费版的典型形态）
    free_cfg = {
        "coreApi": "free",
        "assistApi": "free",
        "summaryModelProvider": "follow_assist",
        "summaryModelId": "",
    }
    assert tm.diagnose_slot_dormancy(free_cfg, "summary") == "free_route"
    # assistApi 缺失但 core=free：宿主默认推导 assist=free，同属免费路由
    assert tm.diagnose_slot_dormancy({"coreApi": "free", "summaryModelProvider": "follow_assist"}, "summary") == "free_route"

    # 非免费路由但模型没配：按未配模型报
    no_model_cfg = {
        "coreApi": "qwen",
        "assistApi": "qwen",
        "assistApiKeyQwen": "sk-x",
        "summaryModelProvider": "follow_assist",
        "summaryModelId": "",  # 模型名空
    }
    assert tm.diagnose_slot_dormancy(no_model_cfg, "summary") == "no_model"

    # follow_core 且 core=free：同样按免费路由报
    assert tm.diagnose_slot_dormancy(
        {"coreApi": "free", "summaryModelProvider": "follow_core"}, "summary"
    ) == "free_route"


def test_slot_dormancy_hint_actionable(plugin_factory, tm) -> None:
    """面板/日志提示：免费路由给出"配自己的 API"指引，不再是含糊的"未配模型"。"""
    hint_free = tm._slot_dormancy_hint({"coreApi": "free", "assistApi": "free"}, "summary")
    assert "免费路由" in hint_free
    assert "无法直连" in hint_free
    hint_no_model = tm._slot_dormancy_hint({"coreApi": "qwen", "assistApi": "qwen"}, "summary")
    assert "未配置模型" in hint_no_model


def test_dashboard_channel_status_and_week_activity(plugin_factory, tm) -> None:
    """总览/通道卡数据面：channel_status 三通道状态灯 + week_activity 近 7 天活跃度。"""
    from datetime import timedelta

    p = plugin_factory()
    # 免费路由形态的宿主配置：碎片/成文通道应为 free_route 休眠
    monkey = pytest.MonkeyPatch()
    monkey.setattr(p, "_load_core_config", lambda: {
        "coreApi": "free", "assistApi": "free",
        "summaryModelProvider": "follow_assist", "summaryModelId": "",
    })
    try:
        # 近 7 天内的日记条目 x2 + 8 天前的 x1 -> week_activity = 2
        shard = run(p._ensure_shard("default"))
        now = tm._now_utc()
        shard.diary = [
            {"ts": now.isoformat(timespec="seconds"), "source": "self", "entry": "今天"},
            {"ts": (now - timedelta(days=2)).isoformat(timespec="seconds"), "source": "auto", "kind": "like", "quote": "q"},
            {"ts": (now - timedelta(days=8)).isoformat(timespec="seconds"), "source": "self", "entry": "上周"},
        ]
        payload = run(p.dashboard())
        cs = payload["channel_status"]
        assert cs["tone"]["enabled"] is True and cs["tone"]["dormant_reason"] == ""
        assert cs["fragments"]["dormant_reason"] == "free_route"
        assert cs["review"]["dormant_reason"] == "free_route"
        assert payload["week_activity"] == 2
    finally:
        monkey.undo()


def test_channel_status_disabled_and_no_model(plugin_factory) -> None:
    """通道灯：功能关闭 = disabled；非免费路由但没配模型 = no_model。"""
    p = plugin_factory()
    monkey = pytest.MonkeyPatch()
    monkey.setattr(p, "_load_core_config", lambda: {
        "coreApi": "qwen", "assistApi": "qwen", "assistApiKeyQwen": "sk-x",
        "summaryModelProvider": "follow_assist", "summaryModelId": "",
    })
    try:
        p._review_cfg["enabled"] = False
        payload = run(p.dashboard())
        cs = payload["channel_status"]
        assert cs["review"]["enabled"] is False
        assert cs["review"]["dormant_reason"] == "disabled"
        assert cs["fragments"]["dormant_reason"] == "no_model"
    finally:
        monkey.undo()


def test_diagnose_slot_custom_and_follow_chain(tm) -> None:
    """槽位诊断与解析链同语义：custom 槽看自己的 Url+Id；follow 链递归到目标槽。"""
    # custom 直连槽（用户自配端点）：配齐 Url+Id 即可用，即使 core/assist 是 free
    custom_cfg = {
        "coreApi": "free", "assistApi": "free",
        "summaryModelProvider": "custom",
        "summaryModelId": "m-1", "summaryModelUrl": "https://x/v1", "summaryModelApiKey": "k",
    }
    assert tm.diagnose_slot_dormancy(custom_cfg, "summary") == ""
    assert tm._resolve_tone_slot(custom_cfg, "summary") is not None
    # custom 但没配 URL：按未配模型报
    assert tm.diagnose_slot_dormancy({**custom_cfg, "summaryModelUrl": ""}, "summary") == "no_model"

    # follow_summary 槽指向 custom summary：summary 配齐 → correction 也可用
    # follow_conversation 的递归由下面的 free_route 反例覆盖（custom 目标的正例
    # 与本例同构：递归到配齐的 custom 槽返回空串）
    fs_cfg = {**custom_cfg, "correctionModelProvider": "follow_summary"}
    assert tm.diagnose_slot_dormancy(fs_cfg, "correction") == ""

    # follow_conversation 指向 free 路由的 conversation → free_route
    free_follow = {
        "coreApi": "free", "assistApi": "free",
        "summaryModelProvider": "follow_conversation",
        "conversationModelProvider": "follow_assist", "conversationModelId": "",
    }
    assert tm.diagnose_slot_dormancy(free_follow, "summary") == "free_route"


def test_dashboard_channel_status_custom_slot_ok(plugin_factory, tm) -> None:
    """custom 自配槽的用户：通道灯应为正常（不再是误判的 no_model 休眠）。"""
    import pytest as _pytest

    p = plugin_factory()
    monkey = _pytest.MonkeyPatch()
    monkey.setattr(p, "_load_core_config", lambda: {
        "coreApi": "free", "assistApi": "free",
        "summaryModelProvider": "custom",
        "summaryModelId": "gpt-x", "summaryModelUrl": "https://api.example.com/v1",
        "summaryModelApiKey": "sk-x",
    })
    try:
        payload = run(p.dashboard())
        cs = payload["channel_status"]
        assert cs["fragments"]["dormant_reason"] == ""  # 正常
        assert cs["review"]["dormant_reason"] == ""
    finally:
        monkey.undo()


def test_drain_surfaces_compose_exception(plugin_factory) -> None:
    """队列成文的异常也必须变成可见结论（1.2.4 审查修复）：pending 在 await 之前就
    清零了，异常若一路冒到 tick 顶层，结果位永远不写——用户点了「立即写一篇」收到
    "已开始写"，随后按钮变回可点、不弹任何东西，这一篇石沉大海（1.2.2 的同步链路
    至少还会以 api.call 失败的形式弹出来）。"""
    p = _with_turns(plugin_factory(), 20)
    monkey = pytest.MonkeyPatch()

    def boom(*args, **kwargs):
        raise RuntimeError("socket exploded")

    _stub_model(p, monkey, reply="不该被调到")
    monkey.setattr(p, "_post_chat_completion", boom)
    try:
        assert run(p.write_review_now()).value["accepted"] is True
        run(p._drain_pending_review_writes())  # 关键：异常不得冒出 tick
        shard = p._get_shard("default")
        assert shard.pending_review_write == 0
        result = shard.review_write_result
        assert result is not None and result["written"] is False
        assert result["reason"] == "compose_raised"
        assert "default" not in p._review_writing, "异常路径也要释放在飞锁"
        assert len(shard.review) == 0 and shard.review_stats["turns"] == 20, "异常不得动篇目与素材"
    finally:
        monkey.undo()
