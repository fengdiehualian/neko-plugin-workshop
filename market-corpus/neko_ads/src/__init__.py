"""N.E.K.O SDK v2 session and assets for the host's opt-in desktop ads."""

from pathlib import Path

from plugin.sdk.plugin import NekoPluginBase, Ok, lifecycle, neko_plugin, plugin_entry, ui

from .desktop import DesktopController
from .session import AdSession


@neko_plugin
class NekoAdsPlugin(NekoPluginBase):
    def __init__(self, ctx):
        super().__init__(ctx)
        self.session = AdSession()
        self.desktop = DesktopController(self.session, voice_cache_dir=Path(__file__).parent / 'static/voice-cache')

    def desktop_runtime(self):
        if not hasattr(self, 'desktop'):
            self.desktop = DesktopController(self.session, voice_cache_dir=Path(__file__).parent / 'static/voice-cache')
        return self.desktop

    @lifecycle(id="startup")
    async def on_startup(self):
        if not self.register_static_ui("static", cache_control="no-store"):
            from plugin.sdk.plugin import Err, SdkError

            return Err(SdkError("猫娘广告部静态界面缺失"))
        self.set_list_actions([{
            "id": "open_ui", "label": "广告部控制台", "kind": "ui",
            "target": "/plugin/neko_ads/ui/", "open_in": "new_tab",
            "requires_running": False,
        }])
        self.session.ready()
        self.desktop_runtime().run()
        return Ok({"status": "ready"})

    @lifecycle(id="shutdown")
    async def on_shutdown(self):
        await self.desktop_runtime().close()
        return Ok(self.session.shutdown())

    @lifecycle(id="freeze")
    async def on_freeze(self):
        await self.desktop_runtime().close()
        return Ok(self.session.shutdown())

    @lifecycle(id="unfreeze")
    async def on_unfreeze(self):
        self.session.ready()
        self.desktop_runtime().run()
        return Ok({"status": "ready"})

    @ui.context(id="main")
    async def dashboard(self):
        return self.desktop_runtime().snapshot()

    @ui.action(label="确认开局", refresh_context=False)
    @plugin_entry(id="start_game", description="仅供用户在广告游戏界面确认后开局。")
    async def start_game(self, round_id: str, epoch: str, confirmed: bool = False):
        return Ok(self.session.start(round_id, confirmed, epoch))

    @ui.action(label="检查本局", refresh_context=False)
    @plugin_entry(id="heartbeat", description="检查本局是否仍运行，不会恢复已结束的游戏。")
    async def heartbeat(self, round_id: str):
        return Ok(self.session.heartbeat(round_id))

    @ui.action(label="立即结束", tone="danger", refresh_context=False)
    @plugin_entry(id="stop_game", description="携带 round_id 或当前 stop_epoch 立即结束对应广告游戏。")
    async def stop_game(self, round_id: str = "", stop_epoch: str = ""):
        if not round_id and not stop_epoch:
            from plugin.sdk.plugin import Err, SdkError
            return Err(SdkError('停止请求需要游戏标识或当前停止代次'))
        return Ok(await self.desktop_runtime().stop(round_id or None, epoch=stop_epoch or None))

    @ui.action(label="主界面广告控制", refresh_context=False)
    @plugin_entry(id="desktop_command", description="广告主界面桥接使用的控制接口。", timeout=20)
    async def desktop_command(self, command: str, payload: dict):
        try:
            return Ok(await self.desktop_runtime().command(command, payload))
        except ValueError as exc:
            from plugin.sdk.plugin import Err, SdkError
            return Err(SdkError(str(exc)))
