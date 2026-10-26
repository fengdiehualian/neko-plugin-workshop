# pyright: reportMissingImports=false
"""「配表情积极度」三档的门（v0.14.0 起，v0.19.0 扩到三面）。

钉五件事：
1. **档位驱动三根"让她想起来"的面**：注入意愿段 + 工具描述的触发判据/许可句 + 点名节奏
   （矜持 3 / 自然 1 / 爱发 1 轮，配置写正数覆盖）；
2. **发送层的四把尺一口都不吃档位**（陷阱 26）：冷却 / 最近不重复 / 概率 / 复用窗口
   在三档下解析结果必须相同——这条是"档位不是万能旋钮"的反向门；
3. **默认档 = 实测基线**：natural 承接 0.9.10 eager；
4. **读入收敛**：不在册/非字符串/缺键一律回 natural，配置写错不该让她的行为变野；
5. **入口与面板**：`set_eagerness` 写对配置路径、拒非法值、盘写不进去如实报错；
   dashboard 露当前档，且节奏读数给的是**生效值**不是哨兵 0。
"""

from __future__ import annotations

from conftest import FakeConfig, FakeHostContext, build_plugin
from sticker_manager.core.awareness import build_awareness_text
from sticker_manager.core.catalog import Sticker
from sticker_manager.core.configuration import (
    EAGERNESS_LEVELS,
    AwarenessSettings,
    SendSettings,
    StickerManagerSettings,
)
from sticker_manager.core.eagerness import (
    NEXT_STEP_NOTE,
    TRIGGER_CRITERIA,
    effective_inject_interval_n,
)
from sticker_manager.core.tool_surface import build_send_tool_description
from sticker_manager.services.awareness import Awareness


def _settings(tmp_path, *, send: dict | None = None, enabled=True):
    host = FakeHostContext(
        data_root=tmp_path,
        config=FakeConfig(data={"sticker_manager": {"enabled": enabled, "send": send or {}}}),
    )
    plugin, host = build_plugin(host)
    plugin._settings = StickerManagerSettings.from_config(host.config.data)
    return plugin, host


def _stickers(count: int = 2):
    return [
        Sticker(id=f"s{i}", file=f"{i}.gif", desc=f"梗{i}", tags=[], added_at=float(i))
        for i in range(count)
    ]


def _text(eagerness: str):
    return build_awareness_text(_stickers(), eagerness=eagerness)


class TestReadIn:
    def test_default_tier_is_natural(self):
        assert SendSettings().eagerness == "natural"
        assert StickerManagerSettings.from_config({}).send.eagerness == "natural"

    def test_unknown_tier_falls_back_to_default(self):
        for junk in ("wild", "", "  ", 7, None, True, ["eager"]):
            got = StickerManagerSettings.from_config(
                {"sticker_manager": {"send": {"eagerness": junk}}}
            ).send.eagerness
            assert got == "natural", junk

    def test_surrounding_whitespace_is_trimmed(self):
        got = StickerManagerSettings.from_config(
            {"sticker_manager": {"send": {"eagerness": "  eager  "}}}
        ).send.eagerness
        assert got == "eager"


class TestInjectionText:
    def test_three_tiers_differ_in_will_without_preemptive_refusal_rules(self):
        texts = {tier: _text(tier) for tier in EAGERNESS_LEVELS}
        assert len({text for text in texts.values()}) == 3
        for tier, text in texts.items():
            for banned in ("别重试", "最近不重复", "概率", "节奏闸"):
                assert banned not in text, f"{tier} 提醒预先告知拒绝规则"
            assert "sticker_send" in text

    def test_natural_uses_the_0910_baseline_and_eager_can_evolve(self):
        assert "自然档：轻松聊天默认配一张贴切图" in _text("natural")
        assert "矜持档" in _text("reserved") and "多数时候纯文字就够了" not in _text("reserved")
        assert "爱发档：轻松聊天默认配一张贴切图" in _text("eager")
        assert "不等强烈情绪或要图" in _text("eager")

    def test_unlisted_tier_degrades_to_natural(self):
        assert _text("wild") == _text("natural")

    def test_service_passes_the_configured_tier(self, tmp_path):
        plugin, _host = _settings(tmp_path, send={"eagerness": "reserved"})
        assert plugin._settings.send.eagerness == "reserved"


class TestEntry:
    def test_set_writes_config_path_and_returns_applied(self, tmp_path, run_async):
        plugin, host = _settings(tmp_path)
        result = run_async(plugin.set_eagerness_entry(eagerness="eager"))
        assert result.is_ok()
        assert result.value["note"] == "eagerness_set"
        assert result.value["eagerness"] == "eager"
        assert host.config.writes == [("sticker_manager.send.eagerness", "eager")]

    def test_invalid_tier_is_refused_not_defaulted(self, tmp_path, run_async):
        plugin, host = _settings(tmp_path)
        result = run_async(plugin.set_eagerness_entry(eagerness="wild"))
        assert not result.is_ok() and str(result.error) == "invalid_value"
        assert host.config.writes == []

    def test_config_failure_surfaces(self, tmp_path, run_async):
        plugin, host = _settings(tmp_path)
        host.config.set_error = RuntimeError("boom")
        result = run_async(plugin.set_eagerness_entry(eagerness="reserved"))
        assert not result.is_ok() and str(result.error) == "config_unavailable"

    def test_dashboard_exposes_current_tier(self, tmp_path, run_async):
        plugin, _host = _settings(tmp_path, send={"eagerness": "eager"})
        state = run_async(plugin.dashboard_context())
        assert state["eagerness"] == "eager"


class TestTierDrivesThreeSurfaces:
    """v0.19.0：一档驱动三面（判据 / 点名句 / 节奏），但发送层一口都不吃。"""

    def test_each_tier_names_its_own_trigger_criteria(self):
        catalog = "・困与睡（9 张） — 深夜犯困时用。"
        texts = {tier: build_send_tool_description(catalog, tier) for tier in EAGERNESS_LEVELS}
        for tier in EAGERNESS_LEVELS:
            assert "什么时候该想到它" in texts[tier], f"{tier} 档的描述里没有触发判据"
            assert TRIGGER_CRITERIA[tier] in texts[tier]
        assert len(set(texts.values())) == len(EAGERNESS_LEVELS)

    def test_eager_says_dont_wait_to_be_asked(self):
        # 主人报的原话是"每次都是我提醒她才知道调用"——eager 档必须正面回应这一点。
        assert "不用等用户点名要图" in TRIGGER_CRITERIA["eager"]

    def test_injection_names_the_tool_and_optional_caption_without_merging(self):
        text = build_awareness_text([Sticker(id="a", file="a.png", desc="甲", tags=[])], eagerness="eager")
        assert "sticker_list" in text and "sticker_send" in text and "text" in text
        assert "sticker_send(sticker_id=候选ID)" in text
        assert "text 留空即可" in text
        assert "先在正常回复通道说完正文" in text and "图片独立发送" in text
        assert "没有合适的就纯文字" in text
        assert "一起发出去" not in text
        assert len(text) < 340, f"注入正文又长回目录复读机了：{len(text)} 字"

    def test_success_result_tells_her_what_next(self, tmp_path, run_async):
        plugin, _host = _settings(tmp_path, send={"eagerness": "eager"})
        plugin._library.load(force=True)
        plugin._library.create_group("困与睡", "深夜犯困时用", "")
        sticker, _ = plugin._library.add(
            data=b"\x89PNG\r\n\x1a\n" + b"\x00" * 64 + b"|y", desc="哈欠", tags=[], group="困与睡"
        )
        result = run_async(plugin.tool_sticker_send(sticker_id=sticker.id, text="先困了"))
        assert result.get("ok") is True, result
        assert result.get("note") == NEXT_STEP_NOTE

    def test_the_note_never_names_a_throttle(self):
        # 陷阱 29 的纪律挪到返回值上：闸只在撞的那一刻由 `_SEND_HINTS` 就地说，
        # 成功指引里不许提前教育她"会被挡"。
        for banned in ("冷却", "最近不重复", "概率", "force"):
            assert banned not in NEXT_STEP_NOTE


class TestTierDoesNotTouchSendGates:
    """反向门（陷阱 26）：档位在"让她想起来"这侧走满，发送侧那四把尺一格都不许动。"""

    def test_the_four_send_rulers_are_tier_independent(self, tmp_path):
        baselines = None
        for tier in EAGERNESS_LEVELS:
            host = FakeHostContext(
                data_root=tmp_path / tier,
                config=FakeConfig(data={"sticker_manager": {"enabled": True, "send": {"eagerness": tier}}}),
            )
            settings = StickerManagerSettings.from_config(host.config.data)
            got = (
                settings.send.cooldown_sec,
                settings.send.recent_dedup_count,
                settings.send.probability,
                settings.send.probability_reuse_sec,
            )
            baselines = baselines or got
            assert got == baselines, f"{tier} 档擅自改了发送层的尺"


class TestTierDrivenCadence:
    def test_each_tier_has_its_own_cadence(self):
        assert effective_inject_interval_n("reserved", 0) == 3
        assert effective_inject_interval_n("natural", 0) == 1
        assert effective_inject_interval_n("eager", 0) == 1
        # 不在册的档位退到 natural 的节奏，不炸也不当 0 用。
        assert effective_inject_interval_n("wild", 0) == 1

    def test_written_number_beats_the_tier(self):
        assert effective_inject_interval_n("eager", 20) == 20
        assert effective_inject_interval_n("reserved", 1) == 1
        # 负数不是"倒着数"，按"没写"处理（配置读入本来就会夹到 0，这里防的是旁路）。
        assert effective_inject_interval_n("eager", -3) == 1

    def test_awareness_uses_the_tier_of_the_running_settings(self, tmp_path):
        plugin, host = _settings(tmp_path, send={"eagerness": "eager"})
        assert Awareness.interval_n(plugin._settings) == 1
        host.config.data["sticker_manager"]["send"]["eagerness"] = "reserved"
        plugin._settings = StickerManagerSettings.from_config(host.config.data)
        assert Awareness.interval_n(plugin._settings) == 3
        # 主人定死一个数以后，档位再怎么切都不该影响节奏。
        host.config.data["sticker_manager"]["awareness"] = {"inject_interval_n": 7}
        plugin._settings = StickerManagerSettings.from_config(host.config.data)
        assert Awareness.interval_n(plugin._settings) == 7

    def test_dashboard_reports_the_effective_cadence_not_the_sentinel(self, tmp_path, run_async):
        # 面板回 0 会让人以为"每 0 轮一次"——那是拿哨兵值当读数，仪器说谎的一种。
        plugin, _host = _settings(tmp_path, send={"eagerness": "eager"})
        state = run_async(plugin.dashboard_context())
        assert state["awareness"]["inject_interval_n"] == 1
        assert AwarenessSettings().inject_interval_n == 0  # 配置面默认仍是"跟随"
