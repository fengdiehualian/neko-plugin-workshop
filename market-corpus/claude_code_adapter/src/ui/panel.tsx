import {
  Page,
  Toolbar,
  ToolbarGroup,
  StatusBadge,
  EmptyState,
  Button,
  RefreshButton,
  Alert,
  useState,
  useEffect,
  useToast,
  useConfirm,
  useClipboard,
} from "@neko/plugin-ui"
import type { PluginSurfaceProps } from "@neko/plugin-ui"

// ==========================================================================
// Claude Code Adapter — 会话管理面板
//
// 界面参考 cc-switch（farion1231/cc-switch）的「会话管理」页：
// - 左侧：会话列表（标题 + 相对时间 + 项目目录 + 搜索过滤）
// - 右侧：会话详情（UUID、时间、用户/AI/工具消息、长消息折叠、删除会话）
// 数据来源：~/.claude/projects 下的 Claude 原生会话存档（*.jsonl）
//
// 注意：api.call 返回的是宿主包装的 envelope
//   { plugin_id, action_id, result: <插件 entry 返回值> }
// 必须先经 unwrapActionResult 解包再读字段（与 neko_live / neko_warthunder
// 等内置插件一致），否则 result.sessions 永远是 undefined → 面板 0 会话。
// ==========================================================================

type SessionMeta = {
  session_id?: string
  title?: string
  summary?: string
  project_dir?: string
  created_at?: number | null
  last_active_at?: number | null
  source_path?: string
}

type SessionMessage = {
  role?: string
  content?: string
  ts?: number | null
}

type SessionDetail = {
  session_id?: string
  title?: string
  project_dir?: string
  created_at?: number | null
  last_active_at?: number | null
  message_count?: number
  messages?: SessionMessage[]
}

type PluginState = {
  cli_available?: boolean
}

function unwrapActionResult(envelope: any): Record<string, any> {
  if (envelope && typeof envelope === "object") {
    if (envelope.result && typeof envelope.result === "object") return envelope.result
    return envelope
  }
  return {}
}

function formatTime(ts: number | null | undefined): string {
  if (!ts) return ""
  const d = new Date(ts)
  if (isNaN(d.getTime())) return ""
  const pad = (n: number) => String(n).padStart(2, "0")
  return `${d.getFullYear()}/${d.getMonth() + 1}/${d.getDate()} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}

// 相对时间（照 cc-switch SessionItem 的 formatRelativeTime）
function formatRelativeTime(ts: number | null | undefined): string {
  if (!ts) return ""
  const d = new Date(ts)
  if (isNaN(d.getTime())) return ""
  const diff = Date.now() - d.getTime()
  const minutes = Math.floor(diff / 60000)
  const hours = Math.floor(diff / 3600000)
  const days = Math.floor(diff / 86400000)
  if (minutes < 1) return "刚刚"
  if (minutes < 60) return `${minutes} 分钟前`
  if (hours < 24) return `${hours} 小时前`
  if (days < 7) return `${days} 天前`
  return `${d.getFullYear()}/${d.getMonth() + 1}/${d.getDate()}`
}

function projectName(dir: string | undefined): string {
  if (!dir) return ""
  const parts = String(dir).replace(/\\/g, "/").split("/").filter(Boolean)
  return parts.length > 0 ? parts[parts.length - 1] : ""
}

const styles = {
  body: {
    display: "flex",
    gap: "12px",
    alignItems: "stretch",
    minHeight: "560px",
  },
  sidebar: {
    width: "300px",
    minWidth: "300px",
    border: "1px solid #e5e7eb",
    borderRadius: "10px",
    background: "#f9fafb",
    overflowY: "auto",
    maxHeight: "640px",
  },
  searchBox: {
    padding: "8px 10px",
    borderBottom: "1px solid #eef0f3",
    position: "sticky" as const,
    top: 0,
    background: "#f9fafb",
    zIndex: 1,
  },
  searchInput: {
    width: "100%",
    boxSizing: "border-box" as const,
    padding: "6px 10px",
    fontSize: "12px",
    border: "1px solid #d1d5db",
    borderRadius: "6px",
    background: "#ffffff",
    color: "#1f2937",
    outline: "none",
  },
  sidebarItem: {
    padding: "10px 12px",
    borderBottom: "1px solid #eef0f3",
    cursor: "pointer",
  },
  sidebarItemSelected: {
    background: "#e8f0fe",
    borderLeft: "3px solid #4285f4",
  },
  itemTitle: {
    fontSize: "13px",
    fontWeight: 600,
    color: "#1f2937",
    overflow: "hidden",
    textOverflow: "ellipsis",
    display: "-webkit-box",
    WebkitLineClamp: "2",
    WebkitBoxOrient: "vertical",
    lineHeight: "1.4",
  },
  itemMeta: {
    fontSize: "11px",
    color: "#9ca3af",
    marginTop: "4px",
    display: "flex",
    justifyContent: "space-between",
    gap: "8px",
  },
  detail: {
    flex: "1",
    minWidth: "0",
    border: "1px solid #e5e7eb",
    borderRadius: "10px",
    background: "#ffffff",
    display: "flex",
    flexDirection: "column",
    maxHeight: "640px",
  },
  detailHeader: {
    padding: "12px 16px",
    borderBottom: "1px solid #eef0f3",
  },
  detailTitle: {
    fontSize: "15px",
    fontWeight: 700,
    color: "#111827",
    margin: "0 0 6px 0",
    wordBreak: "break-word",
  },
  detailMeta: {
    fontSize: "11px",
    color: "#9ca3af",
    display: "flex",
    flexWrap: "wrap",
    gap: "10px",
    alignItems: "center",
  },
  uuid: {
    fontFamily: "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace",
    fontSize: "11px",
    background: "#f3f4f6",
    borderRadius: "4px",
    padding: "1px 6px",
    cursor: "pointer",
  },
  messages: {
    flex: "1",
    overflowY: "auto",
    padding: "16px",
    background: "#f5f6f8",
  },
  bubbleRow: {
    display: "flex",
    marginBottom: "14px",
  },
  bubble: {
    maxWidth: "82%",
    borderRadius: "10px",
    padding: "10px 12px",
    fontSize: "13px",
    lineHeight: "1.6",
    whiteSpace: "pre-wrap",
    wordBreak: "break-word",
  },
  roleTag: {
    fontSize: "11px",
    fontWeight: 600,
    padding: "1px 8px",
    borderRadius: "999px",
    marginBottom: "4px",
    display: "inline-block",
  },
  deleteButton: {
    background: "#dc2626",
    color: "#ffffff",
    border: "none",
    borderRadius: "6px",
    padding: "6px 14px",
    fontSize: "13px",
    cursor: "pointer",
  },
  expandButton: {
    background: "transparent",
    border: "none",
    color: "#2563eb",
    fontSize: "12px",
    cursor: "pointer",
    padding: "4px 0 0 0",
  },
}

function roleAppearance(role: string): { label: string; tagBg: string; tagFg: string; bubbleBg: string; align: string } {
  if (role === "user") {
    return { label: "用户", tagBg: "#dcfce7", tagFg: "#15803d", bubbleBg: "#ffffff", align: "flex-start" }
  }
  if (role === "assistant") {
    return { label: "AI", tagBg: "#dbeafe", tagFg: "#1d4ed8", bubbleBg: "#eff6ff", align: "flex-start" }
  }
  return { label: "工具", tagBg: "#f3f4f6", tagFg: "#6b7280", bubbleBg: "#f9fafb", align: "flex-start" }
}

// 长消息折叠阈值（照 cc-switch SessionMessageItem：>3000 字符折叠到 1500）
const COLLAPSE_THRESHOLD = 3000
const COLLAPSED_LENGTH = 1500

function MessageBubble({ message }: { message: SessionMessage }) {
  const role = message.role || "unknown"
  const appearance = roleAppearance(role)
  const [expanded, setExpanded] = useState(false)
  const content = message.content || ""
  const isLong = content.length > COLLAPSE_THRESHOLD
  const displayContent = isLong && !expanded ? content.slice(0, COLLAPSED_LENGTH) + "…" : content
  return (
    <div style={{ ...styles.bubbleRow, justifyContent: appearance.align }}>
      <div style={{ maxWidth: "82%" }}>
        <span style={{ ...styles.roleTag, background: appearance.tagBg, color: appearance.tagFg }}>
          {appearance.label}
        </span>
        {message.ts ? (
          <span style={{ fontSize: "11px", color: "#9ca3af", marginLeft: "8px" }}>
            {formatTime(message.ts)}
          </span>
        ) : null}
        <div style={{ ...styles.bubble, background: appearance.bubbleBg, border: "1px solid #e5e7eb", marginTop: "4px" }}>
          {displayContent}
          {isLong ? (
            <div>
              <button type="button" style={styles.expandButton} onClick={() => setExpanded((v) => !v)}>
                {expanded ? "收起" : `展开完整内容 (${Math.round(content.length / 1000)}k)`}
              </button>
            </div>
          ) : null}
        </div>
      </div>
    </div>
  )
}

export default function ClaudeCodeAdapterPanel(props: PluginSurfaceProps<PluginState>) {
  const { api } = props
  const toast = useToast()
  const confirm = useConfirm()
  const clipboard = useClipboard()

  const [sessions, setSessions] = useState<SessionMeta[]>([])
  const [loadingList, setLoadingList] = useState(false)
  const [listError, setListError] = useState("")
  const [searchQuery, setSearchQuery] = useState("")
  const [selectedId, setSelectedId] = useState("")
  const [detail, setDetail] = useState<SessionDetail | null>(null)
  const [loadingDetail, setLoadingDetail] = useState(false)
  const [detailError, setDetailError] = useState("")
  const [deleting, setDeleting] = useState(false)

  const loadSessions = async () => {
    setLoadingList(true)
    setListError("")
    try {
      // api.call 返回宿主包装的 envelope，真实数据在 envelope.result 里，
      // 必须先解包（否则 result.sessions 恒为 undefined，面板显示 0 会话）
      const envelope = await api.call("list_claude_sessions", { limit: 300 }, { timeoutMs: 60000 })
      const result: any = unwrapActionResult(envelope)
      const list = Array.isArray(result && result.sessions) ? result.sessions : []
      setSessions(list)
    } catch (error: any) {
      setListError(error && error.message ? error.message : String(error))
    } finally {
      setLoadingList(false)
    }
  }

  const loadDetail = async (sessionId: string) => {
    setLoadingDetail(true)
    setDetailError("")
    setDetail(null)
    try {
      const envelope = await api.call("get_claude_session", { session_id: sessionId }, { timeoutMs: 60000 })
      const result: any = unwrapActionResult(envelope)
      setDetail(result && result.session_id ? result : null)
    } catch (error: any) {
      setDetailError(error && error.message ? error.message : String(error))
    } finally {
      setLoadingDetail(false)
    }
  }

  useEffect(() => {
    loadSessions()
  }, [])

  const onSelect = (sessionId: string) => {
    if (!sessionId || sessionId === selectedId) return
    setSelectedId(sessionId)
    loadDetail(sessionId)
  }

  const onDelete = async () => {
    if (!selectedId || deleting) return
    const accepted = await confirm({
      title: "删除会话",
      message: `确定删除会话 ${selectedId.slice(0, 8)}… 吗？会话文件和附属数据将被永久删除，无法恢复。`,
      tone: "danger",
      confirmLabel: "删除会话",
      cancelLabel: "取消",
    })
    if (!accepted) return
    setDeleting(true)
    try {
      await api.call("delete_claude_session", { session_id: selectedId }, { userInitiated: true })
      toast.success("会话已删除")
      setSelectedId("")
      setDetail(null)
      await loadSessions()
    } catch (error: any) {
      toast.error(error && error.message ? error.message : String(error))
    } finally {
      setDeleting(false)
    }
  }

  const copyUuid = async () => {
    if (!selectedId) return
    try {
      await clipboard.write(selectedId)
      toast.success("会话 UUID 已复制")
    } catch (error: any) {
      toast.error("复制失败：" + (error && error.message ? error.message : String(error)))
    }
  }

  // 搜索过滤：标题 / 项目目录 / 会话 ID（照 cc-switch useSessionSearch 的匹配面）
  const query = searchQuery.trim().toLowerCase()
  const filteredSessions = query
    ? sessions.filter((session) => {
        const title = (session.title || "").toLowerCase()
        const dir = (session.project_dir || "").toLowerCase()
        const sid = (session.session_id || "").toLowerCase()
        return title.includes(query) || dir.includes(query) || sid.includes(query)
      })
    : sessions

  return (
    <Page title="CC Switch" subtitle="会话管理 — 查看 / 检索 / 删除 Claude Code 会话">
      <Toolbar>
        <ToolbarGroup>
          <StatusBadge tone={sessions.length > 0 ? "success" : "warning"}>
            共 {sessions.length} 个会话
          </StatusBadge>
          {query && filteredSessions.length !== sessions.length ? (
            <StatusBadge tone="info">匹配 {filteredSessions.length} 个</StatusBadge>
          ) : null}
          {loadingList ? <StatusBadge tone="info">扫描中…</StatusBadge> : null}
        </ToolbarGroup>
        <ToolbarGroup>
          <RefreshButton onClick={loadSessions}>刷新</RefreshButton>
        </ToolbarGroup>
      </Toolbar>

      {listError ? <Alert tone="danger">扫描会话失败：{listError}</Alert> : null}

      {sessions.length === 0 && !loadingList && !listError ? (
        <EmptyState
          title="暂无 Claude 会话"
          description="还没有任何 Claude Code 会话记录。让猫娘调用 claude_code_submit 执行任务后，会话会自动出现在这里。"
        />
      ) : (
        <div style={styles.body}>
          {/* 左侧：会话列表（含搜索框） */}
          <div style={styles.sidebar}>
            <div style={styles.searchBox}>
              <input
                style={styles.searchInput}
                placeholder="搜索标题 / 项目目录 / 会话 ID"
                value={searchQuery}
                onChange={(event: any) => setSearchQuery(event && event.target ? String(event.target.value || "") : "")}
              />
            </div>
            {filteredSessions.length === 0 ? (
              <div style={{ padding: "16px 12px", color: "#9ca3af", fontSize: "12px" }}>
                没有匹配「{searchQuery.trim()}」的会话
              </div>
            ) : (
              filteredSessions.map((session) => {
                const id = session.session_id || ""
                const selected = id === selectedId
                return (
                  <div
                    key={id}
                    style={{ ...styles.sidebarItem, ...(selected ? styles.sidebarItemSelected : {}) }}
                    onClick={() => onSelect(id)}
                  >
                    <div style={styles.itemTitle}>{session.title || "(无标题)"}</div>
                    <div style={styles.itemMeta}>
                      <span>{projectName(session.project_dir) || "未知项目"}</span>
                      <span title={formatTime(session.last_active_at || session.created_at)}>
                        {formatRelativeTime(session.last_active_at || session.created_at)}
                      </span>
                    </div>
                  </div>
                )
              })
            )}
          </div>

          {/* 右侧：会话详情 */}
          <div style={styles.detail}>
            {!selectedId ? (
              <div style={{ padding: "40px", color: "#9ca3af", textAlign: "center", fontSize: "13px" }}>
                从左侧选择一个会话查看对话内容
              </div>
            ) : (
              <>
                <div style={styles.detailHeader}>
                  <div style={{ display: "flex", justifyContent: "space-between", gap: "12px", alignItems: "flex-start" }}>
                    <div style={{ minWidth: 0, flex: 1 }}>
                      <p style={styles.detailTitle}>{(detail && detail.title) || selectedId}</p>
                      <div style={styles.detailMeta}>
                        <span>{formatTime((detail && detail.created_at) || undefined) || "—"}</span>
                        <span
                          style={styles.uuid}
                          title="点击复制完整 UUID"
                          onClick={copyUuid}
                        >
                          {clipboard.copied ? "已复制 ✓" : selectedId}
                        </span>
                        {detail && detail.project_dir ? <span>{detail.project_dir}</span> : null}
                        {detail && typeof detail.message_count === "number" ? (
                          <span>共 {detail.message_count} 条消息</span>
                        ) : null}
                      </div>
                    </div>
                    <button
                      style={{ ...styles.deleteButton, opacity: deleting ? 0.6 : 1 }}
                      disabled={deleting}
                      onClick={onDelete}
                    >
                      {deleting ? "删除中…" : "删除会话"}
                    </button>
                  </div>
                </div>

                <div style={styles.messages}>
                  {loadingDetail ? (
                    <div style={{ color: "#9ca3af", fontSize: "13px", padding: "16px" }}>加载对话内容中…</div>
                  ) : detailError ? (
                    <div style={{ padding: "16px" }}>
                      <Alert tone="danger">加载失败：{detailError}</Alert>
                      <Button onClick={() => loadDetail(selectedId)}>重试</Button>
                    </div>
                  ) : detail && Array.isArray(detail.messages) ? (
                    detail.messages.length > 0 ? (
                      detail.messages.map((message, index) => (
                        <MessageBubble key={`${index}`} message={message} />
                      ))
                    ) : (
                      <div style={{ color: "#9ca3af", fontSize: "13px", padding: "16px" }}>
                        该会话没有可展示的消息内容。
                      </div>
                    )
                  ) : null}
                </div>
              </>
            )}
          </div>
        </div>
      )}
    </Page>
  )
}
