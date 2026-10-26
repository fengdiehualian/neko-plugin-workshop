import type { ExchangeDetails } from "./types"

type Translate = (key: string, params?: Record<string, any>) => string
export function analysisLabel(status: string, active: boolean, t: Translate): string {
  return t(active ? "common.analyzing" : `answer.status.${["ok", "degraded", "failed", "cancelled"].includes(status) ? status : "unknown"}`)
}

export function displayTime(value: string | undefined, locale: string, t: Translate): string {
  if (!value) return t("history.noTime")
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? t("history.noTime") : date.toLocaleString(locale, { hour12: false })
}

export function colonyLabel(row: { colony_id?: string | null; workspace_id: string; current_colony?: boolean }, t: (key: string, params?: Record<string, any>) => string) {
  return row.current_colony ? t("history.currentColony") : t("history.colonyLabel", { id: (row.colony_id || row.workspace_id).slice(-8) })
}

export function savedAnswer(exchange: ExchangeDetails) {
  const result = exchange.result
  const full = result?.answer || exchange.report?.full_text || ""
  const handoff = exchange.handoff
  const operational = handoff?.delivery?.operational_notice || handoff?.delivery?.submission_mode === "operational_fallback"
  const short = (!operational && handoff?.summary) ||
    (result?.delivery?.submission_mode !== "operational_fallback" ? result?.agent_text || result?.summary : "") || ""
  return { full, short }
}

const READ_NAMES = new Set(["read_situation", "inspect_state", "inspect_resources", "inspect_planet", "inspect_factions", "inspect_quests", "inspect_trade", "inspect_research", "evaluate_journey", "evaluate_trade", "inspect_threats", "inspect_actions", "survey_space", "inspect_route"])

export function readingLabel(observation: any, index: number, t: Translate): string {
  const request = observation?.request
  const operation = request?.operation
  if (READ_NAMES.has(operation)) return t(`reading.${operation}`)
  if (operation === "query") {
    const args = request.arguments || {}
    const requests = Array.isArray(args.requests) ? args.requests : [args]
    const populations = requests.map((item: any) => String(item?.from || "").toLowerCase())
    const names = []
    if (populations.some((name: string) => name.includes("pawn"))) names.push(t("reading.people"))
    if (populations.some((name: string) => name.includes("thing"))) names.push(t("reading.things"))
    if (populations.some((name: string) => name.includes("map"))) names.push(t("reading.map"))
    return names.length ? t("reading.query", { names: names.join(" / ") }) : t("reading.gameQuery")
  }
  return t("reading.unknown", { index: index + 1 })
}

export function savedReadings(exchange: ExchangeDetails, t: Translate) {
  const observations = exchange.result?.observations || []
  return exchange.evidence.map((record, index) => {
    const observation: any = observations.find((item: any) => item?.observation_id === record.observation_id)
    return { ...record, label: readingLabel(record.request ? record : observation, index, t), received_at: record.received_at || observation?.received_at }
  })
}
