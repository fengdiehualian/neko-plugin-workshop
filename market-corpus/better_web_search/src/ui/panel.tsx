// better_web_search 插件面板（hosted-tsx）
//
// 只依赖 @neko/plugin-ui 的导出；不产生任何网络请求（注册网址只是纯文本，供用户复制）。
// 密钥只显示后端返回的掩码，输入框内容在保存后立刻清空，面板不留明文。
// 版式约定：首屏是"还差一步"检查页（宿主内置还在跑就出现，停了就不再弹）；进入后
// Tabs 分三区（状态/进阶/诊断），解释与注册教程一律进 Accordion，两家密钥各自成卡。
// 首屏与卡片首屏只留徽章、数字和主操作 —— kit 里的 Tip 是带色边框的提示盒，多放就满屏高亮。
import {
  Accordion,
  Alert,
  Button,
  Card,
  CodeBlock,
  DataTable,
  Field,
  Grid,
  Inline,
  Page,
  PasswordInput,
  RefreshButton,
  Stack,
  StatCard,
  StatusBadge,
  Step,
  Steps,
  Switch,
  Tabs,
  Text,
  useClipboard,
  useConfirm,
  useState,
  useToast,
} from "@neko/plugin-ui"
import type { HostedAction, PluginSurfaceProps } from "@neko/plugin-ui"

const EXA_KEY_PAGE = "https://dashboard.exa.ai/api-keys"
const ANYSEARCH_KEY_PAGE = "https://www.anysearch.com/about"

type HostSearchState = {
  exists?: boolean
  running?: boolean
}

type LastSearchState = {
  ok?: boolean
  backend?: string
  count?: number
  requested?: number
  attempted?: string[]
  query_len?: number
  ms?: number
  at?: string
  message?: string
  code?: string
}

type ExaKeyCard = {
  fingerprint?: string
  masked?: string
  state?: string
  current?: boolean
}

type PanelState = {
  exa_keys?: ExaKeyCard[]
  exa_key_masked?: string
  exa_key_source?: string
  exa_key_state?: string
  exa_last_error?: string
  anysearch_key_masked?: string
  anysearch_key_state?: string
  key_fallback?: boolean
  chain?: string[]
  effective_chain?: string[]
  proxy_mode?: string
  proxy_detected?: boolean
  host_search?: HostSearchState
  ssrf_fake_ip?: boolean
  quota_note?: string
  last_search?: LastSearchState
}

type DiagnoseView = {
  summary: string
  rows: DiagnoseRowView[]
  recommended: string[]
}

type DiagnoseRowView = {
  name: string
  direct: string
  proxied: string
  ms: string
  note: string
}

type ActionRecord = Record<string, unknown>

function unwrapActionResult(envelope: unknown): ActionRecord {
  if (!envelope || typeof envelope !== "object") return {}
  const record = envelope as ActionRecord
  const inner = record.result
  if (inner && typeof inner === "object") return inner as ActionRecord
  return record
}

function asString(value: unknown, fallback: string): string {
  if (typeof value === "string" && value.trim() !== "") return value.trim()
  if (typeof value === "number" && Number.isFinite(value)) return String(value)
  return fallback
}

function asList(value: unknown): string[] {
  if (!Array.isArray(value)) return []
  const items: string[] = []
  for (const item of value) {
    const text = asString(item, "")
    if (text) items.push(text)
  }
  return items
}

function asBool(value: unknown, fallback: boolean): boolean {
  if (typeof value === "boolean") return value
  if (value === "true") return true
  if (value === "false") return false
  return fallback
}

function asNumber(value: unknown): number {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : 0
}

function resultMessage(result: ActionRecord): string {
  return asString(result.message, asString(result.summary, ""))
}

function resultOk(result: ActionRecord): boolean {
  if (typeof result.ok === "boolean") return result.ok
  if (typeof result.success === "boolean") return result.success
  const status = asString(result.status, "").toLowerCase()
  if (status === "error" || status === "failed" || status === "fail" || status === "rejected") return false
  return true
}

// 未知/空值一律回到引导首屏，符合「打开面板第一屏就是新手引导」。
function maskOrDash(masked: string): string {
  return masked || "-"
}

function latencyText(value: unknown): string {
  const ms = Number(value)
  if (!Number.isFinite(ms) || ms <= 0) return ""
  if (ms < 1000) return `${Math.round(ms)} ms`
  return `${(ms / 1000).toFixed(1)} s`
}

async function nativeCopy(text: string): Promise<boolean> {
  // Use the async API directly instead of the host's useClipboard() hook: that
  // hook only checks whether writeText *exists*, so under a blocking
  // Permissions-Policy it awaits, catches the DOMException and reports it through
  // reportHostedRuntimeError('clipboard.write') -- which the panel frame renders
  // as "插件界面控件错误" even though the hook hands back a clean false. Doing the
  // call here keeps the rejection ours to swallow.
  try {
    await navigator.clipboard.writeText(text)
    return true
  } catch {
    return false
  }
}

function legacyCopy(text: string): boolean {
  // The legacy selection copy is not gated by the Clipboard Permissions-Policy,
  // so it is the fallback whenever the async API is blocked by the host.
  try {
    const area = document.createElement("textarea")
    area.value = text
    area.setAttribute("readonly", "")
    area.style.position = "fixed"
    area.style.top = "-1000px"
    area.style.opacity = "0"
    document.body.appendChild(area)
    area.select()
    const copied = document.execCommand("copy")
    document.body.removeChild(area)
    return copied
  } catch {
    return false
  }
}

export default function BetterWebSearchPanel(props: PluginSurfaceProps<PanelState>) {
  const { actions, state, t } = props
  const toast = useToast()
  const confirm = useConfirm()
  const clipboard = useClipboard()

  const safeState: PanelState = state && typeof state === "object" ? state : {}
  const actionList: HostedAction[] = Array.isArray(actions) ? actions : []
  const actionRegistryLoaded = actionList.length > 0

  const [pending, setPending] = useState("")
  const [keyDraft, setKeyDraft] = useState("")
  const [replaceOpen, setReplaceOpen] = useState(false)
  const [keyError, setKeyError] = useState("")
  const [anyDraft, setAnyDraft] = useState("")
  const [anyError, setAnyError] = useState("")
  const [notice, setNotice] = useState("")
  const [noticeIsError, setNoticeIsError] = useState(false)
  const [diagnose, setDiagnose] = useState<DiagnoseView | null>(null)
  // "" = follow whatever the host reports as the proxy situation; otherwise the
  // user forced dual-path on/off and we must not silently override that.
  const [dualPathDraft, setDualPathDraft] = useState("")
  // 只记本次会话：内置还在跑就每次打开都回到检查页，但想先配密钥的人不该被锁在外面。
  const [entered, setEntered] = useState(false)

  const maskedKey = asString(safeState.exa_key_masked, "")
  const keyState = asString(safeState.exa_key_state, "unknown").toLowerCase()
  const keyCards: ExaKeyCard[] = Array.isArray(safeState.exa_keys) ? safeState.exa_keys : []
  const usableKeys = keyCards.filter((card) => card.state !== "invalid" && card.state !== "exhausted").length
  const anyMasked = asString(safeState.anysearch_key_masked, "")
  const anyKeyState = asString(safeState.anysearch_key_state, "none").toLowerCase()
  const keyFallbackKnown = typeof safeState.key_fallback === "boolean"
  const keyFallback = asBool(safeState.key_fallback, false)
  const lastError = asString(safeState.exa_last_error, "")
  const proxyDetected = asBool(safeState.proxy_detected, false)
  const withProxy = dualPathDraft === "" ? proxyDetected : dualPathDraft === "on"
  const fullChain = asList(safeState.effective_chain).length ? asList(safeState.effective_chain) : asList(safeState.chain)
  const shownChain = proxyDetected ? fullChain : fullChain.filter((item) => item.toLowerCase() !== "duckduckgo")
  const quotaNote = asString(safeState.quota_note, t("panel.quota.fallback"))
  const hostSearch: HostSearchState = safeState.host_search && typeof safeState.host_search === "object" ? safeState.host_search : {}
  const hostKnown = typeof hostSearch.exists === "boolean" || typeof hostSearch.running === "boolean"
  const hostExists = asBool(hostSearch.exists, true)
  const hostRunning = asBool(hostSearch.running, false)
  // 「读不到」不等于「没关」：宿主管理接口不应答时不许骚扰用户。
  const showStart = hostKnown && hostRunning && !entered
  // 教程序列在做完之后就自己收起。
  const exaConfigured = keyCards.length > 0 || maskedKey !== ""
  const anyConfigured = anyMasked !== ""
  const ssrfFakeIpKnown = typeof safeState.ssrf_fake_ip === "boolean"
  const ssrfFakeIp = asBool(safeState.ssrf_fake_ip, false)
  const proxyMode = asString(safeState.proxy_mode, "-")
  const lastSearch: LastSearchState =
    safeState.last_search && typeof safeState.last_search === "object" ? safeState.last_search : {}
  // ``at`` is stamped when the search happens, so an absent one means the plugin
  // has not answered a search since it started -- not "the search was empty".
  const hasLastSearch = asString(lastSearch.at, "") !== ""

  function hasAction(id: string): boolean {
    return actionList.some((action) => action.id === id || action.entry_id === id)
  }

  // 动作注册表读不到时不锁死面板（W5 可能没给某些 id 打 @ui.action 元数据）；
  // 一旦注册表非空，就按 id 精确禁用按钮。
  function canCall(id: string): boolean {
    if (!actionRegistryLoaded) return true
    return hasAction(id)
  }

  // 任意一个动作在跑时就锁住全部按钮，避免重复提交；参数只为调用处可读。
  function busy(_pendingKey: string): boolean {
    return pending !== ""
  }

  function label(pendingKey: string, textKey: string): string {
    return pending === pendingKey ? t("panel.messages.working") : t(textKey)
  }

  function showNotice(message: string, isError: boolean) {
    setNotice(message)
    setNoticeIsError(isError)
  }

  async function runAction(id: string, args: ActionRecord, pendingKey: string, timeoutMs?: number): Promise<ActionRecord | null> {
    if (!canCall(id)) {
      toast.error(t("panel.errors.actionUnavailable"))
      return null
    }
    setPending(pendingKey)
    try {
      const envelope = timeoutMs
        ? await props.api.call(id, args, { timeoutMs })
        : await props.api.call(id, args)
      return unwrapActionResult(envelope)
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error)
      toast.error(message)
      showNotice(message, true)
      return null
    } finally {
      setPending("")
    }
  }

  // 状态一律以面板 context 为准：不做乐观值，免得刷新前显示的是猜出来的进度。
  async function refreshContext(): Promise<void> {
    try {
      await props.api.refresh()
    } catch (error) {
      const detail = error instanceof Error ? error.message : String(error)
      toast.error(t("panel.errors.refreshFailed", { detail }))
    }
  }

  async function copyRegisterUrl(url: string): Promise<void> {
    // Order matters: try the copies that can fail *silently* first. The host
    // clipboard hook reports every internal rejection to the panel frame
    // ("插件界面控件错误"), so it may only be the last resort -- by then a failure
    // is real and the banner is informative rather than alarming.
    // See crbug.com/414348233 for why the async API is blocked in this document.
    let copied = await nativeCopy(url)
    if (!copied) copied = legacyCopy(url)
    if (!copied) copied = await clipboard.write(url)
    if (copied) toast.success(t("panel.guide.urlCopied"))
    else toast.error(t("panel.guide.copyFailed"))
  }

  async function saveKey(): Promise<void> {
    const value = keyDraft.trim()
    if (!value) {
      setKeyError(t("panel.guide.keyEmpty"))
      return
    }
    setKeyError("")
    const result = await runAction("save_exa_key", { api_key: value }, "save", 60000)
    if (!result) return
    const ok = resultOk(result)
    const message = resultMessage(result) || (ok ? t("panel.messages.keySaved") : t("panel.messages.keySaveFailed"))
    setKeyDraft("")
    setReplaceOpen(false)
    showNotice(message, !ok)
    if (ok) toast.success(message)
    else toast.error(message)
    await refreshContext()
  }

  async function testKey(): Promise<void> {
    const result = await runAction("test_exa_key", {}, "test", 60000)
    if (!result) return
    const ok = resultOk(result)
    const parts: string[] = [resultMessage(result) || (ok ? t("panel.messages.testOk") : t("panel.messages.testFailed"))]
    const latency = latencyText(result.latency_ms)
    if (latency) parts.push(t("panel.messages.testLatency", { latency }))
    const count = Number(result.count)
    if (Number.isFinite(count) && count > 0) parts.push(t("panel.messages.testCount", { count }))
    showNotice(parts.join(" · "), !ok)
    if (ok) toast.success(parts.join(" · "))
    else toast.error(parts.join(" · "))
    await refreshContext()
  }

  async function removeKey(): Promise<void> {
    const accepted = await confirm({
      title: t("panel.confirm.removeTitle"),
      message: t("panel.confirm.removeMessage"),
      tone: "danger",
      confirmLabel: t("panel.confirm.removeOk"),
      cancelLabel: t("panel.actions.cancel"),
    })
    if (!accepted) return
    const result = await runAction("clear_exa_key", {}, "clear")
    if (!result) return
    const ok = resultOk(result)
    const message = resultMessage(result) || (ok ? t("panel.messages.keyRemoved") : t("panel.messages.keyRemoveFailed"))
    showNotice(message, !ok)
    if (ok) toast.success(message)
    else toast.error(message)
    setKeyDraft("")
    setReplaceOpen(false)
    await refreshContext()
  }

  async function removeOneKey(card: ExaKeyCard): Promise<void> {
    const accepted = await confirm({
      title: t("panel.confirm.oneTitle"),
      message: t("panel.confirm.oneMessage", { masked: asString(card.masked, "-") }),
      tone: "danger",
      confirmLabel: t("panel.confirm.removeOk"),
      cancelLabel: t("panel.actions.cancel"),
    })
    if (!accepted) return
    const result = await runAction(
      "remove_exa_key", { fingerprint: asString(card.fingerprint, "") }, "clear")
    if (!result) return
    const ok = resultOk(result)
    const message = resultMessage(result) || (ok ? t("panel.messages.keyRemoved") : t("panel.messages.keyRemoveFailed"))
    showNotice(message, !ok)
    if (ok) toast.success(message)
    else toast.error(message)
    await refreshContext()
  }

  async function toggleSsrfGuard(enabled: boolean): Promise<void> {
    const result = await runAction("set_ssrf_guard", { enabled }, "ssrf", 15000)
    if (!result) return
    const ok = resultOk(result)
    const fallback = enabled ? t("panel.net.fakeIpOn") : t("panel.net.fakeIpOff")
    const message = resultMessage(result) || (ok ? fallback : t("panel.errors.ssrfFailed"))
    showNotice(message, !ok)
    if (ok) toast.success(message)
    else toast.error(message)
    await refreshContext()
  }

  async function checkHostSearch(): Promise<void> {
    const result = await runAction("get_host_search", {}, "hostcheck")
    if (!result) return
    const ok = resultOk(result)
    const running = asBool(result.running, asBool(result.enabled, false))
    const message = resultMessage(result) || t("panel.messages.hostChecked", { state: running ? t("panel.host.stateRunning") : t("panel.host.stateStopped") })
    showNotice(message, !ok)
    await refreshContext()
  }

  function diagStatus(value: unknown): string {
    if (typeof value === "boolean") return value ? t("panel.diag.ok") : t("panel.diag.fail")
    // The backend leaves a column empty ("") when that path was not attempted,
    // which is not the same statement as "this path failed".
    if (typeof value === "string") return value.trim() || t("panel.diag.skipped")
    return "-"
  }

  function toDiagnoseRows(value: unknown): DiagnoseRowView[] {
    if (!Array.isArray(value)) return []
    const rows: DiagnoseRowView[] = []
    for (const item of value) {
      if (typeof item === "string") {
        rows.push({ name: item, direct: "-", proxied: "-", ms: "-", note: "-" })
        continue
      }
      if (!item || typeof item !== "object") continue
      const record = item as ActionRecord
      const name = asString(record.backend, asString(record.name, asString(record.source, "-")))
      const noteValue = asString(record.note, asString(record.message, "-"))
      rows.push({
        name,
        direct: diagStatus(record.direct),
        proxied: diagStatus(record.proxied),
        ms: latencyText(record.ms) || "-",
        note: noteValue,
      })
    }
    return rows
  }

  async function runDiagnose(): Promise<void> {
    setDiagnose(null)
    const result = await runAction("diagnose_network", { with_proxy: withProxy }, "diagnose", 90000)
    if (!result) return
    const ok = resultOk(result)
    const summary = resultMessage(result) || t("panel.diag.emptyResult")
    const rows = toDiagnoseRows(result.rows)
    const recommended = asList(result.recommended_chain)
    setDiagnose({ summary, rows, recommended })
    showNotice(summary, !ok)
    await refreshContext()
  }

  function renderNotice() {
    if (!notice) return null
    return <Alert tone={noticeIsError ? "danger" : "info"}>{notice}</Alert>
  }

  function renderKeyField(placeholderKey: string) {
    return (
      <Field
        label={t("panel.guide.keyField.label")}
        help={t("panel.guide.keyField.help")}
        error={keyError}
        required
      >
        <PasswordInput
          value={keyDraft}
          placeholder={t(placeholderKey)}
          disabled={pending === "save"}
          onChange={(value) => setKeyDraft(value)}
        />
      </Field>
    )
  }

  function keyBadge() {
    if (!maskedKey) return <StatusBadge tone="warning" label={t("panel.key.notSaved")} />
    if (keyState === "valid") return <StatusBadge tone="success" label={t("panel.key.valid")} />
    if (keyState === "invalid") return <StatusBadge tone="danger" label={t("panel.key.invalid")} />
    if (keyState === "exhausted") return <StatusBadge tone="warning" label={t("panel.key.spent")} />
    return <StatusBadge tone="info" label={t("panel.key.unknown")} />
  }

  function renderStartPage() {
    // Gated on one objective fact: the host's built-in search is still running.
    // A check that comes back unreadable must NOT be read as "not stopped yet" --
    // that is how this plugin used to report landed changes as failures.
    return (
      <Card title={t("panel.start.title")}>
        <Stack>
          <Alert tone="warning">{t("panel.start.lead")}</Alert>
          <Grid cols={2}>
            <StatCard
              label={t("panel.host.label")}
              value={<StatusBadge tone="warning" label={t("panel.host.stateRunning")} />}
            />
            <StatCard
              label={t("panel.start.selfLabel")}
              value={<StatusBadge tone="success" label={t("panel.start.selfValue")} />}
            />
          </Grid>
          <Text>{t("panel.start.whoClicks")}</Text>
          <Text>{t("panel.start.twoPaths")}</Text>
          {renderNotice()}
          <Inline gap={3} wrap>
            <Button tone="primary" disabled={busy("hostcheck") || !canCall("get_host_search")} onClick={checkHostSearch}>
              {label("hostcheck", "panel.actions.recheck")}
            </Button>
            <Button tone="default" onClick={() => { setEntered(true) }}>
              {t("panel.start.enter")}
            </Button>
          </Inline>
          <Accordion id="start-where" title={t("panel.start.accWhere")} open={false}>
            <Text>{t("panel.start.where")}</Text>
            <Text>{t("panel.start.reopen")}</Text>
          </Accordion>
          <Accordion id="start-why" title={t("panel.host.accWhy")} open={false}>
            <Text>{t("panel.host.why")}</Text>
            <Text>{t("panel.host.gate")}</Text>
          </Accordion>
        </Stack>
      </Card>
    )
  }

  function anyKeyBadge() {
    if (anyKeyState === "none" || !anyMasked) {
      return <StatusBadge tone="warning" label={t("panel.key.notSaved")} />
    }
    if (anyKeyState === "valid") return <StatusBadge tone="success" label={t("panel.key.valid")} />
    if (anyKeyState === "invalid") return <StatusBadge tone="danger" label={t("panel.key.invalid")} />
    return <StatusBadge tone="info" label={t("panel.key.unknown")} />
  }

  function keyCardBadge(card: ExaKeyCard) {
    const state = asString(card.state, "unknown").toLowerCase()
    if (state === "invalid") return <StatusBadge tone="danger" label={t("panel.key.invalid")} />
    if (state === "exhausted") return <StatusBadge tone="warning" label={t("panel.key.spent")} />
    if (state === "ok") return <StatusBadge tone="success" label={t("panel.key.valid")} />
    return <StatusBadge tone="info" label={t("panel.key.unknown")} />
  }

  // The AnySearch key is a single value, so its three actions never touch a ring:
  // the backend answers with the fixed copy shown here (the key itself never does).
  async function saveAnyKey(): Promise<void> {
    const value = anyDraft.trim()
    if (!value) {
      setAnyError(t("panel.guide.keyEmpty"))
      return
    }
    setAnyError("")
    const result = await runAction("set_anysearch_key", { api_key: value }, "anysave", 60000)
    if (!result) return
    const ok = resultOk(result)
    const message = resultMessage(result) || (ok ? t("panel.messages.fallbackSaved") : t("panel.messages.fallbackFailed"))
    setAnyDraft("")
    showNotice(message, !ok)
    if (ok) toast.success(message)
    else toast.error(message)
    await refreshContext()
  }

  async function testAnyKey(): Promise<void> {
    const result = await runAction("test_anysearch_key", {}, "anytest", 60000)
    if (!result) return
    const ok = resultOk(result)
    const parts: string[] = [resultMessage(result) || (ok ? t("panel.messages.testOk") : t("panel.messages.testFailed"))]
    const latency = latencyText(result.latency_ms)
    if (latency) parts.push(t("panel.messages.testLatency", { latency }))
    const count = Number(result.count)
    if (Number.isFinite(count) && count > 0) parts.push(t("panel.messages.testCount", { count }))
    showNotice(parts.join(" · "), !ok)
    if (ok) toast.success(parts.join(" · "))
    else toast.error(parts.join(" · "))
    await refreshContext()
  }

  async function clearAnyKey(): Promise<void> {
    const accepted = await confirm({
      title: t("panel.confirm.anyTitle"),
      message: t("panel.confirm.anyMessage"),
      tone: "danger",
      confirmLabel: t("panel.confirm.removeOk"),
      cancelLabel: t("panel.actions.cancel"),
    })
    if (!accepted) return
    const result = await runAction("clear_anysearch_key", {}, "anyclear")
    if (!result) return
    const ok = resultOk(result)
    const message = resultMessage(result) || (ok ? t("panel.messages.keyRemoved") : t("panel.messages.keyRemoveFailed"))
    showNotice(message, !ok)
    if (ok) toast.success(message)
    else toast.error(message)
    setAnyDraft("")
    await refreshContext()
  }

  async function toggleKeyFallback(value: boolean): Promise<void> {
    const result = await runAction("set_key_fallback", { enabled: value }, "fallback")
    if (!result) return
    const ok = resultOk(result)
    const message = resultMessage(result) || (ok ? t("panel.messages.fallbackSaved") : t("panel.messages.fallbackFailed"))
    showNotice(message, !ok)
    if (ok) toast.success(message)
    else toast.error(message)
    await refreshContext()
  }

  function renderExaCard() {
    return (
      <Card title={t("panel.exa.title")}>
        <Stack>
          <Grid cols={2}>
            <StatCard
              label={t("panel.key.label")}
              value={keyCards.length ? t("panel.keys.count", {
                usable: usableKeys, total: keyCards.length,
              }) : maskOrDash(maskedKey)}
            />
            <StatCard label={t("panel.key.stateLabel")} value={keyBadge()} />
          </Grid>
          {!exaConfigured ? <Text>{t("panel.trial.line1")}</Text> : null}
          {keyCards.map((card, index) => (
            <Inline key={asString(card.fingerprint, String(index))} gap={3} wrap justify="space-between">
              <Inline gap={2} wrap>
                <Text>{asString(card.masked, "-")}</Text>
                {keyCardBadge(card)}
                {card.current ? <Text>{t("panel.keys.current")}</Text> : null}
              </Inline>
              <Button
                tone="danger"
                disabled={busy("clear") || !canCall("remove_exa_key")}
                onClick={() => removeOneKey(card)}
              >
                {t("panel.actions.removeOne")}
              </Button>
            </Inline>
          ))}
          <Switch
            checked={keyFallback}
            label={t("panel.key.fallbackLabel")}
            disabled={busy("fallback") || !keyFallbackKnown || !canCall("set_key_fallback")}
            onChange={(value) => toggleKeyFallback(value)}
          />
          {lastError ? <Alert tone="warning">{lastError}</Alert> : null}
          {replaceOpen ? renderKeyField("panel.guide.keyField.placeholderReplace") : null}
          <Inline gap={3} wrap>
            <Button tone="info" disabled={busy("test") || !canCall("test_exa_key")} onClick={testKey}>
              {label("test", "panel.actions.testAll")}
            </Button>
            <Button
              tone="primary"
              disabled={busy("save") || !canCall("save_exa_key")}
              onClick={() => {
                setNotice("")
                setKeyError("")
                setReplaceOpen(true)
              }}
            >
              {t("panel.actions.addKey")}
            </Button>
            {replaceOpen ? (
              <Button tone="success" disabled={busy("save") || !canCall("save_exa_key")} onClick={saveKey}>
                {label("save", "panel.actions.saveKey")}
              </Button>
            ) : null}
            {replaceOpen ? (
              <Button
                tone="default"
                disabled={busy("save")}
                onClick={() => {
                  setReplaceOpen(false)
                  setKeyDraft("")
                  setKeyError("")
                }}
              >
                {t("panel.actions.cancel")}
              </Button>
            ) : null}
            {keyCards.length ? (
              <Button tone="danger" disabled={busy("clear") || !canCall("clear_exa_key")} onClick={removeKey}>
                {label("clear", "panel.actions.clearAll")}
              </Button>
            ) : null}
          </Inline>
          {/* The old first-run guide lives here now, and folds itself once a key exists. */}
          <Accordion id="exa-register" title={t("panel.exa.accRegister")} open={!exaConfigured}>
            <Steps>
              <Step index="1" title={t("panel.guide.step1.title")}>
                <Stack>
                  <Text>{t("panel.guide.step1.line1")}</Text>
                  <CodeBlock>{EXA_KEY_PAGE}</CodeBlock>
                  <Inline gap={2} wrap>
                    <Button tone="default" onClick={() => copyRegisterUrl(EXA_KEY_PAGE)}>
                      {t("panel.guide.copyUrl")}
                    </Button>
                    <Text>{t("panel.guide.step1.line1b")}</Text>
                  </Inline>
                  <Text>{t("panel.guide.step1.line2")}</Text>
                  <Text>{t("panel.guide.step1.line3")}</Text>
                  <Text>{t("panel.guide.step1.qIntro")}</Text>
                  <Text>{t("panel.guide.step1.q1")}</Text>
                  <Text>{t("panel.guide.step1.q2")}</Text>
                  <Text>{t("panel.guide.step1.q3")}</Text>
                  <Text>{t("panel.guide.step1.qAfter")}</Text>
                  <Text>{t("panel.guide.freeNote")}</Text>
                </Stack>
              </Step>
              <Step index="2" title={t("panel.exa.step2.title")}>
                <Text>{t("panel.exa.pasteLine")}</Text>
              </Step>
              <Step index="3" title={t("panel.exa.step3.title")}>
                <Text>{t("panel.exa.saveLine")}</Text>
              </Step>
            </Steps>
          </Accordion>
          <Accordion id="keys-help" title={t("panel.keys.helpTitle")} open={false}>
            <Text>{t("panel.keys.help")}</Text>
            <Text>{t("panel.key.fallbackHelp")}</Text>
            <Text>{quotaNote}</Text>
            <Text>{t("panel.guide.quota")}</Text>
          </Accordion>
          {!actionRegistryLoaded ? <Text>{t("panel.errors.actionRegistryMissing")}</Text> : null}
        </Stack>
      </Card>
    )
  }

  function renderAnyCard() {
    return (
      <Card title={t("panel.any.title")}>
        <Stack>
          <Grid cols={2}>
            <StatCard label={t("panel.any.label")} value={maskOrDash(anyMasked)} />
            <StatCard label={t("panel.any.stateLabel")} value={anyKeyBadge()} />
          </Grid>
          <Text>{t("panel.any.line")}</Text>
          <Field
            label={t("panel.any.fieldLabel")}
            help={t("panel.any.fieldHelp")}
            error={anyError}
            required
          >
            <PasswordInput
              value={anyDraft}
              placeholder={t("panel.any.placeholder")}
              disabled={pending === "anysave"}
              onChange={(value) => setAnyDraft(value)}
            />
          </Field>
          <Inline gap={3} wrap>
            <Button
              tone="success"
              disabled={busy("anysave") || !canCall("set_anysearch_key")}
              onClick={saveAnyKey}
            >
              {label("anysave", "panel.actions.saveKey")}
            </Button>
            <Button
              tone="info"
              disabled={busy("anytest") || !canCall("test_anysearch_key")}
              onClick={testAnyKey}
            >
              {label("anytest", "panel.actions.testOne")}
            </Button>
            {anyMasked ? (
              <Button
                tone="danger"
                disabled={busy("anyclear") || !canCall("clear_anysearch_key")}
                onClick={clearAnyKey}
              >
                {label("anyclear", "panel.actions.removeOne")}
              </Button>
            ) : null}
          </Inline>
          <Accordion id="any-register" title={t("panel.any.accRegister")} open={!anyConfigured}>
            <Text>{t("panel.any.help")}</Text>
            <Text>{t("panel.any.registerLine1")}</Text>
            <CodeBlock>{ANYSEARCH_KEY_PAGE}</CodeBlock>
            <Inline gap={2} wrap>
              <Button tone="default" onClick={() => copyRegisterUrl(ANYSEARCH_KEY_PAGE)}>
                {t("panel.guide.copyUrl")}
              </Button>
              <Text>{t("panel.guide.step1.line1b")}</Text>
            </Inline>
            <Text>{t("panel.any.registerLine2")}</Text>
          </Accordion>
        </Stack>
      </Card>
    )
  }

  function renderAccountNoteCard() {
    return (
      <Card title={t("panel.note.title")}>
        <Text>{t("panel.note.mail")}</Text>
      </Card>
    )
  }

  function renderLastSearchCard() {
    const ok = asBool(lastSearch.ok, false)
    const backend = asString(lastSearch.backend, "-")
    const attempted = asList(lastSearch.attempted)
    const message = asString(lastSearch.message, "")
    const fellBack = attempted.length > 1
    const count = Number(lastSearch.count)
    const countText = Number.isFinite(count) ? String(count) : "-"
    return (
      <Card title={t("panel.last.title")}>
        <Stack>
          {!hasLastSearch ? <Text>{t("panel.last.none")}</Text> : null}
          {hasLastSearch ? (
            <Grid cols={3}>
              <StatCard label={t("panel.last.backendLabel")} value={backend} />
              <StatCard label={t("panel.last.countLabel")} value={countText} />
              <StatCard label={t("panel.last.msLabel")} value={latencyText(lastSearch.ms) || "-"} />
            </Grid>
          ) : null}
          {hasLastSearch ? (
            <Inline gap={2} wrap align="center">
              <StatusBadge tone={ok ? "success" : "danger"} label={ok ? t("panel.last.ok") : t("panel.last.fail")} />
              <Text>{t("panel.last.meta", {
                at: asString(lastSearch.at, "-"),
                chars: asNumber(lastSearch.query_len),
                requested: asNumber(lastSearch.requested),
              })}</Text>
            </Inline>
          ) : null}
          {hasLastSearch && !ok && message ? (
            <Alert tone="warning">{t("panel.last.error", { detail: message })}</Alert>
          ) : null}
          {hasLastSearch ? (
            <Accordion id="last-chain" title={t("panel.last.accChain")} open={false}>
              {fellBack ? <Text>{t("panel.last.attempted", { chain: attempted.join(" → ") })}</Text> : null}
              {fellBack ? <Text>{t("panel.last.fellBack", { first: attempted[0], backend })}</Text> : null}
              <Text>{t("panel.last.help")}</Text>
            </Accordion>
          ) : null}
        </Stack>
      </Card>
    )
  }

  function renderChainCard() {
    return (
      <Card title={t("panel.chain.title")}>
        <Stack>
          {shownChain.length ? (
            <Inline gap={2} wrap>
              {shownChain.map((item) => (
                <StatusBadge key={item} tone="primary" label={item} />
              ))}
            </Inline>
          ) : (
            <Text>{t("panel.chain.empty")}</Text>
          )}
          <Text>{t("panel.chain.proxyLine", { mode: proxyMode })}</Text>
        </Stack>
      </Card>
    )
  }

  function renderProxyCard() {
    return (
      <Card title={t("panel.net.title")}>
        <Stack>
          <Inline gap={3} wrap align="center" justify="space-between">
            <Switch
              checked={ssrfFakeIp}
              label={t("panel.net.fakeIpLabel")}
              disabled={busy("ssrf") || !ssrfFakeIpKnown || !canCall("set_ssrf_guard")}
              onChange={(value) => toggleSsrfGuard(value)}
            />
            <StatusBadge
              tone={!ssrfFakeIpKnown ? "info" : ssrfFakeIp ? "success" : "warning"}
              label={!ssrfFakeIpKnown ? t("panel.net.unknown") : ssrfFakeIp ? t("panel.net.fakeIpOn") : t("panel.net.fakeIpOff")}
            />
          </Inline>
          <Accordion id="net-fakeip" title={t("panel.net.accFakeIp")} open={false}>
            <Text>{t("panel.net.fakeIpHelp")}</Text>
          </Accordion>
        </Stack>
      </Card>
    )
  }

  function renderHostCard() {
    // Read-only by design. Pressing the host's toggle from here meant POSTing
    // /plugin/web_search/stop, and a host that is reloading its plugin list answers
    // that 8+ seconds late -- late enough that our client had already given up, so
    // the panel could not tell "the built-in is stopped" from "it failed" and kept
    // reporting a landed change as an error. Stopping it is one click in the host's
    // plugin centre and persists across restarts, so all we do here is tell the
    // truth about whether it is still running.
    let hint = t("panel.host.help")
    if (!hostKnown) hint = t("panel.host.stateUnknown")
    else if (!hostExists) hint = t("panel.host.notFound")
    return (
      <Card title={t("panel.host.title")}>
        <Stack>
          <Inline gap={3} wrap align="center" justify="space-between">
            <Text>{t("panel.host.label")}</Text>
            <StatusBadge
              tone={!hostKnown ? "info" : hostRunning ? "warning" : "success"}
              label={!hostKnown ? t("panel.host.stateUnknown") : hostRunning ? t("panel.host.stateRunning") : t("panel.host.stateStopped")}
            />
          </Inline>
          <Text>{hint}</Text>
          {hostKnown && hostExists && hostRunning ? <Alert tone="warning">{t("panel.host.stopHere")}</Alert> : null}
          <Inline gap={3} wrap>
            <Button tone="default" disabled={busy("hostcheck") || !canCall("get_host_search")} onClick={checkHostSearch}>
              {label("hostcheck", "panel.actions.checkHost")}
            </Button>
          </Inline>
          <Accordion id="host-why" title={t("panel.host.accWhy")} open={false}>
            <Text>{t("panel.host.line")}</Text>
            <Text>{t("panel.host.why")}</Text>
            <Text>{t("panel.host.gate")}</Text>
          </Accordion>
          <Accordion id="host-cost" title={t("panel.host.accCost")} open={false}>
            <Text>{t("panel.host.impact")}</Text>
            <Text>{t("panel.host.tradeoff")}</Text>
          </Accordion>
          <Accordion id="host-manual" title={t("panel.host.accManual")} open={false}>
            <Text>{t("panel.host.manualWhy")}</Text>
          </Accordion>
        </Stack>
      </Card>
    )
  }

  function renderDiagnoseCard() {
    return (
      <Card title={t("panel.diag.title")}>
        <Stack>
          <Text>{t("panel.diag.help")}</Text>
          <Switch
            checked={withProxy}
            label={t("panel.diag.dual")}
            disabled={busy("diagnose")}
            onChange={(value) => setDualPathDraft(value ? "on" : "off")}
          />
          <Text>{dualPathDraft === "" ? t("panel.diag.dualAuto", { state: proxyDetected ? t("panel.diag.on") : t("panel.diag.off") }) : t("panel.diag.dualHelp")}</Text>
          <Inline gap={3} wrap>
            <Button tone="primary" disabled={busy("diagnose") || !canCall("diagnose_network")} onClick={runDiagnose}>
              {label("diagnose", "panel.actions.runDiagnose")}
            </Button>
            <RefreshButton label={t("panel.actions.refresh")} />
          </Inline>
          <Alert tone="warning">{t("panel.diag.cost")}</Alert>
          {/* Without this the table is ambiguous: users read "搜索来源" as "the host's
              search" or "the AI model", and never learn whether exa was probed with
              their key or anonymously. It belongs next to the results, not above them. */}
          <Accordion id="diag-scope" title={t("panel.diag.accScope")} open={false}>
            <Text>{t("panel.diag.scope", { chain: shownChain.join(" → ") || "-" })}</Text>
            <Text>{maskedKey ? t("panel.diag.exaWithKey", { masked: maskedKey }) : t("panel.diag.exaAnonymous")}</Text>
            <Text>{hostRunning ? t("panel.diag.hostUnrelatedOn") : t("panel.diag.hostUnrelatedOff")}</Text>
          </Accordion>
          {diagnose ? (
            <Stack>
              <Text>{t("panel.diag.summary")}</Text>
              <Alert tone="info">{diagnose.summary || t("panel.diag.emptyResult")}</Alert>
              {diagnose.rows.length ? (
                <DataTable
                  data={diagnose.rows}
                  rowKey="name"
                  columns={[
                    { key: "name", label: t("panel.diag.column.name") },
                    { key: "direct", label: t("panel.diag.column.direct") },
                    { key: "proxied", label: t("panel.diag.column.proxied") },
                    { key: "ms", label: t("panel.diag.column.ms") },
                    { key: "note", label: t("panel.diag.column.note") },
                  ]}
                  emptyText={t("panel.diag.none")}
                />
              ) : (
                <Text>{t("panel.diag.none")}</Text>
              )}
              {diagnose.recommended.length ? (
                <Text>{t("panel.diag.recommended", { chain: diagnose.recommended.join(" → ") })}</Text>
              ) : null}
            </Stack>
          ) : null}
        </Stack>
      </Card>
    )
  }

  function renderHome() {
    // Three panes instead of one long scroll: what happened / what you can change /
    // how to debug it. The built-in-search card belongs to "what happened" -- it is a
    // fact about the host, not something this plugin configures. Keys are two separate
    // cards because the two providers do different jobs (a ring vs a single upgrade).
    const tabs = [
      {
        id: "status",
        label: t("panel.tabs.status"),
        content: <Stack>{renderLastSearchCard()}{renderChainCard()}{renderHostCard()}</Stack>,
      },
      {
        id: "advanced",
        label: t("panel.tabs.advanced"),
        content: <Stack>{renderExaCard()}{renderAnyCard()}{renderAccountNoteCard()}{renderProxyCard()}</Stack>,
      },
      {
        id: "diag",
        label: t("panel.tabs.diag"),
        content: <Stack>{renderDiagnoseCard()}</Stack>,
      },
    ]
    return (
      <Stack>
        {renderNotice()}
        <Tabs id="main" items={tabs} />
      </Stack>
    )
  }

  return (
    <Page title={t("panel.title")} subtitle={t("panel.subtitle")}>
      <Stack>
        {showStart ? renderStartPage() : renderHome()}
      </Stack>
    </Page>
  )
}
