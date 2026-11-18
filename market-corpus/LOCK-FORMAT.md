# corpus.lock 格式约定（v0）

> 面向**外部 skill / 第三方维护者**：这份文档足以让你在不读实现的前提下，
> 为一份上游语料写出一条锁条目，并自己实现一个等价的校验器。
>
> 判据的唯一事实源仍是 `workbench/corpus-lock.cjs` 与 `workbench/fetch_corpus.cjs`。
> 本文与代码冲突时以代码为准，并请回来改本文。

## 1. 锁回答什么问题

仓库里现在这一份语料，到底来自上游哪个 commit、哪一份字节。

所以每条锁四样东西：**上游仓库、commit、归档哈希、落盘树的哈希**。
树哈希对「过滤后实际落盘的文件」计算，因此它同时盯住两件事——上游内容变了，
以及**过滤规则变了**（后者归档哈希看不见）。

## 2. 文件位置与顶层结构

`market-corpus/corpus.lock.json`：

```json
{
  "schema": 1,
  "algo": "tree-sha256-v0",
  "note": "…（说明文字，校验器忽略）",
  "entries": { "<slug>": { … } }
}
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `schema` | number | **当前是 2**。读取器必须同时接受 1（读时迁移字段名）与 2；其他值直接报错，不猜。 |
| `algo` | string | **必须是 `tree-sha256-v0`**。不认识直接报错。 |
| `entries` | object | 键是 slug，值是条目。 |
| `note` | string | 可选，人类可读。 |

`slug` 必须匹配 `^[a-z0-9_-]+$`（目录名即 slug，不允许大写与路径片段）。

## 3. 条目字段

### 3.1 必填

先分清三档，别把「手写时要给」当成「校验器会查」：

| 档 | 要求 |
|---|---|
| **手写时必填** | `source`、`kind`、`ref`、`anchors`。`--update` 不传 `--ref` 时按锁里的 `commit` 重抓；传了才解析 ref。`anchors` 用于在写锁前核对新内容。 |
| **`--update` 之后必填** | `commit`、`computedHash`、`files`、`archive_sha256`、`updated_at`。由工具写，不要手改。 |
| **校验器强制**（`--locked` / `--verify-only`） | slug 走 `^[a-z0-9_-]+$`；`source` 必须 `owner/repo`；`commit` 必须 40 位十六进制；`computedHash` 必须 64 位十六进制；`files` 必须是数字；`source_type: "git"` 必须带 `ref`（用于识别 ref 被移动；抓取以 `commit` 为准）。缺任一项报「锁条目不合规」，不会抛 TypeError。 |

| 字段 | 类型 | 含义 |
|---|---|---|
| `source` | string | 仓库标识 `owner/name`（GitHub）。**不带协议与 `.git`**。 |
| `kind` | string | `plugin`（市场插件）或 `forge`（工具仓）。缺省按 `plugin` 理解。 |
| `ref` | string | 抓取用的 ref：tag（`v0.3.22`）或分支名。**git 来源必填**，zip 来源也建议写。 |
| `commit` | string | 40 位十六进制 commit sha。**校验以它为准。** |
| `archive_sha256` | string | 归档文件的 sha256（64 位十六进制）。**不符只警告**，见 §6。 |
| `computedHash` | string | 落盘树的 sha256（64 位十六进制）。**不符即失败。** |
| `files` | number | 树里的文件数。用于失败时给人看规模差异。 |
| `anchors` | array | 语义锚点，见 §5。 |
| `updated_at` | string | ISO 时间戳，`--update` 自动写。 |

### 3.2 可选

| 字段 | 类型 | 含义 |
|---|---|---|
| `source_type` | string | `zip`（默认，市场 codeload zip）或 `git`（工具仓）。 |
| `archive_format` | string | `zip`（默认）或 `tar`（git 来源）。与 `source_type` 对应。 |
| `version` | string \| null | 上游版本号。 |
| `version_source` | string \| null | 版本是从哪个文件读到的：`plugin.toml` / `pyproject.toml` / `SKILL.md` / `plugin.json`。 |
| `release` | object | 发布镜像信息（仅市场插件可能有），见 §3.3。 |

### 3.3 `release` 块（可选）

```json
"release": {
  "tag": "v0.3.22",
  "asset": "neko-xxx-v0.3.22-vendor.zip",
  "asset_sha256": "…",
  "checksums": "SHA256SUMS.txt",
  "checksums_sha256": "…",
  "tree_scope": "vendor",
  "note": "发布镜像；语料树按 commit 的完整源码计算，不拿 vendor 树冒充源码树"
}
```

`tree_scope` 必须写清镜像树与语料树的关系。**不要拿 vendor 树冒充源码树**。

## 4. 树哈希算法（可自行实现）

对条目根 `<slug>/src` 下的文件：

1. **过滤**（见 §4.1），只保留命中的文件；
2. 路径用 `/` 相对 `src/`，例如 `skills/x/SKILL.md`；
3. 按路径的 **UTF-8 字节序**升序排序（不是 locale 排序，不是码点排序）；
4. 每行拼 `<sha256(文件内容，小写十六进制)> + 空格 + <路径> + \n`；
5. 把全部行拼成一个字符串，对它再算 **sha256**，即 `computedHash`；
6. 全程**按字节读，不做 CRLF 转换**。

### 4.1 过滤规则（v0 快照）

- **扩展名白名单**：`.py .toml .md .json .yaml .yml .txt .ts .tsx .js .jsx .css .html .sql .sh .ps1`
- **无扩展名文件**只保留这些名字：`license` `licence` `copying` `notice` `readme`
  `license-mit` `licence-mit` `license-apache` `unlicense`
- **目录跳过**：`.git` `.github` `node_modules` `__pycache__` `.venv` `dist` `build` `.vscode`
- **单文件上限**：`1536000` 字节（1500 × 1024），超了不进树

实现细节（不写清这几处，自己写的校验器会对不上）：

- 扩展名与无扩展名文件名**先转小写再匹配**（`LICENSE`、`License` 等价）；
- 跳过目录按**目录名精确匹配**，任意深度（`a/b/.github/` 也跳）；
- 单文件上限是 `size <= MAX_FILE`，**等号在内**；
- **符号链接与非常规文件一律不进树**（抓取与哈希两侧都用 `lstat`，不跟随链接）；
- `anchors` 是**区分大小写**的子串匹配，不是正则。

> 这份清单是 v0 快照。判据源在 `workbench/corpus-lock.cjs` 的 `KEEP_EXT` / `KEEP_NAME` /
> `SKIP_DIRS` / `MAX_FILE`。**改这些常量等于改锁**：必须跑 `--update-all` 重锁，
> 否则所有条目的树哈希都会变成"改过语料"。

### 4.2 为什么不做 CRLF 归一

同一份语料在两台机器上算出两个哈希，锁就只在一台机器上成立。
仓库 `.gitattributes` 给 `market-corpus/**` 设了 `-text`，保证检出保留 LF。
**已经存在的克隆**拉下这条属性后工作区仍是旧的 CRLF 字节，需要按路径强制重检出：

```bash
git rm -r --cached market-corpus
git checkout -- market-corpus
```

## 5. anchors：树哈希之后的第二层

树哈希保证「一个字节没变」。所以 anchors 的用途只剩两件：**失败时说人话**，
和 `--update` 时给 diff 摘要。因此只锚**语义契约**，不逐文件锚脚本。

```json
{ "id": "plugin-manifest", "file": "src/plugin.toml", "all": ["id = \"xxx\"", "version = \""] }
{ "id": "prompt-registry", "file": "src/prompt-digests.json", "any": ["\"prompts\""] }
```

| 键 | 含义 |
|---|---|
| `file` | 相对**条目根**（含 `src/` 前缀），例如 `src/plugin.toml`。 |
| `all` | 数组里每个字符串都必须在文件里出现。 |
| `any` | 数组里至少出现一个。`all` 与 `any` 可同时给。 |

`--update` 抓完新内容后会**先用旧 anchors 核对**；不满足就失败，不会把坏内容写进锁。
所以上游挪了文件时，正确动作是先改 `anchors`，再重锁。

## 6. 校验与退出码

```bash
node workbench/fetch_corpus.cjs --locked --verify-only   # 只校验磁盘,不联网(CI 用)
node workbench/fetch_corpus.cjs --locked                 # 缺的按锁里的 commit 抓,再校验
node workbench/fetch_corpus.cjs --update <slug> --ref <tag>   # 重抓一条并重写锁
node workbench/fetch_corpus.cjs --update-all             # 过滤规则改了之后整体重锁
```

| 退出码 | 含义 |
|---|---|
| `0` | 全部通过 |
| `1` | 有失败项（树哈希不符 / 锚点缺失 / 抓取失败 / 语料缺失且 `--verify-only`） |
| `2` | 用法错误（没有待检查文件、slug 不合法、锁文件读不了） |

`archive_sha256` **不符只警告**：镜像站偶尔改压缩包封装，内容树对得上才是硬要求。
`computedHash` 不符是硬失败，报错里会同时给磁盘值与锁值。

### 6.1 退出码按命令分列

三个入口的 `2` 含义完全不同，别混着读：

| 命令 | `0` | `1` | `2` |
|---|---|---|---|
| `fetch_corpus --locked` / `--verify-only` | 全部通过 | 有失败：空锁、条目不合规、树哈希不符、锚点缺失、抓取失败、语料缺失（`--verify-only` 时） | ——（这条命令不返回 2） |
| `fetch_corpus --update` / `--update-all` | 更新成功 | 有条目更新失败（含 ref 被移动、锚点不满足） | 用法错误：缺 slug、slug 非法、锁里没有可更新条目 |
| `workbench/pull_forge.cjs` | 与钉版一致 | 语料缺失或树哈希不符 | `forge-pin.json` 与锁口径冲突（commit/ref/version 等对不上）、pin 或锁读不了 |

顶层异常（JSON 坏、schema 不认识）走 `1`，与「有失败项」同码——这是已知的粗粒度，
不影响判读：输出里有 `FAIL` 行点名原因。

## 7. 新增一条：两条路径

### 7.1 市场插件（zip 来源）

1. 在 `entries` 里手工加一条**最小条目**：

   ```json
   "my_plugin": {
     "source": "owner/neko-my-plugin",
     "kind": "plugin",
     "ref": "main",
     "anchors": [
       { "id": "plugin-manifest", "file": "src/plugin.toml", "all": ["id = \"my_plugin\""] }
     ]
   }
   ```

2. 跑 `node workbench/fetch_corpus.cjs --update my_plugin --ref <tag 或 main>`。
   它会：解析 commit → 下 codeload zip → 过滤解压 → 算 tree → 核对旧 anchors → 写回
   `commit` / `archive_sha256` / `computedHash` / `files` / `version` / `updated_at`。
3. review diff（尤其 `computedHash` 与 `files` 是否合理），提交。
4. `--locked --verify-only` 应返回 `0`。

### 7.2 工具仓（git 来源）

同上，但条目要多两个字段：

```json
"my_forge": {
  "source": "Owner/my-forge",
  "kind": "forge",
  "source_type": "git",
  "archive_format": "tar",
  "ref": "v1.2.3",
  "anchors": [ … ]
}
```

三个硬约束（都是实测踩出来的）：

- **抓取次序：完整 sha 优先，失败再退 `refs/tags/<ref>` 或 `refs/heads/<ref>`。**
  40 位 sha 的 `git fetch <url> <sha>` GitHub 接受（`actions/checkout` 也这么取）；
  **7 位缩写会 `couldn't find remote ref`**——早期用缩写测过，得出了「裸 sha 不能抓」的错结论。
  钉 sha 比钉 tag 稳：tag 可被删或移动，sha 不会。
- **ref 被移动时 `--update` 默认拒绝**：同一个 `ref` 指向了新 commit，意味着「版本号没变、内容变了」。
  确认上游是有意重打之后，加 `--accept-moved` 重跑。
- **`git archive` 必须固定换行**：抓取时用 `-c core.autocrlf=false -c core.eol=lf`。
  不固定的话同一 commit 在两台机器上导出不同字节的 tar，`archive_sha256` 只在一台机器上成立。

## 8. 常见错误

| 现象 | 原因 | 处理 |
|---|---|---|
| `树哈希不符`，文件数一样 | 语料被手改，或过滤规则变了 | 确认是故意的 → `--update` 重锁 |
| `锚点缺失`，但树哈希一致 | 上游挪了文件但内容没变 | 先改 `anchors`，再 `--update` |
| `ref vX 现在指向 <shaA>，锁里钉的是 <shaB>` | ref 被移动（tag 重打过） | 确认上游有意 → `--update … --accept-moved`；否则查上游 |
| `语料缺失 … --verify-only 不联网` | 没抓过 | 去掉 `--verify-only` 跑一次 `--locked` |
| Windows 上 tree 与 CI 不一致 | 检出成了 CRLF | 见 §4.2 强制重检出 |

## 9. 版本演进

`schema` 是整数，只在**破坏性变更**时升（字段语义变了、算法换了）。
新增可选字段不升 schema——老条目缺字段时按§3.2 的默认值理解即可。

### 9.1 v1 → v2：字段改名（当前版本）

| v1 | v2 | 说明 |
|---|---|---|
| `repo` | `source` | 改名 |
| `source`（`zip` / `git`） | `source_type` | 改名，避免与新的 `source` 撞名 |
| `tree_sha256` | `computedHash` | 改名，算法不变（`tree-sha256-v0`） |

`schema: 1` 的锁**仍然能读**：读取器看到 `schema: 1` 时按上表把字段名迁到 v2 再返回，
写回时自然变成 `schema: 2`。`source` 在两版里语义不同，所以只能靠 `schema` 判断，
不能靠「字段在不在」——这是迁移逻辑唯一的坑。

老条目缺字段时的默认值口径不变
（例如没有 `source_type` 就是 `zip`，没有 `archive_format` 就是 `zip`）。

## 10. 与 vercel-labs/skills 的字段对齐

字段名与 `vercel-labs/skills` 的锁条目对齐，**但算法不同，哈希值不可互认**：

| vercel-labs/skills | 本锁 | 关系 |
|---|---|---|
| `source` | `source` | 同为仓库标识 `owner/name` |
| `ref` | `ref` | 同名同义 |
| `computedHash` | `computedHash` | 字段名相同，**算法不同**：本锁是「过滤后文件按路径字节序 → 每行 `sha256 + 空格 + 路径 + \n` → 整串再 sha256」，且不做 CRLF 归一 |
| —— | `source_type` | 本锁额外区分传输方式（`zip` / `git`），那边没有这个概念 |
| —— | `archive_sha256` | 本锁额外记归档字节 |
| —— | `anchors` | 本锁额外记语义锚点 |

同名字段只有三个（`source` / `ref` / `computedHash`）。跨工具复用时**必须同时确认算法版本**，
否则会出现「字段对得上、哈希对不上」的误判。`schema` 与 `algo` 两个字段就是给这件事用的：
`algo` 不匹配时，校验器必须直接报错而不是"差不多就过"。
