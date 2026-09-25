"""core.py 纯逻辑单测（不依赖 SDK，可在 N.E.K.O 树内直接 pytest）。"""

from __future__ import annotations

import time

from plugin.plugins.mood_state.core import (
    clamp,
    clamp_delta,
    drift_toward,
    heuristic_eval,
    load_state_file,
    normalize_state,
    pick,
    refresh_state_entry,
    render_state_block,
    save_state_file,
    tier,
)

PROMPTS = {
    "mood": {"0": "心情谷底", "50": "心情平稳", "90": "心情爆棚"},
    "energy": {"0": "非常疲惫", "50": "精力平平", "90": "精力爆棚"},
}


def test_clamp():
    assert clamp(-20) == 0
    assert clamp(120) == 100
    assert clamp("73.6") == 74
    assert clamp(None) == 50


def test_clamp_delta():
    assert clamp_delta(150) == 100
    assert clamp_delta(-150) == -100
    assert clamp_delta("bad") == 0


def test_tier_boundaries():
    assert tier(0) == "0"
    assert tier(9) == "0"
    assert tier(10) == "10"
    assert tier(55) == "50"
    assert tier(99) == "90"
    assert tier(100) == "90"


def test_pick_and_render():
    assert pick(PROMPTS, "mood", 95) == "心情爆棚"
    assert pick(PROMPTS, "energy", 3) == "非常疲惫"
    assert pick(PROMPTS, "missing", 50) == ""

    block = render_state_block({"enabled": True, "mood": 55, "energy": 3}, PROMPTS)
    assert "心情 55/100" in block
    assert "精力 3/100" in block
    assert "心情平稳" in block
    assert "非常疲惫" in block

    assert (
        render_state_block({"enabled": False, "mood": 55, "energy": 3}, PROMPTS) == ""
    )
    assert render_state_block(None, PROMPTS) == ""


def test_normalize_state_defaults_and_override():
    s = normalize_state(None, {"enabled": True, "mood": 60, "energy": 40})
    assert s == {"enabled": True, "mood": 60, "energy": 40}

    s = normalize_state({"mood": 999, "energy": -5, "enabled": False})
    assert s["mood"] == 100
    assert s["energy"] == 0
    assert s["enabled"] is False


def test_state_file_roundtrip(tmp_path):
    path = tmp_path / "state.json"
    saved = save_state_file(path, {"enabled": True, "mood": 70, "energy": 30})
    assert saved["updated_at"] > 0
    loaded = load_state_file(path)
    assert loaded["mood"] == 70
    assert loaded["energy"] == 30
    assert loaded["enabled"] is True
    assert load_state_file(tmp_path / "missing.json") is None


def test_drift_toward_baseline():
    now = time.time()
    state = {"enabled": True, "mood": 20, "energy": 80, "updated_at": now - 3 * 3600}
    new, changed = drift_toward(state, 50, 50, drift_per_hour=2.0, now=now)
    assert changed
    assert new["mood"] == 26  # 3h * 2/h = 6 分向基线
    assert new["energy"] == 74

    # 预算不足 1 分时不动
    state2 = {"enabled": True, "mood": 20, "energy": 80, "updated_at": now - 60}
    _, changed2 = drift_toward(state2, 50, 50, drift_per_hour=2.0, now=now)
    assert not changed2

    # 到达基线后不再变化
    state3 = {"enabled": True, "mood": 50, "energy": 50, "updated_at": now - 10 * 3600}
    new3, changed3 = drift_toward(state3, 50, 50, drift_per_hour=2.0, now=now)
    assert not changed3
    assert new3["mood"] == 50


def test_refresh_state_entry():
    disk_chat = [
        {"type": "human", "data": {"content": "你好"}},
        {
            "type": "system",
            "data": {"content": "【当前状态】心情 50/100,精力 50/100(旧块)"},
        },
        {"type": "ai", "data": {"content": "嗨"}},
    ]
    out = refresh_state_entry(disk_chat, "【当前状态】心情 65/100,精力 45/100(新块)")
    assert out[0] == {"role": "human", "text": "你好"}
    assert out[1] == {"role": "ai", "text": "嗨"}
    assert out[-1] == {
        "role": "system",
        "text": "【当前状态】心情 65/100,精力 45/100(新块)",
    }
    assert len(out) == 3  # 旧块被剔除

    # API 形状兼容 + 空 block 只做剔除
    api_chat = [
        {"role": "human", "text": "x"},
        {"role": "system", "text": "【当前状态】旧"},
    ]
    out2 = refresh_state_entry(api_chat, "")
    assert out2 == [{"role": "human", "text": "x"}]

    # 无旧块且空 block：原样返回
    out3 = refresh_state_entry([{"role": "human", "text": "x"}], "")
    assert out3 == [{"role": "human", "text": "x"}]


def test_heuristic_eval():
    state = {"enabled": True, "mood": 50, "energy": 50}

    mood_d, energy_d, note = heuristic_eval(["今天考砸了好难过", "分手了"], state)
    assert mood_d < 0
    assert "负面情绪" in note

    mood_d, _, note = heuristic_eval(["哈哈哈太好笑了", "谢谢你"], state)
    assert mood_d > 0
    assert "正面情绪" in note

    _, energy_d, note = heuristic_eval(["哇塞居然真的赢了"], state)
    assert energy_d > 0
    assert "兴奋点" in note

    mood_d, energy_d, note = heuristic_eval([], state)
    assert mood_d == 0 and energy_d == 0
    assert "无明显情绪信号" in note

    # 幅度封顶：大量正面也不会超过 +8
    mood_d, _, _ = heuristic_eval(["哈哈"] * 50, state)
    assert mood_d <= 8
