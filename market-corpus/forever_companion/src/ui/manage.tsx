// 设置页：通用设置（时区/调试模式）+ 面板外观 + 危险区（重置全部数据）
// + 已有状态的角色名单（孤儿清理）。全局杂项归位页——原「管理」页。
import { Card, Field, Select, StatusBadge, Button } from "@neko/plugin-ui"
import { TmSwitch } from "./tmswitch"
import type { HostedAction } from "@neko/plugin-ui"
import type { FormValues, LanlanItem, TFunc } from "./types"
import { lanlanPhaseLabel, moodBadgeTone } from "./utils"

export function ManagePane(props: {
  t: TFunc
  form: FormValues
  updateForm: (patch: Partial<FormValues>) => void
  resetAll?: HostedAction
  // 受控重置入口（i18n 契约第九轮）：错误经 panel.tsx 的 errorText 翻译，
  // 不再走 ActionButton 的内联裸码展示
  onResetAll?: () => void
  lanlanList: LanlanItem[]
  lanlan?: string
  canPrune: boolean
  onPruneLanlan: (name: string) => void
  // 再看一次新手引导（1.2.6）：只清引导记录，不动任何配置
  onReopenGuide?: () => void
  children?: any
}) {
  const { t, form, updateForm, resetAll, onResetAll, lanlanList, lanlan, canPrune, onPruneLanlan, onReopenGuide } = props

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
    <div className="tm-pane">
      {/* 通用：时区与调试（跟保存条走，但独立成卡放设置页） */}
      <Card title={t("panel.settings.sectionGeneral", { defaultValue: "通用" })}>
        <div className="tm-derived">
          {t("panel.settings.sectionGlobalHint", { defaultValue: "以下设置对所有角色生效" })}
        </div>
        <Field label={t("panel.settings.timezone", { defaultValue: "时区" })} help={t("panel.settings.timezoneHelp", { defaultValue: "周期与日历按此时区计算；默认自动跟随系统" })}>
          <Select value={form.timezone} options={timezoneOptions} onChange={(v: any) => updateForm({ timezone: String(v) })} />
        </Field>
        <TmSwitch
          checked={form.debug_mode}
          label={t("panel.settings.debugMode", { defaultValue: "调试模式（注册 debug_* 调试入口，把内部机制快进到秒级可验证）" })}
          onChange={(value: boolean) => updateForm({ debug_mode: value })}
        />
        {onReopenGuide ? (
          <div className="tm-reopen-guide">
            <span className="tm-reopen-hint">{t("panel.manage.reopenHint", { defaultValue: "想重新走一遍首次配置向导？（不会改动任何设置）" })}</span>
            <Button onClick={onReopenGuide}>{t("panel.manage.reopenGuide", { defaultValue: "再看一次新手引导" })}</Button>
          </div>
        ) : null}
      </Card>

      {/* 面板外观由 panel.tsx 以 children 注入（保持背景图状态在顶层管理） */}
      {props.children}

      <Card title={t("panel.settings.dangerZone", { defaultValue: "危险区" })}>
        {resetAll && onResetAll ? (
          <Button tone="danger" onClick={onResetAll}>{t("actions.reset.label", { defaultValue: "重置全部数据" })}</Button>
        ) : null}
      </Card>

      {lanlanList.length > 0 ? (
        <Card title={t("panel.lanlan.known", { defaultValue: "已有状态的角色" })}>
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
        </Card>
      ) : null}
    </div>
  )
}
