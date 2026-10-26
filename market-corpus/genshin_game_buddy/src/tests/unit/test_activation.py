"""ActivationState 单元测试：TTL 过期 / 上限淘汰 / 优先级 / 场景激活。"""
from __future__ import annotations

import time

from plugin.plugins.genshin_game_buddy.activation import (
    MAX_ACTIVE,
    SCENE_MISS_THRESHOLD,
    ActivationState,
)


def test_activate_terms_basic() -> None:
    state = ActivationState()
    state.activate_terms(["a", "b"], source="query", ttl_seconds=60.0)
    assert set(state.get_active_keys()) == {"a", "b"}


def test_activate_terms_ttl_expiry() -> None:
    state = ActivationState()
    state.activate_terms(["a"], source="query", ttl_seconds=0.01)
    time.sleep(0.02)
    assert state.get_active_keys() == []


def test_activate_terms_empty_list() -> None:
    state = ActivationState()
    state.activate_terms([], source="query", ttl_seconds=60.0)
    assert state.get_active_keys() == []


def test_activate_scene_and_miss_clears() -> None:
    state = ActivationState()
    state.activate_scene("scene1", ["x", "y"])
    assert set(state.get_active_keys()) == {"x", "y"}
    for _ in range(SCENE_MISS_THRESHOLD - 1):
        state.tick_scene("scene1", hit=False)
    assert set(state.get_active_keys()) == {"x", "y"}
    state.tick_scene("scene1", hit=False)
    assert state.get_active_keys() == []


def test_scene_hit_resets_miss_count() -> None:
    state = ActivationState()
    state.activate_scene("scene1", ["x"])
    state.tick_scene("scene1", hit=False)
    state.tick_scene("scene1", hit=True)
    state.tick_scene("scene1", hit=False)
    state.tick_scene("scene1", hit=False)
    assert set(state.get_active_keys()) == {"x"}


# =====================================================================
# 拍板 2.0.64：clear_by_sources 转场清空
# =====================================================================


def test_clear_by_sources__only_specified_sources_removed() -> None:
    """拍板 2.0.64：clear_by_sources 只清指定 source；query/core_layer 保留。"""
    state = ActivationState()
    state.activate_terms(["q1", "q2"], source="query", ttl_seconds=60.0)
    state.activate_terms(["c1", "c2"], source="core_layer", ttl_seconds=float("inf"))
    state.activate_terms(["s1", "s2"], source="screen", ttl_seconds=180.0)
    state.activate_terms(["l1"], source="screen_slang", ttl_seconds=180.0)
    state.activate_terms(["sc1"], source="scene:scene_a", ttl_seconds=120.0)

    cleared = state.clear_by_sources(("screen", "screen_slang", "scene"))

    # screen / screen_slang / scene:* 都被清掉
    assert set(cleared) == {"s1", "s2", "l1", "sc1"}
    # query + core_layer 保留
    assert set(state.get_active_keys()) == {"q1", "q2", "c1", "c2"}


def test_clear_by_sources__empty_returns_empty() -> None:
    """拍板 2.0.64：没东西可清时返回空列表（不影响转场日志噪音）。"""
    state = ActivationState()
    state.activate_terms(["x"], source="query", ttl_seconds=60.0)
    cleared = state.clear_by_sources(("screen",))
    assert cleared == []
    assert set(state.get_active_keys()) == {"x"}


def test_clear_by_sources__unknown_source_does_nothing() -> None:
    """拍板 2.0.64：不存在的 source 不报错。"""
    state = ActivationState()
    state.activate_terms(["x"], source="query", ttl_seconds=60.0)
    cleared = state.clear_by_sources(("nonexistent",))
    assert cleared == []
    assert set(state.get_active_keys()) == {"x"}


def test_max_active_eviction() -> None:
    state = ActivationState()
    keys = [f"k{i}" for i in range(MAX_ACTIVE + 5)]
    state.activate_terms(keys, source="scene:test", ttl_seconds=60.0)
    assert len(state.get_active_keys()) <= MAX_ACTIVE


def test_core_layer_never_evicted() -> None:
    state = ActivationState()
    state.activate_terms(["core_layer1"], source="core_layer", ttl_seconds=float("inf"))
    keys = [f"k{i}" for i in range(MAX_ACTIVE + 5)]
    state.activate_terms(keys, source="scene:test", ttl_seconds=60.0)
    assert "core_layer1" in state.get_active_keys()


def test_query_priority_over_scene() -> None:
    state = ActivationState()
    state.activate_terms(["q1"], source="query", ttl_seconds=60.0)
    keys = [f"s{i}" for i in range(MAX_ACTIVE + 5)]
    state.activate_terms(keys, source="scene:test", ttl_seconds=60.0)
    assert "q1" in state.get_active_keys()


def test_clear_scene_only_removes_scene_terms() -> None:
    state = ActivationState()
    state.activate_scene("scene1", ["x", "y"])
    state.activate_terms(["z"], source="query", ttl_seconds=60.0)
    state.clear_scene("scene1")
    assert set(state.get_active_keys()) == {"z"}


def test_clear_all() -> None:
    state = ActivationState()
    state.activate_terms(["a", "b"], source="query", ttl_seconds=60.0)
    state.clear_all()
    assert state.get_active_keys() == []
