import { Alert, Button, EmptyState, InlineError, Stack, TextBlock, Toolbar, ToolbarGroup, useI18n } from "@neko/plugin-ui"
import type { WorkController } from "./useWorkView"
import type { DashboardState } from "../types"
import { ConsultationAnswer } from "./consultation-answer"
import { GettingStarted } from "./getting-started"
import { progressText, timing } from "../format"

export function WorkPage({ c, state, openHistory, openDiagnostics, openSettings, openConnectionTest }: { c: WorkController; state: DashboardState; openHistory: () => void; openDiagnostics: () => void; openSettings: () => void; openConnectionTest: () => void }) {
  const { t } = useI18n()
  const active = c.work?.active
  const ready = state.status === "ready" && state.session && !state.session.stale && state.configuration?.agent_configured
  return <Stack>
    {!ready || (!c.exchange && !c.loading && !c.error) ? <GettingStarted state={state} openSettings={openSettings} openConnectionTest={openConnectionTest} /> : null}
    <Toolbar><ToolbarGroup><TextBlock text={t("advice.latest")} /></ToolbarGroup><ToolbarGroup>
      <Button disabled={c.loading} onClick={c.load}>{t(c.loading ? "common.loading" : "advice.refresh")}</Button>
      <Button onClick={openHistory}>{t("advice.history")}</Button>
    </ToolbarGroup></Toolbar>
    {c.error ? <InlineError title={t("advice.readError")} error={new Error(t("advice.readErrorHelp"))} /> : null}
    {active ? <div role="status" aria-live="polite"><Alert tone="info">
      <Stack><TextBlock text={active.question || t("advice.preparing")} /><TextBlock text={`${progressText(active, t)}${active.duration_ms != null ? ` · ${timing(active.duration_ms, t)}` : ""}`} /><TextBlock text={t("advice.waitHelp")} /></Stack>
    </Alert></div> : null}
    {c.exchange ? <ConsultationAnswer exchange={c.exchange} createdAt={c.work?.turns.find(row => row.turn_id === c.exchange?.turn_id)?.created_at} owner={c.work?.workspace.owner} currentColony={c.currentWork} openDiagnostics={openDiagnostics} openSettings={openSettings} /> : c.loading ? <EmptyState title={t("advice.loading")} /> : ready ? <EmptyState title={t("advice.empty")} description={t("setup.ask")} /> : null}
    <TextBlock text={t("advice.continue")} />
  </Stack>
}
