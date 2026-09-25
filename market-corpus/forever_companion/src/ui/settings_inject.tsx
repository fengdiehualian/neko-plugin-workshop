// 设置 · 状态注入（全局）：身体状态以何种频率/方式进入对话上下文。
// 时区在「设置」页；这张卡只管注入行为。周期参数（角色独立）在「周期」页的她周期卡。
import { Card, Field, Grid, NumberInput, Select } from "@neko/plugin-ui"
import { TmSwitch } from "./tmswitch"
import type { FormValues, TFunc } from "./types"

export function InjectSettingsCard(props: {
  t: TFunc
  form: FormValues
  updateForm: (patch: Partial<FormValues>) => void
}) {
  const { t, form, updateForm } = props

  const injectModes = [
    { value: "every_user_message", label: t("panel.settings.modeEvery", { defaultValue: "每次消息" }) },
    { value: "interval_n", label: t("panel.settings.modeInterval", { defaultValue: "每 N 条消息" }) },
    { value: "on_trigger", label: t("panel.settings.modeTrigger", { defaultValue: "关键词触发" }) },
    { value: "off", label: t("panel.settings.modeOff", { defaultValue: "关闭注入" }) },
  ]

  return (
    <Card title={t("panel.settings.sectionInject", { defaultValue: "状态注入" })}>
      <div className="tm-derived">
        {t("panel.settings.sectionGlobalHint", { defaultValue: "以下设置对所有角色生效" })}
      </div>
      <Grid cols={2}>
        <Field label={t("panel.settings.injectMode", { defaultValue: "注入策略" })} help={t("panel.settings.injectModeHelp", { defaultValue: "身体状态以何种频率注入对话上下文" })}>
          <Select value={form.inject_mode} options={injectModes} onChange={(v: any) => updateForm({ inject_mode: String(v) })} />
        </Field>
        {form.inject_mode === "interval_n" ? (
          <Field className="tm-reserve-help" label={t("panel.settings.intervalN", { defaultValue: "每 N 条消息注入一次" })}>
            <NumberInput value={form.inject_interval_n} min={1} max={50} step={1} onChange={(v: number | string) => updateForm({ inject_interval_n: typeof v === "number" ? v : 3 })} />
          </Field>
        ) : null}
      </Grid>
      <TmSwitch
        checked={form.phase_openers}
        label={t("panel.settings.phaseOpeners", { defaultValue: "阶段开场白（进入新阶段时她主动说一句，每阶段仅一次）" })}
        onChange={(value: boolean) => updateForm({ phase_openers: value })}
      />
    </Card>
  )
}
