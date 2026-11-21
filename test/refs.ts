import { readdirSync } from "node:fs"
import { join } from "node:path"

/**
 * 递归列出 dir 下的 .md 文件(绝对路径),跳过 skipDirs 里的一级子目录名。
 *
 * 为什么不点名具体目录再逐个加进检查:`references/` 下会不断长出新子目录
 * (deep-dive 进过又出过、现在是 market-examples),按目录名白名单写的检查
 * 每加一个子目录都要改一次,漏一次就是永久空档。
 */
export function walkMarkdown(dir: string, skipDirs: string[] = []): string[] {
  return readdirSync(dir, { withFileTypes: true })
    .filter((d) => !(d.isDirectory() && skipDirs.includes(d.name)))
    .flatMap((d) =>
      d.isDirectory()
        ? walkMarkdown(join(dir, d.name), skipDirs)
        : d.name.endsWith(".md") ? [join(dir, d.name)] : [])
}

/**
 * 第三方逐字节副本:内容由上游决定,`.gitattributes` 与另一条测试都要求它与语料保持一致。
 * 我们既不能改它,也不该要求它符合本仓库的编号/数量约定——它里面出现「R2」「12 条规则」
 * 是上游的事,在这里判红只会逼人要么改副本(破坏逐字节一致)、要么把检查关掉。
 */
export const THIRD_PARTY_DIRS = ["market-examples"]
