# 先搜后建 · N.E.K.O 适配版

把 [reuse-before-build](https://github.com/1-dcjiang/reuse-before-build) 的规则装进 N.E.K.O。

## 它解决什么

N.E.K.O 里的 AI 接到「帮我做个 X」时，最容易干的事是直接写一版脚手架。这个插件把顺序掰回来：

> 先假设已经有人做过 → 三轮检索证明 → 默认加入上游当贡献者 → 只有证明不可复用才许新建。

规则本体不是插件作者的一句话，而是随包固定的一段文本：带 `<!-- RBB:INVARIANTS -->` 标记和 sha256 基线。删条、改编号、丢门禁会被随包校验器当场报错。注意：哈希只覆盖不变块，保留锚点的重写不在机械校验范围内，仍需人读 `kept / changed`。

## 版本记录

完整修复与新增记录见 [CHANGELOG.md](CHANGELOG.md)。

- `0.3.2`：修复 `rbb_verify` 路径收敛、`gh` 参数注入、配置失败可见、默认值去重与布尔显式解析，并补录好的 `gh` JSON 契约测试。
- `0.3.3`：清理 README 历史段并把市场短描述压到 255 字上限。
- `0.3.4`：修复异常穿透、检索总预算、`gh_binary` 收敛、`extra` 白名单、结论词边界、空键去重、无时区时间与 stderr 脱敏。
- `0.3.5`：去掉 `.bat/.cmd`、给登录检查留预算、补齐 stdout/Bearer 脱敏、夹取直调参数并兼容 Windows `py` 启动器。
- `0.3.6`：把结论判定收到结论行、预算耗尽单列 `code=4`、Build 门禁识别 Markdown 装饰、关键词与日期严格校验，并让状态检查并行。
- `0.3.7`：修复 `search_candidates` 把已解析的 gh 绝对路径传给 `auth_ok` 后被自家白名单拒绝的问题。
- `0.3.8`：统一插件与 CLI 脱敏、禁止 PATH 未命中时回退裸 `gh`、删除重复版本定义，并新增带双层 SHA-256 清单的 vendor 源码包与接入文档。
- `0.3.9`：补齐交付契约、可改性硬闸、快速档与结构化判决，加固本地化校验、Windows 进程路径和限流预算，并修复随包测试独立收集。
- `0.3.10`：将下游 vendor 接入、CI 校验与本地化扩展指南纳入发布包。
- `0.3.11`：按 #172 增量复审收口 Build 可改性、快速档没命中、必需章节缺失、脱敏与关键章节哈希。
- `0.3.12`：补齐下游 vendor 的 `critical_sections` 接入要求，把章节哈希钉进校验器并新增 `--print-section-hash`，同时同步脚本头、README 与缺章报错文案。
- `0.3.13`：修 vendor 文档残留的旧版本号，并把四个关键章节的锚点也钉进契约基线。
- `0.3.14`：让 `all` 锚点在运行时生效、把契约其余基线字段钉进代码、扩宽 vendor 版本断言，并修复检索关键词去重分叉、未知 `section` 静默回退与根目录过度脱敏。
- `0.3.15`：修复盘符根目录过度脱敏、CLI 重复关键词实际重复检索与 `all` 锚点误命中，合并两端检索辅助实现，并固定离线测试工作流的 Action SHA。
- `0.3.16`：校验器升到 `1.2.11`，空锚点不再抛栈，关键词按大小写不敏感去重，并把时间/许可/表格等辅助函数继续收进 `gh_helpers.py`。
- `0.3.17`：`check_anchors` 的 `any` 与 `all` 共用词边界（仅 ASCII），契约加载时拒绝空/非字符串锚点；校验器升 `1.2.12`。
- `0.3.18`：关键章节的 `any` 也改用词边界并删除旧别名，契约顶层/`block` 非对象时不再抛栈；`repo_scan.py` 升 `1.2.9`，校验器升 `1.2.13`。
- `0.3.19`：补齐契约内层字段类型校验，`critical_sections[].headings` / `block.begin` / `declaration.keys` 等坏结构不再抛栈；校验器升 `1.2.14`。
- `0.3.20`：补齐非基线关键章节条目的 `min_chars` / `anchors` 形状防御；校验器升 `1.2.15`。

## 五个工具

| 工具 | 何时被调 | 返回 |
|---|---|---|
| `rbb_rules` | 用户说「帮我写一个…」「从零实现」时先调 | 正典核心规则与完整硬约束 / 四步流程 / 整份 SKILL.md |
| `rbb_scan` | Discovery 阶段证明「有没有现成的」 | 去重候选表 + 结构化候选；`code=2/3/4` 明确表示**没查成**（4=预算耗尽） |
| `rbb_check` | 回复用户之前自检格式 | 结构化 `verdict / modifiability / lane`、完整档或快速档缺项、可改性硬闸、Build 首行门禁 |
| `rbb_verify` | 把规则改写成别的宿主格式之后 | 内置正典与契约哈希 / 规则条数 1..5 / 全局锚点 / 声明行 |
| `rbb_status` | 排查「哈希没算出来」「gh 不可用」 | 规则哈希、随包技能自检、gh 状态、当前设置 |

## 装法

1. 下载 Release 里的 `reuse_before_build.neko-plugin`；
2. N.E.K.O → 插件 → 安装本地插件包；
3. 插件设置里确认 `enable_github_scan`（默认开）与 `gh_binary`（默认 `gh`，只允许 `gh` / `gh.exe`）；
4. 点一次「自检规则完整性」，看到规则哈希与 `PASS … verbatim` 即可。

没有 `gh` 也能装：检索会报 `code=2` 并给网页检索串，规则、格式检查、校验器照常可用。

## 目录

```
plugin.toml                        清单（id / entry / 设置项 / ui guide）
config.example.toml                可复制的运行配置示例（`[settings]` 默认值只在 plugin.toml 写一份）
pyproject.toml                     最小工程声明与 Ruff 基础规则
__init__.py                        插件类：5 个 @llm_tool + 生命周期 + 自检 action
rbb_core.py                        宿主无关纯逻辑（检索/格式检查/配置解析/校验包装），可离线测试
scripts/vendor_release.py          可复现 vendor 构建、Release 清单与复制后复检
skills/reuse-before-build/          N.E.K.O 随包技能与本地化校验
  SKILL.md                         正典，含 RBB:INVARIANTS 基线块
  localization.contract.json       块标记 + sha256 + 规则条数/全局锚点 + 声明格式
  references/                      检索手册 / 六维打分+可改性硬闸 / 贡献协议+分支纪律 / 接入口与薄适配层 / 宿主适配 / 中文完整版
  prompt-digests.json              粘贴型提示词登记表（id / 版本 / sha256 / 字符数）
  scripts/redaction.py             插件内核与 CLI 共用的唯一脱敏实现
  scripts/gh_helpers.py            两端共用的 gh 路径、关键词、JSON 与检索参数辅助
  scripts/repo_scan.py             无宿主时的 CLI 检索（退出码 0/2/3/4/5/6）
  scripts/prompt_digest.py         粘贴提示词反查（--check / --verify / --digest / --emit）
  scripts/verify_localized.py      本地化校验器（内置哈希基线 + 五道闸）
  tests/test_skill_pack.py         随包独立测试（默认禁止真实联网）
tests/test_offline.py              主仓离线自检（不需要 N.E.K.O SDK）
tests/test_plugin_runtime.py       运行时自检（跑在最小 SDK 桩上）
tests/test_smoke.py                市场模板要求的清单/入口 smoke test
tests/test_security_regressions.py 脱敏、gh 路径与版本定义回归
tests/test_deep_review_regressions.py 补深审负例与交付契约回归
tests/test_vendor_release.py       vendor 构建、篡改拒绝与文档契约
tests/test_vendor_smoke.py         解压后脱离源仓库的独立运行验证
tests/fixtures/gh_search_repos.json 录好的 gh 检索输出（契约测试用）
CHANGELOG.md                       版本修复记录
.vscode/                           市场模板要求的 settings.json / tasks.json
docs/guide.md                      插件面板里的使用说明
docs/vendor.md                     下游 vendor 的固定版本接入与校验说明
.github/workflows/                 官方 Verify / Release + 独立离线回归 + 后续 vendor 发布
```

> 说明：核心不变块使用固定 SHA-256 基线（`fa0f991f…bc93`，由 `verify_localized.py` 验证），不把它扩大解释为整份规则或脚本与上游逐字一致。N.E.K.O 版的安全修复、适配文档与共用模块由独立回归测试锁定。

`rbb_core.py` 导入时硬加载 `skills/reuse-before-build/scripts/gh_helpers.py`，后者又硬加载同目录 `redaction.py`；这两个文件是必需文件，不能单独移走或删除，否则整个插件导入失败。


## 市场合规

仓库保留 N.E.K.O 市场 CLI 严格检查所需的脚手架，发布结果以远端官方检查为准：

- `plugin.toml` 符合 `[plugin]` 允许键集；`entry` 指向 `plugin.plugins.reuse_before_build`；
- `config.example.toml` 承接运行配置模板（不含 `[plugin]`），`[settings]` 默认值不重复抄写，消掉「legacy runtime configuration」警告；
- `tests/test_smoke.py`、`.vscode/settings.json`、`.vscode/tasks.json`、`pyproject.toml`、`.gitignore` 齐备；
- `.github/workflows/{verify,release}.yml` 复用官方 `plugin-market-{verify,release}.yml@main`；
- 发版 tag 必须与 `[plugin].version` 相同（当前 `0.3.20` → tag `v0.3.20`）。

## 验过什么

本版（v0.3.20）：

- **本地**：`python -m pytest -q tests skills/reuse-before-build/tests` **185 passed**（另有 73 个子测试）；`python -m unittest discover -s tests -v` **140 tests**；Ruff 全绿；vendor 重复构建字节一致，解压后可脱离源仓库运行。
- **随包 skill**：`verify_localized.py` 对 `SKILL.md` 输出 `PASS verbatim`，`zh-guide.md` 输出 `translated(zh)`；核心不变块哈希 `fa0f991f…bc93` 未变。
- **CI**：官方检查、payload 哈希与发布证明见本版 Release 的 `market-evidence.json` 和检查文本。

## 发布与市场

- 流程：打 tag → 官方 Release 产出 `reuse_before_build.neko-plugin`、检查文本与 `market-evidence.json` → 独立 `workflow_run` vendor 工作流上传 `neko-reuse-before-build-v0.3.20-vendor.zip`、`vendor_release.py` 和统一 `SHA256SUMS.txt`；官方 managed `release.yml` 保持原样。
- 下游把源码纳入自己仓库时，按 [vendor 接入文档](docs/vendor.md) 固定 tag、校验 ZIP、复制到 `plugin/plugins/reuse_before_build` 并做复制后复检；不要与 `.neko-plugin` 重复安装。
- 插件包发布到市场：`neko-plugin publish market <release-url>`。
- 市场展示信息（简介、展示页 README）由市场侧独立维护，发版不会覆盖；发布后在开发者中心「编辑展示信息 / 编辑展示页面」同步。

## 边界（写在这里，也写进规则里）

- **只读**：本插件不创建 issue、不提 PR、不发评论、不推送任何远端变更。公开写一律要用户逐次确认。
- **不虚构**：检索失败时返回的是「没查成」，不是「没有」；插件不会替你编仓库名和星数。
- **能抓什么**：删条、改编号、丢规则、缺声明、块内改字冒充逐字版。
- **抓不住什么**：保留全部关键词但把语气写软（「应当考虑」替掉「必须」）。这一层只能人读 `kept / changed`。

## 许可

MIT。规则本体与上游同源；N.E.K.O 适配修复留在本仓库，通用规则变更再向上游提 PR。
