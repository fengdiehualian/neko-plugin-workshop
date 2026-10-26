"""地下探索闭环（v0.5，地下探索系统）。

找洞口 → 下挖进入 → 探索（挖矿/开箱/打怪/捡掉落）→ 记 visited → 回家。

相比 task_chain._explore 的"向下挖 25 格"，这里是完整的地下探险：
- 周期性下挖（挖到 1 格净深算前进）
- 发现矿就挖（背包计数确认）
- 记录已探索的 chunk，避免原地打转
- 血量低/背包满/超时 → 回家
"""

from __future__ import annotations

import asyncio
import logging
import time

logger = logging.getLogger(__name__)

# 探索参数
EXPLORE_MAX_TIME = 180.0       # 单次探索时长上限（秒）
DIG_DEPTH_PER_ROUND = 3         # 每轮下挖格数（脚下方块）
EXPLORE_MIN_DEPTH = 30          # 至少下挖多少格才算"去过地下"（净深）
VISITED_CHUNK = 16              # visited 分块大小（格）
MAX_STUCK_ROUNDS = 4            # 连续无进展轮数上限
COLLECT_RADIUS = 600            # 捡掉落半径（像素）

# 值得挖的矿石（按优先级）
ORE_PRIORITY = ["金矿", "银矿", "铁矿", "铜矿", "锡矿", "钨矿", "铂金矿"]


class UndergroundExplorer:
    def __init__(self, agent) -> None:
        self.agent = agent
        self._visited: set = set()
        self._start_y = 0
        self._start_time = 0.0
        self.last_result = ""

    # ---------------- 主入口 ----------------

    async def explore(self, direction: int = 1, max_time: float = EXPLORE_MAX_TIME) -> bool:
        """执行一次地下探索。返回是否挖到了东西/下到了深处。"""
        self.last_result = ""
        st = self.agent.get_state()
        self._start_y = int(st.get("tile_y", 0) or 0)
        self._start_time = time.time()
        self._visited = set()

        self.agent.log(f"地下探索开始（方向{'右' if direction > 0 else '左'}，限时{int(max_time)}s）", "nav")

        # 确保有镐
        try:
            await self.agent.capability.refresh()
            if not self.agent.capability.has_pickaxe():
                self.agent.log("没有镐子，下不了地~", "warn")
                return False
        except Exception:
            pass

        # 探索前确保有火把/照明（避免摸黑）
        await self._ensure_light()

        max_depth_reached = 0
        mined_total = 0
        stuck = 0
        collected_total = 0
        ore_n = 0

        while time.time() - self._start_time < max_time:
            if self.agent.executor and self.agent.executor.should_stop():
                break

            st = await self.agent.refresh_state()
            mx = int(st.get("tile_x", 0) or 0)
            my = int(st.get("tile_y", 0) or 0)
            depth = my - self._start_y
            max_depth_reached = max(max_depth_reached, depth)

            # 自保：血量低 → 回家
            hp = int(st.get("hp", 0) or 0)
            max_hp = int(st.get("max_life", 100) or 100) or 100
            if max_hp > 0 and hp / max_hp < 0.3:
                self.agent.log("血低了，先回家恢复~", "warn")
                await self._go_home()
                break

            # 背包满 → 回家存
            base = getattr(self.agent, "base", None)
            if base and base.inventory_nearly_full():
                self.agent.log("背包满了，回家存一下~", "base")
                await base.handle_inventory_full("地下探索后背包空间不足")
                break

            # 本 chunk 已探索 → 换个方向
            chunk = (mx // VISITED_CHUNK, my // VISITED_CHUNK)
            if chunk in self._visited:
                stuck += 1
                if stuck > MAX_STUCK_ROUNDS:
                    self.agent.log("探索到重复区域了，往回走走~", "nav")
                    await self._go_home()
                    break
            else:
                self._visited.add(chunk)
                stuck = 0

            # 优先挖附近的矿（探索 = 挖矿动力）
            mined = await self._try_mine_ore(mx, my)
            if mined:
                mined_total += mined
                ore_n += 1

            # 下挖前进
            dug = await self._dig_down(mx, my)
            if dug <= 0:
                # 挖不动 → 尝试横向找路（导航到更深处）
                target_y = my + 15
                try:
                    ok = await self.agent.navigate_to(mx, target_y, timeout=10)
                    if not ok:
                        stuck += 1
                        if stuck > MAX_STUCK_ROUNDS:
                            break
                except Exception:
                    stuck += 1
            else:
                stuck = 0

            # 捡掉落物
            try:
                collected = await self.agent.mod.collect_items(radius=COLLECT_RADIUS)
                collected_total += collected
            except Exception:
                pass

            await asyncio.sleep(0.5)

        self.agent.log(
            f"地下探索结束：下挖{max_depth_reached}格，挖到{ore_n}次矿，捡了{collected_total}个掉落",
            "nav")
        self.last_result = f"最深处比起点低 {max_depth_reached} 格，确认采矿 {mined_total} 个"
        # 地表捡到一块矿不能作为“地下探索完成”的证据。
        return max_depth_reached >= EXPLORE_MIN_DEPTH

    # ---------------- 下挖 ----------------

    async def _dig_down(self, mx: int, my: int) -> int:
        """向下挖 DIG_DEPTH_PER_ROUND 格（挖脚下方块，break_tile 自动切镐）。"""
        dug = 0
        for dy in range(1, DIG_DEPTH_PER_ROUND + 1):
            try:
                if await self.agent.mod.break_tile(mx, my + dy):
                    dug += 1
                else:
                    break
            except Exception:
                break
            await asyncio.sleep(0.15)
        return dug

    # ---------------- 挖矿 ----------------

    async def _try_mine_ore(self, mx: int, my: int) -> int:
        """扫描附近矿并挖 1-2 块（背包计数确认）。"""
        mined = 0
        for ore in ORE_PRIORITY:
            iid = self.agent.resolve_item(ore)
            if iid < 0:
                continue
            from .item_npc_dict import tile_type_of
            try:
                ores = await self.agent.mod.find_ore(radius=30, tile_type=tile_type_of(ore, iid, getattr(self.agent, "registry", None)))
            except Exception:
                ores = []
            if not ores:
                continue
            target = ores[0]
            d = abs(int(target.get("x", 0) or 0) - mx) + abs(int(target.get("y", 0) or 0) - my)
            if d > 20:
                continue
            try:
                got = await self.agent.mining.mine_ore_inplace(ore, target["x"], target["y"])
            except Exception:
                got = 0
            mined += got or 0
            if mined:
                break
        return mined

    # ---------------- 照明 ----------------

    async def _ensure_light(self) -> None:
        """确保背包有火把；没有就合成/生成。"""
        try:
            iid = self.agent.resolve_item("火把")
            if iid < 0:
                return
            inv = self.agent.get_inventory_sync()
            has = any(it.get("id") == iid for it in inv.get("inventory", []) or [])
            if not has:
                # 用 give_item 兜底（AI 客户端工具物品，属于合法补给）
                await self.agent.mod.give_item(iid, 20)
        except Exception:
            pass

    # ---------------- 回家 ----------------

    async def _go_home(self) -> None:
        """回基地（魔镜优先，失败导航）。"""
        base = getattr(self.agent, "base", None)
        if base:
            await base.go_home()
        else:
            try:
                await self.agent.mod.use_mirror()
            except Exception:
                pass
