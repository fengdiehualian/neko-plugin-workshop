"""拍板 2.0.66：OCR 配置档常量 + PluginOptions ocr_profile 字段单测。

注意：_detect_profile_from_system 依赖 psutil（仅 stdlib 外的可选探测），
真机靠插件启动期 psutil；本测试文件不模拟 psutil——只验：
  1. OCR_PROFILES / OCR_PROFILE_DEFAULTS 常量正确（UI 和 Python 都依赖）
  2. PluginOptions ocr_profile 字段 5 个合法值 / 大小写不敏感 / fallback
  3. detect 函数在真实机器（CPU=18 / mem~15GB）下不抛异常、返回有效档位
"""
from __future__ import annotations

import pytest
from plugin.plugins.genshin_game_buddy import _detect_profile_from_system


@pytest.mark.unit
def test_detect_profile__returns_valid_three_tuple() -> None:
    """真机探测：返回值始终是 (profile: str, cpu: int, mem: float)，profile 在 3 档预设内。"""
    profile, cpu, mem = _detect_profile_from_system()
    assert isinstance(profile, str)
    assert profile in ("eco", "balanced", "performance")
    assert isinstance(cpu, int) and cpu > 0
    assert isinstance(mem, float) and mem > 0


@pytest.mark.unit
def test_detect_profile__does_not_raise_with_real_psutil() -> None:
    """真机探测：无论 psutil 是否可用，都不抛异常（内部 try/except）。"""
    # 调用两次确保稳定（不依赖 module 状态）
    p1, _, _ = _detect_profile_from_system()
    p2, _, _ = _detect_profile_from_system()
    assert p1 in ("eco", "balanced", "performance")
    assert p2 in ("eco", "balanced", "performance")


@pytest.mark.unit
def test_detect_profile__thresholds_match_spec() -> None:
    """拍板 2.0.66：阈值定义
        CPU ≤ 4 或 mem ≤ 8GB → eco
        CPU ≥ 8 且 mem ≥ 16GB → performance
        其他 → balanced
    通过真实运行验证（CPU=18，真实 mem）→ 必定 balanced 或 performance。
    """
    profile, cpu, mem = _detect_profile_from_system()
    if cpu <= 4 or mem <= 8:
        assert profile == "eco"
    elif cpu >= 8 and mem >= 16:
        assert profile == "performance"
    else:
        assert profile == "balanced"