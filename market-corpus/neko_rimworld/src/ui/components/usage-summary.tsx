import { useI18n, Alert, Grid, KeyValue, Stack, StatCard, TextBlock } from "@neko/plugin-ui"
import { timing, tokenValue } from "../format"
import type { AgentRun, AgentRuntimeState } from "../types"

export function UsageSummary({ run, history }: { run?: AgentRun | null; history?: AgentRuntimeState }) {
  const { t } = useI18n()
  const requests = run?.model_calls ?? 0
  return <Stack>
    <Grid cols={4}>
      <StatCard label={t("debug.modelRequests")}  value={run ? requests : "—"} />
      <StatCard label={t("debug.toolCalls")}  value={run?.tool_calls ?? "—"} />
      <StatCard label={t("debug.knownTokens")}  value={tokenValue(run?.usage, "total_tokens", requests)} />
      <StatCard label={t("debug.runDuration")}  value={timing(run?.duration_ms, t)} />
    </Grid>
    <KeyValue items={[
      { key: "input", label: t("debug.inputTokens"), value: tokenValue(run?.usage, "prompt_tokens", requests) },
      { key: "output", label: t("debug.outputTokens"), value: tokenValue(run?.usage, "completion_tokens", requests) },
      { key: "reported", label: t("debug.reportedRequests"), value: run ? `${run.usage?.reported_requests ?? 0} / ${requests}` : "—" },
      { key: "failures", label: t("debug.failedCancelled"), value: `${run?.model_failures ?? 0} / ${run?.model_cancellations ?? 0}` },
    ]} />
    {requests > 0 && !run?.usage?.complete ? <Alert tone="warning">{t("debug.usageIncomplete")}</Alert> : null}
    {history?.totals ? <TextBlock text={t("debug.usageHistory", { runs: history.totals.runs ?? 0, requests: history.totals.model_calls ?? 0, tokens: tokenValue(history.totals.usage, "total_tokens", history.totals.model_calls ?? 0), limit: history.retained_run_limit ?? 20 })} /> : null}
  </Stack>
}
