// 生成/更新 workbench/zcode_bundle.json:随包 ZCode 底座的来源、版本与哈希登记。
// 用法:node workbench/collect_zcode.cjs [--zcode <目录>]
//       node workbench/collect_zcode.cjs --verify      # 只向官方 SHASUMS256.txt 复核已登记的 zip 哈希(不改动文件)
//
// 为什么要有这个文件(issue #13/#6):
// _zcode 以前只是「从本机安装实例拷一份」:没记录来源、版本、哈希,
// 拷错了、被改了、少打了都没人知道(载荷少 30MB 那次就是这类问题)。
// build_payload.py 现在会强制核对这里的登记,缺条目直接拒绝出包。
//
// 第二轮复核又指出(issue #13 第 3 点):上游地址当时写的是浮动的 `latest-v24.x/`,
// latest 一移动,那个地址上的 SHASUMS256.txt 就不再包含 v24.14.0 的校验和,「可复核」只是句漂亮话。
// 现在钉到具体版本目录,并且**当场向官方文件取 zip 级哈希**——
// 注意 zip 的哈希与解包后 node.exe 的哈希是两个不同对象,所以两个都记、各写各的用途。
const fs = require("fs")
const path = require("path")
const crypto = require("crypto")
const { execFileSync } = require("child_process")

const REPO = path.resolve(__dirname, "..")
const DEFAULT_SRC = path.join(process.env.LOCALAPPDATA || path.join(require("os").homedir(), "AppData", "Local"), "Programs", "NEKOWorkshop", "_zcode")
const OUT = path.join(REPO, "workbench", "zcode_bundle.json")
const VERIFY = process.argv.includes("--verify")
const SRC = process.argv[2] === "--zcode" ? process.argv[3] : (process.env.NEKO_ZCODE_DIR || DEFAULT_SRC)

const FILES = [
  {
    path: "node.exe", role: "runtime",
    // node 的 upstream / hash_source 在生成时按实际版本拼出来(不再写 latest),见下方 mkNode
  },
  {
    path: "zcode.cjs", role: "agent",
    upstream: "随 ZCode 官方发行版分发(客户端内置 bundle,非本仓库构建产物)",
    upstreamRepo: "https://github.com/zai-org/ZCode",
    license: "Apache-2.0",
    licenseSource: "https://github.com/zai-org/ZCode/blob/main/LICENSE",
    hash_source: "以 ZCode 官方发行包内同名文件比对;升级底座时重新生成本文件",
  },
  {
    path: "provider/zcode-builtin.json", role: "provider",
    upstream: "本仓库生成(工坊内置 provider 清单)",
    hash_source: "本仓库内生成后重新登记",
  },
]
function sha256(p) {
  return crypto.createHash("sha256").update(fs.readFileSync(p)).digest("hex")
}
function nodeVersion(p) {
  try { return execFileSync(p, ["--version"], { encoding: "utf8" }).trim() } catch { return "" }
}
function agentVersion(p, nodeExe) {
  // 版本以 CLI 自报为准:直接跑 --version(实测输出形如 0.16.5)。
  // 正文正则只做兜底:老版本的第一条正则抓到过 0.13.3 —— 那其实是 bundle 内嵌
  // GLM 原生二进制配置里的 version 字段(zcode 自己的版本在 appVersion:"x.y.z"),
  // 字面量扫描分不清两者,登记出来就是错的版本(issue #17 复核发现)。
  try {
    return execFileSync(nodeExe, [p, "--version"], { encoding: "utf8", timeout: 30000 }).trim()
  } catch {}
  try {
    const s = fs.readFileSync(p, "latin1")
    const m = s.match(/appVersion\s*:\s*['"](\d+\.\d+\.\d+[^'"]{0,20})['"]/) || s.match(/zcode[- _]v?(\d+\.\d+\.\d+)/i)
    return m ? m[1] : ""
  } catch { return "" }
}
// 向官方要 zip 级校验和:只读那一个版本目录的 SHASUMS256.txt(约 2KB),不下载 30MB 的包
async function zipShaFromOfficial(ver, artifact) {
  const url = `https://nodejs.org/dist/${ver}/SHASUMS256.txt`
  const r = await fetch(url, { headers: { "user-agent": "neko-workshop" }, signal: AbortSignal.timeout(90000) })
  if (!r.ok) throw new Error(`HTTP ${r.status} ${url}`)
  const txt = await r.text()
  const line = txt.split(/\r?\n/).find((l) => l.trim().endsWith(artifact))
  if (!line) throw new Error(`${url} 里没有 ${artifact} 这一行(版本目录或文件名不对)`)
  return line.trim().split(/\s+/)[0].toLowerCase()
}
// (.cjs 没有顶层 await,整段流程包在 main() 里)
async function main() {
if (!fs.existsSync(OUT)) { console.error("没有登记文件:" + OUT + "(先不带 --verify 跑一次)"); process.exit(1) }
const cur = JSON.parse(fs.readFileSync(OUT, "utf8"))
const node0 = (cur.files || []).find((f) => f.role === "runtime") || {}
if (VERIFY) {
  // 复核:登记的 zip 哈希是否还能在**那个具体版本目录**的官方文件里找到
  const ver = cur.nodeVersion, art = node0.zip_artifact
  if (!ver || !art || !node0.zip_sha256) { console.error("登记里缺 nodeVersion/zip_artifact/zip_sha256,无从复核"); process.exit(1) }
  if (/latest-v\d+\.x/.test(JSON.stringify(cur))) { console.error("✗ 登记里还写着浮动地址 latest-vXX.x,先重新生成"); process.exit(1) }
  try {
    const now = await zipShaFromOfficial(ver, art)
    const ok = now === String(node0.zip_sha256).toLowerCase()
    console.log(`官方 SHASUMS256.txt(${ver}/${art}):\n  现取 ${now}\n  登记 ${node0.zip_sha256}\n  ${ok ? "一致 ✓ 来源可复核" : "不一致 ✗ 登记与官方对不上"}`)
    return ok ? 0 : 1
  } catch (e) { console.error("复核失败:" + String(e.message || e)); return 1 }
}

if (!fs.existsSync(SRC)) { console.error("找不到 _zcode 目录:" + SRC); process.exit(1) }
const ver = nodeVersion(path.join(SRC, "node.exe"))
if (!/^v\d+\.\d+\.\d+$/.test(ver)) {
  // 宁可不出登记,也不要一份「来源写 latest、事后无法复核」的糊账(issue #13)
  console.error(`拿不到确切的 node 版本(得到 ${JSON.stringify(ver)}),拒绝生成登记`)
  process.exit(1)
}
const winArch = process.env.NEKO_NODE_ARTIFACT || `node-${ver}-win-x64.zip`
let zipSha = ""
try {
  zipSha = await zipShaFromOfficial(ver, winArch)
  console.log(`已向官方核对 ${ver}/${winArch} → zip sha256=${zipSha.slice(0, 16)}…`)
} catch (e) {
  console.error("✗ 取不到官方校验和:" + String(e.message || e) + "\n  (不写「大概来自官方」的空话,登记中止)")
  process.exit(1)
}
const files = FILES.map((f) => {
  const p = path.join(SRC, f.path.replace(/\//g, path.sep))
  if (!fs.existsSync(p)) { console.error("缺文件:" + p); process.exit(1) }
  // 模板里的静态字段(含 zcode.cjs 的 license / upstreamRepo / licenseSource)原样带进登记,
  // 运行时字段(size/sha256 与 node 的来源)在下面补/覆盖
  const rec = {
    ...f,
    size: fs.statSync(p).size, sha256: sha256(p),
  }
  if (f.role === "runtime") {
    rec.upstream = `https://nodejs.org/dist/${ver}/`
    rec.hash_source = `https://nodejs.org/dist/${ver}/SHASUMS256.txt`
    rec.zip_artifact = winArch
    rec.zip_sha256 = zipSha
    rec.note = `zip_sha256 是官方 ${winArch} 的校验和;sha256 是解包后 node.exe 这个文件本身的哈希——两者本来就不该相等,build_payload 核后者,--verify 核前者`
  }
  return rec
})
const doc = {
  $comment: "随包 ZCode 底座的来源与完整性登记。build_payload.py 打包时逐项比对 size 与 sha256,对不上或缺登记就拒绝出包;`node workbench/collect_zcode.cjs --verify` 再向官方 SHASUMS256.txt 复核 zip 级哈希。",
  nodeVersion: ver,
  agentVersion: agentVersion(path.join(SRC, "zcode.cjs"), path.join(SRC, "node.exe")),
  license: "Node.js: MIT(见 Node 官方 LICENSE 与随附第三方许可);zcode.cjs: Apache-2.0(上游 zai-org/ZCode,见其仓库 LICENSE);provider 清单: 本仓库生成",
  files,
  generatedAt: new Date().toISOString().slice(0, 10),
}
fs.writeFileSync(OUT, JSON.stringify(doc, null, 2) + "\n", "utf8")
console.log("已登记 " + OUT)
console.log("  node " + doc.nodeVersion + " / zcode " + doc.agentVersion)
for (const f of files) console.log("  " + f.path + " " + f.size.toLocaleString() + " 字节 " + f.sha256.slice(0, 16) + "…" + (f.zip_sha256 ? "  (zip " + f.zip_sha256.slice(0, 12) + "…)" : ""))
}
main().then((c) => process.exit(typeof c === "number" ? c : 0)).catch((e) => { console.error("出错:" + String(e && e.message || e)); process.exit(1) })
