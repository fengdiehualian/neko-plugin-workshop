import {
  Page, Card, Stack, Inline, Columns, Split, Text, Button, StatusBadge, Field, Input,
  PasswordInput, Textarea, Select, Switch, Accordion, Alert, EmptyState, Tooltip,
  useState, useEffect, useRef, useConfirm, type PluginSurfaceProps,
} from "@neko/plugin-ui";

const KEEP = "__API_PRESETS_KEEP_SECRET__";
const slots = ["conversation", "summary", "gameMain", "gameSummary", "correction", "emotion", "vision", "agent", "omni", "tts", "image"];
const defaults = Object.fromEntries(slots.map(s => [s, s === "omni" ? "follow_core" : s === "image" ? "disabled" : s === "gameMain" ? "follow_conversation" : s === "gameSummary" ? "follow_summary" : "follow_assist"]));
type Slot = { provider: string; url?: string; model?: string; key?: string; vendor_id?: string };
type Vendor = { name: string; url: string; key: string };
type Preset = { name: string; notes: string; core: string; assist: string; enableCustomApi: boolean; keys: Record<string, string>; slots: Record<string, Slot>; vendors?: Record<string, Vendor> };
type Item = { id: string; revision: number; preset: Preset };
type ProbeGroup = { functions: string[]; model: string; status: string; note?: string; error_code?: string; http_status?: number; free_policy?: boolean };
type Probe = { preset_id: string; revision: number; groups: ProbeGroup[]; tested: number };
type Catalog = { core: string[]; assist: string[]; labels: Record<string, string>; key_fields: Record<string, string>; slot_providers: Record<string, string[]>; models?: { core: Record<string, string>; assist: Record<string, Record<string, string>> } };
type State = { presets?: Item[]; catalog?: Catalog; connectivity?: Probe; error?: string; catalog_error?: string; last_application?: { name: string; time: string; status: string }; max_presets?: number };
const blank = (): Preset => ({ name: "", notes: "", core: "free", assist: "free", enableCustomApi: false, keys: {}, slots: {}, vendors: {} });
const clone = <T,>(value: T): T => JSON.parse(JSON.stringify(value));

export default function Panel({ state, t, api }: PluginSurfaceProps<State>) {
  const [items, setItems] = useState<Item[]>(state.presets || []);
  const [selected, setSelected] = useState("");
  const [revision, setRevision] = useState(0);
  const [draft, setDraft] = useState<Preset | null>(null);
  const [vendorEdit, setVendorEdit] = useState<{ id: string; value: Vendor } | null>(null);
  const [baseline, setBaseline] = useState("");
  const [copyPicker, setCopyPicker] = useState(false);
  const [copySource, setCopySource] = useState("");
  const [imported, setImported] = useState(false);
  const [importSkipped, setImportSkipped] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [testing, setTesting] = useState(false);
  const [probe, setProbe] = useState<Probe | null>(state.connectivity || null);
  const [testFailure, setTestFailure] = useState(false);
  const [message, setMessage] = useState("");
  const [tone, setTone] = useState<"success" | "danger">("success");
  const alive = useRef(true);
  const activeCall = useRef<AbortController | null>(null);
  const confirm = useConfirm();
  const catalog = state.catalog;
  const dirty = draft !== null && (JSON.stringify(draft) !== baseline || vendorEdit !== null);
  // Secret drafts stay in this mounted surface; never use persisted useLocalState.
  useEffect(() => {
    alive.current = true;
    return () => { alive.current = false; activeCall.current?.abort(); activeCall.current = null; };
  }, []);
  useEffect(() => { if (Array.isArray(state.presets)) setItems(state.presets); }, [state.presets]);
  useEffect(() => { setProbe(state.connectivity || null); }, [state.connectivity]);
  function loadDraft(value: Preset, item?: Item) {
    setSelected(item?.id || ""); setRevision(item?.revision || 0);
    setDraft(value); setBaseline(JSON.stringify(value)); setMessage("");
    setCopyPicker(false); setCopySource(""); setImported(false); setImportSkipped([]);
    setTestFailure(false);
    setVendorEdit(null);
  }
  function load(item?: Item) { loadDraft(item ? clone(item.preset) : blank(), item); }
  async function discard() {
    return !dirty || await confirm({ title: t("panel.unsaved"), message: t("panel.discard"), confirmLabel: t("panel.discardButton"), cancelLabel: t("panel.cancel") });
  }
  async function choose(item?: Item) { if (!busy && await discard()) load(item); }
  function change<K extends keyof Preset>(key: K, value: Preset[K]) { setDraft(old => old ? { ...old, [key]: value } : old); }
  function keyChange(provider: string, value: string) { setDraft(old => old ? { ...old, keys: { ...old.keys, [provider]: value } } : old); }
  function slotChange(slot: string, value: Slot) { setDraft(old => old ? { ...old, slots: { ...old.slots, [slot]: value } } : old); }
  function vendorChange(id: string, value: Vendor) { setDraft(old => old ? { ...old, vendors: { ...old.vendors, [id]: value } } : old); }
  function addVendor() {
    if (!draft || busy || vendorEdit || Object.keys(draft.vendors || {}).length >= 32) return;
    let index = 1;
    while (draft.vendors?.["vendor_" + index]) index++;
    setVendorEdit({ id: "vendor_" + index, value: { name: "", url: "", key: "" } });
  }
  async function saveVendor() {
    if (!draft || !vendorEdit) return;
    await savePreset({ ...draft, vendors: { ...draft.vendors, [vendorEdit.id]: vendorEdit.value } });
  }
  async function removeVendor(id: string) {
    if (busy || vendorEdit || !draft || Object.values(draft.slots).some(value => value.vendor_id === id)) return;
    if (!await confirm({ title: t("vendors.remove"), message: t("vendors.removeConfirm"), confirmLabel: t("vendors.remove"), cancelLabel: t("panel.cancel") })) return;
    setDraft(old => {
      if (!old) return old;
      const vendors = { ...old.vendors }; delete vendors[id];
      return { ...old, vendors };
    });
  }
  function slotOptions(slot: string, value: Slot) {
    const result = providerOptions(catalog?.slot_providers[slot] || [defaults[slot], "custom"], value.provider);
    for (const [id, vendor] of Object.entries(draft?.vendors || {})) {
      const compatible = slot === "omni" ? /^wss?:\/\//.test(vendor.url) : slot === "image" ? vendor.url.startsWith("https://") : /^https?:\/\//.test(vendor.url);
      if (vendor.name.trim() && compatible || value.vendor_id === id) result.push({ value: "vendor:" + id, label: vendor.name || t("vendors.unnamed") });
    }
    return result;
  }
  function options(values: string[]) { return values.map(value => ({ value, label: value.startsWith("follow_") || value === "disabled" || value === "custom" ? t("modes." + value) : catalog?.labels[value] || value })); }
  function providerOptions(values: string[], current: string) { return options([...new Set([...values, current])]); }
  async function action(id: string, args: Record<string, any>, success: string, onResult?: (result: any) => void) {
    if (busy || activeCall.current) return;
    const controller = new AbortController(); activeCall.current = controller;
    setBusy(true); setMessage("");
    try {
      const response = await api.call(id, args, { timeoutMs: id === "apply_preset" || id === "test_preset" ? 100000 : 20000, signal: controller.signal, userInitiated: true });
      if (!alive.current) return;
      const result = response?.result ?? response?.value ?? response;
      onResult?.(result);
      setTone("success"); setMessage(success);
      return result;
    } catch (error) {
      if (alive.current) { setTone("danger"); setMessage(error instanceof Error ? error.message : t("panel.failed")); }
    } finally {
      activeCall.current = null;
      if (alive.current) { setBusy(false); try { await api.refresh(); } catch { /* Preserve the action outcome if context refresh fails. */ } }
    }
  }
  async function save() { if (draft && !vendorEdit) await savePreset(draft); }
  async function savePreset(value: Preset) {
    const saved = await action("save_preset", { preset: value, preset_id: selected, revision }, t("panel.saved"), result => {
      if (result?.id && result?.preset) {
        setItems(old => [...old.filter(item => item.id !== result.id), result]); load(result);
      }
    });
    if (alive.current && saved?.id) await test(saved.id, saved.revision, true);
  }
  async function test(id = selected, rev = revision, afterSave = false) {
    if (!id || !alive.current) return;
    setTesting(true); setTestFailure(false); setProbe(null);
    try {
      const result = await action("test_preset", { preset_id: id, revision: rev }, t(afterSave ? "probe.savedTested" : "probe.finished"), value => setProbe(value));
      if (alive.current && !result) { setTestFailure(true); setTone("danger"); setMessage(t(afterSave ? "probe.savedTestFailed" : "probe.failed")); }
    } finally { if (alive.current) setTesting(false); }
  }
  async function copy() {
    if (busy || !copySource || !await discard()) return;
    const original = items.find(item => item.id === copySource);
    if (!original) return;
    let name = original.preset.name.slice(0, 62) + t("panel.copySuffix");
    let n = 2;
    while (items.some(item => item.preset.name.toLowerCase() === name.toLowerCase())) name = original.preset.name.slice(0, 60) + t("panel.copySuffix") + " " + n++;
    await action("copy_preset", { preset_id: original.id, name }, t("panel.copied"), result => {
      if (result?.id && result?.preset) { setItems(old => [...old, result]); load(result); }
    });
  }
  async function newFromCurrentApi() {
    if (busy || !await discard()) return;
    await action("current_api_draft", {}, t("panel.imported"), result => {
      if (result?.preset) {
        loadDraft(result.preset); setImported(true);
        setImportSkipped(Array.isArray(result.skipped_slots) ? result.skipped_slots : []);
      }
    });
  }
  async function remove() {
    if (!selected || !await confirm({ title: t("panel.delete"), message: t("panel.deleteConfirm", { name: draft?.name }), tone: "danger", confirmLabel: t("panel.delete"), cancelLabel: t("panel.cancel") })) return;
    await action("delete_preset", { preset_id: selected, revision }, t("panel.deleted"), () => { setItems(old => old.filter(item => item.id !== selected)); setDraft(null); setVendorEdit(null); setSelected(""); });
  }
  async function apply() {
    if (!selected || dirty || !catalog || !await confirm({ title: t("panel.apply"), message: t("panel.applyConfirm", { name: draft?.name }), tone: "warning", confirmLabel: t("panel.apply"), cancelLabel: t("panel.cancel") })) return;
    await action("apply_preset", { preset_id: selected, revision, confirmed: true }, t("panel.applied"));
  }
  function secretField(value: string, onChange: (value: string) => void, disabled = busy) {
    return <Stack gap={6}>
      <PasswordInput value={value === KEEP ? "" : value} placeholder={t(value === KEEP ? "panel.keySaved" : "panel.keyPlaceholder")} disabled={disabled} onChange={onChange} />
      {value === KEEP && <Inline><StatusBadge tone="success" label={t("panel.keyStored")} /><Button disabled={disabled} onClick={() => onChange("")}>{t("panel.clearKey")}</Button></Inline>}
    </Stack>;
  }
  const bookProviders = draft ? [...new Set([...Object.keys(catalog?.key_fields || {}), ...Object.keys(draft.keys)])].filter(p => p !== "free") : [];
  const last = state.last_application;
  function functionLabel(id: string) { return t(id === "core" || id === "assist" ? "panel." + id : "slots." + id); }
  function previewModel(slot: string): string {
    if (!draft) return "";
    if (slot === "core") return catalog?.models?.core[draft.core] || "";
    if (slot === "assist") return catalog?.models?.assist[draft.assist]?.conversation || "";
    const saved = draft.slots[slot] || { provider: defaults[slot] };
    const value = draft.enableCustomApi || slot === "tts" && saved.provider === "gptsovits" ? saved : { provider: defaults[slot] };
    if (value.provider === "custom" || value.provider === "gptsovits") return value.model || "";
    if (value.provider === "follow_conversation") return previewModel("conversation");
    if (value.provider === "follow_summary") return previewModel("summary");
    if (slot === "omni") return previewModel("core");
    if (slot === "tts" || slot === "image") return "";
    const provider = value.provider === "follow_core" ? draft.core : draft.assist;
    const field = slot === "gameMain" ? "conversation" : slot === "gameSummary" ? "summary" : slot;
    return catalog?.models?.assist[provider]?.[field] || "";
  }
  const tested = !dirty && probe?.preset_id === selected && probe.revision === revision;
  const lights: ProbeGroup[] = tested ? probe.groups.slice(0, 13) : ["core", "assist", ...slots].map(id => ({
    functions: [id], model: previewModel(id),
    status: id === "image" && (!draft?.enableCustomApi || (draft?.slots.image?.provider || defaults.image) === "disabled") ? "not_configured" : testing ? "testing" : "untested",
  }));
  return <Page title={t("panel.title")} subtitle={t("panel.subtitle")}>
    <style>{`
      .api-presets-layout > * { min-width: 0; }
      .api-presets-layout .neko-button { overflow-wrap: anywhere; }
      .api-presets-editor { min-width: 0; border: 1px solid var(--border); border-radius: var(--radius-md); background: var(--surface); }
      .api-presets-editor-bar { position: sticky; top: 8px; z-index: 10; padding: 12px 14px; border-bottom: 1px solid var(--border); border-radius: var(--radius-md) var(--radius-md) 0 0; background: var(--surface-strong); }
      .api-presets-editor-title { margin: 0; font-size: 15px; font-weight: 720; }
      .api-presets-editor-body { padding: 14px; }
      .api-presets-editor-body input, .api-presets-editor-body textarea, .api-presets-editor-body select, .api-presets-editor-body button { scroll-margin-top: 140px; }
      .api-presets-key-row { min-width: 0; }
      .api-presets-vendor-label { display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-bottom: 6px; font-size: 13px; font-weight: 700; }
      .api-presets-vendor-name { color: #20863c; overflow-wrap: anywhere; }
      .api-presets-vendor-tools { display: flex; gap: 4px; flex-shrink: 0; }
      .api-presets-vendor-tools .neko-button { min-height: 28px; padding: 2px 7px; font-size: 18px; line-height: 1; }
      .api-presets-vendor-delete { color: #dc3545; }
      .api-presets-status-row { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; }
      .api-presets-lights { display: flex; flex-wrap: wrap; gap: 2px; align-items: center; }
      .api-presets-light-tip { padding: 6px; }
      .api-presets-light-tip .neko-tooltip-content { white-space: normal; width: 250px; max-width: calc(100vw - 60px); left: 0; transform: translate(0, -4px); overflow-wrap: anywhere; }
      .api-presets-light-tip:hover .neko-tooltip-content, .api-presets-light-tip:focus .neko-tooltip-content { transform: translate(0, 0); }
      .api-presets-light-tip:focus-visible { outline: 2px solid var(--primary); border-radius: 4px; }
      .api-presets-light { display: block; width: 10px; height: 10px; border-radius: 50%; background: #FFD700; }
      .api-presets-light[data-status="connected"] { background: #28A745; box-shadow: 0 0 6px rgba(40,167,69,.5); }
      .api-presets-light[data-status="failed"] { background: #DC3545; box-shadow: 0 0 6px rgba(220,53,69,.5); }
      .api-presets-light[data-status="not_configured"], .api-presets-light[data-status="unsupported"] { background: #CCC; }
      .api-presets-light[data-status="testing"] { background: #40C5F1; animation: api-presets-pulse 1.2s ease-in-out infinite; }
      @keyframes api-presets-pulse { 50% { opacity: .4; } }
      @media (prefers-reduced-motion: reduce) { .api-presets-light[data-status="testing"] { animation: none; } }
      @media (max-width: 900px) { .api-presets-lights .api-presets-light-tip .neko-tooltip-content { position: fixed; width: auto; max-width: none; left: 16px; right: 16px; top: auto; bottom: 16px; transform: none; } }
      @media (max-width: 900px) { .api-presets-layout.neko-split { grid-template-columns: minmax(0, 1fr); } }
    `}</style>
    <Stack gap={16}>
      {state.error && <Alert tone="danger">{state.error}</Alert>}
      {state.catalog_error && <Alert tone="warning">{state.catalog_error} <Button disabled={busy} onClick={async () => { await api.refresh(); }}>{t("panel.refresh")}</Button></Alert>}
      {message && <Alert tone={tone}>{message}</Alert>}
      {last && <Alert tone={last.status === "success" ? "info" : "warning"}>{t(last.status === "success" ? "panel.lastSuccess" : "panel.lastUnknown", { name: last.name, time: new Date(last.time).toLocaleString() })}</Alert>}
      <Split className="api-presets-layout" ratio="280px minmax(0, 1fr)" gap={16} align="start">
        <Card title={t("panel.presets", { count: items.length, max: state.max_presets || 100 })}>
          <Stack gap={12}>
            <Inline>
              <Button tone="primary" disabled={busy || items.length >= 100} onClick={() => choose()}>{t("panel.new")}</Button>
              <Button disabled={busy || !catalog || items.length >= 100} onClick={newFromCurrentApi}>{t("panel.newFromApi")}</Button>
              <Button disabled={busy || items.length === 0 || items.length >= 100} onClick={() => { setCopyPicker(true); setCopySource(""); }}>{t("panel.copy")}</Button>
            </Inline>
            {copyPicker && <Card title={t("panel.copyChoose")}><Stack gap={12}>
              <Field label={t("panel.copySource")}><Select value={copySource} disabled={busy} options={[{ value: "", label: t("panel.copyPlaceholder") }, ...items.map(item => ({ value: item.id, label: item.preset.name }))]} onChange={setCopySource} /></Field>
              <Inline><Button tone="primary" disabled={busy || !items.some(item => item.id === copySource)} onClick={copy}>{t("panel.copyConfirm")}</Button><Button disabled={busy} onClick={() => { setCopyPicker(false); setCopySource(""); }}>{t("panel.cancel")}</Button></Inline>
            </Stack></Card>}
            {items.length === 0 && <EmptyState title={t("panel.empty")} description={t("panel.emptyHelp")} />}
            {items.map(item => <Card key={item.id}>
              <Stack gap={6}><Inline justify="space-between"><Button tone={selected === item.id ? "primary" : "default"} disabled={busy} onClick={() => choose(item)}>{item.preset.name}</Button>{selected === item.id && <StatusBadge label={t("panel.selected")} />}</Inline><Text>{item.preset.notes.slice(0, 180)}</Text><Text>{t("panel.core") + "：" + (catalog?.labels[item.preset.core] || item.preset.core)}</Text><Text>{t("panel.assist") + "：" + (catalog?.labels[item.preset.assist] || item.preset.assist)}</Text></Stack>
            </Card>)}
          </Stack>
        </Card>
        {!draft ? <Card><EmptyState title={t("panel.selectPreset")} description={t("panel.emptyHelp")} /></Card> : <section className="api-presets-editor">
          <div className="api-presets-editor-bar">
            <Inline justify="space-between" gap={12}>
              <Stack gap={6}><h2 className="api-presets-editor-title">{selected ? t("panel.edit") : t("panel.new")}</h2><div className="api-presets-status-row"><StatusBadge tone={dirty || !selected ? "warning" : "default"} label={t(dirty || !selected ? "panel.unsaved" : "panel.savedState")} /><div className="api-presets-lights" aria-label={t("probe.title")}>
                {lights.map((light, index) => <Tooltip key={index} className="api-presets-light-tip" placement="bottom" content={<Stack gap={4}><Text>{t("probe.function") + "：" + light.functions.map(functionLabel).join("、")}</Text><Text>{t("probe.model") + "：" + (light.model || t("probe.hostModel"))}</Text><Text>{t("probe.status." + light.status)}</Text>{light.note && <Text>{t("probe.note." + light.note)}</Text>}{light.http_status && <Text>{"HTTP " + light.http_status}</Text>}{light.error_code && <Text>{t("probe.error." + light.error_code)}</Text>}</Stack>}><span className="api-presets-light" data-status={light.status} aria-label={light.functions.map(functionLabel).join("、") + " · " + t("probe.status." + light.status)} /></Tooltip>)}
              </div></div></Stack>
              <Inline>
                <Button tone="primary" disabled={busy || !!vendorEdit || !draft.name.trim() || !dirty && !!selected} onClick={save}>{t(busy ? "panel.working" : "panel.save")}</Button>
                <Button tone="success" disabled={busy || !selected || dirty || !catalog} onClick={apply}>{t("panel.apply")}</Button>
                <Button disabled={busy || !selected || dirty || !catalog} onClick={() => test()}>{t(testing ? "probe.testing" : "actions.test")}</Button>
                <Button tone="danger" disabled={busy || !selected} onClick={remove}>{t("panel.delete")}</Button>
              </Inline>
            </Inline>
            {dirty && <Text>{t("panel.saveFirst")}</Text>}
            <Text>{t(testFailure ? "probe.failed" : "probe.help")}</Text>
          </div>
          <div className="api-presets-editor-body"><Stack gap={16}>
            {imported && <Alert tone="info">{t("panel.importHelp")}</Alert>}
            {importSkipped.length > 0 && <Alert tone="warning">{t("panel.importSkipped", { slots: importSkipped.map(slot => t("slots." + slot)).join("、") })}</Alert>}
            <Field label={t("panel.name")} required help={t("panel.nameHelp")}><Input value={draft.name} disabled={busy} onChange={value => change("name", value.slice(0, 80))} /></Field>
            <Field label={t("panel.notes")}><Textarea value={draft.notes} disabled={busy} onChange={value => change("notes", value.slice(0, 2000))} /></Field>
            <Card title={t("panel.builtin")}><Stack gap={12}>
              <Text>{t("panel.builtinHelp")}</Text>
              <Field label={t("panel.core")}><Select value={draft.core} options={providerOptions(catalog?.core || ["free"], draft.core)} disabled={busy || !catalog} onChange={value => change("core", value)} /></Field>
              <Field label={t("panel.assist")}><Select value={draft.assist} options={providerOptions(catalog?.assist || ["free"], draft.assist)} disabled={busy || !catalog} onChange={value => change("assist", value)} /></Field>
            </Stack></Card>
            <Accordion id="api-presets-key-book" className="api-presets-key-book" title={t("panel.keyBook")} open={false}><Stack gap={12}>
              <Text>{t("panel.keyBookHelp")}</Text>
              <Columns cols={2} minWidth={280} gap={16} fluid>
                {bookProviders.map(provider => <div key={provider} className="api-presets-key-row" data-provider={provider}><Field label={(catalog?.labels[provider] || provider) + " · API Key"}>{secretField(draft.keys[provider] || "", value => keyChange(provider, value))}</Field></div>)}
                {Object.entries(draft.vendors || {}).map(([id, vendor]) => {
                  const inUse = Object.values(draft.slots).some(value => value.vendor_id === id);
                  return <div key={id} className="api-presets-key-row api-presets-vendor-row" data-vendor={id}>
                    <div className="api-presets-vendor-label"><span><span className="api-presets-vendor-name">{vendor.name}</span>{" · API Key"}</span><div className="api-presets-vendor-tools">
                      <button type="button" className="neko-button" aria-label={t("vendors.edit")} title={t("vendors.edit")} disabled={busy || !!vendorEdit} onClick={() => setVendorEdit({ id, value: clone(vendor) })}>📝</button>
                      <button type="button" className="neko-button api-presets-vendor-delete" aria-label={t("vendors.remove")} title={t(inUse ? "vendors.inUse" : "vendors.remove")} disabled={busy || !!vendorEdit || inUse} onClick={() => removeVendor(id)}>×</button>
                    </div></div>
                    {secretField(vendor.key, key => vendorChange(id, { ...vendor, key }), busy || !!vendorEdit)}
                  </div>;
                })}
              </Columns>
              {!catalog && <Text>{t("panel.keyBookUnavailable")}</Text>}
              {vendorEdit && <Card className="api-presets-vendor-editor" title={t(draft.vendors?.[vendorEdit.id] ? "vendors.edit" : "vendors.unnamed")}>
                <Stack gap={10}>
                  <Field label={t("vendors.name")} required><Input value={vendorEdit.value.name} disabled={busy} onChange={name => setVendorEdit({ ...vendorEdit, value: { ...vendorEdit.value, name: name.slice(0, 80) } })} /></Field>
                  <Field label={t("panel.url")} required><Input value={vendorEdit.value.url} disabled={busy} onChange={url => setVendorEdit({ ...vendorEdit, value: { ...vendorEdit.value, url: url.slice(0, 2048) } })} /></Field>
                  <Field label="API Key" help={t("panel.optionalKey")}>{secretField(vendorEdit.value.key, key => setVendorEdit({ ...vendorEdit, value: { ...vendorEdit.value, key } }))}</Field>
                  <Text>{t("vendors.saveHelp")}</Text>
                  <Inline><Button tone="primary" disabled={busy || !draft.name.trim() || !vendorEdit.value.name.trim() || !vendorEdit.value.url.trim()} onClick={saveVendor}>{t("vendors.save")}</Button><Button disabled={busy} onClick={() => { setVendorEdit(null); }}>{t("panel.cancel")}</Button></Inline>
                </Stack>
              </Card>}
              <Inline><Button disabled={busy || !!vendorEdit || Object.keys(draft.vendors || {}).length >= 32} onClick={addVendor}>＋</Button><Text>{t("vendors.addHelp")}</Text></Inline>
              <Text>{t("vendors.testHelp")}</Text>
            </Stack></Accordion>
            <Accordion id="api-presets-models" title={t("panel.custom")} open={false}><Stack gap={12}>
              <Switch checked={draft.enableCustomApi} label={t("panel.customEnabled")} disabled={busy} onChange={enabled => change("enableCustomApi", enabled)} />
              <Text>{t("panel.customEnabledHelp")}</Text>
              <Text>{t("panel.customHelp")}</Text>
              {slots.map(slot => {
                const value = draft.slots[slot] || { provider: defaults[slot] };
                const custom = value.provider === "custom" || value.provider === "gptsovits";
                const vendor = value.vendor_id ? draft.vendors?.[value.vendor_id] : undefined;
                return <Card key={slot} title={t("slots." + slot)}><Stack gap={10}>
                  <Select value={value.vendor_id ? "vendor:" + value.vendor_id : value.provider} options={slotOptions(slot, value)} disabled={busy || !catalog} onChange={provider => slotChange(slot, provider.startsWith("vendor:") ? { provider: "custom", vendor_id: provider.slice(7), model: custom ? value.model || "" : "" } : { provider, ...(provider === "custom" || provider === "gptsovits" ? { url: "", model: "", key: "" } : {}) })} />
                  {custom && <Stack gap={10}>
                    {(slot === "omni" || slot === "tts") && <Alert tone="warning">{t("panel.specialHelp")}</Alert>}
                    <Field label={t("panel.url")} required help={t(slot === "omni" ? "panel.wsHelp" : slot === "image" ? "panel.httpsHelp" : "panel.urlHelp")}><Input value={vendor?.url ?? value.url ?? ""} disabled={busy || !!value.vendor_id} onChange={url => slotChange(slot, { ...value, url: url.slice(0, 2048) })} /></Field>
                    <Field label={t("panel.model")} required={value.provider !== "gptsovits"}><Input value={value.model || ""} disabled={busy} onChange={model => slotChange(slot, { ...value, model: model.slice(0, 200) })} /></Field>
                    {value.vendor_id ? <Text>{t("vendors.linked")}</Text> : <Field label="API Key" help={t("panel.optionalKey")}>{secretField(value.key || "", key => slotChange(slot, { ...value, key }))}</Field>}
                  </Stack>}
                  {!custom && <Text>{t(slot === "image" ? "panel.imageOff" : "panel.followHelp")}</Text>}
                  <Button disabled={busy || value.provider === defaults[slot]} onClick={() => slotChange(slot, { provider: defaults[slot] })}>{t(slot === "image" ? "panel.disable" : "panel.resetFollow")}</Button>
                </Stack></Card>;
              })}
            </Stack></Accordion>
            <Text>{t("panel.applyScope")}</Text>
          </Stack></div>
        </section>}
      </Split>
    </Stack>
  </Page>;
}
