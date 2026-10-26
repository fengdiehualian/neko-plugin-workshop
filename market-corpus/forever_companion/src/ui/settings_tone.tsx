// 设置 · 模型通道（全局）：三个小模型槽位集中一处，各自带状态灯——
// 语气分析（默认走宿主情感端点）、碎片提取与我的日记成文（默认复用宿主 LLM 管线）。
// 每张卡两个正交旋钮：槽位 = 用宿主哪个槽的模型，通道 = 由谁去调它。
// 通道选「宿主」时零配置即用（端点、模型名、免费路由、客户端身份都由宿主给出）；
// 选「自定义」则插件读宿主本地配置自己直连该槽端点——留给要接本地模型/独立服务商
// 的空间，代价是宿主免费路由下拼不出可用端点（那些值不在存盘配置里），会休眠。
// 状态灯直说"为什么不能用/怎么办"，并如实显示这次实际走了哪条路（宿主模式回落直连时不说"正常"）。
// 行为开关（启用/频率/灵敏度）在各自功能的设置卡里，这张卡只管"走哪条模型通道"。
//
// ⚠️ 空值哨兵：UI kit 受控 Select 在 option value='' 时有 mount 竞态——select.value
// 先于 options 设置会进 dirty 态，selectedIndex 停在 -1，change 事件回传的是 option
// 的 label 文本而不是空串（实测复现）。因此"宿主默认"选项的 value 用哨兵 "__default"，
// onChange 时翻译回空串再进表单；后端校验只认空串。哨兵不会出现在表单/存储里。
import { Card, NumberInput, Select } from "@neko/plugin-ui"
import type { AgentTierUsage, ChannelStatusItem, FormValues, TFunc, ToneSlotOption } from "./types"

const DEFAULT_SLOT_SENTINEL = "__default__"

// 通道模式：与 core/state.py 的 _CHANNEL_MODE_* 一字不差
const MODE_HOST = "host"
const MODE_CUSTOM = "custom"

export function ChannelSettingsCard(props: {
  t: TFunc
  form: FormValues
  updateForm: (patch: Partial<FormValues>) => void
  toneSlotOptions?: ToneSlotOption[]
  channelStatus?: { tone?: ChannelStatusItem; fragments?: ChannelStatusItem; review?: ChannelStatusItem }
  agentUsage?: AgentTierUsage
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
  // 碎片/成文的下拉不给空串与 emotion：空串档是"跟随宿主情感端点"（五分类专用，
  // 对这两条要自定义 prompt 的通道无意义），emotion 槽只由语气分析那张卡暴露
  const directSlots = allSlots.filter((opt) => String(opt.value) !== DEFAULT_SLOT_SENTINEL && String(opt.value) !== "emotion")
  const modeOptions = [
    { value: MODE_HOST, label: t("panel.settings.channelModeHost", { defaultValue: "通过宿主" }) },
    { value: MODE_CUSTOM, label: t("panel.settings.channelModeCustom", { defaultValue: "自定义直连" }) },
  ]

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
        hint={t("panel.settings.channelToneHint", { defaultValue: "默认通过宿主调用情感模型；选择其他槽位时改为直连。互动文本可能发送给配置的模型服务。" })}
        light={channelLight(t, toneCh, form.emotion_sense_enabled)}
      >
        <Select
          value={form.tone_slot === "" ? DEFAULT_SLOT_SENTINEL : form.tone_slot}
          options={allSlots}
          onChange={(v: any) => updateForm({ tone_slot: String(v) === DEFAULT_SLOT_SENTINEL ? "" : String(v) })}
        />
      </ChannelRow>

      <div className="tm-channel-divider" />

      {/* 碎片提取：默认复用宿主管线，可切自定义直连 */}
      <ChannelRow
        t={t}
        label={t("panel.settings.channelFragments", { defaultValue: "对话片段提取" })}
        hint={channelHint(t, form.fragments_mode)}
        light={channelLight(t, fragCh, form.fragments_enabled, form.fragments_mode || MODE_HOST)}
      >
        <Select value={form.fragments_slot} options={directSlots} onChange={(v: any) => updateForm({ fragments_slot: String(v) })} />
        <Select
          value={form.fragments_mode || MODE_HOST}
          options={modeOptions}
          onChange={(v: any) => updateForm({ fragments_mode: String(v) })}
        />
      </ChannelRow>

      {/* 我的日记成文：与碎片提取同款通道 */}
      <ChannelRow
        t={t}
        label={t("panel.settings.channelReview", { defaultValue: "我的日记生成" })}
        hint={channelHint(t, form.review_mode)}
        light={channelLight(t, reviewCh, form.review_enabled, form.review_mode || MODE_HOST)}
      >
        <Select value={form.review_slot} options={directSlots} onChange={(v: any) => updateForm({ review_slot: String(v) })} />
        <Select
          value={form.review_mode || MODE_HOST}
          options={modeOptions}
          onChange={(v: any) => updateForm({ review_mode: String(v) })}
        />
      </ChannelRow>

      <div className="tm-channel-divider" />

      {/* agent 档节流（1.3.3 修订轮）：碎片与成文只要走 agent 槽就合记一份当日计数
          （按角色分账），用完休眠到次日。这里给的是全局预算旋钮 + 当前角色的已用读数；
          300 秒间隔地板刻意不开放配置（它防的是自动节拍砸配额，用户没有调它的理由），
          只在说明里讲清"还有一道在代码里"。5/150/20 三个字面量与后端同值，
          由 test_ui_budget_input_range_matches_backend_bounds / 出厂值同源门钉住 */}
      <div className="tm-derived">
        {t("panel.settings.agentBudgetHelp", { defaultValue: "对话片段和我的日记使用 agent 模型时，共享此上限，按角色分别计数；用完后次日恢复。可设为 5～150 次，建议 5～50 次。调用间隔至少 300 秒，宿主服务还可能有额外额度限制。" })}
      </div>
      <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
        <span>{t("panel.settings.agentBudget", { defaultValue: "agent 模型每日调用上限" })}</span>
        <NumberInput
          value={form.agent_daily_budget}
          min={5}
          max={150}
          step={1}
          onChange={(v: number | string) => updateForm({ agent_daily_budget: typeof v === "number" ? v : 20 })}
        />
        <span>
          {t("panel.settings.agentBudgetUsed", {
            defaultValue: "今日已用 {used}/{budget} 次",
            used: String(props.agentUsage?.used ?? 0),
            budget: String(props.agentUsage?.budget ?? form.agent_daily_budget),
          })}
        </span>
      </div>

      {/* 免费路由说明只在"直连这条路确实因为免费路由而不通"时说：自定义模式选了
          免费路由，或宿主模式回落到直连后仍拼不出端点——其余状态各灯自己解释 */}
      {isFreeRoute(fragCh) || isFreeRoute(reviewCh) ? (
        <div className="tm-channel-warn">
          {t("panel.settings.freeRouteWarn", { defaultValue: "免费路由不支持自定义直连。可选择通过宿主使用 agent 模型（受宿主调用额度限制），或在宿主中配置自己的模型服务。" })}
        </div>
      ) : null}
    </Card>
  )
}

// 通道说明文案：两种模式的代价/收益不同，说清各自那一句
function channelHint(t: TFunc, mode: string | undefined): string {
  return (mode || MODE_HOST) === MODE_CUSTOM
    ? t("panel.settings.channelHintCustom", { defaultValue: "读取宿主本地模型配置，直接调用所选服务。未配置模型或使用免费路由时，直连功能不可用。" })
    : t("panel.settings.channelHintHost", { defaultValue: "通过宿主模型通道调用，默认使用 agent 槽位。可用性取决于宿主配置和服务权限；通道不可用时可能尝试直连。" })
}

// 通道行：名称 + 状态灯 + 说明 + 槽位/通道下拉
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

// 通道 → 状态灯（cls 控配色，text 是一句话状态）。mode = 这条通道用户选的走法：
// 只有"选了宿主管线却实际落在直连上"才是一次该如实说的回落。语气分析没有 mode
// 旋钮（槽位留空走宿主端点、选别的槽本就是插件直连），调用方不传 mode 即不参与该判定。
function channelLight(
  t: TFunc,
  ch: ChannelStatusItem | undefined,
  featureOn: boolean,
  mode?: string,
): { cls: string; text: string } {
  if (!featureOn) {
    return { cls: "tm-light-off", text: t("panel.channel.off", { defaultValue: "未启用" }) }
  }
  if (!ch) return { cls: "tm-light-ok", text: t("panel.channel.ok", { defaultValue: "正常" }) }
  if (ch.dormant_reason === "free_route") {
    return { cls: "tm-light-dormant", text: t("panel.channel.freeRoute", { defaultValue: "免费路由不支持直连" }) }
  }
  if (ch.dormant_reason === "no_model") {
    return { cls: "tm-light-dormant", text: t("panel.channel.noModel", { defaultValue: "未配置模型" }) }
  }
  // 端点解析得到、真发出去却被服务端连着拒 → 已熔断（不再发请求）。这一档必须单独
  // 有灯：1.3.3 实机就是"解析成功=绿灯 + 15 次静默失败"，用户看不出任何异常
  if (ch.dormant_reason === "rejected") {
    return { cls: "tm-light-dormant", text: t("panel.channel.rejected", { defaultValue: "请求被拒绝 · 通道已暂停" }) }
  }
  // 当日 agent 档预算用完：这是我们自己按下不发（不是故障），说明与"改路"无关，
  // 所以不和 rejected 共用文案，免得用户以为服务端把他拒了
  if (ch.dormant_reason === "budget") {
    return { cls: "tm-light-dormant", text: t("panel.channel.budget", { defaultValue: "今日额度已用完 · 次日恢复" }) }
  }
  if (ch.dormant_reason === "disabled") {
    return { cls: "tm-light-off", text: t("panel.channel.off", { defaultValue: "未启用" }) }
  }
  // 选的是宿主通道却实际在直连（宿主模块没给到端点）：能干活，但不是用户选的那条路，
  // 如实说——否则"正常"灯会藏住一次静默回落。反之 mode=custom 走 direct 是用户自己
  // 选的，报"回落"就等于把一次正常配置说成故障，所以这里必须带上 mode 判据。
  if (mode === MODE_HOST && ch.transport === "direct") {
    return { cls: "tm-light-dormant", text: t("panel.channel.fellBackDirect", { defaultValue: "宿主通道不可用 · 已改用直连" }) }
  }
  return { cls: "tm-light-ok", text: t("panel.channel.ok", { defaultValue: "正常" }) }
}

// 这条通道此刻是不是"直连被免费路由挡住"（免费路由警告只在这种状态下才说得出道理）
function isFreeRoute(ch: ChannelStatusItem | undefined): boolean {
  return !!ch && ch.dormant_reason === "free_route"
}
