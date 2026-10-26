// Hosted TSX 面板 · 方向C「蓝黑印」(令牌见 plugins/DESIGN.md)。v3:ActionForm/慢入口三路。
// 数据:Python 侧 @ui.context(id="plugin_database_panel") -> props.state;动作:@ui.action -> props.actions。
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

const SLOW = {}
const SLOW_MS = 180000

export default function Panel(props: PluginSurfaceProps<State>) {
  const { state, actions } = props
  const entries = state.entries && state.entries.length ? state.entries : [
  {
    "id": "db_execute",
    "name": "db_execute",
    "description": "",
    "timeout": 0.0,
    "has_params": true
  },
  {
    "id": "db_fetch_all",
    "name": "db_fetch_all",
    "description": "",
    "timeout": 0.0,
    "has_params": true
  },
  {
    "id": "db_fetch_one",
    "name": "db_fetch_one",
    "description": "",
    "timeout": 0.0,
    "has_params": true
  },
  {
    "id": "db_transaction",
    "name": "db_transaction",
    "description": "",
    "timeout": 0.0,
    "has_params": true
  },
  {
    "id": "db_status",
    "name": "db_status",
    "description": "",
    "timeout": 0.0,
    "has_params": false
  },
  {
    "id": "db_capabilities",
    "name": "db_capabilities",
    "description": "",
    "timeout": 0.0,
    "has_params": false
  },
  {
    "id": "db_switch_driver",
    "name": "db_switch_driver",
    "description": "",
    "timeout": 0.0,
    "has_params": true
  },
  {
    "id": "register_hot_schema",
    "name": "register_hot_schema",
    "description": "",
    "timeout": 0.0,
    "has_params": false
  },
  {
    "id": "hot_promote",
    "name": "hot_promote",
    "description": "",
    "timeout": 0.0,
    "has_params": true
  },
  {
    "id": "hot_demote",
    "name": "hot_demote",
    "description": "",
    "timeout": 0.0,
    "has_params": true
  },
  {
    "id": "hot_query",
    "name": "hot_query",
    "description": "",
    "timeout": 0.0,
    "has_params": true
  },
  {
    "id": "skill_stat_record",
    "name": "skill_stat_record",
    "description": "",
    "timeout": 0.0,
    "has_params": true
  },
  {
    "id": "skill_stat_query",
    "name": "skill_stat_query",
    "description": "",
    "timeout": 0.0,
    "has_params": false
  }
]
  const actionOf = (id: string) =>
    actions.find((a) => a.id === id) as HostedAction | undefined

  const callSlow = (id: string) => {
    props.api.call(id, {}, { userInitiated: true, timeoutMs: SLOW_MS })
  }

  return (
    <Page title="Plugin Database" subtitle="统一数据库访问基础层：DatabaseAdapter 抽象 + SQLite（默认零配置）/ PostgreSQL / MySQL / MongoDB 四…">
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
