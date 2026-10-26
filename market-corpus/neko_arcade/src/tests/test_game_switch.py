"""交互式换游戏测试。

背景（线上会遇到的坑）: 用户在玩钓鱼时直接发「修仙」，parse_input 会路由到
修仙，但旧实现里 brain._current_game 仍是钓鱼 —— 新游戏 on_start 不跑、
旧游戏 on_stop 不跑、状态锚还在说钓鱼，玩家感觉「修仙指令失效」。

现在改为：先返回 outcome="switch_confirm" 并只给 LLM 一条 summary 指令
（不推送固定话术，确认对话由猫娘 LLM 自己生成）；用户确认后 LLM 再调
play_game 并填 switch_to，插件才完整收尾旧会话、开始新会话。
"""
from __future__ import annotations

import asyncio

from plugin.plugins.neko_arcade.core.brain import GameBrain


class _FakeMood:
    def snapshot(self):
        return {"mood": "calm"}

    def style(self):
        return "calm"

    def primary(self):
        return "calm"

    def decay_all(self):
        pass


class _FakePersona:
    def __init__(self):
        self.mood = _FakeMood()
        self.user_call = "主人"

    def on_event(self, kind):
        pass

    def polish(self, text):
        return text

    def snapshot(self):
        return {}


class _FakeMemory:
    async def register_play(self, gid):
        pass

    def record(self, gid, outcome, facts, msg):
        pass

    async def bump_stat(self, *a, **k):
        pass

    async def snapshot(self):
        return {}


class _FakeProactive:
    def tick(self):
        pass

    def on_result(self, outcome):
        pass

    def on_owner_speak(self):
        pass

    def snapshot(self):
        return {}


class _FakeEmotion:
    async def render(self, *a, **k):
        return ("", "routine")


class _FakePush:
    def __init__(self):
        self.texts = []

    async def text(self, text, **kw):
        self.texts.append(text)


class _FakeGame:
    def __init__(self, gid, name, keywords, enabled=True):
        self.id = gid
        self.name = name
        self.icon = "🎮"
        self.description = name
        self.version = "0.1.0"
        self.enabled = enabled
        self._keywords = keywords
        self.started = 0
        self.stopped = 0
        self.last_cmd = None

    def get_keywords(self):
        return self._keywords

    def get_emotion_templates(self):
        return {}

    def classify_event(self, outcome, facts):
        return "routine"

    def wants_card(self, outcome, facts):
        return False

    def get_meta(self):
        return {"id": self.id, "name": self.name, "icon": self.icon,
                "enabled": self.enabled}

    async def on_start(self, uid):
        self.started += 1

    async def on_stop(self, uid):
        self.stopped += 1

    async def on_milestone(self, outcome, facts, memory):
        pass

    async def handle_action(self, uid, cmd, args=None):
        self.last_cmd = cmd
        if cmd in getattr(self, "unknown_cmds", ()):
            return {"facts": [], "outcome": "unknown", "message": ""}
        return {"facts": [], "outcome": "done", "message": f"{self.name}开局"}


class _FakeRegistry:
    def __init__(self, games):
        self._by_id = {g.id: g for g in games}
        self.games = list(games)

    def get(self, gid):
        return self._by_id.get(gid)

    def get_by_name(self, name):
        for g in self.games:
            if name and (name in g.name or name in g.id):
                return g
        return None

    async def get_help(self, gid):
        return {"commands": []}


def _make_brain(current=None):
    fishing = _FakeGame("fishing", "钓鱼", ["钓鱼", "抛竿"])
    xiuxian = _FakeGame("xiuxian", "修仙", ["修仙"])
    brain = GameBrain.__new__(GameBrain)
    brain.registry = _FakeRegistry([fishing, xiuxian])
    brain.persona = _FakePersona()
    brain.memory = _FakeMemory()
    brain.proactive = _FakeProactive()
    brain.emotion = _FakeEmotion()
    brain.push = _FakePush()
    brain.tts = None
    brain.img = None
    brain.cfg = {}
    brain.plugin = None
    brain.cfg_mgr = None
    brain._current_game = current
    brain._current_user = "default" if current else None
    brain._session_start = 0.0
    brain._last_anchor_ts = 0.0
    brain._anchor_interval = 20.0
    brain._last_invite = {}
    brain._pending_switch = None
    brain._switch_confirm_ttl = 180.0
    brain._last_activity_ts = 0.0
    brain._background_active_window = 900.0
    brain._help_rend = None
    return brain, fishing, xiuxian


def test_switch_asks_confirmation_instead_of_silent_switch():
    brain, fishing, xiuxian = _make_brain(current="fishing")
    res = asyncio.run(brain.handle_action("xiuxian", "修仙"))
    assert res["outcome"] == "switch_confirm"
    assert brain._current_game == "fishing", "确认前不能切走"
    assert fishing.stopped == 0 and xiuxian.started == 0
    assert xiuxian.last_cmd is None, "确认前不能执行新游戏"
    assert "switch_to" in res["summary"]
    assert brain.push.texts == [], "确认前不推固定话术（由 LLM 生成）"


def test_confirmed_switch_ends_old_and_starts_new():
    brain, fishing, xiuxian = _make_brain(current="fishing")
    asyncio.run(brain.handle_action("xiuxian", "修仙"))
    res = asyncio.run(brain.handle_action(
        "fishing", "好", {"switch_to": "xiuxian"}))
    assert brain._current_game == "xiuxian"
    assert fishing.stopped == 1, "旧游戏要 on_stop"
    assert xiuxian.started == 1, "新游戏要 on_start"
    assert xiuxian.last_cmd == "修仙", "用之前想玩的指令开新游戏"
    assert "[切换完成]" in res["summary"]
    assert brain._pending_switch is None


def test_switch_to_without_pending_still_asks_first():
    """LLM 抢跑（没确认就填 switch_to）也要先走确认，不能静默切走。"""
    brain, fishing, xiuxian = _make_brain(current="fishing")
    res = asyncio.run(brain.handle_action(
        "fishing", "修仙", {"switch_to": "xiuxian"}))
    assert res["outcome"] == "switch_confirm"
    assert brain._current_game == "fishing"
    assert fishing.stopped == 0 and xiuxian.started == 0


def test_decline_clears_pending_and_keeps_current():
    brain, fishing, xiuxian = _make_brain(current="fishing")
    asyncio.run(brain.handle_action("xiuxian", "修仙"))
    asyncio.run(brain.handle_action("fishing", "继续钓鱼"))
    assert brain._pending_switch is None
    assert brain._current_game == "fishing"
    assert xiuxian.started == 0 and xiuxian.stopped == 0


def test_pending_switch_reasks_when_current_game_rejects_input():
    """用户回了确认词但 LLM 没填 switch_to：当前游戏不认这条 → 重新问一次。"""
    brain, fishing, xiuxian = _make_brain(current="fishing")
    asyncio.run(brain.handle_action("xiuxian", "修仙"))
    fishing.unknown_cmds = {"好"}
    res = asyncio.run(brain.handle_action("fishing", "好"))
    assert res["outcome"] == "switch_confirm"
    assert brain._pending_switch is not None
    assert brain._current_game == "fishing"
    assert xiuxian.started == 0


def test_start_game_also_ends_old_session():
    brain, fishing, xiuxian = _make_brain(current="fishing")
    asyncio.run(brain.start_game("xiuxian"))
    assert fishing.stopped == 1
    assert xiuxian.started == 1
    assert brain._current_game == "xiuxian"


def test_pending_switch_expires_and_does_not_hijack():
    brain, fishing, xiuxian = _make_brain(current="fishing")
    asyncio.run(brain.handle_action("xiuxian", "修仙"))
    brain._pending_switch["ts"] = 0.0
    brain._switch_confirm_ttl = 1.0
    asyncio.run(brain.tick())
    assert brain._pending_switch is None
