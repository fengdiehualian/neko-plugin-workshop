# OpenBiliClaw 个性化推荐兼容层

[![Verify N.E.K.O Plugin](https://github.com/Himifox/n.e.k.o_plugin_proactive_recommender/actions/workflows/verify.yml/badge.svg)](https://github.com/Himifox/n.e.k.o_plugin_proactive_recommender/actions/workflows/verify.yml)
[![Release N.E.K.O Plugin](https://github.com/Himifox/n.e.k.o_plugin_proactive_recommender/actions/workflows/release.yml/badge.svg)](https://github.com/Himifox/n.e.k.o_plugin_proactive_recommender/actions/workflows/release.yml)

这个插件是 OpenBiliClaw 与 NEKO 之间的纯插件推荐调度层，不要求修改 NEKO 本体：

1. 从本机 OpenBiliClaw 完整后端 `127.0.0.1:8420` 读取已经基于跨平台浏览行为生成的最终推荐，不复制 Cookie、页面快照或原始浏览记录。
2. 从 NEKO 现有 `bus.memory` 读取最近一小时的新用户消息，提炼非敏感本地兴趣；也可调用 `web_search:search` 和 `bilibili_danmaku:bili_search` 补充候选。
3. 将所有候选统一进行相关性排序、去重、隐私前台、离开状态、安静时段、每日上限和最短间隔门控。
4. 直接读取主服务 `/api/proactive/mode`，尊重主动搭话总开关和 `off` 模式；不要求启动 `proactive_controller`，读取失败时停止交接。原生视频、新闻等来源开关不控制本插件，用户可以关闭它们避免重复推荐，本插件的来源由 Hosted UI 独立管理。
5. 将最佳候选的标题、主题和推荐理由通过不可见的 `push_message(visibility=[], ai_behavior="respond")` 交给当前 NEKO 主角色模型，由猫娘结合当前对话、记忆和人设决定保持沉默或自然搭话；候选 URL 和封面不进入模型提示词。

Hosted 面板中的“我的画像”直接同步 OpenBiliClaw 的 `GET /api/profile-summary`，展示其性格画像、核心特质、兴趣领域和当前阶段。原先从 NEKO 最近聊天提炼的轻量兴趣仍只作为插件内部的补充排序信号，不再作为面板中的主画像。

插件只把“主消息服务已经接受候选”记录为 `handoff_submitted`，不会把它伪装成猫娘已经说出推荐，也不会把猫娘保持沉默误判为用户忽略。正式反馈仅来自显式的 `recommendation_feedback` 调用。

## 当前内容展示边界

- 当前版本只把个性化内容作为猫娘的主动搭话素材，不在聊天中展示超链接或视频封面。
- 插件仍在内部校验候选的规范内容 URL，用于候选去重和交接记录，但不会把该 URL 交给猫娘复述，也不会生成短链、占位链接或 Markdown 链接。
- 交接提示明确禁止猫娘主动提出“稍后给链接”、询问用户是否需要链接，或声称自己保存、查找、找回了内容。猫娘若决定搭话，必须自然说明“这里只拿到了主题和摘要，没有经过验证的可用链接”，并改为邀请用户聊这个主题；这句可见事实会保留在对话历史中，降低用户后续追问时主模型编造链接的风险。
- 这个限制目前只能通过插件生成的可见上下文约束，不能做成确定性的后续回复拦截：NEKO 的插件消息处理器是观察者，主动交接提示又只对当次推理有效。若宿主未来提供逐轮提示扩展或输出过滤钩子，插件可再升级为硬性阻止后续假链接。
- 暂不借用 `music_pusher` 传递内容卡片：它是音频播放器控制链路，不是通用链接卡片接口。等宿主提供可与猫娘回复可靠绑定的不可变附件能力后，再恢复准确链接和封面展示。

## 浏览器扩展兼容范围

主要推荐链路直接读取完整 OpenBiliClaw 后端的 `GET /api/recommendations`。此外，插件仍在 `127.0.0.1:8421` 提供可选的旧版行为事件兼容入口，支持：

- `GET /api/ping`
- `GET /api/health`
- `GET /api/runtime-status`
- `GET /api/runtime-stream`（WebSocket）
- `POST /api/events`
- 通知、认知更新和 delight 的空队列/确认接口

这不是 OpenBiliClaw 完整后端的复制品。账号抓取、初始化、内容池和浏览器弹窗推荐仍由 OpenBiliClaw 自己负责。

## 隐私边界

- 兼容服务只绑定本机回环地址 `127.0.0.1`。
- B 站和抖音 Cookie 接口会明确拒绝请求；Cookie 不会被读取或保存。
- 不保存完整 URL、DOM 快照、原始页面标题或原始平台事件。
- 长期状态只保存事件指纹、按平台计数和提炼后的兴趣词。
- 不推断健康、政治、宗教、财务等敏感属性。

## 使用

1. 启动完整 OpenBiliClaw 后端，并让浏览器扩展连接其默认地址 `127.0.0.1:8420`。
2. 在 Hosted UI 中启用“OpenBiliClaw 推荐接入”，后端端口保持 `8420`；只有旧扩展需要直接上报行为事件时才使用可选的 `8421` 入口。
3. 先使用影子模式观察兴趣和候选，确认后再切换正式运行。正式运行只表示候选会交给猫娘，猫娘仍可以决定不说。

候选搜索仍依赖已安装的搜索插件；启用 B 站搜索前，需要 `bilibili_danmaku` 插件提供 `bili_search` 能力。

## 安装

从 [GitHub Releases](https://github.com/Himifox/n.e.k.o_plugin_proactive_recommender/releases) 下载 `proactive_recommender.neko-plugin`，然后在 NEKO 插件管理器中导入。

插件需要本机运行完整 OpenBiliClaw 后端；浏览器扩展可通过面板中的安装按钮获取。

## 开发与校验

本仓库遵循 NEKO 独立插件仓库标准，目标安装目录为：

```text
N.E.K.O/plugin/plugins/proactive_recommender
```

在 NEKO 仓库根目录执行：

```bash
uv run --with pip python -m plugin.neko_plugin_cli.cli sync proactive_recommender --clean
uv run python -m plugin.neko_plugin_cli.cli check proactive_recommender
uv run python -m plugin.neko_plugin_cli.cli check -r proactive_recommender
```

插件复用 NEKO 宿主已经提供的 `aiohttp`，不把平台相关的二进制扩展写入 `vendor/`，因此同一个 Release 安装包可用于 Windows、macOS 和 Linux。

## 发布

- `verify.yml` 会在 push、pull request 和手动触发时调用 NEKO 官方插件校验工作流。
- `release.yml` 会在推送与 `plugin.toml` 版本一致的 `v*` 标签时执行市场发布检查，并创建带 `.neko-plugin` 安装包、校验日志和市场证据的 GitHub Release。
- 当前版本为 `0.4.1`，对应发布标签为 `v0.4.1`。
