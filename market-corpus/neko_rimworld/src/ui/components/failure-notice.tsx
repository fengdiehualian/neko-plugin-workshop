import { useI18n, Alert, Field, KeyValue, Stack, TextBlock } from "@neko/plugin-ui"
import { text } from "../format"

export function FailureNotice({ summary, observationCount, compact = false }: { summary?: Record<string, any> | null; observationCount?: number; compact?: boolean }) {
  const { t } = useI18n()
  if (!summary) return null
  const provider = summary.provider_error || {}
  const providerMessage = text(provider.message, "", 500)
  const marker = `${text(provider.code, "")} ${text(provider.type, "")} ${providerMessage}`.toLowerCase()
  const overloaded = marker.includes("overload") || marker.includes("upstream") || marker.includes("try again") || marker.includes("unavailable")
  const finalCode = text(summary.error_code, "unknown")
  const timedOut = finalCode.includes("timeout") || finalCode.includes("deadline")
  const limited = finalCode === "model_output_limit" || summary.finish_reason === "length"
  const title = limited ? t("debug.outputLimit") : overloaded ? t("debug.overloaded") : timedOut ? t("debug.modelTimeout") : t("debug.modelFailure")
  const retries = Number(summary.retries || 0)
  const explanation = limited ? t("debug.outputLimitHelp") : overloaded && timedOut
    ? t("debug.overloadTimeoutHelp")
    : overloaded
      ? t("debug.overloadHelp")
      : timedOut
        ? t("debug.timeoutHelp")
        : text(summary.message, t("debug.noModelResponse"), 500)
  const action = limited ? t("debug.outputLimitAction") : overloaded
    ? t("debug.overloadAction")
    : timedOut
      ? t("debug.timeoutAction")
      : t("debug.modelFailureAction")
  return <Stack>
    <Alert tone="danger">{title}：{explanation}</Alert>
    {retries ? <TextBlock text={t("debug.retries", { count: retries })} /> : <TextBlock text={t("debug.noRetry")}  />}
    {typeof observationCount === "number" ? observationCount > 0 ? <TextBlock text={t("debug.retainedReads", { count: observationCount })} /> : <TextBlock text={t("debug.noReadsBeforeFailure")}  /> : null}
    <TextBlock text={action} />
    {!compact && providerMessage ? <Field label={t("debug.providerResponse")} ><TextBlock text={providerMessage} /></Field> : null}
    {!compact ? <KeyValue items={[
      { key: "error", label: t("debug.finalError"), value: finalCode },
      { key: "finish", label: t("debug.finishReason"), value: text(summary.finish_reason) },
      { key: "limit", label: t("debug.requestOutputLimit"), value: text(summary.max_output_tokens) },
      { key: "usage", label: t("debug.outputReasoning"), value: `${text(summary.usage?.completion_tokens)} / ${text(summary.usage?.reasoning_tokens)}` },
      { key: "provider", label: t("debug.providerError"), value: `${text(provider.type)} / ${text(provider.code)}` },
      { key: "phase", label: t("debug.failurePhase"), value: text(summary.phase) },
      { key: "attempts", label: t("debug.attemptCounts"), value: `${text(summary.current_cycle_attempts, text(summary.attempts))} / ${text(summary.failed_attempts)} / ${text(summary.retries)}` },
    ]} /> : null}
  </Stack>
}
