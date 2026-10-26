// Hosted TSX 面板 · 方向C「蓝黑印」(令牌见 plugins/DESIGN.md)。v3:ActionForm/慢入口三路。
// 数据:Python 侧 @ui.context(id="agent_bridge_panel") -> props.state;动作:@ui.action -> props.actions。
import {
  ActionButton,
  ActionForm,
  Button,
  Card,
  Page,
  Stack,
  Text,
} from "@neko/plugin-ui"
import type { HostedAction, PluginSurfaceProps } from "@neko/plugin-ui"

type EntryRow = {
  id: string
  name: string
  description: string
  timeout: number
  has_params: boolean
  has_required?: boolean
}
type State = {
  plugin?: { id?: string; name?: string; version?: string; description?: string }
  entries?: EntryRow[]
}

type AnyRow = Record<string, unknown>
const hasParamsOf = (e: AnyRow): boolean =>
  typeof e.has_params === "boolean"
    ? (e.has_params as boolean)
    : typeof e.has_required === "boolean"
      ? (e.has_required as boolean)
      : true

// 慢入口阈值：超过它就走「带 timeoutMs 的 api.call」那条路。
// 以前这里是 `const SLOW = {}` —— 永远为空，慢入口分支永远不可达；而 agent_run
// 声明的超时是 1800 秒，一旦真跑长任务，默认的调用超时会把请求掐掉。
const SLOW_MS = 180000
const isSlow = (e: EntryRow): boolean =>
  typeof e.timeout === "number" && e.timeout > SLOW_MS / 1000

export default function Panel(props: PluginSurfaceProps<State>) {
  const { state, actions } = props
  // 兜底清单只在宿主没给 state 时用（正常路径由 Python 侧 _ui_panel_state 生成）。
  // 这里**不再手抄入口元数据**：抄本会和真入口漂移（description / has_required /
  // timeout 都错过），只留 id 与显示名。
  const FALLBACK: EntryRow[] = [
    { id: "agents_list", name: "列出可用 Agent", description: "", timeout: 1800, has_params: true, has_required: false },
    { id: "agent_run", name: "派发任务给 Agent", description: "", timeout: 1800, has_params: true, has_required: true },
    { id: "agent_run_parallel", name: "并行派发多个任务", description: "", timeout: 1800, has_params: true, has_required: true },
    { id: "agent_doctor", name: "诊断桥接链路", description: "", timeout: 1800, has_params: true, has_required: false },
  ]
  const entries = state.entries && state.entries.length ? state.entries : FALLBACK
  const actionOf = (id: string) =>
    actions.find((a) => a.id === id) as HostedAction | undefined

  const callSlow = (id: string, timeoutMs: number) => {
    props.api.call(id, {}, { userInitiated: true, timeoutMs })
  }

  return (
    <Page title="本机 Agent 桥接" subtitle="把本机的编码 Agent CLI（CodeBuddy / Claude Code / dsh / omp / EvoX / OpenCode / Open…">
      <Stack>
        {entries.map((e) => {
          const act = actionOf(e.id)
          if (!act) {
            return (
              <Card key={e.id} title={e.name}>
                <Text>动作未注册(需 @ui.action)。</Text>
              </Card>
            )
          }
          const slow = isSlow(e)
          // 慢入口按入口自己声明的 timeout 放宽调用上限（至少 SLOW_MS）。
          const timeoutMs = Math.max(SLOW_MS, (e.timeout || 0) * 1000)
          const hp = hasParamsOf(e)
          return (
            <Card key={e.id} title={e.name}>
              <Stack>
                {e.description ? <Text>{e.description}</Text> : null}
                {hp ? (
                  <ActionForm action={act} />
                ) : (
                  <Stack>
                    {slow ? <Text>(慢入口:最长可等 {timeoutMs / 1000}s)</Text> : null}
                    {slow ? (
                      <Button onClick={() => callSlow(e.id, timeoutMs)}>执行 {e.name}</Button>
                    ) : (
                      <ActionButton action={act}>执行 {e.name}</ActionButton>
                    )}
                  </Stack>
                )}
              </Stack>
            </Card>
          )
        })}
        <Text>带 * 为必填;执行结果以 entry 返回值为准。</Text>
      </Stack>
    </Page>
  )
}
