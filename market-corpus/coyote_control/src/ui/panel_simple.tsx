import {
  Page,
  Card,
  Text,
} from "@neko/plugin-ui"
import type { PluginSurfaceProps } from "@neko/plugin-ui"

type State = {
  is_controlling?: boolean
  connected?: boolean
  current_mood?: string
  status_message?: string
}

export default function CoyoteControlPanel(props: PluginSurfaceProps<State>) {
  const { state } = props

  return (
    <Page title="郊狼控制测试">
      <Card title="状态">
        <Text>控制中: {state?.is_controlling ? "是" : "否"}</Text>
        <Text>已连接: {state?.connected ? "是" : "否"}</Text>
        <Text>当前心情: {state?.current_mood || "未知"}</Text>
        <Text>状态消息: {state?.status_message || "无"}</Text>
      </Card>
    </Page>
  )
}
