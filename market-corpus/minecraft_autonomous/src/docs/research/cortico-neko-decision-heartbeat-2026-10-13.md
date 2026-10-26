# Cortico 主脑与 Minecraft 执行层的持续决策闭环

核查日期：2026-10-13。结论基于用户 fork 的固定源码和独立执行包协议；本次只读源码、fetch 远程引用，没有运行 Cortico、模型、Minecraft 或服务。下面的 Neko 适配是设计建议，不能当作已部署或游玩验收。

## 结论

用户描述的方向正确：其 Cortico 组合中的 Persona 主会话会结合现场与目标主动决定下一步，游戏工具执行、结果事件重新唤醒，解说在这个决策闭环内。更准确地说，它由**任务结果和有效变化驱动，配合有退避的定时复核**，不是只靠固定频率发送一句“继续生存”。

最直接的证据是 fork 的 Persona 指引：队列空闲或定时醒来要选择下一件事；没人回复不意味着停止生活；已有足够证据要接成实际行动；相同条件下重复观察不算进展；身体活动与是否说话分别判断。已经决定的下一步要提交实际工具，然后简短解说，不等观众同意或语音播完。[行为指引：ORIENTATION.md:9–25](https://github.com/jcs130/Cortico/blob/8c00fbb022ea82fc9725301fde0437ccc00fdcc9/bots/cortiv/persona/ORIENTATION.md#L9-L25)

本插件在 `581e65b` 的自动呈现提示明确要求“一句当前情况”且“无需再查询或派任务”；它只证明 Neko 能收到观测和演出，不能证明主脑在持续决定游戏方向。独立 controller 已有自主动作循环，但这不能代替 Neko 主人格的参与。[当时的呈现入口：neko_presentation.py:64–118](https://github.com/jcs130/n.e.k.o_plugin_minecraft_autonomous/blob/581e65bf6760aa2ac8d8d734b862bfdcffbeb2a1/neko_presentation.py#L64-L118)、[独立循环：controller.py:597–640](https://github.com/jcs130/n.e.k.o_plugin_minecraft_autonomous/blob/581e65bf6760aa2ac8d8d734b862bfdcffbeb2a1/autonomy/controller.py#L597-L640)

## 核实版本

| 仓库 | 本次 fetch 后的 main | 阅读方式 |
| --- | --- | --- |
| `jcs130/Cortico` | `8c00fbb022ea82fc9725301fde0437ccc00fdcc9` | 本地 HEAD 与远程一致 |
| `jcs130/cortico-world-qiandengji` | `09dc900c58be624806fc4391c6d36fc565c4bf73` | `git show origin/main:<path>`；未切换旧 checkout |
| `jcs130/minecraft-ai-friend` | `9591fdda73841837dfe453ec1b4c3d8f0d070c60` | `git show origin/main:<path>`；未切换旧 checkout |
| Neko 插件比较基线 | `581e65bf6760aa2ac8d8d734b862bfdcffbeb2a1` | 修改前提交；不引用并行开发的未提交代码 |

这次没有读取用户当次成功运行的私有部署开关，不能断言所有可选机制都已启用。Cortico 内容来自用户 fork，不是用官方原版代替；fork 在共同祖先 `29bda9837c8d96d49be45977d17b10f2b7c86ed9` 后确实增加了当前事实尾部投影、阅读/响应时机建议和 Core 回合扩展。可对照 [前台时机修改 47d7ce1](https://github.com/jcs130/Cortico/commit/47d7ce19337effd9cce49b7b5091337dc72f7660)、[状态去重修改 086abce](https://github.com/jcs130/Cortico/commit/086abcec2ad5b3943681f01ebeb04fd9dcbe00ba)、[延后输入去重 e245fc2](https://github.com/jcs130/Cortico/commit/e245fc20d77a9b1aadf757150f2ba6728da3beb5)；这些功能仍需分别看默认开关。

## 实际组合机制

| 时机 | 源码行为 | 移植应保留的意义 |
| --- | --- | --- |
| 一单结束或受阻 | 任务结果与当前队列放在一起，通常以 `flush` 立即投递；取消事件不单独唤醒 | 让主脑拿到结果后接续目标，避免只见旧受理回执 [1] |
| 当前任务进入最后一步且后面无待办 | `pushDeferred` 在真正投递时核对连接代、任务和队列，再现读事实；`debounce` 唤醒 | 可以提前安排后续；当前任务继续，不要求停止或替换 [2] |
| 普通状态采样 | 按分区比较变化；未实质变化不发；定时快照只 `piggyback`，正文投递时才生成 | 状态刷新与模型唤醒分开，不能每次扫视都启动主脑 [3] |
| 暂时没事件 | 可选 Persona 心跳，连续空拍间隔翻倍，最多 8 倍；外部非快照活动复位 | 有兜底复核，但不能制造高频空转 [4] |
| 一轮暂时无需行动 | `end_turn` 显式结束当前唤醒，后续事件/心跳再叫醒 | 主脑结束生成不等于取消原生工作 [5] |
| 新输入 | `flush/debounce` 只改变投递时机；`preempt/interrupt` 才有明确定义的模型/可中断工具取消语义 | 不能把一条普通观察或聊天一律当立即换任务 [6] |

[1] [world.ts:8058–8115](https://github.com/jcs130/Cortico/blob/8c00fbb022ea82fc9725301fde0437ccc00fdcc9/src/worlds/minecraft/world.ts#L8058-L8115)。[2] [world.ts:8033–8055](https://github.com/jcs130/Cortico/blob/8c00fbb022ea82fc9725301fde0437ccc00fdcc9/src/worlds/minecraft/world.ts#L8033-L8055)。[3] [world.ts:8416–8484](https://github.com/jcs130/Cortico/blob/8c00fbb022ea82fc9725301fde0437ccc00fdcc9/src/worlds/minecraft/world.ts#L8416-L8484)。[4] [heartbeat.ts:1–81](https://github.com/jcs130/Cortico/blob/8c00fbb022ea82fc9725301fde0437ccc00fdcc9/bots/cormini/persona/heartbeat.ts#L1-L81)、[快照不重置退避：persona.ts:539–545](https://github.com/jcs130/Cortico/blob/8c00fbb022ea82fc9725301fde0437ccc00fdcc9/bots/cormini/persona/persona.ts#L539-L545)。[5] [end_turn 工具：persona.ts:72–87](https://github.com/jcs130/Cortico/blob/8c00fbb022ea82fc9725301fde0437ccc00fdcc9/bots/cormini/persona/persona.ts#L72-L87)。[6] [触发模式：types.ts:592–613](https://github.com/jcs130/Cortico/blob/8c00fbb022ea82fc9725301fde0437ccc00fdcc9/src/core/types.ts#L592-L613)、[总线：bus.ts:63–145](https://github.com/jcs130/Cortico/blob/8c00fbb022ea82fc9725301fde0437ccc00fdcc9/src/core/bus.ts#L63-L145)。

这里有两个容易误读的细节：

- CortiV 的默认 Persona 心跳是 **45 分钟**，心跳自身文本只有安静时长。持续游玩的密集反馈主要来自 World 事件和随批次现读的事实；不能把默认值解读成每 45 分钟才玩一次，也不能说原框架已经是每 5 秒一次带完整状态的主脑请求。[默认配置：index.ts:328](https://github.com/jcs130/Cortico/blob/8c00fbb022ea82fc9725301fde0437ccc00fdcc9/bots/cortiv/index.ts#L328)、[装配：index.ts:284–285](https://github.com/jcs130/Cortico/blob/8c00fbb022ea82fc9725301fde0437ccc00fdcc9/bots/cortiv/index.ts#L284-L285)、[心跳正文：persona.ts:246–248](https://github.com/jcs130/Cortico/blob/8c00fbb022ea82fc9725301fde0437ccc00fdcc9/bots/cormini/persona/persona.ts#L246-L248)
- 可选 `FastForeground` 只在新鲜的队列尾步观察等受限条件下建议延后判断，任何其他实质事件会绕过这条延后路径；延后最多 30 秒就注入复核。它默认关闭，且不拥有新增、停止、替换身体任务的权限。[条件与默认值：foreground-adviser.ts:18–83](https://github.com/jcs130/Cortico/blob/8c00fbb022ea82fc9725301fde0437ccc00fdcc9/bots/cortiv/persona/foreground-adviser.ts#L18-L83)、[复核安排：persona.ts:864–894](https://github.com/jcs130/Cortico/blob/8c00fbb022ea82fc9725301fde0437ccc00fdcc9/bots/cortiv/persona/persona.ts#L864-L894)

普通游戏聊天、系统提示、私聊有不同投递策略；自己的公屏消息不回灌成新的自我指令。Persona 明确把游戏聊天和服务器提示当外界资料，而非操作员命令。迁移时仍需保留来源身份，不能因为某消息措辞像命令就升级权限。[聊天接线：world.ts:6875–6908](https://github.com/jcs130/Cortico/blob/8c00fbb022ea82fc9725301fde0437ccc00fdcc9/src/worlds/minecraft/world.ts#L6875-L6908)、[输入边界：ORIENTATION.md:1–7](https://github.com/jcs130/Cortico/blob/8c00fbb022ea82fc9725301fde0437ccc00fdcc9/bots/cortiv/persona/ORIENTATION.md#L1-L7)

## 长动作、队列和上下文

主会话可直接调用 `mc_do`：立即拿任务 ID，原生步骤在后台继续；`append` 添加后续，`afterCheckpoint` 在安全点插入并恢复原工作。`mc_script` 也共用同一个执行器，并等待各单的真实终态。额外的一次模型响应不是维持每个游戏动作的必要条件。[工具契约：world.ts:2199–2244](https://github.com/jcs130/Cortico/blob/8c00fbb022ea82fc9725301fde0437ccc00fdcc9/src/worlds/minecraft/world.ts#L2199-L2244)

Core 的 `barrierAfter` 阻止同一次模型输出中的后续工具提前执行；真实执行工具返回 `endsTurn` 后才结束本轮。这两个字段有控制语义，不能只留下可读提示文本。[loop.ts:1388–1439](https://github.com/jcs130/Cortico/blob/8c00fbb022ea82fc9725301fde0437ccc00fdcc9/src/core/loop.ts#L1388-L1439)、[loop.ts:1464–1485](https://github.com/jcs130/Cortico/blob/8c00fbb022ea82fc9725301fde0437ccc00fdcc9/src/core/loop.ts#L1464-L1485)

`prepareRequest` 是本轮出站请求视图，不改原始会话。CortiV 开启 foreground 后，将当前 World facts、目标/待办等加入 pins，保留稳定历史基段，把当前值放到尾部；覆盖旧快照前要求完整当前事实可用。该优化默认关闭，历史软预算也不等于整个模型请求硬上限；缓存收益仍需实际指标证明。[Core 请求钩子：loop.ts:531–541](https://github.com/jcs130/Cortico/blob/8c00fbb022ea82fc9725301fde0437ccc00fdcc9/src/core/loop.ts#L531-L541)、[Persona 装配：persona.ts:780–861](https://github.com/jcs130/Cortico/blob/8c00fbb022ea82fc9725301fde0437ccc00fdcc9/bots/cortiv/persona/persona.ts#L780-L861)、[当前尾部：foreground-epoch.ts:150–246](https://github.com/jcs130/Cortico/blob/8c00fbb022ea82fc9725301fde0437ccc00fdcc9/bots/cortiv/persona/foreground-epoch.ts#L150-L246)、[默认与预算：foreground-context.ts:6–27](https://github.com/jcs130/Cortico/blob/8c00fbb022ea82fc9725301fde0437ccc00fdcc9/bots/cortiv/persona/foreground-context.ts#L6-L27)

新的独立执行核心已经把这些语义写成 `agent-mc/v1`：事件包含 run/cursor/trigger，facts 按 key 替换且原文可查，`prepareContext` 返回稳定 prefix 与动态 tail；宿主显式开始/结束决策回合、确认屏障。事件提示不在核心偷偷启动模型循环。受理、任务终态和目标达成分别记录。[独立协议：protocol-v1.md](https://github.com/jcs130/minecraft-ai-friend/blob/9591fdda73841837dfe453ec1b4c3d8f0d070c60/packages/agent-mc-world/docs/protocol-v1.md)

当前千灯纪装配通过显式 `serverPack` 选择通用或服务器扩展，并将同一个独立执行核心的工具和事件交给宿主；通用适配器通过展开原工具对象保留控制字段，不另设一套身体执行器。这个依赖方向可借鉴，不需要将 Cortico Persona/Core 实现带回 Neko。[装配选择：cortico.ts:10–32](https://github.com/jcs130/cortico-world-qiandengji/blob/09dc900c58be624806fc4391c6d36fc565c4bf73/integrations/qiandengji/cortico.ts#L10-L32)、[通用适配：generic-world.ts:1–44](https://github.com/jcs130/cortico-world-qiandengji/blob/09dc900c58be624806fc4391c6d36fc565c4bf73/integrations/qiandengji/generic-world.ts#L1-L44)

## 独立 Agent 框架与 Neko 的归属

用户随后明确：游戏唤醒、状态整理、任务推进等放在其 Minecraft 自主 Agent 框架，Neko 插件只做接线。这个边界应作为最终实现要求。Cortico 原主会话直接拥有游戏工具；独立框架消费事件、整理事实和推进任务，Neko 通过中立主脑端口提供同一人格的判断，是这里的适配设计，不是把 Cortico Persona 复制到插件。

在 `minecraft-ai-friend@9591fdd`，`git ls-tree -d origin/main:packages` 只有 `agent-mc-world`，尚无 `packages/agent-mc-host`。`src/runtime/host-runtime.d.mts` 和 `core/support/runtime.mjs` 是日志、时间、模板工具，名称不表示已有自主决策宿主。核心指令明确要求宿主负责模型、人格和学习，不在执行核心暗藏模型循环。因此自主调度层应独立提供或由现有中立 `autonomy/` 迁入独立项目，Neko 再消费该层；不应将主脑心跳策略添加到执行核心内部。[包边界：AGENTS.md](https://github.com/jcs130/minecraft-ai-friend/blob/9591fdda73841837dfe453ec1b4c3d8f0d070c60/packages/agent-mc-world/AGENTS.md)、[所谓 host runtime 的实际导出](https://github.com/jcs130/minecraft-ai-friend/blob/9591fdda73841837dfe453ec1b4c3d8f0d070c60/packages/agent-mc-world/src/runtime/host-runtime.d.mts)、[当前公开入口](https://github.com/jcs130/minecraft-ai-friend/blob/9591fdda73841837dfe453ec1b4c3d8f0d070c60/packages/agent-mc-world/src/client/index.ts)

| 所属项目/层 | 应负责 | 可以复用 |
| --- | --- | --- |
| `agent-mc-world` 执行核心 | 游戏连接、观察、原生队列、工具屏障、任务回执与归档 | 已有 `agent-mc/v1`、`MinecraftAgentClient` |
| 独立 Minecraft Agent 宿主/框架 | 事件合批、变化筛选、复核时机、上下文预算、目标版本、planner、恢复/停止与重复提交控制 | 现有中立 `autonomy/controller.py`、`context.py`、`mailbox.py` 的行为；迁移时保持账本兼容 |
| Neko 插件适配器 | 接收框架准备好的主脑请求，调用公开主会话入口；原样回传主脑决定、用户反馈；展示配置/统计/观战 | 公开 SDK 的消息和工具入口；不复制危险/空闲/停滞等游戏判断规则 |
| Neko 主会话 | 高层判断、人格、用户交流、演出与官方语音 | 正常的同一角色会话 |

核心现有可复用接口：`events({after,run,limit,waitMs})` 读取增量事件并返回 `gap/hasMore`，`currentFacts()` 按键替换，`prepareContext()` 给出预算化 prefix/tail，`beginTurn/endTurn/acknowledgeBarrier` 明确回合边界，`AgentJournal` 保存原文并支持历史查询。缺少的是把这些证据持续整理成对可插拔主脑的 review 请求、管理其在途状态/过期/回复，以及无外接主脑时的独立规划策略；公开 SDK 并未替宿主实现这一整层。[客户端运行时](https://github.com/jcs130/minecraft-ai-friend/blob/9591fdda73841837dfe453ec1b4c3d8f0d070c60/packages/agent-mc-world/src/client/runtime.ts)、[事件账本](https://github.com/jcs130/minecraft-ai-friend/blob/9591fdda73841837dfe453ec1b4c3d8f0d070c60/packages/agent-mc-world/src/client/journal.ts)、[协议声明](https://github.com/jcs130/minecraft-ai-friend/blob/9591fdda73841837dfe453ec1b4c3d8f0d070c60/packages/agent-mc-world/src/contracts/protocol.ts)

```text
agent-mc-world：世界事实 / 原生任务回执
                         ↓
独立 Agent 框架：事件合并、上下文、目标和复核调度
                         ↓ 中立主脑端口
        Neko 薄适配器 ↔ Neko 同一主人格
                         ↓ 决定 / 用户反馈，稳定 request_id
独立 Agent 框架：规划细化 → 同一原生队列 → 回执与新事实

未连接 Neko：独立 Agent 框架使用自己的已配置主脑/规则能力
```

1. **框架准备决策材料。** 至少包含观测时间、目标及版本、身体/补给、当前工作、最近有意义终态和变化原因；缺信息明确未知。单纯扫视成功通常不单独唤醒，但长期只有观察且没有净进展要给持续时长、重复次数和实际证据，让主脑决定是否调整。筛选、退避、轮询与复核计时属于独立框架，插件不再维护另一份。
2. **保持正常工作是正式决策。** `continue` 确认当前策略，不重复提交原目标。后续工作优先追加；真正策略调整才安全点插入；明确取代/停止单独处理。一个未结的同阶段目标不因每次心跳重复入队。定时复核是对现场再判断，不要求强制动作或强制口播。
3. **保留单一执行控制者。** Neko review 工具只把战略决定回传框架，由框架校验并沿已有 mailbox 投递 intent，不直接操作原生 `begin_turn/end_turn/ack_barrier`。这些仍由独立 controller 在收妥回执后独占处理。当前 controller 已实现屏障确认与结束原生回合；让两层分别结束同一回合会破坏原生协议。[现有控制入口：controller.py:350–358](https://github.com/jcs130/n.e.k.o_plugin_minecraft_autonomous/blob/581e65bf6760aa2ac8d8d734b862bfdcffbeb2a1/autonomy/controller.py#L350-L358)、[原生回合结束：controller.py:559–570](https://github.com/jcs130/n.e.k.o_plugin_minecraft_autonomous/blob/581e65bf6760aa2ac8d8d734b862bfdcffbeb2a1/autonomy/controller.py#L559-L570)
4. **复核回执不是完成回执。** 保存 decision/intent ID、依据版本与观察时间；主脑返回 `continue` 或 intent 被受理都不等于目标完成。旧观测、旧用户意图版本或过期连接代不得触发迟到的任务；确认不明复用原 ID 查询，不另造 ID 重放。
5. **普通观察与紧急输入分开。** 普通变化限频、合批，正常长动作不反复打断；死亡、断线、明确用户反馈应有独立的及时处理策略。Neko 的公开 push 接口是否能抢占生成必须按宿主实际能力验证，不能把通知提交成功宣称成 Cortico 的 `preempt/interrupt`。
6. **主脑身份和语音共用现有 Neko。** 使用公开主会话通知和工具入口，避免在插件里另开一个假扮 YUI 的模型循环。旧自动纯解说通知在决策心跳启用后应避免重复推送或给出“不要派任务”的冲突要求；主脑可以行动后自然说话，也可以安静继续。Neko 不在场时独立 planner 继续现有目标。

建议的最小数据契约为：`review_id`、`session/run`、`observed_at`、`intent_revision`、`trigger/reason`、`goal`、`facts`、`queue`、`last_result`、`progress_since_last_review`、`expires_at`；回传 `continue` 或带自然语言目标的 `append/safe_insert`，并携带同一个 `review_id`。时间与状态放动态尾部，工具契约和职责放稳定部分；原文保存在独立账本按需查询。**该契约及决策请求生命周期属于中立 Agent 框架，Neko 只是一个适配实现**，不能要求框架或执行核心依赖 Neko/Cortico。Neko 通知字段长度、CSRF、公开工具包装等宿主差异才留在插件。

验收需要分别证明：主会话真的调用 review/intent 工具、同一请求没有重复派发、独立队列收到实际目标并执行、原生终态和现场变化相符、普通复核不取消长动作、用户新反馈使旧 review 失效、关闭主脑前端后原独立任务仍可继续。收到通知、产生台词、播放音频或离线测试通过，都不能单独证明这些行为。
