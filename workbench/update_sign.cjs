#!/usr/bin/env node
// 自更新信任根(issue #12):给「版本 + 资产名 + 体积 + SHA256」这张清单做 ed25519 签名。
//
// 为什么需要它:哈希值原先取自同一条发布渠道的 release 正文——渠道被劫持(token 泄露、
// 镜像被抢注)时,攻击者可以把包和哈希一起换掉,校验等于摆设;而且客户端在 GitHub/码云之间
// 「取版本号最高的源」,被劫持的一端只要报一个更高的版本就能胜出。
// 签名把信任根从渠道挪到本地内置公钥:包是谁发的,不再由渠道说了算。
//
// 用法:
//   node workbench/update_sign.mjs keygen                 # 首次:生成密钥对(私钥只留本机)
//   node workbench/update_sign.mjs sign --exe <路径> --version v1.2.64 --out build/manifest.json
//   node workbench/update_sign.mjs verify --manifest <路径>
//   node workbench/update_sign.mjs pubkey                 # 打印要嵌进 wb-agent-lib.mjs 的公钥
//
// 私钥位置:%USERPROFILE%\.neko-workshop\update-sign-ed25519(PEM,权限仅当前用户)。
// 私钥丢了 = 无法再签新包,自动更新会退回「拒绝自动安装、提示手动下载」;请务必自行备份。
const fs = require("fs")
const path = require("path")
const os = require("os")
const crypto = require("crypto")

const KEY_DIR = process.env.NEKO_SIGN_DIR || path.join(os.homedir(), ".neko-workshop")
const PRIV = path.join(KEY_DIR, "update-sign-ed25519")
const PUB = path.join(KEY_DIR, "update-sign-ed25519.pub")
const NAME = "N.E.K.O.PluginWorkshop-Setup.exe"
const MANIFEST_NAME = "N.E.K.O.PluginWorkshop-Manifest.json"

function arg(name, d = "") {
  const i = process.argv.indexOf("--" + name)
  return i >= 0 && process.argv[i + 1] ? process.argv[i + 1] : d
}
function needDir() {
  fs.mkdirSync(KEY_DIR, { recursive: true })
}
function keygen() {
  if (fs.existsSync(PRIV) && !process.argv.includes("--force")) {
    console.log("私钥已存在,不覆盖:" + PRIV + "(要重来请加 --force,但那会让已发布的包验不过)")
    console.log("公钥:" + pubkeySpki())
    return 0
  }
  needDir()
  const { publicKey, privateKey } = crypto.generateKeyPairSync("ed25519")
  fs.writeFileSync(PRIV, privateKey.export({ type: "pkcs8", format: "pem" }), { mode: 0o600 })
  fs.writeFileSync(PUB, publicKey.export({ type: "spki", format: "pem" }), { mode: 0o644 })
  try { fs.chmodSync(PRIV, 0o600) } catch {}
  console.log("已生成密钥对")
  console.log("  私钥(仅此一份,请自行备份):" + PRIV)
  console.log("  公钥(要写进 wb-agent-lib.mjs):" + pubkeySpki())
  console.log("  把上面这串 base64 填到 wb-agent-lib.mjs 的 UPDATE_TRUST_PUBKEY,并跑 verify 自检")
  return 0
}
function pubkeySpki() {
  if (!fs.existsSync(PUB)) throw new Error("没有公钥,先跑 keygen:" + PUB)
  return fs.readFileSync(PUB, "utf8")
    .replace(/-----[^-]+-----/g, "")
    .replace(/\s+/g, "")
}
function privKey() {
  if (!fs.existsSync(PRIV)) throw new Error("没有私钥,先跑 keygen:" + PRIV)
  return crypto.createPrivateKey(fs.readFileSync(PRIV, "utf8"))
}
function pubKey() {
  return crypto.createPublicKey({ key: Buffer.from(pubkeySpki(), "base64"), format: "der", type: "spki" })
}
// 规范载荷:字段顺序固定,签的是这段**原始字节**,客户端解 base64 后原样验签,
// 不做二次序列化(序列化风格不同就会验不过——这类坑没必要踩)
function canonical(manifest) {
  return Buffer.from(JSON.stringify({
    v: 1,
    version: manifest.version,
    asset: manifest.asset,
    size: manifest.size,
    sha256: manifest.sha256,
    publishedAt: manifest.publishedAt,
  }), "utf8")
}
function sign() {
  const exe = arg("exe", path.join(__dirname, "..", "build", NAME))
  const version = arg("version")
  if (!version) { console.error("要 --version vX.Y.Z"); return 2 }
  if (!fs.existsSync(exe)) { console.error("安装包不存在:" + exe); return 2 }
  const blob = fs.readFileSync(exe)
  const manifest = {
    v: 1,
    version,
    asset: path.basename(exe),
    size: blob.length,
    sha256: crypto.createHash("sha256").update(blob).digest("hex").toUpperCase(),
    publishedAt: new Date().toISOString(),
  }
  const payload = canonical(manifest)
  const sig = crypto.sign(null, payload, privKey())
  const doc = { v: 1, alg: "ed25519", payload: payload.toString("base64"), sig: sig.toString("base64") }
  const out = arg("out", path.join(__dirname, "..", "build", MANIFEST_NAME))
  fs.mkdirSync(path.dirname(out), { recursive: true })
  fs.writeFileSync(out, JSON.stringify(doc, null, 2), "utf8")
  console.log("清单已签名:", out)
  console.log("  " + manifest.version + " " + manifest.asset + " " + manifest.size.toLocaleString() + " 字节 " + manifest.sha256.slice(0, 16) + "…")
  return verifyFile(out)
}
function verifyFile(file) {
  const doc = JSON.parse(fs.readFileSync(file, "utf8"))
  if (!doc || doc.alg !== "ed25519" || !doc.payload || !doc.sig) { console.error("清单格式不对"); return 2 }
  const payload = Buffer.from(doc.payload, "base64")
  const ok = crypto.verify(null, payload, pubKey(), Buffer.from(doc.sig, "base64"))
  if (!ok) { console.error("验签失败:签名与公钥不匹配"); return 2 }
  const m = JSON.parse(payload.toString("utf8"))
  console.log("  验签通过:", JSON.stringify(m))
  return 0
}
function main() {
  const cmd = process.argv[2] || ""
  try {
    if (cmd === "keygen") return keygen()
    if (cmd === "demo") return demo()
    if (cmd === "pubkey") { console.log(pubkeySpki()); return 0 }
    if (cmd === "sign") return sign()
    if (cmd === "verify") return verifyFile(arg("manifest"))
    if (cmd === "manifest-name") { console.log(MANIFEST_NAME); return 0 }
  } catch (e) {
    console.error("出错:" + (e && e.message ? e.message : e))
    return 1
  }
  console.log("用法:keygen | demo | pubkey | sign --exe <安装包> --version vX.Y.Z [--out f] | verify --manifest f")
  return 0
}

// 签一份「示范清单」当单测素材。
// 关键(issue #7):绝不能用生产私钥签测试素材 ——
// 那会造出一条「签名完全合法、但内容与任何真实发布都无关」的清单(9.9.9 / 假哈希),
// 一旦被拿出去配合别的渠道展示,就是误导性的「我们签过这一版」。
// 所以测试素材用一把独立、公开的测试钥匙,与生产钥匙完全隔离。
function testKeyPair() {
  const dir = path.join(__dirname, "..", "test", "fixtures")
  fs.mkdirSync(dir, { recursive: true })
  const pf = path.join(dir, "test-sign-ed25519")
  if (!fs.existsSync(pf)) {
    const { publicKey, privateKey } = crypto.generateKeyPairSync("ed25519")
    fs.writeFileSync(pf, privateKey.export({ type: "pkcs8", format: "pem" }), "utf8")
    fs.writeFileSync(pf + ".pub", publicKey.export({ type: "spki", format: "pem" }), "utf8")
  }
  return {
    priv: crypto.createPrivateKey(fs.readFileSync(pf, "utf8")),
    pub: fs.readFileSync(pf + ".pub", "utf8").replace(/-----[^-]+-----/g, "").replace(/\s+/g, ""),
  }
}
function demo() {
  const { priv, pub } = testKeyPair()
  const dir = path.join(__dirname, "..", "test", "fixtures")
  const manifest = {
    v: 1,
    version: "v9.9.9",
    asset: NAME,
    size: 12345678,
    sha256: crypto.createHash("sha256").update("neko-demo-payload").digest("hex").toUpperCase(),
    publishedAt: "2026-11-15T00:00:00.000Z",
  }
  const payload = canonical(manifest)
  const sig = crypto.sign(null, payload, priv)
  const doc = { v: 1, alg: "ed25519", payload: payload.toString("base64"), sig: sig.toString("base64") }
  const out = arg("out", path.join(dir, "manifest-good.json"))
  fs.writeFileSync(out, JSON.stringify(doc, null, 2) + "\n", "utf8")
  fs.writeFileSync(path.join(dir, "test-pubkey.txt"), pub + "\n", "utf8")
  // 自验用测试公钥,不用生产公钥
  const ok = crypto.verify(null, payload, crypto.createPublicKey({ key: Buffer.from(pub, "base64"), format: "der", type: "spki" }), sig)
  console.log("示范清单已签名(独立测试钥匙,不碰生产私钥):", out, ok ? "自验通过" : "自验失败")
  console.log("  测试公钥(单测要用这把):" + pub.slice(0, 24) + "…")
  console.log("  生产公钥(未参与本次签名):" + pubkeySpki().slice(0, 24) + "…")
  console.log("  ⚠ 用生产公钥验这份素材必须失败 —— 这正是「测试素材不是生产签名」的证明")
  try {
    const okProd = crypto.verify(null, payload,
      crypto.createPublicKey({ key: Buffer.from(pubkeySpki(), "base64"), format: "der", type: "spki" }), sig)
    console.log("  用生产公钥验这份测试素材:", okProd ? "居然通过了(不对,请检查钥匙)" : "失败(符合预期)")
  } catch {}
  return ok ? 0 : 1
}
if (require.main === module) process.exit(main())
module.exports = { canonical, sign, verifyFile, testKeyPair }
