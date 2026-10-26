# 部署与安装同步（kards_companion）

本文件说明 `scripts/sync_installations.py` 的用途、边界和正式发布门禁。脚本默认
`--check` 只读；任何写入都必须显式 `--apply`。脚本不启动宿主、不访问外网，也不
读取或修改目标 `config`、运行时状态、身份文件。

`scripts/bundle_manifest.py` 提供独立的发行资源清单生成/校验；`sync_installations.py`
只有显式 `--bundle-manifest` 才读取它，默认语义保持原状。

## 目标目录

源目录固定为脚本父目录的父目录（即本仓库根目录）：

| 通道 | 安装副本 |
| --- | --- |
| dev | `%LOCALAPPDATA%\N.E.K.O\plugins\kards_companion_dev` |
| dev | `%LOCALAPPDATA%\N.E.K.O\.neko-plugin-installations\plugins\kards_companion_dev` |
| formal | `%LOCALAPPDATA%\N.E.K.O\plugins\kards_companion` |
| formal | `%LOCALAPPDATA%\N.E.K.O\.neko-plugin-installations\plugins\kards_companion` |

`LOCALAPPDATA` 缺失或不是绝对路径时直接拒绝，绝不退化成相对路径。

## 复制范围

只复制以下明确 allowlist 中的项目文件/包：

```text
__init__.py  plugin.toml  README.md  requirements.txt(如存在)
adapters/ delivery/ policy/ runtime/ state/ knowledge/ perception/
ui/ tests/ docs/ scripts/
```

递归复制时跳过 `__pycache__`、`.pytest_cache`、`.pyc`、`env`、`data`、`config`、
`model/models`、`dataset`、`outputs`、`records`。因此：

- `models/` 模型权重不复制、不覆盖；
- 空 `templates/` 不复制；
- 目标侧 `config/`、运行时状态、身份文件、未知文件一律保留；
- 目标侧存在但源侧没有的文件只记录为 `stale`，不删除、不替换；
- 目标目录本身及其父链只要出现 symlink / junction / reparse point 就拒绝；
- 目标已有 `plugin.toml` 时，`[plugin].id` 必须与当前通道一致。

源树中出现 symlink / junction / reparse point 同样拒绝，避免把链接内容当作源码复制。

### dev 清单派生

dev 的 `plugin.toml` 从源清单派生，只替换三个字段：

```text
[plugin].id    = kards_companion_dev
[plugin].name  = KARDS 猫娘陪玩（dev）
[plugin].entry = plugin.plugins.kards_companion_dev:KardsCompanionPlugin
```

不会把源清单里的正式 id 复制到 dev。`version` 当前保持源值；`6.0.0` 属于后续
最终集成时的版本写入，本脚本现在不改版本。

### 内置卡牌库

复制前先解析 `knowledge/cards_builtin.json` 的 JSON shape，再校验：

- `cards` 为列表且长度为 `1083`；
- 文件 SHA-256 必须为
  `dc06efb1180646e15953b73ccb9dc0683b83676bd0062d1b8ff5831771ec4661`。

任一条件不满足即拒绝，不写入目标。未来若上游卡库更新，必须显式修改脚本常量并
重新验收，不能静默漂移。

## 完整分发包清单（显式 opt-in）

`scripts/bundle_manifest.py` 负责发行 payload 的清单生成与校验；它和源码同步是
两条独立通道。只有显式给同步脚本加 `--bundle-manifest` 时才会读取清单并合并
payload；未加时保持上面的原 allowlist 行为不变。

清单只覆盖以下位置：

| 位置 | 内容 |
| --- | --- |
| `worker/` | 独立 Python worker 与包内运行时入口 |
| `vendor/` | 固定版本第三方运行库（含 `vendor/python` 与 `vendor/python/Lib/site-packages`） |
| `assets/` | 卡图与 `assets/cards/manifest.json` |
| `models/` | 检测模型与 `models/manifest.json` |
| `ocr_models/` | OCR 模型/字典与 runtime marker |
| 根目录 `THIRD_PARTY_NOTICES` | 第三方声明（`.md` / `.txt` / 无后缀） |

路径任意一层出现 `training`、`dataset`、`Videos`、`recording`、`data`、`config`、
`.pytest_cache`、`whl`、`keys` 都拒绝；`__pycache__`、`*.pyc` 在扫描时跳过、不会
进入清单，若清单显式列出仍拒绝。唯一例外是固定包内运行时目录
`vendor/python/Lib/site-packages`：该目录本身及其中的 `data/`、`config/` 包资源
允许（例如 OpenCV 数据文件）；其他禁用名、缓存和 wheel 仍拒绝。该目录之外的
任何 `site-packages` 仍拒绝。扫描和校验都不跟随 symlink / junction /
reparse point，发现即拒绝。

以下 marker 必须存在且非空，否则生成、校验和 apply 都 fail closed：

| 名称 | 路径 |
| --- | --- |
| Python worker marker | `worker/entry.py` 或 `worker/bootstrap.py` |
| OCR runtime | `ocr_models/runtime.json` 或 `worker/models/ocr/runtime.json`（JSON object） |
| assets manifest | `assets/cards/manifest.json`（兼容 `assets/manifest.json`，JSON object） |
| third-party notices | `THIRD_PARTY_NOTICES` / `.md` / `.txt` |

`models/kards_det.onnx` 是可选组件。缺失时清单和报告记录
`optional.kards_det.state = "missing"`、`blocks_ocr = false`，不阻塞 OCR bundle；
required 四项不受影响。存在时按普通文件纳入 size/SHA-256 校验；存在但未列入
清单只产生 warning，不作为 OCR 失败条件。

`vendor/python/python.exe` / `vendor/python/Lib/site-packages` 不属于 required 四项，
但缺失时清单会写 `vendor_python_runtime_missing` /
`vendor_site_packages_missing` warning；worker 自检会把 vendor 缺失作为 blocker，
因此完整离线包仍必须构建 vendor。

每个文件记录 `path`、`size`、`sha256`、`component`、`version`、`license`。
component 取允许目录或最长的显式前缀；version/license 只来自
`--component PREFIX=VERSION=LICENSE`。未提供时 `license` 为 `NOASSERTION`，
vendor 的 version 为 `unversioned`。工具不会从 README、文件名或 URL 猜测或
声称许可证，也不会创建空 vendor 文件。

```bat
py -3.11 scripts\bundle_manifest.py --generate
py -3.11 scripts\bundle_manifest.py --generate --output bundle-manifest.json
py -3.11 scripts\bundle_manifest.py --check
py -3.11 scripts\bundle_manifest.py --check path\to\bundle-manifest.json
```

`--generate` 默认只输出 JSON 到 stdout，不写文件；`--output` 也只在全部 required
通过后原子写入。`--check` 校验 schema、路径白名单与禁用项、reparse、逐文件
size/SHA-256、未列出的 payload 文件、required marker 和 manifest fingerprint；
可选 `kards_det` 缺失不会使 `ok=false`。

### 同步时启用清单

```bat
py -3.11 scripts\sync_installations.py --check --bundle-manifest
py -3.11 scripts\sync_installations.py --check --bundle-manifest path\to\bundle-manifest.json
```

无参数值时读取 `<repo>\bundle-manifest.json`。manifest 缺失、不可读、schema
不符、required 缺失、哈希不符、路径不安全或出现 reparse，都会在写入前拒绝；
`--apply` 同样如此，不会退回到普通源码同步。清单文件按原字节加入本次文件集合；
源码 allowlist、`plugin.toml` 和 dev/formal 身份派生逻辑保持不变。

### 备份根与空间预检

`--apply` 默认备份到 `%TEMP%\neko-kards-sync-<channel>-<timestamp>`，可用
`--backup-root <绝对路径>` 指定父目录。新增的备份预检只做 stat/磁盘空间检查，
不读取目标 `config/` 内容（既有的写后 `config/` hash 比较保持原行为）：

- 备份根及其父链必须是普通目录，不允许 symlink / junction / reparse；
- 预计备份字节 = 将被覆盖且允许备份的目标文件大小之和；来自已校验清单的
  `models/` 文件即使位于旧 SKIP 中也纳入备份；
- 可用空间必须 ≥ 预计备份字节 + 16 MiB。

`--check` 只报告 `backup_preflight`，不因空间不足失败；`--apply` 不满足时直接
拒绝，不写目标文件。写后 `config/` hash 比较保持原行为。

### 当前真实包状态（未 apply）

当前仓库仍不满足完整包 required：worker marker 已由 `worker/bootstrap.py` 满足，
`THIRD_PARTY_NOTICES` 存在；但 OCR runtime 和 assets manifest 缺失，`vendor/`
（含 `vendor/python/Lib/site-packages`）、`assets/` 卡图与 `ocr_models/`、真实
许可证元数据均不存在；`models/kards_det.onnx` 缺失。`__pycache__`/`*.pyc` 会被
扫描跳过，不影响该结论。bundle 生成会被拒绝，不写 `bundle-manifest.json`，也不
执行任何安装同步。禁止用空文件、假 vendor、假 license 或首次运行下载来“通过”
门禁。

> 集成说明：`bundle-manifest.json` 只有一套 schema `neko-kards-bundle-manifest/1`，
> 由本工具生成和校验；根目录 `bundle-manifest.schema.json` 描述这一套 schema。
> `worker/package_check.py` 只读该清单并兼容其形状（files/components/required/
> optional/integrity），绝不写入或覆盖。worker 自身的 runtime/dependencies/
> models 声明放在可选的 `worker-runtime.json`（`kards-worker-runtime/1`），
> 不写同名 `bundle-manifest.json`；两种 schema 不得写到同一路径。

## 只读检查与写入

```bat
py -3.11 scripts\sync_installations.py --help
py -3.11 scripts\sync_installations.py --check
py -3.11 scripts\sync_installations.py --check --channel formal
py -3.11 scripts\sync_installations.py --check --bundle-manifest
py -3.11 scripts\sync_installations.py --check --bundle-manifest path\to\bundle-manifest.json
```

`--check` 输出 JSON 到 stdout，包含每个目标的 `changes`（含 source/target hash）、
`stale`、`issues`、`config_hashes`、源/目标差异数量、`bundle` 和
`backup_preflight`；不写任何备份或报告文件。

dev 写入：

```bat
py -3.11 scripts\sync_installations.py --apply --channel dev
py -3.11 scripts\sync_installations.py --apply --channel dev --bundle-manifest
```

正式写入只在真实宿主验收通过后执行：

```bat
py -3.11 scripts\sync_installations.py --apply --channel formal ^
  --host-verified --acceptance-report <验收报告.json> --bundle-manifest
```

写入前请关闭 N.E.K.O。脚本本身不会启动宿主，也不会停止宿主。

每次 `--apply`：

1. 先做完整 preflight（源码、清单、目标、backup root/空间）；任何 issue 都拒绝，
   不写目标文件；
2. 备份“将变更且已存在于目标”的文件到
   `%TEMP%\neko-kards-sync-<channel>-<timestamp>\backup\<target-label>\`（可用
   `--backup-root` 改父目录）；新文件没有旧内容，不需要备份；
3. 备份前对目标侧将变更的 `.py` 做静态 secret 检测；任何命中或无法扫描都拒绝
   备份和写入。JSON/卡库/其他资产不按密钥扫描，避免把 `assets`/卡牌字段误判；
4. 每个文件先在目标同目录写临时文件，再 `os.replace` 原子替换；
5. 写后再次检查 allowlist 差异和 `config/` hash，必须全部通过；
6. 报告只写到上述备份目录的 `report.json`；check 模式不写报告。

目标中未知文件始终不删除；它们会出现在 `stale` 列表里。目标 `config/` 内容不会被
打印，只记录 hash 并在写后比较。

## 正式门禁（formal）

`--host-verified` 只是执行者的声明，不是证明。脚本在 `--apply --channel formal` 时
还要求真实验收证据，并检查：

- `plugin_loaded` 为 true；
- `delivery.submitted` 为 true；
- `sdk.accepted` 为 true；
- `source_manifest` / `artifact` fingerprint 与当前源 artifact 完全一致；
- 如报告带 `plugin_id`，必须是 `kards_companion_dev`；
- 两个 dev 安装副本已与当前源同步，否则拒绝晋级 formal。

支持的证据格式（二者择一，不能同时给）：

1. `--acceptance-report`：schema 以 `/1` 结尾的真实报告，例如

```json
{
  "schema": "neko-plugin-acceptance/1",
  "plugin_loaded": true,
  "plugin_id": "kards_companion_dev",
  "delivery": {"submitted": true},
  "sdk": {"accepted": true},
  "source_manifest": {"fingerprint": "<artifact fingerprint>"},
  "artifact": {"sha256": "<artifact fingerprint>"}
}
```

2. `--verified-manifest`：简化扁平 JSON，至少包含

```json
{
  "plugin_loaded": true,
  "delivery_submitted": true,
  "sdk_accepted": true,
  "source_manifest_fingerprint": "<artifact fingerprint>",
  "plugin_id": "kards_companion_dev"
}
```

如果真实宿主验收报告没有上述 schema/字段，formal 会被拒绝并给出原因（例如
`acceptance_schema_not_v1`、`plugin_loaded_not_true`、
`source_manifest_fingerprint_missing`、`artifact_hash_mismatch`），不会写入任何
formal 文件。不得伪造验收报告；证据必须来自真实宿主运行。当前
`scripts/host_acceptance.py` 生成的 `neko-host-acceptance/1` 报告不包含
`plugin_loaded`、`delivery`、`sdk`、`source_manifest` 字段，因此不能直接作为
formal 证据；需要由真实验收流程补齐上述字段后再生效。

bundle 清单只证明包体完整性，不能替代宿主验收。当前没有真实宿主验收证据，
formal 仍会被上述门禁阻止；在补齐证据前不要执行 formal apply。

### artifact fingerprint

为保证 dev 验收结果可复用到 formal，fingerprint **不包含** 通道相关的
`plugin.toml`，算法为：

```text
sha256( 对每个 allowlist 文件按相对路径排序:
        utf8(relpath) + b"\0" + utf8(sha256(file)) + b"\n" )
```

代码、文档、测试、脚本等都在内；`plugin.toml` 因 id/name/entry 随通道变化而排除。
正式验收报告里的 `source_manifest` 与 `artifact.sha256` 都应使用该值。

## 退出码

| 退出码 | 含义 |
| --- | --- |
| 0 | check 完成，或 apply 成功 |
| 2 | preflight/源校验/secret/卡库/目标安全检查失败，未写目标 |
| 3 | formal 门禁失败，未写目标 |
| 4 | 写入后校验失败（需人工检查报告） |

## 最终集成后的顺序

1. 构建 `worker/`、`vendor/`、`ocr_models/`、`assets/`（含真实卡图）、
   `models/` 与 `THIRD_PARTY_NOTICES`，并为第三方组件提供显式
   `--component PREFIX=VERSION=LICENSE`；
2. `bundle_manifest.py --check` 通过后，用
   `sync_installations.py --check --bundle-manifest` 确认 bundle 与源码差异；
3. `--apply --channel dev --bundle-manifest`，确认两个 dev 副本一致；
4. 关闭/重启宿主，跑真实 dev 验收，取得带 `plugin_loaded`、
   `delivery.submitted`、`sdk.accepted` 和当前 fingerprint 的真实证据；
5. 证据通过后，才执行 formal apply；仍然保留目标 config/state/identity；
6. 版本 `6.0.0` 在最终集成时另行写入，本脚本不越权修改。

本次改动只实现清单与门禁，未生成真实清单，未执行 dev/formal apply，也未启动
宿主或下载任何资源。
