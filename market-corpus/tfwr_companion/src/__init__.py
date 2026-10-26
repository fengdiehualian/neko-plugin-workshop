"""农夫助手：把《The Farmer Was Replaced》的文档 / 代码 / 验收交给 N.E.K.O.。"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Annotated

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

from . import install as tfr_install
from . import tfr


@neko_plugin
class TfwrCompanionPlugin(NekoPluginBase):
    def __init__(self, ctx):
        super().__init__(ctx)
        self._watch_task: asyncio.Task | None = None
        self._watch_stop = asyncio.Event()
        self._watch_last: dict = {}
        self._watch_last_run: str | None = None
        self._watch_error: str | None = None
        self._install_result = None

    # ------------------------------------------------------------------ config

    async def _cfg(self) -> dict:
        try:
            raw = await self.config.dump() or {}
        except Exception:
            raw = {}
        if not isinstance(raw, dict):
            return {}
        section = raw.get("tfr")
        return section if isinstance(section, dict) else raw

    def _paths(self, cfg: dict) -> tuple[Path, Path]:
        user = tfr.resolve_user_dir(cfg)
        return user, tfr.save_dir(user, str(cfg.get("save_name") or "Save0"))

    # --------------------------------------------------------------------- ops

    async def _status(self) -> dict:
        cfg = await self._cfg()
        user, sdir = self._paths(cfg)
        exe = str(cfg.get("game_exe") or tfr.GAME_EXE)
        out = {
            "user_dir": str(user),
            "save_dir": str(sdir),
            "save_exists": sdir.exists(),
            "game_running": tfr.is_game_running(exe),
            "allow_write": bool(cfg.get("allow_write", True)),
            "allow_input": bool(cfg.get("allow_input", True)),
            "file_watcher": tfr.read_options(user).get("file watcher"),
            "code_files": tfr.list_code_files(sdir),
            "install": self._install_result,
        }
        try:
            save = tfr.read_save(sdir)
            out["save"] = tfr.save_summary(save)
        except Exception as exc:
            out["save_error"] = f"{type(exc).__name__}: {exc}"
        try:
            out["last_output"] = tfr.parse_output(tfr.read_output(user))
        except Exception as exc:
            out["output_error"] = f"{type(exc).__name__}: {exc}"
        return out

    async def _docs_op(self, action: str = "list", query: str = "", name: str = "", limit: int = 20) -> dict:
        cfg = await self._cfg()
        user, _ = self._paths(cfg)
        entries = tfr.load_docs(user, str(cfg.get("save_name") or "Save0"))
        action = (action or "list").strip().lower()

        if action == "list":
            return {
                "count": len(entries),
                "functions": sorted(
                    {e["name"] for e in entries if e["kind"] == "def" and not e["name"].startswith("_")}
                ),
                "classes": sorted({e["name"] for e in entries if e["kind"] == "class"}),
            }
        if action == "search":
            return {"query": query, "hits": tfr.search_docs(entries, query, limit)}
        if action == "read":
            wanted = (name or query or "").strip().lower()
            hits = [e for e in entries if e["name"].lower() == wanted]
            if not hits:
                hits = tfr.search_docs(entries, name or query, 5)
            return {"name": name or query, "entries": hits}
        raise SdkError(f"未知 action：{action}")

    async def _code_op(self, action: str = "list", file: str = "", code: str = "", value: str = "") -> dict:
        cfg = await self._cfg()
        user, sdir = self._paths(cfg)
        action = (action or "list").strip().lower()

        if action == "list":
            return {
                "files": [
                    {"name": name, "bytes": (sdir / f"{name}.py").stat().st_size}
                    for name in tfr.list_code_files(sdir)
                ]
            }
        if action == "read":
            return {"file": file, "code": tfr.read_code(sdir, file)}
        if action == "write":
            if not bool(cfg.get("allow_write", True)):
                raise SdkError("配置里 allow_write=false，已拒绝写盘")
            exe = str(cfg.get("game_exe") or tfr.GAME_EXE)
            watcher = tfr.read_options(user).get("file watcher")
            if tfr.is_game_running(exe) and watcher != "enabled":
                raise SdkError(
                    "游戏正在运行且 file watcher 未开启：写入会被游戏退出时覆盖。"
                    "请先关游戏，或用 tfr_code action=watcher value=enabled 开启文件监视。"
                )
            return tfr.write_code(sdir, file, code)
        if action == "watcher":
            if tfr.is_game_running(str(cfg.get("game_exe") or tfr.GAME_EXE)):
                raise SdkError("游戏正在运行；options.txt 会在退出时被覆盖，请先关游戏")
            if value not in ("enabled", "disabled"):
                raise SdkError("value 只能是 enabled 或 disabled")
            return tfr.set_option(user, "file watcher", value)
        raise SdkError(f"未知 action：{action}")

    async def _run_op(self, action: str = "run", wait_seconds: float = 6.0) -> dict:
        cfg = await self._cfg()
        user, sdir = self._paths(cfg)
        exe = str(cfg.get("game_exe") or tfr.GAME_EXE)
        action = (action or "run").strip().lower()

        if action == "check":
            return {
                "save": tfr.save_summary(tfr.read_save(sdir)),
                "output": tfr.parse_output(tfr.read_output(user)),
            }
        if not bool(cfg.get("allow_input", True)):
            raise SdkError("配置里 allow_input=false；请手动按 F5，再用 tfr_run action=check 验收")
        if action == "stop":
            return {"sent": tfr.send_game_keys(exe=exe, stop_first=True)}
        if action == "run":
            return tfr.run_and_check(user, sdir, wait_seconds, exe)
        raise SdkError(f"未知 action：{action}")

    def _watch_status(self) -> dict:
        return {
            "running": bool(self._watch_task and not self._watch_task.done()),
            "last_change": self._watch_last_run,
            "last_error": self._watch_error,
            "files": dict(self._watch_last),
        }

    async def _watch_op(self, action: str = "status") -> dict:
        action = (action or "status").strip().lower()
        if action == "start":
            if self._watch_task and not self._watch_task.done():
                return self._watch_status()
            self._watch_stop.clear()
            self._watch_task = asyncio.create_task(self._watch_loop())
            return self._watch_status()
        if action == "stop":
            self._watch_stop.set()
            if self._watch_task:
                try:
                    await asyncio.wait_for(self._watch_task, timeout=5)
                except (asyncio.TimeoutError, asyncio.CancelledError):
                    pass
            self._watch_task = None
            return self._watch_status()
        return self._watch_status()

    async def _watch_loop(self) -> None:
        cfg = await self._cfg()
        exe = str(cfg.get("game_exe") or tfr.GAME_EXE)
        poll = max(1, int(cfg.get("watch_seconds", 2) or 2))
        _, sdir = self._paths(cfg)
        self._watch_last = tfr.code_mtimes(sdir)
        while not self._watch_stop.is_set():
            try:
                await asyncio.wait_for(self._watch_stop.wait(), timeout=poll)
                break
            except asyncio.TimeoutError:
                pass
            try:
                current = tfr.code_mtimes(sdir)
                if current != self._watch_last:
                    self._watch_last = current
                    if bool(cfg.get("auto_run", True)) and bool(cfg.get("allow_input", True)) and tfr.is_game_running(exe):
                        tfr.send_game_keys(exe=exe, stop_first=True)
                        self._watch_last_run = tfr.now_stamp()
                    self._watch_error = None
            except Exception as exc:
                self._watch_error = f"{type(exc).__name__}: {exc}"

    async def _solve_op(
        self,
        action: str = "context",
        file: str = "main",
        code: str = "",
        wait_seconds: float = 8.0,
    ) -> dict:
        cfg = await self._cfg()
        user, sdir = self._paths(cfg)
        action = (action or "context").strip().lower()
        if action == "context":
            files = {}
            for name in tfr.list_code_files(sdir):
                try:
                    files[name] = tfr.read_code(sdir, name)
                except Exception:
                    pass
            return {
                "code": files,
                "last_output": tfr.parse_output(tfr.read_output(user)),
                "save": tfr.save_summary(tfr.read_save(sdir)),
                "auto_run": self._watch_status(),
                "hint": "先用 tfr_docs search 查 API，再 tfr_solve action=apply file=main code=...；插件会自动执行并返回 output。",
            }
        if action == "apply":
            if not code:
                raise SdkError("apply 需要 code")
            written = tfr.write_code(sdir, file, code)
            await asyncio.sleep(min(max(float(wait_seconds), 2.0), 30.0))
            return {
                "written": written,
                "output": tfr.parse_output(tfr.read_output(user)),
                "save": tfr.save_summary(tfr.read_save(sdir)),
            }
        raise SdkError(f"未知 action：{action}")

    # ----------------------------------------------------------------- entries

    @plugin_entry(id="tfr_status", name="农夫助手状态", description="查看游戏是否运行、存档、资源、解锁、文件监视和最近一次输出。")
    async def tfr_status(self, **_):
        try:
            return Ok(await self._status())
        except Exception as exc:
            return Err(SdkError(f"{type(exc).__name__}: {exc}"))

    @plugin_entry(id="tfr_docs", name="读 API 文档", description="读取游戏内置 __builtins__.py：list 列表、search 搜索、read 看某个函数或类。")
    async def tfr_docs(
        self,
        action: Annotated[str, "list / search / read"] = "list",
        query: Annotated[str, "搜索词，如 harvest、water、plant"] = "",
        name: Annotated[str, "read 时精确函数名"] = "",
        limit: Annotated[int, "最多返回多少条"] = 20,
        **_,
    ):
        try:
            return Ok(await self._docs_op(action, query, name, limit))
        except SdkError as exc:
            return Err(exc)
        except Exception as exc:
            return Err(SdkError(f"{type(exc).__name__}: {exc}"))

    @plugin_entry(id="tfr_code", name="读写代码", description="list/read/write 玩家代码文件；watcher 开关控制游戏是否实时读取磁盘上的改动。")
    async def tfr_code(
        self,
        action: Annotated[str, "list / read / write / watcher"] = "list",
        file: Annotated[str, "main / f0 / f1 ..."] = "",
        code: Annotated[str, "write 时的完整代码"] = "",
        value: Annotated[str, "watcher 用：enabled / disabled"] = "",
        **_,
    ):
        try:
            return Ok(await self._code_op(action, file, code, value))
        except SdkError as exc:
            return Err(exc)
        except Exception as exc:
            return Err(SdkError(f"{type(exc).__name__}: {exc}"))

    @plugin_entry(id="tfr_run", name="执行与验收", description="run=Shift+F5 再 F5 并对比输出；stop=只按 Shift+F5；check=只读最近输出。")
    async def tfr_run(
        self,
        action: Annotated[str, "run / stop / check"] = "run",
        wait_seconds: Annotated[float, "跑完后等多久再读输出"] = 6.0,
        **_,
    ):
        try:
            return Ok(await self._run_op(action, wait_seconds))
        except SdkError as exc:
            return Err(exc)
        except Exception as exc:
            return Err(SdkError(f"{type(exc).__name__}: {exc}"))

    @plugin_entry(
        id="tfr_watch",
        name="自动执行",
        description="start/stop/status：监听 main.py 等文件变化，自动 Shift+F5→F5。",
    )
    async def tfr_watch(self, action: Annotated[str, "start / stop / status"] = "status", **_):
        try:
            return Ok(await self._watch_op(action))
        except Exception as exc:
            return Err(SdkError(f"{type(exc).__name__}: {exc}"))

    @plugin_entry(
        id="tfr_solve",
        name="自动写码闭环",
        description="context=读取当前代码/输出/存档；apply=写代码并等待自动执行后返回结果。",
    )
    async def tfr_solve(
        self,
        action: Annotated[str, "context / apply"] = "context",
        file: Annotated[str, "main / f0 / f1 ..."] = "main",
        code: Annotated[str, "apply 时的完整代码"] = "",
        wait_seconds: Annotated[float, "apply 后等多久读输出"] = 8.0,
        **_,
    ):
        try:
            return Ok(await self._solve_op(action, file, code, wait_seconds))
        except SdkError as exc:
            return Err(exc)
        except Exception as exc:
            return Err(SdkError(f"{type(exc).__name__}: {exc}"))

    # -------------------------------------------------------------- llm tools

    @llm_tool(
        name="tfr_status",
        description="查看《The Farmer Was Replaced》当前状态：游戏是否运行、资源、解锁数、文件监视、最近输出。",
        parameters={"type": "object", "properties": {}},
    )
    async def tool_status(self, **_):
        try:
            return {"output": await self._status()}
        except Exception as exc:
            return {"output": {"reason": f"{type(exc).__name__}: {exc}"}, "is_error": True, "error": "TFR_STATUS_FAILED"}

    @llm_tool(
        name="tfr_docs",
        description="查询《The Farmer Was Replaced》内置 Python API 文档（函数签名与用法说明）。",
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["list", "search", "read"]},
                "query": {"type": "string", "description": "搜索词"},
                "name": {"type": "string", "description": "精确函数/类名"},
                "limit": {"type": "integer", "description": "最多条数"},
            },
        },
    )
    async def tool_docs(self, *, action: str = "list", query: str = "", name: str = "", limit: int = 20):
        try:
            return {"output": await self._docs_op(action, query, name, limit)}
        except SdkError as exc:
            return {"output": {"reason": str(exc)}, "is_error": True, "error": "TFR_DOCS_REJECTED"}
        except Exception as exc:
            return {"output": {"reason": f"{type(exc).__name__}: {exc}"}, "is_error": True, "error": "TFR_DOCS_FAILED"}

    @llm_tool(
        name="tfr_code",
        description="读写《The Farmer Was Replaced》的玩家代码文件（main / f0 / f1 ...），写入前自动备份。",
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["list", "read", "write", "watcher"]},
                "file": {"type": "string", "description": "main / f0 / f1 ..."},
                "code": {"type": "string", "description": "write 时的完整代码"},
                "value": {"type": "string", "enum": ["enabled", "disabled"]},
            },
        },
    )
    async def tool_code(self, *, action: str = "list", file: str = "", code: str = "", value: str = ""):
        try:
            return {"output": await self._code_op(action, file, code, value)}
        except SdkError as exc:
            return {"output": {"reason": str(exc)}, "is_error": True, "error": "TFR_CODE_REJECTED"}
        except Exception as exc:
            return {"output": {"reason": f"{type(exc).__name__}: {exc}"}, "is_error": True, "error": "TFR_CODE_FAILED"}

    @llm_tool(
        name="tfr_run",
        description="在《The Farmer Was Replaced》里执行代码并验收：发 F5 后读取 output.txt 的报错/警告和 save.json 的资源变化。",
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["run", "stop", "check"]},
                "wait_seconds": {"type": "number", "description": "跑完等待秒数，默认 6"},
            },
        },
    )
    async def tool_run(self, *, action: str = "run", wait_seconds: float = 6.0):
        try:
            return {"output": await self._run_op(action, wait_seconds)}
        except SdkError as exc:
            return {"output": {"reason": str(exc)}, "is_error": True, "error": "TFR_RUN_REJECTED"}
        except Exception as exc:
            return {"output": {"reason": f"{type(exc).__name__}: {exc}"}, "is_error": True, "error": "TFR_RUN_FAILED"}

    @llm_tool(
        name="tfr_watch",
        description="监听《The Farmer Was Replaced》的代码文件变化并自动执行（无需手按 F5）。",
        parameters={
            "type": "object",
            "properties": {"action": {"type": "string", "enum": ["start", "stop", "status"]}},
        },
    )
    async def tool_watch(self, *, action: str = "status"):
        try:
            return {"output": await self._watch_op(action)}
        except SdkError as exc:
            return {"output": {"reason": str(exc)}, "is_error": True, "error": "TFR_WATCH_REJECTED"}
        except Exception as exc:
            return {"output": {"reason": f"{type(exc).__name__}: {exc}"}, "is_error": True, "error": "TFR_WATCH_FAILED"}

    @llm_tool(
        name="tfr_solve",
        description="《The Farmer Was Replaced》自动写码闭环：context 读当前代码/输出/存档，apply 写代码并自动执行后返回结果。",
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["context", "apply"]},
                "file": {"type": "string"},
                "code": {"type": "string"},
                "wait_seconds": {"type": "number"},
            },
        },
    )
    async def tool_solve(
        self, *, action: str = "context", file: str = "main", code: str = "", wait_seconds: float = 8.0
    ):
        try:
            return {"output": await self._solve_op(action, file, code, wait_seconds)}
        except SdkError as exc:
            return {"output": {"reason": str(exc)}, "is_error": True, "error": "TFR_SOLVE_REJECTED"}
        except Exception as exc:
            return {"output": {"reason": f"{type(exc).__name__}: {exc}"}, "is_error": True, "error": "TFR_SOLVE_FAILED"}

    # -------------------------------------------------------------- lifecycle

    @lifecycle(id="startup")
    async def on_startup(self, **_):
        cfg = await self._cfg()
        user, sdir = self._paths(cfg)
        try:
            self._install_result = await asyncio.to_thread(tfr_install.install, cfg)
        except Exception as exc:
            self._install_result = {"ok": False, "stage": "error", "reason": f"{type(exc).__name__}: {exc}"}
        if bool(cfg.get("auto_run", True)):
            await self._watch_op("start")
        return Ok(
            {
                "status": "ready",
                "save_exists": sdir.exists(),
                "file_watcher": tfr.read_options(user).get("file watcher"),
                "auto_run": bool(cfg.get("auto_run", True)),
                "install": self._install_result,
            }
        )

    @lifecycle(id="shutdown")
    async def on_shutdown(self, **_):
        if self._watch_task and not self._watch_task.done():
            self._watch_stop.set()
            try:
                await asyncio.wait_for(self._watch_task, timeout=5)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass
            self._watch_task = None
        return Ok({"status": "stopped"})
