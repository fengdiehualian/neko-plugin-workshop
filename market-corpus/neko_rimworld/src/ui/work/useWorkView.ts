import { useI18n, useEffect, useLocalState, useRef, useState } from "@neko/plugin-ui"
import type { PanelServices } from "../services"
import type { PageId } from "../types"
import type { EvidenceView, ExchangeDetails, WorkDetails, WorkList } from "./types"
import { actionPayload, normalizedError } from "../format"

export function useWorkView(services: PanelServices, page: PageId) {
  const { t } = useI18n()
  const { state, call, mounted } = services
  const [selectedWork, setSelectedWork] = useLocalState<string>("rimworld:selected-work", "")
  const [selectedTurn, setSelectedTurn] = useState("")
  const [workOffset, setWorkOffset] = useState(0)
  const [turnOffset, setTurnOffset] = useState(0)
  const [search, setSearch] = useState("")
  const [filterSearch, setFilterSearch] = useState("")
  const [colonyOnly, setColonyOnly] = useState(false)
  const [list, setList] = useState<WorkList | null>(null)
  const [loadedSession, setLoadedSession] = useState("")
  const [work, setWork] = useState<WorkDetails | null>(null)
  const [exchange, setExchange] = useState<ExchangeDetails | null>(null)
  const [evidence, setEvidence] = useState<EvidenceView | null>(null)
  const [error, setError] = useState<Error | null>(null)
  const [loading, setLoading] = useState(false)
  const [readingError, setReadingError] = useState<Error | null>(null)
  const [readingLoading, setReadingLoading] = useState(false)
  const generation = useRef(0)
  const evidenceGeneration = useRef(0)
  const sessionKey = `${state.session?.colony_id || ""}:${state.session?.load_epoch || ""}`

  async function load() {
    const browse = page === "history"
    const seq = ++generation.current
    setLoading(true); setError(null)
    try {
      const rows = actionPayload(await call("workspace_view", { action: "list", offset: browse ? workOffset : 0, limit: 20, search: browse ? filterSearch : "", colony_only: browse && colonyOnly })) as WorkList
      if (!mounted.current || seq !== generation.current) return
      setList(rows)
      setLoadedSession(sessionKey)
      const current = state.session && !state.session.stale
        ? rows.workspaces.find(row => row.current_colony || row.colony_id === state.session?.colony_id) : null
      const id = browse ? (rows.workspaces.some(row => row.workspace_id === selectedWork) ? selectedWork : rows.workspaces[0]?.workspace_id) : current?.workspace_id
      if (!id) { setWork(null); setExchange(null); setEvidence(null); return }
      const history = actionPayload(await call("workspace_view", { action: "work", workspace_id: id, offset: browse ? turnOffset : 0, limit: 20, search: browse ? filterSearch : "" })) as WorkDetails
      if (!mounted.current || seq !== generation.current) return
      if (history.workspace_id !== id) throw new Error(t("debug.workMismatch"))
      setWork(history)
      const turn = browse && id === selectedWork && history.turns.some(row => row.turn_id === selectedTurn) ? selectedTurn : history.turns[0]?.turn_id
      if (!turn) { setWork(history); setExchange(null); setEvidence(null); return }
      const result = actionPayload(await call("workspace_view", { action: "turn", workspace_id: id, turn_id: turn })) as ExchangeDetails
      if (!mounted.current || seq !== generation.current) return
      if (result.workspace_id !== id || result.turn_id !== turn) throw new Error(t("debug.turnMismatch"))
      if (exchange?.workspace_id !== id || exchange?.turn_id !== turn) {
        evidenceGeneration.current++; setEvidence(null); setReadingError(null); setReadingLoading(false)
      }
      setWork(history); setExchange(result)
    } catch (failure) { if (mounted.current && seq === generation.current) setError(normalizedError(failure)) }
    finally { if (mounted.current && seq === generation.current) setLoading(false) }
  }
  useEffect(() => {
    if (search.trim().slice(0, 256) === filterSearch) return
    const timer = setTimeout(() => { clearSelection(); setFilterSearch(search.trim().slice(0, 256)); setWorkOffset(0); setSelectedWork(""); setSelectedTurn(""); setTurnOffset(0) }, 250)
    return () => clearTimeout(timer)
  }, [search, filterSearch])
  function filterColony(value: boolean) { clearSelection(); setColonyOnly(value); setWorkOffset(0); setTurnOffset(0); setSelectedWork(""); setSelectedTurn("") }
  useEffect(() => {
    if (page === "work" || page === "history") void load()
    return () => { generation.current++ }
  }, [page, selectedWork, selectedTurn, workOffset, turnOffset, filterSearch, colonyOnly, state.action_nonce, state.workspaces?.revision, state.session?.colony_id, state.session?.load_epoch, state.session?.stale])

  function clearSelection() {
    generation.current++; evidenceGeneration.current++
    setWork(null); setExchange(null); setEvidence(null); setError(null); setReadingError(null); setReadingLoading(false)
  }
  function selectWork(id: string) {
    clearSelection(); setSelectedWork(id); setSelectedTurn(""); setTurnOffset(0)
  }
  function selectTurn(id: string) {
    clearSelection(); setSelectedWork(work?.workspace_id || selectedWork); setSelectedTurn(id)
  }
  async function readEvidence(id: string) {
    if (!exchange) return
    const seq = ++evidenceGeneration.current
    const target = { workspace_id: exchange.workspace_id, turn_id: exchange.turn_id }
    setEvidence(null); setReadingError(null); setReadingLoading(true)
    try {
      const result = actionPayload(await call("workspace_view", { action: "observation", ...target, observation_id: id })) as EvidenceView
      if (!mounted.current || seq !== evidenceGeneration.current) return
      if (result.workspace_id !== target.workspace_id || result.turn_id !== target.turn_id || result.observation_id !== id) throw new Error(t("debug.readingMismatch"))
      setEvidence(result)
    } catch (failure) { if (mounted.current && seq === evidenceGeneration.current) setReadingError(normalizedError(failure)) }
    finally { if (mounted.current && seq === evidenceGeneration.current) setReadingLoading(false) }
  }
  // Dashboard identities are deliberately shortened. The read-only host marks
  // the matching work with current_colony using the full native identity.
  const currentMatches = !!state.session && !state.session.stale && loadedSession === sessionKey && !!work &&
    (list?.workspaces.find(row => row.workspace_id === work.workspace_id)?.current_colony || work.workspace.colony_id === state.session.colony_id)
  const visible = page !== "work" || currentMatches
  return { list, work: visible ? work : null, exchange: visible && exchange?.workspace_id === work?.workspace_id ? exchange : null,
    evidence, selectedWork: work?.workspace_id || selectedWork, selectedTurn: exchange?.turn_id || selectedTurn,
    selectWork, selectTurn, workOffset, setWorkOffset, turnOffset, setTurnOffset,
    loading, error, load, readEvidence, readingError, readingLoading, currentWork: currentMatches, search, setSearch, colonyOnly, filterColony }
}
export type WorkController = ReturnType<typeof useWorkView>
