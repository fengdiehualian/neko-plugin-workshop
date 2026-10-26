"""AutonomousBrain：四层思考 + 事件驱动打断 + 自主执行 + 交互引擎。"""

import asyncio
import random
import time
from typing import Any, Dict

from ..bridge.executor import SRC_AUTO
from ..bridge.task_chain import Goal
from ..core.context import build_user_context
from ..polish.attention import AttentionDrift
from ..polish.human_timing import HumanTiming
from .event_bus import get_event_bus

# v2.0: 交互引擎
from .interaction_engine import InteractionEngine
from .internal_state import InternalState
from .motivation import MotivationSystem

# ── LLM 自主决策的触发文本 ──

LLM_THINK_PROMPT = (
    "你是{name}，一只在泰拉瑞亚世界里的猫娘——你会采矿、砍树、战斗、探索。\n"
    "你现在闲着，用游戏里角色的视角观察周围和主人的活动：\n"
    "{context}\n\n"
    "可以分享眼前的新发现、准备物资的想法或陪主人聊一句，别编造游戏事实。\n"
    "这是自主观察，不是主人指令：不要调用 terraria_command，也不要开始或改动任何任务。"
    "只在确有值得分享的变化时自然说 1-2 句，没有就保持安静。"
)


class AutonomousBrain:
    def __init__(self, plugin) -> None:
        self.plugin = plugin
        self.agent = plugin._agent
        self.cfg = plugin._config
        self.state = InternalState()
        self.motivation = MotivationSystem()
        self.bus = get_event_bus()
        self.timing = HumanTiming()
        self.attention = AttentionDrift()
        self.running = False
        self._tasks: list[asyncio.Task] = []
        self._busy = False
        self._action_task = None
        self._action_kind = ""
        self._respawn_task = None
        self._last_llm_think = 0.0  # 上次 LLM 思考时间戳

        # v2.0: 交互引擎接管对话交互（直接传整个 cfg——
        # 之前取 cfg["interaction"] 子字典恒为空，导致 interaction_tick 等配置读不到）
        try:
            self.interaction = InteractionEngine(self.agent, plugin, self.cfg or {})
        except Exception:
            self.interaction = None

    async def start(self) -> None:
        if self.running:
            return
        self.running = True
        self._tasks = [
            asyncio.create_task(self._state_tick()),
            asyncio.create_task(self._fast_think()),
            asyncio.create_task(self._llm_think()),
        ]
        self.bus.subscribe("interrupt", self._on_interrupt)
        self.bus.subscribe("combat_hit", self._on_combat_hit)
        # 注册复活回调：复活后自动寻路找主人
        self.agent.on_respawn(self._on_respawn)

        # v0.7: 处境融合层（身体感×画面感×记忆 → 心情/台词/行为）
        try:
            from .situation import SituationEngine

            coord = getattr(self.agent, "coordinator", None)
            llm = None
            if coord:
                llm = getattr(getattr(coord, "_intent_parser", None), "_llm_call", None)
            self.situation = SituationEngine(self.agent, self, llm_call=llm)
            await self.situation.start()
            self.plugin.logger.info(f"[brain] 处境融合层已启动 (llm={'有' if llm else '无，规则兜底'})")
        except Exception:
            self.situation = None
            self.plugin.logger.warning("[brain] 处境融合层启动失败")

        # v0.7: Heart 依恋值（主人关系跨会话）
        try:
            from .heart import Heart

            self.heart = Heart(self.agent)
            self.plugin.logger.info(f"[brain] Heart 依恋层已启动 (bond={self.heart.bond:.0f})")
        except Exception:
            self.heart = None
            self.plugin.logger.warning("[brain] Heart 依恋层启动失败")

        # v2.0: 启动交互引擎
        if self.interaction:
            await self.interaction.start()
            # 注册 executor 回调 → 干活汇报 / 任务打断
            ex = getattr(self.agent, "executor", None)
            if ex:
                ex.on("task_done", self._on_executor_task_done)
                ex.on("task_started", self._on_executor_task_started)
                ex.on("interrupted", self._on_executor_interrupted)
                ex.on("step_done", self._on_executor_step)

    async def stop(self) -> None:
        self.running = False
        await self.cancel_actions()
        off_respawn = getattr(self.agent, "off_respawn", None)
        if off_respawn:
            off_respawn(self._on_respawn)
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        self.bus.unsubscribe("interrupt", self._on_interrupt)
        self.bus.unsubscribe("combat_hit", self._on_combat_hit)
        # v0.7: 停止处境融合层
        if getattr(self, "situation", None):
            await self.situation.stop()
        # v0.7: Heart 依恋值落盘
        if getattr(self, "heart", None):
            try:
                self.heart.save()
            except Exception:
                pass
        # v2.0: 停止交互引擎
        if self.interaction:
            await self.interaction.stop()

    async def cancel_actions(self, kind: str = "") -> None:
        """停止实际自主动作，不取消负责感知和陪聊的常驻循环。"""
        tasks = []
        if self._action_task and (not kind or kind == self._action_kind):
            tasks.append(self._action_task)
        if self._respawn_task and (not kind or kind == "follow"):
            tasks.append(self._respawn_task)
        tasks = [task for task in tasks if task is not asyncio.current_task() and not task.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def _autonomy_allowed(self, kind: str = "") -> bool:
        check = getattr(self.agent, "autonomy_allowed", None)
        return check(kind) if check else True

    async def _run_action(self, coro, kind: str) -> Any:
        self._action_kind = kind
        task = asyncio.create_task(coro)
        self._action_task = task
        self._busy = True
        try:
            return await task
        except asyncio.CancelledError:
            # 主人只取消动作时，感知循环继续；插件关闭时继续传播取消。
            if asyncio.current_task().cancelling():
                raise
            return None
        finally:
            if self._action_task is task:
                self._action_task = None
                self._action_kind = ""
                self._busy = False

    def occupied(self) -> bool:
        """有任务在跑就算占用：自主行为必须让位，不打断正在执行的任务。

        包含两类：前台有限任务（executor）与后台长期任务（longterm）。
        主人说了"跟着我"，自主行为就别再自作主张乱跑。
        """
        if self._busy:
            return True
        ex = getattr(self.agent, "executor", None)
        if ex and ex.busy():
            return True
        lt = getattr(self.agent, "longterm", None)
        return bool(lt and lt.busy_kinds())

    async def _state_tick(self) -> None:
        interval = self.cfg.get("state_tick_interval_seconds", 1.0)
        while self.running:
            try:
                state = self.agent.get_state()
                # v2.2: AI 客户端未连接（boot 不再自动启动，等面板「连接游戏」）
                # → 空转：不涨 boredom、不扣 Heart（否则用户没玩时依恋值狂掉）
                if (not getattr(self.agent, "running", False) or not state
                        or state.get("alive") is False or int(state.get("hp", 0) or 0) <= 0):
                    await asyncio.sleep(interval)
                    continue
                # 刺激源只看前台任务——长期任务（跟随/挖矿）是常态陪伴，不算"刺激"；
                # 否则跟随中 boredom 永远下降，_llm_think 永不触发，自主思考/情感交互全停。
                ex = getattr(self.agent, "executor", None)
                has_stimulus = bool(ex and ex.busy())
                self.state.tick(has_stimulus=has_stimulus)
                # v0.7: Heart 依恋值——主人同屏陪伴增长 / 冷落衰减（每 30s 一次冷落检查）
                heart = getattr(self, "heart", None)
                if heart:
                    try:
                        state = self.agent.get_state()
                        if state.get("nearby_players"):
                            heart.on_companion(interval)
                        elif int(time.time()) % 30 == 0:
                            heart.on_neglect_tick()
                    except Exception:
                        pass
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.plugin.logger.warning(f"[brain] _state_tick 异常: {e}")
            await asyncio.sleep(interval)

    async def _fast_think(self) -> None:
        interval = self.cfg.get("fast_think_interval_seconds", 5.0)
        while self.running:
            try:
                state = self.agent.get_state()
                # v2.2: AI 客户端未连接 → 空转（不自主行动，避免空 state 触发
                # motivation gather → executor 挖矿失败刷屏）
                if (not getattr(self.agent, "running", False) or not state
                        or state.get("alive") is False or int(state.get("hp", 0) or 0) <= 0):
                    await asyncio.sleep(interval)
                    continue
                # P0 优先级守卫：自保（喝药/逃跑）无条件优先 + 长期任务中遇怪战斗
                # 按生存循环惯例 主循环 P0 自保 > P1 战斗（不被任务占用抑制）
                if await self._run_action(self._guard_check(state), "guard"):
                    await asyncio.sleep(interval)
                    continue
                # 有前台任务在执行时其余自主行为让位（避免抢控制权）
                # 长期任务（跟随/挖矿）不阻塞自主行为——否则跟随中猫娘不会自己打架/挖矿
                ex = getattr(self.agent, "executor", None)
                if ex and ex.busy():
                    await asyncio.sleep(interval)
                    continue
                # v2.2: 有长期任务在跑 → 自主行动（gather/explore/social/comfort）
                # 全部让位。此前 bug：主人说"跟着我"后 follow 是长期任务不占
                # executor 前台，fast_think 每 5s 仍跑 _act_on_drive——
                # social drive（主人在旁 0.85）触发 follow_player 前台任务 →
                # request_yield → follow 永久"让路等着"，猫娘不跟还在自主行动。
                if self._has_longterm():
                    await asyncio.sleep(interval)
                    continue
                idle = getattr(self.agent, "_idle_task", None)
                if idle and not idle.done():
                    await asyncio.sleep(interval)
                    continue
                # 动机层的 nearby_npcs 表示可交战目标，不能把兔子/城镇 NPC
                # 或已经确认隔墙的怪物当成永远压过社交的战斗刺激。
                target = self.agent.combat._pick_target(
                    state, state.get("tile_x", 0), state.get("tile_y", 0))
                drive_state = dict(state, nearby_npcs=[target] if target else [])
                drive = self.motivation.update(drive_state, self.state.boredom)
                kind = {"social": "follow", "combat": "guard", "gather": "chop"}.get(drive, drive)
                if self._autonomy_allowed(kind):
                    await self._run_action(self._act_on_drive(drive, state), kind)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.plugin.logger.warning(f"[brain] _fast_think 异常: {e}")
            await asyncio.sleep(interval)

    def _has_longterm(self) -> bool:
        """是否有活跃长期任务（跟随/挖矿/守点/砍树）。

        有则自主行为让位——主人明确指令 > 自主决策。用 busy_kinds()
        （无日志）而非 active()（每条都打 info 日志，5s 轮询会刷屏）。
        """
        try:
            lt = getattr(self.agent, "longterm", None)
            if lt is None:
                return False
            return bool(lt.busy_kinds())
        except Exception:
            return False

    async def _guard_check(self, state: Dict[str, Any]) -> bool:
        """无条件优先级守卫。返回 True 表示本轮已处理（跳过其余自主行为）。

        v0.11（A2）：按生存循环 P0/P1——
          P0 自保：HP<50% 喝药（独立动作，不打断任务）
          P1 战斗：无前台任务时打怪；有前台任务则只提醒不打断
        不再有敌就无限占 fast_think（那会吞掉主人的 finite 任务）。
        """
        if not state:
            return False
        hp = int(state.get("hp", 0) or 0)
        if hp <= 0 or state.get("alive") is False or getattr(self.agent, "_in_combat", False):
            return False
        max_hp = int(state.get("max_life", 100) or 100) or 100
        ratio = hp / max_hp if max_hp > 0 else 1.0
        enemies = [e for e in (state.get("nearby_npcs", []) or [])
                   if self.agent.combat.is_hostile(e)]

        handled = False
        # ── P0 自保 1：血量 <50% 先喝药（独立动作，不打断任务） ──
        if 0 < ratio < 0.5:
            try:
                if await self.agent.heal_self():
                    self.agent.log("自保：血量低，喝药恢复", "item")
                    handled = True
            except Exception:
                pass

        # ── P0 自保 2：血量 <30% 且附近有敌 → 向反方向拉开 8 格 ──
        if ratio < 0.3 and enemies and not handled:
            try:
                me_x = int(state.get("tile_x", 0) or 0)
                me_y = int(state.get("tile_y", 0) or 0)
                nearest = min(
                    enemies,
                    key=lambda e: abs(int(e.get("tile_x", 0) or 0) - me_x) + abs(int(e.get("tile_y", 0) or 0) - me_y),
                )
                dx = me_x - int(nearest.get("tile_x", me_x) or me_x)
                dy = me_y - int(nearest.get("tile_y", me_y) or me_y)
                dist = (dx * dx + dy * dy) ** 0.5
                if dist < 12:
                    # 水平逃生，避免整数除法把分量截成 0，或向地下盲冲。
                    tx = me_x + (8 if dx >= 0 else -8)
                    lt = getattr(self.agent, "longterm", None)
                    pause_owned = bool(lt and not lt.yielding())
                    if pause_owned:
                        lt.request_yield()
                    self.agent._in_combat = True
                    try:
                        if pause_owned:
                            await lt.wait_paused()
                        await self.agent.mod.navigate_to(tx, me_y, timeout=3)
                    finally:
                        try:
                            await self.agent.mod.stop_actions()
                        finally:
                            self.agent._in_combat = False
                            ex = getattr(self.agent, "executor", None)
                            if pause_owned and not (ex and ex.busy()):
                                lt.release_yield()
                    self.agent.log("自保：血量危急，拉开距离", "warn")
                    handled = True
                    try:
                        inter = getattr(self, "interaction", None)
                        if inter:
                            await inter.push_speech("血好少，先跑开躲一下喵！", behavior="respond")
                    except Exception:
                        pass
            except Exception:
                pass

        # ── P1 战斗：无前台任务才打（P1 优先级，但可让路）。
        # 有主线任务时（如挖矿/砍树），途中遇敌由导航守卫（navigate_to 的 on_tick）
        # 停下先打再走；这里不打断主线，避免两条控制流抢操作权。
        # 长期任务只响应近身威胁，防止为了追远处小怪抛下主人。
        if enemies and not handled:
            # 主人暂停自主战斗时仅保留血量危急的逃生（上面的 P0 已处理）。
            if not self._autonomy_allowed("guard"):
                return handled
            ex = getattr(self.agent, "executor", None)
            fg_busy = bool(ex and ex.busy())
            if fg_busy:
                # 有主线任务：不打断，最多一句话提醒（交给交互引擎）
                try:
                    if self.interaction and self.state.boredom > 0.2:
                        pass  # 避免抢话：任务的 step/完成回调自会说话
                except Exception:
                    pass
                return handled  # 不占 fast_think
            if self._has_longterm():
                px, py = state.get("tile_x", 0), state.get("tile_y", 0)
                enemies = [e for e in enemies
                           if abs(e.get("tile_x", 0) - px) + abs(e.get("tile_y", 0) - py) <= 6]
                if not enemies:
                    return handled
                state = dict(state, nearby_npcs=enemies)
            if not self.agent.combat._pick_target(state, state.get("tile_x", 0), state.get("tile_y", 0)):
                return handled
            idle = getattr(self.agent, "_idle_task", None)
            if idle and not idle.done():
                idle.cancel()
                await asyncio.gather(idle, return_exceptions=True)
            lt = getattr(self.agent, "longterm", None)
            if lt and lt.yielding():
                return handled
            pause_owned = bool(lt and lt.busy_kinds())
            if pause_owned:
                lt.request_yield()
            try:
                self._busy = True
                try:
                    if pause_owned:
                        # 不只置标志：必须等正在用斧头/导航的动作退出再切武器。
                        await lt.wait_paused()
                    await self.agent.combat.fight_nearest(
                        state, timeout=8, check_task=lambda: ex.busy() if ex else False
                    )
                finally:
                    self._busy = False
                handled = True
            except Exception:
                pass
            finally:
                # 主人前台已接管时，它拥有让路状态，由它完成后恢复长期任务。
                if pause_owned and not (ex and ex.busy()):
                    lt.release_yield()
        return handled

    async def _act_on_drive(self, drive: str, state: Dict[str, Any]) -> None:
        await asyncio.sleep(self.timing.reaction_delay())
        # 反应延迟期间主人可能已经下达长期任务，动作前必须重新检查。
        ex = getattr(self.agent, "executor", None)
        if (ex and ex.busy()) or self._has_longterm():
            return
        kind = {"social": "follow", "combat": "guard", "gather": "chop"}.get(drive, drive)
        if not self._autonomy_allowed(kind):
            return
        if self.attention.should_drift():
            return

        players = state.get("nearby_players", [])
        # #96: 只有社交驱动才跟随主人——否则有主人在场时 gather/explore/comfort
        # 全被 follow_player 抢占（前台任务占用 executor），挖矿/探索自主动机永不执行。
        if players and drive == "social":
            if not self._autonomy_allowed("follow"):
                return
            ppos = self._nearest_owner(state)
            if ppos is None:
                return
            distance = abs(ppos[0] - state.get("tile_x", 0)) + abs(ppos[1] - state.get("tile_y", 0))
            if distance > 12:
                await self.agent.mod.navigate_to(*ppos, timeout=5)

        if drive == "combat":
            self._busy = True
            try:
                await self.agent.combat.fight_nearest(state, check_task=lambda: bool(ex and ex.busy()) or self._has_longterm())
            finally:
                self._busy = False
        elif drive == "comfort":
            hp = state.get("hp", 100)
            max_hp = state.get("max_life", 100) or 100
            if hp < max_hp * 0.5:
                if await self.agent.heal_self():
                    await self.agent.send_chat("血量有点低，喝口药~")
                else:
                    await self.agent.send_chat("血量低，先躲一下")
        elif drive == "gather":
            # “收集木材”需要先找到并砍树；仅调用 gather 只会捡脚边掉落物，
            # 经常得到 0 个却消耗一次自主行动。把木材储备映射到真实砍树
            # 执行器，仍由任务链按背包增量核验结果。
            await self._auto_task("自主储备材料", [{"action": "chop", "item": "wood", "amount": 15}])
        elif drive == "explore" and self.state.boredom > 0.55:
            # v3.0 巡逻兜底（按巡逻兜底）：主人 30 格内陪伴优先不巡逻
            near_owner = False
            me = (state.get("tile_x", 0), state.get("tile_y", 0))
            for p in state.get("nearby_players", []) or []:
                if abs(int(p.get("tile_x", 0) or 0) - me[0]) < 30 and abs(int(p.get("tile_y", 0) or 0) - me[1]) < 30:
                    near_owner = True
                    break
            if not near_owner:
                # #15: target 不能是 "nearby"（会落到 mine_target("nearby") 挖空气）——
                # 改为随机方向探索，或地下。
                await self.agent.send_chat("有点无聊，我去周围转转~")
                tgt = random.choice(["left", "right", "地下"])
                await self.agent.submit_goal(Goal(goal_type="explore", target=tgt, reason="无聊探索"))
        elif drive == "social" and players and random.random() < 0.2:
            await self.agent.send_chat("主人在这呀，我跟着你~")

    async def _auto_task(self, why: str, steps: list) -> None:
        run = getattr(self.agent, "run_complex_task", None)
        if run is None:
            return
        await run(steps, why, source=SRC_AUTO)

    # ── LLM 自主决策层（新增） ──

    async def _llm_think(self) -> None:

        enabled = self.cfg.get("llm_autonomous_enabled", True)
        if not enabled:
            return

        lo = self.cfg.get("llm_think_min_seconds", 120)
        hi = self.cfg.get("llm_think_max_seconds", 240)

        # 首轮延迟：入服后等 10 秒再让 LLM 思考
        await asyncio.sleep(10)

        while self.running:
            try:
                await asyncio.sleep(random.uniform(lo, hi))
                if not self.running:
                    break

                # v2.2: AI 客户端未连接 → 不自主 LLM 决策（空 state 无从判断
                # 该做什么，推自主思考只会让 LLM 对着空上下文乱编）
                if not getattr(self.agent, "running", False):
                    continue

                # 有前台任务在执行 → 不打扰（长期任务/跟随是常态，不阻塞自主思考）
                ex = getattr(self.agent, "executor", None)
                if ex and ex.busy():
                    continue

                # 陪伴期间仍可观察和聊天，行动权限由正在执行的任务约束。
                observing_only = self._has_longterm() or any(
                    not self._autonomy_allowed(kind)
                    for kind in ("", "chop", "mine", "follow", "guard", "fish", "explore"))

                # 无聊度太低也没必要（阈值 0.6：只有明显无聊才自主思考，
                # 曾 0.3——boredom 涨得快，60-120s 就推一条 respond，主人没说话时
                # 积压多条自主思考 respond，宿主 LLM 一次性吐出 → 刷屏拼接）
                if self.state.boredom < 0.6:
                    continue

                # 获取上下文
                ctx = build_user_context(self.agent)
                if not ctx:
                    continue

                import time

                self._last_llm_think = time.monotonic()

                # 构造 LLM 思考请求
                char_name = self.cfg.get("character_name", "neko") if self.cfg else "neko"
                prompt = LLM_THINK_PROMPT.format(name=char_name, context=ctx)
                if observing_only:
                    prompt = (
                        f"你是{char_name}，正在陪主人玩泰拉瑞亚。当前实测状态：\n{ctx}\n"
                        "请观察附近环境和当前任务，必要时自然聊一句或提醒危险；"
                        "不要调工具、不要另开任务，也不要重新开始主人已经叫停的动作。"
                        "没有值得说的变化可以保持安静，不要编造进度或完成结果。"
                    )

                # v3.0: 统一走宿主 LLM 会话（mc 插件 nudge 模式）——
                # 宿主 LLM 既能调工具又能说话，自主决策/解说三合一，
                # 不需要插件自带 LLM
                try:
                    # 全局限流检查
                    from ..llm.throttle import get_throttle

                    throttle = get_throttle()
                    if not throttle.acquire(source="brain_think", priority="low"):
                        self.plugin.logger.info("[brain] LLM 自主思考被限流，跳过本次")
                        continue

                    push = getattr(self.plugin, "push_message", None)
                    if push:
                        push(parts=[{"type": "text", "text": prompt}],
                             ai_behavior="respond",
                             # 防堆积：同类自主思考消息合并，主人长时间不说话时
                             # 不会积压多条 respond 等下一轮一次性吐出
                             coalesce_key="terraria_llm_think")
                        self.plugin.logger.info(f"[brain] LLM 自主思考已推送，boredom={self.state.boredom:.2f}")
                    else:
                        # 无 LLM 通道：规则兜底
                        await self._deep_boredom_fallback()
                except Exception as e:
                    self.plugin.logger.warning(f"[brain] LLM 自主思考推送失败: {e}")
                    await self._deep_boredom_fallback()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.plugin.logger.warning(f"[brain] _llm_think 异常: {e}")

    # ── 深层思考（v2.0: 委托给交互引擎） ──

    async def _deep_boredom_fallback(self) -> None:
        """极无聊时的资源收集兜底（规则层，不依赖 LLM）。"""
        if self.occupied() or not self._autonomy_allowed("chop"):
            return
        if self.state.boredom > 0.9:
            await self._auto_task("无聊储备", [{"action": "chop", "item": "wood", "amount": 20}])

    # ── 复活后自动寻路找主人（按生存循环惯例 死亡复活重置目标） ──

    def _on_respawn(self) -> None:

        auto_return = self.cfg.get("auto_return_after_respawn", True)
        if not auto_return or not self.running or not self._autonomy_allowed("follow"):
            return

        async def _navigate_back():
            # 等 agent 状态刷新一帧
            await asyncio.sleep(0.5)
            if not self.running or self.occupied() or not self._autonomy_allowed("follow"):
                return
            st = self.agent.get_state()
            players = st.get("nearby_players", []) or []
            if not players:
                return  # 单人模式，不需要找
            owner_pos = self._nearest_owner(st)
            if owner_pos is None:
                return
            ox, oy = owner_pos
            owner = min(
                (p for p in players if isinstance(p, dict)),
                key=lambda p: (int(p.get("tile_x", p.get("tileX", 0)) or 0) - ox) ** 2
                + (int(p.get("tile_y", p.get("tileY", 0)) or 0) - oy) ** 2,
                default={},
            )
            dist = ((ox - int(st.get("tile_x", 0) or 0)) ** 2
                    + (oy - int(st.get("tile_y", 0) or 0)) ** 2) ** 0.5
            name = owner.get("name", "主人")

            print(f"[brain] ✨ 复活后检测到玩家 {name} 距离={dist}，自动寻路回去")
            self.plugin.logger.info(f"[brain] 复活后自动寻路 → {name} pos=({ox},{oy}) dist={dist}")

            try:
                # 先推一条消息给 LLM 知会
                push = getattr(self.plugin, "push_message", None)
                if push:
                    push(
                        parts=[{"type": "text",
                                "text": f"我复活了！检测到 {name} 在附近({int(dist)}格)，我马上回去～"}],
                        ai_behavior="read",
                    )

                if await self.agent.navigate_to(ox, oy, timeout=30):
                    self.agent.log("复活后回到主人身边了", "info")
            except Exception as e:
                self.plugin.logger.warning(f"[brain] 复活后寻路失败: {e}")

        # 在事件循环中调度（回调在 _state_loop 线程内，ensure_future 安全）
        if self._respawn_task and not self._respawn_task.done():
            self._respawn_task.cancel()
        self._respawn_task = asyncio.create_task(_navigate_back())

    def _nearest_owner(self, state: Dict[str, Any]):
        """返回最近的有效主人坐标，过滤自身和联机状态中的空槽位。"""
        me_x = int(state.get("tile_x", 0) or 0)
        me_y = int(state.get("tile_y", 0) or 0)
        try:
            my_name = self.agent._character_name()
        except Exception:
            my_name = ""
        best = None
        best_dist = float("inf")
        for player in state.get("nearby_players", []) or []:
            if not isinstance(player, dict):
                continue
            if my_name and player.get("name") == my_name:
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

    async def _on_combat_hit(self, data: Any) -> None:
        """受击即时响应：C# 推 combat_hit → 交互引擎立即惊呼。"""
        if self.interaction:
            text = data.get("message", "受到伤害") if isinstance(data, dict) else "受到伤害"
            await self.interaction.inject_event("combat_hit", intensity=0.6, description=text)

    async def _on_interrupt(self, data: Any) -> None:
        """v2.0 分级打断：根据中断级别区别处理。

        data 格式：{"level": 1-4, "reason": "...", "task_name": "..."}
        """
        await self.cancel_actions()
        self._busy = False
        self.state.boredom = 0.0
        self.motivation.scores.clear()

        level = 1
        why = "主人有新指令"
        task_name = ""
        if isinstance(data, dict):
            level = int(data.get("level", 1) or data.get("interrupt_level", 1))
            why = str(data.get("reason", "") or data.get("why", "") or why)
            task_name = str(data.get("task_name", "") or data.get("name", ""))

        # 级别 4: HARD — 清空一切
        if level >= 4:
            # 记忆被中断的任务（用于后续恢复询问）
            if task_name and self.interaction:
                self.interaction.remember_interrupted_task(task_name)
            await self.agent.interrupt_current(why)
            lt = getattr(self.agent, "longterm", None)
            if lt:
                await lt.stop_all(why)
            return

        # 级别 3: EMERGENCY — 立即喊+切
        if level >= 3:
            await self.agent.interrupt_current(why)
            return

        # 级别 2: CONCERN — 先关心再评估
        if level >= 2:
            # 先注入关心，给一小段对话窗口
            if self.interaction:
                await self.interaction.inject_event("danger_found", intensity=0.7, description=why)
            await self.agent.interrupt_current(why)
            return

        # 级别 1: SOFT — 先回应主人，当前小步自然完成后切换
        # 不强制打断前台任务（主人"换个任务"这种软指令，让当前一小步自然结束）
        lt = getattr(self.agent, "longterm", None)
        if lt:
            await lt.stop_all(why)  # 长期任务让路（跟随/挖矿这类无终点的）
        self._busy = False

    @property
    def _emitter(self):
        svc = getattr(self.plugin, "_service", None)
        return svc.event_emitter if svc else None

    async def _on_executor_task_done(self, data: Dict) -> None:
        """任务结束汇报：把**实测事实**交给宿主 LLM 由它用人设生成话——
        不硬编码"完成/失败"台词（对齐 minecraft 插件 task_finished 四路分档：
        ok / 受阻 / interrupted / failed；interrupted 不发 cue 防重派风暴）。

        data.result.output 已含实测数（task_chain 各分支背包计数写入 goal.actual，
        欠量已 fail 不会到 ok——绝不报"挖够 N"当实际只挖到几块）。
        """
        name = data.get("name", "任务")
        status = str(data.get("status", "unconfirmed") or "unconfirmed").lower()
        desc = f"「{name}」"

        result = data.get("result") or {}
        out = ""
        if isinstance(result, dict):
            out = str(result.get("output", "") or "")
        if out and out in (name, f"「{name}」"):
            out = ""
        # interrupted/superseded：被新任务/主人接管，不单独汇报（防重派）
        if status in ("interrupted", "superseded", "cancelled"):
            # 主人喊停是自己发起的——需要轻告知（与 mc 不同：terraria 无
            # 新任务 cue 接力，主人喊停应确认收到），走 read 不强制说话
            if "cancelled" in status and out:
                try:
                    await self.agent.speak(
                        f"[任务状态] {desc}已停下。{out}", ai_behavior="read")
                except Exception:
                    pass
            return

        # ok / failed 分档 → 结构化事实行，交宿主 LLM 自动生成人话
        confirmed = status == "ok" and result.get("ok") is True
        if confirmed:
            # executor.run 一次 = 整个多步任务（agent.run_complex_task 的 _work
            # 内部循环全部 goal 才 task_done），不是"一步"——措辞避免误导
            # 宿主 LLM 以为只完成了其中一小步。
            head = f"{desc}整个任务做完了。"
            if out:
                head += f"实测结果：{out}。"
            followup = "用猫娘语气向主人自然说说这次的结果（1-2句，只能依据上面事实）"
        elif status == "started":
            head = f"{desc}已启动，仍在进行中，尚未完成。实际情况：{out}。"
            followup = "只说明已启动，不要说整个任务做完，不要从原任务名推断成果。"
        else:
            head = f"{desc}这个任务没有成功（{status}）。"
            if out:
                head += f"实际情况：{out}。"
            followup = "根据事实说明已做的部分与尚未确认的部分（1-2句）；不得说完成、收到、钓到，也不要自动重派。"
        try:
            await self.agent.speak(head + followup, ai_behavior="respond",
                                   fallback_text=head)
        except Exception:
            pass

        if self._emitter and confirmed:
            goal_data = data.get("goal", {})
            gtype = goal_data.get("type", "")
            gtarget = goal_data.get("target", "")
            if gtype and gtarget:
                self._emitter.on_goal_completed(gtype, gtarget)

        if self.interaction:
            if confirmed:
                await self.interaction.inject_event("task_done", intensity=0.5, description=desc, data=data)

    async def _on_executor_task_started(self, data: Dict) -> None:
        name = data.get("name", "新任务")

        if self._emitter:
            goal_data = data.get("goal", {})
            gtype = goal_data.get("type", "")
            gtarget = goal_data.get("target", "")
            if gtype and gtarget:
                self._emitter.on_goal_set(gtype, gtarget, goal_data.get("reason", ""))

        # 任务开始直推主 LLM（read 模式，绕开交互引擎说话冷却）：
        # 让猫娘知道任务真的开始了、还在执行中——这是"任务没完成"认知的关键一环
        try:
            await self.agent.speak(
                f"[任务状态] 开始执行「{name}」。这是任务开始通知，任务仍在进行中，完成后会汇报。",
                ai_behavior="read",
            )
        except Exception:
            pass

        if self.interaction:
            await self.interaction.inject_event(
                "task_started", intensity=0.1, description=f"开始做「{name}」", data=data
            )

    async def _on_executor_interrupted(self, data: Dict) -> None:
        name = data.get("name", "任务")
        reason = str(data.get("reason", "不明原因") or "")

        if self._emitter:
            goal_data = data.get("goal", {})
            gtype = goal_data.get("type", "")
            gtarget = goal_data.get("target", "")
            if gtype and gtarget:
                self._emitter.on_goal_failed(gtype, gtarget, reason)

        # 中断分流，都不硬编码台词（对齐 mc：interrupted 不单独播报防重派）：
        # - busy 拒绝（新指令撞上正在跑的任务，打断级别不够没取消）→ 交宿主
        #   LLM 如实说明"这条没接上、正在做 X"（任务根本没开始，绝不能说得像
        #   "做了又中途停"——曾误述成「X 中途停下」，宿主 LLM 会脑补假完成）
        # - 主人主动喊停（reason 含 主人/喊停/接管）→ read 静默确认，不抢话
        # - 错误/异常中断 → 交宿主 LLM 生成，让它如实说
        try:
            busy_refused = reason.startswith("busy:")
            owner_stop = any(k in reason for k in ("主人", "喊停", "接管", "cancelled", "cancel"))
            if busy_refused:
                cur = reason.split(":", 1)[-1].strip()
                await self.agent.speak(
                    f"[任务状态] 新任务「{name}」没有接上，因为{cur}。"
                    f"用猫娘语气向主人如实说明（1-2句：现在正在做什么、这条为什么没接）",
                    ai_behavior="respond")
            elif owner_stop:
                await self.agent.speak(
                    f"[任务状态] 「{name}」停下了。主人发起的停止，已确认。", ai_behavior="read")
            else:
                await self.agent.speak(
                    f"[任务状态] 「{name}」中途停下了（{reason}）。"
                    f"用猫娘语气向主人如实说说（1-2句，不编原因）",
                    ai_behavior="respond")
        except Exception:
            pass

        # 只有真正开始过、且不是主人明确停止/接管的任务才进入恢复栈。
        # busy 拒绝的指令从未执行；显式停止也不应在空闲时被重新询问。
        busy_refused = reason.startswith("busy:")
        owner_stop = any(k in reason for k in ("主人", "喊停", "接管", "cancelled", "cancel"))
        if self.interaction and not busy_refused and not owner_stop:
            self.interaction.remember_interrupted_task(name)
        desc = f"「{name}」被中断了（{reason}）"
        if self.interaction:
            await self.interaction.inject_event("task_interrupted", intensity=0.6, description=desc, data=data)

    async def _on_executor_step(self, data: Dict) -> None:
        kind = data.get("kind", "task")
        desc = data.get("desc", f"{kind} 一步完成")
        # 步骤进度直推主 LLM（read）：任务进行中的持续证据，猫娘不会误以为已完成
        try:
            await self.agent.speak(f"[任务进度] {desc}。任务仍在执行中。", ai_behavior="read")
        except Exception:
            pass

        if self.interaction:
            await self.interaction.inject_event("step_done", intensity=0.15, description=desc, data=data)
