// 时光页（1.1.0）· 相处统计：数字摘要（hero 大数字 + 纪念日进度环）
// + 相处热力图（GitHub 式日历年视图：格子从首条互动日长起，非当年可 ‹ › 翻年份）
// + 我们的一月（月报，可翻历史月份）+ 相处徽章（收藏墙）+ 生日设置卡（同页最底部）。
// hosted-tsx 约束：唯一 export 排在任何 JSX 闭合标签之前；辅助组件放文件尾部靠函数声明提升
import { Card, EmptyState, StatusBadge, useRef } from "@neko/plugin-ui"
import type { BirthdayView, HeatDay, Heatmap, MonthReport, StatsBadge, StatsSummary, TFunc } from "./types"
import { heatLevel, monthLabel, moodDotColor, toneLabelKey } from "./utils"
import { BirthdaySettingsCard } from "./birthdaycard"

export function MomentPane(props: {
  t: TFunc
  lanlan?: string
  summary?: StatsSummary
  badges?: StatsBadge[]
  heatmap: Heatmap | null
  month: MonthReport | null
  monthAvailable: string[]
  monthLoading: boolean
  onPickMonth: (month: string) => void
  onPickYear: (year: string) => void
  // 生日设置卡（1.3.1）：dashboard 的 birthday 视图 + 保存回调（复用 update_settings）
  birthday?: BirthdayView
  canSaveBirthday?: boolean
  onSetBirthday?: (date: string, keepDiary: boolean) => Promise<boolean>
}) {
  const { t, lanlan, summary, badges, heatmap, month, monthAvailable, monthLoading, onPickMonth, onPickYear,
    birthday, canSaveBirthday, onSetBirthday } = props

  // 不做整体空态早退：热力图无数据时卡内渲染空态、徽章全部锁定占位，
  // 页面结构与有数据时完全一致（GitHub 式），装完即知会长成什么样
  return (
    <div className="tm-pane">
      {/* ---- 相处里程碑：hero 数字 + 统计卡 ---- */}
      <Card title={t("panel.stats.milestones", { defaultValue: "相处里程碑" })}>
        <SummaryHero t={t} summary={summary} lanlan={lanlan} />
      </Card>

      {/* ---- 相处热力图 ---- */}
      <Card title={t("panel.stats.heatmap", { defaultValue: "相处热力图" })} className="tm-heat-card">
        <HeatGrid t={t} heatmap={heatmap} onPickYear={onPickYear} />
      </Card>

      {/* ---- 我们的一月 ---- */}
      <Card title={t("panel.stats.monthReport", { defaultValue: "我们的一月" })}>
        <MonthNav
          t={t}
          month={month}
          monthAvailable={monthAvailable}
          monthLoading={monthLoading}
          onPickMonth={onPickMonth}
        />
        <MonthReportView t={t} month={month} />
      </Card>

      {/* ---- 相处徽章：收藏墙（翻到底部正好收获） ---- */}
      <Card title={t("panel.stats.badgeWall", { defaultValue: "相处徽章" })}>
        <BadgeWall t={t} badges={badges} daysTogether={summary ? summary.days_together : undefined} />
      </Card>

      {/* ---- 生日设置卡：时光页最底部（1.3.1 改版：展示型倒数退役，纯设置项压页尾） ---- */}
      <BirthdaySettingsCard
        t={t}
        birthday={birthday}
        canSave={canSaveBirthday !== false}
        onSave={onSetBirthday || (async () => false)}
      />
    </div>
  )
}

// hero 数字区：相伴天数大数字 + 纪念日进度环（纯 CSS 圆环，conic-gradient 填充）+ 其余统计玻璃小卡。
// 进度环语义：从上一个节点到下一个纪念日节点的旅程完成度，到节点当天闭合。
function SummaryHero(props: { t: TFunc; summary?: StatsSummary; lanlan?: string }) {
  const { t, summary, lanlan } = props
  const s = summary || {}
  const days = Number(s.days_together ?? 0)
  const untilNext = Number(s.next_anniversary_in ?? 0)
  // 节点节奏与后端 next_anniversary 一致：30 的倍数或 365 的倍数
  const nextNode = days + untilNext
  const prevNode = lastAnniversaryNode(days)
  // days=0（还没有相处记录）时环不显示，progress 归 0 兜底
  const progress = days > 0 ? (nextNode > prevNode ? (days - prevNode) / (nextNode - prevNode) : 1) : 0
  const ringPct = Math.max(0, Math.min(1, progress)) * 100
  const ringStyle = { background: `conic-gradient(rgb(245, 176, 77) ${ringPct}%, rgba(148, 163, 184, 0.22) 0)` }
  const daysHint = lanlan
    ? t("panel.stats.sum.daysHint", { defaultValue: "自你们的第一句互动起算（跟随 {n}）" }).replace("{n}", String(lanlan))
    : t("panel.stats.sum.daysHintPlain", { defaultValue: "自你们的第一句互动起算" })
  const cells = [
    {
      key: "turns",
      value: String(s.total_turns ?? 0),
      label: t("panel.stats.sum.turns", { defaultValue: "互动轮数" }),
      hint: t("panel.stats.sum.turnsHint", { defaultValue: "你发给她的消息总数" }),
    },
    {
      key: "streak",
      value: String(s.current_streak ?? 0),
      label: t("panel.stats.sum.streak", { defaultValue: "当前连续" }),
      hint: t("panel.stats.sum.streakHint", { defaultValue: "最长连续 {n} 天" }).replace("{n}", String(s.longest_streak ?? 0)),
    },
    {
      key: "fight",
      value: t("panel.stats.sum.fightValue", { defaultValue: "{c} / {m}" })
        .replace("{c}", String(s.cold_wars ?? 0))
        .replace("{m}", String(s.made_ups ?? 0)),
      label: t("panel.stats.sum.fight", { defaultValue: "冷战 / 和好" }),
      hint: t("panel.stats.sum.fightHint", { defaultValue: "只数她自己起的情绪；和好 = 她主动转晴" }),
    },
    {
      key: "warm",
      value: String(s.warm_moments ?? 0),
      label: t("panel.stats.sum.warm", { defaultValue: "开心时刻" }),
      hint: t("panel.stats.sum.warmHint", { defaultValue: "她自己起的暖流与满潮" }),
    },
  ]
  return (
    <div className="tm-hero-card">
      <div className="tm-hero">
        <AdaptiveTip content={daysHint} place="bottom">
          <div className="tm-hero-days">
            <span className="tm-hero-days-value">{days}</span>
            <span className="tm-hero-days-label">
              {t("panel.stats.sum.days", { defaultValue: "相伴天数" })}
            </span>
          </div>
        </AdaptiveTip>
        {days > 0 ? (
          <AdaptiveTip
            content={t("panel.stats.sum.nextHint", { defaultValue: "满 30/100/… 天的日子，她当天会知道" })}
            place="bottom"
          >
            <div className="tm-hero-ring">
              <div className="tm-hero-ring-track" style={ringStyle}>
                <div className="tm-hero-ring-inner">
                  <span className="tm-hero-ring-num">{untilNext}</span>
                  <span className="tm-hero-ring-unit">{t("panel.days", { defaultValue: "天" })}</span>
                </div>
              </div>
              <span className="tm-hero-ring-label">{t("panel.stats.sum.next", { defaultValue: "距纪念日" })}</span>
            </div>
          </AdaptiveTip>
        ) : null}
      </div>
      <div className="tm-stat-strip">
        {cells.map((item) => (
          <AdaptiveTip key={item.key} content={item.hint} place="bottom">
            <div className="tm-stat-cell">
              <span className="tm-stat-value">{item.value}</span>
              <span className="tm-stat-label">{item.label}</span>
            </div>
          </AdaptiveTip>
        ))}
      </div>
    </div>
  )
}

// 上一个纪念日节点：与 next_anniversary 同一节奏（30/365 的倍数），用于进度环起算
function lastAnniversaryNode(days: number): number {
  if (days < 30) return 0
  let node = 0
  for (let d = 30; d <= days; d++) {
    if (d % 30 === 0 || d % 365 === 0) node = d
  }
  return node
}

// 徽章墙兜底数据：dashboard 的 stats_summary 异常兜底路径会返回空 badges，
// 用与后端同构的 8 枚标准徽章占位（全锁定态），避免页底卡片空壳
const FALLBACK_BADGES: StatsBadge[] = [
  { id: "d7", days: 7, unlocked: false, date: "" },
  { id: "d30", days: 30, unlocked: false, date: "" },
  { id: "d100", days: 100, unlocked: false, date: "" },
  { id: "d365", days: 365, unlocked: false, date: "" },
  { id: "d730", days: 730, unlocked: false, date: "" },
  { id: "first_diary", unlocked: false, date: "" },
  { id: "first_journal", unlocked: false, date: "" },
  { id: "first_review", unlocked: false, date: "" },
]

// 徽章墙（成就卡片式 2.0）：2 列横排卡片，左图标右文案。蓝色系月光/潮汐/羽毛意象；
// 天数类按难度升级月相（新月→半月→满月→星拱月→潮汐月），事件类专属图形（羽毛笔/书页/信封）；
// 未解锁整卡灰化剪影 + 相伴天数进度条，解锁后月光蓝点亮 + 微光晕
function BadgeWall(props: { t: TFunc; badges?: StatsBadge[]; daysTogether?: number }) {
  const { t, badges, daysTogether } = props
  const list = (badges && badges.length) ? badges : FALLBACK_BADGES
  const cur = Math.max(0, Number(daysTogether || 0))
  return (
    <div className="tm-badge-wall">
      {list.map((badge) => {
        const unlocked = !!badge.unlocked
        const label = badgeLabel(t, badge)
        const sub = unlocked
          ? t("panel.stats.badge.unlockedOn", { defaultValue: "{d} 解锁" }).replace("{d}", String(badge.date || "—"))
          : (badge.days
              ? t("panel.stats.badge.inDays", { defaultValue: "还差 {n} 天" }).replace("{n}", String(badge.days))
              : t("panel.stats.badge.locked", { defaultValue: "还未发生" }))
        const icon = badgeShape(badge)
        const tip = unlocked
          ? t("panel.stats.badge.unlockedOn", { defaultValue: "{d} 解锁" }).replace("{d}", String(badge.date || "—"))
          : t("panel.stats.badge.lockedTip", { defaultValue: "继续相处，它会在某一天悄悄点亮" })
        const pct = unlocked ? 100 : Math.min(100, Math.round((cur / Number(badge.days)) * 100))
        return (
          <AdaptiveTip key={badge.id || label} content={`${label} · ${tip}`}>
            <div className={`tm-ach ${unlocked ? "tm-ach-on" : ""}`}>
              <span className="tm-ach-icon"><BadgeGlyph id={icon} /></span>
              <span className="tm-ach-body">
                <span className="tm-ach-name">{label}</span>
                <span className="tm-ach-sub">{sub}</span>
                {badge.days ? (
                  <span className="tm-ach-bar"><span className="tm-ach-bar-fill" style={{ width: `${pct}%` }} /></span>
                ) : null}
              </span>
            </div>
          </AdaptiveTip>
        )
      })}
    </div>
  )
}

// 徽章 id → 图标档位（天数越高月相越圆满：新月→半月→满月→星拱月→潮汐月）
function badgeShape(badge: StatsBadge): string {
  const id = String(badge.id || "")
  if (badge.days) {
    if (badge.days >= 730) return "tide" // 潮汐月：新月 + 涌动的潮水线（相伴如潮）
    if (badge.days >= 365) return "orbit" // 星拱月：满月 + 环拱星光（一整年的星光）
    if (badge.days >= 100) return "full" // 满月：光晕圆满（百日圆满）
    if (badge.days >= 30) return "half" // 半月（初见轮廓）
    return "crescent" // 新月（初识的一弯）
  }
  if (id === "first_diary") return "quill"
  if (id === "first_journal") return "book"
  if (id === "first_review") return "letter"
  return "crescent"
}

// 徽章图形（纯 CSS 结构，配色由样式层按 lit/dim 两态控制 currentColor 与容器底色）：
// 月相五档（相伴天数）+ 羽毛笔/书页/信封（三本日记的「第一次」）
function BadgeGlyph(props: { id: string }) {
  const { id } = props
  if (id === "crescent") {
    return <span className="tm-glyph tm-glyph-crescent" />
  }
  if (id === "half") {
    return <span className="tm-glyph tm-glyph-half" />
  }
  if (id === "full") {
    return <span className="tm-glyph tm-glyph-full" />
  }
  if (id === "orbit") {
    return (
      <span className="tm-glyph tm-glyph-orbit">
        <span className="tm-glyph-full" />
      </span>
    )
  }
  if (id === "tide") {
    return (
      <span className="tm-glyph tm-glyph-tide">
        <span className="tm-glyph-crescent" />
        <span className="tm-glyph-wave tm-glyph-w1" />
        <span className="tm-glyph-wave tm-glyph-w2" />
      </span>
    )
  }
  if (id === "quill") {
    return (
      <span className="tm-glyph tm-glyph-quill">
        <span className="tm-glyph-quill-nib" />
        <span className="tm-glyph-quill-body" />
      </span>
    )
  }
  if (id === "book") {
    return (
      <span className="tm-glyph tm-glyph-book">
        <span className="tm-glyph-book-page tm-glyph-book-left" />
        <span className="tm-glyph-book-page tm-glyph-book-right" />
        <span className="tm-glyph-book-line tm-glyph-book-l1" />
        <span className="tm-glyph-book-line tm-glyph-book-l2" />
      </span>
    )
  }
  if (id === "letter") {
    return (
      <span className="tm-glyph tm-glyph-letter">
        <span className="tm-glyph-letter-flap" />
        <span className="tm-glyph-letter-seal" />
      </span>
    )
  }
  return null
}

// 自适应悬停提示（AdaptiveTip，body 门户单例）：tip 是挂在 document.body 的
// fixed 元素（在面板渲染器管辖之外的原生 DOM），悬停瞬间量取触发元视口坐标直接定位。
// 为什么必须挂 body 而非渲染在组件树里：.tm-heat-scroll 的 container-type 与
// .neko-card 的 backdrop-filter 都会劫持 fixed 后代的包含块——写入的视口坐标被
// 浏览器按容器内坐标解释（实测偏移 ~180px），再叠加卡片 overflow:hidden 裁剪，
// 就出现"时隐时现/飞到奇怪位置"。body 无任何 transform/filter 祖先，fixed 即真
// 视口坐标系且不被任何后代容器裁剪。上方空间不足自动向下翻、左右缘自动收拢；
// place 仅作首选方向。面板内任何滚动立即隐藏（fixed 不随滚动容器移动）。
let __tipEl: any = null
function __bodyTip(): any {
  if (__tipEl && __tipEl.isConnected) return __tipEl
  __tipEl = document.createElement("span")
  __tipEl.className = "tm-tip"
  __tipEl.style.display = "none"
  document.body.appendChild(__tipEl)
  return __tipEl
}
function hideBodyTip() {
  if (!__tipEl) return
  __tipEl.style.display = "none"
  window.removeEventListener("scroll", hideBodyTip, true)
}
function AdaptiveTip(props: { key?: string; content: string; place?: string; children?: any }) {
  const ref = useRef(null)
  const show = () => {
    const el = ref.current as HTMLElement | null
    if (!el || !props.content) return
    const r = el.getBoundingClientRect()
    const vw = window.innerWidth || 800
    const below = (props.place || "top") === "bottom" || r.top < 72
    const x = Math.max(8, Math.min(vw - 8, r.left + r.width / 2))
    const y = below ? r.bottom + 7 : r.top - 7
    const tip = __bodyTip()
    tip.textContent = props.content
    tip.className = `tm-tip${below ? " tm-tip-below" : ""}`
    tip.style.left = `${x}px`
    tip.style.top = `${y}px`
    tip.style.display = ""
    window.addEventListener("scroll", hideBodyTip, true)
  }
  return (
    <span ref={ref as any} className="tm-tip-wrap" onMouseEnter={show} onMouseLeave={hideBodyTip}>
      {props.children}
    </span>
  )
}

// 徽章 id → 展示文案（天数类带数字）
function badgeLabel(t: TFunc, badge: StatsBadge): string {
  const id = String(badge.id || "")
  if (badge.days) {
    return t("panel.stats.badge.days", { defaultValue: "相伴 {n} 天" }).replace("{n}", String(badge.days))
  }
  const map: Record<string, string> = {
    first_diary: t("panel.stats.badge.firstDiary", { defaultValue: "第一篇手记" }),
    first_journal: t("panel.stats.badge.firstJournal", { defaultValue: "第一页日记" }),
    first_review: t("panel.stats.badge.firstReview", { defaultValue: "第一篇我的日记" }),
  }
  return map[id] || id
}

// 热力图（GitHub 贡献图布局 · 日历年视图）：列 = 周，行 = 星期几（周一起 7 行）。
// 窗口由后端下发（start/end）：起点 = max(首条互动日, 视图年 1 月 1 日)——相识之前
// 的日子不铺格，新装只有几格、随天数生长；终点 = min(12 月 31 日, 昨天)，今天明天才亮格。
// 视图年不是当年时可 ‹ › 翻年份（复用月报导航样式，仅一年数据时隐藏）。
// 窄网格切换弹性公式档位（tm-gh-w12/w26），格子不被 /49 公式压到 7px 下限。
// 月份标签只标包含新月首日的那一列（顶部），左侧标一/三/五。
// 格子深浅（4 档蓝）= 当天互动轮数；窗口内无记录天 = 浅灰格；窗口外不渲染（透明占位保持列高）。
// 还没有可展示日子时卡内渲染空态，不再铺默认网格
// hosted-tsx 无 SVG：格子用 div + 档位色；悬停 Tooltip 显示当日明细
function HeatGrid(props: { t: TFunc; heatmap: Heatmap | null; onPickYear: (year: string) => void }) {
  const { t, heatmap, onPickYear } = props
  const days: HeatDay[] = (heatmap && heatmap.days) || []
  const years: number[] = (heatmap && heatmap.years) || []
  const curYear = Number((heatmap && heatmap.year) || 0)
  const start = String((heatmap && heatmap.start) || "")
  const end = String((heatmap && heatmap.end) || "")
  const byDate: Record<string, HeatDay> = {}
  days.forEach((day) => {
    if (day && day.date) byDate[String(day.date)] = day
  })
  const showGrid = !!start && !!end && start <= end
  const weekList = showGrid ? buildWeekColumns(start, end) : []
  const weeks = weekList.map((week) => ({
    ...week,
    monthLabel: week.monthNo ? t("panel.stats.monthShort", { defaultValue: "{m}月" }).replace("{m}", String(week.monthNo)) : "",
  }))
  const sizeClass = weekList.length <= 12 ? " tm-gh-w12" : weekList.length <= 26 ? " tm-gh-w26" : ""
  const idx = years.indexOf(curYear)
  const prevYear = idx >= 0 && idx < years.length - 1 ? String(years[idx + 1]) : ""
  const nextYear = idx > 0 ? String(years[idx - 1]) : ""
  const wdLabels = [
    t("panel.stats.heatWd1", { defaultValue: "一" }),
    "",
    t("panel.stats.heatWd3", { defaultValue: "三" }),
    "",
    t("panel.stats.heatWd5", { defaultValue: "五" }),
    "",
    "",
  ]
  if (!showGrid) {
    return (
      <EmptyState
        title={t("panel.stats.heatEmpty", { defaultValue: "还没有可展示的日子" })}
        description={t("panel.stats.heatEmptySub", { defaultValue: "互动过的日子会在这里亮起来。" })}
      />
    )
  }
  return (
    <div>
      {years.length > 1 ? (
        <div className="tm-month-nav">
          <button
            type="button"
            className="tm-month-btn"
            disabled={!prevYear}
            onClick={() => prevYear && onPickYear(prevYear)}
          >
            ‹
          </button>
          <span className="tm-month-label">
            {curYear && curYear === years[0]
              ? t("panel.stats.heatYearThis", { defaultValue: "今年" })
              : t("panel.stats.heatYearN", { defaultValue: "{y} 年" }).replace("{y}", String(curYear))}
          </span>
          <button
            type="button"
            className="tm-month-btn"
            disabled={!nextYear}
            onClick={() => nextYear && onPickYear(nextYear)}
          >
            ›
          </button>
        </div>
      ) : null}
      <div className="tm-heat-scroll">
        <div className={`tm-gh${sizeClass}`}>
          <div className="tm-gh-monthrow">
            <span className="tm-gh-corner" />
            {weeks.map((week) => (
              <span key={week.key} className="tm-gh-monthslot">
                {week.monthLabel ? <span className="tm-gh-month">{week.monthLabel}</span> : null}
              </span>
            ))}
          </div>
          <div className="tm-gh-body">
            <div className="tm-gh-wdcol">
              {wdLabels.map((wd, i) => (
                <span key={i} className={`tm-gh-wd ${wd ? "" : "tm-gh-wd-empty"}`}>{wd}</span>
              ))}
            </div>
            <div className="tm-gh-cols">
              {weeks.map((week, wi) => (
                <div key={week.key} className="tm-gh-col">
                  {week.cells.map((date, i) => {
                    if (!date) {
                      return <span key={i} className="tm-heat-future" />
                    }
                    const day = byDate[date]
                    const lv = heatLevel(day ? day.turns : 0)
                    const tip = `${date} · ${t("panel.stats.heatTipTurns", { defaultValue: "互动 {n} 轮" }).replace("{n}", String((day && day.turns) || 0))}`
                    // 悬停明细由自适应 Tip 承载（顶部行自动向下翻、左右缘自动收拢）
                    return (
                      <AdaptiveTip key={date} content={tip}>
                        <span className={`tm-heat-cell tm-heat-lv${lv}`} />
                      </AdaptiveTip>
                    )
                  })}
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
      <div className="tm-heat-footer">
        <span className="tm-heat-note">
          {t("panel.stats.heatNote", { defaultValue: "颜色深浅 = 当天互动轮数；悬停查看明细" })}
        </span>
        <div className="tm-heat-legend">
          <span className="tm-heat-legend-label">{t("panel.stats.heatLess", { defaultValue: "少" })}</span>
          {[0, 1, 2, 3, 4].map((lv) => (
            <span key={lv} className={`tm-heat-cell tm-heat-lv${lv}`} />
          ))}
          <span className="tm-heat-legend-label">{t("panel.stats.heatMore", { defaultValue: "多" })}</span>
        </div>
      </div>
    </div>
  )
}

// ISO 日期串 → 本地 Date（手动解析避免时区歧义）
function parseISODate(iso: string): Date | null {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(iso || ""))
  if (!match) return null
  return new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]))
}

// Date → ISO 日期串
function isoOfDate(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`
}

// 周一基 0 的星期序号（JS getDay 周日=0；GitHub 布局顶行是周一）
function weekdayIndex(d: Date): number {
  return (d.getDay() + 6) % 7
}

// 周列生成：窗口起点对齐到周一，每列 7 格（周一→周日），终点=窗口最后一天所在周。
// monthNo = 列内含某月 1 号时的月号（首列特例：不含 1 号也标首列月）；短月名由渲染层 i18n
function buildWeekColumns(firstDate: string, lastDate: string): WeekColumn[] {
  const first = parseISODate(firstDate)
  const last = parseISODate(lastDate)
  if (!first || !last) return []
  const start = new Date(first.getFullYear(), first.getMonth(), first.getDate() - weekdayIndex(first))
  const weeks: WeekColumn[] = []
  let cursor = new Date(start.getFullYear(), start.getMonth(), start.getDate())
  let idx = 0
  while (cursor.getTime() <= last.getTime()) {
    const cells: string[] = []
    let monthNo = 0
    for (let i = 0; i < 7; i++) {
      const d = new Date(cursor.getFullYear(), cursor.getMonth(), cursor.getDate() + i)
      cells.push(d.getTime() <= last.getTime() ? isoOfDate(d) : "")
      if (d.getDate() === 1 && d.getTime() <= last.getTime()) {
        monthNo = d.getMonth() + 1
      }
    }
    if (idx === 0 && !monthNo) {
      monthNo = cursor.getMonth() + 1
    }
    weeks.push({ key: isoOfDate(cursor), cells, monthNo })
    cursor = new Date(cursor.getFullYear(), cursor.getMonth(), cursor.getDate() + 7)
    idx++
  }
  return weeks
}

type WeekColumn = { key: string; cells: string[]; monthNo: number }

// 月报导航：左右翻月 + 月份标题（历史封卷月固定，当月实时）
function MonthNav(props: {
  t: TFunc
  month?: MonthReport | null
  monthAvailable: string[]
  monthLoading: boolean
  onPickMonth: (month: string) => void
}) {
  const { t, month, monthAvailable, monthLoading, onPickMonth } = props
  const list = monthAvailable || []
  const current = String((month && month.month) || "")
  const idx = list.indexOf(current)
  const prev = idx > 0 ? list[idx - 1] : ""
  const next = idx >= 0 && idx < list.length - 1 ? list[idx + 1] : ""
  return (
    <div className="tm-month-nav">
      <button
        type="button"
        className="tm-month-btn"
        disabled={!prev}
        onClick={() => prev && onPickMonth(prev)}
      >
        ‹
      </button>
      <span className="tm-month-label">
        {current ? monthLabel(t, current) : t("panel.stats.monthNone", { defaultValue: "还没有月份" })}
        {month && month.sealed === false ? (
          <StatusBadge tone="info" label={t("panel.stats.monthLive", { defaultValue: "进行中" })} />
        ) : null}
        {monthLoading ? <span className="tm-month-loading">…</span> : null}
      </span>
      <button
        type="button"
        className="tm-month-btn"
        disabled={!next}
        onClick={() => next && onPickMonth(next)}
      >
        ›
      </button>
    </div>
  )
}

// 月报正文：玻璃数字卡 + 语气主色胶囊 + 本月声音（玻璃引言卡）
function MonthReportView(props: { t: TFunc; month?: MonthReport | null }) {
  const { t, month } = props
  if (!month || !month.month) {
    return (
      <EmptyState
        title={t("panel.stats.monthEmpty", { defaultValue: "这个月还没有记录" })}
        description={t("panel.stats.monthEmptySub", { defaultValue: "等这个月过完，这里会为你们留一份小结。" })}
      />
    )
  }
  const m = month
  const toneEntries = Object.entries(m.tone || {}).slice(0, 3)
  const valence = m.valence_avg === null || m.valence_avg === undefined ? null : Number(m.valence_avg)
  const valenceWord = valence === null
    ? t("panel.journal.trend.unknown", { defaultValue: "暂无" })
    : t(valence > 0.2 ? "panel.journal.trend.warm" : valence >= -0.2 ? "panel.journal.trend.calm" : "panel.journal.trend.cold", { defaultValue: "" })
  return (
    <div className="tm-month-report">
      <div className="tm-month-grid">
        <MonthCell
          label={t("panel.stats.month.turns", { defaultValue: "本月互动" })}
          value={t("panel.stats.month.turnsValue", { defaultValue: "{n} 轮" }).replace("{n}", String(m.turns || 0))}
        />
        <MonthCell
          label={t("panel.stats.month.activeDays", { defaultValue: "活跃天数" })}
          value={t("panel.stats.month.daysValue", { defaultValue: "{n} 天" }).replace("{n}", String(m.active_days || 0))}
        />
        <MonthCell
          label={t("panel.stats.month.busiest", { defaultValue: "最热闹的一天" })}
          value={m.busiest_day
            ? t("panel.stats.month.busiestValue", { defaultValue: "{d}（{n} 轮）" })
                .replace("{d}", String(m.busiest_day).slice(5))
                .replace("{n}", String(m.busiest_turns || 0))
            : "-"}
        />
        <MonthCell
          label={t("panel.stats.month.streak", { defaultValue: "最长连续" })}
          value={t("panel.stats.month.daysValue", { defaultValue: "{n} 天" }).replace("{n}", String(m.longest_streak || 0))}
        />
        <MonthCell
          label={t("panel.stats.month.fight", { defaultValue: "冷战 / 和好" })}
          value={t("panel.stats.month.fightValue", { defaultValue: "{c} / {m}" })
            .replace("{c}", String(m.cold_wars || 0))
            .replace("{m}", String(m.made_ups || 0))}
        />
        <MonthCell
          label={t("panel.stats.month.warm", { defaultValue: "开心时刻" })}
          value={t("panel.stats.month.daysValue", { defaultValue: "{n} 天" }).replace("{n}", String(m.warm_moments || 0))}
        />
      </div>

      {/* 语气主色：她这个月回复的整体色调 */}
      {toneEntries.length ? (
        <div className="tm-month-tone">
          <span className="tm-month-tone-label">{t("panel.stats.month.toneLabel", { defaultValue: "她的语气" })}</span>
          {toneEntries.map(([label, count]) => (
            <span key={label} className="tm-month-tone-item">
              {t(toneLabelKey(label) || "panel.stats.tone.neutral", { defaultValue: label })}
              <span className="tm-month-tone-count">×{count}</span>
            </span>
          ))}
          {valence !== null ? (
            <span className="tm-month-tone-item">
              <span className="tm-month-tone-dot" style={{ background: moodDotColor(valence) }} />
              {valenceWord}
            </span>
          ) : null}
        </div>
      ) : null}

      {/* 本月声音：她当月写过的最长一条手记（原话摘录，不改写） */}
      {m.voice && m.voice.entry ? (
        <div className="tm-month-voice">
          <div className="tm-month-voice-label">
            {t("panel.stats.month.voiceLabel", { defaultValue: "这个月她写下的一句话" })}
            {m.voice.mood ? <StatusBadge tone="default" label={String(m.voice.mood)} /> : null}
          </div>
          <div className="tm-month-voice-text">「{String(m.voice.entry)}」</div>
        </div>
      ) : (
        <div className="tm-month-voice-empty">
          {t("panel.stats.month.voiceEmpty", { defaultValue: "这个月她还没有写过手记。" })}
        </div>
      )}
    </div>
  )
}

// 月报数字格
function MonthCell(props: { label: string; value: string }) {
  const { label, value } = props
  return (
    <div className="tm-month-cell">
      <span className="tm-month-cell-value">{value}</span>
      <span className="tm-month-cell-label">{label}</span>
    </div>
  )
}
