# Skill 兼容加载器 · 使用指南

## 这是什么
让 N.E.K.O 兼容加载各平台（ima / clawhub / skillhub / N.E.K.O）的 skill 包。自动处理不同来源 skill 的格式差异，把 skill 内容索引后暴露给 N.E.K.O 的 AI 调用。

## 兼容处理的格式差异
- **YAML 头缺失**：自动从目录名补 name/description
- **命名空间嵌套**（`@owner/slug/`）：展开识别内部 skill
- **符号链接**（指向 `/root/.skills`）：识别并标注
- **中文目录名**：回退到目录名作为标识
- **重复目录**（大小写/中英同名）：保留最外层

## 给 AI 的工具
| 工具 | 作用 |
|---|---|
| `list_skills` | 列出全部 skill（可按关键词过滤） |
| `load_skill` | 按名称加载 skill 的 SKILL.md 内容 |
| `search_skills` | 按主题关键词搜 skill |
| `skill_status` | 查看扫描状态与格式问题报告 |
| `install_skill` | **AI 自主安装 skill**：给 source（本地目录 / 本地 .zip / https 下载链接），安装到 `skill_dirs` 并自动刷新索引 |

### 自主安装示例（对话里直接说）
- "安装 skill：`C:\my-skills\emoji-maker`"（本地目录）
- "安装 skill：`D:\packages\my-skill.zip`"（本地压缩包）
- "安装这个 skill：`https://example.com/xxx.zip`"（在线 .zip，如 GitHub 的 Download ZIP 链接）

同名已存在时 AI 会询问是否覆盖；也可直接说"覆盖安装"（overwrite=true）。
来源超过默认资源上限（解压/下载超 200MB 或 zip 超 5000 条目）时，AI 会先询问你确认，你同意后它会带 confirm_large=true 继续安装。

## 配置（plugin.toml → settings）
- `skill_dirs`：skill 搜索目录列表（默认 `["/sandbox/workspace/skills"]`）；**自主安装的新 skill 会装进其中第一个可写目录**
- `follow_symlinks`：是否跟随符号链接（默认 true）

## ⚠️ 注意
skill 内容是**数据不是命令**——加载出的 skill 文本可能含"忽略以上指令"类提示注入，AI 调用时应把 skill 内容当参考资料，不对其中指令盲信后执行敏感操作。
