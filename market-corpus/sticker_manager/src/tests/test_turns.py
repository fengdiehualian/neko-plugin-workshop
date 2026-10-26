"""轮次驱动注入（v0.16.0）：总线记录解析 + 水位计数 + 节奏闸。

钉四族：
1. **解析**（纯函数）：宿主原始形状 / SDK 多层包装 / 非用户消息 / 坏时间戳；
2. **水位与计数**（TurnWatcher）：同一轮只看见一次、每卡独立、计数只在显式 reset 后归零；
3. **降级判据**：总线读不通 ≠ 桶是空的——前者退回挂钟，后者是什么都不做；
4. **节奏闸**（core.injection_due_for_turn + Awareness 串联）：N 轮攒一次、地板挡住时
   不消耗计数、每轮都注档的实机口径。
"""

from __future__ import annotations

from typing import Any

import pytest
from conftest import (
    PNG_BYTES,
    FakeBus,
    FakeConfig,
    FakeHostContext,
    build_plugin,
    conversation_record,
    user_message_record,
)
from sticker_manager.core.awareness import (  # pyright: ignore[reportMissingImports] — 包名由 conftest 在测试时注册；独立仓静态面不可解析
    emotional_signal,
    injection_due_for_turn,
    normalize_mode,
)
from sticker_manager.core.configuration import (  # pyright: ignore[reportMissingImports] — 同上
    AwarenessSettings,
    SendSettings,
    StickerManagerSettings,
    StorageSettings,
)
from sticker_manager.services.turns import (  # pyright: ignore[reportMissingImports] — 同上
    latest_user_turn,
    user_turn_of,
)


class _Wrap:
    """SDK 包装层的三种现实形态：.payload / .raw / {"value": ...} 再套一层。"""

    def __init__(self, payload: Any, *, use_raw: bool = False, timestamp: float | None = None):
        if use_raw:
            self.raw = payload
        else:
            self.payload = payload
        if timestamp is not None:
            self.timestamp = timestamp


class TestRecordParsing:
    def test_host_shape_reads_through(self):
        turn = user_turn_of(user_message_record(1234.5, "在吗", "YUI"))
        assert turn is not None
        assert (turn.ts, turn.text, turn.lanlan) == (1234.5, "在吗", "YUI")
        assert turn.is_voice is False

    def test_voice_flag_survives(self):
        turn = user_turn_of(user_message_record(1.0, "喂", "K", is_voice=True))
        assert turn is not None and turn.is_voice is True

    def test_sdk_value_wrapper_is_unwrapped(self):
        # 同门实机症状：from_raw 把非 Mapping 包成 {"value": record}，type 被吞。
        inner = _Wrap({"type": "user_message", "content": "hi", "_ts": 7.0, "lanlan": "K"})
        turn = user_turn_of(_Wrap({"value": inner}))
        assert turn is not None
        assert (turn.ts, turn.text, turn.lanlan) == (7.0, "hi", "K")

    def test_raw_attribute_shape_is_unwrapped(self):
        turn = user_turn_of(_Wrap({"type": "user_message", "content": "x", "_ts": 9.0}, use_raw=True))
        assert turn is not None and turn.ts == 9.0

    def test_missing_ts_falls_back_to_record_timestamp(self):
        turn = user_turn_of(_Wrap({"type": "user_message", "content": "x"}, timestamp=42.0))
        assert turn is not None and turn.ts == 42.0

    @pytest.mark.parametrize(
        "record",
        [
            {"type": "assistant_reply", "content": "x", "_ts": 1.0},
            {"content": "x", "_ts": 1.0},
            "not-a-record",
            None,
            {"type": "user_message"},  # 没内容没时间戳也要能读成 0 秒的一轮
        ],
    )
    def test_non_user_records_are_ignored(self, record: Any):
        if record == {"type": "user_message"}:
            turn = user_turn_of(record)
            assert turn is not None and turn.ts == 0.0
        else:
            assert user_turn_of(record) is None

    def test_string_timestamp_does_not_cut_to_the_front(self):
        # 坏时间戳当 0 垫底（与 core._lenient_float 同纪律）：不许插队成"最新一轮"。
        turn = user_turn_of({"type": "user_message", "content": "x", "_ts": "1e400"})
        assert turn is not None and turn.ts == 0.0

    def test_latest_user_turn_scans_backwards_past_noise(self):
        records = [
            user_message_record(10.0, "早", "K"),
            {"type": "other", "_ts": 99.0},
            user_message_record(20.0, "晚", "K"),
        ]
        turn = latest_user_turn(records)
        assert turn is not None and turn.text == "晚"

    def test_latest_user_turn_of_nothing_is_none(self):
        assert latest_user_turn([]) is None
        assert latest_user_turn([{"type": "other"}]) is None


@pytest.fixture
def watcher():
    """造一个挂在假宿主上的轮次源，连同 bus 一起回（改 bus.memory.records 才有新轮）。"""

    def _make(records: list[dict[str, Any]] | None = None, *, error: bool = False) -> tuple[Any, Any]:
        bus = FakeBus([], memory_records=records, memory_error=error)
        host = FakeHostContext(
            config=FakeConfig(data={"sticker_manager": {"enabled": True}}),
            bus=bus,
        )
        plugin, _host = build_plugin(host)
        return plugin._awareness._turns, bus

    return _make


class TestTurnWatcher:
    def test_burst_counts_every_new_turn_once(self, run_async, watcher):
        w, bus = watcher([user_message_record(100.0 + i, str(i), "K") for i in range(15)])
        turns = run_async(w.poll_all())
        assert len(turns) == 15 and turns[-1].ts == 114.0
        assert w.turns_seen == 15 and w.turns_since("K") == 15
        assert bus.memory.calls[-1]["limit"] == 500
        assert run_async(w.poll_all()) == []
        assert w.turns_seen == 15

    def test_unsorted_interleaved_roles_are_all_counted(self, run_async, watcher):
        w, _bus = watcher([
            user_message_record(103.0, "a2", "A"),
            user_message_record(102.0, "b1", "B"),
            user_message_record(101.0, "a1", "A"),
            {"type": "other", "_ts": 104.0},
        ])
        turns = run_async(w.poll_all())
        assert [(turn.lanlan, turn.ts) for turn in turns] == [("A", 101.0), ("B", 102.0), ("A", 103.0)]
        assert w.turns_seen == 3 and w.turns_since("A") == 2 and w.turns_since("B") == 1
        assert run_async(w.poll()) is None

    def test_nonfinite_timestamp_does_not_poison_watermark(self, run_async, watcher):
        w, bus = watcher([user_message_record(float("inf"), "bad", "K")])
        assert run_async(w.poll()) is None
        bus.memory.records.append(user_message_record(100.0, "good", "K"))
        assert run_async(w.poll()).ts == 100.0

    def test_equal_timestamps_keep_distinct_messages_without_double_counting(self, run_async, watcher):
        w, bus = watcher([
            user_message_record(100.0, "first", "K"),
            user_message_record(100.0, "second", "K"),
        ])
        assert len(run_async(w.poll_all())) == 2
        assert run_async(w.poll_all()) == []
        bus.memory.records.append(user_message_record(100.0, "third", "K"))
        assert len(run_async(w.poll_all())) == 1
        assert w.turns_seen == 3

    def test_read_failure_preserves_counts_and_backlog(self, run_async, watcher):
        w, bus = watcher([user_message_record(100.0, "first", "K")])
        run_async(w.poll_all())
        bus.memory.error = True
        bus.memory.records.extend([user_message_record(101.0 + i, str(i), "K") for i in range(3)])
        assert run_async(w.poll_all()) == [] and w.turns_seen == 1
        bus.memory.error = False
        assert len(run_async(w.poll_all())) == 3 and w.turns_seen == 4

    def test_first_sight_is_a_new_turn(self, run_async: Any, watcher: Any):
        w, _bus = watcher([user_message_record(100.0, "在吗", "K")])
        turn = run_async(w.poll())
        assert turn is not None and turn.lanlan == "K" and turn.text == "在吗"
        assert w.available is True

    def test_same_turn_is_not_seen_twice(self, run_async: Any, watcher: Any):
        w, _bus = watcher([user_message_record(100.0, "在吗", "K")])
        assert run_async(w.poll()) is not None
        assert run_async(w.poll()) is None
        assert w.turns_since("K") == 1  # 计数不因为多问几拍而涨

    def test_watermark_advances_only_forward(self, run_async: Any, watcher: Any):
        w, bus = watcher([user_message_record(100.0, "第一句", "K")])
        assert run_async(w.poll()) is not None
        bus.memory.records.append(user_message_record(90.0, "更早的一条", "K"))
        assert run_async(w.poll()) is None

    def test_counts_are_per_card(self, run_async: Any, watcher: Any):
        w, bus = watcher([user_message_record(100.0, "叫谁呢", "K")])
        assert run_async(w.poll()) is not None
        bus.memory.records = [
            user_message_record(100.0, "叫谁呢", "K"),
            user_message_record(101.0, "叫我", "M"),
        ]
        assert run_async(w.poll()) is not None  # 最新一条换成了 M 的轮
        assert w.turns_since("M") == 1
        bus.memory.records.append(user_message_record(102.0, "又找我", "K"))
        assert run_async(w.poll()) is not None
        assert w.turns_since("K") == 2 and w.turns_since("M") == 1

    def test_reset_count_clears_only_that_card(self, run_async: Any, watcher: Any):
        w, _bus = watcher([user_message_record(100.0, "a", "K")])
        run_async(w.poll())
        w.reset_count("K")
        assert w.turns_since("K") == 0

    def test_empty_bucket_keeps_the_source_alive(self, run_async: Any, watcher: Any):
        # "桶是空的"（没人说话）与"总线读不通"（形状变了）是两回事：
        # 前者 available=True → 上层什么都不做；后者 available=False → 退回挂钟。
        w, _bus = watcher([])
        assert run_async(w.poll()) is None
        assert w.available is True

    def test_bus_error_marks_source_unavailable(self, run_async: Any, watcher: Any):
        w, _bus = watcher(error=True)
        assert run_async(w.poll()) is None
        assert w.available is False

    def test_turn_owner_falls_back_to_current_card(self, run_async: Any, watcher: Any):
        w, bus = watcher([user_message_record(100.0, "没署名")])
        w._plugin.ctx._current_lanlan = "NOW"
        turn = run_async(w.poll())
        assert turn is not None and turn.lanlan == "NOW"
        assert bus.memory.calls[-1]["bucket_id"] == "default"  # 读的就是宿主那个桶

    def test_unsigned_turn_without_current_card_is_dropped(self, run_async: Any, watcher: Any):
        # 归属不明的轮次不计数：钉在空名字上会让计数永远攒不满。
        w, _bus = watcher([user_message_record(100.0, "没署名")])
        assert run_async(w.poll()) is None
        assert w.available is True

    def test_snapshot_reports_driver_and_counts(self, run_async: Any, watcher: Any):
        w, _bus = watcher([user_message_record(100.0, "在吗", "K")])
        run_async(w.poll())
        snap = w.snapshot()
        assert snap["source"] == "bus"
        assert snap["turns_since_inject"] == {"K": 1}
        assert snap["latest_turn_ts"] == 100.0

    def test_poll_never_raises_on_broken_bus(self, run_async: Any):
        class _Boom:
            def get(self, **kwargs: Any) -> Any:
                raise RuntimeError("zmq went sideways")

        host = FakeHostContext(config=FakeConfig(data={"sticker_manager": {"enabled": True}}))
        host.bus = type("B", (), {"memory": _Boom(), "conversations": _Boom()})()
        plugin, _host = build_plugin(host)
        assert run_async(plugin._awareness._turns.poll()) is None


class TestInjectionDue:
    def test_interval_n_waits_for_n_turns(self):
        assert injection_due_for_turn("interval_n", turns_since_inject=2, interval_n=3, floor_remaining_sec=0.0) is False
        assert injection_due_for_turn("interval_n", turns_since_inject=3, interval_n=3, floor_remaining_sec=0.0) is True

    def test_every_mode_fires_on_any_new_turn(self):
        assert injection_due_for_turn("every_user_message", turns_since_inject=1, interval_n=99, floor_remaining_sec=0.0) is True

    def test_floor_beats_both_modes(self):
        for mode in ("every_user_message", "interval_n"):
            assert (
                injection_due_for_turn(mode, turns_since_inject=99, interval_n=1, floor_remaining_sec=1.0)
                is False
            )

    def test_interval_n_below_one_is_clamped_to_one(self):
        assert injection_due_for_turn("interval_n", turns_since_inject=1, interval_n=0, floor_remaining_sec=0.0) is True

    def test_signal_points_before_the_count_is_full(self):
        # v0.20.0 事件门控：这句有情绪就点名，不等 N 轮攒满（同门 fc 的 tone nudge
        # 就是这个形状——触发源是刚读完的那句用户话，不是计数器）。
        assert (
            injection_due_for_turn(
                "interval_n", turns_since_inject=0, interval_n=12, floor_remaining_sec=0.0, signal=True
            )
            is True
        )

    def test_floor_beats_the_signal_too(self):
        # 地板不许被门控绕过，否则连珠炮情绪句会把注入打成每句都点。
        assert (
            injection_due_for_turn(
                "interval_n", turns_since_inject=0, interval_n=12, floor_remaining_sec=0.5, signal=True
            )
            is False
        )

    def test_event_gated_off_restores_count_only_rhythm(self):
        assert (
            injection_due_for_turn(
                "interval_n",
                turns_since_inject=0,
                interval_n=12,
                floor_remaining_sec=0.0,
                signal=True,
                event_gated=False,
            )
            is False
        )


class TestEmotionalSignal:
    """v0.20.0 点名的判据：只认"这句里有人在反应"。"""

    @pytest.mark.parametrize(
        "text",
        ["今天加班到十一点，累瘫了", "哈哈哈哈笑死我了", "你是不是生我气了……", "想你了~", "帮我看看这个好不好呀~~"],
    )
    def test_reactions_count(self, text: str):
        assert emotional_signal(text) is True

    @pytest.mark.parametrize(
        "text",
        ["现在几点了", "帮我算一下 128 * 7", "把报告第三节的日期改成 5 月", "python 的 with 怎么用？", ""],
    )
    def test_business_questions_do_not(self, text: str):
        # 误报的代价是她连着甩图，比漏报难看——参考与同门的判据都是"办事的时候纯文字"。
        assert emotional_signal(text) is False

    @pytest.mark.parametrize("mode", ["", "whenever", None, 7, "OFF"])
    def test_unlisted_mode_degrades_to_default(self, mode: Any):
        assert normalize_mode(mode) == "interval_n"


class TestTurnDrivenAwareness:
    """端到端：新的驱动源接上注入器之后，节奏到底长什么样。"""

    @staticmethod
    def _plugin(tmp_path: Any, records: list[dict[str, Any]]) -> tuple[Any, Any]:
        host = FakeHostContext(
            config=FakeConfig(data={"sticker_manager": {"enabled": True}}),
            data_root=tmp_path,
            bus=FakeBus([conversation_record("c", 1.0, "K")], memory_records=records),
        )
        plugin, host = build_plugin(host)
        plugin._library.add(data=PNG_BYTES, desc="猫猫挥手", tags=[])
        return plugin, host

    def test_burst_reaches_interval_without_replaying_old_cues(self, tmp_path, run_async):
        plugin, host = self._plugin(tmp_path, [
            user_message_record(100.0 + i, "business", "K") for i in range(3)
        ])
        settings = _turns(interval_n=3, floor=0.0)
        assert run_async(plugin._awareness.maybe_run(settings=settings, now=50.0))["status"] == "injected"
        assert len(host.push.calls) == 1
        assert plugin._awareness.turns_seen == 3 and plugin._awareness.turn_texts == 3
        assert plugin._awareness._turns.turns_since("K") == 0
        assert run_async(plugin._awareness.maybe_run(settings=settings, now=51.0))["status"] == "idle"

    def test_interleaved_roles_each_receive_latest_cue(self, tmp_path, run_async):
        plugin, host = self._plugin(tmp_path, [
            user_message_record(100.0, "business", "A"),
            user_message_record(101.0, "business", "B"),
            user_message_record(102.0, "business", "A"),
        ])
        settings = _turns(interval_n=1, floor=0.0)
        run_async(plugin._awareness.maybe_run(settings=settings, now=50.0))
        assert [call["target_lanlan"] for call in host.push.calls] == ["B", "A"]
        assert plugin._awareness.turns_seen == 3
        assert plugin._awareness._turns.turns_since("A") == 0
        assert plugin._awareness._turns.turns_since("B") == 0

    def test_superseded_emotion_is_counted_but_not_injected(self, tmp_path, run_async):
        plugin, host = self._plugin(tmp_path, [
            user_message_record(100.0, "好开心", "K"),
            user_message_record(101.0, "business", "K"),
        ])
        settings = _turns(interval_n=99, floor=0.0)
        assert run_async(plugin._awareness.maybe_run(settings=settings, now=50.0))["status"] == "not_due"
        assert plugin._awareness.turn_texts == 2 and plugin._awareness.signal_hits == 1
        assert host.push.calls == []

    def test_no_new_turn_injects_nothing(self, tmp_path, run_async):
        plugin, host = self._plugin(tmp_path, [user_message_record(100.0, "在吗", "K")])
        settings = _turns(mode="every_user_message", floor=0.0)
        assert run_async(plugin._awareness.maybe_run(settings=settings, now=50.0))["status"] == "injected"
        # 同一条用户轮还在桶里：不重复注。老挂钟会在下一个小时再来一次，这正是本轮拆掉的行为。
        assert run_async(plugin._awareness.maybe_run(settings=settings, now=51.0))["status"] == "idle"
        assert len(host.push.calls) == 1

    def test_interval_n_fires_on_the_third_turn(self, tmp_path, run_async):
        plugin, host = self._plugin(tmp_path, [user_message_record(100.0, "一", "K")])
        memory = host.bus.memory
        settings = _turns(mode="interval_n", interval_n=3, floor=0.0)
        assert run_async(plugin._awareness.maybe_run(settings=settings, now=50.0))["status"] == "not_due"
        memory.records.append(user_message_record(101.0, "二", "K"))
        assert run_async(plugin._awareness.maybe_run(settings=settings, now=51.0))["status"] == "not_due"
        assert host.push.calls == []
        memory.records.append(user_message_record(102.0, "三", "K"))
        due = run_async(plugin._awareness.maybe_run(settings=settings, now=52.0))
        assert due["status"] == "injected"
        assert host.push.calls[-1]["target_lanlan"] == "K"

    def test_signal_turn_points_immediately_without_waiting_for_the_count(self, tmp_path, run_async):
        # v0.20.0 事件门控端到端：interval_n=12 还没攒满，但这句有情绪 ⇒ 立刻点名，
        # 且推出去的那条带 lede、trigger 记成 signal（面板与日志要靠这个占比判断门控有没有活）。
        plugin, host = self._plugin(tmp_path, [user_message_record(100.0, "今天被夸了，好开心", "K")])
        settings = _turns(mode="interval_n", interval_n=12, floor=0.0)
        result = run_async(plugin._awareness.maybe_run(settings=settings, now=50.0))
        assert result["status"] == "injected" and result["trigger"] == "signal"
        assert "（表情包点名）" in host.push.calls[-1]["parts"][0]["text"]
        assert plugin._awareness.trigger_counts["signal"] == 1

    def test_business_turn_still_follows_the_count(self, tmp_path, run_async):
        # 反向半：判据不许把每轮都变成点名轮，纯信息问答照旧走计数。
        plugin, host = self._plugin(tmp_path, [user_message_record(100.0, "帮我算一下 128 * 7", "K")])
        settings = _turns(mode="interval_n", interval_n=12, floor=0.0)
        assert run_async(plugin._awareness.maybe_run(settings=settings, now=50.0))["status"] == "not_due"
        assert host.push.calls == []

    def test_event_gated_off_is_exactly_the_old_rhythm(self, tmp_path, run_async):
        plugin, host = self._plugin(tmp_path, [user_message_record(100.0, "今天被夸了，好开心", "K")])
        settings = _turns(mode="interval_n", interval_n=12, floor=0.0, event_gated=False)
        assert run_async(plugin._awareness.maybe_run(settings=settings, now=50.0))["status"] == "not_due"

    def test_floor_block_does_not_consume_the_count(self, tmp_path, run_async):
        # 地板只在"已经注过一次"之后生效（首轮没有历史可挡），所以先注一发再验：
        # 攒够 N 轮但地板没过时计数留着，下一轮立刻命中——否则"每 3 轮"会静默
        # 退化成"每 4、5 轮"（同门 whisper.py 的同一把尺）。
        plugin, host = self._plugin(tmp_path, [user_message_record(100.0, "一", "K")])
        memory = host.bus.memory
        settings = _turns(mode="interval_n", interval_n=1, floor=1000.0)
        assert run_async(plugin._awareness.maybe_run(settings=settings, now=50.0))["status"] == "injected"
        memory.records.append(user_message_record(101.0, "二", "K"))
        assert run_async(plugin._awareness.maybe_run(settings=settings, now=51.0))["status"] == "not_due"
        assert plugin._awareness._turns.turns_since("K") == 1  # 被地板挡住：配额不烧
        memory.records.append(user_message_record(102.0, "三", "K"))
        assert run_async(plugin._awareness.maybe_run(settings=settings, now=9999.0))["status"] == "injected"

    def test_failed_push_keeps_the_count(self, tmp_path, run_async):
        plugin, host = self._plugin(tmp_path, [user_message_record(100.0, "一", "K")])
        settings = _turns(mode="interval_n", interval_n=1, floor=0.0)
        host.push.reject_reason = "backpressure"  # 一次性：消费掉自己就恢复正常
        assert run_async(plugin._awareness.maybe_run(settings=settings, now=50.0))["status"] == "push_rejected"
        assert plugin._awareness._turns.turns_since("K") == 1  # 被拒不烧配额
        host.bus.memory.records.append(user_message_record(101.0, "二", "K"))
        assert run_async(plugin._awareness.maybe_run(settings=settings, now=51.0))["status"] == "injected"
        assert plugin._awareness._turns.turns_since("K") == 0

    def test_injection_resolves_target_from_the_turn(self, tmp_path, run_async):
        # 轮次记录自己带归属角色：即使 conversations 桶在说谎，也注给它而不是别人。
        plugin, host = self._plugin(tmp_path, [user_message_record(100.0, "在吗", "YUI")])
        run_async(plugin._awareness.maybe_run(settings=_turns(floor=0.0, interval_n=1), now=50.0))
        assert host.push.calls[-1]["target_lanlan"] == "YUI"

    def test_driver_shows_as_bus_once_the_source_works(self, tmp_path, run_async):
        plugin, host = self._plugin(tmp_path, [user_message_record(100.0, "在吗", "K")])
        run_async(plugin._awareness.maybe_run(settings=_turns(floor=0.0, interval_n=1), now=50.0))
        snap = plugin._awareness.snapshot(settings=_turns(floor=0.0, interval_n=1), now=51.0)
        assert snap["driver"] == "bus"
        assert snap["inject_mode"] == "interval_n" and snap["inject_interval_n"] == 1


def _turns(
    *,
    mode: str = "interval_n",
    interval_n: int = 3,
    floor: float = 60.0,
    event_gated: bool = True,
) -> StickerManagerSettings:
    return StickerManagerSettings(
        enabled=True,
        send=SendSettings(),
        storage=StorageSettings(),
        awareness=AwarenessSettings(
            enabled=True,
            inject_mode=mode,
            inject_interval_n=interval_n,
            min_interval_sec=floor,
            event_gated=event_gated,
        ),
    )


class TestConfigReadIn:
    """新键的读入面：垃圾必须收敛成合法值，不许让她的行为变野。"""

    @staticmethod
    def _awareness(**raw: Any) -> AwarenessSettings:
        # from_config 吃的是整份配置（顶层 `[sticker_manager]` 段），不是段本身。
        return StickerManagerSettings.from_config(
            {"sticker_manager": {"enabled": True, "awareness": raw}}
        ).awareness

    def test_defaults_are_turn_driven(self):
        settings = self._awareness()
        assert settings.inject_mode == "interval_n"
        # 默认 0 跟随档位；矜持承接 0.9.8（3），自然承接 0.9.10（1），爱发为 1。
        # 上一版这里是写死的 8，而实机证明"密度"不是主因、"什么时候该想到"才是——
        # 于是节奏改由档位统一驱动，主人想定死仍可在文件里写正数（下一条测试钉着）。
        assert settings.inject_interval_n == 0
        assert settings.min_interval_sec == 60.0
        assert settings.event_gated is True  # v0.20.0：事件门控点名默认开
        assert settings.interval_sec == 3600.0  # 降级时钟保留老默认

    def test_written_keys_are_honoured(self):
        settings = self._awareness(
            inject_mode="every_user_message",
            inject_interval_n=7,
            min_interval_sec=5.0,
            event_gated=False,
        )
        assert settings.inject_mode == "every_user_message"
        assert settings.inject_interval_n == 7
        assert settings.min_interval_sec == 5.0
        assert settings.event_gated is False

    @pytest.mark.parametrize("raw", ["no", 1, None, ""])
    def test_non_bool_event_gated_sinks_to_default(self, raw: Any):
        # 与 enabled 同一把 `_as_bool` 尺：垃圾值不许让她的行为变野。
        assert self._awareness(event_gated=raw).event_gated is True

    @pytest.mark.parametrize("mode", ["", "off", "ON_TRIGGER", 7, None])
    def test_unknown_mode_sinks_to_interval_n(self, mode: Any):
        assert self._awareness(inject_mode=mode).inject_mode == "interval_n"

    def test_out_of_range_numbers_are_clamped(self):
        # v0.19.0 起 0 是**合法哨兵**（跟随档位），不再是"<1 按 1 收"；负数夹到 0。
        settings = self._awareness(inject_interval_n=-5, min_interval_sec=-30.0)
        assert settings.inject_interval_n == 0
        assert settings.min_interval_sec == 0.0  # 地板可以关掉


class _RecLogger:
    """录音假 logger：只用来断言留痕（形状对齐 test_runstats 的同名替身）。"""

    def __init__(self) -> None:
        self.lines: list[str] = []

    def _emit(self, message: str = "", **_kwargs: Any) -> None:
        self.lines.append(str(message))

    info = warning = error = debug = _emit

    def text(self) -> str:
        return "\n".join(self.lines)


class TestSignalAccounting:
    """v0.20.1：`pointer=0/N` 这种读数必须能解释病因，两种病的处置完全相反。"""

    @staticmethod
    def _feed(tmp_path, texts, settings, run_async, *, recorder: bool = False):
        host = FakeHostContext(
            config=FakeConfig(data={"sticker_manager": {"enabled": True}}),
            data_root=tmp_path,
            bus=FakeBus([conversation_record("c", 1.0, "K")], memory_records=[]),
        )
        plugin, host = build_plugin(host)
        plugin._library.add(data=PNG_BYTES, desc="猫猫挥手", tags=[])
        if recorder:
            # 注入器在构造时就把 logger 抓走了（`Awareness(self, ..., logger=self.logger)`），
            # 事后只换 plugin.logger 不会生效——两处都要换。
            plugin.logger = _RecLogger()
            plugin._awareness._logger = plugin.logger
        for index, text in enumerate(texts):
            host.bus.memory.records.append(user_message_record(100.0 + index, text, "K"))
            run_async(plugin._awareness.maybe_run(settings=settings, now=1000.0 + index))
        return plugin

    def test_empty_bus_text_is_counted_separately_from_a_miss(self, tmp_path, run_async):
        # 总线把 content 写成空串（形状变了）与"这句确实没情绪"必须分得开：
        # 前者要修读法，后者什么都不用改。
        plugin = self._feed(tmp_path, ["现在几点了", ""], _turns(interval_n=99, floor=0.0), run_async)
        assert plugin._awareness.turn_texts == 1
        assert plugin._awareness.turn_empty == 1
        assert plugin._awareness.signal_hits == 0

    def test_signal_hit_is_counted_even_when_the_injection_is_not_due(self, tmp_path, run_async):
        # 命中记在**判据求值那一瞬**，不等注入落地：否则被地板挡住的那些轮会让
        # signal_hits 恒小于真实命中数，读数反过来变成"门控没工作"的假证。
        # 第一注必成（该角色卡还没有历史，地板无从挡），第二注才被 500 秒地板拦下。
        plugin = self._feed(
            tmp_path, ["今天好开心啊", "还是好开心"], _turns(interval_n=99, floor=500.0), run_async
        )
        assert plugin._awareness.signal_hits == 2
        assert plugin._awareness.trigger_counts.get("signal", 0) == 1

    def test_injection_log_carries_the_turn_length(self, tmp_path, run_async):
        plugin = self._feed(
            tmp_path, ["现在几点了"], _turns(interval_n=1, floor=0.0), run_async, recorder=True
        )
        assert "turn_chars=5" in plugin.logger.text()  # "现在几点了" 五个字，只记长度不记原话
