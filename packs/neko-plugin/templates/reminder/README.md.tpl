# {{PLUGIN_NAME}}

N.E.K.O. 插件,由 N.E.K.O. 工坊生成(模板:reminder)。到点主动把提醒推送到对话。

## 功能

- `add`:添加一条提醒。参数 `text`(提醒内容)、`after_seconds`(多少秒后提醒,5~86400,默认 60)。
- `list`:查看当前所有提醒,含每条的剩余秒数。
- 定时器每 5 秒扫描到期提醒,到点通过 `push_message` 主动推送(带附加问候语),推送失败会自动重试不丢提醒。

## 配置(plugin.toml `[settings]` 段)

| 字段 | 默认 | 说明 |
|---|---|---|
| `interval_seconds` | 5 | 到期检查间隔(秒);越小提醒越准时 |
| `greet_text` | 空 | 附加问候语,空则不附加 |

## 开发

在 N.E.K.O 仓库根执行(禁止 `uv run`,会触发全量依赖重建):

```bash
python plugin\neko_plugin_cli\cli.py check {{PLUGIN_ID}}
python plugin\neko_plugin_cli\cli.py build {{PLUGIN_ID}} --out {{PLUGIN_ID}}.neko-plugin
```

详情见 `AGENTS.md`;插件开发规范全文在工作区根的 `.opencode/skill/neko-plugin-dev/SKILL.md`(工坊生成项目时自动放置,不随插件打包),也可用 wb_install_skill 安装到任意 Agent 底座。

## 发布上架

插件目录必须是**独立 Git 仓库**,仓库名 `n.e.k.o_plugin_{{PLUGIN_ID}}`,并带标准 `.github/workflows/verify.yml` 与 `release.yml`(由 `python plugin\neko_plugin_cli\cli.py setup-repo {{PLUGIN_ID}} --git --github-actions` 生成)。本地 build 的 `.neko-plugin` 只是安装包,**不能代替 GitHub Release**,也不要用复制目录/安装包导入/符号链接伪装发布流程。完整流程(Market 首次审核、`neko-plugin publish`)见开发规范 `references/cli.md`。
