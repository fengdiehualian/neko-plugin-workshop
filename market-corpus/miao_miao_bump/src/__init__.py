from typing import Any

from plugin.sdk.plugin import (
    Err,
    NekoPluginBase,
    Ok,
    SdkError,
    lifecycle,
    neko_plugin,
    tr,
)


@neko_plugin
class MiaoMiaoBumpPlugin(NekoPluginBase):
    """喵喵碰碰大作战"""

    def __init__(self, ctx: Any):
        super().__init__(ctx)
        self.logger = ctx.logger

    @lifecycle(id="startup")
    async def on_startup(self, **_):
        if not self.register_static_ui("static", cache_control="no-store"):
            return Err(SdkError("Static UI is missing", code="STATIC_UI_MISSING"))
        self.set_list_actions([
            {
                "id": "open_playground",
                "label": tr("actions.open", default="Open Meow Meow Bump Battle"),
                "kind": "ui",
                "target": "/plugin/miao_miao_bump/ui/index.html",
                "open_in": "new_tab",
                "requires_running": True,
            }
        ])
        self.logger.info("MiaoMiaoBumpPlugin started")
        return Ok({"status": "ready", "playable": True})

    @lifecycle(id="shutdown")
    async def on_shutdown(self, **_):
        self.logger.info("MiaoMiaoBumpPlugin stopped")
        return Ok({"status": "stopped"})
