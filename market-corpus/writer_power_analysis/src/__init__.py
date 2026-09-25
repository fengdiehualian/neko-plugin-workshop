"""Writer power analysis plugin for N.E.K.O."""

from __future__ import annotations

from typing import Any, ClassVar

from plugin.sdk.plugin import (
    Err,
    NekoPluginBase,
    Ok,
    PluginSettings,
    SdkError,
    SettingsField,
    lifecycle,
    neko_plugin,
    plugin_entry,
    ui,
)

from .analysis_nodes import (
    DEFAULT_BASE_URL,
    DEFAULT_FALLBACK_MODELS,
    DEFAULT_MAX_ARTICLE_CHARS,
    DEFAULT_MODEL,
    DEFAULT_MODEL_LIST_PATH,
    DEFAULT_TEMPERATURE,
    DEFAULT_TIMEOUT_SECONDS,
    WriterAnalysisError,
    WriterPowerAnalysisService,
)
from .platform_store import PlatformPresetStore
from .task_queue import AnalysisTaskQueue

PUSH_FIELD_MAX_CHARS = 4000
PUSH_HIGHLIGHTS_MAX_CHARS = 2500
PUSH_IMPROVEMENTS_MAX_CHARS = 5000


def _emit_node_log(logger: Any, event: str, node: str, **metadata: Any) -> None:
    """Write a lifecycle node log without leaking sensitive values."""

    parts = []
    for key, value in metadata.items():
        if value is None:
            continue
        if any(marker in key.lower() for marker in ("api_key", "token", "secret", "authorization")):
            value = "<redacted>"
        parts.append(f"{key}={value}")
    suffix = " ".join(parts)
    message = f"[writer_power_analysis] {event} name={node}"
    if suffix:
        message = f"{message} {suffix}"
    try:
        logger.info(message)
    except Exception:  # noqa: BLE001 - host logger implementations can fail unexpectedly.
        return


def _as_text(value: Any, default: str = "") -> str:
    """Convert plugin result values to compact text for proactive context."""

    if value is None:
        return default
    text = value if isinstance(value, str) else str(value)
    text = text.strip()
    return text or default


def _format_improvements(value: Any) -> str:
    """Render model improvement suggestions as a stable numbered list."""

    if isinstance(value, list):
        lines: list[str] = []
        for index, item in enumerate(value, start=1):
            if isinstance(item, dict):
                text = _as_text(item.get("text") or item.get("desc") or item.get("description"))
            else:
                text = _as_text(item)
            if text:
                lines.append(f"{index}. {text}")
        return "\n".join(lines) if lines else "模型未返回改进指导。"
    return _as_text(value, "模型未返回改进指导。")


def _format_sentence_highlights(value: Any) -> tuple[str, int]:
    """Render extracted sentence highlights for Neko's proactive response."""

    if not isinstance(value, list):
        return "模型未提取到可推送的文句高光。", 0

    lines: list[str] = []
    seen: set[str] = set()
    for item in value:
        if isinstance(item, dict):
            content = _as_text(item.get("content"))
            reason = _as_text(item.get("reason"))
        else:
            content = _as_text(item)
            reason = ""
        normalized_content = "".join(content.split())
        if not normalized_content or normalized_content in seen:
            continue
        seen.add(normalized_content)
        index = len(lines) + 1
        if reason:
            lines.append(f"{index}. “{content}”\n   鉴赏：{reason}")
        else:
            lines.append(f"{index}. “{content}”")

    return ("\n".join(lines) if lines else "模型未提取到可推送的文句高光。", len(lines))


@neko_plugin
class WriterPowerAnalysisPlugin(NekoPluginBase):
    """Ink Battles writer power analysis as a N.E.K.O plugin."""

    class Settings(PluginSettings):
        """Plugin configuration exposed through N.E.K.O config."""

        model_config: ClassVar[dict[str, str]] = {"toml_section": "writer_power_analysis"}

        base_url: str = SettingsField(DEFAULT_BASE_URL, hot=True, description="OpenAI-compatible base URL")
        api_key: str = SettingsField("", hot=True, description="API key; leave empty and pass per call if preferred")
        model_list_path: str = SettingsField(DEFAULT_MODEL_LIST_PATH, hot=True, description="Model list path or absolute URL")
        default_model: str = SettingsField(DEFAULT_MODEL, hot=True, description="Default model id")
        fallback_models: list[str] = SettingsField(
            list(DEFAULT_FALLBACK_MODELS),
            hot=True,
            description="Fallback model ids when the selected model is unavailable",
        )
        timeout_seconds: float = SettingsField(DEFAULT_TIMEOUT_SECONDS, hot=True, ge=5, le=300, description="Upstream request timeout")
        max_article_chars: int = SettingsField(DEFAULT_MAX_ARTICLE_CHARS, hot=True, ge=1000, le=1_000_000, description="Max input characters")
        temperature: float = SettingsField(DEFAULT_TEMPERATURE, hot=True, ge=0, le=2, description="Model temperature")
        json_mode: bool = SettingsField(False, hot=True, description="Send OpenAI response_format=json_object when possible")

    def __init__(self, ctx: Any):
        super().__init__(ctx)
        self.logger = ctx.logger
        self._cfg: dict[str, Any] = {}
        self._service = WriterPowerAnalysisService(self.logger)
        self._preset_store = PlatformPresetStore(self.data_path())
        self._task_queue = AnalysisTaskQueue(self.logger)
        self.register_static_ui("static", cache_control="no-cache")

    @lifecycle(id="startup")
    async def startup(self, **_):
        """Load config when the plugin starts."""

        await self._reload_config()
        status = self._service.status(self._cfg)
        self.logger.info(
            f"WriterPowerAnalysisPlugin ready: base_url={status['base_url']}, "
            f"default_model={status['default_model']}, has_api_key={status['has_api_key']}"
        )
        return Ok({"status": "ready", "default_model": status["default_model"]})

    @lifecycle(id="shutdown")
    async def shutdown(self, **_):
        """Cancel pending tasks and report graceful shutdown."""

        _emit_node_log(self.logger, "node.enter", "plugin.shutdown")
        await self._task_queue.shutdown()
        _emit_node_log(self.logger, "node.exit", "plugin.shutdown", status="ok")
        return Ok({"status": "stopped"})

    @lifecycle(id="config_change")
    async def on_config_change(self, **_):
        """Hot-reload plugin config."""

        await self._reload_config()
        return Ok({"status": "reloaded"})

    @ui.context(id="writer_power_analysis", title="文本分析")
    async def get_dashboard_ui_context(self) -> dict[str, Any]:
        """Provide panel state."""
        status = self._service.status(self._cfg)
        return {
            "status": status.get("status", "unknown"),
            "default_model": status.get("default_model", ""),
            "base_url": status.get("base_url", ""),
            "model_list_path": status.get("model_list_url", ""),
            "has_api_key": status.get("has_api_key", False),
        }

    async def _reload_config(self) -> None:
        """Read this plugin's TOML section."""

        _emit_node_log(self.logger, "node.enter", "config.reload")
        try:
            cfg = await self.config.dump(timeout=5.0)
            cfg = cfg if isinstance(cfg, dict) else {}
            section = cfg.get("writer_power_analysis")
            self._cfg = section if isinstance(section, dict) else {}
        except Exception as exc:
            _emit_node_log(self.logger, "node.exit", "config.reload", status="error", error_type=type(exc).__name__, error=exc)
            raise
        _emit_node_log(self.logger, "node.exit", "config.reload", status="ok", keys=len(self._cfg))

    @ui.action(id="status", label="刷新状态", group="info", order=10, refresh_context=True)
    @plugin_entry(
        id="status",
        name="文本分析状态",
        description="查看文本分析插件配置状态。不会返回 API key。",
        llm_result_fields=["status", "base_url", "default_model", "has_api_key"],
    )
    async def status(self, **kwargs):
        """Return non-secret plugin status."""

        try:
            return Ok(self._service.status(self._cfg, **kwargs))
        except Exception as exc:  # noqa: BLE001 - plugin entrypoints must return SDK errors.
            self.logger.warning(f"Writer analysis status node failed: {type(exc).__name__}")
            return Err(SdkError(f"读取状态失败: {exc}"))

    @ui.action(id="list_models", label="刷新模型列表", group="model", order=20, refresh_context=True)
    @plugin_entry(
        id="list_models",
        name="获取作家分析模型列表",
        description="从配置的模型接口获取可用模型列表；未配置 API key 时返回内置候选列表。",
        input_schema={
            "type": "object",
            "properties": {
                "api_key": {"type": "string", "description": "临时 API key，优先于插件配置"},
                "base_url": {"type": "string", "description": "临时 base URL"},
                "model_list_path": {"type": "string", "description": "模型列表路径，默认 /v1/models"},
            },
        },
        llm_result_fields=["models", "count", "source"],
        timeout=30.0,
    )
    async def list_models(self, **kwargs):
        """Fetch available model ids from the configured model-list endpoint."""

        try:
            return Ok(await self._service.list_models(self._cfg, **kwargs))
        except WriterAnalysisError as exc:
            return Err(SdkError(str(exc)))
        except Exception as exc:  # noqa: BLE001 - plugin entrypoints must return SDK errors.
            self.logger.warning(f"Writer analysis list_models failed: {type(exc).__name__}")
            return Err(SdkError(f"获取模型列表失败: {type(exc).__name__}: {exc}"))

    @ui.action(id="get_neko_model", label="获取 Neko 模型信息", group="model", order=30, refresh_context=True)
    @plugin_entry(
        id="get_neko_model",
        name="获取 Neko 当前模型",
        description="返回 N.E.K.O 当前使用的对话模型信息，用于前端「使用 Neko 当前模型」选项。",
        llm_result_fields=["available", "model", "base_url", "has_api_key"],
        timeout=10.0,
    )
    async def get_neko_model(self, **_):
        """Return Neko's current conversation model configuration."""

        try:
            return Ok(self._service.get_neko_model())
        except Exception as exc:  # noqa: BLE001 - plugin entrypoints must return SDK errors.
            return Err(SdkError(f"获取 Neko 模型信息失败: {exc}"))

    @ui.action(id="load_platform_presets", label="加载平台预设", group="config", order=40, refresh_context=True)
    @plugin_entry(
        id="load_platform_presets",
        name="加载平台预设",
        description="从磁盘 JSON 文件加载所有已保存的平台预设。",
        llm_result_fields=["presets"],
        timeout=5.0,
    )
    async def load_platform_presets(self, **_):
        try:
            presets = await self._preset_store.load_all()
            return Ok({"presets": presets})
        except Exception as exc:  # noqa: BLE001 - plugin entrypoints must return SDK errors.
            return Err(SdkError(f"加载预设失败: {exc}"))

    @ui.action(id="save_platform_preset", label="保存平台预设", group="config", order=50, refresh_context=True)
    @plugin_entry(
        id="save_platform_preset",
        name="保存平台预设",
        description="保存一个平台预设到磁盘 JSON 文件。apiKey 会加密存储在本地。",
        input_schema={
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "label": {"type": "string"},
                "baseUrl": {"type": "string"},
                "modelListPath": {"type": "string"},
                "model": {"type": "string"},
                "apiKey": {"type": "string", "description": "API key，会随预设一起保存"},
            },
            "required": ["id", "baseUrl"],
        },
        llm_result_fields=["presets"],
        timeout=5.0,
    )
    async def save_platform_preset(self, **kwargs):
        try:
            presets = await self._preset_store.save(dict(kwargs))
            return Ok({"presets": presets})
        except Exception as exc:  # noqa: BLE001 - plugin entrypoints must return SDK errors.
            return Err(SdkError(f"保存预设失败: {exc}"))

    @ui.action(id="delete_platform_preset", label="删除平台预设", group="config", order=60, refresh_context=True)
    @plugin_entry(
        id="delete_platform_preset",
        name="删除平台预设",
        description="按 id 删除一个已保存的平台预设。",
        input_schema={
            "type": "object",
            "properties": {
                "preset_id": {"type": "string", "description": "要删除的预设 id"},
            },
            "required": ["preset_id"],
        },
        llm_result_fields=["presets"],
        timeout=5.0,
    )
    async def delete_platform_preset(self, preset_id: str = "", **_):
        try:
            presets = await self._preset_store.delete(preset_id)
            return Ok({"presets": presets})
        except Exception as exc:  # noqa: BLE001 - plugin entrypoints must return SDK errors.
            return Err(SdkError(f"删除预设失败: {exc}"))

    @ui.action(id="analyze_text", label="开始战力分析", icon="🔍", group="analyze", order=10, refresh_context=True)
    @plugin_entry(
        id="analyze_text",
        name="文本分析",
        description="提交作品正文到后台分析队列，立即返回 task_id，前端轮询 get_analysis_status 获取结果。",
        input_schema={
            "type": "object",
            "properties": {
                "article_text": {"type": "string", "description": "要分析的作品正文"},
                "mode": {
                    "type": "string",
                    "description": "评分模式，例如 standard、strict、reader、judge、fan、fragment",
                    "default": "standard",
                },
                "model": {"type": "string", "description": "模型 id；留空使用配置 default_model"},
                "api_key": {"type": "string", "description": "临时 API key，优先于插件配置"},
                "base_url": {"type": "string", "description": "临时 base URL"},
                "use_neko_model": {"type": "boolean", "description": "使用 Neko 当前对话模型而非插件独立配置", "default": False},
            },
            "required": ["article_text"],
        },
        llm_result_fields=["task_id", "status"],
        timeout=10.0,
    )
    async def analyze_text(
        self,
        article_text: str,
        mode: str = "standard",
        model: str = "",
        api_key: str = "",
        base_url: str = "",
        use_neko_model: bool = False,
        **kwargs,
    ):
        """Queue an analysis task, return task_id immediately."""

        async def _runner() -> dict[str, Any]:
            return await self._service.analyze(
                self._cfg,
                article_text=article_text,
                mode=mode,
                model=model,
                api_key=api_key,
                base_url=base_url,
                use_neko_model=use_neko_model,
            )

        try:
            target_lanlan = self._resolve_target_lanlan(kwargs)

            def _on_done(task: dict[str, Any]) -> None:
                result = task.get("result")
                if isinstance(result, dict):
                    self._push_analysis_completed(
                        task=task,
                        result=result,
                        target_lanlan=target_lanlan,
                    )

            task_id = await self._task_queue.submit(
                runner=_runner,
                model=model or ("neko" if use_neko_model else "custom"),
                mode=mode,
                article_chars=len(article_text),
                on_done=_on_done,
            )
            return Ok({"task_id": task_id, "status": "queued"})
        except Exception as exc:  # noqa: BLE001 - plugin entrypoints must return SDK errors.
            return Err(SdkError(f"提交分析任务失败: {exc}"))

    @ui.action(id="get_analysis_status", label="查询分析状态", group="analyze", order=20, refresh_context=True)
    @plugin_entry(
        id="get_analysis_status",
        name="查询分析状态",
        description="按 task_id 查询后台分析任务的状态和结果。status: queued | running | done | error。",
        input_schema={
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "analyze_text 返回的 task_id"},
            },
            "required": ["task_id"],
        },
        llm_result_fields=["task_id", "status", "result", "error"],
        timeout=5.0,
    )
    async def get_analysis_status(self, task_id: str = "", **_):
        """Poll for analysis task status / result."""
        task = await self._task_queue.get(task_id)
        if task is None:
            return Err(SdkError(f"未找到任务: {task_id}"))
        return Ok(task)

    @ui.action(id="synthesize_author_style_profile", label="统合作者文风画像", group="analyze", order=30, refresh_context=False)
    @plugin_entry(
        id="synthesize_author_style_profile",
        name="统合作者文风画像",
        description="根据历史作品文风画像样本，调用模型生成作者级文风画像。",
        input_schema={
            "type": "object",
            "properties": {
                "profiles": {"type": "array", "description": "历史作品 articleStyleProfile 数组"},
                "model": {"type": "string", "description": "模型 id；留空使用配置 default_model"},
                "api_key": {"type": "string", "description": "临时 API key，优先于插件配置"},
                "base_url": {"type": "string", "description": "临时 base URL"},
                "model_list_path": {"type": "string", "description": "模型列表路径，默认 /v1/models"},
                "use_neko_model": {"type": "boolean", "description": "使用 Neko 当前对话模型", "default": False},
            },
            "required": ["profiles"],
        },
        llm_result_fields=["sampleCount", "dominantStyle", "dominantGenre", "summary", "source"],
        timeout=180.0,
    )
    async def synthesize_author_style_profile(
        self,
        profiles: list[dict[str, Any]] | None = None,
        model: str = "",
        api_key: str = "",
        base_url: str = "",
        model_list_path: str = "",
        use_neko_model: bool = False,
        **_,
    ):
        """Synthesize an author style profile from historical article style profiles."""

        try:
            result = await self._service.synthesize_author_style_profile(
                self._cfg,
                profiles=profiles or [],
                model=model,
                api_key=api_key,
                base_url=base_url,
                model_list_path=model_list_path,
                use_neko_model=use_neko_model,
            )
            return Ok(result)
        except WriterAnalysisError as exc:
            return Err(SdkError(str(exc)))
        except Exception as exc:  # noqa: BLE001 - plugin entrypoints must return SDK errors.
            self.logger.warning(f"Author style synthesis failed: {exc}")
            return Err(SdkError(f"统合作者文风画像失败: {exc}"))

    def _resolve_target_lanlan(self, kwargs: dict[str, Any]) -> str | None:
        """Best-effort target resolution for proactive push messages."""

        ctx_obj = kwargs.get("_ctx")
        if isinstance(ctx_obj, dict):
            lanlan_name = ctx_obj.get("lanlan_name")
            if isinstance(lanlan_name, str) and lanlan_name.strip():
                return lanlan_name.strip()
        current_lanlan = getattr(self.ctx, "_current_lanlan", None)
        if isinstance(current_lanlan, str) and current_lanlan.strip():
            return current_lanlan.strip()
        return None

    def _push_analysis_completed(
        self,
        *,
        task: dict[str, Any],
        result: dict[str, Any],
        target_lanlan: str | None = None,
    ) -> None:
        """Push analysis completion context to Neko so it can respond naturally."""

        if not hasattr(self.ctx, "push_message"):
            self.logger.warning("Writer analysis completion push skipped: ctx has no push_message")
            return

        analysis = result.get("analysis") if isinstance(result.get("analysis"), dict) else {}
        improvements = _format_improvements(analysis.get("improvements"))
        sentence_highlights, highlight_count = _format_sentence_highlights(analysis.get("excellentSentences"))
        overall_assessment = _as_text(analysis.get("overallAssessment"), "模型未返回综合评价。")

        if len(sentence_highlights) > PUSH_HIGHLIGHTS_MAX_CHARS:
            sentence_highlights = sentence_highlights[:PUSH_HIGHLIGHTS_MAX_CHARS] + "\n[...文句高光过长，已截断...]"
        if len(overall_assessment) > PUSH_FIELD_MAX_CHARS:
            overall_assessment = overall_assessment[:PUSH_FIELD_MAX_CHARS] + "\n[...综合评价过长，已截断...]"
        if len(improvements) > PUSH_IMPROVEMENTS_MAX_CHARS:
            improvements = improvements[:PUSH_IMPROVEMENTS_MAX_CHARS] + "\n[...改进指导过长，已截断...]"

        message = (
            "请你根据下面三个模块，像 Neko 一样对用户作出自然回应："
            "结合文句高光、综合评价和改进指导给出有帮助的反馈；"
            "可以把文句高光原句原文复述，不要说你看不到报告。\n\n"
            f"【文句高光】\n{sentence_highlights}\n\n"
            f"【综合评价】\n{overall_assessment}\n\n"
            f"【改进指导】\n{improvements}"
        )

        metadata = {
            "event_type": "writer_analysis_completed",
            "task_id": task.get("task_id"),
            "mode": task.get("mode"),
            "article_chars": task.get("article_chars"),
            "model": result.get("model") or task.get("model"),
            "overallScore": result.get("overallScore"),
            "ratingTag": result.get("ratingTag"),
            "title": analysis.get("title"),
            "highlight_count": highlight_count,
        }
        if target_lanlan:
            metadata["target_lanlan"] = target_lanlan

        self.ctx.push_message(
            source="writer_power_analysis",
            visibility=[],
            ai_behavior="respond",
            parts=[{"type": "text", "text": message}],
            priority=6,
            metadata=metadata,
            target_lanlan=target_lanlan,
        )
        self.logger.info(
            f"Writer analysis completion pushed: task_id={task.get('task_id')}, "
            f"target_lanlan={target_lanlan or ''}, highlight_count={highlight_count}"
        )
