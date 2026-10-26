"""E2E 桩测试: 俄罗斯轮盘(猫粮版) — 真实加载 game.py + 桩存档。

以 pytest 运行(本地自动经 tests/conftest.py 建链, CI 里插件已 mount)。
"""
import asyncio
import json
import time
from pathlib import Path

from plugin.plugins.neko_arcade.games.russian.game import RussianGame

ROOT = Path(__file__).resolve().parent.parent
CFG = json.loads((ROOT / "data" / "config" / "russian" / "config.json").read_text(encoding="utf-8"))
EMO = json.loads((ROOT / "data" / "config" / "russian" / "emotion.json").read_text(encoding="utf-8"))
HELP = json.loads((ROOT / "data" / "config" / "russian" / "help.json").read_text(encoding="utf-8"))
KWS = json.loads((ROOT / "data" / "config" / "russian" / "keywords.json").read_text(encoding="utf-8"))


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

    async def text(self, text):
        self._sink.append(text)


class StubPlugin:
    def __init__(self):
        self.store = StubStore()
        self.pushes = []

    async def store_get_user(self, gid, uid, default=None):
        return await self.store.store_get_user(gid, uid, default)

    async def store_save_user(self, gid, uid, data):
        await self.store.store_save_user(gid, uid, data)

    async def push_text(self, text):
        self.pushes.append(text)


async def main():
    plugin = StubPlugin()
    game = RussianGame(plugin)
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

    # 1. 签到
    r = await game.handle_action(uid, "轮盘签到")
    check("签到 outcome=sign", r.get("outcome") == "sign", r)
    check("签到得猫粮", 1 <= r["facts"][0]["food"] <= 100, r)
    food_after_sign = game._cache[uid]["food"]
    check("签到后猫粮>0", food_after_sign > 0, food_after_sign)

    # 2. 重复签到被拒
    r = await game.handle_action(uid, "轮盘签到")
    check("重复签到被拒", r.get("outcome") == "already_sign", r)

    # 3. 没装弹就开枪
    r = await game.handle_action(uid, "开枪")
    check("无对局开枪=no_duel", r.get("outcome") == "no_duel", r)

    # 4. 装弹(默认 1 弹 200 猫粮)
    r = await game.handle_action(uid, "装弹")
    check("装弹 outcome=duel_start", r.get("outcome") == "duel_start", r)
    check("装弹扣猫粮", game._cache[uid]["food"] == food_after_sign - 200,
          (food_after_sign, game._cache[uid]["food"]))
    check("装弹默认1弹", game._cache[uid]["duel"]["bullet_num"] == 1, game._cache[uid]["duel"])
    check("装弹默认200", game._cache[uid]["duel"]["money"] == 200, game._cache[uid]["duel"])

    # 5. 对局中再装弹被拒
    r = await game.handle_action(uid, "装弹")
    check("对局中装弹=duel_active", r.get("outcome") == "duel_active", r)

    # 6. 开枪直到分出胜负(最多 14 枪, 1 弹必中)
    outcome_seen = None
    shots = 0
    for _ in range(14):
        r = await game.handle_action(uid, "开枪")
        shots += 1
        if r.get("outcome") in ("win", "lose", "draw"):
            outcome_seen = r.get("outcome")
            break
        check("空枪继续", r.get("outcome") == "fire", r)
    check(f"1弹对局分出胜负({outcome_seen}, {shots}枪)", outcome_seen in ("win", "lose"), outcome_seen)
    check("对局结束清除duel", game._cache[uid]["duel"] is None, game._cache[uid]["duel"])
    if outcome_seen == "win":
        check("胜场+1", game._cache[uid]["win_count"] == 1, game._cache[uid])
        check("赢猫粮=200-抽水", game._cache[uid]["make_food"] == 200 - r["facts"][-1]["fee"], r)
    else:
        check("败场+1", game._cache[uid]["lose_count"] == 1, game._cache[uid])
        check("输猫粮+200", game._cache[uid]["lose_food"] == 200, game._cache[uid])

    # 7. 我的猫粮 / 我的战绩
    r = await game.handle_action(uid, "我的猫粮")
    check("我的猫粮", r.get("outcome") == "food" and r["facts"][0]["food"] == game._cache[uid]["food"], r)
    r = await game.handle_action(uid, "我的战绩")
    check("我的战绩", r.get("outcome") == "record", r)

    # 8. 排行榜
    r = await game.handle_action(uid, "猫粮排行")
    check("猫粮排行", r.get("outcome") == "rank" and "猫粮排行榜" in r["message"], r)
    r = await game.handle_action(uid, "胜场排行")
    check("胜场排行", r.get("outcome") == "rank" and "胜场排行榜" in r["message"], r)
    r = await game.handle_action(uid, "欧皇排行")
    check("欧皇排行", r.get("outcome") == "rank" and "赢取猫粮" in r["message"], r)
    r = await game.handle_action(uid, "慈善家排行")
    check("慈善家排行", r.get("outcome") == "rank" and "输掉猫粮" in r["message"], r)

    # 9. 多弹对局(装弹 3 500) — 先补足猫粮
    game._cache[uid]["food"] += 1000
    r = await game.handle_action(uid, "装弹 3 500")
    check("装弹3弹500", r.get("outcome") == "duel_start"
          and game._cache[uid]["duel"]["bullet_num"] == 3
          and game._cache[uid]["duel"]["money"] == 500, r)
    r = await game.handle_action(uid, "开枪 2")
    if r.get("outcome") == "fire":
        check("连开2枪后继续", True)
    else:
        check("连开2枪内出胜负", r.get("outcome") in ("win", "lose"))

    # 10. 认输
    if game._cache[uid]["duel"]:
        r = await game.handle_action(uid, "逃跑")
        check("逃跑 outcome=surrender", r.get("outcome") == "surrender", r)
        check("逃跑输掉赌注", game._cache[uid]["lose_food"] >= 500, game._cache[uid]["lose_food"])
        check("逃跑后无对局", game._cache[uid]["duel"] is None)

    # 11. unknown 契约
    r = await game.handle_action(uid, "随便说点什么")
    check("unknown 契约", r.get("outcome") == "unknown", r)

    # 12. 猫粮不足
    poor = "user_poor"
    r = await game.handle_action(poor, "装弹 1 5000")
    check("猫粮不足=no_food", r.get("outcome") == "no_food", r)

    # 13. 超时自动取消
    t = "user_timeout"
    await game.handle_action(t, "轮盘签到")
    await game.handle_action(t, "装弹")
    game._cache[t]["duel"]["started_at"] = time.time() - 9999
    said = await game.on_tick(t)
    check("超时退回赌注", game._cache[t]["duel"] is None and game._cache[t]["food"] > 0,
          (game._cache[t]["duel"], game._cache[t]["food"]))
    check("超时提醒(on_tick 返回, 推送归本体)",
          any("超时" in p for p in plugin.pushes) or (isinstance(said, str) and "超时" in said),
          (said, plugin.pushes))

    # 14. 关键词覆盖
    kw_need = ["轮盘签到", "装弹", "开枪", "逃跑", "我的猫粮", "我的战绩",
               "猫粮排行", "胜场排行", "败场排行", "欧皇排行", "慈善家排行"]
    for kw in kw_need:
        assert kw in KWS, f"keywords.json 缺 {kw}"
    check("keywords 完整", True)

    print()
    print(f"PASSED {len(passed)} | FAILED {len(failed)}")
    assert not failed, f"{len(failed)} 项失败: {failed}"


def test_russian_e2e() -> None:
    """pytest 入口: 俄罗斯轮盘全流程 E2E。"""
    asyncio.run(main())


if __name__ == "__main__":
    asyncio.run(main())
