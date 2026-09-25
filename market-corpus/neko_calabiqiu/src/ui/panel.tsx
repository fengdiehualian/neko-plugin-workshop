import {
  Page,
  RefreshButton,
  Alert,
  Toolbar,
  ToolbarGroup,
  Button,
  ButtonGroup,
  Card,
  Stack,
  Text,
  Steps,
  Step,
  Input,
  useEffect,
  useState,
} from "@neko/plugin-ui"
import type { PluginSurfaceProps } from "@neko/plugin-ui"

type Dashboard = {
  plugin_version?: string
  data_layer_version?: string
  runtime_sync?: {
    status?: "ok" | "waiting" | "mismatch"
    message?: string
    plugin_version?: string
    data_layer_version?: string
    expected_model?: string
    actual_model?: string
  }
  enabled?: boolean
  dry_run?: boolean
  connected?: boolean
  scenario?: string
  game_running?: boolean
  game_region?: string
  process_name?: string | null
  session_phase?: string
  degraded?: boolean
  degrade_reason?: string | null
  awareness?: { threat_level?: string; target_count?: number; pressure?: number }
  classifier?: {
    backend?: string
    state?: string
    last_source?: string
    template_dir?: string
    last_result?: { state?: string; score?: number; backend?: string } | null
    ocr?: {
      available?: boolean
      lang?: string
      last_error?: string | null
      last_source?: string
      last_logged_date?: string | null
      last_match?: { state?: string; score?: number } | null
    } | null
  } | null
  infer_mode?: string
  model?: {
    device?: string
    device_reason?: string
    name?: string
    weights_id?: string
    weights_fallback_from?: string
  }
  data_layer?: { mode?: string; last_error?: string | null; assistant_directory?: string; started_by_plugin?: boolean; pid?: number | null; follow_game?: boolean; game_running?: boolean; game_paused?: boolean; game_watch_error?: string | null }
  safety?: { status?: string }
  context_active?: boolean
  context_restore_pending?: boolean
  observe?: {
    last_decision?: {
      event_id?: string
      outcome?: string
      reason?: string
    } | null
    last_output_status?: {
      stage?: string
      outcome?: string
      reason?: string
      event_id?: string
      plugin_recommended_reply?: string
      delivery_ttl_seconds?: number
      replace_pending?: boolean
      interrupt_pending?: boolean
    } | null
  }
}

// ── helpers ──

const label = (v: unknown, fallback = "—") =>
  v === undefined || v === null || v === "" ? fallback : String(v)

// Public download locations for the companion assistant.
const ASSISTANT_DOWNLOADS = [
  { name: "夸克网盘（提取码：qpWS）", url: "https://pan.quark.cn/s/73e1c8343e61?pwd=qpWS" },
  { name: "Google Drive", url: "https://drive.google.com/drive/folders/1TzsU6Mjky2p-94hX-mtI-fvPpAPSWqdP?usp=sharing" },
  { name: "百度网盘（提取码：64db）", url: "https://pan.baidu.com/s/1aZcZFHyPGlwgPBqBixSBJA?pwd=64db" },
]

function openDownload(url: string) {
  if (!url.startsWith("https://")) return
  window.parent.postMessage(
    { type: "neko-hosted-surface-open-external", payload: { url } },
    "*",
  )
}

// ── colour palette ──

const C = {
  bg: "#f7f8fc",
  cardBg: "#ffffff",
  cardBorder: "#e8ecf1",
  accent: "#7c5cfc",
  accentSoft: "#f3f0ff",
  success: "#22c55e",
  successSoft: "#f0fdf4",
  warning: "#f59e0b",
  warningSoft: "#fffbeb",
  danger: "#ef4444",
  dangerSoft: "#fef2f2",
  info: "#3b82f6",
  infoSoft: "#eff6ff",
  text: "#1e293b",
  textSecondary: "#64748b",
  textMuted: "#94a3b8",
  shadow: "0 1px 3px rgba(0,0,0,0.04), 0 1px 2px rgba(0,0,0,0.06)",
}

// ── shared styles ──

const card = {
  flex: "1 1 280px",
  minWidth: 0,
  background: C.cardBg,
  borderRadius: "14px",
  padding: "20px 22px",
  boxShadow: C.shadow,
  border: `1px solid ${C.cardBorder}`,
}

const cardHeading = {
  display: "flex",
  alignItems: "center",
  gap: "8px",
  margin: "0 0 14px 0",
  fontSize: "14px",
  fontWeight: 700,
  color: C.text,
  letterSpacing: "0.01em",
}

const iconBox = (bg: string) => ({
  width: "32px",
  height: "32px",
  borderRadius: "8px",
  display: "inline-flex",
  alignItems: "center",
  justifyContent: "center",
  fontSize: "15px",
  flexShrink: 0,
  background: bg,
})

const row = {
  display: "flex",
  justifyContent: "space-between",
  alignItems: "center",
  padding: "5px 0",
  fontSize: "13px",
  lineHeight: 1.6,
  borderBottom: "1px solid #f8fafc",
}

const keyStyle = {
  fontWeight: 500,
  color: C.textSecondary,
  fontSize: "12.5px",
}

const valStyle = {
  minWidth: 0,
  color: C.text,
  fontWeight: 500,
  textAlign: "right" as const,
  maxWidth: "55%",
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap" as const,
}

const dot = (on: boolean | undefined | null) => ({
  display: "inline-block",
  width: "7px",
  height: "7px",
  borderRadius: "50%",
  marginRight: "6px",
  background: on ? C.success : C.textMuted,
  boxShadow: on ? `0 0 6px ${C.success}80` : "none",
})

const pill = (
  tone: "success" | "warning" | "danger" | "info" | "neutral"
) => {
  const map: Record<string, { bg: string; fg: string }> = {
    success: { bg: C.successSoft, fg: "#15803d" },
    warning: { bg: C.warningSoft, fg: "#b45309" },
    danger: { bg: C.dangerSoft, fg: "#b91c1c" },
    info: { bg: C.infoSoft, fg: "#1d4ed8" },
    neutral: { bg: "#f1f5f9", fg: C.textSecondary },
  }
  const m = map[tone] ?? map.neutral
  return {
    display: "inline-block",
    padding: "1px 10px",
    borderRadius: "999px",
    fontSize: "12px",
    fontWeight: 600,
    background: m.bg,
    color: m.fg,
    lineHeight: "22px",
  }
}

// ── gauge bar ──

function GaugeBar({ value, max = 10 }: { value?: number; max?: number }) {
  const pct = Math.min(100, Math.max(0, ((value ?? 0) / max) * 100))
  const hue = 120 - (pct / 100) * 120
  const color = `hsl(${hue}, 70%, 48%)`
  return (
    <div style={{ marginTop: "6px" }}>
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          fontSize: "11px",
          color: C.textMuted,
          marginBottom: "3px",
        }}
      >
        <span>压迫值</span>
        <span>
          {value ?? 0} / {max}
        </span>
      </div>
      <div
        style={{
          height: "6px",
          borderRadius: "3px",
          background: "#e5e7eb",
          overflow: "hidden",
        }}
      >
        <div
          style={{
            width: `${pct}%`,
            height: "100%",
            borderRadius: "3px",
            background: color,
            transition: "width 0.5s ease, background 0.5s ease",
          }}
        />
      </div>
    </div>
  )
}

// ── panel ──

export default function Panel(props: PluginSurfaceProps<Dashboard>) {
  const d = props.state || {}
  const [actionError, setActionError] = useState("")
  const [assistantDirectory, setAssistantDirectory] = useState<string | null>(null)
  const [processBusy, setProcessBusy] = useState(false)
  const directory = assistantDirectory ?? d.data_layer?.assistant_directory ?? ""
  const owned = !!d.data_layer?.started_by_plugin
  const processMode = d.data_layer?.mode ?? "unknown"
  const gameRunning = d.data_layer?.game_running ?? d.game_running ?? false
  const speechPaused = d.safety?.status === "paused" || d.safety?.status === "auto_stopped"
  const waitingAutomatically = !!directory.trim() && processMode === "waiting_game" && !d.data_layer?.game_paused
  const processLabels: Record<string, string> = {
    unknown: "尚未启动", missing: "尚未启动", starting: "后台加载中", managed: "运行中",
    external: "从外部启动", stopped: "已停止", failed: "助手操作失败",
    incompatible: "助手不匹配", unverified_external: "正在检查连接", unverified_scene: "正在检查连接",
    waiting_game: "等待游戏主程序",
  }
  async function controlAssistant(operation: string) {
    if (processBusy) return
    setProcessBusy(true)
    try {
      await runAction("assistant_control", { operation, directory })
    } finally {
      setProcessBusy(false)
    }
  }

  useEffect(() => {
    if (processBusy) return
    const timer = setInterval(() => {
      void props.api.refresh().catch((error: unknown) => {
        setActionError(error instanceof Error ? error.message : String(error))
      })
    }, 1500)
    return () => clearInterval(timer)
  }, [props.api, processBusy])

  async function runAction(id: string, input: Record<string, unknown> = {}) {
    try {
      setActionError("")
      await props.api.call(id, input)
      await props.api.refresh()
    } catch (error) {
      setActionError(error instanceof Error ? error.message : String(error))
    }
  }

  const managerIncompatible = d.data_layer?.mode === "incompatible"
  const syncMismatch = managerIncompatible || d.runtime_sync?.status === "mismatch"

  return (
    <Page
      title="卡拉彼丘陪伴"
      subtitle={`v${label(d.plugin_version, "未知")} · 开游戏，一起陪伴；退出游戏，助手自动休息`}
    >
      {/* ── toolbar ── */}
      <Toolbar>
        <ToolbarGroup>
          <span style={dot(d.connected)} />
          <span
            style={{
              fontSize: "13px",
              fontWeight: 600,
              color: d.connected ? C.success : C.textMuted,
            }}
          >
            {d.connected ? "已连接" : !directory.trim() ? "等待配置" : processMode === "starting" ? "助手加载中" : waitingAutomatically ? "等待游戏" : "助手未运行"}
          </span>
          <span
            style={{
              width: "1px",
              height: "16px",
              background: C.cardBorder,
              margin: "0 8px",
            }}
          />
          <span style={dot(gameRunning)} />
          <span
            style={{
              fontSize: "13px",
              fontWeight: 600,
              color: gameRunning ? C.success : C.textMuted,
            }}
          >
            {gameRunning ? "游戏运行中" : "游戏未运行"}
          </span>
          <span
            style={{
              width: "1px",
              height: "16px",
              background: C.cardBorder,
              margin: "0 8px",
            }}
          />
          <span style={pill(speechPaused ? "warning" : d.dry_run ? "neutral" : "success")}>
            {speechPaused ? "播报已暂停" : d.dry_run ? "仅观察，不播报" : "语音提醒已开启"}
          </span>
          <span
            style={{
              width: "1px",
              height: "16px",
              background: C.cardBorder,
              margin: "0 8px",
            }}
          />
          <span
            style={pill(
              syncMismatch
                ? "danger"
                : d.runtime_sync?.status === "ok"
                ? "success"
                : "neutral"
            )}
          >
            {syncMismatch
              ? "版本不一致"
              : d.runtime_sync?.status === "ok"
              ? "版本已同步"
              : "助手启动后核验版本"}
          </span>
        </ToolbarGroup>
        <ToolbarGroup>
          <RefreshButton label="刷新状态" />
        </ToolbarGroup>
      </Toolbar>

      {actionError ? <Alert tone="danger">{actionError}</Alert> : null}
      {managerIncompatible ? (
        <Alert tone="danger">⚠️ 陪伴助手版本或模型不匹配，暂时无法连接。请关闭旧助手，再启动与插件配套的版本。</Alert>
      ) : null}
      {d.runtime_sync?.status === "mismatch" ? (
        <Alert tone="danger">⚠️ {label(d.runtime_sync?.message)}</Alert>
      ) : null}

      <Card title="陪伴助手">
        <Stack>
          <Text>状态：{!directory.trim() ? "请先配置助手目录" : processLabels[processMode] ?? processMode}</Text>
          <Text>{d.data_layer?.follow_game ? "游戏主程序启动后，助手会在后台加载；退出游戏后自动关闭。无需手动运行后端窗口。" : "当前使用手动模式，可在这里启动和停止助手。需要随游戏自动启停时，请在插件设置中开启对应选项。"}</Text>
          <ButtonGroup>
            <Button tone="primary" disabled={processBusy || !directory.trim() || owned || processMode === "external" || waitingAutomatically}
              onClick={() => controlAssistant("start")}>{d.data_layer?.game_paused ? "恢复自动启动" : waitingAutomatically ? "自动启动已开启" : "启动助手"}</Button>
            <Button tone="danger" disabled={processBusy || (!owned && (!d.data_layer?.follow_game || d.data_layer?.game_paused))}
              onClick={() => controlAssistant("stop")}>{owned ? "停止助手" : "暂停自动启动"}</Button>
            <Button disabled={processBusy || !owned || processMode === "starting"}
              onClick={() => controlAssistant("restart")}>重启助手</Button>
          </ButtonGroup>
          {processBusy ? <Text>正在提交操作，请稍候……</Text> : null}
          {processMode === "starting" ? <Text>启动请求已完成，模型正在后台加载。就绪后自动连接，你可以继续使用 N.E.K.O；需要取消时点击「停止助手」。</Text> : null}
          {waitingAutomatically ? <Text>这是正常待机状态。直接打开卡拉彼丘即可，只打开游戏启动器不会加载助手。</Text> : null}
          {d.data_layer?.game_paused ? <Text>已手动停止，本次游戏期间不会自动拉起。重新打开游戏或点击恢复自动启动后继续。</Text> : null}
          {d.data_layer?.game_watch_error ? <Alert tone="warning">{d.data_layer.game_watch_error}</Alert> : null}
          {d.data_layer?.last_error ? <Alert tone="warning">{d.data_layer.last_error}</Alert> : null}
          {processMode === "external" ? <Alert tone="warning">当前助手由外部启动。首次切换请先关闭旧助手，再使用面板启动；之后都可以在这里停止和重启。</Alert> : null}
          <details open={!directory.trim()}>
            <summary style={{ cursor: "pointer", color: C.textMuted }}>助手位置与连接设置</summary>
            <Stack>
              <label>助手解压目录</label>
              <Input value={directory} onChange={setAssistantDirectory} disabled={processBusy || owned}
                placeholder="选择完整解压后的 neko_calabiqiu_assistant 文件夹" />
              <Text>首次接入脚本会自动填写，正常使用无需修改。移动助手目录后，请先停止助手并退出 N.E.K.O，再从新目录重新运行接入脚本。</Text>
            </Stack>
          </details>
        </Stack>
      </Card>
      <Card title="语音提醒">
        <Stack>
          <Text>{d.safety?.status === "auto_stopped" ? "连续投递失败，播报已自动暂停。请检查 N.E.K.O 状态后再恢复播报。" : speechPaused ? "播报已暂停，助手仍可继续运行。" : d.dry_run ? "仅观察游戏状态，不向猫猫发送播报。" : "游戏中的提醒会交给猫猫播报。"}</Text>
          <ButtonGroup>
            <Button onClick={() => void runAction("set_dry_run", { value: !d.dry_run })}>{d.dry_run ? "开启语音提醒" : "切换为仅观察"}</Button>
            <Button tone="danger" disabled={speechPaused} onClick={() => void runAction("pause")}>暂停播报</Button>
            <Button disabled={!speechPaused} onClick={() => void runAction("resume")}>恢复播报</Button>
            <Button disabled={!!d.dry_run || speechPaused} onClick={() => void runAction("test_say", {})}>试听提醒</Button>
          </ButtonGroup>
          <Text>暂停播报不会关闭助手；要释放后台资源，请使用上方的「停止助手」。</Text>
          {d.dry_run ? <Text>需要试听时，先开启语音提醒。</Text> : null}
        </Stack>
      </Card>
      <details open={!directory.trim()} style={{ marginTop: "16px" }}>
      <summary style={{ cursor: "pointer", fontWeight: 600, padding: "12px 0" }}>安装、下载与更新 · ZIP 密码 nekonekoneko</summary>
      <Card title="下载陪伴助手">
        <Stack>
          <Text>完整包包含助手运行环境、加密模型、轻量面板和一次性连接脚本，无需另装 Python。已有接入的用户无需重复安装。</Text>
          <Text>夸克、Google Drive 和百度网盘均提供 0.1.8 完整助手包，任选一个即可。文件名：neko_calabiqiu-assistant-0.1.8.zip，约 1.19 GB。</Text>
          <Text>使用支持密码 ZIP 的解压工具完整解压，不要直接在压缩包里运行脚本。</Text>
          <Alert tone="info">
            <strong>ZIP 解压密码：</strong>
            <code style={{ fontSize: "16px", fontWeight: 700, userSelect: "all" }}>nekonekoneko</code>
            <div>此密码用于解压文件，与网盘提取码不同。</div>
          </Alert>
          <ButtonGroup>
            {ASSISTANT_DOWNLOADS.map((download) => (
              <Button
                key={download.name}
                disabled={!download.url}
                onClick={() => openDownload(download.url)}
              >
                {download.name}{download.url ? "" : " · 尚未发布"}
              </Button>
            ))}
          </ButtonGroup>
          {!ASSISTANT_DOWNLOADS.some((download) => download.url) ? (
            <Text>下载地址尚未发布。已有完整包可以直接按下面的步骤启动。</Text>
          ) : null}
        </Stack>
      </Card>
      <Card title="开始使用">
        <Steps>
          <Step index="1" title="下载配套完整包">
            <Text>用上方密码完整解压到固定目录。模型和运行环境都已包含，无需单独导入插件包。</Text>
          </Step>
          <Step index="2" title="首次运行一次 CMD 接入">
            <Text>退出 N.E.K.O，在解压后的 neko_calabiqiu_assistant 文件夹中双击「首次使用_连接到NEKO.cmd」，等待提示「连接完成」。脚本会自动设置助手位置，无需手动填写路径。</Text>
          </Step>
          <Step index="3" title="重新打开 N.E.K.O，通过面板控制">
            <Text>重新打开 N.E.K.O，在插件列表启用「卡拉彼丘陪伴」，以后通过面板启动、停止或重启助手，无需重复运行 CMD。打开游戏后助手自动准备，就绪后显示「已连接」；退出游戏后助手自动关闭。</Text>
          </Step>
        </Steps>
      </Card>
      <Alert tone="info">关闭面板页面不会停止插件。移动助手目录后，先停止助手并退出 N.E.K.O，再从新目录运行一次接入 CMD；修改助手设置后，在游戏运行时点击「重启助手」。</Alert>
      </details>

      {/* ── card grid ── */}
      <details style={{ marginTop: "16px" }}>
      <summary style={{ cursor: "pointer", fontWeight: 600, padding: "12px 0" }}>运行详情与排查</summary>
      <div
        style={{
          minWidth: 0,
          display: "flex",
          flexWrap: "wrap",
          gap: "16px",
          marginTop: "16px",
        }}
      >
        {/* ── 连接状态 ── */}
        <div style={card}>
          <h4 style={cardHeading}>
            <span style={iconBox(C.accentSoft)}>🔌</span>连接状态
          </h4>
          <div style={row}>
            <span style={keyStyle}>插件面板版本</span>
            <span style={valStyle}>v{label(d.plugin_version)}</span>
          </div>
          <div style={row}>
            <span style={keyStyle}>陪伴助手版本</span>
            <span style={valStyle}>{d.data_layer_version ? `v${d.data_layer_version}` : "未上报"}</span>
          </div>
          <div style={row}>
            <span style={keyStyle}>陪伴助手模式</span>
            <span style={valStyle}>{processLabels[processMode] ?? processMode}</span>
          </div>
          <div style={row}>
            <span style={keyStyle}>推理模式</span>
            <span style={valStyle}>{label(d.infer_mode)}</span>
          </div>
          <div style={row}>
            <span style={keyStyle}>推理设备</span>
            <span style={valStyle}>{label(d.model?.device)}</span>
          </div>
          <div style={row}>
            <span style={keyStyle}>当前模型</span>
            <span
              title={label(d.model?.weights_id)}
              style={{
                ...valStyle,
                maxWidth: "50%",
                fontSize: "11.5px",
                fontFamily: "monospace",
              }}
            >
              {label(d.model?.weights_id)}
            </span>
          </div>
          <div style={row}>
            <span style={keyStyle}>期望模型</span>
            <span style={valStyle}>{label(d.runtime_sync?.expected_model)}</span>
          </div>
          <div style={row}>
            <span style={keyStyle}>实际模型</span>
            <span style={valStyle}>{label(d.runtime_sync?.actual_model)}</span>
          </div>
          <div style={row}>
            <span style={keyStyle}>仅观察、不播报</span>
            <span style={d.dry_run ? pill("warning") : pill("danger")}>
              {d.dry_run ? "开启" : "关闭"}
            </span>
          </div>
          {d.data_layer?.last_error && (
            <div
              style={{
                marginTop: "10px",
                padding: "8px 12px",
                borderRadius: "8px",
                background: C.dangerSoft,
                color: "#b91c1c",
                fontSize: "12.5px",
                lineHeight: 1.5,
              }}
            >
              ⚠️ {d.data_layer.last_error}
            </div>
          )}
          {d.model?.weights_fallback_from && (
            <div
              style={{
                marginTop: "10px",
                padding: "8px 12px",
                borderRadius: "8px",
                background: C.warningSoft,
                color: "#b45309",
                fontSize: "12.5px",
                lineHeight: 1.5,
              }}
            >
              ⚠️ 新模型无法加载，已自动使用旧模型
            </div>
          )}
        </div>

        {/* ── 会话 ── */}
        <div style={card}>
          <h4 style={cardHeading}>
            <span style={iconBox(C.infoSoft)}>🎮</span>会话
          </h4>
          <div style={row}>
            <span style={keyStyle}>游戏状态</span>
            <span style={gameRunning ? pill("success") : pill("neutral")}>
              {gameRunning ? "运行中" : "未运行"}
            </span>
          </div>
          <div style={row}>
            <span style={keyStyle}>游戏区域</span>
            <span style={valStyle}>{label(d.game_region)}</span>
          </div>
          <div style={row}>
            <span style={keyStyle}>进程名</span>
            <span
              style={{
                ...valStyle,
                fontFamily: "monospace",
                fontSize: "12px",
              }}
            >
              {label(d.process_name)}
            </span>
          </div>
          <div style={row}>
            <span style={keyStyle}>场景</span>
            <span style={valStyle}>{label(d.scenario)}</span>
          </div>
          <div style={row}>
            <span style={keyStyle}>阶段</span>
            <span style={valStyle}>{label(d.session_phase)}</span>
          </div>
          <div style={row}>
            <span style={keyStyle}>语境注入</span>
            <span
              style={
                d.context_restore_pending
                  ? pill("warning")
                  : d.context_active
                    ? pill("success")
                    : pill("neutral")
              }
            >
              {d.context_restore_pending
                ? "恢复中"
                : d.context_active
                  ? "已注入"
                  : "未注入"}
            </span>
          </div>
          {d.degraded && (
            <div
              style={{
                marginTop: "10px",
                padding: "8px 12px",
                borderRadius: "8px",
                background: C.warningSoft,
                color: "#b45309",
                fontSize: "12.5px",
                lineHeight: 1.5,
              }}
            >
              ⚠️ 降级：{label(d.degrade_reason)}
            </div>
          )}
        </div>

        {/* ── 态势感知 ── */}
        <div style={card}>
          <h4 style={cardHeading}>
            <span style={iconBox(C.warningSoft)}>📊</span>态势感知
          </h4>
          <div style={row}>
            <span style={keyStyle}>目标数</span>
            <span
              style={{
                ...valStyle,
                fontSize: "20px",
                fontWeight: 700,
                color:
                  (d.awareness?.target_count ?? 0) > 0 ? C.danger : C.text,
              }}
            >
              {label(d.awareness?.target_count, "0")}
            </span>
          </div>
          <div style={row}>
            <span style={keyStyle}>威胁等级</span>
            <span style={valStyle}>
              {label(d.awareness?.threat_level, "无")}
            </span>
          </div>
          <GaugeBar value={d.awareness?.pressure} max={10} />
          <div
            style={{
              fontSize: "11px",
              color: C.textMuted,
              marginTop: "10px",
              lineHeight: 1.5,
            }}
          >
            粗粒度屏幕识别结果，经跟踪平滑
          </div>
        </div>

        {/* ── 识别 ── */}
        <div style={card}>
          <h4 style={cardHeading}>
            <span style={iconBox(C.accentSoft)}>🧠</span>识别
          </h4>
          <div style={row}>
            <span style={keyStyle}>分类后端</span>
            <span style={valStyle}>{label(d.classifier?.backend)}</span>
          </div>
          <div style={row}>
            <span style={keyStyle}>当前状态</span>
            <span style={valStyle}>{label(d.classifier?.state)}</span>
          </div>
          <div style={row}>
            <span style={keyStyle}>最近判定</span>
            <span style={valStyle}>
              {label(d.classifier?.last_result?.state)}
              {d.classifier?.last_result?.score !== undefined
                ? ` (${d.classifier.last_result.score})`
                : ""}
            </span>
          </div>
          <div style={row}>
            <span style={keyStyle}>OCR 可用</span>
            <span
              style={
                d.classifier?.ocr?.available ? pill("success") : pill("neutral")
              }
            >
              {d.classifier?.ocr?.available ? "可用" : "不可用"}
            </span>
          </div>
          <div style={row}>
            <span style={keyStyle}>OCR 命中</span>
            <span style={valStyle}>
              {label(d.classifier?.ocr?.last_match?.state)}
              {d.classifier?.ocr?.last_match?.score !== undefined
                ? ` (${d.classifier.ocr.last_match.score})`
                : ""}
            </span>
          </div>
          {d.classifier?.ocr?.last_error && (
            <div
              style={{
                marginTop: "8px",
                padding: "6px 10px",
                borderRadius: "6px",
                background: C.dangerSoft,
                color: "#b91c1c",
                fontSize: "12px",
              }}
            >
              ⚠️ {d.classifier.ocr.last_error}
            </div>
          )}
        </div>

        {/* ── 输出链路 ── */}
        <div style={card}>
          <h4 style={cardHeading}>
            <span style={iconBox(C.infoSoft)}>📤</span>输出链路
          </h4>
          <div style={row}>
            <span style={keyStyle}>最近决策</span>
            <span style={valStyle}>
              {label(d.observe?.last_decision?.event_id)} ·{" "}
              {label(d.observe?.last_decision?.outcome)}
            </span>
          </div>
          <div style={row}>
            <span style={keyStyle}>最近投递</span>
            <span style={valStyle}>
              {label(d.observe?.last_output_status?.event_id)} ·{" "}
              {label(d.observe?.last_output_status?.stage)}
            </span>
          </div>
          {d.observe?.last_output_status?.plugin_recommended_reply && (
            <div
              style={{
                marginTop: "10px",
                padding: "10px 14px",
                borderRadius: "10px",
                background: "linear-gradient(135deg, #fef9e7, #fef3c7)",
                border: "1px solid #fde68a",
                fontSize: "13px",
                color: "#92400e",
                lineHeight: 1.5,
              }}
            >
              <span
                style={{
                  fontWeight: 700,
                  fontSize: "11px",
                  textTransform: "uppercase",
                  letterSpacing: "0.05em",
                  display: "block",
                  marginBottom: "2px",
                }}
              >
                💬 推荐回复
              </span>
              {d.observe.last_output_status.plugin_recommended_reply}
            </div>
          )}
          <div
            style={{
              fontSize: "11px",
              color: C.textMuted,
              marginTop: "10px",
              lineHeight: 1.5,
            }}
          >
            旧警报会过期丢弃，同类重复事件会折叠
          </div>
        </div>

      </div>
      </details>
    </Page>
  )
}
