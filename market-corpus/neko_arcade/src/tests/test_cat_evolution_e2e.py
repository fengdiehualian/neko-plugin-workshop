"""E2E 桩测试: 猫猫进化路 — 注册/探索/战斗/进化。"""
import asyncio
import json
from pathlib import Path

from plugin.plugins.neko_arcade.games.cat_evolution.game import CatEvolutionGame

ROOT = Path(__file__).resolve().parent.parent
CFG = json.loads((ROOT / "data" / "config" / "cat_evolution" / "config.json").read_text(encoding="utf-8"))
EMO = json.loads((ROOT / "data" / "config" / "cat_evolution" / "emotion.json").read_text(encoding="utf-8"))
HELP = json.loads((ROOT / "data" / "config" / "cat_evolution" / "help.json").read_text(encoding="utf-8"))
KWS = json.loads((ROOT / "data" / "config" / "cat_evolution" / "keywords.json").read_text(encoding="utf-8"))


class StubStore:
    def __init__(self):
        self.data = {}

    async def store_get_user(self, gid, uid, default=None):
        return self.data.get((gid, uid), default)

    async def store_save_user(self, gid, uid, data):
        self.data[(gid, uid)] = data


class StubPush:
    def __init__(self, sink):
        self._sink = sink

    async def text(self, text, visibility=None, ai_behavior="blind"):
        self._sink.append(("text", text))


class StubPlugin:
    def __init__(self):
        self.store = StubStore()
        self.pushes = []

    async def store_get_user(self, gid, uid, default=None):
        return await self.store.store_get_user(gid, uid, default)

    async def store_save_user(self, gid, uid, data):
        await self.store.store_save_user(gid, uid, data)


async def main():
    plugin = StubPlugin()
    game = CatEvolutionGame(plugin)
    game._push = StubPush(plugin.pushes)
    game._config = CFG
    game._help = HELP
    game._keywords = KWS
    game._emotion_templates = EMO

    uid = "user_1"
    passed, failed = [], []

    def check(name, cond, detail=""):
        if cond:
            passed.append(name)
            print(f"  ✓ {name}")
        else:
            failed.append(name)
            print(f"  ✗ {name}  {detail}")

    # 1. 注册
    r = await game.handle_action(uid, "领养小猫 咪咪")
    check("注册 outcome", r.get("outcome") == "register", r)
    check("名字=咪咪", game._cache[uid]["name"] == "咪咪", game._cache[uid])
    check("初始小鱼干", game._cache[uid]["bag"].get("小鱼干", 0) == 20, game._cache[uid]["bag"])

    # 2. 未注册保护
    r = await game.handle_action("user_2", "探索")
    check("未注册提示", r.get("outcome") == "need_register", r)

    # 3. 探索 → 地图选择或直接遇敌
    r = await game.handle_action(uid, "探索")
    check("探索 outcome", r.get("outcome") in ("map_select", "win", "lose"), r)

    # 4. 战斗后属性成长(若赢了)
    if r.get("outcome") == "win":
        check("击杀+1", game._cache[uid]["kills"] >= 1, game._cache[uid])
        check("等级成长", game._cache[uid]["stats"]["lv"] >= 2, game._cache[uid]["stats"])

    # 5. 状态
    r = await game.handle_action(uid, "我的状态")
    check("状态 outcome", r.get("outcome") == "status", r)

    # 6. 背包
    r = await game.handle_action(uid, "我的背包")
    check("背包 outcome", r.get("outcome") == "bag", r)

    # 7. 帮助/未知
    r = await game.handle_action(uid, "随便什么")
    check("help outcome", r.get("outcome") == "help", r)

    print()
    print(f"PASSED {len(passed)} | FAILED {len(failed)}")
    assert not failed, f"{len(failed)} 项失败: {failed}"


def test_cat_evolution_e2e() -> None:
    asyncio.run(main())


if __name__ == "__main__":
    asyncio.run(main())
