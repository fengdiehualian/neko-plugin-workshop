// Run from the N.E.K.O root with the plugin-manager development dependencies installed.
const fs = require("fs")
const path = require("path")
const Module = require("module")
const { createRequire } = Module
const root = path.resolve(__dirname, "..")
const repo = path.resolve(root, "../../..")
const requireFrontend = createRequire(path.join(repo, "frontend/plugin-manager/package.json"))
const esbuild = requireFrontend("esbuild")
const { chromium } = requireFrontend("playwright")

async function main() {
  const built = await esbuild.build({
    entryPoints: [path.join(repo, "frontend/plugin-manager/src/components/plugin/hosted/tsxRuntime.ts")],
    bundle: true, platform: "node", format: "cjs", write: false,
    plugins: [{ name: "raw", setup(build) {
      build.onResolve({ filter: /\?raw$/ }, args => ({
        path: path.resolve(args.resolveDir, args.path.slice(0, -4)), namespace: "raw",
      }))
      build.onLoad({ filter: /.*/, namespace: "raw" }, args => ({
        contents: fs.readFileSync(args.path, "utf8"), loader: "text",
      }))
    } }],
  })
  const runtime = new Module(__filename)
  runtime.filename = __filename
  runtime.paths = module.paths
  runtime._compile(built.outputFiles[0].text, __filename)
  global.window = { location: { origin: "http://localhost" } }
  const messages = Object.fromEntries(["en", "zh-CN"].map(locale => [
    locale, JSON.parse(fs.readFileSync(path.join(root, `i18n/${locale}.json`), "utf8")),
  ]))
  const base = {
    plugin: { id: "neko_sous", name: "NekoSous" },
    actions: [{ id: "connect", entry_id: "connect" }, { id: "disconnect", entry_id: "disconnect" },
      { id: "set_mode", entry_id: "set_mode" }],
    state: { game_state: "stopped", connection_state: "disconnected", desired: false,
      address: "ws://127.0.0.1:48930/neko" },
  }
  const browser = await chromium.launch({ headless: true, channel: process.env.NEKO_SOUS_BROWSER_CHANNEL || undefined })
  try {
    const errors = []
    for (const locale of ["zh-CN", "en"]) {
      const page = await browser.newPage({ viewport: { width: 960, height: 760 } })
      page.on("pageerror", error => errors.push(error.message))
      const context = { ...base, i18n: { locale, default_locale: "en", messages } }
      const html = runtime.exports.buildHostedTsxDocument({
        source: fs.readFileSync(path.join(root, "ui/panel.tsx"), "utf8"), pluginId: "neko_sous",
        surface: { id: "main", kind: "panel", entry: "ui/panel.tsx" }, context, locale,
      })
      await page.setContent(html)
      await page.evaluate(() => {
        window.NekoUiKit.api.refresh = async () => ({})
        window.NekoUiKit.api.call = async () => ({ result: {
          last_error: { id: "not-running", code: "game_not_running" },
        } })
      })
      if (!(await page.locator("body").innerText()).includes(messages[locale]["game.stopped"]))
        throw new Error("Missing localized game status")
      if (!(await page.getByRole("radio").first().isDisabled())) throw new Error("Offline mode switch enabled")
      await page.getByRole("button", { name: messages[locale]["actions.connect"], exact: true }).click()
      await page.getByRole("dialog").waitFor()
      if (!(await page.getByRole("dialog").innerText()).includes(messages[locale]["errors.game_not_running"]))
        throw new Error("Wrong error modal")
      await page.getByRole("button", { name: messages[locale]["actions.ok"], exact: true }).click()
      await page.evaluate(context => window.__NekoRefreshHostedPayload({
        ...context, state: { ...context.state, game_state: "running", connection_state: "connected",
          desired: true, session_id: "123456789", server_version: "0.1.0", mode: "companion", mode_supported: true },
      }), context)
      if (!(await page.locator("body").innerText()).includes(messages[locale]["connection.connected"]))
        throw new Error("Missing connected status")
      await page.evaluate(() => {
        window.NekoUiKit.api.call = async (action, args) => {
          if (action !== "set_mode" || args.mode !== "playmate") throw new Error("Wrong mode request")
          return new Promise(resolve => { window.__finishMode = result => { window.__modeConfirmed = true; resolve(result) } })
        }
        window.NekoUiKit.api.refresh = async () => {
          if (!window.__modeConfirmed) return
          const payload = window.__NEKO_PAYLOAD
          window.__NekoRefreshHostedPayload({ ...payload, state: { ...payload.state, mode: "playmate" } })
        }
      })
      const playmate = page.getByRole("radio", { name: new RegExp(messages[locale]["mode.playmate"]) })
      await playmate.click()
      if (await playmate.getAttribute("aria-checked") !== "false") throw new Error("Optimistic mode change")
      if (!(await playmate.isDisabled())) throw new Error("Mode switch not locked while awaiting confirmation")
      await page.evaluate(() => window.__finishMode({ result: { mode: "playmate" } }))
      await page.waitForFunction(() => document.querySelector('[role="radio"][aria-checked="true"]')?.textContent.includes(
        window.__NEKO_PAYLOAD.i18n.messages[window.__NEKO_PAYLOAD.locale]["mode.playmate"]))
      await page.evaluate(() => {
        const payload = window.__NEKO_PAYLOAD
        window.__NekoRefreshHostedPayload({ ...payload, state: { ...payload.state, connection_state: "reconnecting",
          mode: "companion", mode_supported: false, pending_mode: null } })
      })
      if (!(await playmate.isDisabled()) || await playmate.getAttribute("aria-checked") !== "false")
        throw new Error("Disconnect did not reset and disable mode switch")
      if (process.env.NEKO_SOUS_SCREENSHOT && locale === "zh-CN")
        await page.screenshot({ path: process.env.NEKO_SOUS_SCREENSHOT, fullPage: true })
      await page.setViewportSize({ width: 390, height: 780 })
      if (await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 2))
        throw new Error("Mobile horizontal overflow")
      await page.setViewportSize({ width: 960, height: 760 })
      console.log(`PASS Hosted TSX: ${locale}, action, error modal, status update, mobile layout`)
      await page.close()
    }
    if (errors.length) throw new Error(errors.join("\n"))
  } finally { await browser.close() }
}

main().catch(error => { console.error(error); process.exitCode = 1 })
