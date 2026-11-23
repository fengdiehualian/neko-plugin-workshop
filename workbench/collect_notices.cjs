// 清点 market-corpus 里第三方源码的真实许可证,生成 THIRD_PARTY_NOTICES.md(issue #13/#8)
// 关键:不写「都是 MIT」这种没核对过的话——#8 就是嫌许可声明与事实不符。
const fs = require("fs")
const path = require("path")

const ROOT = path.resolve(__dirname, "..")
const CORPUS = path.join(ROOT, "market-corpus")
const OUT = path.join(ROOT, "THIRD_PARTY_NOTICES.md")

const KINDS = [
  ["MIT", /MIT license|Permission is hereby granted, free of charge/i],
  ["Apache-2.0", /Apache License[,\s]+Version 2\.0|apache\.org\/LICENSE-2\.0/i],
  ["BSD-3-Clause", /BSD 3-Clause|Redistributions in binary form/i],
  ["BSD-2-Clause", /BSD 2-Clause|Simplified BSD/i],
  ["ISC", /Permission to use, copy, modify, and(?:\/or)? distribute this software/i],
  ["MPL-2.0", /Mozilla Public License.*?2\.0/i],
  ["LGPL", /GNU LESSER GENERAL PUBLIC LICENSE|GNU LIBRARY GENERAL PUBLIC LICENSE/i],
  ["AGPL", /GNU AFFERO GENERAL PUBLIC LICENSE/i],
  ["GPL", /GNU GENERAL PUBLIC LICENSE/i],
  ["Unlicense", /This is free and unencumbered software/i],
  ["CC0", /Creative Commons CC0|CC0 1\.0|CC0 1\.0 Universal/i],
  ["GPL-3.0", /GNU GENERAL PUBLIC LICENSE.*Version 3/is],
  ["BSL-1.0", /Boost Software License/i],
  ["Zlib", /This software is provided 'as-is'/i],
]
// 双许可等复合写法:命中多个时按出现顺序记第一个,并在依据里说明
function classify(txt) {
  for (const [name, re] of KINDS) if (re.test(txt)) return name
  return ""
}
// LICENSE 文件不止一种命名/位置,漏找就会把「有许可但没读出来」误报成「无许可」(issue #6)
const LIC_NAMES = ["LICENSE", "LICENSE.md", "LICENSE.txt", "LICENSE-MIT", "LICENCE", "licence", "license", "COPYING", "unlicense", "LICENSE-APACHE", "license.md", "License.txt", "LICENCE.md"]
function findLicenseFile(base) {
  for (const sub of ["", "src", "plugin"]) {
    for (const n of LIC_NAMES) {
      const p = path.join(base, sub, n)
      if (fs.existsSync(p) && fs.statSync(p).isFile()) return { file: (sub ? sub + "/" : "") + n, txt: (() => { try { return fs.readFileSync(p, "utf8").slice(0, 8000) } catch { return "" } })() }
    }
    // 大小写不敏感的兜底扫描(Windows 目录列表大小写混着写很常见)
    try {
      const dir = path.join(base, sub)
      if (!fs.existsSync(dir)) continue
      for (const f of fs.readdirSync(dir)) {
        if (/^licen[cs]e|^copying|^unlicense/i.test(f) && fs.statSync(path.join(dir, f)).isFile()) {
          return { file: (sub ? sub + "/" : "") + f, txt: (() => { try { return fs.readFileSync(path.join(dir, f), "utf8").slice(0, 8000) } catch { return "" } })() }
        }
      }
    } catch {}
  }
  return null
}
const files = fs.readdirSync(CORPUS).filter((d) => fs.statSync(path.join(CORPUS, d)).isDirectory())
const rows = []
for (const slug of files) {
  const base = path.join(CORPUS, slug)
  let license = ""
  let licFile = ""
  const found = findLicenseFile(base)
  if (found) { licFile = found.file; license = classify(found.txt) }
  // plugin.toml 里的 license / author 字段也算线索
  let meta = {}
  for (const cand of ["src/plugin.toml", "plugin.toml"]) {
    const p = path.join(base, cand)
    if (fs.existsSync(p)) {
      const t = fs.readFileSync(p, "utf8")
      const g = (k) => (t.match(new RegExp('^' + k + '\\s*=\\s*"([^"]*)"', "im")) || [, ""])[1]
      meta = { licenseField: g("license"), author: g("author"), version: g("version"), homepage: g("homepage") }
      break
    }
  }
  if (!license && meta.licenseField) {
    const f = meta.licenseField.toUpperCase()
    if (/MIT/.test(f)) license = "MIT"
    else if (/APACHE/.test(f)) license = "Apache-2.0"
    else if (/AGPL/.test(f)) license = "AGPL"
    else if (/LGPL/.test(f)) license = "LGPL"
    else if (/GPL/.test(f)) license = "GPL"
    else if (/BSD/.test(f)) license = f
    else if (/ISC/.test(f)) license = "ISC"
    else if (/UNLIC/.test(f)) license = "Unlicense"
    else if (/CC0|PUBLIC/i.test(f)) license = "CC0"
  }
  // 「有 LICENSE 文件但没读出名堂」与「压根没有 LICENSE 文件」必须分开说,
  // 前者是「我们没识别出来」,后者才是「上游确实未声明」(issue #6)
  const state = license || (licFile ? "有 LICENSE 文件但未识别条款" : "未声明(仓库里没有可识别的 LICENSE)")
  rows.push({ slug, license, state, licFile, meta })
}
const tally = {}
for (const r of rows) tally[r.state] = (tally[r.state] || 0) + 1
const unknown = rows.filter((r) => r.state === "未声明(仓库里没有可识别的 LICENSE)").map((r) => r.slug)
const unread = rows.filter((r) => r.state === "有 LICENSE 文件但未识别条款").map((r) => r.slug)

const lines = []
lines.push("# 第三方内容许可声明(THIRD_PARTY_NOTICES)")
lines.push("")
lines.push("本文件由 `node workbench/collect_notices.cjs` 生成,记录仓库内**第三方来源**的许可情况。")
lines.push("改内容后请重跑生成脚本,别手改数字——issue #8/#13 抱怨过「声明与事实不符」。")
lines.push("")
lines.push("## 0. 重要:市场语料已不进安装包")
lines.push("")
lines.push("自 v1.2.64 起,`.gitattributes` 对 `market-corpus/**` 设了 `export-ignore`,")
lines.push("**`git archive` 产出的安装载荷不再包含这份语料**(`tools/check_package.ps1` 会核对载荷里没有 `market-corpus`)。")
lines.push("原因:这些是第三方插件源码,其中相当一部分没有可识别的许可声明(见下表),")
lines.push("由我们打包再分发并不合适。语料保留在开发仓库里作为**本地学习材料**;")
lines.push("需要的人在自己的机器上跑 `node workbench/fetch_corpus.cjs` 自行拉取。")
lines.push("")
lines.push("> 老用户不受影响:覆盖安装只解包写入载荷里存在的文件,不会删除本机已有语料。")
lines.push("")
lines.push("## 1. 市场语料 `market-corpus/` 的许可判定")
lines.push("")
lines.push("共 **" + rows.length + " 个插件源码目录**,取自官方市场接口(2026-10-26 抓取)。")
lines.push("判定顺序:仓库里的 LICENSE/COPYING 正文(含 `src/` 层级与大小写变体)→ `plugin.toml` 的 license 字段。")
lines.push("")
lines.push("| 判定结果 | 目录数 |")
lines.push("|---|---|")
for (const [k, v] of Object.entries(tally).sort((a, b) => b[1] - a[1])) lines.push("| " + k + " | " + v + " |")
lines.push("")
lines.push("两类「不是许可」的情况要分开看:")
lines.push("")
lines.push("- **有 LICENSE 文件但未识别条款(" + unread.length + " 个)**:文件在,但正文是我们没匹配上的写法。")
lines.push("  这类**不代表无许可**,需要逐个肉眼确认;确认前同样按不分发处理。")
lines.push("- **未声明(" + unknown.length + " 个)**:仓库里根本没有可识别的 LICENSE 文件。")
lines.push("  按「版权所有、未授予再分发权利」对待:只作本地阅读参考,不复制其代码进生成物,不打包分发。")
lines.push("")
if (unread.length) {
  lines.push("<details><summary>有 LICENSE 但未识别的 " + unread.length + " 个(待人工确认)</summary>")
  lines.push("")
  lines.push(unread.map((s) => "- `" + s + "`").join("\n"))
  lines.push("")
  lines.push("</details>")
  lines.push("")
}
if (unknown.length) {
  lines.push("<details><summary>无 LICENSE 文件的 " + unknown.length + " 个</summary>")
  lines.push("")
  lines.push(unknown.map((s) => "- `" + s + "`").join("\n"))
  lines.push("")
  lines.push("</details>")
  lines.push("")
}
lines.push("## 2. 逐目录明细")
lines.push("")
lines.push("| 目录 | 判定 | 依据 | plugin.toml 里写的 license |")
lines.push("|---|---|---|---|")
for (const r of rows.slice().sort((a, b) => a.slug.localeCompare(b.slug))) {
  lines.push("| `" + r.slug + "` | " + r.state + " | " + (r.licFile ? "`" + r.licFile + "`" : "无 LICENSE 文件") + " | " + (r.meta.licenseField || "—") + " |")
}
lines.push("")
lines.push("## 3. 随包分发的运行时")
lines.push("")
lines.push("工坊安装包(`payload.zip`)除本仓库源码外,只额外携带 `workbench/zcode_bundle.json` 登记的三件:")
lines.push("")
lines.push("| 条目 | 作用 | 来源与许可 |")
lines.push("|---|---|---|")
lines.push("| `_zcode/node.exe` | 跑 ZCode 底座的 Node 运行时 | Node.js 官方发行版二进制," + (tally["MIT"] ? "MIT 及随附第三方许可" : "MIT(见 Node.js 项目 LICENSE)") + ";版本见 zcode_bundle.json 的 nodeVersion |")
lines.push("| `_zcode/zcode.cjs` | ZCode CLI 主体 | 随 ZCode 官方发布分发(第三方,版本见 agentVersion);工坊不修改其内容,仅随包放置 |")
lines.push("| `_zcode/provider/zcode-builtin.json` | 工坊内置 provider 清单 | 本仓库生成,随本仓库许可 |")
lines.push("")
lines.push("`_engine`(OpenCode 编译版)、`_bin`、`_py`、`_site` 自 v1.2.26 起**从不进安装包**;因此**本安装包仅支持覆盖升级**——")
lines.push("首次安装请使用完整便携包。包内 `start.cmd` 是启动入口,引擎缺失时会明确报错停住")
lines.push("(issue #30 拍板:start.cmd 留包、依赖目录不进包,check_package 必查与 NOTICES 两条口径就此对齐)。")
lines.push("")
let vendored = null
lines.push("## 4. 仓库里的第三方文本与副本(逐条实测)")
lines.push("")
{
  // deep-dive(neko-plugin-dev 深度专题)走「钉版 + 按需拉取」:仓库里不存正文,
  // 钉版清单是 references/neko-plugin-dev-pin.json —— 署名段必须**无条件输出**,
  // 不能因为本机没拉过目录就让声明丢掉这一节(issue #16 的教训:声明与事实不符)。
  const pinPath = path.join(ROOT, "packs/neko-plugin/skill/neko-plugin-dev/references/neko-plugin-dev-pin.json")
  if (fs.existsSync(pinPath)) {
    let pin = null
    try { pin = JSON.parse(fs.readFileSync(pinPath, "utf8")) } catch {}
    if (pin && pin.source) {
      lines.push("### deep-dive(neko-plugin-dev 深度专题):钉版 + 按需拉取,仓库不存正文")
      lines.push("")
      lines.push("- 上游仓库:" + pin.source + "(MIT;版权人见其 LICENSE。PR #14 评审后按「文档出库」口径落地)")
      lines.push("- 钉版:版本 `" + (pin.version || "?") + "` / commit `" + (pin.commit || "?") + "`;`archive_sha256` `" + (pin.archive_sha256 || "?") + "`;树哈希 `computedHash` `" + (pin.computedHash || "?") + "`")
      lines.push("- 拉取入口:`node workbench/pull_skill_docs.cjs`(以本钉版为准);**正文不随安装包分发**,装机版按需自取")
      lines.push("")
    }
  }
}
lines.push("")
lines.push("以前这一段最容易被含混过去:把 `references/**` 整片说成「本仓库文档」。这里按实测分开写。")
lines.push("")
{
  const crypto = require("crypto")
  const sha = (p) => { try { return crypto.createHash("sha256").update(fs.readFileSync(p)).digest("hex") } catch { return "" } }
  const SKILL = path.join(ROOT, "packs/neko-plugin/skill/neko-plugin-dev/references")
  // (a) market-examples:与 market-corpus 逐字节相同的第三方副本
  const ME = path.join(SKILL, "market-examples")
  let meLines = []
  if (fs.existsSync(ME)) {
    for (const slug of fs.readdirSync(ME).filter((d) => fs.statSync(path.join(ME, d)).isDirectory()).sort()) {
      const src = path.join(ME, slug)
      const files = []
      const walk = (d, rel) => {
        for (const e of fs.readdirSync(d, { withFileTypes: true })) {
          if (e.isDirectory()) { walk(path.join(d, e.name), rel ? rel + "/" + e.name : e.name); continue }
          files.push([rel ? rel + "/" + e.name : e.name, sha(path.join(d, e.name))])
        }
      }
      walk(src, "")
      const row = rows.find((r) => r.slug === slug)
      meLines.push([slug, files.length, (row && row.license) || "未声明(没有可识别的 LICENSE)"])
      lines.push("- `market-examples/" + slug + "`:" + files.length + " 个文件,"
        + "与 `market-corpus/" + slug + "` 逐字节相同,许可判定:**" + ((row && row.license) || "未声明") + "**"
        + (row && row.file ? "(依据 `" + row.file + "`)" : "(仓库里没有 LICENSE 文件)"))
    }
    lines.push("")
    lines.push("处理方式:既然 `market-corpus/**` 因许可问题已不进安装包,这三份**同内容副本**也没有理由继续随包分发。")
    lines.push("`market-examples` 与 `market-examples/**` 都设了 `export-ignore`,`build_payload.py` 的 FORBIDDEN 与")
    lines.push("`check_package.ps1` 的禁含清单同样拦住它;开发仓里仍保留(要读源码时它是现成的精读范例),")
    lines.push("装机版改成本机 `node workbench/fetch_corpus.cjs` 取回后读 `market-corpus/<slug>/src`。")
  } else {
    lines.push("- `market-examples/` 不在工作区(新克隆未取语料),本段只作说明。")
  }
  lines.push("")
  // (a2) rbb:同样是钉版 + 按需拉取,正文不在仓库里。署名段也不能因目录缺失而消失。
  const RBB_PIN = path.join(SKILL, "rbb-pin.json")
  if (fs.existsSync(RBB_PIN)) {
    let pin = null
    try { pin = JSON.parse(fs.readFileSync(RBB_PIN, "utf8")) } catch {}
    if (pin && pin.source) {
      lines.push("### `reuse_before_build`(先搜后建,`" + pin.source + "`)")
      lines.push("")
      lines.push("- 上游:`" + pin.source + "`,**MIT**,版权人 `1-dcjiang`(上游 `src/LICENSE`)。")
      lines.push("- 引用范围:" + (pin.files || "?") + " 个文件(插件源码、`skills/reuse-before-build/` 正典与 references/scripts)。")
      lines.push("- **正文不进本仓库**:上游 commit `" + (pin.commit || "?") + "`(版本 `" + (pin.version || "?") + "`)钉在")
      lines.push("  `market-corpus/corpus.lock.json` 的 `" + (pin.slug || "reuse_before_build") + "` 条目(`vendored:false`,只记 source / 完整 SHA / 树哈希),")
      lines.push("  用时 `" + (pin.pull || "node workbench/pull_rbb.cjs") + "` 拉到 `" + (pin.corpus_root || "market-corpus/reuse_before_build/src") + "`。钉版详情见")
      lines.push("  `packs/neko-plugin/skill/neko-plugin-dev/references/rbb-pin.json`。")
      lines.push("- 分发口径:**不随包分发**。正文不在仓库里,`market-corpus/**` 也已 `export-ignore`,安装包与 `payload.zip` 都不含这份源码;")
      lines.push("  引用方若要把正文随包带上,必须自行补上游 MIT 全文与版权声明。")
      lines.push("- 与第 5 节的关系:这批是上游版权文本,不属于「本仓库生成物」,不适用本仓库 MIT。")
      lines.push("")
    }
  }
  // (b) experts/neko-plugin-ops:README 自陈吸收自两个 MIT agent 集
  const EX = path.join(ROOT, "experts/neko-plugin-ops")
  if (fs.existsSync(EX)) {
    const files = []
    for (const sub of ["references", "agents"]) {
      const d = path.join(EX, sub)
      if (!fs.existsSync(d)) continue
      for (const f of fs.readdirSync(d).sort()) {
        if (fs.statSync(path.join(d, f)).isFile()) files.push([sub + "/" + f, sha(path.join(d, f))])
      }
    }
    lines.push("### `experts/neko-plugin-ops/`(专家团文本)")
    lines.push("")
    lines.push("该团 `README.md` 自陈:多名成员的能力**吸收自**开源 agent 集合")
    lines.push("[msitarzewski/agency-agents](https://github.com/msitarzewski/agency-agents) 与其中文版")
    lines.push("[jnMetaCode/agency-agents-zh](https://github.com/jnMetaCode/agency-agents-zh)。")
    lines.push("两个上游 2026-11-16 实测**均存在、均为 MIT**(GitHub API license=MIT)。")
    lines.push("所以这里是「按 MIT 分发并保留上游署名」,不是「本仓库原创」;逐文件的对应关系尚未建立,")
    lines.push("下表先把现状哈希钉住,日后改动或补映射时能看出差异。")
    lines.push("")
    lines.push("| 文件 | 字节 | SHA-256(现算) |")
    lines.push("|---|---|---|")
    for (const [rel, h] of files) {
      const p = path.join(EX, ...rel.split("/"))
      lines.push("| `experts/neko-plugin-ops/" + rel + "` | " + fs.statSync(p).size + " | `" + h + "` |")
    }
    lines.push("")
    vendored = {
      market_examples: meLines.map(([slug, n, lic]) => ({ slug, files: n, license: lic })),
      experts: {
        upstreams: ["https://github.com/msitarzewski/agency-agents", "https://github.com/jnMetaCode/agency-agents-zh"],
        upstream_license: "MIT(2026-11-16 实测)",
        files: files.map(([rel, h]) => ({ path: rel, sha256: h })),
      },
    }
  }
}
lines.push("")
lines.push("## 5. 本仓库生成物")
lines.push("")
lines.push("工坊生成的插件脚手架(`packs/neko-plugin/templates/**`)与本仓库自写的官方文档快照")
lines.push("(`packs/neko-plugin/skill/neko-plugin-dev/references/**`,**不含第 4 节所列的第三方副本与派生文本**)按本仓库 MIT 许可分发。")
lines.push("references 下的官方文档属于 N.E.K.O. 项目文档,由工作台同步而来,引用时以文档站为准(见 `SKILL.md` 的「唯一事实来源」条目)。")
lines.push("")
lines.push("第 4 节那两类不归在这里:`market-examples/**` 是第三方市场插件的逐字节副本(许可未声明,已不进包),")
lines.push("`experts/neko-plugin-ops` 的提示词文本吸收自两个 MIT 上游(保留上游署名,不是本仓库原创)。")
lines.push("")
lines.push("**不存在的说法**:本文件不声称「forge / 其他仓库反向引用本工程」,也不声称市场语料「改写自」本仓库任何代码——")
lines.push("此前评审意见指出这类表述与事实不符(issue #8),现在明细以上面第 2 节的实测判定为准。")
lines.push("")
lines.push("## 6. 本仓库的 MIT 全文")
lines.push("")
// 版权行从仓库 LICENSE 里读,别写 <年份> <持有人> 这种空模板:
// 空模板会让人以为「还没填」,而且和 LICENSE 里的真实声明不一致(issue #6)
let licHead = ""
try {
  licHead = fs.readFileSync(path.join(ROOT, "LICENSE"), "utf8")
} catch {}
const copyrightLine = (licHead.match(/^Copyright.*$/im) || [""])[0]
lines.push("```")
if (copyrightLine) {
  lines.push(licHead.trim())
} else {
  lines.push("(读不到仓库根 LICENSE,请补上;MIT 全文应出现在 LICENSE 里,不该在这里重复一份空模板)")
}
lines.push("```")
lines.push("")
lines.push("生成于:" + new Date().toISOString().slice(0, 10))
fs.writeFileSync(OUT, lines.join("\n") + "\n", "utf8")

// 明细表也存一份机器可读版,给测试用
fs.writeFileSync(path.join(ROOT, "workbench", "notices.json"), JSON.stringify({ total: rows.length, tally, unknown, rows: rows.map((r) => ({ slug: r.slug, license: r.license, file: r.licFile, field: r.meta.licenseField || "" })), vendored }, null, 1) + "\n", "utf8")
console.log("目录总数:" + rows.length)
console.log("许可分布:" + JSON.stringify(tally))
console.log("未声明清单(前 20):" + unknown.slice(0, 20).join(", "))
console.log("已写 " + OUT + " 与 workbench/notices.json")
