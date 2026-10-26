"""任务链引擎：将目标编排为多步行为（挖矿→合成→给玩家/自穿）。

v0.11（A3）：诚实化——
  - explore 不再"走100格就算完成"：真实找洞→下挖→记录坐标→标记探索
  - gather 按"真捡到物品数"判定
  - 失败不再静默：return (ok, reason) 外，能丢信息的都丢进 agent.log，
    上游 run_complex_task 保证有说话
"""

import asyncio
from dataclasses import dataclass
from typing import List, Optional

from .equipment import EquipmentManager
from .mining import MiningEngine
from .mod_link import ModLink


@dataclass
class Goal:
    goal_type: str
    target: str
    reason: str = ""
    amount: int = 10
    deliver_to_player: bool = False
    craft_first: bool = False
    equip_self: bool = False
    interrupt: bool = False
    report_fail: str = ""  # 步骤失败时向主人汇报的话
    actual: int = 0        # 实际完成量（背包实测/真实计数），供完成播报用实数
    outcome: str = "pending"  # completed / started / partial / unconfirmed / failed
    evidence: str = ""       # 最终汇报只能使用已确认的事实
    recipe_index: Optional[int] = None


def _goal_done(goal: "Goal") -> bool:
    """有数量目标时：实际获得 >= 目标 才算真完成。

    曾各分支用 ">0 即成功"——挖到 1 个也报"挖铁矿x10 完成"（欠量谎报）。
    对齐 mc 插件：完成必须由实测量证实，不足如实带量失败。
    """
    if goal.amount <= 0:
        return True  # 无数量目标（如"跟着我"）只看动作成功
    return goal.actual >= goal.amount


class TaskChain:
    def __init__(self, mining: MiningEngine, mod: ModLink, equip: EquipmentManager, agent=None) -> None:
        self.mining = mining
        self.mod = mod
        self.equip = equip
        self.agent = agent
        self._queue: asyncio.Queue[Goal] = asyncio.Queue(maxsize=1)
        self._current: Optional[Goal] = None
        self._chain: List[str] = []  # 决策链：每步结果，供解释
        self._step: str = ""  # 当前进度 "2/4"
        self._last_ok: bool = True  # 上一步是否成功，用于中止后续

    async def submit(self, goal: Goal) -> None:
        if goal.interrupt and self._current:
            self.mining.cancel()
            if self.agent:
                await self.agent.interrupt_current("新目标接管")
        self.cancel_pending()
        await self._queue.put(goal)

    def cancel_pending(self) -> None:
        """停止时丢弃尚未开始的旧目标，避免下一次循环自动重启。"""
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
                self._queue.task_done()
            except asyncio.QueueEmpty:
                break
        self._last_ok = False

    async def submit_sequence(self, goals: List[Goal]) -> None:
        # 串行多步骤：等上一步真正执行完再下一步；任一步失败则中止后续（不自动重试）
        self._chain = []
        total = len(goals)
        for i, g in enumerate(goals):
            self._step = f"{i + 1}/{total}"
            await self.submit(g)
            await self._queue.join()
            if not self._last_ok:
                self._chain.append(f"{i + 1}.{g.goal_type}:{g.target} 失败，中止")
                if self.agent:
                    self.agent.log(f"多步任务在第{i + 1}/{total}步中止：{g.goal_type} {g.target}", "warn")
                return
            if g.outcome != "completed":
                self._chain.append(g.evidence or "当前步骤尚未完成，后续步骤未执行")
                return
            self._chain.append(f"{i + 1}.{g.goal_type}:{g.target} 完成")
        self._step = ""

    def chain(self) -> List[str]:
        # 决策链：每步做了什么、在哪步停的，供 UI/汇报解释
        return list(self._chain)

    async def run_one(self, goal: Goal) -> bool:
        """直接执行单个目标并返回结果（不经队列），供执行器逐步驱动。"""
        previous_current = self._current
        self._current = goal
        goal.actual = 0
        goal.outcome = "pending"
        goal.evidence = ""
        try:
            if goal.goal_type in ("mine", "chop", "craft", "gather", "fetch", "give", "fish", "combat") and goal.amount <= 0:
                goal.outcome = "failed"
                goal.report_fail = "有限数量任务必须指定大于零的目标数量"
                return False
            self.mining.reset()
            ok = await self._execute(goal)
            if (ok and goal.goal_type in ("mine", "chop", "craft", "gather", "fetch", "give", "fish", "combat")
                    and (goal.actual <= 0 or not _goal_done(goal))):
                ok = False
                goal.report_fail = f"只确认 {goal.actual} 个{goal.target}，要求 {goal.amount} 个，目标未达成"
            if goal.outcome == "pending":
                goal.outcome = "completed" if ok else ("partial" if goal.actual > 0 else "failed")
            if not ok and not goal.report_fail:
                goal.report_fail = goal.evidence or "没有确认目标达成"
            return bool(ok)
        except asyncio.CancelledError:
            goal.outcome = "cancelled"
            raise
        except Exception as e:
            goal.outcome = "unconfirmed"
            goal.report_fail = f"执行或结果核验异常，不能确认完成：{e}"
            if self.agent:
                self.agent.log(f"步骤异常：{goal.goal_type} {goal.target} → {e}", "warn")
            return False
        finally:
            if self._current is goal:
                self._current = previous_current

    async def run_loop(self) -> None:
        while True:
            goal = await self._queue.get()
            try:
                if self.agent:
                    async def _work(info):
                        ok = await self.run_one(goal)
                        return {"ok": ok, "status": "ok" if goal.outcome == "completed" else goal.outcome,
                                "output": goal.report_fail if not ok else self.agent._step_done_text(goal)}
                    result = await self.agent.executor.run(
                        f"{goal.goal_type} {goal.target}", _work)
                    ok = bool(result.get("ok"))
                else:
                    ok = await self.run_one(goal)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                ok = False
                if goal.outcome == "pending":
                    goal.outcome = "failed"
                if not goal.report_fail:
                    goal.report_fail = f"任务执行异常，未确认完成：{e}"
                if self.agent:
                    self.agent.log(f"任务异常：{e}", "warn")
            finally:
                self._queue.task_done()
            self._last_ok = ok

    async def _execute(self, goal: Goal) -> bool:
        # follow：交给长期管理器；前台不能等待它结束，否则 yield 会相互等待。
        if goal.goal_type == "follow":
            try:
                if self.agent:
                    res = await self.agent.start_longterm("follow", reason=goal.reason or "跟着主人")
                    if not res.get("ok"):
                        return False
                    goal.outcome = "started"
                    goal.evidence = "长期跟随已启动，尚未确认到达主人身边"
                    return True
            except asyncio.CancelledError:
                raise
            except Exception:
                pass
            return False

        # explore：探索（"去地下看看"、"帮我找铁矿"）
        if goal.goal_type == "explore":
            return await self._explore(goal)

        # resupply：回基地存箱，动作和结果都由 BaseManager 实际核验。
        if goal.goal_type == "resupply":
            base = getattr(self.agent, "base", None)
            if base is None:
                goal.report_fail = "基地存储系统不可用，未执行回家整理"
                return False
            ok = await base.handle_inventory_full(
                goal.reason or "主人要求回基地整理背包", return_to_owner=True)
            report = getattr(base, "last_full_result", {})
            if report.get("stored", 0) and report.get("returned_to_owner"):
                goal.evidence = "已确认物品存入基地箱并返回主人附近"
            elif report.get("stored", 0):
                goal.evidence = "已确认物品存入基地箱，但未确认返回主人附近"
            else:
                goal.evidence = "未确认存箱成功，不能报告整理完成"
            goal.report_fail = goal.evidence
            return bool(ok and report.get("stored", 0))

        # give：给玩家物品
        if goal.goal_type == "give":
            iid = self.agent.resolve_item(goal.target) if self.agent else -1
            if iid < 0:
                if self.agent:
                    self.agent.log(f"give：不认识物品 {goal.target}", "warn")
                return False
            # 真验证：give 结果曾被丢弃恒 True（给失败也报完成）——假完成红线
            return await self._deliver(goal, iid, goal.amount)

        # wait：等待 N 秒
        if goal.goal_type == "wait":
            wait_time = goal.amount or 5
            await asyncio.sleep(wait_time)
            return True

        # combat：战斗（打最近的敌对怪；v0.8：按 fight_nearest 真实结果判定，不再无条件成功）
        if goal.goal_type == "combat":
            combat = self.agent.combat
            from .item_npc_dict import npc_id
            target = goal.target.strip()
            wanted = npc_id(target) if target else -1
            for _ in range(max(1, goal.amount)):
                st = dict(await self.agent.refresh_state())
                enemies = st.get("nearby_npcs", []) or []
                if target not in ("", "敌人", "敌怪", "怪物", "附近的怪物", "目标"):
                    enemies = [e for e in enemies if
                               (wanted >= 0 and e.get("type") == wanted)
                               or str(e.get("name", "")).casefold() == target.casefold()]
                st["nearby_npcs"] = enemies
                if not await combat.fight_nearest(st, timeout=30):
                    goal.report_fail = f"已确认击败 {goal.actual} 个{target or '敌人'}，未确认达到 {max(1, goal.amount)} 个目标"
                    return False
                goal.actual += 1
            goal.evidence = f"已确认击败 {goal.actual} 个{target or '敌人'}"
            return True

        # gather：收集掉落物（v0.11：按真捡到数判定）
        if goal.goal_type == "gather":
            iid = self.agent.resolve_item(goal.target)
            generic = goal.target in ("", "物品", "掉落物", "附近掉落物", "掉落")
            if iid <= 0 and not generic:
                goal.report_fail = f"无法识别要收集的物品：{goal.target}"
                return False
            before = await self.mod.get_inventory()
            await self.mod.collect_items(radius=600, item_id=iid if iid > 0 else None)
            after = await self.mod.get_inventory()
            self.agent._inv_full = after
            if iid > 0:
                goal.actual = max(0, self._count(after, iid) - self._count(before, iid))
            else:
                ids = {it.get("id") for k in ("hotbar", "inventory") for it in after.get(k, [])}
                goal.actual = sum(max(0, self._count(after, i) - self._count(before, i)) for i in ids if i)
            goal.evidence = f"背包实际新增 {goal.actual} 个{goal.target or '物品'}"
            goal.report_fail = f"{goal.evidence}，未达到要求的 {goal.amount} 个"
            return goal.actual > 0 and _goal_done(goal)

        # chop：真砍树（什么任务用什么工具 → 斧头）
        if goal.goal_type == "chop":
            try:
                life = getattr(self.agent, "life", None)
                if life:
                    amount = goal.amount or 10
                    iid = self.agent.resolve_item(goal.target or "木材")
                    if iid <= 0:
                        goal.report_fail = f"无法识别要收集的木材：{goal.target}"
                        return False
                    got = await life.chop_wood(target=amount, item_id=iid)
                    goal.actual = int(got or 0)
                    if goal.actual <= 0:
                        goal.report_fail = life.last_failure or "砍树后没有确认到新增木材"
                        return False
                    if goal.actual < goal.amount:
                        goal.report_fail = f"只砍到 {goal.actual} 个木材（要 {goal.amount} 个）"
                        return False
                    base = getattr(self.agent, "base", None)
                    if base is not None and base.inventory_nearly_full():
                        # 砍树结果已经核验后再整理，避免把木材存箱导致本步骤
                        # 的实际产量统计被清零。
                        if not await base.handle_inventory_full("砍树后背包空间不足"):
                            goal.report_fail = (
                                f"已确认砍到 {goal.actual} 个{goal.target}，"
                                "但背包整理或返回主人未完成")
                            return False
                    return True
                return False
            except Exception as exc:
                goal.report_fail = f"砍树执行异常，未确认获得目标物：{exc}"
                return False

        # fish：真钓鱼（工具 → 钓竿）
        if goal.goal_type == "fish":
            try:
                life = getattr(self.agent, "life", None)
                if life:
                    attempts = max(1, int(goal.amount or 0) or 3)
                    ok = await life.fish(attempts=attempts)
                    goal.outcome = "unconfirmed" if ok else "failed"
                    goal.report_fail = (f"已进行 {life.last_cast_count} 次抛竿/收竿操作，但没有鱼获核验，"
                                        f"不能确认钓到 {goal.amount} 条{goal.target or '鱼'}") if ok else (
                                            life.last_failure or "未能完成钓鱼动作")
                    return False
                return False
            except Exception as exc:
                goal.report_fail = f"钓鱼执行异常，未确认鱼获：{exc}"
                return False

        # 没有实际执行器的行为不能凭空成功。
        if goal.goal_type == "social":
            goal.report_fail = "此任务没有可核验的社交动作，未执行"
            return False

        # 爬升/移动：target 形如 "x,y" 走坐标；否则按目标名（方向词/某物）处理
        if goal.goal_type in ("climb", "goto"):
            tgt = goal.target or ""
            if "," in tgt:
                try:
                    sx, sy = tgt.split(",")
                    tx, ty = int(sx.strip()), int(sy.strip())
                except (ValueError, AttributeError):
                    tx = ty = None
                if tx is not None and ty is not None:
                    if goal.goal_type == "climb":
                        return await self.agent.climb_to(tx, ty)
                    return await self.agent.navigate_to(tx, ty)
            # #4: 非坐标目标 → 方向词走 explore 语义，其他尝试找物
            return await self._goto_by_name(goal, tgt)

        # 去箱子取物：先找到含该物的最近箱子，再取
        if goal.goal_type == "fetch":
            chest = await self.agent.nearest_chest_with(goal.target)
            if chest is None:
                if self.agent:
                    self.agent.log(f"fetch：附近没有含 {goal.target} 的箱子", "warn")
                return False
            iid = self.agent.resolve_item(goal.target)
            before = await self.mod.get_inventory()
            ok = await self.agent.take_from_chest(goal.target, chest, goal.amount)
            after = await self.mod.get_inventory()
            self.agent._inv_full = after
            goal.actual = max(0, self._count(after, iid) - self._count(before, iid))
            goal.evidence = f"从箱子取物后，背包实际新增 {goal.actual} 个{goal.target}"
            goal.report_fail = f"{goal.evidence}，未取够要求的 {goal.amount} 个"
            if not ok:
                goal.outcome = "unconfirmed"
                goal.report_fail = f"{goal.evidence}，但取箱回执或数量核验失败，未确认完成取物"
            return ok and goal.actual > 0 and _goal_done(goal)

        if goal.goal_type not in ("mine", "craft"):
            goal.report_fail = f"未实现的任务类型：{goal.goal_type}，没有执行"
            return False
        # 挖矿/合成流程
        iid = self.agent.resolve_item(goal.target) if self.agent else -1
        if iid < 0:
            if self.agent:
                self.agent.log(f"不认识目标物品：{goal.target}", "warn")
            return False
        if goal.craft_first or goal.goal_type == "craft":
            before = await self.mod.get_inventory()
            crafted = await self.mod.craft(item_id=iid, amount=goal.amount,
                                           recipe_index=goal.recipe_index)
            await self.mod.collect_items(radius=160, item_id=iid)
            after = await self.mod.get_inventory()
            self.agent._inv_full = after
            gained = max(0, self._count(after, iid) - self._count(before, iid))
            goal.actual = min(max(0, int(crafted or 0)), gained)
            goal.evidence = f"合成回执产出 {crafted} 个，背包净增 {gained} 个{goal.target}，确认获得 {goal.actual} 个"
            if goal.actual <= 0:
                goal.report_fail = goal.evidence + "，未确认合成收获"
                return False  # 材料不足/合成失败，不假装成功
            if goal.actual < goal.amount:
                # 材料不够做满：诚实报实际做成的量
                goal.report_fail = f"{goal.evidence}（要求 {goal.amount} 个，尚未满足）"
                return False
            if goal.deliver_to_player:
                return await self._deliver(goal, iid, goal.amount)
            if goal.equip_self:
                return await self._equip_and_verify(goal, iid)
            return True
        mined_iid, mined = await self.mining.mine_target(goal.target, goal.amount)
        goal.actual = int(mined or 0)
        if goal.actual <= 0:
            if self.agent:
                self.agent.log(f"mine：附近没找到 {goal.target} 或挖不到", "warn")
            return False
        if goal.actual < goal.amount:
            # 矿脉挖尽/挖不够：诚实报实际量（假完成红线——绝不报"挖够 N"）
            goal.report_fail = f"{goal.target}只确认挖到 {goal.actual} 个（要 {goal.amount} 个），本次未达标"
            return False
        if goal.deliver_to_player:
            return await self._deliver(goal, mined_iid, goal.amount)
        elif goal.equip_self:
            return await self._equip_and_verify(goal, mined_iid)
        return True

    async def _equip_and_verify(self, goal: Goal, iid: int) -> bool:
        await self.equip.auto_equip()
        inv = await self.mod.get_inventory()
        self.agent._inv_full = inv
        if any(it.get("id") == iid and int(it.get("stack", 0) or 0) > 0
               for it in inv.get("equipped", [])):
            goal.evidence += f"；装备栏已确认穿戴{goal.target}"
            return True
        goal.report_fail = f"已获得 {goal.actual} 个{goal.target}，但装备栏未确认穿戴该物品"
        return False

    @staticmethod
    def _count(inv, iid) -> int:
        return sum(max(0, int(it.get("stack", 0) or 0))
                   for k in ("hotbar", "inventory") for it in inv.get(k, [])
                   if it.get("id") == iid)

    async def _deliver(self, goal: Goal, iid: int, amount: int) -> bool:
        before = await self.mod.get_inventory()
        ok = await self.equip.give_to_player(iid, amount)
        after = await self.mod.get_inventory()
        self.agent._inv_full = after
        dropped = max(0, self._count(before, iid) - self._count(after, iid))
        goal.actual = 0  # 背包减少不是主人收到物品的证据。
        goal.outcome = "unconfirmed"
        goal.evidence = f"已丢出 {dropped} 个{goal.target}供拾取" if ok else f"转交操作未完成，背包减少 {dropped} 个{goal.target}"
        goal.report_fail = goal.evidence + "；未确认主人拾取，不能报告交付完成"
        return False

    # ---------------- 按目标名移动（#4 goto 无坐标时） ----------------

    async def _goto_by_name(self, goal: Goal, tgt: str) -> bool:
        """goto/climb 目标不是坐标时：方向词走 explore，其他找物导航。"""
        try:
            st = self.agent.get_state()
            sx = int(st.get("tile_x", 0) or 0)
            sy = int(st.get("tile_y", 0) or 0)
        except Exception:
            sx, sy = 0, 0

        t = (tgt or "").strip()
        # 方向词
        if t in ("左", "左边", "left", "west"):
            tx = sx - 100  # 往左 = x 减小（曾误写 +100，往左却向右跑）
            return bool(await self.agent.navigate_to(tx, sy, timeout=30))
        if t in ("右", "右边", "right", "east"):
            tx = sx + 100
            return bool(await self.agent.navigate_to(tx, sy, timeout=30))
        if t in ("地下", "下方", "down", "underground"):
            # 复用探索的下挖逻辑
            return await self._explore(Goal(goal_type="explore", target="地下",
                                            reason=goal.reason))
        if t in ("上方", "上面", "up"):
            ty = sy - 30
            return bool(await self.agent.navigate_to(sx, ty, timeout=30))
        # 目标物：找最近的该物品箱子/矿点导航过去
        try:
            ores = await self.agent.mod.find_ore(radius=60)
            if ores:
                # 尽量匹配目标物对应的矿（tile 类型），匹配不到就取最近的矿
                from .item_npc_dict import tile_type_of
                want = tile_type_of(t, registry=getattr(self.agent, "registry", None))
                pick = None
                for o in ores:
                    if want and int(o.get("type", 0) or 0) == want:
                        pick = o
                        break
                if pick is not None:
                    return bool(await self.agent.navigate_to(
                        pick["x"], pick["y"], timeout=30))
        except Exception:
            pass
        goal.report_fail = f"没有定位到指定目的地「{t}」，未到达"
        return False

    # ---------------- 探索（A3 诚实化） ----------------

    async def _explore(self, goal: Goal) -> bool:
        """真实探索：找洞→下挖→记录→标记，不再"走100格就完成"。

        方向探索走到目标附近并确认有移动就算完成（导航成功即真的到了）；
        地下探索真正往下挖一段并记录坐标；
        目标性探索（找某物）真的去挖一次并计数。
        """
        target = goal.target or ""
        try:
            st = self.agent.get_state()
            sx = int(st.get("tile_x", 0) or 0)
            sy = int(st.get("tile_y", 0) or 0)
        except Exception:
            sx, sy = 0, 0

        if target in ("left", "right", "左", "右"):
            direction = -1 if target in ("left", "左") else 1
            tx = sx + direction * 100
            ok = bool(await self.agent.navigate_to(tx, sy, timeout=30))
            if self.agent:
                self.agent.log(
                    f"探索：向{'左' if direction < 0 else '右'}走到 ({tx},{sy}) {'成功' if ok else '失败'}", "nav"
                )
            return ok

        if target in ("附近", "周围", "目标", ""):
            # 附近探索：真实走动一段（人物必须动起来，不能站着算完成）。
            # 先看附近有没有可去的有趣点（矿/树），没有就往一侧走 ~50 格。
            try:
                ores = await self.agent.mod.find_ore(radius=30)
                if ores:
                    o = ores[0]
                    return bool(await self.agent.navigate_to(
                        int(o.get("x", 0) or 0), int(o.get("y", 0) or 0),
                        timeout=30))
            except Exception:
                pass
            try:
                trees = await self.agent.mod.find_trees(radius=30)
                if trees:
                    t = trees[0]
                    return bool(await self.agent.navigate_to(
                        int(t.get("x", 0) or 0), int(t.get("y", 0) or 0),
                        timeout=30))
            except Exception:
                pass
            # 往世界中间方向走（不贴边），确认真的到达才算完成
            direction = 1 if sx < 4000 else -1
            tx = sx + direction * 50
            ok = bool(await self.agent.navigate_to(tx, sy, timeout=30))
            if self.agent:
                self.agent.log(
                    f"探索：向{'右' if direction > 0 else '左'}走了 {abs(tx - sx)} 格"
                    f"{'（成功）' if ok else '（失败）'}", "nav")
            return ok

        if target in ("地下", "下方", "underground"):
            # v0.5: 完整地下探索闭环（找洞→下挖→挖矿→回家），替代单纯下挖 25 格
            explorer = getattr(self.agent, "explorer", None)
            if explorer is not None:
                try:
                    ok = await explorer.explore(direction=1, max_time=180.0)
                    goal.evidence = explorer.last_result
                    if not ok:
                        goal.report_fail = f"{goal.evidence or '没有确认地下移动进展'}，未达到地下探索目标"
                    return ok
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    if self.agent:
                        self.agent.log(f"地下探索异常: {e}", "warn")
                    return False
            # 兜底：真下挖（有镐才挖，否则诚实失败）
            down = 0
            for _ in range(5):
                if self.agent.executor and self.agent.executor.should_stop():
                    break
                st = await self.agent.refresh_state()
                mx = int(st.get("tile_x", 0) or 0)
                my = int(st.get("tile_y", 0) or 0)
                moved = False
                for dy in range(1, 4):
                    try:
                        if await self.mod.break_tile(mx, my + dy):
                            down += 1
                            moved = True
                            break
                    except Exception:
                        break
                if not moved:
                    break
                await asyncio.sleep(0.6)
            if self.agent:
                self.agent.log(f"探索：向下挖了 {down} 格", "nav")
            final = await self.agent.refresh_state()
            depth = int(final.get("tile_y", sy)) - sy
            goal.evidence = f"向下挖掘后实测下降 {depth} 格"
            goal.report_fail = goal.evidence + "，尚未确认完成地下探索"
            return depth >= 30

        # 目标性探索：找某物 → 真的挖一次
        try:
            _iid, mined = await self.mining.mine_target(target, max(1, goal.amount))
            goal.actual = mined
            goal.evidence = f"目标探索确认采集 {mined} 个{target}"
            goal.report_fail = f"{goal.evidence}，未达到要求的 {goal.amount} 个"
            return mined > 0 and _goal_done(goal)
        except Exception:
            return False
