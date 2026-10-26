"""Declarative hot settings and the optional host chat message hook."""

try:
    from plugin.sdk.plugin import message as _host_message
except ImportError:  # pragma: no cover - exercised by the stub SDK
    _host_message = None


def _message_hook(**kwargs):
    if _host_message is not None:
        return _host_message(**kwargs)

    def decorate(func):
        return func

    return decorate


try:
    from plugin.sdk.plugin import PluginSettings, SettingsField

    class KardsSettings(PluginSettings):
        model_config = {"toml_section": "kards"}
        mode: str = SettingsField("companion", hot=True, enum=["companion", "coach", "silent"],
                                  description="陪伴 / 教练 / 静默")
        dry_run: bool = SettingsField(False, hot=True, description="只记录事件到 events.jsonl，不推送")
        auto_start_monitoring: bool = SettingsField(
            True, hot=True, description="启动后自动开始监控（旧习惯默认开启）")
        llm_data_consent: bool = SettingsField(
            True, hot=True, description="允许把结构化局势交给宿主 LLM 生成解说")
        chatter_interval_seconds: float = SettingsField(60.0, hot=True, ge=0, le=600,
                                                        description="平稳期主动点评间隔（0=关）")
        cooldown_seconds: float = SettingsField(25.0, hot=True, ge=1, le=300, description="普通事件冷却")
        critical_cooldown_seconds: float = SettingsField(5.0, hot=True, ge=1, le=300, description="紧急事件冷却")
        min_priority: int = SettingsField(4, hot=True, ge=1, le=9, description="解说优先级门槛")
        do_not_disturb: bool = SettingsField(False, hot=True, description="免打扰")
        perf_mode: str = SettingsField("realtime", hot=True, enum=["realtime", "power_saver"],
                                       description="实时优先 / 省电")
        tick_ms: float = SettingsField(
            333.0, hot=True, ge=150, le=5000,
            description="主循环上限；实际采样由预设/四项 FPS 决定")
        perception_preset: str = SettingsField(
            "balanced", hot=True, enum=["low_cost", "balanced", "quality", "custom"],
            description="采样预设：低负载 / 平衡 / 质量 / 自定义")
        capture_fps: float = SettingsField(20.0, hot=True, ge=1, le=30, description="采集帧率")
        change_fps: float = SettingsField(10.0, hot=True, ge=0.5, le=15, description="画面变化检测帧率")
        unit_fps: float = SettingsField(4.0, hot=True, ge=0.5, le=12, description="界面检测/单位跟踪帧率")
        card_fps: float = SettingsField(6.0, hot=True, ge=0.5, le=12, description="卡牌分类帧率")
        capture_fps_power_saver: float = SettingsField(
            4.0, hot=True, ge=0.5, le=30, description="旧版省电采集帧率（兼容保留）")
        catalog_enabled: bool = SettingsField(False, hot=True, description="卡牌库联网更新（离线加载始终可用）")
        catalog_url: str = SettingsField("", hot=True, description="卡牌数据源 URL（空=内置候选）")
        catalog_refresh_hours: float = SettingsField(24.0, hot=True, ge=1, le=720)
        vlm_enabled: bool = SettingsField(False, hot=True, description="VLM 兜底（密钥仅环境变量）")
        target_lanlan: str = SettingsField(
            "", hot=True, description="目标猫娘；空=跟随当前角色（绝不伪造角色名）")
        plugin_owned_output_enabled: bool = SettingsField(
            False, hot=True, description="允许插件自有输出；默认由宿主 LLM 生成短回复")
        user_chat_quiet_window_seconds: float = SettingsField(
            60.0, hot=True, ge=0, le=600, description="用户聊天后的普通播报安静窗")
        battle_output_quiet_window_seconds: float = SettingsField(
            30.0, hot=True, ge=0, le=600, description="普通战斗播报安静窗")
        battle_output_backpressure_seconds: float = SettingsField(
            20.0, hot=True, ge=0, le=600, description="普通播报背压窗")
        reply_max_chars: int = SettingsField(
            28, hot=True, ge=1, le=200, description="短 TTS 台词字数上限")
        battle_event_ttl_seconds: float = SettingsField(
            12.0, hot=True, ge=0, le=120, description="战斗事件最大年龄（秒）")
        imported_deck_code: str = SettingsField("", hot=True, description="已导入卡组代码")
except ImportError:  # 老 SDK 无声明式热设置，退化为 config.dump 快照
    KardsSettings = None  # type: ignore[assignment]
    SettingsField = None  # type: ignore[assignment]