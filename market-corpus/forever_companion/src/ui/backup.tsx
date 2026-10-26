import { Button, FileDownload, useConfirm, useEffect, useRef, useState } from "@neko/plugin-ui"
import type { HostedApi } from "@neko/plugin-ui"
import type { TFunc } from "./types"
import { errorText, unwrapCallResult } from "./utils"

export function BackupPanel(props: {
  t: TFunc
  api: HostedApi
  available: boolean
  disabled: boolean
  recoveryRequired?: boolean
  onRestored: () => Promise<void>
}) {
  const { t, api } = props
  const confirm = useConfirm()
  const picker = useRef<HTMLInputElement | null>(null)
  const task = useRef<AbortController | null>(null)
  const upload = useRef("")
  const alive = useRef(true)
  const locked = useRef(false)
  const [busy, setBusy] = useState("")
  const [progress, setProgress] = useState(0)
  const [preview, setPreview] = useState<any>(null)
  const [saved, setSaved] = useState<any>(null)
  const [error, setError] = useState("")
  const [recovery, setRecovery] = useState(!!props.recoveryRequired)

  useEffect(() => {
    alive.current = true
    return () => {
      alive.current = false
      task.current?.abort()
      const uid = upload.current
      if (uid) api.call("data_backup", { op: "abort", upload_id: uid }).catch(() => {})
    }
  }, [])

  function failure(err: unknown) {
    if (!alive.current) return
    setError(errorText(err, t))
    if (err instanceof Error && err.message === "backup_recovery_required") setRecovery(true)
  }

  async function call(args: Record<string, any>, signal?: AbortSignal) {
    return unwrapCallResult(await api.call("data_backup", args, { timeoutMs: 120000, signal }))
  }

  async function discard() {
    const uid = upload.current
    if (uid) await call({ op: "abort", upload_id: uid })
    upload.current = ""
    if (alive.current) setPreview(null)
  }

  async function cancelPreview() {
    if (locked.current) return
    locked.current = true
    setBusy("cancel")
    try { await discard() }
    catch (err) { failure(err) }
    finally {
      locked.current = false
      if (alive.current) setBusy("")
    }
  }

  async function exportData() {
    if (locked.current) return
    locked.current = true
    setBusy("export")
    setError("")
    try {
      const result = await call({ op: "export" })
      if (alive.current) setSaved(result)
    }
    catch (err) { failure(err) }
    finally {
      locked.current = false
      if (alive.current) setBusy("")
    }
  }

  async function choose(file: File) {
    if (locked.current) return
    locked.current = true
    setBusy("upload")
    setError("")
    setProgress(0)
    const controller = new AbortController()
    task.current = controller
    try {
      await discard()
      if (!file.size || file.size > 268435456) throw new Error("backup_too_large")
      const started = await call({ op: "begin", size: file.size }, controller.signal)
      upload.current = String(started.upload_id || "")
      const chunkBytes = Number(started.chunk_bytes)
      if (!/^[a-f0-9]{32}$/.test(upload.current) || chunkBytes !== 393216) throw new Error("backup_upload_invalid")
      for (let offset = 0; offset < file.size; offset += chunkBytes) {
        if (controller.signal.aborted) throw new Error("backup_cancelled")
        const bytes = new Uint8Array(await file.slice(offset, offset + chunkBytes).arrayBuffer())
        let binary = ""
        for (let i = 0; i < bytes.length; i += 8192) binary += String.fromCharCode(...bytes.subarray(i, i + 8192))
        const reply = await call({ op: "chunk", upload_id: upload.current, offset, data_b64: btoa(binary) }, controller.signal)
        if (reply.received !== offset + bytes.length) throw new Error("backup_upload_invalid")
        if (alive.current) setProgress(Math.round(reply.received / file.size * 100))
      }
      const result = await call({ op: "preview", upload_id: upload.current }, controller.signal)
      if (alive.current) setPreview(result)
    } catch (err) {
      try { await discard() } catch {}
      if (!controller.signal.aborted) failure(err)
    } finally {
      task.current = null
      locked.current = false
      if (alive.current) setBusy("")
    }
  }

  async function restore() {
    if (locked.current || !preview) return
    locked.current = true
    setBusy("restore")
    try {
      const ok = await confirm({
        title: t("panel.backup.restore"),
        message: t("panel.backup.confirm"),
        tone: "danger",
        confirmLabel: t("panel.confirm"), cancelLabel: t("panel.cancel"),
      })
      if (!ok || !alive.current) return
      setError("")
      const result = await call({ op: "apply", upload_id: upload.current, token: preview.token, confirm: true })
      if (result.restored !== true) throw new Error("backup_restore_failed")
      upload.current = ""
      if (!alive.current) return
      setPreview(null)
      setSaved(result.previous)
      await props.onRestored()
    } catch (err) { failure(err) }
    finally {
      locked.current = false
      if (alive.current) setBusy("")
    }
  }

  async function recover() {
    if (locked.current) return
    locked.current = true
    setBusy("restore")
    setError("")
    try {
      await call({ op: "recover" })
      if (!alive.current) return
      setRecovery(false)
      await props.onRestored()
    } catch (err) { failure(err) }
    finally {
      locked.current = false
      if (alive.current) setBusy("")
    }
  }

  const disabled = !props.available || props.disabled || !!busy
  const counts = preview?.manifest?.summary
  return (
    <section className="tm-settings-section tm-backup">
      <h3>{t("panel.backup.title")}</h3>
      <div className="tm-derived">{t("panel.backup.scope")}</div>
      <div className="tm-backup-actions">
        <Button disabled={disabled || recovery} onClick={exportData}>{t("panel.backup.export")}</Button>
        <Button disabled={disabled || recovery} onClick={() => picker.current?.click()}>{t("panel.backup.import")}</Button>
        <input ref={picker} type="file" accept=".fc-backup" hidden onChange={(event: any) => {
          const file = event.target.files?.[0]
          event.target.value = ""
          if (file) choose(file)
        }} />
        {busy === "upload" ? <Button onClick={() => task.current?.abort()}>{t("panel.cancel")}</Button> : null}
        {recovery ? <Button disabled={disabled} onClick={recover}>{t("panel.backup.recover")}</Button> : null}
      </div>
      {busy ? <div role="status" aria-live="polite" className="tm-derived">
        {busy === "upload" ? t("panel.backup.uploading", { n: progress }) : t("panel.backup.working")}
      </div> : null}
      {preview && counts ? (
        <div className="tm-backup-preview">
          <h4>{t("panel.backup.preview")}</h4>
          <div>{String(preview.manifest.created_at || "")} · {String(preview.manifest.plugin_version || "")}</div>
          <p>{t("panel.backup.counts", { roles: counts.roles.length, diary: counts.diary, journal: counts.journal, review: counts.review, images: counts.images })}</p>
          <div className="tm-backup-roles">{counts.roles.join(" · ")}</div>
          <p className="tm-derived">{t("panel.backup.target", { n: preview.current.roles.length })}</p>
          <div className="tm-backup-actions">
            <Button tone="danger" disabled={disabled} onClick={restore}>{t("panel.backup.restore")}</Button>
            <Button disabled={disabled} onClick={cancelPreview}>{t("panel.cancel")}</Button>
          </div>
        </div>
      ) : null}
      {saved ? <div className="tm-backup-result" role="status">
        <div>{t("panel.backup.saved")}</div>
        <div className="tm-backup-path">{String(saved.path || "")}</div>
        <FileDownload path={saved.directory} label={t("panel.backup.openFolder")} />
      </div> : null}
      {error || recovery ? <p role="alert" className="tm-backup-error">{error || t("panel.errors.backupRecoveryRequired")}</p> : null}
    </section>
  )
}
