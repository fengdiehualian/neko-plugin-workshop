"""运行时：装配遥测、事件引擎、仲裁、推送、记忆。"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional

from ..adapters.llm_client import LLMProvider
from ..adapters.map_parser import MapParser
from ..adapters.push_sender import PushSender
from ..adapters.telemetry_client import TelemetryReader
from ..adapters.telemetry_installer import TelemetryInstaller
from ..catgirl.bridge import CatgirlBridge
from .arbiter import Arbiter
from .challenge import Challenge
from .config_model import PawpilotConfig
from .event_engine import EventEngine, TruckEvent
from .knowledge import KnowledgeBase
from .ledger import Ledger
from .level_celebrate import LevelCelebrate
from .map_kb import MapKnowledge
from .memory import MemoryStore
from .mood import Persona
from .photo_album import PhotoAlbum
from .pilot import CatPilot
from .proactive import Proactive
from .profile import DriverProfile
from .recall import Recall
from .route_planner import RoutePlanner
from .safety_guard import SafetyGuard
from .scene_chat import SceneChat
from .small_talk import SmallTalk
from .templates import EmotionRenderer
from .trip_summary import TripSummary

ACTIVITY_TITLES = {
    "speeding": "超速",
    "hard_brake": "急刹",
    "crash": "车祸",
    "job_start": "接单",
    "job_delivered": "到货",
    "job_cancelled": "取消任务",
    "fine": "罚款",
    "tollgate": "过收费站",
    "refuel": "加油",
    "trailer_attach": "挂上挂车",
    "trailer_detach": "卸下挂车",
    "time_warning": "交付时间警告",
    "time_over": "超时",
    "low_fuel": "低油量",
    "game_start": "进入游戏",
    "game_end": "退出游戏",
    "trip_progress": "行程进度",
    "distance_mark": "距离预告",
    "time_relaxed": "时间充裕",
    "time_tight": "时间紧张",
    "early_arrival": "提前到货",
    "cargo_damage": "货物受损",
}


class PawpilotRuntime:
    """插件运行时。"""

    def __init__(self, plugin: Any, config: PawpilotConfig) -> None:
        self.plugin = plugin
        self.cfg = config
        host_persona = getattr(plugin, "persona", None)
        self.persona = Persona(host_persona)
        self.catgirl = CatgirlBridge()
        self.llm = LLMProvider()
        self.emotion = EmotionRenderer(self.persona, llm=self.llm)
        self.engine = EventEngine(config)
        self.safety = SafetyGuard(config)
        self.arbiter = Arbiter(config, self.safety)
        self.arbiter.broadcast_categories = dict(config.broadcast_categories)
        self.arbiter.broadcast_frequency = config.broadcast_frequency
        self.push = PushSender(plugin, dry_run=config.dry_run)
        self.memory = MemoryStore(plugin.store)
        self.recall = Recall(self.memory)
        self.ledger = Ledger(self.memory)
        self.challenge = Challenge(self.memory)
        self.trip_summary = TripSummary({})
        self.knowledge = KnowledgeBase()
        self.map_kb = MapKnowledge()
        self.proactive = Proactive(config, map_kb=self.map_kb)
        self.small_talk = SmallTalk(persona=self.persona)
        self.pilot = CatPilot(persona=self.persona)
        self.profile = DriverProfile(self.memory)
        self.scene_chat = SceneChat()
        self.route_planner = RoutePlanner(self.knowledge)
        self.level_celebrate = LevelCelebrate(self.memory)
        self.photo_album = PhotoAlbum(plugin)
        self.telemetry_installer = TelemetryInstaller(
            plugin_rel=config.telemetry_plugin_rel,
            bundle_rel=config.telemetry_bundle_rel)
        self.telemetry_install_state: Dict[str, str] = {}
        self.map_parser = MapParser(plugin)
        self._game_dir: Optional[str] = None
        self.ocr_regions = self._load_ocr_regions()
        self.hud_ocr = None  # 惰性：OCR 可用时创建
        self._ocr_interval = float(self.ocr_regions.get("scan_interval_s", 600))
        self._last_ocr_at = 0.0
        self._tick_task: Optional[asyncio.Task] = None
        self._propose_task: Optional[asyncio.Task] = None
        self._bg_thread: Optional[Any] = None
        self._bg_stop: Optional[Any] = None
        self._bg_loop_ref: Optional[Any] = None
        self._job_crashes = 0
        self._job_speedings = 0
        self._job_hard_brakes = 0
        self._job_refuels = 0
        self._job_fines = 0.0
        self._job_tolls = 0.0
        self._job_fuel_avg_sum = 0.0
        self._job_fuel_avg_n = 0
        self._job_start_ts = 0.0
        self._job_night = 0
        self._trip_start: Optional[TruckEvent] = None
        self._last_snap: Any = None
        self._game_running: bool = False
        self._activity: list = []
        self._bg_tasks: set = set()

    def _spawn(self, coro) -> None:
        """创建后台任务并跟踪，防泄漏；异常打日志。

        必须在 runtime 自己的后台循环（_bg_loop）里调用；宿主 lifecycle/entry
        的 loop 与后台线程不同，跨 loop create_task 会抛 RuntimeError
        （照 neko_fishpower 线程改造）。
        """
        loop = self._bg_loop_ref
        if loop is None or not loop.is_running():
            self.plugin.logger.warning("_spawn 无后台循环，丢弃任务")
            return
        task = asyncio.run_coroutine_threadsafe(coro, loop)
        self._bg_tasks.add(task)

        def _log_err(t):
            try:
                t.result()
            except (asyncio.CancelledError, Exception) as exc:
                if not isinstance(exc, asyncio.CancelledError):
                    self.plugin.logger.warning("bg task error: %s", exc)
        task.add_done_callback(_log_err)
        task.add_done_callback(self._bg_tasks.discard)

    def _ui_settings_path(self):
        from pathlib import Path
        return Path(__file__).resolve().parent.parent / "data" / "config" / "ui_settings.json"

    def _ui_settings(self) -> dict:
        """读 ui_settings.json，缺失返回空。"""
        import json
        try:
            data = json.loads(self._ui_settings_path().read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    async def settings_save(self) -> None:
        """持久化面板可调设置到 data/config/ui_settings.json（原子写）。"""
        import json
        import os
        data = self._ui_settings()
        data.update({
            "dry_run": self.cfg.dry_run,
            "voice_styles": list(self.persona.voice_styles),
            "broadcast_frequency": self.arbiter.broadcast_frequency,
            "broadcast_categories": dict(self.arbiter.broadcast_categories),
        })
        path = self._ui_settings_path()
        tmp = path.with_suffix(".json.tmp")
        try:
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                           encoding="utf-8")
            os.replace(tmp, path)
        except OSError as exc:
            self.plugin.logger.warning("settings_save failed: %s", exc)

    async def settings_load(self) -> None:
        """启动时从 data/config/ui_settings.json 恢复面板设置。"""
        saved = self._ui_settings()
        if not saved:
            return
        style = saved.get("voice_styles")
        if isinstance(style, list) and style:
            self.persona.set_voice_styles(style)
        else:
            # 兼容旧版单值 voice_style
            old = saved.get("voice_style")
            if old:
                self.persona.set_voice_style(old)
        if "dry_run" in saved:
            self.set_dry_run(bool(saved["dry_run"]))
        if saved.get("broadcast_frequency"):
            self.set_frequency(saved["broadcast_frequency"])
        cats = saved.get("broadcast_categories")
        if isinstance(cats, dict):
            for k in self.arbiter.broadcast_categories:
                if k in cats:
                    self.arbiter.broadcast_categories[k] = bool(cats[k])

    def _load_ocr_regions(self) -> dict:
        import json
        from pathlib import Path
        p = Path(__file__).resolve().parent.parent / "data" / "config" / "ocr_regions.json"
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def set_dry_run(self, value: bool) -> None:
        self.cfg.dry_run = bool(value)
        self.push.dry_run = bool(value)

    def apply_config(self, config: PawpilotConfig) -> None:
        """配置热更新（config_change 生命周期）。"""
        self.cfg = config
        self.push.dry_run = config.dry_run
        self.arbiter.broadcast_categories = dict(config.broadcast_categories)
        self.arbiter.broadcast_frequency = config.broadcast_frequency
        self.engine.apply_config(config)

    def pause(self) -> None:
        self.safety.pause()

    def resume(self) -> None:
        self.safety.resume()

    def set_frequency(self, frequency: str) -> bool:
        from .event_catalog import BROADCAST_FREQUENCIES
        if frequency not in BROADCAST_FREQUENCIES:
            return False
        self.arbiter.broadcast_frequency = frequency
        return True

    def set_category(self, category: str, enabled: bool) -> bool:
        if category not in self.arbiter.broadcast_categories:
            return False
        self.arbiter.broadcast_categories[category] = bool(enabled)
        return True

    def llm_config(self) -> dict:
        """读 LLM 配置（ui_settings 的 llm 段）。"""
        data = self._ui_settings()
        llm = data.get("llm") if isinstance(data, dict) else {}
        return llm if isinstance(llm, dict) else {}

    async def save_llm_config(self, config: dict) -> bool:
        """保存 LLM 配置并热应用（配置优先，未配置降级模板）。"""
        import json
        import os
        data = self._ui_settings()
        cfg = {k: str(config.get(k, "") or "").strip()
               for k in ("provider", "model", "api_key", "base_url")}
        data["llm"] = cfg
        path = self._ui_settings_path()
        tmp = path.with_suffix(".json.tmp")
        try:
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                           encoding="utf-8")
            os.replace(tmp, path)
        except OSError as exc:
            self.plugin.logger.warning("save_llm_config failed: %s", exc)
            return False
        self._wire_llm()
        return True

    async def start(self) -> Dict[str, Any]:
        if not self.cfg.enabled:
            return {"status": "disabled"}
        await self.memory.load()
        self.profile.load()
        self.level_celebrate.load()
        await self.settings_load()
        self._wire_llm()
        templates = self.emotion._short_lines
        self.trip_summary = TripSummary(templates)
        self.engine.on_event(self._on_event)
        # 后台循环必须跑在独立 daemon 线程里：宿主 lifecycle startup 用
        # asyncio.run()（临时 loop），create_task 的后台任务会在返回后被
        # 取消（照 neko_fishpower 线程改造，进度/事件播报依赖 tick_loop）。
        import threading
        self._bg_stop = threading.Event()
        self._bg_thread = threading.Thread(
            target=self._bg_runner, daemon=True, name="pawpilot-bg")
        self._bg_thread.start()
        self._game_dir = self.telemetry_installer.detect_game_dir()
        self.telemetry_install_state = self.telemetry_installer.install(self._game_dir)
        self._check_map_version()
        return {"status": "ready", "telemetry": self._probe_telemetry(),
                "dry_run": self.cfg.dry_run, "map": self.map_kb.snapshot(),
                "llm": self.llm.snapshot()}

    def _bg_runner(self) -> None:
        """后台线程入口：自己的事件循环跑 tick/propose。"""
        try:
            asyncio.run(self._bg_loop())
        except Exception as exc:
            self.plugin.logger.exception("后台循环异常退出: %s", exc)

    async def _bg_loop(self) -> None:
        """后台主循环：tick + propose（同线程内任务，照 neko_fishpower）。"""
        self._bg_loop_ref = asyncio.get_running_loop()
        self._tick_task = asyncio.create_task(self._tick_loop())
        self._propose_task = asyncio.create_task(self._propose_loop())
        try:
            while not self._bg_stop.is_set():
                await asyncio.sleep(0.5)
        except asyncio.CancelledError:
            pass
        finally:
            for task in (self._tick_task, getattr(self, "_propose_task", None)):
                if task:
                    task.cancel()
            for task in list(self._bg_tasks):
                task.cancel()
            self._bg_loop_ref = None

    def _wire_llm(self) -> None:
        """LLM 配置：ui_settings 里配了 → 自建 LLM 优先；没配 → 模板降级。

        配置来源：data/config/ui_settings.json 的 llm 段
        （provider/model/api_key/base_url）。
        """
        data = self._ui_settings()
        llm = data.get("llm") if isinstance(data, dict) else None
        if isinstance(llm, dict):
            provider = str(llm.get("provider", "") or "")
            model = str(llm.get("model", "") or "")
            api_key = str(llm.get("api_key", "") or "")
            base_url = str(llm.get("base_url", "") or "")
            self.llm.set_client(provider, model, api_key, base_url)
            if provider and model:
                self.plugin.logger.info("已配置 LLM: %s/%s", provider, model)
            else:
                self.plugin.logger.info("未配置 LLM，情感渲染用模板兜底")
        else:
            self.llm.set_client("", "")
            self.plugin.logger.info("未配置 LLM，情感渲染用模板兜底")
        return {"status": "ready", "telemetry": self._probe_telemetry(),
                "dry_run": self.cfg.dry_run, "map": self.map_kb.snapshot()}

    def _check_map_version(self) -> None:
        """自动检测：地图知识库版本与游戏版本匹配性。"""
        game_version = self._game_version()
        kb = self.map_kb.snapshot()
        if game_version and kb.get("loaded") and game_version not in kb.get("version", ""):
            self.plugin.logger.info(
                "游戏版本 %s 与地图知识库 %s 不匹配，可重新解析", game_version, kb.get("version"))

    def _game_version(self) -> str:
        """读游戏版本（game.log 或 version.scs 首行）。"""
        try:
            from pathlib import Path
            doc = Path.home() / "Documents" / "Euro Truck Simulator 2" / "game.log.txt"
            if doc.exists():
                for line in doc.read_text(encoding="utf-8", errors="ignore").splitlines()[:20]:
                    if "version" in line.lower() and "1." in line:
                        return line.strip()
        except Exception:
            pass
        return ""

    async def reparse_map(self) -> Dict[str, Any]:
        """手动触发地图解析：运行提取器并重新加载知识库（耗时，后台执行）。"""
        from pathlib import Path
        if not self.map_parser.extractor_available():
            return {"ok": False, "detail": "地图提取器不可用（缺编译产物）"}
        if not self._game_dir:
            self._game_dir = self.map_parser.detect_game_dir()
        if not self._game_dir:
            return {"ok": False, "detail": "未找到欧卡2安装目录"}
        out = Path(__file__).resolve().parent.parent / "data" / "map" / "map_kb.json"
        try:
            ok = await asyncio.to_thread(self.map_parser.extract, out, self._game_dir)
            if not ok:
                return {"ok": False, "detail": "地图解析失败（检查游戏目录权限）"}
            self.map_kb.reload()
            return {"ok": True,
                    "detail": f"解析完成：{self.map_kb.snapshot().get('facilities', 0)} 设施",
                    "map": self.map_kb.snapshot()}
        except Exception as exc:
            self.plugin.logger.exception("reparse failed")
            return {"ok": False, "detail": f"地图解析异常: {exc}"}

    def install_telemetry(self) -> Dict[str, str]:
        """手动触发遥测文件写入（复用已探测的游戏目录）。"""
        if not self._game_dir:
            self._game_dir = self.telemetry_installer.detect_game_dir()
        self.telemetry_install_state = self.telemetry_installer.install(self._game_dir)
        return self.telemetry_install_state

    def pilot_accept(self) -> str:
        """同意接管：用当前快照对齐注入目标，防首个 tick 误判抢控。"""
        return self.pilot.accept(self._last_snap)

    async def shutdown(self) -> None:
        self.pilot.release(reason="shutdown")
        if self._bg_stop is not None:
            self._bg_stop.set()
        if self._bg_thread is not None and self._bg_thread.is_alive():
            self._bg_thread.join(timeout=5.0)
        self._bg_thread = None
        self._bg_loop_ref = None

    async def dashboard_state(self) -> Dict[str, Any]:
        """主面板状态（@ui.context dashboard）。"""
        s = self._last_snap
        persona = self.persona.snapshot()
        arb = self.arbiter.snapshot()
        base = {
            "connected": s is not None and s.sdk_active,
            "game_running": self._game_running,
            "dry_run": self.cfg.dry_run,
            "telemetry_install": self.telemetry_install_state,
            "game_dir": self._game_dir or "",
            "pilot": self.pilot.snapshot(),
            "mood": persona.get("mood", ""),
            "catgirl": {
                "name": self.persona.name,
                "user_call": self.persona.user_call,
                "traits": self.persona.traits,
                "description": self.persona.description,
                "voice_style": self.persona.voice_style,
                "voice_label": self.persona.snapshot().get("voice_label", ""),
                "voice_styles": list(self.persona.voice_styles),
                "voice_labels": self.persona.snapshot().get("voice_labels", []),
            },
            "memory": self.memory.snapshot(),
            "top_cities": self._top_entries("cities"),
            "top_cargos": self._top_entries("cargos"),
            "scenario": arb.get("scenario", "IDLE"),
            "broadcast_frequency": arb.get("broadcast_frequency", "standard"),
            "broadcast_categories": arb.get("broadcast_categories", {}),
            "safety": arb.get("safety", {}),
            "decision_log": self.arbiter.decision_snapshot(),
            "ledger": self.ledger.month_summary(),
            "challenge_stats": {
                "wins": (self.memory.query("relationship", "challenge_wins") or {}).get("count", 0),
                "losses": (self.memory.query("relationship", "challenge_losses") or {}).get("count", 0),
            },
            "profile": self.profile.snapshot(),
            "level": self.level_celebrate.snapshot().get("level", 0),
            "route_options": self.route_planner.snapshot().get("options", []),
            "ocr": self.hud_ocr.snapshot() if self.hud_ocr else {"available": False},
            "map": self.map_kb.snapshot(),
            "photos": self.photo_album.snapshot(),
            "road_level": "",
            "nearest_service": "",
            # 任务区默认值（未驾驶/主菜单时清空显示）
            "on_job": False,
            "cargo": "",
            "city_src": "",
            "city_dst": "",
            "progress_percent": 0,
            "remaining_km": 0,
            "cargo_damage": 0,
            "speed_kmh": 0,
            "fuel_percent": 0,
            "truck": "",
            "world": {"x": 0, "z": 0},
        }
        if s is None:
            return base
        service = self.map_kb.nearest_facility(s.world_x, s.world_z, "service")
        base.update({
            "road_level": self.map_kb.road_level(s.speed_limit_kmh),
            "nearest_service": (f"{service['name']} {service['distance_km']:.0f} km"
                                if service else "（地图数据未加载）"),
        })
        base.update({
            "speed_kmh": round(s.speed_kmh, 1),
            "speed_limit_kmh": round(s.speed_limit_kmh, 1),
            "is_speeding": s.is_speeding,
            "fuel_percent": round(s.fuel_percent, 1),
            "fuel_range_km": round(s.fuel_range_km, 1),
            "power_type": s.power_type,
            "on_job": s.on_job,
            "cargo": s.cargo,
            "city_src": s.city_src,
            "city_dst": s.city_dst,
            "truck": f"{s.truck_brand} {s.truck_name}",
            "progress_percent": round(s.trip_progress_percent or 0, 1),
            "remaining_km": round(s.route_remaining_km, 1),
            "delivery_remaining_min": s.delivery_remaining_min,
            "damage": round(s.max_damage * 100, 1),
            "cargo_damage": round(s.job_cargo_damage, 3),
            "recent_activity": self._activity[:20],
            "world": {"x": round(s.world_x, 1), "z": round(s.world_z, 1)},
        })
        return base

    def _top_entries(self, kind: str) -> list:
        """记忆 top 条目（带 key 字段给面板展示）。"""
        return [{"key": k, **v} for k, v in self.memory.best(kind, 5)]

    async def memory_state(self) -> Dict[str, Any]:
        """记忆面板状态。"""
        return {
            "counts": self.memory.snapshot(),
            "top_cities": self._top_entries("cities"),
            "top_cargos": self._top_entries("cargos"),
            "recent_events": self.memory.best("events", 8),
        }

    def _probe_telemetry(self) -> str:
        r = TelemetryReader()
        ok = r.open()
        if ok:
            r.close()
        return "connected" if ok else "game-not-running"

    async def _tick_loop(self) -> None:
        r = TelemetryReader()
        while True:
            try:
                if not r.open():
                    self._game_running = False
                    self._last_snap = None
                    await asyncio.sleep(2.0)
                    continue
                self._game_running = True
                try:
                    while True:
                        s = r.snapshot()
                        # 事件检测持续运行：急刹/超速/撞车不依赖任务状态
                        self.arbiter.update_scenario(s)
                        self.proactive.update(s)
                        self.small_talk.update(s)
                        self.engine.feed(s)
                        self.persona.mood.decay_all()
                        # 猫娘智驾：dry_run 时禁止任何输入注入（试运行只跑链路）
                        if not self.cfg.dry_run:
                            pilot_msg = self.pilot.tick(s)
                            if pilot_msg:
                                self._spawn(
                                    self.push.push_direct(self.persona.polish(pilot_msg)))
                        else:
                            self.pilot.tick_dry(s)
                        # 有驾驶行为（任务中或车速>0）才保留快照，供提议/闲聊/面板
                        if s.sdk_active and (s.on_job or s.speed_kmh > 5):
                            self._last_snap = s
                        else:
                            self._last_snap = None
                        await asyncio.sleep(self.cfg.poll_interval_s)
                except Exception:
                    r.close()
                    await asyncio.sleep(2.0)  # 退避，防快照异常死循环饿死事件循环
            except asyncio.CancelledError:
                r.close()
                raise
            except Exception:
                await asyncio.sleep(2.0)

    async def _propose_loop(self) -> None:
        """L3 主动提议 + L4 闲聊 + 里程碑（低频）。"""
        import time as _time
        while True:
            try:
                await asyncio.sleep(15.0)
                now = _time.time()
                # 记忆衰减（每 10 分钟）：weight 递减，低于下限归档
                if now - getattr(self, "_last_mem_decay", 0) >= 600:
                    self._last_mem_decay = now
                    self.memory.decay()
                    await self.memory.save()
                snap = self._last_snap
                if snap is None or not snap.sdk_active:
                    continue
                # 里程碑（优先，只报一次）
                topic = self.small_talk.milestone_topic()
                if topic:
                    await self.push.push_direct(self.persona.polish(topic))
                    continue
                # 画面畅聊：OCR 采样（每 10 分钟一次）
                if now - self._last_ocr_at >= self._ocr_interval:
                    self._last_ocr_at = now
                    scene_topic = await self._ocr_scene_topic()
                    if scene_topic:
                        await self.push.push_direct(self.persona.polish(scene_topic))
                        continue
                # 世界坐标定位（M3：有地图数据时播报）
                if self.map_kb.snapshot().get("loaded") and now - getattr(self, "_last_pos", 0) > 1800:
                    self._last_pos = now
                    pos_line = self._world_position_line(snap)
                    if pos_line:
                        await self.push.push_direct(self.persona.polish(pos_line))
                        continue
                # L3 主动提议（含服务区建议）
                if self.arbiter.scenario.current in ("DRIVING", "URBAN", "HIGHWAY"):
                    propose = self.proactive.propose(now)
                    if propose:
                        await self.push.push_direct(self.persona.polish(propose))
                        continue
                    sa_advice = self.route_planner.service_area_advice(snap)
                    if sa_advice and now - getattr(self, "_last_sa_advice", 0) > 1200:
                        self._last_sa_advice = now
                        await self.push.push_direct(self.persona.polish(sa_advice))
                        continue
                # L4 随机闲聊（低频保底，仅驾驶中，按场景话题池）
                if snap.on_job:
                    scenario_key = self._scenario_talk_key(snap)
                    topic = self.small_talk.random_topic(scenario_key, now)
                    if topic and not self.cfg.dry_run:
                        await self.push.push_fact(topic)
            except asyncio.CancelledError:
                raise
            except Exception:
                await asyncio.sleep(15.0)

    async def _ocr_scene_topic(self) -> Optional[str]:
        """OCR 截屏识别 → 场景话题 + 等级检测。"""
        if self.hud_ocr is None:
            from ..adapters.hud_ocr import HudOcr
            self.hud_ocr = HudOcr(self.plugin)
            if not self.hud_ocr.is_available():
                return None
        text = await asyncio.to_thread(self.hud_ocr.read_text)
        if not text:
            return None
        # 等级检测：OCR 识别到数字等级 → 庆祝
        level = self._extract_level(text)
        if level:
            celebrate = self.level_celebrate.update(level)
            if celebrate:
                return celebrate
        # 美景检测 → 拍照
        scene = self.photo_album.detect(text)
        if scene:
            record = await self.photo_album.shoot(scene, self.hud_ocr)
            if record:
                caption = f"📸 拍到{scene}的美景了喵！"
                self._spawn(self.push.push_direct(self.persona.polish(caption)))
                return caption
        # 天气提醒（雨/雪）
        weather = self.proactive.weather_propose(text)
        if weather:
            return weather
        return self.scene_chat.topic_from_ocr(text)

    def _scenario_talk_key(self, snap) -> str:
        """闲聊话题池选择：高速/市区/深夜/服务区/默认。"""
        if snap is None:
            return "default"
        if snap.time_abs_min is not None and Proactive._is_night(snap.time_abs_min):
            return "night"
        sc = self.arbiter.scenario.current
        if sc == "HIGHWAY":
            return "highway"
        if sc in ("URBAN", "DRIVING") and snap.speed_kmh < 50:
            return "urban"
        if snap.paused or snap.speed_kmh < 5:
            return "service"
        return "default"

    def _world_position_line(self, snap) -> Optional[str]:
        """世界坐标 → 定位叙事（M3 地图数据）。"""
        if not snap or snap.world_x == 0 and snap.world_z == 0:
            return None
        road = self.map_kb.road_level(snap.speed_limit_kmh)
        if not road:
            return None
        service = self.map_kb.nearest_facility(snap.world_x, snap.world_z, "service")
        parts = [f"现在在{road}上喵"]
        if service:
            parts.append(f"前方 {service['distance_km']:.0f} km 有服务区")
        return "，".join(parts) + "~"

    @staticmethod
    def _extract_level(text: str) -> Optional[int]:
        """从 OCR 文本提取等级数字（'level 15'/'Lv.15'/'15级'）。"""
        import re
        for pat in (r"lv\.?\s*(\d+)", r"level\s*(\d+)", r"(\d+)\s*级"):
            m = re.search(pat, text, re.IGNORECASE)
            if m:
                return int(m.group(1))
        return None

    async def _say_line(self, event_name: str, **kw: Any) -> None:
        """blind 短句推送：LLM 优先（配置了），失败/未配置用模板兜底。"""
        text = await self.emotion.short_line_llm(event_name, **kw)
        if not text:
            text = self.emotion.short_line(event_name, **kw)
        await self.push.push_direct(self.persona.polish(text))

    def _on_event(self, ev: TruckEvent) -> None:
        self._record_activity(ev)
        self._track_stats(ev)
        if ev.name == "job_start" and ev.snapshot:
            self._trip_start = ev
            self._reset_trip_stats()
            # 记忆写入不依赖推送仲裁：接单即记城市/货物/路线
            hints = self.recall.on_job_start(ev.snapshot.city_src, ev.snapshot.city_dst,
                                             ev.snapshot.cargo)
            self._spawn(self.memory.save())
            lines = []
            challenge_line = self.challenge.start()
            if challenge_line:
                lines.append(self.persona.polish(challenge_line))
            preview = self.route_planner.preview(ev.snapshot)
            if preview:
                lines.append(self.persona.polish(preview))
            if hints:
                # 记忆唤起走 respond（LLM 演绎），其余盲出
                self._spawn(self.push.push_fact(
                    self.emotion.custom_fact(" ".join(hints), "job_start")))
            if lines:
                self._spawn(self.push.push_direct("\n".join(lines)))
            return
        if ev.name == "crash":
            self._job_crashes += 1
            crash_hint = self.recall.on_crash()
            parts = ", ".join(f"{k} {v * 100:.0f}%" for k, v in
                              ev.snapshot.damage_parts.items() if v > 0.01)
            lines = []
            if crash_hint:
                lines.append(self.persona.polish(crash_hint))
            line = self.catgirl.existence_line("crash", parts=parts)
            if line:
                lines.append(self.persona.polish(line))
            if lines:
                self._spawn(self.push.push_direct("\n".join(lines)))
            self._spawn(self._crash_chain(ev))
            return
        if ev.name == "job_delivered" and ev.snapshot:
            self._spawn(self._finish_trip(ev))  # 内部含归档，避免统计竞态
            line = self.catgirl.existence_line("job_delivered")
            if line:
                self._spawn(self.push.push_direct(
                    self.persona.polish(line)))
            # 到货升级提议（赚了钱 → 车行升级，L3 低频）
            revenue = ev.data.get("revenue", ev.snapshot.job_delivered_revenue)
            if revenue and revenue > 8000 and getattr(self, "_last_upgrade_tip", 0) == 0:
                self._last_upgrade_tip = 1
                self._spawn(self.push.push_direct(
                    self.persona.polish(f"赚了 {revenue} € 喵，要不要去车行升级下引擎？")))
            return
        if ev.name == "refuel":
            line = self.catgirl.existence_line("refuel")
            if line:
                self._spawn(self.push.push_direct(
                    self.persona.polish(line)))
            return
        if ev.name == "game_end":
            line = self.catgirl.existence_line("game_end")
            if line:
                self._spawn(self.push.push_direct(
                    self.persona.polish(line)))
            return
        allowed, reason = self.arbiter.decide(ev.name, ev.snapshot)
        if not allowed:
            return
        prompt = self._render(ev)
        if prompt:
            self._spawn(self.push.push_fact(prompt))

    def _reset_trip_stats(self) -> None:
        self._job_crashes = 0
        self._job_speedings = 0
        self._job_hard_brakes = 0
        self._job_refuels = 0
        self._job_fines = 0.0
        self._job_tolls = 0.0
        self._job_fuel_avg_sum = 0.0
        self._job_fuel_avg_n = 0
        self._job_night = 0
        self._job_start_ts = __import__("time").time()

    async def _crash_chain(self, ev: TruckEvent) -> None:
        """事故叙事链：伤情检查 → 维修评估/救援 → 事后安慰（L1 时间轴）。"""
        snap = ev.snapshot
        damage = snap.max_damage
        parts_detail = ", ".join(
            f"{k} {v * 100:.0f}%" for k, v in snap.damage_parts.items() if v > 0.01)
        # 伤情检查（5s 后）
        await asyncio.sleep(5)
        if damage >= 0.5:
            await self.push.push_direct(
                self.persona.polish("这撞得太重了喵！先靠边停，别硬开了！"))
            await self.push.push_direct(
                self.persona.polish("车这样别硬开了喵… 叫救援拖车吧，200€ 认了，命要紧"))
        else:
            await self.push.push_direct(
                self.persona.polish(f"让我看看… {parts_detail}，还好还好~"))
            repair = round(damage * 1000)
            await self.push.push_direct(
                self.persona.polish(f"修车大概要 {repair} € 喵… 以后见到弯道慢点呀"))
        # 事后安慰（30s 后）
        await asyncio.sleep(25)
        await self.push.push_direct(
            self.persona.polish("没大事就好… 我陪着你呢，慢慢开，不着急喵 💕"))

    def _track_stats(self, ev: TruckEvent) -> None:
        """累计行程统计。"""
        s = ev.snapshot
        if ev.name == "speeding":
            self._job_speedings += 1
        elif ev.name == "hard_brake":
            self._job_hard_brakes += 1
        elif ev.name == "crash":
            self._job_crashes += 1
        elif ev.name == "refuel":
            self._job_refuels += 1
        elif ev.name == "fine" and s:
            self._job_fines += float(s.fine_amount or 0)
        elif ev.name == "tollgate" and s:
            self._job_tolls += float(s.tollgate_amount or 0)
        elif ev.name == "trip_progress" and s and s.fuel_avg_consumption > 0:
            self._job_fuel_avg_sum += s.fuel_avg_consumption
            self._job_fuel_avg_n += 1
            if s.time_abs_min is not None and Proactive._is_night(s.time_abs_min):
                self._job_night = 1

    def _record_activity(self, ev: TruckEvent) -> None:
        """记录最近活动（面板时间线）。"""
        import datetime
        title = ACTIVITY_TITLES.get(ev.name, ev.name)
        detail = ""
        s = ev.snapshot
        if ev.name == "speeding" and s:
            detail = f"{s.speed_kmh:.0f}/{s.speed_limit_kmh:.0f} km/h"
        elif ev.name == "job_delivered" and s:
            detail = f"+{ev.data.get('revenue', s.job_delivered_revenue)} €"
        elif ev.name == "crash" and s:
            detail = f"损伤 {s.max_damage * 100:.0f}%"
        elif ev.name == "fine" and s:
            detail = f"-{s.fine_amount} €"
        elif ev.name == "job_start" and s:
            detail = f"{s.city_src} → {s.city_dst}"
        self._activity.insert(0, {
            "title": title,
            "detail": detail,
            "time": datetime.datetime.now().strftime("%H:%M"),
        })
        self._activity = self._activity[:20]

    async def _archive_trip(self, ev: TruckEvent) -> None:
        s = ev.snapshot
        start = self._trip_start.snapshot if self._trip_start else None
        fuel_avg = (self._job_fuel_avg_sum / self._job_fuel_avg_n
                    if self._job_fuel_avg_n else 0.0)
        duration_min = int((__import__("time").time() - self._job_start_ts) / 60) \
            if self._job_start_ts else 0
        trip = {
            "ts": int(ev.ts),
            "src": s.city_src or (start.city_src if start else ""),
            "dst": s.city_dst or (start.city_dst if start else ""),
            "cargo": s.cargo or "",
            "revenue": ev.data.get("revenue", s.job_delivered_revenue),
            "distance_km": s.planned_distance_km,
            "crashes": self._job_crashes,
            "speedings": self._job_speedings,
            "hard_brakes": self._job_hard_brakes,
            "refuels": self._job_refuels,
            "fines": self._job_fines,
            "tolls": self._job_tolls,
            "fuel_avg": round(fuel_avg, 1),
            "fuel_cost": round(fuel_avg * s.planned_distance_km / 100.0 * 1.5, 1),
            "repair": round(s.job_cargo_damage * 1000, 1),
            "duration_min": duration_min,
            "damage": round(s.job_cargo_damage, 3),
            "odometer_km": round(s.odometer_km, 1),
            "night": self._job_night,
        }
        self.recall.on_trip_end(trip)
        self.ledger.record(trip)
        await self.memory.save()
        self._trip_start = None
        self._reset_trip_stats()

    async def _finish_trip(self, ev: TruckEvent) -> None:
        """到货叙事：先取统计，再归档（清零），最后结算+总结。"""
        # 统计快照必须先于归档（归档会重置计数器）
        fuel_avg = (self._job_fuel_avg_sum / self._job_fuel_avg_n
                    if self._job_fuel_avg_n else 99.0)
        snap_stats = {
            "speedings": self._job_speedings,
            "hard_brakes": self._job_hard_brakes,
            "crashes": self._job_crashes,
            "refuels": self._job_refuels,
        }
        await self._archive_trip(ev)
        s = ev.snapshot
        start = self._trip_start.snapshot if self._trip_start else None
        stats = {
            "fuel_avg": fuel_avg,
            "speedings": snap_stats["speedings"],
            "hard_brakes": snap_stats["hard_brakes"],
        }
        lines = []
        settle = self.challenge.settle(stats)
        if settle:
            lines.append(self.persona.polish(settle))
        trip = {
            "dst": s.city_dst or (start.city_dst if start else ""),
            "revenue": ev.data.get("revenue", s.job_delivered_revenue),
            "distance_km": s.planned_distance_km,
            "cargo": s.cargo or "",
            "duration_min": int((__import__("time").time() - self._job_start_ts) / 60)
            if self._job_start_ts else 0,
            "speedings": snap_stats["speedings"],
            "hard_brakes": snap_stats["hard_brakes"],
            "crashes": snap_stats["crashes"],
            "refuels": snap_stats["refuels"],
            "fuel_avg": round(fuel_avg, 1),
        }
        summary = self.trip_summary.build(trip)
        lines.append(self.persona.polish(summary))
        # 新纪录/连续无事故庆祝
        profile_result = self.profile.record(trip)
        celebration = self.profile.record_celebration(profile_result)
        if celebration:
            lines.append(self.persona.polish(celebration))
        await self.push.push_fact("\n".join(lines))

    def _render(self, ev: TruckEvent) -> Optional[str]:
        s = ev.snapshot
        if s is None:
            return None
        if ev.name == "speeding":
            if self.persona.strict_mode:
                return ("主人超速了！{speed:.0f}/{limit:.0f} km/h！"
                        "立刻减速，安全不是儿戏！".format(speed=s.speed_kmh,
                                                       limit=s.speed_limit_kmh))
            return self.emotion.fact_prompt(ev.name, speed=s.speed_kmh, limit=s.speed_limit_kmh)
        if ev.name == "hard_brake":
            if self.persona.strict_mode:
                return (f"急刹！{s.speed_kmh:.0f} km/h 突然刹停！"
                        "跟车距离留够了吗？这样开迟早出事！")
            return self.emotion.fact_prompt(ev.name, speed=s.speed_kmh)
        if ev.name == "crash":
            parts = ", ".join(f"{k} {v * 100:.0f}%" for k, v in s.damage_parts.items() if v > 0.01)
            return self.emotion.fact_prompt(ev.name, parts=parts)
        if ev.name == "job_start":
            return self.emotion.fact_prompt(ev.name, cargo=s.cargo, src=s.city_src,
                                            dst=s.city_dst, km=s.planned_distance_km)
        if ev.name == "job_delivered":
            return self.emotion.fact_prompt(ev.name, revenue=ev.data.get("revenue", s.job_delivered_revenue))
        if ev.name == "job_cancelled":
            return self.emotion.fact_prompt(ev.name, penalty=ev.data.get("penalty", 0))
        if ev.name == "fine":
            return self.emotion.fact_prompt(ev.name, amount=s.fine_amount, offence=s.fine_offence or "违规")
        if ev.name == "tollgate":
            return self.emotion.fact_prompt(ev.name, amount=ev.data.get("amount", s.tollgate_amount))
        if ev.name == "refuel":
            return self.emotion.fact_prompt(ev.name, liters=s.fuel, percent=s.fuel_percent)
        if ev.name == "trailer_attach":
            return self.emotion.fact_prompt(ev.name, plate=s.trailer_license)
        if ev.name == "trailer_detach":
            return self.emotion.fact_prompt(ev.name)
        if ev.name == "time_warning":
            return self.emotion.fact_prompt(ev.name, min=ev.data.get("min", 0))
        if ev.name == "time_over":
            return self.emotion.fact_prompt(ev.name)
        if ev.name == "low_fuel":
            return self.emotion.fact_prompt(ev.name, percent=ev.data.get("percent", 0))
        if ev.name == "game_start":
            return self.emotion.fact_prompt(ev.name) + self._greeting()
        if ev.name == "game_end":
            return self.emotion.fact_prompt(ev.name) + self._farewell()
        if ev.name == "trip_progress":
            d = ev.data
            mark = d.get("mark", 0)
            ratio = {33: "三分之一", 50: "一半", 67: "三分之二", 100: "全程"}.get(mark, f"{mark}%")
            return self.emotion.fact_prompt(ev.name, mark_ratio=ratio,
                                            km=d.get("km", 0), min=d.get("min", 0))
        if ev.name == "distance_mark":
            d = ev.data
            return self.emotion.fact_prompt(ev.name,
                                            km=round(d.get("remaining_km", 0)),
                                            mark=d.get("mark", 0))
        if ev.name == "time_relaxed":
            return self.emotion.fact_prompt(ev.name, min=ev.data.get("min", 0))
        if ev.name == "time_tight":
            return self.emotion.fact_prompt(ev.name, min=ev.data.get("min", 0))
        if ev.name == "early_arrival":
            return self.emotion.fact_prompt(ev.name, min=ev.data.get("min", 0))
        if ev.name == "cargo_damage":
            return self.emotion.fact_prompt(ev.name, pct=ev.data.get("pct", 0))
        return None

    def _greeting(self) -> str:
        """按游戏内时段问候。"""
        import datetime
        hour = datetime.datetime.now().hour
        streak = self._bump_streak()
        base = ""
        if 5 <= hour < 12:
            base = " 早呀！今天想拉什么货？"
        elif 12 <= hour < 18:
            base = " 下午好喵！今天跑哪条线？"
        elif 18 <= hour < 23:
            base = " 晚上好喵！今晚跑哪条线？"
        else:
            base = " 深夜了喵… 我陪你跑夜路！"
        if streak >= 2:
            base += f" 又是你喵，连续 {streak} 天跑车真勤快！"
        return base

    def _bump_streak(self) -> int:
        """连续天数记录（关系记忆）。"""
        import datetime
        today = datetime.date.today().isoformat()
        entry = self.memory.query("relationship", "streak") or {}
        last = entry.get("last_day", "")
        count = int(entry.get("count", 0))
        if last == today:
            return count
        # 昨天 → 连续+1；否则重置
        yesterday = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
        count = count + 1 if last == yesterday else 1
        self.memory.remember("relationship", "streak",
                             {"count": count, "last_day": today}, importance=0.7)
        return count

    def _farewell(self) -> str:
        """退出总结：今日里程/单数。"""
        p = self.profile.snapshot()
        stats = p.get("stats", {})
        km = int(stats.get("total_km", 0))
        trips = int(stats.get("trips", 0))
        if trips:
            return f" 今天累计跑了 {km} km、{trips} 单喵，辛苦啦，晚安~ 💤"
        return " 今天辛苦了喵，晚安~ 💤"

    async def handle_status_query(self) -> Dict[str, Any]:
        r = TelemetryReader()
        if not r.open():
            return {"ok": False, "summary": "游戏没在运行喵，先打开欧卡2吧"}
        s = r.snapshot()
        r.close()
        if not s.on_job:
            state = "休息中" if s.paused else "自由驾驶"
            return {"ok": True,
                    "summary": f"现在没有任务喵，在{state}。车辆：{s.truck_brand} {s.truck_name}"}
        pct = s.trip_progress_percent or 0
        return {"ok": True, "summary": (
            f"任务：{s.city_src} → {s.city_dst}，{s.cargo}，"
            f"进度 {pct:.0f}%，剩 {s.route_remaining_km:.0f} km "
            f"~{s.route_remaining_time_min:.0f} 分钟喵")}

    async def handle_player_talk(self, text: str) -> Dict[str, Any]:
        self.arbiter.on_player_speak()
        r = TelemetryReader()
        if not r.open():
            return {"ok": True,
                    "summary": "游戏没在运行喵，先打开欧卡2再聊驾驶吧"}
        s = r.snapshot()
        r.close()
        if "超速" in text or ("快" in text and "慢" not in text):
            if s.is_speeding:
                return {"ok": True, "summary": (
                    f"正在超速：{s.speed_kmh:.0f} / 限速 {s.speed_limit_kmh:.0f} km/h")}
            return {"ok": True, "summary": f"没有超速，当前 {s.speed_kmh:.0f} km/h"}
        if "油" in text:
            return {"ok": True, "summary": (
                f"油量 {s.fuel_percent:.0f}%（{s.fuel:.0f}L），还能跑 {s.fuel_range_km:.0f} km")}
        # 知识库问答
        if "省油" in text or "怎么开省" in text:
            return {"ok": True, "summary": self.knowledge.fuel_tip()}
        if "卡车" in text or "这车" in text or "这辆" in text:
            info = self.knowledge.truck_info(s.truck_brand)
            if info:
                return {"ok": True, "summary": info}
        if "载重" in text or "拉多少" in text or "能拉" in text:
            return {"ok": True, "summary": (
                f"当前挂车货物 {s.cargo_mass:.0f}t，这台车拉这个量很轻松喵")}
        if "哪个城市" in text and ("赚" in text or "钱" in text):
            return {"ok": True, "summary": self.knowledge.best_city_tip(s.city_dst)}
        if "第二台车" in text or "车队" in text or "买台" in text or "买辆车" in text:
            return {"ok": True, "summary": self.knowledge.fleet_tip(self.ledger.month_summary())}
        if "多远" in text or "距离" in text:
            tip = self.knowledge.route_tip(s.city_src, s.city_dst)
            if tip:
                return {"ok": True, "summary": tip}
        if "货物" in text and s.cargo:
            return {"ok": True, "summary": self.knowledge.cargo_tip(s.cargo)}
        if "路" in text and ("哪条" in text or "怎么走" in text or "选" in text):
            choice = self.route_planner.route_choice(s)
            if choice:
                return {"ok": True, "summary": choice}
        if "服务区" in text or "加油" in text:
            advice = self.route_planner.service_area_advice(s)
            if advice:
                return {"ok": True, "summary": advice}
        if "几级" in text or "等级" in text:
            return {"ok": True, "summary": (
                f"你现在 {self.level_celebrate.snapshot().get('level', 0)} 级喵")}
        if "赔" in text or "修车" in text or "撞车" in text:
            repair = round(s.max_damage * 1000)
            return {"ok": True, "summary": (
                f"当前损伤 {s.max_damage * 100:.0f}%，修车估计要 {repair} € 喵，小心点开呀")}
        if "累" in text or "困" in text or "疲劳" in text:
            rest = s.rest_stop_min
            if rest is not None and rest > 0:
                return {"ok": True, "summary": (
                    f"离强制休息还有 {rest:.0f} 分钟喵，累了就找休息区睡一觉")}
            return {"ok": True, "summary": "目前还不累喵，继续开没问题~"}
        if "超时" in text or "来得及" in text or "时间够" in text:
            rem = s.delivery_remaining_min
            if rem is not None:
                state = "很宽裕" if rem > 120 else "有点紧" if rem > 60 else "很紧张"
                return {"ok": True, "summary": (
                    f"交付还剩 {rem} 分钟，时间{state}喵，别超速赶路")}
            return {"ok": True, "summary": "现在没有任务喵"}
        if "接" in text and ("单" in text or "任务" in text) and "该" in text:
            dist = s.planned_distance_km
            revenue = s.job_income
            if dist and revenue:
                per_km = revenue / dist
                verdict = "划算" if per_km > 15 else "一般" if per_km > 10 else "不划算"
                return {"ok": True, "summary": (
                    f"这单 {dist} km 赚 {revenue} €，每公里 {per_km:.0f} €，{verdict}喵")}
        # 游戏机制/风景/建筑/标识知识问答（任意语句先匹配，命中即答）
        tip = self.knowledge.game_tip(text)
        if tip:
            return {"ok": True, "summary": tip}
        # 配置了自建 LLM → 深度回答（失败/未配置自动落兜底）
        if self.llm.configured:
            prompt = (f"你是{self.persona.name}（{self.persona.user_call}的副驾驶猫娘）。"
                      f"当前车辆 {s.truck_brand} {s.truck_name}，车速 {s.speed_kmh:.0f} km/h，"
                      f"限速 {s.speed_limit_kmh:.0f} km/h。玩家说：{text}。"
                      f"用一句话自然回应（25 字内，带猫娘语气）。")
            answer = await self.llm.call(prompt)
            if answer:
                return {"ok": True, "summary": answer}
        # 兜底：知识性回应（车况 + 随机技巧），给 LLM 素材避免反问
        import random as _rnd
        extra = _rnd.choice(self.knowledge.drive_tips + self.knowledge.fuel_tips)
        return {"ok": True, "summary": (
            f"当前车辆 {s.truck_brand} {s.truck_name}，限速 {s.speed_limit_kmh:.0f} km/h。"
            f"顺带一提：{extra}")}

