import {
  ActionButton,
  Alert,
  Button,
  Card,
  KeyValue,
  Page,
  Stack,
  StatusBadge,
  Text,
  Tip,
  useEffect,
  useRef,
  useState,
} from "@neko/plugin-ui"
import type { HostedAction, PluginSurfaceProps } from "@neko/plugin-ui"

// 状态轮询间隔；面板上下文（二维码/电脑锁）由 Python @ui.context 提供，
// api.call 的结果和 game_agent 一样是 {plugin_id, action_id, result} 信封。
const STATUS_REFRESH_INTERVAL_MS = 5000

type StatusState = {
  loading: boolean
  online: boolean | null // null = 还没查过
  summary: string
  error: string
}

function unwrapActionResult(envelope: any): Record<string, any> {
  if (envelope && typeof envelope === "object") {
    if (envelope.result && typeof envelope.result === "object") return envelope.result
    return envelope
  }
  return {}
}

// 面板只展示这几个入口按钮（同一个 group 的 @ui.action 元数据自带确认弹窗）。
const ACTION_IDS = [
  "lock_phone_now",
  "lock_phone_apps_now",
  "unlock_phone_now",
  "lock_pc_now",
  "unlock_pc_now",
]

export default function YuiLockPanel(props: PluginSurfaceProps) {
  const state: Record<string, any> = (props.state || {}) as Record<string, any>
  const actions: HostedAction[] = Array.isArray(props.actions) ? props.actions : []

  const [status, setStatus] = useState<StatusState>({
    loading: false,
    online: null,
    summary: "",
    error: "",
  })

  const refreshingRef = useRef(false)
  const unmountedRef = useRef(false)

  const refresh = async () => {
    if (refreshingRef.current || unmountedRef.current) return
    refreshingRef.current = true
    setStatus((prev) => ({ ...prev, loading: true, error: "" }))
    try {
      const envelope = await props.api.call("yuilock_status")
      if (unmountedRef.current) return
      const data = unwrapActionResult(envelope)
      setStatus({
        loading: false,
        // RPC 成功 ≠ 手机在线：只用接口明确给出的 phone_online 上色
        online: data.phone_online === true,
        summary: String(data.summary || ""),
        error: "",
      })
    } catch (exc: any) {
      if (unmountedRef.current) return
      const raw = String(exc?.message || exc)
      setStatus((prev) => ({
        ...prev,
        loading: false,
        online: false,
        summary: "",
        error: raw,
      }))
    } finally {
      refreshingRef.current = false
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

  const pcLocked = Boolean(state.pc_lock)
  const tokenSet = Boolean(state.token_set)
  const pairUri = String(state.pair_uri || "")

  const statusItems = status.summary
    ? [{ key: "summary", label: "状态", value: status.summary }]
    : []

  const panelActions = ACTION_IDS.map((id) =>
    actions.find((a) => a && (a.id === id || a.entry_id === id)),
  ).filter(Boolean) as HostedAction[]

  return (
    <Page title="Yui 手机/电脑锁" subtitle="让 Yui 生气时可以惩罚主人：锁手机、锁电脑">
      <Card title="手机状态">
        <Stack>
          <StatusBadge
            tone={status.online === null ? "default" : status.online ? "success" : "warning"}
          >
            {status.online === null ? "查询中…" : status.online ? "在线" : "不在线"}
          </StatusBadge>
          {status.error ? <Alert tone="warning">{status.error}</Alert> : null}
          {statusItems.length > 0 ? <KeyValue items={statusItems} /> : null}
          <Button onClick={refresh} disabled={status.loading}>
            刷新状态
          </Button>
          {!tokenSet ? (
            <Alert tone="warning">
              plugin.toml 的 [yuilock] 还没有设置 token：手机端会拒绝一切控制命令。设置后记得在插件卡片上点「重载」。
            </Alert>
          ) : null}
        </Stack>
      </Card>

      <Card title="配对二维码">
        <Stack>
          {state.qr_data_url ? (
            <img
              src={String(state.qr_data_url)}
              alt="Yui Lock 配对二维码"
              style={{ width: "220px", height: "220px", background: "#fff", borderRadius: "10px" }}
            />
          ) : (
            <Tip>
              二维码生成需要 qrcode 库：在 N.E.K.O. 的 Python 环境安装
              `pip install qrcode pillow`，或电脑上运行 `uv run --with qrcode --with pillow python pair_qr.py`。
            </Tip>
          )}
          <Text>
            手机打开 Yui Lock App → 点「扫码配对」→ 对准上面的二维码，端口和令牌自动填好。
          </Text>
          {pairUri ? <Text>配对码：{pairUri}</Text> : null}
          <Text>
            局域网地址 {String(state.host || "?")}:{String(state.port || "?")} · 手机和电脑需同一 Wi-Fi（或蓝牙配对）。
          </Text>
        </Stack>
      </Card>

      <Card title="手动控制">
        <Stack>
          <StatusBadge tone={pcLocked ? "danger" : "default"}>
            {pcLocked ? "电脑已锁定" : "电脑未锁定"}
          </StatusBadge>
          <Text>危险操作有二次确认；也可以直接在对话里让 Yui 自己决定。</Text>
          <div style={{ display: "flex", flexWrap: "wrap", gap: "8px" }}>
            {panelActions.map((action) => (
              <ActionButton key={action.id} action={action} />
            ))}
          </div>
          {panelActions.length === 0 ? (
            <Text>面板动作尚未加载（插件可能未启动，启动后刷新本页）。</Text>
          ) : null}
        </Stack>
      </Card>

      <Tip>
        电脑锁定期间打开任何程序都会被立即弹回桌面（N.E.K.O. 不受影响，重启即解锁）；
        手机应用锁重启即解除。解锁方式不对外显示。
      </Tip>
    </Page>
  )
}
