import assert from "node:assert/strict"
import { join } from "node:path"

export async function moodChecks(browser, sources, origin, output, { loadPage, inspectLayout }) {
  const checks = []
  const screenshots = []
  const check = async (name, operation) => {
    try { await operation(); checks.push({ name, passed: true }); console.log(`PASS ${name}`) }
    catch (error) { checks.push({ name, passed: false, error: error.stack }); console.log(`FAIL ${name}: ${error.message}`) }
  }
  for (const locale of Object.keys(sources.messages)) for (const width of [1280, 390]) {
    const loaded = await loadPage(browser, origin, { locale, tab: "mood", moodCase: "active" }, width, 900, width === 390 ? "dark" : "light")
    const { page, frame, context, errors } = loaded
    const text = sources.messages[locale]
    const id = `mood-autonomy-${locale}-${width}`
    const refresh = async () => {
      await frame.evaluate(payload => window.__NekoRefreshHostedPayload(payload), await page.evaluate(() => window.qaFixture.context))
    }
    const calls = () => page.evaluate(() => window.qaMessages.filter(msg => msg.method === "call" && msg.payload.actionId === "lift_mood"))
    try {
      await check(`${id}-read-only-history-refresh-and-layout`, async () => {
        assert.equal(await frame.locator(".tm-mood-event").count(), 12)
        assert((await frame.locator(".tm-content").innerText()).includes(text["panel.mood.sourceSelf"]))
        const timeline = await frame.locator(".tm-mood-history").first().innerText()
        assert(timeline.includes(text["panel.mood.sourceUser"]))
        assert(timeline.includes(text["panel.mood.sourceTimer"]))
        const reason = frame.locator(".tm-mood-current").locator("..").locator(".tm-mood-reason").first()
        await reason.locator("summary").click()
        assert((await reason.innerText()).includes("<img src=x onerror=alert(1)>"))
        assert.equal(await reason.locator("img").count(), 0)
        await frame.locator(".tm-mood-earlier > summary").click()
        const stored = await page.evaluate(() => JSON.stringify(window.qaFixture.context.state.mood))
        if (width === 390 && locale === "zh-CN") {
          const before = await page.evaluate(() => window.qaRefreshes)
          await page.waitForFunction(value => window.qaRefreshes > value, before, { timeout: 6500 })
        } else await refresh()
        assert(await reason.evaluate(node => node.open))
        assert(await frame.locator(".tm-mood-earlier").evaluate(node => node.open))
        assert.equal(await page.evaluate(() => JSON.stringify(window.qaFixture.context.state.mood)), stored)
        assert.equal((await calls()).length, 0)
        const path = join(output, `${id}.png`)
        await frame.locator(".tm-content").evaluate(node => { node.scrollTop = 0 })
        await page.screenshot({ path, animations: "disabled" })
        const layout = await inspectLayout(frame)
        const diagnostics = await page.evaluate(() => window.qaDiagnostics.filter(item => item.type === "neko-hosted-surface-error"))
        screenshots.push({ id, path, layout, errors: [...errors], diagnostics })
        assert.equal(layout.documentOverflow, 0)
        assert.equal(layout.contentOverflow, 0)
        assert.deepEqual(layout.issues, [])
        assert.deepEqual(errors, [])
        assert.deepEqual(diagnostics, [])
      })
      await check(`${id}-cancel-and-end-preserves-observation`, async () => {
        const label = text["actions.liftMood.label"]
        await frame.getByRole("button", { name: label, exact: true }).click()
        assert((await frame.locator(".neko-modal").innerText()).includes(text["actions.liftMood.confirm"]))
        await frame.getByRole("button", { name: text["panel.cancel"], exact: true }).last().click()
        assert.equal((await calls()).length, 0)
        const before = await page.evaluate(() => ({ affect: window.qaFixture.context.state.mood.affect, role: window.qaFixture.context.state.lanlan, start: window.qaFixture.context.state.mood.started_at }))
        await frame.getByRole("button", { name: label, exact: true }).click()
        await frame.getByRole("button", { name: text["panel.confirm"], exact: true }).last().click()
        await page.waitForFunction(() => !window.qaFixture.context.state.mood.active)
        const actual = await calls()
        assert.equal(actual.length, 1)
        assert.deepEqual(actual[0].payload.args, { expected_lanlan: before.role, expected_started_at: before.start })
        assert.deepEqual(await page.evaluate(() => window.qaFixture.context.state.mood.affect), before.affect)
        await frame.getByRole("button", { name: label, exact: true }).waitFor({ state: "detached" })
        assert.equal(await page.evaluate(() => window.qaFixture.context.state.mood.history[0].origin), "user")
      })
      await check(`${id}-role-change-during-confirmation-is-rejected`, async () => {
        await page.evaluate(() => {
          const mood = window.qaFixture.context.state.mood
          mood.active = true; mood.action = "ripple"; mood.started_at = 123456; mood.origin = "self"
        })
        await refresh()
        await frame.getByRole("button", { name: text["actions.liftMood.label"], exact: true }).click()
        await page.evaluate(() => {
          const state = window.qaFixture.context.state
          state.lanlan = "AnotherRole"
          state.mood = { active: false, system_enabled: true, history: [], affect: { valence: 0, arousal: 0 } }
        })
        await refresh()
        await frame.getByRole("button", { name: text["panel.confirm"], exact: true }).last().click()
        await frame.waitForFunction(expected => [...document.querySelectorAll(".neko-toast")].some(node => node.textContent.includes(expected)), text["panel.errors.moodChanged"])
        assert.equal((await calls()).length, 1)
        assert.equal(await frame.locator(".tm-mood-event").count(), 0)
        assert((await frame.locator(".tm-content").innerText()).includes(text["panel.mood.historyEmpty"]))
      })
      await check(`${id}-new-choice-and-unrefreshed-backend-change-reject-old-confirmation`, async () => {
        const setChoice = async started => {
          await page.evaluate(started => {
            window.qaFixture.context.state.mood = {
              active: true, action: "ripple", system_enabled: true, started_at: started,
              origin: "self", history: [], affect: { valence: 0.2, arousal: 0.3 },
            }
          }, started)
          await refresh()
        }
        await setChoice(111)
        await frame.getByRole("button", { name: text["actions.liftMood.label"], exact: true }).click()
        await setChoice(222)
        await frame.getByRole("button", { name: text["panel.confirm"], exact: true }).last().click()
        assert.equal((await calls()).length, 1)
        await frame.getByRole("button", { name: text["actions.liftMood.label"], exact: true }).click()
        await page.evaluate(() => { window.qaChangeMoodBeforeLift = true })
        await frame.getByRole("button", { name: text["panel.confirm"], exact: true }).last().click()
        await page.waitForFunction(() => window.qaMessages.filter(msg => msg.method === "call" && msg.payload.actionId === "lift_mood").length === 2)
        await frame.waitForFunction(expected => [...document.querySelectorAll(".neko-toast")].some(node => node.textContent.includes(expected)), text["panel.errors.moodChanged"])
        assert(await page.evaluate(() => window.qaFixture.context.state.mood.active))
      })
    } finally { await context.close() }
  }
  for (const moodCase of ["legacy", "empty", "disabled"]) {
    const { page, frame, context } = await loadPage(browser, origin, { tab: "mood", moodCase, locale: "zh-CN" }, 390, 900, "light")
    const text = sources.messages["zh-CN"]
    try {
      await check(`mood-${moodCase}-compatible-read-only-state`, async () => {
        const body = await frame.locator(".tm-content").innerText()
        if (moodCase === "legacy") assert(body.includes(text["panel.mood.sourceUnknown"]))
        else assert.equal(await frame.getByRole("button", { name: text["actions.liftMood.label"], exact: true }).count(), 0)
        if (moodCase === "disabled") {
          assert.equal(await frame.locator(".tm-mood-event").count(), 12)
          assert.equal(await frame.locator(".tm-mood-observation-value").first().innerText(), "\u2014")
        } else assert(body.includes(text["panel.mood.historyEmpty"]))
        assert.equal(await frame.getByRole("button", { name: text["panel.mood.diaryLink"], exact: true }).count(), 0)
        assert.equal(await page.evaluate(() => window.qaMessages.filter(msg => msg.method === "call" && msg.payload.actionId === "lift_mood").length), 0)
      })
    } finally { await context.close() }
  }
  return { checks, screenshots }
}
