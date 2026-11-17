# market-corpus —— 语料及其锁文件

这里放的是**第三方插件源码的本地副本**,只作本机阅读参考:不复制进生成物、不打包分发
(理由与许可明细见仓库根 `THIRD_PARTY_NOTICES.md`;`.gitattributes` 对 `market-corpus/**`
设了 `export-ignore`,安装载荷里没有这个目录)。

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

## 三条命令

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

## 现状

试点阶段只有 `reuse_before_build` 一条进了锁,其余条目仍是「抓分支 HEAD + `.done`」的旧路径。
迁移顺序:先试点(本条) → `fetch_corpus` 接 `--locked` → 其余条目 `--update-all` 补齐后删掉
`.done` 与 `SOURCES.json`。
