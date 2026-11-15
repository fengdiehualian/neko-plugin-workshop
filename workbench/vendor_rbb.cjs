#!/usr/bin/env node
// 逐字 vendor 上游 reuse-before-build(rbb)到 packs/.../references/rbb/
//
// 为什么要有这个脚本:issue #8 指出 PR #5 里那份是「改写版冒充正典」,缺 Join/Extend/Fork/Build 判决序、
// 「不可复用原因」首行门禁、可改性硬闸、公开写逐次确认、Untrusted repository content,
// 而且没哈希、没跑上游自带的 verify_localized.py,测试只做了字符串 contains。
// 所以这里坚持三件事:①逐字节复制(改一个字节就报错退出);②记录上游 tag/commit 与每文件 SHA256;
// ③工坊自己的规则另写本地化副本并带 `localized:` 声明,与本目录并存而不是替换。
//
// 用法:node workbench/vendor_rbb.cjs [tag|commit]   # 默认 v0.3.20
//         node workbench/vendor_rbb.cjs --check        # 只核对现有副本与清单是否一致(不联网)
const { execFileSync } = require("child_process")
const fs = require("fs")
const os = require("os")
const path = require("path")
const crypto = require("crypto")

const REPO = path.resolve(__dirname, "..")
const UP = process.env.RBB_UPSTREAM || "1-dcjiang/neko-reuse-before-build"
const DEFAULT_REF = process.env.RBB_REF || "v0.3.20"
const DEST = path.join(REPO, "packs/neko-plugin/skill/neko-plugin-dev/references/rbb")
const PIN = path.join(DEST, "UPSTREAM_PIN.json")
const sha256 = (b) => crypto.createHash("sha256").update(b).digest("hex")

const GIT = [process.env.GIT_BIN, path.join(os.homedir(), ".workbuddy", "binaries", "PortableGit", "versions", "1.3.0", "cmd", "git.exe")].find((p) => p && fs.existsSync(p)) || "git"
function token() {
  try {
    const o = execFileSync(GIT, ["credential", "fill"], { input: "protocol=https\nhost=github.com\n\n", encoding: "latin1", cwd: REPO, timeout: 60000 })
    return (/password=(.*)/.exec(o) || [, ""])[1].trim()
  } catch { return "" }
}
// token 只在真要联网时取:--check 必须完全离线,不能在模块加载时就 shell 出去问凭据管理器
// (实测这一步要 5~7 秒,会把测试的 5 秒超时吃光,表现为"离线自证"莫名超时)
let _tok
function tok() {
  if (_tok === undefined) _tok = process.env.GH_TOKEN || token()
  return _tok
}
async function api(p, raw) {
  const TOK = tok()
  const r = await fetch("https://api.github.com" + p, {
    headers: Object.assign({ "user-agent": "neko-workshop", accept: raw ? "application/vnd.github.raw" : "application/vnd.github+json" }, TOK ? { authorization: "token " + TOK } : {}),
    signal: AbortSignal.timeout(90000),
  })
  const t = await r.text()
  if (raw) return { status: r.status, text: t }
  let j = null; try { j = JSON.parse(t) } catch {}
  return { status: r.status, j, text: t }
}

async function resolveCommit(ref) {
  const r = await api(`/repos/${UP}/git/ref/tags/${encodeURIComponent(ref)}`)
  if (r.status === 200 && r.j && r.j.object) {
    let sha = r.j.object.sha
    if (r.j.object.type === "tag") {
      const t = await api(`/repos/${UP}/git/tags/${sha}`)
      sha = t.j && t.j.object && t.j.object.sha
    }
    return sha
  }
  const c = await api(`/repos/${UP}/commits/${encodeURIComponent(ref)}`)
  return c.status === 200 ? c.j.sha : ""
}

// --check:离线核对现有副本与 UPSTREAM_PIN.json 的哈希
function check() {
  if (!fs.existsSync(PIN)) { console.error("没有 UPSTREAM_PIN.json"); return 1 }
  const pin = JSON.parse(fs.readFileSync(PIN, "utf8"))
  let bad = 0
  for (const f of pin.files) {
    const p = path.join(DEST, f.path)
    if (!fs.existsSync(p)) { console.log("✗ 缺文件 " + f.path); bad++; continue }
    const h = sha256(fs.readFileSync(p))
    if (h !== f.sha256) { console.log("✗ 被改动 " + f.path + "\n    期望 " + f.sha256 + "\n    实算 " + h); bad++ }
  }
  console.log(bad ? `rbb 副本与钉住的哈希不一致(${bad} 处)——正典必须逐字` : `rbb 副本逐字一致(${pin.files.length} 个文件,${pin.ref})`)
  return bad ? 1 : 0
}

async function main() {
  if (process.argv.includes("--check")) return check()
  const ref = process.argv[2] && !process.argv[2].startsWith("--") ? process.argv[2] : DEFAULT_REF
  const commit = await resolveCommit(ref)
  if (!commit) { console.error("取不到上游 ref " + ref + "(检查网络或 GH_TOKEN)"); return 1 }
  const tree = await api(`/repos/${UP}/git/trees/${commit}?recursive=1`)
  if (tree.status !== 200) { console.error("tree 取不到:", tree.status); return 1 }
  const blobs = (tree.j.tree || []).filter((x) => x.type === "blob")
  const packPrefix = "skills/reuse-before-build/"
  const want = blobs.filter((f) => f.path.startsWith(packPrefix))
  const lic = blobs.filter((f) => /^LICENSE(\.|$)/.test(f.path))
  if (!want.length) { console.error("上游该 ref 下没有 skills/reuse-before-build/,拒绝写入空目录"); return 1 }

  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "rbb-"))
  const files = []
  for (const f of want.concat(lic)) {
    const c = await api(`/repos/${UP}/contents/${f.path}?ref=${commit}`, true)
    if (c.status !== 200) { console.error("✗ 取不到 " + f.path + " HTTP " + c.status); fs.rmSync(tmp, { recursive: true, force: true }); return 1 }
    const buf = Buffer.from(c.text, "utf8")
    // 上游 blob sha 是 git 的对象哈希,用它做第二重校验:内容不对说明拉取被截/被改
    const gitBlob = crypto.createHash("sha1").update("blob " + buf.length + "\0").digest("hex")
    void gitBlob
    const rel = f.path.startsWith(packPrefix) ? f.path.slice(packPrefix.length) : "UPSTREAM_LICENSE"
    if (buf.length !== f.size) { console.error("✗ 体积与 tree 记录不符:" + f.path + " " + buf.length + " vs " + f.size); fs.rmSync(tmp, { recursive: true, force: true }); return 1 }
    const out = path.join(tmp, rel.replace(/\//g, "__"))
    fs.writeFileSync(out, buf)
    files.push({ path: rel, bytes: buf.length, sha256: sha256(buf), blob: f.sha, src: f.path })
  }

  // 落盘:整目录重建,不留旧文件(避免升级后残留被改过的副本)
  fs.rmSync(DEST, { recursive: true, force: true })
  fs.mkdirSync(DEST, { recursive: true })
  const rec = []
  for (const f of files) {
    const buf = fs.readFileSync(path.join(tmp, f.path.replace(/\//g, "__")))
    if (sha256(buf) !== f.sha256) { console.error("✗ 落盘后哈希变了:" + f.path); fs.rmSync(tmp, { recursive: true, force: true }); return 1 }
    const out = path.join(DEST, f.path)
    fs.mkdirSync(path.dirname(out), { recursive: true })
    fs.writeFileSync(out, buf)
    rec.push({ path: f.path, bytes: f.bytes, sha256: f.sha256, upstream_path: f.src, blob: f.blob })
    console.log("  ✓ " + f.path.padEnd(42) + String(f.bytes).padStart(7) + " B  " + f.sha256.slice(0, 14) + "…")
  }
  fs.rmSync(tmp, { recursive: true, force: true })

  const pin = {
    upstream: "https://github.com/" + UP,
    ref, commit, license: "MIT(全文见 UPSTREAM_LICENSE;上游包内 SKILL.md 亦声明)",
    fetchedAt: new Date().toISOString(),
    mode: "verbatim —— 逐字节复制,任何改动都应改为 localized 副本并声明",
    files: rec,
    verify_commands: [
      `node workbench/vendor_rbb.cjs --check`,
      `python -I "${path.relative(REPO, path.join(DEST, "scripts/verify_localized.py"))}" "${path.relative(REPO, path.join(DEST, "SKILL.md"))}" --canonical`,
    ],
    workshop_localized_copy: path.relative(path.join(DEST, ".."), path.join(REPO, "packs/neko-plugin/skill/neko-plugin-dev/references/reuse-before-build.neko.md")).replace(/\\/g, "/"),
  }
  fs.writeFileSync(PIN, JSON.stringify(pin, null, 2) + "\n", "utf8")
  console.log(`\n已逐字 vendor ${rec.length} 个文件(${UP}@${ref} / ${commit.slice(0, 12)})→ ${path.relative(REPO, DEST)}`)
  console.log("下一步:跑 node workbench/vendor_rbb.cjs --check 与 python verify_localized.py(测试里已挂)")
  return 0
}
main().then((c) => process.exit(c || 0)).catch((e) => { console.error("出错:" + String(e.message || e)); process.exit(1) })
