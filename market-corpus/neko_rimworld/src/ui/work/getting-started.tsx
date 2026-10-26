import { Alert, Button, Card, Stack, TextBlock, useI18n } from "@neko/plugin-ui"
import type { DashboardState } from "../types"

export function GettingStarted({ state, openSettings, openConnectionTest }: { state: DashboardState; openSettings: () => void; openConnectionTest: () => void }) {
  const { t } = useI18n()
  const paired = !!state.configuration?.token_configured
  const loaded = state.status === "ready" && !!state.session && !state.session.stale
  const configured = !!state.configuration?.agent_configured
  return <Card title={t(loaded && configured ? "setup.readyTitle" : "setup.title")}><Stack>
    <TextBlock text={t("setup.intro")} />
    <ol style={{ margin: 0, paddingInlineStart: "24px", lineHeight: 1.8 }}>
      <li>{t(paired ? "setup.paired" : "setup.pair")}</li>
      <li>{t(loaded ? "setup.loaded" : "setup.load")}</li>
      <li>{t(configured ? "setup.configured" : "setup.model")}</li>
    </ol>
    <Alert tone="info">{t("setup.ask")}</Alert>
    <TextBlock text={t("setup.modelNotice")} />
    <div style={{ display: "flex", flexWrap: "wrap", gap: "8px" }}>
      <Button tone="primary" onClick={openSettings}>{t("setup.openSettings")}</Button>
      <Button onClick={openConnectionTest}>{t("setup.checkConnection")}</Button>
    </div>
  </Stack></Card>
}
