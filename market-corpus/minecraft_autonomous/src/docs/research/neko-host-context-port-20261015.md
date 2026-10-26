Owner: agent_mc_autonomy.context.assemble、ReviewPort，以及 minecraft_autonomous 的 Neko 主脑上下文适配边界。

# Neko 主脑与 Minecraft 自主框架的上下文组合研究

日期：2026-10-15。结论以本地第一方源码为准，属于只读架构研究，不代表实现、部署或真实模型效果已验收。未读取角色配置、个人记忆正文、凭据或日志；未调用 HTTP 接口、模型、游戏、消息、服务或进程控制接口。

## 结论

当前缺口是 **语义上下文没有接通**：Neko 主脑可以检查游戏事实、提出阶段目标并解说；实际规划请求由 MC 框架独立组装，只含通用玩家规则、游戏环境、目标、游戏事实和自己的工具历史。使用 Neko 的模型槽只复用了模型调用能力，没有继承 Neko 角色的人格、相关记忆或当前会话。

最小可实施方案是在 agent-mc-autonomy 增加一个可注入的 HostContextPort：主脑继续拥有身份、人格、个人记忆解释和当前意图，MC 框架把宿主选出的有界上下文真正放入每次 planner 请求，同时继续独占具身 turn、原生队列、回执和未知动作处理。Neko 适配器只提供这些材料，不建立第二份人格、记忆抽取循环或动作调度器。

SDK 没有完整人格/当前请求快照的正式读取口。已有 SDK 总线只能补充近期实际对话与近期用户话语。先复用 Neko 主脑现有回合与原生 recall_memory，再由 review 工具携带选出的相关材料，是不修改主程序/SDK的可行起点。由模型描述的内容必须标为 host_projection，不能冒充精确的原始人格或完整主脑请求快照。

## 核对基线

以下为读取时 HEAD；没有 fetch、修改引用、提交或推送。

| 范围 | 本地路径 | HEAD / 声明版本 |
|---|---|---|
| Neko 插件 | D:/neko-plugins/minecraft_autonomous | 7468cfad06cfeab9ad81204e74547fb47c12ae31；0.5.35 |
| canonical runtime/core | D:/neko-plugins/_sources/minecraft-ai-friend | 0b440ecebc13ba0ddd34afb611b67a38975cb7b6；agent-mc-autonomy 0.1.52，agent-mc-world 0.5.4 |
| Cortico fork | D:/neko-plugins/_sources/Cortico | 8c00fbb022ea82fc9725301fde0437ccc00fdcc9；0.1.8 |
| 历史 World fork | D:/neko-plugins/_sources/cortico-world-qiandengji | 084234b24f816562d6493da9d57fe9f11b0a7874；0.3.0-next.2 |
| Neko 公共接口源码 | D:/neko-mc-trial/N.E.K.O | bbfc07dcbb397953e697d52efbf480dcaab89bbb |

插件声明同时固定 autonomy 来源为 0b440ece…；core 分发回执声明 source_revision 为 7e8b9750015bb31b7307da6dfb03a90302ce9cef、版本 0.5.4。前者是当前 Python runtime 所在仓库提交，后者是 core 构建来源，不能混用。[插件依赖]、[runtime 版本]、[core 来源]。

## 当前调用链的实际缺口

| 已核实行为 | 根因/影响 | 第一方证据 |
|---|---|---|
| Controller.run 从 mc://environment 取得 prefix；turn_once 将其和 goal/facts/recent/groups 交给 assemble | prefix 来自游戏环境，没有 host persona/context 输入 | [controller 环境]、[controller 组装] |
| assemble 固定 system = RULES + prefix，RULES 的身份是通用 autonomous Minecraft player | 此时没有宿主身份、人格、相关个人记忆插槽 | [context 入口] |
| Planner 协议只有 decide(messages, tools) 与 aclose | 模型调用端没有身份/记忆提供者参数 | [planner 协议] |
| NekoPlanner 原样提交框架传来的 messages；direct planner 也是同一条输入链 | 使用相同提供商、模型或 Neko usage slot，不会自行获得主脑上下文 | [Neko planner]、[planner 选择] |
| Neko 网关校验并深拷贝调用者请求，替换 model 与默认参数；没有读取角色并补人格的逻辑 | 共享模型网关本身不能补上缺口 | [网关请求]、[网关编码] |
| 主脑 heartbeat 通过 push_message 发起 respond；review 工具只接 heartbeat_id/decision/reason/goal/lineage_offset | 回传面只包含决策与自然语言阶段目标，没有人格/记忆/会话上下文 | [review 通知]、[review 工具] |
| ReviewPort.decide 的 choice 是 decision/reason/goal；insert/revise 最终 mailbox payload 只有 goal/mode/origin/parent/revision | reason 留在 review 状态，实际 planner 不会读到这段主脑依据；continue 更不会给 planner 增加语义材料 | [review 决策]、[review 投递] |
| goal_prompt 已区分 user 与 host_review，现有 mailbox 也保留目标父链 | 当前用户目标和阶段建议已有可复用权威边界，不能把 host context 当另一条用户指令入口 | [目标权威]、[mailbox schema] |

因此，调整解说风格、增加“你是主脑”字样、扩大 review reason 字数，均不会单独改变实际 planner 的认知输入。需要改变 canonical 请求组装接口，并证实材料进入了最终 messages。

## Cortico fork 实际怎样组合

Cortico 的公共契约规定 Persona 负责 systemSegments、sessionHead、prepareRequest、Memory 解释和 cognition；Core 负责机械生命周期。World 只提供环境、事件、工具及事实。该分工在源码里有实际装配，不只是概念描述。[Persona 契约]、[请求投影契约]、[Cortico 哲学]。

### 人格与常驻材料

Cormini.systemSegments 渲染 PREFIX 模板，并将每个 World 的环境提示加入各自段；prefixVars 读取 orientation、Memory 目录里的 CONSTITUTION.md、partnerDoc、Memory 协议和工作区目录。它不是把所有个人记忆塞进游戏 World。[Cormini 组装]。

CortiV 的 bot 装配明确传入同一 loaded.memoryDir、World 集合、ORIENTATION 默认文件、部署 prompts/ORIENTATION.md 覆盖路径和部署首轮对话目录。CONSTITUTION 的种子只负责缺失初始化，后续前缀读取实际 Memory 文件；首轮对话作为风格锚由 sessionHead 提供。[CortiV 装配]、[Cormini Memory 初始化]、[首轮上下文]。本研究只检查这些读取路径和协议，没有打开任何 deployment/Memory 文件。

### 动态记忆、上下文与意图

CortiV.prepareRequest 从同一个主 session 的记录开始，投影 stateMemory 当前结论，并读取：

- recentMemoryNote：stateMemory.summary + 近期短笺的历史来源说明；
- longTermMemoryIndex：明确配置的 Memory 文件的有界原文节选与来源；
- 世界当前 facts、近期发言、动作证据；
- pendingWork、activityAgenda、观众身份/相关档案、referenceLibrary。

它将这些作为 pins 带进本轮请求，再做前台历史预算与投影。旧记录留在完整 session，投影不等于另建人格或删掉历史。[CortiV 请求投影]、[当前 World facts]、[记忆来源]。

长期索引最多取 8 个明确配置的文件，总计 4,000 字符；节选保留原文行号、来源说明和按需 read_file 入口，不能据“未展示”推断已完成或取消。这里没有另跑一个汇总模型。[Memory 索引]。

### 后台 planning/cognition 仍归同一 Persona

planningMessages 的材料类型直接包含 constitution、memories、pending、activity、capturedAt、agenda、observations；实际装配取同一个 Memory 的 CONSTITUTION.md、配置的记忆来源、pendingWork 与现场 facts，并按来源分别分预算。这是人格/记忆/意图进入实际规划请求的直接对照。[Planning 材料]、[Planning 注入]。

普通 acceptCognition 从主 session 快照和当前 World facts 构造材料，再交 Core.spawnFork，工具来自请求 World 与同一 Persona 的文件工具；任务框架明确标识 brief 为 World 材料，不能冒充观众命令。fork 的初始 messages 必须由 Persona 提供；不能假定 Core 会自动补全人格。[共享 cognition]、[fork 初始上下文]。

另有显式的 focused-cognition，只读取单次任务与附件，且不读写工作区。它是窄任务通道，不能用它说明 Cortico 的每条子请求都继承完整人格/历史。[Focused cognition]。

可借鉴的是 **一个宿主语义所有者，按任务选择上下文，多个受其管理的推理通道**。这不要求所有调用使用同一模型或一个无限增长的 chat；但若 planner 持有另一套人格/个人记忆、只接受主脑下达目标，仍然没有达到上述组合。

历史 cortico-world-qiandengji 的 Cortico adapter 负责创建 World；WorldHost 的结构端口提供事件、工具、modelFacts 和可选 cognition，并不自己读取 Persona Memory。[历史 Cortico adapter]、[历史 WorldHost]。当前独立 agent-mc-world 的 WorldHost 已改为中立 decisions 选择端口，它只是 typed choice 服务，没有 Persona/context 导出能力。[当前 WorldHost]、[DecisionPort]。因此本次缺口优先补在 autonomy 的认知请求组装层，无需把 Cortico 实现搬回 core。

## Neko 可用接口及其实际边界

| 接口 | 能取得什么 | 不能据此声称什么 | 证据 |
|---|---|---|---|
| self.ctx.models.get_client | 插件声明的 usage slot 对应的通用模型网关 | 已继承角色人格、聊天 session、记忆 | [SDK 模型]、[网关请求] |
| self.bus.conversations.get / get_by_id | 已发生对话的 content、lanlan_name、turn_type、conversation_id、metadata 等 | 完整 system prompt、工具历史或精确请求快照 | [SDK conversation schema]、[总线发布语义] |
| self.bus.memory.get(bucket_id, limit) | 容量受限、TTL 一小时的近期用户话语 | 持久人格、facts/reflections 或语义检索 | [SDK 总线说明] |
| ctx.query_memory | 兼容调用 | 真正语义召回；它调用已弃用 search_for_memory 占位端点 | [旧召回实现] |
| system_info.get_system_config / get_server_settings | 插件系统白名单设置；后者解包前者的 config | 角色配置、人格、MAIN_SERVER_PORT 或 MEMORY_SERVER_PORT | [系统白名单]、[server settings] |
| llm_tool / register_llm_tool(role=…) | 让主脑在已有回合显式交付结构化上下文；可按已配置角色限制工具可见性 | callback 自动携带主脑完整上下文或可验证的调用者会话身份 | [SDK tool role]、[callback 参数] |

conversations 的 read scope 是共享的，不按插件隔离；适配器必须只选择已绑定角色的必要记录。更关键的是，已发生的 turn 不保证包含一个完整 user/assistant 对，conversation_id 在不同生产者中语义不同；源码还明说当前 bus.query 不按 conversation_id 过滤。应在读到的记录上显式检查角色、来源、时间和组配，不把 get_by_id 的名称当服务端强匹配保证。[SDK 总线说明]、[总线发布语义]。

SDK 的通用 _ctx 可含 run_id、lanlan_name 等入口元数据，但 llm-tool callback 的公开请求形状只有 name/arguments/call_id/raw_arguments，路由把 arguments 直接交 trigger。没有证据证明这条 callback 总能传真实 lanlan_name 或当前主脑 request。因此身份应由 adapter 已配置 target 与有效 review 绑定；模型写出的 agent_id/turn_id 只作声明，不能提升为宿主认证事实。[入口元数据]、[callback 参数]。

### 公开角色档案 API

GET /api/characters?language=zh-CN 返回 characters 对象：猫娘为名称到 profile 的映射；当前猫娘为当前名称。另有 GET /api/characters/current_catgirl 返回 current_catgirl。读取时固定使用已有 heartbeat.target；只有产品明确选择“跟随当前角色”时，才按 current_catgirl 解析并在切换时撤销旧上下文。[角色集合]、[当前角色]。

角色 profile 可使用明确选中的名称/昵称/性别等档案字段，以及 system_prompt。system_prompt 从 _reserved 展平给 API；不是供应商配置。应挑选已绑定目标的字段，不把整个 characters、主人资料、其他角色或整个 _reserved 写进模型。返回字段可扩展，不应把任意字段都当人格。[profile 展平]。

这一 API 返回档案，不承诺等于有效主脑 system prompt。运行时还可有 persona、反思、动态记忆与当前会话。来源应标 character_profile，保留版本/内容指纹及采样时间；不得标 exact_host_request。非中文分支还会调用 translation_service.translate_dict，因此不新增生成调用的读取应显式使用 language=zh-CN 的直接返回分支。[角色集合]。

本研究未调用上述 API。Neko SDK 没有公开角色档案读取方法或主服务实际地址读取口；若适配 HTTP，应使用显式配置的 loopback 主服务 base_url，不扫描端口，不读取私有 config，不复用 Market token/凭据，不默认导入宿主私有模块。

### 原生相关记忆召回

主脑已经有内置 recall_memory 工具：只需 query/time，宿主以自己的 lanlan_name、memory_server_port 和 locale 调真实 POST /query_memory/{lanlan_name}，把结果渲染回原回合。优先让主脑在确有需要时用它，再在 review 中交付相关摘录；不会新增独立生成模型循环。[原生 recall_memory]。

记忆服务器接口的已核实 schema 为：

~~~json
{
  "query": "可选的相关主题",
  "time": "可选的小时/日期/月/年/区间",
  "subjects": [
    {"subject_kind": "participant", "subject_id": "已授权的主体", "scope": "可选"}
  ],
  "language": "zh-CN"
}
~~~

query/time/subjects/language 都可省略；subjects 的合法 kind 是 group_chat、participant、group_participant。省略 subjects 是旧私有范围；显式空数组会被拒绝，不能变成全域查询。成功响应包含 results 数组，每条有 id/text/tier/entity/score/created_at/event_start_at/event_end_at，以及 query/candidates_total/elapsed_ms。没有结果与召回失败要区分：失败可能返回空 results 加 error_code=hybrid_recall_failed；未初始化则 503。[记忆请求 schema]、[记忆主体 schema]、[记忆召回实现]、[记忆响应文档]。

该路径使用 BM25、可选 embedding cosine 和 RRF；不会调用生成 LLM 精排。embedding 调用不等于生成调用，也不保证运行时完全没有网络，实际 embedding provider 取决于宿主配置；本研究未访问该配置。[召回算法]。

文档写明默认地址 http://127.0.0.1:48912、配置项 MEMORY_SERVER_PORT，并同时注明其为内部 loopback 服务、非稳定第三方 HTTP 契约。SDK 的公共 system config 白名单不提供该端口；get_server_settings 也只是该白名单别名。不能用默认端口当实际部署事实。[记忆 API 范围]、[系统白名单]、[server settings]。

若以后明确配置 memory_base_url，可增加默认关闭、按此源码版本适配的只读召回端口；不默认启用，不自动发现服务。GET /get_settings 会刷新 reflection suppress 状态；GET /new_dialog 涉及 locale 和会话准备；GET /get_persona 返回版本化内部对象。它们都不适合作为最小无副作用、稳定人格导出口。[settings 实现]、[内部 persona 文档]。

## 建议的最小中立 HostContextPort

以下是建议契约，当前源码尚未实现。

### 数据与所有权

~~~text
HostContextPort.read(binding, now) -> HostContextEnvelope | unavailable

HostContextEnvelope:
  schema: "agent-host-context/v1"
  id, revision
  owner: 宿主适配器提供的稳定 agent 标识
  binding:
    realm/account identity
    runtime session
    review id
    goal id / current user goal id
    intent revision
  captured_at, expires_at
  persona:
    source: character_profile | host_export | host_projection
    version
    text / 显式选中的身份与偏好字段
  memories[]:
    source, source_ref?, observed_at?, text
  conversation[]:
    source, source_ref?, observed_at?, text
  intent_context:
    source: host_projection
    text
~~~

owner、runtime session、goal id、intent revision 与实际生效时间由 adapter/ReviewPort 从已验证的当前 review 派生；模型不能任意声明绑定范围。source_ref 只有存在真实来源标识时才填；模型提供的引用仍为 host_projection 的声明，不能凭一个看似 fact_ 的字符串标为已独立验证。

persona 是宿主提供的个体身份/偏好；memories/conversation 是有来源的历史材料；intent_context 是主脑当前策略与关注点。原生游戏当前事实、显式用户目标及其权威仍由现有框架提供。任何个人记忆都不能覆盖当前世界事实、授权边界或把旧愿望提升成新用户指令。context 更新不产生身体任务。

### 建议接入点

1. **canonical autonomy** 新建中立 host_context 模块，负责 schema、大小、时效、绑定、私有持久化与读取；向 Controller/run_session 提供可注入端口。缺失/不可用时明确记录 unavailable，不能伪造人格。
2. **canonical context.assemble** 接受已验证 envelope。稳定 persona 段与游戏规则/环境一起进入请求前缀；相关记忆、必要会话摘录、主脑当前关注点进入动态尾部，与当前游戏 facts 清楚区分。所有字段纳入现有 ContextCounter/字节回退预算。
3. **Neko adapter** 使用已绑定 target 的公共档案（若显式配置 API）提供 character_profile；在现有 review 工具中增加可选 host_context 材料参数，让主脑选择自己已经读取/召回的相关内容。依赖 canonical 模块写入与验证，不复制政策实现。
4. **模型端** Neko slot 与 direct planner 都消费相同的 assemble 输出，不在 NekoPlanner.decide 之后额外拼接文本；否则会绕过预算，并使 standalone/direct 分支不一致。
5. **World core** 维持现有独立工具、事实、执行和 game-only memory 能力。本次不导入 Neko/Cortico Persona，不把个人记忆存入 mc_memory，也不恢复历史 World 的私有宿主依赖。

“稳定人格前缀”不应包含每轮 captured_at、review_id 或会变化的事实；这些进入动态段，避免相同人格每轮改变前缀。人格版本变化才重建该段。动态材料采用整体条目裁剪，保留 omitted/source 元数据；不能因人格过长挤掉失败回执、新鲜 facts 与当前用户目标，也不能绕过已有超预算拒绝机制。[context 预算]。

### 生命周期必须处理的两个细节

- 当前 mailbox.intent_revision 会被 goal/feedback/stop 更新，而 host_review 的 insert/revise 自身也是 feedback。若 envelope 绑定提交前 revision，刚插入阶段就会自我失效。建议保存 reviewed_revision 和提交后 applies_to_revision/goal_id，或在同一 canonical 事务里原子生成提交后的 binding。continue 更新 context 不应增加目标 revision。[mailbox revision]、[review 投递]。
- 外部用户反馈、显式停止或新 session 必须使旧 context 失效；新增上下文不能恢复已停止框架、确认 unknown dispatch 或取消正在运行的原生任务。模型调用期间收到新指令时，沿用 Controller 已有 preempt/cancel 机制；若 context 单独更新，也需在派发前检查其 revision，不能执行基于被替换材料的迟到模型结果。[调用期间抢占]。

### 私有持久化和公开表面

原文只进 canonical 的私有 host context 存储，按当前 owner/session 存一个最新版本或少量明确保留的版本；不另建可学习人格库。不能把整个 envelope 塞进 current_goal、review choice/reason、tool_failure、model_status 或公开 viewer 文件。ReviewPort.state 会返回完整 review JSON，dashboard 又公开 last_decision，所以给 review 状态随手加原文会扩大泄露范围。[review 状态]、[dashboard]。

public viewer 当前只投影动作、队列、有限 goal 与回执；保持这份白名单。公开或 dashboard 最多报告 available/source/version、采用/过期/省略计数和预算，不返回人格/记忆正文、原始 prompt、会话片段或私人引用。[public viewer]。

## 何时算组合成立，何时仍只是新增摘要

至少应有以下可复核结果，才能称上下文组合接口成立：

- 给同一游戏 facts/goal 注入不同宿主 persona/相关记忆时，最终传给 planner 的 messages 确实包含各自原文或明确标注的摘录，且 source/version/binding 可追溯。
- persona 稳定存在于前缀，动态材料会刷新/失效；不是仅把 summary 放进 review 回执或解说提示。
- 主脑提供的阶段策略和实际 planner 读取的是同一套当前用户目标权威；历史记忆没有覆盖实时事实。
- 主脑仍拥有个人记忆选择/修改与人格版本，planner 没有独立的个人记忆抽取、人格文件或第二个语义目标账本。
- Neko/direct 两条模型路径都受同一预算、工具历史配对、显式停止和 unknown-dispatch 边界约束。

纯 host_projection 是可用的第一阶段上下文桥，但不等于精确主脑请求共享。若最终只拿到一句“我喜欢探索，接下来采矿”，它仍只是较弱的摘要。用有来源的档案字段、原生召回摘录、必要会话原文和当前明确意图分别入槽，才有实际组合的内容与接口。要达到 Cortico 那样的精确宿主上下文控制，后续仍需正式 Host API 导出主脑已选择的 request projection；在禁止修改主程序/SDK的本轮范围内不能宣称已具备。

也不应以接口接通声称“两个调用变成一个 LLM session”。它实现的是共享语义所有者与上下文契约；真实角色连续性、策略一致性和自主游戏收益仍需独立验证。

## 建议验证边界

只读研究没有执行实现测试。后续实现至少用临时状态与 fake model/body 验证：

1. 真正的 assemble 请求包含 persona 与动态材料，并且 host/direct 两个 planner 都收到；空端口保留 standalone 行为。
2. 错 owner、错 session、错 goal/revision、过期、未来时间、超限、重复 id 内容冲突均不进入实际请求；同 id 同内容重试不新增目标。
3. insert/revise 提交后的 binding 正确，continue 能更新 context 但不派发身体动作。
4. 人格版本更新替换前缀，历史材料不累计；预算仍保留最新工具回执、新鲜事实及用户目标，图片回退分支也计入 context。
5. 新用户反馈/stop/session 更换可使迟到结果失效；unknown dispatch 不因 context 更新被解除。
6. public viewer/dashboard/status/audit 投影中不存在测试用私人正文标记；任何持久化恢复均使用有效 owner/session/revision。
7. 若增加 HTTP 档案/召回适配，只测试显式允许的 base_url 与 schema；禁网络/禁进程测试，拒绝从私有宿主配置、默认端口扫描或内部模块导入获取数据。

完成这些只能证明端口、隔离和请求内容正确；真实模型是否沿同一人格记忆行动，需要另行授权的运行窗口、可归因动作与世界结果验收。

## 源码索引

文中链接均指向本地第一方源码的核对位置，行号对应上面的基线。

[插件依赖]: D:/neko-plugins/minecraft_autonomous/pyproject.toml:3
[runtime 版本]: D:/neko-plugins/_sources/minecraft-ai-friend/packages/agent-mc-autonomy/pyproject.toml:7
[core 来源]: D:/neko-plugins/_sources/minecraft-ai-friend/packages/agent-mc-autonomy/src/agent_mc_autonomy/core_distribution/source.json:3
[controller 环境]: D:/neko-plugins/_sources/minecraft-ai-friend/packages/agent-mc-autonomy/src/agent_mc_autonomy/controller.py:985
[controller 组装]: D:/neko-plugins/_sources/minecraft-ai-friend/packages/agent-mc-autonomy/src/agent_mc_autonomy/controller.py:785
[context 入口]: D:/neko-plugins/_sources/minecraft-ai-friend/packages/agent-mc-autonomy/src/agent_mc_autonomy/context.py:71
[context 预算]: D:/neko-plugins/_sources/minecraft-ai-friend/packages/agent-mc-autonomy/src/agent_mc_autonomy/context.py:123
[planner 协议]: D:/neko-plugins/_sources/minecraft-ai-friend/packages/agent-mc-autonomy/src/agent_mc_autonomy/planner.py:17
[Neko planner]: D:/neko-plugins/minecraft_autonomous/neko_planner.py:37
[planner 选择]: D:/neko-plugins/minecraft_autonomous/direct_planner.py:18
[网关请求]: D:/neko-mc-trial/N.E.K.O/plugin/server/model_gateway/request.py:275
[网关编码]: D:/neko-mc-trial/N.E.K.O/plugin/server/application/model_gateway_service.py:93
[review 通知]: D:/neko-plugins/minecraft_autonomous/neko_heartbeat.py:115
[review 工具]: D:/neko-plugins/minecraft_autonomous/neko_heartbeat.py:152
[review 决策]: D:/neko-plugins/_sources/minecraft-ai-friend/packages/agent-mc-autonomy/src/agent_mc_autonomy/review.py:246
[review 投递]: D:/neko-plugins/_sources/minecraft-ai-friend/packages/agent-mc-autonomy/src/agent_mc_autonomy/review.py:299
[目标权威]: D:/neko-plugins/_sources/minecraft-ai-friend/packages/agent-mc-autonomy/src/agent_mc_autonomy/controller.py:927
[mailbox schema]: D:/neko-plugins/_sources/minecraft-ai-friend/packages/agent-mc-autonomy/src/agent_mc_autonomy/mailbox.py:42
[mailbox revision]: D:/neko-plugins/_sources/minecraft-ai-friend/packages/agent-mc-autonomy/src/agent_mc_autonomy/mailbox.py:32
[调用期间抢占]: D:/neko-plugins/_sources/minecraft-ai-friend/packages/agent-mc-autonomy/src/agent_mc_autonomy/controller.py:442
[Persona 契约]: D:/neko-plugins/_sources/Cortico/src/core/types.ts:1007
[请求投影契约]: D:/neko-plugins/_sources/Cortico/src/core/types.ts:1057
[Cortico 哲学]: D:/neko-plugins/_sources/Cortico/PHILOSOPHY.md:14
[Cormini 组装]: D:/neko-plugins/_sources/Cortico/bots/cormini/persona/persona.ts:328
[CortiV 装配]: D:/neko-plugins/_sources/Cortico/bots/cortiv/index.ts:249
[Cormini Memory 初始化]: D:/neko-plugins/_sources/Cortico/bots/cormini/persona/persona.ts:206
[首轮上下文]: D:/neko-plugins/_sources/Cortico/bots/cormini/persona/persona.ts:432
[CortiV 请求投影]: D:/neko-plugins/_sources/Cortico/bots/cortiv/persona/persona.ts:780
[当前 World facts]: D:/neko-plugins/_sources/Cortico/bots/cortiv/persona/persona.ts:918
[记忆来源]: D:/neko-plugins/_sources/Cortico/bots/cortiv/persona/persona.ts:2220
[Memory 索引]: D:/neko-plugins/_sources/Cortico/bots/cortiv/persona/memory-index.ts:37
[Planning 材料]: D:/neko-plugins/_sources/Cortico/bots/cortiv/persona/planning-review.ts:143
[Planning 注入]: D:/neko-plugins/_sources/Cortico/bots/cortiv/persona/persona.ts:541
[共享 cognition]: D:/neko-plugins/_sources/Cortico/bots/cortiv/persona/persona.ts:1083
[fork 初始上下文]: D:/neko-plugins/_sources/Cortico/src/core/types.ts:460
[Focused cognition]: D:/neko-plugins/_sources/Cortico/bots/cortiv/persona/focused-cognition.ts:19
[历史 Cortico adapter]: D:/neko-plugins/_sources/cortico-world-qiandengji/integrations/qiandengji/cortico.ts:6
[历史 WorldHost]: D:/neko-plugins/_sources/cortico-world-qiandengji/src/client/contracts.ts:55
[当前 WorldHost]: D:/neko-plugins/_sources/minecraft-ai-friend/packages/agent-mc-world/src/contracts/host.ts:56
[DecisionPort]: D:/neko-plugins/_sources/minecraft-ai-friend/packages/agent-mc-world/src/contracts/decision.ts:1
[SDK 模型]: D:/neko-mc-trial/N.E.K.O/plugin/sdk/shared/core/models.py:87
[SDK conversation schema]: D:/neko-mc-trial/N.E.K.O/plugin/sdk/shared/core/bus_context.py:229
[总线发布语义]: D:/neko-mc-trial/N.E.K.O/main_logic/agent_event_bus.py:949
[SDK 总线说明]: D:/neko-mc-trial/N.E.K.O/docs/zh-CN/plugins/sdk-reference.md:365
[旧召回实现]: D:/neko-mc-trial/N.E.K.O/plugin/server/application/messages/memory_query_service.py:38
[系统白名单]: D:/neko-mc-trial/N.E.K.O/plugin/settings.py:1000
[server settings]: D:/neko-mc-trial/N.E.K.O/plugin/sdk/shared/runtime/system_info.py:86
[SDK tool role]: D:/neko-mc-trial/N.E.K.O/plugin/sdk/plugin/base.py:550
[callback 参数]: D:/neko-mc-trial/N.E.K.O/plugin/server/routes/llm_tools.py:62
[入口元数据]: D:/neko-mc-trial/N.E.K.O/plugin/sdk/shared/core/entry_runtime.py:19
[角色集合]: D:/neko-mc-trial/N.E.K.O/main_routers/characters_router/crud.py:986
[当前角色]: D:/neko-mc-trial/N.E.K.O/main_routers/characters_router/crud.py:1454
[profile 展平]: D:/neko-mc-trial/N.E.K.O/utils/config_manager/reserved_schema.py:546
[原生 recall_memory]: D:/neko-mc-trial/N.E.K.O/main_logic/core/tool_calling.py:208
[记忆请求 schema]: D:/neko-mc-trial/N.E.K.O/app/memory_server/routes.py:2300
[记忆主体 schema]: D:/neko-mc-trial/N.E.K.O/app/memory_server/routes.py:1694
[记忆召回实现]: D:/neko-mc-trial/N.E.K.O/app/memory_server/routes.py:6464
[记忆响应文档]: D:/neko-mc-trial/N.E.K.O/docs/zh-CN/api/memory-server.md:108
[召回算法]: D:/neko-mc-trial/N.E.K.O/memory/hybrid_recall.py:17
[记忆 API 范围]: D:/neko-mc-trial/N.E.K.O/docs/zh-CN/api/memory-server.md:1
[settings 实现]: D:/neko-mc-trial/N.E.K.O/app/memory_server/routes.py:6574
[内部 persona 文档]: D:/neko-mc-trial/N.E.K.O/docs/zh-CN/api/memory-server.md:165
[review 状态]: D:/neko-plugins/_sources/minecraft-ai-friend/packages/agent-mc-autonomy/src/agent_mc_autonomy/review.py:158
[dashboard]: D:/neko-plugins/minecraft_autonomous/dashboard.py:128
[public viewer]: D:/neko-plugins/_sources/minecraft-ai-friend/packages/agent-mc-autonomy/src/agent_mc_autonomy/viewer_public.py:48
