import {
  Page, Card, Stack, Text, Tip, Alert, StatusBadge, Button, Accordion,
  Textarea, Input, Switch, Field, RadioGroup, ConfirmDialog, FileDownload, ButtonGroup, useToast, useEffect,
} from "@neko/plugin-ui"
import type { HostedAction, PluginSurfaceProps } from "@neko/plugin-ui"
import { PlayerBuildEditor, SavedPlayerBuilds, usePlayerBuildDraft } from "./player_build_editor"

type Assistant = {
  installed?: boolean; running?: boolean; connected?: boolean; source_launchable?: boolean
  mode?: string; last_error?: string; game?: { running?: boolean; minimized?: boolean }
}
type Diagnostic = {
  checked_at: string
  checks: { name: string; level: "info" | "success" | "warning"; detail: string }[]
  last_query?: { outcome?: string; wait_seconds?: number | null }
  copy_text: string
}
type DiagnosticExport = { path: string; filename: string; checked_at: string }
type EquipmentCard = {
  state?: string; name?: string; slot?: string; level?: string; complete?: boolean
  main_stat?: { label: string; value: string } | null
  sub_stats?: { label: string; value: string }[]
}
type Visual = {
  fresh?: boolean; status?: string; observed_at?: string; scene?: { label?: string; verified?: boolean }
  characters?: { name?: string }[]; controlled_character?: { name?: string }
  screen_facts?: { facts?: { kind?: string; label?: string; value?: string }[]; equipment_card?: EquipmentCard }
}
type ImportedBuild = {
  form_id: string; name: string; level: number; eidolon: number; fetched_at?: string
  change?: "added" | "updated" | "unchanged"
  light_cone?: { name: string; level: number; superimposition: number } | null
  missing?: string[]; freshness?: string; relic_sets?: string[]
  relics?: { slot: string; set_name: string; level: number; complete: boolean
    main_stat?: { label: string; display: string } | null
    sub_stats: { label: string; display: string }[] }[]
}
type UidImport = {
  uid?: string; status?: string; error_code?: string; last_query_at?: string; next_query_at?: string
  use_system_proxy?: boolean; error_info?: { http_status?: number | null; network_mode?: string } | null
  preview?: { uid?: string; preview_id: string; fetched_at: string; can_confirm: boolean; conflicting_form_ids?: string[]; characters: ImportedBuild[]
    summary?: { added: number; updated: number; unchanged: number; retained: number; total_after: number } } | null
  roster?: { characters: ImportedBuild[] }
}
type CompanionState = {
  observer?: { enabled?: boolean; state?: string; message?: string; assistant?: Assistant; capture_backend?: string }
  vision_assistant?: Assistant
  current_visual_evidence?: Visual
  observation_display?: { current?: Visual; last_successful?: Visual; revision?: number }
  manual_refresh?: { state?: string; requested_at?: string; finished_at?: string; revision?: number; field_count?: number }
  proactive_companionship?: { enabled?: boolean }
  data_pack?: { state?: string; installed?: boolean; update_available?: boolean; meta?: { game_version?: string; snapshot_date?: string } }
  avatar_pack?: { state?: string; installed?: boolean }
  last_operation?: { operation?: string; state?: string; message?: string }
  uid_import?: UidImport
  player_builds?: { characters: ImportedBuild[]; state?: string }
  build_options?: Record<string, any[]>
  data_freshness?: { guidance?: { checked_at?: string; status?: string
    active_periods?: { id: string; name: string; ends_at: string }[] } }
}
function hasAction(actions: HostedAction[], id: string): boolean {
  return actions.some(action => action.id === id || action.entry_id === id)
}
function extractDiagnostic(result: any): Diagnostic | null {
  const value = result?.diagnostic || result?.result?.diagnostic || result?.data?.diagnostic
  return value && typeof value.copy_text === "string" && value.copy_text.length > 0
    && typeof value.checked_at === "string" && Array.isArray(value.checks) ? value : null
}

async function copyDiagnosticText(text: string): Promise<boolean> {
  try {
    if (typeof navigator !== "undefined" && navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text)
      return true
    }
  } catch { /* Hosted iframe may expose the API but reject it by policy. */ }
  // Keep this inside the user's click. No permission changes or host access.
  if (typeof document === "undefined") return false
  const previous = document.activeElement as HTMLElement | null
  const field = document.createElement("textarea")
  field.value = text
  field.readOnly = true
  field.style.cssText = "position:fixed;left:-9999px;top:0;opacity:0"
  try {
    document.body.appendChild(field)
    field.focus()
    field.select()
    return document.execCommand("copy") === true
  } catch { return false }
  finally {
    field.remove()
    try { previous?.focus?.() } catch { /* Focus restoration is best effort. */ }
  }
}

function friendlyError(error: unknown, label = "操作"): string {
  const detail = error && typeof error === "object" ? error as { message?: string; code?: string; status?: number } : {}
  const raw = String(detail.message || (typeof error === "string" ? error : ""))
  const text = raw.toLowerCase()
  const prefix = `“${label}”没有完成：`
  if (detail.code === "PLUGIN_UI_ACTION_NOT_FOUND" || text.includes("not a plugin entry")) {
    return `${prefix}按钮对应的操作未登记。请重新打开星铁插件面板；仍失败时，重启星铁插件。`
  }
  if (detail.code === "PLUGIN_NOT_RUNNING" || text.includes("plugin is not running")) {
    return `${prefix}星铁插件未运行，请先在插件管理中启动它。`
  }
  if (raw.includes("视觉助手") || raw.includes("下载失败") || raw.includes("校验")) return raw
  if (text.includes("timeout") || text.includes("timed out")) {
    return `${prefix}等待回复超时。请先刷新状态，确认是否已完成，再决定是否重试。`
  }
  if (text.includes("network") || text.includes("failed to fetch")) {
    return `${prefix}无法连接 N.E.K.O. 的插件服务，请确认 N.E.K.O. 正在运行后重试。`
  }
  return `${prefix}${raw.slice(0, 240) || "后台没有返回具体原因，请刷新状态后重试。"}`
}

async function runPanelAction(callAction: () => Promise<any>, refreshState: () => Promise<any>, success: string) {
  const result = await callAction()
  const operation = result?.result?.last_operation || result?.last_operation || result?.data?.last_operation
  const message = operation?.message || success
  const tone = operation?.state === "error" ? "danger" as const : operation?.state === "info" ? "info" as const : "success" as const
  try {
    await refreshState()
    return { result, message: String(message), tone }
  } catch {
    if (tone === "danger") return { result, message: `${message} 面板状态也暂未更新，请点击“刷新状态”。`, tone }
    return { result, message: `${message} 但面板状态暂未更新，请点击“刷新状态”；无需重复刚才的操作。`, tone: "info" as const }
  }
}

export default function HsrCompanionPanel(props: PluginSurfaceProps<CompanionState>) {
  const state = props.state || {}
  const t = props.t
  const actions = Array.isArray(props.actions) ? props.actions : []
  const observer = state.observer || {}
  const assistant = state.vision_assistant || observer.assistant || {}
  const visual = state.observation_display?.current || state.current_visual_evidence || {}
  const history = state.observation_display?.last_successful
  const manualRefresh = state.manual_refresh || {}
  const usable = observer.enabled === true && visual.fresh === true
  const roles = usable ? (visual.characters || []).map(item => item.name).filter(Boolean) : []
  const facts = usable ? (visual.screen_facts?.facts || []) : []
  const equipment = usable ? visual.screen_facts?.equipment_card : null
  const proactive = state.proactive_companionship?.enabled !== false
  const dataPack = state.data_pack || {}
  const avatarPack = state.avatar_pack || {}
  const lastOperation = state.last_operation || {}
  const toast = useToast()
  const [running, setRunning] = props.useLocalState("hsr095Running", "")
  const [notice, setNotice] = props.useLocalState<{ tone: "info" | "success" | "danger"; text: string } | null>("hsr095Notice", null)
  const [diagnostic, setDiagnostic] = props.useLocalState<Diagnostic | null>("hsr095Diagnostic", null)
  const [diagnosticError, setDiagnosticError] = props.useLocalState("hsr095DiagnosticError", "")
  const [manualCopy, setManualCopy] = props.useLocalState("hsr095ManualCopy", false)
  const [exported, setExported] = props.useLocalState<DiagnosticExport | null>("hsr095DiagnosticExport", null)
  const [wrongName, setWrongName] = props.useLocalState("hsr095WrongName", "")
  const [correctName, setCorrectName] = props.useLocalState("hsr095CorrectName", "")
  const [modality, setModality] = props.useLocalState("hsr095CorrectionModality", "voice")
  const [confirmReset, setConfirmReset] = props.useLocalState("hsr095ConfirmReset", false)
  const [uidInput, setUidInput] = props.useLocalState("hsrUidInput", state.uid_import?.uid || "")
  const [uidConsent, setUidConsent] = props.useLocalState("hsrUidConsent", !!state.uid_import?.uid)
  const [uidProxy, setUidProxy] = props.useLocalState("hsrUidProxy", state.uid_import?.use_system_proxy === true)
  const [uidNotice, setUidNotice] = props.useLocalState<{ tone: "info" | "success" | "danger"; text: string } | null>("hsrUidNotice", null)
  const [uidFallback, setUidFallback] = props.useLocalState<UidImport | null>("hsrUidFallback", null)
  const [confirmUidClear, setConfirmUidClear] = props.useLocalState("hsrUidClearConfirm", false)
  const imported = uidFallback || state.uid_import || {}
  const importedRoles = imported.roster?.characters || []
  const savedRoles = state.player_builds?.characters || importedRoles
  const [buildInput, setBuildInput] = props.useLocalState("hsrBuildInput", "uid")
  const buildEditor = usePlayerBuildDraft(props, running)
  const uidPreview = imported.preview
  const uidChanges = (uidPreview?.characters || []).filter(role => role.change !== "unchanged")
  const uidUnchanged = (uidPreview?.characters || []).filter(role => role.change === "unchanged")
  const uidSummary = uidPreview?.summary
  const retryAt = new Date(imported.next_query_at || "").getTime()
  const retrySeconds = Number.isFinite(retryAt) ? Math.max(0, Math.ceil((retryAt - Date.now()) / 1000)) : 0

  function uidErrorMessage(value: UidImport) {
    const status = value.error_info?.http_status || 0
    return status >= 500 && status <= 599 ? t("uid.error.serverFailure", { code: status }) :
      status === 403 ? t("uid.error.accessDenied") : t("uid.error." + (value.error_code || "unknown"))
  }

  async function uidAction(id: string, args: Record<string, any>) {
    if (running) return
    setRunning("uid")
    setUidNotice({ tone: "info", text: t("uid.status.reading") })
    try {
      if (!hasAction(actions, id)) throw new Error(t("panel.actionUnavailable"))
      const response = await props.api.call(id, args, { timeoutMs: 30000 })
      const result = response?.uid_import || response?.result?.uid_import || response?.data?.uid_import
      if (!result || typeof result.status !== "string") throw new Error(t("uid.invalidResult"))
      setUidFallback(result)
      const failed = result.status === "error"
      setUidNotice({ tone: failed ? "danger" : result.status === "saved" ? "success" : "info",
        text: failed ? uidErrorMessage(result) : t("uid.status." + result.status) })
      if (id === "clear_uid_import" && result.status === "empty") {
        setUidInput(""); setUidConsent(false); setUidProxy(false); setConfirmUidClear(false)
      }
      try { await props.api.refresh(); setUidFallback(null) }
      catch { /* Keep the response/preview visible if the host refresh fails. */ }
    } catch (error) { setUidNotice({ tone: "danger", text: friendlyError(error, t("uid.title")) }) }
    finally { setRunning("") }
  }

  function importedRole(role: ImportedBuild) {
    return <Accordion id={"uid-preview-" + role.form_id} key={role.form_id} open={false}
      title={(role.change ? t("uid.change." + role.change) + " · " : "") + t("uid.roleSummary", { name: role.name, level: role.level, eidolon: role.eidolon })}>
      <Text>{role.light_cone ? t("uid.cone", { name: role.light_cone.name, level: role.light_cone.level, rank: role.light_cone.superimposition }) : t("uid.noCone")}</Text>
      {(role.relics || []).map((relic, index) => <Stack key={index}>
        <Text>{relic.slot} · {relic.set_name} +{relic.level}</Text>
        {relic.main_stat ? <Text>{t("equipmentCard.main")}：{relic.main_stat.label} {relic.main_stat.display}</Text> : <Text>{t("uid.missingMain")}</Text>}
        {relic.sub_stats.map((row, subIndex) => <Text key={subIndex}>{row.label} {row.display}</Text>)}
        {!relic.complete ? <Text>{t("uid.partial")}</Text> : null}
      </Stack>)}
    </Accordion>
  }

  useEffect(() => {
    let active = true
    let timer: ReturnType<typeof setTimeout>
    async function refreshStatus() {
      try { await props.api.refresh() } catch { /* Retain the visible operation result. */ }
      if (active) timer = setTimeout(refreshStatus, 3000)
    }
    timer = setTimeout(refreshStatus, 3000)
    return () => { active = false; clearTimeout(timer) }
  }, [props.api])

  async function call(id: string, args: Record<string, any>, busy: string, success: string) {
    const label = actions.find(action => action.id === id || action.entry_id === id)?.label || t("panel.operation")
    if (!hasAction(actions, id)) {
      setNotice({ tone: "danger", text: t("panel.actionUnavailable") })
      return null
    }
    setRunning(busy)
    setNotice({ tone: "info", text: t(busy === "assistantDownload" ? "panel.downloadingAssistant" : busy === "dataPack" ? "panel.downloadingData" : "panel.working") })
    try {
      const feedback = await runPanelAction(
        () => props.api.call(id, args, { timeoutMs: busy === "assistantDownload" ? 600000 : busy === "dataPack" ? 300000 : 60000 }),
        () => props.api.refresh(), success,
      )
      setNotice({ tone: feedback.tone, text: feedback.message })
      return feedback.result
    } catch (error) {
      setNotice({ tone: "danger", text: friendlyError(error, label) })
      return null
    } finally { setRunning("") }
  }

  async function diagnose() {
    setRunning("diagnose")
    setDiagnosticError("")
    try {
      if (!hasAction(actions, "diagnose_companion")) throw new Error(t("panel.actionUnavailable"))
      // No generic call()/refresh: diagnosis must not restore tasks or download.
      const result = extractDiagnostic(await props.api.call("diagnose_companion", {}, { timeoutMs: 8000 }))
      if (!result) throw new Error(t("diagnostics.invalidResult"))
      setDiagnostic(result)
    } catch (error) {
      setDiagnosticError(friendlyError(error, t("diagnostics.run")))
    } finally { setRunning("") }
  }

  async function copyDiagnostic() {
    if (!diagnostic || running) return
    const copied = await copyDiagnosticText(diagnostic.copy_text)
    setManualCopy(!copied)
    if (copied) toast.success(t("diagnostics.copied"))
    else toast.error(t("diagnostics.copyFailed"))
  }

  async function exportDiagnostic() {
    if (running) return
    setRunning("exportDiagnostic")
    setDiagnosticError("")
    setExported(null)
    try {
      if (!hasAction(actions, "export_companion_diagnostic")) throw new Error(t("panel.actionUnavailable"))
      const response = await props.api.call("export_companion_diagnostic", {}, { timeoutMs: 10000 })
      const payload = response?.result || response?.data || response
      const report = extractDiagnostic(response)
      const file = payload?.export
      if (!report || typeof file?.path !== "string" || !file.path || typeof file?.filename !== "string"
        || file.checked_at !== report.checked_at) throw new Error(t("diagnostics.exportInvalid"))
      setDiagnostic(report)
      setExported(file)
      toast.success(t("diagnostics.exported"))
    } catch (error) {
      setDiagnosticError(friendlyError(error, t("diagnostics.export")))
    } finally { setRunning("") }
  }

  async function refreshPanelStatus() {
    setRunning("refresh")
    try {
      await props.api.refresh()
      setNotice({ tone: "success", text: t("panel.refreshed") })
    } catch (error) { setNotice({ tone: "danger", text: friendlyError(error, t("panel.refresh")) }) }
    finally { setRunning("") }
  }

  async function saveCorrection() {
    if (!wrongName.trim() || !correctName.trim()) return
    const result = await call("remember_character_correction", {
      observed_name: wrongName.trim(), canonical_name: correctName.trim(), modality,
      note: "玩家在插件面板中明确纠正",
    }, "correction", t("panel.correctionSaved"))
    if (result) setCorrectName("")
  }

  let statusKey = "panel.stopped"
  let statusTone: "success" | "warning" | "danger" | "default" = "default"
  let explanation = t("panel.stoppedHelp")
  if (observer.enabled) {
    if (observer.state === "error") {
      statusKey = "panel.readFailed"; statusTone = "danger"; explanation = t("panel.readFailedHelp")
    } else if (observer.state === "paused") {
      statusKey = "panel.capturePaused"; statusTone = "warning"; explanation = t("panel.capturePausedHelp")
    } else if (!assistant.connected && observer.capture_backend !== "plugin_compatibility") {
      statusKey = "panel.connecting"; statusTone = "warning"; explanation = t("panel.connectingHelp")
    } else if (!assistant.game?.running && observer.capture_backend !== "plugin_compatibility") {
      statusKey = "panel.waitingGame"; statusTone = "warning"; explanation = t("panel.waitingGameHelp")
    } else if (usable && visual.scene?.verified) {
      statusKey = "panel.ready"; statusTone = "success"; explanation = visual.scene.label || t("panel.ready")
    } else {
      statusKey = "panel.confirming"; statusTone = "warning"; explanation = t("panel.confirmingHelp")
    }
  }
  const savedNotice = lastOperation.message ? {
    tone: lastOperation.state === "error" ? "danger" as const : lastOperation.state === "success" ? "success" as const : "info" as const,
    text: lastOperation.message,
  } : null
  const visibleNotice = running && lastOperation.state === "working" ? savedNotice || notice : notice || savedNotice

  return (
    <Page title={t("panel.title")} subtitle={t("panel.subtitle")}>
      <Card title={t("panel.observation")}>
        <Stack>
          <StatusBadge tone={statusTone} label={t(statusKey)} />
          <Text>{explanation}</Text>
          {usable && roles.length ? <Text>{t("panel.roles", { names: roles.join("、") })}</Text> : null}
          {usable && visual.controlled_character?.name ? <Text>{t("panel.controlled", { name: visual.controlled_character.name })}</Text> : null}
          {usable && visual.scene?.verified && !roles.length ? <Text>{t("panel.rolesUnconfirmed")}</Text> : null}
          {facts.filter(fact => ["current_location", "quest_objective"].includes(fact.kind || "")).slice(0, 2).map((fact, index) => (
            <Text key={index}>{fact.label}：{fact.value}</Text>
          ))}
          {equipment?.slot ? <Accordion title={t("equipmentCard.title")}>
            <Text>{[equipment.name, equipment.slot, equipment.level].filter(Boolean).join(" · ")}</Text>
            {equipment.main_stat ? <Text>{t("equipmentCard.main")}：{equipment.main_stat.label} {equipment.main_stat.value}</Text> : null}
            {(equipment.sub_stats || []).map((row, index) => <Text key={index}>{t("equipmentCard.sub")}：{row.label} {row.value}</Text>)}
            <Text>{t(equipment.state === "conflict" ? "equipmentCard.conflict" : equipment.complete ? "equipmentCard.complete" : "equipmentCard.partial")}</Text>
          </Accordion> : null}
          {history?.observed_at && (!usable || history.observed_at !== visual.observed_at) ? <Accordion title={t("panel.lastRead")}>
            <Text>{t("panel.lastReadHelp")}</Text>
            <Text>{t("panel.readAt", { time: history.observed_at })}</Text>
            {history.scene?.verified ? <Text>{history.scene.label}</Text> : null}
            {history.characters?.length ? <Text>{t("panel.roles", { names: history.characters.map(item => item.name).filter(Boolean).join("、") })}</Text> : null}
            {(history.screen_facts?.facts || []).slice(0, 12).map((fact, index) => <Text key={index}>{fact.label}：{String(fact.value || "").slice(0, 160)}</Text>)}
          </Accordion> : null}
          <Button tone={observer.enabled ? "default" : "success"} disabled={!!running} onClick={() => call(
            observer.enabled ? "stop_auto_observation" : "start_auto_observation", {}, "companion",
            t(observer.enabled ? "panel.stopped" : "panel.startRequested"),
          )}>{running === "companion" ? t("panel.working") : t(observer.enabled ? "panel.pause" : "panel.start")}</Button>
          <Tip>{t("panel.pauseHelp")}</Tip>
          <Button disabled={!!running || !observer.enabled || manualRefresh.state === "running"}
            onClick={() => call("refresh_current_observation", {}, "manualRefresh", t("manualRefresh.requested"))}>
            {t(manualRefresh.state === "running" ? "manualRefresh.running" : "manualRefresh.label")}
          </Button>
          {manualRefresh.state ? <Alert tone={manualRefresh.state === "completed" ? "success" :
            ["failed", "timeout", "unavailable"].includes(manualRefresh.state) ? "warning" : "info"}>
            {t("manualRefresh." + manualRefresh.state)}
          </Alert> : null}
          {facts.length > 2 ? <Accordion id="screen-facts-v2" open={false} title={t("screenFacts.title")}>
            {facts.slice(0, 12).map((fact, index) => <Text key={index}>{fact.label}：{fact.kind === "skill_description" ? t("screenFacts.bodyReady") : String(fact.value || "").slice(0, 160)}</Text>)}
          </Accordion> : null}
        </Stack>
      </Card>

      {visibleNotice ? <Alert tone={visibleNotice.tone}>{visibleNotice.text}</Alert> : null}

      <Card title={t("uid.title")}>
        <Stack>
          <StatusBadge tone={savedRoles.length ? "success" : "info"} label={t("uid.savedCount", { count: savedRoles.length })} />
          {savedRoles.length ? <Text>{savedRoles.map(role => role.name).join("、")}</Text> : <Text>{t("build.empty")}</Text>}
          <RadioGroup value={buildInput} options={[{ value: "uid", label: t("build.uidTab") }, { value: "manual", label: t("build.manualTab") }]} onChange={setBuildInput} />
          {buildInput === "manual" ? <PlayerBuildEditor surface={props} busy={running} onBusy={setRunning} editor={buildEditor} /> : <Stack>
          {uidNotice ? <Alert tone={uidNotice.tone}>{uidNotice.text}</Alert> :
            imported.status === "error" ? <Alert tone="danger">{uidErrorMessage(imported)}</Alert> : null}
          {imported.status === "error" && imported.error_info?.http_status ? <Text>{t("uid.httpError", { code: imported.error_info.http_status })}</Text> : null}
          <Button tone="primary" disabled={!!running || !uidConsent || !/^[125][0-9]{8}$/.test(uidInput.trim()) || retrySeconds > 0}
            onClick={() => uidAction("preview_uid_import", { uid: uidInput.trim(), consent: uidConsent, use_system_proxy: uidProxy })}>
            {t(running === "uid" ? "uid.status.reading" : importedRoles.length ? "uid.refresh" : "uid.read")}
          </Button>
          {retrySeconds > 0 ? <Text>{t("uid.cooldown", { seconds: retrySeconds })}</Text> : null}
          <Accordion id="uid-query-settings-v2" open={!imported.uid} title={t("uid.querySettings")}>
            <Field label={t("uid.uidLabel")}>
              <Input value={uidInput} placeholder={t("uid.uidPlaceholder")}
                onChange={(value: string) => { if (!running) { setUidInput(value); setUidConsent(false) } }} />
            </Field>
            <Switch checked={uidConsent} onChange={setUidConsent} label={t("uid.consent")} />
            <Switch checked={uidProxy} onChange={(value: boolean) => { if (!running) setUidProxy(value) }} label={t("uid.systemProxy")} />
            <Text>{t("uid.systemProxyHelp")}</Text>
            <Text>{t("uid.uidHelp")}</Text>
            {imported.last_query_at ? <Text>{t("uid.lastAttemptAt", { time: new Date(imported.last_query_at).toLocaleString() })}</Text> : null}
          </Accordion>
          {uidPreview ? <Stack>
            <Tip>{t("build.importProtection")}</Tip>
            {uidSummary ? <Text>{t("uid.changeSummary", uidSummary)}</Text> : <Text>{t("uid.previewCount", { count: uidPreview.characters.length })}</Text>}
            {uidSummary && !uidSummary.added && !uidSummary.updated ? <Alert tone="info">{t("uid.noChanges")}</Alert> : null}
            {uidChanges.map(importedRole)}
            {uidUnchanged.length ? <Accordion id="uid-unchanged-v2" open={false} title={t("uid.unchangedCount", { count: uidUnchanged.length })}>
              {uidUnchanged.map(importedRole)}
            </Accordion> : null}
            {uidSummary ? <Text>{t("uid.afterSave", { count: uidSummary.total_after, retained: uidSummary.retained })}</Text> : null}
            {uidPreview.conflicting_form_ids?.length ? <Alert tone="warning">{t("uid.conflictingCharacters")}</Alert> : null}
            {!uidPreview.can_confirm ? <Alert tone="warning">{t("uid.error.preview_expired")}</Alert> : null}
            <ButtonGroup>
              <Button tone="success" disabled={!!running || !uidPreview.can_confirm}
                onClick={() => uidAction("confirm_uid_import", { preview_id: uidPreview.preview_id })}>{t("uid.confirm")}</Button>
              <Button disabled={!!running} onClick={() => uidAction("discard_uid_import", { preview_id: uidPreview.preview_id })}>{t("uid.discard")}</Button>
            </ButtonGroup>
            <Accordion id="uid-preview-source-v2" open={false} title={t("uid.sourceDetails")}>
              <Text>{t("uid.previewUid", { uid: uidPreview.uid || imported.uid || "—" })}</Text>
              <Text>{t("uid.previewTime", { time: new Date(uidPreview.fetched_at).toLocaleString() })}</Text>
              <Tip>{t("uid.panelNotProvided")}</Tip>
            </Accordion>
          </Stack> : null}
          </Stack>}
          <SavedPlayerBuilds surface={props} rows={savedRoles} busy={running} onEdit={() => setBuildInput("manual")} editor={buildEditor} />
        </Stack>
      </Card>

      <Card title={t("panel.assistant")}>
        <Stack>
          <Text>{t("panel.installation")}：{t(assistant.installed ? "panel.installed" : "panel.notInstalled")}</Text>
          <Text>{t("panel.running")}：{t(assistant.running ? "panel.isRunning" : assistant.mode === "error" ? "panel.startFailed" : "panel.notRunning")}</Text>
          <Text>{t("panel.connection")}：{t(assistant.connected ? "panel.connected" : assistant.running ? "panel.connectPending" : "panel.disconnected")}</Text>
          {!assistant.installed ? <Button tone="primary" disabled={!!running} onClick={() => call("install_background_perception", {}, "assistantDownload", t("panel.assistantInstalled"))}>
            {t(running === "assistantDownload" ? "panel.downloadingAssistant" : lastOperation.state === "error" ? "panel.retryAssistant" : "panel.downloadAssistant")}
          </Button> : !assistant.connected && observer.enabled ? <Button disabled={!!running} onClick={() => call("restart_background_perception", {}, "repair", t("panel.assistantStarted"))}>
            {t("panel.startAssistant")}
          </Button> : null}
          {assistant.last_error && observer.enabled && !assistant.connected ? <Alert tone="warning">{t("panel.assistantError")}</Alert> : null}
        </Stack>
      </Card>

      <Card title={t("panel.proactive")}>
        <Stack>
          <Text>{t(proactive ? "panel.proactiveOn" : "panel.proactiveOff")}</Text>
          <Button disabled={!!running} onClick={() => call("set_proactive_companionship", { enabled: !proactive }, "proactive", t(proactive ? "panel.proactiveOff" : "panel.proactiveOn"))}>
            {running === "proactive" ? t("panel.working") : t(proactive ? "panel.disableProactive" : "panel.enableProactive")}
          </Button>
          <Tip>{t("panel.proactiveHelp")}</Tip>
        </Stack>
      </Card>

      <Card title={t("diagnostics.title")}>
        <Stack>
          <Button disabled={!!running} onClick={diagnose}>{t(running === "diagnose" ? "diagnostics.running" : "diagnostics.run")}</Button>
          <Button disabled={!!running} onClick={exportDiagnostic}>{t(running === "exportDiagnostic" ? "diagnostics.exporting" : "diagnostics.export")}</Button>
          <Tip>{t("diagnostics.description")}</Tip>
          {diagnosticError ? <Alert tone="danger">{diagnosticError}</Alert> : null}
          {exported ? <Stack>
            <Alert tone="success">{t("diagnostics.exported")}</Alert>
            <Text>{t("diagnostics.exportPath", { path: exported.path })}</Text>
            <Text>{t("diagnostics.exportTime", { time: new Date(exported.checked_at).toLocaleString() })}</Text>
            <FileDownload path={exported.path} filename={exported.filename} label={t("diagnostics.openExport")} />
            <Tip>{t("diagnostics.openExportHelp")}</Tip>
          </Stack> : null}
          {diagnostic ? <Stack>
            <Text>{t("diagnostics.checkedAt", { time: new Date(diagnostic.checked_at).toLocaleString() })}</Text>
            {diagnostic.checks.map((check, index) => <StatusBadge key={index} tone={check.level} label={t("diagnostics.check." + check.name) + "：" + t("diagnostics.detail." + check.detail)} />)}
            {diagnostic.last_query?.wait_seconds != null ? <Text>{t("diagnostics.queryTiming", { seconds: diagnostic.last_query.wait_seconds, outcome: t("diagnostics.outcome." + diagnostic.last_query.outcome) })}</Text> : null}
            <Button disabled={!!running} onClick={copyDiagnostic}>{t("diagnostics.copy")}</Button>
            {manualCopy ? <Stack>
              <Alert tone="warning">{t("diagnostics.copyFailed")}</Alert>
              <Textarea value={diagnostic.copy_text} />
            </Stack> : null}
            <Accordion title={t("diagnostics.report")}>
              <Textarea value={diagnostic.copy_text} />
              <Tip>{t("diagnostics.privacy")}</Tip>
            </Accordion>
          </Stack> : <Text>{t("diagnostics.notRun")}</Text>}
        </Stack>
      </Card>

      <Accordion id="maintenance-v2" open={false} title={t("panel.maintenance")}>
        <Stack>
          {state.data_freshness?.guidance?.checked_at ? <Stack>
            <Text>{t("uid.guidanceChecked", { date: state.data_freshness.guidance.checked_at })}</Text>
            {(state.data_freshness.guidance.active_periods || []).map(period => <Text key={period.id}>
              {period.name} · {t("uid.periodUntil", { time: new Date(period.ends_at).toLocaleString() })}
            </Text>)}
            <Text>{t(state.data_freshness.guidance.status === "review_due" ? "uid.guidanceReviewDue" : "uid.guidanceValidity")}</Text>
          </Stack> : null}
          <StatusBadge tone={dataPack.installed ? "success" : dataPack.state === "error" ? "danger" : "warning"}
            label={t(dataPack.installed ? "panel.dataReady" : dataPack.state === "error" ? "panel.dataFailed" : dataPack.state === "installing" ? "panel.dataInstalling" : "panel.dataMissing")} />
          <Text>{t(dataPack.installed ? "panel.dataComplete" : "panel.dataFallback")}</Text>
          {dataPack.installed ? <Text>{t("panel.dataVersion", { version: dataPack.meta?.game_version || t("panel.unknownVersion"), date: dataPack.meta?.snapshot_date || "—" })}</Text> : null}
          <Text>{t(avatarPack.installed ? "panel.portraitsReady" : avatarPack.state === "error" ? "panel.portraitsFailed" : avatarPack.state === "installing" ? "panel.portraitsInstalling" : "panel.portraitsMissing")}</Text>
          <Button disabled={!!running || dataPack.state === "installing"} onClick={() => call("prepare_data_pack", {}, "dataPack", t("panel.dataUpdated"))}>
            {t(running === "dataPack" ? "panel.downloadingData" : dataPack.update_available ? "panel.updateAvailable" : dataPack.installed ? "panel.updateData" : "panel.retryData")}
          </Button>
          <Button disabled={!!running} onClick={refreshPanelStatus}>{t("panel.refresh")}</Button>
          {assistant.installed ? <Button disabled={!!running} onClick={() => call("restart_background_perception", {}, "repair", t("panel.assistantStarted"))}>{t("panel.restartAssistant")}</Button> : null}
          <Tip>{t("panel.restartHelp")}</Tip>
        </Stack>
      </Accordion>

      <Accordion id="correction-v2" open={false} title={t("panel.correction")}>
        <Stack>
          <Tip>{t("panel.correctionHelp")}</Tip>
          <Field label={t("panel.wrongName")}><Textarea value={wrongName} onChange={setWrongName} /></Field>
          <Field label={t("panel.correctName")}><Textarea value={correctName} onChange={setCorrectName} /></Field>
          <RadioGroup value={modality} options={[
            { value: "voice", label: t("panel.voiceCorrection") }, { value: "chat", label: t("panel.chatCorrection") }, { value: "vision", label: t("panel.visionCorrection") },
          ]} onChange={setModality} />
          <Button disabled={!!running || !wrongName.trim() || !correctName.trim()} onClick={saveCorrection}>{t("panel.saveCorrection")}</Button>
        </Stack>
      </Accordion>

      <Accordion id="help-v2" open={false} title={t("panel.help")}>
        <Stack>
          <Text>{t("panel.privacyHelp")}</Text>
          <Text>{t("panel.captureHelp")}</Text>
          <Text>{t("panel.betaHelp")}</Text>
          <Accordion id="data-management-v2" open={false} title={t("panel.dataManagement")}>
            {importedRoles.length || imported.uid ? <Button disabled={!!running} onClick={() => { setConfirmUidClear(true) }}>{t("uid.clear")}</Button> : null}
            <Tip>{t("panel.resetWarning")}</Tip>
            <Button tone="danger" disabled={!!running} onClick={() => { setConfirmReset(true) }}>{t("panel.reset")}</Button>
          </Accordion>
        </Stack>
      </Accordion>

      <ConfirmDialog open={confirmReset} title={t("panel.resetTitle")} message={t("panel.resetWarning")} tone="danger"
        confirmLabel={t("panel.resetConfirm")} cancelLabel={t("panel.cancel")}
        onConfirm={async () => { if (await call("reset_demo_data", {}, "reset", t("panel.resetDone"))) { setConfirmReset(false); setDiagnostic(null) } }}
        onCancel={() => { setConfirmReset(false) }} />
      <ConfirmDialog open={confirmUidClear} title={t("uid.clear")} message={t("uid.clearWarning")} tone="warning"
        confirmLabel={t("uid.clear")} cancelLabel={t("panel.cancel")}
        onConfirm={() => uidAction("clear_uid_import", { confirmed: true })}
        onCancel={() => { setConfirmUidClear(false) }} />
    </Page>
  )
}
