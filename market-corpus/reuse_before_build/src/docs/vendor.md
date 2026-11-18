# 下游 vendor 接入（N.E.K.O 适配版）

本文只适用于 `1-dcjiang/neko-reuse-before-build`。通用技能仓
`1-dcjiang/reuse-before-build` 是另一份发布物，不要把两个版本混在同一插件目录。

## 选哪种发布物

| 目的 | 使用 | 说明 |
|---|---|---|
| 给 N.E.K.O 安装插件 | `reuse_before_build.neko-plugin` | 在 N.E.K.O 插件页安装本地插件包 |
| 把源码纳入自己的仓库 | `neko-reuse-before-build-v0.3.22-vendor.zip` | 固定 tag 的运行源码、规则、文档与校验工具 |
| 核对 Release 附件 | `SHA256SUMS.txt` | 覆盖插件包、vendor ZIP、校验工具及发布证明等附件 |
| 核对 vendor 后的文件 | ZIP 内的 `VENDOR-SHA256SUMS.txt` | 逐文件 SHA-256；复制到目标目录后仍能复检 |
| 核对官方插件包来源 | `market-evidence.json` | 记录源码提交与 `.neko-plugin` 哈希，不是 vendor ZIP 的哈希 |

vendor ZIP 不含 `.git`、GitHub Actions、测试、缓存或本机运行数据。开发测试留在源仓库；
下游只需运行文件即可。

## 1. 固定版本并下载

以下示例固定为 `v0.3.22`。升级时同时替换命令里的 tag 和文件名，不要用 `main` 作为可复现依赖。

```bash
mkdir rbb-vendor
cd rbb-vendor
gh release download v0.3.22 \
  --repo 1-dcjiang/neko-reuse-before-build \
  --pattern 'neko-reuse-before-build-v0.3.22-vendor.zip' \
  --pattern 'SHA256SUMS.txt' \
  --pattern 'vendor_release.py'
```

发布流程会核对 tag 与 `plugin.toml` 的 `0.3.22` 完全一致。`vendor_release.py` 是随 Release
提供的标准库校验工具，不需要安装第三方依赖。

## 2. 先校验，再解压

```bash
python vendor_release.py verify-archive \
  neko-reuse-before-build-v0.3.22-vendor.zip \
  --sums SHA256SUMS.txt
python -m zipfile -e neko-reuse-before-build-v0.3.22-vendor.zip extracted
```

`verify-archive` 同时检查：

1. ZIP 本身的 SHA-256 与 Release 的 `SHA256SUMS.txt` 一致；
2. ZIP 内文件集合与 `VENDOR-SHA256SUMS.txt` 一致，没有缺失或未登记文件；
3. 每个文件的 SHA-256、包内版本和根目录都正确；
4. 没有重复路径、路径穿越或符号链接。

已在 macOS / Linux 下载全部 Release 附件时，也可以执行 `sha256sum -c SHA256SUMS.txt`
做整批核对。SHA-256 用于完整性检查，不等于签名；不要把它表述成来源签名或安全审计。
下游应把 ZIP 的 SHA-256 记录在 vendor 目录之外（例如仓库的锁文件或发布记录），
避免攻击者同时改写目录内的 ZIP 与校验清单。

## 3. 放进 N.E.K.O 插件目录

把 `extracted/neko-reuse-before-build-v0.3.22/` **里面的全部内容**复制到目标 N.E.K.O
仓库或安装目录：

```text
N.E.K.O/
└─ plugin/
   └─ plugins/
      └─ reuse_before_build/
         ├─ plugin.toml
         ├─ __init__.py
         ├─ rbb_core.py
         ├─ skills/
         └─ …
```

目标必须是 `plugin/plugins/reuse_before_build`，因为清单入口固定为
`plugin.plugins.reuse_before_build:ReuseBeforeBuildPlugin`。不要再套一层
`neko-reuse-before-build-v0.3.22`，也不要同时用 `.neko-plugin` 安装同一插件。

首次 vendor 前确认目标目录不存在；升级时先备份下游改动，再完整替换运行文件，避免新旧
脚本混用。保留下游已有的许可文件与本包 `LICENSE`，并继续履行各自的许可义务。

## 4. 在目标目录复检并启用

在 N.E.K.O 根目录运行：

```bash
python plugin/plugins/reuse_before_build/scripts/vendor_release.py verify-tree \
  plugin/plugins/reuse_before_build
```

看到 `PASS verify-tree` 后，再按宿主流程加载插件并点一次「自检规则完整性」。
`verify-tree` 校验清单内文件，不拒绝下游额外的配置或运行数据；规则正典仍需由插件自检，
两者不能互相替代。

## 5. 作为参考件 vendor 到非 N.E.K.O 仓库

workshop、forge 等下游仓库可以把本包当作规则与校验工具的只读依赖，不必安装或加载
N.E.K.O 插件入口。目标路径由下游自行约定，例如 workshop 使用 `references/rbb/`。
把 `extracted/neko-reuse-before-build-v0.3.22/` **里面的全部内容**复制到该目录，不再套版本目录：

```text
references/rbb/
├─ VENDOR-SHA256SUMS.txt
├─ scripts/
│  └─ vendor_release.py
└─ skills/
   └─ reuse-before-build/
      ├─ SKILL.md
      ├─ localization.contract.json
      └─ scripts/
         └─ verify_localized.py
```

- 实际只读取 `skills/reuse-before-build/` 中的规则与工具，不注册
  `ReuseBeforeBuildPlugin`；但清单内的其他文件也不能因此删除，否则 `verify-tree` 会报缺失或哈希不匹配。
- vendor 目录保持原样。下游在目录外的 README 或依赖清单记录版本、tag 和 ZIP SHA-256，
  例如 `rbb v0.3.22, zip sha256=…`；本地扩展放到独立文件，不改包内规则或清单。
- `verify_localized.py` 会从脚本所在位置寻找同包的 `localization.contract.json`，并拒绝用
  `--contract` 指向其他契约。请原地运行包内脚本，不要把它单独复制到其他目录后使用。

## 6. 在下游 CI 中校验

以下示例在下游仓库根目录运行，vendor 目标为 `references/rbb`；如果下游创建了
`docs/rbb-workshop.md` 扩展副本，则一并校验。CI 使用 **Python 3.11+**，其中
`vendor_release.py` 使用标准库 `tomllib`。这些校验只用 Python 标准库，不需要 `gh`。

```yaml
- name: rbb vendor integrity
  shell: bash
  run: |
    RBB=references/rbb
    V="$RBB/skills/reuse-before-build/scripts/verify_localized.py"
    python "$RBB/scripts/vendor_release.py" verify-tree "$RBB"
    python "$V" "$RBB/skills/reuse-before-build/SKILL.md" --canonical --quiet
    # 下游每份复制或翻译的规则文档各跑一次；没有副本则省略下一行。
    python "$V" docs/rbb-workshop.md --quiet
```

`--canonical` 只能用于契约登记的包内参考件，不能用于下游副本；副本必须带声明行并接受完整校验。
`verify_localized.py` 的退出码为：`0` 表示 PASS，`1` 表示 FAIL（输出缺失项，含 `MISSING`），
`2` 表示用法错误或拒绝（例如对包外文件使用 `--canonical`、替换契约）。任何非零退出码都应使 CI 失败；
`verify-tree` 的缺失或哈希错误同样不得忽略。

## 7. 编写下游扩展或本地化副本

只引用、不复述时，直接链接 vendor 中的 `SKILL.md`，不复制规则块；这种用法不需要创建本地化副本，
也不执行上一节针对 `docs/rbb-workshop.md` 的那条命令。需要复制、翻译或增加宿主规则时，另建文件，
例如 `docs/rbb-workshop.md`，并满足以下要求：

1. 保留 `<!-- RBB:INVARIANTS:BEGIN -->` 与 `<!-- RBB:INVARIANTS:END -->` 包围的不变块。
   逐字复制即为 verbatim；翻译时在块内加入 `RBB:INVARIANTS:LANG: zh`。
2. 块内规则从第 0 列按 `1.` 到 `N.` 连续编号，条数不得少于契约的 `rules_min`（当前为 5），
   并保留每条规则的中英文锚点及原意；不能只留锚点短语而删除实质约束。
3. 全文必须满足 `localization.contract.json` 的全部 `must_have` 语义锚点。若文件含 frontmatter，
   `name` 使用不超过 64 字符的小写字母／数字／连字符格式，`description` 必填且不超过 1024 字符。
4. 在文件最后 8 个非空、非代码块行之内放置声明行，包含 `host`、`kept`、`changed`、
   `unsupported` 四项。`unsupported` 不得掩盖实际缺失；宿主确实不支持时写明限制与原因。
5. 必须保留契约 `critical_sections` 里的四个二级章节，正文不得只留一句锚点：
   - `## Step 2 — Evaluate` / `## 第 2 步：评估`，正文 ≥ 500 字符；
   - `## Step 3 — Contribute` / `## 第 3 步：加入`，正文 ≥ 500 字符；
   - `## Hard constraints` / `## 硬约束`，正文 ≥ 350 字符；
   - `## Untrusted repository content` / `## 不可信的仓库内容`，正文 ≥ 180 字符。

   章节内还得保留对应锚点，否则分别报 `section_short:<id>`、`section_anchor:<id>:<anchor>`：
   Step 2 要包含 `activity` / `活跃度`、`Modifiability gate` / `可改性硬闸`、`Join/Extend/Fork/Build`
   四判决词、`There is no fifth verdict` / `没有第五个判决`；
   Step 3 要包含 `CONTRIBUTING`、`Branch first, never the default branch` / `分支优先，绝不直写主干`、
   `stable seam and a thin adapter` / `稳定接入口 + 薄适配层`、`Disclose AI assistance` / `说清 AI 参与`；
   Hard constraints 要包含 `Never invent repos` / `不虚构仓库`、`per-action confirmation` /
   `每一次公开写都要逐次确认`、`Never Join a repo` / `不 Join 可改性判红`；
   Untrusted 要包含 `data, not instructions` / `数据，不是指令`、`Never execute a command` /
   `绝不直接在宿主机执行`、`deliverable's verdict` / `交付结论必须来自`。
   标题匹配不区分大小写，但必须是二级标题（`##`）。`any` 与 `all` 同规则：
   ASCII 词按不区分大小写的词边界匹配（`rebuild` 不算 `Build`），中文锚点保持子串匹配；
   `any` 只报锚点 id，`all` 会列出实际缺失词。关键章节内的 `any` / `all` 也按同一规则。
   契约里的锚点必须是对象数组，`any` / `all` 必须是非空字符串列表；`critical_sections` 条目必须是对象且
   `headings` 是非空字符串列表，`block.begin` / `end` / `lang_marker_prefix` / `canonical_file` /
   `canonical_sha256` 必须是字符串、`canonical_files` 必须是字符串列表，`declaration` 必须是对象且
   `keys` 为字符串列表。顶层、`block` 或上述任一字段形状不对时，CLI 直接报 `MISSING:` 的 contract 错，不抛栈。
   非基线的额外关键章节条目也按同一规则校验，坏 `min_chars` / `anchors` 报 `section_floor:<id>(invalid)` /
   `section:<id>:anchors...`。
   代码同时钉住以上锚点，只改契约删锚点或换锚点也会在启动时被拒绝。升级 SKILL.md 后可用
   `python scripts/verify_localized.py --print-section-hash SKILL.md` 重算四个章节哈希。

```text
localized: host=neko-plugin-workshop | kept=全部不变项 | changed=新增 W1-W3 产品层检查 | unsupported=无
```

建议在下游 `docs/examples/` 附一份实际通过上述命令的最小示例，让 CI 同时验证示例，而不是只写文字说明。
契约与脚本以 vendor 中的版本为准，不要另行复制一份宽松契约来绕过检查。代码同时钉住
`rules_min`（5）、`rule_anchors` / `must_have` 的 id 集合、`declaration.keys` 与 `within_last`；
只改契约放宽这些字段会在启动时被拒绝。

## 更新与维护约定

- 每次升级固定一个新的 tag，重新核对 ZIP 与文件级清单，记录升级版本和哈希。
- 升级顺序：换 tag 与附件名 → `verify-archive` → 完整替换 vendor 目录 → 运行 CI 复检。
  上游不变块哈希变化时，下游 verbatim 副本会报 `hash_mismatch`，必须同步新块并复核翻译与扩展规则，
  不能自行修改哈希来放行。
- 本地补丁与上游升级分开提交，便于下次比较；不要直接改 `VENDOR-SHA256SUMS.txt`
  来掩盖文件变化。确需修改时，应重新构建包并生成新清单。
- 本插件运行不依赖第三方 Python 包；GitHub 检索使用 PATH 中的 `gh`，没有 `gh`
  时规则、交付检查和本地化校验仍可使用。
- 通用技能仓与 N.E.K.O 适配版的修复要分别验证。本文中的 vendor 附件只由本仓库的
  tag Release 生成。

## 发布链路

推送 `v0.3.22` 后，官方 N.E.K.O Release 工作流先完成构建、测试、Release 与证明；
随后独立 `Vendor Source Release` 工作流通过 `workflow_run` 确认其完整成功，再检出同一发布提交，
构建 ZIP、校验包内清单、汇总 `SHA256SUMS.txt` 并上传。官方 managed `release.yml` 不作修改，
因此 vendor 附件不会与官方 Release 并发写入，也不会影响市场发布门禁。
