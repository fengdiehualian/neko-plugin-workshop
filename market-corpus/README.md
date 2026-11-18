# market-corpus —— 语料及其锁文件

这里放的是**第三方插件源码的本地副本**,只作本机阅读参考:不复制进生成物、不打包分发
(理由与许可明细见仓库根 `THIRD_PARTY_NOTICES.md`;`.gitattributes` 对 `market-corpus/**`
设了 `export-ignore`,安装载荷里没有这个目录)。

> **要给一份上游语料新增锁条目**、或想在自己的工具里实现等价校验器,看
> [`LOCK-FORMAT.md`](./LOCK-FORMAT.md):字段表、树哈希算法（含过滤规则）、
> 两条新增路径（zip / git）、退出码与常见错误。本文只讲本仓怎么用。

## 语料锁 `corpus.lock.json`

每条语料记四样东西:上游仓库、commit、压缩包哈希、**落盘树的哈希**。
树哈希对「过滤后实际落盘的文件」计算,所以它同时盯住两件事——上游内容变了,
以及**过滤规则变了**(后者 zip 哈希看不见,这也是不能只用 zip 哈希的原因)。

算法(v0,`workbench/corpus-lock.cjs`):

1. 取条目根(`<slug>/src`)下的全部文件,路径用 `/` 相对条目根;
2. 按路径的 UTF-8 字节序排序;
3. 每行 `<sha256(文件内容)> <路径>\n` 拼接,对整串再 sha256;
4. 按字节算,不做 CRLF 转换——`.gitattributes` 里 `market-corpus/** -text` 保证
   Windows 检出也保留 LF,否则同一份语料在两台机器上会算出两个哈希。

格式与 `SHA256SUMS.txt` 同构,可以直接把每行的 `<hash> <path>` 导给人工核对。

## 四条命令

```bash
node workbench/fetch_corpus.cjs --locked                    # 只按锁里的 commit 抓;锁外 slug 跳过并报告
node workbench/fetch_corpus.cjs --locked --verify-only      # 只校验磁盘,不联网(CI 用这个)
node workbench/fetch_corpus.cjs --update <slug> [--ref vX]  # 重抓一条并重写锁,打印 old→new 摘要
node workbench/fetch_corpus.cjs --update-all                # 过滤规则改了之后整体重锁
```

行为约定:

- **锁里没有的 slug 不会被静默抓 HEAD**——HEAD 一提交内容就变,那会让目录里同时存在
  「可复现的」和「说不清是哪一份的」两种语料。它们只被跳过并计数。
- 续跑判断改成「目录存在且树哈希对得上就跳过」,不再用 `.done` 标记
  (同一件事有两套判断标准,迟早会有一个说谎)。
- 抓取失败、树哈希不符、锚点缺失都算失败,退出码 1。
- `archive_sha256` 不符只警告:镜像站偶尔改压缩包封装,内容树对得上才是硬要求。
- slug 必须匹配 `^[a-z0-9_-]+$`(目录名即 slug,不允许大小写混写或路径片段)。
- `--update` 会先用锁里的 anchors 核对新内容:上游把 `plugin.toml` / `SKILL.md` 挪走时,
  重锁直接失败(而不是写进一条下一次 `--verify` 解释不了的锁)。锚点过不去就先改锁里的 anchors。
- `api.github.com` 未认证只有 60 次/小时,`--update-all` 或多条重锁请先设 `GITHUB_TOKEN`。

## git 来源（工具仓）

市场插件走 codeload zip；**工具仓**（如 `plugin-forge`）不是市场插件，锁条目写
`"source": "git"`，抓取走 `git fetch` + `git archive`。两条硬约束都是实测踩出来的：

- **抓取次序：完整 sha 优先，失败再退 tag。** 实测 40 位 sha 的 `git fetch <url> <sha>` GitHub 接受
  （`actions/checkout` 也这么取）；**7 位缩写会 `couldn't find remote ref`**——早期用缩写测，
  得出了「裸 sha 不能抓」的错结论。钉 sha 比钉 tag 稳：tag 可被删或移动，sha 不会。
- **tag 被移动时 `--update` 默认拒绝**：同一个 `ref` 指向了新 commit，意味着「版本号没变、内容变了」。
  确认是上游有意重打之后，再加 `--accept-moved` 重跑。
- **`git archive` 必须固定换行**：同一 commit，本地仓库与浅克隆导出的 tar 字节不同（CRLF 差异，
  实测 1,259,520 vs 1,269,760）。抓取时固定 `core.autocrlf=false` 与 `core.eol=lf`，
  `archive_sha256` 才能跨机器复现。

Windows 上还有两个 git 参数,可用环境变量关掉,免得把用户环境写死:
`WB_GIT_SSL_BACKEND=openssl` 换 TLS 后端(企业 CA 环境常用);
`WB_GIT_STRICT_TLS=1` 完全按本机 git 配置走(不关吊销检查)。

条目里用 `archive_format` 区分两种归档：`tar`（git 来源）与 `zip`（市场来源，老条目缺省按 zip 理解）。
条目的 `ref` 对 git 来源是必填的——`commit` 只用于核对，抓取要靠 `ref` 定位。

```bash
node workbench/fetch_corpus.cjs --update plugin-forge --ref v2.8.0   # 追新版本(显式给 tag 或 sha)
node workbench/fetch_corpus.cjs --update plugin-forge                # 按锁里的 commit 重抓,不追新
```

**不传 `--ref` 时不会去问 tag 现在指哪**：锁里的 `commit` 就是权威，重抓只用于修复磁盘上的语料。
要换版本必须显式给 `--ref`——「自动跟随 tag」正是这套锁要避免的事。

## 已有克隆的换行提示

`.gitattributes` 给 `market-corpus/**` 设了 `-text`。**已经存在的克隆**在拉下这次改动后,
工作区里仍是旧的 CRLF 字节、索引里已是 LF,`git status` 会显示语料里上百个文件 modified ——
这不是内容改动,是检出属性变了。按路径强制重检出即可(别用 `git stash`,语料很大):

```bash
git rm -r --cached market-corpus
git checkout -- market-corpus
```

新克隆不受影响。

## 现状

锁里现在两条：`reuse_before_build`（市场插件，codeload zip）与 `plugin-forge`（工具仓，git + tar）。
其余条目仍是「抓分支 HEAD + `.done`」的旧路径。
迁移顺序：先试点（rbb） → `fetch_corpus` 接 `--locked` → 工具仓走 git 来源 → 其余条目 `--update-all` 补齐后删掉
`.done` 与 `SOURCES.json`。
