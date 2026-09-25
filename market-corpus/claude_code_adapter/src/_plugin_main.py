"""Claude Code Adapter 插件主类。

从 __init__.py 拆分出来，保证 entry 类位于模块顶层，
满足 neko-plugin release check 的 AST 静态校验要求。

v0.6.0 全异步化：不再有同步执行入口（同步会被网关 ~60s
ReadTimeout 卡死，且 Claude Code 常执行半小时以上的长任务）。

工具集：
- claude_code_submit: 提交后台异步任务（无超时限制），立即返回 task_id + session_id
  （新会话短暂等待 CLI init 捕获 UUID，resume 会话立即返回）
- claude_code_poll / claude_code_wait / claude_code_cancel: 状态查询/提前盯/取消
- claude_code_followup: 任务执行中/完成后在同一会话追加补充或纠正指令
  （自动中断跑偏的任务，保留上下文续发）
- claude_code_list_tasks: 列出活跃会话/任务（与 CC 进程一一对应，最多 2 个）
- claude_code_clear_session: 清理会话列表（取消活跃任务+杀 CC 进程，支持定向/全清）
- claude_code_set_poll_interval: 调整轮询安全网间隔（默认 5 分钟，可改默认/单任务）
- claude_code_check_health / claude_code_list_sessions /
  claude_code_get_config: 辅助工具
- claude_code_list_providers / claude_code_switch_provider /
  claude_code_remove_provider: cc-switch 风格的多供应商切换

任务完成主动推送：任务到达终态时插件经 push_message 把结果注入
对话频道（ai_behavior="respond"），猫娘自动收到并组织汇报，
无需一直轮询；想提前盯时可用 claude_code_wait/poll（含实时进度）。
每次完成推送都带会话 UUID 与轮询时间（poll_interval_sec）。

会话控制（v0.4.0）：
submit 支持 session_mode（auto/new/resume）+ session_id，
让猫娘能明确指定续跑同一会话或强制新开会话。
session_id 就是 Claude Code 原生会话 UUID（每个 Claude Code 会话唯一）：
- resume 模式支持裸 UUID、唯一前缀、'claude --resume <uuid>' 命令形态，
  并自动从 ~/.claude/projects 存档解析会话归属目录（Claude CLI 按 cwd
  索引会话，目录不对 --resume 必然失败），可随时回到任意历史会话。

v0.7.0 会话/轮询机制：UUID 为会话唯一标识；开新会话立即拿到 UUID（必与现有
UUID 绝对不同，作为新/旧会话唯一判断标准）；活跃列表与 CC 进程一一对应、最多
2 个；重启后列表清空不意外续跑；内置轮询安全网（默认 5 分钟可调）确保结果稳定送达。
"""

from __future__ import annotations

import os
from typing import Any, Optional

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

from . import claude_sessions
from .executor import (
    ClaudeCLIExecutor,
    detect_claude_cli,
)
from .models import AdapterConfig
from .provider_manager import Provider, ProviderManager
from .session import SessionManager, compute_prompt_signature
from .task_manager import TaskManager, TaskRecord, TaskStatus


@neko_plugin
class ClaudeCodeAdapterPlugin(NekoPluginBase):
    """Claude Code 适配器插件。

    通过 @llm_tool 装饰器将 Claude Code CLI 的能力暴露给猫娘 LLM。
    猫娘可以在对话中调用这些工具，让 Claude Code 执行具体的编码任务。
    """

    def __init__(self, ctx) -> None:
        super().__init__(ctx)
        # 文件日志器（与 web_search 插件一致的模式）
        self.file_logger = self.enable_file_logging(log_level="INFO")
        self.logger = self.file_logger

        # 运行时状态
        self._config: AdapterConfig = AdapterConfig()
        self._executor: Optional[ClaudeCLIExecutor] = None
        self._session_mgr: Optional[SessionManager] = None
        self._task_mgr: Optional[TaskManager] = None
        self._provider_mgr: Optional[ProviderManager] = None
        self._ready: bool = False

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    @lifecycle(id="startup")
    async def startup(self, **_) -> Any:
        """启动：加载配置、初始化执行器和会话管理器。"""
        try:
            # 1. 加载 plugin.toml 的 [claude_code] 节
            cfg_dict = await self._load_config_section("claude_code")
            self._config = AdapterConfig.from_config_dict(cfg_dict)

            # 2. 确保 PluginStore 已启用（基类在 __init__ 中已创建 self.store，
            #    但需要根据 plugin.toml 的 [plugin.store] enabled 决定是否可用）
            if not self.store.enabled:
                self.store.enabled = True
                self.logger.info("PluginStore auto-enabled for session persistence")

            # 3. 初始化会话管理器（使用基类的 self.store）
            self._session_mgr = SessionManager(self.store, logger=self.logger)
            await self._session_mgr.load()
            # 重启/重载后会话列表必须为空：清除跨重启持久化的会话索引，
            # 避免 auto 模式在用户不知情下静默续跑旧 Claude Code 会话。
            # 历史会话仍可通过 session_mode='resume' + session_id=<UUID> 显式回到
            # （直接读取 ~/.claude/projects 原生存档，不依赖此持久化索引）。
            cleared = await self._session_mgr.clear()
            if cleared:
                self.logger.info(
                    "Cleared {} persisted session(s) on startup (no surprise resume)",
                    cleared,
                )

            # 4. 初始化执行器
            self._executor = ClaudeCLIExecutor(self._config, logger=self.logger)

            # 5. 初始化供应商管理器（cc-switch 风格多供应商切换）
            plugin_dir = os.path.dirname(os.path.abspath(__file__))
            self._provider_mgr = ProviderManager(plugin_dir, logger=self.logger)
            self._provider_mgr.load()

            # 6. 初始化任务管理器（全异步：无超时限制，完成后主动推送结果）
            self._task_mgr = TaskManager(
                self._executor,
                self._config,
                logger=self.logger,
                env_provider=self._active_provider_env,
                on_complete=self._push_task_result,
            )
            await self._task_mgr.start()
            self.logger.info("TaskManager initialized and started")

            # 7. 检测 Claude CLI 是否可用
            cli_path = self._config.command or detect_claude_cli()
            cli_available = bool(cli_path)

            self._ready = True
            self.logger.info(
                "ClaudeCodeAdapter started: cli_available={} model={!r} cwd={!r} provider={!r}",
                cli_available,
                self._config.model,
                self._config.cwd or os.getcwd(),
                self._provider_mgr.get_active_name() or "(default)",
            )

            return Ok(
                {
                    "status": "ready",
                    "cli_available": cli_available,
                    "cli_path": cli_path or "",
                    "model": self._config.model,
                    "active_provider": self._provider_mgr.get_active_name(),
                    "sessions_loaded": len(self._session_mgr._sessions)
                    if self._session_mgr
                    else 0,
                }
            )
        except Exception as e:
            self.logger.exception("ClaudeCodeAdapter startup failed")
            return Err(SdkError(f"startup failed: {e}"))

    @lifecycle(id="shutdown")
    async def shutdown(self, **_) -> Any:
        """关闭：释放资源。"""
        self._ready = False
        if self._task_mgr:
            await self._task_mgr.stop()
            self.logger.info("TaskManager stopped")
        self.logger.info("ClaudeCodeAdapter shutdown")
        return Ok({"status": "shutdown"})

    # ------------------------------------------------------------------
    # 配置加载辅助
    # ------------------------------------------------------------------

    async def _load_config_section(self, section: str) -> dict[str, Any]:
        """从 plugin.toml 加载指定节。

        SDK 的 self.config.dump() 返回整个 plugin.toml 的字典。
        """
        try:
            cfg = await self.config.dump(timeout=5.0)
            if isinstance(cfg, dict):
                section_data = cfg.get(section)
                if isinstance(section_data, dict):
                    return section_data
        except Exception as e:
            self.logger.warning("Failed to load config section {}: {}", section, e)
        return {}

    def _ensure_ready(self) -> Optional[Any]:
        """检查插件是否就绪。返回 None 表示就绪，否则返回 Err。"""
        if not self._ready:
            return Err(
                SdkError(
                    "Claude Code Adapter not ready (startup not completed or failed)"
                )
            )
        if self._executor is None or self._session_mgr is None:
            return Err(SdkError("Claude Code Adapter internal state invalid"))
        return None

    def _active_provider_env(self) -> dict[str, str]:
        """返回激活 provider 的环境变量覆盖（供执行链路注入）。"""
        if self._provider_mgr is None:
            return {}
        try:
            return self._provider_mgr.env_overrides()
        except Exception:
            return {}

    async def _resolve_resume_id(
        self,
        *,
        session_mode: str,
        session_id: str,
        cwd: str,
        signature: str,
    ) -> tuple[str, str, str, str]:
        """根据会话模式解析要续跑的会话。

        Returns:
            (resume_id, resolved_cwd, session_title, error_message)。
            error_message 非空时调用方应直接返回。

        - auto: 自动查找可恢复会话（cwd + 提示包签名匹配）
        - new:  强制新开会话（resume_id = ""）
        - resume: 按 Claude Code 原生会话 UUID 强制续跑：
          * session_id 支持裸 UUID、``claude --resume <uuid>`` 命令形态、
            唯一 UUID 前缀（面板复制出的截断形态）
          * 自动从 ``~/.claude/projects`` 存档解析会话归属的项目目录。
            Claude CLI 按工作目录索引会话，cwd 不匹配时 --resume 会失败，
            因此调用方不传 cwd 时自动采用会话归属目录。
        """
        assert self._session_mgr is not None
        mode = (session_mode or "auto").strip().lower() or "auto"

        if mode not in ("auto", "new", "resume"):
            return (
                "",
                "",
                "",
                (
                    f"无效的 session_mode: {session_mode!r}，"
                    "只支持 'auto' / 'new' / 'resume'。"
                ),
            )
        if mode == "new":
            return "", "", "", ""
        if mode == "resume":
            ref = claude_sessions.normalize_session_ref(session_id)
            if not ref:
                return (
                    "",
                    "",
                    "",
                    (
                        "session_mode='resume' 需要同时提供 session_id（Claude Code 会话 UUID）。"
                        "可用 claude_code_list_sessions(include_history=true) 或插件面板查看可用会话。"
                    ),
                )
            try:
                meta = claude_sessions.find_session_by_id(ref)
            except Exception as e:
                self.logger.warning("find_session_by_id failed: {}", e)
                meta = None
            if meta is None:
                return (
                    "",
                    "",
                    "",
                    (
                        f"未找到 session_id={session_id!r} 对应的 Claude Code 会话。"
                        "请检查 UUID 是否正确（可用 claude_code_list_sessions(include_history=true) "
                        "或插件面板查看历史会话 UUID）。"
                    ),
                )
            full_id = str(meta.get("session_id") or ref)
            project_dir = str(meta.get("project_dir") or "")
            title = str(meta.get("title") or "")
            explicit_cwd = (cwd or "").strip()
            if explicit_cwd and project_dir:
                # Claude CLI 的 --resume 只在当前目录的会话索引里查找，
                # cwd 与会话归属目录不一致时必然失败 — 提前报清晰错误
                if os.path.normcase(os.path.normpath(explicit_cwd)) != os.path.normcase(
                    os.path.normpath(project_dir)
                ):
                    return (
                        "",
                        "",
                        "",
                        (
                            f"会话 {full_id} 归属目录为 {project_dir!r}，"
                            f"与传入的 cwd={explicit_cwd!r} 不一致。"
                            "请去掉 cwd 参数（自动采用会话归属目录），或传入正确的目录。"
                        ),
                    )
            return full_id, project_dir, title, ""
        # auto
        lookup_cwd = (cwd or "").strip() or self._config.cwd or os.getcwd()
        record = await self._session_mgr.find_resumable(lookup_cwd, signature)
        return (record.session_id if record else ""), "", "", ""

    # ------------------------------------------------------------------
    # 任务完成主动推送（TaskManager 回调 → push_message）
    # ------------------------------------------------------------------

    def _push_task_result(self, record: TaskRecord) -> None:
        """任务到达终态时把结果注入对话频道。

        用 push_message(visibility=[], ai_behavior="respond")：猫娘 LLM
        收到结果后自然生成汇报，长任务（半小时以上）期间无需轮询。
        被取消的任务不推送（通常是 followup 主动中断，新任务会另行推送）。
        """
        try:
            data = record.to_dict()
            elapsed = float(data.get("elapsed") or 0)
            prompt_brief = (record.prompt or "").strip()
            if len(prompt_brief) > 200:
                prompt_brief = prompt_brief[:200] + "…"

            if record.status == TaskStatus.DONE:
                result = data.get("result") or {}
                output = str(result.get("output") or "")
                if len(output) > 4000:
                    output = output[:4000] + "\n…（结果过长已截断）"
                lines = [
                    f"【Claude Code 任务完成】task_id={record.task_id}，耗时 {elapsed:.0f}s。"
                ]
                if record.session_id:
                    lines.append(
                        f"会话 session_id={record.session_id}（后续可用 claude_code_followup 在同一会话追加指令）"
                    )
                lines.append(f"轮询时间 poll_interval_sec={record.poll_interval_sec:.0f}s")
                lines.append(f"任务指令：{prompt_brief}")
                lines.append(f"执行结果：\n{output or '（无文本输出）'}")
            else:
                lines = [
                    f"【Claude Code 任务失败】task_id={record.task_id}，耗时 {elapsed:.0f}s。"
                ]
                if record.session_id:
                    lines.append(
                        f"会话 session_id={record.session_id}（可用 claude_code_followup 在同一会话重试/纠正）"
                    )
                lines.append(f"轮询时间 poll_interval_sec={record.poll_interval_sec:.0f}s")
                lines.append(f"任务指令：{prompt_brief}")
                lines.append(f"失败原因：{record.error_message or '未知错误'}")
            lines.append(
                "请基于以上结果，用你自己的语气向 {MASTER_NAME} 汇报这次 Claude Code 任务的情况。"
            )

            self.push_message(
                source="claude_code_adapter",
                visibility=[],
                ai_behavior="respond",
                parts=[{"type": "text", "text": "\n".join(lines)}],
                priority=6,
                metadata={
                    "kind": "claude_code_task_finished",
                    "task_id": record.task_id,
                    "status": record.status.value,
                    "session_id": record.session_id,
                    "poll_interval_sec": record.poll_interval_sec,
                    "elapsed_sec": round(elapsed, 1),
                },
            )
            self.logger.info("Task result pushed to dialog: {}", record.task_id)
        except Exception as e:
            # 推送失败不影响任务本身；猫娘仍可用 poll 取结果
            self.logger.warning("push task result failed: {}: {}", record.task_id, e)

    # ==================================================================
    # LLM 工具集（@llm_tool 装饰器注册）
    # ==================================================================

    @llm_tool(
        name="claude_code_submit",
        description=(
            "提交 Claude Code 编码任务到后台异步执行（唯一的执行入口，无超时限制，"
            "适合半小时到一小时以上的长任务）。Claude Code 是一个强大的 AI 编码助手，"
            "可以读写文件、运行命令、调试代码、写测试、查文档等。\n\n"
            "核心流程（重要）：\n"
            "1. 调用本工具提交任务，立即返回 task_id（不阻塞）\n"
            "2. 立刻回复用户「任务已交给 Claude Code，正在执行」之类的话，\n"
            "   不要原地等待——任务完成后插件会自动把结果推送给你，\n"
            "   你收到推送后再向用户汇报\n"
            "3. 若想提前盯进度，可用 claude_code_wait（单次最多 55s，可反复调用，"
            "   返回含实时进度）或 claude_code_poll 单次查询；取消用 claude_code_cancel\n"
            "4. 执行中发现方向跑偏，用 claude_code_followup 在同一会话追加纠正指令\n\n"
            "适用场景：写新功能/新文件、修改代码修 bug、运行测试构建项目、"
            "代码审查重构、Git 操作、插件打包上传等一切编码任务。\n\n"
            "参数说明：\n"
            "- prompt: 详细描述要让 Claude Code 做什么。要具体、清晰，包含必要的上下文。\n"
            "- cwd: 工作目录（项目根目录的绝对路径）。同一目录的调用会自动复用会话上下文。\n"
            "- model: 模型 ID（可选）。留空使用适配器默认配置。\n"
            "- effort: 推理努力级别（可选）：'low' / 'medium' / 'high'。\n"
            "- max_turns: 最大轮次（可选，0=使用默认值）。\n"
            "- session_mode: 'auto'（默认）/ 'new'（强制新会话）/ 'resume'（续跑指定会话）。\n"
            "- session_id: 仅 session_mode='resume' 时需要。\n"
            "- poll_interval_sec: 本任务轮询安全网间隔（秒，可选，默认 5 分钟=300）。\n"
            "  按预期任务时长设置：短任务设短、长任务设长；不传用默认值。\n\n"
            "返回：包含 task_id、status、session_id（Claude Code 会话 UUID）和 poll_interval_sec 的字典。\n"
            "重要：新会话的 UUID 由 Claude CLI 启动后返回，本工具会短暂等待（通常 1-3s）\n"
            "捕获后立即返回给你——拿到 session_id 即表示会话已建立，不要再重复开新会话。\n"
            "resume 会话的 UUID 提交时已知，立即返回无需等待。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "要交给 Claude Code 执行的任务描述。要具体、清晰，包含必要的项目上下文。",
                },
                "cwd": {
                    "type": "string",
                    "description": "工作目录（项目根目录的绝对路径）。留空使用适配器默认配置。",
                },
                "model": {
                    "type": "string",
                    "description": "模型 ID（可选）。例如 'sonnet' / 'opus'。留空使用默认配置。",
                },
                "effort": {
                    "type": "string",
                    "description": "推理努力级别（可选）：'low' / 'medium' / 'high'。留空使用默认配置。",
                    "enum": ["", "low", "medium", "high"],
                },
                "max_turns": {
                    "type": "integer",
                    "description": "最大轮次（可选，0=使用默认值）。",
                },
                "session_mode": {
                    "type": "string",
                    "description": (
                        "会话控制：'auto'（默认，自动复用）/ 'new'（强制新会话）/ "
                        "'resume'（续跑 session_id 指定的会话，支持任意历史会话）。"
                    ),
                    "enum": ["", "auto", "new", "resume"],
                },
                "session_id": {
                    "type": "string",
                    "description": (
                        "仅 session_mode='resume' 时需要：Claude Code 原生会话 UUID。"
                        "取自之前执行返回的 session_id、claude_code_list_sessions(include_history=true) "
                        "或插件面板；支持完整 UUID、唯一前缀或 'claude --resume <uuid>' 命令形态。"
                    ),
                },
                "poll_interval_sec": {
                    "type": "number",
                    "description": (
                        "本任务轮询安全网间隔（秒，可选）。默认 300（5 分钟）。"
                        "按预期任务时长设置：短任务设短、长任务设长。"
                        "也可事后用 claude_code_set_poll_interval 调整。"
                    ),
                },
            },
            "required": ["prompt"],
        },
        timeout=15.0,
    )
    async def claude_code_submit(
        self,
        prompt: str = "",
        cwd: str = "",
        model: str = "",
        effort: str = "",
        max_turns: int = 0,
        session_mode: str = "auto",
        session_id: str = "",
        poll_interval_sec: float = 0.0,
        **_,
    ) -> dict[str, Any]:
        """提交 Claude Code 任务到后台异步执行。"""
        not_ready = self._ensure_ready()
        if not_ready is not None:
            return not_ready

        if not prompt or not prompt.strip():
            return Err(SdkError("prompt 不能为空"))

        if self._task_mgr is None:
            return Err(SdkError("TaskManager not initialized"))

        try:
            assert self._session_mgr is not None
            # 提交时一次性解析续跑会话（TaskManager 不依赖 SessionManager）
            requested_cwd = (cwd or "").strip()
            signature = compute_prompt_signature(
                system_prompt_file=self._config.append_system_prompt_file,
                skills_dir=self._config.skills_dir,
            )
            (
                resume_id,
                resume_cwd,
                resume_title,
                resolve_err,
            ) = await self._resolve_resume_id(
                session_mode=session_mode,
                session_id=session_id,
                cwd=requested_cwd,
                signature=signature,
            )
            if resolve_err:
                return Err(SdkError(resolve_err))
            # 工作目录优先级：调用方显式指定 > 会话归属目录(resume) > 配置默认 > 进程 cwd
            effective_cwd = (
                requested_cwd or resume_cwd or self._config.cwd or os.getcwd()
            )

            record = await self._task_mgr.submit(
                prompt=prompt,
                cwd=effective_cwd,
                model=model,
                effort=effort,
                max_turns=max_turns,
                resume_session_id=resume_id,
                poll_interval_sec=poll_interval_sec,
            )

            # 解析会话 UUID：resume 会话提交时已知；新会话需等 CLI init 事件捕获
            if resume_id:
                session_uuid = resume_id
            else:
                session_uuid = await self._task_mgr.wait_session_id(
                    record.task_id, timeout=8.0
                )
                # 唯一性校验：新会话 UUID 必须与当前所有可获取的 UUID 绝对不同
                if session_uuid:
                    assert self._task_mgr is not None
                    collided = [
                        r.task_id
                        for r in self._task_mgr._tasks.values()
                        if r.task_id != record.task_id and r.session_id == session_uuid
                    ]
                    if collided:
                        await self._task_mgr.cancel(record.task_id)
                        return Err(
                            SdkError(
                                f"新会话 UUID {session_uuid} 与现有会话冲突"
                                f"（极罕见，task={collided}），已取消任务，请重试。"
                            )
                        )

            payload: dict[str, Any] = {
                "task_id": record.task_id,
                "status": record.status.value,
                "session_id": session_uuid,
                "poll_interval_sec": record.poll_interval_sec,
                "resumed_session": resume_id,
                "message": "任务已在后台开始执行（无超时限制）。请先回复用户「任务正在执行」，"
                "不要原地等待；任务完成后插件会自动把结果推送给你（携带本会话 UUID 与轮询时间）。"
                "想提前盯进度可反复调用 claude_code_wait（单次最多 55s，含实时进度）。",
            }
            if not session_uuid:
                # CLI 启动慢，init 事件尚未到达——猫娘可用 poll(task_id) 稍后取 UUID
                payload["session_id"] = ""
                payload["message"] = (
                    "任务已提交，Claude Code 会话 UUID 正在建立中（CLI 启动稍慢）。"
                    "请先回复用户「任务正在执行」，稍后用 claude_code_poll(task_id) 即可取到 session_id。"
                    "任务完成后插件会自动推送结果。"
                )
            if resume_title:
                payload["resumed_session_title"] = resume_title
            return Ok(payload)
        except Exception as e:
            self.logger.exception("claude_code_submit failed")
            return Err(SdkError(f"提交任务失败: {e}"))

    @llm_tool(
        name="claude_code_poll",
        description=(
            "查询 Claude Code 异步任务的执行状态和结果。\n\n"
            "使用流程：\n"
            "1. 调用 claude_code_submit 提交任务，获取 task_id\n"
            "2. 调用 claude_code_poll 查询任务状态\n"
            "3. 当 status 为 'done' 时，结果在 result 字段中\n"
            "4. 当 status 为 'error' 时，错误信息在 error 字段中\n\n"
            "参数说明：\n"
            "- task_id: 任务 ID（从 claude_code_submit 返回）\n\n"
            "返回：包含 task_id、status、result/error 的字典。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "任务 ID（从 claude_code_submit 返回）。",
                },
            },
            "required": ["task_id"],
        },
        timeout=10.0,
    )
    async def claude_code_poll(
        self,
        task_id: str = "",
        **_,
    ) -> dict[str, Any]:
        """查询 Claude Code 任务状态。"""
        not_ready = self._ensure_ready()
        if not_ready is not None:
            return not_ready

        if not task_id or not task_id.strip():
            return Err(SdkError("task_id 不能为空"))

        if self._task_mgr is None:
            return Err(SdkError("TaskManager not initialized"))

        try:
            result = await self._task_mgr.poll(task_id)
            if "error" in result and result["error"].startswith("Task not found"):
                return Err(SdkError(result["error"]))
            return Ok(result)
        except Exception as e:
            self.logger.exception("claude_code_poll failed")
            return Err(SdkError(f"查询任务失败: {e}"))

    @llm_tool(
        name="claude_code_wait",
        description=(
            "提前盯 Claude Code 异步任务的进度（可选工具）。在插件内部自动轮询，"
            "任务一完成立即返回结果。注意：任务完成后插件会自动推送结果给你，"
            "所以通常不需要一直等——本工具只用于你想提前盯进度的场景。\n\n"
            "使用约束（重要）：\n"
            "- 单次最多等 55 秒（受网关调用超时限制，超过会 ReadTimeout）\n"
            "- 返回 still_running=true 时，可携带返回的 progress（实时进度：\n"
            "  已输出消息数/最近动态）先向用户汇报进展，隔一会儿再调本工具\n\n"
            "参数说明：\n"
            "- task_id: 任务 ID（从 claude_code_submit 返回）\n"
            "- max_wait_sec: 本次最多等待的秒数（5-55，默认 50）\n"
            "- poll_interval: 内部轮询初始间隔秒数（可选，自适应退避，无需关心）\n\n"
            "返回：任务完成时返回完整结果；仍在运行时返回实时进度、"
            "still_running=true 和 suggested_next_wait_sec。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "任务 ID（从 claude_code_submit 返回）。",
                },
                "max_wait_sec": {
                    "type": "number",
                    "description": "本次最多等待的秒数（5-55，默认 50）。受网关调用超时限制，单次不能超过 55s。",
                },
                "poll_interval": {
                    "type": "number",
                    "description": "内部轮询初始间隔秒数（可选，1-15，默认自适应）。",
                },
            },
            "required": ["task_id"],
        },
        timeout=60.0,
    )
    async def claude_code_wait(
        self,
        task_id: str = "",
        max_wait_sec: float = 50.0,
        poll_interval: float = 0.0,
        **_,
    ) -> dict[str, Any]:
        """提前盯任务进度（任务完成后插件会自动推送，本工具非必需）。"""
        not_ready = self._ensure_ready()
        if not_ready is not None:
            return not_ready

        if not task_id or not task_id.strip():
            return Err(SdkError("task_id 不能为空"))

        if self._task_mgr is None:
            return Err(SdkError("TaskManager not initialized"))

        try:
            # 夹在安全区间：网关远程工具调用 ~60s 会 ReadTimeout，单次等待不超 55s
            wait_sec = max(5.0, min(55.0, float(max_wait_sec or 50.0)))
            result = await self._task_mgr.wait_for(
                task_id, max_wait_sec=wait_sec, interval_sec=float(poll_interval or 0)
            )
            if "error" in result and result["error"].startswith("Task not found"):
                return Err(SdkError(result["error"]))
            return Ok(result)
        except Exception as e:
            self.logger.exception("claude_code_wait failed")
            return Err(SdkError(f"等待任务失败: {e}"))

    @llm_tool(
        name="claude_code_cancel",
        description=(
            "取消正在运行的 Claude Code 异步任务。\n\n"
            "适用场景：\n"
            "- 任务执行时间过长，想提前终止\n"
            "- 提交了错误的任务，需要取消\n"
            "- 需要释放资源\n\n"
            "参数说明：\n"
            "- task_id: 任务 ID（从 claude_code_submit 返回）\n\n"
            "返回：包含 task_id 和 status 的字典。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "任务 ID（从 claude_code_submit 返回）。",
                },
            },
            "required": ["task_id"],
        },
        timeout=10.0,
    )
    async def claude_code_cancel(
        self,
        task_id: str = "",
        **_,
    ) -> dict[str, Any]:
        """取消 Claude Code 任务。"""
        not_ready = self._ensure_ready()
        if not_ready is not None:
            return not_ready

        if not task_id or not task_id.strip():
            return Err(SdkError("task_id 不能为空"))

        if self._task_mgr is None:
            return Err(SdkError("TaskManager not initialized"))

        try:
            result = await self._task_mgr.cancel(task_id)
            if "error" in result:
                return Err(SdkError(result["error"]))
            return Ok(result)
        except Exception as e:
            self.logger.exception("claude_code_cancel failed")
            return Err(SdkError(f"取消任务失败: {e}"))

    @llm_tool(
        name="claude_code_followup",
        description=(
            "在同一个 Claude Code 会话中追加补充/纠正指令（异步执行）。\n\n"
            "适用场景：\n"
            "- 任务正在执行，用户发现方向有偏差，需要立即纠正：\n"
            "  传 task_id，插件会先中断当前任务，再在同一会话（同一 UUID）里\n"
            "  把纠正指令发给 Claude Code，已有上下文全部保留\n"
            "- 任务已完成，想对同一成果继续提要求：传 task_id 或 session_id 均可\n\n"
            "参数说明：\n"
            "- instruction: 要追加的补充/纠正指令（必填），要具体清晰\n"
            "- task_id: 目标异步任务 ID（从 claude_code_submit 返回）。\n"
            "  任务运行中→自动中断后续发；已完成→直接在它的会话里续发\n"
            "- session_id: Claude Code 会话 UUID（与 task_id 二选一）。\n"
            "  支持完整 UUID、唯一前缀或 'claude --resume <uuid>' 命令形态\n\n"
            "返回：新的 task_id 和续用的 session_id；"
            "后续用 claude_code_wait 等待追加指令的执行结果。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "instruction": {
                    "type": "string",
                    "description": "要追加给 Claude Code 的补充/纠正指令。要具体、清晰。",
                },
                "task_id": {
                    "type": "string",
                    "description": (
                        "目标异步任务 ID（从 claude_code_submit 返回）。"
                        "任务运行中会先被中断，然后在同一会话续发。与 session_id 二选一。"
                    ),
                },
                "session_id": {
                    "type": "string",
                    "description": (
                        "Claude Code 会话 UUID（与 task_id 二选一）。"
                        "支持完整 UUID、唯一前缀或 'claude --resume <uuid>' 命令形态。"
                    ),
                },
                "model": {
                    "type": "string",
                    "description": "模型 ID（可选）。留空使用默认配置。",
                },
                "effort": {
                    "type": "string",
                    "description": "推理努力级别（可选）：'low' / 'medium' / 'high'。",
                    "enum": ["", "low", "medium", "high"],
                },
                "max_turns": {
                    "type": "integer",
                    "description": "最大轮次（可选，0=使用默认值）。",
                },
                "poll_interval_sec": {
                    "type": "number",
                    "description": (
                        "本任务轮询安全网间隔（秒，可选，默认 5 分钟=300）。"
                        "按预期任务时长设置：短任务设短、长任务设长。"
                    ),
                },
            },
            "required": ["instruction"],
        },
        timeout=30.0,
    )
    async def claude_code_followup(
        self,
        instruction: str = "",
        task_id: str = "",
        session_id: str = "",
        model: str = "",
        effort: str = "",
        max_turns: int = 0,
        poll_interval_sec: float = 0.0,
        **_,
    ) -> dict[str, Any]:
        """在同一会话中追加补充/纠正指令。"""
        not_ready = self._ensure_ready()
        if not_ready is not None:
            return not_ready

        if not instruction or not instruction.strip():
            return Err(SdkError("instruction 不能为空"))
        if self._task_mgr is None:
            return Err(SdkError("TaskManager not initialized"))

        tid = (task_id or "").strip()
        sid_ref = (session_id or "").strip()
        if not tid and not sid_ref:
            return Err(
                SdkError(
                    "需要指定 task_id（正在执行/已完成的异步任务）或 "
                    "session_id（Claude Code 会话 UUID）之一，"
                    "以确定追加指令的目标会话。"
                )
            )

        try:
            target_sid = ""
            fallback_cwd = ""
            interrupted = False

            if tid:
                # 从异步任务定位目标会话
                record = self._task_mgr.get_record(tid)
                if record is None:
                    return Err(SdkError(f"Task not found: {tid}"))
                fallback_cwd = record.cwd
                running = record.status in (
                    TaskStatus.PENDING,
                    TaskStatus.RUNNING,
                )
                if running:
                    # 中断跑偏的任务；session_id 已在 init 事件时早期捕获
                    cancel_result = await self._task_mgr.cancel(tid)
                    if "error" in cancel_result:
                        return Err(SdkError(cancel_result["error"]))
                    interrupted = True
                target_sid = record.session_id or record.resume_session_id
                if not target_sid:
                    return Err(
                        SdkError(
                            f"任务 {tid} 尚未建立 Claude Code 会话"
                            "（CLI 还没输出 init 事件），无法在同一会话续发。"
                            "请稍等几秒后重试，或直接重新提交任务。"
                        )
                    )
            else:
                # 直接按会话 UUID 定位（支持裸 UUID / 唯一前缀 / 命令形态）
                ref = claude_sessions.normalize_session_ref(sid_ref)
                meta = claude_sessions.find_session_by_id(ref)
                if meta is None:
                    return Err(
                        SdkError(
                            f"未找到 session_id={sid_ref!r} 对应的 Claude Code 会话。"
                            "可用 claude_code_list_sessions(include_history=true) "
                            "或插件面板查看历史会话 UUID。"
                        )
                    )
                target_sid = str(meta.get("session_id") or ref)
                fallback_cwd = str(meta.get("project_dir") or "")

            # 工作目录：会话归属目录优先（Claude CLI 按 cwd 索引会话），
            # 存档未落盘时回退到任务提交时的 cwd
            effective_cwd = ""
            try:
                meta = claude_sessions.find_session_by_id(target_sid)
                if meta and meta.get("project_dir"):
                    effective_cwd = str(meta["project_dir"])
            except Exception:
                pass
            effective_cwd = (
                effective_cwd or fallback_cwd or self._config.cwd or os.getcwd()
            )

            new_record = await self._task_mgr.submit(
                prompt=instruction,
                cwd=effective_cwd,
                model=model,
                effort=effort,
                max_turns=max_turns,
                resume_session_id=target_sid,
                poll_interval_sec=poll_interval_sec,
            )
            payload: dict[str, Any] = {
                "task_id": new_record.task_id,
                "status": new_record.status.value,
                "session_id": target_sid,
                "poll_interval_sec": new_record.poll_interval_sec,
                "message": (
                    "追加指令已提交，将在同一会话中继续。"
                    "建议调用 claude_code_wait 等待执行结果。"
                ),
            }
            if interrupted:
                payload["interrupted_task_id"] = tid
                payload["message"] = (
                    f"已中断原任务 {tid}，纠正指令将在同一会话 {target_sid} 中续发。"
                    "建议调用 claude_code_wait 等待执行结果。"
                )
            return Ok(payload)
        except Exception as e:
            self.logger.exception("claude_code_followup failed")
            return Err(SdkError(f"追加指令失败: {e}"))

    @llm_tool(
        name="claude_code_check_health",
        description=(
            "检查 Claude Code CLI 是否可用。返回 CLI 路径、版本信息和适配器状态。\n\n"
            "适用场景：\n"
            "- 在调用 claude_code_submit 之前确认环境就绪\n"
            "- 诊断 Claude Code 相关问题\n"
            "- 检查适配器配置是否正确\n\n"
            "返回：包含 cli_available、cli_path、config 等信息的字典。"
        ),
        parameters={
            "type": "object",
            "properties": {},
        },
        timeout=10.0,
    )
    async def claude_code_check_health(self, **_) -> dict[str, Any]:
        """检查 Claude CLI 健康状态。"""
        try:
            cli_path = self._config.command or detect_claude_cli()
            cli_available = bool(cli_path)

            # 尝试获取版本（执行 claude --version）
            version = ""
            if cli_available:
                try:
                    import asyncio as _asyncio

                    proc = await _asyncio.create_subprocess_exec(
                        cli_path,
                        "--version",
                        stdout=_asyncio.subprocess.PIPE,
                        stderr=_asyncio.subprocess.PIPE,
                    )
                    try:
                        stdout, _ = await _asyncio.wait_for(
                            proc.communicate(), timeout=5.0
                        )
                        version = stdout.decode("utf-8", errors="replace").strip()
                    except _asyncio.TimeoutError:
                        try:
                            proc.kill()
                        except Exception:
                            pass
                except Exception as e:
                    version = f"(version check failed: {e})"

            return Ok(
                {
                    "cli_available": cli_available,
                    "cli_path": cli_path or "",
                    "version": version,
                    "ready": self._ready,
                    "config": self._config.to_dict(),
                    "sessions_count": len(self._session_mgr._sessions)
                    if self._session_mgr
                    else 0,
                }
            )
        except Exception as e:
            self.logger.exception("claude_code_check_health failed")
            return Err(SdkError(f"健康检查失败: {e}"))

    @llm_tool(
        name="claude_code_list_sessions",
        description=(
            "列出 Claude Code 会话记录。默认列出插件维护的活跃会话索引；"
            "include_history=true 时额外扫描 ~/.claude/projects 原生存档，"
            "列出所有历史会话（含 session_id/标题/归属目录/最近活跃时间）。\n\n"
            "适用场景：\n"
            "- 查看当前有哪些活跃的 Claude Code 会话\n"
            "- include_history=true：找回历史会话的 UUID，配合 "
            "claude_code_submit(session_mode='resume', session_id=<UUID>) 回到该会话的上下文\n\n"
            "返回：包含 sessions 列表的字典，每个会话含 session_id（Claude Code 原生 UUID）、"
            "cwd、turn_count 等字段；历史会话额外含 title、resume_command。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "include_history": {
                    "type": "boolean",
                    "description": (
                        "是否同时列出 ~/.claude/projects 中的所有历史会话（默认 false）。"
                        "需要找回旧会话 UUID 续跑时设为 true。"
                    ),
                },
                "cwd": {
                    "type": "string",
                    "description": "可选：只列出归属该工作目录的会话。",
                },
                "limit": {
                    "type": "integer",
                    "description": "历史会话最多返回条数（可选，默认 50，仅 include_history=true 时生效）。",
                },
            },
        },
        timeout=30.0,
    )
    async def claude_code_list_sessions(
        self,
        include_history: bool = False,
        cwd: str = "",
        limit: int = 50,
        **_,
    ) -> dict[str, Any]:
        """列出会话（可选含历史会话）。"""
        not_ready = self._ensure_ready()
        if not_ready is not None:
            return not_ready

        try:
            assert self._session_mgr is not None
            cwd_filter = os.path.normcase(os.path.normpath(cwd)) if cwd.strip() else ""

            records = await self._session_mgr.list_all()
            sessions: list[dict[str, Any]] = []
            seen_ids: set[str] = set()
            for r in records:
                if (
                    cwd_filter
                    and os.path.normcase(os.path.normpath(r.cwd)) != cwd_filter
                ):
                    continue
                item = r.to_dict()
                item["source"] = "active"
                sessions.append(item)
                if r.session_id:
                    seen_ids.add(r.session_id.lower())

            history_count = 0
            if include_history:
                max_history = max(1, int(limit or 50))
                for meta in claude_sessions.scan_sessions(limit=0):
                    sid = str(meta.get("session_id") or "")
                    project_dir = str(meta.get("project_dir") or "")
                    if cwd_filter:
                        if (
                            not project_dir
                            or os.path.normcase(os.path.normpath(project_dir))
                            != cwd_filter
                        ):
                            continue
                    if sid.lower() in seen_ids:
                        continue
                    seen_ids.add(sid.lower())
                    sessions.append(
                        {
                            "source": "history",
                            "session_id": sid,
                            "title": meta.get("title", ""),
                            "cwd": project_dir,
                            "created_at": meta.get("created_at"),
                            "last_used_at": meta.get("last_active_at"),
                            "resume_command": meta.get("resume_command", ""),
                        }
                    )
                    history_count += 1
                    if history_count >= max_history:
                        break

            return Ok(
                {
                    "count": len(sessions),
                    "history_count": history_count,
                    "sessions": sessions,
                }
            )
        except Exception as e:
            self.logger.exception("claude_code_list_sessions failed")
            return Err(SdkError(f"列出会话失败: {e}"))

    @llm_tool(
        name="claude_code_clear_session",
        description=(
            "清理 Claude Code 会话列表——取消活跃任务并杀掉对应 Claude Code 进程，"
            "同时清空会话索引。\n\n"
            "对应「清理会话列表 → Claude Code 同一会话进程关闭」。清理后释放的会话槽位"
            "可让猫娘继续传同一 UUID 维持会话并再次进入任务列表。\n\n"
            "参数（三选一，都不传则清理全部）：\n"
            "- task_id: 只清理指定任务（取消并杀掉其 CC 进程，从列表释放）\n"
            "- session_id: 只清理指定会话（按 UUID 定位活跃任务 + 清除索引）\n"
            "- cwd: 只清理指定工作目录的会话索引\n\n"
            "返回：包含 cleared_count、cancelled_active 的字典。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "只清理指定任务（从 claude_code_submit/list_tasks 返回）。",
                },
                "session_id": {
                    "type": "string",
                    "description": "只清理指定会话（Claude Code 会话 UUID，支持前缀/命令形态）。",
                },
                "cwd": {
                    "type": "string",
                    "description": "只清理指定工作目录的会话索引（绝对路径）。",
                },
            },
        },
        timeout=15.0,
    )
    async def claude_code_clear_session(
        self,
        task_id: str = "",
        session_id: str = "",
        cwd: str = "",
        **_,
    ) -> dict[str, Any]:
        """清理会话列表（取消活跃任务 + 杀 CC 进程 + 清空索引）。"""
        not_ready = self._ensure_ready()
        if not_ready is not None:
            return not_ready

        try:
            assert self._session_mgr is not None
            assert self._task_mgr is not None

            tid = (task_id or "").strip()
            sid_ref = (session_id or "").strip()
            target_cwd = (cwd or "").strip() or None

            # 1) 按 task_id 定向清理
            if tid:
                res = await self._task_mgr.clear_one(tid)
                if "error" in res:
                    return Err(SdkError(res["error"]))
                return Ok(
                    {
                        "cleared_count": 1,
                        "cancelled_active": res.get("cancelled", False),
                        "task_id": tid,
                    }
                )

            # 2) 按 session_id 定向清理：先在活跃任务里找，找不到也清索引
            if sid_ref:
                ref = claude_sessions.normalize_session_ref(sid_ref)
                matched_tid = ""
                for r in list(self._task_mgr._tasks.values()):
                    if r.session_id and r.session_id.lower() == ref.lower():
                        matched_tid = r.task_id
                        break
                cancelled_active = 0
                if matched_tid:
                    res = await self._task_mgr.clear_one(matched_tid)
                    cancelled_active = 1 if res.get("cancelled") else 0
                # 同步清除该会话的持久化索引（若存在）
                store_cleared = await self._session_mgr.clear(target_cwd)
                return Ok(
                    {
                        "cleared_count": (1 if matched_tid else 0) + store_cleared,
                        "cancelled_active": cancelled_active,
                        "session_id": sid_ref,
                    }
                )

            # 3) 按 cwd 清理：取消该 cwd 的活跃任务 + 清索引
            if target_cwd:
                import os as _os

                norm = _os.path.normcase(_os.path.normpath(target_cwd))
                tids = [
                    r.task_id
                    for r in list(self._task_mgr._tasks.values())
                    if _os.normcase(_os.path.normpath(r.cwd)) == norm
                ]
                cancelled = 0
                for t in tids:
                    res = await self._task_mgr.clear_one(t)
                    if res.get("cancelled"):
                        cancelled += 1
                store_cleared = await self._session_mgr.clear(target_cwd)
                return Ok(
                    {
                        "cleared_count": cancelled + store_cleared,
                        "cancelled_active": cancelled,
                        "cwd": target_cwd,
                    }
                )

            # 4) 全清：取消所有活跃任务（杀全部 CC 进程）+ 清空索引
            res = await self._task_mgr.clear_all()
            store_cleared = await self._session_mgr.clear()
            self.logger.info(
                "Cleared all sessions: active_cancelled={} store_cleared={}",
                res.get("cancelled_active", 0),
                store_cleared,
            )
            return Ok(
                {
                    "cleared_count": res.get("cleared_count", 0) + store_cleared,
                    "cancelled_active": res.get("cancelled_active", 0),
                    "scope": "all",
                }
            )
        except Exception as e:
            self.logger.exception("claude_code_clear_session failed")
            return Err(SdkError(f"清理会话失败: {e}"))

    @llm_tool(
        name="claude_code_list_tasks",
        description=(
            "列出当前活跃的 Claude Code 会话/任务列表（与正在执行的 Claude Code 进程一一对应）。\n\n"
            "每个会话含 task_id、session_id（会话 UUID）、status、poll_interval_sec（轮询时间）、"
            "已耗时、实时进度等。猫娘据此判断当前开了几个会话、是否需要清理或追加指令。\n\n"
            "注意：已完成的任务会从列表自动释放（结果已推送给你）；想看历史会话用 "
            "claude_code_list_sessions(include_history=true)。猫娘最多同时管理 2 个会话。"
        ),
        parameters={
            "type": "object",
            "properties": {},
        },
        timeout=10.0,
    )
    async def claude_code_list_tasks(self, **_) -> dict[str, Any]:
        """列出活跃会话/任务（与 CC 进程一一对应）。"""
        not_ready = self._ensure_ready()
        if not_ready is not None:
            return not_ready
        if self._task_mgr is None:
            return Err(SdkError("TaskManager not initialized"))
        try:
            tasks = self._task_mgr.list_active()
            return Ok(
                {
                    "count": len(tasks),
                    "max_concurrent": self._task_mgr._max_concurrent,
                    "default_poll_interval_sec": self._task_mgr._default_poll_interval,
                    "tasks": tasks,
                }
            )
        except Exception as e:
            self.logger.exception("claude_code_list_tasks failed")
            return Err(SdkError(f"列出任务失败: {e}"))

    @llm_tool(
        name="claude_code_set_poll_interval",
        description=(
            "调整 Claude Code 任务的轮询安全网间隔（默认 5 分钟=300 秒）。\n\n"
            "用途：按预期任务完成时间合理设置轮询时间——短任务设短、长任务设长。\n"
            "可在提交时传 poll_interval_sec 单次覆盖，也可用本工具事后调整默认值或单个任务。\n\n"
            "参数：\n"
            "- interval_sec: 要设置的单任务轮询间隔（秒）。需配合 task_id 使用。\n"
            "- default: 要设置的默认轮询间隔（秒），影响后续新任务。\n"
            "- task_id: 仅调整该任务的轮询间隔（运行中也即时生效）。\n\n"
            "返回：更新后的默认/单任务轮询间隔。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "interval_sec": {
                    "type": "number",
                    "description": "单任务轮询间隔（秒），配合 task_id 使用。",
                },
                "default": {
                    "type": "number",
                    "description": "默认轮询间隔（秒），影响后续新任务。",
                },
                "task_id": {
                    "type": "string",
                    "description": "仅调整该任务的轮询间隔（可选）。",
                },
            },
        },
        timeout=5.0,
    )
    async def claude_code_set_poll_interval(
        self,
        interval_sec: float = 0.0,
        default: float = 0.0,
        task_id: str = "",
        **_,
    ) -> dict[str, Any]:
        """调整轮询安全网间隔（默认/单任务）。"""
        not_ready = self._ensure_ready()
        if not_ready is not None:
            return not_ready
        if self._task_mgr is None:
            return Err(SdkError("TaskManager not initialized"))
        try:
            res = self._task_mgr.set_poll_interval(
                task_id=task_id.strip(),
                interval_sec=interval_sec,
                default=default,
            )
            if "error" in res:
                return Err(SdkError(res["error"]))
            return Ok(res)
        except Exception as e:
            self.logger.exception("claude_code_set_poll_interval failed")
            return Err(SdkError(f"调整轮询间隔失败: {e}"))

    @llm_tool(
        name="claude_code_get_config",
        description=(
            "获取 Claude Code 适配器的当前配置。\n\n"
            "适用场景：\n"
            "- 了解默认模型、超时、工作目录等设置\n"
            "- 诊断配置问题\n"
            "- 在调用 claude_code_submit 前确认参数默认值\n\n"
            "返回：包含完整适配器配置的字典。"
        ),
        parameters={
            "type": "object",
            "properties": {},
        },
        timeout=5.0,
    )
    async def claude_code_get_config(self, **_) -> dict[str, Any]:
        """获取适配器配置。"""
        try:
            return Ok(
                {
                    "config": self._config.to_dict(),
                    "ready": self._ready,
                    "default_cwd": self._config.cwd or os.getcwd(),
                    "active_provider": self._provider_mgr.get_active_name()
                    if self._provider_mgr
                    else "",
                }
            )
        except Exception as e:
            return Err(SdkError(f"获取配置失败: {e}"))

    @llm_tool(
        name="claude_code_list_providers",
        description=(
            "列出所有已注册的 Claude Code 供应商（API 提供方），并标出当前激活项。\n\n"
            "供应商决定 Claude Code 走哪个 API 端点（官方 Anthropic / 第三方中转 / "
            "OpenRouter 等），切换后对后续所有执行生效。\n\n"
            "返回：包含 providers 列表（token 已打码）和 active_provider 的字典。"
        ),
        parameters={
            "type": "object",
            "properties": {},
        },
        timeout=5.0,
    )
    async def claude_code_list_providers(self, **_) -> dict[str, Any]:
        """列出所有供应商。"""
        try:
            assert self._provider_mgr is not None
            return Ok(
                {
                    "active_provider": self._provider_mgr.get_active_name(),
                    "providers": self._provider_mgr.list_providers(),
                }
            )
        except Exception as e:
            self.logger.exception("claude_code_list_providers failed")
            return Err(SdkError(f"列出供应商失败: {e}"))

    @llm_tool(
        name="claude_code_switch_provider",
        description=(
            "切换 Claude Code 的 API 供应商（cc-switch 风格）。切换后对后续所有\n"
            "claude_code_submit 调用生效，不影响已在运行的任务。\n\n"
            "参数说明：\n"
            "- name: 供应商名称（从 claude_code_list_providers 获取）。\n"
            "  传空字符串表示清除激活状态，回退到用户全局 ~/.claude 配置。\n"
            "- base_url: （可选）新供应商的 API 端点。传入后会先注册/更新该供应商再激活。\n"
            "- auth_token: （可选）新供应商的认证 token。\n"
            "- display_name: （可选）展示名称。\n\n"
            "返回：包含 active_provider 和生效环境变量的字典。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "供应商名称（小写标识）。传空字符串清除激活状态。",
                },
                "base_url": {
                    "type": "string",
                    "description": "API 端点（如 https://api.anthropic.com）。传入后会先注册/更新再激活。",
                },
                "auth_token": {
                    "type": "string",
                    "description": "认证 token（ANTHROPIC_AUTH_TOKEN）。可选。",
                },
                "display_name": {
                    "type": "string",
                    "description": "展示名称。可选。",
                },
            },
            "required": ["name"],
        },
        timeout=5.0,
    )
    async def claude_code_switch_provider(
        self,
        name: str = "",
        base_url: str = "",
        auth_token: str = "",
        display_name: str = "",
        **_,
    ) -> dict[str, Any]:
        """切换/注册供应商。"""
        if self._provider_mgr is None:
            return Err(SdkError("ProviderManager not initialized"))

        try:
            key = name.strip().lower()

            # 携带新配置 → 先注册/更新
            if key and (base_url or auth_token):
                env: dict[str, str] = {}
                if base_url.strip():
                    env["ANTHROPIC_BASE_URL"] = base_url.strip()
                if auth_token.strip():
                    env["ANTHROPIC_AUTH_TOKEN"] = auth_token.strip()
                self._provider_mgr.add_or_update(
                    Provider(name=key, display_name=display_name, env=env)
                )

            provider = self._provider_mgr.set_active(key)
            return Ok(
                {
                    "active_provider": self._provider_mgr.get_active_name(),
                    "display_name": provider.display_name or provider.name,
                    "env": {
                        k: (v[:4] + "****" if "TOKEN" in k or "KEY" in k else v)
                        for k, v in self._provider_mgr.env_overrides().items()
                    },
                    "message": "供应商已切换，对后续执行生效"
                    if key
                    else "已回退到用户全局配置",
                }
            )
        except KeyError:
            return Err(
                SdkError(
                    f"供应商不存在: {name}。请先用 claude_code_list_providers 查看，"
                    "或传入 base_url/auth_token 注册新供应商。"
                )
            )
        except Exception as e:
            self.logger.exception("claude_code_switch_provider failed")
            return Err(SdkError(f"切换供应商失败: {e}"))

    @llm_tool(
        name="claude_code_remove_provider",
        description=(
            "删除已注册的 Claude Code 供应商。\n\n"
            "参数说明：\n"
            "- name: 要删除的供应商名称。\n\n"
            "返回：包含 deleted 布尔值的字典。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "要删除的供应商名称。",
                },
            },
            "required": ["name"],
        },
        timeout=5.0,
    )
    async def claude_code_remove_provider(self, name: str = "", **_) -> dict[str, Any]:
        """删除供应商。"""
        if self._provider_mgr is None:
            return Err(SdkError("ProviderManager not initialized"))

        if not name or not name.strip():
            return Err(SdkError("name 不能为空"))

        try:
            deleted = self._provider_mgr.remove(name)
            if not deleted:
                return Err(SdkError(f"供应商不存在: {name}"))
            return Ok(
                {
                    "deleted": True,
                    "name": name.strip().lower(),
                    "active_provider": self._provider_mgr.get_active_name(),
                }
            )
        except Exception as e:
            self.logger.exception("claude_code_remove_provider failed")
            return Err(SdkError(f"删除供应商失败: {e}"))

    # ==================================================================
    # 插件入口（供 UI / 其他插件调用，非 LLM 工具）
    # ==================================================================

    @plugin_entry(
        id="execute",
        name="执行 Claude Code 任务",
        description="提交 Claude Code 任务到后台异步执行（v0.6.0 起全异步，与 claude_code_submit 相同），立即返回 task_id，完成后自动推送结果。",
        llm_result_fields=["task_id", "status"],
        input_schema={
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "任务描述"},
                "cwd": {"type": "string", "description": "工作目录"},
                "model": {"type": "string", "description": "模型 ID"},
                "effort": {"type": "string", "description": "推理努力级别"},
                "max_turns": {"type": "integer", "description": "最大轮次"},
                "session_mode": {
                    "type": "string",
                    "description": "会话控制：auto / new / resume",
                },
                "session_id": {
                    "type": "string",
                    "description": "session_mode=resume 时要续跑的会话 ID",
                },
            },
            "required": ["prompt"],
        },
    )
    async def execute_entry(
        self,
        prompt: str = "",
        cwd: str = "",
        model: str = "",
        effort: str = "",
        max_turns: int = 0,
        session_mode: str = "auto",
        session_id: str = "",
        **_,
    ) -> Any:
        """插件入口（全异步：委托 claude_code_submit，供 UI/其他插件调用）。"""
        return await self.claude_code_submit(
            prompt=prompt,
            cwd=cwd,
            model=model,
            effort=effort,
            max_turns=max_turns,
            session_mode=session_mode,
            session_id=session_id,
        )

    @plugin_entry(
        id="submit_task",
        name="提交 Claude Code 异步任务",
        description="提交 Claude Code 任务到后台异步执行（供 UI/其他插件调用）。",
        llm_result_fields=["task_id", "status"],
        input_schema={
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "任务描述"},
                "cwd": {"type": "string", "description": "工作目录"},
                "model": {"type": "string", "description": "模型 ID"},
                "effort": {"type": "string", "description": "推理努力级别"},
                "max_turns": {"type": "integer", "description": "最大轮次"},
            },
            "required": ["prompt"],
        },
    )
    async def submit_task_entry(
        self,
        prompt: str = "",
        cwd: str = "",
        model: str = "",
        effort: str = "",
        max_turns: int = 0,
        **_,
    ) -> Any:
        """插件入口（提交异步任务，供 UI/其他插件调用）。"""
        not_ready = self._ensure_ready()
        if not_ready is not None:
            return not_ready

        if not prompt or not prompt.strip():
            return Err(SdkError("prompt 不能为空"))

        if self._task_mgr is None:
            return Err(SdkError("TaskManager not initialized"))

        try:
            record = await self._task_mgr.submit(
                prompt=prompt,
                cwd=cwd,
                model=model,
                effort=effort,
                max_turns=max_turns,
            )
            return Ok(
                {
                    "task_id": record.task_id,
                    "status": record.status.value,
                }
            )
        except Exception as e:
            self.logger.exception("submit_task_entry failed")
            return Err(SdkError(f"提交任务失败: {e}"))

    # ------------------------------------------------------------------
    # Hosted panel UI context（缺它面板 api.call 会报
    # "UI context 'dashboard' not found" → 扫描会话失败）
    # ------------------------------------------------------------------

    @ui.context(id="dashboard", title="CC Switch 会话管理")
    async def dashboard_ui_context(self, **_) -> dict[str, Any]:
        """panel:main surface 的只读上下文 provider。

        hosted-surface 的 ``api.call`` 走 ``call_surface_action`` →
        ``host.get_ui_context(<surface 的 context_id>)``，缺少对应
        provider 会让面板所有 action 调用直接失败。这里只返回
        轻量状态快照，真实数据由面板通过下方 @ui.action 条目拉取。
        """
        tasks: list[dict[str, Any]] = []
        mgr = self._task_mgr
        if mgr is not None:
            for record in list(mgr._tasks.values()):
                if record.status in (TaskStatus.PENDING, TaskStatus.RUNNING):
                    tasks.append(
                        {
                            "task_id": record.task_id,
                            "status": record.status.value,
                            "session_id": record.session_id,
                        }
                    )
        return {
            "ready": self._ready,
            "active_provider": self._provider_mgr.get_active_name()
            if self._provider_mgr
            else "",
            "active_tasks": tasks,
        }

    # ------------------------------------------------------------------
    # Claude 原生会话管理（供前端面板调用，照搬 cc-switch 会话管理器能力）
    # ------------------------------------------------------------------

    @ui.action(label="扫描会话", group="sessions", order=10)
    @plugin_entry(
        id="list_claude_sessions",
        name="列出 Claude 会话",
        description="扫描 ~/.claude/projects 下的所有 Claude Code 会话记录（标题/时间/项目目录），供前端面板展示。",
        input_schema={
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "最多返回的会话数（默认 200）",
                },
            },
        },
    )
    async def list_claude_sessions_entry(self, limit: int = 200, **_) -> Any:
        """扫描 Claude 原生会话存档。"""
        try:
            sessions = claude_sessions.scan_sessions(limit=int(limit or 200))
            self.logger.info(
                "Session scan finished: roots={} count={}",
                claude_sessions.candidate_project_roots(),
                len(sessions),
            )
            return Ok({"count": len(sessions), "sessions": sessions})
        except Exception as e:
            self.logger.exception("list_claude_sessions_entry failed")
            return Err(SdkError(f"扫描会话失败: {e}"))

    @ui.action(label="读取会话消息", group="sessions", order=20)
    @plugin_entry(
        id="get_claude_session",
        name="读取会话消息",
        description="读取指定 Claude 会话的完整消息列表（用户/AI/工具消息），供前端面板展示对话内容。",
        input_schema={
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "会话 ID",
                },
            },
            "required": ["session_id"],
        },
    )
    async def get_claude_session_entry(self, session_id: str = "", **_) -> Any:
        """读取会话消息。"""
        sid = (session_id or "").strip()
        if not sid:
            return Err(SdkError("session_id 不能为空"))
        try:
            # 定位会话文件（支持完整 UUID / 唯一前缀 / 命令形态）
            target = claude_sessions.find_session_by_id(sid)
            if target is None:
                return Err(SdkError(f"会话不存在: {sid}"))
            full_id = str(target.get("session_id") or sid)
            messages = claude_sessions.load_messages(
                target["source_path"], expected_session_id=full_id
            )
            return Ok(
                {
                    "session_id": full_id,
                    "title": target["title"],
                    "project_dir": target["project_dir"],
                    "created_at": target["created_at"],
                    "last_active_at": target["last_active_at"],
                    "resume_command": target.get("resume_command", ""),
                    "message_count": len(messages),
                    "messages": messages,
                }
            )
        except Exception as e:
            self.logger.exception("get_claude_session_entry failed")
            return Err(SdkError(f"读取会话失败: {e}"))

    @ui.action(label="删除会话", tone="danger", group="sessions", order=30)
    @plugin_entry(
        id="delete_claude_session",
        name="删除 Claude 会话",
        description="删除指定的 Claude 会话文件及同名 sidecar 目录（与 cc-switch 删除行为一致）。",
        input_schema={
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "要删除的会话 ID",
                },
            },
            "required": ["session_id"],
        },
    )
    async def delete_claude_session_entry(self, session_id: str = "", **_) -> Any:
        """删除会话。"""
        sid = (session_id or "").strip()
        if not sid:
            return Err(SdkError("session_id 不能为空"))
        try:
            target = claude_sessions.find_session_by_id(sid)
            if target is None:
                return Err(SdkError(f"会话不存在: {sid}"))
            full_id = str(target.get("session_id") or sid)
            deleted = claude_sessions.delete_session(target["source_path"], full_id)
            if not deleted:
                return Err(SdkError(f"删除失败（ID 不匹配或文件占用）: {full_id}"))
            self.logger.info("Deleted Claude session: {}", full_id)
            return Ok({"deleted": True, "session_id": full_id})
        except Exception as e:
            self.logger.exception("delete_claude_session_entry failed")
            return Err(SdkError(f"删除会话失败: {e}"))


__all__ = ["ClaudeCodeAdapterPlugin"]
