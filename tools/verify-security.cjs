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
//   2) 外发写确认队列接口:未知 id / 坏 body 不崩,缺 token 的写被门禁挡下,
//      且抢不到端口的第二个实例不许污染 runtime/studio-token.txt
//   3) 自动更新完整性与信任根(issue #6 + #12):
//      单渠道无签名清单 → 拒;伪造清单 → 拒;清单有效但包被换 → 拒;正文与清单矛盾 → 拒;
//      清单验签且哈希一致 → 才放行(此时进程退出、安装脚本落盘)
const fs = require("fs")
const path = require("path")
const os = require("os")
const http = require("http")
const crypto = require("crypto")
const { spawnSync } = require("child_process")

const P = require("./probes/paths.cjs")
const REPO_ROOT = P.REPO
const DST = P.testDir()
const FEED = P.feedDir()
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
const post = (url, obj, token, ms) =>
  j(url, { method: "POST", headers: { "content-type": "application/json", "x-wb-token": token }, body: typeof obj === "string" ? obj : JSON.stringify(obj), signal: AbortSignal.timeout(ms || 8000) })
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

  // ---------- 2e) 抢不到端口的第二个实例,不许污染 token 文件 ----------
  // v1.2.65 发版前实测撞到:token 原本在模块加载时就落盘。第二个实例(连点两次快捷方式、
  // 或更新器撞上还在跑的实例)先写了自己的 token,再因端口占用异常退出 —— 存活实例用的是
  // 第一个进程的 token,文件里却是死掉那个的 → 之后所有读该文件的本机工具/自检一律 403,
  // 而提示语还让人以为是自己的错。修法:只有真的占住端口的进程才 publishLocalToken()。
  const tokFile = path.join(DST, "runtime", "studio-token.txt")
  const live2e = await j(A + "/api/health")
  if (live2e.status !== 200) {
    chk("2e 二次启动不污染 token", false, "5199 实例没在跑,本场景无法验证(不是通过)")
  } else {
    const before = fs.readFileSync(tokFile, "utf8").trim()
    const loser = spawnSync(P.bunExe(), ["wb-studio.mjs"], {
      cwd: DST, encoding: "utf8", timeout: 150000,
      env: Object.assign({}, process.env, { WB_STUDIO_NOWINDOW: "1" }),
    })
    const outStr = String((loser.stdout || "") + (loser.stderr || ""))
    const after = fs.readFileSync(tokFile, "utf8").trim()
    const again = await post(A + "/api/approvals", { id: "ghost", reply: "once" }, before)
    const note = (outStr.match(/端口 \d+ 已被占用[^\n]{0,40}/) || ["日志里没写占用原因"])[0]
    chk("2e 二次启动不污染 token(本机工具不会突然 403)",
      loser.status !== 0 && before === after && again.status === 200,
      "二次退出码=" + loser.status + " token 被改=" + (before !== after) + " 用文件 token 写接口 HTTP=" + again.status + " | " + note)
  }

  // ---------- 3) 更新包完整性 + 信任根 ----------
  // 清单素材:用发布机的私钥现场签(演练用,不进包)。定义要放在第一次调用之前。
  const SIGNER = path.join(REPO_ROOT, "workbench", "update_sign.cjs")
  function signManifest(exePath, version, outPath) {
    const r = spawnSync(process.execPath, [SIGNER, "sign", "--exe", exePath, "--version", version, "--out", outPath],
      { encoding: "utf8" })
    if (r.status !== 0) throw new Error("签清单失败:" + String(r.stderr || r.stdout).slice(0, 200))
    return JSON.parse(fs.readFileSync(outPath, "utf8"))
  }
  const alive = async () => { try { return (await j(B + "/api/health")).status === 200 } catch { return false } }
  const TMP = path.join(os.tmpdir(), "wbfeed")
  fs.rmSync(TMP, { recursive: true, force: true })
  fs.mkdirSync(TMP, { recursive: true })
  const buf = Buffer.alloc(64 * 1024, 7)
  buf.write("PK\x03\x04", 0, "latin1")
  const realSha = crypto.createHash("sha256").update(buf).digest("hex")
  // 演练渠道实际吐出的字节:场景里会临时换成「同一名字的另一份包」,模拟镜像被换包
  let served = buf
  let feedJson = { version: "9.9.9", notes: "", assets: [] }
  const srv = http.createServer((req, res) => {
    const u = String(req.url || "")
    if (u.indexOf("feed.json") >= 0) { res.writeHead(200, { "content-type": "application/json" }); res.end(JSON.stringify(feedJson)) }
    else if (u.indexOf("fake.zip") >= 0) {
      res.writeHead(200, { "content-type": "application/zip", "content-length": String(served.length) })
      res.end(served)
    } else { res.writeHead(404); res.end("x") }
  })
  await new Promise((r) => srv.listen(4312, "127.0.0.1", r))
  log("     演练源 4312:假包 " + buf.length + " 字节,真实 SHA256=" + realSha.slice(0, 16) + "…")

  // 给这份假包签一张真清单(私钥在发布机上;没有私钥时整组信任根场景跳过并说明)
  fs.writeFileSync(path.join(TMP, "fake.zip"), buf)
  let goodManifest = null
  try {
    goodManifest = signManifest(path.join(TMP, "fake.zip"), "9.9.9", path.join(TMP, "manifest.json"))
    log("     清单已签名(资产名 fake.zip,sha=" + realSha.slice(0, 12) + "…)")
  } catch (e) {
    log("     ! 签不出清单:" + String(e.message || e).slice(0, 140) + " —— 信任根场景无法验证")
  }

  async function waitState(pred, ms) {
    const t0 = Date.now()
    while (Date.now() - t0 < ms) {
      try { const s = await j(B + "/api/update"); if (s.b && pred(s.b)) return s.b } catch {}
      await sleep(400)
    }
    return null
  }
  // 每个场景先 action=check(重读演练源),再 action=start
  // check 会串行拉 GitHub + 码云两个渠道,本机实测 13~14 秒,所以这里给 45 秒(探针的旧 8 秒预算会把好结果读成超时)
  async function runScenario(feed, pred) {
    feedJson = feed
    try {
      await post(B + "/api/update", { action: "check" }, tok(FEED), 45000)
      await post(B + "/api/update", { action: "start" }, tok(FEED), 45000)
    } catch (e) {
      // 实例没起来/超时要在结果里说清楚,不能抛出去把整轮变成一坨栈
      return { s: { phase: "error", error: "请求失败:" + String(e && e.message || e) }, ok: false }
    }
    const s = await waitState(pred, 60000)
    return { s, ok: pred(s) }
  }
  const sawError = (x) => x && x.phase === "error"
  const sawVerified = (x) => x && x.verified === true
  const asset = (size) => [{ name: "fake.zip", url: "http://127.0.0.1:4312/fake.zip", size }]

  // A. 单渠道、没有签名清单 → 升级结论无从印证,拒装(issue #12 第 2 点)
  let r = await runScenario({ version: "9.9.9", notes: "本版没有校验信息", assets: asset(buf.length) }, sawError)
  chk("3a 单渠道且无签名清单 → 不自动安装", r.ok && /一个渠道|签名清单/.test(String(r.s.error)) && (await alive()), r.s && String(r.s.error).slice(0, 110))

  // B. 伪造清单(签名对不上内置公钥)→ 必须硬拒,不能退化成「当没清单」再靠双渠道互证(issue #3)
  const forged = { ...goodManifest, sig: Buffer.from("x".repeat(64)).toString("base64") }
  r = await runScenario({ version: "9.9.9", notes: "SHA256:`" + realSha + "`", assets: asset(buf.length), manifest: forged }, sawError)
  chk("3b 清单签名伪造 → 硬拒(不退化为多渠道互证)", r.ok && /清单不可信/.test(String(r.s.error)) && (await alive()), r.s && String(r.s.error).slice(0, 130))

  // B2. 一张「签名完全合法、但版本是旧的」清单拿来给新版本用 → 必须拒(防签名回滚)
  //     注意:一定要**重新签**一张 9.9.8 的清单。手工改 payload 里的版本号会破坏签名,
  //     命中的是「签名不符」而不是「版本不符」那条分支,等于没测到想测的东西(实测踩过)
  const stalePath = path.join(TMP, "manifest-998.json")
  let staleManifest = null
  try {
    staleManifest = signManifest(path.join(TMP, "fake.zip"), "9.9.8", stalePath)
  } catch (e) {
    log("     ! 签旧版清单失败:" + String(e.message || e).slice(0, 100))
  }
  if (staleManifest) {
    r = await runScenario({ version: "9.9.9", notes: "SHA256:`" + realSha + "`", assets: asset(buf.length), manifest: staleManifest }, sawError)
    chk("3b2 签名合法的旧版清单冒充新版 → 拒绝(防签名回滚)", r.ok && /清单版本|不一致/.test(String(r.s.error)), r.s && String(r.s.error).slice(0, 150))
  }

  // C. 清单有效,但渠道实际发的包被换掉(镜像换包演练) → 下载后实算不符,拒装
  served = Buffer.concat([buf.subarray(0, 4), Buffer.alloc(buf.length - 4, 9)])
  r = await runScenario({ version: "9.9.9", notes: "SHA256:`" + realSha + "`", assets: asset(buf.length), manifest: goodManifest }, sawError)
  chk("3c 清单有效但包被换 → 实算不符拒装", r.ok && /校验不通过/.test(String(r.s.error)) && (await alive()), r.s && String(r.s.error).slice(0, 130))
  served = buf

  // D. 渠道正文与清单互相矛盾(正文被改的演练) → 以清单为准并拒装
  r = await runScenario({ version: "9.9.9", notes: "SHA256:`" + "b".repeat(64) + "`", assets: asset(buf.length), manifest: goodManifest }, sawError)
  chk("3d 正文哈希与签名清单矛盾 → 拒装", r.ok && /签名清单与渠道正文/.test(String(r.s.error)), r.s && String(r.s.error).slice(0, 130))

  // E. 一切对得上 → 才允许放行(此时进程退出、安装脚本落盘)
  r = await runScenario({ version: "9.9.9", notes: "SHA256:`" + realSha + "`", assets: asset(buf.length), manifest: goodManifest }, sawVerified)
  let gone = false
  for (let i = 0; i < 30; i++) { await sleep(500); if (!(await alive())) { gone = true; break } }
  const helper = path.join(FEED, "runtime", "update", "apply-update.cmd")
  chk("3e 清单验签+哈希一致 → 放行并进入安装", Boolean(r.s && r.s.verified === true) && r.s.trustRoot === "manifest" && gone,
    "verified=" + (r.s && r.s.verified) + " trustRoot=" + (r.s && r.s.trustRoot) + " 进程退出=" + gone + " 脚本落盘=" + fs.existsSync(helper))
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
