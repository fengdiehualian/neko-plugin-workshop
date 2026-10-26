# v0.2 发行前改造方案（free_web_search）

> 本文件是给实现者（人/子代理）的施工单。所有"宿主事实"均已核对到 `路径:行号`，不要凭印象改。
> 每个工作包**只允许改自己名下的文件**；跨模块只能通过本文件第 3 节的接缝接口调用。

> **v1.0.0 作废项（写于 2026-09-21，当时编号还是 0.9.8）**：本文件里所有"内置搜索双向开关"相关内容（§0 的 start/stop 往返结论、
> §1.5 的开关、§3 接缝里的 `set_host_search` 与 `host_search.toggleable`、`[host].takeover_search`、
> §5 验收第 5 条、§7 的"待人工"项）**已全部作废，不要照此重新实现**。插件不再向宿主写任何东西：
> 那条 POST 在宿主重载插件列表时要 8 秒以上才回话，晚于客户端超时，真机 v0.2.0~v0.9.6 的 19 次启动
> 记录的"失败"无一次是真失败；改成后台 20 秒核对后又变成"确认一次就撒手"，内置被启回来就没人管。
> 现在 `_host.py` 只读 `GET /plugin/status`，停用由用户去宿主「插件中心」完成。
> 下面保留原文，因为它记录了宿主接口的真实行为，读状态时仍然用得上。

> **v1.0.0 作废项之二（2026-09-22）**：§3 接缝里的 `set_onboarding` / `show_guide` 与配置项
> `[ui].onboarding_stage`（"面板记住引导走到哪一步"那套）**已整体撤掉，不要照此重新实现**。
> 首屏现在由一个客观事实决定——宿主的 `web_search` 还在不在跑（`GET /plugin/status` 读来的），
> 在跑就显示检查页、停了就不再弹、读不到不算在跑；三步注册教程搬进「进阶」页两张密钥卡各自的
> 折叠块，配好之后自动收起。唯一保留的进度记录是 `[ui].first_run_notice_sent`（首启聊天提示只发一次）。

## 0. 已实测事实（施工依据）

测试环境：本机 mainland 直连，系统代理关闭（本地有代理进程在跑但未接入），
`urllib.request.getproxies() == {}`，等价于"用户没开代理"。

| 后端 | 直连 | 走本地代理 | 结论 |
| --- | --- | --- | --- |
| exa `mcp.exa.ai` | ✅ 1.6–3.5s（simple 1.6s / advanced 3.7–11.0s） | ✅ 1.0s | 主路径，匿名可用 |
| anysearch `api.anysearch.com` | ✅ 1.2–5.0s | ✅ | 匿名可用 |
| bing | ✅ **0.6s**，中英皆优 | ✅ | 应进默认链路 |
| baidu | ❌ `HTTP 200` + `<title>百度安全验证</title>`（1438B） | ❌ | 需 BAIDUID 预热才有救 |
| sogou | ❌ 无可解析结果 | ❌ | 不进链路 |
| duckduckgo | ❌ DNS 污染 → 超时 | ❌ SSL EOF | 只在有代理时启用 |

Exa key 事实（官方文档 + 用真 key 实测）：

- Free plan = 注册送 **$20**，之后**每月刷新 $10 额度**，10 QPS。`/search` 单价 **$7/1k**，
  实测 `costDollars.total = 0.007` 且 numResults 2/6/10 同价 ⇒ **$10/月 ≈ 1428 次 ≈ 47 次/天**。
- 注册入口 `https://dashboard.exa.ai/api-keys`；国内**直连可达**：
  `exa.ai` 0.17s / `auth.exa.ai` 0.38s / `dashboard.exa.ai` 0.23s / `api.exa.ai` 0.42s。
  登录方式 **Continue with Google / Email** ⇒ 引导只能主推 **Email 注册**（Google 国内不可用）。
- key 三种传法实测等效：`?exaApiKey=`、`x-api-key` header、`Authorization: Bearer`。
  **一律用 `x-api-key` header**（key 不进 URL、不进访问日志）。
- ⚠️ **坏 key 不返回 HTTP 401**：MCP 返回 `HTTP 200` + `result.isError=true` +
  文本 `"web_search_exa error (401): Invalid API key"`。现有 `_mcp_call`（`_providers.py:248-262`）
  只看 JSON-RPC 层的 `payload["error"]`，**看不到 `result.isError`** ⇒ 坏 key 会被误报成
  "Exa 未返回可解析结果"并静默跳过后端。必须修。
- `tools/list` 带 key 与匿名**完全相同**（只有 `web_search_exa`、`web_fetch_exa`），
  但 `web_search_advanced_exa` 两种身份都能调 ⇒ 带 key 买到的是额度确定性 + advanced 稳定性，不是更多工具。
- 额度用尽返回 `402 Payment Required`（官方 billing 文档）。

宿主事实（已核对）：

- 内置插件 `web_search` **可以被停**（只有卸载被禁：`PLUGIN_UNINSTALL_BUILTIN_FORBIDDEN`）。
- 停/启 = `POST {USER_PLUGIN_BASE}/plugin/web_search/stop|start`，`USER_PLUGIN_BASE=http://127.0.0.1:48916`
  （`config/network.py:165-168`），**免鉴权**（`plugin/server/infrastructure/auth.py:21-27` 是空实现）。
  插件内回环 HTTP 已有先例：`plugin/plugins/qq_auto_reply/__init__.py:252-268`。
- `stop` 会持久化 `enabled=false + auto_start=false` 到
  `%LOCALAPPDATA%\N.E.K.O\config\plugin_runtime_overrides.json`（`runtime_overrides.py:28,148,226`）。
- **停用后仍能被显式 `/start` 拉起来**：`lifecycle_service.py:1044-1053` 在
  `persist_user_intent=True` 时先把 `enabled_value` 置 True；已在跑时 `/start` 幂等成功
  （`lifecycle_service.py:889-897`）。⇒ **双向开关成立**。
- 坑：对**没在跑**的插件调 `stop` → `404 PLUGIN_NOT_RUNNING`（`lifecycle_service.py:1403-1410`），
  必须当幂等成功处理；宿主**没有**"恢复默认偏好"端点。
- 读状态用 `GET {USER_PLUGIN_BASE}/plugin/status?plugin_id=web_search`（`routes/plugins.py:37`）。
- 插件写自己配置：`await self.config.update({"search": {...}})` 深合并 + 原子落盘
  （`sdk/shared/core/config.py:215` → `core/context.py:2098` → `config_updates.py:275`），
  **不触发 `config_change`** ⇒ 写完必须自己 reload（先例：`plugins/lifekit/__init__.py:531-532`）。
- 面板：`[[plugin.ui.panel]] entry="ui/panel.tsx"`（`.tsx`→hosted-tsx，`.md`→markdown，
  `.html`→static，`ui_manifest.py:217-227`）；声明 panel 会自动给插件卡片加 `open_panel`
  动作 → `/plugins/<id>?tab=panel`（`ui_query_service.py:1237-1241`）。
  面板里用 `props.api.call(actionId, args)` + `props.api.refresh()`
  （`plugins/lifekit/ui/panel.tsx:120-177`），`actionId` = `@plugin_entry` 的 id（`@ui.action` 只是加元数据）。
  **宿主没有"安装后自动打开面板"**，所以"自动弹引导"= 面板首帧即引导 + `push_message` 让猫娘开口。
- 日志**不脱敏**（`plugin/logging_config.py:174-185` 的 `REDACT_PATTERNS` 全仓零调用），
  且 `core/context.py:703` 会把整个 status payload 用 INFO 打出来 ⇒ **key 绝不能进 `report_status`
  /日志/面板回显**，对外一律掩码 `exa****尾4位`。

## 1. 已定决策

1. 默认链路 → `exa → anysearch → bing → baidu`；`duckduckgo` 移出默认链路，
   **仅在检测到系统/环境/显式代理时才进链路**。
2. exa 用哪个工具：**已由实测推翻原决策**。原计划"有 key 用 advanced"，但带 key 实测
   `web_search_advanced_exa` = 3.95s / **11.70s**（两个样本），匿名 = 3.67s / **11.03s** ——
   抖动来自 advanced 自己抓页面，**与是否带 key 无关**，11.7s 已顶穿单后端 12s 预算。
   ⇒ `exa_tool="auto"` **恒用 `web_search_exa`**（0.9–3.3s）；`advanced` 保留为显式选项。
   key 买到的是额度确定性与 402 行为，不是速度。
3. 坏 key / 额度耗尽：**明确提示 + 自动回落匿名档**（本次降级并记一次告警，不每次骚扰）。
4. TUN+fake-ip：默认放行 `198.18.0.0/15`，且只对"域名解析结果"放行；面板提供一键开关，用户不需要懂 CIDR。
5. 宿主内置搜索：插件面板里做**真·双向开关**（含状态显示）。
6. 新手引导**只做在面板里**（不做对话录入）：注册地址/步骤 → 粘贴 key → 立即保存并测试；
   有「先体验」按钮（跳过、用免费额度）；引导可随时从面板重新呼起；key 可在面板管理（替换/删除/测试）。
7. 加 `diagnose` 自检入口（消耗额度，仅用户显式要求时调）。

## 2. 工作包与文件所有权（互不重叠，可并行）

| 包 | 拥有文件（只能改这些） | 内容 |
| --- | --- | --- |
| **W1 后端层** | `_providers.py`, `_parsing.py`, `_resilience.py`, `tests/test_providers.py`, `tests/test_parsing.py`, `tests/test_resilience.py` | exa key 支持、`result.isError` 分类、baidu BAIDUID 预热、bing 选择器复核、新错误类型 |
| **W2 SSRF** | `_guard.py`, `tests/test_guard.py`(新) | `allow_ranges` 参数 + 只对 DNS 结果放行 |
| **W3 宿主控制** | `_host.py`(新), `tests/test_host.py`(新) | 状态读取/停/启，幂等，超时与错误归一 |
| **W4 面板+文案** | `ui/panel.tsx`(新), `docs/quickstart.md`(新), `i18n/zh-CN.json`, `i18n/en.json` | 引导三步、先体验、key 管理、内置搜索开关、诊断按钮 |
| **W5 集成** | `__init__.py`, `plugin.toml`, `config.example.toml`, `README.md` | 新配置项、新 entry、链路裁剪、超时预算、把 W1-W4 缝起来 |
| **W6 自检** | `_diagnose.py`(新), `tests/test_diagnose.py`(新) | 直连/代理双测、推荐链路、话术 |

W5 由主控（我）实现，必须等 W1/W2/W3/W6 的接缝落地后进行；W4 可与 W5 并行（只依赖第 3 节契约）。

## 3. 接缝契约（写死，任何包不得单方面改动；要改先改本文件）

```python
# _resilience.py (W1) —— 新增两个类型，均从 SearchProviderError 派生以便被现有 except 捕获
class ApiKeyRejectedError(SearchProviderError): ...   # key 无效/失效（MCP isError 里的 401/403 文案，或 HTTP 401/403 且已带 key）
class QuotaExhaustedError(SearchProviderError):       # 额度耗尽：仅 HTTP 402（或文案里的 402/quota/credit）。
                                                      # HTTP 429 不算：Exa 用 402 表示没钱、用 429 表示问得太快，
                                                      # 带 key 的 429 归 BlockedError（后端退避，不冤枉这把钥匙）
    retry_after_seconds: float | None

# _providers.py (W1)
def search_exa(query, limit, *, timeout, policy, proxy_url, live_crawl=False,
               api_key="", tool="auto") -> list[dict]: ...
#   tool ∈ {"auto","advanced","simple"}；auto 恒为 simple（见 §1.2 实测修正）
def fetch_exa(url, *, timeout, policy, proxy_url, max_chars, api_key="") -> dict: ...
def search_baidu(...)  # 内部：无 BAIDUID cookie 时先 GET https://www.baidu.com/ 领 cookie，
                       # 再打 /s；仍拿到"百度安全验证"则抛 BlockedError（触发冷却），不复用现有一次性失败
def _mcp_call(tool, arguments, *, timeout, policy, proxy_url, api_key="") -> dict
#   必须：(a) api_key 非空时加 headers["x-api-key"]; (b) 解析 result.isError=true 的文本，
#         命中 Invalid API key / 401 / 403 → ApiKeyRejectedError；402/quota/exhausted → QuotaExhaustedError;
#         其它 isError → SearchProviderError(原文截断 200)

# _net.py (W5，主控改，不在 W1 范围)  —— 无新增

# _guard.py (W2)
def normalize_http_url(raw, *, allow_ranges: Sequence[str] = ()) -> str
#   allow_ranges 只在"域名 getaddrinfo 结果"分支生效；字面 IP 目标、metadata、*.local、
#   .internal 等一律照旧拒绝。非法 CIDR 忽略并返回时不抛。
def is_http_url(raw, *, allow_ranges=()) -> bool

# _host.py (W3)
@dataclass
class HostPluginState: plugin_id: str; running: bool; exists: bool; raw: dict
class HostPluginControl:
    def __init__(self, base_url: str = "", timeout: float = 4.0): ...   # 默认解析 USER_PLUGIN_BASE，失败回落 http://127.0.0.1:48916
    def status(self, plugin_id: str) -> HostPluginState: ...            # GET /plugin/status?plugin_id=
    def set_enabled(self, plugin_id: str, enabled: bool) -> tuple[bool, str]  # → (ok, 中文说明)
    #   enabled=False → POST /plugin/{id}/stop；404 PLUGIN_NOT_RUNNING 视为成功（已停）
    #   enabled=True  → POST /plugin/{id}/start；已运行(响应含 "already running") 视为成功
    #   仅允许 plugin_id ∈ {"web_search"}（模块常量 ALLOWED_IDS），其它抛 ValueError

# _diagnose.py (W6)
def run(probes: list[tuple[str, Callable[[], list[dict]]]], *, allow_proxy: bool) -> dict
#   返回 {"rows":[{"backend","direct","proxied","ms","note"}], "proxy_detected":bool,
#         "recommended_chain":[...], "summary":"可直接贴给用户的中文结论"}
#   每个 probe 独立超时、单个失败不影响其它行、总预算由调用方控制

# __init__.py (W5) —— 面板要调的 actionId 全集（都是 @plugin_entry id）
"panel_context"      # @ui.context(id="main")，返回下方 context 结构
"save_exa_key"       # args {key:str}        → 掩码 + 立刻试一次 search 验证；失败也要保存但标 invalid
"clear_exa_key"      # args {}               → 清空并 reload
"test_exa_key"       # args {}               → 只测不存；返回 {ok, message, latency_ms, count}
"set_host_search"    # args {enabled:bool}   → 调 W3；返回 {ok, message, running}
"get_host_search"    # args {}               → 读 W3 状态
"set_onboarding"     # args {stage:"done"|"trial"}  → 记 [ui].onboarding_stage，控制面板首帧
"show_guide"         # args {}               → 把 stage 重置成 "welcome"（面板里的"重新引导"）
"diagnose_network"   # args {with_proxy:bool}→ 调 W6；返回 {summary, rows, recommended_chain}
"set_ssrf_guard"     # args {enabled:bool} → 写 [net].ssrf_allow_ranges = 默认段 或 []；面板一键切，
                     #   不让小白手写 CIDR（§1.4 要求的“一键开关”，W4 验收时发现缺 actionId，集成补上）
"open_plugin_panel"  # 不实现（宿主没有该 API）→ 面板按钮文案改为"打开面板"由宿主卡片提供
```

### 面板 context 结构（`panel_context` 返回；key 一律掩码）

```json
{
  "onboarding_stage": "welcome|done|trial",
  "exa_key_masked": "exa****9b2c",
  "exa_key_source": "config|none",
  "exa_key_state": "unknown|valid|invalid",
  "exa_last_error": "" ,
  "chain": ["exa","anysearch","bing","baidu"],
  "effective_chain": ["exa","anysearch","bing"],
  "proxy_mode": "auto",
  "proxy_detected": false,
  "host_search": {"exists": true, "running": true, "toggleable": true},
  "ssrf_fake_ip": true,
  "quota_note": "每月刷新 $10 ≈ 1400 次；不填 key 也能用，但匿名档慢且限额低"
}
```

### 新配置项（W5 写进 `plugin.toml` + `config.example.toml`）

```toml
[search]
backend_chain = ["exa", "anysearch", "bing", "baidu"]   # 改默认
exa_api_key = ""            # 新增：面板写入
exa_tool = "auto"           # 新增：auto|advanced|simple
exa_key_fallback_anonymous = true   # 新增：坏 key/超额时本次回落匿名
baidu_warmup = true         # 新增：领 BAIDUID cookie
duckduckgo_needs_proxy = true  # 新增：无代理则 ddg 不进 effective chain
[net]
ssrf_allow_ranges = ["198.18.0.0/15"]   # 新增：fake-ip 放行（仅对解析结果）
[ui]
onboarding_stage = ""        # 新增：""|"welcome"|"done"|"trial"
first_run_notice_sent = false
[host]
takeover_search = false      # 新增：用户意图（startup 是否重申停用内置）
```

## 4. 验收标准
- 离线单测全绿：`uv run --project "../../N.E.K.O" python -m pytest tests -q`（不得新增联网测试）。
- `uvx ruff==0.12.4 check --ignore-noqa --config ruff.toml .` 干净。
- `PYTHONDONTWRITEBYTECODE=1 uv run --project "../../N.E.K.O" neko-plugin check -r .` 通过。
- 行为验收（本机，代理关闭）：  1. 默认配置下 `search` 首次调用命中 exa simple，**p50 < 2s**；
  2. 无代理时 `effective_chain` 不含 `duckduckgo`；手动设 `proxy=<url>` 后出现；
  3. 在面板存一个错 key ⇒ 明确返回"Exa 密钥无效"，并且**搜索仍然出结果**（匿名回落）；
  4. `fetch` 在 fake-ip 环境（monkeypatch `getaddrinfo` 返回 198.18.1.7）能放行，
     而 `http://127.0.0.1/`、`http://169.254.169.254/`、`http://[fd00::1]/`、`http://x.local/` 仍被拒；
  5. `set_host_search(enabled=false)` 后内置 `web_search` 进程消失且重启不自启；
     `set_host_search(true)` 能拉回（`/start` 会清掉 disabled 状态）。
- 安全：日志与 `report_status` 中 grep 不到 key 明文（只允许掩码形式）。

## 5. 明确不做

- 不硬编码任何 key、不硬编码开发机上的代理地址；发行默认值不得依赖开发机代理。
- 不引入第三方依赖（保持零 `vendor/`，见 README 构建说明）。
- 不改宿主源码（N.E.K.O 主仓库只读；开关走公开 HTTP 端点，不走"直接改宿主 JSON"）。
- 不做对话式录入 key（用户明确否决：不稳定）。
- 不新增宿主没有消费方的元数据（如 `quick_action`，全仓无消费方）。

## 6. 验收结果（真机，代理关闭，2026-09-18）

门禁：`pytest 202 passed` / `ruff@0.12.4 All checks passed` / `neko-plugin check 0 error`。
真机行为（走插件真实 entry，不 monkeypatch provider）：**19/19 通过**。

- §4.1 默认配置 `search` 命中 `exa`，快路径 **1.08–1.27s**（第二次 0.00s 是 300s 缓存命中）。
- §4.2 无代理 ⇒ `effective_chain = [exa, anysearch, bing, baidu]`（ddg 被裁）；伪造"检测到代理"后 ddg 回归。
- §4.3 存坏 key ⇒ 返回"Exa 判定这个密钥无效…"，掩码 `exa****beef`，**同一次搜索仍成功（匿名回落）**；
  断言了返回体、`panel_context`、日志 blob 里都搜不到明文 key。`clear` 后掩码为空。
- §4.4 fake-ip：`198.18.1.7` 解析结果放行；`127.0.0.1`、`169.254.169.254`、`[fd00::1]`、`x.local`、
  **字面** `198.18.1.7`、`192.168.1.1`、`2130706433`、`10.1.2.3` 全部仍拒。
- §4.5 `diagnose_network` 6.0s 跑完 5 个后端：exa/anysearch/bing/baidu 可用、ddg 超时；
  summary "不开代理就能用：4 个后端直连成功，最快 bing 约 0.7 秒"。
- §4.5 内置搜索双向开关：**待人工**（需要宿主在 127.0.0.1:48916 起来才能验 stop/start 往返）。

### 验收期间发现并修掉的两个真 bug

1. **单位不一致（面板会显示"1 ms"其实是 1.2 秒）**：`_diagnose._since()` 返回**秒**，
   却塞进字段名 `ms`，而 `ui/panel.tsx:latencyText` 按毫秒渲染。
   ⇒ 统一到毫秒（`_since` ×1000、`MEASURED_FLOOR = 1.0`、`_fmt_seconds` → `_seconds_text`），
   加 `_since` / 端到端 `rows[].ms` / summary 措辞三条回归测试。
2. **baidu 被自己的过滤规则误杀**：BAIDUID 预热其实**已经成功**（拿到 916KB 正常结果页，
   `c-container` ×99，卡片分数 75–85），但 `_is_unresolved_redirect` 把 `baidu.com/link?url=`
   一律当"打不开的壳"丢掉 ⇒ 8 条全丢，最后报"未返回可解析结果"。
   实测该壳是真 `302`（`Location: space.bilibili.com/...`）且 `_net` 默认跟随重定向，
   于是 baidu 从"永远无结果"变成 **725ms 可用**。JS 壳页仍由 `_check_block` 单独负责，两件事不再混为一谈。

### 一处被实测推翻的原决策

`exa_tool="auto"` 原本"有 key 用 advanced"。带 key 二次采样 advanced = **11.70s**（首次 3.95s），
说明抖动来自 advanced 自己抓页面、与 key 无关 ⇒ `auto` **恒用快路径**，advanced 只作显式选项。
施工单 §1.2 / §3 / README 已同步。

### 集成期补的接缝

§1.4 要求"面板一键开关 fake-ip 白名单"，但 §3 从未定义对应 actionId，
W4 因此只能做成只读展示。补 `set_ssrf_guard{enabled}`（`__init__.py`）+ 面板 `Switch` + 中英 i18n 键。
§3 已把它写进契约表。
