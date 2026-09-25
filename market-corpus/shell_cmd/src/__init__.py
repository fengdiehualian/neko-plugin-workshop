"""
猫娘命令操作器 v0.1 (Shell Command)

让猫娘在对话里直接执行 Shell / CMD / PowerShell 命令，并管理本地文件。
内置安全围栏：命令超时、危险命令黑名单、输出截断。

入口:
  - run        : 执行系统命令
  - list_dir   : 列出目录内容
  - read_file  : 读取文本文件
  - write_file : 写入文本文件
  - get_status : 查看当前配置状态
"""

from __future__ import annotations

import threading
from typing import Optional

from plugin.sdk.plugin import (
    Err,
    NekoPluginBase,
    Ok,
    SdkError,
    lifecycle,
    llm_tool,
    neko_plugin,
    plugin_entry,
)

from .shell_core import ShellExecutor

_PLUGIN_ID = "shell_cmd"
_DEFAULT_TIMEOUT = 30
_DEFAULT_BLACKLIST = [
    "rm -rf /",
    "format ",
    "mkfs",
    "shutdown",
    "restart",
    ":(){",
    "dd if=",
    "> /dev/sda",
    "chmod -R 000",
    "del /f /s /q",
    "rd /s /q",
    "rmdir /s /q",
    "curl | sh",
    "wget | sh",
]


@neko_plugin
class ShellCmdPlugin(NekoPluginBase):
    """猫娘命令操作器 - N.E.K.O 插件入口"""

    def __init__(self, ctx):
        super().__init__(ctx)
        self.file_logger = self.enable_file_logging(log_level="INFO")
        self.logger = self.file_logger
        self._lock = threading.Lock()
        self._executor: Optional[ShellExecutor] = None

    # ── lifecycle ──
    @lifecycle(id="startup")
    async def startup(self, **_):
        cfg = await self.config.dump(timeout=5.0)
        cfg = cfg if isinstance(cfg, dict) else {}
        section = cfg.get("shell_cmd") if isinstance(cfg.get("shell_cmd"), dict) else {}

        timeout = int(section.get("timeout", _DEFAULT_TIMEOUT))
        workdir = str(section.get("workdir", "")).strip()
        allow_dangerous = bool(section.get("allow_dangerous", False))
        max_output_lines = int(section.get("max_output_lines", 200))

        blacklist_raw = section.get("blacklist", _DEFAULT_BLACKLIST)
        if isinstance(blacklist_raw, str):
            blacklist = [s.strip() for s in blacklist_raw.split(",") if s.strip()]
        elif isinstance(blacklist_raw, list):
            blacklist = [str(s) for s in blacklist_raw]
        else:
            blacklist = list(_DEFAULT_BLACKLIST)

        self._executor = ShellExecutor(
            workdir=workdir,
            timeout=timeout,
            blacklist=blacklist,
            allow_dangerous=allow_dangerous,
            max_output_lines=max_output_lines,
        )
        self.logger.info(
            f"ShellCmd 插件已启动: timeout={timeout}s, workdir={workdir or 'CWD'}, "
            f"allow_dangerous={allow_dangerous}"
        )
        return Ok({"status": "running", "version": "1.0.1"})

    @lifecycle(id="shutdown")
    def shutdown(self, **_):
        self.logger.info("ShellCmd 插件已停止")
        return Ok({"status": "shutdown"})

    # ── 辅助 ──
    def _get_executor(self) -> ShellExecutor:
        if self._executor is None:
            raise RuntimeError("命令执行器未初始化，请检查插件配置")
        return self._executor

    # ── 入口：执行命令 ──
    @llm_tool(
        name="shell_cmd_run",
        description="执行一条 Shell / CMD / PowerShell 命令，返回 stdout、stderr 与退出码。"
        "当用户想让猫娘直接在系统里跑命令时使用。",
        parameters={
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "要执行的命令"},
                "shell": {
                    "type": "string",
                    "description": "执行环境: auto(默认) / cmd / bash / powershell",
                },
                "cwd": {"type": "string", "description": "工作目录(可选)"},
                "timeout": {"type": "integer", "description": "超时秒数(可选)"},
            },
            "required": ["command"],
        },
        timeout=30.0,
    )
    @plugin_entry(
        id="run",
        name="执行命令",
        description="执行 Shell / CMD / PowerShell 命令",
        input_schema={
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "shell": {"type": "string"},
                "cwd": {"type": "string"},
                "timeout": {"type": "integer"},
            },
            "required": ["command"],
        },
        llm_result_fields=["ok", "exit_code", "stdout", "stderr", "timed_out"],
    )
    async def run(
        self,
        command: str,
        shell: str = "auto",
        cwd: Optional[str] = None,
        timeout: Optional[int] = None,
        **_,
    ):
        try:
            ex = self._get_executor()
            result = ex.run(command=command, shell=shell, cwd=cwd, timeout=timeout)
            if not result.get("ok"):
                return Err(SdkError(result.get("error", "执行失败")))
            return Ok(result)
        except Exception as e:
            return Err(SdkError(f"执行命令失败: {e}"))

    # ── 入口：列出目录 ──
    @llm_tool(
        name="shell_cmd_list_dir",
        description="列出某个目录下的文件与子目录。当用户想看看目录里有什么时使用。",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "目录路径，默认插件工作目录"}
            },
            "required": [],
        },
        timeout=15.0,
    )
    @plugin_entry(
        id="list_dir",
        name="列出目录",
        description="列出目录内容",
        input_schema={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": [],
        },
        llm_result_fields=["ok", "path", "entries", "count"],
    )
    async def list_dir(self, path: Optional[str] = None, **_):
        try:
            ex = self._get_executor()
            result = ex.list_dir(path or "")
            if not result.get("ok"):
                return Err(SdkError(result.get("error", "列举失败")))
            return Ok(result)
        except Exception as e:
            return Err(SdkError(f"列举目录失败: {e}"))

    # ── 入口：读取文件 ──
    @llm_tool(
        name="shell_cmd_read_file",
        description="读取一个文本文件的内容。当用户想让猫娘查看某个文件时使用。",
        parameters={
            "type": "object",
            "properties": {"path": {"type": "string", "description": "文件路径"}},
            "required": ["path"],
        },
        timeout=15.0,
    )
    @plugin_entry(
        id="read_file",
        name="读取文件",
        description="读取文本文件内容",
        input_schema={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
        llm_result_fields=["ok", "path", "content", "truncated"],
    )
    async def read_file(self, path: str, **_):
        try:
            ex = self._get_executor()
            result = ex.read_file(path)
            if not result.get("ok"):
                return Err(SdkError(result.get("error", "读取失败")))
            return Ok(result)
        except Exception as e:
            return Err(SdkError(f"读取文件失败: {e}"))

    # ── 入口：写入文件 ──
    @llm_tool(
        name="shell_cmd_write_file",
        description="把文本内容写入一个文件。可覆盖或追加。当用户想让猫娘创建/修改文件时使用。",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "文件路径"},
                "content": {"type": "string", "description": "要写入的内容"},
                "append": {"type": "boolean", "description": "是否追加(默认 false 覆盖)"},
            },
            "required": ["path", "content"],
        },
        timeout=15.0,
    )
    @plugin_entry(
        id="write_file",
        name="写入文件",
        description="写入文本文件",
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
                "append": {"type": "boolean"},
            },
            "required": ["path", "content"],
        },
        llm_result_fields=["ok", "path", "bytes", "mode"],
    )
    async def write_file(
        self, path: str, content: str, append: bool = False, **_
    ):
        try:
            ex = self._get_executor()
            result = ex.write_file(path, content, append=append)
            if not result.get("ok"):
                return Err(SdkError(result.get("error", "写入失败")))
            return Ok(result)
        except Exception as e:
            return Err(SdkError(f"写入文件失败: {e}"))

    # ── 入口：查看状态 ──
    @llm_tool(
        name="shell_cmd_get_status",
        description="查看命令操作器的当前配置状态(超时、工作目录、危险命令放行等)。",
        parameters={"type": "object", "properties": {}, "required": []},
        timeout=10.0,
    )
    @plugin_entry(
        id="get_status",
        name="查看状态",
        description="查看当前配置状态",
        input_schema={"type": "object", "properties": {}, "required": []},
        llm_result_fields=["version", "timeout", "workdir", "allow_dangerous", "max_output_lines"],
    )
    async def get_status(self, **_):
        try:
            ex = self._get_executor()
            return Ok(
                {
                    "version": "1.0.1",
                    "timeout": ex.timeout,
                    "workdir": ex.workdir,
                    "allow_dangerous": ex.allow_dangerous,
                    "max_output_lines": ex.max_output_lines,
                    "blacklist_size": len(ex.blacklist),
                }
            )
        except Exception as e:
            return Err(SdkError(f"获取状态失败: {e}"))
