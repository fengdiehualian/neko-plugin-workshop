// 设置 · 语气感知（全局）：回复后异步分析互动情绪，只提醒不自动改状态。
// 槽位选择在「模型通道」卡；这张卡只管行为（启用/分析间隔/抽查频率/阶段灵敏度）。
import { Card, Field, Grid, NumberInput, Select } from "@neko/plugin-ui"
import { TmSwitch } from "./tmswitch"
import type { FormValues, TFunc } from "./types"

export function EmotionSenseSettingsCard(props: {
  t: TFunc
  form: FormValues
  updateForm: (patch: Partial<FormValues>) => void
}) {
  const { t, form, updateForm } = props

  const toneCheckRates = [
    { value: "1", label: t("panel.settings.toneCheckRateEvery", { defaultValue: "每轮" }) },
    { value: "0.5", label: t("panel.settings.toneCheckRateHalf", { defaultValue: "约一半" }) },
    { value: "0.25", label: t("panel.settings.toneCheckRateRare", { defaultValue: "偶尔" }) },
    { value: "0", label: t("panel.settings.toneCheckRateOff", { defaultValue: "关闭检查" }) },
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
            {t("panel.settings.emotionSenseHint", { defaultValue: "回复完成后在后台分析，只发送提醒，不自动改变情绪状态" })}
          </div>
          <Grid cols={2}>
            <Field className="tm-reserve-help" label={t("panel.settings.toneMinIntervalSec", { defaultValue: "分析间隔（秒）" })}>
              <NumberInput value={form.tone_min_interval_sec} min={10} max={3600} step={1} onChange={(v: number | string) => updateForm({ tone_min_interval_sec: typeof v === "number" ? v : 20 })} />
            </Field>
            <Field className="tm-reserve-help" label={t("panel.settings.toneCheckRate", { defaultValue: "语气检查频率" })}>
              <Select value={String(form.tone_check_rate)} options={toneCheckRates} onChange={(v: any) => updateForm({ tone_check_rate: Number(v) })} />
            </Field>
            {form.tone_phase_sensitivity_enabled ? (
              <Field className="tm-reserve-help" label={t("panel.settings.tonePhaseSensitivity", { defaultValue: "灵敏度调整幅度" })}>
                <Select value={String(form.tone_phase_sensitivity)} options={toneSensitivities} onChange={(v: any) => updateForm({ tone_phase_sensitivity: Number(v) })} />
              </Field>
            ) : null}
          </Grid>
          <TmSwitch
            checked={form.tone_phase_sensitivity_enabled}
            label={t("panel.settings.tonePhaseSensitivityEnabled", { defaultValue: "随阶段调整语气灵敏度" })}
            onChange={(value: boolean) => updateForm({ tone_phase_sensitivity_enabled: value })}
          />
        </>
      ) : null}
    </Card>
  )
}
