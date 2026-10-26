"""仓库冒烟测试：配置/入口/事件链路。"""

from __future__ import annotations

import pathlib
import tomllib

_ROOT = pathlib.Path(__file__).resolve().parent.parent


def _manifest() -> dict:
    return tomllib.loads((_ROOT / "plugin.toml").read_text(encoding="utf-8"))


def test_plugin_manifest_declares_entry_and_ui():
    m = _manifest()
    assert m["plugin"]["id"] == "neko_fishpower"
    assert m["plugin"]["entry"] == "plugin.plugins.neko_fishpower:NekoFishpowerPlugin"
    assert m["plugin"]["ui"]["enabled"] is True
    for panel in m["plugin"]["ui"]["panel"]:
        entry = _ROOT / panel["entry"]
        assert entry.exists(), f"UI entry missing: {panel['entry']}"


def test_core_modules_compile():
    import ast
    for py in sorted((_ROOT / "core").glob("*.py")) + sorted((_ROOT / "adapters").glob("*.py")):
        ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
    from core.contracts import FishEvent, FishState
    st = FishState()
    assert st.game_running is False
    ev = FishEvent(name="caught")
    assert ev.name == "caught"


def test_arbiter_gates_categories_and_cooldown():
    from core.arbiter import Arbiter
    from core.config_model import FishpowerConfig
    from core.safety_guard import SafetyGuard
    from core.state_machine import FISHING

    cfg = FishpowerConfig({"dry_run": True})
    safety = SafetyGuard(cfg)
    arb = Arbiter(cfg, safety)
    arb.broadcast_categories = dict(cfg.broadcast_categories)
    # 进入钓鱼状态（否则 caught 被状态门控拒绝）
    arb.scenario.current = FISHING

    # 类别关闭 → 拒绝
    arb.broadcast_categories["chatter"] = False
    ok, _ = arb.decide("chatter")
    assert ok is False, "chatter 类别关闭应拒绝"

    # 上钩（抢占）→ 允许
    ok, reason = arb.decide("caught")
    assert ok is True, reason

    # 同事件冷却 → 拒绝
    ok, reason = arb.decide("caught")
    assert ok is False, "caught 冷却中应拒绝"

    # 玩家说话静默窗 → 拒绝
    arb.on_player_speak(silence_s=60)
    ok, reason = arb.decide("bite")
    assert ok is False, "静默窗内应拒绝"
    assert reason == "player_quiet_window"


def test_runtime_event_renders_caught():
    """caught 事件 → 事实行包含鱼信息。"""
    from core.mood import Persona
    from core.templates import EmotionRenderer

    class _FakeLlm:
        configured = False
        async def call(self, prompt):  # noqa: ARG002
            return None

    persona = Persona(None)
    renderer = EmotionRenderer(persona, llm=_FakeLlm())
    line = renderer.fact_prompt("caught", fish="Cod", weight=3.2, worth=45, extra="")
    assert "Cod" in line and "3.2" in line and "45" in line


def test_scene_chat_knowledge_ocr():
    """OCR 屏幕理解：Boss 机制命中聊知识；看不懂的屏幕安静（非十万个为什么）。"""
    from core.scene_chat import SceneChat

    class _FakeKB:
        def creature_names(self):
            return ["Cod"]

        def bait_names(self):
            return ["Worm"]

        def creature_info(self, n):
            return f"「{n}」值 30 金币 喵~"

    import time
    now = time.time()
    sc = SceneChat(_FakeKB())
    # 机制关键词兜底（scene_chat 不含 casino——已在场景层排除）
    assert sc.topic_from_ocr("boss 出现了", now=now) is not None
    # 知识库鱼种命中
    sc2 = SceneChat(_FakeKB())
    t = sc2.topic_from_ocr("caught Cod", now=now)
    assert t is not None and "Cod" in t
    # 看不懂的屏幕（去重后安静）
    sc3 = SceneChat(_FakeKB())
    assert sc3.topic_from_ocr("设置 图形 音量", now=now) is None


def test_recall_island_no_dup_suffix():
    """recall 岛屿名：已含"岛"不重复拼；原始名自动补"岛"。"""
    from core.recall import Recall

    class _FakeMem:
        def query(self, kind, key):
            return {"ts": 1725000000}

        def weight_of(self, kind, key):
            return 0.5

    r = Recall(_FakeMem())
    assert "一号岛岛" not in r.on_island("一号岛")
    assert r.on_island("一号岛").startswith("又回一号岛了")
    assert r.on_island("Island_1").startswith("又回Island_1岛了")


def test_event_catalog_high_freq_suppressed():
    """高频常态事件（cast/bite/miss）长冷却≈静默；大事件保持短冷却抢占。"""
    from core.event_catalog import spec

    for name in ("cast", "bite", "miss", "sold"):
        s = spec(name)
        assert s.cooldown_seconds >= 120, f"{name} 应高频静默（冷却>=120s）"
    # 大事件高优先抢占
    death = spec("player_death")
    assert death.preempt is True and death.priority >= 80
    caught = spec("caught")
    assert caught.preempt is True


def test_state_machine_gates_no_game():
    """游戏状态机：没开游戏不交互（修"没开还提示换饵"）；钓鱼中可交互。"""
    from core.state_machine import FISHING, MENU, NO_GAME, GameStateMachine

    class _S:
        def __init__(self, **kw):
            self.connected = kw.get("connected", False)
            self.boss_active = kw.get("boss_active", False)
            self.betting = kw.get("betting", False)
            self.island = kw.get("island", "")
            self.phase = kw.get("phase", "")

    sm = GameStateMachine()
    # 游戏没开 → NO_GAME，不交互，禁游戏事件
    assert sm.update(_S()) == NO_GAME
    assert sm.interactive() is False
    assert sm.allow("caught") is False
    # 主菜单 → MENU，禁游戏事件
    assert sm.update(_S(connected=True, island="", phase="")) == MENU
    assert sm.allow("caught") is False
    # 钓鱼中 → FISHING，可交互
    assert sm.update(_S(connected=True, island="一号岛", phase="waiting")) == FISHING
    assert sm.interactive() is True
    assert sm.allow("caught") is True
    # 断连强制回 NO_GAME
    sm.force_no_game()
    assert sm.current == NO_GAME
    assert sm.interactive() is False


def test_arbiter_gates_by_state():
    """arbiter 状态门控：游戏没开时 caught 被拒（修误触发）。"""
    from core.arbiter import Arbiter
    from core.config_model import FishpowerConfig
    from core.safety_guard import SafetyGuard

    cfg = FishpowerConfig({"dry_run": True})
    arb = Arbiter(cfg, SafetyGuard(cfg))
    arb.broadcast_categories = dict(cfg.broadcast_categories)
    # 默认 NO_GAME → caught 拒绝
    ok, reason = arb.decide("caught")
    assert ok is False
    assert reason.startswith("state_gated")
