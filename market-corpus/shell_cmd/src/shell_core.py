"""
猫娘命令操作器 - 核心执行模块

跨平台执行 shell / cmd / powershell 命令，并提供本地文件读写能力。
内置安全围栏：命令超时、危险命令黑名单、输出截断。
"""

from __future__ import annotations

import os
import subprocess
from typing import Optional


class ShellExecutor:
    """跨平台命令执行器，带安全围栏。"""

    def __init__(
        self,
        workdir: str = "",
        timeout: int = 30,
        blacklist: Optional[list] = None,
        allow_dangerous: bool = False,
        max_output_lines: int = 200,
    ):
        self.workdir = workdir or os.getcwd()
        self.timeout = max(1, int(timeout))
        self.blacklist = [str(s).lower() for s in (blacklist or [])]
        self.allow_dangerous = bool(allow_dangerous)
        self.max_output_lines = max(10, int(max_output_lines))

    # ── 安全检查 ──
    def _is_dangerous(self, command: str) -> Optional[str]:
        if self.allow_dangerous:
            return None
        low = command.lower()
        for pattern in self.blacklist:
            if pattern and pattern in low:
                return pattern
        return None

    # ── 执行 ──
    def run(
        self,
        command: str,
        shell: str = "auto",
        cwd: Optional[str] = None,
        timeout: Optional[int] = None,
    ) -> dict:
        if not command or not command.strip():
            return {
                "ok": False,
                "error": "命令不能为空",
                "exit_code": -1,
                "stdout": "",
                "stderr": "",
                "timed_out": False,
            }

        danger = self._is_dangerous(command)
        if danger:
            return {
                "ok": False,
                "error": f"命令命中危险黑名单片段: '{danger}'，已拦截",
                "exit_code": -1,
                "stdout": "",
                "stderr": "",
                "timed_out": False,
            }

        work_dir = cwd or self.workdir
        try:
            os.makedirs(work_dir, exist_ok=True)
        except Exception:
            pass

        to = timeout or self.timeout
        shell_flag = True
        exec_args = command

        if shell == "powershell":
            exec_args = ["powershell", "-NoProfile", "-Command", command]
            shell_flag = False
        elif shell == "bash" and os.name != "nt":
            exec_args = ["bash", "-c", command]
            shell_flag = False
        elif shell == "cmd" and os.name == "nt":
            exec_args = ["cmd.exe", "/c", command]
            shell_flag = False

        try:
            proc = subprocess.run(
                exec_args,
                shell=shell_flag,
                cwd=work_dir,
                capture_output=True,
                text=True,
                timeout=to,
                encoding="utf-8",
                errors="replace",
            )
            return {
                "ok": proc.returncode == 0,
                "exit_code": proc.returncode,
                "stdout": self._truncate(proc.stdout or ""),
                "stderr": self._truncate(proc.stderr or ""),
                "timed_out": False,
                "shell": shell,
                "cwd": work_dir,
            }
        except subprocess.TimeoutExpired:
            return {
                "ok": False,
                "error": f"命令执行超时（>{to}s）",
                "exit_code": -1,
                "stdout": "",
                "stderr": "",
                "timed_out": True,
            }
        except Exception as e:
            return {
                "ok": False,
                "error": f"执行异常: {e}",
                "exit_code": -1,
                "stdout": "",
                "stderr": "",
                "timed_out": False,
            }

    def _truncate(self, text: str) -> str:
        lines = text.splitlines()
        if len(lines) > self.max_output_lines:
            head = lines[: self.max_output_lines]
            return "\n".join(head) + f"\n... (输出已截断，共 {len(lines)} 行)"
        return text

    # ── 文件操作 ──
    def list_dir(self, path: str) -> dict:
        target = path or self.workdir
        try:
            entries = []
            for name in sorted(os.listdir(target)):
                full = os.path.join(target, name)
                entries.append(
                    {
                        "name": name,
                        "is_dir": os.path.isdir(full),
                        "size": os.path.getsize(full) if os.path.isfile(full) else 0,
                    }
                )
            return {"ok": True, "path": target, "entries": entries, "count": len(entries)}
        except Exception as e:
            return {"ok": False, "error": f"列举目录失败: {e}"}

    def read_file(self, path: str, max_bytes: int = 200000) -> dict:
        try:
            if not os.path.isfile(path):
                return {"ok": False, "error": "文件不存在"}
            size = os.path.getsize(path)
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                if size > max_bytes:
                    content = f.read(max_bytes)
                    truncated = True
                else:
                    content = f.read()
                    truncated = False
            return {
                "ok": True,
                "path": path,
                "size": size,
                "content": content,
                "truncated": truncated,
            }
        except Exception as e:
            return {"ok": False, "error": f"读取文件失败: {e}"}

    def write_file(self, path: str, content: str, append: bool = False) -> dict:
        try:
            parent = os.path.dirname(os.path.abspath(path))
            os.makedirs(parent, exist_ok=True)
            mode = "a" if append else "w"
            with open(path, mode, encoding="utf-8") as f:
                f.write(content)
            return {
                "ok": True,
                "path": path,
                "bytes": len(content.encode("utf-8")),
                "mode": "append" if append else "write",
            }
        except Exception as e:
            return {"ok": False, "error": f"写入文件失败: {e}"}
