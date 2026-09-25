// 月相圆环：月相盈亏表达周期进度（潮汐期初=新月 → 周期中点约活跃期=满月），
// 外圈细轨道上排布阶段边界小点与今日呼吸光点；中心 pill 三视图（天数/本阶段/下一阶段），
// 点月相盘或下方指示点切换
// 注意：hosted-tsx 运行时不支持 SVG 命名空间（mount 用 createElement 而非 createElementNS），
// 月相用纯 CSS 实现：亮盘之上一个同尺寸暗盘横向扫过（translateX），不要用 <svg>
import { useState } from "@neko/plugin-ui"
import type { Settings, Status, TFunc } from "./types"
import { phaseColorOf, phaseLabels } from "./utils"

// 月相扫掠几何：暗盘横向滑过亮盘。返回暗盘 translateX 占盘宽百分比，
// 0 = 完全遮住（新月），±100 = 完全移出（满月）；
// 盈月暗盘向左退场（光从右侧来），亏月暗盘从右侧进入。
// 中点校正到 f(照度 0.5) = 0.404（两等圆重叠面积恰为一半时 d≈0.808·2r），让弦月恰好半明半暗
function moonSweep(ratio: number): number {
  const r = Math.min(1, Math.max(0, ratio))
  const illum = (1 - Math.cos(r * Math.PI * 2)) / 2
  const f = illum < 0.5 ? illum * 0.808 : 0.404 + (illum - 0.5) * 1.192
  return (r * Math.PI * 2 <= Math.PI ? -1 : 1) * f * 100
}

// 月相照度 0~1：驱动光晕强度，满月最亮
function moonIllum(ratio: number): number {
  return (1 - Math.cos(Math.min(1, Math.max(0, ratio)) * Math.PI * 2)) / 2
}

export function RingStatus(props: { t: TFunc; status: Status; settings: Settings }) {
  const { t, status, settings } = props
  const [ringView, setRingView] = useState(0)
  const [surgeTick, setSurgeTick] = useState(0)

  // 四阶段周期：settings 是后端已生效的推导值，前端不再二次推导
  // tint/dot 与日历单元格同色系；月相方案里阶段分段不上环，只留边界小点
  const cycle = Math.max(1, Number(settings.cycle_length ?? 28))
  const periodEnd = Math.max(0, Math.min(cycle, Number(settings.period_length ?? 5)))
  const ovuLo = Math.max(periodEnd + 1, Number(settings.ovulation_day ?? 14) - Number(settings.ovulation_window ?? 3))
  const ovuHi = Math.min(cycle, Number(settings.ovulation_day ?? 14) + Number(settings.ovulation_window ?? 3))
  const segments = [
    { key: "menstrual", days: periodEnd, dot: "rgba(245, 108, 108, 0.55)" },
    { key: "follicular", days: Math.max(0, ovuLo - periodEnd - 1), dot: "rgba(103, 194, 58, 0.5)" },
    { key: "ovulatory", days: Math.max(0, ovuHi - ovuLo + 1), dot: "rgba(20, 184, 166, 0.5)" },
    { key: "luteal", days: Math.max(0, cycle - ovuHi), dot: "rgba(100, 116, 139, 0.45)" },
  ]

  // 阶段边界小点：记录每段结束（= 下一段开始）的角度与下一段颜色
  let segAcc = 0
  const boundaryDots: { deg: number; color: string }[] = []
  for (let i = 0; i < segments.length; i += 1) {
    const seg = segments[i]
    if (seg.days <= 0) continue
    segAcc += seg.days
    if (segAcc >= cycle) break
    let nextColor = seg.dot
    for (let j = i + 1; j < segments.length; j += 1) {
      if (segments[j].days > 0) {
        nextColor = segments[j].dot
        break
      }
    }
    boundaryDots.push({ deg: (segAcc / cycle) * 360, color: nextColor })
  }

  const showMarker = status.enabled !== false && !status.error && typeof status.day_ratio === "number"
  const ringRatio = Math.min(1, Math.max(0, Number(status.day_ratio ?? 0)))
  const illum = moonIllum(ringRatio)
  const moonX = moonSweep(showMarker ? ringRatio : 0)
  // 月盘外晕：随满月度增强的暖白辉光（v3 去掉 inset 内阴影，发光体要 bloom 不要灰）
  const moonShadow = `0 0 ${(4 + illum * 14).toFixed(1)}px rgba(246, 239, 221, ${(0.06 + illum * 0.38).toFixed(2)})`
  // 今日光点骑在外圈轨道上（轨道半径 = 49%）
  const markerAngle = ringRatio * Math.PI * 2 - Math.PI / 2
  const markerLeft = `${(50 + 49 * Math.cos(markerAngle)).toFixed(2)}%`
  const markerTop = `${(50 + 49 * Math.sin(markerAngle)).toFixed(2)}%`

  // 中心 pill 三视图（点击切换）：天数 → 当前阶段 → 下一阶段
  const phaseLabelMap = phaseLabels(t)
  let phaseIdx = -1
  let dayInPhase = 0
  let daysToNext = 0
  if (typeof status.cycle_day === "number") {
    let acc = 0
    for (let i = 0; i < segments.length; i += 1) {
      const seg = segments[i]
      if (seg.days <= 0) continue
      if (status.cycle_day <= acc + seg.days) {
        phaseIdx = i
        dayInPhase = status.cycle_day - acc
        daysToNext = acc + seg.days - status.cycle_day + 1
        break
      }
      acc += seg.days
    }
  }
  let nextKey = ""
  if (phaseIdx >= 0) {
    for (let i = 1; i <= segments.length; i += 1) {
      const seg = segments[(phaseIdx + i) % segments.length]
      if (seg.days > 0) {
        nextKey = seg.key
        break
      }
    }
  }
  const pillText = ringView === 0
    ? `${status.cycle_day ?? "-"} / ${cycle}`
    : ringView === 1
      ? (phaseIdx >= 0
        ? `${phaseLabelMap[segments[phaseIdx].key] || "-"} · ${t("panel.ring.dayN", { n: String(dayInPhase), defaultValue: "第{n}天" })}`
        : status.phase_label || "-")
      : (nextKey
        ? `${phaseLabelMap[nextKey] || "-"} · ${t("panel.ring.inNDays", { n: String(daysToNext), defaultValue: "{n}天后" })}`
        : "-")

  function onRingClick() {
    if (typeof status.cycle_day !== "number") return
    setRingView((v) => (v + 1) % 3)
    setSurgeTick((n) => n + 1)
  }

  return (
    <div className="tm-ring-col">
      <div
        className="tm-ring-wrap"
        onClick={onRingClick}
        title={t("panel.ring.switchTip", { defaultValue: "点击切换：天数 / 本阶段 / 下一阶段" })}
      >
        <div key="glow" className="tm-ring-glow" style={{ opacity: (0.15 + illum * 0.55).toFixed(2) }} />
        <div key="track" className="tm-ring-track">
          {surgeTick > 0 ? <div key={surgeTick} className="tm-ring-surge-sweep" onAnimationEnd={() => setSurgeTick(0)} /> : null}
        </div>
        <div key="orbit" className="tm-ring-orbit" />
        {boundaryDots.map((d, i) => (
          <div key={i} className="tm-ring-phase-dot" style={{ transform: `rotate(${d.deg.toFixed(2)}deg) translateY(-39px)`, background: d.color }} />
        ))}
        {showMarker ? <div key="marker" className="tm-ring-marker" style={{ left: markerLeft, top: markerTop, background: phaseColorOf(status) }} /> : null}
        <div key="moon" className="tm-moon" style={{ boxShadow: moonShadow }}>
          <div className="tm-moon-base" />
          <div className="tm-moon-lit" />
          <div className="tm-moon-shadow" style={{ transform: `translateX(${moonX.toFixed(2)}%)` }} />
        </div>
        <div key={ringView} className="tm-moon-pill">{pillText}</div>
      </div>
      {typeof status.cycle_day === "number" ? (
        <div className="tm-ring-dots" title={t("panel.ring.switchTip", { defaultValue: "点击切换：天数 / 本阶段 / 下一阶段" })}>
          {[0, 1, 2].map((i) => (
            <button
              key={i}
              type="button"
              className={ringView === i ? "tm-ring-dot tm-ring-dot-active" : "tm-ring-dot"}
              onClick={() => { setRingView(i); setSurgeTick((n) => n + 1) }}
            />
          ))}
        </div>
      ) : null}
    </div>
  )
}
