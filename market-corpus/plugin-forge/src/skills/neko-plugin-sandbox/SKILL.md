---
name: neko-plugin-sandbox
description: 基于 N.E.K.O 官方仓库搭建隔离的插件运行时沙箱：独立启动 plugin/user_plugin_server.py（standalone，自管插件生命周期），在隔离的 LOCALAPPDATA/端口/目录下加载待测插件，用 GET /health、/plugins、/plugin/status 等路由验证真实运行时行为。用于「插件在宿主里到底能不能跑、会不会被 autostart」这类必须真跑一次才能回答的问题。
---

# N.E.K.O 插件运行时沙箱

## 什么时候用它

只在**必须真跑一次**的时候用，不要拿它当日常检查：

- 插件在自己的进程里能跑，但怀疑在宿主里跑不起来（子进程、路径、权限、生命周期）
- 要验证 `plugin.toml` 声明、入口注册、消息平面是否真的生效
- **要验证 autostart 到底会不会触发**（清单里 `[plugin_runtime] auto_start`、待审批门、加载失败态，
  这些只有真启动一次才会暴露——见下面契约表第 8 行）
- 复现「启动 failed / 入口 404 / 模型 403」这类只在宿主里出现的问题
- **验证 hosted UI 面板**：插件声明的 surface、`/plugin-ui-sdk/display.js` 注入、宿主确认的专注/全屏
  握手，都是在真实宿主里才成立的，进程内单测覆盖不到（`GET /plugin/{id}/surfaces`、
  `/plugin/{id}/hosted-ui/source` 可以直接查声明是否被宿主认下来）
- **验证插件制作工作台的后端链路**：工作台不是纯前端，它通过 `/plugin-projects/*`、`/plugin-cli/*`
  在宿主进程里调 CLI（打包 / 预检 / 依赖装填）。「CLI 在这台机器的这个 venv 里到底能不能跑通」
  只有起了服务才验得准
- 交付前做一次发布前的冒烟

**不要**用它跑需要真实 API Key 的完整对话链路——那属于用户环境的验收，不是沙箱的职责。
也**不要**在沙箱里验 Market 提审/发布：那要真实 GitHub 凭据，触红线。沙箱最多验到
「`/market/*` 桥接起来了、token 文件写进隔离 HOME、未授权时是干净的拒绝」为止。

## 契约（来自官方源码，改动前先复核）

| 项 | 事实 | 出处 |
|---|---|---|
| 运行时入口 | `plugin/user_plugin_server.py` | 文件头 docstring：「Main entry point for the HTTP server」(L1-5) |
| standalone 语义 | `build_plugin_server_app(manage_lifecycle=True)` —— 由它自己扫描并 autostart | `plugin/user_plugin_server.py:81` 传 `manage_lifecycle=True`；该参数**默认是 `False`**（`plugin/server/http_app.py:280` 的 `manage_lifecycle` 形参），只有 standalone 入口传 `True` |
| autostart 的实际开关 | `if manage_lifecycle: await lifecycle_startup()` —— **不看环境变量** | `plugin/server/http_app.py`（startup 段的 `if manage_lifecycle:`） |
| 端口 | `NEKO_USER_PLUGIN_SERVER_PORT`；上游默认 `USER_PLUGIN_SERVER_PORT=48916`；standalone 入口被占用时自己向后找（50 次）并回写该变量 | `config/network.py:193` 的 `USER_PLUGIN_SERVER_PORT`；`plugin/user_plugin_server.py:173` 读 `NEKO_USER_PLUGIN_SERVER_PORT`、`:180` 调 `_find_available_port`、`:184` 回写该变量 |
| 健康检查 | `GET /health` → `{"status": "ok", "time": <iso>}` | `plugin/server/routes/health.py:20` 起 `health()`，返回体在 :22 |
| 插件源目录 | `plugin/plugins/<plugin_id>/`，含 `plugin.toml` 的 `[plugin] id` / `version`；内置根 = 仓库 `plugin/plugins` | `docs/plugins/cli.md`；`plugin/settings.py:69-71` 的 `get_builtin_plugin_config_root` |
| 嵌入选址标志 | **已不再是开关**：`NEKO_PLUGIN_HOSTED_BY_AGENT` 只剩 agent_server 在写，`http_app.py` 无读取点；autostart 由上面那个参数决定 | `app/agent_server/api_runtime.py`（`NEKO_PLUGIN_HOSTED_BY_AGENT` 赋值处）；`http_app.py`（`manage_lifecycle` 上方注释明说早先读它会在 import 时冻结） |
| **autostart 的额外前置** | 除上面参数外，还要同时满足：清单 `[plugin_runtime] auto_start = true`（**省略即 false**）、`runtime_enabled` 非 `False`、`runtime_load_state != "failed"`、`runtime_source_missing != True`、且 id 不在 `plugin_autostart_pending.json` 待审批集合里 | `plugin/core/registry.py:1293` 的 `auto_start_val` 默认 False（`parse_bool_config` 在 :1296）；`plugin/server/application/plugins/registry_service.py:1254` 的 `_get_autostart_plugin_ids_sync`（候选排序返回在 :1276） |
| Windows 事件循环 | 强制 `WindowsSelectorEventLoopPolicy`（在起 event loop 之前） | `plugin/user_plugin_server.py:59` 起的 `_configure_windows_event_loop_policy` |
| CLI 能力 | `init / setup-repo / create-repo / sync / check / lint / precheck / build / install / analyze / publish`（**没有 run / validate / release / deps**） | `plugin/neko_plugin_cli/cli.py:105-113` + 各 `*_cmd.register` |
| **验证用路由** | `GET /plugins`（列表）、`GET /plugin/status`（逐插件状态，验 autostart 用这条）、`GET /plugin/messages`；`GET /runs`（集合）405，集合只有 POST；单个 run 有 `GET /runs/{run_id}`（需先有 run_id，不当探活用） | `plugin/server/routes/plugins.py:35` 的 `plugin_status`、`:42` 的 `list_plugins`；`plugin/server/routes/messages.py:22` 的 `get_plugin_messages`；`plugin/server/routes/runs.py:35` 的 `runs_create` 是 POST、`:44` 的 `runs_get` |
| **用户数据根** | Windows 上取 **`LOCALAPPDATA`**（不是 `HOME`/`USERPROFILE`） | `utils/config_manager/storage_roots.py: _get_standard_data_directory_candidates`（def 367，函数体到 381） |
| **用户插件执行根** | `PLUGIN_CONFIG_ROOT` 可显式覆盖（上游已标 legacy，新代码用 `PLUGIN_CONFIG_ROOTS` / `USER_PLUGIN_CONFIG_ROOT`；env 仍生效）；否则 `<数据根>/N.E.K.O/.neko-plugin-installations/plugins` | `plugin/settings.py:get_user_plugin_exec_root`（L102-118） |
| **插件启动超时** | `NEKO_PLUGIN_STARTUP_TIMEOUT`，默认 **10.0s**，且 **>300s 直接 raise ValueError** | `plugin/settings.py:315` 的 `PLUGIN_STARTUP_TIMEOUT` 默认 10.0、`:769` 处 >300 直接 raise |

**复核记录 2026-11-17（commit `db81c1be0`，同日在 `e14f61963` 上复核对齐）**：本表上游锚点按当前 main 改行号 ——
`plugin/server/http_app.py` 的 `manage_lifecycle` 默认值旧记 268，现 280；`plugin/settings.py` 的超时 raise 旧记 788-791，现 766-769。
复核后行号未变的那几格见上表：`plugin/user_plugin_server.py`、`config/network.py`、`plugin/neko_plugin_cli/cli.py`、
`plugin/core/registry.py`、`plugin/server/application/plugins/registry_service.py`。两处是我自己改错的，已改回：`plugin/neko_plugin_cli/cli.py` 一度写成 106-114，
`config/network.py` 一度写成 194。另改正一条事实：内置插件 12 个变 11 个（`wechat_integration` 已移除），
且 11 个全部显式写 `auto_start`，只有 `web_search` 是 `true`。

**复核记录 2026-11-07（commit `645da54`）**：本表相对旧版改了 3 处 ——
① 嵌入标志从「环境变量即开关」改为「`manage_lifecycle` 参数即开关」；
② CLI 能力行整条重写（旧版写 `validate`/`release`/`deps` 但不写 `lint`/`precheck`/`setup-repo`，
且漏了 `sync` 其实还在，只是由 `deps_cmd` 注册）；
③ 新增 autostart 前置条件与验证用路由两行——旧版把「插件没 autostart」只归因于目录名和嵌入标志，
现在这两条都不是主因了。

## 第 0 步：干装载（dry_load.py）

它回答「这个插件装载得起来吗」。有宿主环境时，它调上游自己的导入函数
`plugin/core/host.py:649` 的 `_import_plugin_module` 真装载一次；环境不全时降级为 AST 级检查，
并在输出里明写「降级模式证明不了能不能真的 import」与降级原因。

```bash
D=skills/neko-plugin-sandbox/scripts/dry_load.py
python $D <插件目录>                                                    # 不给 --root：固定降级
python $D <插件目录> --root <沙箱根>                                    # 从沙箱根探 venv 与 checkout
python $D <插件目录> --python <venv 的 python> --neko-repo <官方源码目录>      # 显式指定
python $D --demo-check                                                 # 仓内对照样例（CI 用）
python $D --self-test                                                  # 自检（CI 用）
```

退出码：0 通过；1 发现问题；2 用法或环境错误。**「模式：」那一行必看**——
`real-import` 是真装载过；`ast-fallback` 只是静态检查，别把它汇报成真装载过。

| 核什么 | 依据（本机 neko_host @ b20a9f8 核对，上游钉 e14f61963） |
|---|---|
| 目录名等于 `[plugin].id` | 宿主按目录名扫描，不一致进不了 `/plugins` |
| `entry` 形状 `module:Symbol` | `plugin/config/schema.py:214` 的 pattern |
| 入口模块必须以 `plugin.plugins.<目录名>` 或 `plugins.<目录名>` 开头 | 2026-11-17 对上游 plugin/core/host.py:649 的 `_import_plugin_module` 实测：裸模块名（`main:Class`）在 `source_only` 两种取值下都装不进——False 给 `ModuleNotFoundError`，True 给 `ValueError`「Source-only plugin module must match its source directory」。本机 23 个安装态插件与 11 个内置件也都只用包写法 |
| `PluginBuilder` 用 `source_only=True` 构建，所以 `plugin.plugins.<目录名>` 是出厂写法；`plugins.<目录名>` 这种归一化前缀两种模式都进得来 | plugin/neko_plugin_cli/core/build.py:509 的 `PluginBuilder`（同形另一条在 :528） |
| 包名与目录名的等价是**单向**的：目录可 kebab、包必须 snake | `plugin/core/entry_points.py:47-62`（main `e14f61963`）。反过来不成立，真对不上照旧报错；dry_load 用同一个规矩判（`plugin_package_matches_directory`） |
| 类入口带 `@neko_plugin`，入口方法是协程 | 内置件 11 个类全带该装饰（实测计数） |
| 没写 `[plugin_runtime] auto_start` 要报 | `plugin/core/registry.py:1293` 的 `auto_start_val = False` 就是按 false 处理 |
| 写了 `[plugin.host]` 要报 | 上游已移除该节，import 期直接报错 |
| 真装载子进程环境要隔离 | `LOCALAPPDATA`/`TEMP`/`HOME` 指到临时根，凭据形状变量删掉 |

真装载那条腿每轮自检都跑到：假 SDK 里放一个同签名的 `_import_plugin_module`，
噪音走 stderr、import 期抛异常、入口符号改名三种形状各有用例。

## 标准流程

```bash
S=skills/neko-plugin-sandbox/scripts/sandbox.py

python skills/neko-plugin-sandbox/scripts/dry_load.py <插件目录> --root <ROOT>   # 第 0 步：先干装载

python $S doctor    --root <ROOT>                        # 前置检查
python $S prepare   --root <ROOT> --repo <已有源码目录>   # 挂载已有 checkout（推荐，免克隆）
#   或：python $S prepare --root <ROOT>                   # 沙箱内自行 clone（受代理影响）
python $S sync      --root <ROOT>                        # 拉最新（官方迭代快，每次跑前必做）
python $S install   --root <ROOT>                        # 装依赖（uv 优先）
python $S plugin-add --root <ROOT> --src <插件目录>       # 会同时报告 auto_start 是否声明
python $S serve     --root <ROOT>                        # 前台常驻（推荐，交给后台任务托管）
#   或：python $S start --root <ROOT>                     # 起服务 → 等 /health → 返回
python $S status    --root <ROOT>                        # health + /plugins + /plugin/status + /plugin/messages
python $S stop      --root <ROOT>                        # 先验 pid 身份再动手，走向表见下
python $S plugin-rm --root <ROOT> --id <插件id>          # 撤掉插件，恢复 checkout 干净
#   两条防误删：id 只差大小写不算另一个插件（NTFS 上是同一个目录，直接拒绝）；
#   junction / 悬空联接点按链接摘除，不会被当成「不存在」留在插件树里
```

**为什么每次都要 sync**：官方仓库迭代很快（实测一次同步就落后 1 个提交）。拿旧源码验出来的结论，
可能对应一个已经修掉的 bug——这类"验证过时"比没验证更糟。

## stop 的走向（表就是清单，别处不要再抄它有几行）

`stop` 先读该 pid 的命令行，确认它真是沙箱服务才动手。机器重启后 pid 会被复用，
验不出身份就开枪会打到宿主自己的进程。动手之后还要回头确认它真的没了，确认不到就不说「已停止」。

| 判定 | 行为 | 要 `--force` 吗 |
|---|---|---|
| 命令行里有 `user_plugin_server.py`，且确认已退出 | Windows：`taskkill /T /F` 收整棵进程树。POSIX：只给该 pid 发 SIGTERM，**不收子进程**——插件服务自己拉起的插件进程要靠它自己的收尾逻辑退出。之后清状态文件，打印「已停止」 | 不用 |
| 发了信号但进程没退（它抓住了 SIGTERM 又没收尾） | 不动状态文件，打印端口可能还被占着 | 只在 POSIX 有用：带 `--force` 重跑会补发 SIGKILL。Windows 的 taskkill 本来就带 `/F`，`--force` 在这边不改变行为，重跑只是再发一次同样的命令 |
| 信号发不出去（进程不归本机用户，`PermissionError`） | 不发信号、不动状态文件，日志写明这个 pid 不是沙箱能停的东西 | 不用：`--force` 也发不出去，别把 force 当「无视权限」 |
| pid 已不存在（服务崩了，或 `start` 超时后自行终止） | 只清状态文件 | 不用 |
| `taskkill` 起不来（没装、被挡、OSError） | 不发第二道信号、不动状态文件，日志写明是 taskkill 起不来 | 不用：强也强不起来，先修工具链 |
| pid 属于别的进程（重启后复用） | 不杀，只清状态文件，并提示人工核对端口 | 不用，也不许用：force 的意思不是「是别人的进程也照杀」 |
| 命令行读不到、进程还在 | 拒绝动手，报原因 | 核对端口归属后带 `--force` 重跑 |

两条硬规矩：SIGKILL 只在 POSIX 且明确带 `--force` 时才发——不带 force 就升级等于击毙想收尾的插件；Windows 的 taskkill 本来就带 `/T /F`，`--force` 在这边不改变行为。两条都由自检钉住（工单 W07）。
读命令行与确认退出都分平台：Windows 用一条 PowerShell 同时给出「进程在不在」与命令行，
确认阶段改用稀疏探测格子（0.4 / 1.0 / 2.0 秒），因为每次探测要起一个 PowerShell。
Linux 读 `/proc/<pid>/cmdline` 与 `/proc/<pid>/stat`。macOS 没有 `/proc`，退回 `ps -p <pid> -o command=`
与 `ps -o state= -p <pid>`。僵尸态（`Z`）算已退出：进程已经不占端口，只剩父进程没回收。
2026-11-16 前 macOS 上这条永远读不到，于是每次 `stop` 都要 `--force`（外部深审 A571 第 2 条）。

## 隔离做了什么

1. **`LOCALAPPDATA` 重定向** → `<ROOT>/state/LocalAppData`。**这是最关键的一条**：N.E.K.O 在 Windows
   上按 `LOCALAPPDATA` 找数据根，只改 `HOME`/`USERPROFILE` 完全无效——会直接读到真实
   `%LOCALAPPDATA%\N.E.K.O`，把用户已装的插件全部拉进来 autostart（实测一次就加载了 20+ 个真实插件，
   其中含会自动发消息的 `qq_auto_reply`）。同时重定向 `APPDATA` / `TEMP` / `TMP` / `HOME`，
   并用 `PLUGIN_CONFIG_ROOT` 双保险钉死插件执行根。
2. **外部 checkout 不写 `.venv`** → 用 `--repo` 挂载别人的源码时，环境建在 `<ROOT>/.venv`。
3. **端口独占** → 先探测空闲再传给服务端，避免和用户正在跑的 N.E.K.O 撞端口（脚本默认 43117，
   刻意避开上游的 48916）。这也正好是上游后来推荐的做法：仲裁放在拉起方、把结果通过
   `NEKO_USER_PLUGIN_SERVER_PORT` 发布给子进程（`config/network.py:199` 起的 `resolve_user_plugin_base` docstring 讲清了
   为什么该这样）；standalone 入口的自增只是兜底。
4. **目录独立** → 日志、状态全落在 `<ROOT>`。
5. **显式 standalone** → `user_plugin_server.py` 硬编码 `manage_lifecycle=True`，autostart 一定走。
   脚本仍会清掉 `NEKO_PLUGIN_HOSTED_BY_AGENT`，但**它现在不再是开关**（`http_app.py` 已无读取点），
   清理只为不往宿主日志里注入一个"嵌入式"的假身份。**别再把"服务起来了但插件没加载"归因到它**——
   先看下面坑 #6（`auto_start` 默认 false）。

6. **宿主凭据不进沙箱** → 起服务前按变量名形状删掉凭据类环境变量，删了哪些会写进日志。
   名单不在这里抄一遍（抄一份就会过期一次）：词根表看 `SECRET_ENV_RE`，粘连与子串表看
   `SECRET_ENV_SUB`，显式保留表看 `KEEP_EXACT`，三张表都在 `scripts/sandbox.py` 的 `_server_env` 上方。
   两个真实后果要点名，否则排查时找不到原因：`WT_SESSION` 与 `SESSION_ID` **会被删**
   （`SESSION` 在词根表里），`SSH_AUTH_SOCK`、`KEYBOARD`、`KEYMAP` 与裸 `PWD` **保留**。
   范围只覆盖 `_server_env`（插件服务与
   插件进程）；`_uv_env`、`pip`、`git` 与建 venv 的子进程仍继承完整环境，因为 uv / pip 需要
   代理与证书变量。**值里带 URL 凭据的也删**（`DATABASE_URL=postgres://u:口令@db`），带口令的代理 URL（`HTTP_PROXY=http://u:口令@proxy`）同样会被删，换代理时注意；模板占位 `${…}`、`<…>` 不算凭据。这是纵深防御的一层，不是防火墙：
   插件仍以同一用户身份持有完整文件读权限与外网出口。

**一处做不到的隔离（必须知道）**：插件的**代码**必须落在 `<ROOT>/repo/plugin/plugins/<id>/`，
因为宿主就是按这个路径扫描的。用 `--repo` 挂载别人的 checkout 时，`plugin-add` 会写进**那个仓库**的
`plugin/plugins/` 下（表现为 `git status` 里的 `?? plugin/plugins/<id>/`）。这是上游允许的本地插件放置方式，
但请用 `plugin-rm` 收尾，别留在别人的工作区里。

## 真实踩过的坑（都已固化进脚本；标题不数条数，加一条不用改三处）

| # | 现象 | 根因 | 处置 |
|---|---|---|---|
| 1 | `pip install -r requirements.txt` 跑十几分钟卡在 `qrcode-terminal` 的源码构建 | 上游钉了大量需本地编译的包 | **改用 uv**：200 个包 **1 分 48 秒**装完（pip 十几分钟没装完）。requirements 第 3 行是 `-e .`，必须 `cwd=repo`；该文件由 `uv export --locked` 生成，改依赖要改 `pyproject.toml`/`uv.lock` |
| 2 | uv 报 SSL 证书失败 | 本机代理换了根证书，uv 默认用打包根证书 | `UV_SYSTEM_CERTS=1`（旧名 `UV_NATIVE_TLS=1`）+ `UV_LINK_MODE=copy`（C: → E: 跨盘硬链接会失败） |
| 3 | `/health` 明明 200，`status` 却报 `WinError 10061 连接被拒` | `urllib` 吃了 `HTTP_PROXY=127.0.0.1:xxxx`，把本机回环也代理转发 | 探活用 `ProxyHandler({})` 显式绕过代理 |
| 4 | 服务刚 `/health` 200 就消失，日志无任何退出记录 | 父进程带着 job object 时（受管后台任务那种），子进程随 job 一起被清。父进程只是普通命令时不会 | 要常驻就用 `serve` 交给受管后台任务。`start` 的 `DETACHED_PROCESS` 只挡得住普通父进程退出，挡不住 job object。两侧都实测过（2026-11-17）：`start` 起的服务在父命令退出后仍活着，`stop` 4.9 秒确认真的停；`serve` 的受管任务被终止时整棵树一起消失，状态文件留下，下一次 `stop` 走 `gone` 分支只清状态 |
| 5 | 插件被判 `failed`（如 `web_search`），日志写明 `startup timed out after 10.0s` | 上游默认 `NEKO_PLUGIN_STARTUP_TIMEOUT=10`，冷 venv 首次 import 重依赖远超 | 脚本默认传 **60s**（`--plugin-startup-timeout` 可调）；**别超过 300s**，上游 `plugin/settings.py:769` 会直接 raise |
| 6 | 服务起来了、`/plugins` 里**有**这个插件、`/plugin/status` 显示它不是 running | 上游 `plugin/core/registry.py:1293`「没写 `plugin_runtime.auto_start` 时默认关闭」。仓库里 11 个内置插件全部显式写了 `auto_start`，只有 `web_search` 是 `true`（`plugin/plugins/web_search/plugin.toml:23`），其余 10 个是 `false` | 在插件 `plugin.toml` 写 `[plugin_runtime] auto_start = true`；`plugin-add` 现在会直接报告这一项 |
| 8 | 装了 `DEMO_PLUGIN` 之后，`demo_plugin` 的工作内容没了，两条命令都退 0 | NTFS 不区分大小写：`dest.exists()` 判成「同一个目录要重装」，`rmtree` 删掉的是另一个插件；`plugin-rm` 打错大小写也照样删，报「已删除」 | `plugin-add`/`plugin-rm` 先按目录里的真名解析（`resolve_plugin_entry`），只差大小写就拒绝并说明；平台行为用探测得到，不靠 `os.name` 猜（工单 W01） |
| 9 | `plugin-add` 撞上 junction 直接 traceback；`plugin-rm` 对一个删不掉的条目报「不存在」 | junction 不是软链：`Path.is_symlink()` 回 False，可 `shutil.rmtree` 按 `os.path.islink` 拒绝执行；悬空 junction 的 `exists()` 也是 False，就被当成不存在 | 形态判断加 `is_reparse_point`（读 `lstat` 的 `FILE_ATTRIBUTE_REPARSE_POINT`），摘链接统一走 `remove_link`（先 `unlink`，目录型链接退到 `rmdir`）（工单 W02） |
| 7 | 列表里有、`auto_start` 也写了，还是不启动 | 该 id 落在 `plugin_autostart_pending.json` 的待审批集合里（新装/导入的插件默认待审批，要用户在面板里开独立开关） | 沙箱是全新数据根，一般没有该文件；若复用了旧 `<ROOT>`，删掉 `state/LocalAppData/N.E.K.O/` 下那个 json 再启 |

另有两个 Windows 专属陷阱：`os.execve` 换解释器直接 **SIGSEGV**（已弃用，改子进程前台等待）；
`uv venv` 默认拿系统最新 Python（本例 3.14/3.15），对 N.E.K.O 太新，脚本固定 `--py-version 3.11`。

## 失败判定（别只看 200）

| 现象 | 含义 | 排查 |
|---|---|---|
| `/health` 200 但 `/plugins` 为空 | 插件没被扫描到 | 目录名是否等于 `plugin.toml` 的 id；`plugin/plugins/` 下是否有该目录 |
| `/plugins` 有它，但 `/plugin/status` 不是 running | autostart 前置条件没满足 | 按顺序查：`[plugin_runtime] auto_start`、`enabled`、加载是否 failed、待审批集合（坑 #6/#7） |
| 进程启动即退出，日志里有 `PLUGIN_STARTUP_TIMEOUT ... unreasonably large` | 超时配超过 300s，上游 import 期就 raise | 用 ≤300；脚本已夹紧并 WARN |
| 进程启动即退出（其它） | 依赖缺失或 import 失败 | 看 `logs/server-*.log` 最后 25 行 |
| 端口被换掉 | 原端口被占 | 以 `sandbox.json` 里的 port 为准 |
| 插件 `status: failed` + `startup timed out` | 冷启动 import 太慢 | 调大 `--plugin-startup-timeout` 重试 |
| 插件 `status: running` 但 LLM 工具注册报 `httpx.ConnectTimeout` | 工具注册要回调主程序（`plugin/core/communication.py`），沙箱里没有主程序 | **可忽略的噪声**：不影响插件本体运行，别把它当失败 |
| 一次加载出十几个陌生插件 | 数据根没隔离，读到真实 `%LOCALAPPDATA%\N.E.K.O` | 确认 `LOCALAPPDATA` 已被重定向（别用 `--no-isolate-home`） |
| `GET /runs` 返回 405 | 正常：该路径只有 `POST`（创建 run），没有列表 | 列表看 `/plugins`；单次 run 看 `/runs/{run_id}` |

## 红线

- **不在沙箱里放真实密钥**：`config.toml` 里的 API Key 用假值，沙箱只验链路不验模型。
- **不在沙箱里做 Market 提审/发布**：`/market/*` 与 `plugin/utils/github_auth.py`、`github_release.py`
  会要真实 GitHub 凭据；沙箱只验桥接与拒绝路径。
- **不把沙箱目录当用户目录**：`<ROOT>` 可随时整个删掉，不要往里放需要保留的产物。
- **不修改官方仓库源码**来让插件跑通——那是掩盖问题。要么改插件，要么记录为平台限制。
- **不收尾就别跑**：`plugin-add` 会写进被挂载 checkout 的 `plugin/plugins/`，跑完用 `plugin-rm` 撤掉。
- **依赖很重**（1421 行 requirements，约 200 个包 / 300 MB），`doctor` 会先看磁盘余量。
