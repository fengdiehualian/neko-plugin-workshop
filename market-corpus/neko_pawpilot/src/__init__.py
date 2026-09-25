"""猫爪副驾：欧卡2遥测陪玩插件入口。"""

from __future__ import annotations

from typing import Any

from plugin.sdk.plugin import (
    Err,
    NekoPluginBase,
    Ok,
    SdkError,
    lifecycle,
    message,
    neko_plugin,
    plugin_entry,
    ui,
)

_CONFIG_SECTION = "neko_pawpilot"


@neko_plugin
class NekoPawpilotPlugin(NekoPluginBase):
    """猫爪副驾 —— 欧卡2遥测陪玩。"""

    def __init__(self, ctx: Any) -> None:
        super().__init__(ctx)
        self.logger = self.enable_file_logging(log_level="INFO")
        self.rt: Any = None

    async def _load_config(self) -> Any:
        from .core.config_model import PawpilotConfig

        raw = await self.config.dump()
        section = raw.get(_CONFIG_SECTION, {}) if isinstance(raw, dict) else {}
        return PawpilotConfig(section)

    @lifecycle(id="startup")
    async def startup(self, **_) -> Any:
        from .core import PawpilotRuntime

        try:
            # 静态 UI 注册：面板入口 static/index.html
            if (self.config_dir / "static").exists():
                self.register_static_ui("static", index_file="index.html",
                                        cache_control="no-cache, no-store, must-revalidate")
            config = await self._load_config()
            self.rt = PawpilotRuntime(self, config)
            status = await self.rt.start()
            return Ok(status)
        except Exception as exc:
            self.logger.exception("startup failed")
            return Err(SdkError(f"启动失败: {exc}"))

    @lifecycle(id="shutdown")
    async def shutdown(self, **_) -> Any:
        if self.rt:
            await self.rt.shutdown()
        return Ok({"status": "shutdown"})

    @lifecycle(id="config_change")
    async def on_config_change(self, **_) -> Any:
        """配置热更新：改 plugin.toml [neko_pawpilot] 段即时生效。"""
        try:
            config = await self._load_config()
            if self.rt:
                self.rt.apply_config(config)
            return Ok({"status": "reloaded", "dry_run": config.dry_run})
        except Exception as exc:
            self.logger.warning("config_change failed: %s", exc)
            return Err(SdkError(f"配置更新失败: {exc}"))

    @message(id="chat_quiet_window", source="chat")
    def on_chat_message(self, **_) -> Any:
        """玩家说话：触发副驾驶静默窗，避免打扰。"""
        if self.rt:
            self.rt.arbiter.on_player_speak()
        return Ok({"status": "observed"})

    @ui.context(id="dashboard")
    async def ctx_dashboard(self) -> dict:
        """面板状态：遥测 + 情绪 + 记忆。"""
        if not self.rt:
            return {"connected": False, "dry_run": True, "memory": {}}
        return await self.rt.dashboard_state()

    @ui.action(id="set_mode", label="查看人设", tone="primary", group="runtime", order=10, refresh_context=True)
    @plugin_entry(
        id="set_mode",
        name="查看当前人设",
        description="查看当前猫娘人设（由宿主导入：名字/特质/口头禅）。玩家问你是谁/你叫什么/你是什么性格时调用。",
        input_schema={"type": "object", "properties": {
            "mode": {"type": "string", "enum": ["gentle", "chatty", "strict", "quiet"], "default": "gentle"},
        }},
    )
    async def action_set_mode(self, mode: str = "gentle", **_) -> Any:
        """展示宿主导入的猫娘人设（模式参数保留兼容，不再切换）。"""
        try:
            if not self.rt:
                return Err(SdkError("猫爪副驾还没准备好喵"))
            p = self.rt.persona
            traits = "、".join(p.traits[:3]) if p.traits else "（宿主未配置）"
            return Ok({
                "reply": f"我是{p.name}喵！{p.description or ''} 我的特点是：{traits}。"
                         f"我叫你「{p.user_call}」~",
                "name": p.name,
                "traits": p.traits,
                "user_call": p.user_call,
            })
        except Exception as exc:
            self.logger.warning("set_mode failed: %s", exc)
            return Err(SdkError("查询人设失败喵"))

    @ui.action(id="set_voice_style", label="切换口吻", tone="primary", group="runtime", order=11, refresh_context=True)
    @plugin_entry(
        id="set_voice_style",
        name="切换播报口吻",
        description="设置猫娘播报口吻：支持多选融合（styles 数组）。可选：default=自然/tsundere=傲娇/yandere=病娇/loli=萝莉/onee=御姐/genki=元气/cold=冰山/chatty=话痨/gentle=温柔/playful=调皮/strict=严厉/quiet=安静/kuudere=三无/sister=妹系/ojousama=大小姐。玩家说傲娇点/话痨点/病娇一点/御姐音时调用，可组合如「傲娇+话痨」。",
        input_schema={"type": "object", "properties": {
            "style": {"type": "string", "enum": ["default", "tsundere", "yandere", "loli", "onee", "genki", "cold", "chatty", "gentle", "playful", "strict", "quiet", "kuudere", "sister", "ojousama"], "default": "default"},
            "styles": {"type": "array", "items": {"type": "string"}, "description": "多口吻融合列表"},
        }},
    )
    async def action_set_voice_style(self, style: str = "default", styles=None, **_) -> Any:
        """切换口吻风格（单值或多选融合，真实生效）。"""
        try:
            if not self.rt:
                return Err(SdkError("猫爪副驾还没准备好喵"))
            if styles and isinstance(styles, list):
                if not self.rt.persona.set_voice_styles(styles):
                    return Err(SdkError("无效的口吻组合喵"))
            elif not self.rt.persona.set_voice_style(style):
                return Err(SdkError("无效的口吻喵"))
            await self.rt.settings_save()
            v = self.rt.persona.snapshot()
            labels = "、".join(v.get("voice_labels", []))
            return Ok({"reply": f"好的喵~ 接下来我用「{labels}」口吻播报",
                       "voice_styles": v.get("voice_styles"),
                       "voice_labels": v.get("voice_labels")})
        except Exception as exc:
            self.logger.warning("set_voice_style failed: %s", exc)
            return Err(SdkError("切换口吻失败喵"))

    @ui.action(id="pilot_offer", label="猫娘智驾·提议", tone="primary", group="pilot", order=40, refresh_context=True)
    @plugin_entry(
        id="pilot_offer",
        name="猫娘智驾",
        description="猫娘主动提出帮你开车（自动驾驶巡航）。玩家说累了/你开吧/帮我开/自动驾驶时调用。注意：用户必须明确同意后才会接管。",
        input_schema={"type": "object", "properties": {}},
    )
    async def action_pilot_offer(self, **_) -> Any:
        """猫娘提议接管驾驶。"""
        try:
            if not self.rt:
                return Err(SdkError("猫爪副驾还没准备好喵"))
            if not self.rt.pilot.available:
                return Err(SdkError("智驾不可用：输入注入组件缺失"))
            return Ok({"reply": self.rt.pilot.offer(),
                       "pilot": self.rt.pilot.snapshot()})
        except Exception as exc:
            self.logger.warning("pilot_offer failed: %s", exc)
            return Err(SdkError("智驾提议失败喵"))

    @ui.action(id="pilot_accept", label="猫娘智驾·同意", tone="success", group="pilot", order=41, refresh_context=True)
    @plugin_entry(
        id="pilot_accept",
        name="同意猫娘开车",
        description="用户同意让猫娘接管驾驶。玩家说好/交给你了/你来开/同意时调用。",
        input_schema={"type": "object", "properties": {}},
    )
    async def action_pilot_accept(self, **_) -> Any:
        """用户同意猫娘接管。"""
        try:
            if not self.rt:
                return Err(SdkError("猫爪副驾还没准备好喵"))
            return Ok({"reply": self.rt.pilot_accept(),
                       "pilot": self.rt.pilot.snapshot()})
        except Exception as exc:
            self.logger.warning("pilot_accept failed: %s", exc)
            return Err(SdkError("智驾接管失败喵"))

    @ui.action(id="pilot_release", label="猫娘智驾·交还", tone="danger", group="pilot", order=42, refresh_context=True)
    @plugin_entry(
        id="pilot_release",
        name="收回驾驶权",
        description="用户要自己开，猫娘交还控制权。玩家说我来开/换我开/我自己来/收回来时调用。",
        input_schema={"type": "object", "properties": {}},
    )
    async def action_pilot_release(self, **_) -> Any:
        """用户收回驾驶权。"""
        try:
            if not self.rt:
                return Err(SdkError("猫爪副驾还没准备好喵"))
            return Ok({"reply": self.rt.pilot.release(reason="user"),
                       "pilot": self.rt.pilot.snapshot()})
        except Exception as exc:
            self.logger.warning("pilot_release failed: %s", exc)
            return Err(SdkError("交还驾驶失败喵"))

    @ui.action(id="set_dry_run", label="切换播报开关", tone="primary", group="runtime", order=20, refresh_context=True)
    @plugin_entry(
        id="set_dry_run",
        name="切换播报开关",
        description="开/关 dry_run（开=只跑链路不真推给猫娘，关=正式播报）。玩家说开启播报/关闭播报时调用。",
        input_schema={"type": "object", "properties": {"value": {"type": "boolean", "default": False}},
                      "required": ["value"]},
    )
    async def action_set_dry_run(self, value: bool = False, **_) -> Any:
        """切换 dry_run：True=试运行不真发，False=正式播报。"""
        try:
            if not self.rt:
                return Err(SdkError("猫爪副驾还没准备好喵"))
            self.rt.set_dry_run(value)
            await self.rt.settings_save()
            return Ok({"dry_run": self.rt.cfg.dry_run})
        except Exception as exc:
            self.logger.warning("set_dry_run failed: %s", exc)
            return Err(SdkError("切换播报开关失败喵"))

    @ui.action(id="pause", label="急停", tone="danger", group="runtime", order=25, refresh_context=True)
    @plugin_entry(
        id="pause",
        name="急停",
        description="暂停所有提醒输出。玩家说闭嘴/别吵/安静点时调用。",
        input_schema={"type": "object", "properties": {}},
    )
    async def action_pause(self, **_) -> Any:
        """暂停所有提醒输出。"""
        try:
            if not self.rt:
                return Err(SdkError("猫爪副驾还没准备好喵"))
            self.rt.pause()
            return Ok({"status": "paused"})
        except Exception as exc:
            self.logger.warning("pause failed: %s", exc)
            return Err(SdkError("急停失败喵"))

    @ui.action(id="resume", label="恢复", tone="success", group="runtime", order=26, refresh_context=True)
    @plugin_entry(
        id="resume",
        name="恢复",
        description="恢复提醒输出并清空安全计数。玩家说继续播报/恢复提醒时调用。",
        input_schema={"type": "object", "properties": {}},
    )
    async def action_resume(self, **_) -> Any:
        """恢复提醒输出并清空安全计数。"""
        try:
            if not self.rt:
                return Err(SdkError("猫爪副驾还没准备好喵"))
            self.rt.resume()
            return Ok({"status": "running"})
        except Exception as exc:
            self.logger.warning("resume failed: %s", exc)
            return Err(SdkError("恢复失败喵"))

    @ui.action(id="set_frequency", label="设置播报频率", tone="primary", group="runtime", order=27, refresh_context=True)
    @plugin_entry(
        id="set_frequency",
        name="设置播报频率",
        description="设置播报频率：quiet=安静/standard=标准/active=活跃。玩家说播报频率调低/调高/安静点播报时调用。",
        input_schema={"type": "object", "properties": {
            "frequency": {"type": "string", "enum": ["quiet", "standard", "active"], "default": "standard"},
        }},
    )
    async def action_set_frequency(self, frequency: str = "standard", **_) -> Any:
        """设置播报频率：quiet/standard/active。"""
        try:
            if not self.rt:
                return Err(SdkError("猫爪副驾还没准备好喵"))
            if not self.rt.set_frequency(frequency):
                return Err(SdkError("无效的频率模式喵"))
            await self.rt.settings_save()
            return Ok({"broadcast_frequency": frequency})
        except Exception as exc:
            self.logger.warning("set_frequency failed: %s", exc)
            return Err(SdkError("设置频率失败喵"))

    @ui.action(id="set_category", label="切换播报类别", tone="primary", group="runtime", order=28, refresh_context=True)
    @plugin_entry(
        id="set_category",
        name="切换播报类别",
        description="切换某个播报类别（safety=安全/task=任务/trip=旅程/lifecycle=启停/chatter=闲聊）。玩家说播报里加上闲聊/关掉安全提醒时调用。",
        input_schema={"type": "object", "properties": {
            "category": {"type": "string", "enum": ["safety", "task", "trip", "lifecycle", "chatter"], "default": ""},
            "enabled": {"type": "boolean", "default": True},
        }},
    )
    async def action_set_category(self, category: str = "", enabled: bool = True, **_) -> Any:
        """切换某个播报类别（safety/task/trip/lifecycle/chatter）。"""
        try:
            if not self.rt:
                return Err(SdkError("猫爪副驾还没准备好喵"))
            if not self.rt.set_category(category, enabled):
                return Err(SdkError("无效的类别喵"))
            await self.rt.settings_save()
            return Ok({"category": category, "enabled": enabled})
        except Exception as exc:
            self.logger.warning("set_category failed: %s", exc)
            return Err(SdkError("切换类别失败喵"))

    @ui.action(id="test_say", label="测试开口", tone="info", group="diagnostics", order=30, refresh_context=False)
    @plugin_entry(
        id="test_say",
        name="测试推送链路",
        description="测试消息推送链路是否正常。玩家说测试推送/发条测试时调用。",
        input_schema={"type": "object", "properties": {}},
    )
    async def action_test_say(self, **_) -> Any:
        """测试推送链路：发一条事实行。"""
        try:
            if not self.rt:
                return Err(SdkError("猫爪副驾还没准备好喵"))
            ok = await self.rt.push.push_fact("这是猫爪副驾的推送链路测试喵")
            return Ok({"pushed": ok})
        except Exception as exc:
            self.logger.warning("test_say failed: %s", exc)
            return Err(SdkError("测试开口失败喵"))

    @ui.action(id="install_telemetry", label="导入遥测文件", tone="primary", group="diagnostics", order=31, refresh_context=True)
    @plugin_entry(
        id="install_telemetry",
        name="导入遥测文件",
        description="把捆绑的 scs-telemetry.dll 写入游戏 bin/win_x64/plugins/ 目录（需要游戏未运行）。玩家说装遥测/装插件/写入遥测时调用。",
        input_schema={"type": "object", "properties": {}},
    )
    async def action_install_telemetry(self, **_) -> Any:
        """手动导入遥测 DLL 到游戏目录。"""
        try:
            if not self.rt:
                return Err(SdkError("猫爪副驾还没准备好喵"))
            result = self.rt.install_telemetry()
            return Ok(result)
        except Exception as exc:
            self.logger.warning("install_telemetry failed: %s", exc)
            return Err(SdkError("导入遥测文件失败喵"))

    @ui.action(id="reparse_map", label="重新解析地图", tone="primary", group="diagnostics", order=32, refresh_context=True)
    @plugin_entry(
        id="reparse_map",
        name="重新解析地图",
        description="用 TruckLib 重新解析游戏地图，更新道路/设施/红绿灯知识库（耗时长，需游戏已安装）。玩家说解析地图/更新地图/重建地图数据时调用。",
        input_schema={"type": "object", "properties": {}},
    )
    async def action_reparse_map(self, **_) -> Any:
        """手动重新解析地图知识库。"""
        try:
            if not self.rt:
                return Err(SdkError("猫爪副驾还没准备好喵"))
            result = await self.rt.reparse_map()
            if result.get("ok"):
                return Ok(result)
            return Err(SdkError(result.get("detail", "地图解析失败喵")))
        except Exception as exc:
            self.logger.warning("reparse_map failed: %s", exc)
            return Err(SdkError("重新解析地图失败喵"))

    @plugin_entry(id="get_panel_state", name="获取面板状态",
                  description="供面板轮询的完整状态入口。",
                  input_schema={"type": "object", "properties": {}},
                  metadata={"agent_hidden": True})
    async def entry_get_panel_state(self, **_) -> Any:
        if not self.rt:
            return Ok({"connected": False, "dry_run": True, "memory": {}})
        return Ok(await self.rt.dashboard_state())

    @plugin_entry(id="copilot_ledger", name="旅程账本",
                  description="查询旅程账本（本月收入/油费/过路费/罚款/修车/净赚，或单趟收支）。玩家问赚了多少/花了多少/账本时调用。",
                  input_schema={"type": "object", "properties": {}},
                  llm_result_fields=["summary"])
    async def entry_copilot_ledger(self, **_) -> Any:
        try:
            if not self.rt:
                return Err(SdkError("猫爪副驾还没准备好喵"))
            summary = self.rt.ledger.render_summary()
            return Ok({"summary": summary})
        except Exception as exc:
            self.logger.warning("ledger entry failed: %s", exc)
            return Err(SdkError("查账失败喵"))

    @plugin_entry(id="copilot_status", name="驾驶状态",
                  description="查询当前驾驶状态（是否在游戏中、任务进度、油量、车辆信息）。玩家问驾驶情况/任务进度/还有多远时调用。",
                  input_schema={"type": "object", "properties": {}},
                  llm_result_fields=["summary"])
    async def entry_copilot_status(self, **_) -> Any:
        try:
            if not self.rt:
                return Err(SdkError("猫爪副驾还没准备好喵"))
            return Ok(await self.rt.handle_status_query())
        except Exception as exc:
            self.logger.warning("status entry failed: %s", exc)
            return Err(SdkError("查询驾驶状态失败喵"))

    @plugin_entry(id="copilot_talk", name="和猫娘聊驾驶",
                  description="玩家对驾驶/路况/任务说话（如「这单好远」「前面好堵」「我超速了吗」），猫娘结合遥测数据回应。",
                  input_schema={"type": "object", "properties": {
                      "input": {"type": "string", "description": "玩家原话"},
                  }, "required": ["input"]},
                  llm_result_fields=["summary"])
    async def entry_copilot_talk(self, input: str = "", **_) -> Any:
        try:
            if not self.rt:
                return Err(SdkError("猫爪副驾还没准备好喵"))
            return Ok(await self.rt.handle_player_talk(input or ""))
        except Exception as exc:
            self.logger.warning("talk entry failed: %s", exc)
            return Err(SdkError("回应失败喵"))

    @plugin_entry(id="get_llm_config", name="获取LLM配置",
                  description="读取猫爪副驾的自建 LLM 配置（provider/model/api_key/base_url）。",
                  input_schema={"type": "object", "properties": {}},
                  metadata={"agent_hidden": True})
    async def entry_get_llm_config(self, **_) -> Any:
        if not self.rt:
            return Ok({"config": {}, "configured": False})
        return Ok({"config": self.rt.llm_config(),
                   "configured": self.rt.llm.configured,
                   "stats": self.rt.llm.snapshot()})

    @plugin_entry(id="save_llm_config", name="保存LLM配置",
                  description="保存猫爪副驾自建 LLM 配置；留空则降级为模板/宿主演绎。",
                  input_schema={"type": "object", "properties": {
                      "config": {"type": "object", "description": "含 provider/model/api_key/base_url"},
                  }},
                  metadata={"agent_hidden": True})
    async def entry_save_llm_config(self, config: dict = None, **_) -> Any:
        try:
            if not self.rt:
                return Err(SdkError("猫爪副驾还没准备好喵"))
            ok = await self.rt.save_llm_config(config or {})
            return Ok({"saved": ok, "configured": self.rt.llm.configured})
        except Exception as exc:
            self.logger.warning("save_llm_config failed: %s", exc)
            return Err(SdkError("保存 LLM 配置失败喵"))
