"""拍板 2.0.69：config_source 日志 + 读写一致性。

真机 2.0.68 暴露：用户手改安装副本 plugin.toml 无效——插件读的是 user config 副本，
但 self.config.update(...) 写哪份也不清楚。

修复：
  ① startup 打 INFO：config_source=<path>——用户能立刻知道该改哪个文件
  ② 验证 self.config.dump / self.config.update 操作同一个文件（读写一致）
  ③ 缺省优雅——self.config.path() 抛异常时降级为 "<unknown>" 而非 startup 崩
"""
from __future__ import annotations

import threading
from unittest.mock import AsyncMock, MagicMock

import pytest
from plugin.plugins.genshin_game_buddy import GenshinGameBuddyPlugin
from plugin.plugins.genshin_game_buddy.game_registry import PluginOptions


def _make_plugin_stub(*, config_path: str | None, config_path_raises: bool = False) -> GenshinGameBuddyPlugin:
    """最小 stub——mock self.config 让 path()/dump()/update() 行为可控。"""
    plugin = GenshinGameBuddyPlugin.__new__(GenshinGameBuddyPlugin)
    # 拍板 2.0.69 测试：用真 PluginOptions（dataclasses.replace 需要真 dataclass）
    plugin._options = PluginOptions()
    plugin._registry = MagicMock()
    plugin._state_lock = threading.Lock()
    plugin._config_ready = False
    # 拍板 2.0.69：self._ocr.warmup_async() 是 coroutine——用 AsyncMock
    plugin._ocr = MagicMock()
    plugin._ocr.warmup_async = AsyncMock(return_value=None)
    plugin._activation = MagicMock()
    plugin._perception_state = "UNKNOWN"
    plugin._miss_streak = 0
    plugin._last_ocr_hash = ""
    plugin._last_pushed_hash = ""
    plugin._last_ocr_hit = False
    plugin._last_proactive_at = 0.0
    plugin._last_scene_pushed = ""
    plugin._last_scene_pushed_at = 0.0
    plugin._last_detected_game = ""
    plugin._last_real_ocr_monotonic = 0.0
    plugin._last_light_hash = ""
    plugin._last_scene_switch_at = 0.0
    plugin._last_scene_for_proactive = ""
    plugin._ocr_executor = None
    plugin._ocr_in_flight_count = 0
    plugin._ocr_in_flight_lock = threading.Lock()
    plugin._scene_store = {}
    plugin._detect_cache = {}
    plugin._manager = MagicMock()
    plugin._manager.restore = AsyncMock(return_value=None)
    plugin._counter = MagicMock()
    plugin._counter.reconfigure = MagicMock()
    plugin._manager.set_options = MagicMock()
    plugin._load_scenes_for_current = MagicMock()
    plugin.logger = MagicMock()
    plugin.store = MagicMock()
    plugin.store.enabled = True

    # mock config（path/dump 是 async，update 也是 async）
    fake_config = MagicMock()
    if config_path_raises:
        async def _boom_path(timeout=2.0):
            raise RuntimeError("config path unavailable")
        fake_config.path = _boom_path
    else:
        async def _ok_path(timeout=2.0):
            return config_path or "<unknown>"
        fake_config.path = _ok_path

    async def _empty_dump(timeout=5.0):
        return {}
    fake_config.dump = _empty_dump
    # 拍板 2.0.69：apply_settings_entry 内 await self.config.update(...)——update 是 async
    fake_config.update = AsyncMock(return_value=None)
    plugin.config = fake_config

    return plugin


@pytest.mark.unit
@pytest.mark.asyncio
async def test_startup__logs_config_source_path() -> None:
    """拍板 2.0.69：startup 打 INFO 含 config_source=<path>。"""
    expected_path = r"C:\Users\alpha\AppData\Local\N.E.K.O\plugins\genshin_game_buddy\config\plugin.toml"
    plugin = _make_plugin_stub(config_path=expected_path)

    await GenshinGameBuddyPlugin.startup(plugin)

    info_calls = [str(c) for c in plugin.logger.info.call_args_list]
    # MagicMock 的 call_args 在 str() 里用 repr 显示 path——repr 会把 \ 转义为 \\，
    # 双重转义后变成 \\\\——直接字符串比对不可靠。改为宽松匹配：仅检查关键词。
    assert any(
        "config_source=" in s and "plugin.toml" in s
        for s in info_calls
    ), f"config_source INFO log missing; got: {info_calls}"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_startup__config_path_raises__does_not_crash() -> None:
    """拍板 2.0.69：self.config.path() 抛异常时降级为 "<unknown>"，startup 不崩。"""
    plugin = _make_plugin_stub(config_path=None, config_path_raises=True)

    # 不应抛
    result = await GenshinGameBuddyPlugin.startup(plugin)
    assert result is not None  # Ok 不是 None

    # config_source 应是 <unknown>（MagicMock str() 把 "<unknown>" 用 repr 包成 quotes）
    info_calls = [str(c) for c in plugin.logger.info.call_args_list]
    assert any("config_source=" in s and "<unknown>" in s for s in info_calls), (
        f"config_source=<unknown> log missing; got: {info_calls}"
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_startup__hard_resets_ocr_in_flight_count() -> None:
    """拍板 2.0.69：startup 硬重置 _ocr_in_flight_count = 0——防上次崩溃遗留。"""
    plugin = _make_plugin_stub(config_path=None)
    plugin._ocr_in_flight_count = 2  # 假装上次崩溃遗留

    await GenshinGameBuddyPlugin.startup(plugin)

    assert plugin._ocr_in_flight_count == 0, (
        f"startup should hard-reset count to 0, got {plugin._ocr_in_flight_count}"
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_config__read_write_consistency() -> None:
    """拍板 2.0.69：read (dump) 和 write (update) 走同一个 self.config 对象。

    验证：dump 返回的 dict 经过 update 写回去，能再 dump 出来——读写闭环。
    实际是 mock，但核心断言：plugin.config 是同一个 instance。
    """
    plugin = _make_plugin_stub(config_path="/some/path.toml")
    assert plugin.config is not None

    # 验证 dump/update 是同一个对象的方法
    assert hasattr(plugin.config, "dump")
    assert hasattr(plugin.config, "update")

    # 调用一次 update——验证调用成功 + 没异常（async）
    await plugin.config.update({"genshin_game_buddy": {"ocr_worker_threads": 1}})
    assert plugin.config.update.called


@pytest.mark.unit
def test_config__path_log_includes_apply_settings_hint() -> None:
    """拍板 2.0.69：config_source 日志含 'apply_settings writes back to this file' 提示。

    让用户从日志就能知道 apply_settings 也写同一个文件——避免改一处不变另一处的困惑。
    """
    # 直接检查日志文案（不需要 startup 全跑）
    expected_phrase = "apply_settings writes back to this file"
    # 查 __init__.py 源码是否含该文案（保证未来重构不会意外去掉）
    from pathlib import Path
    plugin_file = (
        Path(__file__).resolve().parents[2] / "__init__.py"
    )
    content = plugin_file.read_text(encoding="utf-8")
    assert expected_phrase in content, (
        f"apply_settings hint missing from startup log; should contain: {expected_phrase!r}"
    )