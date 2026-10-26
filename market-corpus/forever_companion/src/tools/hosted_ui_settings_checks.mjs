import assert from "node:assert/strict"
import { join } from "node:path"

export async function selectSettings(frame, section) {
  await frame.locator(`#tm-settings-tab-${section}`).click()
}

export async function selectMedia(frame, kind) {
  await selectSettings(frame, "appearance")
  await frame.locator(`#tm-media-tab-${kind}`).click()
}

export async function openImport(frame, kind = "image") {
  await selectSettings(frame, "appearance")
  const drawer = frame.locator(".tm-media-add")
  if (!await drawer.evaluate(element => element.open)) await drawer.locator("summary").click()
  await drawer.locator(".neko-segmented-button").nth(["image", "video", "directory"].indexOf(kind)).click()
}

export async function openStorage(frame) {
  await selectSettings(frame, "appearance")
  const drawer = frame.locator(".tm-media-storage-disclosure")
  if (!await drawer.evaluate(element => element.open)) await drawer.locator("summary").click()
}

export async function settingsChecks(browser, sources, origin, output, helpers) {
  const { loadPage, inspectLayout, recordFixture } = helpers
  const fixture = await recordFixture(browser, output, false)
  sources.qaVideoFixture = fixture.source
  const checks = []
  const screenshots = []
  const locales = ["zh-CN", "zh-TW", "en", "ja", "ko", "ru", "es", "pt"]
  async function check(name, operation) {
    try {
      await operation()
      checks.push({ name, passed: true })
      console.log(`PASS ${name}`)
    } catch (error) {
      checks.push({ name, passed: false, error: error.stack })
      console.log(`FAIL ${name}: ${error.message}`)
    }
  }
  async function capture(loaded, id) {
    const layout = await inspectLayout(loaded.frame)
    const diagnostics = await loaded.page.evaluate(() => window.qaDiagnostics.filter(message => message.type === "neko-hosted-surface-error"))
    const path = join(output, `${id}.png`)
    await loaded.page.screenshot({ path, animations: "disabled" })
    screenshots.push({ id, path, layout, errors: [...loaded.errors], diagnostics })
    assert(layout.documentOverflow <= 2 && layout.contentOverflow <= 2, `${id}: horizontal overflow`)
    assert.deepEqual(layout.issues, [], `${id}: clipped controls`)
    assert.deepEqual(loaded.errors, [], `${id}: browser errors`)
    assert.deepEqual(diagnostics, [], `${id}: hosted diagnostics`)
    assert.deepEqual(layout.errorNodes, [])
  }
  for (const locale of locales) for (const width of [1280, 390]) for (const theme of ["light", "dark"]) {
    const id = `settings-${locale}-${width}-${theme}`
    const loaded = await loadPage(browser, origin, {
      locale, wallpaper: theme === "dark" ? "bright" : "none", video: "true", tab: "settings",
    }, width, 900, theme)
    const { frame, page } = loaded
    const text = sources.messages[locale]
    const offset = await page.evaluate(() => window.qaMessages.length)
    try {
      await check(`${id}-grouping-and-keyboard-media-filter`, async () => {
        assert(await frame.locator("#tm-settings-appearance").isVisible())
        assert(!await frame.locator("#tm-settings-general").isVisible())
        assert(!await frame.locator("#tm-settings-data").isVisible())
        assert.equal(await frame.locator(".tm-settings .tm-save").count(), 1)
        assert.equal(await frame.locator(".tm-settings .neko-card").count(), 0)
        assert.equal(await frame.locator(".tm-settings-surface").count(), 1)
        assert.equal(await frame.locator(".tm-settings-surface .tm-save").count(), 0)
        const surfaceStyle = await frame.locator(".tm-settings-surface").evaluate(element => {
          const style = getComputedStyle(element)
          return {
            borders: ["Top", "Right", "Bottom", "Left"].map(side => ({
              width: style[`border${side}Width`], style: style[`border${side}Style`], color: style[`border${side}Color`],
            })),
            radii: ["TopLeft", "TopRight", "BottomLeft", "BottomRight"].map(corner => style[`border${corner}Radius`]),
            shadow: style.boxShadow,
          }
        })
        for (const border of surfaceStyle.borders) {
          assert.equal(border.width, "1px")
          assert.equal(border.style, "solid")
          assert.notEqual(border.color, "rgba(0, 0, 0, 0)")
        }
        assert(surfaceStyle.radii.every(radius => parseFloat(radius) > 0))
        assert.notEqual(surfaceStyle.shadow, "none")
        const activeStyle = await frame.locator('.tm-settings-tab[aria-selected="true"]').evaluate(element => ({
          border: getComputedStyle(element).borderBottomColor,
          width: getComputedStyle(element).borderBottomWidth,
        }))
        assert.notEqual(activeStyle.border, "rgba(0, 0, 0, 0)")
        assert.equal(activeStyle.width, "2px")
        for (const selector of [".tm-media-add", ".tm-media-storage-disclosure", ".tm-appearance-adjust"]) {
          assert.equal(await frame.locator(selector).evaluate(element => element.open), false)
        }
        assert.equal(await frame.locator('[data-media-kind="image"]').count(), 3)
        assert.equal(await frame.locator('[data-media-kind="video"]').count(), 0)
        assert.deepEqual(await frame.locator(".tm-media-count").allTextContents(), ["3", "1"])
        await capture(loaded, `${id}-images`)
        const background = await frame.locator(".tm-wallpaper-current").innerText()
        await frame.locator("#tm-media-tab-image").focus()
        await frame.locator("#tm-media-tab-image").press("ArrowRight")
        assert.equal(await frame.locator("#tm-media-tab-video").getAttribute("aria-selected"), "true")
        assert.equal(await frame.locator('[data-media-kind="image"]').count(), 0)
        assert.equal(await frame.locator('[data-media-kind="video"]').count(), 1)
        assert.equal(await frame.locator(".tm-gallery-none").count(), 1)
        assert.equal(await frame.locator(".tm-wallpaper-current").innerText(), background)
        assert.equal(await page.evaluate(offset => window.qaMessages.slice(offset).filter(message =>
          ["set_panel_appearance", "update_settings", "gallery_remove"].includes(message.payload?.actionId)).length, offset), 0)
        await frame.locator('[data-item-id="qa-video"] .tm-gallery-pick').click()
        assert.equal(await frame.locator(".tm-appearance-save .tm-save-hint").innerText(), text["panel.appearance.unsavedHint"])
        await capture(loaded, `${id}-videos`)
      })
      await check(`${id}-drafts-survive-sections-and-save-from-data`, async () => {
        await frame.locator("#tm-settings-tab-appearance").focus()
        await frame.locator("#tm-settings-tab-appearance").press("ArrowRight")
        assert(await frame.locator("#tm-settings-general").isVisible())
        await frame.locator("#tm-settings-general select").selectOption("Asia/Tokyo")
        const advanced = frame.locator("#tm-settings-general details")
        assert.equal(await advanced.evaluate(element => element.open), false)
        await advanced.locator("summary").click()
        await advanced.locator(".tm-sw-track").click()
        if (locale === "zh-CN") await capture(loaded, `${id}-general`)
        await frame.locator("#tm-settings-tab-general").press("End")
        assert(await frame.locator("#tm-settings-data").isVisible())
        assert((await frame.locator(".tm-settings-reset-row").innerText()).includes("测试角色"))
        const resetCalls = await page.evaluate(() => window.qaMessages.filter(message => message.payload?.actionId === "reset_all").length)
        await frame.getByRole("button", { name: text["actions.reset.label"], exact: true }).click()
        await frame.getByRole("button", { name: text["panel.cancel"], exact: true }).last().click()
        assert.equal(await page.evaluate(() => window.qaMessages.filter(message => message.payload?.actionId === "reset_all").length), resetCalls)
        if (locale === "zh-CN") await capture(loaded, `${id}-data`)
        const before = await page.evaluate(() => window.qaMessages.length)
        await frame.locator(".tm-save button").click()
        await page.waitForFunction(() => window.qaFixture.appearance.bg_id === "qa-video" &&
          window.qaFixture.context.state.settings.timezone === "Asia/Tokyo")
        const calls = await page.evaluate(offset => window.qaMessages.slice(offset).filter(message => message.method === "call"), before)
        assert.equal(calls.filter(message => message.payload.actionId === "set_panel_appearance").length, 1)
        assert.deepEqual(calls.find(message => message.payload.actionId === "update_settings").payload.args,
          { timezone: "Asia/Tokyo", debug_mode: true })
        await selectSettings(frame, "appearance")
        assert.equal(await frame.locator("#tm-media-tab-video").getAttribute("aria-selected"), "true")
        assert.equal(await frame.locator(".tm-gallery-applied .tm-gallery-pick").getAttribute("aria-pressed"), "true")
        await selectMedia(frame, "image")
        assert.equal(await page.evaluate(() => window.qaFixture.appearance.bg_id), "qa-video")
      })
      await check(`${id}-import-methods-and-storage-keep-local-drafts`, async () => {
        await openImport(frame)
        const drawer = frame.locator(".tm-media-add")
        assert(await drawer.locator('input[accept="image/*"]').isVisible())
        assert(!await frame.locator(".tm-wallpaper-video-input").isVisible())
        await drawer.locator(".tm-media-import-fields .neko-segmented-button").last().click()
        await selectSettings(frame, "general")
        await selectSettings(frame, "appearance")
        assert.equal(await drawer.evaluate(element => element.open), true)
        assert.equal(await drawer.locator(".tm-media-import-fields .neko-segmented-button.is-active").innerText(), text["panel.appearance.modeRaw"])
        await openImport(frame, "video")
        assert(await frame.locator(".tm-wallpaper-video-input").isVisible())
        assert(!await drawer.locator('input[accept="image/*"]').isVisible())
        await openImport(frame, "directory")
        assert(await frame.locator(".tm-wallpaper-directory-input").isVisible())
        assert(!await frame.locator(".tm-wallpaper-video-input").isVisible())
        await openStorage(frame)
        await frame.locator(".tm-media-policy input").first().fill("300")
        await selectSettings(frame, "data")
        await selectSettings(frame, "appearance")
        assert.equal(await frame.locator(".tm-media-policy input").first().inputValue(), "300")
        assert.equal(await page.evaluate(() => window.qaFixture.mediaStorage.single_limit_bytes), 256 * 1024 * 1024)
        if (locale === "zh-CN") await capture(loaded, `${id}-import-storage`)
        else await inspectLayout(frame).then(layout => assert.deepEqual(layout.issues, []))
      })
    } finally { await loaded.context.close() }
  }
  const empty = await loadPage(browser, origin, { locale: "zh-CN", wallpaper: "none", tab: "settings" }, 320, 600, "light")
  try {
    await check("settings-empty-video-category-small-window", async () => {
      await selectMedia(empty.frame, "video")
      assert.equal(await empty.frame.locator(".tm-gallery-tile[data-item-id]").count(), 0)
      assert(await empty.frame.locator(".tm-gallery-empty").isVisible())
      assert(await empty.frame.locator(".tm-gallery-none").isVisible())
      await capture(empty, "settings-320-empty-video")
    })
  } finally { await empty.context.close() }
  const edits = await loadPage(browser, origin, { locale: "zh-CN", wallpaper: "bright", video: "true", tab: "settings" }, 390, 900, "light")
  try {
    await check("settings-cross-media-revert-reveals-saved-image", async () => {
      await selectMedia(edits.frame, "video")
      await edits.frame.locator('[data-item-id="qa-video"] .tm-gallery-pick').click()
      await edits.frame.locator(".tm-appearance-save button").click()
      assert.equal(await edits.frame.locator("#tm-media-tab-image").getAttribute("aria-selected"), "true")
      assert.equal(await edits.frame.locator(".tm-gallery-active").getAttribute("data-item-id"), "qa-wallpaper")
    })
    await check("settings-image-import-reveals-image-category-without-selecting", async () => {
      await selectMedia(edits.frame, "video")
      await openImport(edits.frame, "image")
      const before = await edits.page.evaluate(() => ({ selected: window.qaFixture.appearance.bg_id, count: window.qaFixture.gallery.length }))
      const data = await edits.page.evaluate(() => window.qaFixture.image.split(",")[1])
      await edits.frame.locator('input[accept="image/*"]').setInputFiles({
        name: "new-image.png", mimeType: "image/png", buffer: Buffer.from(data, "base64"),
      })
      await edits.page.waitForFunction(count => window.qaFixture.gallery.length === count + 1, before.count)
      await edits.frame.waitForFunction(() => document.querySelector("#tm-media-tab-image").getAttribute("aria-selected") === "true")
      assert.equal(await edits.frame.locator('[data-media-kind="image"]').count(), 4)
      assert.equal(await edits.page.evaluate(() => window.qaFixture.appearance.bg_id), before.selected)
    })
    await check("settings-import-error-stays-visible-outside-collapsed-method", async () => {
      const count = await edits.page.evaluate(() => window.qaFixture.gallery.length)
      await edits.frame.locator('input[accept="image/*"]').setInputFiles({
        name: "broken.png", mimeType: "image/png", buffer: Buffer.from("invalid image"),
      })
      await edits.frame.waitForSelector('.tm-appearance .neko-alert[data-tone="danger"]')
      await edits.frame.locator(".tm-media-add summary").click()
      await selectMedia(edits.frame, "video")
      assert(await edits.frame.locator('.tm-appearance .neko-alert[data-tone="danger"]').isVisible())
      assert.equal(await edits.page.evaluate(() => window.qaFixture.gallery.length), count)
    })
    await check("settings-delete-last-video-preserves-images-and-current-wallpaper", async () => {
      await edits.frame.locator('[data-item-id="qa-video"] .tm-gallery-del').click()
      await edits.frame.getByRole("button", { name: sources.messages["zh-CN"]["panel.confirm"], exact: true }).last().click()
      await edits.page.waitForFunction(() => !window.qaFixture.gallery.some(item => item.kind === "video"))
      assert(await edits.frame.locator(".tm-gallery-empty").isVisible())
      assert.deepEqual(await edits.frame.locator(".tm-media-count").allTextContents(), ["4", "0"])
      assert.equal(await edits.page.evaluate(() => window.qaFixture.appearance.bg_id), "qa-wallpaper")
      await selectMedia(edits.frame, "image")
      assert.equal(await edits.frame.locator('[data-media-kind="image"]').count(), 4)
      await capture(edits, "settings-import-error-and-images")
    })
  } finally { await edits.context.close() }
  return { screenshots, checks }
}
