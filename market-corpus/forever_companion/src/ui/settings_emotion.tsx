// 设置 · 语气感知（全局）：回复后异步分析互动情绪，只提醒不自动改状态。
// 槽位选择在「模型通道」卡；这张卡只管行为（启用/抽查频率/阶段灵敏度）。
import { Card, Field, Grid, Select } from "@neko/plugin-ui"
import { TmSwitch } from "./tmswitch"
import type { FormValues, TFunc } from "./types"

export function EmotionSenseSettingsCard(props: {
  t: TFunc
  form: FormValues
  updateForm: (patch: Partial<FormValues>) => void
}) {
  const { t, form, updateForm } = props

  const toneCheckRates = [
    { value: "1", label: t("panel.settings.toneCheckRateEvery", { defaultValue: "每轮都查" }) },
    { value: "0.5", label: t("panel.settings.toneCheckRateHalf", { defaultValue: "一半" }) },
    { value: "0.25", label: t("panel.settings.toneCheckRateRare", { defaultValue: "偶尔" }) },
    { value: "0", label: t("panel.settings.toneCheckRateOff", { defaultValue: "关闭筛选" }) },
  ]
  const toneSensitivities = [
    { value: "0.1", label: t("panel.settings.toneSensitivityGentle", { defaultValue: "温和" }) },
    { value: "0.15", label: t("panel.settings.toneSensitivityModerate", { defaultValue: "适中" }) },
    { value: "0.25", label: t("panel.settings.toneSensitivitySensitive", { defaultValue: "敏感" }) },
  ]

  return (
    <Card title={t("panel.settings.emotionSense", { defaultValue: "语气感知" })}>
      <div className="tm-derived">
        {t("panel.settings.sectionGlobalHint", { defaultValue: "以下设置对所有角色生效" })}
      </div>
      <TmSwitch
        checked={form.emotion_sense_enabled}
        label={t("panel.settings.emotionSenseEnabled", { defaultValue: "启用语气感知" })}
        onChange={(value: boolean) => updateForm({ emotion_sense_enabled: value })}
      />
      {form.emotion_sense_enabled ? (
        <>
          <div className="tm-derived">
            {t("panel.settings.emotionSenseHint", { defaultValue: "分析在她回复完成后异步进行，不影响聊天延迟；只提醒，不会自动改变状态" })}
          </div>
          <Grid cols={2}>
            <Field className="tm-reserve-help" label={t("panel.settings.toneCheckRate", { defaultValue: "筛选抽查频率" })}>
              <Select value={String(form.tone_check_rate)} options={toneCheckRates} onChange={(v: any) => updateForm({ tone_check_rate: Number(v) })} />
            </Field>
            {form.tone_phase_sensitivity_enabled ? (
              <Field className="tm-reserve-help" label={t("panel.settings.tonePhaseSensitivity", { defaultValue: "阶段灵敏度强度" })}>
                <Select value={String(form.tone_phase_sensitivity)} options={toneSensitivities} onChange={(v: any) => updateForm({ tone_phase_sensitivity: Number(v) })} />
              </Field>
            ) : null}
          </Grid>
          <TmSwitch
            checked={form.tone_phase_sensitivity_enabled}
            label={t("panel.settings.tonePhaseSensitivityEnabled", { defaultValue: "阶段灵敏度（潮汐期/回升期/活跃期筛选更灵敏）" })}
            onChange={(value: boolean) => updateForm({ tone_phase_sensitivity_enabled: value })}
          />
        </>
      ) : null}
    </Card>
  )
}
