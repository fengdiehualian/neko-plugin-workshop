import assert from "node:assert/strict"
import { join } from "node:path"

const LOCALES = ["zh-CN", "zh-TW", "en", "ja", "ko", "ru", "es", "pt"]
const MODE = { before: ".tm-demo-mode-before", after: ".tm-demo-mode-after" }
const ROOT = ".tm-demo"
const STAGE = ".tm-demo-stage"

function option(name, fallback) {
  const index = process.argv.indexOf(`--${name}`)
  return index < 0 ? fallback : process.argv[index + 1]
}

function textFor(sources, locale, value) {
  if (typeof value === "string") return value
  return sources.messages[locale]?.[value?.$i18n] || value?.default || ""
}

async function demoState(frame) {
  const state = await frame.locator(ROOT).evaluate(element => {
    const stage = element.querySelector(".tm-demo-stage")
    const visuals = [...stage.querySelectorAll("*")].map(node => {
      const style = getComputedStyle(node)
      return [style.transform, style.opacity, style.width, style.height].join(":")
    }).join("|")
    return {
      mode: element.dataset.mode,
      step: Number(element.dataset.step),
      progress: Number(element.dataset.progress),
      playing: element.dataset.playing,
      reduced: element.dataset.reduced,
      choice: element.dataset.choice || "",
      private: element.dataset.private || "",
      visuals,
      callbacks: window.__demoQA?.callbacks || 0,
    }
  })
  assert(Number.isInteger(state.step) && state.step >= 0, "Demo must expose its real flow node index")
  assert(Number.isFinite(state.progress) && state.progress >= 0 && state.progress <= 1, "Demo must expose finite animation progress between zero and one")
  return state
}

async function demoGeometry(frame) {
  return frame.locator(ROOT).evaluate(root => {
    const problems = []
    const rootRect = root.getBoundingClientRect()
    const stage = root.querySelector(".tm-demo-stage")
    const stageRect = stage.getBoundingClientRect()
    const visible = element => {
      let opacity = 1
      for (let current = element; current && current !== root.parentElement; current = current.parentElement) {
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
        if (rect.left < rootRect.left - 2 || rect.right > rootRect.right + 2) {
          problems.push({ kind: "demo-copy-outside", label, left: rect.left, right: rect.right })
        }
        for (let ancestor = element; ancestor && root.contains(ancestor); ancestor = ancestor.parentElement) {
          const style = getComputedStyle(ancestor)
          const bounds = ancestor.getBoundingClientRect()
          const horizontal = ["hidden", "clip"].includes(style.overflowX)
          const vertical = ["hidden", "clip"].includes(style.overflowY)
          if (
            (horizontal && (rect.left < bounds.left - 2 || rect.right > bounds.right + 2)) ||
            (vertical && (rect.top < bounds.top - 2 || rect.bottom > bounds.bottom + 2))
          ) {
            problems.push({ kind: "demo-clipped-copy", label, className: ancestor.className })
            break
          }
        }
        if (stage.contains(element)) textBoxes.push({ node, label, rect })
      }
    }
    for (let left = 0; left < textBoxes.length; left += 1) {
      for (let right = left + 1; right < textBoxes.length; right += 1) {
        const a = textBoxes[left]
        const b = textBoxes[right]
        if (a.node === b.node) continue
        const x = Math.min(a.rect.right, b.rect.right) - Math.max(a.rect.left, b.rect.left)
        const y = Math.min(a.rect.bottom, b.rect.bottom) - Math.max(a.rect.top, b.rect.top)
        if (x > 2 && y > 2) problems.push({ kind: "demo-copy-overlap", labels: [a.label, b.label], x, y })
      }
    }
    const buttons = [...root.querySelectorAll("button,input")].filter(visible)
    for (let left = 0; left < buttons.length; left += 1) {
      for (let right = left + 1; right < buttons.length; right += 1) {
        const a = buttons[left].getBoundingClientRect()
        const b = buttons[right].getBoundingClientRect()
        if (Math.min(a.right, b.right) - Math.max(a.left, b.left) > 2 &&
            Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top) > 2) {
          problems.push({ kind: "demo-controls-overlap", classes: [buttons[left].className, buttons[right].className] })
        }
      }
    }
    if (stageRect.width < 120 || stageRect.height < 120) problems.push({ kind: "demo-stage-collapsed", width: stageRect.width, height: stageRect.height })
    return { issues: problems, stage: { width: stageRect.width, height: stageRect.height }, textCount: textBoxes.length }
  })
}

async function assertDemoStyles(frame) {
  const styles = await frame.locator(ROOT).evaluate(root => {
    const stage = getComputedStyle(root.querySelector(".tm-demo-stage"))
    const icon = root.querySelector(".tm-demo-icon")
    const iconStyle = icon ? getComputedStyle(icon) : null
    return {
      display: stage.display,
      border: Number.parseFloat(stage.borderTopWidth),
      minHeight: Number.parseFloat(stage.minHeight),
      container: getComputedStyle(root).containerName,
      mask: iconStyle?.maskImage || iconStyle?.webkitMaskImage || "none",
    }
  })
  assert.equal(styles.display, "flex", "Capability demo stylesheet is not active")
  assert(styles.border >= 1 && styles.minHeight >= 318, "Capability demo stage styling is missing")
  assert.equal(styles.container, "tm-demo", "Capability demo responsive container is missing")
  assert.notEqual(styles.mask, "none", "Capability demo icon masks did not render")
}

async function modeSnapshot(frame) {
  return frame.locator(ROOT).evaluate(root => {
    const canvas = document.createElement("canvas")
    canvas.width = canvas.height = 1
    const context = canvas.getContext("2d", { willReadFrequently: true })
    function rgba(value) {
      context.clearRect(0, 0, 1, 1)
      context.fillStyle = value
      context.fillRect(0, 0, 1, 1)
      const [r, g, b, alpha] = context.getImageData(0, 0, 1, 1).data
      return { r, g, b, alpha: alpha / 255 }
    }
    const buttons = [...root.querySelectorAll(".tm-demo-modes button")].map(button => {
      const style = getComputedStyle(button)
      const bounds = button.getBoundingClientRect()
      return {
        mode: button.classList.contains("tm-demo-mode-before") ? "before" : "after",
        pressed: button.getAttribute("aria-pressed"),
        background: rgba(style.backgroundColor),
        foreground: rgba(style.color),
        backgroundImage: style.backgroundImage,
        opacity: Number(style.opacity),
        outline: { style: style.outlineStyle, width: Number.parseFloat(style.outlineWidth) },
        focused: document.activeElement === button,
        bounds: { x: bounds.x, y: bounds.y, width: bounds.width, height: bounds.height },
      }
    })
    return { mode: root.dataset.mode, buttons }
  })
}

function blueFill(color) {
  return color.alpha >= 0.99 && color.b >= 150 && color.b > color.r + 40 && color.b > color.g + 10
}

function colorContrast(first, second) {
  function luminance(color) {
    const linear = [color.r, color.g, color.b].map(value => {
      const channel = value / 255
      return channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4
    })
    return linear.reduce((sum, value, index) => sum + value * [0.2126, 0.7152, 0.0722][index], 0)
  }
  const left = luminance(first)
  const right = luminance(second)
  return (Math.max(left, right) + 0.05) / (Math.min(left, right) + 0.05)
}

function colorLuminance(color) {
  const channels = [color.r, color.g, color.b].map(value => {
    const channel = value / 255
    return channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4
  })
  return channels.reduce((sum, value, index) => sum + value * [0.2126, 0.7152, 0.0722][index], 0)
}

async function assertModeSelection(frame, mode) {
  const state = await modeSnapshot(frame)
  assert.equal(state.mode, mode, "Demo and comparison controls disagree about the current mode")
  assert.equal(state.buttons.length, 2, "Comparison must have exactly two mode buttons")
  assert.equal(state.buttons.filter(button => button.pressed === "true").length, 1, "Exactly one comparison button must expose aria-pressed=\"true\"")
  for (const button of state.buttons) {
    const selected = button.mode === mode
    assert.equal(button.pressed, selected ? "true" : "false", `${button.mode}: hosted runtime needs explicit ARIA strings`)
    if (selected) {
      assert(blueFill(button.background), `${mode}: selected comparison must have a solid blue background`)
      assert.equal(button.backgroundImage, "none", `${mode}: selected comparison must use a solid fill`)
      assert(button.opacity >= 0.99, `${mode}: selected comparison must not be faded`)
      assert(colorLuminance(button.background) >= 0.6, `${mode}: selected comparison fill should remain a soft light blue`)
      assert(button.foreground.alpha >= 0.99, `${mode}: selected comparison text must be opaque`)
      assert(button.foreground.b >= 80 && button.foreground.b > button.foreground.r + 15, `${mode}: selected comparison text must remain blue-toned`)
      assert(colorLuminance(button.foreground) < colorLuminance(button.background), `${mode}: selected comparison text must be darker than its fill`)
      assert(colorContrast(button.foreground, button.background) >= 4.5, `${mode}: selected comparison text contrast must be at least 4.5`)
    } else {
      assert(!blueFill(button.background), `${button.mode}: an unselected comparison must not look selected`)
    }
  }
  return state
}

function assertModeBounds(first, second, label) {
  for (const previous of first.buttons) {
    const current = second.buttons.find(button => button.mode === previous.mode)
    assert(current, `${label}: comparison button disappeared`)
    for (const dimension of ["x", "y", "width", "height"]) {
      assert(Math.abs(current.bounds[dimension] - previous.bounds[dimension]) <= 0.75, `${label}: ${previous.mode} ${dimension} changed`)
    }
  }
}

async function openDemo(loaded, index) {
  await loaded.frame.locator(".tm-ci-open").nth(index).click()
  await loaded.frame.waitForSelector(ROOT)
  await loaded.frame.locator(ROOT).scrollIntoViewIfNeeded()
}

async function setMode(frame, mode) {
  await frame.locator(MODE[mode]).click()
  await frame.waitForFunction(value => document.querySelector(".tm-demo")?.dataset.mode === value, mode)
}

async function finalStep(frame, count) {
  for (let index = 0; index < count + 1; index += 1) {
    const next = frame.locator(".tm-demo-next")
    if (await next.isDisabled()) break
    await next.click()
  }
  assert.equal((await demoState(frame)).step, Math.max(0, count - 1), "Demo steps must use actual flow node indexes")
}

async function callSnapshot(page) {
  return page.evaluate(() => ({
    calls: window.qaMessages.filter(message => message.method === "call").length,
    capabilities: JSON.stringify(window.qaFixture.context.state.capabilities),
    settings: JSON.stringify(window.qaFixture.context.state.settings),
  }))
}

async function assertNoMutation(page, before) {
  const after = await callSnapshot(page)
  assert.equal(after.capabilities, before.capabilities, "Demonstration changed real capabilities")
  assert.equal(after.settings, before.settings, "Demonstration changed real settings")
  const calls = await page.evaluate(offset => window.qaMessages.filter(message => message.method === "call").slice(offset), before.calls)
  const forbidden = calls.filter(message => message.payload.actionId !== "get_capability_intro")
  assert.deepEqual(forbidden, [], "Demonstration controls must never invoke plugin actions")
}

async function installClockProbe(frame) {
  await frame.evaluate(() => {
    if (window.__demoQA) return
    const original = window.requestAnimationFrame.bind(window)
    window.__demoQA = { callbacks: 0 }
    window.requestAnimationFrame = callback => original(timestamp => {
      window.__demoQA.callbacks += 1
      callback(timestamp)
    })
  })
}

async function assertStable(frame, label) {
  await frame.waitForTimeout(120)
  const first = await demoState(frame)
  await frame.waitForTimeout(250)
  const second = await demoState(frame)
  assert.equal(second.step, first.step, `${label}: step moved`)
  assert.equal(second.progress, first.progress, `${label}: progress moved`)
  assert.equal(second.visuals, first.visuals, `${label}: scene still animates`)
  assert.equal(second.callbacks, first.callbacks, `${label}: animation callbacks still run`)
  return second
}

export async function demoChecks(browser, sources, origin, output, helpers) {
  const { loadPage, inspectLayout } = helpers
  assert.equal(typeof loadPage, "function", "demoChecks needs helpers.loadPage")
  assert.equal(typeof inspectLayout, "function", "demoChecks needs helpers.inspectLayout")
  const screenshots = []
  const checks = []
  const capabilities = Object.keys(sources.intros)
  assert.equal(capabilities.length, 10, "Update the demo matrix when the managed capability count changes")

  async function check(name, operation, metadata = {}) {
    try {
      await operation()
      checks.push({ name, passed: true, ...metadata })
      console.log(`PASS ${name}`)
    } catch (error) {
      checks.push({ name, passed: false, error: error.stack, ...metadata })
      console.log(`FAIL ${name}: ${error.message}`)
    }
  }

  async function capture(loaded, id, stageOnly = false) {
    await loaded.frame.locator(ROOT).scrollIntoViewIfNeeded()
    const layout = await inspectLayout(loaded.frame)
    const geometry = await demoGeometry(loaded.frame)
    layout.issues.push(...geometry.issues)
    const diagnostics = await loaded.page.evaluate(() => window.qaDiagnostics.filter(message => message.type === "neko-hosted-surface-error"))
    const path = join(output, `${id}.png`)
    if (stageOnly) await loaded.frame.locator(STAGE).screenshot({ path, animations: "disabled" })
    else await loaded.page.screenshot({ path, animations: "disabled" })
    screenshots.push({ id, path, layout, geometry, errors: [...loaded.errors], diagnostics, stageOnly })
    assert(layout.documentOverflow <= 2, `${id}: document overflow ${layout.documentOverflow}`)
    assert(layout.contentOverflow <= 2, `${id}: content overflow ${layout.contentOverflow}`)
    assert.deepEqual(layout.issues, [], `${id}: clipping or overlap`)
    assert.deepEqual(loaded.errors, [], `${id}: runtime errors`)
    assert.deepEqual(diagnostics, [], `${id}: hosted runtime diagnostics`)
    assert.deepEqual(layout.errorNodes, [], `${id}: visible error nodes`)
  }

  if (!process.argv.includes("--demo-dynamic-only")) {
    const locales = option("demo-locales", LOCALES.join(",")).split(",")
    const widths = option("demo-widths", "1280,390").split(",").map(Number)
    const themes = option("demo-themes", "light,dark").split(",")
    const selected = option("demo-capabilities", capabilities.join(",")).split(",")
    assert(widths.every(width => [1280, 390].includes(width)), "Demo matrix widths must be 1280 or 390")
    assert(themes.every(theme => ["light", "dark"].includes(theme)), "Demo matrix themes must be light or dark")
    assert(selected.every(capability => capabilities.includes(capability)), "Unknown demo capability filter")
    for (const locale of locales) {
      assert(LOCALES.includes(locale), `Unsupported demo locale ${locale}`)
      const messages = sources.messages[locale]
      for (const width of widths) for (const theme of themes) {
        const loaded = await loadPage(browser, origin, { locale, wallpaper: "none", tab: "features" }, width, 900, theme, "reduce")
        const baseline = await callSnapshot(loaded.page)
        try {
          for (const [index, capability] of capabilities.entries()) {
            if (!selected.includes(capability)) continue
            const id = `demo-${locale}-${width}-${theme}-${capability}`
            await check(`${id}-real-source-and-controls`, async () => {
              await openDemo(loaded, index)
              await assertDemoStyles(loaded.frame)
              assert.equal(await loaded.frame.locator(".tm-ci-node").count(), sources.intros[capability].flow.length)
              const expected = sources.intros[capability].flow.map(node => textFor(sources, locale, node.label))
              assert.deepEqual(await loaded.frame.locator(".tm-ci-node-label").allTextContents(), expected)
              for (const [selector, key] of Object.entries({ ".tm-demo-mode-before": "before", ".tm-demo-mode-after": "after" })) {
                assert.equal((await loaded.frame.locator(selector).innerText()).trim(), messages[`panel.capdemo.${key}`])
              }
              assert.equal(await loaded.frame.locator(".tm-demo-mode-status").count(), 0, "Redundant current-mode status card must stay removed")
              await assertModeSelection(loaded.frame, "after")
              assert.equal((await demoState(loaded.frame)).reduced, "true")
              assert.equal((await demoState(loaded.frame)).playing, "false")
              if (capability === "anniversary") {
                const copy = await loaded.frame.locator(STAGE).innerText()
                assert(!/(^|\D)100(\D|$)/.test(copy), "Anniversary example must not show a non-reminder badge milestone")
              }
            })
            if (!await loaded.frame.locator(ROOT).count()) continue
            for (const mode of ["before", "after"]) {
              await check(`${id}-${mode}`, async () => {
                await setMode(loaded.frame, mode)
                await assertModeSelection(loaded.frame, mode)
                await finalStep(loaded.frame, sources.intros[capability].flow.length)
                assert.equal((await demoState(loaded.frame)).playing, "false", "Reduced-motion snapshot must not autoplay")
                if (capability === "journal" && mode === "before") {
                  assert((await loaded.frame.locator(".tm-demo-caption").innerText()).includes(messages["panel.capdemo.journal.before"]), "Turning off invitations must preserve existing pages")
                }
                await capture(loaded, `${id}-${mode}`)
                if (locale === "zh-CN" && ((width === 1280 && theme === "light") || (width === 390 && theme === "dark"))) {
                  await capture(loaded, `${id}-${mode}-stage`, true)
                }
                await assertNoMutation(loaded.page, baseline)
              })
            }
            await loaded.frame.locator(".tm-ci-back").click()
          }
          await check(`demo-${locale}-${width}-${theme}-no-plugin-mutations`, () => assertNoMutation(loaded.page, baseline))
        } finally { await loaded.context.close() }
      }
    }
  }

  if (!process.argv.includes("--demo-dynamic-only")) {
    for (const width of [1280, 390]) for (const theme of ["light", "dark"]) {
      const loaded = await loadPage(browser, origin, { locale: "zh-CN", wallpaper: "none", tab: "features" }, width, 900, theme, "reduce")
      const baseline = await callSnapshot(loaded.page)
      const id = `demo-mode-selection-${width}-${theme}`
      try {
        await check(`${id}-hover-focus-and-stable-bounds`, async () => {
          await openDemo(loaded, 0)
          const initial = await assertModeSelection(loaded.frame, "after")
          for (const mode of ["before", "after"]) {
            await setMode(loaded.frame, mode)
            const resting = await assertModeSelection(loaded.frame, mode)
            assertModeBounds(initial, resting, `${mode} mode switch`)
            await loaded.frame.locator(MODE[mode]).hover()
            const hovered = await assertModeSelection(loaded.frame, mode)
            assertModeBounds(resting, hovered, `${mode} selected hover`)
            const other = mode === "before" ? "after" : "before"
            await loaded.frame.locator(MODE[other]).hover()
            assertModeBounds(resting, await assertModeSelection(loaded.frame, mode), `${mode} unselected hover`)
            await loaded.page.mouse.move(0, 0)
            await loaded.frame.locator(MODE[mode]).focus()
            await loaded.page.keyboard.press("Tab")
            await loaded.page.keyboard.press("Shift+Tab")
            const focused = await assertModeSelection(loaded.frame, mode)
            const selected = focused.buttons.find(button => button.mode === mode)
            assert(selected.focused, `${mode}: keyboard focus did not return to the selected comparison`)
            assert(selected.outline.style !== "none" && selected.outline.width >= 2, `${mode}: comparison keyboard focus must stay visible`)
            assertModeBounds(resting, focused, `${mode} keyboard focus`)
          }
          await assertNoMutation(loaded.page, baseline)
          await capture(loaded, id)
        })
      } finally { await loaded.context.close() }
    }
  }

  if (!process.argv.includes("--demo-static-only")) {
    const loaded = await loadPage(browser, origin, { locale: "zh-CN", wallpaper: "none", tab: "features" }, 1280, 900, "light", "no-preference")
    const baseline = await callSnapshot(loaded.page)
    try {
      await installClockProbe(loaded.frame)
      for (const [index, capability] of capabilities.entries()) {
        await check(`demo-${capability}-open-animated`, async () => {
          await openDemo(loaded, index)
          await assertDemoStyles(loaded.frame)
          await setMode(loaded.frame, "after")
          const state = await demoState(loaded.frame)
          assert.equal(state.reduced, "false", "Motion tests must not inherit reduced-motion snapshots")
          if (state.playing !== "true") await loaded.frame.locator(".tm-demo-play").click()
          await loaded.frame.waitForTimeout(120)
          const first = await demoState(loaded.frame)
          await loaded.frame.waitForTimeout(250)
          const second = await demoState(loaded.frame)
          assert(second.callbacks > first.callbacks, "Animation must advance via the owned RAF clock")
          assert(second.progress !== first.progress || second.step !== first.step, "Animation progress is static")
          assert.notEqual(second.visuals, first.visuals, "Scene has no changing visual state")
        })
        if (!await loaded.frame.locator(ROOT).count()) continue
        await check(`demo-${capability}-pause-resume-replay`, async () => {
          if ((await demoState(loaded.frame)).playing === "true") await loaded.frame.locator(".tm-demo-play").click()
          const paused = await assertStable(loaded.frame, "paused")
          await loaded.frame.locator(".tm-demo-play").click()
          await loaded.frame.waitForTimeout(250)
          const resumed = await demoState(loaded.frame)
          assert.equal(resumed.playing, "true")
          assert(resumed.progress !== paused.progress || resumed.step !== paused.step, "Resume did not advance")
          await loaded.frame.locator(".tm-demo-replay").click()
          const replay = await demoState(loaded.frame)
          assert.equal(replay.step, 0, "Replay did not reset the real flow index")
          assert(replay.progress < 0.2, "Replay did not reset animation progress")
          if (replay.playing === "true") await loaded.frame.locator(".tm-demo-play").click()
          await assertStable(loaded.frame, "replay pause")
        })
        await check(`demo-${capability}-manual-steps-and-independent-modes`, async () => {
          const flow = sources.intros[capability].flow
          await loaded.frame.locator(".tm-demo-replay").click()
          if ((await demoState(loaded.frame)).playing === "true") await loaded.frame.locator(".tm-demo-play").click()
          assert.equal((await demoState(loaded.frame)).step, 0)
          assert(await loaded.frame.locator(".tm-demo-prev").isDisabled(), "Previous should be disabled at the first real node")
          if (flow.length > 1) {
            await loaded.frame.locator(".tm-demo-next").click()
            assert.equal((await demoState(loaded.frame)).step, 1)
            await loaded.frame.locator(".tm-demo-prev").click()
            assert.equal((await demoState(loaded.frame)).step, 0)
          }
          await finalStep(loaded.frame, flow.length)
          assert(await loaded.frame.locator(".tm-demo-next").isDisabled(), "Next should be disabled at the last real node")
          await setMode(loaded.frame, "before")
          assert.equal((await demoState(loaded.frame)).mode, "before")
          await setMode(loaded.frame, "after")
          assert.equal((await demoState(loaded.frame)).mode, "after")
          await assertNoMutation(loaded.page, baseline)
        })
        await check(`demo-${capability}-unmount-cancels-animation`, async () => {
          if ((await demoState(loaded.frame)).playing !== "true") await loaded.frame.locator(".tm-demo-play").click()
          await loaded.frame.waitForTimeout(120)
          await loaded.frame.locator(".tm-ci-back").click()
          await loaded.frame.waitForTimeout(120)
          const first = await loaded.frame.evaluate(() => window.__demoQA.callbacks)
          await loaded.frame.waitForTimeout(250)
          assert.equal(await loaded.frame.evaluate(() => window.__demoQA.callbacks), first, "RAF callbacks continued after unmount")
        })
        if (await loaded.frame.locator(ROOT).count()) await loaded.frame.locator(".tm-ci-back").click()
      }

      await check("demo-journal-invitation-is-not-a-completed-entry", async () => {
        await openDemo(loaded, capabilities.indexOf("journal"))
        await setMode(loaded.frame, "after")
        if ((await demoState(loaded.frame)).playing === "true") await loaded.frame.locator(".tm-demo-play").click()
        await finalStep(loaded.frame, sources.intros.journal.flow.length)
        const messages = sources.messages["zh-CN"]
        let state = await demoState(loaded.frame)
        assert(!state.choice || state.choice === "pending", "An invitation must not choose writing automatically")
        assert(!(await loaded.frame.locator(STAGE).innerText()).includes(messages["panel.capdemo.complete"]), "An invitation was falsely marked saved")
        await loaded.frame.locator(".tm-demo-journal-later").click()
        state = await demoState(loaded.frame)
        assert.equal(state.choice, "later")
        assert(!(await loaded.frame.locator(STAGE).innerText()).includes(messages["panel.capdemo.complete"]), "Declining an invitation saved an entry")
        await loaded.frame.locator(".tm-demo-journal-write").click()
        assert.equal((await demoState(loaded.frame)).choice, "write")
        assert((await loaded.frame.locator(STAGE).innerText()).includes(messages["panel.capdemo.complete"]), "Explicit writing choice did not produce the example saved state")
        await assertNoMutation(loaded.page, baseline)
      })
      if (await loaded.frame.locator(ROOT).count()) await loaded.frame.locator(".tm-ci-back").click()

      await check("demo-activity-privacy-closes-the-reference-route", async () => {
        await openDemo(loaded, capabilities.indexOf("activity_sense"))
        await setMode(loaded.frame, "after")
        if ((await demoState(loaded.frame)).playing === "true") await loaded.frame.locator(".tm-demo-play").click()
        await finalStep(loaded.frame, sources.intros.activity_sense.flow.length)
        const control = loaded.frame.locator(".tm-demo-privacy")
        if (await control.evaluate(element => element.tagName === "INPUT")) await control.check()
        else await control.click()
        assert.equal((await demoState(loaded.frame)).private, "true")
        assert((await loaded.frame.locator(STAGE).innerText()).includes(sources.messages["zh-CN"]["panel.capdemo.private"]))
        assert.equal(await loaded.frame.locator(`${STAGE} [data-route="closed"]`).count(), 1, "Privacy mode must visibly close the route")
        await assertNoMutation(loaded.page, baseline)
      })

      await check("demo-state-survives-five-second-host-refresh", async () => {
        await setMode(loaded.frame, "before")
        if ((await demoState(loaded.frame)).playing === "true") await loaded.frame.locator(".tm-demo-play").click()
        await loaded.frame.locator(".tm-demo-next").click()
        const before = await demoState(loaded.frame)
        const refreshes = await loaded.page.evaluate(() => window.qaRefreshes)
        await loaded.page.waitForFunction(value => window.qaRefreshes > value, refreshes, { timeout: 6500 })
        const after = await demoState(loaded.frame)
        for (const key of ["mode", "step", "progress", "playing", "private"]) assert.equal(after[key], before[key], `${key} lost on host refresh`)
        await assertNoMutation(loaded.page, baseline)
      })

      await check("demo-runtime-reduced-motion-preference", async () => {
        await setMode(loaded.frame, "after")
        if ((await demoState(loaded.frame)).playing !== "true") await loaded.frame.locator(".tm-demo-play").click()
        await loaded.page.emulateMedia({ reducedMotion: "reduce" })
        await loaded.frame.waitForFunction(() => document.querySelector(".tm-demo")?.dataset.reduced === "true")
        assert.equal((await demoState(loaded.frame)).playing, "false")
        await assertStable(loaded.frame, "runtime reduced motion")
        await loaded.page.emulateMedia({ reducedMotion: "no-preference" })
        await loaded.frame.waitForFunction(() => document.querySelector(".tm-demo")?.dataset.reduced === "false")
        if ((await demoState(loaded.frame)).playing !== "true") await loaded.frame.locator(".tm-demo-play").click()
        await loaded.frame.waitForTimeout(120)
        const before = await demoState(loaded.frame)
        await loaded.frame.waitForTimeout(250)
        const after = await demoState(loaded.frame)
        assert(after.progress !== before.progress || after.step !== before.step, "Animation did not recover after a preference change")
      })

      await check("demo-offscreen-intersection-suspends-owned-clock", async () => {
        await loaded.frame.evaluate(() => {
          const root = document.querySelector(".tm-demo")
          const spacer = document.createElement("div")
          spacer.id = "demo-qa-spacer"
          spacer.style.height = "2400px"
          spacer.style.flexShrink = "0"
          root.before(spacer)
          document.querySelector(".tm-content").scrollTop = 0
        })
        await loaded.frame.waitForTimeout(180)
        await assertStable(loaded.frame, "offscreen")
        await loaded.frame.evaluate(() => document.getElementById("demo-qa-spacer").remove())
        await loaded.frame.locator(ROOT).scrollIntoViewIfNeeded()
        await loaded.frame.waitForTimeout(120)
        const before = await demoState(loaded.frame)
        await loaded.frame.waitForTimeout(250)
        const after = await demoState(loaded.frame)
        assert(after.progress !== before.progress || after.step !== before.step, "Visible demo did not resume")
      })

      await check("demo-synthetic-child-document-visibility-event", async () => {
        await loaded.frame.evaluate(() => {
          Object.defineProperty(document, "hidden", { configurable: true, get: () => true })
          document.dispatchEvent(new Event("visibilitychange"))
        })
        await assertStable(loaded.frame, "synthetic hidden child document")
        await loaded.frame.evaluate(() => {
          delete document.hidden
          document.dispatchEvent(new Event("visibilitychange"))
        })
        await loaded.frame.waitForTimeout(120)
        const before = await demoState(loaded.frame)
        await loaded.frame.waitForTimeout(250)
        const after = await demoState(loaded.frame)
        assert(after.progress !== before.progress || after.step !== before.step, "Visible document did not resume")
      }, { synthetic: true, note: "Exercises the iframe document visibility listener; it does not claim an actual operating-system background transition." })

      await check("demo-controls-never-call-plugin-mutations", () => assertNoMutation(loaded.page, baseline))
      await check("demo-animation-has-no-runtime-errors", async () => {
        assert.deepEqual(loaded.errors, [])
        assert.deepEqual(await loaded.page.evaluate(() => window.qaDiagnostics.filter(message => message.type === "neko-hosted-surface-error")), [])
      })
    } finally { await loaded.context.close() }
  }
  return { screenshots, checks }
}
