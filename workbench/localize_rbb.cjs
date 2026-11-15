#!/usr/bin/env node
// 生成工坊本地化副本 references/reuse-before-build.neko.md
//
// 为什么用脚本生成而不是手写:issue #8 的教训是"改写版冒充正典"——手打一遍就会丢条款。
// 这里把上游中文完整版(zh-guide.md)整段逐字嵌进来,再接工坊附录,正典部分不经过我的手。
// 不变块、四个关键章节、12 个锚点因此天然成立;工坊专有条款只"收窄不放宽",并在 localized: 声明里写清。
//
// 用法:node workbench/localize_rbb.cjs           # 生成/覆盖
//       node workbench/localize_rbb.cjs --check   # 只检查现有生成物是否与源一致(CI/测试用)
const fs = require("fs")
const path = require("path")

const REPO = path.resolve(__dirname, "..")
const REF = path.join(REPO, "packs/neko-plugin/skill/neko-plugin-dev/references")
const RBB = path.join(REF, "rbb")
const ZH = path.join(RBB, "references/zh-guide.md")
const APPENDIX = path.join(__dirname, "rbb_neko_appendix.md")
const PIN = path.join(RBB, "UPSTREAM_PIN.json")
const CONTRACT = path.join(RBB, "localization.contract.json")
const OUT = path.join(REF, "reuse-before-build.neko.md")

function build() {
  for (const [what, p] of [["上游中文完整版", ZH], ["工坊附录", APPENDIX], ["上游钉", PIN], ["契约", CONTRACT]]) {
    if (!fs.existsSync(p)) throw new Error(`缺${what}:${p}`)
  }
  const zh = fs.readFileSync(ZH, "utf8")
  const cut = zh.indexOf("\n## 目录")
  if (cut < 0) throw new Error("上游 zh-guide.md 里找不到 `## 目录`,结构变了,先看清再改脚本")
  const body = zh.slice(cut + 1).replace(/\s+$/, "")
  const blk = body.indexOf("RBB:INVARIANTS:BEGIN")
  const blkEnd = body.indexOf("RBB:INVARIANTS:END")
  if (blk < 0 || blkEnd < 0) throw new Error("上游正文里没有不变块标记,拒绝生成(正典不许残缺)")
  if (!/RBB:INVARIANTS:LANG:/.test(body)) throw new Error("上游中文不变块缺 LANG 标记,校验器会判 hash_mismatch")

  const pin = JSON.parse(fs.readFileSync(PIN, "utf8"))
  const contract = JSON.parse(fs.readFileSync(CONTRACT, "utf8"))
  const app = fs.readFileSync(APPENDIX, "utf8").replace(/\s+$/, "")
  const decl = app.split(/\r?\n/).filter((l) => l.trim().toLowerCase().startsWith("localized:")).pop()
  if (!decl) throw new Error("附录末尾缺 `localized:` 声明行")

  const head = `# 先复用，再新建 —— N.E.K.O. 插件工坊本地化副本

> **本文件不是正典。** 正典(逐字节)在同目录 \`rbb/\`:上游 ${pin.upstream}
> tag \`${pin.ref}\` / commit \`${String(pin.commit).slice(0, 12)}\`,每文件 SHA-256 见 \`rbb/UPSTREAM_PIN.json\`,
> 不变块正典哈希 \`${contract.block.canonical_sha256}\`(上游 \`${contract.block.canonical_file}\`)。
>
> 下面从 \`## 目录\` 起是上游中文完整版**整段逐字**嵌入(未删条、未合并、未重编号),
> 之后接工坊自己的落位条款。两份都在,谁也不替换谁;要对规则本身负责,请看 \`rbb/\`。
>
> 生成物,别手改:源 = \`rbb/references/zh-guide.md\` + \`workbench/rbb_neko_appendix.md\`,
> 重跑 \`node workbench/localize_rbb.cjs\`。
`
  return head + "\n" + body + "\n\n" + app + "\n"
}

function main() {
  let want
  try { want = build() } catch (e) { console.error("生成失败:" + e.message); return 1 }
  if (process.argv.includes("--check")) {
    if (!fs.existsSync(OUT)) { console.error("✗ 缺生成物 " + path.relative(REPO, OUT)); return 1 }
    const got = fs.readFileSync(OUT, "utf8")
    if (got !== want) { console.error("✗ 本地化副本与源不一致(上游或附录改过却没重跑生成)"); return 1 }
    console.log("✓ reuse-before-build.neko.md 与上游正文 + 工坊附录一致")
    return 0
  }
  const last = want.split(/\r?\n/).filter((l) => l.trim()).pop() || ""
  if (!last.toLowerCase().startsWith("localized:")) { console.error("✗ 声明行不在文件末尾,校验器会拒"); return 1 }
  fs.writeFileSync(OUT, want, "utf8")
  console.log(`✓ 已写 ${path.relative(REPO, OUT)}(${Buffer.byteLength(want, "utf8")} B)`)
  console.log("下一步:python -I references/rbb/scripts/verify_localized.py references/reuse-before-build.neko.md 要求 PASS")
  return 0
}
process.exit(main())
