// 潮汐日历页：月视图 + 阶段图例 + 选日模式（设置潮汐首日 / 快进一天）
// 组件内部持有月份/选日交互态；父级用 key={lanlan} 在切换角色时整体重置
import { Button, EmptyState, Tooltip, useEffect, useState } from "@neko/plugin-ui"
import type { Calendar, TFunc } from "./types"

export function CalendarPane(props: {
  t: TFunc
  calendar?: Calendar
  anchorDate?: string
  advanceDays?: number
  lanlan?: string
  key?: string
  canSetAnchor: boolean
  canAdvance: boolean
  onSetAnchor: (date: string) => Promise<boolean>
  onAdvance: () => void
}) {
  const { t, calendar, anchorDate, advanceDays, lanlan, canSetAnchor, canAdvance, onSetAnchor, onAdvance } = props
  const [monthIndex, setMonthIndex] = useState(1)
  const [selectMode, setSelectMode] = useState(false)
  const [picked, setPicked] = useState<string | null>(null)

  // 宿主切换角色后（轮询拿到新 lanlan），日历交互态属于上一个角色，需重置
  useEffect(() => {
    setSelectMode(false)
    setPicked(null)
    setMonthIndex(1)
  }, [lanlan])

  async function handleSetAnchor() {
    if (!picked || !canSetAnchor) return
    const ok = await onSetAnchor(picked)
    if (ok) {
      setSelectMode(false)
      setPicked(null)
    }
  }

  const months = calendar?.months || []
  const m = months[Math.min(Math.max(monthIndex, 0), Math.max(months.length - 1, 0))]
  const weekdays = [
    t("panel.calendar.wd1", { defaultValue: "一" }),
    t("panel.calendar.wd2", { defaultValue: "二" }),
    t("panel.calendar.wd3", { defaultValue: "三" }),
    t("panel.calendar.wd4", { defaultValue: "四" }),
    t("panel.calendar.wd5", { defaultValue: "五" }),
    t("panel.calendar.wd6", { defaultValue: "六" }),
    t("panel.calendar.wd7", { defaultValue: "日" }),
  ]

  return (
    <div className="tm-pane">
      <div className="tm-card">
        <div className="tm-card-header">
          <h2 className="tm-card-title">{t("panel.calendar.title", { defaultValue: "潮汐日历" })}</h2>
          <Tooltip
            content={selectMode
              ? t("panel.calendar.exitPick", { defaultValue: "退出日期选择" })
              : t("panel.calendar.pickTip", { defaultValue: "选择日期：可设置潮汐首日或快进" })}
            placement="bottom"
          >
            <button
              type="button"
              className={selectMode ? "tm-iconbtn tm-iconbtn-active" : "tm-iconbtn"}
              onClick={() => {
                setSelectMode((v) => !v)
                setPicked(null)
              }}
            >
              <span className="tm-ico tm-ico-target" />
            </button>
          </Tooltip>
        </div>
        <div className="tm-card-body">
          {calendar?.error || !m || !m.cells ? (
            <EmptyState
              title={t("panel.calendar.unavailable", { defaultValue: "日历不可用" })}
              description={calendar?.error || t("panel.calendar.needAnchor", { defaultValue: "设置潮汐首日锚点后即可推算" })}
            />
          ) : (
            <div>
              <div className="tm-cal-nav">
                <Button disabled={monthIndex <= 0} onClick={() => { setMonthIndex((i) => Math.max(i - 1, 0)) }}>‹</Button>
                <span className="tm-cal-label">{m.label}</span>
                <Button disabled={monthIndex >= months.length - 1} onClick={() => { setMonthIndex((i) => Math.min(i + 1, months.length - 1)) }}>›</Button>
              </div>
              <div className="tm-cal-head">
                {weekdays.map((w, i) => (
                  <div key={i} className="tm-cal-wd">{w}</div>
                ))}
              </div>
              <div className="tm-cal-grid">
                {m.cells.map((c, idx) => {
                  if (!c.in_month || !c.day) {
                    return <div key={idx} className="tm-cal-cell tm-cal-blank" />
                  }
                  const dateStr = `${m.year}-${String(m.month ?? 0).padStart(2, "0")}-${String(c.day).padStart(2, "0")}`
                  return (
                    <div
                      key={idx}
                      className="tm-cal-cell"
                      data-phase={c.phase || ""}
                      data-today={c.is_today ? "1" : null}
                      data-future={c.is_future ? "1" : null}
                      data-selectable={selectMode ? "1" : null}
                      data-picked={picked === dateStr ? "1" : null}
                      title={c.phase_label || ""}
                      onClick={selectMode ? () => setPicked(dateStr) : undefined}
                    >
                      <span>{c.day}</span>
                      {c.is_tide ? <span className="tm-cal-tidebar" /> : null}
                    </div>
                  )
                })}
              </div>
              <div className="tm-legend">
                <span className="tm-legend-item"><i className="tm-dot" style={{ background: "var(--danger)" }} />{t("panel.calendar.tide", { defaultValue: "潮汐日" })}</span>
                <span className="tm-legend-item"><i className="tm-dot" style={{ background: "var(--success)" }} />{t("panel.calendar.follicular", { defaultValue: "回升期" })}</span>
                <span className="tm-legend-item"><i className="tm-dot" style={{ background: "var(--info)" }} />{t("panel.calendar.ovulatory", { defaultValue: "活跃期" })}</span>
                <span className="tm-legend-item"><i className="tm-dot" style={{ background: "var(--border)" }} />{t("panel.calendar.luteal", { defaultValue: "平稳期" })}</span>
                <span className="tm-legend-item"><i className="tm-dot tm-dot-today" />{t("panel.calendar.today", { defaultValue: "今天" })}</span>
                {selectMode ? (
                  <span className="tm-cal-actions">
                    {picked ? (
                      <span className="tm-picked-label">{t("panel.calendar.picked", { defaultValue: "已选" })} {picked}</span>
                    ) : (
                      <span className="tm-pick-hint">{t("panel.calendar.pickHint", { defaultValue: "点击日历中的某一天" })}</span>
                    )}
                    {picked ? (
                      <Button tone="primary" disabled={!canSetAnchor} onClick={handleSetAnchor}>
                        {t("actions.set_anchor.label", { defaultValue: "设置潮汐首日" })}
                      </Button>
                    ) : null}
                    <Button disabled={!canAdvance} onClick={onAdvance}>
                      {t("actions.advance.label", { defaultValue: "快进一天" })}
                    </Button>
                  </span>
                ) : null}
              </div>
              <div className="tm-cal-meta">
                {t("panel.anchorCurrent", { defaultValue: "当前锚点" })}: {anchorDate || "-"}
                {" · "}
                {t("panel.advancedN", { n: String(advanceDays ?? 0), defaultValue: "已快进 {n} 天" })}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
