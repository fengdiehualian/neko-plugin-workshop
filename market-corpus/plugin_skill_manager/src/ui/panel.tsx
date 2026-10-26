// Hosted TSX 面板 · 方向C「蓝黑印」(令牌见 plugins/DESIGN.md)。v3:ActionForm/慢入口三路。
// 数据:Python 侧 @ui.context(id="plugin_skill_manager_panel") -> props.state;动作:@ui.action -> props.actions。
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

const SLOW: Record<string, boolean> = { skill_sync: true, skill_reload: true, skill_invoke: true, skill_validate: true }
const SLOW_MS = 300000

export default function Panel(props: PluginSurfaceProps<State>) {
  const { state, actions } = props
  const entries = state.entries && state.entries.length ? state.entries : [
  {
    "id": "skill_list",
    "name": "skill_list",
    "description": "",
    "timeout": 0.0,
    "has_params": false
  },
  {
    "id": "skill_sync",
    "name": "skill_sync",
    "description": "",
    "timeout": 300.0,
    "has_params": false
  },
  {
    "id": "skill_enable",
    "name": "skill_enable",
    "description": "",
    "timeout": 0.0,
    "has_params": true
  },
  {
    "id": "skill_disable",
    "name": "skill_disable",
    "description": "",
    "timeout": 0.0,
    "has_params": true
  },
  {
    "id": "skill_reload",
    "name": "skill_reload",
    "description": "",
    "timeout": 300.0,
    "has_params": false
  },
  {
    "id": "skill_invoke",
    "name": "skill_invoke",
    "description": "",
    "timeout": 300.0,
    "has_params": true
  },
  {
    "id": "skill_validate",
    "name": "skill_validate",
    "description": "",
    "timeout": 300.0,
    "has_params": false
  }
]
  const actionOf = (id: string) =>
    actions.find((a) => a.id === id) as HostedAction | undefined

  const callSlow = (id: string) => {
    props.api.call(id, {}, { userInitiated: true, timeoutMs: SLOW_MS })
  }

  return (
    <Page title="Plugin Skill Manager" subtitle="自带 4 个可执行 Skill(知识库/数据库) + 用户文件夹各 Agent 的 skills 根以目录联接挂进 skills/ 统一登记与校验。">
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
          const slow = Boolean(SLOW[e.id])
          const hp = hasParamsOf(e)
          return (
            <Card key={e.id} title={e.name}>
              <Stack>
                {e.description ? <Text>{e.description}</Text> : null}
                {hp ? (
                  <ActionForm action={act} />
                ) : (
                  <Stack>
                    {slow ? <Text>(慢入口:最长可等 {SLOW_MS / 1000}s)</Text> : null}
                    {slow ? (
                      <Button onClick={() => callSlow(e.id)}>执行 {e.name}</Button>
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
