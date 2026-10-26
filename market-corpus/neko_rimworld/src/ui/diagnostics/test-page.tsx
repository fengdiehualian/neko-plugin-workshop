import { useI18n, Alert, Button, Card, Field, Grid, InlineError, KeyValue, Stack, StatusBadge, Textarea, TextBlock } from "@neko/plugin-ui"
import type { DiagnosticsController } from "./useDiagnostics"
import { statusTone, text } from "../format"

export function IndependentTestPage({ c }: { c: DiagnosticsController }) {
  const { t } = useI18n()
  return <Grid cols={2}>
    <Card title={t("debug.testTitle")} ><Stack>
      {c.referenceReportId ? <Stack><Alert tone="info">{t("debug.reportReference", { id: c.referenceReportId })}</Alert><Button disabled={c.running} onClick={() => { c.setReferenceReportId("") }}>{t("debug.cancelReference")}</Button></Stack> : null}
      <Field label={t("debug.question")}  help={t("debug.testHelp")}  required>
        <Textarea value={c.question} onChange={c.setQuestion} placeholder={t("debug.sampleQuestion")}  />
      </Field>
      {c.testError ? <InlineError title={t("debug.testFailed")}  error={c.testError} /> : null}
      <Button tone="primary" disabled={c.running || !c.question.trim() || !c.state.configuration?.agent_configured} onClick={c.runTest}>{c.running ? t("debug.testRunning") : t("debug.runConsultation")}</Button>
      {c.running ? <Alert tone="info">{t("debug.testWait")}</Alert> : null}
      {c.testResult ? <Stack><StatusBadge tone={statusTone(text(c.testResult.status))} label={text(c.testResult.status)} /><TextBlock text={text(c.testResult.summary, t("debug.noDeliveredText"), 1500)} /></Stack> : null}
      {c.testResult?.report?.saved ? <Button onClick={() => c.openReport(c.testResult!.report.report_id)}>{t("debug.openFullReport")}</Button> : null}
      {c.testResult?.audit?.run_id ? <Button onClick={() => c.openRun(c.testResult!.audit.run_id)}>{t("debug.openRun")}</Button> : c.testError && c.latestRun?.run_id ? <Button onClick={() => c.openRun(c.latestRun!.run_id!)}>{t("debug.latestRun")}</Button> : null}
    </Stack></Card>
    <Card title={t("debug.connectionTest")} ><Stack>
      <TextBlock text={t("debug.connectionTestHelp")}  />
      <Button disabled={c.probing} onClick={c.probe}>{c.probing ? t("debug.probing") : t("debug.probe")}</Button>
      {c.probeError ? <InlineError title={t("debug.probeFailed")}  error={c.probeError} /> : null}
      {c.probeResult ? <KeyValue items={[
        { key: "status", label: t("debug.status"), value: text(c.probeResult.status) },
        { key: "summary", label: t("debug.description"), value: text(c.probeResult.summary) },
        { key: "tick", label: t("debug.gameTick"), value: text(c.probeResult.response?.game_tick) },
      ]} /> : null}
      <Button onClick={() => { c.setSessionMode("connection"); c.setDiagnosticMode("records") }}>{t("debug.connectionDetails")}</Button>
    </Stack></Card>
  </Grid>
}
