import assert from "node:assert/strict"
import { join } from "node:path"

const LOCALES = ["zh-CN", "zh-TW", "en", "ja", "ko", "ru", "es", "pt"]
const ROOT = ".tm-ci"
const SECTION = {
  purpose: '[data-section="purpose"]',
  scenarios: '[data-section="scenarios"]',
  limits: '[data-section="limits"]',
  requirements: '[data-section="requirements"]',
  demo: '[data-section="demo"]',
}
const TECHNICAL = ".tm-ci-technical"
const SUMMARY = `${TECHNICAL} > summary`
const MODEL_NOTE = {
  tool: "panel.capintro.modelTool",
  direct: "panel.capintro.modelDirect",
  host_http: "panel.capintro.modelHost",
  injection: "panel.capintro.modelInjection",
  none: "panel.capintro.modelLocal",
}

function option(name, fallback) {
  const index = process.argv.indexOf(`--${name}`)
  return index < 0 ? fallback : process.argv[index + 1]
}

function textFor(sources, locale, value) {
  if (typeof value === "string") return value
  return sources.messages[locale]?.[value?.$i18n] || value?.default || ""
}

async function mutationSnapshot(page) {
  return page.evaluate(() => ({
    offset: window.qaMessages.length,
    capabilities: JSON.stringify(window.qaFixture.context.state.capabilities),
    settings: JSON.stringify(window.qaFixture.context.state.settings),
  }))
}

async function assertNoMutations(page, before) {
  const current = await mutationSnapshot(page)
  assert.equal(current.capabilities, before.capabilities, "Introduction controls changed real capabilities")
  assert.equal(current.settings, before.settings, "Introduction controls changed real settings")
  const calls = await page.evaluate(offset => window.qaMessages.slice(offset).filter(message => message.method === "call"), before.offset)
  assert.deepEqual(calls.filter(message => message.payload.actionId !== "get_capability_intro"), [], "Introduction controls must not call plugin mutations")
}

async function introGeometry(frame) {
  return frame.locator(ROOT).evaluate(root => {
    const issues = []
    const bounds = element => {
      const rect = element.getBoundingClientRect()
      return { left: rect.left, right: rect.right, top: rect.top, bottom: rect.bottom, width: rect.width, height: rect.height }
    }
    const rootBounds = bounds(root)
    const sections = [...root.querySelectorAll("[data-section]")].map(element => ({
      name: element.dataset.section,
      bounds: bounds(element),
      borderTop: Number.parseFloat(getComputedStyle(element).borderTopWidth),
      paddingTop: Number.parseFloat(getComputedStyle(element).paddingTop),
    }))
    const visible = element => {
      let opacity = 1
      for (let current = element; current && root.contains(current); current = current.parentElement) {
        const style = getComputedStyle(current)
        if (style.display === "none" || style.visibility === "hidden") return false
        opacity *= Number(style.opacity)
      }
      return opacity > 0.05
    }
    const textBoxes = []
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT)
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
      const element = node.parentElement
      const text = node.textContent.trim()
      if (!text || !visible(element) || element.closest('[aria-hidden="true"]')) continue
      const range = document.createRange()
      range.selectNodeContents(node)
      for (const rect of range.getClientRects()) {
        if (rect.width < 1 || rect.height < 1) continue
        const label = text.slice(0, 100)
        if (rect.left < rootBounds.left - 2 || rect.right > rootBounds.right + 2) {
          issues.push({ kind: "intro-copy-outside", label, left: rect.left, right: rect.right })
        }
        for (let ancestor = element; ancestor && root.contains(ancestor); ancestor = ancestor.parentElement) {
          const style = getComputedStyle(ancestor)
          const rectAncestor = ancestor.getBoundingClientRect()
          const horizontal = ["hidden", "clip"].includes(style.overflowX)
          const vertical = ["hidden", "clip"].includes(style.overflowY)
          if ((horizontal && (rect.left < rectAncestor.left - 2 || rect.right > rectAncestor.right + 2)) ||
              (vertical && (rect.top < rectAncestor.top - 2 || rect.bottom > rectAncestor.bottom + 2))) {
            issues.push({ kind: "intro-clipped-copy", label, className: ancestor.className })
            break
          }
        }
        textBoxes.push({ node, label, rect })
      }
    }
    for (let left = 0; left < textBoxes.length; left += 1) {
      for (let right = left + 1; right < textBoxes.length; right += 1) {
        const a = textBoxes[left]
        const b = textBoxes[right]
        if (a.node === b.node) continue
        const x = Math.min(a.rect.right, b.rect.right) - Math.max(a.rect.left, b.rect.left)
        const y = Math.min(a.rect.bottom, b.rect.bottom) - Math.max(a.rect.top, b.rect.top)
        if (x > 2 && y > 2) issues.push({ kind: "intro-copy-overlap", labels: [a.label, b.label], x, y })
      }
    }
    const details = root.querySelector(".tm-ci-technical")
    const grid = root.querySelector(".tm-ci-grid")
    const mono = [...root.querySelectorAll(".tm-ci-technical code,.tm-ci-technical .tm-ci-mono")].map(element => {
      const style = getComputedStyle(element)
      return {
        text: element.textContent,
        bounds: bounds(element),
        overflowWrap: style.overflowWrap,
        wordBreak: style.wordBreak,
        whiteSpace: style.whiteSpace,
        scrollWidth: element.scrollWidth,
        clientWidth: element.clientWidth,
      }
    })
    return {
      root: rootBounds, sections, textCount: textBoxes.length,
      gridColumns: grid ? getComputedStyle(grid).gridTemplateColumns.split(/\s+/).map(value => Number.parseFloat(value)).filter(value => value > 2) : [],
      technical: details ? bounds(details) : null,
      technicalOpen: !!details?.open, mono, issues,
    }
  })
}

async function assertScrollVisibility(frame, selector, index = 0) {
  const target = frame.locator(selector).nth(index)
  await target.scrollIntoViewIfNeeded()
  const position = await target.evaluate(element => {
    const rect = element.getBoundingClientRect()
    const content = document.querySelector(".tm-content").getBoundingClientRect()
    const x = Math.max(rect.left + 1, Math.min(rect.right - 1, (rect.left + rect.right) / 2))
    const y = Math.max(rect.top + 1, Math.min(rect.bottom - 1, (rect.top + rect.bottom) / 2))
    const hit = document.elementFromPoint(x, y)
    return {
      inside: x >= content.left && x <= content.right && y >= content.top && y <= content.bottom,
      reached: !!hit && (element === hit || element.contains(hit)),
      bounds: { left: rect.left, right: rect.right, top: rect.top, bottom: rect.bottom },
      content: { left: content.left, right: content.right, top: content.top, bottom: content.bottom },
    }
  })
  assert(position.inside, `${selector}: target is outside the readable content viewport`)
  assert(position.reached, `${selector}: target is clipped or covered after scrolling`)
  return position
}

function assertSectionOrder(geometry) {
  const section = name => geometry.sections.find(value => value.name === name)?.bounds
  for (const name of Object.keys(SECTION)) assert(section(name), `${name}: introduction section is missing`)
  for (const name of ["scenarios", "limits", "requirements", "demo"]) {
    const boundary = geometry.sections.find(value => value.name === name)
    assert(boundary.borderTop >= 1 && boundary.paddingTop >= 8, `${name}: reading sections need visible boundaries and padding`)
  }
  const purpose = section("purpose")
  const actions = section("actions")
  const scenarios = section("scenarios")
  const limits = section("limits")
  const requirements = section("requirements")
  const demo = section("demo")
  if (actions) assert(actions.top >= purpose.bottom + 8, "Mood explanations need clear space after the purpose")
  assert(scenarios.top >= (actions || purpose).bottom + 8, "Scenarios need clear space after the preceding section")
  assert(limits.top >= (actions || purpose).bottom + 8, "Notes need clear space after the preceding section")
  assert(requirements.top >= Math.max(scenarios.bottom, limits.bottom) + 8, "Usage conditions need clear space after scenarios and notes")
  assert(demo.top >= requirements.bottom + 8, "The demonstration must follow the visible usage conditions")
  assert(geometry.technical?.top >= demo.bottom + 8, "Technical details need clear space after the demonstration")
  assert([1, 2].includes(geometry.gridColumns.length), "Reading sections need a single-column or two-column grid")
  if (geometry.gridColumns.length === 1) {
    assert(limits.top >= scenarios.bottom + 8, "Narrow introductions must place notes below scenarios")
  } else {
    assert(Math.abs(scenarios.top - limits.top) <= 2, "Wide introductions should align the two reading columns")
    assert(limits.left >= scenarios.right + 8, "Wide scenarios and notes need a readable column gutter")
  }
}

async function assertDemoControls(frame, flowLength, capability) {
  const demo = frame.locator(".tm-demo")
  for (const selector of [".tm-demo-mode-before", ".tm-demo-mode-after", ".tm-demo-prev", ".tm-demo-play", ".tm-demo-next", ".tm-demo-replay"]) {
    assert.equal(await frame.locator(selector).count(), 1, `${selector}: demonstration control disappeared`)
  }
  assert.equal(await frame.locator(".tm-ci-node").count(), flowLength, "Introduction must retain every real flow node")
  await frame.locator(".tm-demo-mode-before").click()
  assert.equal(await demo.getAttribute("data-mode"), "before")
  await frame.locator(".tm-demo-mode-after").click()
  assert.equal(await demo.getAttribute("data-mode"), "after")
  const reduced = await demo.getAttribute("data-reduced") === "true"
  await frame.locator(".tm-demo-replay").click()
  const replayStep = reduced ? flowLength - 1 : 0
  assert.equal(await demo.getAttribute("data-step"), String(replayStep))
  if (!reduced) assert(await frame.locator(".tm-demo-prev").isDisabled(), "Previous must remain disabled on the first flow node")
  if (flowLength > 1 && !reduced) {
    await frame.locator(".tm-demo-next").click()
    assert.equal(await demo.getAttribute("data-step"), "1")
    await frame.locator(".tm-demo-prev").click()
    assert.equal(await demo.getAttribute("data-step"), "0")
  }
  if (reduced) {
    for (let step = flowLength - 2; step >= 0; step -= 1) await frame.locator(".tm-demo-prev").click()
    assert.equal(await demo.getAttribute("data-step"), "0")
    assert(await frame.locator(".tm-demo-prev").isDisabled(), "Previous must be disabled at the first flow node")
    for (let step = 1; step < flowLength; step += 1) await frame.locator(".tm-demo-next").click()
  } else {
    for (let step = 1; step < flowLength; step += 1) await frame.locator(".tm-demo-next").click()
  }
  assert(await frame.locator(".tm-demo-next").isDisabled(), "Next must remain disabled on the final flow node")
  if (capability === "journal") {
    await frame.locator(".tm-demo-journal-later").click()
    assert.equal(await demo.getAttribute("data-choice"), "later")
    await frame.locator(".tm-demo-journal-write").click()
    assert.equal(await demo.getAttribute("data-choice"), "write")
  }
  if (capability === "activity_sense") {
    const privacy = frame.locator(".tm-demo-privacy")
    if (await privacy.evaluate(element => element.tagName === "INPUT")) await privacy.check()
    else await privacy.click()
    assert.equal(await demo.getAttribute("data-private"), "true")
    if (await privacy.evaluate(element => element.tagName === "INPUT")) await privacy.uncheck()
    else await privacy.click()
    assert.equal(await demo.getAttribute("data-private"), "false")
  }
}

export async function introChecks(browser, sources, origin, output, helpers) {
  const { loadPage, inspectLayout } = helpers
  const screenshots = []
  const checks = []
  const capabilities = Object.keys(sources.intros)
  const locales = option("intro-locales", LOCALES.join(",")).split(",")
  const widths = option("intro-widths", "1280,390").split(",").map(Number)
  const height = Number(option("intro-height", "900"))
  const themes = option("intro-themes", "light,dark").split(",")
  const selected = option("intro-capabilities", capabilities.join(",")).split(",")
  assert.equal(capabilities.length, 10, "Update the introduction matrix when the managed capability count changes")
  assert(locales.every(locale => LOCALES.includes(locale)), "Unknown introduction locale")
  assert(widths.every(width => Number.isInteger(width) && width >= 320), "Introduction widths must be integer pixels at least 320")
  assert(Number.isInteger(height) && height >= 320, "Introduction height must be integer pixels at least 320")
  assert(themes.every(theme => ["light", "dark"].includes(theme)), "Introduction themes must be light or dark")
  assert(selected.every(capability => capabilities.includes(capability)), "Unknown introduction capability")
  assert.equal(typeof loadPage, "function", "introChecks needs helpers.loadPage")
  assert.equal(typeof inspectLayout, "function", "introChecks needs helpers.inspectLayout")

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

  async function capture(loaded, id, save = true) {
    const layout = await inspectLayout(loaded.frame)
    const geometry = await introGeometry(loaded.frame)
    layout.issues.push(...geometry.issues)
    const diagnostics = await loaded.page.evaluate(() => window.qaDiagnostics.filter(message => message.type === "neko-hosted-surface-error"))
    let path = null
    if (save) {
      path = join(output, `${id}.png`)
      await loaded.page.screenshot({ path, animations: "disabled" })
      screenshots.push({ id, path, layout, geometry, errors: [...loaded.errors], diagnostics })
    }
    assert(layout.documentOverflow <= 2, `${id}: document overflow ${layout.documentOverflow}`)
    assert(layout.contentOverflow <= 2, `${id}: content overflow ${layout.contentOverflow}`)
    assert.deepEqual(layout.issues, [], `${id}: copy clipping or overlap`)
    assert.deepEqual(loaded.errors, [], `${id}: runtime errors`)
    assert.deepEqual(diagnostics, [], `${id}: hosted runtime diagnostics`)
    assert.deepEqual(layout.errorNodes, [], `${id}: visible error nodes`)
    return geometry
  }

  for (const locale of locales) for (const width of widths) for (const theme of themes) {
    const loaded = await loadPage(browser, origin, { locale, wallpaper: "none", tab: "features" }, width, height, theme, "reduce")
    const baseline = await mutationSnapshot(loaded.page)
    try {
      for (const [index, capability] of capabilities.entries()) {
        if (!selected.includes(capability)) continue
        const id = `intro-${locale}-${width}-${theme}-${capability}`
        const payload = sources.intros[capability]
        await check(`${id}-real-copy-and-structure`, async () => {
          await loaded.frame.locator(".tm-ci-open").nth(index).click()
          await loaded.frame.waitForSelector(".tm-ci-purpose")
          assert.equal(await loaded.frame.locator(".tm-ci-title").innerText(), sources.messages[locale][`panel.features.cap.${capability}.label`])
          assert.equal(await loaded.frame.locator(".tm-ci-head .neko-badge").count(), 1, "The compact introduction header must not repeat technical model badges")
          assert.equal(await loaded.frame.locator(".tm-ci-head .neko-badge").innerText(), sources.messages[locale]["panel.capintro.stateOn"])
          assert.equal(await loaded.frame.locator(".tm-ci-purpose").innerText(), textFor(sources, locale, payload.purpose))
          for (const section of ["purpose", "scenarios", "limits", "requirements"]) {
            assert.equal(await loaded.frame.locator(`${SECTION[section]} .tm-ci-h`).innerText(), sources.messages[locale][`panel.capintro.${section}`])
          }
          assert.deepEqual(await loaded.frame.locator(`${SECTION.scenarios} li`).allTextContents(), payload.scenarios.map(value => textFor(sources, locale, value)))
          assert.deepEqual(await loaded.frame.locator(`${SECTION.limits} li`).allTextContents(), payload.limits.map(value => textFor(sources, locale, value)))
          const actions = payload.actions || []
          assert.equal(await loaded.frame.locator(".tm-ci-actions").count(), actions.length ? 1 : 0)
          assert.equal(await loaded.frame.locator(".tm-ci-actions [data-action]").count(), actions.length)
          for (const action of actions) {
            const row = loaded.frame.locator(`.tm-ci-actions [data-action="${action.id}"]`)
            assert.equal(await row.locator("dt").innerText(), textFor(sources, locale, action.label))
            assert.equal(await row.locator("dd").innerText(), textFor(sources, locale, action.description))
            await assertScrollVisibility(loaded.frame, `.tm-ci-actions [data-action="${action.id}"] dd`)
          }
          if (actions.length) {
            assert.equal(await loaded.frame.locator(".tm-ci-actions .tm-ci-h").innerText(), sources.messages[locale]["panel.capintro.mood_engine.actionsTitle"])
            await loaded.page.screenshot({ path: join(output, `${id}-actions.png`), animations: "disabled" })
          }
          assert.deepEqual(await loaded.frame.locator(".tm-ci-node-label").allTextContents(), payload.flow.map(node => textFor(sources, locale, node.label)))
          assert.equal(await loaded.frame.locator(TECHNICAL).getAttribute("open"), null, "Advanced technical information must start collapsed")
          assert.equal(await loaded.frame.locator(`${SECTION.requirements} details`).count(), 0, "Usage conditions must not be hidden in a disclosure")
          const body = await loaded.frame.locator(ROOT).innerText()
          assert(!/\{(?:deps|n|total|d|capability)\}/.test(body), "Introduction has an unresolved translation placeholder")
          await loaded.frame.locator(".tm-content").evaluate(element => { element.scrollTop = 0 })
          const geometry = await capture(loaded, `${id}-top`)
          assertSectionOrder(geometry)
        })
        if (!await loaded.frame.locator(ROOT).count()) continue
        await check(`${id}-visible-conditions-and-keyboard-details`, async () => {
          const conditions = await loaded.frame.locator(SECTION.requirements).innerText()
          const master = sources.messages[locale]["panel.capintro.masterRequired"]
          assert(master, "Every locale needs the visible current-character master-switch condition")
          assert(conditions.includes(master), "Usage conditions must always mention the current-character master switch")
          for (const dependency of payload.deps) {
            assert(conditions.includes(sources.messages[locale][`panel.features.cap.${dependency}.label`]), `Usage condition missing dependency ${dependency}`)
          }
          if (!payload.deps.length) {
            assert(conditions.includes(sources.messages[locale]["panel.capintro.depsNone"]), "Usage conditions must state that no other capability is needed")
          }
          assert(conditions.includes(sources.messages[locale][MODEL_NOTE[payload.llm] || MODEL_NOTE.none]), "Usage conditions must explain how the feature interacts with models")
          await assertScrollVisibility(loaded.frame, `${SECTION.requirements} .tm-ci-h`)
          await capture(loaded, `${id}-conditions`, false)
          await assertScrollVisibility(loaded.frame, SUMMARY)
          await loaded.frame.locator(SUMMARY).focus()
          await loaded.frame.locator(SUMMARY).press("Enter")
          assert.equal(await loaded.frame.locator(TECHNICAL).getAttribute("open"), "", "Enter must expand the native technical disclosure")
          const technical = await loaded.frame.locator(TECHNICAL).innerText()
          for (const value of [...payload.config_keys, ...payload.tools]) assert(technical.includes(value), `Advanced technical information missing ${value}`)
          assert(technical.includes(sources.messages[locale]["panel.capintro.config"]), "Technical configuration identifiers need a plain-language heading")
          assert(technical.includes(sources.messages[locale]["panel.capintro.tools"]), "Technical tool names need a plain-language heading")
          const technicalCodes = loaded.frame.locator(`${TECHNICAL} code,${TECHNICAL} .tm-ci-mono`)
          const codeCount = await technicalCodes.count()
          if (codeCount) {
            await assertScrollVisibility(loaded.frame, `${TECHNICAL} code,${TECHNICAL} .tm-ci-mono`)
            await assertScrollVisibility(loaded.frame, `${TECHNICAL} code,${TECHNICAL} .tm-ci-mono`, codeCount - 1)
          }
          const representative = ["zh-CN", "en", "ru", "ja"].includes(locale) && ["whisper", "mood_engine", "fragments", "review"].includes(capability)
          const geometry = await capture(loaded, `${id}-technical`, representative)
          for (const code of geometry.mono) {
            assert(code.bounds.right <= geometry.root.right + 2, `${code.text}: technical identifier exceeds the readable page width`)
            assert(code.whiteSpace !== "nowrap", `${code.text}: technical identifiers must be allowed to wrap`)
            assert(code.overflowWrap === "anywhere" || code.overflowWrap === "break-word" || code.wordBreak === "break-all", `${code.text}: technical identifiers need safe narrow-window wrapping`)
          }
          await loaded.frame.locator(SUMMARY).focus()
          await loaded.frame.locator(SUMMARY).press("Space")
          assert.equal(await loaded.frame.locator(TECHNICAL).getAttribute("open"), null, "Space must collapse the native technical disclosure")
        })
        await check(`${id}-demo-controls-and-no-mutations`, async () => {
          await loaded.frame.locator(".tm-demo").scrollIntoViewIfNeeded()
          await assertDemoControls(loaded.frame, payload.flow.length, capability)
          await capture(loaded, `${id}-demo-controls`, false)
          await assertNoMutations(loaded.page, baseline)
        })
        await loaded.frame.locator(".tm-ci-back").click()
      }
      await check(`intro-${locale}-${width}-${theme}-no-plugin-mutations`, () => assertNoMutations(loaded.page, baseline))
    } finally { await loaded.context.close() }
  }
  for (const width of widths) for (const theme of themes) {
    const locale = locales.includes("zh-CN") ? "zh-CN" : locales[0]
    const loaded = await loadPage(browser, origin, { locale, wallpaper: "none", tab: "features" }, width, height, theme, "reduce")
    const baseline = await mutationSnapshot(loaded.page)
    const id = `intro-lifecycle-${locale}-${width}-${theme}`
    try {
      await check(`${id}-details-survive-real-refresh-and-reset-on-navigation`, async () => {
        await loaded.frame.locator(".tm-ci-open").first().click()
        await loaded.frame.waitForSelector(".tm-ci-purpose")
        await loaded.frame.locator(SUMMARY).focus()
        await loaded.frame.locator(SUMMARY).press("Enter")
        assert.equal(await loaded.frame.locator(TECHNICAL).getAttribute("open"), "")
        const refreshes = await loaded.page.evaluate(() => window.qaRefreshes)
        await loaded.page.waitForFunction(previous => window.qaRefreshes > previous, refreshes, { timeout: 6500 })
        assert.equal(await loaded.frame.locator(TECHNICAL).getAttribute("open"), "", "The real five-second hosted refresh must preserve an opened disclosure")
        for (const value of sources.intros[capabilities[0]].config_keys) {
          assert((await loaded.frame.locator(TECHNICAL).innerText()).includes(value), "Expanded technical text must remain readable after refresh")
        }
        await assertScrollVisibility(loaded.frame, SUMMARY)
        await capture(loaded, `${id}-expanded-after-refresh`)
        await loaded.frame.locator(".tm-ci-back").click()
        await loaded.frame.locator(".tm-ci-open").nth(capabilities.indexOf("mood_engine")).click()
        await loaded.frame.waitForSelector(".tm-ci-purpose")
        assert.equal(await loaded.frame.locator(TECHNICAL).getAttribute("open"), null, "Another introduction must start with collapsed technical details")
        await loaded.frame.locator(".tm-ci-back").click()
        await loaded.frame.locator(".tm-ci-open").first().click()
        await loaded.frame.waitForSelector(".tm-ci-purpose")
        assert.equal(await loaded.frame.locator(TECHNICAL).getAttribute("open"), null, "Returning to the same introduction must create a fresh collapsed disclosure")
        await assertNoMutations(loaded.page, baseline)
      })
    } finally { await loaded.context.close() }
  }
  return { screenshots, checks }
}
