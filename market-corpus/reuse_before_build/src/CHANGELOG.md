# Changelog

## 0.3.21 — 2026-11-16

收口 `rbb_core.py` 自己实现的两处章节切分：围栏里的示例标题曾把正文截断。

- **`skill_section` 改走 `extract_section`**：原先用 `(?ms)^##\s+<标题>\s*$\n(.*?)(?=^##\s+|\Z)` 手写切节，代码围栏里的 `## ` 行会被当成章节边界。现在交给随包校验器的 `extract_section`（用 `_fence_mask` 跳过围栏），与 `core_rules_block` 调 `extract_block` 同口径；保留「把标题拼回去」和「取不到返回空串、由调用方失败关闭」两个原有行为。
- **`flow_steps` 同样改为围栏安全切节**：标题扫描按 `_fence_mask` 过滤，正文用 `extract_section` 抽取；四步不齐或某步正文为空仍返回空列表。
- **实际影响**：`SKILL.md` 的 Step 4 在 ` ```markdown ` 围栏里嵌着 `## What I found` / `## Why this one` / `## What I will add (issue / PR plan)` / `## Alternatives and risks`，旧实现取到的 Step 4 正文在围栏开头就被截断，交付模板整段丢失。
- **`default_since` 不再接受日期作第一个参数**：旧实现遇到 `default_since(日期, today)` 会把 `today` 静默覆盖成那个日期、丢掉第二个参数；现在第一个参数只收天数，传日期在 `int()` 处直接报错。闰日回退防线保留，两处调用改为双参、断言值不变（行为变更：不再兼容旧的 `default_since(today)` 写法）。
- **新增回归**：`tests/test_fenced_sections.py` 锁定 Step 4 模板完整、不越界吞并下一节、缺章返回空串，并确认 `mandatory_rules` 仍取得到四个必需章节；另用内联 fixture（围栏里放一个 `## What I found`）直接验证切节不被围栏标题截断，不依赖 SKILL.md 当前写了什么。
- **计划废弃（先标记，待核实仓外无引用后执行）**：`rbb_core.manifest_settings()` 包装（`load_manifest_settings` 的单行别名，仓内零业务调用）、`rbb_core` / `repo_scan` 里的 `PERMISSIVE` 别名、`resolve_python` / `canonical_hash` / `verify_localized` 的 `python_exe` 注入通道。三项都是仓外可能 import 或调用过的公开面，本次不删，核实无外部依赖后再删并单列变更。
- **版本**：插件升 `0.3.21`；`verify_localized.py` 本次未改，版本保持 `1.2.15`。
- 测试 192 passed（另有 73 个子测试），unittest 147 tests，Ruff 全绿。

## 0.3.20 — 2026-11-15

按 `83043a9`（0.3.19）增量深审 A470 收口非基线关键章节条目的崩溃面：

- **非基线 `critical_sections` 条目**：`check_critical_sections` 现在对所有条目复用 `anchor_spec_errors`，`anchors` 不是列表、anchor 不是对象、`any` / `all` 不是非空字符串列表会报 `section:<id>:anchors...`，不再在 `.get` 抛栈。
- **`min_chars` 类型**：`int()` 捕获 `TypeError` / `ValueError` / `OverflowError`，坏值报 `section_floor:<id>(invalid)`，不再抛栈。
- **版本**：`verify_localized.py` 升 `1.2.15`，插件升 `0.3.20`；README 与 `docs/vendor.md` 示例同步。
- 测试 185 passed（另有 73 个子测试），unittest 140 tests，Ruff 全绿。

## 0.3.19 — 2026-11-15

按「v0.3.17 修复后的潜在坑」第二轮复查，收口契约内层字段的崩溃面：

- **关键章节 `headings` 形状**：`extract_section` 只接受字符串列表，`check_critical_sections` 对非列表 / 非对象 / 坏 `headings` 返回 `critical_sections(...)` 或 `section:<id>(bad_headings)`，不再在 `.strip()` 抛栈；契约基线在加载时同步报 `critical_sections_headings:<id>(invalid)`。
- **`block` 内层字段**：`contract_root_errors` 校验 `begin` / `end` / `lang_marker_prefix` / `canonical_file` / `canonical_sha256` 必须是字符串，`canonical_files` 必须是字符串列表；`block.begin: 1` 这类手工改坏现在报 `MISSING: block.begin(not_string)`，不再在 `re.escape` 抛栈。
- **`declaration` 形状**：`declaration` 必须是对象，`keys` 必须是字符串列表，`prefix` 必须是字符串；坏结构在入口直接报 contract 错。
- **`--print-section-hash` 防御**：坏 `critical_sections` / 坏 `headings` 现在打印 `MISSING ...` 并返回 1，不再抛 `AttributeError`。
- **版本**：`verify_localized.py` 升 `1.2.14`，插件升 `0.3.19`；README 与 `docs/vendor.md` 示例同步。
- 测试 185 passed（另有 73 个子测试），unittest 140 tests，Ruff 全绿。

## 0.3.18 — 2026-11-15

按《rbb-d261aa5-增量深审-A467》收口增量审核残留：

- **关键章节 `any` 补词边界**：`check_critical_sections` 的 `any` / `all` 都改用 `_anchor_present`，`activity` 不再命中正文里的 `inactivity`；删除只剩测试在用的 `_all_anchor_present` 别名，并补章节 `any` 回归测试。
- **`repo_scan.py` 行为变更补版本**：0.3.16 起 CLI 关键词去重已改大小写不敏感，补丁版本从 `1.2.8` 升到 `1.2.9`，测试同步。
- **契约顶层结构防御**：契约 JSON 顶层不是对象、或 `block` 不是对象时，CLI 直接报 `MISSING: contract(not_object)` / `block(not_object)`，不再在 `contract.get(...)` 处抛栈。
- **vendor 手册同步**：`docs/vendor.md` 写明 `any` / `all` 同规则（含关键章节）、ASCII 词边界、中文子串，以及坏契约在加载时报 contract 错。
- **版本**：`verify_localized.py` 升 `1.2.13`，插件升 `0.3.18`；README 与 `docs/vendor.md` 示例同步。
- 保留的已知取舍：`gh_json` / `auth_ok` / `scan` 仍按两侧路径校验与 host 参数分开实现；版本断言仍为硬编码常量，只防回退。
- 测试 184 passed（另有 73 个子测试），unittest 139 tests，Ruff 全绿。

## 0.3.17 — 2026-11-15

按「剩余项」复查再收口两处会误导排查的行为：

- **`check_anchors` 的 `any` 统一词边界**：`check_anchors` 的 `any` 与 `all` 共用 `_anchor_present`；ASCII 词按词边界匹配，中文等多字节文本仍按子串匹配，避免给中文硬套 `\b` 导致译本全红。关键章节的 `any` 在 0.3.18 单独补齐。
- **契约结构在加载时报错**：`check_contract_baseline` 校验 `rule_anchors` / `must_have` 的 `any` / `all` 必须是非空字符串列表；空串、`null`、非列表都在 CLI 入口直接报 contract 错，不再伪装成普通「缺锚点」。
- **版本**：`verify_localized.py` 升 `1.2.12`，插件升 `0.3.17`；README 与 `docs/vendor.md` 示例同步。
- 保持硬加载：`gh_helpers.py` / `redaction.py` 缺失仍在导入时失败，README 已写明必需文件；不做懒加载，避免把「插件起不来」变成更晚的「第一次检索才报错」。
- 测试 183 passed（另有 73 个子测试），unittest 138 tests，Ruff 全绿。

## 0.3.16 — 2026-11-15

按《A3-rbb-1f349eb-增量深审》收口可操作残留：

- **校验器版本**：`verify_localized.py` 的 `all` 锚点语义与报错格式已变，补丁版本从 `1.2.10` 升到 `1.2.11`，不再让下游无法按版本判断行为差异。
- **空锚点防御**：`_all_anchor_present` 对空字符串、非字符串直接返回 `False`，契约被改坏时不再抛 `IndexError` / `AttributeError`。
- **关键词去重区分大小写**：`gh_helpers.unique_keywords` 按 `casefold()` 去重，`Kw` / `kw` 只检索一次，保留首次写法；`repo_scan` 与 `rbb_core` 共用。
- **检索辅助继续收敛**：`default_since`、`activity_points`、`license_flags`、`md_cell`、`render_table`、`PERMISSIVE`、`AUTH_HINT` / `FAIL_HINT`、`GH_CREATIONFLAGS` 也移入 `gh_helpers.py`；0.3.15 的标题更正为「共用 5 个辅助函数」。
- **版本与文档**：插件版本升至 `0.3.16`，README 当前引用与 `docs/vendor.md` 示例同步。
- 仍保留的差异：`gh_json` / `auth_ok` / `scan` 因两侧路径校验与 host 参数不同，尚未合并；`any` 锚点继续按子串匹配（当前契约短词风险低）。
- 测试 182 passed（另有 73 个子测试），unittest 137 tests，Ruff 全绿。

## 0.3.15 — 2026-11-15

按《rbb-6900359-增量深审》修完全部可操作遗留项：

- **盘符根目录脱敏**：`redaction.py` 对 `C:`、`C:\`、`C:/` 等形式跳过 home 替换，避免全文本过度脱敏；已有 POSIX 根目录与极短路径保护不变。
- **CLI 检索前去重**：`repo_scan.py` 在关键词数量限制、gh 检索和统计之前按首次出现顺序去重；重复关键词只触发一次搜索。`rbb_core.py` 入口已有去重，本轮补真实 `gh_json` 调用层回归。
- **`all` 锚点更严格且可诊断**：失败信息只列出实际缺失词；ASCII 锚点按不区分大小写的词边界匹配，`rebuild` / `joined` 不再冒充 `Build` / `Join`，中文锚点保持子串语义。
- **检索辅助先共用 5 个函数**：新增随包 `gh_helpers.py`，插件内核与 CLI 共用 `which_path_only`、`validate_keyword`、`parse_search_output`、`search_args` 与 `normalize_search_row`；CLI、插件入口及 PATH 安全行为不变。
- **脚本与工作流版本**：`repo_scan.py` 补丁版本升至 `1.2.8`，`verify_localized.py` 在 0.3.15 发布时保持 `1.2.10`；离线测试工作流的 checkout / setup-python 固定到 vendor 工作流所用完整 SHA，官方 managed `verify.yml` / `release.yml` 的 `@main` 不变。
- **版本与文档**：插件版本升至 `0.3.15`，同步 README 当前引用与 `docs/vendor.md` 全部示例；README 历史版本记录拆为列表。规则不变块、契约哈希与 prompt 登记均未修改。
- 测试 180 passed（另有 49 个子测试），unittest 135 tests，Ruff 全绿。

## 0.3.14 — 2026-11-15

按《rbb-fc4b500-深审-1114225859》修深审新挖出的契约与运行时不一致，并收口其余可修项：

- **`all` 锚点运行时生效**：`check_critical_sections` 同时检查 `any` 与 `all`；删掉第 2 步任一判决词（如 `Fork`）会报 `section_anchor:step2:verdict_table`，新增负例测试。
- **契约基线钉全**：`rules_min` 固定为 5，`rule_anchors` / `must_have` 的 id 集合、`declaration.keys` 与 `within_last` 全部钉进代码；只改契约放宽会报 `rules_min:*` / `must_have_ids` / `declaration_keys`。
- **vendor 版本断言收窄**：测试改为抓取 `docs/vendor.md` 全部 `x.y.z`，断言都等于当前 `plugin_version`，不再只写死 `0.3.11`。
- **检索去重分叉**：`repo_scan.py` 合并重复关键词时去重，与 `rbb_core.py` 对齐；新增 CLI 回归测试，表里不再出现 `k, k`。
- **未知 section 显式报错**：`rbb_rules` 只接受 `rules/flow/full`，未知值返回 `Err`，不再静默回落。
- **根目录 home 不再过度脱敏**：`redaction.py` 在 home 为 `/` 或极短路径时跳过替换，避免把所有 `/` 变成 `~`；新增回归测试。
- **vendor 工作流固定 Action SHA**：`actions/checkout` 与 `actions/setup-python` 从 `@v4` / `@v5` 改为固定 commit SHA，避免可变 tag 替换。
- **校验器版本**：`__version__` 升至 `1.2.10`，测试同步。
- **版本同步**：`plugin.toml`、`pyproject.toml`、README 与 `docs/vendor.md` 当前版本升到 `0.3.14`；规则不变块与 `CANONICAL_SHA256` 未动；退出码 0/1/2 语义不变。
- 测试 174 passed（另有 33 个子测试），Ruff 全绿。

## 0.3.13 — 2026-11-15

按《rbb-4a14fbd-增量复核-1114224427》收口 `0.3.12` 的文档残留与契约硬化缺口：

- **vendor 文档旧版本号**：`docs/vendor.md` 第 1 节发布流程里的 `0.3.11` 改为 `0.3.13`；`test_vendor_guide_pins_neko_repository_version_and_target` 新增断言，vendor 文档不得再出现 `0.3.11`。
- **章节锚点钉进代码**：`verify_localized.py` 新增 `CANONICAL_SECTION_ANCHORS`；只改契约删掉某节锚点、换锚点或改锚点条件，会在 `check_contract_baseline` 启动检查时被拒绝。
- **校验器版本**：`__version__` 升至 `1.2.9`，测试同步。
- **版本同步**：`plugin.toml`、`pyproject.toml` 与 README 当前版本升到 `0.3.13`；规则不变块与 `CANONICAL_SHA256` 未动；退出码 0/1/2 语义不变。
- 测试 171 passed（另有 33 个子测试），Ruff 全绿。

## 0.3.12 — 2026-11-15

按《rbb-bd72178-增量核对-1114191452》修 v0.3.11 留下的文档与契约同步缺口：

- **vendor 文档补关键章节要求**：`docs/vendor.md` 第 7 节新增第 5 条，明确 Step 2 / Step 3 / Hard constraints / Untrusted repository content 的标题、500/500/350/180 字符下限、章节锚点与 `section_short` / `section_anchor` 报错；并用 `--print-section-hash` 重算章节哈希。
- **章节哈希钉进代码**：`verify_localized.py` 新增 `CANONICAL_SECTION_SHA256` 与 `CANONICAL_SECTION_FLOORS`；只改契约删掉 `critical_sections`、降低长度或换掉哈希都会在启动时被拒绝。
- **新增 `--print-section-hash`**：可一次打印四个关键章节的正文 SHA-256，避免升级 SKILL.md 时手算。
- **校验器文件头与版本同步**：由“四道闸”改为五道，`__version__` 升到 `1.2.8`。
- **Build 快速档允许门禁行在前**：先写 `不可复用原因：…` 再写“未找到同类项目；检索了…”不再掉进完整档并误报缺四段；门禁原因为空仍报 `gate_reason`。
- **缺章报错文案补齐**：`rbb_rules` 的 Err 现在同时提到 `RBB:INVARIANTS`、Step 2/3、Hard constraints、Untrusted repository content。
- **README 与版本记录**：补 0.3.11 / 0.3.12 记录，`plugin.toml`、`pyproject.toml` 与 README 同步到 0.3.12。
- 规则不变块与 `CANONICAL_SHA256` 未动；退出码 0/1/2 语义不变。测试 171 passed（另有 33 个子测试），Ruff 全绿。

## 0.3.11 — 2026-11-15

按《neko-reuse-before-build_增量复审_对照_172》修当前代码里的残留与新增问题：

- **Build 不再被逼编可改性**：`check_deliverable` 在 `verdict=Build` 且候选表没有数据行时，允许省略可改性档位并写 note；有候选表时仍要求绿/黄/红。
- **快速档补“没命中”形态**：首行为 `未找到 / 没找到 / No match` 等、且不超过三行时按快速档处理；必须至少提到一次检索来源，否则报 `quick:searches`，不再误落完整档四段模板。
- **必需章节缺失即 Err**：`mandatory_rules()` 任一核心章节抽不到就返回空串，由 `rbb_rules` 返回 Err，不再静默少一块硬约束。
- **脱敏覆盖 JSON 字段与 Windows 路径**：字段名允许带引号（`{"access_token":"…"}` 也命中），home 路径按大小写与 `/`、`\`、双反斜杠变体归一替换。
- **关键章节不再只靠子串**：`localization.contract.json` 新增 `critical_sections`（Step 2 / Step 3 / Hard constraints / Untrusted）；英文正典比正文 SHA-256，本地化副本比最小长度 + 多锚点，保留一句再删正文会报 `section_short`。
- **vendor 说明**：继续明确 `SHA256SUMS.txt` 是完整性清单、不是签名；下游应在 vendor 目录之外另行记录 ZIP 的 SHA-256。`attest-build-provenance` 属发布工作流可选增强，本版未改 workflow。
- 版本升至 0.3.11；规则不变块与契约基线哈希未动。

## 0.3.10 — 2026-11-14

- **下游 vendor 文档补齐**：在原 1–4 节后新增非 N.E.K.O 仓库接入、CI 校验、扩展／本地化副本三节，并补充升级复检流程；示例改用本版 tag 与 ZIP 文件名。
- **发布附件同步**：本版 vendor ZIP 纳入上述 `docs/vendor.md`，避免文档已推送但发布包仍停留在旧版。`v0.3.9` 及其附件保持原样。
- 规则不变块、契约哈希、脚本逻辑和提示词登记均未修改。

## 0.3.9 — 2026-11-14

按《rbb 补深审》逐项核对 v0.3.7 报告与当前实现，只修改 N.E.K.O 适配版：

- **交付契约补齐**：完整档兼容正典英文括号后缀、`Alternatives and risks` 与中文「我要加的东西」；必须给出可改性档位和结构化 `verdict / modifiability / lane`，红、黄档 Join 明确拒绝。Build 门禁只认首个非空行且原因不得为空；快速档支持中英 `Reused / Change / Risk`、`复用 / 改动 / 风险`。
- **默认提示不再漏硬约束**：`rbb_rules` 与流程直接抽取正典正文，包含六维评分、可改性硬闸、不可信仓库内容、分支纪律、薄适配层与逐次公开写确认；四段模板、四个 worked examples 与中英文粘贴提示词均可通过自家检查。
- **本地化校验加固**：代码内置正典与契约 SHA-256 基线，随包英文加语言标记也不能冒充翻译版；契约新增六项全局锚点，支持 `~~~` 围栏，导入模块不再改宿主 stdout/stderr 编码。
- **进程与预算加固**：Windows PATH 结果再次拒绝相对路径和当前目录，优先 `gh.exe`；gh 子进程隐藏控制台窗口；限流只在剩余预算内退避 2 秒并重试一次；找不到 Python 时不再回退裸名。
- **测试与发布工程**：新增深审负例回归；随包测试改名 `test_skill_pack.py`，修复独立收集、SDK 桩与断网问题；新增独立 Offline regression tests 工作流，官方 managed `verify.yml` / `release.yml` 保持原样。
- **元数据与文档**：版本升至 0.3.9，短描述移除更新说明，收敛宽泛触发关键词；同步 README、使用说明、中英文规则与 vendor 文档。
- 测试：160 passed（另有 20 个子测试），Ruff 全绿；核心不变块哈希 `fa0f991f…bc93` 未动。

## 0.3.8 — 2026-11-14

按复查截图第一组 `rbb（快门）` 的四项建议，只修改 N.E.K.O 适配版：

- **脱敏只剩一份实现**：新增随包 `scripts/redaction.py`，`rbb_core.py` 与 `repo_scan.py` 都按文件位置加载同一模块；URL userinfo、Cookie / Authorization 头、GitHub token、Bearer、口令与 home 路径共用同一组规则和契约测试，不再各自维护正则。
- **PATH 未命中不再退回裸 `gh`**：`command_path()` 只返回 `which_path_only()` 解析出的绝对路径；找不到时 `gh_json()` / `auth_ok()` 在启动子进程前就返回不可用，Windows 不会借机执行当前目录的 `gh.exe`。v0.3.7 已接受的合法绝对路径仍然可用。
- **版本定义去重**：`verify_localized.py` 删除旧的 `__version__ = "1.2.6"`，只保留与上游技能本体对应的 `1.2.7`；N.E.K.O 插件版本独立升为 `0.3.8`。
- **可复现 vendor 发布**：新增 `scripts/vendor_release.py`，固定 ZIP 文件顺序、时间戳、权限和压缩级别；tag 必须与 `plugin.toml` 版本一致。ZIP 内附 `VENDOR-SHA256SUMS.txt`，Release 另附覆盖插件包、vendor ZIP、校验工具与证明的 `SHA256SUMS.txt`。
- **接入文档**：新增 `docs/vendor.md`，明确只用于本 N.E.K.O 仓库，给出固定 tag、下载校验、目标目录 `plugin/plugins/reuse_before_build`、复制后复检与升级约定。
- **发布顺序**：官方 managed `release.yml` 保持原样；独立 `Vendor Source Release` 工作流通过 `workflow_run` 等待官方 Release 完整成功，再从同一发布提交构建、复检并上传附件，不影响市场发布门禁。
- **回归测试**：覆盖两个调用方脱敏一致性、PATH 未命中不启动 gh、合法绝对路径、单一版本定义、重复构建字节一致、文件篡改、额外文件、路径穿越、复制后漂移与 Release 哈希不匹配。
- 测试：100 passed（另有 4 个脱敏子测试），Ruff 全绿；规则本体与契约哈希 `fa0f991f…bc93` 未动。

## 0.3.7 — 2026-11-14

修一个会让 `rbb_scan` 在本机永远返回 `code=2` 的白名单自锁：

- **根因**：`search_candidates()` 先用 `resolve_gh_path()` 把 `gh` 解析成绝对路径，再传给 `auth_ok()` / `_scan_one()`；但 `auth_ok()` 内部重新走 `command_path()`，而当时 `command_path()` 只接受字面量 `gh` / `gh.exe`，于是把自家刚解析出的绝对路径判为非法，返回“gh_binary 非法：只允许 gh / gh.exe”。默认配置也会中招，和用户是否把 gh 装在 PATH 里无关。
- **修法**：`command_path()` 现在接受“由当前 PATH 解析出的同一个绝对路径”，并仍拒绝任意路径：basename 必须是 `gh` / `gh.exe`，且 `realpath` 必须与 `which_path_only()` 的结果一致；其他绝对路径（包括指向 `curl.exe` 等）继续拒绝。
- **回归测试**：新增 `test_command_path_accepts_its_own_resolved_absolute_gh`、`test_command_path_rejects_unrelated_absolute_gh`，以及不 mock `auth_ok` 的 `test_search_candidates_absolute_gh_path_survives_auth`，防止只 mock 登录检查掩盖真实链路。
- **实测**：修复前 `search_candidates(['react'])` 返回 `code=2 / gh_binary 非法`；修复后 `rbb_scan` 真实返回 `Ok(code=0, count=3)`，并列出 `react/react` 等 3 个候选。技能仓 `repo_scan.py` 不受影响，同参数本地跑出 3 个候选、退出码 0。
- 测试：83 passed（离线 70 + SDK 桩 10 + 市场 smoke 3），`ruff check .` 全绿。
- 规则本体与契约哈希 `fa0f991f…bc93` 未动。

## 0.3.6 — 2026-11-14

按第四轮深审（7.5 / 10）修的 A/B、中 1–7 与轻量项：

- **A · 中文判决词不再全文子串匹配**：`check_deliverable` 只从 `结论：Join/Extend/Fork/Build`（含 `加入/扩展/分叉/新建`）这类结论行取判决；正文出现「新建文件」「加入上游」不再被当成结论。没有结论行时只对 ASCII 判决词做整词退化判定，中文常见词不做全文扫描。
- **B · 预算耗尽单独报 `code=4`**：`skipped` 与 `failed` 分开记录；全部关键词因总预算用完未检索时返回 `code=4` + `BUDGET_HINT`，不再误报 `code=3`「全部检索失败」，也不再输出「0 命中」提示。部分跳过时 `code=0` 并在 note 里说明。
- **中 1 · Build 门禁识别 Markdown 装饰**：`**不可复用原因：**`、`> 不可复用原因`、`## 不可复用原因` 现在都能命中；门禁仍只认行首。
- **中 2 · 多结论行取最后一条**：备选段先写 `结论：Build` 不会再盖掉最终的 `结论：Join`。
- **中 3 · 四段标题按标题行匹配**：`## 找到了什么`、`**找到了什么**`、`### 1. 找到了什么`、`> ### 备选与风险` 都算；正文里包含同名短语不算。
- **中 4 · 关键词校验**：拒绝空、>200 字符、NUL/控制字符与 `stars:/pushed:/archived:/user:/org:` 等限定符；`_scan_one` / `gh_json` 把 `ValueError` 转成可读错误。
- **中 5 · 超时预算**：`rbb_scan` 新增 `timeout`（5–300 秒）并透传到内核，外层 `wait_for(total + 5)`；`rbb_status` / `self_check` 的哈希、校验、gh 检查改为并行，不再串行等三份超时。
- **中 6 · `resolve_python` 不认嵌入式宿主**：只有当 `sys.executable` 的文件名像 Python（`python*` / `py`）时才使用，否则从 PATH 找真解释器。
- **中 7 · gh 绝对路径**：新增 `which_path_only()`，过滤 PATH 空条目与当前目录，检索只把解析出的绝对路径交给 subprocess；`stars:` / `pushed:` 改为 `>=` 闭区间。
- **轻量项**：`rbb_scan` 的 `stars=0` / `limit=0` 不再被 `or` 当成没传；`on_shutdown` 取消后台哈希任务；`rbb_rules` 在规则块为空时返回 Err；`updated_since` 只接受严格 `YYYY-MM-DD`（`20250101` 会回退并写 note）；围栏解析统一复用随包 `verify_localized.py`，不再维护第二份。
- **文档**：删除「哈希防篡改」的暗示，明确哈希只覆盖不变块，保留锚点的重写仍需人读 `kept / changed`。
- **同步上游 v1.2.7**：`SKILL.md`、`references/repo-triage.md` 与三份 `scripts/` 对齐已发布的 v1.2.7（判定表、Untrusted repository content、契约锁、`--timeout` 等）。
- 测试：80 passed（离线 67 + SDK 桩 10 + 市场 smoke 3），`ruff check .` 全绿。新增结论行/正文误命中、Markdown 门禁、多结论、标题行、NUL/限定符/超长关键词、预算跳过、绝对 gh 路径、`ValueError`、紧凑日期、嵌入式 Python、`stars=0` 与工具 timeout 透传等回归。
- 规则本体与契约哈希 `fa0f991f…bc93` 未动。

## 0.3.5 — 2026-11-14

按第三轮复查（8.3 / 10）补的三处 + 新问题：

- **`gh` 白名单只剩 `gh` / `gh.exe`**：`resolve_gh_executable` 不再接受 `.bat` / `.cmd` 或任意路径；Windows 下 `.bat` 会被 `cmd.exe` 接管，关键词里的 `&`、`%` 可能被解释。配置与直调两条路径现在用同一份白名单。
- **登录检查不再吃光总预算**：`auth_ok` 只拿 `min(10s, 总预算 × 40%)`，剩余时间留给关键词；auth 超时/失败统一返回 `code=2`，并明确写「`gh auth status` 用完了总时限」，不再把超时误报成 `code=3` 的「gh 坏了」。
- **脱敏补齐**：`parse_search_output` 的坏 JSON 片段、`verify_localized` 的 stdout、`canonical_hash` 的 stdout 都过 `redact_text`；正则新增 `Bearer`、`api_key`、`access_token`、`Authorization`。
- **直调参数夹取**：`stars` 夹到 0..1,000,000，`limit` 夹到 1..10，`updated_days` 夹到 1..3650；`updated_since` 必须是 `YYYY-MM-DD`，非法时回退最近 N 天并写进 `note`。
- **`pushedAt` 非字符串**：`activity_points` 先判 `isinstance(str)`，并捕获 `AttributeError`，数字/列表不再让 `.replace` 崩。
- **Windows 只有 `py` 启动器**：`resolve_python` 按 `sys.executable` → `python` → `python3` → `py` 的顺序找解释器，`canonical_hash` / `verify_localized` 共用。
- **随包 CLI**：同一套参数校验（`--stars` / `--limit` / `--updated-since`）与脱敏；主仓 `scripts/repo_scan.py` 同步。
- 测试：66 passed（离线 54 + SDK 桩 9 + 市场 smoke 3），`ruff check .` 全绿。
- 规则本体、契约哈希 `fa0f991f…bc93`、四段提示词 sha256 未动。

## 0.3.4 — 2026-11-14

按第二轮第三方复查（8 条问题 + 3 条优先项）修的加固：

- **异常不漏**：`gh_json` / `auth_ok` / `canonical_hash` / `verify_localized` 统一捕获 `OSError`（含 `PermissionError`），`_scan_one` 捕获 `OSError` + `RuntimeError`；子进程启动失败不再穿透到工具层。
- **`scan_timeout` 真正生效**：整次 `rbb_scan` 共享一份总预算（登录检查 + 所有关键词），每个关键词只拿到剩余时间；哈希/校验也走同一个超时参数，不再写死 30/60 秒。
- **`gh_binary` 收敛**：配置只允许 `gh` / `gh.exe`，其它值回退 `gh` 并在 `settings.warning` 说明；库调用仍可注入文件名必须是 gh 系列的测试替身，非法名字直接按「gh 不可用」处理。
- **`extra` 白名单**：`verify_localized` 只接受 `--quiet`，`--canonical` 等可跳过声明检查的开关不能由模型侧注入。
- **结论词边界**：`check_deliverable` 的 ASCII 结论词改整词匹配，`rebuild` / `building` 不再误命中 `Build`。
- **去重键**：`url` 与 `full_name` 都空的行直接丢弃，不再把互不相关的仓合并成一条。
- **无时区时间**：`activity_points` 把 `now - pushed` 一起纳入 `try`，`naive` 时间抛 `TypeError` 时返回 0 而不是崩。
- **stderr 脱敏**：回给模型的 `reason` 经 `redact_text()` 去掉 token/口令与本机绝对路径，并截断。
- **坏 JSON**：`parse_search_output` 只保留 dict 行，非 dict 输出不再让 `row.get` 崩。
- 测试：新增 OSError 穿透、总预算、非法 `gh_binary`、`extra` 白名单、`rebuild` 词边界、空键去重、无时区时间、stderr 脱敏、非 dict JSON 的离线回归；59 passed，ruff check 全绿。
- 规则本体、契约哈希 `fa0f991f…bc93`、四段提示词 sha256 未动。

## 0.3.3 — 2026-11-14

- `short_description` 压到市场展示字段 255 字上限（当前 112 字），避免「编辑展示信息」保存时被 `String should have at most 255 characters` 拒绝。
- README 移除 v0.1.1 历史验证段与已过期的「市场登记未完成」说明，改为通用发布流程；历史继续留在本文件。
- 规则本体、契约哈希、测试数（49 passed）均未变。

## 0.3.2 — 2026-11-14

按第三方复查（7.5/10）修的五项 + 一项文档整理：

- **`rbb_verify` 路径收敛**：`path` 必须在插件工作目录内且是 `.md`，`realpath` 穿透 `..` 与符号链接；越界返回 `rc=2`，不再能当任意文件读取入口。输出截断到 800/400 字符。
- **`gh` 参数注入**：`gh search repos` 的所有选项前置，检索串放在 `--` 之后；`-foo` 这类关键词不再被当成选项。两处 `repo_scan`（插件内核与随包 CLI）同步。
- **配置失败不再静默**：`plugin.toml` 缺失/解析失败会 `warning` 日志，`rbb_status.settings` 增加 `source` 与 `warning`，明确显示「正在用兜底默认值」；兜底会关闭 `enable_github_scan`、条数压到 1，不拿脏配置去检索。
- **默认值去重 + 布尔显式解析**：`plugin.toml [settings]` 为唯一默认值来源，`config.example.toml` 不再抄第二份；`"false"` / `"0"` / `"off"` 不再被 `bool()` 当成 True。
- **契约测试**：新增 `tests/fixtures/gh_search_repos.json`（录好的 `gh search repos --json` 输出），既直接测 JSON 形状，也用假 `gh` 真正跑一遍 `subprocess` 并校验 `--` 前后的 argv 顺序。
- **测试工程**：修复 pytest 以绝对 `tests` 路径收集时把仓库根 `__init__.py` 当模块导入导致的 47 项全错；`python -m pytest tests` 现为 49 passed。
- README 的版本修复记录迁到本文件。

## 0.3.1 — 2026-11-XX

- `check_deliverable` 不再因备选段出现 `Build` 就误报 `gate_line`；无结论行时仅在结论词唯一时套门禁。
- `prompt_digest.py` 登记表结构校验：缺 `id` / `versions` 非数组 / id 重复 / `prompts` 非数组 → `FAIL` + `rc=2`。
- `rbb_scan` 非数字参数回默认值；启动哈希任务保留引用避免 GC。
- 同步上游 v1.2.1 的 E741 清理。

## 0.3.0 — 同步上游 v1.2.0

- 粘贴型提示词 sha256 登记（`prompt-digests.json` + `scripts/prompt_digest.py`）。
- `host-adapters.md` 四段提示词加 `RBB:PROMPT` 锚点。
- 规则本体未动。

## 0.2.0 — 同步上游 v1.1.0

- 可改性硬闸（绿/黄/红，红档禁止 Join）。
- 稳定接入口 + ≤150 行薄适配层。
- 分支纪律：不直推 `main` / `master`。

## 0.1.1

- 市场首发版；官方 Verify / Release 工作流通过。
