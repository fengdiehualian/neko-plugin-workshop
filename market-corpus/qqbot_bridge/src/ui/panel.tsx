// QQ机器人桥接 - Hosted TSX 面板（由 N.E.K.O 宿主渲染，走 @ui.context / @ui.action 后端，无需手动 fetch）
import {
  Page,
  Card,
  Grid,
  Stack,
  Text,
  Button,
  ButtonGroup,
  Input,
  CodeBlock,
  StatusBadge,
  Divider,
  Alert,
} from "@neko/plugin-ui"
import type { HostedAction, PluginSurfaceProps } from "@neko/plugin-ui"

type RecentItem = {
  ts?: number
  direction?: string
  channel?: string
  author?: string
  text?: string
}

type QqState = {
  connected?: boolean
  app_id?: string
  session_id?: string
  reply_mode?: string
  webhook_url?: string
  auto_reply?: boolean
  recent?: RecentItem[]
  error?: string
  started_at?: number
}

function fmtTime(ts?: number): string {
  if (!ts) return ""
  const d = new Date(ts * 1000)
  return d.toLocaleTimeString()
}

export default function QqBotBridgePanel(props: PluginSurfaceProps<QqState>) {
  const { state, actions } = props
  const safeState = state || {}
  const recent = Array.isArray(safeState.recent) ? (safeState.recent as RecentItem[]) : []
  const connected = !!safeState.connected

  const sendAction = actions.find((a) => a.id === "qq_send") as HostedAction | undefined
  const reconnectAction = actions.find((a) => a.id === "qq_reconnect") as HostedAction | undefined

  const [targetType, setTargetType] = props.useLocalState("target_type", "group")
  const [targetId, setTargetId] = props.useLocalState("target_id", "")
  const [content, setContent] = props.useLocalState("content", "")

  const doSend = () => {
    if (sendAction) {
      props.api.call("qq_send", {
        target_type: targetType,
        target_id: targetId,
        content: content,
      })
    }
  }

  return (
    <Page title="QQ机器人桥接" subtitle="在 N.E.K.O 里连接官方 QQ 机器人">
      <Card title="连接状态">
        <Stack>
          <Stack direction="horizontal">
            {connected ? (
              <StatusBadge tone="success">已连接</StatusBadge>
            ) : (
              <StatusBadge tone="danger">未连接</StatusBadge>
            )}
            <StatusBadge tone="info">{safeState.reply_mode || "webhook"}</StatusBadge>
            {safeState.auto_reply ? (
              <StatusBadge tone="success">自动回复开</StatusBadge>
            ) : (
              <StatusBadge tone="default">自动回复关</StatusBadge>
            )}
          </Stack>
          <Text>AppID：{safeState.app_id || "（未配置）"}</Text>
          <Text>Webhook：{safeState.webhook_url || "（未配置）"}</Text>
          {safeState.error ? <Alert tone="danger">{safeState.error}</Alert> : null}
        </Stack>
      </Card>

      <Card title="发送消息（主动）">
        <Stack>
          <Stack direction="horizontal">
            <Input value={targetType} placeholder="group / c2c / channel" onChange={setTargetType} />
            <Input value={targetId} placeholder="目标 ID（group_openid 等）" onChange={setTargetId} />
          </Stack>
          <Input value={content} placeholder="要发送的文本" onChange={setContent} />
          <ButtonGroup>
            <Button tone="primary" onClick={doSend}>📤 发送</Button>
            {reconnectAction ? (
              <Button tone="default" onClick={() => props.api.call("qq_reconnect", {})}>🔄 重连</Button>
            ) : null}
          </ButtonGroup>
        </Stack>
      </Card>

      {recent.length === 0 ? (
        <Card title="最近消息"><Text>暂无消息。在 QQ 里 @ 机器人，或上面的表单主动发送一条试试。</Text></Card>
      ) : (
        <Card title={`最近消息（${recent.length}）`}>
          <Stack>
            {recent
              .slice()
              .reverse()
              .map((item, idx) => (
                <Card key={idx} title={`${item.direction === "in" ? "← 收到" : "→ 发送"} · ${item.channel || "?"}`}>
                  <Stack>
                    <Text>
                      {fmtTime(item.ts)} · {item.author || "?"}
                    </Text>
                    <CodeBlock>{item.text || ""}</CodeBlock>
                  </Stack>
                </Card>
              ))}
          </Stack>
        </Card>
      )}

      <Divider />
      <Text>
        回复模式说明：echo=原样返回；webhook=把消息 POST 到你的 N.E.K.O Agent 端点并把返回值发回 QQ；none=只接收。
      </Text>
    </Page>
  )
}
