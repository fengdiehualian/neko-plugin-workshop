"""本机 Agent 桥接 —— N.E.K.O 原生插件。

把本机的编码 Agent CLI 包装成 N.E.K.O 可以调用的东西，分两层：

* ``@plugin_entry`` —— **运行时入口**。出现在插件管理器的「入口点」里，可以手动触发，
  也参与独立用户插件 Agent 的路由。长任务走这一层。
* ``@llm_tool`` —— **对话期工具**。注册到 main_server 的 ``/api/tools``，猫娘在对话中
  可以直接调用。这一层有硬约束：``timeout`` 最大 300 秒，所以它只适合
  「派活 + 拿结果」这种中等长度任务，别指望它跑十分钟。

两条路径共用 ``.bridge`` 子包里的注册表与执行层（那是 ``neko_agent_bridge`` MCP 服务端
同一份代码的同步副本，见 ``plugin_src/sync_bridge.py``）。

宿主是 N.E.K.O 自带的嵌入式 Python，所以这里**只依赖标准库**。
"""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Annotated, Any

from plugin.sdk.plugin import (
    Err,
    NekoPluginBase,
    Ok,
    SdkError,
    lifecycle,
    llm_tool,
    neko_plugin,
    plugin_entry,
    ui,
)

from .bridge.config import BridgeConfig, config_from_dict
from .bridge.registry import (
    AGENT_SPECS,
    AgentSpec,
    ResolvedCommand,
    canonical_id,
    list_specs,
    resolve_agent,
)
from .bridge.runner import probe_version, run_agent
from .bridge.tools import format_agent_result, write_audit

# 宿主对入口有看门狗（默认很短）。Agent 任务动辄几分钟，所以长任务入口必须显式声明超时。
_ENTRY_TIMEOUT = 1800.0
# @llm_tool 的硬上限是 300 秒，超了会被协议拒绝。
_LLM_TOOL_TIMEOUT = 300.0

_AGENT_IDS = [spec.id for spec in list_specs()]
_AGENT_HINT = "、".join(_AGENT_IDS)


def _json_dumps(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False)


@neko_plugin
class AgentBridgePlugin(NekoPluginBase):
    """把本机 Agent CLI 桥接成 N.E.K.O 入口与对话工具。"""
    @ui.context(id="agent_bridge_panel")
    async def _ui_panel_state(self):
        """Hosted 面板状态：插件元信息 + 入口清单。

        入口清单**从 SDK 真元数据生成**（``self.list_entries()`` 读的是各入口的
        ``EventMeta``），不再手抄一份。手抄那份已经漂移过：``agent_run`` 的
        description 是空串（真入口有一长串说明，告诉模型有哪些 Agent 可选），
        而 ``has_required`` 一律写 True —— ``agents_list`` / ``agent_doctor``
        其实任何参数都不必填，面板因此把它们渲染成带必填星号的表单。
        """
        return {
            'plugin': {
                'id': 'agent_bridge',
                'name': 'Agent 桥接',
                'version': '0.1.0',
                'description': '把本机编码 Agent CLI 桥接成 N.E.K.O 运行时入口(生成面板:入口清单)。',
            },
            'entries': self._entry_rows(),
        }

    def _entry_rows(self) -> list[dict[str, Any]]:
        """把 SDK 的入口元数据整理成面板要用的字段。

        ``timeout`` 是面板判断「慢入口」的依据（见 ui/panel.tsx 的 SLOW_MS）：
        以前这里从不返回它，于是 TSX 里 ``const SLOW = {}`` 永远是空的，慢入口
        分支（带 timeoutMs 的 api.call）根本走不到 —— 而 agent_run 声明的可是
        1800 秒。
        """
        rows: list[dict[str, Any]] = []
        try:
            entries = self.list_entries()
        except Exception as exc:  # noqa: BLE001 — 面板状态拿不到不该让面板炸掉
            self._log("debug", f"读取入口元数据失败：{exc!r}")
            return rows

        for entry in entries:
            if not isinstance(entry, dict):
                continue
            # 只保留「面板上能按的运行时入口」：
            #   - lifecycle（startup/shutdown）不是给用户点的按钮；
            #   - dynamic 的 __llm_tool__* 是对话期工具，没有对应的 @ui.action，
            #     渲染出来只会得到一张「动作未注册」的卡片。
            if entry.get("event_type") != "plugin_entry" or entry.get("dynamic"):
                continue
            schema = entry.get("input_schema")
            schema = schema if isinstance(schema, dict) else {}
            properties = schema.get("properties")
            properties = properties if isinstance(properties, dict) else {}
            required = schema.get("required")
            required = [str(x) for x in required] if isinstance(required, (list, tuple)) else []
            timeout = entry.get("timeout")
            rows.append(
                {
                    "id": str(entry.get("id", "")),
                    "name": str(entry.get("name") or entry.get("id") or ""),
                    "description": str(entry.get("description") or ""),
                    # has_params 决定面板是否渲染参数表单；has_required 决定星号。
                    "has_params": bool(properties),
                    "has_required": bool(required),
                    "timeout": float(timeout) if isinstance(timeout, (int, float)) else 0.0,
                }
            )
        return rows


    def __init__(self, ctx):
        # 必须最先调用：基类 __init__ 末尾会自动注册本类上被 @llm_tool 标记的方法。
        super().__init__(ctx)
        self._bridge_config: BridgeConfig | None = None
        self._config_lock = asyncio.Lock()
        self._parallel_sem: asyncio.Semaphore | None = None
        # 曾经回「需要登录」的 Agent。CLI 未登录时退出码是 0，光看退出码永远发现不了，
        # 于是记录下来：后续直接拦下，别再让模型一次次挑中同一台没登录的机器。
        # 跑成功一次就自动摘掉（用户可能刚登录完）。
        self._needs_login: set[str] = set()

    # ------------------------------------------------------------------ 基础

    def _logger(self):
        logger = getattr(self, "logger", None)
        if logger is None:
            logger = getattr(getattr(self, "ctx", None), "logger", None)
        return logger

    def _log(self, level: str, message: str) -> None:
        logger = self._logger()
        if logger is None:
            return
        try:
            getattr(logger, level, logger.info)(message)
        except Exception:  # noqa: BLE001 — 日志永远不该影响主流程
            pass

    def _config_source_path(self) -> str:
        """插件基础配置（plugin.toml）的真实路径。

        **不要用 ``getattr(self.config, "path", "")``** —— SDK 的 ``PluginConfig``
        （``plugin/sdk/shared/core/config.py``）根本没有 ``path`` 属性，那行代码永远
        拿到空串，于是诊断里永远显示「（内置默认）」，哪怕配置确实是读来的。
        真正的来源在 ``ctx.config_path``：SDK 自己解析 ``plugin_dir`` / i18n 时用的
        就是它（``base.py`` 的 ``_load_plugin_i18n``）。再退到 ``metadata``，
        最后退回 ``plugin_dir / plugin.toml`` 这个 SDK 的默认位置。
        """

        def _from_ctx() -> object:
            return getattr(getattr(self, "ctx", None), "config_path", None)

        def _from_metadata() -> object:
            return self.metadata.get("config_path")

        def _from_plugin_dir() -> object:
            return self.config_dir / "plugin.toml"

        for getter in (_from_ctx, _from_metadata, _from_plugin_dir):
            try:
                candidate = getter()
            except Exception as exc:  # noqa: BLE001 — 逐个来源降级，不该因此起不来
                self._log("debug", f"读取配置路径来源失败：{exc!r}")
                continue
            if candidate:
                return str(candidate)
        return ""

    def _runtime_config_path(self) -> str:
        """运行时覆盖配置的路径（Profile 写入这里），拿不到就返回空串。"""
        try:
            return str(self.runtime_config_path)
        except Exception as exc:  # noqa: BLE001 — 只是诊断信息，缺了不影响主流程
            self._log("debug", f"读取运行时配置路径失败：{exc!r}")
            return ""

    async def _config(self) -> BridgeConfig:
        """读插件配置并缓存。读失败就退回默认值 —— 桥接不该因为配置写坏而起不来。"""
        if self._bridge_config is not None:
            return self._bridge_config
        async with self._config_lock:
            if self._bridge_config is not None:
                return self._bridge_config

            raw: dict[str, Any] = {}
            source = self._config_source_path()
            try:
                dumped = await self.config.dump()
                if isinstance(dumped, dict):
                    section = dumped.get("bridge")
                    if isinstance(section, dict):
                        raw = section
            except Exception as exc:  # noqa: BLE001
                self._log("warning", f"读取插件配置失败，使用默认值：{exc!r}")

            config = config_from_dict(raw, source)
            if not config.audit_log:
                try:
                    config.audit_log = str(self.data_path("dispatch.jsonl"))
                except Exception:  # noqa: BLE001
                    pass

            self._bridge_config = config
            return config

    def _audit(self, config: BridgeConfig, record: dict[str, Any]) -> None:
        try:
            write_audit(config, record)
        except Exception as exc:  # noqa: BLE001
            self._log("debug", f"审计写入失败：{exc!r}")

    def _semaphore(self, config: BridgeConfig) -> asyncio.Semaphore:
        if self._parallel_sem is None:
            self._parallel_sem = asyncio.Semaphore(max(1, int(config.max_parallel)))
        return self._parallel_sem

    def _ready_agents(self, config: BridgeConfig) -> list[str]:
        """本机真正能跑的 Agent：配置启用 + 可执行文件在 + 没被「需要登录」拉黑。"""
        ready: list[str] = []
        for spec in list_specs():
            override = config.override_for(spec.id)
            if not override.enabled or spec.id in self._needs_login:
                continue
            if resolve_agent(spec, override.exe_override).exists:
                ready.append(spec.id)
        return ready

    def _agent_hint(self, config: BridgeConfig) -> str:
        ready = self._ready_agents(config)
        return f"当前可用：{'、'.join(ready) or '（无）'}"

    # ---------------------------------------------------------------- 生命周期

    @lifecycle(id="startup")
    async def on_startup(self, **_):
        config = await self._config()
        ready = 0
        for spec in list_specs():
            override = config.override_for(spec.id)
            if override.enabled and resolve_agent(spec, override.exe_override).exists:
                ready += 1
        self._log(
            "info",
            f"本机 Agent 桥接就绪：{ready}/{len(_AGENT_IDS)} 个 Agent 可用"
            f"（默认超时 {config.default_timeout_seconds}s，审计 {config.audit_log or '关闭'}）",
        )
        return Ok({"ready": ready, "total": len(_AGENT_IDS)})

    @lifecycle(id="shutdown")
    async def on_shutdown(self, **_):
        self._log("info", "本机 Agent 桥接停止")
        return Ok({"status": "stopped"})

    # ------------------------------------------------------------ 运行时入口

    @ui.action(label="列出可用 Agent", refresh_context=True)

    @plugin_entry(
        id="agents_list",
        name="列出可用 Agent",
        description="列出本机登记的编码 Agent CLI，含解析到的可执行文件、来源与就绪状态。",
        timeout=_ENTRY_TIMEOUT,
        llm_result_fields=["summary", "agents"],
    )
    async def agents_list(
        self,
        probe_versions: Annotated[bool, "是否逐个探测版本号（较慢，每个约 1-3 秒）"] = False,
    **_):
        config = await self._config()
        rows: list[dict[str, Any]] = []
        pending_specs: list[tuple[AgentSpec, ResolvedCommand, dict[str, Any]]] = []
        for spec in list_specs():
            override = config.override_for(spec.id)
            resolved = resolve_agent(spec, override.exe_override)
            blocked = spec.id in self._needs_login
            row: dict[str, Any] = {
                "agent": spec.id,
                "label": spec.label,
                "enabled": override.enabled,
                "ready": bool(resolved.exists and override.enabled and not blocked),
                "needs_login": blocked,
                "command": resolved.describe(),
                "source": resolved.source,
                "version": "",
            }
            rows.append(row)
            if probe_versions and resolved.exists and override.enabled:
                pending_specs.append((spec, resolved, row))

        if pending_specs:
            # 版本探测必须**并发**：每个 Agent 最坏 45 秒（probe_version 的硬超时），
            # 9 个串行就是 405 秒。宿主对入口有看门狗，串行会把入口跑超时。
            # 并发后最坏仍在 45 秒量级，稳稳落在上面声明的 timeout 之内。
            results = await asyncio.gather(
                *(
                    probe_version(spec, resolved, config=config)
                    for spec, resolved, _row in pending_specs
                ),
                return_exceptions=True,
            )
            for (_spec, _resolved, row), result in zip(pending_specs, results, strict=True):
                # 探测失败不该让整个列表失败 —— 版本号只是附加信息
                row["version"] = "" if isinstance(result, BaseException) else str(result)

        ready = [row["agent"] for row in rows if row["ready"]]
        summary = f"共 {len(rows)} 个登记 Agent，{len(ready)} 个已就绪：{'、'.join(ready) or '（无）'}"
        return Ok({"summary": summary, "agents": rows, "total": len(rows), "ready": len(ready)})

    @ui.action(label="派发任务给 Agent", refresh_context=True)

    @plugin_entry(
        id="agent_run",
        name="派发任务给 Agent",
        description=(
            "把一条任务交给指定的本机编码 Agent CLI 执行，返回它的输出。"
            f"可选 Agent：{_AGENT_HINT}。"
        ),
        timeout=_ENTRY_TIMEOUT,
        llm_result_fields=["summary", "output", "exit_code", "timed_out"],
    )
    async def agent_run(
        self,
        agent: Annotated[str, f"Agent 标识，取值之一：{_AGENT_HINT}"],
        task: Annotated[str, "要交给该 Agent 执行的自然语言任务描述"],
        cwd: Annotated[str, "可选：工作目录绝对路径，留空用配置里的默认值"] = "",
        timeout_seconds: Annotated[int, "可选：超时秒数，0 表示用配置默认值"] = 0,
        model: Annotated[str, "可选：指定模型（部分 CLI 支持）"] = "",
    **_):
        outcome = await self._dispatch(agent, task, cwd=cwd, timeout_seconds=timeout_seconds, model=model)
        if isinstance(outcome, str):  # 校验失败的说明
            return Err(SdkError(outcome))
        return Ok(outcome)

    @ui.action(label="并行派发多个任务", refresh_context=True)

    @plugin_entry(
        id="agent_run_parallel",
        name="并行派发多个任务",
        description=(
            "同时把多条任务派发给多个 Agent。tasks_json 是一个 JSON 数组，"
            '每项形如 {"agent": "dsh", "task": "跑一下单元测试"}。'
            "是否真的并行由配置 allow_parallel / max_parallel 决定。"
        ),
        timeout=_ENTRY_TIMEOUT,
        llm_result_fields=["summary", "results"],
    )
    async def agent_run_parallel(
        self,
        tasks_json: Annotated[str, 'JSON 数组，例如 [{"agent":"dsh","task":"列出仓库结构"}]'],
    **_):
        config = await self._config()
        try:
            parsed = json.loads(tasks_json or "[]")
        except json.JSONDecodeError as exc:
            return Err(SdkError(f"tasks_json 不是合法 JSON：{exc}"))
        if not isinstance(parsed, list) or not parsed:
            return Err(SdkError("tasks_json 必须是非空 JSON 数组"))
        if not config.allow_parallel and len(parsed) > 1:
            return Err(SdkError("配置里 allow_parallel = false，无法并行派发"))

        async def one(item: Any) -> dict[str, Any]:
            if not isinstance(item, dict):
                return {"agent": "?", "ok": False, "error": "每一项都必须是对象"}
            agent = str(item.get("agent") or "")
            task = str(item.get("task") or "")
            timeout = item.get("timeout_seconds") or 0
            try:
                timeout = int(timeout)
            except (TypeError, ValueError):
                timeout = 0
            outcome = await self._dispatch(
                agent,
                task,
                cwd=str(item.get("cwd") or ""),
                timeout_seconds=timeout,
                model=str(item.get("model") or ""),
                semaphore=self._semaphore(config),
            )
            if isinstance(outcome, str):
                return {"agent": agent, "ok": False, "error": outcome}
            return {"agent": outcome.get("agent", agent), **outcome}

        results = await asyncio.gather(*(one(item) for item in parsed))
        succeeded = sum(1 for row in results if row.get("ok"))
        summary = f"并行派发 {len(results)} 条，成功 {succeeded} 条"
        return Ok({"summary": summary, "results": results, "total": len(results), "succeeded": succeeded})

    @ui.action(label="诊断桥接链路", refresh_context=True)

    @plugin_entry(
        id="agent_doctor",
        name="诊断桥接链路",
        description="检查宿主平台、解释器版本、配置来源，以及每个 Agent 的解析与版本情况。",
        timeout=_ENTRY_TIMEOUT,  # 默认 probe_versions=True 会逐个探测全部 CLI，30 秒看门狗不够
        llm_result_fields=["summary", "agents"],
    )
    async def agent_doctor(
        self,
        agent: Annotated[str, "可选：只诊断某一个 Agent 标识"] = "",
        probe_versions: Annotated[bool, "是否探测各 CLI 版本"] = True,
    **_):
        config = await self._config()
        only = canonical_id(agent.strip()) if agent.strip() else None
        if agent.strip() and not only:
            return Err(SdkError(f"未知的 agent：{agent!r}。可选：{_AGENT_HINT}"))

        specs = [AGENT_SPECS[only]] if only else list_specs()
        lines = [f"平台 {sys.platform} · Python {sys.version.split()[0]}"]
        # 配置来源必须报真路径：SDK 的 PluginConfig 没有 .path，以前那行 getattr
        # 永远是空串，于是不管配置读没读进来都显示「（内置默认）」。
        source_path = config.source_path or self._config_source_path()
        lines.append(f"配置来源：{source_path or '（未知：宿主未暴露 config_path）'}")
        runtime_path = self._runtime_config_path()
        if runtime_path and runtime_path != source_path:
            lines.append(f"运行时覆盖：{runtime_path}")
        lines.append(f"审计日志：{config.audit_log or '（关闭）'}")

        # 先并行探测版本号，再拼报告 —— 串行探测 9 个 Agent 最坏 405 秒，
        # 会把入口跑过宿主看门狗（同 agents_list 的处理）。
        probes: dict[str, str] = {}
        probe_jobs = []
        for spec in specs:
            override = config.override_for(spec.id)
            resolved = resolve_agent(spec, override.exe_override)
            if probe_versions and resolved.exists and override.enabled:
                probe_jobs.append((spec, resolved))
        if probe_jobs:
            results = await asyncio.gather(
                *(probe_version(spec, resolved, config=config) for spec, resolved in probe_jobs),
                return_exceptions=True,
            )
            for (spec, _resolved), result in zip(probe_jobs, results, strict=True):
                probes[spec.id] = "" if isinstance(result, BaseException) else str(result)

        rows: list[dict[str, Any]] = []
        for spec in specs:
            override = config.override_for(spec.id)
            resolved = resolve_agent(spec, override.exe_override)
            version = probes.get(spec.id, "")
            if not override.enabled:
                mark = "--"
            elif not resolved.exists or version.startswith("<"):
                mark = "!!"
            else:
                mark = "OK"
            lines.append(f"{mark} {spec.id}  {resolved.describe()}")
            rows.append(
                {
                    "agent": spec.id,
                    "mark": mark,
                    "command": resolved.describe(),
                    "source": resolved.source,
                    "enabled": override.enabled,
                    "exists": resolved.exists,
                    "version": version,
                }
            )

        healthy = sum(1 for row in rows if row["mark"] == "OK")
        summary = f"诊断完成：{healthy}/{len(rows)} 个 Agent 标记为 OK"
        return Ok({"summary": summary, "report": "\n".join(lines), "agents": rows, "healthy": healthy})

    # ------------------------------------------------------------ 对话期工具

    @llm_tool(
        name="agent_list",
        description=(
            "列出本机可用的编码 Agent CLI（CodeBuddy / Claude Code / dsh / omp / EvoX / "
            "OpenCode / OpenClaw / AtomCode / QwenPaw）及其就绪状态。"
            "当用户问「你本机有哪些编码 Agent 能用」时调用。"
        ),
        parameters={"type": "object", "properties": {}, "required": []},
        timeout=60.0,
    )
    async def agent_list_tool(self):
        # 成功路径直接返回业务 payload，**不要**自己再包一层 ``output``。
        # 宿主的 llm_tools 路由（plugin/server/routes/llm_tools.py）约定：
        #   - 返回值是普通 dict（只有 output 也算普通数据）→ 包成
        #     {"output": <返回值>, "is_error": false}
        #   - 返回值里带 is_error，或同时带 output+images → 视为信封，原样规范化
        # 所以自己包一层会让模型看到两层 output；只有「显式报错」才需要写信封。
        try:
            config = await self._config()
            rows = []
            for spec in list_specs():
                override = config.override_for(spec.id)
                resolved = resolve_agent(spec, override.exe_override)
                blocked = spec.id in self._needs_login
                rows.append(
                    {
                        "agent": spec.id,
                        "label": spec.label,
                        # 「文件在」不等于「能用」：没登录的 CLI 一样能启动、一样退出码 0。
                        "ready": bool(resolved.exists and override.enabled and not blocked),
                        "needs_login": blocked,
                        "command": resolved.describe(),
                    }
                )
            ready = [row["agent"] for row in rows if row["ready"]]
            blocked_ids = [row["agent"] for row in rows if row["needs_login"]]
            return {
                "ready_agents": ready,
                "needs_login_agents": blocked_ids,
                "all_agents": rows,
                "note": (
                    f"共 {len(rows)} 个，{len(ready)} 个就绪"
                    + (f"；未登录已跳过：{'、'.join(blocked_ids)}" if blocked_ids else "")
                ),
            }
        except Exception as exc:  # noqa: BLE001
            return {"output": {"reason": f"列举失败：{exc}"}, "is_error": True, "error": "AGENT_LIST_FAILED"}

    @llm_tool(
        name="agent_run",
        description=(
            "把一条编码任务派发给本机某个 Agent CLI 执行并返回输出，适合写代码、改文件、"
            "分析仓库、跑测试等。任务描述要自包含。注意单次调用最多 300 秒。"
            "派发前先调 agent_list 拿 ready_agents（没登录的 CLI 会被自动跳过）；"
            "若返回 needs_login=true，说明那台 CLI 未登录、任务没执行，换 ready_agents 里的重试。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "agent": {
                    "type": "string",
                    "description": f"Agent 标识，先用 agent_list 查 ready_agents，取值之一：{_AGENT_HINT}",
                },
                "task": {"type": "string", "description": "要交给该 Agent 执行的自然语言任务描述"},
                "cwd": {"type": "string", "description": "可选：工作目录绝对路径"},
                "timeout_seconds": {"type": "integer", "description": "可选：超时秒数，上限 300"},
            },
            "required": ["agent", "task"],
        },
        timeout=_LLM_TOOL_TIMEOUT,
    )
    async def agent_run_tool(
        self,
        *,
        agent: str,
        task: str,
        cwd: str = "",
        timeout_seconds: int = 0,
    ):
        try:
            requested = int(timeout_seconds or 0)
        except (TypeError, ValueError):
            requested = 0
        # 对话期工具被协议限制在 300 秒内，这里再夹一次，避免用户填了更大的值却被硬切。
        requested = min(requested, int(_LLM_TOOL_TIMEOUT)) if requested > 0 else int(_LLM_TOOL_TIMEOUT)

        outcome = await self._dispatch(agent, task, cwd=cwd, timeout_seconds=requested, model="")
        if isinstance(outcome, str):
            return {"output": {"reason": outcome}, "is_error": True, "error": "AGENT_RUN_REJECTED"}
        if not outcome.get("ok"):
            return {
                "output": outcome,
                "is_error": True,
                "error": "AGENT_RUN_FAILED",
            }
        # 成功时直接返回 payload：宿主路由会包成 {"output": outcome, "is_error": false}。
        return outcome

    # ------------------------------------------------------------------ 内部

    async def _dispatch(
        self,
        agent: str,
        task: str,
        *,
        cwd: str = "",
        timeout_seconds: int = 0,
        model: str = "",
        semaphore: asyncio.Semaphore | None = None,
    ) -> dict[str, Any] | str:
        """派发一条任务。返回结果字典，或在参数/环境不合法时返回一句错误说明。"""
        config = await self._config()

        spec_id = canonical_id(str(agent or "").strip())
        if not spec_id:
            return f"未知的 agent：{agent!r}。可选：{_AGENT_HINT}"
        spec = AGENT_SPECS[spec_id]
        override = config.override_for(spec.id)
        if not override.enabled:
            return f"agent {spec.id} 已在配置里禁用"

        resolved = resolve_agent(spec, override.exe_override)
        if not resolved.exists:
            return f"未找到 {spec.id} 的可执行文件：{resolved.describe()}（可用 enabled/exe_override 调整）"

        # 已经确认过没登录的，直接拦：再跑一次只是白等一个进程 + 拿回同一句登录提示。
        # 拉黑名单只能靠「重新加载插件」清掉（拦住之后就没机会跑到成功分支），所以
        # 提示里必须写清楚这一点，别让用户以为登录完重试一次就能过。
        if spec.id in self._needs_login:
            return (
                f"agent {spec.id} 上次调用返回「需要登录」，已跳过（登录完请重新加载本插件）。"
                f"请改用：{self._agent_hint(config)}"
            )

        task_text = str(task or "").strip()
        if not task_text:
            return "task 不能为空"

        seconds = config.clamp_timeout(
            int(timeout_seconds or 0) or override.timeout_seconds or config.default_timeout_seconds
        )
        workdir = config.resolve_cwd(cwd or None)

        async def invoke():
            return await run_agent(
                spec,
                resolved,
                task_text,
                config=config,
                cwd=workdir,
                model=model or None,
                timeout=seconds,
                extra_args=override.extra_args,
            )

        if semaphore is None:
            result = await invoke()
        else:
            async with semaphore:
                result = await invoke()

        text = format_agent_result(result)
        summary = text.splitlines()[0] if text else f"[agent_run] {spec.id}"
        self._audit(
            config,
            {
                "agent": spec.id,
                "exit_code": result.exit_code,
                "timed_out": result.timed_out,
                "needs_login": result.needs_login,
                "duration_ms": result.duration_ms,
                "cwd": workdir or "",
                "task": task_text[:300],
            },
        )

        if result.needs_login:
            self._needs_login.add(spec.id)
        elif result.ok:
            self._needs_login.discard(spec.id)  # 用户可能刚登录完，别永久拉黑

        return {
            "ok": bool(result.ok),
            "agent": spec.id,
            "label": result.label,
            "summary": summary,
            "output": text,
            "exit_code": result.exit_code,
            "timed_out": result.timed_out,
            "needs_login": bool(result.needs_login),
            "output_truncated": result.output_truncated,
            "duration_seconds": round(result.duration_ms / 1000, 1),
            "command": result.command_preview,
            "error": result.error,
            "retry_with": self._ready_agents(config) if result.needs_login else [],
        }
