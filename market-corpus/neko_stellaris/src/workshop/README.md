# 创意工坊发布清单

发布时要填的东西都在这个目录里。名称、描述、标签三者互相独立，别混用：

| 位置 | 名字 |
|---|---|
| 工坊条目名 / 启动器列表 | 星辰喵语 顾问语音包 |
| 游戏内 `设置 → 声音 → 顾问语音` | YUI |

## 1. 描述

| 文件 | 用途 |
|---|---|
| `description.zh-CN.bbcode` | 正文，**可直接整段粘贴**（Steam 用 BBCode，不是 Markdown） |
| `description-bridge-section.bbcode` | 仅当上传的包**含事件桥**（整合版）时，接在正文末尾 |

如果上传的是纯语音包（`standalone` 导出，257 个文件、只有 `Sound` 标签、不含 `common/` 与 `events/`），
就**不要**接那段 —— 那个包里根本没有事件桥，写了就是假的。

## 2. 简短描述（启动器上传窗口里的 `Short description` 字段）

```
把《群星》顾问语音全部换成一只傲娇猫娘的中文录音：覆盖原版 82 个声音名、共 248 条，含多套变体，随机抽取。
```

## 3. 标签

- 纯语音包：`Sound`
- 整合版（含事件桥）：`Sound`、`Gameplay`、`Utilities`

## 4. 封面

启动器的发布流程**自动读取 mod 根目录的 `thumbnail.png`**，不需要手动选文件：

```
<mod目录>/thumbnail.png        512×512 PNG，254 KB
```

上限 1 MiB（超过会警告并跳过），格式接受 `jpeg` / `jpg` / `png`。本机其他工坊 mod 的封面缓存实测为
512×512、520×520、500×500 与 958×682 —— 方形是主流，我们这张符合。

想手动选图的话：把 `thumbnail.png` 拷到别处改个名再选即可。

## 5. 发布前必须做的一件事

**先自己进一次游戏，确认日志干净。** 历史上这条要求不是形式主义：
早先的构建在真机里产生过 55,019 行 `unsupported scope`、32,492 行 `Invalid context switch`，
而离线检查当时是"通过"的。

## 6. 1.0.1 的发布前核查（已完成，2026-10-20）

用本地安装的 1.0.1 包实跑一局后的日志：

| 检查 | 结果 |
|---|---|
| `error.log` 搜 `neko_bridge_events` | **0 行**（此前 32,492） |
| 搜 `Invalid context switch` | **0 行** |
| 搜 `unsupported scope` | **0 行**（此前 11 行，且那 11 行是原版 `fircon.5035`） |
| `game.log` 搜 `NEKO\|v1\|` | 177 行 / 13 种事件 |
| `survey_completed_planet` | **89 行**（此前 32,383 次报错、几乎从不执行） |
| `first_contact_finished` | **5 行**（此前 0，钩子静默坏掉） |

也就是说这套资产在真机里是干净的，可以发布。下次改完资产仍要做这一步：

```
文档\Paradox Interactive\Stellaris\logs\error.log   → 搜 nekko_stellaris / Invalid context switch，应为 0 行
                                            → 搜 No sound scripted / unsupported scope，应为 0 行
文档\Paradox Interactive\Stellaris\logs\game.log    → 搜 NEKO|，应能看到事件（含心跳）
```

