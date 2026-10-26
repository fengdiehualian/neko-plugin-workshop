"""交互引擎：猫娘陪伴感的核心。"""

import asyncio
import json
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from ..polish.attention import AttentionDrift
from ..polish.habits import PersonalHabits
from ..polish.human_timing import HumanTiming
from ..polish.imperfections import ImperfectionInjector

# 事件去重窗口（秒）
EVENT_DEDUP_WINDOW: float = 60.0
# v0.7 治理：事件最大存活时间（超过即丢弃——"小心！"晚播不如不播）
# #13: 场景 tick 间隔最大 15s（recovery）+ 人性化抖动，8s 会导致 idle/follow/travel
# 场景里注入的事件大部分过期丢弃。提到 30s，覆盖最慢场景仍有余量。
EVENT_MAX_AGE: float = 30.0
# v0.7 治理：主人刚说完话的静默窗（秒）——窗内不主动开口
OWNER_SPEECH_QUIET_WINDOW: float = 20.0

# 9 种场景 → (阈值, 基础增长率, 基础间隔秒, 描述)
SCENE_CONFIG: Dict[str, Tuple[float, float, float, str]] = {
    "combat":  (0.25, 0.15, 3.0,   "战斗"),       # 低阈值快节奏：战斗中猫娘频繁喊话
    "boss":    (0.15, 0.25, 2.0,   "BOSS战"),     # 更低阈值：BOSS 战疯狂碎碎念
    "explore": (0.50, 0.04, 8.0,   "探索"),       # 时不时好奇一句
    "follow":  (0.60, 0.03, 12.0,  "跟随主人"),   # 安静跟随，偶尔说话
    "idle":    (0.65, 0.06, 12.0,  "空闲"),       # 陪伴：憋太久会主动找话
    "mining":  (0.35, 0.10, 4.0,   "挖矿"),       # 挖矿时活跃念叨：边挖边吐槽/汇报
    "build":   (0.55, 0.05, 10.0,  "建造"),
    "recovery":(0.65, 0.03, 15.0,  "恢复"),       # 恢复期安静
    "travel":  (0.60, 0.04, 12.0,  "赶路"),
}


@dataclass
class SceneState:
    name: str = "idle"
    threshold: float = 0.70
    charge_rate: float = 0.06
    interval: float = 20.0
    switched_at: float = 0.0
    prev: str = ""


class SceneClassifier:
    """场景分类器：根据游戏快照判定猫娘当前处于什么场景。"""

    _boss_keywords = [
        "king slime", "eye of cthulhu", "eater of worlds",
        "brain of cthulhu", "queen bee", "skeletron",
        "wall of flesh", "destroyer", "twins", "skeletron prime",
        "plantera", "golem", "duke fishron", "lunatic cultist",
        "moon lord", "empress of light", "deerclops",
    ]

    @staticmethod
    def classify(state: Dict[str, Any]) -> str:
        """根据游戏状态快照判定场景类型。

        敌人数据源是 mod 返回的 nearby_npcs（mod_link.get_state），
        不要读 nearby_enemies（mod 不返回该键，会导致战斗场景永不触发）。
        """
        nearby = state.get("nearby_npcs", []) or state.get("nearby_enemies", []) or []
        # 只把"有伤害且活着"的 NPC 当敌人（曾把向导/商人等友善 NPC 也判
        # 成 combat → 猫娘站在 NPC 旁永远战斗场景高频碎碎念战斗话题。
        # 口径对齐 combat.py / brain._guard_check 的 damage>0 and life>0）
        enemies = [e for e in nearby
                   if not e.get("friendly", False)
                   and not e.get("townNPC", e.get("town_npc", False))
                   and int(e.get("damage", 0) or 0) > 0
                   and int(e.get("life", 0) or 0) > 0]
        hp = state.get("hp", 100)
        max_hp = state.get("max_life", state.get("max_hp", 100))
        hp_ratio = hp / max(max_hp, 1)

        # BOSS 战：附近有 boss 级怪物
        if enemies:
            for e in enemies:
                name = str(e.get("name", "")).lower()
                for kw in SceneClassifier._boss_keywords:
                    if kw in name:
                        return "boss"

        # 战斗：附近有敌怪
        if enemies:
            return "combat"

        # 恢复：血量低且附近没敌怪
        if hp_ratio < 0.5 and not enemies:
            return "recovery"

        # 通过 agent 的长期任务判断其他场景
        # 这里由调用方传入额外 Hint（executor/ longterm 状态）
        return "idle"

    @staticmethod
    def classify_with_hints(state: Dict[str, Any],
                            current_task: Optional[str] = None,
                            longterm_kinds: Optional[List[str]] = None) -> str:
        """带 Hint 的分类（更准确）。"""
        base = SceneClassifier.classify(state)
        if base != "idle":
            return base

        if longterm_kinds:
            kinds = set(longterm_kinds)
            if "mine" in kinds or "chop" in kinds:
                return "mining"
            if "follow" in kinds:
                return "follow"
            if "guard" in kinds:
                return "combat"

        # 从速度判断赶路（#93: mod 状态键是 velocity_x/velocity_y，velocity 不存在）
        vx = state.get("velocity_x", 0) or 0
        vy = state.get("velocity_y", 0) or 0
        if abs(vx) > 3 or abs(vy) > 3:
            return "travel"

        if state.get("is_building", False):
            return "build"

        return "idle"


# ══════════════════════════════════════════════════════════
# 情绪管理
# ══════════════════════════════════════════════════════════

@dataclass
class MoodArc:
    """情绪弧线：事件 → 峰值 → 衰减 → 残留痕迹。"""

    name: str                           # 情绪标签
    value: float = 0.0                  # 当前值
    peak: float = 0.0                   # 峰值
    decay_rate: float = 0.05            # 衰减速率（每 tick）
    residual: float = 0.0               # 残留（不再衰减的底）
    duration: int = 0                   # 发生后经过的 tick 数

    def trigger(self, intensity: float) -> None:
        """触发情绪峰值。"""
        self.value = max(self.value, intensity)
        self.peak = self.value
        self.residual = max(self.residual, intensity * 0.1)
        self.duration = 0

    def decay(self) -> None:
        """衰减一步。"""
        self.duration += 1
        if self.value > self.residual:
            self.value -= self.decay_rate * (self.value - self.residual)
            self.value = max(self.residual, self.value)

    def style_modifier(self) -> dict:
        """返回对说话风格的影响。"""
        if self.value > 0.8:
            return {"energy": "high", "verbosity": "多话", "emoji": 3}
        if self.value > 0.5:
            return {"energy": "medium", "verbosity": "正常", "emoji": 2}
        if self.value > 0.2:
            return {"energy": "low", "verbosity": "简洁", "emoji": 1}
        return {"energy": "calm", "verbosity": "极简", "emoji": 0}


class MoodManager:
    """管理多根情绪弧线。"""

    def __init__(self) -> None:
        self.arcs: Dict[str, MoodArc] = {
            "excitement": MoodArc("excitement", decay_rate=0.06),
            "fear":       MoodArc("fear", decay_rate=0.04),
            "curiosity":  MoodArc("curiosity", decay_rate=0.05),
            "tired":      MoodArc("tired", decay_rate=0.02),
            "proud":      MoodArc("proud", decay_rate=0.07),
        }

    def trigger(self, emotion: str, intensity: float = 0.5) -> None:
        """触发某个情绪。"""
        if emotion in self.arcs:
            self.arcs[emotion].trigger(min(intensity, 1.0))

    def decay_all(self) -> None:
        for arc in self.arcs.values():
            arc.decay()

    def primary_style(self) -> dict:
        """取最强情绪的说话风格。"""
        best = max(self.arcs.values(), key=lambda a: a.value)
        return best.style_modifier()

    def combined_modifier(self) -> float:
        """情绪对 speech_urge 的倍率加成。excitement/curiosity 加速，tired/fear 减速。"""
        bonus = 1.0
        bonus += self.arcs["excitement"].value * 0.3
        bonus += self.arcs["curiosity"].value * 0.2
        bonus -= self.arcs["tired"].value * 0.2
        bonus -= self.arcs["fear"].value * 0.15
        return max(0.5, bonus)


# ══════════════════════════════════════════════════════════
# 主人行为追踪
# ══════════════════════════════════════════════════════════

@dataclass
class PositionRecord:
    tile_x: int
    tile_y: int
    ts: float

class OwnerTracker:
    """追踪主人位置历史、行为序列，生成好奇心问题。"""

    def __init__(self, max_history: int = 60) -> None:
        self.max_history = max_history
        self.positions: List[PositionRecord] = []
        self.last_action: str = ""
        self.action_sequence: List[str] = []
        self.inventory_snapshot: set = set()
        self._recent_new_items: List[str] = []
        self._new_items_ts: float = 0.0

    def update(self, state: Dict[str, Any]) -> None:
        """喂入一帧游戏状态。"""
        now = time.time()
        if "tile_x" in state and "tile_y" in state:
            self.positions.append(PositionRecord(
                tile_x=int(state["tile_x"]), tile_y=int(state["tile_y"]),
                ts=now))
            if len(self.positions) > self.max_history:
                self.positions = self.positions[-self.max_history:]
        elif not state:
            self.positions.clear()

    def update_inventory(self, inv: Dict[str, Any]) -> None:
        """对比背包快照，记录主人新获得的物品（好奇素材）。

        inv 结构：{"hotbar": [...], "equipped": [...], "inventory": [...]}
        """
        names: set = set()
        for slot in ("hotbar", "equipped", "inventory"):
            for it in (inv or {}).get(slot, []) or []:
                nm = ""
                if isinstance(it, dict):
                    nm = it.get("name", "")
                elif isinstance(it, str):
                    nm = it
                if nm:
                    names.add(str(nm))
        if self.inventory_snapshot:
            new_items = names - self.inventory_snapshot
            if new_items:
                self._recent_new_items = sorted(new_items)[:3]
                self._new_items_ts = time.time()
        self.inventory_snapshot = names

    def idle_duration(self) -> float:
        """主人在同一位置停留了多久（秒）"""
        if len(self.positions) < 2:
            return 0
        latest = self.positions[-1]
        # 找到最后一次位置变化的时间
        for p in reversed(self.positions):
            if p.tile_x != latest.tile_x or p.tile_y != latest.tile_y:
                return latest.ts - p.ts
        return latest.ts - self.positions[0].ts

    def movement_type(self) -> str:
        """判断主人移动类型：static / wander / sprint / fall"""
        if len(self.positions) < 5:
            return "unknown"
        recent = self.positions[-5:]
        xs = [p.tile_x for p in recent]
        ys = [p.tile_y for p in recent]
        dx = max(xs) - min(xs)
        dy = max(ys) - min(ys)
        if dx <= 1 and dy <= 1:
            return "static"
        if dx >= 20:
            return "sprint"
        if dy > 5 and dx < 5:
            return "fall"
        return "wander"

    def curiosity_question(self) -> str:
        """生成好奇心问题：主人在做什么？"""
        # 背包刚多了东西 → 好奇主人捡到什么（60s 内有效）
        if self._recent_new_items and time.time() - self._new_items_ts < 60:
            items = "、".join(self._recent_new_items)
            return f"诶？主人背包里多了{items}，捡到什么好东西了喵？"
        idle_sec = self.idle_duration()
        mv = self.movement_type()
        if idle_sec > 30:
            return "主人怎么不动了？在看地图吗？还是想事情呢喵~"
        if mv == "static":
            return "主人站着发呆吗？需要帮忙吗？"
        if mv == "sprint":
            return "主人跑这么快要去哪！等等我呀~"
        if mv == "fall":
            return "主人！你在往下掉吗？没事吧！"
        return "主人在想什么呢~"


# ══════════════════════════════════════════════════════════
# 交互引擎主体
# ══════════════════════════════════════════════════════════

class InteractionEngine:
    """交互引擎：猫娘的语言交互核心。 """

    def __init__(self, agent, plugin, cfg: Optional[Dict] = None) -> None:
        self.agent = agent
        self.plugin = plugin
        self.cfg = cfg or {}

        # 场景与情绪
        self.scene = SceneState()
        self.mood = MoodManager()
        self.owner = OwnerTracker()

        # 说话冲动
        self._urge: float = 0.0
        self._last_speech_ts: float = 0.0

        # 事件队列（executor/ service/ vision 注入）
        self._event_queue: asyncio.Queue = asyncio.Queue()
        # 事件去重：同源同描述在窗口内不重复注入（防机械复读）
        self._recent_events: Dict[str, float] = {}

        # 被打断的任务栈 [(task_name, interrupted_at, task_snapshot), ...]
        self._interrupted_stack: List[dict] = []

        # 运行状态
        self._running: bool = False
        self._tick_task: Optional[asyncio.Task] = None
        self._event_bus = None

        # 冷却（防重叠）
        self._speech_cooldown_until: float = 0.0

        # 陪伴：主人最后发言时间（静默太久会主动找话/关心）
        self._last_owner_speech_ts: float = time.time()
        try:
            from .event_bus import get_event_bus
            self._event_bus = get_event_bus()
            self._event_bus.subscribe("owner_spoke", self._on_owner_spoke)
        except Exception:
            pass

        # 陪伴：危险解除后的自然衔接（"可恶的小白终于甩掉他了，继续挖矿~"）
        self._danger_desc: str = ""
        self._danger_ts: float = 0.0
        self._danger_task: str = ""

        # 陪伴：最近一步任务事实（让主动说话有具体内容，防幻觉）
        self._recent_step_desc: str = ""
        self._recent_step_ts: float = 0.0

        # 人性化模块 — v2.1: 接受主项目人设
        host_persona: Optional[dict] = self.cfg.get("_host_persona", None)
        self.habits = PersonalHabits.from_persona(host_persona)
        self.timing = HumanTiming()
        imperfection_intensity: float = (
            float(host_persona.get("habits", {}).get("imperfection", 0.5))
            if host_persona else 0.5
        )
        self.imperfections = ImperfectionInjector(intensity=imperfection_intensity)
        self.attention = AttentionDrift()

        # 参数来源 config / polish
        self._base_tick_interval: float = self.cfg.get("interaction_tick", 1.0)

    # ── 生命周期 ──────────────────────────────────────

    async def start(self) -> None:
        """启动交互引擎主循环。"""
        if self._running:
            return
        if self._event_bus is None:
            try:
                from .event_bus import get_event_bus
                self._event_bus = get_event_bus()
                self._event_bus.subscribe("owner_spoke", self._on_owner_spoke)
            except Exception:
                self._event_bus = None
        self._running = True
        self._tick_task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        """停止交互引擎。"""
        self._running = False
        task = self._tick_task
        self._tick_task = None
        if task and task is not asyncio.current_task() and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        if self._event_bus is not None:
            self._event_bus.unsubscribe("owner_spoke", self._on_owner_spoke)
            self._event_bus = None

    async def _loop(self) -> None:
        """主循环：动态间隔 tick。"""
        while self._running:
            try:
                await self._tick()
            except asyncio.CancelledError:
                return
            except Exception:
                pass  # tick 中任何异常都不应该杀死循环

            # 动态间隔
            tick_interval = self._base_tick_interval
            scene_conf = SCENE_CONFIG.get(self.scene.name)
            if scene_conf:
                tick_interval = scene_conf[2]
            # 场景间隔 × 人性化缩放
            interval = self.timing.action_duration(tick_interval)
            await asyncio.sleep(interval)

    # ── 核心 tick ────────────────────────────────────

    async def _tick(self) -> None:
        """单次 tick：更新状态 → 场景分类 → 情绪衰减 → 主人追踪 → 处理事件 → urge → 判定 → 恢复。"""
        # v2.2: AI 客户端未连接（等面板「连接游戏」）→ 空转，不主动开口。
        # 此前 bug：游戏没开时 state 空 → 场景 idle → urge 慢慢涨到阈值 →
        # 猫娘对着没开的游戏主动搭话（"主人，在忙什么呀？"）。
        # 主人说话由 coordinator 直推 push_speech，不走 tick——不受此门影响。
        if not getattr(self.agent, "running", False):
            return
        state = self._get_state()
        if not state or state.get("alive") is False or state.get("hp", 1) <= 0:
            self.owner.update({})
            return
        longterm_kinds = self._get_longterm_kinds()
        current_name = self._get_current_task_name()

        # 1. 场景分类
        scene_name = SceneClassifier.classify_with_hints(
            state, current_name, longterm_kinds)
        self._maybe_switch_scene(scene_name)

        # 2. 情绪衰减
        self.mood.decay_all()

        # 3. 主人追踪（#12: 主人 = nearby_players 里最近玩家，不是猫娘自己。
        #    主人背包 mod 不返回，不再喂猫娘背包，避免"主人背包多了矿"的假话。）
        self.owner.update(self._extract_owner_state(state))

        # 4. 处理注入事件（先于 urge 计算，紧急事件走 immediate_respond 直推）
        await self._process_events()

        # 5. 冲动值计算（仅处理场景 + 情绪自然增长，事件 boost 由 _handle_event 完成）
        urge_delta = self._calc_urge_delta()
        self._urge = min(1.0, self._urge + urge_delta)

        # 6. 判定说话
        if self._should_speak():
            await self._trigger_speech()

        # 6.5 危险解除衔接（场景从战斗切回安全 → 自然吐槽一句）
        await self._check_danger_recovery()

        # 7. 恢复询问
        await self._check_recovery()

    # ── 事件注入（外部接口）──────────────────────────

    async def inject_event(self, event_type: str,
                            intensity: float = 0.5,
                            description: str = "",
                            data: Optional[Dict] = None) -> None:
        """统一的事件注入入口。外部（executor/service/vision）通过此方法推送事件。

        同源同描述 60s 内不重复注入（防机械复读）。
        """
        key = f"{event_type}:{description}"
        now = time.time()
        if self._recent_events.get(key, 0.0) > now - EVENT_DEDUP_WINDOW:
            return
        self._recent_events[key] = now
        if len(self._recent_events) > 128:
            cutoff = now - EVENT_DEDUP_WINDOW
            self._recent_events = {k: v for k, v in self._recent_events.items()
                                   if v > cutoff}
        await self._event_queue.put({
            "type": event_type,
            "intensity": intensity,
            "description": description,
            "data": data or {},
            "ts": now,
        })

    # ── 消息推送 ──────────────────────────────────────

    async def push_speech(self, text: str, behavior: str = "respond",
                          urgent: bool = False) -> None:
        """推送一条猫娘话语给 LLM 管道。

        v0.7 治理：
        - blind：插件直出短句（危险场景），emergency 优先级，低延迟
        - 静默窗：主人刚说完话（20s 内）非紧急主动开口降级为 read 上下文
        """
        if not text:
            return
        # #95: read 是"上下文数据"（任务进度/画面感知/目标状态），不应被说话冷却拦截——
        # 否则猫娘正在干活时所有 read 感知全被吞，宿主 LLM 看不到进行中的事。
        # blind 是紧急短句（低延迟）也不受冷却限制。只有 respond（主动开口）受冷却约束。
        if not urgent and behavior == "respond" and time.time() < self._speech_cooldown_until:
            return

        # v0.7 静默窗：主人刚说话，非紧急不打扰（blind 危险短句例外）
        if not urgent and behavior != "blind" and behavior != "read":
            if time.time() - self._last_owner_speech_ts < OWNER_SPEECH_QUIET_WINDOW:
                behavior = "read"

        # 全局限流检查（respond 用 normal，blind 用 emergency）
        if behavior in ("respond", "blind"):
            from ..llm.throttle import get_throttle
            throttle = get_throttle()
            prio = "emergency" if urgent or behavior == "blind" else "normal"
            if not throttle.acquire(source="interaction_speech", priority=prio):
                # 被限流 → 转为 read 模式（不强制 LLM 响应）；blind 限流直接放弃
                if behavior == "blind":
                    return
                behavior = "read"

        # v2.1: respond 话语做"不完美"后处理（手滑/结巴/忘词/语气词），
        #       read 是上下文数据不加工
        # 此处传递的是事实和生成要求，不是 LLM 的最终台词。
        # 随机插字会破坏物品名、数量和指令，语气交给宿主人设生成。
        # 走 agent.speak：优先宿主 push_message（LLM 人设润色），
        # 宿主拒收时记录失败；只有面向玩家的 blind 短句可直接游戏聊天兜底。
        try:
            delivered = await self.agent.speak(text, ai_behavior=behavior)
            # #95 治理：只有真正开口（respond）才清零冲动 + 进入说话冷却。
            # read 是上下文数据（step_done/画面感知），blind 是紧急短句——
            # 二者若也刷新冷却/清零 urge，挖矿时每步 read 会持续压制主动说话，
            # 猫娘永远不开口（"闷头干活"假象）。blind 紧急短句也不设冷却，
            # 允许连续危险连续喊。
            if behavior == "respond" and delivered:
                self._urge = 0.0  # 说过话了，冲动值清零
                self._speech_cooldown_until = time.time() + self.timing.reaction_delay()
            if delivered and behavior in ("respond", "blind"):
                self._last_speech_ts = time.time()
            # respond/read 内容是给宿主 LLM 的事实和提示词；宿主拒收时
            # 不能把内部提示词原样发到游戏聊天。危险短句走 blind 分支，
            # 由 agent.speak 负责安全的游戏内兜底。
        except Exception:
            pass  # 推送失败不崩溃

    # ── 内部方法 ──────────────────────────────────────

    def _get_state(self) -> Dict[str, Any]:
        try:
            return self.agent.get_state()
        except Exception:
            return {}

    def _extract_owner_state(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """#12: 从游戏状态里提取"主人"（最近的非自身玩家）的状态。

        OwnerTracker 应追踪主人的位置/行为，而不是猫娘自己的（agent.get_state
        返回的是猫娘；主人数据在 state['nearby_players']）。附近无玩家返回空。
        """
        if not state:
            return {}
        me_x = int(state.get("tile_x", 0) or 0)
        me_y = int(state.get("tile_y", 0) or 0)
        my_name = ""
        try:
            my_name = self.agent._character_name()
        except Exception:
            pass
        best: Optional[Dict[str, Any]] = None
        best_d = float("inf")
        for p in (state.get("nearby_players", []) or []):
            if not isinstance(p, dict):
                continue
            name = p.get("name", "")
            if name and my_name and name == my_name:
                continue  # 过滤猫娘自己
            x = int(p.get("tile_x", 0) or 0)
            y = int(p.get("tile_y", 0) or 0)
            if x == 0 and y == 0:
                continue  # 残留槽位
            d = (x - me_x) ** 2 + (y - me_y) ** 2
            if d < best_d:
                best_d = d
                best = {"tile_x": x, "tile_y": y}
        return best or {}

    def _get_longterm_kinds(self) -> List[str]:
        try:
            lt = getattr(self.agent, "longterm", None)
            if lt:
                return [a.get("kind", "") for a in (lt.active() or [])]
        except Exception:
            pass
        return []

    def _get_current_task_name(self) -> Optional[str]:
        try:
            exe = getattr(self.agent, "executor", None)
            if exe:
                cur = exe.current()
                if cur:
                    return cur.get("name", "")
        except Exception:
            pass
        return None

    def _maybe_switch_scene(self, new_name: str) -> None:
        if new_name != self.scene.name:
            self.scene.prev = self.scene.name
            self.scene.name = new_name
            self.scene.switched_at = time.time()

    def _calc_urge_delta(self) -> float:
        """计算本次 tick 的 urge 增量。"""
        conf = SCENE_CONFIG.get(self.scene.name, (0.70, 0.06, 20.0, "未知"))
        base_rate = conf[1]

        # 情绪倍率
        modifier = self.mood.combined_modifier()

        # 注意力漂移：发呆时不计分（模仿走神）
        if self.attention.should_drift():
            modifier *= 0.5

        # 陪伴：主人静默太久 → 猫娘心里犯嘀咕，说话欲加速（假定人就在身边）
        if self.scene.name == "idle":
            silent = time.time() - self._last_owner_speech_ts
            if silent > 90:
                modifier *= 1.5
            if silent > 240:
                modifier *= 1.6

        # 习惯加成：健谈猫 urge 涨更快
        modifier *= self.habits.trait("talkative")

        delta = base_rate * modifier

        # 不完美停顿
        if self.imperfections.maybe_pause():
            delta *= 0.3

        return delta

    def _event_urge_boost(self, evt: Dict) -> float:
        """事件对 urge 的加成。按生存循环惯例 补全日常事件 boost。"""
        etype = evt.get("type", "")
        intensity = evt.get("intensity", 0.5)
        boosts = {
            "step_done":        0.15,   # 陪伴：每步完成都值得念叨一句，不闷头干活
            "task_done":        0.45,
            "task_interrupted": 0.50,
            "goal_set":         0.15,
            "goal_completed":   0.45,
            "goal_failed":      0.25,
            "danger_found":     0.80,
            "hp_low":           0.70,   # 兼容遗留的 "hp_low"，实际使用 low_hp
            "low_hp":           0.70,
            "hp_crash":         0.85,
            "boss_nearby":      0.90,
            "drowning":         0.75,
            "in_lava":          0.80,
            "falling":          0.60,
            "boss_killed":      0.85,
            "enemy_killed":     0.25,
            "combat_summary":   0.35,
            "enemy_spotted":    0.25,
            "found_chest":      0.35,
            "chest_found":      0.35,
            "found_rare":       0.45,
            "ore_found":        0.15,
            "biome_changed":    0.10,
            "terrain_changed":  0.10,
            "equipment_upgraded":  0.20,
            "inventory_full":      0.20,
            "player_nearby":   0.10,
            "time_changed":    0.05,
            "weather_changed": 0.03,
        }
        return boosts.get(etype, 0.10) * intensity

    def _should_speak(self) -> bool:
        """判定是否应该说话。"""
        # 冷却未过
        if time.time() < self._speech_cooldown_until:
            return False
        # 场景阈值
        conf = SCENE_CONFIG.get(self.scene.name, (0.70, 0.06, 20.0, "未知"))
        threshold = conf[0]
        # 情绪影响阈值（激动/恐惧时更容易说话）
        threshold *= (2.0 - self.mood.combined_modifier())
        threshold = max(0.10, min(threshold, 0.90))
        return self._urge >= threshold

    async def _trigger_speech(self) -> None:
        """触发主动说话。"""
        conf = SCENE_CONFIG.get(self.scene.name, ("idle", 0.06, 20.0, "未知"))
        scene_desc = conf[3]

        # 生成场景化话题提示
        topic = self._pick_topic()
        style = self.mood.primary_style()

        # 最近一步的具体事实（如"挖到铁矿×2，共5个"）→ 说话有依据，防幻觉
        fact = ""
        if self._recent_step_desc and time.time() - self._recent_step_ts < 60:
            fact = f"\n你刚才做过的事：{self._recent_step_desc}"

        # 视觉事实：最近看到的画面（LLM Vision 感知报告，2 分钟内新鲜）
        vision_fact = self._recent_vision_fact()

        prompt = f"""[猫娘主动说话 - {scene_desc}]
说话风格：{json.dumps(style, ensure_ascii=False)}
话题提示：{topic}
{fact}{vision_fact}

请用猫娘语气说1-2句话（不要超过30字），基于当前场景自然地说。"""

        await self.push_speech(prompt, behavior="respond")

    def _pick_topic(self) -> str:
        """根据场景和主人状态选择话题。"""
        scene_topic_map = {
            "combat":   "战斗实况吐槽（怪好多/打死它/主人小心左边）",
            "boss":     "BOSS战紧张喊话（危险/快躲/我去吸引火力）",
            "explore":  "探索发现（这里好美/前面有矿/好像来过）",
            "follow":   "跟随闲聊（主人要去哪里/好无聊说点什么/要不要换个方向）",
            "idle":     "空闲陪伴（主人是不是在挂机/今天收获怎么样/要不要一起去探险）",
            "mining":   "挖矿话题（这块矿石好漂亮/挖了多少了/主人需要我做点什么吗）",
            "build":    "建造话题（好棒！再加点什么/这个角度好好看）",
            "recovery": "恢复期（头有点晕/刚才好险/还好没事）",
            "travel":   "赶路话题（好远呀/飞过去快一点/世界好大）",
        }
        # 陪伴：主人静默很久 → 直接关心（不是自说自话，是找主人说话）
        silent = time.time() - self._last_owner_speech_ts
        if silent > 180:
            return "主人好久没说话啦，关心一句（在忙什么？要不要陪你说说话？）"
        # 如果主人站了很久，触发好奇心
        if self.owner.idle_duration() > 20:
            return self.owner.curiosity_question()
        return scene_topic_map.get(self.scene.name, "自由发言")

    async def _process_events(self) -> None:
        """处理积压的事件（场景内独立处理，不进入 urge 计算流）。

        v0.7 治理：超过 EVENT_MAX_AGE 的事件直接丢弃（"小心！"晚播不如不播）。
        #9：先冲刷超时的紧急缓冲（否则连续受击后一直安全，缓冲里的描述永远不播）。
        #13：EVENT_MAX_AGE 已放大到覆盖最大场景 tick 间隔，避免事件在 idle/follow 等慢场景里全部过期。
        """
        try:
            await self._flush_emergency_buffer()
            for _ in range(min(10, self._event_queue.qsize())):
                evt = self._event_queue.get_nowait()
                if time.time() - evt.get("ts", 0.0) > EVENT_MAX_AGE:
                    continue
                await self._handle_event(evt)
        except asyncio.QueueEmpty:
            pass

    async def _flush_emergency_buffer(self) -> None:
        """#9: 紧急事件缓冲超时冲刷——连续危险后不再有新事件时，把攒下的描述播出去。"""
        if not hasattr(self, "_emergency_buffer") or not self._emergency_buffer:
            return
        now = time.time()
        if now - self._last_emergency < 10:
            return
        merged = "、".join(self._emergency_buffer)
        self._emergency_buffer = []
        self.mood.trigger("fear", 0.5)
        self._danger_desc = merged
        self._danger_ts = time.time()
        self._danger_task = self._get_current_task_name() or ""
        await self.push_speech(f"唔…{merged}，好疼", behavior="blind")

    async def _handle_event(self, evt: Dict) -> None:
        """事件分类处理：紧急强制回应，日常只 boost urge。"""
        etype = evt.get("type", "")
        desc = evt.get("description", "")
        intensity = evt.get("intensity", 0.5)

        immediate_respond = {
            "danger_found", "hp_low", "low_hp", "hp_crash", "boss_nearby",
            "drowning", "in_lava", "falling", "combat_hit",
        }
        # v3.0: task_done/task_interrupted 不再强制说话——fire-and-forget 的
        # 完成 cue 已由 brain._on_executor_task_done 统一推给宿主 LLM，避免双响
        task_respond = {"goal_completed", "goal_failed", "boss_killed"}
        task_read = {"task_done", "task_interrupted"}
        goal_read = {"goal_set", "equipment_upgraded", "inventory_full"}
        explore_read = {
            "step_done", "found_chest", "chest_found", "found_rare",
            "ore_found", "biome_changed", "terrain_changed",
        }
        combat_read = {"combat_summary", "enemy_killed", "enemy_spotted"}
        # v0.7: 画面理解事件——主人动作感知（read，猫娘注意到了）+ 画面第一反应（respond，说出口）
        owner_read = {
            "owner_fighting", "owner_mining", "owner_building",
            "owner_exploring", "owner_idle",
        }
        scene_say_respond = {"scene_say"}
        light = {"player_nearby", "time_changed", "weather_changed"}

        if etype in immediate_respond:
            # 紧急事件合并：10秒内多个紧急事件合并成一条
            now = time.time()
            if not hasattr(self, "_emergency_buffer"):
                self._emergency_buffer = []
                self._last_emergency = 0

            if now - self._last_emergency < 10:
                # 缓存事件
                self._emergency_buffer.append(desc)
                return

            # 合并描述
            if self._emergency_buffer:
                merged = "、".join(self._emergency_buffer + [desc])
                self._emergency_buffer = []
            else:
                merged = desc

            self._last_emergency = now

            self.mood.trigger("fear", intensity)
            # 记录危险上下文 → 危险解除后自然衔接（"甩掉它啦，继续挖矿~"）
            self._danger_desc = merged
            self._danger_ts = time.time()
            self._danger_task = self._get_current_task_name() or ""

            # v0.7: danger 类用 blind 短句直出（低延迟，不打扰 LLM 人设流）。
            # 台词归属原则：危险场景固定短句速度优先（同战雷插件"拉起来！"），
            # 日常情感仍走角色 LLM。
            danger_blind = {
                "danger_found": "危险！快躲开！",
                "hp_crash": "救命！我好疼！",
                "drowning": "喘不过气！",
                "in_lava": "好烫！烫烫烫！",
                "boss_nearby": "BOSS来了！主人小心！",
                "falling": "啊——！",
            }
            if etype in danger_blind:
                await self.push_speech(danger_blind[etype], behavior="blind")
                return

            await self.push_speech(
                f"[紧急事件] {merged}\n立刻用猫娘语气紧急警告（1句话，10字以内）",
                behavior="respond", urgent=True)

        elif etype in task_respond:
            mood_type = "proud" if etype in ("goal_completed", "boss_killed") else "tired"
            self.mood.trigger(mood_type, intensity)

            await self.push_speech(
                f"[任务事件] {desc}\n用猫娘语气汇报/吐槽（1句话，20字以内）",
                behavior="respond")

        elif etype in task_read:
            # 完成 cue 由 brain 统一推送，这里只进上下文 + 推高说话欲（不强制说话）
            mood_type = "proud" if "done" in etype else "tired"
            self.mood.trigger(mood_type, intensity)
            self._urge = min(1.0, self._urge + self._event_urge_boost(evt))
            await self.push_speech(
                f"[任务事件] {desc}（猫娘注意到了，结果由主人那边汇报）",
                behavior="read")

        elif etype in goal_read:
            self._urge = min(1.0, self._urge + self._event_urge_boost(evt))
            await self.push_speech(
                f"[目标/状态] {desc}（猫娘注意到了，需要时可以提一下）",
                behavior="read")

        elif etype in explore_read:
            self._urge = min(1.0, self._urge + self._event_urge_boost(evt))
            if etype == "step_done" and desc:
                # 记住最新一步的具体事实（挖到铁矿×2等）→ 说话时用得上
                self._recent_step_desc = desc
                self._recent_step_ts = time.time()
            await self.push_speech(
                f"[感知] {desc}（猫娘注意到了，但不一定要说话）",
                behavior="read")

        elif etype in combat_read:
            self._urge = min(1.0, self._urge + self._event_urge_boost(evt))
            await self.push_speech(
                f"[战斗] {desc}（猫娘注意到了，可以说一句战斗吐槽）",
                behavior="read")

        elif etype in owner_read:
            # 主人动作感知：注入情绪 + 提升说话欲（不强制开口，避免吵）
            self._urge = min(1.0, self._urge + self._event_urge_boost(evt))
            await self.push_speech(
                f"[主人动态] {desc}（猫娘通过画面注意到了主人的动态，自然关心一下）",
                behavior="read")

        elif etype in scene_say_respond:
            # 画面触发的第一反应：直接说出口（respond 走主 LLM 人设润色）
            await self.push_speech(
                f"[画面心情] {desc}\n用猫娘语气说出这句话（不超过15字，保持原意）",
                behavior="respond")

        elif etype in light:
            self._urge = min(1.0, self._urge + self._event_urge_boost(evt))

        else:
            self._urge = min(1.0, self._urge + self._event_urge_boost(evt))
            await self.push_speech(f"[事件] {desc}", behavior="read")

    async def _check_recovery(self) -> None:
        """检查是否有被中断的任务需要恢复询问。"""
        if not self._interrupted_stack:
            return
        # 只在空闲场景 + 空闲了至少 10s 时才问
        if self.scene.name != "idle":
            return
        idle = self.owner.idle_duration()
        if idle < 10:
            return
        # 取最新的中断任务
        entry = self._interrupted_stack.pop()
        task_name = entry.get("task_name", "那个任务")
        prompt = (f"主人，刚才的「{task_name}」还没做完呢~ 要继续吗？喵~")
        # 用 read 模式，不强制
        await self.push_speech(prompt, behavior="read")

    def _on_owner_spoke(self, data: Any) -> None:
        """主人说话了 → 刷新静默计时（陪伴感知）。"""
        self._last_owner_speech_ts = time.time()

    async def _check_danger_recovery(self) -> None:
        """危险解除后的自然衔接：刚被怪追/掉血，场景切回安全 → 吐槽一句。

        对应"可恶的小白终于甩掉他了，我要继续挖矿了~"：
        - 危险事件（low_hp/boss/drowning 等）触发时记录上下文
        - 90s 内场景从 combat/boss 切回安全场景 → 让 LLM 自然衔接
        - 正在做任务的话顺带提一句继续做什么
        """
        if not self._danger_desc or not self._danger_ts:
            return
        if time.time() - self._danger_ts > 90:
            self._danger_desc = ""   # 超时忘掉（人也会忘，符合"重新规划"）
            self._danger_ts = 0.0
            self._danger_task = ""
            return
        safe_scenes = {"recovery", "idle", "mining", "explore",
                       "follow", "build", "travel"}
        if self.scene.name in safe_scenes and self.scene.prev in ("combat", "boss"):
            desc = self._danger_desc
            state = self._get_state()
            if (not state or state.get("alive") is False or state.get("hp", 0) <= 0
                    or state.get("in_lava")
                    or (state.get("in_water") and int(state.get("breath", 200) or 0) < 50)):
                return
            # 当前任务可能已被主人取消/替换，不能播报旧任务即将恢复。
            task = self._get_current_task_name() or ""
            self._danger_desc = ""
            self._danger_ts = 0.0
            self._danger_task = ""
            task_note = f"，当前任务是{task}" if task else "，当前没有前台任务"
            await self.push_speech(
                f"[危险变化] 刚才{desc}，当前附近未检测到敌怪{task_note}。"
                "自然回应一句，只描述已知状态，不宣称绝对安全或自行恢复旧任务。",
                behavior="respond")

    def owner_silence_seconds(self) -> float:
        """主人已沉默多久（秒）。"""
        return time.time() - self._last_owner_speech_ts

    def _recent_vision_fact(self) -> str:
        """最近 2 分钟内的视觉感知报告 → 说话注入（画面事实，防幻觉）。

        数据源：agent.vision.perception.last_report（LLM Vision 分析结果）。
        """
        try:
            vp = getattr(self.agent, "vision", None)
            per = getattr(vp, "perception", None)
            if not per:
                return ""
            report = per.last_report or {}
            if not report:
                return ""
            # perception 内部用 time.monotonic() 记录分析时间，必须同钟比较
            if time.monotonic() - getattr(per, "_last_analysis_ts", 0) > 120:
                return ""
            summary = str(report.get("summary", "") or "")
            threats = report.get("visible_threats", []) or []
            rare = report.get("visible_rare_items", []) or []
            parts = []
            if summary:
                parts.append(f"你刚才看到的画面：{summary}")
            if threats:
                parts.append(f"画面里有敌人：{', '.join(str(t) for t in threats[:3])}")
            if rare:
                parts.append(f"看到稀有物品：{', '.join(str(r) for r in rare[:3])}")
            return ("\n" + "\n".join(parts)) if parts else ""
        except Exception:
            return ""

    # ── 任务记忆 ──────────────────────────────────────

    def remember_interrupted_task(self, task_name: str,
                                   snapshot: Optional[dict] = None) -> None:
        """记录被中断的任务，用于后续恢复询问。"""
        now = time.time()
        # executor 的取消通知和 brain 的中断通知可能在同一轮到达，
        # 同名任务只保留一条，避免恢复询问重复弹出。
        if self._interrupted_stack:
            last = self._interrupted_stack[-1]
            if (last.get("task_name") == task_name
                    and now - last.get("interrupted_at", 0.0) < 10.0):
                return
        self._interrupted_stack.append({
            "task_name": task_name,
            "interrupted_at": now,
            "snapshot": snapshot or {},
        })
        # 只保留最近 5 条：恢复询问按 LIFO 弹最新一条，老的中断没必要一直占内存
        if len(self._interrupted_stack) > 5:
            self._interrupted_stack = self._interrupted_stack[-5:]

    def memory_count(self) -> int:
        return len(self._interrupted_stack)

    def clear_memory(self) -> None:
        self._interrupted_stack.clear()

    # ── 调试/状态面板 ─────────────────────────────────

    def status(self) -> Dict[str, Any]:
        return {
            "scene": self.scene.name,
            "urge": round(self._urge, 3),
            "threshold": round(SCENE_CONFIG.get(self.scene.name, (0.70,))[0], 2),
            "primary_mood": max(self.mood.arcs.values(), key=lambda a: a.value).name,
            "mood_modifier": round(self.mood.combined_modifier(), 2),
            "owner_idle_sec": round(self.owner.idle_duration(), 1),
            "owner_movement": self.owner.movement_type(),
            "interrupted_memories": len(self._interrupted_stack),
        }
