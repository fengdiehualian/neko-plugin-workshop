# src/studio —— wb-studio.mjs 的拆分目标

`wb-studio.mjs` 5646 行、单文件、顶层 `boot()` 直接执行。这个目录是它的拆分落点。

## 为什么拆

不是"为了好看"。现在这个文件的实际问题是：

- **改一处要读 5600 行**：任何改动都得先确认有没有同名变量在别处被复用；
- **没法单测**：没有 import 入口，只能整个进程起起来测（冒烟能测到的粒度就是"能启动"）；
- **拆分风险不可见**：没有对外面快照之前，搬错了只能靠手点网页发现。

## 六块的边界与依赖方向

依赖只允许**从左指向右**（外层依赖内层，内层不知道外层）：

```
CLI 入口  →  发布  →  校验  →  生成  →  契约  →  外部依赖
（进程/窗口） （update） （check） （提示词/会话） （配置/权限） （引擎/CLI）
```

| 块 | 管什么 | 现状 |
|---|---|---|
| **契约** | `defaultConfig`、`loadUserSettings`、后端选择、API token、权限升级 | 未拆 |
| **生成** | 会话、提示词拼装、专家团注入、上下文压缩 | 未拆 |
| **校验** | `/api/test`、`neko_plugin_cli check` 调用、结构化 issues | 未拆 |
| **发布** | `update` 检查、`build_payload`、`verifyUpdateManifest` | 未拆 |
| **外部依赖** | 引擎（zcode / opencode / cli）、Chromium 探测、venv | 未拆 |
| **CLI 入口** | `boot()`、`openWorkshopWindow`、`studio.lock`、退出清理 | 未拆 |
| **纯工具**（不属于六块，先落地） | `writeJsonAtomic` / `estTokens` / `sessionTokens` / `openaiEffort` / `THINK_LEVELS` | **已搬**（`util.mjs`） |

## 拆分原则（硬约束）

1. **只搬不改**。函数体逐字搬走，调用点靠 import 接上。
   唯一的允许差异：`require("node:fs")` 换成模块顶部 `import`（独立模块不共享父文件的 require 环境）。
2. **一块一提交，每块都跑安全网**：
   ```bash
   bun test test/wb-studio-surface.test.ts   # 对外面必须零变化
   bun test test/wb-studio-smoke.test.ts     # 搬完还能启动、还能回话
   bun test                                  # 既有测试不回退
   ```
3. **对外面变了就是错**。确实要改对外面（加/删路由、改配置键）时，跑
   `UPDATE_SNAPSHOT=1 bun test` 重生成快照，并在 PR 描述里写清改了哪条、为什么。
4. **不新建全局状态**。搬出来的模块不许导出可变单例；需要状态就由调用方传进来。

## 为什么先搬"纯工具"而不是 CLI 入口

字面上的"从外到内"是 CLI 入口先行，但这个文件的外层（`boot()`）直接引用 8 个模块级可变状态
（`config`、`srv`、`backendCfg`、`engineLastError`、`ensureEngine`、`publishLocalToken`…）。
先搬外层，等于要么加一层 context 对象（那是"改"），要么用 `globalThis` 桥接（那是 hack）。

所以实际顺序是**先摘掉零状态的部分，把内层模块先立起来**，等内层都有了明确接口，
外层自然变薄——这也是绞杀者模式（strangler）的常规走法。

## 后续顺序（建议）

1. **纯工具**（已完成）：`util.mjs`
2. **契约**：`config.mjs` —— 把 `defaultConfig` / `loadUserSettings` / `BACKEND_*` 收进去，
   导出 `loadConfig(root)` 而不是模块级 `let config`（这一步开始会有真正的接口改动，
   需要单独一个 PR 并更新快照里的配置键断言）。
3. **外部依赖**：`engine.mjs` —— `findChromium` / `detectZcode` / `ensureEngine` / `zcodeRuntimeEnv`。
4. **校验**：`check.mjs`。
5. **发布**：`update.mjs`。
6. **生成**：`session.mjs` / `prompt.mjs`。
7. **CLI 入口**：`boot.mjs` + 主文件只剩 import 与 `boot()`。

## 安装版说明

`src/studio/` 是运行期代码，随包分发（`git archive` 全量打包，不在 `export-ignore` 里）。

但**打包不会因为少了一个模块而失败**：`workbench/build_payload.py` 的 `REQUIRED` 是
出厂检查的白名单，只核对点名的文件在不在、够不够大。新增模块必须同步往 `REQUIRED`
追加一条，否则缺模块的包照样能过检查，装完启动即崩。
`src/studio/util.mjs` 已经加进去了，后续每搬一块都要补一条。
