import { useI18n, Alert, Button, Card, EmptyState, InlineError, JsonView, Stack, StatusBadge, TextBlock } from "@neko/plugin-ui"
import type { WorkController } from "./useWorkView"
import { displayTime, savedReadings } from "./presentation"

export function ReadingRecords({ c, openAudit }: { c: WorkController; openAudit: (workspace: string, turn: string) => void }) {
  const { t, locale } = useI18n()
  const exchange = c.exchange
  return <Card title={t("debug.selectedReadings")} ><Stack>
    <TextBlock text={t("debug.savedReadingsHelp")}  />
    {c.error ? <InlineError title={t("debug.readingsError")}  error={c.error} /> : null}
    {!exchange ? <EmptyState title={t("debug.noDiscussion")}  description={t("debug.chooseDiscussionHelp")}  /> : <Stack>
      <TextBlock text={exchange.question} />
      <TextBlock text={t("debug.discussionIds", { workspace: exchange.workspace_id, turn: exchange.turn_id })} />
      {!exchange.history_saved ? <Alert tone="warning">{t("debug.historySaveUnknown")}</Alert> : null}
      {savedReadings(exchange, t).map((source, index) => <div key={source.observation_id} style={{ padding: "8px 0" }}>
        <Button disabled={c.readingLoading} onClick={() => c.readEvidence(source.observation_id)}>{`${index + 1}. ${source.label}`}</Button>
        {source.status ? <StatusBadge tone={source.status === "ok" ? "success" : "warning"} label={source.status === "ok" ? t("debug.readComplete") : source.status === "partial" ? t("debug.readPartial") : t("debug.readIncomplete")} /> : null}
        {source.error_code ? <TextBlock text={t("debug.reasonCode", { code: source.error_code })} /> : null}
        {source.received_at ? <TextBlock text={displayTime(source.received_at, locale, t)} /> : null}
      </div>)}
      {!exchange.evidence.length ? <TextBlock text={t("debug.noSavedReadings")}  /> : null}
      {c.readingLoading ? <TextBlock text={t("debug.loadingReading")}  /> : null}
      {c.readingError ? <InlineError title={t("debug.readingError")}  error={c.readingError} /> : null}
      {c.evidence ? <details open><summary>{t("debug.rawData")}</summary><Stack><TextBlock text={t("debug.readingIdentity", { id: c.evidence.observation_id, hash: c.evidence.sha256 })} /><JsonView data={c.evidence.observation} /></Stack></details> : null}
      <details><summary>{t("debug.deliveryStatus")}</summary><Stack>
        {exchange.handoff ? <><TextBlock text={t("debug.preparedShort")}  /><TextBlock text={exchange.handoff.summary} /><TextBlock text={t("debug.preparedNotReceived")}  /></> : exchange.result?.agent_text ? <TextBlock text={exchange.result.agent_text} /> : <TextBlock text={t("debug.noSavedShort")}  />}
        <TextBlock text={exchange.report_unavailable ? t("debug.reportUnavailable") : exchange.report ? t("debug.reportState", { state: exchange.report.state }) : t("debug.noSeparateReport")} />
        <JsonView data={{ history_saved: exchange.history_saved, report: exchange.report, handoff: exchange.handoff, delivery: exchange.result?.delivery }} />
      </Stack></details>
      {exchange.audit_available ? <Button onClick={() => openAudit(exchange.workspace_id, exchange.turn_id)}>{t("debug.viewSavedAudit")}</Button> : null}
    </Stack>}
  </Stack></Card>
}
