# 酒狐插件（N.E.K.O × Touhou Little Maid）

酒狐插件让 [N.E.K.O](https://github.com/Project-N-E-K-O/N.E.K.O) 能通过自然语言与《车万女仆》（Touhou Little Maid）联动：在对话中指挥女仆行动、了解游戏状态，并获得低打扰的游戏陪伴。

## 功能

- **女仆指挥**：跟随、驻守、坐下/站起、切换工作模式、攻击目标、装备物品和执行技能。
- **自主行动**：让女仆前往指定坐标、导航到玩家身边、砍树、采集附近方块、寻找并开采矿物、完成后返程。
- **状态感知**：获取女仆、玩家、背包、时间、天气、维度与附近环境状态；在受伤、死亡、物品变化等关键事件发生时主动提醒。
- **游戏目标板**：在 Minecraft HUD 中展示当前目标和步骤，可由对话、插件面板或游戏内命令更新。
- **陪玩与建议**：根据游戏活动和上下文提供节制的提醒、建议与互动，支持安静、标准、活跃和自定义模式。
- **控制面板与诊断**：在 N.E.K.O 面板中选择女仆、调整桥接地址和陪玩设置，并诊断 Minecraft、WebSocket 与女仆连接状态。

## 使用条件

- N.E.K.O
- Minecraft `1.21.1`
- NeoForge `21.1.172` 或兼容的 `21.x`
- 《车万女仆》模组 `1.5.3`
- 与本插件对应版本的 `neko_tlm_bridge` 桥接模组

插件默认通过 `ws://127.0.0.1:48920` 与桥接模组通信。

## 安装

1. 从本仓库的 [Releases](https://github.com/ggg233m/n.e.k.o_plugin_neko_tlm/releases) 下载 `neko_tlm.neko-plugin`，并在 N.E.K.O 中导入。
2. 从 [主仓库 Releases](https://github.com/ggg233m/N.E.K.OxTLM/releases) 下载对应版本的 `neko_tlm_bridge`，放入 Minecraft 的 `mods` 目录。
3. 启动 Minecraft 并进入存档，再启动 N.E.K.O、启用酒狐插件。
4. 在插件面板刷新女仆列表，选择要控制的女仆。

第一次安装或不确定应下载哪些文件时，请先阅读[《Mod 下载与安装教程》](MOD_DOWNLOAD_GUIDE.md)。

如果修改了桥接模组端口，请在插件面板中保存新的 WebSocket 端口，或同步修改 `plugin.toml` 的 `minecraft_bridge.ws_url`。

## 开发与贡献

本仓库只承载插件源码与发布。桥接模组、完整功能说明、开发环境、测试、依赖同步、本地打包和市场发布流程都集中维护在主仓库：

- [N.E.K.OxTLM 主仓库](https://github.com/ggg233m/N.E.K.OxTLM)
- [完整使用教程](https://github.com/ggg233m/N.E.K.OxTLM/blob/master/%E4%BD%BF%E7%94%A8%E6%95%99%E7%A8%8B.md)
- [开发与同步说明](https://github.com/ggg233m/N.E.K.OxTLM#%E6%8F%92%E4%BB%B6%E4%BB%93%E5%BA%93%E5%90%8C%E6%AD%A5)
