"""civ6_advice 纯逻辑测试（无宿主依赖，CI 可跑）。

真实存档存在时跑集成测试（验证 format_situation + advice_lines 输出非空、含玩家文明），
存档不存在则跳过——不依赖特定机器状态。
"""

import importlib
import os
from pathlib import Path

import pytest

_advice = importlib.import_module("civ6_advice")


def _sample_sit():
    return {
        "game_turn": 1,
        "game_speed": "标准",
        "map_name": "大陆",
        "map_size": "小型（6人）",
        "humans": [{"id": "CIVILIZATION_KONGO", "name": "刚果",
                    "leader": "恩津加·姆班德", "alive": True}],
        "ais": [{"id": "CIVILIZATION_GERMANY", "name": "德国",
                 "leader": "腓特烈·巴巴罗萨", "alive": True}],
        "city_states": [{"id": "CIVILIZATION_KUMASI", "name": "库马西（文化）"}],
        "barbarian_count": 1,
        "mods": ["R.E.D. Modpack"],
    }


def test_advice_opening_contains_framework():
    lines = _advice.advice_lines(_sample_sit(), "开局")
    joined = "\n".join(lines)
    assert "刚果" in joined
    assert "第 1 回合" in joined
    assert "开局三件事" in joined


def test_advice_unknown_topic_falls_back():
    lines = _advice.advice_lines(_sample_sit(), "随便啥")
    joined = "\n".join(lines)
    assert "未收录" in joined
    assert "开局三件事" in joined  # 回退综合


def test_advice_war_mentions_rivals():
    lines = _advice.advice_lines(_sample_sit(), "战争")
    joined = "\n".join(lines)
    assert "德国" in joined
    assert "对手 1 个" in joined


def test_advice_empty_situation_safe():
    lines = _advice.advice_lines({}, "")
    assert isinstance(lines, list)
    assert len(lines) >= 1


_SAVES = Path.home() / "Documents" / "My Games" / "Sid Meier's Civilization VI" / "Saves"


def _find_real_save() -> str | None:
    if not _SAVES.is_dir():
        return None
    best = None
    for root, _dirs, files in os.walk(_SAVES):
        for f in files:
            if f.endswith(".Civ6Save"):
                p = os.path.join(root, f)
                if best is None or os.path.getmtime(p) > os.path.getmtime(best):
                    best = p
    return best


def test_real_save_format_and_advice():
    """集成测试：真实存档存在时，局面格式化与建议必须产出有效文本。"""
    save = _find_real_save()
    if not save:
        pytest.skip("本机暂无真实存档，跳过集成测试")
    _parser = importlib.import_module("civ6_parser")
    sit = _parser.parse_save(save, str(_SAVES))
    text = _parser.format_situation(sit)
    assert "当前局面" in text
    assert sit.get("game_turn") is not None
    lines = _advice.advice_lines(sit, "")
    assert len(lines) >= 2
    assert any("你的文明" in line or "阶段" in line for line in lines)
