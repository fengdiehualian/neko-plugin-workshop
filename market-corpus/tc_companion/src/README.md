# 图灵完备助手（tc_companion）

给《Turing Complete》用的 N.E.K.O. 陪玩插件：读手册、读关卡测试、写 ISA/汇编、检查与部署电路存档、按进度验收。

## 反推到的事实

游戏是 **Nim 引擎 + Godot 启动器**（Steam appid 1444480）。`circuit.data` 是版本化二进制（当前玩家存档首字节 `0x0f`，旧关卡文件是 `0x0d`）；`spec.isa`、`new_program.asm`、手册、关卡测试都是文本。

| 位置 | 内容 |
|---|---|
| `%APPDATA%\Turing Complete\settings.txt` | 当前关卡、架构 |
| `%APPDATA%\Turing Complete\levels.txt` | 每关完成状态与分数 |
| `...\schematics\<level>\<name>\circuit.data` | 电路（二进制） |
| `...\schematics\<level>\<name>\spec.isa` / `new_program.asm` | 指令集定义 / 汇编程序 |
| `<game>\asset\manual\**\title.txt` + `doc.txt` | 内置手册 |
| `<game>\campaign\<level>\meta.txt` / `test.si` / `ui.txt` | 关卡定义与测试 |

## 入口

| 入口 | 作用 |
|---|---|
| `tc_status` | 当前关卡、架构、进度、电路数量 |
| `tc_docs` | `list` / `search` / `read` 手册；`level` 看关卡 meta/test/ui |
| `tc_code` | `list` / `read` / `write` / `validate` ISA 与汇编 |
| `tc_circuit` | `list` / `info` / `deploy` 电路存档 |
| `tc_accept` | `check` / `snapshot` / `test` |

`validate` 会检查：指令名是否在 `spec.isa` 里、标签是否重复或未定义、所有指令位宽是否一致。

## 电路能力边界

`circuit.data` 是私有二进制格式，插件**不会凭空生成元件或新连线**。它能做的是：

- 读取并展示电路版本、大小、内嵌的端口/标签字符串；
- 把你已经保存过的某个关卡电路，整份复制到目标关卡（先备份）。

这是有意保守的选择：乱改二进制会直接毁掉存档，而且无法验证。

## 使用前提

- 写代码/部署电路前先退出游戏（游戏打开关卡时会覆盖文件）。
- 部署后重新进入关卡让游戏读取新存档。

## 自检

```powershell
python tc.py
```
