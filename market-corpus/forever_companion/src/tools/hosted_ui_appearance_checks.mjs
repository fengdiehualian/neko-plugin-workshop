import assert from "node:assert/strict"
import { join } from "node:path"

const LOCALES = ["zh-CN", "zh-TW", "en", "ja", "ko", "ru", "es", "pt"]
const ROOT = ".tm-appearance"
const DISCLOSURE = ".tm-appearance-adjust"
const SUMMARY = ".tm-appearance-adjust-summary"
const BODY = ".tm-appearance-adjust-body"
const RANGES = `${BODY} input[type="range"]`
const RANGE_KEYS = ["blur", "dim", "brightness", "saturate", "contrast", "glass", "card_alpha", "text_weight"]
const READ_ACTIONS = ["get_panel_gallery", "get_gallery_image"]

function option(name, fallback) {
  const index = process.argv.indexOf(`--${name}`)
  return index < 0 ? fallback : process.argv[index + 1]
}

export async function expandAppearance(frame) {
  assert.equal(await frame.locator(DISCLOSURE).count(), 1, "Appearance adjustments need their native disclosure")
  if (!await frame.locator(DISCLOSURE).evaluate(element => element.open)) {
    await frame.locator(SUMMARY).click()
  }
  assert(await frame.locator(BODY).isVisible(), "Expand adjustments before reading or changing their controls")
}

async function actionSnapshot(page) {
  return page.evaluate(() => ({
    offset: window.qaMessages.length,
    capabilities: JSON.stringify(window.qaFixture.context.state.capabilities),
    settings: JSON.stringify(window.qaFixture.context.state.settings),
    appearance: JSON.parse(JSON.stringify(window.qaFixture.appearance)),
  }))
}

async function callsSince(page, before) {
  return page.evaluate(offset => window.qaMessages.slice(offset).filter(message => message.method === "call"), before.offset)
}

async function assertNoBusinessMutation(page, before, maySave = false) {
  const current = await actionSnapshot(page)
  assert.equal(current.capabilities, before.capabilities, "Appearance interactions changed business capabilities")
  assert.equal(current.settings, before.settings, "Appearance interactions changed business settings")
  const allowed = [...READ_ACTIONS, ...(maySave ? ["set_panel_appearance"] : [])]
  assert.deepEqual((await callsSince(page, before)).filter(message => !allowed.includes(message.payload.actionId)), [], "Appearance controls must not call unrelated plugin actions")
}

async function assertReachable(frame, selector, index = 0) {
  const target = frame.locator(selector).nth(index)
  await target.scrollIntoViewIfNeeded()
  const position = await target.evaluate(element => {
    const box = node => {
      if (!node) return null
      const bounds = node.getBoundingClientRect()
      return { left: bounds.left, right: bounds.right, top: bounds.top, bottom: bounds.bottom, width: bounds.width, height: bounds.height }
    }
    const rect = element.getBoundingClientRect()
    const scroll = document.querySelector(".tm-content")
    const content = scroll.getBoundingClientRect()
    const details = document.querySelector(".tm-appearance-adjust")
    const x = (rect.left + rect.right) / 2
    const y = (rect.top + rect.bottom) / 2
    const hit = document.elementFromPoint(x, y)
    return {
      inside: x >= content.left && x <= content.right && y >= content.top && y <= content.bottom,
      reached: !!hit && (hit === element || element.contains(hit)),
      point: { x, y },
      hit: hit ? { tag: hit.tagName, className: String(hit.className || ""), text: (hit.textContent || "").trim().slice(0, 100) } : null,
      bounds: box(element),
      contentBounds: box(scroll),
      scroll: { top: scroll.scrollTop, height: scroll.scrollHeight, clientHeight: scroll.clientHeight, paddingBottom: getComputedStyle(scroll).paddingBottom },
      summaryBounds: box(document.querySelector(".tm-appearance-adjust-summary")),
      details: { open: details.open, bounds: box(details) },
      saveBounds: box(document.querySelector(".tm-save")),
      targetStyle: { visibility: getComputedStyle(element).visibility, pointerEvents: getComputedStyle(element).pointerEvents },
    }
  })
  assert(position.inside && position.reached, `${selector}[${index}]: control is clipped or covered after scrolling: ${JSON.stringify(position)}`)
}

async function geometry(frame) {
  return frame.locator(ROOT).evaluate(root => {
    const box = element => {
      const rect = element.getBoundingClientRect()
      return { left: rect.left, right: rect.right, top: rect.top, bottom: rect.bottom, width: rect.width, height: rect.height }
    }
    const rootBox = box(root)
    const issues = []
    const visible = element => {
      let opacity = 1
      for (let current = element; current && root.contains(current); current = current.parentElement) {
        if (current.tagName === "DETAILS" && !current.open) {
          const summary = current.querySelector(":scope > summary")
          if (!summary?.contains(element)) return false
        }
        const style = getComputedStyle(current)
        if (style.display === "none" || style.visibility === "hidden") return false
        opacity *= Number(style.opacity)
      }
      return opacity > 0.05
    }
    const texts = []
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT)
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
      const element = node.parentElement
      const label = node.textContent.trim()
      if (!label || !visible(element) || element.closest('[aria-hidden="true"]')) continue
      const style = getComputedStyle(element)
      if (element.matches(".tm-gallery-name") && style.textOverflow === "ellipsis") continue
      const range = document.createRange()
      range.selectNodeContents(node)
      for (const rect of range.getClientRects()) {
        if (rect.width < 1 || rect.height < 1) continue
        if (rect.left < rootBox.left - 2 || rect.right > rootBox.right + 2) issues.push({ kind: "appearance-copy-outside", label: label.slice(0, 90) })
        for (let ancestor = element; ancestor && root.contains(ancestor); ancestor = ancestor.parentElement) {
          const style = getComputedStyle(ancestor)
          const bounds = ancestor.getBoundingClientRect()
          if ((["hidden", "clip"].includes(style.overflowX) && (rect.left < bounds.left - 2 || rect.right > bounds.right + 2)) ||
              (["hidden", "clip"].includes(style.overflowY) && (rect.top < bounds.top - 2 || rect.bottom > bounds.bottom + 2))) {
            issues.push({ kind: "appearance-clipped-copy", label: label.slice(0, 90), className: ancestor.className })
            break
          }
        }
        texts.push({ node, label: label.slice(0, 90), rect })
      }
    }
    for (let left = 0; left < texts.length; left += 1) for (let right = left + 1; right < texts.length; right += 1) {
      const a = texts[left]
      const b = texts[right]
      if (a.node === b.node) continue
      const x = Math.min(a.rect.right, b.rect.right) - Math.max(a.rect.left, b.rect.left)
      const y = Math.min(a.rect.bottom, b.rect.bottom) - Math.max(a.rect.top, b.rect.top)
      if (x > 2 && y > 2) issues.push({ kind: "appearance-copy-overlap", labels: [a.label, b.label], x, y })
    }
    return {
      root: rootBox,
      disclosure: box(root.querySelector(".tm-appearance-adjust")),
      summary: box(root.querySelector(".tm-appearance-adjust-summary")),
      body: box(root.querySelector(".tm-appearance-adjust-body")),
      opened: root.querySelector(".tm-appearance-adjust").open,
      textCount: texts.length,
      issues,
    }
  })
}

async function rangeValues(frame) {
  return frame.locator(RANGES).evaluateAll(elements => elements.map(element => Number(element.value)))
}

async function editVisibleParameters(loaded, before, diagnosticPath) {
  const { frame } = loaded
  if (!before.appearance.bg_id) await frame.locator(".tm-gallery-pick").nth(1).click()
  await expandAppearance(frame)
  await frame.locator(`${BODY} .neko-segmented-button`).nth(1).click()
  await frame.locator(`${BODY} .tm-pos-cell`).last().click()
  const ranges = frame.locator(RANGES)
  const expected = { ...before.appearance, bg_id: "qa-wallpaper", fill: "contain", position: "right bottom" }
  for (const [index, key] of RANGE_KEYS.entries()) {
    const range = ranges.nth(index)
    assert(await range.isVisible() && await range.isEnabled(), `${key}: only visible enabled sliders may be edited`)
    await range.scrollIntoViewIfNeeded()
    if (index === RANGE_KEYS.length - 1) {
      try { await assertReachable(frame, RANGES, index) }
      catch (error) {
        if (diagnosticPath) {
          await loaded.page.screenshot({ path: diagnosticPath, animations: "disabled" })
          error.message += `\nScreenshot: ${diagnosticPath}`
        }
        throw error
      }
    }
    await range.focus()
    const previous = Number(await range.inputValue())
    const minimum = Number(await range.getAttribute("min"))
    const maximum = Number(await range.getAttribute("max"))
    const step = Number(await range.getAttribute("step"))
    const next = previous + step <= maximum ? previous + step : Math.max(minimum, previous - step)
    await range.press(next > previous ? "ArrowRight" : "ArrowLeft")
    const actual = Number(await range.inputValue())
    assert(Math.abs(actual - next) < 0.000001, `${key}: keyboard adjustment did not reach the original callback`)
    expected[key] = actual
  }
  return expected
}

export async function appearanceChecks(browser, sources, origin, output, helpers) {
  const { loadPage, inspectLayout } = helpers
  const locales = option("appearance-locales", LOCALES.join(",")).split(",")
  const widths = option("appearance-widths", "1280,390").split(",").map(Number)
  const themes = option("appearance-themes", "light,dark").split(",")
  const wallpapers = option("appearance-wallpapers", "none,bright").split(",")
  const height = Number(option("appearance-height", "900"))
  assert(locales.every(locale => LOCALES.includes(locale)), "Unknown appearance locale")
  assert(widths.every(width => Number.isInteger(width) && width >= 320), "Appearance widths must be integer pixels at least 320")
  assert(themes.every(theme => ["light", "dark"].includes(theme)), "Appearance themes must be light or dark")
  assert(wallpapers.every(wallpaper => ["none", "bright", "dark"].includes(wallpaper)), "Unknown appearance wallpaper fixture")
  assert(Number.isInteger(height) && height >= 320, "Appearance height must be integer pixels at least 320")
  const screenshots = []
  const checks = []
  async function check(name, operation) {
    try {
      const detail = await operation()
      checks.push({ name, passed: true, detail })
      console.log(`PASS ${name}`)
    } catch (error) {
      checks.push({ name, passed: false, error: error.stack })
      console.log(`FAIL ${name}: ${error.message}`)
    }
  }
  async function capture(loaded, id) {
    const layout = await inspectLayout(loaded.frame)
    const measurements = await geometry(loaded.frame)
    layout.issues.push(...measurements.issues)
    const diagnostics = await loaded.page.evaluate(() => window.qaDiagnostics.filter(message => message.type === "neko-hosted-surface-error"))
    const path = join(output, `${id}.png`)
    await loaded.page.screenshot({ path, animations: "disabled" })
    screenshots.push({ id, path, layout, geometry: measurements, errors: [...loaded.errors], diagnostics })
    assert(layout.documentOverflow <= 2 && layout.contentOverflow <= 2, `${id}: horizontal overflow`)
    assert.deepEqual(layout.issues, [], `${id}: clipped or overlapping copy`)
    assert.deepEqual(loaded.errors, [], `${id}: browser runtime errors`)
    assert.deepEqual(diagnostics, [], `${id}: hosted runtime diagnostics`)
    assert.deepEqual(layout.errorNodes, [], `${id}: visible error nodes`)
    return measurements
  }

  for (const locale of locales) for (const width of widths) for (const theme of themes) for (const wallpaper of wallpapers) {
    const loaded = await loadPage(browser, origin, { locale, wallpaper, tab: "settings" }, width, height, theme)
    const { frame, page } = loaded
    const baseline = await actionSnapshot(page)
    const text = sources.messages[locale]
    const id = `appearance-${locale}-${width}-${theme}-${wallpaper}`
    let collapsed
    try {
      await check(`${id}-default-collapsed-and-outside-controls`, async () => {
        assert.equal(await frame.locator(DISCLOSURE).evaluate(element => element.tagName), "DETAILS")
        assert.equal(await frame.locator(DISCLOSURE).getAttribute("open"), null, "Adjustment parameters must start collapsed")
        assert.equal(await frame.locator(SUMMARY).innerText(), text["panel.appearance.adjustSection"])
        assert(!await frame.locator(BODY).isVisible(), "Collapsed adjustment controls must not be displayed")
        assert.equal(await frame.locator(RANGES).count(), 8, "Retain all eight original sliders")
        assert.equal(await frame.locator(`${BODY} .tm-pos-cell`).count(), 9, "Retain the nine position controls")
        assert.equal(await frame.locator(`${BODY} .neko-segmented-button`).count(), 4, "Retain all four fill modes")
        for (const selector of [".tm-gallery", "input[type=file]", ".tm-theme-colors", ".tm-appearance-save"]) {
          assert.equal(await frame.locator(`${DISCLOSURE} ${selector}`).count(), 0, `${selector}: this control must stay outside adjustments`)
        }
        assert(await frame.locator(".tm-gallery").isVisible() && await frame.locator(".tm-theme-colors").isVisible())
        assert(await frame.locator(".tm-appearance-save").isVisible(), "Saved/dirty feedback and revert must stay visible")
        assert.deepEqual(await rangeValues(frame), RANGE_KEYS.map(key => baseline.appearance[key]))
        await assertReachable(frame, SUMMARY)
        collapsed = await capture(loaded, `${id}-collapsed`)
        assert(collapsed.summary.height >= 44, "The disclosure header must preserve its comfortable pointer target")
        await frame.locator(SUMMARY).focus()
        await frame.locator(SUMMARY).press("Tab")
        assert(!await frame.evaluate(selector => !!document.activeElement?.closest(selector), BODY), "Hidden adjustment controls must not receive keyboard focus")
        await assertNoBusinessMutation(page, baseline)
      })
      await check(`${id}-mouse-keyboard-expanded-layout-and-height`, async () => {
        await expandAppearance(frame)
        const enabled = wallpaper !== "none"
        for (let index = 0; index < 8; index += 1) {
          assert(await frame.locator(RANGES).nth(index).isVisible(), `Slider ${index + 1} must be readable when expanded`)
          assert.equal(await frame.locator(RANGES).nth(index).isEnabled(), enabled, "Preserve no-wallpaper slider disabled behavior")
        }
        if (!enabled) assert((await frame.locator(BODY).innerText()).includes(text["panel.appearance.noBgHint"]))
        await assertReachable(frame, SUMMARY)
        const expanded = await capture(loaded, `${id}-expanded`)
        assert(collapsed, "The initial collapsed geometry is required")
        const savedHeight = expanded.root.height - collapsed.root.height
        assert(savedHeight > 250, "Collapsing adjustments must remove a meaningful amount of page height")
        assert(Math.abs(expanded.summary.height - collapsed.summary.height) <= 1, "Expansion must not resize the summary target")
        await frame.locator(SUMMARY).focus()
        await frame.locator(SUMMARY).press("Space")
        assert.equal(await frame.locator(DISCLOSURE).getAttribute("open"), null, "Space must collapse adjustments")
        await frame.locator(SUMMARY).press("Enter")
        assert.equal(await frame.locator(DISCLOSURE).getAttribute("open"), "", "Enter must expand adjustments")
        const focus = await frame.locator(SUMMARY).evaluate(element => ({ focused: document.activeElement === element, outline: getComputedStyle(element).outlineStyle, width: Number.parseFloat(getComputedStyle(element).outlineWidth) }))
        assert(focus.focused && focus.outline !== "none" && focus.width >= 2, "Keyboard disclosure focus must stay visible")
        await frame.locator(SUMMARY).press("Space")
        assert.equal(await frame.locator(DISCLOSURE).getAttribute("open"), null)
        await assertNoBusinessMutation(page, baseline)
        return { savedHeight }
      })
      await check(`${id}-draft-refresh-collapsed-revert-and-save`, async () => {
        const diagnosticPath = join(output, `${id}-unreachable-last-slider.png`)
        const expected = await editVisibleParameters(loaded, baseline, diagnosticPath)
        assert.equal(await frame.locator(".tm-appearance-save .tm-save-hint").innerText(), text["panel.appearance.unsavedHint"])
        assert.deepEqual((await actionSnapshot(page)).appearance, baseline.appearance, "Preview changes must not persist before save")
        if (locale === locales[0]) {
          const refreshes = await page.evaluate(() => window.qaRefreshes)
          await page.waitForFunction(previous => window.qaRefreshes > previous, refreshes, { timeout: 6500 })
          assert.equal(await frame.locator(DISCLOSURE).getAttribute("open"), "", "The real five-second hosted refresh must preserve an expanded disclosure")
          assert.deepEqual(await rangeValues(frame), RANGE_KEYS.map(key => expected[key]), "The real hosted refresh lost unsaved adjustment parameters")
          assert.equal(await frame.locator(`${BODY} .neko-segmented-button.is-active`).innerText(), text["panel.appearance.fill.contain"])
          assert.equal(await frame.locator(`${BODY} .tm-pos-cell[aria-pressed="true"]`).getAttribute("aria-label"), text["panel.appearance.pos.right-bottom"])
          await assertReachable(frame, SUMMARY)
          await capture(loaded, `${id}-draft-after-refresh`)
        }
        await frame.locator(SUMMARY).click()
        assert.equal(await frame.locator(DISCLOSURE).getAttribute("open"), null)
        await assertReachable(frame, ".tm-appearance-save button")
        await frame.locator(".tm-appearance-save button").click()
        assert.equal(await frame.locator(DISCLOSURE).getAttribute("open"), null, "Revert must not reopen adjustment parameters")
        assert.deepEqual(await rangeValues(frame), RANGE_KEYS.map(key => baseline.appearance[key]))
        assert.equal(await frame.locator(".tm-appearance-save .tm-save-hint").innerText(), text["panel.appearance.savedHint"])
        assert(await frame.locator(".tm-appearance-save button").isDisabled(), "Successful revert must clear the dirty state")
        assert.deepEqual((await actionSnapshot(page)).appearance, baseline.appearance)
        assert.equal((await callsSince(page, baseline)).filter(message => message.payload.actionId === "set_panel_appearance").length, 0, "Revert must not write appearance")
        await editVisibleParameters(loaded, baseline, diagnosticPath)
        await frame.locator(SUMMARY).click()
        assert.equal(await frame.locator(DISCLOSURE).getAttribute("open"), null)
        const beforeSave = await actionSnapshot(page)
        await frame.locator(".tm-save button").click()
        await page.waitForFunction(value => Object.entries(value).every(([key, expectedValue]) => window.qaFixture.appearance[key] === expectedValue), expected)
        assert.equal(await frame.locator(DISCLOSURE).getAttribute("open"), null, "Saving while collapsed must not reopen adjustments")
        assert.equal(await frame.locator(".tm-appearance-save .tm-save-hint").innerText(), text["panel.appearance.savedHint"])
        const saves = (await callsSince(page, beforeSave)).filter(message => message.payload.actionId === "set_panel_appearance")
        assert.equal(saves.length, 1, "The unified save must submit appearance exactly once")
        assert.deepEqual(saves[0].payload.args, expected, "The original complete appearance save schema and callbacks must be preserved")
        await assertNoBusinessMutation(page, baseline, true)
      })
    } finally { await loaded.context.close() }
  }
  return { screenshots, checks }
}
