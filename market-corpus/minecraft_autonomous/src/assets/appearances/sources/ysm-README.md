# NEKO / YUI 的 YSM 模型

模型 ID：`neko_yui`；材质 ID：`default`；首版 `1.0.0`，目标为当前正式服接入链路的 YSM **2.4.1**。外观参考正在使用的 `yui-lolita`：黑猫耳、黑色双马尾、蓝眼睛、蓝白洛丽塔裙装、蝴蝶结与猫尾，采用 Minecraft 方块风格。

- [可交给服主的模型包](neko_yui.zip)：ZIP 根目录直接是 `ysm.json`，共 7 个资源。
- [正面 / 侧面 / 背面预览](neko-yui-preview.png)。
- [可编辑原始资源](neko_yui/)与[文件校验清单](neko-yui-manifest.json)。
- [验证记录和已知边界](verification-2026-10-11.md)。

![导出的实际几何与贴图预览](neko-yui-preview.png)

## 发给服主的安装步骤

请将 `neko_yui.zip` 解压到 **Freesia Worker 对应的 Minecraft 实例**的 `config/yes_steve_model/custom/neko_yui/`，使最终文件为 `config/yes_steve_model/custom/neko_yui/ysm.json`。不要多嵌套一层 `neko_yui`，也不要仅放进 Paper 插件目录。安装前保留 ag_NEKO 当前外观设置的备份。

本包使用公开原始资源、`spec: 2`、`free: true`，没有加密文件；当前 Web 的 ModelCatalog 需要能读取这份原始目录。服务端已有目录监测时按其流程加载；需要手动重载时，在承载 YSM 的实例上通过正常管理控制台执行 `ysm model reload`。原生 YSM 客户端也需要正常接收该模型资源。

模型加载成功后，使用服务器已配置的正规 YSM 管理入口，为准确账号 **ag_NEKO** 执行：

```text
ysm model set ag_NEKO neko_yui default
```

游戏内支持该管理命令且有权限时在前面加 `/`。不使用 `ignore_auth`、不授予 OP，也不直接改写其他玩家的配置。如果当前 Freesia 版本在独立 Worker 上管理玩家状态，请使用它实际提供的绑定入口，不把上述命令盲目发到不支持该命令的 Paper 后端。

官方依据：[资源结构（2.4.x）](https://yesstevemodel.github.io/wiki/struct/)、[模型命令](https://yesstevemodel.github.io/wiki/command/)、[Freesia Worker 架构](https://yesstevemodel.github.io/wiki/freesia-plugin/)。本次制作没有向游戏公屏发送安装命令。

绑定之后需要分别验收：

1. Worker 接受 `neko_yui/default`，账号 ag_NEKO 的权威保存状态已更新。
2. 原生观战客户端收到模型并能显示正确外观、手持装备和动作。
3. Web 收到 ag_NEKO 本人的 `mcagent:appearance`：`available: true`、`modelId: neko_yui`、`texture: default`；随后收到哈希匹配的原始资源，并实际渲染。包通过本地检查或网页返回 HTTP 200 都不能代替这一步。

2026-10-11 20:27（北京时间）已核对正式服 **ag_NEKO → `neko_yui/default`** 生效：
两路局域网可视化收到本人权威外观、匹配资源，`available/webAvailable/renderAvailable` 均为 `true`。
Web 使用的四个原始资源与制作包逐项 SHA-256 一致；地下城页面已实际显示自定义造型，
未捕获到页面渲染错误。安装由用户确认完成，本次只读验收没有重启服务。
原生观战客户端的装备、全部动画及完整渲染一致性仍需单独验收，详见验证记录。

## 开发与复现

从仓库根目录运行，Node 环境沿用项目已有的 `canvas`：

```sh
node models/ysm/build-neko-yui.mjs
node models/ysm/verify-neko-yui.mjs
python models/ysm/pack-neko-yui.py
```

如需使用真实共享渲染器检查，设置 `NEKO_YSM_RENDERER_ROOT` 为已安装依赖的 `mc-visual-console/packages/modern-viewer/renderer-src` 根目录，再运行验证脚本。它会导入生产 Bedrock 模型解析器与 YSM 动画采样器，并校验离线资源传输包；不会连接服务器或伪造外观状态。

```sh
node models/ysm/start-preview.mjs /path/to/renderer-src 18043
```

打开 `http://127.0.0.1:18043/preview.html` 可切换待机、行走、跑步、挥动工具、招手、欢呼、鞠躬、潜行与坐下。预览只绑定 loopback，没有 Neko 聊天或 Minecraft WebSocket。保存预览按钮将画面导出到本目录固定的 `neko-yui-preview.png`；停止该独立 Node 进程即可结束预览。

## 来源与维护

`build-neko-yui.mjs` 是原创几何和数字关键帧动画的可编辑来源，生成的 JSON 保持紧凑便于传输；需要编辑细节时可在 Blockbench 中打开 `models/main.json`。主模型保留 Head、视角、双手物品、鞘翅与肩部定位组，第一人称手臂使用独立模型。31 个主动画包含 3 个轮盘动作的 Web 可解析副本；14 个手部条件动作与轮盘原文件分别索引。

贴图逐字节复用仓库中已批准的 `skins/yui-lolita-slim.png`，原贴图由内置 imagegen 生成，来源与提示见 [skin README](../../skins/README.md)。新部件通过原贴图 UV 和原有颜色取样着色，没有复制默认 YSM 模型或任何第三方角色网格；未打包 Live2D 的 moc3、纹理或私有运行配置。角色造型参考 N.E.K.O. YUI，模型元数据保留来源说明。
