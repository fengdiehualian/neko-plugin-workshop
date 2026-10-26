# -*- coding: utf-8 -*-
"""陪伴层测试: 台词三级降级、心情映射、局中搭话策略。

重点锁死一个曾经静默失效的点: **事件键 → 心情必须用 persona 的规范 id**
(excitement/curiosity…)。写 excited/curious 时 Mood.trigger 会静默忽略,
表现为"游戏赢了猫娘也不兴奋、提示词里的心情永远是平静"——不报错, 只是不生效。
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Dict, List

from plugin.plugins.neko_arcade.core.companion import (
    MOOD_ALIASES,
    MOOD_BY_EVENT,
    MOOD_DESC,
    Companion,
    mood_id,
)
from plugin.plugins.neko_arcade.core.persona import EMOTION_POOL, Persona

REPO = Path(__file__).resolve().parents[1]


class StubCfg:
    """按游戏返回 emotion.json 模板。"""

    def __init__(self, templates: Dict[str, Dict[str, List[str]]]):
        self._t = templates

    def load(self, game_id: str):
        return type("GC", (), {"emotion_templates": self._t.get(game_id, {})})()


def _comp(persona: Any = None, templates=None, cfg=None) -> Companion:
    return Companion(persona=persona if persona is not None else Persona({}),
                     llm=None, config_manager=StubCfg(templates or {}), cfg=cfg or {})


# ── 心情映射: 必须落在规范 id 上 ─────────────────────────
def test_event_moods_use_canonical_ids() -> None:
    """事件 → 心情的值必须是 persona 认得的规范 id(否则心情静默不动)。"""
    allowed = set(EMOTION_POOL) | {"calm"}
    bad = {k: v for k, v in MOOD_BY_EVENT.items() if v not in allowed}
    assert not bad, f"这些事件映射到了 persona 不认识的心情: {bad}"


def test_mood_desc_covers_canonical_pool() -> None:
    """提示词用的中文标签必须覆盖全部规范 id(否则会退成"平静")。"""
    missing = set(EMOTION_POOL) - set(MOOD_DESC)
    assert not missing, f"MOOD_DESC 缺规范 id: {missing}"


def test_mood_alias_normalizes_common_spellings() -> None:
    assert mood_id("excited") == "excitement"
    assert mood_id("EXCITED") == "excitement"
    assert mood_id("兴奋") == "excitement"
    assert mood_id("curious") == "curiosity"
    assert mood_id("excitement") == "excitement"      # 规范 id 原样通过
    assert mood_id("不认识的心情") == ""
    assert mood_id(None) == ""
    for alias, target in MOOD_ALIASES.items():
        assert target in set(EMOTION_POOL) | {"calm"}, (alias, target)


def test_feel_actually_moves_the_mood() -> None:
    """高光事件必须真的把心情推起来(曾经因为 id 写成 excited 而完全无效)。"""
    comp = _comp()
    assert comp.persona.mood.snapshot()["excitement"] == 0.0
    comp.feel("highlight")
    assert comp.persona.mood.snapshot()["excitement"] > 0.5
    assert comp.mood() == "excitement"
    assert comp.mood_label() == "兴奋"


def test_feel_lowlight_and_unknown_event() -> None:
    comp = _comp()
    comp.feel("lose")
    assert comp.persona.mood.snapshot()["upset"] > 0.4
    comp.feel("完全不认识的事件")          # 不该抛
    comp.feel(None)
    assert comp.mood() in set(EMOTION_POOL) | {"calm"}


def test_apply_control_accepts_aliases_and_ignores_junk() -> None:
    """台词尾部的控制 JSON 可以写 excited/兴奋, 也要能扛住乱写。"""
    comp = _comp()
    comp.apply_control({"mood": "excited", "intensity": 0.9})
    assert comp.persona.mood.snapshot()["excitement"] > 0.8
    comp.apply_control({"mood": "curious", "intensity": 0.5})
    assert comp.persona.mood.snapshot()["curiosity"] > 0.4
    comp.apply_control({"mood": "??", "intensity": "很大"})   # 双重异常输入
    comp.apply_control({})
    assert comp.mood()                       # 仍在正常范围


def test_prompt_carries_real_mood_label() -> None:
    comp = _comp()
    comp.feel("highlight")
    prompt = comp._prompt("win", {"event_desc": "主人赢了"}, "", "五子棋", "")
    assert "你此刻的心情：兴奋" in prompt
    assert "平静" not in prompt


def test_split_control_strips_json_from_line() -> None:
    """台词模型可以在句尾带控制 JSON —— 要剥掉不给用户看, 并应用心情。"""
    from plugin.plugins.neko_arcade.core.companion import _split_control

    text, control = _split_control('哼，这条不算喵！\n{"mood": "proud", "intensity": 0.8}')
    assert text == "哼，这条不算喵！"
    assert control == {"mood": "proud", "intensity": 0.8}
    assert _split_control("普通台词") == ("普通台词", {})
    assert _split_control("带半个括号 {") == ("带半个括号 {", {})


# ── 台词三级降级 ────────────────────────────────────────
def test_line_falls_back_to_game_template_without_llm() -> None:
    templates = {"boardgame": {"win": ["赢啦{who}喵！", "又赢了喵~"]}}
    comp = _comp(templates=templates)
    out = asyncio.run(comp.line("boardgame", "win", ctx={"who": "主人"}))
    assert out in ("赢啦主人喵！", "又赢了喵~")


def test_line_template_key_fallback_chain() -> None:
    """事件键细分(catch_rare)时退到前缀(catch), 再退到 chat。"""
    comp = _comp(templates={"fishing": {"catch": ["钓到啦喵"], "chat": ["在呢喵"]}})
    assert asyncio.run(comp.line("fishing", "catch_rare")) == "钓到啦喵"
    assert asyncio.run(comp.line("fishing", "unknown_event")) == "在呢喵"


def test_line_returns_empty_when_nothing_available() -> None:
    assert asyncio.run(_comp().line("nope", "win")) == ""


def test_chat_reply_never_cached() -> None:
    """主人说话要新鲜回应: 不读缓存。"""
    comp = _comp()
    comp._cache["boardgame|chat|calm|"] = "旧回应"
    out = asyncio.run(comp.chat_reply("boardgame", "你在干嘛"))
    assert out != "旧回应"


# ── 局中搭话策略(阈值/次数/节流都在本体) ────────────────
def test_should_nudge_respects_threshold_count_and_gap() -> None:
    comp = _comp(cfg={"nudge_after": 60, "nudge_max": 2, "nudge_gap": 100})
    now = 10_000.0
    assert not comp.should_nudge(now, now=now)                       # 刚活动过
    assert comp.should_nudge(now - 90, now=now)                      # 静默够久 → 可以
    assert not comp.should_nudge(now - 90, now=now + 10)             # 节流未过
    assert comp.should_nudge(now - 90, nudges=1, now=now + 200)      # 次数还没用完
    assert not comp.should_nudge(now - 90, nudges=2, now=now + 400)  # 次数用完


# ── 真实配置: 棋类游戏的模板能读到 ──────────────────────
def test_boardgame_emotion_templates_load() -> None:
    raw = json.loads((REPO / "data" / "config" / "boardgame" / "emotion.json")
                     .read_text(encoding="utf-8"))
    comp = _comp(templates={"boardgame": raw})
    pool = comp.templates("boardgame", "win")
    assert pool, "棋类游戏 emotion.json 应有 win 模板"
