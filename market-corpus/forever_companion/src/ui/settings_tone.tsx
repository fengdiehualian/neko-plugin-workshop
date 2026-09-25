// 设置 · 模型通道（全局）：三个小模型槽位集中一处，各自带状态灯——
// 语气分析（默认走宿主情感端点）、碎片提取与我的日记成文（插件直连槽位）。
// 状态灯直说"为什么不能用/怎么办"：免费路由 = 宿主免费端点只认 N.E.K.O 客户端，
// 插件直连必被拒；未配模型 = 去宿主设置给该槽配模型。行为开关（启用/频率/灵敏度）
// 在各自功能的设置卡里，这张卡只管"走哪条模型通道"。
//
// ⚠️ 空值哨兵：UI kit 受控 Select 在 option value='' 时有 mount 竞态——select.value
// 先于 options 设置会进 dirty 态，selectedIndex 停在 -1，change 事件回传的是 option
// 的 label 文本而不是空串（实测复现）。因此"宿主默认"选项的 value 用哨兵 "__default"，
// onChange 时翻译回空串再进表单；后端校验只认空串。哨兵不会出现在表单/存储里。
import { Card, Select } from "@neko/plugin-ui"
import type { ChannelStatusItem, FormValues, TFunc, ToneSlotOption } from "./types"

const DEFAULT_SLOT_SENTINEL = "__default__"

export function ChannelSettingsCard(props: {
  t: TFunc
  form: FormValues
  updateForm: (patch: Partial<FormValues>) => void
  toneSlotOptions?: ToneSlotOption[]
  channelStatus?: { tone?: ChannelStatusItem; fragments?: ChannelStatusItem; review?: ChannelStatusItem }
}) {
  const { t, form, updateForm } = props

  // 槽位下拉：label = i18n 槽位显示名 + （拿到宿主配置时）· 当前模型名；后端失败时静态兜底
  const slotFallback = ["", "conversation", "summary", "correction", "emotion", "vision", "agent"]
  const allSlots = (Array.isArray(props.toneSlotOptions) && props.toneSlotOptions.length
    ? props.toneSlotOptions
    : slotFallback.map((value) => ({ value, model: "" }))
  ).map((opt) => {
    const value = String(opt.value || "")
    const name = t(`panel.settings.tone_slot_names.${value || "default"}`, { defaultValue: value || "default" })
    const model = String(opt.model || "").trim()
    return {
      // 空值选项用哨兵，规避 UI kit Select 的 value='' mount 竞态（见文件头注释）
      value: value === "" ? DEFAULT_SLOT_SENTINEL : value,
      label: model ? `${name} · ${model}` : name,
    }
  })
  // 直连槽位（碎片/成文）不能选 emotion 与空串：宿主情感端点只做五分类
  const directSlots = allSlots.filter((opt) => String(opt.value) !== DEFAULT_SLOT_SENTINEL && String(opt.value) !== "emotion")

  const cs = props.channelStatus || {}
  const toneCh = cs.tone
  const fragCh = cs.fragments
  const reviewCh = cs.review

  return (
    <Card title={t("panel.settings.channels", { defaultValue: "模型通道" })}>
      <div className="tm-derived">
        {t("panel.settings.sectionGlobalHint", { defaultValue: "以下设置对所有角色生效" })}
      </div>

      {/* 语气分析：默认跟随宿主情感模型（经宿主端点调用） */}
      <ChannelRow
        t={t}
        label={t("panel.settings.channelTone", { defaultValue: "语气分析" })}
        hint={t("panel.settings.channelToneHint", { defaultValue: "默认跟随宿主情感模型（经宿主端点调用，免费路由下也可用）；选其他槽位时改为插件直连" })}
        light={channelLight(t, toneCh, form.emotion_sense_enabled)}
      >
        <Select
          value={form.tone_slot === "" ? DEFAULT_SLOT_SENTINEL : form.tone_slot}
          options={allSlots}
          onChange={(v: any) => updateForm({ tone_slot: String(v) === DEFAULT_SLOT_SENTINEL ? "" : String(v) })}
        />
      </ChannelRow>

      <div className="tm-channel-divider" />

      {/* 碎片提取：插件直连 */}
      <ChannelRow
        t={t}
        label={t("panel.settings.channelFragments", { defaultValue: "碎片提取" })}
        hint={t("panel.settings.channelFragmentsHint", { defaultValue: "插件直连该槽端点（读取宿主本地配置），槽位无模型时自动休眠" })}
        light={channelLight(t, fragCh, form.fragments_enabled)}
      >
        <Select value={form.fragments_slot} options={directSlots} onChange={(v: any) => updateForm({ fragments_slot: String(v) })} />
      </ChannelRow>

      {/* 我的日记成文：插件直连 */}
      <ChannelRow
        t={t}
        label={t("panel.settings.channelReview", { defaultValue: "我的日记成文" })}
        hint={t("panel.settings.channelReviewHint", { defaultValue: "插件直连该槽端点（读取宿主本地配置），槽位无模型时自动休眠" })}
        light={channelLight(t, reviewCh, form.review_enabled)}
      >
        <Select value={form.review_slot} options={directSlots} onChange={(v: any) => updateForm({ review_slot: String(v) })} />
      </ChannelRow>

      {/* 免费路由统一说明：任一直连通道是 free_route 时显示 */}
      {isFreeRoute(fragCh) || isFreeRoute(reviewCh) ? (
        <div className="tm-channel-warn">
          {t("panel.settings.freeRouteWarn", { defaultValue: "宿主正在使用免费路由：免费端点只接受 N.E.K.O 客户端调用，插件直连通道不可用。在宿主设置里配置自己的 API 服务商后即可启用。" })}
        </div>
      ) : null}
    </Card>
  )
}

// 通道行：名称 + 状态灯 + 说明 + 槽位下拉
function ChannelRow(props: { t: TFunc; label: string; hint: string; light: { cls: string; text: string }; children?: any }) {
  const { label, hint, light, children } = props
  return (
    <div className="tm-channel-row">
      <div className="tm-channel-head">
        <span className="tm-channel-label">{label}</span>
        <span className={`tm-channel-light ${light.cls}`} title={light.text}>{light.text}</span>
      </div>
      <div className="tm-derived">{hint}</div>
      {children ? <div className="tm-channel-control">{children}</div> : null}
    </div>
  )
}

// 通道 → 状态灯（cls 控配色，text 是一句话状态）
function channelLight(t: TFunc, ch: ChannelStatusItem | undefined, featureOn: boolean): { cls: string; text: string } {
  if (!featureOn) {
    return { cls: "tm-light-off", text: t("panel.channel.off", { defaultValue: "未启用" }) }
  }
  if (!ch) return { cls: "tm-light-ok", text: t("panel.channel.ok", { defaultValue: "正常" }) }
  if (ch.dormant_reason === "free_route") {
    return { cls: "tm-light-dormant", text: t("panel.channel.freeRoute", { defaultValue: "免费路由 · 不可用" }) }
  }
  if (ch.dormant_reason === "no_model") {
    return { cls: "tm-light-dormant", text: t("panel.channel.noModel", { defaultValue: "槽位未配模型 · 休眠" }) }
  }
  if (ch.dormant_reason === "disabled") {
    return { cls: "tm-light-off", text: t("panel.channel.off", { defaultValue: "未启用" }) }
  }
  return { cls: "tm-light-ok", text: t("panel.channel.ok", { defaultValue: "正常" }) }
}

function isFreeRoute(ch: ChannelStatusItem | undefined): boolean {
  return !!ch && ch.dormant_reason === "free_route"
}
