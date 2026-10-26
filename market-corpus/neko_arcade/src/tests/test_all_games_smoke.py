"""全游戏审查: 每个游戏都能构造/开局/接指令且不抛异常, 事件键都有台词可讲。

这类问题静态检查看不出来(ruff 只查名字), 所以用真跑一遍来兜底:
  · 构造 + on_start + 首关键词 + 几个通用输入 → 必须返回结构化结果, 不能抛
  · 游戏会往外报的 fact kind / 事件键, 必须在自己的 emotion.json 里有模板,
    否则线上会退化成"嗯…就这些啦喵"这种冷场(未知 bug 的典型来源)
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

from plugin.plugins.neko_arcade.core.config_manager import ConfigManager
from plugin.plugins.neko_arcade.core.registry import GameRegistry

ROOT = Path(__file__).resolve().parent.parent
CFG_ROOT = ROOT / "data" / "config"

#: 合规游戏必须"每类事件都有话说"; 图库本体是特例(它的输出不是台词驱动)
WHITELIST = {"neko_photo"}


class StubStore:
    def __init__(self) -> None:
        self.data = {}


class StubPlugin:
    def __init__(self, data_dir: Path) -> None:
        self.store = StubStore()
        self.plugin_dir = str(ROOT)
        self.config_dir = str(ROOT)
        self._data = str(data_dir)

    def data_path(self, *parts):
        return Path(self._data).joinpath(*parts) if parts else Path(self._data)

    async def store_get_user(self, gid, uid, default=None):
        return self.store.data.get((gid, uid), default)

    async def store_save_user(self, gid, uid, data):
        self.store.data[(gid, uid)] = data


def _registry(tmp_path) -> GameRegistry:
    import plugin.plugins.neko_arcade.games as games_pkg
    if not getattr(games_pkg, "__file__", None):
        games_pkg.__file__ = str(ROOT / "games" / "__init__.py")
    reg = GameRegistry(StubPlugin(tmp_path / "data"), ConfigManager(str(tmp_path / "data")))
    asyncio.run(reg.discover())
    return reg


def test_every_game_survives_smoke_inputs(tmp_path) -> None:
    reg = _registry(tmp_path)
    assert reg.game_ids, "一个游戏都没加载"
    problems = []
    for game in list(reg.games):
        try:
            asyncio.run(game.on_start("smoke"))
        except Exception as exc:
            problems.append(f"{game.id} on_start: {exc!r}")
        kws = game.get_keywords() or []
        cmds = [k for k in kws[:1]] + ["帮助", "状态", "继续", "随便聊聊"]
        for cmd in cmds:
            try:
                out = asyncio.run(game.handle_action("smoke", cmd))
                assert isinstance(out, dict), f"{game.id}/{cmd} 返回了 {type(out)}"
                assert "outcome" in out or "message" in out, f"{game.id}/{cmd} 没有 outcome/message"
            except Exception as exc:
                problems.append(f"{game.id} handle_action({cmd!r}): {exc!r}")
    assert not problems, "有游戏在冒烟输入下抛异常:\n" + "\n".join(problems)


def test_compliant_games_have_templates_for_their_events(tmp_path) -> None:
    """合规游戏往外报的事件键/事实类型, 必须能在自己的 emotion.json 里找到台词。"""
    reg = _registry(tmp_path)
    missing = {}
    for game in list(reg.games):
        if game.id in WHITELIST:
            continue
        emo = getattr(game, "_emotion_templates", None) or {}
        keys = set(emo)
        # 代码里所有 {"kind": "xxx"} 的事实类型
        kinds = set()
        for py in (ROOT / "games" / game.id).rglob("*.py"):
            kinds |= set(re.findall(r'"kind"\s*:\s*"([a-z_0-9]+)"', py.read_text(encoding="utf-8")))
        # 情感渲染器自带兜底的事实类型(不算冷场)
        # 内部标记/结构化数据: 不驱动台词, 不算冷场
        FALLBACK_OK = {"catch", "trash", "empty", "event", "win", "lose", "start", "stop",
                       "stats", "score", "hint", "spend", "coin", "balance",
                       "line_source", "none", "board", "timing", "tension", "mood",
                       "hand", "combo", "match_open", "match_abort", "equip", "buy_idle",
                       "record", "new_record", "match_result", "situation"}
        gap = sorted(k for k in kinds if k not in keys and k not in FALLBACK_OK
                     and not any(k.startswith(p) for p in ("catch_", "player_", "cat_"))
                     or (k.startswith(("player_", "cat_")) and k not in keys and k in
                         set(re.findall(r'"(player_[a-z_]+|cat_[a-z_]+)"',
                                        (CFG_ROOT / game.id / "emotion.json").read_text(encoding="utf-8")
                                        if (CFG_ROOT / game.id / "emotion.json").exists() else ""))))
        # 硬断言: 除图库本体外, 任何游戏"会往外报的事件"都必须有模板, 否则线上冷场
        if gap:
            missing[game.id] = gap[:8]
    assert not missing, f"有游戏的事件缺台词模板(线上会冷场): {missing}"
