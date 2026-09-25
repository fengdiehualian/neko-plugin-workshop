// 顶部状态条（精简版）：她是谁 · 今天状态一句话 · 心情胶囊 · 总开关。
// 详细状态（月相环/天数/情绪详情）都在「总览」页——点击状态条任意空白跳转总览。
// hosted-tsx 约束：唯一 export 在任何 JSX 闭合标签之前；辅助组件放文件尾部靠函数声明提升
import { StatusBadge, Tooltip } from "@neko/plugin-ui"
import { TmSwitch } from "./tmswitch"
import type { Mood, Status, TFunc } from "./types"
import { moodBadgeTone, moodDotColor, moodWordOf, stripEmoji, valenceScore, arousalScore, phaseColorOf } from "./utils"

type StatusBarProps = {
  t: TFunc
  status: Status
  mood: Mood
  lanlan?: string
  canToggle: boolean
  // 返回 Promise：resolve false（确认取消/失败）时开关动画回落（见 tmswitch 乐观回滚契约）
  onToggle: () => any
  onGotoOverview: () => void
  // 关闭状态细提示（卡片内第二行）：模拟关闭→行内直接开启；情绪关闭→跳「情绪」页
  showOffHint: boolean
  showMoodHint: boolean
  onGotoMood: () => void
}

export function StatusBar(props: StatusBarProps) {
  const { t, status, mood, lanlan, canToggle, onToggle, onGotoOverview, showOffHint, showMoodHint, onGotoMood } = props

  const enabled = status.enabled !== false
  const phaseColor = phaseColorOf(status)
  // 一句话状态：开启时 = 阶段名 + 周期第 N 天；关闭时只有"已关闭"
  const summary = enabled
    ? `${status.phase_label || "-"} · ${t("panel.ring.cycleDayN", { n: String(status.cycle_day ?? "-"), defaultValue: "周期第 {n} 天" })}`
    : t("panel.off", { defaultValue: "已关闭" })

  return (
    <div className="tm-statusbar tm-statusbar-slim">
      <button type="button" className="tm-status-main" onClick={onGotoOverview} title={t("panel.overview.gotoTip", { defaultValue: "点击查看总览" })}>
        <span className="tm-status-dot" style={{ background: phaseColor }} />
        {lanlan ? <span className="tm-status-name">{lanlan}</span> : null}
        <span className="tm-status-summary" style={{ color: enabled ? phaseColor : undefined }}>{summary}</span>
      </button>
      <div className="tm-status-right">
        {enabled && mood.system_enabled !== false && mood.affect ? (
          <MoodPill t={t} valence={mood.affect.valence} arousal={mood.affect.arousal} />
        ) : null}
        {enabled && mood.system_enabled !== false && mood.active ? (
          mood.reason ? (
            <Tooltip content={mood.reason} placement="bottom">
              <StatusBadge tone={moodBadgeTone(mood.action)} label={stripEmoji(mood.action_label) || mood.action || ""} />
            </Tooltip>
          ) : (
            <StatusBadge tone={moodBadgeTone(mood.action)} label={stripEmoji(mood.action_label) || mood.action || ""} />
          )
        ) : null}
        {/* 总开关（1.3.1）：原「关闭模拟/开启模拟」按钮换成动画开关，
            动作文案保留在悬停 title（也钉住 i18n 键引用面） */}
        <TmSwitch
          checked={enabled}
          disabled={!canToggle}
          small
          title={enabled ? t("panel.turnOff", { defaultValue: "关闭模拟" }) : t("panel.turnOn", { defaultValue: "开启模拟" })}
          onChange={() => onToggle()}
        />
      </div>
      {showOffHint || showMoodHint ? (
        <div className="tm-status-hints">
          {showOffHint ? (
            <span className="tm-offhint-item">
              {t("panel.offHintState", { defaultValue: "模拟已关闭" })}
              <span className="tm-offhint-dot">·</span>
              <button type="button" className="tm-offhint-link" disabled={!canToggle} onClick={onToggle}>
                {t("panel.offHintAction", { defaultValue: "点击开启" })}
              </button>
            </span>
          ) : null}
          {showMoodHint ? (
            <span className="tm-offhint-item">
              {t("panel.moodOffHintState", { defaultValue: "情绪系统已关闭" })}
              <span className="tm-offhint-dot">·</span>
              <button type="button" className="tm-offhint-link" onClick={onGotoMood}>
                {t("panel.moodOffHintAction", { defaultValue: "去「情绪」页开启" })}
              </button>
            </span>
          ) : null}
        </div>
      ) : null}
    </div>
  )
}

// 连续心情胶囊：心情词 + 底色随 valence + arousal 呼吸动画（从旧状态栏原样保留——
// 它是顶栏唯一"活的"信号，也是陪伴感的常驻提示）
function MoodPill(props: { key?: string; t: TFunc; valence: number; arousal: number }) {
  const { t, valence, arousal } = props
  const v = Math.max(-1, Math.min(1, Number(valence) || 0))
  const a = Math.max(0, Math.min(1, Number(arousal) || 0))
  const duration = `${(3.4 - a * 2.2).toFixed(2)}s`
  const haloSize = Math.round(16 + a * 12)
  const word = t(moodWordOf(v, a), { defaultValue: "平静" })
  const title = `${word} · ${t("panel.mood.gauge.valence", { defaultValue: "愉悦度" })} ${valenceScore(v)} · ${t("panel.mood.gauge.arousal", { defaultValue: "活跃度" })} ${arousalScore(a)}`
  return (
    <span className="tm-mood-pill" title={title} style={{ background: moodDotColor(v) }}>
      <span className="tm-mood-pill-word">{word}</span>
      <span
        className="tm-mood-pill-halo"
        style={{ width: `${haloSize}px`, height: `${haloSize}px`, animationDuration: duration }}
      />
    </span>
  )
}
