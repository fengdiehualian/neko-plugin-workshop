"""猫娘小游戏：游戏大脑总管家。"""

from __future__ import annotations

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
)

PUSH_SOURCE = "neko_arcade"
STORE_PREFIX = "game_user_data"


@neko_plugin
class NekoArcadePlugin(NekoPluginBase):
    """猫娘小游戏 —— 大脑总管家。"""

    def __init__(self, ctx: Any) -> None:
        super().__init__(ctx)
        self.logger = self.enable_file_logging(log_level="INFO")
        self.rt: Any = None

    # ui.context 由 plugin.toml 静态配置，装饰器在运行时动态注册
    async def ctx_dashboard(self) -> dict:
        """Hosted UI / static 面板共用的状态入口。"""
        if not self.rt:
            return {"games": [], "statuses": {}, "brain": {}, "started": False}
        return await self.entry_get_arcade_state()

    @lifecycle(id="startup")
    async def startup(self, **_) -> Any:
        # 延迟导入，避免模块加载时级联导入导致部分加载
        from .core import ArcadeRuntime

        # 静态 UI 必须在同步段注册，路由才能找到 /plugin/neko_arcade/ui/；
        # plugin.toml [[plugin.ui.panel]] entry 指向 static/index.html。
        if (self.config_dir / "static").exists():
            ok = self.register_static_ui(
                "static",
                index_file="index.html",
                cache_control="no-cache, no-store, must-revalidate",
            )
            if ok:
                self.logger.info("已注册猫娘小游戏面板: /plugin/neko_arcade/ui/")
            else:
                self.logger.warning("注册静态 UI 失败，请检查 static/index.html 是否存在")
        self.rt = ArcadeRuntime(self)
        count = await self.rt.start()
        return Ok({"status": "ready", "games_loaded": count, "brain": True})

    @lifecycle(id="shutdown")
    async def shutdown(self, **_) -> Any:
        if self.rt:
            await self.rt.shutdown()
        return Ok({"status": "shutdown"})

    @message(id="chat_activity", source="chat")
    async def on_chat_activity(self, **_) -> Any:
        """主人说话信号: 宿主在每次用户聊天时触发(参考 neko_warthunder)。

        刷新 brain 的活动状态——后台自动发图/游戏会话锚都依赖"主人最近是否
        活跃"来判断是否应该动作, 避免插件在无人聊天时刷屏或发图。
        """
        if self.rt and self.rt.brain:
            await self.rt.brain.on_owner_speak()
        return Ok({"status": "observed"})

    async def store_get_user(self, game_id: str, user_id: str, default: Any = None) -> Any:
        key = f"{STORE_PREFIX}:{game_id}:{user_id}"
        val = await self.store.get(key)
        # SDK store.get 返回 Ok/Err 包装（数据在 Ok.value），统一解开再返回
        return self.unwrap_result(val, default=default)

    @staticmethod
    def unwrap_result(val: Any, default: Any = None) -> Any:
        """解开 SDK 的 Ok/Err 包装（兼容 value / data 两种字段名）。"""
        if val is None:
            return default
        # Err 包装 → 视为无数据，用默认值
        is_err = getattr(val, "is_err", None)
        if callable(is_err) and is_err():
            return default
        # Ok(...).value（SDK v2） / Ok(...).data（旧版兼容）
        for field in ("value", "data"):
            if hasattr(val, field):
                inner = getattr(val, field)
                return inner if inner is not None else default
        return val

    async def store_save_user(self, game_id: str, user_id: str, data: Any) -> None:
        key = f"{STORE_PREFIX}:{game_id}:{user_id}"
        await self.store.set(key, data)

    @plugin_entry(id="list_games", name="游戏列表", description="列出所有可玩的小游戏名(按需查询, 一次调用即可, 不要反复调用)。",
                  input_schema={"type": "object", "properties": {}},
                  # agent_hidden: 查询类入口不参与 LLM 自动路由——宿主评估器若看到
                  # list_games/game_status 会把"玩钓鱼"路由成"先查列表", 而不是调
                  # play_game。LLM 路由只留 play_game 一个游戏入口(见其注释)。
                  metadata={"agent_hidden": True},
                  llm_result_fields=["message"])
    async def entry_list_games(self, **_) -> Any:
        if not self.rt:
            return Err(SdkError("猫娘小游戏还没准备好"))
        games = self.rt.registry.get_meta_list()
        if not games:
            return Ok({"games": [], "message": "暂时没有小游戏喵"})
        names = "、".join(f"{g['icon']} {g['name']}" for g in games)
        return Ok({"games": [{"id": g["id"], "name": g["name"]} for g in games],
                   "message": f"可玩的小游戏：{names}。想玩哪个说游戏名就行喵"})

    @plugin_entry(id="play_game", name="玩游戏",
                  description="猫娘小游戏的唯一游戏入口。用户说想玩小游戏、提到游戏名(塔罗牌/占卜/钓鱼/人生重开/猜硬币/海龟汤/修仙/俄罗斯轮盘/历史上的今天/猫猫进化路等)、要发图/看照片(发图/来张图/来一张/照片/自拍/喵图/图库/图鉴等)、或对当前游戏下指令(抛竿/开始/继续/再来一局/帮助)时，直接调用本入口，传用户原话，插件自动匹配游戏并执行。喵图相册是内置小游戏：用户要图就发图，说「图库」列分类，说「喵图 分类名」发指定分类。不要先查游戏列表或状态。",
                  input_schema={"type": "object", "properties": {
                      "input": {"type": "string", "description": "用户说的原话，如「塔罗牌」「占卜」「钓鱼」「抛竿」「人生重开」「发图」「来张图」"},
                  }, "required": ["input"]},
                  # 宿主按 summary 字段拼装任务结果喂给对话 LLM，猫娘才能对游戏结果有反馈。
                  # ⚠️ 这是唯一对宿主 LLM 路由可见的 entry——list_games/game_status/game_help
                  # 都已 agent_hidden(宿主评估器会把"玩钓鱼"错路由成"先查列表/状态")。
                  llm_result_fields=["summary"])
    async def entry_play_game(self, input: str = "", game: str = "", cmd: str = "",
                              switch_to: str = "", args: dict = None, **_) -> Any:
        if not self.rt or not self.rt.brain:
            return Err(SdkError("猫娘小游戏还没准备好"))
        args = dict(args or {})
        switch_to = str(switch_to or args.get("switch_to") or "").strip()
        if switch_to:
            args["switch_to"] = switch_to
        # 兼容两种调用: 新面板/内部传 input(原话) → parse_input 自动匹配;
        # 旧面板传 game+cmd → 直接定位。LLM 走动态 play_game 工具 → tool_play_game
        # → entry_play_game(input=...) → 这里 parse_input 路由(含弱指令兜底)。
        # ⚠️ 防呆: 宿主任务路由偶尔会把 schema 的**参数名**当值传进来
        #    （实测日志 `没有找到游戏「input」喵` = game 收到了字符串 "input"）。
        #    这时只要 input 原话在，就忽略坏的 game，继续按原话路由。
        if switch_to:
            # 已确认换游戏：不需要 route 当前输入，目标由 switch_to 指定。
            game_id, cmd = "", (input or "")
        elif game:
            game_obj = self.rt.registry.get(str(game)) or self.rt.registry.get_by_name(str(game))
            if game_obj is None:
                # 宿主有时把用户原话塞在 cmd 里、game 里塞了参数名 → 两个都试。
                raw = str(input or cmd or "").strip()
                if not raw:
                    return Err(SdkError(f"没有找到游戏「{game}」喵"))
                self.logger.warning(
                    "[play_game] game=%r 不是已知游戏，改用 input=%r 路由", game, raw)
                game_id, cmd = self.rt.parse_input(raw)
                if not game_id:
                    return Err(SdkError(
                        f"没有找到匹配的游戏喵，试试：{self.rt.registry.game_names()}"))
            else:
                game_id, cmd = game_obj.id, (cmd or "")
        elif cmd:
            # 只给了 cmd（没有 game）：按原话路由
            game_id, cmd = self.rt.parse_input(str(cmd))
            if not game_id:
                return Err(SdkError(
                    f"没有找到匹配的游戏喵，试试：{self.rt.registry.game_names()}"))
        else:
            game_id, cmd = self.rt.parse_input(input or "")
            if not game_id:
                return Err(SdkError(
                    f"没有找到匹配的游戏喵，试试：{self.rt.registry.game_names()}"))
        user_id = getattr(self.ctx, "user_id", "default") or "default"
        result = await self.rt.brain.handle_action(game_id, cmd, args, user_id)
        # 兜底：确保返回给宿主的 dict 一定带可读 summary（宿主按 llm_result_fields 提取）
        if isinstance(result, dict):
            result.setdefault("summary",
                              result.get("game_result") or result.get("message")
                              or result.get("game_docs") or "")
        return Ok(result)

    @plugin_entry(id="start_game", name="启动游戏", description="仅用于面板手动启动游戏会话。",
                  input_schema={"type": "object", "properties": {"game": {"type": "string"}}, "required": ["game"]},
                  metadata={"agent_hidden": True})
    async def entry_start_game(self, game: str = "", **_) -> Any:
        if not self.rt or not self.rt.brain:
            return Err(SdkError("猫娘小游戏还没准备好"))
        result = await self.rt.brain.start_game(game)
        return Ok(result)

    @plugin_entry(id="stop_game", name="停止游戏", description="停止当前游戏会话。",
                  input_schema={"type": "object", "properties": {}},
                  metadata={"agent_hidden": True})
    async def entry_stop_game(self, **_) -> Any:
        if not self.rt or not self.rt.brain:
            return Err(SdkError("猫娘小游戏还没准备好"))
        result = await self.rt.brain.stop_game()
        return Ok(result)

    @plugin_entry(id="game_status", name="游戏状态", description="查看小游戏当前状态。",
                  input_schema={"type": "object", "properties": {"game": {"type": "string", "description": "游戏 id 或名称"}}, "required": ["game"]},
                  metadata={"agent_hidden": True})
    async def entry_game_status(self, game: str = "", **_) -> Any:
        if not self.rt:
            return Err(SdkError("猫娘小游戏还没准备好"))
        game_obj = self.rt.registry.get(game) or self.rt.registry.get_by_name(game)
        if not game_obj:
            return Err(SdkError(f"没有找到游戏「{game}」喵"))
        user_id = getattr(self.ctx, "user_id", "default") or "default"
        status = await game_obj.get_status(user_id)
        return Ok({"game": game_obj.id, "status": status})

    @plugin_entry(id="set_game_enabled", name="开关游戏", description="启用或停用某个小游戏。",
                  input_schema={"type": "object", "properties": {
                      "game": {"type": "string", "description": "游戏 id"},
                      "enabled": {"type": "boolean", "description": "true 启用 / false 停用"},
                  }, "required": ["game", "enabled"]},
                  metadata={"agent_hidden": True})
    async def entry_set_game_enabled(self, game: str = "", enabled: bool = True, **_) -> Any:
        if not self.rt:
            return Err(SdkError("猫娘小游戏还没准备好"))
        ok = await self.rt.registry.set_enabled(game, enabled)
        if not ok:
            return Err(SdkError(f"没有找到游戏「{game}」喵"))
        return Ok({"game": game, "enabled": enabled})

    @plugin_entry(id="game_help", name="游戏帮助",
                  description="查看并推送小游戏的帮助文档图。可只给 game 看目录页, 也可带 topic 看某个功能分组(如「战斗」「纳戒」)或单条指令。",
                  input_schema={"type": "object", "properties": {
                      "game": {"type": "string", "description": "游戏 id"},
                      "topic": {"type": "string",
                                "description": "可选功能主题: 分组名/别名(战斗、纳戒、宗门…)或指令名(装备 X)"},
                  }, "required": ["game"]},
                  metadata={"agent_hidden": True},
                  llm_result_fields=["message"])
    async def entry_game_help(self, game: str = "", topic: str = "", **_) -> Any:
        if not self.rt or not self.rt.brain:
            return Err(SdkError("猫娘小游戏还没准备好"))
        result = await self.rt.brain.show_help(game, topic=topic)
        return Ok(result)

    @plugin_entry(id="get_game_config", name="获取游戏配置",
                  description="获取某个小游戏的配置和帮助数据。",
                  input_schema={"type": "object", "properties": {
                      "game": {"type": "string", "description": "游戏 id"},
                  }, "required": ["game"]},
                  metadata={"agent_hidden": True})
    async def entry_get_game_config(self, game: str = "", **_) -> Any:
        if not self.rt:
            return Err(SdkError("猫娘小游戏还没准备好"))
        if not self.rt.registry.get(game):
            return Err(SdkError(f"没有找到游戏「{game}」喵"))
        gc = self.rt.cfg_mgr.load(game)
        panel = self.rt.registry.get_panel(game)
        return Ok({"game": game, "config": gc.config, "help": gc.help, "panel": panel})

    @staticmethod
    def _deep_merge(base: dict, update: dict) -> dict:
        """深度合并配置：update 覆盖 base，base 里未提到的键保留。"""
        out = dict(base or {})
        for k, v in (update or {}).items():
            if isinstance(v, dict) and isinstance(out.get(k), dict):
                out[k] = NekoArcadePlugin._deep_merge(out[k], v)
            else:
                out[k] = v
        return out

    @plugin_entry(id="save_game_config", name="保存游戏配置",
                  description="保存小游戏的配置参数。",
                  input_schema={"type": "object", "properties": {
                      "game": {"type": "string", "description": "游戏 id"},
                      "config": {"type": "object", "description": "配置键值"},
                  }, "required": ["game", "config"]},
                  metadata={"agent_hidden": True})
    async def entry_save_game_config(self, game: str = "", config: dict = None, **_) -> Any:
        if not self.rt:
            return Err(SdkError("猫娘小游戏还没准备好"))
        if not self.rt.registry.get(game):
            return Err(SdkError(f"没有找到游戏「{game}」喵"))
        gc = self.rt.cfg_mgr.load(game)
        merged = self._deep_merge(gc.config, config or {})
        self.rt.cfg_mgr.save(game, merged)
        return Ok({"game": game, "saved": True})

    # ── LLM 配置(UI「全部游戏」下方, 学泰拉瑞亚猫娘方式) ──────
    # 键名与 runtime._wire_llm 读取的 neko_arcade.llm_main_* 完全一致：
    # 配置了 → 自建 LLM 客户端; 没配 → 模板兜底(对话由宿主按 summary 演绎)。

    @plugin_entry(id="get_llm_config", name="获取LLM配置",
                  description="读取插件级 LLM 配置(猫娘对话用)。",
                  input_schema={"type": "object", "properties": {}},
                  metadata={"agent_hidden": True})
    async def entry_get_llm_config(self, **_) -> Any:
        """读取主插件配置 data/config/main/config.json 的 llm 段。"""
        cfg = self.rt.cfg_mgr.load_main_config() if self.rt else {}
        llm = cfg.get("llm", {}) if isinstance(cfg, dict) else {}
        help_sec = cfg.get("help", {}) if isinstance(cfg, dict) else {}
        return Ok({"config": {
            "provider": llm.get("provider", ""),
            "model": llm.get("model", ""),
            "api_key": llm.get("api_key", ""),
            "base_url": llm.get("base_url", ""),
            "max_calls_per_minute": llm.get("max_calls_per_minute", 15),
            "help_width": help_sec.get("width", ""),
        }})

    @plugin_entry(id="save_llm_config", name="保存LLM配置",
                  description="保存插件级 LLM 配置。留空则降级宿主/本地。",
                  input_schema={"type": "object", "properties": {
                      "config": {"type": "object", "description": "{provider,model,api_key,base_url}"},
                      "help_width": {"type": "integer", "description": "帮助图渲染宽度(px, 240–900)；不填保持原值"},
                  }, "required": ["config"]},
                  metadata={"agent_hidden": True})
    async def entry_save_llm_config(self, config: dict = None,
                                    help_width: Any = None, **_) -> Any:
        """写入主插件配置 data/config/main/config.json 的 llm 段。"""
        if not self.rt:
            return Err(SdkError("猫娘小游戏还没准备好"))
        cfg = dict(config or {})
        if help_width is not None:
            cfg["help_width"] = help_width
        main_cfg = self.rt.cfg_mgr.load_main_config()
        if not isinstance(main_cfg, dict):
            main_cfg = {}
        llm = {
            "provider": str(cfg.get("provider", "") or ""),
            "model": str(cfg.get("model", "") or ""),
            "api_key": str(cfg.get("api_key", "") or ""),
            "base_url": str(cfg.get("base_url", "") or ""),
        }
        if cfg.get("max_calls_per_minute"):
            try:
                llm["max_calls_per_minute"] = int(cfg["max_calls_per_minute"])
            except (TypeError, ValueError):
                pass
        main_cfg["llm"] = llm
        hw = cfg.get("help_width")
        if hw not in (None, ""):
            try:
                width = max(240, min(900, int(hw)))
                help_sec = main_cfg.get("help") if isinstance(main_cfg.get("help"), dict) else {}
                help_sec["width"] = width
                main_cfg["help"] = help_sec
            except (TypeError, ValueError):
                pass
        self.rt.cfg_mgr.save_main_config(main_cfg)
        # 热更新 LLM 客户端
        self.rt._wire_llm()
        self.logger.info("LLM 配置已保存到 data/config/main/config.json: provider=%s model=%s",
                         llm.get("provider", ""), llm.get("model", ""))
        return Ok({"saved": True})

    @plugin_entry(id="get_token_stats", name="Token使用统计",
                  description="统计本插件小游戏的 LLM token 消耗(插件自身 LLMProvider 统计)。",
                  input_schema={"type": "object", "properties": {}},
                  metadata={"agent_hidden": True})
    async def entry_get_token_stats(self, **_) -> Any:
        """返回 neko_arcade 自己产生的 LLM token 消耗(精确)。

        所有小游戏 LLM 调用都经过 LLMProvider.call(emotion 渲染/海龟汤出题/
        修仙闲聊等), 在此统一统计并按场景分组。自建客户端取真实 usage,
        宿主调用按字符估算。统计持久化在插件 store(键 game_user_data:llm_stats)。
        """
        stats = {"available": False, "note": "", "own": {}, "today": {}, "total": {}}
        try:
            # 插件自身统计(精确)
            own = {}
            try:
                raw = await self.store.get("game_user_data:llm_stats")
                if isinstance(raw, dict):
                    own = raw
            except Exception:
                own = {}
            if own.get("calls"):
                stats["own"] = {
                    "calls": own.get("calls", 0),
                    "prompt": own.get("prompt_tokens", 0),
                    "completion": own.get("completion_tokens", 0),
                    "total": own.get("total_tokens", 0),
                    "by_scene": own.get("by_scene", {}),
                }
            stats["available"] = True
            stats["note"] = "own=本插件小游戏实际产生的 LLM token(精确统计)"
            return Ok(stats)
        except Exception as exc:
            stats["note"] = f"统计失败: {exc}"
            return Ok(stats)

    @plugin_entry(id="get_arcade_state", name="获取街机状态", description="供面板轮询。",
                  input_schema={"type": "object", "properties": {}},
                  metadata={"agent_hidden": True})
    async def entry_get_arcade_state(self, **_) -> Any:
        if not self.rt:
            return Ok({"games": [], "statuses": {}, "brain": {}, "started": False,
                       "token_stats": {}})
        user_id = getattr(self.ctx, "user_id", "default") or "default"
        games = self.rt.registry.get_meta_list(with_help=True)
        statuses = await self.rt.registry.get_statuses(user_id)
        brain_snap = await self.rt.brain.snapshot() if self.rt.brain else {}
        enabled = self.rt.registry.enabled_ids()
        # token 统计: LLMProvider 内存快照(最新)优先, 无则读 store 落盘
        token_stats = {}
        try:
            if self.rt.llm:
                snap = self.rt.llm.snapshot()
                if snap.get("calls"):
                    token_stats = snap
            if not token_stats:
                raw = await self.store.get("game_user_data:llm_stats")
                if isinstance(raw, dict) and raw.get("calls"):
                    token_stats = raw
        except Exception:
            token_stats = {}
        # 主插件配置(前端轮询读取): games.json 启停 + config.json 其他配置
        main_config = {}
        try:
            main_config = {
                "game_states": self.rt.cfg_mgr.load_game_states(),
                "config": self.rt.cfg_mgr.load_main_config(),
            }
        except Exception:
            main_config = {}
        return Ok({"games": games, "statuses": statuses, "brain": brain_snap,
                   "started": True, "enabled_count": len(enabled),
                   "token_stats": token_stats, "main_config": main_config})

    @llm_tool(
        name="play_game",
        description=(
            "用户想玩小游戏、提到某个游戏名、或对当前游戏下指令时调用（如「塔罗牌」"
            "「占卜」「钓鱼」「抛竿」「人生重开」「再来一局」「帮助」等）。"
            "传入用户原话，插件自动匹配游戏并执行。\n\n"
            "调用规则：\n"
            "- 用户提到任何游戏名或游戏指令 = 明确的执行请求，直接调用本工具，"
            "**不要先问「要玩吗？」、不要自己扮演游戏流程**。\n"
            "- 用户要发图/看照片/看相册（如「发图」「来张图」「来一张」「照片」「自拍」"
            "「喵图」「图库」「图鉴」）也走本工具：传用户原话，喵图相册会自动发图，"
            "不要自己编造图片或假装发图。\n"
            "- 多轮游戏中游戏返回选择提示后，用户说「可以/好的/随机/继续/对的/嗯」等"
            "表示继续或让 AI 决定时，同样调用本工具并传「随机」或用户原话。\n"
            "- 若当前有进行中的游戏（上下文可能出现 [游戏状态] 提示），用户说的原话"
            "都应传给本工具，不要自己扮演游戏流程。\n"
            "- 若上下文出现 [游戏切换确认]（你刚问过他还在玩 X、要不要换到 Y）："
            "他表示同意换时，调用本工具并填 switch_to=目标游戏名，input 传他的原话；"
            "他表示不换或要继续原来的游戏时，不要填 switch_to。切换与否由他的意思决定，"
            "不要自己替他决定。\n"
            "- 用户没提到任何游戏玩法时不要调用。调用后直接基于工具结果回应，不要重复调用。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "input": {"type": "string",
                          "description": "用户说的原话，如「塔罗牌」「占卜」「钓鱼」「抛竿」「人生重开」"},
                "switch_to": {"type": "string",
                              "description": "仅当上下文出现 [游戏切换确认] 且用户同意换游戏时填写：目标游戏名/id（如「修仙」）。不要在其他时候填写。"},
            },
            "required": ["input"],
        },
    )
    async def tool_play_game(self, input: str, switch_to: str = "") -> Any:
        """play_game LLM 工具处理器（@llm_tool 静态注册, SDK 启动自动注册）。

        auto=True: 多轮状态机游戏(如人生重开)由 AI 代玩时一次调用走完随机流程,
        避免卡在"选天赋/分属性"等用户输入。
        """
        if not self.rt:
            return {"error": "猫娘小游戏还没准备好"}
        result = await self.entry_play_game(
            input=input, switch_to=switch_to, args={"auto": True})
        # 从 Ok/Err 包装中解出数据（SDK v2: Ok.value；旧版: Ok.data）
        return self.unwrap_result(result, default={"error": "游戏执行失败喵"})

    # ── 喵图相册: LLM 工具 + 用户上传 ─────────────────

    async def tool_send_photo(self, input: str = "") -> Any:
        """send_photo LLM 工具处理器（由 runtime._register_dynamic_llm_tool 注册）。

        猫娘在聊天中自主决定何时调用: 想分享心情/活跃气氛/给主人惊喜时,
        随机发一张图(动态猫娘表情或用户上传的图库照片)。
        """
        if not self.rt:
            return {"error": "猫娘小游戏还没准备好"}
        game = self.rt.registry.get("neko_photo")
        if not game:
            return {"error": "喵图相册还没加载喵"}
        user_id = getattr(self.ctx, "user_id", "default") or "default"
        caption = (input or "").strip()
        try:
            return await game.send_random_photo(user_id, caption=caption)
        except Exception as exc:
            self.logger.warning("send_photo 工具调用失败: %s", exc)
            return {"error": f"发图失败喵: {exc}"}

    @plugin_entry(id="upload_photo", name="上传图片",
                  description="用户上传一张图片到猫娘图库指定分类(面板用)。",
                  input_schema={"type": "object", "properties": {
                      "name": {"type": "string", "description": "文件名(带扩展名)"},
                      "data_b64": {"type": "string", "description": "图片 base64"},
                      "category": {"type": "string", "description": "分类名(子文件夹), 默认「默认」"},
                  }, "required": ["name", "data_b64"]},
                  metadata={"agent_hidden": True})
    async def entry_upload_photo(self, name: str = "", data_b64: str = "",
                                 category: str = "默认", **_) -> Any:
        if not self.rt:
            return Err(SdkError("猫娘小游戏还没准备好"))
        game = self.rt.registry.get("neko_photo")
        if not game:
            return Err(SdkError("喵图相册还没加载喵"))
        user_id = getattr(self.ctx, "user_id", "default") or "default"
        result = await game.upload_photo(user_id, name=name, data_b64=data_b64,
                                         category=category)
        return Ok(result)

    @plugin_entry(id="upload_photos", name="批量上传图片",
                  description="用户批量上传多张图片到猫娘图库指定分类(面板用)。",
                  input_schema={"type": "object", "properties": {
                      "files": {"type": "array", "description": "图片列表, 每项 {name, data_b64}"},
                      "category": {"type": "string", "description": "分类名(子文件夹), 默认「默认」"},
                  }, "required": ["files", "category"]},
                  metadata={"agent_hidden": True})
    async def entry_upload_photos(self, files: list = None,
                                  category: str = "默认", **_) -> Any:
        if not self.rt:
            return Err(SdkError("猫娘小游戏还没准备好"))
        game = self.rt.registry.get("neko_photo")
        if not game:
            return Err(SdkError("喵图相册还没加载喵"))
        user_id = getattr(self.ctx, "user_id", "default") or "default"
        results = []
        ok_count = 0
        dup_count = 0
        for item in (files or []):  # 全部处理, 不静默截断(超出部分也要如实上报)
            if not isinstance(item, dict):
                results.append({"ok": False, "name": "", "message": "无效条目"})
                continue
            r = await game.upload_photo(user_id, name=str(item.get("name", "")),
                                        data_b64=str(item.get("data_b64", "")),
                                        category=category)
            r["name"] = item.get("name", "")
            results.append(r)
            if r.get("ok"):
                ok_count += 1
            elif r.get("duplicate"):
                dup_count += 1
        return Ok({"ok": ok_count > 0, "count": len(results),
                   "succeeded": ok_count,
                   "failed": len(results) - ok_count - dup_count,
                   "duplicated": dup_count, "results": results})

    @plugin_entry(id="photo_library", name="图库状态",
                  description="查看猫娘图库分类和图片数量。",
                  input_schema={"type": "object", "properties": {}},
                  metadata={"agent_hidden": True})
    async def entry_photo_library(self, **_) -> Any:
        if not self.rt:
            return Ok({"ok": False, "count": 0, "categories": [], "uploaded": []})
        bridge = getattr(self.rt, "photo", None)
        if not bridge:
            return Ok({"ok": False, "count": 0, "categories": [], "uploaded": []})
        try:
            imgs = bridge.scan_images()
            cats = bridge.get_categories()
            by_cat = {}
            for cat in cats:
                n = len([i for i in imgs if i.get("category") == cat])
                by_cat[cat] = n
            uploaded = [i["style"] for i in imgs if i.get("source") == "local"]
            return Ok({"ok": True, "count": len(uploaded),
                       "categories": cats, "by_category": by_cat,
                       "uploaded": uploaded})
        except Exception as exc:
            return Ok({"ok": False, "count": 0, "categories": [], "uploaded": [],
                       "error": str(exc)})
