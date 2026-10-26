"""Public hosted UI state/actions, with bounded read-only audit sampling."""

import json
import uuid
from dataclasses import asdict

from agent_mc_autonomy.configuration import Settings
from agent_mc_autonomy.decision_profiles import validate_policy
from agent_mc_autonomy.host_context import HostContextPort
from agent_mc_autonomy.host_context import metadata as host_context_metadata
from agent_mc_autonomy.model_config import HttpPlannerConfig, planner_settings
from agent_mc_autonomy.readiness import installation_readiness, validation_feedback
from agent_mc_autonomy.viewer import viewer_observation
from plugin.sdk.plugin import Err, Ok, plugin_entry, ui

from .bundled_extension import bundled_extension_status, extension_mode
from .direct_planner import public_planner
from .model_check import check_model
from .neko_heartbeat import HeartbeatSettings, is_stopped, review_port
from .neko_host_context import HostContextSettings
from .neko_presentation import PresentationSettings
from .neko_speech import output_settings
from .neko_tools import unwrap
from .prepared_runtime import resolve_host_runtime
from .qiandengji_account import configured_identity

try:
    from agent_mc_autonomy.operator import operator_link
except ModuleNotFoundError as error:
    if error.name != "agent_mc_autonomy.operator":
        raise
    operator_link = None

OPERATOR_UNAVAILABLE = {"available": False, "reason": "operator_capability_unavailable",
                        "guidance": "当前框架尚不支持账户入口。请安装匹配的核心与自主框架后启用；没有读取或发起 Microsoft 授权。"}

EDITABLE = {
    "autonomy": {"enabled", "min_decision_seconds", "idle_decision_seconds", "max_decisions", "max_tool_rounds", "context_tokens", "context_mode", "context_history_headroom_tokens", "max_output_tokens", "model_timeout_seconds", "review_model_pool"},
    "server": {"host", "port", "username", "auth", "version", "connection_type", "login_config_file"},
    "qiandengji": {"identity_file"},
    "operator": {"port"},
    "decision_policy": {"enabled","routine_output_tokens","complex_output_tokens","routine_parameters","complex_parameters"},
    "runtime": {"mode", "data_directory", "bundle_root", "bundle_manifest_sha256"},
    "backend": {"node", "directory"},
    "viewer": {"url", "auto_open", "renderer_directory", "catalog_file", "bundled_appearance_catalog_file", "default_appearance", "listen_port"},
    "perception": {"enabled", "mineral_vision"},
    "presentation": {"enabled", "target", "min_interval_seconds"},
    "heartbeat": {"enabled", "target", "min_interval_seconds", "review_interval_seconds", "idle_presence_seconds", "structured_reviews", "review_max_output_tokens"},
    "speech_output": {"enabled", "base_url", "planner_narration", "min_interval_seconds"},
    "extension_pack": {"mode"},
    "server_extension": {"presentation_module", "presentation_sha256"},
    "host_context": {"enabled", "character_api_url"},
    "fast": {"mode", "endpoint", "model", "api_key_env", "min_confidence"},
    "memory": {"mode", "endpoint", "model", "api_key_env", "timeout_ms", "provider", "library_id", "project_id", "scope_id", "search_mode", "learning_enabled", "read_preference", "agent_id"},
    "knowledge": {"minedojo_index_file", "retrieval"},
    "planner": {"route", *HttpPlannerConfig.__dataclass_fields__},
}


def public_config(settings):
    return {
        "autonomy": {key: getattr(settings, "enabled" if key == "enabled" else key) for key in EDITABLE["autonomy"]},
        "server": {key: getattr(settings, key, "local" if settings.host in {"", "localhost", "127.0.0.1", "::1"} else "custom") for key in EDITABLE["server"]},
        "operator": {"port": settings.console_port},
        "decision_policy": asdict(settings.decision_policy),
        "runtime": {"mode": settings.runtime_mode, "data_directory": settings.data_directory,
                    "bundle_root": settings.bundle_root, "bundle_manifest_sha256": settings.bundle_manifest_sha256},
        "backend": {"node": settings.node, "directory": settings.backend_directory},
        "viewer": {"url": settings.external_viewer_url, "auto_open": settings.viewer_auto_open,
                   "renderer_directory": settings.viewer_renderer_directory, "catalog_file": settings.viewer_catalog_file,
                   "bundled_appearance_catalog_file": settings.viewer_bundled_appearance_catalog_file,
                   "default_appearance": settings.viewer_default_appearance,
                   "listen_port": settings.viewer_listen_port},
        "perception": {"enabled": settings.vision_enabled, "mineral_vision": settings.mineral_vision},
        "server_extension": {"presentation_module": getattr(settings, "server_presentation_module", ""),
                             "presentation_sha256": getattr(settings, "server_presentation_sha256", "")},
        "fast": asdict(settings.fast),
        "memory": asdict(settings.memory),
        "knowledge": asdict(settings.knowledge),
    }


def tail_rows(path, limit=20):
    """Fixed byte/row cap. Skip partial records during concurrent append."""
    try:
        with path.open("rb") as source:
            source.seek(0, 2)
            start = max(0, source.tell() - 131072)
            source.seek(start)
            lines = source.read(131072).splitlines(keepends=True)
    except FileNotFoundError:
        return []
    if start and lines:
        lines.pop(0)
    rows = []
    for line in lines:
        if not line.endswith(b"\n"):
            continue
        try:
            value = json.loads(line)
            if isinstance(value, dict):
                rows.append(value)
        except (ValueError, UnicodeError):
            pass
    return rows[-limit:]


def audit_sample(directory):
    decisions, receipts = tail_rows(directory / "decisions.jsonl"), tail_rows(directory / "receipts.jsonl")
    samples = []
    for row in decisions:
        response = row.get("response") or {}
        route = response.get("model_status") or {}
        samples.append({"turn": row.get("turnId"), "status": "rejected" if "rejected" in row else "returned",
                        "tools": ", ".join(str(c.get("function", {}).get("name", ""))[:80] for c in response.get("tool_calls", [])[:1]),
                        "reply": str(response.get("content") or "")[:800], "route": route.get("route", "not_recorded"),
                        "latency_ms": route.get("latency_ms"), "usage": route.get("usage", {})})
    actions = [{"tool": str(row.get("tool", ""))[:80], "request_id": row.get("requestId"),
                "failed": bool((row.get("result") or {}).get("failed")),
                "execution": {k: ((row.get("result") or {}).get("execution") or {}).get(k) for k in ("kind", "id", "phase", "terminal")},
                "text": str((row.get("result") or {}).get("text", ""))[:1000]} for row in receipts]
    usage = [row["usage"] for row in samples if isinstance(row["usage"], dict)]
    return {"decisions": samples, "receipts": actions, "sample_scope": "last_20_records_within_128KiB_each",
            "usage_recorded": any("total_tokens" in row for row in usage),
            "sample_tokens": sum(row.get("total_tokens", 0) for row in usage if type(row.get("total_tokens", 0)) is int),
            "sample_failed_receipts": sum(row["failed"] for row in actions)}


class Dashboard:
    @ui.action(label="检查直连模型", tone="default")
    @plugin_entry(id="check_direct_model", name="检查显式配置的模型服务", kind="service", timeout=45,
                  description="按已保存配置显式调用一次无游戏动作的工具测试；会使用模型额度。",
                  input_schema={"type": "object", "properties": {"catalog": {"type": "boolean"}, "planner": {"type": "object"}}})
    async def check_direct_model(self, catalog: bool = False, planner: dict | None = None, **_):
        from agent_mc_autonomy.model_admin import check_model as direct_check
        from agent_mc_autonomy.model_admin import list_models
        try:
            raw = await self.config.dump()
            settings = Settings.parse(raw)
            if settings.runtime_mode == "external":
                return Err({"code": "external_runtime_use_standalone_config"})
            if planner is not None:
                if not catalog or not isinstance(planner, dict) or planner.get("route") != "direct":
                    raise ValueError("draft_catalog_only")
                config = HttpPlannerConfig.parse({k: v for k, v in planner.items() if k != "route"}, require_model=False)
            else:
                config = planner_settings(raw.get("planner", {}))
            if config is None:
                return Err({"code": "direct_model_not_selected"})
            return Ok(await (list_models(config) if catalog else direct_check(config, max_output_tokens=settings.max_output_tokens)))
        except (RuntimeError, ValueError):
            return Err({"code": "direct_model_check_failed", "message": "未通过检查。请检查密钥、OAuth 登录、模型权限和参数；游戏未启动。"})

    @ui.action(label="检查模型接口", tone="default")
    @plugin_entry(id="check_model", name="检查已绑定模型", kind="service", timeout=30,
                  description="显式调用 Neko 模型槽做一次中性结构化测试。图像模式使用合成色块，不连接游戏，不发送聊天。",
                  input_schema={"type": "object", "properties": {"mode": {"enum": ["text", "vision"]}}, "required": ["mode"]})
    async def check_model(self, mode: str, **_):
        if mode not in {"text", "vision"}:
            return Err({"code": "invalid_model_check"})
        return Ok(await check_model(getattr(self.ctx, "models", None), mode))

    @ui.context(id="dashboard")
    async def dashboard(self):
        try:
            raw = await self.config.dump()
            effective, prepared = resolve_host_runtime(raw)
            settings = Settings.parse(effective)
            result = unwrap(await self.status(metadata_only=True))
            if result.get("is_error"):
                return {"error": "configuration_or_observation_unavailable"}
            snapshot = result.get("observation", result)
            directory = settings.data_root(self.data_path()) / "sessions" / settings.identity / "host"
            state = {key: snapshot.get(key) for key in (
                "phase", "game_connected", "autonomy_running", "updated_at", "decisions", "source", "live_verified",
                "standing_goal", "current_goal", "context", "host_context", "model_status", "fast_focus", "perception", "planner_error", "planner_diagnostic", "viewer", "viewer_window")}
            state["facts"] = [{"key": str(p.get("key", ""))[:80], "text": str(p.get("text", ""))[:900],
                               "has_more": len(str(p.get("text", ""))) > 900} for p in snapshot.get("facts", {}).get("parts", [])[:24]]
            state["pending"] = [{k: p.get(k) for k in ("kind", "id", "phase", "terminal")} for p in snapshot.get("pending", [])[:16]]
            heartbeat = HeartbeatSettings.parse(raw)
            port = review_port(self, settings)
            review_state = port.state()
            state["host_context"] = host_context_metadata(HostContextPort(directory, now=port.now).read(port.mailbox.intent_revision()))
            review = review_state.get("review", {})
            heartbeat_state = {"enabled": heartbeat.enabled, "explicit_stop": is_stopped(self), "last_decision": review_state.get("last_decision"),
                               "submitted_at": review_state.get("submitted_at"), "retry_after": review_state.get("retry_after"),
                               "review": {key: review.get(key) for key in ("id", "trigger", "attempts", "result", "opportunity", "notification_status", "inspected_at", "reflection", "inspected_evidence_ref")}}
            heartbeat_state["inspection"] = {"default": "compact", "original_query": "minecraft_autonomy_review: evidence_ref/detail_path/detail_offset"}
            heartbeat_state["delivery_failure"] = (review.get("result") or {}).get("error")
            heartbeat_state["transport"] = "neko_public_model_gateway" if heartbeat.structured_reviews else "host_notification"
            if heartbeat.structured_reviews:
                from .neko_commentary import load_commentary
                heartbeat_state["structured_delivery"] = load_commentary(port.directory / "structured-review.json")
            heartbeat_state["delivery_state"] = ("final_decision" if (review.get("result") or {}).get("acknowledged") else "inspected_awaiting_final" if review.get("inspected_at") else "notification_" + str(review.get("notification_status", "not_delivered")))
            heartbeat_state["next"] = "主脑尚未完成最终判断；重新检查当前心跳后提交简短判断。" if heartbeat_state["delivery_failure"] else None
            return {"settings": {**public_config(settings), "presentation": asdict(PresentationSettings.parse(raw)),
                                 "qiandengji": {"identity_file": configured_identity(raw)},
                                 "heartbeat": asdict(heartbeat), "host_context": asdict(HostContextSettings.parse(raw)), "planner": public_planner(raw.get("planner", {})),
                                 "speech_output": {"enabled": raw.get("speech_output", {}).get("enabled", False),
                                    "base_url": raw.get("speech_output", {}).get("base_url", ""),
                                    "planner_narration": raw.get("speech_output", {}).get("planner_narration", True),
                                    "min_interval_seconds": raw.get("speech_output", {}).get("min_interval_seconds", 30)},
                                 "extension_pack": {"mode": extension_mode(raw)}}, "heartbeat": heartbeat_state, "runtime": state, "audit": audit_sample(directory),
                    "server_pack": {k:v for k,v in bundled_extension_status(raw).items() if k not in {"module", "sha256"}},
                    "model_slots": ["decision", "vision", "fast"],
                    "installation": {**installation_readiness(settings, self.cache_path()), "prepared_runtime": prepared},
                    "operator": operator_link(settings, settings.data_root(self.data_path())) if operator_link else OPERATOR_UNAVAILABLE,
                    **viewer_observation(settings, snapshot)}
        except (OSError, ValueError, KeyError):
            return {"error": "configuration_or_observation_unavailable"}

    @plugin_entry(id="save_settings", name="保存自主游玩设置", kind="service",
                  description="仅保存经过校验的插件设置；不连接游戏、不调用模型，已有运行下次显式启动生效。",
                  input_schema={"type": "object", "properties": {"settings": {"type": "object"}}, "required": ["settings"]})
    async def save_settings(self, settings: dict, **_):
        try:
            if not isinstance(settings, dict) or not settings or set(settings) - EDITABLE.keys():
                raise ValueError("unknown_settings_section")
            for section, values in settings.items():
                if not isinstance(values, dict) or set(values) - EDITABLE[section]:
                    raise ValueError("unknown_settings_field")
            if operator_link is None and ("operator" in settings or "connection_type" in settings.get("server", {})):
                return Err({"code": "operator_capability_unavailable", "message": OPERATOR_UNAVAILABLE["guidance"]})
            original = await self.config.dump()
            merged = {**original, **{key: {**original.get(key, {}), **value} for key, value in settings.items()}}
            parsed = Settings.parse(merged)
            configured_identity(merged)
            extension_mode(merged)
            direct = planner_settings(merged.get("planner", {}))
            if direct:
                direct.validate_budget(parsed.max_output_tokens)
                validate_policy(parsed.decision_policy,direct,parsed.max_output_tokens)
            PresentationSettings.parse(merged)
            HeartbeatSettings.parse(merged)
            HostContextSettings.parse(merged)
            output_settings(merged, HeartbeatSettings.parse(merged).target)
            with self._control_lock:
                if set(settings) - {"viewer", "presentation", "heartbeat", "host_context", "speech_output"} and self._worker and self._worker.snapshot().get("worker_alive"):
                    return Err({"code": "settings_runtime_active", "message": "运行参数须先显式停止嵌入式运行再保存；观战地址可单独保存，模型槽绑定可在宿主修改。"})
                fence = bool(set(settings) & {"server", "runtime", "autonomy", "heartbeat", "host_context", "speech_output", "qiandengji"})
                if fence:
                    self._generation += 1
                    self._configuration_writes += 1
            try:
                await self.config.update(settings)
                if set(settings) & {"host_context", "heartbeat"}:
                    port = review_port(self, parsed)
                    with self._control_lock:
                        HostContextPort(port.directory).invalidate()
                        self._host_profile_cache = None
            finally:
                if fence:
                    with self._control_lock:
                        self._generation += 1
                        self._configuration_writes -= 1
            if "heartbeat" in settings:
                review_port(self, Settings.parse(merged)).configure(HeartbeatSettings.parse(merged).policy())
            return Ok({"saved": True, "running_session_changed": False, "applies": "next_explicit_start_or_observer_read"})
        except (ValueError, TypeError) as error:
            return Err(validation_feedback(error))

    @ui.action(label="保存插件设置", tone="primary")
    @plugin_entry(id="save_settings_hosted", name="托管面板保存设置", kind="service",
                  description="通过可序列化反馈保存设置；校验拒绝保留草稿，不启动运行。",
                  input_schema={"type": "object", "properties": {"settings": {"type": "object"}}, "required": ["settings"]})
    async def save_settings_hosted(self, settings: dict, **_):
        result = await self.save_settings(settings)
        if isinstance(result, Err):
            # The host stringifies Err payloads. Expected rejection must travel
            # through a successful dispatch so the panel receives field metadata.
            return Ok({"success": False, "error": result.error})
        return result

    @ui.action(label="投递目标", tone="primary")
    @plugin_entry(id="panel_intent", name="控制台投递目标", kind="service", description="通过同一个持久队列投递自然语言目标。",
                  input_schema={"type": "object", "properties": {"goal": {"type": "string", "minLength": 1, "maxLength": 4000},
                      "mode": {"enum": ["append", "insert", "replace"]}, "request_id": {"type": "string"}},
                      "required": ["goal", "mode", "request_id"]})
    async def panel_intent(self, goal: str, mode: str, request_id: str, **_):
        if mode == "append":
            return await self._submit("goal", {"goal": goal, "origin": "user"}, request_id)
        if mode in {"insert", "replace"}:
            return await self._submit("feedback", {"goal": goal, "mode": mode, "origin": "user"}, request_id)
        return Err({"code": "invalid_intent_mode"})

    @ui.action(label="开始嵌入式游玩", tone="primary")
    @plugin_entry(id="panel_start", name="控制台开始游玩", kind="service", description="按已保存配置显式开始；外部观察模式不能接管进程。",
                  input_schema={"type": "object", "properties": {"goal": {"type": "string", "minLength": 1, "maxLength": 4000}}, "required": ["goal"]})
    async def panel_start(self, goal: str, **_):
        return await self.start(goal=goal)

    @ui.action(label="保存并一键启动", tone="primary")
    @plugin_entry(id="quick_start", name="保存连接并显式启动自主游玩", kind="service",
                  description="保存常用连接，启用本次自主游玩和主脑参与，再通过正常启动流程运行。不会接管正在运行的外部执行器或自动重启Neko。",
                  input_schema={"type": "object", "properties": {"connection": {"type": "object"},
                      "goal": {"type": "string", "maxLength": 4000}, "use_neko_model": {"type": "boolean"},
                      "appearance": {"type": "string"}, "extension_mode": {"enum": ["auto", "none", "custom"]},
                      "character": {"type": "string", "maxLength": 80}, "narration": {"type": "boolean"},
                      "neko_url": {"type": "string", "maxLength": 200}}, "required": ["connection"]})
    async def quick_start(self, connection: dict, goal: str = "", use_neko_model: bool = True,
                          appearance: str | None = None, extension_mode: str | None = None, character: str | None = None,
                          narration: bool = True, neko_url: str | None = None, **_):
        from agent_mc_autonomy.mailbox import Mailbox
        from agent_mc_autonomy.ownership import session_is_owned
        from agent_mc_autonomy.recovery import require_no_pending_acknowledgement

        if (not isinstance(connection, dict) or not connection or set(connection) - EDITABLE["server"]
                or not isinstance(goal, str) or len(goal) > 4000 or type(use_neko_model) is not bool or type(narration) is not bool):
            return Err({"code": "invalid_quick_start", "message": "请填写有效连接；不要在连接字段中传入密码或其它控制参数。"})
        try:
            raw = await self.config.dump()
            connection = dict(connection)
            # Defaults are applied only on this explicit play action. Importing
            # the plugin never creates an account or joins a server.
            if not connection.get("host"):
                connection["host"] = "127.0.0.1"
            if not connection.get("username"):
                if connection.get("auth", raw.get("server", {}).get("auth", "offline")) == "microsoft":
                    return Err({"code": "account_required", "message": "正版登录请先填写自己的 Microsoft 账户标识，再通过官方页面授权。"})
                connection["username"] = raw.get("server", {}).get("username") or "Neko_" + uuid.uuid4().hex[:8]
            current = Settings.parse(raw)
            directory = current.data_root(self.data_path()) / "sessions" / current.identity / "host"
            if session_is_owned(directory) or self._worker and self._worker.snapshot().get("worker_alive"):
                return Err({"code": "runtime_already_owned", "message": "当前角色正在游玩。请先停止并等动作排空，再修改连接或启动。"})
            if directory.exists():
                Mailbox(directory).check_uncertain()
                require_no_pending_acknowledgement(directory)
            desired = Settings.parse({**raw, "server": {**raw.get("server", {}), **connection}})
            target = desired.data_root(self.data_path()) / "sessions" / desired.identity / "host"
            if target != directory:
                if session_is_owned(target):
                    return Err({"code": "runtime_already_owned", "message": "目标账号已有执行器，请先在对应会话停止游玩。"})
                if target.exists():
                    Mailbox(target).check_uncertain()
                    require_no_pending_acknowledgement(target)
            patch = {"server": connection, "runtime": {"mode": "embedded"}, "autonomy": {"enabled": True},
                     "heartbeat": {"enabled": True}, "host_context": {"enabled": True}, "presentation": {"enabled": True}}
            if "max_decisions" not in raw.get("autonomy", {}):
                patch["autonomy"]["max_decisions"] = 0
            patch["speech_output"] = {"enabled": narration}
            if narration:
                origin = neko_url if neko_url is not None else raw.get("speech_output", {}).get("base_url") or "http://127.0.0.1:48911"
                output_settings({"speech_output": {"enabled": True, "base_url": origin}}, "current_character")
                patch["speech_output"].update(base_url=origin, planner_narration=True)
                if neko_url is not None or not raw.get("host_context", {}).get("character_api_url"):
                    patch["host_context"]["character_api_url"] = origin.rstrip("/") + "/api/characters"
            if use_neko_model:
                patch["planner"] = {"route": "host"}
            if appearance is not None:
                patch["viewer"] = {"default_appearance": appearance}
            if extension_mode is not None:
                patch["extension_pack"] = {"mode": extension_mode}
            if character is not None:
                if not isinstance(character, str) or len(character) > 80:
                    raise ValueError("invalid_character")
                patch["heartbeat"]["target"] = character
                patch["presentation"]["target"] = character
            result = await self.save_settings(patch)
            if isinstance(result, Err):
                return result
            return await self.start(goal=goal.strip() or "在 Minecraft 世界里自主生活，结合自己的兴趣和当前事实选择目标，兼顾生存并根据用户反馈调整。")
        except (OSError, ValueError, KeyError, RuntimeError):
            return Err({"code": "quick_start_not_ready", "message": "当前会话有未确认回执或配置尚未就绪；请先处理面板中的状态，未启动新角色。"})

    @ui.action(label="停止游玩", tone="danger", confirm="停止自主框架和它的游戏连接？服务器、模型与桌面保持运行。")
    @plugin_entry(id="panel_stop", name="控制台停止游玩", kind="service", description="显式停止本框架，保留账本。")
    async def panel_stop(self, **_):
        return await self.stop(request_id=str(uuid.uuid4()))
