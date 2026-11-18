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
| `schema` | number | **必须是 1**。不认识的值直接报错，不猜。 |
| `algo` | string | **必须是 `tree-sha256-v0`**。不认识直接报错。 |
| `entries` | object | 键是 slug，值是条目。 |
| `note` | string | 可选，人类可读。 |

`slug` 必须匹配 `^[a-z0-9_-]+$`（目录名即 slug，不允许大写与路径片段）。

## 3. 条目字段

### 3.1 必填

| 字段 | 类型 | 含义 |
|---|---|---|
| `repo` | string | `owner/name`（GitHub）。**不带协议与 `.git`**。 |
| `kind` | string | `plugin`（市场插件）或 `forge`（工具仓）。缺省按 `plugin` 理解。 |
| `ref` | string | 抓取用的 ref：tag（`v0.3.22`）或分支名。**git 来源必填**，zip 来源也建议写。 |
| `commit` | string | 40 位十六进制 commit sha。**校验以它为准。** |
| `archive_sha256` | string | 归档文件的 sha256（64 位十六进制）。**不符只警告**，见 §6。 |
| `tree_sha256` | string | 落盘树的 sha256（64 位十六进制）。**不符即失败。** |
| `files` | number | 树里的文件数。用于失败时给人看规模差异。 |
| `anchors` | array | 语义锚点，见 §5。 |
| `updated_at` | string | ISO 时间戳，`--update` 自动写。 |

### 3.2 可选

| 字段 | 类型 | 含义 |
|---|---|---|
| `source` | string | `zip`（默认，市场 codeload zip）或 `git`（工具仓）。 |
| `archive_format` | string | `zip`（默认）或 `tar`（git 来源）。与 `source` 对应。 |
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
5. 把全部行拼成一个字符串，对它再算 **sha256**，即 `tree_sha256`；
6. 全程**按字节读，不做 CRLF 转换**。

### 4.1 过滤规则（v0 快照）

- **扩展名白名单**：`.py .toml .md .json .yaml .yml .txt .ts .tsx .js .jsx .css .html .sql .sh .ps1`
- **无扩展名文件**只保留这些名字：`license` `licence` `copying` `notice` `readme`
  `license-mit` `licence-mit` `license-apache` `unlicense`
- **目录跳过**：`.git` `.github` `node_modules` `__pycache__` `.venv` `dist` `build` `.vscode`
- **单文件上限**：`1536000` 字节（1500 × 1024），超了不进树

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
`tree_sha256` 不符是硬失败，报错里会同时给磁盘值与锁值。

## 7. 新增一条：两条路径

### 7.1 市场插件（zip 来源）

1. 在 `entries` 里手工加一条**最小条目**：

   ```json
   "my_plugin": {
     "repo": "owner/neko-my-plugin",
     "kind": "plugin",
     "ref": "main",
     "anchors": [
       { "id": "plugin-manifest", "file": "src/plugin.toml", "all": ["id = \"my_plugin\""] }
     ]
   }
   ```

2. 跑 `node workbench/fetch_corpus.cjs --update my_plugin --ref <tag 或 main>`。
   它会：解析 commit → 下 codeload zip → 过滤解压 → 算 tree → 核对旧 anchors → 写回
   `commit` / `archive_sha256` / `tree_sha256` / `files` / `version` / `updated_at`。
3. review diff（尤其 `tree_sha256` 与 `files` 是否合理），提交。
4. `--locked --verify-only` 应返回 `0`。

### 7.2 工具仓（git 来源）

同上，但条目要多两个字段：

```json
"my_forge": {
  "repo": "Owner/my-forge",
  "kind": "forge",
  "source": "git",
  "archive_format": "tar",
  "ref": "v1.2.3",
  "anchors": [ … ]
}
```

两个硬约束（都是实测踩出来的）：

- **不能按裸 sha 抓**：`git fetch <url> <sha>` 在 GitHub 上直接 `couldn't find remote ref`。
  只能按 `refs/tags/<ref>`（或 `refs/heads/<ref>`）取，再用 `rev-parse` 核对是否等于锁里的 `commit`。
- **`git archive` 必须固定换行**：抓取时用 `-c core.autocrlf=false -c core.eol=lf`。
  不固定的话同一 commit 在两台机器上导出不同字节的 tar，`archive_sha256` 只在一台机器上成立。

## 8. 常见错误

| 现象 | 原因 | 处理 |
|---|---|---|
| `树哈希不符`，文件数一样 | 语料被手改，或过滤规则变了 | 确认是故意的 → `--update` 重锁 |
| `锚点缺失`，但树哈希一致 | 上游挪了文件但内容没变 | 先改 `anchors`，再 `--update` |
| `ref vX 现在指向 <shaA>，锁里钉的是 <shaB>` | tag 被移动过 | 确认新内容后改锁里的 `commit` |
| `语料缺失 … --verify-only 不联网` | 没抓过 | 去掉 `--verify-only` 跑一次 `--locked` |
| Windows 上 tree 与 CI 不一致 | 检出成了 CRLF | 见 §4.2 强制重检出 |

## 9. 版本演进

`schema` 是整数，只在**破坏性变更**时升（字段语义变了、算法换了）。
新增可选字段不升 schema——老条目缺字段时按§3.2 的默认值理解即可
（例如没有 `source` 就是 `zip`，没有 `archive_format` 就是 `zip`）。
