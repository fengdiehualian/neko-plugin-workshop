// 设置 · 情绪系统（全局）：允许她自主使用限时情绪动作
import { Card, Field, NumberInput } from "@neko/plugin-ui"
import { TmSwitch } from "./tmswitch"
import type { FormValues, TFunc } from "./types"

export function MoodSettingsCard(props: {
  t: TFunc
  form: FormValues
  updateForm: (patch: Partial<FormValues>) => void
}) {
  const { t, form, updateForm } = props

  return (
    <Card title={t("panel.settings.sectionMood", { defaultValue: "情绪系统" })}>
      <div className="tm-derived">
        {t("panel.settings.sectionGlobalHint", { defaultValue: "以下设置对所有角色生效" })}
      </div>
      <TmSwitch
        checked={form.mood_enabled}
        label={t("panel.settings.moodEnabled", { defaultValue: "情绪系统（实验性：允许她自主使用冷战沉默等情绪动作）" })}
        onChange={(value: boolean) => updateForm({ mood_enabled: value })}
      />
      {form.mood_enabled ? (
        <Field className="tm-reserve-help" label={t("panel.settings.actionMinutes", { defaultValue: "限时情绪动作默认时长（分钟）" })}>
          <NumberInput value={form.default_action_minutes} min={1} max={720} step={1} onChange={(v: number | string) => updateForm({ default_action_minutes: typeof v === "number" ? v : 20 })} />
        </Field>
      ) : null}
    </Card>
  )
}
