# 更好的网络搜索 / Better Web Search

给 N.E.K.O 的免 API Key 联网搜索 + 网页正文阅读插件。**装好就能用，不需要注册任何服务、不需要填任何 Key。**

一个不需要任何 API Key 的联网搜索与网页正文阅读插件，开箱即用，失败自动切换后端。插件自带面板：首装猫娘会开口引导；**首屏只做一件事**——查宿主内置『网络搜索』还在不在跑，还在跑就把用户指到宿主的「插件中心」去停用（停用后这一页不再出现，读不到状态时也不弹）。进入后分**状态 / 进阶 / 诊断**三区（`Tabs`），长段解释一律收在默认收起的折叠块里，首屏只留徽章、数字和主操作。Exa 与 AnySearch 两家密钥各自一卡、各自带注册教程（做了就自动收起）：Exa 可填多把轮着用，AnySearch 只有一把、填了当场真搜一次验货；另有代理软件兼容开关与一键网络自检。

---

## 为什么它是免费的

大多数"免费搜索插件"其实是去爬搜索引擎的搜索结果页，这条路会被限流、会被反爬、而且在国内经常根本连不上。这个插件的主路径不是爬页面，而是走**别人公开免费的检索网关**：

下表为 mainland 直连实测（系统代理关闭、无环境代理，plan §0 同款环境）：

| 后端 | 要不要 Key | 原理 | 国内直连（实测） |
| --- | --- | --- | --- |
| `anysearch`（默认首选） | 不要（可选填） | AnySearch 的匿名档 | ✅ 1.2–5.0s |
| `exa`（填了自己的 Key 就升到首位） | 不要（可选填自己的免费 Key） | Exa 的公共 MCP 端点 `mcp.exa.ai`，对方替所有匿名用户付钱 | ✅ 快路径约 1.0–2.0s（2026-09-19 真机五次实测 1.8–2.0s），返回标题+链接+高亮片段 |
| `bing` | 不要 | 抓结果页 | ✅ 约 0.6s，中英文都好，已进默认链路 |
| `baidu` | 不要 | 抓结果页 + BAIDUID 预热 | ❌ 直连裸请求回"百度安全验证"（HTTP 200 验证页），预热才有救，排链路末位 |
| `duckduckgo` | 不要 | 抓 `html.duckduckgo.com` 的结果页 | ❌ 直连 DNS 污染直接超时；**没检测到代理时根本不会进实际链路**（见 `duckduckgo_needs_proxy`） |
| `sogou` | 不要 | 抓结果页 | ❌ 实测无可解析结果，不进默认链路 |
| `searxng` | 不要 | **你自己的**实例，唯一真正不受别人限流的路 | 自己部署 |

`anysearch` 排第一是这一版定的默认顺序。理由是 `exa` 的免 Key 档为所有匿名用户共用同一个池子，问得快就会撞 429（插件里那句「Exa 免配额已用完（429）」就是它），不该让整条链路的第一次尝试压在这个公共池上。**唯一的例外**：`exa_api_keys` 里至少有一把、而 `anysearch_api_key` 还空着 → `exa` 自动升到第一位，这时它走的是你自己账号的额度，不与他人抢。`[search] backend` 显式选定引擎时仍然优先于这条规则。

首位只看配置、不会在一次会话中途变——整池密钥都用完时的让位由后端冷却负责（见下节）。

`exa` 值得为它注册 Key 的地方是它给的是**带高亮的结果**，而不是搜索引擎那种一句被截断的预览。要看**整页正文**请接着用 `fetch`（它会先直连抓原文，失败才走 Exa 阅读器）。

> 如果你愿意拿延迟换更长的正文片段，可以把 `exa_tool` 显式改成 `advanced`（返回页正文、但实测 3.7–11.7s，会接近单后端 12s 超时）。默认**不**这么做。

### 想更稳？填 Exa 免费 Key（可选，不是必需），一把不够就填几把

- 注册地址 <https://dashboard.exa.ai/api-keys>，国内**直连可达**；登录只有 Google / Email 两种方式，国内建议走 **Email 注册**。
- 免费档 = 注册送 $20，之后**每月刷新 $10**；≈ **1400 次/月 ≈ 47 次/天**。
- 计费口径：Exa 定价页写的是 *$7 per 1k requests (up to 10 results)*，**一次请求最多 10 条不加价**，超出部分另计 $1/1k。本插件走 `https://mcp.exa.ai/mcp` 的 `web_search_exa` 工具（带 Key 也是同一个端点，Key 放在 `x-api-key` 头），实测响应里 `costDollars.total = 0.007`，且 numResults 取 2/6/10 **同价**（`docs/plan-v0.2.md` §0）。所以 1400 次/月这个数只在"每次 ≤10 条"时成立；我们允许 `max_results` 到 15，模型要满 15 条的那次大约是 $0.012。
- 不填 Key 照样能搜。填 Key 买到的是**额度确定性**和**链路首位**，不是速度：实测 advanced 档 3.7–11.7s 会顶穿 12s 预算，所以 `exa_tool = "auto"` 恒用快路径（带不带 Key 都一样）。
- **一把不够用就填多把（环形轮转，没有计时器）**：`exa_api_keys` 是一个列表，一个 Exa 账号一把 Key。一次搜索从**上一把成功的那把**开始，这把报错就顺延下一把，走到列表末尾再绕回开头。所以一把用坏的钥匙，要等其它每把都轮过一遍才会再被试一次——**循环本身就是重试节奏**，不需要额外记"歇到几点"。顺移的条件是**这把答得不对**（401 / 402 / 429、5xx、给了段解析不出来的响应）；**根本没答上**（超时、连不上）时不去撞下一把，见下条。
- **重启不会把循环打回原点**：环停在哪儿，就按密钥的指纹记在宿主给插件的 KV 存储里，插件或软件重启后从同一把继续。只有位置真的移动时（也就是一把用坏时）才写一次；存储不可用只记日志，不影响搜索。落盘这个动作挂在**搜索**的收尾，`fetch` 走同一个环、也会挪内存里的起点，但不单独写存储，所以只读网页、没再搜过的话，重启最多退回上一次搜索记下的那把（偏一把，下一次搜索就校准了）。
- **整池都报错、或 Exa 压根连不上 = 整条 Exa 链路进退避期**：这两种情况抛的都是 `BlockedError`，正好复用协调器已有的后端冷却（`cooldown_seconds`），下一次搜索不会把整个环再跑一遍，而是直接改走 anysearch / 必应。连不上时**一把都不多试**是有意的：首后端独享 `total_timeout_seconds` 这 25 秒，让 N 把钥匙各自挂满 12 秒的代价不是"慢一点"，是 anysearch 和必应一分预算都拿不到、这一轮真的搜不到东西。这条边界由 `tests/test_entries.py::test_a_hang_rotates_nothing_and_hands_exa_to_the_cooldown` 钉住。
- **要不要降级成无 Key 是一个开关，默认关闭**：`exa_key_fallback_anonymous = false`。关掉的理由很实际——池子都空了时匿名档通常也不通，不如把 25 秒预算留给下一个后端。设为 `true` 时的行为是：整环跑完 → 匿名档试一次（只一次，不循环）→ 失败就交给后端链。
- **401/403 会写明"这把没验通过"**，面板逐把显示状态；`fetch` 读正文走同一个环，不会刚在搜索里撞死、转头又拿它抓网页。
- **429 顺移但绝不定罪**：Exa 用 402 表示没钱、用 429 表示问得太快（见其 billing 文档）。带 Key 的 429 只是**换一把继续**（不同账号各有自己的限速），不会在面板上留下"额度用完"的标记——否则手快连点两下就能把整个池子误杀掉。同理 5xx 与"解析不出来"也只顺移；**能写显示状态的只有 401/403 与 402**。
- **不要再给环加计数器**：`exa_key_max_attempts` 这类"每把最多试几次"的开关加过又删了——一整圈天然就是上限，多一个配置就多一处会说谎的地方。
- 查不到"还剩多少额度"：Exa 的 `GET /api-keys/{id}/usage` 只报已花费（`total_cost_usd`）不报余额，且属于 Team Management API、要找客服按团队开通。所以哪把钥匙还能用只能靠它实际答了什么来判断，面板不猜数字。
- 安全：Key 只存在本插件配置里；日志、状态上报、面板回显一律最多出现 `exa****尾4位`（宿主日志不脱敏，所以我们连状态都不写明文）。

## 装完之后怎么用

对猫娘直接说"搜一下 XXX"就会走 `search`；想知道某条结果的具体内容时说"打开这个链接看看"就会走 `fetch`。两个入口都注册给了对话侧：

- **`search`** — 免 Key 搜索。参数 `query` / `max_results` / `backend`。返回 `summary`（含标题、摘要、链接），外加 `backend` 与 `attempted`（这一次实际走过哪几路）。
- **`fetch`** — 读取网页正文。参数 `url` / `max_chars` / `mode`。先本地直连抓正文（**URL 不会经过任何第三方**），失败再走远端阅读器。

`fetch` 带 SSRF 防护：`localhost`、`127.0.0.1`、`192.168.x.x`、`169.254.169.254`（云元数据地址）、`*.local`、以及解析到内网的域名、`javascript:` / `ftp:` 之类协议，全部拒绝。唯一例外是 `[net] ssrf_allow_ranges`（默认 `198.18.0.0/15`）：兼容 TUN + fake-ip 代理把外网域名解析成假 IP 的情况，且**只对域名解析结果放行**——直接把 IP 填进链接照样拒绝。

面板里还有：**「最近一次搜索」回看卡**（哪一路答的、几条、多久、依次试过哪几路——这些原本只在插件日志里，宿主不记插件的工具返回体，所以面板是唯一能看到的地方；只记关键词字数，不记内容，重启插件即清空）、**「Exa 密钥」卡**（多把轮转，教程收在「怎么拿到一把 Exa 密钥」折叠块里，池子里已经有钥匙时它自己收起）、「**AnySearch 密钥**」卡（同样带注册教程，一家一卡不再混排）、一张「密钥账号小提醒」（两家都能退订广告邮件、账号与密钥照常用）、宿主内置『网络搜索』的**状态只读显示**（避免两个搜索插件抢活；要停用请到宿主的「插件中心」，原因见下）、**「代理软件兼容」开关**（Clash/mihomo 的 TUN + fake-ip 模式下面板一键切，不需要你懂 CIDR）、网络自检（默认跟随检测到的代理决定是否**直连/代理双测**，也可手动固定；双测会把探测次数翻倍，结果表格里"走代理能不能用"单独一列，未测不等于用不了）。

> **为什么停用内置要用户自己动手**：v0.9.7 及之前，面板这个开关是替用户去按宿主的本地管理接口
> （`POST /plugin/web_search/stop`）。真机测量：宿主正在重载插件列表时那一按要 8 秒以上才回话，
> 而客户端 8.0 秒就放弃了——改动其实已经落地，插件却只能报"未能连接宿主管理接口"。
> 从 v0.2.0 引入到 v0.9.6，真机 19 次启动的这条日志**全是失败，没有一次是真失败**。
> 改成后台每 20 秒核对之后又不彻底：核对成功一次就不再看了，内置要是之后被启回来就没人管。
> 所以这条路整个撤掉：现在面板只读状态，停用请去「插件中心」，那是宿主自己做的、还会顺手把「开机自启」写成关。

> **停用内置的影响范围**：它只影响**对话侧的搜索工具**这一路。宿主自己还有两条不走 LLM 工具的路径
> 经 `utils/web_scraper/search_gateway.py:228` **硬编码**调用内置 `web_search` 插件；停用内置之后，它们会安静地
> 返回空结果（`{"success": False, "results": []}`），**不会**改走本插件：
>
> 1. **主动聊天的「窗口」信息源**（`utils/web_scraper/window_context.py:404,521,761` →
>    `main_logic/proactive_chat/sources.py:403-406`）：照着用户正在看的窗口主动搭话那一路拿不到资料，
>    该信息源被丢掉，宿主日志里留一行 `信息源 [window] 获取失败`。
> 2. **主动话题的素材联网增强**（`main_logic/topic/materials.py:73-101`）：带真实标题与链接的
>    「找到了和某某有关的素材…」不再出现；`_safe_fetch`（`materials.py:244-251`）把失败**连日志都不记**地吞掉。
>    同一套素材里的「梗 / 音乐」两类走别的通道，不受影响。
>
> 其余联网功能（B 站 / YouTube / Twitch 热搜与动态、一起看、网页正文）走自己的 httpx 通道，停用内置不影响。
> 这两条的实际影响面也不大：主动聊天共 9 种信息源（`main_logic/proactive_chat/sources.py:313-455` 里 `_fetch_source`
> 的九个 `mode` 分支：vision / news / community / video / window / home / personal / music / meme），
> **只有 `window` 需要搜索，且它默认关闭**（`main_logic/proactive_chat/contracts.py:50`
> `use_window_search: bool = False`）；主动话题的联网增强默认开着但**失败不阻断**，宿主自己的注释就写着
> "Any failure leaves the cheap keyword floor hint intact"（`main_logic/topic/pipeline.py:962-966`），
> 话题照发、只是开场白退成关键词提示。
> 想靠"把插件 id 也叫 `web_search`、同名盖掉内置"来接这两条路是**走不通的**：注册表确实支持用户目录覆盖内置
> （`plugin/server/application/plugins/registry_service.py:187-247`），但安装侧对 `action == "override_builtin"`
> 的本地/zip 导入直接 409 拒绝（`plugin/server/application/plugin_cli/service.py:503-510`
> `PLUGIN_BUILTIN_OVERRIDE_MARKET_REQUIRED`，原文 "builtin plugins can only be overridden by a SHA256-verified
> Market package"），而且真走 Market 覆盖时包里不许带 `previous_ids`（`install_plan.py:321-322`）、
> 已有的 `plugin_runtime_overrides.json["web_search"].enabled=false` 还会把顶替者一起停掉。
> 所以这两条只能等宿主把 `search_gateway.py:228` 的那个 id 改成可配置。
> 另有一条容易误判的：**停用期间那两条路径不会污染宿主缓存**（`search_gateway.py` 里 `_store()` 只有一个调用点
> `:460`，只在"插件跑完了但没结果"时写），插件停着时是抛异常并给该后端上 300 秒失败冷却（`:370-373`、`:345-347`）。
> 所以把内置开回来之后头几分钟还是空，是**在冷却里**，不是缓存脏了。
> 要是你依赖上面那两条，就别停用内置，或者去宿主侧把它改成可插拔。
>
> **停用内置之后，本插件的 `keywords` 就是宿主唯一的关键词兜底**。宿主在派发动作前有一道闸门
> （`brain/task_executor.py:1958-1968`：`external_intent < 0.2` 且没有任何确定性信号 → **整轮不派发任何插件**，
> 于是"凭记忆答"），而插件侧唯一的确定性信号就是把 `keywords` 当**正则**去 `re.search`
> （`brain/plugin_filter.py:114-124`）。实测过它真的会咬人：2026-09-19 22:45:07 用户说
> 「你仔细帮查查然后总结一下给我」，宿主日志是
> `[AgentGate] skip assessment: external_intent=0.00 < 0.20, no deterministic signal` —— 于是那句
> "本喵这就去仔细查"之后什么都没有发生（顺带说明：那句承诺是 `config/prompts/prompts_sys.py:170-172`
> 要求模型说的，而对话模型的工具表里**没有**搜索工具，插件入口走的是另一条 analyzer 链路，所以
> "说去查"和"真去查"是两条互不知情的路）。
>
> 所以本仓库的 `keywords` 比内置更宽：`(?<![调检侦考审警探追巡])查(?:[一下询找看]|查|资料|了)`
> 覆盖 查查/查下/查询/查找/查看/查了，内置的 `查[一找]` 和旧写法都漏了这些（**「查查」两边都命中不了**，
> 这不是接管带来的回归）；另有 `搜(?:[一下]|一搜|搜)` 与 `look\s?up`。那条否后视同时消掉了内置会犯的
> "检查一下身体 / 调查一下 / 审查一下"三类误伤。
>
> **不要再往裸 `查|搜` 加**：命中不只是"多跑一次评估"——`task_executor.py:1503` 会把命中的插件强推进
> Stage-2 候选、`:1525` 在提示里打上 `[KEYWORD MATCH]`，而评估提示（`config/prompts/prompts_agent.py:370,414,458`）
> 要求模型**优先**选打了标的插件，所以误命中会变成一次真搜索并占用引擎冷却，把后面真该搜的那轮挡在门外。
> 这条边界由 `tests/test_smoke.py::test_keyword_shortcut_covers_what_the_builtin_matched` 钉住：
> 该命中清单、不许命中清单、以及"我们刻意比内置窄"的三处，分开记账。
>
> 仍然要说清楚：`external_intent` 那个分是宿主打的，插件改不了；加宽只保证"闸门不会因为没关键词而刹这一轮"，
> 最后仍由 analyzer 判断要不要派发。日常最可靠的说法还是带「搜」字。

## 装到自己的 N.E.K.O 上

在 N.E.K.O 源码根目录构建，然后在 **插件中心 → 导入** 选这个包：

```bash
cd N.E.K.O
PYTHONDONTWRITEBYTECODE=1 uv run python -m plugin.neko_plugin_cli build "../plugins/better_web_search"
# 产物：N.E.K.O/plugin/neko_plugin_cli/target/better_web_search.neko-plugin
```

> **老名字 `free_web_search` 装过的话，先卸载再导入**：插件 id 换了，宿主**不会**把
> `plugins/free_web_search/config/` 搬过去（`registry.py:1461 _migrate_plugin_id` 只搬进程内的注册表
> 映射，不碰磁盘配置），所以 Exa 与 AnySearch 的密钥都要重新填一遍。包里声明了
> `[plugin].previous_ids = ["free_web_search"]`，旧插件还在的时候导入会被直接拒绝并提示冲突
> （`install_plan.py:135-153`），这是故意的——两个都注册了 `search` 工具的插件同时活着比装不上更糟。

> **构建后花一秒自验包**（元数据在不在 = 面板能不能用；测试夹具不该在里面）：
>
> ```bash
> python -c "import zipfile,sys; n=zipfile.ZipFile(sys.argv[1]).namelist(); \
> print('files:', len(n)); \
> print('plugin.meta.json:', any('plugin.meta.json' in x for x in n) or '缺失！这份包不能让面板正常工作'); \
> print('tests/ leaked:', any('/tests/' in x for x in n) or 'no'); \
> print('quickstart.md:', any('docs/quickstart.md' in x for x in n) or '缺失！教程打不开')" \
> N.E.K.O/plugin/neko_plugin_cli/target/better_web_search.neko-plugin
> ```
>
> 干净的样子是 `files: 24`、`tests/ leaked: no`、`quickstart.md: True`。
>
> 实测过的坑：`neko-plugin` 这个命令入口（venv 里的 console script）可能解析到**另一份旧的宿主
> 检出**，那份 CLI 还没有 entry 元数据探测步骤，于是构建照样 `[OK]`、**不打任何警告**，但产物里没有
> `plugin.meta.json`。宿主只能退回"从 manifest 猜入口"，静态注册表拿到空集，面板每个按钮都回
> `UI action 'xxx' is not a plugin entry`（404，文案还骗人——它说的是"一个入口都没注册上"）。
> 用 `python -m plugin.neko_plugin_cli` 代替 `neko-plugin` 就能确定性地走当前这棵树（cwd 在哪儿都无所谓，
> 已实测两边都能出 meta）。

Windows PowerShell 下这样带环境变量：

```powershell
$env:PYTHONDONTWRITEBYTECODE = "1"
uv run python -m plugin.neko_plugin_cli build "../plugins/better_web_search"
```

> 本仓库独立检出在别处时（例如与 `N.E.K.O` 同级、目录名保留自旧仓库名的
> `n.e.k.o_plugin_Better_web_search`），把路径参数换成那个目录名即可，"在宿主根目录执行"这一条不能变。

> **为什么要加 `PYTHONDONTWRITEBYTECODE=1`**：`neko-plugin build` 会 import 插件来探测它的 entry 元数据，
> 而探测用的是**暂存目录里的那份副本**（构建日志里能看到它在 `%TEMP%\neko_build_<插件 id>\payload\plugins\…`
> 下读 `plugin.toml`），于是这一步写出的 `__pycache__/*.pyc` 落在暂存树里，最终跟着分发包一起被 zip。
> 实测不带这个环境变量：包里多 8 个 `.pyc`、159 KB 变 290 KB，而**源码目录的 `__pycache__` mtime 一个都没变**
> ——所以"先清理源码目录再构建"是无效动作，字节码压根不是写在源码目录里的。
> `[tool.neko.build] exclude_dirs` 只在复制到暂存树那一步生效（`plugin/neko_plugin_cli/core/build.py:342`），
> `export_package` 压缩时不再重套规则，因此对这条泄漏**无效**。上游的修法是构建时设
> `sys.dont_write_bytecode = True`，以及让压缩步骤复用 `build_rules.should_skip_path()`。
> 同理，本插件的分发包排除（`tests/` 与内部施工单 `docs/plan-*.md` 不进包）也是靠 `[tool.neko.build]`
> 生效的：包里只有 24 个文件，`docs/quickstart.md` 因为在 manifest 里被声明为教程 entry 所以必须留。

本插件**零第三方依赖**（只用 Python 标准库），所以没有 `vendor/`，包很小，也不会因为宿主依赖版本变动而坏掉。

## 配置

复制 `config.example.toml` 到插件配置里按需修改。最常碰的几项：

```toml
[search]
backend = "auto"
backend_chain = ["anysearch", "exa", "bing", "baidu"]   # 按直连实测排序；ddg 只有在检测到代理时才会被真正使用。填了 exa_api_keys 而 anysearch_api_key 还空着时，exa 会自动升到首位（无需改这一行）
max_results = 6           # 只是"模型没填时"的默认值：对话里模型会自己填 max_results 并压过这里
                          # （再被收敛到 1..15）。目前**没有**能硬性限制单次条数的开关——实测
                          # 模型会要 5 也会要 10；面板的「最近一次搜索」能看到当次要了几条。

# 代理：这是"搜索突然不能用"的头号原因
proxy = "auto"              # auto=跟随系统/环境变量，off=不用，on=全走，或直接填 http://127.0.0.1:7890
proxy_url = ""              # 显式代理；填了就等效"有代理"，duckduckgo 会回到链路里
duckduckgo_proxy = "proxy"  # DDG 在国内直连不通，默认强制走代理
bing_proxy = "direct"       # 百度/必应/搜狗保持直连，不要白白绕代理

# Exa 密钥（可选升级）：更推荐在插件面板里逐把填写并一键测试
exa_api_keys = []           # 一个账号一把；邮箱注册每账号每月 $10：https://dashboard.exa.ai/api-keys
exa_tool = "auto"           # auto 恒用快路径（advanced 实测会顶穿 12s 预算，仅显式选择时才用）
exa_key_fallback_anonymous = false    # 开关：整池密钥都报错时要不要再降级匿名档（默认关闭）
baidu_warmup = true         # 先领 BAIDUID cookie 再搜，否则基本必撞"百度安全验证"
duckduckgo_needs_proxy = true        # 无代理时 ddg 不进 effective chain

# 可选增强（留空照样能用）
anysearch_api_key = ""      # 填了限额更高，不填走匿名档；面板里也能填并当场真搜一次。填了它 exa 就不再升到首位
searxng_base_url = ""       # 自建实例，例如 http://127.0.0.1:8888

[net]
ssrf_allow_ranges = ["198.18.0.0/15"]   # TUN+fake-ip 兼容，只对域名解析结果放行
```

排查建议：如果搜索没结果，优先点面板里的**网络自检**（直连/代理双测 + 推荐链路 + 中文结论）；或看 `startup` 返回里的 `chain`（用户配置的）与 `effective_chain`（实际会用的，代理感知裁剪后）和 `system_proxy_detected`；再单独指定 `backend` 逐个试。想彻底不依赖第三方服务，就自建一个 SearXNG 并把 `backend_chain` 改成 `["searxng"]`。

## 开发

当前目录既是插件源码，也是它自己的 Git 仓库。发版到插件市场时，GitHub 仓库名必须是：

```text
n.e.k.o_plugin_better_web_search
```

> **一个字母都不能大写。** 命令行侧的比对是 `casefold()` 的（`release_cmd.py:231`），所以写作
> `n.e.k.o_plugin_Better_web_search` 在本地自检里也过得去；但市场**投稿页区分大小写**——它取
> `n.e.k.o_plugin_` 后面那一段当插件 id 套小写规则（这条提示语不在宿主仓库里，是网站前端的行为）。
> 2026-09-20 投稿时被这一步拦下，于是把 GitHub 仓库名改成了现在这个全小写形式；提交历史、检查记录
> 和旧地址跳转都保留，本地只需 `git remote set-url` 跟上。

在本仓库根目录执行（本仓库与 `N.E.K.O` 是**同级**目录，所以宿主路径是 `../N.E.K.O`；`neko-plugin` 换成
`python -m plugin.neko_plugin_cli` 的原因见上文那条坑注）：

```bash
uv run --project "../N.E.K.O" python -m plugin.neko_plugin_cli check .
# 含 lint + 测试 + 构建 + 包校验；带 NO-BYTECODE 才能得到干净的包
PYTHONDONTWRITEBYTECODE=1 uv run --project "../N.E.K.O" python -m plugin.neko_plugin_cli check -r .
uvx ruff==0.12.4 check --ignore-noqa --config ruff.toml .
```

> ⚠️ **别写成 `--project "../../N.E.K.O"`**。在写这份文档的那台机器上，`../../N.E.K.O` **也存在**，
> 但它是一个早了将近一个月的宿主检出（提交 `fb78210a`，2026-08-21；同级那份是
> `a3c82b5a`，2026-09-17）。指向它的后果是上文那条坑：老 CLI 没有 entry 元数据探测步骤，构建照样
> `[OK]` 却不出 `plugin.meta.json`，面板按钮全部 404。判断标准是构建后自验包里有没有 `plugin.meta.json`，
> 而不是命令有没有报错。

单元测试是**离线**的（`tests/fixtures/` 里是真实响应结构，不联网）：

```bash
uv run --project "../N.E.K.O" python -m pytest -c tests/pytest.ini tests -q
```

面板 `ui/panel.tsx` **也能真类型检查**，不用装宿主的 `frontend/node_modules`（宿主那个
`check-hosted-tsx.mjs` 才需要）：临时装一个 typescript，拿宿主 SDK 里现成的 `.d.ts` 当声明文件即可。

```bash
mkdir -p /tmp/tscpkg && cd /tmp/tscpkg && npm install typescript@5.6 --no-fund --no-audit
# tsconfig 照抄宿主 plugin/sdk/hosted-ui/tsconfig.json 的 compilerOptions
# （jsx react + jsxFactory h + jsxFragmentFactory Fragment + paths @neko/plugin-ui），
# baseUrl 指到 <宿主>/plugin/sdk，files 带 globals.d.ts，include 指本仓库的 ui/panel.tsx
node /tmp/tscpkg/node_modules/typescript/bin/tsc -p /tmp/tsx-check/tsconfig.json
```

建议开 `noUnusedLocals`：JSX 结构改完之后，它能抓出多余的 import（比如说明搬进 Accordion 之后就
没人用的 `Tip`）。**这挡不住渲染问题**，版式仍然要人在真机看一遍。

> `-c tests/pytest.ini` 不能省：本仓库根目录就是插件包（有 `__init__.py`），pytest 8/9 会为 rootdir 到用例之间的每层目录建 Package 节点并去 import 根 `__init__.py`，而插件独立检出时它无法作为包被导入，全部用例会在 setup 阶段集体 CollectError。把 rootdir 收进 `tests/` 就没这个节点（与宿主 `plugin/tests/pytest.ini` 同一约定）。

四条门禁由测试自己把守，改的时候别绕开它们：`tests/test_config_keys.py` 要求代码里读到的每个配置键都在 `plugin.toml` 声明、且 `plugin.toml` 与 `config.example.toml` 键集一致；`tests/test_smoke.py` 要求中英 locale 键集完全相同、面板**可见**文案不超字数预算、`panel.tsx` 用到的每个 action id 真的是一条已声明入口、`plugin.toml` 与 `pyproject.toml` 版本号一致；`[plugin].version` 必须三段数字（`validate_cmd.py:181`，写成 `0.97` 直接 error）；宿主侧 `PLUGIN_EXECUTION_TIMEOUT = 30.0`、`PLUGIN_STARTUP_TIMEOUT = 10.0`（`plugin/settings.py:283,292`）决定了"入口 timeout ≤ 30、`total_timeout_seconds` 上限 28、**不要在 `startup()` 里做逐把网络探测**"。

> 别把这份 checkout 直接拷进 `N.E.K.O/plugin/plugins/`：宿主要求**模块段等于目录名**（`plugin/core/host.py:459-478`），而仓库名带着一个点（`n.e.k.o_plugin_better_web_search`）只是 Git 侧的约定。要么用打包产物，要么建一个无点的目录名。

结构：

```text
__init__.py      插件主体：四段配置、后端编排与代理感知裁剪、回退、对话入口、最近一次搜索记录、面板 entry、首启提示
_providers.py    exa / anysearch / bing / baidu / duckduckgo / sogou / searxng + 正文抓取（exa 支持密钥）
_parsing.py      零依赖 HTML 解析（结果卡片打分 + 正文线性化 + GBK 容错解码）
_net.py          标准库 HTTP + 代理策略
_resilience.py   缓存、并发合并、限流、失败退避（含 ApiKeyRejected / QuotaExhausted 错误）
_guard.py        fetch 的 SSRF 防护（allow_ranges 只对域名解析结果生效）
_host.py         宿主内置 web_search 的状态读取（只读；只走宿主公开回环 API，不含任何 start/stop）
_diagnose.py     网络自检编排（纯逻辑，probe 闭包由 __init__.py 注入）
ui/panel.tsx     面板：首屏是"内置还在跑吗"的检查页 + Tabs 三区（状态=最近一次搜索/来源链路/内置搜索状态 · 进阶=Exa 密钥 ·
                 AnySearch 密钥 · 账号提醒 · 代理兼容 · 诊断=网络自检），解释与注册教程一律放进默认收起的 Accordion，
                 首屏只留徽章、数字与主操作
docs/quickstart.md  接入教程
tests/           离线用例 + 真实响应夹具（**不进分发包**，见 pyproject 的 [tool.neko.build]）
docs/plan-*.md   施工单，内部文档（**不进分发包**）
```

面板 entry（actionId，全部对宿主 30s 看门狗留了预算）：`panel_context`、`save_exa_key`（参数 `api_key`，加一把）、
`remove_exa_key`（参数 `fingerprint`）、`clear_exa_key`、`test_exa_key`（逐把重测，20s 内多少算多少）、
`set_anysearch_key`（参数 `api_key`，存下并立刻真搜一次验货）、`test_anysearch_key`、`clear_anysearch_key`
（AnySearch 只有一把，没有环，所以只有"存/测/移除"三个动作）、`set_key_fallback`、
`get_host_search`（只读，本插件不代替用户开关内置搜索）、`diagnose_network`、
`set_ssrf_guard`。

## 发布到 Market

```bash
uv run --project "../N.E.K.O" python -m plugin.neko_plugin_cli publish .
```

先在 [Market 投稿页](https://market.project-neko.cn/#/upload) 用 GitHub 仓库地址提交一次审核，通过后这条命令会打 tag、等 GitHub Release、再通知 Market。`.github/workflows/release.yml` 会构建并上传 `better_web_search.neko-plugin`，Market 独立校验该 Release 后才上架。

发布前会**直接报 error** 的三条硬条件（前两条在 `plugin/neko_plugin_cli/commands/release_cmd.py`，
第三条在 `validate_cmd.py:181`）：

| 条件 | 代码位置 | 本仓库现状 |
| --- | --- | --- |
| git origin 的仓库名必须是 `n.e.k.o_plugin_<插件 id>`，**且全小写** | `release_cmd.py:230-232`（命令行比对用 `casefold()`，投稿页比对区分大小写） | ✅ 2026-09-20 已把 GitHub 仓库名改成 `n.e.k.o_plugin_better_web_search`，本地 `git remote` 同步跟上 |
| tag 去掉 `v` 前缀后必须等于 `plugin.toml` 的 `version` | `release_cmd.py:237-239` | ✅ 当前 `plugin.toml` 是 `0.9.7`，要打的 tag 是 `v0.9.7`；`tests/test_smoke.py::test_release_version_is_stated_once` 保证 `plugin.toml` 与 `pyproject.toml` 不打架 |
| `[plugin].version` 必须至少三段数字 | `validate_cmd.py:181`（`^\d+\.\d+\.\d+.*$`） | ✅ 写成 `0.97` 会直接 error，所以这里是 `0.9.7` |

> 代码侧的发布条件都已验证过，剩下的只有"打 tag + 投稿 Market"这一步人工动作：`v0.9.6` 是本仓库第一个真正
> 打上 tag 的版本（tag 在 `origin/main` 上）。发新版前照例确认推送的那棵树就是你要发的那一棵。

## Entry

```toml
entry = "plugin.plugins.better_web_search:BetterWebSearchPlugin"
```

插件被安装到用户目录后，宿主会把 `plugin.plugins.*` 前缀改写成 `plugins.*` 再加载，因此开发时（挂在 N.E.K.O 源码树里）和安装后（在用户插件目录）用同一个 entry 字符串即可。
