// 功能介绍视图（1.2.7 单页化二轮修订）：面板「功能管理」每行一个"功能介绍"
// 按钮，点击后在功能页内切换到介绍子页（不再是居中 Modal）——作用 / 主要场景 /
// 限制与注意事项 / 依赖 / 原理演示图，左上角「返回」回到功能列表。
//
// 为什么从 Modal 改成页内子页（用户反馈）：居中弹窗的宽高按视口算，和面板
// 实际框体脱节——默认窗口下要么大到压迫、要么被挤出横向滚动条，内容观感
// "被压缩"。子页直接吃 .tm-content 的自然宽度：刚好贴合外框、随窗口伸缩，
// 横向滚动问题从根上消失。窄容器里双栏由 auto-fit 自动回落单栏，宁滚不裁。
//
// 数据契约（不变）：文案事实源在 core/intros.py（中文原文 + key 派生），经
// get_capability_intro 入口以 SDK 的 tr() 引用（{"$i18n","default"}）透传——
// action 返回值不经宿主 i18n 解析（只有 dashboard context 会），所以这里
// 用 resolveText() 手动展开：zh 走 default，en 等语言进 i18n/*.json 补同名
// key 即生效。依赖链/LLM 触点/配置键从声明表 payload 现场取，与开关同源。
//
// 原理图：hosted-tsx 运行时无 SVG 命名空间（mount 用 createElement，见
// ring.tsx 注释），流程图为纯 CSS 文字胶囊 + 箭头。应用户反馈已删 emoji
// 图标：类型区分全靠配色（src 蓝 / proc 紫 / gate 琥珀虚线 / out 绿，
// 未知 kind 回落中性）。
import { StatusBadge } from "@neko/plugin-ui"
import type { CapItem, TFunc } from "./types"

// ---- 文案引用解析（$i18n 引用 / 裸字符串都吃得下）----
type I18nRef = { $i18n?: string; default?: string }
export type IntroText = string | I18nRef

// 签名纪律（1.2.7 白屏事故 + 二轮修订二进宫复盘，双向复现验证）：
// hosted 链接器的导出扫描器扫到带 JSX 的函数时（不分导出不导出！）会状态
// 漂移，把其后所有 `export function/const` 漏采——编译产物残留裸 export →
// iframe 语法错、整面白屏，本地 tsc 查不出。故：只导出跨文件引用的符号；
// 一切带 JSX 的函数（含 IntroFlow 这类纯内部件）一律排在全部导出之后；
// 确需多导出的函数用命名类型 + 单行签名，不要把带 JSX 的导出排在它们前面；
// 改版 ui/*.tsx 后必须过仓内 hosted 链接自检（能完整复现此坑，比肉眼可靠）。
export function resolveText(t: TFunc, v: IntroText | null | undefined): string {
  if (v == null) return ""
  if (typeof v === "string") return v
  const key = String(v.$i18n || "")
  const def = String(v.default || "")
  if (!key) return def
  return t(key, { defaultValue: def })
}

function listTexts(t: TFunc, list: IntroText[] | null | undefined): string[] {
  return (list || []).map((v) => resolveText(t, v)).filter((s) => !!s)
}

// ---- 介绍入口 payload（与 core/intros.py build_intro_payload 对齐）----
export type IntroFlowNode = { kind?: string; label?: IntroText }
export type CapIntroPayload = {
  id?: string
  found?: boolean
  purpose?: IntroText
  scenarios?: IntroText[]
  limits?: IntroText[]
  flow?: IntroFlowNode[]
  deps?: string[]
  config_keys?: string[]
  llm?: string
  tools?: string[]
}

// LLM 触点徽标表（1.2.7 起从 features.tsx 迁移至此统一维护：
// 功能行与介绍视图共用一张表，features.tsx 反向 import，避免两处漂移）。
// 类型别名绕开校验器：export const 的注解里不能带泛型尖括号的逗号（见 utils.ts 同款注释）
type LlmBadge = { key: string; def: string; tone: "warning" | "success" | "default" }
type LlmBadgeMap = Record<string, LlmBadge>

export const LLM_BADGES: LlmBadgeMap = {
  tool: { key: "panel.features.llm.tool", def: "LLM 工具", tone: "warning" },
  direct: { key: "panel.features.llm.direct", def: "直连模型", tone: "warning" },
  host_http: { key: "panel.features.llm.host", def: "宿主模型", tone: "warning" },
  injection: { key: "panel.features.llm.injection", def: "上下文注入", tone: "default" },
  none: { key: "panel.features.llm.local", def: "纯本地", tone: "success" },
}

// props 命名类型（单行签名配套）
type CapIntroViewProps = {
  t: TFunc
  item: CapItem
  capLabel: (id: string) => string
  statusHint: string
  intro: CapIntroPayload | null
  loading?: boolean
  error?: string
  onBack: () => void
}

// 介绍子页本体（features.tsx 唯一引用的视图导出；带 JSX，必须排在
// 全部非 JSX 导出之后——见签名纪律注释）
export function CapIntroView(props: CapIntroViewProps) {
  const { t, item, capLabel, statusHint, intro, loading, error, onBack } = props

  const scenarios = listTexts(t, intro && intro.scenarios)
  const limits = listTexts(t, intro && intro.limits)
  const deps = (intro && intro.deps) || []
  const configKeys = (intro && intro.config_keys) || []
  const tools = (intro && intro.tools) || []
  const badge = LLM_BADGES[(intro && intro.llm) || item.llm] || LLM_BADGES.none

  return (
    <div className="tm-pane tm-ci-page">
      <div className="tm-ci-head">
        <button type="button" className="tm-ci-back" onClick={onBack}>
          ← {t("panel.capintro.back", { defaultValue: "返回功能列表" })}
        </button>
        <h3 className="tm-ci-title">{capLabel(item.id)}</h3>
        <StatusBadge tone={badge.tone} label={t(badge.key, { defaultValue: badge.def })} />
        <span className="tm-ci-head-spacer" />
        <StatusBadge
          tone={item.enabled ? "success" : "warning"}
          label={item.enabled
            ? t("panel.capintro.stateOn", { defaultValue: "当前生效" })
            : (statusHint || t("panel.capintro.stateOff", { defaultValue: "当前未生效" }))}
        />
      </div>

      {error ? (
        <div className="tm-ci-error">{error}</div>
      ) : null}
      {loading ? (
        <div className="tm-ci-loading">{t("panel.capintro.loading", { defaultValue: "介绍加载中…" })}</div>
      ) : null}
      {!loading && !error && intro && intro.found === false ? (
        <div className="tm-ci-empty">
          {t("panel.capintro.notFound", { defaultValue: "该功能暂无介绍内容" })}
        </div>
      ) : null}
      {!loading && !error && intro && intro.found !== false ? (
        <div className="tm-ci">
          {/* 自适应网格：容器够宽时双栏（左：作用+场景 / 右：限制+依赖），
              窄面板自动回落单栏；流程图通栏置底。全部按子页可用宽度计算，
              不再有任何视口级 media query */}
          <div className="tm-ci-grid">
            <div className="tm-ci-col">
              <section className="tm-ci-sec">
                <h4 className="tm-ci-h">{t("panel.capintro.purpose", { defaultValue: "功能作用" })}</h4>
                <p className="tm-ci-purpose">{resolveText(t, intro.purpose)}</p>
              </section>
              <section className="tm-ci-sec">
                <h4 className="tm-ci-h">{t("panel.capintro.scenarios", { defaultValue: "主要场景" })}</h4>
                <ul className="tm-ci-list">
                  {scenarios.map((s, i) => <li key={i}>{s}</li>)}
                </ul>
              </section>
            </div>
            <div className="tm-ci-col">
              <section className="tm-ci-sec">
                <h4 className="tm-ci-h">{t("panel.capintro.limits", { defaultValue: "限制与注意事项" })}</h4>
                <ul className="tm-ci-list tm-ci-list-warn">
                  {limits.map((s, i) => <li key={i}>{s}</li>)}
                </ul>
              </section>
              <section className="tm-ci-sec">
                <h4 className="tm-ci-h">{t("panel.capintro.deps", { defaultValue: "依赖" })}</h4>
                <div className="tm-ci-chips">
                  {deps.length ? deps.map((d) => (
                    <span key={d} className="tm-ci-chip" data-tone="dep">
                      {t("panel.capintro.depUpstream", { defaultValue: "上游功能" })} · {capLabel(d)}
                    </span>
                  )) : (
                    <span className="tm-ci-chip" data-tone="none">
                      {t("panel.capintro.depsNone", { defaultValue: "无上游功能依赖" })}
                    </span>
                  )}
                  {configKeys.map((k) => (
                    <span key={k} className="tm-ci-chip tm-ci-mono" data-tone="cfg">
                      {k}
                    </span>
                  ))}
                  {tools.length ? (
                    <span className="tm-ci-chip" data-tone="tool" title={tools.join("  ·  ")}>
                      {t("panel.capintro.toolsCount", { defaultValue: "占用 {n} 个模型工具", n: tools.length })}
                    </span>
                  ) : null}
                </div>
              </section>
            </div>
          </div>

          <section className="tm-ci-sec">
            <h4 className="tm-ci-h">{t("panel.capintro.flowTitle", { defaultValue: "原理演示" })}</h4>
            <IntroFlow t={t} nodes={(intro.flow || []) as IntroFlowNode[]} />
          </section>
        </div>
      ) : null}
    </div>
  )
}


// IntroFlow：纯内部渲染函数（不导出），按签名纪律固定在文件末尾：
// 带 JSX 的函数（即使不导出）排在导出之前同样会撞漂扫描器丢导出（二进宫
// 实验），由 CapIntroView 内部直用，函数声明提升保证前向引用成立
function IntroFlow(props: { t: TFunc; nodes: IntroFlowNode[] }) {
  const { t, nodes } = props
  if (!nodes.length) {
    return (
      <div className="tm-ci-flow-empty">
        {t("panel.capintro.flowEmpty", { defaultValue: "该功能暂无可视化流程" })}
      </div>
    )
  }
  return (
    <div className="tm-ci-flow">
      {nodes.map((node, i) => (
        // 流程链是静态内容，位置即身份，index key 稳定够用
        <span key={i} className="tm-ci-flow-step">
          {i > 0 ? <span className="tm-ci-flow-arrow">→</span> : null}
          <span className="tm-ci-node" data-kind={node.kind || "proc"}>
            <span className="tm-ci-node-label">{resolveText(t, node.label)}</span>
          </span>
        </span>
      ))}
    </div>
  )
}
