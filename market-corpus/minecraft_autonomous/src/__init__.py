"""Explicitly started N.E.K.O decisions and one independent Minecraft body."""

import asyncio
import json
import threading
import uuid

from agent_mc_autonomy.configuration import Settings
from agent_mc_autonomy.mailbox import Mailbox, observation
from agent_mc_autonomy.runner import run_session
from agent_mc_autonomy.viewer import viewer_observation
from agent_mc_autonomy.worker import Worker
from plugin.sdk.plugin import Err, NekoPluginBase, Ok, TransportError, lifecycle, neko_plugin, plugin_entry

from .backend.install import verify_runtime
from .bundled_appearance import default_bundled_appearance_settings as default_bundled_appearance_settings
from .dashboard import Dashboard
from .direct_planner import create_planner
from .neko_heartbeat import NekoHeartbeat
from .neko_narration import NekoNarration
from .neko_presentation import NekoPresentation
from .neko_tools import NekoAutonomyTools
from .prepared_runtime import require_prepared_runtime, resolve_host_runtime
from .qiandengji_account import AccountError
from .qiandengji_account import preflight as qiandengji_preflight
from .qiandengji_panel import QiandengjiPanel


@neko_plugin
class MinecraftAutonomousPlugin(Dashboard, QiandengjiPanel, NekoAutonomyTools, NekoHeartbeat, NekoPresentation, NekoNarration, NekoPluginBase):
    def __init__(self, ctx):
        super().__init__(ctx)
        self._control_lock = threading.RLock()
        self._generation = 0
        self._configuration_writes = 0
        self._worker = None

    def _operator_state(self, stopped):
        path = self.data_path("operator.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"explicit_stop": stopped}), encoding="utf-8")
        temporary.replace(path)

    @lifecycle(id="startup")
    async def on_startup(self, **_):
        return await self.status()

    @lifecycle(id="shutdown")
    async def on_shutdown(self, **_):
        settings = Settings.parse(await self.config.dump())
        if settings.runtime_mode == "external":
            return await self.status()
        return await self.stop()

    @plugin_entry(
        id="status",
        name="自主游玩状态 / Autonomous play status / 自律プレイ状態",
        description="只读报告后端、Minecraft 连接、当前动作与自主决策状态。",
        kind="service",
    )
    async def status(self, metadata_only: bool = False, **_):
        state = {"phase": "stopped", "game_connected": False, "autonomy_running": False, "worker_alive": False}
        with self._control_lock:
            if self._worker:
                state.update(self._worker.snapshot())
        ready, reason, viewer = False, "backend_missing", ""
        try:
            effective, prepared = resolve_host_runtime(await self.config.dump())
            settings = Settings.parse(effective)
            if settings.runtime_mode == "external":
                if not settings.data_directory:
                    return Err({"code": "external_data_directory_required", "message": "填写与 standalone 相同的 runtime.data_directory。"})
                directory = settings.data_root(self.data_path()) / "sessions" / settings.identity / "host"
                snapshot = observation(directory)
                return Ok({"mode": "external", "minecraft_version": "1.21.1", "observation": snapshot,
                           **viewer_observation(settings, snapshot)})
            viewer = settings.external_viewer_url
            if prepared in {"missing", "incompatible"}:
                reason = "backend_missing" if prepared == "missing" else "runtime_bundle_incompatible"
            elif metadata_only:
                reason = "metadata_only_not_runtime_verified"
            else:
                from agent_mc_autonomy.readiness import installation_readiness
                metadata = installation_readiness(settings, self.cache_path())
                ready = metadata["backend"] == "version_matches_not_integrity_verified"
                reason = "metadata_matches_not_integrity_verified" if ready else "backend_missing"
        except (OSError, ValueError, KeyError):
            pass
        except TransportError:
            reason = "configuration_unavailable"
        return Ok({"plugin_id": "minecraft_autonomous", "mode": "autonomous", "stage": "development",
                   "minecraft_version": "1.21.1", "backend_ready": ready, "reason": reason,
                   "viewer_url": viewer, "viewer_body_binding_verified": False, "runtime_integrity_verified": False, **state,
                   **(viewer_observation(settings, state) if ready else {})})

    @plugin_entry(
        id="start",
        name="开始自主游玩 / Start autonomous play / 自律プレイ開始",
        description="按目标启动 Minecraft 1.21.1 自主游玩；需要启用配置、安装后端并绑定 decision 模型。返回受理状态，连接结果请查 status。",
    )
    async def start(self, goal: str = "survive", **_):
        with self._control_lock:
            if self._worker and self._worker.snapshot()["worker_alive"]:
                return Ok(self._worker.snapshot())
            self._generation += 1
            generation = self._generation
        try:
            if not isinstance(goal, str) or not goal.strip() or len(goal) > 4000:
                raise ValueError("invalid_goal: goal must contain 1..4000 characters")
            planner_raw, prepared = resolve_host_runtime(await self.config.dump())
            settings = Settings.parse(planner_raw)
            if settings.runtime_mode == "external":
                return Err({"code": "external_runtime", "message": "此入口只观察和投递任务。请显式启动 standalone.py run；Neko 不接管外部运行进程。"})
            if not settings.enabled:
                return Err({"code": "autonomy_disabled", "message": "先在配置中启用 autonomy；插件不会自动进入游戏。"})
            settings.require_server()
            await qiandengji_preflight(planner_raw, settings)
            require_prepared_runtime(prepared)
            entry = verify_runtime(settings.runtime_directory(self.cache_path()))
            data_root = settings.data_root(self.data_path())
            data = data_root / "sessions" / settings.identity / "host"

            from agent_mc_autonomy.model_config import planner_settings
            selected = planner_settings(planner_raw.get("planner", {}))
            if selected:
                selected.key()
                selected.validate_budget(settings.max_output_tokens)

            async def play(goal, publish, stopped):
                await run_session(settings, entry, data_root, create_planner(planner_raw, getattr(self.ctx, "models", None), settings),
                                  goal, publish, stopped)

            with self._control_lock:
                if generation != self._generation:
                    return Err({"code": "start_cancelled", "message": "启动期间已收到停止指令。"})
                data.mkdir(parents=True, exist_ok=True)
                self._operator_state(False)
                self._worker = Worker(play, data / "host-error.log")
                self._worker.start(goal.strip())
                return Ok({"accepted": True, **self._worker.snapshot()})
        except AccountError:
            return Err({"code": "qiandengji_account_not_ready", "message": "已导入的千灯纪身份尚未通过本次 ready 核对；请查询原身份状态，未启动游戏。"})
        except (OSError, ValueError, KeyError) as error:
            return Err({"code": "configuration_error", "message": str(error)})
        except TransportError:
            return Err({"code": "configuration_unavailable", "message": "宿主配置接口暂不可用，未启动游戏。"})

    @plugin_entry(
        id="stop",
        name="停止自主游玩 / Stop autonomous play / 自律プレイ停止",
        description="停止决策并断开插件拥有的 Minecraft 连接，保留账本；可重复调用。",
        timeout=30,
    )
    async def stop(self, request_id: str = "", **_):
        with self._control_lock:
            self._generation += 1
            self._operator_state(True)
            worker = self._worker
        stopped = await asyncio.to_thread(worker.stop) if worker else True
        if not stopped:
            return Err({"code": "stop_pending", "message": "停止指令已生效，资源仍在清理；新的启动会被拒绝。"})
        settings = Settings.parse(await self.config.dump())
        if settings.runtime_mode == "external":
            return await self._submit("stop", {}, request_id or str(uuid.uuid4()))
        return await self.status()

    async def _submit(self, kind, payload, request_id):
        try:
            settings = Settings.parse(await self.config.dump())
            if kind != "stop" and not settings.enabled:
                return Err({"code": "autonomy_disabled", "message": "自主框架未启用，未投递指令。"})
            if settings.runtime_mode == "external" and not settings.data_directory:
                raise ValueError("external_data_directory_required")
            settings.require_server()
            directory = settings.data_root(self.data_path()) / "sessions" / settings.identity / "host"
            mailbox = Mailbox(directory)
            if payload.get("origin") == "user" and kind in {"goal", "feedback"}:
                return Ok(mailbox.submit_user_intent(kind, {key: value for key, value in payload.items() if key != "origin"}, request_id))
            return Ok(mailbox.submit(kind, payload, request_id))
        except (OSError, ValueError) as error:
            return Err({"code": "command_rejected", "message": str(error)})

    @plugin_entry(id="submit_steps", name="插入原生任务 / Insert native task / タスク追加",
                  description="投递已规划的 mc_do.steps。默认 append；afterCheckpoint 安全检查点插入；now 明确抢占。必须提供 UUID request_id，重试复用原 ID。投递成功不等于原生受理或执行完成。")
    async def submit_steps(self, steps: list[dict], request_id: str, queue: str = "append", **_):
        return await self._submit("steps", {"steps": steps, "queue": queue}, request_id)

    @plugin_entry(id="submit_goal", name="加入自主目标 / Add autonomous goal / 目標追加",
                  description="投递自然语言目标，由独立规划器在当前目标及原生动作结束后接续。Neko 可继续解说。request_id 必须是 UUID，确认不明时复用原 ID。需要外部规划模型或 embedded decision 模型。")
    async def submit_goal(self, goal: str, request_id: str, **_):
        return await self._submit("goal", {"goal": goal}, request_id)

    @plugin_entry(id="feedback", name="更新当前意图 / Update current intent / 方針変更",
                  description="用户新反馈优先于旧规划。mode=insert：在原生安全检查点插入，之后恢复原工作；mode=replace：明确停止并清空旧原生队列，改为新目标。必须明确选择模式；复用同一个 UUID request_id 查询或重试，语音/解说不走游戏队列。")
    async def feedback(self, goal: str, mode: str, request_id: str, **_):
        return await self._submit("feedback", {"goal": goal, "mode": mode}, request_id)

    @plugin_entry(id="task_receipt", name="任务回执 / Task receipt / 実行結果", kind="service",
                  description="只读查询投递状态与原生回执。请结合 status 的原生队列与采样时间解说；不把受理称为完成。")
    async def task_receipt(self, request_id: str, **_):
        settings = Settings.parse(await self.config.dump())
        directory = settings.data_root(self.data_path()) / "sessions" / settings.identity / "host"
        return Ok({"command": Mailbox(directory).get(request_id)})
