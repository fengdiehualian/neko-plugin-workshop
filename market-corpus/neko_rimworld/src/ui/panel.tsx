import { NavigationTabs } from "./components/navigation-tabs"
import { Alert, Button, ErrorBoundary, InlineError, Page, Stack, useEffect, useLocalState, useI18n } from "@neko/plugin-ui"
import type { PluginSurfaceProps } from "@neko/plugin-ui"
import type { DashboardState, PageId } from "./types"
import { usePanelServices } from "./services"
import { useDiagnostics } from "./diagnostics/useDiagnostics"
import { useSettings } from "./settings/useSettings"
import { useWorkView } from "./work/useWorkView"
import { StatusStrip } from "./components/status-strip"
import { WorkPage } from "./work/work-page"
import { HistoryPage } from "./work/history-page"
import { ReadingRecords } from "./work/reading-records"
import { DiagnosticsPage } from "./diagnostics/diagnostics-page"
import { SettingsPage } from "./settings/settings-page"
import { text } from "./format"

function PanelBody(props: PluginSurfaceProps<DashboardState>) {
  const { t } = useI18n()
  const services = usePanelServices(props)
  const [storedPage, setPage] = useLocalState<PageId>("rimworld:advice:page", "work")
  const page = ["work", "history", "diagnostics", "settings"].includes(storedPage) ? storedPage : "work"
  const diagnostics = useDiagnostics(services, page, setPage)
  const settings = useSettings(services)
  const work = useWorkView(services, page)
  const active = diagnostics.running || (services.state.agent?.active_count ?? 0) > 0 || (services.state.consultations_in_progress ?? 0) > 0
  useEffect(() => {
    if (!active) return
    let stopped = false
    let timer: ReturnType<typeof setTimeout>
    async function tick() {
      await services.refresh()
      if (!stopped) timer = setTimeout(tick, typeof document !== "undefined" && document.hidden ? 4000 : 1000)
    }
    timer = setTimeout(tick, 1000)
    return () => { stopped = true; clearTimeout(timer) }
  }, [active])
  const openReadings = () => { diagnostics.setDiagnosticMode("readings"); setPage("diagnostics") }
  const openConnectionTest = () => { diagnostics.setDiagnosticMode("test"); setPage("diagnostics") }
  return <Page title="N.E.K.O RimWorld" subtitle={t("panel.subtitle")}>
    <Stack>
      <StatusStrip c={{ ...services, running: active }} />
      {services.refreshError ? <InlineError title={t("panel.refreshError")} error={new Error(t("panel.refreshErrorHelp"))} /> : null}
      {(props.warnings || []).map((warning, index) => <Alert key={index} tone="warning">{text(warning.message, t("debug.panelWarning"), 500)}</Alert>)}
      <NavigationTabs id="rimworld-main-nav" activeId={page} onChange={id => setPage(id as PageId)} items={[
        { id: "work", label: t("nav.advice") },
        { id: "history", label: t("nav.history") },
        { id: "diagnostics", label: t("nav.debug") },
        { id: "settings", label: t(settings.bridgeDirty || settings.agentDirty ? "nav.settingsDirty" : "nav.settings") },
      ]}>
        <ErrorBoundary key={page} title={t("debug.pageError")} >
          {page === "diagnostics" ? <DiagnosticsPage c={diagnostics} readings={<ReadingRecords c={work} openAudit={diagnostics.openArchive} />} /> : page === "settings" ? <SettingsPage c={settings} /> : <div style={{ maxWidth: "1120px", width: "100%", marginInline: "auto", minWidth: 0 }}>{page === "history" ? <HistoryPage c={work} state={services.state} openDiagnostics={openReadings} openSettings={() => setPage("settings")} /> : <WorkPage c={work} state={services.state} openHistory={() => setPage("history")} openDiagnostics={openReadings} openSettings={() => setPage("settings")} openConnectionTest={openConnectionTest} />}</div>}
        </ErrorBoundary>
      </NavigationTabs>
    </Stack>
  </Page>
}

export default function RimWorldBridgePanel(props: PluginSurfaceProps<DashboardState>) {
  const { t } = useI18n()
  return <ErrorBoundary title={t("debug.panelError")}  fallback={(error, reset) => <Page title="N.E.K.O RimWorld"><Stack><InlineError title={t("debug.displayError")}  error={error} /><Button onClick={reset}>{t("debug.displayRetry")}</Button></Stack></Page>}>
    <PanelBody {...props} />
  </ErrorBoundary>
}
