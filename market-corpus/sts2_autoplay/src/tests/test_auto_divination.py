"""自动占卜：自定义事件屏（水晶球）上按下标依次试。

**实盘证据（2026-10-07）**：这一页把 2 个占卜按钮 + 15 个图块都暴露成 `choose_event_option` 的
选项，但**不是每个下标都有用** —— 点 [3] 回 `pending` 而局面一字未变，点 [4] 才 `completed`、
图块揭开（`screen_progress` 从 `items=15` 变 `items=0`）。而规划器默认死盯一个下标，
于是就成了那条 `[sts2_stuck] 动作没生效（无进展）… repeat=8`。

做法：每次派发把 `option_index` 往前推一格（尾部回绕），把所有下标扫一遍。
"""

from __future__ import annotations

from typing import Any

import pytest
from plugin.plugins.sts2_autoplay.service import STS2AutoplayService


class _Logger:
    def info(self, *args: Any, **kwargs: Any) -> None: ...
    def warning(self, *args: Any, **kwargs: Any) -> None: ...
    def error(self, *args: Any, **kwargs: Any) -> None: ...
    def debug(self, *args: Any, **kwargs: Any) -> None: ...


def _service() -> STS2AutoplayService:
    return STS2AutoplayService(_Logger(), lambda payload: None, lambda **kwargs: None)


def _crystal_snapshot(*, floor: int = 22, option_count: int = 5) -> dict[str, Any]:
    return {
        "screen": "EVENT",
        "floor": floor,
        "raw_state": {
            "event": {
                "screen_progress": "divinations_left=3 items=15",
                "options": [{"index": i} for i in range(option_count)],
            }
        },
    }


def _operation(index: int = 0) -> dict[str, Any]:
    return {"action_type": "choose_event_option", "kwargs": {"option_index": index}}


@pytest.mark.unit
def test_cursor_walks_every_option() -> None:
    service = _service()
    snapshot = _crystal_snapshot(option_count=5)

    seen = [service._advance_custom_event_cursor(snapshot, _operation())["kwargs"]["option_index"] for _ in range(5)]

    assert seen == [0, 1, 2, 3, 4], seen


@pytest.mark.unit
def test_cursor_wraps_around() -> None:
    service = _service()
    snapshot = _crystal_snapshot(option_count=3)

    seen = [service._advance_custom_event_cursor(snapshot, _operation())["kwargs"]["option_index"] for _ in range(7)]

    assert seen == [0, 1, 2, 0, 1, 2, 0], seen


@pytest.mark.unit
def test_cursor_resets_on_another_screen() -> None:
    service = _service()
    first = _crystal_snapshot(floor=22, option_count=4)
    service._advance_custom_event_cursor(first, _operation())
    service._advance_custom_event_cursor(first, _operation())

    other = _crystal_snapshot(floor=23, option_count=4)
    result = service._advance_custom_event_cursor(other, _operation())

    assert result["kwargs"]["option_index"] == 0


@pytest.mark.unit
def test_normal_event_screen_is_left_alone() -> None:
    """常规事件屏（没有 screen_progress）不碰规划器的下标 —— 那里的选项模型是有效的。"""
    service = _service()
    snapshot = {"screen": "EVENT", "floor": 22, "raw_state": {"event": {"options": [{"index": 0}, {"index": 1}]}}}

    result = service._advance_custom_event_cursor(snapshot, _operation(index=1))

    assert result["kwargs"]["option_index"] == 1


@pytest.mark.unit
def test_other_actions_are_left_alone() -> None:
    service = _service()
    snapshot = _crystal_snapshot()
    operation = {"action_type": "play_card", "kwargs": {"card_index": 2}}

    assert service._advance_custom_event_cursor(snapshot, operation) == operation
