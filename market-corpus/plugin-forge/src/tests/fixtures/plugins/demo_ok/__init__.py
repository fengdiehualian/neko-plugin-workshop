"""dry_load 正样例插件源码。"""
from plugin.sdk.plugin import NekoPluginBase, llm_tool, neko_plugin, plugin_entry


@neko_plugin
class DemoOkPlugin(NekoPluginBase):
    """加载入口：类带 @neko_plugin，方法都是协程（与上游 11 个内置件同形）。"""

    @plugin_entry(id="demo_ok.echo", name="回声")
    async def echo(self, text: str = "hi"):
        return {"output": text}

    @llm_tool(name="demo_ok_time")
    async def now(self):
        return {"output": 0}
