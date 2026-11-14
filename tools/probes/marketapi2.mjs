const t = await Bun.file(process.env.TEMP + "/market_index.js").text()
// 找相对路径字符串(不带引号前的斜杠开头,含 plugin 字样)
const out = new Set()
for (const m of t.matchAll(/[`'"](\/plugins[^`"`']{0,60}|\/api[^`"`']{0,60}|\/market[^`"`']{0,60}|\/store[^`"`']{0,60})[`"']/g)) {
  out.add(m[1])
}
console.log([...out].slice(0, 80).join("\n"))
console.log("===fetch/axios targets===")
for (const m of t.matchAll(/(?:fetch|get|post)\((`|["'])([^`"']{4,90})\1/gi)) {
  if (/plugin|market|api/i.test(m[2])) console.log(m[2].slice(0, 90))
}
