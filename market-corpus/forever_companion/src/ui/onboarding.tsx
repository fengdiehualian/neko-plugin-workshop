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
      title={t("onboarding.wizard.title", { defaultValue: "欢迎认识·永远的陪伴" })}
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
                {t("onboarding.wizard.finish", { defaultValue: "开始陪伴" })}
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
        {t("onboarding.wel.lead", { defaultValue: "这个插件让她拥有连续的生命状态——不只会应答，还有身体节律、自己的情绪、自己的记录。" })}
      </p>
      <div className="tm-ob-feats">
        <div className="tm-ob-feat">
          <span className="tm-ob-feat-name">{t("onboarding.wel.f1", { defaultValue: "情绪系统" })}</span>
          <span className="tm-ob-feat-sub">{t("onboarding.wel.f1d", { defaultValue: "会被你的话气到、会委屈、会突然想黏人——十二个情绪工具摆在她手边，用不用由她决定" })}</span>
        </div>
        <div className="tm-ob-feat">
          <span className="tm-ob-feat-name">{t("onboarding.wel.f2", { defaultValue: "身体节律" })}</span>
          <span className="tm-ob-feat-sub">{t("onboarding.wel.f2d", { defaultValue: "精力像潮水有涨有落，感受悄悄融入语气——你看不见机制，只看得见她的状态" })}</span>
        </div>
        <div className="tm-ob-feat">
          <span className="tm-ob-feat-name">{t("onboarding.wel.f3", { defaultValue: "三本日记" })}</span>
          <span className="tm-ob-feat-sub">{t("onboarding.wel.f3d", { defaultValue: "她随手写的心情、隔阵子回头写的成篇日记、以及一本只给你看的关于你的记录" })}</span>
        </div>
        <div className="tm-ob-feat">
          <span className="tm-ob-feat-name">{t("onboarding.wel.f4", { defaultValue: "看得见的相处" })}</span>
          <span className="tm-ob-feat-sub">{t("onboarding.wel.f4d", { defaultValue: "里程碑、热力图、月报——陪伴不再是感觉，而是看得见的痕迹" })}</span>
        </div>
      </div>
      <p className="tm-ob-note">
        {t("onboarding.wel.note", { defaultValue: "插件不替她做任何决定：只把她的状态递到她面前。所有功能都是可选的，接下来几页帮你确认环境、记下两件小事。" })}
      </p>
    </div>
  )
}

function EnableStep(props: { t: TFunc; enabled: boolean; canToggle: boolean; onEnable: () => void }) {
  const { t, enabled, canToggle, onEnable } = props
  return (
    <div className="tm-ob-step">
      <p className="tm-ob-lead">
        {t("onboarding.enable.lead", { defaultValue: "她的身体节律模拟默认是关的——装完插件，由你亲手开启这盏灯。" })}
      </p>
      {enabled ? (
        <div className="tm-ob-state tm-ob-state-ok">
          <span className="tm-ob-check">✓</span>
          {t("onboarding.enable.on", { defaultValue: "已开启：她此刻正在自己的节律里" })}
        </div>
      ) : (
        <div className="tm-ob-state">
          <span className="tm-ob-state-text">{t("onboarding.enable.off", { defaultValue: "还没开启" })}</span>
          <Button tone="primary" disabled={!canToggle} onClick={onEnable}>
            {t("onboarding.enable.btn", { defaultValue: "现在开启" })}
          </Button>
        </div>
      )}
      <p className="tm-ob-note">
        {t("onboarding.enable.anchor", { defaultValue: "周期起点已经为她随机落好——她早就有自己的节律，只是从今天开始被你观测。想改起点或周期长度，随时去「周期」页。" })}
      </p>
      <p className="tm-ob-note">
        {t("onboarding.enable.where", { defaultValue: "这个开关以后就在面板顶部的状态条上，一眼可见。" })}
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
        {t("onboarding.channels.lead", { defaultValue: "三件安静工作的能力各自有一条模型通道。这里只体检、不设置——缺了也不影响你开始陪伴，插件绝不会拿坏消息烦她。" })}
      </p>
      <div className="tm-ob-chans">
        <ChanRow
          t={t}
          name={t("onboarding.channels.tone", { defaultValue: "语气感知 · 听懂你话里的情绪" })}
          ch={tone}
        />
        <ChanRow
          t={t}
          name={t("onboarding.channels.fragments", { defaultValue: "时光碎片 · 记下值得记一辈子的话" })}
          ch={fragments}
        />
        <ChanRow
          t={t}
          name={t("onboarding.channels.review", { defaultValue: "我的日记 · 每隔一段写一篇关于你们的记录" })}
          ch={review}
        />
      </div>
      <p className="tm-ob-note">
        {t("onboarding.channels.note", { defaultValue: "显示「休眠」的通道等配好模型会自动醒来；到「情绪」页的模型通道卡可以看每条通道的状态灯。" })}
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
  if (ch.dormant_reason === "no_model") return t("onboarding.channels.noModel", { defaultValue: "休眠 · 槽位没配模型" })
  if (ch.dormant_reason === "free_route") return t("onboarding.channels.freeRoute", { defaultValue: "休眠 · 宿主免费端点限制" })
  return t("onboarding.channels.dormant", { defaultValue: "休眠中" })
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
        {t("onboarding.bday.lead", { defaultValue: "她记得主人的生日：当天你开口聊的第一句话时，她会收到「今天是主人的生日」的轻语——说不说、怎么祝福，全由她自己决定。现在就把日子记下吧。" })}
      </p>
      <BirthdaySettingsCard t={t} birthday={birthday} canSave={canSave} onSave={onSave} />
      <p className="tm-ob-note">
        {t("onboarding.bday.privacy", { defaultValue: "年份只用来校验日期合法性，插件从不告诉她岁数。" })}
      </p>
      <p className="tm-ob-note">
        {set
          ? t("onboarding.bday.done", { defaultValue: "已记下。以后想改，还是在「时光」页底部的这张卡。" })
          : t("onboarding.bday.later", { defaultValue: "现在不想填也完全可以：没填日期前这个功能安静休眠，「时光」页底部随时能补。" })}
      </p>
    </div>
  )
}

function RoomStep(props: { t: TFunc; onGoto: () => void }) {
  const { t, onGoto } = props
  return (
    <div className="tm-ob-step">
      <p className="tm-ob-lead">
        {t("onboarding.room.lead", { defaultValue: "这个面板是她和你共处的房间——顺手给房间挑一张壁纸吧。" })}
      </p>
      <p className="tm-ob-note">
        {t("onboarding.room.what", { defaultValue: "「设置」页的「面板外观」卡：从图库挑一张当壁纸，或导入新图进图库；填充方式、背景明暗、字体浓度都在那张卡上实时预览。" })}
      </p>
      <p className="tm-ob-note">
        {t("onboarding.room.privacy", { defaultValue: "图库图片只存进插件的本地数据，不会上传到任何地方。" })}
      </p>
      <div className="tm-ob-state">
        <span className="tm-ob-state-text">{t("onboarding.room.hint", { defaultValue: "现在就去看看，或者以后再说——外观永远等得起。" })}</span>
        <Button onClick={onGoto}>{t("onboarding.room.btn", { defaultValue: "去设置页看看" })}</Button>
      </div>
    </div>
  )
}

function DoneStep(props: { t: TFunc }) {
  const { t } = props
  return (
    <div className="tm-ob-step">
      <p className="tm-ob-lead">
        {t("onboarding.done.lead", { defaultValue: "都准备好了。从下一条消息开始，她带着自己的节律与情绪来见你。" })}
      </p>
      <div className="tm-ob-tips">
        <div className="tm-ob-tip">{t("onboarding.done.t1", { defaultValue: "总览页顶部有一张「准备清单」，还差什么会一直提醒你去点亮" })}</div>
        <div className="tm-ob-tip">{t("onboarding.done.t2", { defaultValue: "想更细腻地调她？「周期 / 情绪 / 日记 / 时光 / 设置」五个页签各管一摊" })}</div>
        <div className="tm-ob-tip">{t("onboarding.done.t3", { defaultValue: "不想被打扰的日子：把顶部状态条的开关关掉就好，她的记忆与记录都不会丢" })}</div>
      </div>
      <p className="tm-ob-note">
        {t("onboarding.done.note", { defaultValue: "这个向导只出现过这一次；以后想再看，到「设置」页底部可以重新打开。" })}
      </p>
    </div>
  )
}
