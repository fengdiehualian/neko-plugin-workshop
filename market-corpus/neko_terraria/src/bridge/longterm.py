"""长期任务：没有明确终点的常驻任务（跟着我 / 挖铁 / 守着这里）。

与前台有限任务的区别：
  有限任务  —— "挖10个铁"，步骤跑完就结束，占用前台唯一槽位。
  长期任务  —— "跟着我"、"挖铁"（没说多少），只有主人喊停、
                条件达成或环境变化才结束，常驻后台不占前台槽位。

长期任务必须满足三点，否则会拖垮体验：
  1. 可随时被叫停（协作式取消，不留残留动作）
  2. 可被前台任务临时让路（yield），前台做完自己恢复
  3. 有心跳与进度，主人问"你在干嘛"时答得出来
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

# 长期任务状态
LT_RUNNING = "running"    # 正在跑
LT_YIELDED = "yielded"    # 为前台任务让路，暂停中
LT_STOPPED = "stopped"    # 已结束
LT_BLOCKED = "blocked"    # 条件不足，已退出动作循环


@dataclass
class StandingTask:
    """一个常驻任务的运行档案。"""

    name: str                       # 人话名字，如 "跟着主人"
    kind: str                       # follow / mine / guard
    target: str = ""                # 目标物/目标人
    reason: str = ""                # 为什么做
    status: str = LT_RUNNING
    progress: int = 0               # 已完成量（挖到几个 / 跟了多久）
    goal_amount: int = 0            # 0 表示无上限（真·长期）
    started_at: float = field(default_factory=time.time)
    last_beat: float = field(default_factory=time.time)
    note: str = ""
    params: Dict[str, Any] = field(default_factory=dict)  # 额外参数（如守护半径）

    def beat(self, note: str = "") -> None:
        # 心跳：证明任务还活着，同时更新一句人话进度
        self.last_beat = time.time()
        if note:
            self.note = note

    def say(self) -> str:
        # 给主人听的人话进度
        el = int(time.time() - self.started_at)
        if self.goal_amount:
            head = f"{self.name}（{self.progress}/{self.goal_amount}）"
        elif self.progress:
            head = f"{self.name}（已经 {self.progress} 个）"
        else:
            head = self.name
        tail = "，让路等着呢" if self.status == LT_YIELDED else ""
        return f"{head}，做了 {el} 秒{tail}"

    def snapshot(self) -> Dict[str, Any]:
        return {
            "name": self.name, "kind": self.kind, "target": self.target,
            "status": self.status, "progress": self.progress,
            "goal_amount": self.goal_amount, "note": self.note,
            "elapsed": round(time.time() - self.started_at, 1),
            "say": self.say(),
        }


class LongTermManager:
    """长期任务管理器：常驻后台，与前台任务并行且自动让路。

    同一 kind 只允许一个（不会既跟着又跟着），下达新的会顶掉旧的。
    """

    def __init__(self, agent=None) -> None:
        self.agent = agent
        self._tasks: Dict[str, StandingTask] = {}
        self._runners: Dict[str, asyncio.Task] = {}
        self._workers: Dict[str, asyncio.Task] = {}
        self._stop_flags: Dict[str, asyncio.Event] = {}
        self._yield_flag = asyncio.Event()   # 置位 = 前台在忙，长期任务集体让路
        self._start_lock = asyncio.Lock()

    # ---------- 让路控制 ----------
    def request_yield(self) -> None:
        # 前台任务开始：长期任务暂停实际动作，避免抢操作权
        self._yield_flag.set()
        for t in self._tasks.values():
            if t.status == LT_RUNNING:
                t.status = LT_YIELDED
        for worker in self._workers.values():
            if not worker.done():
                worker.cancel()

    async def wait_paused(self) -> None:
        """前台接管前等正在导航/采集的动作退出，保留长期任务档案。"""
        workers = list(self._workers.values())
        if workers:
            await asyncio.gather(*workers, return_exceptions=True)
        if self._tasks and self.agent:
            await self.agent.mod.stop_actions()

    def owns_current_action(self) -> bool:
        current = asyncio.current_task()
        return current in self._workers.values() or current in self._runners.values()

    def release_yield(self) -> None:
        # 前台任务结束：长期任务自动恢复，无需主人重新下令
        self._yield_flag.clear()
        for t in self._tasks.values():
            if t.status == LT_YIELDED:
                t.status = LT_RUNNING

    def yielding(self) -> bool:
        return self._yield_flag.is_set()

    async def wait_turn(self, kind: str) -> bool:
        """长期任务每轮动作前调用：前台忙就等，被停就返回 False。"""
        while self._yield_flag.is_set() or getattr(self.agent, "_in_combat", False):
            if self.should_stop(kind):
                return False
            await asyncio.sleep(0.3)
        if self.should_stop(kind):
            return False
        if self.agent and not self.agent.conn.is_mod_connected():
            task = self._tasks.get(kind)
            if task:
                task.status = LT_BLOCKED
                task.beat("游戏连接已断开，停止使用旧状态执行动作")
            return False
        return True

    # ---------- 状态 ----------
    def should_stop(self, kind: str) -> bool:
        ev = self._stop_flags.get(kind)
        return ev is None or ev.is_set()

    def active(self) -> List[Dict[str, Any]]:
        return [t.snapshot() for t in self._tasks.values()
                if t.status in (LT_RUNNING, LT_YIELDED)]

    def get(self, kind: str) -> Optional[StandingTask]:
        return self._tasks.get(kind)

    def busy_kinds(self) -> List[str]:
        return [k for k, t in self._tasks.items() if t.status != LT_STOPPED]

    def say_all(self) -> str:
        act = [t for t in self._tasks.values() if t.status != LT_STOPPED]
        if not act:
            return ""
        return "、".join(t.say() for t in act)

    def _log(self, msg: str, kind: str = "task") -> None:
        if self.agent:
            self.agent.log(msg, kind)

    # ---------- 生命周期 ----------
    async def start(self, task: StandingTask, loop_fn: Callable) -> Dict[str, Any]:
        async with self._start_lock:
            # 同一角色只有一套移动/物品输入；跟随和砍树不能同时抢操作权。
            await self.stop_all("新长期任务接管")
            if self._runners:
                return {"ok": False, "status": "busy", "output": "旧任务尚未停止，未启动新任务。"}
            return await self._start(task, loop_fn)

    async def _start(self, task: StandingTask, loop_fn: Callable) -> Dict[str, Any]:
        """启动一个长期任务。loop_fn(task) 内部应循环并调用 wait_turn。"""
        logger.info(f"🟢 启动长期任务: {task.name} (kind={task.kind})")
        await self.stop(task.kind, why="换新的长期任务")
        ev = asyncio.Event()
        self._stop_flags[task.kind] = ev
        self._tasks[task.kind] = task
        self._log(f"🟢 启动长期任务: {task.name} (kind={task.kind})", "task")
        logger.info(f"📦 _tasks 现在有: {list(self._tasks.keys())}")
        # 若前台正忙，新长期任务直接以让路状态起步，不抢操作权
        if self._yield_flag.is_set():
            task.status = LT_YIELDED

        async def _wrap() -> None:
            cancelled = False
            try:
                logger.info(f"🚀 任务协程开始执行: {task.name}")
                while not self.should_stop(task.kind):
                    if not await self.wait_turn(task.kind):
                        break
                    worker = asyncio.create_task(loop_fn(task))
                    self._workers[task.kind] = worker
                    try:
                        await worker
                        break
                    except asyncio.CancelledError:
                        # 前台只取消动作子协程；管理协程仍保留进度，待前台结束续做。
                        if asyncio.current_task().cancelling() or self.should_stop(task.kind):
                            raise
                        if not self.yielding():
                            raise
                    finally:
                        if self._workers.get(task.kind) is worker:
                            self._workers.pop(task.kind, None)
                logger.info(f"✅ 任务协程正常结束: {task.name}")
            except asyncio.CancelledError:
                logger.info(f"⚠️ 任务协程被取消: {task.name}")
                cancelled = True
            except Exception as e:
                logger.error(f"❌ 长期任务出错：{task.name} → {e}", exc_info=True)
                self._log(f"长期任务出错：{task.name} → {e}", "warn")
                task.status = LT_BLOCKED
                task.beat(str(e))
            finally:
                blocked = task.status == LT_BLOCKED
                task.status = LT_STOPPED
                if self._tasks.get(task.kind) is task:
                    try:
                        # stop() 会在 runner 退出后统一停止 Mod 动作；取消态
                        # 这里再等一次 TCP 回执会把停止延迟到请求超时。
                        if not cancelled and self.agent and not self.agent.executor.busy():
                            await self.agent.mod.stop_actions()
                    finally:
                        self._tasks.pop(task.kind, None)
                        self._runners.pop(task.kind, None)
                        self._stop_flags.pop(task.kind, None)
                # 停止路径已经由 stop() 处理；取消态不能再等待宿主 LLM
                # 的播报，否则主人喊停时 runner 可能长期占着槽位。
                if not cancelled and self.agent:
                    if blocked:
                        text = (f"[任务受阻] 「{task.name}」已停止，实际进度 {task.progress}。"
                                f"原因：{task.note}。请根据事实向主人自然说明困难，不要自动重派旧任务。")
                    else:
                        text = (f"[任务结束] 「{task.name}」已结束，实际进度 {task.progress}。"
                                f"{task.note}。请根据事实自然告知主人，不要自动重派旧任务。")
                    await self.agent.speak(
                        text, ai_behavior="respond",
                        fallback_text=f"「{task.name}」已结束，实际进度 {task.progress}。{task.note}")

        self._runners[task.kind] = asyncio.ensure_future(_wrap())
        self._log(f"开始长期任务：{task.name}", "task")
        logger.info(f"🎯 任务协程已提交到事件循环: {task.kind}")
        return {"ok": True, "status": "started", "output": f"好的，我{task.name}~"}

    async def stop(self, kind: str, why: str = "") -> bool:
        """停止某类长期任务（协作式，先置位再取消兜底）。"""
        # 对外仍允许用 mine 指代“砍树”长期任务；start() 会将它规范化为 chop，
        # 这里也统一别名，避免停止命令找不到已登记的 runner。
        if kind == "mine" and kind not in self._tasks and "chop" in self._tasks:
            kind = "chop"
        logger.info(f"🛑 stop() 被调用: kind={kind}, why={why}")
        self._log(f"🛑 stop() 被调用: kind={kind}, why={why}", "task")
        t = self._tasks.get(kind)
        if t is None:
            logger.info(f"⚠️ 任务 {kind} 不存在，无需停止")
            self._log(f"⚠️  {kind} 不存在，无需停止", "task")
            return False
        logger.info(f"🔄 停止任务 {t.name} (当前状态: {t.status})")
        self._log(f"停止任务: {t.name} (status={t.status})", "task")
        ev = self._stop_flags.get(kind)
        if ev:
            ev.set()
        r = self._runners.get(kind)
        if r and not r.done():
            logger.info(f"⏹️ 取消任务协程: {kind}")
            self._log(f"取消运行器: {kind}", "task")
            r.cancel()
            try:
                await asyncio.wait([r], timeout=5)
            except Exception:
                pass
            if not r.done():
                self._log(f"任务 {t.name} 仍在退出，保留取消标记", "warn")
                return False
        t.status = LT_STOPPED
        if self._tasks.get(kind) is t:
            self._tasks.pop(kind, None)
            self._runners.pop(kind, None)
            self._stop_flags.pop(kind, None)
        if self.agent:
            await self.agent.mod.stop_actions()
        if why:
            self._log(f"停止长期任务：{t.name}（{why}）", "task")
        self._log(f"✅ {kind} 已停止", "task")
        logger.info(f"✅ 任务 {kind} 已完全停止，_tasks 剩余: {list(self._tasks.keys())}")
        # 任务生命周期推送：长期任务被停止 = 任务结束，通知主 LLM（read 模式，
        # 猫娘感知状态变化即可，不强制说话打断）
        try:
            if self.agent:
                await self.agent.speak(
                    f"[任务状态] 「{t.name}」已停止（{why or '主人喊停'}）。这是状态通知，不是新任务。",
                    ai_behavior="read")
        except Exception:
            pass
        return True

    async def stop_all(self, why: str = "主人喊停") -> List[str]:
        names = []
        for kind, task in list(self._tasks.items()):
            n = task.name
            if await self.stop(kind, why):
                names.append(n)
        return names
