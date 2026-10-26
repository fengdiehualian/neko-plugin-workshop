# 一言 · Hitokoto

N.E.K.O 的第三方插件，通过 [N.E.K.O 插件市场](https://market.project-neko.cn/) 分发。
它从 Hitokoto（一言）公开 API `https://v1.hitokoto.cn/` 获取随机一言，并在本机按天缓存一条作为「今日一言」，无需账号或 API Key。

## 安装

1. 在 N.E.K.O 的「插件管理」→「插件市场」中搜索「一言 · Hitokoto」，点击安装。
2. 新安装的插件不会自动运行：在插件列表中找到它，点击「启动」。
3. 想让它随 N.E.K.O 自动运行（「每日首次聊天分享」需要插件常驻），请打开它的「自动启动」开关。手动启动不会顺带开启自动启动。

也可以从本仓库的 [Releases](https://github.com/Alumin-Hydro/n.e.k.o_plugin_hitokoto/releases) 下载 `hitokoto.neko-plugin`，在「插件管理」中导入，之后同样需要手动启动。

## 能力

- **随机一言**：角色可以直接调用对话工具 `hitokoto_random_quote`，比如你说“来一句”“来句诗”“给我一句动漫台词”。可以按官方类型 `a`–`l` 筛选。
- **今日一言**：对话工具 `hitokoto_daily_quote`。「今日一言」是插件在本机按日历日缓存的一条随机一言（使用面板里的默认类型），不是 Hitokoto 官方统一的每日一句，每个人看到的都不一样。开启每日缓存时，当天重复获取返回同一句；修改类型、超时、最大句长或缓存开关，或在面板中清除缓存后，会重新获取。
- **每日首次聊天分享**：每天第一次检测到你发消息时，把今日一言交给当前角色，由角色用自己的口吻自然分享；插件原文不会直接作为聊天气泡显示。每天只尝试一次，获取或推送失败当天也不会重试。
  - 这个功能目前只在 N.E.K.O 以单进程模式运行时生效（Steam 版和桌面版的默认模式）。源码版默认的多进程模式下插件观察不到聊天，不会触发；随机一言和今日一言不受影响。
- **管理面板**：调整默认类型、请求超时、最大句长、每日缓存和每日分享，并可以立即试用、测试 API、恢复默认值或清除每日缓存。

本插件不参与 Agent 的任务分派（`passive = true`），一言只通过上面两个对话工具和管理面板使用，不会被重复触发。

## 类型码

| 代码 | 类型 | 代码 | 类型 |
| --- | --- | --- | --- |
| `a` | 动画 | `g` | 其他 |
| `b` | 漫画 | `h` | 影视 |
| `c` | 游戏 | `i` | 诗词 |
| `d` | 文学 | `j` | 网易云 |
| `e` | 原创 | `k` | 哲学 |
| `f` | 来自网络 | `l` | 抖机灵 |

空类型表示全类型随机。调用 `hitokoto_random_quote` 时省略类型，会使用面板里保存的默认类型。

## 设置

首次运行时，N.E.K.O 会把 `config.example.toml` 复制为插件配置，其中的值就是默认设置。在管理面板里保存的设置会覆盖它们；「恢复默认值」会回到配置文件里的值。

## 数据与隐私

- 插件只向 Hitokoto API 发送固定的编码、字符集、类型和最大句长参数，不上传聊天内容，不需要账号或密钥。
- 为了识别「今天第一次聊天」，在开启每日分享且今天还没有分享时，插件每 2 秒读取一次 N.E.K.O 本机最近的聊天记录（仅在本机进程之间读取）。它只判断有没有新消息，不保存、不记录、不上传聊天内容。今天分享过或关闭每日分享后，就不再读取。
- 日志只记录 quote ID、UUID、类型、长度、缓存命中状态和失败类型，不记录一言正文或你的输入。
- 设置、每日缓存和问候日期保存在 N.E.K.O 的 `PluginStore` 中。存储损坏或不可用时插件会安全降级，同一天也不会重复问候。
- 一言是公开的用户投稿内容。插件把它交给角色时会去掉换行和控制字符，并明确标注为第三方原句，降低内容被当作指令的风险。

## 开发与本地验证

本仓库需要放在 N.E.K.O 源码的 `plugin/plugins/hitokoto` 目录中。目录名必须是插件 ID `hitokoto`，因为入口 `plugin.plugins.hitokoto:HitokotoPlugin` 和测试都按这个包名导入。以下命令在 N.E.K.O 源码根目录运行，需要 [uv](https://docs.astral.sh/uv/)：

```bash
git clone https://github.com/Alumin-Hydro/n.e.k.o_plugin_hitokoto.git plugin/plugins/hitokoto

uv run pytest plugin/plugins/hitokoto/tests -q    # 插件单测
uv run neko-plugin check hitokoto                  # 开发检查
uv run neko-plugin check -r hitokoto               # 与 CI 相同的发布前检查（单测、打包、哈希校验）
uv run neko-plugin build hitokoto                  # 构建 .neko-plugin 安装包
```

## 发布

- 每次 push 和 PR 都会运行 `verify.yml`，调用 N.E.K.O 官方的 `plugin-market-verify.yml` 做标准检查。
- 推送 `v*` 标签会运行 `release.yml`，由官方工作流构建 `.neko-plugin` 并创建 GitHub Release。这一步不会通知插件市场。
- 通知市场：`uv run neko-plugin publish market <Release 页面 URL>`。也可以用 `uv run neko-plugin publish hitokoto` 一步完成打标签、等待 Release 和通知市场。
  - 已知问题：插件嵌在 N.E.K.O 源码树中时，`publish` 的本地 ruff 预检会误报 I001（导入分组），CI 布局下检查是通过的。遇到时可以先推送标签，再用 `publish market` 通知市场。

## 许可证

[MIT](LICENSE)
