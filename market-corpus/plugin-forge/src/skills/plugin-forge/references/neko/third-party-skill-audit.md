# 第三方 Skill 安全审计记录：`security-audit`（Cloudflare）

> 满足团队规则「安装 / 应用第三方 skill 前必须先做安全审计」。本记录在挂接进专家团**之前**完成，结论：通过。

## 1. 目标

| 项 | 值 |
|---|---|
| 名称 | `security-audit` |
| 上游 | https://github.com/cloudflare/security-audit-skill |
| 组织 | Cloudflare（官方） |
| 许可 | MIT（LICENSE 已核对，1099 B） |
| 定位 | 代码库 / API / 服务 / CLI / 库 / 守护进程的安全审计与漏洞复核 |
| 审计 commit | `c1c8a8c1471069fb0e188eeaff69b8e8db6564a8`（2026-09-14，"Clarify guidance and full audit modes"） |
| 完整性核对 | 本地克隆 HEAD 与 GitHub API tree sha **一致**，未被篡改 |
| 本地克隆 | `C:\Users\<user>\WorkBuddy\2026-11-02-21-48-31\security-audit-skill`（工作区，**不在专家包内**） |
| 文件数 | 22（LICENSE + README + `skills/security-audit/` 下 19 个 + 1 层目录） |

## 2. 能力（六阶段）

1. **Reconnaissance** → 产出 `architecture.md` + `coverage-ledger.json`
2. **Coverage-led hunting** → 从 ledger 单元派隔离 hunter，配 coverage critic 补缺口
3. **Candidate validation** → 每个候选交给**未参与发现**的新 verifier 尝试反证
4. **Structured output** → `findings.json`（`confirmed` / `needs_validation` / `rejected`），按 `report-schema.json` 校验
5. **Independent record verification** → 新 agent 复核最终 source 声明；实质性替换再走一次独立核验
6. **Target-neutral reporting** → `REPORT.md` / `FINDINGS-DETAIL.md` / `NEEDS-VALIDATION.md`

**核心设计原则**：只确认已成立的信任边界失效；severity = likelihood × impact（不是"偏离清单"）；纵深防御缺失只是加固建议、不算漏洞；多次运行可提升覆盖率（单次约覆盖半数）。

## 3. 审计项与结论

| 审计项 | 结论 | 证据 |
|---|---|---|
| 网络外发 | **无** | 全仓库代码无 `http(s).request`、`fetch(`、`net.`、`dns.` |
| 子进程 / 命令执行 | 生产脚本**无** | 两个 `.test.cjs` 的 `spawnSync` 仅调用**自身 node 跑本地校验器**；生产 `.cjs` 无 `child_process` |
| 文件写入 | 生产脚本**无** | 生产 `.cjs` 仅 `fs` 只读（`openSync` / `readSync` / `fstatSync`）+ stdout；测试写 `os.tmpdir()` 临时目录并 `rmSync` 清理 |
| 环境变量 | **无** | 无任何 `process.env` |
| 动态执行 | **无** | 无 `eval(` / `new Function(` / `vm.` |
| 提示词注入 | **无** | 全文本扫描仅 1 处命中，是攻击类描述名词（"Export/backup as exfiltration"），非指令 |
| 混淆 / 编码载荷 | **无** | 超长 base64 串 0 处 |
| 外链 | 3 个文档链接 | `blog.cloudflare.com`、`github.com`、`skills.sh`，无数据外发端点 |
| 擅自写文件 | **无** | `SKILL.md:12` 明确"加载不授权完整审计流程或文件创建"，默认 guidance 模式 |
| 执行不可信代码 | **有约束** | `SKILL.md` §Universal execution safety：禁外网、白名单环境、只读目标、显式资源上限；**无 OS 沙箱则不执行目标代码**，改为保留 `needs_validation` |

**审计结论：通过，可挂接使用。**

## 4. 使用约束（团队必须遵守）

1. **默认 guidance 模式**——只回答与复核，不擅自创建文件；完整审计需用户显式要求。
2. **禁止外部网络**——执行目标代码须在 OS 强制沙箱内：无外网、白名单环境、目标只读、显式资源上限。
3. **不探测生产端点 / 共享基础设施 / 他人凭据**，一律用 dummy 主体与密钥。
4. 输出目录默认 `~/security-audit-skill/<repo>/run-<N>`；写进目标仓库仅限版本控制忽略的目录。
5. 与团队宪法一致：每条发现给「位置（文件:行）+ 修法」，不报"已合规 %"。

## 5. 与专家团的对接

- **挂接成员**：云安（neko-security）——作为**通用代码库级**安全审计的方法论与流程。
- **分工不重叠**：云安原有能力（密钥卫生 / 提示词注入 / AI 生成代码审计 / 供应链）= **N.E.K.O 插件场景的专项检查**；`security-audit` 提供**六阶段通用审计流程**。二者互补。
- **依赖**：Node.js（仅两个零依赖 `.cjs` 校验器需要）、支持并行子 agent 的平台。
- **安装**（如需在 WorkBuddy 环境内直接调用，须先审计即本记录）：
  ```
  npx skills add https://github.com/cloudflare/security-audit-skill --skill security-audit --global
  ```
- **未内置**：本专家包不含该 skill 文件，仅挂接引用；上游更新无需同步包内副本。

## 6. 安装记录（2026-11-06）

| 项 | 值 |
|---|---|
| 安装决定 | 用户 2026-11-06 明确指示「装」 |
| 前置复核 | `git ls-remote` 上游 HEAD = `c1c8a8c1471069fb0e188eeaff69b8e8db6564a8`，**与 §1 审计 commit 完全一致** → 审计仍然有效，无需重审 |
| 源 | 已审计克隆 `~/WorkBuddy/2026-11-02-21-48-31/security-audit-skill/skills/security-audit`（工作树干净） |
| 目标 | `~/.workbuddy/skills/security-audit/` |
| 完整性 | 20 个文件，与源逐一一致；含 `SKILL.md`；**未复制 `.git`** |
| 生效方式 | 用户级 skill，宿主扫描后可直接调用；`§4` 使用约束继续适用 |
| 后续 | 上游若推进，须重新审计后方可同步；`skills` 计数 67 → 68、`mcp.workbuddy` 指纹因 rea 升级同步变化（见 `capability-baseline.md`） |
