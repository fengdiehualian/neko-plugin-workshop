"""任务执行器：任务生命周期与占用仲裁的唯一权威（谁在做、能否打断、怎么停）。"""

import asyncio
import inspect
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from ..polish.human_timing import HumanTiming

# 来源优先级：主人命令 > 自主行为。低优先级不能打断高优先级
SRC_OWNER = "owner"
SRC_AUTO = "auto"
_PRIORITY = {SRC_OWNER: 10, SRC_AUTO: 1}


@dataclass
class TaskInfo:
    name: str
    source: str = SRC_AUTO
    phase: str = "think"          # think(想) / plan(处理) / act(做) / done
    steps: List[str] = field(default_factory=list)
    step_index: int = 0
    total_steps: int = 0
    started_at: float = field(default_factory=time.time)
    note: str = ""

    def snapshot(self) -> Dict[str, Any]:
        return {
            "name": self.name, "source": self.source, "phase": self.phase,
            "step": f"{self.step_index}/{self.total_steps}" if self.total_steps else "",
            "steps": list(self.steps), "note": self.note,
            "elapsed": round(time.time() - self.started_at, 1),
        }


class TaskExecutor:
    """单槽位执行器：同一时刻只有一个任务在跑。

    主人命令可抢占自主行为；自主行为遇到任何进行中的任务则让位不触发。

    v2.0: 回调系统——step_done / task_done / interrupted 事件通知，
          供交互引擎订阅，实现干活汇报 / 任务打断对话。
    """

    def __init__(self, agent=None) -> None:
        self.agent = agent
        self._current: Optional[TaskInfo] = None
        self.timing = HumanTiming()  # v2.1: 人类化延迟
        self._task: Optional[asyncio.Task] = None
        self._cancel = asyncio.Event()
        self._lock = asyncio.Lock()
        self._last: Optional[Dict[str, Any]] = None
        # 回调系统：事件名 → [callable]
        self._callbacks: Dict[str, list] = {
            "step_done":    [],
            "task_done":    [],
            "interrupted":  [],
            "task_started": [],
        }

    # --- 状态查询 ---
    def busy(self) -> bool:
        return self._current is not None

    def current(self) -> Optional[Dict[str, Any]]:
        return self._current.snapshot() if self._current else None

    def last_result(self) -> Optional[Dict[str, Any]]:
        return self._last

    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def _log(self, msg: str, kind: str = "task") -> None:
        if self.agent:
            self.agent.log(msg, kind)

    # --- 占用仲裁 ---
    def can_start(self, source: str) -> bool:
        if self._current is None:
            return True
        return _PRIORITY.get(source, 0) > _PRIORITY.get(self._current.source, 0)

    async def cancel_current(self, why: str = "") -> bool:
        # 真正取消：置取消位 + 取消协程 + 停下 mod 侧动作
        if self._current is None:
            return False
        info = self._current
        name = info.name
        runner = self._task
        self._cancel.set()
        if self.agent and getattr(self.agent, "mining", None):
            self.agent.mining.cancel()
        if runner and not runner.done():
            runner.cancel()
            # 只等它真正停下，异常由 run() 那侧接住，这里不重复消费
            try:
                await asyncio.wait([runner], timeout=5)
            except Exception:
                pass
        if self._current is info and (runner is None or runner.done()):
            self._current = None
            self._task = None
        if self.agent and (runner is None or not runner.done()):
            await self.agent.mod.stop_actions()
        if runner is not None and not runner.done():
            self._log(f"任务仍在退出：{name}", "warn")
            return False
        self._log(f"任务被打断：{name}（{why}）", "warn")
        return True

    async def run(self, name: str, coro_fn: Callable, source: str = SRC_AUTO,
                  steps: Optional[List[str]] = None) -> Dict[str, Any]:
        """执行一个任务。coro_fn 接收本 TaskInfo，可用 should_stop() 协作式退出。"""
        async with self._lock:
            if not self.can_start(source):
                # #7: busy 拒绝也要发 interrupted 通知——否则 fire-and-forget 的
                # LLM 工具（llm_mine/llm_task/llm_chain）会永久留下"进行中"的假任务，
                # brain._on_executor_interrupted 永不触发。
                cur = self._current.name if self._current else ""
                self._log(f"任务被拒（忙）：{name}，当前「{cur}」", "warn")
                await self.notify(
                    "interrupted", name=name, reason=f"busy:当前正在做「{cur}」")
                return {"ok": False, "status": "busy", "output": f"正忙着「{cur}」，这条先不接"}
            if self._current is not None:
                await self.cancel_current(f"被{source}的「{name}」接管")
                if self._current is not None:
                    return {"ok": False, "status": "busy", "output": "旧任务仍在退出，新任务尚未开始。"}
            self._cancel.clear()
            info = TaskInfo(name=name, source=source, steps=steps or [],
                            total_steps=len(steps or []))
            self._current = info
            lt = getattr(self.agent, "longterm", None) if self.agent else None
            if lt:
                lt.request_yield()
            async def _work():
                try:
                    if lt:
                        await lt.wait_paused()
                    return await coro_fn(info)
                finally:
                    # 清理完成才能释放前台，否则旧动作的停止命令会截断恢复的长期任务。
                    if self.agent:
                        await self.agent.mod.stop_actions()
            # 把实际工作放进独立 task，这样打断时取消的是工作本身而非调用方
            inner = asyncio.ensure_future(_work())
            self._task = inner

        self._log(f"开始任务：{name}", "task")
        # 前台一开工，长期任务（跟随/挖矿）自动让路，避免抢操作权
        try:
            await self.notify("task_started", name=name, source=source, steps=steps)
            result = await inner
            if isinstance(result, dict):
                out = result
            else:
                # 非 dict 结果（bool/None）不代表成功——曾一律包 ok:True 导致
                # navigate 返回 False 也算"完成"。假完成红线：非 dict 视为失败。
                self._log(f"任务 {name} 返回非 dict({result!r})，按失败处理", "warn")
                out = {"ok": False, "status": "error",
                       "output": f"任务内部返回值异常（{result!r}）"}
        except asyncio.CancelledError:
            if not inner.done():
                inner.cancel()
                await asyncio.gather(inner, return_exceptions=True)
            self._last = {"ok": False, "status": "cancelled", "output": f"「{name}」被打断了"}
            if self._current is info:
                self._current = None
                self._task = None
            await self.notify("interrupted", name=name, reason="cancelled")
            if lt and not self.busy():
                lt.release_yield()
            return self._last
        except Exception as e:
            # 异常任务只发 interrupted（不发 task_done，避免事件双发）
            self._log(f"任务异常：{name} → {e}", "warn")
            out = {"ok": False, "status": "error", "output": f"「{name}」出错了：{e}"}
            self._last = out
            if self._current is info:
                self._current = None
                self._task = None
            await self.notify("interrupted", name=name, reason=f"error:{e}")
            return out
        finally:
            if self._current is info:
                info.phase = "done"
                self._current = None
                self._task = None
            # 前台空了才恢复长期任务，避免被接管时提前恢复
            if lt and not self.busy():
                lt.release_yield()

        # 成功必须显式声明；空 dict、缺字段、"false" 字符串均不能默认为成功。
        out["ok"] = out.get("ok") is True
        status = str(out.get("status") or ("ok" if out["ok"] else "unconfirmed"))
        if status not in ("ok", "started"):
            out["ok"] = False
        elif not out["ok"]:
            status = "unconfirmed"
        out["status"] = status
        if not out.get("output"):
            out["output"] = "任务未提供可确认的结果"
            out["ok"] = False
            out["status"] = "unconfirmed"
        self._last = out
        self._log(f"任务结束：{name} → {out.get('status')}", "task")
        await self.notify("task_done", name=name, status=out.get("status"), result=out)
        return out

    def should_stop(self) -> bool:
        # 任务内部循环用它做协作式退出，保证能被主人随时叫停
        # 取消位只属于被取消的前台任务，不能污染后续长期/空闲动作。
        return self._current is not None and self._cancel.is_set()

    # ── 回调系统（v2.0 交互引擎挂钩） ───────────────────────

    def on(self, event: str, cb) -> None:
        """注册事件回调。event: step_done / task_done / interrupted / task_started"""
        if event in self._callbacks and cb not in self._callbacks[event]:
            self._callbacks[event].append(cb)

    def off(self, event: str, cb) -> None:
        """移除事件回调。"""
        if event in self._callbacks and cb in self._callbacks[event]:
            self._callbacks[event].remove(cb)

    async def notify(self, event: str, **data) -> None:
        """通知所有订阅者。data 里放 event 相关字段。

        async 回调 fire-and-forget（create_task），不阻塞任务执行流。
        """
        if event not in self._callbacks:
            return
        data["event"] = event
        for cb in list(self._callbacks[event]):
            try:
                result = cb(data)
                if inspect.isawaitable(result):
                    task = asyncio.create_task(result)
                    task.add_done_callback(self._callback_done)
            except Exception as exc:
                self._log(f"任务回调失败：{event}: {exc}", "warn")

    def _callback_done(self, task: asyncio.Task) -> None:
        if task.cancelled():
            return
        try:
            error = task.exception()
        except Exception as exc:
            self._log(f"读取任务回调异常失败：{exc}", "warn")
            return
        if error is not None:
            self._log(f"任务回调异常：{error}", "warn")
