# 用户 Cortico fork 的增量与独立 Minecraft 边界

核查日期：2026-10-13。范围为公开源码、Git 差异和本插件已固定的发行包；不读取私有部署，不启动旧服务、游戏或模型。这里区分“源码具有能力”“实际部署启用能力”和“实测取得效果”。后两项没有由本次源码审查证明。

## 结论

上一轮研究已经使用 `jcs130/Cortico`，本次补充与官方共同祖先的差异核对。用户 fork 的游戏执行、上下文、动作证据和目标记忆修改必须作为迁移基线，不能以原版 Cortico 代替。**现有 Agent MC World 已去掉独立入口的 Cortico npm 运行依赖，但仍有构建来源、默认入口、可视化服务发现与协议边界上的耦合；尚不能称为彻底解耦。** 逐项证据见[执行包解耦审计](agent-mc-world-decoupling-audit.md)。

本插件继续保持入口未接入。`0.3.0-next.2` 用于固定执行能力和离线传输对照，不能越过解耦验收直接作为已完成的自治后端。

## 版本与比较方法

| 对象 | 本次固定版本 |
| --- | --- |
| 用户 fork `jcs130/Cortico`，fetch 后 `origin/main` | `8c00fbb022ea82fc9725301fde0437ccc00fdcc9` |
| 官方 `Pal-AI-Lab/Cortico`，fetch 后 `upstream/main` | `b094425d958a73f350cb659cb04f39cc56ea8441` |
| 两者共同祖先 | `29bda9837c8d96d49be45977d17b10f2b7c86ed9` |
| 独立客户端源码 `jcs130/cortico-world-qiandengji` | `084234b24f816562d6493da9d57fe9f11b0a7874` |
| 本插件固定发行包 | `agent-mc-world@0.3.0-next.2`，来源 `fb16e2d03e9f321a2c0a583de749c1fa9a0275d3` |

使用 `git merge-base origin/main upstream/main` 与 `git diff <共同祖先> <fork>`，避免把官方在分叉后的更新误算成用户修改。Git 图显示官方独有 18、fork 独有 202 个提交；包含合并与重复移植，不能解释为 202 项独立功能或某一作者的工作量。

fork 相对共同祖先共改变 959 个文件，包含整包纳入的 viewer/VTuber 等。其中 `src/worlds/minecraft` 改变 124 个文件，`bots/cortiv/persona` 改变 36 个文件，`src/core` 改变 10 个文件。数量只用于说明审查范围，下面的结论以代码行为为依据。

## 保留哪些修改，放在哪一层

| 已核实的 fork 修改 | 移植归属 | 验收含义 |
| --- | --- | --- |
| `inventory-click-sync.ts` 等待包含目标槽位与游标的服务端快照，记录 pending/quarantined；`receipt.ts` 区分失败来源 | Minecraft 执行核心 | 保留实际世界确认，不能退回“发包或函数返回即成功” [F1] |
| `method-runner.ts` 为 Agent 编写的方法提供受限调用、取消、轨迹、源码摘要和任务编号 | Minecraft 执行核心及中立方法协议 | 保留受理、执行、校验和目标达成的区别；改造协议时保留旧方法的明确迁移入口 [F2] |
| `WorldRequestFacts.parts`、`requestFacts()` 和独立的请求视图校验 | 中立事实协议 + N.E.K.O 游戏请求构建器 | 保留稳定键、完整分区、工具调用配对；无需依赖 Cortico Core 类型 [F3] |
| CortiV `prepareRequest`/`foreground-epoch` 投影历史并追加当前事实 | N.E.K.O 插件上下文策略 | 保留规则与工具前缀，更新尾部事实；原始账本独立保存，不搬入整个 Persona。foreground 有开关，默认关闭 [F4] |
| `action-evidence.ts` 依工具标签区分行动、发言、读取，关联 call_id 回执 | 游戏任务/证据账本 | “说了打算”和“执行了动作”分开；任务结束不能自动证明目标完成 [F5] |
| `activity-agenda.ts` 的版本化前提、amend/reopen；`state-memory.ts` 的观察来源和有效期 | N.E.K.O 游戏控制器的目标与经验状态 | 保留误判订正、过期与并发版本规则，通过中立数据结构表达，不读取 CortiV 私有 Memory [F6] |
| Core 中的动态 `endsTurn`、请求投影、fork 取消和后台优先级扩展 | 中立宿主能力与 N.E.K.O 调度适配 | 按实际所需定义可选能力，不把 Cortico 会话、provider 或 fork 作为依赖安装 [F3] |

还发现宿主侧存在具体游戏绑定：CortiV 的重复任务复核和战斗复核分别在 `persona.ts`、`combat-reflection.ts` 中识别 `minecraft`/`mymc` 来源及事件类型。直接复制这些逻辑会继续绑定旧命名；应消费中立的、带来源身份和版本的结构化回执。这里没有把它推断为用户正在运行的故障。[F7]

用户 fork 内置的 `packages/cortico-world-qiandengji/package.json` 仍声明 `0.1.0` 与开发依赖 `cortico: workspace:*`；它不是本插件装配的独立 `agent-mc-world@0.3.0-next.2`。不要从该旧内置包覆盖新独立入口。[F8]

## 目标依赖方向

```text
contracts                 不依赖宿主或执行实现
core                   → contracts + Mineflayer
viewer-adapter         → contracts + mc-visual-console
adapters/neko          → contracts + core 的 SDK/MCP 接口
adapters/cortico        → contracts + core 的 SDK 接口
```

中立协议描述事件投递、当前事实、任务/方法回执、取消和必要的回合边界。N.E.K.O 游戏控制器持有目标和唯一的正常动作提交权；确定性自保通过同一身体仲裁。核心不导入 N.E.K.O 或 Cortico，也不主动选择主模型。可选演出桥与服务器特有能力从显式适配器接入。

允许一次性、有来源和许可证记录的源码迁移；后续构建不再依赖 Cortico checkout 或对其内部路径逐次打补丁。保留来源、许可证和必要旧数据迁移标识，不能以全局删除“Cortico”字符串代替解耦。

## 可直接转发的整改提示词

请继续拆分 jcs130/cortico-world-qiandengji，使 agent-mc-world 成为可由 N.E.K.O、MCP 或其他宿主使用的独立 Minecraft 执行核心。以我修改过的 jcs130/Cortico 为能力基线，先逐项核对改动，保留已经做好的库存同步、导航与动作校验、方法执行、回执证据、当前事实分区、请求去重和目标修订能力，不要退回原版实现。当前“不安装 Cortico 也能启动 MCP”只证明一部分运行路径独立：构建仍通过 Cortico 的 publish-worlds.ts 抽取引擎并叠加补丁，根入口仍偏向 Cortico adapter，类型声明存在 cortico/world.ts 引用，可视化仍默认扫描本机 7792–7796 的旧演出服务，部分服务器专用能力只是默认关闭。请把执行源码和构建流程归入独立核心，明确 contracts、core、adapters/cortico、adapters/neko、可选服务器集成与演出桥的单向依赖；核心及其默认导出、公开类型、安装和构建不得要求 Cortico。把事件唤醒策略、工具屏障、可取消性、动态回合结束、任务编号与终态、事实分区和归档查询定义成中立的版本化协议，SDK/MCP 完整传递，不能再丢字段或把单次 RPC 返回当作整轮决策结束。角色、目标、模型调用、长期记忆由宿主负责，通过显式能力接口协作，不在核心藏第二套模型循环。保留同一 bot 的现代可视化；演出端点与特殊服务器能力改为显式选择，未配置不扫描或调用。保留稳定提示词/工具前缀，当前事实在请求尾部按键替换，原文落盘并可按需查询，预算包含工具描述和预留输出。保留许可证与来源，旧方法协议使用明确迁移，不做盲目改名。请提交能力迁移对照表及离线验收：无 Cortico 安装和源码的干净构建、公开类型编译、同一组事件/动作轨迹在 SDK/MCP 的语义一致、任务受理与完成区分、等待可取消、停止后不复活、不扫描旧演出端口、不产生未授权模型请求。保持现有服务停止，不连接真实服务器；源码、构建、离线契约和现场游玩验收分别报告。

## 本插件侧复查记录

本轮未修改产品运行代码。20 项 pytest、官方 Ruff 和 `check --release --market-release` 均通过；禁止网络/Cortico 导入的真实 MCP 断连复查仍发现 23 个工具，`phase=disconnected`、`game_connected=false`、`cortico_installed=false`。官方结构检查未处于 release tag，故未检查 tag/version 对齐，没有发布。

这些结果验证当前插件基础未被文档修订破坏，不覆盖上述尚待完成的核心独立构建、公开类型、viewer 旁路或联网游戏验收。上游测试源码仅作阅读，本轮未运行 Cortico 或 Agent MC World 的全量测试。

## 固定源码

- [F1 库存同步](https://github.com/jcs130/Cortico/blob/8c00fbb022ea82fc9725301fde0437ccc00fdcc9/src/worlds/minecraft/inventory-click-sync.ts)、[回执](https://github.com/jcs130/Cortico/blob/8c00fbb022ea82fc9725301fde0437ccc00fdcc9/src/worlds/minecraft/receipt.ts)。
- [F2 方法执行](https://github.com/jcs130/Cortico/blob/8c00fbb022ea82fc9725301fde0437ccc00fdcc9/src/worlds/minecraft/method-runner.ts)。
- [F3 Core 类型](https://github.com/jcs130/Cortico/blob/8c00fbb022ea82fc9725301fde0437ccc00fdcc9/src/core/types.ts)、[请求视图校验](https://github.com/jcs130/Cortico/blob/8c00fbb022ea82fc9725301fde0437ccc00fdcc9/src/core/request-context.ts)、[Minecraft 当前事实](https://github.com/jcs130/Cortico/blob/8c00fbb022ea82fc9725301fde0437ccc00fdcc9/src/worlds/minecraft/world.ts#L3360)。
- [F4 请求组装](https://github.com/jcs130/Cortico/blob/8c00fbb022ea82fc9725301fde0437ccc00fdcc9/bots/cortiv/persona/persona.ts#L780)、[稳定请求历史](https://github.com/jcs130/Cortico/blob/8c00fbb022ea82fc9725301fde0437ccc00fdcc9/bots/cortiv/persona/foreground-epoch.ts)、[默认开关](https://github.com/jcs130/Cortico/blob/8c00fbb022ea82fc9725301fde0437ccc00fdcc9/bots/cortiv/persona/foreground-context.ts#L18)。
- [F5 动作证据](https://github.com/jcs130/Cortico/blob/8c00fbb022ea82fc9725301fde0437ccc00fdcc9/bots/cortiv/persona/action-evidence.ts)。
- [F6 日程修订](https://github.com/jcs130/Cortico/blob/8c00fbb022ea82fc9725301fde0437ccc00fdcc9/bots/cortiv/persona/activity-agenda.ts)、[事实记忆](https://github.com/jcs130/Cortico/blob/8c00fbb022ea82fc9725301fde0437ccc00fdcc9/bots/cortiv/persona/state-memory.ts)。
- [F7 任务来源绑定](https://github.com/jcs130/Cortico/blob/8c00fbb022ea82fc9725301fde0437ccc00fdcc9/bots/cortiv/persona/persona.ts#L1669)、[战斗来源绑定](https://github.com/jcs130/Cortico/blob/8c00fbb022ea82fc9725301fde0437ccc00fdcc9/bots/cortiv/persona/combat-reflection.ts#L9)。
- [F8 fork 内置旧包](https://github.com/jcs130/Cortico/blob/8c00fbb022ea82fc9725301fde0437ccc00fdcc9/packages/cortico-world-qiandengji/package.json)、[独立发行包来源](../../backend/source.json)。
