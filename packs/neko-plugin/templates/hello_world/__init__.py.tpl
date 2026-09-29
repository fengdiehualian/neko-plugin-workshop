from plugin.sdk.plugin import NekoPluginBase, Ok, neko_plugin, plugin_entry, lifecycle


@neko_plugin
class {{CLASS_NAME}}Plugin(NekoPluginBase):
    @lifecycle(id="startup")
    async def startup(self, **_):
        return Ok({"status": "ready"})

    @lifecycle(id="shutdown")
    async def shutdown(self, **_):
        return Ok({"status": "bye"})

    @plugin_entry(
        id="hello",
        name="打招呼",
        description="说一声你好",
    )
    async def hello(self, name: str = "World", **_):
        return Ok({"message": f"你好,{name}!"})
