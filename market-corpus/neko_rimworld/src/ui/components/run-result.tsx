import { useI18n, Alert, Button, CodeBlock, EmptyState, Field, KeyValue, Stack, StatusBadge, TextBlock } from "@neko/plugin-ui"
import { statusTone, text } from "../format"
import { FailureNotice } from "./failure-notice"

export function RunResult({ result, draft, failureSummary, onOpenReport }: { result?: Record<string, any> | null; draft?: Record<string, any> | null; failureSummary?: Record<string, any> | null; onOpenReport?: (id: string) => void }) {
  const { t } = useI18n()
  if (!result && draft?.full_text) return <Stack><Alert tone="warning">{t("debug.draftRetained")}</Alert><TextBlock text={text(draft.full_text, "", 12000)} /></Stack>
  if (!result) return <EmptyState title={t("debug.noResult")}  description={t("debug.noResultHelp")}  />
  const status = text(result.status, "unknown")
  return <Stack>
    <StatusBadge tone={statusTone(status)} label={status} />
    {status !== "ok" ? <Alert tone="warning">{t("debug.incompleteResult")}</Alert> : null}
    {status !== "ok" ? <FailureNotice summary={failureSummary} observationCount={Array.isArray(result.observations) ? result.observations.length : 0} /> : null}
    {result.report?.saved ? <Stack><Field label={t("debug.reportId")} ><CodeBlock>{result.report.report_id}</CodeBlock></Field>{onOpenReport ? <Button onClick={() => onOpenReport(result.report.report_id)}>{t("debug.openFullReport")}</Button> : null}</Stack> : result.report?.saved === false ? <Alert tone="warning">{t("debug.reportSaveWarning")}</Alert> : null}
    <Field label={t("debug.fullAnswer")} ><TextBlock text={text(result.answer, t("debug.noFullAnswer"), 12000)} /></Field>
    <Field label={t("debug.shortAnswer")} ><TextBlock text={text(result.summary, t("debug.noShortAnswer"), 4000)} /></Field>
    {Array.isArray(result.caveats) && result.caveats.length ? <TextBlock text={result.caveats.map((x: any) => text(x)).join("\n")} /> : null}
    <KeyValue items={[
      { key: "mode", label: t("debug.submissionMode"), value: text(result.delivery?.submission_mode) },
      { key: "full", label: t("debug.answerTokens"), value: text(result.delivery?.full_answer_tokens) },
      { key: "handoff", label: t("debug.handoffTokens"), value: `${text(result.delivery?.handoff_tokens)} / ${text(result.delivery?.transport_token_budget)}` },
    ]} />
  </Stack>
}
