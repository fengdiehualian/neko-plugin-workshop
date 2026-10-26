# DSH 播报桥（dsh_bridge）— P0 最小验证版

把 DeepSeek Harness 的文字交给猫娘，让她**自己精简、自己念出来**。
本插件只做两件事：**收文本 + 下命令**；精简与朗读全部在角色侧完成。

```
DeepSeek Harness ──(HTTP POST /push)──▶ 本插件 HTTP Server ──push_message
        (ai_behavior="respond")──▶ 猫娘：精简 → TTS 念出来
```

> 骨架照抄官方内置插件 `claude_companion`（它用同一套形态把 Claude Code 的活动推给她），
> 所以这条路在 v0.9.0 上是被官方实现验证过的，不是猜测。

## 1. 安装与启动（2026-10-01 实测可用的做法）

> ⚠️ 本机装的是 **v0.9.0（Steam）**，**没有**「开发模式／加载未打包插件」界面——那是 v0.9.1 才加的功能（官方 main 分支文档比发行版新）。v0.9.0 的正路是走插件服务端（默认 `http://127.0.0.1:48916`）的 `/plugin-cli/*` 接口装包。

插件 ID：`dsh_bridge`；入口必须是 **`plugin.plugins.dsh_bridge:DshBridgePlugin`**（安装版的命名空间约定，写成 `dsh_bridge:DshBridgePlugin` 会启动失败且日志里没有 traceback）。

打包成 `.neko-plugin`（`manifest.toml` + `metadata.toml` + `payload/plugins/<id>/`，payload 要算 sha256 写进 metadata），然后：

```text
POST /plugin-cli/upload          (multipart file)         → 得到服务器上的包路径
POST /plugin-cli/install-plan    {package}                 → 拿到 confirmation_token
POST /plugin-cli/install         {package, confirm_upgrade: true, confirmation_token}
POST /plugin/dsh_bridge/start                              → success
GET  http://127.0.0.1:48921/health → {"status":"ok","plugin":"dsh_bridge"}
```

插件安装位置：`D:\AI\猫娘计划\N.E.K.O\.neko-plugin-installations\plugins\dsh_bridge`；
运行数据/配置：`D:\AI\猫娘计划\N.E.K.O\plugins\dsh_bridge\{config,data,cache}`。
启动日志（含"已就绪/已播报"）可用 `GET /plugin/dsh_bridge/logs` 查看。

## 2. 验证她会不会念（✅ 2026-10-01 已验证通过）

**正确用法：材料 + 指令写在同一条消息里，一次投递。**

```powershell
node -e "fetch('http://127.0.0.1:48921/push',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({text:'暗号会飞的橘猫七只。以下是需要播报的内容：猫娘计划的插件桥已经装好并跑通，DSH 能把文字推给猫娘，由猫娘自己精简后念出来。',ai_behavior:'respond',visibility:[],priority:9})}).then(r=>r.json()).then(console.log)"
```

实测结果（2026-10-01 15:33，她空闲）：她开口念出了 **"他说插件桥装好了，DSH 可以推送材料给他，他会念给我听"** ——
**用自己的话精简转述了材料内容**，不是逐字复读，也不是单纯评论。

### 两个必须遵守的要点

| 要点 | 说明 |
|---|---|
| **材料必须与指令同条** | ❌ 拆成 `read`（投材料）+ `respond`（催促）两步会失败：`read` 那步被降级成 `enqueued callback (passive)`（要等用户下个回合才带上），她手里没材料，会说"材料内容是什么，你得给本喵看"。 |
| **等她空闲再推** | 你正在跟她实时对话时，推送会排队（日志 `pending=N`），并可能与旧 cue 混在一起。干净效果要求她 `idle`（日志出现"会话结束"/连接关闭即可）。她从"会话已结束"状态也能被唤醒：会自动新开 text 会话。 |

投递参数建议：`ai_behavior="respond"` + `visibility=[]` + `priority=9`。

> 她还会把推送内容写进长期记忆（`memory\<角色>\time_indexed.db` 里能搜到由此提取的事实），所以别说不想让她记住的话。

## 3. 接口

| 方法 | 路径 | body | 说明 |
|---|---|---|---|
| GET | `/health` | — | `{"status":"ok"}` |
| POST | `/push` | `{"text":"..."}` | 走 `plugin.toml` 里的模板（下命令 + 原文） |
| POST | `/speak` | `{"text":"..."}` | 原样投递，不加模板 |

只绑定 `127.0.0.1`。端口/模板/长度上限/最小间隔都在 `plugin.toml` 的 `[dsh_bridge]` 段，
首次加载后也会落到插件配置里，可在插件配置面板改（改完重载插件生效）。

## 4. 默认配置

```toml
[dsh_bridge]
port = 48921
template = "（来自 DeepSeek Harness）主人让你把下面这段内容用一两句话精简一下，然后念给他听：\n\n{text}"
max_chars = 4000
min_interval_seconds = 0
```

## 5. 下一步（P0 通过后才做）

- DSH 侧 cordis 插件：挂 `assistant/message` 事件 → 把文本 POST 到本插件（截断 + 节流）；
- 需要定：推什么（整条回复？只要结论？）、什么时候推（每轮？仅长回复？）、推多少（长度上限）。

## 6. 排错

| 现象 | 处理 |
|---|---|
| 端口绑定失败 | 换 `[dsh_bridge] port`（如 48922），重载插件 |
| 校验说入口包名不匹配 | `entry` 改成 `plugin.plugins.dsh_bridge:DshBridgePlugin` |
| 加载了但没反应 | 确认开发模式开着、插件卡片是"运行中"；看 `logs\N.E.K.O_Plugin_*.log` |
| 她没出声 | 先确认 N.E.K.O. 正常语音可用（TTS 配置、音量、是否静音） |
| curl/PS body 中文乱码 | 用上面 Node 版本，或 PS 里先转 UTF-8 字节数组 |
