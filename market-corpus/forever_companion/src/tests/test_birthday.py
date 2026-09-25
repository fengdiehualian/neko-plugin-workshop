# -*- coding: utf-8 -*-
"""生日轻语（1.3.1）测试：纯函数层行为 + 面板契约接线。

core/birthday.py 是纯日期算术，本文件主体逐函数对拍（闰日回落、
水位去重、坏数据 fail-closed）；另用 plugin_factory_full 走两条契约线：
update_settings 的 birthday_date 校验（非法发稳定码、合法归一化落盘）、
dashboard 的 birthday 视图字段。轻语触发本身依赖轮询链路，whisper 的
_maybe_birthday_push 用桩直调验证闸与水位。

运行方式（插件目录内）：uv run python -m pytest tests -q
"""

from __future__ import annotations

from datetime import date


def run(coro):
    import asyncio

    return asyncio.run(coro)


# ---------- parse / normalize ----------


def test_parse_accepts_full_and_month_day() -> None:
    from forever_companion.core.birthday import parse_birthday

    assert parse_birthday("1995-08-17") == (8, 17)
    assert parse_birthday("02-29") == (2, 29)
    assert parse_birthday("") is None
    assert parse_birthday(None) is None
    assert parse_birthday("  ") is None


def test_parse_rejects_impossible_dates() -> None:
    from forever_companion.core.birthday import parse_birthday

    for bad in ("2000-02-30", "2000-13-01", "2000-00-10", "2000-04-31", "bad", "20000-01-01"):
        assert parse_birthday(bad) is None, bad


def test_normalize_stores_canonical() -> None:
    from forever_companion.core.birthday import normalize_birthday

    assert normalize_birthday("1995-8-17") == "1995-08-17"  # 零填充归一（原生 date input 只认补形）
    assert normalize_birthday("") == ""
    assert normalize_birthday("  ") == ""
    assert normalize_birthday("02-29") == "2000-02-29"
    import pytest

    with pytest.raises(ValueError):
        normalize_birthday("1995-13-01")


# ---------- 当天判定（闰日回落） ----------


def test_is_today_matches_month_day_regardless_of_year() -> None:
    from forever_companion.core.birthday import birthday_is_today

    assert birthday_is_today("1995-08-17", date(2026, 8, 17))
    assert birthday_is_today("1995-08-17", "2026-08-17")
    assert not birthday_is_today("1995-08-17", "2026-08-16")
    assert not birthday_is_today("", "2026-08-17")
    assert not birthday_is_today("garbage", "2026-08-17")
    assert not birthday_is_today("1995-08-17", "not-a-date")


def test_leap_day_falls_back_to_feb28_in_common_years() -> None:
    from forever_companion.core.birthday import birthday_is_today

    # 闰年：02-29 正日命中
    assert birthday_is_today("2000-02-29", "2024-02-29")
    # 平年：回落 02-28（宁早勿漏），02-29 与 03-01 都不算
    assert birthday_is_today("2000-02-29", "2025-02-28")
    assert not birthday_is_today("2000-02-29", "2025-03-01")
    assert not birthday_is_today("2000-02-29", "2025-02-27")


# ---------- 水位 ----------


def test_watermark_dedupes_same_day_only() -> None:
    from forever_companion.core.birthday import birthday_due, mark_birthday_pushed

    stats: dict = {}
    assert birthday_due(stats, "2026-08-17") is True
    marked = mark_birthday_pushed(stats, "2026-08-17")
    assert birthday_due(marked, "2026-08-17") is False
    # 第二天又是生日（只可能闰日回落情形）/跨年：水位按整天日期比较，天然失效
    assert birthday_due(marked, "2026-08-18") is True
    assert birthday_due(marked, "2027-08-17") is True
    # 不原地改（与 mark_anniversary_pushed 同款纪律）
    assert stats == {}
    assert marked["birthday"]["last_pushed"] == "2026-08-17"
    # 旧 blob 其他键保留
    marked2 = mark_birthday_pushed({"first_seen": "x"}, "2026-08-17")
    assert marked2["first_seen"] == "x"


# ---------- 纪念手记 ----------


def test_diary_record_shape_matches_drift_bottle() -> None:
    from forever_companion.core.birthday import make_birthday_diary_record

    rec = make_birthday_diary_record("2026-08-17T00:30:00+00:00", "活跃期")
    assert rec["source"] == "self"  # 时间线"她写"档（缺省即 self，零迁移）
    assert rec["kind"] == "birthday"
    assert rec["ts"] == "2026-08-17T00:30:00+00:00"
    assert rec["phase"] == "活跃期"
    assert rec["mood"] == "开心"
    assert 20 <= len(rec["entry"]) <= 500


# ---------- 能力中心接线 ----------


def test_birthday_capability_registered_and_bound() -> None:
    from forever_companion.core.capabilities import CAPABILITY_SPECS, evaluate_capabilities

    spec = CAPABILITY_SPECS["birthday"]
    assert spec.group == "rhythm"
    assert spec.config == ("birthday", "enabled", True)
    assert spec.llm == "injection"
    # 默认全开时生效；用户否决即灭；总开关关 → master_off
    flags = {"birthday": True}
    states = evaluate_capabilities(True, flags)
    assert states["birthday"].enabled is True
    states = evaluate_capabilities(True, flags, overrides_off={"birthday"})
    assert states["birthday"].enabled is False
    assert states["birthday"].source == "user_off"


# ---------- 面板契约（update_settings + dashboard 视图） ----------


def test_update_settings_birthday_roundtrip(plugin_factory_full) -> None:
    from forever_companion import Ok

    p = plugin_factory_full()
    run(p.startup())
    res = run(p.update_settings(birthday_date="1995-08-17", birthday_keep_diary=False))
    assert isinstance(res, Ok)
    snap = res.value
    assert snap["birthday_date"] == "1995-08-17"
    assert snap["birthday_keep_diary"] is False
    # 落进 settings 覆盖层的 [birthday] 段
    overrides = p._settings_override.get("birthday") or {}
    assert overrides.get("date") == "1995-08-17"
    assert overrides.get("keep_diary") is False
    # 空串 = 清除
    res = run(p.update_settings(birthday_date=""))
    assert isinstance(res, Ok) and res.value["birthday_date"] == ""


def test_update_settings_birthday_rejects_garbage(plugin_factory_full) -> None:
    from forever_companion import Err

    p = plugin_factory_full()
    run(p.startup())
    res = run(p.update_settings(birthday_date="1995-13-40"))
    assert isinstance(res, Err)
    # i18n 契约：回的是稳定码，不是句子
    assert str(res.error) == "invalid_birthday_date"


def test_dashboard_birthday_view(plugin_factory_full) -> None:
    p = plugin_factory_full()
    run(p.startup())
    view = run(p.dashboard())["birthday"]
    assert view["set"] is False and view["date"] == "" and view["keep_diary"] is True
    run(p.update_settings(birthday_date="2000-01-01"))
    view = run(p.dashboard())["birthday"]
    assert view["set"] is True and view["date"] == "2000-01-01"
    # 1.3.1 改版：倒数/当天视图字段随总览生日卡退役，视图不挂死字段
    assert set(view.keys()) == {"date", "set", "keep_diary"}


# ---------- whisper 触发线（桩直调，绕开轮询） ----------


def _stub_push_capture(p, sink: list) -> None:
    def fake_push(**kwargs):
        sink.append(kwargs)
        return {"submitted": True}

    p.push_message = fake_push


def test_birthday_push_fires_once_gated_by_watermark(plugin_factory_full) -> None:
    from datetime import timedelta

    p = plugin_factory_full()
    run(p.startup())
    lanlan = run(p._resolve_current_lanlan())
    shard = run(p._ensure_shard(lanlan))
    # 生日设为"今天"（真实时钟即可，测试环境无固定时钟）
    today = p._stats_today()
    run(p.update_settings(birthday_date=today))
    sink: list = []
    _stub_push_capture(p, sink)
    assert run(p._maybe_birthday_push(lanlan, shard)) is True
    assert len(sink) == 1
    pushed = sink[0]
    assert pushed["visibility"] == [] and pushed["ai_behavior"] == "read"
    assert pushed["target_lanlan"] == lanlan
    # 当天水位已盖：第二次调用不再推
    assert run(p._maybe_birthday_push(lanlan, shard)) is False
    assert len(sink) == 1
    # 生日改成非今日 → 当天不再命中（未到期不推）
    other = (date.fromisoformat(today) + timedelta(days=120)).isoformat()
    run(p.update_settings(birthday_date=other))
    shard2 = run(p._ensure_shard(lanlan))
    shard2.stats = {}  # 清水位，只靠"今天不是生日"拦下
    assert run(p._maybe_birthday_push(lanlan, shard2)) is False
    assert len(sink) == 1


def test_birthday_push_dormant_without_date_or_when_vetoed(plugin_factory_full) -> None:
    p = plugin_factory_full()
    run(p.startup())
    lanlan = run(p._resolve_current_lanlan())
    shard = run(p._ensure_shard(lanlan))
    sink: list = []
    _stub_push_capture(p, sink)
    assert run(p._maybe_birthday_push(lanlan, shard)) is False  # 未填日期
    assert sink == []
    run(p.update_settings(birthday_date=p._stats_today()))
    run(p.set_capability(capability_id="birthday", enabled=False))  # 功能管理否决
    assert run(p._maybe_birthday_push(lanlan, shard)) is False
    assert sink == []


def test_birthday_push_skips_diary_when_keep_diary_off(plugin_factory_full) -> None:
    p = plugin_factory_full()
    run(p.startup())
    lanlan = run(p._resolve_current_lanlan())
    shard = run(p._ensure_shard(lanlan))
    run(p.update_settings(birthday_date=p._stats_today(), birthday_keep_diary=False))
    _stub_push_capture(p, [])
    before = len(shard.diary)
    assert run(p._maybe_birthday_push(lanlan, shard)) is True
    assert len(shard.diary) == before
    # 开着则留一条纪念手记
    run(p.update_settings(birthday_keep_diary=True))
    shard.stats = {}  # 清水位模拟明年
    assert run(p._maybe_birthday_push(lanlan, shard)) is True
    assert len(shard.diary) == before + 1
    assert shard.diary[-1]["kind"] == "birthday"


def test_birthday_push_not_watermarked_when_transport_refuses(plugin_factory_full) -> None:
    """submitted=False（宿主背压/传输不可用）：水位不盖，当天还能重试。"""
    p = plugin_factory_full()
    run(p.startup())
    lanlan = run(p._resolve_current_lanlan())
    shard = run(p._ensure_shard(lanlan))
    run(p.update_settings(birthday_date=p._stats_today()))
    calls: list = []

    def refusing_push(**kwargs):
        calls.append(kwargs)
        return {"submitted": False, "reason": "backpressure"}

    p.push_message = refusing_push
    assert run(p._maybe_birthday_push(lanlan, shard)) is False
    from forever_companion.core.birthday import birthday_due

    assert birthday_due(shard.stats, p._stats_today()) is True  # 水位没盖
    assert len(shard.diary) == 0  # 手记也没留
