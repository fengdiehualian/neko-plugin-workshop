"""card_master v0.4.5 通用入口测试：entry_run 分派（纯逻辑路径，不依赖真实运行环境）。"""

import asyncio
import threading


def _plugin():
    from plugin.plugins.card_master import CardMasterPlugin

    inst = CardMasterPlugin.__new__(CardMasterPlugin)
    inst._lock = threading.RLock()
    inst._game = None
    inst._game_log = []
    inst._live_sessions = {}
    inst._dd_game = None
    inst._dd_stats = {"wins": 0, "losses": 0, "role": "auto"}
    return inst


def _run(coro):
    return asyncio.run(coro)


def test_run_entry_registered():
    """plugin_entry run 入口可被宿主收集（v0.4.5 新增）。"""
    from plugin.plugins.card_master import CardMasterPlugin

    metas = []
    for name in dir(CardMasterPlugin):
        if name.startswith("_"):
            continue
        meta = getattr(getattr(CardMasterPlugin, name, None), "__neko_event_meta__", None)
        if meta is not None and getattr(meta, "event_type", None) == "plugin_entry":
            metas.append(meta)
    ids = [m.id for m in metas]
    assert "run" in ids, f"缺少入口 run，现有：{ids}"


def test_run_unknown_action():
    inst = _plugin()
    out = _run(inst.entry_run(action="fly", parameters={}))
    assert out["is_error"] is True
    assert out["error"] == "NO_ACTION"


def test_run_rules_poker():
    inst = _plugin()
    out = _run(inst.entry_run(action="rules", game="poker", parameters={}))
    assert out["is_error"] is False
    assert "德州扑克" in out["output"]


def test_run_evaluate_hand():
    inst = _plugin()
    out = _run(inst.entry_run(action="evaluate", hand="As Ks", community="Qs Js Ts", parameters={}))
    assert out["is_error"] is False
    assert "牌型" in out["output"]


def test_run_unknown_game_rules():
    inst = _plugin()
    out = _run(inst.entry_run(action="rules", game="mau_mau", parameters={}))
    assert out["is_error"] is True
    assert out["error"] == "UNKNOWN_GAME"


def test_run_doudizhu_new_and_state():
    inst = _plugin()
    out = _run(inst.entry_run(action="doudizhu", parameters={}))
    assert out["is_error"] is False
    assert inst._dd_game is not None
    out2 = _run(inst.entry_run(action="doudizhu", sub_action="state", parameters={}))
    assert out2["is_error"] is False
    assert out2["game_state"]["stage"] in ("bidding", "playing", "over")
