# 猫娘命令操作器 (shell_cmd)

让猫娘在对话里直接执行 **Shell / CMD / PowerShell** 命令、查看并编辑本地文件的 N.E.K.O 插件。

> 零第三方依赖，仅用 Python 标准库（`subprocess` / `os`），适配 Windows / Linux / macOS。

## 功能入口

| 入口 id | 说明 | 关键参数 |
| --- | --- | --- |
| `run` | 执行一条系统命令 | `command`、`shell`(auto/cmd/bash/powershell)、`cwd`、`timeout` |
| `list_dir` | 列出目录内容 | `path` |
| `read_file` | 读取文本文件 | `path` |
| `write_file` | 写入 / 追加文本文件 | `path`、`content`、`append` |
| `get_status` | 查看当前配置状态 | — |

## 安全围栏

- **超时**：单条命令默认 30s，超时即终止。
- **危险命令黑名单**：命中 `rm -rf /`、`mkfs`、`shutdown`、`:(){`(fork 炸弹)、`dd if=`、管道安装脚本等片段直接拦截。
- **输出截断**：默认最多返回 200 行，防止刷屏。
- `allow_dangerous` 默认 `false`，强烈建议保持关闭。

## 配置

见 `config.example.toml`。把可变默认值放入 `plugin.toml` 的 `[shell_cmd]` 段或独立 `config.toml`。

## 目录结构

```
shell_cmd/
├── plugin.toml              # 根清单（id=shell_cmd, entry=plugins.shell_cmd:ShellCmdPlugin）
├── config.example.toml
├── .github/workflows/      # verify.yml / release.yml
├── .vscode/
├── tests/test_smoke.py
└── plugins/
    ├── __init__.py
    └── shell_cmd/
        ├── __init__.py     # 插件入口类 ShellCmdPlugin
        ├── shell_core.py   # 命令执行核心
        └── i18n/           # 中英文案
```

## 安装

把本仓库作为 N.E.K.O 市场插件安装，或将 `plugins/shell_cmd/` 放入你的 `plugin/plugins/` 目录后重载即可。

## 许可证

MIT
