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
lines.push("`_engine`(OpenCode 编译版)、`_bin`、`_py`、`_site` 自 v1.2.26 起**从不进安装包**,故不在分发清单内;")
lines.push("官方安装器事实上只支持覆盖升级。")
lines.push("")
lines.push("## 4. 本仓库生成物")
lines.push("")
lines.push("工坊生成的插件脚手架(`packs/neko-plugin/templates/**`)与官方文档快照")
lines.push("(`packs/neko-plugin/skill/neko-plugin-dev/references/**`)按本仓库 MIT 许可分发;")
lines.push("其中 references 下的官方文档属于 N.E.K.O. 项目文档,由工作台同步而来,")
lines.push("引用时以文档站为准(见 `SKILL.md` 的「唯一事实来源」条目)。")
lines.push("")
lines.push("**不存在的说法**:本文件不声称「forge / 其他仓库反向引用本工程」,也不声称市场语料「改写自」本仓库任何代码——")
lines.push("此前评审意见指出这类表述与事实不符(issue #8),现在明细以上面第 2 节的实测判定为准。")
lines.push("")
lines.push("## 5. 本仓库的 MIT 全文")
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
fs.writeFileSync(path.join(ROOT, "workbench", "notices.json"), JSON.stringify({ total: rows.length, tally, unknown, rows: rows.map((r) => ({ slug: r.slug, license: r.license, file: r.licFile, field: r.meta.licenseField || "" })) }, null, 1) + "\n", "utf8")
console.log("目录总数:" + rows.length)
console.log("许可分布:" + JSON.stringify(tally))
console.log("未声明清单(前 20):" + unknown.slice(0, 20).join(", "))
console.log("已写 " + OUT + " 与 workbench/notices.json")
