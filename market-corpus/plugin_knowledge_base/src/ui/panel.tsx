// Hosted TSX 面板 · 方向C「蓝黑印」(令牌见 plugins/DESIGN.md)。v3:ActionForm/慢入口三路。
// 数据:Python 侧 @ui.context(id="plugin_knowledge_base_panel") -> props.state;动作:@ui.action -> props.actions。
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

// 慢入口集合（真实 timeout 见 __init__.py 的 @plugin_entry）：这些入口要加载
// 90MB 模型 / 走网络 / 跑批量嵌入，ActionButton 的默认动作超时覆盖不了，
// 必须改走 callSlow（props.api.call + timeoutMs）。
// 历史缺陷：SLOW 恒为 {}，于是 SLOW_MS / callSlow / 慢入口 Button 分支全是死代码。
const SLOW: Record<string, boolean> = {
  kb_import: true, // timeout=300s：批量分块 + 嵌入 + 逐块 IPC
  kb_model_download: true, // timeout=600s：model.onnx 约 90MB，走网络
}
const SLOW_MS = 180000
// fallback 列表的 timeout 必须与 @plugin_entry 的真实值一致（秒），
// 否则 state.entries 为空时面板既判不出慢入口、也展示了错误的时间。
const TIMEOUTS: Record<string, number> = {
  kb_import: 300,
  kb_search: 60,
  kb_hot_list: 30,
  kb_hot_export: 60,
  kb_stats: 30,
  kb_demote_sweep: 30,
  kb_rebuild: 60,
  kb_model_status: 0,
  kb_model_download: 600,
  kb_record_skill_stat: 30,
  kb_query_skill_stats: 30,
}

const isSlow = (e: AnyRow): boolean => SLOW[String(e.id)] === true

export default function Panel(props: PluginSurfaceProps<State>) {
  const { state, actions } = props
  const entries = state.entries && state.entries.length ? state.entries : [
  {
    "id": "kb_import",
    "name": "kb_import",
    "description": "",
    "timeout": TIMEOUTS.kb_import,
    "has_params": true
  },
  {
    "id": "kb_search",
    "name": "kb_search",
    "description": "",
    "timeout": TIMEOUTS.kb_search,
    "has_params": true
  },
  {
    "id": "kb_hot_list",
    "name": "kb_hot_list",
    "description": "",
    "timeout": TIMEOUTS.kb_hot_list,
    "has_params": true
  },
  {
    "id": "kb_hot_export",
    "name": "kb_hot_export",
    "description": "",
    "timeout": TIMEOUTS.kb_hot_export,
    "has_params": true
  },
  {
    "id": "kb_stats",
    "name": "kb_stats",
    "description": "",
    "timeout": TIMEOUTS.kb_stats,
    "has_params": false
  },
  {
    "id": "kb_demote_sweep",
    "name": "kb_demote_sweep",
    "description": "",
    "timeout": TIMEOUTS.kb_demote_sweep,
    "has_params": false
  },
  {
    "id": "kb_rebuild",
    "name": "kb_rebuild",
    "description": "",
    "timeout": TIMEOUTS.kb_rebuild,
    "has_params": false
  },
  {
    "id": "kb_model_status",
    "name": "kb_model_status",
    "description": "",
    "timeout": TIMEOUTS.kb_model_status,
    "has_params": false
  },
  {
    "id": "kb_model_download",
    "name": "kb_model_download",
    "description": "",
    "timeout": TIMEOUTS.kb_model_download,
    "has_params": true
  },
  {
    "id": "kb_record_skill_stat",
    "name": "kb_record_skill_stat",
    "description": "",
    "timeout": TIMEOUTS.kb_record_skill_stat,
    "has_params": true
  },
  {
    "id": "kb_query_skill_stats",
    "name": "kb_query_skill_stats",
    "description": "",
    "timeout": TIMEOUTS.kb_query_skill_stats,
    "has_params": false
  }
]
  const actionOf = (id: string) =>
    actions.find((a) => a.id === id) as HostedAction | undefined

  const callSlow = (id: string) => {
    props.api.call(id, {}, { userInitiated: true, timeoutMs: SLOW_MS })
  }

  return (
    <Page title="Plugin Knowledge Base" subtitle="知识库：导入 .md/.txt/.pdf 文件，按段落分块（默认 240 tokens / 重叠 64，可配置；MiniLM 窗口 256），用 all-…">
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
