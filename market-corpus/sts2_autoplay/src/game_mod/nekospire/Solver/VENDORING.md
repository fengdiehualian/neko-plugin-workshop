# 内嵌 CombatSolver 引擎的 vendor 机制

本目录（`game_mod/nekospire/Solver/`）里的引擎**不是整棵拷贝**，而是"**按边界取一个闭包，再叠三层本地适配**"。
这份文档说明边界怎么划、适配怎么留、以及将来怎么再同步一次。

配套文件：

| 文件 | 性质 | 作用 |
|---|---|---|
| `VENDORING-RULES.json` | **手写**（唯一事实来源） | vendor 边界、排除清单、必须去 Godot 化的文件、适配规则 |
| `VENDORING-MANIFEST.json` | **生成**（不要手改） | 现状普查：每个已 vendor 文件与上游的逐行比对结果 |
| `game_mod/scripts/sync-vendor.ps1` | 工具 | 读规则 → 生成普查 → 打差距报告；加 `-Apply` 才真正改写源码 |
| `VENDORING-GAP-REPORT.md` | 生成时机同上 | 当前版本与上游的差距与工作量评估 |

---

## 1. 上游与基线

- 上游仓库：<https://github.com/Torch1230/CombatSolver>
- 许可证：**MIT**（`Copyright (c) 2026 Torch1230 and contributors`；Random Foreseer 部分 `Copyright (c) 2026 hotwords123`）
- 上游当前版本：**0.50.1**（commit `6031deb`）
- **内嵌基线：上游标签 `v0.28.0`**（已用逐字比对钉死，证据见下）

### 基线是怎么钉死的

把本地 246 个 vendor 文件与上游各个候选标签逐个逐字比对：

| 上游标签 | 逐字相同 | 命中率 |
|---|---:|---:|
| `v0.27.0` / `v0.27.2` | 171 / 242 | 70.7% |
| **`v0.28.0`** | **199 / 245** | **81.2%** ← 峰值 |
| `v0.28.1` | 197 / 245 | 80.4% |
| `v0.28.2` | 177 / 245 | 72.2% |
| `v0.28.3` | 160 / 245 | 65.3% |
| `v0.29.1` | 155 / 245 | 63.3% |

峰值精确落在 `v0.28.0`，与 README 里"vendored CombatSolver 0.28"的说法一致。**所以合并基线是 `v0.28.0`，不是"某个模糊的 0.28 时代"。**

> 这个基线是三方合并的 `base`，它把"哪些文件能整份取上游新版本、哪些必须人工合并"变成了可计算的事实——见 `VENDORING-GAP-REPORT.md`。

> `Engine/README.md` 记录了更早的来源：`Engine/` 子树最初来自本地 RandomForeseer 分支的 commit `598dce06`，随后改名到 `CombatSolver.Engine` 命名空间。上游 CombatSolver 自己也 vendoring 了同一套引擎，所以按 CombatSolver 的标签追踪仍然有效。

### 前提：上游需要完整历史

三方分析要读 `v0.28.0` 的源码树，所以上游克隆**不能是浅克隆**。补齐：

```powershell
git -C D:\NekoClaw\CombatSolver fetch --unshallow --tags
```

（实测上游共 1659 个提交、109 个标签，`--depth 1` 补成完整历史只占十几 MB。）

---

## 2. 三层结构

### 第 1 层：vendor 闭包（来自上游，尽量零改动）

上游 4 个子树里**不属于独立模组外壳**的部分：

| 子树 | 上游总数 | 本仓库已 vendor |
|---|---|---|
| `Engine/` | 170 | 132 |
| `Prediction/` | 56 | 41 |
| `Runtime/` | 64 | **12**（上游这棵树基本已变成 Godot 外壳） |
| `Search/` | 252 | 61 |

命名空间保持上游原样（`CombatSolver.*`，根命名空间 `CombatSolver`），**不改名**——这样重新 vendor 时不需要做任何重命名变换。

### 第 2 层：本地保留文件（重新 vendor 时**绝不能被覆盖**）

| 文件 | 为什么存在 |
|---|---|
| `Engine/Common/GameRef.cs` | **反射适配层**。上游直接读写游戏私有成员（`card._cloneOf`、`power._amount`、`cost._unused`…），本模组无权这么做，全部改走这里。上游**没有**这个文件。 |
| `EntryShim.cs` | 补上被排除的上游 `src/Api/Entry.cs` 所声明的 `CombatSolver.Entry`（`Logger` / `ModId`），让 vendor 文件**零改动**解析这些引用。 |
| `SolverPolicyEnums.cs` | 补上被排除的 `src/Runtime/SolverSettings.cs` 所声明的 `SolverPotionPolicy` / `BossHpStrategy` 枚举。 |

### 第 3 层：接缝（本模组自己的代码）

| 文件 | 作用 |
|---|---|
| `CombatSolverFacade.cs` | 把上游 `SolverResult` 映射成本模组的 `SolverPlanPayload`（HTTP `/solver/plan` 的合同）。只给建议，**从不出手部署**。 |
| `CombatSolverRuntime.cs` | 从 `NekoComm.ModEntry` 安装引擎运行时与两个 Harmony 隔离补丁。 |

---

## 3. 三条硬约束（决定了边界为什么长这样）

### 约束一：**不能用 Krafs.Publicizer**

`GameRef.cs` 头部记录了实测结论：

> Publicizer 的 `IgnoresAccessChecksTo("sts2")` 在 plain-SDK 插件模组下**不被 mod 加载器承认**（实测：直接 `ldfld` 一个私有字段仍然抛 `FieldAccessException`），RitsuLib 也不做运行时公开化。

**后果**：上游每一处直连游戏私有成员的地方，都必须改写成 `GameRef` 反射调用。这是每次再 vendor 的**主要工作量**。当前基线有 **226 处调用、散在 40 个文件**（见清单里的 `uses_gameref` 字段）。

### 约束二：**不能用 Godot.NET.Sdk**

本工程是 `Microsoft.NET.Sdk`（原因见 `Game/DanmakuOverlay.cs` 注释：游戏数据目录不带 Godot 源生成器，继承 `_Process` 的 C# 节点不会被接线）。

**后果**：任何 `using Godot;` 或继承 `Node` 的上游文件都不能编进来。判别方法就是这两个特征串。

### 约束三：**工具链是 .NET 9 / C# 13**

上游如果用 C# 14 语法（例如 C# 14 的 extension blocks），要改写成 C# 13 等价写法。`Engine/README.md` 记录了历史上做过同类改写。

---

## 4. 边界怎么划（分类规则）

判据来自上游自己的 `docs/ARCHITECTURE.md` 模块归属表：

| 上游模块 | 处置 | 理由 |
|---|---|---|
| §3 Search（搜索大脑） | **vendor** | 引擎本体 |
| §4 `Engine/InCombat/Simulation/`、`Engine/InCombat/Mirrors/`、`Prediction/` | **vendor** | 模拟与预测，引擎本体 |
| §2 `Runtime/` 中的 `CombatRootSnapshot`、`ContinuationStamp`、`SearchMemoryPressureSignal` 等 | **vendor** | 主线程根捕获/续用戳/内存压力信号，嵌入方也需要 |
| §2 `Runtime/` 中的 `Entry.cs`、`SolverController*`、`SolverDispatcher`、`SolverSettings`、`PlayerTurnSetupPatches`、`NativeChoiceRuntime` | **排除** | 独立模组的生命周期/控制器/调度器/原生回合接线；本模组用 `CombatSolverFacade` 自己驱动 |
| §5 UI（`SolverOverlay*`） | **排除** | 本模组用自己的游戏内弹幕渲染 |
| §6 Testing（237 个文件） | **排除** | 测试宿主不随引擎分发 |
| `Api/`、`Diagnostics/`、`Replay/`、`Strategy/` | **排除** | 独立模组的外壳/网络展柜/缺陷报告/性能录制 |
| **特例**：Search 核心的 partial 分片若含 Godot 调用 | **vendor 但要先去 Godot 化** | 它们是 `CombatBeamSolver` 的一部分，排除会引发大量 missing-type 连锁错误（实测过一次，炸出 47 类错误） |

具体的文件名模式、排除清单与"必须去 Godot 化"清单，**以 `VENDORING-RULES.json` 为准**——不要在这份文档里找权威答案，改动只改那个 JSON。

---

## 5. 再同步一次的标准流程

核心思路：**用 `v0.28.0` 当基线做三方合并**，而不是"逐文件比 0.50.1"。前者能把工作量算清楚，后者会被"代码在文件之间搬家"误导。

```powershell
# 0) 拿到上游 + 补齐历史（三方分析需要标签 v0.28.0）
git clone https://github.com/Torch1230/CombatSolver.git D:\NekoClaw\CombatSolver
git -C D:\NekoClaw\CombatSolver fetch --unshallow --tags

# 1) 只读差距报告 —— 安全，不动任何源码
#    报告会给出：可整份取上游的文件数 / 必须人工合并的冲突文件清单 / 新增核心文件数
game_mod\scripts\sync-vendor.ps1

# 2) 按报告处理：
#    a. 「与 base 逐字相同」的文件 -> 直接用上游新版本覆盖（零冲突）
#    b. 「人工三方合并」的 38 个文件 -> 逐个把上游改动合进我们的改编版，保住 GameRef 适配
#    c. 「保留我们的」8 个文件 -> 不动
#    d. 5 个 Search 核心 -> 去 Godot 化后编入
#    e. 新核心文件 -> 按规则纳入（其中含私有成员访问的要改走 GameRef）

# 3) 真正执行（会先备份到 game_mod/build/vendor-backup/Solver-<时间戳>.zip）
game_mod\scripts\sync-vendor.ps1 -Apply

# 4) 从备份里取回 5 个本地保留文件（脚本刻意不自动做，避免静默用错版本）

# 5) 按编译错误逐类适配，直到零错误：
#    CS0122/CS1061  -> 改走 GameRef
#    CS0246/CS0234  -> 排除引用方，或在 Shim 里补声明
#    Godot/Node     -> 去 Godot 化

# 6) 重新生成普查并跑测试
game_mod\scripts\sync-vendor.ps1
dotnet build game_mod\nekospire\nekospire.csproj -c Release
python -m pytest tests -q
```

### 完成标准

见 `VENDORING-RULES.json` 的 `definition_of_done`。核心是：**Release 编译零错误 + 现有测试无新增失败 + 清单里本地独有文件仍只有 `GameRef.cs` + README 版本号与许可证描述与上游一致。**

### ⚠️ 本机**验证不了**的部分

这台机器上**没有游戏可跑**。任何同步动作最多只能保证"**编译通过 + 测试通过**"，**无法验证求解器在真实对局里的行为**。

求解器是本模组最要命的部件（它决定你打哪张牌）。一次未被发现的语义回归，可能让胜率**悄悄**变差而日志里看不出异常。**所以每次同步后必须由作者在游戏里实测。**

---

## 6. 已知的历史教训（避免重犯）

1. **"含 Godot 就排除"这条规则不能无脑套到 Search 上。** 曾经这么干过，把 `CombatBeamSolver.Phases.cs`、`Retention.cs`、`BeamRetentionPolicy.cs` 等 partial 分片砍掉，立刻炸出 47 类缺失错误。这些文件是搜索大脑本体，只能去 Godot 化，不能排除。
2. **逐文件差异比对会被"代码在文件之间搬家"误导。** 例如 `CombatBeamSolver.Expansion.cs` 上游 306 行、本仓库 3430 行，而 `CyclePlanning.cs` 正好相反——说明代码被重新切分过。可靠的办法是看**类型/成员**层面，或者干脆让编译器来判断。
3. **比较文件时一定要先规范化换行符与 BOM。** 否则会出现"245 个文件全都不同"的假象——实际上其中 54 个是逐字相同的，只是 CRLF/LF 不同。
4. **PowerShell 脚本必须存成 UTF-8 with BOM。** Windows PowerShell 5.1 默认按 GBK 读 `.ps1`，无 BOM 的中文注释会被撕碎并引发语法错误（本脚本踩过一次）。生成的 JSON 则相反，写成不带 BOM 更便于其它工具读取。
