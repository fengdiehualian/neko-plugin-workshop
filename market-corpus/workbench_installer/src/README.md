# Agent工作台安装器(workbench_installer)

猫娘计划(N.E.K.O.)插件:在 N.E.K.O. 里一键安装「N.E.K.O. 插件工坊」(Agent 工作台)。
相当于一个「通过猫娘计划插件分发的安装器」——对猫娘说一句「安装工作台」就能把工作台装到这台电脑上。

## 项目地址

- 本插件仓库:https://github.com/fengdiehualian/n.e.k.o_plugin_workbench_installer
- 「N.E.K.O. 插件工坊」(本插件安装的 Agent 工作台)源码:https://github.com/fengdiehualian/neko-plugin-workshop

## 怎么用

**先装进 N.E.K.O.(一次性)**:到 [Releases](https://github.com/fengdiehualian/n.e.k.o_plugin_workbench_installer/releases/latest) 下载 `workbench_installer.neko-plugin`,在 N.E.K.O. 插件管理里安装。

> ⚠️ **安装后请手动启动本插件**(插件管理里点「启动」):按代码评审要求已移除 `auto_start`,因此每次猫娘启动后都需要手动启动一次,下面的口令才会生效。

**然后对猫娘说话即可**(入口已注册为 LLM 工具,说人话就能触发):

| 你说 | 它做什么 |
|---|---|
| 「**安装工作台**」 | 下载官方安装版(Setup.exe)并静默安装到 `%LOCALAPPDATA%\Programs\NEKOWorkshop`,建桌面快捷方式并启动 |
| 「**启动工作台**」 | 启动工作台;已在运行则返回地址 `http://127.0.0.1:5099` |
| 「**工作台状态**」 | 报告安装/运行状态与引擎、模型健康情况 |

**常用变体**(说的时候带出来,或让 Agent 传参):

| 你说 / 传参 | 作用 |
|---|---|
| 「覆盖重装工作台」(`overwrite=true`) | 覆盖重装;**不带此参数时拒绝覆盖**,防误删 |
| 「用这个包安装」(`exe=本地安装包路径`) | 离线安装,不联网(官方安装版 .exe) |
| `url=下载地址` | 自定义下载源(默认官方 Release 最新**安装版 Setup.exe**) |

> 📦 **只支持安装版 exe**:官方从 v1.2.5 起只发布安装版,便携 zip 停留在 v1.2.3 且已停止支持。
> 安装位置由官方安装器固定为 `%LOCALAPPDATA%\Programs\NEKOWorkshop`,升级时自动保留 `runtime` 与 `workspace` 用户数据。

## 入口(全部双注册为 llm_tool,对话/面板/Agent 都能调)

| 入口 | 作用 | 参数 |
|---|---|---|
| `install` | 下载官方安装版并静默安装,可建桌面快捷方式并启动 | `exe` 本地安装包路径(填了不联网)、`url` 自定义下载地址(默认官方 Release 最新安装版)、`launch` 装完是否启动(默认 true)、`shortcut` 是否建桌面快捷方式(默认 true)、`overwrite` 覆盖重装(默认 false,**不传不覆盖**) |
| `status` | 查看是否安装、是否在运行 | `target`(可选,默认目录) |
| `launch` | 启动工作台;已在运行则返回地址 | `target`(可选,默认目录) |

工作台访问地址:`http://127.0.0.1:5099`

## 目录布局与 entry

实现代码在 `plugin/plugins/workbench_installer/`,与 `entry = "plugin.plugins.workbench_installer:WorkbenchInstallerPlugin"` 的包路径同形;仓库根的 `__init__.py` 是入口垫片,把实现类再导出给宿主。

说明:`plugin.plugins.<id>` 是**安装/挂载时**由 SDK 把插件包根注册成的运行时 Python 包(官方 CI 的 `Mount plugin into N.E.K.O tree` 与引擎安装器都会生成该路径),它不是仓库内的相对路径 —— 这也是全部市场插件的公共约定。加载链:宿主按 entry 导入包根(垫片)→ 垫片载入嵌套实现 → 拿到 `WorkbenchInstallerPlugin`。

## 配置(plugin.toml)

| 键 | 默认 | 说明 |
|---|---|---|
| `[plugin_runtime] auto_start` | `false` | 安装器不随 N.E.K.O 自启,由用户/猫娘主动触发 |
| `url` | 空 | 下载地址;留空 = 官方 Release 最新安装版(GitHub) |
| `mirror` | 空 | 镜像回退;留空 = GitHub 失败后自动改用**码云最新发行版**资产;填 `off` 关闭;也可填具体直链 |
| `proxy` | 空 | 下载代理;留空 = 先环境变量、再直连 |
| `download_timeout` | `900` | 下载整体超时(秒,夹在 60–1800) |

### 下载顺序:GitHub 优先,超时回落码云

1. **先拉 GitHub** 的 `releases/latest/download/N.E.K.O.PluginWorkshop-Setup.exe`。
   这一段用 **25 秒停滞阈值**(连续 25 秒收不到数据即判超时,不是按总时长——慢速下载本身可能要 7–10 分钟)。
2. 命中以下任一情况才回落:**超时 / 连不上 / HTTP 401 / 403 / 404 / 429 / 5xx**。
   401、403 多半是源站拒绝(仓库私有化、IP 被限、企业网关拦截),镜像仓是公开的,换源常常能绕过去;
   其余普通 4xx(如 400)换源也没用,直接报错。
3. 回落时向码云 API 取**最新发行版**里的镜像资产,优先级:
   `N.E.K.O.PluginWorkshop-Payload.7z` → `N.E.K.O.PluginWorkshop-Setup.exe` → 旧的 `.tar.xz`
   → **分卷安装包** `N.E.K.O.PluginWorkshop-Setup-vX.Y.Z.exe.part1/.part2/...`。
   分卷必须从 `part1` 开始连续;缺卷会直接判镜像不可用(拼出来的坏文件装到一半才报错更糟)。
   下载后按编号拼回单一 exe,并校验 `MZ` 文件头才交给安装器。
4. 两段下载都在同一条链路上依次尝试 配置代理 → 环境变量代理 → 直连。
5. **按文件头分流**(不看扩展名):`MZ` → 交给官方 SFX 安装器;
   `7z` → 用**插件内置的 7zr.exe** 解压,再合并进安装目录;
   `xz` → 标准库 `tarfile`+`lzma` 兜底。
6. 合并时 runtime/workspace 里**已存在的用户文件不覆盖**,
   缺失的默认内容(如 `workspace/.opencode/skill`)仍会补齐;程序文件正常覆盖升级。

> 📦 **为什么镜像放 7z 压缩包**:码云免费账号的**单附件上限是 100MB**,SFX 安装包约 133MB
> 传不上去(`文件大小已超出限制:100 MB`)。7z(LZMA2)重压后约 90MB,既能上传,
> 解压也比 deflate 快。插件自带 `bin/7zr.exe`(约 590KB,公共领域),
> 用户机器无需单独安装 7-Zip;找不到内置文件时会退回 PATH 或常见安装路径的 7z。
> 从工作台 v1.2.14 起镜像侧只放**分卷 exe**(`...exe.part1/.part2`),插件同样能装(拼回后校验 MZ)。

### 全失败时:自动弹手动下载页

GitHub 与码云都拉不动时,插件会**写一个本地 HTML 并直接用默认浏览器打开**:

- 列出**所有尝试过的下载地址**(GitHub 直链 + 码云镜像)
- 每个地址一个「复制」按钮(剪贴板不可用时自动降级到 `execCommand`)
- 一个「打开」链接,点了直接在浏览器里下
- 显示失败原因,以及「装在哪、覆盖安装会不会丢配置」的说明

返回给猫娘的错误信息里也带完整地址,聊天窗口照样能看到。
不想要弹页可传 `manual_page=false`,或在 `plugin.toml` 里设 `manual_page = false`。

## 安全设计

- **拒绝覆盖**:已安装时不覆盖重装,必须显式传 `overwrite=true`(任何替换操作都需用户明确确认)。
- **安装位置不可控**:只调用官方安装器,安装目录由它固定,插件不接收任意目标路径。
- **只认 .exe**:传 zip 等其它格式一律拒绝,不做本地解压(避免路径穿越与压缩炸弹面)。
- **本机探测不走代理**:健康检查固定绕开代理,避免环境里存在 `HTTP_PROXY` 时把 127.0.0.1 拦掉造成误判。
- 联网失败自动重试:配置的 `proxy` → 环境变量代理 → 直连;HTTP 4xx/5xx 属服务端明确回应,不换代理重试,立即报错。
- `httpx` 缺失时自动用标准库实现。

## 开发

```bash
# 在 N.E.K.O 仓库根执行(便携/离线环境禁用 uv run)
python plugin\neko_plugin_cli\cli.py check workbench_installer
python plugin\neko_plugin_cli\cli.py build workbench_installer --out workbench_installer.neko-plugin
```

详情见 `AGENTS.md`;插件开发规范全文在 `.opencode/skill/neko-plugin-dev/SKILL.md`(工坊生成项目时自动放置,不随插件打包)。
# 没装 SDK 依赖(或 .venv 被删)时,跑纯逻辑离线自检:分卷选择/拼接校验/文件头识别
python tools\offline_check.py
