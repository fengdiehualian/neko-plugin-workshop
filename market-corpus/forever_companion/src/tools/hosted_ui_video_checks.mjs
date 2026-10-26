import assert from "node:assert/strict"
import { createHash } from "node:crypto"
import { mkdirSync, readFileSync, writeFileSync } from "node:fs"
import { join } from "node:path"
import { openImport, openStorage, selectMedia } from "./hosted_ui_settings_checks.mjs"

const VIDEO = "video.tm-bg-video"
const VIDEO_INPUT = "input.tm-wallpaper-video-input"
const DIRECTORY_INPUT = "input.tm-wallpaper-directory-input"
const MOTION = "input.tm-wallpaper-motion"
const CHUNK_BYTES = 768 * 1024
const LOCALES = ["zh-CN", "zh-TW", "en", "ja", "ko", "ru", "es", "pt"]

function option(name, fallback) {
  const index = process.argv.indexOf(`--${name}`)
  return index < 0 ? fallback : process.argv[index + 1]
}

// The browser records real moving pixels. Only generated QA output is written.
export async function recordFixture(browser, output, stress = true) {
  const context = await browser.newContext()
  try {
    const page = await context.newPage()
    const recording = await page.evaluate(async stress => {
      const canvas = document.createElement("canvas")
      canvas.width = 640
      canvas.height = 360
      const paint = canvas.getContext("2d", { willReadFrequently: true })
      const pixels = paint.createImageData(canvas.width, canvas.height)
      let frame = 0
      const draw = () => {
        if (!stress) {
          paint.fillStyle = "#8dccd7"
          paint.fillRect(0, 0, 640, 360)
          paint.fillStyle = "#f3ce67"
          paint.beginPath()
          paint.arc(534, 74, 35, 0, Math.PI * 2)
          paint.fill()
          paint.fillStyle = "#e9f5f2"
          paint.fillRect(35 + frame % 90, 65, 120, 16)
          paint.fillStyle = "#639b9f"
          paint.beginPath()
          paint.moveTo(0, 265)
          paint.lineTo(168, 116)
          paint.lineTo(332, 260)
          paint.lineTo(448, 156)
          paint.lineTo(640, 285)
          paint.fill()
          paint.fillStyle = "#367c72"
          paint.fillRect(0, 265, 640, 95)
          paint.fillStyle = "#ef7282"
          paint.fillRect(100 + frame % 140 * 2, 278, 46, 46)
          frame += 1
          return
        }
        let seed = (frame * 1664525 + 1013904223) >>> 0
        for (let index = 0; index < pixels.data.length; index += 4) {
          seed ^= seed << 13
          seed ^= seed >>> 17
          seed ^= seed << 5
          pixels.data[index] = seed & 255
          pixels.data[index + 1] = seed >>> 8 & 255
          pixels.data[index + 2] = seed >>> 16 & 255
          pixels.data[index + 3] = 255
        }
        paint.putImageData(pixels, 0, 0)
        paint.fillStyle = frame % 20 < 10 ? "#ef5467" : "#44bea5"
        paint.fillRect(240, 100, 160, 160)
        frame += 1
      }
      draw()
      const poster = canvas.toDataURL("image/jpeg", 0.7)
      const small = document.createElement("canvas")
      small.width = 160
      small.height = 90
      small.getContext("2d").drawImage(canvas, 0, 0, 160, 90)
      const thumb = small.toDataURL("image/jpeg", 0.7)
      const stream = canvas.captureStream(25)
      const mime = ["video/webm;codecs=vp8", "video/webm"].find(value => MediaRecorder.isTypeSupported(value))
      if (!mime) throw new Error("This isolated Chromium cannot record a WebM fixture")
      const recorder = new MediaRecorder(stream, { mimeType: mime, videoBitsPerSecond: stress ? 6500000 : 600000 })
      const chunks = []
      recorder.ondataavailable = event => { if (event.data.size) chunks.push(event.data) }
      const finished = new Promise((resolve, reject) => {
        recorder.onerror = event => reject(event.error || new Error("MediaRecorder failed"))
        recorder.onstop = resolve
      })
      const timer = setInterval(draw, 40)
      recorder.start()
      await new Promise(resolve => setTimeout(resolve, 4000))
      clearInterval(timer)
      recorder.stop()
      await finished
      stream.getTracks().forEach(track => track.stop())
      const blob = new Blob(chunks, { type: "video/webm" })
      const dataUrl = await new Promise((resolve, reject) => {
        const reader = new FileReader()
        reader.onload = () => resolve(reader.result)
        reader.onerror = () => reject(new Error("Fixture FileReader failed"))
        reader.readAsDataURL(blob)
      })
      return { dataUrl, poster, thumb, frames: frame }
    }, stress)
    const bytes = Buffer.from(recording.dataUrl.split(",")[1], "base64")
    if (stress) assert(bytes.length > CHUNK_BYTES, "Real video fixture must exercise multiple production-size chunks")
    assert(bytes.length < 8 * 1024 * 1024, "Keep the real multi-frame QA fixture small")
    assert.equal(bytes.subarray(0, 4).toString("hex"), "1a45dfa3", "MediaRecorder did not produce WebM")
    const chunks = []
    for (let offset = 0; offset < bytes.length; offset += CHUNK_BYTES) chunks.push(bytes.subarray(offset, offset + CHUNK_BYTES).toString("base64"))
    const assetPath = join(output, stress ? "qa-motion.webm" : "qa-motion-preview.webm")
    writeFileSync(assetPath, bytes)
    return {
      source: { mime: "video/webm", size: bytes.length, poster: recording.poster, thumb: recording.thumb, chunks },
      assetPath, frames: recording.frames, size: bytes.length, chunkCount: chunks.length,
      sha256: createHash("sha256").update(bytes).digest("hex"),
    }
  } finally { await context.close() }
}

export function largeFixture(recorded, output) {
  const source = readFileSync(recorded.assetPath)
  const target = 49 * 1024 * 1024 + 123
  // A valid EBML Void element is padding, not counterfeit video data. The
  // original moving WebM remains intact and Chromium must decode it below.
  const voidSize = target - source.length - 9
  const header = Buffer.alloc(9)
  header[0] = 0xec
  header[1] = 0x01
  header.writeUIntBE(voidSize, 3, 6)
  const bytes = Buffer.concat([source, header, Buffer.alloc(voidSize)])
  const chunks = []
  for (let offset = 0; offset < bytes.length; offset += CHUNK_BYTES) chunks.push(bytes.subarray(offset, offset + CHUNK_BYTES).toString("base64"))
  const assetPath = join(output, "qa-motion-over-48mib.webm")
  writeFileSync(assetPath, bytes)
  return {
    source: { ...recorded.source, size: bytes.length, chunks },
    assetPath, frames: recorded.frames, size: bytes.length, chunkCount: chunks.length,
    sha256: createHash("sha256").update(bytes).digest("hex"),
  }
}

async function installResourceProbe(frame) {
  await frame.evaluate(() => {
    if (window.qaVideoResources) return
    window.qaVideoResources = { created: [], revoked: [], elements: [], play: 0, pause: 0 }
    const create = URL.createObjectURL.bind(URL)
    const revoke = URL.revokeObjectURL.bind(URL)
    URL.createObjectURL = blob => {
      const url = create(blob)
      window.qaVideoResources.created.push({ url, type: blob.type, size: blob.size })
      return url
    }
    URL.revokeObjectURL = url => {
      window.qaVideoResources.revoked.push(url)
      return revoke(url)
    }
    const play = HTMLMediaElement.prototype.play
    const pause = HTMLMediaElement.prototype.pause
    HTMLMediaElement.prototype.play = function() {
      window.qaVideoResources.play += 1
      if (!window.qaVideoResources.elements.includes(this)) window.qaVideoResources.elements.push(this)
      return play.call(this)
    }
    HTMLMediaElement.prototype.pause = function() {
      window.qaVideoResources.pause += 1
      if (!window.qaVideoResources.elements.includes(this)) window.qaVideoResources.elements.push(this)
      return pause.call(this)
    }
  })
}

async function pick(frame, id) {
  await selectMedia(frame, id.startsWith("qa-video") ? "video" : "image")
  await frame.locator(`.tm-gallery-tile[data-item-id="${id}"] .tm-gallery-pick`).click()
}

async function videoSnapshot(frame) {
  return frame.locator(VIDEO).evaluate(video => {
    const canvas = document.createElement("canvas")
    canvas.width = 32
    canvas.height = 18
    const context = canvas.getContext("2d", { willReadFrequently: true })
    context.drawImage(video, 0, 0, 32, 18)
    const pixels = [...context.getImageData(0, 0, 32, 18).data]
    const box = video.getBoundingClientRect()
    const style = getComputedStyle(video)
    return {
      time: video.currentTime, paused: video.paused, readyState: video.readyState,
      muted: video.muted, defaultMuted: video.defaultMuted, loop: video.loop,
      playsInline: video.playsInline, width: video.videoWidth, height: video.videoHeight,
      currentSrc: video.currentSrc, pixels,
      hasColor: pixels.some((value, index) => index % 4 !== 3 && value > 20),
      opacity: Number(style.opacity), display: style.display, visibility: style.visibility,
      box: { left: box.left, top: box.top, right: box.right, bottom: box.bottom },
      viewport: { width: window.innerWidth, height: window.innerHeight },
    }
  })
}

async function assertMoving(frame) {
  await frame.waitForSelector(VIDEO)
  await frame.waitForFunction(() => {
    const video = document.querySelector("video.tm-bg-video")
    return video && video.readyState >= 2 && !video.paused
  }, null, { timeout: 12000 })
  const first = await videoSnapshot(frame)
  await frame.waitForTimeout(470)
  const second = await videoSnapshot(frame)
  assert(first.hasColor && second.hasColor, "Decoded video frames are blank")
  assert.notDeepEqual(second.pixels, first.pixels, "Video pixels did not change between decoded frames")
  assert(second.time !== first.time, "Video playback time did not advance")
  assert(second.width >= 320 && second.height >= 180, "Video decoded dimensions collapsed")
  assert(second.opacity >= 0.99 && second.display !== "none" && second.visibility !== "hidden", "Video moves offscreen or invisibly instead of being the actual background")
  assert(second.box.left <= 1 && second.box.top <= 1 && second.box.right >= second.viewport.width - 1 && second.box.bottom >= second.viewport.height - 1, "Video layer does not cover the plugin viewport")
  assert(second.muted && second.loop && second.playsInline, "Background video must be muted, looping and inline")
  return second
}

async function assertPaused(frame) {
  await frame.waitForTimeout(160)
  const state = await frame.evaluate(() => [...document.querySelectorAll("video.tm-bg-video")].map(video => ({ paused: video.paused, time: video.currentTime })))
  assert(state.every(video => video.paused), "Background playback continued while it should be suspended")
  await frame.waitForTimeout(350)
  const next = await frame.evaluate(() => [...document.querySelectorAll("video.tm-bg-video")].map(video => ({ paused: video.paused, time: video.currentTime })))
  assert.deepEqual(next, state, "Paused media time continued advancing")
}

async function call(frame, actionId, args) {
  const result = await frame.evaluate(async value => {
    const payload = await window.NekoUiKit.api.call(value.actionId, value.args)
    return payload.result ?? payload
  }, { actionId, args })
  return result
}

async function callReject(frame, actionId, args, expected) {
  await assert.rejects(() => call(frame, actionId, args), error => error.message.includes(expected))
}

async function reopen(loaded) {
  const result = await loaded.page.evaluate(() => ({
    fixture: window.qaFixture,
    messages: window.qaMessages,
  }))
  await loaded.frame.evaluate(() => window.NekoUiKit.render(null, document.getElementById("root")))
  await loaded.frame.evaluate(() => window.__NekoRenderHostedSurface())
  await loaded.frame.waitForSelector(".tm-tabs")
  await loaded.frame.waitForTimeout(250)
  await loaded.frame.locator(".tm-tab").nth(7).click()
  await openImport(loaded.frame, "video")
  await openStorage(loaded.frame)
  return result
}

export async function videoChecks(browser, sources, origin, output, helpers) {
  const { loadPage: createPage, inspectLayout, pluginRoot, loadDependency } = helpers
  async function loadPage(...args) {
    const loaded = await createPage(...args)
    await openImport(loaded.frame, "video")
    await openStorage(loaded.frame)
    return loaded
  }
  const screenshots = []
  const checks = []
  const fixture = await recordFixture(browser, output)
  sources.qaVideoFixture = fixture.source

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

  async function capture(loaded, id, focus = VIDEO_INPUT) {
    if (focus === VIDEO_INPUT) await openImport(loaded.frame, "video")
    await loaded.frame.locator(focus).scrollIntoViewIfNeeded()
    const layout = await inspectLayout(loaded.frame)
    const diagnostics = await loaded.page.evaluate(() => window.qaDiagnostics.filter(message => message.type === "neko-hosted-surface-error"))
    const path = join(output, `${id}.png`)
    await loaded.page.screenshot({ path, animations: "disabled" })
    screenshots.push({ id, path, layout, errors: [...loaded.errors], diagnostics })
    assert(layout.documentOverflow <= 2 && layout.contentOverflow <= 2, `${id}: horizontal overflow`)
    assert.deepEqual(layout.issues, [], `${id}: clipped controls or copy`)
    assert.deepEqual(loaded.errors, [], `${id}: browser errors`)
    assert.deepEqual(diagnostics, [], `${id}: hosted runtime errors`)
  }

  const recovery = await loadPage(browser, origin, { locale: "en", wallpaper: "none", video: "true", tab: "settings" }, 1280, 900, "light", "no-preference")
  try {
    await check("video-aborted-play-request-recovers-without-permanent-cover", async () => {
      await pick(recovery.frame, "qa-video")
      await assertMoving(recovery.frame)
      await recovery.frame.locator(VIDEO).evaluate(video => {
        const original = video.play.bind(video)
        window.qaInterruptedPlay = 0
        video.play = function() {
          if (window.qaInterruptedPlay++ === 0) {
            return Promise.reject(new DOMException("Playback temporarily interrupted", "AbortError"))
          }
          return original()
        }
        video.pause()
        window.dispatchEvent(new Event("resize"))
      })
      await assertMoving(recovery.frame)
      assert.equal(await recovery.frame.locator(".tm-appearance .neko-alert").count(), 0)
      await capture(recovery, "video-interrupted-play-recovered")
    }, { failure: "One rejected play promise models Chromium's transient AbortError; subsequent calls use real browser playback" })
  } finally { await recovery.context.close() }

  const fresh = await loadPage(browser, origin, { locale: "en", wallpaper: "none", video: "true", tab: "settings" }, 1280, 900, "light", "no-preference")
  try {
    await check("video-destroyed-iframe-reopens-saved-video-with-delayed-batch", async () => {
      await pick(fresh.frame, "qa-video")
      await assertMoving(fresh.frame)
      await fresh.frame.locator(".tm-save button").click()
      await fresh.page.waitForFunction(() => window.qaFixture.appearance.bg_id === "qa-video")
      for (let pass = 0; pass < 2; pass++) {
        const attached = fresh.page.waitForEvent("frameattached")
        await fresh.page.evaluate(() => {
          window.qaHoldReplies["get_gallery_image:qa-video:0"] = true
          const frame = document.getElementById("hosted")
          const next = frame.cloneNode()
          frame.remove()
          document.body.appendChild(next)
        })
        fresh.frame = await attached
        await fresh.frame.waitForSelector(".tm-tabs")
        await fresh.frame.locator(".tm-tab").nth(7).click()
        await fresh.frame.waitForSelector(".tm-bg-image")
        await fresh.frame.waitForSelector(".tm-video-loading")
        await fresh.page.evaluate(() => {
          // Release future duplicate requests, keeping the original response held.
          window.qaHoldReplies["get_gallery_image:qa-video:0"] = false
        })
        await assertMoving(fresh.frame)
        await fresh.page.evaluate(() => window.qaReleaseHeldReplies("get_gallery_image:qa-video:0"))
        await fresh.frame.waitForTimeout(100)
        await assertMoving(fresh.frame)
        await fresh.frame.waitForSelector(".tm-video-loading", { state: "detached" })
      }
      await capture(fresh, "video-fresh-iframe-recovered")
    }, { realBrowser: true, lifecycle: "Two genuinely destroyed opaque iframe documents, restored saved settings, held first batch and late original replies" })
  } finally { await fresh.context.close() }

  if (process.argv.includes("--video-recovery-only")) return { screenshots, checks }

  await check("video-generated-real-multiframe-webm", async () => {
    assert(fixture.frames >= 70)
    assert(fixture.chunkCount >= 2)
  }, { source: "Chromium canvas.captureStream + MediaRecorder; not a fake video tag or encoded still image", bytes: fixture.size, chunks: fixture.chunkCount })

  const large = largeFixture(fixture, output)
  sources.qaVideoFixture = large.source
  const largeLoaded = await loadPage(browser, origin, { locale: "en", wallpaper: "none", video: "true", tab: "settings" }, 1280, 900, "light", "no-preference")
  try {
    await check("video-real-over-48mib-upload-download-and-moving-pixels", async () => {
      await largeLoaded.frame.locator(VIDEO_INPUT).setInputFiles(large.assetPath)
      await largeLoaded.page.waitForFunction(() => window.qaFixture.gallery.some(item => item.id.startsWith("qa-video-upload-")), null, { timeout: 60000 })
      const result = await largeLoaded.page.evaluate(() => {
        const item = window.qaFixture.gallery.find(item => item.id.startsWith("qa-video-upload-"))
        return { item, chunks: window.qaFixture.videos[item.id].chunks }
      })
      assert(result.item.size > 48 * 1024 * 1024)
      assert(result.chunks.length > 64, "Large fixture did not cross the former hidden chunk cap")
      const received = Buffer.concat(result.chunks.map(value => Buffer.from(value, "base64")))
      assert.equal(createHash("sha256").update(received).digest("hex"), large.sha256)
      await pick(largeLoaded.frame, result.item.id)
      await assertMoving(largeLoaded.frame)
      const reads = await largeLoaded.page.evaluate(id => window.qaMessages.filter(message => message.method === "call"
        && message.payload.actionId === "get_gallery_image" && message.payload.args.item_id === id).length, result.item.id)
      assert.equal(reads, 0, "Selecting a newly imported large video requested metadata or downloaded its bytes again")
      await largeLoaded.frame.locator(".tm-save button").click()
      await largeLoaded.page.waitForFunction(id => window.qaFixture.appearance.bg_id === id, result.item.id)
      await reopen(largeLoaded)
      await assertMoving(largeLoaded.frame)
      const restoredReads = await largeLoaded.page.evaluate(id => window.qaMessages.filter(message => message.method === "call"
        && message.payload.actionId === "get_gallery_image" && message.payload.args.item_id === id
        && message.payload.args.chunk_index >= 0).map(message => ({
          index: message.payload.args.chunk_index,
          count: message.payload.args.chunk_count,
        })), result.item.id)
      assert.equal(restoredReads.length, Math.ceil(large.chunkCount / 4), "Reopened stored media did not use bounded batch reads")
      assert.deepEqual(restoredReads.map(value => value.index), Array.from(
        { length: restoredReads.length }, (_, index) => index * 4,
      ))
      assert(restoredReads.every(value => Number.isInteger(value.count) && value.count >= 1 && value.count <= 4))
      assert.equal(restoredReads.reduce((sum, value) => sum + value.count, 0), large.chunkCount,
        "Reopened batch reads did not cover every production-size chunk")
    }, { bytes: large.size, chunks: large.chunkCount, realBrowser: true, limitation: "49 MiB container with real recorded moving frames plus standards-compatible EBML Void padding; not a 512 MiB stress claim" })
  } finally { await largeLoaded.context.close() }
  sources.qaVideoFixture = fixture.source

  for (const replyMode of ["dropped", "late"]) {
    const recovery = await loadPage(browser, origin, { locale: "en", wallpaper: "none", video: "true", tab: "settings" }, 1280, 900, "light", "no-preference")
    try {
      await check(`video-written-chunk-${replyMode}-ack-recovers-via-receipt`, async () => {
        const before = await recovery.page.evaluate(mode => {
          if (mode === "dropped") window.qaDroppedReplies["gallery_add:video_chunk:0"] = true
          else window.qaDelays["gallery_add:video_chunk:0"] = 3500
          return window.qaFixture.gallery.length
        }, replyMode)
        await recovery.frame.locator(VIDEO_INPUT).setInputFiles(fixture.assetPath)
        await recovery.page.waitForFunction(count => window.qaFixture.gallery.length === count + 1, before, { timeout: 10000 })
        await recovery.frame.waitForFunction(() => !document.querySelector("input.tm-wallpaper-video-input").disabled)
        const recovered = await recovery.page.evaluate(() => {
          const chunks = window.qaMessages.filter(message => message.payload?.args?.op === "video_chunk")
          const first = chunks.find(message => message.payload.args.chunk_index === 0)
          const receipt = window.qaMessages.find(message => message.payload?.args?.op === "video_status")
          const commit = window.qaMessages.find(message => message.payload?.args?.op === "video_commit")
          return {
            first, receipt, commit,
            writes: window.qaVideoWrites,
            commits: window.qaVideoCalls.filter(value => value.op === "video_commit").length,
            chunkRequests: chunks.length,
            items: window.qaFixture.gallery.filter(item => item.id.startsWith("qa-video-upload-")),
            cancelled: window.qaCancelledRequests,
          }
        })
        assert(recovered.receipt, "Lost ACK never triggered a read-only receipt query")
        assert.equal(recovered.receipt.payload.args.upload_id, recovered.first.payload.args.upload_id)
        assert.equal(recovered.receipt.payload.args.chunk_index, 0)
        assert(recovered.receipt.receivedAt - recovered.first.receivedAt < 3000, "Receipt waited for the old 20-second request timeout")
        assert(recovered.commit.receivedAt - recovered.first.receivedAt < 3500, "Receipt did not complete upload before the late ACK")
        assert.equal(recovered.commits, 1)
        assert.equal(recovered.chunkRequests, fixture.chunkCount, "Receipt recovery unnecessarily retransmitted media bytes")
        assert.equal(recovered.writes.length, fixture.chunkCount)
        assert.equal(recovered.writes.filter(value => value.chunk_index === 0).length, 1)
        assert.equal(recovered.items.length, 1)
        assert.equal(await recovery.frame.locator(`.tm-gallery-tile[data-item-id="${recovered.items[0].id}"]`).count(), 1)
        assert(recovered.cancelled.some(value => value.requestId === recovered.first.requestId), "Recovered primary request remained pending")
        if (replyMode === "late") {
          await recovery.page.waitForFunction(id => window.qaResolved.some(value => value.requestId === id), recovered.first.requestId, { timeout: 6000 })
          await recovery.frame.waitForTimeout(150)
          const afterLate = await recovery.page.evaluate(() => ({
            items: window.qaFixture.gallery.filter(item => item.id.startsWith("qa-video-upload-")).length,
            commits: window.qaVideoCalls.filter(value => value.op === "video_commit").length,
            writes: window.qaVideoWrites.length,
          }))
          assert.deepEqual(afterLate, { items: 1, commits: 1, writes: fixture.chunkCount }, "Late ACK advanced or published an already completed upload again")
        }
      }, { realBrowser: true, race: "The production-sized first chunk is written once; only its postMessage ACK is dropped or delayed. A real one-second receipt probe restores completion without byte retransmission." })
    } finally { await recovery.context.close() }
  }

  for (const mutation of ["cancel", "clear"]) {
    const interruptedReceipt = await loadPage(browser, origin, { locale: "en", wallpaper: "none", video: "true", tab: "settings" }, 1280, 900, "light", "no-preference")
    try {
      await check(`video-late-receipt-cannot-resurrect-upload-after-${mutation}`, async () => {
        const before = await interruptedReceipt.page.evaluate(() => {
          window.qaDroppedReplies["gallery_add:video_chunk:0"] = true
          window.qaDelays["gallery_add:video_status:0"] = 1400
          return window.qaFixture.gallery.length
        })
        if (mutation === "clear") {
          await openStorage(interruptedReceipt.frame)
          await interruptedReceipt.frame.getByRole("button", { name: sources.messages.en["panel.appearance.clearMedia"], exact: true }).click()
        }
        await interruptedReceipt.frame.locator(VIDEO_INPUT).setInputFiles(fixture.assetPath)
        await interruptedReceipt.page.waitForFunction(() => window.qaMessages.some(message => message.payload?.args?.op === "video_status"), null, { timeout: 6000 })
        const receipt = await interruptedReceipt.page.evaluate(() => window.qaMessages.find(message => message.payload?.args?.op === "video_status").requestId)
        assert.equal(await interruptedReceipt.page.evaluate(id => window.qaResolved.some(value => value.requestId === id), receipt), false)
        if (mutation === "clear") {
          await interruptedReceipt.frame.getByRole("button", { name: sources.messages.en["panel.confirm"], exact: true }).last().click()
        } else await interruptedReceipt.frame.locator(".tm-video-progress button").click()
        await interruptedReceipt.page.waitForFunction(() => Object.keys(window.qaFixture.uploads).length === 0, null, { timeout: 6000 })
        await interruptedReceipt.page.waitForFunction(id => window.qaResolved.some(value => value.requestId === id), receipt, { timeout: 6000 })
        await interruptedReceipt.frame.waitForTimeout(200)
        const result = await interruptedReceipt.page.evaluate(() => ({
          count: window.qaFixture.gallery.length,
          ops: window.qaVideoCalls.map(value => value.op),
          writes: window.qaVideoWrites.length,
          cancelled: window.qaCancelledRequests.map(value => value.requestId),
          uploads: Object.keys(window.qaFixture.uploads).length,
        }))
        assert(result.ops.includes("video_abort"))
        assert(!result.ops.includes("video_commit"), "A late accepted receipt published a cancelled upload")
        assert.equal(result.ops.filter(value => value === "video_chunk").length, 1)
        assert.equal(result.writes, 1)
        assert.equal(result.uploads, 0)
        assert.equal(result.count, mutation === "clear" ? 0 : before)
        assert(result.cancelled.includes(receipt), "User cancellation did not cancel the receipt request")
      }, { realBrowser: true, race: "An accepted read-only receipt is captured before cancellation/clear and arrives afterward; it must not resume the upload or recreate data." })
    } finally { await interruptedReceipt.context.close() }
  }

  await check("animated-webp-and-apng-keep-original-in-auto-import", async () => {
    const { transform } = loadDependency("sucrase").value
    const source = readFileSync(join(pluginRoot, "ui/utils.ts"), "utf8")
    const compiled = transform(source, { transforms: ["typescript"], production: true }).code
      .replace(/^import .*$/gm, "").replace(/\bexport\s+(?=(?:async\s+)?(?:function|const))/g, "")
    class QaImage {
      naturalWidth = 400
      naturalHeight = 240
      set src(value) { queueMicrotask(() => this.onload?.()) }
    }
    const qaDocument = { createElement: () => ({ getContext: () => ({ drawImage() {} }), toDataURL: mime => `data:${mime};base64,YWJj` }) }
    const { compressImageDataUrl } = new Function("Image", "document", `${compiled}; return {compressImageDataUrl};`)(QaImage, qaDocument)
    const webp = Buffer.concat([Buffer.from("RIFF"), Buffer.from([30, 0, 0, 0]), Buffer.from("WEBPVP8X"), Buffer.from([10, 0, 0, 0, 2]), Buffer.alloc(9)])
    const png = Buffer.concat([Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]), Buffer.from([0, 0, 0, 8]), Buffer.from("acTL"), Buffer.alloc(12)])
    for (const [mime, bytes] of [["image/webp", webp], ["image/png", png]]) {
      const original = `data:${mime};base64,${bytes.toString("base64")}`
      const result = await compressImageDataUrl(original, mime, "auto")
      assert.equal(result.dataUrl, original, "Automatic import flattened an animation container")
    }
  }, { unit: true, limitation: "Container detection is exercised with synthetic chunk headers and a mocked thumbnail decoder; moving-frame video checks use the real browser" })

  await check("wallpaper-engine-pure-parser-security-and-selection", async () => {
    const { transform } = loadDependency("sucrase").value
    const source = readFileSync(join(pluginRoot, "ui/wallpaper_import.ts"), "utf8")
    const compiled = transform(source, { transforms: ["typescript"], production: true }).code.replace(/\bexport\s+(?=(?:async\s+)?(?:function|const))/g, "")
    const parser = new Function(`${compiled}; return {safeWallpaperPath, wallpaperMediaKind, scanWallpaperFiles};`)()
    for (const value of ["../clip.webm", "/clip.webm", "C:\\clip.webm", "\\\\server\\clip.webm", "https://x/clip.webm", "folder/../../clip.webm"]) {
      assert.equal(parser.safeWallpaperPath(value), "", `Unsafe project path accepted: ${value}`)
    }
    assert.equal(parser.wallpaperMediaKind("CLIP.WEBM"), "video")
    assert.equal(parser.wallpaperMediaKind("cover.png"), "image")
    assert.equal(parser.wallpaperMediaKind("scene.pkg"), "")
    const file = (path, content = "") => {
      const entry = new File([content], path.split("/").at(-1), { type: path.endsWith(".json") ? "application/json" : "application/octet-stream" })
      Object.defineProperty(entry, "webkitRelativePath", { value: path })
      return entry
    }
    const scan = await parser.scanWallpaperFiles([
      file("root/video/project.json", JSON.stringify({ type: "video", file: "clip.webm", title: "<b>title</b>", preview: "preview.png" })),
      file("root/video/clip.webm"), file("root/video/preview.png"),
      file("root/scene/project.json", JSON.stringify({ type: "scene", file: "scene.pkg" })), file("root/scene/scene.pkg"), file("root/scene/texture.png"),
      file("root/web/project.json", JSON.stringify({ type: "web", file: "index.html" })), file("root/web/index.html"), file("root/web/preview.png"),
      file("root/application/project.json", JSON.stringify({ type: "application", file: "app.exe" })), file("root/application/app.exe"),
      file("root/bad/project.json", JSON.stringify({ type: "video", file: "../video/clip.webm" })),
    ], () => false)
    assert.equal(scan.candidates.length, 1, "Scene textures and project previews must not become standalone wallpaper candidates")
    assert.equal(scan.candidates[0].kind, "video")
    assert.equal(scan.candidates[0].title, "<b>title</b>", "Titles are plain text, not HTML")
    assert.equal(scan.unsupported, 3)
    assert.equal(scan.invalid, 1)
    await assert.rejects(() => parser.scanWallpaperFiles([
      file("root/p/project.json", JSON.stringify({ type: "video", file: "clip.webm" })),
      file("root/p/clip.webm"),
    ], () => true), /wallpaper_cancelled/, "Cancelled directory scan continued")
  })

  const loaded = await loadPage(browser, origin, { locale: "en", wallpaper: "none", video: "true", tab: "settings" }, 1280, 900, "light", "no-preference")
  let importedId = ""
  try {
    await installResourceProbe(loaded.frame)
    await check("video-fixture-rejects-incomplete-out-of-order-and-signature", async () => {
      const begin = await call(loaded.frame, "gallery_add", { op: "video_begin", name: "broken.webm", mime: "video/webm", size: 8, thumb: fixture.source.thumb, poster: fixture.source.poster })
      await callReject(loaded.frame, "gallery_add", { op: "video_commit", upload_id: begin.upload_id }, "video_upload_incomplete")
      await callReject(loaded.frame, "gallery_add", { op: "video_chunk", upload_id: begin.upload_id, chunk_index: 1, data_b64: "AAAAAAAAAAA=" }, "video_chunk_out_of_order")
      await call(loaded.frame, "gallery_add", { op: "video_chunk", upload_id: begin.upload_id, chunk_index: 0, data_b64: "AAAAAAAAAAA=" })
      await callReject(loaded.frame, "gallery_add", { op: "video_commit", upload_id: begin.upload_id }, "video_signature_invalid")
      await call(loaded.frame, "gallery_add", { op: "video_abort", upload_id: begin.upload_id })
      assert.equal(await loaded.page.evaluate(() => Object.keys(window.qaFixture.uploads).length), 0)
      assert.equal(await loaded.page.evaluate(() => window.qaFixture.gallery.length), 4)
    })
    await check("video-old-image-and-real-looping-pixel-playback", async () => {
      await pick(loaded.frame, "qa-wallpaper")
      await loaded.frame.waitForSelector(".tm-bg-image")
      await pick(loaded.frame, "qa-video")
      const state = await assertMoving(loaded.frame)
      assert(state.currentSrc.startsWith("blob:"), "Stored media must be reconstructed into a temporary Blob URL")
      assert.equal(await loaded.page.evaluate(() => window.qaFixture.appearance.bg_id), "", "Preview implicitly saved wallpaper")
      const reads = await loaded.page.evaluate(() => window.qaMessages.filter(message => message.method === "call" && message.payload.actionId === "get_gallery_image" && message.payload.args.item_id === "qa-video").map(message => message.payload.args))
      assert(reads.some(args => args.chunk_index === 0 && Number(args.chunk_count) > 1))
      assert.equal(reads.filter(args => args.chunk_index >= 0).reduce((sum, args) => sum + Number(args.chunk_count || 1), 0),
        fixture.chunkCount, "Download did not cover every chunk through bounded batch transport")
      await loaded.frame.locator(VIDEO).evaluate(video => {
        window.qaLoopSeen = false
        let previous = video.currentTime
        const detect = () => {
          if (video.currentTime < previous - 0.5) {
            window.qaLoopSeen = true
            video.removeEventListener("timeupdate", detect)
          }
          previous = video.currentTime
        }
        video.addEventListener("timeupdate", detect)
      })
      await loaded.frame.waitForFunction(() => window.qaLoopSeen, null, { timeout: 6500 })
      const looped = await videoSnapshot(loaded.frame)
      assert(!looped.paused, "Video did not actually loop at end of media")
    })
    await check("video-five-second-refresh-keeps-node-and-playback", async () => {
      await loaded.frame.locator(VIDEO).evaluate(video => { window.qaPlayingNode = video })
      const before = await loaded.page.evaluate(() => window.qaRefreshes)
      await loaded.page.waitForFunction(value => window.qaRefreshes > value, before, { timeout: 6500 })
      assert(await loaded.frame.locator(VIDEO).evaluate(video => video === window.qaPlayingNode), "Hosted refresh replaced the video element")
      await assertMoving(loaded.frame)
    })
    await check("video-filters-mask-and-poster-are-independent-siblings", async () => {
      await loaded.frame.locator(".tm-appearance-adjust-summary").click()
      const slider = loaded.frame.locator('.tm-appearance-adjust input[type="range"]')
      for (const [index, value] of [[0, "7"], [2, "75"], [3, "130"]]) {
        await slider.nth(index).evaluate((element, next) => {
          element.value = next
          element.dispatchEvent(new Event("input", { bubbles: true }))
        }, value)
      }
      const layers = await loaded.frame.locator(".tm-bg").evaluate(root => {
        const image = root.querySelector(".tm-bg-image")
        const video = root.querySelector(".tm-bg-video")
        const mask = root.querySelector(".tm-bg-dim")
        return {
          siblings: image.parentElement === root && video.parentElement === root && mask.parentElement === root,
          wrapper: getComputedStyle(root).filter,
          image: getComputedStyle(image).filter,
          video: getComputedStyle(video).filter,
          mask: getComputedStyle(mask).filter,
          opacity: getComputedStyle(mask).opacity,
        }
      })
      assert(layers.siblings)
      assert.equal(layers.wrapper, "none")
      assert.equal(layers.mask, "none")
      assert.equal(layers.opacity, "0.4")
      assert.equal(layers.video, layers.image)
      assert(layers.video.includes("blur(7px)") && layers.video.includes("brightness(0.75)") && layers.video.includes("saturate(1.3)"))
      await loaded.frame.getByRole("button", { name: sources.messages.en["panel.appearance.revert"], exact: true }).click()
      await pick(loaded.frame, "qa-video")
      await assertMoving(loaded.frame)
      await loaded.frame.locator(".tm-appearance-adjust-summary").click()
    })
    await check("video-preview-revert-save-and-reopen", async () => {
      await loaded.frame.getByRole("button", { name: sources.messages.en["panel.appearance.revert"], exact: true }).click()
      await loaded.frame.waitForSelector(".tm-bg", { state: "detached" })
      await pick(loaded.frame, "qa-video")
      await assertMoving(loaded.frame)
      await loaded.frame.locator(".tm-save button").click()
      await loaded.page.waitForFunction(() => window.qaFixture.appearance.bg_id === "qa-video")
      const beforeReopen = await loaded.page.evaluate(() => window.qaMessages.length)
      await reopen(loaded)
      assert(await loaded.page.evaluate(offset => window.qaMessages.slice(offset).some(message =>
        message.payload?.actionId === "get_gallery_image"
        && Number(message.payload.args?.chunk_count) > 1), beforeReopen),
      "Reopening a saved video must use the bounded batch-read request")
      await assertMoving(loaded.frame)
      assert.equal(await loaded.page.evaluate(() => window.qaFixture.appearance.bg_id), "qa-video")
    }, { persistence: "A remounted real panel restores fixture-owned bytes/settings; this is isolated API persistence, not Steam Store verification" })
    await check("video-motion-switch-and-runtime-reduced-motion", async () => {
      await loaded.frame.locator(".tm-appearance-adjust-summary").click()
      await loaded.frame.locator(VIDEO).evaluate(video => { window.qaMotionNode = video })
      await loaded.frame.locator(MOTION).uncheck()
      await assertPaused(loaded.frame)
      assert(await loaded.frame.locator(VIDEO).evaluate(video => video === window.qaMotionNode), "Motion preference discarded the video playback position")
      assert.equal(await loaded.frame.locator(VIDEO).evaluate(video => getComputedStyle(video).opacity), "0", "Paused video must show its fixed cover rather than an arbitrary frozen frame")
      assert.equal(await loaded.frame.locator(".tm-bg").count(), 1, "Turning motion off removed the static wallpaper fallback")
      await loaded.frame.locator(".tm-save button").click()
      await loaded.page.waitForFunction(() => window.qaFixture.appearance.motion === false)
      await reopen(loaded)
      await assertPaused(loaded.frame)
      await loaded.frame.locator(".tm-appearance-adjust-summary").click()
      await loaded.frame.locator(MOTION).check()
      await assertMoving(loaded.frame)
      await loaded.page.emulateMedia({ reducedMotion: "reduce" })
      await assertPaused(loaded.frame)
      await loaded.page.emulateMedia({ reducedMotion: "no-preference" })
      await assertMoving(loaded.frame)
    })
    await check("video-synthetic-document-hidden-pause-and-visible-resume", async () => {
      await loaded.frame.evaluate(() => {
        Object.defineProperty(document, "hidden", { configurable: true, get: () => true })
        document.dispatchEvent(new Event("visibilitychange"))
      })
      await assertPaused(loaded.frame)
      await loaded.frame.evaluate(() => {
        delete document.hidden
        document.dispatchEvent(new Event("visibilitychange"))
      })
      await assertMoving(loaded.frame)
    }, { synthetic: true, limitation: "Tests child-document visibility handlers; it does not claim an operating-system background or Steam panel transition" })
    await check("video-real-parent-iframe-css-hiding-suspends-and-resumes", async () => {
      await loaded.page.locator("#hosted").evaluate(element => { element.style.display = "none" })
      await loaded.frame.waitForTimeout(1250)
      await assertPaused(loaded.frame)
      await loaded.page.locator("#hosted").evaluate(element => { element.style.display = "block" })
      await assertMoving(loaded.frame)
    }, { realBrowser: true, limitation: "Actual CSS hiding of the isolated iframe, not a Steam tab or operating-system window transition" })
    await check("video-native-file-import-sends-real-production-chunks", async () => {
      const before = await loaded.page.evaluate(() => window.qaVideoCalls.length)
      await loaded.frame.locator(VIDEO_INPUT).setInputFiles(fixture.assetPath)
      await loaded.page.waitForFunction(() => window.qaFixture.gallery.some(item => item.id.startsWith("qa-video-upload-")), null, { timeout: 25000 })
      const record = await loaded.page.evaluate(offset => {
        const calls = window.qaVideoCalls.slice(offset)
        const item = window.qaFixture.gallery.find(item => item.id.startsWith("qa-video-upload-"))
        return { calls, item, video: window.qaFixture.videos[item.id], uploadCount: Object.keys(window.qaFixture.uploads).length }
      }, before)
      importedId = record.item.id
      assert.equal(record.item.kind, "video")
      assert.equal(await loaded.frame.locator("#tm-media-tab-video").getAttribute("aria-selected"), "true")
      assert.equal(record.item.size, fixture.size)
      assert.equal(record.uploadCount, 0)
      assert.equal(record.calls[0].op, "video_begin")
      const parts = record.calls.filter(value => value.op === "video_chunk")
      assert.equal(parts.length, fixture.chunkCount)
      assert.deepEqual(parts.map(value => value.chunk_index), parts.map((_value, index) => index))
      assert(parts.every(value => Buffer.from(value.data_b64, "base64").length <= CHUNK_BYTES))
      const stored = Buffer.concat(record.video.chunks.map(value => Buffer.from(value, "base64")))
      assert.equal(createHash("sha256").update(stored).digest("hex"), fixture.sha256, "Uploaded media bytes changed")
      assert.equal(record.calls.at(-1).op, "video_commit")
      assert.equal(await loaded.page.evaluate(() => window.qaFixture.appearance.bg_id), "qa-video", "Import replaced the saved wallpaper")
      await pick(loaded.frame, importedId)
      await assertMoving(loaded.frame)
      const importedReads = await loaded.page.evaluate(id => window.qaMessages.filter(message => message.method === "call"
        && message.payload.actionId === "get_gallery_image" && message.payload.args.item_id === id).length, importedId)
      assert.equal(importedReads, 0, "Selecting the imported File requested metadata or discarded its reusable local video")
    })
    await check("video-failed-chunk-aborts-without-committing-gallery", async () => {
      const before = await loaded.page.evaluate(() => {
        window.qaFailVideoOp = "video_chunk"
        window.qaFailVideoChunk = 1
        return { count: window.qaFixture.gallery.length, calls: window.qaVideoCalls.length }
      })
      await loaded.frame.locator(VIDEO_INPUT).setInputFiles(fixture.assetPath)
      await loaded.frame.waitForSelector('.neko-toast[data-tone="danger"]', { timeout: 25000 })
      await loaded.page.waitForFunction(() => Object.keys(window.qaFixture.uploads).length === 0, null, { timeout: 5000 })
      assert.equal(await loaded.page.evaluate(() => window.qaFixture.gallery.length), before.count)
      const ops = await loaded.page.evaluate(offset => window.qaVideoCalls.slice(offset).map(value => value.op), before.calls)
      assert(ops.includes("video_abort"))
      assert(!ops.includes("video_commit"), "Failed chunk was committed")
      await assertMoving(loaded.frame)
    })
    await check("video-cancel-during-upload-aborts-and-preserves-gallery", async () => {
      const before = await loaded.page.evaluate(() => {
        window.qaDelays["gallery_add:video_chunk:0"] = 900
        return { count: window.qaFixture.gallery.length, calls: window.qaVideoCalls.length }
      })
      await loaded.frame.locator(VIDEO_INPUT).setInputFiles(fixture.assetPath)
      await loaded.page.waitForFunction(offset => window.qaVideoCalls.slice(offset).some(value => value.op === "video_chunk"), before.calls, { timeout: 15000 })
      await loaded.frame.locator(".tm-video-progress button").click()
      await loaded.page.waitForFunction(() => Object.keys(window.qaFixture.uploads).length === 0, null, { timeout: 5000 })
      assert.equal(await loaded.page.evaluate(() => window.qaFixture.gallery.length), before.count)
      const ops = await loaded.page.evaluate(offset => {
        delete window.qaDelays["gallery_add:video_chunk:0"]
        return window.qaVideoCalls.slice(offset).map(value => value.op)
      }, before.calls)
      assert(ops.includes("video_abort"))
      assert(!ops.includes("video_commit"))
      assert.equal(ops.filter(value => value === "video_chunk").length, 1, "Cancellation continued uploading later chunks")
      await assertMoving(loaded.frame)
    })
    for (const [name, acknowledgement] of [
      ["rejected", { chunk_index: 0, accepted: false }],
      ["wrong-index", { chunk_index: 1, accepted: true }],
    ]) {
      await check(`video-malformed-${name}-chunk-ack-aborts`, async () => {
        const before = await loaded.page.evaluate(value => {
          window.qaVideoAckOverrides[0] = value
          return { count: window.qaFixture.gallery.length, calls: window.qaVideoCalls.length }
        }, acknowledgement)
        await loaded.frame.locator(VIDEO_INPUT).setInputFiles(fixture.assetPath)
        await loaded.page.waitForFunction(offset => window.qaVideoCalls.slice(offset).some(value => value.op === "video_abort"), before.calls, { timeout: 15000 })
        const result = await loaded.page.evaluate(offset => {
          delete window.qaVideoAckOverrides[0]
          return { count: window.qaFixture.gallery.length, calls: window.qaVideoCalls.slice(offset), sessions: Object.keys(window.qaFixture.uploads).length }
        }, before.calls)
        assert.equal(result.count, before.count)
        assert.equal(result.sessions, 0)
        assert(result.calls.some(value => value.op === "video_chunk"), "Malformed acknowledgement did not exercise the chunk path")
        assert(!result.calls.some(value => value.op === "video_commit"), "Rejected or mismatched acknowledgement was committed")
      })
    }
    await check("video-wallpaper-engine-directory-real-file-import", async () => {
      const directory = join(output, "we-project")
      mkdirSync(directory, { recursive: true })
      writeFileSync(join(directory, "project.json"), JSON.stringify({ type: "video", file: "wallpaper.webm", title: "QA WE motion" }))
      writeFileSync(join(directory, "wallpaper.webm"), readFileSync(fixture.assetPath))
      const before = await loaded.page.evaluate(() => window.qaFixture.gallery.length)
      await openImport(loaded.frame, "directory")
      await loaded.frame.locator(DIRECTORY_INPUT).setInputFiles(directory)
      await loaded.frame.waitForSelector(".tm-wallpaper-candidate")
      assert.equal(await loaded.frame.locator(".tm-wallpaper-candidate").count(), 1)
      assert.equal(await loaded.page.evaluate(() => window.qaFixture.gallery.length), before, "Directory selection imported without explicit confirmation")
      await loaded.frame.locator(".tm-wallpaper-candidate button").click()
      await loaded.page.waitForFunction(count => window.qaFixture.gallery.length > count, before, { timeout: 25000 })
      const item = await loaded.page.evaluate(() => window.qaFixture.gallery.at(-1))
      assert.equal(item.kind, "video")
      assert.equal(item.name, "QA WE motion")
      assert.equal(item.size, fixture.size)
      await pick(loaded.frame, item.id)
      await assertMoving(loaded.frame)
    })
    await check("video-delete-current-releases-blob-and-returns-default", async () => {
      await pick(loaded.frame, importedId)
      await assertMoving(loaded.frame)
      await loaded.frame.locator(".tm-save button").click()
      await loaded.page.waitForFunction(id => window.qaFixture.appearance.bg_id === id, importedId)
      const current = await videoSnapshot(loaded.frame)
      await loaded.frame.locator(`.tm-gallery-tile[data-item-id="${importedId}"] .tm-gallery-del`).click()
      await loaded.frame.getByRole("button", { name: sources.messages.en["panel.confirm"], exact: true }).last().click()
      await loaded.frame.waitForSelector(".neko-modal", { state: "detached" })
      await loaded.frame.waitForSelector(".tm-bg", { state: "detached" })
      assert.equal(await loaded.page.evaluate(() => window.qaFixture.appearance.bg_id), "")
      assert.equal(await loaded.page.evaluate(id => Boolean(window.qaFixture.videos[id]), importedId), false)
      assert(await loaded.frame.evaluate(url => window.qaVideoResources.revoked.includes(url), current.currentSrc), "Deleting current wallpaper leaked its Blob URL")
    })
    await check("video-unmount-pauses-and-releases-all-owned-urls", async () => {
      await pick(loaded.frame, "qa-video")
      await assertMoving(loaded.frame)
      await loaded.frame.evaluate(() => window.NekoUiKit.render(null, document.getElementById("root")))
      await loaded.frame.waitForTimeout(150)
      const state = await loaded.frame.evaluate(() => ({
        urls: window.qaVideoResources.created.filter(value => value.type.startsWith("video/")).map(value => value.url),
        revoked: window.qaVideoResources.revoked,
        videos: window.qaVideoResources.elements.map(video => ({ paused: video.paused, src: video.getAttribute("src") })),
      }))
      assert(state.urls.length > 0)
      assert(state.urls.every(url => state.revoked.includes(url)), "Panel unmount leaked a created video Blob URL")
      assert(state.videos.every(video => video.paused), "Panel unmount left a media decoder playing")
    })
  } finally { await loaded.context.close() }

  const restoring = await loadPage(browser, origin, { locale: "en", wallpaper: "none", video: "true", tab: "settings" }, 1280, 900, "light", "no-preference")
  try {
    await check("video-reopen-shows-poster-and-progress-before-delayed-chunk", async () => {
      await restoring.page.evaluate(() => {
        window.qaFixture.appearance.bg_id = "qa-video"
        window.qaHoldReplies["get_gallery_image:qa-video:0"] = true
      })
      await reopen(restoring)
      await restoring.page.waitForFunction(() => window.qaMessages.some(message => message.payload?.actionId === "get_gallery_image"
        && message.payload.args.item_id === "qa-video" && message.payload.args.chunk_index === 0))
      const request = await restoring.page.evaluate(() => window.qaMessages.find(message => message.payload?.actionId === "get_gallery_image"
        && message.payload.args.item_id === "qa-video" && message.payload.args.chunk_index === 0).requestId)
      await restoring.frame.waitForSelector(".tm-video-loading")
      await restoring.frame.waitForSelector(".tm-bg-image")
      const poster = await restoring.page.evaluate(() => window.qaFixture.videos["qa-video"].poster)
      assert((await restoring.frame.locator(".tm-bg-image").evaluate(element => element.style.backgroundImage)).includes(poster),
        "Saved video stayed blank until the complete media download")
      assert.equal(await restoring.frame.locator(".tm-video-loading").getAttribute("role"), "status")
      assert.equal(await restoring.page.evaluate(id => window.qaResolved.some(value => value.requestId === id), request), false,
        "Poster/progress was checked only after the delayed chunk arrived")
      assert.equal(await restoring.frame.locator(VIDEO_INPUT).isDisabled(), false, "Background loading disabled unrelated imports")
      await openStorage(restoring.frame)
      await restoring.frame.getByRole("button", { name: sources.messages.en["panel.appearance.clearMedia"], exact: true }).click()
      await restoring.frame.getByRole("button", { name: sources.messages.en["panel.cancel"], exact: true }).last().click()
      await capture(restoring, "video-reopen-poster-loading")
      await restoring.page.evaluate(() => window.qaReleaseHeldReplies("get_gallery_image:qa-video:0"))
      await assertMoving(restoring.frame)
      await restoring.frame.waitForSelector(".tm-video-loading", { state: "detached" })
    }, { realBrowser: true, race: "A remounted saved video receives metadata immediately; its first production-sized chunk reply is held until the real poster/status screenshot and control checks finish." })
    await check("video-switch-during-delayed-read-does-not-revive-background", async () => {
      const start = await restoring.page.evaluate(() => {
        window.qaFixture.appearance.bg_id = "qa-video"
        window.qaHoldReplies["get_gallery_image:qa-video:0"] = true
        return window.qaMessages.length
      })
      await reopen(restoring)
      await restoring.page.waitForFunction(offset => window.qaMessages.slice(offset).some(message => message.payload?.actionId === "get_gallery_image"
        && message.payload.args.item_id === "qa-video" && message.payload.args.chunk_index === 0), start)
      const request = await restoring.page.evaluate(offset => window.qaMessages.slice(offset).find(message => message.payload?.actionId === "get_gallery_image"
        && message.payload.args.item_id === "qa-video" && message.payload.args.chunk_index === 0).requestId, start)
      await pick(restoring.frame, "qa-wallpaper")
      await restoring.frame.waitForSelector('.tm-gallery-tile[data-item-id="qa-wallpaper"].tm-gallery-active')
      await restoring.page.evaluate(() => window.qaReleaseHeldReplies("get_gallery_image:qa-video:0"))
      await restoring.page.waitForFunction(id => window.qaResolved.some(value => value.requestId === id), request, { timeout: 6000 })
      await restoring.frame.waitForTimeout(180)
      assert(await restoring.page.evaluate(id => window.qaCancelledRequests.some(value => value.requestId === id), request),
        "Switching wallpapers did not cancel its active hosted read")
      assert.equal(await restoring.frame.locator(VIDEO).count(), 0, "Late video chunk replaced the new image selection")
      assert.equal(await restoring.frame.locator(".tm-video-loading").count(), 0, "Cancelled background retained its loading indicator")
      assert((await restoring.frame.locator(".tm-bg-image").evaluate(element => element.style.backgroundImage))
        .includes(await restoring.page.evaluate(() => window.qaFixture.images["qa-wallpaper"])))
      const laterReads = await restoring.page.evaluate(({ offset, first }) => {
        const cancelledAt = window.qaCancelledRequests.find(value => value.requestId === first).at
        return window.qaMessages.slice(offset).filter(message => message.payload?.actionId === "get_gallery_image"
          && message.payload.args.item_id === "qa-video" && message.payload.args.chunk_index >= 0
          && message.receivedAt > cancelledAt).length
      }, { offset: start, first: request })
      assert.equal(laterReads, 0, "Cancelled download continued sending later chunks")
    }, { realBrowser: true, race: "A written first-read result is delivered after a different wallpaper is selected; it cannot resume download or revive the old video." })
  } finally { await restoring.context.close() }

  for (const mutation of ["switch", "clear", "unmount"]) {
    const pending = await loadPage(browser, origin, { locale: "en", wallpaper: "none", video: "true", tab: "settings" }, 1280, 900, "light", "no-preference")
    try {
      await installResourceProbe(pending.frame)
      await check(`video-unselected-import-cache-released-on-${mutation}`, async () => {
        let delayedRead = ""
        if (mutation !== "switch") {
          const start = await pending.page.evaluate(() => {
            window.qaFixture.appearance.bg_id = "qa-video"
            window.qaHoldReplies["get_gallery_image:qa-video:0"] = true
            return window.qaMessages.length
          })
          await reopen(pending)
          await pending.page.waitForFunction(offset => window.qaMessages.slice(offset).some(message => message.payload?.actionId === "get_gallery_image"
            && message.payload.args.item_id === "qa-video" && message.payload.args.chunk_index === 0), start)
          delayedRead = await pending.page.evaluate(offset => window.qaMessages.slice(offset).find(message => message.payload?.actionId === "get_gallery_image"
            && message.payload.args.item_id === "qa-video" && message.payload.args.chunk_index === 0).requestId, start)
        }
        await pending.frame.locator(VIDEO_INPUT).setInputFiles(fixture.assetPath)
        await pending.page.waitForFunction(() => window.qaFixture.gallery.some(item => item.id.startsWith("qa-video-upload-")), null, { timeout: 15000 })
        await pending.frame.waitForFunction(() => !document.querySelector("input.tm-wallpaper-video-input").disabled)
        const owned = await pending.frame.evaluate(() => window.qaVideoResources.created
          .filter(value => value.type.startsWith("video/") && !window.qaVideoResources.revoked.includes(value.url)).map(value => value.url))
        assert.equal(owned.length, 1, "Import did not retain exactly one unselected reusable File URL")
        assert.equal(await pending.frame.locator(VIDEO).count(), 0, "Import implicitly selected and played its pending cache")
        if (mutation === "switch") {
          await pick(pending.frame, "qa-wallpaper")
          await pending.frame.waitForSelector('.tm-gallery-tile[data-item-id="qa-wallpaper"].tm-gallery-active')
        } else if (mutation === "clear") {
          await openStorage(pending.frame)
          await pending.frame.getByRole("button", { name: sources.messages.en["panel.appearance.clearMedia"], exact: true }).click()
          await pending.frame.getByRole("button", { name: sources.messages.en["panel.confirm"], exact: true }).last().click()
          await pending.page.waitForFunction(() => window.qaFixture.gallery.length === 0)
        } else await pending.frame.evaluate(() => window.NekoUiKit.render(null, document.getElementById("root")))
        await pending.frame.waitForFunction(url => window.qaVideoResources.revoked.includes(url), owned[0])
        if (delayedRead) {
          await pending.page.evaluate(() => window.qaReleaseHeldReplies("get_gallery_image:qa-video:0"))
          await pending.page.waitForFunction(id => window.qaResolved.some(value => value.requestId === id), delayedRead, { timeout: 6500 })
          await pending.frame.waitForTimeout(180)
          assert(await pending.page.evaluate(id => window.qaCancelledRequests.some(value => value.requestId === id), delayedRead),
            "Clear or unmount did not cancel its active hosted read")
          const laterChunks = await pending.page.evaluate(first => {
            const cancelledAt = window.qaCancelledRequests.find(value => value.requestId === first).at
            return window.qaMessages.filter(message => message.payload?.actionId === "get_gallery_image"
              && message.payload.args.item_id === "qa-video" && message.payload.args.chunk_index >= 0
              && message.receivedAt > cancelledAt).length
          }, delayedRead)
          assert.equal(laterChunks, 0, "Clear or unmount let a delayed background request resume")
          assert.equal(await pending.frame.locator(".tm-bg").count(), 0, "Late read revived the cleared or unmounted background")
        }
        const live = await pending.frame.evaluate(() => window.qaVideoResources.created
          .filter(value => value.type.startsWith("video/") && !window.qaVideoResources.revoked.includes(value.url)).map(value => value.url))
        assert.deepEqual(live, [], "Unused imported video URL survived its ownership boundary")
        assert.equal(await pending.frame.locator(VIDEO).count(), 0)
      }, { realBrowser: true, ownership: "The imported File is unselected and retained once; another image selection, clear, or unmount releases its URL. Clear/unmount also receive an old background chunk after cancellation and must not resume it." })
    } finally { await pending.context.close() }
  }

  const replacedPending = await loadPage(browser, origin, { locale: "en", wallpaper: "none", video: "true", tab: "settings" }, 1280, 900, "light", "no-preference")
  try {
    await installResourceProbe(replacedPending.frame)
    await check("video-second-unselected-import-replaces-first-file-cache", async () => {
      const imported = []
      for (let index = 0; index < 2; index += 1) {
        const before = await replacedPending.page.evaluate(() => window.qaFixture.gallery.length)
        await replacedPending.frame.locator(VIDEO_INPUT).setInputFiles(fixture.assetPath)
        await replacedPending.page.waitForFunction(count => window.qaFixture.gallery.length === count + 1, before, { timeout: 15000 })
        await replacedPending.frame.waitForFunction(() => !document.querySelector("input.tm-wallpaper-video-input").disabled)
        const id = await replacedPending.page.evaluate(() => window.qaFixture.gallery.at(-1).id)
        const live = await replacedPending.frame.evaluate(() => window.qaVideoResources.created
          .filter(value => value.type.startsWith("video/") && !window.qaVideoResources.revoked.includes(value.url)).map(value => value.url))
        assert.equal(live.length, 1, "Multiple unselected imported video Files remained in memory")
        imported.push({ id, url: live[0] })
      }
      assert.notEqual(imported[0].url, imported[1].url)
      assert(await replacedPending.frame.evaluate(url => window.qaVideoResources.revoked.includes(url), imported[0].url),
        "Second unselected import did not release its predecessor")
      assert.equal(await replacedPending.frame.locator(VIDEO).count(), 0, "Replacing pending cache implicitly selected a video")
      await pick(replacedPending.frame, imported[1].id)
      await assertMoving(replacedPending.frame)
      const reads = await replacedPending.page.evaluate(id => window.qaMessages.filter(message => message.payload?.actionId === "get_gallery_image"
        && message.payload.args.item_id === id).length, imported[1].id)
      assert.equal(reads, 0, "The newest pending File was lost before first selection")
      await replacedPending.frame.evaluate(() => window.NekoUiKit.render(null, document.getElementById("root")))
      assert(await replacedPending.frame.evaluate(url => window.qaVideoResources.revoked.includes(url), imported[1].url),
        "Promoted pending File survived panel unmount")
    }, { realBrowser: true, ownership: "Exactly one unselected imported File is retained; the second import revokes the first, promotes with no host reads, and is revoked on unmount." })
  } finally { await replacedPending.context.close() }

  const clearing = await loadPage(browser, origin, { locale: "en", wallpaper: "none", video: "true", tab: "settings" }, 1280, 900, "light", "no-preference")
  try {
    await installResourceProbe(clearing.frame)
    await check("video-policy-controls-use-persisted-server-budget", async () => {
      const inputs = clearing.frame.locator(".tm-media-policy input")
      assert.equal(await inputs.nth(0).inputValue(), "256")
      assert.equal(await inputs.nth(1).inputValue(), "2048")
      await inputs.nth(0).fill("384")
      await inputs.nth(1).fill("4096")
      await clearing.frame.locator(".tm-media-policy button").click()
      await clearing.page.waitForFunction(() => window.qaFixture.mediaStorage.single_limit_bytes === 384 * 1048576)
      assert.equal(await clearing.page.evaluate(() => window.qaFixture.mediaStorage.total_limit_bytes), 4096 * 1048576)
      assert.equal(await inputs.nth(0).inputValue(), "384")
    })
    await check("video-clear-cancel-preserves-library-and-diaries", async () => {
      await pick(clearing.frame, "qa-video")
      await assertMoving(clearing.frame)
      const before = await clearing.page.evaluate(() => JSON.stringify({ gallery: window.qaFixture.gallery, diary: window.qaFixture.diary, journal: window.qaFixture.journal, review: window.qaFixture.review }))
      await openStorage(clearing.frame)
      await clearing.frame.getByRole("button", { name: sources.messages.en["panel.appearance.clearMedia"], exact: true }).click()
      await clearing.frame.getByRole("button", { name: sources.messages.en["panel.cancel"], exact: true }).last().click()
      assert.equal(await clearing.page.evaluate(() => JSON.stringify({ gallery: window.qaFixture.gallery, diary: window.qaFixture.diary, journal: window.qaFixture.journal, review: window.qaFixture.review })), before)
      await assertMoving(clearing.frame)
    })
    await check("video-clear-failure-does-not-report-success-or-leak-cached-playback", async () => {
      await clearing.page.evaluate(() => { window.qaFailNext = "gallery_remove" })
      await openStorage(clearing.frame)
      await clearing.frame.getByRole("button", { name: sources.messages.en["panel.appearance.clearMedia"], exact: true }).click()
      await clearing.frame.getByRole("button", { name: sources.messages.en["panel.confirm"], exact: true }).last().click()
      await clearing.frame.waitForSelector('.neko-toast[data-tone="danger"]')
      await clearing.frame.waitForSelector(".tm-bg", { state: "detached" })
      assert.equal(await clearing.page.evaluate(() => window.qaFixture.gallery.length), 4, "Failed clear pretended to remove backend media")
      const state = await clearing.frame.evaluate(() => ({
        urls: window.qaVideoResources.created.filter(value => value.type.startsWith("video/")).map(value => value.url),
        revoked: window.qaVideoResources.revoked,
      }))
      assert(state.urls.every(url => state.revoked.includes(url)), "Failed clear kept owned video URLs alive")
      assert(!await clearing.frame.getByText(sources.messages.en["panel.appearance.mediaCleared"], { exact: true }).count(), "Failed clear announced success")
    })
    await check("video-clear-confirm-releases-files-blobs-and-cancels-late-downloads", async () => {
      const directory = join(output, "we-project")
      await openImport(clearing.frame, "directory")
      await clearing.frame.locator(DIRECTORY_INPUT).setInputFiles(directory)
      await clearing.frame.waitForSelector(".tm-wallpaper-candidate")
      const before = await clearing.page.evaluate(() => {
        window.qaDelays["qa-wallpaper-b"] = 850
        return JSON.stringify({ diary: window.qaFixture.diary, journal: window.qaFixture.journal, review: window.qaFixture.review })
      })
      await pick(clearing.frame, "qa-wallpaper-b")
      await clearing.page.waitForFunction(() => window.qaMessages.some(message => message.method === "call" && message.payload.actionId === "get_gallery_image" && message.payload.args.item_id === "qa-wallpaper-b"))
      await openStorage(clearing.frame)
      await clearing.frame.getByRole("button", { name: sources.messages.en["panel.appearance.clearMedia"], exact: true }).click()
      await clearing.frame.getByRole("button", { name: sources.messages.en["panel.confirm"], exact: true }).last().click()
      await clearing.page.waitForFunction(() => window.qaFixture.gallery.length === 0)
      await clearing.frame.waitForTimeout(1200)
      assert.equal(await clearing.frame.locator(".tm-bg").count(), 0, "Late image response resurrected a cleared background")
      assert.equal(await clearing.frame.locator(".tm-wallpaper-candidate").count(), 0, "Cleared panel retained directory File references")
      assert.equal(await clearing.frame.locator(DIRECTORY_INPUT).inputValue(), "")
      assert.equal(await clearing.frame.locator(VIDEO_INPUT).inputValue(), "")
      assert.equal(await clearing.page.evaluate(() => Object.keys(window.qaFixture.images).length + Object.keys(window.qaFixture.videos).length + Object.keys(window.qaFixture.uploads).length), 0)
      assert.equal(await clearing.page.evaluate(() => JSON.stringify({ diary: window.qaFixture.diary, journal: window.qaFixture.journal, review: window.qaFixture.review })), before)
      await capture(clearing, "video-clear-default-and-storage")
    })
  } finally { await clearing.context.close() }

  for (const mutation of ["add", "clear"]) {
    const delayedSnapshot = await loadPage(browser, origin, { locale: "en", wallpaper: "none", video: "true", tab: "settings" }, 1280, 900, "light", "no-preference")
    try {
      await check(`video-late-initial-gallery-snapshot-cannot-overwrite-${mutation}`, async () => {
        const offset = await delayedSnapshot.page.evaluate(mode => {
          window.qaDelays.get_panel_gallery = 7000
          if (mode === "clear") window.qaFixture.appearance.bg_id = "qa-video"
          return window.qaMessages.length
        }, mutation)
        await reopen(delayedSnapshot)
        const snapshotRequest = await delayedSnapshot.page.evaluate(start => window.qaMessages.slice(start)
          .find(message => message.method === "call" && message.payload.actionId === "get_panel_gallery").requestId, offset)
        await delayedSnapshot.frame.locator(VIDEO_INPUT).setInputFiles(fixture.assetPath)
        await delayedSnapshot.page.waitForFunction(() => window.qaFixture.gallery.some(item => item.id.startsWith("qa-video-upload-")), null, { timeout: 5000 })
        const addedId = await delayedSnapshot.page.evaluate(() => window.qaFixture.gallery.find(item => item.id.startsWith("qa-video-upload-")).id)
        await delayedSnapshot.frame.waitForSelector(`.tm-gallery-tile[data-item-id="${addedId}"]`)
        assert.equal(await delayedSnapshot.page.evaluate(id => window.qaResolved.some(value => value.requestId === id), snapshotRequest), false,
          "The initial gallery response arrived before the mutation; this did not exercise the race")
        if (mutation === "clear") {
          await openStorage(delayedSnapshot.frame)
          await delayedSnapshot.frame.getByRole("button", { name: sources.messages.en["panel.appearance.clearMedia"], exact: true }).click()
          await delayedSnapshot.frame.getByRole("button", { name: sources.messages.en["panel.confirm"], exact: true }).last().click()
          await delayedSnapshot.page.waitForFunction(() => window.qaFixture.gallery.length === 0)
          await delayedSnapshot.frame.waitForSelector(`.tm-gallery-tile[data-item-id="${addedId}"]`, { state: "detached" })
        }
        await delayedSnapshot.page.waitForFunction(id => window.qaResolved.some(value => value.requestId === id), snapshotRequest, { timeout: 10000 })
        await delayedSnapshot.frame.waitForTimeout(180)
        assert.equal(await delayedSnapshot.frame.locator(".tm-gallery-tile[data-item-id]").count(), mutation === "clear" ? 0 : 2,
          "A late initial snapshot resurrected cleared media or discarded the new wallpaper")
        if (mutation === "clear") {
          assert.equal(await delayedSnapshot.frame.locator(".tm-bg").count(), 0, "A late saved appearance resurrected the cleared background")
          assert.equal(await delayedSnapshot.page.evaluate(() => Object.keys(window.qaFixture.videos).length + Object.keys(window.qaFixture.uploads).length), 0)
        } else {
          assert.equal(await delayedSnapshot.frame.locator(`.tm-gallery-tile[data-item-id="${addedId}"]`).count(), 1)
          await pick(delayedSnapshot.frame, addedId)
          await assertMoving(delayedSnapshot.frame)
        }
        await capture(delayedSnapshot, `video-late-snapshot-${mutation}`)
      }, { realBrowser: true, race: "The actual remounted panel receives a fixture snapshot captured before an import/clear but delivered after it" })
    } finally { await delayedSnapshot.context.close() }
  }

  const deletingOther = await loadPage(browser, origin, { locale: "en", wallpaper: "none", video: "true", tab: "settings" }, 1280, 900, "light", "no-preference")
  try {
    await check("video-delete-other-item-restarts-selected-inflight-download", async () => {
      const offset = await deletingOther.page.evaluate(() => {
        window.qaFixture.appearance.bg_id = "qa-video"
        window.qaDelays["get_gallery_image:qa-video:0"] = 1600
        return window.qaMessages.length
      })
      await pick(deletingOther.frame, "qa-video")
      await deletingOther.page.waitForFunction(start => window.qaMessages.slice(start).some(message => message.method === "call"
        && message.payload.actionId === "get_gallery_image" && message.payload.args.item_id === "qa-video" && message.payload.args.chunk_index === 0), offset)
      await selectMedia(deletingOther.frame, "image")
      await deletingOther.frame.locator('.tm-gallery-tile[data-item-id="qa-wallpaper-b"] .tm-gallery-del').click()
      await deletingOther.frame.getByRole("button", { name: sources.messages.en["panel.confirm"], exact: true }).last().click()
      await deletingOther.frame.waitForSelector('.tm-gallery-tile[data-item-id="qa-wallpaper-b"]', { state: "detached" })
      await deletingOther.page.waitForFunction(start => window.qaMessages.slice(start).filter(message => message.method === "call"
        && message.payload.actionId === "get_gallery_image" && message.payload.args.item_id === "qa-video"
        && message.payload.args.chunk_index === 0).length >= 2, offset, { timeout: 5000 })
      const requestCount = await deletingOther.page.evaluate(start => window.qaMessages.slice(start).filter(message => message.method === "call"
        && message.payload.actionId === "get_gallery_image" && message.payload.args.item_id === "qa-video"
        && message.payload.args.chunk_index === 0).length, offset)
      assert(requestCount >= 2, "Deleting another item did not restart the selected video's invalidated download")
      await assertMoving(deletingOther.frame)
      assert.equal(await deletingOther.page.evaluate(() => window.qaFixture.appearance.bg_id), "qa-video")
      await selectMedia(deletingOther.frame, "video")
      assert.equal(await deletingOther.frame.locator('.tm-gallery-tile[data-item-id="qa-video"].tm-gallery-active').count(), 1)
      await capture(deletingOther, "video-delete-other-during-download")
    }, { realBrowser: true, race: "Production-sized selected-video chunk is delayed while an unrelated image is deleted through the real confirmation dialog" })
  } finally { await deletingOther.context.close() }

  const uploadClearing = await loadPage(browser, origin, { locale: "en", wallpaper: "none", video: "true", tab: "settings" }, 1280, 900, "light", "no-preference")
  try {
    await check("video-clear-awaiting-upload-keeps-imports-disabled-until-clear-reply", async () => {
      const offset = await uploadClearing.page.evaluate(() => {
        window.qaDelays["gallery_add:video_chunk:0"] = 3500
        window.qaDelays["gallery_add:video_abort:"] = 500
        window.qaDelays["gallery_remove:clear_media:"] = 2200
        return window.qaMessages.length
      })
      // Confirm is deliberately left open while native file selection starts.
      await openStorage(uploadClearing.frame)
      await uploadClearing.frame.getByRole("button", { name: sources.messages.en["panel.appearance.clearMedia"], exact: true }).click()
      await uploadClearing.frame.locator(VIDEO_INPUT).setInputFiles(fixture.assetPath)
      await uploadClearing.page.waitForFunction(start => window.qaMessages.slice(start).some(message => message.method === "call"
        && message.payload.actionId === "gallery_add" && message.payload.args.op === "video_chunk"), offset)
      const chunkRequest = await uploadClearing.page.evaluate(start => window.qaMessages.slice(start).find(message => message.method === "call"
        && message.payload.actionId === "gallery_add" && message.payload.args.op === "video_chunk").requestId, offset)
      await uploadClearing.frame.getByRole("button", { name: sources.messages.en["panel.confirm"], exact: true }).last().click()
      await uploadClearing.page.waitForFunction(start => window.qaMessages.slice(start).some(message => message.method === "call"
        && message.payload.actionId === "gallery_remove" && message.payload.args.op === "clear_media"), offset)
      const clearRequest = await uploadClearing.page.evaluate(start => window.qaMessages.slice(start).find(message => message.method === "call"
        && message.payload.actionId === "gallery_remove" && message.payload.args.op === "clear_media").requestId, offset)
      for (let probe = 0; probe < 4; probe += 1) {
        assert.equal(await uploadClearing.page.evaluate(id => window.qaResolved.some(value => value.requestId === id), clearRequest), false,
          "Clear already finished before the disabled-control probe")
        assert.equal(await uploadClearing.frame.locator(VIDEO_INPUT).isDisabled(), true, "Upload finally reopened video import while clear was pending")
        assert.equal(await uploadClearing.frame.locator(DIRECTORY_INPUT).isDisabled(), true, "Upload finally reopened directory import while clear was pending")
        assert.equal(await uploadClearing.frame.locator('.tm-media-storage button[data-tone="danger"]').isDisabled(), true)
        assert.equal(await uploadClearing.frame.locator(".neko-image-upload").evaluate(element => getComputedStyle(element).pointerEvents), "none",
          "Image import became pointer-active while clear was pending")
        await uploadClearing.frame.waitForTimeout(220)
      }
      await uploadClearing.page.waitForFunction(id => window.qaResolved.some(value => value.requestId === id), clearRequest)
      await uploadClearing.frame.waitForFunction(() => !document.querySelector("input.tm-wallpaper-video-input").disabled)
      await uploadClearing.page.waitForFunction(id => window.qaResolved.some(value => value.requestId === id), chunkRequest)
      await uploadClearing.frame.waitForTimeout(160)
      const calls = await uploadClearing.page.evaluate(start => window.qaMessages.slice(start).filter(message => message.method === "call"
        && message.payload.actionId === "gallery_add").map(message => message.payload.args.op), offset)
      assert(calls.includes("video_abort"), "Clear did not await cancellation and upload cleanup")
      assert.equal(calls.filter(value => value === "video_chunk").length, 1, "Cancelled upload continued sending later chunks")
      assert(!calls.includes("video_commit"), "Clear allowed the cancelled video to publish")
      assert.equal(await uploadClearing.frame.locator(".tm-gallery-tile[data-item-id]").count(), 0)
      assert.equal(await uploadClearing.frame.locator(".tm-bg").count(), 0)
      assert.equal(await uploadClearing.page.evaluate(() => window.qaFixture.gallery.length + Object.keys(window.qaFixture.uploads).length
        + Object.keys(window.qaFixture.videos).length + Object.keys(window.qaFixture.images).length), 0)
      await capture(uploadClearing, "video-clear-pending-upload-controls")
    }, { realBrowser: true, race: "Upload cancellation finishes before a delayed clear reply; controls remain closed throughout, including the late first-chunk ACK" })
  } finally { await uploadClearing.context.close() }

  const legacy = await loadPage(browser, origin, { locale: "en", wallpaper: "none", video: "true", tab: "settings" }, 1280, 900, "light", "no-preference")
  try {
    await check("video-old-backend-falls-back-to-original-import-budget", async () => {
      await legacy.page.evaluate(() => {
        window.qaResults.get_panel_gallery = { items: window.qaFixture.gallery, appearance: window.qaFixture.appearance }
      })
      await reopen(legacy)
      assert.equal(await legacy.frame.locator(".tm-media-policy").count(), 0)
      const before = await legacy.page.evaluate(() => window.qaVideoCalls.length)
      await legacy.frame.locator(VIDEO_INPUT).setInputFiles(large.assetPath)
      await legacy.frame.waitForSelector('.neko-toast[data-tone="danger"]')
      assert.equal(await legacy.page.evaluate(() => window.qaVideoCalls.length), before, "Legacy backend attempted a >32 MiB import")
      await pick(legacy.frame, "qa-video")
      await assertMoving(legacy.frame)
    })
  } finally { await legacy.context.close() }

  const runtimeFailure = await loadPage(browser, origin, { locale: "en", wallpaper: "none", video: "true", tab: "settings" }, 1280, 900, "light", "no-preference")
  try {
    await check("video-real-runtime-decoder-error-pauses-and-shows-cover", async () => {
      await installResourceProbe(runtimeFailure.frame)
      await pick(runtimeFailure.frame, "qa-video")
      await assertMoving(runtimeFailure.frame)
      const previous = await runtimeFailure.frame.locator(".tm-bg-image").evaluate(element => element.style.backgroundImage)
      const originalSrc = await videoSnapshot(runtimeFailure.frame)
      const brokenUrl = await runtimeFailure.frame.locator(VIDEO).evaluate(video => {
        const url = URL.createObjectURL(new Blob(["QA damaged runtime video stream"], { type: "video/webm" }))
        video.src = url
        video.load()
        return url
      })
      await runtimeFailure.frame.waitForSelector(".tm-appearance .neko-alert")
      await assertPaused(runtimeFailure.frame)
      assert.equal(await runtimeFailure.frame.locator(VIDEO).evaluate(video => getComputedStyle(video).opacity), "0")
      assert.equal(await runtimeFailure.frame.locator(".tm-bg-image").evaluate(element => element.style.backgroundImage), previous)
      await capture(runtimeFailure, "video-runtime-error-cover")
      await runtimeFailure.frame.evaluate(url => { URL.revokeObjectURL(url) }, brokenUrl)
      await runtimeFailure.frame.evaluate(() => window.NekoUiKit.render(null, document.getElementById("root")))
      assert(await runtimeFailure.frame.evaluate(url => window.qaVideoResources.revoked.includes(url), originalSrc.currentSrc))
    }, { realBrowser: true, failure: "A playing video is switched to genuinely undecodable Blob bytes; Chromium emits its real media error" })
  } finally { await runtimeFailure.context.close() }

  for (const previous of ["none", "bright"]) {
    const failed = await loadPage(browser, origin, { locale: "en", wallpaper: previous, video: "true", tab: "settings" }, 1280, 900, "light", "no-preference")
    try {
      await check(`video-real-decode-failure-${previous}-keeps-static-cover`, async () => {
        if (previous !== "none") await failed.frame.waitForSelector(".tm-bg-image")
        const before = await failed.frame.locator(".tm-bg-image").count()
          ? await failed.frame.locator(".tm-bg-image").evaluate(element => element.style.backgroundImage) : ""
        const poster = await failed.page.evaluate(() => {
          const record = window.qaFixture.videos["qa-video"]
          record.chunks = record.chunks.map(data => btoa("\0".repeat(atob(data).length)))
          return record.poster
        })
        await pick(failed.frame, "qa-video")
        await failed.frame.waitForSelector(".tm-appearance .neko-alert", { timeout: 12000 })
        await failed.frame.waitForSelector(".tm-video-loading", { state: "detached" })
        const actual = await failed.frame.locator(".tm-bg-image").evaluate(element => element.style.backgroundImage)
        if (previous === "none") assert(actual.includes(poster), "Decode failure discarded its known cover")
        else assert.equal(actual, before, "Decode failure replaced a different usable background")
        assert.equal(await failed.frame.locator(VIDEO).count(), 0, "Undecodable video became a playback element")
        assert.equal(await failed.frame.locator(".tm-bg").count(), 1, "Decode failure left a blank background")
        assert(await failed.frame.locator(".tm-appearance .neko-alert").count() > 0, "Decode failure was hidden from the user")
        await capture(failed, `video-decode-fallback-${previous}`)
      }, { realBrowser: true, failure: "Real invalid media bytes are decoded by Chromium; no synthetic video error event is used" })
    } finally { await failed.context.close() }
  }
  for (const previous of ["none", "bright"]) {
    const failed = await loadPage(browser, origin, { locale: "en", wallpaper: previous, video: "true", tab: "settings" }, 1280, 900, "light", "no-preference")
    try {
      await check(`video-chunk-download-failure-${previous}-poster-or-old-background`, async () => {
        if (previous !== "none") await failed.frame.waitForSelector(".tm-bg-image")
        const before = await failed.frame.locator(".tm-bg-image").count()
          ? await failed.frame.locator(".tm-bg-image").evaluate(element => element.style.backgroundImage) : ""
        await failed.page.evaluate(() => { window.qaFailVideoReadChunk = 1 })
        await pick(failed.frame, "qa-video")
        await failed.frame.waitForSelector(".tm-appearance .neko-alert")
        await failed.frame.waitForSelector(".tm-video-loading", { state: "detached" })
        const actual = await failed.frame.locator(".tm-bg-image").evaluate(element => element.style.backgroundImage)
        const poster = await failed.page.evaluate(() => window.qaFixture.videos["qa-video"].poster)
        if (previous === "none") assert(actual.includes(poster), "Chunk failure discarded its known cover")
        else assert.equal(actual, before, "Chunk failure replaced a different usable background")
        assert.equal(await failed.frame.locator(VIDEO).count(), 0, "Incomplete media became a playback element")
        await capture(failed, `video-transport-fallback-${previous}`)
      })
    } finally { await failed.context.close() }
  }
  for (const previous of ["none", "bright"]) {
    const failed = await loadPage(browser, origin, { locale: "en", wallpaper: previous, video: "true", tab: "settings" }, 1280, 900, "light", "no-preference")
    try {
      await check(`video-metadata-request-failure-${previous}-thumb-or-old-background`, async () => {
        if (previous !== "none") await failed.frame.waitForSelector(".tm-bg-image")
        const before = await failed.frame.locator(".tm-bg-image").count()
          ? await failed.frame.locator(".tm-bg-image").evaluate(element => element.style.backgroundImage) : ""
        await failed.page.evaluate(() => { window.qaFailNext = "get_gallery_image" })
        await pick(failed.frame, "qa-video")
        await failed.frame.waitForSelector(".tm-appearance .neko-alert")
        await failed.frame.waitForSelector(".tm-video-loading", { state: "detached" })
        const actual = await failed.frame.locator(".tm-bg-image").evaluate(element => element.style.backgroundImage)
        const thumb = await failed.page.evaluate(() => window.qaFixture.gallery.find(item => item.id === "qa-video").thumb)
        if (previous === "none") assert(actual.includes(thumb), "Metadata failure discarded the gallery cover")
        else assert.equal(actual, before, "Metadata failure replaced a different usable background")
        assert.equal(await failed.frame.locator(VIDEO).count(), 0)
        assert.equal(await failed.page.evaluate(() => window.qaFixture.appearance.bg_id), previous === "none" ? "" : "qa-wallpaper", "Loading failure persisted an unrequested wallpaper")
        await capture(failed, `video-metadata-fallback-${previous}`)
      })
    } finally { await failed.context.close() }
  }

  const preview = await recordFixture(browser, output, false)
  sources.qaVideoFixture = preview.source
  for (const width of [1280, 390]) for (const theme of ["light", "dark"]) {
    const matrix = await loadPage(browser, origin, { locale: "en", wallpaper: "video", video: "true", tab: "settings" }, width, width === 390 ? 600 : 900, theme, "no-preference")
    try {
      await check(`video-layout-${width}-${theme}-decoded-pixels`, async () => {
        await assertMoving(matrix.frame)
        await capture(matrix, `video-${width}-${theme}-settings`)
        await matrix.frame.locator(".tm-appearance-adjust-summary").click()
        await capture(matrix, `video-${width}-${theme}-adjustments`, ".tm-appearance-adjust-summary")
      })
    } finally { await matrix.context.close() }
  }

  for (const locale of option("video-locales", LOCALES.join(",")).split(",")) {
    const matrix = await loadPage(browser, origin, { locale, wallpaper: "video", video: "true", tab: "settings" }, 390, 600, "light", "reduce")
    try {
      await check(`video-locale-${locale}-copy-controls-static-poster`, async () => {
        assert.equal(await matrix.frame.locator(VIDEO_INPUT).count(), 1)
        assert.equal(await matrix.frame.locator(DIRECTORY_INPUT).count(), 1)
        assert.equal(await matrix.frame.locator(MOTION).count(), 1)
        await assertPaused(matrix.frame)
        assert.equal(await matrix.frame.locator(".tm-bg").count(), 1, "Reduced motion lost the cover")
        const chunks = await matrix.page.evaluate(() => window.qaMessages.filter(message => message.method === "call"
          && message.payload.actionId === "get_gallery_image" && message.payload.args.item_id === "qa-video" && message.payload.args.chunk_index >= 0))
        assert.equal(chunks.length, 0, "Initial reduced-motion view must not download video bytes")
        const body = await matrix.frame.locator(".tm-appearance").innerText()
        assert(!body.includes("panel.appearance."), "Unresolved translation key is visible")
        await capture(matrix, `video-copy-${locale}-390-light`)
      })
      if (locale === "en") {
        await check("video-initial-reduced-motion-can-enable-real-playback", async () => {
          await matrix.page.emulateMedia({ reducedMotion: "no-preference" })
          await assertMoving(matrix.frame)
          const chunks = await matrix.page.evaluate(() => window.qaMessages.filter(message => message.method === "call"
            && message.payload.actionId === "get_gallery_image" && message.payload.args.item_id === "qa-video" && message.payload.args.chunk_index >= 0))
          assert(chunks.length >= 1, "Leaving initial reduced-motion mode did not fetch real media")
        })
      }
    } finally { await matrix.context.close() }
  }
  return { screenshots, checks, fixture: { path: fixture.assetPath, bytes: fixture.size, frames: fixture.frames, chunkCount: fixture.chunkCount, sha256: fixture.sha256, large: { path: large.assetPath, bytes: large.size, chunkCount: large.chunkCount, sha256: large.sha256 }, preview: { path: preview.assetPath, bytes: preview.size, frames: preview.frames, sha256: preview.sha256 } } }
}
