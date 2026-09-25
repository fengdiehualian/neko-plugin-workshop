"""1.2.6 新手引导 + 配置引导测试：guide 记录规整 / 就绪清单 / 入口回写 / 轮询载荷。"""

from __future__ import annotations

import asyncio


def run(coro):
    return asyncio.run(coro)


# ---------- norm_guide_record：宽容规整 ----------


def test_norm_guide_record_tolerant(tm) -> None:
    norm = tm.norm_guide_record
    # 非 dict / None：一律未引导
    assert norm(None) == {"wizard": "", "at": "", "version": ""}
    assert norm("junk") == {"wizard": "", "at": "", "version": ""}
    # 坏 wizard 值自愈为未引导（宁可多看一次向导，不可把用户永久关在引导之外）
    assert norm({"wizard": "whatever"})["wizard"] == ""
    assert norm({"wizard": 42})["wizard"] == ""
    # 合法值保留，缺字段容忍
    assert norm({"wizard": "done"}) == {"wizard": "done", "at": "", "version": ""}
    assert norm({"wizard": "skip", "at": "2026-09-08T00:00:00", "version": "1"}) == {
        "wizard": "skip",
        "at": "2026-09-08T00:00:00",
        "version": "1",
    }


def test_wizard_pending(tm) -> None:
    pending = tm.wizard_pending
    assert pending({}) is True
    assert pending(None) is True
    assert pending({"wizard": "done", "version": tm._GUIDE_VERSION}) is False
    assert pending({"wizard": "skip", "version": tm._GUIDE_VERSION}) is False
    # reopen 清回未引导后重新自动弹
    assert pending({"wizard": ""}) is True


def test_wizard_pending_regates_on_guide_version_bump(tm) -> None:
    """引导改版（修订轮三：六步新增生日/外观页）：旧 done/skip 记录再放行一次。

    版本不符或记录缺版本（旧版手工/盘上数据）→ pending True；用户走完新向导
    后 make_guide_record 盖当前版本 → 恢复正常不再弹（幂等，不反复骚扰）。
    """
    pending = tm.wizard_pending
    assert pending({"wizard": "done", "version": "1"}) is True
    assert pending({"wizard": "skip", "version": "1"}) is True
    # 无版本字段的旧记录同样失效重弹（fail-open 到"多看一次向导"侧）
    assert pending({"wizard": "done"}) is True
    # 走完新向导 → 盖章当前版本，收工不再弹
    fresh = tm.make_guide_record("done", "2026-09-15T00:00:00")
    assert pending(fresh) is False
    assert pending(tm.make_guide_record("skip", "t")) is False


# ---------- make_guide_record ----------


def test_make_guide_record(tm) -> None:
    make = tm.make_guide_record
    done = make("done", "2026-09-08T10:00:00")
    assert done["wizard"] == "done" and done["at"] == "2026-09-08T10:00:00"
    assert done["version"] == tm._GUIDE_VERSION
    assert make("skip", "t")["wizard"] == "skip"
    assert make("reopen", "t")["wizard"] == ""
    # 未知 action 按 done 防御（入口 schema 已限 enum，这里锁兜底行为）
    assert make("???", "t")["wizard"] == "done"


# ---------- build_readiness：就绪清单判定 ----------


def test_build_readiness_defaults_missing_as_false(tm) -> None:
    # 空 signals：全按 False 容忍，must 项不过
    r = tm.build_readiness({})
    assert [i["id"] for i in r["items"]] == ["rhythm", "anchor", "mood", "channels", "together"]
    assert r["must_ok"] is False
    assert r["all_ok"] is False
    assert r["pending"] == 5


def test_build_readiness_must_vs_suggest(tm) -> None:
    # must 全过、suggest 欠账：must_ok True、all_ok False（清单卡继续显示）
    r = tm.build_readiness({"rhythm": True, "anchor": True})
    assert r["must_ok"] is True
    assert r["all_ok"] is False
    assert r["pending"] == 3
    # 全就绪：整卡收起的判据
    full = tm.build_readiness({
        "rhythm": True, "anchor": True, "mood": True,
        "channels": True, "together": True,
    })
    assert full["all_ok"] is True and full["pending"] == 0


def test_build_readiness_item_shape(tm) -> None:
    r = tm.build_readiness({"rhythm": False, "anchor": True, "mood": True, "channels": True})
    by_id = {i["id"]: i for i in r["items"]}
    assert by_id["rhythm"] == {"id": "rhythm", "ok": False, "level": "must", "tab": "cycle"}
    assert by_id["anchor"]["ok"] is True
    # 展示项无跳转页签
    assert by_id["together"]["tab"] == ""
    assert by_id["mood"]["tab"] == "mood"


# ---------- 入口 set_onboarding：内存位 + 落盘 + Err 传播 ----------


def test_set_onboarding_roundtrip(plugin_factory_full, tm) -> None:
    p = plugin_factory_full()
    # 初始未引导
    assert p._guide["wizard"] == ""
    res = run(p.set_onboarding(action="done"))
    assert isinstance(res, tm.Ok)
    assert res.value["wizard"] == "done"
    # 内存位即时更新（5s 轮询滞后期不重复弹窗依赖这一点）
    assert p._guide["wizard"] == "done"
    # 落盘经统一出口
    assert p.store.data["guide"]["wizard"] == "done"
    # reopen 清回未引导
    res2 = run(p.set_onboarding(action="reopen"))
    assert res2.value["wizard"] == ""
    assert p.store.data["guide"]["wizard"] == ""


def test_set_onboarding_rejects_bad_action(plugin_factory_full, tm) -> None:
    p = plugin_factory_full()
    res = run(p.set_onboarding(action="explode"))
    assert isinstance(res, tm.Err)
    # 拒绝非法 action 不得污染记录
    assert p._guide["wizard"] == ""
    assert "guide" not in p.store.data


def test_guide_loaded_from_store_on_start(plugin_factory_full) -> None:
    # 上一会话完成过引导 → 新会话载入后不再自动弹
    p = plugin_factory_full(store_initial={
        "guide": {"wizard": "done", "at": "2026-09-01T00:00:00", "version": "1"},
    })
    assert p._guide["wizard"] == "done"


# ---------- dashboard 载荷 ----------


def test_dashboard_carries_onboarding(plugin_factory_full, tm) -> None:
    p = plugin_factory_full()
    ctx = run(p.dashboard())
    ob = ctx["onboarding"]
    assert ob["wizard_pending"] is True
    # DEFAULT_TIDE：enabled + 锚点已设 → must 两项已过
    readiness = ob["readiness"]
    by_id = {i["id"]: i for i in readiness["items"]}
    assert by_id["rhythm"]["ok"] is True
    assert by_id["anchor"]["ok"] is True
    assert readiness["must_ok"] is True
    # 向导收尾后轮询即时反映（读内存位）
    run(p.set_onboarding(action="done"))
    ctx2 = run(p.dashboard())
    assert ctx2["onboarding"]["wizard_pending"] is False
    assert isinstance(ctx2["onboarding"]["guide"]["wizard"], str)


def test_dashboard_channels_signal(plugin_factory_full, tm) -> None:
    """channels 信号：三条通道全休眠 → False；任一路在线 → True。"""
    live = tm_panel_live(tm, {"tone": {"enabled": True, "dormant_reason": ""}})
    assert live is True
    dead = tm_panel_live(tm, {
        "tone": {"enabled": False, "dormant_reason": ""},
        "fragments": {"enabled": True, "dormant_reason": "no_model"},
        "review": {"enabled": True, "dormant_reason": "free_route"},
    })
    assert dead is False


def tm_panel_live(tm, channel_status: dict) -> bool:
    cls = tm.ForeverCompanionPlugin
    return cls._channels_any_live(channel_status)
