"""
Claude Code 异步任务管理器

v0.6.0 起插件全异步化：所有执行一律后台任务，无超时限制
（Claude Code 常执行半小时到一小时以上的长任务）。
任务完成后通过 on_complete 回调主动推送结果给调用方（插件
再经 push_message 注入对话频道，猫娘无需一直轮询）。

v0.7.0 会话与轮询机制：
- 每个会话以 Claude Code 原生 UUID 为唯一标识（session_id 即 UUID）。
  新会话 UUID 由 CLI init 事件返回，submit 经 wait_session_id 短暂等待
  捕获后立即回传猫娘；resume 会话 UUID 提交时已知，立即返回。
- 最多同时 2 个活跃会话（_max_concurrent=2），与 CC 进程一一对应；
  list_active 暴露活跃列表，clear_all/clear_one 清理（杀 CC 进程）。
- 内置 _poll_loop 轮询安全网（默认 5 分钟，可调）：按各任务自身
  poll_interval_sec 周期检测，若已结束且未推送则补推，确保结果稳定送达。
  on_complete 立即推送为主路径，_poll_loop 为安全网，_pushed 去重绝不重复。
- 插件重启后 _tasks 内存态为空，配合插件主类清空 SessionManager 持久化
  索引，确保不会在用户不知情下静默续跑旧会话。

其他能力：
- 失败时自动用 session_id 续跑（--resume）而非重开新会话
- wait_for 智能等待（自适应间隔），供猫娘想提前盯时使用
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

from .errors import is_retryable
from .executor import build_cli_invocation
from .models import ExecuteResult
from .parser import ClaudeOutputParser

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 轮询安全网常量
# ---------------------------------------------------------------------------
DEFAULT_POLL_INTERVAL = 300.0
"""默认轮询间隔（秒）= 5 分钟。猫娘可用 claude_code_set_poll_interval 调整，
或在每次提交/追加指令时传 poll_interval_sec 按预期任务时长覆盖。"""

MIN_POLL_INTERVAL = 15.0
"""轮询间隔下限（秒）。过短会让安全网空转，夹到 15s。"""

POLL_TICK = 30.0
"""_poll_loop 唤醒粒度（秒）。每 30s 遍历一次任务，按各任务自身 poll_interval_sec
判断是否到达轮询周期，避免为每个任务各起一个定时器。"""


class TaskStatus(str, Enum):
    """任务状态"""

    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    ERROR = "error"
    CANCELLED = "cancelled"


@dataclass
class TaskRecord:
    """任务记录"""

    task_id: str
    prompt: str
    cwd: str
    model: str = ""
    effort: str = ""
    max_turns: int = 0
    resume_session_id: str = ""  # 提交时指定的续跑会话（空=新会话）
    session_id: str = ""  # 执行过程中从 CLI 流中捕获的会话 ID
    status: TaskStatus = TaskStatus.PENDING
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    result: Optional[ExecuteResult] = None
    error_message: Optional[str] = None
    _task: Optional[asyncio.Task] = field(default=None, repr=False)
    _parser: Optional[Any] = field(default=None, repr=False)  # 运行中实时进度来源
    poll_interval_sec: float = 300.0
    """该任务的轮询间隔（秒）。完成推送安全网的检查周期，默认 5 分钟。

    猫娘可在提交/追加指令时按预期任务时长调整（短任务设短、长任务设长），
    不传则用插件默认值（可用 claude_code_set_poll_interval 调整默认）。
    """
    _pushed: bool = field(default=False, repr=False)
    """完成结果是否已推送给猫娘。

    on_complete 立即推送与 _poll_loop 安全网共用此标记去重，
    确保同一任务的结果只推送一次（除非首次推送失败回退重试）。
    """
    _last_poll_at: float = field(default_factory=time.time, repr=False)
    """上次轮询检查时间戳。_poll_loop 据此判断是否到达该任务的轮询周期。"""

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典（用于返回给调用方）"""
        elapsed = 0.0
        if self.started_at:
            end_time = self.finished_at or time.time()
            elapsed = end_time - self.started_at

        data = {
            "task_id": self.task_id,
            "status": self.status.value,
            "created_at": self.created_at,
            "elapsed": round(elapsed, 2),
            "poll_interval_sec": self.poll_interval_sec,
        }

        if self.session_id:
            data["session_id"] = self.session_id

        # 运行中：附带实时进度（已输出的 assistant 消息数与最近动态），
        # 便于猫娘向用户汇报「任务在干吗」而不是干等
        if self.status in (TaskStatus.PENDING, TaskStatus.RUNNING):
            progress: Dict[str, Any] = {"message_count": 0}
            parser = self._parser
            if parser is not None:
                try:
                    messages = getattr(parser, "messages", None) or []
                    progress["message_count"] = len(messages)
                    for msg in reversed(messages):
                        if getattr(msg, "text", ""):
                            progress["last_activity"] = msg.text[:300]
                            break
                except Exception:
                    pass
            data["progress"] = progress

        if self.status == TaskStatus.DONE and self.result:
            data["result"] = self.result.to_llm_payload()
        elif self.status == TaskStatus.ERROR:
            data["error"] = self.error_message or "Unknown error"
        elif self.status == TaskStatus.CANCELLED:
            data["error"] = "Task was cancelled"

        return data


class TaskManager:
    """
    Claude Code 任务管理器

    功能：
    - 提交任务到后台执行（asyncio.create_task），无超时限制
    - 查询任务状态和结果（运行中含实时进度）
    - 取消正在运行的任务
    - 任务到达终态（完成/失败）时触发 on_complete 回调，
      供插件主动 push 结果给猫娘（无需轮询到底）
    - 自动清理过期任务（结果保留 1 小时）
    - 并发控制（最多同时 3 个任务）
    """

    def __init__(
        self,
        executor,
        config,
        *,
        logger=None,
        env_provider=None,
        on_complete: Optional[Callable[["TaskRecord"], None]] = None,
    ):
        self._executor = executor
        self._config = config
        self.logger = logger or logging.getLogger(__name__)
        # env_provider: 无参 callable，返回激活 provider 的环境变量覆盖
        self._env_provider = env_provider
        # on_complete: 任务到达终态（DONE/ERROR）时的回调，入参为 TaskRecord。
        # 回调内部异常不影响任务状态（推送失败只记日志）。
        self._on_complete = on_complete
        self._tasks: Dict[str, TaskRecord] = {}
        self._lock = asyncio.Lock()
        self._cleanup_task: Optional[asyncio.Task] = None
        self._poll_task: Optional[asyncio.Task] = None
        self._max_concurrent = 2  # 猫娘最多同时管理 2 个会话（与 CC 进程一一对应）
        self._result_ttl = 3600  # 结果保留时间（秒）：长任务完成后猫娘可能过一阵才来取
        self._default_poll_interval = DEFAULT_POLL_INTERVAL  # 默认轮询间隔，可被 claude_code_set_poll_interval 调整

    async def start(self):
        """启动清理任务与轮询安全网"""
        if self._cleanup_task is None:
            self._cleanup_task = asyncio.create_task(self._cleanup_loop())
        if self._poll_task is None:
            self._poll_task = asyncio.create_task(self._poll_loop())
            self.logger.info("TaskManager started (poll_interval=%ss)", self._default_poll_interval)

    async def stop(self):
        """停止所有任务"""
        # 取消清理任务
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
            self._cleanup_task = None

        # 取消轮询安全网
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
            self._poll_task = None

        # 取消所有未完成任务（PENDING 在队列中、RUNNING 在执行中，
        # 都必须取消，避免插件关闭后留下无人管控的 CLI 进程）
        async with self._lock:
            for record in self._tasks.values():
                if (
                    record.status in (TaskStatus.PENDING, TaskStatus.RUNNING)
                    and record._task
                ):
                    record._task.cancel()
                    try:
                        await record._task
                    except (asyncio.CancelledError, Exception):
                        pass
            self._tasks.clear()

        self.logger.info("TaskManager stopped")

    async def submit(
        self,
        prompt: str,
        cwd: str = "",
        model: str = "",
        effort: str = "",
        max_turns: int = 0,
        resume_session_id: str = "",
        poll_interval_sec: float = 0.0,
    ) -> TaskRecord:
        """
        提交任务到后台执行

        Parameters
        ----------
        resume_session_id:
            由调用方（插件主类）根据 session_mode 预先解析：
            非空时任务会以 ``--resume <id>`` 续跑该会话；空则新开会话。
        poll_interval_sec:
            本任务的轮询安全网间隔（秒）。>0 时覆盖默认值；
            <=0 时用 self._default_poll_interval。最终夹到 >= MIN_POLL_INTERVAL。

        Returns:
            TaskRecord: 任务记录（包含 task_id 和初始状态）
        """
        # 在锁内检查并发限制并创建任务记录
        async with self._lock:
            active_count = sum(
                1
                for t in self._tasks.values()
                if t.status in (TaskStatus.PENDING, TaskStatus.RUNNING)
            )
            if active_count >= self._max_concurrent:
                raise RuntimeError(
                    f"已达到最大并发会话数 {self._max_concurrent}（猫娘最多同时管理两个会话），"
                    "请等待现有任务完成或用 claude_code_clear_session 清理后再提交"
                )

            pi = (
                poll_interval_sec
                if poll_interval_sec and poll_interval_sec > 0
                else self._default_poll_interval
            )
            pi = max(MIN_POLL_INTERVAL, float(pi))

            task_id = str(uuid.uuid4())[:8]
            record = TaskRecord(
                task_id=task_id,
                prompt=prompt,
                cwd=cwd,
                model=model,
                effort=effort,
                max_turns=max_turns,
                resume_session_id=resume_session_id,
                status=TaskStatus.PENDING,
                poll_interval_sec=pi,
            )
            self._tasks[task_id] = record

        # 在锁外启动后台任务，避免死锁
        record._task = asyncio.create_task(self._execute_task(record))
        self.logger.info(f"Task submitted: {task_id} (poll_interval={pi}s)")

        return record

    async def wait_session_id(
        self, task_id: str, timeout: float = 8.0
    ) -> str:
        """等待新会话的 UUID 被 CLI init 事件捕获。

        新会话的 UUID 由 Claude CLI 在启动后通过 system/init 事件返回，
        无法在提交前预知。本方法在 ``timeout`` 内轮询 record.session_id，
        捕获到立即返回（通常 1-3s）；超时返回空串（调用方应提示猫娘用
        claude_code_poll(task_id) 稍后取）。

        resume 会话的 UUID 提交时已知（=resume_session_id），调用方无需等待。
        """
        deadline = time.time() + max(0.5, timeout)
        while time.time() < deadline:
            async with self._lock:
                rec = self._tasks.get(task_id)
                if rec is None:
                    return ""
                if rec.session_id:
                    return rec.session_id
                # 任务在 init 到达前就失败/取消 → 不再等
                if rec.status in (TaskStatus.ERROR, TaskStatus.CANCELLED):
                    return rec.session_id or ""
            await asyncio.sleep(0.15)
        return ""

    def list_active(self) -> List[Dict[str, Any]]:
        """返回活跃任务（PENDING/RUNNING）的状态列表。

        活跃任务与正在执行的 Claude Code 子进程一一对应，即猫娘可见的
        「会话列表」。已完成/失败的任务不在其中（完成后即从列表释放，
        猫娘仍可用同一 UUID 继续维持会话并再次进入列表）。
        """
        # 快照后处理，避免持锁；_reconcile_stuck_record 只读改 record 字段
        records = [
            r
            for r in list(self._tasks.values())
            if r.status in (TaskStatus.PENDING, TaskStatus.RUNNING)
        ]
        for r in records:
            self._reconcile_stuck_record(r)
        return [r.to_dict() for r in records]

    async def clear_all(self) -> Dict[str, Any]:
        """清理整个会话列表：取消所有活跃任务（杀掉对应 CC 进程树）并清空列表。

        对应「猫娘清理会话列表 → Claude Code 同一会话进程关闭」。
        已完成的结果一并清除（猫娘通常已收到完成推送）。
        """
        cancelled: List[str] = []
        async with self._lock:
            records = list(self._tasks.values())
            self._tasks.clear()

        for record in records:
            if record.status in (TaskStatus.PENDING, TaskStatus.RUNNING) and record._task:
                record._task.cancel()
                cancelled.append(record.task_id)
                try:
                    await record._task
                except (asyncio.CancelledError, Exception):
                    pass
        self.logger.info("Cleared all sessions: {} active cancelled", len(cancelled))
        return {"cleared_count": len(records), "cancelled_active": len(cancelled)}

    async def clear_one(self, task_id: str) -> Dict[str, Any]:
        """清理指定任务：取消并从列表释放（杀掉对应 CC 进程）。"""
        async with self._lock:
            record = self._tasks.pop(task_id, None)
        if record is None:
            return {"error": f"Task not found: {task_id}"}
        cancelled = False
        if record.status in (TaskStatus.PENDING, TaskStatus.RUNNING) and record._task:
            record._task.cancel()
            cancelled = True
            try:
                await record._task
            except (asyncio.CancelledError, Exception):
                pass
        return {"task_id": task_id, "cancelled": cancelled, "status": record.status.value}

    def set_poll_interval(
        self,
        *,
        task_id: str = "",
        interval_sec: float = 0.0,
        default: float = 0.0,
    ) -> Dict[str, Any]:
        """调整轮询间隔：可改默认值、单个任务、或两者。

        - default > 0：更新插件默认轮询间隔（影响后续新任务）
        - task_id 非空 + interval_sec > 0：仅改该任务（运行中也即时生效）
        """
        result: Dict[str, Any] = {}
        if default and default > 0:
            self._default_poll_interval = max(MIN_POLL_INTERVAL, float(default))
            result["default_poll_interval_sec"] = self._default_poll_interval
        if task_id:
            record = self._tasks.get(task_id)
            if record is None:
                return {"error": f"Task not found: {task_id}"}
            if interval_sec and interval_sec > 0:
                record.poll_interval_sec = max(MIN_POLL_INTERVAL, float(interval_sec))
                result["task_id"] = task_id
                result["poll_interval_sec"] = record.poll_interval_sec
            else:
                return {"error": "interval_sec 必须 > 0"}
        return result or {"default_poll_interval_sec": self._default_poll_interval}

    async def poll(self, task_id: str) -> Dict[str, Any]:
        """
        查询任务状态

        Returns:
            任务状态字典
        """
        async with self._lock:
            record = self._tasks.get(task_id)
            if not record:
                return {"error": f"Task not found: {task_id}"}
            self._reconcile_stuck_record(record)
            return record.to_dict()

    @staticmethod
    def _reconcile_stuck_record(record: TaskRecord) -> None:
        """防御性核对：任务协程已结束但状态仍是 PENDING/RUNNING 时强制纠正。

        正常路径下 _execute_task 总会把状态推进到终态；但若协程被
        意外吞掉（如事件循环关闭、未预期的异常逃逸），状态会永远停在
        running，猫娘轮询永远拿不到结果。查询时发现协程已死则直接
        标记 ERROR，让调用方拿到确定性结果（可用 session_id 续跑）。
        """
        if record.status not in (TaskStatus.PENDING, TaskStatus.RUNNING):
            return
        task = record._task
        if task is not None and task.done():
            record.status = TaskStatus.ERROR
            record.error_message = (
                "任务执行协程已意外结束但未写入结果，已强制标记为失败；"
                "若会话已有进展，可用 session_id 续跑（--resume）。"
            )
            record.finished_at = record.finished_at or time.time()

    def get_record(self, task_id: str) -> Optional[TaskRecord]:
        """返回任务记录对象（不存在时返回 None）。供插件主类编排用。"""
        return self._tasks.get(task_id)

    async def wait_for(
        self,
        task_id: str,
        max_wait_sec: float = 60.0,
        interval_sec: float = 0.0,
    ) -> Dict[str, Any]:
        """提前盯进度：在插件内部轮询任务直到完成或本次等待超时。

        定位：任务完成后插件会经 on_complete 主动推送结果，本方法
        只供猫娘想提前盯进度时使用：
        - 任务一旦完成立即返回，不白等
        - 自适应退避间隔（3s → 5s → 8s → 封顶 15s），避免空转
        - 超时后返回实时进度和建议的下次等待时长，供猫娘向用户汇报

        Returns:
            完成/失败时返回完整结果；仍在运行时返回进度 +
            ``still_running=True`` 和 ``suggested_next_wait_sec``。
        """
        deadline = time.time() + max(1.0, max_wait_sec)
        # 自适应间隔：斐波那契式退避，封顶 15s
        interval = interval_sec if interval_sec > 0 else 3.0
        interval = max(1.0, min(interval, 15.0))

        while True:
            async with self._lock:
                record = self._tasks.get(task_id)
                if not record:
                    return {"error": f"Task not found: {task_id}"}
                self._reconcile_stuck_record(record)
                if record.status in (
                    TaskStatus.DONE,
                    TaskStatus.ERROR,
                    TaskStatus.CANCELLED,
                ):
                    return record.to_dict()
                elapsed = time.time() - (record.started_at or record.created_at)

            if time.time() >= deadline:
                data = record.to_dict()
                data["still_running"] = True
                # 建议下次等待时长：夹在 [5, 55]（单次工具调用受网关 ~60s 超时限制）
                data["suggested_next_wait_sec"] = min(
                    55.0, max(5.0, round(max_wait_sec * 1.0))
                )
                data["message"] = (
                    f"任务仍在运行（已耗时 {elapsed:.0f}s，任务本身无超时限制）。"
                    f"任务完成后插件会自动推送结果；若想继续盯，"
                    f"建议 {data['suggested_next_wait_sec']:.0f}s 后再次调用 claude_code_wait。"
                )
                return data

            await asyncio.sleep(min(interval, max(0.1, deadline - time.time())))
            interval = min(15.0, interval * 1.6)

    async def cancel(self, task_id: str) -> Dict[str, Any]:
        """
        取消任务

        Returns:
            取消结果
        """
        async with self._lock:
            record = self._tasks.get(task_id)
            if not record:
                return {"error": f"Task not found: {task_id}"}

            if record.status not in (TaskStatus.PENDING, TaskStatus.RUNNING):
                return {"error": f"Task already finished: {record.status.value}"}

            # 取消任务
            task = record._task
            record.status = TaskStatus.CANCELLED
            record.finished_at = time.time()

        # 在锁外取消任务
        if task:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

        self.logger.info(f"Task cancelled: {task_id}")
        return {"status": "cancelled", "task_id": task_id}

    async def _execute_task(self, record: TaskRecord):
        """后台执行任务。

        失败处理策略（v0.3.0）：
        - 若错误可重试且已从流中捕获到 session_id，自动用
          ``--resume <session_id>`` 续跑一次，而非重开新会话（保留上下文）。
        - 续跑仍失败才标记为 ERROR。
        """
        try:
            record.status = TaskStatus.RUNNING
            record.started_at = time.time()

            # 提交时确定的续跑会话
            resume_id = record.resume_session_id

            result, exec_err = await self._run_once(record, resume_id)

            # 可重试失败 + 已有会话 → 用 session_id 续跑一次
            if (
                exec_err is not None
                and is_retryable(getattr(exec_err, "kind", ""))
                and record.session_id
            ):
                self.logger.info(
                    f"Task {record.task_id}: retryable failure "
                    f"({getattr(exec_err, 'kind', 'unknown')}), "
                    f"resuming session {record.session_id}"
                )
                result, exec_err = await self._run_once(record, record.session_id)

            if exec_err:
                raise RuntimeError(f"Execution failed: {exec_err}")

            record.result = result
            record.status = TaskStatus.DONE
            record.finished_at = time.time()
            self.logger.info(f"Task completed: {record.task_id}")
            self._fire_complete(record)

        except asyncio.CancelledError:
            record.status = TaskStatus.CANCELLED
            record.finished_at = time.time()
            self.logger.info(f"Task cancelled: {record.task_id}")
            raise

        except Exception as e:
            record.status = TaskStatus.ERROR
            record.error_message = str(e)
            record.finished_at = time.time()
            self.logger.error(f"Task failed: {record.task_id}, error: {e}")
            self._fire_complete(record)

    def _fire_complete(self, record: TaskRecord) -> None:
        """触发完成回调（推送结果给猫娘）。

        幂等：用 _pushed 去重，确保 on_complete 立即推送与 _poll_loop
        安全网不会对同一任务重复推送。回调异常时回退 _pushed，让安全网
        下个周期重试。
        """
        if record._pushed:
            return
        if self._on_complete is None:
            record._pushed = True
            return
        record._pushed = True
        try:
            self._on_complete(record)
        except Exception as e:
            record._pushed = False  # 推送失败，回退让安全网重试
            self.logger.warning(f"on_complete callback failed: {record.task_id}: {e}")

    async def _run_once(self, record: TaskRecord, resume_id: str):
        """执行一次 CLI 调用，返回 (ExecuteResult | None, error | None)。"""
        # 构建 CLI 调用（注入激活 provider 的环境变量，提交时取值）
        extra_env = self._env_provider() if callable(self._env_provider) else {}
        invocation, build_err = build_cli_invocation(
            self._config,
            prompt=record.prompt,
            resume_session_id=resume_id,
            cwd=record.cwd,
            model=record.model,
            effort=record.effort,
            max_turns=record.max_turns,
            extra_env=extra_env,
        )

        if build_err:
            raise RuntimeError(f"Failed to build CLI invocation: {build_err}")

        # session_id 早期捕获：init 事件到达时立即写入 record，
        # 使任务执行中途也能感知会话 UUID（供中断后同会话续发）
        def _capture_session_id(sid: str) -> None:
            if sid:
                record.session_id = sid

        parser = ClaudeOutputParser(on_session_id=_capture_session_id)
        # 挂载到 record，供 poll/wait 在运行中读取实时进度
        record._parser = parser
        stream, exec_err = await self._executor.execute(invocation, parser)

        # 捕获会话 ID（无论成败，供续跑和 UI 展示）
        if stream.session_id:
            record.session_id = stream.session_id

        if exec_err:
            return None, exec_err

        result = ExecuteResult(
            session_id=stream.session_id,
            is_new_session=not bool(resume_id),
            messages=stream.messages,
            final_text=stream.final_text,
            total_cost_usd=stream.result.total_cost_usd if stream.result else 0.0,
            duration_ms=stream.result.duration_ms if stream.result else 0,
            num_turns=stream.result.num_turns if stream.result else 0,
            raw_result=stream.result.raw if stream.result else {},
        )
        return result, None

    async def _cleanup_loop(self):
        """定期清理过期任务"""
        try:
            while True:
                await asyncio.sleep(60)  # 每分钟检查一次
                await self._cleanup_expired()
        except asyncio.CancelledError:
            pass

    async def _poll_loop(self):
        """轮询安全网：按各任务自身的 poll_interval_sec 周期检测会话是否仍在进行。

        - 任务完成时 on_complete 会立即推送结果给猫娘（主路径，"及时"）。
        - 本循环作为安全网：到达某任务轮询周期时，若该任务已结束但尚未推送
          （on_complete 因故未送达），则补推一次，确保结果必定稳定送达猫娘。
        - 推送通过 _fire_complete 的 _pushed 去重，绝不重复推送。
        - 轮询间隔默认 5 分钟，可用 claude_code_set_poll_interval 或提交时的
          poll_interval_sec 调整（短任务设短、长任务设长）。
        """
        try:
            while True:
                await asyncio.sleep(POLL_TICK)
                now = time.time()
                due_records: list[TaskRecord] = []
                async with self._lock:
                    for record in self._tasks.values():
                        if (now - record._last_poll_at) < record.poll_interval_sec:
                            continue
                        record._last_poll_at = now
                        if record.status in (
                            TaskStatus.DONE,
                            TaskStatus.ERROR,
                        ) and not record._pushed:
                            due_records.append(record)
                # 在锁外触发推送（_fire_complete 内部用 _pushed 去重）
                for record in due_records:
                    self._fire_complete(record)
        except asyncio.CancelledError:
            pass

    async def _cleanup_expired(self):
        """清理过期的已完成任务"""
        async with self._lock:
            now = time.time()
            expired_ids = []

            for task_id, record in self._tasks.items():
                if record.status in (
                    TaskStatus.DONE,
                    TaskStatus.ERROR,
                    TaskStatus.CANCELLED,
                ):
                    if (
                        record.finished_at
                        and (now - record.finished_at) > self._result_ttl
                    ):
                        expired_ids.append(task_id)

            for task_id in expired_ids:
                del self._tasks[task_id]
                self.logger.debug(f"Cleaned up expired task: {task_id}")

    def get_running_count(self) -> int:
        """获取当前运行中的任务数"""
        return sum(1 for t in self._tasks.values() if t.status == TaskStatus.RUNNING)

    def get_all_tasks(self) -> List[Dict[str, Any]]:
        """获取所有任务的状态（用于调试/监控）"""
        for record in self._tasks.values():
            self._reconcile_stuck_record(record)
        return [record.to_dict() for record in self._tasks.values()]
