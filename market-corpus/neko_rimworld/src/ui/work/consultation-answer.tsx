import { Alert, Button, Card, EmptyState, Markdown, Stack, StatusBadge, TextBlock, Toolbar, ToolbarGroup, useClipboard, useI18n } from "@neko/plugin-ui"
import type { ExchangeDetails } from "./types"
import { analysisLabel, displayTime, savedAnswer } from "./presentation"
import { failureKey } from "../readiness"

export function ConsultationAnswer({ exchange, createdAt, owner, currentColony, openDiagnostics, openSettings }: { exchange: ExchangeDetails; createdAt?: string; owner?: string; currentColony: boolean; openDiagnostics: () => void; openSettings?: () => void }) {
  const { t, locale } = useI18n()
  const clipboard = useClipboard()
  const { full, short } = savedAnswer(exchange)
  const active = exchange.active?.turn_id === exchange.turn_id
  const status = exchange.result?.status || exchange.status
  const failed = !active && ["degraded", "failed", "cancelled"].includes(status)
  const code = exchange.result?.audit?.error_code
  const draftOnly = !!exchange.report && exchange.report.state !== "complete"
  const exported = exchange.handoff?.report?.export || exchange.result?.report?.export
  return <Card title={exchange.question}><Stack>
    <Toolbar><ToolbarGroup><StatusBadge tone={status === "ok" ? "success" : "warning"} label={analysisLabel(status, active, t)} /><TextBlock text={`${owner ? t("answer.owner", { owner }) + " · " : ""}${displayTime(createdAt, locale, t)}`} /></ToolbarGroup></Toolbar>
    <TextBlock text={t(currentColony ? "answer.currentHistory" : "answer.otherHistory")} />
    {failed || !exchange.history_saved || draftOnly ? <Alert tone="warning"><Stack>
      {failed ? <TextBlock text={t(status === "cancelled" ? "failure.cancelled" : failureKey(code))} /> : draftOnly ? <TextBlock text={t("answer.draft")} /> : null}
      {!exchange.history_saved ? <TextBlock text={t(active ? "answer.saving" : "answer.saveUnconfirmed")} /> : null}
      {failed ? <TextBlock text={t("failure.retained")} /> : null}
      {openSettings && (code === "agent_not_configured" || code === "model_output_limit") ? <div><Button onClick={openSettings}>{t("failure.settings")}</Button></div> : null}
    </Stack></Alert> : null}
    {short ? <Markdown source={short} /> : !full ? <EmptyState title={t(active ? "answer.reading" : "answer.empty")} /> : <TextBlock text={t("answer.fullOnly")} />}
    {full ? <details key={`${exchange.workspace_id}:${exchange.turn_id}`} open={!short}>
      <summary style={{ padding: "10px 0", fontWeight: 500, cursor: "pointer" }}>{t("answer.expand")}</summary>
      <div style={{ overflowX: "auto" }}><Stack><Markdown source={full} />{exchange.result?.caveats?.length ? <TextBlock text={exchange.result.caveats.join("\n")} /> : null}</Stack></div>
    </details> : null}
    {exported?.status === "unavailable" && exported.reason === "export_failed" ? <TextBlock text={t("answer.exportUnavailable")} /> : null}
    <Toolbar><ToolbarGroup>
      {full ? <Button onClick={async () => { await clipboard.write(full) }}>{t("answer.copy")}</Button> : null}
      <Button onClick={openDiagnostics}>{t("answer.diagnostics")}</Button>
    </ToolbarGroup></Toolbar>
    <div role="status" aria-live="polite">{clipboard.copied ? <TextBlock text={t("answer.copied")} /> : null}{clipboard.error ? <TextBlock text={t("answer.copyError")} /> : null}</div>
  </Stack></Card>
}
