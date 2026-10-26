# dsh-plugin-neko

把 N.E.K.O. 的插件工具镜像成 DeepSeek Harness 的原生工具，让 dsh agent
能调用 N.E.K.O. 的能力（点歌、shell、天气……）就像调用自己的工具一样。

## 开箱即用

只需要装好的 DeepSeek Harness 桌面端。**不需要** Node.js、pnpm、npm，
也不需要克隆任何仓库——DSH 把这些全打包在 `resources/` 里了。

```cmd
REM 装到独立 profile，不用退出正在运行的 DSH
install.cmd neko

REM 装到 desktop profile（需要先退出 DSH）
install.cmd

REM DSH 不在默认位置时显式指定
install.cmd neko "D:\DeepSeek Harness"
```

脚本自动定位 DSH 安装目录和它内置的 CLI，然后调 `dsh plugin add` 装好。
用 `.cmd` 而不是 `.ps1`：某些终端宿主（Cherry Studio、VS Code）把 `HOME`
暴露为只读 PowerShell 变量，从这类宿主启动 ps1 会在同步环境时失败，
cmd 不碰它。

如果你更习惯 PowerShell，`install.ps1` 功能相同——但要**从普通
PowerShell 窗口运行**，不要从上面那类宿主里启动。

## 它怎么工作

N.E.K.O. 的主服务器在 `/api/tools` 暴露一份工具注册表，每个工具都带一个
回环地址的 HTTP 回调。这个插件：

1. 读注册表，过滤出 `plugin:` 前缀的工具（插件提供的，非内置）
2. 把每个工具用 `defineTool` 注册成 dsh 原生工具，名前加 `neko_` 前缀
3. dsh agent 调用时，POST 到 N.E.K.O. 的回调地址，把结果还给模型

N.E.K.O. 的工具注册只活在运行时里，重启就没，所以插件会轮询刷新。

### 为什么过滤内置工具

`source: "builtin"` 的工具（如 `recall_memory`）在猫娘自己的对话里已经有，
镜像进 dsh 会和 dsh 自己的文件、shell 工具重名冲突。想要全部可以改配置
`pluginsOnly: false`。

### 参数格式转换

两边参数格式不同，插件负责翻译：

| | N.E.K.O. (JSON Schema) | dsh (扁平参数表) |
| --- | --- | --- |
| 类型 | `{"type":"object","properties":{...}}` | `{ name: { type: 'string' } }` |
| 必填 | 顶层 `required` 数组 | 每属性 `required: true` |
| 自由参数 | `additionalProperties` | `{ type: 'json' }` |

无法精确对应的类型降级为 `json` 原样透传，保证工具仍能加载。

## 配置

`cordis.patch.yml` 里可改：

| 键 | 默认 | 说明 |
| --- | --- | --- |
| `baseUrl` | `http://127.0.0.1:48911` | N.E.K.O. 主服务器地址 |
| `role` | `''` | 限定某个猫娘角色，空表示全部 |
| `sourcePrefix` | `'plugin:'` | 只镜像此前缀的工具 |
| `refreshMs` | `30000` | 轮询注册表的间隔 |
| `timeoutMs` | `60000` | 单次调用超时 |
| `pluginsOnly` | `true` | 是否排除内置工具 |

## 验证

```powershell
$env:ELECTRON_RUN_AS_NODE = "1"
$env:DSH_HOME = "$env:USERPROFILE\.dsh"
& "D:\DeepSeek Harness\DeepSeek Harness.exe" --expose-internals `
  "D:\DeepSeek Harness\resources\app.asar\dsh\node_modules\@deepseek-ai\dsh-desktop-host\lib\cli.js" `
  --profile desktop --dump-config
```

输出里应看到 `# == dsh-plugin-neko` 和 `- id: neko-bridge`。

启动时终端会打印 `[neko-bridge] 已镜像 N.E.K.O. 工具 xxx`。连不上 N.E.K.O.
时打印 `刷新失败` 并继续——这是设计如此，不是崩溃。

## 已知限制

- **DSH 侧没有按名反注册工具的公开接口。** N.E.K.O. 里某个工具消失后，
  dsh 里的镜像会留着，调用时报错。插件会打告警。
- **桌面端独占 `desktop` profile。** 正在运行时不能从 CLI 改它，要么退出
  DSH，要么用独立 profile。
- 依赖版本写死为 DSH `0.2.1-rc.2` 内置的版本（`schemastery@3.18.5`、
  `cordis@4.0.5`、`dsh-tools@0.2.1-rc.2`）。DSH 升级后可能需要同步 bump。

## 从源码构建

`lib/index.js` 是已提交的构建产物，直接可用。改了 `src/neko-bridge.ts`
才需要重新构建（需要 Node 和 npm）：

```powershell
npm install
npm run build
```
