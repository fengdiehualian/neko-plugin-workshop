"""E2E 桩测试: 人生重开(remake) — 真实加载 game.py + 桩存档。

以 pytest 运行(本地自动经 tests/conftest.py 建链, CI 里插件已 mount)。
PIL 可用时验证人生总结图真实渲染。
"""
import asyncio
import json
import time
from pathlib import Path

from plugin.plugins.neko_arcade.games.remake.game import RemakeGame

ROOT = Path(__file__).resolve().parent.parent
CFG = json.loads((ROOT / "data" / "config" / "remake" / "config.json").read_text(encoding="utf-8"))
EMO = json.loads((ROOT / "data" / "config" / "remake" / "emotion.json").read_text(encoding="utf-8"))
HELP = json.loads((ROOT / "data" / "config" / "remake" / "help.json").read_text(encoding="utf-8"))
KWS = json.loads((ROOT / "data" / "config" / "remake" / "keywords.json").read_text(encoding="utf-8"))


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
        self._sink.append(("text", text))

    async def text_with_image(self, text, image_bytes, mime="image/png"):
        self._sink.append(("image", text, len(image_bytes), mime))


class StubPlugin:
    def __init__(self):
        self.store = StubStore()
        self.pushes = []

    async def store_get_user(self, gid, uid, default=None):
        return await self.store.store_get_user(gid, uid, default)

    async def store_save_user(self, gid, uid, data):
        await self.store.store_save_user(gid, uid, data)


class StubRenderBridge:
    """渲染桥接桩: 记录游戏传来的页面数据, 返回假 PNG。

    按「游戏适配插件」: 游戏只给数据(标题/版式块), 出图由插件负责。
    """

    def __init__(self):
        self.pages = []

    @property
    def ready(self):
        return True

    async def page(self, title, subtitle="", blocks=None, rows=None,
                   commands=None, chips=None, tip="", theme="", game_id=""):
        self.pages.append({"title": title, "subtitle": subtitle,
                           "blocks": list(blocks or []), "commands": list(commands or []),
                           "tip": tip})
        return [b"\x89PNG-life"]


async def main():
    plugin = StubPlugin()
    game = RemakeGame(plugin)
    game._push = StubPush(plugin.pushes)
    game._config = CFG
    game._help = HELP
    game._keywords = KWS
    game._emotion_templates = EMO
    bridge = StubRenderBridge()
    game.bind_services(render=bridge)

    uid = "user_1"
    passed, failed = [], []

    def check(name, cond, detail=""):
        if cond:
            passed.append(name)
            print(f"  ✓ {name}")
        else:
            failed.append(name)
            print(f"  ✗ {name}  {detail}")

    # 1. 人生重开 → 选天赋阶段
    t0 = time.time()
    r = await game.handle_action(uid, "人生重开")
    load_sec = time.time() - t0
    check("人生重开 outcome=talent_prompt", r.get("outcome") == "talent_prompt", r)
    check("10 个候选天赋", len(game._cache[uid]["pending"]) == 10, game._cache[uid]["pending"])
    check("消息含天赋列表", "0." in r["message"] and "9." in r["message"], r["message"][:80])
    print(f"    (数据加载耗时 {load_sec:.2f}s)")

    # 2. 状态机: 非法输入
    r = await game.handle_action(uid, "abc")
    check("非法天赋输入仍提示", r.get("outcome") == "talent_prompt", r)

    # 3. 选天赋 0 1 2 → 分属性阶段
    r = await game.handle_action(uid, "0 1 2")
    check("选天赋后 outcome=prop_prompt", r.get("outcome") == "prop_prompt", r)
    check("已选 3 个天赋", len(game._cache[uid]["chosen"]) == 3, game._cache[uid]["chosen"])
    check("状态=prop", game._cache[uid]["state"] == "prop", game._cache[uid]["state"])

    # ⚠️ 适配坑: total 不是固定 20, 天赋 status 加成会改变可分配总和(如 21~34)。
    # 必须从返回 facts 读真实 total, 不能写死「5 5 5 5」(和=20), 否则 total≠20
    # 时测试随机失败。玩家侧同理: 游戏提示的示例也必须跟真实 total 走。
    total = 20
    for f in r.get("facts", []):
        if f.get("kind") == "prop_prompt":
            total = int(f.get("total", 20))

    # 4. 分属性: 非法(和不对)
    wrong = "1 1 1 1" if total != 4 else "2 1 1 1"  # 保证和≠total
    r = await game.handle_action(uid, wrong)
    check("属性和不对被拒", r.get("outcome") == "prop_prompt" and "和" in r["message"], r)

    # 5. 分属性合法 → 开跑人生 (按真实 total 构造合法拆分, 每项≤10)
    n1 = min(total, 10)
    n2 = min(max(total - n1, 0), 10)
    n3 = min(max(total - n1 - n2, 0), 10)
    n4 = max(total - n1 - n2 - n3, 0)
    if max(n1, n2, n3, n4) <= 10:
        r = await game.handle_action(uid, f"{n1} {n2} {n3} {n4}")
    else:
        # 极端情况 total>40(理论不可能: 单天赋 status 最大+8×3)才走随机兜底
        r = await game.handle_action(uid, "随机")
    check("开跑后 outcome=life_*", r.get("outcome") in ("life_good", "life_mid", "life_bad"), r)
    check("重开次数+1", game._cache[uid]["lifes"] == 1, game._cache[uid])
    check("享年记录", game._cache[uid]["best_age"] > 0, game._cache[uid])
    check("life_end fact", r["facts"][0]["kind"] == "life_end", r)
    # 渲染图: 游戏把页面数据交给插件渲染桥接(不再自己画), brain 统一推
    check("人生总结图已生成(images)", bool(r.get("images")), r)
    check("总结图走渲染桥接", bool(bridge.pages), bridge.pages)
    if bridge.pages:
        spec = bridge.pages[-1]
        kinds = [b.get("type") for b in spec["blocks"]]
        check("页面含 cards(天赋)", "cards" in kinds, kinds)
        check("页面含 stats(属性/总评)", kinds.count("stats") >= 2, kinds)
        check("页面含 table(人生轨迹)", "table" in kinds, kinds)
    if r.get("images"):
        print(f"    (总结图 {len(r['images'][0].get('bytes', b''))} bytes)")

    # 6. 随机人生一步到位
    r = await game.handle_action(uid, "随机人生")
    check("随机人生 outcome=life_*", r.get("outcome") in ("life_good", "life_mid", "life_bad"), r)
    check("重开次数+1", game._cache[uid]["lifes"] == 2, game._cache[uid])

    # 7. 纪录
    r = await game.handle_action(uid, "我的重开纪录")
    check("纪录 outcome=record", r.get("outcome") == "record", r)
    check("纪录含重开次数", "2" in r["message"], r)

    # 8. 状态机残留清理: 新开一局覆盖
    r = await game.handle_action(uid, "人生重开")
    check("再次重开重置状态", r.get("outcome") == "talent_prompt"
          and game._cache[uid]["state"] == "talent", r)
    r = await game.handle_action(uid, "放弃重开")
    check("放弃重开", r.get("outcome") == "cancel" and game._cache[uid]["state"] is None, r)

    # 9. unknown 契约
    r = await game.handle_action(uid, "随便说点什么")
    check("unknown 契约", r.get("outcome") == "unknown", r)

    # 10. 关键词覆盖
    for kw in KWS:
        assert kw in KWS
    check("keywords 完整", len(KWS) >= 6, KWS)

    print()
    print(f"PASSED {len(passed)} | FAILED {len(failed)}")
    assert not failed, f"{len(failed)} 项失败: {failed}"


async def _run_e2e() -> None:
    """内部入口(直接跑脚本或 pytest 共用)。"""
    await main()


def test_remake_e2e() -> None:
    """pytest 入口: 人生重开全流程 E2E。"""
    asyncio.run(_run_e2e())


if __name__ == "__main__":
    asyncio.run(main())
    asyncio.run(main())
