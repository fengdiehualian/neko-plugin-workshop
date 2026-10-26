import { Alert, Button, Card, DataTable, EmptyState, Field, Input, InlineError, Stack, TextBlock, Toolbar, ToolbarGroup, useI18n } from "@neko/plugin-ui"
import type { DashboardState } from "../types"
import type { WorkController } from "./useWorkView"
import { ConsultationAnswer } from "./consultation-answer"
import { analysisLabel, colonyLabel, displayTime } from "./presentation"
import { text } from "../format"

export function HistoryPage({ c, state, openDiagnostics, openSettings }: { c: WorkController; state: DashboardState; openDiagnostics: () => void; openSettings?: () => void }) {
  const { t, locale } = useI18n()
  return <Stack>
    <Toolbar><ToolbarGroup><TextBlock text={t("history.intro")} /></ToolbarGroup><ToolbarGroup><Button disabled={c.loading} onClick={c.load}>{t(c.loading ? "common.loading" : "advice.refresh")}</Button></ToolbarGroup></Toolbar>
    {!state.session || state.session.stale ? <Alert tone="info">{t("history.offline")}</Alert> : null}
    {c.error ? <InlineError title={t("history.readError")} error={new Error(t("history.readErrorHelp"))} /> : null}
    <Card title={t("history.selectDiscussion")}><Stack>
      <Field label={t("history.search")} help={t("history.searchHelp")}><Input value={c.search} onChange={value => c.setSearch(value.slice(0, 256))} placeholder={t("history.searchPlaceholder")} /></Field>
      <Toolbar><ToolbarGroup>
        <Button tone={!c.colonyOnly ? "primary" : "default"} onClick={() => c.filterColony(false)}>{t("history.all")}</Button>
        <Button tone={c.colonyOnly ? "primary" : "default"} disabled={!state.session || !!state.session.stale} onClick={() => c.filterColony(true)}>{t("history.currentOnly")}</Button>
      </ToolbarGroup></Toolbar>
      <div style={{ overflowX: "auto" }}><DataTable data={c.list?.workspaces || []} rowKey="workspace_id" selectedKey={c.selectedWork} onSelect={row => c.selectWork(row.workspace_id)} maxRows={20} emptyText={t(c.search || c.colonyOnly ? "history.noMatches" : "history.empty")} columns={[
        { key: "owner", label: t("history.role") },
        { key: "colony", label: t("history.colony"), render: row => colonyLabel(row, t) },
        { key: "last_question", label: t("history.latestQuestion"), render: row => <Button onClick={(event?: MouseEvent) => { event?.stopPropagation(); c.selectWork(row.workspace_id) }}>{text(row.last_question, t("history.noQuestion"), 120)}</Button> },
        { key: "turn_count", label: t("history.discussionCount") },
      ]} /></div>
      <Toolbar><ToolbarGroup><Button disabled={c.workOffset === 0 || c.loading} onClick={() => { c.setWorkOffset(Math.max(0, c.workOffset - 20)) }}>{t("common.previous")}</Button><Button disabled={c.list?.next_offset == null || c.loading} onClick={() => { c.setWorkOffset(c.list!.next_offset!) }}>{t("common.next")}</Button></ToolbarGroup><ToolbarGroup><TextBlock text={t("history.total", { count: c.list?.total ?? 0 })} /></ToolbarGroup></Toolbar>
    </Stack></Card>
    {c.work ? <Card title={t("history.questions")}><Stack>
      <TextBlock text={t("history.selectedDiscussion", { owner: c.work.workspace.owner, colony: colonyLabel({ ...c.work.workspace, current_colony: c.currentWork }, t) })} />
      <div style={{ overflowX: "auto" }}><DataTable data={c.work.turns} rowKey="turn_id" selectedKey={c.selectedTurn} onSelect={row => c.selectTurn(row.turn_id)} maxRows={20} emptyText={t("history.noQuestions")} columns={[
        { key: "question", label: t("history.question"), render: row => <Button onClick={(event?: MouseEvent) => { event?.stopPropagation(); c.selectTurn(row.turn_id) }}>{text(row.question, "", 160)}</Button> },
        { key: "status", label: t("history.status"), render: row => analysisLabel(row.status, c.work?.active?.turn_id === row.turn_id, t) },
        { key: "created_at", label: t("history.time"), render: row => displayTime(row.created_at, locale, t) },
      ]} /></div>
      <Toolbar><ToolbarGroup><Button disabled={c.turnOffset === 0 || c.loading} onClick={() => { c.setTurnOffset(Math.max(0, c.turnOffset - 20)) }}>{t("common.previous")}</Button><Button disabled={c.work.next_offset == null || c.loading} onClick={() => { c.setTurnOffset(c.work!.next_offset!) }}>{t("common.next")}</Button></ToolbarGroup></Toolbar>
    </Stack></Card> : c.loading ? null : <EmptyState title={t("history.choose")} />}
    {c.exchange ? <ConsultationAnswer exchange={c.exchange} createdAt={c.work?.turns.find(row => row.turn_id === c.exchange?.turn_id)?.created_at} owner={c.work?.workspace.owner} currentColony={c.currentWork} openDiagnostics={openDiagnostics} openSettings={openSettings} /> : null}
    <TextBlock text={t("history.continueHelp")} />
  </Stack>
}
