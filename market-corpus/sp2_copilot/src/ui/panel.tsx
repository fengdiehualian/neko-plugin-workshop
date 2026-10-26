// 和猫娘一起开飞机（简单飞机2副驾驶）插件 —— 副驾驶仪表盘面板
// Copyright (C) 2026 星河拓航工作室 (Galaxy Exploration Studio)
//
// 本文件是 sp2_copilot 插件的一部分，以 GNU General Public License v3.0 许可发布。
// 完整条款见仓库根目录的 LICENSE 文件。
//
// 这个面板在宿主提供的 sandbox iframe 里运行，**不是**普通网页：
//   · 只允许 import 相对文件与 "@neko/plugin-ui"，别的裸模块解析不了；
//   · 拿不到 fetch / window.electronShell，一切数据都走 props.api.call；
//   · 面板通过 props.api.call 调到的每个动作，都必须在 plugin.toml 的
//     permissions 里声明 action:call，并在后端用 @ui.action 暴露（见 ui_api.py）。
//
// ⚠️ 本插件是**只读副驾驶**：面板上刻意不做任何「帮我操作」的按钮。
//     所有结论都是「看」和「说」，动手的永远是玩家。

import {
  Alert,
  Button,
  Card,
  DataTable,
  Divider,
  Field,
  Grid,
  Inline,
  Input,
  KeyValue,
  NumberInput,
  Page,
  Stack,
  StatCard,
  StatusBadge,
  Switch,
  Text,
  Tip,
  Toolbar,
  ToolbarGroup,
  useEffect,
  useLocalState,
  useRef,
  useState,
  useToast,
} from "@neko/plugin-ui"
import type { PluginSurfaceProps } from "@neko/plugin-ui"

// ─────────────────────────────────────────────────────────────────────────
// 常量
// ─────────────────────────────────────────────────────────────────────────

// 宿主桥接（ui-kit 的 requestHost）默认超时只有 30 秒，而快照要连着读四个接口。
// 每个动作显式带上限，且必须 ≥ 后端入口声明的 timeout，否则面板会先超时。
const CALL_TIMEOUT_MS = 60_000
const TIMEOUTS: Record<string, number> = {
  sp2_ui_snapshot: 50_000,
  sp2_briefing: 50_000,
  sp2_status: 40_000,
  sp2_ui_ping: 15_000,
}

function timeoutFor(actionId: string): number {
  return TIMEOUTS[actionId] || CALL_TIMEOUT_MS
}

const TABS = [
  { id: "overview", label: "总览" },
  { id: "instruments", label: "仪表" },
  { id: "checklist", label: "检查单" },
  { id: "navigation", label: "导航" },
  { id: "settings", label: "设置" },
]

// 告警等级：后端只给 level，文案和配色由面板维护（后端也带了一份兜底）。
const FALLBACK_LEVELS = [
  { id: "danger", label: "紧急", tone: "danger" },
  { id: "warning", label: "注意", tone: "warning" },
  { id: "caution", label: "提示", tone: "info" },
  { id: "info", label: "提示", tone: "info" },
]

const FALLBACK_PHASES = [
  { id: "menu", label: "未进入飞行", tone: "default" },
  { id: "parked", label: "地面停机", tone: "info" },
  { id: "taxi", label: "地面滑行", tone: "info" },
  { id: "takeoff_roll", label: "起飞滑跑", tone: "warning" },
  { id: "rollout", label: "着陆滑跑", tone: "warning" },
  { id: "initial_climb", label: "初始爬升", tone: "success" },
  { id: "climb", label: "爬升", tone: "success" },
  { id: "cruise", label: "巡航", tone: "success" },
  { id: "descent", label: "下降", tone: "info" },
  { id: "approach", label: "进近着陆", tone: "warning" },
  { id: "crashed", label: "已坠毁", tone: "danger" },
  { id: "unknown", label: "阶段未知", tone: "default" },
]

// ─────────────────────────────────────────────────────────────────────────
// 类型
// ─────────────────────────────────────────────────────────────────────────

type Flight = {
  airspeed?: number
  true_airspeed?: number
  ground_speed?: number
  vertical_speed?: number
  altitude_agl?: number
  altitude_msl?: number
  terrain_height?: number
  heading?: number
  pitch?: number
  roll?: number
  aoa?: number
  sideslip?: number
  g_force?: number
  mach?: number
  stalled?: boolean
  stall_warning?: boolean
  on_ground?: boolean
  crashed?: boolean
}

type Controls = {
  throttle?: number
  pitch_input?: number
  roll_input?: number
  yaw_input?: number
  brake?: number
  trim?: number
  flaps?: number
  airbrake?: number
  gear_down?: boolean
  autopilot?: boolean
  autopilot_mode?: string
}

type WarningItem = {
  code?: string
  level?: string
  text?: string
  action?: string
}

type ChecklistItem = {
  id?: string
  label?: string
  status?: string
  detail?: string
}

type Checklist = {
  phase?: string
  phase_label?: string
  items?: ChecklistItem[]
  done_count?: number
  pending_count?: number
  unknown_count?: number
  total_count?: number
}

type Snapshot = {
  connected?: boolean
  error?: string
  latency_ms?: number
  bridge_url?: string
  state?: any
  aircraft?: any
  navigation?: any
  environment?: any
  phase?: string
  phase_label?: string
  status_summary?: string
  warning_summary?: string
  checklist_summary?: string
  navigation_summary?: string
  warnings?: WarningItem[]
  checklist?: Checklist
  errors?: Record<string, string>
  fetched_at?: number
}

type PanelState = {
  connected?: boolean | null
  last_error?: string
  bridge_url?: string
  read_only?: boolean
  config?: Record<string, any>
  levels?: Array<{ id: string; label: string; tone: string }>
  phases?: Array<{ id: string; label: string; tone: string }>
}

type SettingsDraft = Record<string, any>

// ─────────────────────────────────────────────────────────────────────────
// 小工具（纯函数，无 hooks）
// ─────────────────────────────────────────────────────────────────────────

/** props.api.call 解析成 `{plugin_id, action_id, result}`；也容错裸 dict 形状。 */
function unwrap(envelope: any): Record<string, any> {
  if (envelope && typeof envelope === "object") {
    const nested = (envelope as any).result
    if (nested && typeof nested === "object") return nested as Record<string, any>
    return envelope as Record<string, any>
  }
  return {}
}

type Failure = { notRunning: boolean; message: string }

function describeError(error: any): Failure {
  const message = String((error && (error.message || error.error)) || error || "未知错误")
  const code = String((error && error.code) || "")
  const notRunning =
    code === "PLUGIN_NOT_RUNNING" || /PLUGIN_NOT_RUNNING|插件未启动|not running/i.test(message)
  return {
    notRunning,
    message: code && !message.includes(code) ? `${code}：${message}` : message,
  }
}

function isNum(value: any): value is number {
  return typeof value === "number" && Number.isFinite(value)
}

function num(value: any, digits = 1, suffix = ""): string {
  return isNum(value) ? `${value.toFixed(digits)}${suffix}` : "未知"
}

function speed(value: any): string {
  return isNum(value) ? `${value.toFixed(1)} m/s（${(value * 3.6).toFixed(0)} km/h）` : "未知"
}

function vsText(value: any): string {
  if (!isNum(value)) return "未知"
  if (value > 0.5) return `+${value.toFixed(1)} m/s（爬升）`
  if (value < -0.5) return `${value.toFixed(1)} m/s（下降）`
  return `${value.toFixed(1)} m/s（平飞）`
}

function altText(value: any): string {
  if (!isNum(value)) return "未知"
  if (Math.abs(value) >= 10000) return `${(value / 1000).toFixed(2)} km`
  return `${value.toFixed(0)} m`
}

function headingText(value: any): string {
  if (!isNum(value)) return "未知"
  const normalized = ((value % 360) + 360) % 360
  return `${normalized.toFixed(0)}°`
}

function pctText(value: any, digits = 0): string {
  if (!isNum(value)) return "未知"
  const scaled = value >= -1 && value <= 1 ? value * 100 : value
  return `${scaled.toFixed(digits)}%`
}

function throttleText(value: any): string {
  return isNum(value) ? `${Math.round(Math.min(Math.max(value, 0), 1) * 100)}%` : "未知"
}

function asText(value: any): string {
  if (typeof value === "string") return value
  if (value === null || value === undefined) return ""
  if (typeof value === "number" || typeof value === "boolean") return String(value)
  return ""
}

function onOff(value: any, yes = "是", no = "否"): string {
  if (value === null || value === undefined) return "未知"
  return value ? yes : no
}

function gearText(value: any): string {
  const text = asText(value).toLowerCase()
  if (text === "up" || text === "retracted") return "收起"
  if (text === "down" || text === "deployed" || text === "extended") return "放下"
  if (text === "moving" || text === "transition") return "收放中"
  if (value === null || value === undefined) return "未知"
  return value ? "放下" : "收起"
}

function numberOr(value: any, fallback: number): number {
  // 空的数字输入框会给出 ""，而 Number("") 是 0 —— 那不是用户想填的值。
  if (value === "" || value === null || value === undefined) return fallback
  const parsed = typeof value === "number" ? value : Number(value)
  return Number.isFinite(parsed) ? parsed : fallback
}

function phaseMeta(phase: string | undefined, phases: Array<{ id: string; label: string; tone: string }>) {
  const found = phases.find((item) => item.id === phase)
  if (found) return found
  return { id: phase || "unknown", label: asText(phase) || "阶段未知", tone: "default" }
}

function levelMeta(level: string | undefined, levels: Array<{ id: string; label: string; tone: string }>) {
  const found = levels.find((item) => item.id === level)
  if (found) return found
  return { id: level || "info", label: "提示", tone: "info" }
}

function statusText(status: string | undefined): { label: string; tone: string } {
  switch (status) {
    case "done":
      return { label: "已完成", tone: "success" }
    case "todo":
      return { label: "待办", tone: "warning" }
    default:
      return { label: "读不到", tone: "default" }
  }
}

/**
 * 姿态仪（人工地平仪）。
 *
 * 用纯 SVG 画，不依赖任何 CSS 类：天空/地面两块跟着俯仰上下平移，
 * 整组跟着滚转旋转，外面再叠一个固定的飞机符号。
 */
function AttitudeIndicator(props: { pitch?: number; roll?: number }) {
  const pitch = isNum(props.pitch) ? Math.min(Math.max(props.pitch, -30), 30) : 0
  const roll = isNum(props.roll) ? props.roll : 0
  const size = 180
  const center = size / 2
  const radius = 78
  // 1 度俯仰 = 2 像素；滚转取负号是因为 SVG 的旋转方向与机体滚转方向相反。
  const offset = pitch * 2
  const clipId = "sp2-adi-clip"

  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} role="img" aria-label="姿态仪">
      <defs>
        <clipPath id={clipId}>
          <circle cx={center} cy={center} r={radius} />
        </clipPath>
      </defs>
      <circle cx={center} cy={center} r={radius} fill="#dfe6ee" stroke="#98a4b3" strokeWidth="2" />
      <g clipPath={`url(#${clipId})`}>
        <g transform={`rotate(${-roll} ${center} ${center}) translate(0 ${offset})`}>
          <rect x={center - radius * 2} y={center - radius * 4} width={radius * 4} height={radius * 4} fill="#5b9bd5" />
          <rect x={center - radius * 2} y={center} width={radius * 4} height={radius * 4} fill="#8a6a4a" />
          <line
            x1={center - radius * 2}
            y1={center}
            x2={center + radius * 2}
            y2={center}
            stroke="#ffffff"
            strokeWidth="2"
          />
          {[-20, -10, 10, 20].map((tick) => (
            <g key={tick}>
              <line
                x1={center - 22}
                y1={center - tick * 2}
                x2={center + 22}
                y2={center - tick * 2}
                stroke="#ffffff"
                strokeWidth="1"
              />
            </g>
          ))}
        </g>
      </g>
      {/* 固定的飞机符号 */}
      <g stroke="#1f2933" strokeWidth="3" fill="none">
        <line x1={center - 44} y1={center} x2={center - 14} y2={center} />
        <line x1={center + 14} y1={center} x2={center + 44} y2={center} />
        <circle cx={center} cy={center} r="3" fill="#1f2933" stroke="none" />
      </g>
      {/* 滚转指针 */}
      <g stroke="#1f2933" strokeWidth="2" fill="none">
        <line x1={center} y1={center - radius} x2={center} y2={center - radius + 10} />
      </g>
      <text x={center} y={size - 8} textAnchor="middle" fontSize="12" fill="#52606d">
        {`俯仰 ${num(pitch)}° · 滚转 ${num(roll)}°`}
      </text>
    </svg>
  )
}

// ─────────────────────────────────────────────────────────────────────────
// 面板
// ─────────────────────────────────────────────────────────────────────────

export default function Sp2CopilotPanel(props: PluginSurfaceProps<PanelState>) {
  const state = (props.state || {}) as PanelState
  const effective = (state.config || {}) as Record<string, any>
  const levels = Array.isArray(state.levels) && state.levels.length > 0 ? state.levels : FALLBACK_LEVELS
  const phases = Array.isArray(state.phases) && state.phases.length > 0 ? state.phases : FALLBACK_PHASES

  const toast = useToast()

  // props.api 每次渲染都可能是新对象；放进 ref，避免把它写进依赖导致轮询重建。
  const apiRef = useRef(props.api)
  apiRef.current = props.api

  const [tab, setTab] = useLocalState<string>("tab", "overview")
  const [snapshot, setSnapshot] = useLocalState<Snapshot>("snapshot", {})
  const [lastError, setLastError] = useLocalState<string>("lastError", "")
  const [pluginMissing, setPluginMissing] = useLocalState<boolean>("pluginMissing", false)
  const [autoRefresh, setAutoRefresh] = useLocalState<boolean>("autoRefresh", true)
  const [busyAction, setBusyAction] = useState("")
  const [draft, setDraft] = useLocalState<SettingsDraft>("settingsDraft", {})

  const pollingRef = useRef(false)
  const actionRef = useRef(false)
  const aliveRef = useRef(true)

  const flight: Flight = ((snapshot.state || {}).flight || {}) as Flight
  const controls: Controls = ((snapshot.state || {}).controls || {}) as Controls
  const aircraft: any = snapshot.aircraft || {}
  const navigation: any = snapshot.navigation || {}
  const environment: any = snapshot.environment || {}
  const warnings: WarningItem[] = Array.isArray(snapshot.warnings) ? snapshot.warnings! : []
  const checklist: Checklist = (snapshot.checklist || {}) as Checklist
  const items: ChecklistItem[] = Array.isArray(checklist.items) ? checklist.items! : []

  const connected = snapshot.connected === true
  const neverProbed = snapshot.fetched_at === undefined && !lastError
  const phase = phaseMeta(snapshot.phase, phases)
  const pollMs = Math.round(numberOr(effective.poll_interval_seconds, 3) * 1000)

  // ── 草稿初始化：生效值到手后灌一次，之后不再覆盖用户正在敲的内容 ──
  useEffect(() => {
    if (!effective || !effective.bridge_url) return
    if (draft && Object.keys(draft).length > 0) return
    setDraft({
      bridge_url: asText(effective.bridge_url),
      timeout_seconds: numberOr(effective.timeout_seconds, 12),
      poll_interval_seconds: numberOr(effective.poll_interval_seconds, 3),
      auto_brief_enabled: effective.auto_brief_enabled !== false,
      auto_brief_interval_seconds: numberOr(effective.auto_brief_interval_seconds, 20),
      terrain_warning_agl_m: numberOr(effective.terrain_warning_agl_m, 300),
      sink_rate_warning_mps: numberOr(effective.sink_rate_warning_mps, 6),
      bank_warning_deg: numberOr(effective.bank_warning_deg, 45),
      low_fuel_warning_percent: numberOr(effective.low_fuel_warning_percent, 20),
      aoa_warning_deg: numberOr(effective.aoa_warning_deg, 12),
      overspeed_warning_ratio: numberOr(effective.overspeed_warning_ratio, 0.9),
    })
  }, [effective.bridge_url, draft])

  // ── 数据读取 ──────────────────────────────────────────────────────────

  async function refreshPanelContext(): Promise<void> {
    try {
      await apiRef.current.refresh()
    } catch (error) {
      void error
    }
  }

  async function loadSnapshot(): Promise<Snapshot | null> {
    try {
      const result = unwrap(
        await apiRef.current.call("sp2_ui_snapshot", {}, { timeoutMs: timeoutFor("sp2_ui_snapshot") }),
      )
      if (!aliveRef.current) return null
      const next = result as Snapshot
      setSnapshot(next)
      setPluginMissing(false)
      setLastError(next.connected ? "" : asText(next.error))
      return next
    } catch (error) {
      const failure = describeError(error)
      if (!aliveRef.current) return null
      setPluginMissing(failure.notRunning)
      setLastError(failure.message)
      setSnapshot((prev) => ({ ...(prev || {}), connected: false, error: failure.message }))
      return null
    }
  }

  async function run(
    actionId: string,
    args: Record<string, any> = {},
    options: { success?: string; refresh?: boolean; refreshContext?: boolean } = {},
  ): Promise<Record<string, any> | null> {
    if (actionRef.current) {
      toast.warning("上一步还没结束，稍等一下再点。")
      return null
    }
    actionRef.current = true
    setBusyAction(actionId)
    try {
      const result = unwrap(await apiRef.current.call(actionId, args, {
        timeoutMs: timeoutFor(actionId),
        userInitiated: true,
      }))
      setPluginMissing(false)
      toast.success(options.success || asText(result.message) || "完成")
      if (options.refresh !== false) await loadSnapshot()
      if (options.refreshContext) await refreshPanelContext()
      return result
    } catch (error) {
      const failure = describeError(error)
      setPluginMissing(failure.notRunning)
      setLastError(failure.message)
      if (failure.notRunning) {
        toast.warning("插件还没启动：先到插件页启动「和猫娘一起开飞机(简单飞机2副驾驶)」。")
      } else {
        toast.error(failure.message)
      }
      return null
    } finally {
      actionRef.current = false
      if (aliveRef.current) setBusyAction("")
    }
  }

  useEffect(() => {
    aliveRef.current = true
    void loadSnapshot()
    return () => {
      aliveRef.current = false
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    if (!autoRefresh) return
    const timer = setInterval(() => {
      if (!aliveRef.current || pollingRef.current || actionRef.current) return
      pollingRef.current = true
      void loadSnapshot().finally(() => {
        pollingRef.current = false
      })
    }, Math.max(1000, pollMs))
    return () => clearInterval(timer)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoRefresh, pollMs])

  function fieldValue(key: string, fallback: any): any {
    const current = (draft || {})[key]
    if (current === undefined || current === null) return fallback
    return current
  }

  function setField(key: string, value: any): void {
    setDraft({ ...(draft || {}), [key]: value })
  }

  async function saveSettings(): Promise<void> {
    const url = asText(fieldValue("bridge_url", effective.bridge_url))
    if (!url.startsWith("http://") && !url.startsWith("https://")) {
      toast.error("桥接地址要以 http:// 或 https:// 开头。")
      return
    }
    await run("sp2_ui_save_settings", {
      bridge_url: url,
      timeout_seconds: numberOr(fieldValue("timeout_seconds", 12), 12),
      poll_interval_seconds: numberOr(fieldValue("poll_interval_seconds", 3), 3),
      auto_brief_enabled: fieldValue("auto_brief_enabled", true) !== false,
      auto_brief_interval_seconds: numberOr(fieldValue("auto_brief_interval_seconds", 20), 20),
      terrain_warning_agl_m: numberOr(fieldValue("terrain_warning_agl_m", 300), 300),
      sink_rate_warning_mps: numberOr(fieldValue("sink_rate_warning_mps", 6), 6),
      bank_warning_deg: numberOr(fieldValue("bank_warning_deg", 45), 45),
      low_fuel_warning_percent: numberOr(fieldValue("low_fuel_warning_percent", 20), 20),
      aoa_warning_deg: numberOr(fieldValue("aoa_warning_deg", 12), 12),
      overspeed_warning_ratio: numberOr(fieldValue("overspeed_warning_ratio", 0.9), 0.9),
    }, { refreshContext: true })
  }

  // ── 各页 ──────────────────────────────────────────────────────────────

  const overviewTab = (
    <Stack>
      {pluginMissing ? (
        <Alert tone="warning">
          插件还没启动，面板暂时拿不到数据。请到 N.E.K.O. 的插件页面启动
          「和猫娘一起开飞机(简单飞机2副驾驶)」，然后点下面的「刷新」。
        </Alert>
      ) : null}

      {!pluginMissing && lastError && !connected ? (
        <Alert tone="danger">{lastError}</Alert>
      ) : null}

      {snapshot.errors && Object.keys(snapshot.errors).length > 0 ? (
        <Alert tone="warning">
          部分数据没读到：
          {Object.entries(snapshot.errors)
            .map(([key, value]) => `${key}：${value}`)
            .join("；")}
        </Alert>
      ) : null}

      <Card title="连接">
        <Stack>
          <Inline align="center" justify="space-between" wrap>
            <Inline align="center" gap={8}>
              <StatusBadge
                tone={connected ? "success" : neverProbed ? "default" : "danger"}
                label={pluginMissing ? "插件未启动" : connected ? "已连接" : neverProbed ? "正在检测…" : "未连接"}
              />
              <Text>{asText(snapshot.bridge_url || state.bridge_url) || "（未配置地址）"}</Text>
            </Inline>
            <Inline align="center" gap={8}>
              {isNum(snapshot.latency_ms) && connected ? (
                <Text>{`往返 ${snapshot.latency_ms} 毫秒`}</Text>
              ) : null}
              {snapshot.fetched_at ? (
                <Text>{`更新于 ${new Date(snapshot.fetched_at * 1000).toLocaleTimeString()}`}</Text>
              ) : null}
            </Inline>
          </Inline>
          <Toolbar>
            <ToolbarGroup>
              <Button
                tone="primary"
                disabled={busyAction !== ""}
                onClick={() => void loadSnapshot()}
              >
                刷新
              </Button>
              <Button
                disabled={busyAction !== ""}
                onClick={() => void run("sp2_ui_ping", {}, { success: "已刷新连接状态" })}
              >
                检测连接
              </Button>
              <Button
                disabled={busyAction !== ""}
                onClick={() => void run("sp2_briefing", {}, { success: "简报已刷新" })}
              >
                副驾驶简报
              </Button>
            </ToolbarGroup>
            <ToolbarGroup>
              <Text>{autoRefresh ? `每 ${Math.max(1, Math.round(pollMs / 1000))} 秒自动刷新` : "自动刷新已关闭"}</Text>
              <Button tone="default" onClick={() => setAutoRefresh(!autoRefresh)}>
                {autoRefresh ? "关闭自动刷新" : "开启自动刷新"}
              </Button>
            </ToolbarGroup>
          </Toolbar>
        </Stack>
      </Card>

      <Card title="当前状态">
        <Stack>
          <Inline align="center" gap={8} wrap>
            <StatusBadge tone={phase.tone as any} label={asText(snapshot.phase_label) || phase.label} />
            {flight.stalled || flight.stall_warning ? (
              <StatusBadge tone="danger" label="失速警告" />
            ) : null}
            {warnings.some((item) => item.level === "danger") ? (
              <StatusBadge tone="danger" label="有紧急告警" />
            ) : null}
          </Inline>
          <Text>{asText(snapshot.status_summary) || "（暂无摘要）"}</Text>
          <Grid cols={3} gap={10}>
            <StatCard label="空速" value={speed(flight.airspeed)} />
            <StatCard label="离地高度" value={altText(flight.altitude_agl)} />
            <StatCard label="升降率" value={vsText(flight.vertical_speed)} />
            <StatCard label="航向" value={headingText(flight.heading)} />
            <StatCard label="油门" value={throttleText(controls.throttle)} />
            <StatCard label="过载" value={num(flight.g_force, 2, " G")} />
          </Grid>
        </Stack>
      </Card>

      <Card title="姿态">
        <Stack>
          <Inline align="center" gap={16} wrap>
            <AttitudeIndicator pitch={flight.pitch} roll={flight.roll} />
            <Grid cols={2} gap={10}>
              <StatCard label="迎角 AoA" value={num(flight.aoa, 1, "°")} />
              <StatCard label="侧滑" value={num(flight.sideslip, 1, "°")} />
              <StatCard label="马赫数" value={num(flight.mach, 2)} />
              <StatCard label="海拔" value={altText(flight.altitude_msl)} />
            </Grid>
          </Inline>
        </Stack>
      </Card>

      <Card title="告警">
        <Stack>
          {warnings.length === 0 ? (
            <Alert tone="success">没有告警，各项参数都在正常范围。</Alert>
          ) : (
            <DataTable
              data={warnings.map((item, index) => ({
                key: `${asText(item.code)}-${index}`,
                level: levelMeta(item.level, levels).label,
                text: asText(item.text),
                action: asText(item.action),
              }))}
              rowKey="key"
              columns={[
                { key: "level", label: "等级" },
                { key: "text", label: "内容" },
                { key: "action", label: "建议" },
              ]}
            />
          )}
          {asText(snapshot.warning_summary) ? <Text>{asText(snapshot.warning_summary)}</Text> : null}
        </Stack>
      </Card>
    </Stack>
  )

  const instrumentsTab = (
    <Stack>
      <Card title="飞行仪表">
        <Stack>
          <Grid cols={3} gap={10}>
            <StatCard label="空速 IAS" value={speed(flight.airspeed)} />
            <StatCard label="真空速 TAS" value={speed(flight.true_airspeed)} />
            <StatCard label="地速 GS" value={speed(flight.ground_speed)} />
            <StatCard label="离地高度 AGL" value={altText(flight.altitude_agl)} />
            <StatCard label="海拔 MSL" value={altText(flight.altitude_msl)} />
            <StatCard label="地形高度" value={altText(flight.terrain_height)} />
          </Grid>
          <Divider />
          <KeyValue
            items={[
              { key: "vs", label: "升降率", value: vsText(flight.vertical_speed) },
              { key: "heading", label: "航向", value: headingText(flight.heading) },
              { key: "pitch", label: "俯仰", value: num(flight.pitch, 1, "°") },
              { key: "roll", label: "滚转", value: num(flight.roll, 1, "°") },
              { key: "aoa", label: "迎角 AoA", value: num(flight.aoa, 1, "°") },
              { key: "sideslip", label: "侧滑", value: num(flight.sideslip, 1, "°") },
              { key: "g", label: "过载", value: num(flight.g_force, 2, " G") },
              { key: "mach", label: "马赫数", value: num(flight.mach, 2) },
            ]}
          />
        </Stack>
      </Card>

      <Card title="操纵与构型">
        <KeyValue
          items={[
            { key: "throttle", label: "油门", value: throttleText(controls.throttle) },
            { key: "pitch_input", label: "升降舵输入", value: num(controls.pitch_input, 2) },
            { key: "roll_input", label: "副翼输入", value: num(controls.roll_input, 2) },
            { key: "yaw_input", label: "方向舵输入", value: num(controls.yaw_input, 2) },
            { key: "trim", label: "配平", value: num(controls.trim, 2) },
            { key: "flaps", label: "襟翼", value: pctText(controls.flaps) },
            { key: "airbrake", label: "减速板", value: pctText(controls.airbrake) },
            { key: "brake", label: "刹车", value: pctText(controls.brake) },
            { key: "gear", label: "起落架", value: gearText(controls.gear_down) },
            { key: "ap", label: "自动驾驶", value: onOff(controls.autopilot, "开", "关") },
            { key: "ap_mode", label: "自动驾驶模式", value: asText(controls.autopilot_mode) || "—" },
          ]}
        />
      </Card>

      <Card title="机体">
        <Stack>
          <Grid cols={3} gap={10}>
            <StatCard label="机体" value={asText(aircraft.name) || "—"} />
            <StatCard label="类型" value={asText(aircraft.type) || "—"} />
            <StatCard label="零件数" value={num(aircraft.part_count, 0)} />
            <StatCard label="油量" value={pctText((aircraft.fuel || {}).percent)} />
            <StatCard label="发动机" value={`${num((aircraft.engines || []).length, 0)} 台`} />
            <StatCard label="完整度" value={pctText((aircraft.damage || {}).integrity_percent)} />
          </Grid>
          <KeyValue
            items={[
              {
                key: "vne",
                label: "最大允许速度 Vne",
                value: isNum((aircraft.limits || {}).vne_mps) ? speed((aircraft.limits || {}).vne_mps) : "机体未提供",
              },
              {
                key: "stall_speed",
                label: "失速速度",
                value: isNum((aircraft.limits || {}).stall_speed_mps)
                  ? speed((aircraft.limits || {}).stall_speed_mps)
                  : "机体未提供",
              },
              {
                key: "g_limit",
                label: "过载上限",
                value: isNum((aircraft.limits || {}).max_g) ? num((aircraft.limits || {}).max_g, 2, " G") : "机体未提供",
              },
              {
                key: "flap_limit",
                label: "襟翼限速",
                value: isNum((aircraft.limits || {}).flap_limit_mps)
                  ? speed((aircraft.limits || {}).flap_limit_mps)
                  : "机体未提供",
              },
            ]}
          />
        </Stack>
      </Card>

      <Card title="环境">
        <KeyValue
          items={[
            { key: "weather", label: "天气", value: asText(environment.weather) || "—" },
            { key: "tod", label: "时间", value: asText(environment.time_of_day) || "—" },
            { key: "temp", label: "气温", value: num(environment.temperature_c, 1, " ℃") },
            { key: "qnh", label: "气压", value: num(environment.pressure_hpa, 1, " hPa") },
            {
              key: "wind",
              label: "风",
              value: isNum((environment.wind || {}).speed_mps)
                ? `${speed((environment.wind || {}).speed_mps)} · 来自 ${headingText((environment.wind || {}).direction_deg)}`
                : "未知",
            },
            { key: "vis", label: "能见度", value: altText(environment.visibility_m) },
          ]}
        />
      </Card>
    </Stack>
  )

  const checklistTab = (
    <Stack>
      <Card title={`检查单 · ${asText(checklist.phase_label) || "未判定阶段"}`}>
        <Stack>
          <Inline align="center" gap={8} wrap>
            <StatusBadge tone="success" label={`已完成 ${num(checklist.done_count, 0)}`} />
            <StatusBadge tone="warning" label={`待办 ${num(checklist.pending_count, 0)}`} />
            <StatusBadge tone="default" label={`读不到 ${num(checklist.unknown_count, 0)}`} />
          </Inline>
          {items.length === 0 ? (
            <Alert tone="info">当前阶段没有检查项目（可能还没进入飞行世界）。</Alert>
          ) : (
            <DataTable
              data={items.map((item, index) => ({
                key: `${asText(item.id)}-${index}`,
                status: statusText(item.status).label,
                label: asText(item.label),
                detail: asText(item.detail),
              }))}
              rowKey="key"
              columns={[
                { key: "status", label: "状态" },
                { key: "label", label: "项目" },
                { key: "detail", label: "说明" },
              ]}
            />
          )}
          {asText(snapshot.checklist_summary) ? <Text>{asText(snapshot.checklist_summary)}</Text> : null}
          <Tip>
            检查单只是提醒，本插件不会替你操作 —— 副驾驶负责看和说，动手的是你。
          </Tip>
        </Stack>
      </Card>
    </Stack>
  )

  const navigationTab = (
    <Stack>
      <Card title="导航">
        <Stack>
          <Text>{asText(snapshot.navigation_summary) || "（暂无导航数据）"}</Text>
          <Grid cols={3} gap={10}>
            <StatCard label="当前航向" value={headingText(navigation.heading)} />
            <StatCard
              label="目标"
              value={asText((navigation.target || {}).name) || "未设定"}
            />
            <StatCard label="到目标距离" value={altText((navigation.target || {}).distance)} />
            <StatCard label="目标方位" value={headingText((navigation.target || {}).bearing)} />
            <StatCard
              label="最近机场"
              value={asText((navigation.nearest_airport || {}).name) || "—"}
            />
            <StatCard label="机场距离" value={altText((navigation.nearest_airport || {}).distance)} />
          </Grid>
          <Divider />
          <KeyValue
            items={[
              {
                key: "wind",
                label: "风",
                value: isNum((navigation.wind || {}).speed_mps)
                  ? `${speed((navigation.wind || {}).speed_mps)} · 来自 ${headingText((navigation.wind || {}).direction_deg)}`
                  : "未知",
              },
              { key: "track", label: "航迹角", value: headingText(navigation.track) },
              {
                key: "pos",
                label: "位置",
                value: isNum((navigation.position || {}).x)
                  ? `${num((navigation.position || {}).x, 0)}, ${num((navigation.position || {}).y, 0)}, ${num(
                      (navigation.position || {}).z,
                      0,
                    )}`
                  : "未知",
              },
            ]}
          />
        </Stack>
      </Card>
    </Stack>
  )

  const settingsTab = (
    <Stack>
      <Card title="桥接">
        <Stack>
          <Field label="桥接地址" help="SP2-Bridge 模组监听的地址；改端口要两边一起改">
            <Input
              value={asText(fieldValue("bridge_url", effective.bridge_url))}
              onChange={(value: any) => setField("bridge_url", value)}
            />
          </Field>
          <Grid cols={2} gap={10}>
            <Field label="请求超时（秒）" help="范围 3-60">
              <NumberInput
                value={numberOr(fieldValue("timeout_seconds", 12), 12)}
                min={3}
                max={60}
                onChange={(value: any) => setField("timeout_seconds", numberOr(value, 12))}
              />
            </Field>
            <Field label="面板刷新间隔（秒）" help="范围 1-30，只影响本面板">
              <NumberInput
                value={numberOr(fieldValue("poll_interval_seconds", 3), 3)}
                min={1}
                max={30}
                onChange={(value: any) => setField("poll_interval_seconds", numberOr(value, 3))}
              />
            </Field>
          </Grid>
        </Stack>
      </Card>

      <Card title="自动播报">
        <Stack>
          <Tip>
            游戏在跑的时候，插件会在后台读仪表，把最新状态放进角色的上下文
            ——不打断对话、不弹提示，但你一开口，它就已经知道飞机现在什么高度、
            什么速度、有没有告警。同一时刻只保留最新一条，不会把上下文撑爆；
            游戏没开时完全不播报。
          </Tip>
          <Switch
            checked={fieldValue("auto_brief_enabled", true) !== false}
            label="开启自动播报（关掉就退回问答式：你问了才读）"
            onChange={(value: boolean) => setField("auto_brief_enabled", value)}
          />
          <Grid cols={2} gap={10}>
            <Field label="播报间隔（秒）" help="范围 5-120，突变最迟一个间隔内播报">
              <NumberInput
                value={numberOr(fieldValue("auto_brief_interval_seconds", 20), 20)}
                min={5}
                max={120}
                onChange={(value: any) =>
                  setField("auto_brief_interval_seconds", numberOr(value, 20))
                }
              />
            </Field>
          </Grid>
        </Stack>
      </Card>

      <Card title="告警阈值">
        <Stack>
          <Tip>
            这几项只影响「什么时候提醒你」，不会影响游戏本身，也不会给游戏发任何指令。
          </Tip>
          <Grid cols={2} gap={10}>
            <Field label="近地告警高度（米）" help="离地低于这个值且仍在下降就报警">
              <NumberInput
                value={numberOr(fieldValue("terrain_warning_agl_m", 300), 300)}
                min={30}
                max={1000}
                onChange={(value: any) => setField("terrain_warning_agl_m", numberOr(value, 300))}
              />
            </Field>
            <Field label="下降率告警（m/s）" help="近地时下降率超过这个值就报警">
              <NumberInput
                value={numberOr(fieldValue("sink_rate_warning_mps", 6), 6)}
                min={1}
                max={30}
                onChange={(value: any) => setField("sink_rate_warning_mps", numberOr(value, 6))}
              />
            </Field>
            <Field label="坡度告警（度）" help="滚转角绝对值超过这个值就报警">
              <NumberInput
                value={numberOr(fieldValue("bank_warning_deg", 45), 45)}
                min={15}
                max={90}
                onChange={(value: any) => setField("bank_warning_deg", numberOr(value, 45))}
              />
            </Field>
            <Field label="低油量告警（%）" help="剩余油量低于这个百分比就报警">
              <NumberInput
                value={numberOr(fieldValue("low_fuel_warning_percent", 20), 20)}
                min={1}
                max={50}
                onChange={(value: any) => setField("low_fuel_warning_percent", numberOr(value, 20))}
              />
            </Field>
            <Field label="迎角告警（度）" help="迎角超过这个值提醒接近失速">
              <NumberInput
                value={numberOr(fieldValue("aoa_warning_deg", 12), 12)}
                min={5}
                max={30}
                onChange={(value: any) => setField("aoa_warning_deg", numberOr(value, 12))}
              />
            </Field>
            <Field label="超速预警比例" help="空速达到 Vne 的这个比例就提前提醒">
              <NumberInput
                value={numberOr(fieldValue("overspeed_warning_ratio", 0.9), 0.9)}
                min={0.5}
                max={1}
                step={0.05}
                onChange={(value: any) => setField("overspeed_warning_ratio", numberOr(value, 0.9))}
              />
            </Field>
          </Grid>
          <Toolbar>
            <ToolbarGroup>
              <Button tone="success" disabled={busyAction !== ""} onClick={() => void saveSettings()}>
                保存设置
              </Button>
              <Button
                disabled={busyAction !== ""}
                onClick={() => {
                  setDraft({})
                  toast.info("已恢复为当前生效值。")
                }}
              >
                恢复生效值
              </Button>
            </ToolbarGroup>
          </Toolbar>
        </Stack>
      </Card>

      <Card title="关于">
        <Stack>
          <Text>
            本插件是**只读副驾驶**：只读取游戏数据、只给出提醒与建议，不会向游戏发送任何控制指令。
          </Text>
          <Text>
            数据来自配套的 SP2-Bridge 模组。模组侧的接口协议见插件目录下的 docs/bridge-protocol.md，
            本地调试可以用 tools/mock_bridge.py 起一个模拟服务。
          </Text>
        </Stack>
      </Card>
    </Stack>
  )

  const body = (() => {
    switch (tab) {
      case "instruments":
        return instrumentsTab
      case "checklist":
        return checklistTab
      case "navigation":
        return navigationTab
      case "settings":
        return settingsTab
      default:
        return overviewTab
    }
  })()

  return (
    <Page
      title="副驾驶仪表盘"
      subtitle="《简单飞机2》只读副驾驶：读仪表 · 报检查单 · 扫告警 · 做导航"
    >
      <Stack>
        <Toolbar>
          <ToolbarGroup>
            {TABS.map((item) => (
              <Button
                key={item.id}
                tone={tab === item.id ? "primary" : "default"}
                onClick={() => setTab(item.id)}
              >
                {item.label}
              </Button>
            ))}
          </ToolbarGroup>
        </Toolbar>
        {body}
      </Stack>
    </Page>
  )
}
