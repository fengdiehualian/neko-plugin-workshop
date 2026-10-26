import { useI18n, Stack } from "@neko/plugin-ui"
import { NavigationTabs } from "../components/navigation-tabs"
import { RunHistoryView } from "./run-history"
import { ExecutionOverview } from "./overview"
import { IndependentTestPage } from "./test-page"
import type { DiagnosticsController } from "./useDiagnostics"

export function DiagnosticsPage({ c, readings }: { c: DiagnosticsController; readings: any }) {
  const { t } = useI18n()
  return <Stack>
    <NavigationTabs id="rimworld-diagnostics-nav" activeId={c.diagnosticMode} onChange={c.setDiagnosticMode} items={[{ id: "records", label: t("debug.executionRecords") }, { id: "readings", label: t("debug.rawReadings") }, { id: "test", label: t("debug.independentTest") }]}>
      {c.diagnosticMode === "test" ? <IndependentTestPage c={c} /> : c.diagnosticMode === "readings" ? readings : <Stack><ExecutionOverview c={c} /><RunHistoryView c={c} /></Stack>}
    </NavigationTabs>
  </Stack>
}
