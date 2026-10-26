"""基地系统（v0.5，基地与补给系统）。

猫娘在出生点附近建立基地：记录出生点 + 找/放箱子作为储物，背包满了回家存、
补给后再出发。让猫娘"会过日子"，像真人玩家一样有家可回。

- base 位置：世界出生点（get_spawn）或首次入服位置
- base 箱子：出生点附近最近的箱子；没有就放一个
- store_surplus：把背包里非必需物品存进基地箱（保留手持/装备/工具/药水）
- resupply：回家 → 存多余 → 补给基础物资
- go_home：用魔镜回出生点（合法物品），或导航回去
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# 基地扫描半径（tile）
BASE_SCAN_RANGE = 60
# 背包满阈值：空位少于这个数就回家存
RESUPPLY_EMPTY_SLOTS = 5
# 补给间隔（秒）
RESUPPLY_COOLDOWN = 120.0

# 保留在身上的物品（不存进基地箱）
# 手持/装备由 inventory 结构区分；这里额外保留功能物品
_KEEP_NAMES = {
    "火把", "魔镜", "冰雪镜", "抓钩", "钩爪", "回忆药水",
    "铁镐", "铜镐", "银镐", "金镐", "钨镐", "钯金镐", "钴镐",
    "铁斧", "铜斧", "银斧", "金斧",
    "治疗药水", "治疗药水II", "治疗药水III", "魔力药水",
}
_KEEP_IDS = {50, 3199}  # 魔镜与冰雪镜，即使 Mod 返回英文物品名也必须保留


class BaseManager:
    def __init__(self, agent) -> None:
        self.agent = agent
        self.base_pos: Optional[Tuple[int, int]] = None
        self.base_chests: List[Dict[str, Any]] = []
        self._last_resupply = 0.0
        self._handling_full = False
        self.last_full_result: Dict[str, Any] = {
            "stored": 0, "returned_to_owner": False, "ok": False,
        }

    # ---------------- 初始化 ----------------

    async def init_base(self) -> bool:
        """初始化基地：确定出生点 + 扫描/放置基地箱。"""
        try:
            spawn = await self.agent.mod.get_spawn()
            if spawn:
                self.base_pos = spawn
            else:
                # 兜底：用当前玩家位置
                st = self.agent.get_state()
                self.base_pos = (int(st.get("tile_x", 0) or 0),
                                 int(st.get("tile_y", 0) or 0))
        except Exception:
            st = self.agent.get_state()
            self.base_pos = (int(st.get("tile_x", 0) or 0),
                             int(st.get("tile_y", 0) or 0))

        if not self.base_pos or self.base_pos == (0, 0):
            return False

        # 扫描出生点附近的箱子
        await self._refresh_base_chests()
        # 没有箱子就放一个（出生点脚边）
        if not self.base_chests:
            bx, by = self.base_pos
            try:
                ok = await self.agent.mod.place_chest(bx, by + 1)
                if ok:
                    await asyncio.sleep(0.3)
                    await self._refresh_base_chests()
            except Exception:
                pass

        self.agent.log(f"基地就绪: 位置{self.base_pos}, 箱子{len(self.base_chests)}个", "base")
        return True

    async def _refresh_base_chests(self) -> None:
        """扫描基地附近的箱子。"""
        if not self.base_pos:
            return
        bx, by = self.base_pos
        try:
            chests = await self.agent.mod.enum_chests()
        except Exception:
            chests = []
        self.base_chests = []
        for c in chests or []:
            if not isinstance(c, dict):
                continue
            cx = int(c.get("x", 0) or 0)
            cy = int(c.get("y", 0) or 0)
            if abs(cx - bx) <= BASE_SCAN_RANGE and abs(cy - by) <= BASE_SCAN_RANGE:
                self.base_chests.append(c)

    # ---------------- 回家 ----------------

    async def go_home(self) -> bool:
        """回基地：优先魔镜（合法瞬移），失败则导航。"""
        if not self.base_pos:
            return False
        try:
            if await self.agent.mod.use_mirror():
                await asyncio.sleep(1.0)
                return True
        except Exception:
            pass
        # 魔镜失败 → 导航回出生点
        bx, by = self.base_pos
        try:
            return await self.agent.navigate_to(bx, by, timeout=25)
        except Exception:
            return False

    # ---------------- 存储 ----------------

    async def store_surplus(self) -> int:
        """把背包里非必需物品存进基地箱。返回存入的物品种数。"""
        # 基地逻辑不能依赖几分钟前的缓存；存储前必须读取一次真实背包。
        try:
            await self.agent.refresh_inventory()
        except Exception as exc:
            self.agent.log(f"刷新背包失败，暂不存箱: {exc}", "warn")
            return 0
        if not self.base_chests:
            await self._refresh_base_chests()
        if not self.base_chests:
            bx, by = self.base_pos or (0, 0)
            try:
                if bx and by and await self.agent.mod.place_chest(bx, by + 1):
                    await asyncio.sleep(0.3)
                    await self._refresh_base_chests()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.agent.log(f"基地没有可用箱子，放置箱子失败: {exc}", "warn")
            if not self.base_chests:
                return 0
        inv = self.agent.get_inventory_sync()
        # ACK 只代表 Mod 接受了命令；最后重新读背包，以槽位堆叠实际减少
        # 作为成功证据，避免箱子满/旧回执造成“假存箱完成”。
        candidates = ((inv.get("inventory", []) or [])
                      + (inv.get("hotbar", []) or []))
        before_stack = {}
        for it in candidates:
            if isinstance(it, dict) and it.get("inv_slot") is not None:
                before_stack[int(it["inv_slot"])] = int(it.get("stack", 0) or 0)
        attempted_slots = set()
        # inventory = 主背包（hotbar 是手持栏，equipped 是装备）
        for it in candidates:
            if not isinstance(it, dict):
                continue
            name = str(it.get("name", "") or "")
            iid = int(it.get("id", -1) or -1)
            slot = it.get("inv_slot")
            if slot is None:
                continue
            if iid <= 0:
                continue
            # 保留功能物品
            if iid in _KEEP_IDS or name in _KEEP_NAMES:
                continue
            # 有防御/伤害/工具属性的保留（防具武器）
            if int(it.get("defense", 0) or 0) > 0:
                continue
            if int(it.get("damage", 0) or 0) > 0:
                continue
            if int(it.get("pick", 0) or 0) > 0:
                continue
            if int(it.get("axe", 0) or 0) > 0:
                continue
            if (int(it.get("accessory", 0) or 0) > 0
                    or int(it.get("ammo", 0) or 0) > 0
                    or int(it.get("healLife", 0) or 0) > 0
                    or int(it.get("healMana", 0) or 0) > 0
                    or int(it.get("fishing_pole", 0) or 0) > 0):
                continue
            # 存进基地箱（全量）
            stored_ok = False
            # 一个箱子满了时继续尝试基地内的其他箱子，不能把“第一个箱子
            # 放不下”误报成整个基地存储失败。
            for target_chest in self.base_chests:
                try:
                    state = self.agent.get_state() or {}
                    px = int(state.get("tile_x", 0) or 0)
                    py = int(state.get("tile_y", 0) or 0)
                    cx = int(target_chest.get("x", 0) or 0)
                    cy = int(target_chest.get("y", 0) or 0)
                    if abs(px - cx) > 4 or abs(py - cy) > 4:
                        if not await self.agent.navigate_to(cx, cy, timeout=15):
                            continue
                    if await self.agent.mod.store_item(
                            cx, cy, slot,
                            int(it.get("stack", 1) or 1)):
                        stored_ok = True
                        break
                except Exception:
                    continue
            if stored_ok:
                attempted_slots.add(int(slot))
        try:
            after = await self.agent.refresh_inventory()
        except Exception as exc:
            self.agent.log(f"存箱后刷新背包失败: {exc}", "warn")
            return 0
        after_stack = {
            int(it["inv_slot"]): int(it.get("stack", 0) or 0)
            for it in ((after.get("inventory", []) or [])
                       + (after.get("hotbar", []) or []))
            if isinstance(it, dict) and it.get("inv_slot") is not None
        }
        stored = sum(1 for slot in attempted_slots
                     if after_stack.get(slot, 0) < before_stack.get(slot, 0))
        if stored:
            self.agent.log(f"存了 {stored} 种物品进基地箱", "base")
        return stored

    # ---------------- 补给循环 ----------------

    def inventory_nearly_full(self, threshold: int = RESUPPLY_EMPTY_SLOTS) -> bool:
        """背包空位是否少于阈值。"""
        inv = self.agent.get_inventory_sync()
        inv_count = len(inv.get("inventory", []) or [])
        hotbar_count = len(inv.get("hotbar", []) or [])
        # ModLink 将 Player.inventory 的前 10 格归入 hotbar，其余归入
        # inventory；槽位总数由 Mod 回传，默认兼容 50 格接口。
        total = int(inv.get("slot_count", 50) or 50)
        # 第三方 Mod 可能省略 slot_count；已返回的槽位编号仍可提供更
        # 可靠的下限，但不能把“空的尾部槽位”误当成不存在。
        if total < 50:
            total = 50
        empty = total - (inv_count + hotbar_count)
        return empty < threshold

    async def resupply(self) -> bool:
        """补给循环：背包满 → 回家 → 存多余 → 刷新。"""
        now = time.time()
        if self._handling_full or now - self._last_resupply < RESUPPLY_COOLDOWN:
            return False
        try:
            await self.agent.refresh_inventory()
        except Exception:
            return False
        if not self.inventory_nearly_full():
            return False
        return await self.handle_inventory_full("背包空间不足，继续行动前需要整理物品")

    async def handle_inventory_full(self, reason: str = "背包已满",
                                    return_to_owner: bool = True) -> bool:
        """处理真实背包满：汇报 → 回基地 → 存箱 → 刷新 → 返回主人。"""
        if self._handling_full:
            return False
        self._handling_full = True
        self._last_resupply = time.time()
        self.last_full_result = {
            "stored": 0, "returned_to_owner": False, "ok": False,
            "reason": reason,
        }
        try:
            owner_before = self._owner_position() if return_to_owner else None
            self.agent.log(f"{reason}，准备回基地存箱", "base")
            # 让宿主 LLM 生成符合当前人格的陪玩话语，同时把事件送入交互引擎。
            brain = getattr(getattr(self.agent, "plugin", None), "_autonomous_brain", None)
            interaction = getattr(brain, "interaction", None) if brain else None
            if interaction:
                await interaction.inject_event(
                    "inventory_full", intensity=0.8, description=reason,
                    data={"reason": reason})
            await self.agent.speak(
                f"[背包状态] {reason}。我现在要回基地找箱子整理，整理完成后再继续。"
                "请用猫娘语气告诉主人我背包满了、正在回去存东西，不要说已经存好。",
                ai_behavior="respond")

            if self.base_pos is None and not await self.init_base():
                await self.agent.speak(
                    "[背包状态] 无法确认基地位置，也没有开始存箱。"
                    "请如实告诉主人我需要主人指路，不要说整理完成。",
                    ai_behavior="respond")
                return False
            if not await self.go_home():
                await self.agent.speak(
                    "[背包状态] 回基地失败，物品还没有存入箱子。"
                    "请如实提醒主人我暂时无法继续深入。",
                    ai_behavior="respond")
                return False
            if self.agent.executor and self.agent.executor.should_stop():
                return False
            try:
                await self.agent.refresh_state()
            except Exception:
                pass
            await asyncio.sleep(0.5)
            stored = await self.store_surplus()
            self.last_full_result["stored"] = stored
            try:
                await self.agent.refresh_inventory()
            except Exception:
                pass
            if stored <= 0:
                await self.agent.speak(
                    "[背包状态] 已回到基地，但没有确认任何物品成功存入箱子。"
                    "请如实告诉主人整理未完成，不要说背包已清空。",
                    ai_behavior="respond")
                return False

            if return_to_owner:
                try:
                    await self.agent.refresh_state()
                except Exception:
                    pass
                owner = self._owner_position() or owner_before
                if owner is not None:
                    if not await self.agent.navigate_to(*owner, timeout=30):
                        await self.agent.speak(
                            f"[背包状态] 已确认存入 {stored} 种物品，但返回主人失败。"
                            "请告诉主人我在基地附近等待，不要自动重派任务。",
                            ai_behavior="respond")
                        return False
                else:
                    await self.agent.speak(
                        f"[背包状态] 已确认存入 {stored} 种物品，但附近暂时找不到主人。"
                        "请告诉主人我先在基地等候，不要说已经回到主人身边。",
                        ai_behavior="respond")
                    return False
                self.last_full_result["returned_to_owner"] = True
            else:
                self.last_full_result["returned_to_owner"] = True
            self.last_full_result["ok"] = True
            await self.agent.speak(
                f"[背包状态] 已确认存入 {stored} 种物品，背包整理完成。"
                "请用猫娘语气告诉主人我已经整理好，后续可以继续行动。",
                ai_behavior="respond")
            return True
        finally:
            self._handling_full = False

    def _owner_position(self) -> Optional[Tuple[int, int]]:
        """选择最近的有效主人坐标，过滤自身和空槽位。"""
        state = self.agent.get_state() or {}
        me_x = int(state.get("tile_x", 0) or 0)
        me_y = int(state.get("tile_y", 0) or 0)
        try:
            my_name = self.agent._character_name()
        except Exception:
            my_name = ""
        best = None
        best_dist = float("inf")
        for player in state.get("nearby_players", []) or []:
            if not isinstance(player, dict) or (my_name and player.get("name") == my_name):
                continue
            x = int(player.get("tile_x", player.get("tileX", 0)) or 0)
            y = int(player.get("tile_y", player.get("tileY", 0)) or 0)
            if x == 0 and y == 0:
                continue
            dist = (x - me_x) ** 2 + (y - me_y) ** 2
            if dist < best_dist:
                best_dist = dist
                best = (x, y)
        return best

    # ---------------- 状态 ----------------

    def status(self) -> Dict[str, Any]:
        return {
            "base_pos": list(self.base_pos) if self.base_pos else None,
            "chests": len(self.base_chests),
            "inventory_full": self.inventory_nearly_full(),
        }
