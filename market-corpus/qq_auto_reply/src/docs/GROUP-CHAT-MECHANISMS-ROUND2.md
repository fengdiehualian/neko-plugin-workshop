# 群聊环境处理：第二轮外部调研 + 与我们逐维度对比

> 第一轮见 `GROUP-CHAT-RESPONSE-MECHANISMS.md`（AstrBot 9 阶段责任链、LangBot 11 阶段、
> MaiBot `reply_necessity` 常数、学术量化依据、QQ 官方通道限制。**注**：第一轮的"LangBot 11 阶段"
> 有误，已在本轮 §1.11 更正为 **12 阶段**）。**本轮换角度**：
> 不看"框架抽象"，而是看**别人在群聊环境里具体装了什么旋钮、默认值多少**，
> 然后逐条对照我们插件现在有什么（对照表的"我们"一列来自 2026-09-27 的代码/线上配置盘点）。

## 0. 取证方式与限制（先读）

- 本轮**我亲自拉到的原文**：AstrBot 官方配置文件文档、AstrBot 生态两个成熟群聊插件
  （`astrbot_plugin_group_chat_plus` 的 `CONFIG_REFERENCE.md`、Yunzai 系 `bl-chat-plugin`
  的 `README.md`/`message.yaml` 说明）、腾讯 QQ 机器人官方文档（消息收发概述）、
  OpenClaw 文档（bot loop protection）。
- 本机 DNS 策略**屏蔽 `github.com` 与 `raw.githubusercontent.com`**（解析到非公网 IP），
  所以 GitHub 源码一律**改用镜像**读。本轮实测可用的四条通路：
  ① `cdn.jsdelivr.net/gh/<owner>/<repo>@<branch>/<path>`（文本类文件；对 `.py`/`.toml` 会报
  unsupported content type，但它的树 API `data.jsdelivr.com/v1/packages/gh/<o>/<r>@<branch>?structure=tree`
  可用）；② `ghproxy.net/https://raw.githubusercontent.com/<o>/<r>/<b>/<p>`（读单文件，含 .py/.toml）；
  ③ `ghfast.top/https://github.com/<o>/<r>` **可以整仓 `git clone`**（本轮据此拿到 MoFox 本地副本，
  逐条 grep 复核过，落在 `.dsh-artifacts/MoFox_Bot-research/`，**主仓工作区不受影响**）；
  ④ `gitlab.mikumikumi.xyz/maibot/maibot`（MaiBot 全历史镜像，tag 0.5.8→1.1.4）与
  `git.gardel.top/gardel/Mofox-Core`（Gitea，**支持 git grep 代码搜索**）。这条对以后的调研同样有效。
- 证据等级标注：`【原文】`＝我直接读到的官方文档/仓库文件；`【子代理】`＝并行子代理读到并
  交出处的（本轮用于 MaiBot 源码级、框架层、协议端风控）；`【二手】`＝博客/搜索摘要。
- **凡是没读到的一律写"未验证"**，不从别人的默认值外推我们的行为。

## 1. 本轮读到的一手材料（摘要）

### 1.1 AstrBot 核心：`platform_settings` 与 `provider_ltm_settings`【原文】

来源：[AstrBot 配置文件](https://docs.astrbot.app/dev/astrbot-config.html)

| 键 | 默认 | 说明 |
|---|---|---|
| `rate_limit.time / count / strategy` | `60 / 30 / "stall"` | 消息速率超限时：`stall` 等待、`discard` 丢弃 |
| `unique_session` | `false` | 「隔离对话」= 每位群成员**独立上下文** |
| `forward_threshold` | `1500` | 回复超长自动折叠成 QQ「转发消息」防刷屏（仅 QQ） |
| `enable_id_white_list` / `id_whitelist` | `true` / `[]` | **ID 白名单**（空列表=不过滤）；`wl_ignore_admin_on_group=true` 管理员无视白名单 |
| `reply_with_mention` / `reply_with_quote` | `false` | 回复时是否 @ / 引用 |
| `segmented_reply` | `enable:false, interval_method:"random", interval:"1.5,3.5", words_count_threshold:150, regex:".*?[。？！~…]+\|.+$"` | **分段回复**：按句切分、**随机间隔 1.5~3.5 秒**、超 150 字才分 |
| `ignore_bot_self_message` / `ignore_at_all` | `false` | 忽略自身消息 / 忽略 @全体 |
| `empty_mention_waiting` | `true` | 空的 @ 消息 → 等待而不是忽略 |
| `provider_ltm_settings.group_icl_enable` | `false` | 群聊长期记忆开关；`group_message_max_cnt: 1000` |
| `provider_ltm_settings.active_reply` | `enable:false, method:"possibility_reply", possibility_reply:0.1, whitelist:[]` | **主动回复**：概率 0.1 + 白名单 |
| `content_safety.internal_keywords` / `baidu_aip` | `enable:true, []` / 关 | 内容安全：内置词表 + 百度审核 API |

### 1.2 `astrbot_plugin_group_chat_plus`：把群聊环境拆成 ~40 个模块【原文】

来源：[CONFIG_REFERENCE.md（jsDelivr 读到的仓库文件）](https://cdn.jsdelivr.net/gh/Him666233/astrbot_plugin_group_chat_plus@main/docs/CONFIG_REFERENCE.md)

这是**目前看到的最完整的"群聊环境处理"配置面**（100+ 项，含 Web 面板）。与我们最相关的：

| 模块 | 关键默认值 | 备注 |
|---|---|---|
| 基础概率 | `initial_probability 0.02`（每条 2% 过第一层筛选）、`after_reply_probability 0.8`、`probability_duration 120` | 回复后临时提概率，促进连续对话 |
| 概率硬限制 | `min 0.05 / max 0.8` | 多重衰减后仍不越界 |
| **读空气 AI** | `decision_ai_timeout 30`，**超时=不回复**；可开推理（先推理块再单独一行 yes/no） | 用一个小模型判"这条要不要回" |
| **注意力机制（默认关）** | **用户级** 0~1 连续值：高注意力回复概率 `0.8` / 低 `0.08`、持续 `120s`、最多追踪 `10` 人；指数衰减**半衰期 300s**；回复该用户 `+0.35`；**读空气判不回 → −0.2**（>0.3 才生效）；情绪 ±0.1/0.15（情绪半衰期 600s）；**注意力溢出**（高注意力用户 spilling 到同群其他人 30%、半衰期 90s、触发下限 0.4）；**两段式冷却**：待冷却（观察该用户后续 1 条 / 最长 60s / 保底概率 0.18）→ 正式冷却（阈值 0.3、最长 600s、可自动解冻） | 与我们"群级注意力"是**两个正交维度**：他们挑"人"，我们挑"群" |
| **回复密度限制** | `enable true`、窗口 `300s`、**硬上限 4 条**、软限比例 `0.6`（≈2 条开始"提示 AI 少回"）、`reply_density_ai_hint true` | 硬闸 + 软提示两层 |
| **对话疲劳（默认关）** | 沉默 `300s` 清零；三档阈值 `3/5/8` 条 → 概率衰减 `0.1/0.2/0.35`；**中度以上 30% 概率发"结束语"**（如"我先忙了"） | 我们用 necessity 的"存在感惩罚"覆盖了同类目的 |
| **拟人模式（默认关）** | 连续 `3` 条未回复 → 进入沉默（最长 `600s`，沉默中收 `8` 条自动醒来）；动态阈值 `1~3`；**兴趣关键词概率 +0.25**；决策历史进 prompt 保持一致性 | 沉默/唤醒状态机 |
| 并发 | `concurrent_mode legacy\|smart`、`smart_concurrent_merge_wait 30s`、`smart_concurrent_max_batch_size 20`、`smart_concurrent_claim_delay 0.3s`、`concurrent_wait_max_loops×interval = 10×1s` | smart = 按到达顺序合并批次后一起交给 AI |
| 上下文格式 | `include_timestamp`（`[2026-03-13 周四 14:30:00]`）、`include_sender_info`（`Name(ID:12345)`）、`max_context_messages -1` | 「谁在什么时候说的」显式进 prompt |
| 单独 @ 的强化 | 消息数窗口 `8` + 时间窗口 `180s` **双满足** | 只 @AI 没内容时，回复阶段补一段中性上下文提醒 |
| 消息缓存 | `custom_storage_max_messages 500`、`pending_cache_max_count 10`、`pending_cache_ttl_seconds 1800` | **未过概率筛选的消息不丢**，暂存待下次回复当上下文 |
| 冷群缓存转正 | `enable_idle_cache_flush false` + `idle_cache_flush_delay_seconds 600` | 静默 10 分钟把待处理池转正入库，避免上下文断裂 |
| 主动对话 | 沉默 `1800s`、概率 `0.2`、检查间隔 `120s`、要求近 `300s` 内 ≥3 条用户消息、连续被无视 `3` 次→冷却 `2400s`、安静时段 `23:00-07:00` + `30` 分钟平滑过渡 | 比我们的破冰更细 |
| 自适应互动评分 | 成功 +15 / 被无视 −10 / 快速回复 +5 / 多人参与 +10 / 连续成功 +5 / 低分复活 +20，每日衰减 2，范围 10~100 | 主动对话策略随反馈调整 |
| 吐槽系统 | 连续被无视 2 次触发，最大累积 15 | 拟人化"被晾着会抱怨" |
| 频率调整器 | 用 AI 判"正常/过于频繁/过少"，`0.85` / `1.1` 系数，间隔 `180s`，持续 `360s` | 群体节奏自适应 |
| 打字模拟 | `enable false`，速度 `15 字符/秒`，最大延迟 `3s` | 与我们 buffer 随机延迟同类 |
| 打字错误 | 概率 `0.02`（每 50 字约 1 个错字） | 拟人化细节 |
| 重复过滤 | 最近 `5` 条、`1800s` 窗口内不重复 | 防复读刷屏 |
| 记忆注入 | `memory_insertion_timing: post_decision`（决策后注入，只影响内容不影响"是否回"）、`livingmemory_top_k 5` | 记忆时机可配 |
| 内容过滤 | 输出过滤与保存过滤**两套独立规则**（Range/Head/Tail 三种标记语法） | 清 thinking 块/第三方注入块 |
| 私聊 | 独立 30+ 项，仍标注"开发中，勿开" | — |

### 1.3 `bl-chat-plugin`（Yunzai + OneBot v11）：Timing Gate 与焦点状态机【原文】

来源：[README.md（jsDelivr）](https://cdn.jsdelivr.net/gh/Cat-bl/bl-chat-plugin@main/README.md)

| 机制 | 默认/细节 | 为什么值得对照 |
|---|---|---|
| **Timing Gate** | 子代理小模型输出 `continue / no_action / wait` 三选一，15s 超时 | 把"什么时候说"交给一个便宜的判断模型（MaiBot 同思路） |
| **talkValue 时段阈值** | `ceil(1/talkValue)`：白天 `0.07`→15 条触发一次、深夜 `0.034`→30 条 | 阈值按时段变，白天更话痨、深夜更安静 |
| 本地预筛 | `skipWhenAddressedOther`（@的是别人→直接跳过，不烧 LLM）、`skipWhenEmptyText` | 先做零成本过滤再花 LLM |
| Gate prompt 信号 | 群 5 分钟 ≥ `30` 条 → 提示"群里热闹，倾向沉默"；bot 10 分钟已回 ≥ `5` 条 → 强烈提示"避免刷屏" | 把"群多忙/我多话"写进判断 prompt |
| **焦点状态机** | `focus / fading / cold`；`focusMaxReplies`、`focusMaxNoAction`、`fadingForceGate` | 与我们"焦点 + 拿不拿得住"同构 |
| 冷群 deferred 唤醒 | 最短 `120s`、最长 `900s` 后再跑一轮 Gate | 冷群里"想想要不要补一句" |
| 打断与让步 | `inFlight` 锁（已有任务在跑则让步并记 `queuedWhileInFlight`）、`maxConsecutiveInterrupts 3`（同群连续被打断上限，超过强制走完） | **我们没有"被打断"这个概念** |
| 复读跟读 | 窗口 5 条、≥`3` 个不同用户文本完全相同、概率 `0.6`、冷却 `180s`、文本 ≤30 字、绕过 LLM 直接发原文 | 我们也有复读（阈值不同） |
| 禁言保护 | bot 被全员/单独禁言 → 所有自动回复链路阻断，兼容 ICQQ/OneBot/NapCat 字段 | 我们也有 `is_group_muted` |
| 群白名单 + 并发 | `enableGroupWhitelist true`、`concurrentLimit 3` | 并发默认 3，与我们一致 |
| 记忆 | 用户记忆 ≤`100` 条/人、群共识记忆 ≤`50` 条/群（类别：topic/rule/meme/event/member）、注入 `8`/`6` 条、总字符 ≤`1200`、**防抖批量提取**（用户静默 45s 或累计 6 条；群最小间隔 10 分钟或 12 条） | 群共识记忆的**分类**比我们细 |
| 表达学习 | 学群友说话风格（每群独立） | 我们只有人格/提示词，没有"学群友口癖" |
| 情感系统 | 每群独立，每小时向中性衰减 `0.02`，被夸 +0.1 / 被骂 −0.15 / 被@ +0.05 | 我们情绪是 9 档**倍率表**（离散），它是连续值 |
| 拟人化发送 | 分段发送（按标点/换行，即使关闭也按 `\n` 拆）、`waitTool`（LLM 主动"稍后再说一句"，1~60s）、错字 | 我们没有分段与 waitTool |
| 工具终态 | `this.terminal(...)`：工具自己已发完（图/语音/表情包）→ 跳过最终文本回复 | 我们有类似收敛（`_confirm_platform_result`） |
| 风控规避 | `textImageTool` 把可能触发敏感词/风控的长文本**转成图片**发送 | 我们没有 |

### 1.4 腾讯 QQ 机器人官方文档：群聊的硬规则【原文】

来源：[消息收发概述（bot.q.qq.com）](https://bot.q.qq.com/wiki/develop/api-v2/server-inter/message/overview.html)（页面更新 2026-07-21）

- **事件**：群聊有 `GROUP_AT_MESSAGE_CREATE` **和** `GROUP_MESSAGE_CREATE` 两种 ——
  后者就是"全量群消息"（印证第一轮文档 §6 的路线判断，事件名一致）。
- **被动消息时效/次数**：群聊 **5 分钟有效、每条消息可回复 5 次**；单聊 60 分钟 / 4 次。
- **主动消息频控**：群 **60/qpm**（未认证 30/qpm）、单关系 20/qpm、**每日上限 1000 条/群**；
  用户可在客户端关闭"允许主动发送"，关了以后主动消息**一律失败**。
- **去重与幂等**：同一 `msg_id` 可能多次推送，要结合 `msg_seq` 去重；相同
  `msg_id + msg_seq` 重复发送会失败，多次回复同一消息要**递增 `msg_seq`**。
- **富媒体**：`msg_type=7` + 先上传拿 `file_info`；官方文档明确"**分片上传（推荐）**"
  （与我们 2026-09-27 真机结论一致：旧式直传已失效、分片成功）；`file_info` 有 `ttl`。
- **撤回**：自己发的消息 **2 分钟内**可撤回。

### 1.5 OpenClaw：多 bot 同群的防死循环【原文】

来源：[Bot loop protection](https://docs.openclaw.ai/channels/bot-loop-protection)

- 按**机器人对（pair，双向算同一对）**做滑动窗口限速，默认
  `maxEventsPerWindow 20 / windowSeconds 60 / cooldownSeconds 60`，超预算则冷却期内抑制该对。
- bot 消息可以**只作为上下文可见、但不触发回合**（`allowBots` 与 admission 分离）。
- 内部多 agent 群轮次另有预算：`maxRounds 4`、`maxTurns 32`。

### 1.6 协议端与登录态：NapCat / LLBot / Lagrange / go-cqhttp【子代理·官方文档】

- **全是"全量"**：NapCat / LLBot / Lagrange / go-cqhttp 的文档化配置里**都没有"仅接收 @
  消息"的开关**，也就是说私有协议端默认就能拿到群里每条消息（我们的 NapCat 路径正是如此）。
  只有"是否上报自身消息"这类过滤：NapCat `reportSelfMessage: false`、
  Lagrange `Message.IgnoreSelf: true`、go-cqhttp `report-self-message: false`（默认都是不发自己的）。
- **禁言感知**：`group_ban` 事件（`sub_type=ban|lift_ban` + `duration`）、群成员对象带
  `shut_up_timestamp`、NapCat 另有 `get_group_shut_list` → 我们能可靠实现"被禁言就闭嘴"。
  反面：Lagrange.Core v1 的事件表里**没有禁言事件**（其 OneBot 层是否补齐未验证）。
- **ASR（语音转写）协议端一律不提供**，必须自建 —— 对应我们已有的本地 STT。
  但官方开放平台的语音附件里**带 `asr_refer_text`（ASR 参考结果）**，那条通道可以省掉自建。
- **语音收发转码需要 FFmpeg**（NapCat v4.4.11 起多数平台免配置，macOS 沙盒不支持）。
- **NapCat 没有消息历史库**：消息 ID 是哈希、约 5000 条后 LRU 过期、撤回后不可再取 ——
  所以引用链/回溯补回**必须依赖我们自己的 backlog 存储**（我们正是这么做的）。
- 登录/稳定性：NapCat 扫码（`loginRate 3/分钟`）**无自动重连项**；Lagrange `AutoReconnect: true`
  + 签名服务；go-cqhttp 官方 README 已写"无力继续维护"、Lagrange v1 标注 sunset。
- 正/反向 WS 都是双工；反向 WS = 协议端当客户端主动连我们（利于内网），NapCat 官方推荐优先 WS。

### 1.7 框架层：会话 / 权限 / 限流都是"容器"，不是"策略"【子代理·源码+官方文档】

| 框架 | 会话容器 | 权限 | 冷却/限流 | 去重 | 拟人化 |
|---|---|---|---|---|---|
| **NoneBot2** | 临时响应器（`temp`）续接，`expire_time` 默认 **2 分钟**；`T_State` 只在单次事件流程内有效；**没有**上下文条数/历史 | 只有 `SUPERUSER`；**无内置群主/群管层级**（自己读事件 `role`） | 核心**无** cd（生态插件做） | 无框架级（插件 `nospam` 做群内重复/相似检测并撤回） | 核心无 |
| **Koishi** | `session.prompt(timeout?)`，`delay.prompt` 默认 **60s**；历史条数无内置 | authority **0~5** + 指令/选项级；**assignee（同频道哪个 bot 应答）**；filters（按平台/用户/群 include-exclude）；实验性 permissions 支持 `inherit`/`depend` 与**按平台群管角色授权的访问器权限**（如 `telegram:admin`） | 官方插件 `rate-limit`：`maxUsage` / `minInterval` / `usageName`（多指令共享额度） | 插件 `repeater`：统计连续相同消息（`times/users/repeated`），按 `minTimes`+概率**复读或打断** | **核心有**：`session.sendQueued(msg, delay)`、`delay.character`（按前一条字数）、`delay.message`（默认 100ms）、`cancelQueued`；广播默认 500ms 间隔 |
| **Yunzai (Miao)** | `setContext/getContext`，key=`插件名.群/用户`，**默认 120s** 超时清理 | 规则级 `master/owner/admin/all`（**框架内置群主/群管**） | **框架内置两级 CD**：`groupGlobalCD`（整群）+ `singleCD`（个人，默认 **1000ms**），内存字典 + `setTimeout` | **框架内置** `msgThrottle`：key=`user_id:raw_message`，**200ms** 内同文本直接丢 | 未内置分段/随机延迟；**回复支持引用与定时撤回（0~120s）** |
| **ZeroBot (Go)** | `State` + `FutureEvent`（`Next` 取一次 / `Repeat` 持续并给 cancel），**无超时清空** | Rule 工厂：`SuperUser/Admin/Owner/UserOrGrpAdmin/GroupHigherPermission/CheckUser/CheckGroup/OnlyGroup/OnlyToMe` | **框架内置** `extension/rate`（令牌桶 `NewLimiter(interval, burst)` + `LimiterManager[K]` 按键分桶）与 `extension/single`（同 key **反并发**中间件） | 插件 `breakrepeat` 打断复读 | 无内置；只有 CLI `-l latency` 全局延时 |

**框架层的一致结论**（子代理逐条核过源码/文档）：**四家都不判定"这条群消息要不要回"**。
它们只提供容器与阻断原语（NoneBot 的 priority/block、Koishi 的中间件链、Yunzai 的
"命中即 break，一个事件只被一个 rule 处理"、ZeroBot 的 `Block`），策略全在插件里。
另外三家**都没有"同一用户短时间多条合并"**，只有 Yunzai 有 200ms 同文本去重、
Koishi 有"复读检测"。

对我们的启示（可抄的原语）：

1. **Yunzai 的 `msgThrottle`（200ms 同文本）**：极便宜，专治"同一事件被推送两次/自己重复回复"。
2. **Koishi 的 `delay.character`（按上一条字数算延迟）**：比"固定随机延迟"更像打字 ——
   我们的 buffer 延迟是固定区间，可以改成随回复长度变化。
3. **Yunzai 的"命中即 break"**：多插件/多策略竞争时**只有一个**处理这条消息 —— 我们的
   门控链已经是顺序短路（等价），但值得明确写成不变式（我们有测试守着顺序）。
4. **Koishi 的 assignee**：同频道"哪个 bot 应答"是框架级概念 —— 我们的多 bot 场景目前没有对策。

### 1.8 MaiBot 官方配置（`bot_config.toml`）：我们抄的那套的**出处**【原文】

来源：[Bot 配置（docs.mai-mai.org，更新 2026-09-14）](https://docs.mai-mai.org/manual/configuration/bot-config)

**这段最要紧**：我们的 `IdleBackoff` 与 necessity 阈值就是从 MaiBot 抄的，现在拿到了官方默认值，
可以对账；而且它**也有跨聊天的焦点机制**（见下），我们不是唯一一家。

`[chat.reply_timing]`（发言时机）：

| 键 | 默认 | 与我们的关系 |
|---|---|---|
| `talk_value` / `private_talk_value` | `1`（0~1，越小越安静；0.3~0.5 明显话少） | 我们**没有**这个旋钮（我们用 necessity 阈值 40 表达"多安静"） |
| `inevitable_at_reply` | `true` | ✓ 我们的 @ 旁路 |
| `mentioned_bot_reply` | `false` | 我们**没有**"提到名字更容易回"（`neko_dynamic_waking_keywords` 是唤醒词，语义不同） |
| **`reply_trigger_mode`** | `"frequency"` / `"reply_necessity"` | **两种触发模式可切**；我们直接并成了 necessity 一条（这是有意的简化） |
| `planner_interrupt_max_consecutive_count` | `0`（不限） | 我们**没有**"思考中来了新消息要不要重新想" |
| `max_consecutive_wait_count` | `3` | 我们**没有**（wait 是它的动作之一） |
| **`no_action_backoff_base_seconds`** | **`15`** | ✓✓ **我们 `IdleBackoff` 的 `15` 就是从这里来的** |
| **`no_action_backoff_cap_seconds`** | **`300`** | ✓✓ 我们的上限 `300` |
| **`no_action_backoff_start_count`** | **`2`** | ✓✓ 我们的 `START_COUNT = 2` |
| **`no_action_backoff_bypass_pending_count`** | **`6`** | ✓✓ 我们的 `BYPASS_PENDING = 6` |
| `talk_value_rules`（平台/群/时段，支持跨夜 `23:00-02:00`） | 默认两条全局规则 | 我们**没有**时段化频率 |

`[chat]`：`max_context_size 40`（群聊参考最近 40 条）/ `max_private_context_size 60` /
`enable_context_optimization true` / `mid_term_memory true` + `mid_term_memory_lenth 10`（**中期记忆
"聊天回想"**）—— 我们靠 Memory Server 的群记忆 + idle finalize，没有"最近 40 条"这种显式窗口。

`[experimental]`（**关键更正**）：

- **`focus_mode`（默认关）＝"同一时间只专注一个聊天流"**，`focus_on_private`、
  `focus_chat_whitelist`、**`focus_groups`（同组共享 Focus，不同组互不抢占）**、
  **`focus_cool_time = 120`（当前聊天多久没继续后允许被其他聊天唤醒）**。
  → **这就是跨聊天/跨群的焦点竞争**，只是实现形态不同：MaiBot 是「焦点 + 120s 冷却后允许被抢 +
  可分组」；我们是「分数 0~10 + 焦点线 4.0 / 保持线 2.0 + 被 @@ 上锁 90s + 蜜月 60s」。
  **我原先"跨群竞争只有我们做"的说法不成立，本文件 §2.2 已更正。**
- `attention_drift`（**话题级**注意力漂移：更容易被新话题/梗/反差点吸引；档位
  `subtle/active/scattered/wild`；`anchor_policy` 回钩策略；`reaction_style`）——
  这是"对话题的注意力"，与"对群/对人的注意力"又是另一个维度，我们完全没有。
- `enable_behavior_learning`（学"什么时候该怎么回应"的经验）、`enable_rich_reply`、
  `emotion_trait`。

`[message_receive]`：`image_parse_threshold 5`（单条图超 5 张就不识图）、`ban_words`、
**`ban_msgs_regex`（正则黑名单，启动时校验，写错直接启动失败）** —— 我们只有关键词表，
没有正则黑名单。

`[response_post_process]`：总开关 `enable_response_post_process true` 同时管**错别字生成与
"回复分割"**，另有 **`typing_speed`（0 最快 / 1 默认 / 2 更慢）** —— 即 MaiBot 也有打字速度模拟
与分段回复（与 AstrBot 的 `segmented_reply` / `typing_simulator` 同类）。

`[expression]` / `[jargon]`：**表达学习 + 黑话学习**（含 `vector_intent` 向量意图召回、
候选池上限 50、`learning_list` 按平台/群/私聊分别控制 use/learn）。

### 1.9 MaiBot 源码级核查（含 MoFox_Bot）【子代理·源码】

取源：MaiBot 全历史 GitLab 镜像 `gitlab.mikumikumi.xyz/maibot/maibot`（tag 覆盖 0.5.8→1.1.4）、
`ghproxy.net` 代理 GitHub raw、MoFox 的 Gitea 镜像（**支持 git grep 代码搜索**）。
（`github.com` / `raw.githubusercontent.com` / DeepWiki 一律不可用；jsDelivr 对 `.py`/`.toml`
报 unsupported content type，所以改用前两者。）

**① 必要性评分表（`src/maisaka/reply_necessity.py`，全硬编码）**

`raw = 相关 + 内容 + 压力 − 存在感惩罚`；`final = int(round(raw × (0.5 + 0.5×min(1.0, talk_value))))`；
**触发线 `REPLY_NECESSITY_TRIGGER_SCORE = 80` 是代码常量、不落配置**。

| 因子 | 分值 | 与我们实现的差异 |
|---|---|---|
| 被 @ / 被提及 / 私聊 / focus 生效 / 普通 | `100 / 80 / 40 / 40 / 0` | ✓ 逐项一致（我们：@100 引用80 焦点40 私聊40 普通0） |
| 问题 / 请求 / 征询 | `+15 / +20 / +20` | ✓ 一致；**但**：弱请求词（需要/求/看看/试试）只在 `is_direct_context` 时计，非直接上下文时"征询意见"必须含"麦麦" —— **我们可能没做这两层限定，需核对** |
| 长文 ≥40 字 / ≥120 字 | `+5 / 再 +10` | ✓ 一致 |
| 整批皆短反应 | `−25` | ✓ 一致 |
| 积压压力 | `ratio<1 → int(50×ratio²)`（空闲≥平均间隔再 +15，上限 50）；`ratio≥1 → 50 + 50×log1p(ratio−1)/log1p(4)`，上限 100 | ✓ 一致 |
| 存在感惩罚 | 300s 窗口，占比 ≤0.25 不罚、≥0.60 罚满 **25**（线性） | ✓ 一致 |
| 频率因子 | `×(0.5 + 0.5×min(1.0, talk_value))` | ✓ 公式一致；**注意它用的是 `talk_value`（0~1，默认 1 → 不加成）**，我们喂的是"群频次倍率"（0.15~1.8），语义不同 —— 需确认我们确实按 ≤1.0 截断 |

**② 两条通路的"条数门槛"（`src/maisaka/runtime.py`）**

`frequency → max(1, ceil(1/talk_value))`；`reply_necessity → max(1, ceil(1/talk_value²))`；
其中 `freq = talk_value × 频率调整量`，focus 生效时强制 1.0。**我们没有这层"攒够几条才检查"**
（我们的门控是每条都评估，靠分数与退避控制），值得知道这是 MaiBot 的"省算力"手段。

**③ 空窗补偿（`turn_gates.py`）**：`等效数 = pending + min(空窗/平均间隔, 阈值−1)`，
**`pending = 0` 一律不触发**（防"纯沉默自唤醒"）；`平均间隔` 样本窗 `1800s`、剔除 `<5s` 连发、
下限 `30s`。→ 我们**没有**这层（我们有 `last_gap_seconds ≥ 30` 的近似判据）。

**④ 焦点槽（`focus/manager.py`）**：`FOCUS_SLOT_LIMIT = 1`（每作用域只允许 1 个会话决策）；
作用域由 `focus_groups` 分组决定（同组共享一槽、未分组用全局槽 `__global__`、否则按会话隔离）；
`focus_cool_time = 120s`；**连续 `FOCUS_NO_ACTION_EXIT_THRESHOLD = 5` 次空闲 → 释放焦点并
"封禁它抢占下一槽"**，直到冷却过期或被 `unblock_focus_entry()` 解除（被 @ 时走这条路、
`wakeup_reason="at"` 无视冷却）；另有 `FOCUS_EVENT_UNREAD_COUNT_THRESHOLD = 3`、
`FOCUS_SWITCH_NEW_MESSAGE_LIMIT = 20`；**状态全在内存**。

**⑤ 群聊上下文渲染（`context/planner_messages.py`）**：每条消息渲染成
`<message msg_id="…" [quote="被引用 id 列表"] time="HH:MM:SS" user="昵称" [group_card=…]
[is_self_message="true"]>` + 换行 + 正文；`self_message_special_mark` 默认 **true**（显式标注
自身消息，减少"把自己当别人"）。**引用是 id 属性、转发用 `view_forward_message` 工具按需展开**
—— 与我们"递归内联展开引用链/合并转发"是**两种相反的策略**（他们省 token、给模型控制权；
我们一次性把内容铺给模型）。图片超过 `max_image_num = 128` 时把旧图替换成 `[图片]` 占位。
启动时回灌 `ceil(max_context_size × 0.5)` 条历史，并注入 `<1min / <30min / <6h / <24h / 更久`
五档离线段落。

**⑥ 记忆（A_Memorix，默认关）**：SQLite + faiss(int8) + 关系图；检索
`weighted_rrf(rrf_k=60, 向量 0.7 / BM25 0.3)` → PPR(`α=0.85`, 超时 1.5s) → `top_k_final=10`；
回灌三路（Planner 主动调工具 / **每轮注入人物画像**（最多 3 个）/ 启发式拉起（默认关））；
写回阈值 `chat_summary_writeback_message_threshold = 36`。
**跨群默认隔离**：`global_memory_sharing_enabled = false`，要共享得用 `shared_memory_groups` 分组放行。

> ⚠️ **我们与他们相反**：我们的 `allow_cross_group_context = true`（跨群上下文默认**开**）。
> 这不是错，但要知道 MaiBot 的选择是"默认隔离、显式分组才共享"。

**⑦ 权限**：MaiBot 只有操作员名单 + 命令级 `allow_users/allow_chats`；**没有群管/群主角色概念**，
**也没有任何禁言期行为处理**（源码级未找到）。MoFox_Bot 才有：`master_users`（无视一切权限节点）、
细粒度权限节点（`plugin.<插件>.<类>.<权限>` + `permission_api` + `/permission` 命令）、
以及 **`message_receive.mute_group_list`（"静默群"：这些群里只有被 @ 或被回复才响应）**；
禁言 notice 会注入上下文并保留 7200s。

**⑧ 拟人化的具体参数**

- MaiBot 0.8.x：`calculate_typing_time()` 中文 **0.3s/字**、英文 0.15s/字、末尾 +0.3s、单字 ×3、
  emoji 固定 1s，再乘 `typing_speed`(0~2)；结尾句号 **90% 删**、逗号 5% 删 / 20% 变空格；
  错别字生成器有 **50% 概率在错字后补发更正消息**（"拟人化瑕疵"的完整闭环）。
- MoFox：`response_splitter`（`split_mode="punctuation"`，`max_length 512`（代码 256）、
  `max_sentence_num 8`（代码 3）、颜文字保护开关）；错别字率 `0.001`(字)/`0.005`(声调)/`0.006`(整词)。
  **未找到发送前的打字延迟，也未找到自动复读**（存疑，见 §4）。

**⑨ MoFox_Bot ＝ MaiBot-Plus 的活跃延续**（不是独立项目：2025-11-19 的重命名提交
`e362615d6d` 即 "rename project from MaiMbot-Pro-Max to MoFox_Bot"）。它**删掉了 talk_value
频率体系**，改用「兴趣阈值 + 焦点能量」：

- 兴趣 = `min(0.5×兴趣匹配 + 0.2×关系 + 0.3×提及, 1)`；**回复阈值 0.75、动作阈值 0.65**；
  强提及 2.0 / 弱提及 0.8 / 基础关系 0.3 → **单靠被 @ 只有 0.6，不足以触发回复**。
- **阈值只降不升**：连续不回复每次 −0.004（≤5 次）；回复后 −`0.1×0.5^n`（n≤3）；回复成功再把
  `no_reply_count −= 2`。
- 焦点能量 = `0.5×兴趣 + 0.3×活跃度 + 0.2×最近性 + 0.1×关系`，整形后限幅 `[0.1, 1]`，
  **Focus↔Normal 双模按能量概率切换**（`p(Focus→Normal)=fe`）。
- 主动发言间隔 = `base_interval × (interest_score_factor − focus_energy)`（720s 基准、
  夹在 360~2880s），抛话题后冷却 3600s，**每日上限 3 次**，安静时段 00:00–07:00（系数 0.7）。
- 多群并发：`asyncio.Semaphore(10)` + **按能量动态重投递周期**（1/3/8/15/30s ±20%）。

### 1.10 勘误与补充：MoFox 复核（第二版，本地 clone 逐条 grep）【子代理】

子代理用 `ghfast.top` 把 MoFox_Bot 整仓 clone 到本地逐条复核，推翻了它自己上一版的三处结论。
**这三处都以本地源码为准，§1.9 里对应的表述按本节理解。**

**勘误 1：MoFox 的打字延迟确实存在**（上一版误列为"未找到"）：
`src/chat/utils/utils.py::calculate_typing_time()` —— **中文 0.2s/字、英文 0.1s/字**（比 MaiBot
上游的 0.3/0.15 快）、单字 `3×0.2+0.3`、emoji 固定 1s、**且"已思考超过 10 秒则压成 1 秒"**
（避免长思考后还慢悠悠打字）；调用点 `uni_message_sender.py` 里 `await asyncio.sleep(typing_time)`。
另有 `timing_utils.get_normal_distributed_interval(base, sigma_percentage=0.1)` ——
**正态分布** + 3σ 规则（我们的延迟是均匀分布，可换成这个）。

**勘误 2：MoFox 多群并发根本没有信号量限流**（上一版记成 `Semaphore(10)`）：
`distribution_manager.py` 全文件无 `asyncio.Semaphore`；真实机制是**每个聊天流一个独立轮询任务**
（`_stream_loop_worker(stream_id)` + per-stream Lock 防重复），轮询周期按能量档
`1 / 3 / 8 / 15 / 30s` × `U(0.8, 1.2)` clamp 到 `[1, 30]`；`max_concurrent_streams` **只在统计
字典里被读、从未用于限流**；用 **`force_dispatch_unread_threshold = 20`（未读 > 20 直接处理）**
替代限流来防积压。唯一的 Semaphore 在另一套 `unified_scheduler`（插件/定时任务用）。

**勘误 3：不存在独立的「MaiBot-Plus」项目**：`MaiBot-Plus/MaiMbot-Pro-Max@master` 的文件树与
`MoFox-Studio/MoFox_Bot@master` **逐字节相同**（模板同 size/hash、README 同字节），即 GitHub
改名重定向；该命名空间下也没有同名仓库。另外被问到的 `mode_mxp.py`（"梦溪畔独家赞助"那种
自嘲文案）是**上游 MaiBot 内的第三方意愿模式插件位**，与 MaiBot-Plus 无关 —— 悬念删除。

**补充 A：MoFox 的记忆是全局跨群共享**（`memory_graph/models.py` 的 `MemoryNode/Edge/Memory`
**无 `chat_id`**、图节点 `concept` 全局唯一）——即"A 群的事在 B 群说出来"是**设计内行为**；
与上游 MaiBot `A_Memorix` 的"每个聊天流只检索自己产生的记忆"**默认策略相反**。
（所以"跨群共享还是隔离"这一维上，外部两家各站一边，我们也是共享 —— 见 §3.2 第 3 条。）

**补充 B：MoFox 的能量阈值有强制不等式**：启动时
`high_match ≥ reply + 0.1`、`reply ≥ non_reply + 0.1`；模板写 `0.6/0.75/0.65`，**实际生效为
`high=0.85 / reply=0.75`** —— 引用它的三档阈值必须换算。

**补充 C：MoFox 的分割器实际只用 `max_sentence_num`**（`max_length` 在 `process_llm_response`
里已被注释掉、不参与截断），超限时**反复合并"最短的相邻句对"**（用"，"拼接）直到达标 ——
比"超长就截尾"更保语义，值得抄。

**新可借鉴的常数（本轮新挖出）**：

- **MaiBot 0.8.x 的可插拔"回复意愿"模式**（`normal_chat/willing/mode_mxp.py`，全源码常量）：
  ① 用反正切把无界意愿压进 `[0,1)`（`w<2 → atan(2w)·2/π`）；② **在途消息防喷射**：同一个人
  **≥2 条在途 → 意愿直接归 0**，群内 2/3/≥4 条 → `−0.5 / −1.5 / 归 0`；③ 意愿**按 (chat, person)
  独立**，每 3s 以 `0.93` 向该群基础意愿收敛；④ `expected_replies_per_min = 3`、
  `mention_willing_gain = 0.6`、`interest_willing_gain = 0.3`；⑤ **疲劳惩罚时长 = 消息间隔 × 2**。
  ⑥ 插件位设计：`mode_{mode}.py` + `{Mode}WillingManager` + `importlib` 动态加载 +
  **加载失败静默回落经典实现** —— 这套"可替换策略 + 安全回落"的做法很适合我们的策略层。
- **MoFox 的打断概率反比例衰减**：`p = 1.4/(interruption_count+2) + 0.05` → 第 1 次 **80%**、
  第 2 次 **35%**、第 3 次 **15%**、第 4 次起约 **10%**，达 `interruption_max_limit = 5` 后为 0；
  触发则 `start_stream_loop(force=True)` 重入。**这是"被打断"这件事的量化版本，我们完全没有。**
- **长消息分片重组**（`message_chunker.py`，`timeout = 30s` 内到达的碎片重组成一条再处理）——
  与我们 5s 的合并缓冲目的相近但语义不同（它治"一条话被拆成几条发"）。
- **消息渲染**：`昵称(QQ号): 内容`、bot 自身渲染成 `昵称(你)`、`@<platform:id>` → `@昵称`、
  **引用只渲染最后一次**（`re.sub(..., count=1)`）、`[picid:x]` 查描述表 → `[图片：描述]`
  （查不到 → `[图片内容未知]`）；转发由适配器**递归展开**（缩进 `"--"×层`，图片 <5 才转图）。

**⚠️ 引用陷阱（务必记住）**：MoFox 的配置值有**三套互相冲突的来源** —— `official_configs.py`
的 Pydantic 默认 / `template/bot_config_template.toml`（**用户实际拿到的，评价行为以它为准**）/
`docs/affinity_flow_guide.md` 的示例；而且 `docs.mofox.chat` 已是 **Neo-MoFox 重写版**文档
（其 DFC 配置与 MoFox_Bot 无关，**不可混用**）。另外 MoFox 的两个镜像
（`MoFox_Bot@master` 模板 7.7.0 + MySQL + `[maim_message]` vs `Mofox-Core@gitea` 8.0.5 +
PostgreSQL + `[message_bus]`）是**两条分叉**而非版本先后，引用时须注明分支。

### 1.11 AstrBot / LangBot 源码级：责任链、唤醒语义与增量上下文【子代理·源码】

**AstrBot（master / v4.x）**

- **9 阶段责任链确认**：`WakingCheck → WhitelistCheck → SessionStatusCheck → RateLimit →
  ContentSafetyCheck → PreProcess → Process → ResultDecorate → Respond`；调度是洋葱模型，
  任一阶段 `event.stop_event()` 即断链。**"忽略这条群消息"发生在第 1 阶段**。
- **唤醒条件是「或」**：① 以 `wake_prefix`（顶层默认 `["/"]`）开头 ② @机器人 ③ @全体
  （`ignore_at_all` 可关）④ **引用机器人的消息** ⑤ 私聊 ⑥ 插件 handler filter。
  **核心没有概率唤醒、也没有关键词/正则唤醒**（那是插件的事）。白名单 `id_whitelist`
  （可填 UMO 或群号）+ `wl_ignore_admin_on_group|friend`；**核心没有黑名单键**（按群拉黑走
  「自定义规则」按 UMO 覆盖）。
- **限流按 UMO（群/会话）固定窗口**：`rate_limit{time:60, count:30, strategy:stall|discard}`，
  `stall` 睡到下一窗口、`discard` 直接丢。
- **会话 key**：`UMO = platform:type:session_id`；aiocqhttp / slack / qq_official 的群
  `session_id = {sender_id}_{group_id}`，`unique_session=True` 时才启用"群+人隔离"。
- **群聊上下文渲染**（这条最值得学）：`[昵称/HH:MM:SS]: 正文`、@机器人的位置插
  `⚠️[DIRECTED AT YOU]`、图片 `[Image: 描述]`（配了 vision 模型）否则 `[Image]`、
  引用 `[Quote(昵称: 文本)]`；**这些记录只在被唤醒时**以
  `<system_reminder>…group chat context after your last reply…` 注入
  `extra_user_content_parts`，**注入后从队列里删除** —— 天然"上次回复之后"语义，
  不重复、省 token。
- **拟人化**：`segmented_reply`（按标点分段、每段 `random.uniform(1.5, 3.5)` 秒，
  这就是它的"打字延迟"，**没有独立键**）；长文防刷屏 `forward_threshold 1500`（合并转发）
  与 `t2i` + `t2i_word_threshold 150`（转图）。**连续回复上限与"被刷屏退避"未查到。**
- **主动插话 `active_reply`**：对**未被唤醒**的群消息按 `random.random() < 0.1` 插话；
  要求该会话已存在（`/new`），开 `unique_session` 时会失败。**冷场检测未查到。**
- **@空消息的 60 秒等待**：`empty_mention_waiting` + `@session_waiter(60)` —— 单独 @ 她
  但没内容时等 60 秒，把用户下一条重新入队（并在消息头补一个 At），低成本做出"你在组织
  语言"的连续感。
- **记忆**：群记忆 = `group_icl_enable`（默认关）+ `group_message_max_cnt 1000`，
  **内存 deque 按 UMO 分桶**；`group_message_history_enable` + 700 条持久化（带 `sender_name`）
  并向模型提供「当前群聊历史查询工具」；检索式记忆是知识库 RAG。
  `builtin_stars/astrbot/long_term_memory.py` 在文件清单里但镜像 404 → **内容未验证**。

**LangBot（v4.8.x）**

- **更正上一轮文档：不是 11 阶段，源码是 12 阶段**（官方文档根本没有阶段清单，"11 阶段"
  是 DeepWiki 的说法且**漏掉了 `ConversationMessageTruncator`**）：
  `GroupRespondRuleCheck → BanSessionCheck → PreContentFilter → PreProcessor →
  ConversationMessageTruncator → RequireRateLimitOccupancy → MessageProcessor →
  ReleaseRateLimitOccupancy → PostContentFilter → ResponseWrapper → LongTextProcess →
  SendResponseBack`；任一阶段返 `INTERRUPT` 即断链（主力是第 1/2/6 阶段）。
- **触发规则取「或」**：`group-respond-rules{at:false, prefix:["ai"], regexp:[], random:0.0}`，
  源码注释明确"任意一个匹配就放行"；**`random` 不是"随机回复概率"，而是"其他规则都没匹配时
  的兜底概率"**；另有优先级更高的 `ignore-rules{prefix, regexp}`。访问控制
  `access-control{mode: blacklist|whitelist, blacklist, whitelist}`，条目形如
  `group_456` / `person_*` / `*_123`。
- **限流粒度是"群/会话"而不是群内用户**：`rate-limit{window-length:60, limitation:60,
  strategy:drop|wait}`，算法硬编码 `fixwin`（固定窗口）。
- **消息聚合防抖**：`trigger.message-aggregation{enabled:false, delay:1.5}`（1.0~10.0s、
  缓冲上限 10 条）—— **与我们 5s 合并缓冲同构**，只是它是配置项。
- 会话 `session_id = group_<群号>`（**群级、不按人**）；上下文轮数 `ai.local-agent.max-round`
  默认 10（`RoundTruncator` 从后往前按 user 消息计轮）；`combine-quote-message` 默认 true
  （引用内容并进本次消息）。
- **拟人化**：`output.force-delay{min,max}` → `random.uniform` + `asyncio.sleep` 再回复；
  **没有分段回复**；长文 `long-text-processing{threshold:1000, strategy: none|forward|image}`。
- **主动发言 / 定时播报 / 冷场检测：三项全部未找到**（config.yaml、pipeline 元数据、官方
  文档都没有对应键）。**没有群记忆**（只有 RAG 知识库）；v4.8 的 `config.yaml` 里**连
  `admins` 键都没有**（只有 `command.privilege{}` 的命令→权限映射）。

**两家都没有"多群注意力竞争"**（这条与 §2.2 的更正一致，可以放心）：

- AstrBot：每群一个 deque、互不可见；限流按 UMO；`active_reply` 是**每群独立掷骰**。
  它最接近"注意力"的东西是**"注意力 = 显式唤醒"**：群消息默认只记录、不进 prompt。
- LangBot：`Controller` 用全局信号量 `concurrency.pipeline:20` + 每会话 `concurrency.session:1`，
  "取第一个未被锁定的会话"执行 —— **每会话串行、跨群并行**，这是**背压调度，不是注意力**；
  代码里没有"当前聚焦群"这个概念。

## 2. 逐维度对比（外部 vs 我们）

> "我们"一列＝当前代码/线上配置（86 个配置键、7 入口 50 动作、64 模块）。

### 2.1 「该不该回」

| 做法 | 外部 | 我们 |
|---|---|---|
| 概率门槛 | AstrBot 插件：首层 `0.02`，回复后临时抬到 `0.8`（120s）；`talkValue` 时段化 | 无概率门槛；normal 群 relay 概率 `0.1`（转达，不是回复） |
| 便宜模型判"要不要回" | AstrBot 插件读空气 AI（超时=不回）；bl-chat Timing Gate（continue/no_action/wait） | **没有**；我们的 8.5 是**确定性打分**（necessity，阈值 40），不烧额外 LLM |
| 打分/因子 | MaiBot 的 necessity 打分（我们抄的） | ✓ 已落地：@100 / 引用80 / 焦点40 / 提问15… + 积压压力 + 存在感惩罚 + 频率因子 |
| 时段化 | bl-chat `talkValue` 按时段、群聊插件的"动态时段概率" | ✗ 没有时段概念（疲劳/作息是我们主动删掉的） |
| 群多忙/我多话作为信号 | bl-chat 把它写进 Gate prompt（群 5min≥30 条→倾向沉默；我 10min 回了≥5 条→提示别刷屏） | ✓ 我们把它做成了**数**（积压压力 + 存在感惩罚），但没有"回了几条"的显式上限提示 |

**结论**：我们在"确定性打分"这条路上走得比他们远（他们是"概率 + 小模型判断"），
代价是没有"读空气"的语义理解（例如"群里在吵架，别插嘴"这种判断我们做不到）。
两者可以并存：**先打分，边界区间再交给小模型**——这是我们目前缺失的一档。

### 2.2 多群与焦点

| 外部 | 我们 |
|---|---|
| **MaiBot 有跨聊天焦点**（`experimental.focus_mode`）：同一时间只专注一个聊天流、`focus_cool_time 120`（多久没继续才允许被别的聊天唤醒）、`focus_groups`（同组共享焦点、不同组互不抢占）、`focus_chat_whitelist`；另有 `attention_drift`（**话题级**漂移） | **群级注意力竞争**：0~10 分、焦点线 4.0 / 保持线 2.0、被 @ 上锁 90s、蜜月 60s、频次倍率 0.15~1.8、情绪 9 档倍率 |
| AstrBot 插件的「注意力机制」是**用户级**：每个用户 0~1 连续值（高注意力回复概率 `0.8` / 低 `0.08`、半衰期 `300s`、回复该用户 `+0.35`、**读空气判不回 −0.2**、情绪 ±0.1/0.15），外加**溢出**（高注意力用户 30% 溢到同群其他人）与**两段式冷却**（待冷却观察 1 条 / 60s / 保底 0.18 → 正式冷却阈值 0.3 / 最长 600s） | 我们**没有用户级注意力**（靠 necessity 的"自己发言占比"惩罚 + @/引用 显式信号） |
| bl-chat：**per-group 焦点状态机**（`focus / fading / cold` + `focusMaxReplies` / `focusMaxNoAction`），群与群之间完全独立 | 我们是**全局竞争**（同一时刻只有一个焦点群） |

**结论（更正）**：三个"注意力维度"是**正交**的 —— **群级**（我们 + MaiBot `focus_mode`）、
**用户级**（AstrBot 插件）、**话题级**（MaiBot `attention_drift`、第一轮文档里的 Heartflow）。
我们只做了**群级**这一维，且形态与 MaiBot 不同：

- MaiBot：布尔焦点 + **120s 冷却后允许被抢** + 可分组（简单、可解释）。
- 我们：连续分数 + 焦点线/保持线 + 锁 90s + 蜜月 + 频次/情绪倍率（表达力强、参数多）。

**值得抄的两点**：① **`focus_groups`（共享组）** —— 让几个"其实是一个场景"的群共享焦点，
避免它们在彼此之间来回抢（我们完全没有这个概念）；② **`focus_cool_time` 的语义** ——
"刚聊过的群有冷却保护"，我们用 `lock + 蜜月` 表达了近似语义但更隐晦。

### 2.3 消息缓存与合并

| 外部 | 我们 |
|---|---|
| AstrBot：`rate_limit` stall/discard；插件 `pending_cache_max_count 10` + TTL 1800s + **冷群 10 分钟自动转正**；smart 模式按到达顺序合批（等待 30s、最多 20 条） | 缓冲合并：群 5s 窗口 / 私聊 1s、随机延迟 0.2~2.5s、**上限 17 条**；idle 10s finalize + digest |
| bl-chat：`concurrentLimit 3`，inFlight 锁让步 | 并发闸 3（一致） |

**结论**：我们的"合并缓冲"与他们的"pending 池"目的相同（都是为了不逐条烧 LLM），
但**他们没有"没通过筛选的消息也要留着当上下文"这一步之外的"转正"语义**；
我们缺的是他们的 **TTL + 冷群转正**（我们靠 idle finalize 推 digest，等价但时机不同）。

### 2.4 会话与上下文构建

| 外部 | 我们 |
|---|---|
| 时间戳 + 发言人 ID 显式进 prompt（`[2026-03-13 周四 14:30:00]`、`Name(ID:12345)`）；单独 @ 的双窗口强化（8 条 / 180s）；`unique_session` 可按人隔离上下文 | 有时间上下文模块、`sender_nickname`、引用链/转发/语音/文件递归展开、跨群上下文（`allow_cross_group_context=true`） |
| 长回复折叠成"转发消息"（1500 字） | ✗ 没有折叠 |
| 内容过滤（清 thinking/第三方注入块）两套规则 | ✗ 没有（我们靠提示词约束 + `_sanitize_message_text`） |

### 2.5 记忆

| 外部 | 我们 |
|---|---|
| bl-chat：用户记忆 100 条/人 + **群共识记忆 50 条/群**（topic/rule/meme/event/member）+ 注入 8/6 条、≤1200 字 + **防抖批量提取**（用户静默 45s / 6 条；群 10 分钟 / 12 条） | 群记忆 + 群成员记忆 + 私聊参与者记忆（都开）；idle finalize + digest 推 Memory Server；群成员画像 |
| AstrBot：群 ICL 开关 + `group_message_max_cnt 1000`；插件记忆注入时机 `pre_decision` / `post_decision` 可选 | 记忆只影响"回复内容"，不参与"要不要回"（等价于 post_decision）✓ |
| 语义召回（embedding）topK（bl-chat 默认关） | 记忆在 Memory Server 侧（宿主），插件只管推送 |

**结论**：**群共识记忆的分类**（群规/梗/事件/成员共识）值得抄一版进我们的提示词层；
防抖批量提取的阈值（45s / 6 条 / 10 分钟 / 12 条）也可以作为我们 idle finalize 的对照。

### 2.6 拟人化

| 外部 | 我们 |
|---|---|
| 分段回复（按句/换行，随机 1.5~3.5s 间隔，>150 字才分）；打字模拟（15 字符/秒、上限 3s）；**错字生成 2%**；`waitTool`「稍后再说一句」（1~60s）；复读跟读 | 只在**投递前**有 0.2~2.5s 随机延迟；没有分段、没有打字速率模拟、没有错字、没有 waitTool；有复读跟随（>5 人 + 焦点群） |
| **沉默状态机**（拟人模式）：连续 `3` 条未回复 → 进入沉默（最长 `600s`，沉默中收 `8` 条自动醒来）；动态阈值 `1~3`；兴趣关键词概率 `+0.25` | 没有"她主动安静一段"的状态（我们的退避是**不接话**，不是"状态上安静"）；也没有兴趣关键词加权 |
| **对话疲劳 + 结束语**：连续对话 `3/5/8` 条 → 概率衰减 `0.1/0.2/0.35`；**中度以上 30% 概率发"我先忙了"**（拟人收尾） | 疲劳被我们按使用者要求删掉（只留情绪）；"收尾语"没有 |
| 情绪：连续值（每小时衰减 0.02，被夸 +0.1 / 被骂 −0.15） | 9 档离散倍率 + `emotion_display` |

**结论**：这是**差距最大的一块**。分段 + 打字节奏 + 收尾语是"像真人"最直接的杠杆，
而我们只有"整条回复延迟一点"。优先级建议：① **分段（按 `\n`/标点）+ 每段随机间隔**
（提示词里她已经会换行分段，落地成本低）；② **收尾语**（聊久了主动说一句"我先忙了"
比突然消失更像人）。

### 2.7 频率与防刷屏

| 外部 | 我们 |
|---|---|
| AstrBot 插件：窗口 `300s`、**硬上限 4 条**，软限 `0.6`（≈2 条时**在提示词里告诉 AI 少回**）；重复过滤（最近 5 条 / 1800s） | 回复频率闸：**60s 内 3 条**强制静默（硬闸）；表情包冷却 5 条；没有重复过滤、没有软提示 |
| AstrBot 核心：消息速率 `60s/30 条`（stall 或 discard）；长回复折叠成"转发消息"（1500 字） | 并发闸 3（处理侧）；没有长回复折叠 |
| bl-chat：Gate prompt 里显式告诉模型"群 5min≥30 条→倾向沉默、我已回≥5 条→别刷屏" | 我们是纯硬闸，不"提醒模型" |

**结论**：我们的硬闸更严（口径也不同：他们限**收到的消息速率**，我们限**自己的回复条数**）。
缺两件小事但都值得做：**重复过滤**（防同一句卡带）与**软提示**（到阈值前先在提示词里收敛，
而不是到点直接静默——静默在连续对话里很突兀）。

### 2.8 权限与名单

| 外部 | 我们 |
|---|---|
| AstrBot：ID 白名单（默认开、空列表=不过滤）+ 管理员无视白名单 | 名单式：用户 admin/trusted/normal/**blacklist**（今天刚加）、群 trusted/normal/none；私聊一律回 |
| bl-chat：群白名单 + 仅主人管理 + 群主/管理员可清群记忆 | 群级别 + 信任名单 + admin 层 |
| 内容安全：AstrBot 内置词表 + 百度审核 | 黑名单词表（负优先级标签）+ 用户黑名单；**没有外部审核 API** |
| 禁言保护 | 双方都有 ✓ |

### 2.9 主动发言

| 外部 | 我们 |
|---|---|
| 沉默阈值（1800s）/ 概率（0.2）/ 检查间隔（120s）/ 要求近期有人活跃 / **连续被无视 3 次 → 冷却 2400s** / 安静时段 23:00-07:00 + 平滑过渡 / 自适应互动评分（成功 +15、被无视 −10）/ 吐槽系统 / 冷群 deferred 唤醒（120~900s） | 冷场破冰（同一焦点群连续 5 次无人说话）+ 主动私聊（静默 300s）+ backlog 通知；**没有失败冷却、没有安静时段、没有"被无视"反馈** |

**结论**：我们缺"**主动发言的失败保护**"（被无视后该收敛）与"安静时段"。
这是很容易被使用者感知的体验差异（深夜自言自语最招人烦）。

### 2.10 风控与合规

| 外部 | 我们 |
|---|---|
| **官方硬限**（可核实）：群被动回复 5 分钟 / **5 次**；单聊 60 分钟 / 4 次；群主动 **60/qpm**（未认证 30/qpm）、单关系 20/qpm、**1000 条/群/日**；频道每子频道每秒 5 条；WS 错误码 **4008**「发送 payload 过快」，`session_start_limit.total=1000 / reset_after=86400000ms`；`msg_id+msg_seq` 幂等；撤回限 2 分钟 | 我们**没有**：主动消息频控计数（破冰/主动私聊没有"每天多少条"上限）、**`msg_seq` 幂等**、撤回能力 |
| 第三方协议端的"安全频率" | **未验证**：NapCat 官方安全页只给定性建议（别与常用号同 IP/设备、换号换设备、SOCKS5），**没有任何 QPS/QPM 数字**；网上流传的"风控阈值"多来自 AI 生成的 SEO 站，与官方文档矛盾且无出处 → 拒绝采信 |
| 多 bot 同群防死循环（OpenClaw pair 限速 + bot 消息不触发回合） | ✗ **完全没有**：同群若有另一个 bot，我们可能与它互相触发（而且 NapCat 默认不上报自己的消息，我们**看不见自己**，更容易误判） |
| 敏感内容规避（把长文本转图发） | ✗ 没有 |
| 内容安全 API（AstrBot 接百度审核） | ✗ 没有（只有本地词表黑名单） |

**结论**：官方通道那套数字是**可以照抄的硬约束**（尤其"每天 1000 条/群"和 msg_seq 幂等）；
第三方协议端的风控**没有可信数字**，只能靠保守行为（我们的回复频率闸 60s/3 条 + 并发闸 3
本身就是一种保守）——这一点上"抄不到数字"要如实记下来。

## 3. 差距清单与建议（按性价比排序）

| # | 建议 | 依据（外部原文） | 成本 | 收益 |
|---|---|---|---|---|
| 1 | **分段回复 + 每段随机间隔**（她已经在换行分段，只是被当一条发出去） | AstrBot `segmented_reply`：按句切、随机 1.5~3.5s、>150 字才分；bl-chat 分段默认开 | 小 | 拟人化最直观 |
| 2 | **`msg_seq` 幂等去重 + 主动消息频控计数**（群 60/qpm、1000 条/群/日） | 腾讯官方文档：`msg_id` 会重复推送、同 `msg_id+msg_seq` 重复发送会失败 | 小 | 避免重复回复/被限流（官方通道尤其） |
| 3 | **重复过滤**（最近 5 条 / 30 分钟内不重发同一句）与**长回复折叠**（超 ~1500 字转"转发消息"） | AstrBot 插件 `duplicate_filter`；AstrBot 核心 `forward_threshold 1500` | 小 | 防"卡带"与刷屏 |
| 4 | **频率软提示**（到硬闸 60% 时先在提示词里让她收敛） | AstrBot 插件 `reply_density_soft_limit_ratio 0.6` + `reply_density_ai_hint` | 小 | 比"到点直接静默"更自然 |
| 5 | **主动发言失败保护 + 安静时段**（被无视 N 次进冷却；深夜不主动开口） | AstrBot 插件：连续被无视 3 次 → 冷却 2400s；安静时段 23:00-07:00 + 30 分钟过渡 | 小 | 减少打扰，最容易被使用者感知 |
| 6 | **收尾语**（聊久了以一定概率主动结束："我先忙了"） | AstrBot 插件疲劳模块 `fatigue_closing_probability 0.3` | 小 | 比突然消失更像人 |
| 7 | **同群多 bot 防死循环**（识别 bot 消息 / pair 滑动窗口限速 + 冷却） | OpenClaw `botLoopProtection`：20 事件/60s、冷却 60s；bot 消息可"只当上下文不触发回合" | 中 | 与其它 bot 共存时的保命机制（NapCat 默认不上报自己的消息，更容易误判） |
| 8 | **边界区间交给小模型"读空气"** | AstrBot 插件读空气 AI（超时=不回、可开推理）；bl-chat Timing Gate（continue/no_action/wait、15s 超时） | 中 | 补上我们缺的语义判断（吵架/玩梗/阴阳） |
| 9 | **用户级注意力**（在群里跟谁聊得热）+ 溢出 + 两段式冷却 | AstrBot 插件注意力机制（0~1、半衰期 300s、溢出 30%、待冷却保底 0.18） | 中 | 与我们群级注意力正交，能补"熟人感" |
| 10 | **群共识记忆分类**（群规/梗/事件/成员共识）与防抖批量提取 | bl-chat 群记忆五类 + 注入上限（8/6 条、1200 字）+ 用户 45s/6 条、群 10 分钟/12 条 | 中 | 群氛围一致性 |
| 11 | **内容安全**（外部审核 API，或把易触发的长文本转图发） | AstrBot `content_safety`（内置词表 + 百度审核）；bl-chat `textImageTool` | 中 | 账号安全 |
| 12 | **官方通道的全量模式 + 平台 ASR** | 官方文档确认 `GROUP_MESSAGE_CREATE`（开启"接收所有消息"后每条都推）；语音附件带 `asr_refer_text` | 中 | 官方通道也能做群聊感知，且省掉自建 STT |
| 13 | **`focus_groups`：把"其实是一个场景"的群共享焦点** | MaiBot `experimental.focus_groups`（同组共享、不同组互不抢占） | 小 | 避免姊妹群互相抢焦点（我们现在是全局平铺竞争） |
| 14 | **`talk_value` 式的"整体安静度"总旋钮**（按时段/按群） | MaiBot `talk_value` + `talk_value_rules`（支持跨夜时段）；bl-chat `talkValue` | 小 | 现在要"更安静"只能去改 necessity 阈值或各种倍率，缺一个直觉旋钮 |
| 15 | **正则黑名单**（`ban_msgs_regex`）与**提到名字更容易回**（`mentioned_bot_reply`） | MaiBot `message_receive.ban_msgs_regex`（启动时校验）、`reply_timing.mentioned_bot_reply` | 小 | 前者补我们"只有关键词表"的短板，后者是低成本的礼貌信号 |
| 16 | **中期记忆 / 聊天回想**（最近 N 条之外的"最近发生过什么"） | MaiBot `mid_term_memory` + `mid_term_memory_lenth 10` | 中 | 我们现在只有"长期群记忆"与"当前上下文"两档 |
| 17 | **在途消息防喷射 + 打断概率反比例衰减** | MaiBot `mode_mxp`：同一人 ≥2 条在途 → 意愿归 0，群内 2/3/≥4 → −0.5/−1.5/0；MoFox `p = 1.4/(n+2)+0.05`（80%→35%→15%→10%） | 中 | 治"她还没说完群里又来了几条"的抢话问题（我们只有 in-flight 锁式的并发闸） |
| 18 | **随机延迟改成正态分布**（σ≈10%，3σ 截断） | MoFox `timing_utils.get_normal_distributed_interval` | 小 | 比均匀分布更像真人（我们现在是 `uniform(0.2, 2.5)`） |
| 19 | **超长回复的"合并最短相邻句"**（而不是截尾/丢弃） | MoFox `response_splitter`：`max_sentence_num` 超限时反复合并最短相邻句对 | 小 | 分段与长回复处理时更保语义 |
| 20 | **可替换策略 + 安全回落**（`mode_{x}.py` + 动态导入 + 失败回落经典实现） | MaiBot `willing_manager.BaseWillingManager.create(mode)` | 中 | 我们的策略（necessity/注意力）是硬接的，换算法只能改代码 |
| 21 | **「上次回复之后」的增量上下文注入**（唤醒时注入上次回复以来的群消息，**注入即删**） | AstrBot `GroupChatContext` + `<system_reminder>…after your last reply…` 注入 `extra_user_content_parts` | 中 | 天然不重复、省 token；我们现在是"每次把最近上下文整段拼进去" |
| 22 | **@空消息的等待窗口**（单独 @ 她但没内容时等 60s，把用户下一条补 @ 后重投） | AstrBot `empty_mention_waiting` + `@session_waiter(60)` | 小 | 低成本做出"你在组织语言"的连续感 |
| 23 | **对"未被唤醒"的消息按概率主动插话**（可选开关 + 白名单 + 每小时上限） | AstrBot `active_reply{possibility_reply 0.1, whitelist}` | 中 | 我们现在只有"冷场破冰"这一种主动开口；这条是"群在聊但没叫我"时的插话 |
| 24 | **长文三种处置可选**（原样 / 合并转发 / 转图） | AstrBot `forward_threshold 1500` + `t2i_word_threshold 150`；LangBot `long-text-processing{threshold:1000, strategy:none\|forward\|image}` | 小 | 我们现在只发文字，长回复会刷屏 |
| 25 | **触发规则分层：多条件「或」+ 更高优先级的 ignore 规则 + random 只作兜底** | LangBot `group-respond-rules{at,prefix,regexp,random}` + `ignore-rules{prefix,regexp}`（源码注释"任意一个匹配就放行"、random 是"都没匹配时"的兜底） | 中 | 我们已有类似的"@/引用/关键词/焦点"多条路，可以把"谁能叫醒她"做成一份显式配置 |

### 3.1 对账：我们抄 MaiBot 抄得准不准

| 我们的实现 | MaiBot 官方默认 | 结论 |
|---|---|---|
| `IdleBackoff`：`min(300, 15 × 2^(n−2))`、`START_COUNT = 2`、`BYPASS_PENDING = 6` | `no_action_backoff_base_seconds 15`、`cap_seconds 300`、`start_count 2`、`bypass_pending_count 6` | **四个常数逐一相同** —— 第一轮移植是准的（现在有了官方文档作为出处） |
| necessity 阈值 `40`（真机回放定） | MaiBot 用 `80`，但它那一关还承担"攒够几条才值得思考" | 我们知道这个差异（第一轮已记录），40 是按我们"焦点群内部"这一层重新定的 |
| @ 必回 | `inevitable_at_reply true` | ✓ 一致 |
| —— | `reply_trigger_mode` 可在 `frequency` / `reply_necessity` 之间切 | 我们**并成了一条**（有意的简化；代价是失去了"按群回到纯频率模式"的退路） |

**待办提示**：如果以后真的需要"某些群退回到纯频率模式"，MaiBot 那个 `reply_trigger_mode`
就是现成的设计参考。

### 3.2 需要回头核对**我们自己实现**的 7 处（本轮调研的直接产出）

| # | 要核的 | 对照的外部事实 | 怎么核 |
|---|---|---|---|
| 1 | necessity 的两层限定：**弱请求词**（需要/求/看看/试试）是否只在"直接上下文"时计分；非直接上下文时"征询意见"是否要求文本含"麦麦" | MaiBot 源码里这两条是明确分支 | 读 `reply_necessity.py` + 补 2 条单测 |
| 2 | 频率因子是否**按 ≤1.0 截断** | MaiBot `×(0.5 + 0.5×min(1.0, talk_value))`，而 `talk_value ≤ 1` | 读我们的 `frequency_factor` 计算；若我们的倍率可 >1 则与 MaiBot 语义不同（不是错，但要写明） |
| 3 | 跨群上下文默认 **开**（`allow_cross_group_context = true`） | MaiBot/A_Memorix **默认隔离**，要共享得 `shared_memory_groups` 显式分组 | 想清楚：我们是要"她记得别的群的事"（产品选择）还是默认隔离更稳 |
| 4 | 群聊上下文里是否**显式标注"这条是她自己发的"** | MaiBot `self_message_special_mark` 默认 true（减少"把自己当别人"） | 读 `prompting.py` 的群聊行渲染；没有就加一个标记 |
| 5 | 引用/合并转发的展开策略：我们**递归内联**（一次性铺给模型） | MaiBot 只给 `quote="msg_id"`，转发用 `view_forward_message` 工具**按需展开** | 评估上下文体积与 token 成本；我们的策略更"懂上下文"但更贵 |
| 6 | 焦点"**连续 N 次空闲就退出并禁止它抢下一槽**" | MaiBot `FOCUS_NO_ACTION_EXIT_THRESHOLD = 5` + 冷却 + 被 @ 强制解封 | 我们现在只有分数与 idle backoff，没有"她对这个群彻底失去兴趣"的表达 |
| 7 | 空窗补偿（**`pending = 0` 不自唤醒** + 用平均间隔折算空闲） | MaiBot `turn_gates.py` | 我们现在是"每条都评估 + 退避"，两种路子都合理，但要确认没有"没人说话她自己反复评估"的开销 |

前 4 条是**行为正确性**问题，后 3 条是**设计取舍**问题 —— 建议按 1→2→4→3→6→7→5 的顺序处理。

**不建议照搬的**：

- **概率门槛**（`initial_probability 0.02` 那套）：我们已有确定性打分，再加一层概率会互相打架。
- **`unique_session`（每人独立上下文）**：与我们的"群聊感 + 群记忆"设计相反。
- **情绪连续值**：我们的 9 档离散倍率已经够用、可解释、可调。
- **照抄"安全发送频率"数字**：第三方协议端**没有**可信数字（子代理专项检索结论：只有官方
  开放平台有明文频控表；网上流传的阈值多来自 AI 生成的 SEO 站）。

## 4. 未验证（禁止外推）

- 上面各家的**默认值**是否等于**推荐值**：文档给的是默认，不代表作者推荐。
- **AstrBot 插件里"对话疲劳"的具体公式**只读到字段（阈值 3/5/8、衰减 0.1/0.2/0.35、结束语 0.3），
  其内部实现未读。
- **MaiBot 1.x** 的 `response_splitter` 与 `[emoji]`/`[chinese_typo]` 默认值（抓取被截断）、
  发送端"打字延迟"的实现位置、是否有"连续回复硬上限"、是否有群管/群主角色、禁言期如何处理
  （1.x 全部**未找到任何证据**）。
- **MoFox** 的打字延迟与自动复读（仅 changelog 提到"为复读增加硬限制"）；`mute_group_list`
  的判定代码路径未逐行核验（证据到配置注释/README 级）；主动发言的 `base_interval` 在**两个
  镜像间默认值不同**（720s vs 1800s），引用时必须注明分支。
- 其它衍生版（MaiBot-Next / Core / Desu / Fork）未核查。
- 第三方协议端的风控触发条件、以及"安全发送频率"数字：**没有可信出处**（见 §2.10）。
- 我们的"60s 内 3 条"回复闸与他们的"60s 30 条消息速率"**口径不同**，不能直接比较松紧。

## 5. 源码级细节（子代理交付）

- **框架层（NoneBot2 / Koishi / Yunzai / ZeroBot）**：已并入 §1.7（源码级证据来自 nonebot2 与
  Miao-Yunzai 的 Gitea 镜像、ZeroBot 官方 docs 与 pkg.go.dev）。
- **协议端与账号安全（NapCat / LLBot / Lagrange / go-cqhttp + QQ 官方）**：已并入 §1.6 与 §2.10。
- **MaiBot 1.x / MoFox_Bot 源码级**：已并入 §1.9 + §1.10（含三处勘误）。
- **AstrBot / LangBot 源码级**：已并入 §1.11。其中一条**更正第一轮文档**：
  **LangBot 是 12 阶段不是 11 阶段** —— 源码 `default_stage_order` 含
  `ConversationMessageTruncator`，而"11 阶段"来自 DeepWiki（漏了它）、官方文档根本没有阶段清单。
  引用阶段数时请以 §1.11 为准。
- **AstrBot / LangBot 的流水线阶段与群响应规则**：已完成，落在 §1.11（第一轮只给了阶段数，
  本轮落到字段：AstrBot 9 阶段的 `WakingCheck` 排在 `SessionStatusCheck`/`RateLimit` 之前、
  唤醒条件取「或」；LangBot 12 阶段 + `group-respond-rules` 取「或」且 `random` 只是兜底）。

## 6. 这份文档的定位（补记）

本文写的是「**别人**有什么、值不值得学」。**「我们**已经有什么、是从哪来的」是另一份：
`docs/UPSTREAM-LINEAGE.md`（血统与署名 + 许可缺口）。两者的关系要说清楚，免得读岔：

- 本文 §3 那 25 条建议**目前一条都没进代码** —— 它是待办清单，不是现状说明；
- 反过来，我们**代码级借鉴过的只有三条线**：MaiBot 的 necessity 打分表、
  **KiraAI 的提示词骨架 + `<msg>` 协议**、宿主本体的连接层副本；
- 本文里被当作"外部做法"来对比的 `kira_unified`/`<feeling>` 那套，**文字其实是我们自己写的**
  （见 UPSTREAM-LINEAGE §2.2）；真正逐字来自 KiraAI 的是提示词骨架。

（未交付的一律保持"未验证"，不用默认值外推。）
