import assert from "node:assert/strict"
import { createHash } from "node:crypto"
import { createServer, request as httpRequest } from "node:http"
import { createRequire } from "node:module"
import { cpSync, existsSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, realpathSync, rmSync, statSync, symlinkSync, writeFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { basename, dirname, join, relative, resolve } from "node:path"
import { fileURLToPath, pathToFileURL } from "node:url"
import { spawnSync } from "node:child_process"
import { installFixtureBridge, makeFixture } from "./hosted_ui_fixture.mjs"
import { demoChecks } from "./hosted_ui_demo_checks.mjs"
import { introChecks } from "./hosted_ui_intro_checks.mjs"
import { appearanceChecks, expandAppearance } from "./hosted_ui_appearance_checks.mjs"
import { videoChecks, recordFixture } from "./hosted_ui_video_checks.mjs"
import { settingsChecks, selectSettings, openImport } from "./hosted_ui_settings_checks.mjs"
import { directChecks } from "./hosted_ui_direct_checks.mjs"
import { heatmapChecks } from "./hosted_ui_heatmap_checks.mjs"
import { diaryChecks } from "./hosted_ui_diary_checks.mjs"
import { moodChecks } from "./hosted_ui_mood_checks.mjs"
import { backupChecks } from "./hosted_ui_backup_checks.mjs"

const TOOL_ROOT = dirname(fileURLToPath(import.meta.url))
const DEFAULT_PLUGIN = resolve(TOOL_ROOT, "..")
const DEFAULT_HOST = resolve(DEFAULT_PLUGIN, "..", "N.E.K.O")
const BUNDLED_MODULES = "C:/Users/ms/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules"
const TOOLING_MODULES = join(tmpdir(), "neko-companion-ui-tooling", "node_modules")
const HOSTED = "frontend/plugin-manager/src/components/plugin/hosted"
const TABS = ["overview", "calendar", "diary", "moment", "mood", "features", "cycle", "settings"]

function option(name, fallback) {
  const index = process.argv.indexOf(`--${name}`)
  return index < 0 ? fallback : process.argv[index + 1]
}

function loadDependency(name) {
  const directories = [option("deps", process.env.NEKO_UI_TOOL_DEPS), TOOLING_MODULES, BUNDLED_MODULES].filter(Boolean)
  for (const directory of directories) {
    try {
      const require = createRequire(join(resolve(directory), "__hosted_qa__.cjs"))
      return { value: require(name), path: require.resolve(name), directory: resolve(directory) }
    } catch { /* Try the next isolated dependency directory. */ }
  }
  throw new Error(`Missing ${name}. Install it outside the host and pass --deps <node_modules>.`)
}

function escapeScript(value) {
  return value.replace(/<\/script/gi, "<\\/script").replace(/<!--/g, "<\\!--")
}

function loadFixtureIntros(pluginRoot) {
  const python = join(pluginRoot, ".venv", "Scripts", "python.exe")
  const code = `
import json, runpy, sys
from pathlib import Path
root = Path(sys.argv[1])
specs = runpy.run_path(str(root / "core/capabilities.py"))["CAPABILITY_SPECS"]
build = runpy.run_path(str(root / "core/intros.py"))["build_intro_payload"]
ref = lambda key, default: {"$i18n": key, "default": default}
print(json.dumps({key: build(spec, ref) for key, spec in specs.items() if spec.managed}))
`
  const result = spawnSync(existsSync(python) ? python : "python", ["-B", "-c", code, pluginRoot], { encoding: "utf8" })
  assert.equal(result.status, 0, result.stderr || "Cannot load real capability introductions")
  return JSON.parse(result.stdout)
}

async function loadSources(pluginRoot, hostRoot) {
  const scannerPath = join(hostRoot, HOSTED, "hostedTsxModule.mjs")
  const scanner = await import(pathToFileURL(scannerPath).href)
  const entryPath = "ui/panel.tsx"
  const seen = new Set()
  const dependencies = []
  function collect(path) {
    const source = readFileSync(path, "utf8")
    const found = scanner.findHostedRelativeImportSpecifiers(source)
    for (const specifier of Array.isArray(found) ? found : found.runtime || []) {
      const base = resolve(dirname(path), specifier)
      const dependency = [base, `${base}.ts`, `${base}.tsx`, join(base, "index.ts"), join(base, "index.tsx")].find(file => existsSync(file) && statSync(file).isFile())
      if (!dependency) throw new Error(`Unresolved ${specifier} from ${path}`)
      assert(!relative(pluginRoot, realpathSync(dependency)).startsWith(".."), "Dependency escapes plugin root")
      if (seen.has(dependency)) continue
      seen.add(dependency)
      collect(dependency)
      dependencies.push({ path: relative(pluginRoot, dependency).replaceAll("\\", "/"), source: readFileSync(dependency, "utf8"), bytes: statSync(dependency).size })
    }
  }
  const entry = readFileSync(join(pluginRoot, entryPath), "utf8")
  collect(join(pluginRoot, entryPath))
  const bytes = dependencies.reduce((sum, item) => sum + item.bytes, 0)
  assert(dependencies.length <= 32, "Runtime dependency file budget exceeded")
  assert(bytes <= 512 * 1024, "Runtime dependency byte budget exceeded")
  const bundle = scanner.bundleHostedTsxSource(entry, dependencies, entryPath)
  assert(!/^\s*export\s+(function|const|class)\s/m.test(bundle), "Bare export remains after hosted linker")
  const { transform } = loadDependency("sucrase").value
  const compiled = transform(bundle, { transforms: ["typescript", "jsx"], jsxPragma: "h", jsxFragmentPragma: "Fragment", production: true }).code
    .replace(/\bexport\s+default\s+function\s+([A-Za-z_$][\w$]*)?\s*\(/, (_match, name) => `const __Panel = function ${name || ""}(`)
    .replace(/\bexport\s+default\s+/, "const __Panel = ")
  const messages = {}
  for (const file of readdirSync(join(pluginRoot, "i18n")).filter(file => file.endsWith(".json"))) messages[basename(file, ".json")] = JSON.parse(readFileSync(join(pluginRoot, "i18n", file), "utf8"))
  return {
    compiled, messages, intros: loadFixtureIntros(pluginRoot), dependencies: dependencies.map(({ path, bytes }) => ({ path, bytes })), dependencyBytes: bytes,
    sourceHash: createHash("sha256").update(bundle).digest("hex"),
    runtime: readFileSync(join(hostRoot, HOSTED, "ui-kit/runtime.js"), "utf8"),
    styles: readFileSync(join(hostRoot, HOSTED, "ui-kit/styles.css"), "utf8"),
  }
}

function documentFor(sources, context, origin, initialTab) {
  const payload = { ...context, host: { origin } }
  return `<!doctype html><html lang="${context.locale}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><style>${sources.styles}</style></head><body><main id="root"></main><script>
let __NEKO_PAYLOAD = ${escapeScript(JSON.stringify(payload))};
window.__NEKO_PAYLOAD = __NEKO_PAYLOAD;
${escapeScript(sources.runtime)}
window.__NekoRefreshHostedPayload = function(context) {
  __NEKO_PAYLOAD = { ...__NEKO_PAYLOAD, ...context, host: { origin: ${JSON.stringify(origin)} } };
  window.__NEKO_PAYLOAD = __NEKO_PAYLOAD;
  window.__NekoRenderHostedSurface();
  return __NEKO_PAYLOAD;
};
function __hostedProps() { return { ...__NEKO_PAYLOAD, ...window.NekoUiKit, api: window.NekoUiKit.api, useLocalState: window.NekoUiKit.useLocalState }; }
try {
${escapeScript(sources.compiled)}
window.__NekoRenderHostedSurface = function() {
  window.NekoUiKit.render(window.NekoUiKit.h(__Panel, __hostedProps()), document.getElementById("root"));
};
window.__NekoRenderHostedSurface();
window.__QA_INITIAL_TAB = ${JSON.stringify(initialTab)};
} catch (error) {
  console.error(error);
  document.getElementById("root").textContent = error.stack || String(error);
}
</script></body></html>`
}

function parentDocument(sources, parameters, origin) {
  const fixture = makeFixture(sources.messages, { ...parameters, intros: sources.intros, empty: parameters.empty === "true", wizard: parameters.wizard === "true", video: parameters.video === "true" || parameters.wallpaper === "video" ? sources.qaVideoFixture : null })
  if (parameters.direct && fixture.videos["qa-video"]) {
    fixture.videos["qa-video"].playback_path = parameters.direct === "missing"
      ? `/plugin/forever_companion/ui/${"0".repeat(64)}.webm`
      : parameters.direct === "unsafe" ? "https://invalid.example/private.webm" : sources.qaDirectPath
  }
  const child = documentFor(sources, fixture.context, origin, parameters.tab || "overview")
  return `<!doctype html><html><head><meta charset="utf-8"><style>html,body{margin:0;width:100%;height:100%;overflow:hidden}iframe{display:block;width:100vw;height:100vh;border:0}</style></head><body><iframe id="hosted" title="Isolated Hosted TSX QA" sandbox="allow-scripts"></iframe><script>
const fixture = ${escapeScript(JSON.stringify(fixture))};
fixture.context.host = { origin: window.location.origin };
(${installFixtureBridge.toString()})(fixture);
document.getElementById("hosted").srcdoc = ${escapeScript(JSON.stringify(child))};
</script></body></html>`
}

async function inspectLayout(frame) {
  return frame.evaluate(() => {
    const width = window.innerWidth
    const issues = []
    function clippedByAncestor(element) {
      let ancestor = element.parentElement
      while (ancestor && ancestor !== document.documentElement) {
        const style = getComputedStyle(ancestor)
        if (["hidden", "clip", "auto", "scroll"].includes(style.overflowX)) {
          const bounds = ancestor.getBoundingClientRect()
          if (bounds.left >= -1 && bounds.right <= width + 1) return true
        }
        ancestor = ancestor.parentElement
      }
      return false
    }
    for (const element of document.querySelectorAll("button,input,select,textarea,h1,h2,h3,p,.neko-field-label,.tm-derived,.tm-cal-cell,.tm-tab,.tm-lanlan-row")) {
      const rect = element.getBoundingClientRect()
      const style = getComputedStyle(element)
      if (!rect.width || !rect.height || style.visibility === "hidden" || style.display === "none") continue
      const label = (element.textContent || element.getAttribute("aria-label") || element.tagName).trim().slice(0, 90)
      if ((rect.left < -2 || rect.right > width + 2) && !clippedByAncestor(element)) issues.push({ kind: "outside-viewport", className: element.className, label, left: Math.round(rect.left), right: Math.round(rect.right) })
      if (element.matches("button,h1,h2,h3,.neko-field-label") && element.scrollWidth > element.clientWidth + 3 && style.overflowX === "hidden" && style.textOverflow !== "ellipsis") issues.push({ kind: "clipped-text", className: element.className, label, scrollWidth: element.scrollWidth, clientWidth: element.clientWidth })
    }
    const scroll = document.querySelector(".tm-content")
    return {
      viewport: { width, height: window.innerHeight },
      documentOverflow: Math.max(document.body.scrollWidth, document.documentElement.scrollWidth) - width,
      contentOverflow: scroll ? scroll.scrollWidth - scroll.clientWidth : null,
      issues,
      cards: document.querySelectorAll(".neko-card,.tm-card").length,
      controls: document.querySelectorAll("button,input,select,textarea").length,
      errorNodes: [...document.querySelectorAll(".neko-inline-error,.neko-error")].map(item => item.textContent),
    }
  })
}

async function loadPage(browser, origin, parameters, width, height, theme, motion = "reduce") {
  const context = await browser.newContext({ viewport: { width, height }, colorScheme: theme, deviceScaleFactor: 1, reducedMotion: motion })
  await context.addInitScript(() => {
    window.qaCreatedVideos = []
    const create = document.createElement.bind(document)
    document.createElement = function(name, options) {
      const element = create(name, options)
      if (String(name).toLowerCase() === "video") {
        window.qaCreatedVideos.push(element)
        element.addEventListener("playing", () => {
          if (!element.classList.contains("tm-bg-video") || window.qaFirstWallpaperFrameAt !== undefined) return
          element.requestVideoFrameCallback(() => { window.qaFirstWallpaperFrameAt ??= performance.now() })
        })
      }
      return element
    }
  })
  const page = await context.newPage()
  const errors = []
  page.on("pageerror", error => errors.push(error.message))
  page.on("console", message => { if (message.type() === "error") errors.push(message.text()) })
  const query = new URLSearchParams(parameters)
  await page.goto(`${origin}/?${query}`, { waitUntil: "load" })
  const frame = page.frames().find(item => item.parentFrame() === page.mainFrame())
  assert(frame, "Hosted iframe is missing")
  try {
    await frame.waitForSelector(".tm-tabs", { timeout: 15000 })
  } catch (error) {
    console.error("FRAME STARTUP", JSON.stringify({ errors, frames: page.frames().map(item => item.url()), text: await frame.locator("body").innerText().catch(() => "") }))
    throw error
  }
  await frame.waitForTimeout(150)
  if (parameters.tab && parameters.tab !== "overview") await frame.locator(".tm-tab").nth(TABS.indexOf(parameters.tab)).click()
  await frame.waitForTimeout(170)
  return { context, page, frame, errors }
}

async function screenshotMatrix(browser, sources, origin, output) {
  const full = process.argv.includes("--full")
  const widths = option("widths", full ? "1920,1280,600,390" : "1280,390").split(",").map(Number)
  const themes = option("themes", "light,dark").split(",")
  const wallpapers = option("wallpapers", full ? "none,bright,dark" : "none,bright").split(",")
  const tabs = option("pages", TABS.join(",")).split(",")
  const locale = option("locale", "zh-CN")
  const results = []
  for (const width of widths) for (const theme of themes) for (const wallpaper of wallpapers) {
    const parameters = { locale, wallpaper }
    const { context, page, frame, errors } = await loadPage(browser, origin, parameters, width, width < 700 ? 900 : 900, theme)
    try {
      if (wallpaper !== "none") await frame.waitForSelector(".tm-bg")
      for (const tab of tabs) {
        await frame.locator(".tm-tab").nth(TABS.indexOf(tab)).click()
        await frame.waitForTimeout(160)
        const id = `${width}-${theme}-${wallpaper}-${tab}`
        const layout = await inspectLayout(frame)
        const path = join(output, `${id}.png`)
        await page.screenshot({ path, fullPage: false, animations: "disabled" })
        const diagnostics = await page.evaluate(() => window.qaDiagnostics.filter(message => message.type === "neko-hosted-surface-error"))
        results.push({ id, path, layout, errors: [...errors], diagnostics })
        console.log(`SCREENSHOT ${id}: overflow=${layout.documentOverflow}/${layout.contentOverflow}, issues=${layout.issues.length}, errors=${errors.length + diagnostics.length}`)
      }
    } finally { await context.close() }
  }
  return results
}

async function emotionIntervalChecks(browser, sources, origin, output) {
  const checks = []
  const screenshots = []
  for (const width of [1280, 390]) for (const locale of Object.keys(sources.messages)) {
    const loaded = await loadPage(browser, origin, { locale, wallpaper: "none", tab: "mood" }, width, 900, "light")
    const { page, frame, errors } = loaded
    const id = `emotion-interval-${width}-${locale}`
    try {
      const label = sources.messages[locale]["panel.settings.toneMinIntervalSec"]
      const input = frame.getByLabel(label, { exact: true })
      assert.equal(await input.inputValue(), "20")
      assert.equal(await input.getAttribute("min"), "10")
      assert.equal(await input.getAttribute("max"), "3600")
      await input.fill("45")
      if (width === 390 && locale === "zh-CN") {
        const before = await page.evaluate(() => window.qaRefreshes)
        await page.waitForFunction(value => window.qaRefreshes > value, before, { timeout: 6500 })
        assert.equal(await input.inputValue(), "45")
      }
      const before = await page.evaluate(() => window.qaMessages.length)
      await frame.locator(".tm-save button").click()
      await page.waitForFunction(() => window.qaFixture.context.state.settings.tone_min_interval_sec === 45)
      const calls = await page.evaluate(offset => window.qaMessages.slice(offset).filter(message => message.method === "call" && message.payload.actionId === "update_settings"), before)
      assert.equal(calls.length, 1)
      assert.deepEqual(calls[0].payload.args, { tone_min_interval_sec: 45 })
      await input.scrollIntoViewIfNeeded()
      const layout = await inspectLayout(frame)
      const path = join(output, `${id}.png`)
      await page.screenshot({ path, animations: "disabled" })
      const diagnostics = await page.evaluate(() => window.qaDiagnostics.filter(message => message.type === "neko-hosted-surface-error"))
      screenshots.push({ id, path, layout, errors: [...errors], diagnostics })
      assert.deepEqual(errors, [])
      assert.deepEqual(diagnostics, [])
      assert.equal(layout.documentOverflow, 0)
      assert.equal(layout.contentOverflow, 0)
      assert.deepEqual(layout.issues, [])
      checks.push({ name: id, passed: true })
      console.log(`PASS ${id}`)
    } catch (error) {
      checks.push({ name: id, passed: false, error: error.stack })
      console.log(`FAIL ${id}: ${error.message}`)
    } finally { await loaded.context.close() }
  }
  return { checks, screenshots }
}

async function interactionChecks(browser, origin, output) {
  const { context, page, frame, errors } = await loadPage(browser, origin, { locale: "en", wallpaper: "bright" }, 1280, 900, "light")
  const checks = []
  async function check(name, operation) {
    try { await operation(); checks.push({ name, passed: true }); console.log(`PASS ${name}`) }
    catch (error) { checks.push({ name, passed: false, error: error.stack }); console.log(`FAIL ${name}: ${error.message}`) }
  }
  try {
    await check("all-eight-tabs", async () => {
      for (let index = 0; index < TABS.length; index += 1) {
        await frame.locator(".tm-tab").nth(index).click()
        await frame.waitForTimeout(90)
        assert.equal(await frame.locator(".tm-tab-active").count(), 1)
        assert.equal(await frame.locator(".neko-inline-error,.neko-error").count(), 0)
      }
    })
    await check("five-second-refresh-preserves-unsaved-timezone-and-scroll", async () => {
      await selectSettings(frame, "general")
      const timezone = frame.locator("select").first()
      await timezone.selectOption("Asia/Tokyo")
      await frame.locator(".tm-content").evaluate(element => { element.scrollTop = 240 })
      const scrollBefore = await frame.locator(".tm-content").evaluate(element => element.scrollTop)
      const refreshBefore = await page.evaluate(() => window.qaRefreshes)
      await page.waitForFunction(before => window.qaRefreshes > before, refreshBefore, { timeout: 6500 })
      assert.equal(await timezone.inputValue(), "Asia/Tokyo")
      assert.equal(await frame.locator(".tm-content").evaluate(element => element.scrollTop), scrollBefore)
    })
    await check("wallpaper-draft-survives-refresh-and-reverts", async () => {
      await selectSettings(frame, "appearance")
      await frame.locator(".tm-gallery-none").click()
      assert.equal(await frame.locator(".tm-bg").count(), 0)
      const refreshBefore = await page.evaluate(() => window.qaRefreshes)
      await page.waitForFunction(before => window.qaRefreshes > before, refreshBefore, { timeout: 6500 })
      assert.equal(await frame.locator(".tm-bg").count(), 0)
      await frame.locator(".tm-gallery-tile").nth(1).click()
      await frame.waitForSelector(".tm-bg")
    })
    await check("unified-save-submits-only-dirty-settings", async () => {
      const before = await page.evaluate(() => window.qaMessages.length)
      await frame.locator(".tm-save button").click()
      await page.waitForTimeout(200)
      const calls = await page.evaluate(offset => window.qaMessages.slice(offset).filter(message => message.method === "call" && message.payload.actionId === "update_settings"), before)
      assert.equal(calls.length, 1)
      assert.deepEqual(calls[0].payload.args, { timezone: "Asia/Tokyo" })
      assert.equal(await page.evaluate(() => window.qaFixture.context.state.settings.timezone), "Asia/Tokyo")
    })
    await check("persist-failure-produces-visible-error", async () => {
      await selectSettings(frame, "general")
      await frame.locator("select").first().selectOption("Europe/London")
      await page.evaluate(() => { window.qaFailNext = "update_settings" })
      await frame.locator(".tm-save button").click()
      await frame.waitForSelector('.neko-toast[data-tone="danger"]')
      assert.equal(await page.evaluate(() => window.qaFixture.context.state.settings.timezone), "Asia/Tokyo")
    })
    await check("dangerous-reset-can-be-cancelled", async () => {
      await selectSettings(frame, "data")
      const before = await page.evaluate(() => window.qaMessages.filter(message => message.method === "call" && message.payload.actionId === "reset_all").length)
      const label = await page.evaluate(() => window.qaFixture.context.i18n.messages.en["actions.reset.label"])
      await frame.getByRole("button", { name: label, exact: true }).click()
      await frame.waitForSelector(".neko-modal")
      await frame.getByRole("button", { name: "Cancel", exact: true }).last().click()
      assert.equal(await page.evaluate(() => window.qaMessages.filter(message => message.method === "call" && message.payload.actionId === "reset_all").length), before)
    })
    await check("capability-toggle-and-inline-introduction", async () => {
      await frame.locator(".tm-tab").nth(TABS.indexOf("features")).click()
      await frame.locator(".tm-feat-row").first().locator(".tm-sw-track").click()
      await page.waitForTimeout(120)
      assert.equal(await page.evaluate(() => window.qaFixture.context.state.capabilities.capabilities[0].enabled), false)
      await frame.locator(".tm-ci-open").first().click()
      await frame.waitForSelector(".tm-ci")
      const purpose = await page.evaluate(() => window.qaFixture.context.i18n.messages.en["panel.capintro.whisper.purpose"])
      assert((await frame.locator(".tm-content").innerText()).includes(purpose))
    })
    await check("journal-and-review-render-real-reading-views", async () => {
      await frame.locator(".tm-tab").nth(TABS.indexOf("diary")).click()
      await frame.getByRole("button", { name: "Personal Journal", exact: true }).click()
      await frame.waitForTimeout(120)
      await frame.locator(".tmb-spine:not(.tmb-spine--file)").first().click()
      await frame.waitForTimeout(80)
      assert.equal(await frame.locator(".neko-inline-error,.neko-error").count(), 0)
      await frame.getByRole("button", { name: "My Diary", exact: true }).click()
      await frame.waitForTimeout(120)
      await frame.locator(".tmb-spine--file").first().click()
      assert.equal(await frame.locator(".neko-inline-error,.neko-error").count(), 0)
    })
    await check("no-runtime-errors-during-interaction", async () => {
      const diagnostics = await page.evaluate(() => window.qaDiagnostics.filter(message => message.type === "neko-hosted-surface-error"))
      assert.deepEqual(errors, [])
      assert.deepEqual(diagnostics, [])
    })
  } finally { await context.close() }
  return checks
}

async function galleryChecks(browser, origin, output) {
  const { context, page, frame, errors } = await loadPage(browser, origin, { locale: "en", wallpaper: "bright", tab: "settings" }, 1280, 900, "light")
  const checks = []
  async function check(name, operation) {
    try { await operation(); checks.push({ name, passed: true }); console.log(`PASS ${name}`) }
    catch (error) { checks.push({ name, passed: false, error: error.stack }); console.log(`FAIL ${name}: ${error.message}`) }
  }
  const background = () => frame.locator(".tm-bg-image").evaluate(element => element.style.backgroundImage)
  try {
    await check("gallery-pick-buttons-support-keyboard", async () => {
      assert.equal(await frame.locator(".tm-gallery-pick").count(), 4)
      await frame.locator(".tm-gallery-pick").nth(2).focus()
      await frame.locator(".tm-gallery-pick").nth(2).press("Enter")
      await page.waitForTimeout(150)
      assert.equal(await frame.locator(".tm-gallery-preview").count(), 1)
      assert.equal(await frame.locator(".tm-gallery-applied").count(), 1)
      assert.equal(await frame.locator(".tm-gallery-preview .tm-gallery-pick").getAttribute("aria-pressed"), "true")
      assert.equal(await frame.locator(".tm-gallery-applied .tm-gallery-pick").getAttribute("aria-pressed"), "false")
    })
    await check("preview-and-applied-badges-are-distinct", async () => {
      const preview = await frame.locator(".tm-gallery-preview .tm-gallery-use").innerText()
      const applied = await frame.locator(".tm-gallery-applied .tm-gallery-use").innerText()
      assert.notEqual(preview, applied)
      assert(preview.length && applied.length)
      await page.screenshot({ path: join(output, "gallery-preview-applied.png"), animations: "disabled" })
    })
    await check("revert-restores-applied-wallpaper", async () => {
      await frame.getByRole("button", { name: "Revert", exact: true }).click()
      await page.waitForTimeout(100)
      assert.equal(await frame.locator(".tm-gallery-preview").count(), 0)
      assert.equal(await frame.locator(".tm-gallery-applied .tm-gallery-pick").getAttribute("aria-pressed"), "true")
    })
    await check("delete-button-keyboard-does-not-select-wallpaper", async () => {
      const before = await background()
      await frame.locator(".tm-gallery-tile").nth(2).locator(".tm-gallery-del").focus()
      await frame.locator(".tm-gallery-tile").nth(2).locator(".tm-gallery-del").press("Enter")
      await frame.waitForSelector(".neko-modal")
      assert.equal(await background(), before)
      assert.equal(await frame.locator(".tm-gallery-preview").count(), 0)
      await frame.getByRole("button", { name: "Cancel", exact: true }).last().click()
      assert.equal(await page.evaluate(() => window.qaMessages.filter(message => message.method === "call" && message.payload.actionId === "gallery_remove").length), 0)
    })
    await check("upload-adds-to-gallery-without-selecting", async () => {
      await openImport(frame, "image")
      const before = await background()
      const data = await page.evaluate(() => window.qaFixture.image.split(",")[1])
      await frame.locator('input[type=file][accept="image/*"]').setInputFiles({ name: "qa-upload.png", mimeType: "image/png", buffer: Buffer.from(data, "base64") })
      await page.waitForFunction(() => window.qaFixture.gallery.length === 4)
      await page.waitForTimeout(150)
      assert.equal(await frame.locator(".tm-gallery-pick").count(), 5)
      assert.equal(await background(), before)
      assert.equal(await frame.locator(".tm-gallery-preview").count(), 0)
    })
    await check("glass-zero-and-card-alpha-zero-preview-and-save", async () => {
      await expandAppearance(frame)
      const range = frame.locator('input[type="range"]')
      const controls = await range.evaluateAll(elements => elements.map(element => ({ max: element.max, min: element.min, value: element.value })))
      const glassIndex = controls.findIndex(item => item.max === "40")
      const alphaIndex = controls.findIndex(item => item.max === "100" && item.min === "0")
      assert(glassIndex >= 0 && alphaIndex >= 0, `Cannot identify appearance sliders: ${JSON.stringify(controls)}`)
      for (const [index, value] of [[glassIndex, "12"], [alphaIndex, "50"]]) await range.nth(index).evaluate((element, next) => { element.value = next; element.dispatchEvent(new Event("input", { bubbles: true })) }, value)
      await frame.locator(".tm-save button").click()
      await page.waitForFunction(() => window.qaFixture.appearance.glass === 12 && window.qaFixture.appearance.card_alpha === 50)
      for (const index of [glassIndex, alphaIndex]) await range.nth(index).evaluate(element => { element.value = "0"; element.dispatchEvent(new Event("input", { bubbles: true })) })
      const variables = await frame.locator(".tm-appearance-root").evaluate(element => ({ glass: element.style.getPropertyValue("--tm-glass"), card: element.style.getPropertyValue("--tm-card-k") }))
      assert.equal(variables.glass, "0px")
      assert.equal(variables.card, "0")
      const surfaces = await frame.locator(".neko-card,.tm-card,.tm-settings-surface,.tm-settings-nav,.tm-settings-section,.tm-tabs,.tm-statusbar,.tm-save,.neko-input,.neko-select,.neko-textarea").evaluateAll(elements => elements.map(element => {
        const style = getComputedStyle(element)
        return { className: element.className, background: style.backgroundColor, filter: style.backdropFilter }
      }))
      assert(surfaces.length > 5)
      for (const surface of surfaces) {
        assert.equal(surface.filter, "none", `${surface.className} retains glass at zero`)
        assert(surface.background === "transparent" || surface.background.endsWith(", 0)"), `${surface.className} retains opacity at zero: ${surface.background}`)
      }
      const before = await page.evaluate(() => window.qaMessages.length)
      await frame.locator(".tm-save button").click()
      await page.waitForTimeout(180)
      const appearanceCalls = await page.evaluate(offset => window.qaMessages.slice(offset).filter(message => message.method === "call" && message.payload.actionId === "set_panel_appearance"), before)
      assert.equal(appearanceCalls.length, 1)
      assert.equal(appearanceCalls[0].payload.args.glass, 0)
      assert.equal(appearanceCalls[0].payload.args.card_alpha, 0)
    })
    await check("unified-save-submits-settings-and-appearance-once", async () => {
      await selectSettings(frame, "general")
      await frame.locator("select").first().selectOption("Europe/Berlin")
      await selectSettings(frame, "appearance")
      await frame.locator(".tm-gallery-pick").nth(3).click()
      const before = await page.evaluate(() => window.qaMessages.length)
      await frame.locator(".tm-save button").click()
      await page.waitForTimeout(180)
      const calls = await page.evaluate(offset => window.qaMessages.slice(offset).filter(message => message.method === "call"), before)
      assert.equal(calls.filter(message => message.payload.actionId === "update_settings").length, 1)
      assert.equal(calls.filter(message => message.payload.actionId === "set_panel_appearance").length, 1)
      assert.equal(await frame.locator(".tm-gallery-preview").count(), 0)
    })
    await check("gallery-delete-confirm-removes-without-wrong-selection", async () => {
      const before = await background()
      await frame.locator(".tm-gallery-tile").nth(2).locator(".tm-gallery-del").click()
      await frame.waitForSelector(".neko-modal")
      await frame.getByRole("button", { name: "Confirm", exact: true }).last().click()
      await page.waitForTimeout(150)
      assert.equal(await page.evaluate(() => window.qaFixture.gallery.length), 3)
      assert.equal(await background(), before)
    })
    await check("gallery-interactions-have-no-runtime-errors", async () => {
      assert.deepEqual(errors, [])
      assert.deepEqual(await page.evaluate(() => window.qaDiagnostics.filter(message => message.type === "neko-hosted-surface-error")), [])
    })
  } finally { await context.close() }

  const delayed = await loadPage(browser, origin, { locale: "en", wallpaper: "bright", tab: "settings" }, 1280, 900, "light")
  try {
    await check("late-B-image-cannot-replace-current-C", async () => {
      await delayed.frame.waitForSelector(".tm-bg")
      const initial = await delayed.frame.locator(".tm-bg-image").evaluate(element => element.style.backgroundImage)
      await delayed.page.evaluate(() => { window.qaDelays = { "qa-wallpaper-b": 800, "qa-wallpaper-c": 150 } })
      await delayed.frame.locator(".tm-gallery-pick").nth(2).click()
      await delayed.page.waitForTimeout(60)
      assert.equal(await delayed.frame.locator(".tm-bg-image").evaluate(element => element.style.backgroundImage), initial)
      await delayed.frame.locator(".tm-gallery-pick").nth(3).click()
      await delayed.page.waitForTimeout(350)
      const expected = await delayed.page.evaluate(() => window.qaFixture.images["qa-wallpaper-c"])
      assert((await delayed.frame.locator(".tm-bg-image").evaluate(element => element.style.backgroundImage)).includes(expected))
      await delayed.page.waitForTimeout(650)
      assert((await delayed.frame.locator(".tm-bg-image").evaluate(element => element.style.backgroundImage)).includes(expected))
      assert.equal(await delayed.frame.locator(".tm-gallery-preview .tm-gallery-pick").getAttribute("aria-label"), "qa-landscape-C.png")
    })
  } finally { await delayed.context.close() }
  const cancelled = await loadPage(browser, origin, { locale: "en", wallpaper: "bright", tab: "settings" }, 1280, 900, "light")
  try {
    await check("cancel-wallpaper-makes-late-image-inactive", async () => {
      await cancelled.page.evaluate(() => { window.qaDelays = { "qa-wallpaper-b": 600 } })
      await cancelled.frame.locator(".tm-gallery-pick").nth(2).click()
      await cancelled.page.waitForTimeout(60)
      await cancelled.frame.locator(".tm-gallery-pick").nth(0).click()
      await cancelled.page.waitForTimeout(120)
      assert.equal(await cancelled.frame.locator(".tm-bg").count(), 0)
      await cancelled.page.waitForTimeout(800)
      assert.equal(await cancelled.frame.locator(".tm-bg").count(), 0)
      assert.equal((await inspectTheme(cancelled.frame)).rawPrimary, "#409eff")
      assert.equal(await cancelled.page.evaluate(() => window.qaResolved.filter(item => item.imageId === "qa-wallpaper-b").length), 1)
    })
  } finally { await cancelled.context.close() }
  const broken = await loadPage(browser, origin, { locale: "en", wallpaper: "bright", tab: "settings" }, 1280, 900, "light")
  try {
    await check("broken-image-keeps-previous-wallpaper", async () => {
      const previous = await broken.frame.locator(".tm-bg-image").evaluate(element => element.style.backgroundImage)
      const previousTheme = await inspectTheme(broken.frame)
      await broken.page.evaluate(() => { window.qaFixture.images["qa-wallpaper-b"] = "data:image/png;base64,bm90IGEgcG5n" })
      await broken.frame.locator(".tm-gallery-pick").nth(2).click()
      await broken.page.waitForTimeout(400)
      assert.equal(await broken.frame.locator(".tm-bg-image").evaluate(element => element.style.backgroundImage), previous)
      assert.deepEqual(await inspectTheme(broken.frame), previousTheme)
      assert.equal(await broken.frame.locator(".tm-gallery-preview .tm-gallery-pick").getAttribute("aria-label"), "qa-landscape-B.png")
    })
    await check("rejected-image-decode-keeps-previous-wallpaper", async () => {
      const previous = await broken.frame.locator(".tm-bg-image").evaluate(element => element.style.backgroundImage)
      const previousTheme = await inspectTheme(broken.frame)
      const rejectedUrl = await broken.page.evaluate(() => window.qaFixture.images["qa-wallpaper-c"])
      await broken.frame.evaluate(url => {
        const decode = HTMLImageElement.prototype.decode
        HTMLImageElement.prototype.decode = function() {
          return this.src === url ? Promise.reject(new Error("QA deliberate decode rejection")) : decode.call(this)
        }
      }, rejectedUrl)
      await broken.frame.locator(".tm-gallery-pick").nth(3).click()
      await broken.page.waitForTimeout(400)
      assert.equal(await broken.frame.locator(".tm-bg-image").evaluate(element => element.style.backgroundImage), previous)
      assert.deepEqual(await inspectTheme(broken.frame), previousTheme)
      assert.equal(await broken.frame.locator(".tm-gallery-preview .tm-gallery-pick").getAttribute("aria-label"), "qa-landscape-C.png")
    })
  } finally { await broken.context.close() }
  return checks
}

async function inspectTheme(frame) {
  return frame.locator(".tm-appearance-root").evaluate(element => {
    const style = getComputedStyle(element)
    const read = name => style.getPropertyValue(name).trim()
    const rgb = hex => `rgb(${[1, 3, 5].map(index => parseInt(hex.slice(index, index + 2), 16)).join(", ")})`
    const primary = read("--primary")
    const secondary = read("--secondary")
    const controlPrimary = read("--tm-control-primary") || primary
    const controlSecondary = read("--tm-control-secondary") || secondary
    const switchTrack = element.querySelector(".tm-sw--on .tm-sw-track")
    const slider = element.querySelector(".neko-slider-input")
    const progress = element.querySelector(".tm-review-bar-fill")
    return {
      primary, secondary, primaryRgb: rgb(primary), secondaryRgb: rgb(secondary),
      controlPrimary, controlSecondary, controlPrimaryRgb: rgb(controlPrimary), controlSecondaryRgb: rgb(controlSecondary),
      rawPrimary: read("--tm-theme-primary"), rawSecondary: read("--tm-theme-secondary"),
      lightPrimary: read("--tm-theme-primary-light"), darkPrimary: read("--tm-theme-primary-dark"),
      source: element.querySelector(".tm-theme-colors")?.dataset.source,
      switchColor: switchTrack && getComputedStyle(switchTrack).backgroundColor,
      sliderColor: slider && getComputedStyle(slider).accentColor,
      progressColor: progress && getComputedStyle(progress).backgroundColor,
      warning: read("--warning"), success: read("--success"), danger: read("--danger"),
      hostInlinePrimary: document.documentElement.style.getPropertyValue("--primary"),
      swatches: [...element.querySelectorAll(".tm-theme-color code")].map(node => node.textContent),
    }
  })
}

async function inspectPlainReadability(frame) {
  return frame.evaluate(() => {
    const canvas = document.createElement("canvas")
    canvas.width = canvas.height = 1
    const context = canvas.getContext("2d")
    const color = css => {
      context.clearRect(0, 0, 1, 1)
      context.fillStyle = css
      context.fillRect(0, 0, 1, 1)
      return [...context.getImageData(0, 0, 1, 1).data]
    }
    const blend = (front, back) => front.slice(0, 3).map((value, index) => value * front[3] / 255 + back[index] * (1 - front[3] / 255))
    const luminance = rgb => rgb.map(channel => {
      const value = channel / 255
      return value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4
    }).reduce((sum, channel, index) => sum + channel * [0.2126, 0.7152, 0.0722][index], 0)
    const ratio = (left, right) => (Math.max(luminance(left), luminance(right)) + 0.05) / (Math.min(luminance(left), luminance(right)) + 0.05)
    return [...document.querySelectorAll('.tm-tab-active,.tm-save .neko-button[data-tone="primary"],.tm-card-title,.neko-field-help,.tm-status-summary')].filter(element => element.getClientRects().length).map(element => {
      const ancestors = []
      for (let current = element; current; current = current.parentElement) ancestors.unshift(current)
      let background = [255, 255, 255]
      for (const ancestor of ancestors) background = blend(color(getComputedStyle(ancestor).backgroundColor), background)
      const style = getComputedStyle(element)
      const foreground = blend(color(style.color), background)
      const focus = blend(color(style.outlineColor), background)
      return { text: element.textContent.trim().slice(0, 50), contrast: ratio(foreground, background), foreground, background,
        focusVisible: element.matches(":focus-visible"), outlineWidth: parseFloat(style.outlineWidth), focusContrast: ratio(focus, background) }
    })
  })
}

async function themeChecks(browser, origin, output) {
  const checks = []
  async function check(name, operation) {
    try { const detail = await operation(); checks.push({ name, passed: true, detail }); console.log(`PASS ${name}`) }
    catch (error) { checks.push({ name, passed: false, error: error.stack }); console.log(`FAIL ${name}: ${error.message}`) }
  }
  for (const width of [1280, 390]) for (const theme of ["light", "dark"]) {
    const plain = await loadPage(browser, origin, { locale: "en", wallpaper: "none", tab: "settings" }, width, 900, theme)
    try {
      await check(`default-blue-without-wallpaper-${width}-${theme}`, async () => {
        const colors = await inspectTheme(plain.frame)
        assert.equal(colors.rawPrimary, "#409eff")
        assert.equal(colors.rawSecondary, "#7ba7d1")
        assert.equal(colors.source, "default")
        assert.equal(colors.primary, theme === "light" ? "#456f89" : "#b0d1e1")
        assert.equal(colors.controlPrimary, theme === "light" ? "#81b5d2" : "#83b0c8")
        assert.equal(colors.switchColor, colors.controlPrimaryRgb)
        assert.notEqual(colors.controlPrimary, colors.primary)
        assert.equal(colors.hostInlinePrimary, "")
        const material = await plain.frame.locator(".tm-appearance-root").evaluate(element => ({
          card: element.style.getPropertyValue("--tm-card-k"),
          glass: element.style.getPropertyValue("--tm-glass-filter"),
        }))
        assert.deepEqual(material, { card: "1", glass: "none" })
        return colors
      })
      await check(`soft-default-theme-readable-and-keyboard-focus-${width}-${theme}`, async () => {
        let measurements = await inspectPlainReadability(plain.frame)
        assert(measurements.length >= 3)
        for (const item of measurements) assert(item.contrast >= 4.5, JSON.stringify(item))
        await plain.frame.locator(".tm-save button").hover()
        measurements = await inspectPlainReadability(plain.frame)
        for (const item of measurements) assert(item.contrast >= 4.5, JSON.stringify(item))
        await plain.frame.locator(".tm-tab-active").hover()
        measurements = await inspectPlainReadability(plain.frame)
        for (const item of measurements) assert(item.contrast >= 4.5, JSON.stringify(item))
        await plain.frame.locator(".tm-tab-active").focus()
        await plain.frame.locator(".tm-tab-active").press("Shift+Tab")
        await plain.page.keyboard.press("Tab")
        measurements = await inspectPlainReadability(plain.frame)
        const focused = measurements.find(item => item.focusVisible)
        assert(focused && focused.outlineWidth >= 2 && focused.focusContrast >= 3, JSON.stringify(measurements))
        await plain.frame.locator(".tm-theme-colors").evaluate(element => element.scrollIntoView({ block: "center", inline: "nearest" }))
        await plain.page.screenshot({ path: join(output, `${width}-${theme}-soft-default-settings.png`), animations: "disabled" })
        await plain.frame.locator(".tm-tab").nth(TABS.indexOf("overview")).click()
        const colors = await inspectTheme(plain.frame)
        assert.equal(colors.progressColor, colors.controlSecondaryRgb)
        await plain.page.screenshot({ path: join(output, `${width}-${theme}-soft-default-overview.png`), animations: "disabled" })
        await plain.frame.locator(".tm-tab").nth(TABS.indexOf("settings")).click()
        assert.deepEqual(plain.errors, [])
        return measurements
      })
    } finally { await plain.context.close() }
    for (const wallpaper of ["olive", "rose"]) {
      const loaded = await loadPage(browser, origin, { locale: "en", wallpaper, tab: "settings" }, width, 900, theme)
      try {
        await check(`wallpaper-primary-secondary-linked-${wallpaper}-${width}-${theme}`, async () => {
          await loaded.frame.waitForSelector(".tm-bg")
          await expandAppearance(loaded.frame)
          const colors = await inspectTheme(loaded.frame)
          assert.equal(colors.source, "wallpaper")
          assert.notEqual(colors.rawPrimary, "#409eff")
          assert.notEqual(colors.rawPrimary, colors.rawSecondary)
          const channels = [1, 3, 5].map(index => parseInt(colors.rawPrimary.slice(index, index + 2), 16))
          if (wallpaper === "olive") assert(channels[0] > channels[2] && channels[1] > channels[2], JSON.stringify(colors))
          else assert(channels[0] > channels[1] && channels[2] > channels[1], JSON.stringify(colors))
          assert.equal(colors.primary, colors.darkPrimary)
          assert.equal(colors.switchColor, colors.primaryRgb)
          assert.equal(colors.sliderColor, colors.primaryRgb)
          assert.deepEqual(colors.swatches, [colors.rawPrimary, colors.rawSecondary])
          assert.equal(colors.hostInlinePrimary, "")
          const values = await loaded.frame.locator('input[type="range"]').evaluateAll(elements => elements.map(element => Number(element.value)))
          assert.deepEqual(values, [0, 0.4, 100, 100, 100, 0, 0, 100])
          const material = await loaded.frame.locator(".tm-appearance-root").evaluate(element => ({
            card: element.style.getPropertyValue("--tm-card-k"),
            glass: element.style.getPropertyValue("--tm-glass-filter"),
          }))
          assert.deepEqual(material, { card: "0", glass: "none" })
          assert.equal(await loaded.frame.locator(".tm-bg-dim").evaluate(element => element.style.opacity), "0.4")
          await loaded.frame.locator(".tm-theme-colors").evaluate(element => element.scrollIntoView({ block: "center", inline: "nearest" }))
          const settingsLayout = await inspectLayout(loaded.frame)
          assert.equal(settingsLayout.contentOverflow, 0, JSON.stringify(settingsLayout))
          const bounds = await loaded.frame.locator(".tm-theme-color").evaluateAll(elements => elements.map(element => {
            const box = element.getBoundingClientRect()
            const range = document.createRange()
            range.selectNodeContents(element)
            return { left: box.left, right: box.right, fragments: [...range.getClientRects()].map(rect => ({ left: rect.left, right: rect.right })) }
          }))
          for (const item of bounds) {
            assert(item.left >= 0 && item.right <= width, JSON.stringify(bounds))
            assert(item.fragments.every(rect => rect.left >= item.left - 1 && rect.right <= item.right + 1), JSON.stringify(bounds))
          }
          await loaded.page.screenshot({ path: join(output, `${width}-${theme}-${wallpaper}-theme-colors.png`), animations: "disabled" })
          await loaded.frame.locator(".tm-tab").nth(TABS.indexOf("overview")).click()
          const overview = await inspectTheme(loaded.frame)
          assert.equal(overview.progressColor, colors.secondaryRgb)
          assert.deepEqual(loaded.errors, [])
          const layout = await inspectLayout(loaded.frame)
          assert.equal(layout.issues.length, 0, JSON.stringify(layout))
          await loaded.page.screenshot({ path: join(output, `${width}-${theme}-${wallpaper}-theme-overview.png`), animations: "disabled" })
          return colors
        })
      } finally { await loaded.context.close() }
    }
  }
  const gray = await loadPage(browser, origin, { locale: "en", wallpaper: "gray", tab: "settings" }, 390, 900, "light")
  try {
    await check("gray-wallpaper-falls-back-without-disabling-background", async () => {
      assert.equal(await gray.frame.locator(".tm-bg").count(), 1)
      const colors = await inspectTheme(gray.frame)
      assert.equal(colors.rawPrimary, "#409eff")
      assert.equal(colors.rawSecondary, "#7ba7d1")
      assert.equal(colors.source, "default")
      return colors
    })
  } finally { await gray.context.close() }
  const loaded = await loadPage(browser, origin, { locale: "en", wallpaper: "olive", tab: "settings" }, 1280, 900, "light")
  try {
    const initial = await inspectTheme(loaded.frame)
    await loaded.frame.evaluate(() => {
      window.qaThemeReads = 0
      const read = CanvasRenderingContext2D.prototype.getImageData
      CanvasRenderingContext2D.prototype.getImageData = function(...args) {
        window.qaThemeReads++
        return read.apply(this, args)
      }
    })
    await check("theme-switch-is-atomic-and-ignores-late-images", async () => {
      await loaded.page.evaluate(() => { window.qaDelays = { "qa-wallpaper-b": 800, "qa-wallpaper-c": 150 } })
      await loaded.frame.locator(".tm-gallery-pick").nth(2).click()
      await loaded.page.waitForTimeout(60)
      assert.equal((await inspectTheme(loaded.frame)).rawPrimary, initial.rawPrimary)
      await loaded.frame.locator(".tm-gallery-pick").nth(3).click()
      await loaded.page.waitForTimeout(400)
      const next = await inspectTheme(loaded.frame)
      assert.notEqual(next.rawPrimary, initial.rawPrimary)
      await loaded.page.waitForTimeout(650)
      assert.deepEqual(await inspectTheme(loaded.frame), next)
      assert.equal(await loaded.frame.evaluate(() => window.qaThemeReads), 1)
      assert.deepEqual({ warning: next.warning, success: next.success, danger: next.danger }, { warning: initial.warning, success: initial.success, danger: initial.danger })
      return next
    })
    await check("revert-restores-palette-from-cache", async () => {
      await loaded.frame.getByRole("button", { name: "Revert", exact: true }).click()
      await loaded.page.waitForTimeout(200)
      assert.deepEqual(await inspectTheme(loaded.frame), initial)
      assert.equal(await loaded.frame.evaluate(() => window.qaThemeReads), 1)
    })
    await check("refresh-and-filter-sliders-do-not-reextract-palette", async () => {
      await expandAppearance(loaded.frame)
      const refreshBefore = await loaded.page.evaluate(() => window.qaRefreshes)
      const ranges = loaded.frame.locator('input[type="range"]')
      await ranges.nth(0).evaluate(element => { element.value = "10"; element.dispatchEvent(new Event("input", { bubbles: true })) })
      await loaded.page.waitForFunction(before => window.qaRefreshes > before, refreshBefore, { timeout: 6500 })
      assert.equal((await inspectTheme(loaded.frame)).rawPrimary, initial.rawPrimary)
      assert.equal(await loaded.frame.evaluate(() => window.qaThemeReads), 1)
    })
    await check("no-wallpaper-restores-blue-and-save-keeps-existing-schema", async () => {
      await loaded.frame.locator(".tm-gallery-pick").nth(0).click()
      await loaded.page.waitForTimeout(100)
      const colors = await inspectTheme(loaded.frame)
      assert.equal(colors.source, "default")
      assert.equal(colors.rawPrimary, "#409eff")
      assert.equal(colors.rawSecondary, "#7ba7d1")
      assert.equal(colors.primary, "#456f89")
      assert.equal(colors.switchColor, colors.controlPrimaryRgb)
      assert.equal(await loaded.frame.locator(".tm-bg").count(), 0)
      const before = await loaded.page.evaluate(() => window.qaMessages.length)
      await loaded.frame.locator(".tm-save button").click()
      await loaded.page.waitForTimeout(180)
      const calls = await loaded.page.evaluate(offset => window.qaMessages.slice(offset).filter(message => message.method === "call" && message.payload.actionId === "set_panel_appearance"), before)
      assert.equal(calls.length, 1)
      assert.deepEqual(Object.keys(calls[0].payload.args).sort(), ["bg_id", "fill", "position", "blur", "dim", "brightness", "saturate", "contrast", "glass", "card_alpha", "text_weight", "motion"].sort())
      assert.equal(calls[0].payload.args.motion, true, "Image appearance saves must preserve the default video-motion preference")
      assert.deepEqual(loaded.errors, [])
      return colors
    })
  } finally { await loaded.context.close() }
  const failed = await loadPage(browser, origin, { locale: "en", wallpaper: "olive", tab: "settings" }, 1280, 900, "light")
  try {
    await check("canvas-read-failure-uses-blue-without-breaking-preview", async () => {
      await failed.frame.evaluate(() => { CanvasRenderingContext2D.prototype.getImageData = function() { throw new Error("QA unreadable pixels") } })
      await failed.frame.locator(".tm-gallery-pick").nth(3).click()
      await failed.page.waitForTimeout(350)
      assert.equal(await failed.frame.locator(".tm-bg").count(), 1)
      assert.equal((await inspectTheme(failed.frame)).rawPrimary, "#409eff")
      assert.deepEqual(failed.errors, [])
    })
  } finally { await failed.context.close() }
  for (const wallpaper of ["white", "olive", "rose"]) {
    const clear = await loadPage(browser, origin, { locale: "en", wallpaper, tab: "settings" }, 390, 900, "light")
    try {
      await check(`colored-controls-readable-with-zero-mask-and-card-${wallpaper}`, async () => {
        await expandAppearance(clear.frame)
        const ranges = clear.frame.locator('input[type="range"]')
        const controls = await ranges.evaluateAll(elements => elements.map(element => ({ min: element.min, max: element.max })))
        for (const index of [controls.findIndex(item => item.max === "0.85"), controls.findIndex(item => item.min === "0" && item.max === "100")]) {
          assert(index >= 0)
          await ranges.nth(index).evaluate(element => { element.value = "0"; element.dispatchEvent(new Event("input", { bubbles: true })) })
        }
        const measurements = await clear.frame.evaluate(() => {
          const canvas = document.createElement("canvas")
          canvas.width = canvas.height = 1
          const context = canvas.getContext("2d")
          const color = css => {
            context.clearRect(0, 0, 1, 1)
            context.fillStyle = css
            context.fillRect(0, 0, 1, 1)
            return [...context.getImageData(0, 0, 1, 1).data]
          }
          const luminance = rgb => rgb.slice(0, 3).map(channel => {
            const value = channel / 255
            return value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4
          }).reduce((sum, channel, index) => sum + channel * [0.2126, 0.7152, 0.0722][index], 0)
          return [...document.querySelectorAll('.tm-tab-active,.tm-save .neko-button[data-tone="primary"]')].map(element => {
            const style = getComputedStyle(element)
            const foreground = color(style.color)
            const background = color(style.backgroundColor)
            const values = [luminance(foreground), luminance(background)].sort((left, right) => right - left)
            return { text: element.textContent, foreground, background, contrast: (values[0] + 0.05) / (values[1] + 0.05) }
          })
        })
        assert.equal(measurements.length, 2)
        for (const item of measurements) {
          assert.equal(item.background[3], 255, JSON.stringify(measurements))
          assert(item.contrast >= 4.5, JSON.stringify(measurements))
        }
        await clear.frame.locator(".tm-save button").focus()
        await clear.frame.locator(".tm-save button").press("Shift+Tab")
        await clear.page.keyboard.press("Tab")
        await clear.frame.locator(".tm-save button").hover()
        const focus = await clear.frame.locator(".tm-save button").evaluate(element => {
          const style = getComputedStyle(element)
          return { visible: element.matches(":focus-visible"), shadow: style.boxShadow }
        })
        assert(focus.visible && focus.shadow !== "none", JSON.stringify(focus))
        await clear.page.screenshot({ path: join(output, `390-${wallpaper}-zero-material-colors.png`), animations: "disabled" })
        return measurements
      })
    } finally { await clear.context.close() }
  }
  const deleted = await loadPage(browser, origin, { locale: "en", wallpaper: "olive", tab: "settings" }, 1280, 900, "light")
  try {
    await check("deleted-image-response-cannot-refill-a-reused-cache-entry", async () => {
      await deleted.page.evaluate(() => { window.qaDelays = { "qa-wallpaper-b": 900 } })
      await deleted.frame.locator(".tm-gallery-pick").nth(2).click()
      await deleted.page.waitForFunction(() => window.qaMessages.some(message => message.method === "call" && message.payload.actionId === "get_gallery_image" && message.payload.args.item_id === "qa-wallpaper-b"))
      await deleted.frame.locator(".tm-gallery-tile").nth(2).locator(".tm-gallery-del").click()
      await deleted.frame.getByRole("button", { name: "Confirm", exact: true }).last().click()
      await deleted.page.waitForFunction(() => window.qaFixture.gallery.length === 2)
      await deleted.page.waitForTimeout(1100)
      await deleted.page.evaluate(() => {
        window.qaDelays = {}
        const image = window.qaFixture.images["qa-wallpaper-c"]
        window.qaFixture.images["qa-wallpaper-b"] = image
        window.qaFixture.gallery.push({ id: "qa-wallpaper-b", name: "qa-readded-B.png", thumb: image, mime: "image/png", size: image.length })
      })
      const upload = await deleted.page.evaluate(() => window.qaFixture.image.split(",")[1])
      await deleted.frame.locator('input[type=file][accept="image/*"]').setInputFiles({ name: "qa-refresh-gallery.png", mimeType: "image/png", buffer: Buffer.from(upload, "base64") })
      await deleted.page.waitForFunction(() => window.qaFixture.gallery.length === 4)
      await deleted.frame.getByRole("button", { name: "qa-readded-B.png", exact: true }).click()
      await deleted.page.waitForTimeout(300)
      const expected = await deleted.page.evaluate(() => window.qaFixture.images["qa-wallpaper-c"])
      assert((await deleted.frame.locator(".tm-bg-image").evaluate(element => element.style.backgroundImage)).includes(expected))
      const reads = await deleted.page.evaluate(() => window.qaMessages.filter(message => message.method === "call" && message.payload.actionId === "get_gallery_image" && message.payload.args.item_id === "qa-wallpaper-b").length)
      assert.equal(reads, 2)
      assert.deepEqual(deleted.errors, [])
    })
  } finally { await deleted.context.close() }
  for (const locale of ["zh-CN", "zh-TW", "en", "ja", "ko", "es", "pt", "ru"]) {
    const localized = await loadPage(browser, origin, { locale, wallpaper: "olive", tab: "settings" }, 390, 600, "light")
    try {
      await check(`theme-swatches-fit-${locale}-390x600`, async () => {
        await localized.frame.locator(".tm-theme-colors").evaluate(element => element.scrollIntoView({ block: "center", inline: "nearest" }))
        const layout = await inspectLayout(localized.frame)
        assert.equal(layout.contentOverflow, 0, JSON.stringify(layout))
        const geometry = await localized.frame.locator(".tm-theme-color").evaluateAll(elements => elements.map(element => {
          const rect = element.getBoundingClientRect()
          return { text: element.textContent, left: rect.left, right: rect.right, top: rect.top, bottom: rect.bottom, overflow: element.scrollWidth - element.clientWidth }
        }))
        assert.equal(geometry.length, 2)
        for (const rect of geometry) assert(rect.left >= 0 && rect.right <= 390 && rect.top >= 0 && rect.bottom <= 540 && rect.overflow <= 1, JSON.stringify(geometry))
        await localized.page.screenshot({ path: join(output, `390x600-${locale}-theme-swatches.png`), animations: "disabled" })
        return geometry
      })
    } finally { await localized.context.close() }
  }
  return checks
}

async function inspectFooterGeometry(frame) {
  return frame.evaluate(() => {
    const rect = element => {
      const bounds = element.getBoundingClientRect()
      return { left: bounds.left, right: bounds.right, top: bounds.top, bottom: bounds.bottom, width: bounds.width, height: bounds.height }
    }
    const foot = document.querySelector(".tmb-foot")
    const save = document.querySelector(".tm-save")
    const content = document.querySelector(".tm-content")
    const ancestors = []
    for (let element = foot.parentElement; element && element !== content; element = element.parentElement) {
      const style = getComputedStyle(element)
      ancestors.push({ className: element.className, overflowX: style.overflowX, overflowY: style.overflowY, position: style.position })
    }
    return {
      foot: rect(foot), save: rect(save), content: rect(content),
      clearance: getComputedStyle(content).getPropertyValue("--tm-save-clearance"),
      expectedClearance: `${Math.ceil(save.getBoundingClientRect().height + (parseFloat(getComputedStyle(content).paddingBottom) || 0))}px`,
      ancestors,
      items: [...foot.querySelectorAll(".tmb-btn,.tmb-ind,.tmb-current-diary")].filter(element => !element.closest(".tmb-current-diary") || element.matches(".tmb-current-diary")).map(element => ({ ...rect(element), text: element.textContent, button: element.matches(".tmb-btn"), overflowX: element.scrollWidth - element.clientWidth })).filter(item => item.width && item.height),
      buttons: [...foot.querySelectorAll("button:not(:disabled)")].map(element => {
        const bounds = rect(element)
        const atCenter = document.elementFromPoint(bounds.left + bounds.width / 2, bounds.top + bounds.height / 2)
        return { ...bounds, text: element.textContent, hit: element === atCenter || element.contains(atCenter) }
      }),
    }
  })
}

async function compactAndFooterChecks(browser, origin, output) {
  const checks = []
  async function check(name, operation) {
    try { const detail = await operation(); checks.push({ name, passed: true, detail }); console.log(`PASS ${name}`) }
    catch (error) { checks.push({ name, passed: false, error: error.stack }); console.log(`FAIL ${name}: ${error.message}`) }
  }
  for (const locale of ["zh-CN", "zh-TW", "en", "ja", "ko", "es", "pt", "ru"]) {
    const loaded = await loadPage(browser, origin, { locale, wallpaper: "none", tab: "settings" }, 390, 600, "light")
    try {
      await check(`compact-savebar-clickable-${locale}-390x600`, async () => {
        await selectSettings(loaded.frame, "general")
        await loaded.frame.locator("select").first().selectOption("Asia/Tokyo")
        await loaded.frame.locator(".tm-content").evaluate(element => { element.scrollTop = element.scrollHeight })
        const geometry = await loaded.frame.locator(".tm-save button").evaluate(element => {
          const rect = element.getBoundingClientRect()
          const atCenter = document.elementFromPoint(rect.left + rect.width / 2, rect.top + rect.height / 2)
          return { text: element.textContent, left: rect.left, right: rect.right, top: rect.top, bottom: rect.bottom, width: rect.width, height: rect.height, scrollWidth: element.scrollWidth, clientWidth: element.clientWidth, hit: element === atCenter || element.contains(atCenter) }
        })
        assert(geometry.left >= 0 && geometry.right <= 390 && geometry.top >= 0 && geometry.bottom <= 600, JSON.stringify(geometry))
        assert(geometry.hit, `Save button is covered: ${JSON.stringify(geometry)}`)
        assert(geometry.scrollWidth <= geometry.clientWidth + 1, `Save text is clipped: ${JSON.stringify(geometry)}`)
        const before = await loaded.page.evaluate(() => window.qaMessages.length)
        await loaded.frame.locator(".tm-save button").click()
        await loaded.page.waitForTimeout(120)
        const calls = await loaded.page.evaluate(offset => window.qaMessages.slice(offset).filter(message => message.method === "call" && message.payload.actionId === "update_settings"), before)
        assert.equal(calls.length, 1)
        assert.deepEqual(calls[0].payload.args, { timezone: "Asia/Tokyo" })
        await loaded.page.screenshot({ path: join(output, `390x600-${locale}-savebar.png`), animations: "disabled" })
        return geometry
      })
      await check(`journal-navigation-labels-fit-${locale}-390x600`, async () => {
        const text = await loaded.page.evaluate(language => window.qaFixture.context.i18n.messages[language], locale)
        await loaded.frame.locator(".tm-tab").nth(TABS.indexOf("diary")).click()
        await loaded.frame.getByRole("button", { name: text["panel.journal.tabJournal"], exact: true }).click()
        await loaded.frame.locator(".tmb-spine:not(.tmb-spine--file)").first().click()
        await loaded.frame.waitForSelector(".tmb-current-diary")
        const geometry = await inspectFooterGeometry(loaded.frame)
        for (const item of geometry.items) {
          assert(item.left >= geometry.foot.left - 1 && item.right <= geometry.foot.right + 1, JSON.stringify(item))
          assert(item.top >= geometry.foot.top - 1 && item.bottom <= geometry.foot.bottom + 1, JSON.stringify(item))
          if (item.button) assert(item.overflowX <= 1, `Navigation text is clipped: ${JSON.stringify(item)}`)
        }
        for (let i = 0; i < geometry.items.length; i += 1) for (let j = i + 1; j < geometry.items.length; j += 1) {
          const a = geometry.items[i]
          const b = geometry.items[j]
          assert(Math.min(a.right, b.right) - Math.max(a.left, b.left) <= 1 || Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top) <= 1, JSON.stringify([a, b]))
        }
        for (const button of geometry.buttons) assert(button.hit, JSON.stringify(button))
        const navigation = geometry.items.slice(1)
        const centers = navigation.map(item => (item.top + item.bottom) / 2)
        assert(Math.max(...centers) - Math.min(...centers) <= 1, `Navigation wrapped: ${JSON.stringify(navigation)}`)
        assert.equal(await loaded.frame.locator(".tmb-foot .tmb-ind").innerText(), text["panel.journal.diaryNo"].replace("{n}", "1"))
        assert.equal(await loaded.frame.locator(".tmb-foot .tmb-btn").last().innerText(), text["panel.journal.nextDiary"])
        await loaded.page.screenshot({ path: join(output, `390x600-${locale}-journal-navigation.png`), animations: "disabled" })
        return geometry
      })
      await check(`review-seal-label-fits-${locale}-390x600`, async () => {
        await loaded.frame.locator(".tm-tab").nth(TABS.indexOf("diary")).click()
        const label = await loaded.page.evaluate(language => window.qaFixture.context.i18n.messages[language]["panel.review.tabReview"], locale)
        await loaded.frame.getByRole("button", { name: label, exact: true }).click()
        await loaded.frame.locator(".tmb-spine--file").first().click()
        await loaded.frame.waitForSelector(".tmb-seal")
        const geometry = await loaded.frame.locator(".tmb-seal").evaluate(element => {
          const range = document.createRange()
          range.selectNodeContents(element)
          const box = element.getBoundingClientRect()
          return {
            text: element.textContent,
            box: { left: box.left, right: box.right, top: box.top, bottom: box.bottom },
            fragments: [...range.getClientRects()].map(rect => ({ left: rect.left, right: rect.right, top: rect.top, bottom: rect.bottom })),
            overflow: { x: element.scrollWidth - element.clientWidth, y: element.scrollHeight - element.clientHeight },
          }
        })
        assert(geometry.fragments.length, "Seal label is blank")
        assert(geometry.overflow.x <= 1 && geometry.overflow.y <= 1, JSON.stringify(geometry))
        for (const rect of geometry.fragments) {
          assert(rect.left >= geometry.box.left - 1 && rect.right <= geometry.box.right + 1 && rect.top >= geometry.box.top - 1 && rect.bottom <= geometry.box.bottom + 1, JSON.stringify(geometry))
        }
        const footer = await inspectFooterGeometry(loaded.frame)
        for (const item of footer.items) {
          assert(item.left >= footer.foot.left - 1 && item.right <= footer.foot.right + 1, JSON.stringify(item))
          if (item.button) assert(item.overflowX <= 1, `Review navigation text is clipped: ${JSON.stringify(item)}`)
        }
        const centers = footer.items.slice(1).map(item => (item.top + item.bottom) / 2)
        assert(Math.max(...centers) - Math.min(...centers) <= 1, `Review navigation wrapped: ${JSON.stringify(footer.items)}`)
        assert.equal(await loaded.frame.locator(".tmb-current-title").innerText(), await loaded.frame.locator(".tmb-reading-title").innerText())
        const composedAt = await loaded.page.evaluate(() => window.qaFixture.review[0].ts.replace("T", " ").slice(0, 16))
        assert.equal(await loaded.frame.locator(".tmb-foot .tmb-ind").innerText(), composedAt)
        await loaded.frame.locator(".tmb-seal").evaluate(element => element.scrollIntoView({ block: "center" }))
        await loaded.page.screenshot({ path: join(output, `390x600-${locale}-review-seal.png`), animations: "disabled" })
        return geometry
      })
    } finally { await loaded.context.close() }
  }
  for (const width of [1280, 390]) for (const height of [900, 600]) for (const theme of ["light", "dark"]) {
    const loaded = await loadPage(browser, origin, { locale: "en", wallpaper: theme === "dark" ? "dark" : "none", tab: "diary" }, width, height, theme)
    try {
      for (const kind of ["journal", "review"]) {
        await check(`${kind}-footer-clear-of-savebar-${width}x${height}-${theme}`, async () => {
          await loaded.frame.getByRole("button", { name: kind === "journal" ? "Personal Journal" : "My Diary", exact: true }).click()
          await loaded.frame.locator(kind === "journal" ? ".tmb-spine:not(.tmb-spine--file)" : ".tmb-spine--file").first().click()
          await loaded.frame.waitForSelector(".tmb-foot")
          await loaded.frame.waitForTimeout(300)
          const measurements = []
          for (const scroll of ["start", "middle", "end"]) {
            await loaded.frame.locator(".tm-content").evaluate((element, position) => {
              if (position === "start") element.scrollTop = 0
              else {
                const book = element.querySelector(".tmb-page").getBoundingClientRect()
                const content = element.getBoundingClientRect()
                const clearance = parseFloat(getComputedStyle(element).getPropertyValue("--tm-save-clearance")) || 0
                const delta = Math.max(0, book.bottom - (content.bottom - clearance))
                element.scrollTop += position === "middle" ? delta / 2 : delta
              }
            }, scroll)
            await loaded.frame.waitForTimeout(60)
            const geometry = await inspectFooterGeometry(loaded.frame)
            const overlapX = Math.min(geometry.foot.right, geometry.save.right) - Math.max(geometry.foot.left, geometry.save.left)
            const overlapY = Math.min(geometry.foot.bottom, geometry.save.bottom) - Math.max(geometry.foot.top, geometry.save.top)
            assert(overlapX <= 0 || overlapY <= 1, `Footer/savebar overlap at ${scroll}: ${JSON.stringify(geometry)}`)
            assert(geometry.foot.top >= geometry.content.top - 1 && geometry.foot.bottom <= height + 1, `Footer is outside visible content: ${JSON.stringify(geometry)}`)
            const card = geometry.ancestors.find(item => item.className.includes("neko-card") && item.className.includes("tmb-card"))
            assert(card && card.overflowX === "visible" && card.overflowY === "visible", `Book card retains a scroll container: ${JSON.stringify(geometry.ancestors)}`)
            for (const item of geometry.items) {
              assert(item.left >= geometry.content.left - 1 && item.right <= width + 1, `Footer item is outside horizontal bounds: ${JSON.stringify(item)}`)
              if (item.button) assert(item.overflowX <= 1, `Footer text is clipped: ${JSON.stringify(item)}`)
            }
            for (let i = 0; i < geometry.items.length; i += 1) for (let j = i + 1; j < geometry.items.length; j += 1) {
              const a = geometry.items[i]
              const b = geometry.items[j]
              const x = Math.min(a.right, b.right) - Math.max(a.left, b.left)
              const y = Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top)
              assert(x <= 1 || y <= 1, `Footer items overlap at ${scroll}: ${JSON.stringify([a, b])}`)
            }
            for (const button of geometry.buttons) assert(button.hit, `Footer control is covered at ${scroll}: ${JSON.stringify(button)}`)
            const navigation = geometry.items.slice(1)
            const centers = navigation.map(item => (item.top + item.bottom) / 2)
            assert(Math.max(...centers) - Math.min(...centers) <= 1, `Navigation wrapped at ${scroll}: ${JSON.stringify(navigation)}`)
            measurements.push({ scroll, ...geometry })
          }
          await loaded.page.screenshot({ path: join(output, `${width}x${height}-${theme}-${kind}-footer.png`), animations: "disabled" })
          await loaded.frame.locator(".tmb-foot .tmb-btn").first().click()
          await loaded.frame.waitForSelector(kind === "journal" ? ".tmb-spine:not(.tmb-spine--file)" : ".tmb-spine--file")
          assert.deepEqual(loaded.errors, [])
          return measurements
        })
      }
    } finally { await loaded.context.close() }
  }
  const refreshed = await loadPage(browser, origin, { locale: "en", wallpaper: "none", tab: "diary" }, 390, 600, "light")
  try {
    await check("refresh-and-tab-roundtrip-preserve-footer-clearance", async () => {
      const openJournal = async () => {
        await refreshed.frame.getByRole("button", { name: "Personal Journal", exact: true }).click()
        await refreshed.frame.locator(".tmb-spine:not(.tmb-spine--file)").first().click()
        await refreshed.frame.waitForTimeout(300)
      }
      const assertVisible = async () => {
        const geometry = await inspectFooterGeometry(refreshed.frame)
        assert.equal(geometry.clearance, geometry.expectedClearance)
        assert(geometry.foot.bottom <= geometry.save.top + 1, JSON.stringify(geometry))
        for (const button of geometry.buttons) assert(button.hit, `Footer covered after refresh/tab change: ${JSON.stringify(button)}`)
        return geometry
      }
      await openJournal()
      const before = await assertVisible()
      const refreshBefore = await refreshed.page.evaluate(() => window.qaRefreshes)
      await refreshed.page.waitForFunction(value => window.qaRefreshes > value, refreshBefore, { timeout: 6500 })
      await refreshed.frame.waitForTimeout(150)
      const afterRefresh = await assertVisible()
      await refreshed.frame.locator(".tm-tab").nth(TABS.indexOf("calendar")).click()
      await refreshed.frame.waitForTimeout(250)
      assert.equal(await refreshed.frame.locator(".tm-save").count(), 0)
      assert.equal(await refreshed.frame.locator(".tm-content").evaluate(element => element.style.getPropertyValue("--tm-save-clearance")), "")
      await refreshed.frame.locator(".tm-tab").nth(TABS.indexOf("cycle")).click()
      await refreshed.frame.waitForTimeout(150)
      const cycleClearance = await refreshed.frame.locator(".tm-content").evaluate(element => {
        const bar = element.querySelector(".tm-save")
        return {
          actual: element.style.getPropertyValue("--tm-save-clearance"),
          expected: `${Math.ceil(bar.getBoundingClientRect().height + (parseFloat(getComputedStyle(element).paddingBottom) || 0))}px`,
        }
      })
      assert.equal(cycleClearance.actual, cycleClearance.expected)
      await refreshed.frame.locator(".tm-tab").nth(TABS.indexOf("diary")).click()
      await openJournal()
      const afterRoundtrip = await assertVisible()
      assert.deepEqual(refreshed.errors, [])
      await refreshed.page.screenshot({ path: join(output, "390x600-journal-after-refresh-and-tabs.png"), animations: "disabled" })
      return { before, afterRefresh, cycleClearance, afterRoundtrip }
    })
  } finally { await refreshed.context.close() }
  return checks
}

async function detailScreenshots(browser, origin, output) {
  const results = []
  const widths = option("widths", "1280,390").split(",").map(Number)
  for (const width of widths) for (const theme of ["light", "dark"]) {
    const { context, page, frame, errors } = await loadPage(browser, origin, { locale: "en", wallpaper: theme === "dark" ? "dark" : "none", tab: "diary" }, width, 900, theme)
    const capture = async name => {
      const id = `${width}-${theme}-${name}`
      const path = join(output, `${id}.png`)
      await page.screenshot({ path, animations: "disabled" })
      results.push({ id, path, layout: await inspectLayout(frame), errors: [...errors], diagnostics: await page.evaluate(() => window.qaDiagnostics.filter(message => message.type === "neko-hosted-surface-error")) })
    }
    try {
      await frame.getByRole("button", { name: "Personal Journal", exact: true }).click()
      await frame.waitForTimeout(160)
      await capture("journal-shelf")
      await frame.locator(".tmb-spine:not(.tmb-spine--file)").first().click()
      await capture("journal-reading")
      await frame.getByRole("button", { name: "My Diary", exact: true }).click()
      await frame.waitForTimeout(160)
      await capture("review-shelf")
      await frame.locator(".tmb-spine--file").first().click()
      await capture("review-reading")
      await frame.locator(".tm-tab").nth(TABS.indexOf("features")).click()
      await frame.locator(".tm-ci-open").first().click()
      await frame.waitForSelector(".tm-ci")
      await capture("capability-introduction")
    } finally { await context.close() }
    const empty = await loadPage(browser, origin, { locale: "en", wallpaper: "none", empty: "true", tab: "diary" }, width, 900, theme)
    try {
      const id = `${width}-${theme}-empty-diary`
      const path = join(output, `${id}.png`)
      await empty.page.screenshot({ path, animations: "disabled" })
      results.push({ id, path, layout: await inspectLayout(empty.frame), errors: [...empty.errors], diagnostics: [] })
    } finally { await empty.context.close() }
  }
  return results
}

async function calendarDiagnostics(browser, origin, output) {
  const results = []
  const widths = option("widths", "1280,390").split(",").map(Number)
  for (const width of widths) for (const locale of ["zh-CN", "en"]) {
    const { context, page, frame, errors } = await loadPage(browser, origin, { locale, wallpaper: "none", tab: "calendar" }, width, 900, "light")
    try {
      const diagnostics = await frame.evaluate(() => {
        const content = document.querySelector(".tm-content")
        const tips = [...content.querySelectorAll(".neko-tooltip-content")]
        const measure = () => ({ clientWidth: content.clientWidth, scrollWidth: content.scrollWidth, overflow: content.scrollWidth - content.clientWidth })
        const before = measure()
        const tooltipBounds = tips.map(element => {
          const rect = element.getBoundingClientRect()
          const style = getComputedStyle(element)
          return { text: element.textContent, left: rect.left, right: rect.right, width: rect.width, opacity: style.opacity, visibility: style.visibility, display: style.display }
        })
        const oldStyles = tips.map(element => element.getAttribute("style"))
        tips.forEach(element => { element.style.display = "none" })
        const withoutTooltip = measure()
        tips.forEach((element, index) => {
          if (oldStyles[index] === null) element.removeAttribute("style")
          else element.setAttribute("style", oldStyles[index])
        })
        return { before, withoutTooltip, tooltipBounds }
      })
      await frame.locator(".tm-card-header .neko-tooltip").hover()
      await frame.waitForTimeout(180)
      const hoveredTooltip = await frame.locator(".tm-card-header .neko-tooltip-content").evaluate(element => {
        const rect = element.getBoundingClientRect()
        const contentRect = document.querySelector(".tm-content").getBoundingClientRect()
        return { left: rect.left, right: rect.right, visibleRight: contentRect.right, clippedRight: Math.max(0, rect.right - contentRect.right), opacity: getComputedStyle(element).opacity }
      })
      const path = join(output, `${width}-${locale}-calendar-tooltip-hover.png`)
      await page.screenshot({ path, animations: "disabled" })
      results.push({ width, locale, path, errors: [...errors], ...diagnostics, hoveredTooltip })
      console.log(`CALENDAR ${width}/${locale}: overflow=${diagnostics.before.overflow}, without-tooltip=${diagnostics.withoutTooltip.overflow}, hover-clip=${hoveredTooltip.clippedRight}`)
    } finally { await context.close() }
  }
  return results
}

async function copyChecks(browser, sources, origin, output) {
  const screenshots = []
  const checks = []
  async function check(name, operation) {
    try { await operation(); checks.push({ name, passed: true }); console.log(`PASS ${name}`) }
    catch (error) { checks.push({ name, passed: false, error: error.stack }); console.log(`FAIL ${name}: ${error.message}`) }
  }
  async function capture(loaded, id, save = true) {
    const layout = await inspectLayout(loaded.frame)
    assert(layout.documentOverflow <= 2, `${id}: document overflow ${layout.documentOverflow}`)
    assert(layout.contentOverflow <= 2, `${id}: content overflow ${layout.contentOverflow}`)
    assert.deepEqual(layout.issues, [], `${id}: clipped copy`)
    assert.deepEqual(loaded.errors, [], `${id}: runtime errors`)
    if (!save) return
    const path = join(output, `${id}.png`)
    await loaded.page.screenshot({ path, animations: "disabled" })
    screenshots.push({ id, path, layout, errors: [...loaded.errors], diagnostics: await loaded.page.evaluate(() => window.qaDiagnostics.filter(message => message.type === "neko-hosted-surface-error")) })
  }
  async function refreshState(loaded, patch) {
    const payload = await loaded.page.evaluate(next => {
      Object.assign(window.qaFixture.context.state, next)
      return window.qaFixture.context
    }, patch)
    await loaded.frame.evaluate(next => window.__NekoRefreshHostedPayload(next), payload)
    await loaded.frame.waitForTimeout(70)
  }
  const calls = (page, action) => page.evaluate(id => window.qaMessages.filter(message => message.method === "call" && message.payload.actionId === id).length, action)
  for (const locale of option("copy-locales", Object.keys(sources.messages).join(",")).split(",")) {
    const text = sources.messages[locale]
    for (const width of [1280, 390]) for (const theme of ["light", "dark"]) {
      const id = `copy-${locale}-${width}-${theme}`
      const save = (width === 1280 && theme === "light") || (width === 390 && theme === "dark")
      const loaded = await loadPage(browser, origin, { locale, wizard: "true", empty: "true", wallpaper: "none" }, width, width === 390 ? 600 : 900, theme)
      try {
        await check(`${id}-six-step-guide`, async () => {
          for (let step = 0; step < 6; step += 1) {
            await loaded.frame.waitForSelector(".tm-ob-step")
            await capture(loaded, `${id}-guide-${step + 1}`, save)
            if (step < 5) await loaded.frame.getByRole("button", { name: text["onboarding.wizard.next"], exact: true }).click()
          }
          assert((await loaded.frame.locator(".tm-ob-step").innerText()).includes(text["onboarding.done.lead"]))
          await loaded.frame.getByRole("button", { name: text["onboarding.wizard.finish"], exact: true }).click()
          await loaded.frame.waitForSelector(".neko-modal", { state: "detached" })
          assert.equal(await calls(loaded.page, "set_onboarding"), 1)
          assert.equal(await calls(loaded.page, "toggle"), 0)
          assert.equal(await calls(loaded.page, "update_settings"), 0)
        })
        await check(`${id}-all-real-introductions`, async () => {
          await loaded.frame.locator(".tm-tab").nth(TABS.indexOf("features")).click()
          for (const [index, cap] of Object.keys(sources.intros).entries()) {
            await loaded.frame.locator(".tm-ci-open").nth(index).click()
            await loaded.frame.waitForSelector(".tm-ci-purpose")
            assert.equal(await loaded.frame.locator(".tm-ci-purpose").innerText(), text[`panel.capintro.${cap}.purpose`])
            assert.equal(await loaded.frame.locator(".tm-ci-node").count(), sources.intros[cap].flow.length)
            await capture(loaded, `${id}-intro-${cap}`, save)
            await loaded.frame.locator(".tm-ci-back").click()
          }
        })
      } finally { await loaded.context.close() }
    }
    const loaded = await loadPage(browser, origin, { locale, empty: "true", wallpaper: "none", tab: "diary" }, 390, 600, "light")
    try {
      await check(`copy-${locale}-diary-empty-and-invite`, async () => {
        assert((await loaded.frame.locator(".tm-content").innerText()).includes(text["panel.diary.emptyTitle"]))
        await loaded.frame.getByRole("button", { name: text["panel.journal.tabJournal"], exact: true }).click()
        await loaded.frame.waitForTimeout(120)
        assert((await loaded.frame.locator(".tm-content").innerText()).includes(text["panel.journal.emptyTitle"]))
        await refreshState(loaded, { journal_invite_pending: true })
        assert((await loaded.frame.locator(".tm-content").innerText()).includes(text["panel.journal.invitePending"]))
        await capture(loaded, `copy-${locale}-journal-invite-pending`)
        const outcomes = [
          [{ invited: true, mode: "respond" }, "panel.journal.invitedRespond"],
          [{ invited: true, mode: "quiet" }, "panel.journal.invitedQuiet"],
          [{ invited: false, mode: "failed" }, "panel.journal.inviteFailed"],
          [{ invited: false, mode: "off" }, "panel.journal.inviteOff"],
        ]
        for (const [result, key] of outcomes) {
          await loaded.page.evaluate(value => { window.qaResults.invite_journal = value }, result)
          await loaded.frame.getByRole("button", { name: text["panel.journal.invite"], exact: true }).click()
          await loaded.frame.waitForTimeout(90)
          assert((await loaded.frame.locator(".neko-toast").allTextContents()).some(value => value.includes(text[key])), key)
        }
      })
      await check(`copy-${locale}-review-progress-and-results`, async () => {
        await loaded.frame.getByRole("button", { name: text["panel.review.tabReview"], exact: true }).click()
        await loaded.frame.waitForTimeout(100)
        assert((await loaded.frame.locator(".tm-content").innerText()).includes(text["panel.review.emptyTitle"]))
        await capture(loaded, `copy-${locale}-review-empty`)
        await refreshState(loaded, { review_brief: { enabled: true, entries: 0, writing: true } })
        assert((await loaded.frame.locator(".tm-content").innerText()).includes(text["panel.review.writingHint"]))
        assert(await loaded.frame.getByRole("button", { name: text["panel.review.writing"], exact: true }).isDisabled())
        await capture(loaded, `copy-${locale}-review-writing`)
        await refreshState(loaded, { review_brief: { enabled: true, entries: 0, writing: false, last_result: { ts: "qa-failed", written: false } } })
        assert((await loaded.frame.locator(".tm-content").innerText()).includes(text["panel.review.writeFailed"]))
        const outcomes = [
          [{ accepted: true }, "panel.review.accepted"],
          [{ reason: "already_writing" }, "panel.review.alreadyWriting"],
          [{ reason: "not_enough_material", turns: 3, min_turns: 10 }, "panel.review.notEnoughMaterial"],
        ]
        for (const [result, key] of outcomes) {
          await loaded.page.evaluate(value => { window.qaResults.write_review_now = value }, result)
          await loaded.frame.getByRole("button", { name: text["panel.review.writeNow"], exact: true }).click()
          await loaded.frame.waitForTimeout(90)
          const expected = text[key].replace("{turns}", "3").replace("{min_turns}", "10")
          assert((await loaded.frame.locator(".neko-toast").allTextContents()).some(value => value.includes(expected)), key)
        }
        const entry = makeFixture(sources.messages, { locale, intros: sources.intros }).review[0]
        await loaded.page.evaluate(value => { window.qaFixture.review = [value] }, entry)
        await refreshState(loaded, { review_brief: { enabled: true, entries: 1, writing: false } })
        await loaded.frame.waitForSelector(".tmb-meter")
        assert(!/\{(?:n|total|d)\}/.test(await loaded.frame.locator(".tmb-meter").innerText()))
        await capture(loaded, `copy-${locale}-review-progress`)
      })
      await check(`copy-${locale}-toggle-confirm-and-cancel`, async () => {
        await loaded.frame.waitForFunction(() => document.querySelectorAll(".neko-toast").length === 0)
        const before = await calls(loaded.page, "toggle")
        await loaded.frame.locator(".tm-statusbar .tm-sw-track").click()
        await loaded.frame.waitForSelector(".neko-modal")
        assert((await loaded.frame.locator(".neko-modal").innerText()).includes(text["actions.toggle.confirm"]))
        await capture(loaded, `copy-${locale}-toggle-confirm`)
        await loaded.frame.getByRole("button", { name: text["panel.cancel"], exact: true }).last().click()
        assert.equal(await calls(loaded.page, "toggle"), before)
        await loaded.frame.locator(".tm-statusbar .tm-sw-track").click()
        await loaded.frame.getByRole("button", { name: text["panel.confirm"], exact: true }).last().click()
        await loaded.frame.waitForSelector(".neko-modal", { state: "detached" })
        assert.equal(await calls(loaded.page, "toggle"), before + 1)
      })
      await check(`copy-${locale}-dependency-placeholder`, async () => {
        await loaded.frame.locator(".tm-tab").nth(TABS.indexOf("features")).click()
        const caps = await loaded.page.evaluate(() => {
          const caps = window.qaFixture.context.state.capabilities
          const cap = caps.capabilities.find(value => value.id === "activity_sense")
          cap.enabled = false
          cap.source = "upstream_off"
          cap.blocked_by = ["whisper"]
          return caps
        })
        await refreshState(loaded, { capabilities: caps })
        const body = await loaded.frame.locator(".tm-content").innerText()
        assert(body.includes(text["panel.features.hint.upstream"].replace("{deps}", text["panel.features.cap.whisper.label"])))
        assert(!body.includes("{deps}"))
        await capture(loaded, `copy-${locale}-features-dependency`)
      })
      await check(`copy-${locale}-destructive-confirmation-cancel`, async () => {
        await loaded.frame.locator(".tm-tab").nth(TABS.indexOf("settings")).click()
        await selectSettings(loaded.frame, "data")
        const before = await calls(loaded.page, "reset_all")
        await loaded.frame.getByRole("button", { name: text["actions.reset.label"], exact: true }).click()
        await loaded.frame.waitForSelector(".neko-modal")
        assert((await loaded.frame.locator(".neko-modal").innerText()).includes(text["actions.reset.confirm"]))
        await capture(loaded, `copy-${locale}-reset-confirm`)
        await loaded.frame.getByRole("button", { name: text["panel.cancel"], exact: true }).last().click()
        assert.equal(await calls(loaded.page, "reset_all"), before)
      })
    } finally { await loaded.context.close() }
    const skipped = await loadPage(browser, origin, { locale, wizard: "true", wallpaper: "none" }, 390, 600, "light")
    try {
      await check(`copy-${locale}-guide-can-be-skipped`, async () => {
        await skipped.frame.getByRole("button", { name: text["onboarding.wizard.skip"], exact: true }).click()
        await skipped.frame.waitForSelector(".neko-modal", { state: "detached" })
        assert.equal(await calls(skipped.page, "set_onboarding"), 1)
        const action = await skipped.page.evaluate(() => window.qaMessages.find(message => message.method === "call" && message.payload.actionId === "set_onboarding"))
        assert.equal(action.payload.args.action, "skip")
      })
    } finally { await skipped.context.close() }
  }
  return { screenshots, checks }
}

function isolatedTypecheck(pluginRoot, hostRoot) {
  const typescript = loadDependency("typescript")
  const scratch = mkdtempSync(join(tmpdir(), "neko-companion-hosted-typecheck-"))
  const frontend = join(scratch, "frontend/plugin-manager")
  const script = "frontend/plugin-manager/scripts/check-hosted-tsx.mjs"
  const files = [script, `${HOSTED}/hostedTsxModule.mjs`, `${HOSTED}/hostedTsxModule.d.mts`]
  try {
    for (const file of files) { mkdirSync(dirname(join(scratch, file)), { recursive: true }); cpSync(join(hostRoot, file), join(scratch, file)) }
    cpSync(join(hostRoot, "plugin/sdk/hosted-ui"), join(scratch, "plugin/sdk/hosted-ui"), { recursive: true })
    const copy = join(scratch, "plugin/plugins/forever_companion")
    mkdirSync(copy, { recursive: true })
    cpSync(join(pluginRoot, "plugin.toml"), join(copy, "plugin.toml"))
    cpSync(join(pluginRoot, "ui"), join(copy, "ui"), { recursive: true })
    symlinkSync(typescript.directory, join(frontend, "node_modules"), "junction")
    const result = spawnSync(process.execPath, [join(scratch, script), "plugin/plugins/forever_companion"], { cwd: frontend, encoding: "utf8" })
    return { passed: result.status === 0, status: result.status, stdout: result.stdout, stderr: result.stderr, hostUntouched: true }
  } finally {
    assert(scratch.startsWith(resolve(tmpdir()) + "\\" ) || scratch.startsWith(resolve(tmpdir()) + "/"), "Refusing unsafe scratch cleanup")
    rmSync(scratch, { recursive: true, force: true })
  }
}

async function main() {
  const pluginRoot = resolve(option("plugin-root", DEFAULT_PLUGIN))
  const hostRoot = resolve(option("host-root", DEFAULT_HOST))
  const output = resolve(option("out", join(tmpdir(), `neko-companion-ui-qa-${Date.now()}`)))
  mkdirSync(output, { recursive: true })
  const sources = await loadSources(pluginRoot, hostRoot)
  const report = { pluginRoot, hostRoot, output, sourceHash: sources.sourceHash, dependencies: sources.dependencies, dependencyBytes: sources.dependencyBytes, screenshots: [], interactions: [], calendarDiagnostics: [], typecheck: null }
  if (process.argv.includes("--typecheck")) {
    report.typecheck = isolatedTypecheck(pluginRoot, hostRoot)
    console.log(report.typecheck.stdout || report.typecheck.stderr)
  }
  if (!process.argv.includes("--typecheck-only")) {
    const { chromium } = loadDependency("playwright").value
    let origin
    const server = createServer((request, response) => {
      const url = new URL(request.url, origin)
      if (sources.qaPlaybackPort && url.pathname.startsWith("/plugin/forever_companion/ui/")) {
        const entry = { path: url.pathname, range: request.headers.range, bytes: 0, status: null }
        sources.qaDirectRequests.push(entry)
        const proxy = httpRequest({
          hostname: "127.0.0.1", port: sources.qaPlaybackPort, path: url.pathname,
          method: request.method, headers: request.headers,
        }, upstream => {
          entry.status = upstream.statusCode
          upstream.on("data", chunk => { entry.bytes += chunk.length })
          response.writeHead(upstream.statusCode, upstream.headers)
          upstream.pipe(response)
        })
        proxy.on("error", () => { if (!response.headersSent) response.writeHead(502); response.end() })
        response.on("close", () => proxy.destroy())
        request.pipe(proxy)
        return
      }
      const parameters = Object.fromEntries(url.searchParams)
      response.writeHead(200, { "content-type": "text/html; charset=utf-8", "cache-control": "no-store" })
      response.end(parentDocument(sources, parameters, origin))
    })
    await new Promise(resolvePromise => server.listen(0, "127.0.0.1", resolvePromise))
    origin = `http://127.0.0.1:${server.address().port}`
    if (process.argv.includes("--serve-only")) {
      console.log(`ISOLATED_PREVIEW ${origin}`)
      await new Promise(() => {})
    }
    const launch = { headless: true }
    const executable = option("browser-executable", process.env.NEKO_UI_BROWSER)
    if (executable) launch.executablePath = executable
    else if (existsSync("C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe")) launch.channel = "msedge"
    let browser
    try {
      browser = await chromium.launch(launch)
      if (!process.argv.includes("--interactions-only") && !process.argv.includes("--diagnostics-only") && !process.argv.includes("--compact-only") && !process.argv.includes("--theme-only") && !process.argv.includes("--copy-only") && !process.argv.includes("--demo-only") && !process.argv.includes("--intro-only") && !process.argv.includes("--appearance-only") && !process.argv.includes("--video-only") && !process.argv.includes("--direct-only") && !process.argv.includes("--heatmap-only") && !process.argv.includes("--diary-only") && !process.argv.includes("--emotion-only") && !process.argv.includes("--mood-only") && !process.argv.includes("--settings-only") && !process.argv.includes("--backup-only")) report.screenshots = await screenshotMatrix(browser, sources, origin, output)
      if (process.argv.includes("--backup") || process.argv.includes("--backup-only")) {
        const backup = await backupChecks(browser, sources, origin, output, { loadPage, inspectLayout })
        report.screenshots.push(...backup.screenshots)
        report.interactions.push(...backup.checks)
      }
      if (process.argv.includes("--settings") || process.argv.includes("--settings-only")) {
        const settings = await settingsChecks(browser, sources, origin, output, { loadPage, inspectLayout, recordFixture })
        report.screenshots.push(...settings.screenshots)
        report.interactions.push(...settings.checks)
      }
      if (process.argv.includes("--details")) report.screenshots.push(...await detailScreenshots(browser, origin, output))
      if (process.argv.includes("--calendar-diagnostics")) report.calendarDiagnostics = await calendarDiagnostics(browser, origin, output)
      if (process.argv.includes("--interactions") || process.argv.includes("--interactions-only")) {
        report.interactions = await interactionChecks(browser, origin, output)
        if (process.argv.includes("--gallery")) report.interactions.push(...await galleryChecks(browser, origin, output))
      }
      if (process.argv.includes("--compact") || process.argv.includes("--compact-only")) report.interactions.push(...await compactAndFooterChecks(browser, origin, output))
      if (process.argv.includes("--theme") || process.argv.includes("--theme-only")) report.interactions.push(...await themeChecks(browser, origin, output))
      if (process.argv.includes("--copy") || process.argv.includes("--copy-only")) {
        const copy = await copyChecks(browser, sources, origin, output)
        report.screenshots.push(...copy.screenshots)
        report.interactions.push(...copy.checks)
      }
      if (process.argv.includes("--demo") || process.argv.includes("--demo-only")) {
        const demo = await demoChecks(browser, sources, origin, output, { loadPage, inspectLayout })
        report.screenshots.push(...demo.screenshots)
        report.interactions.push(...demo.checks)
      }
      if (process.argv.includes("--intro") || process.argv.includes("--intro-only")) {
        const intro = await introChecks(browser, sources, origin, output, { loadPage, inspectLayout })
        report.screenshots.push(...intro.screenshots)
        report.interactions.push(...intro.checks)
      }
      if (process.argv.includes("--appearance") || process.argv.includes("--appearance-only")) {
        const appearance = await appearanceChecks(browser, sources, origin, output, { loadPage, inspectLayout })
        report.screenshots.push(...appearance.screenshots)
        report.interactions.push(...appearance.checks)
      }
      if (process.argv.includes("--video") || process.argv.includes("--video-only")) {
        const video = await videoChecks(browser, sources, origin, output, { loadPage, inspectLayout, pluginRoot, loadDependency })
        report.screenshots.push(...video.screenshots)
        report.interactions.push(...video.checks)
        report.videoFixture = video.fixture
      }
      if (process.argv.includes("--direct") || process.argv.includes("--direct-only")) {
        const direct = await directChecks(browser, sources, origin, output, { loadPage, inspectLayout, pluginRoot, hostRoot })
        report.screenshots.push(...direct.screenshots)
        report.interactions.push(...direct.checks)
        report.directPlayback = direct.measurements
      }
      if (process.argv.includes("--heatmap") || process.argv.includes("--heatmap-only")) {
        const heatmap = await heatmapChecks(browser, sources, origin, output, { loadPage, inspectLayout })
        report.screenshots.push(...heatmap.screenshots)
        report.interactions.push(...heatmap.checks)
      }
      if (process.argv.includes("--diary") || process.argv.includes("--diary-only")) {
        const diary = await diaryChecks(browser, sources, origin, output, { loadPage, inspectLayout })
        report.screenshots.push(...diary.screenshots)
        report.interactions.push(...diary.checks)
      }
      if (process.argv.includes("--emotion") || process.argv.includes("--emotion-only")) {
        const emotion = await emotionIntervalChecks(browser, sources, origin, output)
        report.screenshots.push(...emotion.screenshots)
        report.interactions.push(...emotion.checks)
      }
      if (process.argv.includes("--mood") || process.argv.includes("--mood-only") || process.argv.includes("--emotion") || process.argv.includes("--emotion-only")) {
        const mood = await moodChecks(browser, sources, origin, output, { loadPage, inspectLayout })
        report.screenshots.push(...mood.screenshots)
        report.interactions.push(...mood.checks)
      }
    } finally {
      if (browser) await browser.close()
      await new Promise(resolvePromise => server.close(resolvePromise))
    }
  }
  const reportPath = join(output, "report.json")
  writeFileSync(reportPath, JSON.stringify(report, null, 2))
  const failures = report.interactions.filter(item => !item.passed).length + (report.typecheck && !report.typecheck.passed ? 1 : 0)
  const runtimeFailures = report.screenshots.filter(item => item.errors.length || item.diagnostics.length || item.layout.errorNodes.length).length
  const layoutFailures = report.screenshots.filter(item => item.layout.documentOverflow > 2 || item.layout.contentOverflow > 2 || item.layout.issues.length).length
  console.log(`REPORT ${reportPath}`)
  console.log(`SUMMARY ${report.screenshots.length} screenshots, ${report.interactions.length} interaction checks, ${failures} check failures, ${runtimeFailures} render failures, ${layoutFailures} layout failures`)
  process.exitCode = failures || runtimeFailures || layoutFailures ? 1 : 0
}

main().catch(error => { console.error(error.stack); process.exitCode = 1 })
