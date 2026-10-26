"""运行时：装配大脑、注入 LLM、配置管理、启动 tick。"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Dict, Optional

from ..adapters import ImageRenderer, LLMProvider, PhotoBridge, PushSender, TTSClient
from .brain import GameBrain
from .config_manager import ConfigManager, resolve_data_dir
from .help import HelpRenderer, RenderBridge
from .registry import GameRegistry

log = logging.getLogger("neko_arcade.runtime")


class ArcadeRuntime:
    """插件运行时。"""

    def __init__(self, plugin: Any) -> None:
        self.plugin = plugin
        self.cfg: Dict[str, Any] = {}
        # 数据目录走 SDK storage dir(0.9.0.2+ 插件代码目录不含 data/), 老宿主回退代码目录旁 data/
        self.data_dir = resolve_data_dir(plugin)
        self.cfg_mgr = ConfigManager(self.data_dir)
        self.registry = GameRegistry(plugin, self.cfg_mgr)
        self.llm = LLMProvider(15)
        self.llm_gateway: Any = None
        #: 外部素材库（塔罗牌面等大素材不进包，首次运行按需下载到缓存）
        self.assets: Any = None
        self.push = PushSender(plugin)
        self.img = ImageRenderer()
        self.tts = TTSClient(plugin)
        # 发图桥接: 插件主体通用发图能力, 注入所有游戏(游戏 self.send_photo 走桥接)
        self.photo = PhotoBridge(plugin, push=self.push, img=self.img)
        # 渲染桥接: 插件主体统一的图片渲染能力(帮助图/面板图/结果卡)。
        # 「游戏适配插件」——游戏只给数据, HTML/CSS/主题/浏览器全在插件侧。
        self.render_bridge = RenderBridge(
            renderer=HelpRenderer(self.img, self._code_dir(), self._help_cache_dir(),
                                  config_manager=self.cfg_mgr),
            config_manager=self.cfg_mgr, push_sender=self.push)
        self.brain: Optional[GameBrain] = None
        self._tick_task: Optional[asyncio.Task] = None

    def _code_dir(self) -> str:
        """插件代码目录(只读, 放静态素材与默认配置)。"""
        return str(getattr(self.plugin, "plugin_dir", "") or "")

    def _help_cache_dir(self) -> str:
        """渲染缓存目录(插件私有缓存区; 老宿主不可用时退化为不缓存)。"""
        cache_path = getattr(self.plugin, "cache_path", None)
        if callable(cache_path):
            try:
                return str(cache_path("help"))
            except Exception as exc:
                log.debug("cache_path 不可用, 帮助图不缓存: %s", exc)
        return ""

    async def start(self) -> int:
        cfg = await self.plugin.config.dump()
        # 兼容两种形状: {"neko_arcade": {...}} 段 或 扁平配置
        if isinstance(cfg, dict):
            self.cfg = cfg.get("neko_arcade", cfg)
        else:
            self.cfg = {}
        self.llm = LLMProvider(self.cfg.get("llm_max_calls_per_minute", 15))
        # token 统计落盘: 写插件自身 store(键 game_user_data:llm_stats), 供 UI 查询
        try:
            store = self.plugin.store

            def _persist_stats(s: Dict[str, Any]) -> None:
                try:
                    asyncio.get_event_loop().create_task(
                        store.set("game_user_data:llm_stats", s)
                    )
                except Exception:
                    pass
            self.llm.set_persist(_persist_stats)
        except Exception as exc:
            log.warning("设置 token 统计落盘失败: %s", exc)
        self.brain = GameBrain(self.plugin, self.registry, self.cfg,
                               self.llm, self.push, self.img, self.cfg_mgr, self.tts)
        self._wire_llm()
        # 陪伴层(带宿主猫娘人格)注入注册表 → 游戏注册时拿到
        self.registry._companion = getattr(self.brain, "companion", None)
        # 统一 LLM 入口: 所有游戏的 call_llm 都走它(场景缓存/限流/token 统计)
        from .llm_gateway import LLMGateway
        self.llm_gateway = LLMGateway(self.llm)
        self.registry._llm_gateway = self.llm_gateway
        # 将插件服务注入注册表，游戏注册后可调用
        self.registry._push = self.push
        self.registry._img = self.img
        self.registry._render = self.render_bridge
        self.registry._tts = self.tts
        self.registry._llm = self.llm
        self.registry._photo = self.photo
        # 外部素材库：塔罗牌面等大素材不进包 → 首次运行后台拉一次，之后走本地缓存。
        # 配置: asset_base_url(主源) / asset_mirrors(备源) / asset_auto_download /
        #       asset_prefetch / asset_cache_mb
        from adapters.asset_store import build_store_from_cfg
        self.assets = build_store_from_cfg(
            self.plugin, self.cfg,
            bundled_manifest=os.path.join(self._code_dir(), "assets.manifest.json"),
            logger=log)
        self.registry._assets = self.assets
        if self.cfg.get("asset_prefetch", True):
            self.assets.start_prefetch()
        count = await self.registry.discover()
        self._register_dynamic_llm_tool()
        self._tick_task = asyncio.create_task(self._tick_loop())
        log.info("猫娘小游戏就绪，加载 %d 个小游戏", count)
        return count

    def _register_dynamic_llm_tool(self) -> None:
        """动态注册 send_photo 工具（play_game 已用 @llm_tool 静态注册, 见 __init__.py）。

        send_photo 需要运行时确定 neko_photo 是否存在, 故动态注册。
        """
        plugin = self.plugin
        register = getattr(plugin, "register_llm_tool", None)
        if not register:
            log.info("宿主不支持 register_llm_tool，跳过动态工具")
            return
        self._register_send_photo_tool(plugin, register, getattr(plugin, "unregister_llm_tool", None))

    def _register_send_photo_tool(self, plugin, register, unregister) -> None:
        """注册 send_photo 工具: 猫娘聊天中自主随机发图。

        不依赖用户关键词 —— 猫娘想分享心情/活跃气氛/给主人惊喜时自行调用。
        工具描述刻意写得不强制: 让 LLM 自然决定频率。
        """
        photo = self.registry.get("neko_photo")
        if not photo:
            return
        if unregister:
            try:
                unregister("send_photo")
            except Exception as exc:
                log.warning("注销静态 send_photo 工具失败: %s", exc)
        description = (
            "给主人发一张照片时调用。适合这些时刻: 聊到开心想分享心情、"
            "气氛有点安静想活跃一下、想给主人一个惊喜、或者主人提到想看你的照片/图片时。"
            "**主人明确说「发图/来张图/来一张/照片/自拍/喵图/给我看看/看图」时，"
            "必须立即调用本工具，不要闲聊绕开**。"
            "调用后你会随机发一张照片给主人(可能是你的表情自拍, 也可能是主人上传的图库照片), "
            "不要连续多次调用, 一次聊天里发一两张就够了。"
            "频率由你自己拿捏: 想发就发, 但插件会拦下过密的调用(会返回 limited 提示), "
            "收到提示就说明刚发过, 别再接着调用。"
        )
        try:
            register(
                name="send_photo",
                description=description,
                parameters={
                    "type": "object",
                    "properties": {
                        "input": {"type": "string",
                                  "description": "可选的配文/发图理由, 不填则猫娘自动配文"},
                    },
                    "required": [],
                },
                handler=plugin.tool_send_photo,
            )
            log.info("已动态注册 send_photo 工具(猫娘聊天中自主发图)")
        except Exception as exc:
            log.error("动态注册 send_photo 工具失败: %s", exc)

    # 弱指令(无关键词、无会话时的「接着玩」意图) → 路由到最近玩过的游戏
    # 只收明确的"接着玩"意图词, 避免「又」「想玩」「换个」等宽泛词误伤闲聊
    _WEAK_PLAY_HINTS = (
        "再来一局", "再来", "继续玩", "继续", "接着玩", "接着", "再玩一次",
        "还想玩", "玩点什么", "随便玩", "来一局", "来一把", "开一局", "再开",
    )

    def parse_input(self, input: str) -> tuple[Optional[str], str]:
        """解析用户输入，返回 (game_id, cmd)。

        规则(按优先级)：
        1. 当前会话游戏优先：若正在玩某游戏，且输入命中该游戏关键词 → 路由回去。
           (修复「商店」等词被钓鱼截胡——用户玩修仙时发「商店」应进修仙市场)
        2. 显式切游戏：输入命中**其他**游戏关键词 → 切过去(如玩修仙时发「钓鱼」应切钓鱼)。
        3. current_game 兜底：输入不含任何游戏关键词时回当前游戏(多轮续接, 如
           「随机」「0 1 2」等由游戏自身语义判断)。
        4. last_game 弱指令兜底：无会话时用户说「再来一局/继续玩/玩点什么」等
           模糊指令, 路由到最近玩过的游戏, 并把 cmd 换成该游戏的启动指令
           (各游戏 keywords[0] 即启动指令, 如「钓鱼」「人生重开」), 让 LLM
           拿到能真正执行的指令而不是"没有找到匹配的游戏"。

        确认/催促词(可以/帮我)不在此硬编码——由 LLM 判断并决定调用 play_game 传什么
        (工具描述已指导: 多轮选择时用户说「可以/随机」→ 传「随机」)。
        """
        input = input.strip()
        if not input:
            return None, ""

        def _normalize(game, raw: str) -> tuple[str, str]:
            """命中游戏关键词后, 把「玩X/来玩X/我要玩X」等口语前缀归一到游戏启动词。

            parse_input 的关键词匹配是子串匹配(「玩塔罗牌」命中「塔罗牌」), 但游戏
            handle_action 多用精确匹配——若把「玩塔罗牌」原样传过去, 游戏不认会返回
            unknown。归一化: raw 仅含口语玩意图(玩/来玩/我要玩/想玩)时, 换成该游戏
            启动词(keywords[0], 如「钓鱼」「人生重开」); 含具体指令(如「钓鱼3次」)
            则保留原样。
            """
            clean = raw.strip()
            # 剥掉「玩/来玩/我要玩/想玩/来把」前缀, 看剩下是否还有具体指令
            for p in ("我要玩", "我想玩", "来玩", "来把", "来一局", "玩一下", "玩", "想玩"):
                if clean.startswith(p):
                    rest = clean[len(p):].strip()
                    if rest and rest not in game.get_keywords():
                        return game.id, rest  # 有具体指令(如「塔罗牌 恋人」)
                    return game.id, (game.get_keywords()[0] if game.get_keywords() else rest or raw)
            return game.id, raw

        cur = self.brain.current_game if (self.brain and self.brain.current_game) else None

        # 1. 当前游戏优先
        if cur:
            cur_game = self.registry.get(cur)
            if cur_game:
                for kw in cur_game.get_keywords():
                    if kw and kw in input:
                        return _normalize(cur_game, input)

        # 2. 全局关键词(先到先得, 允许切游戏)
        for game in self.registry.games:
            if cur and game.id == cur:
                continue  # 当前游戏已在上面查过
            for kw in game.get_keywords():
                if kw and kw in input:
                    return _normalize(game, input)

        # 3. current_game 兜底(多轮续接: 数字/随机等无关键词输入)
        if cur:
            return cur, input

        # 4. last_game 弱指令兜底: 无会话 + 弱指令意图词 → 回最近游戏并换启动指令
        last = self.brain.last_game if (self.brain and self.brain.last_game) else None
        if last and self.registry.get(last):
            for hint in self._WEAK_PLAY_HINTS:
                if hint in input:
                    game = self.registry.get(last)
                    kws = game.get_keywords()
                    start = kws[0] if kws else input
                    return last, start
        return None, input

    def _wire_llm(self) -> None:
        """主插件 LLM 接口：配置了 → 所有游戏走新 LLM；没配 → 宿主。

        (用户要求: 配置优先于宿主, 而不是宿主优先。)
        配置来源: data/config/main/config.json 的 llm 段
        (兼容旧键: neko_arcade.llm_main_* 插件配置)。
        """
        if not self.brain:
            return
        provider = model = api_key = base_url = ""
        try:
            main_cfg = self.cfg_mgr.load_main_config()
            if isinstance(main_cfg, dict):
                llm = main_cfg.get("llm") or {}
                if isinstance(llm, dict):
                    provider = str(llm.get("provider", "") or "")
                    model = str(llm.get("model", "") or "")
                    api_key = str(llm.get("api_key", "") or "")
                    base_url = str(llm.get("base_url", "") or "")
        except Exception:
            pass
        if not provider or not model:
            # 兼容旧配置(plugin 配置 neko_arcade.llm_main_*)
            provider = self.cfg.get("llm_main_provider", "")
            model = self.cfg.get("llm_main_model", "")
            api_key = self.cfg.get("llm_main_api_key", "")
            base_url = self.cfg.get("llm_main_base_url", "")
        if provider and model:
            self.llm.set_client(provider, model, api_key, base_url)
            log.info("已配置 LLM: %s/%s (所有游戏走此接口)", provider, model)
            return
        # 宿主不提供「插件直调 LLM」的 API(官方插件从不直接调宿主 LLM——
        # 它们靠 @llm_tool / @plugin_entry 返回 summary 让宿主演绎, 本插件同样
        # 通过 entry 的 llm_result_fields=["summary"] 通道实现)。未配置自建 LLM
        # 时情感渲染降级到预制模板, 不影响对话(猫娘自然回应由宿主 LLM 按 summary 生成)。
        log.info("未配置自建 LLM, 情感渲染用模板兜底(对话由宿主按 summary 演绎)")

    async def shutdown(self) -> None:
        if self.assets is not None:
            try:
                await self.assets.stop()      # 取消后台资源预热
            except Exception as exc:          # noqa: BLE001
                log.debug("资源预热取消异常: %s", exc)
        if self._tick_task:
            self._tick_task.cancel()
            self._tick_task = None
        for game in self.registry.games:
            try:
                await game.on_unload()
            except Exception as exc:
                log.warning("卸载 %s 异常: %s", game.id, exc)

    async def _tick_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(1)
                if self.brain:
                    await self.brain.tick()
        except asyncio.CancelledError:
            pass

    async def dispatch(self, game_id: str, cmd: str, args: Optional[Dict],
                       user_id: str) -> Dict[str, Any]:
        if not self.brain:
            return {"message": "大脑还没准备好喵"}
        return await self.brain.handle_action(game_id, cmd, args, user_id)
