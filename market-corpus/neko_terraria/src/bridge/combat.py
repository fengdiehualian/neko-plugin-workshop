"""战斗：走位拉扯、武器挥动与不可达目标退让，基于 mod 状态决策。
- 移动通过 mod.navigate_stream_fire 实现
- 通过真实武器挥动造成伤害

v0.11（A1）：战斗守卫不吞任务。fight_nearest 增加 yield 能力：
  check_task 存在时，一旦目标不可达/被隔墙/超时就返回 False（打不死就不打），
  由守卫方决定是否退回主线（不占前台任务槽）。
"""

import asyncio
import time
from typing import Any, Callable, Dict, Optional

from .mod_link import ModLink


class CombatEngine:
    def __init__(self, mod: ModLink, agent: Any = None) -> None:
        self.mod = mod
        self.agent = agent
        self._blacklist: Dict[tuple, float] = {}
        self._kill_confirmations: Dict[tuple, float] = {}
        self.blacklist_secs = 30
        self.no_dmg_timeout = 4
        # ── 风筝参数（按生存循环惯例 KITE_IDEAL_DIST/KITE_TOO_CLOSE） ──
        self.kite_ideal_dist = 3.0  # 理想距离（曼哈顿）：站桩打
        self.kite_too_close = 1.0  # 过近：后退
        self.max_height_gap = 10  # 超过此垂直差视为不可达（跳过）
        self.retreat_hp_ratio = 0.30  # 自身血量低于此比例 → 逃跑保命

    @staticmethod
    def _enemy_key(enemy: Dict[str, Any]) -> tuple:
        # NPC 槽位 0 有效。坐标会随移动变化，不能用坐标识别同一只敌人。
        return (enemy.get("slot", -1), enemy.get("type"), enemy.get("name", ""))

    @staticmethod
    def is_hostile(enemy: Dict[str, Any]) -> bool:
        return (not enemy.get("friendly", False)
                and not enemy.get("townNPC", enemy.get("town_npc", False))
                and int(enemy.get("damage", 0) or 0) > 0
                and int(enemy.get("life", 0) or 0) > 0)

    def _blacklist_enemy(self, enemy: Dict[str, Any]) -> None:
        self._blacklist[self._enemy_key(enemy)] = time.monotonic() + self.blacklist_secs

    def _is_blacklisted(self, enemy: Dict[str, Any]) -> bool:
        key = self._enemy_key(enemy)
        exp = self._blacklist.get(key, 0)
        if exp and time.monotonic() < exp:
            return True
        if key in self._blacklist:
            del self._blacklist[key]
        return False

    def confirm_npc_kill(self, event: Dict[str, Any]) -> None:
        """Record an authoritative Mod kill event keyed to the NPC instance."""
        try:
            slot = int(event.get("npc_slot", -1))
            npc_type = int(event.get("npc_type", -1))
        except (TypeError, ValueError):
            return
        if slot < 0 or npc_type < 0:
            return
        now = time.monotonic()
        self._kill_confirmations[(slot, npc_type)] = now
        self._kill_confirmations = {
            key: ts for key, ts in self._kill_confirmations.items()
            if now - ts <= 10.0
        }

    def _consume_kill_confirmation(self, enemy: Dict[str, Any]) -> bool:
        try:
            key = (int(enemy.get("slot", -1)), int(enemy.get("type", -1)))
        except (TypeError, ValueError):
            return False
        killed_at = self._kill_confirmations.pop(key, None)
        return killed_at is not None and time.monotonic() - killed_at <= 10.0

    async def fight_nearest(
        self, state: Dict[str, Any], timeout: int = 10, check_task: Optional[Callable[[], bool]] = None
    ) -> bool:
        """战斗最近的敌人（风筝走位 + 黑名单 + 隔墙判定 + 低血保命）。

        check_task: 外部提供的"是否有更重要的前台任务"谓词。
          为真 → 战斗中放弃（打不死就不打，不占任务槽，交给守卫方调度）。
          战斗优先但可让路。
        """
        if not state or state.get("alive") is False or int(state.get("hp", 0) or 0) <= 0:
            return False
        if self.agent is not None and getattr(self.agent, "_in_combat", False):
            return False
        px0 = int(state.get("tile_x", 0) or 0)
        py0 = int(state.get("tile_y", 0) or 0)
        target = self._pick_target(state, px0, py0)
        if not target:
            return False

        # 战斗中置标志：防止导航遇敌守卫自我嵌套（战斗内部的风筝移动也是 navigate_to）
        if self.agent is not None:
            self.agent._in_combat = True
        try:
            return await self._fight_loop(state, timeout, check_task,
                                          px0, py0, target)
        finally:
            try:
                await self.mod.stop_actions()
            except Exception:
                pass
            finally:
                if self.agent is not None:
                    self.agent._in_combat = False

    async def _fight_loop(self, state: Dict[str, Any], timeout: int,
                          check_task: Optional[Callable[[], bool]],
                          px0: int, py0: int,
                          target: Dict[str, Any]) -> bool:
        # 战斗前选好武器（近战/远程/魔法/召唤中伤害最高，陪玩真实感）
        try:
            life = getattr(self.agent, "life", None)
            if life is not None and not await life.select_tool("weapon"):
                self._blacklist_enemy(target)
                return False
        except Exception:
            return False

        key = self._enemy_key(target)
        start = last_change = time.monotonic()
        last_hp = int(target.get("life", 0) or 0)
        last_move = 0.0
        moving = False
        while time.monotonic() - start < timeout:
            # 重要任务优先：主人要求做的事 > 打小怪（不打折的主线）
            if check_task is not None and check_task():
                return False
            if self.agent is not None and not getattr(self.agent, "running", True):
                return False

            # ★ 每轮刷新状态：目标血量/位置是动态的（旧实现用静态快照，
            #   目标血量永不更新 → 隔墙判定永不触发，只能等超时退出）
            try:
                state = self.agent.get_state() if self.agent else state
            except Exception:
                pass
            px = int(state.get("tile_x", px0))
            py = int(state.get("tile_y", py0))

            # 从最新状态找目标（按 slot），消失只代表脱离接触。
            cur = None
            for e in state.get("nearby_npcs", []) or []:
                if self._enemy_key(e) == key:
                    cur = e
                    break
            if cur is None:
                # The Mod exports living NPCs only. Damage followed by
                # disappearance is ambiguous because the NPC may have walked
                # beyond the state radius; require its authoritative kill event.
                # The state push and npc_killed event travel through separate
                # callbacks, so the event can arrive a few frames after the
                # disappearance snapshot. Give the event loop a short window
                # to deliver it instead of reporting a false failed fight.
                confirmed = False
                for _ in range(5):
                    confirmed = self._consume_kill_confirmation(target)
                    if confirmed:
                        break
                    if check_task is not None and check_task():
                        return False
                    await asyncio.sleep(0.1)
                if confirmed:
                    try:
                        await self.mod.collect_items(radius=400)
                    except Exception:
                        pass
                return confirmed
            if int(cur.get("life", 0) or 0) <= 0:
                # 战斗胜利，收集掉落物（打完不抢任务，掉落让主线收）
                try:
                    collected = await self.mod.collect_items(radius=400)
                    if self.agent and collected > 0:
                        self.agent.logger.info(f"[战斗] 拾取了 {collected} 个掉落物")
                except Exception:
                    pass
                return True
            tx = int(cur.get("tile_x", 0) or 0)
            ty = int(cur.get("tile_y", 0) or 0)
            hp = int(cur.get("life", 0) or 0)

            # 自身低血 → 保命（不恋战）
            my_hp = int(state.get("hp", 0) or 0)
            my_max = int(state.get("max_life", 100) or 100) or 100
            if state.get("alive") is False or my_hp <= 0:
                return False
            if my_hp / my_max < self.retreat_hp_ratio:
                if self.agent is not None:
                    await self.agent.heal_self()
                return False  # 撤，交给 brain._guard_check 的逃跑逻辑

            dx = tx - px
            dy = ty - py
            dist = abs(dx) + abs(dy)
            if abs(dy) > self.max_height_gap:
                self._blacklist_enemy(cur)
                return False

            # 服务器的伤害结果可能在两次循环之间到达，必须跨轮比较血量。
            if hp < last_hp:
                last_change = time.monotonic()
            last_hp = hp
            if time.monotonic() - last_change > self.no_dmg_timeout:
                self._blacklist_enemy(cur)
                return False

            # ── 风筝走位 ──
            destination = None
            if dist < self.kite_too_close:
                # 过近 → 后退 3 格
                away = -1 if dx > 0 else 1
                destination = (px + away * 3, py)
            elif dist > self.kite_ideal_dist:
                offset = -2 if tx > px else 2
                destination = (tx + offset, ty)
            # 移动与挥砍并行；原先每挥一次都等一秒导航超时，攻击会一直被拖住。
            if destination is not None and time.monotonic() - last_move >= 0.8:
                await self.mod.navigate_stream_fire(*destination)
                last_move = time.monotonic()
                moving = True
            elif destination is None and moving:
                await self.mod.stop_actions()
                moving = False
            # 真实武器挥动：朝敌人 tile 坐标挥砍（方向由 C# 面向目标自动对准）
            await self.mod.use_item(tx, ty)

            await asyncio.sleep(0.3)
            try:
                if self.agent:
                    await self.agent.refresh_state()
            except Exception:
                pass
        self._blacklist_enemy(target)
        return False

    def _pick_target(self, state: Dict[str, Any], px: int, py: int) -> Optional[Dict[str, Any]]:
        """选最近的可达敌人（跳过黑名单/高差过大的）。"""
        enemies = state.get("nearby_npcs", []) or []
        best = None
        best_dist = 10**9
        for e in enemies:
            if not self.is_hostile(e) or e.get("slot") is None or int(e.get("slot", -1)) < 0:
                continue
            ex = int(e.get("tile_x", 0) or 0)
            ey = int(e.get("tile_y", 0) or 0)
            if self._is_blacklisted(e):
                continue
            # 可达性：垂直差过大（悬崖上/深坑里）跳过
            if abs(ey - py) > self.max_height_gap:
                continue
            d = abs(ex - px) + abs(ey - py)
            if d < best_dist:
                best_dist = d
                best = e
        return best
