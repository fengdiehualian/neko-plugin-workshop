"""运行时：装配遥测、事件引擎、仲裁、推送、记忆。"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, Optional

from ..adapters.llm_client import LLMProvider
from ..adapters.push_sender import CHAT, CRITICAL, IMPORTANT, PushSender
from ..adapters.telemetry_client import TelemetryReader
from ..adapters.telemetry_installer import TelemetryInstaller
from .arbiter import Arbiter
from .cargo import CargoBook
from .companion import Companion
from .config_model import PawpilotConfig
from .diary import TripDiary
from .event_catalog import BROADCAST_FREQUENCY_MULTIPLIERS
from .event_engine import EventEngine, TruckEvent
from .knowledge import KnowledgeBase
from .ledger import Ledger
from .level_celebrate import LevelCelebrate
from .map_kb import MapKnowledge
from .memory import STORE_KEY, MemoryStore, unwrap_result
from .mood import Persona
from .photo_album import PhotoAlbum
from .pilot import CatPilot
from .places import PlaceBook
from .playstyle import PlaystyleBook
from .proactive import Proactive
from .profile import DriverProfile
from .radio import RadioDJ
from .recall import Recall
from .route_planner import RoutePlanner
from .safety_guard import SafetyGuard
from .scene_chat import SceneChat
from .templates import EmotionRenderer, offence_note
from .trip_summary import TripSummary
from .vehicle import damage_sentence, trailer_config

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

# 内容类事件：一次性 / 长间隔的真实内容，直接 respond，不参与仲裁限流。
# 实测教训：过仲裁时它们会被"场景门控"或限流单槽窗口吞掉，主人反馈
# "任务分段播报、时间提醒、收费站从没响过"。
CONTENT_EVENTS = frozenset({
    "trip_progress",   # 1/3、1/2、2/3 分段播报
    "distance_mark",   # 剩余距离锚点预告
    "time_warning",    # 交付时间提醒
    "time_over",       # 超时
    "time_tight",      # 时间紧张
    "time_relaxed",    # 时间宽裕
    "early_arrival",   # 提前到达
    "tollgate",        # 过收费站
    "trailer_attach",  # 挂上挂车
    "trailer_detach",  # 卸下挂车
})

# 紧急事件：立即播、并且让闲聊整体安静一会儿（"紧急先走，闲聊排队"）
CRITICAL_EVENTS = frozenset({
    "crash",            # 事故
    "wheels_airborne",  # 翻车/离地
    "engine_stalled",   # 行驶中熄火
})

# ── 事件路由表：唯一说明"哪个事件走哪条道"的地方 ──────────────────
#   own     —— 有专属处理（带副作用，自己决定说什么、什么时候说）
#   content —— 内容类：一次性/长间隔的真实内容，绕过仲裁直接说
#   normal  —— 常规：过仲裁后按优先级说（在 CRITICAL_EVENTS 里的走紧急车道）
# 改行为先看这张表；test_event_routes_cover_catalog 会守住它覆盖全部事件。
ROUTE_OWN = "own"
ROUTE_CONTENT = "content"
ROUTE_NORMAL = "normal"

EVENT_ROUTES: Dict[str, str] = {}
EVENT_ROUTES.update({name: ROUTE_OWN for name in (
    "job_start",        # 接单：记城市/货物、地点簿、开场故事
    "job_delivered",    # 到货：归档、日记、结算、城市打卡
    "crash",            # 事故：事故链叙事 + 话题倾向
    "refuel",           # 加油：走通用渲染但需要单独过仲裁
    "game_end",         # 退出：收车小结
    "ferry",            # 过境：学地名 + 介绍两端
    "train",
)})
EVENT_ROUTES.update({name: ROUTE_CONTENT for name in (CONTENT_EVENTS | {"mountain_pass"})})


def route_of(event_name: str) -> str:
    """事件走哪条道（未登记的一律按常规处理）。"""
    return EVENT_ROUTES.get(event_name, ROUTE_NORMAL)


# 里程关口（原 SmallTalk 的里程碑表；现在只作为陪玩 milestone 维度的触发源）
MILESTONE_KM = (100, 500, 1000, 2500, 5000, 10000, 25000, 50000,
                100000, 200000, 400000, 600000, 1000000)


def _as_bool(value: Any) -> bool:
    """面板传来的开关值可能是 bool / "true" / 0，统一成 bool。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in ("1", "true", "yes", "on", "开", "是")


def _memory_mirror_path(plugin: Any) -> Optional[str]:
    """记忆的本地 JSON 兜底路径（宿主 store 出问题时还能救回来）。"""
    try:
        return str(plugin.data_path("memory_backup.json"))
    except Exception:                       # noqa: BLE001
        return None


class PawpilotRuntime:
    """插件运行时。"""

    def __init__(self, plugin: Any, config: PawpilotConfig) -> None:
        self.plugin = plugin
        self.cfg = config
        host_persona = getattr(plugin, "persona", None)
        self.persona = Persona(host_persona)
        self.llm = LLMProvider()
        self.emotion = EmotionRenderer(self.persona, llm=self.llm)
        self.engine = EventEngine(config)
        self.safety = SafetyGuard(config)
        self.arbiter = Arbiter(config, self.safety)
        self.arbiter.broadcast_categories = dict(config.broadcast_categories)
        self.arbiter.broadcast_frequency = config.broadcast_frequency
        self.push = PushSender(plugin, dry_run=config.dry_run, on_push=self._note_push,
                               gap_chat=float(getattr(config, "companion_gap_s", 90.0) or 90.0))
        self.memory = MemoryStore(plugin.store, mirror_path=_memory_mirror_path(plugin))
        # 旅途日记（故事层）：收车小结 / 回归问候 / 陪聊回忆 共用
        self.diary = TripDiary(self.memory)
        # 玩法层：货物分类 + 玩法画像（只吃真实遥测字段，不编造游戏里没有的概念）
        self.cargo_book = CargoBook()
        self.playbook = PlaystyleBook(self.cargo_book)
        self._playstyle = None
        self.recall = Recall(self.memory)
        self.ledger = Ledger(self.memory)
        self.trip_summary = TripSummary({})
        self.knowledge = KnowledgeBase()
        self.map_kb = MapKnowledge()
        self.proactive = Proactive(config, map_kb=self.map_kb)
        # 内容层（2026-10 玩法扩充）：地点簿（自学习地名坐标）+ 猫娘电台
        self.places = PlaceBook(self.memory)
        self.radio = RadioDJ(config)
        # 陪玩维度引擎（2026-10 主人定调"陪玩是方方面面的"）：十几个维度轮转，
        # 每条只投事实行，措辞交给宿主 LLM 按人设演绎
        self.companion = Companion(config, map_kb=self.map_kb)
        self.pilot = CatPilot(persona=self.persona, map_kb=self.map_kb, cfg=config)
        self.profile = DriverProfile(self.memory)
        self.scene_chat = SceneChat()
        self.route_planner = RoutePlanner(self.knowledge)
        self.level_celebrate = LevelCelebrate(self.memory)
        self.photo_album = PhotoAlbum(plugin)
        self.telemetry_installer = TelemetryInstaller(
            plugin_rel=config.telemetry_plugin_rel,
            bundle_rel=config.telemetry_bundle_rel)
        self.telemetry_install_state: Dict[str, str] = {}
        self._game_dir: Optional[str] = None
        self.ocr_regions = self._load_ocr_regions()
        self.hud_ocr = None  # 惰性：OCR 可用时创建
        self._game_window_titles = self.ocr_regions.get("game_window_titles") or None
        self._pending_photo_scene: str = ""
        self._pending_photo_until: float = 0.0
        self._last_km: float = 0.0
        self._last_milestone: float = 0.0
        self._milestone_km: float = 0.0
        self._chat_bias: str = ""
        self._chat_bias_until: float = 0.0
        self._mood_drive: Optional[Dict[str, Any]] = None
        self._session_live: bool = False
        self._ocr_interval = float(self.ocr_regions.get("scan_interval_s", 240))
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

    def _note_push(self) -> None:
        """任何一条推送落地时打时间戳：陪聊要给它让路（别把事件内容挤掉）。"""
        self._last_push_at = time.time()

    # ── 里程碑 & 话题倾向（2026-10 模块关系收拢）──
    def _track_milestone(self, s) -> None:
        """里程关口只负责"记一笔"，说什么交给陪玩维度表统一调度。

        原 SmallTalk 模块只剩这一个在用的功能，现已并入 companion 的 milestone 维度。
        """
        km = float(getattr(s, "odometer_km", 0) or 0)
        if km <= 0:
            return
        if self._last_km <= 0:               # 第一次读到里程：只对齐基准，不补报历史关口
            self._last_km = km
            self._last_milestone = max((m for m in MILESTONE_KM if m <= km), default=0.0)
            return
        if km < self._last_km:               # 换车/读档回退
            self._last_km = km
            return
        self._last_km = km
        for mark in MILESTONE_KM:
            if self._last_milestone < mark <= km:
                self._last_milestone = mark
                self._milestone_km = float(mark)

    def _set_bias(self, dim_id: str, seconds: float) -> None:
        """给接下来的闲聊定个话题倾向（刚撞完就别聊吃的）。"""
        self._chat_bias = dim_id
        self._chat_bias_until = time.time() + seconds

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

    # ── 面板可调的节奏/内容配置（白名单，防止面板乱写配置）──
    _PACING_KEYS = {
        "companion_gap_s": float,           # 陪聊基准间隔（秒）
        "companion_event_quiet_s": float,   # 事件之后让路多久（秒）
        "radio_interval_s": float,          # 电台间隔（秒，0=关）
        "passing_radius_km": float,         # 途经地名判定半径
        "passing_cooldown_s": float,        # 同一地点播报冷却
        "companion_enabled": bool,          # 陪玩总开关
        "content_passing_places": bool,
        "content_radio_dj": bool,
        "content_opening_story": bool,
        "content_arrival_culture": bool,
    }

    def pacing_state(self) -> Dict[str, Any]:
        """当前节奏/内容配置（供面板显示）。"""
        return {k: getattr(self.cfg, k, None) for k in self._PACING_KEYS}

    def set_pacing(self, values: Dict[str, Any]) -> Dict[str, Any]:
        """面板改配置：只认白名单里的键，类型按声明强制转换。"""
        applied: Dict[str, Any] = {}
        for key, value in (values or {}).items():
            caster = self._PACING_KEYS.get(key)
            if caster is None:
                continue
            try:
                applied[key] = caster(value) if caster is not bool else _as_bool(value)
            except (TypeError, ValueError):
                continue
        for key, value in applied.items():
            setattr(self.cfg, key, value)
        if applied:
            self.plugin.logger.info("[配置] 面板调整：%s", applied)
        return {"applied": applied, "state": self.pacing_state()}

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
            "pacing": self.pacing_state(),      # 节奏/内容配置（设置页可调）
        })
        path = self._ui_settings_path()
        tmp = path.with_suffix(".json.tmp")
        try:
            # data/config 可能整个缺失（安装包未带 data/ 或用户手动清过），
            # 旧版这里不建目录 → 写 .tmp 直接 OSError → 面板所有开关"存了等于没存"
            path.parent.mkdir(parents=True, exist_ok=True)
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
        pacing = saved.get("pacing")
        if isinstance(pacing, dict):
            self.set_pacing(pacing)          # 节奏/内容配置（白名单 + 类型转换）

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
            path.parent.mkdir(parents=True, exist_ok=True)
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
            "telemetry_live": s is not None,     # 真的在开车（不是主菜单的 0 值）
            "game_running": self._game_running,
            "dry_run": self.cfg.dry_run,
            "telemetry_install": self.telemetry_install_state,
            "game_dir": self._game_dir or "",
            "pilot": self.pilot.snapshot(),            "mood": persona.get("mood", ""),
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
            "diary": self.diary.snapshot(top=5),
            "playstyle": (self.playbook.describe(s) if s is not None
                          else self.playbook.describe(None)),
            "push": self.push.snapshot(),
            "mood_drive": (self._mood_drive or self.persona.mood.drive()),
            "top_cities": self._top_entries("cities"),
            "top_cargos": self._top_entries("cargos"),
            "scenario": arb.get("scenario", "IDLE"),
            "broadcast_frequency": arb.get("broadcast_frequency", "standard"),
            "broadcast_categories": arb.get("broadcast_categories", {}),
            "safety": arb.get("safety", {}),
            "decision_log": self.arbiter.decision_snapshot(),
            "companion": self.companion.snapshot(),
            "ledger": self.ledger.month_summary(),
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
            # 面板/排查用：给出"生效货损"（两路取大）与两路原始值，便于现场
            # 判断 SCS SDK 到底把实时货损写在 job 还是 trailer 通道
            "cargo_damage": round(s.cargo_damage, 3),
            "cargo_damage_job": round(s.job_cargo_damage, 3),
            "cargo_damage_trailer": round(s.trailer_cargo_damage, 3),
            # 碰撞校准用：面板实时显示加速度合成值（g），据此设 crash_accel_g
            "accel_g": round(s.accel_g, 2),
            # 2026-10 新接上的遥测：档位/灯光/机务/地图比例尺
            "gear": s.gear,
            "gear_dashboard": s.gear_dashboard,
            "lights": {
                "low_beam": s.lights_low_beam, "parking": s.lights_parking,
                "brake": s.lights_brake, "hazard": s.lights_hazard,
                "beacon": s.lights_beacon, "reverse": s.lights_reverse,
                "blinker_left": s.blinker_left, "blinker_right": s.blinker_right,
            },
            "mech": {
                "air_pressure": round(s.air_pressure, 1),
                "brake_temp": round(s.brake_temperature, 1),
                "oil_temp": round(s.oil_temperature, 1),
                "water_temp": round(s.water_temperature, 1),
                "battery_v": round(s.battery_voltage, 1),
                "wheels_on_ground": s.wheels_on_ground,
                "warnings": list(s.active_warnings),
            },
            "map_scale": round(s.map_scale, 1),
            "in_city": s.is_city,
            "crash_accel_g": round(self.cfg.crash_accel_g, 2),
            "recent_activity": self._activity[:20],
            "world": {"x": round(s.world_x, 1), "z": round(s.world_z, 1)},
        })
        return base

    def _top_entries(self, kind: str) -> list:
        """记忆 top 条目（带 key 字段给面板展示）。

        注意：MemoryStore 里存的是 {名字: {count, weight, ...}}，`best()` 只返回
        值列表、丢了名字。旧写法 `for k, v in self.memory.best(...)` 是拿字典的
        key 去解包 → 记忆一多就 ValueError: too many values to unpack，
        整个 dashboard/get_panel_state 全崩（面板永远停在「加载中」）。
        """
        try:
            store = self.memory.query(kind) or {}
        except Exception:
            return []
        items = [{"key": k, **v} for k, v in store.items()
                 if isinstance(v, dict) and not v.get("archived")
                 and not str(k).startswith("route_")]
        items.sort(key=lambda e: float(e.get("weight", 0) or 0), reverse=True)
        return items[:5]

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
        self._boot_probe_done = False
        while True:
            try:
                if not r.open():
                    self._game_running = False
                    self._last_snap = None
                    self._boot_probe_done = True
                    await asyncio.sleep(2.0)
                    continue
                if not self._boot_probe_done:
                    # 插件刚加载就发现游戏已经在跑 → 多半是主人点了「重载」，
                    # 不能当成"刚打开游戏"再喊一次「主人打开了欧卡2」。
                    # 实证：重载两次就喊两次，主人直接判定"说假话"。
                    self._boot_probe_done = True
                    self.engine.skip_next_game_start()
                self._game_running = True
                try:
                    while True:
                        s = r.snapshot()
                        # 事件检测持续运行：急刹/超速/撞车不依赖任务状态
                        self.arbiter.update_scenario(s)
                        self.proactive.update(s)
                        self._track_milestone(s)
                        self.engine.feed(s)
                        self.persona.mood.decay_all()
                        # 猫娘智驾：dry_run 时禁止任何输入注入（试运行只跑链路）
                        if not self.cfg.dry_run:
                            pilot_msg = self.pilot.tick(s)
                            if pilot_msg:
                                # 交还/危险退出：走 respond（她要解释"为什么还你"）
                                self._spawn(self.push.push_fact(
                                    self.emotion.custom_fact(pilot_msg, "proactive")))
                                self._last_pilot_handback = time.time()
                        else:
                            self.pilot.tick_dry(s)
                        # 只有"真的在开车"才保留快照（供提议/闲聊/面板）：
                        # 主菜单里 SDK 也 active 但全是 0，旧版把 0 当真实数据 →
                        # 面板显示 0%、猫娘在车库里就开聊、识屏还报"前方有事故"。
                        if s.in_session:
                            self._last_snap = s
                            self._session_live = True
                        else:
                            self._last_snap = None
                            self._session_live = False
                        # 推送车道排空：优先级队列里攒下的话，车道空了就放一条出来
                        await self.push.drain()
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
        """L3 主动提议 + L4 闲聊 + 里程碑（低频）。

        去话痨化原则（2026-10 用户定调）：**每条主动输出都应伴随一个动作或状态
        变化**，否则不发。所以这里只保留：接力驾驶提议（她要接管方向盘）、
        油量/休息等可执行提醒、途经/到达等真实事件内容；纯陪聊频率大幅下调
        （电台 30 分钟一次、定位叙事 15 分钟一次）。
        """
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
                if snap is None or not snap.in_session:
                    continue
                # 统一的"陪聊密度"倍率：面板播报频率 × 玩法画像 × 当前情绪。
                # 2026-10 修正：以前只有事件冷却和陪玩维度吃这个倍率，电台/途经地点/
                # 识屏各用各的固定间隔 → 调「安静」并不会真的让整体安静下来。
                st_play = self.playbook.classify(snap)
                self._playstyle = st_play
                mood_drive = self.persona.mood.drive()
                self._mood_drive = mood_drive
                chatter_mult = (BROADCAST_FREQUENCY_MULTIPLIERS.get(
                    self.arbiter.broadcast_frequency, 1.0)
                    * float(getattr(st_play, "gap_mult", 1.0) or 1.0)
                    * float(mood_drive.get("gap_mult", 1.0) or 1.0))
                # 里程碑：交给陪玩维度表统一调度（原先在这里单独插一条硬编码台词）
                # 画面畅聊：OCR 采样（基准 4 分钟，同样受陪聊密度影响）
                if now - self._last_ocr_at >= self._ocr_interval * chatter_mult:
                    self._last_ocr_at = now
                    photo_ask, scene_topic = await self._ocr_scan()
                    if photo_ask:
                        self._spawn(self.push.push_fact(
                            self.emotion.custom_fact(photo_ask, "photo"),
                            priority=CHAT, topic="photo"))
                        continue
                    if scene_topic:
                        # 画面里真看到的东西（警车/事故/隧道/收费站…）。
                        # 走 chat 而不是 important：识屏是整条链里最不可靠的一环
                        # （OCR 可能认错字、可能读到别处文字），不值得插队抢占播报，
                        # 更不该在他没动的时候打断别的正事。
                        await self.push.push_fact(scene_topic, priority=CHAT,
                                                  topic="screen")
                        continue
                # ── 接力驾驶提议：开阔路段主动提出"我来开一段"（有动作，不是话）
                if (getattr(self.cfg, "pilot_relay_enabled", True)
                        and self.pilot.state == "idle" and self.pilot.available
                        and snap.on_job and not snap.paused
                        and snap.is_open_road and snap.speed_kmh > 40
                        and now - getattr(self, "_last_pilot_offer", 0) > 900
                        and now - getattr(self, "_last_pilot_handback", 0) > 600):
                    self._last_pilot_offer = now
                    self.pilot.offer()
                    await self.push.push_fact(
                        "[接力驾驶] 现在是开阔路段（郊外/高速、车速 "
                        f"{snap.speed_kmh:.0f} km/h），猫娘想替你开一段让你歇会儿。"
                        "请以猫娘语气问一句要不要她来开（提示可用面板「猫娘智驾·同意」"
                        "或直接说一声）。不要提系统/工具")
                    continue
                # 途经已知地点 → 顺手介绍这个地方（地点簿自学习，越开越准）
                if getattr(self.cfg, "content_passing_places", True) and not snap.paused:
                    place = self.places.passing(
                        snap.world_x, snap.world_z, now,
                        radius_km=float(getattr(self.cfg, "passing_radius_km", 6.0) or 6.0),
                        cooldown_s=(float(getattr(self.cfg, "passing_cooldown_s", 1800.0) or 1800.0)
                                    * chatter_mult),
                    )
                    if place:
                        await self.push.push_fact(self._passing_place_fact(place),
                                                  priority=CHAT, topic="place")
                        continue
                # 猫娘电台（按时段/天气/驾驶时长挑节目；内容交宿主 LLM 演绎）
                if getattr(self.cfg, "content_radio_dj", True):
                    driving_since = 0.0
                    if self.proactive._drive_start_ts:
                        driving_since = now - self.proactive._drive_start_ts
                    weather = getattr(self, "_last_weather_hint", "")
                    brief = self.radio.pick(snap, now, driving_since=driving_since,
                                            station_hint=getattr(self, "_radio_station_hint", ""),
                                            weather=weather,
                                            interval_mult=chatter_mult)
                    if brief:
                        await self.push.push_fact(brief, priority=CHAT, topic="radio")
                        continue
                # ── 陪玩维度引擎（主通道）──────────────────────────────
                # 2026-10 主人定调"陪玩是方方面面的"：旧版把主动输出收得只剩安全
                # 提醒，自由驾驶时更几乎全被门控挡掉，玩起来不像有人陪。
                # 现在由十几个维度轮转（路况/进城/天气/夜路/这单货/行程/车况/
                # 关心/巡航/晨昏/生活闲聊/反问/回忆/风景），每维独立冷却，
                # 角度轮换避免车轱辘话；类别挂面板开关，频率乘面板倍率。
                # 陪聊给事件让路：刚有真实内容（分段/收费站/加油站/事故…）出去过，
                # 就先闭嘴，别把主人的注意力抢走（实测每分钟一条闲聊会盖住事件类）
                quiet_s = float(getattr(self.cfg, "companion_event_quiet_s", 45.0) or 45.0)
                if now - getattr(self, "_last_push_at", 0.0) < quiet_s:
                    pass
                else:
                    gap_mul = chatter_mult          # 和上面几处用同一个倍率
                    cctx = self.companion.build_context(
                        snap,
                        drive_min=((now - self.proactive._drive_start_ts) / 60.0
                                   if self.proactive._drive_start_ts else 0.0),
                        place=self.places.nearest(snap.world_x, snap.world_z, max_km=8.0),
                        odometer_km=snap.odometer_km,
                        trips=int((self.profile.snapshot().get("stats") or {}).get("trips", 0) or 0),
                        top_city=self._top_city_name(),
                        rain_confirmed=(getattr(self, "_last_weather_hint", "") == "雨"),
                        remark=self.recall.recent_remark(),
                        mood=self.persona.mood.primary(),
                        mood_tone=str(mood_drive.get("tone", "")),
                        memory_hint=self.recall.context_hint(top=2),
                        diary_today=self.diary.today_summary(),
                        diary_last=self.diary.last() or {},
                        driving_by=("catgirl" if self.pilot.state == "engaged" else "user"),
                        profile_label=self.profile.label(),
                    )
                    cctx["milestone_km"] = self._milestone_km
                    fam = self.cargo_book.classify(getattr(snap, "cargo_id", ""),
                                                   getattr(snap, "cargo", ""))
                    cctx["play_label"] = st_play.label
                    cctx["play_advice"] = self.cargo_book.tip(
                        getattr(snap, "cargo_id", ""), getattr(snap, "cargo", ""))
                    cctx["cargo_family"] = (fam or {}).get("label", "")
                    bias = (self._chat_bias
                            if now < getattr(self, "_chat_bias_until", 0.0) else "")
                    if not bias:
                        # 没有刚发生的事：先看情绪主线（心疼→关心、困→夜路），
                        # 再看玩法倾向（观光→风景、市区→进城…）
                        bias = mood_drive.get("bias") or st_play.chat_bias
                    picked = self.companion.next_fact(
                        snap, cctx, now=now,
                        categories=self.arbiter.broadcast_categories,
                        gap_multiplier=gap_mul, bias=bias)
                    if picked:
                        self.plugin.logger.info(
                            "[陪玩] %s | %s", picked["id"],
                            picked["fact"].splitlines()[0][:60])
                        if picked["id"] == "remark":
                            self.recall.mark_remark_spoken()
                        if picked["id"] == "milestone":
                            self._milestone_km = 0.0
                        fact = self.emotion.custom_fact(picked["fact"], "proactive")
                        if picked.get("hint"):
                            fact = f"{fact}（{picked['hint']}）"
                        await self.push.push_fact(fact, priority=CHAT,
                                                  topic=picked["id"])
                        continue
                # L3 主动提议（服务区/加油/疲劳/夜间…）
                # 放宽门控：只要在任务中且未暂停就允许。旧版限定
                # DRIVING/URBAN/HIGHWAY，而停车等灯时场景被判成 DELIVERY
                # → 整段提议被跳过（这也是"地图信息没反应"的原因之一）。
                if snap.on_job and not snap.paused:
                    propose = self.proactive.propose(now)
                    if propose:
                        await self.push.push_fact(
                            self.emotion.custom_fact(propose, "proactive"))
                        continue
                    sa_advice = self.route_planner.service_area_advice(snap)
                    if sa_advice and now - getattr(self, "_last_sa_advice", 0) > 1200:
                        self._last_sa_advice = now
                        await self.push.push_fact(
                            self.emotion.custom_fact(sa_advice, "proactive"))
                        continue
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # 别静默吞：这个循环一挂，陪玩就整个哑掉，而面板上看不出任何异常
                self.plugin.logger.warning("[陪玩循环] 本轮异常: %s: %s",
                                           type(exc).__name__, exc)
                await asyncio.sleep(15.0)

    def _hud(self) -> Any:
        """惰性创建 OCR（把配置里的游戏窗口标题一起带进去）。"""
        if self.hud_ocr is None:
            from ..adapters.hud_ocr import HudOcr
            self.hud_ocr = HudOcr(self.plugin, game_titles=self._game_window_titles)
        return self.hud_ocr

    async def _ocr_scan(self) -> tuple:
        """一次截屏识别，返回 (美景拍照问句, 普通场景话题)。

        2026-10 主人定调："识别到好风景要询问要不要拍照"——所以美景不再自动拍，
        改成先问一句，主人答应（面板按钮或直接说"拍吧"）才真的拍。

        2026-10 又一条：识屏**只读游戏窗口**。以前截整个虚拟桌面，把插件面板和
        DSH 桌面端的文字一起读进来，从里面匹到"隧道/雨"就当成游戏画面推给主人，
        他车停着没动却听到"隧道里这么黑"——那就是假话。现在游戏不在前台一个字都不读。
        """
        hud = self._hud()
        if not hud.is_available():
            return "", ""
        text = await asyncio.to_thread(hud.read_game_text)
        if not text:
            note = (hud.snapshot() or {}).get("note", "")
            self.plugin.logger.info("[OCR] 这次没读到游戏画面文字：%s", note or "(无说明)")
            return "", ""
        self.plugin.logger.info("[OCR] 读到 %d 字（%s）: %s",
                                len(text), (hud.snapshot() or {}).get("note", ""),
                                " ".join(text.split())[:120])
        # 天气线索（OCR 里出现雨/雪/雾就记一笔）——供猫娘电台与路况叙述引用
        low = text.lower()
        for kw, label in (("rain", "雨"), ("雨", "雨"), ("snow", "雪"),
                          ("雪", "雪"), ("fog", "雾"), ("雾", "雾")):
            if kw in low:
                self._last_weather_hint = label
                break
        # 等级检测：OCR 识别到数字等级 → 庆祝（这条是重要内容）
        level = self._extract_level(text)
        if level:
            celebrate = self.level_celebrate.update(level)
            if celebrate:
                return "", celebrate
        # 美景检测 → 问她要不要拍一张（记住场景，主人答应时才拍）
        scene = self.photo_album.detect(text)
        if scene and self.photo_album.can_shoot():
            self._pending_photo_scene = scene
            self._pending_photo_until = time.time() + 300.0
            return (f"[美景] 屏幕/画面里认出了「{scene}」这样的景色，"
                    f"车主现在车速 {self._snap_speed_label()}。"
                    f"请以猫娘语气问一句要不要拍一张留念"
                    f"（提醒他可以直接说「拍吧」，面板上也有「📸 拍一张」）", "")
        # 天气提醒（雨/雪）——事实行，走内容通道
        weather = self.proactive.weather_propose(text)
        if weather:
            return "", weather
        return "", self.scene_chat.topic_from_ocr(text)

    def _snap_speed_label(self) -> str:
        s = self._last_snap
        return f"{s.speed_kmh:.0f} km/h" if s is not None else "未知"

    async def diagnose(self) -> Dict[str, Any]:
        """自检：把每个子系统"到底能不能用"摊开说。

        为什么需要：这个插件吃过太多次"静默失效"的亏——识屏从来没过（宿主没装
        mss）、记忆从来没存住（宿主 store 返回 Ok 包装）、面板整片空白（一个字段
        类型不对整段渲染被掐断）。它们都不会报错，只会不工作。
        所以这里逐项**真的去试一次**，而不是查配置。
        """
        items: list = []

        def add(name: str, ok: bool, detail: str, hint: str = "") -> None:
            items.append({"name": name, "ok": bool(ok), "detail": detail, "hint": hint})

        # ── 遥测 ──
        st = self.telemetry_install_state or {}
        add("遥测文件", st.get("status") == "ok",
            st.get("detail", "未知"), "游戏未运行时才能写入 bin/win_x64/plugins/")
        add("游戏连接", bool(self._game_running), "游戏进程已识别" if self._game_running else "没检测到游戏",
            "启动欧卡2 后进入驾驶即自动连接")
        snap = self._last_snap
        add("遥测数据", snap is not None,
            (f"车速 {snap.speed_kmh:.0f} km/h · 油量 {snap.fuel_percent:.0f}%"
             if snap is not None else "还没有拿到有效帧"), "在主菜单时拿不到驾驶数据是正常的")

        # ── 地图与位置 ──
        mk = self.map_kb.snapshot()
        add("地图知识库", bool(mk.get("loaded")),
            f"{mk.get('roads', 0)} 段道路 · {mk.get('facilities', 0)} 个设施")

        # ── 记忆持久化（最容易静默失效的一块）──
        mem_counts = self.memory.snapshot()
        kinds = sum(1 for v in mem_counts.values() if v)
        stored = None
        try:
            stored = unwrap_result(await self.plugin.store.get(STORE_KEY))
        except Exception as exc:            # noqa: BLE001
            stored = None
            add("记忆读写", False, f"读取宿主 store 失败：{exc}", "检查插件 store 是否启用")
        else:
            in_store = isinstance(stored, dict) and any(bool(v) for v in stored.values())
            if in_store and self.memory.is_empty():
                add("记忆读写", False, "存档里有数据但内存是空的 → 加载链路有问题",
                    "这条一旦出现就是 bug，请把日志给我")
            else:
                add("记忆读写", True,
                    f"内存 {kinds} 类 / 存档 {'有' if in_store else '空'}"
                    + (f" · {self.memory.last_error}" if self.memory.last_error else ""))
        if self.memory._mirror is not None:      # noqa: SLF001
            exists = self.memory._mirror.exists()   # noqa: SLF001
            add("记忆兜底镜像", exists, str(self.memory._mirror),  # noqa: SLF001
                "跑第一单后才会写第一份镜像")
        add("旅途日记", True, f"今天 {self.diary.today_summary()['trips']} 单 · "
                              f"累计 {self.diary.snapshot()['count']} 条")
        add("地点簿", True, f"认识 {self.places.count()} 个地名")

        # ── 识屏（真的构造一次后端 + 看一眼前台窗口）──
        hud = self._hud()
        ocr = hud.snapshot()
        fg_ok, fg_title = hud.game_foreground()
        if ocr.get("available"):
            detail = (f"截屏后端 {ocr.get('capture') or 'gdi'} · OCR 后端就绪"
                      f" · 前台「{fg_title or '未知'}」")
            add("屏幕识别", True, detail,
                "" if fg_ok else "识屏只在游戏窗口是前台时生效，切到游戏里再测")
        else:
            add("屏幕识别", False, ocr.get("error", "不可用"), "没有它就只能靠遥测")

        # ── 播报链路 ──
        ps = self.push.snapshot()
        add("播报模式", not self.cfg.dry_run,
            "正式播报" if not self.cfg.dry_run else "试运行（只记录，不推送）",
            "面板设置页可切换")
        add("播报队列", True,
            f"待发 {ps['pending']} · 去重丢弃 {ps['dropped']}")

        # ── 陪玩引擎 ──
        cs = self.companion.snapshot()
        fired = len(cs.get("last", {}))
        add("陪玩引擎", cs.get("dimensions", 0) > 0,
            f"{cs.get('dimensions')} 个维度 · 已触发 {fired} 个 · 间隔 {cs.get('gap_s'):.0f}s")
        add("玩法画像", self._playstyle is not None,
            (self._playstyle.label if self._playstyle else "还没判定（进入驾驶后开始）"))
        add("本局累计", True,
            f"{len(self._activity)} 条活动 · 台账 {len(self.ledger.month_summary())} 项")

        failed = [i for i in items if not i["ok"]]
        return {"ok": not failed, "items": items,
                "failed": [i["name"] for i in failed]}

    async def take_photo(self, scene: str = "", auto: bool = False) -> Dict[str, Any]:
        """拍一张：面板按钮 / 主人说"拍吧" / 她自己按场景拍。

        `auto=False` 表示这是明确要求，跳过相册冷却。
        """
        hud = self._hud()
        if not hud.is_available():
            return {"ok": False, "summary": "现在抓不到画面喵（截屏组件不可用）"}
        if not scene:
            scene = self._pending_photo_scene or "scenery"
        record = await self.photo_album.shoot(scene, hud,
                                              force=not auto,
                                              allow_background=not auto)
        if not record:
            reason = getattr(self.photo_album, "last_reason", "")
            if reason == "cooldown":
                return {"ok": False, "summary": "刚拍过一张喵，过十分钟再拍~"}
            if "不是游戏" in reason:
                return {"ok": False,
                        "summary": f"抓不到画面喵：{reason}——把游戏切到前台再拍"}
            return {"ok": False, "summary": f"拍照没成喵：{reason or '原因不明'}"}
        self._pending_photo_scene = ""
        self.recall.on_relationship("last_photo", {"value": scene}, importance=0.3)
        await self.push.push_fact(
            self.emotion.custom_fact(
                f"[拍照] 已经把刚才的画面存进相册（场景：{scene}）。"
                "请以猫娘语气说一句「拍好了」，提一句这个画面值得留着", "photo"),
            priority=IMPORTANT)
        return {"ok": True,
                "summary": f"📸 拍好了（{scene}）——面板「地图 → 美景相册」里能看到"}

    def _top_city_name(self) -> str:
        """跑得最多的城市（陪玩回忆维度用）。"""
        try:
            top = self._top_entries("cities")
            if top:
                return str(top[0].get("key", "") or "")
        except Exception:
            pass
        return ""

    # ── 内容层：地点文化 / 开场故事 / 途经播报（事实行交宿主 LLM 演绎）──

    def _opening_story_fact(self, snap, hints: Optional[list] = None) -> str:
        """每单开场：把这一单的客观信息 + 起点地域特色要求交给宿主 LLM。

        2026-10 主人补需求："开始任务的里程剩余里程什么的播报"——所以这里除了
        起点/终点/货物，还要带上全程里程、出发时剩余里程、预计到达时间，
        以及"这箱油够不够跑完全程"（和加油判断同一套判据）。
        """
        km = snap.planned_distance_km
        eta = km / 65.0 if km else 0.0
        hour = (snap.time_abs_min // 60) % 24 if snap.time_abs_min else -1
        when = ""
        if 0 <= hour < 5 or hour >= 23:
            when = "深夜出发"
        elif 5 <= hour < 9:
            when = "清晨出发"
        elif 17 <= hour < 22:
            when = "傍晚出发"
        bits = [f"起点 {snap.city_src or '未知'}", f"终点 {snap.city_dst or '未知'}",
                f"货物 {snap.cargo or '普通货'}", f"全程约 {km} km"]
        # 货物家族（用游戏真实 cargo_id/显示名判断，认不出就不说）
        fam_label, fam_tip = self.cargo_book.describe(
            getattr(snap, "cargo_id", ""), getattr(snap, "cargo", ""),
            float(getattr(snap, "cargo_mass", 0) or 0) / 1000.0)
        if fam_label:
            bits.append(f"货物类型：{fam_label}")
        if eta:
            bits.append(f"预计 {eta:.1f} 小时")
        remain = float(getattr(snap, "route_remaining_km", 0) or 0)
        if remain:
            bits.append(f"出发时导航剩余 {remain:.0f} km")
        if snap.time_abs_min and eta:
            arr = int(snap.time_abs_min + eta * 60) % 1440
            bits.append(f"按当前时段推算约 {arr // 60:02d}:{arr % 60:02d} 到达")
        # 一路的油够不够：和 fuel_advice 同一判据（续航 vs 全程）
        rng = float(getattr(snap, "fuel_range_km", 0) or 0)
        if rng and km:
            bits.append("这箱油够跑完全程，不用专门找加油站"
                        if rng >= km * 1.1 else
                        f"续航只有 {rng:.0f} km，跑不完全程，中途必须加油")
        if when:
            bits.append(when)
        if snap.is_city:
            bits.append("当前在市区发车")
        mem = ""
        if hints:
            mem = "、".join(str(h) for h in hints[:2])
        fact = ("[行程开场] " + "；".join(bits) + "。"
                "请以猫娘语气开个场：一句介绍起点的地域特色/文化（可以讲当地风物、"
                "路况习惯），一句说这趟的打算（顺带把里程/预计时间说清楚）。"
                "不要念参数，像副驾随口聊。")
        if mem:
            fact += f"（可顺带提起：{mem}）"
        if fam_tip:
            fact += f"（这趟的驾驶要点，用一句话自然带出来：{fam_tip}）"
        return fact

    def _arrival_culture_fact(self, snap) -> str:
        """到达目的地：让猫娘讲讲这个地方的特色与文化。"""
        city = snap.city_dst or ""
        bits = [f"目的地 {city or '（未知城市）'}"]
        if snap.job_delivered_revenue:
            bits.append(f"本单收入 {snap.job_delivered_revenue} €")
        if snap.job_cargo_damage is not None:
            bits.append(f"货物完好率 {(1 - snap.cargo_damage) * 100:.0f}%")
        return ("[到达] " + "；".join(bits) + "。"
                "请以猫娘语气收个尾：介绍一下这个地方的风土/文化/值得一看的东西"
                "（如果知道当地特色就点一句），并祝贺主人跑完这单。")

    def _passing_place_fact(self, place: dict) -> str:
        """途经已知地点：让她随口讲一句这个地方的特点。"""
        name = place.get("name", "")
        dist = place.get("distance_km", 0.0)
        visits = int(place.get("visits", 0) or 0)
        tail = "、我们以前来过这里" if visits > 1 else ""
        return (f"[途经] 正经过 {name} 附近（约 {dist:.1f} km）{tail}。"
                f"请以猫娘语气随口说一句这个地方的特点或风物（一句话，别念参数）")

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
            # 地点簿学习：接单时的坐标就是起点城市的位置
            snap0 = ev.snapshot
            if snap0.city_src:
                self.places.learn(snap0.city_src, snap0.world_x, snap0.world_z, "city")
            if snap0.city_dst:
                # 终点坐标未知，等到达时再学（这里只登记名字，坐标留待 job_delivered）
                pass
            lines = []
            preview = self.route_planner.preview(ev.snapshot)
            if preview:
                lines.append(self.persona.polish(preview))
            if lines:
                self._spawn(self.push.push_direct("\n".join(lines)))
            # ── 每单开场故事 + 起点地域文化（事实行交宿主 LLM 演绎，不硬编码台词）
            self._set_bias("cargo", 300.0)
            if getattr(self.cfg, "content_opening_story", True):
                self._spawn(self.push.push_fact(self._opening_story_fact(ev.snapshot, hints)))
            return
        if ev.name == "crash":
            # 计数已由 _track_stats 记一次（此处再 +1 会把单次事故计成两次）
            crash_hint = self.recall.on_crash()
            snap = ev.snapshot
            parts = ", ".join(f"{k} {v * 100:.0f}%" for k, v in
                              snap.damage_parts.items() if v > 0.01)
            # 事故必走 respond（事实行交宿主 LLM 按人设说话）：旧版只 push_direct
            # 直出硬编码短句，猫娘"没有 llm 对话"，主人听不到她的声音。
            self._spawn(self.push.push_fact(self.emotion.fact_prompt(
                "crash",
                parts=parts or "无明显损伤",
                speed=float(ev.data.get("speed_kmh", snap.speed_kmh)),
            ), priority=CRITICAL))
            if crash_hint:
                self._spawn(self.push.push_fact(
                    self.emotion.custom_fact(crash_hint, "crash"),
                    priority=CRITICAL))
            self._spawn(self._crash_chain(ev))
            self._set_bias("care", 300.0)     # 刚撞完：接下来的闲聊先聊关心
            return
        if ev.name == "job_delivered" and ev.snapshot:
            self._spawn(self._finish_trip(ev))  # 内部含归档，避免统计竞态
            self._set_bias("memory", 300.0)   # 刚跑完一单：聊聊这一趟与老记忆
            # 到货只发一条：结算数据行（交宿主 LLM 收尾）。
            # 旧版还额外盲推一句写死的"到站检查"话术，和总结叠在一起像复读。
            snap_d = ev.snapshot
            # 城市打卡：用地点簿的真实到访次数判断"第一次来"还是"第 N 次"
            visits = self.places.query_visits(snap_d.city_dst) if snap_d.city_dst else 0
            if snap_d.city_dst and visits == 0:
                self._spawn(self.push.push_fact(
                    self.emotion.custom_fact(
                        f"[打卡] 第一次把货送到 {snap_d.city_dst}，这个城市是新地图",
                        "job_delivered"), priority=CHAT, topic="city_first"))
            elif snap_d.city_dst and visits >= 3:
                self._spawn(self.push.push_fact(
                    self.emotion.custom_fact(
                        f"[打卡] {snap_d.city_dst} 已经是第 {visits + 1} 次来了，"
                        f"这地方快成老朋友了", "job_delivered"),
                    priority=CHAT, topic="city_repeat"))
            if snap_d.city_dst:
                self.places.learn(snap_d.city_dst, snap_d.world_x, snap_d.world_z, "city")
                self._spawn(self.memory.save())
            # ── 到达地文化介绍（交宿主 LLM 讲这个地方的风土，不硬编码）
            if getattr(self.cfg, "content_arrival_culture", True):
                self._spawn(self.push.push_fact(self._arrival_culture_fact(snap_d)))
            # 到货升级提议（赚了钱 → 车行升级，L3 低频）
            revenue = ev.data.get("revenue", ev.snapshot.job_delivered_revenue)
            if revenue and revenue > 8000 and getattr(self, "_last_upgrade_tip", 0) == 0:
                self._last_upgrade_tip = 1
                self._spawn(self.push.push_direct(
                    self.persona.polish(f"赚了 {revenue} € 喵，要不要去车行升级下引擎？")))
            return
        # refuel / game_end 交给通用路径：_render 里已有事实行模板，
        # 旧的 catgirl.existence_line 是硬编码台词（与"不写死台词"的定调冲突），已删除
        if ev.name in ("refuel", "game_end"):
            allowed, reason = self.arbiter.decide(ev.name, ev.snapshot)
            prompt = self._render(ev) if allowed else None
            if prompt:
                # 退出游戏时补一段"今日收车小结"（今天真跑过才说）
                if ev.name == "game_end":
                    today = self.diary.render_today()
                    if today:
                        prompt = f"{prompt}\n{today}"
                self._spawn(self.push.push_fact(prompt, priority=IMPORTANT))
            return
        if ev.name in ("ferry", "train"):
            # 过境：站点名是遥测里唯一可靠的地名 → 学习 + 介绍两端风土
            src = str(ev.data.get("source", "") or "")
            dst = str(ev.data.get("target", "") or "")
            snap_p = ev.snapshot
            if snap_p is not None:
                if src:
                    self.places.learn(src, snap_p.world_x, snap_p.world_z, "ferry_station")
                if dst:
                    self.places.learn(dst, snap_p.world_x, snap_p.world_z, "ferry_station")
                self._spawn(self.memory.save())
            # 事实行统一走 _render（模板在 FACT_TEMPLATES 里，不再是内联硬编码指令）
            self._spawn(self.push.push_fact(self._render(ev), priority=IMPORTANT,
                                            topic="transit"))
            return
        # mountain_pass 已并入内容类路由（_render 里有对应事实行模板）
        # ── 内容类事件：不走仲裁，直接 respond ──────────────────────
        # 这些都是一次性/长间隔的真实内容（行程分段、距离锚点、交付时间、
        # 收费站、挂车），旧版和普通事件一样过仲裁 → 常被"场景门控"或
        # "限流单槽窗口"吞掉。实测主人反馈："任务分段播报、时间提醒从没响过"。
        # 它们本身频率极低（一次任务 3~4 条），直接放行不会刷屏。
        if route_of(ev.name) == ROUTE_CONTENT:
            prompt = self._render(ev)
            if prompt:
                self._spawn(self.push.push_fact(prompt, priority=IMPORTANT))
            return
        if ev.name == "fine":
            self._set_bias("banter", 180.0)   # 刚吃罚单：允许她损你两句
        if ev.name == "cargo_damage":
            self._set_bias("cargo", 240.0)    # 货损：先盯着这单货
        if ev.name == "vehicle_damage":
            self._set_bias("truck", 240.0)
        allowed, reason = self.arbiter.decide(ev.name, ev.snapshot)
        if not allowed:
            # 留痕：面板决策日志现在也是滚动历史，这里再记一条便于对账
            self.plugin.logger.info("[事件·抑制] %s ← %s", ev.name, reason)
            return
        prompt = self._render(ev)
        if prompt:
            prio = CRITICAL if ev.name in CRITICAL_EVENTS else IMPORTANT
            if ev.name == "air_pressure_low" and ev.data.get("emergency"):
                prio = CRITICAL          # 紧急气压报警按事故级处理
            self._spawn(self.push.push_fact(prompt, priority=prio))

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
        """事故叙事链：伤情检查 → 维修评估/救援 → 事后安慰（L1 时间轴）。

        三个阶段全部走 respond（事实行 → 宿主 LLM 按人设演绎）。旧版是三段
        push_direct 硬编码台词，主人听不到猫娘自己的话（"车祸没有 llm 对话"）。
        """
        snap = ev.snapshot
        damage = snap.max_damage
        parts_detail = ", ".join(
            f"{k} {v * 100:.0f}%" for k, v in snap.damage_parts.items() if v > 0.01)
        # 伤情检查（5s 后）
        await asyncio.sleep(5)
        if damage >= 0.5:
            await self.push.push_fact(self.emotion.custom_fact(
                f"事故伤情检查：车辆损伤 {damage * 100:.0f}%（{parts_detail}），"
                f"损伤过半，继续开会持续恶化，该靠边停车叫救援拖车", "crash"))
        else:
            repair = round(damage * 1000)
            await self.push.push_fact(self.emotion.custom_fact(
                f"事故伤情检查：车辆损伤 {damage * 100:.0f}%（{parts_detail}），"
                f"修车预估 {repair} €，顺口提醒主人过弯慢一点", "crash"))
        # 事后安慰（30s 后）
        await asyncio.sleep(25)
        await self.push.push_fact(self.emotion.custom_fact(
            "事故已过 30 秒，主人仍在正常行驶、情绪平稳：安抚一句，"
            "提示慢慢开不着急", "crash"))

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
        # 故事层：写一条旅途日记（收车小结 / 回归问候 / 陪聊回忆都读它）
        try:
            self.diary.write(trip, note=getattr(self, "_last_weather_hint", ""))
        except Exception as exc:            # noqa: BLE001
            self.plugin.logger.warning("写日记失败: %s", exc)
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
        lines = []
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
        summary = self.trip_summary.build_fact(trip)
        lines.append(summary)
        # 新纪录/连续无事故庆祝（同样只给事实，不写死台词）
        profile_result = self.profile.record(trip)
        celebration = self.profile.record_celebration(profile_result)
        if celebration:
            lines.append(celebration)
        # 到达时顺手把"今天整体跑成什么样"提一句（收车小结的铺垫）
        today = self.diary.today_summary()
        if today["trips"] >= 2:
            lines.append(f"今天累计已跑 {today['km']:.0f} km、{today['trips']} 单")
        # 油耗风格反馈（真实对比：这趟 vs 历史均值）
        verdict = self.profile.fuel_verdict(trip.get("fuel_avg", 0))
        if verdict:
            lines.append(verdict)
        await self.push.push_fact(
            self.emotion.custom_fact("\n".join(lines), "job_delivered"),
            priority=IMPORTANT, topic="trip_summary")

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
            offence = s.fine_offence or "违规"
            note = offence_note(offence)
            fact = self.emotion.fact_prompt(ev.name, amount=s.fine_amount, offence=offence)
            if note:
                fact += f"（对应规则：{note}）"
            return fact
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
            # 回归问候：如果记忆里有"上次收车"，就接着上次说
            back = self.diary.render_welcome_back()
            base = self.emotion.fact_prompt(ev.name)
            if back:
                base = f"{base}\n{back}"
            return base + self._greeting()
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
        if ev.name == "mountain_pass":
            return self.emotion.fact_prompt(
                ev.name, climb_m=ev.data.get("climb_m", 0),
                altitude=ev.data.get("altitude", 0))
        if ev.name == "ferry":
            return self.emotion.fact_prompt(
                ev.name, source=ev.data.get("source", ""), target=ev.data.get("target", ""))
        if ev.name == "train":
            return self.emotion.fact_prompt(
                ev.name, source=ev.data.get("source", ""), target=ev.data.get("target", ""))
        if ev.name == "time_relaxed":
            return self.emotion.fact_prompt(ev.name, min=ev.data.get("min", 0))
        if ev.name == "time_tight":
            return self.emotion.fact_prompt(ev.name, min=ev.data.get("min", 0))
        if ev.name == "early_arrival":
            return self.emotion.fact_prompt(ev.name, min=ev.data.get("min", 0))
        if ev.name == "cargo_damage":
            return self.emotion.fact_prompt(ev.name, pct=ev.data.get("pct", 0))
        if ev.name == "vehicle_damage":
            # 按部位说清"伤在哪、大概多少修车费"（估算口径）
            return self.emotion.fact_prompt(
                ev.name,
                percent=ev.data.get("percent", 0.0),
                parts=ev.data.get("parts", ""),
                detail=damage_sentence(s.damage_parts, s.max_damage),
            )
        if ev.name == "trailer_attach":
            label, tip = trailer_config(int(getattr(s, "trailer_count", 0) or 0))
            return self.emotion.fact_prompt(
                ev.name, plate=s.trailer_license,
                config=(f"编组：{label}（{tip}）" if label and tip else ""))
        if ev.name == "brake_temp_high":
            return self.emotion.fact_prompt(
                ev.name, temp=ev.data.get("temp", 0.0),
                retarder_hint=("（缓速器开着能帮上忙）" if ev.data.get("retarder")
                               else "（长下坡记得用缓速器/发动机制动）"))
        if ev.name == "air_pressure_low":
            return self.emotion.fact_prompt(
                ev.name, bar=ev.data.get("bar", 0.0),
                emergency_hint=("是紧急气压报警，必须马上靠边停车"
                                if ev.data.get("emergency") else "刹车会变软"))
        # 机务/操作类：参数直接来自遥测
        if ev.name in ("adblue_low", "battery_low",
                       "lights_off_night", "park_brake_moving", "wheels_airborne",
                       "engine_stalled", "high_beam_on", "blinker_forgotten",
                       "engine_warning", "overheat", "rested"):
            kw = dict(ev.data)
            kw.setdefault("speed", s.speed_kmh)
            return self.emotion.fact_prompt(ev.name, **kw)
        return None

    def _greeting(self) -> str:
        """按**游戏内**时段问候。

        2026-10 翻车：旧版用 `datetime.now()`（真实时钟）判时段。主人晚上 23:36
        玩、游戏里是上午 8:34，猫娘却说"深夜了喵… 我陪你跑夜路"——他当场判为假话。
        真实时钟不是游戏时间：游戏内时间只从遥测 `time_abs_min` 拿，拿不到就不提时段。
        """
        streak = self._bump_streak()
        snap = self._last_snap
        mins = int(getattr(snap, "time_abs_min", 0) or 0) if snap is not None else 0
        if mins <= 0:
            base = " 上车啦，今天想拉什么货？"
        else:
            hour = (mins // 60) % 24
            minute = mins % 60
            clock = f"游戏内 {hour:02d}:{minute:02d}"
            if 5 <= hour < 12:
                base = f" 现在是{clock}（上午），今天想拉什么货？"
            elif 12 <= hour < 18:
                base = f" 现在是{clock}（下午），今天跑哪条线？"
            elif 18 <= hour < 23:
                base = f" 现在是{clock}（游戏里天黑了），今晚跑哪条线？"
            else:
                base = f" 现在是{clock}（游戏里深夜），我陪你跑夜路！"
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
        # 陪玩关键：主人说过的话要记住，之后她会自己主动提起
        try:
            self.recall.on_player_remark(text)
            self._spawn(self.memory.save())
        except Exception as exc:            # noqa: BLE001
            self.plugin.logger.warning("记住主人这句话失败: %s", exc)
        # "拍吧" —— 她问了要不要拍照，主人答应了（口语/面板按钮两条路都通）
        if any(k in text for k in ("拍照", "拍一张", "拍吧", "拍下来", "照一张",
                                   "来一张", "截图", "留个纪念")):
            return await self.take_photo()
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

