const t = await Bun.file(process.env.TEMP + "/market_index.js").text()
// 找 API 调用:常见形态 "/api/xxx"、baseURL、fetch/axios 目标
const out = new Set()
for (const m of t.matchAll(/["'](\/[A-Za-z0-9_\-/.]{2,60})["']/g)) {
  const s = m[1]
  if (/api|plugin|market|search|detail|download|list|v1|store/i.test(s)) out.add(s)
}
console.log([...out].slice(0, 60).join("\n"))
// 找上游域名
const domains = new Set()
for (const m of t.matchAll(/https:\/\/[a-z0-9.\-]+[a-z0-9]/gi)) domains.add(m[0])
console.log("---domains---")
console.log([...domains].slice(0, 30).join("\n"))
