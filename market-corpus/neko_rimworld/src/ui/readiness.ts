import type { DashboardState } from "./types"

export function connectionLabel(state: DashboardState) {
  if (!state.configuration?.token_configured) return "connection.pairingNeeded"
  if (state.status === "error") return "connection.error"
  if (state.status === "stale" || state.session?.stale) return "connection.interrupted"
  if (state.status === "ready") return state.session ? "connection.ready" : "connection.loadColony"
  if (state.status === "starting" || state.status === "authenticating") return "connection.connecting"
  if (state.status === "stopped" || state.status === "stopping") return "connection.stopped"
  return "connection.waiting"
}

export function failureKey(code?: string) {
  if (code === "world_changed" || code === "connection_changed" || code === "stale_session") return "failure.worldChanged"
  if (code === "agent_not_configured") return "failure.configureModel"
  if (code === "agent_run_budget" || code?.includes("deadline") || code?.includes("timeout")) return "failure.timeout"
  if (code === "model_output_limit") return "failure.outputLimit"
  if (code === "semantic_surface_unavailable" || code === "invalid_catalog") return "failure.modVersion"
  return "failure.incomplete"
}
