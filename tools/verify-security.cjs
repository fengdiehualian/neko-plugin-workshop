// 安全门禁端到端复验(issue #6 / #9)——不进 CI(要本机实例与真引擎),发版前手跑一次留证据。
//
// 前置(全部是隔离实例,绝不碰用户真实安装实例):
//   1) 仓库代码同步到 _wb_test(引擎底座实例,端口 5199)与 _wb_feed(CLI 底座演练实例,端口 5299)
//   2) _wb_feed 用 WB_UPDATE_FEED=http://127.0.0.1:4312/feed.json 启动(本脚本自己起这个演练源)
//   3) 两个 studio 都在跑,且各自的 runtime/studio-token.txt 可读(每实例随机,不能混用)
// 用法:node tools/verify-security.cjs   (全 PASS 退出码 0,否则 1)
//
// 检查项:
//   1) 引擎 Basic 密码是本实例随机值,旧硬编码常量已失效(旧=401、无认证=401、新=200)
//   2) 外发写确认队列接口:未知 id / 坏 body 不崩,缺 token 的写被门禁挡下
//   3) 自动更新包 SHA256 校验:没有校验值→拒装、校验不一致→拒装(且不回退到别的版本)、一致才放行
const fs = require("fs")
const path = require("path")
const http = require("http")
const crypto = require("crypto")

const DST = process.env.WB_TEST_DIR || "C:\\Users\\Administrator\\dev\\_wb_test"
const FEED = process.env.WB_FEED_DIR || "C:\\Users\\Administrator\\dev\\_wb_feed"
// 历史版本写死在包里的引擎密码(现已废弃);这里只用来证明它已经不起作用
const OLD_PW = "neko-studio-pw"
const A = "http://127.0.0.1:5199" // 引擎(opencode)实例:测引擎密码 + 确认队列
const B = "http://127.0.0.1:5299" // CLI 实例 + 本机演练源:测更新包校验
const lines = []
const log = (s) => { lines.push(s); try { process.stdout.write(s + "\n") } catch {} }
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
const tok = (dir) => fs.readFileSync(path.join(dir, "runtime", "studio-token.txt"), "utf8").trim()
async function j(url, opt) {
  const r = await fetch(url, Object.assign({ signal: AbortSignal.timeout(8000) }, opt || {}))
  let b = null
  try { b = await r.json() } catch {}
  return { status: r.status, b }
}
const post = (url, obj, token) =>
  j(url, { method: "POST", headers: { "content-type": "application/json", "x-wb-token": token }, body: typeof obj === "string" ? obj : JSON.stringify(obj) })
let pass = 0, fail = 0
function chk(name, ok, detail) {
  if (ok) pass++; else fail++
  log((ok ? "PASS " : "FAIL ") + name + "  |  " + detail)
}

;(async () => {
  // ---------- 1) 引擎随机密码 ----------
  const rec = JSON.parse(fs.readFileSync(path.join(DST, "runtime", "engine-auth.json"), "utf8"))
  chk("1a 密码是本实例随机值", rec.password.length >= 40 && /^[0-9a-f]+$/.test(rec.password) && rec.password !== OLD_PW, "长度=" + rec.password.length)
  const basic = (pw) => "Basic " + Buffer.from("opencode:" + pw).toString("base64")
  const good = await fetch("http://127.0.0.1:4097/api/health", { headers: { authorization: basic(rec.password) }, signal: AbortSignal.timeout(5000) })
  const bad = await fetch("http://127.0.0.1:4097/api/health", { headers: { authorization: basic(OLD_PW) }, signal: AbortSignal.timeout(5000) })
  const none = await fetch("http://127.0.0.1:4097/api/health", { signal: AbortSignal.timeout(5000) })
  chk("1b 旧硬编码常量已失效", good.status === 200 && bad.status === 401 && none.status === 401, "新=" + good.status + " 旧=" + bad.status + " 无认证=" + none.status)
  const h = await j(A + "/api/health")
  chk("1c 工坊用随机密码仍能自连引擎", h.b && h.b.engine === true, "health.engine=" + (h.b && h.b.engine))

  // ---------- 2) 外发写确认队列 ----------
  const l0 = await j(A + "/api/approvals")
  chk("2a GET /api/approvals 可用", l0.status === 200 && l0.b && Array.isArray(l0.b.items), "items=" + (l0.b && l0.b.items && l0.b.items.length))
  const ghost = await post(B + "/api/approvals", { id: "ghost", reply: "once" }, tok(FEED))
  chk("2b 未知 id 不崩", ghost.status === 200 && ghost.b && ghost.b.found === false, JSON.stringify(ghost.b))
  const junk = await post(B + "/api/approvals", "{{{坏 JSON", tok(FEED))
  chk("2c 坏 body 不崩", junk.status === 200 && junk.b && junk.b.found === false, JSON.stringify(junk.b))
  const noTok = await fetch(B + "/api/approvals", { method: "POST", headers: { "content-type": "application/json", origin: "http://127.0.0.1:5299" }, body: JSON.stringify({ id: "x" }) })
  chk("2d 无 token 的写被门禁挡下", noTok.status === 401 || noTok.status === 403, "HTTP " + noTok.status)

  // ---------- 3) 更新包 SHA256 校验 ----------
  const TMP = "C:\\Users\\Administrator\\AppData\\Local\\Temp\\wbfeed"
  fs.rmSync(TMP, { recursive: true, force: true })
  fs.mkdirSync(TMP, { recursive: true })
  const buf = Buffer.alloc(64 * 1024, 7)
  buf.write("PK\x03\x04", 0, "latin1")
  const realSha = crypto.createHash("sha256").update(buf).digest("hex")
  let feedJson = { version: "9.9.9", notes: "", assets: [] }
  const srv = http.createServer((req, res) => {
    const u = String(req.url || "")
    if (u.indexOf("feed.json") >= 0) { res.writeHead(200, { "content-type": "application/json" }); res.end(JSON.stringify(feedJson)) }
    else if (u.indexOf("fake.zip") >= 0) { res.writeHead(200, { "content-type": "application/zip", "content-length": String(buf.length) }); res.end(buf) }
    else { res.writeHead(404); res.end("x") }
  })
  await new Promise((r) => srv.listen(4312, "127.0.0.1", r))
  log("     演练源 4312:假包 " + buf.length + " 字节,真实 SHA256=" + realSha.slice(0, 16) + "…")

  async function waitState(pred, ms) {
    const t0 = Date.now()
    while (Date.now() - t0 < ms) {
      try { const s = await j(B + "/api/update"); if (s.b && pred(s.b)) return s.b } catch {}
      await sleep(400)
    }
    return null
  }
  const alive = async () => { try { return (await j(B + "/api/health")).status === 200 } catch { return false } }

  // 每个场景先 action=check(重读演练源),再 action=start
  async function runScenario(name, feed, pred, detailOf) {
    feedJson = feed
    await post(B + "/api/update", { action: "check" }, tok(FEED))
    await post(B + "/api/update", { action: "start" }, tok(FEED))
    const s = await waitState(pred, 60000)
    return { s, ok: pred(s), detail: detailOf(s) }
  }

  // A. 没登记校验值 → 拒装
  let r = await runScenario(
    "a",
    { version: "9.9.9", notes: "本版没有校验信息", assets: [{ name: "fake.zip", url: "http://127.0.0.1:4312/fake.zip", size: buf.length }] },
    (x) => x && (x.phase === "error" || x.phase === "done" || x.verified === true),
    (x) => "phase=" + (x && x.phase) + " verified=" + (x && x.verified) + " err=" + String((x && x.error) || "").slice(0, 70),
  )
  chk("3a 无校验值 → 拒绝自动安装", r.ok && /没有登记校验值/.test(String(r.s.error)) && r.s.verified === false && (await alive()), r.detail)

  // B. 校验值不一致(镜像换包演练) → 拒装
  r = await runScenario(
    "b",
    { version: "9.9.9", notes: "## 校验\n- 大小:65,536 字节\n- SHA256:`" + "b".repeat(64) + "`", assets: [{ name: "fake.zip", url: "http://127.0.0.1:4312/fake.zip", size: buf.length }] },
    (x) => x && (x.phase === "error" || x.phase === "done" || x.verified === true),
    (x) => "phase=" + (x && x.phase) + " err=" + String((x && x.error) || "").slice(0, 90),
  )
  chk("3b 校验不一致 → 拒绝自动安装(且不回退到别的版本)", r.ok && /校验不通过/.test(String(r.s.error)) && (await alive()), r.detail)

  // C. 校验一致 → 才放行(此时进程退出、安装脚本落盘)
  feedJson = { version: "9.9.9", notes: "## 校验\n- 大小:65,536 字节\n- SHA256:`" + realSha.toUpperCase() + "`", assets: [{ name: "fake.zip", url: "http://127.0.0.1:4312/fake.zip", size: buf.length }] }
  await post(B + "/api/update", { action: "check" }, tok(FEED))
  await post(B + "/api/update", { action: "start" }, tok(FEED))
  s = await waitState((x) => x.verified === true || x.phase === "error", 45000)
  let gone = false
  for (let i = 0; i < 30; i++) { await sleep(500); if (!(await alive())) { gone = true; break } }
  const helper = path.join(FEED, "runtime", "update", "apply-update.cmd")
  chk("3c 校验一致 → 放行并进入安装", Boolean(s && s.verified === true) && gone, "verified=" + (s && s.verified) + " 进程已退出=" + gone + " 安装脚本落盘=" + fs.existsSync(helper))

  srv.close()
  await sleep(1200)
  try { fs.rmSync(helper, { force: true }) } catch {}
  try { fs.rmSync(path.join(FEED, "runtime", "update"), { recursive: true, force: true }) } catch {}
  try { fs.rmSync(TMP, { recursive: true, force: true }) } catch {}
  log("== 合计 PASS=" + pass + " FAIL=" + fail)
  fs.writeFileSync(path.join(DST, "_verify_out.txt"), lines.join("\n"), "utf8")
  process.exit(fail ? 1 : 0)
})().catch(async (e) => {
  log("ERROR: " + ((e && e.stack) || e))
  try { fs.writeFileSync(path.join(DST, "_verify_out.txt"), lines.join("\n"), "utf8") } catch {}
  process.exit(1)
})
