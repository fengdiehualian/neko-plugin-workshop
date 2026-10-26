"""两个"跟本体说话"的开关：尖塔速报 / 尖塔复盘。

玩家口径：「给尖塔速报和尖塔复盘设置两个开关控制是否开启这个」。

- **尖塔速报**（`host_digest_enabled`）：是否把局势悄悄交给本体、由它主动讲一句。
- **尖塔复盘**（`run_reflection_enabled`）：局末是否生成反思并推给本体。

两者都**只影响"跟本体说话"这一路**：关掉之后游戏内弹幕、自动游玩、结构化记录
（损血归因 / 路线图 / 历史页）照旧 —— 这是刻意的，别把开关做成"顺便把记录也停了"。
"""

from __future__ import annotations

import asyncio

import pytest
from plugin.plugins.sts2_autoplay.catgirl_memory import CatgirlMemoryStore
from plugin.plugins.sts2_autoplay.service import STS2AutoplayService


class DummyLogger:
    def debug(self, *a, **k):
        return None

    def info(self, *a, **k):
        return None

    def warning(self, *a, **k):
        return None

    def exception(self, *a, **k):
        return None


def _service() -> STS2AutoplayService:
    return STS2AutoplayService(DummyLogger(), lambda payload: None, lambda **k: None)


_COMBAT_PAYLOAD = {
    "screen": "COMBAT",
    "summary_kind": "combat",
    "player": {"current_hp": 60, "max_hp": 80},
    "enemies": [{"name": "史莱姆", "intent": "GOOP"}],
}
_COMBAT_SNAPSHOT = {"screen": "COMBAT", "floor": 5, "turn": 2, "raw_state": {"combat": {"enemies": []}}, "in_combat": True}


def _terminal_snapshot() -> dict:
    return {
        "classification": {"screen_class": "terminal"},
        "screen": "GAME_OVER",
        "run_id": "run-a",
        "character": "C",
        "floor": 7,
        "act": 1,
        "raw_state": {},
    }


# ---- 默认值与开关本身 -------------------------------------------------------------------


@pytest.mark.unit
def test_both_switches_default_to_on() -> None:
    service = _service()

    assert service._state.host_digest_enabled is True
    assert service._state.run_reflection_enabled is True


@pytest.mark.unit
def test_setters_toggle_state_and_config() -> None:
    service = _service()

    off = service.set_host_digest_enabled(False)
    assert off["status"] == "ok" and off["enabled"] is False
    assert service._state.host_digest_enabled is False
    assert service._cfg["host_digest_enabled"] is False

    off2 = service.set_run_reflection_enabled(False)
    assert off2["enabled"] is False
    assert service._state.run_reflection_enabled is False
    assert service._cfg["run_reflection_enabled"] is False


# ---- 尖塔速报 ---------------------------------------------------------------------------


@pytest.mark.unit
def test_situation_digest_is_silent_when_the_switch_is_off() -> None:
    calls: list[dict] = []
    service = STS2AutoplayService(DummyLogger(), lambda payload: None, lambda **k: calls.append(k))
    service.set_host_digest_enabled(False)

    service._push_host_situation_digest(_COMBAT_SNAPSHOT, _COMBAT_PAYLOAD)

    assert calls == [], "速报关掉后不该再打扰本体"


@pytest.mark.unit
def test_character_announcement_keeps_danmaku_but_drops_the_host_push() -> None:
    """速报关掉时：游戏内弹幕照旧（那是弹幕开关管的），只是不再推给本体。"""
    calls: list[dict] = []
    pushed: list[str] = []
    service = STS2AutoplayService(DummyLogger(), lambda payload: None, lambda **k: calls.append(k))
    service.set_host_digest_enabled(False)
    service._schedule_catgirl_push = lambda text, **kw: pushed.append(text)

    service._maybe_announce_character(
        {"run_id": "run-a", "raw_state": {"run": {"character_name": "储君", "character_id": "REGENT"}}}
    )

    assert pushed == ["这局我抽到储君喵，开打！"], "弹幕不受速报开关影响"
    assert calls == [], "不该推给本体"


# ---- 尖塔复盘 ---------------------------------------------------------------------------


@pytest.mark.unit
def test_run_end_records_but_does_not_pause_when_reflection_is_off(tmp_path) -> None:
    """复盘关掉时：结构化记录照写，但不反思、也不做"暂停-恢复"这一步。"""
    service = _service()
    service._catgirl_memory = CatgirlMemoryStore(path=str(tmp_path / "mem.json"), logger=DummyLogger())
    service._state.autoplay_state = "running"
    service.set_run_reflection_enabled(False)

    async def drive() -> None:
        service._maybe_record_run_memory(dict(_terminal_snapshot()))
        for _ in range(3):
            await asyncio.sleep(0)

    asyncio.run(drive())

    assert len(service._catgirl_memory.runs) == 1, "记录必须照写（历史页/路线图靠它）"
    assert service._catgirl_memory.runs[0]["reflection"] == "", "不该生成反思"
    assert service._state.autoplay_state == "running", "关掉复盘就不该动自动玩的状态"
    assert service._state.reflection_paused is False


@pytest.mark.unit
def test_reflect_run_end_returns_early_when_disabled(tmp_path) -> None:
    service = _service()
    service._catgirl_memory = CatgirlMemoryStore(path=str(tmp_path / "mem.json"), logger=DummyLogger())
    service.set_run_reflection_enabled(False)

    class Gen:
        available = True

        async def generate(self, **kw):
            raise AssertionError("开关关掉时不该调用模型")

    service._catgirl_llm = Gen()
    record = {"run_id": "run-a", "outcome": "defeat", "character": "C", "floor": 3, "lessons": [], "decisions": []}

    asyncio.run(service._reflect_run_end(dict(record), {}))


# ---- 启动时读配置 -----------------------------------------------------------------------


@pytest.mark.unit
def test_startup_reads_the_switch_config() -> None:
    service = _service()

    asyncio.run(service.startup({"host_digest_enabled": False, "run_reflection_enabled": False}))

    assert service._state.host_digest_enabled is False
    assert service._state.run_reflection_enabled is False
