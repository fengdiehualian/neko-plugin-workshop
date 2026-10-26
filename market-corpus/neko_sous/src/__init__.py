import asyncio
import time

from plugin.sdk.plugin import NekoPluginBase, Ok, lifecycle, neko_plugin, plugin_entry, tr, ui

from .announcer import Announcer
from .client import NekoSousClient
from .companion import FREQUENCIES, Companion
from .orders import CATEGORIES, category


@neko_plugin
class NekoSousPlugin(NekoPluginBase):
    def __init__(self, ctx) -> None:
        super().__init__(ctx)
        self._announcer = Announcer(self.i18n, self.push_message, self.logger)
        self._companion = Companion()
        self._client = NekoSousClient(self.logger, on_event=self._on_game_event,
                                      on_state=self._companion.observe, on_reset=self._companion.reset)
        self._options: dict = {}
        self._settings_lock = asyncio.Lock()
        self._settings_pending = False
        self._companion_task: asyncio.Task[None] | None = None
        self._companion_skip: str | None = None

    @lifecycle(id="startup")
    async def startup(self, **_):
        options = await self.config.get("broadcast", {})
        if isinstance(options, dict):
            self._options = dict(options)
            self._client.configure_events({key: options.get(f"{key}_enabled", True) is True for key in CATEGORIES})
            self._client.events_enabled = options.get("events_enabled", options.get("enabled", True)) is True
            self._companion.enabled = options.get("chatter_enabled", True) is True
            frequency = options.get("frequency", "balanced")
            self._companion.frequency = frequency if isinstance(frequency, str) and frequency in FREQUENCIES else "balanced"
            locale = options.get("locale", "zh-CN")
            self._announcer.locale = "en" if locale == "en" else "zh-CN"
        # No connection exists until the user clicks Connect.
        return Ok({"status": "ready"})

    async def _on_game_event(self, event: dict) -> bool:
        if self._client.events_subscribed:
            kind = category(event)
            if kind and not self._client.event_options[kind]:
                return False
            accepted = await asyncio.to_thread(self._announcer.announce, event)
            if accepted and event["event"] != "companion_chat":
                self._companion.event_spoke()
            return accepted
        return False

    def _snapshot(self) -> dict:
        return {**self._client.snapshot(), "settings_pending": self._settings_pending,
                "companion": {**self._companion.status(), "last_skip": self._companion_skip,
                              "task_running": self._companion_task is not None and not self._companion_task.done()},
                "settings": {"events_enabled": self._client.events_enabled,
                             "chatter_enabled": self._companion.enabled, "frequency": self._companion.frequency,
                             **{f"{key}_enabled": value for key, value in self._client.event_options.items()}}}

    async def _companion_loop(self) -> None:
        while True:
            await asyncio.sleep(1)
            if not (self._client.state == "connected" and self._client.events_subscribed
                    and self._client.state_supported) or self._settings_pending:
                continue
            if self._companion.candidate() is None:
                continue
            if self._client.kitchen_pending:
                continue
            try:
                low = FREQUENCIES[self._companion.frequency][0]
                recent = await self.bus.conversations.get(max_count=1, since_ts=time.time() - low, timeout=2)
                if not hasattr(recent, "items"):
                    raise RuntimeError("近期对话状态不可用")
                if recent.items:
                    self._companion_skip = "recent_conversation"
                    self._companion.defer()
                    continue
                event = self._companion.candidate()
                if (event and self._client.events_subscribed and not self._settings_pending
                        and not self._client.kitchen_pending
                        and await self._on_game_event(event)):
                    self._companion.spoke(event)
                    self._companion_skip = None
                    self.logger.info("[NekoSous] 已提交本局主动搭话")
            except Exception as exc:
                self._companion_skip = f"{type(exc).__name__}: {exc}"
                self.logger.debug(f"[NekoSous] 本次主动搭话已跳过：{type(exc).__name__}: {exc}")
                self._companion.defer()

    @lifecycle(id="shutdown")
    async def shutdown(self, **_):
        if self._companion_task and not self._companion_task.done():
            self._companion_task.cancel()
            await asyncio.gather(self._companion_task, return_exceptions=True)
        await self._client.stop()
        return Ok({"status": "stopped"})

    @ui.context(id="dashboard")
    async def dashboard(self):
        await self._client.refresh_game()
        return self._snapshot()

    @ui.action(label=tr("actions.settings", default="Companion settings"))
    @plugin_entry(id="update_settings", name=tr("actions.settings", default="Companion settings"),
                  description=tr("entries.settings", default="Save event subscription and companion preferences."),
                  input_schema={"type": "object", "properties": {
                      "events_enabled": {"type": "boolean"}, "chatter_enabled": {"type": "boolean"},
                      "delivery_enabled": {"type": "boolean"}, "expiry_enabled": {"type": "boolean"},
                      "failure_enabled": {"type": "boolean"}, "combo_enabled": {"type": "boolean"},
                      "fire_enabled": {"type": "boolean"}, "ready_enabled": {"type": "boolean"},
                      "overcooking_enabled": {"type": "boolean"},
                      "mixing_ready_enabled": {"type": "boolean"}, "mixing_warning_enabled": {"type": "boolean"},
                      "mixing_ruined_enabled": {"type": "boolean"},
                      "frequency": {"type": "string", "enum": ["quiet", "balanced", "lively"]}}})
    async def update_settings(self, events_enabled: bool | None = None, chatter_enabled: bool | None = None,
                              frequency: str | None = None, delivery_enabled: bool | None = None,
                              expiry_enabled: bool | None = None, failure_enabled: bool | None = None,
                              combo_enabled: bool | None = None, fire_enabled: bool | None = None,
                              ready_enabled: bool | None = None, overcooking_enabled: bool | None = None,
                              mixing_ready_enabled: bool | None = None, mixing_warning_enabled: bool | None = None,
                              mixing_ruined_enabled: bool | None = None, **_):
        choices = dict(delivery=delivery_enabled, expiry=expiry_enabled, failure=failure_enabled, combo=combo_enabled,
                       fire=fire_enabled, ready=ready_enabled, overcooking=overcooking_enabled,
                       mixing_ready=mixing_ready_enabled, mixing_warning=mixing_warning_enabled, mixing_ruined=mixing_ruined_enabled)
        async with self._settings_lock:
            if any(value is not None and type(value) is not bool for value in choices.values()):
                self._client.report_error("invalid_settings", "事件设置参数无效")
                return Ok(self._snapshot())
            if ((events_enabled is not None and type(events_enabled) is not bool)
                    or (chatter_enabled is not None and type(chatter_enabled) is not bool)
                    or (frequency is not None and (not isinstance(frequency, str) or frequency not in FREQUENCIES))):
                self._client.report_error("invalid_settings", "陪伴设置参数无效")
                return Ok(self._snapshot())
            previous = self._client.events_enabled
            desired = previous if events_enabled is None else events_enabled
            self._settings_pending = True
            try:
                if desired != previous:
                    await self._client.set_subscription(desired)
                event_options: dict[str, bool] = {
                    key: self._client.event_options[key] if value is None else value
                    for key, value in choices.items()
                }
                options = {**self._options, "events_enabled": desired,
                           "chatter_enabled": self._companion.enabled if chatter_enabled is None else chatter_enabled,
                           "frequency": frequency or self._companion.frequency,
                           **{f"{key}_enabled": value for key, value in event_options.items()}}
                await self.config.set("broadcast", options)
                self._client.configure_events(event_options)
                self._options = options
                self._companion.enabled = options["chatter_enabled"]
                self._companion.frequency = options["frequency"]
                self._companion.defer()
                self._client.last_error = None
            except Exception as exc:
                if self._client.events_enabled != previous:
                    try:
                        await self._client.set_subscription(previous)
                    except Exception as rollback:
                        self.logger.warning(f"[NekoSous] 恢复事件订阅失败：{rollback}")
                        await self._client.close_connection()
                    self._client.events_enabled = previous
                self._client.report_error(getattr(exc, "code", "settings_failed"), f"保存陪伴设置失败：{exc}")
            finally:
                self._settings_pending = False
            return Ok(self._snapshot())

    @ui.action(label=tr("actions.connect", default="Connect"), tone="primary")
    @plugin_entry(id="connect", name=tr("actions.connect", default="Connect"),
                  description=tr("entries.connect", default="Check the game and connect to NekoWsServer."))
    async def connect_server(self, **_):
        result = await self._client.start()
        # startup 在临时事件循环中执行；长期任务要随手动连接进入命令循环。
        if self._client.desired and (self._companion_task is None or self._companion_task.done()):
            self._companion_task = asyncio.create_task(self._companion_loop())
        return Ok(result)

    @ui.action(label=tr("actions.disconnect", default="Disconnect"))
    @plugin_entry(id="disconnect", name=tr("actions.disconnect", default="Disconnect"),
                  description=tr("entries.disconnect", default="Disconnect and stop automatic reconnection."))
    async def disconnect_server(self, **_):
        return Ok(await self._client.stop())

    @ui.action(label=tr("actions.setMode", default="Switch mode"), tone="primary")
    @plugin_entry(
        id="set_mode", name=tr("actions.setMode", default="Switch mode"),
        description=tr("entries.setMode", default="Request and confirm a game mode change. No game controls are enabled."),
        input_schema={"type": "object", "properties": {"mode": {"type": "string", "enum": ["companion", "playmate"]}},
                      "required": ["mode"]},
    )
    async def set_mode(self, mode: str, **_):
        return Ok(await self._client.set_mode(mode))
