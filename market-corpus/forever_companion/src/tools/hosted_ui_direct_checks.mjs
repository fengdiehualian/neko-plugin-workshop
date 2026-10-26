import assert from "node:assert/strict"
import { spawn } from "node:child_process"
import { createHash } from "node:crypto"
import { copyFileSync, createReadStream, mkdtempSync, rmSync, statSync } from "node:fs"
import { tmpdir } from "node:os"
import { basename, dirname, join, resolve } from "node:path"
import { fileURLToPath } from "node:url"
import { recordFixture, largeFixture } from "./hosted_ui_video_checks.mjs"
import { selectMedia } from "./hosted_ui_settings_checks.mjs"

const VIDEO = "video.tm-bg-video"

async function startProbe(hostRoot, directory) {
  const child = spawn(join(hostRoot, ".venv/Scripts/python.exe"), [
    "-B", join(dirname(fileURLToPath(import.meta.url)), "playback_route_probe.py"),
    "--host", hostRoot, "--directory", directory,
  ], { windowsHide: true, stdio: ["ignore", "pipe", "pipe"], env: { ...process.env, PYTHONDONTWRITEBYTECODE: "1" } })
  let stderr = ""
  child.stderr.on("data", chunk => { stderr += chunk.toString() })
  try {
    const port = await new Promise((resolve, reject) => {
      let stdout = ""
      const timer = setTimeout(() => reject(new Error(`Playback probe startup timeout: ${stderr}`)), 10000)
      child.once("exit", code => { clearTimeout(timer); reject(new Error(`Playback probe exited ${code}: ${stderr}`)) })
      child.once("error", error => { clearTimeout(timer); reject(error) })
      child.stdout.on("data", chunk => {
        stdout += chunk.toString()
        if (stdout.includes("\n")) {
          clearTimeout(timer)
          try { resolve(JSON.parse(stdout.split("\n")[0]).port) } catch (error) { reject(error) }
        }
      })
    })
    return { child, port }
  } catch (error) { child.kill(); throw error }
}

async function destroyAndReopen(loaded) {
  const removed = loaded.page.waitForEvent("framedetached", { predicate: frame => frame === loaded.frame })
  await loaded.page.locator("#hosted").evaluate(old => {
    window.qaOldSrcdoc = old.srcdoc
    old.remove()
  })
  await removed
  assert.equal(loaded.page.frames().length, 1)
  const attached = loaded.page.waitForEvent("frameattached")
  await loaded.page.evaluate(() => {
    const next = document.createElement("iframe")
    next.id = "hosted"
    next.setAttribute("sandbox", "allow-scripts")
    document.body.appendChild(next)
    next.srcdoc = window.qaOldSrcdoc
  })
  loaded.frame = await attached
  await loaded.frame.waitForSelector(".tm-tabs")
}

async function assertDirectMoving(loaded) {
  await loaded.frame.waitForFunction(() => {
    const video = document.querySelector("video.tm-bg-video")
    return video && video.readyState >= 2 && !video.paused && Number(getComputedStyle(video).opacity) > 0.99
  }, null, { timeout: 12000 })
  const state = () => loaded.frame.locator(VIDEO).evaluate(video => ({
    time: video.currentTime, width: video.videoWidth, height: video.videoHeight,
    source: video.currentSrc, muted: video.muted, loop: video.loop,
  }))
  const first = await state()
  // HTTP video in an opaque iframe correctly taints a canvas. Read rendered
  // pixels through isolated browser screenshots instead of weakening CORS.
  const before = await loaded.page.screenshot({ animations: "disabled" })
  await loaded.frame.waitForTimeout(470)
  const after = await loaded.page.screenshot({ animations: "disabled" })
  const second = await state()
  assert.notEqual(first.time, second.time)
  assert.notEqual(createHash("sha256").update(before).digest("hex"), createHash("sha256").update(after).digest("hex"))
  assert(second.width >= 320 && second.height >= 180 && second.muted && second.loop)
  return second
}

async function chunks(loaded) {
  return loaded.page.evaluate(() => window.qaMessages.filter(message =>
    message.payload?.actionId === "get_gallery_image" && message.payload.args.chunk_index >= 0))
}

export async function directChecks(browser, sources, origin, output, helpers) {
  const { loadPage, inspectLayout, pluginRoot, hostRoot } = helpers
  const checks = []
  const screenshots = []
  const measurements = []
  const measureOnly = process.argv.includes("--measure-only")
  const switchOnly = process.argv.includes("--switch-only")
  const fixture = largeFixture(await recordFixture(browser, output), output)
  sources.qaVideoFixture = fixture.source
  sources.qaDirectPath = `/plugin/forever_companion/ui/${fixture.sha256}.webm`
  sources.qaDirectRequests = []
  const directory = mkdtempSync(join(tmpdir(), "neko-direct-playback-"))
  copyFileSync(fixture.assetPath, join(directory, `${fixture.sha256}.webm`))
  const probe = await startProbe(hostRoot, directory)
  sources.qaPlaybackPort = probe.port
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
  try {
    if (switchOnly || process.argv.includes("--switch")) {
      for (const width of [1280, 390]) {
        const loaded = await loadPage(browser, origin, {
          locale: "en", wallpaper: "video", direct: "true", tab: "settings",
        }, width, 900, "light", "no-preference")
        try {
          await assertDirectMoving(loaded)
          await check(`switch-${width}-return-to-playing-video-clears-loading`, async () => {
            await loaded.page.evaluate(() => { window.qaDelays["qa-wallpaper-b"] = 1000 })
            for (let round = 0; round < 4; round++) {
              await selectMedia(loaded.frame, "image")
              await loaded.frame.locator('[data-item-id="qa-wallpaper-b"] .tm-gallery-pick').click()
              await loaded.page.waitForFunction(() => window.qaMessages.some(message =>
                message.payload?.actionId === "get_gallery_image" && message.payload.args.item_id === "qa-wallpaper-b"))
              await selectMedia(loaded.frame, "video")
              await loaded.frame.locator('[data-item-id="qa-video"] .tm-gallery-pick').click()
              await loaded.frame.waitForFunction(() => !document.querySelector(".tm-video-loading"), null, { timeout: 2500 })
              await assertDirectMoving(loaded)
            }
            assert.equal((await chunks(loaded)).length, 0)
          })
          await check(`switch-${width}-image-and-video-alternation`, async () => {
            for (let round = 0; round < 6; round++) {
              await selectMedia(loaded.frame, "image")
              await loaded.frame.locator('[data-item-id="qa-wallpaper"] .tm-gallery-pick').click()
              await loaded.frame.waitForFunction(() => !document.querySelector("video.tm-bg-video"))
              await selectMedia(loaded.frame, "video")
              await loaded.frame.locator('[data-item-id="qa-video"] .tm-gallery-pick').click()
              await assertDirectMoving(loaded)
              await loaded.frame.waitForFunction(() => !document.querySelector(".tm-video-loading"))
            }
            assert.equal((await chunks(loaded)).length, 0)
            const path = join(output, `switch-${width}-playing.png`)
            await loaded.page.screenshot({ path, animations: "disabled" })
            screenshots.push({ id: `switch-${width}`, path, layout: await inspectLayout(loaded.frame), errors: [...loaded.errors], diagnostics: [] })
          })
          await check(`switch-${width}-multiple-video-sources-release-old-players`, async () => {
            for (const digit of ["1", "2", "3"]) {
              copyFileSync(fixture.assetPath, join(directory, `${digit.repeat(64)}.webm`))
            }
            await loaded.page.evaluate(() => {
              const fixture = window.qaFixture
              for (const digit of ["1", "2", "3"]) {
                const id = `qa-video-${digit}`
                fixture.videos[id] = { ...fixture.videos["qa-video"],
                  playback_path: `/plugin/forever_companion/ui/${digit.repeat(64)}.webm` }
                fixture.gallery.push({ ...fixture.gallery.find(item => item.id === "qa-video"), id, name: id })
              }
            })
            await destroyAndReopen(loaded)
            await assertDirectMoving(loaded)
            await loaded.frame.locator(".tm-tab").nth(7).click()
            for (const digit of ["1", "2", "3", "2", "1", "3", "1", "2", "3"]) {
              await selectMedia(loaded.frame, "video")
              await loaded.frame.locator(`[data-item-id="qa-video-${digit}"] .tm-gallery-pick`).click()
              await loaded.frame.waitForFunction(source =>
                document.querySelector("video.tm-bg-video")?.currentSrc === source,
              `${origin}/plugin/forever_companion/ui/${digit.repeat(64)}.webm`)
              await assertDirectMoving(loaded)
              await loaded.frame.waitForFunction(() => !document.querySelector(".tm-video-loading"), null, { timeout: 2000 })
              assert.equal((await chunks(loaded)).length, 0)
              assert(await loaded.frame.evaluate(() => window.qaCreatedVideos.every(video =>
                video.isConnected || (!video.getAttribute("src") && video.paused))))
            }
          })
        } finally { await loaded.context.close() }
      }
      if (switchOnly) return { checks, screenshots, measurements }
    }
    await check("direct-host-route-range-mime-cache-and-traversal", async () => {
      const range = await fetch(`${origin}${sources.qaDirectPath}`, { headers: { Range: "bytes=0-255" } })
      assert.equal(range.status, 206)
      assert.equal(range.headers.get("content-type"), "video/webm")
      assert.equal(range.headers.get("accept-ranges"), "bytes")
      assert.equal(range.headers.get("cache-control"), "private, no-cache, max-age=0, must-revalidate")
      assert.equal((await range.arrayBuffer()).byteLength, 256)
      const invalid = await fetch(`${origin}${sources.qaDirectPath}`, { headers: { Range: `bytes=${fixture.size}-` } })
      assert.equal(invalid.status, 416)
      const traversal = await fetch(`http://127.0.0.1:${probe.port}/plugin/forever_companion/ui/..%2Fprivate.db`)
      assert.equal(traversal.status, 403)
    })
    for (const width of measureOnly ? [] : [1280, 390]) {
      const started = Date.now()
      const loaded = await loadPage(browser, origin, { locale: "en", wallpaper: "video", direct: "true" }, width, width === 390 ? 700 : 900, "light", "no-preference")
      try {
        await check(`direct-${width}-large-video-without-blob-or-chunk-rpc`, async () => {
          const moving = await assertDirectMoving(loaded)
          measurements.push({ width, phase: "initial", elapsedIncludingPixelChecksMs: Date.now() - started, bytes: fixture.size })
          assert.equal(moving.source, `${origin}${sources.qaDirectPath}`)
          assert.equal((await chunks(loaded)).length, 0)
          assert.equal(await loaded.frame.evaluate(() => window.qaCreatedVideos.length), 1, "Direct playback created a redundant decoder probe")
          assert.equal(await loaded.page.evaluate(() => window.qaMessages.filter(message =>
            message.payload?.actionId === "get_gallery_image").length), 0, "Bootstrap fetched selected metadata twice")
          assert.equal(await loaded.page.locator("#hosted").getAttribute("sandbox"), "allow-scripts")
          const path = join(output, `direct-${width}-playing.png`)
          await loaded.page.screenshot({ path, animations: "disabled" })
          screenshots.push({ id: `direct-${width}`, path, layout: await inspectLayout(loaded.frame), errors: [...loaded.errors], diagnostics: [] })
        })
        for (let round = 0; round < 2; round++) {
          await check(`direct-${width}-destroyed-iframe-reopen-${round + 1}`, async () => {
            const before = loaded.frame
            const started = Date.now()
            await destroyAndReopen(loaded)
            assert.notEqual(loaded.frame, before)
            assert(before.isDetached())
            await assertDirectMoving(loaded)
            measurements.push({ width, phase: `reopen-${round + 1}`, elapsedIncludingPixelChecksMs: Date.now() - started, bytes: fixture.size })
            assert.equal((await chunks(loaded)).length, 0)
          })
        }
        await check(`direct-${width}-context-refresh-retains-playing-node`, async () => {
          await loaded.frame.locator(VIDEO).evaluate(video => { window.qaDirectNode = video })
          await loaded.frame.evaluate(() => window.NekoUiKit.api.refresh())
          assert(await loaded.frame.locator(VIDEO).evaluate(video => video === window.qaDirectNode))
          await assertDirectMoving(loaded)
        })
        await check(`direct-${width}-focus-and-pageshow-resume-without-poll-delay`, async () => {
          const source = await loaded.frame.locator(VIDEO).evaluate(video => {
            window.qaFocusSource = video.currentSrc
            video.pause()
            window.dispatchEvent(new Event("focus"))
            return video.currentSrc
          })
          await loaded.frame.waitForFunction(() => !document.querySelector("video.tm-bg-video").paused, null, { timeout: 800 })
          await assertDirectMoving(loaded)
          await loaded.frame.locator(VIDEO).evaluate(video => {
            video.pause()
            window.dispatchEvent(new Event("pageshow"))
          })
          await loaded.frame.waitForFunction(() => !document.querySelector("video.tm-bg-video").paused, null, { timeout: 800 })
          assert.equal((await assertDirectMoving(loaded)).source, source)
          assert.equal((await chunks(loaded)).length, 0)
        })
        await check(`direct-${width}-stalled-decoder-recovers-when-paused-is-false`, async () => {
          const source = await loaded.frame.locator(VIDEO).evaluate(video => {
            const nativePlay = video.play.bind(video)
            const currentTime = Object.getOwnPropertyDescriptor(HTMLMediaElement.prototype, "currentTime")
            window.qaRecoveredSeek = 0
            video.pause()
            Object.defineProperty(video, "paused", { configurable: true, get: () => false })
            video.play = () => Promise.resolve()
            Object.defineProperty(video, "currentTime", {
              configurable: true, get: () => currentTime.get.call(video),
              set: value => {
                window.qaRecoveredSeek++
                delete video.paused
                delete video.play
                delete video.currentTime
                currentTime.set.call(video, value)
                nativePlay()
              },
            })
            return video.currentSrc
          })
          await loaded.frame.waitForFunction(() => window.qaRecoveredSeek > 0, null, { timeout: 9000 })
          assert.equal((await assertDirectMoving(loaded)).source, source)
          assert.equal((await chunks(loaded)).length, 0)
        })
        if (width === 1280) await check("direct-stalled-frame-presentation-reloads-same-source-once", async () => {
          const source = await loaded.frame.locator(VIDEO).evaluate(video => {
            const requestFrame = video.requestVideoFrameCallback.bind(video)
            const load = video.load.bind(video)
            const now = performance.now.bind(performance)
            window.qaRecoveryOffset = 0
            window.qaRecoveryLoads = 0
            performance.now = () => now() + window.qaRecoveryOffset
            video.requestVideoFrameCallback = () => 0
            video.load = () => {
              window.qaRecoveryLoads++
              video.requestVideoFrameCallback = requestFrame
              load()
            }
            return video.currentSrc
          })
          // Stop only frame presentation callbacks; currentTime still advances.
          await loaded.frame.waitForTimeout(150)
          await loaded.frame.evaluate(() => { window.qaRecoveryOffset = 5000 })
          await loaded.frame.waitForTimeout(1200)
          await loaded.frame.evaluate(() => { window.qaRecoveryOffset = 10000 })
          await loaded.frame.waitForFunction(() => window.qaRecoveryLoads === 1, null, { timeout: 2500 })
          assert.equal((await assertDirectMoving(loaded)).source, source)
          assert.equal(await loaded.frame.evaluate(() => window.qaRecoveryLoads), 1)
          assert.equal((await chunks(loaded)).length, 0)
        })
        await check(`direct-${width}-runtime-error-recovers-once-through-blob`, async () => {
          await loaded.frame.locator(VIDEO).evaluate(video => video.dispatchEvent(new Event("error")))
          await loaded.frame.waitForFunction(() => document.querySelector("video.tm-bg-video")?.currentSrc.startsWith("blob:"), null, { timeout: 15000 })
          await assertDirectMoving(loaded)
          assert((await chunks(loaded)).length > 0)
          const requests = await loaded.page.evaluate(() => window.qaMessages.filter(message =>
            message.payload?.actionId === "get_gallery_image" && message.payload.args.prefer_direct === false))
          assert(requests.length >= 1)
        })
      } finally { await loaded.context.close() }
    }
    if (!measureOnly) {
      const legacy = await loadPage(browser, origin, {
        locale: "en", wallpaper: "video", direct: "true", legacyGallery: "true",
      }, 1280, 900, "light", "no-preference")
      try {
        await check("direct-legacy-gallery-without-bootstrap-still-plays", async () => {
          await assertDirectMoving(legacy)
          assert.equal((await chunks(legacy)).length, 0)
          assert.equal(await legacy.page.evaluate(() => window.qaMessages.filter(message =>
            message.payload?.actionId === "get_gallery_image").length), 1)
        })
      } finally { await legacy.context.close() }
    }
    for (let round = 0; round < 3; round++) {
      const timed = await loadPage(browser, origin, {
        locale: "en", wallpaper: "video", direct: "true", rpcDelay: "200",
      }, 1280, 900, "light", "no-preference")
      try {
        await check(`direct-first-frame-with-200ms-rpc-${round + 1}`, async () => {
          await assertDirectMoving(timed)
          measurements.push({ phase: "first-frame", round: round + 1, simulatedRpcMs: 200,
            ...await timed.frame.evaluate(() => ({
              firstFrameMs: window.qaFirstWallpaperFrameAt, decoderCount: window.qaCreatedVideos.length,
            })),
            metadataRequests: await timed.page.evaluate(() => window.qaMessages.filter(message =>
              message.payload?.actionId === "get_gallery_image").length),
          })
        })
      } finally { await timed.context.close() }
    }
    if (measureOnly) return { checks, screenshots, measurements }
    for (const direct of ["missing", "unsafe"]) {
      const loaded = await loadPage(browser, origin, { locale: "en", wallpaper: "video", direct }, 1280, 900, "light", "no-preference")
      try {
        await check(`direct-${direct}-endpoint-falls-back-to-real-blob`, async () => {
          const moving = await assertDirectMoving(loaded)
          assert(moving.source.startsWith("blob:"))
          assert((await chunks(loaded)).length > 0)
        })
      } finally { await loaded.context.close() }
    }
    const reduced = await loadPage(browser, origin, { locale: "en", wallpaper: "video", direct: "true" }, 390, 700, "light", "reduce")
    try {
      await check("direct-reduced-motion-does-not-request-or-prepare-video", async () => {
        assert.equal((await chunks(reduced)).length, 0)
        assert.equal(await reduced.frame.locator(VIDEO).count(), 0)
        const requests = await reduced.page.evaluate(() => window.qaMessages.filter(message =>
          message.payload?.actionId === "get_gallery_image" && message.payload.args.prefer_direct === true))
        assert.equal(requests.length, 0)
      })
    } finally { await reduced.context.close() }
    const actualIndex = process.argv.indexOf("--actual-video")
    if (actualIndex >= 0) {
      const actualPath = resolve(process.argv[actualIndex + 1])
      const hash = createHash("sha256")
      for await (const block of createReadStream(actualPath)) hash.update(block)
      const name = `${hash.digest("hex")}.mp4`
      copyFileSync(actualPath, join(directory, name))
      const size = statSync(actualPath).size
      sources.qaDirectPath = `/plugin/forever_companion/ui/${name}`
      sources.qaVideoFixture = { ...fixture.source, mime: "video/mp4", size,
        chunks: Array(Math.ceil(size / (768 * 1024))).fill("") }
      const started = Date.now()
      const actual = await loadPage(browser, origin, { locale: "en", wallpaper: "video", direct: "true" }, 1280, 900, "light", "no-preference")
      try {
        await check("direct-actual-video-read-only-temporary-copy-and-reopen", async () => {
          await assertDirectMoving(actual)
          measurements.push({ phase: "actual-initial", bytes: size, elapsedIncludingPixelChecksMs: Date.now() - started })
          const restart = Date.now()
          await destroyAndReopen(actual)
          const moving = await assertDirectMoving(actual)
          assert.equal(moving.source, `${origin}${sources.qaDirectPath}`)
          assert.equal((await chunks(actual)).length, 0)
          measurements.push({ phase: "actual-reopen", bytes: size, width: moving.width, height: moving.height,
            elapsedIncludingPixelChecksMs: Date.now() - restart })
        })
      } finally { await actual.context.close() }
    }
    assert(sources.qaDirectRequests.some(request => request.status === 206 && request.range), "Browser did not exercise HTTP Range")
    measurements.push({ httpRequests: sources.qaDirectRequests })
  } finally {
    sources.qaPlaybackPort = null
    const finished = new Promise(resolve => probe.child.once("exit", resolve))
    probe.child.kill()
    await finished
    assert.equal(dirname(resolve(directory)), resolve(tmpdir()))
    assert(basename(directory).startsWith("neko-direct-playback-"))
    rmSync(directory, { recursive: true, force: true })
  }
  return { checks, screenshots, measurements }
}
