"""图灵完备助手：把《Turing Complete》的文档 / 代码 / 电路 / 验收交给 N.E.K.O.。"""

from __future__ import annotations

import asyncio
import json
import socket
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

from . import install as tc_install
from . import tc


@neko_plugin
class TcCompanionPlugin(NekoPluginBase):
    def __init__(self, ctx):
        super().__init__(ctx)
        self._install_result = None

    # ------------------------------------------------------------------ config

    async def _cfg(self) -> dict:
        try:
            raw = await self.config.dump() or {}
        except Exception:
            raw = {}
        if not isinstance(raw, dict):
            return {}
        section = raw.get("tc")
        return section if isinstance(section, dict) else raw

    async def _ctx(self) -> tuple[dict, Path, Path | None, dict]:
        cfg = await self._cfg()
        user = tc.resolve_user_dir(cfg)
        game = tc.resolve_game_dir(cfg)
        settings = tc.read_settings(user)
        return cfg, user, game, settings

    def _data_file(self, name: str) -> Path:
        try:
            return Path(str(self.data_path(name)))
        except Exception:
            return Path.cwd() / name

    def _schematic(self, user: Path, settings: dict, level: str = "", schematic: str = "") -> Path:
        level = level or settings.get("setting_current_level") or ""
        if not level:
            raise SdkError("无法确定当前关卡，请显式传 level")
        progress = tc.level_progress(tc.read_levels(user), level)
        name = schematic or (progress or {}).get("schematic") or "Default"
        return tc.schematic_dir(user, level, name)

    @staticmethod
    def _code_dir(sdir: Path, file: str) -> Path:
        """spec.isa 属于架构根目录；new_program.asm 属于当前关卡子目录。"""
        if not (file or "").endswith(".isa"):
            return sdir
        folder = sdir
        for _ in range(4):
            if (folder / "spec.isa").exists():
                return folder
            if folder.parent == folder:
                break
            folder = folder.parent
        return sdir

    # --------------------------------------------------------------------- ops

    async def _status(self) -> dict:
        cfg, user, game, settings = await self._ctx()
        levels = tc.read_levels(user)
        current = settings.get("setting_current_level", "")
        out = {
            "user_dir": str(user),
            "game_dir": str(game) if game else None,
            "game_running": tc.is_game_running(),
            "current_level": current,
            "architecture": settings.get("setting_loaded_architecture", ""),
            "levels_total": len(levels),
            "levels_done": sum(1 for row in levels if row["done"]),
            "current_progress": tc.level_progress(levels, current) if current else None,
            "schematics": len(tc.list_schematics(user)),
            "manual_available": bool(game and tc.manual_dir(game).exists()),
            "allow_write": bool(cfg.get("allow_write", True)),
            "install": self._install_result,
        }
        if out["game_running"]:
            try:
                out["bridge"] = self._bridge({"cmd": "status"}, timeout=1.5)
            except SdkError as exc:
                out["bridge_error"] = str(exc)
        return out

    async def _docs_op(self, action: str = "list", query: str = "", name: str = "", level: str = "", limit: int = 20) -> dict:
        cfg, user, game, settings = await self._ctx()
        action = (action or "list").strip().lower()

        if action == "level":
            if not game:
                raise SdkError("找不到游戏目录，请在配置里填 game_dir")
            level = level or settings.get("setting_current_level") or ""
            if not level:
                raise SdkError("无法确定当前关卡")
            info = tc.level_info(game, level)
            info.pop("path", None)
            return info

        if not game:
            raise SdkError("找不到游戏目录，请在配置里填 game_dir")
        docs = tc.list_docs(game)
        if action == "list":
            return {"count": len(docs), "docs": [{"path": d["path"], "title": d["title"]} for d in docs]}
        if action == "search":
            hits = tc.search_docs(docs, query, limit)
            return {
                "query": query,
                "hits": [{"path": d["path"], "title": d["title"], "doc": d["doc"][:800]} for d in hits],
            }
        if action == "read":
            doc = tc.find_doc(docs, name or query)
            if not doc:
                raise SdkError(f"找不到文档：{name or query}")
            return doc
        raise SdkError(f"未知 action：{action}")

    async def _code_op(self, action: str = "list", file: str = "", code: str = "", level: str = "", schematic: str = "") -> dict:
        cfg, user, _, settings = await self._ctx()
        sdir = self._schematic(user, settings, level, schematic)
        action = (action or "list").strip().lower()

        if action == "list":
            isa_dir = self._code_dir(sdir, "spec.isa")
            folders = [sdir] if isa_dir == sdir else [sdir, isa_dir]
            return {
                "schematic_dir": str(sdir),
                "isa_dir": str(isa_dir),
                "files": [
                    {"name": p.name, "dir": str(p.parent), "bytes": p.stat().st_size}
                    for folder in folders
                    if folder.exists()
                    for p in sorted(folder.iterdir())
                    if p.is_file() and p.suffix in (".isa", ".asm", ".bin")
                ],
            }
        if action == "read":
            return {"file": file, "code": tc.read_code(self._code_dir(sdir, file), file)}
        if action == "write":
            if not bool(cfg.get("allow_write", True)):
                raise SdkError("配置里 allow_write=false，已拒绝写盘")
            if tc.is_game_running():
                raise SdkError("游戏正在运行；关卡打开时会覆盖文件，请先退出游戏")
            return tc.write_code(self._code_dir(sdir, file), file, code)
        if action == "validate":
            isa_dir = self._code_dir(sdir, "spec.isa")
            isa_text = tc.read_code(isa_dir, "spec.isa")
            asm_text = code if code else (
                tc.read_code(sdir, "new_program.asm") if (sdir / "new_program.asm").exists() else ""
            )
            isa = tc.parse_isa(isa_text)
            asm = tc.validate_asm(asm_text, isa)
            return {
                "isa": {"name": isa["settings"].get("name"), "instructions": isa["instructions"], "errors": isa["errors"]},
                "asm": asm,
            }
        raise SdkError(f"未知 action：{action}")

    async def _circuit_op(self, action: str = "list", level: str = "", schematic: str = "", source_level: str = "", source_schematic: str = "") -> dict:
        cfg, user, _, settings = await self._ctx()
        action = (action or "list").strip().lower()

        if action == "list":
            return {"schematics": tc.list_schematics(user)}
        if action == "info":
            sdir = self._schematic(user, settings, level, schematic)
            path = sdir / "circuit.data"
            if not path.exists():
                raise SdkError(f"没有电路文件：{path}")
            return tc.circuit_info(path)
        if action == "deploy":
            if not bool(cfg.get("allow_write", True)):
                raise SdkError("配置里 allow_write=false，已拒绝写盘")
            if tc.is_game_running():
                raise SdkError("游戏正在运行；请先退出游戏再部署电路")
            if not source_level:
                raise SdkError("deploy 需要 source_level")
            src = self._schematic(user, settings, source_level, source_schematic) / "circuit.data"
            dst = self._schematic(user, settings, level, schematic)
            return tc.deploy_circuit(src, dst)
        raise SdkError(f"未知 action：{action}")

    async def _accept_op(self, action: str = "check", level: str = "") -> dict:
        _, user, game, settings = await self._ctx()
        level = level or settings.get("setting_current_level") or ""
        levels = tc.read_levels(user)
        current = tc.level_progress(levels, level) if level else None
        action = (action or "check").strip().lower()

        if action == "check":
            return {
                "level": level,
                "progress": current,
                "done_count": sum(1 for row in levels if row["done"]),
                "total": len(levels),
            }
        if action == "snapshot":
            path = self._data_file("levels_snapshot.json")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(levels, ensure_ascii=False, indent=1), encoding="utf-8")
            return {"snapshot": str(path), "levels": len(levels)}
        if action == "test":
            if not game:
                raise SdkError("找不到游戏目录，请在配置里填 game_dir")
            info = tc.level_info(game, level)
            return {
                "level": level,
                "title": info.get("title"),
                "kind": info.get("kind"),
                "test": info.get("test.si", ""),
                "progress": current,
            }
        raise SdkError(f"未知 action：{action}")

    def _bridge(self, payload: dict, timeout: float = 8.0) -> dict:
        try:
            with socket.create_connection(("127.0.0.1", 47654), timeout=timeout) as sock:
                sock.sendall((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
                data = b""
                while not data.endswith(b"\n"):
                    chunk = sock.recv(65536)
                    if not chunk:
                        break
                    data += chunk
        except OSError as exc:
            raise SdkError(f"控制桥未运行（127.0.0.1:47654）：{exc}")
        try:
            return json.loads(data.decode("utf-8", "replace"))
        except json.JSONDecodeError as exc:
            raise SdkError(f"控制桥返回了非 JSON：{data[:200]!r}") from exc

    async def _bridge_op(self, action: str = "status", **extra) -> dict:
        action = (action or "status").strip().lower()
        allowed = {"status", "ping", "run", "stop", "reset", "undo", "redo", "save", "place", "components"}
        if action not in allowed:
            raise SdkError(f"未知 action：{action}")
        return self._bridge({"cmd": action, **extra})

    async def _solve_op(
        self,
        action: str = "context",
        file: str = "new_program.asm",
        code: str = "",
        level: str = "",
    ) -> dict:
        cfg, user, game, settings = await self._ctx()
        level = level or settings.get("setting_current_level") or ""
        sdir = self._schematic(user, settings, level)
        action = (action or "context").strip().lower()
        if action == "context":
            info = tc.level_info(game, level) if game else {}
            try:
                bridge = self._bridge({"cmd": "status"}, timeout=1.5)
            except SdkError:
                bridge = None
            files = {}
            for name in ("spec.isa", "new_program.asm"):
                try:
                    files[name] = tc.read_code(self._code_dir(sdir, name), name)
                except Exception:
                    pass
            return {
                "level": level,
                "title": info.get("title"),
                "kind": info.get("kind"),
                "test": (info.get("test.si") or "")[:4000],
                "files": files,
                "bridge": bridge,
                "hint": "先看 test 和 bridge；写 tc_solve action=apply file=new_program.asm code=...，插件会校验并返回结果。",
            }
        if action == "apply":
            if not code:
                raise SdkError("apply 需要 code")
            written = tc.write_code(self._code_dir(sdir, file), file, code)
            isa = tc.parse_isa(tc.read_code(self._code_dir(sdir, "spec.isa"), "spec.isa"))
            asm_text = code if file == "new_program.asm" else (
                tc.read_code(sdir, "new_program.asm") if (sdir / "new_program.asm").exists() else ""
            )
            return {"written": written, "validation": tc.validate_asm(asm_text, isa)}
        raise SdkError(f"未知 action：{action}")

    # ----------------------------------------------------------------- entries

    @plugin_entry(id="tc_status", name="图灵助手状态", description="查看当前关卡、架构、进度、电路存档数量和游戏是否运行。")
    async def tc_status(self, **_):
        try:
            return Ok(await self._status())
        except Exception as exc:
            return Err(SdkError(f"{type(exc).__name__}: {exc}"))

    @plugin_entry(id="tc_docs", name="读手册/关卡定义", description="list/search/read 内置手册；level 查看当前关卡的 meta/test/ui。")
    async def tc_docs(
        self,
        action: Annotated[str, "list / search / read / level"] = "list",
        query: Annotated[str, "搜索词"] = "",
        name: Annotated[str, "文档标题或路径"] = "",
        level: Annotated[str, "level 用：关卡 id"] = "",
        limit: Annotated[int, "最多条数"] = 20,
        **_,
    ):
        try:
            return Ok(await self._docs_op(action, query, name, level, limit))
        except SdkError as exc:
            return Err(exc)
        except Exception as exc:
            return Err(SdkError(f"{type(exc).__name__}: {exc}"))

    @plugin_entry(id="tc_code", name="读写 ISA/汇编", description="list/read/write/validate spec.isa 与 new_program.asm；validate 会检查指令、标签和位宽。")
    async def tc_code(
        self,
        action: Annotated[str, "list / read / write / validate"] = "list",
        file: Annotated[str, "spec.isa 或 new_program.asm"] = "",
        code: Annotated[str, "write 时的完整内容"] = "",
        level: Annotated[str, "关卡 id；留空用当前关卡"] = "",
        schematic: Annotated[str, "存档名；留空用该关卡的存档"] = "",
        **_,
    ):
        try:
            return Ok(await self._code_op(action, file, code, level, schematic))
        except SdkError as exc:
            return Err(exc)
        except Exception as exc:
            return Err(SdkError(f"{type(exc).__name__}: {exc}"))

    @plugin_entry(id="tc_circuit", name="电路存档", description="list/info 查看电路；deploy 把某个关卡的 circuit.data 复制到目标关卡（先备份）。")
    async def tc_circuit(
        self,
        action: Annotated[str, "list / info / deploy"] = "list",
        level: Annotated[str, "目标关卡"] = "",
        schematic: Annotated[str, "目标存档名"] = "",
        source_level: Annotated[str, "deploy 的源关卡"] = "",
        source_schematic: Annotated[str, "deploy 的源存档名"] = "",
        **_,
    ):
        try:
            return Ok(await self._circuit_op(action, level, schematic, source_level, source_schematic))
        except SdkError as exc:
            return Err(exc)
        except Exception as exc:
            return Err(SdkError(f"{type(exc).__name__}: {exc}"))

    @plugin_entry(id="tc_accept", name="验收", description="check 看当前关卡完成/分数；snapshot 存基线；test 看该关卡的测试定义。")
    async def tc_accept(
        self,
        action: Annotated[str, "check / snapshot / test"] = "check",
        level: Annotated[str, "关卡 id；留空用当前关卡"] = "",
        **_,
    ):
        try:
            return Ok(await self._accept_op(action, level))
        except SdkError as exc:
            return Err(exc)
        except Exception as exc:
            return Err(SdkError(f"{type(exc).__name__}: {exc}"))

    @plugin_entry(
        id="tc_bridge",
        name="原生控制桥",
        description="调用 tc_bridge.mod 的本地 TCP 控制口：status/run/stop/reset/place/undo/redo/save/components。",
    )
    async def tc_bridge(
        self,
        action: Annotated[str, "status/run/stop/reset/place/undo/redo/save/components"] = "status",
        cycles: Annotated[int, "run：跑多少个周期"] = 1000,
        kind: Annotated[int, "place：内置元件 kind"] = 0,
        custom_id: Annotated[int, "place：自定义元件 id"] = 0,
        x: Annotated[int, "place：x 坐标"] = 0,
        y: Annotated[int, "place：y 坐标"] = 0,
        rotation: Annotated[int, "place：旋转"] = 0,
        **_,
    ):
        try:
            extra = {}
            if action == "run":
                extra["cycles"] = cycles
            elif action == "place":
                extra.update({"kind": kind, "custom_id": custom_id, "x": x, "y": y, "rotation": rotation})
            return Ok(await self._bridge_op(action, **extra))
        except SdkError as exc:
            return Err(exc)
        except Exception as exc:
            return Err(SdkError(f"{type(exc).__name__}: {exc}"))

    @plugin_entry(
        id="tc_solve",
        name="自动写码闭环",
        description="context=当前关卡/测试/代码/棋盘；apply=写 ISA 或汇编并校验。",
    )
    async def tc_solve(
        self,
        action: Annotated[str, "context / apply"] = "context",
        file: Annotated[str, "spec.isa 或 new_program.asm"] = "new_program.asm",
        code: Annotated[str, "apply 时的完整内容"] = "",
        level: Annotated[str, "关卡 id；留空用当前关卡"] = "",
        **_,
    ):
        try:
            return Ok(await self._solve_op(action, file, code, level))
        except SdkError as exc:
            return Err(exc)
        except Exception as exc:
            return Err(SdkError(f"{type(exc).__name__}: {exc}"))

    # -------------------------------------------------------------- llm tools

    @llm_tool(
        name="tc_status",
        description="查看《Turing Complete》当前关卡、架构、进度和电路存档。",
        parameters={"type": "object", "properties": {}},
    )
    async def tool_status(self, **_):
        try:
            return {"output": await self._status()}
        except Exception as exc:
            return {"output": {"reason": f"{type(exc).__name__}: {exc}"}, "is_error": True, "error": "TC_STATUS_FAILED"}

    @llm_tool(
        name="tc_docs",
        description="查询《Turing Complete》内置手册与关卡测试定义。",
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["list", "search", "read", "level"]},
                "query": {"type": "string"},
                "name": {"type": "string"},
                "level": {"type": "string"},
                "limit": {"type": "integer"},
            },
        },
    )
    async def tool_docs(self, *, action: str = "list", query: str = "", name: str = "", level: str = "", limit: int = 20):
        try:
            return {"output": await self._docs_op(action, query, name, level, limit)}
        except SdkError as exc:
            return {"output": {"reason": str(exc)}, "is_error": True, "error": "TC_DOCS_REJECTED"}
        except Exception as exc:
            return {"output": {"reason": f"{type(exc).__name__}: {exc}"}, "is_error": True, "error": "TC_DOCS_FAILED"}

    @llm_tool(
        name="tc_code",
        description="读写并校验《Turing Complete》的 spec.isa 与 new_program.asm。",
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["list", "read", "write", "validate"]},
                "file": {"type": "string"},
                "code": {"type": "string"},
                "level": {"type": "string"},
                "schematic": {"type": "string"},
            },
        },
    )
    async def tool_code(
        self, *, action: str = "list", file: str = "", code: str = "", level: str = "", schematic: str = ""
    ):
        try:
            return {"output": await self._code_op(action, file, code, level, schematic)}
        except SdkError as exc:
            return {"output": {"reason": str(exc)}, "is_error": True, "error": "TC_CODE_REJECTED"}
        except Exception as exc:
            return {"output": {"reason": f"{type(exc).__name__}: {exc}"}, "is_error": True, "error": "TC_CODE_FAILED"}

    @llm_tool(
        name="tc_circuit",
        description="查看或部署《Turing Complete》的电路存档（circuit.data），部署前自动备份。",
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["list", "info", "deploy"]},
                "level": {"type": "string"},
                "schematic": {"type": "string"},
                "source_level": {"type": "string"},
                "source_schematic": {"type": "string"},
            },
        },
    )
    async def tool_circuit(
        self, *, action: str = "list", level: str = "", schematic: str = "", source_level: str = "", source_schematic: str = ""
    ):
        try:
            return {"output": await self._circuit_op(action, level, schematic, source_level, source_schematic)}
        except SdkError as exc:
            return {"output": {"reason": str(exc)}, "is_error": True, "error": "TC_CIRCUIT_REJECTED"}
        except Exception as exc:
            return {"output": {"reason": f"{type(exc).__name__}: {exc}"}, "is_error": True, "error": "TC_CIRCUIT_FAILED"}

    @llm_tool(
        name="tc_accept",
        description="验收《Turing Complete》关卡：完成状态、分数、以及该关卡的测试定义。",
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["check", "snapshot", "test"]},
                "level": {"type": "string"},
            },
        },
    )
    async def tool_accept(self, *, action: str = "check", level: str = ""):
        try:
            return {"output": await self._accept_op(action, level)}
        except SdkError as exc:
            return {"output": {"reason": str(exc)}, "is_error": True, "error": "TC_ACCEPT_REJECTED"}
        except Exception as exc:
            return {"output": {"reason": f"{type(exc).__name__}: {exc}"}, "is_error": True, "error": "TC_ACCEPT_FAILED"}

    @llm_tool(
        name="tc_bridge",
        description="通过 tc_bridge.mod 直接控制《Turing Complete》：运行/停止/重置、放元件、撤销/重做、保存、列元件。",
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["status", "run", "stop", "reset", "undo", "redo", "save", "place", "components"]},
                "cycles": {"type": "integer", "description": "run 的周期数"},
                "kind": {"type": "integer", "description": "place 的内置 kind"},
                "custom_id": {"type": "integer", "description": "place 的自定义元件 id"},
                "x": {"type": "integer"},
                "y": {"type": "integer"},
                "rotation": {"type": "integer"},
            },
        },
    )
    async def tool_bridge(
        self,
        *,
        action: str = "status",
        cycles: int = 1000,
        kind: int = 0,
        custom_id: int = 0,
        x: int = 0,
        y: int = 0,
        rotation: int = 0,
    ):
        try:
            extra = {}
            if action == "run":
                extra["cycles"] = cycles
            elif action == "place":
                extra.update({"kind": kind, "custom_id": custom_id, "x": x, "y": y, "rotation": rotation})
            return {"output": await self._bridge_op(action, **extra)}
        except SdkError as exc:
            return {"output": {"reason": str(exc)}, "is_error": True, "error": "TC_BRIDGE_REJECTED"}
        except Exception as exc:
            return {"output": {"reason": f"{type(exc).__name__}: {exc}"}, "is_error": True, "error": "TC_BRIDGE_FAILED"}

    @llm_tool(
        name="tc_solve",
        description="《Turing Complete》自动写码闭环：context 读关卡测试/代码/棋盘，apply 写 ISA 或汇编并校验。",
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["context", "apply"]},
                "file": {"type": "string"},
                "code": {"type": "string"},
                "level": {"type": "string"},
            },
        },
    )
    async def tool_solve(self, *, action: str = "context", file: str = "new_program.asm", code: str = "", level: str = ""):
        try:
            return {"output": await self._solve_op(action, file, code, level)}
        except SdkError as exc:
            return {"output": {"reason": str(exc)}, "is_error": True, "error": "TC_SOLVE_REJECTED"}
        except Exception as exc:
            return {"output": {"reason": f"{type(exc).__name__}: {exc}"}, "is_error": True, "error": "TC_SOLVE_FAILED"}

    # -------------------------------------------------------------- lifecycle

    @lifecycle(id="startup")
    async def on_startup(self, **_):
        cfg, user, game, settings = await self._ctx()
        try:
            self._install_result = await asyncio.to_thread(tc_install.install, cfg)
        except Exception as exc:
            self._install_result = {"ok": False, "stage": "error", "reason": f"{type(exc).__name__}: {exc}"}
        return Ok({
            "status": "ready",
            "current_level": settings.get("setting_current_level", ""),
            "game_dir_found": bool(game),
            "levels": len(tc.read_levels(user)),
            "install": self._install_result,
        })

    @lifecycle(id="shutdown")
    async def on_shutdown(self, **_):
        return Ok({"status": "stopped"})
