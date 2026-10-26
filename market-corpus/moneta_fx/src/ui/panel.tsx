// MONETA FX 交易终端 — Hosted TSX 面板
// 复刻 MONETA FX 原版交易室：市场观察、K 线图、下单面板、持仓/挂单/历史、账户分析、行情控制台。

import {
  ActionButton,
  Alert,
  Button,
  Card,
  Checkbox,
  DataTable,
  Divider,
  EmptyState,
  Field,
  Grid,
  Inline,
  InlineError,
  Input,
  KeyValue,
  List,
  Modal,
  NumberInput,
  Page,
  RefreshButton,
  Section,
  Select,
  Stack,
  StatCard,
  StatusBadge,
  Tabs,
  Text,
  Textarea,
  Toolbar,
  ToolbarGroup,
  useAsync,
  useCallback,
  useConfirm,
  useEffect,
  useLocalState,
  useMemo,
  useRef,
  useState,
  useToast,
} from "@neko/plugin-ui"
import type { HostedAction, PluginSurfaceProps } from "@neko/plugin-ui"

type Quote = {
  symbol: string
  mid: number
  bid: number
  ask: number
  time: number
  marketOpen: boolean
}

type Position = {
  id: string
  symbol: string
  side: "buy" | "sell"
  lots: number
  entry: number
  margin: number
  openedAt: number
  sl?: number
  tp?: number
  swap: number
  lastPnl: number
}

type PendingOrder = {
  id: string
  symbol: string
  side: "buy" | "sell"
  type: "limit" | "stop"
  lots: number
  price: number
  sl?: number
  tp?: number
  createdAt: number
}

type HistoryTrade = {
  id: string
  symbol: string
  side: "buy" | "sell"
  lots: number
  entry: number
  exit: number
  netPnl: number
  reason: string
  closedAt: number
}

type Account = {
  version: number
  mode: string
  initialBalance: number
  balance: number
  positions: Position[]
  pending: PendingOrder[]
  history: HistoryTrade[]
  journal: Array<{ time: number; message: string; kind: string }>
  settings: {
    leverage: number
    spreadMultiplier: number
    slippage: number
    commission: number
    swapLong: number
    swapShort: number
  }
  lastRollover: number
  equityCurve: Array<{ time: number; value: number }>
}

type Snapshot = {
  user: { id: string; username: string; role: string } | null
  book?: { account: Account; quotes: Record<string, Quote>; bars: Record<string, Bar[]>; selected: string }
  market?: { time: number; paused: boolean; speed: number; event: string; revision: number }
  baseline?: Record<string, number>
  admin?: { plans: any[]; audit: any[]; volatility: number; paused: boolean; speed: number; time: number }
  selected?: string
}

type Bar = { time: number; open: number; high: number; low: number; close: number; synthetic?: boolean }

type PanelState = {
  market: { time: number; paused: boolean; speed: number; event: string; revision: number }
  watchlist: string[]
  majors: string[]
  symbolCount: number
  quotes: Record<string, Quote>
  initialBalance: number
  version: string
}

type TabId = "terminal" | "positions" | "pending" | "history" | "report" | "journal"
type WatchFilter = "watch" | "major" | "cross"

const PERIODS = [1, 5, 15, 60]
const SPEEDS = [1, 5, 20, 100]
const EVENTS = ["dollar", "euro", "yen", "storm"]

const GLYPHS: Record<string, string> = {
  EUR: "€", USD: "$", GBP: "£", JPY: "¥", CHF: "₣", AUD: "A",
  CAD: "C", NZD: "N", CNY: "¥", HKD: "H", SGD: "S",
}

const money = (value: number) =>
  (value ?? 0).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })

const signed = (value: number) => `${value >= 0 ? "+" : ""}${money(value)}`

const priceOf = (value: number | undefined, symbol: string) =>
  value === undefined || value === null ? "—" : value.toFixed(symbol.endsWith("/JPY") ? 3 : 5)

const pipSize = (symbol: string) => (symbol.endsWith("/JPY") ? 0.01 : 0.0001)

const clockOf = (time: number) => new Date(time).toISOString().slice(5, 19).replace("T", " ")

export default function MonetaPanel(props: PluginSurfaceProps<PanelState>) {
  const { t, state, actions, api } = props
  const toast = useToast()
  const confirm = useConfirm()

  const [sessionKey] = useLocalState("moneta.session", "default")
  const [tab, setTab] = useState("terminal" as TabId)
  const [filter, setFilter] = useState("watch" as WatchFilter)
  const [period, setPeriod] = useState(1)
  const [showEma, setShowEma] = useState(true)

  const [snapshot, setSnapshot] = useState<Snapshot | null>(null)
  const [selected, setSelected] = useState("EUR/USD")
  const [side, setSide] = useState<"buy" | "sell">("sell")
  const [orderType, setOrderType] = useState<"market" | "limit" | "stop">("market")
  const [lots, setLots] = useState("0.10")
  const [entryPrice, setEntryPrice] = useState("")
  const [sl, setSl] = useState("")
  const [tp, setTp] = useState("")
  const [estimate, setEstimate] = useState<any>(null)
  const [authOpen, setAuthOpen] = useState(false)
  const [authMode, setAuthMode] = useState<"login" | "register" | "developer">("login")
  const [username, setUsername] = useState("")
  const [password, setPassword] = useState("")
  const [developerKey, setDeveloperKey] = useState("")
  const [controlOpen, setControlOpen] = useState(false)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [report, setReport] = useState<any>(null)
  const [busy, setBusy] = useState(false)

  const canvasRef = useRef<any>({ current: null })
  const chartRef = useRef<Chart | null>(null)

  const action = useCallback(
    (id: string) => actions.find((item) => item.id === id) as HostedAction | undefined,
    [actions],
  )

  const bridge = props.api

  // 调用一个插件 entry。
  //
  // props.actions 是宿主从 @ui.action 元数据收集的白名单，正常情况下里面
  // 有全部动作。但白名单依赖宿主的收集链路（collect_entries → entry_map →
  // _collect_ui_actions），中间任何一环没接上就会变成空数组——那时机就
  // 会报"xxx 动作不可用"，而 entry 本身是好的。
  //
  // 所以这里不把"白名单里没有"当致命错误：仍然尝试 api.call，让宿主去
  // 路由。真正不可用时宿主会返回 403/404，那才是可信的错误。
  const call = useCallback(
    async (id: string, args: Record<string, any> = {}) => {
      const target = action(id)
      const entryId = target?.entry_id || target?.id || id
      return bridge.call(entryId, { session_key: sessionKey, ...args })
    },
    [action, bridge, sessionKey],
  )

  // ── data loading ─────────────────────────────────────────────────────
  const load = useCallback(async () => {
    const next = (await call("state", { symbol: selected })) as Snapshot
    setSnapshot(next)
    return next
  }, [call, selected])

  const initial = useAsync(async () => {
    // state 拿不到不能让整块面板废掉——@ui.context 已经提供了首帧的
    // 行情与市场状态，先渲染出来，用户看到的是可用界面而不是错误页。
    try {
      const next = (await call("state", { symbol: selected })) as Snapshot
      setSnapshot(next)
      return next
    } catch (error) {
      console.warn("[moneta] state 首次加载失败，先用 context 数据渲染:", error)
      return null
    }
  }, [call, selected])

  useEffect(() => {
    if (!snapshot) return
    const timer = setInterval(() => {
      void load().catch(() => undefined)
    }, 1000)
    return () => clearInterval(timer)
  }, [snapshot, load])

  const account = snapshot?.book?.account
  const quotes = snapshot?.book?.quotes ?? state.quotes ?? {}
  const market = snapshot?.market ?? state.market
  const isDeveloper = snapshot?.user?.role === "developer"
  const loggedIn = !!snapshot?.user
  const quote = quotes[selected]
  const bars = useMemo(() => snapshot?.book?.bars?.[selected] ?? [], [snapshot, selected])

  const positions = account?.positions ?? []
  const pending = account?.pending ?? []
  const history = account?.history ?? []

  // 终端页内的子页签：持仓 / 挂单 / 历史
  const activeSubTab: TabId =
    tab === "pending" || tab === "history" || tab === "positions" ? tab : "positions"

  const metrics = useMemo(() => {
    if (!account) return null
    let unrealized = 0
    for (const position of positions) unrealized += position.lastPnl ?? 0
    const margin = positions.reduce((sum, p) => sum + (p.margin ?? 0), 0)
    const equity = account.balance + unrealized
    return {
      unrealized,
      equity,
      margin,
      freeMargin: equity - margin,
      marginLevel: margin ? (equity / margin) * 100 : null,
    }
  }, [account, positions])

  // ── order ticket ─────────────────────────────────────────────────────
  const lotsValue = Number(lots)
  const lotsValid = Number.isFinite(lotsValue) && lotsValue >= 0.01 && lotsValue <= 1000

  const refreshEstimate = useCallback(async () => {
    if (!loggedIn || !lotsValid || !quote) {
      setEstimate(null)
      return
    }
    try {
      const result = await call("estimate", {
        symbol: selected,
        side,
        type: orderType,
        lots: lotsValue,
        price: orderType === "market" ? undefined : Number(entryPrice) || undefined,
        sl: sl ? Number(sl) : undefined,
        tp: tp ? Number(tp) : undefined,
      })
      setEstimate(result?.estimate ?? null)
    } catch {
      setEstimate(null)
    }
  }, [call, entryPrice, loggedIn, lotsValid, lotsValue, orderType, quote, selected, side, sl, tp])

  useEffect(() => {
    const timer = setTimeout(() => void refreshEstimate(), 250)
    return () => clearTimeout(timer)
  }, [refreshEstimate])

  const submitOrder = async () => {
    if (!loggedIn) {
      toast.warning(t("panel.toast.loginRequired"))
      setAuthOpen(true)
      return
    }
    setBusy(true)
    try {
      await call("order", {
        symbol: selected,
        side,
        type: orderType,
        lots: lotsValue,
        price: orderType === "market" ? undefined : Number(entryPrice) || undefined,
        sl: sl ? Number(sl) : undefined,
        tp: tp ? Number(tp) : undefined,
      })
      toast.success(`${side === "buy" ? "买入" : "卖出"} ${selected} ${lotsValue.toFixed(2)} 手`)
      await load()
    } catch (error: any) {
      toast.error(error?.message ?? t("panel.toast.tradeFailed"))
    } finally {
      setBusy(false)
    }
  }

  const closePosition = async (id: string, lotsToClose?: number) => {
    setBusy(true)
    try {
      await call("close", { id, lots: lotsToClose })
      toast.success(t("panel.action.close"))
      await load()
    } catch (error: any) {
      toast.error(error?.message ?? t("panel.toast.tradeFailed"))
    } finally {
      setBusy(false)
    }
  }

  const closeAll = async () => {
    const ok = await confirm({
      title: t("panel.action.closeAll"),
      message: `将尝试按当前有效报价关闭全部 ${positions.length} 笔持仓。`,
      tone: "warning",
    })
    if (!ok) return
    setBusy(true)
    try {
      await call("close_all", {})
      toast.success(t("panel.action.closeAll"))
      await load()
    } catch (error: any) {
      toast.error(error?.message ?? t("panel.toast.tradeFailed"))
    } finally {
      setBusy(false)
    }
  }

  const cancelOrder = async (id: string) => {
    setBusy(true)
    try {
      await call("cancel", { id })
      toast.success(t("panel.action.cancel"))
      await load()
    } catch (error: any) {
      toast.error(error?.message ?? t("panel.toast.tradeFailed"))
    } finally {
      setBusy(false)
    }
  }

  const modifyPosition = async (id: string, nextSl?: number, nextTp?: number) => {
    setBusy(true)
    try {
      await call("modify", { id, sl: nextSl, tp: nextTp })
      toast.success(t("panel.action.modify"))
      await load()
    } catch (error: any) {
      toast.error(error?.message ?? t("panel.toast.tradeFailed"))
    } finally {
      setBusy(false)
    }
  }

  const autoProtection = () => {
    if (!quote) return
    const entry = orderType === "market" ? (side === "buy" ? quote.ask : quote.bid) : Number(entryPrice)
    if (!(entry > 0)) {
      toast.info(t("panel.order.price"))
      return
    }
    const distance = pipSize(selected) * 30
    setSl(priceOf(entry + (side === "buy" ? -distance : distance), selected))
    setTp(priceOf(entry + (side === "buy" ? distance : -distance), selected))
  }

  // ── auth ─────────────────────────────────────────────────────────────
  const submitAuth = async () => {
    setBusy(true)
    try {
      if (authMode === "developer") {
        await call("developer_login", { key: developerKey })
        toast.success(t("panel.control.developerOnly"))
      } else if (authMode === "register") {
        await call("register", { username, password })
        toast.success(t("panel.auth.register"))
      } else {
        await call("login", { username, password })
        toast.success(t("panel.auth.login"))
      }
      setAuthOpen(false)
      setPassword("")
      setDeveloperKey("")
      await load()
    } catch (error: any) {
      toast.error(error?.message ?? t("panel.toast.tradeFailed"))
    } finally {
      setBusy(false)
    }
  }

  const logout = async () => {
    setBusy(true)
    try {
      await call("logout", {})
      setSnapshot(null)
      setReport(null)
      await initial.reload()
    } finally {
      setBusy(false)
    }
  }

  // ── market control ───────────────────────────────────────────────────
  const runControl = async (body: Record<string, any>) => {
    setBusy(true)
    try {
      await call("control", body)
      toast.success(t("panel.control.apply"))
      await load()
    } catch (error: any) {
      toast.error(error?.message ?? t("panel.toast.tradeFailed"))
    } finally {
      setBusy(false)
    }
  }

  const buildReport = async () => {
    setBusy(true)
    try {
      const result = await call("report", {})
      setReport(result?.report ?? null)
    } catch (error: any) {
      toast.error(error?.message ?? t("panel.toast.tradeFailed"))
    } finally {
      setBusy(false)
    }
  }

  const saveSettings = async (next: Record<string, number>) => {
    setBusy(true)
    try {
      await call("settings", next)
      toast.success(t("panel.toast.saved"))
      setSettingsOpen(false)
      await load()
    } catch (error: any) {
      toast.error(error?.message ?? t("panel.toast.tradeFailed"))
    } finally {
      setBusy(false)
    }
  }

  // ── chart ────────────────────────────────────────────────────────────
  useEffect(() => {
    const canvas = canvasRef.current?.current
    if (!canvas || typeof canvas.getContext !== "function") return
    chartRef.current = new Chart(canvas, selected)
    const observer = new ResizeObserver(() => chartRef.current?.resize())
    observer.observe(canvas.parentElement)
    return () => observer.disconnect()
  }, [selected])

  useEffect(() => {
    chartRef.current?.update(bars, quote, positions, showEma)
  }, [bars, quote, positions, showEma])

  // ── watchlist ────────────────────────────────────────────────────────
  const watchSymbols = useMemo(() => {
    const all = state.watchlist ?? []
    if (filter === "major") return all.filter((s) => (state.majors ?? []).includes(s))
    if (filter === "cross") return all.filter((s) => !s.includes("USD"))
    return all
  }, [filter, state.majors, state.watchlist])

  const changeOf = (symbol: string) => {
    const q = quotes[symbol]
    if (!q) return null
    const base = symbol.split("/")[0]
    const reference = snapshot?.baseline?.[base]
    if (!reference) return null
    const [b, term] = symbol.split("/")
    const ref = (snapshot?.baseline?.[b] ?? 0) / (snapshot?.baseline?.[term] ?? 1)
    return ref ? (q.mid / ref - 1) * 100 : null
  }

  if (initial.loading && !snapshot) {
    return (
      <Page title="MONETA FX" subtitle={t("panel.subtitle")}>
        <Card>
          <Text>{t("panel.chart.empty")}</Text>
        </Card>
      </Page>
    )
  }

  if (initial.error && !snapshot) {
    return (
      <Page title="MONETA FX" subtitle={t("panel.subtitle")}>
        <InlineError error={initial.error} />
      </Page>
    )
  }

  return (
    <Page title="MONETA FX" subtitle={t("panel.subtitle")}>
      <Toolbar>
        <ToolbarGroup>
          <StatusBadge tone={market?.paused ? "warning" : "success"}>
            {market?.paused ? t("panel.market.paused") : t("panel.market.running")} · {market?.speed ?? 1}×
          </StatusBadge>
          <StatusBadge tone="info">{state.symbolCount} 对</StatusBadge>
          <StatusBadge>{market?.event ?? "常态市场"}</StatusBadge>
        </ToolbarGroup>
        <ToolbarGroup>
          {loggedIn ? (
            <>
              <StatusBadge tone={isDeveloper ? "danger" : "default"}>
                {snapshot?.user?.username} · {isDeveloper ? "开发者" : "普通账户"}
              </StatusBadge>
              {isDeveloper && (
                <Button tone="primary" onClick={() => { setControlOpen(true) }}>
                  {t("panel.tab.admin")}
                </Button>
              )}
              <Button onClick={() => { setSettingsOpen(true) }}>{t("panel.action.settings")}</Button>
              <Button tone="warning" onClick={logout} disabled={busy}>
                {t("panel.auth.logout")}
              </Button>
            </>
          ) : (
            <Button tone="primary" onClick={() => { setAuthOpen(true) }}>
              {t("panel.auth.login")} / {t("panel.auth.register")}
            </Button>
          )}
          <RefreshButton label={t("actions.refresh.label")} onRefresh={() => load()} />
        </ToolbarGroup>
      </Toolbar>

      <Tabs
        id="moneta-tabs"
        activeId={tab}
        onChange={(id) => { setTab(id as TabId) }}
        items={[
          { id: "terminal", label: t("panel.tab.terminal") },
          { id: "report", label: t("panel.tab.report") },
          { id: "journal", label: t("panel.tab.journal") },
        ]}
      />

      {tab === "terminal" && (
        <Grid cols={12} gap={12}>
          {/* 市场观察 */}
          <Section className="moneta-watch">
            <Card title={t("panel.watch.title")}>
              <Stack gap={8}>
                <Input
                  value=""
                  placeholder={t("panel.watch.search")}
                  onChange={() => undefined}
                />
                <Inline gap={4}>
                  {(["watch", "major", "cross"] as const).map((key) => (
                    <Button
                      key={key}
                      tone={filter === key ? "primary" : "default"}
                      onClick={() => { setFilter(key) }}
                    >
                      {t(`panel.watch.${key}`)}
                    </Button>
                  ))}
                </Inline>
                <Divider />
                <List
                  items={watchSymbols}
                  render={(symbol: string) => {
                    const q = quotes[symbol]
                    const change = changeOf(symbol)
                    const [base, term] = symbol.split("/")
                    return (
                      <button
                        type="button"
                        className={`moneta-watch-row ${symbol === selected ? "is-active" : ""}`}
                        onClick={() => { setSelected(symbol) }}
                      >
                        <span className="moneta-glyph">
                          {GLYPHS[base] ?? base.slice(0, 1)}
                          <small>{GLYPHS[term] ?? term.slice(0, 1)}</small>
                        </span>
                        <span className="moneta-watch-info">
                          <strong>{symbol}</strong>
                          <small>{priceOf(q?.mid, symbol)}</small>
                        </span>
                        <span className={change === null ? "" : change >= 0 ? "moneta-up" : "moneta-down"}>
                          {change === null ? "—" : `${change >= 0 ? "+" : ""}${change.toFixed(2)}%`}
                        </span>
                      </button>
                    )
                  }}
                />
              </Stack>
            </Card>
          </Section>

          {/* 图表 + 账户概览 */}
          <Section className="moneta-main">
            <Card
              title={`${selected} · ${priceOf(quote?.mid, selected)}`}
            >
              <Stack gap={10}>
                <Grid cols={5} gap={8}>
                  <StatCard label={t("panel.account.balance")} value={money(account?.balance ?? state.initialBalance)} />
                  <StatCard label={t("panel.account.equity")} value={money(metrics?.equity ?? state.initialBalance)} />
                  <StatCard
                    label={t("panel.account.unrealized")}
                    value={signed(metrics?.unrealized ?? 0)}
                  />
                  <StatCard label={t("panel.account.freeMargin")} value={money(metrics?.freeMargin ?? state.initialBalance)} />
                  <StatCard
                    label={t("panel.account.marginLevel")}
                    value={metrics?.marginLevel === null || metrics?.marginLevel === undefined ? "—" : `${metrics.marginLevel.toFixed(1)}%`}
                  />
                </Grid>

                <Inline gap={4}>
                  {PERIODS.map((value) => (
                    <Button
                      key={value}
                      tone={period === value ? "primary" : "default"}
                      onClick={() => { setPeriod(value) }}
                    >
                      {value === 60 ? "1小时" : `${value}分`}
                    </Button>
                  ))}
                  <Button tone={showEma ? "primary" : "default"} onClick={() => { setShowEma(!showEma) }}>
                    {t("panel.chart.ema")}
                  </Button>
                </Inline>

                <div className="moneta-chart" ref={(node) => { canvasRef.current = { current: node } }}>
                  <canvas ref={(node) => { canvasRef.current = { current: node } }} />
                </div>

                <Inline gap={8}>
                  <Text>{clockOf(market?.time ?? Date.now())} UTC</Text>
                  <Text>{t("panel.notice.simulation")}</Text>
                </Inline>
              </Stack>
            </Card>

            {/* 持仓 / 挂单 / 历史 */}
            <Card>
              <Tabs
                id="moneta-positions"
                activeId={activeSubTab}
                onChange={(id) => { setTab(id as TabId) }}
                items={[
                  { id: "positions", label: `${t("panel.tab.positions")} ${positions.length}` },
                  { id: "pending", label: `${t("panel.tab.pending")} ${pending.length}` },
                  { id: "history", label: t("panel.tab.history") },
                ]}
              />
              {activeSubTab === "positions" && (
                <DataTable
                  data={positions}
                  rowKey="id"
                  emptyText={t("panel.empty.positions")}
                  columns={[
                    { key: "symbol", label: "货币对" },
                    {
                      key: "side",
                      label: "方向 / 手数",
                      render: (row: Position) => (
                        <StatusBadge tone={row.side === "buy" ? "success" : "danger"}>
                          {row.side === "buy" ? "买入" : "卖出"} {row.lots.toFixed(2)}
                        </StatusBadge>
                      ),
                    },
                    { key: "entry", label: "开仓价", render: (row: Position) => priceOf(row.entry, row.symbol) },
                    {
                      key: "lastPnl",
                      label: "浮动盈亏",
                      render: (row: Position) => (
                        <span className={row.lastPnl >= 0 ? "moneta-up" : "moneta-down"}>
                          {signed(row.lastPnl ?? 0)}
                        </span>
                      ),
                    },
                    {
                      key: "protection",
                      label: "止损 / 止盈",
                      render: (row: Position) => `${priceOf(row.sl, row.symbol)} / ${priceOf(row.tp, row.symbol)}`,
                    },
                    {
                      key: "actions",
                      label: "操作",
                      render: (row: Position) => (
                        <Inline gap={4}>
                          <ModifyDialog position={row} onSave={modifyPosition} symbol={row.symbol} />
                          <Button tone="danger" disabled={busy} onClick={() => closePosition(row.id)}>
                            {t("panel.action.close")}
                          </Button>
                        </Inline>
                      ),
                    },
                  ]}
                />
              )}
              {activeSubTab === "pending" && (
                <DataTable
                  data={pending}
                  rowKey="id"
                  emptyText={t("panel.empty.pending")}
                  columns={[
                    { key: "symbol", label: "货币对" },
                    { key: "type", label: "类型", render: (row: PendingOrder) => (row.type === "limit" ? "限价单" : "突破单") },
                    { key: "side", label: "方向", render: (row: PendingOrder) => (row.side === "buy" ? "买入" : "卖出") },
                    { key: "lots", label: "手数", render: (row: PendingOrder) => row.lots.toFixed(2) },
                    { key: "price", label: "挂单价", render: (row: PendingOrder) => priceOf(row.price, row.symbol) },
                    {
                      key: "actions",
                      label: "操作",
                      render: (row: PendingOrder) => (
                        <Button tone="warning" disabled={busy} onClick={() => cancelOrder(row.id)}>
                          {t("panel.action.cancel")}
                        </Button>
                      ),
                    },
                  ]}
                />
              )}
              {activeSubTab === "history" && (
                <DataTable
                  data={[...history].reverse().slice(0, 100)}
                  rowKey="id"
                  emptyText={t("panel.empty.history")}
                  columns={[
                    { key: "symbol", label: "货币对" },
                    { key: "side", label: "方向 / 手数", render: (row: HistoryTrade) => `${row.side === "buy" ? "买入" : "卖出"} ${row.lots.toFixed(2)}` },
                    { key: "entry", label: "开仓价", render: (row: HistoryTrade) => priceOf(row.entry, row.symbol) },
                    { key: "exit", label: "平仓价", render: (row: HistoryTrade) => priceOf(row.exit, row.symbol) },
                    {
                      key: "netPnl",
                      label: "净盈亏",
                      render: (row: HistoryTrade) => (
                        <span className={row.netPnl >= 0 ? "moneta-up" : "moneta-down"}>{signed(row.netPnl)}</span>
                      ),
                    },
                    { key: "reason", label: "离场原因" },
                    { key: "closedAt", label: "平仓时间 UTC", render: (row: HistoryTrade) => clockOf(row.closedAt) },
                  ]}
                />
              )}
            </Card>
          </Section>

          {/* 下单面板 */}
          <Section className="moneta-order">
            <Card title={t("panel.order.title")}>
              <Stack gap={10}>
                <Inline gap={4}>
                  {(["market", "limit", "stop"] as const).map((type) => (
                    <Button
                      key={type}
                      tone={orderType === type ? "primary" : "default"}
                      onClick={() => { setOrderType(type) }}
                    >
                      {t(`panel.order.${type}`)}
                    </Button>
                  ))}
                </Inline>

                <Grid cols={2} gap={8}>
                  <button
                    type="button"
                    className={`moneta-quote-card moneta-sell ${side === "sell" ? "is-active" : ""}`}
                    onClick={() => { setSide("sell") }}
                  >
                    <span>卖出 SELL</span>
                    <strong>{priceOf(quote?.bid, selected)}</strong>
                  </button>
                  <button
                    type="button"
                    className={`moneta-quote-card moneta-buy ${side === "buy" ? "is-active" : ""}`}
                    onClick={() => { setSide("buy") }}
                  >
                    <span>买入 BUY</span>
                    <strong>{priceOf(quote?.ask, selected)}</strong>
                  </button>
                </Grid>
                <Text>
                  {t("panel.order.spreadLabel")}:{" "}
                  {quote ? ((quote.ask - quote.bid) / pipSize(selected)).toFixed(1) : "—"}
                </Text>

                <Field label={`${t("panel.order.lots")} · ${Number.isFinite(lotsValue) ? (lotsValue * 100000).toLocaleString() : "—"} ${t("panel.order.units")}`}>
                  <Input value={lots} onChange={(value) => { setLots(value) }} type="number" />
                </Field>
                <Inline gap={4}>
                  {["0.01", "0.10", "0.50", "1.00"].map((preset) => (
                    <Button key={preset} tone={lots === preset ? "primary" : "default"} onClick={() => { setLots(preset) }}>
                      {preset}
                    </Button>
                  ))}
                </Inline>

                {orderType !== "market" && (
                  <Field label={t("panel.order.price")}>
                    <Input value={entryPrice} onChange={(value) => { setEntryPrice(value) }} type="number" />
                  </Field>
                )}

                <Inline gap={8}>
                  <Field label={`${t("panel.order.sl")} SL`}>
                    <Input value={sl} onChange={(value) => { setSl(value) }} type="number" />
                  </Field>
                  <Field label={`${t("panel.order.tp")} TP`}>
                    <Input value={tp} onChange={(value) => { setTp(value) }} type="number" />
                  </Field>
                </Inline>
                <Button onClick={autoProtection}>{t("panel.order.autoProtection")}</Button>

                <Divider />
                <KeyValue
                  items={[
                    { label: t("panel.order.margin"), value: estimate ? `${money(estimate.margin)} USD` : "—" },
                    { label: t("panel.order.commission"), value: estimate ? `${money(estimate.commission)} USD` : "—" },
                    { label: t("panel.order.spread"), value: estimate ? `${money(estimate.spreadCost)} USD` : "—" },
                    {
                      label: t("panel.order.risk"),
                      value: estimate?.risk != null ? `−$${money(estimate.risk)}` : "—",
                    },
                    {
                      label: t("panel.order.tp"),
                      value: estimate?.reward != null ? `+$${money(estimate.reward)}` : "—",
                    },
                  ]}
                />

                <Button
                  tone={side === "buy" ? "success" : "danger"}
                  disabled={busy || !loggedIn || !lotsValid}
                  onClick={submitOrder}
                >
                  {side === "buy" ? t("panel.order.buy") : t("panel.order.sell")} {selected}
                </Button>
                {!loggedIn && <Alert tone="info" message={t("panel.toast.loginRequired")} />}
                <Text>{t("panel.notice.simulation")}</Text>

                {positions.length > 0 && (
                  <Button tone="warning" disabled={busy} onClick={closeAll}>
                    {t("panel.action.closeAll")}
                  </Button>
                )}
              </Stack>
            </Card>
          </Section>
        </Grid>
      )}

      {tab === "report" && (
        <Card title={t("panel.tab.report")}>
          <Stack gap={12}>
            <Button tone="primary" disabled={busy || !loggedIn} onClick={buildReport}>
              {t("actions.report.label")}
            </Button>
            {report ? (
              <>
                <Grid cols={4} gap={8}>
                  <StatCard
                    label={t("panel.report.netPnl")}
                    value={signed(report.netPnl ?? 0)}
                  />
                  <StatCard
                    label={t("panel.report.winRate")}
                    value={report.winRate == null ? "—" : `${report.winRate.toFixed(1)}%`}
                  />
                  <StatCard
                    label={t("panel.report.profitFactor")}
                    value={report.profitFactor == null ? "—" : report.profitFactor.toFixed(2)}
                  />
                  <StatCard label={t("panel.report.drawdown")} value={`${(report.maxDrawdown ?? 0).toFixed(2)}%`} />
                </Grid>
                <EquityCurve points={report.curve ?? []} />
                <Text>
                  {report.trades} 条已平仓记录 · 当前净值 {money(report.equity)} USD · 总回报{" "}
                  {(((report.equity ?? 0) / (report.initialBalance ?? 1) - 1) * 100).toFixed(2)}%
                </Text>
              </>
            ) : (
              <EmptyState title={t("panel.tab.report")} description={t("panel.empty.historyHint")} />
            )}
          </Stack>
        </Card>
      )}

      {tab === "journal" && (
        <Card title={t("panel.tab.journal")}>
          <List
            items={[...(account?.journal ?? [])].reverse().slice(0, 100)}
            render={(entry: { time: number; message: string; kind: string }) => (
              <div className={`moneta-journal moneta-journal-${entry.kind}`}>
                <time>{clockOf(entry.time)}</time>
                <span>{entry.message}</span>
              </div>
            )}
          />
        </Card>
      )}

      {/* 登录 / 注册 */}
      <Modal
        open={authOpen}
        title={authMode === "developer" ? t("panel.auth.developerAction") : authMode === "register" ? t("panel.auth.register") : t("panel.auth.login")}
        onClose={() => setAuthOpen(false)}
      >
        <Stack gap={10}>
          {authMode !== "developer" && (
            <>
              <Field label={t("panel.auth.username")}>
                <Input value={username} onChange={(value) => { setUsername(value) }} />
              </Field>
              <Field label={t("panel.auth.password")}>
                <Input value={password} onChange={(value) => { setPassword(value) }} type="password" />
              </Field>
            </>
          )}
          {authMode === "developer" && (
            <Field label={t("panel.auth.developerKey")}>
              <Input value={developerKey} onChange={(value) => { setDeveloperKey(value) }} type="password" />
            </Field>
          )}
          <Inline gap={4}>
            <Button tone="primary" disabled={busy} onClick={submitAuth}>
              {authMode === "developer"
                ? t("panel.auth.developerAction")
                : authMode === "register"
                  ? t("panel.auth.registerAction")
                  : t("panel.auth.loginAction")}
            </Button>
            {authMode !== "register" && (
              <Button onClick={() => { setAuthMode("register") }}>{t("panel.auth.register")}</Button>
            )}
            {authMode !== "login" && (
              <Button onClick={() => { setAuthMode("login") }}>{t("panel.auth.login")}</Button>
            )}
            {authMode !== "developer" && (
              <Button onClick={() => { setAuthMode("developer") }}>{t("panel.auth.developerAction")}</Button>
            )}
          </Inline>
          <Text>{authMode === "register" ? t("panel.auth.registerIntro") : t("panel.auth.intro")}</Text>
        </Stack>
      </Modal>

      {/* 行情控制台 */}
      <MarketConsole
        open={controlOpen}
        onClose={() => setControlOpen(false)}
        snapshot={snapshot}
        selected={selected}
        onRun={runControl}
        busy={busy}
        t={t}
      />

      {/* 设置 */}
      <SettingsDialog
        open={settingsOpen}
        onClose={() => setSettingsOpen(false)}
        settings={account?.settings}
        onSave={saveSettings}
        busy={busy}
        t={t}
        readOnly
      />
    </Page>
  )
}

// ── 修改止损止盈 ────────────────────────────────────────────────────────
function ModifyDialog(props: {
  position: Position
  symbol: string
  onSave: (id: string, sl?: number, tp?: number) => void
}) {
  const [open, setOpen] = useState(false)
  const [sl, setSl] = useState("")
  const [tp, setTp] = useState("")
  return (
    <>
      <Button onClick={() => { setOpen(true) }}>{`修改`}</Button>
      <Modal open={open} title={`风险控制 · ${props.position.symbol}`} onClose={() => setOpen(false)}>
        <Stack gap={10}>
          <Text>
            当前{props.position.side === "buy" ? "多" : "空"}单 {props.position.lots.toFixed(2)} 手，开仓{" "}
            {priceOf(props.position.entry, props.symbol)}。
          </Text>
          <Field label="止损">
            <Input value={sl} onChange={(value) => { setSl(value) }} type="number" />
          </Field>
          <Field label="止盈">
            <Input value={tp} onChange={(value) => { setTp(value) }} type="number" />
          </Field>
          <Button
            tone="primary"
            onClick={() => {
              props.onSave(props.position.id, sl ? Number(sl) : undefined, tp ? Number(tp) : undefined)
              setOpen(false)
            }}
          >
            保存止损止盈
          </Button>
        </Stack>
      </Modal>
    </>
  )
}

// ── 行情控制台 ─────────────────────────────────────────────────────────
function MarketConsole(props: {
  open: boolean
  onClose: () => void
  snapshot: Snapshot | null
  selected: string
  onRun: (body: Record<string, any>) => void
  busy: boolean
  t: (key: string, params?: any) => string
}) {
  const { t } = props
  const [price, setPrice] = useState("")
  const [currency, setCurrency] = useState("EUR")
  const [percent, setPercent] = useState("2")
  const [seconds, setSeconds] = useState("60")
  const [speed, setSpeed] = useState("1")
  const [paused, setPaused] = useState(false)
  const [volatility, setVolatility] = useState("1")

  const admin = props.snapshot?.admin
  const quote = props.snapshot?.book?.quotes?.[props.selected]

  return (
    <Modal open={props.open} title={t("panel.control.title")} onClose={props.onClose} size="lg">
      <Stack gap={14}>
        <Alert tone="warning" message={t("panel.control.developerOnly")} />
        <Grid cols={2} gap={12}>
          <Card title={`${t("panel.control.price")} · ${props.selected}`}>
            <Stack gap={8}>
              <Field label={t("panel.control.targetPrice")}>
                <Input
                  value={price}
                  onChange={(value) => { setPrice(value) }}
                  type="number"
                  placeholder={quote ? quote.mid.toFixed(5) : "1.10000"}
                />
              </Field>
              <Button
                tone="primary"
                disabled={props.busy}
                onClick={() => props.onRun({ kind: "price", symbol: props.selected, price: Number(price) })}
              >
                {t("panel.control.apply")}
              </Button>
            </Stack>
          </Card>

          <Card title={t("panel.control.trend")}>
            <Stack gap={8}>
              <Field label={t("panel.control.currency")}>
                <Select
                  value={currency}
                  onChange={(value) => { setCurrency(value) }}
                  options={["EUR", "GBP", "JPY", "CHF", "AUD", "CAD", "NZD", "CNY"].map((c) => ({ value: c, label: c }))}
                />
              </Field>
              <Field label={t("panel.control.percent")}>
                <Input value={percent} onChange={(value) => { setPercent(value) }} type="number" />
              </Field>
              <Field label={t("panel.control.seconds")}>
                <Input value={seconds} onChange={(value) => { setSeconds(value) }} type="number" />
              </Field>
              <Button
                tone="primary"
                disabled={props.busy}
                onClick={() =>
                  props.onRun({ kind: "trend", currency, percent: Number(percent), seconds: Number(seconds) })
                }
              >
                {t("panel.control.apply")}
              </Button>
            </Stack>
          </Card>

          <Card title={t("panel.control.clock")}>
            <Stack gap={8}>
              <Field label={t("panel.control.speed")}>
                <Select
                  value={speed}
                  onChange={(value) => { setSpeed(value) }}
                  options={SPEEDS.map((v) => ({ value: String(v), label: `${v}×` }))}
                />
              </Field>
              <Checkbox checked={paused} onChange={(value) => { setPaused(value) }} label={t("panel.market.pause")} />
              <Button
                disabled={props.busy}
                onClick={() => props.onRun({ kind: "clock", speed: Number(speed), paused })}
              >
                {t("panel.control.apply")}
              </Button>
              <Field label={t("panel.control.volatility")}>
                <Input value={volatility} onChange={(value) => { setVolatility(value) }} type="number" />
              </Field>
              <Button
                disabled={props.busy}
                onClick={() => props.onRun({ kind: "volatility", value: Number(volatility) })}
              >
                {t("panel.control.apply")}
              </Button>
            </Stack>
          </Card>

          <Card title={t("panel.control.event")}>
            <Stack gap={8}>
              {EVENTS.map((event) => (
                <Button key={event} disabled={props.busy} onClick={() => props.onRun({ kind: "event", event })}>
                  {t(`panel.control.event${event[0].toUpperCase()}${event.slice(1)}`)}
                </Button>
              ))}
            </Stack>
          </Card>
        </Grid>

        <Card title={t("panel.control.plans")}>
          {(admin?.plans ?? []).length ? (
            <List
              items={admin?.plans ?? []}
              render={(plan: any) => (
                <KeyValue
                  items={[
                    { label: plan.currency, value: `目标 ${plan.target.toFixed(6)} · 剩余 ${Math.ceil(plan.remaining)} 模拟秒` },
                  ]}
                />
              )}
            />
          ) : (
            <Text>当前没有未来走势编排，市场自然运行。</Text>
          )}
          <Button tone="warning" disabled={props.busy} onClick={() => props.onRun({ kind: "cancel-trends" })}>
            {t("panel.control.cancelTrends")}
          </Button>
        </Card>

        <Card title={t("panel.control.audit")}>
          <List
            items={[...(admin?.audit ?? [])].reverse().slice(0, 20)}
            render={(entry: any) => (
              <div className="moneta-journal">
                <time>{new Date(entry.realTime).toLocaleTimeString("zh-CN", { hour12: false })}</time>
                <span>{entry.message}</span>
              </div>
            )}
          />
        </Card>
      </Stack>
    </Modal>
  )
}

// ── 设置 ───────────────────────────────────────────────────────────────
function SettingsDialog(props: {
  open: boolean
  onClose: () => void
  settings?: Account["settings"]
  onSave: (next: Record<string, number>) => void
  busy: boolean
  t: (key: string, params?: any) => string
  readOnly?: boolean
}) {
  const { t } = props
  const [values, setValues] = useState({
    leverage: props.settings?.leverage ?? 100,
    spreadMultiplier: props.settings?.spreadMultiplier ?? 1,
    slippage: props.settings?.slippage ?? 0.3,
    commission: props.settings?.commission ?? 3,
    swapLong: props.settings?.swapLong ?? -6,
    swapShort: props.settings?.swapShort ?? 1.5,
  })

  return (
    <Modal open={props.open} title={t("panel.settings.title")} onClose={props.onClose}>
      <Stack gap={12}>
        {props.readOnly && <Alert tone="info" message={t("panel.settings.readOnly")} />}
        <Field label={t("panel.settings.leverage")}>
          <Select
            value={String(values.leverage)}
            onChange={(value) => { setValues({ ...values, leverage: Number(value) }) }}
            options={[10, 20, 30, 50, 100, 200, 500].map((v) => ({ value: String(v), label: `1:${v}` }))}
          />
        </Field>
        <Field label={t("panel.settings.spread")}>
          <NumberInput
            value={values.spreadMultiplier}
            min={0.5}
            max={10}
            step={0.1}
            onChange={(value) => { setValues({ ...values, spreadMultiplier: Number(value) }) }}
          />
        </Field>
        <Field label={t("panel.settings.slippage")}>
          <NumberInput
            value={values.slippage}
            min={0}
            max={20}
            step={0.1}
            onChange={(value) => { setValues({ ...values, slippage: Number(value) }) }}
          />
        </Field>
        <Field label={t("panel.settings.commission")}>
          <NumberInput
            value={values.commission}
            min={0}
            max={100}
            step={0.1}
            onChange={(value) => { setValues({ ...values, commission: Number(value) }) }}
          />
        </Field>
        <Field label={t("panel.settings.swapLong")}>
          <NumberInput
            value={values.swapLong}
            min={-100}
            max={100}
            step={0.1}
            onChange={(value) => { setValues({ ...values, swapLong: Number(value) }) }}
          />
        </Field>
        <Field label={t("panel.settings.swapShort")}>
          <NumberInput
            value={values.swapShort}
            min={-100}
            max={100}
            step={0.1}
            onChange={(value) => { setValues({ ...values, swapShort: Number(value) }) }}
          />
        </Field>
        <Button tone="primary" disabled={props.busy} onClick={() => props.onSave(values)}>
          {t("panel.settings.save")}
        </Button>
      </Stack>
    </Modal>
  )
}

// ── 净值曲线 ───────────────────────────────────────────────────────────
function EquityCurve(props: { points: Array<{ time: number; value: number }> }) {
  const points = props.points
  if (points.length < 2) return <Text>—</Text>
  const min = Math.min(...points.map((p) => p.value)) - 10
  const max = Math.max(...points.map((p) => p.value)) + 10
  const path = points
    .map(
      (point, index) =>
        `${index ? "L" : "M"}${(15 + (index / Math.max(1, points.length - 1)) * 690).toFixed(2)},${(
          140 -
          ((point.value - min) / (max - min)) * 120
        ).toFixed(2)}`,
    )
    .join(" ")
  return (
    <svg className="moneta-equity" viewBox="0 0 720 160" preserveAspectRatio="none">
      <path d="M15 30H705M15 80H705M15 140H705" stroke="#233342" />
      <path d={path} fill="none" stroke="#60dcb4" strokeWidth={2} />
    </svg>
  )
}

// ── K 线图（Canvas） ───────────────────────────────────────────────────
class Chart {
  private canvas: any
  private ctx: any
  private symbol: string
  private bars: Bar[] = []
  private quote?: Quote
  private positions: Position[] = []
  private ema = true
  private width = 0
  private height = 0

  constructor(canvas: any, symbol: string) {
    this.canvas = canvas
    this.symbol = symbol
    this.ctx = canvas.getContext("2d")
  }

  resize() {
    const parent = this.canvas.parentElement
    if (!parent) return
    const rect = parent.getBoundingClientRect()
    this.width = rect.width
    this.height = 260
    const dpr = window.devicePixelRatio || 1
    this.canvas.width = this.width * dpr
    this.canvas.height = this.height * dpr
    this.canvas.style.width = `${this.width}px`
    this.canvas.style.height = `${this.height}px`
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
    this.draw()
  }

  update(bars: Bar[], quote: Quote | undefined, positions: Position[], ema: boolean) {
    this.bars = bars
    this.quote = quote
    this.positions = positions
    this.ema = ema
    this.draw()
  }

  private aggregate(bars: Bar[], minutes: number): Bar[] {
    if (minutes <= 1) return bars
    const interval = minutes * 60000
    const result: Bar[] = []
    for (const bar of bars) {
      const slot = Math.floor(bar.time / interval) * interval
      const last = result[result.length - 1]
      if (last && last.time === slot) {
        last.high = Math.max(last.high, bar.high)
        last.low = Math.min(last.low, bar.low)
        last.close = bar.close
      } else {
        result.push({ ...bar, time: slot })
      }
    }
    return result
  }

  private draw() {
    const ctx = this.ctx
    if (!ctx || !this.width || !this.height) return
    const bars = this.aggregate(this.bars, 1).slice(-120)
    ctx.clearRect(0, 0, this.width, this.height)
    ctx.fillStyle = "#0d141c"
    ctx.fillRect(0, 0, this.width, this.height)
    if (!bars.length) return

    const right = this.width - 72
    const top = 16
    const bottom = this.height - 24
    let min = Math.min(...bars.map((b) => b.low))
    let max = Math.max(...bars.map((b) => b.high))
    if (this.quote) {
      min = Math.min(min, this.quote.mid)
      max = Math.max(max, this.quote.mid)
    }
    const padding = Math.max((max - min) * 0.14, max * 0.00012)
    min -= padding
    max += padding
    const y = (value: number) => top + ((max - value) / (max - min)) * (bottom - top)
    const step = (right - 18) / Math.max(bars.length + 3, 24)
    const x = (index: number) => 18 + (index + 0.5) * step

    ctx.font = "9px system-ui, sans-serif"
    ctx.lineWidth = 1
    for (let i = 0; i <= 5; i += 1) {
      const py = top + ((bottom - top) * i) / 5
      ctx.strokeStyle = "#1b2631"
      ctx.beginPath()
      ctx.moveTo(0, Math.round(py) + 0.5)
      ctx.lineTo(right, Math.round(py) + 0.5)
      ctx.stroke()
      ctx.fillStyle = "#5d768d"
      ctx.fillText((max - ((max - min) * i) / 5).toFixed(this.symbol.endsWith("/JPY") ? 3 : 5), right + 9, py + 3)
    }

    bars.forEach((bar, index) => {
      const px = x(index)
      const green = bar.close >= bar.open
      ctx.strokeStyle = ctx.fillStyle = green ? "#5dd8b2" : "#e78481"
      ctx.beginPath()
      ctx.moveTo(px, y(bar.high))
      ctx.lineTo(px, y(bar.low))
      ctx.stroke()
      const upper = Math.min(y(bar.open), y(bar.close))
      ctx.fillRect(px - Math.max(1, step * 0.31), upper, Math.max(2, step * 0.62), Math.max(1.1, Math.abs(y(bar.open) - y(bar.close))))
    })

    if (this.ema) {
      for (const [period, color] of [[20, "#d5b276"], [60, "#8993bf"]] as Array<[number, string]>) {
        let value = bars[0].close
        const alpha = 2 / (period + 1)
        ctx.strokeStyle = color
        ctx.lineWidth = 1.2
        ctx.beginPath()
        bars.forEach((bar, index) => {
          value = bar.close * alpha + value * (1 - alpha)
          if (index === 0) ctx.moveTo(x(index), y(value))
          else ctx.lineTo(x(index), y(value))
        })
        ctx.stroke()
      }
    }

    for (const position of this.positions.filter((p) => p.symbol === this.symbol)) {
      const lines: Array<[number | undefined, string, string]> = [
        [position.entry, `${position.side === "buy" ? "BUY" : "SELL"} ${position.lots.toFixed(2)}`, "#8aaac2"],
        [position.sl, "SL", "#ef827f"],
        [position.tp, "TP", "#60dcb4"],
      ]
      for (const [value, label, color] of lines) {
        if (!value || value < min || value > max) continue
        ctx.setLineDash([4, 5])
        ctx.strokeStyle = color
        ctx.beginPath()
        ctx.moveTo(8, y(value))
        ctx.lineTo(right, y(value))
        ctx.stroke()
        ctx.fillStyle = "#182632"
        ctx.fillRect(9, y(value) - 8, 72, 15)
        ctx.fillStyle = color
        ctx.fillText(label, 13, y(value) + 3)
      }
      ctx.setLineDash([])
    }

    if (this.quote) {
      const py = y(this.quote.mid)
      ctx.setLineDash([3, 4])
      ctx.strokeStyle = "rgba(93,204,170,0.47)"
      ctx.beginPath()
      ctx.moveTo(0, py)
      ctx.lineTo(right, py)
      ctx.stroke()
      ctx.setLineDash([])
      ctx.fillStyle = "#5dd8b2"
      ctx.fillRect(right, py - 10, 70, 20)
      ctx.fillStyle = "#0e2b22"
      ctx.font = "10px system-ui, sans-serif"
      ctx.fillText(this.quote.mid.toFixed(this.symbol.endsWith("/JPY") ? 3 : 5), right + 7, py + 4)
    }
  }
}
