# DSH 更新复用核对

本轮核对 `jcs130/dsh-minecraft-agent` 的 rc.19 主干，以及当前固定核心 rc.82 到 rc.86 的变化。重点源码为 DSH [d526fb8](https://github.com/jcs130/dsh-minecraft-agent/commit/d526fb8bc1797e089d1ed39574329ada97d4ed8d) 和共享核心 [eb001cef](https://github.com/jcs130/minecraft-ai-friend/commit/eb001cef396d0d40b7218317e9b93746346b4ef6)、[01fe3bd1](https://github.com/jcs130/minecraft-ai-friend/commit/01fe3bd11421549eac8e3be02db82b6bd0abf82a)。源码、安装包、实际部署与自然游戏效果分别记录。

| 能力 | Neko 接入方式 | 边界 |
|---|---|---|
| 离线词元向量与关键词混合检索 | 自主框架转发 `memory.mode = "local-vector"`，面板可选 | 核心实现；不下载模型、不发 embeddings 请求，不是神经语义模型 |
| 矿物查询策略 | `perception.mineral_vision`，默认 false | 仅已接收且 32 格内的矿物；不放宽移动、实体视线、碰撞或服务器权限 |
| 稳定笔记角色标识 | 可选 `memory.agent_id` → 核心 `memoryAgentId` | 留空保留现有笔记命名空间；更换标识不自动迁移/合并；账号目录和原始记录保持 |
| 矿点、地点、方法与经验笔记 | 原有 `mc_memory` 继续复用同一核心 | 来源、维度、原始回执、版本和历史不确定性保留；不把旧记忆当当前事实 |
| 主人笔记编辑页 | 核心已有在线/离线 `client.notebook`，作为后续 UI 接线依据 | 本次未增加第二个写入器或绕过运行身体的独占进程；主人报告不能标成运行观测 |
| 上传本地 PNG 皮肤 | 核心已有尺寸/CRC/解压上限校验和本人预览来源标记 | Neko 已有 YUI/服务器外观选择；通用上传还需共享渲染器与本人来源校验同步验收，本次不宣称已提供上传入口 |
| 人物、背景与初始记忆 | 继续用 Neko 当前角色及公开主脑投影 | DSH 人物预设属于宿主人格，不作为服务器事实或覆盖现有人设 |
| DSH 能力预设装配 | 留在 DSH 官方 `agentPresets.mount` | 不复制 DSH 插件加载器、权限规则或压缩服务到独立框架 |
| AuthMe、观战就绪与世界挂载公共合同 | 随固定 rc.86 消费和回归验证 | 挂载/迁移辅助器由宿主显式选择；不因更新调用旧迁移脚本、切账号或搬运行目录 |

配置保存不连接游戏、不调用模型。默认检索仍是 `local`，矿物查询扩展默认关闭，稳定笔记标识默认空。已有用户可只将检索模式改为 `local-vector`，保留同一目录和笔记所有者；安装新包不会自行启用外部记忆服务、DSH、训练或额外模型循环。

适配逻辑只包含配置校验、转发和 Neko 面板，游戏记忆和感知算法在 `agent-mc-world`，规划/上下文/队列仍归 `agent-mc-autonomy`。离线测试验证合同与包内容，不等于实际长期项目、战术改善、语义视觉、皮肤显示或桌面声音验收。
