# 千灯纪 1.21.1 接入验收边界

2026-10-15 北京时间 23:35，使用已获授权的现有 `ag_NEKO` 账号连接用户确认的公网入口 `micro.kangqiang.site:25702`。同一 Mineflayer 连接完成正常登录，服务端结构化认证从 pending/authorized=false 变为 authenticated/authorized=true。检查采用原生队列观察模式，没有规划模型、测试聊天或游戏任务；结束后正常断开并排空。

收到服务端直接系统消息 `MC_GUIDE`（guideVersion `2026-10-12.8`）和 `MC_STATUS`，包括当前职业、法力、准备技能及任务、背包、规则和能力查询入口。真实状态显示 SURVIVAL，生命与上限约20.8、饥饿20，职业 mage、准备技能 mage_bolt。当前普通身体快照仍只列原版字段，不能把收到文本当作专用技能已可执行。

## 独立扩展职责

- 默认仍为普通 Mineflayer 1.21.1。地址预设只填草稿，不自动登录或启用千灯纪技能。
- 最新远端已有独立 `agent-mc-qiandengji@0.5.0-dev.20261015.2`，位于 `cortico-world-qiandengji@1ad4de238d89222107db3538dc6548865a3e90d0` 的 `packages/qiandengji`，并提供中立入口及资料清单；此前本机旧检出尚未包含它。应采用公开接口的中立部分，不加载旧宿主装配或迁移其工具命名空间。
- 新扩展应依赖核心公开的 `/extension` 和 `/tactics` 接口，复用同一 bot、原生队列和终态回执。解析直接系统消息时绑定实际本人 UUID、当前连接和序列，限制大小及保留原文查询入口；换服、换人、重连清空当前投影。
- `MC_GUIDE` 作为玩法资料；`MC_STATUS` 作为带观测时间的服务器事实。它们不是宿主系统提示，也不能授权任意命令、修改凭据或自动重放未知动作。技能、交易、任务和个人大背包按实际能力声明及真实回执逐项映射。
- 共享观战和服务器扩展必须组合在同一个世界引擎中，保留 `TacticalMinecraftWorld` 及 start/createBridge/stop 生命周期；不能各开一个身体或互相替换。
- 玩法、技能、魔力、自定义物品和资源映射属于可选扩展。只有匹配当前版本、注册表及实际收到的能力数据时显示专用界面；未匹配时明确说明不可用，不能套用旧1.20.6资源或将普通服误判为千灯纪。

## 实际发现及待验收

公网原版观战已收到真实区块、库存和位置。现场界面曾显示生命20/1，而服务器状态给出约20.8/20.8；密集区域实体快照报告 `NATIVE_ENTITY_SNAPSHOT_BUDGET_EXCEEDED`。这两项属于共享渲染链的待定位问题，不能在千灯纪扩展里写死数值或扩大无界预算掩盖。

玩法权威源码已在 MC friend 的 `qiandengji-personal-stash@22e0eb1f4657e1d24fe606dedfb49a9c91434412` 找到：`paper/plugins/AgentFriend/src/org/afuhome/agentfriend/AgentCommunication.java` 的指引版本与现场 `2026-10-12.8` 一致，协议见 `paper/docs/AGENT_CLI_OBSERVATIONS.md` 和 `CLI_CONTRACT.md`。该分支保持隔离读取，不覆盖主干或服务器。

`paper/docs/PAPER_1_21_1_UPGRADE.md` 仍描述旧 Agent/相机通过 ViaBackwards 使用1.20.6资源；原生1.21.1观战资源需另做匹配转换。`paper/item-appearances/catalog.json` 和 `ITEM_APPEARANCES.md` 记载五种自定义模型731001—731005及大背包头像，普通原版资源不能冒充已支持这些外观。此插件的主力1.21.1配置保持，不能为了套用旧包而静默降级。

新扩展仍存在自动查询技能及 physicsTick 施法接线，不能直接在只读检查中整包装载；当前中立入口也尚未解析现场 `MC_GUIDE`/`MC_STATUS`。后续应先补协议投影与显式能力开关，再分别验收查询、技能、任务、大背包和匹配资源。正常登录保护不属于服务器故障；当前没有证据需要服主修复。原版长期自主补给、生命恢复和无人干预生存仍是发布前验收条件，不能由公网登录成功替代。


2026-10-20 大背包显示修复：可选资料包按当前本人随身物品的实际 profile 纹理匹配原始 PNG，可独立启用外观；不再等待技能/法力状态。该外观投影不附带生命、技能、法力或权限，不触发状态命令，不按物品名/基础 ID 猜纹理。通用名称解析接受现代普通文本组件，保留 `minecraft:player_head` 作为真实基础 ID。
