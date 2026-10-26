import {
  Alert,
  Button,
  Card,
  Field,
  Inline,
  Page,
  SegmentedControl,
  Slider,
  Stack,
  StatusBadge,
  Switch,
  Text,
  Textarea,
  useState,
  useToast,
} from "@neko/plugin-ui"
import type { HostedAction, PluginSurfaceProps } from "@neko/plugin-ui"

type OptionMeta = {
  value?: number | boolean | string | string[]
  default?: number | boolean | string | string[]
  min?: number
  max?: number
  label?: string
  hint?: string
  choices?: string[]
  profile_defaults?: number
}

type SettingsState = {
  options?: Record<string, OptionMeta>
}

type FormState = {
  ocr_profile: string
  ocr_perceive_interval_seconds: number
  ocr_worker_threads: number
  screen_activation_limit: number
  query_activation_ttl_seconds: number
  scene_prompt_reinject_seconds: number
  change_driven_enabled: boolean
  change_detect_threshold: number
  scene_switch_cooldown_seconds: number
  // 拍板 2.0.71：高频 OCR + 场景状态机
  ocr_scene_interval_seconds: number
  ocr_term_interval_seconds: number
  scene_hysteresis_count: number
  desktop_markers_enabled: boolean
  desktop_markers_extra: string
}

const PROFILE_LABELS: Record<string, string> = {
  auto: "自动",
  eco: "省电",
  balanced: "平衡",
  performance: "性能",
  custom: "自定义",
}
const PROFILE_DESCRIPTIONS: Record<string, string> = {
  auto: "启动时探测",
  eco: "30s / 1 线程",
  balanced: "15s / 1 线程",
  performance: "8s / 2 线程",
  custom: "自定义参数",
}
const PROFILE_TONES: Record<string, "info" | "success" | "warning" | "danger"> = {
  auto: "info",
  eco: "success",
  balanced: "info",
  performance: "warning",
  // 拍板 2.0.69：custom 改 warning 表示"偏离预设"——和 performance 同色，用户一眼能看出
  // 当前在用自定义参数（不是 auto/eco/balanced/performance 任意一档）
  custom: "warning",
}

function actionById(actions: HostedAction[], id: string): HostedAction | undefined {
  return actions.find((action) => action.id === id || action.entry_id === id)
}

function unwrap(envelope: unknown): Record<string, unknown> {
  if (envelope && typeof envelope === "object" && "result" in envelope) {
    const r = (envelope as { result: unknown }).result
    if (r && typeof r === "object") return r as Record<string, unknown>
  }
  if (envelope && typeof envelope === "object") return envelope as Record<string, unknown>
  return {}
}

function num(opt: OptionMeta | undefined, fb: number): number {
  if (!opt) return fb
  if (typeof opt.value === "number") return opt.value
  if (typeof opt.default === "number") return opt.default
  return fb
}

function bool(opt: OptionMeta | undefined, fb: boolean): boolean {
  if (!opt) return fb
  if (typeof opt.value === "boolean") return opt.value
  if (typeof opt.default === "boolean") return opt.default
  return fb
}

function str(opt: OptionMeta | undefined, fb: string): string {
  if (!opt) return fb
  if (typeof opt.value === "string") return opt.value
  return fb
}

function listText(opt: OptionMeta | undefined): string {
  if (!opt) return ""
  const arr = (opt.value as string[] | undefined) || []
  return Array.isArray(arr) ? arr.join("\n") : ""
}

function buildDefaults(state: SettingsState): FormState {
  const opts = state.options || {}
  return {
    ocr_profile: str(opts.ocr_profile, "auto"),
    ocr_perceive_interval_seconds: num(opts.ocr_perceive_interval_seconds, 15),
    ocr_worker_threads: num(opts.ocr_worker_threads, 1),
    screen_activation_limit: num(opts.screen_activation_limit, 12),
    query_activation_ttl_seconds: num(opts.query_activation_ttl_seconds, 300),
    scene_prompt_reinject_seconds: num(opts.scene_prompt_reinject_seconds, 0),
    change_driven_enabled: bool(opts.change_driven_enabled, false),  // 拍板 2.0.70：默认关（S1 真机连续 worker 卡死，默认关回到 2.0.66 稳定行为）
    change_detect_threshold: num(opts.change_detect_threshold, 8),
    scene_switch_cooldown_seconds: num(opts.scene_switch_cooldown_seconds, 30),
    // 拍板 2.0.71：高频 OCR + 场景状态机
    ocr_scene_interval_seconds: num(opts.ocr_scene_interval_seconds, 2),
    ocr_term_interval_seconds: num(opts.ocr_term_interval_seconds, 15),
    scene_hysteresis_count: num(opts.scene_hysteresis_count, 3),
    desktop_markers_enabled: bool(opts.desktop_markers_enabled, true),
    desktop_markers_extra: listText(opts.desktop_markers_extra),
  }
}

function buildDefaultsForProfile(state: SettingsState, profile: string): FormState {
  const opts = state.options || {}
  const defaults = buildDefaults(state)
  if (profile === "custom" || profile === "auto") return defaults
  const ecoOpt = opts.ocr_perceive_interval_seconds
  const thrOpt = opts.ocr_worker_threads
  if (profile === "eco") {
    return { ...defaults, ocr_perceive_interval_seconds: 30, ocr_worker_threads: 1 }
  }
  if (profile === "balanced") {
    return { ...defaults, ocr_perceive_interval_seconds: 15, ocr_worker_threads: 1 }
  }
  if (profile === "performance") {
    return { ...defaults, ocr_perceive_interval_seconds: 8, ocr_worker_threads: 2 }
  }
  return defaults
}

// ---- Theme tokens (拍板 2.0.66：深色卡片风) ----
const THEME = {
  bg: "#0f1116",
  bgCard: "#171a21",
  bgSubtle: "#1f232c",
  border: "#2a2f3a",
  text: "#e6e8ee",
  textMuted: "#8b94a3",
  textDim: "#5d6678",
  accent: "#7c5cff",         // 主色：紫罗兰
  accentSoft: "#9b82ff",
  accentBg: "#2a214a",
  success: "#4ade80",
  warning: "#fbbf24",
  danger: "#f87171",
  info: "#60a5fa",
} as const

const panelStyle: Record<string, string | number> = {
  background: THEME.bg,
  color: THEME.text,
  minHeight: "100%",
  padding: "24px 16px",
}

const cardStyle: Record<string, string | number> = {
  background: THEME.bgCard,
  borderRadius: 12,
  border: `1px solid ${THEME.border}`,
  padding: "16px 18px",
  marginBottom: 12,
  boxShadow: "0 1px 3px rgba(0,0,0,0.4)",
}

const cardTitleStyle: Record<string, string | number> = {
  fontSize: 14,
  fontWeight: 600,
  color: THEME.text,
  marginBottom: 4,
}

const hintStyle: Record<string, string | number> = {
  fontSize: 12,
  color: THEME.textMuted,
  marginTop: 8,
  lineHeight: 1.5,
}

const headerStyle: Record<string, string | number> = {
  display: "flex",
  flexDirection: "column",
  gap: 8,
  padding: "8px 4px 16px",
  borderBottom: `1px solid ${THEME.border}`,
  marginBottom: 16,
}

const badgeRowStyle: Record<string, string | number> = {
  display: "flex",
  alignItems: "center",
  gap: 8,
  flexWrap: "wrap",
}

export default function MultiGameCompanionPanel(
  props: PluginSurfaceProps<SettingsState>,
) {
  const opts = props.state?.options || {}
  const [form, setForm] = useState<FormState>(buildDefaults(props.state || {}))
  const [saving, setSaving] = useState(false)
  const [savedAt, setSavedAt] = useState<string>("")
  const toast = useToast()
  const applyAction = actionById(props.actions || [], "apply_settings")

  function pickProfile(p: string) {
    const next = buildDefaultsForProfile(props.state || {}, p)
    setForm({ ...next, ocr_profile: p })
  }

  // 用户手填 interval/threads → 自动切 custom 档
  function setInterval(v: number) {
    setForm({ ...form, ocr_perceive_interval_seconds: v, ocr_profile: "custom" })
  }
  function setThreads(v: number) {
    setForm({ ...form, ocr_worker_threads: v, ocr_profile: "custom" })
  }

  async function apply() {
    if (!applyAction) {
      toast.error("应用动作不可用，请确认插件已启动。")
      return
    }
    setSaving(true)
    const lines = (form.desktop_markers_extra || "")
      .split(/\r?\n/)
      .map((s) => s.trim())
      .filter(Boolean)
    const payload = {
      options: {
        ocr_profile: form.ocr_profile,
        ocr_perceive_interval_seconds: Number(form.ocr_perceive_interval_seconds),
        ocr_worker_threads: Number(form.ocr_worker_threads),
        screen_activation_limit: Number(form.screen_activation_limit),
        query_activation_ttl_seconds: Number(form.query_activation_ttl_seconds),
        scene_prompt_reinject_seconds: Number(form.scene_prompt_reinject_seconds),
        change_driven_enabled: !!form.change_driven_enabled,
        change_detect_threshold: Number(form.change_detect_threshold),
        scene_switch_cooldown_seconds: Number(form.scene_switch_cooldown_seconds),
        // 拍板 2.0.71：高频 OCR + 场景状态机
        ocr_scene_interval_seconds: Number(form.ocr_scene_interval_seconds),
        ocr_term_interval_seconds: Number(form.ocr_term_interval_seconds),
        scene_hysteresis_count: Number(form.scene_hysteresis_count),
        desktop_markers_enabled: !!form.desktop_markers_enabled,
        desktop_markers_extra: lines,
      },
    }
    try {
      const result = unwrap(await props.api.call("apply_settings", { payload }))
      const rebuilt = result.executor_rebuilt === true
      const persisted = result.persisted !== false
      const stamp = new Date().toLocaleTimeString("zh-CN", { hour12: false })
      setSavedAt(stamp)
      if (!persisted) {
        toast.warning("内存已更新，但保存到 plugin.toml 失败（请检查文件权限）。")
      } else if (rebuilt) {
        toast.success(`已保存；OCR worker 线程数变化，下次 OCR 会重建线程池。`)
      } else {
        toast.success("已保存到 plugin.toml；下次 OCR tick 生效。")
      }
      await props.api.refresh()
    } catch (error) {
      toast.error(error instanceof Error ? error.message : String(error))
    } finally {
      setSaving(false)
    }
  }

  function resetDefaults() {
    const d = buildDefaults(props.state || {})
    setForm(d)
    toast.info("已重置为默认值（未保存，请点「应用」写入）。")
  }

  const profileChoices = (opts.ocr_profile && opts.ocr_profile.choices) || [
    "auto",
    "eco",
    "balanced",
    "performance",
    "custom",
  ]
  const intervalMin = (opts.ocr_perceive_interval_seconds?.min as number) || 3
  const intervalMax = (opts.ocr_perceive_interval_seconds?.max as number) || 300
  const threadsMin = (opts.ocr_worker_threads?.min as number) || 1
  const threadsMax = (opts.ocr_worker_threads?.max as number) || 2
  const screenMin = (opts.screen_activation_limit?.min as number) || 1
  const screenMax = (opts.screen_activation_limit?.max as number) || 30
  const queryMin = (opts.query_activation_ttl_seconds?.min as number) || 10
  const queryMax = (opts.query_activation_ttl_seconds?.max as number) || 3600
  const sceneMin = (opts.scene_prompt_reinject_seconds?.min as number) || 0
  const sceneMax = (opts.scene_prompt_reinject_seconds?.max as number) || 3600
  const detectMin = (opts.change_detect_threshold?.min as number) || 1
  const detectMax = (opts.change_detect_threshold?.max as number) || 64
  const switchCooldownMin = (opts.scene_switch_cooldown_seconds?.min as number) || 10
  const switchCooldownMax = (opts.scene_switch_cooldown_seconds?.max as number) || 300
  // 拍板 2.0.71：场景状态机 3 个新 Slider 的范围（对齐后端 clamp）
  const sceneJudgeMin = (opts.ocr_scene_interval_seconds?.min as number) || 1
  const sceneJudgeMax = (opts.ocr_scene_interval_seconds?.max as number) || 30
  const termMin = (opts.ocr_term_interval_seconds?.min as number) || 3
  const termMax = (opts.ocr_term_interval_seconds?.max as number) || 300
  const hystMin = (opts.scene_hysteresis_count?.min as number) || 1
  const hystMax = (opts.scene_hysteresis_count?.max as number) || 10

  const currentProfile = form.ocr_profile || "auto"

  return (
    <div style={panelStyle}>
      <div style={headerStyle}>
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <div
            style={{
              width: 36,
              height: 36,
              borderRadius: 10,
              background: `linear-gradient(135deg, ${THEME.accent}, ${THEME.accentSoft})`,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              fontSize: 18,
              fontWeight: 700,
              color: "#fff",
            }}
          >
            N
          </div>
          <div>
            <div style={{ fontSize: 18, fontWeight: 700, color: THEME.text }}>
              原神游戏搭子
            </div>
            <div style={{ fontSize: 12, color: THEME.textMuted, marginTop: 2 }}>
              感知与激活参数 · 按需权衡转场响应 ↔ CPU
            </div>
          </div>
        </div>
        <div style={badgeRowStyle}>
          <StatusBadge
            tone={PROFILE_TONES[currentProfile] || "info"}
            label={`当前档位 · ${PROFILE_LABELS[currentProfile] || currentProfile}`}
          />
          <StatusBadge
            tone="info"
            label={`OCR 间隔 ${form.ocr_perceive_interval_seconds}s · ${form.ocr_worker_threads} 线程`}
          />
          {savedAt ? (
            <StatusBadge tone="success" label={`已保存 · ${savedAt}`} />
          ) : null}
        </div>
      </div>

      {/* 配置档选择 */}
      <div style={cardStyle}>
        <div style={cardTitleStyle}>⚡ 配置档</div>
        <div style={hintStyle}>
          {opts.ocr_profile?.hint ||
            "auto = 启动时自动探测；eco/balanced/performance = 预设档；custom = 自定义"}
        </div>
        <div style={{ marginTop: 12 }}>
          <SegmentedControl
            value={currentProfile}
            options={profileChoices.map((p) => ({
              value: p,
              label: `${PROFILE_LABELS[p] || p}`,
            }))}
            onChange={(v: any) => pickProfile(String(v))}
          />
        </div>
        <div style={badgeRowStyle as any}>
          <span style={{ fontSize: 11, color: THEME.textDim }}>
            档位说明：auto={
              PROFILE_DESCRIPTIONS.auto
            } · eco={PROFILE_DESCRIPTIONS.eco} · balanced={
              PROFILE_DESCRIPTIONS.balanced
            } · performance={PROFILE_DESCRIPTIONS.performance}
          </span>
        </div>
      </div>

      {/* OCR 感知与并发 */}
      <div style={cardStyle}>
        <div style={cardTitleStyle}>🔍 OCR 感知与并发</div>
        <Stack>
          <Field
            label={`OCR 感知周期 · 当前 ${form.ocr_perceive_interval_seconds}s`}
            help={`${opts.ocr_perceive_interval_seconds?.hint || "3=极限 / 15=平衡 / 60=省 CPU"} · 范围 [${intervalMin}, ${intervalMax}]`}
          >
            <Slider
              value={form.ocr_perceive_interval_seconds}
              min={intervalMin}
              max={intervalMax}
              step={1}
              showValue
              onChange={(v: number) => setInterval(v)}
            />
          </Field>
          <Field
            label={`OCR worker 线程数 · 当前 ${form.ocr_worker_threads}`}
            help={`${opts.ocr_worker_threads?.hint || "1=串行，2=允许 2 并发"} · 范围 [${threadsMin}, ${threadsMax}]，改此值会重建线程池`}
          >
            <Slider
              value={form.ocr_worker_threads}
              min={threadsMin}
              max={threadsMax}
              step={1}
              showValue
              onChange={(v: number) => setThreads(v)}
            />
          </Field>
        </Stack>
      </div>

      {/* 拍板 2.0.71：场景状态机——高频 OCR 拆分 + 滞回 */}
      <div style={cardStyle}>
        <div style={cardTitleStyle}>🎬 场景状态机（高频 OCR + 滞回防抖）</div>
        <Stack>
          <Field
            label={`场景判定间隔 · 当前 ${form.ocr_scene_interval_seconds}s`}
            help={`${opts.ocr_scene_interval_seconds?.hint || "屏幕变化后多久判定一次场景；2s 灵敏，15s 省电"} · 范围 [${sceneJudgeMin}, ${sceneJudgeMax}]`}
          >
            <Slider
              value={form.ocr_scene_interval_seconds}
              min={sceneJudgeMin}
              max={sceneJudgeMax}
              step={1}
              showValue
              onChange={(v: number) => setForm({ ...form, ocr_scene_interval_seconds: v })}
            />
          </Field>
          <Field
            label={`术语激活间隔 · 当前 ${form.ocr_term_interval_seconds}s`}
            help={`${opts.ocr_term_interval_seconds?.hint || "屏幕术语激活频率；与场景判定独立"} · 范围 [${termMin}, ${termMax}]`}
          >
            <Slider
              value={form.ocr_term_interval_seconds}
              min={termMin}
              max={termMax}
              step={1}
              showValue
              onChange={(v: number) => setForm({ ...form, ocr_term_interval_seconds: v })}
            />
          </Field>
          <Field
            label={`场景滞回次数 · 当前 ${form.scene_hysteresis_count}`}
            help={`${opts.scene_hysteresis_count?.hint || "连续 N 次命中新场景才切换；防抖动"} · 范围 [${hystMin}, ${hystMax}]`}
          >
            <Slider
              value={form.scene_hysteresis_count}
              min={hystMin}
              max={hystMax}
              step={1}
              showValue
              onChange={(v: number) => setForm({ ...form, scene_hysteresis_count: v })}
            />
          </Field>
        </Stack>
      </div>

      {/* 激活阈值 */}
      <div style={cardStyle}>
        <div style={cardTitleStyle}>📊 激活阈值（context token 权衡）</div>
        <Stack>
          <Field
            label={`单次 screen-activate 上限 · 当前 ${form.screen_activation_limit}`}
            help={`${opts.screen_activation_limit?.hint || "↑ 命中术语多 token 多"} · 范围 [${screenMin}, ${screenMax}]`}
          >
            <Slider
              value={form.screen_activation_limit}
              min={screenMin}
              max={screenMax}
              step={1}
              showValue
              onChange={(v: number) => setForm({ ...form, screen_activation_limit: v })}
            />
          </Field>
          <Field
            label={`用户消息激活 TTL · 当前 ${form.query_activation_ttl_seconds}s`}
            help={`${opts.query_activation_ttl_seconds?.hint || "↑ 话题延续久 token 多"} · 范围 [${queryMin}, ${queryMax}]`}
          >
            <Slider
              value={form.query_activation_ttl_seconds}
              min={queryMin}
              max={queryMax}
              step={10}
              showValue
              onChange={(v: number) => setForm({ ...form, query_activation_ttl_seconds: v })}
            />
          </Field>
          <Field
            label={`scene prompt 重推间隔 · 当前 ${form.scene_prompt_reinject_seconds}s`}
            help={`${opts.scene_prompt_reinject_seconds?.hint || "0=只推一次，>0 每 N 秒重推"} · 范围 [${sceneMin}, ${sceneMax}]`}
          >
            <Slider
              value={form.scene_prompt_reinject_seconds}
              min={sceneMin}
              max={sceneMax}
              step={30}
              showValue
              onChange={(v: number) => setForm({ ...form, scene_prompt_reinject_seconds: v })}
            />
          </Field>
        </Stack>
      </div>

      {/* 桌面/IDE 黑名单 */}
      <div style={cardStyle}>
        <div style={cardTitleStyle}>⚡ 感知增强（S1 + S2）</div>
        <Stack>
          <Inline align="center" justify="space-between">
            <Text>{opts.change_driven_enabled?.label || "变化驱动 OCR（S1）"}</Text>
            <Switch
              checked={!!form.change_driven_enabled}
              onChange={(v: boolean) => setForm({ ...form, change_driven_enabled: v })}
            />
          </Inline>
          <div
            style={{
              marginTop: 4,
              fontSize: 12,
              color: form.change_driven_enabled ? THEME.info : THEME.warning,
            }}
          >
            {opts.change_driven_enabled?.hint ||
              "开：每 3s tick 抓轻量帧对比，差异大则跳过 interval 立即 OCR；关：按 interval 节奏"}
          </div>
          <Field
            label={`变化检测阈值 · 当前 ${form.change_detect_threshold} 位差异`}
            help={`${opts.change_detect_threshold?.hint || "dHash 16-进位汉明距离"} · 范围 [${detectMin}, ${detectMax}]`}
          >
            <Slider
              value={form.change_detect_threshold}
              min={detectMin}
              max={detectMax}
              step={1}
              showValue
              onChange={(v: number) => setForm({ ...form, change_detect_threshold: v })}
            />
          </Field>
          <Field
            label={`场景切换冷却 · 当前 ${form.scene_switch_cooldown_seconds}s`}
            help={`${opts.scene_switch_cooldown_seconds?.hint || "切场景后多久内不再主动搭话"} · 范围 [${switchCooldownMin}, ${switchCooldownMax}]`}
          >
            <Slider
              value={form.scene_switch_cooldown_seconds}
              min={switchCooldownMin}
              max={switchCooldownMax}
              step={5}
              showValue
              onChange={(v: number) => setForm({ ...form, scene_switch_cooldown_seconds: v })}
            />
          </Field>
        </Stack>
      </div>

      {/* 桌面/IDE 黑名单 */}
      <div style={cardStyle}>
        <div style={cardTitleStyle}>🛡️ 桌面/IDE 黑名单</div>
        <Inline align="center" justify="space-between">
          <Text>{opts.desktop_markers_enabled?.label || "启用桌面/IDE 反特征黑名单"}</Text>
          <Switch
            checked={!!form.desktop_markers_enabled}
            onChange={(v: boolean) => setForm({ ...form, desktop_markers_enabled: v })}
          />
        </Inline>
        <div
          style={{
            marginTop: 8,
            fontSize: 12,
            color: form.desktop_markers_enabled ? THEME.info : THEME.warning,
          }}
        >
          {form.desktop_markers_enabled
            ? "开（推荐）：IDE/浏览器/桌面场景不会误判 IN_GAME"
            : "⚠️ 关：桌面/IDE 场景可能被误识别为游戏中"}
        </div>
        <div style={{ marginTop: 12 }}>
          <Field
            label={opts.desktop_markers_extra?.label || "用户自定义额外桌面特征（每行一个）"}
            help={opts.desktop_markers_extra?.hint || "追加到内置 18 个桌面/IDE 特征串之后"}
          >
            <Textarea
              value={form.desktop_markers_extra || ""}
              onChange={(v: string) => setForm({ ...form, desktop_markers_extra: v })}
              
              placeholder={"Steam\nDiscord\nVS Code"}
            />
          </Field>
        </div>
      </div>

      {/* 底部按钮 */}
      <div
        style={{
          display: "flex",
          justifyContent: "flex-end",
          gap: 8,
          marginTop: 16,
          paddingTop: 16,
          borderTop: `1px solid ${THEME.border}`,
        }}
      >
        <Button tone="default" onClick={resetDefaults} disabled={saving}>
          恢复默认
        </Button>
        <Button tone="primary" onClick={apply} disabled={!applyAction || saving}>
          {saving ? "保存中..." : "应用"}
        </Button>
      </div>

      {/* 保存状态 banner */}
      {savedAt ? (
        <Alert tone="success">
          设置已持久化到 plugin.toml · {savedAt}
        </Alert>
      ) : (
        <Alert tone="info">
          所有设置都是"读 self._options.xxx"的运行时参数；点「应用」会写入 plugin.toml，重启不丢。
          worker 线程数变化会重建 ThreadPoolExecutor（旧的 OCR 自然结束）。
        </Alert>
      )}
    </div>
  )
}