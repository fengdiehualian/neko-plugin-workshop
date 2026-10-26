import { Stack, Text, Tip, Alert, Button, Field, Input, Select, Grid, Accordion, StatusBadge, useConfirm, useEffect } from "@neko/plugin-ui"
import type { PluginSurfaceProps } from "@neko/plugin-ui"
import { BuildPicker } from "./build_picker"

// Keep declarations before JSX for the host's lightweight module scanner.
export const SavedPlayerBuilds = SavedPlayerBuildsView
export const PlayerBuildEditor = PlayerBuildEditorView
export const usePlayerBuildDraft = useBuildDraft

type Choice = { id: string; name: string; label?: string; path_id?: string; path_name?: string; rarity?: number
  signature_for?: string[]; signature_form_ids?: string[]; effects?: string[] }
type Build = {
  form_id: string; name: string; level?: number | null; eidolon?: number | null; edit_revision?: string
  light_cone?: { id?: string; name: string; level?: number | null; superimposition?: number | null } | null
  sets?: { outer: { id: string; name: string; pieces: number }[] | null; inner: { id: string; name: string } | null }
  panel?: { values?: Record<string, string>; state?: string }; manual_fields?: string[]; manual_updated_at?: string; fetched_at?: string
  relic_sets?: string[]; source?: string; freshness?: string
}
type Draft = {
  form_id: string; level: string; eidolon: string; cone: string; cone_level: string; cone_rank: string
  outer_mode: string; outer_a: string; outer_b: string; inner: string; panel: Record<string, string>
}
const basicStats = ["hp", "atk", "def", "spd", "crit_rate", "crit_dmg"]
const extraStats = ["break_effect", "effect_hit", "effect_res", "energy_regen", "healing_bonus", "damage_bonus", "elation"]

function toDraft(form_id: string, row?: Build): Draft {
  const outer = row?.sets?.outer
  return { form_id, level: String(row?.level ?? ""), eidolon: String(row?.eidolon ?? ""),
    cone: row?.light_cone?.id || "", cone_level: String(row?.light_cone?.level ?? ""),
    cone_rank: String(row?.light_cone?.superimposition ?? ""),
    outer_mode: outer == null ? "unknown" : !outer.length ? "scattered" : outer.length === 2 ? "two_two" : outer[0].pieces === 4 ? "four" : "two_scattered",
    outer_a: outer?.[0]?.id || "", outer_b: outer?.[1]?.id || "", inner: row?.sets?.inner?.id || "",
    panel: { ...(row?.panel?.values || {}) } }
}

function draftChanges(draft: Draft, original: Draft): Record<string, any> {
  const result: Record<string, any> = {}
  for (const key of ["level", "eidolon"] as const) {
    if (draft[key] !== original[key]) result[key] = draft[key].trim() ? Number(draft[key]) : null
  }
  if (["cone", "cone_level", "cone_rank"].some(key => draft[key as keyof Draft] !== original[key as keyof Draft])) {
    result.light_cone = draft.cone ? { id: draft.cone, level: draft.cone_level.trim() ? Number(draft.cone_level) : null,
      superimposition: draft.cone_rank.trim() ? Number(draft.cone_rank) : null } : null
  }
  if (["outer_mode", "outer_a", "outer_b", "inner"].some(key => draft[key as keyof Draft] !== original[key as keyof Draft])) {
    result.sets = { outer: draft.outer_mode === "unknown" ? null : draft.outer_mode === "scattered" ? [] :
      draft.outer_mode === "four" ? [{ id: draft.outer_a, pieces: 4 }] :
      draft.outer_mode === "two_scattered" ? [{ id: draft.outer_a, pieces: 2 }] :
      [{ id: draft.outer_a, pieces: 2 }, { id: draft.outer_b, pieces: 2 }], inner: draft.inner || null }
  }
  if (JSON.stringify(draft.panel) !== JSON.stringify(original.panel)) {
    result.panel = Object.fromEntries(Object.entries(draft.panel).filter(([, value]) => value.trim()).map(([key, value]) => [key, value.trim()]))
  }
  return result
}

function useBuildDraft(surface: PluginSurfaceProps<any>, busy: string) {
  const { t, state } = surface
  const rows: Build[] = state.player_builds?.characters || []
  const [draft, setDraft] = surface.useLocalState<Draft | null>("hsrBuildDraft", null)
  const [base, setBase] = surface.useLocalState<Draft | null>("hsrBuildBase", null)
  const [revision, setRevision] = surface.useLocalState("hsrBuildRevision", "")
  const [notice, setNotice] = surface.useLocalState<{ tone: "success" | "danger" | "info"; text: string } | null>("hsrBuildNotice", null)
  const [receipt, setReceipt] = surface.useLocalState<Build | null>("hsrBuildReceipt", null)
  const [focusRequest, setFocusRequest] = surface.useLocalState("hsrBuildFocusRequest", 0)
  const confirm = useConfirm()
  const changes = draft && base ? draftChanges(draft, base) : {}
  const dirty = Object.keys(changes).length > 0
  const saved = rows.find(row => row.form_id === draft?.form_id)
  const stale = !!draft && (saved?.edit_revision || state.player_builds?.new_character_revision || "") !== revision

  async function load(id: string, discard = false) {
    if (busy) return false
    if (dirty && !discard && !await confirm(t("build.discardDraft"))) return false
    const row = rows.find(row => row.form_id === id)
    const next = id ? toDraft(id, row) : null
    setDraft(next); setBase(next); setRevision(row?.edit_revision || state.player_builds?.new_character_revision || ""); setNotice(null); setReceipt(null)
    setFocusRequest((value: number) => value + 1)
    return true
  }
  return { draft, base, revision, notice, receipt, changes, dirty, saved, stale, focusRequest,
    setDraft, setBase, setRevision, setNotice, setReceipt, load,
    focusEditor: () => setFocusRequest((value: number) => value + 1) }
}

function buildSummary(row: Build, t: PluginSurfaceProps<any>["t"]) {
  return t("uid.roleSummary", { name: row.name, level: row.level ?? t("build.optional"), eidolon: row.eidolon ?? t("build.optional") })
}

function BuildDetails({ row, t }: { row: Build; t: PluginSurfaceProps<any>["t"] }) {
  const values = row.panel?.values || {}
  const stats = [...basicStats, ...extraStats].filter(key => values[key] !== undefined && values[key] !== "")
  return <Stack gap={8}>
    <StatusBadge tone="info" label={t(row.manual_fields?.length ? row.fetched_at ? "build.source.mixed" : "build.source.manual" : "build.source.uid")} />
    <Text>{row.light_cone ? t("uid.cone", { name: row.light_cone.name, level: row.light_cone.level ?? t("build.optional"), rank: row.light_cone.superimposition ?? t("build.optional") }) : t("build.noSavedCone")}</Text>
    <Text>{(row.relic_sets || []).join(" / ") || t("build.noSavedSets")}</Text>
    {stats.length ? <dl style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(130px, 1fr))", gap: "8px 16px", margin: 0 }}>
      {stats.map(key => <div key={key}><dt style={{ color: "var(--muted)", fontSize: "13px" }}>{t("build.stat." + key)}</dt>
        <dd style={{ margin: "2px 0 0", fontWeight: 650 }}>{values[key]}</dd></div>)}
    </dl> : <Text>{t("build.noSavedPanel")}</Text>}
    {stats.length && basicStats.some(key => values[key] === undefined || values[key] === "") ? <Text>{t("build.partialPanel")}</Text> : null}
    {row.manual_updated_at ? <Text>{t("build.updatedAt", { time: new Date(row.manual_updated_at).toLocaleString() })}</Text> : null}
    {row.fetched_at ? <Text>{t("uid.queriedAt", { time: new Date(row.fetched_at).toLocaleString() })}</Text> : null}
    {row.freshness === "refresh_recommended" ? <Text>{t("uid.refreshRecommended")}</Text> : null}
  </Stack>
}

function SavedPlayerBuildsView({ surface, rows, busy, onEdit, editor }: {
  surface: PluginSurfaceProps<any>; rows: Build[]; busy: string; onEdit: () => void; editor: ReturnType<typeof useBuildDraft>
}) {
  const { t } = surface
  return rows.length ? <Accordion id="uid-saved-v2" open={false} title={t("uid.savedDetails")}>
    {rows.map(row => <Accordion id={"uid-saved-" + row.form_id} key={row.form_id} open={false} title={buildSummary(row, t)}>
      <BuildDetails row={row} t={t} />
      <Button disabled={!!busy} onClick={async () => { if (await editor.load(row.form_id)) onEdit() }}>{t("build.editRole", { name: row.name })}</Button>
    </Accordion>)}
  </Accordion> : null
}

function PlayerBuildEditorView({ surface, busy, onBusy, editor }: {
  surface: PluginSurfaceProps<any>; busy: string; onBusy: (value: string) => void; editor: ReturnType<typeof useBuildDraft>
}) {
  const { t, state, api } = surface
  const options = state.build_options || { characters: [], light_cones: [], outer_sets: [], inner_sets: [] }
  const { draft, revision, notice, receipt, changes, dirty, saved, stale, setDraft, setBase, setRevision, setNotice, setReceipt, load, focusEditor } = editor
  useEffect(() => {
    if (editor.focusRequest && draft) {
      const target = document.getElementById("manual-build-editor")
      target?.focus({ preventScroll: true })
      target?.scrollIntoView({ block: "start", behavior: "auto" })
    }
  }, [editor.focusRequest])
  function edit(key: keyof Draft, value: string) { if (!busy && draft) setDraft({ ...draft, [key]: value }) }
  function stat(key: string, value: string) {
    if (!busy && draft) setDraft({ ...draft, panel: { ...draft.panel, [key]: value } })
  }
  async function save() {
    if (busy || !draft || !dirty) return
    onBusy("manualBuild")
    setNotice({ tone: "info", text: t("build.saving") })
    try {
      const reply = await api.call("save_manual_build", { form_id: draft.form_id, changes, expected_revision: revision }, { timeoutMs: 15000 })
      const result = reply?.player_build_save || reply?.result?.player_build_save || reply?.data?.player_build_save
      if (result?.status !== "saved") {
        setNotice({ tone: "danger", text: t("build.error." + (result?.error_code || "storage_failed")) })
        return
      }
      const verified = result.readback_state === "confirmed" && result.build?.form_id === draft.form_id && result.build?.edit_revision
      if (verified) {
        const next = toDraft(draft.form_id, result.build)
        setDraft(next); setBase(next); setRevision(result.build.edit_revision); setReceipt(result.build)
      } else {
        // The write succeeded, but no claim of verified values without read-back.
        setBase(draft); setReceipt(null)
      }
      setNotice({ tone: verified ? "success" : "info", text: t(verified ? "build.saved" : "build.savedReadbackPending") })
      focusEditor()
      try { await api.refresh() }
      catch { setNotice({ tone: "info", text: t("build.savedRefreshPending") }) }
    } catch { setNotice({ tone: "danger", text: t("build.error.transport") }) }
    finally { onBusy("") }
  }
  function panelField(key: string) {
    return <Field key={key} label={t("build.stat." + key)}>
      <Input value={draft?.panel[key] || ""} placeholder={t("build.optional")} onChange={(value: string) => stat(key, value)} />
    </Field>
  }
  function picker(id: string, label: string, rows: Choice[], value: string, onChange: (value: string) => any, empty = "build.unknown") {
    const items = rows.map(row => ({ ...row, note: row.signature_for?.length ? t("build.signature", { names: row.signature_for.join("、") }) : row.path_name || "",
      search: [...row.signature_for || [], ...row.effects || []].join(" ") }))
    return <BuildPicker id={id} label={t(label)} value={value} items={items} onChange={onChange} disabled={!!busy} t={t}
      placeholder={t("build.searchSelect")} emptyLabel={t(empty)} />
  }
  const cones: Choice[] = [...options.light_cones].sort((a: Choice, b: Choice) =>
    Number(b.signature_form_ids?.includes(draft?.form_id || "") || false) - Number(a.signature_form_ids?.includes(draft?.form_id || "") || false))
  const chosenSets = [draft?.outer_a, draft?.outer_mode === "two_two" ? draft?.outer_b : "", draft?.inner]
    .filter(Boolean).map(id => [...options.outer_sets, ...options.inner_sets].find((row: Choice) => row.id === id)).filter(Boolean)
  return <div id="manual-build-editor" tabIndex={-1} aria-label={t("build.editor")}><Stack>
    <Text>{t("build.help")}</Text>
    {notice ? <Alert tone={notice.tone}>{notice.text}</Alert> : null}
    {receipt ? <Accordion id="manual-save-receipt" title={t("build.savedResult", { name: receipt.name })} open={true}><BuildDetails row={receipt} t={t} /></Accordion> : null}
    {picker("build-character", "build.character", options.characters, draft?.form_id || "", value => load(value), "build.chooseCharacter")}
    {draft ? <Stack>
      <Text>{t(saved?.manual_fields?.length ? "build.protected" : saved ? "build.prefilled" : "build.newCharacter")}</Text>
      <Grid cols={2}>
        <Field label={t("build.level")}><Input value={draft.level} placeholder={t("build.optional")} onChange={(value: string) => edit("level", value)} /></Field>
        <Field label={t("build.eidolon")}><Select value={draft.eidolon} options={[{ value: "", label: t("build.unknown") }, ...Array.from({ length: 7 }, (_, i) => ({ value: String(i), label: String(i) }))]}
          onChange={(value: string) => edit("eidolon", value)} /></Field>
      </Grid>
      {picker("build-cone", "build.cone", cones, draft.cone, value => edit("cone", value))}
      {!options.light_cones.length ? <Text>{t("build.catalogMissing")}</Text> : null}
      <Grid cols={2}>
        <Field label={t("build.coneLevel")}><Input value={draft.cone_level} placeholder={t("build.optional")} onChange={(value: string) => edit("cone_level", value)} /></Field>
        <Field label={t("build.coneRank")}><Select value={draft.cone_rank} options={[{ value: "", label: t("build.unknown") }, ...Array.from({ length: 5 }, (_, i) => ({ value: String(i + 1), label: String(i + 1) }))]}
          onChange={(value: string) => edit("cone_rank", value)} /></Field>
      </Grid>
      <Field label={t("build.outerMode")}><Select value={draft.outer_mode} options={["unknown", "four", "two_two", "two_scattered", "scattered"].map(value => ({ value, label: t("build.mode." + value) }))}
        onChange={(value: string) => edit("outer_mode", value)} /></Field>
      {!["unknown", "scattered"].includes(draft.outer_mode) ? <Stack>
        {picker("build-outer-a", "build.outerA", options.outer_sets, draft.outer_a, value => edit("outer_a", value), "build.chooseSet")}
        {draft.outer_mode === "two_two" ? picker("build-outer-b", "build.outerB", options.outer_sets, draft.outer_b, value => edit("outer_b", value), "build.chooseSet") : null}
      </Stack> : null}
      {picker("build-inner", "build.inner", options.inner_sets, draft.inner, value => edit("inner", value))}
      {chosenSets.some(row => row.effects?.length) ? <Accordion id="manual-set-effects" open={false} title={t("build.setEffects")}>
        {chosenSets.map(row => <Stack key={row.id} gap={4}><Text>{row.name}</Text>
          {(row.effects || []).map((effect: string, index: number) => <Text key={index}>{t(index === 1 ? "build.fourEffect" : "build.twoEffect", { effect })}</Text>)}
        </Stack>)}
      </Accordion> : null}
      <Text>{t("build.panelHelp")}</Text>
      <Grid cols={2}>{basicStats.map(panelField)}</Grid>
      <Accordion id="manual-advanced-stats" open={false} title={t("build.advanced")}><Grid cols={2}>{extraStats.map(panelField)}</Grid></Accordion>
      <Tip>{t("build.setBoundary")}</Tip>
      {stale ? <Alert tone="warning">{t("build.stale")}</Alert> : null}
      <Button tone="success" disabled={!!busy || !dirty || stale} onClick={save}>{t(busy === "manualBuild" ? "build.saving" : "build.save")}</Button>
      {stale ? <Button disabled={!!busy} onClick={async () => { await load(draft.form_id) }}>{t("build.reload")}</Button> : null}
    </Stack> : null}
  </Stack></div>
}
