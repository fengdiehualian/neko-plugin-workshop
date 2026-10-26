import { Button, StatusBadge, Toolbar, ToolbarGroup, useI18n } from "@neko/plugin-ui"
import { statusTone, text } from "../format"
import type { PanelServices } from "../services"
import { connectionLabel } from "../readiness"

export function StatusStrip({ c }: { c: PanelServices & { running: boolean } }) {
  const { t } = useI18n()
  const status = text(c.state.status, "unavailable")
  const connected = status === "ready"
  const busy = (c.state.agent?.active_count ?? 0) > 0 || c.running
  return <Toolbar>
    <ToolbarGroup>
      <StatusBadge tone={connected ? "success" : statusTone(status)} label={t(connectionLabel(c.state))} />
      <StatusBadge tone={c.state.configuration?.agent_configured ? "default" : "warning"} label={t(c.state.configuration?.agent_configured ? "connection.modelConfigured" : "connection.modelNeeded")} />
      <StatusBadge tone={busy ? "info" : "default"} label={t(busy ? "common.analyzing" : "common.idle")} />
    </ToolbarGroup>
    <ToolbarGroup><Button disabled={c.refreshing} onClick={c.refresh}>{t(c.refreshing ? "common.refreshing" : "connection.refresh")}</Button></ToolbarGroup>
  </Toolbar>
}
