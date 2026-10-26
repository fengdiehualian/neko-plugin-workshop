"""猫爪渔力陪玩：渔力全开（How to Fish）mod 遥测陪玩插件入口。"""

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

_CONFIG_SECTION = "neko_fishpower"


@neko_plugin
class NekoFishpowerPlugin(NekoPluginBase):
    """猫爪渔力陪玩 —— 渔力全开 mod 遥测陪玩。"""

    def __init__(self, ctx: Any) -> None:
        super().__init__(ctx)
        self.logger = self.enable_file_logging(log_level="INFO")
        self.rt: Any = None

    async def _load_config(self) -> Any:
        from .core.config_model import FishpowerConfig

        raw = await self.config.dump()
        section = raw.get(_CONFIG_SECTION, {}) if isinstance(raw, dict) else {}
        return FishpowerConfig(section)

    @lifecycle(id="startup")
    async def startup(self, **_) -> Any:
        from .core.runtime import FishpowerRuntime

        try:
            # 静态 UI 注册：面板入口 static/index.html
            if (self.config_dir / "static").exists():
                self.register_static_ui("static", index_file="index.html",
                                        cache_control="no-cache, no-store, must-revalidate")
            config = await self._load_config()
            self.rt = FishpowerRuntime(self, config)
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
        """配置热更新：改 plugin.toml [neko_fishpower] 段即时生效。"""
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
        """玩家说话：触发静默窗，避免打扰。"""
        if self.rt:
            self.rt.arbiter.on_player_speak()
        return Ok({"status": "observed"})

    @ui.context(id="dashboard")
    async def ctx_dashboard(self) -> dict:
        """面板状态：遥测 + 图鉴 + 记忆。"""
        if not self.rt:
            return {"connected": False, "dry_run": True, "memory": {}}
        return await self.rt.dashboard_state()

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
                return Err(SdkError("猫爪渔力陪玩还没准备好喵"))
            self.rt.set_dry_run(value)
            await self.rt.settings_save()
            return Ok({"dry_run": self.rt.cfg.dry_run})
        except Exception as exc:
            self.logger.warning("set_dry_run failed: %s", exc)
            return Err(SdkError("切换播报开关失败喵"))

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
                return Err(SdkError("猫爪渔力陪玩还没准备好喵"))
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

    @ui.action(id="set_theme", label="切换主题", tone="primary", group="runtime", order=21, refresh_context=False)
    @plugin_entry(
        id="set_theme",
        name="切换面板主题",
        description="切换面板明暗主题（light/dark），持久化到配置。",
        input_schema={"type": "object", "properties": {
            "theme": {"type": "string", "enum": ["light", "dark"], "default": "dark"},
        }},
    )
    async def action_set_theme(self, theme: str = "dark", **_) -> Any:
        try:
            if not self.rt:
                return Err(SdkError("猫爪渔力陪玩还没准备好喵"))
            if not self.rt.set_theme(theme):
                return Err(SdkError("无效的主题喵"))
            await self.rt.settings_save()
            return Ok({"theme": theme})
        except Exception as exc:
            self.logger.warning("set_theme failed: %s", exc)
            return Err(SdkError("切换主题失败喵"))

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
        try:
            if not self.rt:
                return Err(SdkError("猫爪渔力陪玩还没准备好喵"))
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
        description="切换某个播报类别（caught=钓鱼/casino=赌场/grill=烤鱼/boss=Boss/journal=图鉴/lifecycle=启停/chatter=闲聊）。玩家说播报里加上闲聊/关掉赌场提醒时调用。",
        input_schema={"type": "object", "properties": {
            "category": {"type": "string", "enum": ["caught", "casino", "grill", "boss", "journal", "lifecycle", "chatter"], "default": ""},
            "enabled": {"type": "boolean", "default": True},
        }},
    )
    async def action_set_category(self, category: str = "", enabled: bool = True, **_) -> Any:
        try:
            if not self.rt:
                return Err(SdkError("猫爪渔力陪玩还没准备好喵"))
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
                return Err(SdkError("猫爪渔力陪玩还没准备好喵"))
            ok = await self.rt.push.push_fact("这是猫爪渔力陪玩的推送链路测试喵")
            return Ok({"pushed": ok})
        except Exception as exc:
            self.logger.warning("test_say failed: %s", exc)
            return Err(SdkError("测试开口失败喵"))

    @ui.action(id="pause", label="急停", tone="danger", group="runtime", order=25, refresh_context=True)
    @plugin_entry(
        id="pause",
        name="急停",
        description="暂停所有提醒输出。玩家说闭嘴/别吵/安静点时调用。",
        input_schema={"type": "object", "properties": {}},
    )
    async def action_pause(self, **_) -> Any:
        try:
            if not self.rt:
                return Err(SdkError("猫爪渔力陪玩还没准备好喵"))
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
        try:
            if not self.rt:
                return Err(SdkError("猫爪渔力陪玩还没准备好喵"))
            self.rt.resume()
            return Ok({"status": "running"})
        except Exception as exc:
            self.logger.warning("resume failed: %s", exc)
            return Err(SdkError("恢复失败喵"))

    @plugin_entry(id="get_panel_state", name="获取面板状态",
                  description="供面板轮询的完整状态入口。",
                  input_schema={"type": "object", "properties": {}},
                  metadata={"agent_hidden": True})
    async def entry_get_panel_state(self, **_) -> Any:
        if not self.rt:
            return Ok({"connected": False, "dry_run": True, "memory": {}})
        return Ok(await self.rt.dashboard_state())

    @plugin_entry(id="fish_status", name="钓鱼状态",
                  description="查询当前钓鱼状态（是否在游戏中、最近渔获、图鉴进度、金币）。玩家问钓鱼情况/钓到什么/图鉴多少/赚了多少时调用。",
                  input_schema={"type": "object", "properties": {}},
                  llm_result_fields=["summary"])
    async def entry_fish_status(self, **_) -> Any:
        try:
            if not self.rt:
                return Err(SdkError("猫爪渔力陪玩还没准备好喵"))
            return Ok(await self.rt.handle_status_query())
        except Exception as exc:
            self.logger.warning("status entry failed: %s", exc)
            return Err(SdkError("查询钓鱼状态失败喵"))

    @plugin_entry(id="fish_talk", name="和猫娘聊钓鱼",
                  description="玩家对钓鱼/鱼种/饵/赌场说话（如「这鱼值钱吗」「什么饵钓鲨鱼」「赌场怎么玩」），猫娘结合遥测数据回应。",
                  input_schema={"type": "object", "properties": {
                      "input": {"type": "string", "description": "玩家原话"},
                  }, "required": ["input"]},
                  llm_result_fields=["summary"])
    async def entry_fish_talk(self, input: str = "", **_) -> Any:
        try:
            if not self.rt:
                return Err(SdkError("猫爪渔力陪玩还没准备好喵"))
            return Ok(await self.rt.handle_player_talk(input or ""))
        except Exception as exc:
            self.logger.warning("talk entry failed: %s", exc)
            return Err(SdkError("回应失败喵"))

    @plugin_entry(id="fish_ledger", name="渔获账本",
                  description="查询渔获账本（卖鱼收入/赌场输赢/净赚）。玩家问赚了多少/花了多少/账本时调用。",
                  input_schema={"type": "object", "properties": {}},
                  llm_result_fields=["summary"])
    async def entry_fish_ledger(self, **_) -> Any:
        try:
            if not self.rt:
                return Err(SdkError("猫爪渔力陪玩还没准备好喵"))
            return Ok({"summary": self.rt.ledger.render_summary()})
        except Exception as exc:
            self.logger.warning("ledger entry failed: %s", exc)
            return Err(SdkError("查账失败喵"))

    @plugin_entry(id="get_llm_config", name="获取LLM配置",
                  description="读取猫爪渔力陪玩的自建 LLM 配置（provider/model/api_key/base_url）。",
                  input_schema={"type": "object", "properties": {}},
                  metadata={"agent_hidden": True})
    async def entry_get_llm_config(self, **_) -> Any:
        if not self.rt:
            return Ok({"config": {}, "configured": False})
        return Ok({"config": self.rt.llm_config(),
                   "configured": self.rt.llm.configured,
                   "stats": self.rt.llm.snapshot()})

    @plugin_entry(id="save_llm_config", name="保存LLM配置",
                  description="保存猫爪渔力陪玩自建 LLM 配置；留空则降级为模板/宿主演绎。",
                  input_schema={"type": "object", "properties": {
                      "config": {"type": "object", "description": "含 provider/model/api_key/base_url"},
                  }},
                  metadata={"agent_hidden": True})
    async def entry_save_llm_config(self, config: dict = None, **_) -> Any:
        try:
            if not self.rt:
                return Err(SdkError("猫爪渔力陪玩还没准备好喵"))
            ok = await self.rt.save_llm_config(config or {})
            return Ok({"saved": ok, "configured": self.rt.llm.configured})
        except Exception as exc:
            self.logger.warning("save_llm_config failed: %s", exc)
            return Err(SdkError("保存 LLM 配置失败喵"))
