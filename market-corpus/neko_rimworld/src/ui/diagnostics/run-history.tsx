import { useI18n } from "@neko/plugin-ui"
import { ReportsView } from "../components/reports-view"
import { NavigationTabs } from "../components/navigation-tabs"
import { Alert, Button, Card, DataTable, EmptyState, Field, Grid, InlineError, Input, JsonView, KeyValue, Stack, StatusBadge, TextBlock, Toolbar, ToolbarGroup } from "@neko/plugin-ui"
import type { DiagnosticsController } from "./useDiagnostics"
import { EventDetails } from "../components/event-details"
import { RunResult } from "../components/run-result"
import { UsageSummary } from "../components/usage-summary"
import { levelTone, progressText, shortId, statusTone, text, timing } from "../format"

export function RunHistoryView({ c }: { c: DiagnosticsController }) {
  const { t } = useI18n()
  const detail = c.detail
  const events = Array.isArray(detail?.events) ? detail.events : []
  const visibleEvents = events.filter(event => (!c.eventFilter || text(event.event).toLowerCase().includes(c.eventFilter.toLowerCase())) && (c.runView !== "model" || text(event.event).startsWith("model_")))
  const event = events.find(item => item.event_seq === c.selectedEvent)
  const connectionEvents = (c.state.diagnostics || []).slice().reverse().filter(item => (!c.connectionLevel || text(item.level).toLowerCase().includes(c.connectionLevel.toLowerCase())) && (!c.connectionCategory || text(item.category).toLowerCase().includes(c.connectionCategory.toLowerCase())))
  const pageInfo = detail?.events_page
  return <Stack>
    <NavigationTabs id="rimworld-session-mode" activeId={c.sessionMode} onChange={c.setSessionMode} items={[{ id: "runs", label: t("debug.runs") }, { id: "reports", label: t("debug.reports") }, { id: "connection", label: t("debug.connectionEvents") }]}>
      {c.sessionMode === "reports" ? <ReportsView c={c} /> : c.sessionMode === "connection" ? <Stack>
        <Grid cols={2}>
          <Card title={t("debug.currentSession")} >{c.state.session ? <KeyValue items={[
            { key: "colony", label: t("debug.colonyId"), value: text(c.state.session.colony_id) },
            { key: "load", label: t("debug.loadEpoch"), value: text(c.state.session.load_epoch) },
            { key: "tick", label: t("debug.gameTick"), value: text(c.state.session.game_tick) },
            { key: "sample", label: t("debug.sampleSequence"), value: text(c.state.session.snapshot_sequence) },
            { key: "stale", label: t("debug.staleSession"), value: c.state.session.stale ? t("debug.yes") : t("debug.no") },
          ]} /> : <EmptyState title={t("debug.noSession")}  />}</Card>
          <Card title={t("debug.protocol")} ><KeyValue items={[
            { key: "endpoint", label: t("debug.endpoint"), value: text(c.state.endpoint) },
            { key: "mod", label: t("debug.versions"), value: `${text(c.state.connection?.mod_version)} / ${text(c.state.connection?.game_version)}` },
            { key: "epoch", label: t("debug.connectionEpoch"), value: text(c.state.connection?.connection_epoch) },
            { key: "frames", label: t("debug.frames"), value: `${c.state.protocol?.rx_frames ?? 0} / ${c.state.protocol?.tx_frames ?? 0}` },
            { key: "failures", label: t("debug.authFailures"), value: `${c.state.protocol?.auth_failures ?? 0} / ${c.state.protocol?.invalid_frames ?? 0}` },
            { key: "pending", label: t("debug.pendingRequests"), value: `${c.state.metrics?.pending_requests ?? 0} / ${c.state.metrics?.outbound_depth ?? 0}` },
            { key: "last", label: t("debug.lastRead"), value: `${text(c.state.last_query?.status)} · ${timing(c.state.last_query?.latency_ms, t)}` },
          ]} /></Card>
        </Grid>
        <Card title={t("debug.connectionLog")} ><Stack>
          <Grid cols={2}><Field label={t("debug.levelFilter")} ><Input value={c.connectionLevel} onChange={c.setConnectionLevel} placeholder={t("debug.levelExample")}  /></Field><Field label={t("debug.categoryFilter")} ><Input value={c.connectionCategory} onChange={c.setConnectionCategory} /></Field></Grid>
          <DataTable data={connectionEvents} rowKey="sequence" maxRows={200} emptyText={t("debug.noConnectionEvents")}  columns={[
            { key: "timestamp", label: t("debug.time"), render: row => text(row.timestamp) },
            { key: "level", label: t("debug.level"), render: row => <StatusBadge tone={levelTone(text(row.level))} label={text(row.level)} /> },
            { key: "category", label: t("debug.category"), render: row => text(row.category) },
            { key: "message", label: t("debug.description"), render: row => text(row.message, "—", 500) },
          ]} />
        </Stack></Card>
      </Stack> : <Grid cols={2}>
        <Card title={t("debug.runHistory")} ><Stack>
          <Button tone={c.followLatest ? "primary" : "default"} onClick={() => { c.setFollowLatest(!c.followLatest) }}>{c.followLatest ? t("debug.followingLatest") : t("debug.followLatest")}</Button>
          <TextBlock text={t("debug.runRetention", { count: c.state.agent?.retained_run_limit ?? 20 })} />
          <DataTable data={c.runs} rowKey="run_id" selectedKey={c.selectedRunId} onSelect={row => c.selectRun(row.run_id!)} maxRows={30} emptyText={t("debug.noRuns")}  columns={[
            { key: "question", label: t("debug.question"), render: row => text(row.question, shortId(row.run_id), 130) },
            { key: "state", label: t("debug.status"), render: row => <StatusBadge tone={statusTone(text(row.result_status || row.state))} label={text(row.result_status || row.state)} /> },
            { key: "duration", label: t("debug.duration"), render: row => timing(row.duration_ms, t) },
          ]} />
        </Stack></Card>
        <Card title={t("debug.runDetails")} ><Stack>
          <Toolbar><ToolbarGroup><TextBlock text={c.archive ? t("debug.savedAudit") : shortId(c.selectedRunId)} /></ToolbarGroup><ToolbarGroup><Button disabled={(!c.selectedRunId && !c.archive) || c.detailLoading} onClick={c.loadDetail}>{c.detailLoading ? t("debug.loading") : t("debug.refreshDetails")}</Button></ToolbarGroup></Toolbar>
          {c.detailError ? <InlineError title={t("debug.detailsFailed")}  error={c.detailError} details={t("debug.detailsRetained")} /> : null}
          {detail ? <Stack>
            <TextBlock text={text(detail.question, t("debug.consultation"), 300)} />
            <UsageSummary run={detail} />
            <NavigationTabs id="rimworld-run-view" activeId={c.runView} onChange={id => c.setRunView(id as any)} items={[{ id: "result", label: t("debug.result") }, { id: "events", label: t("debug.events") }, { id: "observations", label: t("debug.observations") }, { id: "model", label: t("debug.modelRequests") }]}>
              {c.runView === "result" ? <RunResult result={detail.result} draft={detail.draft} failureSummary={detail.failure_summary} onOpenReport={c.openReport} /> : c.runView === "observations" ? (
                detail.result?.observations?.length ? <Stack><TextBlock text={t("debug.observationScope")}  /><JsonView data={detail.result.observations} /></Stack> : <EmptyState title={t("debug.noObservations")}  description={t("debug.noObservationsHelp")}  />
              ) : <Stack>
                <Field label={t("debug.eventFilter")} ><Input value={c.eventFilter} onChange={c.setEventFilter} placeholder={t("debug.eventExample")}  /></Field>
                <DataTable data={visibleEvents} rowKey="event_seq" selectedKey={c.selectedEvent} onSelect={row => c.setSelectedEvent(row.event_seq!)} maxRows={50} emptyText={t("debug.noEvents")}  columns={[
                  { key: "event_seq", label: "#" },
                  { key: "event", label: t("debug.events") },
                  { key: "elapsed_ms", label: t("debug.elapsed"), render: row => timing(row.elapsed_ms, t) },
                ]} />
                <Toolbar><ToolbarGroup><Button disabled={c.eventOffset === 0 || c.detailLoading} onClick={() => c.changeEventPage(c.eventOffset - 50)}>{t("debug.previous")}</Button><Button disabled={!pageInfo?.has_more || c.detailLoading} onClick={() => c.changeEventPage(c.eventOffset + 50)}>{t("debug.next")}</Button></ToolbarGroup></Toolbar>
                <TextBlock text={t("debug.eventPage", { from: pageInfo ? Math.min(pageInfo.offset + 1, pageInfo.total) : 0, to: pageInfo ? Math.min(pageInfo.offset + pageInfo.limit, pageInfo.total) : 0, total: pageInfo?.total ?? 0 })} />
                <EventDetails event={event} />
              </Stack>}
            </NavigationTabs>
          </Stack> : c.selectedSummary ? <Stack>
            <TextBlock text={text(c.selectedSummary.question, t("debug.consultation"), 300)} />
            <UsageSummary run={c.selectedSummary} />
            <StatusBadge tone={statusTone(text(c.selectedSummary.result_status || c.selectedSummary.state))} label={text(c.selectedSummary.result_status || c.selectedSummary.state)} />
            <KeyValue items={[
              { key: "progress", label: t("debug.progress"), value: progressText(c.selectedSummary, t) },
              { key: "cycle", label: t("debug.cycle"), value: text(c.selectedSummary.cycle) },
              { key: "revision", label: t("debug.revision"), value: text(c.selectedSummary.revision, text(c.selectedSummary.event_count)) },
              { key: "event", label: t("debug.latestEvent"), value: text(c.selectedSummary.latest_event?.event) },
            ]} />
            {!c.selectedSummary.terminal ? <TextBlock text={t("debug.lightweightProgress")}  /> : null}
          </Stack> : !c.detailError && !c.detailLoading ? <EmptyState title={t("debug.selectRun")}  /> : null}
        </Stack></Card>
      </Grid>}
    </NavigationTabs>
  </Stack>
}
