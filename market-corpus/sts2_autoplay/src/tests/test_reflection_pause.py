"""复盘期间暂停自动游玩，复盘完再继续。

玩家口径：「复盘的时候暂停自动游玩，复盘完再继续」——否则猫娘会在复盘还没讲完时就冲进主菜单
开下一局。

两条硬约束：

* **一定要恢复**：卡在"暂停"上等于猫娘永久停手（这比不暂停糟得多），所以 `_reflect_run_end`
  用 `finally` 恢复，连 LLM 没产出、抛异常也要恢复。
* **不擅自恢复**：只在"确实是我们为了复盘暂停的、且玩家没在这期间动过档位"时才恢复 ——
  玩家手动暂停或切到 standby 之后被我们偷偷恢复，比不恢复更糟。
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


_TERMINAL = {
    "classification": {"screen_class": "terminal"},
    "screen": "GAME_OVER",
    "run_id": "run-a",
    "character": "C",
    "floor": 7,
    "act": 1,
    "raw_state": {},
}


# ---- 暂停/恢复基本语义 -------------------------------------------------------------------


@pytest.mark.unit
def test_reflection_pauses_a_running_autoplay() -> None:
    service = _service()
    service._state.autoplay_state = "running"

    service._pause_for_reflection()

    assert service._state.autoplay_state == "paused"
    assert service._state.pause_reason == "run_reflection"
    assert service._state.reflection_paused is True


@pytest.mark.unit
def test_pause_does_nothing_when_autoplay_is_not_running() -> None:
    service = _service()
    service._state.autoplay_state = "idle"

    service._pause_for_reflection()

    assert service._state.autoplay_state == "idle"
    assert service._state.reflection_paused is False


@pytest.mark.unit
def test_resume_restores_running_after_our_own_pause() -> None:
    service = _service()
    service._state.autoplay_state = "running"
    service._pause_for_reflection()

    service._resume_after_reflection()

    assert service._state.autoplay_state == "running"
    assert service._state.reflection_paused is False


@pytest.mark.unit
def test_resume_is_skipped_when_the_player_took_over() -> None:
    """玩家在复盘期间手动暂停/切档位 -> 我们不许擅自恢复。"""
    service = _service()
    service._state.autoplay_state = "running"
    service._pause_for_reflection()
    service._state.pause_reason = "user"  # 玩家自己又暂停了一次

    service._resume_after_reflection()

    assert service._state.autoplay_state == "paused"


@pytest.mark.unit
def test_resume_is_skipped_in_standby() -> None:
    service = _service()
    service._state.autoplay_state = "running"
    service._pause_for_reflection()
    service._state.control_mode = "standby"

    service._resume_after_reflection()

    assert service._state.autoplay_state != "running"


@pytest.mark.unit
def test_resume_without_a_prior_pause_does_nothing() -> None:
    service = _service()
    service._state.autoplay_state = "paused"

    service._resume_after_reflection()

    assert service._state.autoplay_state == "paused"


# ---- 与局末记录串起来 --------------------------------------------------------------------


@pytest.mark.unit
def test_run_end_pauses_and_the_reflection_resumes(tmp_path) -> None:
    """**整条链路**：终端屏 -> 暂停 -> 复盘（LLM 没产出也要恢复）-> 恢复运行。"""
    service = _service()
    service._catgirl_memory = CatgirlMemoryStore(path=str(tmp_path / "mem.json"), logger=DummyLogger())
    service._state.autoplay_state = "running"

    class SilentGen:
        available = True

        async def generate(self, **kw):
            return None  # 复盘没产出，也必须恢复

    service._catgirl_llm = SilentGen()

    async def drive() -> None:
        service._maybe_record_run_memory(dict(_TERMINAL))
        assert service._state.autoplay_state == "paused", "复盘期间应暂停"
        # 让 fire-and-forget 的复盘任务跑完
        for _ in range(5):
            await asyncio.sleep(0)

    asyncio.run(drive())

    assert service._state.autoplay_state == "running", "复盘结束必须恢复自动游玩"
    assert service._state.reflection_paused is False


@pytest.mark.unit
def test_reflection_resumes_even_when_it_raises(tmp_path) -> None:
    service = _service()
    service._catgirl_memory = CatgirlMemoryStore(path=str(tmp_path / "mem.json"), logger=DummyLogger())

    class BoomGen:
        available = True

        async def generate(self, **kw):
            raise RuntimeError("model down")

    service._catgirl_llm = BoomGen()
    service._state.autoplay_state = "running"
    service._pause_for_reflection()

    record = {"run_id": "run-a", "outcome": "defeat", "character": "C", "floor": 3, "lessons": [], "decisions": []}
    asyncio.run(service._reflect_run_end(dict(record), {}))

    assert service._state.autoplay_state == "running"
