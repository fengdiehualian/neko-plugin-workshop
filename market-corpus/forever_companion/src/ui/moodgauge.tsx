// 情绪页 · 当前心情仪表盘：大字心情词 + 愉悦度双向条形（中心零点，左灰蓝/右暖琥珀）
// + 活跃度单向条形（左灰→中蓝→右红，静息基线刻度），均标注数值；纯 CSS/div 实现（hosted-tsx 不支持 SVG/Canvas）
import { Card } from "@neko/plugin-ui"
import type { Mood, TFunc } from "./types"
import { arousalColor, arousalScore, moodDotColor, moodWordOf, valenceScore } from "./utils"

// 注意（hosted-tsx 链接器坑）：export 声明必须排在任何 JSX 闭合标签之前，
// 辅助组件（GaugeBar）放文件尾部，靠函数声明提升供上面引用。
export function MoodGaugeCard(props: { t: TFunc; mood: Mood; enabled: boolean }) {
  const { t, mood, enabled } = props
  const systemOn = enabled && mood.system_enabled !== false
  const v = Math.max(-1, Math.min(1, Number(mood.affect && mood.affect.valence) || 0))
  const a = Math.max(0, Math.min(1, Number(mood.affect && mood.affect.arousal) || 0))
  // 活跃度静息基线（后端下发，缺省 0.35）：刻度标出"自然平复会回到的位置"
  const baseline = Math.max(0, Math.min(1, Number(mood.affect && mood.affect.arousal_baseline) || 0.35))
  const word = systemOn ? t(moodWordOf(v, a), { defaultValue: "平静" }) : "—"
  const color = moodDotColor(systemOn ? v : 0)
  const aColor = arousalColor(systemOn ? a : 0)

  return (
    <Card title={t("panel.mood.gauge.title", { defaultValue: "当前心情" })}>
      <div className="tm-gauge">
        <div className="tm-gauge-head">
          <span className="tm-gauge-word" style={{ color }}>{word}</span>
          <span className="tm-gauge-hint">
            {t("panel.mood.gauge.hint", { defaultValue: "情绪动作与语气感知留下的余波，会随时间自然平复" })}
          </span>
        </div>
        <GaugeBar
          label={t("panel.mood.gauge.valence", { defaultValue: "愉悦度" })}
          valueText={systemOn ? String(valenceScore(v)) : "—"}
          fill={systemOn ? valenceFill(v, color) : EMPTY_FILL}
          bipolar={true}
          markerLeft={systemOn ? `${(v * 50 + 50).toFixed(1)}%` : null}
          markerColor={color}
        />
        <GaugeBar
          label={t("panel.mood.gauge.arousal", { defaultValue: "活跃度" })}
          valueText={systemOn ? String(arousalScore(a)) : "—"}
          fill={systemOn ? { left: "0%", width: `${(a * 100).toFixed(1)}%`, background: aColor } : EMPTY_FILL}
          bipolar={false}
          markerLeft={systemOn ? `${(a * 100).toFixed(1)}%` : null}
          markerColor={aColor}
          baselineLeft={systemOn ? `${(baseline * 100).toFixed(1)}%` : null}
        />
      </div>
    </Card>
  )
}

type GaugeFill = { left: string; width: string; background: string }

const EMPTY_FILL: GaugeFill = { left: "50%", width: "0%", background: "transparent" }

// 愉悦度双向条：中心为零点；v≥0 从中心向右延伸，v<0 从中心向左延伸
function valenceFill(v: number, color: string): GaugeFill {
  const half = (Math.abs(v) * 50).toFixed(1)
  return v >= 0
    ? { left: "50%", width: `${half}%`, background: color }
    : { left: `${(50 - Math.abs(v) * 50).toFixed(1)}%`, width: `${half}%`, background: color }
}

function GaugeBar(props: {
  label: string
  valueText: string
  fill: GaugeFill
  bipolar: boolean
  markerLeft: string | null
  markerColor: string
  baselineLeft?: string | null
}) {
  const { label, valueText, fill, bipolar, markerLeft, markerColor, baselineLeft } = props
  return (
    <div className="tm-gauge-row">
      <span className="tm-gauge-label">{label}</span>
      <div className={bipolar ? "tm-gauge-track tm-gauge-track-bipolar" : "tm-gauge-track tm-gauge-track-plain"}>
        {bipolar ? <div className="tm-gauge-zero" /> : null}
        <div
          className="tm-gauge-fill"
          style={{ left: fill.left, width: fill.width, background: fill.background }}
        />
        {baselineLeft ? (
          <div className="tm-gauge-baseline-tick" style={{ left: baselineLeft }} />
        ) : null}
        {markerLeft ? (
          <div
            className="tm-gauge-marker"
            style={{ left: markerLeft, background: markerColor }}
          />
        ) : null}
      </div>
      <span className="tm-gauge-value">{valueText}</span>
    </div>
  )
}
