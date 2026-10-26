import { useEffect, useRef } from "@neko/plugin-ui"
import type { Appearance, MediaStorage } from "./types"
import { bgLayerStyle, unwrapCallResult } from "./utils"
import { callMediaWithRetry, cancelMediaTask as cancelTransportTask } from "./media_transport"
import type { MediaCall as TransportCall, MediaTask as TransportTask } from "./media_transport"

export type MediaTask = TransportTask
export type VideoFrames = { mime: string; poster: string; thumb: string }
export type GalleryMedia = { kind: string; url: string; poster: string; error?: string; loading?: boolean; direct?: boolean }
export type VideoUploadResult = Record<string, any> & { localFrames: VideoFrames }
export type MediaCall = TransportCall

export const VIDEO_MAX_BYTES = 32 * 1024 * 1024
export const VIDEO_HARD_MAX_BYTES = 512 * 1024 * 1024
export const VIDEO_CHUNK_BYTES = 768 * 1024
// Media work has bounded budgets independent from ordinary hosted requests.
// Only idempotent transfers retry intermittent transport failures.
export const MEDIA_REQUEST_TIMEOUT_MS = 150_000
export const MEDIA_CLEANUP_TIMEOUT_MS = 330_000
export const MEDIA_CHUNK_REQUEST_TIMEOUT_MS = 20_000
export const MEDIA_ABORT_REQUEST_TIMEOUT_MS = 30_000
export const VIDEO_READ_BATCH_CHUNKS = 4

export function readMediaStorage(value: any): MediaStorage | null {
  if (value == null) return null
  if (typeof value !== "object" || value.schema_version !== 1 || value.backend !== "files") throw new Error("video_schema_unsupported")
  const keys = ["single_limit_bytes", "total_limit_bytes", "hard_single_limit_bytes", "chunk_bytes", "video_bytes", "pending_bytes", "reclaim_bytes", "occupied_bytes"]
  for (const key of keys) {
    if (!Number.isSafeInteger(value[key]) || value[key] < 0) throw new Error("video_storage_invalid")
  }
  if (value.single_limit_bytes < 1 || value.single_limit_bytes > VIDEO_HARD_MAX_BYTES
    || value.hard_single_limit_bytes !== VIDEO_HARD_MAX_BYTES || value.total_limit_bytes < 1
    || value.chunk_bytes !== VIDEO_CHUNK_BYTES
    || (value.available_bytes !== null && (!Number.isSafeInteger(value.available_bytes) || value.available_bytes < 0))) throw new Error("video_storage_invalid")
  return value as MediaStorage
}

export function videoMime(file: File): string {
  if (/\.mp4$/i.test(file.name) && (!file.type || file.type === "video/mp4")) return "video/mp4"
  if (/\.webm$/i.test(file.name) && (!file.type || file.type === "video/webm")) return "video/webm"
  throw new Error("video_type_unsupported")
}

export function checkMediaTask(task: MediaTask) {
  if (task.cancelled) throw new Error("wallpaper_cancelled")
}

export function cancelMediaTask(task: MediaTask) {
  cancelTransportTask(task)
}

export function decodeVideoChunkResult(value: any, chunkIndex: number, expectedBytes: number): Uint8Array {
  const result = unwrapCallResult(value)
  if (Number(result?.chunk_index) !== chunkIndex || typeof result?.data_b64 !== "string"
    || result.data_b64.length > 1048576) throw new Error("video_read_failed")
  let binary = ""
  try { binary = atob(result.data_b64) } catch { throw new Error("video_read_failed") }
  if (binary.length !== expectedBytes) throw new Error("video_read_failed")
  const chunk = new Uint8Array(binary.length)
  for (let offset = 0; offset < binary.length; offset++) chunk[offset] = binary.charCodeAt(offset)
  return chunk
}

export function readFileDataUrl(file: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result || ""))
    reader.onerror = () => reject(new Error("wallpaper_file_read_failed"))
    reader.onabort = () => reject(new Error("wallpaper_cancelled"))
    reader.readAsDataURL(file)
  })
}

export async function prepareVideoFile(file: File, task: MediaTask, maxBytes = VIDEO_MAX_BYTES): Promise<VideoFrames> {
  const mime = videoMime(file)
  if (!file.size) throw new Error("video_size_invalid")
  if (file.size > Math.min(VIDEO_HARD_MAX_BYTES, maxBytes)) throw new Error("video_too_large")
  checkMediaTask(task)
  const url = URL.createObjectURL(file)
  const video = document.createElement("video")
  try {
    await waitVideoFrame(video, url, task)
    checkMediaTask(task)
    return { mime, poster: videoSnapshot(video, 960, 0.76), thumb: videoSnapshot(video, 256, 0.7) }
  } finally {
    releaseVideo(video)
    URL.revokeObjectURL(url)
  }
}

export async function verifyVideoUrl(url: string, task: MediaTask): Promise<void> {
  const video = document.createElement("video")
  try {
    await waitVideoFrame(video, url, task)
  } finally {
    releaseVideo(video)
  }
}

export async function uploadVideoFile(
  call: MediaCall,
  file: File,
  name: string,
  task: MediaTask,
  progress: (value: number, stage: string) => void,
  maxBytes = VIDEO_MAX_BYTES,
): Promise<VideoUploadResult> {
  progress(0, "preparing")
  const frames = await prepareVideoFile(file, task, maxBytes)
  checkMediaTask(task)
  let uploadId = ""
  try {
    const started = unwrapCallResult(await callMediaWithRetry(call, "gallery_add", {
      op: "video_begin", name, mime: frames.mime, size: file.size,
      poster: frames.poster, thumb: frames.thumb,
    }, task, { operation: "video_begin", timeoutMs: MEDIA_REQUEST_TIMEOUT_MS, attempts: 1 }))
    uploadId = String(started.upload_id || "")
    const chunkBytes = Number(started.chunk_bytes)
    if (!uploadId || !Number.isSafeInteger(chunkBytes) || chunkBytes !== VIDEO_CHUNK_BYTES) throw new Error("video_chunk_invalid")
    const chunkReceipts = started.chunk_receipts === true
    for (let offset = 0, index = 0; offset < file.size; offset += chunkBytes, index++) {
      checkMediaTask(task)
      const dataUrl = await readFileDataUrl(file.slice(offset, offset + chunkBytes))
      checkMediaTask(task)
      const acknowledged = unwrapCallResult(await callMediaWithRetry(call, "gallery_add", {
        op: "video_chunk", upload_id: uploadId, chunk_index: index,
        data_b64: dataUrl.slice(dataUrl.indexOf(",") + 1),
      }, task, {
        operation: "video_chunk", chunkIndex: index, timeoutMs: MEDIA_CHUNK_REQUEST_TIMEOUT_MS, attempts: 3,
        receipt: chunkReceipts ? {
          uploadId,
          isAccepted: (value) => {
            const receipt = unwrapCallResult(value)
            return receipt?.accepted === true && receipt.chunk_index === index
          },
        } : undefined,
      }))
      if (!acknowledged || acknowledged.accepted !== true || Number(acknowledged.chunk_index) !== index) throw new Error("video_chunk_invalid")
      checkMediaTask(task)
      progress(Math.min(99, Math.round(Math.min(file.size, offset + chunkBytes) * 100 / file.size)), "uploading")
    }
    checkMediaTask(task)
    progress(99, "saving")
    const result = unwrapCallResult(await callMediaWithRetry(
      call,
      "gallery_add",
      { op: "video_commit", upload_id: uploadId },
      task,
      { operation: "video_commit", timeoutMs: MEDIA_REQUEST_TIMEOUT_MS, attempts: 2 },
    ))
    // Once committed, cancellation cannot pretend that the saved item disappeared.
    uploadId = ""
    progress(100, "done")
    return { ...result, localFrames: frames }
  } finally {
    if (uploadId) {
      try {
        await callMediaWithRetry(
          call,
          "gallery_add",
          { op: "video_abort", upload_id: uploadId },
          null,
          { operation: "video_abort", timeoutMs: MEDIA_ABORT_REQUEST_TIMEOUT_MS, attempts: 2 },
        )
      } catch {}
    }
  }
}

function decodeVideoReadResult(value: any, index: number, requestedCount: number, size: number): Uint8Array[] {
  const result = unwrapCallResult(value)
  const batch = Array.isArray(result?.chunks) ? result.chunks : [result]
  if (batch.length < 1 || batch.length > requestedCount) throw new Error("video_read_failed")
  return batch.map((chunk, offset) => decodeVideoChunkResult(
    chunk, index + offset, Math.min(VIDEO_CHUNK_BYTES, size - (index + offset) * VIDEO_CHUNK_BYTES),
  ))
}

export function galleryPlaybackUrl(metadata: Record<string, any>, hostOrigin?: string): string {
  const path = metadata.playback_path
  if (typeof path !== "string" || !/^\/plugin\/forever_companion\/ui\/[a-f0-9]{64}\.(mp4|webm)$/.test(path)) return ""
  if (!path.endsWith(metadata.mime === "video/mp4" ? ".mp4" : ".webm")) return ""
  try {
    const origin = new URL(hostOrigin || "")
    if (!["http:", "https:"].includes(origin.protocol) || origin.origin !== hostOrigin) return ""
    return new URL(path, origin).href
  } catch { return "" }
}

export async function downloadGalleryVideo(
  call: MediaCall,
  id: string,
  metadata: Record<string, any>,
  task: MediaTask,
  progress?: (value: number) => void,
  hostOrigin?: string,
): Promise<GalleryMedia> {
  const count = Number(metadata.chunk_count)
  const size = Number(metadata.size)
  const mime = String(metadata.mime || "")
  if ((mime !== "video/mp4" && mime !== "video/webm") || !Number.isSafeInteger(size) || size < 1 || size > VIDEO_HARD_MAX_BYTES
    || !Number.isSafeInteger(count) || count < 1) throw new Error("video_read_failed")
  const chunkBytes = Number(metadata.chunk_bytes || VIDEO_CHUNK_BYTES)
  if (chunkBytes !== VIDEO_CHUNK_BYTES || count !== Math.ceil(size / chunkBytes)) throw new Error("video_read_failed")
  const directUrl = galleryPlaybackUrl(metadata, hostOrigin)
  if (directUrl) {
    checkMediaTask(task)
    // The displayed player owns decode and fallback; a probe would decode the
    // same source twice and delay the first visible frame.
    progress?.(100)
    return { kind: "video", url: directUrl, poster: String(metadata.poster || ""), direct: true }
  }
  const chunks: Uint8Array[] = []
  let bytes = 0
  progress?.(0)
  for (let index = 0; index < count;) {
    checkMediaTask(task)
    const requestedCount = Math.min(VIDEO_READ_BATCH_CHUNKS, count - index)
    const chunkArgs = { item_id: id, chunk_index: index, chunk_count: requestedCount }
    const result = unwrapCallResult(await callMediaWithRetry(
      call,
      "get_gallery_image",
      chunkArgs,
      task,
      {
        operation: "video_read", chunkIndex: index, timeoutMs: MEDIA_CHUNK_REQUEST_TIMEOUT_MS, attempts: 3,
        // A completed backend read can still lose its Hosted reply. Recover
        // single and batched replies with one validated, read-only duplicate.
        hedge: {
          id: "get_gallery_image",
          args: chunkArgs,
          timeoutMs: 3000,
          validate: (value) => {
            try {
              decodeVideoReadResult(value, index, requestedCount, size)
              return true
            } catch { return false }
          },
        },
      },
    ))
    checkMediaTask(task)
    const batch = decodeVideoReadResult(result, index, requestedCount, size)
    for (const chunk of batch) {
      bytes += chunk.length
      if (bytes > size) throw new Error("video_read_failed")
      chunks.push(chunk)
      progress?.(Math.min(bytes === size ? 100 : 99, Math.round(bytes * 100 / size)))
    }
    index += batch.length
  }
  if (bytes !== size) throw new Error("video_read_failed")
  checkMediaTask(task)
  const blob = new Blob(chunks as BlobPart[], { type: mime })
  return { kind: "video", url: URL.createObjectURL(blob), poster: String(metadata.poster || "") }
}

// Stable source effect: context polling and appearance changes do not reset currentTime.
export function BackgroundVideo(props: {
  media: GalleryMedia
  appearance: Appearance
  onError: () => void
  onReady: () => void
}) {
  const ref = useRef<HTMLVideoElement | null>(null)
  const motion = useRef(props.appearance.motion)
  const loading = useRef(props.media.loading)
  const onError = useRef(props.onError)
  const onReady = useRef(props.onReady)
  const syncPlayback = useRef<(() => void) | null>(null)
  motion.current = props.appearance.motion
  loading.current = props.media.loading
  onError.current = props.onError
  onReady.current = props.onReady
  useEffect(() => {
    const video = ref.current
    if (!video) return
    let alive = true
    let intersecting = true
    let failed = false
    let playRequest = 0
    let playPending = false
    let suspended = false
    let suspendedAt = 0
    let blurredAt = 0
    let lastTime = -1
    let lastProgress = performance.now()
    let recoveryCount = 0
    let lastRecovery = 0
    let resumeAt: number | null = null
    let waitingFrame = true
    let frameRequest: number | undefined
    let announced = false
    const frameCallbacks = typeof video.requestVideoFrameCallback === "function"
    const reduced = typeof window.matchMedia === "function" ? window.matchMedia("(prefers-reduced-motion: reduce)") : null
    video.muted = true
    video.defaultMuted = true
    video.loop = true
    video.playsInline = true
    video.preload = "auto"
    const fail = () => {
      if (!alive || failed) return
      failed = true
      video.pause()
      video.style.opacity = "0"
      onError.current()
    }
    const presented = () => {
      if (!alive || failed || suspended || video.seeking || document.hidden || !motion.current || reduced?.matches) return
      lastProgress = performance.now()
      lastTime = video.currentTime
      if (lastProgress - lastRecovery > 10000) recoveryCount = 0
      if (waitingFrame) {
        waitingFrame = false
        video.style.opacity = "1"
      }
      // A selection can return to this source without recreating the decoder.
      // A new preview still needs its own ready notification.
      if (!announced || loading.current) {
        announced = true
        loading.current = false
        onReady.current()
      }
    }
    const watchFrame = () => {
      frameRequest = video.requestVideoFrameCallback(() => {
        presented()
        if (alive && !failed) watchFrame()
      })
    }
    const progressed = () => {
      if (!frameCallbacks && video.readyState >= 2 && !video.seeking && video.currentTime !== lastTime) presented()
    }
    const repaint = () => {
      waitingFrame = true
      video.style.opacity = "0"
      lastProgress = performance.now()
      if (video.readyState >= 2 && !video.seeking) {
        try { video.currentTime = Math.max(0, video.currentTime - 0.001) } catch {}
      }
    }
    const restorePosition = () => {
      if (resumeAt === null) return
      const position = resumeAt
      resumeAt = null
      try {
        video.currentTime = Number.isFinite(video.duration)
          ? Math.min(position, Math.max(0, video.duration - 0.05)) : position
      } catch {}
    }
    const synchronize = () => {
      if (!alive || failed) return
      const bounds = video.getBoundingClientRect()
      const visible = bounds.width > 0 && bounds.height > 0 && video.getClientRects().length > 0
      if (!motion.current || document.hidden || !intersecting || !visible || reduced?.matches) {
        if (!suspended) suspendedAt = performance.now()
        suspended = true
        waitingFrame = true
        playRequest++
        playPending = false
        if (!video.paused) video.pause()
        video.style.opacity = "0"
      }
      else {
        if (suspended) {
          suspended = false
          lastProgress = performance.now()
          if (lastProgress - suspendedAt >= 1500) repaint()
        }
        video.style.opacity = !waitingFrame && video.readyState >= 2 ? "1" : "0"
        if (!video.paused || playPending) return
        const request = ++playRequest
        playPending = true
        const result = video.play()
        if (result && typeof result.then === "function") result.then(() => {
          if (alive && request === playRequest) playPending = false
        }, (error) => {
          if (!alive || request !== playRequest) return
          playPending = false
          // Chromium can interrupt play while a hosted frame is hidden or
          // moved. The next visibility check resumes without reloading bytes.
          if (error?.name === "AbortError") return
          fail()
        })
        else playPending = false
      }
    }
    const wake = () => {
      if (!suspended && blurredAt && performance.now() - blurredAt >= 1500) repaint()
      blurredAt = 0
      synchronize()
    }
    const blur = () => { blurredAt = performance.now() }
    const checkPlayback = () => {
      synchronize()
      if (!alive || failed || suspended || performance.now() - lastProgress < 4000) return
      // A decoder can stop presenting frames while paused remains false.
      // Seek locally first, then reload the same source once before fallback.
      if (recoveryCount >= 2) return fail()
      recoveryCount++
      lastRecovery = performance.now()
      playRequest++
      playPending = false
      repaint()
      if (recoveryCount === 2 || video.readyState < 2) {
        resumeAt = video.currentTime
        video.load()
        if (frameCallbacks) {
          if (frameRequest !== undefined) video.cancelVideoFrameCallback(frameRequest)
          watchFrame()
        }
      }
      console.info("[forever_companion] media playback: status=recovering attempt=%s", recoveryCount)
      synchronize()
    }
    syncPlayback.current = synchronize
    video.addEventListener("error", fail)
    video.addEventListener("loadeddata", synchronize)
    video.addEventListener("loadedmetadata", restorePosition)
    video.addEventListener("playing", progressed)
    video.addEventListener("timeupdate", progressed)
    video.addEventListener("seeked", progressed)
    document.addEventListener("visibilitychange", synchronize)
    window.addEventListener("resize", synchronize)
    window.addEventListener("focus", wake)
    window.addEventListener("pageshow", wake)
    window.addEventListener("blur", blur)
    if (reduced?.addEventListener) reduced.addEventListener("change", synchronize)
    else if (reduced?.addListener) reduced.addListener(synchronize)
    const observer = typeof IntersectionObserver === "function"
      ? new IntersectionObserver((entries) => {
        intersecting = entries.some((entry) => entry.isIntersecting)
        synchronize()
      }) : null
    observer?.observe(video)
    // Opaque hosted frames may be hidden by a parent without visibilitychange.
    const visibilityTimer = setInterval(checkPlayback, 1000)
    video.src = props.media.url
    video.style.opacity = "0"
    if (frameCallbacks) watchFrame()
    video.load()
    synchronize()
    return () => {
      alive = false
      clearInterval(visibilityTimer)
      if (frameRequest !== undefined) video.cancelVideoFrameCallback(frameRequest)
      observer?.disconnect()
      video.removeEventListener("error", fail)
      video.removeEventListener("loadeddata", synchronize)
      video.removeEventListener("loadedmetadata", restorePosition)
      video.removeEventListener("playing", progressed)
      video.removeEventListener("timeupdate", progressed)
      video.removeEventListener("seeked", progressed)
      document.removeEventListener("visibilitychange", synchronize)
      window.removeEventListener("resize", synchronize)
      window.removeEventListener("focus", wake)
      window.removeEventListener("pageshow", wake)
      window.removeEventListener("blur", blur)
      if (reduced?.removeEventListener) reduced.removeEventListener("change", synchronize)
      else if (reduced?.removeListener) reduced.removeListener(synchronize)
      syncPlayback.current = null
      releaseVideo(video)
    }
  }, [props.media.url])
  useEffect(() => { syncPlayback.current?.() }, [props.appearance.motion])
  const layer = bgLayerStyle(props.appearance, props.media.poster)
  const inset = props.appearance.blur > 0 ? Math.round(props.appearance.blur) + 16 : 0
  const style = {
    position: "absolute", top: `${-inset}px`, left: `${-inset}px`,
    width: `calc(100% + ${inset * 2}px)`, height: `calc(100% + ${inset * 2}px)`,
    objectFit: props.appearance.fill === "contain" ? "contain" : props.appearance.fill === "stretch" ? "fill" : "cover",
    objectPosition: props.appearance.position, filter: layer.filter, pointerEvents: "none",
  }
  return <video key="wallpaper-video" ref={ref} className="tm-bg-video" style={style}
    poster={props.media.poster} muted loop playsInline aria-hidden="true" />
}

function releaseVideo(video: HTMLVideoElement) {
  video.pause()
  video.removeAttribute("src")
  video.load()
}

function waitVideoFrame(video: HTMLVideoElement, url: string, task: MediaTask, timeoutMs = 20000): Promise<void> {
  return new Promise((resolve, reject) => {
    let settled = false
    const finish = (error?: Error) => {
      if (settled) return
      settled = true
      clearTimeout(timeout)
      clearInterval(cancelTimer)
      video.removeEventListener("loadeddata", loaded)
      video.removeEventListener("error", failed)
      if (error) reject(error)
      else resolve()
    }
    const loaded = () => {
      if (task.cancelled) return finish(new Error("wallpaper_cancelled"))
      if (!video.videoWidth || !video.videoHeight || video.readyState < 2) return
      if (video.videoWidth * video.videoHeight > 33177600) return finish(new Error("wallpaper_video_decode_failed"))
      finish()
    }
    const failed = () => finish(new Error("wallpaper_video_decode_failed"))
    const timeout = setTimeout(failed, timeoutMs)
    const cancelTimer = setInterval(() => { if (task.cancelled) finish(new Error("wallpaper_cancelled")) }, 100)
    video.muted = true
    video.defaultMuted = true
    video.loop = true
    video.playsInline = true
    video.preload = "auto"
    video.addEventListener("loadeddata", loaded)
    video.addEventListener("error", failed)
    video.src = url
    video.load()
  })
}

function videoSnapshot(video: HTMLVideoElement, edge: number, quality: number): string {
  const scale = Math.min(1, edge / Math.max(video.videoWidth, video.videoHeight))
  const canvas = document.createElement("canvas")
  canvas.width = Math.max(1, Math.round(video.videoWidth * scale))
  canvas.height = Math.max(1, Math.round(video.videoHeight * scale))
  const ctx = canvas.getContext("2d")
  if (!ctx) throw new Error("video_poster_invalid")
  ctx.drawImage(video, 0, 0, canvas.width, canvas.height)
  const result = canvas.toDataURL("image/jpeg", quality)
  if (!result.startsWith("data:image/jpeg;base64,") || result.length > (edge === 256 ? 400000 : 1000000)) throw new Error("video_poster_invalid")
  return result
}
