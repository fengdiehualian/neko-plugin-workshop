import { describe, expect, test } from "bun:test"
import { readFileSync } from "node:fs"
import { dirname, join } from "node:path"
import { fileURLToPath } from "node:url"
import { detectOutwardWrite, classifyPermission, classifyQuestionReply, enginePermissionConfig, resolveBashEffect } from "../wb-agent-lib.mjs"

// 对应 GitHub issue #9/#10:外发写既要在事件侧被认出来,也要在引擎侧真的会被拦下来问
const ROOT = dirname(dirname(fileURLToPath(import.meta.url)))
const ow = (text) => detectOutwardWrite({ patterns: [text] })

describe("绕过写法也要命中(issue #10:黑名单正则曾被全局选项绕过)", () => {
  test("git 全局选项变体", () => {
    for (const t of [
      "git push origin main",
      "git -C /repo push origin main",
      "git -c user.name=x push",
      "git --git-dir=/repo/.git push",
      "git --no-pager push -u origin HEAD",
      "cd /repo && git push",
      "git -C repo push --force-with-lease",
    ]) expect(ow(t).outward, t).toBe(true)
  })
  test("gh 带 --repo/-R 的变体", () => {
    for (const t of [
      "gh issue create --title 反馈",
      "gh --repo owner/x issue create --title 反馈",
      "gh -R owner/x pr create --base main",
      "gh api repos/x/y/issues -X POST",
      "cd repo && gh release create v1.0.0",
    ]) expect(ow(t).outward, t).toBe(true)
  })
  test("内联脚本往外发 POST", () => {
    for (const t of [
      'python -c "import requests;requests.post(\'https://hook.example.com/x\')"',
      'node -e "fetch(\'https://api.example.com/x\',{method:\'POST\'})"',
      'pwsh -Command "Invoke-RestMethod -Uri https://api.example.com/x -Method Post"',
    ]) expect(ow(t).outward, t).toBe(true)
  })
  test("只读与本机自检仍然放行", () => {
    for (const t of [
      "git status", "git log --oneline", "git diff", "git commit -m x", "git -C repo status",
      "gh issue list", "gh pr view 4", "gh -R owner/x pr list", "gh run watch 12",
      "curl -s https://api.github.com/repos/x/y/releases/latest",
      "curl -X POST http://127.0.0.1:5199/api/ui",
      "git clone https://github.com/x/y.git",
      "python tools/static_check.py",
    ]) expect(ow(t).outward, t).toBe(false)
  })
})

describe("引擎权限配置与判定同源(issue #10:整段 allow 会让 permission.asked 永不发出)", () => {
  const cfg = enginePermissionConfig()
  test("bash 是模式表而不是整段 allow,通配为 ask(一律先问工坊)", () => {
    expect(typeof cfg.bash).toBe("object")
    expect(cfg.bash["*"]).toBe("ask")
  })
  test("外发写形态被设成 ask", () => {
    for (const k of ["git push*", "*git push*", "git -C * push*", "gh *", "npm publish*", "docker push*", "*curl *--data*", "*scp *", "*twine upload*"]) {
      expect(cfg.bash[k], k).toBe("ask")
    }
  })
  test("外发询问规则排在最后(引擎取最后命中,后面的 allow 会盖掉前面的 ask)", () => {
    const keys = Object.keys(cfg.bash)
    const askIdx = keys.map((k, i) => [k, i] as const).filter(([k]) => cfg.bash[k] === "ask").map(([, i]) => i)
    const allowIdx = keys.map((k, i) => [k, i] as const).filter(([k]) => cfg.bash[k] === "allow" && k !== "*").map(([, i]) => i)
    // 这里必须是 max:要的是「最靠后的那条 allow 回放行」也不能盖过任何 ask。
    // 用 min 的话只要第一个 ask 在开头就能骗过断言,后面混进回放行也照样绿。
    expect(allowIdx.length).toBe(0)
    expect(Math.max(...askIdx)).toBeGreaterThan(0)
  })
  test("ask 规则数够多(覆盖 git/gh/发布/镜像/HTTP/内联脚本)", () => {
    const asks = Object.entries(cfg.bash).filter(([, v]) => v === "ask").length
    expect(asks).toBeGreaterThanOrEqual(25)
  })
  test("复合命令必须被认出来(引擎 glob 不跨空格,只能靠判定层)", () => {
    for (const c of [
      'bash -c "git add . && git push"',
      "git add . && git push origin main",
      "git commit -m x; git push",
      "cd repo && git push",
      "ls && npm publish",
    ]) {
      expect(detectOutwardWrite({ patterns: [c] }).outward, c).toBe(true)
      expect(classifyPermission({ patterns: [c], action: "shell" }, true).reply, c).toBe("confirm")
    }
  })
  test("引擎默认一律要问(实测 glob 不跨空格,复合命令靠模式列不全)", () => {
    expect(cfg.bash["*"]).toBe("ask")
  })
  test("但只读命令不会打扰用户:工坊自动应答 once", () => {
    for (const c of ["git status", "git add .", "ls -la", "python tools/static_check.py", "tar -xf payload.zip", "uv pip install ruff"]) {
      const plan = classifyPermission({ patterns: [c], action: "shell" }, true)
      expect(plan.reply, c).toBe("once")
    }
  })
  test("外发写才进人工确认队列", () => {
    for (const c of ["git push", "git add . && git push origin main", "gh pr create", "npm publish"]) {
      const plan = classifyPermission({ patterns: [c], action: "shell" }, true)
      expect(plan.reply, c).toBe("confirm")
    }
  })
  test("间接外发形态也被认出来(别名/对象存储/远端复制)", () => {
    for (const c of [
      "git -c alias.p=push alias p",
      "git config alias.up 'push --mirror'",
      "scp dist.tar user@host:/var/www/",
      "rsync -avz src/ host:/srv/www/",
      "aws s3 cp dist/ s3://bucket/dist/",
      "twine upload dist/*",
      "curl https://hook.example.com --json '{\"a\":1}'",
    ]) {
      expect(detectOutwardWrite({ patterns: [c] }).outward, c).toBe(true)
    }
  })
  test("生成的配置是干净 JSON:不存在只有大小写不同的重复键", () => {
    // PowerShell ConvertFrom-Json 对这类键直接报错,说明我们的产出物不是「任何解析器都能读」
    const keys = Object.keys(cfg.bash)
    const lower = keys.map((k) => k.toLowerCase())
    expect(new Set(lower).size).toBe(keys.length)
    const txt = JSON.stringify(cfg)
    expect(() => JSON.parse(txt)).not.toThrow()
  })
  test("引擎一律先问 ⇒ 是否打扰用户完全由判定层决定(两侧必须同结论)", () => {
    // 通配是 ask,所以「会不会弹给用户」只剩一个决定者:detectOutwardWrite。
    // 这条测试钉住链条:被判定为外发的命令,绝不能被自动应答吞掉。
    for (const c of ["git push", "git -C /repo push", "git add . && git push", "gh pr create", "npm publish", "docker push x"]) {
      expect(resolveBashEffect(c), c).toBe("ask")
      expect(classifyPermission({ patterns: [c], action: "shell" }, true).reply, c).toBe("confirm")
    }
  })
  test("非外发的常规命令由工坊自动放行(不把无人值守变成点点点)", () => {
    for (const c of ["git status", "ls -la", "python verify.py", "tar -xf payload.zip"]) {
      expect(classifyPermission({ patterns: [c], action: "shell" }, true).reply, c).toBe("once")
    }
  })
})

describe("detectOutwardWrite:什么算「写到仓库外」", () => {
  test("命中:推送/gh 写/外网 POST", () => {
    for (const t of ["git push origin main", "gh issue create --title x", "gh pr create", "gh release create v1.0", "npm publish", "docker push img", 'curl -X POST https://api.example.com/h -d "{}"', "Invoke-RestMethod -Uri https://api.example.com/x -Method Post"]) {
      expect(ow(t).outward, t).toBe(true)
    }
  })
  test("不命中:只读命令、本机自检、拉取", () => {
    for (const t of [
      "git status",
      "git log --oneline -5",
      "git diff",
      "git commit -m x",
      "gh issue list",
      "gh pr view 4",
      "gh run watch 123",
      "curl -s https://api.github.com/repos/fengdiehualian/neko-plugin-workshop/releases/latest",
      "curl -X POST http://127.0.0.1:5199/api/ui",
      "npm install",
      "python tools/static_check.py",
      "tar -xf payload.zip",
    ]) {
      expect(ow(t).outward, t).toBe(false)
    }
  })
  test("中文文案要动作+目标同时出现才算(避免误伤「要不要推送提醒」)", () => {
    expect(ow("要不要我把这次改动推送到 GitHub 远端仓库").outward).toBe(true)
    expect(ow("需要我新建一个 issue 吗").outward).toBe(true)
    expect(ow("要不要每天定时推送提醒给你").outward).toBe(false)
    expect(ow("提醒消息推送到手机").outward).toBe(false)
  })
  test("命令藏在其它字段里也能挖到", () => {
    expect(detectOutwardWrite({ input: { command: "git push" } }).outward).toBe(true)
    expect(detectOutwardWrite({ title: "推送 v1.2.63 到 GitHub" }).outward).toBe(true)
  })
})

describe("classifyPermission:外发写必须真人点头", () => {
  test("普通权限照常放行(不新增等待)", () => {
    expect(classifyPermission({ patterns: ["python verify.py"] }, true).reply).toBe("once")
    expect(classifyPermission({ action: "external_directory", patterns: ["读工作区外"] }, true).reply).toBe("always")
  })
  test("外发写:有界面 → 问人;没界面 → 拒绝", () => {
    const p = { patterns: ["git push origin main"] }
    expect(classifyPermission(p, true).reply).toBe("confirm")
    expect(classifyPermission(p, false).reply).toBe("reject")
    expect(classifyPermission(p, false).outward.why).toContain("git push")
  })
})

describe("classifyQuestionReply:外发意图的提问不许自动选第一项", () => {
  const q = (question, options) => ({ questions: [{ question, options: options.map((label) => ({ label })) }] })
  test("普通澄清题仍选第一项(既有行为不回退)", () => {
    const r = classifyQuestionReply(q("提醒间隔用多久?", ["1 小时", "1 天"]), true)
    expect(r.confirm).toBe(false)
    expect(r.answers).toEqual([["1 小时"]])
  })
  test("外发提问:有界面 → 交给用户", () => {
    const r = classifyQuestionReply(q("要不要把这次改动推送到 GitHub?", ["好,推送", "先不推"]), true)
    expect(r.confirm).toBe(true)
    expect(r.outward.outward).toBe(true)
  })
  test("外发提问:没界面时选「先不」而不是第一个「好」", () => {
    const r = classifyQuestionReply(q("要不要把这次改动推送到 GitHub?", ["好,推送", "先不推"]), false)
    expect(r.confirm).toBe(false)
    expect(r.answers).toEqual([["先不推"]])
  })
  test("外发提问:没有明显拒绝项时选最后一项", () => {
    const r = classifyQuestionReply(q("推送到码云仓库吗?", ["推送", "只推 GitHub"]), false)
    expect(r.answers).toEqual([["只推 GitHub"]])
  })
  test("兜底答案永远不为空(应答丢了会把任务挂死)", () => {
    const r = classifyQuestionReply(q("要发布到 GitHub 吗?", []), true)
    expect(r.answers.length).toBe(1)
    expect(Array.isArray(r.answers[0])).toBe(true)
  })
})

// issue #10 复核意见(2026-11-16)剩下的三个洞,逐条钉住:
// 1) 判定层 HTTP_WRITE_RE 缺 `curl -d` 短写与 `wget --post-*` —— 引擎会问,但判定说「非外发」就自动回 once,没人拦
// 2) SEGMENT_SPLIT 不拆单个 `&` —— `git status & git push` 被整段判成非外发
// 3) `python deploy.py` 这类「脚本内部再外发」要至少按脚本名兜一层
describe("判定层的漏口(issue #10 复核第二轮)", () => {
  test("curl 短写 -d / wget POST:不带 -X 也算外发", () => {
    for (const t of [
      `curl https://hooks.example.com/notify -d '{"a":1}'`,
      "curl -d 'x=1' https://api.example.com/v1/ingest",
      "curl --data x=1 https://api.example.com/v1/ingest",
      "wget --post-data='k=v' https://api.example.com/p",
      "wget --post-file=payload.json https://api.example.com/p",
      "wget --content-post=body.bin https://api.example.com/p",
    ]) expect(ow(t).outward, "应判外发:" + t).toBe(true)
  })
  test("单个 & 分隔的复合命令也要拆开判", () => {
    for (const t of [
      "git status & git push origin main",
      "npm run build & npm publish",
      "tar -czf x.tgz . & curl -T x.tgz https://files.example.com/u",
    ]) expect(ow(t).outward, "应判外发:" + t).toBe(true)
  })
  test("重定向与本地地址不许被误判成分隔/外发", () => {
    for (const t of [
      "python tools/static_check.py > out.log 2>&1",
      "bun test 2>&1 | tail -20",
      "echo a &> /dev/null",
      "curl -s http://127.0.0.1:9/v1/models",
      "git config user.name x && git add .",
    ]) expect(ow(t).outward, "不该弹窗:" + t).toBe(false)
  })
  test("按脚本名兜住「脚本内部再外发」", () => {
    for (const t of ["python deploy.py", "bash scripts/publish.sh", "node tools/release.js", "python build\\upload_plugin.py"]) {
      expect(ow(t).outward, "应判外发:" + t).toBe(true)
    }
    // 反面:日常工具脚本不许弹窗(否则门禁会变成把人问死)
    for (const t of ["python tools/static_check.py", "node tools/check-page.mjs", "node workbench/collect_notices.cjs"]) {
      expect(ow(t).outward, "不该弹窗:" + t).toBe(false)
    }
  })
  test("新加的判定形态在引擎规则表里也必须至少是 ask(两侧同源)", () => {
    for (const t of [
      "curl https://hooks.example.com/notify -d '{\"a\":1}'",
      "wget --post-data='k=v' https://api.example.com/p",
      "git status & git push origin main",
      "python deploy.py",
    ]) expect(resolveBashEffect(t), "引擎不该直接 allow:" + t).not.toBe("allow")
  })
  test("启动自愈要盯住通配 ask 这个不变量(不能只比规则条数)", () => {
    // wb-studio.mjs 是服务端脚本、起进程就有副作用,单测里不能 import,只能按源码断言。
    // 这里钉的是:upgradeEnginePermission 的 stale 条件必须包含 `bash["*"] !== "ask"`——
    // 只比条数的旧写法会让「条数够多但通配仍是 allow」的配置永远不升级(issue #10 复核第二轮第 3 条)。
    const src = readFileSync(join(ROOT, "wb-studio.mjs"), "utf8")
    const i = src.indexOf("function upgradeEnginePermission")
    expect(i, "upgradeEnginePermission 不见了").toBeGreaterThanOrEqual(0)
    const body = src.slice(i, i + 1600)
    expect(body, "stale 判据里没有通配 ask 检查,老配置会永不升级").toMatch(/bash\["\*"\][^]{0,40}!==\s*"ask"/)
  })
})
