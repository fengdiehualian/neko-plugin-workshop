import secrets
from pathlib import Path

from plugin.sdk.plugin import Err, NekoPluginBase, Ok, SdkError, lifecycle, neko_plugin, plugin_entry, tr, ui

from .channel_context import speaker_context
from .diagnostics import configure
from .service import ConnectionService


@neko_plugin
class NekoVoiceBridgePlugin(NekoPluginBase):
    def __init__(self, ctx):
        super().__init__(ctx)
        configure(self.logger)
        self.service = None
        self.ui_token = secrets.token_urlsafe(32)

    @lifecycle(id="startup")
    async def startup(self, **_):
        await self.ensure_service()
        return Ok({"panel_ready": True})

    async def ensure_service(self):
        if self.service is None:
            self.service = ConnectionService(self.data_path("bridge.json"), Path(__file__).with_name("bridge.local.json"), context_sender=self.send_speaker_context)
        return await self.service.call("snapshot")

    def send_speaker_context(self, speaker):
        result = self.push_message(source="neko_voice_bridge", visibility=[], ai_behavior="read",
            parts=[{"type": "text", "text": speaker_context(speaker)}], priority=5,
            coalesce_key="neko_voice_bridge.current_speaker")
        return bool(result.get("submitted"))

    @ui.context(id="dashboard", title=tr("panel.title"))
    async def dashboard(self):
        state = await self.ensure_service()
        return {**state, "action_token": self.ui_token, "plugin_version": "0.7.1"}

    def valid_ui(self, value):
        return isinstance(value, str) and secrets.compare_digest(value, self.ui_token)

    @ui.action(id="ui_pair", label=tr("actions.pair"), tone="primary", refresh_context=True)
    @plugin_entry(id="ui_pair", name=tr("actions.pair"), description=tr("entries.ui_only"))
    async def pair(self, connection_code: str = "", ui_token: str = "", **_):
        if not self.valid_ui(ui_token):
            return Err(SdkError("UI_ONLY"))
        try:
            await self.ensure_service()
            await self.service.call("pair", connection_code)
            return Ok(await self.service.call("snapshot"))
        except Exception:
            return Err(SdkError(self.i18n.t("errors.pair")))

    @ui.action(id="ui_control", label=tr("actions.control"), refresh_context=True)
    @plugin_entry(id="ui_control", name=tr("actions.control"), description=tr("entries.ui_only"))
    async def control(self, action: str = "", ui_token: str = "", **_):
        if not self.valid_ui(ui_token):
            return Err(SdkError("UI_ONLY"))
        if action not in ("start", "stop", "disconnect", "reconnect"):
            return Err(SdkError("INVALID_ACTION"))
        try:
            await self.ensure_service()
            await self.service.call("control", action)
            return Ok(await self.service.call("snapshot"))
        except Exception:
            return Err(SdkError(self.i18n.t("errors.control")))

    @lifecycle(id="shutdown")
    async def shutdown(self, **_):
        service, self.service = self.service, None
        if service:
            await service.close()
        return Ok({"closed": True})
