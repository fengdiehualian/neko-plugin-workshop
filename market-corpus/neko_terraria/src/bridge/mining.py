"""挖矿引擎：导航到矿点、循环挖掘、可被中断切换目标。"""

import asyncio
from typing import Any, Dict, Optional

from .item_npc_dict import item_id
from .mod_link import ModLink


class MiningEngine:
    def __init__(self, mod: ModLink, agent=None) -> None:
        self.mod = mod
        self.agent = agent
        self._cancel = asyncio.Event()
        self.last_resupply_ok: Optional[bool] = None

    def _stopped(self) -> bool:
        if self._cancel.is_set():
            return True
        ex = getattr(self.agent, "executor", None) if self.agent else None
        return bool(ex and ex.should_stop())

    def cancel(self) -> None:
        self._cancel.set()

    def reset(self) -> None:
        self._cancel.clear()

    # ---------------- 含自动导航的挖掘（v0.8+） ----------------

    async def mine_target(self, target_item: str, amount: int = 10, state: Optional[Dict[str, Any]] = None) -> tuple:
        """挖掘目标矿物（含导航到矿）。

        Returns:
            (物品id, 挖到数量)  — 计数以背包真实增量确认；
            挖到 amount 个或目标矿不可达便返回，杜绝假完成。
        """
        if self.agent is None:
            return -1, 0
        self.last_resupply_ok = None
        iid = item_id(target_item, getattr(self.agent, "registry", None))
        if iid <= 0:
            return iid, 0
        mined = 0
        no_gain = 0
        # 初始背包基数
        try:
            self.agent._inv_full = await self.mod.get_inventory()
            last_count = self._count_item(self.agent.get_inventory_sync(), iid)
        except Exception:
            last_count = -1
        while mined < amount:
            if self._stopped():
                self._cancel.clear()
                return iid, mined
            if state is None:
                try:
                    state = self.agent.get_state()
                except Exception:
                    pass
            mine = await self._find_target(state, iid, target_item)
            if mine is None:
                return iid, mined  # 附近没有目标矿，诚实返回
            tx, ty = int(mine.get("x", 0)), int(mine.get("y", 0))
            # 到矿点后挖 1 下（省导航，mine_ore_inplace 完成导航 + 挖 + 计数）
            got = await self.mine_ore_inplace(target_item, tx, ty)
            if got:
                mined += got
                last_count += got
                no_gain = 0
                if not state:
                    try:
                        state = self.agent.get_state()
                    except Exception:
                        pass
                try:
                    await self._notify_mining(target_item, got)
                except Exception:
                    pass
                # 采集后的真实背包可能已经没有空位。回基地存箱是采集流程
                # 的一部分，不能等到下一轮空闲心跳才处理，否则会继续挖掘失败。
                handled = await self._maybe_resupply()
                if handled is False:
                    break
            else:
                no_gain += 1
                if no_gain >= 3:
                    break  # 连续挖不到：换下一个矿或直接返回
        return iid, mined

    async def _maybe_resupply(self) -> Optional[bool]:
        base = getattr(self.agent, "base", None)
        if base is None:
            return None
        try:
            if base.inventory_nearly_full():
                self.last_resupply_ok = await base.handle_inventory_full(
                    "挖矿后背包空间不足")
                return self.last_resupply_ok
            return None
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.agent.log(f"挖矿后整理背包失败: {exc}", "warn")
        return False

    async def _find_target(self, state: Optional[Dict[str, Any]],
                           iid: int, target_item: str = "") -> Optional[Dict[str, Any]]:
        """找最近可挖矿点（含距离过滤）。

        #14: tile_type 必须用 TileID（矿石方块类型），不是物品 ID——
        C# find_ore 拿 tile_type 与 Main.tile[].TileType 比较（铁矿 TileID=7 ≠ ItemID=11）。
        """
        from .item_npc_dict import tile_type_of
        tile = tile_type_of(target_item, iid, getattr(self.agent, "registry", None))
        try:
            ores = await self.mod.find_ore(radius=30, tile_type=tile)
        except Exception:
            ores = []
        if not ores:
            return None
        st = state or {}
        mx = int(st.get("tile_x", 0) or 0)
        my = int(st.get("tile_y", 0) or 0)
        for o in ores:
            d = abs(int(o.get("x", 0)) - mx) + abs(int(o.get("y", 0)) - my)
            if d <= 30:
                return o
        return None

    async def mine_ore_inplace(self, ore: str, tx: int, ty: int) -> int:
        """原地挖一格矿：导航到位 → dig_tile → 背包计数确认。

        用于空闲挖矿（idle）等不重复 nav 的场景。返回挖到的数量（0 = 没挖到）。
        """
        iid = item_id(ore, getattr(self.agent, "registry", None))
        if iid <= 0:
            return 0
        # 未取得基数不能把已有存货计为新挖到的；让调用方报告读取失败。
        self.agent._inv_full = await self.mod.get_inventory()
        before = self._count_item(self.agent.get_inventory_sync(), iid)
        if not await self.agent.navigate_to(tx, ty, timeout=15):
            return 0
        if self._stopped() or not await self.agent.life.select_tool("pick"):
            return 0
        try:
            ok = await self.mod.dig_tile(tx, ty)
        except Exception:
            ok = False
        if not ok:
            return 0
        await asyncio.sleep(0.8)
        await self.mod.collect_items(radius=400)
        self.agent._inv_full = await self.mod.get_inventory()
        after = self._count_item(self.agent.get_inventory_sync(), iid)
        return max(0, after - before)

    async def _notify_mining(self, target_item: str, got: int) -> None:
        try:
            exe = getattr(self.agent, "executor", None)
            if exe:
                await exe.notify(
                    "step_done", kind="mine", desc=f"挖到 {got} 个 {target_item}", item=target_item, got=got
                )
        except Exception:
            pass

    @staticmethod
    def _count_item(inv: Dict[str, Any], iid: int) -> int:
        """统计背包中某物品 id 的总数（hotbar+equipped+inventory）。"""
        total = 0
        for slot in ("hotbar", "equipped", "inventory"):
            for it in (inv or {}).get(slot, []) or []:
                if it.get("id") == iid:
                    total += int(it.get("stack", 1) or 1)
        return total
