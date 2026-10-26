// 设置 · 日记功能行为（全局）：碎片捕获与我的日记的开关/门槛（放在日记页，
// 与浏览同页）。槽位选择统一在「情绪」页的模型通道卡。
import { Card, Field, NumberInput } from "@neko/plugin-ui"
import { TmSwitch } from "./tmswitch"
import type { FormValues, TFunc } from "./types"

export function DiarySettingsCard(props: {
  t: TFunc
  form: FormValues
  updateForm: (patch: Partial<FormValues>) => void
}) {
  const { t, form, updateForm } = props

  return (
    <Card title={t("panel.settings.diary", { defaultValue: "时光日记（对话片段）" })}>
      <div className="tm-derived">
        {t("panel.settings.sectionGlobalHint", { defaultValue: "以下设置对所有角色生效" })}
      </div>

      {/* 时光日记 · 自动碎片 */}
      <TmSwitch
        checked={form.fragments_enabled}
        label={t("panel.settings.fragmentsEnabled", { defaultValue: "自动保存喜好和重要表达" })}
        onChange={(value: boolean) => updateForm({ fragments_enabled: value })}
      />
      {form.fragments_enabled ? (
        <div className="tm-derived">
          {t("panel.settings.fragmentsHint", { defaultValue: "用户消息会交给配置的模型分析，片段保存在本地，供角色检索。模型可能遗漏或误判，可在时光日记中查看和删除。" })}
        </div>
      ) : null}

      <div className="tm-review-settings">
        <div className="tm-subcard-title">{t("panel.settings.stats", { defaultValue: "相处统计（时光页）" })}</div>
        <TmSwitch
          checked={form.anniversary_inject}
          label={t("settings.anniversary.title", { defaultValue: "纪念日提醒" })}
          onChange={(value: boolean) => updateForm({ anniversary_inject: value })}
        />
        <div className="tm-derived">
          {t("settings.anniversary.desc", { defaultValue: "到达 30、60 等相伴纪念节点时提醒角色，是否在聊天中提起由角色决定。" })}
        </div>
      </div>

      <div className="tm-review-settings">
        <div className="tm-subcard-title">{t("panel.settings.review", { defaultValue: "我的日记（相处记录）" })}</div>
        <TmSwitch
          checked={form.review_enabled}
          label={t("panel.settings.reviewEnabled", { defaultValue: "定期生成近期相处记录" })}
          onChange={(value: boolean) => updateForm({ review_enabled: value })}
        />
        {form.review_enabled ? (
          <>
            <div className="tm-derived">
              {t("panel.settings.reviewHint", { defaultValue: "互动素材会交给配置的模型整理，结果可能有偏差，仅供回顾。记录不自动加入角色对话或宿主记忆，也不提供角色读取工具。" })}
            </div>
            <Field
              className="tm-reserve-help"
              label={t("panel.settings.review_turns", { defaultValue: "生成条件：互动轮数" })}
              help={t("panel.settings.review_turns_help", { defaultValue: "达到互动轮数或天数条件之一即可生成" })}
            >
              <NumberInput value={form.review_turns_threshold} min={10} max={500} step={1} onChange={(v: number | string) => updateForm({ review_turns_threshold: typeof v === "number" ? v : 50 })} />
            </Field>
            <Field
              className="tm-reserve-help"
              label={t("panel.settings.review_days", { defaultValue: "生成条件：累计天数" })}
              help={t("panel.settings.review_days_help", { defaultValue: "达到设定天数且期间有聊天时生成；无互动时不生成" })}
            >
              <NumberInput value={form.review_days_threshold} min={1} max={90} step={1} onChange={(v: number | string) => updateForm({ review_days_threshold: typeof v === "number" ? v : 7 })} />
            </Field>
          </>
        ) : null}
      </div>
    </Card>
  )
}
