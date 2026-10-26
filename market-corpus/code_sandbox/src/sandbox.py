"""code_sandbox — 安全 Python 沙箱执行。

安全设计（v0.1.0，Windows 可行方案）：
1. AST 静态扫描：禁止危险模块导入（os/subprocess/sys/socket/网络/ctypes/shutil/pathlib 等）
   与危险内置调用（open/exec/eval/compile/input/breakpoint/getattr 等）。
2. 子进程隔离：以 python -I（隔离模式，忽略环境与 site）执行，cwd 指向沙箱目录。
3. 超时强制终止：超时后先 kill 再 taskkill /T /F 兜底，防止子进程残留。
4. 环境变量净化：执行时剥离 N.E.K.O 相关敏感变量（API Key 等）。
5. 输出截断：stdout/stderr 各自截断到上限，防止刷屏。

允许使用的标准库：math/random/json/statistics/collections/datetime/re/itertools/
functools/typing/decimal/fractions/heapq/bisect/string/base64/hashlib/textwrap/
unicodedata/zoneinfo/struct 等纯计算类模块。
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# ══════════ 常量 ══════════

MAX_OUTPUT_CHARS = 50000  # 每路输出截断上限（约 50K 字符）
DEFAULT_TIMEOUT = 10.0  # 默认超时（秒）
MAX_TIMEOUT = 60.0  # 工具允许的最大超时

# 禁止导入的模块（含子模块前缀）
_BLOCKED_MODULES = {
    "os", "subprocess", "sys", "socket", "http", "urllib", "requests", "httpx",
    "ctypes", "shutil", "pathlib", "io", "glob", "pickle", "marshal", "shelve",
    "dbm", "sqlite3", "multiprocessing", "threading", "asyncio", "importlib",
    "runpy", "pdb", "traceback", "logging", "argparse", "platform", "psutil",
    "tkinter", "webbrowser", "ftplib", "smtplib", "telnetlib", "xmlrpc",
    "zipfile", "tarfile", "gzip", "bz2", "lzma", "zlib", "codecs", "tokenize",
    "dis", "inspect", "signal", "fcntl", "mmap", "resource", "pwd", "grp",
    "curses", "termios", "tty", "pty", "cgi", "wsgiref", "email", "tempfile",
}

# 禁止调用的内置/属性名（AST Name / Attribute 匹配）
_BLOCKED_CALL_NAMES = {
    "open", "exec", "eval", "compile", "input", "breakpoint", "getattr",
    "setattr", "delattr", "globals", "locals", "vars", "__import__", "exit",
    "quit", "help", "memoryview", "buffer", "vars",
}

# 禁止的属性访问（如 os.system、subprocess.run、Path.write_text、__builtins__.open）
_BLOCKED_ATTRS = {
    "system", "popen", "run", "Popen", "call", "check_call", "check_output",
    "spawn", "fork", "execv", "execve", "kill", "connect", "connect_ex",
    "sendto", "sendall", "bind", "listen", "accept", "write_text", "write_bytes",
    "unlink", "rmtree", "remove", "rename", "replace", "mkdir", "makedirs",
    "symlink", "read_text", "read_bytes", "listdir", "walk", "scandir",
    "startfile", "sleep", "mkdtemp", "mkstemp", "NamedTemporaryFile",
    # 危险内置以属性形式访问（__builtins__.open / .eval 等）
    "open", "exec", "eval", "compile", "input", "breakpoint",
}

# Python 沙箱经典逃逸链属性（任何对象访问都拦截，不受危险根限定）
_ESCAPE_ATTRS = {
    "__class__", "__bases__", "__mro__", "__subclasses__", "__globals__",
    "__builtins__", "__code__", "__closure__", "__dict__", "__getattribute__",
    "__self__", "__func__", "__init__", "__new__",
}

# 属性拦截只作用于这些"危险根对象"上，避免误伤纯计算数据结构的同名属性
# （如 list.remove / dict.update 等合法操作）；time 仅针对 sleep 等阻塞属性。
_DANGEROUS_ROOTS = _BLOCKED_MODULES | {
    "Path", "time", "builtins", "__builtins__", "tempfile",
}


def _root_name(node) -> str | None:
    """取属性链的最左根名：os.system→os；Path('x').write_text→Path；a.b.c→a。"""
    while isinstance(node, ast.Attribute):
        node = node.value
    if isinstance(node, ast.Call):
        node = node.func
    if isinstance(node, ast.Name):
        return node.id
    return None

# 允许导入的标准库白名单
_ALLOWED_MODULES = {
    "math", "random", "json", "statistics", "collections", "datetime", "re",
    "itertools", "functools", "typing", "decimal", "fractions", "heapq",
    "bisect", "string", "base64", "hashlib", "textwrap", "unicodedata",
    "zoneinfo", "struct", "numbers", "operator", "copy", "types", "enum",
    "dataclasses", "uuid", "secrets",
}


class SandboxViolation(Exception):
    """沙箱违规：扫描或执行阶段的禁止操作。"""


# ══════════ AST 扫描 ══════════


class _SandboxVisitor(ast.NodeVisitor):
    """遍历 AST，收集违规项。"""

    def __init__(self) -> None:
        self.violations: list[str] = []

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            name = alias.name.split(".")[0]
            if name in _BLOCKED_MODULES:
                self.violations.append(f"禁止导入模块: {alias.name}")
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module:
            root = node.module.split(".")[0]
            if root in _BLOCKED_MODULES:
                self.violations.append(f"禁止导入模块: {node.module}")
            elif root in _ALLOWED_MODULES:
                for alias in node.names:
                    if alias.name == "*" or alias.name in {
                        "system", "popen", "run", "Popen", "call", "open",
                        "exec", "eval", "compile", "input", "getattr",
                        "setattr", "delattr", "sleep",
                    }:
                        self.violations.append(f"禁止从 {node.module} 导入: {alias.name}")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Name) and node.func.id in _BLOCKED_CALL_NAMES:
            self.violations.append(f"禁止调用: {node.func.id}()")
        if isinstance(node.func, ast.Attribute):
            attr = node.func.attr
            if attr in _BLOCKED_ATTRS and _root_name(node.func.value) in _DANGEROUS_ROOTS:
                self.violations.append(f"禁止属性调用: .{attr}()")
            elif attr in _ESCAPE_ATTRS:
                self.violations.append(f"禁止访问逃逸属性: .{attr}")
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr in _ESCAPE_ATTRS:
            self.violations.append(f"禁止访问逃逸属性: .{node.attr}")
        elif node.attr in _BLOCKED_ATTRS and _root_name(node.value) in _DANGEROUS_ROOTS:
            self.violations.append(f"禁止访问属性: .{node.attr}")
        self.generic_visit(node)


def audit_code(code: str) -> None:
    """AST 静态扫描代码，违规抛出 SandboxViolation。"""
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        raise SandboxViolation(f"代码语法错误: {exc}") from exc
    visitor = _SandboxVisitor()
    visitor.visit(tree)
    if visitor.violations:
        raise SandboxViolation("; ".join(visitor.violations[:8]))


# ══════════ 执行 ══════════


def _sanitized_env() -> dict[str, str]:
    """构造净化后的子进程环境：剥离常见敏感变量与 N.E.K.O 配置。"""
    block_prefixes = (
        "DASHSCOPE_", "OPENAI_", "DOUBAO_", "ARK_", "KLING_", "ASSIST_API_",
        "ANTHROPIC_", "GEMINI_", "DEEPSEEK_", "KIMI_", "MINIMAX_", "MIMO_",
        "ELEVENLABS_", "OPENROUTER_", "REQUESTY_", "ORCAROUTER_", "VOLC_",
        "GH_TOKEN", "GITHUB_TOKEN", "NEKO_",
    )
    env = {}
    for key, value in os.environ.items():
        if key.upper().startswith(block_prefixes) and key != "NEKO_REPOSITORY":
            continue
        env[key] = value
    return env


def run_code(code: str, timeout: float = DEFAULT_TIMEOUT, workdir: Path | None = None) -> dict:
    """在子进程隔离中执行 Python 代码，返回结构化结果。

    返回 {"output": str, "is_error": bool, "error": str} 兼容 llm_tool 信封。
    """
    code = (code or "").strip()
    if not code:
        return {"output": "代码为空。请提供要执行的 Python 代码。", "is_error": True, "error": "NO_CODE"}

    try:
        audit_code(code)
    except SandboxViolation as exc:
        return {"output": f"安全拦截：{exc}。\n\n提示：沙箱禁止系统/网络/文件操作，请改用纯计算代码。",
                "is_error": True, "error": "SANDBOX_VIOLATION"}

    timeout = min(max(float(timeout), 1.0), MAX_TIMEOUT)
    workdir = workdir or Path(tempfile.gettempdir()) / "neko_code_sandbox"
    try:
        workdir.mkdir(parents=True, exist_ok=True)
    except OSError:
        workdir = Path(tempfile.gettempdir())

    started = time.monotonic()
    try:
        proc = subprocess.Popen(
            [sys.executable, "-I", "-c", code],
            cwd=str(workdir),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=_sanitized_env(),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except OSError as exc:
        return {"output": f"无法启动 Python 子进程：{exc}", "is_error": True, "error": "SPAWN_FAILED"}

    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _force_kill(proc)
        stdout, stderr = proc.communicate()
        elapsed = time.monotonic() - started
        tail = (stderr or stdout or "").strip()[-300:]
        return {"output": f"执行超时（>{timeout:.0f}s），已强制终止。\n最后输出片段：{tail}",
                "is_error": True, "error": "TIMEOUT"}

    elapsed = time.monotonic() - started
    out = _truncate(stdout)
    err = _truncate(stderr)
    if proc.returncode != 0:
        return {"output": f"执行出错（退出码 {proc.returncode}，{elapsed:.1f}s）：\n{err or out}",
                "is_error": True, "error": "RUNTIME_ERROR", "stdout": out, "stderr": err}
    return {"output": f"执行成功（{elapsed:.1f}s）：\n{out or '(无输出)'}", "is_error": False, "stdout": out, "stderr": err}


def _truncate(text: str) -> str:
    text = (text or "").strip()
    if len(text) > MAX_OUTPUT_CHARS:
        return text[:MAX_OUTPUT_CHARS] + f"\n…（输出已截断，共 {len(text)} 字符）"
    return text


def _force_kill(proc: subprocess.Popen) -> None:
    try:
        proc.kill()
    except OSError:
        pass
    # Windows 兜底：taskkill 终止进程树，防止 -c 派生的残留
    if os.name == "nt" and proc.pid:
        try:
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                capture_output=True, timeout=5.0,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (OSError, subprocess.SubprocessError):
            pass
