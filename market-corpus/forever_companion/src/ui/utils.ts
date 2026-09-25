// 面板共享逻辑：阶段配色/文案映射、情绪徽标语气、设置快照 → 表单初值、
// 面板外观（1.2.0 图库 + 可调背景）参数归一/渲染换算/canvas 压缩
import type { Appearance, FormValues, Settings, Status, TFunc } from "./types"

export const DATE_RE = /^\d{4}-\d{2}-\d{2}$/

// 面板展示层去 emoji：情绪动作标签带 emoji 是给模型辨语气用的（后端不动），
// 面板渲染前统一剔掉，保持纯文字观感；仅去图形符号区，不动正常文字与标点
export function stripEmoji(text?: string): string {
  return String(text || "")
    .replace(/[\u{1F000}-\u{1FAFF}\u{2190}-\u{2BFF}\u{2600}-\u{27BF}\u{2100}-\u{214F}\u{FE0F}\u{200D}]/gu, "")
    .trim()
}

// 情绪徽标语气：正面动作（暖流涌动/满潮欢喜）用 success，心有涟漪与 5 个负面动作维持 warning
export const POSITIVE_MOOD_ACTIONS = new Set(["warm_current", "spring_tide"])

export function moodBadgeTone(action?: string): "warning" | "success" {
  return action && POSITIVE_MOOD_ACTIONS.has(action) ? "success" : "warning"
}

// 连续心情二维量 → 心情词 i18n key 分桶：valence 定冷暖、arousal 定强弱
// （雀跃=好且激动，烦躁=坏且激动，低落=坏且平静）
export function moodWordOf(valence: number, arousal: number): string {
  if (valence > 0.4) return arousal > 0.5 ? "panel.mood.word.elated" : "panel.mood.word.bright"
  if (valence > 0.1) return "panel.mood.word.warm"
  if (valence >= -0.1) return "panel.mood.word.calm"
  if (valence >= -0.4) return "panel.mood.word.muffled"
  return arousal > 0.5 ? "panel.mood.word.restless" : "panel.mood.word.low"
}

// 心情指示点配色：valence 插值——负=灰蓝、近零=中性灰、正=暖琥珀
export function moodDotColor(valence: number): string {
  const v = Math.max(-1, Math.min(1, valence))
  const to = v < 0 ? [110, 141, 171] : [245, 176, 77]
  const ratio = Math.abs(v)
  const mix = (zero: number, target: number) => Math.round(zero + (target - zero) * ratio)
  return `rgb(${mix(148, to[0])}, ${mix(163, to[1])}, ${mix(184, to[2])})`
}

// 愉悦度双向条填充色：正侧恒暖琥珀、负侧恒灰蓝（与 moodDotColor 的"近零=灰"不同——
// 条形图需要每侧有辨识色，否则静息态（valence=0，显示 50 分）整条灰掉，
// 与"未启用"视觉混淆；心情词/胶囊仍用 moodDotColor，两套语义并存）
export function valenceBarColor(valence: number): string {
  return valence < 0 ? "rgb(110, 141, 171)" : "rgb(245, 176, 77)"
}

// 活跃度配色：arousal 三段插值——低=中性灰、中=天蓝、高=活跃红，
// 与轨道"左灰→中蓝→右红"渐变同一色标，填充/游标随当前值取色
export function arousalColor(arousal: number): string {
  const a = Math.max(0, Math.min(1, arousal))
  const from = a < 0.5 ? [100, 116, 139] : [96, 165, 250]
  const to = a < 0.5 ? [96, 165, 250] : [244, 63, 94]
  const ratio = a < 0.5 ? a * 2 : (a - 0.5) * 2
  const mix = (f: number, t: number) => Math.round(f + (t - f) * ratio)
  return `rgb(${mix(from[0], to[0])}, ${mix(from[1], to[1])}, ${mix(from[2], to[2])})`
}

// props.api.call 的返回值是宿主信封 {plugin_id, action_id, result: <入口载荷>}，
// iframe 侧不做解包；这里兼容两种形态取出真实载荷（信封判据：同时带 action_id 与 result 键）
export function unwrapCallResult<T = Record<string, any>>(payload: any): T {
  if (payload && typeof payload === "object" && "result" in payload && "action_id" in payload) {
    return payload.result as T
  }
  return payload as T
}

// i18n 契约（1.3.0 第九轮）：后端面板入口的用户可见错误一律是稳定码
// （Err(SdkError("some_code"))，经宿主原样透传到 reject message），前端在此
// 按 panel.errors.<camelCase(code)> 翻译；非码文本（宿主自身错误、超时提示等）
// 原样直出，行为与旧版一致。后端只准发 ^[a-z][a-z0-9_]*$（tests/test_i18n_contract.py 钉死），
// 所以单码英文/中文裸串不会再短路这里的 t()。
const ERROR_CODE_RE = /^[a-z][a-z0-9_]*$/

function codeToCamel(code: string): string {
  return code.replace(/_+([a-z0-9])/g, (_m, c: string) => c.toUpperCase())
}

export function errorText(raw: unknown, t: (key: string, opts?: Record<string, any>) => string): string {
  const msg = raw instanceof Error ? raw.message : String(raw == null ? "" : raw)
  if (ERROR_CODE_RE.test(msg)) {
    // defaultValue 给码本身：新码忘了进 locale 时退化为英文码（可排查），
    // 而不是把某一语言的裸串直喷给所有用户
    return t(`panel.errors.${codeToCamel(msg)}`, { defaultValue: msg })
  }
  return msg
}

// 时光日记自动碎片的类型 → i18n key 后缀（panel.diary.kind.*）
export function fragmentKindKey(kind?: string): string {
  const known = ["like", "dislike", "important", "overstep"]
  return known.indexOf(String(kind || "")) >= 0 ? `panel.diary.kind.${kind}` : "panel.diary.kind.important"
}

// 个人日记页眉的心情走向：该页各段落笔瞬间的愉悦度均值 → 文案分档
//（与状态栏心情胶囊同款冷暖语义，但只看 valence、不分强弱档）
export function journalTrendKey(moodAvg?: number | null): string {
  if (moodAvg === null || moodAvg === undefined) return "panel.journal.trend.unknown"
  if (moodAvg > 0.2) return "panel.journal.trend.warm"
  if (moodAvg >= -0.2) return "panel.journal.trend.calm"
  return "panel.journal.trend.cold"
}

// ---- 相处统计（1.1.0）----
// 语气 label → 面板展示词 i18n key（热力图悬停/月报语气主色；与五分类同词表）
export function toneLabelKey(label?: string): string {
  const known = ["happy", "sad", "angry", "surprised", "neutral"]
  const key = String(label || "").trim()
  return known.indexOf(key) >= 0 ? `panel.stats.tone.${key}` : ""
}

// 热力图当日心情词：优先当天主导语气，无语气数据时按 valence 均值分桶
//（与状态栏心情胶囊同款 moodWordOf；两者都没有 → 空串，悬停只显示轮数）
export function heatDayMoodKey(day: { tone?: string; valence?: number | null }): string {
  const toneKey = toneLabelKey(day.tone)
  if (toneKey) return toneKey
  if (day.valence === null || day.valence === undefined) return ""
  return moodWordOf(Number(day.valence) || 0, 0.5)
}

// 热力图格子档位：turns → 0~4 档（0=无记录，1~4 递增），CSS 按档取色
export function heatLevel(turns?: number): number {
  const n = Number(turns) || 0
  if (n <= 0) return 0
  if (n < 10) return 1
  if (n < 30) return 2
  if (n < 80) return 3
  return 4
}

// YYYY-MM → 展示文案（如 "2026-08" → "2026 年 8 月"，纯数字替换避免 Intl 依赖）
export function monthLabel(t: TFunc, month?: string): string {
  const raw = String(month || "")
  const match = /^(\d{4})-(\d{2})$/.exec(raw)
  if (!match) return raw
  return t("panel.stats.monthFormat", { defaultValue: "{y} 年 {m} 月" })
    .replace("{y}", match[1])
    .replace("{m}", String(Number(match[2])))
}

// 愉悦度显示分制：内部 [-1,1] 映射为 0~100 整数分（50=中性）——静息态显示 50，
// 避免 "0.00" 看起来像"没有愉悦"；仅显示层换算，数据语义不变
export function valenceScore(valence: number): number {
  const v = Math.max(-1, Math.min(1, Number(valence) || 0))
  return Math.round(v * 50 + 50)
}

// 活跃度显示分制：内部 [0,1] 映射为 0~100 整数分，与愉悦度分制观感一致
export function arousalScore(arousal: number): number {
  const a = Math.max(0, Math.min(1, Number(arousal) || 0))
  return Math.round(a * 100)
}

// 注意：hosted-tsx 校验器不识别泛型尖括号层级，export const 的类型注解里不能带逗号，
// 所以先起类型别名再注解（直接写 Record<string, string> 会被误判为多声明符）
export type PhaseColorMap = Record<string, string>

export const PHASE_COLOR: PhaseColorMap = {
  menstrual: "var(--danger)",
  follicular: "var(--success)",
  ovulatory: "var(--info)",
  luteal: "var(--text)",
  // 锚点前：展示层按平稳期呈现（与 cycle.py 标签映射同语义），配色同平稳期
  before_start: "var(--text)",
}

export function phaseColorOf(status: Status): string {
  return status.enabled === false ? "var(--muted)" : PHASE_COLOR[status.phase || ""] || "var(--text)"
}

export function phaseLabels(t: TFunc): Record<string, string> {
  return {
    menstrual: t("panel.ring.phase.menstrual", { defaultValue: "潮汐期" }),
    follicular: t("panel.ring.phase.follicular", { defaultValue: "回升期" }),
    ovulatory: t("panel.ring.phase.ovulatory", { defaultValue: "活跃期" }),
    luteal: t("panel.ring.phase.luteal", { defaultValue: "平稳期" }),
  }
}

// 名单行的阶段 id → 阶段名：复用四阶段映射（before_start 展示层按平稳期，同后端语义）
export function lanlanPhaseLabel(t: TFunc, phase?: string): string {
  if (!phase || phase === "error") return ""
  return phaseLabels(t)[phase] || ""
}

// ---- 面板外观（1.2.0）：参数归一 / 背景层样式 / 渲染变量 / 图片压缩 ----

// 默认值复刻 1.1.x 观感（cover/center/glass16/dim0.3/无滤镜/底色与文字满格）；
// 与后端 core/appearance.py 的 clamp_appearance 保持同一套域值，两边各归一一次
export const APPEARANCE_DEFAULTS: AppearanceDefaults = {
  bg_id: "", fill: "cover", position: "center",
  blur: 0, dim: 0.3, brightness: 100, saturate: 100, contrast: 100,
  glass: 16, card_alpha: 100, text_weight: 100,
}

// 类型别名绕开校验器：export const 注解里不能带泛型尖括号的逗号
export type AppearanceDefaults = Record<string, string | number>

export const APPEARANCE_FILLS = ["cover", "contain", "repeat", "stretch"]
export const APPEARANCE_POSITIONS = [
  "left top", "center top", "right top",
  "left center", "center", "right center",
  "left bottom", "center bottom", "right bottom",
]

const APPEARANCE_NUMERIC: NumericRangeMap = {
  blur: [0, 30], dim: [0, 0.85], brightness: [30, 150],
  saturate: [0, 200], contrast: [50, 200], glass: [0, 40],
  card_alpha: [0, 100], text_weight: [40, 100],
}

// 校验器同样不认内联 Record 注解：先起别名（range 是 [min, max] 两元数组）
export type NumericRange = number[]
export type NumericRangeMap = Record<string, NumericRange>

export function normAppearance(raw?: AppearanceDefaults | null): Appearance {
  const src = (raw && typeof raw === "object") ? raw : {}
  const out = Object.assign({} as AppearanceDefaults, APPEARANCE_DEFAULTS)
  const bg = String(src.bg_id || "").trim()
  out.bg_id = bg && bg.length <= 64 ? bg : ""
  const fill = String(src.fill || "").trim().toLowerCase()
  out.fill = APPEARANCE_FILLS.indexOf(fill) >= 0 ? fill : "cover"
  const pos = String(src.position || "").trim().toLowerCase().replace(/\s+/g, " ")
  out.position = APPEARANCE_POSITIONS.indexOf(pos) >= 0 ? pos : "center"
  const keys = Object.keys(APPEARANCE_NUMERIC)
  for (let i = 0; i < keys.length; i += 1) {
    const name = keys[i]
    const value = Number((src as Record<string, any>)[name])
    const range = APPEARANCE_NUMERIC[name]
    if (!isFinite(value)) {
      out[name] = APPEARANCE_DEFAULTS[name]
    } else {
      out[name] = Math.min(range[1], Math.max(range[0], value))
    }
  }
  return out as Appearance
}

export function appearanceEquals(a: Appearance, b: Appearance): boolean {
  const keys = Object.keys(APPEARANCE_DEFAULTS)
  for (let i = 0; i < keys.length; i += 1) {
    if (String((a as Record<string, any>)[keys[i]]) !== String((b as Record<string, any>)[keys[i]])) return false
  }
  return true
}

// 背景层内联样式：填充/位置/滤镜全部按参数即时算（实时预览就靠它）。
// 有模糊时把层向外扩 blur+6px，否则 filter 会把图像边缘拉出透明露底
export function bgLayerStyle(ap: Appearance, dataUrl: string): Record<string, string> {
  const size = ap.fill === "contain" ? "contain" : ap.fill === "repeat" ? "auto" : ap.fill === "stretch" ? "100% 100%" : "cover"
  const filters: string[] = []
  if (ap.blur > 0) filters.push(`blur(${ap.blur}px)`)
  if (ap.brightness !== 100) filters.push(`brightness(${ap.brightness}%)`)
  if (ap.saturate !== 100) filters.push(`saturate(${ap.saturate}%)`)
  if (ap.contrast !== 100) filters.push(`contrast(${ap.contrast}%)`)
  return {
    backgroundImage: `url("${dataUrl}")`,
    backgroundSize: size,
    backgroundRepeat: ap.fill === "repeat" ? "repeat" : "no-repeat",
    backgroundPosition: ap.position,
    filter: filters.length ? filters.join(" ") : "none",
    WebkitFilter: filters.length ? filters.join(" ") : "none",
    inset: ap.blur > 0 ? `${-Math.round(ap.blur) - 6}px` : "0px",
  }
}

// 写到根包装 div 上的 CSS 自定义属性（display:contents 不改布局），
// 供 styles.ts 里 var() 消费；glass 恒写，其余仅偏离默认时写
export type CssVarMap = Record<string, string>

export function appearanceVars(ap: Appearance): CssVarMap {
  const vars: CssVarMap = { "--tm-glass": `${ap.glass}px` }
  vars["--tm-card-k"] = String(Math.round(ap.card_alpha) / 100)
  if (ap.text_weight < 100) {
    const w = ap.text_weight
    const conc = Math.round(55 + (w - 40) * 0.75)
    vars["--tm-text-color"] = `color-mix(in srgb, var(--text) ${conc}%, transparent)`
    if (w < 95) {
      const radius = Math.round((100 - w) / 10) + 2
      const alpha = (((100 - w) / 60) * 0.5 + 0.15).toFixed(2)
      vars["--tm-text-shadow"] = `0 1px ${radius}px rgba(2, 6, 23, ${alpha})`
    }
  }
  return vars
}

function loadImageElement(src: string): Promise<any> {
  return new Promise((resolve, reject) => {
    const img = new Image()
    img.onload = () => resolve(img)
    img.onerror = () => reject(new Error("image decode failed"))
    img.src = src
  })
}

function drawScaled(img: any, edge: number, mime: string, quality: number): string {
  const w = Number(img.naturalWidth || img.width) || 0
  const h = Number(img.naturalHeight || img.height) || 0
  if (!w || !h) throw new Error("image size unknown")
  const scale = Math.min(1, edge / Math.max(w, h))
  const canvas = document.createElement("canvas")
  canvas.width = Math.max(1, Math.round(w * scale))
  canvas.height = Math.max(1, Math.round(h * scale))
  const ctx = canvas.getContext("2d")
  if (!ctx) throw new Error("canvas unavailable")
  ctx.drawImage(img, 0, 0, canvas.width, canvas.height)
  const out = canvas.toDataURL(mime, quality)
  if (String(out || "").indexOf(`data:${mime}`) !== 0) throw new Error("encode failed")
  return out
}

// 压缩管线：auto=限长边 2560 转 WebP（失败降 JPEG，仍不划算则用原图）+ 256px 缩略图；
// raw=原样入册（仅补缩略图）；thumb=只生成缩略图（旧迁移图回填用）。
// GIF/SVG 不转码（动图/矢量语义）：auto 对它们等同 raw；SVG 画进 canvas 会污染
// toDataURL（SecurityError），缩略图尽力而为、失败留空由面板以占位样式呈现。
export function compressImageDataUrl(
  dataUrl: string,
  mime: string,
  mode: string,
): Promise<{ dataUrl: string; thumb: string; skipped: boolean }> {
  const animated = mime === "image/gif" || mime === "image/svg+xml"
  return loadImageElement(dataUrl).then((img) => {
    let out = dataUrl
    let skipped = false
    if (mode === "auto" && !animated) {
      try {
        let candidate = drawScaled(img, 2560, "image/webp", 0.82)
        if (candidate.length >= dataUrl.length) {
          candidate = drawScaled(img, 2560, "image/jpeg", 0.82)
        }
        out = candidate.length < dataUrl.length ? candidate : dataUrl
        skipped = out === dataUrl
      } catch {
        out = dataUrl
        skipped = true
      }
    }
    // 缩略图一律尽力生成（raw 档入册也要有图可看）；失败留空由占位样式兜底
    let thumb = ""
    try {
      thumb = drawScaled(img, 256, animated ? "image/png" : "image/webp", 0.7)
    } catch {
      thumb = ""
    }
    return { dataUrl: out, thumb, skipped }
  })
}

export function settingsToForm(settings: Settings): FormValues {
  return {
    auto_derive: settings.auto_derive !== false,
    cycle_length: Number(settings.cycle_length ?? 28),
    period_length: Number(settings.period_length ?? 5),
    ovulation_day: Number(settings.ovulation_day ?? 14),
    ovulation_window: Number(settings.ovulation_window ?? 3),
    inject_mode: String(settings.inject_mode || "every_user_message"),
    inject_interval_n: Number(settings.inject_interval_n ?? 3),
    phase_openers: settings.phase_openers !== false,
    timezone: String(settings.timezone || "auto"),
    mood_enabled: settings.mood_enabled !== false,
    default_action_minutes: Number(settings.default_action_minutes ?? 20),
    emotion_sense_enabled: settings.emotion_sense_enabled !== false,
    tone_check_rate: Number(settings.tone_check_rate ?? 1),
    tone_phase_sensitivity_enabled: settings.tone_phase_sensitivity_enabled !== false,
    tone_phase_sensitivity: Number(settings.tone_phase_sensitivity ?? 0.15),
    tone_slot: String(settings.tone_slot || ""),
    fragments_enabled: settings.fragments_enabled !== false,
    fragments_slot: String(settings.fragments_slot || "summary"),
    review_enabled: settings.review_enabled !== false,
    review_slot: String(settings.review_slot || "summary"),
    review_turns_threshold: Number(settings.review_turns_threshold ?? 50),
    review_days_threshold: Number(settings.review_days_threshold ?? 7),
    anniversary_inject: (settings as Record<string, any>).anniversary_inject !== false,
    debug_mode: settings.debug_mode === true,
  }
}
