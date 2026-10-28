// 用 Bun 自带的 TS/TSX 转译器做语法体检(不需要装 typescript)
// 用法: bun tools/tsx_check.mjs <file...>  → 输出 JSON: { ok: [...], bad: [{file, error}] }
const files = process.argv.slice(2)
const out = { ok: [], bad: [] }
const loaders = {
  tsx: new Bun.Transpiler({ loader: "tsx" }),
  ts: new Bun.Transpiler({ loader: "ts" }),
  jsx: new Bun.Transpiler({ loader: "jsx" }),
}
for (const f of files) {
  const ext = f.slice(f.lastIndexOf(".") + 1).toLowerCase()
  const t = loaders[ext] || loaders.tsx
  try {
    const src = await Bun.file(f).text()
    t.transformSync(src)
    out.ok.push(f)
  } catch (e) {
    out.bad.push({ file: f, error: String(e?.message || e).slice(0, 200) })
  }
}
console.log(JSON.stringify(out))
