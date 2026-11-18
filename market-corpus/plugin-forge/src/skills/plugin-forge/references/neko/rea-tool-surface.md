# REA 工具面参考与安全审计记录

> **性质不同**：`morluto/rea` 不是纯提示词 skill，而是 **MCP server + CLI + agent skill**（npm `rea-agents`）。它具备运行目标程序、进程注入、调试会话等高权限能力。
> **审计结论：有条件通过** —— 源码无恶意外发、无隐藏遥测、无混淆载荷；但工具本身能力高，必须按下述约束使用。
> **本专家包不安装、不执行 `rea setup`**，仅记录为团队可用工具面。

## 1. 目标

| 项 | 值 |
|---|---|
| 上游 | https://github.com/morluto/rea |
| 名称 | REA: Reverse Engineer Anything |
| 定位 | 在**没有目标源码**时，检查应用行为与实现，给出每条结论的 evidence 与 limitations |
| 形态 | MCP server（核心）/ CLI（`rea`）/ agent skill（`skills.sh/morluto/rea/reverse-engineer-anything`） |
| 发行 | npm `rea-agents@6.8.0`（release 2026-10-25） |
| 许可 | MIT |
| 维护者 | morluto |
| 审计 commit | `4b961e3715375971aee6dec848827c9c072d947b`（2026-11-03，`fix(cli): protect Amp settings during default registration (#8076)`） |
| 本地克隆 | `C:\Users\<user>\WorkBuddy\2026-11-02-21-48-31\rea`（`--depth 1`，7097 文件） |
| 环境现状 | 宿主已挂载 `mcp__rea__*` 工具集（无需本包内置） |

**子模块（5 个，浅克隆未拉取）**：`third_party/jadx-headless-mcp`、`binwalk`、`unblob`、`ghidra-nativeaot`、`wakaru`。审计范围限主仓库；若后续要跑这些 provider，须单独审计对应子模块。

## 2. 能力地图（与 N.E.K.O 插件场景相关的部分）

| 层 | 能力 |
|---|---|
| **静态分析（只读目标，不运行应用）** | JavaScript / Electron 模块、imports、IPC、source maps；.NET assemblies 元数据与 CIL；WASM 校验与 WAT；APK manifest/classes/签名；native ELF/PE/Mach-O 布局、符号、字符串；Go/Rust/IL2CPP/Flutter/Godot/GGUF 等工件元数据；SQLite 库 |
| **运行时（高权限，需显式授权）** | PTY 进程行为观察、Frida instrumentation、GDB/Rizin 调试会话、浏览器 CDP 网页结构与网络观察、adb 设备交互 |
| **记录数据（离线）** | HAR / Fiddler SAZ 网络捕获、pcap、崩溃记录、逻辑信号采样 |
| **输出** | 结构化结果 + 每条结论的 evidence 与 limitations；`--json` 可机读 |

原生二进制反编译需外部 provider（Hopper / Ghidra / IDA / Kuna）；静态 JS 分析不需要。

## 3. 安全审计（结论：源码层通过）

| 审计项 | 结论 | 证据 |
|---|---|---|
| `install.sh` 恶意外发 | 无 | 187 行：从 `api.github.com` 读 release 版本 → `npm install --global rea-agents@<version>` → 交互式 `setup`；有 `--dry-run`、安装后版本身份校验 |
| `install.sh` 的 `eval` | **安全设计** | L29–35 注释明确：`eval '[[ "$1" =~ $2 ]]'` 只解析字面量，参数作为数据展开，防 `curl\|bash` 下载截断时误执行 |
| npm 生命周期钩子 | 仅本地构建 | `prepare` → 检查 `src/` + 给 `release-please` 打本地补丁；`prepack` → `spawnSync` 仅调 `node npm_execpath run build:cached` 与 `generate-skill-metadata.mjs --check` |
| git hooks | 低风险 | `.husky/pre-commit` = `lint-staged`，`pre-push` = `check:fast`；仅提交时触发，克隆/读取不触发 |
| 隐藏遥测 | **无，且多处显式关闭** | 46 处精确命中全部为：测试夹具里的 turborepo `telemetry.json` 路径、注释中的本地进程 lifecycle telemetry、`telemetry: {enabled:false}` / `mode=off`、CI 只读 workflow 计时；非测试文件无上报代码 |
| 可疑外发端点 | 无 | 扫描到的域名均为 RFC 保留测试域（`.test` / `.invalid` / `192.0.2.1`）、官方文档（apple / hopperapp / oracle / android.googlesource）、或 `localhost`；`HopperDownload.ts` 的 `fetch` 目标由**调用方传入**且做 URL 校验（剥离 userinfo、拒绝带凭据 URL、校验重定向） |
| 混淆 / 编码载荷 | 未发现 | 无异常长 base64 串 |
| 许可与合规 | 完整 | MIT；`DISCLAIMER.md`（负责任使用）、`SECURITY.md`、`THIRD_PARTY_NOTICES.md`、`licenses/` 齐备 |

## 4. 风险定级与使用约束（**必须遵守**）

REA 与 `security-audit` 不同：后者是只读方法论，前者**会与真实进程、文件、设备交互**。挂接为工具面，须遵守：

1. **合法边界（DISCLAIMER 红线）**：仅用于自有或已获授权的目标——安全研究、恶意软件/事件分析、漏洞研究与协同披露、CTF/教学、互操作性、软件保全、调试自有软件。**禁止**：规避 DRM/许可校验/反作弊、制作游戏作弊/篡改在线服务、绕过未授权系统的认证与访问控制、复制专有软件或复现其受保护代码与资产、规避 AI 模型提供商的使用政策。
2. **运行时能力需显式授权**：执行目标程序、Frida 注入、GDB/Rizin 会话、adb 设备交互、浏览器打开网页，均须用户明确要求且在隔离环境中进行；默认只用**静态分析**（只读目标文件，不运行应用）。
3. **不自动安装 / 不改配置**：`npx rea-agents setup` 会向 agent 注册 MCP server 并写入 workflow instructions（会备份现有配置）；本专家团**不代为执行**，需要时由用户自行决定。
4. **工具面探测（团队宪法第 10 条）**：调用任何 `mcp__rea__*` 能力前，先非破坏性确认工具存在与参数签名；无法探测则标 `ASSUMED-SURFACE`，不裸写未验证调用。
5. **环境要求**：Node.js `>=22.23.4` / `>=24.22.1` / `26.12.1+`；部分 provider 需额外安装（Hopper/Ghidra/IDA/Kuna、JADX+JDK、adb、WABT、tshark 等），REA 不自动安装这些。
6. 涉及删除 / 外部写等高风险操作，先列清单交用户确认，不擅自执行。

## 5. 与专家团的对接

| 成员 | 用途 |
|---|---|
| **云安（neko-security）** | 逆向分析、插件/依赖二进制的安全审计；`security-audit` 提供**六阶段流程**，REA 提供**取证工具面** |
| **云雨（neko-plugin-dev）** | 理解现有插件 / 竞品实现时，用 REA 的**静态 JS/Electron 分析**（模块、IPC、source maps）作为参考，不运行目标 |
| 云远（neko-architect） | 需要还原第三方组件架构时按需借用 |

**层级区分**：`security-audit` = 流程方法论（纯提示词）；`rea` = 运行时工具能力（MCP）。二者不重叠，可组合：先用 REA 取证，再按 security-audit 的验证/报告纪律输出。

**安装（如需，须用户确认，本包不执行）**：
```bash
npx rea-agents setup                    # 注册 MCP + workflow instructions
npm install --global rea-agents         # 全局 CLI
npx -y rea-agents@latest analyze-javascript-application /abs/path/to/app --json   # 一次性静态分析
```
