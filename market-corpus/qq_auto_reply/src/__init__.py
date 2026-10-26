from __future__ import annotations

# 先把 vendored lib/ 加入 sys.path（副作用式导入），使 utils.connection 等本地依赖可解析，
# 且不触发 E402 的“import 不在顶部”。
from . import _lib_bootstrap  # isort: skip

import asyncio
import json
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Optional

from plugin.plugins.qq_auto_reply.backlog_store import QQBacklogStore
from plugin.sdk.plugin import Err, NekoPluginBase, Ok, SdkError, lifecycle, neko_plugin, plugin_entry, tr, ui

# OneBotConnector 仅作类型注解使用（from __future__ import annotations 下为惰性求值），
# 运行时无需导入 —— 连接由 connector_seam 在方法内惰性解析并构建，见 _make_qq_connection。
if TYPE_CHECKING:
    from .connector_seam import OneBotConnector

try:
    from utils.tts.native_voice_registry import get_active_realtime_native_provider_for_ui
except (ImportError, ModuleNotFoundError):
    get_active_realtime_native_provider_for_ui = None
try:
    from utils.tts.providers.gemini import normalize_gemini_tts_voice
except (ImportError, ModuleNotFoundError):
    normalize_gemini_tts_voice = None
try:
    from utils.voice_clone import (
        MimoVoiceCloneClient,
        MimoVoiceCloneError,
        MinimaxVoiceCloneClient,
        MinimaxVoiceCloneError,
    )
except (ImportError, ModuleNotFoundError):
    MimoVoiceCloneClient = MimoVoiceCloneError = MinimaxVoiceCloneClient = MinimaxVoiceCloneError = None
try:
    from utils.voice_config import read_legacy_voice_id
except (ImportError, ModuleNotFoundError):
    read_legacy_voice_id = None

from . import settings_schema
from .attention_gate_service import QQAttentionGateService
from .attention_service import QQAttentionService
from .backlog_models import QQBacklogMessage as QQBacklogMessage
from .backlog_service import QQBacklogService
from .config_store import QQAutoReplyConfigStore
from .dashboard_service import QQDashboardService
from .deploy_service import QQDeployService
from .display_name_service import QQDisplayNameService
from .enrichment import QQMessageEnricher
from .feedback_classifier import QQFeedbackClassifier as QQFeedbackClassifier
from .group_permission import DEFAULT_GROUP_LEVEL, GroupPermissionManager
from .handler_runtime_service import QQHandlerRuntimeService
from .memory_bridge import QQMemoryBridge
from .memory_tool_service import QQMemoryToolService
from .message_dispatcher import QQMessageDispatcher
from .napcat_service import QQNapcatService
from .outbound_guard_service import QQOutboundGuardService
from .permission import PermissionManager
from .plugin_tool_followup_service import QQPluginToolFollowupService
from .plugin_tool_service import (
    PLUGIN_TOOL_MAX_MOUNTED,
    QQPluginToolService,
    channel_allows_plugin_tools,
    emit_bridge_log,
)
from .proactive_initiative_service import QQProactiveInitiativeService
from .prompt_builder import QQPromptBuilder
from .prompting import QQAutoReplyPromptingMixin
from .relay_service import QQRelayService
from .repeat_echo_service import QQRepeatEchoService
from .reply_buffer_service import QQReplyBufferService
from .reply_context_node import QQReplyContextNode
from .reply_decision_node import QQReplyDecisionNode
from .reply_delivery_node import QQReplyDeliveryNode
from .reply_generation_service import QQReplyGenerationService
from .reply_model_node import QQReplyModelNode
from .reply_pipeline import QQReplyPipelineRunner
from .reply_postprocess_node import QQReplyPostprocessNode
from .reply_relay_node import QQReplyRelayNode
from .runtime_ops_service import QQProactiveMessageService, QQRuntimeOpsService
from .runtime_service import QQRuntimeService
from .runtime_transition import runtime_transition_guard
from .session import QQAutoReplySessionMixin
from .session_bootstrap_service import QQSessionBootstrapService
from .session_handoff_service import QQSessionHandoffService
from .session_instruction_service import (
    QQSessionInstructionService,
    resolve_prompt_override,
)
from .session_memory_service import QQSessionMemoryService
from .session_runtime_service import QQSessionRuntimeService
from .settings_service import QQSettingsService
from .targets import QQAutoReplyTargetsMixin
from .targets import QQAutoReplyValidationError as QQAutoReplyValidationError
from .time_context import build_time_context
from .voice_reply_service import QQVoiceReplyService

# 本地依赖（vendored lib/）由 _lib_bootstrap 在模块加载时放入 sys.path；此处记录目录，
# 保持对 _lib_bootstrap 的引用（--ignore-noqa 下不可用 noqa，须显式引用）。
_LIB_DIR = _lib_bootstrap.lib_dir


def build_open_ui_payload(*, plugin_id: str, available: bool, i18n=None) -> dict[str, Any]:
    path = f"/plugin/{plugin_id}/ui/" if available else ""
    message_key = "ui.open_path.message" if available else "ui.unavailable.message"
    default_message = "UI 已注册" if available else "UI 未注册"
    message = i18n.t(message_key, default=default_message) if i18n else default_message
    return {
        "available": available,
        "path": path,
        "message": message,
    }


#: 合法的连接方式。三条接入流程各自对应其中一段：napcat/napcat_forward 走 OneBot，
#: open_platform 走官方 Bot API。schema 与校验都引用它，别再各写一份内联元组。
#: 真相在 settings_schema 的 `qq_connection_mode` 那条 enum。
CONNECTION_MODES: tuple[str, ...] = tuple(
    settings_schema.BY_KEY["qq_connection_mode"].enum or ()
)

#: 表情包自动描述用的提示词。
#:
#: 和引用回复那句"描述这张图片"不同：这句描述**是要给模型自己以后挑图用的**
#: （它会进 system prompt 的表情包目录），所以要素是"画面 + 情绪 + 什么场合发"，
#: 而不是客观转写。限定 30 字是因为它要进提示词，太长会挤占上下文。
STICKER_VLM_PROMPT = (
    "这是一张聊天用的表情包图片。请用一句简短中文描述它的画面和情绪，"
    "让人只看这句就知道什么场合适合发它（不超过30字；直接给描述，不要引号、不要解释）"
)

#: 免费文字端的域名特征：命中它才需要按免费线的规矩发请求
#: （见 `QQAutoReplyPlugin._vlm_free_route_system_prompt`）。
FREE_ROUTE_HOST_HINT = "lanlan"


@neko_plugin
class QQAutoReplyPlugin(QQAutoReplySessionMixin, QQAutoReplyPromptingMixin, QQAutoReplyTargetsMixin, NekoPluginBase):
    SESSION_IDLE_TIMEOUT_SECONDS = 300
    SESSION_SWEEP_INTERVAL_SECONDS = 30
    LOG_BUFFER_SIZE = 500

    def __init__(self, ctx):
        super().__init__(ctx)
        self.file_logger = self.enable_file_logging(log_level="INFO")
        self.logger = self.file_logger
        # 内存日志缓冲区（供前端运行日志页读取）
        import collections
        import time as _time
        self._log_buffer: collections.deque = collections.deque(maxlen=self.LOG_BUFFER_SIZE)
        self._last_log_push_at = 0.0
        self._log_push_throttle_seconds = 1.5
        self._last_status_push_at = 0.0
        self._status_push_throttle_seconds = 2.0
        def _emit(level: str, msg: str) -> None:
            try:
                ts = _time.strftime("%H:%M:%S")
                self._log_buffer.append(f"{ts} [{level}] {msg}")
            except Exception:
                pass
            self._maybe_push_log_event()
        self._emit_log = _emit
        self.config_store = QQAutoReplyConfigStore(self.data_path())
        self._qq_settings: dict[str, Any] = self.config_store.default_config()
        self.backlog_store = self._create_backlog_store_from_settings(self._qq_settings)
        self.settings_service = QQSettingsService(self)
        self.runtime_service = QQRuntimeService(self)
        self.dashboard_service = QQDashboardService(self)
        self.napcat_service = QQNapcatService(
            get_settings=lambda: self._qq_settings,
            get_qq_client=lambda: self.qq_client,
            config_dir=self.config_dir,
            logger=self.logger,
            emit_log=self._emit_log,
        )
        self.deploy_service = QQDeployService(self)
        self.backlog_service = QQBacklogService(self)
        self.attention_service = QQAttentionService(self)
        self.prompt_builder = QQPromptBuilder(self)
        self.memory_bridge = QQMemoryBridge(self)
        self.display_name_service = QQDisplayNameService(self)
        self.memory_tool_service = QQMemoryToolService(self)
        # 插件工具桥（只在开放平台启用）：把别的插件的能力按分级挂到 QQ 会话上。
        self.plugin_tool_service = QQPluginToolService(self)
        # 异步插件任务的结果回投：她答应过"结果出来告诉你"，这条链路让它成真。
        self.plugin_tool_followup_service = QQPluginToolFollowupService(self)
        self.relay_service = QQRelayService(self)
        self.reply_generation_service = QQReplyGenerationService(self)
        self.reply_decision_node = QQReplyDecisionNode(self)
        self.reply_context_node = QQReplyContextNode(self)
        self.reply_model_node = QQReplyModelNode(self)
        self.reply_postprocess_node = QQReplyPostprocessNode(self)
        self.reply_delivery_node = QQReplyDeliveryNode(self)
        self.reply_buffer_service: Optional[QQReplyBufferService] = None
        self.reply_relay_node = QQReplyRelayNode(self)
        self.reply_pipeline = QQReplyPipelineRunner(self)
        self.voice_reply_service = QQVoiceReplyService(self)
        self.runtime_ops_service = QQRuntimeOpsService(self)
        self.proactive_message_service = QQProactiveMessageService(self)
        # 管理员私聊的「有由头」主动搭话（2026-10-02 使用者选 A）。发送仍走
        # `proactive_message_service`，本服务只负责"什么时候开口"与"由头从哪来"。
        self.proactive_initiative_service = QQProactiveInitiativeService(self)
        self.handler_runtime_service = QQHandlerRuntimeService(self)
        self.message_dispatcher = QQMessageDispatcher(self)
        self.session_bootstrap_service = QQSessionBootstrapService(self)
        self.session_instruction_service = QQSessionInstructionService(self)
        self.session_memory_service = QQSessionMemoryService(self)
        self.session_runtime_service = QQSessionRuntimeService(self)
        self.qq_client: Optional[OneBotConnector] = None
        self.enricher: Optional[QQMessageEnricher] = None
        self.attention_gate_service = QQAttentionGateService(self)
        self.permission_mgr: Optional[PermissionManager] = None
        self.group_permission_mgr: Optional[GroupPermissionManager] = None
        self._running = False
        self._message_task: Optional[asyncio.Task] = None
        self._session_housekeeping_task: Optional[asyncio.Task] = None
        self._group_digest_task: Optional[asyncio.Task] = None
        self._trust_migration_task: Optional[asyncio.Task] = None
        self._identity_scope_task: Optional[asyncio.Task] = None
        # 只有在存量 trust 池成功推给 memory_server 之后才开始上报
        # speaker_tier / speaker_activity_events。纵深防御第一层，服务端的
        # legacy_barriers 是第二层。
        self.trust_ready: asyncio.Event = asyncio.Event()
        self._handler_tasks: set[asyncio.Task] = set()
        self._user_sessions: dict[str, dict[str, Any]] = {}
        self._session_locks: dict[str, asyncio.Lock] = {}
        self._session_locks_guard = asyncio.Lock()
        self._message_concurrency = asyncio.Semaphore(3)
        self._max_concurrent_messages = 3
        self._ai_connect_timeout_seconds = 10.0
        self._ai_turn_timeout_seconds = 60.0
        self._handler_shutdown_timeout_seconds = 10.0
        self._normal_relay_probability = 0.1
        self._admin_qq: Optional[str] = None
        # `_strategy_mode` 已随「回复策略」删除（2026-09-27）：它只有一个取值，
        # 留着只会让每个读取点都写一句恒真的分支。见 docs/SESSION-HANDOFF.md §24。
        # NapCat 进程/启动错误状态由连接层 napcat_service 自持，插件不再持有。
        self._proactive_task: Optional[asyncio.Task] = None
        # `_last_proactive_enabled` / `_last_proactive_send_at` / `_last_proactive_greeting_at`
        # 三个字段是更早那版"主动问候"的残骸：全仓库只写不读（2026-10-02 清掉）。
        # 主动搭话的真实状态由 `proactive_initiative_service` 自持并落盘。
        self._backlog_summary_threshold = 10
        self._backlog_notify_cooldown_seconds = 900
        self._backlog_issue_notify_threshold = 1
        self._relay_backlog_items: list[dict[str, Any]] = []
        self._recent_pipeline_traces: list[dict[str, Any]] = []
        self._poke_timestamps: dict[str, list[float]] = {}  # user_id → 最近回戳时间戳列表（5分钟窗口，每人最多2次）
        #: group_id → [(timestamp, poker_id)]：短窗内戳她的**不同人**。
        #: 现在**只用于留痕**（日志里写一句"戳一戳风暴（N 人）"）——
        #: 使用者 2026-09-27 定下「戳戳风暴就不需要回复了，只需要跟戳」，
        #: 所以不管几个人戳都只跟戳、不回话，风暴本身不再改变行为。
        self._poke_storm: dict[str, list[tuple[float, str]]] = {}

    def _create_backlog_store_from_settings(self, settings: dict[str, Any] | None) -> QQBacklogStore:
        return QQBacklogStore(
            self.data_path(),
            retention_limit=int((settings or {}).get("backlog_retention_limit", 200) or 200),
        )

    def _make_qq_connection(self):
        # 延迟导入：连接构造器依赖的模块较重（顶层 import 会拖慢插件进程启动握手），
        # 而连接对象只在真正启动自动回复时才需要。连接本身由连接器工厂构建；
        # VLM/STT 描述器不注入连接器——增强是插件业务，由 QQMessageEnricher 在
        # _ensure_qq_client_initialized 里绑定。
        #
        # 解析走 connector_seam：宿主第一方包 utils.connection.onebot 优先，拿不到时
        # 回退 _vendor 里的副本（出处见 _vendor/connection_onebot/PROVENANCE.md）。
        from .connector_seam import (
            CONNECTOR_MODULE,
            CONNECTOR_SOURCE,
            create_onebot_connection,
        )

        # 两路都发：`_emit_log` 只写内存缓冲 + 推 SSE（插件 UI 的运行日志页），
        # 不进控制台/日志文件；`logger` 才是能在宿主控制台和插件日志文件里看到的。
        # 排查"装上去到底连的哪个连接器"时要的是后者。
        source_msg = f"[QQ] 连接器来源: {CONNECTOR_SOURCE} ({CONNECTOR_MODULE})"
        self._emit_log("INFO", source_msg)
        self.logger.info(source_msg)
        connection = create_onebot_connection(
            self._qq_settings,
            logger=self.logger,
            emit_log=self._emit_log,
        )
        # 富媒体走谁：宿主适配器（连接对象自带 upload_image）还是内置副本。
        # 与上面那行一样两路都发 —— 排查"装上去到底跑哪份"时看的是日志文件那份。
        from . import media_seam

        media_msg = f"[QQ] 富媒体来源: {media_seam.describe(connection)}"
        self._emit_log("INFO", media_msg)
        self.logger.info(media_msg)
        return connection

    # ── UI SSE 事件推送（#2822 通道）──────────────────────────

    def _spawn_push_ui_event(self, msg_type: str, text: str = "", data: Any = None) -> None:
        """把一次 SSE 推送放进事件循环，fire-and-forget——绝不打断消息管线。

        所有调用点都在插件事件循环内（消息处理/生命周期协程），get_running_loop
        可用；推失败静默（SSE 是尽力而为，前端有兜底轮询）。``data`` 透传给订阅者。
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(self._push_ui_event(msg_type, text, data=data))

    async def _push_ui_event(self, msg_type: str, text: str = "", data: Any = None) -> None:
        """经 /ui-api/push 向插件 UI 的 SSE 客户端广播一条事件。

        用进程级回环 HTTP 单例 + 固定插件服务基址；只发本插件的 channel。
        ``data`` 可选结构化数据（如 qq_message 的 qq_inbound），透传给 SSE 订阅者。
        """
        try:
            from config import USER_PLUGIN_BASE
            from utils.internal_http_client import get_internal_http_client

            client = get_internal_http_client()
            payload: dict[str, Any] = {"type": msg_type, "text": str(text or msg_type)[:200]}
            if isinstance(data, dict):
                payload["data"] = data
            await client.post(
                f"{USER_PLUGIN_BASE}/plugin/{self.plugin_id}/ui-api/push",
                json=payload,
            )
        except Exception:
            pass

    def _maybe_push_log_event(self) -> None:
        """日志写入后的节流推送（1.5s 内最多一次 {"type":"logs"}），避免每条日志一条 SSE。"""
        try:
            import time as _t
            now = _t.time()
            if now - self._last_log_push_at < self._log_push_throttle_seconds:
                return
            self._last_log_push_at = now
            self._spawn_push_ui_event("logs")
        except Exception:
            pass

    def _maybe_push_status_event(self) -> None:
        """高频状态变更的节流推送（2s 内最多一次 {"type":"status"}）。

        消息活动/注意力离散事件/缓冲变更都会调这里；运行翻转这种低频但重要
        的状态用直接 ``_spawn_push_ui_event("status")``，不走节流。
        """
        try:
            import time as _t
            now = _t.time()
            if now - self._last_status_push_at < self._status_push_throttle_seconds:
                return
            self._last_status_push_at = now
            self._spawn_push_ui_event("status")
        except Exception:
            pass

    async def _transcribe_voice(self, audio_base64: str = "", *, audio_url: str = "") -> str:
        """语音转文字：优先本地 STT，其次云端 OpenAI/Qwen。audio_url 用于 Qwen。"""
        try:
            import base64 as b64

            import httpx
            from utils.config_manager import get_config_manager

            core_config = get_config_manager().get_core_config() or {}
            audio_bytes = b64.b64decode(audio_base64) if audio_base64 else b""
            # 如果没有 base64 但有 URL，下载音频
            if not audio_bytes and audio_url:
                try:
                    async with httpx.AsyncClient(timeout=30.0, proxy=None, trust_env=False) as cl:
                        dl = await cl.get(audio_url)
                        if dl.status_code == 200:
                            audio_bytes = dl.content
                except Exception:
                    pass

            stt_filename = "voice.mp3"
            stt_mime = "audio/mp3"

            # ── 本地 STT（优先独立配置 local_stt_url，其次 tts_custom base_url 推导）──
            if audio_bytes:
                try:
                    amr_detected = audio_bytes and (audio_bytes[:6] == b"#!AMR\n" or audio_bytes[:9].startswith(b"#!AMR-W"))
                    stt_filename = "voice.amr" if amr_detected else "voice.mp3"
                    stt_mime = "audio/amr" if amr_detected else "audio/mp3"
                    # 优先使用 qq_settings 中的 local_stt_url
                    local_stt_url = str((self._qq_settings or {}).get("local_stt_url", "") or "").strip()
                    if not local_stt_url:
                        # 回退：tts_custom base_url 推导
                        tts_config = get_config_manager().get_model_api_config("tts_custom")
                        local_base = str(tts_config.get("base_url") or "").strip()
                        _is_ws = local_base.startswith("ws://") or local_base.startswith("wss://")
                        _is_http = local_base.startswith("http://") or local_base.startswith("https://")
                        if local_base and (_is_ws or _is_http):
                            http_base = local_base.replace("ws://", "http://").replace("wss://", "https://")
                            local_stt_url = http_base.rstrip("/") + "/v1/audio/transcriptions"
                    if local_stt_url:
                        async with httpx.AsyncClient(timeout=30.0, proxy=None, trust_env=False) as client:
                            resp = await client.post(
                                local_stt_url,
                                files={"file": (stt_filename, audio_bytes, stt_mime)},
                                data={"model": "whisper-1", "language": "zh"},
                            )
                            if resp.status_code == 200:
                                text = str(resp.json().get("text", "") or "").strip()
                                if text:
                                    self._emit_log("INFO", f"[Voice] 本地STT完成: {text[:40]}")
                                    return text
                            self._emit_log("DEBUG", f"[Voice] 本地STT: {resp.status_code}")
                except Exception:
                    pass

            # ── OpenAI Whisper ──
            openai_key = str(core_config.get("ASSIST_API_KEY_OPENAI") or "").strip()
            if openai_key and audio_bytes:
                async with httpx.AsyncClient(timeout=30.0, proxy=None, trust_env=False) as client:
                    resp = await client.post(
                        "https://api.openai.com/v1/audio/transcriptions",
                        headers={"Authorization": f"Bearer {openai_key}"},
                        files={"file": (stt_filename, audio_bytes, stt_mime)},
                        data={"model": "whisper-1", "language": "zh"},
                    )
                    if resp.status_code == 200:
                        return str(resp.json().get("text", "") or "").strip()
                    self._emit_log("DEBUG", f"[Voice] OpenAI转录: {resp.status_code}")

            # ── Qwen DashScope (SenseVoice) 同步模式 ──
            import json as _json
            qwen_key = str(core_config.get("ASSIST_API_KEY_QWEN") or "").strip()
            if qwen_key and audio_bytes:
                amr_detected = audio_bytes and (audio_bytes[:6] == b"#!AMR\n" or audio_bytes[:9].startswith(b"#!AMR-W"))
                if amr_detected:
                    self._emit_log("DEBUG", f"[Voice] 检测到AMR: magic={audio_bytes[:9]!r}")
                self._emit_log("DEBUG", f"[Voice] Qwen同步转录: {len(audio_bytes)} bytes")
                mime = "audio/amr-wb" if (amr_detected and audio_bytes[:9].startswith(b"#!AMR-W")) else ("audio/amr" if amr_detected else "audio/mpeg")
                data_uri = f"data:{mime};base64,{b64.b64encode(audio_bytes).decode()}"
                async with httpx.AsyncClient(timeout=30.0, proxy=None, trust_env=False) as client:
                    submit_resp = await client.post(
                        "https://dashscope.aliyuncs.com/api/v1/services/audio/asr/transcription",
                        headers={"Authorization": f"Bearer {qwen_key}"},
                        json={
                            "model": "sensevoice-v1",
                            "input": {"file_urls": [data_uri]},
                        },
                    )
                    if submit_resp.status_code != 200:
                        try:
                            err = submit_resp.json()
                            self._emit_log("DEBUG", f"[Voice] Qwen同步转录失败: {submit_resp.status_code} code={err.get('code','?')} msg={err.get('message','?')}")
                        except Exception:
                            self._emit_log("DEBUG", f"[Voice] Qwen同步转录失败: {submit_resp.status_code} {submit_resp.text[:200]}")
                    else:
                        result = submit_resp.json()
                        output = result.get("output") or {}
                        results = output.get("results") or []
                        text = ""
                        for r in results:
                            transcripts = r.get("transcripts") or []
                            trans_url = r.get("transcription_url") or ""
                            if not transcripts and trans_url:
                                try:
                                    async with httpx.AsyncClient(timeout=15.0, proxy=None, trust_env=False) as dl:
                                        dl_resp = await dl.get(trans_url)
                                    if dl_resp.status_code == 200:
                                        trans_data = dl_resp.json()
                                        transcripts = trans_data.get("transcripts") or []
                                        props = trans_data.get("properties") or {}
                                        if props:
                                            dur = props.get("original_duration_in_milliseconds", 0)
                                            fmt = props.get("audio_format", "?")
                                            sr = props.get("original_sampling_rate", 0)
                                            self._emit_log("DEBUG", f"[Voice] Qwen音频属性: {fmt} {sr}Hz {dur}ms")
                                    else:
                                        self._emit_log("DEBUG", f"[Voice] Qwen下载转录失败: status={dl_resp.status_code}")
                                except Exception as e:
                                    self._emit_log("DEBUG", f"[Voice] Qwen下载转录异常: {type(e).__name__}: {e}")
                            if transcripts:
                                import re as _re
                                for t in transcripts:
                                    raw_text = str(t.get("text", "") or "").strip()
                                    if raw_text:
                                        cleaned = _re.sub(r'<\|/?\w+\|>', '', raw_text).strip()
                                        text += cleaned
                            else:
                                text += str(r.get("transcript", "") or r.get("text", "") or "").strip()
                            result_text = text.strip()
                            if result_text:
                                self._emit_log("INFO", f"[Voice] Qwen转录完成: {result_text[:80]}")
                            else:
                                self._emit_log("DEBUG", f"[Voice] Qwen转录成功但无文字, full_output={_json.dumps(output, ensure_ascii=False)[:2000]}")
                            return result_text
            return ""
        except Exception:
            return ""

    def _free_route_system_prompt(self, model_config: dict[str, Any]) -> str:
        """免费线上发给 LLM 的请求要带的 system：**本体自己的角色人设**，否则 400。

        免费文字端（`www.lanlan.tech/text/v1`）对"看起来不是 Lanlan 客户端"的请求
        直接回 400 `Invalid request: you are not using Lanlan. STOP ABUSE THE API.`
        ——插件里所有**自己拼消息**的 LLM 调用都撞在它上面，而且撞得很安静
        （`except` 里吞掉、或只留一条 `[VLM] … 看图失败`），表现为"功能点了没反应"。
        同进程里聊天会话那条路没事：本体每轮都带人设。

        实测的分界线**不是**进程、模型槽、streaming、key、UA、连接复用或代理，而是
        **请求里带不带本体人设的标志句**（见 docs/SESSION-HANDOFF.md §4.0t）：
        同一张图、同一个 `free-model`、同一个 key，人设原样发过去就 200，去掉就 400。

        这里**逐字复用本体的人设文本**（`config_manager.get_character_data()` 的
        `lanlan_prompt_map[her_name]`，也就是插件建会话时用的那份 `character_prompt`），
        不硬编码那句英文标志句：人设是本体的资产、随本体升级，标志句变了插件跟着变，
        不用二次维护，也避免把一句"咒语"抄进插件里看起来像绕过校验。

        只对免费线加：自配 API（付费 provider / 本地端点）没有这道门，多塞 3k 字符
        人设纯粹是按 token 付费。取不到人设时返回空串 —— 请求照发，失败由调用点
        自己的日志说话。
        """
        base_url = str(model_config.get("base_url") or "").lower()
        if FREE_ROUTE_HOST_HINT not in base_url:
            return ""
        try:
            from utils.config_manager import get_config_manager

            data = get_config_manager().get_character_data()
            master_name = str(data[0] or "")
            her_name = str(data[1] or "")
            persona_map = data[5] if len(data) > 5 else None
            persona = str((persona_map or {}).get(her_name) or "")
            if not persona:
                return ""
            from .session_instruction_service import _apply_role_placeholders

            return _apply_role_placeholders(
                persona, lanlan_name=her_name, master_name=master_name,
            ) or ""
        except Exception as e:
            self.logger.info(f"[FreeRoute] 免费线人设文本取不到（按无 system 继续）: {type(e).__name__}: {e}")
            return ""

    async def _vlm_describe_locator(self, locator: str, *, prompt: str, max_tokens: int = 60) -> str:
        """对一张图（本地路径或 http(s) URL）跑一次 VLM，返回文本；失败返回 ""。

        **插件里"看图"的地方都走这一条**（conversation 模型配置 → 图片压成 JPEG b64
        → create_chat_llm_async）。刻意收成一个函数：以前只有引用回复的图走这条路，
        表情包自动描述再抄一份的话，两处的模型配置迟早会漂移。

        免费线上这条请求要带上本体人设（见 `_free_route_system_prompt`）：不带的话
        免费端一律 400，功能会"静默没反应"。

        失败一律返回空串（调用方决定怎么兜底），但**会把原因写进日志** —— 这个函数
        服务的都是"用户看得到的功能"（表情包自动描述、引用图描述），静默返回空会让
        用户以为是自己没点到，而真实原因可能是"没配看图模型"或"那个模型不支持看图"。
        """
        import asyncio as _asyncio
        model_config = self._pick_vlm_config()
        if not model_config:
            self.logger.info(
                "[VLM] 没有可用的看图模型配置（conversation 缺 model 或 base_url）")
            return ""
        slot = str(model_config.get("_slot") or "?")
        model = str(model_config.get("model") or "").strip()
        try:
            from utils.llm_client import create_chat_llm_async

            image_b64 = await self._prepare_attachment_image_b64({"path": locator})
            if not image_b64:
                self.logger.info(f"[VLM] 图片预处理失败，跳过描述: {locator}")
                return ""

            system_prompt = self._free_route_system_prompt(model_config)
            messages: list[dict[str, Any]] = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            messages.append({"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}},
                {"type": "text", "text": prompt},
            ]})

            llm = await create_chat_llm_async(
                model=model, base_url=str(model_config.get("base_url") or ""),
                api_key=str(model_config.get("api_key") or ""),
                max_completion_tokens=max_tokens, timeout=15.0,
                provider_type=model_config.get("provider_type"),
            )
            try:
                response = await _asyncio.wait_for(
                    llm.ainvoke(messages),
                    timeout=15.0,
                )
                text = str(getattr(response, "content", "") or "").strip()
                if not text:
                    self.logger.info(f"[VLM] {slot} 槽（{model}）返回空内容")
                return text
            finally:
                aclose = getattr(llm, "aclose", None)
                if callable(aclose):
                    try:
                        await aclose()
                    except Exception:
                        pass
        except Exception as e:
            # 不往上抛（调用点都是"尽力而为"），但把原因留下来 —— 否则用户只看到
            # "描述没出来"，无法判断是模型不支持看图、还是网络/额度问题。
            self.logger.warning(f"[VLM] {slot} 槽（{model}）看图失败: {type(e).__name__}: {e}")
            return ""

    def _pick_vlm_config(self) -> dict[str, Any] | None:
        """"看图"用哪套模型配置：**conversation**（与插件改动前一致）。

        这里一度改成"优先本体的 vision 槽"—— 本体给图片分析专门留了 `VISION_MODEL`
        （`utils/screenshot_utils.py` 用的就是 `aget_model_api_config('vision')`），
        而聊天模型不一定多模态、不支持看图时会静默失败。

        **但使用者明确要求回退**：只把新功能（表情包自动描述）接到既有分析上，
        **不要动既有路径用的模型**。这两个用途共用 `_vlm_describe_locator`，
        换槽会连带改变引用回复图片描述的行为 —— 那不是这次要动的东西。

        **别再"顺手"改回 vision。** 真出问题时有日志可查（见 `_vlm_describe_locator`），
        不需要靠换模型来"修"。
        """
        try:
            from utils.config_manager import get_config_manager

            cfg = get_config_manager().get_model_api_config("conversation")
        except Exception:
            return None
        if str(cfg.get("base_url") or "").strip() and str(cfg.get("model") or "").strip():
            return dict(cfg, _slot="conversation")
        return None

    async def _describe_reply_image(self, image_url: str) -> str:
        """对引用回复中的图片做简短 VLM 描述。

        这是"VLM→文字入上下文"的**同族**做法（KiraAI 及多家都这么做），但不等价：
        上游覆盖全部入站图（含引用链、递归无深度上限）、用专用 `default_vlm` 槽、
        提示词可配置、按 md5 缓存，且支持 `native`（不描述、直接把图给多模态模型）；
        我们固定 `conversation` 槽 + 固定中文提示词、无缓存、引用链深度限 3。
        """
        return await self._vlm_describe_locator(
            image_url, prompt="用简短的中文描述这张图片的内容（不超过20字）",
        )

    def _refresh_admin_qq(self) -> None:
        self._admin_qq = None
        if not self.permission_mgr:
            return
        for user in self.permission_mgr.list_users():
            if user.get("level") == "admin":
                qq = str(user.get("qq") or "").strip()
                if qq:
                    self._admin_qq = qq
                    return

    def _get_reply_mode(self) -> str:
        return self.config_store.normalize_reply_mode((self._qq_settings or {}).get("reply_mode"))

    def _get_voice_output_dir(self) -> Path:
        return self.voice_reply_service.get_voice_output_dir()

    async def _cleanup_voice_output_dir(self, *, max_age_seconds: int = 1800) -> None:
        await self.voice_reply_service.cleanup_voice_output_dir(max_age_seconds=max_age_seconds)

    async def _get_current_voice_id(self) -> str:
        return await self.voice_reply_service.get_current_voice_id()

    async def _synthesize_reply_voice_audio(self, text: str) -> tuple[bytes, str]:
        return await self.voice_reply_service.synthesize_reply_voice_audio(text)

    async def _synthesize_reply_voice_file(self, text: str) -> tuple[str, str]:
        return await self.voice_reply_service.synthesize_reply_voice_file(text)

    async def _deliver_private_reply(self, target_qq: str, text: str, *, voice_text: str = "", fallback_to_text_on_voice_failure: bool) -> bool:
        return await self.voice_reply_service.deliver_private_reply(
            target_qq,
            text,
            voice_text=voice_text,
            fallback_to_text_on_voice_failure=fallback_to_text_on_voice_failure,
        )

    async def _deliver_group_reply(self, group_id: str, text: str, *, reply_message_id: str = "", at_user_id: str = "", keyboard: str = "", voice_text: str = "", fallback_to_text_on_voice_failure: bool) -> bool:
        return await self.voice_reply_service.deliver_group_reply(
            group_id,
            text,
            reply_message_id=reply_message_id,
            at_user_id=at_user_id,
            keyboard=keyboard,
            voice_text=voice_text,
            fallback_to_text_on_voice_failure=fallback_to_text_on_voice_failure,
        )

    async def _load_business_config(self) -> dict[str, Any]:
        return await self.settings_service.load_business_config()

    async def _ensure_business_config_initialized(self) -> dict[str, Any]:
        return await self.settings_service.ensure_business_config_initialized()

    async def _create_business_config(self) -> dict[str, Any]:
        return await self.settings_service.create_business_config()

    async def _persist_business_config(self) -> bool:
        return await self.settings_service.persist_business_config()

    async def _mutate_business_config(self, mutation) -> bool:
        """Route direct action mutations through the serialized writer."""
        settings_service = getattr(self, "settings_service", None)
        if settings_service is not None:
            return await settings_service.mutate_business_config(mutation)
        # Preserve the established lightweight-host seam used by unit tests.
        if not mutation(self._qq_settings):
            return True
        return await self._persist_business_config()

    def _ensure_qq_client_initialized(self) -> None:
        if self.qq_client is not None:
            return
        self.qq_client = self._make_qq_connection()
        # 入站消息广播钩子：把每条规范化 QQ 消息推给已注册的 sink（本插件用它
        # 广播到 message_plane，供其它插件订阅）。
        self.qq_client.set_inbound_sink(self._broadcast_qq_inbound)
        # 消息增强（引用/转发/语音/文件/VLM 描述）是业务，由插件自持；连接器只提供数据 API。
        if self.enricher is None:
            self.enricher = QQMessageEnricher(
                self.qq_client,
                image_describer=self._describe_reply_image,
                voice_transcriber=self._transcribe_voice,
                logger=self.logger,
                emit_log=self._emit_log,
            )
        elif getattr(self.enricher, "_client", None) is not self.qq_client:
            # 连接对象被收尾重建换掉之后（切换连接模式 / 一键部署 / 补写配置），
            # enricher 仍指着**被丢弃的那个**：引用链、合并转发、语音、文件全都
            # 会去调一条已经断开的连接，异常被 ``_fetch_reply_content`` 的 except
            # 吞成一行 "Failed to fetch reply msg"，**被引用正文静默丢出 prompt**。
            # 2026-09-27 00:41-00:58 现场：NapCat 早就在跑，增强却去调开放平台对象，
            # 报 `QQOpenPlatformConnection.get_msg() takes 1 positional argument`。
            self.enricher.rebind(self.qq_client)
            self.logger.info("[增强] enricher 已重绑到新连接对象")

    async def _broadcast_qq_inbound(self, message: dict[str, Any]) -> None:
        """把一条入站 QQ 消息广播给其它插件（SSE 推送）。

        其它插件/前端连 ``GET /plugin/qq_auto_reply/ui-api/events``（SSE）流式收取
        ``type=qq_message`` 帧（``data`` 即 qq_inbound）。尽力而为：任何失败静默，
        绝不拖垮消息管线。
        """
        try:
            # 发件人/收件人/消息文本 —— 供其它插件拿到的清晰契约。
            self_id = ""
            if getattr(self, "qq_client", None) is not None:
                self_id = str(getattr(self.qq_client, "self_id", "") or "")
            msg_type = str(message.get("message_type") or "")
            group_id = str(message.get("group_id") or "")
            sender = str(message.get("user_id") or "")
            # 收件人：群消息=群号；私聊=bot 自己的 id（不知则回落 sender）。
            recipient = group_id if msg_type == "group" else (self_id or sender)
            relay = {
                "message_type": msg_type,
                "sender": sender,                                 # 发件人 QQ 号
                "sender_nickname": str(message.get("user_nickname") or ""),  # 发件人昵称
                "recipient": recipient,                            # 收件人（群=群号，私聊=bot）
                "recipient_type": "group" if msg_type == "group" else "private",
                "text": str(message.get("content") or "")[:2000],  # 消息文本
                # 原始上下文，便于需要详情的消费者（含 file/voice/引用相关 _* 标记）。
                "user_id": sender,
                "group_id": group_id,
                "content": str(message.get("content") or "")[:2000],
                "message_id": str(message.get("message_id") or ""),
                "timestamp": message.get("timestamp"),
                "is_at_bot": bool(message.get("is_at_bot")),
                "is_reply_to_bot": bool(message.get("is_reply_to_bot")),
            }
            # SSE 推送：其它插件/前端连 /plugin/qq_auto_reply/ui-api/events 流式收取。
            self._spawn_push_ui_event("qq_message", relay["text"] or "QQ 入站消息", data=relay)
        except Exception:
            pass

    @lifecycle(id="startup")
    async def startup(self, **_):
        if not await self.config_store.exists():
            await self._create_business_config()
        settings = await self._ensure_business_config_initialized()
        self.settings_service.rebuild_permission_managers(settings)
        self.settings_service.apply_runtime_settings(settings)
        await self.attention_service.load_cached_state()
        self.reply_buffer_service = QQReplyBufferService(self)
        # 群友复读 → 跟着复读一次（判定要按人去重、跨消息累积，所以单独一个服务；
        # 规则与理由见 repeat_echo_service 的模块 docstring）。
        self.repeat_echo_service = QQRepeatEchoService(self)
        # 出站守门人：内容安全（复用关键词黑名单）+ 重复过滤。三条"把别人原文发出去"
        # 的路（跟着复读 / 合并转发 / 别的插件原文直发）都从它过一道。
        self.outbound_guard_service = QQOutboundGuardService(self)
        # 接续摘要：会话被回收（空闲 5 分钟 / 换人格 / 重启）时留一句"刚才聊到哪儿"，
        # 新会话开口就接得上（见 session_handoff_service 的模块 docstring）。
        self.session_handoff_service = QQSessionHandoffService(self)
        self._ensure_qq_client_initialized()
        if self.attention_gate_service:
            await self.attention_gate_service.start_proactive_loop()
        # UI 静态文件不设强缓存（默认 max-age=3600 会让浏览器缓存旧版
        # script.js/index.html 长达 1 小时，改代码后用户仍看到旧页面、
        # 报早已修复的错误）。no-cache = 每次加载都重新校验，内容恒为最新。
        self.register_static_ui("static", cache_control="no-cache")
        self.set_list_actions([
            {
                "id": "open_ui",
                "label": self.i18n.t("ui.actions.open", default="打开 UI"),
                "kind": "ui",
                "target": f"/plugin/{self.plugin_id}/ui/",
                "open_in": "new_tab",
            }
        ])
        # 后台任务一律**不在这里建**，只记一个"待办"，由 ``_on_command_loop_start``
        # （宿主在常驻 loop 上主动调，不需要界面打开）在那边补建；
        # 各 entry 分流里也挂了一份兜底。见 ``_kick_deferred_startup_tasks``。
        #
        # ⚠️ 宿主跑各个阶段用的 loop 不同（plugin/core/host.py）：
        #
        #   asyncio.run(_run_startup_with_downlink(startup_fn))   # L1 一次性
        #   asyncio.run(_async_command_loop())                    # L2 常驻 ← 入口在这
        #   asyncio.run(result)  # shutdown                       # L3 一次性
        #
        # 定时任务和自定义事件也各自 ``asyncio.run(fn())``，同样是一次性的。
        #
        # 所以在 L1 上 ``create_task`` 出来的东西**活不过 startup 返回** —— 钩子一结束
        # 它就把这条 loop 上还挂着的任务全部取消并关闭。实测自启任务 1 秒内被取消；
        # 这里的几个死得更安静，一行日志都没有，于是"清理旧消息""推送 trust 池"
        # 这些功能一直是**静默没跑**的状态。
        #
        # 也**不能内联 await**：``ensure_napcat_started()`` 会在 L1 上
        # ``create_subprocess_exec``，NapCat 子进程句柄从此绑在一条已关闭的 loop 上,
        # ``_stop_managed_napcat`` 再也杀不掉它。（这正是日志里那个
        # ``got Future attached to a different loop``。）
        #
        # ``_session_housekeeping_task`` **不在此列**：它的生命周期归
        # ``runtime_ops_service.start_auto_reply`` —— 那里明确要求**连接成功之后**
        # 才建（否则连接失败时它会在"已停止"状态下一直跑 idle flush / attention decay）。
        # 标识符语义的登记同理，也发生在连接真正建立之后（见 §2.15.4）。
        self._deferred_startup_tasks = True
        return Ok({"status": "ready"})

    async def _on_command_loop_start(self) -> None:
        """宿主在**常驻 loop**（L2）上、命令循环开始读之前调这里。

        这是插件里唯一一个"由宿主主动调、且跑在常驻 loop 上"的地方 —— ``startup()``
        跑在 ``asyncio.run`` 开的一次性 loop 上（理由见那里的说明），在那里建的后台
        任务活不过钩子返回。所以自启、trust 池推送、旧消息清理都在这里起。

        **不需要界面打开**：宿主在插件起来之后就会调它，与有没有人看插件页无关。
        """
        self._kick_deferred_startup_tasks()

    def _kick_deferred_startup_tasks(self) -> None:
        """在**常驻 loop** 上补建 startup 阶段建不了的后台任务。

        由 ``_on_command_loop_start``（主路径）和各 entry 分流的最前面（兜底）调用。
        入口那份是给"宿主没调这个钩子"的老版本留的退路；**入口是插件里另一个跑在
        常驻 loop 上的地方**，所以退路本身也是对的，只是要等界面轮询 ``query``。

        幂等：``_deferred_startup_tasks`` 标记清掉之后就是空转。
        """
        if not getattr(self, "_deferred_startup_tasks", False):
            return
        self._deferred_startup_tasks = False

        # 自启：起监听 / 拉起 NapCat（要在这里 spawn 子进程，所以非 L2 不可）
        task = getattr(self, "_autostart_task", None)
        if task is None or task.done():
            self._autostart_task = asyncio.create_task(self._autostart_on_launch())

        # 存量 trust 池推送。**不阻塞 startup**：memory_server 可能还没起来，
        # 而这个循环自带退避重试。
        task = getattr(self, "_trust_migration_task", None)
        if task is None or task.done():
            self._trust_migration_task = asyncio.create_task(
                self.settings_service.push_legacy_speaker_trust_forever()
            )

        # 定期清理已审核超过 24h 的旧消息
        task = getattr(self, "_purge_task", None)
        if task is None or task.done():
            self._purge_task = asyncio.create_task(self._purge_old_reviewed_loop())

        # 插件工具桥的候选表：**必须在这里起**（常驻 loop）。真机实测在 entry
        # handler 里同步查宿主会超时（`Plugin query timed out after 15.0s`），
        # 而后台任务里跑正常 —— 所以这条查询只能由后台循环承担。
        # `getattr`：轻量调用方/测试可能没有这个服务，缺了也只是没有候选表。
        tool_bridge = getattr(self, "plugin_tool_service", None)
        if tool_bridge is not None:
            tool_bridge.ensure_refresh_loop()

        # 异步任务的结果回投（她也在这里把"从上次没说完的事"接着说完）：
        # 磁盘上可能还留着宿主重启前在等的任务，得接着轮询 —— 否则那句
        # "结果出来我告诉你"就永远没人兑现，而且连日志都不会有。
        followups = getattr(self, "plugin_tool_followup_service", None)
        if followups is not None:
            followups.ensure_loop()

    async def _autostart_on_launch(self) -> None:
        """开机自启：起监听 / 拉起 NapCat。

        **必须由 ``startup()`` 内联 await，不能 create_task。** 原因见 ``startup()``
        里的说明：startup 钩子跑在一次性 event loop 上，在那里建的任务活不过 startup
        返回 —— 这条路上"任务"和"不工作"是同一件事。

        **反向模式（``napcat``）先起自动回复。** 那条路上插件是**监听**方：监听没
        竖起来，NapCat 拨进来也没人接。一键部署那边早就是这个顺序（见
        ``deploy_service`` 步骤④/⑤的说明），自启这条路当时没跟上。正向模式
        （``napcat_forward``）反过来：插件是拨号方，NapCat 得先在。

        **不等 OneBot 就绪。** 那一步只是报个状态，而两个模式的连接都是**自愈**的：
        反向等 NapCat 拨进来、正向有退避重连循环。等它只会白白把最多 20 秒压在
        插件握手上（握手超时 = 整个插件不可用），得不偿失。
        """
        if not bool((self._qq_settings or {}).get("auto_start_on_launch", False)):
            return
        try:
            mode = str((self._qq_settings or {}).get("qq_connection_mode") or "napcat")
            reverse = mode != "napcat_forward"
            # 每一步都留痕：这条路上出过"三个出口全都不打日志、卡住了无从下手"的事。
            self._emit_log("INFO", f"[自启] 开始（模式 {mode}）")

            result = None
            if reverse:
                self._emit_log("INFO", "[自启] 1/3 启动自动回复（反向模式：插件是监听方）")
                result = await self.runtime_ops_service.start_auto_reply()

            self._emit_log("INFO", "[自启] 2/3 启动 NapCat…")
            await self.napcat_service.ensure_napcat_started()
            err = self.napcat_service.get_startup_error()
            if err:
                self._emit_log("WARN", f"[自启] NapCat 未启动: {err}")
                return

            if not reverse:
                result = await self.runtime_ops_service.start_auto_reply()

            status = ""
            if isinstance(result, Ok) and isinstance(result.value, dict):
                status = str(result.value.get("status") or "")
            self._emit_log(
                "INFO",
                f"[自启] 完成（{status or 'ok'}）—— NapCat 就绪后会自动接上，不必手动点启动",
            )
        except asyncio.CancelledError:
            # 取消是 ``BaseException``，被下面的 ``except Exception`` 漏掉的话
            # 会**静默消失**、一行日志都不留 —— 之前正是这个状态，完全没法排查。
            self._emit_log("WARN", "[自启] 被取消")
            raise
        except Exception as e:
            # 自启失败只记日志：插件本身是好的，用户还能手动开。
            self._emit_log("WARN", f"[自启] 失败: {type(e).__name__}: {e}")

    async def _purge_old_reviewed_loop(self):
        """每小时清理一次已审核超过 24 小时的旧消息。"""
        await asyncio.sleep(300)  # 启动后等 5 分钟再开始
        while True:
            try:
                removed = await self.backlog_store.purge_old_reviewed(max_age_seconds=86400)
                if removed > 0:
                    self._emit_log("INFO", f"清理了 {removed} 条过期已审核消息")
            except asyncio.CancelledError:
                break
            except Exception:
                self.logger.warning("清理过期消息失败", exc_info=True)
            await asyncio.sleep(3600)

    async def _group_digest_loop(self, interval_minutes: int = 5):
        """定期将各群聊摘要推送到 Memory Server（跨群共享记忆）"""
        await asyncio.sleep(60)
        while True:
            try:
                await asyncio.sleep(interval_minutes * 60)
            except asyncio.CancelledError:
                break
            try:
                sessions = getattr(self, "_user_sessions", {}) or {}
                for key, s in list(sessions.items()):
                    if not isinstance(s, dict) or not s.get("is_group"):
                        continue
                    session = s.get("session")
                    if not session or not hasattr(session, "_conversation_history"):
                        continue
                    history = getattr(session, "_conversation_history", []) or []
                    if len(history) < 4:
                        continue
                    group_id = str(s.get("group_id") or key)
                    her_name = str(s.get("her_name") or "neko")
                    login_id = str(s.get("login_self_id") or "")
                    sender_id = str(s.get("sender_id") or "")
                    user_title = str(s.get("user_title") or "")
                    user_label = f"{user_title}(QQ:{sender_id})" if user_title else f"QQ{sender_id}"
                    messages = []
                    for msg in history[-20:]:
                        role = getattr(msg, "role", "") if hasattr(msg, "role") else msg.get("role", "")
                        content = getattr(msg, "content", "") if hasattr(msg, "content") else msg.get("content", "")
                        if role in ("user", "assistant") and content:
                            messages.append({"role": role, "content": str(content)[:200]})
                    if not messages:
                        continue
                    try:
                        await self.memory_bridge.post_memory_history(
                            "process",
                            her_name,
                            [{"role": "system", "content": (
                                f"[QQ群聊记录] {her_name} 使用QQ插件在群 {group_id}"
                                + (f"（账号 {login_id}）" if login_id else "")
                                + " 聊了以下内容：\n"
                                + "\n".join(f"{user_label if m['role']=='user' else her_name}: {m['content']}" for m in messages[-8:])
                            )}],
                            timeout=3.0,
                        )
                        self._emit_log("INFO", f"群 {group_id} 摘要已推送 Memory Server ({len(messages)}条)")
                    except Exception:
                        pass
            except Exception as e:
                self.logger.warning(f"群摘要推送异常: {e}")

    @lifecycle(id="shutdown")
    async def shutdown(self, **_):
        if self.attention_gate_service:
            await self.attention_gate_service.stop_proactive_loop()
        await self._stop_auto_reply_runtime(stop_napcat=True)
        sync_tasks = (
            list(getattr(self, "_group_memory_sync_tasks", ()) or ())
            + list(getattr(self, "_prompt_change_discard_tasks", ()) or ())
        )
        if sync_tasks:
            # 隐私关键的开关转变任务在关机 flush 前 join（限 1s），避免
            # 结算做到一半被进程退出截断。
            # asyncio.wait 不取消未完成任务——超时放行但不杀结算。
            done_tasks, _pending = await asyncio.wait(sync_tasks, timeout=1.0)
            for finished in done_tasks:
                if finished.cancelled():
                    self._emit_log("WARNING", "记忆同步任务被外部取消")
                    continue
                exc = finished.exception()
                if exc is not None:
                    self._emit_log("ERROR", f"记忆同步任务异常结束: {exc}")
        await self._flush_all_memory_sessions(reason="shutdown")
        if self.attention_gate_service:
            await self.attention_gate_service.shutdown()
        if (
            self._trust_migration_task
            and not self._trust_migration_task.done()
        ):
            self._trust_migration_task.cancel()
        if (
            self._identity_scope_task
            and not self._identity_scope_task.done()
        ):
            self._identity_scope_task.cancel()
        if self._group_digest_task and not self._group_digest_task.done():
            self._group_digest_task.cancel()
        if getattr(self, "_purge_task", None) and not self._purge_task.done():
            self._purge_task.cancel()
        if self._session_housekeeping_task:
            self._session_housekeeping_task.cancel()
            try:
                await self._session_housekeeping_task
            except asyncio.CancelledError:
                pass
            self._session_housekeeping_task = None
        # 这里不关 http client：记忆桥与附件下载用的是 utils/http 的进程级
        # 单例，由 main_server 的 shutdown 钩子统一关。插件自己关会把上面
        # 那批"只 join 1s、不取消"的结算任务的在途请求打断。
        return Ok({"status": "shutdown"})

    def _mask_token(self, token: str) -> str:
        normalized = str(token or "")
        if not normalized:
            return ""
        if len(normalized) <= 6:
            return "*" * len(normalized)
        return f"{normalized[:3]}***{normalized[-3:]}"

    def _get_napcat_directory(self) -> Path:
        return self.napcat_service.get_napcat_directory()

    def _get_napcat_launch_target(self) -> Path:
        return self.napcat_service.get_napcat_launch_target()

    def _get_napcat_qrcode_path(self) -> Path:
        return self.napcat_service.get_napcat_qrcode_path()

    async def _sync_napcat_qrcode_into_static(self) -> bool:
        return await self.napcat_service.sync_napcat_qrcode_into_static()

    def _resolve_sticker_path(self, sticker_id: str) -> str:
        """解析表情包 ID 到文件路径（供 delivery_node 使用）。"""
        return self.reply_pipeline._resolve_sticker_path(sticker_id)

    def _find_napcat_launcher(self) -> Path | None:
        return self.napcat_service.find_napcat_launcher()

    async def _ensure_napcat_started(self) -> None:
        mode = str((self._qq_settings or {}).get("qq_connection_mode", "napcat") or "napcat").strip()
        if mode == "open_platform":
            return  # 开放平台不需要本地 NapCat 进程
        # 反向和正向都需要本地 NapCat：正向模式同样是 NapCat 提供 QQ 登录/
        # 扫码与 OneBot 服务，只是我们作为 WS 客户端拨出而不是等它拨入。
        await self.napcat_service.ensure_napcat_started()

    async def _stop_managed_napcat(self) -> None:
        await self.napcat_service.stop_managed_napcat()

    def _build_runtime_status(self) -> dict[str, Any]:
        return self.runtime_service.build_runtime_status()

    async def _fetch_login_status_payload(self) -> dict[str, Any]:
        return await self.runtime_service.fetch_login_status_payload()

    async def _refresh_actual_contacts_cache(self) -> dict[str, Any]:
        return await self.runtime_service.refresh_actual_contacts_cache()

    async def _build_dashboard_state(self) -> dict[str, Any]:
        return await self.dashboard_service.build_dashboard_state()

    @ui.context(id="qq_auto_reply")
    async def get_dashboard_context(self):
        return await self.dashboard_service.build_dashboard_context()

    async def open_ui(self, **_):
        return await self.dashboard_service.open_ui()


    # ── trust：名单与身份 ───────────────────────────────────────
    #
    # 信任用户/群、用户昵称、开放平台身份合并，以及联系人刷新。对 agent 可见。

    @ui.action(id="trust", label=tr("entries.trust.name", default="信任名单与身份"), refresh_context=True)
    @plugin_entry(
        id="trust",
        name=tr("entries.trust.name", default="信任名单与身份"),
        description=tr("entries.trust.description", default="管理信任用户/群、用户昵称，以及开放平台身份合并。action 取 user_add / user_remove / user_nickname / group_add / group_remove / claims / identity_bind / identity_unbind / refresh_contacts。"),
        input_schema={"type": "object", "properties": {
            "action": {"type": "string",
                       "enum": ["user_add", "user_remove", "user_nickname",
                                "group_add", "group_remove", "claims",
                                "identity_bind", "identity_unbind", "refresh_contacts"],
                       "description": "user_add=加信任用户；user_remove=移除；user_nickname=改昵称；group_add=加信任群；group_remove=移除；claims=列出未认领的群内 ID；identity_bind=合并到已有身份；identity_unbind=撤销合并；refresh_contacts=从 OneBot 重新拉联系人"},
            "qq_number": {"type": "string", "description": "user_add / user_remove / user_nickname：QQ 号"},
            "nickname": {"type": "string", "description": "user_add / user_nickname：显示昵称"},
            "level": {"type": "string", "description": "user_add / group_add：权限等级（用户：admin / trusted / normal / blacklist，blacklist＝不处理这个人的消息；群：trusted / normal，群省略 level 时默认 trusted＝正常参与，只有显式 normal 才是仅转发）"},
            "normal_relay_probability": {"type": "number", "description": "user_add / group_add：普通转发概率"},
            "group_id": {"type": "string", "description": "group_add / group_remove：群号"},
            "user_id": {"type": "string", "description": "identity_bind / identity_unbind：群内 ID"},
            "target_user_id": {"type": "string", "description": "identity_bind：要并入的已有身份"},
        }, "required": ["action"], "additionalProperties": False},
    )
    async def trust(self, action: str = "", **kw):
        return await self._trust_dispatch(str(action or "").strip(), kw)

    async def _trust_dispatch(self, action: str, kw: dict[str, Any]):
        if action == "user_add":
            return await self._trust_user_add(kw)
        if action == "user_remove":
            return await self._trust_user_remove(kw)
        if action == "user_nickname":
            return await self._trust_user_nickname(kw)
        if action == "group_add":
            return await self._trust_group_add(kw)
        if action == "group_remove":
            return await self._trust_group_remove(kw)
        if action == "claims":
            return await self._trust_claims(kw)
        if action == "identity_bind":
            return await self._trust_identity_bind(kw)
        if action == "identity_unbind":
            return await self._trust_identity_unbind(kw)
        if action == "refresh_contacts":
            return await self._trust_refresh_contacts(kw)
        return Err(SdkError(
            f"BAD_ACTION: trust 不支持 {action!r}"
            f"（可选 user_add/user_remove/user_nickname/group_add/group_remove/"
            f"claims/identity_bind/identity_unbind/refresh_contacts）"))

    async def _trust_user_add(self, kw: dict[str, Any]):
        """把一个 QQ 号加入信任用户列表。"""
        qq_number = str(kw.get("qq_number") or "").strip()
        if not qq_number:
            return Err(SdkError("INVALID_INPUT: user_add 需要 qq_number"))
        return await self.dashboard_service.add_trusted_user(
            qq_number=qq_number,
            level=str(kw.get("level") or "trusted"),
            nickname=str(kw.get("nickname") or ""),
            normal_relay_probability=kw.get("normal_relay_probability"),
        )

    async def _trust_user_remove(self, kw: dict[str, Any]):
        """把一个 QQ 号从信任用户列表移除。"""
        qq_number = str(kw.get("qq_number") or "").strip()
        if not qq_number:
            return Err(SdkError("INVALID_INPUT: user_remove 需要 qq_number"))
        return await self.dashboard_service.remove_trusted_user(qq_number=qq_number)

    async def _trust_user_nickname(self, kw: dict[str, Any]):
        """修改信任用户在回复里显示的昵称。"""
        qq_number = str(kw.get("qq_number") or "").strip()
        if not qq_number:
            return Err(SdkError("INVALID_INPUT: user_nickname 需要 qq_number"))
        return await self.dashboard_service.set_user_nickname(
            qq_number=qq_number, nickname=str(kw.get("nickname") or ""))

    async def _trust_group_add(self, kw: dict[str, Any]):
        """把一个 QQ 群加入信任群聊列表。"""
        group_id = str(kw.get("group_id") or "").strip()
        if not group_id:
            return Err(SdkError("INVALID_INPUT: group_add 需要 group_id"))
        return await self.dashboard_service.add_trusted_group(
            group_id=group_id,
            level=str(kw.get("level") or DEFAULT_GROUP_LEVEL),
            normal_relay_probability=kw.get("normal_relay_probability"),
        )

    async def _trust_group_remove(self, kw: dict[str, Any]):
        """把一个 QQ 群从信任群聊列表移除。"""
        group_id = str(kw.get("group_id") or "").strip()
        if not group_id:
            return Err(SdkError("INVALID_INPUT: group_remove 需要 group_id"))
        return await self.dashboard_service.remove_trusted_group(group_id=group_id)

    async def _trust_claims(self, kw: dict[str, Any]):
        """列出开放平台上出现过、但还不在名册里的群内 ID。"""
        return await self.dashboard_service.list_identity_claims()

    async def _trust_identity_bind(self, kw: dict[str, Any]):
        """把一个群内 ID 的信赖度账本并入已有身份。只能由人触发。"""
        user_id = str(kw.get("user_id") or "")
        target_user_id = str(kw.get("target_user_id") or "")
        if not user_id or not target_user_id:
            return Err(SdkError("INVALID_INPUT: identity_bind 需要 user_id 与 target_user_id"))
        return await self.dashboard_service.bind_identity_account(
            user_id=user_id, target_user_id=target_user_id)

    async def _trust_identity_unbind(self, kw: dict[str, Any]):
        """把一个群内 ID 从被合并进的身份里拆回独立身份（误合并的唯一回滚方式）。"""
        user_id = str(kw.get("user_id") or "")
        if not user_id:
            return Err(SdkError("INVALID_INPUT: identity_unbind 需要 user_id"))
        return await self.dashboard_service.unbind_identity_account(user_id=user_id)

    async def _trust_refresh_contacts(self, kw: dict[str, Any]):
        """重新从 OneBot 拉取好友与群列表。"""
        return await self.dashboard_service.refresh_actual_contacts()









    # ── QQ 开放平台：扫码创建 / 复用机器人 ────────────────────








    # ── query：状态读取 ─────────────────────────────────────────
    #
    # 全是只读。对 agent 可见 —— 它得能看状态才知道自己做得对不对。

    @ui.action(id="query", label=tr("entries.query.name", default="查询状态"), refresh_context=False)
    @plugin_entry(
        id="query",
        name=tr("entries.query.name", default="查询状态"),
        description=tr("entries.query.description", default="只读查询：控制面板状态、缓冲、日志、提示词、用户画像、待审阅、NapCat WebUI、机器人账本。action 取 dashboard / buffer / logs / prompt_editor / group_prompts / user_profiles / backlog_summary / backlog_detail / napcat_webui / bots。"),
        input_schema={"type": "object", "properties": {
            "action": {"type": "string",
                       "enum": ["dashboard", "buffer", "logs", "prompt_editor",
                                "group_prompts", "user_profiles",
                                "backlog_summary", "backlog_detail",
                                "napcat_webui", "bots"],
                       "description": "要读哪一份状态"},
            "lines": {"type": "integer", "description": "logs：取最近多少行，默认 100"},
            "group_id": {"type": "string", "description": "backlog_detail：群号"},
            "mode": {"type": "string", "description": "prompt_editor：策略模式（选填）"},
            "locale": {"type": "string", "description": "prompt_editor：语言（选填）"},
        }, "required": ["action"], "additionalProperties": False},
    )
    async def query(self, action: str = "", **kw):
        return await self._query_dispatch(str(action or "").strip(), kw)

    async def _query_dispatch(self, action: str, kw: dict[str, Any]):
        self._kick_deferred_startup_tasks()
        if action == "dashboard":
            return await self._query_dashboard(kw)
        if action == "buffer":
            return await self._query_buffer(kw)
        if action == "logs":
            return await self._query_logs(kw)
        if action == "prompt_editor":
            return await self._query_prompt_editor(kw)
        if action == "group_prompts":
            return await self._query_group_prompts(kw)
        if action == "user_profiles":
            return await self._query_user_profiles(kw)
        if action == "backlog_summary":
            return await self._query_backlog_summary(kw)
        if action == "backlog_detail":
            return await self._query_backlog_detail(kw)
        if action == "napcat_webui":
            return await self._query_napcat_webui(kw)
        if action == "bots":
            return await self._query_bots(kw)
        if action == "plugin_tools":
            return await self._query_plugin_tools(kw)
        if action == "proactive_initiative":
            # 主动搭话（管理员私聊）的状态视图：开关/静音时段/今日已开口/待说的由头。
            # 判定与"为什么还不会开口"由服务自己算，界面只显示 —— 别在两处各写一份判据。
            return Ok(self.proactive_initiative_service.snapshot())
        return Err(SdkError(
            f"BAD_ACTION: query 不支持 {action!r}"
            f"（可选 dashboard/buffer/logs/prompt_editor/group_prompts/"
            f"user_profiles/backlog_summary/backlog_detail/napcat_webui/bots/plugin_tools）"))

    async def _query_plugin_tools(self, kw: dict[str, Any]):
        """插件工具桥的配置视图：**全量非 QQ 插件**（带是否在跑）+ 当前分级。

        **加插件不筛启动状态**（使用者定的）：界面全量出卡片，先分配档位；是不是在跑
        只影响"这一轮挂不挂得上"，不影响"能不能先配好"。所以候选里每条都带 ``running``，
        界面据此打「未启动」标记；而挂载那道闸在 `select_mounted`（只带在跑的）。

        另外仍给 ``configured_not_running``：配了但此刻没在跑的插件清单 ——
        界面用它显示一行"这些挂不上去"，避免使用者以为配好了就能用。
        """
        _ = kw
        service = self.plugin_tool_service
        tiers = service.tiers()
        # refresh_for_ui：界面要看**此刻**的启停状态（刚启动一个插件就该能加它）。
        # 它是一次真网络往返（本机 HTTP，实测 ~0.3s，4s 上限后回退缓存）——
        # 而每轮生成那条路径吃的是 60s 缓存（见 CANDIDATES_TTL_SECONDS）。
        candidates = await service.refresh_for_ui()
        running_by_id = {
            row["plugin_id"]: bool(row.get("running")) for row in candidates
        }
        configured_not_running = sorted(
            pid for pid in tiers if not running_by_id.get(pid, False)
        )
        current = (
            str(self.qq_client.mode if self.qq_client is not None else "")
            or str(self._qq_settings.get("qq_connection_mode") or "")
        )
        followups = getattr(self, "plugin_tool_followup_service", None)
        pending_followups = followups.snapshot() if followups is not None else []
        return Ok({
            # 通道闸对所有 QQ 通道都开（开放平台 + OneBot）：界面用这个布尔决定
            # "配置正在生效" 还是 "这条通道挂不上工具"，别再写死 mode 比较。
            "enabled_on_this_channel": channel_allows_plugin_tools(self.qq_client),
            "connection_mode": current,
            "tiers": tiers,
            "candidates": candidates,
            "configured_not_running": configured_not_running,
            "max_plugins": PLUGIN_TOOL_MAX_MOUNTED,
            # 还在等结果的异步任务（结果一到她会主动回一条），界面显示它，
            # 免得使用者以为"又没下文了"。
            "followups_enabled": bool(followups.enabled()) if followups is not None else False,
            "pending_followups": pending_followups,
            "max_pending_followups": (
                followups.max_pending() if followups is not None else 0
            ),
        })

    async def _query_dashboard(self, kw: dict[str, Any]):
        _ = kw
        return await self.dashboard_service.get_dashboard_state()

    async def _query_buffer(self, kw: dict[str, Any]):
        _ = kw
        if not self.reply_buffer_service:
            return Ok({"pending": [], "count": 0})
        return Ok(self.reply_buffer_service.get_state())

    async def _query_napcat_webui(self, kw: dict[str, Any]):
        _ = kw
        url = self.napcat_service.get_webui_url()
        webui_lines = await self.napcat_service._read_napcat_webui_lines()
        return Ok({"url": url, "lines": webui_lines})

    async def _query_bots(self, kw: dict[str, Any]):
        _ = kw
        from . import qq_official_bind as bind

        active = str(self._qq_settings.get(bind.ACTIVE_KEY) or "")
        bots = [
            {**{k: v for k, v in b.items() if k != "secret"},
             "active": str(b.get("appid") or "") == active}
            for b in bind.list_bots(self._qq_settings)
        ]
        return Ok({"bots": bots, "active_appid": active, "total": len(bots)})

    async def _query_backlog_summary(self, kw: dict[str, Any]):
        _ = kw
        return Ok(await self.backlog_service.get_summary_payload())

    async def _query_group_prompts(self, kw: dict[str, Any]):
        _ = kw
        """获取所有群的专属提示词映射。"""
        group_prompts = dict(self._qq_settings.get("group_prompts") or {})
        return Ok({"group_prompts": group_prompts})

    async def _maybe_notify_backlog_summary(self, *, group_id: str) -> None:
        await self.backlog_service.maybe_notify_summary(group_id=group_id)

    async def _process_messages(self):
        await self.message_dispatcher.process_messages()

    async def _handle_message(self, message: Dict[str, Any]):
        await self.message_dispatcher.handle_message(message)

    async def _handle_private_message(self, sender_id: str, message_text: str, attachments: Optional[list[Dict[str, Any]]] = None, user_nickname: Optional[str] = None):
        await self.message_dispatcher.handle_private_message(sender_id, message_text, attachments=attachments, user_nickname=user_nickname)

    async def _handle_group_message(self, group_id: str, sender_id: str, message_text: str, is_at_bot: bool, attachments: Optional[list[Dict[str, Any]]] = None, user_nickname: Optional[str] = None):
        await self.message_dispatcher.handle_group_message(group_id, sender_id, message_text, is_at_bot, attachments=attachments, user_nickname=user_nickname)

    async def _query_logs(self, kw: dict[str, Any]):
        lines = kw.get("lines", 100)
        """返回最近的日志行（内存缓冲区 + NapCat 输出）"""
        result_lines: list[str] = []
        buf = getattr(self, "_log_buffer", None)
        if buf and len(buf) > 0:
            n = max(1, min(int(lines or 100), self.LOG_BUFFER_SIZE))
            result_lines = list(buf)[-n:]
        # 追加 NapCat 输出
        try:
            napcat_lines = await self.napcat_service._read_napcat_webui_lines()
            if napcat_lines:
                result_lines.append("--- NapCat 输出 ---")
                result_lines.extend(napcat_lines)
        except Exception:
            pass
        if result_lines:
            return Ok({"lines": result_lines, "total": len(result_lines), "source": "memory+napcat"})
        # 回退：从日志文件读取
        import os
        log_path = ""
        try:
            handler = getattr(self, "file_logger", None)
            if handler and hasattr(handler, "handlers"):
                for h in handler.handlers:
                    if hasattr(h, "baseFilename"):
                        log_path = h.baseFilename
                        break
        except Exception:
            pass
        if log_path and os.path.exists(log_path):
            try:
                with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                    all_lines = f.readlines()
                n = max(1, min(int(lines or 100), 500))
                return Ok({"lines": [ln.rstrip("\n\r") for ln in all_lines[-n:]], "total": n, "source": "file"})
            except Exception as e:
                return Ok({"lines": [], "total": 0, "message": str(e)})
        return Ok({"lines": [], "total": 0, "message": "暂无日志（缓冲区为空且未找到日志文件）"})

    async def _query_backlog_detail(self, kw: dict[str, Any]):
        group_id = str(kw.get("group_id") or "")
        normalized_group_id = self._validate_group_id(group_id)
        return Ok(await self.backlog_service.get_group_detail_payload(normalized_group_id))

    async def _query_user_profiles(self, kw: dict[str, Any]):
        _ = kw
        profiles: list[dict[str, Any]] = []
        now = time.time()
        cache = getattr(self.session_instruction_service, "_user_profile_cache", {}) or {}
        perm_mgr = self.permission_mgr

        # 收集所有有缓存画像的用户（cache key 格式: sender_id:scope_key）
        seen: set[str] = set()
        for cache_key, (text, expire_at) in list(cache.items()):
            # 从复合 key 中提取 sender_id
            sender_id = str(cache_key).split(":", 1)[0] if ":" in str(cache_key) else str(cache_key)
            if sender_id in seen:
                continue
            seen.add(sender_id)
            nickname = ""
            level = "none"
            if perm_mgr:
                nickname = perm_mgr.get_nickname(sender_id) or ""
                level = perm_mgr.get_permission_level(sender_id)
            profiles.append({
                "sender_id": sender_id,
                "nickname": nickname,
                "permission_level": level,
                "profile_text": text,
                "cached": True,
                "expires_in_seconds": max(0, int(expire_at - now)),
            })

        # 也列出信任用户中还没有画像的
        if perm_mgr:
            for u in perm_mgr.list_users():
                sid = str(u.get("qq") or "").strip()
                if sid and sid not in seen:
                    seen.add(sid)
                    profiles.append({
                        "sender_id": sid,
                        "nickname": str(u.get("nickname") or ""),
                        "permission_level": str(u.get("level") or "normal"),
                        "profile_text": "",
                        "cached": False,
                        "expires_in_seconds": 0,
                    })


        return Ok({"profiles": profiles, "count": len(profiles), "cached_count": sum(1 for p in profiles if p["cached"])})

    # ==========================================
    # 提示词编辑器
    # ==========================================

    async def _query_prompt_editor(self, kw: dict[str, Any]):
        mode = str(kw.get("mode") or "")
        locale = str(kw.get("locale") or "")
        frontend_mode = str(mode or "").strip()
        stored_mode = str((self._qq_settings or {}).get("qq_connection_mode", "napcat") or "napcat").strip()
        mode = frontend_mode if frontend_mode in ("napcat", "napcat_forward", "open_platform") else stored_mode
        from utils.language_utils import get_global_language_full
        frontend_locale = str(locale or "").strip()
        # #2500 第 2 步：兜底也用全码。这个 locale 有两个身份——查 i18n bundle 的
        # 键，以及回传给前端、被 save_prompt_override 原样当作覆盖的存储键。短码
        # 'zh' 会让繁中用户在编辑器里看到（并覆盖）简体那份。
        # ⚠️ 必须和 session_instruction_service._resolve_static_layer 的兜底同时
        # 翻：写侧用 'zh-TW' 存、读侧还按 'zh' 的候选链找，覆盖会静默失效。
        locale = frontend_locale if frontend_locale else get_global_language_full()
        is_napcat = mode in ("napcat", "napcat_forward")
        overrides = (self._qq_settings or {}).get("prompt_overrides") or {}
        if not isinstance(overrides, dict):
            overrides = {}
        layers = []
        for layer_def in self.session_instruction_service._PROMPT_LAYERS:
            lid = layer_def["id"]
            is_runtime = layer_def.get("runtime", False)
            is_scene = lid.startswith("scene_") or lid.startswith("naming_")
            # 按连接模式过滤 format/scene 层
            if lid.startswith("format_"):
                if is_napcat:
                    if lid == "format_open_platform":
                        continue
                    # neko_dynamic 是唯一策略 → format_neko_dynamic 永远显示；
                    # format_neko_scene 层已随模式合并删除。
                else:
                    # 开放平台只显示 format_open_platform
                    if lid != "format_open_platform":
                        continue
            # NapCat：scene 层只保留 scene_group_dynamic（策略已单值）
            if is_scene and lid not in ("scene_group_dynamic",):
                continue
            # 开放平台跳过 scene/naming 层
            if not is_napcat and is_scene:
                continue
            # 获取当前生效的文本
            i18n_key = layer_def.get("i18n_key", "")
            default_text = ""
            if not is_runtime:
                # 层 → 模板的映射收口在 `prompt_fragment_templates.layer_default_templates()`：
                # 编辑器回显与"bundle 有没有盖住模板"的看门狗共用一份真相源
                # （内联副本漂了就等于没有闸，`core_memory_section` 那次就是这么漏的）。
                from .prompt_fragment_templates import layer_default_templates

                default_text = layer_default_templates().get(i18n_key, "")
            has_override = False
            effective_text = ""
            if not is_runtime:
                # ⚠️ 走候选链而不是精确匹配 ``overrides[locale]``：覆盖桶的键是
                # 存的时候那次的 locale，未必等于现在解析出来的（#2500 之前繁中
                # 用户的兜底是短码 'zh'）。运行时按候选链读，编辑器精确匹配的
                # 话，那份覆盖照样生效、编辑器却报「未修改」。
                found = resolve_prompt_override(overrides, locale, i18n_key)
                if found is not None:
                    has_override = True
                    effective_text = str(found[1] or "")
                else:
                    effective_text = self.i18n.t(i18n_key, locale=locale, default=default_text)
            if lid == "time":
                effective_text = build_time_context()
            layers.append({
                "id": lid,
                "i18n_key": i18n_key,
                "is_runtime": is_runtime,
                "required_placeholders": layer_def.get("required_placeholders", []),
                "format_after": layer_def.get("format_after", False),
                "has_override": has_override,
                "default_text": default_text,
                "effective_text": effective_text,
            })
        self._emit_log("INFO", f"[PromptEditor] mode={mode} is_napcat={is_napcat} locale={locale} layers={len(layers)}")
        self.logger.info(f"[PromptEditor] mode={mode} is_napcat={is_napcat} locale={locale} layers={len(layers)}")
        return Ok({
            "mode": mode,
            "locale": locale,
            "layers": layers,
        })

    async def _config_init(self, kw: dict[str, Any]):
        guide_step_config_done = kw.get("guide_step_config_done")
        return await self.dashboard_service.init_config(guide_step_config_done=guide_step_config_done)

    async def _config_nl(self, kw: dict[str, Any]):
        message = str(kw.get("message") or "")
        """通过自然语言解析并保存 OneBot 配置"""
        import re
        text = str(message or "").strip()
        if not text:
            return Err(SdkError("INVALID_INPUT: 请提供自然语言指令，如：设置地址为 ws://0.0.0.0:6199 token 为 abc123"))

        url = ""
        token = ""
        clear_token = False

        # 提取 WebSocket/HTTP 地址
        url_patterns = [
            r'(wss?://\S+)',           # ws://... 或 wss://...
            r'(https?://\S+)',          # http://... 或 https://...
            r'地址[设为是]*[：:\s]*(\S+:\d+\S*)',  # 地址设为 xxx:3001/...
            r'url[设为是]*[：:\s]*(\S+:\d+\S*)',   # url 设为 ...
            r'改为\s*(\S+:\d+\S*)',    # 改为 ...
            r'改成\s*(\S+:\d+\S*)',    # 改成 ...
        ]
        for pattern in url_patterns:
            m = re.search(pattern, text, re.IGNORECASE)
            if m:
                candidate = m.group(1).rstrip(".,;!?）)")
                if "://" in candidate:
                    url = candidate
                    break

        # 提取 token
        token_patterns = [
            r'token\s*[设为是]*[：:\s]*(\S+)',     # token 设为 xxx
            r'access_token\s*[设为是]*[：:\s]*(\S+)',
            r'密钥\s*[设为是]*[：:\s]*(\S+)',
            r'token\s*[=：:]\s*(\S+)',
        ]
        for pattern in token_patterns:
            m = re.search(pattern, text, re.IGNORECASE)
            if m:
                candidate = m.group(1).rstrip(".,;!?）)")
                if candidate in ("空", "无", "清空", "清除", "none", "null"):
                    clear_token = True
                else:
                    token = candidate
                break

        # 检测清空 token
        if not token and not clear_token:
            if re.search(r'(清空|清除|去掉|删除|移除)\s*token', text, re.IGNORECASE):
                clear_token = True

        if not url and not token and not clear_token:
            return Ok({
                "parsed": False,
                "hint": "未能从指令中解析出 OneBot 地址或 Token。请尝试更明确的表达，如：设置地址为 ws://0.0.0.0:6199，token 为 my_token_123",
                "current": {
                    "onebot_url": str(self._qq_settings.get("onebot_url", "")),
                    "token_configured": bool(self._qq_settings.get("token")),
                },
            })

        # 构建 save_settings 参数
        save_kwargs: dict[str, Any] = {}
        if url:
            save_kwargs["onebot_url"] = url
        if token:
            save_kwargs["token"] = token
        if clear_token:
            save_kwargs["token"] = ""

        await self.dashboard_service.save_settings(**save_kwargs)

        changes: list[str] = []
        if url:
            changes.append(f"地址 → {url}")
        if token:
            changes.append(f"Token → {self._mask_token(token)}")
        if clear_token:
            changes.append("Token → (已清空)")

        return Ok({
            "parsed": True,
            "changes": changes,
            "reconnect_required": bool(self._running),
            "hint": "配置已保存" + ("，需要重启自动回复以应用新连接" if self._running else ""),
        })

    async def _config_attention_adjust(self, kw: dict[str, Any]):
        group_id = str(kw.get("group_id") or "")
        delta = kw.get("delta")
        if not self.attention_service:
            return Err(SdkError("attention_service_not_initialized"))
        gid = str(group_id or "").strip()
        if not gid:
            return Err(SdkError("INVALID_INPUT: group_id 不能为空"))
        try:
            amount = float(delta or 0.0)
        except (TypeError, ValueError):
            return Err(SdkError("INVALID_INPUT: delta 必须是数字"))
        if amount > 0:
            await self.attention_service.boost_attention(gid, amount, reason="manual_adjust")
        elif amount < 0:
            await self.attention_service.consume_attention(gid, -amount, reason="manual_adjust")
        else:
            return Ok({"group_id": gid, "delta": 0.0, "note": "noop"})
        state = self.attention_service.get_state(gid)
        self._emit_log("INFO", f"[Attention] 手动调整 群{gid} delta={amount:+.1f} → score={state.attention_score:.1f}")
        return Ok({
            "group_id": gid,
            "delta": amount,
            "attention_score": float(state.attention_score),
        })

    async def _config_memory_forget(self, kw: dict[str, Any]):
        group_id = str(kw.get("group_id") or "")
        normalized_group_id = self._validate_group_id(group_id)
        from utils.config_manager import get_config_manager
        try:
            _, her_name, _, _, _, _, _, _, _ = get_config_manager().get_character_data()
        except Exception:
            her_name = "neko"
        try:
            result = await self.memory_bridge.post_scoped_forget(
                her_name,
                subject=self.memory_bridge.group_subject(normalized_group_id),
            )
            self._emit_log("INFO", f"群 {normalized_group_id} 记忆已清除: {result}")
            return Ok({"group_id": normalized_group_id, "forgotten": True, "detail": result})
        except Exception as exc:
            self._emit_log("ERROR", f"清除群 {normalized_group_id} 记忆失败: {exc}")
            return Err(SdkError(f"FORGET_FAILED: {exc}"))

    async def _config_prompt_save(self, kw: dict[str, Any]):
        locale = str(kw.get("locale") or "")
        layer_id = str(kw.get("layer_id") or "")
        text = kw.get("text")
        locale = str(locale or "").strip()
        layer_id = str(layer_id or "").strip()
        text_val = str(text or "")
        if not locale:
            return Err(SdkError("INVALID_INPUT: locale 不能为空"))
        if not layer_id:
            return Err(SdkError("INVALID_INPUT: layer_id 不能为空"))
        # 验证 layer_id 存在且非 runtime
        layer_def = next((ld for ld in self.session_instruction_service._PROMPT_LAYERS if ld["id"] == layer_id), None)
        if layer_def is None:
            return Err(SdkError(f"INVALID_INPUT: 未知的提示词层: {layer_id}"))
        if layer_def.get("runtime"):
            return Err(SdkError(f"INVALID_INPUT: 运行时层不可编辑: {layer_id}"))
        def _save_override(settings):
            raw_overrides = settings.get("prompt_overrides") or {}
            overrides = (
                dict(raw_overrides) if isinstance(raw_overrides, dict) else {}
            )
            overrides.setdefault(locale, {})
            overrides[locale] = dict(overrides[locale])
            overrides[locale][layer_def["i18n_key"]] = (
                text_val if text_val.strip() else ""
            )
            settings["prompt_overrides"] = overrides
            return True

        success = await QQAutoReplyPlugin._mutate_business_config(
            self, _save_override,
        )
        if success:
            self.session_instruction_service._discard_all_sessions_for_prompt_change()
        return Ok({"persisted": success, "layer_id": layer_id, "locale": locale})

    async def _config_prompt_reset(self, kw: dict[str, Any]):
        locale = str(kw.get("locale") or "")
        layer_id = str(kw.get("layer_id") or "")
        locale = str(locale or "").strip()
        layer_id = str(layer_id or "").strip()
        if not locale or not layer_id:
            return Err(SdkError("INVALID_INPUT"))
        layer_def = next((ld for ld in self.session_instruction_service._PROMPT_LAYERS if ld["id"] == layer_id), None)
        if layer_def is None or layer_def.get("runtime"):
            return Err(SdkError(f"INVALID_INPUT: 无法重置的层: {layer_id}"))
        override_found = False

        def _reset_override(settings):
            nonlocal override_found
            raw_overrides = settings.get("prompt_overrides") or {}
            overrides = {
                bucket: (dict(entries) if isinstance(entries, dict) else entries)
                for bucket, entries in (
                    raw_overrides.items() if isinstance(raw_overrides, dict) else ()
                )
            }
            i18n_key = layer_def["i18n_key"]
            removed = False

            # 先删精确桶。它可能存着空串（编辑器清空时的存法），resolve 看不见
            # 那种，光靠下面的循环会漏。
            exact = overrides.get(locale)
            if isinstance(exact, dict) and i18n_key in exact:
                exact.pop(i18n_key)
                removed = True

            # 再一直删到「解析不出覆盖」为止。只删精确桶是不够的：候选链上
            # 还有别的桶（存量短码 'zh'，以及每条链都会带上的 'zh-CN' /
            # 'en'），删掉 zh-TW 之后它们会顶上来 —— 「恢复默认」就变成了
            # 「换一份旧覆盖」，而且再按一次还是它。
            # ⚠️ 代价说清楚：同一个人如果按 locale 分别调过这一层的提示词，
            # 重置会把该层其它 locale 的那几份一起清掉。单用户桌面应用里，
            # 这比「按了恢复默认却恢复不掉」轻 —— 后者没有任何出路。
            while True:
                found = resolve_prompt_override(overrides, locale, i18n_key)
                if found is None:
                    break
                overrides[found[0]].pop(i18n_key, None)
                removed = True

            if not removed:
                return False
            override_found = True
            for bucket in [
                b for b, entries in overrides.items()
                if isinstance(entries, dict) and not entries
            ]:
                overrides.pop(bucket, None)
            settings["prompt_overrides"] = overrides
            return True

        success = await QQAutoReplyPlugin._mutate_business_config(
            self, _reset_override,
        )
        if override_found:
            if success:
                self.session_instruction_service._discard_all_sessions_for_prompt_change()
            return Ok({"persisted": success, "layer_id": layer_id, "locale": locale})
        return Ok({"persisted": True, "layer_id": layer_id, "locale": locale, "reason": "no_override_found"})

    async def _config_group_prompt_save(self, kw: dict[str, Any]):
        group_id = str(kw.get("group_id") or "")
        text = kw.get("text")
        """保存某个群的专属提示词。text 为空字符串则视为删除。"""
        gid = str(group_id or "").strip()
        if not gid:
            return Err(SdkError("INVALID_GROUP_ID: group_id 不能为空"))
        custom_text = str(text or "").strip()
        def _save_group_prompt(settings):
            group_prompts = dict(settings.get("group_prompts") or {})
            if custom_text:
                group_prompts[gid] = custom_text
            else:
                group_prompts.pop(gid, None)
            settings["group_prompts"] = group_prompts
            return True

        success = await QQAutoReplyPlugin._mutate_business_config(
            self, _save_group_prompt,
        )
        if success:
            if custom_text:
                self._emit_log(
                    "INFO",
                    f"已保存群 {gid} 的自定义提示词 ({len(custom_text)} 字符)",
                )
            else:
                self._emit_log("INFO", f"已清除群 {gid} 的自定义提示词")
        else:
            self._emit_log(
                "WARNING",
                f"群 {gid} 自定义提示词写盘失败，运行时变更未持久化",
            )
        # 清除该群的当前会话，下次回复时重新注入新提示词
        if self.session_runtime_service:
            discarded = await self._run_with_session_lock(
                f"group:{gid}",
                lambda: self.session_runtime_service.discard_session(f"group:{gid}", reason="group_prompt_changed"),
            )
            if discarded is False:
                self._emit_log("WARNING", f"群 {gid} 会话因记忆结算失败暂未重置，新提示词将在下次会话重建时生效")
        return Ok({"persisted": success, "group_id": gid, "has_text": bool(custom_text)})

    async def _config_group_prompt_delete(self, kw: dict[str, Any]):
        group_id = str(kw.get("group_id") or "")
        """删除某个群的专属提示词。"""
        gid = str(group_id or "").strip()
        if not gid:
            return Err(SdkError("INVALID_GROUP_ID: group_id 不能为空"))
        existed = False

        def _delete_group_prompt(settings):
            nonlocal existed
            group_prompts = dict(settings.get("group_prompts") or {})
            existed = gid in group_prompts
            if not existed:
                return False
            group_prompts.pop(gid)
            settings["group_prompts"] = group_prompts
            return True

        success = await QQAutoReplyPlugin._mutate_business_config(
            self, _delete_group_prompt,
        )
        if existed:
            if self.session_runtime_service:
                discarded = await self._run_with_session_lock(
                    f"group:{gid}",
                    lambda: self.session_runtime_service.discard_session(f"group:{gid}", reason="group_prompt_deleted"),
                )
                if discarded is False:
                    self._emit_log("WARNING", f"群 {gid} 会话因记忆结算失败暂未重置，新提示词将在下次会话重建时生效")
            if success:
                self._emit_log("INFO", f"已删除群 {gid} 的自定义提示词")
            else:
                self._emit_log(
                    "WARNING",
                    f"群 {gid} 自定义提示词删除写盘失败，运行时变更未持久化",
                )
            return Ok({"persisted": success, "group_id": gid, "deleted": True})
        return Ok({"persisted": True, "group_id": gid, "deleted": False, "reason": "not_found"})

    async def _run_message_handler(self, message: Dict[str, Any]) -> None:
        await self.handler_runtime_service.run_message_handler(message)

    # ── config：配置写入 ────────────────────────────────────────
    #
    # 所有持久化设置的写入口。对 agent 可见 —— 改配置是它的正当动作。

    #: 允许透传给 ``dashboard_service.save_settings`` 的键。**白名单，不是全透传。**
    #:
    #: 服务层是关键字参数、逐个 `kwargs.get(...)` 具名写回，而前端会多塞键
    #: （``napcat.html`` 就在发 ``locale``）。老入口靠自己的 `**_` 把它们悄悄吞掉；
    #: 合并后如果直接 `**kw` 转发，多余的键会撞到服务层；而漏在名单外的键会**静默
    #: 丢失** —— 回复缓冲那两个开关正是这么踩的：界面能点，值从没存进去过。
    #:
    #: 名单由 ``settings_schema`` 生成（只收 ``saveable=True`` 的键，含历史别名）。
    #: 加键请改那张表，别在这里手写。
    _CONFIG_SAVE_KEYS = settings_schema.SAVEABLE_KEYS

    @ui.action(id="config", label=tr("entries.config.name", default="保存配置"), refresh_context=True)
    @plugin_entry(
        id="config",
        name=tr("entries.config.name", default="保存配置"),
        description=tr("entries.config.description", default="写入持久化设置与提示词。action 取 save / init / nl / prompt_save / prompt_reset / group_prompt_save / group_prompt_delete / attention_adjust / memory_forget。"),
        input_schema={"type": "object", "properties": {
            "action": {"type": "string",
                       "enum": ["save", "init", "nl", "prompt_save", "prompt_reset",
                                "group_prompt_save", "group_prompt_delete",
                                "attention_adjust", "memory_forget"],
                       "description": "save=保存设置（其余参数见下）；init=初始化配置；nl=用自然语言改 OneBot 地址/token；prompt_save/reset=提示词覆盖；group_prompt_save/delete=群专属提示词；attention_adjust=调群注意力；memory_forget=清除群长期记忆"},
            "message": {"type": "string", "description": "nl：自然语言指令"},
            "locale": {"type": "string", "description": "prompt_save / prompt_reset：语言"},
            "layer_id": {"type": "string", "description": "prompt_save / prompt_reset：提示词层 id"},
            "text": {"type": "string", "description": "prompt_save / group_prompt_save：内容（群提示词传空串=删除）"},
            "group_id": {"type": "string", "description": "group_prompt_save / group_prompt_delete / attention_adjust / memory_forget：群号"},
            "delta": {"type": "number", "description": "attention_adjust：正数加分、负数扣分"},
            # ── action="save" 的键由 settings_schema.input_schema_properties() 生成。
            #    加键只改那张表 —— 过去这里要和白名单、save_settings 签名三处手工对齐，
            #    漏一处就静默失效。
            **settings_schema.input_schema_properties(),
        }, "required": ["action"], "additionalProperties": False},
    )
    # ⚠️ 方法名**不能**叫 `config`：基类在 `plugin/sdk/shared/core/base.py:65`
    # 把 `self.config` 设成了 PluginConfig 实例属性，而 `collect_entries()`
    # 是按**属性名**取处理函数的（`getattr(self, attr_name)`）—— 同名会让入口
    # 的 handler 解析成那个 PluginConfig 对象，宿主报
    # "Entry 'config' must be 'async def'. Sync entries are not supported."
    #
    # 入口 id 与属性名本来就不必相同（host 认的是 meta.id），所以只改方法名即可，
    # 对外的 `call('config', ...)` 不受影响。`test_qq_entry_dispatch.py` 里有一条
    # 用例专门守这个撞名。
    async def config_entry(self, action: str = "", **kw):
        return await self._config_dispatch(str(action or "").strip(), kw)

    async def _config_dispatch(self, action: str, kw: dict[str, Any]):
        self._kick_deferred_startup_tasks()
        if action == "save":
            return await self._config_save(kw)
        if action == "init":
            return await self._config_init(kw)
        if action == "nl":
            return await self._config_nl(kw)
        if action == "prompt_save":
            return await self._config_prompt_save(kw)
        if action == "prompt_reset":
            return await self._config_prompt_reset(kw)
        if action == "group_prompt_save":
            return await self._config_group_prompt_save(kw)
        if action == "group_prompt_delete":
            return await self._config_group_prompt_delete(kw)
        if action == "attention_adjust":
            return await self._config_attention_adjust(kw)
        if action == "memory_forget":
            return await self._config_memory_forget(kw)
        return Err(SdkError(
            f"BAD_ACTION: config 不支持 {action!r}"
            f"（可选 save/init/nl/prompt_save/prompt_reset/group_prompt_save/"
            f"group_prompt_delete/attention_adjust/memory_forget）"))

    async def _config_save(self, kw: dict[str, Any]):
        """保存设置。只认白名单里的键 —— 多余的**记一条日志**后丢弃。

        以前是纯静默丢弃，这是本插件反复出现的故障形态：调用方（前端表单、
        调试脚本、`proactive_topics` 这种走错了入口的键）以为保存成功，实际
        整键消失且没有任何痕迹可查。丢弃本身是必须的（不能撞到服务层），
        但"丢了什么"必须留痕。

        **但宿主塞进来的信封键不算"手滑"**：调用插件 entry 的那两条宿主路径都会
        往参数里加一个下划线开头的信封 ——

        * `plugin/server/application/plugins/ui_query_service.py:1756-1762`：
          hosted UI action 触发时加 `_ctx`（含 `run_id`）；
        * `brain/task_executor.py:2329-2348`：agent 派发调用时加 `_ctx`
          （含 `lanlan_name` / `conversation_id` / `latest_user_request` / `entry_timeout`）。

        它们以前会被算进"不可识别的键"，于是**每次保存都报一条警告**
        （真机 17:59:37 那条 `丢弃了 1 个不可识别的键: ['_ctx']`）—— 噪音会把真正的
        手滑键名淹掉，正好毁掉这条日志存在的意义。所以下划线开头的信封键不进 `dropped`；
        只有信封、没有真手滑时**一声不响**。
        """
        payload = {k: v for k, v in kw.items() if k in self._CONFIG_SAVE_KEYS}
        envelopes = sorted(k for k in kw if k.startswith("_"))
        dropped = sorted(
            k for k in kw
            if k not in self._CONFIG_SAVE_KEYS and k != "action" and not k.startswith("_")
        )
        if dropped:
            # 双写：ring 给界面看，文件日志给事后复盘（`_emit_log` 只进 ring，
            # 而 ring 在插件重载时清空 —— 这条警告就这么"查不到"过一次）。
            emit_bridge_log(
                self,
                "WARNING",
                f"[Config] save 丢弃了 {len(dropped)} 个不可识别的键: {dropped}"
                f"（可用键 {len(self._CONFIG_SAVE_KEYS)} 个；"
                f"注意有些键有专用入口（例如 group_prompts 走 group_prompt_save））",
            )
        if not payload:
            return Err(SdkError(
                "INVALID_INPUT: save 没收到任何可识别的设置项"
                f"（可用键 {len(self._CONFIG_SAVE_KEYS)} 个，见 config 入口的 input_schema）"
                + (f"；被丢弃的键: {dropped + envelopes}"
                   if (dropped or envelopes) else "")))
        return await self.dashboard_service.save_settings(**payload)

    async def _deploy_ensure(self, kw: dict[str, Any]):
        _ = kw
        """仅启动 NapCat 进程，不连接"""
        await self._ensure_napcat_started()
        # 硬失败（目录缺失/启动器缺失/进程拉起失败）→ 明确报错，不返回
        # 「已启动」假象，也不让前端反复重试（ensure_napcat_started 已短路）。
        if self.napcat_service.has_hard_startup_error():
            return Err(SdkError(f"NAPCAT_START_FAILED: {self.napcat_service.get_startup_error()}"))
        ready = await self.napcat_service.wait_for_onebot_ready()
        if ready:
            await self._sync_napcat_qrcode_into_static()
            return Ok({"status": "napcat_ready"})
        return Ok({"status": "napcat_started", "onebot_ready": False})

    async def _deploy_one_click(self, kw: dict[str, Any]):
        uin = str(kw.get("uin") or "").strip()
        force = bool(kw.get("force", False))
        auto_start = bool(kw.get("auto_start", True))
        """一键部署：出二维码 → 启动自动回复"""
        try:
            result = await self.deploy_service.deploy(
                uin=str(uin or "").strip(),
                force=bool(force),
                # 自动回复由 deploy 自己**在启动 NapCat 之前**拉起（反向模式下先竖起
                # 监听，NapCat 起来就能拨进来）。这里只把开关传下去，别再启一次 ——
                # 那会白停一次刚建好的连接。
                auto_start=bool(auto_start),
                # 经 SSE 推给界面（status.html 订阅 deploy_progress 渲染进度）
                emit=lambda p: self._spawn_push_ui_event(
                    "deploy_progress", p.get("message", ""), data=p,
                ),
            )
        except Exception as e:
            self.logger.error(f"一键部署失败: {e}")
            return Err(SdkError(f"DEPLOY_FAILED: {e}"))
        return Ok({**result})   # 收尾字段由 deploy 一并带回

    async def _deploy_apply_onebot(self, kw: dict[str, Any]):
        uin = str(kw.get("uin") or "").strip()
        # 默认**不重启** —— NapCat 热读 OneBot 配置（重启会掐断刚建立的登录会话）
        restart = bool(kw.get("restart", False))
        auto_start = bool(kw.get("auto_start", True))
        """扫码后的兜底：补写配置 → 重启 NapCat → 按新配置启动自动回复"""
        try:
            result = await self.deploy_service.apply_onebot_config(
                uin=str(uin or "").strip(),
                restart=bool(restart),
                emit=lambda p: self._spawn_push_ui_event(
                    "deploy_progress", p.get("message", ""), data=p,
                ),
            )
        except Exception as e:
            self.logger.error(f"补写 OneBot 配置失败: {e}")
            return Err(SdkError(f"APPLY_CONFIG_FAILED: {e}"))
        # 配置刚被改写、NapCat 刚重启 —— 运行中的连接还指着旧端点（连接对象也是按旧
        # 设置建的），必须丢掉重建，否则"配置写对了却连不上"。
        return Ok({**result,
                   **self._auto_start_fields(
                       await self._restart_auto_reply_runtime(bool(auto_start)))})

    async def _deploy_login_poll(self, kw: dict[str, Any]):
        """轮询扫码登录是否完成；完成就把收尾做完。"""
        return await self.deploy_service.poll_login(
            auto_start=bool(kw.get("auto_start", True)),
            emit=lambda p: self._spawn_push_ui_event(
                "deploy_progress", p.get("message", ""), data=p))

    async def _deploy_bind_start(self, kw: dict[str, Any]):
        _ = kw
        """开始扫码绑定。

        **不做拦截**：新建还是绑定已有，是用户在手机连接页上选的，本地无从决定 ——
        原先那个 ``force`` 参数建立在一个错误前提上（以为服务端能"强制新建"）。
        账本里已记过的机器人只作为**提示**回传（``reusable_appid``），供界面提醒用户
        扫码时选「已有的机器人」而不是新建 —— 扫码会轮换 AppSecret，能不重扫就不重扫。
        """
        from . import qq_official_bind as bind

        reusable = bind.pick_reusable_bot(self._qq_settings)
        try:
            session = await bind.create_bind_task()
        except Exception as e:
            self.logger.error(f"创建绑定任务失败: {e}")
            return Err(SdkError(f"BIND_START_FAILED: {e}"))

        # 会话只放内存：bind_key 是本次解密用的临时密钥，不该落盘。
        self._qq_bind_session = session
        # 服务端渲染成 PNG，前端直接 <img> 取（与 NapCat 登录二维码同一条静态路径）。
        qr_file = self.config_dir / "static" / "cache" / "bind_qrcode.png"
        rendered = bind.render_qr_png(session.qrcode, qr_file)
        return Ok({
            "task_id": session.task_id,
            "qrcode": session.qrcode,
            "qrcode_ready": rendered,
            "qrcode_url": (
                f"/plugin/{self.plugin_id}/ui/cache/bind_qrcode.png" if rendered else ""
            ),
            "interval": session.interval,
            "existing_bots": len(bind.list_bots(self._qq_settings)),
            # 提示用：扫码时选「已有的机器人」就会复用它，不必新建
            "reusable_appid": str((reusable or {}).get("appid") or ""),
        })

    async def _deploy_bind_poll(self, kw: dict[str, Any]):
        auto_start = bool(kw.get("auto_start", True))
        from . import qq_official_bind as bind

        session = getattr(self, "_qq_bind_session", None)
        if session is None:
            return Err(SdkError("NO_BIND_TASK: 请先调用 qq_official_bind_start"))
        try:
            result = await bind.poll_bind_result(session)
        except Exception as e:
            self.logger.error(f"轮询绑定结果失败: {e}")
            return Err(SdkError(f"BIND_POLL_FAILED: {e}"))
        if result.get("status") != "completed":
            return Ok(result)

        appid, secret = str(result["appid"]), str(result["secret"])
        record = bind.remember_bot(self._qq_settings, appid=appid, secret=secret)
        # 连接器读的就是这两个键 —— 记账的同时让它立刻可用。
        self._qq_settings["qq_open_app_id"] = appid
        self._qq_settings["qq_open_client_secret"] = secret
        self._qq_bind_session = None

        # 扫码者 = 主人。绑定回包里的 ``user_openid`` 是**平台**给出的、扫码那个人
        # 的 openid —— 这是唯一一个"谁是主人"能被平台证明的时刻，比事后靠
        # "第一个私聊的人"去猜可靠得多（那条 bootstrap 只在名单为空时生效一次）。
        #
        # 开放平台拿不到 QQ 号，只给 openid，所以信任名单里存的就是 openid；
        # 同一个人在不同群的 openid 可能不同，那部分仍要人工合并身份
        # （bind_identity_account 刻意不自动做）。
        admin_openid = str(result.get("user_openid") or "").strip()
        if admin_openid:
            if self.permission_mgr and self.permission_mgr.add_user(admin_openid, "admin"):
                self._refresh_admin_qq()
                self.logger.info(f"[绑定] 已把扫码者设为管理员: {admin_openid}")
            else:
                self.logger.warning(f"[绑定] 扫码者 openid 无效，未能设为管理员: {admin_openid!r}")

        try:
            await self.settings_service.persist_business_config()
        except Exception as e:
            self.logger.warning(f"机器人凭据已获取但落盘失败: {e}")

        # 先落盘、再校验 —— 顺序不能反。绑定会轮换 AppSecret（实测），所以走到这里时
        # **旧密钥已经作废**："验不过就不写"等于把配置停在一个确定失效的值上，比写了更糟。
        # 校验的产物是给用户看的结论，不是落盘的前提。
        verify = await bind.verify_credentials(appid, secret)
        if verify["ok"]:
            self.logger.info(f"[绑定] 凭据已校验可用（第 {verify['attempts']} 次）")
        else:
            self.logger.warning(
                f"[绑定] 凭据未通过校验（旧密钥此时已被轮换，新值仍已写入配置）: "
                f"{verify['error']}")

        auto = await self._restart_auto_reply_runtime(bool(auto_start))

        return Ok({"status": "completed", "appid": appid,
                   "verified": verify["ok"],
                   "verify_attempts": verify["attempts"],
                   "verify_error": verify["error"],
                   **self._auto_start_fields(auto),
                   "bot": {k: v for k, v in record.items() if k != "secret"}})

    async def _deploy_use_bot(self, kw: dict[str, Any]):
        appid = str(kw.get("appid") or "")
        from . import qq_official_bind as bind

        bot = bind.find_bot(self._qq_settings, appid)
        if bot is None:
            return Err(SdkError(f"BOT_NOT_FOUND: 账本里没有 {appid}"))
        self._qq_settings["qq_open_app_id"] = str(bot.get("appid") or "")
        self._qq_settings["qq_open_client_secret"] = str(bot.get("secret") or "")
        self._qq_settings[bind.ACTIVE_KEY] = str(bot.get("appid") or "")
        try:
            await self.settings_service.persist_business_config()
        except Exception as e:
            self.logger.warning(f"切换机器人后落盘失败: {e}")
        return Ok({"status": "switched", "appid": self._qq_settings["qq_open_app_id"]})

    async def _deploy_qrcode_sync(self, kw: dict[str, Any]):
        _ = kw
        return await self.dashboard_service.sync_qrcode()

    # ── deploy：接入与部署 ──────────────────────────────────────
    #
    # NapCat 进程的起停、OneBot 配置补写、开放平台扫码绑定与账本切换。
    #
    # **对 agent 隐藏**（`agent_auto: False`，见 brain/task_executor.py 的
    # `_is_plugin_entry_agent_hidden`）：这里装着不该让模型自己碰的东西 ——
    # `one_click` 会为了注入而**杀掉正在运行的 QQ**，`bind_*` 会轮换 AppSecret。
    # 标记链路已验：query_service 把 entry.metadata 原样放进入口清单，brain 读它。

    @ui.action(id="deploy", label=tr("entries.deploy.name", default="接入与部署"), refresh_context=True)
    @plugin_entry(
        id="deploy",
        name=tr("entries.deploy.name", default="接入与部署"),
        description=tr("entries.deploy.description", default="启动 NapCat、一键部署、补写 OneBot 配置、开放平台扫码绑定与机器人切换。action 取 ensure / one_click / apply_onebot / bind_start / bind_poll / use_bot / qrcode_sync。**此入口对 AI 隐藏，只能由界面或人触发。**"),
        input_schema={"type": "object", "properties": {
            "action": {"type": "string",
                       "enum": ["ensure", "one_click", "apply_onebot",
                                "login_poll",
                                "bind_start", "bind_poll", "use_bot", "qrcode_sync"],
                       "description": "ensure=只启动 NapCat 进程；one_click=一键部署（下载/解包/写配置/启动/出码）；apply_onebot=扫码后补写 OneBot 配置并重启；login_poll=轮询「扫码登录成功了没」，成功就顺手做完收尾（前端在一键部署后定时调它）；bind_start/bind_poll=开放平台扫码绑定与轮询；use_bot=切换到账本里已有的机器人；qrcode_sync=刷新登录二维码"},
            "uin": {"type": "string", "description": "one_click / apply_onebot：机器人 QQ 号（apply_onebot 留空则取最近登录的）"},
            "force": {"type": "boolean", "default": False, "description": "one_click：已有 NapCat 也强制重新下载"},
            "auto_start": {"type": "boolean", "default": True,
                           "description": "one_click / apply_onebot / bind_poll：完事顺手把自动回复跑起来（会按新设置停掉重建）"},
            "restart": {"type": "boolean", "default": False, "description": "apply_onebot：写完是否重启 NapCat（默认不重启 —— NapCat 热读 OneBot 配置；重启会掐断刚建立的登录会话）"},
            "appid": {"type": "string", "description": "use_bot：要启用的机器人 AppID"},
        }, "required": ["action"], "additionalProperties": False},
        # 必须显式声明：宿主默认的 ``PLUGIN_EXECUTION_TIMEOUT`` 只有 30 秒，而
        # ``one_click`` 要下载 28MB 再解包 690 个文件 —— 连 ``napcat_install``
        # 自己给下载的 ``DOWNLOAD_TIMEOUT_SECONDS`` 都是 300 秒。不声明的话，
        # 一次稍慢的下载就会撞上 `Entry 'deploy' timed out after 30.0s`，
        # 而且报出来只是个语焉不详的 failed（后端 error 没往界面上带）。
        #
        # 300 是对齐出来的值：UI 发起的 run 另有 ``RUN_EXECUTION_TIMEOUT``（默认 300）
        # 兜底，声明得比它更大没有意义；前端对应 ``static/status.html`` 的
        # DEPLOY_TIMEOUT_MS。
        timeout=300,
        metadata={"agent_auto": False},
    )
    async def deploy(self, action: str = "", **kw):
        return await self._deploy_dispatch(str(action or "").strip(), kw)

    async def _deploy_dispatch(self, action: str, kw: dict[str, Any]):
        self._kick_deferred_startup_tasks()
        if action == "ensure":
            return await self._deploy_ensure(kw)
        if action == "one_click":
            return await self._deploy_one_click(kw)
        if action == "apply_onebot":
            return await self._deploy_apply_onebot(kw)
        if action == "login_poll":
            return await self._deploy_login_poll(kw)
        if action == "bind_start":
            return await self._deploy_bind_start(kw)
        if action == "bind_poll":
            return await self._deploy_bind_poll(kw)
        if action == "use_bot":
            return await self._deploy_use_bot(kw)
        if action == "qrcode_sync":
            return await self._deploy_qrcode_sync(kw)
        return Err(SdkError(
            f"BAD_ACTION: deploy 不支持 {action!r}"
            f"（可选 ensure/one_click/apply_onebot/login_poll/bind_start/"
            f"bind_poll/use_bot/qrcode_sync）"))

    async def _asset_attention(self, kw: dict[str, Any]):
        _ = kw
        if not self.attention_service:
            return Ok({"enabled": False, "groups": [], "focus_group_id": "", "global_sleep": False})
        snapshot = self.attention_service.get_snapshot()
        return Ok({
            "enabled": snapshot.get("enabled", False),
            "focus_group_id": snapshot.get("focus_group_id", ""),
            "focus_score": snapshot.get("focus_score", 0.0),
            "global_sleep": self.attention_service.is_global_sleep(),
            "groups": snapshot.get("groups", []),
        })

    async def _asset_list_stickers(self, kw: dict[str, Any]):
        _ = kw
        """列出所有已注册表情包"""
        sticker_json = str(self.data_path("sticker.json"))
        try:
            with open(sticker_json, "r", encoding="utf-8") as f:
                data = json.loads(f.read())
        except Exception:
            data = {}
        if not isinstance(data, dict):
            data = {}
        items = []
        for sid, info in data.items():
            items.append({
                "id": sid,
                "desc": info.get("desc", "") if isinstance(info, dict) else str(info),
                "path": info.get("path", "") if isinstance(info, dict) else "",
            })
        return Ok({"stickers": items, "total": len(items)})

    async def _asset_register_sticker(self, kw: dict[str, Any]):
        image_path = str(kw.get("image_path") or "")
        desc = str(kw.get("desc") or "")
        """注册表情包到 sticker.json"""
        import os
        path = str(image_path or "").strip()
        description = str(desc or "").strip()
        if not path:
            return Err(SdkError("INVALID_INPUT: image_path 不能为空"))
        if not description:
            return Err(SdkError("INVALID_INPUT: desc 不能为空"))
        sticker_json = str(self.data_path("sticker.json"))
        sticker_dir = str(self.data_path("sticker"))
        full_path = os.path.join(sticker_dir, path)
        if not os.path.exists(full_path):
            return Err(SdkError(f"NOT_FOUND: 图片文件不存在: data/sticker/{path}"))
        try:
            with open(sticker_json, "r", encoding="utf-8") as f:
                data = json.loads(f.read())
        except Exception:
            data = {}
        if not isinstance(data, dict):
            data = {}
        next_id = 1
        while str(next_id) in data:
            next_id += 1
        sid = str(next_id)
        data[sid] = {"desc": description, "path": path}
        os.makedirs(os.path.dirname(sticker_json), exist_ok=True)
        with open(sticker_json, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        self.session_instruction_service._sticker_catalog_cache = ""
        self.logger.info(f"注册表情包: id={sid}, path={path}, desc={description}")
        return Ok({"id": sid, "desc": description, "path": path, "total": len(data)})

    async def _asset_upload_sticker(self, kw: dict[str, Any]):
        filename = str(kw.get("filename") or "")
        data_base64 = kw.get("data_base64")
        desc = str(kw.get("desc") or "")
        """上传表情包图片并注册"""
        import base64 as b64
        import json as _json
        import os as _os
        fname = str(filename or "").strip()
        description = str(desc or "").strip()
        raw_b64 = str(data_base64 or "").strip()
        #: 传了就让 VLM 生成描述（覆盖 desc）；没传 / 解析失败则沿用 desc。
        auto_desc = bool(kw.get("auto_desc"))
        if not fname:
            return Err(SdkError("INVALID_INPUT: filename 不能为空"))
        if not raw_b64:
            return Err(SdkError("INVALID_INPUT: data_base64 不能为空"))
        if not description:
            return Err(SdkError("INVALID_INPUT: desc 不能为空"))
        # 走 SDK 状态根（data_path）；__file__ 相对路径指向代码根，迁移后写不到存档
        sticker_dir = str(self.data_path("sticker"))
        sticker_json = str(self.data_path("sticker.json"))
        _os.makedirs(sticker_dir, exist_ok=True)
        # 处理 base64（可能带 data:image/...;base64, 前缀）
        if "," in raw_b64 and raw_b64.startswith("data:"):
            raw_b64 = raw_b64.split(",", 1)[1]
        # 安全检查：文件名只保留安全字符
        safe_name = "".join(c for c in fname if c.isalnum() or c in "._-")
        if not safe_name:
            safe_name = "sticker.png"
        # 避免重名
        base, ext = _os.path.splitext(safe_name)
        if not ext:
            ext = ".png"
        dest_name = safe_name
        counter = 1
        while _os.path.exists(_os.path.join(sticker_dir, dest_name)):
            dest_name = f"{base}_{counter}{ext}"
            counter += 1
        dest_path = _os.path.join(sticker_dir, dest_name)
        try:
            img_bytes = b64.b64decode(raw_b64)
        except Exception as e:
            return Err(SdkError(f"DECODE_FAILED: base64 解码失败: {e}"))
        with open(dest_path, "wb") as f:
            f.write(img_bytes)
        # 注册到 sticker.json
        try:
            with open(sticker_json, "r", encoding="utf-8") as f:
                data = _json.loads(f.read())
        except Exception:
            data = {}
        if not isinstance(data, dict):
            data = {}
        next_id = 1
        while str(next_id) in data:
            next_id += 1
        sid = str(next_id)
        data[sid] = {"desc": description, "path": dest_name}
        with open(sticker_json, "w", encoding="utf-8") as f:
            _json.dump(data, f, ensure_ascii=False, indent=2)
        self.session_instruction_service._sticker_catalog_cache = ""

        # 自动描述：**先注册再升级**。VLM 失败（模型没配 / 超时 / 返回空）时，
        # 上面那条用 desc/文件名兜底的登记仍然有效 —— 不会因为一次模型抖动就丢图。
        vlm_used = False
        if auto_desc:
            vlm_desc = await self._vlm_describe_locator(
                str(dest_path), prompt=STICKER_VLM_PROMPT, max_tokens=80,
            )
            if vlm_desc:
                data[sid] = {"desc": vlm_desc, "path": dest_name}
                with open(sticker_json, "w", encoding="utf-8") as f:
                    _json.dump(data, f, ensure_ascii=False, indent=2)
                self.session_instruction_service._sticker_catalog_cache = ""
                description = vlm_desc
                vlm_used = True
                self.logger.info(f"[VLM] 表情包自动描述: id={sid}, desc={vlm_desc}")
            else:
                self.logger.info(f"[VLM] 表情包自动描述返回空，沿用兜底描述: id={sid}")

        self.logger.info(f"上传表情包: id={sid}, file={dest_name}, desc={description}, vlm={vlm_used}")
        return Ok({"id": sid, "desc": description, "path": dest_name,
                   "total": len(data), "vlm_used": vlm_used})

    async def _asset_describe_sticker(self, kw: dict[str, Any]):
        """对一张**已注册**的表情包跑 VLM，把描述写回 sticker.json。

        `upload_sticker` 的 `auto_desc` 走的是同一段逻辑；这条单独开出来是为了
        能对**以前传的**表情包补描述（那些的描述多半还是文件名）。
        """
        import os as _os

        sid = str(kw.get("id") or "").strip()
        if not sid:
            return Err(SdkError("INVALID_INPUT: id 不能为空"))
        sticker_json = str(self.data_path("sticker.json"))
        sticker_dir = str(self.data_path("sticker"))
        try:
            with open(sticker_json, "r", encoding="utf-8") as f:
                data = json.loads(f.read())
        except Exception:
            data = {}
        if not isinstance(data, dict) or sid not in data:
            return Err(SdkError(f"NOT_FOUND: 没有 id={sid} 的表情包"))
        entry = data[sid]
        raw_path = entry.get("path", "") if isinstance(entry, dict) else ""
        safe_name = _os.path.basename(str(raw_path).replace("\\", "/"))
        full_path = _os.path.join(sticker_dir, safe_name)
        if not safe_name or not _os.path.isfile(full_path):
            return Err(SdkError(f"NOT_FOUND: 图片文件不存在: data/sticker/{safe_name}"))

        desc = await self._vlm_describe_locator(full_path, prompt=STICKER_VLM_PROMPT, max_tokens=80)
        if not desc:
            # 空结果要**报错**而不是静默保留旧描述：界面上点了"重新解析"却什么都没变，
            # 用户会以为是自己没点到。多半是模型没配或没返回内容。
            return Err(SdkError(
                "VLM_FAILED: 没能解析出描述（对话模型未配置、不支持看图，或返回为空）"))

        previous = entry.get("desc", "") if isinstance(entry, dict) else ""
        data[sid] = {"desc": desc, "path": raw_path}
        with open(sticker_json, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        self.session_instruction_service._sticker_catalog_cache = ""
        self.logger.info(f"[VLM] 表情包重新描述: id={sid}, {previous!r} -> {desc!r}")
        return Ok({"id": sid, "desc": desc, "previous": previous})

    async def _asset_delete_sticker(self, kw: dict[str, Any]):
        """删掉一个已注册表情包：先从 sticker.json 摘掉登记，再删磁盘上的图。

        **顺序是刻意的**：先摘登记、再删文件。万一删文件失败（被占用 / 权限），
        登记已经没了，猫娘不会再引用一张不存在的图；反过来则会留下悬空登记 ——
        那比留个孤儿文件糟得多。
        """
        import os as _os

        sid = str(kw.get("id") or "").strip()
        if not sid:
            return Err(SdkError("INVALID_INPUT: id 不能为空"))
        sticker_json = str(self.data_path("sticker.json"))
        sticker_dir = str(self.data_path("sticker"))
        try:
            with open(sticker_json, "r", encoding="utf-8") as f:
                data = json.loads(f.read())
        except Exception:
            data = {}
        if not isinstance(data, dict):
            data = {}
        if sid not in data:
            return Err(SdkError(f"NOT_FOUND: 没有 id={sid} 的表情包"))

        entry = data.pop(sid)
        raw_path = entry.get("path", "") if isinstance(entry, dict) else ""
        with open(sticker_json, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        removed_file = False
        # 同一个文件可能被登记了多次（register_sticker 允许这么做）；
        # 还有人引用就别删图，否则会把别人那条表情包一起弄坏。
        still_used = any(
            (v.get("path", "") if isinstance(v, dict) else "") == raw_path
            for v in data.values()
        )
        # 只取 basename 并统一分隔符：sticker.json 是本地文件，理论上可能被手改，
        # 不能让 "../../x" 这种值逃出表情包目录。
        safe_name = _os.path.basename(str(raw_path).replace("\\", "/"))
        if safe_name and not still_used:
            target = _os.path.join(sticker_dir, safe_name)
            try:
                if _os.path.isfile(target):
                    _os.remove(target)
                    removed_file = True
            except Exception as e:
                # 文件删不掉不影响这次调用的结果：登记已经摘掉了
                self.logger.warning(f"删除表情包文件失败（登记已摘除）: {target}: {e}")

        self.session_instruction_service._sticker_catalog_cache = ""
        self.logger.info(f"删除表情包: id={sid}, path={raw_path}, 文件已删={removed_file}")
        return Ok({"id": sid, "path": raw_path, "removed_file": removed_file, "total": len(data)})

    # ── asset：机器人自有资源 ───────────────────────────────────
    #
    # 表情包目录与注意力读数。
    #
    # **对 agent 隐藏**（`agent_auto: False`）：这里是资源写入面（存图、写
    # sticker.json），不该让模型自己动手。注意力**写入**不在这儿 —— 它在
    # `config(action="attention_adjust")`，那条对 agent 是可见的。

    @ui.action(id="asset", label=tr("entries.asset.name", default="表情包与注意力"), refresh_context=True)
    @plugin_entry(
        id="asset",
        name=tr("entries.asset.name", default="表情包与注意力"),
        description=tr("entries.asset.description", default="表情包目录的读写与注意力读数。action 取 list_stickers / register_sticker / upload_sticker / describe_sticker / delete_sticker / attention。**此入口对 AI 隐藏。**"),
        input_schema={"type": "object", "properties": {
            "action": {"type": "string",
                       "enum": ["list_stickers", "register_sticker", "upload_sticker", "describe_sticker", "delete_sticker", "attention"],
                       "description": "list_stickers=列出已注册表情包；register_sticker=登记磁盘上已有的图片；upload_sticker=上传 base64 图片并存档；describe_sticker=用 VLM 重新解析某张表情包的描述；delete_sticker=删除一个已注册表情包（连图一起删）；attention=读群注意力状态"},
            "id": {"type": "string", "description": "describe_sticker / delete_sticker：表情包 id（取自 list_stickers）"},
            "auto_desc": {"type": "boolean", "description": "upload_sticker：为 true 时用 VLM 自动生成描述并覆盖 desc；VLM 失败则沿用 desc"},
            "image_path": {"type": "string", "description": "register_sticker：data/sticker/ 下的图片文件名"},
            "filename": {"type": "string", "description": "upload_sticker：文件名（如 cat.png）"},
            "data_base64": {"type": "string", "description": "upload_sticker：图片 base64（可带 data:image/...;base64, 前缀）"},
            "desc": {"type": "string", "description": "register_sticker / upload_sticker：描述，LLM 按它挑图"},
        }, "required": ["action"], "additionalProperties": False},
        metadata={"agent_auto": False},
    )
    async def asset(self, action: str = "", **kw):
        return await self._asset_dispatch(str(action or "").strip(), kw)

    async def _asset_dispatch(self, action: str, kw: dict[str, Any]):
        if action == "list_stickers":
            return await self._asset_list_stickers(kw)
        if action == "register_sticker":
            return await self._asset_register_sticker(kw)
        if action == "upload_sticker":
            return await self._asset_upload_sticker(kw)
        if action == "describe_sticker":
            return await self._asset_describe_sticker(kw)
        if action == "delete_sticker":
            return await self._asset_delete_sticker(kw)
        if action == "attention":
            return await self._asset_attention(kw)
        return Err(SdkError(
            f"BAD_ACTION: asset 不支持 {action!r}"
            f"（可选 list_stickers/register_sticker/upload_sticker/describe_sticker"
            f"/delete_sticker/attention）"))

    # ── send：收发 ──────────────────────────────────────────────
    #
    # 出站消息 + backlog 处理。**对 agent 可见** —— 其它插件也经它发消息
    # （`call_entry("qq_auto_reply:send", {"action": "group", ...})`）。

    @plugin_entry(
        id="send",
        name=tr("entries.send.name", default="发送消息"),
        description=tr("entries.send.description", default="主动发私聊/群聊消息，或处理待审阅消息。action 取 private / group / backlog_reply / backlog_review。"),
        input_schema={"type": "object", "properties": {
            "action": {"type": "string",
                       "enum": ["private", "group", "backlog_reply", "backlog_review"],
                       "description": "private=发私聊；group=发群聊；backlog_reply=回复一条待审阅消息；backlog_review=把某群标记为已处理"},
            "target": {"type": "string", "description": "private：目标 QQ 号"},
            "group_id": {"type": "string", "description": "group / backlog_review：群号"},
            "message": {"type": "string", "description": "private / group：消息内容"},
            "verbatim": {"type": "boolean", "default": False,
                         "description": "private / group：true 则原文直发，不经过 LLM 生成"},
            "source_type": {"type": "string", "description": "backlog_reply：来源类型"},
            "target_id": {"type": "string", "description": "backlog_reply：目标 id"},
            "sender_id": {"type": "string", "description": "backlog_reply：发件人（选填）"},
            "message_id": {"type": "string", "description": "backlog_reply：原消息 id（选填）"},
            "original_message": {"type": "string", "description": "backlog_reply：被回复的原文"},
            "reply_text": {"type": "string", "description": "backlog_reply：要回复的内容"},
        }, "required": ["action"], "additionalProperties": False},
        metadata={"timeout": 90},
    )
    async def send(self, action: str = "", **kw):
        return await self._send_dispatch(str(action or "").strip(), kw)

    async def _send_dispatch(self, action: str, kw: dict[str, Any]):
        if action == "private":
            return await self._send_private(kw)
        if action == "group":
            return await self._send_group(kw)
        if action == "backlog_reply":
            return await self._send_backlog_reply(kw)
        if action == "backlog_review":
            return await self._send_backlog_review(kw)
        return Err(SdkError(
            f"BAD_ACTION: send 不支持 {action!r}"
            f"（可选 private/group/backlog_reply/backlog_review）"))

    async def _send_private(self, kw: dict[str, Any]):
        """给指定用户发一条私聊消息（内容由 LLM 生成，verbatim 则原文直发）。"""
        target = str(kw.get("target") or "").strip()
        message = str(kw.get("message") or "")
        if not target or not message:
            return Err(SdkError("INVALID_INPUT: private 需要 target 与 message"))
        # JSON 来的 verbatim 可能是字符串（"false" 是真值），统一转 bool
        return await self.proactive_message_service.send_private_message(
            target=target, message=message, verbatim=bool(kw.get("verbatim", False)))

    async def _send_group(self, kw: dict[str, Any]):
        """给指定群发一条消息（内容由 LLM 生成，verbatim 则原文直发）。"""
        group_id = str(kw.get("group_id") or "").strip()
        message = str(kw.get("message") or "")
        if not group_id or not message:
            return Err(SdkError("INVALID_INPUT: group 需要 group_id 与 message"))
        return await self.proactive_message_service.send_group_message(
            group_id=group_id, message=message, verbatim=bool(kw.get("verbatim", False)))

    async def _send_backlog_reply(self, kw: dict[str, Any]):
        """把填写的内容直接回复到那条 QQ 消息（发送后顺带把该群标记为已处理）。"""
        source_type = str(kw.get("source_type") or "")
        target_id = str(kw.get("target_id") or "")
        original_message = str(kw.get("original_message") or "")
        reply_text = str(kw.get("reply_text") or "")
        if not (source_type and target_id and original_message and reply_text):
            return Err(SdkError(
                "INVALID_INPUT: backlog_reply 需要 source_type / target_id / "
                "original_message / reply_text"))
        return await self.relay_service.send_backlog_reply_direct(
            source_type=source_type,
            target_id=target_id,
            original_message=original_message,
            reply_text=reply_text,
            sender_id=str(kw.get("sender_id") or ""),
            message_id=str(kw.get("message_id") or ""),
        )

    async def _send_backlog_review(self, kw: dict[str, Any]):
        """把这个群当前所有待处理消息标记为已处理。"""
        # 注意：_validate_group_id 是 raise 而不是返回 Err（targets.py），
        # 这里沿用原行为未改 —— 非法 group_id 仍会以异常形式冒泡。
        normalized_group_id = self._validate_group_id(str(kw.get("group_id") or ""))
        return Ok(await self.backlog_service.mark_group_reviewed_payload(normalized_group_id))


    # ── runtime：启停与连接方式 ─────────────────────────────────
    #
    # 三个动作合成一个入口，靠 `action` 分流。入口只做归一化，真实逻辑在 _runtime_* 里。

    @plugin_entry(
        id="runtime",
        name=tr("entries.runtime.name", default="运行时控制"),
        description=tr("entries.runtime.description", default="启停自动回复、切换连接方式。action 取 start / stop / set_mode。"),
        input_schema={"type": "object", "properties": {
            "action": {"type": "string", "enum": ["start", "stop", "set_mode"],
                       "description": "start=启动自动回复；stop=停止；set_mode=切换连接方式（另需 mode）"},
            "mode": {"type": "string", "enum": list(CONNECTION_MODES),
                     "description": "仅 set_mode 用：napcat / napcat_forward / open_platform"},
            "restart": {"type": "boolean", "default": True,
                        "description": "仅 set_mode 用：正在运行时是否按新模式重连；关掉则只存设置"},
        }, "required": ["action"], "additionalProperties": False},
    )
    async def runtime(self, action: str = "", **kw):
        return await self._runtime_dispatch(str(action or "").strip(), kw)

    async def _runtime_dispatch(self, action: str, kw: dict[str, Any]):
        self._kick_deferred_startup_tasks()
        if action == "start":
            return await self._runtime_start(kw)
        if action == "stop":
            return await self._runtime_stop(kw)
        if action == "set_mode":
            return await self._runtime_set_mode(kw)
        return Err(SdkError(
            f"BAD_ACTION: runtime 不支持 {action!r}（可选 start/stop/set_mode）"))

    async def _runtime_start(self, kw: dict[str, Any]):
        """开始监听 QQ 消息，并按当前配置自动回复或转发。"""
        return await self.runtime_ops_service.start_auto_reply()

    async def _runtime_stop(self, kw: dict[str, Any]):
        """停止监听，不再继续自动回复或转发。"""
        return await self.runtime_ops_service.stop_auto_reply()

    async def _runtime_set_mode(self, kw: dict[str, Any]):
        """切换连接方式。运行中则停掉重连，否则只存设置。"""
        mode = str(kw.get("mode") or "").strip()
        restart = bool(kw.get("restart", True))
        if mode not in CONNECTION_MODES:
            return Err(SdkError(
                f"BAD_MODE: 未知的连接方式 {mode!r}（可选 {'/'.join(CONNECTION_MODES)}）"))

        self._qq_settings["qq_connection_mode"] = mode
        try:
            await self.settings_service.persist_business_config()
        except Exception as e:
            self.logger.warning(f"切换连接方式后落盘失败: {e}")
        self.settings_service.apply_runtime_settings(self._qq_settings)

        # 没在跑就只存设置 —— 别顺手把机器人启动起来，那是 runtime.start 的事。
        if not (restart and self._running):
            return Ok({"status": "saved", "mode": mode, "restarted": False, "error": ""})

        auto = await self._restart_auto_reply_runtime(True)
        return Ok({"status": "reconnected" if auto["ok"] else "reconnect_failed",
                   "mode": mode, "restarted": auto["ok"], "error": auto["error"]})

    async def _stop_auto_reply_runtime(self, *, stop_napcat: bool):
        await self.runtime_ops_service.stop_runtime(stop_napcat=stop_napcat)

    @staticmethod
    def _auto_start_fields(auto: dict[str, Any]) -> dict[str, Any]:
        """把 ``_restart_auto_reply_runtime`` 的结果摊成入口返回值里的固定三键。

        开放平台绑定 / 一键部署 / 补写 OneBot 配置三条流程共用同一组键名，
        前端不必按流程分支去猜字段。
        """
        return {"auto_started": bool(auto.get("ok")),
                "auto_start_status": str(auto.get("status") or ""),
                "auto_start_error": str(auto.get("error") or "")}

    async def _restart_auto_reply_runtime(self, auto_start: bool) -> dict[str, Any]:
        """把运行时收敛到"按当前设置在跑"的状态 —— 各条接入流程的收尾。

        开放平台扫码绑定、NapCat 一键部署、扫码后补写 OneBot 配置都走这里。三者的共同点
        是**设置刚变过，而运行时可能还挂着按旧设置建的连接**；对开放平台尤其致命，因为
        绑定会把 AppSecret 轮换掉（见 ``qq_official_bind``）。

        **必须丢弃连接对象重建。** ``create_onebot_connection`` 在**构造时**就把
        app_id / client_secret 拷进了连接对象（``QQOpenPlatformConnection.__init__``），
        而 ``_ensure_qq_client_initialized`` 见对象非空直接早退 —— 不丢的话，收尾这次
        启动会拿**刚被轮换掉的旧密钥**去连，现象是"配置明明写对了却连不上"。

        顺序：停 → 丢对象 → 启。``stop_napcat=False``：轮换的是开放平台凭据，与 NapCat
        进程无关，不该顺手把它杀掉。

        任何一步失败都不向上抛：凭据此刻已经写好了，启动失败是**运行状态**问题，不该让
        整个绑定结果变成"失败"。

        **整段持闸**：下面是「停 → 丢对象 → 启」，中间任何一刻放别的入口进来动同一个
        连接对象，都会复现 2026-09-27 00:18:53 那次
        ``AttributeError: 'NoneType' object has no attribute 'get'``（``disconnect``
        把正在 ``connect`` 的 ``_http`` 关掉并置空）。重入是安全的：内层
        ``stop_runtime`` / ``start_auto_reply`` 与本方法同属一个任务。
        """
        out: dict[str, Any] = {"ok": False, "status": "", "error": ""}
        if not auto_start:
            return out
        async with runtime_transition_guard(self).hold("收尾重建运行时"):
            self.logger.info(f"[运行时] 收尾重建连接对象（auto_start={auto_start}）")
            try:
                await self._stop_auto_reply_runtime(stop_napcat=False)
            except Exception as e:
                # 停不下来不阻断重建：对象照样丢，运行状态由下面的 start 收敛。
                self.logger.warning(f"[绑定] 停止旧运行时失败（继续重建）: {e}")
            self.qq_client = None
            try:
                # 必须走 service，不能调入口方法：`start_auto_reply` 这个入口已经并进
                # `runtime`（见本文件 runtime 段），调 self.start_auto_reply() 只会抛
                # AttributeError，然后被下面的 except 吞掉 —— 表现为"启动静默失败"。
                started = await self.runtime_ops_service.start_auto_reply()
            except Exception as e:
                out["error"] = f"{type(e).__name__}: {e}"
                self.logger.warning(f"[绑定] 凭据已写入，但自动回复启动失败: {e}")
                return out
            if started.is_ok():
                payload = started.value if isinstance(started.value, dict) else {}
                out["ok"] = True
                out["status"] = str(payload.get("status") or "")
                self.logger.info(f"[绑定] 自动回复已启动（{out['status'] or 'ok'}）")
            else:
                out["error"] = str(started.error)
                self.logger.warning(f"[绑定] 凭据已写入，但自动回复启动被拒: {started.error}")
            return out

    @staticmethod
    def _sanitize_message_text(text: str, *, is_reply_to_bot: bool = False) -> str:
        import re
        # 回复标签 → 人类可读格式。
        #
        # id 用 [^\]]* 而不是 \d+：消息 ID 不保证是纯数字。实测既有
        # ``poke_<群>_<人>_<时间戳>`` 这类（backlog 里 120 个 ID 有 14 个非纯数字），
        # 开放平台/Lagrange 更甚。用 \d+ 时这些 CQ 码替换不掉，**用户会在聊天里
        # 直接看到裸的 [CQ:reply,id=...]**。同一模式在 enrichment._fetch_reply_content
        # 也有一份，改这里时两处一起改。
        if is_reply_to_bot:
            text = re.sub(r"\[CQ:reply,\s*id=[^\]]*\]", "[回复你的消息]", text)
        else:
            text = re.sub(r"\[CQ:reply,\s*id=[^\]]*\]", "[回复他人的消息]", text)
        text = re.sub(r"\[CQ:at,qq=all\]", "@全体成员", text)
        text = re.sub(r"\[CQ:at,qq=(\d+)\]", r"@用户\1", text)
        return text

    async def _handle_normal_relay(self, message_text: str, sender_id: str, source_type: str, source_id: str, relay_probability: Optional[float] = None):
        return await self.relay_service.handle_normal_relay(
            message_text,
            sender_id,
            source_type,
            source_id,
            relay_probability=relay_probability,
        )

