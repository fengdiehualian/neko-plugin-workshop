// 新手引导（1.2.6）：首次安装向导（Modal）；修订轮三扩到六步——新增
// "记住生日"（内嵌生日设置卡，与时光页同一组件同一保存通路）与
// "她的房间"（壁纸/外观引导，点按钮直达设置页）。仅 wizard_pending 时自动弹
// （引导记录带版本：老用户在新版引导改版后会被再放行一次，走完盖新版本号）；
// 完成/跳过经 set_onboarding 落盘，管理页可 reopen 再看一次。
// 向导本身不改任何配置（除第 2 步用户主动点的"开启"与第 4 步主动填的生日）——
// 锚点沿随机默认值，我们只解释"她早就有自己的节律，只是今天开始被观测"。
// hosted-tsx 约束：唯一 export 在任何 JSX 闭合标签之前；辅助组件放文件尾部靠提升
import { Button, Modal, StatusBadge } from "@neko/plugin-ui"
import { useState } from "@neko/plugin-ui"
import type { BirthdayView, ChannelStatus, Status, TFunc } from "./types"
import { BirthdaySettingsCard } from "./birthdaycard"

export function OnboardingWizard(props: {
  t: TFunc
  open: boolean
  status: Status
  channelStatus?: ChannelStatus
  canToggle: boolean
  onEnableRhythm: () => void
  // 修订轮三：生日页复用「时光」页设置卡（数据/保存通路同源）；外观页只做
  // 引导与跳转（去设置页看真实卡片），不在向导里复刻外观逻辑
  birthday?: BirthdayView
  canSaveBirthday: boolean
  onSetBirthday: (date: string, keepDiary: boolean) => Promise<boolean>
  onGotoAppearance: () => void
  onFinish: (action: "done" | "skip") => void
}) {
  const { t, open, status, channelStatus, canToggle, onEnableRhythm,
    birthday, canSaveBirthday, onSetBirthday, onGotoAppearance, onFinish } = props
  const [step, setStep] = useState(0)
  const total = 6

  if (!open) return null

  const enabled = status.enabled !== false
  const next = step < total - 1

  return (
    <Modal
      open={open}
      size="md"
      title={t("onboarding.wizard.title", { defaultValue: "欢迎使用永远的陪伴" })}
      onClose={() => onFinish("skip")}
      footer={(
        <div className="tm-ob-foot">
          <Button onClick={() => { onFinish("skip") }}>
            {t("onboarding.wizard.skip", { defaultValue: "稍后再说" })}
          </Button>
          <ObDots total={total} step={step} />
          <div className="tm-ob-foot-right">
            {step > 0 ? (
              <Button onClick={() => { setStep(step - 1) }}>
                {t("onboarding.wizard.back", { defaultValue: "上一步" })}
              </Button>
            ) : null}
            {next ? (
              <Button tone="primary" onClick={() => { setStep(step + 1) }}>
                {t("onboarding.wizard.next", { defaultValue: "下一步" })}
              </Button>
            ) : (
              <Button tone="primary" onClick={() => { onFinish("done") }}>
                {t("onboarding.wizard.finish", { defaultValue: "完成" })}
              </Button>
            )}
          </div>
        </div>
      )}
    >
      {step === 0 ? <WelStep t={t} /> : null}
      {step === 1 ? (
        <EnableStep t={t} enabled={enabled} canToggle={canToggle} onEnable={onEnableRhythm} />
      ) : null}
      {step === 2 ? <ChannelStep t={t} channelStatus={channelStatus} /> : null}
      {step === 3 ? (
        <BirthdayStep t={t} birthday={birthday} canSave={canSaveBirthday} onSave={onSetBirthday} />
      ) : null}
      {step === 4 ? <RoomStep t={t} onGoto={onGotoAppearance} /> : null}
      {step === 5 ? <DoneStep t={t} /> : null}
    </Modal>
  )
}

// ---- 以下为非导出辅助组件：函数声明提升，可写在导出之后 ----

function ObDots(props: { total: number; step: number }) {
  const dots = []
  for (let i = 0; i < props.total; i += 1) {
    dots.push(<span key={i} className={i === props.step ? "tm-ob-dot tm-ob-dot-on" : "tm-ob-dot"} />)
  }
  return <div className="tm-ob-dots">{dots}</div>
}

function WelStep(props: { t: TFunc }) {
  const { t } = props
  return (
    <div className="tm-ob-step">
      <p className="tm-ob-lead">
        {t("onboarding.wel.lead", { defaultValue: "欢迎使用永远的陪伴。这里可以查看角色状态、保存日记，回顾你们的日常互动。" })}
      </p>
      <div className="tm-ob-feats">
        <div className="tm-ob-feat">
          <span className="tm-ob-feat-name">{t("onboarding.wel.f1", { defaultValue: "情绪系统" })}</span>
          <span className="tm-ob-feat-sub">{t("onboarding.wel.f1d", { defaultValue: "角色可通过情绪工具表达心情，面板会记录状态变化。" })}</span>
        </div>
        <div className="tm-ob-feat">
          <span className="tm-ob-feat-name">{t("onboarding.wel.f2", { defaultValue: "日常状态" })}</span>
          <span className="tm-ob-feat-sub">{t("onboarding.wel.f2d", { defaultValue: "按周期提供状态参考，让聊天语气有所变化。" })}</span>
        </div>
        <div className="tm-ob-feat">
          <span className="tm-ob-feat-name">{t("onboarding.wel.f3", { defaultValue: "三本日记" })}</span>
          <span className="tm-ob-feat-sub">{t("onboarding.wel.f3d", { defaultValue: "保存角色手记、个人日记，以及后台整理的相处记录。" })}</span>
        </div>
        <div className="tm-ob-feat">
          <span className="tm-ob-feat-name">{t("onboarding.wel.f4", { defaultValue: "相处统计" })}</span>
          <span className="tm-ob-feat-sub">{t("onboarding.wel.f4d", { defaultValue: "通过里程碑、热力图和月报，回顾日常互动。" })}</span>
        </div>
      </div>
      <p className="tm-ob-note">
        {t("onboarding.wel.note", { defaultValue: "接下来会查看模拟开关、模型通道、生日和外观设置。各项功能都可以按需开启，实际回复由角色使用的模型决定。" })}
      </p>
    </div>
  )
}

function EnableStep(props: { t: TFunc; enabled: boolean; canToggle: boolean; onEnable: () => void }) {
  const { t, enabled, canToggle, onEnable } = props
  return (
    <div className="tm-ob-step">
      <p className="tm-ob-lead">
        {t("onboarding.enable.lead", { defaultValue: "模拟功能默认关闭。开启后，插件会按你的设置提供状态提示、情绪和记录功能。" })}
      </p>
      {enabled ? (
        <div className="tm-ob-state tm-ob-state-ok">
          <span className="tm-ob-check">✓</span>
          {t("onboarding.enable.on", { defaultValue: "模拟已开启" })}
        </div>
      ) : (
        <div className="tm-ob-state">
          <span className="tm-ob-state-text">{t("onboarding.enable.off", { defaultValue: "模拟未开启" })}</span>
          <Button tone="primary" disabled={!canToggle} onClick={onEnable}>
            {t("onboarding.enable.btn", { defaultValue: "现在开启" })}
          </Button>
        </div>
      )}
      <p className="tm-ob-note">
        {t("onboarding.enable.anchor", { defaultValue: "插件已随机设置周期起点。起点和周期长度可在「周期」页调整。" })}
      </p>
      <p className="tm-ob-note">
        {t("onboarding.enable.where", { defaultValue: "之后可在面板顶部随时开启或关闭模拟。" })}
      </p>
    </div>
  )
}

function ChannelStep(props: { t: TFunc; channelStatus?: ChannelStatus }) {
  const { t, channelStatus } = props
  const tone = (channelStatus && channelStatus.tone) || {}
  const fragments = (channelStatus && channelStatus.fragments) || {}
  const review = (channelStatus && channelStatus.review) || {}
  return (
    <div className="tm-ob-step">
      <p className="tm-ob-lead">
        {t("onboarding.channels.lead", { defaultValue: "以下功能需要模型支持。这里先查看通道状态；暂不可用的功能不会影响正常聊天。" })}
      </p>
      <div className="tm-ob-chans">
        <ChanRow
          t={t}
          name={t("onboarding.channels.tone", { defaultValue: "语气感知 · 分析互动语气" })}
          ch={tone}
        />
        <ChanRow
          t={t}
          name={t("onboarding.channels.fragments", { defaultValue: "对话片段 · 保存重要表达" })}
          ch={fragments}
        />
        <ChanRow
          t={t}
          name={t("onboarding.channels.review", { defaultValue: "我的日记 · 整理相处记录" })}
          ch={review}
        />
      </div>
      <p className="tm-ob-note">
        {t("onboarding.channels.note", { defaultValue: "可在「情绪」页的模型通道设置中查看详情。配置可用的模型后，相关功能会恢复。" })}
      </p>
    </div>
  )
}

function ChanRow(props: { t: TFunc; name: string; ch: { enabled?: boolean; dormant_reason?: string } }) {
  const { t, name, ch } = props
  const live = ch.enabled === true && !ch.dormant_reason
  return (
    <div className="tm-ob-chan">
      <span className="tm-ob-chan-name">{name}</span>
      <span className="tm-ob-chan-spacer" />
      {live ? (
        <StatusBadge tone="success" label={t("onboarding.channels.ok", { defaultValue: "可用" })} />
      ) : (
        <StatusBadge tone="info" label={channelStatusLabel(t, ch)} />
      )}
    </div>
  )
}

function channelStatusLabel(t: TFunc, ch: { enabled?: boolean; dormant_reason?: string }): string {
  if (!ch.enabled) return t("onboarding.channels.disabled", { defaultValue: "未开启" })
  if (ch.dormant_reason === "no_model") return t("onboarding.channels.noModel", { defaultValue: "未配置模型" })
  if (ch.dormant_reason === "free_route") return t("onboarding.channels.freeRoute", { defaultValue: "免费路由不支持直连" })
  // 熔断态复用模型通道卡那条文案：两处说的是同一件事，两份文案必然漂移出两个答案
  if (ch.dormant_reason === "rejected") return t("panel.channel.rejected", { defaultValue: "请求被拒绝 · 通道已暂停" })
  return t("onboarding.channels.dormant", { defaultValue: "暂不可用" })
}

function BirthdayStep(props: {
  t: TFunc
  birthday?: BirthdayView
  canSave: boolean
  onSave: (date: string, keepDiary: boolean) => Promise<boolean>
}) {
  const { t, birthday, canSave, onSave } = props
  const set = birthday?.set === true
  return (
    <div className="tm-ob-step">
      <p className="tm-ob-lead">
        {t("onboarding.bday.lead", { defaultValue: "可以记下你的生日。生日当天首次聊天时，插件会提醒角色；是否送上祝福，由角色决定。" })}
      </p>
      <BirthdaySettingsCard t={t} birthday={birthday} canSave={canSave} onSave={onSave} />
      <p className="tm-ob-note">
        {t("onboarding.bday.privacy", { defaultValue: "年份仅用于校验日期，不计算年龄，也不会向角色提供年龄。" })}
      </p>
      <p className="tm-ob-note">
        {set
          ? t("onboarding.bday.done", { defaultValue: "生日已保存。之后可在「时光」页修改或清除。" })
          : t("onboarding.bday.later", { defaultValue: "生日可稍后填写。未设置日期时，不会发送生日提醒。" })}
      </p>
    </div>
  )
}

function RoomStep(props: { t: TFunc; onGoto: () => void }) {
  const { t, onGoto } = props
  return (
    <div className="tm-ob-step">
      <p className="tm-ob-lead">
        {t("onboarding.room.lead", { defaultValue: "你可以为面板选择壁纸，也可以保持默认浅蓝主题。" })}
      </p>
      <p className="tm-ob-note">
        {t("onboarding.room.what", { defaultValue: "在「设置」页的面板外观中，可导入图片、选择壁纸，并预览背景和文字效果。保存后生效。" })}
      </p>
      <p className="tm-ob-note">
        {t("onboarding.room.privacy", { defaultValue: "图库图片保存在插件本地，不会由插件发送给模型服务。" })}
      </p>
      <div className="tm-ob-state">
        <span className="tm-ob-state-text">{t("onboarding.room.hint", { defaultValue: "外观设置是可选项，也可以稍后调整。" })}</span>
        <Button onClick={onGoto}>{t("onboarding.room.btn", { defaultValue: "打开外观设置" })}</Button>
      </div>
    </div>
  )
}

function DoneStep(props: { t: TFunc }) {
  const { t } = props
  return (
    <div className="tm-ob-step">
      <p className="tm-ob-lead">
        {t("onboarding.done.lead", { defaultValue: "初始设置完成。你可以开始聊天，也可以随时调整各项功能。" })}
      </p>
      <div className="tm-ob-tips">
        <div className="tm-ob-tip">{t("onboarding.done.t1", { defaultValue: "「总览」页的准备清单可以查看配置进度。" })}</div>
        <div className="tm-ob-tip">{t("onboarding.done.t2", { defaultValue: "在「功能」页查看各项功能，其他页面可调整参数、翻阅日记和查看统计。" })}</div>
        <div className="tm-ob-tip">{t("onboarding.done.t3", { defaultValue: "关闭面板顶部的模拟开关会暂停插件功能，已有记录会保留。" })}</div>
      </div>
      <p className="tm-ob-note">
        {t("onboarding.done.note", { defaultValue: "之后可在「设置」页重新打开引导。" })}
      </p>
    </div>
  )
}
