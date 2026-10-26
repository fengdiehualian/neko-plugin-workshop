import { useI18n, Alert, Button, Card, EmptyState, Grid, Stack, StatCard, StatusBadge, TextBlock, Toolbar, ToolbarGroup } from "@neko/plugin-ui"
import type { DiagnosticsController } from "./useDiagnostics"
import { UsageSummary } from "../components/usage-summary"
import { FailureNotice } from "../components/failure-notice"
import { progressText, statusTone, text, timing } from "../format"

export function ExecutionOverview({ c }: { c: DiagnosticsController }) {
  const { t } = useI18n()
  const state = c.state
  const latest = c.latestRun
  const incident = state.last_incident || state.panel_error
  return <Stack>
    <Grid cols={4}>
      <StatCard label={t("debug.gameConnection")}  value={state.connection ? t("debug.authenticated") : t("debug.notConnected")} />
      <StatCard label={t("debug.gameSession")}  value={state.session ? state.session.stale ? t("debug.expired") : t("debug.valid") : t("debug.notLoaded")} />
      <StatCard label={t("debug.modelConfig")}  value={state.configuration?.agent_configured ? t("debug.configured") : t("debug.notConfigured")} />
      <StatCard label={t("debug.activeConsultations")}  value={state.agent?.active_count ?? 0} />
    </Grid>
    {!state.configuration?.token_configured || !state.configuration?.agent_configured ? <Alert tone="warning">{t("debug.setupWarning")}</Alert> : null}
    <Card title={t("debug.latestUsage")} ><UsageSummary run={latest} history={state.agent} /></Card>
    <Card title={t("debug.latestResult")} >
      {latest ? <Stack>
        <TextBlock text={text(latest.question, t("debug.consultation"), 300)} />
        <StatusBadge tone={statusTone(text(latest.result_status || latest.state))} label={text(latest.result_status || latest.state)} />
        {!latest.terminal ? <Alert tone="info">{progressText(latest, t)} · {timing(latest.duration_ms, t)}</Alert> : null}
        {latest.result_status && latest.result_status !== "ok" ? <FailureNotice summary={latest.failure_summary} compact /> : null}
        <TextBlock text={text(latest.result_summary, t("debug.noHandoff"), 1000)} />
        <Button onClick={() => c.openRun(latest.run_id!)}>{t("debug.viewRun")}</Button>
      </Stack> : <EmptyState title={t("debug.noRunHistory")}  description={t("debug.noRunHistoryHelp")}  />}
    </Card>
    {incident ? <Card title={t("debug.latestIncident")} ><Stack><TextBlock text={text(incident.message, t("debug.runtimeIssue"), 500)} /><Button onClick={() => { c.setSessionMode("connection"); c.setDiagnosticMode("records") }}>{t("debug.viewConnectionEvents")}</Button></Stack></Card> : null}
    <Toolbar><ToolbarGroup><Button tone="primary" onClick={() => { c.setDiagnosticMode("test") }}>{t("debug.enterTest")}</Button><Button onClick={() => { c.setPage("settings") }}>{t("debug.openSettings")}</Button></ToolbarGroup></Toolbar>
  </Stack>
}
