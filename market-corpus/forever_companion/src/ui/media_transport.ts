export type MediaTask = { cancelled: boolean; controller?: AbortController }
export type MediaCall = (
  id: string,
  args?: Record<string, any>,
  options?: { timeoutMs?: number; signal?: AbortSignal; userInitiated?: boolean },
) => Promise<any>
export type MediaRequestPolicy = {
  operation: string
  chunkIndex?: number
  timeoutMs: number
  attempts?: number
  hedge?: {
    id: string
    args: Record<string, any>
    timeoutMs: number
    validate: (result: any) => boolean
  }
  receipt?: {
    uploadId: string
    isAccepted: (result: any) => boolean
  }
}

const TRANSIENT_MEDIA_CODES = new Set([
  "media_request_timeout", "hosted_surface_request_timeout", "network_error",
  "ETIMEDOUT", "ECONNABORTED", "ECONNRESET", "EPIPE", "ERR_NETWORK",
  "PLUGIN_TIMEOUT", "PLUGIN_EXECUTION_TIMEOUT", "HOST_TIMEOUT", "HOST_UNAVAILABLE",
  "PLUGIN_NOT_RUNNING",
])
const BUSINESS_MEDIA_CODE = /^(?:video_|gallery_|wallpaper_|image_)[a-z0-9_]+$/
const STABLE_ERROR_CODE = /^[a-z][a-z0-9_]*$/
const RETRY_DELAYS_MS = [250, 750]

export function cancelMediaTask(task: MediaTask): void {
  task.cancelled = true
  task.controller?.abort()
}

export function isTransientMediaError(error: unknown): boolean {
  const value = error && typeof error === "object" ? error as Record<string, any> : {}
  const message = String(value.message ?? (typeof error === "string" ? error : ""))
  const code = String(value.code || "")
  if (value.name === "AbortError" || code === "ERR_CANCELED" || BUSINESS_MEDIA_CODE.test(message)
    || BUSINESS_MEDIA_CODE.test(code)) return false
  if (STABLE_ERROR_CODE.test(message)) return TRANSIENT_MEDIA_CODES.has(message)
  if (STABLE_ERROR_CODE.test(code) && !TRANSIENT_MEDIA_CODES.has(code)) return false
  if (TRANSIENT_MEDIA_CODES.has(code)) return true
  const hostErrorType = String(value.details?.error_type || value.details?.cause?.error_type || "")
  if ((code === "PLUGIN_UI_ACTION_FAILED" || code === "PLUGIN_UI_CONTEXT_QUERY_FAILED")
    && ["TimeoutError", "ConnectionError", "ConnectionResetError"].indexOf(hostErrorType) >= 0) return true
  if ([408, 502, 503, 504].indexOf(Number(value.status ?? value.response?.status)) >= 0) return true
  return /hosted surface request timed out|execution timed out after|timeout of \d+ms exceeded|network error|failed to fetch|network request failed|connection reset|ECONNRESET/i.test(message)
}

function checkTask(task: MediaTask | null): void {
  if (task?.cancelled) throw new Error("wallpaper_cancelled")
}

function waitBeforeRetry(task: MediaTask | null, delayMs: number): Promise<void> {
  checkTask(task)
  const controller = new AbortController()
  if (task) task.controller = controller
  return new Promise((resolve, reject) => {
    const finish = (error?: Error) => {
      clearTimeout(timer)
      controller.signal.removeEventListener("abort", aborted)
      if (task?.controller === controller) task.controller = undefined
      if (error) reject(error)
      else {
        try {
          checkTask(task)
          resolve()
        } catch (caught) {
          reject(caught)
        }
      }
    }
    const aborted = () => finish(new Error("wallpaper_cancelled"))
    const timer = setTimeout(() => finish(), delayMs)
    controller.signal.addEventListener("abort", aborted, { once: true })
  })
}

async function requestAttempt(
  call: MediaCall,
  id: string,
  args: Record<string, any>,
  task: MediaTask | null,
  policy: MediaRequestPolicy,
): Promise<any> {
  checkTask(task)
  const controller = new AbortController()
  const primaryController = new AbortController()
  let receiptController: AbortController | undefined
  let hedgeController: AbortController | undefined
  if (task) task.controller = controller
  let deadlineReached = false
  let settled = false
  let hedgeWon = false
  let timeout: ReturnType<typeof setTimeout> | undefined
  let receiptDelay: ReturnType<typeof setTimeout> | undefined
  let receiptTimeout: ReturnType<typeof setTimeout> | undefined
  let hedgeDelay: ReturnType<typeof setTimeout> | undefined
  let hedgeTimeout: ReturnType<typeof setTimeout> | undefined
  let aborted = () => {}
  const interrupted = new Promise<never>((_resolve, reject) => {
    aborted = () => {
      primaryController.abort()
      receiptController?.abort()
      hedgeController?.abort()
      reject(new Error(deadlineReached ? "media_request_timeout" : "wallpaper_cancelled"))
    }
    controller.signal.addEventListener("abort", aborted, { once: true })
    timeout = setTimeout(() => {
      deadlineReached = true
      controller.abort()
    }, policy.timeoutMs)
  })
  const confirmation = new Promise<any>((resolve) => {
    if (!policy.receipt || policy.operation !== "video_chunk") return
    receiptDelay = setTimeout(() => {
      if (settled || controller.signal.aborted) return
      receiptController = new AbortController()
      const signal = receiptController.signal
      receiptTimeout = setTimeout(() => receiptController?.abort(), 3000)
      Promise.resolve().then(() => {
        if (settled || controller.signal.aborted || signal.aborted) return
        return call(id, {
          op: "video_status", upload_id: policy.receipt!.uploadId, chunk_index: policy.chunkIndex,
        }, { timeoutMs: 3000, signal })
      }).then((result) => {
        if (settled || controller.signal.aborted || signal.aborted) return
        if (!policy.receipt!.isAccepted(result)) return
        // The persisted receipt, not a file length or speculative response, confirms this chunk.
        console.info("[forever_companion] media receipt: chunk_index=%s status=%s",
          policy.chunkIndex ?? -1, "confirmed")
        resolve(result)
        primaryController.abort()
      }).catch(() => {
        // A failed or unavailable probe must not interrupt the original transfer.
      }).finally(() => {
        if (receiptTimeout !== undefined) clearTimeout(receiptTimeout)
      })
    }, 1000)
  })
  const duplicate = new Promise<any>((resolve) => {
    const hedge = policy.hedge
    if (!hedge || policy.operation !== "video_read") return
    hedgeDelay = setTimeout(() => {
      if (settled || controller.signal.aborted) return
      hedgeController = new AbortController()
      const signal = hedgeController.signal
      hedgeTimeout = setTimeout(() => hedgeController?.abort(), hedge.timeoutMs)
      Promise.resolve().then(() => {
        if (settled || controller.signal.aborted || signal.aborted) return
        return call(hedge.id, hedge.args, { timeoutMs: hedge.timeoutMs, signal })
      }).then((result) => {
        if (settled || controller.signal.aborted || signal.aborted || !hedge.validate(result)) return
        settled = true
        hedgeWon = true
        resolve(result)
        primaryController.abort()
      }).catch(() => {
        // A failed or malformed duplicate must not interrupt the primary request.
      }).finally(() => {
        if (hedgeTimeout !== undefined) clearTimeout(hedgeTimeout)
      })
    }, 1000)
  })
  try {
    return await Promise.race([
      Promise.resolve().then(() => {
        checkTask(task)
        return call(id, args, { timeoutMs: policy.timeoutMs, signal: primaryController.signal })
      }).then((result) => {
        if (policy.hedge && policy.operation === "video_read" && !policy.hedge.validate(result)) {
          throw new Error("video_read_failed")
        }
        settled = true
        return result
      }, (error) => {
        settled = true
        throw error
      }),
      interrupted,
      confirmation,
      duplicate,
    ])
  } catch (error) {
    controller.abort()
    checkTask(task)
    throw error
  } finally {
    settled = true
    if (timeout !== undefined) clearTimeout(timeout)
    if (receiptDelay !== undefined) clearTimeout(receiptDelay)
    if (receiptTimeout !== undefined) clearTimeout(receiptTimeout)
    if (hedgeDelay !== undefined) clearTimeout(hedgeDelay)
    if (hedgeTimeout !== undefined) clearTimeout(hedgeTimeout)
    receiptController?.abort()
    if (!hedgeWon) hedgeController?.abort()
    controller.signal.removeEventListener("abort", aborted)
    if (task?.controller === controller) task.controller = undefined
    // Do not abort a completed request: the host may still be dispatching its response.
  }
}

export async function callMediaWithRetry(
  call: MediaCall,
  id: string,
  args: Record<string, any>,
  task: MediaTask | null,
  policy: MediaRequestPolicy,
): Promise<any> {
  const attempts = Math.max(1, Math.min(3, Math.round(Number(policy.attempts) || 1)))
  for (let attempt = 1; attempt <= attempts; attempt++) {
    checkTask(task)
    const started = Date.now()
    try {
      const result = await requestAttempt(call, id, args, task, policy)
      console.info("[forever_companion] media transport: op=%s chunk_index=%s attempt=%s elapsed_ms=%s status=%s",
        policy.operation, policy.chunkIndex ?? -1, attempt, Date.now() - started, "ok")
      return result
    } catch (error) {
      const retry = !task?.cancelled && attempt < attempts && isTransientMediaError(error)
      console.info("[forever_companion] media transport: op=%s chunk_index=%s attempt=%s elapsed_ms=%s status=%s",
        policy.operation, policy.chunkIndex ?? -1, attempt, Date.now() - started,
        task?.cancelled ? "cancelled" : retry ? "retry" : "failed")
      if (!retry) throw error
      await waitBeforeRetry(task, RETRY_DELAYS_MS[attempt - 1])
    }
  }
  throw new Error("media_request_timeout")
}
