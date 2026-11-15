// 清点 market-corpus 里第三方源码的真实许可证,生成 THIRD_PARTY_NOTICES.md(issue #13/#8)
// 关键:不写「都是 MIT」这种没核对过的话——#8 就是嫌许可声明与事实不符。
const fs = require("fs")
const path = require("path")

const ROOT = path.resolve(__dirname, "..")
const CORPUS = path.join(ROOT, "market-corpus")
const OUT = path.join(ROOT, "THIRD_PARTY_NOTICES.md")

const KINDS = [
  ["MIT", /\bMIT license\b|Permission is hereby granted, free of charge/i],
  ["Apache-2.0", /Apache License,? Version 2\.0|apache\.org\/LICENSE-2\.0/i],
  ["BSD-3", /Redistributions in binary form.*(?:must retain|NEGLIGENCE)|BSD 3-Clause/i],
  ["BSD-2", /BSD 2-Clause|Simplified BSD/i],
  ["ISC", /Permission to use, copy, modify, and\/?or distribute this software/i],
  ["MPL-2.0", /Mozilla Public License.*2\.0/i],
  ["GPL", /GNU GENERAL PUBLIC LICENSE/i],
  ["AGPL", /GNU AFFERO GENERAL PUBLIC LICENSE/i],
  ["Unlicense", /This is free and unencumbered software/i],
  ["CC0", /Creative Commons CC0|CC0 1\.0/i],
]
function classify(txt) {
  for (const [name, re] of KINDS) if (re.test(txt)) return name
  return ""
}
const files = fs.readdirSync(CORPUS).filter((d) => fs.statSync(path.join(CORPUS, d)).isDirectory())
const rows = []
for (const slug of files) {
  const base = path.join(CORPUS, slug)
  let license = ""
  let licFile = ""
  for (const cand of ["LICENSE", "LICENSE.md", "LICENSE.txt", "LICENCE", "license", "src/LICENSE", "src/LICENSE.md", "src/LICENSE.txt"]) {
    const p = path.join(base, cand)
    if (fs.existsSync(p)) {
      try {
        const txt = fs.readFileSync(p, "utf8").slice(0, 6000)
        license = classify(txt)
        licFile = cand
        if (license) break
      } catch {}
    }
  }
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
    else if (/GPL/.test(f)) license = /AGPL/.test(f) ? "AGPL" : "GPL"
    else if (/BSD/.test(f)) license = f
    else if (/ISC/.test(f)) license = "ISC"
    else if (/UNLIC/.test(f)) license = "Unlicense"
    else if (/CC0|PUBLIC/i.test(f)) license = "CC0"
    else license = ""
  }
  rows.push({ slug, license: license || "未声明", licFile, meta })
}
const tally = {}
for (const r of rows) tally[r.license] = (tally[r.license] || 0) + 1
const unknown = rows.filter((r) => r.license === "未声明").map((r) => r.slug)

const lines = []
lines.push("# 第三方内容许可声明(THIRD_PARTY_NOTICES)")
lines.push("")
lines.push("本文件由 `node workbench/collect_notices.cjs` 生成,记录仓库内**第三方来源**的许可情况。")
lines.push("改内容后请重跑生成脚本,别手改数字——issue #8/#13 抱怨过「声明与事实不符」。")
lines.push("")
lines.push("## 1. 市场语料 `market-corpus/`")
lines.push("")
lines.push("共 **" + rows.length + " 个插件源码目录**,取自官方市场接口(2026-10-26 抓取),仅作本地学习语料,不随工坊安装包分发二进制资产。")
lines.push("每个目录的许可按「仓库里的 LICENSE 文件正文 → plugin.toml 的 license 字段」顺序判定,判定结果:")
lines.push("")
lines.push("| 许可 | 目录数 |")
lines.push("|---|---|")
for (const [k, v] of Object.entries(tally).sort((a, b) => b[1] - a[1])) lines.push("| " + k + " | " + v + " |")
lines.push("")
if (unknown.length) {
  lines.push("### 未能确认许可的目录(" + unknown.length + " 个)")
  lines.push("")
  lines.push("这些仓库既没有可识别的 LICENSE 正文,`plugin.toml` 也没写 license。")
  lines.push("**默认按「版权所有、未授予再分发权利」对待**:只作本地阅读参考,不复制其代码进生成物,")
  lines.push("不把它们打包分发。需要引用时先向上游确认许可。")
  lines.push("")
  lines.push(unknown.map((s) => "- `" + s + "`").join("\n"))
  lines.push("")
}
lines.push("## 2. 逐目录许可明细")
lines.push("")
lines.push("| 目录 | 判定许可 | 依据 | plugin.toml 里写的 license |")
lines.push("|---|---|---|---|")
for (const r of rows.sort((a, b) => a.slug.localeCompare(b.slug))) {
  lines.push("| `" + r.slug + "` | " + r.license + " | " + (r.licFile ? "`" + r.licFile + "`" : "无 LICENSE 文件") + " | " + (r.meta.licenseField || "—") + " |")
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
lines.push("## 5. MIT 许可证全文")
lines.push("")
lines.push("```")
lines.push("MIT License")
lines.push("")
lines.push("Copyright (c) <年份> <版权持有人>")
lines.push("")
lines.push("Permission is hereby granted, free of charge, to any person obtaining a copy")
lines.push("of this software and associated documentation files (the \"Software\"), to deal")
lines.push("in the Software without restriction, including without limitation the rights")
lines.push("to use, copy, modify, merge, publish, distribute, sublicense, and/or sell")
lines.push("copies of the Software, and to permit persons to whom the Software is")
lines.push("furnished to do so, subject to the following conditions:")
lines.push("")
lines.push("The above copyright notice and this permission notice shall be included in all")
lines.push("copies or substantial portions of the Software.")
lines.push("")
lines.push("THE SOFTWARE IS PROVIDED \"AS IS\", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR")
lines.push("IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,")
lines.push("FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE")
lines.push("AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER")
lines.push("LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,")
lines.push("OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE")
lines.push("SOFTWARE.")
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
