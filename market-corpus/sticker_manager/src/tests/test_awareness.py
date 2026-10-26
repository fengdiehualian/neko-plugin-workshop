"""存在感注入（v0.2.0）：core 纯函数 + services 节拍/时钟/闸。

口径钉四族：
1. 选内容与文案（core.awareness）：空库空串、行序=最近爱用、带上 sticker_send 指路；
2. 目标发现（latest_lanlan / bus 读失败降级）；
3. 节拍与闸：disabled / no_target / waiting / empty_library 各不推进时钟、不 push；
4. 被拒不推进时钟（下一拍重试同一条不算轰炸）+ 永不炸拍 + inject_now 只绕间隔。
"""

from __future__ import annotations

from typing import Any

from conftest import (
    PNG_BYTES,
    FakeBus,
    FakeConfig,
    FakeHostContext,
    build_plugin,
    conversation_record,
)
from sticker_manager.core.awareness import (  # pyright: ignore[reportMissingImports] — 包名由 conftest 在测试时注册；独立仓静态面不可解析
    build_awareness_text,
)
from sticker_manager.core.catalog import Sticker  # pyright: ignore[reportMissingImports] — 同上
from sticker_manager.core.configuration import (  # pyright: ignore[reportMissingImports] — 同上
    AwarenessSettings,
    SendSettings,
    StickerManagerSettings,
    StorageSettings,
)
from sticker_manager.services.lanlan import latest_lanlan  # pyright: ignore[reportMissingImports] — 同上


def _st(sid: str, *, desc: str = "", use: int = 0, last: float = 0.0, disabled: bool = False) -> Sticker:
    return Sticker(
        id=sid,
        file=f"{sid}.png",
        desc=desc or f"desc-{sid}",
        disabled=disabled,
        added_at=1000.0 + int(last),
        use_count=use,
        last_used_at=last,
    )


def _settings(*, enabled: bool = True, interval: float = 3600.0) -> StickerManagerSettings:
    return StickerManagerSettings(
        enabled=enabled,
        send=SendSettings(),
        storage=StorageSettings(),
        awareness=AwarenessSettings(enabled=True, interval_sec=interval),
    )


def _clock_bus(lanlan: str = "K", *, ts: float = 10.0) -> FakeBus:
    """降级路径的总线桩：memory 桶读不通 → 轮次源不可用 → 退回 `interval_sec` 挂钟。

    v0.16.0 起自动注入的主路径是轮次驱动（见 `tests/test_turns.py::TestTurnDrivenAwareness`）。
    这一族测试钉的是降级时钟的闸语义（被拒不推进时钟/每卡独立/waiting 档），
    所以显式把总线弄坏——坏得越明确，越不会哪天又变成"测的其实不是它声称在测的那条路"。
    """
    return FakeBus([conversation_record("c", ts, lanlan)], memory_error=True)


class TestCoreSelection:
    def test_empty_library_gives_empty_text(self):
        assert build_awareness_text([]) == ""
        assert build_awareness_text([_st("x", disabled=True)]) == ""

    def test_text_is_one_breath_and_carries_no_catalog(self):
        """v0.17.0：注入只剩"想起来"，目录不再随它走。

        钉"不带目录"比钉"带那句"更重要——目录回到注入里 = 这轮的载体判断被推翻，
        而一次性 cue 装目录正是实机量出来的失效模式（10 次注入只换 2 次发图）。
        """
        stickers = [_st("a", desc="猫猫挥手", use=2), _st("b", desc="大哭", use=1)]
        text = build_awareness_text(stickers)
        assert "2" in text  # 总数按未禁用算
        assert "sticker_send" in text and "逐图说明" in text
        assert "[a]" not in text and "猫猫挥手" not in text and "大哭" not in text  # 无逐图行
        assert "最近常用" not in text and "你的套图" not in text  # 无目录块
        assert len(text) < 310, f"注入正文涨回目录复读机了：{len(text)} 字"

    def test_text_still_carries_the_tier_guidance(self):
        # Reminder retains willingness; actual refusal responses own retry rules.
        text = build_awareness_text([_st("a", desc="笑", use=1)])
        assert "分清安慰与自述" in text and "自然档：轻松聊天默认配一张贴切图" in text
        assert "最近不重复" not in text and "别重试" not in text

    def test_event_pointer_leads_with_the_landing_line(self):
        """v0.20.0：事件门控那一支要有一句"就是现在这句"的落点，计数那一支不许有。

        带上 lede 是给她的定位锚（同门 `（语气感知提醒）` 的形状）；计数兜底那一支
        披上它就等于把例行点名伪装成"这句有情绪"，是撒谎的提醒。
        """
        event = build_awareness_text([_st("a")], event=True)
        routine = build_awareness_text([_st("a")])
        assert "（表情包点名）" in event
        assert "（表情包点名）" not in routine
        assert event.startswith("（表情包点名）")
        assert event.endswith(routine)

    def test_unlisted_tier_degrades_to_default_wording(self):
        natural = build_awareness_text([_st("a")])
        assert build_awareness_text([_st("a")], eagerness="wild") == natural
        assert build_awareness_text([_st("a")], eagerness="eager") != natural


class TestLatestLanlan:
    def test_picks_newest_record(self):
        records = [
            conversation_record("c1", 100.0, "K"),
            conversation_record("c2", 300.0, "M"),
            conversation_record("c3", 200.0, "K"),
        ]
        assert latest_lanlan(records) == "M"

    def test_bad_timestamps_sink_to_bottom(self):
        records = [
            conversation_record("c1", 500.0, "Good"),
            {"conversation_id": "c2", "metadata": {"lanlan_name": "NoTs"}},
            {"conversation_id": "c3", "timestamp": True, "metadata": {"lanlan_name": "BoolTs"}},
        ]
        assert latest_lanlan(records) == "Good"

    def test_records_without_names_or_empty_give_blank(self):
        assert latest_lanlan([]) == ""
        assert latest_lanlan([{"conversation_id": "x"}]) == ""
        assert latest_lanlan([{"metadata": {"lanlan_name": "  "}}]) == ""

    def test_top_level_lanlan_field_also_counts(self):
        assert latest_lanlan([{"timestamp": 1.0, "lanlan_name": "Top"}]) == "Top"


class TestAwarenessGates:
    def test_master_switch_freezes_injection(self, tmp_path, run_async):
        # 与 tool_watch 相反的存在感纪律：业务关 = 注入门上（docstring 纪律 2）。
        host = FakeHostContext(
            config=FakeConfig(data={}),
            data_root=tmp_path,
            bus=_clock_bus(),
        )
        plugin, host = build_plugin(host)
        result = run_async(plugin._awareness.maybe_run(settings=_settings(enabled=False), now=100.0))
        assert result["status"] == "disabled"
        assert host.push.calls == []

    def test_no_conversation_target_skips(self, tmp_path, run_async):
        host = FakeHostContext(
            config=FakeConfig(data={"sticker_manager": {"enabled": True}}),
            data_root=tmp_path,
            bus=FakeBus([]),
        )
        plugin, host = build_plugin(host)
        plugin._library.add(data=PNG_BYTES, desc="d", tags=[])
        result = run_async(plugin._awareness.maybe_run(settings=_settings(), now=100.0))
        assert result["status"] == "no_target"
        assert host.push.calls == []

    def test_empty_library_skips_without_clock(self, tmp_path, run_async):
        host = FakeHostContext(
            config=FakeConfig(data={"sticker_manager": {"enabled": True}}),
            data_root=tmp_path,
            bus=_clock_bus(),
        )
        plugin, host = build_plugin(host)
        result = run_async(plugin._awareness.maybe_run(settings=_settings(), now=100.0))
        assert result["status"] == "empty_library"
        assert host.push.calls == []
        # 库后来有了内容：下一拍就能注（时钟没被空拍推进）。
        plugin._library.add(data=PNG_BYTES, desc="d", tags=[])
        second = run_async(plugin._awareness.maybe_run(settings=_settings(), now=101.0))
        assert second["status"] == "injected"

    def test_injects_once_then_waits_on_interval(self, tmp_path, run_async):
        host = FakeHostContext(
            config=FakeConfig(data={"sticker_manager": {"enabled": True}}),
            data_root=tmp_path,
            bus=_clock_bus(),
        )
        plugin, host = build_plugin(host)
        plugin._library.add(data=PNG_BYTES, desc="猫猫挥手", tags=[])
        first = run_async(plugin._awareness.maybe_run(settings=_settings(interval=100.0), now=50.0))
        assert first["status"] == "injected"
        soon = run_async(plugin._awareness.maybe_run(settings=_settings(interval=100.0), now=120.0))
        assert soon["status"] == "waiting"
        late = run_async(plugin._awareness.maybe_run(settings=_settings(interval=100.0), now=151.0))
        assert late["status"] == "injected"

    def test_push_shape_is_silent_read_directed_to_card(self, tmp_path, run_async):
        host = FakeHostContext(
            config=FakeConfig(data={"sticker_manager": {"enabled": True}}),
            data_root=tmp_path,
            bus=_clock_bus(),
        )
        plugin, host = build_plugin(host)
        plugin._library.add(data=PNG_BYTES, desc="猫猫挥手", tags=[])
        run_async(plugin._awareness.maybe_run(settings=_settings(), now=50.0))
        assert len(host.push.calls) == 1
        call = host.push.calls[0]
        assert call["visibility"] == []  # 用户看不见
        assert call["ai_behavior"] == "read"  # 不起话轮
        assert call["target_lanlan"] == "K"  # 归属只给本次选定的角色卡
        # v0.17.0：同卡共用一个合并键——cue 可能排队好几个话轮（语音模式尤甚），
        # 不合并就会让一次对话收到多条内容相同的旧提醒。尺在宿主 proactive.py:3330。
        assert call["coalesce_key"] == "sticker_manager:awareness:K"
        assert "sticker_send" in call["parts"][0]["text"]  # 指向常驻面，不复述目录

    def test_rejected_push_does_not_advance_clock(self, tmp_path, run_async):
        host = FakeHostContext(
            config=FakeConfig(data={"sticker_manager": {"enabled": True}}),
            data_root=tmp_path,
            bus=_clock_bus(),
        )
        plugin, host = build_plugin(host)
        plugin._library.add(data=PNG_BYTES, desc="d", tags=[])
        host.push.reject_reason = "payload_too_large"
        rejected = run_async(plugin._awareness.maybe_run(settings=_settings(), now=50.0))
        assert rejected["status"] == "push_rejected"
        retried = run_async(plugin._awareness.maybe_run(settings=_settings(), now=51.0))
        assert retried["status"] == "injected"

    def test_per_lanlan_clocks_are_independent(self, tmp_path, run_async):
        bus = FakeBus(
            [
                conversation_record("c1", 10.0, "K"),
                conversation_record("c2", 20.0, "M"),
            ],
            memory_error=True,
        )
        host = FakeHostContext(
            config=FakeConfig(data={"sticker_manager": {"enabled": True}}),
            data_root=tmp_path,
            bus=bus,
        )
        plugin, host = build_plugin(host)
        plugin._library.add(data=PNG_BYTES, desc="d", tags=[])
        settings = _settings(interval=1000.0)
        first = run_async(plugin._awareness.maybe_run(settings=settings, now=50.0))
        assert first["status"] == "injected" and first["target"] == "M"
        # 目标漂到另一张卡：新卡没有时钟，注；不许拿"等节奏"敷衍新出现的对话对象。
        bus.conversations.records = [conversation_record("c1", 999.0, "K")]
        second = run_async(plugin._awareness.maybe_run(settings=settings, now=60.0))
        assert second["status"] == "injected" and second["target"] == "K"

    def test_bus_failure_degrades_to_no_target(self, tmp_path, run_async):
        host = FakeHostContext(
            config=FakeConfig(data={"sticker_manager": {"enabled": True}}),
            data_root=tmp_path,
            bus=FakeBus([], error=True),
        )
        plugin, host = build_plugin(host)
        plugin._library.add(data=PNG_BYTES, desc="d", tags=[])
        result = run_async(plugin._awareness.maybe_run(settings=_settings(), now=50.0))
        assert result["status"] == "no_target"
        assert host.push.calls == []

    def test_maybe_run_never_leaks_exceptions(self, tmp_path, run_async, monkeypatch):
        host = FakeHostContext(
            config=FakeConfig(data={"sticker_manager": {"enabled": True}}),
            data_root=tmp_path,
            bus=_clock_bus(),
        )
        plugin, host = build_plugin(host)
        plugin._library.add(data=PNG_BYTES, desc="d", tags=[])

        async def explode(*args: Any, **kwargs: Any):
            raise RuntimeError("host went sideways")

        monkeypatch.setattr(plugin._awareness, "_run", explode)
        result = run_async(plugin._awareness.maybe_run(settings=_settings(), now=50.0))
        assert result["status"] == "failed"


class TestInjectNow:
    def test_bypasses_interval_but_not_switches(self, tmp_path, run_async):
        host = FakeHostContext(
            config=FakeConfig(data={"sticker_manager": {"enabled": True}}),
            data_root=tmp_path,
            bus=_clock_bus(),
        )
        plugin, host = build_plugin(host)
        plugin._library.add(data=PNG_BYTES, desc="d", tags=[])
        settings = _settings(interval=100000.0)
        assert run_async(plugin._awareness.maybe_run(settings=settings, now=50.0))["status"] == "injected"
        # 间隔远未到期：自动拍 waiting，手动拍照样注（绕的是节奏，不是开关）。
        assert run_async(plugin._awareness.maybe_run(settings=settings, now=51.0))["status"] == "waiting"
        forced = run_async(plugin._awareness.inject_now(settings=settings, lanlan="K", now=52.0))
        assert forced["status"] == "injected"
        blocked = run_async(plugin._awareness.inject_now(settings=_settings(enabled=False), lanlan="K", now=53.0))
        assert blocked["status"] == "disabled"

    def test_manual_hint_targets_without_bus_read(self, tmp_path, run_async):
        # 面板入口带着 _ctx 的角色名来：不必查总线也能注（当前正在看的那张卡）。
        host = FakeHostContext(
            config=FakeConfig(data={"sticker_manager": {"enabled": True}}),
            data_root=tmp_path,
            bus=FakeBus([]),
        )
        plugin, host = build_plugin(host)
        plugin._library.add(data=PNG_BYTES, desc="d", tags=[])
        result = run_async(plugin._awareness.inject_now(settings=_settings(), lanlan="K", now=50.0))
        assert result["status"] == "injected"
        assert host.push.calls[0]["target_lanlan"] == "K"


class TestSnapshot:
    def test_snapshot_reports_last_run_and_wait(self, tmp_path, run_async):
        host = FakeHostContext(
            config=FakeConfig(data={"sticker_manager": {"enabled": True}}),
            data_root=tmp_path,
            bus=_clock_bus(),
        )
        plugin, host = build_plugin(host)
        before = plugin._awareness.snapshot(settings=_settings(interval=100.0), now=50.0)
        assert before["last_inject_at"] is None
        assert before["driver"] == "unavailable"  # 降级路径：轮次源没读通过
        plugin._library.add(data=PNG_BYTES, desc="d", tags=[])
        run_async(plugin._awareness.maybe_run(settings=_settings(interval=100.0), now=50.0))
        after = plugin._awareness.snapshot(settings=_settings(interval=100.0), now=60.0)
        assert after["status"] == "injected"
        assert after["target"] == "K"
        assert after["last_inject_at"] == 50.0
        # 快照报的是"最快多久以后"= 地板（min_interval_sec 默认 60），不是降级时钟。
        assert after["min_next_wait_sec"] == 50.0
