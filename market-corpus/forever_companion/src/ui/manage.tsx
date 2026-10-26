// Keep settings sections mounted so switching views preserves drafts and imports.
import { Field, Select, StatusBadge, Button, useState } from "@neko/plugin-ui"
import { TmSwitch } from "./tmswitch"
import type { HostedAction, HostedApi } from "@neko/plugin-ui"
import { BackupPanel } from "./backup"
import type { FormValues, LanlanItem, TFunc } from "./types"
import { lanlanPhaseLabel, moodBadgeTone, navigateTabs } from "./utils"

export function ManagePane(props: {
  t: TFunc
  form: FormValues
  updateForm: (patch: Partial<FormValues>) => void
  resetAll?: HostedAction
  // 受控重置入口（i18n 契约第九轮）：错误经 panel.tsx 的 errorText 翻译，
  // 不再走 ActionButton 的内联裸码展示
  onResetAll?: () => void
  resetting?: boolean
  lanlanList: LanlanItem[]
  lanlan?: string
  canPrune: boolean
  onPruneLanlan: (name: string) => void
  // 再看一次新手引导（1.2.6）：只清引导记录，不动任何配置
  onReopenGuide?: () => void
  api: HostedApi
  canBackup: boolean
  backupDisabled: boolean
  onRestored: () => Promise<void>
  children?: any
  // 页面底部的吸底保存条由 panel.tsx 注入（放在最后一张卡之后，与其它设置页同位；
  // 危险区与角色名单是"点了即生效"的区，不该被保存条压在下面）
  footer?: any
}) {
  const { t, form, updateForm, resetAll, onResetAll, lanlanList, lanlan, canPrune, onPruneLanlan, onReopenGuide } = props
  const [section, setSection] = useState("appearance")
  const sections = [
    { id: "appearance", label: t("panel.settings.viewAppearance", { defaultValue: "外观与壁纸" }) },
    { id: "general", label: t("panel.settings.sectionGeneral", { defaultValue: "通用" }) },
    { id: "data", label: t("panel.settings.viewData", { defaultValue: "数据管理" }) },
  ]

  const timezoneOptions = [
    { value: "auto", label: t("panel.settings.tzAuto", { defaultValue: "自动（跟随系统）" }) },
    { value: "Asia/Shanghai", label: "Asia/Shanghai (UTC+8)" },
    { value: "Asia/Tokyo", label: "Asia/Tokyo (UTC+9)" },
    { value: "Asia/Seoul", label: "Asia/Seoul (UTC+9)" },
    { value: "Asia/Singapore", label: "Asia/Singapore (UTC+8)" },
    { value: "Europe/London", label: "Europe/London" },
    { value: "Europe/Berlin", label: "Europe/Berlin" },
    { value: "America/New_York", label: "America/New_York" },
    { value: "America/Los_Angeles", label: "America/Los_Angeles" },
  ]

  return (
    <div className="tm-pane tm-settings">
      <div className="tm-settings-surface">
      <div className="tm-settings-nav" role="tablist" aria-label={t("panel.tab.settings", { defaultValue: "设置" })}>
        {sections.map((item) => (
          <button key={item.id} type="button" role="tab" id={`tm-settings-tab-${item.id}`}
            className="tm-settings-tab" aria-selected={section === item.id ? "true" : "false"}
            aria-controls={`tm-settings-${item.id}`} tabIndex={section === item.id ? 0 : -1}
            onKeyDown={navigateTabs} onClick={() => setSection(item.id)}>
            {item.label}
          </button>
        ))}
      </div>

      <div id="tm-settings-appearance" role="tabpanel" aria-labelledby="tm-settings-tab-appearance"
        hidden={section !== "appearance"} tabIndex={0}>
        {props.children}
      </div>

      <div id="tm-settings-general" role="tabpanel" aria-labelledby="tm-settings-tab-general"
        hidden={section !== "general"} tabIndex={0}>
      <section className="tm-settings-section">
        <h3>{t("panel.settings.sectionGeneral", { defaultValue: "通用" })}</h3>
        <div className="tm-derived">
          {t("panel.settings.sectionGlobalHint", { defaultValue: "以下设置对所有角色生效" })}
        </div>
        <Field label={t("panel.settings.timezone", { defaultValue: "时区" })} help={t("panel.settings.timezoneHelp", { defaultValue: "周期与日历按此时区计算；默认自动跟随系统" })}>
          <Select value={form.timezone} options={timezoneOptions} onChange={(v: any) => updateForm({ timezone: String(v) })} />
        </Field>
        {onReopenGuide ? (
          <div className="tm-reopen-guide">
            <span className="tm-reopen-hint">{t("panel.manage.reopenHint", { defaultValue: "再次查看初始设置流程。现有设置会保留。" })}</span>
            <Button onClick={onReopenGuide}>{t("panel.manage.reopenGuide", { defaultValue: "重新打开引导" })}</Button>
          </div>
        ) : null}
        <details className="tm-settings-disclosure">
          <summary>{t("panel.settings.advanced", { defaultValue: "高级设置" })}</summary>
          <div className="tm-settings-disclosure-body">
            <TmSwitch checked={form.debug_mode}
              label={t("panel.settings.debugMode", { defaultValue: "调试模式（供开发测试使用）" })}
              onChange={(value: boolean) => updateForm({ debug_mode: value })} />
          </div>
        </details>
      </section>
      </div>

      <div id="tm-settings-data" role="tabpanel" aria-labelledby="tm-settings-tab-data"
        hidden={section !== "data"} tabIndex={0}>
      <BackupPanel t={t} api={props.api} available={props.canBackup} disabled={props.backupDisabled} onRestored={props.onRestored} />
      {lanlanList.length > 0 ? (
        <section className="tm-settings-section">
          <h3>{t("panel.lanlan.known", { defaultValue: "已有状态的角色" })}</h3>
          <div className="tm-lanlan-list">
            {lanlanList.map((item) => {
              const name = String(item.name || "")
              const isCurrent = name !== "" && name === lanlan
              const phaseText = lanlanPhaseLabel(t, item.phase)
              return (
                <div key={name} className="tm-lanlan-row" data-orphan={item.orphan ? "1" : null}>
                  <span className="tm-lanlan-name">{name}</span>
                  {isCurrent ? (
                    <span className="tm-lanlan-current">（{t("panel.lanlan.current", { defaultValue: "当前" })}）</span>
                  ) : null}
                  <span className="tm-lanlan-meta">
                    {item.enabled === false
                      ? t("panel.lanlan.stateOff", { defaultValue: "已关闭" })
                      : t("panel.lanlan.stateOn", { defaultValue: "已开启" })}
                    {phaseText ? ` · ${phaseText}` : ""}
                  </span>
                  {item.mood_active ? (
                    <StatusBadge tone={moodBadgeTone(item.mood_action)} label={t("panel.lanlan.moodActive", { defaultValue: "情绪中" })} />
                  ) : null}
                  {item.orphan ? (
                    <>
                      <StatusBadge tone="danger" label={t("panel.lanlan.orphan", { defaultValue: "角色卡已删除" })} />
                      <span className="tm-lanlan-spacer" />
                      <Button tone="danger" disabled={!canPrune || isCurrent} onClick={() => onPruneLanlan(name)}>
                        {t("actions.prune_lanlan.label", { defaultValue: "清除残留数据" })}
                      </Button>
                    </>
                  ) : null}
                </div>
              )
            })}
          </div>
        </section>
      ) : null}
      {resetAll && onResetAll ? (
        <section className="tm-settings-section tm-settings-danger">
          <h3>{t("panel.settings.dangerZone", { defaultValue: "数据重置" })}</h3>
          <div className="tm-settings-reset-row">
            <span className="tm-derived">{t("panel.settings.resetCharacter", {
              defaultValue: "当前角色：{name}", name: lanlan || "—",
            })}</span>
            <Button tone="danger" disabled={props.resetting} onClick={onResetAll}>{t("actions.reset.label", { defaultValue: "重置状态与记录" })}</Button>
          </div>
        </section>
      ) : null}
      </div>
      </div>
      {props.footer}
    </div>
  )
}
