"""Claude Code Adapter — Claude CLI 子进程执行器。

负责：
1. 检测 Claude CLI 可执行文件（跨平台，处理 Windows .cmd shim）
2. 构建 CLI 参数列表
3. 启动子进程并通过 stdin 传入 prompt
4. 逐行读取 stdout，交给解析器处理
5. 处理超时和进程终止

参考：
- `neko_claudian/core/providers/claude/runtime/spawn.py` 的跨平台 spawn 工具
- Paperclip `claude-local` 适配器的 CLI 参数构建逻辑
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
from typing import Any, Optional

from .errors import (
    CLI_NOT_FOUND,
    TIMEOUT,
    ClassifiedError,
    classify_error,
)
from .models import AdapterConfig, CLIInvocation
from .parser import ClaudeOutputParser, ParsedStream

# ---------------------------------------------------------------------------
# 跨平台 CLI 检测
# ---------------------------------------------------------------------------


def is_windows() -> bool:
    return sys.platform == "win32"


def which_cmd(name: str) -> Optional[str]:
    """跨平台 which。"""
    return shutil.which(name)


def find_windows_shim(name: str) -> Optional[str]:
    """Windows 上 `claude` 命令经常是 .cmd shim。

    `shutil.which('claude')` 在 Windows 上默认返回 .exe 而非 .cmd，
    但用户直接调用 `claude` 时会优先走 .cmd。

    解决方法：显式找 .cmd / .bat 版本。
    """
    if not is_windows():
        return which_cmd(name)
    # 优先 .cmd
    for ext in (".cmd", ".bat", ".exe", ""):
        candidate = which_cmd(name + ext) if ext else which_cmd(name)
        if candidate:
            return candidate
    return None


def detect_claude_cli() -> Optional[str]:
    """寻找 `claude` 可执行文件。

    - POSIX: 直接 which claude
    - Windows: 找 .cmd / .bat / .exe
    """
    if is_windows():
        return find_windows_shim("claude")
    return which_cmd("claude")


# ---------------------------------------------------------------------------
# CLI 参数构建
# ---------------------------------------------------------------------------


def build_cli_invocation(
    config: AdapterConfig,
    *,
    prompt: str,
    resume_session_id: str = "",
    cwd: Optional[str] = None,
    model: str = "",
    effort: str = "",
    max_turns: int = 0,
    extra_env: Optional[dict[str, str]] = None,
) -> tuple[CLIInvocation, Optional[ClassifiedError]]:
    """构建一次 Claude CLI 调用。

    Parameters
    ----------
    extra_env:
        额外注入的环境变量（如激活 provider 的 ANTHROPIC_BASE_URL /
        ANTHROPIC_AUTH_TOKEN 等，参考 cc-switch 的供应商切换思路）。

    Returns
    -------
    invocation:
        CLI 调用参数。如果出错，仍返回一个占位 invocation。
    error:
        如果 CLI 未找到或参数非法，返回错误；否则 None。
    """
    # 1. 解析可执行文件路径
    exe_path = config.command or detect_claude_cli() or ""
    if not exe_path:
        placeholder = CLIInvocation(
            cmd=[],
            cwd=cwd or config.cwd or os.getcwd(),
            stdin_data=prompt.encode("utf-8"),
            timeout=float(config.timeout_sec),
        )
        return placeholder, ClassifiedError(
            kind=CLI_NOT_FOUND,
            message="claude CLI not found in PATH. Install Claude Code or set [claude_code].command.",
            retryable=False,
        )

    # 2. 构建参数列表
    # 参考 Paperclip 的参数顺序：
    #   claude --print - --output-format stream-json --verbose
    #           [--resume <uuid>] [--model <m>] [--effort <e>] [--max-turns <n>]
    #           [--dangerously-skip-permissions]
    #           [--append-system-prompt-file <path>] [--add-dir <dir>]
    cmd: list[str] = [exe_path]
    cmd.extend(["--print", "-"])
    cmd.extend(["--output-format", "stream-json"])
    cmd.extend(["--verbose"])

    if resume_session_id:
        cmd.extend(["--resume", resume_session_id])

    # 模型：调用参数 > 配置默认值
    effective_model = model or config.model
    if effective_model:
        cmd.extend(["--model", effective_model])

    # 推理努力级别
    effective_effort = effort or config.effort
    if effective_effort:
        cmd.extend(["--effort", effective_effort])

    # 最大轮次
    effective_max_turns = max_turns if max_turns > 0 else config.max_turns_per_run
    if effective_max_turns > 0:
        cmd.extend(["--max-turns", str(effective_max_turns)])

    # 跳过权限提示
    if config.dangerously_skip_permissions:
        cmd.append("--dangerously-skip-permissions")

    # 附加系统提示文件
    if config.append_system_prompt_file:
        cmd.extend(["--append-system-prompt-file", config.append_system_prompt_file])

    # 技能目录
    if config.skills_dir:
        cmd.extend(["--add-dir", config.skills_dir])

    # 3. 确定工作目录
    effective_cwd = cwd or config.cwd or os.getcwd()

    # 4. 构建 invocation
    invocation = CLIInvocation(
        cmd=cmd,
        cwd=effective_cwd,
        stdin_data=prompt.encode("utf-8"),
        timeout=float(config.timeout_sec),
        env_overrides=dict(extra_env or {}),
    )
    return invocation, None


# ---------------------------------------------------------------------------
# 进程树终止
# ---------------------------------------------------------------------------


def kill_process_tree(proc: asyncio.subprocess.Process, logger: Any = None) -> None:
    """同步强杀子进程及其整棵进程树。

    Windows 上 CLI 经 .cmd shim 启动（cmd → node → claude.exe），
    ``proc.kill()`` 只会杀掉 shim 本身，真正的 claude 进程会成为
    孤儿继续自主执行旧指令（曾导致插件重载/任务取消后 Claude Code
    在用户不知情下继续跑）。因此必须按进程树终止：
    - Windows: taskkill /F /T /PID
    - POSIX: 优先杀进程组，失败再单杀
    """
    pid = getattr(proc, "pid", None)
    if not pid:
        return
    try:
        if proc.returncode is None:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(pid)],
                    capture_output=True,
                    timeout=10,
                )
            else:
                import signal

                try:
                    os.killpg(os.getpgid(pid), signal.SIGKILL)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass
    except Exception as e:
        if logger is not None:
            try:
                logger.warning("kill process tree failed: pid={}: {}", pid, e)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# 子进程执行器
# ---------------------------------------------------------------------------

# StreamReader 单行上限。stream-json 的单行可以非常大（长 assistant 文本、
# 工具输出、base64 图片），asyncio 默认 limit 仅 64KB：一旦超限 readline
# 抛 ValueError，读取协程直接死亡，管道缓冲写满后 CLI 会永久卡死在输出上
# ——表现为任务偶发“中断”但状态永远停在 running。长任务必须调大。
_PIPE_LINE_LIMIT = 32 * 1024 * 1024

# 进程退出后等待 stdout/stderr 读取协程收尾的时限。若 Claude 派生的子孙
# 进程继承了管道写端句柄（如后台 dev server），readline 会永远阻塞，
# 导致任务状态永远停在 running；超时后强制取消读取协程继续收尾。
_DRAIN_TIMEOUT = 15.0


class ClaudeCLIExecutor:
    """Claude CLI 子进程执行器。

    封装 asyncio.create_subprocess_exec，提供：
    - 跨平台 spawn（Windows .cmd shim 由 detect_claude_cli 处理）
    - stdin 写入 prompt
    - stdout 逐行读取并交给解析器
    - stderr 收集（用于错误诊断）
    - 超时处理和进程终止
    """

    def __init__(self, config: AdapterConfig, logger: Any = None) -> None:
        self.config = config
        self.logger = logger

    async def execute(
        self,
        invocation: CLIInvocation,
        parser: ClaudeOutputParser,
    ) -> tuple[ParsedStream, Optional[ClassifiedError]]:
        """执行一次 CLI 调用。

        Parameters
        ----------
        invocation:
            CLI 调用参数（由 build_cli_invocation 构建）。
        parser:
            流式输出解析器。每行 stdout 会被喂给 parser.parse_line。

        Returns
        -------
        stream:
            解析后的完整流。
        error:
            如果执行失败（CLI 未找到、超时、子进程异常退出），
            返回分类后的错误；否则 None。
        """
        if not invocation.cmd:
            return parser.finalize(), ClassifiedError(
                kind=CLI_NOT_FOUND,
                message="claude CLI not found",
                retryable=False,
            )

        if self.logger is not None:
            try:
                self.logger.info(
                    "Claude CLI invoke: cmd={} cwd={} stdin_len={} timeout={}",
                    invocation.cmd,
                    invocation.cwd,
                    len(invocation.stdin_data),
                    invocation.timeout,
                )
            except Exception:
                pass

        # 合并环境变量
        env = os.environ.copy()
        env.update(invocation.env_overrides)

        try:
            proc = await asyncio.create_subprocess_exec(
                *invocation.cmd,
                cwd=invocation.cwd,
                env=env,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                limit=_PIPE_LINE_LIMIT,
            )
        except FileNotFoundError as e:
            return parser.finalize(), ClassifiedError(
                kind=CLI_NOT_FOUND,
                message=f"claude CLI not found: {e}",
                retryable=False,
            )
        except Exception as e:
            return parser.finalize(), classify_error(str(e))

        # 收集 stderr
        stderr_lines: list[str] = []

        async def _read_stderr() -> None:
            if proc.stderr is None:
                return
            while True:
                try:
                    line = await proc.stderr.readline()
                except ValueError:
                    # 单行超过 limit：丢弃超限部分，继续读后续数据
                    # （流位置已消费到 limit 处，后续从下一个换行处恢复）
                    if self.logger is not None:
                        try:
                            self.logger.warning(
                                "stderr line exceeded {}B limit, truncated",
                                _PIPE_LINE_LIMIT,
                            )
                        except Exception:
                            pass
                    continue
                except Exception:
                    break
                if not line:
                    break
                stderr_lines.append(
                    line.decode("utf-8", errors="replace").rstrip("\r\n")
                )

        # 读取 stdout 并喂给解析器
        async def _read_stdout() -> None:
            if proc.stdout is None:
                return
            while True:
                try:
                    line = await proc.stdout.readline()
                except ValueError:
                    # 单行超过 limit：丢弃超限部分继续读，避免读取协程死亡
                    # 导致管道写满、CLI 永久卡死（长任务偶发中断的根因之一）
                    if self.logger is not None:
                        try:
                            self.logger.warning(
                                "stdout line exceeded {}B limit, truncated",
                                _PIPE_LINE_LIMIT,
                            )
                        except Exception:
                            pass
                    continue
                except Exception:
                    break
                if not line:
                    break
                try:
                    parser.parse_line(line)
                except Exception as e:
                    if self.logger is not None:
                        try:
                            self.logger.warning("Failed to parse stdout line: {}", e)
                        except Exception:
                            pass

        stderr_task = asyncio.create_task(_read_stderr())
        stdout_task = asyncio.create_task(_read_stdout())

        try:
            return await self._run_process(
                proc, parser, invocation, stderr_lines, stderr_task, stdout_task
            )
        except asyncio.CancelledError:
            # 任务被取消（followup 中断 / 插件重载 shutdown）：
            # 必须杀掉整棵 CLI 进程树，否则 claude 会成为孤儿进程
            # 在用户不知情下继续自主执行旧指令。
            kill_process_tree(proc, self.logger)
            stdout_task.cancel()
            stderr_task.cancel()
            if self.logger is not None:
                try:
                    self.logger.info(
                        "CLI process tree killed on cancel: pid={}", proc.pid
                    )
                except Exception:
                    pass
            raise

    async def _run_process(
        self,
        proc: asyncio.subprocess.Process,
        parser: ClaudeOutputParser,
        invocation: CLIInvocation,
        stderr_lines: list[str],
        stderr_task: asyncio.Task,
        stdout_task: asyncio.Task,
    ) -> tuple[ParsedStream, Optional[ClassifiedError]]:
        """写入 stdin 并等待进程结束，返回解析结果。"""
        # 写入 stdin 并关闭
        try:
            if proc.stdin is not None:
                proc.stdin.write(invocation.stdin_data)
                await proc.stdin.drain()
                proc.stdin.close()
        except Exception as e:
            if self.logger is not None:
                try:
                    self.logger.warning("Failed to write stdin: {}", e)
                except Exception:
                    pass

        # 等待进程结束（timeout <= 0 表示不限制，长任务常见半小时以上）
        try:
            if invocation.timeout and invocation.timeout > 0:
                return_code = await asyncio.wait_for(
                    proc.wait(), timeout=invocation.timeout
                )
            else:
                return_code = await proc.wait()
        except asyncio.TimeoutError:
            # 超时 — 杀整棵进程树（只杀 shim 会留下孤儿 claude）
            kill_process_tree(proc, self.logger)
            try:
                await proc.wait()
            except Exception:
                pass
            # 等待读取任务结束（限时，防止孤儿子进程持有管道句柄导致永久阻塞）
            await _drain_tasks(stdout_task, stderr_task, logger=self.logger)
            stderr_text = "\n".join(stderr_lines)
            return parser.finalize(), ClassifiedError(
                kind=TIMEOUT,
                message=f"execution timed out after {invocation.timeout}s. stderr: {stderr_text[:500]}",
                retryable=False,
            )

        # 等待读取任务完成（限时：进程已退出，但若子孙进程继承了管道
        # 写端句柄，readline 会永远阻塞，任务将永远停在 running）
        await _drain_tasks(stdout_task, stderr_task, logger=self.logger)

        stream = parser.finalize()

        # 检查返回码和 result 事件
        if return_code != 0:
            stderr_text = "\n".join(stderr_lines)
            # 如果有 result 事件的 is_error，优先用 result 的信息
            if stream.result and stream.result.is_error:
                err = classify_error(
                    stream.result.result_text or stderr_text,
                    return_code=return_code,
                    raw=stream.result.raw,
                )
                return stream, err
            # 否则用 stderr 分类
            err = classify_error(
                stderr_text or f"process exited with code {return_code}",
                return_code=return_code,
            )
            return stream, err

        # 返回码 0 但 result 事件标记为错误
        if stream.result and stream.result.is_error:
            err = classify_error(
                stream.result.result_text,
                return_code=return_code,
                raw=stream.result.raw,
            )
            return stream, err

        # 成功
        return stream, None


async def _drain_tasks(
    *tasks: asyncio.Task,
    timeout: float = _DRAIN_TIMEOUT,
    logger: Any = None,
) -> None:
    """限时等待所有读取任务结束；超时则强制取消并继续。

    进程退出后，若 Claude 派生的子孙进程（如后台服务）继承了
    stdout/stderr 管道写端句柄，管道不会 EOF，readline 会永远阻塞，
    任务状态将永远停在 running。因此必须限时等待：超时后取消读取
    任务并继续收尾（此时 CLI 已退出，未读尽的只是尾部残余输出）。
    """
    pending = [t for t in tasks if not t.done()]
    if pending:
        _, not_done = await asyncio.wait(set(pending), timeout=timeout)
        for task in not_done:
            task.cancel()
        for task in not_done:
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        if not_done and logger is not None:
            try:
                logger.warning(
                    "stdout/stderr drain timed out after {}s; {} reader task(s) cancelled "
                    "(likely orphaned child processes still holding pipe handles)",
                    timeout,
                    len(not_done),
                )
            except Exception:
                pass
    # 已完成的任务也要 await 一次，消费其异常避免“exception never retrieved”
    for task in tasks:
        if task.done():
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass


__all__ = [
    "is_windows",
    "which_cmd",
    "find_windows_shim",
    "detect_claude_cli",
    "build_cli_invocation",
    "kill_process_tree",
    "ClaudeCLIExecutor",
]
