import { useI18n, Alert, Button, Card, CodeBlock, DataTable, EmptyState, Field, Grid, InlineError, JsonView, Markdown, useClipboard, Stack, StatusBadge, TextBlock, Toolbar, ToolbarGroup } from "@neko/plugin-ui"
import type { DiagnosticsController } from "../diagnostics/useDiagnostics"
import { text } from "../format"

export function ReportsView({ c }: { c: DiagnosticsController }) {
  const { t } = useI18n()
  const clipboard = useClipboard()
  const report = c.report
  return <Grid cols={2}>
    <Card title={t("debug.savedReports")} ><Stack>
      <TextBlock text={t("debug.reportRetention")}  />
      <Button disabled={c.reportLoading} onClick={c.loadReports}>{c.reportLoading ? t("debug.loading") : t("debug.refreshReports")}</Button>
      {c.reportError ? <InlineError title={t("debug.reportReadError")}  error={c.reportError} /> : null}
      <DataTable data={c.reportPage?.reports || []} rowKey="report_id" selectedKey={c.selectedReportId} onSelect={row => c.openReport(row.report_id)} maxRows={20} emptyText={t("debug.noReports")}  columns={[
        { key: "question", label: t("debug.question"), render: row => text(row.question, t("debug.consultation"), 130) },
        { key: "state", label: t("debug.status"), render: row => <StatusBadge tone={row.state === "complete" ? "success" : "warning"} label={row.state === "complete" ? t("debug.completed") : t("debug.draft")} /> },
        { key: "created_at", label: t("debug.time"), render: row => text(row.created_at) },
      ]} />
      <Toolbar><ToolbarGroup><Button disabled={c.reportOffset === 0 || c.reportLoading} onClick={() => { c.setReportOffset(Math.max(0, c.reportOffset - 20)) }}>{t("debug.previousReports")}</Button><Button disabled={c.reportPage?.next_offset == null || c.reportLoading} onClick={() => { c.setReportOffset(c.reportPage!.next_offset) }}>{t("debug.nextReports")}</Button></ToolbarGroup></Toolbar>
      <TextBlock text={t("debug.reportCount", { count: c.reportPage?.total ?? 0 })} />
    </Stack></Card>
    <Card title={t("debug.reportBody")} ><Stack>
      {report ? <Stack>
        <Alert tone={report.state === "complete" ? "info" : "warning"}>{report.state === "complete" ? t("debug.historicalReport") : t("debug.draftReport")}</Alert>
        <Field label={t("debug.reportId")} ><CodeBlock>{report.report_id}</CodeBlock></Field>
        <TextBlock text={`${text(report.created_at)} · ${text(report.question, t("debug.consultation"), 2000)}`} />
        <Field label={t("debug.fullText")} ><Markdown source={text(report.full_text, "", 12000)} /></Field>
        <Toolbar><ToolbarGroup><Button onClick={async () => { await clipboard.write(text(report.full_text, "", 12000)) }}>{t("debug.copyFullText")}</Button><Button onClick={async () => { await clipboard.write(report.report_id) }}>{t("debug.copyReportId")}</Button></ToolbarGroup></Toolbar>
        {clipboard.copied ? <TextBlock text={t("debug.copied")}  /> : null}
        {clipboard.error ? <InlineError title={t("debug.copyFailed")}  error={clipboard.error} /> : null}
        <Button tone="primary" disabled={c.running} onClick={() => c.askAboutReport(report.report_id)}>{t("debug.askReport")}</Button>
        {report.evidence_status === "omitted_size_limit" ? <Alert tone="warning">{t("debug.evidenceOmitted")}</Alert> : null}
        <TextBlock text={t("debug.reportReadings", { count: report.observation_count ?? 0 })} />
        {report.state === "draft" ? <JsonView data={report.draft_errors || {}} /> : null}
      </Stack> : <EmptyState title={c.reportLoading ? t("debug.loadingReport") : t("debug.chooseReport")} />}
    </Stack></Card>
  </Grid>
}
