import threading
from typing import Annotated

from plugin.sdk.plugin import (
    NekoPluginBase, Ok, Err, SdkError,
    neko_plugin, plugin_entry, lifecycle, timer_interval,
    PluginSettings, SettingsField,
)
from plugin.sdk.plugin.settings import create_settings_safe


class ReminderSettings(PluginSettings):
    """插件业务配置:plugin.toml 用户配置的 [settings] 段;hot 字段改动后随 config_change 生效。"""

    model_config = {"toml_section": "settings"}

    interval_seconds: int = SettingsField(30, hot=True, ge=5, le=86400, description="定时检查间隔(秒)")
    greet_text: str = SettingsField("", hot=True, description="附加问候语,空则不附加")


@neko_plugin
class {{CLASS_NAME}}Plugin(NekoPluginBase):
    def __init__(self, ctx):
        super().__init__(ctx)
        self._items: list[dict] = []
        self._lock = threading.Lock()
        self._cfg = ReminderSettings()

    async def _load_cfg(self) -> None:
        # 官方配置读取方式:config.dump() 返回整个 plugin.toml 的映射,取本插件对应段
        cfg = await self.config.dump(timeout=5.0)
        section = cfg.get("settings") if isinstance(cfg, dict) else None
        self._cfg = create_settings_safe(ReminderSettings, section if isinstance(section, dict) else None)

    @lifecycle(id="startup")
    async def startup(self, **_):
        await self._load_cfg()
        self.logger.info("reminder plugin started (interval=%ss)", self._cfg.interval_seconds)
        return Ok({"status": "ready"})

    @lifecycle(id="config_change")
    async def on_config_change(self, **_):
        await self._load_cfg()
        return Ok({"status": "reloaded"})

    @lifecycle(id="shutdown")
    async def shutdown(self, **_):
        return Ok({"status": "bye"})

    @plugin_entry(
        id="add",
        name="添加提醒",
        description="添加一条提醒,在指定秒数后触发",
    )
    async def add(
        self,
        text: Annotated[str, "提醒内容"],
        after_seconds: Annotated[int, "多少秒后提醒"] = 60,
        **_,
    ):
        if not text.strip():
            return Err(SdkError("提醒内容不能为空"))
        if after_seconds < 5 or after_seconds > 86400:
            return Err(SdkError("间隔需在 5 秒到 24 小时之间"))
        with self._lock:
            self._items.append({"text": text, "at": after_seconds})
        return Ok({"added": text, "after_seconds": after_seconds})

    @plugin_entry(id="list", name="提醒列表", description="查看当前所有提醒")
    async def list_items(self, **_):
        with self._lock:
            return Ok({"items": list(self._items)})

    @timer_interval(id="tick", seconds=30, auto_start=True)
    async def tick(self):
        self.logger.debug("reminder tick, greet=%s", self._cfg.greet_text)
