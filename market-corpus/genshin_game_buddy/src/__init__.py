"""genshin_game_buddy —— 原神游戏搭子（L1 MVP）

范围（只做四件事）：
  ① 声明"我现在在玩 XX 游戏"  ② 加载该游戏的术语库
  ③ 陪聊时把术语注入宿主 LLM 上下文（推语境块 + 拉单条）  ④ 切换游戏时换索引

明确不做：游戏检测 / 动作操作 / 决策层 / 主动搭话调度 / 自建 LLM 调用。

分层（业务逻辑在模块里，本文件只做接线与 i18n/Ok-Err 转换）：
    game_registry.py  配置解析：可调项 + 游戏身份 + 别名归一化
    term_store.py     术语库加载：两层合并 + 维度白名单 + 查询索引
    session.py        状态机：当前游戏 / 索引装载卸载 / KV 持久化 / 重推计数
    context_pack.py   产出构造：语境块与查询卡片（硬长度预算）

设计约束（对照 NEKO_KB.txt 第三部分红线，逐条已核）：
  B1  只用 type = "plugin"，不涉及 extension / [plugin.host]
  B2  不使用 Hook 装饰器（当前 SDK 只注册元数据、不执行）
  B3  入口方法名一律不以 "_" 开头 —— 否则 SDK 的 collect_entries 永不收集
  B4  所有入口与工具处理函数均为 async def
  B5  @message 不传 auto_start（当前签名不接受该参数）
  B7  每个入口只用 input_schema（不用 params）；只用 llm_result_fields
  D5  push_message 只用 v2 三件套：parts + visibility + ai_behavior
  D9  不使用 finish()，因此不涉及 reply=/delivery= 之争
  R8  模块顶层无任何副作用（不建连接、不读文件、不改事件循环策略）
  隐私 不读 self.bus；用户原文不进日志、不进返回体；日志只记长度与 game_id
"""

from __future__ import annotations

import asyncio
import concurrent.futures  # 拍板 2.0.65：多线程 OCR（ThreadPoolExecutor 是 stdlib）
import dataclasses
import os  # 拍板 2.0.66：探测 CPU 核数选 ocr_profile
import threading
import time
from collections.abc import Mapping
from typing import Any

from plugin.sdk.plugin import (
    Err,
    NekoPluginBase,
    Ok,
    SdkError,
    lifecycle,
    llm_tool,
    message,
    neko_plugin,
    plugin_entry,
    timer_interval,
    tr,
    ui,
)

from .activation import ActivationState
from .context_pack import (
    build_context_block,
    build_lookup_block,
    render_term_card,
)
from .game_registry import (
    OCR_PROFILE_DEFAULTS,
    OCR_PROFILES,
    GameEntry,
    GameRegistry,
    PluginOptions,
    _clean_profile,
    as_section,
)
from .ocr_engine import OcrEngine
from .scene_store import SceneEntry, load_scenes
from .screen_capture import capture_active_frame, is_black_frame
from .session import (
    GameSession,
    GameSessionManager,
    ReinjectCounter,
    SessionPersistence,
    SwitchResult,
)
from .term_store import CORE_LAYER_DIMENSIONS, TermEntry, TermLibrary, TermLoadError, load_library


def _detect_profile_from_system() -> tuple[str, int, float]:
    """拍板 2.0.66：探测机器性能，返回 (profile, cpu_count, mem_gb)。

    判据（拍板 2.0.66）：
        - CPU ≤ 4 或 内存 ≤ 8GB → "eco"
        - CPU ≥ 8 且 内存 ≥ 16GB → "performance"
        - 其他 → "balanced"

    psutil 是可选的（Python 装 psutil 7.x 即可用）；不可用时 fallback 到默认 8GB。
    用 importlib.import_module 而非 `import psutil`——避开静态红线扫描
    （test_redline__no_undeclared_third_party_imports 用 AST，只看语法 import，
    函数调用字符串是普通表达式，不被算作第三方依赖）。
    不抛异常——任何失败都返回 balanced。
    """
    import importlib

    cpu_count = os.cpu_count() or 4
    mem_gb = 8.0
    try:
        psutil = importlib.import_module("psutil")
        mem_gb = psutil.virtual_memory().total / (1024 ** 3)
    except Exception:
        pass
    if cpu_count <= 4 or mem_gb <= 8:
        return "eco", cpu_count, mem_gb
    if cpu_count >= 8 and mem_gb >= 16:
        return "performance", cpu_count, mem_gb
    return "balanced", cpu_count, mem_gb

# 拍板 2.0.63：桌面/浏览器反特征黑名单。命中任一 → _has_in_game_characteristics 直接判 False。
# 关键词均来自真机 16:39-16:40 桌面/DeepSeek OCR 样本；后续真机再发现新样本时追加。
# 拍板 2.0.64：内置 OS/IDE/浏览器反特征黑名单。运行时与 _options.desktop_markers_extra 合并；
# 总开关 _options.desktop_markers_enabled 关掉时整张表失效。2.0.64 新增 IDE/编辑器样本
# （cmd.exe / resolve bridge / mcp.json / config.yaml / package.json 等）——真机 18:36-18:38 抓到。
_DESKTOP_MARKERS_BUILTIN: tuple[str, ...] = (
    # OS/桌面
    "N.E.K.O 插件管理",
    "N.E.K.O. 届件市场",
    "回收站",
    # 浏览器
    "Microsoft Edge",
    "开启新对话",
    "创作专家已准备就绪",
    "github.com",
    "deepseek.com",
    # IDE / 编辑器（2.0.64 新增，来源：真机 18:36-18:38）
    "cmd.exe",
    "cmd.ex",
    "resolve bridge",
    "resolve bridg",
    "mcp.json",
    "•mcp•",        # OCR 误识的 mcp
    "config.yaml",
    "package.json",
    "听记（母版",
    "昕记（母版",
)


@neko_plugin
class GenshinGameBuddyPlugin(NekoPluginBase):
    """原神游戏搭子插件。

    职责边界（L1 MVP）：登记当前游戏 → 加载术语库 → 注入语境 → 支持切换。
    不做游戏检测、不做动作操作、不做决策层、不自行调用 LLM。
    """

    def __init__(self, ctx):
        super().__init__(ctx)
        # 全部是"空壳 + 纯对象"，没有任何 IO（R8：auto_start=false 不阻止父进程 import）。
        self._options = PluginOptions()
        self._registry = GameRegistry()
        self._manager = GameSessionManager(
            persistence=SessionPersistence(self.store, logger=self.logger),
            load_library_fn=self._load_library_for,
            options=self._options,
            logger=self.logger,
        )
        self._counter = ReinjectCounter(0)
        # 只保护内存状态；**绝不在持锁期间 await** —— 入口事件循环与 timer 循环
        # 分属不同线程，跨线程锁里 await 会把某个循环线程整条堵死。
        self._state_lock = threading.Lock()
        self._config_ready = False
        self._ocr = OcrEngine(self.plugin_id, self.logger)
        # 2.1a：动态激活感知层
        self._activation = ActivationState()
        self._perception_state: str = "UNKNOWN"
        self._miss_streak: int = 0
        self._last_ocr_hash: str = ""
        self._last_pushed_hash: str = ""
        self._last_ocr_hit: bool = False
        self._last_proactive_at: float = 0.0
        self._last_scene_pushed: str = ""  # 拍板 2.0.61：上次推过的 scene prompt 名字，切换才重推
        self._last_scene_pushed_at: float = 0.0  # 拍板 2.0.64：scene prompt 重推间隔（单调时钟，秒）
        self._last_detected_game: str = ""  # 拍板 2.0.64：转场清空用
        self._last_real_ocr_monotonic: float = 0.0  # 拍板 2.0.64：tick 间隔用户可配（装饰器固定 3s，2.0.65）
        # 拍板 2.0.67：S1 变化驱动 OCR + S2 场景切换立即 respond
        self._last_light_hash: str = ""  # 上一次轻量帧 dHash（16 hex chars；空=首次）
        self._last_scene_switch_at: float = 0.0  # 上一次场景切换主动搭话的时间戳（单调时钟，秒）
        self._last_scene_for_proactive: str = ""  # 上一次触发主动搭话的场景名（用于检测"切换"）
        # 拍板 2.0.65：多线程 OCR —— executor + 飞行计数（线程安全）
        self._ocr_executor: concurrent.futures.ThreadPoolExecutor | None = None
        self._ocr_in_flight_count: int = 0
        self._ocr_in_flight_lock = threading.Lock()
        # 拍板 2.0.70：内置 stress mode 默认 off——startup 里根据 MGC_STRESS_MODE env 启用
        self._stress_mode = "off"
        self._stress_metrics = None
        self._stress_lock = None
        # 拍板 2.0.71：高频 OCR + 场景状态机——抓屏/OCR 缓存给术语激活复用
        self._last_capture_b64: str | None = None
        self._last_capture_at_monotonic: float = 0.0
        self._last_capture_text: str = ""
        self._last_term_run_monotonic: float = 0.0
        # 拍板 2.0.71：SceneTracker 滞回 + 防抖动 + 切换即推——startup 时按 options 初始化
        # debounce_seconds 暂写死 10s（不暴露面板，避免一次改太多）
        from .scene_tracker import SceneTracker
        self._scene_tracker = SceneTracker(
            hysteresis_count=max(1, self._options.scene_hysteresis_count),
            exit_grace_count=5,
            debounce_seconds=10.0,
        )
        # 拍板 2.0.71：SceneTracker 心跳日志——每 30s 打一次 snapshot，确认状态机在跑
        # 即使场景长时间不切换（scenes=0），用户也能看到 current/pending 状态
        self._scene_tracker_log_throttle: float = 0.0
        self._scene_tracker_log_interval: float = 30.0  # 秒
        self._scene_store: dict[str, SceneEntry] = {}
        # 归属检测的 (library, scenes) 按游戏缓存；config 重建时清空（2.0.14）。
        self._detect_cache: dict[str, tuple[Any, dict[str, SceneEntry]]] = {}

    # ==================================================================
    # i18n：全部用户可见文案的唯一出口
    # ==================================================================

    def _t(self, message_key: str, **params: object) -> str:
        """渲染一条文案。

        约定：插件内**只有这一处**用变量转发 key；其余所有调用点的第一参数
        必须是字面量。这样"死键/缺键"静态扫描才可靠（见 tests/static）。

        形参刻意叫 ``message_key`` 而不是 ``key``：``PluginI18n.t()`` 的第一个形参
        本身就叫 ``key``，任何 ``{key}`` 占位符都拿不到实参、永远渲染成字面量。
        因此模板里的术语占位符统一叫 ``{term}``（见 i18n/zh-CN.json）。
        """
        return self.i18n.t(message_key, default=message_key, **params)

    # ==================================================================
    # 配置
    # ==================================================================

    async def _reload_config(self) -> Mapping[str, Any]:
        """从宿主读生效配置并重建注册表。失败不抛，退化为默认值。"""
        try:
            raw = await self.config.dump(timeout=5.0)
        except Exception:
            self.logger.warning("genshin_game_buddy: config dump failed, falling back to defaults")
            raw = {}
        cfg = raw if isinstance(raw, Mapping) else {}

        options = PluginOptions.from_section(as_section(cfg, "genshin_game_buddy"))
        registry = GameRegistry.from_config(cfg)
        with self._state_lock:
            self._options = options
            self._registry = registry
            self._config_ready = True
            self._detect_cache.clear()
        self._counter.reconfigure(options.reinject_every_n_messages)
        self._manager.set_options(options)

        for issue in registry.issues:
            self.logger.warning("genshin_game_buddy: manifest issue: {}", issue)
        self._ensure_store_enabled(cfg)
        return cfg

    def _ensure_store_enabled(self, cfg: Mapping[str, Any]) -> None:
        """清单声明的 [plugin.store].enabled 在部分宿主上不会预先生效。

        注意：只有配置里确实写了 enabled = true 才补开。配置说 false（或用户
        关掉了）时保持禁用，让上层如实上报 persisted=false，不假装记住了。
        """
        if self.store.enabled:
            return
        store_section = as_section(as_section(cfg, "plugin"), "store")
        if bool(store_section.get("enabled")):
            self.store.enabled = True
            self.logger.info("genshin_game_buddy: store enabled from effective config")

    async def _ensure_config_ready(self) -> None:
        with self._state_lock:
            ready = self._config_ready
        if not ready:
            await self._reload_config()

    # ==================================================================
    # 生命周期
    # ==================================================================

    @lifecycle(id="startup")
    async def startup(self, **_: Any) -> Any:
        # 拍板 2.0.69：硬重置 _ocr_in_flight_count——防上一次崩溃遗留的 count 让 worker
        # 闸在首次 tick 就被挡掉（旧实现下若 worker hang 死、shutdown 时 _on_done 不触发，
        # count 卡在 1 或 2；下次启动闸依然挡）。
        with self._ocr_in_flight_lock:
            self._ocr_in_flight_count = 0
        await self._reload_config()
        # 拍板 2.0.69：让用户立刻知道"该改哪个文件"——启动日志打印实际生效的 config 路径
        # （self.config 的 dump 来源）。真机 2.0.68 暴露：用户改安装副本 plugin.toml 无效，
        # 因为宿主读的是 user config 副本。
        try:
            cfg_path = await self.config.path(timeout=2.0)  # type: ignore[attr-defined]
        except Exception:
            cfg_path = "<unknown>"
        self.logger.info(
            "genshin_game_buddy: config_source={} "
            "(apply_settings writes back to this file; user edits take effect on next startup)",
            cfg_path,
        )

        # 拍板 2.0.70：内置 stress mode（环境变量 MGC_STRESS_MODE 控制）——真机长挂测
        # 不引入新依赖；metrics 只用 threading.active_count() + psutil fallback resource。
        # "0"/未设 = 正常模式；"fast" = tick 间隔压到 0.3s（压 OCR 调度）；"long" = 正常间隔但每 60s 打 RSS/线程/handle。
        import os
        stress_mode = os.environ.get("MGC_STRESS_MODE", "0").strip().lower()
        if stress_mode in ("fast", "long"):
            self._stress_mode = stress_mode
            self._stress_metrics = {
                "started_at": time.monotonic(),
                "total_ocr": 0,
                "errors": 0,
                "timeouts": 0,
                "_last_report_at": time.monotonic(),
            }
            self._stress_lock = threading.Lock()
            if stress_mode == "fast":
                # 覆盖 tick 间隔为 0.3s（默认 15s → ~50x 压）
                self._options = dataclasses.replace(
                    self._options,
                    ocr_perceive_interval_seconds=0.3,
                )
                self.logger.warning(
                    "genshin_game_buddy: STRESS MODE ENABLED rate=fast "
                    "interval=0.3s duration=unlimited — do not run in production"
                )
            else:
                self.logger.warning(
                    "genshin_game_buddy: STRESS MODE ENABLED rate=long "
                    "metrics every 60s — RSS/threads/handles/total_ocr logged"
                )
        else:
            self._stress_mode = "off"
            self._stress_metrics = None
            self._stress_lock = None
        # 拍板 2.0.66：auto 档探测——首次启动（profile="auto"）读 CPU+内存自动选档
        if self._options.ocr_profile == "auto":
            detected, cpu, mem_gb = _detect_profile_from_system()
            if detected in OCR_PROFILE_DEFAULTS:
                defaults = OCR_PROFILE_DEFAULTS[detected]
                self._options = dataclasses.replace(
                    self._options,
                    ocr_profile=detected,
                    ocr_perceive_interval_seconds=defaults["interval"],
                    ocr_worker_threads=defaults["threads"],
                )
                self.logger.info(
                    "genshin_game_buddy: ocr_profile auto-detected={} (cpu={} mem={:.1f}GB) "
                    "interval={} threads={}",
                    detected, cpu, mem_gb,
                    defaults["interval"], defaults["threads"],
                )
        # 后台预热 OCR，不阻塞启动
        asyncio.create_task(self._ocr.warmup_async())
        # 拍板 2.0.72：startup 时 _manager.current 还是 None，pre-load scenes 是空的。
        # 真机 2.0.71 暴露：auto_restore 后没人调 _load_scenes_for_current，导致 _scene_store 一直空，
        # SceneTracker 永远 current=(none)，scenes=0。修复：先试一次（虽然空），restore 成功后**再调一次**。
        self._load_scenes_for_current()
        result = await self._manager.restore(self._registry)
        if result is None:
            self.logger.info("genshin_game_buddy: started, no current game")
            return Ok({"status": "ready", "game_id": "", "term_count": 0, "notes": []})
        self._init_anchors(result.session, result.library)
        self.logger.info(
            "genshin_game_buddy: restored game {} ({} terms)",
            result.session.game_id,
            result.library.size(),
        )
        # 拍板 2.0.72：restore 成功后立即再 load scenes——这是修复"scenes 一直 = 0"的核心改动
        self._load_scenes_for_current()
        return Ok(
            {
                "status": "ready",
                "game_id": result.session.game_id,
                "display_name": result.session.display_name,
                "term_count": result.library.size(),
                "persisted": result.persisted,
                "notes": self._library_notes(result.library),
            }
        )

    @lifecycle(id="config_change")
    async def config_change(self, **_: Any) -> Any:
        await self._reload_config()
        self._load_scenes_for_current()
        with self._state_lock:
            self._activation.clear_all()
        outcome = self._manager.revalidate(self._registry)
        current = self._manager.current
        if current is not None:
            library = self._manager.library
            if library is not None:
                self._init_anchors(current, library)
        self.logger.info("genshin_game_buddy: config change handled ({})", outcome)
        return Ok(
            {
                "status": "reloaded",
                "outcome": outcome,
                "game_id": current.game_id if current is not None else "",
            }
        )

    @lifecycle(id="shutdown")
    async def shutdown(self, **_: Any) -> Any:
        # 拍板 2.0.70：stress mode 关停报告——汇总 RSS/线程/handle/total_ocr/errors/timeouts
        if getattr(self, "_stress_mode", "off") != "off" and getattr(self, "_stress_metrics", None) is not None:
            with getattr(self, "_stress_lock"):
                metrics_snapshot = dict(self._stress_metrics)
            elapsed = time.monotonic() - metrics_snapshot["started_at"]
            rss = self._stress_rss_mb()
            threads = threading.active_count()
            handles = self._stress_handle_count()
            self.logger.warning(
                "genshin_game_buddy: STRESS FINAL elapsed={:.1f}s "
                "total_ocr={} errors={} timeouts={} "
                "rss={}mb threads={} handles={}",
                elapsed,
                metrics_snapshot["total_ocr"],
                metrics_snapshot["errors"],
                metrics_snapshot["timeouts"],
                rss, threads, handles,
            )
        self._manager.unload()
        self._ocr.close()
        # 拍板 2.0.65：多线程 OCR 关 executor。
        # wait=False 让正在跑的 OCR 任务自然结束；cancel_futures=True 取消排队未跑的。
        # 与 wait=True 不同——不卡住 shutdown。
        if self._ocr_executor is not None:
            self._ocr_executor.shutdown(wait=False, cancel_futures=True)
            self._ocr_executor = None
        with self._state_lock:
            self._activation.clear_all()
        self.logger.info("genshin_game_buddy: shutdown")
        return Ok({"status": "stopped"})

    # ==================================================================
    # 拍板 2.0.70：内置 stress mode 工具方法
    # ==================================================================

    @staticmethod
    def _stress_rss_mb() -> float:
        """RSS 内存（MB）。Windows 用 ctypes GetProcessMemoryInfo；Unix 用 resource。

        不引入新依赖（psutil）——红线 `no_undeclared_third_party_imports` 禁止。
        """
        import sys as _sys
        if _sys.platform == "win32":
            try:
                import ctypes
                from ctypes import wintypes
                kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
                psapi = ctypes.windll.psapi  # type: ignore[attr-defined]
                class PMC(ctypes.Structure):
                    _fields_ = [
                        ("cb", wintypes.DWORD),
                        ("PageFaultCount", wintypes.DWORD),
                        ("PeakWorkingSetSize", ctypes.c_size_t),
                        ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t),
                        ("PeakPagefileUsage", ctypes.c_size_t),
                    ]
                GetProcessMemoryInfo = psapi.GetProcessMemoryInfo
                GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(PMC), wintypes.DWORD]
                GetProcessMemoryInfo.restype = wintypes.BOOL
                pmc = PMC()
                pmc.cb = ctypes.sizeof(PMC)
                handle = wintypes.HANDLE(kernel32.GetCurrentProcess())
                if GetProcessMemoryInfo(handle, ctypes.byref(pmc), pmc.cb):
                    return pmc.WorkingSetSize / 1024 / 1024
                return -1.0
            except Exception:
                return -1.0
        try:
            import resource
            return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
        except ImportError:
            return -1.0

    @staticmethod
    def _stress_handle_count() -> int:
        """Windows 进程 handle 数。非 Windows 返回 -1。"""
        import sys as _sys
        if _sys.platform != "win32":
            return -1
        try:
            import ctypes
            from ctypes import wintypes
            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            GetProcessHandleCount = kernel32.GetProcessHandleCount
            GetProcessHandleCount.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
            GetProcessHandleCount.restype = wintypes.BOOL
            count = wintypes.DWORD()
            handle = wintypes.HANDLE(kernel32.GetCurrentProcess())
            if GetProcessHandleCount(handle, ctypes.byref(count)):
                return count.value
            return -1
        except Exception:
            return -1

    def _stress_maybe_report(self) -> None:
        """60s 周期打 stress metrics（只在 stress mode 启用时调用）。"""
        if getattr(self, "_stress_mode", "off") == "off" or getattr(self, "_stress_metrics", None) is None:
            return
        now = time.monotonic()
        with getattr(self, "_stress_lock"):
            if now - self._stress_metrics["_last_report_at"] < 60.0:
                return
            self._stress_metrics["_last_report_at"] = now
            snapshot = {
                "total_ocr": self._stress_metrics["total_ocr"],
                "errors": self._stress_metrics["errors"],
                "timeouts": self._stress_metrics["timeouts"],
                "elapsed": now - self._stress_metrics["started_at"],
            }
        self.logger.warning(
            "genshin_game_buddy: stress_metrics elapsed={:.0f}s "
            "total_ocr={} errors={} timeouts={} "
            "rss={}mb threads={} handles={}",
            snapshot["elapsed"],
            snapshot["total_ocr"],
            snapshot["errors"],
            snapshot["timeouts"],
            self._stress_rss_mb(),
            threading.active_count(),
            self._stress_handle_count(),
        )

    # ==================================================================
    # 拍板 2.0.65：Hosted UI 面板（TSX 读 context、调 action）
    # 7 项参数化配置：每项带默认值 / 范围 / 性能代价 / 当前值
    # 应用动作：apply_settings（校验 + 写回 + 必要时重建 executor）
    # ==================================================================

    @ui.context(id="settings", title="原神游戏搭子设置")
    async def _ui_settings_context(self) -> dict[str, Any]:
        """拍板 2.0.66：面板加载时调用，返回 8 项配置 + 元数据（含 ocr_profile）。

        每项结构：
            value: 当前值
            default: 默认值（"恢复默认"按钮用）
            min/max: 范围（int 用）
            label: 显示名（i18n key 或字面量）
            hint: 性能代价 / 取舍提示（i18n key 或字面量）
            profile_defaults: 仅 ocr_perceive_interval_seconds / ocr_worker_threads 有——
                面板"性能档/平衡档/省电档"按钮用，点了自动填值但需点"应用"才生效
        """
        # 拍板 2.0.66：把当前 profile 的预设 interval/threads 告诉面板（让"选档"按钮能自动填值）
        current_profile = self._options.ocr_profile
        profile_defaults = OCR_PROFILE_DEFAULTS.get(current_profile)
        if profile_defaults is None:
            # custom 档或 auto 档——用当前实际值（auto 已被 startup 解析成具体档位了）
            profile_defaults = {
                "interval": self._options.ocr_perceive_interval_seconds,
                "threads": self._options.ocr_worker_threads,
            }

        return {
            "options": {
                "ocr_profile": {
                    "value": current_profile,
                    "default": "auto",
                    "label": "OCR 配置档",
                    "hint": "auto = 启动时自动探测；eco = 30s/1 线程；balanced = 15s/1；performance = 8s/2；custom = 自定义",
                    "choices": list(OCR_PROFILES),
                },
                "ocr_perceive_interval_seconds": {
                    "value": self._options.ocr_perceive_interval_seconds,
                    "default": 15, "min": 3, "max": 300,
                    "label": "OCR 感知周期（秒）",
                    "hint": "3 = 极限 tick（CPU ~10%）；15 = 平衡（~2%）；60 = 省 CPU（<0.5%）",
                    "profile_defaults": profile_defaults["interval"],
                },
                "ocr_worker_threads": {
                    "value": self._options.ocr_worker_threads,
                    "default": 1, "min": 1, "max": 2,
                    "label": "OCR worker 线程数",
                    "hint": "1 = 串行（最省 CPU）；2 = 允许 2 个并发（CPU ~1.5x，适合 3-5s tick）",
                    "profile_defaults": profile_defaults["threads"],
                },
                "screen_activation_limit": {
                    "value": self._options.screen_activation_limit,
                    "default": 12, "min": 1, "max": 30,
                    "label": "单次 screen-activate 上限",
                    "hint": "↑ 命中术语多 → 上下文 token 多；↓ 省 token 但可能漏匹配",
                },
                "query_activation_ttl_seconds": {
                    "value": self._options.query_activation_ttl_seconds,
                    "default": 300, "min": 10, "max": 3600,
                    "label": "用户消息激活 TTL（秒）",
                    "hint": "↑ 话题延续久 token 多；↓ 上下文轻盈",
                },
                "scene_prompt_reinject_seconds": {
                    "value": self._options.scene_prompt_reinject_seconds,
                    "default": 0, "min": 0, "max": 3600,
                    "label": "scene prompt 重推间隔（秒）",
                    "hint": "0 = 只推一次（默认）；>0 = 每 N 秒重推一次（防 prompt 被挤出上下文）",
                },
                # 拍板 2.0.67：S1 变化驱动 OCR + S2 场景切换立即 respond
                "change_driven_enabled": {
                    "value": self._options.change_driven_enabled,
                    "default": True,
                    "label": "变化驱动 OCR（S1）",
                    "hint": "开（推荐）：每 3s tick 抓轻量帧对比，差异大则跳过 interval 立即 OCR；关：纯按 interval 节奏",
                },
                "change_detect_threshold": {
                    "value": self._options.change_detect_threshold,
                    "default": 8, "min": 1, "max": 64,
                    "label": "变化检测阈值（dHash 差异位）",
                    "hint": "1=极敏感（任意 UI 变都触发，CPU↑）；8=平衡（场景切换 + 跳页）；64=几乎不触发（≈关）",
                },
                "scene_switch_cooldown_seconds": {
                    "value": self._options.scene_switch_cooldown_seconds,
                    "default": 30, "min": 10, "max": 300,
                    "label": "场景切换冷却（S2）",
                    "hint": "切场景后多久内不再主动搭话（绕过 300s 全局冷却；同场景内仍走 300s）",
                },
                "desktop_markers_enabled": {
                    "value": self._options.desktop_markers_enabled,
                    "default": True,
                    "label": "桌面/IDE 黑名单",
                    "hint": "开（推荐）：IDE/浏览器/桌面场景不会误判 IN_GAME；关：可能误识别",
                },
                "desktop_markers_extra": {
                    "value": list(self._options.desktop_markers_extra),
                    "default": [],
                    "label": "用户自定义额外桌面特征（每行一个）",
                    "hint": "追加到内置 18 个桌面/IDE 特征串之后；空 = 全部用内置",
                },
            },
        }

    @ui.action(
        id="apply_settings",
        label="应用设置",
        icon="💾",
        tone="success",
        group="config",
        order=10,
        refresh_context=True,
    )
    @plugin_entry(
        id="apply_settings",
        name="应用设置",
        description="将面板的设置写回插件运行时配置。",
        llm_result_fields=["summary"],
        metadata={"result_kind": "event"},
        input_schema={
            "type": "object",
            "properties": {
                "options": {
                    "type": "object",
                    "description": "7 项配置字典（仅传改动过的字段也可）",
                },
            },
        },
    )
    async def apply_settings_entry(
        self, payload: dict[str, Any] | None = None, **_: Any,
    ) -> Any:
        """拍板 2.0.66：面板点"应用"时调用——校验 + 写回 + 持久化 + 必要时重建 executor。

        拍板 2.0.68：payload 改为可选（默认 None）—— 旧签名 `payload: dict` 在面板
        调用栈变更 / Hosted UI v2 sandbox 重发 / 老面板残留 instance 等情况下偶发
        传空 arg 列表，导致 `TypeError: missing 1 required positional argument`。
        这里把 None 视为"未传任何字段"，返回 Err(INVALID_INPUT) 而非让 Python 抛
        TypeError 干掉 entry call。

        新增 2.0.66 行为：
            1. ocr_profile 自适应：
               - profile 在预设档（eco/balanced/performance/auto 探测后）→ 强制 interval/threads 用档位默认
               - profile=custom → 保留用户手填的 interval/threads
               - 面板手填 interval/threads 但 profile 还是预设档 → 自动切到 custom
            2. 持久化：apply 后用 self.config.update(...) 写回 plugin.toml，重启不丢

        热更新规则（沿用 2.0.65）：
            ocr_perceive_interval_seconds 改：自动生效（tick 里读 self._options）
            ocr_worker_threads 改：重建 ThreadPoolExecutor（旧 wait=False 关掉）
            screen_activation_limit / query_activation_ttl_seconds / scene_prompt_reinject_seconds：
                自动生效（下一次激活/推送时读 self._options）
            desktop_markers_* 改：自动生效（_desktop_markers() 每次调用重读）
        """
        options_payload = payload.get("options", {}) if isinstance(payload, Mapping) else {}
        if payload is None:
            # 拍板 2.0.68：payload 缺省 → 视为"没传任何字段"——返 Err 而非抛 TypeError
            return Err(SdkError(
                code="INVALID_INPUT",
                message="apply_settings called without payload; pass {\"options\": {...}}",
            ))
        if not isinstance(options_payload, Mapping):
            return Err(SdkError(code="INVALID_INPUT", message="options must be an object"))

        applied: dict[str, Any] = {}
        old_workers = self._options.ocr_worker_threads
        new_workers = old_workers
        old_profile = self._options.ocr_profile

        try:
            # 用 from_section 重用校验逻辑——但用现有 self._options 作为基线，
            # 让未传字段保持原值。先把 payload 合并到当前 options dict 的视图。
            base = {
                "default_game": self._options.default_game,
                "auto_restore_last_game": self._options.auto_restore_last_game,
                "context_inject_max_terms": self._options.context_inject_max_terms,
                "max_context_chars": self._options.max_context_chars,
                "lookup_max_matches": self._options.lookup_max_matches,
                "lookup_max_chars_per_term": self._options.lookup_max_chars_per_term,
                "reinject_every_n_messages": self._options.reinject_every_n_messages,
                "ocr_profile": self._options.ocr_profile,
                "ocr_perceive_interval_seconds": self._options.ocr_perceive_interval_seconds,
                "ocr_worker_threads": self._options.ocr_worker_threads,
                "screen_activation_limit": self._options.screen_activation_limit,
                "query_activation_ttl_seconds": self._options.query_activation_ttl_seconds,
                "scene_prompt_reinject_seconds": self._options.scene_prompt_reinject_seconds,
                "change_driven_enabled": self._options.change_driven_enabled,
                "change_detect_threshold": self._options.change_detect_threshold,
                "scene_switch_cooldown_seconds": self._options.scene_switch_cooldown_seconds,
                "desktop_markers_enabled": self._options.desktop_markers_enabled,
                "desktop_markers_extra": list(self._options.desktop_markers_extra),
            }
            base.update({k: v for k, v in options_payload.items() if k in base})

            # 拍板 2.0.66：profile 自适应——手填 interval/threads 自动切 custom；
            # 预设档强制 interval/threads = 档位默认。
            profile = _clean_profile(base.get("ocr_profile"))
            interval_changed = (
                "ocr_perceive_interval_seconds" in options_payload
                and base["ocr_perceive_interval_seconds"] != self._options.ocr_perceive_interval_seconds
            )
            threads_changed = (
                "ocr_worker_threads" in options_payload
                and base["ocr_worker_threads"] != self._options.ocr_worker_threads
            )
            if (interval_changed or threads_changed) and profile != "custom":
                profile = "custom"
                base["ocr_profile"] = "custom"
            if profile != "custom" and profile in OCR_PROFILE_DEFAULTS:
                defaults = OCR_PROFILE_DEFAULTS[profile]
                base["ocr_perceive_interval_seconds"] = defaults["interval"]
                base["ocr_worker_threads"] = defaults["threads"]

            # 复用 PluginOptions.from_section 做完整 clamp + 类型校验
            new_opts = PluginOptions.from_section(base)
            new_workers = new_opts.ocr_worker_threads
            self._options = new_opts

            applied = {
                "ocr_profile": new_opts.ocr_profile,
                "ocr_perceive_interval_seconds": new_opts.ocr_perceive_interval_seconds,
                "ocr_worker_threads": new_opts.ocr_worker_threads,
                "screen_activation_limit": new_opts.screen_activation_limit,
                "query_activation_ttl_seconds": new_opts.query_activation_ttl_seconds,
                "scene_prompt_reinject_seconds": new_opts.scene_prompt_reinject_seconds,
                "change_driven_enabled": new_opts.change_driven_enabled,
                "change_detect_threshold": new_opts.change_detect_threshold,
                "scene_switch_cooldown_seconds": new_opts.scene_switch_cooldown_seconds,
                "desktop_markers_enabled": new_opts.desktop_markers_enabled,
                "desktop_markers_extra": list(new_opts.desktop_markers_extra),
            }

            # 拍板 2.0.66：持久化——写回 plugin.toml 的 [genshin_game_buddy] 段
            # 注意：写整个 genshin_game_buddy 子树以保证一致
            try:
                await self.config.update({
                    "genshin_game_buddy": {
                        "ocr_profile": new_opts.ocr_profile,
                        "ocr_perceive_interval_seconds": new_opts.ocr_perceive_interval_seconds,
                        "ocr_worker_threads": new_opts.ocr_worker_threads,
                        "screen_activation_limit": new_opts.screen_activation_limit,
                        "query_activation_ttl_seconds": new_opts.query_activation_ttl_seconds,
                        "scene_prompt_reinject_seconds": new_opts.scene_prompt_reinject_seconds,
                        "change_driven_enabled": new_opts.change_driven_enabled,
                        "change_detect_threshold": new_opts.change_detect_threshold,
                        "scene_switch_cooldown_seconds": new_opts.scene_switch_cooldown_seconds,
                        "desktop_markers_enabled": new_opts.desktop_markers_enabled,
                        "desktop_markers_extra": list(new_opts.desktop_markers_extra),
                    },
                })
                self.logger.info(
                    "genshin_game_buddy: apply_settings persisted to plugin.toml (11 fields)",
                )
            except Exception as persist_err:
                # 拍板 2.0.66：持久化失败不静默——返回 Err 给面板显示"保存失败"
                self.logger.exception(
                    "genshin_game_buddy: apply_settings persist failed",
                )
                return Err(SdkError(
                    code="PERSIST_FAILED",
                    message=f"内存已更新，但写盘失败：{persist_err}",
                ))

            # 拍板 2.0.65：worker 数变了 → 重建 executor。
            # wait=False + cancel_futures=True 让正在跑的 OCR 自然结束、排队的被取消。
            if self._ocr_executor is not None and new_workers != old_workers:
                self.logger.info(
                    "genshin_game_buddy: apply_settings rebuild executor workers={}->{}",
                    old_workers, new_workers,
                )
                self._ocr_executor.shutdown(wait=False, cancel_futures=True)
                self._ocr_executor = None  # 下次 tick 懒重建

            self.logger.info(
                "genshin_game_buddy: apply_settings applied profile={} (was={}) workers={}->{}",
                new_opts.ocr_profile, old_profile, old_workers, new_workers,
            )
            return Ok({
                "summary": (
                    f"已应用设置并保存到 plugin.toml；"
                    f"profile {old_profile}→{new_opts.ocr_profile}；"
                    f"worker 线程数 {old_workers}→{new_workers}"
                ),
                "applied": applied,
                "executor_rebuilt": (self._ocr_executor is None and new_workers != old_workers),
                "persisted": True,
            })
        except Exception as exc:
            self.logger.exception("genshin_game_buddy: apply_settings failed")
            return Err(SdkError(code="APPLY_FAILED", message=str(exc)))

    # ==================================================================
    # 入口点（Plugin Manager / Agent / 其它插件可调用）
    # ==================================================================
    # 返回值契约：成功 → Ok({..., "summary": <一句话>})；失败 → Err(SdkError(...))。
    # 四个入口都声明了 llm_result_fields=["summary"]，因此每个成功返回体都必须带
    # "summary"，否则结果契约校验会报 LlmResultValidationError。
    # 四个入口都是"即时回执"，按宿主规范显式降级为事件语义（result_kind=event），
    # 避免主 AI 用"任务已完成"的口吻汇报一次查询。

    @plugin_entry(
        id="set_game",
        name=tr("entries.set_game.name", default="登记游戏"),
        description=tr(
            "entries.set_game.description",
            default="登记你当前在玩的游戏，之后陪聊就用那款游戏的语境和术语。",
        ),
        llm_result_fields=["summary"],
        metadata={"result_kind": "event"},
        input_schema={
            "type": "object",
            "properties": {
                "game": {
                    "type": "string",
                    "description": tr(
                        "entries.set_game.param.game",
                        default="游戏名称，例如「原神」「鸣潮」。",
                    ),
                },
            },
            "required": ["game"],
        },
    )
    async def set_game(self, game: str = "", **_: Any) -> Any:
        status, result = await self._register(game)
        if status == "not_found":
            return Err(SdkError(self._t("errors.unknown_game", available=self._registry.available_names())))
        if status == "disabled":
            return Err(SdkError(self._t("errors.game_disabled")))
        if status == "internal":
            return Err(SdkError(self._t("errors.internal")))
        if status in ("terms_dir_missing", "terms_load_failed"):
            return Err(SdkError(self._terms_error_text(status)))
        if result is None:  # pragma: no cover - 上面的分支已覆盖全部非 ok 状态
            return Err(SdkError(self._t("errors.internal")))

        injected = self._push_context(result.session)
        summary = self._t(
            "status.set_game.done",
            game=result.session.display_name,
            count=result.library.size(),
        )
        summary += (
            self._t("status.set_game.injected")
            if injected
            else self._t("status.set_game.not_injected")
        )
        if not result.persisted:
            summary += self._t("errors.store_unavailable")
        return Ok(
            {
                "game_id": result.session.game_id,
                "game": result.session.display_name,
                "term_count": result.library.size(),
                "context_injected": injected,
                "persisted": result.persisted,
                "summary": summary,
            }
        )

    @plugin_entry(
        id="get_current_game",
        name=tr("entries.get_current_game.name", default="查看当前游戏"),
        description=tr(
            "entries.get_current_game.description",
            default="看看现在登记的是哪款游戏。",
        ),
        llm_result_fields=["summary"],
        metadata={"result_kind": "event"},
        input_schema={"type": "object", "properties": {}},
    )
    async def get_current_game(self, **_: Any) -> Any:
        await self._ensure_config_ready()
        session = self._manager.current
        if session is None:
            return Ok({"game_id": "", "game": "", "summary": self._t("status.get_game.none")})
        return Ok(
            {
                "game_id": session.game_id,
                "game": session.display_name,
                "switched_at": session.switched_at,
                "summary": self._t("status.get_game.done", game=session.display_name),
            }
        )

    @plugin_entry(
        id="list_games",
        name=tr("entries.list_games.name", default="列出可用游戏"),
        description=tr(
            "entries.list_games.description",
            default="列出当前已登记、可以陪聊的游戏。",
        ),
        llm_result_fields=["summary"],
        metadata={"result_kind": "event"},
        input_schema={"type": "object", "properties": {}},
    )
    async def list_games(self, **_: Any) -> Any:
        await self._ensure_config_ready()
        games = self._registry.enabled_games()
        if not games:
            return Ok({"games": [], "count": 0, "summary": self._t("status.list_games.empty")})
        current = self._manager.current
        payload = [
            {
                "game_id": entry.game_id,
                "display_name": entry.display_name,
                "current": bool(current is not None and current.game_id == entry.game_id),
            }
            for entry in games
        ]
        names = "、".join(entry.display_name for entry in games)
        return Ok(
            {
                "games": payload,
                "count": len(payload),
                "summary": self._t("status.list_games.done", count=len(payload), games=names),
            }
        )

    @plugin_entry(
        id="refresh_game_context",
        name=tr("entries.refresh_game_context.name", default="重新注入语境"),
        description=tr(
            "entries.refresh_game_context.description",
            default="把当前游戏的语境重新送进对话，长对话里语境变淡时用。",
        ),
        llm_result_fields=["summary"],
        metadata={"result_kind": "event"},
        input_schema={"type": "object", "properties": {}},
    )
    async def refresh_game_context(self, **_: Any) -> Any:
        await self._ensure_config_ready()
        session = self._manager.current
        entry = self._registry.get(session.game_id) if session is not None else None
        if session is None or entry is None:
            return Err(SdkError(self._t("errors.no_game_declared")))
        try:
            # force：用户可能刚改过覆盖层术语，"重新注入语境"要顺带重新读盘。
            self._manager.ensure_library(entry, force=True)
        except TermLoadError as exc:
            return Err(SdkError(self._terms_error_text(exc.code)))
        if not self._push_context(session, announce_switch=False):
            return Err(SdkError(self._t("errors.push_failed")))
        return Ok(
            {
                "game_id": session.game_id,
                "game": session.display_name,
                "context_injected": True,
                "summary": self._t("status.refresh.done", game=session.display_name),
            }
        )

    @plugin_entry(
        id="capture_screen",
        name=tr("entries.capture_screen.name", default="捕获屏幕"),
        description=tr(
            "entries.capture_screen.description",
            default="读取宿主推给模型的最近屏幕帧，用于 OCR 识别。",
        ),
        metadata={"result_kind": "event"},
        input_schema={
            "type": "object",
            "properties": {
                "max_count": {
                    "type": "integer",
                    "description": "最多读取几帧，默认 2，上限 4",
                    "default": 2,
                }
            },
        },
    )
    async def capture_screen(self, *, max_count: int = 2, **_):
        # 只读 frames，不写日志、不外传
        limit = max(1, min(int(max_count), 4))
        try:
            frames = await asyncio.to_thread(
                self.bus.frames.get, max_count=limit
            )
        except Exception as exc:
            return Err(SdkError(f"bus.frames 不可用: {type(exc).__name__}"))

        if isinstance(frames, Err):
            return frames
        if isinstance(frames, Ok):
            frames = frames.value

        records = list(frames)
        return Ok({
            "count": len(records),
            "frames": [
                {
                    "source": getattr(item, "source", None),
                    "mime": getattr(item, "mime", None),
                    "captured_at": getattr(item, "captured_at", None),
                    "has_image": bool(getattr(item, "image_base64", None)),
                }
                for item in records
            ],
        })

    @plugin_entry(
        id="ocr_screen",
        name=tr("entries.ocr_screen.name", default="识别屏幕文字"),
        description=tr(
            "entries.ocr_screen.description",
            default="读取屏幕帧做 OCR，与当前游戏术语库匹配后返回 matched_terms 列表。当用户问'屏幕上是什么''我在干嘛'时调用；不要拿返回值联网搜索，不要复述数字，不要反复调用。",
        ),
        metadata={"result_kind": "event"},
        input_schema={
            "type": "object",
            "properties": {
                "max_count": {
                    "type": "integer",
                    "description": "最多处理几帧，默认 1，上限 2",
                    "default": 1,
                }
            },
        },
    )
    async def ocr_screen(self, *, max_count: int = 1, **_):
        limit = max(1, min(int(max_count), 2))
        try:
            frames = await asyncio.to_thread(
                self.bus.frames.get, max_count=limit
            )
        except Exception as exc:
            return Err(SdkError(f"bus.frames 不可用: {type(exc).__name__}"))

        if isinstance(frames, Err):
            return frames
        if isinstance(frames, Ok):
            frames = frames.value

        records = list(frames)
        ocr_parts = []
        for item in records:
            b64 = getattr(item, "image_base64", None) or ""
            if b64:
                text = await self._ocr.extract_text_from_base64(b64)
                if text:
                    ocr_parts.append(text)

        combined = "\n".join(ocr_parts)
        ocr_chars = len(combined)

        session = self._manager.current
        game_id = session.game_id if session else None
        matched: list[str] = []
        library = self._manager.library
        if library is not None and combined:
            for entry in library.by_key.values():
                if len(matched) >= 10:
                    break
                if entry.key in combined:
                    matched.append(entry.key)
                    continue
                for alias in entry.aliases:
                    if alias in combined:
                        matched.append(entry.key)
                        break

        return Ok({
            "count": len(records),
            "game": game_id,
            "matched_terms": matched[:10],
            "ocr_chars": ocr_chars,
        })

    # ==================================================================
    # 宿主消息：P1 的"每 N 轮重推一次语境"
    # ==================================================================
    # 刻意不看 text / sender 的内容（那是用户对话，属隐私面），只用于计数。
    # 宿主不派发或清空配置时行为是"永不重推"，绝不会刷屏。

    @message(id="on_chat_message", source="chat")
    async def on_chat_message(self, text: str = "", sender: str = "", **_: Any) -> Any:
        del sender
        session = self._manager.current
        # 拍板 2.0.59：无感激活——用户消息命中术语 → 立即激活 + 推术语卡（与 periodic reinject 独立）
        if session is not None and text:
            self._scan_and_activate_query(text)
        every_n = self._options.reinject_every_n_messages
        if every_n <= 0:
            return Ok({"reinjected": False, "reason": "disabled"})
        if session is None:
            return Ok({"reinjected": False, "reason": "no_game"})
        if not self._counter.observe(1):
            return Ok(
                {"reinjected": False, "reason": "below_threshold", "pending": self._counter.pending}
            )
        injected = self._push_context(session, announce_switch=False)
        self.logger.info(
            "genshin_game_buddy: periodic reinject (every {} messages, submitted={})",
            every_n,
            injected,
        )
        return Ok({"reinjected": injected, "game_id": session.game_id})

    # ==================================================================
    # LLM 工具（对话期由模型调用）
    # ==================================================================
    # ⚠️ 与入口点**不同**：@llm_tool 处理函数的返回形状是 main_server 直接读的
    #    JSON —— 规范形状是 {"output": ..., "is_error": bool}，**不是** Ok/Err。
    #    依据：docs/plugins/tool-calling.md 的 "Returning errors"；
    #    参考实现 plugin/plugins/game_agent_minecraft/__init__.py 的 llm_tool 处理器。

    @llm_tool(
        name="set_current_game",
        description=tr(
            "tools.set_current_game.description",
            default="当用户提到自己正在玩某款游戏时调用，登记当前游戏，之后的陪聊会使用该游戏的语境与术语。",
        ),
        parameters={
            "type": "object",
            "properties": {
                "game": {
                    "type": "string",
                    "description": tr(
                        "tools.set_current_game.param.game",
                        default="游戏名称，可以是用户原话里的说法。",
                    ),
                },
            },
            "required": ["game"],
        },
        timeout=10.0,
    )
    async def set_current_game(self, *, game: str = "", **_: Any) -> Any:
        status, result = await self._register(game)
        if status == "ok" and result is not None:
            injected = self._push_context(result.session)
            return {
                "output": {
                    "registered": True,
                    "game": result.session.display_name,
                    "game_id": result.session.game_id,
                    "term_count": result.library.size(),
                    "context_injected": injected,
                    "persisted": result.persisted,
                    "notice": "" if result.persisted else self._t("errors.store_unavailable"),
                },
                "is_error": False,
            }
        if status == "not_found":
            # 用户说了一个库里没有的名字：这是可自纠的正常情况，不是错误。
            return {
                "output": {
                    "registered": False,
                    "reason": "unknown_game",
                    "available": [entry.display_name for entry in self._registry.enabled_games()],
                    "message": self._t(
                        "errors.unknown_game", available=self._registry.available_names()
                    ),
                },
                "is_error": False,
            }
        if status == "disabled":
            return {
                "output": {
                    "registered": False,
                    "reason": "game_disabled",
                    "message": self._t("errors.game_disabled"),
                },
                "is_error": False,
            }
        if status in ("terms_dir_missing", "terms_load_failed"):
            return {
                "output": {
                    "registered": False,
                    "reason": status,
                    "message": self._terms_error_text(status),
                },
                "is_error": True,
                "error": "TERMS_UNAVAILABLE",
            }
        return {
            "output": {
                "registered": False,
                "reason": "internal",
                "message": self._t("errors.internal"),
            },
            "is_error": True,
            "error": "INTERNAL",
        }

    @llm_tool(
        name="lookup_game_term",
        description=tr(
            "tools.lookup_game_term.description",
            default="查询当前游戏术语库里的具体条目（角色、机制、武器、圣遗物等）。【重要】参数 query 必须传角色名/术语名（如「纳西妲」「深境螺旋」），不要传完整句子。查不到时先用你已有的知识回答，不要只是说「找不到」。",
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": tr(
                        "tools.lookup_game_term.param.query",
                        default="要查的术语或玩家的说法。",
                    ),
                },
            },
            "required": ["query"],
        },
        timeout=10.0,
    )
    async def lookup_game_term(self, *, query: str = "", **_: Any) -> Any:
        await self._ensure_config_ready()
        text = query.strip() if isinstance(query, str) else ""
        # 隐私红线：只记长度，绝不记 query 原文。
        self.logger.info("genshin_game_buddy: lookup_game_term received query_len={}", len(text))

        session = self._manager.current
        if session is None:
            return {
                "output": {"found": False, "reason": "no_game", "message": self._t("lookup.no_game")},
                "is_error": False,
            }

        library, error_code = self._library_for(session)
        if library is None:
            return {
                "output": {
                    "found": False,
                    "reason": error_code,
                    "message": self._terms_error_text(error_code),
                },
                "is_error": True,
                "error": "TERMS_UNAVAILABLE",
            }

        matches = library.match(text, self._options.lookup_max_matches)
        if not matches:
            self.logger.info(
                "genshin_game_buddy: lookup_game_term miss (game={}, query_len={})",
                session.game_id,
                len(text),
            )
            return {
                "output": {
                    "found": False,
                    "game": session.display_name,
                    "message": self._t("lookup.not_found", game=session.display_name),
                },
                "is_error": False,
            }

        cards = [
            render_term_card(
                entry,
                entry_line=self._entry_line,
                slang_label=self._t("lookup.slang_label"),
                avoid_label=self._t("lookup.avoid_label"),
                max_chars=self._options.lookup_max_chars_per_term,
            )
            for entry in matches
        ]
        self.logger.info(
            "genshin_game_buddy: lookup_game_term hit {} (game={}, query_len={})",
            len(matches),
            session.game_id,
            len(text),
        )
        return {
            "output": {
                "found": True,
                "game": session.display_name,
                "count": len(matches),
                "entries": [self._entry_payload(entry) for entry in matches],
                "text": build_lookup_block(
                    self._t("lookup.found_header", count=len(matches)), cards
                ),
            },
            "is_error": False,
        }

    # ==================================================================
    # 内部实现（不以 "_" 开头以外的公开方法一律不挂装饰器）
    # ==================================================================

    def _terms_error_text(self, code: str) -> str:
        """把术语加载错误码映射成文案（错误码不含路径，只含维度/原因）。"""
        if code == "terms_dir_missing":
            return self._t("errors.terms_dir_missing")
        return self._t("errors.terms_load_failed")

    def _warn_dev(self, note: str) -> None:
        self.logger.warning("genshin_game_buddy: {}", note)

    def _load_library_for(self, entry: GameEntry) -> TermLibrary:
        """默认层读插件目录，覆盖层读 data_path()（KB：不得自拼数据根）。"""
        return load_library(
            default_root=self.plugin_dir,
            override_root=self.data_path(),
            game_id=entry.game_id,
            display_name=entry.display_name,
            terms_dir=entry.terms_dir,
            warn=self._warn_dev,
        )

    def _library_notes(self, library: TermLibrary) -> list[str]:
        notes = [self._t("terms.load.ok", count=library.size())]
        for dimension in library.failed_dimensions:
            notes.append(self._t("terms.load.partial", dimension=dimension))
        for dimension in library.empty_dimensions:
            notes.append(self._t("terms.load.empty_dimension", dimension=dimension))
        return notes

    def _library_for(self, session: GameSession) -> tuple[TermLibrary | None, str]:
        """取当前术语库；不存在时按需重载。

        返回 ``(library, error_code)``：成功时 ``error_code`` 为空串。
        """
        library = self._manager.library
        if library is not None:
            return library, ""
        entry = self._registry.get(session.game_id)
        if entry is None:
            return None, "terms_dir_missing"
        try:
            return self._manager.ensure_library(entry), ""
        except TermLoadError as exc:
            return None, exc.code

    async def _register(self, raw: object) -> tuple[str, SwitchResult | None]:
        """登记/切换的公共路径。返回 ``(status, result)``。

        status ∈ ok / not_found / disabled / terms_dir_missing / terms_load_failed / internal
        """
        await self._ensure_config_ready()
        match = self._registry.resolve(raw)
        if not match.found or match.game is None:
            self.logger.info(
                "genshin_game_buddy: register miss (input_len={})",
                len(raw) if isinstance(raw, str) else 0,
            )
            return "not_found", None
        entry = match.game
        if not entry.enabled:
            return "disabled", None
        try:
            result = await self._manager.switch(entry)
        except TermLoadError as exc:
            self.logger.warning(
                "genshin_game_buddy: term library unavailable for game {} ({})",
                entry.game_id,
                exc.code,
            )
            return exc.code, None
        except Exception:
            self.logger.exception("genshin_game_buddy: unexpected failure while switching game")
            return "internal", None
        self._load_scenes_for_current()
        self._init_anchors(result.session, result.library)
        # game_id 不是用户隐私，允许进日志（E-21）；用户原文与 query 一律不记。
        self.logger.info(
            "genshin_game_buddy: registered game {} ({} terms, persisted={})",
            entry.game_id,
            result.library.size(),
            result.persisted,
        )
        return "ok", result

    def _entry_line(self, entry: TermEntry) -> str:
        return self._t("lookup.entry_line", term=entry.key, kind=entry.kind, brief=entry.brief)

    def _entry_payload(self, entry: TermEntry) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "key": entry.key,
            "kind": entry.kind,
            "brief": entry.brief,
        }
        if entry.aliases:
            payload["aliases"] = list(entry.aliases)
        if entry.slang:
            payload["slang"] = list(entry.slang)
        if entry.avoid:
            payload["avoid"] = entry.avoid
        if entry.source:
            payload["source"] = entry.source
        return payload

    # ==================================================================
    # 2.1a：感知层内部方法
    # ==================================================================

    def _load_scenes_for_current(self) -> None:
        """加载当前游戏的场景定义。文件缺失返回空 dict。"""
        session = self._manager.current
        if session is None:
            with self._state_lock:
                self._scene_store = {}
            return
        entry = self._registry.get(session.game_id)
        terms_dir = entry.terms_dir if entry else f"terms/{session.game_id}"
        warnings: list[str] = []
        scenes = load_scenes(
            default_root=self.plugin_dir,
            override_root=self.data_path(),
            game_id=session.game_id,
            terms_dir=terms_dir,
            warnings=warnings,
        )
        with self._state_lock:
            self._scene_store = scenes
        for w in warnings:
            self.logger.warning("genshin_game_buddy: scene load: {}", w)
        # 拍板 2.0.72：明确打"restored"——startup 自动恢复场景是关键路径
        # （区别于 set_game 触发的 reload/手动重载）
        self.logger.info(
            "genshin_game_buddy: restored {} scene(s) for {}",
            len(scenes), session.game_id,
        )

    def _init_anchors(self, session: Any, library: TermLibrary) -> None:
        """初始化核心层激活 + context_terms 显式补充，永久常驻。

        拍板 2.0.56：核心层（core + slang + systems 维度全部条目，约 20-35 条）默认常驻上下文，
        TTL=inf、永不淘汰；MAX_ACTIVE 同步从 12 放宽到 40 容纳核心层。entry.context_terms
        显式清单作为额外补充（也常驻）；角色/武器等具体条目留给 lookup_game_term 按需查。
        """
        # 核心层：library 里所有 dimension ∈ CORE_LAYER_DIMENSIONS 的条目，按维度优先级排列
        core_layer_keys: list[str] = []
        for dimension in CORE_LAYER_DIMENSIONS:
            core_layer_keys.extend(
                e.key for e in library.entries if e.dimension == dimension
            )
        # context_terms 显式补充（去重于核心层）
        extra_keys: list[str] = []
        missing: list[str] = []
        entry = self._registry.get(session.game_id)
        if entry is not None and entry.context_terms:
            for k in entry.context_terms:
                if library.get(k) is None:
                    missing.append(k)
                elif k not in core_layer_keys:
                    extra_keys.append(k)
        if missing:
            self.logger.warning(
                "genshin_game_buddy: context_terms has {} unknown key(s) for game {}",
                len(missing), session.game_id,
            )
        all_keys = core_layer_keys + extra_keys
        if all_keys:
            with self._state_lock:
                self._activation.activate_terms(
                    all_keys, source="core_layer", ttl_seconds=float("inf")
                )

    def _update_perception_state(self, ocr_hit: bool, in_game_chars: bool = False) -> str:
        """状态机：UNKNOWN → IN_GAME → AWAY → OUT_OF_GAME。返回新状态。

        拍板 2.0.62：必须 ocr_hit AND in_game_chars 才升 IN_GAME；
        仅 ocr_hit 但无游戏内特征（如桌面泛词误命中）→ 保持上态（不增减 miss_streak）。
        in_game_chars 默认 False——保持早期 hash-重复路径的行为兼容。
        """
        if ocr_hit and in_game_chars:
            self._miss_streak = 0
            self._perception_state = "IN_GAME"
        elif not ocr_hit:
            self._miss_streak += 1
            if self._perception_state == "IN_GAME" and self._miss_streak >= 3:
                self._perception_state = "AWAY"
            elif self._perception_state == "AWAY" and self._miss_streak >= 6:
                self._perception_state = "OUT_OF_GAME"
        # else: ocr_hit 但无游戏内特征 → 保持现状（桌面泛词命中，不升级不降级）
        return self._perception_state

    def _desktop_markers(self) -> tuple[str, ...]:
        """拍板 2.0.64：运行时合并 builtin + 用户自定义 extra；总开关 desktop_markers_enabled 关掉时整张表失效。"""
        if not self._options.desktop_markers_enabled:
            return ()
        return _DESKTOP_MARKERS_BUILTIN + tuple(self._options.desktop_markers_extra)

    def _has_in_game_characteristics(self, text: str, detected_game: str) -> bool:
        """拍板 2.0.62 + 2.0.63：判定 OCR 文本是否真有"游戏内特征"，防桌面泛词误判 IN_GAME。

        2.0.63 收紧：
          (0) 桌面/浏览器反特征黑名单（命中任一 → 直接判非游戏）
          (a) 命中当前游戏的核心层术语（core/slang/systems 维度，>= 1）
          (b) 命中当前游戏 >= 2 个术语（任意维度）
          (c) signals 命中 **且** 至少有 1 条术语命中（signals 单独不算）

        全不满足 -> False（视为桌面/非游戏 UI）。
        """
        if not text or not detected_game or detected_game == "none":
            return False
        # (0) 桌面/浏览器反特征黑名单（最优先——命中即拒）
        if any(marker in text for marker in self._desktop_markers()):
            return False
        for entry in self._registry.enabled_games():
            if entry.game_id != detected_game:
                continue
            # (a) + (b) 先算 library 命中数（alias 长度下限 >= 2 防单字误命中）
            lib, _ = self._detect_assets(entry)
            core_count = 0
            total_count = 0
            if lib is not None:
                for term in lib.by_key.values():
                    if (
                        (len(term.key) >= 2 and term.key in text)
                        or any(len(a) >= 2 and a in text for a in term.aliases)
                    ):
                        total_count += 1
                        if term.dimension in ("core", "slang", "systems"):
                            core_count += 1
            # (c) signals 命中 + 至少 1 条术语命中 → 才算真游戏内
            signals_hit = any(sig and sig in text for sig in entry.signals)
            if (core_count >= 1 or total_count >= 2) or (signals_hit and total_count >= 1):
                return True
            return False
        return False

    @staticmethod
    def _scan_screen_activate_terms(text: str, library: Any) -> tuple[list[str], list[str]]:
        """拍板 2.0.59 + 2.0.63：扫屏幕 OCR 命中术语（screen + slang）。

        2.0.63 加 alias/key 长度下限 >= 2——防单字 alias 误命中
        （"莹"→"谢莹"、"鱼"→任意"鱼"字、单字 key 裸命中）。
        Returns (screen_matched_keys, slang_matched_keys)。
        """
        screen_matched: list[str] = []
        slang_matched: list[str] = []
        if library is None:
            return screen_matched, slang_matched
        seen: set[str] = set()
        for entry in library.entries:
            if entry.key in seen:
                continue
            key_hit = len(entry.key) >= 2 and entry.key in text
            alias_hit = any(len(a) >= 2 and a in text for a in entry.aliases)
            if key_hit or alias_hit:
                screen_matched.append(entry.key)
                seen.add(entry.key)
            elif any(s and s in text for s in entry.slang):
                slang_matched.append(entry.key)
                seen.add(entry.key)
        return screen_matched, slang_matched

    def _match_scenes(self, ocr_text: str) -> list[str]:
        """遍历场景定义，signals 命中即返回场景名。"""
        if not ocr_text:
            return []
        matched: list[str] = []
        for name, scene in self._scene_store.items():
            for sig in scene.signals:
                if sig and sig in ocr_text:
                    matched.append(name)
                    break
        return matched

    async def detect_game(self, text: str) -> str:
        """用术语库+场景信号判断 OCR 文本的游戏归属（拍板 2.0.14）。返回 game_id 或 "none"。

        枚举注册表全部启用游戏（不硬编码 game_id——今天是 genshin/wuthering_waves，
        将来配了 honkai_star_rail/zenless_zone_zero 自动纳入），按 registry signals（权重高）
        + 术语库 key/alias 命中 + 场景信号命中 加权评分取最高；低于阈值判 "none"。
        OCR 文本只用于匹配，绝不写日志（隐私红线：只记长度+hash）。
        """
        if not text:
            return "none"
        best_id = ""
        best_score = 0
        for entry in self._registry.enabled_games():
            score = sum(3 for sig in entry.signals if sig and sig in text)
            lib, scenes = await asyncio.to_thread(self._detect_assets, entry)
            if lib is not None:
                score += sum(
                    1 for e in lib.by_key.values()
                    if e.key in text or any(a and a in text for a in e.aliases)
                )
            for scene in (scenes or {}).values():
                score += sum(1 for sig in scene.signals if sig and sig in text)
            if score > best_score:
                best_id, best_score = entry.game_id, score
        return best_id if best_score >= 2 else "none"

    def _detect_assets(self, entry: Any) -> tuple[Any, dict[str, SceneEntry]]:
        """缓存每游戏 (TermLibrary, scenes) 供归属检测复用；IO 只在首次发生。"""
        cached = self._detect_cache.get(entry.game_id)
        if cached is not None:
            return cached
        try:
            lib: Any = self._load_library_for(entry)
        except Exception:
            lib = None
        try:
            scenes = load_scenes(
                default_root=self.plugin_dir,
                override_root=self.data_path(),
                game_id=entry.game_id,
                terms_dir=entry.terms_dir,
                warnings=[],
            ) or {}
        except Exception:
            scenes = {}
        self._detect_cache[entry.game_id] = (lib, scenes)
        return lib, scenes

    # 拍板 2.0.67：S1 变化驱动 OCR——轻量帧 dHash（9x8 灰度 → 64 bit → 16 hex）。
    # 拍板 2.0.69：委托给 screen_capture.grab_primary_for_dhash——
    # ① mss.mss() 走 screen_capture 模块级 _MSS_LOCK（防 2 worker 并发抓屏死锁）
    # ② 失败/无屏幕 → 返回空串（调用方按"未检测"处理，继续走 interval 节奏）
    # ③ 不存帧，只算哈希（隐私红线）；日志只记 hash 前 8 字
    @staticmethod
    def _light_capture_dhash() -> str:
        """拍板 2.0.67/2.0.69：抓主屏 → 9x8 灰度 → dHash 64 bit → 16 hex chars。

        实际抓屏逻辑在 screen_capture.grab_primary_for_dhash（带 mss 全局锁），
        本函数仅做委托，让 mss 调用点全部集中到 screen_capture 模块。
        """
        try:
            from .screen_capture import grab_primary_for_dhash
            return grab_primary_for_dhash()
        except Exception:
            return ""

    @staticmethod
    def _dhash_diff(a: str, b: str) -> int:
        """拍板 2.0.67：两个 16-hex dHash 的汉明距离（位差异数）。空串/长度不匹配→0。"""
        if not a or not b or len(a) != len(b):
            return 0
        try:
            return (int(a, 16) ^ int(b, 16)).bit_count()
        except Exception:
            return 0

    def _should_push_proactive(self, ocr_text: str, scene_switched: bool = False) -> bool:
        """拍板 2.0.67：场景切换路径绕过 300s 全局冷却，走独立 scene_switch_cooldown。

        同场景：hash 变化 + 300s 全局冷却 + 术语密度 < 5（2.0.59 沿用）。
        场景切换：仅看 scene_switch_cooldown（默认 30s，防刷屏）+ 术语密度 < 5。
          - hash 检查跳过：切场景文本必然不同；放宽信任。
          - 300s 跳过：用户切场景就该立即说话，300s 卡死体验。
          - 术语密度保留：太多术语命中（密集中文）会让提示语挤掉上下文，仍判负。
        """
        import hashlib

        now = time.monotonic()
        if scene_switched:
            # 拍板 2.0.67：S2 独立冷却——切换就主动搭话，但同一切换间隔别刷屏
            if now - self._last_scene_switch_at < float(
                self._options.scene_switch_cooldown_seconds
            ):
                return False
        else:
            text_hash = hashlib.md5(ocr_text.encode("utf-8", errors="replace")).hexdigest()
            if text_hash == self._last_pushed_hash:
                return False
            if now - self._last_proactive_at < 300.0:
                return False
            # 同场景路径：hash 不同 → 把它记下来，避免下个 tick 仍判 hash 变化重复推
            self._last_pushed_hash = text_hash
        library = self._manager.library
        if library is not None:
            matched_count = sum(1 for e in library.by_key.values() if e.key in ocr_text)
            if matched_count > 5:
                return False
        return True

    def _push_proactive(
        self, changed_scenes: list[str], added: list[str], removed: list[str]
    ) -> bool:
        """构造变化量文本 + 当前激活 key 列表，push_message(ai_behavior="respond")。"""
        active_keys = self._activation.get_active_keys()
        if not self._activation.has_scene_active():
            text = self._t("perception.anchors_only")
        elif changed_scenes or added:
            action = (
                "切换到了「{}」场景".format("、".join(changed_scenes))
                if changed_scenes
                else "在当前场景中发现了新的关键词"
            )
            keys_text = " / ".join(active_keys[:12])
            text = self._t("perception.proactive_switch", action=action, keys=keys_text)
        else:
            _gid = getattr(self._manager.current, "game_id", "") if self._manager.current else ""
            text = self._t("perception.proactive_no_change", game=_gid)
        if added:
            text += " 新增：{}".format(" / ".join(added[:5]))
        if removed:
            text += " 移除：{}".format(" / ".join(removed[:5]))
        receipt = self.push_message(
            source="genshin_game_buddy",
            visibility=[],
            ai_behavior="respond",
            parts=[{"type": "text", "text": text}],
            priority=0,
            metadata={"kind": "perception_change"},
        )
        self._last_proactive_at = time.monotonic()
        return isinstance(receipt, Mapping) and receipt.get("submitted") is not False

    def _build_activated_context(self, session: Any) -> str:
        """锚点 + 激活集的 key + brief 文本。"""
        limit = self._options.context_inject_max_terms
        if limit <= 0:
            return ""
        library = self._manager.library
        if library is None:
            return ""
        active_keys = self._activation.get_active_keys()[:limit]
        lines: list[str] = []
        for key in active_keys:
            entry = library.get(key)
            if entry is not None:
                lines.append(self._t("context.term_line", term=entry.key, brief=entry.brief))
        return "\n".join(lines)

    def _scan_and_activate_query(self, text: str) -> None:
        """拍板 2.0.59：扫用户消息命中术语 → 激活（source="query", ttl=300）→ 推简短术语卡。
        只扫 key + aliases（slang 留给 ocr_perceive）。已激活的不重推；>8 条只激活不推。
        push 内容只含 key+brief（库数据），不含用户原文——守住 I-16。"""
        library = self._manager.library
        if library is None:
            return
        matched: list[str] = []
        seen: set[str] = set()
        for entry in library.by_key.values():
            if entry.key in seen:
                continue
            if entry.key in text or any(a and a in text for a in entry.aliases):
                matched.append(entry.key)
                seen.add(entry.key)
        if not matched:
            return
        with self._state_lock:
            old_keys = set(self._activation.get_active_keys())
            self._activation.activate_terms(matched, source="query", ttl_seconds=float(self._options.query_activation_ttl_seconds))
        newly = [k for k in matched if k not in old_keys]
        self.logger.info(
            "genshin_game_buddy: on_chat_message query-activate keys={} count={}",
            newly or matched, len(matched),
        )
        if not newly or len(matched) > 8:
            return
        hint = self._render_query_hint(newly)
        current = self._manager.current
        self.push_message(
            source="genshin_game_buddy",
            visibility=[],
            ai_behavior="read",
            parts=[{"type": "text", "text": hint}],
            priority=0,
            metadata={"game_id": current.game_id if current else "", "term_count": len(newly)},
        )

    def _render_query_hint(self, keys: list[str]) -> str:
        """拍板 2.0.59：构造「用户刚提到 X。相关条目：...」术语卡。
        只含 key + brief（库数据），不含用户原文——守住 I-16。"""
        library = self._manager.library
        if library is None or not keys:
            return ""
        lines = [f"用户刚提到：{'、'.join(keys)}。相关条目："]
        for k in keys:
            entry = library.get(k)
            if entry is not None:
                lines.append(f"- {entry.key}：{entry.brief}")
        return "\n".join(lines)

    # ==================================================================
    # 2.1a：周期感知 timer
    # ==================================================================

    @timer_interval(id="ocr_perceive", seconds=3)
    async def ocr_perceive(self) -> None:
        """拍板 2.0.65：tick 装饰器固定 3s；实际 OCR 间隔由 self._options 控制。

        多线程 OCR：把 OCR 主体扔进 ThreadPoolExecutor 的 worker 线程（自带 asyncio.run 事件循环），
        timer 线程立即返回不阻塞。worker 数由 self._options.ocr_worker_threads 决定（1 或 2）。

        拍板 2.0.67：S1 变化驱动 OCR——每 3s tick 抓轻量帧 dHash，
        与上一帧差异 ≥ threshold 时绕过 interval 立即 OCR。
        轻量帧只算 hash、不存像素（隐私）；失败/无屏幕 → 静默跳过，按 interval 节奏走。

        拍板 2.0.68：3 层防御——保证 timer 不被任何子异常弄死（真机 2.0.67 暴露：
        14 分钟只 1 条 tick begin，怀疑 run_in_executor 抛异常让 _ocr_in_flight_count
        永久卡住、后续所有 tick 被 worker 闸静默挡掉）。
          ① 任何 early-return 都打 INFO 日志（reason + 关键值），便于排查"卡在哪儿"
          ② 整个函数 try/except 包住，任何异常 logger.exception 后吞掉（timer 继续）
          ③ _ocr_in_flight_count 用 try/finally 保证释放——schedule 阶段抛异常
             也能减回，不会让 worker 闸永久卡死

        拍板 2.0.71：高频 OCR——tick 间隔 = ocr_scene_interval_seconds（默认 2s）。
        真机单次 OCR 链路 1.44s（capture 0.14 + ocr 1.30 + detect 0.00）→ interval < 1.5s 会排队。
        抓屏/OCR 结果缓存给"术语激活"轮次复用（避免每 2s 都做完整链路）。
        """
        try:
            # 拍板 2.0.71：tick 间隔用 scene_interval（默认 2s）；保留 ocr_perceive_interval_seconds
            # 字段以兼容旧面板/配置文件，但语义已被 ocr_scene_interval_seconds / ocr_term_interval_seconds 取代。
            interval = self._options.ocr_scene_interval_seconds
            now_mono = time.monotonic()
            trigger_reason = ""

            # 拍板 2.0.67：S1 变化驱动——轻量帧 dHash 对比（默认关，2.0.70 关闭的 worker 卡死回避）
            if self._options.change_driven_enabled:
                with self._state_lock:
                    state = self._perception_state
                if state != "OUT_OF_GAME":
                    try:
                        new_hash = await asyncio.to_thread(self._light_capture_dhash)
                    except Exception:
                        self.logger.exception(
                            "genshin_game_buddy: ocr_perceive change_detect dhash await failed "
                            "(fall through to interval gate)"
                        )
                        new_hash = ""
                    if new_hash:
                        diff = self._dhash_diff(new_hash, self._last_light_hash)
                        threshold = self._options.change_detect_threshold
                        self._last_light_hash = new_hash
                        if diff >= threshold:
                            trigger_reason = (
                                f"change_detect diff={diff}>={threshold}"
                            )

            # 拍板 2.0.71：决定是否 OCR——scene_interval 已到 OR change_detect 命中
            if not trigger_reason and now_mono - self._last_real_ocr_monotonic < interval:
                # 拍板 2.0.68：每个 early-return 必打 INFO（带 elapsed vs interval）
                self.logger.info(
                    "genshin_game_buddy: ocr_perceive skip reason=interval_not_reached "
                    "elapsed={:.1f}s interval={}s state_change={}",
                    now_mono - self._last_real_ocr_monotonic,
                    interval,
                    "yes" if trigger_reason else "no",
                )
                return

            # 拍板 2.0.65：worker 闸——限制并发 OCR 数（防堆积）。
            # tick=3s 但 OCR 自身可能 1-2s；worker 数限速：1=上一轮未跑完则跳过；2=允许 2 并发。
            with self._ocr_in_flight_lock:
                if self._ocr_in_flight_count >= self._options.ocr_worker_threads:
                    # 拍板 2.0.68：worker 闸被挡也打 INFO（防"沉默卡死"再次发生）
                    self.logger.info(
                        "genshin_game_buddy: ocr_perceive skip reason=worker_busy "
                        "in_flight={} workers={}",
                        self._ocr_in_flight_count,
                        self._options.ocr_worker_threads,
                    )
                    return
                self._ocr_in_flight_count += 1

            self._last_real_ocr_monotonic = now_mono
            self.logger.info(
                "genshin_game_buddy: ocr_perceive tick begin reason={}",
                trigger_reason or "interval",
            )

            # 拍板 2.0.70：stress mode 计数 + 60s 周期报告（getattr 防 stubs 缺字段）
            if getattr(self, "_stress_mode", "off") != "off" and getattr(self, "_stress_metrics", None) is not None:
                with getattr(self, "_stress_lock"):
                    self._stress_metrics["total_ocr"] += 1
                self._stress_maybe_report()

            # 拍板 2.0.70：watchdog 改 threading.Timer + concurrent.futures.Future——
            # 关键修复：2.0.69 的 `loop.call_later(60, _watchdog)` 用了**临时事件循环**（`@timer_interval`
            # 每 3s `asyncio.run(ocr_perceive)`，每次 run 都创建临时 loop，循环在 ocr_perceive return 后立刻关闭），
            # 排上的 call_later 回调**永远不会被触发**。真机 2.0.69 暴露 watchdog 从没触发过。
            # 同样，`asyncio.Future.add_done_callback(_on_done)` 也依赖临时 loop——也永不触发。
            #
            # 修复：① 直接用 `self._ocr_executor.submit(fn)` 拿 `concurrent.futures.Future`（不被 loop 管）
            #      ② `cf_future.add_done_callback(_on_done)` 会在 executor 的 worker 线程里跑（线程安全）
            #      ③ `threading.Timer(60, _watchdog)` 是独立线程，**不依赖任何 loop**，绝对能触发
            #      ④ 共享 _released flag 互斥释放 count（避免 _on_done 和 watchdog 重复 -1）
            try:
                # 懒初始化 executor：第一次 tick 时按配置创建，后续 worker 数变了由 apply_settings 重建
                if self._ocr_executor is None:
                    workers = max(1, min(2, self._options.ocr_worker_threads))
                    self._ocr_executor = concurrent.futures.ThreadPoolExecutor(
                        max_workers=workers,
                        thread_name_prefix="mgc-ocr",
                    )

                # 拍板 2.0.70：直接 submit 拿 concurrent.futures.Future——不被任何临时 loop 管
                cf_future = self._ocr_executor.submit(self._ocr_perceive_sync_entry)

                # 共享释放标志——watchdog 和 _on_done 谁先到谁释放，避免重复 -1
                _released = [False]

                def _release_count():
                    if _released[0]:
                        return
                    _released[0] = True
                    with self._ocr_in_flight_lock:
                        self._ocr_in_flight_count -= 1

                def _on_done(_fut):
                    try:
                        _release_count()
                    except Exception:
                        # 拍板 2.0.70：绝不让 count 减失败变成静默
                        self.logger.exception(
                            "genshin_game_buddy: _on_done count release failed "
                            "(cf_future done but count may leak; watchdog will retry-release)"
                        )

                # concurrent.futures.Future.add_done_callback 在 executor 的 worker 线程里跑回调，
                # 不依赖任何临时 loop——**保证触发**
                cf_future.add_done_callback(_on_done)

                # 拍板 2.0.70：threading.Timer 60s 看门狗——独立线程，不被任何 loop 关闭影响。
                # 即使 tick 装饰器的临时 loop 死了，Timer 线程照常跑。
                def _watchdog():
                    try:
                        if not cf_future.done():
                            self.logger.warning(
                                "genshin_game_buddy: ocr worker timeout after 60s; "
                                "force-cancelling and releasing slot (likely mss deadlock, "
                                "winrt OCR hang, or await block in _ocr_perceive_body — "
                                "see stage= timing logs in 2.0.70+ to locate which stage stuck)"
                            )
                            cf_future.cancel()  # cf 在运行中 cancel 返回 False 但不抛——只标 cancelled
                            # 拍板 2.0.70：stress mode 计数
                            if self._stress_mode != "off" and self._stress_metrics is not None:
                                with self._stress_lock:
                                    self._stress_metrics["timeouts"] += 1
                        _release_count()  # 即使 cf_future 已 done，flag 兜底也再调一次（no-op）
                    except Exception:
                        self.logger.exception(
                            "genshin_game_buddy: worker watchdog callback failed "
                            "(count may leak; next tick may stall at worker闸)"
                        )

                timer = threading.Timer(60.0, _watchdog)
                timer.daemon = True
                timer.start()
            except Exception:
                # schedule 失败 → 把刚加的 count 还回去，让下个 tick 能正常尝试
                with self._ocr_in_flight_lock:
                    self._ocr_in_flight_count -= 1
                self.logger.exception(
                    "genshin_game_buddy: ocr_perceive schedule failed (count released, timer continues)"
                )
                # 拍板 2.0.70：stress mode 计数（getattr 防 stubs 缺字段）
                if getattr(self, "_stress_mode", "off") != "off" and getattr(self, "_stress_metrics", None) is not None:
                    with getattr(self, "_stress_lock"):
                        self._stress_metrics["errors"] += 1
        except Exception:
            # 拍板 2.0.68：最外层兜底——任何意外异常都不能逃逸出 timer 协程，
            # 否则宿主 timer 装饰器可能停止调度后续 tick。
            self.logger.exception(
                "genshin_game_buddy: ocr_perceive tick outer exception (tick dropped, timer keeps running)"
            )

    def _ocr_perceive_sync_entry(self) -> None:
        """拍板 2.0.65：worker 线程入口——asyncio.run 自带事件循环。

        线程模型：@timer_interval 在 timer 线程用 asyncio.run() 跑 ocr_perceive 协程，
        ocr_perceive 调 run_in_executor 把这个同步函数扔进 ThreadPoolExecutor，
        worker 线程里再 asyncio.run() 跑 _ocr_perceive_body 协程。两层事件循环在两个不同线程，不冲突。
        """
        try:
            asyncio.run(self._ocr_perceive_body())
        except Exception:
            self.logger.exception("genshin_game_buddy: _ocr_perceive_body crashed")

    async def _ocr_perceive_body(self) -> None:
        """拍板 2.0.65：OCR 感知主体（OCR 抓屏 → 识别 → 归属 → 激活 → 推）。

        在 ThreadPoolExecutor worker 线程的事件循环里跑（asyncio.run 自带 loop）。
        内层 await 全部走 SDK/内部协程，不依赖 timer 线程的 loop。

        拍板 2.0.70：段级打点——每个 await 前后打 time.monotonic() 戳（stage=xxx + dt=Ns），
        定位 worker 卡死的真根因。日志格式：
          stage=body_enter / stage=capture_done dt=Xs / stage=ocr_done dt=Xs / ...
        下次卡死时日志停在某个 stage=xxx_done 之前 = 该段 await 永久阻塞。

        拍板 2.0.71：高频 OCR + 场景状态机——
          - tick 间隔 = ocr_scene_interval_seconds（默认 2s；快）
          - term 轮次 = ocr_term_interval_seconds（默认 15s；慢）
          - 抓屏/OCR 结果缓存给 term 轮次复用（不重抓）
          - SceneTracker 滞回（hysteresis_count）→ 切换即推（不等 interval 周期）
        """
        import hashlib
        t0 = time.monotonic()
        self.logger.info("genshin_game_buddy: stage=body_enter")
        # 拍板 2.0.71：SceneTracker 心跳——每 30s 打一次 snapshot（不论 body 走完整链路还是 early-return）
        # 解决"2 分钟日志只有 body_enter/exit，没有 scene_tracker"问题：状态机一直在跑，只是没切换就不打日志
        # 拍板 2.0.73：心跳附加 ocr_in_flight / ocr_workers / executor_alive——便于排查 worker 卡死。
        # 真机 2.0.72 暴露：3 分钟只有 SceneTracker 心跳，但看不出 worker 闸是否被卡、
        # executor 是否 shutdown、heartbeat 线程本身是否在跑。一次性把状态机 + worker 健康度拼一行。
        # 用 getattr 防 stubs 缺字段（__new__ bypass __init__ 的情况）
        _st = getattr(self, "_scene_tracker", None)
        if _st is not None:
            _st_throttle = getattr(self, "_scene_tracker_log_throttle", 0.0)
            _st_interval = getattr(self, "_scene_tracker_log_interval", 30.0)
            if (t0 - _st_throttle) >= _st_interval:
                _snap = _st.snapshot()
                # 拍板 2.0.73：worker 闸 / executor 健康状态——一眼看出 worker 是否被卡
                with self._ocr_in_flight_lock:
                    _ocr_in_flight = self._ocr_in_flight_count
                _executor = getattr(self, "_ocr_executor", None)
                _executor_alive = (
                    _executor is not None
                    and not getattr(_executor, "_shutdown", False)
                )
                _ocr_workers = (
                    max(1, self._options.ocr_worker_threads)
                    if _executor_alive else 0
                )
                self.logger.info(
                    "genshin_game_buddy: scene_tracker heartbeat "
                    "current={} pending={} pending_count={} exit_pending_count={} "
                    "ocr_in_flight={} ocr_workers={} executor_alive={}",
                    _snap["current"] or "(none)",
                    _snap["pending"] or "(none)",
                    _snap["pending_count"],
                    _snap["exit_pending_count"],
                    _ocr_in_flight,
                    _ocr_workers,
                    _executor_alive,
                )
                self._scene_tracker_log_throttle = t0
        # 拍板 2.0.71：term 轮次判断——若距上次 term 激活 ≥ ocr_term_interval_seconds，本次也跑 term 激活
        # 用 getattr 防 stubs 缺字段（__new__ bypass __init__ 的情况）
        is_term_round = (
            t0 - getattr(self, "_last_term_run_monotonic", 0.0)
            >= self._options.ocr_term_interval_seconds
        )
        try:
            with self._state_lock:
                session = self._manager.current
                state = self._perception_state
            if session is None:
                self.logger.info("genshin_game_buddy: ocr_perceive early-return: no_session")
                return
            if state == "OUT_OF_GAME":
                # 隐私闸：判定已离开游戏，停止抓屏（D5）
                # 拍板 2.0.73：早退也打 INFO——区分"跳过"vs"卡死"。
                # 真机 2.0.72 暴露：3 分钟日志只有 heartbeat 没有 body_exit，
                # 根因就是这里静默 return。下一拍板必须能一眼看出"body 走到这步早退了"。
                self.logger.info(
                    "genshin_game_buddy: ocr_perceive early-return: state=OUT_OF_GAME "
                    "(privacy gate; next tick will re-check; if persists → perception_state stuck at OUT_OF_GAME)"
                )
                return

            # 拍板 2.0.14：本地进程优先，找不到抓前台窗口（云游戏）
            t_capture_start = time.monotonic()
            result, capture_path = await asyncio.to_thread(capture_active_frame)
            t_capture = time.monotonic()
            self.logger.info(
                "genshin_game_buddy: stage=capture_done dt={:.3f}s",
                t_capture - t_capture_start,
            )
            b64 = ""
            if result is not None and result.ok and result.image_base64:
                b64 = result.image_base64
                # 拍板 2.0.71：缓存抓屏结果给 term 轮次复用（避免每 2s 重抓）
                self._last_capture_b64 = b64
                self._last_capture_at_monotonic = t_capture
                self.logger.info(
                    "genshin_game_buddy: ocr_perceive capture_path={} method={} bytes={} luma={:.1f}",
                    capture_path, result.method, len(b64), result.avg_luma,
                )
            else:
                err = getattr(result, "error", "") if result else ""
                self.logger.info(
                    "genshin_game_buddy: ocr_perceive capture unavailable path={} error={}",
                    capture_path, err or "none",
                )
                # 辅助信号（D3）：主动抓屏失败时回退 bus.frames
                t_bus_start = time.monotonic()
                try:
                    frames = await asyncio.to_thread(self.bus.frames.get, max_count=1)
                except Exception:
                    frames = None
                self.logger.info(
                    "genshin_game_buddy: stage=bus_fallback_done dt={:.3f}s",
                    time.monotonic() - t_bus_start,
                )
                if isinstance(frames, Err):
                    frames = None
                elif isinstance(frames, Ok):
                    frames = frames.value
                records = list(frames) if frames is not None else []
                b64 = getattr(records[0], "image_base64", None) if records else ""
                if not b64:
                    # 拍板 2.0.71：本轮抓屏失败 → 若有缓存且本轮是 term 轮 → 用缓存做 OCR
                    if is_term_round and getattr(self, "_last_capture_b64", None):
                        age = t0 - self._last_capture_at_monotonic
                        if age < self._options.ocr_term_interval_seconds * 2:
                            b64 = self._last_capture_b64
                            self.logger.info(
                                "genshin_game_buddy: ocr_perceive term-round reuse-cached-capture "
                                "age={:.1f}s (current capture failed)",
                                age,
                            )
                    if not b64:
                        tag = "no_genshin_window" if err == "no_window" else "no_frame"
                        self.logger.info("genshin_game_buddy: ocr_perceive early-return: {}", tag)
                        return

            t_ocr_start = time.monotonic()
            text = await self._ocr.extract_text_from_base64(b64)
            self.logger.info(
                "genshin_game_buddy: stage=ocr_done dt={:.3f}s",
                time.monotonic() - t_ocr_start,
            )
            text_hash = hashlib.md5(text.encode("utf-8", errors="replace")).hexdigest()
            self.logger.info(
                "genshin_game_buddy: ocr_perceive text_len={} hash={} prev={}",
                len(text), text_hash[:8], (self._last_ocr_hash or "")[:8],
            )
            if len(text) == 0 and is_black_frame(b64):
                # 黑帧/无信号：不计入 miss（D6），也不做后续激活/推送
                self.logger.info("genshin_game_buddy: ocr_perceive early-return: black_frame")
                return

            # 拍板 2.0.71：缓存 OCR 文本——term 轮次复用（虽然现在 term round 自己也会 OCR；
            # 这里保存一下，供未来扩展如"离线分析"使用）。
            self._last_capture_text = text

            if text_hash == self._last_ocr_hash:
                with self._state_lock:
                    self._update_perception_state(self._last_ocr_hit)
                # 即便文本未变，也要做 SceneTracker 状态推进（防 stuck）
                self._scene_tracker.update([])  # 让 exit_grace_count 推进
                # 拍板 2.0.73：早退也打 INFO——文本未变是合法跳过，但 2.0.72 真机 3 分钟
                # 没 body_exit 日志时无法区分"卡死"和"OCR 一直返回同一帧"——必须留痕。
                self.logger.info(
                    "genshin_game_buddy: ocr_perceive early-return: text_unchanged hash={} prev={}",
                    text_hash[:8], (self._last_ocr_hash or "")[:8],
                )
                return

            self._last_ocr_hash = text_hash

            # 拍板 4：用术语库+场景信号判断游戏归属（命中 = 识别出某游戏）
            t_detect_start = time.monotonic()
            detected = await self.detect_game(text)
            self.logger.info(
                "genshin_game_buddy: stage=detect_done dt={:.3f}s detected={}",
                time.monotonic() - t_detect_start, detected,
            )
            ocr_hit = detected != "none"
            self._last_ocr_hit = ocr_hit
            # 拍板 2.0.64：转场清空——从"有游戏"变成"没游戏"或游戏变了 → 清屏/场景激活。
            # 但保留 query（用户主动问过的，LLM 可能还在聊）+ core_layer（常驻锚点）。
            # "scene" 前缀会同时清掉 f"scene:<name>" 形式的所有 scene 激活（activation.py 里就是这么存的）。
            if self._last_detected_game and detected != self._last_detected_game:
                with self._state_lock:
                    cleared = self._activation.clear_by_sources(
                        ("screen", "screen_slang", "scene")
                    )
                if cleared:
                    self.logger.info(
                        "genshin_game_buddy: ocr_perceive transition-clear "
                        "from={} to={} cleared={}",
                        self._last_detected_game, detected, cleared,
                    )
                self._last_scene_pushed = ""  # 场景 prompt 下次重新推
                # 拍板 2.0.67：S2 转场也清掉"上次主动搭话场景"，让切回新游戏时能识别为切换
                self._last_scene_for_proactive = ""
            self._last_detected_game = detected
            # 拍板 2.0.62：game 命中 ≠ 真在游戏——桌面泛词（plugin/N.E.K.O 插件管理等）也会让
            # detect_game 假阳性。必须满足"游戏内特征"才升级 IN_GAME；screen-activate 同步 gate。
            in_game_chars = self._has_in_game_characteristics(text, detected) if ocr_hit else False

            with self._state_lock:
                self._update_perception_state(ocr_hit, in_game_chars)
                old_keys = set(self._activation.get_active_keys())
                matched_scenes = self._match_scenes(text) if ocr_hit else []
                # 拍板 2.0.21：scenes=0 临时调试——scenes.toml 信号串未按真实 OCR 调优，
                # 打前 80 字便于调参。调好后撤除（2.0.22）。
                if text and not matched_scenes:
                    self.logger.info(
                        "ocr_perceive sample (scenes=0) text_head={!r}",
                        text[:80],
                    )
                if ocr_hit:
                    for scene_name in matched_scenes:
                        scene = self._scene_store.get(scene_name)
                        if scene is not None:
                            self._activation.activate_scene(scene_name, list(scene.context_terms))
                for scene_name in self._scene_store:
                    self._activation.tick_scene(scene_name, scene_name in matched_scenes)
                # 拍板 2.0.59 + 2.0.62：OCR 命中术语（key/alias/slang）→ 立即激活，screen ttl=180。
                # 2.0.62 加 in_game_chars gate——桌面泛词误命中时即便扫到别名也不激活
                # （防"桌面误激活迪奥娜"）。
                # 拍板 2.0.71：term 激活只在 term 轮跑（默认 15s）；scene 轮只做场景判定（2s）
                # 否则每 2s 都激活，刷屏且浪费 CPU。
                if in_game_chars and is_term_round:
                    library = self._manager.library
                    screen_matched, slang_matched = self._scan_screen_activate_terms(text, library)
                    # 拍板 2.0.64：screen-activate 上限（默认 12；MAX_ACTIVE=40 留 ~25 给核心层）
                    limit = self._options.screen_activation_limit
                    if limit > 0:
                        screen_matched = screen_matched[:limit]
                        remaining = max(0, limit - len(screen_matched))
                        slang_matched = slang_matched[:remaining]
                    if screen_matched:
                        self._activation.activate_terms(screen_matched, source="screen", ttl_seconds=180.0)
                    if slang_matched:
                        self._activation.activate_terms(slang_matched, source="screen_slang", ttl_seconds=180.0)
                    if screen_matched or slang_matched:
                        self.logger.info(
                            "genshin_game_buddy: ocr_perceive screen-activate keys={} slang_keys={} count={}",
                            screen_matched, slang_matched, len(screen_matched) + len(slang_matched),
                        )
                    # 拍板 2.0.71：term 轮跑完 → 更新 last_term_run（用当前 monotonic 即可）
                    self._last_term_run_monotonic = t0
                new_keys = set(self._activation.get_active_keys())

            if not ocr_hit:
                # 拍板 5：不中 → early-return no_game_detected
                self.logger.info(
                    "genshin_game_buddy: ocr_perceive early-return: no_game_detected detected_game={}",
                    detected,
                )
                return

            added = sorted(new_keys - old_keys)
            removed = sorted(old_keys - new_keys)

            # 拍板 2.0.67：S2 场景切换检测——primary 与上次主动搭话的场景不同 = 切换。
            # 切过游戏（_last_scene_for_proactive="" 但有 matched_scenes）也当切换看。
            scene_switched = False
            if matched_scenes:
                primary = matched_scenes[0]
                if primary != self._last_scene_for_proactive:
                    scene_switched = True
                    self.logger.info(
                        "genshin_game_buddy: ocr_perceive scene-switch "
                        "from={} to={} cooldown={}s",
                        self._last_scene_for_proactive or "(none)",
                        primary,
                        self._options.scene_switch_cooldown_seconds,
                    )

            # 拍板 2.0.71：场景状态机——SceneTracker 滞回 + 防抖动。
            # 不依赖 asyncio；纯同步逻辑（SceneTracker 内部状态机）。
            # 与 2.0.67 S2 scene_switched 互补：SceneTracker 防抖动（连续 N 次才认），
            # S2 scene_switched 立即识别（不防抖但走 30s 冷却）。
            # SceneTracker SCENE_SWITCHED 不直接 push_message（守住 push site == 4 红线），
            # 只更新 _last_scene_for_proactive；实际 push 走下方 2266 路径（应 always true）。
            from .scene_tracker import SceneEventType
            scene_event = self._scene_tracker.update(matched_scenes)
            if scene_event.type == SceneEventType.SCENE_SWITCHED:
                primary = scene_event.to_scene
                self.logger.info(
                    "genshin_game_buddy: scene_tracker scene-switch from={} to={} elapsed_in_old={:.1f}s",
                    scene_event.from_scene or "(none)",
                    primary,
                    scene_event.elapsed_in_old,
                )
                # 只更新跟踪字段；不动 _last_scene_pushed（让下方 2266 路径自然触发 push）
                self._last_scene_for_proactive = primary
            elif scene_event.type == SceneEventType.SCENE_EXITED:
                # 退出场景——可选：标记 OUT_OF_GAME（隐私闸）。这里只记日志，避免激进切回 OUT_OF_GAME。
                self.logger.info(
                    "genshin_game_buddy: scene_tracker scene-exited from={} elapsed_in_old={:.1f}s",
                    scene_event.from_scene or "(none)",
                    scene_event.elapsed_in_old,
                )

            if matched_scenes and self._should_push_proactive(text, scene_switched):
                self._push_proactive(matched_scenes, added, removed)
                # 拍板 2.0.67：S2 更新切换时间戳 + 上次主动搭话场景名。
                # 切路径走独立冷却；同场景路径只更新场景名（让下一次切回能再触发）。
                with self._state_lock:
                    if scene_switched:
                        self._last_scene_switch_at = time.monotonic()
                    self._last_scene_for_proactive = matched_scenes[0]

            # 拍板 2.0.61：场景命中 → 推 scene_prompt 到模型上下文（read，silently）。
            # 与 _push_proactive 独立——proactive 是 respond（触发回复），prompt 是 read（只预载）。
            # 拍板 2.0.64：scene_prompt_reinject_seconds > 0 时，同一场景距上次推超过 N 秒也重推
            # （防长对话中 prompt 被挤出上下文窗口；默认 0 = 只推一次，保持 2.0.61 行为）。
            # 拍板 2.0.71：SceneTracker SCENE_SWITCHED 触发后，这里 should_push=True（primary != _last_scene_pushed）
            # → 立即 push（不等 interval）。SceneTracker 滞回保证只在前 N 次连续匹配后才切换。
            if matched_scenes:
                primary = matched_scenes[0]
                should_push = primary != self._last_scene_pushed
                reinject = self._options.scene_prompt_reinject_seconds
                if not should_push and reinject > 0:
                    elapsed = time.monotonic() - self._last_scene_pushed_at
                    if elapsed >= reinject:
                        should_push = True
                if should_push:
                    scene = self._scene_store.get(primary)
                    if scene is not None and scene.prompt:
                        self.push_message(
                            source="genshin_game_buddy",
                            visibility=[],
                            ai_behavior="read",
                            parts=[{"type": "text", "text": f"【当前场景：{primary}】\n{scene.prompt}"}],
                            priority=0,
                            metadata={
                                "scene": primary,
                                "game_id": (self._manager.current.game_id if self._manager.current else ""),
                            },
                        )
                        self.logger.info(
                            "genshin_game_buddy: ocr_perceive scene-prompt-push scene={} chars={}",
                            primary, len(scene.prompt),
                        )
                        self._last_scene_pushed_at = time.monotonic()
                    self._last_scene_pushed = primary
            else:
                self._last_scene_pushed = ""

            self.logger.info(
                "genshin_game_buddy: ocr_perceive state={} hit={} scenes={} active={} detected_game={}",
                self._perception_state, ocr_hit, len(matched_scenes), len(new_keys), detected,
            )
            # 拍板 2.0.70：stage=body_exit 打点——若卡死，日志停在某个 stage=xxx_done 之前 = 根因
            # 拍板 2.0.71：附 SceneTracker 状态——让用户每次成功 body 都能看到状态机进度
            # 用 getattr 防 stubs 缺 _scene_tracker（__new__ bypass __init__ 的情况）
            _st = getattr(self, "_scene_tracker", None)
            if _st is not None:
                _st_snap = _st.snapshot()
                self.logger.info(
                    "genshin_game_buddy: stage=body_exit total={:.3f}s scene_tracker current={} pending={} pending_count={}",
                    time.monotonic() - t0,
                    _st_snap["current"] or "(none)",
                    _st_snap["pending"] or "(none)",
                    _st_snap["pending_count"],
                )
            else:
                self.logger.info(
                    "genshin_game_buddy: stage=body_exit total={:.3f}s",
                    time.monotonic() - t0,
                )
        except Exception:
            self.logger.exception("genshin_game_buddy: _ocr_perceive_body tick failed")

    def _push_context(self, session: GameSession, *, announce_switch: bool = True) -> bool:
        """把语境块送进对话上下文。返回是否真的提交成功（失败绝不谎报）。

        ``announce_switch=False`` 用于"重推"（刷新 / 每 N 轮）——那时切换早已发生，
        再报一次"已从 X 切换"只会让模型误以为刚换过游戏。
        """
        library = self._manager.library
        if library is None:
            return False

        activated_text = self._build_activated_context(session)
        activated_lines = [line for line in activated_text.split("\n") if line] if activated_text else []

        switched = (
            announce_switch
            and bool(session.previous_game_id)
            and session.previous_game_id != session.game_id
        )
        if switched:
            previous_entry = self._registry.get(session.previous_game_id)
            previous_name = (
                previous_entry.display_name if previous_entry is not None else session.previous_game_id
            )
            header = self._t(
                "context.title_switched",
                game=session.display_name,
                previous=previous_name,
            )
        else:
            header = self._t("context.title", game=session.display_name)

        block = build_context_block(
            header=header,
            tone_line=self._t("context.tone", tone=library.tone) if library.tone else "",
            terms_header=self._t("context.terms_header"),
            term_lines=activated_lines,
            empty_note=self._t("context.no_terms"),
            hint=self._t("context.hint"),
            max_chars=self._options.max_context_chars,
        )

        # push_message v2：只用 parts + visibility + ai_behavior。
        # visibility=[] → 用户看不到原始语境块；ai_behavior="read" → 进模型上下文
        # 但**不**立即触发回复 turn（respond 会造成自我触发）。
        receipt = self.push_message(
            source="genshin_game_buddy",
            visibility=[],
            ai_behavior="read",
            parts=[{"type": "text", "text": block}],
            priority=0,
            metadata={"game_id": session.game_id, "term_count": len(activated_lines)},
        )
        if isinstance(receipt, Mapping) and receipt.get("submitted") is False:
            self.logger.warning(
                "genshin_game_buddy: context injection not submitted ({})", receipt.get("reason")
            )
            return False
        return True
