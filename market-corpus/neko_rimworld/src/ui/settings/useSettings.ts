import { useEffect, useLocalState, useRef, useState, useI18n } from "@neko/plugin-ui"
import type { PanelServices } from "../services"
import { actionPayload, budgetError } from "../format"
import { BUDGET_FIELDS } from "../types"
import type { BudgetValues, DashboardState, SettingsReceipt } from "../types"

type Kind = "bridge" | "agent"
function validReceipt(value: SettingsReceipt, kind: Kind, id: string) {
  return value?.operation_id === id && value.kind === kind && ["saving", "saved", "failed", "unconfirmed"].includes(value.state)
    && (value.state !== "saved" || value.persisted === true && value.applied === true)
}
function operationId() {
  const bytes = new Uint8Array(16)
  crypto.getRandomValues(bytes)
  return Array.from(bytes, value => value.toString(16).padStart(2, "0")).join("")
}
function latestReceipt(state: DashboardState, kind: Kind) {
  // The Hosted iframe's local state ends with the iframe. The backend owns
  // the ordered, bounded receipt list, including saves completed while away.
  return [...(state.settings_saves || [])].reverse().find(value =>
    /^[a-f0-9]{32}$/.test(value.operation_id) && validReceipt(value, kind, value.operation_id))
}
export function useSettings(services: PanelServices) {
  const { state, mounted, call, refresh } = services
  const { t } = useI18n()
  const [port, setPort] = useState<number | "">(state.port ?? 48919)
  const [pairingToken, setPairingToken] = useState("")
  const [bridgeDirty, setBridgeDirty] = useState(false)
  const [baseUrl, setBaseUrl] = useState(state.configuration?.agent_base_url || "")
  const [model, setModel] = useState(state.configuration?.agent_model || "")
  const [apiKey, setApiKey] = useState("")
  const [budgets, setBudgets] = useState<BudgetValues>(initialBudgets(state))
  const [agentDirty, setAgentDirty] = useState(false)
  const [bridgeId, setBridgeId] = useLocalState("rimworld:bridge-save", latestReceipt(state, "bridge")?.operation_id || "")
  const [agentId, setAgentId] = useLocalState("rimworld:agent-save", latestReceipt(state, "agent")?.operation_id || "")
  const [bridgeReceipt, setBridgeReceipt] = useState<SettingsReceipt | null>(null)
  const [agentReceipt, setAgentReceipt] = useState<SettingsReceipt | null>(null)
  const [checking, setChecking] = useState(false)
  const [bridgeError, setBridgeError] = useState<Error | null>(null)
  const [agentError, setAgentError] = useState<Error | null>(null)
  const edits = useRef({ bridge: 0, agent: 0 })
  const pendingEdits = useRef<Record<string, { id: string; revision: number }>>({})
  const acknowledged = useRef(new Set<string>())
  const currentIds = useRef({ bridge: bridgeId, agent: agentId })
  currentIds.current = { bridge: bridgeId, agent: agentId }
  const gate = useRef(false)
  const runtimeNonce = useRef(state.action_nonce || "")
  runtimeNonce.current = state.action_nonce || ""
  const checkingGate = useRef<string | null>(null)
  const [submitting, setSubmitting] = useState<Kind | null>(null)
  const savedAgentKey = JSON.stringify([state.configuration?.agent_base_url, state.configuration?.agent_model, state.configuration?.agent_budgets])
  useEffect(() => { if (!bridgeDirty) setPort(state.port ?? 48919) }, [state.port])
  useEffect(() => {
    if (!agentDirty) { setBaseUrl(state.configuration?.agent_base_url || ""); setModel(state.configuration?.agent_model || ""); setBudgets(initialBudgets(state)) }
  }, [savedAgentKey])
  function setReceipt(kind: Kind, value: SettingsReceipt) {
    if (!mounted.current) return
    if (currentIds.current[kind] && value.operation_id !== currentIds.current[kind]) return
    if (kind === "bridge") setBridgeReceipt(value); else setAgentReceipt(value)
    if (value.state === "saved" && !acknowledged.current.has(value.operation_id)) {
      acknowledged.current.add(value.operation_id)
      const submitted = pendingEdits.current[kind]
      if (submitted ? submitted.id !== value.operation_id || submitted.revision !== edits.current[kind] : edits.current[kind] > 0) return
      if (kind === "bridge") { setPairingToken(""); setBridgeDirty(false) }
      else { setApiKey(""); setAgentDirty(false) }
    }
  }
  async function readReceipt(kind: Kind, id: string) {
    const generation = runtimeNonce.current
    if (!id || checkingGate.current === generation) return
    checkingGate.current = generation; setChecking(true)
    try {
      const value = actionPayload(await call("get_settings_save", { operation_id: id }, 5_000)) as SettingsReceipt
      if (!mounted.current || runtimeNonce.current !== generation || currentIds.current[kind] !== id) return
      if (!validReceipt(value, kind, id)) throw new Error("receipt_mismatch")
      setReceipt(kind, value)
      if (value.state === "saved") await refresh()
    } catch {
      if (runtimeNonce.current === generation && currentIds.current[kind] === id) setReceipt(kind, { operation_id: id, kind, state: "unconfirmed" })
    } finally {
      if (checkingGate.current === generation) { checkingGate.current = null; if (mounted.current) setChecking(false) }
    }
  }
  useEffect(() => {
    let stopped = false
    async function recover() {
      for (const kind of ["bridge", "agent"] as Kind[]) {
        if (stopped) return
        const id = currentIds.current[kind]
        if (!id) continue
        const value = (state.settings_saves || []).find(value => validReceipt(value, kind, id))
        if (value) setReceipt(kind, value)
        else await readReceipt(kind, id)
      }
    }
    void recover()
    return () => { stopped = true }
  }, [state.action_nonce])
  useEffect(() => {
    const active = bridgeReceipt?.state === "saving" ? bridgeReceipt : agentReceipt?.state === "saving" ? agentReceipt : null
    if (!active) return
    const timer = setTimeout(() => { void readReceipt(active.kind, active.operation_id) }, 700)
    return () => clearTimeout(timer)
  }, [bridgeReceipt, agentReceipt, checking])
  useEffect(() => {
    for (const kind of ["bridge", "agent"] as Kind[]) {
      const id = currentIds.current[kind]
      const value = id ? (state.settings_saves || []).find(value => validReceipt(value, kind, id)) : latestReceipt(state, kind)
      if (!value) continue
      if (!id) {
        currentIds.current[kind] = value.operation_id
        if (kind === "bridge") setBridgeId(value.operation_id); else setAgentId(value.operation_id)
      }
      setReceipt(kind, value)
    }
  }, [JSON.stringify(state.settings_saves)])
  async function save(kind: Kind, values: Record<string, any>) {
    if (gate.current || bridgeReceipt?.state === "saving" || agentReceipt?.state === "saving") return
    gate.current = true; setSubmitting(kind)
    const generation = runtimeNonce.current
    const id = operationId()
    currentIds.current[kind] = id
    pendingEdits.current[kind] = { id, revision: edits.current[kind] }
    if (kind === "bridge") setBridgeId(id); else setAgentId(id)
    setReceipt(kind, { operation_id: id, kind, state: "saving" })
    try {
      const value = actionPayload(await call("begin_settings_save", { ...values, operation_id: id, kind, ui_nonce: state.action_nonce }, 5_000)) as SettingsReceipt
      if (!mounted.current || runtimeNonce.current !== generation) return
      if (!validReceipt(value, kind, id)) throw new Error("receipt_mismatch")
      setReceipt(kind, value)
      if (value.state === "saved") await refresh()
    } catch (error) {
      if (!mounted.current || runtimeNonce.current !== generation) return
      // Check the same operation after a lost reply; never silently repeat a write.
      const code = (error as any)?.code || (error as any)?.details?.code
      if (["ui_only", "settings_busy", "invalid_settings_operation"].includes(code)) setReceipt(kind, { operation_id: id, kind, state: "failed", persisted: false, applied: false, error_code: code })
      else await readReceipt(kind, id)
      await refresh()
    } finally { gate.current = false; if (mounted.current) setSubmitting(null) }
  }
  function changePort(value: number | "") { edits.current.bridge++; setPort(value); setBridgeDirty(true); setBridgeError(null); setBridgeReceipt(null) }
  function changeToken(value: string) { edits.current.bridge++; setPairingToken(value); setBridgeDirty(true); setBridgeError(null); setBridgeReceipt(null) }
  function changeBaseUrl(value: string) { edits.current.agent++; setBaseUrl(value); setAgentDirty(true); setAgentError(null); setAgentReceipt(null) }
  function changeModel(value: string) { edits.current.agent++; setModel(value); setAgentDirty(true); setAgentError(null); setAgentReceipt(null) }
  function changeApiKey(value: string) { edits.current.agent++; setApiKey(value); setAgentDirty(true); setAgentError(null); setAgentReceipt(null) }
  function changeBudget(key: string, value: number | "") { edits.current.agent++; setBudgets(old => ({ ...old, [key]: value })); setAgentDirty(true); setAgentError(null); setAgentReceipt(null) }
  async function saveBridge() {
    const token = pairingToken.trim()
    if (typeof port !== "number" || !Number.isInteger(port) || port < 1 || port > 65535 || (token && (token.length < 32 || token.length > 128)) || (!token && !state.configuration?.token_configured)) { setBridgeError(new Error(t("settings.invalidBridge"))); return }
    await save("bridge", { port, pairing_token: token })
  }
  async function saveAgent() {
    const invalid = budgetError(budgets, BUDGET_FIELDS, t)
    if (!baseUrl.trim() || !model.trim() || invalid) { setAgentError(new Error(invalid || t("settings.invalidModel"))); return }
    await save("agent", { base_url: baseUrl.trim(), model: model.trim(), api_key: apiKey.trim(), budgets })
  }
  return { state, port, changePort, pairingToken, changeToken, baseUrl, changeBaseUrl,
    model, changeModel, apiKey, changeApiKey, budgets, changeBudget, bridgeDirty, agentDirty,
    savingBridge: submitting === "bridge" || bridgeReceipt?.state === "saving",
    savingAgent: submitting === "agent" || agentReceipt?.state === "saving",
    bridgeError, agentError, bridgeReceipt, agentReceipt, checking,
    checkBridge: () => readReceipt("bridge", bridgeId), checkAgent: () => readReceipt("agent", agentId), saveBridge, saveAgent }
}
function initialBudgets(state: DashboardState): BudgetValues {
  const values: BudgetValues = {}
  for (const field of BUDGET_FIELDS) values[field.key] = state.configuration?.agent_budgets?.[field.key] ?? field.initial
  return values
}
export type SettingsController = ReturnType<typeof useSettings>
