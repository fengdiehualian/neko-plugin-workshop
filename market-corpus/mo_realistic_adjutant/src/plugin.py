from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from plugin.sdk.plugin import (
    NekoPluginBase,
    Ok,
    lifecycle,
    llm_tool,
    message,
    neko_plugin,
)

from .bridge_client import BridgeClient, BridgeError
from .bridge_runtime import EmbeddedBridgeError, EmbeddedBridgeRuntime
from .adaptive_control import (
    assign_repairers,
    distance_sq,
    find_entity,
    movement_progress,
    reacquire_target,
    staging_cells,
)
from .adaptive_tasks import AdaptiveTaskEngine
from .base_development import (
    choose_adaptive_defense,
    choose_anti_infantry_defense,
    choose_building,
    choose_needed_upgrade,
    choose_defense_force,
    deduplicate_base_tasks,
    filter_defense_profiles_for_power,
    has_established_technology_center,
    placement_candidates,
    power_reserve_allows_defense,
    filter_upgrade_profiles,
    profile_class,
    upgrade_host_candidates,
)
from .building_command import (
    assault_target_layer,
    building_query_categories,
    combat_target_priority,
    is_valid_attack_target,
    select_assault_target,
    strategic_building_priority,
)
from .alert_watching import (
    alert_watch_report,
    create_alert_watch,
    observe_alert_watch,
)
from .locations import TECH_NAMES, base_label, list_tech_buildings, nearby_cells, resolve_base
from .economy_tracking import EconomyTracker
from .economy_strategy import (
    PERSONALITY_REFRESH_SECONDS,
    EconomicBudget,
    balanced_personality,
    build_economic_budget,
    infer_economic_personality,
    load_current_character,
)
from .intent_planning import IntentPlanEngine, IntentPlanError
from .strategy_evaluation import StrategyEvaluator
from .action_validation import ActionValidator
from .autonomy_nudge import build_autonomy_activation_nudge, build_reasoning_nudge
from .autonomy_authorization import classify_autonomy_authorization, extract_user_text
from .monitoring import describe_alerts, describe_location, economy_report
from .neko_focus import NekoFocusController, NekoFocusError
from .opening_doctrine import DEFAULT_DOCTRINE, doctrine_step, get_doctrine, save_doctrine
from .order_plans import OrderPlanEngine, OrderPlanError
from .command_runtime import CommandRuntime
from .report_policy import ReportPolicy, should_report_production_completion
from .policy import (
    CommandPolicy,
    PolicyError,
    plan_mining_orders,
    select_nearest_mining_order,
)
from .proactive import NekoSpeaker, ProactiveReportError
from .unit_knowledge import (
    catalog_summary,
    load_catalog,
    match_visible_entities,
    resolve_type_ids,
    unit_profile,
)
from .unit_threat import summarize_visible_enemy_threat
from .unit_control import (
    entity_label,
    relative_move_cells,
    resolve_map_region_destination,
    resolve_visible_destination,
    is_relative_move_request,
    selection_kind,
    selection_name_query,
    select_owned_units,
    spatial_region,
)
from .tutorial_knowledge import recommend_tactics, tutorial_search, tutorial_sources
from .watching import observe_unit_watch, watch_report_text, watch_transition
from .world_model import WorldModel
from .combat_learning import CombatExperimentLog
from .us_strategy import (
    choose_us_decision,
    choose_us_convoy_requirements,
    choose_us_convoy_escort_requirements,
    cooling_production_lanes,
    production_lane,
    profiles_for_idle_lanes,
)
from .strategic_ai import StrategicDirector
from .strategy_library import StrategyLibrary
from .strategy_server import StrategyLibraryServer
from .support_power_control import SupportPowerController
from .combat_response import (
    combat_matchup_score,
    contact_enemies,
    damaged_buildings,
    effective_combat_strength,
    is_economic_unit,
    is_deployable_combat_unit,
    partition_force,
    prioritized_counter_assignments,
    threatened_harvesters,
)
from .contact_defense_doctrine import select_contact_defense_rule
from .tech_capture_strategy import (
    ENGINEER_TYPE_IDS,
    capturable_neutral_tech_buildings,
    capture_target_is_safe,
    choose_capture_assignment,
    owned_engineers,
)


GROUP_TELEMETRY_CONFIRM_SECONDS = 0.5
COMMAND_PROGRESS_POLL_SECONDS = 0.2
COMMAND_PROGRESS_TIMEOUT_SECONDS = 1.2


def _num_health(item: dict[str, Any]) -> float:
    value = item.get("health_ratio")
    return float(value) if isinstance(value, (int, float)) else 1.0


@neko_plugin
class MOCompanionPlugin(NekoPluginBase):
    def __init__(self, ctx: Any):
        super().__init__(ctx)
        self._plugin_context = ctx
        self.logger = ctx.logger
        self._client = BridgeClient("http://127.0.0.1:48971")
        self._bridge_runtime = EmbeddedBridgeRuntime()
        self._cfg: dict[str, Any] = {}
        self._session_enabled = False
        self._auto_control_enabled = True
        self._last_game_connected: bool | None = None
        self._watches: dict[str, dict[str, Any]] = {}
        self._alert_watches: dict[str, dict[str, Any]] = {}
        self._attack_tasks: dict[str, dict[str, Any]] = {}
        self._load_tasks: dict[str, dict[str, Any]] = {}
        self._production_tasks: dict[str, dict[str, Any]] = {}
        self._base_tasks: dict[str, dict[str, Any]] = {}
        self._force_tasks: dict[str, dict[str, Any]] = {}
        self._defense_tasks: dict[str, dict[str, Any]] = {}
        self._groups: dict[int, dict[str, Any]] = {}
        self._watch_thread: threading.Thread | None = None
        self._watch_stop = threading.Event()
        self._watch_started_at: float | None = None
        self._watch_last_poll_at: float | None = None
        self._watch_last_error: str | None = None
        self._bridge_last_restart_at: float | None = None
        self._bridge_restart_count = 0
        self._speaker = NekoSpeaker("http://127.0.0.1:48911")
        self._focus = NekoFocusController("http://127.0.0.1:48911")
        self._economy_tracker = EconomyTracker()
        self._economic_personality = balanced_personality()
        self._economic_character_name = ""
        self._economic_personality_updated_at = 0.0
        self._adaptive_tasks = AdaptiveTaskEngine(self._submit_unit_command_sync)
        self._order_plans = OrderPlanEngine(
            self._execute_plan_order,
            on_transition=self._on_plan_order_transition,
            validator=self._validate_plan_action,
        )
        self._intent_plans = IntentPlanEngine()
        self._strategy_evaluator = StrategyEvaluator()
        self._action_validator = ActionValidator()
        self._intent_last_status: dict[str, str] = {}
        self._autonomy_last_nudge_at: float | None = None
        self._autonomy_activation_last_nudge_at: float | None = None
        self._autonomy_nudge_count = 0
        self._autonomy_activation_nudge_count = 0
        self._autonomy_nudge_supported: bool | None = None
        self._autonomy_last_skip_reason: str | None = None
        self._natural_autonomy_lock: asyncio.Lock | None = None
        self._natural_autonomy_last_text = ""
        self._natural_autonomy_last_at = 0.0
        self._command_runtime = CommandRuntime()
        self._command_runtime.flags.reserve(128)
        self._command_runtime.interrupts.register(
            "base_under_attack", self._handle_base_under_attack_interrupt
        )
        self._command_runtime.interrupts.register(
            "flag_changed", self._handle_flag_changed_interrupt
        )
        self._handled_alert_interrupts: set[int] = set()
        self._report_policy = ReportPolicy()
        self._world_model = WorldModel(
            log_path=Path(__file__).resolve().parent / "logs" / "world_model.jsonl"
        )
        self._combat_experiments = CombatExperimentLog(
            Path(__file__).resolve().parent / "logs" / "combat_experiments.jsonl"
        )
        self._strategic_director = StrategicDirector()
        self._support_power_controller = SupportPowerController(
            self._client.command_support_power,
            Path(__file__).resolve().parent / "logs" / "support_powers.jsonl",
        )
        self._strategic_roles: dict[str, Any] = {}
        self._strategic_order_at: dict[str, float] = {}
        self._last_contact_rule_id: str | None = None
        self._contact_last_order: dict[int, float] = {}
        self._repair_last_order: dict[int, float] = {}
        self._harvester_protection: dict[int, dict[str, Any]] = {}
        self._tech_capture_tasks: dict[int, dict[str, Any]] = {}
        self._tech_engineer_last_request_at = 0.0
        self._strategic_last_phase: str | None = None
        self._under_attack_reports_enabled = False
        self._production_completion_reports_enabled = False
        self._autonomy_enabled = False
        self._autonomy_last_action: dict[int, float] = {}
        self._task_terminal_reports: set[str] = set()
        self._diagnostic_log_lock = threading.Lock()
        self._runtime_code_fingerprint = "not-started"
        self._strategy_library: StrategyLibrary | None = None
        self._strategy_server: StrategyLibraryServer | None = None

    @lifecycle(id="startup")
    async def startup(self, **_):
        self._runtime_code_fingerprint = self._compute_runtime_code_fingerprint()
        self._append_diagnostic_log("runtime_startup.jsonl", {
            "event": "plugin_startup",
            "code_fingerprint": self._runtime_code_fingerprint,
            "plugin_path": str(Path(__file__).resolve()),
        })
        self.logger.info(
            "Mental Omega runtime code fingerprint: %s path=%s",
            self._runtime_code_fingerprint,
            Path(__file__).resolve(),
        )
        config = await self.config.dump(timeout=5.0)
        config = config if isinstance(config, dict) else {}
        bridge_cfg = config.get("mo_bridge", {})
        self._cfg = bridge_cfg if isinstance(bridge_cfg, dict) else {}
        if self._cfg.get("strategy_library_enabled", True) is True:
            try:
                self._strategy_library = await asyncio.to_thread(StrategyLibrary)
                migration = await asyncio.to_thread(self._strategy_library.bootstrap)
                self.logger.info(
                    "Mental Omega strategy library ready: inserted=%s existing=%s",
                    migration.get("inserted", 0), migration.get("existing", 0),
                )
                if self._cfg.get("strategy_editor_enabled", True) is True:
                    self._strategy_server = StrategyLibraryServer(
                        self._strategy_library,
                        host="127.0.0.1",
                        port=int(self._cfg.get("strategy_editor_port", 8765)),
                    )
                    await asyncio.to_thread(self._strategy_server.start)
                    self.logger.info(
                        "Mental Omega strategy editor listening on http://127.0.0.1:%s/",
                        self._strategy_server.port,
                    )
            except Exception as exc:
                self._strategy_library = None
                self._strategy_server = None
                self.logger.error("Mental Omega strategy library could not start: %s", exc)
        self._client = BridgeClient(
            str(self._cfg.get("base_url", "http://127.0.0.1:48971")),
            float(self._cfg.get("request_timeout_seconds", 2.0)),
        )
        self._support_power_controller = SupportPowerController(
            self._client.command_support_power,
            Path(__file__).resolve().parent / "logs" / "support_powers.jsonl",
        )
        await asyncio.to_thread(self._refresh_economic_personality, True)
        bridge_auto_started = False
        if bool(self._cfg.get("auto_start_bridge", True)):
            try:
                bridge_auto_started = await asyncio.to_thread(
                    self._bridge_runtime.ensure_started,
                    self._client.base_url,
                )
            except EmbeddedBridgeError as exc:
                self.logger.error("Mental Omega local bridge could not start: %s", exc)
        self._session_enabled = False
        self._auto_control_enabled = self._cfg.get("auto_enable_control", True) is True
        self._last_game_connected = None
        self._watches.clear()
        self._alert_watches.clear()
        self._attack_tasks.clear()
        self._load_tasks.clear()
        self._production_tasks.clear()
        self._base_tasks.clear()
        self._force_tasks.clear()
        self._defense_tasks.clear()
        self._groups.clear()
        self._economy_tracker.reset()
        self._combat_experiments.reset()
        self._strategic_director.reset()
        self._strategic_last_phase = None
        self._strategic_roles.clear()
        self._strategic_order_at.clear()
        self._last_contact_rule_id = None
        self._contact_last_order.clear()
        self._repair_last_order.clear()
        self._harvester_protection.clear()
        self._tech_capture_tasks.clear()
        self._tech_engineer_last_request_at = 0.0
        self._world_model = WorldModel(
            history_size=int(self._cfg.get("world_model_history_size", 256)),
            entity_memory_seconds=float(
                self._cfg.get("world_model_entity_memory_seconds", 120.0)
            ),
            memory_budget_bytes=int(
                self._cfg.get("world_model_memory_budget_bytes", 256 * 1024 * 1024)
            ),
            log_path=(
                Path(__file__).resolve().parent / "logs" / "world_model.jsonl"
                if self._cfg.get("world_model_log_enabled", True) is True
                else None
            ),
        )
        self._adaptive_tasks.clear()
        self._order_plans.cancel()
        self._intent_plans.clear()
        self._intent_last_status.clear()
        self._autonomy_last_nudge_at = None
        self._autonomy_activation_last_nudge_at = None
        self._autonomy_nudge_count = 0
        self._autonomy_activation_nudge_count = 0
        self._autonomy_nudge_supported = None
        self._autonomy_last_skip_reason = None
        self._autonomy_enabled = False
        self._handled_alert_interrupts.clear()
        self._task_terminal_reports.clear()
        self._report_policy.configure(
            enabled=self._cfg.get("proactive_reports_enabled", True) is True
        )
        self._production_completion_reports_enabled = (
            self._cfg.get("production_completion_reports_enabled", False) is True
        )
        self._under_attack_reports_enabled = (
            self._cfg.get("under_attack_reports_enabled", False) is True
        )
        self._speaker = NekoSpeaker(
            str(self._cfg.get("neko_main_url", "http://127.0.0.1:48911")),
            float(self._cfg.get("request_timeout_seconds", 2.0)),
        )
        self._focus = NekoFocusController(
            str(self._cfg.get("neko_main_url", "http://127.0.0.1:48911")),
            float(self._cfg.get("request_timeout_seconds", 2.0)),
        )
        self._watch_stop.clear()
        self._bridge_last_restart_at = None
        self._bridge_restart_count = 0
        self._watch_thread = threading.Thread(
            target=self._watch_loop,
            name="mo-companion-watch",
            daemon=True,
        )
        self._watch_thread.start()
        self.logger.info("Mental Omega watch thread started")
        if not self._auto_control_enabled:
            try:
                await asyncio.to_thread(self._client.set_control, False)
            except BridgeError:
                pass
        return Ok(
            {
                "status": "ready",
                "control_enabled": False,
                "bridge_auto_started": bridge_auto_started,
            }
        )

    @lifecycle(id="shutdown")
    async def shutdown(self, **_):
        self._session_enabled = False
        if self._strategy_server is not None:
            await asyncio.to_thread(self._strategy_server.stop)
            self._strategy_server = None
        self._strategy_library = None
        self._auto_control_enabled = False
        self._last_game_connected = False
        self._watch_stop.set()
        if self._watch_thread is not None:
            await asyncio.to_thread(self._watch_thread.join, 3.0)
            self._watch_thread = None
        self._watches.clear()
        self._alert_watches.clear()
        self._attack_tasks.clear()
        self._load_tasks.clear()
        self._production_tasks.clear()
        self._base_tasks.clear()
        self._force_tasks.clear()
        self._defense_tasks.clear()
        self._adaptive_tasks.clear()
        self._order_plans.cancel()
        self._intent_plans.clear()
        self._intent_last_status.clear()
        self._autonomy_enabled = False
        self._adaptive_tasks.close()
        self._groups.clear()
        try:
            await asyncio.to_thread(self._focus.exit)
        except NekoFocusError:
            pass
        try:
            await asyncio.to_thread(self._client.set_control, False)
        except BridgeError:
            pass
        await asyncio.to_thread(self._bridge_runtime.stop)
        return Ok({"status": "shutdown"})

    @message(id="natural_autonomous_victory", source="chat")
    async def on_natural_autonomy_message(self, **event: Any):
        """Start a real persistent task before the dialog model can merely promise it."""
        text = extract_user_text(event)
        if not text:
            for delay in (0.0, 0.15, 0.5):
                if delay:
                    await asyncio.sleep(delay)
                text = self._latest_user_message_text()
                if text:
                    break
        decision = classify_autonomy_authorization(text)
        if decision.get("authorized") is not True:
            return Ok({"status": "ignored", "reason": decision.get("reason")})
        return Ok(await self._activate_natural_autonomous_victory(text, decision))

    def _latest_user_message_text(self) -> str:
        ctx = getattr(self, "_plugin_context", None) or getattr(self, "ctx", None)
        bus = getattr(ctx, "bus", None)
        memory = getattr(bus, "memory", None)
        get_sync = getattr(memory, "get_sync", None)
        if not callable(get_sync):
            return ""
        lanlan = str(getattr(ctx, "_current_lanlan", "") or "").strip()
        if not lanlan:
            return ""
        try:
            records = get_sync(lanlan, limit=4, timeout=0.25)
        except Exception as exc:
            self.logger.debug("Mental Omega user-context read failed: %s", exc)
            return ""
        for record in reversed(list(records or [])):
            raw = getattr(record, "raw", record)
            if isinstance(raw, dict):
                record_lanlan = str(raw.get("lanlan") or "").strip()
                if record_lanlan and record_lanlan != lanlan:
                    continue
            text = extract_user_text(raw)
            if text:
                return text
        return ""

    async def _activate_natural_autonomous_victory(
        self, text: str, decision: dict[str, Any]
    ) -> dict[str, Any]:
        now = time.monotonic()
        compact_text = "".join(str(text).split()).casefold()
        if (
            compact_text == self._natural_autonomy_last_text
            and now - self._natural_autonomy_last_at < 10.0
        ):
            return {"status": "duplicate_ignored", "accepted": True}
        if self._natural_autonomy_lock is None:
            self._natural_autonomy_lock = asyncio.Lock()
        async with self._natural_autonomy_lock:
            active = [
                plan for plan in self._intent_plans.plans(include_finished=False)
                if plan.get("autonomy_granted") is True
                and plan.get("intent_kind") == "autonomous_victory"
            ]
            if active:
                self._natural_autonomy_last_text = compact_text
                self._natural_autonomy_last_at = now
                return {
                    "status": "already_running",
                    "accepted": True,
                    "task_id": active[0].get("intent_id"),
                }
            self.logger.info(
                "Mental Omega natural autonomy authorization accepted: evidence=%s",
                decision.get("evidence"),
            )
            result = await self.mental_omega_autonomous_victory()
            accepted = result.get("accepted") is True
            if accepted:
                self._natural_autonomy_last_text = compact_text
                self._natural_autonomy_last_at = now
                self._autonomy_enabled = True
                task_id = result.get("task_id") or result.get("intent_id")
                try:
                    await asyncio.to_thread(
                        self._speaker.speak,
                        f"自主作战任务已真实启动，任务编号 {task_id}。我会持续发展、作战并以击败对手为目标。",
                        {
                            "kind": "mental_omega_autonomy_started",
                            "task_id": task_id,
                            "source": "natural_language_authorization",
                        },
                    )
                except ProactiveReportError as exc:
                    self.logger.warning("Mental Omega autonomy confirmation failed: %s", exc)
                return {"status": "started", **result}
            self.logger.warning(
                "Mental Omega natural autonomy authorization could not start: %s",
                result.get("summary"),
            )
            return {"status": "start_failed", **result}

    @llm_tool(
        name="mental_omega_submit_order_plan",
        description=(
            "Submit one compound battlefield plan containing any number of dependent orders. "
            "ALWAYS use this whenever the commander gives several instructions in one message, "
            "including move-then-deploy-then-build and produce-then-load-then-group requests. "
            "Orders may move, attack, stop, guard, deploy, explore, produce, load, capture, "
            "repair, unload, or wait. Express sequencing with depends_on; independent orders "
            "run together. Chain vanilla building orders one after another so each waits for "
            "real map placement. Production waits for new object IDs, loading waits for passenger "
            "telemetry, movement waits for arrival, and deployment waits for the construction "
            "yard. The plan continues in the background and retries transient failures."
        ),
        parameters={
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "duration_seconds": {"type": "integer", "minimum": 1},
                "orders": {
                    "type": "array",
                    "minItems": 1,
                    "items": {
                        "type": "object",
                        "properties": {
                            "order_id": {"type": "string"},
                            "action": {"type": "string", "enum": [
                                "move", "attack", "stop", "guard", "deploy", "explore",
                                "produce", "load", "capture", "repair", "unload", "wait",
                            ]},
                            "query": {"type": "string"},
                            "group_number": {"type": "integer", "minimum": 0, "maximum": 9},
                            "target_query": {"type": "string"},
                            "destination": {"type": "string"},
                            "product": {"type": "string"},
                            "type_id": {"type": "string"},
                            "quantity": {"type": "integer", "minimum": 1},
                            "maximum_passengers": {"type": "integer", "minimum": 1},
                            "continuous": {"type": "boolean"},
                            "depends_on": {"type": "array", "items": {"type": "string"}},
                            "delay_seconds": {"type": "number", "minimum": 0},
                            "max_attempts": {"type": "integer", "minimum": 1},
                            "retry_seconds": {"type": "number", "minimum": 0.1},
                            "on_failure": {"type": "string", "enum": ["abort", "skip"]},
                        },
                        "required": ["action"],
                    },
                },
            },
            "required": ["orders"],
        },
        timeout=12.0,
    )
    async def mental_omega_submit_order_plan(
        self, name: Any = None, orders: Any = None, duration_seconds: Any = None, **_
    ):
        try:
            await self._ensure_control_for_order()
            plan = self._order_plans.create(
                str(name or "复合作战计划"),
                orders,
                duration_seconds=(int(duration_seconds) if duration_seconds is not None else None),
            )
            state = await asyncio.to_thread(self._client.state)
            await asyncio.to_thread(self._order_plans.tick, state)
            current = self._order_plans.plans(plan["plan_id"], include_finished=True)[0]
            return {
                "summary": f"已建立复合作战计划，共 {len(current['orders'])} 条命令，后台将按依赖关系持续执行",
                "accepted": True,
                "continuous": True,
                "plan": current,
            }
        except (BridgeError, PolicyError, OrderPlanError, TypeError, ValueError) as exc:
            return {"summary": f"复合作战计划创建失败：{exc}", "accepted": False}

    @llm_tool(
        name="mental_omega_order_plan_status",
        description="Inspect or cancel one or all background compound battlefield plans.",
        parameters={
            "type": "object",
            "properties": {
                "plan_id": {"type": "string"},
                "cancel": {"type": "boolean"},
                "include_finished": {"type": "boolean"},
            },
        },
        timeout=5.0,
    )
    async def mental_omega_order_plan_status(
        self, plan_id: Any = None, cancel: Any = False,
        include_finished: Any = False, **_
    ):
        selected = str(plan_id or "").strip() or None
        if cancel is True:
            return await self._cancel_background_tasks(
                task_id=selected, task_type="plan", all_matching=selected is None
            )
        plans = self._order_plans.plans(
            selected, include_finished=include_finished is True
        )
        return {"summary": f"当前有 {len(plans)} 个作战计划", "plans": plans}

    @llm_tool(
        name="mental_omega_autonomous_adjutant",
        description=(
            "Enable or disable the proactive battlefield commander. When enabled, the "
            "adjutant continuously evaluates fresh alerts, visible hostiles, owned forces, "
            "economy and active plans, then reacts to urgent threats without waiting for a "
            "new user message. It never targets hidden or allied objects and reports the "
            "reason for each autonomous intervention."
        ),
        parameters={
            "type": "object",
            "properties": {"enabled": {"type": "boolean"}},
            "required": ["enabled"],
        },
        timeout=8.0,
    )
    async def mental_omega_autonomous_adjutant(self, enabled: Any = None, **_):
        if enabled is not True:
            self._autonomy_enabled = False
            return {"summary": "已关闭主动战场副官，不会再自动调动部队", "enabled": False}
        try:
            await self._ensure_control_for_order()
            self._autonomy_enabled = True
            return {
                "summary": "已开启主动战场副官；她会持续观察战况并在必要时先行处理",
                "enabled": True,
                "policy": "只使用当前遥测中的己方对象、可见敌军和已确认警报",
            }
        except (BridgeError, PolicyError) as exc:
            return {"summary": f"主动副官开启失败：{exc}", "enabled": False}

    @llm_tool(
        name="mental_omega_support_powers",
        description=(
            "查看当前可用的支援技能，或把指定支援技能释放到明确的地图格。"
            "只有玩家明确要求释放时才传入 type_id、x、y；只查看时不要传目标。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "type_id": {"type": "string"},
                "x": {"type": "integer"},
                "y": {"type": "integer"},
            },
        },
        timeout=8.0,
    )
    async def mental_omega_support_powers(
        self, type_id: Any = None, x: Any = None, y: Any = None, **_
    ):
        try:
            state = await asyncio.to_thread(self._client.state)
            powers = self._support_power_controller.available(state)
            selected = str(type_id or "").strip().upper()
            if not selected:
                return {
                    "summary": f"当前检测到 {len(powers)} 个支援技能。",
                    "support_powers": powers,
                    "scheduler": self._support_power_controller.status(),
                }
            if any(isinstance(value, bool) or not isinstance(value, int) for value in (x, y)):
                return {"summary": "释放支援技能需要整数地图格坐标 x、y。", "accepted": False}
            await self._ensure_control_for_order()
            result = await asyncio.to_thread(
                self._support_power_controller.manual,
                selected,
                {"x": int(x), "y": int(y)},
            )
            return {
                "summary": f"已提交支援技能 {selected}。" if result.get("accepted") else f"支援技能 {selected} 未被游戏接受。",
                **result,
            }
        except (BridgeError, PolicyError, TypeError, ValueError) as exc:
            return {"summary": f"支援技能操作失败：{exc}", "accepted": False}

    @llm_tool(
        name="mental_omega_develop_base",
        description=(
            "Start one persistent closed-loop base-development objective. ALWAYS use this "
            "single tool when the commander says 发展基地, 建设基地, 自动发展, or asks the "
            "adjutant to handle base construction. It deploys an MCV when needed, reads the "
            "live sidebar/economy/power/queue every tick, serializes vanilla building "
            "production and placement, searches hundreds of native-validated placement "
            "cells, expands newly unlocked structures, and builds defensive structures and "
            "forces without requiring more dialogue turns."
        ),
        parameters={
            "type": "object",
            "properties": {
                "defense_buildings": {"type": "integer", "minimum": 0},
                "defense_infantry": {"type": "integer", "minimum": 0},
                "defense_vehicles": {"type": "integer", "minimum": 0},
                "defense_aircraft": {"type": "integer", "minimum": 0},
                "credit_reserve": {"type": "integer", "minimum": 0},
            },
        },
        timeout=8.0,
    )
    async def mental_omega_develop_base(
        self, defense_buildings: Any = 6, defense_infantry: Any = 12,
        defense_vehicles: Any = 6, defense_aircraft: Any = 4,
        credit_reserve: Any = None, **_,
    ):
        try:
            await self._ensure_control_for_order()
            health = await asyncio.to_thread(self._client.health)
            capabilities = health.get("capabilities", {})
            if not isinstance(capabilities, dict) or capabilities.get("production") is not True:
                raise PolicyError("当前 MO 端没有可用的原生生产遥测")
            if capabilities.get("building_placement") is not True:
                raise PolicyError("请覆盖新版 MO 端文件；当前桥不支持自动放置建筑")
            now = time.monotonic()
            existing = self._deduplicate_base_development_tasks()
            if existing is not None:
                return {
                    "summary": "existing base-development controller reused",
                    "accepted": True,
                    "continuous": True,
                    "reused": True,
                    "task_id": existing["task_id"],
                    "task": self._public_base_task(existing),
                }
            task_id = uuid.uuid4().hex[:12]
            task = {
                "task_id": task_id,
                "kind": "base_development",
                "status": "starting",
                # Background polling may only execute tasks created by an
                # explicit commander request (or an explicitly authorized
                # autonomous-victory intent).
                "commander_authorized": True,
                "created_at": now,
                "last_action_at": 0.0,
                "active_production_task_id": None,
                "active_production_task_ids": {},
                "committed_counts": {},
                "placed_counts": {},
                "used_upgrade_hosts": {},
                "defense_buildings": max(0, int(defense_buildings)),
                "force_targets": {
                    "infantry": max(0, int(defense_infantry)),
                    "vehicle": max(0, int(defense_vehicles)),
                    "aircraft": max(0, int(defense_aircraft)),
                },
                "credit_reserve": (
                    None if credit_reserve is None else max(0, int(credit_reserve))
                ),
                "doctrine": get_doctrine(),
                "decisions": 0,
                "placement_candidates_tested": 0,
                "initial_completed": False,
                "last_reason": "读取战场与建造栏",
                "last_error": None,
            }
            self._base_tasks[task_id] = task
            state = await asyncio.to_thread(self._client.state)
            await asyncio.to_thread(self._run_base_development_task, task, state)
            return {
                "summary": "已接管基地发展；会持续处理展开、经济、电力、建筑落地、防御建筑和防御部队",
                "accepted": True,
                "continuous": True,
                "task_id": task_id,
                "task": self._public_base_task(task),
            }
        except (BridgeError, PolicyError, TypeError, ValueError) as exc:
            return {"summary": f"基地发展任务启动失败：{exc}", "accepted": False}

    @llm_tool(
        name="mental_omega_configure_opening_doctrine",
        description=(
            "Save a persistent custom Mental Omega opening and expansion doctrine. Use this "
            "when the commander explains a preferred build order such as barracks, refinery, "
            "power, war factory, four harvesters, second refinery, then technology. The "
            "doctrine is reused by future base-development tasks until replaced."
        ),
        parameters={
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "sequence": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string"},
                            "count": {"type": "integer", "minimum": 1},
                            "kind": {"type": "string"},
                            "optional": {"type": "boolean"},
                        },
                        "required": ["query"],
                    },
                },
                "expansion_policy": {"type": "string"},
                "distance_cells": {"type": "integer", "minimum": 1},
                "ore_buffer_cells": {"type": "integer", "minimum": 0},
                "harvester_target": {"type": "integer", "minimum": 0},
            },
            "required": ["sequence"],
        },
        timeout=5.0,
    )
    async def mental_omega_configure_opening_doctrine(
        self, sequence: Any = None, name: Any = "default",
        expansion_policy: Any = "nearest_ore", distance_cells: Any = 6,
        ore_buffer_cells: Any = 3, harvester_target: Any = 4, **_,
    ):
        if not isinstance(sequence, list) or not sequence:
            return {"summary": "开局顺序不能为空", "accepted": False}
        cleaned = []
        for item in sequence:
            if not isinstance(item, dict) or not str(item.get("query") or "").strip():
                return {"summary": "每个开局步骤都必须有 query", "accepted": False}
            cleaned.append({
                "query": str(item["query"]).strip(),
                "count": max(1, int(item.get("count", 1))),
                **({"kind": str(item["kind"])} if item.get("kind") else {}),
                **({"optional": True} if item.get("optional") is True else {}),
            })
        doctrine = {
            "name": str(name or "default"),
            "sequence": cleaned,
            "expansion": {
                "policy": str(expansion_policy or "nearest_ore"),
                "distance_cells": max(1, int(distance_cells)),
                "ore_buffer_cells": max(0, int(ore_buffer_cells)),
            },
            "harvester_target": max(0, int(harvester_target)),
        }
        try:
            await asyncio.to_thread(save_doctrine, str(name or "default"), doctrine)
        except (OSError, TypeError, ValueError) as exc:
            return {"summary": f"保存开局策略失败：{exc}", "accepted": False}
        return {"summary": f"已记住开局策略“{name or 'default'}”，下次发展基地自动使用", "accepted": True, "doctrine": doctrine}

    @llm_tool(
        name="mental_omega_maintain_force",
        description=(
            "Persistently maintain an exact owned count of one named infantry, vehicle, or "
            "aircraft type. ALWAYS use this for requests such as 始终维持弓箭手50个, "
            "坦克少于20就补充, or 持续补兵. It reads native House owned counts, including "
            "passengers and limbo objects, subtracts the live factory queue, and produces only "
            "the real deficit. The task has no expiry and resumes after casualties or unlocks. "
            "Set target_count=0 to cancel matching maintenance goals."
        ),
        parameters={
            "type": "object",
            "properties": {
                "product": {"type": "string"},
                "target_count": {"type": "integer", "minimum": 0},
                "credit_reserve": {"type": "integer", "minimum": 0},
            },
            "required": ["product", "target_count"],
        },
        timeout=8.0,
    )
    async def mental_omega_maintain_force(
        self, product: Any = None, target_count: Any = None,
        credit_reserve: Any = None, **_,
    ):
        try:
            query = str(product or "").strip()
            if not query:
                raise PolicyError("product is required")
            if isinstance(target_count, bool) or not isinstance(target_count, int) or target_count < 0:
                raise PolicyError("target_count must be a non-negative integer")
            await self._ensure_control_for_order()
            profiles = [
                item for item in catalog_summary(query)
                if item.get("kind") in {"infantry", "vehicle", "aircraft"}
                and item.get("is_ai_only") is not True
                and int(item.get("tech_level") or 0) >= 0
            ]
            if not profiles:
                raise PolicyError(f"没有识别到可生产的指定兵种：{query}")
            type_ids = [str(item["type_id"]).upper() for item in profiles]
            matching = [
                task for task in self._force_tasks.values()
                if task.get("status") != "cancelled"
                and set(task.get("type_ids", [])) == set(type_ids)
            ]
            if target_count == 0:
                for task in matching:
                    task["status"] = "cancelled"
                    task["completed_at"] = time.monotonic()
                return {
                    "summary": f"已取消 {len(matching)} 个“{query}”持续补员目标",
                    "cancelled": bool(matching), "cancelled_count": len(matching),
                }
            now = time.monotonic()
            task = matching[0] if matching else {
                "task_id": uuid.uuid4().hex[:12], "kind": "force_maintenance",
                "created_at": now, "orders_issued": 0, "produced_requested": 0,
                "target_reached_once": False, "last_owned_count": None,
            }
            task.update({
                "query": query, "type_ids": type_ids,
                "display_name": str(profiles[0].get("display_name") or query),
                "target_count": target_count,
                "credit_reserve": (
                    None if credit_reserve is None else max(0, int(credit_reserve))
                ),
                "status": "checking", "last_error": None,
            })
            self._force_tasks[task["task_id"]] = task
            state = await asyncio.to_thread(self._client.state)
            await asyncio.to_thread(self._run_force_maintenance_task, task, state)
            return {
                "summary": f"已建立持续补员目标：{task['display_name']} 始终维持 {target_count} 个",
                "accepted": True, "continuous": True,
                "task_id": task["task_id"], "task": self._public_force_task(task),
            }
        except (BridgeError, PolicyError, TypeError, ValueError) as exc:
            return {"summary": f"持续补员任务创建失败：{exc}", "accepted": False}

    @llm_tool(
        name="mental_omega_assign_defense",
        description=(
            "Assign a persistent defensive force to a named base, ore field, visible landmark, "
            "or map region. The task recruits matching owned units, attacks only visible enemies "
            "inside its pursuit radius, returns units that leave the sector, guards while quiet, "
            "and recruits replacements after losses. Use maintain_count to also keep producing "
            "that named force strength indefinitely."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "destination": {"type": "string"},
                "unit_count": {"type": "integer", "minimum": 1},
                "guard_radius": {"type": "integer", "minimum": 2},
                "pursuit_radius": {"type": "integer", "minimum": 3},
                "maintain_count": {"type": "integer", "minimum": 0},
            },
            "required": ["query", "destination"],
        },
        timeout=10.0,
    )
    async def mental_omega_assign_defense(
        self, query: Any = None, destination: Any = None,
        unit_count: Any = None, guard_radius: Any = 10,
        pursuit_radius: Any = 24, maintain_count: Any = 0, **_,
    ):
        try:
            await self._ensure_control_for_order()
            query_text = str(query or "").strip()
            destination_text = str(destination or "").strip()
            if not query_text or not destination_text:
                raise PolicyError("query and destination are required")
            state = await asyncio.to_thread(self._client.state)
            units = select_owned_units(state, query_text)
            maintain_value = (
                int(maintain_count)
                if isinstance(maintain_count, int) and not isinstance(maintain_count, bool)
                else 0
            )
            if not units and maintain_value <= 0:
                raise PolicyError("当前没有匹配且可控制的防御单位")
            desired = (
                int(unit_count) if unit_count is not None
                else maintain_value if maintain_value > 0
                else len(units)
            )
            if desired < 1:
                raise PolicyError("unit_count must be positive")
            anchors = units or [
                item for item in state.get("visible_entities", [])
                if isinstance(item, dict) and item.get("relation") == "self"
                and isinstance(item.get("cell"), dict)
            ]
            if not anchors:
                raise PolicyError("没有可用于解析防区的己方位置")
            origin = self._centroid(anchors)
            landmark = resolve_map_region_destination(state, destination_text) or resolve_visible_destination(
                state, destination_text, origin
            )
            anchor = landmark.get("cell")
            if not isinstance(anchor, dict):
                raise PolicyError("防区没有可用的位置")
            guard = max(2, int(guard_radius))
            pursuit = max(guard + 1, int(pursuit_radius))
            selected = sorted(
                units, key=lambda item: (
                    self._distance_sq(item, {"cell": anchor}), int(item["object_id"])
                )
            )[:desired]
            selected_ids = {int(item["object_id"]) for item in selected}
            for attack_task in self._attack_tasks.values():
                attack_task["unit_ids"] = [
                    value for value in attack_task.get("unit_ids", [])
                    if int(value) not in selected_ids
                ]
            for adaptive in self._adaptive_tasks.tasks(include_finished=False):
                adaptive_ids = {
                    int(item["object_id"]) for item in adaptive.get("units", [])
                    if isinstance(item.get("object_id"), int)
                }
                if adaptive_ids & selected_ids:
                    self._adaptive_tasks.cancel(str(adaptive["task_id"]))
            now = time.monotonic()
            task_id = uuid.uuid4().hex[:12]
            task = {
                "task_id": task_id, "kind": "area_defense",
                "query": query_text, "destination": destination_text,
                "anchor": {"x": int(anchor["x"]), "y": int(anchor["y"])},
                "unit_ids": sorted(selected_ids), "desired_count": desired,
                "guard_radius": guard, "pursuit_radius": pursuit,
                "status": "deploying", "created_at": now,
                "last_order_at": {}, "orders_issued": 0, "losses": 0,
                "reinforcements": 0, "maintain_task_id": None,
            }
            self._defense_tasks[task_id] = task
            if isinstance(maintain_count, int) and not isinstance(maintain_count, bool) and maintain_count > 0:
                maintain_result = await self.mental_omega_maintain_force(
                    product=query_text, target_count=maintain_count
                )
                if maintain_result.get("accepted") is True:
                    task["maintain_task_id"] = maintain_result.get("task_id")
            await asyncio.to_thread(self._run_defense_task, task, state)
            return {
                "summary": f"已分配 {len(selected)} 个单位持续防守{destination_text}，越界后会返回并自动补充编队空缺",
                "accepted": True, "continuous": True, "task_id": task_id,
                "task": self._public_defense_task(task),
            }
        except (BridgeError, PolicyError, TypeError, ValueError) as exc:
            return {"summary": f"防御任务创建失败：{exc}", "accepted": False}

    @llm_tool(
        name="mental_omega_force_status",
        description="Inspect persistent exact-count force maintenance goals and their live owned/queued deficit.",
        parameters={
            "type": "object",
            "properties": {"task_id": {"type": "string"}},
        },
        timeout=5.0,
    )
    async def mental_omega_force_status(self, task_id: Any = None, **_):
        selected = str(task_id or "").strip()
        tasks = [self._public_force_task(task) for task in self._force_tasks.values()]
        if selected:
            tasks = [task for task in tasks if task.get("task_id") == selected]
        return {"summary": f"当前有 {len(tasks)} 个持续补员目标", "tasks": tasks}

    @llm_tool(
        name="mental_omega_defense_status",
        description="Inspect persistent sector-defense assignments, replacements, guard radius, and pursuit radius.",
        parameters={
            "type": "object",
            "properties": {"task_id": {"type": "string"}},
        },
        timeout=5.0,
    )
    async def mental_omega_defense_status(self, task_id: Any = None, **_):
        selected = str(task_id or "").strip()
        tasks = [self._public_defense_task(task) for task in self._defense_tasks.values()]
        if selected:
            tasks = [task for task in tasks if task.get("task_id") == selected]
        return {"summary": f"当前有 {len(tasks)} 个持续防御任务", "tasks": tasks}

    @llm_tool(
        name="mental_omega_runtime_signals",
        description=(
            "Inspect writable task flags, pending priority interrupts, timers and DMA "
            "channel sequence. Use this to verify that a promised long-running action "
            "actually has a persistent wake-up condition instead of only a verbal promise."
        ),
        parameters={
            "type": "object",
            "properties": {"prefix": {"type": "string"}},
        },
        timeout=5.0,
    )
    async def mental_omega_runtime_signals(self, prefix: Any = None, **_):
        return {
            "summary": "已读取副官运行时标志与中断状态",
            "flags": self._command_runtime.flags.snapshot(
                str(prefix).strip() if prefix else None
            ),
            "pending_interrupts": self._command_runtime.interrupts.pending,
            "active_timers": self._command_runtime.timers.active,
            "dma": self._command_runtime.dma.snapshot(),
            "runtime_flag_count": len(self._command_runtime.flags.snapshot()),
            "report_policy": self._report_policy.snapshot(),
        }

    @llm_tool(
        name="mental_omega_configure_reports",
        description=(
            "Configure or completely stop proactive battlefield reports. ALWAYS call this "
            "when the commander says stop/pause/cancel reports. Supports event-kind filters, "
            "minimum severity, per-event cooldown, rate limiting, and clearing existing watches."
        ),
        parameters={
            "type": "object",
            "properties": {
                "enabled": {"type": "boolean"},
                "event_kinds": {"type": "array", "items": {"type": "string"}},
                "minimum_severity": {"type": "integer"},
                "cooldown_seconds": {"type": "number", "minimum": 0},
                "max_per_minute": {"type": "integer", "minimum": 0},
                "under_attack_enabled": {"type": "boolean"},
                "production_completion_enabled": {"type": "boolean"},
                "clear_existing_watches": {"type": "boolean"},
            },
        },
        timeout=5.0,
    )
    async def mental_omega_configure_reports(
        self, enabled: Any = None, event_kinds: Any = None,
        minimum_severity: Any = None, cooldown_seconds: Any = None,
        max_per_minute: Any = None, under_attack_enabled: Any = None,
        production_completion_enabled: Any = None,
        clear_existing_watches: Any = False, **_
    ):
        allowed = None
        if event_kinds is not None:
            if not isinstance(event_kinds, list):
                return {"summary": "event_kinds 必须是数组", "configured": False}
            allowed = [str(item) for item in event_kinds]
        rate = ...
        if max_per_minute is not None:
            rate = None if int(max_per_minute) == 0 else int(max_per_minute)
        policy = self._report_policy.configure(
            enabled=enabled if isinstance(enabled, bool) else None,
            allowed_kinds=allowed,
            minimum_severity=(int(minimum_severity) if minimum_severity is not None else None),
            cooldown_seconds=(float(cooldown_seconds) if cooldown_seconds is not None else None),
            max_per_minute=rate,
        )
        if isinstance(under_attack_enabled, bool):
            self._under_attack_reports_enabled = under_attack_enabled
        elif isinstance(enabled, bool):
            self._under_attack_reports_enabled = enabled
        if isinstance(production_completion_enabled, bool):
            self._production_completion_reports_enabled = production_completion_enabled
        cleared = 0
        if clear_existing_watches is True or enabled is False:
            cleared = len(self._watches) + len(self._alert_watches)
            self._watches.clear()
            self._alert_watches.clear()
        return {
            "summary": "主动汇报已开启" if policy["enabled"] else "主动汇报已完全关闭",
            "configured": True,
            "policy": policy,
            "under_attack_reports_enabled": self._under_attack_reports_enabled,
            "production_completion_reports_enabled": self._production_completion_reports_enabled,
            "cleared_watch_count": cleared,
        }

    @llm_tool(
        name="mental_omega_set_runtime_flag",
        description=(
            "Set or clear a writable runtime flag for custom plans, interrupts and timers. "
            "At least user_flag_000 through user_flag_127 are preallocated; descriptive "
            "custom flag names are also supported. A changed value emits flag_changed."
        ),
        parameters={
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "value": {},
                "priority": {"type": "integer"},
            },
            "required": ["name", "value"],
        },
        timeout=5.0,
    )
    async def mental_omega_set_runtime_flag(
        self, name: Any = None, value: Any = None, priority: Any = 60, **_
    ):
        try:
            changed = self._command_runtime.flags.set(
                str(name or ""), value, source="llm", priority=int(priority)
            )
            return {
                "summary": f"运行时标志 {name} 已{'更新并触发中断' if changed else '保持不变'}",
                "accepted": True,
                "changed": changed,
                "name": str(name),
                "value": value,
            }
        except (TypeError, ValueError) as exc:
            return {"summary": f"设置运行时标志失败：{exc}", "accepted": False}

    @llm_tool(
        name="mental_omega_unit_info",
        description=(
            "Look up Mental Omega unit or building knowledge by a natural Chinese or "
            "English name. ALWAYS use this before judging whether an object is a combat, "
            "economic, transport, engineer, civilian or environment unit, or when the user "
            "asks for damage, range, ROF, armor, passengers or deployment data. Use it "
            "for faction, TechLevel, Tier, battlefield class, anti-infantry, anti-armor, "
            "anti-air, area anti-infantry and threat-level judgments. "
            "instead of asking the user to clarify a recognized nickname. The factual "
            "catalog and editable player alias file are bundled with the plugin. This is "
            "reference knowledge, not live detection."
        ),
        parameters={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
        timeout=5.0,
    )
    async def mental_omega_unit_info(self, query: Any = None, **_):
        query = str(query or "").strip()
        matches = catalog_summary(query)
        if not matches:
            return {"summary": f"单位资料库中没有识别出“{query}”", "matches": []}
        return {
            "summary": f"已识别“{query}”为{matches[0].get('display_name') or matches[0].get('type_id')}",
            "matches": matches,
            "live_data_checked": False,
        }

    @llm_tool(
        name="mental_omega_tutorial_knowledge",
        description=(
            "Use the bundled structured lessons derived from the downloaded Mental Omega "
            "tutorial videos. Query tactical rules by topic/faction, or request a live "
            "recommendation that combines economy, visible threats, loading state and "
            "the current intent. This is an explainable strategy layer, not model training."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "faction": {"type": "string"},
                "intent": {"type": "string"},
                "live_recommendation": {"type": "boolean"},
            },
        },
        timeout=5.0,
    )
    async def mental_omega_tutorial_knowledge(
        self,
        query: Any = None,
        faction: Any = None,
        intent: Any = None,
        live_recommendation: Any = False,
        **_,
    ):
        if bool(live_recommendation):
            state = await asyncio.to_thread(self._client.state)
            result = recommend_tactics(state, str(intent or query or ""))
            return {
                "summary": f"已根据当前战场匹配 {len(result['rules'])} 条教程策略",
                **result,
            }
        rows = tutorial_search(str(query or ""), str(faction) if faction else None)
        return {
            "summary": f"教程知识匹配到 {len(rows)} 条可执行规则",
            "rules": rows,
            "source_count": len(tutorial_sources()),
        }

    @llm_tool(
        name="mental_omega_visible_units",
        description=(
            "Read live visible battlefield entities. ALWAYS call this before answering "
            "whether a named unit is currently present or where it is. It only includes "
            "objects the player can currently see; hidden-map, cloaked and unidentified "
            "disguised enemies are excluded by the native bridge."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "relation": {
                    "type": "string",
                    "enum": ["self", "ally", "enemy", "neutral"],
                },
            },
        },
        timeout=5.0,
    )
    async def mental_omega_visible_units(
        self, query: Any = None, relation: Any = None, **_
    ):
        try:
            query_text = str(query).strip() if query else ""
            name_query = selection_name_query(query_text)
            kind = selection_kind(query_text)
            type_ids = resolve_type_ids(name_query) if name_query else []
            if hasattr(self._client, "query_entities") and (
                not name_query or type_ids
            ):
                result = await asyncio.to_thread(
                    self._client.query_entities,
                    {
                        "relation": str(relation) if relation else None,
                        "type_ids": type_ids or None,
                        "kind": kind,
                        "region": spatial_region(query_text) or "all",
                        "visible": True,
                        "limit": 200,
                    },
                )
                matched = int(result.get("matched_count", 0))
                telemetry_complete = not result.get("entity_feed", {}).get(
                    "truncated", False
                )
                summary = f"实时战场数据中找到 {matched} 个符合条件的可见目标"
                if matched == 0 and not telemetry_complete:
                    summary += "；实体数据已达容量上限，不能据此断言目标不存在"
                return {
                    "summary": summary,
                    "entities": result.get("entities", []),
                    "matched_count": matched,
                    "returned_count": result.get("returned_count", 0),
                    "grouped_summaries": result.get("grouped_summaries", {}),
                    "snapshot_age_ms": result.get("snapshot_age_ms"),
                    "telemetry_complete": telemetry_complete,
                    "fair_play": "仅包含玩家当前可见且已正确识别的单位",
                }
            state = await asyncio.to_thread(self._client.state)
            if query:
                entities = match_visible_entities(
                    state, str(query), str(relation) if relation else None
                )
            else:
                raw = state.get("visible_entities")
                entities = [
                    item
                    for item in raw if isinstance(item, dict)
                    and item.get("visible") is True
                    and (not relation or item.get("relation") == relation)
                ] if isinstance(raw, list) else []
            feed = state.get("entity_feed")
            telemetry_complete = not (
                isinstance(feed, dict) and feed.get("truncated") is True
            )
            summary = f"实时战场数据中找到 {len(entities)} 个符合条件的可见目标"
            if not entities and not telemetry_complete:
                summary += "；实体数据已达容量上限，不能据此断言目标不存在"
            return {
                "summary": summary,
                "entities": entities,
                "snapshot_age_ms": state.get("snapshot_age_ms"),
                "telemetry_complete": telemetry_complete,
                "fair_play": "仅包含玩家当前可见且已正确识别的单位",
            }
        except BridgeError as exc:
            return {"summary": f"读取实时单位数据失败：{exc}", "entities": []}

    @llm_tool(
        name="mental_omega_owned_units",
        description=(
            "Read the player's live unit status, health and passenger telemetry. ALWAYS "
            "use this for requests about all units, damaged units, repair targets, unit "
            "status, transports, or whether a transport contains engineers. Set "
            "damaged_only=true ONLY when the current user request explicitly asks for "
            "damaged/injured units. The result distinguishes total owned objects, independently "
            "controllable objects, and tactical-team members; report all three when they differ. "
            "Never infer health, passengers, or unit counts from screenshots."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "damaged_only": {"type": "boolean"},
                "health_below": {"type": "number", "minimum": 0.01, "maximum": 1.0},
                "include_buildings": {"type": "boolean"},
            },
        },
        timeout=5.0,
    )
    async def mental_omega_owned_units(
        self,
        query: Any = None,
        damaged_only: Any = False,
        health_below: Any = 1.0,
        include_buildings: Any = False,
        **_,
    ):
        try:
            query_text = str(query).strip() if query else ""
            name_query = selection_name_query(query_text)
            kind = selection_kind(query_text)
            type_ids = resolve_type_ids(name_query) if name_query else []
            if hasattr(self._client, "query_entities") and (
                not name_query or type_ids
            ):
                filters = {
                    "relation": "self",
                    "type_ids": type_ids or None,
                    "kind": kind,
                    "region": spatial_region(query_text) or "all",
                    "alive": True,
                    "visible": True,
                    "exclude_kind": None if include_buildings is True else "building",
                    "limit": 200,
                }
                name_result = await asyncio.to_thread(
                    self._client.query_entities, filters
                )
                matching_count = int(name_result.get("matched_count", 0))
                name_summaries = name_result.get("grouped_summaries", {})
                controllable_count = int(
                    name_summaries.get("controllable", {}).get("true", 0)
                )
                tactical_team_count = int(
                    name_summaries.get("in_tactical_team", {}).get("true", 0)
                )
                result = name_result
                if damaged_only is True:
                    filters["health_below"] = max(
                        0.01, min(float(health_below), 1.0)
                    )
                    result = await asyncio.to_thread(
                        self._client.query_entities, filters
                    )
                matched = int(result.get("matched_count", 0))
                summary = (
                    f"实时数据中找到 {matching_count} 个名称匹配的己方对象，"
                    f"其中 {matched} 个符合受损条件"
                    if damaged_only is True
                    else (
                        f"实时数据中找到 {matched} 个符合条件的己方对象，"
                        f"其中 {controllable_count} 个可独立控制"
                    )
                )
                if tactical_team_count:
                    summary += f"，{tactical_team_count} 个属于战术小队"
                telemetry_complete = not result.get("entity_feed", {}).get(
                    "truncated", False
                )
                if matched == 0 and not telemetry_complete:
                    summary += "；实体数据已达容量上限，不能据此断言目标不存在"
                response = {
                    "summary": summary,
                    "units": result.get("entities", []),
                    "matched_count": matched,
                    "returned_count": result.get("returned_count", 0),
                    "name_matched_count": matching_count,
                    "controllable_count": controllable_count,
                    "tactical_team_count": tactical_team_count,
                    "damaged_filter_applied": damaged_only is True,
                    "grouped_summaries": result.get("grouped_summaries", {}),
                    "snapshot_age_ms": result.get("snapshot_age_ms"),
                    "telemetry_complete": telemetry_complete,
                }
                if damaged_only is True:
                    response["name_matches"] = name_result.get("entities", [])
                return response
            state = await asyncio.to_thread(self._client.state)
            matching_units = select_owned_units(
                state,
                query_text or None,
                include_buildings=include_buildings is True,
            )
            if damaged_only is True:
                threshold = max(0.01, min(float(health_below), 1.0))
                units = [
                    unit for unit in matching_units
                    if isinstance(unit.get("health_ratio"), (int, float))
                    and not isinstance(unit.get("health_ratio"), bool)
                    and float(unit["health_ratio"]) < threshold
                ]
                summary = (
                    f"实时数据中找到 {len(matching_units)} 个名称匹配的己方可控对象，"
                    f"其中 {len(units)} 个符合受损条件"
                )
            else:
                units = matching_units
                summary = f"实时数据中找到 {len(units)} 个符合条件的己方可控对象"
            feed = state.get("entity_feed")
            telemetry_complete = not (
                isinstance(feed, dict) and feed.get("truncated") is True
            )
            if not units and not telemetry_complete:
                summary += "；实体数据已达容量上限，不能据此断言目标不存在"
            response = {
                "summary": summary,
                "units": units,
                "name_matched_count": len(matching_units),
                "damaged_filter_applied": damaged_only is True,
                "snapshot_age_ms": state.get("snapshot_age_ms"),
                "telemetry_complete": telemetry_complete,
            }
            if damaged_only is True:
                response["name_matches"] = matching_units
            return response
        except (BridgeError, PolicyError, TypeError, ValueError) as exc:
            return {"summary": f"读取己方单位状态失败：{exc}", "units": []}

    @llm_tool(
        name="mental_omega_assign_control_group",
        description=(
            "Create or replace a native in-game Ctrl+number group from live owned units. Use for "
            "requests such as '把所有残血单位编为1' and '把地图左边的我方单位编为1'. "
            "Natural spatial selectors include map left/right/top/bottom halves. For one user "
            "sentence containing multiple different groups, use "
            "mental_omega_assign_multiple_control_groups instead. "
            "damaged_only=true selects only units below health_below. A successful result means "
            "the native game thread acknowledged every assignment."
        ),
        parameters={
            "type": "object",
            "properties": {
                "group_number": {"type": "integer", "minimum": 0, "maximum": 9},
                "query": {"type": "string"},
                "damaged_only": {"type": "boolean"},
                "health_below": {"type": "number", "minimum": 0.01, "maximum": 1.0},
                "include_buildings": {"type": "boolean"},
            },
            "required": ["group_number"],
        },
        timeout=10.0,
    )
    async def mental_omega_assign_control_group(
        self,
        group_number: Any = None,
        query: Any = None,
        damaged_only: Any = False,
        health_below: Any = 1.0,
        include_buildings: Any = False,
        **_,
    ):
        try:
            group = self._group_number(group_number)
            await self._ensure_control_for_order()
            query_text = str(query).strip() if query else ""
            region = spatial_region(query_text) or "all"
            kind = selection_kind(query_text) or "all"
            name_query = selection_name_query(query_text)
            type_ids = resolve_type_ids(name_query) if name_query else []
            use_native_batch = (
                damaged_only is not True
                and include_buildings is not True
                and (not name_query or bool(type_ids))
            )
            if use_native_batch:
                result = await asyncio.to_thread(
                    self._client.assign_group_batch,
                    {
                        "request_id": str(uuid.uuid4()),
                        "group_number": group,
                        "replace_group": True,
                        "region": region,
                        "kind": kind,
                        "type_ids": type_ids,
                    },
                )
                matched = int(result.get("matched_count", 0))
                accepted_count = int(result.get("accepted_count", 0))
                failed_count = int(result.get("failed_count", 0))
                if result.get("accepted") is not True or accepted_count <= 0:
                    detail = result.get("error") or "游戏没有接收批量编组"
                    return {
                        "summary": f"编组失败：{detail}",
                        "accepted": False,
                        "group_number": group,
                        "matched_count": matched,
                        "accepted_count": accepted_count,
                        "failed_count": failed_count,
                    }
                self._groups[group] = {
                    "object_ids": [],
                    "labels": type_ids,
                    "selector": {"region": region, "kind": kind, "type_ids": type_ids},
                    "created_at": time.time(),
                }
                region_labels = {
                    "all": "全部地图",
                    "left": "地图左半区",
                    "right": "地图右半区",
                    "top": "地图上半区",
                    "bottom": "地图下半区",
                }
                complete = failed_count == 0 and accepted_count == matched
                summary = (
                    f"已将{region_labels[region]}内 {accepted_count} 个符合条件的"
                    f"己方可控单位编为 {group}"
                )
                if not complete:
                    summary += (
                        f"；另有 {failed_count} 个对象未编入，通常是战术小队成员"
                        "或当前不可独立控制的脚本单位"
                    )
                return {
                    "summary": summary,
                    "accepted": True,
                    "complete": complete,
                    "group_number": group,
                    "matched_count": matched,
                    "accepted_count": accepted_count,
                    "failed_count": failed_count,
                    "selector": {"region": region, "kind": kind, "type_ids": type_ids},
                    "scope": "native_game_batch",
                    "native_confirmed": True,
                }

            state = await asyncio.to_thread(self._client.state)
            units = select_owned_units(
                state,
                query_text or None,
                damaged_only=damaged_only is True,
                health_below=float(health_below),
                include_buildings=include_buildings is True,
            )
            if not units:
                raise PolicyError("没有符合条件的己方可控单位")
            accepted, failed = [], []
            for index, unit in enumerate(units):
                result = await self._submit_unit_command(
                    state,
                    int(unit["object_id"]),
                    "assign_group",
                    group_number=group,
                    replace_group=index == 0,
                )
                (accepted if result.get("accepted") else failed).append(
                    {"unit": entity_label(unit), "unit_id": unit["object_id"], **result}
                )
            if failed:
                return {
                    "summary": f"原生编组失败：{len(failed)} 个单位未被游戏接受",
                    "accepted": False,
                    "accepted_count": len(accepted),
                    "failed_count": len(failed),
                    "failures": failed,
                }
            wanted_ids = {int(item["object_id"]) for item in units}
            confirmed_ids: set[int] = set()
            confirmation_deadline = (
                time.monotonic() + GROUP_TELEMETRY_CONFIRM_SECONDS
            )
            while time.monotonic() < confirmation_deadline:
                await asyncio.sleep(0.05)
                probe_state = await asyncio.to_thread(self._client.state)
                confirmed_ids = {
                    int(item["object_id"])
                    for item in probe_state.get("visible_entities", [])
                    if isinstance(item, dict)
                    and item.get("relation") == "self"
                    and item.get("group_number") == group
                    and item.get("object_id") in wanted_ids
                }
                if confirmed_ids == wanted_ids:
                    break
            self._groups[group] = {
                "object_ids": [int(item["object_id"]) for item in units],
                "labels": [entity_label(item) for item in units],
                "created_at": time.time(),
            }
            telemetry_confirmed = confirmed_ids == wanted_ids
            if not telemetry_confirmed:
                self.logger.warning(
                    "Native group %s was accepted but telemetry has not refreshed for unit ids: %s",
                    group,
                    sorted(wanted_ids - confirmed_ids),
                )
            response = {
                "summary": f"已将 {len(units)} 个单位加入副官控制组 {group}",
                "accepted": True,
                "group_number": group,
                "units": units,
                "scope": "native_game",
                "native_confirmed": True,
            }
            if telemetry_confirmed:
                response["telemetry_confirmed"] = True
            return response
        except (BridgeError, PolicyError, TypeError, ValueError) as exc:
            return {"summary": f"编组失败：{exc}", "accepted": False}

    @llm_tool(
        name="mental_omega_assign_multiple_control_groups",
        description=(
            "Execute a compound native grouping order containing TWO OR MORE different group "
            "assignments in the user's original order. ALWAYS use this for sentences such as "
            "'所有部队编为1，磁暴步兵单独编为2' or '左边编1，右边编2'. Keep broad "
            "selectors broad: '所有部队' must remain all units and must not be narrowed to the "
            "named subset in another assignment. Common nicknames are accepted."
        ),
        parameters={
            "type": "object",
            "properties": {
                "assignments": {
                    "type": "array",
                    "minItems": 2,
                    "maxItems": 10,
                    "items": {
                        "type": "object",
                        "properties": {
                            "group_number": {"type": "integer", "minimum": 0, "maximum": 9},
                            "query": {"type": "string"},
                            "damaged_only": {"type": "boolean"},
                            "health_below": {"type": "number", "minimum": 0.01, "maximum": 1.0},
                        },
                        "required": ["group_number", "query"],
                    },
                }
            },
            "required": ["assignments"],
        },
        timeout=20.0,
    )
    async def mental_omega_assign_multiple_control_groups(
        self, assignments: Any = None, **_
    ):
        if not isinstance(assignments, list) or not 2 <= len(assignments) <= 10:
            return {
                "summary": "复合编组失败：assignments 必须包含 2 到 10 条编组指令",
                "accepted": False,
            }
        results = []
        for assignment in assignments:
            if not isinstance(assignment, dict):
                return {"summary": "复合编组失败：每条指令必须是对象", "accepted": False}
            result = await self.mental_omega_assign_control_group(
                group_number=assignment.get("group_number"),
                query=assignment.get("query"),
                damaged_only=assignment.get("damaged_only", False),
                health_below=assignment.get("health_below", 1.0),
            )
            results.append(result)
        accepted = all(item.get("accepted") is True for item in results)
        complete = accepted and all(item.get("complete", True) is True for item in results)
        return {
            "summary": "；".join(str(item.get("summary", "")) for item in results),
            "accepted": accepted,
            "complete": complete,
            "results": results,
        }

    @llm_tool(
        name="mental_omega_move_units",
        description=(
            "Atomically move a companion group or a natural-language selection of owned units to a "
            "human-readable destination, including map corners and directional ore regions even "
            "without a visible landmark. Use for ordinary infantry, vehicles and "
            "aircraft, not only harvesters. It also supports human relative requests such "
            "as moving left, nearby, or one or two cells. The dispatcher verifies real movement, "
            "tries alternate staging cells, and can take control from a tactical script only after "
            "a unit is observed stuck. Never ask the user for map cells and do not narrate retries."
        ),
        parameters={
            "type": "object",
            "properties": {
                "destination": {"type": "string"},
                "group_number": {"type": "integer", "minimum": 0, "maximum": 9},
                "query": {"type": "string"},
                "damaged_only": {"type": "boolean"},
            },
            "required": ["destination"],
        },
        timeout=12.0,
    )
    async def mental_omega_move_units(
        self,
        destination: Any = None,
        group_number: Any = None,
        query: Any = None,
        damaged_only: Any = False,
        **_,
    ):
        try:
            await self._ensure_control_for_order()
            state = await asyncio.to_thread(self._client.state)
            units = self._resolve_command_units(
                state, group_number, query, damaged_only is True
            )
            jobs = []
            if is_relative_move_request(str(destination)):
                relative_targets = [
                    relative_move_cells(state, unit["cell"], str(destination)) for unit in units
                ]
                destination_label = str(destination)
                for unit, candidates in zip(units, relative_targets):
                    jobs.append(self._move_one_adaptive(unit, candidates))
                results = await asyncio.gather(*jobs)
            else:
                origin = self._centroid(units)
                region_target = resolve_map_region_destination(state, str(destination))
                landmark = region_target or resolve_visible_destination(state, str(destination), origin)
                destination_label = (
                    str(destination)
                    if region_target
                    else str(landmark.get("location_label") or entity_label(landmark))
                )
                task = self._adaptive_tasks.create_move_task(
                    units,
                    landmark,
                    query=str(query).strip() if query else None,
                    destination=destination_label,
                    duration_seconds=int(
                        self._cfg.get("adaptive_move_task_seconds", 1800)
                    ),
                    arrival_radius=int(
                        self._cfg.get("adaptive_arrival_radius_cells", 5)
                    ),
                )
                await asyncio.to_thread(self._adaptive_tasks.tick, state)
                current = self._adaptive_tasks.tasks(
                    include_finished=True, task_id=task["task_id"]
                )[0]
                return {
                    "summary": (
                        f"已建立持续移动任务，{len(units)} 个单位会自行接近"
                        f"{destination_label}，遇阻时会换路并持续确认是否到达"
                    ),
                    "accepted": True,
                    "continuous": True,
                    "task_id": task["task_id"],
                    "destination": destination_label,
                    "accepted_count": sum(
                        int(item.get("accepted_orders", 0)) > 0
                        for item in current["units"]
                    ),
                    "verified_count": int(current.get("arrived_count") or 0),
                    "task": current,
                }
            accepted = [item for item in results if item.get("accepted")]
            verified = [item for item in accepted if item.get("verified")]
            failed = [item for item in results if not item.get("accepted")]
            stalled = [item for item in accepted if not item.get("verified")]
            if failed or stalled:
                summary = (
                    f"{len(verified)} 个单位已确认开始移动，"
                    f"{len(stalled)} 个未观察到位移，{len(failed)} 个下令失败"
                )
            else:
                summary = f"{len(verified)} 个单位已确认向{destination_label}移动"
            return {
                "summary": summary,
                "accepted": bool(accepted),
                "accepted_count": len(accepted),
                "verified_count": len(verified),
                "failed_count": len(failed),
                "destination": destination_label,
                "orders": accepted,
                "failures": failed,
            }
        except (BridgeError, PolicyError, TypeError, ValueError) as exc:
            return {"summary": f"单位移动失败：{exc}", "accepted": False}

    @llm_tool(
        name="mental_omega_tactical_advance",
        description=(
            "Continuously advance selected owned combat units toward a destination or player "
            "B marker. Visible enemies within engage_radius_cells interrupt movement and are "
            "attacked first; after the local threat disappears the original advance resumes. "
            "Use for commands such as 聚集后向上持续移动，发现敌人优先消灭 or 去标记点沿途清敌. "
            "fire_mode=concentrated focuses one target; distributed allocates different nearby "
            "targets. This is a persistent goal, not a one-step move."
        ),
        parameters={
            "type": "object",
            "properties": {
                "destination": {"type": "string"},
                "group_number": {"type": "integer", "minimum": 0, "maximum": 9},
                "query": {"type": "string"},
                "engage_radius_cells": {"type": "integer", "minimum": 3, "maximum": 64},
                "fire_mode": {
                    "type": "string", "enum": ["distributed", "concentrated"]
                },
                "regroup": {"type": "boolean"},
            },
            "required": ["destination"],
        },
        timeout=12.0,
    )
    async def mental_omega_tactical_advance(
        self, destination: Any = None, group_number: Any = None,
        query: Any = None, engage_radius_cells: Any = None,
        fire_mode: Any = "distributed", regroup: Any = True, **_,
    ):
        try:
            await self._ensure_control_for_order()
            state = await asyncio.to_thread(self._client.state)
            units = self._resolve_command_units(state, group_number, query, False)
            destination_text = str(destination or "").strip()
            origin = self._centroid(units)
            target = (
                resolve_map_region_destination(state, destination_text)
                or resolve_map_region_destination(state, f"地图{destination_text}")
                or resolve_visible_destination(state, destination_text, origin)
            )
            label = str(target.get("location_label") or destination_text)
            task = self._adaptive_tasks.create_tactical_advance_task(
                units,
                target,
                query=str(query).strip() if query else None,
                destination=label,
                duration_seconds=int(self._cfg.get("adaptive_move_task_seconds", 1800)),
                arrival_radius=int(self._cfg.get("adaptive_arrival_radius_cells", 5)),
                engage_radius=int(
                    engage_radius_cells
                    if engage_radius_cells is not None
                    else self._cfg.get("tactical_engage_radius_cells", 18)
                ),
                fire_mode=str(fire_mode or "distributed"),
                regroup=regroup is not False,
            )
            await asyncio.to_thread(self._adaptive_tasks.tick, state)
            current = self._adaptive_tasks.tasks(
                include_finished=True, task_id=task["task_id"]
            )[0]
            return {
                "summary": (
                    f"已建立向{label}推进的持续战术任务；部队会在指定范围内先交战，"
                    "局部清场后自动恢复原路线"
                ),
                "accepted": True,
                "continuous": True,
                "task_id": task["task_id"],
                "task": current,
            }
        except (BridgeError, PolicyError, TypeError, ValueError) as exc:
            return {"summary": f"战术推进启动失败：{exc}", "accepted": False}

    @llm_tool(
        name="mental_omega_attack_units",
        description=(
            "Command a native in-game control group or natural-language selection of owned "
            "units to attack enemies. target_query may name an enemy unit; faction words in "
            "unit names such as 盟军大兵 are names, while hostility is always decided from "
            "live relation telemetry. When omitted, choose the nearest visible enemy. Set "
            "continuous=true whenever the user says 清理所有敌军、继续清理、负责清剿、守住并消灭敌人 "
            "or otherwise delegates an ongoing combat duty. A continuous task keeps the "
            "selected units, waits when no enemy is visible, and automatically attacks the "
            "next nearest visible enemy after the current target dies or disappears. Never "
            "fire_control selects focus (集中/集火), spread (分散/火力分配), or auto "
            "(native threat evaluation); preserve it for the entire continuous task. "
            "Never target allies, hidden objects, or remembered positions. A specifically "
            "named neutral object may be attacked when the game's native cursor allows it."
        ),
        parameters={
            "type": "object",
            "properties": {
                "group_number": {"type": "integer", "minimum": 0, "maximum": 9},
                "query": {"type": "string"},
                "target_query": {"type": "string"},
                "fire_control": {
                    "type": "string",
                    "enum": ["focus", "spread", "auto"],
                },
                "continuous": {"type": "boolean"},
                "duration_seconds": {
                    "type": "integer",
                    "minimum": 30,
                    "maximum": 21600,
                },
            },
        },
        timeout=15.0,
    )
    async def mental_omega_attack_units(
        self,
        group_number: Any = None,
        query: Any = None,
        target_query: Any = None,
        fire_control: Any = None,
        continuous: Any = False,
        duration_seconds: Any = None,
        **_,
    ):
        try:
            await self._ensure_control_for_order()
            state = await asyncio.to_thread(self._client.state)
            units = self._resolve_command_units(state, group_number, query, False)
            target_text = self._specific_attack_query(target_query)
            control = self._resolve_fire_control(fire_control, target_query)
            # A generic phrase is still an explicit target selection when the
            # commander supplied it (for example "最近的建筑"). Only an
            # omitted target_query delegates selection entirely to native AA.
            explicit_target_argument = bool(str(target_query or "").strip())
            native_continuous_attack = (
                continuous is True and not target_text and not explicit_target_argument
                and control == "auto"
            )
            native_auto_attack = (
                continuous is not True and not target_text
                and not explicit_target_argument and control == "auto"
            )
            targets = await asyncio.to_thread(
                self._visible_attack_targets,
                state,
                "" if native_continuous_attack else target_text,
                True,
            ) if (native_continuous_attack or not native_auto_attack) else []
            if not targets and not native_continuous_attack and not native_auto_attack:
                raise PolicyError("当前视野内没有符合条件的可攻击敌军")
            accepted, failed = [], []
            assignments: dict[int, int] = {}
            if (native_continuous_attack and targets) or native_auto_attack:
                for unit in units:
                    if not self._aircraft_ready_for_attack(unit):
                        failed.append({
                            "unit": entity_label(unit), "unit_id": unit["object_id"],
                            "accepted": False, "rearming": True,
                        })
                        continue
                    result = await self._submit_unit_command(
                        state,
                        int(unit["object_id"]),
                        "auto_attack",
                    )
                    row = {
                        "unit": entity_label(unit),
                        "unit_id": unit["object_id"],
                        **result,
                    }
                    if result.get("accepted"):
                        accepted.append(row)
                    else:
                        failed.append(row)
            elif targets:
                target = self._focus_attack_target(units, targets)
                target_load = {int(item["object_id"]): 0 for item in targets}
                for unit in units:
                    if not self._aircraft_ready_for_attack(unit):
                        failed.append({
                            "unit": entity_label(unit), "unit_id": unit["object_id"],
                            "target": entity_label(target), "target_id": target["object_id"],
                            "accepted": False, "rearming": True,
                        })
                        continue
                    if control == "spread":
                        target = min(
                            targets,
                            key=lambda item: (
                                target_load[int(item["object_id"])],
                                self._distance_sq(unit, item),
                                int(item["object_id"]),
                            ),
                        )
                    result = await self._submit_unit_command(
                        state,
                        int(unit["object_id"]),
                        "attack",
                        target_object_id=int(target["object_id"]),
                    )
                    row = {
                        "unit": entity_label(unit),
                        "unit_id": unit["object_id"],
                        "target": entity_label(target),
                        "target_id": target["object_id"],
                        **result,
                    }
                    if result.get("accepted"):
                        accepted.append(row)
                        assignments[int(unit["object_id"])] = int(target["object_id"])
                        target_load[int(target["object_id"])] += 1
                    else:
                        failed.append(row)
            if continuous is True:
                duration = self._attack_duration(duration_seconds)
                task_id = uuid.uuid4().hex[:12]
                now = time.monotonic()
                self._attack_tasks[task_id] = {
                    "task_id": task_id,
                    "query": str(query or "").strip(),
                    "target_query": target_text,
                    "mode": "auto_attack" if native_continuous_attack else "target",
                    "fire_control": control,
                    "unit_ids": [int(unit["object_id"]) for unit in units],
                    "assignments": assignments,
                    "last_order_at": {
                        int(unit_id): now for unit_id in assignments
                    },
                    "created_at": now,
                    "expires_at": now + duration,
                    "duration_seconds": duration,
                    "orders_issued": len(accepted),
                    "failed_orders": len(failed),
                    "status": "engaging" if accepted else "waiting_for_enemy",
                }
                return {
                    "summary": (
                        f"已启动持续清剿任务并下达 {len(accepted)} 个攻击命令；后台将自动接敌"
                        if accepted else "已启动持续清剿任务；当前没有可见敌军，部队将待命并自动接敌"
                    ),
                    "accepted": True,
                    "continuous": True,
                    "task_id": task_id,
                    "duration_seconds": duration,
                    "accepted_count": len(accepted),
                    "failed_count": len(failed),
                    "orders": accepted,
                    "failures": failed,
                }
            return {
                "summary": f"游戏已接受 {len(accepted)} 个攻击命令，{len(failed)} 个失败",
                "accepted": bool(accepted),
                "accepted_count": len(accepted),
                "failed_count": len(failed),
                "orders": accepted,
                "failures": failed,
            }
        except (BridgeError, PolicyError, TypeError, ValueError) as exc:
            return {"summary": f"攻击命令失败：{exc}", "accepted": False}

    @llm_tool(
        name="mental_omega_attack_task_status",
        description=(
            "List, inspect, or cancel persistent combat-clearing tasks created by "
            "mental_omega_attack_units with continuous=true. Use cancel=true only when "
            "the user asks those units to stop their ongoing clearing duty."
        ),
        parameters={
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "cancel": {"type": "boolean"},
            },
        },
        timeout=5.0,
    )
    async def mental_omega_attack_task_status(
        self, task_id: Any = None, cancel: Any = False, **_
    ):
        selected = str(task_id or "").strip()
        if cancel is True:
            return await self._cancel_attack_tasks(selected)
        tasks = [self._public_attack_task(item) for item in self._attack_tasks.values()]
        if selected:
            tasks = [item for item in tasks if item["task_id"] == selected]
        return {
            "summary": f"当前有 {len(tasks)} 个持续清剿任务",
            "tasks": tasks,
        }

    @llm_tool(
        name="mental_omega_adaptive_task_status",
        description=(
            "Inspect or cancel persistent move-near and shroud-exploration goals. "
            "These goals keep checking real unit progress after the first accepted order, "
            "retry stalled units, reacquire moving destinations, and remain active without "
            "requiring the user to repeat the command. Call with cancel=true only when the "
            "user asks to stop that delegated movement or exploration duty."
        ),
        parameters={
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "cancel": {"type": "boolean"},
                "include_finished": {"type": "boolean"},
            },
        },
        timeout=5.0,
    )
    async def mental_omega_adaptive_task_status(
        self,
        task_id: Any = None,
        cancel: Any = False,
        include_finished: Any = False,
        **_,
    ):
        selected = str(task_id or "").strip() or None
        if cancel is True:
            return await self._cancel_background_tasks(
                task_id=selected, task_type="adaptive", all_matching=selected is None
            )
        tasks = self._adaptive_tasks.tasks(
            include_finished=include_finished is True,
            task_id=selected,
        )
        return {
            "summary": f"当前查询到 {len(tasks)} 个自适应任务",
            "tasks": tasks,
            "learning": self._adaptive_tasks.learning.summary(limit=5),
        }

    @llm_tool(
        name="mental_omega_cancel_task",
        description=(
            "Cancel the corresponding background task regardless of its internal kind. "
            "Use this for natural requests such as cancel that task, stop the latest "
            "exploration, stop moving those units, or cancel a production-and-loading plan. "
            "The task and linked parent/child tasks are cancelled before native Stop orders "
            "are sent, preventing a scheduler from taking the units back on the next tick."
        ),
        parameters={
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "task_type": {
                    "type": "string",
                    "enum": ["any", "plan", "attack", "load", "production", "move", "explore", "adaptive", "base", "force", "defense"],
                },
                "description": {"type": "string"},
                "latest": {"type": "boolean"},
                "all_matching": {"type": "boolean"},
            },
        },
        timeout=8.0,
    )
    async def mental_omega_cancel_task(
        self, task_id: Any = None, task_type: Any = "any",
        description: Any = None, latest: Any = True,
        all_matching: Any = False, **_,
    ):
        return await self._cancel_background_tasks(
            task_id=str(task_id or "").strip() or None,
            task_type=str(task_type or "any").strip().lower(),
            description=str(description or "").strip(),
            latest=latest is not False,
            all_matching=all_matching is True,
        )

    @llm_tool(
        name="mental_omega_repair_units",
        description=(
            "Atomic adaptive repair dispatcher for repair vehicles, repair aircraft, medics, "
            "or engineer-loaded support transports. It finds damaged owned targets, locks and "
            "refreshes moving targets, approaches them when needed, and then repairs/heals. "
            "Always use this single tool for requests such as 维修IFV维修受损单位; never chain "
            "detection, coordinate lookup, movement and repair tools, and never narrate retries."
        ),
        parameters={
            "type": "object",
            "properties": {
                "source_query": {"type": "string"},
                "target_query": {"type": "string"},
                "max_targets": {"type": "integer", "minimum": 1, "maximum": 32},
            },
            "required": ["source_query"],
        },
        timeout=15.0,
    )
    async def mental_omega_repair_units(
        self,
        source_query: Any = None,
        target_query: Any = None,
        max_targets: Any = None,
        **_,
    ):
        try:
            await self._ensure_control_for_order()
            state = await asyncio.to_thread(self._client.state)
            source_text = str(source_query or "")
            sources = self._select_repair_sources(state, source_text)
            if "工程师" in source_text and any(word in source_text for word in ("装载", "载有", "带着")):
                sources = [item for item in sources if item.get("engineer_passenger_count", 0) > 0]
            targets = select_owned_units(
                state,
                str(target_query).strip() if target_query else None,
                damaged_only=True,
                health_below=1.0,
                require_controllable=False,
            )
            if not sources:
                raise PolicyError(f"没有找到符合“{source_text}”的己方可控维修单位")
            if not targets:
                raise PolicyError("没有找到符合条件的受损己方单位")
            if max_targets is not None:
                target_limit = max(1, min(int(max_targets), 32))
                targets = targets[:target_limit]
            # Give every repairer a queue. Extra damaged targets are processed by
            # the least-loaded repairer after its current target is confirmed.
            queues: dict[int, list[dict[str, Any]]] = {
                int(source["object_id"]): [] for source in sources
            }
            source_by_id = {int(source["object_id"]): source for source in sources}
            for target in sorted(
                targets,
                key=lambda item: (
                    float(item.get("health_ratio", 1.0)),
                    int(item.get("object_id", 0)),
                ),
            ):
                available = min(
                    sources,
                    key=lambda source: (
                        len(queues[int(source["object_id"])]),
                        self._distance_sq(source, target),
                        int(source["object_id"]),
                    ),
                )
                queues[int(available["object_id"])].append(target)
            results = await asyncio.gather(*(
                self._repair_sequence(
                    source_by_id[source_id], queue, slot
                )
                for slot, (source_id, queue) in enumerate(queues.items())
                if queue
            ))
            results = [item for batch in results for item in batch]
            accepted = [item for item in results if item.get("accepted")]
            failures = [item for item in results if not item.get("accepted")]
            verified = [item for item in accepted if item.get("verified")]
            if failures:
                summary = (
                    f"{len(accepted)} 台维修单位已前往受损目标并执行维修，"
                    f"{len(failures)} 台未能执行"
                )
            else:
                summary = f"{len(accepted)} 台维修单位已前往受损目标并执行维修"
            return {
                "summary": summary,
                "accepted": bool(accepted),
                "accepted_count": len(accepted),
                "verified_count": len(verified),
                "orders": accepted,
                "failures": failures[-12:],
            }
        except (BridgeError, PolicyError, TypeError, ValueError) as exc:
            return {"summary": f"维修命令失败：{exc}", "accepted": False}

    @llm_tool(
        name="mental_omega_capture_tech_buildings",
        description=(
            "Command real on-map owned engineers to capture visible non-owned tech "
            "buildings such as oil derricks, including buildings captured by an enemy. "
            "The native bridge uses the game's real engineer Capture action as the final "
            "eligibility check. Use all_matches=true when the user says all."
        ),
        parameters={
            "type": "object",
            "properties": {
                "building_query": {"type": "string"},
                "all_matches": {"type": "boolean"},
            },
            "required": ["building_query"],
        },
        timeout=15.0,
    )
    async def mental_omega_capture_tech_buildings(
        self, building_query: Any = None, all_matches: Any = False, **_
    ):
        try:
            await self._ensure_control_for_order()
            state = await asyncio.to_thread(self._client.state)
            targets = self._matching_tech_landmarks(state, str(building_query or ""))
            desired_count = len(targets) if all_matches is True else 1
            engineers = [
                item for item in select_owned_units(state)
                if item.get("is_engineer") is True
            ]
            if not targets:
                raise PolicyError("当前视野内没有找到非己方且符合游戏占领条件的科技建筑")
            if not engineers:
                transports = [
                    item for item in select_owned_units(state)
                    if item.get("engineer_passenger_count", 0) > 0
                ]
                if transports:
                    return {
                        "summary": "工程师仍在运输载具内；需要先移动并卸载，当前尚未执行占领",
                        "accepted": False,
                        "requires_unload": True,
                        "transports": transports,
                        "targets": targets,
                    }
                raise PolicyError("没有找到在地图上的己方可控工程师")
            accepted, failed, used = [], [], set()
            for target in targets:
                if len(accepted) >= desired_count:
                    break
                available = [item for item in engineers if item["object_id"] not in used]
                if not available:
                    break
                engineer = min(available, key=lambda item: self._distance_sq(item, target))
                result = await self._submit_unit_command(
                    state,
                    int(engineer["object_id"]),
                    "capture",
                    target_object_id=int(target["object_id"]),
                )
                if result.get("accepted"):
                    used.add(engineer["object_id"])
                    accepted.append({
                        "engineer": entity_label(engineer),
                        "building": entity_label(target),
                    })
                else:
                    failed.append({"building": entity_label(target), **result})
            return {
                "summary": f"已确认 {len(accepted)} 个工程师占领命令，{len(failed)} 个失败",
                "accepted": bool(accepted),
                "accepted_count": len(accepted),
                "orders": accepted,
                "failures": failed,
            }
        except (BridgeError, PolicyError, TypeError, ValueError) as exc:
            return {"summary": f"占领命令失败：{exc}", "accepted": False}

    @llm_tool(
        name="mental_omega_deploy_units",
        description=(
            "Deploy owned deploy-capable units such as GGI through the native game action. "
            "Use this for 部署/架设 requests. This is not passenger unloading. Call once and "
            "return the compact tool summary without narrating intermediate checks. NEVER use this "
            "as a substitute for a full-control, win-the-match, autonomous base-development request; "
            "those requests MUST use mental_omega_autonomous_victory."
        ),
        parameters={
            "type": "object",
            "properties": {
                "group_number": {"type": "integer", "minimum": 0, "maximum": 9},
                "query": {"type": "string"},
            },
        },
        timeout=12.0,
    )
    async def mental_omega_deploy_units(
        self, group_number: Any = None, query: Any = None, **_
    ):
        try:
            await self._ensure_control_for_order()
            state = await asyncio.to_thread(self._client.state)
            units = self._resolve_command_units(state, group_number, query, False)
            results = await asyncio.gather(*(
                self._submit_unit_command(
                    state, int(unit["object_id"]), "deploy"
                )
                for unit in units
            ))
            accepted = [
                {"unit": entity_label(unit), **result}
                for unit, result in zip(units, results)
                if result.get("accepted")
            ]
            failures = [
                {"unit": entity_label(unit), **result}
                for unit, result in zip(units, results)
                if not result.get("accepted")
            ]
            return {
                "summary": f"{len(accepted)} 个单位已执行部署，{len(failures)} 个无法部署",
                "accepted": bool(accepted),
                "accepted_count": len(accepted),
                "failed_count": len(failures),
                "orders": accepted,
                "failures": failures,
            }
        except (BridgeError, PolicyError, TypeError, ValueError) as exc:
            return {"summary": f"部署失败：{exc}", "accepted": False}

    @llm_tool(
        name="mental_omega_explore_units",
        description=(
            "Send owned mobile units to legally explore the nearest current shroud/fog boundary. "
            "The native game chooses destinations only from map visibility state and never reveals "
            "hidden enemy data. Use this for 探索黑幕/战争迷雾 requests; do not search for a "
            "landmark named 黑幕区域 and do not ask the user for coordinates."
        ),
        parameters={
            "type": "object",
            "properties": {
                "group_number": {"type": "integer", "minimum": 0, "maximum": 9},
                "query": {"type": "string"},
            },
        },
        timeout=15.0,
    )
    async def mental_omega_explore_units(
        self, group_number: Any = None, query: Any = None, **_
    ):
        try:
            await self._ensure_control_for_order()
            state = await asyncio.to_thread(self._client.state)
            units = self._resolve_command_units(state, group_number, query, False)
            task = self._adaptive_tasks.create_explore_task(
                units,
                query=str(query).strip() if query else None,
                duration_seconds=int(
                    self._cfg.get("adaptive_explore_task_seconds", 3600)
                ),
            )
            await asyncio.to_thread(self._adaptive_tasks.tick, state)
            current = self._adaptive_tasks.tasks(
                include_finished=True, task_id=task["task_id"]
            )[0]
            accepted_count = sum(
                int(item.get("accepted_orders", 0)) > 0
                for item in current["units"]
            )
            return {
                "summary": (
                    f"已建立持续探图任务，{len(units)} 个单位会持续寻找当前可达的"
                    "黑幕边界；暂时没有边界时会等待并再次尝试"
                ),
                "accepted": True,
                "continuous": True,
                "task_id": task["task_id"],
                "accepted_count": accepted_count,
                "verified_count": sum(
                    item.get("progress_events", 0) > 0 for item in current["units"]
                ),
                "failed_count": sum(
                    int(item.get("failed_orders", 0)) > 0
                    for item in current["units"]
                ),
                "task": current,
            }
        except (BridgeError, PolicyError, TypeError, ValueError) as exc:
            return {"summary": f"探索失败：{exc}", "accepted": False}

    @llm_tool(
        name="mental_omega_simple_unit_action",
        description=(
            "Execute stop, guard, unload, or paid building repair on a companion group or "
            "natural-language selection. Use repair_building when the user clicks the repair "
            "wrench on owned damaged buildings."
        ),
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["stop", "guard", "unload", "repair_building"]},
                "group_number": {"type": "integer", "minimum": 0, "maximum": 9},
                "query": {"type": "string"},
                "damaged_only": {"type": "boolean"},
            },
            "required": ["action"],
        },
        timeout=12.0,
    )
    async def mental_omega_simple_unit_action(
        self,
        action: Any = None,
        group_number: Any = None,
        query: Any = None,
        damaged_only: Any = False,
        **_,
    ):
        try:
            await self._ensure_control_for_order()
            state = await asyncio.to_thread(self._client.state)
            include_buildings = str(action) == "repair_building"
            units = self._resolve_command_units(
                state,
                group_number,
                query,
                damaged_only is True,
                include_buildings=include_buildings,
            )
            if str(action) == "stop":
                stopped_ids = {int(unit["object_id"]) for unit in units}
                for attack_task_id, task in list(self._attack_tasks.items()):
                    remaining = [
                        int(unit_id) for unit_id in task.get("unit_ids", [])
                        if int(unit_id) not in stopped_ids
                    ]
                    if remaining:
                        task["unit_ids"] = remaining
                        for unit_id in stopped_ids:
                            task.get("assignments", {}).pop(unit_id, None)
                            task.get("last_order_at", {}).pop(unit_id, None)
                    else:
                        self._attack_tasks.pop(attack_task_id, None)
            accepted, failed = [], []
            for unit in units:
                result = await self._submit_unit_command(
                    state, int(unit["object_id"]), str(action)
                )
                row = {"unit": entity_label(unit), "unit_id": unit["object_id"], **result}
                (accepted if result.get("accepted") else failed).append(row)
            return {
                "summary": f"已确认 {len(accepted)} 个{action}命令，{len(failed)} 个失败",
                "accepted": bool(accepted),
                "accepted_count": len(accepted),
                "orders": accepted,
                "failures": failed,
            }
        except (BridgeError, PolicyError, TypeError, ValueError) as exc:
            return {"summary": f"单位操作失败：{exc}", "accepted": False}

    @llm_tool(
        name="mental_omega_load_transport",
        description=(
            "Load owned passengers into owned transports with the native enter order. "
            "Use this for enter, board, or load requests. Never substitute unload or a "
            "one-cell move. The task retries interrupted orders and only completes after "
            "live passenger-count telemetry increases."
        ),
        parameters={
            "type": "object",
            "properties": {
                "passenger_query": {"type": "string"},
                "transport_query": {"type": "string"},
                "maximum_passengers": {"type": "integer", "minimum": 1},
            },
            "required": ["passenger_query", "transport_query"],
        },
        timeout=20.0,
    )
    async def mental_omega_load_transport(
        self,
        passenger_query: Any = None,
        transport_query: Any = None,
        maximum_passengers: Any = None,
        **_,
    ):
        try:
            await self._ensure_control_for_order()
            state = await asyncio.to_thread(self._client.state)
            passengers = select_owned_units(state, str(passenger_query or "").strip())
            transports = select_owned_units(state, str(transport_query or "").strip())
            transports = [
                item for item in transports
                if item.get("transport_capacity") is True
                and isinstance(item.get("object_id"), int)
            ]
            transport_ids = {int(item["object_id"]) for item in transports}
            passengers = [
                item for item in passengers
                if isinstance(item.get("object_id"), int)
                and int(item["object_id"]) not in transport_ids
                and item.get("kind") != "building"
            ]
            if maximum_passengers is not None:
                if (
                    isinstance(maximum_passengers, bool)
                    or not isinstance(maximum_passengers, int)
                    or maximum_passengers < 1
                ):
                    raise PolicyError("maximum_passengers must be a positive integer")
                passengers = passengers[:maximum_passengers]
            if not passengers:
                raise PolicyError("no controllable owned passengers matched the request")
            if not transports:
                raise PolicyError("no owned transport with passenger capacity was found")
            passenger_id_set = {int(item["object_id"]) for item in passengers}
            # Boarding is an exclusive intent. Remove these units from any
            # persistent attack task before issuing enter orders.
            for attack_task in self._attack_tasks.values():
                attack_task["unit_ids"] = [
                    unit_id for unit_id in attack_task.get("unit_ids", [])
                    if int(unit_id) not in passenger_id_set
                ]
                for unit_id in passenger_id_set:
                    attack_task.get("assignments", {}).pop(unit_id, None)
                    attack_task.get("last_order_at", {}).pop(unit_id, None)
            now = time.monotonic()
            task_id = uuid.uuid4().hex[:12]
            task = {
                "task_id": task_id,
                "passenger_query": str(passenger_query or "").strip(),
                "transport_query": str(transport_query or "").strip(),
                "passenger_ids": [int(item["object_id"]) for item in passengers],
                "transport_ids": [int(item["object_id"]) for item in transports],
                "baseline_counts": {
                    int(item["object_id"]): max(0, int(item.get("passenger_count", 0)))
                    for item in transports
                },
                "assignments": {},
                "last_order_at": {},
                "last_positions": {
                    int(item["object_id"]): dict(item.get("cell", {}))
                    for item in passengers
                    if isinstance(item.get("cell"), dict)
                },
                "approach_attempts": {},
                "orders_issued": 0,
                "failed_orders": 0,
                "loaded_count": 0,
                "status": "loading",
                "created_at": now,
                "expires_at": now + int(self._cfg.get("load_task_seconds", 21600)),
            }
            self._load_tasks[task_id] = task
            await asyncio.to_thread(self._run_load_task, task, state)
            return {
                "summary": (
                    f"created persistent loading task for {len(passengers)} passengers; "
                    f"accepted {task['orders_issued']} native enter orders"
                ),
                "accepted": task["orders_issued"] > 0,
                "continuous": True,
                "task_id": task_id,
                "accepted_count": task["orders_issued"],
                "passenger_count": len(passengers),
                "transport_count": len(transports),
                "task": self._public_load_task(task),
            }
        except (BridgeError, PolicyError, TypeError, ValueError) as exc:
            return {"summary": f"loading failed: {exc}", "accepted": False}

    @llm_tool(
        name="mental_omega_load_status",
        description="Inspect or cancel persistent passenger-loading tasks.",
        parameters={
            "type": "object",
            "properties": {"task_id": {"type": "string"}, "cancel": {"type": "boolean"}},
        },
        timeout=5.0,
    )
    async def mental_omega_load_status(self, task_id: Any = None, cancel: Any = False, **_):
        selected = str(task_id or "").strip()
        if cancel is True:
            return await self._cancel_background_tasks(
                task_id=selected or None, task_type="load", all_matching=not selected
            )
        tasks = [self._public_load_task(item) for item in self._load_tasks.values()]
        if selected:
            tasks = [item for item in tasks if item["task_id"] == selected]
        return {"summary": f"{len(tasks)} loading tasks", "tasks": tasks}

    @llm_tool(
        name="mental_omega_production_options",
        description=(
            "Read the live production items accepted by the game's native house build planner. "
            "Call this before answering what the player can currently build. The result "
            "comes from HouseClass::CanBuild and native factory discovery, not from the "
            "static unit catalog or the sidebar presentation cache."
        ),
        parameters={"type": "object", "properties": {}},
        timeout=5.0,
    )
    async def mental_omega_production_options(self, **_):
        try:
            await self._ensure_control_for_order()
            state = await asyncio.to_thread(self._client.state)
            production = state.get("production", {})
            raw_options = production.get("sidebar_available", [])
            if not isinstance(raw_options, list):
                raw_options = []
            options = []
            for item in raw_options:
                if not isinstance(item, dict):
                    continue
                type_id = str(item.get("type_id", "")).upper()
                profile = next(
                    (candidate for candidate in catalog_summary(type_id)
                     if str(candidate.get("type_id", "")).upper() == type_id),
                    None,
                )
                options.append(
                    {
                        "type_id": type_id,
                        "display_name": (profile or {}).get("display_name") or type_id,
                        "kind": item.get("kind", (profile or {}).get("kind", "unknown")),
                        "tab_index": int(item.get("tab_index", -1)),
                        "cameo_index": int(item.get("cameo_index", -1)),
                        "has_factory": item.get("has_factory") is True,
                    }
                )
            return {
                "summary": f"当前侧边栏发现 {len(options)} 个生产条目",
                "available": production.get("available") is True,
                "telemetry_version": production.get("telemetry_version"),
                "native_connected": production.get("native_connected", False),
                "sidebar_count": len(options),
                "queue_count": len(production.get("queue", []) or []),
                "reason": production.get("reason"),
                "options": options,
            }
        except (BridgeError, PolicyError, TypeError, ValueError) as exc:
            return {"summary": f"读取侧边栏生产列表失败：{exc}", "available": False, "options": []}

    @llm_tool(
        name="mental_omega_production_sequence",
        description=(
            "ALWAYS use this when the commander says 依次、然后、完成后、放下后再建, "
            "or otherwise gives an ordered building/unit production chain. It creates "
            "persistent dependency flags. For buildings, each next item waits until the "
            "previous building is visibly placed on the map; no reminder is required."
        ),
        parameters={
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "products": {
                    "type": "array",
                    "minItems": 1,
                    "items": {
                        "type": "object",
                        "properties": {
                            "product": {"type": "string"},
                            "type_id": {"type": "string"},
                            "quantity": {"type": "integer", "minimum": 1},
                        },
                    },
                },
            },
            "required": ["products"],
        },
        timeout=10.0,
    )
    async def mental_omega_production_sequence(
        self, products: Any = None, name: Any = None, **_
    ):
        try:
            if not isinstance(products, list) or not products:
                raise OrderPlanError("products 必须至少包含一个生产项目")
            orders = []
            previous = None
            for index, product in enumerate(products, 1):
                if not isinstance(product, dict):
                    raise OrderPlanError("每个生产项目必须是对象")
                order_id = f"production_{index}"
                order = {
                    "order_id": order_id,
                    "action": "produce",
                    "product": product.get("product"),
                    "type_id": product.get("type_id"),
                    "quantity": int(product.get("quantity", 1)),
                    "depends_on": [previous] if previous else [],
                    "on_failure": "abort",
                }
                orders.append(order)
                previous = order_id
            await self._ensure_control_for_order()
            plan = self._order_plans.create(
                str(name or "顺序生产计划"), orders, duration_seconds=None
            )
            state = await asyncio.to_thread(self._client.state)
            await asyncio.to_thread(self._order_plans.tick, state)
            current = self._order_plans.plans(plan["plan_id"], include_finished=True)[0]
            return {
                "summary": f"已建立 {len(orders)} 步顺序生产计划；建筑落地标志会自动唤醒下一步",
                "accepted": True,
                "continuous": True,
                "plan": current,
                "flags_prefix": f"plan:{plan['plan_id']}:",
            }
        except (BridgeError, PolicyError, OrderPlanError, TypeError, ValueError) as exc:
            return {"summary": f"顺序生产计划创建失败：{exc}", "accepted": False}

    @llm_tool(
        name="mental_omega_produce_units",
        description=(
            "Queue building, infantry, vehicle, or aircraft production by a natural Chinese or "
            "English unit name. It first matches the native build-planner result, then submits "
            "the game's native production event using the real type-array index. A background task verifies "
            "newly created owned unit IDs. Use this only for an independent production request; "
            "if another action must happen afterward, use mental_omega_submit_order_plan."
        ),
        parameters={
            "type": "object",
            "properties": {
                "product": {"type": "string"},
                "quantity": {"type": "integer", "minimum": 1},
            },
            "required": ["product", "quantity"],
        },
        timeout=15.0,
    )
    async def mental_omega_produce_units(self, product: Any = None, quantity: Any = None, **_):
        try:
            if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity < 1:
                raise PolicyError("quantity must be a positive integer")
            await self._ensure_control_for_order()
            health = await asyncio.to_thread(self._client.health)
            capabilities = health.get("capabilities", {})
            if not isinstance(capabilities, dict) or capabilities.get("production") is not True:
                raise PolicyError("the installed native bridge does not support production")
            profiles = [
                item for item in catalog_summary(str(product or "").strip())
                if item.get("kind") in {"building", "infantry", "vehicle", "aircraft"}
                and int(item.get("tech_level") or 0) >= 0
            ]
            if not profiles:
                raise PolicyError("no producible unit matched that name")
            state = await asyncio.to_thread(self._client.state)
            production = state.get("production", {})
            sidebar_options = production.get("sidebar_available", [])
            sidebar_by_type = {
                str(item.get("type_id", "")).upper(): item
                for item in sidebar_options
                if isinstance(item, dict) and item.get("type_id")
            } if isinstance(sidebar_options, list) else {}
            if production.get("available") is not True:
                raise PolicyError(
                    production.get("reason")
                    or "原生建造栏不可用，请确认已加载新版 MOBridge.dll"
                )
            if not sidebar_by_type:
                raise PolicyError("当前玩家建造栏为空，没有可生产项目")

            # The live sidebar is authoritative. The catalog only resolves a
            # natural-language alias to a type id; it can never add an item.
            live_profiles = [
                item for item in profiles
                if str(item.get("type_id", "")).upper() in sidebar_by_type
            ]
            if not live_profiles:
                requested = str(product or "").strip().upper()
                direct = sidebar_by_type.get(requested)
                if direct:
                    live_profiles = [{
                        "type_id": requested,
                        "kind": direct.get("kind", "unknown"),
                        "display_name": requested,
                    }]
            profiles = live_profiles
            if not profiles:
                visible_names = ", ".join(sidebar_by_type.keys())
                raise PolicyError(f"{product} 当前不在玩家建造栏中；当前项目：{visible_names}")
            profiles.sort(key=lambda item: str(item.get("type_id", "")))
            result = None
            profile = None
            failures = []
            for candidate in profiles:
                try:
                    submit_quantity = (
                        1 if candidate.get("kind") == "building" else min(50, quantity)
                    )
                    candidate_result = await asyncio.to_thread(
                        self._client.command_production,
                        {
                            "request_id": str(uuid.uuid4()),
                            "type_id": str(candidate["type_id"]),
                            "quantity": submit_quantity,
                        },
                    )
                except BridgeError as exc:
                    failures.append(str(exc))
                    continue
                if candidate_result.get("accepted") is True and int(candidate_result.get("accepted_count", 0)) > 0:
                    result, profile = candidate_result, candidate
                    break
                failures.append(
                    str(
                        candidate_result.get("last_error")
                        or candidate_result.get("error")
                        or "native sidebar production event rejected"
                    )
                )
            if result is None or profile is None:
                sidebar_names = [
                    str(item.get("type_id", "")).upper()
                    for item in sidebar_options
                    if isinstance(item, dict) and item.get("type_id")
                ] if isinstance(sidebar_options, list) else []
                detail = "; ".join(failures[:4]) or "native sidebar event was rejected"
                if sidebar_names:
                    detail += f"; live sidebar items: {', '.join(sidebar_names[:24])}"
                raise PolicyError(detail)
            type_id = str(profile["type_id"]).upper()
            accepted_count = int(result.get("accepted_count", 0))
            baseline_ids = {
                int(item["object_id"])
                for item in state.get("visible_entities", [])
                if isinstance(item, dict)
                and item.get("relation") == "self"
                and str(item.get("type_id", "")).upper() == type_id
                and isinstance(item.get("object_id"), int)
            }
            now = time.monotonic()
            task_id = uuid.uuid4().hex[:12]
            task = {
                "task_id": task_id,
                "product": str(product or "").strip(),
                "display_name": profile.get("display_name") or type_id,
                "type_id": type_id,
                "kind": str(profile.get("kind") or "unknown"),
                "requested_count": quantity,
                "submitted_count": accepted_count,
                "baseline_ids": baseline_ids,
                "observed_ids": set(),
                "queue_count": 0,
                "queue_progress_ratio": 0.0,
                "queue_on_hold": False,
                "last_submit_at": now,
                "submit_attempts": 1,
                "status": "queued",
                "created_at": now,
                "expires_at": now + int(self._cfg.get("production_task_seconds", 21600)),
            }
            self._production_tasks[task_id] = task
            self._command_runtime.flags.set(
                f"production:{task_id}:started", True,
                source="production_task", priority=40,
            )
            self._command_runtime.flags.set(
                f"production:{task_id}:awaiting_placement", False,
                source="production_task", priority=40,
            )
            self._command_runtime.flags.set(
                f"production:{task_id}:completed", False,
                source="production_task", priority=40,
            )
            return {
                "summary": f"queued {accepted_count}/{quantity} {type_id}; production will be verified continuously",
                "accepted": True,
                "continuous": True,
                "task_id": task_id,
                "type_id": type_id,
                "requested_count": quantity,
                "accepted_count": accepted_count,
                "failed_count": max(0, quantity - accepted_count),
                "sidebar_item": sidebar_by_type.get(type_id),
                "task": self._public_production_task(task),
            }
        except (BridgeError, PolicyError, TypeError, ValueError) as exc:
            return {"summary": f"production failed: {exc}", "accepted": False}

    @llm_tool(
        name="mental_omega_production_status",
        description="Inspect or cancel persistent unit-production verification tasks.",
        parameters={
            "type": "object",
            "properties": {"task_id": {"type": "string"}, "cancel": {"type": "boolean"}},
        },
        timeout=5.0,
    )
    async def mental_omega_production_status(self, task_id: Any = None, cancel: Any = False, **_):
        selected = str(task_id or "").strip()
        if cancel is True:
            return await self._cancel_background_tasks(
                task_id=selected or None, task_type="production", all_matching=not selected
            )
        tasks = [self._public_production_task(item) for item in self._production_tasks.values()]
        if selected:
            tasks = [item for item in tasks if item["task_id"] == selected]
        return {"summary": f"{len(tasks)} production tasks", "tasks": tasks}

    @llm_tool(
        name="mental_omega_watch_units",
        description=(
            "START a persistent live battlefield watch for a named enemy unit. Use it "
            "whenever the user asks to monitor, watch, warn when approaching, or keep an "
            "eye on a unit. Natural aliases are resolved automatically. This task remains "
            "active in the background until its timeout or explicit cancellation; NEVER "
            "announce that monitoring is finished immediately after calling this tool."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "duration_seconds": {
                    "type": "integer",
                    "minimum": 30,
                    "maximum": 21600,
                },
            },
            "required": ["query"],
        },
        timeout=5.0,
    )
    async def mental_omega_watch_units(
        self, query: Any = None, duration_seconds: Any = None, **_
    ):
        query = str(query or "").strip()
        matches = catalog_summary(query)
        if not matches:
            return {
                "summary": f"无法识别要监控的单位“{query}”",
                "watching": False,
            }
        try:
            duration = self._watch_duration(duration_seconds)
            state = await asyncio.to_thread(self._client.state)
        except (BridgeError, TypeError, ValueError) as exc:
            return {
                "summary": f"已识别目标，但实时战场数据尚不可用：{exc}",
                "watching": False,
                "resolved_unit": matches[0],
            }
        status = observe_unit_watch(
            state,
            query,
            near_distance=self._watch_near_distance(),
            critical_distance=self._watch_critical_distance(),
        )
        watch_id = uuid.uuid4().hex[:12]
        now = time.monotonic()
        self._watches[watch_id] = {
            "watch_id": watch_id,
            "query": query,
            "resolved_unit": matches[0],
            "created_at": now,
            "expires_at": now + duration,
            "duration_seconds": duration,
            "status": status,
        }
        return {
            "summary": (
                f"已开始持续监控{matches[0].get('display_name') or query}；"
                f"当前状态：{watch_report_text(status)}。监控仍在后台运行"
            ),
            "watching": True,
            "watch_id": watch_id,
            "duration_seconds": duration,
            "current_status": status,
            "fair_play": "目标离开玩家视野后只会报告失去视野，不会读取黑幕信息",
        }

    @llm_tool(
        name="mental_omega_watch_status",
        description=(
            "List current persistent unit watches, inspect one watch, or cancel it. "
            "Use cancel=true only when the user asks to stop that monitoring task."
        ),
        parameters={
            "type": "object",
            "properties": {
                "watch_id": {"type": "string"},
                "cancel": {"type": "boolean"},
            },
        },
        timeout=5.0,
    )
    async def mental_omega_watch_status(
        self, watch_id: Any = None, cancel: Any = False, **_
    ):
        selected = str(watch_id or "").strip()
        if cancel is True:
            if not selected:
                count = len(self._watches) + len(self._alert_watches)
                self._watches.clear()
                self._alert_watches.clear()
                self._report_policy.configure(enabled=False)
                self._under_attack_reports_enabled = False
                return {
                    "summary": "已取消全部监控并关闭主动战场汇报",
                    "cancelled": True,
                    "cancelled_count": count,
                    "reports_enabled": False,
                }
            watch = self._watches.pop(selected, None)
            alert_watch = self._alert_watches.pop(selected, None)
            if watch is None and alert_watch is None:
                return {"summary": "没有找到要取消的监控任务", "cancelled": False}
            target = watch.get("query") if watch else alert_watch["anchor"]["label"]
            return {
                "summary": f"已取消对{target}的持续监控",
                "cancelled": True,
                "watch_id": selected,
            }
        watches = [self._public_watch(item) for item in self._watches.values()]
        watches.extend(
            self._public_alert_watch(item) for item in self._alert_watches.values()
        )
        if selected:
            watches = [item for item in watches if item["watch_id"] == selected]
        return {
            "summary": f"当前有 {len(watches)} 个持续监控任务",
            "watches": watches,
        }

    @llm_tool(
        name="mental_omega_watch_alerts",
        description=(
            "START a persistent watch for damage or loss of one exact owned object, or for "
            "events within a radius around an owned object, base area, or player B marker. "
            "Use object_id when the commander selected a specific building/unit. Omit radius "
            "for exact-object monitoring; provide radius_cells for nearby-event monitoring. "
            "label assigns names such as 1号短剑、前线高科 or 谭雅. "
            "The watch stays active until timeout or cancellation; never merely promise "
            "to watch without calling this tool and never announce immediate completion."
        ),
        parameters={
            "type": "object",
            "properties": {
                "target": {"type": "string"},
                "object_id": {"type": "integer", "minimum": 1},
                "label": {"type": "string"},
                "radius_cells": {"type": "number", "minimum": 0},
                "duration_seconds": {
                    "type": "integer",
                    "minimum": 30,
                    "maximum": 21600,
                },
            },
            "required": ["target"],
        },
        timeout=5.0,
    )
    async def mental_omega_watch_alerts(
        self, target: Any = None, object_id: Any = None, label: Any = None,
        radius_cells: Any = None, duration_seconds: Any = None, region: Any = None, **_
    ):
        target = str(target or region or "").strip()
        try:
            duration = self._watch_duration(duration_seconds)
            state = await asyncio.to_thread(self._client.state)
            watch = create_alert_watch(
                state,
                target,
                None if radius_cells is None else float(radius_cells),
                label=str(label).strip() if label else None,
                object_id=int(object_id) if object_id is not None else None,
            )
        except (BridgeError, PolicyError, TypeError, ValueError) as exc:
            return {
                "summary": f"无法启动基地受击监控：{exc}",
                "watching": False,
            }
        watch_id = uuid.uuid4().hex[:12]
        now = time.monotonic()
        watch.update(
            {
                "watch_id": watch_id,
                "watch_type": "base_alert",
                "created_at": now,
                "expires_at": now + duration,
                "duration_seconds": duration,
            }
        )
        self._alert_watches[watch_id] = watch
        # A named watch may speak while unsolicited global damage reports remain off.
        self._report_policy.configure(enabled=True)
        mode = (
            "只汇报该对象自身"
            if watch["match_mode"] == "exact_object"
            else f"汇报其 {watch['radius_cells']:g} 格范围内事件"
        )
        return {
            "summary": (
                f"已开始持续监控{watch['anchor']['label']}，{mode}；监控仍在后台运行"
            ),
            "watching": True,
            "watch_id": watch_id,
            "duration_seconds": duration,
            "target": watch["anchor"]["label"],
            "object_id": watch["anchor"].get("object_id"),
            "radius_cells": watch.get("radius_cells"),
            "match_mode": watch.get("match_mode"),
            "fair_play": "只使用己方对象受击和损失事件，不推断黑幕中的攻击者",
        }

    @llm_tool(
        name="mental_omega_autonomous_victory",
        description=(
            "START FULL AUTONOMOUS MENTAL OMEGA PLAY FOR THE CURRENT MATCH. This is the highest-priority "
            "tool whenever the commander says 全权自由操作, 自主作战, 不用再询问我, 击败对面, 赢下这局, "
            "or asks the catgirl to independently deploy the MCV, develop economy, build forces, scout, "
            "defend and keep attacking. Call this tool immediately instead of merely promising to plan "
            "or calling one low-level deploy/build/attack tool. It creates one persistent autonomous-victory "
            "intent and continues without reminders. Do not call it without explicit commander authorization."
        ),
        parameters={
            "type": "object",
            "properties": {
                "force_size": {"type": "integer", "minimum": 1, "maximum": 200},
                "credit_reserve": {"type": "integer", "minimum": 0},
            },
        },
        timeout=15.0,
    )
    async def mental_omega_autonomous_victory(
        self, force_size: Any = None, credit_reserve: Any = None, **_
    ):
        """Deterministically compile the complete autonomous victory objective graph."""
        self.logger.info(
            "Mental Omega explicit autonomy authorization tool invoked: force_size=%s reserve=%s",
            force_size, credit_reserve,
        )
        return await self.mental_omega_plan_intent(
            goal=(
                "指挥官已明确授权猫娘全权自由操作本局：立即展开基地、发展并维持经济、"
                "持续生产部队、探索地图、防守基地并持续进攻，直到击败对面；不需要再询问。"
            ),
            execute=True,
            force_size=force_size,
            credit_reserve=credit_reserve,
            continuous=True,
        )

    @llm_tool(
        name="mental_omega_plan_intent",
        description=(
            "Turn one commander goal into an explainable, persistent Mental Omega intent plan. "
            "ALWAYS use this for strategic sentences that combine base development, economy, "
            "force production and combat, or for produce-N-then-attack requests. The planner "
            "reads the world model, records assumptions and dependencies, starts base development "
            "when requested, waits for native production unlocks, and submits verified lower-level "
            "orders. Set execute=false to inspect the plan without controlling the game."
        ),
        parameters={
            "type": "object",
            "properties": {
                "goal": {"type": "string"},
                "execute": {"type": "boolean"},
                "product": {"type": "string"},
                "quantity": {"type": "integer", "minimum": 1},
                "target_query": {"type": "string"},
                "group_number": {"type": "integer", "minimum": 0, "maximum": 9},
                "force_size": {"type": "integer", "minimum": 1, "maximum": 200},
                "credit_reserve": {"type": "integer", "minimum": 0},
                "continuous": {"type": "boolean"},
                "objectives": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "objective_id": {"type": "string"},
                            "kind": {
                                "type": "string",
                                "enum": [
                                    "develop_base", "deploy_mcv", "produce_force", "attack", "explore", "defend",
                                    "move", "load", "capture", "repair", "stop", "guard", "wait",
                                ],
                            },
                            "params": {"type": "object"},
                            "depends_on": {"type": "array", "items": {"type": "string"}},
                        },
                        "required": ["kind"],
                    },
                },
            },
            "required": ["goal"],
        },
        timeout=15.0,
    )
    async def mental_omega_plan_intent(
        self,
        goal: Any = None,
        execute: Any = True,
        product: Any = None,
        quantity: Any = None,
        target_query: Any = None,
        group_number: Any = None,
        force_size: Any = None,
        credit_reserve: Any = None,
        continuous: Any = True,
        objectives: Any = None,
        **_,
    ):
        try:
            if self._cfg.get("intent_planner_enabled", True) is not True:
                raise PolicyError("intent planner is disabled in plugin configuration")
            state = await asyncio.to_thread(self._client.state)
            await asyncio.to_thread(self._world_model.update, state)
            plan = self._intent_plans.create(
                str(goal or ""),
                self._world_model.summary(),
                product=(str(product).strip() if product is not None else None),
                quantity=(int(quantity) if quantity is not None else None),
                target_query=(str(target_query).strip() if target_query is not None else None),
                group_number=(int(group_number) if group_number is not None else None),
                force_size=(
                    int(force_size)
                    if force_size is not None
                    else int(self._cfg.get("intent_default_force_size", 12))
                ),
                credit_reserve=(
                    None if credit_reserve is None else max(0, int(credit_reserve))
                ),
                continuous=continuous is not False,
                objectives=objectives if objectives is not None else None,
            )
            intent_id = str(plan["intent_id"])
            self.logger.info(
                "Mental Omega intent created: id=%s kind=%s autonomy_granted=%s objectives=%s",
                intent_id, plan.get("intent_kind"), plan.get("autonomy_granted"),
                [item.get("kind") for item in plan.get("objectives", [])],
            )
            if execute is False:
                return {
                    "summary": "intent plan compiled without game control",
                    "accepted": True,
                    "executing": False,
                    "task_id": intent_id,
                    "plan": self._intent_plans.get(intent_id),
                }
            await self._ensure_control_for_order()
            for objective in plan["objectives"]:
                if objective.get("kind") != "develop_base":
                    continue
                raw_reserve = objective.get("params", {}).get("credit_reserve")
                reserve = None if raw_reserve is None else max(0, int(raw_reserve))
                result = await self.mental_omega_develop_base(credit_reserve=reserve)
                if result.get("accepted") is not True:
                    self._intent_plans.set_objective(
                        intent_id, str(objective["objective_id"]), "failed",
                        result=result, error=str(result.get("summary") or "base development rejected"),
                    )
                    return {
                        "summary": "intent planning succeeded but base development could not start",
                        "accepted": False,
                        "intent_id": intent_id,
                        "plan": self._intent_plans.get(intent_id),
                    }
                self._intent_plans.set_objective(
                    intent_id, str(objective["objective_id"]), "succeeded", result=result,
                )
                self._intent_plans.attach_executor(
                    intent_id, base_task_id=result.get("task_id")
                )
            await asyncio.to_thread(self._tick_intent_plans, state, intent_id)
            return {
                "summary": "intent accepted; objectives will continue without further reminders",
                "accepted": True,
                "executing": True,
                "task_id": intent_id,
                "intent_id": intent_id,
                "plan": self._intent_plans.get(intent_id),
            }
        except (BridgeError, PolicyError, IntentPlanError, OrderPlanError, TypeError, ValueError) as exc:
            self.logger.warning("Mental Omega intent creation rejected: %s", exc)
            return {"summary": f"intent plan rejected: {exc}", "accepted": False}

    @llm_tool(
        name="mental_omega_intent_status",
        description=(
            "Inspect or cancel persistent high-level intent plans, their objective dependencies, "
            "world-model revision, selected force and attached executor tasks."
        ),
        parameters={
            "type": "object",
            "properties": {
                "intent_id": {"type": "string"},
                "include_finished": {"type": "boolean"},
                "cancel": {"type": "boolean"},
            },
        },
        timeout=5.0,
    )
    async def mental_omega_intent_status(
        self, intent_id: Any = None, include_finished: Any = False,
        cancel: Any = False, **_,
    ):
        selected = str(intent_id or "").strip() or None
        cancelled = self._intent_plans.cancel(selected) if cancel is True else 0
        plans = self._intent_plans.plans(include_finished=include_finished is True)
        if selected:
            plans = [plan for plan in plans if plan.get("intent_id") == selected]
        return {
            "summary": f"{len(plans)} intent plan(s); cancelled={cancelled}",
            "cancelled": cancelled,
            "plans": plans,
        }

    @llm_tool(
        name="mental_omega_world_model",
        description=(
            "Read the persistent Mental Omega battlefield world model. Use this before "
            "making strategic decisions, answering questions about control groups, economy, "
            "production, current threats, or remembered enemy positions. Live observations "
            "have confidence=1; remembered entities are explicitly marked observed=false and "
            "must never be treated as currently targetable."
        ),
        parameters={
            "type": "object",
            "properties": {
                "relation": {
                    "type": "string",
                    "enum": ["self", "ally", "enemy", "neutral", "unknown"],
                },
                "kind": {"type": "string"},
                "group_number": {"type": "integer", "minimum": 0, "maximum": 9},
                "visible_only": {"type": "boolean"},
                "entity_limit": {"type": "integer", "minimum": 1, "maximum": 512},
                "event_limit": {"type": "integer", "minimum": 0, "maximum": 256},
            },
        },
        timeout=5.0,
    )
    async def mental_omega_world_model(
        self,
        relation: Any = None,
        kind: Any = None,
        group_number: Any = None,
        visible_only: Any = False,
        entity_limit: Any = 120,
        event_limit: Any = 30,
        **_,
    ):
        try:
            state = await asyncio.to_thread(self._client.state)
            await asyncio.to_thread(self._world_model.update, state)
            return self._world_model.view(
                relation=str(relation) if relation is not None else None,
                kind=str(kind) if kind is not None else None,
                group_number=(int(group_number) if group_number is not None else None),
                visible_only=visible_only is True,
                entity_limit=int(entity_limit),
                event_limit=int(event_limit),
            )
        except (BridgeError, TypeError, ValueError) as exc:
            cached = self._world_model.view(entity_limit=120, event_limit=30)
            cached.update({"summary": str(exc), "connected": False, "cached": True})
            return cached

    @llm_tool(
        name="mental_omega_status",
        description=(
            "Read the current Mental Omega bridge and match status. This is "
            "observation-only and never controls the game."
        ),
        parameters={"type": "object", "properties": {}},
        timeout=5.0,
    )
    async def mental_omega_status(self, **_):
        try:
            health, state = await asyncio.gather(
                asyncio.to_thread(self._client.health),
                asyncio.to_thread(self._client.state),
            )
            await asyncio.to_thread(self._world_model.update, state)
            self._world_model.augment(state)
            game = state.get("game") if isinstance(state.get("game"), dict) else {}
            match_running = game.get("running") is True
            return {
                "summary": self._summarize(state),
                "match_running": match_running,
                "match_state": "running" if match_running else "not_running",
                "match_frame": game.get("frame"),
                "match_mode": game.get("mode"),
                "health": health,
                "state": state,
                "session_control_enabled": self._session_enabled,
                "companion_runtime": self._runtime_status(),
            }
        except BridgeError as exc:
            return {"summary": str(exc), "connected": False}

    @llm_tool(
        name="mental_omega_harvesters",
        description=(
            "List the player's currently observed Mental Omega harvesters. "
            "Use this before considering a harvester command."
        ),
        parameters={"type": "object", "properties": {}},
        timeout=5.0,
    )
    async def mental_omega_harvesters(self, **_):
        try:
            state = await asyncio.to_thread(self._client.state)
            return {
                "summary": self._summarize(state),
                "harvesters": state.get("harvesters", []),
                "snapshot_age_ms": state.get("snapshot_age_ms"),
            }
        except BridgeError as exc:
            return {"summary": str(exc), "harvesters": []}

    @llm_tool(
        name="mental_omega_harvester_command",
        description=(
            "EXECUTE one harvester order in Mental Omega. Call this whenever the "
            "user asks a harvester to move, mine, return, retreat, or stop. A direct "
            "user order authorizes automatic session control. Observation tools do "
            "not move units. Never claim an order was executed unless this tool "
            "returns accepted=true. mine/retreat require target cell x,y; "
            "return/stop must omit target_cell."
        ),
        parameters={
            "type": "object",
            "properties": {
                "unit_id": {"type": "integer"},
                "action": {
                    "type": "string",
                    "enum": ["mine", "return", "retreat", "stop", "move"],
                },
                "target_cell": {
                    "type": "object",
                    "properties": {
                        "x": {"type": "integer"},
                        "y": {"type": "integer"},
                    },
                    "required": ["x", "y"],
                },
            },
            "required": ["unit_id", "action"],
        },
        timeout=5.0,
    )
    async def mental_omega_harvester_command(
        self,
        unit_id: Any = None,
        action: Any = None,
        target_cell: Any = None,
        **_,
    ):
        try:
            await self._ensure_control_for_order()
            state = await asyncio.to_thread(self._client.state)
            command = self._policy().validate(
                state,
                unit_id=unit_id,
                action=action,
                target_cell=target_cell,
            )
            command["request_id"] = str(uuid.uuid4())
            result = await asyncio.to_thread(
                self._client.command_harvester,
                command,
            )
            return {"summary": "harvester command accepted", "result": result}
        except (BridgeError, PolicyError, TypeError, ValueError) as exc:
            return {"summary": f"command refused: {exc}", "accepted": False}

    @llm_tool(
        name="mental_omega_mine_nearest",
        description=(
            "AUTONOMOUSLY execute the user's natural-language request to send a "
            "harvester mining. This tool enables single-player session control, "
            "reads the live match, chooses an owned controllable harvester (prefers "
            "the least loaded), chooses its nearest visible ore cell, and submits "
            "the real game order. Call it for requests such as '安排一辆矿车采矿', "
            "'让矿车去最近矿区', or 'manage a harvester'. Never merely describe "
            "the chosen order without calling this tool."
        ),
        parameters={
            "type": "object",
            "properties": {
                "unit_id": {
                    "type": "integer",
                    "description": "Optional specific harvester ID; omit to choose automatically.",
                }
            },
        },
        timeout=5.0,
    )
    async def mental_omega_mine_nearest(self, unit_id: Any = None, **_):
        try:
            await self._ensure_control_for_order()
            state = await asyncio.to_thread(self._client.state)
            selected_id, target = select_nearest_mining_order(state, unit_id)
            command = self._policy().validate(
                state,
                unit_id=selected_id,
                action="mine",
                target_cell=target,
            )
            command["request_id"] = str(uuid.uuid4())
            result = await asyncio.to_thread(self._client.command_harvester, command)
            return {
                "summary": (
                    f"harvester {selected_id} was ordered to mine at "
                    f"({target['x']}, {target['y']})"
                ),
                "accepted": result.get("accepted") is True,
                "unit_id": selected_id,
                "target_cell": target,
                "result": result,
            }
        except (BridgeError, PolicyError, TypeError, ValueError) as exc:
            return {"summary": f"mining order refused: {exc}", "accepted": False}

    @llm_tool(
        name="mental_omega_manage_harvester_fleet",
        description=(
            "BATCH EXECUTE mining orders for multiple different Mental Omega "
            "harvesters in one call. Use this whenever the user says all, multiple, "
            "several, two or more harvesters should mine, including Chinese requests "
            "such as '让所有采矿车分别去采矿', '同时调动多辆矿车', or '超时空矿车和矿甲虫都去采矿'. "
            "The tool automatically enables single-player control, chooses distinct "
            "owned units and distinct visible ore cells, then submits and confirms "
            "every order. Never call the single-harvester tool repeatedly for a batch "
            "request, and never claim completion unless accepted_count is positive."
        ),
        parameters={
            "type": "object",
            "properties": {
                "count": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "Number of harvesters; omit to command all controllable harvesters.",
                },
                "unit_ids": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "minItems": 1,
                    "description": "Optional explicit harvester IDs; omit for automatic selection.",
                },
            },
        },
        timeout=10.0,
    )
    async def mental_omega_manage_harvester_fleet(
        self,
        count: Any = None,
        unit_ids: Any = None,
        **_,
    ):
        try:
            await self._ensure_control_for_order()
            state = await asyncio.to_thread(self._client.state)
            orders = plan_mining_orders(state, unit_ids=unit_ids, count=count)
            accepted = []
            failed = []
            for order in orders:
                try:
                    command = self._policy().validate(
                        state,
                        unit_id=order["unit_id"],
                        action="mine",
                        target_cell=order["target_cell"],
                    )
                    command["request_id"] = str(uuid.uuid4())
                    result = await asyncio.to_thread(
                        self._client.command_harvester, command
                    )
                    accepted.append({**order, "request_id": result.get("request_id")})
                except (BridgeError, PolicyError, TypeError, ValueError) as exc:
                    failed.append({**order, "error": str(exc)})
            return {
                "summary": (
                    f"accepted mining orders for {len(accepted)} harvesters; "
                    f"{len(failed)} failed"
                ),
                "accepted": bool(accepted) and not failed,
                "accepted_count": len(accepted),
                "failed_count": len(failed),
                "orders": accepted,
                "failures": failed,
            }
        except (BridgeError, PolicyError, TypeError, ValueError) as exc:
            return {"summary": f"fleet mining order refused: {exc}", "accepted": False}

    @llm_tool(
        name="mental_omega_move_harvesters",
        description=(
            "EXECUTE a natural-language order that moves one or more harvesters near "
            "a human-readable base landmark. Use this for requests such as '把采矿车移到基地附近', "
            "'让两辆矿车去最近的盟军基地', '所有矿车去最左边的EP基地'. The user never "
            "needs map coordinates. This tool resolves visible mobile construction vehicles first, "
            "falls back to construction yards, spreads multiple harvesters nearby, enables "
            "single-player control, and confirms every accepted order."
        ),
        parameters={
            "type": "object",
            "properties": {
                "destination": {
                    "type": "string",
                    "description": "Natural destination, such as 最近的基地、盟军基地、最左边的焚风基地.",
                },
                "count": {"type": "integer", "minimum": 1},
                "unit_ids": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "minItems": 1,
                },
            },
            "required": ["destination"],
        },
        timeout=10.0,
    )
    async def mental_omega_move_harvesters(
        self,
        destination: Any = None,
        count: Any = None,
        unit_ids: Any = None,
        **_,
    ):
        try:
            await self._ensure_control_for_order()
            state = await asyncio.to_thread(self._client.state)
            units = self._select_harvesters(state, unit_ids, count)
            origin = {
                "x": round(sum(unit["cell"]["x"] for unit in units) / len(units)),
                "y": round(sum(unit["cell"]["y"] for unit in units) / len(units)),
            }
            landmark = resolve_base(state, destination, origin)
            targets = nearby_cells(state, landmark["cell"], len(units))
            accepted, failed = [], []
            for unit, target in zip(units, targets):
                order = {"unit_id": unit["unit_id"], "target_cell": target}
                try:
                    command = self._policy().validate(
                        state, unit["unit_id"], "move", target
                    )
                    command["request_id"] = str(uuid.uuid4())
                    result = await asyncio.to_thread(
                        self._client.command_harvester, command
                    )
                    if result.get("accepted") is not True:
                        raise BridgeError("bridge did not confirm acceptance")
                    accepted.append(order)
                except (BridgeError, PolicyError, TypeError, ValueError) as exc:
                    failed.append({**order, "error": str(exc)})
            label = base_label(landmark)
            return {
                "summary": f"已接受 {len(accepted)} 辆采矿车前往{label}附近；{len(failed)} 辆失败",
                "accepted": bool(accepted) and not failed,
                "accepted_count": len(accepted),
                "failed_count": len(failed),
                "destination": label,
                "landmark": {
                    "object_id": landmark.get("object_id"),
                    "type_id": landmark.get("type_id"),
                    "kind": landmark.get("kind"),
                    "faction": landmark.get("faction"),
                },
                "orders": accepted,
                "failures": failed,
            }
        except (BridgeError, PolicyError, TypeError, ValueError) as exc:
            return {"summary": f"移动命令被拒绝：{exc}", "accepted": False}

    @llm_tool(
        name="mental_omega_landmarks",
        description=(
            "List all currently visible bases and tech buildings using player-readable names. "
            "Use when the user asks what tech buildings or faction bases are visible or asks "
            "for every visible oil derrick location. Optional query filters by natural name. "
            "This is observation-only and does not expose or require map coordinates."
        ),
        parameters={
            "type": "object",
            "properties": {"query": {"type": "string"}},
        },
        timeout=5.0,
    )
    async def mental_omega_landmarks(self, query: Any = None, **_):
        try:
            state = await asyncio.to_thread(self._client.state)
            landmarks = state.get("landmarks", [])
            bases = [
                {
                    "name": base_label(item),
                    "kind": item.get("kind"),
                    "faction": item.get("faction"),
                    "controllable": item.get("controllable") is True,
                }
                for item in landmarks
                if isinstance(item, dict)
                and item.get("kind") in {"mobile_base", "construction_yard"}
            ]
            tech_by_id = {
                item.get("object_id"): item for item in list_tech_buildings(state)
            }
            entity_relations = {
                item.get("object_id"): item.get("relation")
                for item in state.get("visible_entities", [])
                if isinstance(item, dict)
            }
            player_house_id = state.get("player", {}).get("house_id")
            matched_ids = None
            if query:
                matched_ids = {
                    item.get("object_id")
                    for item in match_visible_entities(state, str(query))
                }
            tech = []
            for item in landmarks:
                if not isinstance(item, dict) or item.get("kind") != "tech_building":
                    continue
                record = tech_by_id.get(item.get("object_id"), {})
                name = record.get("name") or entity_label(item)
                if query and item.get("object_id") not in matched_ids and str(query) not in str(name):
                    continue
                tech.append({
                    **record,
                    "name": name,
                    "location": describe_location(state, item.get("cell")),
                    "owner_relation": entity_relations.get(item.get("object_id")),
                    "available_to_capture": (
                        item.get("owner_house_id") != player_house_id
                        and entity_relations.get(item.get("object_id")) not in {"self", "ally"}
                    ),
                })
            return {
                "summary": f"当前可见 {len(bases)} 个基地目标和 {len(tech)} 座科技建筑",
                "bases": bases,
                "tech_buildings": tech,
            }
        except (BridgeError, PolicyError) as exc:
            return {"summary": f"读取地标失败：{exc}", "bases": [], "tech_buildings": []}

    @llm_tool(
        name="mental_omega_mission_objectives",
        description=(
            "Check whether the native bridge currently exposes campaign mission objectives. "
            "ALWAYS call this before answering the user's current primary objective. Never "
            "read objective text from screenshots or invent an objective."
        ),
        parameters={"type": "object", "properties": {}},
        timeout=5.0,
    )
    async def mental_omega_mission_objectives(self, **_):
        return {
            "summary": "当前原生桥尚未提供任务目标文本，无法可靠读取主要目标",
            "available": False,
            "objectives": [],
            "instruction": "明确告诉指挥官该数据接口尚未实现，不得根据画面或记忆猜测",
        }

    @llm_tool(
        name="mental_omega_situation",
        description=(
            "Read the fair-play adjutant situation report. ALWAYS call this when the user asks "
            "where they are under attack, what was damaged or lost, whether the home ore is "
            "depleted, or for recent battlefield alerts. It only reports current-player objects "
            "and visible ore data supplied by the native bridge. Locations are translated into "
            "human descriptions relative to owned bases; never invent unseen enemies or ore."
        ),
        parameters={"type": "object", "properties": {}},
        timeout=5.0,
    )
    async def mental_omega_situation(self, **_):
        try:
            state = await asyncio.to_thread(self._client.state)
            self._economy_tracker.observe(state)
            alerts = describe_alerts(self._report_context(state))
            economy = {
                **economy_report(state),
                "tracking": self._economy_tracker.report(),
            }
            attacks = [item for item in alerts if item["kind"] == "under_attack"]
            losses = [item for item in alerts if item["kind"] == "object_lost"]
            if attacks:
                summary = attacks[0]["summary"]
            elif losses:
                summary = losses[0]["summary"]
            elif alerts:
                summary = alerts[0]["summary"]
            else:
                summary = "最近30秒没有收到可定位的己方受击或损失事件"
            return {
                "summary": summary,
                "recent_alerts": alerts,
                "attack_points": attacks,
                "losses": losses,
                "economy": economy,
                "visible_forces": self._visible_force_summary(state),
                "fair_play": {
                    "own_objects_only": True,
                    "visible_ore_only": True,
                    "hidden_enemy_information": False,
                },
            }
        except (BridgeError, PolicyError) as exc:
            return {
                "summary": f"目前没有收到可用的战场监视数据：{exc}",
                "recent_alerts": [],
            }

    @llm_tool(
        name="mental_omega_economy",
        description=(
            "Read current credits, observed income/spending/net credit flow over time, "
            "and power output/drain/surplus. ALWAYS call this for questions about money "
            "growth, spending, income, low power, power generation, or power consumption. "
            "The flow is calculated from continuous balance samples and does not invent "
            "unavailable transaction sources."
        ),
        parameters={
            "type": "object",
            "properties": {
                "include_recent_events": {"type": "boolean"},
            },
        },
        timeout=5.0,
    )
    async def mental_omega_economy(
        self, include_recent_events: Any = False, **_
    ):
        try:
            state = await asyncio.to_thread(self._client.state)
            self._economy_tracker.observe(state)
            result = self._economy_tracker.report()
            result["home_ore"] = economy_report(state)
            if include_recent_events is True:
                result["recent_credit_events"] = self._economy_tracker.recent_events()
            return result
        except (BridgeError, PolicyError) as exc:
            return {
                "summary": f"目前没有收到可用的经济数据：{exc}",
                "available": False,
            }

    @llm_tool(
        name="mental_omega_emergency_stop",
        description=(
            "Immediately cancel queued companion commands and disable this "
            "session's game control. Safe to call whenever the user says stop."
        ),
        parameters={"type": "object", "properties": {}},
        timeout=5.0,
    )
    async def mental_omega_emergency_stop(self, **_):
        stopped = 0
        try:
            state = await asyncio.to_thread(self._client.state)
            for unit in select_owned_units(state):
                result = await self._submit_unit_command(
                    state, int(unit["object_id"]), "stop"
                )
                stopped += result.get("accepted") is True
        except (BridgeError, PolicyError, TypeError, ValueError):
            pass
        self._session_enabled = False
        self._attack_tasks.clear()
        self._load_tasks.clear()
        self._production_tasks.clear()
        self._base_tasks.clear()
        self._force_tasks.clear()
        self._defense_tasks.clear()
        self._adaptive_tasks.clear()
        try:
            result = await asyncio.to_thread(self._client.emergency_stop)
            return {
                "summary": f"companion control stopped; {stopped} units halted",
                "stopped_count": stopped,
                "result": result,
            }
        except BridgeError as exc:
            return {
                "summary": f"local control disabled; bridge stop failed: {exc}",
                "accepted": False,
            }

    @llm_tool(
        name="mental_omega_set_session_control",
        description=(
            "Enable or disable Mental Omega control for this session. Only enable "
            "after the user explicitly asks to allow game control. Disabling is "
            "always allowed. Single-player only."
        ),
        parameters={
            "type": "object",
            "properties": {"enabled": {"type": "boolean"}},
            "required": ["enabled"],
        },
        timeout=5.0,
    )
    async def mental_omega_set_session_control(self, enabled: bool, **_):
        try:
            result = await self._change_session_control(enabled)
            status = "enabled" if result.get("control_enabled") else "disabled"
            return {"summary": f"Mental Omega session control {status}", **result}
        except (BridgeError, PolicyError) as exc:
            return {"summary": f"control change refused: {exc}", "accepted": False}

    async def _change_session_control(self, enabled: bool) -> dict[str, Any]:
        if enabled is not True:
            self._session_enabled = False
            self._attack_tasks.clear()
            self._load_tasks.clear()
            self._production_tasks.clear()
            self._base_tasks.clear()
            self._force_tasks.clear()
            self._defense_tasks.clear()
            self._adaptive_tasks.clear()
            try:
                result = await asyncio.to_thread(self._client.set_control, False)
            except BridgeError as exc:
                return {"control_enabled": False, "bridge_warning": str(exc)}
            return {"control_enabled": False, "bridge": result}
        if self._cfg.get("commands_enabled") is not True:
            raise PolicyError("commands_enabled is false in plugin configuration")
        health = await asyncio.to_thread(self._client.health)
        capabilities = health.get("capabilities", {})
        if not isinstance(capabilities, dict) or capabilities.get("commands") is not True:
            raise PolicyError("bridge does not advertise command capability")
        result = await asyncio.to_thread(
            self._client.set_control,
            True,
            network_opt_in=self._cfg.get("allow_network_control", False) is True,
        )
        if result.get("control_enabled") is not True:
            raise PolicyError(str(result.get("error", "native control was refused")))
        self._session_enabled = True
        return {"control_enabled": True, "bridge": result}

    async def _ensure_control_for_order(self) -> None:
        if self._session_enabled:
            return
        result = await self._change_session_control(True)
        if result.get("control_enabled") is not True:
            raise PolicyError("native session control could not be enabled")

    def _policy(self) -> CommandPolicy:
        modes = self._cfg.get("allowed_modes", ["skirmish", "campaign"])
        if not isinstance(modes, list):
            modes = []
        return CommandPolicy(
            commands_enabled=self._cfg.get("commands_enabled") is True,
            session_enabled=self._session_enabled,
            allowed_modes=frozenset(str(mode) for mode in modes),
            max_snapshot_age_ms=int(self._cfg.get("max_snapshot_age_ms", 2000)),
            allow_network_game=self._cfg.get("allow_network_control", False) is True,
        )

    def _resolve_command_units(
        self,
        state: dict[str, Any],
        group_number: Any,
        query: Any,
        damaged_only: bool,
        include_buildings: bool = False,
    ) -> list[dict[str, Any]]:
        object_ids = None
        if group_number is not None:
            group = self._group_number(group_number)
            native_ids = [
                int(item["object_id"])
                for item in state.get("visible_entities", [])
                if isinstance(item, dict)
                and item.get("relation") == "self"
                and item.get("alive") is True
                and item.get("controllable") is True
                and item.get("group_number") == group
                and isinstance(item.get("object_id"), int)
            ]
            if native_ids:
                units = select_owned_units(
                    state,
                    str(query).strip() if query else None,
                    object_ids=native_ids,
                    damaged_only=damaged_only,
                    health_below=1.0,
                    include_buildings=include_buildings,
                )
                if not units:
                    raise PolicyError(f"原生控制组 {group} 中没有符合条件的可控单位")
                return units
            saved = self._groups.get(group)
            if not saved:
                raise PolicyError(f"副官控制组 {group} 尚未建立")
            object_ids = list(saved["object_ids"])
        units = select_owned_units(
            state,
            str(query).strip() if query else None,
            object_ids=object_ids,
            damaged_only=damaged_only,
            health_below=1.0,
            include_buildings=include_buildings,
        )
        if not units:
            raise PolicyError("没有找到符合条件的己方可控对象")
        return units

    async def _submit_unit_command(
        self,
        state: dict[str, Any],
        unit_id: int,
        action: str,
        target_cell: dict[str, int] | None = None,
        target_object_id: int | None = None,
        group_number: int | None = None,
        replace_group: bool = False,
        override_team: bool = False,
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            self._submit_unit_command_sync,
            state,
            unit_id,
            action,
            target_cell=target_cell,
            target_object_id=target_object_id,
            group_number=group_number,
            replace_group=replace_group,
            override_team=override_team,
        )

    def _submit_unit_command_sync(
        self,
        state: dict[str, Any],
        unit_id: int,
        action: str,
        target_cell: dict[str, int] | None = None,
        target_object_id: int | None = None,
        group_number: int | None = None,
        replace_group: bool = False,
        override_team: bool = False,
        append_waypoint: bool = False,
    ) -> dict[str, Any]:
        if action == "attack" and not self._ifv_attack_ready(state, unit_id):
            return {
                "accepted": False,
                "error": "empty IFV is locked for passenger assembly",
            }
        try:
            command = self._policy().validate_unit(
                state,
                unit_id,
                action,
                target_cell=target_cell,
                target_object_id=target_object_id,
                group_number=group_number,
                replace_group=replace_group,
                override_team=override_team,
            )
            command = {key: value for key, value in command.items() if value is not None}
            if append_waypoint:
                command["append_waypoint"] = True
            command["request_id"] = str(uuid.uuid4())
            result = self._client.command_unit(command)
            if result.get("accepted") is not True:
                return {"accepted": False, "error": "bridge did not confirm acceptance"}
            return {"accepted": True, "request_id": result.get("request_id")}
        except (BridgeError, PolicyError, TypeError, ValueError) as exc:
            return {"accepted": False, "error": str(exc)}

    async def _wait_for_movement(
        self,
        unit_id: int,
        start_cell: dict[str, int],
        target_cell: dict[str, int] | None,
        timeout: float = COMMAND_PROGRESS_TIMEOUT_SECONDS,
    ) -> tuple[bool, dict[str, Any] | None]:
        deadline = time.monotonic() + timeout
        latest = None
        while time.monotonic() < deadline:
            await asyncio.sleep(COMMAND_PROGRESS_POLL_SECONDS)
            state = await asyncio.to_thread(self._client.state)
            latest = find_entity(state, unit_id)
            if latest is None:
                continue
            current_cell = latest.get("cell")
            if isinstance(current_cell, dict) and target_cell is None:
                if current_cell != start_cell:
                    return True, latest
            elif isinstance(current_cell, dict) and isinstance(target_cell, dict):
                if movement_progress(start_cell, current_cell, target_cell):
                    return True, latest
        return False, latest

    async def _move_one_adaptive(
        self,
        unit: dict[str, Any],
        candidates: list[dict[str, int]],
        *,
        max_attempts: int = 8,
    ) -> dict[str, Any]:
        unit_id = int(unit["object_id"])
        start_cell = unit.get("cell")
        if not isinstance(start_cell, dict) or not candidates:
            return {"unit": entity_label(unit), "accepted": False, "error": "没有可用移动位置"}
        last_error = "未观察到单位移动"
        accepted_once = False
        for attempt, target_cell in enumerate(candidates[:max_attempts]):
            state = await asyncio.to_thread(self._client.state)
            current = find_entity(state, unit_id)
            if current is None:
                return {"unit": entity_label(unit), "accepted": False, "error": "单位已离开当前可控状态"}
            current_cell = current.get("cell")
            if not isinstance(current_cell, dict):
                return {"unit": entity_label(unit), "accepted": False, "error": "单位位置不可用"}
            takeover = attempt > 0 and current.get("in_tactical_team") is True
            result = await self._submit_unit_command(
                state,
                unit_id,
                "move",
                target_cell=target_cell,
                override_team=takeover,
            )
            if not result.get("accepted"):
                last_error = str(result.get("error") or "移动命令被拒绝")
                continue
            accepted_once = True
            moved, latest = await self._wait_for_movement(
                unit_id, current_cell, target_cell
            )
            if moved:
                return {
                    "unit": entity_label(unit),
                    "accepted": True,
                    "verified": True,
                    "team_override": takeover,
                }
            if latest is not None and distance_sq(latest, target_cell) <= 4:
                return {
                    "unit": entity_label(unit),
                    "accepted": True,
                    "verified": True,
                    "arrived": True,
                    "team_override": takeover,
                }
        return {
            "unit": entity_label(unit),
            "accepted": accepted_once,
            "verified": False,
            "error": last_error,
        }

    async def _repair_sequence(
        self,
        source: dict[str, Any],
        targets: list[dict[str, Any]],
        slot: int,
    ) -> list[dict[str, Any]]:
        results = []
        for target in targets:
            results.append(await self._repair_one_adaptive_strict(source, target, slot))
        return results

    async def _repair_one_adaptive_strict(
        self,
        source: dict[str, Any],
        target: dict[str, Any],
        slot: int,
    ) -> dict[str, Any]:
        """Repair only after observing an attack/repair mission or health growth."""
        source_id = int(source["object_id"])
        current_target = target
        last_error = "repair did not start"
        max_attempts = max(3, min(int(self._cfg.get("repair_attempts", 5)), 8))
        for attempt in range(max_attempts):
            state = await asyncio.to_thread(self._client.state)
            current_source = find_entity(state, source_id)
            current_target = reacquire_target(state, current_target)
            if current_source is None:
                return {"repairer": entity_label(source), "accepted": False, "error": "repairer is no longer controllable"}
            if current_target is None:
                return {"repairer": entity_label(source), "target": entity_label(target), "accepted": True, "verified": True, "status": "target repaired or left damaged set"}
            target_id = int(current_target["object_id"])
            if target_id == source_id:
                return {"repairer": entity_label(source), "accepted": False, "error": "repair target is the repairer itself"}
            target_cell = current_target.get("cell")
            source_cell = current_source.get("cell")
            before_health = current_target.get("health_ratio")
            command = await self._submit_unit_command(
                state, source_id, "repair", target_object_id=target_id
            )
            if command.get("accepted") is True:
                observed = await self._observe_repair_start(
                    source_id, current_target, before_health, timeout=3.0
                )
                if observed["started"]:
                    return {
                        "repairer": entity_label(source),
                        "target": entity_label(current_target),
                        "accepted": True,
                        "verified": observed["health_improved"],
                        "repair_started": True,
                        "attempts": attempt + 1,
                    }
                last_error = "native command accepted but repair mission did not start"
            else:
                last_error = str(command.get("error") or "native repair command was rejected")
            if isinstance(target_cell, dict):
                move_result = await self._move_one_adaptive(
                    current_source,
                    staging_cells(target_cell, source_cell, slot=slot + attempt),
                    max_attempts=5,
                )
                if not move_result.get("accepted"):
                    last_error = str(move_result.get("error") or last_error)
            await asyncio.sleep(0.25)
        return {
            "repairer": entity_label(source),
            "target": entity_label(current_target or target),
            "accepted": False,
            "verified": False,
            "error": last_error,
            "attempts": max_attempts,
        }

    async def _observe_repair_start(
        self,
        source_id: int,
        target: dict[str, Any],
        before_health: Any,
        timeout: float,
    ) -> dict[str, bool]:
        deadline = time.monotonic() + timeout
        target_id = int(target["object_id"])
        while time.monotonic() < deadline:
            await asyncio.sleep(0.25)
            state = await asyncio.to_thread(self._client.state)
            source = find_entity(state, source_id)
            current = find_entity(state, target_id)
            if current is None:
                replacement = reacquire_target(state, target)
                if replacement is None:
                    return {"started": True, "health_improved": True}
                current = replacement
            health = current.get("health_ratio")
            improved = (
                isinstance(before_health, (int, float))
                and isinstance(health, (int, float))
                and float(health) > float(before_health) + 0.0001
            )
            mission_started = source is not None and source.get("mission_id") in {1, 29}
            if improved:
                return {"started": True, "health_improved": True}
            if mission_started:
                return {"started": True, "health_improved": False}
        return {"started": False, "health_improved": False}

    async def _repair_one_adaptive(
        self,
        source: dict[str, Any],
        target: dict[str, Any],
        slot: int,
    ) -> dict[str, Any]:
        source_id = int(source["object_id"])
        current_target = target
        for phase in range(2):
            state = await asyncio.to_thread(self._client.state)
            current_source = find_entity(state, source_id)
            current_target = reacquire_target(state, current_target)
            if current_source is None:
                return {"repairer": entity_label(source), "accepted": False, "error": "维修单位已不可控"}
            if current_target is None:
                return {
                    "repairer": entity_label(source),
                    "accepted": phase > 0,
                    "verified": phase > 0,
                    "status": "目标已修复或离开视野",
                }
            if current_target.get("object_id") == source_id:
                return {"repairer": entity_label(source), "accepted": False, "error": "没有其他受损目标"}
            start_cell = current_source.get("cell")
            target_cell = current_target.get("cell")
            before_health = current_target.get("health_ratio")
            result = await self._submit_unit_command(
                state,
                source_id,
                "repair",
                target_object_id=int(current_target["object_id"]),
            )
            if result.get("accepted") and isinstance(start_cell, dict):
                moved, _ = await self._wait_for_movement(
                    source_id, start_cell, target_cell if isinstance(target_cell, dict) else None,
                    timeout=0.8,
                )
                refreshed = await asyncio.to_thread(self._client.state)
                refreshed_target = reacquire_target(refreshed, current_target)
                health_improved = (
                    refreshed_target is not None
                    and isinstance(before_health, (int, float))
                    and isinstance(refreshed_target.get("health_ratio"), (int, float))
                    and float(refreshed_target["health_ratio"]) > float(before_health)
                )
                if moved or health_improved or phase > 0:
                    return {
                        "repairer": entity_label(source),
                        "target": entity_label(current_target),
                        "accepted": True,
                        "verified": moved or health_improved,
                    }
            if not isinstance(target_cell, dict):
                continue
            move_result = await self._move_one_adaptive(
                current_source,
                staging_cells(target_cell, current_source.get("cell"), slot=slot),
                max_attempts=4,
            )
            if not move_result.get("accepted"):
                return {
                    "repairer": entity_label(source),
                    "target": entity_label(current_target),
                    "accepted": False,
                    "error": move_result.get("error") or result.get("error"),
                }
        return {
            "repairer": entity_label(source),
            "target": entity_label(current_target),
            "accepted": False,
            "error": "未确认维修单位接近或开始维修",
        }

    async def _explore_one_adaptive(self, unit: dict[str, Any]) -> dict[str, Any]:
        unit_id = int(unit["object_id"])
        start_cell = unit.get("cell")
        if not isinstance(start_cell, dict):
            return {"unit": entity_label(unit), "accepted": False, "error": "单位位置不可用"}
        accepted_once = False
        last_error = "未观察到单位移动"
        for attempt in range(2):
            state = await asyncio.to_thread(self._client.state)
            current = find_entity(state, unit_id)
            if current is None:
                return {"unit": entity_label(unit), "accepted": False, "error": "单位已不可控"}
            current_cell = current.get("cell")
            if not isinstance(current_cell, dict):
                break
            result = await self._submit_unit_command(
                state,
                unit_id,
                "explore",
                override_team=attempt > 0 and current.get("in_tactical_team") is True,
            )
            if not result.get("accepted"):
                last_error = str(result.get("error") or "没有可探索的黑幕边界")
                continue
            accepted_once = True
            moved, _ = await self._wait_for_movement(unit_id, current_cell, None)
            if moved:
                return {"unit": entity_label(unit), "accepted": True, "verified": True}
        return {
            "unit": entity_label(unit),
            "accepted": accepted_once,
            "verified": False,
            "error": last_error,
        }

    def _matching_tech_landmarks(
        self, state: dict[str, Any], query: str
    ) -> list[dict[str, Any]]:
        text = str(query or "").strip().casefold().replace(" ", "")
        if not text:
            raise PolicyError("building_query is required")
        matched_ids = {
            item.get("object_id") for item in match_visible_entities(state, query)
        }
        entity_relations = {
            item.get("object_id"): item.get("relation")
            for item in state.get("visible_entities", [])
            if isinstance(item, dict)
        }
        player_house_id = state.get("player", {}).get("house_id")
        landmarks = state.get("landmarks")
        landmarks = landmarks if isinstance(landmarks, list) else []
        result = []
        for item in landmarks:
            if not isinstance(item, dict) or item.get("kind") != "tech_building":
                continue
            relation = entity_relations.get(item.get("object_id"))
            if (
                item.get("alive") is not True
                or item.get("owner_house_id") == player_house_id
                or item.get("controllable") is True
                or relation in {"self", "ally"}
            ):
                continue
            type_id = str(item.get("type_id", "")).upper()
            labels = (
                str(item.get("display_name", "")),
                TECH_NAMES.get(type_id, ""),
                type_id,
            )
            if item.get("object_id") in matched_ids or any(
                text in value.casefold().replace(" ", "") for value in labels if value
            ):
                result.append({**item, "relation": relation})
        return sorted(result, key=lambda item: int(item.get("object_id", 0)))

    @staticmethod
    def _select_repair_sources(
        state: dict[str, Any], query: str
    ) -> list[dict[str, Any]]:
        sources = select_owned_units(state, query.strip()) if query.strip() else []
        if sources:
            return sources
        text = query.casefold()
        if not any(word in text for word in ("维修", "修理", "医疗", "军医", "治疗")):
            return []
        repair_type_ids = {
            type_id
            for type_id, record in load_catalog().items()
            if isinstance(record, dict)
            and any(
                marker in str(record.get("primary_weapon", "")).casefold()
                for marker in ("repair", "heal", "rejuven")
            )
        }
        return [
            item for item in select_owned_units(state)
            if str(item.get("type_id", "")).upper() in repair_type_ids
        ]

    @staticmethod
    def _group_number(value: Any) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 9:
            raise PolicyError("group_number must be an integer from 0 to 9")
        return value

    @staticmethod
    def _centroid(units: list[dict[str, Any]]) -> dict[str, int]:
        cells = [item.get("cell") for item in units]
        if not cells or any(not isinstance(cell, dict) for cell in cells):
            raise PolicyError("selected units do not have usable positions")
        return {
            "x": round(sum(int(cell["x"]) for cell in cells) / len(cells)),
            "y": round(sum(int(cell["y"]) for cell in cells) / len(cells)),
        }

    @staticmethod
    def _distance_sq(a: dict[str, Any], b: dict[str, Any]) -> int:
        acell, bcell = a.get("cell"), b.get("cell")
        if not isinstance(acell, dict) or not isinstance(bcell, dict):
            return 2**31 - 1
        return (int(acell["x"]) - int(bcell["x"])) ** 2 + (
            int(acell["y"]) - int(bcell["y"])
        ) ** 2

    @staticmethod
    def _compute_runtime_code_fingerprint() -> str:
        digest = hashlib.sha256()
        root = Path(__file__).resolve().parent
        for name in (
            "plugin.py", "base_development.py", "building_command.py",
            "combat_response.py", "strategic_ai.py", "us_strategy.py",
        ):
            path = root / name
            digest.update(name.encode("ascii"))
            digest.update(path.read_bytes())
        return digest.hexdigest()[:16]

    def _append_diagnostic_log(
        self, filename: str, payload: dict[str, Any]
    ) -> None:
        record = {
            "timestamp": time.time(),
            "code_fingerprint": getattr(
                self, "_runtime_code_fingerprint", "unknown"
            ),
            **payload,
        }
        path = Path(__file__).resolve().parent / "logs" / filename
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            lock = getattr(self, "_diagnostic_log_lock", None)
            if lock is None:
                lock = threading.Lock()
                self._diagnostic_log_lock = lock
            with lock, path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
                handle.write("\n")
        except OSError as exc:
            self.logger.warning("Mental Omega diagnostic log write failed: %s", exc)

    @staticmethod
    def _ifv_attack_ready(state: dict[str, Any], unit_id: int) -> bool:
        unit = find_entity(state, int(unit_id))
        if unit is None or str(unit.get("type_id") or "").upper() != "FV":
            return True
        passenger_count = unit.get("passenger_count")
        return isinstance(passenger_count, int) and passenger_count > 0

    def _focus_attack_target(
        self, units: list[dict[str, Any]], targets: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Choose a local tactical target, never an economy unit or a premature base core."""
        targets = [item for item in targets if not is_economic_unit(item)]
        if not targets:
            raise ValueError("no eligible attack targets")
        return select_assault_target(units, targets, targets)

    @staticmethod
    def _aircraft_ready_for_attack(unit: dict[str, Any]) -> bool:
        if str(unit.get("kind", "")).casefold() != "aircraft":
            return True
        maximum = unit.get("max_ammo")
        ammo = unit.get("ammo")
        if not isinstance(maximum, int) or maximum <= 0:
            return True
        return isinstance(ammo, int) and ammo >= maximum

    def _prepare_deployable_attack_posture(
        self,
        state: dict[str, Any],
        task: dict[str, Any],
        unit: dict[str, Any],
        target: dict[str, Any],
        now: float,
    ) -> bool:
        """Toggle GI/GGI stance before an attack order when telemetry exposes it."""
        if not is_deployable_combat_unit(unit):
            return False
        deployed = unit.get("is_deployed")
        if not isinstance(deployed, bool):
            return False
        unit_cell, target_cell = unit.get("cell"), target.get("cell")
        if not isinstance(unit_cell, dict) or not isinstance(target_cell, dict):
            return False
        distance_sq = self._distance_sq(unit, target)
        # A deployed GI/GGI should fire from a stable position; stand up first
        # when the target is outside the effective firing envelope.
        desired_deployed = distance_sq <= 8 * 8
        if deployed == desired_deployed:
            return False
        unit_id = int(unit["object_id"])
        last = float(task.setdefault("deployment_order_at", {}).get(unit_id, 0.0))
        if now - last < 2.0:
            return True
        result = self._submit_unit_command_sync(state, unit_id, "deploy")
        task["deployment_order_at"][unit_id] = now
        if result.get("accepted") is True:
            task["deployment_orders"] = int(task.get("deployment_orders", 0)) + 1
        return True

    def _release_deployed_combat_posture(
        self,
        state: dict[str, Any],
        task: dict[str, Any],
        units: list[dict[str, Any]],
        now: float,
    ) -> int:
        """Stand deployed infantry up once no valid target remains."""
        released = 0
        order_at = task.setdefault("deployment_order_at", {})
        for unit in units:
            if not is_deployable_combat_unit(unit) or unit.get("is_deployed") is not True:
                continue
            unit_id = int(unit["object_id"])
            if now - float(order_at.get(unit_id, 0.0)) < 2.0:
                continue
            result = self._submit_unit_command_sync(state, unit_id, "deploy")
            order_at[unit_id] = now
            if result.get("accepted") is True:
                released += 1
                task["deployment_orders"] = int(task.get("deployment_orders", 0)) + 1
        return released

    def _visible_attack_targets(
        self,
        state: dict[str, Any],
        target_query: str = "",
        use_native_query: bool = False,
    ) -> list[dict[str, Any]]:
        requested_building_categories = building_query_categories(target_query)
        generic_kind = "building" if requested_building_categories else self._generic_attack_kind(target_query)
        allowed_relations = {"enemy", "neutral"} if target_query else {"enemy"}
        if target_query and generic_kind is None:
            targets = match_visible_entities(state, target_query)
        else:
            targets = list(state.get("visible_entities", []))
        targets = [
            item
            for item in targets
            if isinstance(item, dict)
            and item.get("relation") in allowed_relations
            and item.get("visible") is True
            and item.get("alive") is True
            and isinstance(item.get("object_id"), int)
            and is_valid_attack_target(item)
            and (generic_kind is None or self._attack_kind_matches(item, generic_kind))
        ]
        if generic_kind in {"building", "base"}:
            categories = (
                {"construction_yard"}
                if generic_kind == "base"
                else requested_building_categories
            )
            targets = [
                item for item in targets
                if strategic_building_priority(item, categories or None) >= 0
            ]

        type_ids = resolve_type_ids(target_query) if target_query and generic_kind is None else []
        can_query = hasattr(self._client, "query_entities") and (
            not target_query or bool(type_ids)
        )
        if use_native_query and can_query:
            query_results = []
            for relation in sorted(allowed_relations):
                result = self._client.query_entities(
                    {
                        "relation": relation,
                        "type_ids": type_ids or None,
                        "kind": "all",
                        "region": "all",
                        "visible": True,
                        "limit": 200,
                    }
                )
                query_results.extend(result.get("entities", []))
            queried = [
                item
                for item in query_results
                if isinstance(item, dict)
                and item.get("relation") in allowed_relations
                and item.get("visible") is True
                and item.get("alive") is True
                and isinstance(item.get("object_id"), int)
                and is_valid_attack_target(item)
                and (generic_kind is None or self._attack_kind_matches(item, generic_kind))
            ]
            by_id = {int(item["object_id"]): item for item in targets}
            by_id.update({int(item["object_id"]): item for item in queried})
            targets = list(by_id.values())
            visible = state.get("visible_entities")
            if isinstance(visible, list):
                known = {
                    item.get("object_id") for item in visible if isinstance(item, dict)
                }
                visible.extend(
                    item for item in queried if item.get("object_id") not in known
                )
        if generic_kind in {"building", "base"}:
            return sorted(
                targets,
                key=lambda item: (
                    -strategic_building_priority(item, requested_building_categories or None),
                    int(item["object_id"]),
                ),
            )
        return sorted(targets, key=lambda item: int(item["object_id"]))

    @staticmethod
    def _generic_attack_kind(value: Any) -> str | None:
        """Translate ordinary commander wording into a visible target category."""
        text = "".join(ch for ch in str(value or "").casefold() if ch.isalnum())
        if not text:
            return None
        if any(token in text for token in ("敌人", "敌军", "敌方", "敌兵", "enemy", "enemies")):
            return "all"
        if any(token in text for token in ("建筑", "建筑物", "结构", "structure", "building")):
            return "building"
        if any(token in text for token in ("单位", "部队", "军队", "unit", "units")):
            return "unit"
        if any(token in text for token in ("基地", "建造场", "基地车", "constructionyard", "conyard")):
            return "base"
        return None

    @staticmethod
    def _resolve_fire_control(value: Any, target_query: Any = None) -> str:
        text = "".join(ch for ch in str(value or "").casefold() if ch.isalnum())
        if any(token in text for token in ("focus", "concentrate", "集中", "集火", "集中攻击")):
            return "focus"
        if any(token in text for token in ("spread", "分散", "分配", "火力分配", "各自攻击")):
            return "spread"
        if any(token in text for token in ("auto", "threat", "自动", "威胁", "智能")):
            return "auto"
        # Explicitly named targets are normally a commander-selected focus;
        # omitted targets use native threat evaluation.
        return "focus" if str(target_query or "").strip() else "auto"

    @staticmethod
    def _attack_kind_matches(item: dict[str, Any], category: str) -> bool:
        kind = str(item.get("kind", "")).casefold()
        if category == "all":
            return True
        if category == "building":
            return kind in {"building", "structure"}
        if category == "base":
            return kind in {"building", "structure", "construction_yard", "mobile_base"}
        return kind not in {"building", "structure", "tech_building", "map_object"}

    @staticmethod
    def _specific_attack_query(value: Any) -> str:
        text = str(value or "").strip()
        compact = "".join(character for character in text.casefold() if character.isalnum())
        for filler in ("当前", "现在", "可见", "视野内", "最近", "全部", "所有", "剩余", "附近", "的"):
            compact = compact.replace(filler, "")
        if compact in {"敌人", "敌军", "敌方", "敌兵", "enemy", "enemies"}:
            return ""
        return text

    def _validate_plan_action(
        self, plan: dict[str, Any], order: dict[str, Any], state: dict[str, Any], result: dict[str, Any]
    ) -> dict[str, Any]:
        """Validate bridge acceptance separately from observed completion."""
        return self._action_validator.validate(plan, order, state, result)

    def _execute_plan_order(
        self, plan: dict[str, Any], order: dict[str, Any], state: dict[str, Any]
    ) -> dict[str, Any]:
        """Run one plan step from the watcher thread without blocking dialogue."""
        action = str(order["action"])
        if action == "wait":
            return {"accepted": True, "complete": True}
        if action == "produce":
            production = state.get("production", {})
            available = production.get("sidebar_available", []) if isinstance(production, dict) else []
            live_by_id = {
                str(item.get("type_id", "")).upper(): item
                for item in available if isinstance(item, dict) and item.get("type_id")
            }
            live_ids = {
                str(item.get("type_id", "")).upper()
                for item in available if isinstance(item, dict) and item.get("type_id")
            }
            requested = str(order.get("type_id") or "").strip().upper()
            if not requested:
                matches = catalog_summary(str(order.get("product") or "").strip())
                requested = next(
                    (str(item.get("type_id", "")).upper() for item in matches
                     if str(item.get("type_id", "")).upper() in live_ids),
                    "",
                )
            if not requested or requested not in live_ids:
                return {"accepted": False, "retryable": True, "error": "等待目标进入原生可生产列表"}
            remaining = int(order.get("remaining_quantity", order.get("quantity", 1)))
            kind = str(live_by_id.get(requested, {}).get("kind", "unknown"))
            flag_prefix = f"plan:{plan.get('plan_id', 'direct')}:{order.get('order_id', 'produce')}"
            current_ids = {
                int(item["object_id"])
                for item in state.get("visible_entities", [])
                if isinstance(item, dict)
                and item.get("relation") == "self"
                and item.get("alive") is True
                and str(item.get("type_id", "")).upper() == requested
                and isinstance(item.get("object_id"), int)
            }
            baseline_ids = set(order.setdefault("production_baseline_ids", sorted(current_ids)))
            observed_ids = set(order.setdefault("produced_object_ids", []))
            observed_ids.update(current_ids - baseline_ids)
            order["produced_object_ids"] = sorted(observed_ids)
            requested_quantity = max(1, int(order.get("quantity", 1)))
            if kind == "building":
                owned_count = sum(
                    isinstance(item, dict)
                    and item.get("relation") == "self"
                    and item.get("alive") is True
                    and str(item.get("type_id", "")).upper() == requested
                    for item in state.get("visible_entities", [])
                )
                previous_count = int(order.setdefault("building_owned_count", owned_count))
                if order.get("building_in_flight") is True:
                    if owned_count <= previous_count:
                        self._command_runtime.flags.set(
                            f"{flag_prefix}:awaiting_placement", True,
                            source="order_plan", priority=90,
                        )
                        return {
                            "accepted": True,
                            "complete": False,
                            "status": "ready_to_place",
                        }
                    order["building_in_flight"] = False
                    order["building_owned_count"] = owned_count
                    self._command_runtime.flags.clear(
                        f"{flag_prefix}:awaiting_placement",
                        source="order_plan", priority=90,
                    )
                    self._command_runtime.flags.set(
                        f"{flag_prefix}:building_placed", True,
                        source="order_plan", priority=90,
                    )
            if remaining <= 0:
                if len(observed_ids) < requested_quantity:
                    self._command_runtime.flags.set(
                        f"{flag_prefix}:awaiting_spawn", True,
                        source="order_plan", priority=90,
                    )
                    return {
                        "accepted": True,
                        "complete": False,
                        "status": "waiting_for_units",
                        "observed_count": len(observed_ids),
                        "requested_count": requested_quantity,
                    }
                self._command_runtime.flags.clear(
                    f"{flag_prefix}:awaiting_spawn",
                    source="order_plan", priority=90,
                )
                self._command_runtime.flags.set(
                    f"{flag_prefix}:completed", True,
                    source="order_plan", priority=90,
                )
                return {"accepted": True, "complete": True}
            result = self._client.command_production({
                "request_id": str(uuid.uuid4()), "type_id": requested,
                "quantity": 1 if kind == "building" else min(50, remaining),
            })
            accepted = max(0, int(result.get("accepted_count", 0)))
            if not accepted:
                return {"accepted": False, "retryable": True, "error": str(result.get("last_error") or "生产事件未被接受")}
            order["remaining_quantity"] = max(0, remaining - accepted)
            if kind == "building":
                order["building_in_flight"] = True
                self._command_runtime.flags.set(
                    f"{flag_prefix}:awaiting_placement", True,
                    source="order_plan", priority=90,
                )
            return {
                "accepted": True,
                # Side-bar acceptance is not completion. A later watcher tick
                # must observe every newly created object id.
                "complete": False,
                "accepted_count": accepted,
            }

        if action == "load":
            task_id = str(order.get("load_task_id") or "")
            task = self._load_tasks.get(task_id) if task_id else None
            if task is not None:
                self._run_load_task(task, state)
                if task.get("status") == "completed":
                    return {
                        "accepted": True, "complete": True,
                        "loaded_count": int(task.get("loaded_count", 0)),
                        "task_id": task_id,
                    }
                return {
                    "accepted": True, "complete": False,
                    "status": task.get("status"), "task_id": task_id,
                    "loaded_count": int(task.get("loaded_count", 0)),
                }

            passengers = self._resolve_command_units(
                state, order.get("group_number"), order.get("query"), False
            )
            transport_query = str(order.get("target_query") or "").strip()
            transports = select_owned_units(state, transport_query)
            transports = [
                item for item in transports
                if item.get("transport_capacity") is True
                and isinstance(item.get("object_id"), int)
            ]
            produced_ids = {
                int(value)
                for dependency_id in order.get("depends_on", [])
                for dependency in plan.get("orders", [])
                if dependency.get("order_id") == dependency_id
                for value in dependency.get("produced_object_ids", [])
            }
            if produced_ids:
                produced_passengers = [
                    item for item in passengers if int(item["object_id"]) in produced_ids
                ]
                produced_transports = [
                    item for item in transports if int(item["object_id"]) in produced_ids
                ]
                passengers = produced_passengers or passengers
                transports = produced_transports or transports
            maximum = order.get("maximum_passengers")
            if maximum is not None:
                passengers = passengers[:max(1, int(maximum))]
            if not transports:
                return {"accepted": False, "retryable": True, "error": "等待可用载具"}
            if not passengers:
                return {"accepted": False, "retryable": True, "error": "等待可装载乘员"}
            task = self._create_load_task(
                passengers, transports,
                str(order.get("query") or ""), transport_query,
            )
            self._load_tasks[task["task_id"]] = task
            order["load_task_id"] = task["task_id"]
            self._run_load_task(task, state)
            return {
                "accepted": True, "complete": task.get("status") == "completed",
                "status": task.get("status"), "task_id": task["task_id"],
            }

        if action == "deploy":
            deployed_ids = set(order.get("deploy_source_ids", []))
            baseline_yards = set(order.get("deploy_baseline_yards", []))
            current_yards = {
                int(item["object_id"])
                for item in state.get("visible_entities", [])
                if isinstance(item, dict)
                and item.get("relation") == "self"
                and item.get("alive") is True
                and item.get("kind") == "building"
                and str(item.get("type_id", "")).upper() in {"GACNST", "NACNST", "YACNST", "FACNST"}
                and isinstance(item.get("object_id"), int)
            }
            if deployed_ids and (current_yards - baseline_yards):
                order["deployed_object_ids"] = sorted(current_yards - baseline_yards)
                return {"accepted": True, "complete": True}
            units = self._resolve_command_units(
                state, order.get("group_number"), order.get("query"), False
            )
            if not deployed_ids:
                order["deploy_source_ids"] = [int(item["object_id"]) for item in units]
                order["deploy_baseline_yards"] = sorted(current_yards)
            issued = sum(
                self._submit_unit_command_sync(state, int(unit["object_id"]), "deploy").get("accepted") is True
                for unit in units
            )
            return {
                "accepted": issued > 0, "complete": False,
                "accepted_count": issued, "retryable": True,
                "error": None if issued else "等待机动建设车停止并进入可展开状态",
            }

        units = self._resolve_command_units(
            state, order.get("group_number"), order.get("query"), False
        )
        units = sorted(units, key=lambda item: int(item.get("object_id", 0)))
        unit_offset = max(0, int(order.get("unit_offset", 0)))
        unit_limit = order.get("unit_limit")
        units = units[unit_offset:]
        if unit_limit is not None:
            units = units[:max(1, int(unit_limit))]
        if action == "move":
            existing_task_id = str(order.get("adaptive_task_id") or "")
            if existing_task_id:
                tasks = self._adaptive_tasks.tasks(
                    include_finished=True, task_id=existing_task_id
                )
                if not tasks:
                    order.pop("adaptive_task_id", None)
                elif tasks[0].get("status") == "completed":
                    return {"accepted": True, "complete": True, "task_id": existing_task_id}
                elif tasks[0].get("status") in {"cancelled", "expired"}:
                    return {"accepted": False, "retryable": True, "error": tasks[0].get("last_error") or "移动任务需要重建"}
                else:
                    return {"accepted": True, "complete": False, "task_id": existing_task_id, "status": tasks[0].get("status")}
            destination = str(order.get("destination") or "").strip()
            if not destination:
                return {"accepted": False, "retryable": False, "error": "移动命令缺少 destination"}
            landmark = (
                resolve_map_region_destination(state, destination)
                or resolve_visible_destination(state, destination, self._centroid(units))
            )
            task = self._adaptive_tasks.create_move_task(
                units, landmark, query=str(order.get("query") or "") or None,
                destination=entity_label(landmark),
                duration_seconds=int(self._cfg.get("adaptive_move_task_seconds", 1800)),
                arrival_radius=int(self._cfg.get("adaptive_arrival_radius_cells", 5)),
            )
            self._adaptive_tasks.tick(state)
            order["adaptive_task_id"] = task["task_id"]
            return {"accepted": True, "complete": False, "task_id": task["task_id"]}
        if action == "explore":
            task = self._adaptive_tasks.create_explore_task(
                units, query=str(order.get("query") or "") or None,
                duration_seconds=int(self._cfg.get("adaptive_explore_task_seconds", 3600)),
            )
            self._adaptive_tasks.tick(state)
            return {"accepted": True, "task_id": task["task_id"]}
        if action == "attack":
            produced_ids = {
                int(value)
                for dependency_id in order.get("depends_on", [])
                for dependency in plan.get("orders", [])
                if dependency.get("order_id") == dependency_id
                for value in dependency.get("produced_object_ids", [])
            }
            if produced_ids:
                produced_units = [
                    item for item in units
                    if isinstance(item.get("object_id"), int)
                    and int(item["object_id"]) in produced_ids
                ]
                units = produced_units or units
            if not units:
                return {"accepted": False, "retryable": True, "error": "waiting for produced units"}
            targets = self._visible_attack_targets(
                state, self._specific_attack_query(order.get("target_query")), True
            )
            if not targets:
                return {"accepted": False, "retryable": True, "error": "等待可见且可攻击的敌军"}
            target = self._focus_attack_target(units, targets)
            issued = 0
            for unit in units:
                result = self._submit_unit_command_sync(
                    state, int(unit["object_id"]), "attack",
                    target_object_id=int(target["object_id"]),
                )
                issued += result.get("accepted") is True
            if order.get("continuous") is True:
                now = time.monotonic()
                task_id = uuid.uuid4().hex[:12]
                self._attack_tasks[task_id] = {
                    "task_id": task_id, "query": str(order.get("query") or ""),
                    "target_query": self._specific_attack_query(order.get("target_query")),
                    "mode": "target", "unit_ids": [int(item["object_id"]) for item in units],
                    "assignments": {int(item["object_id"]): int(target["object_id"]) for item in units},
                    "last_order_at": {}, "created_at": now,
                    "expires_at": now + int(self._cfg.get("attack_task_default_seconds", 21600)),
                    "duration_seconds": int(self._cfg.get("attack_task_default_seconds", 21600)),
                    "orders_issued": issued, "failed_orders": len(units) - issued,
                    "status": "engaging" if issued else "waiting_for_enemy",
                }
            return {"accepted": issued > 0, "accepted_count": issued, "retryable": True}
        if action in {"capture", "repair"}:
            target_query = str(order.get("target_query") or "").strip()
            candidates = match_visible_entities(state, target_query, None) if target_query else []
            candidates = [item for item in candidates if item.get("alive") is True and item.get("visible") is True]
            if action == "capture":
                candidates = [item for item in candidates if item.get("relation") in {"enemy", "neutral"}]
            else:
                candidates = [item for item in candidates if item.get("relation") in {"self", "ally"}]
            if not candidates:
                return {"accepted": False, "retryable": True, "error": "等待目标进入当前视野"}
            target = min(candidates, key=lambda item: self._distance_sq(units[0], item))
            native_action = {"capture": "capture", "repair": "repair"}[action]
            issued = sum(
                self._submit_unit_command_sync(
                    state, int(unit["object_id"]), native_action,
                    target_object_id=int(target["object_id"]),
                ).get("accepted") is True
                for unit in units
            )
            return {"accepted": issued > 0, "accepted_count": issued, "retryable": True}
        issued = sum(
            self._submit_unit_command_sync(state, int(unit["object_id"]), action).get("accepted") is True
            for unit in units
        )
        return {"accepted": issued > 0, "accepted_count": issued, "retryable": True}

    async def _cancel_attack_tasks(self, selected: str = "") -> dict[str, Any]:
        return await self._cancel_background_tasks(
            task_id=selected or None,
            task_type="attack",
            all_matching=not selected,
        )

    async def _cancel_background_tasks(
        self, *, task_id: str | None = None, task_type: str = "any",
        description: str = "", latest: bool = False,
        all_matching: bool = False,
    ) -> dict[str, Any]:
        """Cancel task ownership first, then stop every unit it controlled."""
        terminal = {"completed", "succeeded", "failed", "cancelled", "expired", "skipped"}
        requested_type = str(task_type or "any").lower()
        type_aliases = {
            "move": {"move_near"}, "explore": {"explore_shroud"},
            "adaptive": {"move_near", "explore_shroud"},
        }
        candidates: list[dict[str, Any]] = []

        def add(kind: str, identity: str, created_at: Any, text: str, record: Any) -> None:
            if task_id and identity != task_id:
                return
            allowed = type_aliases.get(requested_type, {requested_type})
            if requested_type != "any" and kind not in allowed:
                return
            if description and description.casefold() not in text.casefold():
                return
            candidates.append({
                "kind": kind, "task_id": identity,
                "created_at": float(created_at or 0.0), "record": record,
            })

        for identity, task in self._attack_tasks.items():
            if str(task.get("status")) not in terminal:
                add("attack", identity, task.get("created_at"),
                    f"{task.get('query', '')} {task.get('target_query', '')}", task)
        for identity, task in getattr(self, "_load_tasks", {}).items():
            if str(task.get("status")) not in terminal:
                add("load", identity, task.get("created_at"),
                    f"{task.get('passenger_query', '')} {task.get('transport_query', '')}", task)
        for identity, task in getattr(self, "_production_tasks", {}).items():
            if str(task.get("status")) not in terminal:
                add("production", identity, task.get("created_at"),
                    f"{task.get('product', '')} {task.get('display_name', '')} {task.get('type_id', '')}", task)
        for identity, task in getattr(self, "_base_tasks", {}).items():
            if str(task.get("status")) not in terminal:
                add("base", identity, task.get("created_at"),
                    f"发展基地 建设基地 {task.get('last_reason', '')}", task)
        for identity, task in getattr(self, "_force_tasks", {}).items():
            if str(task.get("status")) not in terminal:
                add("force", identity, task.get("created_at"),
                    f"持续补员 维持数量 {task.get('query', '')} {task.get('display_name', '')}", task)
        for identity, task in getattr(self, "_defense_tasks", {}).items():
            if str(task.get("status")) not in terminal:
                add("defense", identity, task.get("created_at"),
                    f"防守 防御 {task.get('query', '')} {task.get('destination', '')}", task)
        adaptive_engine = getattr(self, "_adaptive_tasks", None)
        adaptive_tasks = adaptive_engine.tasks(include_finished=False) if adaptive_engine else []
        for task in adaptive_tasks:
            add(str(task.get("kind")), str(task.get("task_id")), task.get("created_at"),
                f"{task.get('query', '')} {task.get('destination', '')}", task)
        plan_engine = getattr(self, "_order_plans", None)
        active_plans = plan_engine.plans(include_finished=False) if plan_engine else []
        for plan in active_plans:
            order_text = " ".join(
                f"{item.get('action', '')} {item.get('query', '')} {item.get('target_query', '')}"
                for item in plan.get("orders", [])
            )
            add("plan", str(plan.get("plan_id")), plan.get("created_at"),
                f"{plan.get('name', '')} {order_text}", plan)

        if not candidates:
            return {"summary": "没有找到符合条件且仍在运行的后台任务", "cancelled": False}
        candidates.sort(key=lambda item: item["created_at"], reverse=True)
        selected = candidates if all_matching else [candidates[0]]
        if task_id:
            selected = [item for item in candidates if item["task_id"] == task_id]
        elif not latest and not all_matching:
            selected = candidates

        # Cancelling a plan owns its child tasks. Cancelling a child also cancels
        # a parent that would otherwise recreate or resume it on the next tick.
        selected_ids = {item["task_id"] for item in selected}
        for item in selected:
            if item["kind"] == "base":
                child_id = item["record"].get("active_production_task_id")
                if child_id:
                    selected_ids.add(str(child_id))
                child_ids = item["record"].get("active_production_task_ids", {})
                if isinstance(child_ids, dict):
                    selected_ids.update(str(value) for value in child_ids.values() if value)
            if item["kind"] == "defense":
                maintain_id = item["record"].get("maintain_task_id")
                if maintain_id:
                    selected_ids.add(str(maintain_id))
        selected_plan_ids = {item["task_id"] for item in selected if item["kind"] == "plan"}
        changed = True
        while changed:
            changed = False
            for plan in active_plans:
                plan_id = str(plan["plan_id"])
                child_ids = {
                    str(order.get(key))
                    for order in plan.get("orders", [])
                    for key in ("adaptive_task_id", "load_task_id", "attack_task_id")
                    if order.get(key)
                }
                if plan_id in selected_plan_ids or child_ids & selected_ids:
                    before = len(selected_ids)
                    selected_ids.update(child_ids)
                    selected_ids.add(plan_id)
                    selected_plan_ids.add(plan_id)
                    changed = changed or len(selected_ids) != before

        unit_ids: set[int] = set()
        cancelled_rows: list[dict[str, str]] = []
        now = time.monotonic()
        for identity, task in self._attack_tasks.items():
            if identity in selected_ids and str(task.get("status")) not in terminal:
                unit_ids.update(int(value) for value in task.get("unit_ids", []))
                task["status"] = "cancelled"
                task["completed_at"] = now
                cancelled_rows.append({"task_id": identity, "task_type": "attack"})
        for identity, task in getattr(self, "_load_tasks", {}).items():
            if identity in selected_ids and str(task.get("status")) not in terminal:
                unit_ids.update(int(value) for value in task.get("passenger_ids", []))
                task["status"] = "cancelled"
                task["completed_at"] = now
                cancelled_rows.append({"task_id": identity, "task_type": "load"})
        for identity, task in getattr(self, "_production_tasks", {}).items():
            if identity in selected_ids and str(task.get("status")) not in terminal:
                task["status"] = "cancelled"
                task["completed_at"] = now
                if hasattr(self, "_command_runtime"):
                    self._command_runtime.flags.set(
                        f"production:{identity}:cancelled", True,
                        source="production_task", priority=95,
                    )
                cancelled_rows.append({"task_id": identity, "task_type": "production"})
        for identity, task in getattr(self, "_base_tasks", {}).items():
            if identity in selected_ids and str(task.get("status")) not in terminal:
                task["status"] = "cancelled"
                task["completed_at"] = now
                cancelled_rows.append({"task_id": identity, "task_type": "base"})
        for identity, task in getattr(self, "_force_tasks", {}).items():
            if identity in selected_ids and str(task.get("status")) not in terminal:
                task["status"] = "cancelled"
                task["completed_at"] = now
                cancelled_rows.append({"task_id": identity, "task_type": "force"})
        for identity, task in getattr(self, "_defense_tasks", {}).items():
            if identity in selected_ids and str(task.get("status")) not in terminal:
                unit_ids.update(int(value) for value in task.get("unit_ids", []))
                task["status"] = "cancelled"
                task["completed_at"] = now
                cancelled_rows.append({"task_id": identity, "task_type": "defense"})
        for task in adaptive_tasks:
            identity = str(task.get("task_id"))
            if identity in selected_ids:
                unit_ids.update(
                    int(unit["object_id"]) for unit in task.get("units", [])
                    if isinstance(unit.get("object_id"), int)
                )
                if adaptive_engine.cancel(identity):
                    cancelled_rows.append({"task_id": identity, "task_type": str(task.get("kind"))})
        for plan in active_plans:
            identity = str(plan["plan_id"])
            if identity not in selected_plan_ids:
                continue
            # Direct plan orders such as guard/deploy may not own a child task.
            for order in plan.get("orders", []):
                if order.get("status") in OrderPlanEngine.TERMINAL:
                    continue
                try:
                    units = self._resolve_command_units(
                        await asyncio.to_thread(self._client.state),
                        order.get("group_number"), order.get("query"), False,
                    )
                    unit_ids.update(int(unit["object_id"]) for unit in units)
                except (BridgeError, PolicyError, TypeError, ValueError):
                    pass
            if plan_engine.cancel(identity):
                cancelled_rows.append({"task_id": identity, "task_type": "plan"})

        stopped = failed = 0
        state: dict[str, Any] | None = None
        if unit_ids:
            try:
                await self._ensure_control_for_order()
                state = await asyncio.to_thread(self._client.state)
            except (BridgeError, PolicyError):
                state = None
        if state is not None:
            for unit_id in sorted(unit_ids):
                try:
                    result = await self._submit_unit_command(state, unit_id, "stop")
                except (BridgeError, PolicyError, TypeError, ValueError):
                    result = {"accepted": False}
                if result.get("accepted") is True:
                    stopped += 1
                else:
                    failed += 1

        if hasattr(self, "_command_runtime"):
            for row in cancelled_rows:
                self._command_runtime.flags.set(
                    f"task:{row['task_id']}:cancelled", True,
                    source="task_cancel", priority=100,
                )
        # Attack tasks have no dependent verification state once their units
        # have been stopped; remove them so legacy status callers also see the
        # clearing duty as gone. Other task kinds keep a terminal audit record.
        for row in cancelled_rows:
            if row["task_type"] == "attack":
                self._attack_tasks.pop(row["task_id"], None)
        return {
            "summary": f"已取消 {len(cancelled_rows)} 个关联任务，并停止 {stopped} 个受控单位",
            "cancelled": bool(cancelled_rows),
            "cancelled_count": len(cancelled_rows),
            "stopped_count": stopped,
            "failed_stop_count": failed,
            "tasks": cancelled_rows,
        }

    def _active_loading_unit_ids(self) -> set[int]:
        terminal = {"cancelled", "expired", "completed", "failed"}
        return {
            int(value)
            for load_task in self._load_tasks.values()
            if load_task.get("status") not in terminal
            for key in ("passenger_ids", "transport_ids")
            for value in load_task.get(key, [])
        }

    def _strategic_hold_unit_ids(
        self, state: dict[str, Any], base_task: dict[str, Any]
    ) -> set[int]:
        held = self._active_loading_unit_ids()
        if not base_task.get("us_strike_attack_task_ids"):
            entities = [
                item for item in state.get("visible_entities", [])
                if isinstance(item, dict)
                and item.get("relation") == "self"
                and item.get("alive") is True
                and item.get("controllable") is True
                and isinstance(item.get("object_id"), int)
            ]
            held.update(
                int(item["object_id"])
                for item in entities
                if str(item.get("type_id") or "").upper() == "FV"
            )
            t3_established = any(
                item.get("kind") == "building"
                and int((unit_profile(str(item.get("type_id") or "")) or {}).get("tech_tier") or 0) >= 3
                for item in entities
            )
            if t3_established:
                for type_id, count, _role in choose_us_convoy_requirements({}):
                    matching = sorted(
                        (
                            item for item in entities
                            if str(item.get("type_id") or "").upper() == type_id
                        ),
                        key=lambda item: int(item["object_id"]),
                    )
                    held.update(int(item["object_id"]) for item in matching[:count])
        return held

    def _run_attack_task(
        self, task: dict[str, Any], state: dict[str, Any]
    ) -> None:
        now = time.monotonic()
        blocked_empty_ifvs = {
            int(unit_id)
            for unit_id in task.get("unit_ids", [])
            if not self._ifv_attack_ready(state, int(unit_id))
        }
        if blocked_empty_ifvs:
            task["blocked_empty_ifv_ids"] = sorted(blocked_empty_ifvs)
            task["unit_ids"] = [
                unit_id for unit_id in task.get("unit_ids", [])
                if int(unit_id) not in blocked_empty_ifvs
            ]
            for unit_id in blocked_empty_ifvs:
                task.get("assignments", {}).pop(unit_id, None)
                task.get("last_order_at", {}).pop(unit_id, None)
                unit = find_entity(state, unit_id)
                if unit is not None and unit.get("mission_id") in {1, 15, 29}:
                    self._submit_unit_command_sync(state, unit_id, "stop")
        loading_ids = self._active_loading_unit_ids()
        if loading_ids:
            task["unit_ids"] = [
                unit_id for unit_id in task.get("unit_ids", [])
                if int(unit_id) not in loading_ids
            ]
            for unit_id in loading_ids:
                task.get("assignments", {}).pop(unit_id, None)
                task.get("last_order_at", {}).pop(unit_id, None)
        economic_ids = {
            int(item["object_id"])
            for item in state.get("visible_entities", [])
            if isinstance(item, dict) and isinstance(item.get("object_id"), int)
            and is_economic_unit(item)
        }
        if economic_ids:
            task["unit_ids"] = [
                unit_id for unit_id in task["unit_ids"]
                if int(unit_id) not in economic_ids
            ]
            for unit_id in economic_ids:
                task.get("assignments", {}).pop(unit_id, None)
                task.get("last_order_at", {}).pop(unit_id, None)
        units = [
            unit for unit in select_owned_units(state, object_ids=list(task["unit_ids"]))
            if not is_economic_unit(unit)
        ]
        retreat_health = task.get("retreat_health_ratio")
        if isinstance(retreat_health, (int, float)):
            threshold = max(0.1, min(0.9, float(retreat_health)))
            retreating = [unit for unit in units if _num_health(unit) < threshold]
            healthy_ids = {
                int(unit["object_id"]) for unit in units if _num_health(unit) >= threshold
            }
            bases = [
                item for item in state.get("visible_entities", [])
                if isinstance(item, dict) and item.get("relation") == "self"
                and item.get("alive") is True and item.get("kind") == "building"
                and isinstance(item.get("cell"), dict)
            ]
            ordered_retreats = task.setdefault("retreat_order_at", {})
            for unit in retreating:
                unit_id = int(unit["object_id"])
                task.get("assignments", {}).pop(unit_id, None)
                task.get("last_order_at", {}).pop(unit_id, None)
                if now - float(ordered_retreats.get(unit_id, 0.0)) < 8.0:
                    continue
                if bases:
                    base = min(bases, key=lambda item: self._distance_sq(unit, item))
                    result = self._submit_unit_command_sync(
                        state, unit_id, "move", target_cell=dict(base["cell"])
                    )
                else:
                    result = self._submit_unit_command_sync(state, unit_id, "guard")
                if result.get("accepted") is True:
                    ordered_retreats[unit_id] = now
            units = [unit for unit in units if int(unit["object_id"]) in healthy_ids]
        if not units:
            task["assignments"].clear()
            task["status"] = "no_controllable_units"
            return
        mode = str(task.get("mode", "target"))
        keepalive = max(
            3.0,
            min(float(self._cfg.get("attack_task_keepalive_seconds", 10.0)), 30.0),
        )
        if mode in {"hunt", "auto_attack"}:
            # Legacy tasks may still say "hunt". Never renew that unbounded
            # mission: use the game's native threat evaluator one target at a
            # time, gated by our visible-hostile eligibility check.
            visible_targets = self._visible_attack_targets(state, "", True)
            issued = 0
            failed = 0
            last_order_at = task.setdefault("last_order_at", {})
            if not visible_targets:
                self._release_deployed_combat_posture(
                    state, task, units, now
                )
                for unit in units:
                    unit_id = int(unit["object_id"])
                    if unit.get("mission_id") not in {1, 15, 29}:
                        continue
                    result = self._submit_unit_command_sync(state, unit_id, "stop")
                    if result.get("accepted"):
                        issued += 1
                    else:
                        failed += 1
                task["orders_issued"] += issued
                task["failed_orders"] += failed
                task["status"] = "waiting_for_enemy"
                return
            for unit in units:
                unit_id = int(unit["object_id"])
                if not self._aircraft_ready_for_attack(unit):
                    # Native aircraft logic returns to its reload building. Do
                    # not overwrite that state with another attack order.
                    if unit.get("mission_id") not in {1, 15, 29}:
                        self._submit_unit_command_sync(state, unit_id, "guard")
                    continue
                mission = unit.get("mission_id")
                if mission in {1, 29}:
                    continue
                last = float(last_order_at.get(unit_id, 0.0))
                if now - last < keepalive:
                    continue
                target = self._focus_attack_target([unit], visible_targets)
                if self._prepare_deployable_attack_posture(
                    state, task, unit, target, now
                ):
                    continue
                result = self._submit_unit_command_sync(
                    state,
                    unit_id,
                    "attack",
                    target_object_id=int(target["object_id"]),
                )
                if result.get("accepted"):
                    last_order_at[unit_id] = now
                    issued += 1
                else:
                    failed += 1
            task["orders_issued"] += issued
            task["failed_orders"] += failed
            task["status"] = "engaging" if any(
                unit.get("mission_id") in {1, 29} for unit in units
            ) or issued else "order_failed"
            return
        targets = self._visible_attack_targets(
            state, str(task.get("target_query", "")), True
        )
        strategic_selection_targets = list(targets)
        contact_override = False
        if task.get("strategic_director") is True:
            controls = task.get("tactical_controls")
            controls = controls if isinstance(controls, dict) else {}
            contact_radius = max(
                6, min(24, int(controls.get("engagement_distance_cells", 12)))
            )
            blockers = contact_enemies(units, targets, contact_radius)
            if blockers:
                targets = blockers
                contact_override = True
                task["contact_engagements"] = int(task.get("contact_engagements", 0)) + 1
                task["last_contact_at"] = now
                if now - float(task.get("last_contact_reassign_at", 0.0)) >= 2.0:
                    task.get("assignments", {}).clear()
                    task.get("last_order_at", {}).clear()
                    task["last_contact_reassign_at"] = now
        target_kind = str(task.get("target_kind") or "").strip().casefold()
        if target_kind and not contact_override:
            targets = [
                item for item in targets
                if str(item.get("kind") or "").casefold() == target_kind
            ]
        preferred_kind = str(
            task.get("preferred_target_kind") or ""
        ).strip().casefold()
        if preferred_kind and not contact_override:
            preferred = [
                item for item in targets
                if str(item.get("kind") or "").casefold() == preferred_kind
            ]
            if preferred:
                targets = preferred
        if task.get("strategic_director") is True and not contact_override:
            # Keep combat units in the selector's local-base view even though
            # the task is initially labelled as a building strike.
            targets = strategic_selection_targets
        if not targets:
            self._release_deployed_combat_posture(state, task, units, now)
            task["assignments"].clear()
            task["status"] = "waiting_for_enemy"
            return

        if not contact_override:
            primary_target = self._focus_attack_target(units, targets)
            priority_layer = assault_target_layer(primary_target)
            targets = [
                item for item in targets
                if assault_target_layer(item) == priority_layer
            ]
            task["target_priority_layer"] = priority_layer

        target_ids = {int(item["object_id"]) for item in targets}
        live_unit_ids = {int(unit["object_id"]) for unit in units}
        assignments = task["assignments"]
        last_order_at = task.setdefault("last_order_at", {})
        for unit_id in list(assignments):
            if unit_id not in live_unit_ids or assignments[unit_id] not in target_ids:
                assignments.pop(unit_id, None)
                last_order_at.pop(unit_id, None)
        for unit in units:
            unit_id = int(unit["object_id"])
            if assignments.get(unit_id) not in target_ids:
                continue
            mission = unit.get("mission_id")
            last = float(last_order_at.get(unit_id, 0.0))
            mission_is_attack = mission in {1, 15, 29}
            if mission_is_attack and now - last < keepalive:
                continue
            # The assigned target is still alive, but the unit is no longer in an
            # attack-like mission or has gone too long without confirmation. Reissue
            # instead of silently considering the delegated duty complete.
            assignments.pop(unit_id, None)
        issued = 0
        failed = 0
        fire_control = str(task.get("fire_control", "")).casefold()
        if contact_override:
            fire_control = "spread"
        legacy_spread = fire_control in {"spread", "auto"} or "mode" not in task
        focus_target_id = task.get("focus_target_id")
        if not legacy_spread:
            selected_focus_id = int(
                self._focus_attack_target(units, targets)["object_id"]
            )
            if focus_target_id != selected_focus_id:
                # A newly exposed defender or production building must pre-empt
                # an older construction-yard order immediately.
                assignments.clear()
                last_order_at.clear()
            focus_target_id = selected_focus_id
            task["focus_target_id"] = focus_target_id
        target_load = {target_id: 0 for target_id in target_ids}
        for target_id in assignments.values():
            if target_id in target_load:
                target_load[target_id] += 1
        for unit in units:
            unit_id = int(unit["object_id"])
            if not self._aircraft_ready_for_attack(unit):
                if unit.get("mission_id") not in {1, 15, 29}:
                    self._submit_unit_command_sync(state, unit_id, "guard")
                continue
            if assignments.get(unit_id) in target_ids:
                continue
            target = (
                min(
                    targets,
                    key=lambda item: (
                        -combat_matchup_score(unit, item) if contact_override else 0.0,
                        target_load[int(item["object_id"])],
                        self._distance_sq(unit, item),
                        int(item["object_id"]),
                    ),
                )
                if legacy_spread
                else next(
                    item for item in targets
                    if int(item["object_id"]) == focus_target_id
                )
            )
            if self._prepare_deployable_attack_posture(
                state, task, unit, target, now
            ):
                continue
            result = self._submit_unit_command_sync(
                state,
                unit_id,
                "attack",
                target_object_id=int(target["object_id"]),
            )
            if result.get("accepted") is True:
                assignments[unit_id] = int(target["object_id"])
                last_order_at[unit_id] = now
                target_load[int(target["object_id"])] += 1
                issued += 1
            else:
                failed += 1
        task["orders_issued"] += issued
        task["failed_orders"] += failed
        task["status"] = "engaging" if assignments else "order_failed"

    def _create_load_task(
        self,
        passengers: list[dict[str, Any]],
        transports: list[dict[str, Any]],
        passenger_query: str,
        transport_query: str,
    ) -> dict[str, Any]:
        passenger_ids = [int(item["object_id"]) for item in passengers]
        passenger_id_set = set(passenger_ids)
        transport_id_set = {int(item["object_id"]) for item in transports}
        loading_id_set = passenger_id_set | transport_id_set
        for attack_task in self._attack_tasks.values():
            attack_task["unit_ids"] = [
                unit_id for unit_id in attack_task.get("unit_ids", [])
                if int(unit_id) not in loading_id_set
            ]
            for unit_id in loading_id_set:
                attack_task.get("assignments", {}).pop(unit_id, None)
                attack_task.get("last_order_at", {}).pop(unit_id, None)
        now = time.monotonic()
        return {
            "task_id": uuid.uuid4().hex[:12],
            "passenger_query": passenger_query,
            "transport_query": transport_query,
            "passenger_ids": passenger_ids,
            "transport_ids": [int(item["object_id"]) for item in transports],
            "baseline_counts": {
                int(item["object_id"]): max(0, int(item.get("passenger_count", 0)))
                for item in transports
            },
            "assignments": {}, "last_order_at": {},
            "last_positions": {
                int(item["object_id"]): dict(item.get("cell", {}))
                for item in passengers if isinstance(item.get("cell"), dict)
            },
            "approach_attempts": {}, "orders_issued": 0, "failed_orders": 0,
            "loaded_count": 0, "status": "loading", "created_at": now,
            "expires_at": now + int(self._cfg.get("load_task_seconds", 21600)),
        }

    def _run_load_task(self, task: dict[str, Any], state: dict[str, Any]) -> None:
        """Issue enter orders, distributing passengers across currently free transports."""
        now = time.monotonic()
        entities = state.get("visible_entities", [])
        by_id = {
            int(item["object_id"]): item
            for item in entities
            if isinstance(item, dict) and isinstance(item.get("object_id"), int)
        }
        transport_ids = [int(value) for value in task["transport_ids"]]
        transports = [
            by_id[value]
            for value in transport_ids
            if value in by_id
            and by_id[value].get("alive") is True
            and by_id[value].get("controllable") is True
            and by_id[value].get("transport_capacity") is True
        ]
        if not transports:
            task["status"] = "no_available_transports"
            return
        baseline = task["baseline_counts"]
        observed_loaded = 0
        occupied_transport_ids: list[int] = []
        for transport in transports:
            transport_id = int(transport["object_id"])
            current_count = max(0, int(transport.get("passenger_count", 0)))
            gained = max(0, current_count - int(baseline.get(transport_id, 0)))
            observed_loaded += gained
            if gained > 0:
                occupied_transport_ids.append(transport_id)
        task["loaded_count"] = min(len(task["passenger_ids"]), observed_loaded)
        task["occupied_transport_ids"] = sorted(occupied_transport_ids)
        required_occupied = max(
            0, int(task.get("required_occupied_transports", 0) or 0)
        )
        strict_transport_count_met = (
            not task.get("require_one_passenger_per_transport")
            or len(occupied_transport_ids) >= required_occupied
        )
        if (
            task["loaded_count"] >= len(task["passenger_ids"])
            and strict_transport_count_met
        ):
            task["status"] = "completed"
            return
        live_passenger_ids = {
            int(item["object_id"])
            for item in entities
            if isinstance(item, dict)
            and item.get("relation") == "self"
            and item.get("alive") is True
            and isinstance(item.get("object_id"), int)
        }
        assignments = task["assignments"]
        last_orders = task["last_order_at"]
        last_positions = task.setdefault("last_positions", {})
        approach_attempts = task.setdefault("approach_attempts", {})
        for passenger_id in list(assignments):
            if passenger_id not in live_passenger_ids:
                assignments.pop(passenger_id, None)
                last_orders.pop(passenger_id, None)
        load_by_transport = {int(item["object_id"]): int(item.get("passenger_count", 0)) for item in transports}
        for transport_id in assignments.values():
            if transport_id in load_by_transport:
                load_by_transport[transport_id] += 1
        for passenger_id in task["passenger_ids"]:
            if passenger_id not in live_passenger_ids:
                continue
            passenger = by_id.get(passenger_id)
            if not passenger:
                continue
            if passenger_id in assignments:
                elapsed = now - float(last_orders.get(passenger_id, 0))
                if elapsed < 5.0:
                    continue
                transport = by_id.get(int(assignments[passenger_id]))
                current_cell = passenger.get("cell")
                previous_cell = last_positions.get(passenger_id)
                has_progress = isinstance(current_cell, dict) and current_cell != previous_cell
                if transport is not None and not has_progress and approach_attempts.get(passenger_id, 0) < 3:
                    transport_cell = transport.get("cell")
                    if isinstance(transport_cell, dict) and isinstance(current_cell, dict):
                        candidates = staging_cells(
                            transport_cell, current_cell, slot=approach_attempts.get(passenger_id, 0)
                        )
                        if candidates:
                            move_result = self._submit_unit_command_sync(
                                state,
                                passenger_id,
                                "move",
                                target_cell=candidates[0],
                                override_team=passenger.get("in_tactical_team") is True,
                            )
                            if move_result.get("accepted") is True:
                                approach_attempts[passenger_id] = approach_attempts.get(passenger_id, 0) + 1
                                last_positions[passenger_id] = dict(current_cell)
                                last_orders[passenger_id] = now
                                continue
                last_positions[passenger_id] = dict(current_cell) if isinstance(current_cell, dict) else previous_cell
            candidates = sorted(
                transports,
                key=lambda item: (
                    load_by_transport.get(int(item["object_id"]), 0),
                    self._distance_sq(passenger, item),
                    int(item["object_id"]),
                ),
            )
            accepted = False
            for transport in candidates:
                transport_id = int(transport["object_id"])
                # Clear an old attack/attack-move mission before boarding. This
                # prevents weapon AI from reacquiring the transport while the
                # enter mission is being path-found.
                if passenger.get("mission_id") in {1, 15, 29}:
                    self._submit_unit_command_sync(state, passenger_id, "stop")
                result = self._submit_unit_command_sync(
                    state,
                    passenger_id,
                    "enter",
                    target_object_id=transport_id,
                )
                if result.get("accepted") is True:
                    assignments[passenger_id] = transport_id
                    last_orders[passenger_id] = now
                    load_by_transport[transport_id] = load_by_transport.get(transport_id, 0) + 1
                    task["orders_issued"] += 1
                    accepted = True
                    break
            if not accepted:
                task["failed_orders"] += 1
        task["status"] = (
            "completed"
            if task["loaded_count"] >= len(task["passenger_ids"])
            and strict_transport_count_met
            else "loading"
        )

    def _run_force_maintenance_task(
        self, task: dict[str, Any], state: dict[str, Any]
    ) -> None:
        if task.get("status") == "cancelled":
            return
        previous_status = str(task.get("status"))
        production = state.get("production", {})
        owned_counts = production.get("owned_counts") if isinstance(production, dict) else None
        if not isinstance(owned_counts, dict):
            task["status"] = "telemetry_upgrade_required"
            task["last_error"] = "MO 端缺少按类型真实持有数量遥测"
            return
        type_ids = {str(value).upper() for value in task.get("type_ids", [])}
        owned = sum(max(0, int(owned_counts.get(type_id, 0))) for type_id in type_ids)
        queue = production.get("queue", []) if isinstance(production, dict) else []
        queued = sum(
            max(0, int(item.get("queued_count", 0)))
            for item in queue if isinstance(item, dict)
            and str(item.get("type_id", "")).upper() in type_ids
        )
        target = max(0, int(task.get("target_count", 0)))
        deficit = max(0, target - owned - queued)
        previous_owned = task.get("last_owned_count")
        task.update({
            "owned_count": owned, "queued_count": queued,
            "deficit": deficit, "last_owned_count": owned,
        })
        if deficit <= 0:
            task["status"] = "maintaining"
            task["last_error"] = None
            if owned >= target:
                task["target_reached_once"] = True
                if previous_status == "replenishing":
                    task["last_replenishment_completed_cycle"] = int(
                        task.get("replenishment_cycle", 0)
                    )
            return
        task["status"] = "replenishing" if task.get("target_reached_once") else "building_up"
        if task.get("target_reached_once") and previous_status == "maintaining":
            task["replenishment_cycle"] = int(task.get("replenishment_cycle", 0)) + 1
        if isinstance(previous_owned, int) and owned < previous_owned:
            task["last_loss_count"] = previous_owned - owned
        now = time.monotonic()
        if now - float(task.get("last_submit_at", 0.0)) < 5.0:
            return
        sidebar = production.get("sidebar_available", []) if isinstance(production, dict) else []
        available = {
            str(item.get("type_id", "")).upper(): item
            for item in sidebar if isinstance(item, dict) and item.get("type_id")
        }
        selected_type = next((type_id for type_id in task.get("type_ids", []) if type_id in available), None)
        if not selected_type:
            task["status"] = "waiting_for_unlock"
            task["last_error"] = "指定兵种当前不在实时建造栏中"
            return
        profile = unit_profile(selected_type) or {}
        cost = max(0, int(profile.get("cost") or 0))
        budget = self._economic_budget(
            state, reserve_override=task.get("credit_reserve")
        )
        task["economic_budget"] = budget.public()
        batch_limit = max(1, int(self._economic_personality.batch_limit))
        quantity = budget.affordable_quantity(cost, min(deficit, batch_limit))
        if quantity <= 0:
            task["status"] = "waiting_for_funds"
            task["last_error"] = None
            return
        try:
            result = self._client.command_production({
                "request_id": str(uuid.uuid4()),
                "type_id": selected_type, "quantity": quantity,
            })
        except (BridgeError, TypeError, ValueError) as exc:
            task["last_submit_at"] = now
            task["last_error"] = str(exc)
            task["status"] = "retrying"
            return
        accepted = max(0, int(result.get("accepted_count", 0)))
        task["last_submit_at"] = now
        task["orders_issued"] = int(task.get("orders_issued", 0)) + (1 if accepted else 0)
        task["produced_requested"] = int(task.get("produced_requested", 0)) + accepted
        if accepted:
            task["economic_reserved_cost"] = cost * accepted
            task["economic_reserved_until"] = now + 3.0
        if not accepted:
            task["status"] = "retrying"
            task["last_error"] = str(result.get("last_error") or result.get("error") or "生产事件未接受")
        else:
            task["status"] = "replenishing" if task.get("target_reached_once") else "building_up"
            task["last_error"] = None

    def _run_defense_task(self, task: dict[str, Any], state: dict[str, Any]) -> None:
        if task.get("status") == "cancelled":
            return
        anchor = task.get("anchor")
        if not isinstance(anchor, dict):
            task["status"] = "failed"
            task["last_error"] = "防区锚点丢失"
            return
        all_matching = select_owned_units(state, str(task.get("query") or ""))
        by_id = {int(item["object_id"]): item for item in all_matching}
        previous_ids = {int(value) for value in task.get("unit_ids", [])}
        live_ids = previous_ids & set(by_id)
        lost = previous_ids - live_ids
        if lost:
            task["losses"] = int(task.get("losses", 0)) + len(lost)
        reserved_elsewhere = {
            int(value)
            for other in self._defense_tasks.values()
            if other is not task and other.get("status") != "cancelled"
            for value in other.get("unit_ids", [])
        }
        desired = max(1, int(task.get("desired_count", 1)))
        recruits = sorted(
            (
                item for item in all_matching
                if int(item["object_id"]) not in live_ids
                and int(item["object_id"]) not in reserved_elsewhere
            ),
            key=lambda item: (
                self._distance_sq(item, {"cell": anchor}), int(item["object_id"])
            ),
        )[:max(0, desired - len(live_ids))]
        if recruits:
            live_ids.update(int(item["object_id"]) for item in recruits)
            task["reinforcements"] = int(task.get("reinforcements", 0)) + len(recruits)
        task["unit_ids"] = sorted(live_ids)
        units = [by_id[value] for value in sorted(live_ids) if value in by_id]
        if not units:
            task["status"] = "waiting_for_reinforcements"
            return
        enemies = [
            item for item in self._visible_attack_targets(state, "", True)
            if self._distance_sq({"cell": anchor}, item)
            <= int(task.get("pursuit_radius", 24)) ** 2
        ]
        now = time.monotonic()
        last_orders = task.setdefault("last_order_at", {})
        issued = 0
        for slot, unit in enumerate(units):
            unit_id = int(unit["object_id"])
            elapsed = now - float(last_orders.get(unit_id, 0.0))
            if enemies:
                target = min(enemies, key=lambda item: (
                    self._distance_sq(unit, item), int(item["object_id"])
                ))
                if unit.get("mission_id") in {1, 15, 29} and elapsed < 8.0:
                    continue
                result = self._submit_unit_command_sync(
                    state, unit_id, "attack", target_object_id=int(target["object_id"])
                )
                if result.get("accepted") is True:
                    issued += 1
                    last_orders[unit_id] = now
                continue
            outside = self._distance_sq(unit, {"cell": anchor}) > int(task.get("guard_radius", 10)) ** 2
            if outside:
                if elapsed < 5.0:
                    continue
                candidates = staging_cells(anchor, unit.get("cell"), slot=slot)
                if not candidates:
                    continue
                result = self._submit_unit_command_sync(
                    state, unit_id, "move", target_cell=candidates[0]
                )
            else:
                if elapsed < 10.0:
                    continue
                result = self._submit_unit_command_sync(state, unit_id, "guard")
            if result.get("accepted") is True:
                issued += 1
                last_orders[unit_id] = now
        task["orders_issued"] = int(task.get("orders_issued", 0)) + issued
        task["status"] = "engaging" if enemies else (
            "waiting_for_reinforcements" if len(units) < desired else "guarding"
        )

    @staticmethod
    def _public_force_task(task: dict[str, Any]) -> dict[str, Any]:
        return {key: task.get(key) for key in (
            "task_id", "kind", "query", "display_name", "target_count",
            "owned_count", "queued_count", "deficit", "status", "orders_issued",
            "produced_requested", "target_reached_once", "economic_budget", "last_error",
        )}

    @staticmethod
    def _public_defense_task(task: dict[str, Any]) -> dict[str, Any]:
        return {
            "task_id": task.get("task_id"), "kind": task.get("kind"),
            "query": task.get("query"), "destination": task.get("destination"),
            "desired_count": task.get("desired_count"),
            "assigned_count": len(task.get("unit_ids", [])),
            "guard_radius": task.get("guard_radius"),
            "pursuit_radius": task.get("pursuit_radius"),
            "status": task.get("status"), "losses": task.get("losses", 0),
            "reinforcements": task.get("reinforcements", 0),
            "orders_issued": task.get("orders_issued", 0),
            "maintain_task_id": task.get("maintain_task_id"),
        }

    def _attempt_building_placement(
        self, task: dict[str, Any], state: dict[str, Any]
    ) -> bool:
        now = time.monotonic()
        if now - float(task.get("last_placement_order_at", 0.0)) < 2.0:
            return task.get("status") == "placing"
        type_id = str(task["type_id"]).upper()
        profile = unit_profile(type_id) or {"type_id": type_id, "building_class": "other"}
        entities = [
            item for item in state.get("visible_entities", [])
            if isinstance(item, dict) and item.get("relation") == "self"
            and item.get("alive") is True and isinstance(item.get("cell"), dict)
        ]
        buildings = [item for item in entities if item.get("kind") == "building"]
        parent_type_id = str(profile.get("powers_up_building") or "").strip().upper()
        if parent_type_id:
            parent_buildings = [
                item for item in buildings
                if str(item.get("type_id", "")).upper() == parent_type_id
            ]
            if not parent_buildings:
                task["placement_error"] = (
                    f"等待前置建筑 {parent_type_id}，该组件不能独立放置"
                )
                task["status"] = "waiting_for_parent_building"
                return False
            # Plug buildings are placed on the host foundation itself. Stable
            # ordering also lets us try another host when one is upgraded.
            anchor_entity = sorted(
                parent_buildings, key=lambda item: int(item.get("object_id", 0))
            )
        else:
            anchor_entity = None
        yards = [
            item for item in buildings
            if profile_class(unit_profile(str(item.get("type_id", ""))) or {}) == "construction_yard"
            or str(item.get("type_id", "")).upper() in {"GACNST", "NACNST", "YACNST", "FACNST"}
        ]
        preferred_anchor_id = task.get("placement_anchor_object_id")
        if parent_type_id == "" and isinstance(preferred_anchor_id, int):
            preferred = [
                item for item in yards
                if int(item.get("object_id", 0)) == preferred_anchor_id
            ]
            if preferred:
                anchor_entity = preferred
        anchor_entity = anchor_entity or (yards or buildings or entities)
        if not anchor_entity:
            task["placement_error"] = "没有可用于确定建造范围的己方基地锚点"
            return False
        epoch = int(task.get("placement_epoch", 0))
        anchor = dict(anchor_entity[epoch % len(anchor_entity)]["cell"])
        upgrade_state = tuple(
            sorted(
                (
                    int(item.get("object_id", 0)),
                    int(item.get("upgrade_occupied_count", -1)),
                )
                for item in anchor_entity
            )
        ) if parent_type_id else ()
        cache_key = (anchor["x"], anchor["y"], epoch, parent_type_id, upgrade_state)
        if task.get("placement_cache_key") != cache_key:
            ore_cells = state.get("ore_cells", [])
            enemies = [
                item["cell"] for item in state.get("visible_entities", [])
                if isinstance(item, dict) and item.get("relation") == "enemy"
                and isinstance(item.get("cell"), dict)
            ]
            if parent_type_id:
                owner = self._base_tasks.get(str(task.get("owner_base_task_id") or ""), {})
                used_hosts = {
                    int(value)
                    for value in owner.get("used_upgrade_hosts", {}).get(type_id, [])
                    if isinstance(value, int)
                }
                task["placement_candidates"] = upgrade_host_candidates(
                    anchor_entity, parent_type_id, used_hosts
                )
            else:
                task["placement_candidates"] = placement_candidates(
                    anchor,
                    purpose=profile_class(profile),
                    ore_cells=ore_cells if isinstance(ore_cells, list) else [],
                    enemy_cells=enemies,
                    occupied_cells=[item["cell"] for item in buildings],
                    limit=512,
                )
            task["placement_cursor"] = 0
            task["placement_cache_key"] = cache_key
        candidates = task.get("placement_candidates", [])
        cursor = int(task.get("placement_cursor", 0))
        attempts_this_tick = 0
        while cursor < len(candidates) and attempts_this_tick < 16:
            cell = candidates[cursor]
            cursor += 1
            attempts_this_tick += 1
            task["placement_attempts"] = int(task.get("placement_attempts", 0)) + 1
            try:
                result = self._client.command_building_placement({
                    "request_id": str(uuid.uuid4()),
                    "type_id": type_id,
                    "target_cell": {"x": int(cell["x"]), "y": int(cell["y"])},
                    "target_object_id": int(cell.get("target_object_id", 0)),
                })
            except (AttributeError, BridgeError, TypeError, ValueError) as exc:
                task["placement_error"] = str(exc)
                self._append_diagnostic_log("building_placement.jsonl", {
                    "event": "placement_submit",
                    "task_id": task.get("task_id"),
                    "type_id": type_id,
                    "parent_type_id": parent_type_id or None,
                    "target_cell": {"x": int(cell["x"]), "y": int(cell["y"])},
                    "target_object_id": int(cell.get("target_object_id", 0)),
                    "attempt": int(task.get("placement_attempts", 0)),
                    "accepted": False,
                    "exception": str(exc),
                })
                break
            host = next(
                (
                    item for item in anchor_entity
                    if int(item.get("object_id", 0))
                    == int(cell.get("target_object_id", 0))
                ),
                None,
            )
            self._append_diagnostic_log("building_placement.jsonl", {
                "event": "placement_submit",
                "task_id": task.get("task_id"),
                "type_id": type_id,
                "parent_type_id": parent_type_id or None,
                "target_cell": {"x": int(cell["x"]), "y": int(cell["y"])},
                "target_object_id": int(cell.get("target_object_id", 0)),
                "host_upgrade_occupied_count": (
                    host.get("upgrade_occupied_count")
                    if isinstance(host, dict) else None
                ),
                "attempt": int(task.get("placement_attempts", 0)),
                "accepted": result.get("accepted") is True,
                "native_status": result.get("status"),
                "matched_count": result.get("matched_count"),
                "accepted_count": result.get("accepted_count"),
                "failed_count": result.get("failed_count"),
                "last_error": result.get("last_error") or result.get("error"),
            })
            if result.get("accepted") is True:
                task["last_placement_order_at"] = now
                task["placement_cell"] = dict(cell)
                task["placement_host_object_id"] = int(cell.get("target_object_id", 0)) or None
                task["placement_accepted_at"] = now
                task["placement_error"] = None
                task["status"] = "placing"
                task["placement_cursor"] = cursor
                return True
            task["placement_error"] = str(
                result.get("last_error") or result.get("error") or "候选格被原生建造规则拒绝"
            )
        task["placement_cursor"] = cursor
        if cursor >= len(candidates):
            task["placement_epoch"] = epoch + 1
            task.pop("placement_cache_key", None)
        if not candidates:
            self._append_diagnostic_log("building_placement.jsonl", {
                "event": "no_placement_candidate",
                "task_id": task.get("task_id"),
                "type_id": type_id,
                "parent_type_id": parent_type_id or None,
                "host_count": len(anchor_entity),
                "placement_epoch": epoch,
            })
        return False

    def _deduplicate_base_development_tasks(
        self,
    ) -> dict[str, Any] | None:
        """Keep one persistent base controller and retire duplicate child tasks."""
        terminal = {"cancelled", "failed", "completed", "expired"}
        for task in self._base_tasks.values():
            if (
                task.get("status") not in terminal
                and task.get("commander_authorized") is not True
            ):
                task["status"] = "cancelled"
                task["last_error"] = "base development requires explicit commander authorization"
                self.logger.info(
                    "Mental Omega base-development task cancelled: explicit authorization missing"
                )
        return deduplicate_base_tasks(self._base_tasks, self._production_tasks)

    def _ore_expansion_candidate(
        self,
        state: dict[str, Any],
        yards: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        ore_cells = [
            item for item in state.get("ore_cells", [])
            if isinstance(item, dict)
            and isinstance(item.get("x"), int)
            and isinstance(item.get("y"), int)
            and int(item.get("value") or 0) > 0
        ]
        yard_cells = [item["cell"] for item in yards if isinstance(item.get("cell"), dict)]
        if not ore_cells or not yard_cells:
            return None
        enemies = [
            item["cell"] for item in state.get("visible_entities", [])
            if isinstance(item, dict)
            and item.get("relation") == "enemy"
            and item.get("alive") is True
            and item.get("visible") is True
            and isinstance(item.get("cell"), dict)
        ]
        candidates: list[tuple[float, dict[str, Any]]] = []
        for seed in ore_cells:
            nearest_yard = min(
                yard_cells,
                key=lambda cell: self._distance_sq({"cell": seed}, {"cell": cell}),
            )
            yard_distance_sq = self._distance_sq(
                {"cell": seed}, {"cell": nearest_yard}
            )
            if yard_distance_sq < 18 ** 2:
                continue
            cluster = [
                cell for cell in ore_cells
                if (int(cell["x"]) - int(seed["x"])) ** 2
                + (int(cell["y"]) - int(seed["y"])) ** 2 <= 9 ** 2
            ]
            ore_value = sum(max(0, int(cell.get("value") or 0)) for cell in cluster)
            if ore_value < 3000:
                continue
            enemy_distance_sq = min(
                (
                    (int(cell["x"]) - int(seed["x"])) ** 2
                    + (int(cell["y"]) - int(seed["y"])) ** 2
                    for cell in enemies
                ),
                default=10 ** 9,
            )
            if enemy_distance_sq < 20 ** 2:
                continue
            distance = max(1.0, yard_distance_sq ** 0.5)
            dx = int(nearest_yard["x"]) - int(seed["x"])
            dy = int(nearest_yard["y"]) - int(seed["y"])
            target = {
                "x": int(round(int(seed["x"]) + dx / distance * 7.0)),
                "y": int(round(int(seed["y"]) + dy / distance * 7.0)),
            }
            score = float(ore_value) + min(enemy_distance_sq ** 0.5, 80.0) * 20.0
            score -= distance * 12.0
            candidates.append((score, {
                "ore_cell": {"x": int(seed["x"]), "y": int(seed["y"])},
                "target_cell": target,
                "ore_value": ore_value,
                "distance_from_base": round(distance, 1),
                "nearest_enemy_distance": round(enemy_distance_sq ** 0.5, 1),
                "score": round(score, 1),
            }))
        return max(candidates, key=lambda item: item[0])[1] if candidates else None

    def _expansion_is_safe(
        self, state: dict[str, Any], target: dict[str, int]
    ) -> tuple[bool, str]:
        if any(
            isinstance(alert, dict)
            and alert.get("kind") == "under_attack"
            and int(alert.get("severity") or 0) >= 2
            for alert in state.get("alerts", [])
        ):
            return False, "基地正在承受高强度攻击"
        local_enemies = [
            item for item in state.get("visible_entities", [])
            if isinstance(item, dict)
            and item.get("relation") == "enemy"
            and item.get("alive") is True
            and item.get("visible") is True
            and isinstance(item.get("cell"), dict)
            and self._distance_sq(item, {"cell": target}) <= 20 ** 2
        ]
        if local_enemies:
            return False, f"目标矿区附近有 {len(local_enemies)} 个可见敌军"
        assessment = self._strategic_director.last_assessment
        if assessment is not None:
            minimum = max(800.0, float(assessment.enemy_strength) * 1.15)
            if float(assessment.own_strength) < minimum:
                return False, (
                    f"我方有效兵力 {assessment.own_strength:.0f} 不足以保护扩张，"
                    f"当前需要至少 {minimum:.0f}"
                )
        return True, "资金、兵力与矿区安全条件均满足"

    def _new_base_production_task(
        self,
        owner: dict[str, Any],
        state: dict[str, Any],
        profile: dict[str, Any],
        accepted: int,
        now: float,
        *,
        placement_anchor_object_id: int | None = None,
    ) -> str:
        type_id = str(profile["type_id"]).upper()
        baseline_ids = {
            int(item["object_id"]) for item in state.get("visible_entities", [])
            if isinstance(item, dict)
            and str(item.get("type_id", "")).upper() == type_id
            and isinstance(item.get("object_id"), int)
        }
        task_id = uuid.uuid4().hex[:12]
        child = {
            "task_id": task_id,
            "product": str(profile.get("display_name") or type_id),
            "display_name": str(profile.get("display_name") or type_id),
            "type_id": type_id,
            "kind": str(profile.get("kind") or "unknown"),
            "requested_count": accepted,
            "submitted_count": accepted,
            "baseline_ids": baseline_ids,
            "observed_ids": set(),
            "queue_count": 0,
            "queue_progress_ratio": 0.0,
            "queue_on_hold": False,
            "last_submit_at": now,
            "submit_attempts": 1,
            "status": "queued",
            "created_at": now,
            "expires_at": now + int(self._cfg.get("production_task_seconds", 21600)),
            "owner_base_task_id": owner["task_id"],
            "economic_reserved_cost": max(0, int(profile.get("cost") or 0)) * accepted,
            "economic_reserved_until": now + 3.0,
        }
        if placement_anchor_object_id is not None:
            child["placement_anchor_object_id"] = int(placement_anchor_object_id)
        self._production_tasks[task_id] = child
        lane = production_lane(profile)
        owner.setdefault("active_production_task_ids", {})[lane] = task_id
        committed = owner.setdefault("committed_counts", {})
        committed[type_id] = int(committed.get(type_id, 0)) + accepted
        return task_id

    def _run_resource_expansion(
        self,
        task: dict[str, Any],
        state: dict[str, Any],
        available_profiles: list[dict[str, Any]],
        entities: list[dict[str, Any]],
        owned_counts: dict[str, int],
        budget: EconomicBudget,
        busy_lanes: set[str],
    ) -> bool:
        expansion = task.setdefault("resource_expansion", {
            "status": "monitoring",
            "attempts": 0,
            "completed_count": 0,
        })
        now = time.monotonic()
        phase = str(expansion.get("status") or "monitoring")
        yards = [
            item for item in entities
            if str(item.get("type_id", "")).upper()
            in {"GACNST", "NACNST", "YACNST", "FACNST"}
            and isinstance(item.get("cell"), dict)
        ]
        home_ore = state.get("economy", {}).get("home_ore_value")
        expansion["home_ore_value"] = home_ore

        if phase == "completed":
            if (
                now - float(expansion.get("completed_at", 0.0)) < 60.0
                or not isinstance(home_ore, int)
                or home_ore > 2500
            ):
                return False
            expansion.update({"status": "monitoring", "target": None})
            phase = "monitoring"

        if phase in {"monitoring", "waiting_for_conditions"}:
            if not has_established_technology_center(entities):
                expansion.update({"status": "waiting_for_conditions", "reason": "科技中心尚未完成"})
                return False
            if not isinstance(home_ore, int) or home_ore > 2500:
                expansion.update({"status": "monitoring", "reason": "主基地矿量仍充足"})
                return False
            candidate = self._ore_expansion_candidate(state, yards)
            if candidate is None:
                expansion.update({"status": "waiting_for_conditions", "reason": "尚未发现足量且远离基地的安全矿区"})
                return False
            safe, reason = self._expansion_is_safe(state, candidate["target_cell"])
            occupied = [
                item["cell"] for item in entities
                if isinstance(item.get("cell"), dict)
            ]
            deploy_candidates = [dict(candidate["target_cell"])]
            deploy_candidates.extend(
                placement_candidates(
                    candidate["ore_cell"],
                    purpose="resource",
                    ore_cells=[candidate["ore_cell"]],
                    occupied_cells=occupied,
                    limit=96,
                )
            )
            deploy_candidates = list({
                (int(cell["x"]), int(cell["y"])): {
                    "x": int(cell["x"]), "y": int(cell["y"])
                }
                for cell in deploy_candidates
            }.values())
            expansion.update({
                "target": candidate,
                "deploy_candidates": deploy_candidates,
                "deploy_cursor": 0,
                "active_deploy_cell": dict(deploy_candidates[0]),
                "deploy_attempts": 0,
                "reason": reason,
            })
            if not safe:
                expansion["status"] = "waiting_for_conditions"
                return False
            mcv = next(
                (
                    profile for profile in available_profiles
                    if str(profile.get("type_id") or "").upper() == "AMCV"
                ),
                None,
            )
            refinery = next(
                (
                    profile for profile in available_profiles
                    if str(profile.get("type_id") or "").upper() == "GAREFN"
                ),
                unit_profile("GAREFN"),
            )
            if mcv is None or "vehicle" in busy_lanes:
                expansion.update({"status": "waiting_for_conditions", "reason": "机动建设车产线尚未空闲或未解锁"})
                return False
            combined_cost = max(0, int(mcv.get("cost") or 0)) + max(
                0, int((refinery or {}).get("cost") or 0)
            )
            expansion["required_investment"] = combined_cost
            if budget.spendable < combined_cost:
                expansion.update({"status": "waiting_for_conditions", "reason": f"扩矿需预留 {combined_cost}，当前可支配 {budget.spendable}"})
                return False
            result = self._client.command_production({
                "request_id": str(uuid.uuid4()), "type_id": "AMCV", "quantity": 1,
            })
            accepted = max(0, int(result.get("accepted_count", 0)))
            if accepted <= 0:
                expansion.update({"status": "waiting_for_conditions", "reason": "机动建设车生产未被接受"})
                return False
            child_id = self._new_base_production_task(task, state, mcv, accepted, now)
            expansion.update({
                "status": "producing_mcv",
                "mcv_production_task_id": child_id,
                "mcv_baseline_ids": sorted(
                    int(item["object_id"]) for item in entities
                    if str(item.get("type_id", "")).upper() == "AMCV"
                    and isinstance(item.get("object_id"), int)
                ),
                "attempts": int(expansion.get("attempts", 0)) + 1,
                "last_action_at": now,
            })
            task["last_reason"] = "主矿衰减，生产机动建设车准备建立远端矿区"
            return True

        target = expansion.get("active_deploy_cell")
        if not isinstance(target, dict):
            target = expansion.get("target", {}).get("target_cell")
        if not isinstance(target, dict):
            expansion.update({"status": "monitoring", "reason": "扩矿目标丢失，重新评估"})
            return False
        safe, reason = self._expansion_is_safe(state, target)
        expansion["reason"] = reason
        mcv_id = expansion.get("mcv_object_id")
        mcvs = [
            item for item in entities
            if str(item.get("type_id", "")).upper() == "AMCV"
            and isinstance(item.get("object_id"), int)
        ]
        if not isinstance(mcv_id, int):
            baseline = {int(value) for value in expansion.get("mcv_baseline_ids", [])}
            fresh = [item for item in mcvs if int(item["object_id"]) not in baseline]
            if fresh:
                mcv_id = int(fresh[0]["object_id"])
                expansion["mcv_object_id"] = mcv_id
                expansion["status"] = "moving_mcv"
                phase = "moving_mcv"
            elif phase == "producing_mcv":
                child = self._production_tasks.get(
                    str(expansion.get("mcv_production_task_id") or "")
                )
                if child and child.get("status") in {
                    "failed", "cancelled", "expired"
                }:
                    expansion.update({
                        "status": "waiting_for_conditions",
                        "reason": "MCV production failed; expansion will be retried",
                        "mcv_production_task_id": None,
                    })
                return False
        mcv_entity = next((item for item in mcvs if int(item["object_id"]) == mcv_id), None)
        if (
            phase in {"moving_mcv", "retreating_mcv"}
            and isinstance(mcv_id, int)
            and mcv_entity is None
        ):
            expansion.update({
                "status": "waiting_for_conditions",
                "mcv_object_id": None,
                "reason": "扩矿机动建设车已损失，重新评估兵力与资金",
            })
            return False

        if phase in {"moving_mcv", "retreating_mcv"} and mcv_entity is not None:
            if not safe:
                home = min(yards, key=lambda item: self._distance_sq(mcv_entity, item))
                if now - float(expansion.get("last_action_at", 0.0)) >= 5.0:
                    self._submit_unit_command_sync(
                        state, int(mcv_id), "move", target_cell=dict(home["cell"])
                    )
                    expansion.update({"status": "retreating_mcv", "last_action_at": now})
                return False
            distance_sq = self._distance_sq(mcv_entity, {"cell": target})
            if distance_sq <= 4 ** 2:
                arrived_at = expansion.get("arrived_at")
                if not isinstance(arrived_at, (int, float)):
                    self._submit_unit_command_sync(state, int(mcv_id), "stop")
                    expansion.update({"arrived_at": now, "last_action_at": now})
                    return False
                if now - float(arrived_at) < 1.0:
                    return False
                result = self._submit_unit_command_sync(state, int(mcv_id), "deploy")
                if result.get("accepted") is True:
                    expansion.update({
                        "status": "deploying_mcv",
                        "last_action_at": now,
                        "deploy_submitted_at": now,
                        "deploy_attempts": int(expansion.get("deploy_attempts", 0)) + 1,
                    })
                else:
                    candidates = expansion.get("deploy_candidates", [])
                    if candidates:
                        cursor = (int(expansion.get("deploy_cursor", 0)) + 1) % len(candidates)
                        expansion.update({
                            "deploy_cursor": cursor,
                            "active_deploy_cell": dict(candidates[cursor]),
                            "status": "moving_mcv",
                            "last_action_at": now,
                            "arrived_at": None,
                            "reason": "deploy rejected; trying the next ore-side site",
                        })
                return False
            if now - float(expansion.get("last_action_at", 0.0)) >= 5.0:
                expansion["arrived_at"] = None
                result = self._submit_unit_command_sync(
                    state, int(mcv_id), "move", target_cell=dict(target)
                )
                if result.get("accepted") is True:
                    expansion.update({"status": "moving_mcv", "last_action_at": now})
            return False

        if phase in {"deploying_mcv", "waiting_for_refinery"}:
            new_yards = [
                item for item in yards
                if self._distance_sq(item, {"cell": target}) <= 8 ** 2
            ]
            if not new_yards:
                elapsed = now - float(expansion.get("deploy_submitted_at", expansion.get("last_action_at", 0.0)))
                if mcv_entity is not None and elapsed >= 8.0:
                    candidates = expansion.get("deploy_candidates", [])
                    if candidates:
                        cursor = (int(expansion.get("deploy_cursor", 0)) + 1) % len(candidates)
                        expansion.update({
                            "deploy_cursor": cursor,
                            "active_deploy_cell": dict(candidates[cursor]),
                            "status": "moving_mcv",
                            "last_action_at": 0.0,
                            "arrived_at": None,
                            "reason": "deployment was not confirmed; relocating and retrying",
                        })
                elif mcv_entity is None and elapsed >= 20.0:
                    expansion.update({
                        "status": "waiting_for_conditions",
                        "mcv_object_id": None,
                        "mcv_production_task_id": None,
                        "reason": "neither MCV nor new yard was confirmed; expansion reset",
                    })
                return False
            new_yard = min(new_yards, key=lambda item: self._distance_sq(item, {"cell": target}))
            expansion["construction_yard_object_id"] = int(new_yard["object_id"])
            expansion["status"] = "waiting_for_refinery"
            if "building" in busy_lanes:
                return False
            refinery = next(
                (
                    profile for profile in available_profiles
                    if str(profile.get("type_id") or "").upper() == "GAREFN"
                ),
                None,
            )
            if refinery is None:
                expansion["reason"] = "等待新基地矿场建造项解锁"
                return False
            refinery_cost = max(0, int(refinery.get("cost") or 0))
            if not budget.can_spend(refinery_cost):
                expansion["reason"] = f"等待矿场资金，可支配 {budget.spendable}"
                return False
            result = self._client.command_production({
                "request_id": str(uuid.uuid4()), "type_id": "GAREFN", "quantity": 1,
            })
            accepted = max(0, int(result.get("accepted_count", 0)))
            if accepted <= 0:
                expansion["reason"] = "远端矿场生产未被接受"
                return False
            child_id = self._new_base_production_task(
                task,
                state,
                refinery,
                accepted,
                now,
                placement_anchor_object_id=int(new_yard["object_id"]),
            )
            expansion.update({"status": "building_refinery", "refinery_production_task_id": child_id, "last_action_at": now})
            task["last_reason"] = "新机动基地已展开，在远端矿区建设矿场"
            return True

        if phase == "building_refinery":
            child = self._production_tasks.get(str(expansion.get("refinery_production_task_id") or ""))
            if child and child.get("status") == "completed":
                expansion.update({
                    "status": "completed",
                    "completed_at": now,
                    "completed_count": int(expansion.get("completed_count", 0)) + 1,
                    "reason": "远端建造场与矿场均已确认完成",
                })
            elif child and child.get("status") in {"failed", "cancelled", "expired"}:
                expansion.update({"status": "waiting_for_refinery", "reason": "矿场任务失败，等待重试"})
            return False
        return False

    def _run_base_development_task(
        self, task: dict[str, Any], state: dict[str, Any]
    ) -> None:
        if task.get("status") in {"cancelled", "failed", "completed"}:
            return
        now = time.monotonic()
        active_ids = task.setdefault("active_production_task_ids", {})
        legacy_id = str(task.get("active_production_task_id") or "")
        if legacy_id and legacy_id not in active_ids.values():
            legacy_task = self._production_tasks.get(legacy_id) or {}
            legacy_profile = unit_profile(str(legacy_task.get("type_id") or "")) or legacy_task
            active_ids[production_lane(legacy_profile)] = legacy_id
        task["active_production_task_id"] = None
        busy_lanes: set[str] = set()
        finished_lane = False
        for lane, active_id_value in list(active_ids.items()):
            active_id = str(active_id_value or "")
            active = self._production_tasks.get(active_id)
            if active and active.get("status") not in {"completed", "cancelled", "expired", "failed"}:
                busy_lanes.add(str(lane))
                task["placement_candidates_tested"] = max(
                    int(task.get("placement_candidates_tested", 0)),
                    int(active.get("placement_attempts", 0)),
                )
                continue
            if active:
                type_id = str(active.get("type_id", "")).upper()
                quantity = int(active.get("requested_count", 0))
                committed = task.setdefault("committed_counts", {})
                committed[type_id] = max(0, int(committed.get(type_id, 0)) - quantity)
                if active.get("status") == "completed":
                    placed = task.setdefault("placed_counts", {})
                    placed[type_id] = int(placed.get(type_id, 0)) + quantity
                    host_id = active.get("placement_host_object_id")
                    profile = unit_profile(type_id) or {}
                    if profile.get("powers_up_building") and isinstance(host_id, int):
                        used = task.setdefault("used_upgrade_hosts", {}).setdefault(type_id, [])
                        if host_id not in used:
                            used.append(host_id)
            active_ids.pop(lane, None)
            finished_lane = True
        if finished_lane:
            task["last_action_at"] = now

        entities = [
            item for item in state.get("visible_entities", [])
            if isinstance(item, dict) and item.get("relation") == "self"
            and item.get("alive") is True
        ]
        yards = [
            item for item in entities
            if str(item.get("type_id", "")).upper() in {"GACNST", "NACNST", "YACNST", "FACNST"}
        ]
        if not yards:
            mcvs = []
            for item in entities:
                profile = unit_profile(str(item.get("type_id", ""))) or {}
                name = str(item.get("display_name", ""))
                if item.get("controllable") is True and (
                    profile.get("deploys_into") or "建设车" in name or "基地车" in name
                ):
                    mcvs.append(item)
            if not mcvs:
                task["status"] = "waiting_for_construction_yard"
                task["last_reason"] = "等待基地车或建造场"
                return
            if now - float(task.get("last_action_at", 0.0)) >= 4.0:
                result = self._submit_unit_command_sync(
                    state, int(mcvs[0]["object_id"]), "deploy"
                )
                task["last_action_at"] = now
                task["decisions"] += 1
                task["status"] = "deploying" if result.get("accepted") is True else "waiting_to_deploy"
                task["last_reason"] = "展开机动建设车"
            return

        production = state.get("production", {})
        sidebar = production.get("sidebar_available", []) if isinstance(production, dict) else []
        available_profiles = []
        for item in sidebar if isinstance(sidebar, list) else []:
            if not isinstance(item, dict) or not item.get("type_id"):
                continue
            type_id = str(item["type_id"]).upper()
            profile = unit_profile(type_id) or {
                "type_id": type_id, "display_name": type_id,
                "kind": item.get("kind", "unknown"), "tech_level": 0,
            }
            available_profiles.append(profile)
        if not available_profiles:
            task["status"] = "waiting_for_sidebar"
            task["last_reason"] = str(production.get("reason") or "等待建造栏解锁")
            return
        lane_last_action = task.setdefault("lane_last_action_at", {})
        cooling_lanes = cooling_production_lanes(lane_last_action, now)
        available_profiles = profiles_for_idle_lanes(
            available_profiles, busy_lanes | cooling_lanes
        )
        available_profiles = filter_upgrade_profiles(
            available_profiles,
            entities,
            task.setdefault("committed_counts", {}),
            task.get("used_upgrade_hosts", {}),
        )
        if not available_profiles:
            task["status"] = "producing_parallel"
            task["last_reason"] = "all currently available production lanes are busy"
            return
        owned_counts: dict[str, int] = {}
        for item in entities:
            type_id = str(item.get("type_id", "")).upper()
            owned_counts[type_id] = owned_counts.get(type_id, 0) + 1
        native_owned = production.get("owned_counts") if isinstance(production, dict) else {}
        if isinstance(native_owned, dict):
            for type_id, count in native_owned.items():
                owned_counts[str(type_id).upper()] = max(
                    owned_counts.get(str(type_id).upper(), 0), int(count)
                )
        for type_id, count in task.get("placed_counts", {}).items():
            # Attached upgrades have no independent map object, so accepted and
            # completed placements are the authoritative owned count for them.
            owned_counts[str(type_id).upper()] = max(
                owned_counts.get(str(type_id).upper(), 0), int(count)
            )
        self._run_us_force_assembly(task, state)
        economy = state.get("economy", {})
        surplus = economy.get("power_surplus") if isinstance(economy, dict) else None
        if isinstance(surplus, bool) or not isinstance(surplus, int):
            surplus = None
        tactical = recommend_tactics(state, "base_development")
        task["tactical_recommendation"] = {
            "actions": list(tactical.get("actions", [])),
            "rules": [str(item.get("id")) for item in tactical.get("rules", []) if isinstance(item, dict)],
            "combat_doctrine_id": (
                tactical.get("combat_doctrine", {}).get("rule", {}).get("id")
                if isinstance(tactical.get("combat_doctrine"), dict) else None
            ),
        }
        defense_target = int(task.get("defense_buildings", 6))
        if "defend_local_area" in tactical.get("actions", []):
            defense_target = max(defense_target, 8)
        committed = task.setdefault("committed_counts", {})
        current_credits = None
        if isinstance(economy, dict) and isinstance(economy.get("credits"), int):
            current_credits = int(economy["credits"])
        elif isinstance(state.get("player"), dict) and isinstance(state["player"].get("credits"), int):
            current_credits = int(state["player"]["credits"])
        flow = self._economy_tracker.report()
        power_report = flow.get("power") if isinstance(flow, dict) else {}
        power_stable = (
            isinstance(power_report, dict)
            and power_report.get("stable") is True
        )
        task["power_gate"] = {
            "surplus": surplus,
            "stable": power_stable,
            "stable_samples": (
                power_report.get("stable_samples")
                if isinstance(power_report, dict) else 0
            ),
            "nasam_minimum_reserve": 300,
        }
        budget = self._economic_budget(
            state, reserve_override=task.get("credit_reserve")
        )
        task["economic_budget"] = budget.public()
        task["economic_personality"] = self._economic_personality.public()
        enemy_threat = summarize_visible_enemy_threat(
            state.get("visible_entities", []), unit_profile
        )
        task["enemy_threat_summary"] = enemy_threat
        base_under_attack = any(
            isinstance(alert, dict) and alert.get("kind") == "under_attack"
            for alert in state.get("alerts", [])
        ) or any(
            item.get("kind") == "building"
            and float(item.get("health_ratio", 1.0)) < 0.98
            for item in entities
        )
        power_ready_for_defense = power_reserve_allows_defense(surplus)
        emergency_defense = (
            choose_adaptive_defense(
                available_profiles,
                owned_counts,
                task.setdefault("committed_counts", {}),
                enemy_threat,
                under_attack=base_under_attack,
                credits=current_credits,
                power_surplus=surplus,
                power_stable=power_stable,
            )
            if power_ready_for_defense else None
        )
        needed_upgrade = choose_needed_upgrade(
            available_profiles, entities, committed,
            task.get("used_upgrade_hosts", {}),
        )
        if (
            emergency_defense is None
            and needed_upgrade is None
            and self._run_resource_expansion(
            task,
            state,
            available_profiles,
            entities,
            owned_counts,
            budget,
            busy_lanes,
            )
        ):
            return
        infantry_defense = (
            choose_anti_infantry_defense(
                available_profiles,
                owned_counts,
                committed,
                int(enemy_threat.get("combat_infantry", 0)),
                power_surplus=surplus,
                power_stable=power_stable,
            )
            if power_ready_for_defense else None
        )
        us_decision, blocked_build = (None, None) if (
            emergency_defense is not None
            or needed_upgrade is not None or infantry_defense is not None
        ) else choose_us_decision(
            available_profiles,
            owned_counts,
            committed,
            credits=current_credits,
            economy_growing=flow.get("flow_status") == "growing",
            surplus_available=budget.spendable > 0,
            variation_index=self._combat_experiments.variation_index,
            combat_doctrine_rule=(
                tactical.get("combat_doctrine", {}).get("rule")
                if isinstance(tactical.get("combat_doctrine"), dict) else None
            ),
        )
        if us_decision is not None:
            profile = next(
                item for item in available_profiles
                if str(item.get("type_id", "")).upper() == us_decision.type_id
            )
        doctrine_choice = None if (
            emergency_defense is not None
            or needed_upgrade is not None
            or infantry_defense is not None
            or us_decision is not None or blocked_build is not None
        ) else doctrine_step(
            task.get("doctrine") or DEFAULT_DOCTRINE,
            available_profiles, owned_counts, committed,
        )
        choice = (
            emergency_defense
            if emergency_defense is not None and base_under_attack else
            (needed_upgrade, "fill an empty support-upgrade host slot")
            if needed_upgrade is not None else
            infantry_defense if infantry_defense is not None else
            emergency_defense if emergency_defense is not None else
            (profile, us_decision.reason) if us_decision is not None else None
        )
        if doctrine_choice:
            profile, step = doctrine_choice
            choice = (profile, f"执行开局策略：{step.get('query')}")
        if choice is None and blocked_build is None:
            choice = choose_building(
                available_profiles, owned_counts, committed,
                power_surplus=surplus, defense_target=defense_target,
                power_stable=power_stable,
            )
        quantity = us_decision.quantity if us_decision is not None else 1
        reason = ""
        if choice:
            profile, reason = choice
        else:
            force = choose_defense_force(
                available_profiles, owned_counts, task.setdefault("committed_counts", {}),
                task.get("force_targets", {}),
            )
            if force:
                profile, quantity = force
                reason = "补充基地防御部队"
            else:
                task["status"] = "maintaining"
                task["last_reason"] = "初始发展目标完成，继续监控经济、电力和新解锁项目"
                task["initial_completed"] = True
                return
        type_id = str(profile["type_id"]).upper()
        if profile_class(profile) == "defense":
            allowed_defenses = filter_defense_profiles_for_power(
                available_profiles,
                power_surplus=surplus,
                power_stable=power_stable,
            )
            allowed_ids = {
                str(item.get("type_id") or "").upper()
                for item in allowed_defenses
                if profile_class(item) == "defense"
            }
            if type_id not in allowed_ids:
                # A custom doctrine must not bypass the power gate.  Prefer a
                # safe Allied early defence if one is visible; otherwise wait
                # instead of sending NASAM into a low-power house.
                fallback = next(
                    (
                        item for item in allowed_defenses
                        if profile_class(item) == "defense"
                    ),
                    None,
                )
                if fallback is None:
                    task["status"] = "waiting_for_power"
                    task["last_reason"] = (
                        "NASAM requires stable telemetry and at least 300 power reserve"
                    )
                    return
                profile = fallback
                type_id = str(profile["type_id"]).upper()
                reason = "低电力阶段改用低耗电防御"
        lane = production_lane(profile)
        lane_last_action[lane] = now
        credits = None
        if isinstance(economy, dict) and isinstance(economy.get("credits"), int):
            credits = int(economy["credits"])
        elif isinstance(state.get("player"), dict) and isinstance(state["player"].get("credits"), int):
            credits = int(state["player"]["credits"])
        cost = max(0, int(profile.get("cost") or 0)) * quantity
        if profile.get("kind") != "building":
            quantity = budget.affordable_quantity(
                max(0, int(profile.get("cost") or 0)), quantity
            )
            cost = max(0, int(profile.get("cost") or 0)) * quantity
        if quantity <= 0 or not budget.can_spend(cost):
            task["status"] = "waiting_for_funds"
            task["last_reason"] = budget.reason
            return
        try:
            result = self._client.command_production({
                "request_id": str(uuid.uuid4()), "type_id": type_id,
                "quantity": 1 if profile.get("kind") == "building" else min(50, quantity),
            })
        except (BridgeError, TypeError, ValueError) as exc:
            task["status"] = "retrying"
            task["last_error"] = str(exc)
            task["last_action_at"] = now
            return
        accepted = max(0, int(result.get("accepted_count", 0)))
        task["last_action_at"] = now
        task["decisions"] += 1
        if not accepted:
            task["status"] = "retrying"
            task["last_error"] = str(result.get("last_error") or result.get("error") or "原生生产事件未接受")
            return
        self._combat_experiments.record_decision({
            "task_id": task["task_id"],
            "type_id": type_id,
            "quantity": accepted,
            "reason": reason,
            "credits": credits,
            "variation_index": self._combat_experiments.variation_index,
        })
        production_task_id = uuid.uuid4().hex[:12]
        baseline_ids = {
            int(item["object_id"]) for item in entities
            if str(item.get("type_id", "")).upper() == type_id
            and isinstance(item.get("object_id"), int)
        }
        production_task = {
            "task_id": production_task_id,
            "product": str(profile.get("display_name") or type_id),
            "display_name": str(profile.get("display_name") or type_id),
            "type_id": type_id, "kind": str(profile.get("kind") or "unknown"),
            "requested_count": accepted, "submitted_count": accepted,
            "baseline_ids": baseline_ids, "observed_ids": set(),
            "queue_count": 0, "queue_progress_ratio": 0.0, "queue_on_hold": False,
            "last_submit_at": now, "submit_attempts": 1, "status": "queued",
            "created_at": now,
            "expires_at": now + int(self._cfg.get("production_task_seconds", 21600)),
            "owner_base_task_id": task["task_id"],
            "economic_reserved_cost": max(0, int(profile.get("cost") or 0)) * accepted,
            "economic_reserved_until": now + 3.0,
        }
        self._production_tasks[production_task_id] = production_task
        active_ids[lane] = production_task_id
        committed = task.setdefault("committed_counts", {})
        committed[type_id] = int(committed.get(type_id, 0)) + accepted
        task["status"] = "producing_parallel" if busy_lanes else "producing"
        task["last_reason"] = reason
        task["last_error"] = None

    @staticmethod
    def _public_base_task(task: dict[str, Any]) -> dict[str, Any]:
        return {
            key: task.get(key) for key in (
                "task_id", "kind", "status", "active_production_task_id",
                "active_production_task_ids",
                "decisions", "placement_candidates_tested", "last_reason",
                "last_error", "initial_completed", "enemy_threat_summary",
                "economic_personality", "economic_budget",
                "resource_expansion",
            )
        }

    def _run_us_force_assembly(
        self, task: dict[str, Any], state: dict[str, Any]
    ) -> None:
        """Load once before departure, then release one cohesive US combat group."""
        load_id = str(task.get("us_assembly_load_task_id") or "")
        if load_id:
            load_task = self._load_tasks.get(load_id)
            if not load_task or load_task.get("status") != "completed":
                return
            live_by_id = {
                int(item["object_id"]): item
                for item in state.get("visible_entities", [])
                if isinstance(item, dict) and isinstance(item.get("object_id"), int)
            }
            baseline = load_task.get("baseline_counts", {})
            verified_transport_ids = sorted(
                int(transport_id)
                for transport_id in load_task.get("transport_ids", [])
                if int(transport_id) in live_by_id
                and int(live_by_id[int(transport_id)].get("passenger_count", 0))
                > int(baseline.get(int(transport_id), 0))
            )
            required_occupied = max(
                1, int(load_task.get("required_occupied_transports", 15))
            )
            if len(verified_transport_ids) < required_occupied:
                load_task["status"] = "loading"
                task["last_reason"] = (
                    "IFV convoy verification failed: occupied transports "
                    f"{len(verified_transport_ids)}/{required_occupied}"
                )
                return
            previous_attacks = [
                self._attack_tasks.get(str(value))
                for value in task.get("us_strike_attack_task_ids", [])
            ]
            if any(
                attack
                and attack.get("status") not in {
                    "cancelled", "expired", "completed", "failed"
                }
                for attack in previous_attacks
            ):
                return
            task["us_strike_attack_task_ids"] = []
            assessment = self._strategic_director.last_assessment
            if (
                assessment is None
                or not self._strategic_director.should_start_attack(assessment)
            ):
                task["last_reason"] = (
                    "IFV convoy loaded; holding at base until sustained force "
                    "advantage is confirmed"
                )
                return
            entities = [
                item for item in state.get("visible_entities", [])
                if isinstance(item, dict) and item.get("relation") == "self"
                and item.get("alive") is True and item.get("controllable") is True
                and isinstance(item.get("object_id"), int)
            ]
            protected_ids = {
                int(value)
                for key in ("defender_ids", "reserve_ids")
                for value in self._strategic_roles.get(key, [])
            }
            threat = summarize_visible_enemy_threat(
                state.get("visible_entities", []), unit_profile
            )
            escort_units: list[dict[str, Any]] = []
            for type_id, count, _role in choose_us_convoy_escort_requirements(threat):
                matching = sorted(
                    (
                        item for item in entities
                        if str(item.get("type_id", "")).upper() == type_id
                        and int(item["object_id"]) not in protected_ids
                    ),
                    key=lambda item: int(item["object_id"]),
                )
                if len(matching) < count:
                    task["last_reason"] = f"waiting for fixed convoy escort: {type_id} {len(matching)}/{count}"
                    return
                escort_units.extend(matching[:count])
            combat_ids = verified_transport_ids + [
                int(item["object_id"]) for item in escort_units
            ]
            now = time.monotonic()
            attack_id = uuid.uuid4().hex[:12]
            self._attack_tasks[attack_id] = {
                "task_id": attack_id,
                "query": "fixed-loadout US combined convoy",
                "target_query": "",
                "preferred_target_kind": "building",
                "mode": "target",
                "fire_control": "concentrated",
                "unit_ids": combat_ids,
                "assignments": {},
                "last_order_at": {},
                "created_at": now,
                "expires_at": now + int(self._cfg.get("attack_task_default_seconds", 21600)),
                "duration_seconds": int(self._cfg.get("attack_task_default_seconds", 21600)),
                "orders_issued": 0,
                "failed_orders": 0,
                "status": "waiting_for_enemy",
                "strategic_director": True,
                "convoy_role": "cohesive_fixed_loadout",
                "passenger_switching_allowed": False,
            }
            task["us_strike_attack_task_ids"] = [attack_id]
            task["last_reason"] = "fixed IFV loadout and external Riot/Abrams escort released as one combat group"
            return

        entities = [
            item for item in state.get("visible_entities", [])
            if isinstance(item, dict) and item.get("relation") == "self"
            and item.get("alive") is True and isinstance(item.get("object_id"), int)
        ]
        passengers: list[dict[str, Any]] = []
        passenger_roles: dict[str, str] = {}
        threat = summarize_visible_enemy_threat(state.get("visible_entities", []), unit_profile)
        requirements = choose_us_convoy_requirements(threat)
        for type_id, count, role in requirements:
            matching = [
                item for item in entities
                if str(item.get("type_id", "")).upper() == type_id
                and item.get("controllable") is True
            ]
            if len(matching) < count:
                return
            selected = sorted(matching, key=lambda item: int(item["object_id"]))[:count]
            passengers.extend(selected)
            for item in selected:
                passenger_roles[str(int(item["object_id"]))] = role
        transports = [
            item for item in entities
            if str(item.get("type_id", "")).upper() == "FV"
            and item.get("controllable") is True
            and item.get("transport_capacity") is True
            and int(item.get("passenger_count", 0)) == 0
        ]
        if len(transports) < len(passengers):
            return
        load_task = self._create_load_task(
            passengers,
            sorted(transports, key=lambda item: int(item["object_id"]))[:len(passengers)],
            "US fixed seats: Tanya, engineers, GGI anti-armor, Navy SEAL assault",
            "fixed-loadout IFV convoy; no switching during combat",
        )
        load_task["passenger_roles"] = passenger_roles
        load_task["passenger_type_ids"] = {
            str(int(item["object_id"])): str(item.get("type_id") or "").upper()
            for item in passengers
        }
        load_task["owner_base_task_id"] = task["task_id"]
        load_task["require_one_passenger_per_transport"] = True
        load_task["required_occupied_transports"] = len(passengers)
        self._load_tasks[load_task["task_id"]] = load_task
        task["us_assembly_load_task_id"] = load_task["task_id"]
        task["last_reason"] = "assembling fixed 15-IFV loadout before departure"
        self._run_load_task(load_task, state)

    def _run_production_task(self, task: dict[str, Any], state: dict[str, Any]) -> None:
        now = time.monotonic()
        type_id = str(task["type_id"]).upper()
        visible = state.get("visible_entities", [])
        current_ids = {
            int(item["object_id"])
            for item in visible
            if isinstance(item, dict)
            and item.get("relation") == "self"
            and item.get("alive") is True
            and str(item.get("type_id", "")).upper() == type_id
            and isinstance(item.get("object_id"), int)
        }
        task["observed_ids"].update(current_ids - task["baseline_ids"])
        requested = int(task["requested_count"])
        if len(task["observed_ids"]) >= requested:
            task["status"] = "completed"
            self._command_runtime.flags.clear(
                f"production:{task['task_id']}:awaiting_placement",
                source="production_task", priority=80,
            )
            self._command_runtime.flags.set(
                f"production:{task['task_id']}:completed", True,
                source="production_task", priority=80,
            )
            return
        production = state.get("production", {})
        queue = production.get("queue", []) if isinstance(production, dict) else []
        matching = [
            item for item in queue
            if isinstance(item, dict) and str(item.get("type_id", "")).upper() == type_id
        ]
        profile = unit_profile(type_id) or {}
        if profile.get("powers_up_building") and task.get("placement_accepted_at") is not None:
            host_id = task.get("placement_host_object_id")
            host = next(
                (
                    item for item in visible
                    if isinstance(item, dict)
                    and item.get("relation") == "self"
                    and item.get("alive") is True
                    and isinstance(host_id, int)
                    and item.get("object_id") == host_id
                ),
                None,
            )
            occupied = (
                host.get("upgrade_occupied_count")
                if isinstance(host, dict) else None
            )
            elapsed = now - float(task["placement_accepted_at"])
            if isinstance(occupied, int) and occupied > 0:
                task["status"] = "completed"
                task["placement_verified"] = True
                task["observed_ids"].add(-max(1, int(host_id or 1)))
                self._append_diagnostic_log("building_placement.jsonl", {
                    "event": "placement_verified",
                    "task_id": task.get("task_id"),
                    "type_id": type_id,
                    "target_object_id": host_id,
                    "host_upgrade_occupied_count": occupied,
                    "verification_seconds": round(elapsed, 3),
                })
                self._command_runtime.flags.clear(
                    f"production:{task['task_id']}:awaiting_placement",
                    source="production_task", priority=80,
                )
                self._command_runtime.flags.set(
                    f"production:{task['task_id']}:completed", True,
                    source="production_task", priority=80,
                )
                return
            if not matching and elapsed >= 5.0:
                task["status"] = "failed"
                task["placement_verified"] = False
                task["placement_error"] = (
                    "native placement event was accepted but the host upgrade slot did not change"
                )
                self._append_diagnostic_log("building_placement.jsonl", {
                    "event": "placement_verification_failed",
                    "task_id": task.get("task_id"),
                    "type_id": type_id,
                    "target_object_id": host_id,
                    "host_upgrade_occupied_count": occupied,
                    "verification_seconds": round(elapsed, 3),
                    "queue_present": False,
                })
                return
        task["queue_count"] = sum(max(0, int(item.get("queued_count", 0))) for item in matching)
        task["queue_progress_ratio"] = max(
            [float(item.get("progress_ratio", 0.0)) for item in matching] or [0.0]
        )
        task["queue_on_hold"] = any(item.get("on_hold") is True for item in matching)
        submitted = int(task["submitted_count"])
        observed = len(task["observed_ids"])
        if (
            task.get("kind") == "building"
            and matching
            and task["queue_progress_ratio"] >= 1.0
            and task["queue_on_hold"]
        ):
            task["status"] = "ready_to_place"
            self._command_runtime.flags.set(
                f"production:{task['task_id']}:awaiting_placement", True,
                source="production_task", priority=80,
            )
            self._attempt_building_placement(task, state)
            return
        if task.get("kind") == "building" and submitted > observed:
            # Vanilla YR has a single completed-building placement slot unless
            # Phobos parallel building queues are explicitly enabled. Do not
            # submit the next building until the previous one exists on-map.
            task["status"] = "producing" if matching else "ready_to_place"
            self._command_runtime.flags.set(
                f"production:{task['task_id']}:awaiting_placement",
                not bool(matching), source="production_task", priority=80,
            )
            if not matching:
                self._attempt_building_placement(task, state)
            return
        if task.get("kind") == "building":
            self._command_runtime.flags.clear(
                f"production:{task['task_id']}:awaiting_placement",
                source="production_task", priority=80,
            )
        if submitted < requested and now - float(task["last_submit_at"]) >= 5.0:
            remaining = requested - submitted
            try:
                result = self._client.command_production(
                    {
                        "request_id": str(uuid.uuid4()),
                        "type_id": type_id,
                        "quantity": 1 if task.get("kind") == "building" else min(50, remaining),
                    }
                )
                accepted = max(0, int(result.get("accepted_count", 0)))
                if accepted:
                    task["submitted_count"] += accepted
                task["submit_attempts"] += 1
                task["last_submit_at"] = now
            except (BridgeError, TypeError, ValueError):
                task["last_submit_at"] = now
        if task["submitted_count"] < requested:
            task["status"] = "waiting_for_factory"
        elif matching:
            task["status"] = "producing"
        else:
            task["status"] = "queued"

    @staticmethod
    def _public_load_task(task: dict[str, Any]) -> dict[str, Any]:
        return {
            "task_id": task["task_id"],
            "passenger_query": task.get("passenger_query"),
            "transport_query": task.get("transport_query"),
            "passenger_count": len(task.get("passenger_ids", [])),
            "transport_count": len(task.get("transport_ids", [])),
            "loaded_count": int(task.get("loaded_count", 0)),
            "status": task.get("status"),
            "orders_issued": int(task.get("orders_issued", 0)),
            "failed_orders": int(task.get("failed_orders", 0)),
            "remaining_seconds": max(0, round(task["expires_at"] - time.monotonic())),
        }

    @staticmethod
    def _public_production_task(task: dict[str, Any]) -> dict[str, Any]:
        return {
            "task_id": task["task_id"],
            "product": task.get("product"),
            "type_id": task.get("type_id"),
            "kind": task.get("kind"),
            "requested_count": int(task.get("requested_count", 0)),
            "submitted_count": int(task.get("submitted_count", 0)),
            "observed_count": len(task.get("observed_ids", set())),
            "queue_count": int(task.get("queue_count", 0)),
            "queue_progress_ratio": round(float(task.get("queue_progress_ratio", 0.0)), 4),
            "queue_on_hold": bool(task.get("queue_on_hold", False)),
            "submit_attempts": int(task.get("submit_attempts", 0)),
            "status": task.get("status"),
            "remaining_seconds": max(0, round(task["expires_at"] - time.monotonic())),
        }

    def _tick_intent_plans(
        self, state: dict[str, Any], intent_id: str | None = None
    ) -> None:
        """Advance high-level objectives without blocking the native watch loop."""
        selected_ids = [intent_id] if intent_id else self._intent_plans.active_ids()
        for selected in selected_ids:
            plan = self._intent_plans.raw(selected)
            if not plan or plan.get("status") in IntentPlanEngine.TERMINAL:
                continue
            self._intent_plans.heartbeat(
                selected,
                world_revision=state.get("world_model", {}).get("revision")
                if isinstance(state.get("world_model"), dict) else None,
                world_frame=state.get("game", {}).get("frame")
                if isinstance(state.get("game"), dict) else None,
            )
            objectives = list(plan.get("objectives", []))
            for objective in objectives:
                if objective.get("status") in {"succeeded", "failed", "cancelled"}:
                    continue
                dependencies = {
                    item.get("objective_id"): item.get("status")
                    for item in objectives
                }
                if any(dependencies.get(dep) in {"failed", "cancelled"} for dep in objective.get("depends_on", [])):
                    self._intent_plans.set_objective(
                        selected, str(objective["objective_id"]), "failed",
                        error="a prerequisite intent failed",
                    )
                    continue
                if not all(dependencies.get(dep) == "succeeded" for dep in objective.get("depends_on", [])):
                    self._intent_plans.set_objective(
                        selected, str(objective["objective_id"]), "waiting",
                    )
                    continue
                kind = str(objective.get("kind"))
                evaluation = self._strategy_evaluator.evaluate(objective, state)
                if evaluation.get("allowed") is not True:
                    blockers = evaluation.get("blockers") or []
                    blocker = blockers[0] if blockers else {}
                    reason = str(blocker.get("reason") if isinstance(blocker, dict) else "策略前置条件未满足")
                    self._intent_plans.decide(selected, f"策略评估等待：{reason}", error=reason)
                    self._intent_plans.set_objective(
                        selected, str(objective["objective_id"]), "waiting", error=reason,
                    )
                    continue
                self._intent_plans.decide(
                    selected, f"策略评估通过：score={float(evaluation.get('score', 0.0)):.2f}"
                )
                if kind == "develop_base":
                    # The async entry point starts this objective before the watcher
                    # sees it. A missing task means the plan was created as a dry
                    # run or a bridge recovery is still in progress.
                    if plan.get("executor", {}).get("base_task_id"):
                        self._intent_plans.set_objective(
                            selected, str(objective["objective_id"]), "succeeded",
                        )
                    else:
                        self._intent_plans.set_objective(
                            selected, str(objective["objective_id"]), "waiting",
                        )
                    continue
                if kind in {
                    "produce_force", "attack", "deploy_mcv", "explore", "defend",
                    "move", "load", "capture", "repair", "stop", "guard", "wait",
                }:
                    self._advance_intent_order_plan(plan, objective, state)
                    continue
                self._intent_plans.set_objective(
                    selected, str(objective["objective_id"]), "failed",
                    error=f"intent objective {kind} has no executor",
                )

    def _advance_intent_order_plan(
        self, plan: dict[str, Any], objective: dict[str, Any], state: dict[str, Any]
    ) -> None:
        intent_id = str(plan["intent_id"])
        executor = plan.setdefault("executor", {})
        low_plan_id = str(executor.get("order_plan_id") or "")
        if not low_plan_id:
            product_type_id = str(plan.get("selected_type_id") or "").upper()
            product_name = str(plan.get("selected_product") or "").strip()
            if not product_type_id and product_name:
                profiles = catalog_summary(product_name)
                product_type_id = str(profiles[0].get("type_id") or "").upper() if profiles else ""
            if not product_type_id and any(item.get("kind") == "produce_force" for item in plan["objectives"]):
                chosen = self._choose_intent_product(state)
                if chosen:
                    product_type_id, product_name = chosen
                    self._intent_plans.decide(
                        intent_id,
                        f"selected native build option {product_type_id} ({product_name})",
                        selected_product=product_name,
                        selected_type_id=product_type_id,
                    )
                else:
                    self._intent_plans.decide(
                        intent_id,
                        "waiting for a currently buildable combat unit in the native sidebar",
                    )
                    self._intent_plans.set_objective(
                        intent_id, str(objective["objective_id"]), "waiting",
                    )
                    return
            orders: list[dict[str, Any]] = []
            produce_objective = next(
                (item for item in plan["objectives"] if item.get("kind") == "produce_force"),
                None,
            )
            attack_objective = next(
                (item for item in plan["objectives"] if item.get("kind") == "attack"),
                None,
            )
            deploy_objective = next(
                (item for item in plan["objectives"] if item.get("kind") == "deploy_mcv"),
                None,
            )
            if deploy_objective:
                orders.append({
                    "order_id": f"intent_{deploy_objective['objective_id']}",
                    "action": "deploy", "query": "机动建设车",
                    "depends_on": [], "on_failure": "abort",
                })
            if produce_objective:
                params = produce_objective.get("params", {})
                produce_order_id = f"intent_{produce_objective['objective_id']}"
                orders.append({
                    "order_id": produce_order_id,
                    "action": "produce", "product": product_name or product_type_id,
                    "type_id": product_type_id,
                    "quantity": int(params.get("quantity") or plan.get("force_size") or 12),
                    "depends_on": ([f"intent_{deploy_objective['objective_id']}"] if deploy_objective else []),
                    "on_failure": "abort", "retry_seconds": 3.0,
                })
            if attack_objective:
                params = attack_objective.get("params", {})
                depends = []
                if produce_objective:
                    depends.append(f"intent_{produce_objective['objective_id']}")
                orders.append({
                    "order_id": f"intent_{attack_objective['objective_id']}",
                    "action": "attack", "query": product_type_id or params.get("query") or "",
                    "group_number": params.get("group_number", plan.get("group_number")),
                    "target_query": params.get("target_query") or plan.get("target_query") or "敌人",
                    "continuous": params.get("continuous", plan.get("continuous", True)) is not False,
                    "unit_offset": params.get("unit_offset", 0),
                    **({"unit_limit": params["unit_limit"]} if params.get("unit_limit") is not None else {}),
                    "depends_on": depends, "on_failure": "abort", "retry_seconds": 3.0,
                })
            handled_kinds = {"develop_base", "deploy_mcv", "produce_force", "attack"}
            for generic in plan["objectives"]:
                generic_kind = str(generic.get("kind"))
                if generic_kind in handled_kinds:
                    continue
                params = generic.get("params") if isinstance(generic.get("params"), dict) else {}
                action = "guard" if generic_kind == "defend" else generic_kind
                if action not in {
                    "move", "load", "capture", "repair", "stop", "guard", "wait", "explore",
                }:
                    continue
                generic_order = {
                    "order_id": f"intent_{generic['objective_id']}",
                    "action": action,
                    "depends_on": [
                        f"intent_{dependency}"
                        for dependency in generic.get("depends_on", [])
                        if any(item.get("objective_id") == dependency for item in plan["objectives"])
                    ],
                    "on_failure": "abort",
                    "retry_seconds": 3.0,
                }
                generic_order.update(params)
                if action == "explore" and "query" not in generic_order:
                    generic_order["query"] = plan.get("selected_product") or ""
                orders.append(generic_order)
            if not orders:
                self._intent_plans.set_objective(
                    intent_id, str(objective["objective_id"]), "failed",
                    error="intent did not compile to a native order",
                )
                return
            try:
                low_plan = self._order_plans.create(
                    f"intent:{intent_id}", orders, duration_seconds=None
                )
            except (OrderPlanError, TypeError, ValueError) as exc:
                self._intent_plans.set_objective(
                    intent_id, str(objective["objective_id"]), "failed", error=str(exc)
                )
                return
            low_plan_id = str(low_plan["plan_id"])
            self._intent_plans.attach_executor(
                intent_id, order_plan_id=low_plan_id, order_ids=[item["order_id"] for item in orders]
            )
            for item in plan["objectives"]:
                if any(order.get("order_id") == f"intent_{item['objective_id']}" for order in orders):
                    self._intent_plans.set_objective(
                        intent_id, str(item["objective_id"]), "running",
                        result={"order_plan_id": low_plan_id},
                    )
            self._order_plans.tick(state)
            return

        low_plans = self._order_plans.plans(low_plan_id, include_finished=True)
        if not low_plans:
            self._intent_plans.set_objective(
                intent_id, str(objective["objective_id"]), "failed",
                error="attached native order plan disappeared",
            )
            return
        low_plan = low_plans[0]
        by_order = {str(item.get("order_id")): item for item in low_plan.get("orders", [])}
        for item in plan["objectives"]:
            order = by_order.get(f"intent_{item['objective_id']}")
            if not order:
                continue
            status = str(order.get("status"))
            mapped = "succeeded" if status == "succeeded" else "failed" if status == "failed" else "running"
            self._intent_plans.set_objective(
                intent_id, str(item["objective_id"]), mapped,
                result=order.get("result") if isinstance(order.get("result"), dict) else None,
                error=order.get("last_error"),
            )

    @staticmethod
    def _choose_intent_product(state: dict[str, Any]) -> tuple[str, str] | None:
        production = state.get("production") if isinstance(state.get("production"), dict) else {}
        options = production.get("sidebar_available", [])
        candidates = []
        if not isinstance(options, list):
            return None
        for option in options:
            if not isinstance(option, dict) or not option.get("type_id"):
                continue
            type_id = str(option["type_id"]).upper()
            profile = unit_profile(type_id) or option
            if profile.get("kind") not in {"infantry", "vehicle", "aircraft"}:
                continue
            if profile.get("is_ai_only") is True or profile.get("combat_relevant") is not True:
                continue
            kind_priority = {"infantry": 0, "vehicle": 1, "aircraft": 2}.get(profile.get("kind"), 9)
            candidates.append((
                kind_priority, int(profile.get("tech_level") or 0),
                int(profile.get("cost") or 999999), type_id,
                str(profile.get("display_name") or type_id),
            ))
        if not candidates:
            return None
        candidates.sort()
        selected = candidates[0]
        return selected[3], selected[4]

    def _watch_loop(self) -> None:
        self._watch_started_at = time.time()
        self.logger.info(
            "Mental Omega watch loop entered; poll=%.2fs focus=%s",
            self._watch_poll_seconds(),
            self._cfg.get("mental_omega_focus_enabled", True) is True,
        )
        while not self._watch_stop.wait(self._watch_poll_seconds()):
            try:
                self._watch_last_poll_at = time.time()
                health = self._bridge_health_with_recovery()
                game_connected = health.get("game_connected") is True
                if not game_connected:
                    self._sync_game_session(False, health)
                if self._cfg.get("mental_omega_focus_enabled", True) is True:
                    try:
                        previous = self._focus.active
                        current = self._focus.sync(game_connected)
                        if current != previous:
                            self.logger.info(
                                "Mental Omega focus mode %s",
                                "enabled" if current else "restored",
                            )
                    except NekoFocusError as exc:
                        self.logger.warning("Mental Omega focus sync failed: %s", exc)
                if not game_connected:
                    continue
                now = time.monotonic()
                for watch_id, watch in list(self._watches.items()):
                    if now >= watch["expires_at"]:
                        self._watches.pop(watch_id, None)
                for watch_id, watch in list(self._alert_watches.items()):
                    if now >= watch["expires_at"]:
                        self._alert_watches.pop(watch_id, None)
                for task_id, task in list(self._attack_tasks.items()):
                    if (
                        task.get("status") not in {"cancelled", "expired", "completed", "failed"}
                        and now >= task["expires_at"]
                    ):
                        task["status"] = "expired"
                        task["completed_at"] = now
                for task_id, task in list(self._load_tasks.items()):
                    if (
                        task.get("status") not in {"cancelled", "expired", "completed", "failed"}
                        and now >= task["expires_at"]
                    ):
                        task["status"] = "expired"
                        task["completed_at"] = now
                for task_id, task in list(self._production_tasks.items()):
                    if (
                        task.get("status") not in {"cancelled", "expired", "completed", "failed"}
                        and now >= task["expires_at"]
                    ):
                        task["status"] = "expired"
                        task["completed_at"] = now
                state = self._client.state()
                previous_world_session = self._world_model.summary().get("session_started_at")
                self._world_model.update(state)
                self._world_model.augment(state)
                game = state.get("game")
                if not isinstance(game, dict) or game.get("running") is not True:
                    self._sync_game_session(
                        False, {"game_connected": False}, confirmed_exit=True
                    )
                    if self._cfg.get("mental_omega_focus_enabled", True) is True:
                        try:
                            self._focus.sync(False)
                        except NekoFocusError:
                            pass
                    self._economy_tracker.reset()
                    continue
                self._sync_game_session(True, health)
                self._refresh_economic_personality()
                assessment = self._strategic_director.assess(state)
                state["strategic_ai"] = self._strategic_director.snapshot()
                if assessment.phase != self._strategic_last_phase:
                    self._combat_experiments.record_strategy(state["strategic_ai"])
                    self._strategic_last_phase = assessment.phase
                self._run_strategic_director(state, assessment)
                current_world_session = state.get("world_model", {}).get("session_started_at")
                if (
                    previous_world_session is not None
                    and current_world_session is not None
                    and current_world_session != previous_world_session
                ):
                    self._cancel_tasks_for_game_exit(
                        reason="检测到新对局，旧对局的任务已取消"
                    )
                self._support_power_controller.observe(state)
                for alert in state.get("alerts", []):
                    if not isinstance(alert, dict) or alert.get("kind") != "under_attack":
                        continue
                    event_id = alert.get("event_id")
                    if not isinstance(event_id, int) or event_id in self._handled_alert_interrupts:
                        continue
                    self._handled_alert_interrupts.add(event_id)
                    self._command_runtime.interrupts.post(
                        "base_under_attack", {"alert": alert}, priority=100
                    )
                self._command_runtime.tick(state, interrupt_budget=32)
                economy_event = self._economy_tracker.observe(state)
                self._combat_experiments.observe(state)
                if (
                    isinstance(economy_event, dict)
                    and economy_event.get("power_transition")
                    and self._report_policy.allow(
                        "economy", key=f"power:{economy_event['power_transition']}"
                    )
                ):
                    power = self._economy_tracker.report().get("power", {})
                    line = str(power.get("summary") or "电力状态发生变化")
                    try:
                        self._speaker.speak(
                            line,
                            {
                                "kind": "mental_omega_economy",
                                "transition": economy_event["power_transition"],
                            },
                        )
                    except ProactiveReportError as exc:
                        self.logger.warning(
                            "Mental Omega economy report failed: %s", exc
                        )
                for watch_id, watch in list(self._watches.items()):
                    previous = watch.get("status")
                    previous_distance = (
                        previous.get("closest_distance_cells")
                        if isinstance(previous, dict)
                        else None
                    )
                    current = observe_unit_watch(
                        state,
                        watch["query"],
                        previous_distance=previous_distance,
                        near_distance=self._watch_near_distance(),
                        critical_distance=self._watch_critical_distance(),
                    )
                    watch["status"] = current
                    transition = watch_transition(previous, current)
                    if transition is None:
                        continue
                    event_kind, line = transition
                    watch["last_event"] = event_kind
                    watch["last_report"] = line
                    self._emit_watch_report(
                        watch_id, event_kind, line,
                        key=f"unit_watch:{watch_id}:{event_kind}",
                    )
                for watch_id, watch in list(self._alert_watches.items()):
                    alerts, newest = observe_alert_watch(state, watch)
                    watch["last_event_id"] = newest
                    for alert in reversed(alerts):
                        line = alert_watch_report(watch, alert)
                        watch["last_event"] = alert
                        watch["last_report"] = line
                        self._emit_watch_report(
                            watch_id, "base_under_attack", line,
                            key=f"alert:{alert.get('event_id')}",
                            severity=int(alert.get("severity", 0)),
                        )
                for task in list(self._attack_tasks.values()):
                    if task.get("status") not in {"cancelled", "expired", "completed", "failed"}:
                        self._run_attack_task(task, state)
                for task in list(self._load_tasks.values()):
                    if task.get("status") not in {"cancelled", "expired", "completed", "failed"}:
                        self._run_load_task(task, state)
                for task in list(self._production_tasks.values()):
                    if task.get("status") not in {"cancelled", "expired", "completed", "failed"}:
                        self._run_production_task(task, state)
                base_task = self._deduplicate_base_development_tasks()
                if base_task is not None and base_task.get("commander_authorized") is True:
                    self._run_base_development_task(base_task, state)
                elif base_task is not None:
                    base_task["status"] = "cancelled"
                    base_task["last_error"] = "base development requires explicit commander authorization"
                    self.logger.info(
                        "Mental Omega base-development task cancelled: explicit authorization missing"
                    )
                for task in list(self._force_tasks.values()):
                    if task.get("status") != "cancelled":
                        self._run_force_maintenance_task(task, state)
                for task in list(self._defense_tasks.values()):
                    if task.get("status") not in {"cancelled", "failed"}:
                        self._run_defense_task(task, state)
                if (
                    self._session_enabled
                    and self._cfg.get("intent_planner_enabled", True) is True
                    and self._intent_plans.active_ids()
                ):
                    self._tick_intent_plans(state)
                if self._session_enabled and self._order_plans.active_count:
                    self._order_plans.tick(state)
                if self._session_enabled and self._adaptive_tasks.active_count:
                    self._adaptive_tasks.tick(state)
                self._report_task_terminal_transitions()
                self._report_intent_transitions()
                self._maybe_push_autonomous_reasoning(state)
                if self._session_enabled and self._autonomy_enabled:
                    self._run_autonomous_commander(state)
                    if any(
                        task.get("status") not in {"cancelled", "failed", "completed"}
                        and task.get("commander_authorized") is True
                        for task in self._base_tasks.values()
                    ):
                        support_result = self._support_power_controller.run_once(state)
                        if support_result is not None:
                            self.logger.info(
                                "Mental Omega support power decision: type=%s target=%s score=%s accepted=%s",
                                support_result.get("type_id"),
                                support_result.get("target_cell"),
                                support_result.get("score"),
                                support_result.get("accepted"),
                            )
                self._watch_last_error = None
            except (BridgeError, TypeError, ValueError) as exc:
                self._watch_last_error = str(exc)
                self.logger.warning("Mental Omega watch poll failed: %s", exc)
            except Exception as exc:
                self._watch_last_error = str(exc)
                self.logger.exception("Mental Omega watch loop error: %s", exc)
        self.logger.info("Mental Omega watch loop stopped")

    def _sync_game_session(
        self, connected: bool, health: dict[str, Any], *, confirmed_exit: bool = False,
    ) -> None:
        """Mirror the native game session in control state and task lifetime."""
        connected = bool(connected)
        previous = self._last_game_connected
        self._last_game_connected = connected
        if not connected:
            if self._session_enabled or previous is True:
                try:
                    self._client.set_control(False)
                except (BridgeError, TypeError, ValueError):
                    pass
            self._session_enabled = False
            self._autonomy_enabled = False
            if confirmed_exit:
                self._cancel_tasks_for_game_exit()
            else:
                self._intent_plans.set_connection(
                    False, reason="游戏桥接暂时断开，长期任务已暂停并等待同一对局恢复"
                )
            return
        self._intent_plans.set_connection(
            True, reason="游戏桥接已连接，长期任务继续执行"
        )
        if not self._auto_control_enabled:
            return
        if health.get("control_enabled") is True and self._session_enabled:
            return
        try:
            result = self._client.set_control(
                True,
                network_opt_in=self._cfg.get("allow_network_control", False) is True,
            )
            if result.get("control_enabled") is True:
                self._session_enabled = True
                if previous is not True:
                    self.logger.info("Mental Omega connected; control enabled automatically")
            else:
                self._session_enabled = False
                self.logger.warning("Mental Omega connected but native control was refused")
        except (BridgeError, TypeError, ValueError) as exc:
            self._session_enabled = False
            self._watch_last_error = str(exc)

    def _cancel_tasks_for_game_exit(
        self, *, reason: str = "已确认退出对局，任务已自动取消",
    ) -> None:
        """Prevent a later match from inheriting commands from an exited game."""
        terminal = {"cancelled", "expired", "completed", "failed"}
        for collection in (
            self._attack_tasks, self._load_tasks, self._production_tasks,
            self._base_tasks, self._force_tasks, self._defense_tasks,
        ):
            for task in collection.values():
                if task.get("status") not in terminal:
                    task["status"] = "cancelled"
                    task["last_error"] = reason
        try:
            self._order_plans.cancel()
        except Exception:
            pass
        try:
            self._adaptive_tasks.clear()
        except Exception:
            pass
        self._intent_plans.cancel(reason=reason)

    def _report_task_terminal_transitions(self) -> None:
        """Speak each task's terminal edge exactly once."""
        events: list[tuple[str, str, str, dict[str, Any]]] = []
        for task in getattr(self, "_force_tasks", {}).values():
            task_id = str(task["task_id"])
            name = str(task.get("display_name") or task.get("query") or "指定部队")
            target = int(task.get("target_count", 0))
            if task.get("target_reached_once") is True:
                events.append((
                    f"force:{task_id}:initial_reached", task_id,
                    f"持续补员目标已经达到：{name} 当前维持 {target} 个。",
                    {"task_type": "force_maintenance"},
                ))
            cycle = int(task.get("last_replenishment_completed_cycle", 0))
            if cycle > 0:
                events.append((
                    f"force:{task_id}:replenished:{cycle}", task_id,
                    f"{name} 的战损补充已经完成，数量恢复到 {target} 个。",
                    {"task_type": "force_maintenance", "cycle": cycle},
                ))
            if task.get("status") == "cancelled":
                events.append((
                    f"force:{task_id}:cancelled", task_id,
                    f"已取消{name}的持续补员目标。",
                    {"task_type": "force_maintenance"},
                ))
        for task in getattr(self, "_defense_tasks", {}).values():
            if task.get("status") != "cancelled":
                continue
            task_id = str(task["task_id"])
            events.append((
                f"defense:{task_id}:cancelled", task_id,
                f"已取消{task.get('destination') or '指定区域'}的持续防御任务，所属单位已经停止。",
                {"task_type": "area_defense"},
            ))
        for task in getattr(self, "_base_tasks", {}).values():
            task_id = str(task["task_id"])
            status = str(task.get("status"))
            if task.get("initial_completed") is True:
                events.append((
                    f"base:{task_id}:initial_completed", task_id,
                    "基地初始发展目标已完成，已进入持续维护；我会继续关注电力、经济和新解锁建筑。",
                    {"task_type": "base_development"},
                ))
            if status in {"cancelled", "failed", "completed"}:
                line = (
                    "基地发展任务已取消。" if status == "cancelled"
                    else "基地发展任务已完成。" if status == "completed"
                    else f"基地发展任务失败：{task.get('last_error') or '无法继续执行'}。"
                )
                events.append((f"base:{task_id}:{status}", task_id, line,
                               {"task_type": "base_development"}))
        for task in self._attack_tasks.values():
            status = str(task.get("status"))
            if status in {"cancelled", "expired", "completed", "failed", "no_controllable_units"}:
                task_id = str(task["task_id"])
                label = str(task.get("target_query") or "持续清剿")
                outcome = "已完成" if status == "completed" else (
                    "已取消" if status == "cancelled" else "已结束"
                )
                events.append((f"attack:{task_id}:{status}", task_id,
                               f"持续攻击任务“{label}”{outcome}。", {"task_type": "attack"}))
        for task in self._load_tasks.values():
            status = str(task.get("status"))
            if status in {"completed", "cancelled", "expired", "failed"}:
                task_id = str(task["task_id"])
                loaded = int(task.get("loaded_count", 0))
                total = len(task.get("passenger_ids", []))
                if status == "completed":
                    line = f"载员任务已完成，{loaded}/{total} 个单位已经进入载具。"
                elif status == "cancelled":
                    line = f"载员任务已取消，停止时已装载 {loaded}/{total} 个单位。"
                else:
                    line = f"载员任务未能完成，停止时已装载 {loaded}/{total} 个单位。"
                events.append((f"load:{task_id}:{status}", task_id, line, {"task_type": "load"}))
        for task in self._production_tasks.values():
            status = str(task.get("status"))
            if status in {"completed", "cancelled", "expired", "failed"}:
                task_id = str(task["task_id"])
                name = str(task.get("display_name") or task.get("product") or task.get("type_id"))
                observed = len(task.get("observed_ids", set()))
                total = int(task.get("requested_count", 0))
                if status == "completed":
                    line = f"生产任务已完成：{name} {observed}/{total}。"
                elif status == "cancelled":
                    line = f"生产跟踪任务已取消：{name}，停止时完成 {observed}/{total}。"
                else:
                    line = f"生产任务未能完成：{name}，停止时完成 {observed}/{total}。"
                events.append((f"production:{task_id}:{status}", task_id, line,
                               {"task_type": "production"}))
        for task in self._adaptive_tasks.tasks(include_finished=True):
            status = str(task.get("status"))
            if status not in {"completed", "cancelled", "expired"}:
                continue
            task_id = str(task["task_id"])
            kind = str(task.get("kind"))
            destination = str(task.get("destination") or "目标区域")
            if status == "completed" and kind == "move_near":
                line = f"移动任务已完成，部队已经到达{destination}附近。"
            elif status == "completed":
                line = "探索任务已完成，当前可执行的黑幕路径点已经探查完毕。"
            elif status == "cancelled":
                line = f"{('探索' if kind == 'explore_shroud' else '移动')}任务已取消，相关单位已停止。"
            else:
                line = f"{('探索' if kind == 'explore_shroud' else '移动')}任务已到期并停止。"
            events.append((f"adaptive:{task_id}:{status}", task_id, line,
                           {"task_type": kind}))
        for plan in self._order_plans.plans(include_finished=True):
            status = str(plan.get("status"))
            if status not in {"succeeded", "failed", "cancelled"}:
                continue
            task_id = str(plan["plan_id"])
            name = str(plan.get("name") or "复合作战计划")
            if status == "succeeded":
                line = f"复合作战计划“{name}”已全部完成，共完成 {len(plan.get('orders', []))} 项命令。"
            elif status == "cancelled":
                line = f"复合作战计划“{name}”已取消，未完成的后续命令不会再执行。"
            else:
                reason = str(plan.get("last_error") or "执行条件未满足")
                line = f"复合作战计划“{name}”执行失败：{reason}。"
            events.append((f"plan:{task_id}:{status}", task_id, line,
                           {"task_type": "plan"}))

        for event_key, task_id, line, metadata in events:
            if (
                metadata.get("task_type") == "production"
                and event_key.endswith(":completed")
                and not should_report_production_completion(
                    self._production_completion_reports_enabled, "completed"
                )
            ):
                # Completion remains visible through mental_omega_production_status
                # and persistent logs, but does not interrupt the commander by default.
                self._task_terminal_reports.add(event_key)
                continue
            if event_key in self._task_terminal_reports:
                continue
            self._task_terminal_reports.add(event_key)
            self._command_runtime.flags.set(
                f"task:{task_id}:reported", True,
                source="task_completion", priority=95,
            )
            try:
                self._speaker.speak(
                    line,
                    {"kind": "mental_omega_task_terminal", "task_id": task_id, **metadata},
                )
            except ProactiveReportError as exc:
                # Keep the edge marked: retrying every polling frame would spam
                # the N.E.K.O endpoint as soon as it recovers.
                self.logger.warning("Mental Omega task completion report failed: %s", exc)

    def _report_intent_transitions(self) -> None:
        """Push phase changes like the native long-running MC agent task service."""
        for plan in self._intent_plans.plans(include_finished=True):
            intent_id = str(plan.get("intent_id"))
            status = str(plan.get("status"))
            previous = self._intent_last_status.get(intent_id)
            self._intent_last_status[intent_id] = status
            if previous == status or status == "planned":
                continue
            if status == "running":
                line = f"长期战术任务已继续执行：{plan.get('goal', '')}。当前决策：{plan.get('last_decision', '')}"
            elif status == "completed":
                line = f"长期战术任务已完成：{plan.get('goal', '')}。"
            elif status == "cancelled":
                line = f"长期战术任务已取消：{plan.get('goal', '')}。"
            elif status == "failed":
                line = f"长期战术任务执行失败：{plan.get('goal', '')}。原因：{plan.get('last_error') or '底层执行器未接受'}"
            else:
                line = f"长期战术任务正在等待条件：{plan.get('goal', '')}。当前决策：{plan.get('last_decision', '')}"
            if not self._report_policy.allow(
                "intent", key=f"intent:{intent_id}:{status}"
            ):
                continue
            try:
                self._speaker.speak(
                    line,
                    {
                        "kind": "mental_omega_intent_transition",
                        "task_id": intent_id,
                        "status": status,
                        "world_revision": plan.get("world_revision"),
                    },
                )
            except ProactiveReportError as exc:
                self.logger.warning("Mental Omega intent transition report failed: %s", exc)

    def _maybe_push_autonomous_reasoning(self, state: dict[str, Any]) -> None:
        """Nudge the LLM with fresh battlefield state while a detached intent is active."""
        if self._cfg.get("autonomous_reasoning_enabled", True) is not True:
            self._log_autonomy_skip("disabled")
            return
        plans = self._intent_plans.plans(include_finished=False)
        if not plans:
            self._maybe_push_autonomy_activation(state)
            return
        if self._cfg.get("skip_autonomous_reasoning_if_busy", True) is True:
            for plan in plans:
                low_plan_id = str(plan.get("executor", {}).get("order_plan_id") or "")
                if low_plan_id and self._order_plans.plans(low_plan_id, include_finished=False):
                    self._log_autonomy_skip("intent_native_executor_busy")
                    return
        self._autonomy_last_skip_reason = None
        now = time.monotonic()
        interval = max(
            2.0, min(float(self._cfg.get("autonomous_reasoning_interval_seconds", 5.0)), 120.0)
        )
        if self._autonomy_last_nudge_at is not None and now - self._autonomy_last_nudge_at < interval:
            return
        push = getattr(self, "push_message", None)
        if not callable(push):
            push = getattr(self._plugin_context, "push_message", None)
        if not callable(push):
            if self._autonomy_nudge_supported is not False:
                self.logger.warning("N.E.K.O SDK has no push_message; autonomous reasoning nudges are unavailable")
            self._autonomy_nudge_supported = False
            return
        prompt = build_reasoning_nudge(
            state.get("world_model", self._world_model.summary()),
            plans,
            state.get("visible_entities", []),
            entity_limit=int(self._cfg.get("autonomous_reasoning_entity_limit", 768)),
        )
        try:
            result = push(
                parts=[{"type": "text", "text": prompt}],
                visibility=[],
                ai_behavior="respond",
            )
            if inspect.isawaitable(result):
                result = asyncio.run(result)
            self._autonomy_last_nudge_at = now
            self._autonomy_nudge_count += 1
            self._autonomy_nudge_supported = True
            self.logger.info(
                "Mental Omega autonomous reasoning nudge sent: plans=%d result=%s",
                len(plans), type(result).__name__,
            )
        except Exception as exc:
            self._autonomy_nudge_supported = True
            self.logger.warning("Mental Omega autonomous reasoning nudge failed: %s", exc)

    def _maybe_push_autonomy_activation(self, state: dict[str, Any]) -> None:
        """Recover when the dialog model missed an explicit full-control request."""
        # Activation prompts are themselves a form of autonomy escalation.
        # Never emit them before the commander explicitly enables the adjunct.
        if self._autonomy_enabled is not True:
            self._log_autonomy_skip("waiting_for_explicit_authorization")
            return
        world = state.get("world_model", self._world_model.summary())
        if not self._session_enabled:
            self._log_autonomy_skip("no_active_plan_control_disabled")
            return
        if not isinstance(world, dict) or world.get("running") is not True:
            self._log_autonomy_skip("no_active_plan_game_not_running")
            return
        now = time.monotonic()
        interval = max(
            5.0,
            min(
                float(self._cfg.get("autonomy_activation_interval_seconds", 12.0)),
                120.0,
            ),
        )
        if (
            self._autonomy_activation_last_nudge_at is not None
            and now - self._autonomy_activation_last_nudge_at < interval
        ):
            self._log_autonomy_skip("no_active_plan_waiting_for_activation_interval")
            return
        push = getattr(self, "push_message", None)
        if not callable(push):
            push = getattr(self._plugin_context, "push_message", None)
        if not callable(push):
            self._autonomy_nudge_supported = False
            self._log_autonomy_skip("no_active_plan_sdk_push_message_unavailable")
            return
        prompt = build_autonomy_activation_nudge(world)
        self._autonomy_activation_last_nudge_at = now
        self.logger.info(
            "Mental Omega autonomy activation check sent: no active plan, attempt=%d",
            self._autonomy_activation_nudge_count + 1,
        )
        try:
            result = push(
                parts=[{"type": "text", "text": prompt}],
                visibility=[],
                ai_behavior="respond",
            )
            if inspect.isawaitable(result):
                result = asyncio.run(result)
            self._autonomy_activation_nudge_count += 1
            self._autonomy_nudge_supported = True
            self._autonomy_last_skip_reason = None
            self.logger.info(
                "Mental Omega autonomy activation check completed: result=%s",
                type(result).__name__,
            )
        except Exception as exc:
            self._autonomy_nudge_supported = True
            self.logger.warning("Mental Omega autonomy activation check failed: %s", exc)

    def _log_autonomy_skip(self, reason: str) -> None:
        if self._autonomy_last_skip_reason == reason:
            return
        self._autonomy_last_skip_reason = reason
        self.logger.info("Mental Omega autonomous reasoning skipped: %s", reason)

    def _run_autonomous_commander(self, state: dict[str, Any]) -> None:
        """React to urgent visible threats while leaving strategic plans intact."""
        alerts = state.get("alerts", [])
        if not isinstance(alerts, list):
            return
        now = time.monotonic()
        response_ids = {
            int(value)
            for key in ("defender_ids", "reserve_ids")
            for value in self._strategic_roles.get(key, [])
        }
        own_units = [
            item for item in state.get("visible_entities", [])
            if isinstance(item, dict)
            and item.get("relation") == "self"
            and item.get("alive") is True
            and item.get("controllable") is True
            and item.get("kind") != "building"
            and not is_economic_unit(item)
            and (
                not response_ids
                or int(item.get("object_id", -1)) in response_ids
            )
        ]
        enemies = self._visible_attack_targets(state, "", True)
        if not own_units or not enemies:
            return
        urgent_cells = [
            item.get("cell") for item in alerts
            if isinstance(item, dict)
            and item.get("kind") == "under_attack"
            and isinstance(item.get("cell"), dict)
        ]
        if not urgent_cells:
            return
        for unit in sorted(
            own_units,
            key=lambda item: min(self._distance_sq(item, {"cell": cell}) for cell in urgent_cells),
        ):
            unit_id = int(unit["object_id"])
            if now - float(self._autonomy_last_action.get(unit_id, 0.0)) < 8.0:
                continue
            target = min(enemies, key=lambda item: self._distance_sq(unit, item))
            result = self._submit_unit_command_sync(
                state, unit_id, "attack", target_object_id=int(target["object_id"])
            )
            self._autonomy_last_action[unit_id] = now
            if result.get("accepted") is True:
                self.logger.info(
                    "Autonomous adjutant defended alert with unit %s against %s",
                    unit_id, target.get("object_id"),
                )

    def _annotate_known_transport_loadouts(self, state: dict[str, Any]) -> None:
        """Expose fixed IFV passenger weapons to the tactical matchup scorer."""
        role_types = {
            "command": "TANY",
            "repair_support": "ENGINEER",
            "anti_armor": "GGI",
            "anti_infantry_assault": "GHOST",
        }
        loadouts: dict[int, str] = {}
        for task in self._load_tasks.values():
            if task.get("status") != "completed":
                continue
            roles = task.get("passenger_roles")
            roles = roles if isinstance(roles, dict) else {}
            type_ids = task.get("passenger_type_ids")
            type_ids = type_ids if isinstance(type_ids, dict) else {}
            for passenger_id, transport_id in task.get("assignments", {}).items():
                key = str(passenger_id)
                type_id = str(type_ids.get(key) or role_types.get(str(roles.get(key))) or "").upper()
                if type_id:
                    loadouts[int(transport_id)] = type_id
        for item in state.get("visible_entities", []):
            if not isinstance(item, dict) or not isinstance(item.get("object_id"), int):
                continue
            effective = loadouts.get(int(item["object_id"]))
            if effective and int(item.get("passenger_count", 0) or 0) > 0:
                item["effective_type_id"] = effective

    def _run_strategic_response(
        self, state: dict[str, Any], roles: dict[str, Any]
    ) -> None:
        """Turn doctrine roles into persistent defence, repair and retreat orders."""
        if not self._session_enabled:
            return
        now = time.monotonic()
        entities = [
            item for item in state.get("visible_entities", [])
            if isinstance(item, dict) and isinstance(item.get("object_id"), int)
        ]
        by_id = {int(item["object_id"]): item for item in entities}
        defenders = [
            by_id[value] for value in roles.get("defender_ids", [])
            if value in by_id and by_id[value].get("controllable") is True
            and not is_economic_unit(by_id[value])
        ]
        reserves = [
            by_id[value] for value in roles.get("reserve_ids", [])
            if value in by_id and by_id[value].get("controllable") is True
            and not is_economic_unit(by_id[value])
        ]
        threats = [
            by_id[value] for value in roles.get("local_threat_ids", [])
            if value in by_id and by_id[value].get("relation") == "enemy"
        ]
        anchor = roles.get("defense_anchor")
        anchor_cell = anchor.get("cell") if isinstance(anchor, dict) else None
        assault = [
            by_id[value] for value in roles.get("assault_ids", [])
            if value in by_id and by_id[value].get("controllable") is True
            and not is_economic_unit(by_id[value])
        ]
        response_pool = list({
            int(item["object_id"]): item for item in defenders + reserves + assault
        }.values())
        counter_pairs = prioritized_counter_assignments(
            response_pool,
            threats,
            maximum_responders=min(len(response_pool), max(2, len(threats) * 2)),
        )
        counter_targets = {
            int(unit["object_id"]): target for unit, target in counter_pairs
        }
        roles["counter_assignments"] = [
            {
                "unit_id": int(unit["object_id"]),
                "unit_type_id": str(unit.get("effective_type_id") or unit.get("type_id") or ""),
                "target_id": int(target["object_id"]),
                "target_type_id": str(target.get("type_id") or ""),
                "matchup_score": round(combat_matchup_score(unit, target), 2),
            }
            for unit, target in counter_pairs
        ]
        roles["counter_updated_at"] = time.time()
        defender_ids = {int(item["object_id"]) for item in defenders}
        reserve_ids = {int(item["object_id"]) for item in reserves}
        assembly_hold_ids = {
            int(value) for value in roles.get("assembly_hold_ids", [])
        }
        responder_ids = set(counter_targets)
        for attack in self._attack_tasks.values():
            if attack.get("strategic_director") is not True:
                continue
            attack["tactical_controls"] = dict(roles.get("tactical_controls", {}))
            attack["contact_defense_rule_id"] = (
                roles.get("contact_rule", {}).get("id")
                if isinstance(roles.get("contact_rule"), dict) else None
            )
            protected_ids = (
                defender_ids | reserve_ids | responder_ids | assembly_hold_ids
            )
            reinforcement_ids = set(roles.get("assault_ids", []))
            live_ids = {
                int(value) for value in attack.get("unit_ids", [])
                if int(value) in by_id and by_id[int(value)].get("alive") is True
            }
            live_ids -= protected_ids
            eligible = sorted(
                (
                    int(value) for value in reinforcement_ids
                    if int(value) in by_id
                    and by_id[int(value)].get("controllable") is True
                    and _num_health(by_id[int(value)]) >= float(
                        attack.get("retreat_health_ratio", 0.35)
                    )
                    and int(value) not in protected_ids
                ),
                key=lambda value: (
                    -float(by_id[value].get("health_ratio", 1.0)), value
                ),
            )
            attack["unit_ids"] = list(sorted(live_ids))
            for unit_id in eligible:
                if unit_id not in attack["unit_ids"] and len(attack["unit_ids"]) < 40:
                    attack["unit_ids"].append(unit_id)
                    attack["reinforcements_added"] = int(
                        attack.get("reinforcements_added", 0)
                    ) + 1
            for unit_id in protected_ids:
                attack.get("assignments", {}).pop(unit_id, None)
                attack.get("last_order_at", {}).pop(unit_id, None)

        # Native building repair is an independent action and must not wait for production.
        for building in damaged_buildings(state):
            building_id = int(building["object_id"])
            key = f"repair:{building_id}"
            if now - self._strategic_order_at.get(key, 0.0) < 15.0:
                continue
            result = self._submit_unit_command_sync(
                state, building_id, "repair_building"
            )
            if result.get("accepted") is True:
                self._strategic_order_at[key] = now

        active_defenders = (
            [unit for unit, _target in counter_pairs]
            if threats else defenders
        )
        for slot, unit in enumerate(active_defenders):
            unit_id = int(unit["object_id"])
            if threats:
                target = counter_targets.get(unit_id)
                if target is None:
                    continue
                key = f"defend:{unit_id}:{int(target['object_id'])}"
                if now - self._strategic_order_at.get(key, 0.0) < 2.0:
                    continue
                result = self._submit_unit_command_sync(
                    state, unit_id, "attack", target_object_id=int(target["object_id"])
                )
            elif isinstance(anchor_cell, dict):
                key = f"defend:{unit_id}"
                if self._distance_sq(unit, {"cell": anchor_cell}) > 11 * 11:
                    if now - self._strategic_order_at.get(key, 0.0) < 8.0:
                        continue
                    cells = staging_cells(anchor_cell, unit.get("cell"), slot=slot)
                    if not cells:
                        continue
                    result = self._submit_unit_command_sync(
                        state, unit_id, "move", target_cell=cells[0]
                    )
                else:
                    if unit.get("mission_id") in {1, 15, 29}:
                        continue
                    if now - self._strategic_order_at.get(key, 0.0) < 12.0:
                        continue
                    result = self._submit_unit_command_sync(state, unit_id, "guard")
            else:
                continue
            if result.get("accepted") is True:
                self._strategic_order_at[key] = now

        safe_buildings = [
            item for item in entities
            if item.get("relation") == "self" and item.get("kind") == "building"
            and item.get("alive") is True and isinstance(item.get("cell"), dict)
        ]
        all_enemies = [
            item for item in entities
            if item.get("relation") == "enemy" and item.get("alive") is True
            and item.get("visible") is True and item.get("kind") in {"infantry", "vehicle", "aircraft"}
        ]
        threatened = {
            int(item["unit_id"]): item
            for item in threatened_harvesters(state, all_enemies)
            if isinstance(item.get("unit_id"), int)
        }
        live_harvesters = {
            int(item["unit_id"]): item
            for item in state.get("harvesters", [])
            if isinstance(item, dict) and isinstance(item.get("unit_id"), int)
            and item.get("alive") is True and item.get("controllable") is True
        }
        for unit_id in list(self._harvester_protection):
            if unit_id not in live_harvesters:
                self._harvester_protection.pop(unit_id, None)

        for unit_id, harvester in threatened.items():
            unit_id = int(harvester["unit_id"])
            key = f"harvester_retreat:{unit_id}"
            protection = self._harvester_protection.get(unit_id)
            if protection is not None:
                protection["last_threat_at"] = now
                protection.pop("safe_since", None)
            if now - self._strategic_order_at.get(key, 0.0) < 10.0 or not safe_buildings:
                continue
            destination = min(
                safe_buildings,
                key=lambda item: (
                    0 if str(item.get("building_class") or "") == "resource" else 1,
                    self._distance_sq(harvester, item),
                ),
            )
            try:
                cargo = float(harvester.get("cargo_ratio", 0.0) or 0.0)
                action = "return" if cargo > 0.05 else "retreat"
                command = self._policy().validate(
                    state,
                    unit_id,
                    action,
                    None if action == "return" else dict(destination["cell"]),
                )
                command["request_id"] = str(uuid.uuid4())
                result = self._client.command_harvester(command)
                if result.get("accepted") is True:
                    self._strategic_order_at[key] = now
                    self._harvester_protection[unit_id] = {
                        "retreated_at": now,
                        "last_threat_at": now,
                        "last_order_at": now,
                        "action": action,
                    }
            except (BridgeError, PolicyError, TypeError, ValueError):
                continue

        # A protection order replaces the native mining mission. Resume mining
        # only after several seconds with no nearby visible enemy.
        for unit_id, protection in list(self._harvester_protection.items()):
            if unit_id in threatened:
                continue
            harvester = live_harvesters.get(unit_id)
            if harvester is None:
                self._harvester_protection.pop(unit_id, None)
                continue
            safe_since = protection.setdefault("safe_since", now)
            if now - float(safe_since) < 4.0:
                continue
            if now - float(protection.get("last_resume_attempt_at", 0.0)) < 6.0:
                continue
            protection["last_resume_attempt_at"] = now
            try:
                selected_id, target = select_nearest_mining_order(state, unit_id)
                command = self._policy().validate(
                    state, selected_id, "mine", target
                )
                command["request_id"] = str(uuid.uuid4())
                result = self._client.command_harvester(command)
                if result.get("accepted") is True:
                    self._harvester_protection.pop(unit_id, None)
                    self._strategic_order_at[f"harvester_resume:{unit_id}"] = now
                    self.logger.info(
                        "Mental Omega harvester resumed mining: unit=%s target=%s",
                        unit_id, target,
                    )
            except (BridgeError, PolicyError, TypeError, ValueError) as exc:
                protection["last_resume_error"] = str(exc)

    def _run_strategic_tech_capture(
        self, state: dict[str, Any], base_task: dict[str, Any]
    ) -> None:
        """Continuously acquire visible neutral tech without stealing combat units."""
        targets = capturable_neutral_tech_buildings(state)
        target_ids = {int(item["object_id"]) for item in targets}
        engineers = owned_engineers(state)
        engineers_by_id = {int(item["object_id"]): item for item in engineers}
        now = time.monotonic()

        for target_id, task in list(self._tech_capture_tasks.items()):
            engineer_id = int(task.get("engineer_id", 0) or 0)
            if target_id not in target_ids:
                task["status"] = "captured_or_unavailable"
                task["completed_at"] = now
                self._tech_capture_tasks.pop(target_id, None)
                continue
            if engineer_id not in engineers_by_id:
                task["status"] = "engineer_lost"
                self._tech_capture_tasks.pop(target_id, None)
                continue
            if now - float(task.get("last_order_at", 0.0)) >= 20.0:
                target = next(
                    (item for item in targets if int(item["object_id"]) == target_id),
                    None,
                )
                if target is not None and capture_target_is_safe(state, target):
                    result = self._submit_unit_command_sync(
                        state, engineer_id, "capture", target_object_id=target_id
                    )
                    task["last_order_at"] = now
                    task["status"] = (
                        "capturing" if result.get("accepted") is True else "retrying"
                    )

        assigned_engineers = {
            int(task["engineer_id"])
            for task in self._tech_capture_tasks.values()
            if isinstance(task.get("engineer_id"), int)
        }
        assignment = choose_capture_assignment(
            state,
            [item for item in targets if int(item["object_id"]) not in self._tech_capture_tasks],
            engineers,
            assigned_engineers,
        )
        if assignment is not None:
            target, engineer = assignment
            target_id = int(target["object_id"])
            engineer_id = int(engineer["object_id"])
            task = self._tech_capture_tasks.get(target_id)
            if task is None or now - float(task.get("last_order_at", 0.0)) >= 20.0:
                result = self._submit_unit_command_sync(
                    state, engineer_id, "capture", target_object_id=target_id
                )
                if result.get("accepted") is True:
                    self._tech_capture_tasks[target_id] = {
                        "target_id": target_id,
                        "target_type_id": str(target.get("type_id") or ""),
                        "engineer_id": engineer_id,
                        "status": "capturing",
                        "created_at": task.get("created_at", now) if task else now,
                        "last_order_at": now,
                    }
                    assigned_engineers.add(engineer_id)
                    base_task["last_reason"] = (
                        f"工程师 {engineer_id} 正在占领中立科技建筑 {target_id}"
                    )
                    self._combat_experiments.record_decision({
                        "task_id": base_task.get("task_id"),
                        "type_id": str(target.get("type_id") or "neutral_tech"),
                        "quantity": 1,
                        "reason": "capture visible neutral tech building",
                        "credits": state.get("player", {}).get("credits"),
                        "variation_index": self._combat_experiments.variation_index,
                    })

        unassigned_targets = [
            item for item in targets
            if int(item["object_id"]) not in self._tech_capture_tasks
        ]
        free_engineers = [
            item for item in engineers
            if int(item["object_id"]) not in assigned_engineers
        ]
        # Keep one reserve engineer during authorized base development. This lets
        # the director react as soon as scouting reveals neutral technology,
        # instead of starting infantry production only after the target appears.
        if not free_engineers:
            self._queue_strategic_engineer(state, base_task)

    def _queue_strategic_engineer(
        self, state: dict[str, Any], base_task: dict[str, Any]
    ) -> None:
        now = time.monotonic()
        if now - self._tech_engineer_last_request_at < 8.0:
            return
        production = state.get("production")
        production = production if isinstance(production, dict) else {}
        queue = production.get("queue")
        queue = queue if isinstance(queue, list) else []
        if any(
            isinstance(item, dict)
            and str(item.get("type_id") or "").upper() in ENGINEER_TYPE_IDS
            and int(item.get("queued_count", 0) or 0) > 0
            for item in queue
        ):
            return
        if any(
            str(task.get("type_id") or "").upper() in ENGINEER_TYPE_IDS
            and task.get("status") not in {"completed", "cancelled", "expired", "failed"}
            for task in self._production_tasks.values()
        ):
            return
        sidebar = production.get("sidebar_available")
        sidebar = sidebar if isinstance(sidebar, list) else []
        available = {
            str(item.get("type_id") or "").upper(): item
            for item in sidebar if isinstance(item, dict) and item.get("type_id")
        }
        selected = next((type_id for type_id in ENGINEER_TYPE_IDS if type_id in available), None)
        if selected is None:
            base_task["tech_capture_status"] = "waiting_for_engineer_unlock"
            return
        profile = unit_profile(selected) or {}
        budget = self._economic_budget(
            state, reserve_override=base_task.get("credit_reserve")
        )
        engineer_cost = max(0, int(profile.get("cost") or 0))
        if not budget.can_spend(engineer_cost):
            base_task["tech_capture_status"] = "waiting_for_engineer_funds"
            return
        self._tech_engineer_last_request_at = now
        try:
            result = self._client.command_production({
                "request_id": str(uuid.uuid4()), "type_id": selected, "quantity": 1,
            })
        except (BridgeError, TypeError, ValueError) as exc:
            base_task["tech_capture_status"] = "engineer_production_retrying"
            base_task["tech_capture_error"] = str(exc)
            return
        accepted = max(0, int(result.get("accepted_count", 0)))
        if accepted <= 0:
            base_task["tech_capture_status"] = "engineer_production_rejected"
            base_task["tech_capture_error"] = str(
                result.get("last_error") or result.get("error") or "production rejected"
            )
            return
        baseline_ids = {
            int(item["object_id"])
            for item in state.get("visible_entities", [])
            if isinstance(item, dict) and item.get("relation") == "self"
            and str(item.get("type_id") or "").upper() == selected
            and isinstance(item.get("object_id"), int)
        }
        task_id = uuid.uuid4().hex[:12]
        self._production_tasks[task_id] = {
            "task_id": task_id, "product": "工程师", "display_name": "工程师",
            "type_id": selected, "kind": "infantry", "requested_count": 1,
            "submitted_count": accepted, "baseline_ids": baseline_ids,
            "observed_ids": set(), "queue_count": 0, "queue_progress_ratio": 0.0,
            "queue_on_hold": False, "last_submit_at": now, "submit_attempts": 1,
            "status": "queued", "created_at": now,
            "expires_at": now + int(self._cfg.get("production_task_seconds", 21600)),
            "economic_reserved_cost": engineer_cost * accepted,
            "economic_reserved_until": now + 3.0,
        }
        base_task["tech_capture_engineer_task_id"] = task_id
        base_task["tech_capture_status"] = "producing_engineer"
        base_task["tech_capture_error"] = None
        self.logger.info(
            "Mental Omega queued engineer for neutral tech capture: task=%s type=%s",
            task_id, selected,
        )

    def _run_strategic_director(
        self, state: dict[str, Any], assessment: Any
    ) -> None:
        """Translate the rules-aware phase into one persistent attack controller.

        This path is enabled only while a commander-authorized base-development task
        exists, so ordinary player control remains untouched outside that scope.
        """
        active_bases = [
            task for task in self._base_tasks.values()
            if task.get("status") not in {"cancelled", "failed", "completed"}
            and task.get("commander_authorized") is True
        ]
        if not active_bases:
            self._strategic_roles = {}
            return
        self._annotate_known_transport_loadouts(state)
        self._run_strategic_tech_capture(state, active_bases[0])
        live_doctrine = (
            self._strategic_director.last_doctrine
            if isinstance(self._strategic_director.last_doctrine, dict) else {}
        )
        live_rule = live_doctrine.get("rule") if isinstance(live_doctrine.get("rule"), dict) else {}
        held_ids = self._strategic_hold_unit_ids(state, active_bases[0])
        committed_attack_ids = {
            int(value)
            for attack in self._attack_tasks.values()
            if attack.get("status") not in {"cancelled", "expired", "completed", "failed"}
            for value in attack.get("unit_ids", [])
        }
        live_roles = partition_force(
            state, live_rule, excluded_ids=held_ids | committed_attack_ids
        )
        live_enemies = [
            item for item in state.get("visible_entities", [])
            if isinstance(item, dict) and item.get("relation") == "enemy"
            and item.get("visible") is True and item.get("alive") is True
        ]
        live_roles["threatened_harvesters"] = threatened_harvesters(
            state, live_enemies
        )
        contact_rule = select_contact_defense_rule(state, live_roles)
        contact_rule_id = str(contact_rule.get("id") or "")
        if contact_rule_id and contact_rule_id != self._last_contact_rule_id:
            self._last_contact_rule_id = contact_rule_id
            self._combat_experiments.record_contact_decision({
                "rule": contact_rule,
                "game_frame": state.get("game", {}).get("frame"),
                "doctrine_id": live_rule.get("id"),
                "defender_ids": [int(item["object_id"]) for item in live_roles.get("defenders", [])],
                "reserve_ids": [int(item["object_id"]) for item in live_roles.get("reserve", [])],
                "assault_ids": [int(item["object_id"]) for item in live_roles.get("assault", [])],
            })
        self._strategic_roles = {
            "doctrine_id": live_roles.get("doctrine_id"),
            "tactical_controls": dict(live_roles.get("tactical_controls", {})),
            "defender_ids": [int(item["object_id"]) for item in live_roles.get("defenders", [])],
            "reserve_ids": [int(item["object_id"]) for item in live_roles.get("reserve", [])],
            "assault_ids": [int(item["object_id"]) for item in live_roles.get("assault", [])],
            "anchor_cells": [dict(item["cell"]) for item in live_roles.get("anchors", []) if isinstance(item.get("cell"), dict)],
            "defense_anchor": dict(live_roles.get("defense_anchor", {})) if isinstance(live_roles.get("defense_anchor"), dict) else None,
            "local_threat_ids": [int(item["object_id"]) for item in live_roles.get("local_threats", [])],
            "assembly_hold_ids": sorted(held_ids),
            "t3_established": live_roles.get("t3_established") is True,
            "contact_rule": contact_rule,
            "updated_at": time.time(),
        }
        active_bases[0]["strategic_roles"] = dict(self._strategic_roles)
        self._run_strategic_response(state, self._strategic_roles)
        attack_authorized = self._strategic_director.should_start_attack(assessment)
        if not attack_authorized:
            live_by_id = {
                int(item["object_id"]): item
                for item in state.get("visible_entities", [])
                if isinstance(item, dict) and isinstance(item.get("object_id"), int)
            }
            for attack in self._attack_tasks.values():
                if (
                    attack.get("strategic_director") is not True
                    or attack.get("status") in {
                        "cancelled", "expired", "completed", "failed"
                    }
                ):
                    continue
                for unit_id in attack.get("unit_ids", []):
                    unit = live_by_id.get(int(unit_id))
                    if not unit or unit.get("controllable") is not True:
                        continue
                    self._submit_unit_command_sync(state, int(unit_id), "stop")
                attack["assignments"] = {}
                attack["last_order_at"] = {}
                attack["status"] = "cancelled"
                attack["completed_at"] = time.monotonic()
                attack["last_reason"] = (
                    "force advantage was lost; regroup before another strike"
                )
        strategic_scouts = [
            task for task in self._adaptive_tasks.tasks(include_finished=False)
            if task.get("kind") == "explore_shroud"
            and task.get("query") == "strategic scout"
        ]
        if not attack_authorized:
            if assessment.enemy_buildings <= 0 and not strategic_scouts:
                scouts = [
                    item for item in state.get("visible_entities", [])
                    if isinstance(item, dict)
                    and item.get("relation") == "self"
                    and item.get("alive") is True
                    and item.get("visible") is True
                    and item.get("controllable") is True
                    and str(item.get("type_id") or "").upper() == "ADOG"
                ][:5]
                if scouts:
                    self._adaptive_tasks.create_explore_task(
                        scouts,
                        query="strategic scout",
                        duration_seconds=int(
                            self._cfg.get("adaptive_explore_task_seconds", 3600)
                        ),
                    )
            return
        for scout in strategic_scouts:
            self._adaptive_tasks.cancel(str(scout["task_id"]))
        if any(
            task.get("status") not in {"cancelled", "expired", "completed", "failed"}
            and task.get("strategic_director") is True
            for task in self._attack_tasks.values()
        ):
            return
        target = self._strategic_director.strategic_target(state)
        if not target:
            return
        doctrine = (
            self._strategic_director.last_doctrine
            if isinstance(self._strategic_director.last_doctrine, dict) else {}
        )
        doctrine_rule = doctrine.get("rule") if isinstance(doctrine.get("rule"), dict) else {}
        held_ids = self._strategic_hold_unit_ids(state, active_bases[0])
        committed_attack_ids = {
            int(value)
            for attack in self._attack_tasks.values()
            if attack.get("status") not in {"cancelled", "expired", "completed", "failed"}
            for value in attack.get("unit_ids", [])
        }
        roles = partition_force(
            state, doctrine_rule, excluded_ids=held_ids | committed_attack_ids
        )
        self._strategic_roles = {
            "doctrine_id": roles.get("doctrine_id"),
            "tactical_controls": dict(roles.get("tactical_controls", {})),
            "defender_ids": [int(item["object_id"]) for item in roles.get("defenders", [])],
            "reserve_ids": [int(item["object_id"]) for item in roles.get("reserve", [])],
            "assault_ids": [int(item["object_id"]) for item in roles.get("assault", [])],
            "anchor_cells": [dict(item["cell"]) for item in roles.get("anchors", []) if isinstance(item.get("cell"), dict)],
            "defense_anchor": dict(roles.get("defense_anchor", {})) if isinstance(roles.get("defense_anchor"), dict) else None,
            "local_threat_ids": [int(item["object_id"]) for item in roles.get("local_threats", [])],
            "assembly_hold_ids": sorted(held_ids),
            "t3_established": roles.get("t3_established") is True,
            "contact_rule": contact_rule,
            "updated_at": time.time(),
        }
        active_bases[0]["strategic_roles"] = dict(self._strategic_roles)
        retreat_health = max(0.1, min(0.9, float(doctrine_rule.get("retreat_health_ratio", 0.45))))
        reserve_fraction = max(0.0, min(0.75, float(doctrine_rule.get("reserve_fraction", 0.2))))
        reserved = {
            int(unit_id)
            for attack in self._attack_tasks.values()
            if attack.get("status") not in {"cancelled", "expired", "completed", "failed"}
            for unit_id in attack.get("unit_ids", [])
        }
        total_assault = (
            assessment.own_strength >= 4000
            and assessment.own_strength >= max(1.0, assessment.enemy_strength) * 1.5
        )
        assault_ids = set(self._strategic_roles.get("assault_ids", []))
        units = [
            item for item in state.get("visible_entities", [])
            if isinstance(item, dict)
            and item.get("relation") == "self"
            and item.get("alive") is True
            and item.get("visible") is True
            and item.get("controllable") is True
            and item.get("kind") in {"infantry", "vehicle", "aircraft"}
            and not is_economic_unit(item)
            and isinstance(item.get("object_id"), int)
            and int(item["object_id"]) in assault_ids
            and int(item["object_id"]) not in reserved
            and _num_health(item) >= retreat_health
        ]
        tactical_controls = doctrine_rule.get("tactical_controls")
        tactical_controls = tactical_controls if isinstance(tactical_controls, dict) else {}
        minimum_assembly = max(
            12 if roles.get("t3_established") is True else 8,
            int(tactical_controls.get("minimum_assembly_count", 8)),
        )
        deploy_count = min(40, len(units))
        if deploy_count < minimum_assembly:
            return
        assault_strength = sum(effective_combat_strength(item) for item in units)
        if assault_strength < max(1800.0, assessment.enemy_strength * 1.35):
            return
        now = time.monotonic()
        task_id = uuid.uuid4().hex[:12]
        unit_ids = [int(item["object_id"]) for item in units[:deploy_count]]
        engagement = str(doctrine_rule.get("engagement") or "concentrated")
        self._attack_tasks[task_id] = {
            "task_id": task_id,
            "query": "rules-aware strategic strike force",
            "target_query": "",
            "target_kind": "",
            "preferred_target_kind": "building",
            "mode": "target",
            "fire_control": "spread" if engagement == "spread_fire" else "concentrated",
            "focus_target_id": int(target["object_id"]),
            "unit_ids": unit_ids,
            "assignments": {},
            "last_order_at": {},
            "created_at": now,
            "expires_at": now + int(self._cfg.get("attack_task_default_seconds", 21600)),
            "duration_seconds": int(self._cfg.get("attack_task_default_seconds", 21600)),
            "orders_issued": 0,
            "failed_orders": 0,
            "status": "engaging",
            "strategic_director": True,
            "phase": assessment.phase,
            "target_class": target.get("building_class"),
            "combat_doctrine_id": doctrine_rule.get("id"),
            "combat_doctrine_actions": list(doctrine_rule.get("actions", [])),
            "combat_doctrine_production_roles": list(doctrine_rule.get("production_roles", [])),
            "formation": doctrine_rule.get("formation"),
            "engagement": engagement,
            "retreat_health_ratio": retreat_health,
            "reserve_fraction": reserve_fraction,
            "tactical_controls": dict(tactical_controls),
            "wave_size": deploy_count,
            "total_assault": total_assault,
            "force_ratio_at_launch": round(
                assessment.own_strength / max(1.0, assessment.enemy_strength), 2
            ),
            "assault_strength_at_launch": round(assault_strength, 2),
            "reinforcements_added": 0,
            "contact_engagements": 0,
            "contact_defense_rule_id": contact_rule.get("id"),
        }
        active_bases[0]["strategic_attack_task_id"] = task_id
    def _handle_base_under_attack_interrupt(
        self, payload: dict[str, Any], state: dict[str, Any]
    ) -> None:
        """High-priority path: pre-empt ordinary plan work with visible defence."""
        alert = payload.get("alert")
        if not isinstance(alert, dict):
            return
        if self._autonomy_enabled:
            self._run_autonomous_commander(state)
        if not self._under_attack_reports_enabled:
            return
        event_id = alert.get("event_id")
        if self._report_policy.allow(
            "base_under_attack",
            key=f"alert:{event_id}",
            severity=int(alert.get("severity", 0)),
        ):
            try:
                self._speaker.speak(
                    describe_alerts(self._report_context({**state, "alerts": [alert]}))[0]["summary"],
                    {
                        "kind": "mental_omega_interrupt",
                        "interrupt": "base_under_attack",
                        "event_id": alert.get("event_id"),
                    },
                )
            except (IndexError, ProactiveReportError) as exc:
                self.logger.warning("Mental Omega interrupt report failed: %s", exc)

    def _handle_flag_changed_interrupt(
        self, payload: dict[str, Any], state: dict[str, Any]
    ) -> None:
        """Wake dependent plans immediately on a rising task-completion edge."""
        if payload.get("value") is True and self._order_plans.active_count:
            self._order_plans.tick(state)

    def _on_plan_order_transition(
        self,
        plan: dict[str, Any],
        order: dict[str, Any],
        previous: str,
        current: str,
    ) -> None:
        """Publish every plan transition through the same writable flag bus."""
        prefix = f"plan:{plan['plan_id']}:order:{order['order_id']}"
        priority = 90 if current in {"succeeded", "failed", "skipped"} else 50
        self._command_runtime.flags.set(
            f"{prefix}:status", current, source="order_plan", priority=priority
        )
        self._command_runtime.flags.set(
            f"{prefix}:{current}", True, source="order_plan", priority=priority
        )
        self._command_runtime.flags.set(
            f"plan:{plan['plan_id']}:last_transition",
            {
                "order_id": order["order_id"],
                "action": order["action"],
                "from": previous,
                "to": current,
            },
            source="order_plan",
            priority=priority,
        )
        if plan.get("status") in {"succeeded", "failed", "cancelled"}:
            self._command_runtime.flags.set(
                f"plan:{plan['plan_id']}:{plan['status']}",
                True,
                source="order_plan",
                priority=95,
            )

    def _emit_watch_report(
        self, watch_id: str, event_kind: str, line: str, *,
        key: str | None = None, severity: int = 0,
    ) -> None:
        if not self._report_policy.allow(
            event_kind, key=key or f"watch:{watch_id}:{event_kind}", severity=severity
        ):
            return
        try:
            self._speaker.speak(
                line,
                {
                    "kind": "mental_omega_unit_watch",
                    "transition": event_kind,
                    "watch_id": watch_id,
                },
            )
        except ProactiveReportError as exc:
            self.logger.warning("Mental Omega proactive report failed: %s", exc)

    def _watch_duration(self, value: Any) -> int:
        if value is None:
            value = self._cfg.get("watch_default_seconds", 21600)
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("duration_seconds must be an integer")
        return max(30, min(value, 21600))

    def _attack_duration(self, value: Any) -> int:
        if value is None:
            value = self._cfg.get("attack_task_default_seconds", 21600)
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("duration_seconds must be an integer")
        return max(30, min(value, 21600))

    def _watch_poll_seconds(self) -> float:
        return max(0.05, min(float(self._cfg.get("watch_poll_seconds", 0.1)), 10.0))

    def _bridge_health_with_recovery(self) -> dict[str, Any]:
        try:
            return self._client.health()
        except BridgeError as initial_error:
            if self._cfg.get("auto_start_bridge", True) is not True:
                raise
            now = time.monotonic()
            retry_seconds = max(
                2.0,
                min(float(self._cfg.get("bridge_restart_retry_seconds", 5.0)), 60.0),
            )
            if (
                self._bridge_last_restart_at is not None
                and now - self._bridge_last_restart_at < retry_seconds
            ):
                raise
            self._bridge_last_restart_at = now
            try:
                started = self._bridge_runtime.ensure_started(
                    self._client.base_url,
                    timeout_seconds=min(retry_seconds, 5.0),
                )
                if started:
                    self._bridge_restart_count += 1
                    self.logger.info("Mental Omega local bridge recovered")
                return self._client.health()
            except (BridgeError, EmbeddedBridgeError) as recovery_error:
                self.logger.warning(
                    "Mental Omega local bridge recovery failed: %s", recovery_error
                )
                raise initial_error

    def _refresh_economic_personality(self, force: bool = False) -> None:
        now = time.monotonic()
        if (
            not force
            and now - self._economic_personality_updated_at
            < PERSONALITY_REFRESH_SECONDS
        ):
            return
        self._economic_personality_updated_at = now
        base_url = str(
            self._cfg.get("neko_main_url", "http://127.0.0.1:48911")
        )
        try:
            current = load_current_character(base_url, timeout=1.0)
        except (OSError, TimeoutError, ValueError) as exc:
            self.logger.debug("Could not refresh economic personality: %s", exc)
            return
        if current is None:
            return
        name, character = current
        personality = infer_economic_personality(character)
        changed = (
            name != self._economic_character_name
            or personality != self._economic_personality
        )
        self._economic_character_name = name
        self._economic_personality = personality
        if changed:
            self.logger.info(
                "Mental Omega economic personality: catgirl=%s mode=%s reserve=%s",
                name, personality.mode, personality.reserve_floor,
            )

    def _pending_economic_cost(self, now: float | None = None) -> int:
        now = time.monotonic() if now is None else float(now)
        total = 0
        for records in (self._production_tasks.values(), self._force_tasks.values()):
            for record in records:
                if float(record.get("economic_reserved_until", 0.0)) <= now:
                    continue
                total += max(0, int(record.get("economic_reserved_cost", 0)))
        return total

    @staticmethod
    def _economic_emergency(state: dict[str, Any]) -> bool:
        economy = state.get("economy")
        if isinstance(economy, dict):
            surplus = economy.get("power_surplus")
            if isinstance(surplus, int) and not isinstance(surplus, bool) and surplus < 0:
                return True
        return any(
            isinstance(alert, dict)
            and alert.get("kind") == "under_attack"
            and int(alert.get("severity") or 0) >= 2
            for alert in state.get("alerts", [])
        )

    def _economic_budget(
        self,
        state: dict[str, Any],
        *,
        reserve_override: int | None = None,
        emergency: bool | None = None,
    ) -> EconomicBudget:
        player = state.get("player")
        economy = state.get("economy")
        credits = player.get("credits") if isinstance(player, dict) else None
        if not isinstance(credits, int) or isinstance(credits, bool):
            credits = economy.get("credits") if isinstance(economy, dict) else None
        return build_economic_budget(
            credits if isinstance(credits, int) and not isinstance(credits, bool) else 0,
            self._economy_tracker.report(),
            self._economic_personality,
            pending_cost=self._pending_economic_cost(),
            reserve_override=reserve_override,
            emergency=(
                self._economic_emergency(state) if emergency is None else bool(emergency)
            ),
        )

    def _watch_near_distance(self) -> float:
        return max(1.0, float(self._cfg.get("watch_near_distance_cells", 25)))

    def _watch_critical_distance(self) -> float:
        configured = max(
            1.0, float(self._cfg.get("watch_critical_distance_cells", 12))
        )
        return min(configured, self._watch_near_distance())

    def _runtime_status(self) -> dict[str, Any]:
        thread = self._watch_thread
        world = self._world_model.summary()
        return {
            "watch_thread_alive": thread is not None and thread.is_alive(),
            "watch_started_at": self._watch_started_at,
            "watch_last_poll_at": self._watch_last_poll_at,
            "watch_last_error": self._watch_last_error,
            "bridge_last_restart_at": self._bridge_last_restart_at,
            "bridge_restart_count": self._bridge_restart_count,
            "focus_enabled": self._cfg.get("mental_omega_focus_enabled", True) is True,
            "focus_active": self._focus.active,
            "unit_watch_count": len(self._watches),
            "alert_watch_count": len(self._alert_watches),
            "attack_task_count": len(self._attack_tasks),
            "load_task_count": len(self._load_tasks),
            "production_task_count": len(self._production_tasks),
            "strategic_roles": dict(self._strategic_roles),
            "tech_capture_task_count": len(self._tech_capture_tasks),
            "tech_capture_tasks": [
                {
                    "target_id": task.get("target_id"),
                    "target_type_id": task.get("target_type_id"),
                    "engineer_id": task.get("engineer_id"),
                    "status": task.get("status"),
                    "last_order_at": task.get("last_order_at"),
                }
                for task in self._tech_capture_tasks.values()
            ],
            "base_development_task_count": sum(
                task.get("status") not in {"cancelled", "failed", "completed"}
                for task in self._base_tasks.values()
            ),
            "force_maintenance_task_count": sum(
                task.get("status") != "cancelled"
                for task in self._force_tasks.values()
            ),
            "area_defense_task_count": sum(
                task.get("status") not in {"cancelled", "failed"}
                for task in self._defense_tasks.values()
            ),
            "adaptive_task_count": self._adaptive_tasks.active_count,
            "order_plan_count": self._order_plans.active_count,
            "intent_plan_count": len(self._intent_plans.active_ids()),
            "autonomous_reasoning_enabled": self._cfg.get("autonomous_reasoning_enabled", True) is True,
            "autonomous_reasoning_nudge_count": self._autonomy_nudge_count,
            "autonomous_reasoning_nudge_supported": self._autonomy_nudge_supported,
            "autonomous_adjutant_enabled": self._autonomy_enabled,
            "interrupt_queue_depth": self._command_runtime.interrupts.pending,
            "timer_count": self._command_runtime.timers.active,
            "dma": self._command_runtime.dma.snapshot(),
            "runtime_flag_count": len(self._command_runtime.flags.snapshot()),
            "report_policy": self._report_policy.snapshot(),
            "economy_sample_count": self._economy_tracker.sample_count,
            "economic_character": self._economic_character_name,
            "economic_personality": self._economic_personality.public(),
            "control_group_count": len(self._groups),
            "world_model": {
                "revision": world.get("revision"),
                "updated_at": world.get("updated_at"),
                "source_frame": world.get("source_frame"),
                "running": world.get("running"),
                "remembered_entity_count": world.get("remembered_entity_count"),
                "telemetry": world.get("telemetry"),
            },
        }

    @staticmethod
    def _public_attack_task(task: dict[str, Any]) -> dict[str, Any]:
        return {
            "task_id": task["task_id"],
            "query": task.get("query"),
            "target_query": task.get("target_query"),
            "fire_control": task.get("fire_control", "auto"),
            "unit_count": len(task.get("unit_ids", [])),
            "status": task.get("status"),
            "orders_issued": task.get("orders_issued", 0),
            "failed_orders": task.get("failed_orders", 0),
            "remaining_seconds": max(
                0, round(task["expires_at"] - time.monotonic())
            ),
        }

    @staticmethod
    def _public_watch(watch: dict[str, Any]) -> dict[str, Any]:
        return {
            "watch_id": watch["watch_id"],
            "query": watch["query"],
            "resolved_unit": watch.get("resolved_unit"),
            "remaining_seconds": max(
                0, round(watch["expires_at"] - time.monotonic())
            ),
            "status": watch.get("status"),
            "last_event": watch.get("last_event"),
            "last_report": watch.get("last_report"),
        }

    @staticmethod
    def _public_alert_watch(watch: dict[str, Any]) -> dict[str, Any]:
        return {
            "watch_id": watch["watch_id"],
            "watch_type": "alert",
            "target": watch.get("anchor", {}).get("label"),
            "anchor": dict(watch.get("anchor", {})),
            "match_mode": watch.get("match_mode"),
            "radius_cells": watch.get("radius_cells"),
            "remaining_seconds": max(
                0, round(watch["expires_at"] - time.monotonic())
            ),
            "last_event": watch.get("last_event"),
            "last_report": watch.get("last_report"),
        }

    @staticmethod
    def _visible_force_summary(state: dict[str, Any]) -> dict[str, Any]:
        entities = state.get("visible_entities")
        counts: dict[str, dict[str, int]] = {"self": {}, "ally": {}, "enemy": {}}
        if isinstance(entities, list):
            for item in entities:
                if not isinstance(item, dict) or item.get("visible") is not True:
                    continue
                relation = str(item.get("relation"))
                if relation not in counts:
                    continue
                name = str(item.get("display_name") or item.get("type_id") or "未知目标")
                counts[relation][name] = counts[relation].get(name, 0) + 1
        return {
            relation: [
                {"name": name, "count": count}
                for name, count in sorted(
                    values.items(), key=lambda pair: (-pair[1], pair[0])
                )
            ]
            for relation, values in counts.items()
        }

    @staticmethod
    def _select_harvesters(
        state: dict[str, Any], unit_ids: Any, count: Any
    ) -> list[dict[str, Any]]:
        player = state.get("player", {})
        house_id = player.get("house_id") if isinstance(player, dict) else None
        harvesters = state.get("harvesters")
        if not isinstance(harvesters, list):
            raise PolicyError("harvester list is missing")
        candidates = [
            item for item in harvesters
            if isinstance(item, dict)
            and item.get("owner_house_id") == house_id
            and item.get("alive") is True
            and item.get("controllable") is True
            and isinstance(item.get("cell"), dict)
        ]
        if unit_ids is not None:
            if not isinstance(unit_ids, list) or not unit_ids or any(
                isinstance(value, bool) or not isinstance(value, int) for value in unit_ids
            ):
                raise PolicyError("unit_ids must be a non-empty integer list")
            by_id = {item.get("unit_id"): item for item in candidates}
            requested = list(dict.fromkeys(unit_ids))
            missing = [value for value in requested if value not in by_id]
            if missing:
                raise PolicyError(f"requested harvesters are not controllable: {missing}")
            candidates = [by_id[value] for value in requested]
        if count is not None:
            if isinstance(count, bool) or not isinstance(count, int) or count < 1:
                raise PolicyError("count must be a positive integer")
            candidates = candidates[:count]
        if not candidates:
            raise PolicyError("no controllable player harvester was found")
        return candidates

    def _report_context(self, state: dict[str, Any]) -> dict[str, Any]:
        context = dict(state)
        context["reporting"] = {
            "base_radius_cells": int(self._cfg.get("base_report_radius_cells", 40)),
        }
        return context

    @staticmethod
    def _summarize(state: dict[str, Any]) -> str:
        game = state.get("game", {})
        if not isinstance(game, dict) or not game.get("running"):
            return "Mental Omega is not in a running match"
        harvesters = state.get("harvesters", [])
        count = len(harvesters) if isinstance(harvesters, list) else 0
        player = state.get("player", {})
        credits = player.get("credits") if isinstance(player, dict) else None
        return f"{game.get('mode', 'unknown')} match; {count} harvesters; credits={credits}"
