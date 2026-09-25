// 总览页 · 陪伴仪表盘：她的现在（月相环 + 当前心情 + 情绪状态）
// + 近况与相处（三本日记速览 + 相处信号）。顶栏瘦身后所有"看状态"的需求都在这页。
// hosted-tsx 约束：唯一 export 在任何 JSX 闭合标签之前；辅助组件放文件尾部靠函数声明提升
import { Card, StatusBadge, Tooltip } from "@neko/plugin-ui"
import type { Mood, Onboarding, ReviewBrief, Settings, Status, TFunc } from "./types"
import { arousalColor, arousalScore, moodDotColor, moodWordOf, stripEmoji, valenceBarColor, valenceScore, phaseColorOf } from "./utils"
import { RingStatus } from "./ring"
import { GuideCard } from "./guidecard"

export function OverviewPane(props: {
  t: TFunc
  status: Status
  mood: Mood
  settings: Settings
  lanlan?: string
  diaryTotal: number
  fragmentTotal: number
  journalIndex: Array<{ entry_count?: number }>
  reviewBrief?: ReviewBrief
  weekActivity?: number
  // 新手引导/就绪清单（1.2.6）：清单数据随 dashboard 即时下发，零新增拉取
  onboarding?: Onboarding
  canEnable?: boolean
  onEnable?: () => void
  onGoto: (tab: string) => void
}) {
  const {
    t, status, mood, settings, lanlan,
    diaryTotal, fragmentTotal, journalIndex, reviewBrief, weekActivity,
    onboarding, canEnable, onEnable, onGoto,
  } = props

  const enabled = status.enabled !== false
  const phaseColor = phaseColorOf(status)
  const journalCount = journalIndex.length
  const selfCount = diaryTotal - fragmentTotal

  return (
    <div className="tm-pane">
      {/* 配置引导（1.2.6）：还差什么一直提醒，全就绪自动收起 */}
      <GuideCard
        t={t}
        readiness={onboarding ? onboarding.readiness : undefined}
        canEnable={canEnable !== false}
        onEnable={onEnable || (() => {})}
        onGoto={onGoto}
      />

      {/* ---- 她的现在 ---- */}
      <Card title={t("panel.overview.now", { defaultValue: "她的现在" })}>
        <div className="tm-ov-hero">
          <RingStatus t={t} status={status} settings={settings} />
          <div className="tm-ov-hero-text">
            {lanlan ? <div className="tm-ov-name">{lanlan}</div> : null}
            <div className="tm-phase-label" style={{ color: phaseColor }}>
              {enabled ? (status.phase_label || "-") : t("panel.off", { defaultValue: "已关闭" })}
            </div>
            <div className="tm-status-sub">
              {enabled
                ? `${t("panel.ring.cycleDayN", { n: String(status.cycle_day ?? "-"), defaultValue: "周期第 {n} 天" })} · ${t("panel.ring.untilTideN", { n: String(status.days_until_next_period ?? "-"), defaultValue: "距下次潮汐还有 {n} 天" })}`
                : t("panel.offSub", { defaultValue: "模拟已关闭，她暂时不会感受身体节律" })}
            </div>
            {enabled && mood.system_enabled !== false && mood.active && mood.affect ? (
              <MoodChip t={t} mood={mood} />
            ) : null}
          </div>
        </div>
      </Card>

      {/* 当前心情（从情绪页迁来：看状态不是改设置） */}
      {enabled && mood.system_enabled !== false && mood.affect ? (
        <MoodSummaryCard t={t} mood={mood} />
      ) : null}

      {/* ---- 近况与相处 ---- */}
      <Card title={t("panel.overview.recent", { defaultValue: "近况与相处" })}>
        <div className="tm-ov-diary-grid">
          {/* 三本日记速览：点卡片跳日记页 */}
          <button type="button" className="tm-ov-tile" onClick={() => onGoto("diary")}>
            <span className="tm-ov-tile-num">{diaryTotal}</span>
            <span className="tm-ov-tile-label">{t("panel.overview.tileDiary", { defaultValue: "时光日记" })}</span>
            <span className="tm-ov-tile-sub">
              {t("panel.overview.tileDiarySub", { defaultValue: "她写 {n} · 碎片 {m}" })
                .replace("{n}", String(selfCount))
                .replace("{m}", String(fragmentTotal))}
            </span>
          </button>
          <button type="button" className="tm-ov-tile" onClick={() => onGoto("diary")}>
            <span className="tm-ov-tile-num">{journalCount}</span>
            <span className="tm-ov-tile-label">{t("panel.overview.tileJournal", { defaultValue: "个人日记" })}</span>
            <span className="tm-ov-tile-sub">
              {t("panel.journal.entryCount", { defaultValue: "{n} 段" })
                .replace("{n}", String(journalIndex.reduce((sum: number, p: { entry_count?: number }) => sum + (p.entry_count || 0), 0)))}
            </span>
          </button>
          <button type="button" className="tm-ov-tile" onClick={() => onGoto("diary")}>
            <span className="tm-ov-tile-num">{reviewBrief ? (reviewBrief.entries || 0) : 0}</span>
            <span className="tm-ov-tile-label">{t("panel.overview.tileReview", { defaultValue: "我的日记" })}</span>
            <span className="tm-ov-tile-sub">
              {reviewBrief && reviewBrief.turns_threshold
                ? t("panel.overview.tileReviewSub", { defaultValue: "下一篇 {n}/{m} 轮" })
                    .replace("{n}", String(reviewBrief.progress_turns || 0))
                    .replace("{m}", String(reviewBrief.turns_threshold))
                : ""}
            </span>
          </button>
        </div>

        {/* 相处信号卡：陪伴型功能的第一块可视化阵地，未来新信号都长在这里 */}
        <CompanionSignalsCard
          t={t}
          weekActivity={weekActivity || 0}
          reviewBrief={reviewBrief}
        />
      </Card>
    </div>
  )
}

// 情绪状态小条（总览 hero 右侧）：生效动作徽标 + 剩余时间
function MoodChip(props: { t: TFunc; mood: Mood }) {
  const { t, mood } = props
  const label = stripEmoji(mood.action_label) || mood.action || ""
  const positive = mood.action === "warm_current" || mood.action === "spring_tide"
  const badge = (
    <StatusBadge tone={positive ? "success" : "warning"} label={label} />
  )
  let remaining = ""
  if (mood.expires_at) {
    const minutes = Math.max(0, Math.round((mood.expires_at * 1000 - Date.now()) / 60000))
    remaining = t("panel.overview.moodRemaining", { defaultValue: "约 {n} 分钟后缓和" }).replace("{n}", String(minutes))
  }
  return (
    <div className="tm-ov-mood-chip">
      {mood.reason ? (
        <Tooltip content={mood.reason} placement="bottom">{badge}</Tooltip>
      ) : badge}
      {remaining ? <span className="tm-status-sub">{remaining}</span> : null}
    </div>
  )
}

// 当前心情卡（愉悦/活跃双条 + 大字心情词）——从 moodgauge.tsx 的卡片简化而来，
// 总览版保留读数语义；两根条都与 0~100 分制同向：从左端填到当前分值位置
//（愉悦度静息态 50 分 = 半条，与活跃度观感一致），填充色按正负取暖琥珀/灰蓝
function MoodSummaryCard(props: { t: TFunc; mood: Mood }) {
  const { t, mood } = props
  const v = Math.max(-1, Math.min(1, Number(mood.affect && mood.affect.valence) || 0))
  const a = Math.max(0, Math.min(1, Number(mood.affect && mood.affect.arousal) || 0))
  const word = t(moodWordOf(v, a), { defaultValue: "平静" })
  const wordColor = moodDotColor(v)
  const vScore = valenceScore(v)
  return (
    <Card title={t("panel.mood.gauge.title", { defaultValue: "当前心情" })}>
      <div className="tm-ov-mood">
        <span className="tm-ov-mood-word" style={{ color: wordColor }}>{word}</span>
        <div className="tm-ov-mood-bars">
          <MiniBar
            label={t("panel.mood.gauge.valence", { defaultValue: "愉悦度" })}
            valueText={String(vScore)}
            left="0%"
            width={`${vScore.toFixed(1)}%`}
            background={valenceBarColor(v)}
          />
          <MiniBar
            label={t("panel.mood.gauge.arousal", { defaultValue: "活跃度" })}
            valueText={String(arousalScore(a))}
            left="0%"
            width={`${(a * 100).toFixed(1)}%`}
            background={arousalColor(a)}
          />
        </div>
      </div>
    </Card>
  )
}

// 相处信号卡：近 7 天活跃 + 我的日记素材进度——陪伴型功能的可视化阵地
function CompanionSignalsCard(props: { t: TFunc; weekActivity: number; reviewBrief?: ReviewBrief }) {
  const { t, weekActivity, reviewBrief } = props
  const turns = reviewBrief ? (reviewBrief.progress_turns || 0) : 0
  const threshold = reviewBrief ? (reviewBrief.turns_threshold || 50) : 50
  const ratio = Math.min(1, threshold > 0 ? turns / threshold : 0)
  return (
    <div className="tm-ov-signals">
      <div className="tm-ov-signal">
        <span className="tm-ov-signal-label">{t("panel.overview.weekActivity", { defaultValue: "近 7 天相处" })}</span>
        <span className="tm-ov-signal-value">
          {t("panel.overview.weekActivityValue", { defaultValue: "{n} 条时光记录" }).replace("{n}", String(weekActivity))}
        </span>
      </div>
      <div className="tm-ov-signal">
        <span className="tm-ov-signal-label">{t("panel.overview.nextReview", { defaultValue: "下一篇我的日记" })}</span>
        <span className="tm-ov-signal-value">
          {t("panel.overview.nextReviewValue", { defaultValue: "{n} / {m} 轮" })
            .replace("{n}", String(turns))
            .replace("{m}", String(threshold))}
        </span>
        <div className="tm-review-bar">
          <div className="tm-review-bar-fill" style={{ width: `${Math.round(ratio * 100)}%` }} />
        </div>
      </div>
    </div>
  )
}

// 心情卡的小进度条（label + 数值 + 一根条，填充从左端到当前分值）；填充给最小
// 可见宽度，零值时也能看到一个色点，不至于整条灰掉。
// 不画中心刻度线：愉悦度静息态恰好 50 分，填充右端与中心刻度重叠会显出一条
// 深色竖线，看起来像渲染异常（刻度语义已由右侧读数承担）
function MiniBar(props: { label: string; valueText: string; left: string; width: string; background: string }) {
  const { label, valueText, left, width, background } = props
  const minWidth = 3 // px：零值时也能看到一个色点，不至于整条灰掉
  return (
    <div className="tm-ov-minibar">
      <span className="tm-ov-minibar-label">{label}</span>
      <div className="tm-ov-minibar-track">
        <div className="tm-ov-minibar-fill" style={{ left, width, minWidth: `${minWidth}px`, background }} />
      </div>
      <span className="tm-ov-minibar-value">{valueText}</span>
    </div>
  )
}
