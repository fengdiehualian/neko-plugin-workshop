"""待机（standby）退出后必须**回到原来的意图**，不能把自动游玩永久停摆。

**实盘证据（2026-10-07 06:2x，`sts2_set_standby{standby:false}` 实测）**：

    [standby=on]  -> 已切换到 standby 模式。   实测: standby=True  autoplay=standby step=7
    [standby=off] -> 已切换到 active 模式。    实测: standby=False autoplay=idle    step=7  ← 卡死
    20 秒后、再开再关陪玩模式后:              实测: autoplay=idle    step=7  ← 再没动过

根因在 `service._apply_control_mode`：退出 standby 时把意图**写死成 `idle`**，而
`loop_runner._autoplay_loop` 的门是 `autoplay_state != "running" -> 空转 continue`。
于是"取消待机"= 永动机熄火，和玩家骂过的"卡住了"是同一族问题。

待机是**临时停手**（对应 `pause` 的语义），不是"停止"。所以这里记住进待机前的意图，
退出时还回去；玩家在待机期间明确喊停（`stop_autoplay`）则要活过这次恢复。
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


@pytest.mark.unit
def test_leaving_standby_restores_the_running_intent() -> None:
    """实盘回归：待机前在自动玩，取消待机后必须还是 `running`（循环只认这个值）。"""
    service = _service()
    service._state.autoplay_state = "running"

    service.set_standby(True)
    assert service._state.autoplay_state == "standby"

    service.set_standby(False)
    assert service._state.autoplay_state == "running", "取消待机后自动游玩必须能继续跑"


@pytest.mark.unit
def test_leaving_standby_keeps_a_paused_intent() -> None:
    """待机前是暂停，取消待机后仍然是暂停 —— 别把玩家的暂停吃掉。"""
    service = _service()
    service._state.autoplay_state = "paused"

    service.set_standby(True)
    service.set_standby(False)

    assert service._state.autoplay_state == "paused"


@pytest.mark.unit
def test_explicit_stop_during_standby_survives_leaving_it() -> None:
    """待机期间玩家喊停，那这条"停"要活过取消待机，不能被"恢复"覆盖。"""
    service = _service()
    service._state.autoplay_state = "running"

    service.set_standby(True)
    service.stop_autoplay()
    service.set_standby(False)

    assert service._state.autoplay_state == "idle"


@pytest.mark.unit
def test_leaving_standby_without_memory_falls_back_to_config_intent() -> None:
    """插件在待机状态下重启时没有记忆，就按开机意图兜底。"""
    service = _service()
    service._cfg["autoplay_on_start"] = True
    service._state.control_mode = "standby"
    service._state.autoplay_state = "standby"

    service.set_standby(False)
    assert service._state.autoplay_state == "running"

    service_off = _service()
    service_off._cfg["autoplay_on_start"] = False
    service_off._state.control_mode = "standby"
    service_off._state.autoplay_state = "standby"

    service_off.set_standby(False)
    assert service_off._state.autoplay_state == "idle"


@pytest.mark.unit
def test_standby_memory_is_cleared_after_restore() -> None:
    """恢复一次就把记忆清掉，免得下一次待机退出时拿旧意图乱恢复。"""
    service = _service()
    service._state.autoplay_state = "running"

    service.set_standby(True)
    assert service._state.autoplay_state_before_standby == "running"

    service.set_standby(False)
    assert service._state.autoplay_state_before_standby == ""


@pytest.mark.unit
def test_entering_standby_twice_keeps_the_first_intent() -> None:
    """重复进待机不能覆盖记忆（第二次进待机时 autoplay_state 已经是 standby 了）。"""
    service = _service()
    service._state.autoplay_state = "running"

    service.set_standby(True)
    service.set_standby(True)
    service.set_standby(False)

    assert service._state.autoplay_state == "running"
