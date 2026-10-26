"""TerrariaAgent：有头客户端架构（全渲染，非无头协议模拟）。
- 通过 GameLauncher 启动完整的 tModLoader 图形客户端
- 移动/战斗控制通过 ModLink (TCP 9877) 实现
- 登录/心跳由游戏原生网络栈处理，不自行模拟协议

"""

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional

from ..autonomous.event_bus import get_event_bus
from .capability import Capability
from .combat import CombatEngine
from .connection import Connection
from .coordinator import TaskCoordinator
from .equipment import EquipmentManager
from .executor import SRC_AUTO, SRC_OWNER, TaskExecutor
from .inventory_ops import InventoryOps
from .launcher import GameLauncher
from .longterm import LongTermManager
from .mining import MiningEngine
from .mod_link import ModLink
from .mod_registry import ModItemRegistry
from .planner import Planner
from .recipe_book import RecipeBook
from .standing_jobs import StandingJobs
from .task_brain import TaskBrain
from .task_chain import Goal, TaskChain
from .task_inquiry import TaskInquiry

# ── 联机模式检测阈值 ──
# 附近玩家距离阈值：存在非自身玩家且距离 < 该值 → 视为联机模式
MULTIPLAYER_DIST_THRESHOLD = 200  # tile


class TerrariaAgent:
    def __init__(self, plugin) -> None:
        from ..core.config_store import PLUGIN_ROOT  # 延迟导入打断循环

        self.plugin = plugin
        self.cfg = plugin._config
        # 标准 logging logger（供各引擎 agent.logger.xxx 使用；agent.log 是内存环）
        self.logger = logging.getLogger("plugin.plugins.neko_terraria.agent")
        self.conn = Connection(self.cfg["mod_host"], self.cfg["mod_port"])
        self.launcher = GameLauncher(self.cfg)
        self.mod = ModLink(self.conn)
        self.combat = CombatEngine(self.mod, self)
        self.mining = MiningEngine(self.mod, self)
        self.equip = EquipmentManager(self.mod)
        self.tasks = TaskChain(self.mining, self.mod, self.equip, self)
        self.registry = ModItemRegistry(PLUGIN_ROOT)
        self.capability = Capability(self)
        self.planner = Planner(self)
        self.executor = TaskExecutor(self)
        self.recipe_book = RecipeBook(self)
        from .base import BaseManager
        from .explore import UndergroundExplorer
        from .life import LifeEngine

        self.base = BaseManager(self)
        self.explorer = UndergroundExplorer(self)
        self.life = LifeEngine(self)
        self.brain = TaskBrain(self)
        self.longterm = LongTermManager(self)
        self.jobs = StandingJobs(self)
        self.coordinator = TaskCoordinator(self, name=self._character_name())
        self.inquiry = TaskInquiry(self)  # v2.1: 任务中决策询问
        self.items = InventoryOps(self)
        from .events import EventResponder

        self.events = EventResponder(self)
        # v2.1: 视觉管线（截图源在 lifecycle 注入；无源时自动降级）
        from ..core.vision import VisionPipeline

        self.vision = VisionPipeline(self.cfg, agent=self, push_message=getattr(plugin, "push_message", None))
        # v0.10: 英雄成长引擎（周期扫配方合成升级）
        from .upgrade import UpgradeEngine

        self.upgrade = UpgradeEngine(self)
        self._state: Dict[str, Any] = {}
        self._last_state_push = 0.0
        self._in_combat: bool = False  # 战斗中标志：防止导航遇敌检查自我嵌套
        self._inv_full: Dict[str, Any] = {"hotbar": [], "equipped": [], "inventory": []}
        self._chests: List[Dict[str, Any]] = []
        self._world_info: Dict[str, Any] = {}
        self._log: List[Dict[str, Any]] = []
        self._running = False
        self._background_tasks: set[asyncio.Task] = set()
        self._idle_task: Optional[asyncio.Task] = None
        self._suppressed_actions: set[str] = set()
        self._autonomy_paused_until = 0.0
        # 防重入：boot 与 nt_connect 可能同时调 start()，并发会启动两个 tML 进程、
        # 覆盖 self.process 导致 boot 卡死。只允许一个 start 真正执行。
        self._start_lock = asyncio.Lock()
        self._start_task: Optional[asyncio.Task] = None

        # ── 死亡/复活状态（按生存循环惯例 bridge._is_dead） ──
        self._is_dead: bool = False
        self._death_message: str = ""  # 最后一条死亡信息
        self._death_position: Optional[tuple] = None  # (tile_x, tile_y) 死亡位置
        self._death_count: int = 0  # 累计死亡次数
        self._last_hp: int = 0  # 上一帧血量（用于暴跌检测）

        # ── 复活回调 ──
        self.respawn_callbacks: list = []  # 复活时触发，供 brain 注册

        # ── 联机模式 ──
        self._multiplayer_mode: bool = False  # 检测到附近有玩家 → 联机模式

        # ── idle 心跳状态：随 _state_loop 1s 轮询更新（不独立开线程） ──
        self._idle_ctx: Dict[str, Any] = {
            "cycle": 0,  # 状态轮询计数（= 秒）
            "last_mode": None,  # idle 模式：patrol/mine/chest/gather/light
            "mode_n": 0,  # 当前模式连续秒数（同一模式不做反复声明）
            "patrol_dir": 1,  # 巡逻方向 ±1
            "last_event": "",  # 最近宣布过的事（去重播报，防刷屏）
            "home": None,  # (x, y) 基地坐标；在 host 附近开箱/被叫停后兜底 = 出生点
            "last_resupply": 0.0,  # 上次补给时间戳（补给冷却 90s）
            "gave_torch": 0,  # 上次传递火把计数（随 fire_and_forget 发送）
        }

    async def start(self) -> bool:
        """启动 AI 客户端 → 加入游戏。

        所有默认值统一由 config_store.DEFAULTS 管理，此处直接从 self.cfg 取，
        不再写死第二份 fallback。
        """
        # 防重入：boot 和 nt_connect 可能并发调用 start()。
        # 若已在启动中，返回进行中状态，不重复拉起 tML 进程（否则覆盖
        # self.process / 卡死 boot）。已运行直接返回 True。
        if self._running:
            return True
        if self._start_task is not None and not self._start_task.done():
            # 正在启动中：等它完成并返回其结果
            return await self._start_task

        async def _run() -> bool:
            async with self._start_lock:
                if self._running:
                    return True
                try:
                    result = await self._start_impl()
                except asyncio.CancelledError:
                    self._cleanup_start_failure()
                    raise
                except Exception as exc:
                    self.log(f"启动 AI 客户端异常: {type(exc).__name__}: {exc}", "error")
                    self._cleanup_start_failure()
                    return False
                if not result:
                    # 启动流程的任一阶段失败时释放半初始化的 TCP/进程，
                    # 否则下一次“重新连接”会复用坏状态或拉起重复客户端。
                    self._cleanup_start_failure()
                return result

        self._start_task = asyncio.create_task(_run())
        return await self._start_task

    def _cleanup_start_failure(self) -> None:
        """清理未完成启动留下的连接、回调和客户端进程。"""
        self._running = False
        try:
            self.conn.close()
        except Exception:
            pass
        try:
            self.launcher.close()
        except Exception:
            pass

    async def _start_impl(self) -> bool:
        """start() 实际执行体（防重入锁保护）。"""
        server_host: str = self.cfg["server_host"]
        server_port: int = self.cfg["server_port"]
        server_password: str = self.cfg["server_password"]
        character_name: str = self._character_name()

        if not await self.launcher.launch():
            return False

        # launcher 已等待 3s；Mod TCP 监听器约在进程启动后 10~12s 就绪
        # 此处补偿等待，避免无效重试
        await asyncio.sleep(8.0)

        # 连接 AI Mod（只有 AI 装了 NekoTerrariaLink → 独占 9877）
        if not await self._ensure_mod_connected():
            self.launcher._log("无法连接到 AI 的 NekoTerrariaLink Mod，检查 AI 客户端是否正常加载")
            return False

        # 注册 mod 事件回调（死亡/Boss/入侵等主动推送事件）
        self.conn.on_message(self._handle_mod_event)

        # 等待游戏加载到主菜单（此时 DrawMenu hook 已在跑自动选角色）
        await asyncio.sleep(1.0)

        # ===== 1) 通知 mod 选定角色（统一从前端 character_name 读取） =====
        # Mod 自身也有 AutoSelect 机制，即使 Python 命令失败也能兜底
        if character_name:
            ok = await self.mod.select_character(name=character_name)
            if not ok:
                self.launcher._log(f"指定角色 '{character_name}' 命令失败（Mod AutoSelect 兜底）")
            else:
                self.launcher._log(f"已选中角色 '{character_name}'")
        else:
            await self.mod.select_character()

        # ===== 2) 加入服务器 —— 使用状态机轮询，模仿 Terraria-Bot =====
        # join_server(wait_confirm=True) 会在 mod 侧等待真实连接确认（netMode→1 + player.active）
        self.launcher._log(f"正在连接服务器 {server_host}:{server_port}...")
        for retry in range(5):
            # 先确保 Mod TCP 连通
            if not await self._ensure_mod_connected():
                self.launcher._log(f"Mod 连接丢失 (第{retry + 1}次重试)，正在重连...")
                if retry < 4:
                    await asyncio.sleep(1.0)
                continue

            self.launcher._log(f"发送 join_server 命令 (第{retry + 1}次)...")
            ok = await self.mod.join_server(
                server_host,
                server_port,
                server_password,
                character_name=character_name,
                wait_confirm=True,
                confirm_timeout=25,
            )
            if ok:
                self.launcher._log("AI 成功进入服务器！")
                break
            # 未确认入服：多半是 TCP 半开（AI 客户端已断但本地未检测），
            # 强制断开让下一次 _ensure_mod_connected 真正重连
            self.launcher._log(f"join_server 未确认入服 (第{retry + 1}次)，重试...")
            self.conn.close()
            if retry < 4:
                await asyncio.sleep(1.0)
        else:
            self.launcher._log("AI 加入服务器失败，请确认服务器已开启且配置正确")
            return False

        self._running = True
        self.events.bind()
        self._spawn_background_task(self.tasks.run_loop())
        self._spawn_background_task(self._state_loop())
        self._spawn_background_task(self._auto_register())
        return True

    def _spawn_background_task(self, coro) -> asyncio.Task:
        task = asyncio.create_task(coro)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_task_done)
        return task

    def _background_task_done(self, task: asyncio.Task) -> None:
        """回收后台任务并记录未处理异常，避免行为循环静默死亡。"""
        self._background_tasks.discard(task)
        if task.cancelled():
            return
        try:
            error = task.exception()
        except Exception as exc:
            self.log(f"读取后台任务结果失败: {exc}", "warn")
            return
        if error is not None:
            self.log(f"后台任务异常: {type(error).__name__}: {error}", "error")
            self.logger.error("后台任务异常", exc_info=error)

    async def _ensure_mod_connected(self) -> bool:
        """确保已连接到 AI Mod 的 TCP 端口，未连接则尝试重连。"""
        if self.conn.is_mod_connected():
            return True
        self.registry.invalidate()
        self.recipe_book.invalidate()
        for retry in range(10):
            if await self.conn.connect_mod(retry_ports=(retry >= 3)):
                return True
            if retry < 9:
                await asyncio.sleep(1.0)
        return False

    def _character_name(self) -> str:
        """直接从配置文件读取角色名，零 cfg 传播依赖。"""
        from ..core.config_store import load_user_config

        return load_user_config().get("character_name", "Neko")

    async def refresh_state(self) -> Dict[str, Any]:
        """立即刷新状态"""
        player_name = self._character_name()
        requested_at = time.monotonic()
        st = await self.mod.get_state(player_name)
        if st and self._last_state_push <= requested_at:
            self._state = st
        return self.get_state()

    def log(self, msg: str, kind: str = "info") -> None:
        # 内存环形日志，供前端静默刷新展示
        from time import time

        self._log.append({"t": time(), "msg": msg, "kind": kind})
        if len(self._log) > 100:
            self._log = self._log[-100:]

    async def _auto_register(self) -> None:
        """进游戏时增量同步 mod 物品。

        enum_items + get_recipes 是一次性重请求（C# 侧遍历全物品/全配方），
        启动瞬间并发会占用命令锁，把推送事件/导航命令堵住。
        延迟 auto_register_delay_seconds 再跑，等入服推送稳定。
        """
        await asyncio.sleep(self.cfg.get("auto_register_delay_seconds", 25.0))
        try:
            mods = await self.mod.enum_items()
            if not mods or not self._running:
                return
            diff = self.registry.sync_from_enum(mods)
            if diff["added"]:
                self.log(f"认识了新 mod：{', '.join(diff['added'])}", "info")
            if diff["removed"]:
                self.log(f"这些 mod 不见了：{', '.join(diff['removed'])}", "warn")
        except Exception:
            pass
        try:
            n = await self.recipe_book.refresh(force=True)
            if n:
                self.log(f"学会了 {n} 条配方", "item")
        except Exception:
            pass

    async def stop(self) -> None:
        self._running = False
        self.registry.invalidate()
        self.recipe_book.invalidate()
        startup = self._start_task
        if startup and startup is not asyncio.current_task() and not startup.done():
            startup.cancel()
            await asyncio.gather(startup, return_exceptions=True)
        await self.stop_everything("Agent 停止")
        tasks = list(self._background_tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._background_tasks.clear()
        self.launcher.close()
        self.conn.close()

    async def _state_loop(self) -> None:
        """状态维护循环 + 死亡/复活/联机检测 + 周期杂务。

        v2.3 减主线程负担：状态改由 mod 主动推送驱动——
          game_state 每 2s 推全量（hp/位置/敌人/玩家/身体感字段，见 C# PushGameState）
          player_status 每 1s 推 hp/位置
        这里不再每秒 get_state 轮询（曾与推送重复，给 C# 主线程徒增 200 NPC 扫描）。
        保留低频兜底轮询（兜底间隔秒，推送断/漏时补），其余周期杂务节奏不变。
        """
        player_name = self._character_name()
        bus = get_event_bus()
        self.log(f"_state_loop 启动, player_name='{player_name}', running={self._running}")
        self.plugin.logger.info(f"[_state_loop] 启动 player_name='{player_name}'")
        loop_count = 0
        # 低频兜底 get_state 间隔：推送 2s 一次，兜底 5s 足矣（断推时最多 5s 旧状态）
        fallback_interval = max(3, int(self.cfg.get("state_fallback_poll_seconds", 5) or 5))
        while self._running:
            loop_count += 1
            if not self.conn.is_mod_connected():
                self.registry.invalidate()
                self.recipe_book.invalidate()
                await self.stop_everything("游戏连接已断开")
                self._state.clear()
                self._inv_full = {"hotbar": [], "equipped": [], "inventory": []}
                await asyncio.sleep(2)
                if not self._running:
                    break
                if not await self._ensure_mod_connected():
                    continue
                self._spawn_background_task(self._auto_register())
            try:
                # 兜底轮询（低频繁）：推送为主，这里只在游戏状态事件尚未填缓存时补一次，
                # 并承担死亡/复活/联机检测（推送是主通道，轮询是保险）
                if loop_count == 1 or loop_count % fallback_interval == 0:
                    requested_at = time.monotonic()
                    st = await self.mod.get_state(player_name)
                    if st:
                        # 推送中断时轮询必须更新旧字段；有更新推送则保留更新的快照。
                        if self._last_state_push <= requested_at:
                            self._state.update(st)
                        # ── 死亡/复活检测（基于推送的缓存血量） ──
                        hp = st.get("hp", -1)

                        # 检测死亡：hp==0 且之前未标记死亡
                        if hp == 0 and not self._is_dead:
                            self._is_dead = True
                            self._death_count += 1
                            tx = st.get("tile_x", st.get("x", 0))
                            ty = st.get("tile_y", st.get("y", 0))
                            self._death_position = (tx, ty)
                            self._death_message = f"角色在 tile=({tx},{ty}) 死亡 (第{self._death_count}次)"
                            print(f"[agent] 💀 {self._death_message}")
                            bus.fire(
                                "player_died",
                                {
                                    "count": self._death_count,
                                    "position": self._death_position,
                                    "message": self._death_message,
                                },
                            )

                        # 检测复活：hp>0 且之前标记为死亡
                        if hp > 0 and self._is_dead:
                            self._is_dead = False
                            tx = st.get("tile_x", st.get("x", 0))
                            ty = st.get("tile_y", st.get("y", 0))
                            print(f"[agent] ✨ 角色复活！hp={hp} pos=({tx},{ty}) 累计死亡={self._death_count}")
                            bus.fire(
                                "player_respawned",
                                {
                                    "hp": hp,
                                    "position": {"tile_x": tx, "tile_y": ty},
                                    "death_count": self._death_count,
                                },
                            )
                            # 触发复活回调（brain 注册的自动寻路等）
                            for cb in self.respawn_callbacks:
                                try:
                                    if asyncio.iscoroutinefunction(cb):
                                        self._spawn_background_task(cb())
                                    else:
                                        cb()
                                except Exception:
                                    pass

                        # 更新上一帧血量（供 service 暴跌检测）
                        self._last_hp = hp

                        # ── 联机模式检测 ──
                        nearby = st.get("nearby_players", []) or []
                        has_other = False
                        if nearby and isinstance(nearby, list):
                            me_x = int(st.get("tile_x", 0) or 0)
                            me_y = int(st.get("tile_y", 0) or 0)
                            for p in nearby:
                                if isinstance(p, dict):
                                    name = p.get("name", "")
                                    px = int(p.get("tile_x", 0) or 0)
                                    py = int(p.get("tile_y", 0) or 0)
                                    dist = ((px - me_x) ** 2 + (py - me_y) ** 2) ** 0.5
                                    if name and name != player_name and dist < MULTIPLAYER_DIST_THRESHOLD:
                                        has_other = True
                                        break
                        if has_other != self._multiplayer_mode:
                            self._multiplayer_mode = has_other
                            mode_str = "联机" if self._multiplayer_mode else "单人"
                            print(f"[agent] 🔄 模式切换 → {mode_str}模式")
                            bus.fire("multiplayer_mode_changed", {"mode": mode_str})

            except Exception as e:
                self.log(f"_state_loop 处理异常: {e}", "error")

            # 周期杂务（节奏不变，用 self._state 缓存而非本地 st——
            # 现在状态主要由推送维护，缓存就是最新）
            st = self._state

            # 背包完全按需：挖矿/查询/装备时主动 get_inventory，这里不轮询。
            # 箱子缓存低频刷新（变化慢），供取物/存物使用
            if loop_count % 30 == 0:
                try:
                    self._chests = await self.mod.enum_chests()
                    await self.events.report_new_chests(self._chests)
                except Exception as e:
                    self.log(f"enum_chests 异常: {e}", "error")

            if not self._world_info:
                try:
                    self._world_info = await self.mod.get_server_info()
                    self.log(f"get_server_info: keys={list(self._world_info.keys())}")
                except Exception as e:
                    self.log(f"get_server_info 异常: {e}", "error")

            # 空闲小忙（v0.11 A4）：没人在指挥时也会自己找事干，贴身陪玩感
            try:
                from .idle import idle_drudge
            except Exception:
                idle_drudge = None
            busy = self.coordinator.status().get("busy")
            if idle_drudge is not None and not busy and self.autonomy_allowed():
                try:
                    # 状态维护不应被长达数十秒的空闲动作阻塞。
                    if not getattr(self, "_idle_task", None) or self._idle_task.done():
                        self._idle_task = self._spawn_background_task(idle_drudge(self, st))
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    self.log(f"idle_drudge 异常: {e}", "warn")

            # 英雄成长（v0.10）：周期扫配方合成升级装备
            try:
                if (not self.coordinator.status().get("busy") and self.autonomy_allowed()
                        and not self._in_combat and (not getattr(self, "_idle_task", None) or self._idle_task.done())):
                    await self.upgrade.consider()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.log(f"upgrade 异常: {e}", "warn")

            await asyncio.sleep(self.cfg.get("state_tick_interval_seconds", 1.0))

    # ── 事件处理（按生存循环惯例 bridge._on_combat_event） ──

    def _handle_mod_event(self, msg: dict) -> None:
        """处理模组主动推送的事件消息。

        事件格式：{"type":"event","event":"player_died","message":"..."}
        可能事件：player_died, boss_spawned, boss_killed, invasion_start, npc_arrived 等
        """
        event = msg.get("event", "")
        bus = get_event_bus()
        print(f"[agent] 📨 mod事件: {event} msg={msg.get('message', '')[:80]}")

        if event == "player_died":
            # 事件推送的死亡（优先于轮询，更及时）
            if not self._is_dead:
                self._is_dead = True
                self._death_count += 1
                self._death_message = msg.get("message", f"角色死了 (第{self._death_count}次)")
                print(f"[agent] 💀 {self._death_message}")
                bus.fire(
                    "player_died",
                    {
                        "count": self._death_count,
                        "message": self._death_message,
                        "source": "mod_event",
                    },
                )

        elif event == "player_respawned":
            # Mod 已确认复活时立即解除死亡闸门；否则只靠低频
            # get_state 轮询，可能让自主行为停在死亡状态数秒甚至更久。
            self._is_dead = False
            self._state["alive"] = True
            if msg.get("hp") is not None:
                self._state["hp"] = int(msg.get("hp", self._state.get("hp", 0)))
            for cb in self.respawn_callbacks:
                try:
                    if asyncio.iscoroutinefunction(cb):
                        self._spawn_background_task(cb())
                    else:
                        cb()
                except Exception:
                    pass
            bus.fire("player_respawned", {
                "hp": self._state.get("hp", 0), "source": "mod_event"})

        elif event == "boss_spawned":
            bus.fire("boss_spawned", {"name": msg.get("boss_name", msg.get("message", "未知Boss"))})

        elif event == "boss_killed":
            bus.fire("boss_killed", {"name": msg.get("boss_name", msg.get("message", "未知Boss"))})

        elif event == "invasion_start":
            bus.fire("invasion_start", {"message": msg.get("message", "")})

        elif event == "invasion_end":
            bus.fire("invasion_end", {"message": msg.get("message", "")})

        elif event == "npc_killed":
            # CombatEngine uses the Mod's slot/type identity to distinguish an
            # actual kill from an NPC merely leaving the 50-tile state radius.
            self.combat.confirm_npc_kill(msg)
            bus.fire("npc_killed", msg)

        # v3.0: 导航状态流事件（nav_moving/nav_arrived/nav_stuck/nav_timeout）
        # → 转发给 mod_link 的导航监听（navigate_async）
        elif event.startswith("nav_"):
            try:
                self.mod._on_nav_event(msg)
            except Exception:
                pass

        # v3.0: mod 主动汇报血量/位置（1s 一次）→ 更新 _state 缓存，
        # 不依赖 get_state 轮询的响应解析（避免 hp=0 问题）
        elif event == "player_status":
            try:
                self._state["hp"] = int(msg.get("hp", self._state.get("hp", 0)))
                self._state["max_life"] = int(msg.get("max_hp", self._state.get("max_life", 100)))
                self._state["tile_x"] = int(msg.get("x", self._state.get("tile_x", 0)))
                self._state["tile_y"] = int(msg.get("y", self._state.get("tile_y", 0)))
                if "alive" in msg:
                    self._state["alive"] = bool(msg.get("alive"))
                if msg.get("alive") is False and not self._is_dead:
                    self._is_dead = True
            except Exception:
                pass

        # v3.0: mod 统一推送全量游戏状态（血量/位置/敌人/玩家/时间，2s 一次）
        elif event == "game_state":
            try:
                self._last_state_push = time.monotonic()
                pl = msg.get("player", {}) or {}
                self._state["hp"] = int(pl.get("hp", self._state.get("hp", 0)))
                self._state["max_life"] = int(pl.get("max_life", self._state.get("max_life", 100)))
                self._state["tile_x"] = int(pl.get("tile_x", self._state.get("tile_x", 0)))
                self._state["tile_y"] = int(pl.get("tile_y", self._state.get("tile_y", 0)))
                if "alive" in pl:
                    self._state["alive"] = bool(pl.get("alive"))
                    if pl.get("alive") is False:
                        self._is_dead = True
                    elif self._is_dead and int(pl.get("hp", 0) or 0) > 0:
                        # 兼容丢失 player_respawned 事件的连接：状态推送本身
                        # 已证明角色恢复生命，解除自主行为的死亡闸门。
                        self._is_dead = False
                # 身体感/移动字段（PushGameState 补齐推送后，Python 无需每秒轮询 get_state）
                for f in ("velocity_x", "velocity_y", "grounded", "selected_slot",
                          "in_water", "in_lava", "in_honey", "breath"):
                    if f in pl:
                        self._state[f] = pl.get(f)
                if "biome" in pl:
                    self._state["biome"] = pl.get("biome", "")
                if "buffs" in pl:
                    self._state["buffs"] = pl.get("buffs", [])
                if "movement_state" in pl:
                    self._state["movement_state"] = pl.get("movement_state", "")
                if "brightness" in pl:
                    self._state["brightness"] = pl.get("brightness", 1.0)
                # 背包
                inv = msg.get("inventory", {}) or {}
                if inv:
                    self._inv_full = {
                        "hotbar": inv.get("hotbar", []),
                        "equipped": inv.get("equipped", []),
                        "inventory": inv.get("inventory", []),
                        "selected_slot": pl.get("selected_slot", 0),
                    }
                # 附近敌人/玩家/时间
                if "nearby_npcs" in msg:
                    npcs = msg.get("nearby_npcs", []) or []
                    # C# 推流 npcs 用 tileX/tileY（camelCase），归一化供战斗/大脑读取
                    for n in npcs:
                        if isinstance(n, dict) and "tileX" in n and "tile_x" not in n:
                            n["tile_x"] = n.get("tileX", 0)
                            n["tile_y"] = n.get("tileY", 0)
                    self._state["nearby_npcs"] = npcs
                if "nearby_players" in msg:
                    players = msg.get("nearby_players", []) or []
                    # 兼容旧版/第三方 Mod 的 camelCase 坐标，避免跟随和
                    # 自主社交读到 0,0 后误判主人丢失。
                    for player in players:
                        if isinstance(player, dict):
                            if "tileX" in player and "tile_x" not in player:
                                player["tile_x"] = player.get("tileX", 0)
                            if "tileY" in player and "tile_y" not in player:
                                player["tile_y"] = player.get("tileY", 0)
                    self._state["nearby_players"] = players
                if "time_of_day" in msg:
                    self._state["time_of_day"] = msg.get("time_of_day", "")
            except Exception:
                pass

        # 其他事件统一转发
        elif event:
            bus.fire(event, msg)

    def on_respawn(self, callback) -> None:
        """注册复活回调。复活时自动调用，用于 brain 注册自动寻路等。"""
        if callback not in self.respawn_callbacks:
            self.respawn_callbacks.append(callback)

    def off_respawn(self, callback) -> None:
        """移除复活回调，避免插件重启后旧 Brain 继续接管移动。"""
        try:
            self.respawn_callbacks.remove(callback)
        except ValueError:
            pass

    async def submit_goal(self, goal: Goal) -> None:
        await self.tasks.submit(goal)

    async def run_complex_task(
        self,
        steps: List[Dict[str, Any]],
        goal_text: str = "",
        source: str = SRC_OWNER,
        dry_run: bool = False,
        _retried: bool = False,
    ) -> Dict[str, Any]:
        """收到任务 → 想(评估) → 处理(规划) → 做(执行)。主人的任务可打断自主行为。"""
        # 想：推演整串步骤，缺东西就自己想补救办法
        assess = await self.brain.think(steps)
        self.log(f"想：{assess.say()}", "task")
        for t in assess.thoughts:
            self.log(f"  思考：{t}", "task")
        if not assess.doable:
            # v2.1: 需要主人提供信息（缺材料/目标模糊）→ 发起决策询问，带答案重试一次
            if not _retried and self.inquiry and assess.need_from_owner:
                inq = self.inquiry.ask(
                    question=f"主人，{assess.say()}，怎么办？",
                    options=["你来决定", "先不做"],
                    context={"need": assess.need_from_owner, "goal": goal_text},
                    timeout=30.0,
                )
                if inq:
                    await self.speak(f"[任务询问] {inq.question} 请根据实际困难向主人询问。")
                    ans = await self.inquiry.wait_answer(inq)
                    rejected = ans.startswith(("deny", "hold", "timeout", "cancelled")) or ans == "先不做"
                    if not rejected:
                        self.log(f"主人回答：{ans}，带答案重试任务", "task")
                        return await self.run_complex_task(
                            steps, f"{goal_text}（主人说：{ans}）", source, dry_run=dry_run, _retried=True
                        )
            return {
                "ok": False,
                "status": "not_doable",
                "phase": "think",
                "output": assess.say(),
                "why": assess.explain(),
                "need": assess.need_from_owner,
            }
        # 想到了补救办法就先说一声，让主人知道我在动脑子
        if assess.fixes:
            await self.send_chat(f"主人，{assess.say()}~")

        # 处理：用补全前置后的步骤编译，顺手合并冗余
        plan = self.brain.plan(steps, goal_text, assess)
        self.log(f"处理：{plan.say()}", "task")
        if not plan.goals or plan.blocked:
            reason = plan.blocked_reason or "没解析出可执行步骤"
            return {"ok": False, "status": "empty_plan", "phase": "plan",
                    "output": reason,
                    "skipped": plan.skipped}
        if dry_run:
            return {
                "ok": True,
                "status": "planned",
                "phase": "plan",
                "output": f"我会这样做：{plan.say()}",
                "why": assess.explain(),
                "fixes": assess.fixes,
                "skipped": plan.skipped,
            }

        # 做：交给执行器（单槽位 + 可被主人打断）
        async def _work(info):
            info.phase = "act"
            done_bits = []
            started_only = False
            for i, g in enumerate(plan.goals):
                if self.executor.should_stop():
                    return {"ok": False, "status": "cancelled", "output": f"做到第{i + 1}步被叫停了"}
                info.step_index = i + 1
                info.note = plan.outline[i] if i < len(plan.outline) else g.goal_type
                self.log(f"做：第{i + 1}/{len(plan.goals)}步 {info.note}", "task")
                # 每步开工前说一声（A5：过程不静默，猫娘有存在感，主人知道在忙）
                try:
                    await self.send_chat(f"好，先来做第{i + 1}步：{info.note}~")
                except Exception:
                    pass
                ok = await self.tasks.run_one(g)
                if not ok:
                    msg = g.report_fail or f"第{i + 1}步没做成"
                    await self.send_chat(f"主人，{msg}，我停下来等你~")
                    return {
                        "ok": False,
                        "status": g.outcome if g.outcome in ("partial", "unconfirmed") else "step_failed",
                        "phase": "act",
                        "output": ("已确认的前序结果：" + "、".join(done_bits) + "。" if done_bits else "")
                                  + f"在第{i + 1}步「{info.note}」停下：{msg}。整个任务尚未完成。",
                    }
                # 完成播报用实测数（g.actual），不 echo 计划里的目标数——
                # 曾 plan.say() 报"挖铁矿x10 完成"，实际可能只挖到几块。
                done_desc = self._step_done_text(g)
                started_only = started_only or g.outcome == "started"
                done_bits.append(done_desc)
                if g.outcome == "started" and i < len(plan.goals) - 1:
                    return {"ok": False, "status": "unconfirmed", "phase": "act",
                            "output": "、".join(done_bits) + "；当前步骤仅启动，尚未完成，后续依赖步骤未执行。"}
                try:
                    await self.send_chat(f"{done_desc}~")
                except Exception:
                    pass
            tail = "、".join(done_bits) if done_bits else plan.say()
            # output 是纯事实（不含"做完啦"等硬编码台词）——由
            # brain._on_executor_task_done 交宿主 LLM 自动生成汇报
            return {"ok": True, "status": "started" if started_only else "ok", "phase": "act",
                    "output": tail}

        return await self.executor.run(goal_text or "多步任务", _work, source=source, steps=plan.outline)

    @staticmethod
    def _step_done_text(g: "Goal") -> str:
        """只汇报已核验结果；数量未知或仅启动时不能退化成通用“完成”。"""
        if g.outcome != "completed":
            return g.evidence or g.report_fail or "结果尚未确认"
        if g.evidence:
            return g.evidence
        name = g.goal_type
        if name == "follow":
            return "长期跟随已启动"
        if g.target and g.goal_type in ("mine", "chop", "craft", "gather",
                                        "fetch", "give", "fish"):
            name = f"{g.goal_type} {g.target}"
        act = int(getattr(g, "actual", 0) or 0)
        amt = int(getattr(g, "amount", 0) or 0)
        if act > 0 and amt > 0:
            if act >= amt:
                return f"{name}完成（{act}个）"
            return f"{name}只做到{act}个"
        if act > 0:
            return f"{name}完成（{act}个）"
        if g.goal_type in ("mine", "chop", "craft", "gather", "fetch", "give", "fish", "combat"):
            return f"{name}数量未确认"
        return f"{name}已执行并核验"

    async def command(self, text: str, source: str = SRC_OWNER) -> Dict[str, Any]:
        """自然语言统一入口：自动判断长期任务/有限任务/喊停并派发。"""
        return await self.coordinator.handle(text, source)

    async def start_longterm(
        self, kind: str, target: str = "", amount: int = 0, reason: str = "", **params
    ) -> Dict[str, Any]:
        """直接起一个长期任务（跟随/挖矿/守点）。"""
        return await self.jobs.start(kind, target=target, amount=amount, reason=reason, **params)

    async def stop_longterm(self, kind: str = "", why: str = "主人喊停") -> Dict[str, Any]:
        """停某个或全部长期任务。"""
        if kind:
            ok = await self.longterm.stop(kind, why)
            return {"ok": ok, "output": "停下了~" if ok else "我没在做这个呀~"}
        names = await self.longterm.stop_all(why)
        return {"ok": bool(names), "output": ("、".join(names) + " 都停下了~") if names else "我现在闲着呢~"}

    def task_status(self) -> Dict[str, Any]:
        """我在干嘛：前台 + 后台一起报。"""
        return self.coordinator.status()

    async def interrupt_current(self, why: str = "主人有新指令") -> bool:
        # 主人随时可打断：停掉正在执行的前台任务（长期任务不受影响，继续跟着）
        return await self.executor.cancel_current(why)

    async def stop_everything(self, why: str = "主人喊停") -> Dict[str, Any]:
        """全停：前台任务 + 所有长期任务。"""
        self.suppress_autonomy()
        self.coordinator.cancel_pending_commands()
        await self.cancel_autonomous_actions()
        # 先摘除长期任务，再取消前台；否则 executor 的 finally 可能先
        # release_yield，让长期 worker 在主人喊停和 stop_all 之间抢跑一轮。
        names = await self.longterm.stop_all(why)
        fg = await self.executor.cancel_current(why)
        self.inquiry.cancel_all()
        actions_stopped = await self.mod.stop_actions()
        stopped = bool(actions_stopped) and not self.executor.busy() and not self.longterm.busy_kinds()
        return {"ok": stopped, "actions_stopped": bool(actions_stopped),
                "foreground_cancelled": fg, "longterm_stopped": names}

    @staticmethod
    def _action_kind(kind: str) -> str:
        return {"木材": "chop", "wood": "chop", "dig": "mine"}.get(kind, kind)

    def suppress_autonomy(self, kind: str = "") -> None:
        if kind:
            self._suppressed_actions.add(self._action_kind(kind))
        else:
            self._autonomy_paused_until = time.monotonic() + 30.0

    def allow_autonomy(self, kind: str = "") -> None:
        self._autonomy_paused_until = 0.0
        if kind:
            self._suppressed_actions.discard(self._action_kind(kind))

    def autonomy_allowed(self, kind: str = "") -> bool:
        if (not self._running or not self.conn.is_mod_connected()
                or self._is_dead
                or time.monotonic() < self._autonomy_paused_until
                or self._action_kind(kind) in self._suppressed_actions):
            return False
        # 所有自主入口共用这道闸门：前台任务、长期任务和另一个自主动作
        # 已经占用输入时，不再让 idle/处境层并行改选中物品或发导航。
        #
        # AutonomousBrain calls this check from inside its own action task.
        # Do not inspect brain._busy here: _run_action() sets that flag before
        # entering _act_on_drive/_guard_check, so checking it would reject the
        # action that just passed the gate and make every autonomous action a
        # no-op. The brain owns its single-action mutex; this gate only blocks
        # competing foreground/long-term work.
        if self._in_combat:
            return False
        ex = getattr(self, "executor", None)
        if ex and ex.busy():
            return False
        lt = getattr(self, "longterm", None)
        # 守卫是长期采集/跟随遇敌时唯一允许的高优先级自主动作；
        # 其他自主行为必须等待长期任务让出控制权。
        if lt and lt.busy_kinds() and kind not in ("guard", "combat"):
            return False
        return True

    async def cancel_autonomous_actions(self, kind: str = "") -> None:
        idle = getattr(self, "_idle_task", None)
        if idle and idle is not asyncio.current_task() and not idle.done():
            idle.cancel()
            await asyncio.gather(idle, return_exceptions=True)
        brain = getattr(self.plugin, "_autonomous_brain", None)
        if brain:
            await brain.cancel_actions(kind)

    async def send_chat(self, text: str) -> bool:
        """通过 Mod 发送聊天消息（A5：加保护，发不出去不炸线程）"""
        try:
            return bool(await self.mod.send_chat(text))
        except Exception as e:
            self.log(f"发送聊天失败: {e}", "warn")
            return False

    async def speak(self, text: str, ai_behavior: str = "respond", *,
                    fallback_text: str = "") -> bool:
        """播报一句话：推给宿主对话 LLM（respond → 语音合成让主人听到）。

        - ai_behavior="respond"：猫娘语气回复（触发语音）
        - ai_behavior="read"：静默上下文（不打断主人，只给 LLM 知道）

        SDK 的 push_message 是同步方法（返回 PushMessageResult），不能 await。
        返回是否受理或已发送游戏聊天，不代表语音已播放。
        fallback_text 只能传给玩家看的事实短句，不能包含 LLM 提示词。
        """
        plugin = getattr(self, "plugin", None)
        push = getattr(plugin, "push_message", None)
        if not push:
            self.log("宿主没有 push_message，无法播报猫娘消息", "warn")
        else:
            try:
                result = push(parts=[{"type": "text", "text": text}], ai_behavior=ai_behavior)
                if isinstance(result, dict) and (
                        result.get("submitted") is False or result.get("ok") is False):
                    self.log(f"宿主未受理猫娘消息：{result.get('reason', 'unknown')}", "warn")
                else:
                    # SDK acknowledgement confirms submission, not playback.
                    return True
            except Exception as exc:
                self.log(f"猫娘消息推送失败: {exc}", "warn")
        # respond/read 内容通常是给宿主 LLM 的事实和提示词，不能把内部
        # 指令原文直接显示在游戏聊天里。blind 才是已经写给玩家看的紧急短句。
        fallback = text if ai_behavior == "blind" else fallback_text
        if ai_behavior in ("blind", "respond") and fallback and self.running:
            return await self.send_chat(fallback[:200])
        return False

    def get_state(self) -> Dict[str, Any]:
        if not self.conn.is_mod_connected():
            return {}
        return self._state

    def remember(self, key: str, value: str, category: str = "fact") -> None:
        try:
            plugin = getattr(self, "plugin", None)
            store_fn = getattr(plugin, "_memory_store", None)
            if store_fn:
                store_fn().remember(key, value, category=category)
        except Exception:
            pass

    @property
    def current_goal(self) -> str:
        g = getattr(self.tasks, "_current", None)
        return g.goal_type if g else ""

    def resolve_item(self, name: str) -> int:
        from .item_npc_dict import item_id

        return item_id(name, self.registry)

    async def follow_player(self, player_pos: tuple) -> None:
        """自动寻路走到玩家身边"""
        px, py = player_pos

        async def _go(info):
            ok = await self.navigate_to(px, py, timeout=8)
            return {"ok": ok, "output": "到主人身边了" if ok else "没追上主人"}

        await self.executor.run("跟随玩家", _go, source=SRC_AUTO)

    async def heal_self(self) -> bool:
        # 用注册表中标记为 heal（加血）的物品自愈：
        # 先确保背包有（没有就 give 一个），再选中并使用
        heals = self.registry.find_by_tag("heal")
        for pid in heals:
            try:
                inv = await self.mod.get_inventory()
                slot_id = None
                for slot in (inv.get("hotbar", []) or []) + (inv.get("inventory", []) or []):
                    if slot.get("id") == pid:
                        slot_id = slot.get("inv_slot", 0)
                        break
                if slot_id is None:
                    ok = await self.mod.give_item(pid, 1)
                    if not ok:
                        continue
                    inv2 = await self.mod.get_inventory()
                    for slot in (inv2.get("hotbar", []) or []) + (inv2.get("inventory", []) or []):
                        if slot.get("id") == pid:
                            slot_id = slot.get("inv_slot", 0)
                            break
                if slot_id is None:
                    continue
                before_hp = int(self._state.get("hp", 0) or 0)
                await self.mod.select_item(slot_id)
                if not await self.mod.use_item_slot(slot_id):
                    continue
                # The command ACK only means the item was used.  Refresh the
                # state and require an actual HP increase before reporting a
                # successful autonomous heal.
                await asyncio.sleep(0.25)
                await self.refresh_state()
                after_hp = int(self._state.get("hp", 0) or 0)
                if after_hp <= before_hp:
                    continue
                self.log("喝了加血物品", "item")
                return True
            except Exception:
                continue
        return False

    async def use_item_on_self(self, name: str) -> bool:
        # 这是“使用身上已有物品”，不能用 give_item 生成一件新物品。
        # 生成物品会让喝药/使用道具的调用在背包没有该物品时也假成功，
        # 并且可能把物品凭空留在世界中。
        return await self.items.use_item_by_name(name)

    async def navigate_to(self, x: int, y: int, timeout: int = 25,
                          allow_build: bool = False) -> bool:
        """自动寻路走到坐标（v3.0: 流式导航——C# BFS 寻路 + 状态流，可中断）。

        导航途中遇敌 → 停下打怪 → 打完继续走（参考轮子插件）。
        """
        await self.capability.refresh()
        st = self._state
        cur_y = st.get("tile_y", 0)
        height_diff = cur_y - y
        if height_diff > 3 and self.capability.can_climb(height_diff):
            # A large vertical gap needs the segmented climb planner.  The
            # planner can choose a hook/rope route or place a small number of
            # real blocks when necessary; sending it through ordinary stream
            # navigation would silently disable pit recovery.
            self.log(f"落差{height_diff}格，尝试分段爬升", "nav")
            return await self.climb_to(x, y, allow_build=allow_build)
        if height_diff > 3 and not allow_build:
            self.log(f"落差{height_diff}格且没有可确认的攀爬手段，停止普通导航", "nav")
            return False

        ok = await self._nav_with_fight(x, y, timeout,
                                        allow_build=allow_build)
        self.log(f"走到 ({x},{y}) " + ("成功" if ok else "失败/超时"), "nav")
        return ok

    async def _nav_with_fight(self, x: int, y: int, timeout: int,
                              _tries: int = 0, allow_build: bool = False) -> bool:
        """带战斗守卫的导航：遇敌停下打，打完重新导航续走（最多 3 轮）。"""
        if _tries > 3:
            return False
        fought = {"yes": False}

        async def _guard():
            # 战斗中不自我嵌套（战斗内部的风筝移动也是 navigate_to）
            if self._in_combat:
                return False
            try:
                st = self.get_state()
            except Exception:
                return False
            target = self.combat._pick_target(
                st, int(st.get("tile_x", 0)), int(st.get("tile_y", 0)))
            if not target:
                return False
            fought["yes"] = True
            self.log(f"导航途中遇敌（{target.get('name', '敌人')}），先打再走", "combat")
            await self.mod.stop_actions()
            await self.combat.fight_nearest(st, timeout=6)
            return True  # 打断本次导航，由外层重发导航续走

        ok = await self.mod.navigate_async(x, y, timeout, on_tick=_guard,
                                            allow_build=allow_build)
        if ok:
            return True
        # 只有真遇敌打断才续走；导航本身失败（卡住/超时）不重复白等
        if fought["yes"]:
            return await self._nav_with_fight(
                x, y, timeout, _tries + 1, allow_build=allow_build)
        return False

    async def climb_to(self, x: int, y: int, _round: int = 1,
                       allow_build: bool = True) -> bool:
        """复杂垂直移动（深坑回地面）：先规划分段，再逐段执行"""
        if _round > 5:
            self.log("爬升重规划超过5轮，放弃", "warn")
            await self.send_chat("主人，我试了好几次都上不去，你来帮帮我吧~")
            return False
        start_y = self._state.get("tile_y", 0)
        plan = await self.planner.plan_climb(x, y)
        if not plan.legs:
            self.log("爬升规划失败：" + plan.blocked_reason, "warn")
            await self.send_chat(f"主人，{plan.blocked_reason}，我上不去，等你命令~")
            return False
        self.log(f"爬升计划（{len(plan.legs)}段）：{plan.describe()}", "nav")
        if not plan.feasible:
            await self.send_chat(f"主人，我先爬{len(plan.legs)}段试试，不过{plan.blocked_reason}，可能到不了顶~")

        for i, leg in enumerate(plan.legs):
            if leg.method == "dirt" and not allow_build:
                self.log("目标需要搭方块才能爬升；普通跟随不自动改造地形", "nav")
                return False
            if self.executor.should_stop():
                self.log("爬升被打断", "warn")
                return False
            self.log(f"第{i + 1}/{len(plan.legs)}段：{leg.method} → ({leg.tx},{leg.ty})", "nav")
            ok = await self._nav_with_fight(leg.tx, leg.ty, timeout=12,
                                            allow_build=allow_build)
            if not ok:
                await self.send_chat(f"主人，我卡在第{i + 1}段了（{leg.method}到 {leg.tx},{leg.ty}），上不去啦~")
                self.log(f"第{i + 1}段失败，中止爬升", "warn")
                return False
            await self.capability.refresh()

        if plan.feasible:
            self.log("爬升完成", "nav")
            return True

        await self.refresh_state()
        now_y = self._state.get("tile_y", 0)
        if start_y - now_y < 1:
            self.log("本轮没有上升，停止重规划", "warn")
            await self.send_chat("主人，我卡在这上不去了，等你想想办法~")
            return False
        return await self.climb_to(x, y, _round + 1, allow_build=allow_build)

    # --- 物品/箱子：实现在 InventoryOps，这里保留原有调用签名 ---
    async def get_inventory(self) -> Dict[str, Any]:
        return await self.refresh_inventory()

    async def refresh_inventory(self) -> Dict[str, Any]:
        """按需刷新背包缓存（C# 不推背包，需要时主动拉取）。"""
        self._inv_full = await self.mod.get_inventory()
        return self._inv_full

    def get_inventory_sync(self) -> Dict[str, Any]:
        return self._inv_full

    def locate_item(self, name: str) -> Dict[str, Any]:
        return self.items.locate_item(name)

    def count_item(self, name: str) -> int:
        return self.items.count_item(name)

    async def nearest_chest_with(self, name: str) -> Optional[Dict[str, Any]]:
        return await self.items.nearest_chest_with(name)

    async def store_to_chest(self, name: str, chest: Dict[str, Any], stack: int = 1) -> bool:
        return await self.items.store_to_chest(name, chest, stack)

    async def take_from_chest(self, name: str, chest: Dict[str, Any], stack: int = 1) -> bool:
        return await self.items.take_from_chest(name, chest, stack)

    async def hand_to_player(self, name: str, stack: int = 1) -> bool:
        return await self.items.hand_to_player(name, stack)

    async def use_item_by_name(self, name: str) -> bool:
        return await self.items.use_item_by_name(name)

    def get_log_sync(self) -> List[Dict[str, Any]]:
        return self._log

    def get_chests_sync(self) -> List[Dict[str, Any]]:
        return self._chests

    async def mine_then_fetch(self, ore: str, surface_item: str, ore_amount: int = 10) -> bool:
        # 深坑挖矿场景：先挖矿，再回地面拿某物，最后回到坑底玩家身边
        # 第1步：挖矿（在坑底进行）
        await self.submit_goal(
            Goal(goal_type="gather", target=ore, amount=ore_amount, reason="坑底挖矿", report_fail="挖不到矿石，主人")
        )
        # 第2步：回地面取物（用 navigate_to 会自动预判能力并汇报）
        chest = await self.nearest_chest_with(surface_item)
        if chest is None:
            chests = await self.mod.enum_chests()
            chest = chests[0] if chests else None
        if chest:
            if not await self.navigate_to(chest["x"], chest["y"]):
                await self.send_chat("回地面拿东西上不去，等我拿到钩锁/土块再说~")
                return False
            await self.take_from_chest(surface_item, chest)
        else:
            await self.send_chat("附近没有箱子，不知道去哪拿" + surface_item)
            return False
        # 第3步：回到玩家身边（坑底）
        st = self._state
        players = st.get("nearby_players", [])
        if players:
            me_x = int(st.get("tile_x", 0) or 0)
            me_y = int(st.get("tile_y", 0) or 0)
            mine = self._character_name()
            valid = []
            for player in players:
                if not isinstance(player, dict) or player.get("name") == mine:
                    continue
                x = int(player.get("tile_x", player.get("tileX", 0)) or 0)
                y = int(player.get("tile_y", player.get("tileY", 0)) or 0)
                if x == 0 and y == 0:
                    continue
                valid.append((x, y, (x - me_x) ** 2 + (y - me_y) ** 2))
            if valid:
                ox, oy, _ = min(valid, key=lambda item: item[2])
                await self.navigate_to(ox, oy)
        return True

    @property
    def running(self) -> bool:
        return self._running
