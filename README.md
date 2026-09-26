# N.E.K.O. 插件工坊 (Plugin Workshop)

让完全不会编程的人,用一句话生成 N.E.K.O.(猫娘计划)插件的 AI 工坊。

对猫娘说「帮我做一个每 10 分钟提醒我喝水的插件」,它会自动写代码、检查、打包,
产出可直接导入 N.E.K.O. 使用的 `.neko-plugin` 文件——全程无需接触代码。

![demo](https://img.shields.io/badge/AI-opencode--cli-blue) ![python](https://img.shields.io/badge/plugin--runtime-Python%203.11-blue)

## 功能特性

- **一句话生成插件**:内置 3 个模板(打招呼/定时提醒/笔记存储),Agent 自动选型、写码、跑官方 check、打包
- **自动修复循环**:check/build 失败输出结构化 issue,Agent 读错误自动修,循环到达标
- **多会话管理**:新建/切换/重命名/归档,记忆互相独立,实时落盘
- **思考过程可见**:任务进行中实时流式显示 Agent 推理,完成后折叠可回看
- **Token 统计**:每条回复与整会话的用量(输入/输出/思考分开计)
- **8 种 API 协议**:OpenAI 兼容 / Anthropic / Gemini / OpenRouter / Groq / Mistral / xAI / OpenAI 官方,切换 2 秒生效
- **四套主题**:樱花粉 / 天空蓝 / 纯净白 / 暗夜黑
- **Build/Plan 模式**:Plan 只出方案不动文件(引擎原生强制)
- **内置窗口**:Edge/Chrome App 模式独立窗口,关窗即安全退出
- **看门狗自愈**:模型接口卡死 4 分钟自动止损;引擎异常自动重启;残留锁自动清理

## 仓库结构

```
├── workbench/            # 核心(opencode workbench 包)
│   ├── cli.mjs           #   wb-plugin CLI(scaffold/verify)
│   ├── src/index.ts      #   生成→check→build 闭环内核 + 错误解析器
│   ├── wb-studio.mjs     #   网页服务(对话 UI/多会话/设置/归档/主题)
│   ├── wb-agent-lib.mjs  #   无人值守 Agent 驱动(v1/v2 引擎双协议)
│   ├── packs/            #   模板 + 插件规范 skill(15 页官方文档 + 市场写法分析)
│   └── make-exe.ps1      #   一键打包 SFX 安装器
├── market-corpus/        # 46 个官方市场插件源码(Agent 的学习语料)
├── wb-studio.mjs 等      # 便携包顶层文件
└── 使用说明.txt           # 面向小白的完整说明
```

## Agent 的知识体系(让它写出市场上架水准的插件)

1. **官方规范写死**:`packs/.../SKILL.md` 15 条铁律 + 15 页官方文档引用,文档里没有的 API 一律不用
2. **市场统计分析**:`references/market-patterns.md`——46 个真实上架插件的能力使用率
   (llm_tool 50% / push_message 57% / store 37%…)与四大惯用法
3. **全量插件卡片**:`references/market-catalog.md`——46 个插件的功能/接口/能力/源码索引
4. **语料库**:`market-corpus/` 全部源码,按功能找同类直接读

## 快速开始(面向开发者)

前置:N.E.K.O. 源码(check/build 依赖)、Node/bun 运行时、一套 OpenAI 兼容(或其它支持协议)的模型服务。

```bash
cd workbench
bun install && bun test        # 9 个单元测试
bun cli.mjs scaffold neko-plugin reminder \
  --out C:/projects --neko C:/path/to/N.E.K.O \
  --PLUGIN_ID my_reminder --PLUGIN_NAME 提醒 --CLASS_NAME MyReminder
bun wb-studio.mjs              # 启动网页工坊(127.0.0.1:5099)
```

发布为绿色一键包(便携运行时 + SFX 安装器):

```powershell
powershell -File workbench/make-exe.ps1 -Stage "<便携包目录>" -Out setup.exe
```

## 技术要点

- **引擎**:opencode(opencode-cli.exe 编译版,Basic 认证 /api/* v2 协议),数据目录锁定包内 runtime\
- **无人值守驱动**:SSE 事件流自动应答权限/提问、断线重连、无进展看门狗(240s)、限流自动重试
- **全局作用域**:Agent 可操作任意目录(带安全底线:不碰系统目录、破坏性操作先确认)
- **隐私**:API 密钥与会话数据只存本机 runtime 目录,升级安装自动保留

## 相关链接

- N.E.K.O. 主程序:https://github.com/Project-N-E-K-O/N.E.K.O
- 插件开发文档:https://project-neko.online/zh-CN/plugins/
- 插件市场:https://market.project-neko.cn

## License

MIT

## 任意 Agent 底座(本实例新增)

工坊的大脑不再绑定 opencode 引擎,三种方式按需选:

1. **CLI 底座**(wb-studio.config.json):
   ```json
   "engineMode": "cli",
   "engineCmd": ["claude", "-p", "{text}"]
   ```
   或 `"enginePreset": "claude"`(内置 claude/codex/omp/opencode 预设)。
   `{text}` 替换为整段任务;cwd=工作区;自动注入 `WB_PROJECTS_DIR`/`WB_NEKO_REPO`;
   底座会经 PATH/PATHEXT 定位,`.cmd/.bat` shim 自动解析回真实解释器(npm 安装的
   claude/codex/omp 即属此类),不经 shell、无转义/注入面;无法解析出 exe 的底座请用
   `{textFile}` 占位(任务文本经 UTF-8 临时文件传递,用后即删),或用 `{stdin}` 占位经管道输入(claude/codex print 模式原生支持);
   模型凭据由底座自理,无需再填 API Key;停止按钮=连子进程整树清理。

2. **MCP 底座**:`bun mcp.mjs`(stdio),工具 `wb_scaffold` / `wb_verify`(支持 strict)/ `wb_install_skill`。

3. **技能包分发**:`wb_install_skill` 把 neko-plugin-dev 规范装进 claude/codex/opencode/omp/自定义底座。

verify/scaffold 命令行支持 `--python <解释器>`(或环境变量 `WB_PYTHON`)与 `--strict`
(warning 一律视为失败:NEKO 原生 `-s` + 残余 warning 兜底判负,不过 build)。
