import { describe, expect, test } from "bun:test"
import { readFileSync, readdirSync, statSync } from "node:fs"
import { dirname, join } from "node:path"
import { fileURLToPath } from "node:url"

// 对应 GitHub issue #11 / #13:凭据与 TLS 的口径、以及「别把本机路径写死进脚本」。
// 这类问题光改一次不算修完——下次有人顺手加个 `?access_token=` 或 `C:\Users\...` 就又回去了,
// 所以把它钉成测试(扫描范围就是会被审阅/复跑的那几个目录)。
const ROOT = dirname(dirname(fileURLToPath(import.meta.url)))
const read = (p: string) => readFileSync(join(ROOT, p), "utf8")

const RELEASE_FILES = ["workbench/publish_release.py", "workbench/check_release_state.py", "workbench/release_tls.py", "workbench/build_payload.py", "tools/probes/push-both.cjs", "tools/probes/restart-instance.cjs"]

describe("发布链路卫生(issue #11):凭据不进 URL、校验不默认关", () => {
  test("URL 里不出现 access_token(请求头才是唯一形态)", () => {
    const bad: string[] = []
    for (const f of RELEASE_FILES) {
      const src = read(f)
      src.split(/\r?\n/).forEach((l, i) => {
        if (!/access_token\s*=/.test(l)) return
        // multipart 表单字段 name="access_token" 是码云上传附件的正规写法(值进 body 不进 URL/日志)
        if (/Content-Disposition/.test(l) || /name=\\?"access_token\\?"/.test(l)) return
        bad.push(`${f}:${i + 1}  ${l.trim().slice(0, 90)}`)
      })
    }
    expect(bad, "token 被拼进 URL(会进访问日志/代理日志/截图):\n  " + bad.join("\n  ")).toEqual([])
  })
  test("TLS 校验只能被显式环境变量关掉,默认一律校验", () => {
    const offenders: string[] = []
    for (const f of RELEASE_FILES) {
      // 只查代码行:注释里写「以前这里是 CERT_NONE」是记录,不是违规(第一版测试把它误判成 offenders)
      const src = read(f).split(/\r?\n/).filter((l) => !/^\s*#|^\s*\/\/|^\s*"""/.test(l)).join("\n")
      if (/GIT_SSL_NO_VERIFY\s*=\s*["']?1/.test(src) && !/NEKO_INSECURE/.test(src)) offenders.push(f + ":无条件设 GIT_SSL_NO_VERIFY")
      if (/CERT_NONE/.test(src) && !/NEKO_INSECURE/.test(src)) offenders.push(f + ":CERT_NONE 没有环境变量开关")
      if (/verify_mode\s*=\s*ssl\.CERT_NONE/.test(src) && !/NEKO_INSECURE/.test(src)) offenders.push(f + ":证书校验默认关")
      if (/-c\s+http\.sslVerify=false/.test(src) && !/NEKO_INSECURE_GIT/.test(src)) offenders.push(f + ":git 关 sslVerify")
    }
    expect(offenders, "出现「默认不校验证书」的写法:\n  " + offenders.join("\n  ")).toEqual([])
    // 反过来也要真留着逃生阀(有些机器就是这么工作的),不能一删了之
    expect(read("workbench/release_tls.py")).toMatch(/NEKO_INSECURE_TLS/)
    expect(read("workbench/publish_release.py")).toMatch(/tag_is_free/)
  })
  test("tag 比较用 peeled 提交(注解 tag 的 ls-remote 第一列是 tag 对象)", () => {
    const src = read("tools/probes/push-both.cjs")
    expect(src, "没问 refs/tags/X^{} 就会把「一致」读成分叉").toMatch(/refs\/tags\/" \+ tag \+ "\^\{\}/)
  })
})

describe("脚本可换机器跑(issue #13):不写死本机路径", () => {
  // 允许写回退路径的只有这两个「路径解析器」本身
  const ALLOW = ["tools/probes/paths.cjs", "workbench/release_tls.py"]
  const files: string[] = []
  const walk = (rel: string) => {
    for (const name of readdirSync(join(ROOT, rel))) {
      const p = join(ROOT, rel, name)
      const r = rel + "/" + name
      if (statSync(p).isDirectory()) { if (name !== "__pycache__") walk(r); continue }
      if (/\.(cjs|mjs|js|py|ts|ps1)$/.test(name)) files.push(r)
    }
  }
  for (const d of ["tools", "workbench"]) walk(d)
  test("tools/ 与 workbench/ 里不许出现 C:\\Users\\<某人> 这类绝对家目录", () => {
    const hard: string[] = []
    for (const f of files) {
      if (ALLOW.includes(f)) continue
      const src = readFileSync(join(ROOT, f), "utf8")
      src.split(/\r?\n/).forEach((l, i) => {
        const m = l.match(/[Cc]:[\\/]{1,2}[Uu]sers[\\/]{1,2}[A-Za-z0-9_.-]+/)
        if (!m) return
        // 注释里举反例、或明确写着「本机已知位置兜底」的说明行,不算硬编码
        if (/^\s*(\/\/|#|\*)/.test(l) || /\bREADME\b/.test(l)) return
        hard.push(`${f}:${i + 1}  ${l.trim().slice(0, 80)}`)
      })
    }
    expect(hard, "写死了本机路径(换机器就废):\n  " + hard.join("\n  ")).toEqual([])
  })
  test("_zcode 的 node 来源要钉在具体版本目录,不能用 latest-vXX.x", () => {
    const zb = JSON.parse(read("workbench/zcode_bundle.json"))
    const items = JSON.stringify(zb)
    expect(items, "浮动地址:latest 一移动,登记里的校验和就无法复核(issue #13 第 3 点)").not.toMatch(/latest-v\d+\.x/)
    const nodeUp = JSON.stringify(zb).match(/https:\/\/nodejs\.org\/dist\/[^"]+/g) || []
    expect(nodeUp.length, "登记里找不到 nodejs.org 地址").toBeGreaterThan(0)
    for (const u of nodeUp) expect(u, "node 来源必须带具体版本号: " + u).toMatch(/\/dist\/v\d+\.\d+\.\d+\//)
  })
})
