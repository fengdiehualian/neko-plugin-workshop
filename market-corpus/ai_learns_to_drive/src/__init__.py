"""学车助手：把《AI Learns To Drive》的存档层能力交给 N.E.K.O.。

只读/写游戏自己的 gzip+JSON 文件，不注入进程。
"""

from __future__ import annotations

import asyncio
import json
import subprocess
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

from . import ald
from . import install as ald_install


@neko_plugin
class AiLearnsToDrivePlugin(NekoPluginBase):
    def __init__(self, ctx):
        super().__init__(ctx)
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._last_genome_mtime = 0.0
        self._last_poll: str | None = None
        self._last_decision: dict | None = None
        self._last_error: str | None = None
        self._install_result = None

    # ------------------------------------------------------------------ config

    async def _cfg(self) -> dict:
        try:
            raw = await self.config.dump() or {}
        except Exception:
            raw = {}
        if not isinstance(raw, dict):
            return {}
        section = raw.get("ald")
        return section if isinstance(section, dict) else raw

    def _data_file(self, name: str) -> Path:
        try:
            return Path(str(self.data_path(name)))
        except Exception:
            return Path.cwd() / name

    def _user_dir(self, cfg: dict) -> Path:
        return ald.resolve_user_dir(cfg)

    # ------------------------------------------------------------------- ops

    async def _status(self) -> dict:
        cfg = await self._cfg()
        user = self._user_dir(cfg)
        exe = str(cfg.get("game_exe") or ald.GAME_EXE)
        out: dict = {
            "user_dir": str(user),
            "settings_file": str(ald.settings_path(user)),
            "settings_exists": ald.settings_path(user).exists(),
            "game_running": ald.is_game_running(exe),
            "allow_write": bool(cfg.get("allow_write", True)),
            "autopilot": self._autopilot_status(cfg),
            "install": self._install_result,
        }
        try:
            settings = ald.load_settings(user)
            out["evolution_settings"] = settings.get("Evolution") or {}
            out["system_settings"] = {
                k: (settings.get("System") or {}).get(k)
                for k in ("MaxPopulation2", "MaxTimeLimit", "MaxSelectionSize", "MaxDegreesOfParallelism")
            }
        except Exception as exc:
            out["settings_error"] = f"{type(exc).__name__}: {exc}"

        genomes = ald.list_genomes(user)
        out["genome_count"] = len(genomes)
        if genomes:
            try:
                out["newest_genome"] = ald.genome_summary(genomes[0])
            except Exception as exc:
                out["newest_genome_error"] = f"{type(exc).__name__}: {exc}"
        return out

    def _autopilot_status(self, cfg: dict) -> dict:
        return {
            "running": bool(self._task and not self._task.done()),
            "enabled_in_config": bool(cfg.get("autopilot_enabled", False)),
            "auto_apply_after_exit": bool(cfg.get("auto_apply_after_exit", True)),
            "poll_seconds": int(cfg.get("poll_seconds", 10) or 10),
            "last_poll": self._last_poll,
            "last_decision": self._last_decision,
            "last_error": self._last_error,
        }

    async def _settings_op(
        self,
        action: str = "get",
        changes: dict | None = None,
        preset: str = "",
        dry_run: bool = False,
        force: bool = False,
    ) -> dict:
        cfg = await self._cfg()
        user = self._user_dir(cfg)
        settings = ald.load_settings(user)
        action = (action or "get").strip().lower()

        if action == "get":
            return {
                "evolution": settings.get("Evolution") or {},
                "system": settings.get("System") or {},
                "presets": sorted(ald.PRESETS),
            }

        if action == "recommend":
            genome_path = ald.newest_genome(user)
            if not genome_path:
                return {"changes": {}, "reason": "还没有 .evolution 基因组文件。"}
            genome = ald.read_json(genome_path)
            rec = ald.recommend((genome.get("Stats") or {}).get("Generations") or [], settings)
            rec["genome"] = genome_path.name
            return rec

        if action == "preset":
            if preset not in ald.PRESETS:
                raise SdkError(f"未知预设：{preset or '(空)'}，可用 {sorted(ald.PRESETS)}")
            changes = ald.preset_changes(preset)
        elif action == "set":
            if not changes:
                raise SdkError("action=set 需要 changes")
        else:
            raise SdkError(f"未知 action：{action}")

        if not bool(cfg.get("allow_write", True)):
            raise SdkError("配置里 allow_write=false，已拒绝写盘")
        if ald.is_game_running(str(cfg.get("game_exe") or ald.GAME_EXE)) and not force:
            raise SdkError("游戏正在运行；它退出时会覆盖设置。请先退出游戏，或显式 force=true")

        new_settings, applied, rejected = ald.apply_changes(settings, changes)
        if rejected:
            return {"applied": {}, "rejected": rejected, "dry_run": dry_run}
        result = {"applied": applied, "rejected": [], "dry_run": dry_run}
        if dry_run:
            result["preview"] = {
                "Evolution": new_settings.get("Evolution") or {},
                "System": new_settings.get("System") or {},
            }
            return result

        backup = ald.save_settings(user, new_settings)
        result["backup"] = str(backup.parent / backup.name) if backup else None
        return result

    async def _genomes_op(
        self,
        action: str = "list",
        name: str = "",
        limit: int = 20,
        include_weights: bool = False,
        strength: float = 0.1,
        rate: float = 0.1,
        copies: int = 1,
    ) -> dict:
        cfg = await self._cfg()
        user = self._user_dir(cfg)
        files = ald.list_genomes(user)
        action = (action or "list").strip().lower()

        if action == "list":
            limit = max(1, min(int(limit or 20), 200))
            return {"count": len(files), "genomes": [ald.genome_summary(p) for p in files[:limit]]}

        target = self._pick_genome(files, name)
        if action == "inspect":
            data = ald.read_json(target)
            if not include_weights:
                data.pop("NeuralNetwork", None)
                data["neural_network"] = ald.genome_summary(target)
            return {"file": target.name, "genome": data}

        if action == "trend":
            data = ald.read_json(target)
            gens = (data.get("Stats") or {}).get("Generations") or []
            limit = max(1, min(int(limit or 20), 500))
            return {"file": target.name, "generations": gens[-limit:], "total": len(gens)}

        if action == "mutate":
            if not bool(cfg.get("allow_write", True)):
                raise SdkError("配置里 allow_write=false，已拒绝写盘")
            strength = max(0.0, min(float(strength or 0.1), 1.0))
            rate = max(0.0, min(float(rate or 0.1), 1.0))
            copies = max(1, min(int(copies or 1), 10))
            made = []
            stamp = ald.datetime.now().strftime("%Y%m%d-%H%M%S")
            for i in range(copies):
                data = ald.read_json(target)
                ald.mutate_genome(data, strength=strength, rate=rate)
                suffix = f"-mut{stamp}" + (f"-{i + 1}" if copies > 1 else "")
                base = str(data.get("Name") or target.stem)
                base = base.translate({ord(c): "_" for c in '<>:"/\\|?*'})
                data["Name"] = f"{base}{suffix}"
                out_path = ald.evolution_dir(user) / f"{data['Name']}.evolution"
                ald.write_json(out_path, data)
                made.append(out_path.name)
            return {"source": target.name, "created": made, "strength": strength, "rate": rate}

        raise SdkError(f"未知 action：{action}")

    @staticmethod
    def _pick_genome(files: list[Path], name: str) -> Path:
        if name:
            for path in files:
                if path.name == name or path.stem == name:
                    return path
            raise SdkError(f"找不到基因组：{name}")
        if not files:
            raise SdkError("还没有 .evolution 基因组文件")
        return files[0]

    async def _autopilot_op(self, action: str = "status") -> dict:
        cfg = await self._cfg()
        action = (action or "status").strip().lower()
        if action == "start":
            if self._task and not self._task.done():
                return self._autopilot_status(cfg)
            self._stop.clear()
            self._task = asyncio.create_task(self._autopilot_loop())
            return self._autopilot_status(cfg)
        if action == "stop":
            self._stop.set()
            if self._task:
                try:
                    await asyncio.wait_for(self._task, timeout=5)
                except (asyncio.TimeoutError, asyncio.CancelledError):
                    pass
            self._task = None
            return self._autopilot_status(cfg)
        return self._autopilot_status(cfg)

    async def _autopilot_loop(self) -> None:
        cfg = await self._cfg()
        exe = str(cfg.get("game_exe") or ald.GAME_EXE)
        poll = max(3, int(cfg.get("poll_seconds", 10) or 10))
        running = ald.is_game_running(exe)
        while not self._stop.is_set():
            try:
                now = ald.is_game_running(exe)
                if running and not now:
                    await asyncio.sleep(2)  # 等游戏把最后一次存档写完
                    self._last_decision = await self._after_game_exit(await self._cfg())
                running = now
                self._last_poll = ald.now_iso()
                self._last_error = None
            except Exception as exc:
                self._last_error = f"{type(exc).__name__}: {exc}"
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=poll)
            except asyncio.TimeoutError:
                pass

    async def _after_game_exit(self, cfg: dict) -> dict:
        user = self._user_dir(cfg)
        genome_path = ald.newest_genome(user)
        if not genome_path:
            return {"skipped": "没有 .evolution 文件"}
        mtime = genome_path.stat().st_mtime
        if mtime <= self._last_genome_mtime:
            return {"skipped": "本次没有新的训练数据", "genome": genome_path.name}
        self._last_genome_mtime = mtime

        genome = ald.read_json(genome_path)
        settings = ald.load_settings(user)
        rec = ald.recommend((genome.get("Stats") or {}).get("Generations") or [], settings)
        decision = {
            "time": ald.now_iso(),
            "genome": genome_path.name,
            "reason": rec.get("reason"),
            "changes": rec.get("changes") or {},
            "applied": False,
        }
        if decision["changes"] and bool(cfg.get("auto_apply_after_exit", True)):
            new_settings, applied, rejected = ald.apply_changes(settings, decision["changes"])
            if applied and not rejected:
                ald.save_settings(user, new_settings)
                decision["applied"] = True
                decision["applied_changes"] = applied
            else:
                decision["rejected"] = rejected

        log = self._data_file("autopilot.jsonl")
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(decision, ensure_ascii=False) + "\n")
        return decision

    async def _run_op(self, action: str = "status", key: str = "") -> dict:
        cfg = await self._cfg()
        exe = str(cfg.get("game_exe") or ald.GAME_EXE)
        action = (action or "status").strip().lower()
        if action == "status":
            pid = ald.game_pid(exe)
            return {
                "game_running": pid is not None,
                "window": bool(pid and ald.find_window(pid)),
                "run_key": cfg.get("run_key", "F5"),
                "stop_key": cfg.get("stop_key", "Shift+F5"),
            }
        if action == "launch":
            game_dir = ald.resolve_game_dir(cfg)
            if not game_dir:
                raise SdkError("找不到游戏目录，请在配置里填 game_dir")
            path = game_dir / exe
            if not path.exists():
                raise SdkError(f"找不到可执行文件：{path}")
            subprocess.Popen([str(path)], cwd=str(game_dir))
            return {"launched": True, "path": str(path)}
        if action in ("toggle", "start", "stop"):
            chosen = key or (cfg.get("stop_key", "Shift+F5") if action == "stop" else cfg.get("run_key", "F5"))
            return ald.send_keys(exe, chosen)
        raise SdkError(f"未知 action：{action}")

    # --------------------------------------------------------------- entries

    @plugin_entry(id="ald_status", name="学车助手状态", description="查看游戏是否在运行、设置文件、基因组数量和最新训练成绩。")
    async def ald_status(self, **_):
        try:
            return Ok(await self._status())
        except Exception as exc:
            return Err(SdkError(f"{type(exc).__name__}: {exc}"))

    @plugin_entry(
        id="ald_settings",
        name="进化参数",
        description="读取/修改进化超参数。action=get|set|preset|recommend；写盘前自动备份，游戏运行时默认拒绝。",
    )
    async def ald_settings(
        self,
        action: Annotated[str, "get 查看，set 修改，preset 套预设，recommend 看建议"] = "get",
        changes: Annotated[dict | None, "要改的键，如 {\"Evolution.MutationRate\": 0.2}"] = None,
        preset: Annotated[str, "explore / balanced / converge"] = "",
        dry_run: Annotated[bool, "只预览不落盘"] = False,
        force: Annotated[bool, "游戏运行时仍强制写（不推荐）"] = False,
        **_,
    ):
        try:
            return Ok(await self._settings_op(action, changes, preset, dry_run, force))
        except SdkError as exc:
            return Err(exc)
        except Exception as exc:
            return Err(SdkError(f"{type(exc).__name__}: {exc}"))

    @plugin_entry(
        id="ald_genomes",
        name="神经网络基因组",
        description="列出/查看/导出训练曲线，或复制一份并扰动权重生成新候选。action=list|inspect|trend|mutate。",
    )
    async def ald_genomes(
        self,
        action: Annotated[str, "list 列表，inspect 详情，trend 训练曲线，mutate 生成变异副本"] = "list",
        name: Annotated[str, "文件名或去掉 .evolution 的名字；留空取最新"] = "",
        limit: Annotated[int, "列表/曲线最多返回多少条"] = 20,
        include_weights: Annotated[bool, "inspect 时是否返回完整权重"] = False,
        strength: Annotated[float, "mutate 的高斯扰动强度（绝对值）"] = 0.1,
        rate: Annotated[float, "每个权重被扰动的概率"] = 0.1,
        copies: Annotated[int, "mutate 生成几份"] = 1,
        **_,
    ):
        try:
            return Ok(await self._genomes_op(action, name, limit, include_weights, strength, rate, copies))
        except SdkError as exc:
            return Err(exc)
        except Exception as exc:
            return Err(SdkError(f"{type(exc).__name__}: {exc}"))

    @plugin_entry(
        id="ald_autopilot",
        name="自动巡航",
        description="start/stop/status。开启后监听游戏退出，读取最新训练曲线并在下一局前自动调参。",
    )
    async def ald_autopilot(self, action: Annotated[str, "start / stop / status"] = "status", **_):
        try:
            return Ok(await self._autopilot_op(action))
        except Exception as exc:
            return Err(SdkError(f"{type(exc).__name__}: {exc}"))

    @plugin_entry(
        id="ald_run",
        name="启动/训练控制",
        description="status/launch/toggle/start/stop。launch 启动游戏；toggle 发 F5；stop 发 Shift+F5。",
    )
    async def ald_run(
        self,
        action: Annotated[str, "status / launch / toggle / start / stop"] = "status",
        key: Annotated[str, "覆盖默认按键，如 F5 / Shift+F5"] = "",
        **_,
    ):
        try:
            return Ok(await self._run_op(action, key))
        except SdkError as exc:
            return Err(exc)
        except Exception as exc:
            return Err(SdkError(f"{type(exc).__name__}: {exc}"))

    # ------------------------------------------------------------- llm tools

    @llm_tool(
        name="ald_status",
        description="查看《AI Learns To Drive》当前训练状态：游戏是否运行、最新基因组、训练代数和最好成绩。",
        parameters={"type": "object", "properties": {}},
    )
    async def tool_status(self, **_):
        try:
            return {"output": await self._status()}
        except Exception as exc:
            return {"output": {"reason": f"{type(exc).__name__}: {exc}"}, "is_error": True, "error": "ALD_STATUS_FAILED"}

    @llm_tool(
        name="ald_tune",
        description="读取或修改《AI Learns To Drive》的进化超参数（变异率、选择压力、奖励、种群大小）。写盘前自动备份。",
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["get", "set", "preset", "recommend"], "description": "get/set/preset/recommend"},
                "changes": {"type": "object", "description": "如 {\"Evolution.MutationRate\": 0.2}"},
                "preset": {"type": "string", "enum": ["explore", "balanced", "converge"]},
                "dry_run": {"type": "boolean", "description": "只预览不落盘"},
            },
        },
    )
    async def tool_tune(self, *, action: str = "get", changes: dict | None = None, preset: str = "", dry_run: bool = False):
        try:
            return {"output": await self._settings_op(action, changes, preset, dry_run, False)}
        except SdkError as exc:
            return {"output": {"reason": str(exc)}, "is_error": True, "error": "ALD_TUNE_REJECTED"}
        except Exception as exc:
            return {"output": {"reason": f"{type(exc).__name__}: {exc}"}, "is_error": True, "error": "ALD_TUNE_FAILED"}

    @llm_tool(
        name="ald_genomes",
        description="查看《AI Learns To Drive》的神经网络基因组与训练曲线；可生成权重扰动副本作为新候选。",
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["list", "inspect", "trend", "mutate"]},
                "name": {"type": "string", "description": "基因组文件名，留空取最新"},
                "limit": {"type": "integer", "description": "最多返回多少条"},
                "strength": {"type": "number", "description": "mutate 扰动强度，默认 0.1"},
                "rate": {"type": "number", "description": "mutate 每个权重被扰动的概率，默认 0.1"},
                "copies": {"type": "integer", "description": "mutate 生成几份"},
            },
        },
    )
    async def tool_genomes(
        self,
        *,
        action: str = "list",
        name: str = "",
        limit: int = 20,
        strength: float = 0.1,
        rate: float = 0.1,
        copies: int = 1,
    ):
        try:
            result = await self._genomes_op(action, name, limit, False, strength, rate, copies)
            return {"output": result}
        except SdkError as exc:
            return {"output": {"reason": str(exc)}, "is_error": True, "error": "ALD_GENOME_REJECTED"}
        except Exception as exc:
            return {"output": {"reason": f"{type(exc).__name__}: {exc}"}, "is_error": True, "error": "ALD_GENOME_FAILED"}

    @llm_tool(
        name="ald_run",
        description="启动/控制《AI Learns To Drive》的训练：launch 启动游戏，toggle 发 F5，stop 发 Shift+F5。",
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["status", "launch", "toggle", "start", "stop"]},
                "key": {"type": "string", "description": "覆盖按键，如 F5"},
            },
        },
    )
    async def tool_run(self, *, action: str = "status", key: str = ""):
        try:
            return {"output": await self._run_op(action, key)}
        except SdkError as exc:
            return {"output": {"reason": str(exc)}, "is_error": True, "error": "ALD_RUN_REJECTED"}
        except Exception as exc:
            return {"output": {"reason": f"{type(exc).__name__}: {exc}"}, "is_error": True, "error": "ALD_RUN_FAILED"}

    # ------------------------------------------------------------- lifecycle

    @lifecycle(id="startup")
    async def on_startup(self, **_):
        cfg = await self._cfg()
        try:
            self._install_result = await asyncio.to_thread(ald_install.install, cfg)
        except Exception as exc:
            self._install_result = {"ok": False, "stage": "error", "reason": f"{type(exc).__name__}: {exc}"}
        if bool(cfg.get("autopilot_enabled", False)):
            await self._autopilot_op("start")
        return Ok({"status": "ready", "autopilot": self._autopilot_status(cfg), "install": self._install_result})

    @lifecycle(id="shutdown")
    async def on_shutdown(self, **_):
        if self._task and not self._task.done():
            self._stop.set()
            try:
                await asyncio.wait_for(self._task, timeout=5)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass
            self._task = None
        return Ok({"status": "stopped"})
