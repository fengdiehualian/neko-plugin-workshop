"""子进程派发层。

必须先弄清楚的四个坑（每一个都是「不报错、只在生产里挂死」的类型）：

1. **stdin 必须隔离。** 桥接服务自己的 stdin 承载 MCP 的 JSON-RPC 流；如果被派发
   出去的 Agent CLI 继承它，两边会互相抢读，协议立刻崩掉。所以一律用 DEVNULL。
2. **不能用 asyncio 的子进程等待。** asyncio 的 `Process.wait()` 要等所有管道 EOF
   才返回，而 Agent CLI 派生的孙进程会一直握着管道写端，导致超时机制失效。
   详见 `_execute_blocking` 的说明。
3. **超时要连进程树一起清。** 这些 Agent CLI 自身会再派生 node/bun/python 子进程，
   只杀父进程会留下孤儿进程继续占用文件与端口。Windows 上用 taskkill /T /F。
4. **输出要限幅但不能停止读取。** 读满上限后若不再读，管道写满会让子进程永久阻塞；
   所以上限只约束内存，不约束读取动作。
"""

from __future__ import annotations

import asyncio
import os
import re
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from .config import BridgeConfig, normalize_path
from .registry import (
    CMD_FALLBACK,
    AgentSpec,
    ResolvedCommand,
    build_argv,
    build_version_argv,
    redact_argv,
)

_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_VERSION_RE = re.compile(r"\d+\.\d+")
_NOISE_PREFIXES = ("(node:", "node:", "npm ", "warning:", "experimentalwarning")

_IS_WINDOWS = sys.platform == "win32"
_READ_CHUNK = 65536

_ENV_STRIP = (
    "PYTHONHOME",
    "PYTHONPATH",
    "PYTHONSTARTUP",
    "PYTHONEXECUTABLE",
    "PYTHONUSERBASE",
    "PYTHONLEGACYWINDOWSSTDIO",
)
"""派发给子进程前要剔除的 Python 环境变量。

宿主场景：N.E.K.O 的插件进程跑在自带的嵌入式 Python 3.11 上，其 ``resources/bin/``
被整体加进了 sys.path，且存在 ``python311.dll``。这些变量一旦传给别的 Python 解释器
（qwenpaw 的 venv、或用户自己在 Agent 里起的脚本），对方会以
``Failed to import encodings module`` / ``python311.dll conflicts`` 直接失败。
"""

_STREAM_GRACE = 5.0
"""读完最后一段输出的宽限期（秒）。

子进程退出后，如果它派生的 node/bun 孙进程还活着，管道的写端就没有关闭，
读取线程永远等不到 EOF。给 5 秒让正常输出落完，到点就放弃等待并保留已有数据。"""

_AUTH_HEAD_CHARS = 400
"""鉴权提示的搜索范围：只看输出开头这么多字符。"""

_AUTH_MAX_CHARS = 800
"""只有「短输出」才可能是登录提示。

真跑完的 Agent 一定会把结果写进 stdout，见 ``_detect_auth_failure``。"""

_AUTH_PATTERNS = (
    re.compile(r"authentication\s+required", re.IGNORECASE),
    re.compile(r"unauthenticated", re.IGNORECASE),
    re.compile(r"not\s+(?:logged\s?in|authenticated|signed\s?in)", re.IGNORECASE),
    re.compile(r"(?:please\s+)?use\s+`?/login`?", re.IGNORECASE),
    re.compile(r"(?:please\s+)?run\s+`?/login`?", re.IGNORECASE),
    re.compile(r"invalid\s+api\s*key", re.IGNORECASE),
    re.compile(r"credentials?\s+(?:expired|invalid|not\s+found|missing)", re.IGNORECASE),
    re.compile(r"请(?:先)?登录|尚未登录|未登录|需要登录|登录后(?:再|才)"),
)
"""「这台 CLI 还没登录」的常见措辞（中英文都收）。"""


def _detect_auth_failure(stdout: str, stderr: str) -> str:
    """识别「CLI 因为没登录而拒绝干活」，返回提示首行；不是这种情况就返回空串。

    **为什么必须单独判**：这些 CLI 未登录时**退出码是 0**，只往 stderr 打一行
    「Authentication required. Please use /login command to sign in」。
    于是 ``AgentResult.ok`` 判定为成功，那行登录提示被当成「任务输出」原样交给
    上层模型 —— 模型只能从一段莫名其妙的输出里猜「哦原来要登录」。

    **不能要求 stdout 为空**：启动期真出问题时，CLI 往往先往 stdout 打几行噪声
    （codebuddy 的 PowerShell 警告就是），登录提示照样在 stderr 里，一旦用
    「stdout 非空就不判」当闸门，这类失败会被报成 ``ok=True``。

    误报防护改成两条更准的：
    1. **只看输出开头**（``_AUTH_HEAD_CHARS``）—— 真跑完任务的 Agent 结果在
       stdout 正文里，不会把登录措辞放在最前面；
    2. **要求 stdout 里没有实质内容** —— stdout 全是噪声行（警告/空行）时仍判，
       一旦出现非噪声的正文就不再判，这样「让人写登录页」也不会被误伤。
    """
    text = (stderr or "").strip()
    if not text or len(text) > _AUTH_MAX_CHARS:
        return ""
    if not any(pattern.search(text[:_AUTH_HEAD_CHARS]) for pattern in _AUTH_PATTERNS):
        return ""
    if _has_substantive_output(stdout):
        return ""
    return next((line.strip() for line in text.splitlines() if line.strip()), text[:120])


def _has_substantive_output(stdout: str) -> bool:
    """stdout 里是否有「像任务结果」的内容（滤掉警告、空行、纯符号行）。"""
    for line in strip_ansi(stdout or "").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        lowered = stripped.lower()
        if any(lowered.startswith(prefix) for prefix in _NOISE_PREFIXES):
            continue
        if "warning" in lowered or lowered.startswith("at "):
            continue
        if len(stripped) >= 8:
            return True
    return False



def strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def _pick_version_line(text: str) -> str:
    """挑出版本那一行。

    有些 CLI 会先往 stdout/stderr 吐 Node 的实验特性警告，直接取第一行会拿到警告
    而不是版本号，所以先滤掉噪声行，再优先取含 `数字.数字` 的行。
    """
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return ""
    # Node 的告警常占好几行，末行形如 "Use `node --trace-warnings ...`"，一并滤掉
    candidates = [
        ln for ln in lines
        if not any(ln.lower().startswith(prefix) for prefix in _NOISE_PREFIXES)
        and "warning" not in ln.lower()
    ]
    if not candidates:
        candidates = lines
    for line in candidates:
        if _VERSION_RE.search(line):
            return line
    return candidates[0]


@dataclass
class AgentResult:
    agent: str
    label: str
    exit_code: int | None
    stdout: str
    stderr: str
    duration_ms: int
    timed_out: bool = False
    output_truncated: bool = False
    command_preview: str = ""
    error: str = ""
    needs_login: bool = False
    """CLI 因为没登录而拒绝执行（``error`` 以 ``AGENT_NEEDS_LOGIN:`` 开头）。"""
    extra: dict[str, object] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out and not self.error

    def output_text(self) -> str:
        """优先返回 stdout；为空时退回 stderr（很多 CLI 把结果写 stderr）。"""
        return self.stdout.strip() or self.stderr.strip()

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "agent": self.agent,
            "label": self.label,
            "exit_code": self.exit_code,
            "ok": self.ok,
            "duration_ms": self.duration_ms,
            "timed_out": self.timed_out,
            "output_truncated": self.output_truncated,
            "command": self.command_preview,
        }
        if self.needs_login:
            payload["needs_login"] = True
        if self.stdout:
            payload["stdout"] = self.stdout
        if self.stderr:
            payload["stderr"] = self.stderr
        if self.error:
            payload["error"] = self.error
        payload.update(self.extra)
        return payload


class _PipeReader(threading.Thread):
    """按上限收集一个管道，且**即使调用方不再等待也保留已读到的部分**。

    两个反直觉但必须处理的点：

    1. 达到上限后不能停止读取。管道缓冲区被写满后子进程会阻塞在 write 上永不退出，
       于是整个调用只能等超时。上限只约束内存，不约束读取动作。
    2. 读循环不能无条件等 EOF。这些 Agent CLI 会派生 node/bun 孙进程，它们同样继承
       着管道的写端；父进程退出后写端仍未关闭，EOF 永远不来（详见下方 _execute 的说明）。
       所以本线程是 daemon，调用方只给固定宽限期，到点就放弃等待。
    """

    def __init__(self, pipe: Any, limit: int) -> None:
        super().__init__(daemon=True)
        self.pipe = pipe
        self.limit = limit
        self.buf = bytearray()
        self.truncated = False

    def run(self) -> None:
        try:
            while True:
                chunk = self.pipe.read(_READ_CHUNK)
                if not chunk:
                    break
                room = self.limit - len(self.buf)
                if room > 0:
                    self.buf.extend(chunk[:room])
                    if room < len(chunk):
                        self.truncated = True
                else:
                    self.truncated = True
        except (OSError, ValueError):
            # 管道被对端关闭，或调用方为了解除阻塞而 close 了读端
            pass

    @property
    def data(self) -> bytes:
        return bytes(self.buf)


def _close_in_background(pipe: Any) -> None:
    """在后台线程里关闭管道。

    不能在当前线程直接 close：FileIO.close() 会等待该 fd 上正在进行的 read 返回，
    而那个 read 正卡在孙进程占着的写端上 —— 直接 close 会把调用方拖到孙进程结束
    （实测多等 10 秒以上）。丢给 daemon 线程去收尾，写端一断开它自然就完成了。
    """

    def _run() -> None:
        try:
            pipe.close()
        except (OSError, ValueError):
            pass

    threading.Thread(target=_run, daemon=True).start()


def _kill_tree_sync(process: subprocess.Popen[bytes]) -> None:
    """同步结束进程及其派生进程。"""
    pid = process.pid
    if pid is None:
        return
    if _IS_WINDOWS:
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=15,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000),
            )
        except (OSError, subprocess.SubprocessError):
            pass
    try:
        process.kill()
    except (ProcessLookupError, OSError):
        pass


def _execute_blocking(
    argv: list[str],
    *,
    cwd: str | None,
    timeout: int,
    output_limit: int,
    env: dict[str, str],
    stream_grace: float,
) -> tuple[int | None, str, str, bool, bool, str]:
    """同步跑一条命令。返回 (exit_code, stdout, stderr, 超时, 输出截断, 错误)。

    **为什么这里刻意用阻塞式 subprocess 而不是 asyncio 的子进程**：

    asyncio 的 `Process.wait()` 并不是「进程退出就返回」。看
    `asyncio/base_subprocess.py::_try_finish`，exit waiter 只在这两种情况被唤醒：
    管道从未连接成功，或者**所有管道都已断开（即读到 EOF）**。

    而 Agent CLI 会派生 node/bun 孙进程，它们继承着管道的写端。父进程退出后写端仍
    被孙进程握着，EOF 永远不会到来，于是 `wait()` 一直挂到孙进程自己结束为止 ——
    实测一个退出 0.5 秒的父进程配一个活 20 秒的孙进程，`wait()` 就是 20 秒才返回，
    用户配置的超时完全失效。

    阻塞式 `Popen.wait(timeout=...)` 走的是操作系统层面的进程句柄等待，不受管道状态
    影响，实测同一场景 0.5 秒返回。读取交给后台 daemon 线程，配合固定宽限期，
    于是「进程退出」与「输出读完」两件事彻底解耦。
    """
    kwargs: dict[str, Any] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        # bufsize=0 很关键。默认会拿到 BufferedReader，而 BufferedReader.read(n)
        # 的语义是「读满 n 字节或遇到 EOF 才返回」—— 在我们这种「父进程退出、孙进程
        # 仍占着写端」的场景里就永远返回不了。而且 BufferedReader.close() 要抢
        # 同一把缓冲区锁，会把想在宽限期后强行收尾的调用方一起卡死。
        # 裸 FileIO 的 read(n) 是单次系统调用，有多少返回多少，关闭也不会阻塞。
        "bufsize": 0,
    }

    # 工作目录与 PWD 必须一起处理。有些 CLI 不看 cwd，而是读 PWD 环境变量来决定
    # 工作目录；如果宿主是从 Git Bash / WSL 启起来的，PWD 会是 `/c/Users/...`
    # 这种 MSYS 形式，CLI 拿去 chdir 会直接失败（实测 opencode 就是这么挂的）。
    workdir = normalize_path(cwd) if cwd else os.getcwd()
    if os.path.isdir(workdir):
        kwargs["cwd"] = workdir
        env = dict(env)
        env["PWD"] = workdir
    kwargs["env"] = env

    if _IS_WINDOWS:
        # 桌面端宿主下不要弹出控制台窗口
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)

    try:
        process = subprocess.Popen(argv, **kwargs)
    except (OSError, ValueError) as exc:
        return None, "", "", False, False, f"无法启动进程：{exc}"

    assert process.stdout is not None and process.stderr is not None
    out_reader = _PipeReader(process.stdout, output_limit)
    err_reader = _PipeReader(process.stderr, output_limit)
    out_reader.start()
    err_reader.start()

    timed_out = False
    error = ""
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        error = f"超过 {timeout}s 未结束"
        _kill_tree_sync(process)
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass

    # 宽限期是所有读取线程共享的总预算，不是每个各给一份 —— 否则两个管道会把
    # 等待时间翻倍。正常情况下父进程退出即 EOF，这一步几乎不花时间；只有
    # 「孙进程握着写端」时才真的等满。
    deadline = time.monotonic() + stream_grace
    for reader in (out_reader, err_reader):
        remaining = deadline - time.monotonic()
        if remaining > 0:
            reader.join(remaining)

    stdout_text = strip_ansi(out_reader.data.decode("utf-8", errors="replace"))
    stderr_text = strip_ansi(err_reader.data.decode("utf-8", errors="replace"))
    truncated = out_reader.truncated or err_reader.truncated

    # 仍被孙进程占着写端的读线程会一直卡在 read()。交给后台线程收尾，
    # 免得每来一个这样的任务就多挂一个占着 fd 的线程。
    for reader, pipe in ((out_reader, process.stdout), (err_reader, process.stderr)):
        if reader.is_alive():
            _close_in_background(pipe)

    return process.returncode, stdout_text, stderr_text, timed_out, truncated, error


async def _execute(
    argv: list[str],
    *,
    cwd: str | None,
    timeout: int,
    output_limit: int,
    env_overrides: dict[str, str] | None = None,
    stream_grace: float = _STREAM_GRACE,
) -> tuple[int | None, str, str, bool, bool, str]:
    """在线程里跑 _execute_blocking，保持调用方是 async 的。

    开销可忽略：真正的工作是子进程在跑，线程只是等它。
    """
    env = os.environ.copy()
    # 派发出去的 Agent CLI 里也有 Python 实现的（例如 qwenpaw 用自己的 venv）。
    # 当本进程跑在 N.E.K.O 的嵌入式 Python 3.11 里时，PYTHONHOME / PYTHONPATH 会指向
    # 宿主自己的 bin 目录（含 python311.dll 与顶层 asyncio/），那些子解释器会因此直接
    # 起不来。在唯一的子进程入口处统一清洗，覆盖 run_agent 与 probe_version 两条路径。
    # 顺序刻意为「先清洗后合并」：如果调用方显式给了这些变量，仍然以调用方为准。
    for _name in _ENV_STRIP:
        env.pop(_name, None)
    if env_overrides:
        env.update(env_overrides)

    return await asyncio.to_thread(
        _execute_blocking,
        argv,
        cwd=cwd,
        timeout=timeout,
        output_limit=output_limit,
        env=env,
        stream_grace=stream_grace,
    )


async def run_agent(
    spec: AgentSpec,
    resolved: ResolvedCommand,
    task: str,
    *,
    config: BridgeConfig,
    cwd: str | None = None,
    model: str | None = None,
    timeout: int | None = None,
    json_output: bool = False,
    extra_args: tuple[str, ...] = (),
) -> AgentResult:
    """把一条非交互式任务派发给指定 Agent。"""
    override = config.override_for(spec.id)
    effective_model = model or override.model
    effective_extra = tuple(extra_args) + tuple(override.extra_args)
    effective_timeout = timeout if timeout is not None else override.timeout_seconds
    seconds = config.clamp_timeout(effective_timeout)

    if resolved.source == CMD_FALLBACK:
        # **绝不能把任务正文交给 cmd.exe。** cmd 的行解析与 list2cmdline 的转义规则
        # 不兼容：正文里的 `"` 会破坏引号配平，紧跟的 `&` 就越界执行；`%VAR%` 在
        # 引号内也照样展开。审计 PoC 的 7 个载荷里有 4 个正是这样逃出去的。
        # 解析不出真身时宁可拒绝，也不把用户文本送进 shell。
        return AgentResult(
            agent=spec.id,
            label=spec.label,
            exit_code=None,
            stdout="",
            stderr="",
            duration_ms=0,
            command_preview=resolved.describe(),
            error=(
                f"AGENT_UNRESOLVED: {spec.binary} 只能通过 cmd.exe 启动，而 cmd.exe 无法安全"
                f"承载任务正文（引号与 % 可越界执行）。请在 plugin.toml 的 "
                f"[bridge.overrides.{spec.id}] 里用 exe_override 指向真实可执行文件。"
                f"（{resolved.detail}）"
            ),
            extra={"unresolved_shim": resolved.exe},
        )

    argv = build_argv(
        spec,
        resolved,
        task,
        model=effective_model,
        json_output=json_output,
        extra_args=effective_extra,
    )
    workdir = config.resolve_cwd(cwd)
    preview = " ".join(redact_argv(argv, task))

    started = time.monotonic()
    exit_code, stdout, stderr, timed_out, truncated, error = await _execute(
        argv,
        cwd=workdir,
        timeout=seconds,
        output_limit=config.max_output_bytes,
        env_overrides=override.env or None,
    )
    duration_ms = int((time.monotonic() - started) * 1000)

    # 未登录的 CLI 会「退出码 0 + stderr 一行登录提示」，不特判就会被当成成功，
    # 上层模型拿到的 output 就是那行提示，只能自己猜为什么没干活。
    needs_login = False
    if not timed_out and not error:
        auth_line = _detect_auth_failure(stdout, stderr)
        if auth_line:
            needs_login = True
            error = f"AGENT_NEEDS_LOGIN: {auth_line}"

    extra: dict[str, object] = {}
    if workdir:
        extra["cwd"] = workdir

    return AgentResult(
        agent=spec.id,
        label=spec.label,
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        duration_ms=duration_ms,
        timed_out=timed_out,
        output_truncated=truncated,
        command_preview=preview,
        error=error,
        needs_login=needs_login,
        extra=extra,
    )


async def probe_version(
    spec: AgentSpec,
    resolved: ResolvedCommand,
    *,
    config: BridgeConfig,
) -> str:
    """取一个 Agent 的版本号，用于 agents_list / agent_doctor。

    带 fixed_args 探测一次；若**看起来不像版本号**就摘掉 fixed_args 再试一次。
    qwenpaw 的 `task` 子命令不接受 `--version`，于是把 usage 文本当成版本号回显；
    去掉子命令后直接问 CLI 才拿得到真版本（sibling skill neko.py 里同样的兜底）。
    """
    override = config.override_for(spec.id)
    exit_code, stdout, stderr, timed_out, _, _ = await _execute(
        build_version_argv(spec, resolved),
        cwd=None,
        timeout=45,
        output_limit=8192,
        env_overrides=override.env or None,
    )
    if timed_out:
        return "<超时>"
    text = (stdout or stderr).strip()
    if text and _looks_like_version(_pick_version_line(text)):
        return _pick_version_line(text)[:120]

    if not spec.fixed_args:
        if not text:
            return f"<无输出 rc={exit_code}>"
        return _pick_version_line(text)[:120]

    _, retry_out, retry_err, retry_timed_out, _, _ = await _execute(
        build_version_argv(spec, resolved, with_fixed_args=False),
        cwd=None,
        timeout=45,
        output_limit=8192,
        env_overrides=override.env or None,
    )
    if retry_timed_out:
        return "<超时>"
    retry_text = (retry_out or retry_err).strip()
    if retry_text and _looks_like_version(_pick_version_line(retry_text)):
        return _pick_version_line(retry_text)[:120]
    # 两次都不像版本号 —— 宁可回显第一次的真实输出，也不要编一个假的
    if not text:
        return f"<无输出 rc={exit_code}>"
    return _pick_version_line(text)[:120]


def _looks_like_version(line: str) -> bool:
    """判断一行文本是否**真的**像版本号，而不是 usage 首行或日志时间戳。

    只看「出现了数字.数字」是不够的：qwenpaw 的 usage 里带路径
    （`...tools\\__init__.py:73 | 2026-10-08`）也能匹配到数字。所以要求这行
    以版本号为主体：要么整行就是 `1.2.3` 这类，要么是 `name 1.2.3` 这种短前缀。
    """
    if not line or len(line) > 120:
        return False
    # 明显的日志/用法行直接否掉
    lowered = line.lower()
    if any(marker in lowered for marker in ("usage:", "usage ", "options:", "traceback", ".py:", "error")):
        return False
    match = _VERSION_RE.search(line)
    if not match:
        return False
    # 版本号必须落在前 40 个字符内，且其后没有大段路径/解释文字
    if match.start() > 40:
        return False
    tail = line[match.end():].strip()
    return len(tail) <= 40

