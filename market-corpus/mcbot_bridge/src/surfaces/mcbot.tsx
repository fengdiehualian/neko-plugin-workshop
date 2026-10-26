import {
  Alert,
  Button,
  ButtonGroup,
  Card,
  KeyValue,
  Page,
  Stack,
  StatusBadge,
  Text,
  useEffect,
  useRef,
  useState,
} from "@neko/plugin-ui"
import type { PluginSurfaceProps, Tone } from "@neko/plugin-ui"

type LocaleKey = "zh-CN" | "en"

const STATUS_REFRESH_INTERVAL_MS = 5000

const COPY: Record<LocaleKey, {
  title: string
  refresh: string
  stop: string
  stopping: string
  connected: string
  disconnected: string
  companion: string
  companionUnknown: string
  task: string
  taskIdle: string
  questions: string
  questionsNone: string
  stopHint: string
  errorPrefix: string
}> = {
  "zh-CN": {
    title: "mcbot 同伴",
    refresh: "刷新",
    stop: "停止任务",
    stopping: "正在停止…",
    connected: "已连上",
    disconnected: "未连接",
    companion: "当前身体",
    companionUnknown: "未知",
    task: "当前任务",
    taskIdle: "空闲",
    questions: "待回答的问题",
    questionsNone: "无",
    stopHint: "停止会让同伴放下当前全部任务；她没接受时面板会提示。",
    errorPrefix: "出错：",
  },
  en: {
    title: "mcbot companion",
    refresh: "Refresh",
    stop: "Stop task",
    stopping: "Stopping…",
    connected: "Connected",
    disconnected: "Disconnected",
    companion: "Current body",
    companionUnknown: "unknown",
    task: "Current task",
    taskIdle: "idle",
    questions: "Open questions",
    questionsNone: "none",
    stopHint: "Stopping makes the companion drop everything she is doing; if she refuses, the panel says so.",
    errorPrefix: "Error: ",
  },
}

function resolveLocale(locale: string | undefined): LocaleKey {
  return locale && locale.toLowerCase().startsWith("zh") ? "zh-CN" : "en"
}

function unwrapActionResult(envelope: any): Record<string, any> {
  const inner = envelope?.result ?? envelope?.data ?? envelope
  return inner && typeof inner === "object" ? inner : {}
}

type PanelState = {
  loading: boolean
  stopping: boolean
  connected: boolean | null
  companion: string
  task: string
  questions: number
  notice: string
  error: string
}

export default function McbotCompanionPanel(props: PluginSurfaceProps) {
  const copy = COPY[resolveLocale(props.locale)]

  const [state, setState] = useState<PanelState>({
    loading: false,
    stopping: false,
    connected: null,
    companion: "",
    task: "",
    questions: 0,
    notice: "",
    error: "",
  })

  const refreshingRef = useRef(false)
  const unmountedRef = useRef(false)

  const refresh = async () => {
    if (refreshingRef.current || unmountedRef.current) return
    refreshingRef.current = true
    setState((prev) => ({ ...prev, loading: true, error: "" }))
    try {
      // Hosted surfaces run in a sandboxed iframe, so the only supported path
      // to the plugin is props.api.call, which needs the UI action below.
      const envelope = await props.api.call("mcbot_status")
      if (unmountedRef.current) return
      const data = unwrapActionResult(envelope)
      setState((prev) => ({
        ...prev,
        loading: false,
        connected: Boolean(data.connected),
        companion: String(data.companion || ""),
        task: String(data.pending_task || ""),
        questions: Number(data.pending_questions || 0),
      }))
    } catch (exc: any) {
      if (unmountedRef.current) return
      const raw = String(exc?.message || exc)
      const code = String(exc?.code || "")
      const notRunning = code === "PLUGIN_NOT_RUNNING" || /not running|not started/i.test(raw)
      setState((prev) => ({
        ...prev,
        loading: false,
        connected: false,
        error: notRunning ? "" : copy.errorPrefix + raw,
      }))
    } finally {
      refreshingRef.current = false
    }
  }

  const stop = async () => {
    if (state.stopping) return
    setState((prev) => ({ ...prev, stopping: true, notice: "", error: "" }))
    try {
      const envelope = await props.api.call("mcbot_stop")
      const data = unwrapActionResult(envelope)
      setState((prev) => ({
        ...prev,
        stopping: false,
        notice: String(data.summary || ""),
      }))
      refresh()
    } catch (exc: any) {
      setState((prev) => ({
        ...prev,
        stopping: false,
        error: copy.errorPrefix + String(exc?.message || exc),
      }))
    }
  }

  useEffect(() => {
    refresh()
    const timer = window.setInterval(refresh, STATUS_REFRESH_INTERVAL_MS)
    return () => {
      unmountedRef.current = true
      window.clearInterval(timer)
    }
  }, [])

  const tone: Tone =
    state.connected === null ? "default" : state.connected ? "success" : "warning"
  const badgeText =
    state.connected === null
      ? copy.refresh
      : state.connected
        ? copy.connected
        : copy.disconnected

  const items = [
    { key: "companion", label: copy.companion, value: state.companion || copy.companionUnknown },
    { key: "task", label: copy.task, value: state.task || copy.taskIdle },
    {
      key: "questions",
      label: copy.questions,
      value: state.questions ? String(state.questions) : copy.questionsNone,
    },
  ]

  return (
    <Page title={copy.title}>
      <Card>
        <Stack>
          <StatusBadge tone={tone}>{badgeText}</StatusBadge>
          {state.error ? <Alert tone="warning">{state.error}</Alert> : null}
          {state.notice ? <Alert tone="info">{state.notice}</Alert> : null}
          <KeyValue items={items} />
          <ButtonGroup>
            <Button onClick={refresh} disabled={state.loading}>
              {copy.refresh}
            </Button>
            <Button tone="danger" onClick={stop} disabled={state.stopping}>
              {state.stopping ? copy.stopping : copy.stop}
            </Button>
          </ButtonGroup>
          <Text>{copy.stopHint}</Text>
        </Stack>
      </Card>
    </Page>
  )
}
