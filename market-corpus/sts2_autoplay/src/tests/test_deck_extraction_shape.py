"""`_extract_deck` 必须兼容 mod 的**实机** `/state` 形状（缺陷 J）。

★ 缺陷 J（2026-10-17 实机查证）：

实机 `/state`（mod 端口 18080）的形状是：
```json
{
  "state_version": ..., "run_id": ..., "screen": ...,
  "run": { "floor": ..., "current_hp": ..., "deck": [ {card}, ... ] },
  "selection": ..., "reward": ..., ...
}
```
卡组在 **`run.deck`**，而且是 **list**；**顶层没有 `deck` 键**。
实测顶层键：`state_version, run_id, screen, unknown_screen_type, session, in_combat,
turn, available_actions, combat, run, multiplayer, multiplayer_lobby, map, selection,
character_select, timeline, chest, event, shop, rest, reward, bundles, modal,
game_over, main_menu, external_control, agent_view` —— **无 `deck`**。

原实现统一写 `raw_state.get("deck")`（顶层）⇒ **恒为 `{}`**
⇒ 奖励屏/商店屏的 `deck` 永远为空
⇒ `_infer_archetype_tags` 拿不到卡组 ⇒ **流派推断永远为空**
⇒ 即使 `_archetype_policy_from_deck` 写得再对，也永远产不出流派偏好。

**实测证据**（实机选卡留痕，`[sts2_reward_route]`）：
```
流派=[] 成型=[] 教训={'defense': 1} 候选评分=0=16.5 ... -> 选 option_index=2
流派=[] 成型=[] 教训={'defense': 1} 候选评分=0=16.5 ... -> 选 option_index=2
```
而同一时刻 `run.deck` 有 20 张卡。`流派=[]` 就是这个缺陷的指纹。
"""
from __future__ import annotations

from plugin.plugins.sts2_autoplay.summary_context_builder import STS2SummaryContextBuilder

DEFEND = {"name": "防御", "card_id": "DEFEND_NECROBINDER",
          "resolved_rules_text": "获得5点格挡。", "card_type": "Skill", "index": 0}


def _builder() -> STS2SummaryContextBuilder:
    return STS2SummaryContextBuilder()


def test_reads_run_deck_list_live_shape() -> None:
    """★实机形状：卡组在 `run.deck`（list）—— 这是唯一真实存在的形状。"""
    b = _builder()
    raw = {"run_id": "X", "run": {"floor": 5, "deck": [dict(DEFEND) for _ in range(5)]}}
    deck = b._extract_deck(raw)
    assert len(deck.get("cards") or []) == 5


def test_live_shape_feeds_archetype_inference() -> None:
    """★端到端：实机形状的卡组必须能推出流派（此前恒为空）。"""
    b = _builder()
    raw = {"run": {"deck": [dict(DEFEND) for _ in range(8)]}}
    deck = b._extract_deck(raw)
    assert "defense" in b._infer_archetype_tags(deck)


def test_backward_compatible_with_top_level_dict_deck() -> None:
    """旧口径（顶层 `deck` 且已是 dict）必须仍然可用。"""
    b = _builder()
    raw = {"deck": {"cards": [dict(DEFEND) for _ in range(4)]}}
    assert len(b._extract_deck(raw).get("cards") or []) == 4


def test_top_level_dict_takes_priority_when_populated() -> None:
    """两者都在时，尊重**已填充**的旧口径（不悄悄换源）。"""
    b = _builder()
    raw = {"deck": {"cards": [dict(DEFEND) for _ in range(2)]},
           "run": {"deck": [dict(DEFEND) for _ in range(5)]}}
    assert len(b._extract_deck(raw).get("cards") or []) == 2


def test_falls_back_to_run_deck_when_top_level_empty() -> None:
    """顶层 `deck` 是空 dict 时，必须回退到 `run.deck`。

    这是**最容易漏**的组合：顶层键**存在但为空** ⇒
    只判 `isinstance(top, dict)` 就会返回空卡组。
    """
    b = _builder()
    raw = {"deck": {}, "run": {"deck": [dict(DEFEND) for _ in range(6)]}}
    assert len(b._extract_deck(raw).get("cards") or []) == 6


def test_malformed_inputs_never_raise() -> None:
    """向前兼容：畸形 `/state` 不得抛异常，且返回空卡组。"""
    b = _builder()
    for bad in ({}, None, {"deck": "x"}, {"run": "x"}, {"run": {"deck": "x"}},
                {"deck": []}, {"run": {"deck": {}}}, {"run": {"deck": [None, 1]}}):
        out = b._extract_deck(bad)  # type: ignore[arg-type]
        assert isinstance(out, dict)


def test_exactly_one_source_is_chosen() -> None:
    """★不得把两个来源合并（否则卡组张数翻倍 ⇒ 流派计数错）。"""
    b = _builder()
    raw = {"deck": {}, "run": {"deck": [dict(DEFEND) for _ in range(3)]}}
    deck = b._extract_deck(raw)
    assert len(deck.get("cards") or []) == 3, "不得合并两个来源"


#: 全部 builder —— **每个都必须产出 `deck`**。
#:
#: ★ 缺陷 J3（2026-10-17 实机查证）：此前只有 4/9 个 builder 有 `deck` 键，
#:   而 `card_selection_reward` / `card_selection`（**选卡屏**）走 `_build_selection_context`，
#:   它**完全没有 `deck` 键** ⇒ 选卡屏流派恒空（实机留痕 `流派=[]` 反复出现）。
#:   `_build_event_context` 同样缺失 —— 而用户点名的缺陷 A/B **都在事件屏**。
_ALL_BUILDERS = [
    "_build_combat_context",
    "_build_event_context",
    "_build_general_context",
    "_build_map_context",
    "_build_menu_context",
    "_build_rest_context",
    "_build_reward_context",
    "_build_selection_context",
    "_build_shop_context",
]


def test_every_builder_declares_deck() -> None:
    """★每一个 builder 都必须产出 `deck` 键（缺一个 ⇒ 那一屏的流派恒空）。"""
    import inspect

    b = _builder()
    missing = []
    for name in _ALL_BUILDERS:
        src = inspect.getsource(getattr(type(b), name))
        if '"deck"' not in src or "_extract_deck" not in src:
            missing.append(name)
    assert not missing, f"以下 builder 缺 deck / 未用 _extract_deck：{missing}"


def test_every_builder_returns_live_shape_deck() -> None:
    """★所有 builder 在**实机形状**输入下都必须返回非空卡组。

    这是"逐屏修复但漏掉某一屏"这类缺陷的通用闸门。
    """
    b = _builder()
    raw = {
        "run_id": "X",
        "screen": "REWARD",
        "run": {"floor": 5, "current_hp": 50, "max_hp": 80,
                "deck": [dict(DEFEND) for _ in range(7)]},
        "reward": {"cards": []},
        "selection": {"cards": []},
    }
    for name in _ALL_BUILDERS:
        out = getattr(b, name)({"available_actions": []}, raw)
        deck = out.get("deck") or {}
        assert len(deck.get("cards") or []) == 7, (
            f"{name} 在实机形状下拿不到卡组（得到 {len(deck.get('cards') or [])} 张）"
        )


def test_selection_builder_feeds_archetype_for_card_selection_reward() -> None:
    """★选卡屏（`card_selection_reward` → selection builder）必须能推出流派。

    这条对应实机留痕反复出现的 `流派=[]`：选卡屏走的正是
    `_build_selection_context`，而它此前没有 `deck`。
    """
    b = _builder()
    raw = {"run": {"deck": [dict(DEFEND) for _ in range(8)]},
           "selection": {"cards": []}}
    out = b._build_selection_context({"available_actions": []}, raw)
    assert "defense" in b._infer_archetype_tags(out.get("deck") or {})


def test_event_builder_feeds_archetype() -> None:
    """★事件屏（用户缺陷 A/B 所在屏）也必须能推出流派。"""
    b = _builder()
    raw = {"run": {"deck": [dict(DEFEND) for _ in range(8)]},
           "event": {"options": []}}
    out = b._build_event_context({"available_actions": []}, raw)
    assert "defense" in b._infer_archetype_tags(out.get("deck") or {})
