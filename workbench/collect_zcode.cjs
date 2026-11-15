// 生成/更新 workbench/zcode_bundle.json:随包 ZCode 底座的来源、版本与哈希登记。
// 用法:node workbench/collect_zcode.cjs [--zcode <目录>]
//
// 为什么要有这个文件(issue #13/#6):
// _zcode 以前只是「从本机安装实例拷一份」:没记录来源、版本、哈希,
// 拷错了、被改了、少打了都没人知道(载荷少 30MB 那次就是这类问题)。
// build_payload.py 现在会强制核对这里的登记,缺条目直接拒绝出包。
const fs = require("fs")
const path = require("path")
const crypto = require("crypto")
const { execFileSync } = require("child_process")

const REPO = path.resolve(__dirname, "..")
const DEFAULT_SRC = path.join(process.env.LOCALAPPDATA || path.join(require("os").homedir(), "AppData", "Local"), "Programs", "NEKOWorkshop", "_zcode")
const SRC = process.argv[2] === "--zcode" ? process.argv[3] : (process.env.NEKO_ZCODE_DIR || DEFAULT_SRC)
const OUT = path.join(REPO, "workbench", "zcode_bundle.json")

const FILES = [
  {
    path: "node.exe", role: "runtime",
    // 上游与哈希核对方式:Node.js 官方发布页 + SHASUMS256.txt(不是「不知道哪来的 91MB」)
    upstream: "https://nodejs.org/dist/latest-v24.x/",
    hash_source: "https://nodejs.org/dist/latest-v24.x/SHASUMS256.txt",
  },
  {
    path: "zcode.cjs", role: "agent",
    upstream: "随 ZCode 官方发行版分发(客户端内置 bundle,非本仓库构建产物)",
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
function agentVersion(p) {
  try {
    const s = fs.readFileSync(p, "latin1")
    const m = s.match(/"version"\s*:\s*"(\d+\.\d+\.\d+[^"]{0,20})"/) || s.match(/version:\s*['"](\d+\.\d+\.\d+[^'"]{0,20})['"]/) || s.match(/zcode[- _]v?(\d+\.\d+\.\d+)/i)
    return m ? m[1] : ""
  } catch { return "" }
}
if (!fs.existsSync(SRC)) { console.error("找不到 _zcode 目录:" + SRC); process.exit(1) }
const files = FILES.map((f) => {
  const p = path.join(SRC, f.path.replace(/\//g, path.sep))
  if (!fs.existsSync(p)) { console.error("缺文件:" + p); process.exit(1) }
  return {
    path: f.path, role: f.role, upstream: f.upstream, hash_source: f.hash_source,
    size: fs.statSync(p).size, sha256: sha256(p),
  }
})
const doc = {
  $comment: "随包 ZCode 底座的来源与完整性登记。build_payload.py 打包时逐项比对 size 与 sha256,对不上或缺登记就拒绝出包。",
  nodeVersion: nodeVersion(path.join(SRC, "node.exe")),
  agentVersion: agentVersion(path.join(SRC, "zcode.cjs")),
  license: "Node.js: MIT(见 LICENSE 与 Node 官方随附第三方许可);zcode.cjs: 随 ZCode 官方发行;provider 清单: 本仓库生成",
  files,
  generatedAt: new Date().toISOString().slice(0, 10),
}
fs.writeFileSync(OUT, JSON.stringify(doc, null, 2) + "\n", "utf8")
console.log("已登记 " + OUT)
console.log("  node " + doc.nodeVersion + " / zcode " + doc.agentVersion)
for (const f of files) console.log("  " + f.path + " " + f.size.toLocaleString() + " 字节 " + f.sha256.slice(0, 16) + "…")
