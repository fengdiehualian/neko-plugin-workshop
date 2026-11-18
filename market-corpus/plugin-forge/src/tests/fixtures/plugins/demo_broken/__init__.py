"""dry_load 反例源码：类名与 entry 不一致，方法还不是协程。"""
from plugin.sdk.plugin import NekoPluginBase, plugin_entry


class RealNamePlugin(NekoPluginBase):
    @plugin_entry(id="demo_broken.ping", name=".ping")
    def ping(self):
        return {"output": 1}
