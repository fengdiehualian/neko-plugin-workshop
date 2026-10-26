import assert from "node:assert/strict"
import { join } from "node:path"

export async function backupChecks(browser, sources, origin, output, { loadPage, inspectLayout }) {
  const checks = []
  const screenshots = []
  const check = async (name, operation) => {
    try { await operation(); checks.push({ name, passed: true }); console.log(`PASS ${name}`) }
    catch (error) { checks.push({ name, passed: false, error: error.stack }); console.log(`FAIL ${name}: ${error.message}`) }
  }
  for (const locale of Object.keys(sources.messages)) for (const width of [1280, 390]) {
    const loaded = await loadPage(browser, origin, { locale, tab: "settings", wallpaper: "bright" }, width, 900, width === 390 ? "dark" : "light")
    const { page, frame } = loaded
    const text = sources.messages[locale]
    const id = `backup-${locale}-${width}`
    const section = frame.locator(".tm-backup")
    const button = key => section.getByRole("button", { name: text[`panel.backup.${key}`], exact: true })
    const count = op => page.evaluate(value => window.qaBackupCalls.filter(item => item.op === value).length, op)
    const choose = () => section.locator('input[type="file"]').setInputFiles({ name: "synthetic.fc-backup", mimeType: "application/octet-stream", buffer: Buffer.alloc(420000, 97) })
    try {
      await frame.locator("#tm-settings-tab-data").click()
      await check(`${id}-export-and-open-folder`, async () => {
        await button("export").click()
        await section.locator(".tm-backup-path").waitFor()
        assert((await section.locator(".tm-backup-path").innerText()).endsWith(".fc-backup"))
        await button("openFolder").click()
        await page.waitForFunction(() => window.qaOpenedPaths.length === 1)
        assert.equal(await count("export"), 1)
      })
      await check(`${id}-chunk-preview-cancel-and-layout`, async () => {
        await choose()
        await section.locator(".tm-backup-preview").waitFor()
        assert.equal(await count("chunk"), 2)
        assert.equal(await count("apply"), 0)
        assert((await section.innerText()).includes("Old @ role"))
        await button("restore").click()
        await frame.locator(".neko-modal").waitFor()
        assert((await frame.locator(".neko-modal").innerText()).includes(text["panel.backup.confirm"]))
        await frame.getByRole("button", { name: text["panel.cancel"], exact: true }).last().click()
        assert.equal(await count("apply"), 0)
        const layout = await inspectLayout(frame)
        const path = join(output, `${id}.png`)
        await page.screenshot({ path, animations: "disabled" })
        const diagnostics = await page.evaluate(() => window.qaDiagnostics.filter(item => item.type === "neko-hosted-surface-error"))
        screenshots.push({ id, path, layout, diagnostics, errors: [...loaded.errors] })
        assert.deepEqual(loaded.errors, [])
        assert.deepEqual(diagnostics, [])
        assert.equal(layout.documentOverflow, 0)
        assert.equal(layout.contentOverflow, 0)
        assert.deepEqual(layout.issues, [])
      })
      await check(`${id}-confirm-reloads-settings-and-wallpaper`, async () => {
        await button("restore").click()
        await frame.getByRole("button", { name: text["panel.confirm"], exact: true }).last().click()
        await section.locator(".tm-backup-preview").waitFor({ state: "detached" })
        assert.equal(await count("apply"), 1)
        await frame.waitForFunction(() => !document.querySelector(".tm-bg"))
        await frame.locator("#tm-settings-tab-general").click()
        assert.equal(await frame.locator("#tm-settings-general select").first().inputValue(), "Europe/London")
        await frame.locator("#tm-settings-tab-data").click()
        assert(await section.locator(".tm-backup-path").isVisible())
      })
      await check(`${id}-errors-and-recovery`, async () => {
        await page.evaluate(() => { window.qaBackupFailure = "backup_checksum_failed" })
        await choose()
        await section.locator('[role="alert"]').waitFor()
        assert.equal(await section.locator('[role="alert"]').innerText(), text["panel.errors.backupChecksumFailed"])
        await page.evaluate(() => { window.qaBackupFailure = "backup_recovery_required" })
        await button("export").click()
        await button("recover").waitFor()
        assert(await button("export").isDisabled())
        await page.evaluate(() => { window.qaBackupFailure = "" })
        await button("recover").click()
        await button("recover").waitFor({ state: "detached" })
        assert.equal(await count("recover"), 1)
        await frame.waitForFunction(() => !document.querySelector(".tm-backup-actions button").disabled)
        assert(await button("export").isEnabled())
      })
      if (locale === "zh-CN" && width === 390) {
        await check(`${id}-upload-cancel-across-settings-tabs`, async () => {
          await page.evaluate(() => { window.qaDelays["data_backup:chunk:"] = 700 })
          await choose()
          await section.getByRole("button", { name: text["panel.cancel"], exact: true }).click()
          await button("export").waitFor({ state: "visible" })
          await frame.waitForFunction(() => !document.querySelector(".tm-backup-preview"))
          await page.waitForFunction(() => window.qaBackupCalls.some(item => item.op === "abort"))
          assert.equal(await count("apply"), 1)
        })
      }
      if (locale === "zh-CN" && width === 1280) {
        await check(`${id}-reopened-recovery-panel-is-reachable`, async () => {
          await page.evaluate(() => {
            window.qaFixture.backupHealthyState = window.qaFixture.context.state
            window.qaFixture.context.state = { backup_recovery_required: true, status: { enabled: false } }
          })
          await frame.evaluate(payload => window.__NekoRefreshHostedPayload(payload), await page.evaluate(() => window.qaFixture.context))
          await button("recover").waitFor()
          assert.equal(await frame.locator(".tm-tabs").count(), 0)
          assert(await button("export").isDisabled())
          await button("recover").click()
          await frame.locator(".tm-tabs").waitFor()
          assert.equal(await count("recover"), 2)
        })
      }
      await check(`${id}-reset-clears-records-and-rejects-old-statistics`, async () => {
        const navigate = tab => frame.locator(".tm-tabs").getByRole("button", { name: text[`panel.tab.${tab}`], exact: true }).click()
        const resetButton = () => frame.getByRole("button", { name: text["actions.reset.label"], exact: true })
        const callCount = action => page.evaluate(action => window.qaMessages.filter(item => item.payload?.actionId === action).length, action)
        await page.evaluate(() => { window.qaHoldReplies.get_stats = true })
        await navigate("moment")
        await page.waitForFunction(() => window.qaHeldReplies.get_stats?.length === 1)
        await navigate("settings")
        await frame.locator("#tm-settings-tab-data").click()
        const before = await callCount("reset_all")
        await resetButton().click()
        assert((await frame.locator(".neko-modal").innerText()).includes(text["actions.reset.confirm"]))
        await frame.getByRole("button", { name: text["panel.cancel"], exact: true }).last().click()
        assert.equal(await callCount("reset_all"), before)
        await page.evaluate(() => { window.qaFailNext = "reset_all" })
        await resetButton().click()
        await frame.getByRole("button", { name: text["panel.confirm"], exact: true }).last().click()
        await frame.waitForFunction(expected =>
          [...document.querySelectorAll(".neko-toast")].some(item => item.textContent.includes(expected)), text["panel.errors.persistFailed"])
        assert(await page.evaluate(() => window.qaFixture.journal.length > 0))
        await page.evaluate(() => { window.qaHoldReplies.get_stats = false })
        await resetButton().click()
        await frame.getByRole("button", { name: text["panel.confirm"], exact: true }).last().click()
        await frame.waitForFunction(expected =>
          [...document.querySelectorAll(".neko-toast")].some(item => item.textContent.includes(expected)), text["panel.settings.resetDone"])
        const resetArgs = await page.evaluate(() => window.qaMessages.filter(item => item.payload?.actionId === "reset_all").at(-1).payload.args)
        assert.equal(resetArgs.expected_lanlan, await page.evaluate(() => window.qaFixture.context.state.lanlan))
        await page.evaluate(() => { window.qaHeldReplies.get_stats.splice(0).forEach(reply => reply()) })
        await navigate("moment")
        await frame.getByText(text["panel.stats.heatEmpty"], { exact: true }).waitFor()
        assert(await frame.getByText(text["panel.stats.monthEmpty"], { exact: true }).isVisible())
        assert.equal(await callCount("get_stats"), 2)
        await navigate("diary")
        for (const [tabKey, emptyKey] of [
          ["panel.diary.tabTime", "panel.diary.emptyTitle"],
          ["panel.journal.tabJournal", "panel.journal.emptyTitle"],
          ["panel.review.tabReview", "panel.review.emptyTitle"],
        ]) {
          await frame.getByRole("button", { name: text[tabKey], exact: true }).click()
          await frame.getByText(text[emptyKey], { exact: true }).waitFor()
        }
        assert.deepEqual(loaded.errors, [])
      })
    } finally { await loaded.context.close() }
  }
  return { checks, screenshots }
}
