// Hosted TSX: browsing state stays mounted while the detail workspace replaces it.
import {
  Alert,
  Button,
  EmptyState,
  Page,
  SegmentedControl,
  Stack,
  Switch,
  Text,
  useEffect,
  useRef,
  useState,
} from "@neko/plugin-ui";
import { AwarenessCard } from "./components/awareness_card";
import { BatchBar } from "./components/batch_bar";
import { CategorySection } from "./components/category_section";
import { FocusCard } from "./components/focus_card";
import { LibraryToolbar } from "./components/library_toolbar";
import { ZoneBar } from "./components/zone_bar";
import { useLibraryModel } from "./library_model";
import { buildSections, callAction, extractCode } from "./shared";
import type { Surface } from "./shared";

const PANEL_STYLES = `
.sticker-page { max-width:1120px; margin:0 auto; }
.sticker-panel { display:grid; gap:20px; min-width:0; }
.sticker-panel * { letter-spacing:0; }
.sticker-panel .neko-button { border-radius:6px; box-shadow:none; font-size:13px; white-space:normal; overflow-wrap:anywhere; }
.sticker-panel .neko-button[data-tone="default"] { color:var(--text); background:transparent; border-color:var(--border); }
.sticker-panel .neko-button:hover { transform:none; box-shadow:none; }
.sticker-panel .neko-input,.sticker-panel .neko-select { max-width:100%; }
.sticker-panel .neko-input,.sticker-panel .neko-select { border-radius:6px; }
.sticker-panel .neko-field { min-width:0; }
.sticker-panel .neko-text { overflow-wrap:anywhere; }
.sticker-header { display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:8px 20px; }
.sticker-current { margin:0; font-size:12px; color:var(--muted); overflow-wrap:anywhere; }
.sticker-nav.neko-segmented { width:100%; display:flex; gap:24px; padding:0; border:0; border-bottom:1px solid var(--border); border-radius:0; background:transparent; }
.sticker-nav .neko-segmented-button { flex:0 1 auto; border:0; border-bottom:2px solid transparent; border-radius:0; padding:10px 2px; background:transparent; color:var(--muted); font-size:14px; }
.sticker-nav .neko-segmented-button.is-active { border-bottom-color:var(--primary); background:transparent; color:var(--text); box-shadow:none; }
.sticker-panel :is(button,summary,input,select):focus-visible { outline:2px solid var(--primary); outline-offset:3px; }
.sticker-sr-only { position:absolute; width:1px; height:1px; padding:0; margin:-1px; overflow:hidden; clip:rect(0,0,0,0); white-space:nowrap; border:0; }
.sticker-workspace { display:grid; gap:20px; min-width:0; }
.sticker-section-title { margin:0; font-size:15px; line-height:1.5; }
.sticker-sticky { position:sticky; top:0; z-index:2; display:grid; gap:8px; padding:8px 0; background:var(--bg); border-bottom:1px solid var(--border); min-width:0; }
.sticker-feedback { display:flex; align-items:center; flex-wrap:wrap; gap:8px; font-size:13px; min-width:0; }
.sticker-feedback .neko-text { max-height:72px; overflow:auto; font-size:13px; }
.sticker-zones { display:flex; flex-wrap:wrap; gap:8px 12px; align-items:center; min-width:0; }
.sticker-zone-picker { display:flex; flex:1 1 220px; align-items:center; gap:10px; min-width:0; max-width:440px; font-size:13px; }
.sticker-zone-picker > span { flex-shrink:0; color:var(--muted); }
.sticker-zone-picker .neko-select { min-width:0; flex:1; }
.sticker-zone-state { font-size:12px; color:var(--success); }
.sticker-zones > .sticker-menu { width:auto; margin-left:auto; }
.sticker-zones > .sticker-menu[open] { width:100%; }
.sticker-toolbar { display:grid; gap:10px; min-width:0; }
.sticker-search { display:grid; grid-template-columns:minmax(0,1fr) minmax(100px,180px) auto auto; align-items:center; gap:8px; }
.sticker-search > * { min-width:0; }
.sticker-toolbar-menu { border-bottom:1px solid var(--border); padding-bottom:12px; }
.sticker-more { width:36px; height:36px; padding:0; border:1px solid var(--border); border-radius:6px; background:transparent; color:var(--text); font-size:20px; cursor:pointer; }
.sticker-more:disabled { opacity:.5; cursor:default; }
.sticker-category-header { display:flex; flex-wrap:wrap; align-items:center; gap:8px; min-width:0; }
.sticker-category-name { flex:1 1 160px; min-width:0; margin:0; font-size:14px; line-height:1.5; overflow-wrap:anywhere; }
.sticker-category-header > .sticker-menu { width:auto; }
.sticker-category-header > .sticker-menu[open] { flex-basis:100%; }
.sticker-category-header > .sticker-menu > summary { padding:6px; }
.sticker-category { display:grid; gap:10px; min-width:0; }
.sticker-category + .sticker-category { border-top:1px solid var(--border); padding-top:20px; }
.sticker-grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(min(100%,128px),1fr)); gap:8px; min-width:0; }
.sticker-grid > * { width:100%; max-width:176px; min-width:0; }
.sticker-tile { transition:border-color 120ms ease,background 120ms ease; }
.sticker-tile:hover { border-color:var(--primary) !important; }
.sticker-menu { width:100%; min-width:0; }
.sticker-menu > summary { width:fit-content; padding:6px 2px; color:var(--muted); cursor:pointer; font-size:13px; }
.sticker-menu[open] > summary { color:var(--text); }
.sticker-menu-body { display:grid; gap:10px; padding:8px 0 0; min-width:0; }
.sticker-menu-actions { display:flex; flex-wrap:wrap; gap:8px; }
.sticker-batch { display:grid; gap:6px; min-width:0; }
.sticker-batch-main { display:grid; grid-template-columns:minmax(0,1fr) auto; gap:8px; align-items:center; }
.sticker-batch-move { display:grid; grid-template-columns:minmax(0,1fr) auto auto; gap:6px; min-width:0; align-items:center; }
.sticker-batch .neko-button { padding:7px 10px; font-size:12px; }
.sticker-batch-count { margin:0; font-size:13px; line-height:1.5; overflow-wrap:anywhere; }
.sticker-batch-more { max-height:min(220px,40vh); overflow:auto; padding:8px 0 0; }
.sticker-batch .sticker-menu > summary { padding:4px 2px; }
.sticker-settings { display:grid; gap:24px; max-width:760px; min-width:0; }
.sticker-preferences { display:grid; gap:24px; min-width:0; }
.sticker-setting { display:grid; gap:12px; min-width:0; }
.sticker-setting + .sticker-setting { border-top:1px solid var(--border); padding-top:24px; }
.sticker-setting .neko-segmented { display:flex; width:100%; border-radius:6px; }
.sticker-setting .neko-segmented-button { flex:1; min-width:0; border-radius:4px; font-size:13px; white-space:normal; }
.sticker-setting-control { display:grid; grid-template-columns:minmax(0,1fr) auto; gap:10px; align-items:end; max-width:400px; }
.sticker-setting-control .neko-field-label { line-height:20px; }
.sticker-setting-control > .neko-button { align-self:start; margin-top:26px; }
.sticker-setting-current { margin:0; font-size:12px; color:var(--muted); }
.sticker-advanced { padding-top:16px; border-top:1px solid var(--border); }
.sticker-advanced > summary { font-weight:600; color:var(--text); }
.sticker-advanced > .sticker-menu-body { gap:20px; padding-top:16px; }
.sticker-diagnostics { display:grid; gap:16px; min-width:0; }
.sticker-readouts { display:grid; grid-template-columns:repeat(auto-fit,minmax(min(100%,180px),1fr)); gap:12px; margin:0; min-width:0; }
.sticker-readouts > div { display:grid; gap:4px; min-width:0; padding:8px 0; border-bottom:1px solid var(--border); }
.sticker-readouts dt { font-size:12px; color:var(--muted); }
.sticker-readouts dd { margin:0; font-size:14px; line-height:1.5; overflow-wrap:anywhere; }
.sticker-collection-count { margin:0; font-size:13px; color:var(--muted); line-height:1.6; }
@media (max-width:480px) {
  .sticker-page.neko-page { padding:12px; }
  .sticker-workspace { gap:12px; }
  .sticker-workspace .neko-button { padding:7px 10px; font-size:13px; }
  .sticker-zone-picker { flex-basis:100%; max-width:none; }
  .sticker-batch-main { grid-template-columns:1fr auto; gap:6px; }
  .sticker-batch-move { grid-template-columns:minmax(0,1fr) auto auto; }
  .sticker-search { grid-template-columns:minmax(0,1fr) auto 36px; }
  .sticker-search-field { grid-column:1 / -1; }
  .sticker-grid { grid-template-columns:repeat(auto-fill,minmax(min(100%,96px),1fr)); }
  .sticker-category-name { flex-basis:120px; }
  .sticker-setting-control { grid-template-columns:minmax(0,1fr) auto; }
}
`;

export default function Panel(props: Surface) {
  const state = props.state || {};
  const t = props.t;
  const lib = useLibraryModel(props);
  const [viewTab, setViewTab] = useState("library");
  const [categoryFilter, setCategoryFilter] = useState("");
  const [switchBusy, setSwitchBusy] = useState(false);
  const [switchNote, setSwitchNote] = useState("");
  const [settingsBusy, setSettingsBusy] = useState(false);
  const switchBusyRef = useRef(false);
  const operationBusy = !!lib.pending || switchBusy || settingsBusy;
  const managementBusy = operationBusy || !!lib.refreshFailed;
  const stickers = state.stickers || [];
  const groups = state.groups || [];
  const zones = lib.zonesList;
  const view = lib.view;
  const term = lib.query.trim().toLowerCase();
  const sectionValue = (group: string) => `group:${group}`;
  const allSections = buildSections(stickers, groups, "", t, view);
  const matchingSections = buildSections(stickers, groups, term, t, view);
  const sections = categoryFilter
    ? matchingSections.filter((section) => sectionValue(section.group) === categoryFilter)
    : matchingSections;
  const visibleIds = sections.flatMap((section) => section.rows.map((row) => row.id));
  const matchingIds = matchingSections.flatMap((section) => section.rows.map((row) => row.id));
  const hiddenSelected = lib.selected.filter((id) => visibleIds.indexOf(id) < 0).length;
  const zoneTotal = stickers.filter((row) => String(row.zone || "") === view).length;
  const activeZone = zones.filter((zone) => zone.id === lib.activeZone)[0];
  const focusRow = lib.focus
    ? stickers.filter((row) => row.id === lib.focus && matchingIds.indexOf(row.id) >= 0)[0] || null
    : null;

  useEffect(() => {
    setCategoryFilter("");
  }, [view]);
  useEffect(() => {
    if (categoryFilter && !allSections.some((section) => sectionValue(section.group) === categoryFilter)) {
      setCategoryFilter("");
    }
  }, [categoryFilter, view, state.groups, state.stickers]);
  useEffect(() => {
    if (lib.focus && !focusRow) lib.closeFocus();
  }, [lib.focus, focusRow]);

  const changeEnabled = async (next: boolean) => {
    if (switchBusyRef.current || managementBusy) return;
    switchBusyRef.current = true;
    setSwitchBusy(true);
    setSwitchNote("");
    let saved = false;
    try {
      await callAction(props, "switch", { enabled: next });
      saved = true;
      await props.api.refresh();
      lib.clearRefreshFailure();
    } catch (error) {
      if (saved) {
        lib.reportRefreshFailure();
      } else {
        const raw = error instanceof Error ? error.message : String(error ?? "failed");
        const code = extractCode(raw);
        setSwitchNote(t(`panel.error.${code}`, { defaultValue: code }));
      }
    } finally {
      switchBusyRef.current = false;
      setSwitchBusy(false);
    }
  };

  if (focusRow) {
    return (
      <Page className="sticker-page" title={t("panel.focus.title", { defaultValue: "表情详情" })}>
        <style>{PANEL_STYLES}</style>
        <FocusCard
          key={focusRow.id}
          surface={props}
          row={focusRow}
          onExit={lib.closeFocus}
          onRefreshFailed={lib.reportRefreshFailure}
          onRefreshRecovered={lib.clearRefreshFailure}
        />
      </Page>
    );
  }

  return (
    <Page className="sticker-page" title={t("panel.title", { defaultValue: "表情包管理" })} subtitle={state.lanlan || ""}>
      <style>{PANEL_STYLES}</style>
      <div className="sticker-panel">
        {state.error_code ? (
          <Alert tone="danger" message={t(`panel.error.${state.error_code}`, { defaultValue: state.error_code })} />
        ) : null}
        {switchNote ? <Alert tone="danger" message={switchNote} /> : null}
        <div className="sticker-header">
          <Switch
            checked={!!state.enabled}
            disabled={managementBusy}
            label={t("panel.switch.compact", { defaultValue: "猫娘发表情包" })}
            onChange={changeEnabled}
          />
          <p className="sticker-current">
              {t("panel.zone.current", {
                name: activeZone?.name || "—",
                defaultValue: "使用区：{name}",
              })}
          </p>
        </div>
        <SegmentedControl
          className="sticker-nav"
          value={viewTab}
          disabled={operationBusy}
          options={[
            { value: "library", label: t("panel.view.library", { defaultValue: "图库" }) },
            { value: "settings", label: t("panel.view.settings", { defaultValue: "设置" }) },
          ]}
          onChange={(next: any) => setViewTab(String(next))}
        />
        {(viewTab === "library" && (lib.selected.length || lib.pending || lib.libraryNote)) || lib.refreshFailed ? (
          <div className="sticker-sticky">
            {viewTab === "library" ? (
                <BatchBar
                  surface={props}
                  selected={lib.selected}
                  hiddenCount={hiddenSelected}
                  zone={view}
                  disabled={managementBusy}
                  batchTags={lib.batchTags}
                  setBatchTags={lib.setBatchTags}
                  batchGroup={lib.batchGroup}
                  setBatchGroup={lib.setBatchGroup}
                  onRun={(patch) => { lib.runBatch(patch); }}
                  onDelete={() => { lib.batchDelete(hiddenSelected); }}
                  onClear={lib.clearSelection}
                  onClearHidden={() => lib.clearHiddenSelection(visibleIds)}
                />
            ) : null}
            {lib.libraryNote || lib.pending || lib.refreshFailed ? (
              <div className="sticker-feedback" role="status" aria-live="polite">
                <Text>
                  {lib.libraryNote || t("panel.action.pending", { defaultValue: "处理中…" })}
                </Text>
                {lib.refreshFailed ? (
                  <Button disabled={operationBusy} onClick={() => { lib.retryRefresh(); }}>
                    {t("panel.refresh.retry", { defaultValue: "重试刷新" })}
                  </Button>
                ) : null}
              </div>
            ) : null}
          </div>
        ) : null}
        {viewTab === "library" ? (
          <div className="sticker-workspace">
            <ZoneBar
              surface={props}
              zones={zones}
              view={view}
              activeZone={lib.activeZone}
              pending={managementBusy ? lib.pending || "external" : ""}
              showRestore={!!(lib.officialInfo?.pack && !lib.officialInfo.zone)}
              onSwitch={lib.setViewZone}
              onCreate={lib.createZone}
              onRename={lib.renameZone}
              onSetDesc={lib.setZoneDesc}
              onActivate={lib.activateZone}
              onRemove={lib.removeZone}
              onRestore={lib.restoreOfficial}
            />
            <LibraryToolbar
              surface={props}
              query={lib.query}
              setQuery={lib.setQuery}
              creating={lib.creating}
              uploading={lib.uploading}
              collectBusy={lib.collectBusy}
              disabled={managementBusy}
              searchDisabled={operationBusy}
              categoryFilter={categoryFilter}
              setCategoryFilter={setCategoryFilter}
              categories={allSections.map((section) => ({
                value: sectionValue(section.group),
                label: `${section.name} (${section.total})`,
              }))}
              newName={lib.newName}
              setNewName={lib.setNewName}
              newDesc={lib.newDesc}
              setNewDesc={lib.setNewDesc}
              zipInputRef={lib.zipInputRef}
              imgInputRef={lib.imgInputRef}
              onToggleCreating={() => lib.setCreating(!lib.creating)}
              onCreate={lib.createCategory}
              onCancelCreate={lib.cancelCreate}
              onPickZip={() => lib.zipInputRef.current?.click()}
              onZipChosen={(file) => { lib.importZip(file); }}
              onImageFiles={(files) => { lib.chooseCollectedFiles(files); }}
              onExport={() => { lib.exportPack(); }}
              onRepair={() => { lib.repair(); }}
            />
              <p className="sticker-collection-count">
                {t("panel.library.visible", {
                  visible: visibleIds.length,
                  total: zoneTotal,
                  categories: sections.length,
                  defaultValue: "显示 {visible} / {total} 张 · {categories} 个分类",
                })}
              </p>
            {sections.length === 0 ? (
              <Stack gap={8}>
                <EmptyState
                  title={term || categoryFilter
                    ? t("panel.filter.empty_title", { defaultValue: "当前筛选没有命中" })
                    : t("panel.cat.empty_title", { defaultValue: "还没有分类" })}
                />
                {!term && !categoryFilter && !allSections.length ? (
                  <Button disabled={managementBusy} onClick={() => { lib.setCreating(true); }}>
                    {t("panel.group.new_button", { defaultValue: "新建分类" })}
                  </Button>
                ) : null}
              </Stack>
            ) : sections.map((section) => (
              <CategorySection
                key={section.key}
                surface={props}
                section={section}
                selected={lib.selected}
                collectBusy={lib.collectBusy}
                disabled={managementBusy}
                descEditing={lib.descEditing}
                descDraft={lib.descDraft}
                onToggleSelect={lib.toggleSelected}
                onOpen={lib.openFocus}
                onCollect={() => lib.collectInto(section.group)}
                onSelectAll={() => lib.selectSection(section.rows)}
                onToggleDescEditing={() => {
                  lib.setDescEditing(lib.descEditing === section.group ? "" : section.group);
                  lib.setDescDraft(section.desc);
                }}
                setDescDraft={lib.setDescDraft}
                onSaveDesc={lib.saveGroupDesc}
                onCancelDesc={() => lib.setDescEditing("")}
                onRemoveCategory={() => { lib.removeCategory(section); }}
              />
            ))}
          </div>
        ) : (
          <AwarenessCard
            surface={props}
            mode="settings"
            disabled={!!lib.pending || switchBusy || lib.refreshFailed}
            onBusyChange={setSettingsBusy}
            onRefreshFailed={lib.reportRefreshFailure}
            onRefreshRecovered={lib.clearRefreshFailure}
          />
        )}
      </div>
    </Page>
  );
}
