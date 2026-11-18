# plugin-forge 接入线 · 契约门禁（forge-contract-gate）

> 正典：`src/skills/plugin-forge/scripts/check_contract_approval.py`（实现）、
> `src/skills/plugin-forge/references/contract-approval.schema.json`（形状）、
> `src/skills/plugin-forge/references/workflows.md`（何时送审）。
> 本文件只写「什么时候必须过、怎么跑、失败怎么读」。

## 什么时候必须过

**写代码之前**，方案要先过契约审议。以下情况一律不许跳过：

- 新建插件 / 新增入口 / 改 `plugin.toml` 的必填字段；
- 改受保护面：源码、`ui/*.html`、`*.svg`、`.github/workflows/*.yml`、
  `hooks.json`、构建文件（`Dockerfile*`、`Makefile*`）；
- 改 schema / 契约 / 权限面；
- 宿主破坏性变更后的机队修复。

纯文档、纯文案不动代码的改动不触发门禁（正典铁律 3 的口径：纯改文案不算写代码）。

## 怎么走

1. 写方案 → 契约审议师出准奏文件 `contract-approval.json`（放工作区根）。
2. 跑门禁：

   ```bash
   python <FORGE>/skills/plugin-forge/scripts/check_contract_approval.py \
     --changed            # 或 --staged，或显式列文件
   ```

3. 退出码：`0` 准奏覆盖全部目标；`1` 有目标不在 scope / 已过期 / 方案改过；
   `2` 用法错误（没有待检查文件、准奏文件读不了）。

## 准奏文件的最小形状

```json
{
  "schema": "plugin-forge/contract-approval@1",
  "task": "给 sys_monitor 加静音时段",
  "platform": "neko@0.9",
  "decision": "approved",
  "issued_by": "plugin-forge-contract",
  "issued_at": "2026-11-18T13:00:00+08:00",
  "expires_at": "2026-12-02T00:00:00+08:00",
  "scope": ["src/**", "plugin.toml"],
  "verify_commands": ["python plugin/neko_plugin_cli/cli.py check sys_monitor"]
}
```

字段含义与全部约束以 `contract-approval.schema.json` 为准。要点：

- `scope` 用项目根相对路径，只认两种通配（`*` 与 `**`）；
- 不在 scope 里的目标一律拒，**根外路径**直接拒；
- `decision` 非 `approved` 一律拒；
- 填了 `plan_sha256` 就必须同时有 `plan_file`，且方案文件改动后旧准奏立刻失效；
- `expires_at` 到期即失效。

## 一条容易误读的口径

**`verify_commands` 只验「形状可解析」，不执行。** 门禁只核命令能分词、引用的文件真在工作区，
不会真的跑一遍（跑就是执行别人的代码）。所以：

- 别把「门禁过了」当成「验收命令跑过了」；
- 交付证据仍要按 `forge-flow.md` 的硬格式贴：命令 + 退出码 + 输出末 20 行；
- `-r requirements-dev.txt` 这类参数连存在性都不核（`.txt` 不在后缀表里）——这是已知边界。

## 去哪查

| 想知道 | 读 |
|---|---|
| 准奏文件的全部字段与约束 | `src/skills/plugin-forge/references/contract-approval.schema.json` |
| 示例 | `src/skills/plugin-forge/references/contract-approval.example.json` |
| 门禁实现与判据注释 | `src/skills/plugin-forge/scripts/check_contract_approval.py` |
| 什么时候送审、封驳怎么重拟 | `src/skills/plugin-forge/references/workflows.md` |
| 契约制度模型（为什么这么设计） | `src/skills/plugin-forge/references/constitution.md` |

## 运行时验证（与门禁配套）

契约管「方案能不能做」，运行管「做出来真能跑」。交付前跑正典的干装载检查：

```bash
python <FORGE>/skills/neko-plugin-sandbox/scripts/dry_load.py <插件目录> --root <沙箱根>
```

读「模式：」那一行：`real-import` 才算真装载；`ast-fallback` 是降级（证明不了能 import）；
`blocked` 是环境阻断（凭据过滤判据缺失，没核过）。后两种都不许汇报成「通过」。
