import { useI18n, useEffect, useRef, useState } from "@neko/plugin-ui"
import type { PluginSurfaceProps } from "@neko/plugin-ui"
import type { DashboardState } from "./types"
import { actionById, normalizedError } from "./format"

export function usePanelServices(props: PluginSurfaceProps<DashboardState>) {
  const { t } = useI18n()
  const state = props.state || {}
  const latestProps = useRef(props)
  latestProps.current = props
  const mounted = useRef(true)
  const [refreshing, setRefreshing] = useState(false)
  const [refreshError, setRefreshError] = useState<Error | null>(null)
  const refreshInFlight = useRef<Promise<void> | null>(null)
  useEffect(() => { mounted.current = true; return () => { mounted.current = false } }, [])
  function refresh(): Promise<void> {
    if (refreshInFlight.current) return refreshInFlight.current
    const request = (async () => {
      setRefreshing(true); setRefreshError(null)
      try { await latestProps.current.api.refresh() }
      catch (error) { if (mounted.current) setRefreshError(normalizedError(error)) }
      finally {
        refreshInFlight.current = null
        if (mounted.current) setRefreshing(false)
      }
    })()
    refreshInFlight.current = request
    return request
  }
  async function call(id: string, args: Record<string, any>, timeoutMs = 15_000) {
    const current = latestProps.current
    const action = actionById(current.actions || [], id)
    if (!action) throw new Error(t("debug.unavailable"))
    return current.api.call(action.entry_id || action.id, args, { timeoutMs, userInitiated: true })
  }
  return { state, mounted, refresh, refreshing, refreshError, call }
}
export type PanelServices = ReturnType<typeof usePanelServices>
