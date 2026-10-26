"""重复回调守门测试（线上"双重回复"）。

背景（2026-09-27 实机五子棋对局）:
  用户「我下在J9吧」→ 插件落子成功并推送状态卡；
  8 秒后宿主的 run 又**带着同一条旧输入**回调 play_game → 第二次落子被判非法
  （"有子了喵"）→ 用户看到两份状态卡，LLM 也跟着说两遍 = 双重回复。

守门规则（core/brain.py）:
  同一条输入在上一次**没有推进状态**(illegal/unknown/idle/hint/error)之后
  DUP_INPUT_WINDOW 秒内再次出现 → 判为宿主重复回调：不再执行、不再推送，
  只回一条"这是重复回调、别复述"的 summary。
  正常推进的输入（例如连点「抛竿」）不受影响。
"""
from __future__ import annotations

import asyncio

from plugin.plugins.neko_arcade.core.brain import (
    DUP_INPUT_WINDOW,
    NO_PROGRESS_OUTCOMES,
    GameBrain,
)


class _FakeMood:
    def snapshot(self):
        return {"mood": "calm"}

    def style(self):
        return "calm"


class _FakePersona:
    def __init__(self):
        self.mood = _FakeMood()

    def on_event(self, kind):
        pass

    def polish(self, text):
        return text


class _FakeMemory:
    def record(self, gid, outcome, facts, msg):
        pass

    async def bump_stat(self, *a, **k):
        pass


class _FakeProactive:
    def on_result(self, outcome):
        pass


class _FakeEmotion:
    async def render(self, *a, **k):
        return ("", "routine")


class _FakePush:
    def __init__(self):
        self.texts = []

    async def text(self, text, **kw):
        self.texts.append(text)


class _RecorderGame:
    """每次 handle_action 计一次，返回脚本给定的 outcome。"""

    id = "boardgame"
    name = "棋类对弈"
    icon = "♟️"
    enabled = True

    def __init__(self, outcome: str = "illegal"):
        self.outcome = outcome
        self.calls = []

    def get_keywords(self):
        return ["五子棋"]

    def get_emotion_templates(self):
        return {}

    def classify_event(self, outcome, facts):
        return "routine"

    def wants_card(self, outcome, facts):
        return False

    async def on_milestone(self, outcome, facts, memory):
        pass

    async def handle_action(self, uid, cmd, args=None):
        self.calls.append(cmd)
        return {"facts": [], "outcome": self.outcome, "message": "有子了喵"}


class _FakeRegistry:
    def __init__(self, game):
        self.game = game

    def get(self, gid):
        return self.game if gid == self.game.id else None

    async def get_help(self, gid):
        return {"commands": []}


def _make_brain(game, current="boardgame"):
    brain = GameBrain.__new__(GameBrain)
    brain.registry = _FakeRegistry(game)
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
    brain._current_user = "default"
    brain._session_start = 0.0
    brain._last_anchor_ts = 0.0
    brain._last_invite = {}
    brain._help_rend = None
    brain._persona_raw = None
    brain._pending_switch = None
    return brain


def test_repeated_non_progress_input_is_ignored():
    """同一条输入 + 上次没推进状态 + 窗口内 → 不再执行、不再推送。"""
    async def run():
        game = _RecorderGame(outcome="illegal")
        brain = _make_brain(game)

        first = await brain.handle_action("boardgame", "我下在J9吧")
        assert game.calls == ["我下在J9吧"], "第一次必须真执行"
        assert first.get("outcome") == "illegal"
        assert brain.push.texts == ["有子了喵"], "第一次要推送提示"

        second = await brain.handle_action("boardgame", "我下在J9吧")
        assert second.get("duplicate") is True
        assert game.calls == ["我下在J9吧"], "重复回调不得再执行一次"
        assert brain.push.texts == ["有子了喵"], "重复回调不得再推第二条（双重回复）"

    asyncio.run(run())


def test_progress_input_twice_still_runs():
    """推进了状态的输入（连点「抛竿」）不受守门影响。"""
    async def run():
        game = _RecorderGame(outcome="done")
        brain = _make_brain(game)

        await brain.handle_action("boardgame", "抛竿")
        await brain.handle_action("boardgame", "抛竿")
        assert game.calls == ["抛竿", "抛竿"], "正常推进的重复输入必须照常执行"
        assert len(brain.push.texts) == 2

    asyncio.run(run())


def test_window_expiry_allows_retry():
    """超出窗口后同一条输入重来一次仍会被执行（用户真的又试了一次）。"""
    async def run():
        game = _RecorderGame(outcome="illegal")
        brain = _make_brain(game)

        await brain.handle_action("boardgame", "我下在J9吧")
        # 把时间戳推到窗口外
        brain._recent_inputs[("boardgame", "我下在J9吧")] = (
            0.0, "illegal")
        await brain.handle_action("boardgame", "我下在J9吧")
        assert game.calls == ["我下在J9吧", "我下在J9吧"]
        assert DUP_INPUT_WINDOW > 0
        assert "illegal" in NO_PROGRESS_OUTCOMES

    asyncio.run(run())


def test_other_input_not_affected():
    """不同输入互不干扰。"""
    async def run():
        game = _RecorderGame(outcome="illegal")
        brain = _make_brain(game)

        await brain.handle_action("boardgame", "我下在J9吧")
        await brain.handle_action("boardgame", "我下在H8吧")
        assert game.calls == ["我下在J9吧", "我下在H8吧"]

    asyncio.run(run())
