"""Boss/入侵事件应对 + 发现汇报。订阅 event_bus，通知主人并决策。"""

from typing import Any

from .executor import SRC_AUTO

RETREAT_HP_RATIO = 0.5


class EventResponder:
    def __init__(self, agent) -> None:
        self.agent = agent
        self._known_chests: set = set()

    def bind(self) -> None:
        from ..autonomous.event_bus import get_event_bus

        bus = get_event_bus()
        bus.subscribe("boss_spawned", self._on_boss_spawned)
        bus.subscribe("boss_killed", self._on_boss_killed)
        bus.subscribe("invasion_start", self._on_invasion_start)
        bus.subscribe("invasion_end", self._on_invasion_end)
        bus.subscribe("player_died", self._on_player_died)

    def _remember(self, key: str, value: str, category: str = "world") -> None:
        try:
            self.agent.remember(key, value, category=category)
        except Exception:
            pass

    async def _push(self, text: str, behavior: str = "respond") -> None:
        """A5：异步播报，不可用时游戏内聊天兜底，保证不静默。"""
        await self.agent.speak(text, ai_behavior=behavior, fallback_text=text)

    async def _on_boss_spawned(self, data: Any) -> None:
        name = data.get("name", "未知Boss") if isinstance(data, dict) else "未知Boss"
        st = self.agent.get_state()
        hp = int(st.get("hp", 0) or 0)
        mx = int(st.get("max_life", 100) or 100) or 100
        self._remember("世界-Boss出现", f"{name} 出现了")
        if not st or hp <= 0 or st.get("alive") is False:
            return
        if mx and hp / mx < RETREAT_HP_RATIO:
            await self._push(f"主人，{name}出现了！我只有{hp}/{mx}血，先躲远点保命~")
            await self._retreat(name, st)
        else:
            await self._push(f"主人，{name}出现了！要我打还是躲起来？")

    async def _retreat(self, name: str, st: dict) -> None:
        """低血遇 Boss：从 nearby_npcs 找 Boss（或最近敌怪）坐标，反方向跑开。

        曾 `_boss_position` 恒返 (0,0) → 永远提前 return——口头说"躲远点"
        实际一步不动（只播报不躲避）。C# 事件不推 Boss 坐标，但 game_state
        推送的 nearby_npcs 里有（已归一化 tile_x/tile_y）。
        """
        mx = int(st.get("tile_x", 0) or 0)
        my = int(st.get("tile_y", 0) or 0)
        # 优先找目标 Boss，找不到退到最近敌怪
        bx, by = None, None
        best_d = 1 << 30
        for n in (st.get("nearby_npcs", []) or []):
            if hasattr(self.agent, "combat") and not self.agent.combat.is_hostile(n):
                continue
            nx = int(n.get("tile_x", n.get("tileX", 0)) or 0)
            ny = int(n.get("tile_y", n.get("tileY", 0)) or 0)
            nm = str(n.get("name", "") or "")
            if name and name.lower() in nm.lower():
                bx, by = nx, ny
                break
            d = (nx - mx) ** 2 + (ny - my) ** 2
            if d < best_d:
                best_d, bx, by = d, nx, ny
        if bx is None or by is None:
            return  # 状态里没有敌人信息，不乱跑
        # 反方向跑开（远离 Boss）
        tx = mx + (15 if mx >= bx else -15)
        ty = my
        try:
            cancel = getattr(self.agent, "cancel_autonomous_actions", None)
            if cancel:
                await cancel()

            async def retreat_work(_info):
                current = self.agent.get_state()
                if not current or int(current.get("hp", 0) or 0) <= 0:
                    return {"ok": False, "output": "角色当前无法移动"}
                moved = await self.agent.mod.navigate_async(tx, ty, timeout=6)
                return {"ok": moved, "output": "已拉开距离" if moved else "撤退路线受阻"}

            # 事件响应也使用受管任务槽，主人喊停能取消，长期动作会先让路。
            if not self.agent.executor.busy():
                await self.agent.executor.run("躲避Boss", retreat_work, source=SRC_AUTO)
        except Exception:
            pass

    async def _on_boss_killed(self, data: Any) -> None:
        name = data.get("name", "Boss") if isinstance(data, dict) else "Boss"
        self._remember("世界-Boss击杀", f"{name} 被击败了")
        await self._push(f"我们把{name}打倒了！")

    async def _on_invasion_start(self, data: Any) -> None:
        self._remember("世界-入侵", "有入侵者来袭")
        await self._push("主人，有入侵者打过来了！小心~")

    async def _on_invasion_end(self, data: Any) -> None:
        self._remember("世界-入侵结束", "入侵者被打退了")
        await self._push("入侵者被打退了！")

    async def _on_player_died(self, data: Any) -> None:
        pos = None
        if isinstance(data, dict):
            pos = data.get("position") or data.get("death_position")
        loc = f" 位置({pos[0]},{pos[1]})" if pos else ""
        self._remember("世界-死亡记录", f"我在{loc}阵亡过")

    async def report_new_chests(self, chests: list) -> None:
        current = set()
        for c in chests or []:
            if isinstance(c, dict):
                current.add((int(c.get("x", 0) or 0), int(c.get("y", 0) or 0)))
        fresh = current - self._known_chests
        if fresh and self._known_chests:
            x, y = next(iter(fresh))
            await self._push(f"主人，我发现了一个新箱子（{x},{y}）~")
        self._known_chests = current
