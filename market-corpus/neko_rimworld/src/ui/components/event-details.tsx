import { useI18n, EmptyState, JsonView, KeyValue, Stack } from "@neko/plugin-ui"
import type { AgentAuditEvent } from "../types"
import { text, timing } from "../format"

export function EventDetails({ event }: { event?: AgentAuditEvent }) {
  const { t } = useI18n()
  if (!event) return <EmptyState title={t("debug.chooseEvent")}  description={t("debug.eventHelp")}  />
  return <Stack>
    <KeyValue items={[
      { key: "event", label: t("debug.events"), value: text(event.event) },
      { key: "time", label: t("debug.elapsed"), value: timing(event.elapsed_ms, t) },
      { key: "cycle", label: t("debug.cycleLabel"), value: text(event.cycle) },
    ]} />
    <JsonView data={event.data || {}} />
  </Stack>
}
