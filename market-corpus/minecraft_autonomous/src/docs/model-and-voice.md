# 模型、人设与角色声音

高级「调用预算」可选择完整上下文：`autonomy.context_mode = "full"`。
由独立 MC 框架保留已收到的事件原文、工具结果/说明、完整决策历史和宿主已提供的投影，
仅按实际窗口预算省略整条记录，状态可查看省略数量；精简模式仍可选。
宿主选择、身份归属、新鲜度和隐私边界继续生效，无法补回宿主没有提供的历史。
独立在线规划器使用自己的显式配置与环境变量，不改变 Neko 主程序的模型、密钥或角色语音。

选用本地 NInfer Chat 服务时，可在自主框架配置的 `[planner]` 显式设置 `cache_strategy = "ninfer"`。
该固定依赖在发送请求时标记稳定历史的缓存边界，最新现场仍在末尾；默认关闭，不猜测模型或本机端口。
128K 是服务实际支持时的窗口配置，不代表每轮必须填满，也不等于持续自主生存已验收。

主 API 负责 Neko 猫娘的对话/实时交互；辅助 API 是宿主按职责配置的文字、记忆整理、视觉及 Agent 服务。插件不读取或复制这两个区域的密钥，也不会自动把辅助密钥当作规划模型。

## 普通用户配置

1. 从 Neko 的 API 设置或插件管理器打开「插件 API」（管理器路径 `/model-api`），创建并保存模型槽。
2. 在 Minecraft Autonomous 的插件详情中绑定 `decision`：需要文字和工具调用。支持图像的模型还可同时绑定 `vision`；未绑定图像用途时回到文字事实。
3. 可选 `fast` 是聊天模型快速选择槽。专用 Jev 使用插件的 System One 设置，二者接口不同；未配置时保留规则选择和原生安全反射。
4. 在本插件「配置与控制」中设置服务器/账号，再显式启动。保存配置、导入包和绑定模型都不会自动登录游戏。
5. 首页「用当前猫娘声音解说游戏」默认开启，点击快捷启动会配置公开语音通路；角色留空跟随当前猫娘。默认官方本机地址为 `http://127.0.0.1:48911`，自定义端口在高级设置修改。无需另填音色 ID 或 TTS 密钥；关闭首页开关即可安静游玩。普通 `start` 仍沿用已保存设置，不隐式开启语音。

台词复用动作规划的同一次模型响应，人设来自公开角色资料，当前意图及选出的记忆/近期对话仍走显式共享上下文。不是复制所有私人记忆，也不会额外调用一个解说模型。主脑心跳可独立处理目标、用户反馈和重要复盘；关闭它不阻止已启动框架自行规划和产生台词。

声音使用 Neko 已配置的角色 TTS。插件通过公开 `/api/characters` 选取必要人格字段，通过 `/api/game/minecraft_autonomous/speak` 投递纯台词；不读取音色 ID、语音凭据或修改主程序。旧宿主缺少公开接口时报告不可用，不绕过认证或接管其它游戏的语音路由。

语音与身体各自推进。先将意图台词放入有界队列，再派发动作；不能保证网络合成的声音一定先于游戏动作开始。普通台词默认最小间隔 30 秒，只有有效新句可被读取；过期、换角色/世界、收到新意图、死亡代际变化或停止时不播放旧句。输出结果不明时不自动重播。合成/发送回执和桌面播放证据分别记录，台词不当作游戏动作成功。

官方宿主每次定时回调使用单独的事件循环。语音定时回调因此等待自己已经领取的有界发送任务结束，避免回调返回时取消它；这一等待不占用 MC 身体、模型规划或主脑回调线程。

## 开发边界

插件模型调用使用公开 `await ctx.models.get_client()` 和清单用途别名 `decision` / `vision` / `fast`。官方网关不自动注入主脑人设或历史，适配层需显式传入受限上下文。独立模式可自行配置模型，其进程不会继承 Neko 模型密钥或临时网关令牌。

核心 `agent-mc-autonomy` 的 `NarrationPort` 与任何宿主 SDK 无关。宿主在拥有的新鲜身体快照下订阅，规划器可在同一次 `mc_do` 调用的可选 `_spoken_text` 中给出台词，控制器会在原生参数校验和派发前移除该宿主字段。也可在原响应 assistant content 提供严格 JSON `{"spoken_text":"一句台词"}` 并保留正常动作调用。其它分析文本不会自动变成语音。端口仅负责台词有效期、限频和一次领取，不调度原生身体队列。

已对照 Neko 官方主干 `ec09fca56ad146a55855d59f12969a3bac12c9f8`：

- [插件模型槽约定](https://github.com/Project-N-E-K-O/N.E.K.O/blob/ec09fca56ad146a55855d59f12969a3bac12c9f8/docs/design/plugin-model-slots.md)
- [公开游戏语音接口](https://github.com/Project-N-E-K-O/N.E.K.O/blob/ec09fca56ad146a55855d59f12969a3bac12c9f8/main_routers/game_router/runtime.py)

发布包通过官方 `sync` 准备依赖、`check --release --market-release` 检查，并由 `build` 生成带静态 `plugin.meta.json` 的 `.neko-plugin`。普通用户从插件中心导入，不直接复制 Git 目录。完整离线包的资源和发布状态另见安装指南及 Release 附件；源码提交不等于 Market 审核通过。
# 本地模型的上下文计数

0.6.48 消费共享框架 0.2.46 的可选 `planner.context_counter`。它属于独立模型适配，
不是 Neko 主程序补丁。只有明确选择本地 NInfer Chat，并提供与实际模型产物匹配的
tokenizer、模板、SHA256 和服务默认参数时才启用；计数在 CPU 上执行，不增加模型调用。
可选依赖、绑定校验和安全回退见共享框架的
[模型接入说明](https://github.com/jcs130/minecraft-ai-friend/blob/e5cf569cb2599dee718333d65005fdf69a66872c/packages/agent-mc-autonomy/docs/model-providers.md)。
普通用户继续沿用 Neko 的模型绑定，不需要下载本地 tokenizer 或填写这些高级参数。

full 模式的完整交换、精确引用及只在真实溢出后退役旧历史的规则，见
[上下文窗口与复用](https://github.com/jcs130/minecraft-ai-friend/blob/e5cf569cb2599dee718333d65005fdf69a66872c/packages/agent-mc-autonomy/docs/context-window-cache.md)。

新版兼容服务返回的 `reasoning` 与旧版 `reasoning_content` 均由共享框架按提供方作用域保存，
不进入口播正文。仅在服务明确支持时设置 `planner.parameters.thinking_token_budget`，
有限思考预算须小于输出预算；这不会提高模型上下文容量。模型或模板改变时，旧 NInfer
计数绑定不能继续沿用；没有匹配的精确计数器时，保守上界可能提前退役历史。
