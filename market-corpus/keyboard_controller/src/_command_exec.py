"""Command execution for keyboard_controller.

Runs a shell command with bounded time and output so a text-only LLM can
drive simple automation (git status, file listing, pip install, etc.).

Safety notes:
- Commands run with the same privileges as the N.E.K.O host process.
- A hard timeout always applies (default 30s) and output is truncated.
- A window is not flashed (CREATE_NO_WINDOW) when supported.
- No interactive/persistent shell: each call spawns a fresh process.
"""

from __future__ import annotations

import locale
import os
import subprocess
import sys
from typing import Any, Optional

DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_MAX_OUTPUT_CHARS = 4000


def is_windows() -> bool:
    return sys.platform == "win32"


def _creation_flags() -> int:
    flags = 0
    if hasattr(subprocess, "CREATE_NO_WINDOW"):
        flags |= int(subprocess.CREATE_NO_WINDOW)
    if hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
        flags |= int(subprocess.CREATE_NEW_PROCESS_GROUP)
    return flags


def _encoding() -> str:
    try:
        return locale.getpreferredencoding(False) or "utf-8"
    except Exception:
        return "utf-8"


def _decode(data: bytes) -> str:
    if not data:
        return ""
    enc = _encoding()
    try:
        return data.decode(enc, errors="replace")
    except Exception:
        return data.decode("utf-8", errors="replace")


def _truncate(text: str, max_chars: int) -> str:
    text = text or ""
    max_chars = max(256, int(max_chars or 0))
    if len(text) <= max_chars:
        return text
    head = max_chars * 3 // 4
    tail = max_chars - head
    return f"{text[:head]}\n…[输出已截断，剩余 {len(text) - max_chars} 字符]…\n{text[-tail:]}"


def _encode_powershell_command(command: str) -> str:
    """Encode a PowerShell command as UTF-16LE Base64 for -EncodedCommand.

    Passing the command this way bypasses every quoting/escaping layer
    (cmd's %-expansion and quote stripping, PowerShell's own $-interpolation
    of the outer string), so variables like ``$shell`` survive intact.
    A leading ``$ProgressPreference`` guard keeps progress-stream CLIXML noise
    out of stdout so the text-only LLM gets clean output.
    """
    try:
        import base64
    except Exception:
        return ""
    try:
        body = str(command)
        if not body.lstrip().startswith("$ProgressPreference"):
            body = "$ProgressPreference='SilentlyContinue'; " + body
        utf16 = body.encode("utf-16-le")
        return base64.b64encode(utf16).decode("ascii")
    except Exception:
        return ""


def _build_command(command: str, shell: str) -> list[str]:
    shell = (shell or "auto").strip().lower()
    if is_windows():
        if shell in ("powershell", "pwsh"):
            encoded = _encode_powershell_command(command)
            if encoded:
                return ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded]
            return ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", command]
        if shell in ("cmd", "cmd.exe", "batch"):
            return ["cmd.exe", "/d", "/s", "/c", command]
        return ["cmd.exe", "/d", "/s", "/c", command]
    if shell in ("powershell", "pwsh"):
        encoded = _encode_powershell_command(command)
        if encoded:
            return ["pwsh", "-NoLogo", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded]
        return ["pwsh", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", command]
    if shell in ("bash", "sh"):
        return ["/bin/bash", "-lc", command]
    return ["/bin/sh", "-lc", command]


_PS_HINTS = (
    "$",  # PowerShell variable
    "-Command",
    "New-Object",
    "-ComObject",
    "Get-Process",
    "Get-ChildItem -Path",
    "| Select",
    "Write-Host",
    "Get-Item",
    "Invoke-",
    "ConvertTo-Json",
    "Get-WmiObject",
    "Get-CimInstance",
    "Get-Content",
    "Set-Content",
    "ForEach-Object",
    "Where-Object",
)


def _looks_like_powershell(command: str) -> bool:
    """Heuristic: does this command read like PowerShell rather than cmd?"""
    text = str(command or "").strip()
    if not text:
        return False
    # cmd also uses %, but PowerShell cmdlets are a strong signal.
    return any(hint in text for hint in _PS_HINTS)


def spawn_detached(command: str) -> tuple[bool, str]:
    """Fire-and-forget 启动 GUI 程序。

    引号感知分词后直接 ``Popen([exe, *args])``：不经过 cmd（其 /s /c 与
    内嵌引号组合会把命令搅碎，实测报“找不到 \\ 文件”）。
    单目标时优先 ``os.startfile``（ShellExecute 解析 App Paths，
    chrome/msedge/notepad 裸名可用）。返回 (success, error)。"""
    import os as _os

    command = str(command or "").strip()
    if not command:
        return False, "命令不能为空"
    import shlex as _shlex
    import subprocess as _sp

    def _startfile_single(target: str) -> Optional[bool]:
        try:
            _os.startfile(target)
            return True
        except FileNotFoundError:
            return None  # 让调用方决定是否继续尝试其他方式
        except OSError:
            return None

    try:
        tokens = [t.strip('"') for t in _shlex.split(command, posix=False)]
    except ValueError:
        tokens = [command]

    if not tokens:
        return False, "命令不能为空"

    # 单目标（裸名或带引号路径）：优先 ShellExecute
    if len(tokens) == 1:
        r = _startfile_single(tokens[0])
        if r is True:
            return True, ""
        # 裸名补 .exe 再试（notepad → notepad.exe）
        if _os.path.splitext(tokens[0])[1] == "":
            r2 = _startfile_single(tokens[0] + ".exe")
            if r2 is True:
                return True, ""
        return False, f"找不到可执行文件或协议：{tokens[0]}"

    exe, args = tokens[0], tokens[1:]
    # 相对裸名且无扩展名 → 补 .exe（CreateProcess 不查 App Paths）
    if _os.path.splitext(exe)[1] == "" and not _os.path.isabs(exe):
        candidate = exe + ".exe"
        if _os.path.isfile(candidate):
            exe = candidate
    try:
        if sys.platform == "win32":
            flags = 0x00000200 | 0x08000000  # NEW_GROUP | NO_WINDOW
            _sp.Popen(
                [exe] + args,
                creationflags=flags,
                stdin=_sp.DEVNULL, stdout=_sp.DEVNULL, stderr=_sp.DEVNULL,
            )
        else:
            _sp.Popen(
                [exe] + args,
                start_new_session=True,
                stdin=_sp.DEVNULL, stdout=_sp.DEVNULL, stderr=_sp.DEVNULL,
            )
        return True, ""
    except FileNotFoundError:
        # 最后回退：ShellExecute 试一次完整首词
        r = _startfile_single(exe)
        if r is True:
            return True, ""
        return False, f"找不到可执行文件：{exe}"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def run_command(
    command: str,
    *,
    shell: str = "auto",
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    max_output_chars: int = DEFAULT_MAX_OUTPUT_CHARS,
    cwd: str = "",
) -> dict[str, Any]:
    """Run ``command`` via a fresh shell process.

    Returns a dict with ``success``, ``returncode``, ``output`` (stdout+stderr),
    ``timed_out``, ``command`` and ``shell``.
    """
    command = str(command or "").strip()
    if not command:
        return {"success": False, "error": "命令不能为空"}

    if str(shell or "auto").strip().lower() == "auto" and is_windows() and _looks_like_powershell(command):
        shell = "powershell"

    argv = _build_command(command, shell)
    effective_shell = os.path.basename(argv[0]) if is_windows() else argv[0]
    cwd = str(cwd or "").strip()
    timeout = max(0.5, float(timeout or DEFAULT_TIMEOUT_SECONDS))

    run_kwargs: dict[str, Any] = {"capture_output": True, "timeout": timeout, "cwd": cwd or None}
    if is_windows():
        run_kwargs["creationflags"] = _creation_flags()
        run_kwargs["close_fds"] = True
    else:
        run_kwargs["start_new_session"] = True

    try:
        completed = subprocess.run(argv, **run_kwargs)
    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "timed_out": True,
            "returncode": None,
            "output": f"命令执行超时（>{int(timeout)}s），已终止。",
            "command": command,
            "shell": effective_shell,
        }
    except FileNotFoundError:
        return {
            "success": False,
            "returncode": None,
            "output": f"找不到 shell 可执行文件: {argv[0]}",
            "command": command,
            "shell": effective_shell,
        }
    except Exception as exc:
        return {
            "success": False,
            "returncode": None,
            "output": f"执行失败：{exc}",
            "command": command,
            "shell": effective_shell,
        }

    raw_output = b""
    if completed.stdout:
        raw_output += completed.stdout
    if completed.stderr:
        raw_output += completed.stderr

    output = _truncate(_decode(raw_output).rstrip(), max_output_chars)
    if not output:
        output = "（无输出）"

    return {
        "success": completed.returncode == 0,
        "returncode": int(completed.returncode),
        "timed_out": False,
        "output": output,
        "command": command,
        "shell": effective_shell,
    }


__all__ = [
    "DEFAULT_MAX_OUTPUT_CHARS",
    "DEFAULT_TIMEOUT_SECONDS",
    "is_windows",
    "run_command",
]
