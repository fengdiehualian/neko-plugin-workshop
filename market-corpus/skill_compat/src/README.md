# Skill 兼容加载器（skill_compat）

让 N.E.K.O 的猫娘（YUI）**通过对话指令自主安装、管理 skill**，并兼容加载来自各平台（ima / clawhub / skillhub / N.E.K.O）的 skill 包。

装上这个插件后，你不用再手动拖文件、改目录——直接对 YUI 说「安装这个 skill」，她就会完成从下载、解压、安全校验到索引的全部流程；也可以让她**直接在 skillhub 技能商店里搜索并安装**现成的技能。

---

## ✨ 功能特性

- **对话即安装**：对 YUI 说「安装/添加 XX skill」，她通过 `install_skill` 工具自动完成安装并刷新索引
- **对话即删除**：对 YUI 说「删除/卸载 XX skill」，她通过 `remove_skill` 工具安全删除（只删受管目录内条目；符号链接条目只删链接不删真实目录）
- **每次安装前需免责声明确认**：**每次**安装 skill 前都自动弹出风险提示，用户回复「我确认」后由 AI 调用 `confirm_consent` 确认**本次**安装，本次安装完成后下次安装前需再次确认（确认仅内存态，不落盘）
- **三种安装来源**：
  - 本地目录（内含 `SKILL.md`，支持 `@owner/slug` 嵌套结构）
  - 本地 `.zip` 压缩包
  - HTTPS 下载链接（`.zip`）
- **skillhub 商店直达**：对 YUI 说「去 skillhub 搜 XX 技能」「把 XX 技能装上」，她通过 `skillhub_search` / `skillhub_install` 调用本机 skillhub CLI 完成搜索与安装（需先装 skillhub CLI）
- **跨平台格式兼容**：自动解析 ima / clawhub / skillhub / N.E.K.O 各来源的格式差异——YAML 头、命名空间嵌套、符号链接、中文目录名、重复目录
- **大文件先询问、不硬拒**：来源超过默认资源上限（200MB / 5000 条目）时转为询问确认，而不是直接禁止
- **索引自愈**：扫描失败、目录变更后自动重建索引，AI 看到的始终是磁盘上的真实状态

---

## 📥 安装方法

在 N.E.K.O 插件市场搜索 **skill兼容** 或直接打开：

```
https://market.project-neko.cn/#/plugin/105
```

点击「安装插件」→ 在 N.E.K.O 客户端确认 → 完成后重启一次对话即可。

> 若要用 skillhub 商店功能，请先按官方指南安装 skillhub CLI：
> `https://skillhub.cn/install/skillhub.md`

### ⚙️ 自动开启（推荐）

插件不会随 N.E.K.O 重启自动加载，需要开启一次。仓库 `scripts/` 提供自动开启脚本，登录 Windows 后自动把插件拉起（幂等，已就绪则跳过）：

```text
scripts\autostart_skill_compat.ps1    # 主脚本：等待 N.E.K.O → POST start → 等待工具就绪（放数据目录）
scripts\autostart_skill_compat.vbs    # 静默启动器（只放启动文件夹，无窗口无弹窗）
```

一键注册（**启动文件夹只放 vbs**，ps1 必须放 `%LOCALAPPDATA%\N.E.K.O\scripts\`，避免 ps1 被系统用编辑器打开弹窗）：

```powershell
New-Item -ItemType Directory -Force "$env:LOCALAPPDATA\N.E.K.O\scripts"
Copy-Item -Force scripts\autostart_skill_compat.ps1 "$env:LOCALAPPDATA\N.E.K.O\scripts\"
Copy-Item -Force scripts\autostart_skill_compat.vbs "$([Environment]::GetFolderPath('Startup'))\"
```

日志写在 `%LOCALAPPDATA%\N.E.K.O\plugins\skill_compat\autostart.log`，退出码 0=成功 / 1=失败。

---

## 🚀 快速上手

安装完成后，直接对 YUI 说：

| 你想做什么 | 对 YUI 说 |
|---|---|
| 查看有哪些 skill | 「列出所有可用的 skill」 |
| 按主题找 skill | 「搜索一下关于 XX 的 skill」 |
| 安装 skill | 「帮我安装这个 skill，链接是 https://…」（每次安装前需回复「我确认」） |
| 删除 skill | 「把 XX skill 删掉」 |
| 安装前确认 | 插件会弹出免责声明，回复「我确认」即可（每次安装前都需确认一次） |
| 从 skillhub 找技能 | 「去 skillhub 搜一下 PDF 相关的技能」 |
| 从 skillhub 装技能 | 「把 skillhub 上的 XX 技能装到本机」 |
| 看某个 skill 的用法 | 「加载 XX skill 的说明」 |
| 排查没被识别的 skill | 「检查一下 skill 加载状态」 |

示例（安装本地目录）：

```
帮我安装这个 skill：C:\Users\me\skills\my-skill
```

示例（通过链接安装）：

```
帮我安装这个 skill：https://example.com/skill.zip
```

示例（skillhub）：

```
去 skillhub 搜一下 PDF 相关的技能，然后把第一个装到本机
```

---

## 🧰 提供给 YUI 的工具

| 工具 | 作用 |
|---|---|
| `install_skill` | 安装/导入 skill（本地目录 / zip / https 链接），装完自动刷新索引；**每次安装前都需先确认免责声明** |
| `confirm_consent` | 确认**本次**安装的免责声明（回复「我确认」后调用；一次确认只放行一次安装） |
| `remove_skill` | 删除已安装的 skill（只删受管目录内条目；符号链接只删链接本身），删完自动刷新索引 |
| `skillhub_search` | 在 skillhub 技能商店按关键词搜索技能（名称/描述/版本/命名空间） |
| `skillhub_install` | 从 skillhub 商店安装技能到本机 skill 目录，装完自动刷新索引；**首次需先确认免责声明** |
| `list_skills` | 列出所有已索引的 skill（名称、描述、路径、类型） |
| `load_skill` | 加载指定 skill 的完整 `SKILL.md` 内容 |
| `search_skills` | 按关键词在 skill 名称与描述中搜索 |
| `skill_status` | 查看运行状态与兼容性报告（扫描目录、已加载数量、格式问题、确认状态） |
| `audit_skill` | **skill 安全初步筛查**：一键给全部 skill 出风险等级（safe/low/medium/high/critical）与重点关注清单，只读不执行、不联网；指定 `name` 可看单个 skill 的详细命中 |

---

## 🔒 安全与可靠性

经过多轮安全加固（v0.1.4+），内置以下防护：

- **SSRF 防护**：下载来源逐跳校验，拒绝内网地址、云元数据地址（169.254.169.254 / 100.100.100.200 等）、IPv4 映射 IPv6 绕过；重定向最多跟随 5 跳且逐跳复查
- **路径穿越拦截**：zip 内绝对路径、`../` 越界条目直接拒绝
- **符号链接拒绝**：zip 内的符号链接条目不释放，防止解压后指向目录外文件
- **zip 炸弹防护**：gzip 解压分块限流；解压后按磁盘实际占用复核（防「声明小、解压大」的变体），超限转询问
- **skillhub 子进程安全**：用独立探测的真实 Python 运行 CLI（打包 exe 不能当解释器）；剥离宿主代理环境变量（避免网络请求静默失败）；找不到 CLI/解释器时给出可操作的下一步提示
- **覆盖不误删**：`overwrite` 交给 skillhub CLI 自己的 `--force` 按实际目录覆盖，插件不猜测、不预删用户目录
- **严格参数解析**：布尔参数严格解析（`"false"` 字符串不会再被当成 `True` 覆盖安装）
- **临时文件清理**：校验或下载失败时自动清理临时目录，不残留垃圾
- **内容即数据**：skill 内容是数据不是命令，插件不会盲目执行其中的指令
- **安装免责声明确认（v0.1.19+）**：**每次安装** skill 前都需用户确认免责声明（回复「我确认」→ 调 `confirm_consent` → 执行安装），一次确认只放行一次安装；安装完成返回 `notice` 明确告知结果，可让用户确认后用 `remove_skill` 删除
- **删除仅限受管目录（v0.1.17+）**：`remove_skill` 只删本插件 skill_dirs 内的条目（越界路径拒绝、Windows 大小写不敏感）；符号链接条目只删链接本身、绝不删链接指向的真实目录；目录已不存在时视为已删并清理索引
- **skill 安全审计（v0.1.14+）**：`audit_skill` 对全部 skill 做初步筛查（危险命令/下载即执行、脚本读取敏感文件、数据外传、提示注入、可疑外链、资源规模），输出风险等级与重点关注清单；只读不执行、不联网，结果供人工确认；指定 `name` 看单 skill 详细命中

---

## 🛠 支持的 skill 来源与结构

- 兼容 ima / clawhub / skillhub / N.E.K.O 各平台发布的 skill 包
- 支持 `@owner/slug` 嵌套目录、中文目录名、YAML 头格式差异
- 安装目标目录由 N.E.K.O 的 `settings.skill_dirs` 配置决定（取第一个可写目录）；建议把第一个目录配成你自己的稳定目录（如 `C:\Users\<你>\.neko-skills`），避免装在会被其他程序清理的目录里

---

## 🆕 更新日志

| 版本 | 要点 |
|---|---|
| **v0.1.21** | 当前正式版。修复 autostart 脚本的**部分注册误判** bug：原脚本用「注册表里出现任意 1 个 skill_compat 工具」判定已就绪并跳过 start，导致 N.E.K.O 启动时若宿主只注册了部分工具（如 6/10），autostart 会误判就绪直接退出，YUI 缺少 `install_skill`/`confirm_consent`/`list_skills`/`load_skill`/`audit_skill`。修复：判定改为必须 10/10 全在；检测到部分注册（1~9 个）时自动 stop+start 触发完整注册；轮询同样要求全量匹配 |
| **v0.1.20** | 修复：源目录已在受管 skill 目录内时安装改为**直接采用原目录**（此前会复制出"清洗后新目录+原目录"两份重复，且删除只清一份）；每次安装前确认机制 + 安装完成 `notice` 告知 |
| v0.1.19 | 免责声明改为**每次安装前都需确认**：一次确认只放行一次安装（确认纯内存态、不落盘、重启后同样需确认）；安装完成返回 `notice` 明确告知用户结果；清理仓库冗余文件（缓存/本地配置） |
| v0.1.18 | 新增 `scripts/autostart_skill_compat.ps1` 自动开启脚本（等待 N.E.K.O → 自动 POST start → 轮询工具就绪，幂等 + 日志 + 退出码，可注册到启动文件夹随登录自动拉起插件）；plugin.toml 补 `[plugin_runtime] auto_start = true`（官方声明模式，供宿主后续版本支持自动加载） |
| v0.1.17 | 新增 `remove_skill` 删除工具（越界路径拒绝、符号链接只删链接本身、目录已不存在时容错清理索引）；免责声明文案强化「请回复我确认」的明确引导 |
| v0.1.16 | 新增**安装免责声明确认**：首次安装 skill 前自动提示风险与免责声明，需用户在对话中回复「我确认」、由 AI 调用 `confirm_consent` 记录一次，之后本机安装不再询问（持久化在插件数据目录，升级不丢失）；修复 `audit_skill` 指定 `name` 详查在真实框架下取不到参数的问题；`load_skill` 读取超大 SKILL.md 改为截断防输出爆炸 |
| v0.1.15 | `audit_skill` 精简为初步筛查：默认只返回每个 skill 的风险等级 + 重点关注清单（不再铺开全部明细），指定 `name` 才看单 skill 详细命中；对常见误报降噪（`subprocess` 单列 medium、凭证词按"文档提及 vs 脚本读取敏感文件"分级） |
| v0.1.14 | 新增 `audit_skill` 安全审计（初版全量明细，输出过大，v0.1.15 已精简） |
| v0.1.11 | 修复：大文件确认后不再截断（zip 损坏导致装不上）、支持 `@owner/slug` 双层嵌套 zip、配置布尔严格解析、拒绝 Windows 保留目录名（CON/NUL 等）、SKILL.md 兼容 UTF-8 BOM |
| v0.1.10 | 修复：skillhub 覆盖不再预删目录（交给 CLI `--force`）、CLI 失败不再被旧目录掩盖、重装同名不再误报失败、namespace 兼容字符串、跳过 WindowsApps 假 python、zip 解压后实际占用复核 |
| v0.1.9 | 新增 `skillhub_search` / `skillhub_install` 工具：调用本机 skillhub CLI 搜索并安装商店技能（独立 python 探测、代理剥离、UTF-8 解码） |
| v0.1.8 | README 改为用户向介绍并同步至 Market |
| v0.1.7 | 安全与可靠性加固全部生效 |
| v0.1.6 | 扫描失败回退未扫描态、失败清理临时目录、gzip 炸弹分块限流、非字符串参数防御 |
| v0.1.5 | 布尔参数严格解析、索引以磁盘为准重建、安装失败不再误报、配置读取失败回退默认值 |
| v0.1.4 | SSRF 全面防护（重定向/IPv6 映射绕过）、zip 符号链接与盘符条目拒绝 |
| v0.1.3 | 惰性扫描修复：索引不再因后台任务被取消而永远为空 |
| v0.1.2 | 超限转询问确认（不硬拒）、URL 预检 |
| v0.1.1 | 首个 `install_skill` 工具 |

---

## 👩‍💻 开发与发布

源码与 Git 仓库：

```text
https://github.com/1-dcjiang/n.e.k.o_plugin_skill_compat
```

发布流程（在 N.E.K.O 仓库根目录）：

```bash
uv run neko-plugin publish skill_compat
```

Market 会独立验证 GitHub Release 的证据链后自动发布。

许可证：Apache 2.0
