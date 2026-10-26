# KARDS 与战雷的信息传递对照

日期：2026-10-25。补丁版本：6.0.2。范围：信息汇总与 N.E.K.O 投递，不训练模型、不提升识别能力。

## 核对依据

War Thunder 参考为 `CN-Zephyr/n.e.k.o_plugin_neko_warthunder` 提交 `7ef37e232117237215ed1b9a8e4add4eef1d4322`。本机安装的 `neko_warthunder` 版本为 0.1.1；本机副本无 Git 元数据，不能仅凭版本证明逐字节等同。核对文件为其 `adapters/neko_dispatcher.py`、`adapters/event_delivery.py`、`core/instructions.py` 与主入口的 tick/context 路径，未使用 WoWS 实现替代。

## 结论

默认使用同一种宿主入口和两条通道。相同的是协议和调度职责，不是战雷的遥测数据或游戏事件。识别能力缺失必须明确传给猫娘，不能因传递协议一致就声称 KARDS 信息完整可用。

| 项目 | 战雷实现 | KARDS 6.0.2 |
| --- | --- | --- |
| 宿主入口 | `plugin.push_message` | 同一 SDK 入口，经统一 HostPushGate |
| 主动短评 | `ai_behavior="respond"`、`visibility=[]` | 默认相同，保留真实目标路由 |
| 被动上下文 | `ai_behavior="read"`、`visibility=[]`、priority=0 | 相同；进入说明与实时事实分别占可替换槽 |
| 目标 | 事件/配置/上下文/环境/宿主角色解析 | 同类解析；不伪造角色，多会话不确定时拒绝 |
| 战斗播报槽 | 单一 coalesce key，replace_pending 意图 | `kards:battle_event`，普通事件也声明替换，critical 才声明中断 |
| 事件正文 | 当前事实、表达意图、只读边界、短句要求 | facts/intents/boundaries/event，携带真实来源及未知项 |
| callback | metadata 中 `host_callback_contract_version` 与嵌套契约 | 同族字段；delivery/freshness/target，原始事件最大年龄与剩余 TTL 分开 |
| 接受后的记账 | 本地提交后记账，后续观察器失败不重发 | 相同；严格区分 accepted、unknown、legacy_unconfirmed、simulated |
| 退出说明 | 取消游戏陪伴上下文，恢复日常聊天 | core 与 context 槽分别失效，提示旧局势不再当前 |

`replace_pending`、`interrupt_pending` 等只放在 metadata；顶层只有已核实的 SDK 参数。宿主是否实际消费替换/打断/callback 预留信息仍是 unknown。没有 generated/played 回执时只报告 SDK 本地提交。

## 本轮发现并修复的整合缺口

1. 卡面观察原先只进入面板，read、respond、查询工具没有共用局势视图。
2. 缺少模型能力和未知原因说明，空列表容易被理解为空棋盘。
3. 原始单位公开结构没有区分当前观测、保留的旧观测和未知侧别。
4. 卡面观察缺少 session/源时间/TTL，重启水位与跨场景残留可能使旧观察继续存在。
5. `enter`/`restore` 原先只有模块方法，没有真实主循环接线；restore 文案还把退出恢复写成重新引用游戏事实。
6. context 游标没有与 core 一起清理，切角色和未知回执可能留下旧上下文或重复推送。
7. 发布时刻被用作观测时刻，旧事实可能被重新标为新鲜。
8. 事件 prompt 只使用 summary 和旧状态；kind 曾由 summary 代替。大 JSON 预算裁剪也可能丢失事件说明或未知原因。
9. 静态画面变化门控可能只运行一次检测，无法完成第二个独立样本确认。

## 统一局势视图

新增 `runtime/situation.py`，生成 `kards_situation_v1`，写入 `MatchState.situation`。

内容包括：

- source：session、帧号、采集时间、UI 实际推理时间和有效性；发布时刻另算。
- capabilities：实际可用界面类别、卡牌可信白名单数量、单位与场景能力边界。
- facts：当前可证实场景、当前观测单位、未知侧别桶、近期可靠事件和新鲜卡面观察。
- retained：单位旧观测单独标记，不把历史身份说成当前盘面。
- unknown/limits：总部血量、资源、当前攻防、行动方、对局边界等不可用项及其原因。

read、respond 和 `kards_get_live_state` 共同读取这份冻结视图；根据各自预算可以压缩明细，但不能把未知改成 0、丢掉事件的敌我/摘要或把缺失单位能力写成空盘。卡面观察不是打出、阵亡或棋盘身份的证据。

卡面仅接受相同 session、明确源帧号、源时间在 2 秒内且模型/区域/可信白名单均允许的观察。未来时间超出统一容差会拒绝。UI 与采集观测新鲜度上限为 1.5 秒；静态画面以新的采集帧做不超过 1 秒间隔的 UI 心跳复查，不重复消费同一帧来增加确认数。

## 上下文生命周期

可信观测发布时先幂等 `enter`，只有本地提交接受或已有同槽说明仍有效时才发布 read 事实。每次发布都让 publisher 检查续期、角色和说明指纹，不能用主入口一个永久标志绕过 TTL 或角色切换。

失效、暂停、停止、场景退出、角色或相关配置变化时，分别清理原 core/context 槽。失败保留有界退避；未知/旧式无回执终态不自动重发。dry-run 和关闭数据同意时零宿主推送，包括清理说明；下次恢复授权后先清理旧槽。不能同时声称“零推送”和“已撤回宿主历史上下文”。

## 有意保留的差异

- 战雷通过遥测获得较完整的事实；KARDS 当前只有有限界面观测。
- 战雷兼容旧 SDK 的无回执为已提交；KARDS 更严格，无回执不记为 accepted，也不据此重发。
- 战雷 context 在遥测连接后可注入；KARDS 局势事实仅在可信观测有效时发布。
- KARDS 的数据同意与 dry-run 硬门覆盖全部 read/respond/context/expire，不照搬参考实现中可能仍注入 read 说明的路径。
- 默认是 respond。显式启用插件自有短句且确有可信短句时才走 blind/chat 分支；本轮不自动启用、不进行真实播报测试。

## 仍未解决的识别限制

生产 `trusted_cards.json` 仍为空；ui_det 仍只允许 `card_back` 和 `end_turn_button`。因此当前不能报告具体卡名、棋盘单位数量、真实行动方、动态数字或胜负；不会靠放宽白名单“补齐信息”。仅按钮信号无法区分所有真实对局边界，本地 game_number 只是观测分段号；按钮消失不能解释为结束或失败。

## 验证结果

- 修复前 Python 3.11 基线：668 passed；当前补丁全量：720 passed，0 failed。
- `test_situation.py` 跨真实代码接缝验证 UI 检测→统一视图→read 文本→respond 正文→查询工具的事实一致性；使用测试专用能力和卡牌白名单，不改生产权重/白名单。
- 真实 `build_situation` 测试载体而非精简手写 JSON：1600 字符上限内仍保留事件 kind、摘要、side、entity、原始事件时间及未知原因；18 个单位可压缩为当前/保留计数，不切坏 JSON。
- 主侧复验一个可信测试卡面场景：prompt 1458 字符，read 1459 UTF-8 字节；事件敌我、摘要、卡面 ID、来源和未知原因均保留。
- 覆盖静态新帧心跳、失败失效与恢复基线、源时间不刷新、卡面过期/未来/跨 session、双槽部分失败、切角色清旧上下文、unknown 回执不重发、consent/dry-run 零宿主调用等行为。
- 模型、类别映射和生产白名单不变：卡牌可信数仍 0，界面只支持两类。

本轮测试和宿主证据记录在仓库外 `kards-acceptance` 的信息整合补丁报告中。测试通过、SDK 本地接受、LLM 生成和 TTS 播放分别核验，不互相替代。正式版不因协议改造完成而越过识别能力验收。
