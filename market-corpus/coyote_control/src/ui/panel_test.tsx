import {
  Page,
  Card,
  Text,
  Stack,
} from "@neko/plugin-ui"
import type { PluginSurfaceProps } from "@neko/plugin-ui"

export default function TestPanel(props: PluginSurfaceProps<any>) {
  return (
    <Page title="测试面板" subtitle={props.plugin.name}>
      <Card title="基础测试">
        <Stack>
          <Text>✅ 如果你能看到这段文字，说明 UI 系统工作正常</Text>
          <Text>当前时间: {new Date().toLocaleString()}</Text>
          <Text>插件 ID: {props.plugin.id}</Text>
          <Text>面板 ID: {props.surface.id}</Text>
        </Stack>
      </Card>
    </Page>
  )
}
