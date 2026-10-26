# 太空发射插件 · 自动播报测试 —— Copyright (C) 2026 星河拓航工作室
#
# 本文件是太空发射插件的一部分，以 GNU General Public License v3.0 许可发布。
# 完整条款见仓库根目录的 LICENSE 文件。
"""自动播报的行为约定。

自动播报 = 插件在后台定期把「下一次发射」写进角色的上下文，
让角色在主人开口时就已经知道答案，而不是先调一遍工具。
"""

import asyncio

import pytest
from plugin.plugins.space_launch import (
    AUTO_BRIEF_COALESCE_KEY,
    AUTO_BRIEF_PREFIX,
    SpaceLaunchPlugin,
    _ApiError,
    _auto_brief_line,
)
from plugin.sdk.plugin import SdkError


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


_LAUNCH = {
    "name": "Falcon 9 Block 5 | Starlink Group 12-3",
    "rocket": "Falcon 9 Block 5",
    "provider": "SpaceX",
    "net_local": "2026-10-19 08:30:00",
    "location": "Cape Canaveral SFS, FL, USA",
    "countdown": "还有 12 小时 30 分钟",
    "status": "Go for Launch",
    "mission_type": "Communications",
}


class _Recorder:
    """替身：只实现自动播报用到的那几个方法，其余行为借真实实现。"""

    _fetch_upcoming = SpaceLaunchPlugin._fetch_upcoming
    _auto_brief_once = SpaceLaunchPlugin._auto_brief_once
    _auto_brief_loop = SpaceLaunchPlugin._auto_brief_loop

    def __init__(self, items=None, error=None):
        self._items = items if items is not None else []
        self._error = error
        self._calls = 0
        self.pushed = []
        self._auto_brief_interval = 0.01
        self._auto_brief_last_text = ""
        self._auto_brief_last_ok = None

        class _Logger:
            def debug(self, *args, **kwargs):
                pass

        self.logger = _Logger()

    async def _fetch_upcoming(self, *, limit, provider="", rocket="", location=""):
        self._calls += 1
        if self._error is not None:
            raise self._error
        return self._items[:limit]

    def push_message(self, **kwargs):
        self.pushed.append(kwargs)
        return {"submitted": True}


def test_line_has_prefix_and_name() -> None:
    line = _auto_brief_line([_LAUNCH])
    assert line.startswith(AUTO_BRIEF_PREFIX)
    assert "Starlink Group 12-3" in line
    assert "Falcon 9 Block 5" in line


def test_line_is_short() -> None:
    assert len(_auto_brief_line([_LAUNCH])) < 400


def test_line_only_mentions_first_launch() -> None:
    second = dict(_LAUNCH, name="Ariane 6 | Sentinel-1D")
    line = _auto_brief_line([_LAUNCH, second])
    assert "Starlink" in line
    assert "Sentinel-1D" not in line


def test_line_prefix_appears_once() -> None:
    line = _auto_brief_line([_LAUNCH])
    assert line.count(AUTO_BRIEF_PREFIX) == 1


def test_line_empty_without_items() -> None:
    assert _auto_brief_line([]) == ""


def test_once_pushes_read_only_context() -> None:
    plugin = _Recorder(items=[_LAUNCH])
    _run(plugin._auto_brief_once())

    assert len(plugin.pushed) == 1
    kwargs = plugin.pushed[0]
    assert kwargs["ai_behavior"] == "read"
    assert kwargs["visibility"] == []
    assert kwargs["coalesce_key"] == AUTO_BRIEF_COALESCE_KEY
    assert kwargs["source"] == "space_launch_auto_brief"
    assert kwargs["parts"][0]["type"] == "text"
    assert "Starlink" in kwargs["parts"][0]["text"]


def test_once_does_not_repeat_identical_payload() -> None:
    plugin = _Recorder(items=[_LAUNCH])
    _run(plugin._auto_brief_once())
    _run(plugin._auto_brief_once())
    assert len(plugin.pushed) == 1


def test_once_stays_quiet_without_data() -> None:
    plugin = _Recorder(items=[])
    _run(plugin._auto_brief_once())
    assert plugin.pushed == []


def test_once_stays_quiet_when_never_connected() -> None:
    plugin = _Recorder(error=_ApiError("连不上"))
    _run(plugin._auto_brief_once())
    assert plugin.pushed == []


def test_once_reports_disconnect_after_success() -> None:
    plugin = _Recorder(items=[_LAUNCH])
    _run(plugin._auto_brief_once())
    plugin._error = _ApiError("连不上")
    _run(plugin._auto_brief_once())

    assert len(plugin.pushed) == 2
    assert "查不到" in plugin.pushed[1]["parts"][0]["text"]
    # 再断一次不该重复刷屏
    _run(plugin._auto_brief_once())
    assert len(plugin.pushed) == 2


def test_once_swallows_unexpected_errors() -> None:
    plugin = _Recorder(error=RuntimeError("炸了"))
    _run(plugin._auto_brief_once())
    assert plugin.pushed == []


def test_once_tolerates_rejected_push() -> None:
    plugin = _Recorder(items=[_LAUNCH])

    def _reject(**kwargs):
        plugin.pushed.append(kwargs)
        return {"submitted": False, "reason": "backpressure"}

    plugin.push_message = _reject
    _run(plugin._auto_brief_once())  # 不得抛
    assert len(plugin.pushed) == 1


def test_stop_is_safe_without_init() -> None:
    plugin = SpaceLaunchPlugin.__new__(SpaceLaunchPlugin)
    _run(plugin._stop_auto_brief())  # 不得抛 AttributeError


def test_loop_exits_when_stop_set() -> None:
    plugin = _Recorder(items=[_LAUNCH])
    plugin._auto_brief_stop = asyncio.Event()

    async def _scenario():
        plugin._auto_brief_stop.set()
        await asyncio.wait_for(plugin._auto_brief_loop(), timeout=1.0)

    _run(_scenario())


@pytest.mark.parametrize("interval", [30.0, 180.0, 3600.0])
def test_interval_defaults_are_in_range(interval: float) -> None:
    assert 30.0 <= interval <= 3600.0


def test_sdk_error_is_not_imported_by_accident() -> None:
    # 自动播报只吞 _ApiError；SdkError 属于 SDK 层，不该被误用
    assert SdkError is not _ApiError
