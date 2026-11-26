import { describe, expect, test } from "bun:test"
import { existsSync, readFileSync, readdirSync, writeFileSync } from "node:fs"
import { dirname, join, relative, sep } from "node:path"
import { fileURLToPath } from "node:url"

// wb-studio 的对外面快照。
//
// 为什么要有它:主文件 5600+ 行、顶层 `boot()` 直接执行,没有 import 入口。
// 拆它(契约/生成/校验/发布/外部依赖/CLI 入口)时,**对外面必须一个字节都不变**——
// 少一条路由、改一个路径、丢一个配置键,网页或本机工具就在别处坏掉,而单测看不见。
//
// 扫哪些文件:主文件 + `src/studio/**/*.mjs`。拆到第 2 块起,`defaultConfig` 与路由会
// 搬进 src/studio/,只扫主文件的话快照会「变少而红」,而 README 教的 UPDATE_SNAPSHOT
// 又会把它刷掉——网就白织了。
//
// 锁三样「重构中不该动」的东西,分两种粒度:
//   1 路由**集合**(精确 + 模式):跨文件搬动不该改变它 —— 排序后比,搬走/搬回来都不红;
//   2 每个文件内的**出现顺序**:同一文件里的 if 顺序决定谁先匹配,换顺序就是改行为;
//   3 `defaultConfig` 的键(按书写顺序)与默认端口。
//
// 不锁函数清单:拆分的正常结果就是函数换文件换名字,锁了只会逼人无意义地刷新快照。
// 「搬完还能跑」由同目录的烟测保证。
//
// 更新快照:`UPDATE_SNAPSHOT=1 bun test`。它会写完立刻抛错,必须 review diff 后重跑
// 第二次才算数;CI 环境直接拒绝这个变量。

const ROOT = dirname(dirname(fileURLToPath(import.meta.url)))
const SRC_PATH = join(ROOT, "wb-studio.mjs")
const STUDIO_DIR = join(ROOT, "src", "studio")
const SNAP_PATH = join(ROOT, "test", "fixtures", "wb-studio-surface.json")

type FileSurface = {
  exact: string[]
  pattern: string[]
  configKeys: string[]
  defaultPort: string | null
}

type Surface = {
  schema: number
  exactPaths: string[]
  patternPaths: string[]
  configKeys: string[]
  defaultPort: string | null
  perFileOrder: Record<string, { exact: string[]; pattern: string[] }>
}

/** src/studio 下所有 .mjs(递归)。没有这个目录时返回空。 */
function listStudioModules(dir: string, out: string[] = []): string[] {
  if (!existsSync(dir)) return out
  for (const e of readdirSync(dir, { withFileTypes: true })) {
    const p = join(dir, e.name)
    if (e.isDirectory()) { listStudioModules(p, out); continue }
    if (e.isFile() && e.name.endsWith(".mjs")) out.push(p)
  }
  return out
}

/** 主文件在前,其余按相对路径字典序 —— 保证快照可复现(目录遍历顺序不可靠)。 */
function surfaceFiles(): { rel: string; abs: string }[] {
  const rest = listStudioModules(STUDIO_DIR)
    .map((abs) => ({ rel: relative(ROOT, abs).split(sep).join("/"), abs }))
    .sort((a, b) => (a.rel < b.rel ? -1 : a.rel > b.rel ? 1 : 0))
  return [{ rel: "wb-studio.mjs", abs: SRC_PATH }, ...rest]
}

/** 去掉纯注释行再提取。
 *  否则注释里举的 `url.pathname === "/api/x"` 会被算成真路由,快照里凭空多一条,
 *  而重构时删掉那条注释又会让快照"少一条"——两次都是假红。
 *  行内注释(`code() // url.pathname === "/x"`)仍会被抓到,这种写法在本仓不存在,
 *  真要全无歧义得上解析器,不值得为一个门禁脚本引依赖。 */
function stripCommentLines(src: string): string {
  return src.split("\n").filter((line) => {
    const t = line.trim()
    return !(t.startsWith("//") || t.startsWith("*") || t.startsWith("/*"))
  }).join("\n")
}

function extractOne(raw: string): FileSurface {
  const src = stripCommentLines(raw)
  // 出现顺序即分支判断顺序:先匹配到的路由赢,顺序变了就是行为变了
  const exact = [...src.matchAll(/url\.pathname === "([^"]+)"/g)].map((m) => m[1])
  const pattern = [...src.matchAll(/url\.pathname\.match\(\s*(\/\^[^\n]*?\/[a-z]*)\s*\)/g)]
    .map((m) => m[1])

  let configKeys: string[] = []
  let defaultPort: string | null = null
  const start = src.indexOf("const defaultConfig = {")
  if (start >= 0) {
    const end = src.indexOf("\n}", start)
    if (end < 0) throw new Error("defaultConfig 没有找到结束大括号")
    const seg = src.slice(start, end)
    configKeys = [...seg.matchAll(/^ {2}([A-Za-z][\w]*):/gm)].map((m) => m[1])
    const pm = seg.match(/\bport:\s*(\d+)/)
    defaultPort = pm ? pm[1] : null
  }
  return { exact, pattern, configKeys, defaultPort }
}

function extractSurface(): Surface {
  const exactSet = new Set<string>()
  const patternSet = new Set<string>()
  const configKeys: string[] = []
  const seenKey = new Set<string>()
  const perFileOrder: Record<string, { exact: string[]; pattern: string[] }> = {}
  let defaultPort: string | null = null

  for (const f of surfaceFiles()) {
    const one = extractOne(readFileSync(f.abs, "utf8"))
    one.exact.forEach((p) => exactSet.add(p))
    one.pattern.forEach((p) => patternSet.add(p))
    for (const k of one.configKeys) {
      if (!seenKey.has(k)) { seenKey.add(k); configKeys.push(k) }
    }
    if (one.defaultPort && !defaultPort) defaultPort = one.defaultPort
    // 只记有路由的文件:纯工具模块搬来搬去不该影响快照
    if (one.exact.length || one.pattern.length) {
      perFileOrder[f.rel] = { exact: one.exact, pattern: one.pattern }
    }
  }

  const sorted = (s: Set<string>) => [...s].sort()
  return {
    schema: 2,
    exactPaths: sorted(exactSet),
    patternPaths: sorted(patternSet),
    configKeys,
    defaultPort,
    perFileOrder,
  }
}

describe("wb-studio 对外面快照", () => {
  const actual = extractSurface()

  test("快照与源码一致(拆代码前后必须相同)", () => {
    if (process.env.UPDATE_SNAPSHOT === "1") {
      if (process.env.CI) {
        throw new Error("CI 环境禁止 UPDATE_SNAPSHOT=1:快照只能在本地更新并 review diff")
      }
      writeFileSync(SNAP_PATH, JSON.stringify(actual, null, 2) + "\n", "utf8")
      // 写完就抛:否则"先写再读再比"必然通过,等于自己给自己发通行证
      throw new Error("快照已重写到 " + SNAP_PATH + ":请 review diff 后重跑 bun test")
    }
    const expected = JSON.parse(readFileSync(SNAP_PATH, "utf8")) as Surface

    expect(actual.schema, "快照格式版本变了").toBe(expected.schema)
    // 集合:跨文件搬动不改变它
    expect(actual.exactPaths, "精确路由集合变了").toEqual(expected.exactPaths)
    expect(actual.patternPaths, "模式路由集合变了").toEqual(expected.patternPaths)
    // 配置面:键与默认端口
    expect(actual.configKeys, "defaultConfig 的键变了").toEqual(expected.configKeys)
    expect(actual.defaultPort, "默认端口不该在重构中变化").toBe(expected.defaultPort)
    // 单文件内的顺序:换顺序就是改行为,换文件则只该在 perFileOrder 的键上体现
    expect(Object.keys(actual.perFileOrder).sort(), "有路由的文件集合变了")
      .toEqual(Object.keys(expected.perFileOrder).sort())
    for (const f of Object.keys(expected.perFileOrder)) {
      expect(actual.perFileOrder[f].exact, f + " 的精确路由顺序变了")
        .toEqual(expected.perFileOrder[f].exact)
      expect(actual.perFileOrder[f].pattern, f + " 的模式路由顺序变了")
        .toEqual(expected.perFileOrder[f].pattern)
    }
  })

  test("关键入口必须还在(失败时说人话,不只给一个快照 diff)", () => {
    for (const p of ["/", "/api/health", "/api/sessions", "/api/backend", "/api/setup"]) {
      expect(actual.exactPaths, `入口路由 ${p} 不见了`).toContain(p)
    }
    // 模式路由在源码里是正则字面量,字符串里带反斜杠转义,所以按片段名判
    for (const frag of ["sessions", "apis"]) {
      expect(actual.patternPaths.some((p) => p.includes(frag)),
        `带 id 的 ${frag} 模式路由不见了`).toBe(true)
    }
  })

  test("配置面必须覆盖启动所需的最小集合", () => {
    for (const k of ["port", "workspace", "engineMode", "openBrowser", "openMode",
                     "timeoutSec", "stallTimeoutSec", "contextLimit"]) {
      expect(actual.configKeys, `配置键 ${k} 不见了`).toContain(k)
    }
    // 取完整数字再比:只判断包含 "5099" 时,50990 也会"通过"
    expect(actual.defaultPort, "默认端口不该在重构中变化").toBe("5099")
  })

  test("源码里没有残留的占位/半成品标记", () => {
    for (const f of surfaceFiles()) {
      const src = readFileSync(f.abs, "utf8")
      for (const bad of ["TODO: wire", "FIXME(move)", "<<<<<<<", ">>>>>>>"]) {
        expect(src.includes(bad), f.rel + " 里残留 " + bad).toBe(false)
      }
    }
  })

  test("两个页面的每个内联 <script> 都必须能通过语法解析", () => {
    // 为什么要有这条:SETUP_PAGE/PAGE 是模板字面量,页面 JS 里的字符串转义(如换行)
    // 要双写反斜杠才安全;写漏一层不会被任何单测看见——模板在加载时就把转义吃掉了,
    // 服务出去的脚本带着真实换行,整页 JS 直接 SyntaxError,所有按钮静默死亡
    // (2026-11 附件功能实测踩过:surface 快照只比路由、端到端测试不加载页面,双双 blind)。
    //
    // 做法:从源码里抠出模板字面量本体,按服务时的同一方式求值(唯一插值 PROVIDER_PRESETS
    // 用空数组打桩),再把每个 <script> 的内容交给 new Function 做完整语法解析
    // (不执行,只 parse;与 bun build 对 served 脚本的解析等价,零子进程开销)。
    const src = readFileSync(SRC_PATH, "utf8")
    for (const name of ["SETUP_PAGE", "PAGE"]) {
      const marker = "const " + name + " = `"
      const start = src.indexOf(marker)
      expect(start, name + " 模板没找到").toBeGreaterThan(0)
      const bodyStart = start + marker.length
      const bodyEnd = src.indexOf("`", bodyStart)
      expect(bodyEnd, name + " 的收尾反引号没找到").toBeGreaterThan(bodyStart)
      const html = eval("var PROVIDER_PRESETS=[];(`" + src.slice(bodyStart, bodyEnd) + "`)") as string
      const scripts = [...html.matchAll(/<script[^>]*>([\s\S]*?)<\/script>/g)]
        .map((m) => m[1]).filter((s) => s.trim().length > 0)
      expect(scripts.length, name + " 应至少有一个内联脚本").toBeGreaterThan(0)
      for (const js of scripts) {
        let err: unknown = null
        try { new Function(js) } catch (e) { err = e }
        expect(err, name + " 有内联脚本语法解析失败(页面 JS 会整段失效): " + String(err)).toBeNull()
      }
    }
  })

  test("侧栏会话搜索框:HTML 有定义、位于 #newbtn 上方、JS 已接线", () => {
    // 搜索是纯前端行为(过滤同一份 GET /api/sessions 全量缓存,无新路由,不进快照),
    // 端到端在 tools/check-page.mjs 的「会话搜索框」段;这里钉结构,防止拆 HTML 时静默丢件:
    // 元素存在、位置在「新对话」按钮上方、脚本侧真的引用并绑定了输入/Esc 事件。
    const src = readFileSync(SRC_PATH, "utf8")
    const marker = "const PAGE = `"
    const start = src.indexOf(marker)
    expect(start, "PAGE 模板没找到").toBeGreaterThan(0)
    const html = eval("var PROVIDER_PRESETS=[];(`" + src.slice(start + marker.length, src.indexOf("`", start + marker.length)) + "`)") as string
    expect(html, "#sessSearch 输入框没写进 HTML").toContain('<input id="sessSearch"')
    expect(html, "placeholder 应说明范围含已归档").toContain('placeholder="搜索会话(含已归档)"')
    // 位置:侧栏内、紧邻 #newbtn 上方(只隔空白)
    expect(html, "#sessSearch 应在 #side 内、#newbtn 上方")
      .toMatch(/<aside id="side">[\s\S]*?<input id="sessSearch"[^>]*>\s*<button id="newbtn">/)
    // 接线:脚本引用 + 输入过滤 + Esc 清空 + 归档徽标样式
    expect(html, "JS 应引用 sessSearch").toMatch(/getElementById\('sessSearch'\)/)
    expect(html, "输入事件应触发 renderSessList").toMatch(/addEventListener\('input',renderSessList\)/)
    expect(html, "Esc 应清空搜索").toMatch(/e\.key==='Escape'\)\{sessSearch\.value='';renderSessList\(\);/)
    expect(html, "归档命中徽标样式 .stag 应存在").toMatch(/\.sitem \.stag\{/)
  })

  test("思考强度滑杆:spec 配色与双层动效骨架钉在页面里", () => {
    // v1.2.75 动效改版(绿→青→蓝渐变填充+胶囊轨道+白thumb蓝环)的回归网:
    // 端到端在 tools/check-page.mjs 的「思考强度滑杆」段;这里钉 CSS/JS 骨架,防止拆样式时静默丢件
    // (旧版调色环 #ff5f9e 曾一度只靠人眼看着不回归)。锁的东西全部取自 spec 观察结论。
    const src = readFileSync(SRC_PATH, "utf8")
    const marker = "const PAGE = `"
    const start = src.indexOf(marker)
    expect(start, "PAGE 模板没找到").toBeGreaterThan(0)
    const html = eval("var PROVIDER_PRESETS=[];(`" + src.slice(start + marker.length, src.indexOf("`", start + marker.length)) + "`)") as string
    // 128px 定宽骨架(滑杆不因行内其他元素挤压/伸展)
    expect(html, "#thinkbar 应保持 128px 定宽").toContain("#thinkbar{position:relative;flex:0 0 128px;height:14px")
    // spec 形态:全圆角胶囊轨道凹槽(--tint+--border,未填满时也可见)
    expect(html, "胶囊轨道 #thinkbar::before 应存在").toContain("#thinkbar::before{content:'';position:absolute;left:0;right:0;top:2px;bottom:2px;border-radius:999px;background:var(--tint);border:1px solid var(--border)}")
    // spec 配色:黑主题=参考图原色绿→青→蓝;浅色主题加深变体(#15803d/#0d9488)保 3:1 对比
    expect(html, "填充渐变应为 spec 绿→青→蓝").toContain("linear-gradient(90deg,#22c55e 0%,#14b8a6 50%,#2f7bf6 100%) no-repeat;background-size:128px 100%")
    expect(html, "渐变应锚定 128px(左绿右蓝映射低→高)").toContain("background-size:128px 100%")
    expect(html, "浅色主题应用深绿/深青变体").toContain("body:not(.theme-black) #thinkfill{background:linear-gradient(90deg,#15803d 0%,#0d9488 50%,#2f7bf6 100%) no-repeat;background-size:128px 100%}")
    // 双层动效骨架:①底流 wheelroll(spec 色板循环渐变,速度=流星÷2.2)②流星白头蓝光晕
    expect(html, "底流应为 spec 色板循环渐变").toContain("repeating-linear-gradient(90deg,rgba(34,197,94,.78) 0px,rgba(20,184,166,.78) 30px,rgba(47,123,246,.78) 60px,rgba(103,232,249,.78) 90px,rgba(34,197,94,.78) 120px)")
    expect(html, "底流 wheelroll 应仍在").toContain("@keyframes wheelroll{from{transform:translateX(0)}to{transform:translateX(120px)}}")
    expect(html, "流星白头光晕应为 spec 蓝").toContain("box-shadow:0 0 0 1.5px var(--bg),0 0 6px 1px #2f7bf6")
    expect(html, "流星 128 行程 keyframes 应仍在").toContain("@keyframes thinkmeteor{0%,10%{transform:translateX(-48px)}85%,100%{transform:translateX(128px)}}")
    // 骨架约束:auto/关 静止由 .flow 驱动、reduced-motion 全停、交互不被遮挡
    expect(html, "流星点亮应仍由 .flow 兄弟选择器驱动").toContain("#thinkfill.flow~#thinkmeteor::before,#thinkfill.flow~#thinkmeteor::after{opacity:1;animation:thinkmeteor var(--flow-dur,.9s) linear infinite}")
    expect(html, "reduced-motion 应全停两层").toContain("@media (prefers-reduced-motion: reduce){")
    expect(html, "流星层不应挡滑杆交互").toContain("#thinkmeteor{position:absolute;left:0;right:0;top:0;bottom:0;overflow:hidden;border-radius:999px;pointer-events:none}")
    expect(html, "滑杆 input 应盖在最上层").toContain("#thinkRange{position:relative;z-index:1")
    // thumb=spec 白圆点+蓝环+柔光(webkit/moz 两套)
    expect(html, "thumb(webkit)应为白底蓝环柔光").toContain("#thinkRange::-webkit-slider-thumb{-webkit-appearance:none;width:16px;height:16px;border-radius:50%;background:#fff;border:2px solid #2f7bf6")
    expect(html, "thumb(moz)应为白底蓝环柔光").toContain("#thinkRange::-moz-range-thumb{width:14px;height:14px;border-radius:50%;background:#fff;border:2px solid #2f7bf6")
    // JS 骨架:auto/关(前两档)静止、开启后按档加速(1.3/0.9/0.6/0.4s)
    expect(html, "paintThink 的 .flow 开关应保留(auto/关 静止)").toContain("const on=thinkCur>=2;")
    expect(html, "档位速度阶梯应保留").toContain("([0,0,'1.3s','0.9s','0.6s','0.4s'][thinkCur]||'0.9s')")
    // 旧版调色环/旧 thumb 配色不应残留
    expect(html, "旧调色环四色(#ff5f9e)不应残留").not.toContain("#ff5f9e")
    expect(html, "旧 thumb 3px var(--pink) 边不应残留").not.toContain("border:3px solid var(--pink)")
  })
})
