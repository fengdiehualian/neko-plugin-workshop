"""UI 操作：前端面板入口 @plugin_entry + @ui.action。

⚠️ 全部入口都标 agent_hidden=True——它们只供宿主前端面板 callEntry 直调，
**绝不暴露给宿主 task_executor 的 LLM 自动路由**（对齐 neko_arcade play_game 的
做法）。宿主 task_executor 会把非 agent_hidden 的 plugin_entry 当"user_plugin 任务"
暴露给 LLM 自动路由：一旦游戏指令被路由成宿主 run，就用宿主 run 语义判定完成
（entry 返回即 succeeded / 长任务超时错乱），绕过"插件自管执行、真完成才由
宿主 LLM 生成播报"的 mc 对齐模型。agent_hidden 不影响面板 callEntry / @ui.action。
"""

from typing import Any

from plugin.sdk.plugin import Ok, plugin_entry, ui


class UiActionsMixin:
    _agent: Any

    @plugin_entry(
        id="get_dashboard_state",
        name="获取面板状态",
        description="返回猫娘当前状态：连接/世界/正在做什么/任务/日志等，供前端轮询刷新。",
        input_schema={"type": "object", "properties": {}},
        metadata={"agent_hidden": True},
    )
    async def act_get_dashboard_state(self, **_):
        """统一状态入口，供前端轮询。

        精简版：前端聚焦「猫娘在做什么 + 指挥 + 日志」，
        不再返回血量/魔力/位置/背包/箱子等前端无用的重数据。
        （后端 _state/_inv_full 缓存仍由事件推送维护，AI 行为不受影响。）
        """
        from ..core.config_store import load_user_config

        # 连接配置（默认值由 config_store.DEFAULTS 统一管理）
        cfg = load_user_config()
        connection = {
            "game_path": cfg["game_path"],
            "character_name": cfg["character_name"],
            "server_host": cfg["server_host"],
            "server_port": cfg["server_port"],
            "server_password": cfg["server_password"],
            "mod_host": cfg["mod_host"],
            "mod_port": cfg["mod_port"],
            "ai_mod_port": self._agent.conn.mod_port if self._agent else 9877,
            "llm_main_provider": cfg.get("llm_main_provider", ""),
            "llm_main_model": cfg.get("llm_main_model", ""),
            "llm_main_api_key_masked": "****" if cfg.get("llm_main_api_key") else "",
            "llm_main_base_url": cfg.get("llm_main_base_url", ""),
            "llm_intent_provider": cfg.get("llm_intent_provider", ""),
            "llm_intent_model": cfg.get("llm_intent_model", ""),
            "llm_intent_api_key_masked": "****" if cfg.get("llm_intent_api_key") else "",
            "llm_intent_base_url": cfg.get("llm_intent_base_url", ""),
            "llm_max_calls_per_minute": cfg.get("llm_max_calls_per_minute", 15),
        }

        # Agent 未初始化
        if self._agent is None:
            return Ok({
                "connected": False,
                "connection": connection,
                "world": {"time": "未知"},
                "log": [],
                "current_goal": "",
                "doing": "未初始化",
                "task": None,
                "longterm": [],
                "thinking": {},
            })

        # 连接状态
        connected = self._agent.running

        # 世界状态（Mod 回传完整信息：名称/难度/大小/邪恶/版本）
        st = self._agent.get_state() if connected else {}
        wi = self._agent._world_info if connected else {}
        game_mode_names = {0: "经典", 1: "专家", 2: "大师", 3: "旅途"}
        world = {
            "world_name": wi.get("world_name", self._config.get("world_name", "")),
            "game_mode": game_mode_names.get(wi.get("game_mode", 0), "未知"),
            "world_size": wi.get("world_size", ""),
            "evil_type": wi.get("evil_type", ""),
            "time": st.get("time_of_day", "未知"),
        }

        # 日志（最近20条）
        log = self._agent.get_log_sync()[-20:] if connected else []

        # 猫娘在做什么（一句话）+ 前台任务 + 长期任务 + 最近思考
        doing = self._agent.coordinator.say() if connected else "未连接"
        task = self._agent.executor.current() if connected else None
        longterm = self._agent.longterm.active() if connected else []
        thinking = {}
        if connected:
            try:
                a = self._agent.brain.last_assessment()
                if a is not None:
                    thinking = {"doable": a.doable, "say": a.say(),
                                "thoughts": a.thoughts, "fixes": a.fixes,
                                "need": a.need_from_owner}
            except Exception:
                pass

        return Ok({
            "connected": connected,
            "connection": connection,
            "world": world,
            "log": log,
            "current_goal": self._agent.current_goal if connected else "",
            "doing": doing,
            "task": task,
            "longterm": longterm,
            "thinking": thinking,
        })

    @plugin_entry(
        id="nt_status",
        name="获取连接状态",
        description="兼容入口：供 N.E.K.O 框架插件管理页面调用",
        input_schema={"type": "object", "properties": {}},
        metadata={"agent_hidden": True},
    )
    async def act_get_status(self, **_):
        """简化状态入口"""
        if self._agent is None:
            return Ok({"connected": False, "msg": "未初始化"})

        connected = self._agent.running
        return Ok({
            "connected": connected,
            "msg": "已连接" if connected else "未连接"
        })

    @plugin_entry(
        id="nt_connect",
        name="连接游戏",
        description="启动猫娘并连接 Terraria 服务端/mod 接口。",
        input_schema={"type": "object", "properties": {}},
        metadata={"agent_hidden": True},
    )
    async def act_connect(self, **_):
        if self._agent is None:
            return Ok({"connected": False, "msg": "猫娘还没准备好，稍等"})
        if self._agent.running:
            return Ok({"connected": True, "msg": "我已经在游戏里啦~"})
        # 启动是长任务（拉起 tModLoader 进程 + 入服，可能超过前端 30s 超时），
        # 后台执行，前端通过轮询的 connected 字段看到真实结果
        import asyncio
        try:
            asyncio.get_running_loop().create_task(self._agent.start())
        except Exception:
            return Ok({"connected": False, "msg": "启动指令发送失败，看看日志"})
        return Ok({"connected": False, "msg": "正在启动并连接游戏，请稍候（面板会自动更新状态）"})

    @plugin_entry(
        id="nt_command",
        name="下达指令",
        description="前端面板下达游戏指令：如「挖10个铁」「跟着我」「去箱子拿把镐子」。",
        input_schema={
            "type": "object",
            "properties": {
                "text": {"type": "string",
                         "description": "给猫娘的游戏指令（自然语言）"},
            },
            "required": ["text"],
        },
        metadata={"agent_hidden": True},
    )
    async def act_command(self, text: str = "", **_):
        """前端 → 猫娘指令：走与 LLM 工具相同的 coordinator 管道。"""
        import logging
        logger = logging.getLogger(__name__)

        logger.info(f"📥 [act_command] 收到指令: {text}")
        text = (text or "").strip()
        if not text:
            logger.warning("⚠️ [act_command] 指令为空")
            return Ok({"ok": False, "msg": "指令不能为空喵~"})
        if self._agent is None:
            logger.warning("⚠️ [act_command] agent 未初始化")
            return Ok({"ok": False, "msg": "猫娘还没准备好"})
        if not self._agent.running:
            logger.warning(f"⚠️ [act_command] 游戏未连接，running={self._agent.running}")
            return Ok({"ok": False, "msg": "还没连接游戏，先点「连接游戏」喵~"})
        try:
            logger.info(f"🎯 [act_command] 调用 agent.command: text={text}")
            res = await self._agent.command(text, source="owner")
            logger.info(f"✅ [act_command] agent.command 返回: {res}")
        except Exception as e:
            logger.error(f"❌ [act_command] 执行出错: {e}", exc_info=True)
            return Ok({"ok": False, "msg": f"指令出错：{e}"})

        # 任务受理语义（关键）：无论长短任务，这都只是"下达成功"确认。
        # 任务生命周期（进行中/进度/完成/失败）由插件通过 push_message 主动推送，
        # 猫娘绝不能把这句当"任务已完成"。
        status = res.get("status", "")
        output_msg = res.get("output", "收到喵~")
        if status in ("ok", "running", "started", "longterm"):
            output_msg = (
                f"✅ 已受理：{output_msg}\n"
                f"⏳ 这条消息只代表任务下达成功，任务仍在执行中，"
                f"我会在游戏里继续做，完成后会向主人汇报喵~"
            )

        logger.info(f"📤 [act_command] 返回结果: ok=True, msg={output_msg}")
        return Ok({
            "ok": True,
            "msg": output_msg,
            "status": status,
            "result": res,
        })

    @plugin_entry(
        id="nt_stop",
        name="断开连接",
        description="断开猫娘与 Terraria 世界的连接。",
        input_schema={"type": "object", "properties": {}},
        metadata={"agent_hidden": True},
    )
    async def act_stop(self, **_):
        if self._agent is not None:
            await self._agent.stop()
        return Ok({"stopped": True, "msg": "已断开连接"})

    @plugin_entry(
        id="nt_save_config",
        name="保存连接配置",
        description="保存游戏启动、服务器连接、Mod 接口和 LLM 配置。",
        input_schema={
            "type": "object",
            "properties": {
                "game_path": {"type": "string"},
                "character_name": {"type": "string"},
                "server_host": {"type": "string"},
                "server_port": {"type": "integer"},
                "server_password": {"type": "string"},
                "mod_host": {"type": "string"},
                "mod_port": {"type": "integer"},
                "llm_main_provider": {"type": "string"},
                "llm_main_model": {"type": "string"},
                "llm_main_api_key": {"type": "string"},
                "llm_main_base_url": {"type": "string"},
                "llm_intent_provider": {"type": "string"},
                "llm_intent_model": {"type": "string"},
                "llm_intent_api_key": {"type": "string"},
                "llm_intent_base_url": {"type": "string"},
                "llm_max_calls_per_minute": {"type": "integer"},
                "llm_emergency_reserve": {"type": "integer"},
            },
            "required": [],
        },
        metadata={"agent_hidden": True},
    )
    @ui.action(id="nt_save_config", label="保存配置", refresh_context=True)
    async def act_save_config(self, **kwargs):
        from ..core.config_store import save_user_config

        patch = {}
        for key in ("game_path", "character_name",
                     "server_host", "server_port", "server_password",
                     "mod_host", "mod_port",
                     "llm_main_provider", "llm_main_model", "llm_main_api_key", "llm_main_base_url",
                     "llm_intent_provider", "llm_intent_model", "llm_intent_api_key", "llm_intent_base_url",
                     "llm_max_calls_per_minute", "llm_emergency_reserve"):
            if key in kwargs:
                val = kwargs[key]
                if key.endswith("_api_key") and val == "****":
                    continue
                patch[key] = val

        save_user_config(patch)

        for k, v in patch.items():
            self._config[k] = v
        if self._agent is not None:
            self._agent.cfg = self._config

        return Ok({"saved": True, "msg": "配置已保存"})
