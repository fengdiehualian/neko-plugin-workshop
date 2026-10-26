"""长期任务的具体干法：跟着我、一直挖某种矿、守在这。

只放"怎么做"，生命周期/让路/停止都归 longterm.LongTermManager 管。
每个 job 的循环骨架统一为：
    while 没被叫停:
        await lt.wait_turn(kind)   # 前台忙就让路
        干一小步
        task.beat(进度人话)
        await _notify_step(...)    # 通知交互引擎（干活汇报）
这样任何一步都能被立刻打断，也不会和前台任务抢操作权。
"""

import asyncio
import logging
import time
from typing import Any, Dict, Optional, Tuple

from ..polish.human_timing import HumanTiming
from .longterm import LT_BLOCKED, StandingTask

logger = logging.getLogger(__name__)

# 跟随参数 → 迟滞带跟随（hysteresis），按生存循环惯例 bridge.py
# 防止在边界反复横跳：走到 STOP 以内才停，走远到 TRIGGER 才追
FOLLOW_TRIGGER_DIST = 60   # 距离超过这个就追上去（大范围跟随）
FOLLOW_STOP_DIST = 15      # 距离小于这个就停下（大范围跟随）
# 贴身跟随（"跟在我身边"）：更近的阈值，始终黏在主人旁边
STICK_TRIGGER_DIST = 8     # 离开 8 格就追
STICK_STOP_DIST = 3        # 回到 3 格内才停
FOLLOW_TICK = 0.6
MINE_BATCH = 1         # 每轮只挖一点，保证能被及时打断


class StandingJobs:
    """长期任务的行为库。"""

    def __init__(self, agent) -> None:
        self.agent = agent
        self._following: bool = False  # 迟滞带跟随状态：是否正在追主人
        self.timing = HumanTiming()    # v2.1: 人类化延迟
        self._last_progress_notice: Dict[str, float] = {}

    # ── 交互引擎挂钩 ────────────────────────────

    async def _notify_step(self, kind: str, desc: str, **kw) -> None:
        """每完成一小步后通知交互引擎。

        executor.notify("step_done") 会被 brain.py 的回调拦截，
        转入 InteractionEngine.inject_event("step_done")，推高 speech_urge。
        """
        now = time.monotonic()
        if now - self._last_progress_notice.get(kind, 0) < 15:
            return
        self._last_progress_notice[kind] = now
        try:
            exe = getattr(self.agent, "executor", None)
            if exe:
                await exe.notify("step_done", kind=kind, desc=desc, **kw)
        except Exception:
            pass

    @property
    def lt(self):
        return self.agent.longterm

    # ---------------- 小工具 ----------------
    def _me(self) -> Tuple[int, int]:
        st = self.agent.get_state()
        return st.get("tile_x", 0), st.get("tile_y", 0)

    def _owner(self) -> Optional[Tuple[int, int]]:
        # 主人 = 最近的有效玩家（过滤自身/残留槽位/无效坐标）
        st = self.agent.get_state()
        players = st.get("nearby_players") or []
        if not players:
            return None
        me_x, me_y = self._me()
        my_name = ""
        try:
            my_name = self.agent._character_name()
        except Exception:
            pass
        best = None
        best_d = 10 ** 9
        for p in players:
            if not isinstance(p, dict):
                continue
            name = p.get("name", "")
            if name and my_name and name == my_name:
                continue  # 过滤自己
            x = int(p.get("tile_x", 0) or 0)
            y = int(p.get("tile_y", 0) or 0)
            if x == 0 and y == 0:
                continue  # 过滤无效坐标（下线残留槽位）
            d = (x - me_x) ** 2 + (y - me_y) ** 2
            if d < best_d:
                best_d = d
                best = (x, y)
        return best

    def _spawn_inquiry_wait(self, inq_mgr, inq) -> None:
        """后台等询问回答/超时，避免 pending 永久占位吞掉主人后续指令。"""
        async def _wait():
            try:
                await inq_mgr.wait_answer(inq)
            except Exception:
                pass
        try:
            asyncio.ensure_future(_wait())
        except Exception:
            pass

    # ---------------- 跟着我：迟滞带跟随（按生存循环惯例 bridge.py） ----------------
    # 原理：
    #   - 距离 > trigger_dist(默认60格) → 开始追
    #   - 距离 < stop_dist(默认15格)    → 停下
    #   - 在中间保持当前状态不变
    # 避免在边界反复"追→停→追"的横跳。
    async def follow_loop(self, task: StandingTask) -> None:
        """一直跟着主人，直到被叫停。迟滞带跟随，避免边界抖。

        距离阈值从 agent.cfg 读取（可在 plugin.toml 配置）：
        - follow_trigger_dist: 距离超过此值才追（默认60）
        - follow_stop_dist: 距离小于此值停止（默认15）
        """
        logger.info(f"🚶 follow_loop 开始执行，task.name={task.name}, kind={task.kind}")
        lt = self.lt
        self._following = False  # 重置跟随状态
        lost = 0
        # 贴身模式（"跟在我身边"）：target=="stick" → 用贴身阈值
        stick = task.target == "stick"
        if stick:
            trigger = self.agent.cfg.get("follow_stick_trigger_dist", STICK_TRIGGER_DIST)
            stop_at = self.agent.cfg.get("follow_stick_stop_dist", STICK_STOP_DIST)
        else:
            # v0.7: 依恋调制——黏人指数高 → 更近才追、贴得更近（Heart）
            base_trigger = self.agent.cfg.get("follow_trigger_dist", FOLLOW_TRIGGER_DIST)
            base_stop = self.agent.cfg.get("follow_stop_dist", FOLLOW_STOP_DIST)
            try:
                # 真 Heart 在插件 AutonomousBrain 上（agent.brain 是 TaskBrain）
                plugin = getattr(self.agent, "plugin", None)
                abrain = getattr(plugin, "_autonomous_brain", None)
                heart = getattr(abrain, "heart", None) if abrain else None
                stickiness = heart.stickiness() if heart else 0.5
            except Exception:
                stickiness = 0.5
            trigger = max(10, int(base_trigger * (1.6 - stickiness)))
            stop_at = max(3, int(base_stop * (1.3 - 0.3 * stickiness)))
        mode = "贴身" if stick else "大范围"
        logger.info(f"📏 跟随参数({mode}): trigger={trigger}, stop_at={stop_at}")

        loop_count = 0
        nav_failures = 0
        while not lt.should_stop(task.kind):
            loop_count += 1
            logger.info(f"🔄 follow_loop 第 {loop_count} 次循环")

            if not await lt.wait_turn(task.kind):
                logger.info("⚠️ wait_turn 返回 False，退出循环")
                break

            owner = self._owner()
            if owner is None:
                lost += 1
                if lost == 5:
                    task.beat("找不到主人了，先待在原地")
                    logger.warning("⚠️ 连续 5 次找不到主人")
                if self._following:
                    self._following = False
                    # The existing navigation runs in the background until its
                    # deadline; losing sight of the owner must release it now.
                    await self.agent.mod.stop_actions()
                await asyncio.sleep(self.timing.action_duration(FOLLOW_TICK))
                continue
            lost = 0

            mx, my = self._me()
            ox, oy = owner
            dx, dy = ox - mx, oy - my
            dist = (dx * dx + dy * dy) ** 0.5
            task.progress = int(time.time() - task.started_at)

            logger.info(f"📍 位置: 我({mx}, {my}), 主人({ox}, {oy}), 距离={int(dist)}")

            # ── 迟滞带判断 ──
            if not self._following:
                # 没在追 → 距离超过触发阈值才追
                if dist >= trigger:
                    self._following = True
                    task.beat(f"主人走远了({int(dist)}格)，追上去")
                else:
                    task.beat(f"主人在附近({int(dist)}格)")
                    await asyncio.sleep(self.timing.action_duration(FOLLOW_TICK))
                    continue
            else:
                # 正在追 → 距离小于停止阈值才停
                if dist <= stop_at:
                    self._following = False
                    task.beat(f"追到主人身边了({int(dist)}格)")
                    await self.agent.mod.stop_actions()
                    await asyncio.sleep(self.timing.action_duration(FOLLOW_TICK))
                    continue

            # 持续追
            task.beat(f"离主人 {int(dist)} 格，追上去")
            try:
                # v0.8 实时跟随（代际接管）：fire-and-forget 流式导航，
                # 每轮（0.6s）更新目标——主人走动 AI 立刻追，不再阻塞等 15 秒。
                # C# 侧路径代际（navGen）保证新导航接管时旧任务不误清路径。
                nav_result = await self.agent.mod.navigate_stream_fire(ox, oy)
                if nav_result is False:
                    nav_failures += 1
                    if nav_failures >= 5:
                        task.status = LT_BLOCKED
                        task.beat("连续导航失败，主人可能在不可达区域")
                        await self.agent.mod.stop_actions()
                        return
                elif nav_result is True:
                    nav_failures = 0
                # 追了一步 → 通知交互引擎
                await self._notify_step("follow", f"追主人中，距离{int(dist)}格", dist=dist)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"❌ [follow_loop] navigate_stream_fire 异常: {e}")
            await asyncio.sleep(self.timing.action_duration(FOLLOW_TICK))

    # ---------------- 一直挖某种矿 ----------------
    async def mine_loop(self, task: StandingTask) -> None:
        """没说挖多少就一直挖，挖满 goal_amount（若有）或被叫停才停。"""
        lt = self.lt
        ore = task.target or "铁矿"
        mining = self.agent.mining
        empty_streak = 0  # 连续挖空次数（触发询问）
        while not lt.should_stop(task.kind):
            if not await lt.wait_turn(task.kind):
                break

            if task.goal_amount and task.progress >= task.goal_amount:
                task.beat(f"{ore} 够了，一共 {task.progress} 个")
                break

            try:
                if not await self.agent.life.select_tool("pick"):
                    task.status = LT_BLOCKED
                    task.beat(self.agent.life.last_failure)
                    return
                mining.reset()
                _iid, got = await mining.mine_target(
                    ore, MINE_BATCH, self.agent.get_state())
            except asyncio.CancelledError:
                raise
            except Exception as e:
                empty_streak += 1
                task.beat(f"挖不动：{e}")
                if empty_streak >= 3:
                    task.status = LT_BLOCKED
                    return
                await asyncio.sleep(self.timing.action_duration(1.5))
                continue

            if got:
                task.progress += int(got)
                empty_streak = 0
                task.beat(f"又挖到 {ore}，一共 {task.progress} 个")
                await self._notify_step("mine",
                    f"挖到{got}个{ore}，共{task.progress}个",
                    ore=ore, got=int(got), total=task.progress)
                if mining.last_resupply_ok is False:
                    task.status = LT_BLOCKED
                    task.beat("背包整理或返回主人未完成，暂停采矿等待主人处理")
                    return
            else:
                empty_streak += 1
                task.beat(f"这附近没{ore}了")
                # 连续无进展就退出并走长期任务的受阻回报，不能无限重试。
                if empty_streak >= 4:
                    task.status = LT_BLOCKED
                    task.beat(f"连续尝试后没有采集到{ore}，可能无矿点或路径不可达")
                    return
                await asyncio.sleep(self.timing.action_duration(1.0))
            await asyncio.sleep(self.timing.action_duration(0.3))

    # ---------------- 砍树 ----------------
    async def chop_loop(self, task: StandingTask) -> None:
        """没说砍多少就一直砍，砍满 goal_amount（若有）或被叫停才停。"""
        lt = self.lt
        wood = task.target or "木材"
        life = self.agent.life
        empty_streak = 0  # 连续砍空次数（触发询问）
        while not lt.should_stop(task.kind):
            if not await lt.wait_turn(task.kind):
                break

            if task.goal_amount and task.progress >= task.goal_amount:
                task.beat(f"{wood} 够了，一共 {task.progress} 个")
                break

            try:
                iid = self.agent.resolve_item(wood)
                if iid <= 0:
                    task.status = LT_BLOCKED
                    task.beat(f"无法识别要收集的木材：{wood}")
                    return
                got = await life.chop_wood(target=MINE_BATCH, item_id=iid)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                empty_streak += 1
                task.beat(f"砍不动：{e}")
                if empty_streak >= 3:
                    task.status = LT_BLOCKED
                    return
                await asyncio.sleep(self.timing.action_duration(1.5))
                continue

            if got:
                task.progress += int(got)
                empty_streak = 0
                task.beat(f"又砍到 {wood}，一共 {task.progress} 个")
                await self._notify_step("chop",
                    f"砍到{got}个{wood}，共{task.progress}个",
                    ore=wood, got=int(got), total=task.progress)
                base = getattr(self.agent, "base", None)
                if base is not None and base.inventory_nearly_full():
                    handled = await base.handle_inventory_full("长期砍树后背包空间不足")
                    if lt.should_stop(task.kind):
                        return
                    if not handled:
                        task.status = LT_BLOCKED
                        task.beat("背包整理或返回主人未完成，暂停砍树等待主人处理")
                        return
            else:
                empty_streak += 1
                reason = life.last_failure or f"砍树后没有确认到新增{wood}"
                task.beat(reason)
                if empty_streak >= 3 or any(w in reason for w in ("工具", "斧头", "背包")):
                    task.status = LT_BLOCKED
                    return
                await asyncio.sleep(self.timing.action_duration(1.0))
            await asyncio.sleep(self.timing.action_duration(0.3))

    # ---------------- 守在这 ----------------
    async def guard_loop(self, task: StandingTask) -> None:
        """守在某点附近，走远了自己回来，有敌人就打。

        守护逻辑（每轮优先级）：
        1. 检查附近敌人 → 有敌人就战斗（守护半径内）
        2. 检查距离守点位置 → 走远了就回去
        3. 原地待命
        """
        lt = self.lt
        hx, hy = task.params.setdefault("home", self._me())
        guard_range = int(task.params.get("range", 15))  # 守护半径（从任务参数读取）
        task.note = f"守护半径{guard_range}格"
        combat = self.agent.combat

        while not lt.should_stop(task.kind):
            if not await lt.wait_turn(task.kind):
                break
            try:
                await self.agent.refresh_state()
                state = self.agent.get_state()
                mx, my = self._me()

                # ── 1. 优先战斗：守护范围内有敌人 ──
                enemies = state.get("nearby_npcs", [])
                threat = None
                for e in enemies:
                    if not combat.is_hostile(e):
                        continue
                    ex = int(e.get("tile_x", 0) or 0)
                    ey = int(e.get("tile_y", 0) or 0)
                    dist_to_home = abs(ex - hx) + abs(ey - hy)
                    if dist_to_home <= guard_range:
                        threat = e
                        break

                if threat:
                    task.beat(f"发现敌人 {threat.get('name', '???')}！开打")
                    fought = await combat.fight_nearest(state, timeout=10)
                    if fought:
                        task.beat("击退敌人，继续守护")
                        await self._notify_step("guard", "击退入侵者", enemy=threat.get("name"))
                    else:
                        task.beat("战斗失败，保持警戒")
                    await asyncio.sleep(self.timing.action_duration(0.5))
                    continue

                # ── 2. 检查是否走远 ──
                dist_from_home = abs(mx - hx) + abs(my - hy)
                if dist_from_home > guard_range * 2:
                    task.beat("走远了，回守点")
                    await self.agent.navigate_to(hx, hy, timeout=15)
                    await self._notify_step("guard", "返回守点", dist=dist_from_home)
                else:
                    task.beat(f"守着呢（范围{guard_range}格）")
                    await self._notify_step("guard", f"守点中，{task.progress}s", elapsed=task.progress)

            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.agent.logger.error(f"[guard_loop] 异常: {e}")
                pass

            task.progress = int(time.time() - task.started_at)
            await asyncio.sleep(self.timing.action_duration(1.2))

    # ---------------- 统一入口 ----------------
    async def start(self, kind: str, target: str = "", amount: int = 0,
                    reason: str = "", **params) -> Dict[str, Any]:
        """按 kind 起一个长期任务。"""
        # 木材/树类目标 → 砍树（长期任务语义：砍够为止）
        WOOD_WORDS = ("木材", "木", "树", "木头")
        if kind == "mine" and target in WOOD_WORDS:
            kind = "chop"
            target = "木材"
        if kind in ("mine", "chop"):
            self.agent.mining.reset()
        table = {
            "follow": ("跟着主人", self.follow_loop),
            "mine": (f"一直挖{target or '矿'}", self.mine_loop),
            "chop": (f"一直砍{target or '木材'}", self.chop_loop),
            "guard": ("守在这里", self.guard_loop),
        }
        if kind not in table:
            return {"ok": False, "output": f"我不会长期做「{kind}」这件事"}
        name, fn = table[kind]
        if kind == "follow" and target == "stick":
            name = "跟在主人身边"  # 贴身模式：任务名区分"跟着我"
        task = StandingTask(name=name, kind=kind, target=target,
                            goal_amount=amount, reason=reason, params=params)
        return await self.lt.start(task, fn)
