// AnySearch 联网搜索 - Hosted TSX 面板（由 N.E.K.O 宿主渲染，走 @ui.context / @ui.action 后端，无需手动 fetch）
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

type ResultItem = {
  title?: string
  url?: string
  description?: string
  content?: string
  source?: string
  score?: number
  quality_score?: number
  published_at?: string
}

type RecentItem = {
  ts?: number
  query?: string
  count?: number
}

type SearchState = {
  configured?: boolean
  mode?: string
  zone?: string
  language?: string
  max_results?: number
  recent?: RecentItem[]
  last_error?: string
  last_query?: string
  last_results?: ResultItem[]
  last_search_ms?: number
}

function fmtTime(ts?: number): string {
  if (!ts) return ""
  return new Date(ts * 1000).toLocaleString()
}

export default function AnySearchPanel(props: PluginSurfaceProps<SearchState>) {
  const { state, actions } = props
  const safe = state || {}
  const results: ResultItem[] = Array.isArray(safe.last_results) ? (safe.last_results as ResultItem[]) : []
  const recent: RecentItem[] = Array.isArray(safe.recent) ? (safe.recent as RecentItem[]) : []
  const searchAction = actions.find((a) => a.id === "search") as HostedAction | undefined

  const [query, setQuery] = props.useLocalState("query", "")
  const [maxResults, setMaxResults] = props.useLocalState("max_results", String(safe.max_results || 5))
  const [zone, setZone] = props.useLocalState("zone", safe.zone || "cn")
  const [contentTypes, setContentTypes] = props.useLocalState("content_types", "" as string)

  const doSearch = () => {
    if (!searchAction || !query.trim()) return
    const ct = contentTypes
      .split(",")
      .map((s: string) => s.trim())
      .filter(Boolean)
    props.api.call("search", {
      query: query.trim(),
      max_results: Number(maxResults) || 5,
      zone: zone,
      content_types: ct,
    })
  }

  const modeText =
    safe.mode === "api_key" ? "API Key 模式" : "匿名模式（按 IP 免费额度）"

  return (
    <Page title="AnySearch 联网搜索" subtitle={modeText}>
      <Card title="搜索">
        <Stack>
          <Input value={query} placeholder="输入搜索关键词…" onChange={setQuery} />
          <Stack direction="horizontal">
            <Input value={maxResults} placeholder="数量" onChange={setMaxResults} />
            <Input value={zone} placeholder="zone: cn/intl" onChange={setZone} />
          </Stack>
          <Input
            value={contentTypes}
            placeholder="内容类型（逗号分隔：web,news,doc,code,academic）"
            onChange={setContentTypes}
          />
          <ButtonGroup>
            <Button tone="primary" onClick={doSearch}>
              🔍 搜索
            </Button>
          </ButtonGroup>
        </Stack>
      </Card>

      {safe.last_error ? <Alert tone="danger">搜索出错：{safe.last_error}</Alert> : null}

      {results.length === 0 ? (
        <Card title="结果">
          <Text>还没有结果。输入关键词点「搜索」试试（匿名模式无需配置即可用）。</Text>
        </Card>
      ) : (
        <Card title={`搜索结果（${results.length} 条 · ${safe.last_search_ms ?? 0}ms）`}>
          <Stack>
            {results.map((r, idx) => (
              <Card key={idx} title={r.title || "(无标题)"}>
                <Stack>
                  {r.url ? (
                    <a href={r.url} target="_blank" rel="noreferrer">
                      {r.url}
                    </a>
                  ) : null}
                  {r.description ? <Text>{r.description}</Text> : null}
                  <Stack direction="horizontal">
                    {r.source ? <StatusBadge tone="info">{r.source}</StatusBadge> : null}
                    {r.quality_score ? (
                      <StatusBadge tone="success">质量 {r.quality_score.toFixed(2)}</StatusBadge>
                    ) : null}
                    {r.published_at ? <Text>{r.published_at}</Text> : null}
                  </Stack>
                  {r.content ? (
                    <CodeBlock>
                      {r.content.length > 600 ? r.content.slice(0, 600) + "…" : r.content}
                    </CodeBlock>
                  ) : null}
                </Stack>
              </Card>
            ))}
          </Stack>
        </Card>
      )}

      {recent.length > 0 ? (
        <Card title={`最近搜索（${recent.length}）`}>
          <Stack>
            {recent
              .slice()
              .reverse()
              .map((item, idx) => (
                <Text key={idx}>
                  {fmtTime(item.ts)} · {item.query}（{item.count} 条）
                </Text>
              ))}
          </Stack>
        </Card>
      ) : null}

      <Divider />
      <Text>
        AnySearch 统一搜索 API：聚合多家搜索源，可选匿名（按 IP 免费额度）或 API Key 模式。
      </Text>
    </Page>
  )
}
