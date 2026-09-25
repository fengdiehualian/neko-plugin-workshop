// hosted-tsx 链接自检门（1.2.7 白屏二进宫复盘产物，见 ui/capintro.tsx 签名纪律）：
// 用宿主同源的导出扫描/链接器（N.E.K.O frontend/plugin-manager 的
// hostedTsxModule.mjs）把面板入口与全部相对依赖真链接一遍，若产物里残留裸
// `export function/const/class`（= 扫描器丢导出）即非零退出。这类错误宿主
// 前端表现为整面白屏，而 tsc/本地构建都查不出——所以这道门必须进测试链。
//
// 用法：node tools/check_hosted_link.mjs [插件根目录]
//   退出码 0=全部链接干净；1=发现丢导出；2=环境/路径问题（调用方应视为 skip）。
// 宿主 scanner 路径解析顺序：
//   1) 环境变量 NEKO_HOSTED_SCANNER（直接指向 hostedTsxModule.mjs）
//   2) 从插件根向上逐层找 frontend/plugin-manager/src/.../hostedTsxModule.mjs
//      （独立仓与宿主 plugin/plugins/<id> 挂载两种布局都命中）
import { readFileSync, existsSync, readdirSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'

const PLUGIN_ROOT = resolve(process.argv[2] || '.')
const SCANNER_REL = join('frontend', 'plugin-manager', 'src', 'components', 'plugin', 'hosted', 'hostedTsxModule.mjs')

function failEnv(msg) {
  console.error('HOSTED-LINK CHECK SKIPPED: ' + msg)
  process.exit(2)
}

function locateScanner() {
  if (process.env.NEKO_HOSTED_SCANNER && existsSync(process.env.NEKO_HOSTED_SCANNER)) {
    return process.env.NEKO_HOSTED_SCANNER
  }
  let dir = PLUGIN_ROOT
  for (let up = 0; up < 8; up++) {
    // 本级目录就是宿主仓（插件挂在宿主 plugin/plugins/<id> 时向上命中仓根），
    // 或宿主仓是本级目录的名为 *neko* 的兄弟子目录（开发仓与 N.E.K.O 并排布局）
    if (existsSync(join(dir, SCANNER_REL))) return join(dir, SCANNER_REL)
    try {
      for (const sib of readdirSync(dir, { withFileTypes: true })) {
        if (!sib.isDirectory()) continue
        // 宿主仓目录名可能是 N.E.K.O / n.e.k.o / neko——去点去下划线再认
        const norm = sib.name.toLowerCase().replace(/[.。_-]/g, '')
        if (!norm.includes('neko')) continue
        const cand = join(dir, sib.name, SCANNER_REL)
        if (existsSync(cand)) return cand
      }
    } catch { /* 无读权限就继续往上找 */ }
    const parent = dirname(dir)
    if (parent === dir) break
    dir = parent
  }
  return null
}

const scannerPath = locateScanner()
if (!scannerPath) failEnv('hostedTsxModule.mjs not found (set NEKO_HOSTED_SCANNER or run inside/next to the N.E.K.O repo)')

const mod = await import('file:///' + scannerPath.replace(/\\/g, '/'))
const { bundleHostedTsxSource, findHostedRelativeImportSpecifiers } = mod
if (!bundleHostedTsxSource || !findHostedRelativeImportSpecifiers) {
  failEnv('hostedTsxModule.mjs exports unexpected API (host scanner drifted; update this tool)')
}

// 面板入口从 plugin.toml 的 [[plugin.ui.panel]] entry 字段解析（不写死文件名）
function panelEntries() {
  const tomlPath = join(PLUGIN_ROOT, 'plugin.toml')
  if (!existsSync(tomlPath)) failEnv('plugin.toml not found at ' + PLUGIN_ROOT)
  const toml = readFileSync(tomlPath, 'utf-8')
  const entries = [...toml.matchAll(/^\s*entry\s*=\s*"([^"]+\.tsx?)"\s*$/gm)].map((m) => m[1])
  if (!entries.length) failEnv('no ui entry found in plugin.toml')
  return [...new Set(entries)]
}

const UI_DIR = join(PLUGIN_ROOT, 'ui')
function resolveUiPath(rel, fromFile) {
  // 依赖表按「相对插件根 ui/」的规范化路径存
  let p = resolve(dirname(join(UI_DIR, fromFile)), rel)
  if (!/\.(ts|tsx)$/.test(p)) p += existsSync(p + '.ts') ? '.ts' : '.tsx'
  const relPath = p.replace(/\\/g, '/').slice(UI_DIR.replace(/\\/g, '/').length + 1)
  return relPath
}

let failures = 0
for (const entry of panelEntries()) {
  const entryPath = join(PLUGIN_ROOT, entry)
  if (!existsSync(entryPath)) { console.error('FAIL: panel entry missing: ' + entry); failures++; continue }
  const seen = new Set()
  const load = (abs) => readFileSync(abs, 'utf-8')
  const collect = (key) => {
    const src = load(join(UI_DIR, key))
    const found = findHostedRelativeImportSpecifiers(src)
    const runtime = Array.isArray(found) ? found : (found && found.runtime) || []
    for (const spec of runtime) {
      const depKey = resolveUiPath(spec, key)
      if (!seen.has(depKey)) { seen.add(depKey); collect(depKey) }
    }
  }
  const entryKey = entry.replace(/^ui\//, '')
  collect(entryKey)
  const dependencies = [...seen].map((p) => ({ path: p, source: load(join(UI_DIR, p)) }))
  const bundle = bundleHostedTsxSource(load(entryPath), dependencies, entryKey)
  const leftovers = [...bundle.matchAll(/^[ \t]*export[ \t]+(function|const|class)[ \t]+([A-Za-z_$][\w$]*)/gm)]
  console.log(`entry ${entry}: ${dependencies.length + 1} modules linked, bare-export leftovers: ${leftovers.length}`)
  for (const m of leftovers) {
    const i = m.index
    console.error(`  !! lost export "${m[2]}" — linker left it bare. Context before:`)
    console.error('     ' + JSON.stringify(bundle.slice(Math.max(0, i - 160), i).split('\n').slice(-2).join('\n     ')))
    failures++
  }
}

if (failures) {
  console.error(`HOSTED-LINK CHECK FAILED: ${failures} lost export(s). Move JSX-containing functions below all exports (see ui/capintro.tsx 签名纪律).`)
  process.exit(1)
}
console.log('HOSTED-LINK CHECK OK')
