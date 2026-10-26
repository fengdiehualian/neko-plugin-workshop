"""运行时：装配 mod 遥测、仲裁、推送、口吻、知识库 + WS 面板推送。"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from typing import Any, Dict, Optional

from ..adapters.llm_client import LLMProvider
from ..adapters.push_sender import PushSender
from ..adapters.telemetry import TelemetryClient
from ..catgirl.bridge import CatgirlBridge
from .achievement import Achievement
from .arbiter import Arbiter
from .challenge import Challenge
from .config_model import FishpowerConfig
from .contracts import FishEvent, FishState
from .knowledge import KnowledgeBase
from .ledger import Ledger
from .memory import MemoryStore
from .mood import Persona
from .proactive import Proactive
from .recall import Recall
from .safety_guard import SafetyGuard
from .small_talk import SmallTalk
from .templates import EmotionRenderer
from .trip_summary import TripSummary

ACTIVITY_TITLES = {
    "cast": "抛竿",
    "bite": "咬钩",
    "caught": "上钩",
    "miss": "跑鱼",
    "sold": "卖鱼",
    "bet": "下注",
    "roulette_result": "轮盘开奖",
    "slot_result": "老虎机",
    "grill_start": "开始烤鱼",
    "grill_done": "烤鱼完成",
    "boss_spawn": "Boss 出现",
    "boss_death": "Boss 击杀",
    "player_death": "玩家倒下",
    "discovered": "图鉴新鱼",
    "kill": "击杀",
    "seagull": "海鸥抢鱼",
    "achievement": "成就解锁",
    "game_start": "进入游戏",
    "game_end": "退出游戏",
}

# 游戏成就字段名 → 中文名（mod 反射推送的静态解锁字段）
ACHIEVEMENT_NAMES = {
    "_firstCreatureUnlocked": "初次钓获",
    "_hasFlyingboatAchievement": "飞行船",
    "A01_FirstCreature": "初次钓获",
    "A02_Seagull": "海鸥",
    "A03_Boss1": "击败首个 Boss",
    "A05_DripCreature": "滴液生物",
    "A06_FlyingBoat": "飞行船",
    "A07_BoatUpgrade": "船升级",
    "A08_Boss2": "击败 Boss II",
    "A09_BurntCreature": "烤鱼",
    "A10_EatMiniBoss": "吃迷你 Boss",
    "A11_KillscoreMultiplier": "击杀倍率",
    "A12_360Noscope": "360 无镜",
    "A13_Boss3": "击败 Boss III",
    "A14_GrillMaster": "烧烤大师",
    "A15_SellWorth": "高价卖出",
    "A16_AllCreatures": "全图鉴",
    "A17_Roulette": "轮盘赌",
    "A18_LegendarySkin": "传奇皮肤",
    "A19_AllAttachments": "全部配件",
    "A20_Boss4": "击败 Boss IV",
    "A21_Boss5": "击败 Boss V",
    "A22_SeagullDynamite": "海鸥炸弹",
    "A23_AllDripCreatures": "全滴液",
    "A24_MaxBoat": "满级船",
    "A25_FinishGame": "通关",
    "A26_FastBoss": "速通 Boss",
    "A27_Speedrunner": "速通者",
    "A28_Boss5MeleeKills": "近战 Boss V",
}


class FishpowerRuntime:
    """插件运行时。"""

    def __init__(self, plugin: Any, config: FishpowerConfig) -> None:
        self.plugin = plugin
        self.cfg = config
        host_persona = getattr(plugin, "persona", None)
        self.persona = Persona(host_persona)
        self.catgirl = CatgirlBridge()
        self.llm = LLMProvider()
        self.emotion = EmotionRenderer(self.persona, llm=self.llm)
        self.safety = SafetyGuard(config)
        self.arbiter = Arbiter(config, self.safety)
        self.arbiter.broadcast_categories = dict(config.broadcast_categories)
        self.arbiter.broadcast_frequency = config.broadcast_frequency
        self.push = PushSender(plugin, dry_run=config.dry_run)
        self.knowledge = KnowledgeBase()
        self.memory = MemoryStore(plugin.store)
        self.ledger = Ledger(self.memory)
        self.trip_summary = TripSummary(self.memory)
        self.small_talk = SmallTalk()
        self.proactive = Proactive()
        self.challenge = Challenge(self.memory)
        self.achievement = Achievement(self.memory)
        self.recall = Recall(self.memory)
        self.scene_chat = None  # 惰性：OCR 可用时创建（照 pawpilot）
        self.hud_ocr = None
        self._session = {"caught": 0, "new_journal": 0, "sold_total": 0,
                         "gamble_net": 0, "last_fish": ""}
        self._state = FishState()
        self._last_island = ""
        self._last_event_ts = 0.0
        self._activity: list = []
        self._bg_tasks: set = set()
        self._telemetry: Optional[TelemetryClient] = None
        self._tick_task: Optional[asyncio.Task] = None
        self._propose_task: Optional[asyncio.Task] = None
        self._bg_thread: Optional[threading.Thread] = None
        self._bg_stop = threading.Event()
        self._bg_loop_ref: Optional[asyncio.AbstractEventLoop] = None

    def _spawn(self, coro) -> None:
        """创建后台任务并跟踪，防泄漏；异常打日志。

        必须在 runtime 自己的后台循环（_bg_loop）里调用；宿主 lifecycle/entry
        的 loop 与后台线程不同，跨 loop create_task 会抛 RuntimeError。
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
            except Exception as exc:
                if not isinstance(exc, asyncio.CancelledError):
                    self.plugin.logger.warning("bg task error: %s", exc)
        task.add_done_callback(_log_err)
        task.add_done_callback(self._bg_tasks.discard)

    # ── 设置持久化（data/config/ui_settings.json） ──

    def _ui_settings_path(self):
        from pathlib import Path
        return Path(__file__).resolve().parent.parent / "data" / "config" / "ui_settings.json"

    def _ui_settings(self) -> dict:
        try:
            data = json.loads(self._ui_settings_path().read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    async def settings_save(self) -> None:
        import os
        data = self._ui_settings()
        data.update({
            "dry_run": self.cfg.dry_run,
            "voice_styles": list(self.persona.voice_styles),
            "broadcast_frequency": self.arbiter.broadcast_frequency,
            "broadcast_categories": dict(self.arbiter.broadcast_categories),
            "theme": getattr(self, "_theme", "dark"),
        })
        path = self._ui_settings_path()
        tmp = path.with_suffix(".json.tmp")
        try:
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, path)
        except OSError as exc:
            self.plugin.logger.warning("settings_save failed: %s", exc)

    async def settings_load(self) -> None:
        saved = self._ui_settings()
        if not saved:
            return
        style = saved.get("voice_styles")
        if isinstance(style, list) and style:
            self.persona.set_voice_styles(style)
        if "dry_run" in saved:
            self.set_dry_run(bool(saved["dry_run"]))
        if saved.get("broadcast_frequency"):
            self.set_frequency(saved["broadcast_frequency"])
        cats = saved.get("broadcast_categories")
        if isinstance(cats, dict):
            for k in self.arbiter.broadcast_categories:
                if k in cats:
                    self.arbiter.broadcast_categories[k] = bool(cats[k])
        t = saved.get("theme")
        if t in ("light", "dark"):
            self._theme = t
        else:
            self._theme = "dark"

    def set_theme(self, theme: str) -> bool:
        if theme not in ("light", "dark"):
            return False
        self._theme = theme
        return True

    # ── 配置/控制 ──

    def apply_config(self, config: FishpowerConfig) -> None:
        self.cfg = config
        self.push.dry_run = config.dry_run
        self.arbiter.broadcast_categories = dict(config.broadcast_categories)
        self.arbiter.broadcast_frequency = config.broadcast_frequency

    def set_dry_run(self, value: bool) -> None:
        self.cfg.dry_run = bool(value)
        self.push.dry_run = bool(value)

    def pause(self) -> None:
        self.safety.pause()

    def resume(self) -> None:
        self.safety.resume()

    def set_frequency(self, frequency: str) -> bool:
        from .event_catalog import BROADCAST_FREQUENCY_MULTIPLIERS
        if frequency not in BROADCAST_FREQUENCY_MULTIPLIERS:
            return False
        self.arbiter.broadcast_frequency = frequency
        return True

    def set_category(self, category: str, enabled: bool) -> bool:
        if category not in self.arbiter.broadcast_categories:
            return False
        self.arbiter.broadcast_categories[category] = bool(enabled)
        return True

    def llm_config(self) -> dict:
        data = self._ui_settings()
        llm = data.get("llm") if isinstance(data, dict) else {}
        return llm if isinstance(llm, dict) else {}

    async def save_llm_config(self, config: dict) -> bool:
        import os
        data = self._ui_settings()
        cfg = {k: str(config.get(k, "") or "").strip()
               for k in ("provider", "model", "api_key", "base_url")}
        data["llm"] = cfg
        path = self._ui_settings_path()
        tmp = path.with_suffix(".json.tmp")
        try:
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, path)
        except OSError as exc:
            self.plugin.logger.warning("save_llm_config failed: %s", exc)
            return False
        self._wire_llm()
        return True

    def _wire_llm(self) -> None:
        """LLM 配置：ui_settings 配了 → 自建 LLM 优先；没配 → 模板降级。"""
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

    # ── 生命周期 ──

    async def start(self) -> Dict[str, Any]:
        if not self.cfg.enabled:
            return {"status": "disabled"}
        await self.settings_load()
        await self.memory.load()
        self._wire_llm()
        # 后台循环必须跑在独立 daemon 线程里：宿主 lifecycle startup 用
        # asyncio.run()（临时 loop），create_task 的后台任务会在返回后被
        # 取消（neko_warthunder 同款 threading.Thread 模式）。
        self._bg_stop.clear()
        self._bg_thread = threading.Thread(
            target=self._bg_runner, daemon=True, name="fishpower-bg")
        self._bg_thread.start()
        return {"status": "ready",
                "dry_run": self.cfg.dry_run,
                "telemetry": self._telemetry_status(),
                "llm": self.llm.snapshot(),
                "knowledge": self.knowledge.snapshot()}

    def _bg_runner(self) -> None:
        """后台线程入口：自己的事件循环跑 tick/propose/telemetry。"""
        try:
            asyncio.run(self._bg_loop())
        except Exception as exc:
            self.plugin.logger.exception("后台循环异常退出: %s", exc)

    async def _bg_loop(self) -> None:
        """后台主循环：telemetry 连接 + tick + propose（同线程内任务）。"""
        self._bg_loop_ref = asyncio.get_running_loop()
        self._telemetry = TelemetryClient(
            self.cfg.telemetry_host, self.cfg.telemetry_port,
            on_event=self._on_event, on_state=self._on_state,
            on_registry=self._on_registry, logger=self.plugin.logger)
        self._tick_task = asyncio.create_task(self._tick_loop())
        self._propose_task = asyncio.create_task(self._propose_loop())
        try:
            while not self._bg_stop.is_set():
                await asyncio.sleep(0.5)
        except asyncio.CancelledError:
            pass
        finally:
            for task in (self._tick_task, self._propose_task):
                if task:
                    task.cancel()
            if self._telemetry:
                self._telemetry.close()
            for task in list(self._bg_tasks):
                task.cancel()
            self._bg_loop_ref = None

    async def shutdown(self) -> None:
        self._bg_stop.set()
        if self._bg_thread and self._bg_thread.is_alive():
            self._bg_thread.join(timeout=5.0)
        self._bg_thread = None

    def _telemetry_status(self) -> str:
        if self._telemetry and self._telemetry.connected:
            return "connected"
        if self._telemetry and self._telemetry.last_error:
            return f"error: {self._telemetry.last_error}"
        return "mod-not-running"

    async def _tick_loop(self) -> None:
        """重连循环：mod 未连接时每 2s 重试；连接后读循环由 TelemetryClient 管理。

        连接建立调用 _on_game_start（内部有 60s 冷却，短断线重连不重复仪式）；
        未连接时状态机回 NO_GAME（游戏没开不聊游戏内容）。
        """
        while True:
            try:
                if self._telemetry and not self._telemetry.connected:
                    ok = await self._telemetry.connect()
                    if ok:
                        self._telemetry.start()
                        self._on_game_start()
                    else:
                        self.arbiter.scenario.force_no_game()
                await asyncio.sleep(2.0)
            except asyncio.CancelledError:
                raise
            except Exception:
                await asyncio.sleep(2.0)

    async def _propose_loop(self) -> None:
        """陪伴循环：像朋友一样看屏幕、搭话、关心（全走 respond 宿主现编）。

        状态门控（照欧卡 §5）：只有游戏开着且在交互状态（钓鱼/赌场/Boss）
        才 OCR 看屏幕/主动提议/闲聊；游戏没开或主菜单 → 安静。
        """
        import time as _time
        while True:
            try:
                await asyncio.sleep(15.0)
                now = _time.time()
                # 记忆衰减 + 周期存档（每 10 分钟）
                if now - getattr(self, "_last_mem_save", 0) >= 600:
                    self._last_mem_save = now
                    self.memory.decay()
                    await self.memory.save()
                # 状态门控：非交互状态（游戏没开/主菜单）不主动说话
                if not self.arbiter.scenario.interactive():
                    continue
                # 画面畅聊：OCR 每 3 分钟看一次屏幕（陪玩核心，像朋友在旁）
                if now - getattr(self, "_last_ocr_at", 0) >= 180:
                    self._last_ocr_at = now
                    scene_topic = await self._ocr_scene_topic()
                    if scene_topic:
                        await self.push.push_fact(
                            self.emotion.custom_fact(scene_topic, "chatter"))
                        continue
                # 主动关心（换饵/换岛/升级…）走 respond
                propose = self.proactive.propose(now, self._state, self._session)
                if propose:
                    await self.push.push_fact(
                        self.emotion.custom_fact(propose, "chatter"))
                    continue
                # 场景闲聊保底（低频，走 respond）
                topic = self.small_talk.random_topic(self._talk_key(), now)
                if topic:
                    await self.push.push_fact(
                        self.emotion.custom_fact(topic, "chatter"))
            except asyncio.CancelledError:
                raise
            except Exception:
                await asyncio.sleep(15.0)

    async def _ocr_scene_topic(self) -> Optional[str]:
        """OCR 截屏识别 → 知识库理解 → 自然情景互动。

        流程（照欧卡猫娘）：
        1. 场景话题池关键词命中（鱼/饵/Boss/赌场…）→ 聊相关
        2. 知识库机制/鱼种理解（OCR 文本里出现游戏内容）→ 引用知识聊
        3. 都看不懂 → 安静（猫娘不是十万个为什么，不把屏幕文字复述出来）
        """
        if not self._ensure_ocr():
            return None
        text = await asyncio.to_thread(self.hud_ocr.read_text)
        if not text:
            return None
        # 1) 场景话题池（关键词快速命中，聊游戏情景）
        topic = self.scene_chat.topic_from_ocr(text)
        if topic:
            return topic
        # 2) 知识库理解：OCR 里出现机制词/鱼种 → 引用知识自然聊
        kb_topic = self._kb_ocr_topic(text)
        if kb_topic:
            return kb_topic
        # 3) 看不懂 → 安静
        return None

    def _kb_ocr_topic(self, text: str) -> Optional[str]:
        """知识库理解 OCR：命中机制词/鱼种/饵 → 聊知识（不是复述屏幕）。"""
        try:
            from .knowledge import MECHANIC_KEYWORDS, MECHANICS
            lower = text.lower()
            # 机制词（Boss/烤鱼/图鉴等游戏场景）。
            # 赌场/老虎机排除：屏幕出现"赌场/下注"字样不代表玩家在赌（误判源）。
            for key, kws in MECHANIC_KEYWORDS.items():
                if key in ("casino", "slot"):
                    continue
                if any(kw in lower for kw in kws):
                    tip = MECHANICS.get(key)
                    if tip:
                        return f"主人屏幕上出现了「{key}」相关内容喵！{tip}"
            # 鱼种/饵（知识库图鉴匹配）
            for name in self.knowledge.creature_names():
                if name and name.lower() in lower:
                    info = self.knowledge.creature_info(name)
                    return (f"诶，我看到主人屏幕上有 {name} 喵！"
                            f"{info if info else '这种鱼我图鉴里有印象~'}")
            for bait in self.knowledge.bait_names():
                if bait and bait.lower() in lower:
                    return f"主人屏幕上有 {bait} 饵喵！用对饵钓对鱼~"
        except Exception:
            return None
        return None

    @staticmethod
    def _cn_num(n: int) -> str:
        """1→一 … 12→十二（岛屿序号友好名）。"""
        cn = "一二三四五六七八九十"
        if n <= 10:
            return cn[n - 1] if 1 <= n <= 10 else str(n)
        if n < 20:
            return "十" + (cn[n - 11] if n > 10 else "")
        return str(n)

    def _talk_key(self) -> str:
        """闲聊话题池场景键。"""
        st = self._state
        if st.grilling:
            return "grill"
        if st.boss_active:
            return "boss"
        if st.phase == "waiting":
            return "waiting"
        if st.phase == "reeling":
            return "reeling"
        return "island"

    # ── mod 数据回调 ──

    def _on_game_start(self) -> None:
        """连接建立：会话计数重置 + （冷却内不重复）仪式播报。

        60s 冷却：短断线重连只恢复连接，不重复 game_start 播报/立挑战，
        避免宿主聊天记录刷屏（此前 5 次重连 = 5 次"又回岛了"）。
        """
        self._session = {"caught": 0, "new_journal": 0, "sold_total": 0,
                         "gamble_net": 0, "last_fish": ""}
        now = time.time()
        if now - getattr(self, "_last_game_start", 0) < 60:
            self.plugin.logger.info("game_start 冷却内跳过仪式（重连）")
            return
        self._last_game_start = now
        ev = FishEvent(name="game_start", ts=now, snapshot=self._state)
        self._dispatch(ev)
        # 每钓小挑战（低频立目标，走 respond 让宿主按人设现编）
        line = self.challenge.start()
        if line:
            self._spawn(self.push.push_fact(
                self.emotion.custom_fact(line, "chatter")))

    async def _say_line(self, event_name: str, **kw: Any) -> None:
        """盲出短句：LLM 动态生成优先（配了 LLM），失败/未配用模板兜底。

        照 pawpilot：避免硬编码话术复读，猫娘每次开口都有变化。
        """
        text = await self.emotion.short_line_llm(event_name, **kw)
        if not text:
            text = self.emotion.short_line(event_name, **kw)
        await self.push.push_direct(self.persona.polish(text))

    def _on_event(self, name: str, msg: Dict[str, Any]) -> None:
        """mod 事件 → 更新快照 → 记账/记忆 → 分层互动（照 pawpilot）。

        分层：特殊时刻单独设计（存在感话术/挑战/总结，打包成一段）；
        一般事件走 arbiter+渲染（高光才说）。猫娘是陪玩的，不是播报机器。
        """
        self._update_state_from_event(name, msg)
        self._track(name, msg)
        if name == "game_end":
            self._telemetry.connected = False  # mod 主动报退出
            self.arbiter.scenario.force_no_game()  # 游戏退出：状态机回 NO_GAME
            self._spawn(self.push.push_fact(
                self.emotion.fact_prompt("game_end")))
            self._spawn(self._finish_trip())
            return
        if name == "caught":
            # 上钩：存在感话术 + （新鱼/稀有时）挑战结算，打包一段
            self._handle_caught_interact(msg)
            return
        if name == "miss":
            lose_line = self.challenge.on_miss()
            if lose_line:
                self._spawn(self.push.push_fact(
                    self.emotion.custom_fact(lose_line, "miss")))
            return
        if name == "discovered":
            self._handle_discovered_interact(msg)
            return
        if name == "sold":
            return  # 卖鱼只记账，不打扰（大额由 _render 处理）
        if name == "boss_death":
            self._spawn(self.push.push_fact(
                self.emotion.fact_prompt("boss_death")))
            return
        if name == "player_death":
            # 大事件：存在感话术（安慰）+ 情绪同情，走 respond 宿主现编
            self.persona.feel("sympathy", 0.8)
            line = self.catgirl.existence_line("player_death")
            if line:
                self._spawn(self.push.push_fact(
                    self.emotion.custom_fact(line, "player_death")))
            return
        if name == "grill_done":
            self._spawn(self.push.push_fact(
                self.emotion.fact_prompt("grill_done")))
            return
        ev = FishEvent(name=name, ts=time.time(), data=dict(msg),
                       snapshot=self._state)
        self._dispatch(ev)

    def _handle_caught_interact(self, msg: Dict[str, Any]) -> None:
        """上钩互动：普通鱼安静（只记账）；新鱼/稀有/大货才说。

        走 respond（push_fact）：事实交给宿主 LLM 按人设动态演绎，
        不硬编码话术（避免复读）。LLM 未配置时由模板兜底。
        """
        is_highlight = bool(msg.get("new")) or bool(msg.get("shiny")) \
            or msg.get("rarity") in ("rare", "legendary") \
            or float(msg.get("weight", 0) or 0) >= 5.0
        if not is_highlight:
            return  # 普通鱼：只记账，猫娘安静陪你
        fish = str(msg.get("fish", ""))
        weight = float(msg.get("weight", 0) or 0)
        worth = int(msg.get("worth", 0) or 0)
        extra = "图鉴新鱼！" if msg.get("new") else (
            "彩虹皮肤！" if msg.get("shiny") else (
                f"{msg.get('rarity')}稀有！" if msg.get("rarity") in ("rare", "legendary")
                else "大货！"))
        # 挑战结算（若有）并入事实，让 LLM 一起演绎成一段自然的话
        win_line = self.challenge.on_caught(msg)
        a_line = self.achievement.on_caught(msg) if msg.get("new") else None
        fact = f"主人钓到一条{fish}（{extra}），重 {weight} kg，价值 {worth} 金币"
        if win_line:
            fact += f"。{win_line}"
        if a_line:
            fact += f"。{a_line}"
        self._spawn(self.push.push_fact(
            self.emotion.custom_fact(fact, "caught")))

    def _handle_discovered_interact(self, msg: Dict[str, Any]) -> None:
        """图鉴新鱼：事实交给宿主 LLM 演绎（进度 + 点评成一段）。"""
        fish = str(msg.get("fish", ""))
        count = int(msg.get("count", 0) or 0)
        total = int(msg.get("total", 0) or 0)
        self._session["new_journal"] += 1
        if fish:
            self.memory.remember("journal", fish, {"ts": time.time()})
        j_line = self.achievement.on_journal(count, total)
        r_line = self.recall.on_caught(msg, count)
        fact = f"主人解锁了图鉴新鱼：{fish}（{count}/{total}）"
        if j_line:
            fact += f"。{j_line}"
        if r_line:
            fact += f"。{r_line}"
        self._spawn(self.push.push_fact(
            self.emotion.custom_fact(fact, "discovered")))

    def _track(self, name: str, msg: Dict[str, Any]) -> None:
        """事件 → 账本/记忆/会话统计（纯记账，播报由 _on_event 分层处理）。"""
        if name == "caught":
            self._session["caught"] += 1
            if msg.get("fish"):
                self._session["last_fish"] = msg["fish"]
            if msg.get("new"):
                self._session["new_journal"] += 1
                self.memory.remember("journal", str(msg.get("fish", "")),
                                     {"ts": time.time()})
        elif name == "miss":
            self.achievement.on_miss()  # 挑战结算在 _on_event miss 分支
        elif name == "discovered" and msg.get("fish"):
            self._session["new_journal"] += 1
            self.memory.remember("journal", str(msg["fish"]),
                                 {"ts": time.time()})
        elif name == "sold":
            amt = int(msg.get("amount", 0) or 0)
            self._session["sold_total"] += amt
            self.ledger.record_sale(amt)
        elif name == "roulette_result":
            amt = int(msg.get("amount", 0) or 0)
            won = bool(msg.get("won", False))
            self.ledger.record_gamble(won, amt)
            self._session["gamble_net"] += amt if won else -amt
        elif name == "slot_result":
            amt = int(msg.get("amount", 0) or 0)
            won = bool(msg.get("won", False))
            self.ledger.record_gamble(won, amt)
            self._session["gamble_net"] += amt if won else -amt

    async def _finish_trip(self) -> None:
        """收竿总结 + 存档。"""
        line = self.trip_summary.build(self._session)
        self._spawn(self.push.push_fact(line))
        await self.memory.save()
        self._session = {"caught": 0, "new_journal": 0, "sold_total": 0,
                         "gamble_net": 0, "last_fish": ""}

    def _on_state(self, msg: Dict[str, Any]) -> None:
        """状态快照：更新 _state + 岛屿足迹。"""
        st = self._state
        raw_island = str(msg.get("island", ""))
        # mod 旧版本推的 Island.CurIsland.name 是类型名（IslandManager），过滤
        if raw_island in ("IslandManager", "Island"):
            raw_island = ""
        # IslandPositionN → 友好名（"一号岛"），进聊天/面板不再露内部名
        if raw_island:
            import re as _re
            m = _re.match(r"^IslandPosition(\d+)$", raw_island, _re.IGNORECASE)
            if m:
                raw_island = "一号岛" if m.group(1) == "1" else f"{self._cn_num(int(m.group(1)))}号岛"
        new_island = raw_island
        if new_island and new_island != self._last_island:
            self._last_island = new_island
            self.memory.bump("islands", new_island, "count")
            r_line = self.recall.on_island(new_island)
            if r_line:
                self._spawn(self.push.push_direct(self.persona.polish(r_line)))
        st.connected = True
        st.island = new_island
        st.island_index = int(msg.get("island_index", 0) or 0)
        st.money = int(msg.get("money", 0) or 0)
        st.bait = str(msg.get("bait", ""))
        st.owned_baits = list(msg.get("owned_baits", []) or [])
        st.held = str(msg.get("held", ""))
        st.betting = bool(msg.get("betting", False))
        st.bet_color = str(msg.get("bet_color", ""))
        st.boss_active = bool(msg.get("boss_active", False))
        st.boss_hp = int(msg.get("boss_hp", 0) or 0)
        st.boss_max_hp = int(msg.get("boss_max_hp", 0) or 0)
        st.grilling = bool(msg.get("grilling", False))
        st.on_boat = bool(msg.get("on_boat", False))
        st.journal_count = int(msg.get("journal_count", 0) or 0)
        st.journal_total = int(msg.get("journal_total", 0) or 0)
        st.phase = str(msg.get("phase", st.phase))
        st.raw = msg
        # 状态机：每个快照更新游戏状态（门控后续交互）
        self.arbiter.update_state(st)

    def _on_registry(self, msg: Dict[str, Any]) -> None:
        """图鉴注册表：写入 knowledge（mod 导出 → 知识库）。"""
        try:
            from pathlib import Path
            out = Path(__file__).resolve().parent.parent / "data" / "knowledge"
            out.mkdir(parents=True, exist_ok=True)
            (out / "creatures.json").write_text(
                json.dumps({"creatures": msg.get("creatures", []),
                            "baits": msg.get("baits", [])},
                           ensure_ascii=False, indent=2), encoding="utf-8")
            self.knowledge.load()
            self.plugin.logger.info(
                "图鉴导出: %d 物种 / %d 饵", len(msg.get("creatures", [])),
                len(msg.get("baits", [])))
        except OSError as exc:
            self.plugin.logger.warning("registry 落盘失败: %s", exc)

    def _update_state_from_event(self, name: str, msg: Dict[str, Any]) -> None:
        """事件里夹带的鱼/赌/Boss 数据同步进快照（caught 后 last_catch）。"""
        if name == "caught" and msg.get("fish"):
            self._state.last_catch = msg
        elif name == "sold" and msg.get("amount"):
            self._state.money = int(msg.get("money", self._state.money) or self._state.money)

    # ── 播报 ──

    def _dispatch(self, ev: FishEvent) -> None:
        """事件 → 仲裁 → 播报（LLM 优先 / 模板兜底）。"""
        self._record_activity(ev)
        allowed, _reason = self.arbiter.decide(ev.name)
        if not allowed:
            return
        prompt = self._render(ev)
        if prompt:
            self._spawn(self.push.push_fact(prompt))

    def _render(self, ev: FishEvent) -> Optional[str]:
        """事件 → 事实行（respond，宿主演绎）。

        只在高光时刻返回文本（猫娘不是播报机器）：
        普通抛竿/咬钩/跑鱼/卖鱼不主动说；稀有/新鱼/大货/Boss/赌局才说。
        """
        name = ev.name
        d = ev.data
        if name == "cast":
            return None  # 抛竿是常态，不喊
        if name == "bite":
            return None  # 咬钩是常态，不喊（challenge/场景由其它事件承载）
        if name == "caught":
            extra = ""
            if d.get("new"):
                extra += "（图鉴新鱼！）"
            if d.get("shiny"):
                extra += "（彩虹皮肤！）"
            elif d.get("rarity") in ("rare", "legendary"):
                extra += f"（{d.get('rarity')}稀有！）"
            if not extra:
                w = float(d.get("weight", 0) or 0)
                if w >= 5.0:
                    extra = "（大货！）"
            if not extra:
                return None  # 普通鱼：只记账，不打扰
            return self.emotion.fact_prompt(
                name, fish=d.get("fish", ""), weight=d.get("weight", 0),
                worth=d.get("worth", 0), extra=extra)
        if name == "miss":
            return None  # 跑鱼常态，不喊
        if name == "sold":
            amt = int(d.get("amount", 0) or 0)
            if amt >= 200:
                return self.emotion.fact_prompt(name, amount=amt,
                                                total=d.get("money", 0))
            return None  # 小额卖鱼常态，只记账
        if name == "bet":
            worth = int(d.get("worth", 0) or 0)
            if worth < 100:
                return None  # 小额下注常态，不打扰
            return self.emotion.fact_prompt(name, color=d.get("color", ""),
                                            worth=worth)
        if name == "roulette_result":
            amt = abs(int(d.get("amount", 0) or 0))
            won = bool(d.get("won", False))
            if amt < 150:
                return None  # 小输小赢常态，不打扰
            return self.emotion.fact_prompt(
                name, color=d.get("color", ""),
                won=won, amount=d.get("amount", 0),
                won_desc="赢" if won else "输")
        if name in ("slot_spin", "slot_result"):
            amt = abs(int(d.get("amount", 0) or 0))
            won = bool(d.get("won", False))
            if amt < 150:
                return None  # 小打小闹不报
            return self.emotion.fact_prompt(
                name, cost=d.get("cost", 0),
                won=won, amount=d.get("amount", 0),
                won_desc="赢" if won else "没中")
        if name in ("grill_start", "grill_done"):
            return self.emotion.fact_prompt(name)
        if name in ("boss_spawn", "boss_hp", "boss_death"):
            return self.emotion.fact_prompt(name, hp=d.get("hp", 0),
                                            max_hp=d.get("max_hp", 0))
        if name == "discovered":
            return self.emotion.fact_prompt(name, fish=d.get("fish", ""),
                                            count=d.get("count", 0),
                                            total=d.get("total", 0))
        if name == "kill":
            return self.emotion.fact_prompt(name, target=d.get("target", ""))
        if name == "seagull":
            return self.emotion.fact_prompt(name)
        if name == "achievement":
            key = str(d.get("key", ""))
            aname = ACHIEVEMENT_NAMES.get(key, key.lstrip("_"))
            self.memory.bump("achievement", key, "count")
            return self.emotion.fact_prompt(name, name=aname,
                                            detail="成就列表又添一笔喵~")
        if name == "game_start":
            return self.emotion.fact_prompt(name) + self._greeting()
        if name == "game_end":
            return self.emotion.fact_prompt(name) + self._farewell()
        return None

    def _greeting(self) -> str:
        import datetime
        hour = datetime.datetime.now().hour
        if 5 <= hour < 12:
            return " 早呀！今天想钓点什么？"
        if 12 <= hour < 18:
            return " 下午好喵！今天去哪片水域？"
        if 18 <= hour < 23:
            return " 晚上好喵！今晚夜钓还是赌场？"
        return " 深夜了喵… 我陪你钓夜鱼！"

    def _farewell(self) -> str:
        if self._state.last_catch:
            f = self._state.last_catch.get("fish", "")
            return f" 今天钓到过 {f} 喵，辛苦啦，收竿晚安~ 💤"
        return " 今天辛苦了喵，收竿晚安~ 💤"

    def _record_activity(self, ev: FishEvent) -> None:
        import datetime
        title = ACTIVITY_TITLES.get(ev.name, ev.name)
        detail = ""
        d = ev.data
        if ev.name == "caught" and d.get("fish"):
            detail = f"{d.get('fish')} {d.get('weight', 0)}kg"
        elif ev.name == "sold" and d.get("amount"):
            detail = f"+{d.get('amount')} 金币"
        elif ev.name == "roulette_result" and d.get("won"):
            detail = f"赢 {d.get('amount', 0)}"
        elif ev.name == "boss_death":
            detail = "击杀成功！"
        elif ev.name == "discovered" and d.get("fish"):
            detail = d.get("fish")
        self._activity.insert(0, {
            "title": title, "detail": detail,
            "time": datetime.datetime.now().strftime("%H:%M"),
        })
        self._activity = self._activity[:20]

    # ── 面板 / 问答 ──

    def _ensure_ocr(self) -> bool:
        """确保 OCR 已初始化（面板/场景话术共用），返回是否可用。"""
        if self.hud_ocr is not None:
            return self.hud_ocr.is_available()
        try:
            from ..adapters.hud_ocr import HudOcr
            self.hud_ocr = HudOcr(self.plugin)
            if not self.hud_ocr.is_available():
                self.plugin.logger.info("画面畅聊不可用（mss/rapidocr 缺失）")
                return False
            from .scene_chat import SceneChat
            self.scene_chat = SceneChat(self.knowledge)
            return True
        except Exception as exc:
            self.plugin.logger.warning("OCR 初始化失败: %s", exc)
            return False

    async def dashboard_state(self) -> Dict[str, Any]:
        persona = self.persona.snapshot()
        st = self._state
        return {
            "connected": st.connected,
            "dry_run": self.cfg.dry_run,
            "theme": getattr(self, "_theme", "dark"),
            "telemetry": self._telemetry_status(),
            "mood": persona.get("mood", ""),
            "catgirl": {
                "name": self.persona.name,
                "user_call": self.persona.user_call,
                "traits": self.persona.traits,
                "description": self.persona.description,
                "voice_styles": list(self.persona.voice_styles),
                "voice_labels": persona.get("voice_labels", []),
            },
            "state": {
                "phase": st.phase,
                "island": st.island,
                "money": st.money,
                "bait": st.bait,
                "held": st.held,
                "betting": st.betting,
                "bet_color": st.bet_color,
                "boss_active": st.boss_active,
                "boss_hp": st.boss_hp,
                "boss_max_hp": st.boss_max_hp,
                "grilling": st.grilling,
                "on_boat": st.on_boat,
                "journal_count": st.journal_count,
                "journal_total": st.journal_total,
            },
            "last_catch": st.last_catch,
            "activity": self._activity[:20],
            "broadcast_frequency": self.arbiter.broadcast_frequency,
            "broadcast_categories": dict(self.arbiter.broadcast_categories),
            "decision_log": self.arbiter.decision_snapshot(),
            "knowledge": self.knowledge.snapshot(),
            "ocr": self.hud_ocr.snapshot() if self._ensure_ocr() else {"available": False},
            "ledger": self.ledger.month_summary(),
            "memory": self.memory.snapshot(),
            "achievements": self._achievement_list(),
            "session": dict(self._session),
            "llm": self.llm.snapshot(),
        }

    def _achievement_list(self) -> list:
        """已解锁的游戏成就 + 插件里程碑（带中文名）。"""
        out = []
        for key in (self.memory._data.get("achievement", {}) or {}):
            out.append({"key": key,
                        "name": ACHIEVEMENT_NAMES.get(key, key.lstrip("_"))})
        out.sort(key=lambda a: a["name"])
        return out

    async def handle_status_query(self) -> Dict[str, Any]:
        st = self._state
        if not st.connected:
            return {"ok": False, "summary": "游戏没在运行喵，先打开渔力全开吧"}
        parts = [f"现在在「{st.island}」岛喵"]
        if st.phase == "waiting":
            parts.append("正在等鱼咬钩")
        elif st.phase == "reeling":
            parts.append("正在收线！")
        elif st.phase == "caught" and st.last_catch:
            parts.append(f"刚钓到 {st.last_catch.get('fish')}")
        parts.append(f"金币 {st.money}")
        if st.journal_total:
            parts.append(f"图鉴 {st.journal_count}/{st.journal_total}")
        return {"ok": True, "summary": "，".join(parts) + "~"}

    async def handle_player_talk(self, text: str) -> Dict[str, Any]:
        self.arbiter.on_player_speak()
        st = self._state
        if not st.connected:
            return {"ok": True, "summary": "游戏没在运行喵，先打开渔力全开再聊钓鱼吧"}
        # 账本查询
        if "账" in text or "赚" in text or "输了" in text or "花了" in text:
            return {"ok": True, "summary": self.ledger.render_summary()}
        # 知识库问答
        tip = self.knowledge.game_tip(text)
        if tip:
            return {"ok": True, "summary": tip}
        # 配置了自建 LLM → 深度回答
        if self.llm.configured:
            prompt = (f"你是{self.persona.name}（{self.persona.user_call}的钓鱼猫娘）。"
                      f"当前在「{st.island}」岛，金币 {st.money}，图鉴 {st.journal_count}/{st.journal_total}。"
                      f"玩家说：{text}。用一句话自然回应（25 字内，带猫娘语气）。")
            answer = await self.llm.call(prompt)
            if answer:
                return {"ok": True, "summary": answer}
        # 兜底：钓况 + 随机技巧
        return {"ok": True, "summary": (
            f"现在在「{st.island}」岛，金币 {st.money} 喵。"
            f"钓鱼有什么想问的都行~")}
