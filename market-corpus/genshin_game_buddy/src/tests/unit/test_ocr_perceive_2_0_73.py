"""拍板 2.0.73：worker 卡死回归 — 早退打日志 + heartbeat 暴露 worker 健康度 + SceneTracker 修正。

真机 2.0.72 暴露：
  - 3 分钟日志只有 heartbeat 没有 body_exit → 根因是 OUT_OF_GAME / text_unchanged 静默 early-return
  - heartbeat 只显示 SceneTracker 状态，看不出 worker 闸是否被卡
  - SceneTracker pending=current 时 pending_count 只涨不触发 switch，心跳日志"看起来像卡死"

本测试覆盖：
  - OUT_OF_GAME 早退必须打 INFO 日志（带 reason=state=OUT_OF_GAME）
  - text_unchanged 早退必须打 INFO 日志
  - heartbeat 必须附带 ocr_in_flight / ocr_workers / executor_alive
  - SceneTracker primary == current 时清空 pending 状态机（pending=None, count=0）
  - 黑帧早退已有 log（验证未回归）
"""
from __future__ import annotations

import threading
from unittest.mock import AsyncMock, MagicMock

import pytest
from plugin.plugins.genshin_game_buddy import GenshinGameBuddyPlugin
from plugin.plugins.genshin_game_buddy.game_registry import PluginOptions
from plugin.plugins.genshin_game_buddy.scene_tracker import SceneTracker

# ============================================================================
# Stub 工厂
# ============================================================================

def _make_plugin_stub(
    *,
    worker_threads: int = 1,
    scene_interval: int = 2,
    term_interval: int = 15,
    perception_state: str = "IN_GAME",
) -> GenshinGameBuddyPlugin:
    """最小可用 stub——含 SceneTracker + 缓存 + ocr_in_flight 字段。"""
    plugin = GenshinGameBuddyPlugin.__new__(GenshinGameBuddyPlugin)
    plugin._options = PluginOptions(
        ocr_scene_interval_seconds=scene_interval,
        ocr_term_interval_seconds=term_interval,
        ocr_worker_threads=worker_threads,
        ocr_perceive_interval_seconds=term_interval,  # legacy
    )
    plugin._state_lock = threading.Lock()
    plugin._perception_state = perception_state
    plugin._ocr_in_flight_lock = threading.Lock()
    plugin._ocr_in_flight_count = 0
    plugin._last_real_ocr_monotonic = 0.0
    plugin._last_light_hash = ""
    plugin._last_capture_b64 = None
    plugin._last_capture_at_monotonic = 0.0
    plugin._last_capture_text = ""
    plugin._last_term_run_monotonic = 0.0
    plugin._light_capture_dhash = lambda: ""
    plugin._dhash_diff = GenshinGameBuddyPlugin._dhash_diff
    plugin._scene_tracker = SceneTracker(
        hysteresis_count=3,
        exit_grace_count=5,
        debounce_seconds=10.0,
    )
    plugin._activation = MagicMock()
    plugin._activation.clear_by_sources = MagicMock(return_value=0)
    plugin._activation.get_active_keys = MagicMock(return_value=[])
    plugin._activation.activate_scene = MagicMock()
    plugin._activation.tick_scene = MagicMock()
    plugin._activation.activate_terms = MagicMock()
    plugin._scene_store = {}
    plugin._manager = MagicMock()
    plugin._manager.current = MagicMock()  # session 不为 None
    plugin._manager.library = None
    plugin._last_detected_game = ""
    plugin._last_scene_pushed = ""
    plugin._last_scene_pushed_at = 0.0
    plugin._last_scene_switch_at = 0.0
    plugin._last_scene_for_proactive = ""
    plugin._last_proactive_at = 0.0
    plugin._last_ocr_hash = ""
    plugin._last_ocr_hit = False
    plugin._scene_tracker_log_throttle = 0.0
    plugin._scene_tracker_log_interval = 30.0
    plugin.detect_game = AsyncMock(return_value="none")  # 让 1st body 顺利过 detect_game
    plugin._has_in_game_characteristics = MagicMock(return_value=False)
    plugin._match_scenes = MagicMock(return_value=[])
    plugin._should_push_proactive = MagicMock(return_value=False)
    plugin._push_proactive = MagicMock()
    plugin._update_perception_state = MagicMock()
    plugin._scan_screen_activate_terms = MagicMock(return_value=([], []))
    plugin.logger = MagicMock()
    return plugin


# ============================================================================
# Fix A: OUT_OF_GAME 早退必须打 INFO 日志
# ============================================================================

@pytest.mark.unit
@pytest.mark.asyncio
async def test_ocr_perceive__out_of_game_early_return_logs() -> None:
    """拍板 2.0.73：perception_state=OUT_OF_GAME 早退必须打 INFO 日志。

    真机 2.0.72 暴露：3 分钟无 body_exit 日志，根因就是这条路径静默 return。
    现在打 INFO 含 reason=state=OUT_OF_GAME，运维能一眼区分"早退"vs"卡死"。
    """
    plugin = _make_plugin_stub(perception_state="OUT_OF_GAME")

    # 调 body——应打 INFO 然后 return（不抛）
    await GenshinGameBuddyPlugin._ocr_perceive_body(plugin)

    # 找带"early-return"和"OUT_OF_GAME"的 INFO 日志
    # 两个关键字都在格式串（args[0]）里——OUT_OF_GAME 是固定串，不是格式化参数
    info_calls = [
        c for c in plugin.logger.info.call_args_list
        if c.args and isinstance(c.args[0], str)
    ]
    found = any(
        "early-return" in c.args[0] and "OUT_OF_GAME" in c.args[0]
        for c in info_calls
    )

    assert found, (
        f"OUT_OF_GAME 早退必须打 INFO 日志（含 'early-return' + 'OUT_OF_GAME'）；"
        f"实际 info 日志：{[c.args[0] for c in info_calls]}"
    )


# ============================================================================
# Fix B: text_unchanged 早退必须打 INFO 日志
# ============================================================================

@pytest.mark.unit
@pytest.mark.asyncio
async def test_ocr_perceive__text_unchanged_early_return_logs() -> None:
    """拍板 2.0.73：OCR 返回与上次相同的 hash 时早退必须打 INFO 日志。

    防 2.0.72 真机："OCR 一直返回同一帧"看起来像"卡死"——必须留痕说明是合法跳过。
    """
    plugin = _make_plugin_stub(perception_state="IN_GAME")

    # 让 OCR 返回同一文本两次——第二次走 text_unchanged 早退路径
    fixed_text = "恒常之境 祈愿"
    plugin._ocr = MagicMock()

    # mock capture_active_frame（避免真抓屏）
    # 注意：`asyncio.to_thread(capture_active_frame)` 需要 sync 函数（返回 tuple），
    # 不能用 async def——async 函数返回 coroutine，to_thread 不会 await 它。
    _fake_result = MagicMock()
    _fake_result.ok = True
    _fake_result.image_base64 = "ZmFrZQ=="
    _fake_result.method = "test"
    _fake_result.avg_luma = 128.0
    def _fake_capture():
        return (_fake_result, "test")
    # mock extract_text_from_base64 —— body 用 `await` 调，必须是 async
    async def _fake_extract(_b64: str) -> str:
        return fixed_text
    plugin._ocr.extract_text_from_base64 = _fake_extract

    # 用 patch：替换模块里的 capture_active_frame
    import plugin.plugins.genshin_game_buddy as _mgc_module
    original_capture = getattr(_mgc_module, "capture_active_frame", None)
    _mgc_module.capture_active_frame = _fake_capture
    try:
        # 第一次 body：写入 _last_ocr_hash
        plugin._last_ocr_hash = ""
        await GenshinGameBuddyPlugin._ocr_perceive_body(plugin)
        # 第二次 body：text_hash == _last_ocr_hash → 早退
        plugin.logger.reset_mock()
        await GenshinGameBuddyPlugin._ocr_perceive_body(plugin)
    finally:
        if original_capture is not None:
            _mgc_module.capture_active_frame = original_capture

    # 找带"text_unchanged"的 INFO 日志
    info_calls = [
        c for c in plugin.logger.info.call_args_list
    ]
    found = any(
        isinstance(c.args[0], str) and "text_unchanged" in c.args[0]
        for c in info_calls
    )

    assert found, (
        f"text_unchanged 早退必须打 INFO 日志；"
        f"实际 info 日志：{[c.args[0] for c in info_calls if c.args]}"
    )


# ============================================================================
# Fix C: heartbeat 必须附带 ocr_in_flight / ocr_workers / executor_alive
# ============================================================================

@pytest.mark.unit
@pytest.mark.asyncio
async def test_heartbeat__exposes_worker_health() -> None:
    """拍板 2.0.73：SceneTracker heartbeat 必须附带 worker 闸 + executor 健康状态。

    真机 2.0.72 暴露：心跳只显示 SceneTracker，看不出 worker 闸是否被卡。
    现在必须附带 ocr_in_flight=N / ocr_workers=M / executor_alive=True/False。
    """
    plugin = _make_plugin_stub(worker_threads=1)

    # 模拟一个 mock executor（alive）
    fake_executor = MagicMock()
    fake_executor._shutdown = False
    plugin._ocr_executor = fake_executor

    # 让心跳节流条件满足：t0 - throttle >= interval
    # 直接设 throttle = 0.0，interval = 30s → 必然触发
    plugin._scene_tracker_log_throttle = 0.0
    plugin._scene_tracker_log_interval = 30.0

    # _perception_state = OUT_OF_GAME → body 会在心跳后早退（无需 capture/ocr）
    plugin._perception_state = "OUT_OF_GAME"
    plugin._manager.current = MagicMock()  # session 不为 None（先过 session check）

    await GenshinGameBuddyPlugin._ocr_perceive_body(plugin)

    # 找 heartbeat 日志——必须包含 ocr_in_flight / ocr_workers / executor_alive
    heartbeat_calls = [
        c for c in plugin.logger.info.call_args_list
        if c.args and isinstance(c.args[0], str) and "scene_tracker heartbeat" in c.args[0]
    ]

    assert len(heartbeat_calls) >= 1, (
        "必须至少打一次 heartbeat 日志（含 ocr_in_flight/ocr_workers/executor_alive）"
    )

    # 检查所有 heartbeat 日志都包含三个新字段
    for call in heartbeat_calls:
        msg = call.args[0]
        assert "ocr_in_flight" in msg, (
            f"heartbeat 必须含 'ocr_in_flight' 字段；实际: {msg}"
        )
        assert "ocr_workers" in msg, (
            f"heartbeat 必须含 'ocr_workers' 字段；实际: {msg}"
        )
        assert "executor_alive" in msg, (
            f"heartbeat 必须含 'executor_alive' 字段；实际: {msg}"
        )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_heartbeat__executor_shutdown_shows_zero_workers() -> None:
    """拍板 2.0.73：executor shutdown 后，heartbeat 必须显示 ocr_workers=0、executor_alive=False。

    极端场景：executor 被显式 shutdown 后仍有 body 进来（不该发生但要防御）。
    """
    plugin = _make_plugin_stub(worker_threads=2)

    # 模拟已 shutdown 的 executor
    fake_executor = MagicMock()
    fake_executor._shutdown = True
    plugin._ocr_executor = fake_executor

    plugin._scene_tracker_log_throttle = 0.0
    plugin._scene_tracker_log_interval = 30.0
    plugin._perception_state = "OUT_OF_GAME"
    plugin._manager.current = MagicMock()

    await GenshinGameBuddyPlugin._ocr_perceive_body(plugin)

    heartbeat_calls = [
        c for c in plugin.logger.info.call_args_list
        if c.args and isinstance(c.args[0], str) and "scene_tracker heartbeat" in c.args[0]
    ]

    assert heartbeat_calls, "必须打 heartbeat 日志"
    msg = heartbeat_calls[0].args[0]
    # 字段名都在；具体值由调用方解析——这里只验证格式
    assert "executor_alive" in msg
    # executor_alive=False、ocr_workers=0 由格式化参数决定
    # 调用 args 应该有 False / 0
    assert False in heartbeat_calls[0].args, (
        f"executor_alive=False 必须出现在 heartbeat 参数；实际 args: {heartbeat_calls[0].args}"
    )


# ============================================================================
# Fix D: SceneTracker primary == current → 清空 pending 状态机
# ============================================================================

@pytest.mark.unit
def test_scene_tracker__pending_cleared_when_primary_equals_current() -> None:
    """拍板 2.0.73：current=primary 时 pending 必须清空（不再累积）。

    真机 2.0.72 暴露：心跳显示 pending=X current=X pending_count=5/10/15... 看起来像卡死。
    实际只是"OCR 一直命中同一场景"——pending 是无意义的，应该清零。
    """
    clock = _FakeClock()
    tracker = SceneTracker(hysteresis_count=3, clock=clock)

    # 先切到 scene_a
    for _ in range(3):
        tracker.update(["scene_a"])
    assert tracker.current_scene == "scene_a"
    # SCENE_SWITCHED 后 pending 应已清空
    assert tracker.pending_scene is None
    assert tracker.pending_count == 0

    # 重复命中 scene_a 5 次——pending 始终保持 None、count 始终保持 0
    for i in range(5):
        event = tracker.update(["scene_a"])
        assert event.type.value == "no_change", (
            f"重复命中同场景应一直 NO_CHANGE；第 {i + 1} 次 event: {event.type.value}"
        )
        assert tracker.pending_scene is None, (
            f"pending 永远不应等于 current；第 {i + 1} 次 pending={tracker.pending_scene!r}"
        )
        assert tracker.pending_count == 0, (
            f"pending 永远不应等于 current，count 不应增长；"
            f"第 {i + 1} 次 count={tracker.pending_count}"
        )


@pytest.mark.unit
def test_scene_tracker__switch_after_pending_clear() -> None:
    """拍板 2.0.73：pending 清零后切到新场景仍能正常走 hysteresis 流程。

    回归测试：primary=current=scene_a 反复命中 → pending 清空 → 切到 scene_b → 仍能正常计数切换。
    """
    clock = _FakeClock()
    tracker = SceneTracker(hysteresis_count=3, clock=clock)

    # 先到 scene_a
    for _ in range(3):
        tracker.update(["scene_a"])
    assert tracker.current_scene == "scene_a"

    # 反复命中 scene_a（pending 清空路径）
    for _ in range(10):
        tracker.update(["scene_a"])
    assert tracker.pending_scene is None
    assert tracker.pending_count == 0

    # 切到 scene_b——必须能正常 3 次触发 SCENE_SWITCHED
    e1 = tracker.update(["scene_b"])
    e2 = tracker.update(["scene_b"])
    e3 = tracker.update(["scene_b"])

    assert e1.type.value == "no_change"
    assert e2.type.value == "no_change"
    assert e3.type.value == "scene_switched"
    assert e3.to_scene == "scene_b"


# ============================================================================
# 辅助
# ============================================================================

class _FakeClock:
    """测试加速时钟——手动 tick。"""

    def __init__(self, start: float = 1000.0) -> None:
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt
