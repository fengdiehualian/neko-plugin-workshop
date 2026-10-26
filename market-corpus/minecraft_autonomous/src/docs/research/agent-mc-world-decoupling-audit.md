# Agent MC World 解耦审查：构建、发布、运行与接口

审查日期：2026-10-13。本文件的上游审计仅静态阅读源码和已安装包，没有启动游戏、模型或连接服务器，也没有修改产品源码；插件侧复查另见[主报告](fork-improvements-and-decoupling-boundary.md)。本文区分已经看到的代码事实与建议验收标准，不把静态检查或断连测试称作在线验收。

## 1. 结论和固定审查对象

当前状态应描述为：**已有可独立消费的 client/MCP 入口与自有基础契约，但游戏引擎的开发/构建仍依附 Cortico fork；包根入口、类型、可选旁路及宿主调度语义尚未完全分离。** 不是“完全没解耦”，也不能只凭无 Cortico 工具发现测试就称为完全独立。

| 对象 | 本次实际核实的版本 |
| --- | --- |
| Agent MC World 源码 | `D:/neko-plugins/_sources/cortico-world-qiandengji`，`084234b24f816562d6493da9d57fe9f11b0a7874`；主任务 fetch 后确认 origin/main 仍是此提交。 |
| 已安装包 | `D:/neko-plugins/minecraft_autonomous/local/backend/node_modules/agent-mc-world`，`0.3.0-next.2`。 |
| 构建用 Cortico | `jcs130/Cortico@8c00fbb022ea82fc9725301fde0437ccc00fdcc9`。这是用户 fork 的固定提交，不能用官方上游或其他 Persona 的印象替代。 |
| 源码补丁数量 | checkout 的 `release-sources.json` 为 **34 项**；已安装包为 **33 项**，少 `minecraft-goal-closure.patch`。 |
| viewer 来源 | checkout：`8536b28516e99ae78a0d11d74f9cef329242c10b`；安装包：`6cd94b034f3064b74771464b192046b6d537f6f0`。两者不是同一生成产物。 |

证据：[固定来源清单](https://github.com/jcs130/cortico-world-qiandengji/blob/084234b24f816562d6493da9d57fe9f11b0a7874/release-sources.json)、[安装包实际清单](<D:/neko-plugins/minecraft_autonomous/local/backend/node_modules/agent-mc-world/release-sources.json:1>)、[源码开发约定](https://github.com/jcs130/cortico-world-qiandengji/blob/084234b24f816562d6493da9d57fe9f11b0a7874/AGENTS.md)。Cortico fork 与官方 upstream 的功能差异应另按共同祖先与实际改动核对；本报告不把提交数量当成独立功能数量。

## 2. 必须分开的四种依赖

| 依赖层 | 代码事实 | 判断 |
| --- | --- | --- |
| 开发/构建 | `build-release.ts` 要求固定 Cortico checkout，调用其发布规划器，复制其 Minecraft 目录，再改 import、应用补丁和打包工具。 | **硬构建耦合仍在。** 没有 Cortico 源码及其脚本，无法用当前流程重新生成游戏引擎。 |
| 安装/执行 | 生产 dependencies 没有 `cortico`；通用 client 与 MCP 使用包内引擎、自有 contracts 和打包的工具函数。 | 不应说运行时必需启动 Cortico；但本次没有覆盖连接后所有执行分支。 |
| 类型/入口 | `agent-mc-world` 根入口默认导出 Cortico `MC` 适配器；适配器声明仍 import `cortico/world.ts`。`/client` 的类型图另有独立保证。 | **根入口仍以 Cortico 适配为默认产品面。** `/client` 独立不等于所有 exports 独立。 |
| 行为/宿主合同 | 事件触发、工具屏障和结束回合字段在独立 MCP 转换中丢失；部分默认值、旁路端点及 viewer 发现协议沿用旧部署结构。 | 需要中立的执行/事件合同及可选集成边界，而不是简单删除带 Cortico 名字的文件。 |

证据：[构建抽取](https://github.com/jcs130/cortico-world-qiandengji/blob/084234b24f816562d6493da9d57fe9f11b0a7874/scripts/build-release.ts#L24-L73)、[包入口和依赖](https://github.com/jcs130/cortico-world-qiandengji/blob/084234b24f816562d6493da9d57fe9f11b0a7874/package.json#L25-L63)、[根入口](https://github.com/jcs130/cortico-world-qiandengji/blob/084234b24f816562d6493da9d57fe9f11b0a7874/src/index.ts#L1-L4)、[MCP 映射](https://github.com/jcs130/cortico-world-qiandengji/blob/084234b24f816562d6493da9d57fe9f11b0a7874/src/mcp/server.ts#L53-L70)。

## 3. 构建和发布：不是一个已经自有源码的独立引擎

`scripts/build-release.ts:25–69` 的实际流程是：

1. 从 `CORTICO_SOURCE_DIR` 或 `.sources/cortico` 取得指定 Git HEAD，并检查受关注源目录没有修改。
2. **直接 import Cortico 的 `scripts/publish-worlds.ts`**，执行 `planWorldPackage('minecraft')`，依赖其目录约定、导入重写和外部依赖枚举规则。
3. 复制 `src/worlds/minecraft` 到本项目 `engine/`；将部分 Cortico 类型和 typed-decision 引用替换到包内 support。
4. 顺序应用补丁，再打包 host utilities；生成哈希来源文件。
5. 另外从固定 `mc-visual-console` 提取共享画面资源和 host 桥接代码。

这不是仅仅引用一个第三方 npm 运行库；Minecraft 引擎的源码所有权和维护入口仍在 Cortico fork 加补丁栈中。`engine/`、`dist/`、`.sources/` 被 `.gitignore` 排除。若目标是独立维护，应一次性迁入需要保留的游戏源码并明确本仓库维护权，之后独立构建，不能长期靠每次复制旧 Cortico 目录再正则改写 import。[实际抽取流程](https://github.com/jcs130/cortico-world-qiandengji/blob/084234b24f816562d6493da9d57fe9f11b0a7874/scripts/build-release.ts#L14-L73)、[忽略规则](https://github.com/jcs130/cortico-world-qiandengji/blob/084234b24f816562d6493da9d57fe9f11b0a7874/.gitignore)、[被依赖的 Cortico 规划器](https://github.com/jcs130/Cortico/blob/8c00fbb022ea82fc9725301fde0437ccc00fdcc9/scripts/publish-worlds.ts#L98-L125)

现有哈希和来源核验值得保留：`verify-release.mjs` 核对源提交、补丁顺序与哈希、host-runtime、声明文件、viewer 桥接、资源和页面产物。但这些证明的是所列产物存在且匹配预期来源，**不证明无宿主耦合、宿主语义等价或游戏在线可用**。[发布核验代码](https://github.com/jcs130/cortico-world-qiandengji/blob/084234b24f816562d6493da9d57fe9f11b0a7874/scripts/verify-release.mjs#L1-L62)

建议形成独立 core 构建任务，Cortico adapter 的构建与兼容测试另设任务。共享 viewer 可以继续作为版本固定的独立组件；保留其必要依赖与来源不是 Cortico 架构耦合。当前 `build` 连带构建 Cortico 控制台，`devDependencies.cortico` 为 `0.1.8`，因此不能用“只有 devDependency”掩盖开发工具链仍需宿主的问题。[构建脚本和开发依赖](https://github.com/jcs130/cortico-world-qiandengji/blob/084234b24f816562d6493da9d57fe9f11b0a7874/package.json#L106-L142)、[控制台打包入口](https://github.com/jcs130/cortico-world-qiandengji/blob/084234b24f816562d6493da9d57fe9f11b0a7874/scripts/build-console.mjs)

## 4. bundled host-runtime：需要摆脱构建来源，但不是隐藏的大脑

`build-host-runtime.ts` 从 Cortico 四个文件提取并 bundle：`nowIso`、`nullLogger`、`createIpcLogger`、`emitLogNote`、`logChildStdio`、`logLines`、`withAnchors`、`renderTemplate`。它把执行器 import 改为包内 `engine/support/runtime.mjs`，把子进程 loader 改为本项目的 `child-process.ts`。[打包器](https://github.com/jcs130/cortico-world-qiandengji/blob/084234b24f816562d6493da9d57fe9f11b0a7874/scripts/build-host-runtime.ts#L5-L31)

安装包中此文件实测大小 **5457 字节**，唯一外部 import 是 `node:async_hooks`。读到的内容是日志锚点、时间格式、错误规范化、IPC 日志和模板替换；没有 Core 主循环、Persona、模型调用或服务启动逻辑。`../../../.../.sources/cortico/...` 是 esbuild 来源注释，不会按那个路径加载源码。[实际 runtime 文件](<D:/neko-plugins/minecraft_autonomous/local/backend/node_modules/agent-mc-world/engine/support/runtime.mjs:1>)

因此整改应是把必要的中立工具源码纳入自己维护、保留来源和许可、使构建无需 Cortico checkout。没有必要重写成熟的日志语义，也不能把注释中的旧目录当成运行错误。`src/runtime/host-runtime.d.mts` 已使用项目自己的 Logger 等类型，这是已完成的边界工作。[自有类型](https://github.com/jcs130/cortico-world-qiandengji/blob/084234b24f816562d6493da9d57fe9f11b0a7874/src/runtime/host-runtime.d.mts)、[独立子进程 loader](https://github.com/jcs130/cortico-world-qiandengji/blob/084234b24f816562d6493da9d57fe9f11b0a7874/src/runtime/child-process.ts)

## 5. 根入口、声明文件和测试覆盖有边界

`src/index.ts` 默认导出 `MC`，经 `src/definition.ts` 到 `src/adapters/minecraft.ts`。适配器的 `WorldDefinition` 是 `import type`，不应把这个类型 import 当成必然的 JS 运行加载；但安装包 `dist/types/src/adapters/minecraft.d.ts` 仍原样引用 `cortico/world.ts`，所以根导出的声明图并未独立。[根入口](https://github.com/jcs130/cortico-world-qiandengji/blob/084234b24f816562d6493da9d57fe9f11b0a7874/src/index.ts)、[适配器](https://github.com/jcs130/cortico-world-qiandengji/blob/084234b24f816562d6493da9d57fe9f11b0a7874/src/adapters/minecraft.ts#L1-L31)、[实际发布声明](<D:/neko-plugins/minecraft_autonomous/local/backend/node_modules/agent-mc-world/dist/types/src/adapters/minecraft.d.ts:1>)

`build-types.mjs` 同时将根入口、client、contracts、MCP 作为声明入口；它遍历声明依赖图，排除外部 `node_modules` 的再发布，却不会消除适配器对 Cortico 的声明引用。这避免把整个私有引擎实现强加给 SDK 消费者，但不等于根入口变成中立客户端。[声明生成流程](https://github.com/jcs130/cortico-world-qiandengji/blob/084234b24f816562d6493da9d57fe9f11b0a7874/scripts/build-types.mjs#L12-L51)

建议根入口默认导出中立 client/执行合同；Cortico 放独立 adapter 子包或显式可选入口，其类型依赖只留在那里，不能要求普通 N.E.K.O 消费者安装它。若保留旧根入口兼容，应明确版本迁移和弃用期；不要突然删除旧部署需要的入口后仍宣称兼容。

| 现有测试 | 实际覆盖 | 没有覆盖的结论 |
| --- | --- | --- |
| `client-types.test.ts` | 禁止解析 Cortico，编译 `src/client/index.ts` 及发布版 client 声明消费样例。 | 不是全部 exports、根默认适配器的独立性测试。 |
| `minecraft-client.test.ts` | 断连时通用配置/工具发现、fake World 的 connect 和原文事件；受禁止 import loader 保护的 MCP 状态/资源查询。 | 没有真实引擎子进程登录服务器、动作、取消、重连、viewer 或附加服务路线验收。 |
| `mcp.test.ts` | fake World 的事件/图片/制品转换，以及旧集成的断连工具发现。 | 不证明五种 trigger、屏障、endsTurn 或整轮语义被保留。 |
| `release-contract.test.ts` | Cortico API 5 dry mount、未连接构造、资产存在等。 | 正是可选宿主兼容性测试，不能替代无宿主消费者测试。 |

证据：[类型测试](https://github.com/jcs130/cortico-world-qiandengji/blob/084234b24f816562d6493da9d57fe9f11b0a7874/tests/client-types.test.ts#L6-L46)、[通用入口测试](https://github.com/jcs130/cortico-world-qiandengji/blob/084234b24f816562d6493da9d57fe9f11b0a7874/tests/minecraft-client.test.ts#L16-L83)、[MCP 测试](https://github.com/jcs130/cortico-world-qiandengji/blob/084234b24f816562d6493da9d57fe9f11b0a7874/tests/mcp.test.ts#L16-L85)、[dry mount](https://github.com/jcs130/cortico-world-qiandengji/blob/084234b24f816562d6493da9d57fe9f11b0a7874/tests/release-contract.test.ts#L1-L31)。以上本次均只阅读，没有重新执行。

禁止 import 的测试也检测不到所有耦合：已 bundle 进文件的函数不会再解析 `cortico/`，复制到 engine 内的服务器协议不必经 `integrations/` 路径，普通 HTTP fetch 也不是模块 import。[loader 检查范围](https://github.com/jcs130/cortico-world-qiandengji/blob/084234b24f816562d6493da9d57fe9f11b0a7874/tests/fixtures/deny-cortico-loader.mjs)

## 6. 运行默认值、旁路模型和 viewer 仍应进一步分层

**已完成的中立默认值不要误删。** 通用 `MINECRAFT_CLIENT_DEFAULTS` 将 host/username 置空、版本设 auto、viewer 默认端口 0，关闭本地服务器自启/作弊、观察者自动旁观及玩家自动传送。它确实覆盖了引擎的本地服旧默认值，默认不会因为 engine 里存在 `corti` 或 `cheats:true` 就按旧配置启动。[通用覆盖](https://github.com/jcs130/cortico-world-qiandengji/blob/084234b24f816562d6493da9d57fe9f11b0a7874/src/client/config.ts#L1-L14)、[安装包引擎旧默认](<D:/neko-plugins/minecraft_autonomous/local/backend/node_modules/agent-mc-world/engine/config.ts:4>)

但当前类型直接别名 `MinecraftConfigSection`，通过 spread 继承整个旧默认对象，仍带本地服管理、原生观察者、玩家客户端、可选演出/快速模型等配置；观察者默认名 `CortiCam` 也没有被通用层覆盖。建议中立核心显式定义需要的配置，原生客户端、本地服控制台、演出桥接由可选组件负责。已有关闭状态应保持，不能把整改理解为启用这些功能。

| 核实的残留 | 真实影响与整改建议 |
| --- | --- |
| `CORTICO_SOURCE_DIR` | 是构建期的真实源目录要求，属于必须移除的硬构建耦合。 |
| `CORTICO_EDGE_EXECUTABLE` | 是截图运行时读取的浏览器路径开关；不调用 Cortico，但运维配置仍使用旧品牌。提供中立配置键，旧键仅作明确兼容别名。 |
| `CORTICO_MC_CREDENTIAL_FILE` | 本包向 Windows 子进程传递的内部临时环境变量，不依赖外部 Cortico 服务；真正平台限制是该凭据实现使用 Windows DPAPI。可迁移命名或抽成凭据 provider，不能输出或改动私有凭据。 |
| 引擎中的 AgentFriend、`/mycli`、施法与快战术 | 通用 facade 明确禁用并过滤 `mc_cast`/`mc_combat_tactic`，这使默认接口更中立；但协议代码仍随通用 engine 发布。应把服务器特有能力迁成显式插件，不只靠隐藏工具。 |
| `decision`、`combatDecision`、`idle` 的外部选择请求 | 默认关闭/空端点。开启后分别可以直接向配置端点 fetch；不是从宿主统一 `cognition` 必经的路径。应成为显式可注入的可选能力，默认无旁路模型请求；确定性反射不依赖它们。 |
| 现代 viewer 的语音服务发现 | viewer 构造 `ViewerSpeechRelay`；页面首次加载及每 20 秒请求 `/speech-source`，后端扫描 `127.0.0.1:7792–7796` 的 `/identity`，匹配 sourceId 后转发。不是必需启动 Cortico，但确实沿用了固定端口的演出服务发现协议。应改为显式关闭/启用、配置端点或宿主提供的字幕接口。 |

证据：[浏览器开关](<D:/neko-plugins/minecraft_autonomous/local/backend/node_modules/agent-mc-world/engine/visual-capture.ts:94>)、[凭据子进程](<D:/neko-plugins/minecraft_autonomous/local/backend/node_modules/agent-mc-world/engine/server-login.mjs:5>)、[默认禁用 AgentFriend](https://github.com/jcs130/cortico-world-qiandengji/blob/084234b24f816562d6493da9d57fe9f11b0a7874/src/client/world.ts#L9-L24)、[引擎工具过滤](<D:/neko-plugins/minecraft_autonomous/local/backend/node_modules/agent-mc-world/engine/world.ts:2653>)、[可选选择默认值](<D:/neko-plugins/minecraft_autonomous/local/backend/node_modules/agent-mc-world/engine/config.ts:69>)、[任务受阻选择入口](<D:/neko-plugins/minecraft_autonomous/local/backend/node_modules/agent-mc-world/engine/world.ts:8439>)、[战斗 fetch](<D:/neko-plugins/minecraft_autonomous/local/backend/node_modules/agent-mc-world/engine/combat-decision.ts:127>)、[空闲选择 fetch](<D:/neko-plugins/minecraft_autonomous/local/backend/node_modules/agent-mc-world/engine/idle-behavior.ts:377>)、[viewer relay](<D:/neko-plugins/minecraft_autonomous/local/backend/node_modules/agent-mc-world/engine/viewer-speech.ts:5>)、[浏览器定时请求](<D:/neko-plugins/minecraft_autonomous/local/backend/node_modules/agent-mc-world/engine/speech-bubble-client.js:15>)、[viewer 装配](<D:/neko-plugins/minecraft_autonomous/local/backend/node_modules/agent-mc-world/engine/modern-viewer.ts:448>)。

这里描述的是可达代码路径，不是声称当前用户机器正在请求这些端点。通用默认 viewerPort 为 0，旁路选择也关闭；本次没有打开页面或访问这些服务。项目自己的架构文档亦承认快战术等尚未完全拆出。[现有边界说明](https://github.com/jcs130/cortico-world-qiandengji/blob/084234b24f816562d6493da9d57fe9f11b0a7874/docs/architecture.md#L23-L36)

## 7. 去掉宿主依赖时，不能把必要执行语义一起丢掉

自有 `contracts.ts` 已定义 TriggerMode、ToolOutcome、ToolDef、WorldRequestFacts 等。结构与 Cortico 兼容不等于它必须依赖 Cortico 运行；真正问题是独立 transport 是否保留这些合同。应让核心定义中立的游戏执行语义，Cortico/N.E.K.O 适配器各自负责映射。[自有合同](https://github.com/jcs130/cortico-world-qiandengji/blob/084234b24f816562d6493da9d57fe9f11b0a7874/src/client/contracts.ts#L1-L78)

| 当前确定的损失/近似 | 必须保留的中立行为 |
| --- | --- |
| `AgentJournal.pushEvent` 不保存 `options.trigger`，`pushDeferred` 不接收 trigger options。 | 保留事件原文、来源、run/cursor、时刻及投递优先级；由宿主执行合批/唤醒/抢占策略。 |
| MCP tools/list 仅保留 readOnlyHint，结果映射不传动态 `endsTurn`。 | 宿主能知道应等待、结束这一轮、禁止同一批后续动作或允许取消；不靠中文回执正则猜。 |
| `activeCalls===0` 即调用 `onTurnEnded`。 | 工具完成与宿主整轮结束分离；按回合/任务 ID 处理去重、取消和清理。 |
| 标准 stdio 未注入 cognition、真实模型名和窗口。 | 能力显式协商，未提供就诚实降级；执行器不能自行假定某个模型或代替宿主开新主脑。 |
| 最近事件只保留 1024 条供当前查询，换 run 游标重置。 | 缺口显式报告并可恢复当前事实；持久化读到/投递/处理水位，不能将缺少终态当作成功。 |

证据：[事件记录](https://github.com/jcs130/cortico-world-qiandengji/blob/084234b24f816562d6493da9d57fe9f11b0a7874/src/client/journal.ts#L7-L96)、[MCP 字段映射](https://github.com/jcs130/cortico-world-qiandengji/blob/084234b24f816562d6493da9d57fe9f11b0a7874/src/mcp/server.ts#L53-L70)、[轮结束近似](https://github.com/jcs130/cortico-world-qiandengji/blob/084234b24f816562d6493da9d57fe9f11b0a7874/src/client/runtime.ts#L130-L155)、[stdio 能力装配](https://github.com/jcs130/cortico-world-qiandengji/blob/084234b24f816562d6493da9d57fe9f11b0a7874/src/mcp/stdio.ts#L18-L24)。这些是本实现的缺口，不是 MCP 规范天然禁止扩展元数据。

值得保留的行为包括：一个 bot/执行器、身体控制权、独立于模型轮次继续执行的动作队列、实际终态及目标后验、确定性自保、停止优先、连接 generation、过期结果拒绝、当前事实与增量历史分离、按需帮助和原文取证入口。应该从 Cortico 宿主名称下提炼为通用机制，而不是因为有历史来源就重新造一套低层移动/挖掘/碰撞控制。更多已核实机制见 [Cortico 宿主研究](cortico-host-architecture.md) 与 [Mineflayer 接口研究](mineflayer-protocol-and-harness.md)。

## 8. 哪些只是归属或持久化兼容记录

| 名称/文件 | 应怎样处理 |
| --- | --- |
| `LICENSE-Cortico.txt`、原作者署名、来源 commit、补丁/生成哈希 | 保留。它们说明代码来源，不构成活动宿主依赖。迁出源码也不能据此抹去归属。 |
| bundle 中的旧源路径注释 | 可在重构构建时自然更新，不应当成会读取旧目录的证据。 |
| `cortico.procedure/v1`、procedure receipt MIME | 是现有方法制品的序列化标识。执行模块只用选中的 blob 和哈希，不需 Cortico 进程。若引入中立新版本，需兼容读取和明确迁移，不能机械全局替换让历史方法失效。 |
| `cortico-realm.json` | 既有世界身份记录文件名。命名可迁移，但要保留旧身份和关联状态，不能靠删除文件获得“零 Cortico 字符串”。 |
| `Symbol.for('cortico.minecraft.…')`、DOM capture 标记、launcher_name | 主要是内部标记或品牌残留；应审查冲突和兼容再命名，不等于调用 Core/Persona。优先级低于硬构建、默认入口、行为合同和旁路服务。 |

证据：[许可复制与来源记录](https://github.com/jcs130/cortico-world-qiandengji/blob/084234b24f816562d6493da9d57fe9f11b0a7874/scripts/build-release.ts#L61-L73)、[procedure 规范常量](<D:/neko-plugins/minecraft_autonomous/local/backend/node_modules/agent-mc-world/engine/support/procedure.ts:1>)、[方法制品边界说明](https://github.com/jcs130/cortico-world-qiandengji/blob/084234b24f816562d6493da9d57fe9f11b0a7874/docs/architecture.md#L38-L44)、[realm 标记](<D:/neko-plugins/minecraft_autonomous/local/backend/node_modules/agent-mc-world/engine/server.ts:160>)、[依赖补丁标记](<D:/neko-plugins/minecraft_autonomous/local/backend/node_modules/agent-mc-world/engine/dependency-patches.ts:130>)、[启动器标记](<D:/neko-plugins/minecraft_autonomous/local/backend/node_modules/agent-mc-world/engine/client-launch.ts:251>)。

## 9. 建议验收清单

下列是后续整改的验收要求，不是本次已经通过的测试：

1. 一个全新独立 checkout 仅安装通用生产/开发依赖，就能构建 core、client、MCP、公共声明；不要求 Cortico 源 checkout、`CORTICO_SOURCE_DIR`、Cortico 发布脚本或宿主开发包。可选适配器单独构建测试。
2. 从实际发布 tarball 而非开发目录消费每个公共入口；根/client/contracts/MCP 的类型和运行闭包均受禁止宿主解析检查。显式 Cortico adapter 是有文档的例外。
3. 离线合同测试覆盖 trigger、endsTurn、barrier、取消信号、回合边界、任务终态、换 run/gap、当前事实替换；网络访问通过可观测端口约束，默认不请求额外模型或旧演出服务。
4. 源码集成分开：通用 Minecraft、共享 viewer、服务器扩展、宿主适配。测试应检测嵌入 engine 的特定服务器协议，不只检测 `integrations/` 目录名。
5. 发布核验继续记录来源/许可证/哈希及兼容格式；源码、生成包和部署版本不能仅凭相同 `0.3.0-next.2` 标签当作一致。
6. 后续获得运行授权时，再使用允许的服务器验证连接、动作、终态、显式停止、取消和重连。离线通过与在线验收分别报告；当前停止状态继续保留。

## 10. 可转发整改说明

统一的整改提示词及 Cortico fork 差异结论见[主报告](fork-improvements-and-decoupling-boundary.md)，本文件作为其构建、发布、运行和接口事实依据。
