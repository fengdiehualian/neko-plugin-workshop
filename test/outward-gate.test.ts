import { describe, expect, test } from "bun:test"
import { detectOutwardWrite, classifyPermission, classifyQuestionReply, enginePermissionConfig } from "../wb-agent-lib.mjs"

// 对应 GitHub issue #9/#10:外发写既要在事件侧被认出来,也要在引擎侧真的会被拦下来问
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
  test("bash 是模式表而不是整段 allow,通配仍为 allow", () => {
    expect(typeof cfg.bash).toBe("object")
    expect(cfg.bash["*"]).toBe("allow")
  })
  test("外发写形态被设成 ask", () => {
    for (const k of ["git push*", "*git push*", "git -C * push*", "gh *", "npm publish*", "docker push*", "*curl *-X *"]) {
      expect(cfg.bash[k], k).toBe("ask")
    }
  })
  test("只读形态排在 ask 之后回放行(引擎取最后命中)", () => {
    for (const k of ["git status*", "git -C * status*", "gh issue list*", "gh pr view*"]) {
      expect(cfg.bash[k], k).toBe("allow")
    }
    const keys = Object.keys(cfg.bash)
    const lastAsk = keys.map((k, i) => [k, i]).filter(([, i]) => cfg.bash[keys[i]] === "ask").map(([, i]) => i)
    const firstAllowBack = keys.findIndex((k, i) => i > 0 && cfg.bash[k] === "allow" && /\b(status|issue list|pr view)\b/.test(k))
    expect(firstAllowBack > Math.min(...lastAsk)).toBe(true)
  })
  test("ask 规则数够多(覆盖 git/gh/发布/镜像/HTTP/内联脚本)", () => {
    const asks = Object.entries(cfg.bash).filter(([, v]) => v === "ask").length
    expect(asks).toBeGreaterThanOrEqual(25)
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
