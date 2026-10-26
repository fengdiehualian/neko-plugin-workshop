"""猫娘人设是**可变**的: 宿主换了角色/改了配置, 插件要跟上(不能冻在初始化)。"""

from __future__ import annotations

import json

from plugin.plugins.neko_arcade.core.brain import GameBrain
from plugin.plugins.neko_arcade.core.companion import Companion
from plugin.plugins.neko_arcade.core.persona import Persona, apply_persona, load_host_persona


def _write_host(tmp_path, *, current: str, cats: dict):
    host = tmp_path / "config" / "characters"
    host.mkdir(parents=True, exist_ok=True)
    path = host / "zh-CN.json"
    path.write_text(json.dumps({"主人": {"昵称": "碳基生物"}, "当前猫娘": current,
                                "猫娘": cats}, ensure_ascii=False), encoding="utf-8")
    return path


YU = {"昵称": "YUI", "自称": "本喵", "核心特质": ["理智可靠"],
      "一句话台词": "当然只有本喵啦~", "行为特点": {"粘人": True}}
RIN = {"昵称": "小铃", "自称": "人家", "核心特质": ["冒失", "爱撒娇"],
       "一句话台词": "人家才没有迷路呢！", "行为特点": {"爱抱抱": True}}


def test_persona_reloads_when_host_switches_character(tmp_path, monkeypatch) -> None:
    path = _write_host(tmp_path, current="YUI", cats={"YUI": YU, "小铃": RIN})
    monkeypatch.setenv("NEKO_CHARACTERS_FILE", str(path))
    first = load_host_persona({}, force=True)
    assert first["name"] == "YUI" and first["self_call"] == "本喵"
    # 宿主换成另一只猫娘(文件变了) → 必须重读, 不能吃缓存
    _write_host(tmp_path, current="小铃", cats={"YUI": YU, "小铃": RIN})
    second = load_host_persona({}, force=True)
    assert second["name"] == "小铃" and second["self_call"] == "人家"
    assert "冒失" in second["traits"]


def test_refresh_updates_persona_and_clears_line_cache(tmp_path, monkeypatch) -> None:
    path = _write_host(tmp_path, current="YUI", cats={"YUI": YU, "小铃": RIN})
    monkeypatch.setenv("NEKO_CHARACTERS_FILE", str(path))
    brain = GameBrain.__new__(GameBrain)          # 只测刷新逻辑, 不跑 __init__
    brain.cfg = {}
    brain.persona = Persona(load_host_persona({}, force=True))
    brain.companion = Companion(persona=brain.persona, llm=None)
    brain.companion._cache["x"] = "旧人格生成的台词"
    brain._persona_raw = None
    assert brain.persona.name == "YUI"
    assert brain._maybe_refresh_persona() is False        # 没变 → 不折腾
    assert brain.companion._cache                       # 缓存还在

    _write_host(tmp_path, current="小铃", cats={"YUI": YU, "小铃": RIN})
    assert brain._maybe_refresh_persona() is True         # 换了人设
    assert brain.persona.name == "小铃" and brain.persona.self_call == "人家"
    assert not brain.companion._cache                    # 旧台词清掉
    block = brain.companion.persona_block()
    assert "小铃" in block and "冒失" in block and "人家才没有迷路呢" in block


def test_apply_persona_keeps_existing_when_field_missing() -> None:
    p = Persona({"name": "YUI", "traits": ["理智"], "user_call": "主人"})
    assert apply_persona(p, {"name": "小铃"}) is True
    assert p.name == "小铃" and p.traits == ["理智"]      # 缺的字段不动
    assert apply_persona(p, {"name": "小铃"}) is False    # 没变化
